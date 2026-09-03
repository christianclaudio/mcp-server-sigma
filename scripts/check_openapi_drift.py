#!/usr/bin/env python3
"""OpenAPI & Parameter drift detection for mcp-server-sigma.

Compares every (method, path) pair and query parameter in the Sigma OpenAPI spec against
the client.py _request() and get/post/put/patch/delete calls to find:
1. Endpoints in the spec we DON'T cover (informational)
2. Endpoints in our client that DON'T exist in the spec (wrong paths - hard error)
3. Parameters marked deprecated in the spec (e.g. 'search' on /v2/members) - warning/error

Usage:
    python scripts/check_openapi_drift.py [--spec-url URL] [--client-path PATH] [--strict]

Exit codes:
    0 = no breaking drift detected
    1 = breaking drift detected (mismatches or deprecated parameter usage in strict mode)
    2 = could not fetch/parse spec
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# Sigma's canonical public spec is hosted as a single combined file on assets.sigmacomputing.com.
# We fall back to the split help.sigmacomputing.com URLs if the main spec fails.
SPEC_URLS = [
    "https://assets.sigmacomputing.com/openapi/public-rest-api/sigma-computing-public-rest-api.json",
    "https://help.sigmacomputing.com/openapi/sigma-rest-api.json",
    "https://help.sigmacomputing.com/openapi/code-representation.json",
]
CLIENT_PATH = Path(__file__).parent.parent / "src" / "sigma_mcp" / "client.py"
ALLOWLIST_PATH = Path(__file__).parent / "drift_allowlist.txt"


@dataclass
class ClientCall:
    method: str
    raw_path: str
    normalized_path: str
    query_params: set[str] = field(default_factory=set)
    line_number: int = 0


def load_allowlist(path: Path) -> set[tuple[str, str]]:
    """Load (METHOD, normalized_path) pairs from the allowlist file."""
    allowed: set[tuple[str, str]] = set()
    if not path.exists():
        return allowed
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            method, path_str = parts
            normalized = re.sub(r"\{[^}]*\}", "{}", path_str)
            allowed.add((method.upper(), normalized))
    return allowed


def fetch_spec(urls: list[str]) -> dict[str, Any]:
    """Fetch Sigma's OpenAPI spec, trying the primary URL first and falling back to split specs."""
    primary_url = urls[0]
    try:
        r = httpx.get(primary_url, timeout=60.0, follow_redirects=True)
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]
    except Exception as e:
        print(f"Warning: Failed to fetch primary spec ({primary_url}): {e}")
        print("Falling back to split documentation specs...")

    merged: dict[str, Any] = {"paths": {}}
    for url in urls[1:]:
        try:
            r = httpx.get(url, timeout=60.0, follow_redirects=True)
            r.raise_for_status()
            spec = r.json()
            merged["paths"].update(spec.get("paths", {}))
        except Exception as err:
            print(f"Error: Failed to fetch fallback spec ({url}): {err}")
            raise err
    return merged


def extract_spec_endpoints_and_deprecations(
    spec: dict[str, Any],
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], set[str]]]:
    """Extract (METHOD, normalized_path) endpoints and map of deprecated parameters per endpoint."""
    endpoints: list[tuple[str, str]] = []
    deprecated_params: dict[tuple[str, str], set[str]] = {}
    paths = spec.get("paths", {})

    for path, methods in paths.items():
        normalized = re.sub(r"\{[^}]+\}", "{}", path)
        if not isinstance(methods, dict):
            continue
        path_level_params = methods.get("parameters", []) if isinstance(methods, dict) else []

        for method, op in methods.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            key = (method.upper(), normalized)
            endpoints.append(key)

            all_params = list(path_level_params) + (op.get("parameters", []) if isinstance(op, dict) else [])
            dep_set: set[str] = set()
            for p in all_params:
                if not isinstance(p, dict):
                    continue
                p_name = p.get("name")
                desc = str(p.get("description", "")).lower()
                is_dep = p.get("deprecated") is True or "[deprecated]" in desc or "deprecated" in desc
                if p_name and is_dep:
                    dep_set.add(p_name)
            if dep_set:
                deprecated_params[key] = dep_set

    return set(endpoints), deprecated_params


class ClientVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[ClientCall] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        # Must be self._request or self.get/post/put/patch/delete
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "self"):
            self.generic_visit(node)
            return

        method_name = func.attr
        http_methods = {"_request", "get", "post", "put", "patch", "delete"}
        if method_name in http_methods:
            method = ""
            raw_path = ""
            query_params: set[str] = set()

            if method_name == "_request":
                if (
                    len(node.args) >= 1
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    method = node.args[0].value.upper()
                if len(node.args) >= 2:
                    raw_path = self._extract_path(node.args[1])
            else:
                method = method_name.upper()
                if len(node.args) >= 1:
                    raw_path = self._extract_path(node.args[0])
                if len(node.args) >= 2 and method_name == "get":
                    query_params.update(self._extract_dict_keys(node.args[1]))

            for kw in node.keywords:
                if kw.arg in ("params", "query_params"):
                    query_params.update(self._extract_dict_keys(kw.value))

            # Filter to API paths (paths starting with / or containing /v)
            if method and raw_path and (raw_path.startswith("/") or "/v" in raw_path):
                normalized = re.sub(r"\{[^}]*\}", "{}", raw_path)
                self.calls.append(
                    ClientCall(
                        method=method,
                        raw_path=raw_path,
                        normalized_path=normalized,
                        query_params=query_params,
                        line_number=node.lineno,
                    )
                )

        self.generic_visit(node)

    def _extract_path(self, node: ast.AST) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts = []
            for part in node.values:
                if isinstance(part, ast.Constant):
                    parts.append(str(part.value))
                else:
                    parts.append("{}")
            return "".join(parts)
        return ""

    def _extract_dict_keys(self, node: ast.AST) -> set[str]:
        keys = set()
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
        return keys


def extract_client_calls(client_path: Path) -> list[ClientCall]:
    """Extract AST client calls from client.py."""
    source = client_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(client_path))
    visitor = ClientVisitor()
    visitor.visit(tree)
    return visitor.calls


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect OpenAPI and Parameter Drift")
    parser.add_argument("--spec-url", action="append", default=None, help="Override spec URL(s); repeatable.")
    parser.add_argument("--client-path", type=Path, default=CLIENT_PATH)
    parser.add_argument("--allowlist", type=Path, default=ALLOWLIST_PATH)
    parser.add_argument("--strict", action="store_true", help="Fail on any deprecated parameter usage in client")
    args = parser.parse_args()

    spec_urls = args.spec_url or SPEC_URLS
    print(f"Fetching spec from: {', '.join(spec_urls)}")
    try:
        spec = fetch_spec(spec_urls)
    except Exception as e:
        print(f"ERROR: Could not fetch spec: {e}", file=sys.stderr)
        return 2

    spec_endpoints, spec_deprecated_params = extract_spec_endpoints_and_deprecations(spec)
    client_calls = extract_client_calls(args.client_path)
    client_endpoints = {(c.method, c.normalized_path) for c in client_calls}
    allowlist = load_allowlist(args.allowlist)

    print(f"Spec endpoints: {len(spec_endpoints)}")
    print(f"Client endpoints: {len(client_endpoints)}")

    # Find mismatches
    in_spec_not_client = spec_endpoints - client_endpoints
    in_client_not_spec = client_endpoints - spec_endpoints

    # Remove allowlisted entries from the wrong-path set
    in_client_not_spec -= allowlist

    drift_found = False

    if in_client_not_spec:
        drift_found = True
        print(f"\n⚠️  {len(in_client_not_spec)} endpoint(s) in CLIENT but NOT in spec (possible wrong paths):")
        for method, path in sorted(in_client_not_spec):
            print(f"  {method} {path}")

    if in_spec_not_client:
        print(f"\n📋 {len(in_spec_not_client)} endpoint(s) in SPEC but not in client (uncovered):")
        for method, path in sorted(in_spec_not_client):
            print(f"  {method} {path}")

    # Parameter deprecation audit
    print("\n🔍 Parameter Deprecation Audit:")
    dep_warnings = 0
    for call in client_calls:
        key = (call.method, call.normalized_path)
        dep_in_spec = spec_deprecated_params.get(key, set())
        for qp in call.query_params:
            if qp in dep_in_spec:
                dep_warnings += 1
                msg = (
                    f"⚠️  DEPRECATED PARAMETER: Client passes deprecated parameter '{qp}' "
                    f"on {call.method} {call.raw_path} (line {call.line_number})"
                )
                print(f"  {msg}")
                if args.strict:
                    drift_found = True

    if dep_warnings == 0:
        print("  ✅ Zero deprecated parameters passed in client calls.")

    if not drift_found:
        print("\n✅ No wrong-path drift detected.")
        return 0
    else:
        print(f"\n❌ Drift detected: {len(in_client_not_spec)} wrong-path endpoint(s) or deprecated parameters.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
