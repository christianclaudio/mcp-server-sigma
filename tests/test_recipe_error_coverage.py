"""Unit tests targeting error and validation paths in server recipe functions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import Response

from sigma_mcp import server as srv
from sigma_mcp.client import SigmaClient


def _mock_response(status_code: int = 200, json_data: dict | list | None = None) -> Response:
    resp = MagicMock(spec=Response)
    resp.status_code = status_code
    resp.headers = {}
    resp.text = "{}" if json_data is None else (str(json_data) if isinstance(json_data, str) else "")
    resp.json = MagicMock(return_value={} if json_data is None else (json_data if json_data is not None else {}))
    return resp


@pytest.mark.asyncio
async def test_recipe_validation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    c._http = MagicMock()
    c._http.request = AsyncMock(return_value=_mock_response(200, {"entries": []}))
    monkeypatch.setattr(srv, "_client", c)

    # Validation errors on empty/invalid params
    assert "template_id is required" in await srv.sigma_deploy_template_to_folder("", "f1", "name")
    assert "folder_id is required" in await srv.sigma_deploy_template_to_folder("t1", "", "name")
    assert "name is required" in await srv.sigma_deploy_template_to_folder("t1", "f1", "")

    assert "workbook_id is required" in await srv.sigma_materialize_and_wait("", "e1")
    assert "element_id is required" in await srv.sigma_materialize_and_wait("wb1", "")

    assert "new_owner_email is required" in await srv.sigma_reassign_workbook_ownership("old@ex.com", "")
    assert "old_owner_email is required" in await srv.sigma_reassign_workbook_ownership("", "new@ex.com")

    assert "member_id is required" in await srv.sigma_list_workbooks_shared_with_member("")

    assert "email is required" in await srv.sigma_onboard_member("", "A", "B")
    assert "first_name is required" in await srv.sigma_onboard_member("a@b.com", "", "B")
    assert "last_name is required" in await srv.sigma_onboard_member("a@b.com", "A", "")

    assert "team_id is required" in await srv.sigma_bulk_assign_team_members("", ["a@b.com"])
    assert "member_ids must be a non-empty list" in await srv.sigma_bulk_assign_team_members("t1", [])

    assert "name_pattern is required" in await srv.sigma_bulk_deactivate_members("", confirm=True)
    assert "is rejected for safety" in await srv.sigma_bulk_deactivate_members(".*", confirm=True)
    assert "is rejected for safety" in await srv.sigma_bulk_deactivate_members(".+", confirm=True)

    assert "member_id is required" in await srv.sigma_change_member_email("", "a@b.com")
    assert "new_email is required" in await srv.sigma_change_member_email("m1", "")


@pytest.mark.asyncio
async def test_composite_recipes_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    c.list_all_workbooks = AsyncMock(return_value=[{"workbookId": "wb1", "name": "Workbook 1"}])
    c.list_workbook_pages = AsyncMock(return_value={"entries": [{"pageId": "p1", "name": "Page 1"}]})
    c.list_workbook_page_elements = AsyncMock(
        return_value={"entries": [{"elementId": "el1", "name": "Input 1", "type": "input-table"}]}
    )
    res_scan = await srv.sigma_list_all_input_tables()
    assert "Input 1" in res_scan

    # Mock template deployment with source mapping
    c.save_workbook_from_template = AsyncMock(return_value={"workbookId": "wb_new"})
    c.swap_workbook_sources = AsyncMock(return_value={"status": "swapped"})
    res_deploy = await srv.sigma_deploy_template_to_folder(
        "tmpl1", "f1", "Deployed Workbook", connection_mapping=[{"source": "c1", "target": "c2"}]
    )
    assert "swapped" in res_deploy

    # Mock promote workbook — tag already exists (lookup by versionTagId)
    c.list_tags = AsyncMock(return_value={"entries": [{"versionTagId": "tag1", "name": "Production"}]})
    c.tag_workbook = AsyncMock(return_value={"status": "tagged"})
    res_promote = await srv.sigma_promote_workbook("wb1", "Production")
    assert "Production" in res_promote
    assert "tag1" in res_promote
    c.tag_workbook.assert_awaited_with("wb1", "Production")

    # Mock bulk sync tenant connections
    c.list_tenants = AsyncMock(return_value={"entries": [{"orgId": "org1", "name": "Tenant 1"}]})
    res_dry = await srv.sigma_bulk_sync_tenant_connections(dry_run=True)
    assert "dry_run" in res_dry

    tenant_client = MagicMock(spec=SigmaClient)
    tenant_client.list_connections = AsyncMock(return_value={"entries": [{"connectionId": "c1"}]})
    tenant_client.sync_connection = AsyncMock(return_value={})
    tenant_client.aclose = AsyncMock()
    c.for_tenant = AsyncMock(return_value=tenant_client)

    res_exec = await srv.sigma_bulk_sync_tenant_connections(dry_run=False)
    assert "connections_synced" in res_exec


@pytest.mark.asyncio
async def test_bulk_deactivate_member_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    c.auto_paginate = AsyncMock(
        return_value=[
            {"memberId": f"m{i}", "firstName": "Viewer", "lastName": f"User{i}", "isInactive": False, "isActive": True}
            for i in range(15)
        ]
    )
    c.deactivate_member = AsyncMock()
    monkeypatch.setattr(srv, "_client", c)

    # Should refuse due to >10 cap
    res = await srv.sigma_bulk_deactivate_members("Viewer", dry_run=False, confirm=True)
    assert "exceeding the safety cap of 10" in res
    c.deactivate_member.assert_not_awaited()
