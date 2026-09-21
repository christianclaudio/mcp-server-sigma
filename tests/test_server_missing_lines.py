"""Targeted tests covering remaining edge-case lines in server.py."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sigma_mcp import server as srv
from sigma_mcp.client import SigmaClient


@pytest.mark.asyncio
async def test_reassign_workbook_ownership_owned_matching(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    c.search_members = AsyncMock(
        side_effect=[
            {"entries": [{"memberId": "old_m1"}]},
            {"entries": [{"memberId": "new_m1"}]},
        ]
    )
    c.get = AsyncMock(
        return_value={
            "entries": [
                {"id": "f1", "ownerId": "old_m1", "name": "WB 1"},
                {"id": None, "ownerId": "old_m1", "name": "Bad ID"},
                {"id": "f2", "ownerId": "old_m1", "name": "WB 2"},
            ]
        }
    )
    c.update_file = AsyncMock(side_effect=[{"status": "ok"}, Exception("Update failed")])

    res = await srv.sigma_reassign_workbook_ownership("old@ex.com", "new@ex.com", dry_run=False)
    assert "transferred" in res


@pytest.mark.asyncio
async def test_bulk_remove_team_members_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    c.search_members = AsyncMock(
        side_effect=[
            {"entries": [{"memberId": "m1"}]},
            {"entries": []},
        ]
    )
    c.update_team_members = AsyncMock(return_value={"status": "ok"})

    res = await srv.sigma_bulk_remove_team_members("t1", ["found@ex.com", "missing@ex.com"], confirm=True)
    assert "removed" in res
    assert "not_found" in res


@pytest.mark.asyncio
async def test_onboard_member_team_addition_success(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    c.create_member = AsyncMock(return_value={"memberId": "m_new", "email": "new@ex.com"})
    c.update_team_members = AsyncMock(return_value={"status": "added"})

    res = await srv.sigma_onboard_member("new@ex.com", "First", "Last", team_ids=["t1"])
    assert "teams_added" in res


@pytest.mark.asyncio
async def test_list_all_input_tables_element_scanning(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    c.list_all_workbooks = AsyncMock(return_value=[{"workbookId": "wb1", "name": "Workbook 1"}])
    c.list_workbook_pages = AsyncMock(return_value={"entries": [{"pageId": "p1", "name": "Page 1"}]})
    c.list_workbook_page_elements = AsyncMock(
        return_value={
            "entries": [
                {"elementId": "el1", "name": "Input Table 1", "type": "input-table"},
                {"elementId": "el2", "name": "Chart 1", "type": "chart"},
            ]
        }
    )

    res = await srv.sigma_list_all_input_tables()
    assert "Input Table 1" in res


@pytest.mark.asyncio
async def test_call_tool_compat_reraises_other_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_call = AsyncMock(side_effect=RuntimeError("database crashed"))
    monkeypatch.setattr(srv, "_orig_call_tool", mock_call)
    with pytest.raises(RuntimeError, match="database crashed"):
        await srv.mcp.call_tool("sigma_api_capabilities", {})


@pytest.mark.asyncio
async def test_call_tool_compat_translates_not_found_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp.exceptions import NotFoundError
    from mcp.server.mcpserver.exceptions import ToolError

    mock_call = AsyncMock(side_effect=NotFoundError("Tool not found"))
    monkeypatch.setattr(srv, "_orig_call_tool", mock_call)
    with pytest.raises(ToolError, match="Unknown tool: 'test_missing_tool'"):
        await srv.mcp.call_tool("test_missing_tool", {})


def test_uri_compat_hash_and_equality() -> None:
    u1 = srv._UriCompat("sigma://reference/formulas")
    u2 = srv._UriCompat("sigma://reference/formulas")
    assert u1 == u2
    assert u1 == "sigma://reference/formulas"
    assert hash(u1) == hash("sigma://reference/formulas")
    assert len({u1, u2, "sigma://reference/formulas"}) == 1
    d = {u1: "found"}
    assert d["sigma://reference/formulas"] == "found"


def test_streamable_http_app_wildcard_host_validation() -> None:
    with pytest.raises(ValueError, match="Explicit allowed_hosts required"):
        srv._streamable_http_app(srv.mcp, host="0.0.0.0")

    with pytest.raises(ValueError, match="Explicit allowed_hosts required"):
        srv._streamable_http_app(srv.mcp, host="::")

    app = srv._streamable_http_app(srv.mcp, host="0.0.0.0", allowed_hosts=["my-domain.com"])
    assert app is not None


@pytest.mark.asyncio
async def test_server_lifespan_clears_client(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)
    async with srv.server_lifespan(srv.mcp) as ctx:
        assert ctx["client"] is c
    assert srv._client is None


@pytest.mark.asyncio
async def test_resource_list_compat_properties() -> None:
    res = await srv.mcp.read_resource("sigma://reference/formulas")
    assert res.contents is not None
    assert res.meta is None


@pytest.mark.asyncio
async def test_get_prompt_compat_no_args() -> None:
    p = await srv.mcp.get_prompt("provision_tenant_dashboard")
    assert p is not None
