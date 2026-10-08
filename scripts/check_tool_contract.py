#!/usr/bin/env python3
"""Assert the Sigma tool surface matches the published contract and safety gates.

Two things drift silently and embarrass us:
  1. Tool, annotation, and per-profile counts quoted in README.
  2. The safety gates: read-only must keep exactly the readOnlyHint=True tools, and the
     bulk-destructive tools must stay listed in ``full`` but refused without the opt-in.

This runs in CI so both are caught before release.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

# Expected (total tools, tools annotated readOnlyHint=True) per profile. ``full`` is exhaustive:
# the two bulk tools are listed and refused at call time unless SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1.
EXPECTED_PROFILE_COUNTS: dict[str, tuple[int, int]] = {
    "full": (172, 90),
    "readonly": (90, 90),
    # Job (allowlist) profiles
    "analyst": (31, 23),
    "author": (40, 28),
    "modeler": (33, 24),
    "embed": (50, 27),
    "access_admin": (52, 22),
}
JOB_PROFILES = ("analyst", "author", "modeler", "embed", "access_admin")

# Expected tools per domain mount on ``full`` (README "Feature & Tool Summary" table).
EXPECTED_DOMAIN_COUNTS: dict[str, int] = {
    "workbooks": 54,
    "datasets": 22,
    "elements": 21,
    "workspace": 15,
    "admin": 60,
}

# Expected annotation split on ``full``.
EXPECTED_DEFAULT = EXPECTED_PROFILE_COUNTS["full"][0]
EXPECTED_READONLY = EXPECTED_PROFILE_COUNTS["readonly"][0]
EXPECTED_READ_ONLY = 90
EXPECTED_DESTRUCTIVE = 20
EXPECTED_IDEMPOTENT = 8

BULK_TOOLS = ("admin_bulk_deactivate_members", "admin_bulk_remove_team_members")
EXPECTED_FULL_ONLY = {
    "workspace_delete_file",
    "workspace_update_file",
    "workspace_create_tag",
    "workspace_delete_tag",
    "admin_list_translations",
    "admin_bulk_deactivate_members",
    "admin_bulk_remove_team_members",
    "admin_api_capabilities",
    "admin_list_recent_webhooks",
}

PROBE = """
import asyncio, json, sys
sys.path.insert(0, "src")
from sigma_mcp.profiles import FULL_ONLY_TOOLS, PROFILES
from sigma_mcp.server import create_server, mcp

def describe(tools):
    return {
        "total": len(tools),
        "read_only": sum(1 for t in tools if t.annotations and t.annotations.read_only_hint is True),
        "destructive": sum(1 for t in tools if t.annotations and t.annotations.destructive_hint is True),
        "idempotent": sum(1 for t in tools if t.annotations and t.annotations.idempotent_hint is True),
        "unannotated": sum(1 for t in tools if t.annotations is None),
        "missing_read_only_hint": sorted(
            t.name for t in tools if t.annotations is None or t.annotations.read_only_hint is None
        ),
        "all_read_only": all(t.annotations and t.annotations.read_only_hint is True for t in tools),
        "names": sorted(t.name for t in tools),
        "read_only_names": sorted(
            t.name for t in tools if t.annotations and t.annotations.read_only_hint is True
        ),
    }

async def main():
    report = {"default": describe(await mcp.list_tools()), "profiles": {}}
    report["prompt_names"] = sorted(p.name for p in await mcp.list_prompts())
    report["resource_uris"] = sorted(str(r.uri) for r in await mcp.list_resources())
    for name in PROFILES:
        report["profiles"][name] = describe(await create_server(profile=name).list_tools())
    report["full_only"] = sorted(FULL_ONLY_TOOLS)
    print(json.dumps(report))

