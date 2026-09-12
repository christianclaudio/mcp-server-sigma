"""End-to-end live testing across all dynamically discovered MCP tools."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent

from sigma_mcp.server import _redact_secrets, mcp

SAFE_TOOL_FIXTURES: dict[str, dict[str, Any]] = {
    "sigma_delete_file": {"inode_id": "e2e-probe-file", "confirm": False},
    "sigma_delete_team": {"team_id": "e2e-probe-team", "confirm": False},
    "sigma_delete_tag": {"tag_id": "e2e-probe-tag", "confirm": False},
    "sigma_delete_workspace": {"workspace_id": "e2e-probe-ws", "confirm": False},
    "sigma_delete_workspace_grant": {
        "workspace_id": "e2e-probe-ws",
        "grant_id": "e2e-probe-grant",
        "confirm": False,
    },
    "sigma_delete_connection_path_grant": {
        "connection_path_id": "c-1",
        "grant_id": "g-1",
        "confirm": False,
    },
    "sigma_delete_workbook_schedule": {
        "workbook_id": "wb-1",
        "schedule_id": "s-1",
        "confirm": False,
    },
    "sigma_remove_workbook_tag": {"workbook_id": "wb-1", "tag_id": "t-1", "confirm": False},
    "sigma_archive_deployment": {"policy_id": "e2e-probe-pol", "confirm": False},
    "sigma_deactivate_member": {"member_id": "e2e-probe-mem", "confirm": False},
    "sigma_bulk_deactivate_members": {
        "name_pattern": "e2e-probe",
        "dry_run": True,
        "confirm": False,
    },
    "sigma_bulk_remove_team_members": {
        "team_id": "e2e-probe-team",
        "member_emails": ["probe@example.com"],
        "confirm": False,
    },
}


async def dispatch_tool_call(tool_name: str, is_destructive: bool) -> tuple[str, bool, str | None]:
    """Execute a single tool call and return (status, is_error, error_message)."""
    try:
        if tool_name in SAFE_TOOL_FIXTURES:
            res = await mcp.call_tool(tool_name, SAFE_TOOL_FIXTURES[tool_name])
        elif is_destructive:
            res = await mcp.call_tool(tool_name, {"confirm": False})
        elif "list" in tool_name:
            res = await mcp.call_tool(tool_name, {"limit": 5})
        else:
            res = await mcp.call_tool(tool_name, {})

        is_err = res.is_error if isinstance(res, CallToolResult) else False
        status = "FAIL" if is_err else "PASS"
        return (status, is_err, None)
    except Exception as exc:
        return ("FAIL", True, _redact_secrets(str(exc)))


@pytest.mark.asyncio
async def test_dispatch_tool_call_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify tool invocation logic and dispatch behavior using AsyncMock."""
    mock_call = AsyncMock()
    monkeypatch.setattr(mcp, "call_tool", mock_call)

    # 1. Successful non-destructive tool
    mock_call.return_value = CallToolResult(content=[TextContent(type="text", text="ok")], is_error=False)
    status, is_err, err = await dispatch_tool_call("sigma_get_user", is_destructive=False)
    assert status == "PASS"
    assert not is_err
    assert err is None
    mock_call.assert_awaited_with("sigma_get_user", {})

    # 2. Destructive tool with safety confirmation gate from fixture map
    status, is_err, err = await dispatch_tool_call("sigma_delete_file", is_destructive=True)
    assert status == "PASS"
    assert not is_err
    mock_call.assert_awaited_with("sigma_delete_file", {"inode_id": "e2e-probe-file", "confirm": False})

    # 3. List tool with limit pagination argument
    status, is_err, err = await dispatch_tool_call("sigma_list_workbooks", is_destructive=False)
    assert status == "PASS"
    assert not is_err
    mock_call.assert_awaited_with("sigma_list_workbooks", {"limit": 5})

    # 4. Error response with is_error=True
    mock_call.return_value = CallToolResult(content=[TextContent(type="text", text="error")], is_error=True)
    status, is_err, err = await dispatch_tool_call("sigma_get_user", is_destructive=False)
    assert status == "FAIL"
    assert is_err
    assert err is None

    # 5. Unexpected exception raised
    mock_call.side_effect = RuntimeError("transport broken with Bearer secret-tok")
    status, is_err, err = await dispatch_tool_call("sigma_get_user", is_destructive=False)
    assert status == "FAIL"
    assert is_err
    assert err is not None
    assert "secret-tok" not in err

    # 6. Non-CallToolResult return value (exercises False branch of isinstance)
    mock_call.side_effect = None
    mock_call.return_value = "plain string result"
    status, is_err, err = await dispatch_tool_call("sigma_get_user", is_destructive=False)
    assert status == "PASS"
    assert not is_err
    assert err is None


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_all_discovered_tools_live() -> None:
    """Dynamically discover and exercise registered tools against live endpoints."""
    tools = await mcp.list_tools()
    assert len(tools) > 0, "No tools registered in MCPServer"

    results: list[dict[str, Any]] = []

    for tool in tools:
        t0 = time.perf_counter()
        tool_name = tool.name
        annotations = tool.annotations
        is_destructive = getattr(annotations, "destructive_hint", False)

        status, is_err, err_msg = await dispatch_tool_call(tool_name, is_destructive)
        latency_ms = (time.perf_counter() - t0) * 1000

        res_entry: dict[str, Any] = {
            "tool": tool_name,
            "status": status,
            "latency_ms": latency_ms,
            "is_error": is_err,
        }
        if err_msg:
            res_entry["error"] = err_msg
        results.append(res_entry)

    failed = [r for r in results if r["status"] == "FAIL"]
    assert not failed, f"E2E live tool verification failed for: {failed}"
