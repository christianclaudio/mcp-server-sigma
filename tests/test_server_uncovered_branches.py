"""Final branch coverage test suite targeting remaining server lines."""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import Response

from sigma_mcp import server as srv
from sigma_mcp.client import SigmaClient


def _mock_response(status_code: int = 200, content: bytes = b"pdf_data") -> Response:
    resp = MagicMock(spec=Response)
    resp.status_code = status_code
    resp.content = content
    return resp


@pytest.mark.asyncio
async def test_tenant_info_and_export_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    # sigma_get_tenant_scoped_info empty check
    assert "tenant_org_id is required" in await srv.sigma_get_tenant_scoped_info("")

    # sigma_get_tenant_scoped_info success
    tc = MagicMock(spec=SigmaClient)
    tc.get_current_user = AsyncMock(return_value={"email": "tenant_user@ex.com"})
    tc.aclose = AsyncMock()
    c.for_tenant = AsyncMock(return_value=tc)
    res_tenant = await srv.sigma_get_tenant_scoped_info("tenant_123")
    assert "tenant_user@ex.com" in res_tenant

    # sigma_deploy_template_to_folder without mapping
    c.save_workbook_from_template = AsyncMock(return_value={"workbookId": "wb_tmpl"})
    res_deploy = await srv.sigma_deploy_template_to_folder("t1", "f1", "Name")
    assert "wb_tmpl" in res_deploy

    # sigma_promote_workbook tag_name empty & tag creation failure
    assert "tag_name is required" in await srv.sigma_promote_workbook("wb1", "")

    c.list_tags = AsyncMock(return_value={"entries": []})
    c.create_tag = AsyncMock(return_value={})
    res_tag_fail = await srv.sigma_promote_workbook("wb1", "NewTag")
    assert "Could not resolve or create tag" in res_tag_fail

    # Success via create_tag — versionTagId returned
    c.list_tags = AsyncMock(return_value={"entries": []})
    c.create_tag = AsyncMock(return_value={"versionTagId": "vtag99", "name": "NewTag"})
    c.tag_workbook = AsyncMock(return_value={"status": "tagged"})
    res_tag_created = await srv.sigma_promote_workbook("wb1", "NewTag")
    assert "vtag99" in res_tag_created
    c.tag_workbook.assert_awaited_with("wb1", "NewTag")

    # sigma_export_and_download pdf layout & queryId missing & truncation
    c.export_workbook = AsyncMock(return_value={"queryId": "q123"})
    c.download_query_raw = AsyncMock(return_value=_mock_response(200, b"a" * 2000))
    res_pdf_trunc = await srv.sigma_export_and_download("wb1", format="pdf", layout="landscape", max_bytes=1000)
    assert "truncated" in res_pdf_trunc

    c.export_workbook = AsyncMock(return_value={})
    res_no_query = await srv.sigma_export_and_download("wb1")
    assert "No queryId in export response" in res_no_query


@pytest.mark.asyncio
async def test_bulk_deactivate_and_assign_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    # bulk assign team members validation
    assert "team_id is required" in await srv.sigma_bulk_assign_team_members("", ["m1"])
    assert "member_ids must be a non-empty list" in await srv.sigma_bulk_assign_team_members("t1", [])

    c.update_team_members = AsyncMock(return_value={"status": "assigned"})
    assert "assigned" in await srv.sigma_bulk_assign_team_members("t1", ["m1"])

    # change member email validation
    assert "member_id is required" in await srv.sigma_change_member_email("", "new@ex.com")
    assert "new_email is required" in await srv.sigma_change_member_email("m1", "")

    c.update_member = AsyncMock(return_value={"memberId": "m1", "email": "new@ex.com"})
    assert "new@ex.com" in await srv.sigma_change_member_email("m1", "new@ex.com")

    # bulk remove team members validation & no valid members return
    assert "Destructive operation requires" in await srv.sigma_bulk_remove_team_members(
        "t1", ["a@b.com"], confirm=False
    )
    assert "team_id is required" in await srv.sigma_bulk_remove_team_members("", ["a@b.com"], confirm=True)
    assert "member_emails must be a non-empty list" in await srv.sigma_bulk_remove_team_members("t1", [], confirm=True)

    c.search_members = AsyncMock(return_value={"entries": []})
    res_no_m = await srv.sigma_bulk_remove_team_members("t1", ["unknown@ex.com"], confirm=True)
    assert "No valid members found" in res_no_m

    # bulk deactivate empty string matching pattern
    res_empty_match = await srv.sigma_bulk_deactivate_members("a*", confirm=True)
    assert "matches empty string and is too broad" in res_empty_match

    c.auto_paginate = AsyncMock(
        return_value=[
            {"memberId": "", "firstName": "Bad", "lastName": "User", "isInactive": False, "isActive": True},
            {"memberId": "m1", "firstName": "Alice", "lastName": "Smith", "isInactive": True, "isActive": False},
            {"memberId": "m2", "firstName": "Bob", "lastName": "Jones", "isInactive": False, "isActive": True},
            {"memberId": "m3", "firstName": "Charlie", "lastName": "Fail", "isInactive": False, "isActive": True},
        ]
    )
    c.deactivate_member = AsyncMock(side_effect=[200, Exception("Deactivate error")])

    # bulk deactivate invalid regex
    res_bad_regex = await srv.sigma_bulk_deactivate_members("[invalid_regex", confirm=True)
    assert "Invalid regex pattern" in res_bad_regex

    res_exec = await srv.sigma_bulk_deactivate_members("Jones|Fail", dry_run=False, confirm=True)
    assert "Bob Jones" in res_exec
    assert "Deactivate error" in res_exec


