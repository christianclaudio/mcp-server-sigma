#!/usr/bin/env python3
"""Assert the tool surface matches what the README publishes.

Two things drift silently and embarrass us:
  1. Tool counts and annotation counts quoted in README.
  2. The safety env vars — if gating breaks, destructive tools ship by default.

This runs in CI so both are caught before release.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Expected registered-tool counts per configuration.
EXPECTED_DEFAULT = 155
EXPECTED_WITH_BULK = 157
EXPECTED_READONLY = 83

# Expected annotation split at default registration.
EXPECTED_READ_ONLY = 83
EXPECTED_DESTRUCTIVE = 16
EXPECTED_IDEMPOTENT = 8

PROBE = """
import asyncio, json, sys
sys.path.insert(0, "src")
from sigma_mcp.server import mcp

async def main():
    tools = await mcp.list_tools()
    print(json.dumps({
        "total": len(tools),
        "read_only": sum(1 for t in tools if t.annotations and t.annotations.read_only_hint),
        "destructive": sum(1 for t in tools if t.annotations and t.annotations.destructive_hint),
        "idempotent": sum(1 for t in tools if t.annotations and t.annotations.idempotent_hint),
        "unannotated": sum(1 for t in tools if t.annotations is None),
        "all_read_only": all(t.annotations and t.annotations.read_only_hint for t in tools),
        "names": sorted(t.name for t in tools),
        "read_only_names": sorted(t.name for t in tools if t.annotations and t.annotations.read_only_hint),
    }))

asyncio.run(main())
"""


def probe(**env_overrides: str) -> dict:
    """Import the server under given env vars and report its tool surface."""
    import json

    env = dict(os.environ)
    # Start from a clean slate so the host environment cannot skew results.
    for key in ("SIGMA_MCP_PROFILE", "SIGMA_MCP_READONLY", "SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE"):
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
    return json.loads(out.stdout.strip().splitlines()[-1])


def parse_readme_counts() -> dict[str, int]:
    """Extract tool/annotation counts from README.md.

    Returns a dict of recognized keys to integer values.
    Fails loudly if a required pattern is missing.
    """
    readme = (REPO / "README.md").read_text()
    patterns = {
        "default_total": r"\b(\d+)\s+tools covering connections",
        "readonly": r"readOnlyHint=true[`\s|]*(\d+)",
        "with_bulk": r"\((\d+)\s+total\)",
    }
    results: dict[str, int] = {}
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
    return results


def main() -> int:
    failures: list[str] = []

    def check(label: str, actual: object, expected: object) -> None:
        if actual != expected:
            failures.append(f"{label}: expected {expected!r}, got {actual!r}")
        else:
            print(f"  ok  {label} = {actual!r}")

    # Validate README counts against computed values.
    print("README count validation:")
    readme_counts = parse_readme_counts()
    check("README default_total", readme_counts["default_total"], EXPECTED_DEFAULT)
    check("README readonly", readme_counts["readonly"], EXPECTED_READONLY)
    check("README with_bulk", readme_counts["with_bulk"], EXPECTED_WITH_BULK)

    print("\nDefault registration:")
    base = probe()
    check("total tools", base["total"], EXPECTED_DEFAULT)
    check("read-only annotations", base["read_only"], EXPECTED_READ_ONLY)
    check("destructive annotations", base["destructive"], EXPECTED_DESTRUCTIVE)
    check("idempotent annotations", base["idempotent"], EXPECTED_IDEMPOTENT)
    check("unannotated tools", base["unannotated"], 0)
    check(
        "bulk_deactivate absent by default",
        "sigma_bulk_deactivate_members" in base["names"],
        False,
    )
    check(
        "bulk_remove_team absent by default",
        "sigma_bulk_remove_team_members" in base["names"],
        False,
    )

    print("\nSIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1:")
    bulk = probe(SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE="1")
    check("total tools", bulk["total"], EXPECTED_WITH_BULK)
    check(
        "bulk_deactivate present with opt-in",
        "sigma_bulk_deactivate_members" in bulk["names"],
        True,
    )
    check(
        "bulk_remove_team present with opt-in",
        "sigma_bulk_remove_team_members" in bulk["names"],
        True,
    )

    print("\nSIGMA_MCP_READONLY=1:")
    ro = probe(SIGMA_MCP_READONLY="1")
    check("total tools", ro["total"], EXPECTED_READONLY)
    check("every tool is read-only", ro["all_read_only"], True)
    check(
        "bulk_remove_team absent from readonly",
        "sigma_bulk_remove_team_members" in ro["names"],
        False,
    )

    print("\nSIGMA_MCP_READONLY=1 + SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1 (combined):")
    combined = probe(SIGMA_MCP_READONLY="1", SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE="1")
    check("combined: every tool is read-only", combined["all_read_only"], True)
    check(
        "combined: bulk_deactivate absent",
        "sigma_bulk_deactivate_members" in combined["names"],
        False,
    )
    check(
        "combined: bulk_remove_team absent",
        "sigma_bulk_remove_team_members" in combined["names"],
        False,
    )

    print("\nProfiles:")
    core = probe(SIGMA_MCP_PROFILE="core")
    admin = probe(SIGMA_MCP_PROFILE="admin")
    embed = probe(SIGMA_MCP_PROFILE="embed")
    check("core < admin", core["total"] < admin["total"], True)
    check("core < embed", core["total"] < embed["total"], True)
    check("embed != core", embed["total"] != core["total"], True)
    check("admin < default", admin["total"] < base["total"], True)
    print(f"  info core={core['total']} admin={admin['total']} embed={embed['total']}")

    print("\nProfile + readonly compose:")
    admin_ro = probe(SIGMA_MCP_PROFILE="admin", SIGMA_MCP_READONLY="1")
    check("admin+readonly all read-only", admin_ro["all_read_only"], True)
    check("admin+readonly <= admin", admin_ro["total"] <= admin["total"], True)
    # Exact name-set comparison: every tool in admin+readonly must be exactly
    # the read-only subset from the admin probe (no missing or extra tools).
    admin_ro_name_set = set(admin_ro["names"])
    admin_read_only_name_set = set(admin["read_only_names"])
    check(
        "admin+readonly names == admin read-only names",
        admin_ro_name_set,
        admin_read_only_name_set,
    )

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
