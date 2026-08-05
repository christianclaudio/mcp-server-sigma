#!/usr/bin/env python3
"""OpenAPI drift detection for mcp-server-sigma.

Compares every (method, path) pair in the Sigma OpenAPI spec against
the client.py _request() calls to find:
1. Endpoints in the spec we DON'T cover
2. Endpoints in our client that DON'T exist in the spec (wrong paths)

Usage:
    python scripts/check_openapi_drift.py [--spec-url URL] [--client-path PATH]

Exit codes:
    0 = no drift detected
    1 = drift detected (mismatches found)
    2 = could not fetch/parse spec
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import httpx

# Sigma splits its OpenAPI definition across two files. The advertised
# /openapi.json is an HTML index page, not a spec.
SPEC_URLS = [
    "https://help.sigmacomputing.com/openapi/sigma-rest-api.json",
    "https://help.sigmacomputing.com/openapi/code-representation.json",
]
CLIENT_PATH = Path(__file__).parent.parent / "src" / "sigma_mcp" / "client.py"
ALLOWLIST_PATH = Path(__file__).parent / "drift_allowlist.txt"


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


def fetch_spec(urls: list[str]) -> dict:
    """Fetch and merge Sigma's OpenAPI specs into one paths dict."""
    merged: dict = {"paths": {}}
    for url in urls:
        r = httpx.get(url, timeout=60.0, follow_redirects=True)
        r.raise_for_status()
        spec = r.json()
        merged["paths"].update(spec.get("paths", {}))
    return merged


def extract_spec_endpoints(spec: dict) -> set[tuple[str, str]]:
    """Extract (METHOD, normalized_path) pairs from the OpenAPI spec."""
    endpoints: set[tuple[str, str]] = []
    paths = spec.get("paths", {})
    for path, methods in paths.items():
        normalized = re.sub(r"\{[^}]+\}", "{}", path)
        for method in methods:
            if method.lower() in ("get", "post", "put", "patch", "delete"):
                endpoints.append((method.upper(), normalized))
    return set(endpoints)


def extract_client_endpoints(client_path: Path) -> set[tuple[str, str]]:
    """Extract (METHOD, normalized_path) pairs from client.py source."""
    source = client_path.read_text()
    endpoints: set[tuple[str, str]] = []

    # Match self._request("METHOD", "path") or self._request("METHOD", f"path")
    pattern = re.compile(r'self\._request\(\s*"(GET|POST|PUT|PATCH|DELETE)"\s*,\s*f?"([^"]+)"')
    for match in pattern.finditer(source):
        method = match.group(1)
        path = match.group(2)
        # Normalize f-string interpolations to {}
        normalized = re.sub(r"\{[^}]*\}", "{}", path)
        endpoints.append((method, normalized))

    # Also match self.get/post/put/patch/delete calls with path literals
    for verb in ("get", "post", "put", "patch", "delete"):
        method = verb.upper()
        verb_pattern = re.compile(rf'(?:await\s+)?self\.{verb}\(\s*f?"(/v2[^"]+)"')
        for match in verb_pattern.finditer(source):
            path = match.group(1)
            normalized = re.sub(r"\{[^}]*\}", "{}", path)
            endpoints.append((method, normalized))

    return set(endpoints)


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect OpenAPI drift")
    parser.add_argument("--spec-url", action="append", default=None, help="Override spec URL(s); repeatable.")
    parser.add_argument("--client-path", type=Path, default=CLIENT_PATH)
    parser.add_argument("--allowlist", type=Path, default=ALLOWLIST_PATH)
    args = parser.parse_args()

    spec_urls = args.spec_url or SPEC_URLS
    print(f"Fetching spec from: {', '.join(spec_urls)}")
    try:
        spec = fetch_spec(spec_urls)
    except Exception as e:
        print(f"ERROR: Could not fetch spec: {e}", file=sys.stderr)
        return 2

    spec_endpoints = extract_spec_endpoints(spec)
    client_endpoints = extract_client_endpoints(args.client_path)
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
        # Uncovered endpoints are informational, not a failure
        # Only wrong paths are a hard failure

    if not drift_found:
        print("\n✅ No wrong-path drift detected.")
        return 0
    else:
        print(f"\n❌ Drift detected: {len(in_client_not_spec)} wrong-path endpoint(s)")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