@pytest.mark.asyncio
async def test_bulk_sync_tenant_connections_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    c.list_tenants = AsyncMock(return_value={"entries": [{"orgId": "t1", "name": "Tenant 1"}]})
    tc = MagicMock(spec=SigmaClient)
    tc.list_connections = AsyncMock(return_value={"entries": [{"connectionId": "conn1", "name": "DB 1"}]})
    tc.aclose = AsyncMock()
    c.for_tenant = AsyncMock(side_effect=Exception("Tenant auth error"))

    res_err = await srv.sigma_bulk_sync_tenant_connections(dry_run=False)
    assert "Tenant auth error" in res_err

    # Missing for_tenant method
    c_no_ft = MagicMock()
    del c_no_ft.for_tenant
    monkeypatch.setattr(srv, "_client", c_no_ft)
    res_no_ft = await srv.sigma_bulk_sync_tenant_connections(dry_run=True)
    assert "Multi-tenant auth" in res_no_ft


def test_server_profile_and_readonly_filtering(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        monkeypatch.setenv("SIGMA_MCP_PROFILE", "invalid_profile_name")
        with pytest.raises(ValueError, match="Unknown SIGMA_MCP_PROFILE"):
            importlib.reload(srv)

        monkeypatch.setenv("SIGMA_MCP_PROFILE", "core")
        importlib.reload(srv)
        assert "sigma_list_workbooks" in srv.mcp._tool_manager._tools

        monkeypatch.setenv("SIGMA_MCP_PROFILE", "full")
        monkeypatch.setenv("SIGMA_MCP_READONLY", "1")
        importlib.reload(srv)
        assert "sigma_delete_file" not in srv.mcp._tool_manager._tools
        assert "sigma_list_workbooks" in srv.mcp._tool_manager._tools

        monkeypatch.delenv("SIGMA_MCP_READONLY", raising=False)
        monkeypatch.setenv("SIGMA_MCP_PROFILE", "full")
        importlib.reload(srv)
        assert "sigma_delete_file" in srv.mcp._tool_manager._tools
    finally:
        monkeypatch.undo()
        importlib.reload(srv)


@pytest.mark.asyncio
async def test_add_connection_grant_invalid_grant_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers the invalid grant_type validation branch."""
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)
    res = await srv.sigma_add_connection_grant("conn1", "invalid_type", "grantee1", "usage")
    assert "grant_type must be" in res


@pytest.mark.asyncio
async def test_add_connection_grant_team_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers the team grantee_key branch."""
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)
    c.add_connection_grant = AsyncMock(return_value={})
    res = await srv.sigma_add_connection_grant("conn1", "team", "team123", "usage")
    assert "error" not in res.lower()[:50]
    call_body = c.add_connection_grant.call_args[0][1]
    assert "teamId" in call_body["grants"][0]["grantee"]


@pytest.mark.asyncio
async def test_create_workbook_embed_missing_source_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers the source_id validation for page/element embeds."""
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)
    res = await srv.sigma_create_workbook_embed("wb1", source_type="page")
    assert "source_id is required" in res


@pytest.mark.asyncio
async def test_export_workbook_auto_discover(monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers sigma_export_workbook auto-discovery of element when element_id is empty."""
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    c.list_workbook_pages = AsyncMock(return_value={"entries": [{"pageId": "p1"}]})
    c.list_workbook_page_elements = AsyncMock(return_value={"entries": [{"elementId": "e1"}]})
    c.export_workbook = AsyncMock(return_value={"queryId": "q1"})

    # Auto-discover with pdf format
    res = await srv.sigma_export_workbook("wb1")
    assert "q1" in res
    call_body = c.export_workbook.call_args[0][1]
    assert call_body["elementId"] == "e1"
    assert call_body["format"]["type"] == "pdf"
    assert call_body["format"]["layout"] == "portrait"

    # Explicit element_id with csv format
    await srv.sigma_export_workbook("wb1", element_id="ex1", format="csv")
    call_body2 = c.export_workbook.call_args[0][1]
    assert call_body2["elementId"] == "ex1"
    assert call_body2["format"] == {"type": "csv"}

    # No pages error
    c.list_workbook_pages = AsyncMock(return_value={"entries": []})
    res3 = await srv.sigma_export_workbook("wb1")
    assert "no pages" in res3.lower()

    # No elements error
    c.list_workbook_pages = AsyncMock(return_value={"entries": [{"pageId": "p1"}]})
    c.list_workbook_page_elements = AsyncMock(return_value={"entries": []})
    res4 = await srv.sigma_export_workbook("wb1")
    assert "no elements" in res4.lower()
