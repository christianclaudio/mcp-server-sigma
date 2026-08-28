"""Safety tests: destructive-op protections, readonly, bulk gating, profiles."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── sigma_bulk_deactivate_members safety ─────────────────────────────────────


def _mock_client_with_members(members: list[dict]) -> MagicMock:
    mc = MagicMock()
    mc.auto_paginate = AsyncMock(return_value=members)
    # deactivate_member is the path the tool actually uses; delete is stubbed
    # too so a regression back to raw path calls is still observable.
    mc.deactivate_member = AsyncMock(return_value=204)
    mc.delete = AsyncMock(return_value=200)
    mc.get = AsyncMock(return_value={})
    return mc


def _make_active_members(n: int) -> list[dict]:
    return [
        {"memberId": f"m-{i}", "firstName": f"Test{i}", "lastName": "User", "isActive": True, "isInactive": False}
        for i in range(n)
    ]


class TestBulkDeactivateSafety:
    @pytest.mark.parametrize("pattern", [".*", ".+", "^.*$", ".", ""])
    async def test_rejects_catchall_patterns_zero_deletes(self, pattern: str) -> None:
        from sigma_mcp.server import sigma_bulk_deactivate_members

        # Use a member whose name would match every catch-all pattern,
        # so the test actually exercises the rejection guard (not vacuous truth).
        members = [{"memberId": "m-0", "firstName": "A", "lastName": "B", "isActive": True, "isInactive": False}]
        mc = _mock_client_with_members(members)
        with patch("sigma_mcp.server.get_client", AsyncMock(return_value=mc)):
            result_str = await sigma_bulk_deactivate_members(pattern, dry_run=False, confirm=True)

        result = json.loads(result_str)
        assert "error" in result
        mc.deactivate_member.assert_not_called()
        mc.delete.assert_not_called()

    async def test_dry_run_default_zero_deletes(self) -> None:
        from sigma_mcp.server import sigma_bulk_deactivate_members

        members = _make_active_members(3)
        mc = _mock_client_with_members(members)
        with patch("sigma_mcp.server.get_client", AsyncMock(return_value=mc)):
            result_str = await sigma_bulk_deactivate_members("Test", dry_run=True)

        result = json.loads(result_str)
        assert result["dry_run"] is True
        mc.delete.assert_not_called()

    async def test_dry_run_false_confirm_false_zero_deletes(self) -> None:
        from sigma_mcp.server import sigma_bulk_deactivate_members

        members = _make_active_members(3)
        mc = _mock_client_with_members(members)
        with patch("sigma_mcp.server.get_client", AsyncMock(return_value=mc)):
            result_str = await sigma_bulk_deactivate_members("Test", dry_run=False, confirm=False)

        result = json.loads(result_str)
        # dry_run must echo the caller's argument, not a hardcoded True.
        assert result["dry_run"] is False
        # Blocked by confirm=False, so nothing may be deactivated.
        mc.deactivate_member.assert_not_called()
        mc.delete.assert_not_called()

    async def test_more_than_10_matches_refuses_zero_deletes(self) -> None:
        from sigma_mcp.server import sigma_bulk_deactivate_members

        members = _make_active_members(15)
        mc = _mock_client_with_members(members)
        with patch("sigma_mcp.server.get_client", AsyncMock(return_value=mc)):
            result_str = await sigma_bulk_deactivate_members("Test", dry_run=False, confirm=True)

        result = json.loads(result_str)
        assert "error" in result
        assert result["count"] == 15
        mc.deactivate_member.assert_not_called()

    async def test_happy_path_executes_deletes(self) -> None:
        from sigma_mcp.server import sigma_bulk_deactivate_members

        members = _make_active_members(3)
        mc = _mock_client_with_members(members)
        with patch("sigma_mcp.server.get_client", AsyncMock(return_value=mc)):
            result_str = await sigma_bulk_deactivate_members("Test", dry_run=False, confirm=True)

        result = json.loads(result_str)
        assert result["deactivated"] == 3
        # Deactivation routes through the documented client method
        # (DELETE /v2/members/{id}), not a raw delete() path call.
        assert mc.deactivate_member.call_count == 3

    async def test_bulk_remove_team_members_cap_exceeded(self) -> None:
        from sigma_mcp.server import sigma_bulk_remove_team_members

        too_many_emails = [f"user{i}@example.com" for i in range(51)]
        result_str = await sigma_bulk_remove_team_members("t1", too_many_emails, confirm=True)
        result = json.loads(result_str)
        assert "error" in result
        assert "Bulk removal cap exceeded" in result["error"]["message"]


# ─── sigma_reassign_workbook_ownership safety ─────────────────────────────────


class TestReassignWorkbookSafety:
    async def test_dry_run_default_no_patches(self) -> None:
        from sigma_mcp.server import sigma_reassign_workbook_ownership

        mc = MagicMock()
        mc.search_members = AsyncMock(
            side_effect=[
                {"entries": [{"memberId": "old-id"}]},
                {"entries": [{"memberId": "new-id"}]},
            ]
        )
        mc.get = AsyncMock(
            return_value={
                "entries": [{"id": "f1", "name": "WB1", "ownerId": "old-id"}],
            }
        )
        mc.update_file = AsyncMock()

        with patch("sigma_mcp.server.get_client", AsyncMock(return_value=mc)):
            result_str = await sigma_reassign_workbook_ownership("old@x.com", "new@x.com")

        result = json.loads(result_str)
        assert result["dry_run"] is True
        mc.update_file.assert_not_called()


# ─── SIGMA_MCP_READONLY=1 -> all tools read_only_hint ─────────────────────────


REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = sys.executable

PROBE_READONLY = """
import asyncio, json, sys
sys.path.insert(0, "src")
from sigma_mcp.server import mcp