asyncio.run(main())
"""


def probe(**env_overrides: str) -> dict[str, Any]:
    """Import the server under given env vars and report its tool surface per profile."""
    env = dict(os.environ)
    # Start from a clean slate so the host environment cannot skew results.
    for key in (
        "SIGMA_MCP_PROFILE",
        "SIGMA_MCP_READONLY",
        "SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE",
        "SIGMA_MCP_ENABLE_TOOL_SEARCH",
        "SIGMA_MCP_ENABLE_CODE_MODE",
        "SIGMA_MCP_TOOL_SEARCH_BACKEND",
    ):
        env.pop(key, None)
    env.update(env_overrides)
    env.setdefault("SIGMA_CLIENT_ID", "ci-placeholder")
    env.setdefault("SIGMA_CLIENT_SECRET", "ci-placeholder")

    out = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    res: dict[str, Any] = json.loads(out.stdout.strip().splitlines()[-1])
    return res


def parse_readme_counts() -> dict[str, Any]:
    """Extract tool counts and the profile table from README.md.

    Fails loudly if a required pattern is missing.
    """
    readme = (REPO / "README.md").read_text()
    patterns = {
        "default_total": r"\b(\d+)\s+tools covering connections",
        "readonly": r"readOnlyHint=true[`\s|]*(\d+)",
    }
    results: dict[str, Any] = {}
    for key, pat in patterns.items():
        m = re.search(pat, readme)
        if not m:
            print(
                f"FATAL: Could not locate README pattern for '{key}': /{pat}/",
                file=sys.stderr,
            )
            print(
                "Update README.md to include the expected count pattern, or update "
                "the regex in scripts/check_tool_contract.py.",
                file=sys.stderr,
            )
            sys.exit(2)
        results[key] = int(m.group(1))
    rows = re.findall(r"^\| `([a-z_]+)` \| [^|]+ \| \*\*(\d+)\*\* \| (\d+) \|$", readme, flags=re.M)
    results["profiles"] = {name: (int(total), int(ro)) for name, total, ro in rows}
    domain_alt = "|".join(EXPECTED_DOMAIN_COUNTS)
    domain_rows = re.findall(rf"^\| `({domain_alt})` \| (\d+) \|", readme, flags=re.M)
    results["domains"] = {name: int(count) for name, count in domain_rows}
    total = re.search(r"^\| \*\*Total\*\* \| \*\*(\d+)\*\* \|", readme, flags=re.M)
    results["domain_total"] = int(total.group(1)) if total else None
    return results


def main() -> int:
    failures: list[str] = []

    def check(label: str, actual: object, expected: object) -> None:
        if actual != expected:
            failures.append(f"{label}: expected {expected!r}, got {actual!r}")
        else:
            print(f"  ok  {label} = {actual!r}")

    print("README count validation:")
    readme_counts = parse_readme_counts()
    check("README default_total", readme_counts["default_total"], EXPECTED_DEFAULT)
    check("README readonly", readme_counts["readonly"], EXPECTED_READONLY)
    check("README profile table", readme_counts["profiles"], EXPECTED_PROFILE_COUNTS)
    check("README domain table", readme_counts["domains"], EXPECTED_DOMAIN_COUNTS)
    check("README domain table total", readme_counts["domain_total"], EXPECTED_DEFAULT)

    print("\nDefault registration (profile=full):")
    base = probe()
    default = base["default"]
    check("total tools", default["total"], EXPECTED_DEFAULT)
    check("read-only annotations", default["read_only"], EXPECTED_READ_ONLY)
    check("destructive annotations", default["destructive"], EXPECTED_DESTRUCTIVE)
    check("idempotent annotations", default["idempotent"], EXPECTED_IDEMPOTENT)
    check("unannotated tools", default["unannotated"], 0)
    check("tools without explicit readOnlyHint", default["missing_read_only_hint"], [])
    for name in BULK_TOOLS:
        check(f"{name} listed in full (gated at call time)", name in default["names"], True)
    check("FULL_ONLY_TOOLS", set(base["full_only"]), EXPECTED_FULL_ONLY)

    domains = tuple(EXPECTED_DOMAIN_COUNTS)
    live_domains = {domain: sum(1 for n in default["names"] if n.startswith(f"{domain}_")) for domain in domains}
    check("tools per domain", live_domains, EXPECTED_DOMAIN_COUNTS)
    sigma_names = [n for n in default["names"] if n.startswith("sigma_")]
    doubled = [
        n
        for n in default["names"]
        if any(n.startswith(f"{domain}_{domain}_") or n.startswith(f"{domain}_sigma_") for domain in domains)
    ]
    sigma_prompts = [n for n in base["prompt_names"] if n.startswith("sigma_")]
    sigma_resources = [u for u in base["resource_uris"] if u.startswith("sigma:") or u.startswith("sigma_")]
    undomain_prompts = [n for n in base["prompt_names"] if not any(n.startswith(f"{domain}_") for domain in domains)]
    check("no tool name starts with sigma_", sigma_names, [])
    check("no doubled domain prefix", doubled, [])
    check("no prompt name starts with sigma_", sigma_prompts, [])
    check("prompts use a domain prefix", undomain_prompts, [])
    check("no resource URI starts with sigma_", sigma_resources, [])

    print("\nProfiles (total tools):")
    for name, (total, _) in EXPECTED_PROFILE_COUNTS.items():
        check(f"{name} total", base["profiles"].get(name, {}).get("total"), total)
    check("profile names", set(base["profiles"]), set(EXPECTED_PROFILE_COUNTS))
    in_jobs = set().union(*(set(base["profiles"][j]["names"]) for j in JOB_PROFILES))
    check(
        "every full tool is in a job profile or FULL_ONLY_TOOLS",
        in_jobs | set(base["full_only"]),
        set(default["names"]),
    )
    check("job profiles and FULL_ONLY_TOOLS are disjoint", in_jobs & set(base["full_only"]), set())

    print("\nSIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1 (listing unchanged):")
    bulk = probe(SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE="1")
    check("total tools", bulk["default"]["total"], EXPECTED_DEFAULT)

    print("\nSIGMA_MCP_READONLY=1 composes with every profile (read-only tools only):")
    ro = probe(SIGMA_MCP_READONLY="1")
    for name, (_, read_only) in EXPECTED_PROFILE_COUNTS.items():
        report = ro["profiles"].get(name, {})
        check(f"{name}+readonly total", report.get("total"), read_only)
        check(f"{name}+readonly all read-only", report.get("all_read_only"), True)
        check(
            f"{name}+readonly names == {name} read-only names",
            report.get("names"),
            base["profiles"][name]["read_only_names"],
        )
    for name in BULK_TOOLS:
        check(f"{name} listed under readonly", name in ro["default"]["names"], False)

    print("\nSIGMA_MCP_READONLY=1 + SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1 (combined):")
    combined = probe(SIGMA_MCP_READONLY="1", SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE="1")
    check("combined: every tool is read-only", combined["default"]["all_read_only"], True)
    for name in BULK_TOOLS:
        check(f"combined: {name} listed", name in combined["default"]["names"], False)

    if failures:
        print(f"\nFAILED — {len(failures)} contract violation(s):", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        print(
            "\nIf this change was intentional, update the expected values at the "
            "top of scripts/check_tool_contract.py AND the counts in README.md.",
            file=sys.stderr,
        )
        return 1

    print("\nAll tool-contract assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
