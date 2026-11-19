#!/usr/bin/env python3
"""OpenAPI & Parameter drift detection for mcp-server-sigma.

Compares every (method, path) pair and query parameter in the Sigma OpenAPI spec against
the client.py _request() and get/post/put/patch/delete calls to find:
1. Endpoints in the spec we DON'T cover (informational)
2. Endpoints in our client that DON'T exist in the spec (wrong paths - hard error)
3. Parameters marked deprecated in the spec (e.g. 'search' on /v2/members) - warning/error

Usage:
    python scripts/check_openapi_drift.py [--spec-url URL] [--client-path PATH] [--strict]
        [--fail-on-new-uncovered] [--uncovered-baseline PATH]

Exit codes:
    0 = no breaking drift detected (uncovered endpoints are informational unless the flag is set)
    1 = breaking drift detected (wrong paths, deprecated parameter usage in strict mode,
        or new uncovered endpoints when --fail-on-new-uncovered is set)
    2 = could not fetch/parse the spec, the primary spec was unavailable and a fallback spec
        was used (both the wrong-path check and the new-uncovered gate are skipped), or the
        uncovered baseline is invalid
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from sigma_mcp.client import SSRFSafeAsyncTransport, validate_outbound_url

# Sigma's canonical public spec is hosted as a single combined file on assets.sigmacomputing.com.
# We fall back to the split help.sigmacomputing.com URLs if the main spec fails.
SPEC_URLS = [
    "https://assets.sigmacomputing.com/openapi/public-rest-api/sigma-computing-public-rest-api.json",
    "https://help.sigmacomputing.com/openapi/sigma-rest-api.json",
    "https://help.sigmacomputing.com/openapi/code-representation.json",
]
CLIENT_PATH = Path(__file__).parent.parent / "src" / "sigma_mcp" / "client.py"
ALLOWLIST_PATH = Path(__file__).parent / "drift_allowlist.txt"
UNCOVERED_BASELINE_PATH = Path(__file__).parent / "drift_uncovered_baseline.txt"
BASELINE_TAGS = frozenset({"deprecated", "excluded", "gap"})
_FALLBACK_WARNING = (
    "::warning::Primary Sigma spec unavailable; used fallback split specs; "
    "new-uncovered gate and wrong-path drift check skipped"
)


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


SPEC_FETCH_TIMEOUT_SECONDS = 60.0


async def _get_pinned(url: str) -> httpx.Response:
    """GET *url* through the client's SSRF transport, without following redirects."""
    async with httpx.AsyncClient(
        transport=SSRFSafeAsyncTransport(),
        timeout=SPEC_FETCH_TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        return await client.get(url)


def _fetch_url(url: str) -> httpx.Response:
    """GET a spec URL, connecting only to an address that passed the public-address check.

    ``SSRFSafeAsyncTransport`` resolves the host once, checks every address, and connects
    only to a checked one, keeping the hostname for the Host header, TLS SNI and certificate
    verification. Redirects are never followed: a 3xx target would skip the check. httpx
    ignores env proxies whenever a transport is passed (``allow_env_proxies = trust_env and
    transport is None``), so connections always go direct through the pin; egress-proxy
    deployments need a proxy-aware transport, which isn't provided; ``trust_env=False`` on
    the drift fetch is belt-and-braces.
    """
    validated = validate_outbound_url(url)
    response = asyncio.run(_get_pinned(validated))
    if response.is_redirect:
        raise RuntimeError(f"Refusing to follow redirect for spec URL: {url}")
    response.raise_for_status()
    return response


def fetch_spec(urls: list[str]) -> tuple[dict[str, Any], bool]:
    """Fetch Sigma's OpenAPI spec, trying the primary URL first and falling back to split specs.

    Returns ``(spec, used_fallback)``. ``used_fallback`` is true when the primary URL failed and
    the split help specs (or an empty spec, when no fallback URL was given) were used instead.
    """
    primary_url = urls[0]
    try:
        return _fetch_url(primary_url).json(), False  # type: ignore[no-any-return]
    except Exception as e:
        print(f"Warning: Failed to fetch primary spec ({primary_url}): {e}")
        print("Falling back to split documentation specs...")

    merged: dict[str, Any] = {"paths": {}}
    for url in urls[1:]:
        try:
            spec = _fetch_url(url).json()
            merged["paths"].update(spec.get("paths", {}))
        except Exception as err:
            print(f"Error: Failed to fetch fallback spec ({url}): {err}")
            raise err
    return merged, True


def normalize_path(path: str) -> str:
    """Collapse ``{param}`` segments the same way ``load_allowlist`` does."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def load_uncovered_baseline(path: Path) -> list[tuple[str, str, str, str]] | None:
    """Load ``METHOD path  # tag: reason`` lines. ``None`` means the file is invalid (exit 2)."""
    if not path.exists():
        print(f"ERROR: uncovered baseline not found: {path}", file=sys.stderr)
        return None
    entries: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "#" not in line:
            print(f"ERROR: baseline line {lineno} has no tag: {raw}", file=sys.stderr)
            return None
        body, comment = line.split("#", 1)
        parts = body.split()
        if len(parts) != 2:
            print(f"ERROR: baseline line {lineno} must be METHOD path: {raw}", file=sys.stderr)
            return None
        method, path_str = parts
        normalized = normalize_path(path_str)
        comment = comment.strip()
        if ":" not in comment:
            print(f"ERROR: baseline line {lineno} needs 'tag: reason': {raw}", file=sys.stderr)
            return None
        tag, reason = comment.split(":", 1)
        tag = tag.strip()
        reason = reason.strip()
        if tag not in BASELINE_TAGS:
            print(f"ERROR: baseline line {lineno} has unknown tag {tag!r}", file=sys.stderr)
            return None
        if not reason:
            print(f"ERROR: baseline line {lineno} has an empty reason", file=sys.stderr)
            return None
        key = (method.upper(), normalized)
        if key in seen:
            print(f"ERROR: baseline line {lineno} duplicates {key[0]} {key[1]}", file=sys.stderr)
            return None
        seen.add(key)
        entries.append((key[0], key[1], tag, reason))
    return entries


def expand_org_settings(endpoints: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Cover each ``VALID_ORG_SETTINGS`` name behind the settings template the client calls."""
    from sigma_mcp.tools.admin import VALID_ORG_SETTINGS

    expanded = set(endpoints)
    for method in ("GET", "PATCH"):
        if (method, "/v2/organizations/settings/{}") in endpoints:
            for name in VALID_ORG_SETTINGS:
                expanded.add((method, f"/v2/organizations/settings/{name}"))
    return expanded


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


# Client paging helpers and the query parameters each adds to every request.
_PAGINATORS = {
    "auto_paginate": ("limit", "page"),
    "auto_paginate_token": ("pageSize", "pageToken"),
    "paged_list": ("limit", "page"),
    "paged_list_token": ("pageSize", "pageToken"),
}


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
        if method_name in _PAGINATORS:
            # The paging helpers (``auto_paginate``, ``paged_list`` and their ``_token`` forms) GET
            # the path page by page, adding their own paging parameters, so they are checked like
            # ``self.get``.
            raw_path = self._extract_path(node.args[0]) if node.args else ""
            query_params = set(_PAGINATORS[method_name])
            if len(node.args) >= 2:
                query_params.update(self._extract_dict_keys(node.args[1]))
            if raw_path.startswith("/"):
                self.calls.append(
                    ClientCall(
                        method="GET",
                        raw_path=raw_path,
                        normalized_path=re.sub(r"\{[^}]*\}", "{}", raw_path),
                        query_params=query_params,
                        line_number=node.lineno,
                    )
                )
        elif method_name in http_methods:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect OpenAPI and Parameter Drift")
    parser.add_argument("--spec-url", action="append", default=None, help="Override spec URL(s); repeatable.")
    parser.add_argument("--client-path", type=Path, default=CLIENT_PATH)
    parser.add_argument("--allowlist", type=Path, default=ALLOWLIST_PATH)
    parser.add_argument("--strict", action="store_true", help="Fail on any deprecated parameter usage in client")
    parser.add_argument(
        "--fail-on-new-uncovered",
        action="store_true",
        help="Exit 1 when the spec has an uncovered operation that is not in the baseline",
    )
    parser.add_argument("--uncovered-baseline", type=Path, default=UNCOVERED_BASELINE_PATH)
    args = parser.parse_args(argv)

    spec_urls = args.spec_url or SPEC_URLS
    print(f"Fetching spec from: {', '.join(spec_urls)}")
    try:
        spec, used_fallback = fetch_spec(spec_urls)
    except Exception as e:
        print(f"ERROR: Could not fetch spec: {e}", file=sys.stderr)
        return 2

    if used_fallback:
        print(_FALLBACK_WARNING)
        return 2

    spec_endpoints, spec_deprecated_params = extract_spec_endpoints_and_deprecations(spec)
    client_calls = extract_client_calls(args.client_path)
    client_endpoints = expand_org_settings({(c.method, c.normalized_path) for c in client_calls})
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

    if args.fail_on_new_uncovered:
        baseline = load_uncovered_baseline(args.uncovered_baseline)
        if baseline is None:
            return 2
        baselined = {(method, path) for method, path, _tag, _reason in baseline}
        new_uncovered = in_spec_not_client - baselined
        stale = baselined - in_spec_not_client
        for method, path in sorted(stale):
            print(f"⚠️  Baseline entry is now covered or no longer in the spec: {method} {path}")
        if new_uncovered:
            print(f"\n📋 {len(new_uncovered)} new uncovered endpoint(s) (not in the baseline):")
            for method, path in sorted(new_uncovered):
                print(f"  {method} {path}")
            print(f"\n❌ New uncovered endpoints: {len(new_uncovered)}")
            drift_found = True

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