async def main():
    tools = await mcp.list_tools()
    all_ro = all(t.annotations and t.annotations.read_only_hint for t in tools)
    print(json.dumps({"total": len(tools), "all_read_only": all_ro}))

asyncio.run(main())
"""


class TestReadonlyMode:
    def test_readonly_env_all_tools_read_only(self) -> None:
        env = dict(os.environ)
        env["SIGMA_MCP_READONLY"] = "1"
        env["SIGMA_CLIENT_ID"] = "test"
        env["SIGMA_CLIENT_SECRET"] = "test"
        # Clean conflicting env vars
        env.pop("SIGMA_MCP_PROFILE", None)
        env.pop("SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE", None)

        out = subprocess.run(
            [PYTHON, "-c", PROBE_READONLY],
            cwd=REPO_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        result = json.loads(out.stdout.strip())
        assert result["all_read_only"] is True
        assert result["total"] > 0


# ─── Bulk destructive gating ──────────────────────────────────────────────────

PROBE_BULK = """
import asyncio, json, sys
sys.path.insert(0, "src")
from sigma_mcp.server import mcp

async def main():
    tools = await mcp.list_tools()
    names = [t.name for t in tools]
    print(json.dumps({"names": names}))

asyncio.run(main())
"""


class TestBulkDestructiveGating:
    def test_bulk_tools_absent_without_env(self) -> None:
        env = dict(os.environ)
        env["SIGMA_CLIENT_ID"] = "test"
        env["SIGMA_CLIENT_SECRET"] = "test"
        env.pop("SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE", None)
        env.pop("SIGMA_MCP_PROFILE", None)
        env.pop("SIGMA_MCP_READONLY", None)

        out = subprocess.run(
            [PYTHON, "-c", PROBE_BULK],
            cwd=REPO_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        result = json.loads(out.stdout.strip())
        assert "sigma_bulk_deactivate_members" not in result["names"]
        assert "sigma_bulk_remove_team_members" not in result["names"]

    def test_bulk_tools_present_with_env(self) -> None:
        env = dict(os.environ)
        env["SIGMA_CLIENT_ID"] = "test"
        env["SIGMA_CLIENT_SECRET"] = "test"
        env["SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE"] = "1"
        env.pop("SIGMA_MCP_PROFILE", None)
        env.pop("SIGMA_MCP_READONLY", None)

        out = subprocess.run(
            [PYTHON, "-c", PROBE_BULK],
            cwd=REPO_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        result = json.loads(out.stdout.strip())
        assert "sigma_bulk_deactivate_members" in result["names"]
        assert "sigma_bulk_remove_team_members" in result["names"]


# ─── Profile filtering ────────────────────────────────────────────────────────

PROBE_PROFILE = """
import asyncio, json, sys
sys.path.insert(0, "src")
from sigma_mcp.server import mcp

async def main():
    tools = await mcp.list_tools()
    names = set(t.name for t in tools)
    print(json.dumps({"names": sorted(names), "count": len(names)}))

asyncio.run(main())
"""


class TestProfileFiltering:
    def _probe_profile(self, profile: str) -> dict:
        env = dict(os.environ)
        env["SIGMA_CLIENT_ID"] = "test"
        env["SIGMA_CLIENT_SECRET"] = "test"
        env["SIGMA_MCP_PROFILE"] = profile
        env.pop("SIGMA_MCP_READONLY", None)
        env.pop("SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE", None)

        out = subprocess.run(
            [PYTHON, "-c", PROBE_PROFILE],
            cwd=REPO_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(out.stdout.strip())

    def test_profile_counts(self) -> None:
        core = self._probe_profile("core")
        admin = self._probe_profile("admin")
        embed = self._probe_profile("embed")

        assert core["count"] > 0
        assert admin["count"] > core["count"]
        assert embed["count"] > core["count"]

    def test_core_is_subset_of_admin(self) -> None:
        core = set(self._probe_profile("core")["names"])
        admin = set(self._probe_profile("admin")["names"])
        assert core.issubset(admin), f"Core tools not in admin: {core - admin}"
