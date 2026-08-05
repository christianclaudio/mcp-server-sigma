"""Final push to achieve 100% test coverage across server.py prompts, resources, and validation branches."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sigma_mcp import server as srv
from sigma_mcp.client import SigmaClient


@pytest.mark.asyncio
async def test_prompts_and_resources_coverage() -> None:
    # Test prompt helper functions
    res_audit1 = srv.prompt_audit_organization_permissions("")
    assert "Perform an organization security" in res_audit1

    res_audit2 = srv.prompt_audit_organization_permissions("Engineering")
    assert "for team 'Engineering'" in res_audit2

    res_model = srv.prompt_prepare_data_model("conn1", "Sales Model")
    assert "Sales Model" in res_model


@pytest.mark.asyncio
async def test_recipe_validation_edge_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    # sigma_sync_all_tables_in_schema validation & success & exception
    assert "connection_id is required" in await srv.sigma_sync_all_tables_in_schema("", "db", "sch")
    assert "database is required" in await srv.sigma_sync_all_tables_in_schema("c1", "", "sch")
    assert "schema is required" in await srv.sigma_sync_all_tables_in_schema("c1", "db", "")

    c.sync_connection = AsyncMock(return_value={})
    res_sync_ok = await srv.sigma_sync_all_tables_in_schema("c1", "db", "sch")
    assert "synced_path" in res_sync_ok

    c.sync_connection = AsyncMock(side_effect=Exception("Sync error"))
    res_sync_err = await srv.sigma_sync_all_tables_in_schema("c1", "db", "sch")
    assert "Sync error" in res_sync_err

    # sigma_copy_workbook_to_member validation & homeFolderId check & success & exception
    assert "workbook_id is required" in await srv.sigma_copy_workbook_to_member("", "m1")
    assert "member_id is required" in await srv.sigma_copy_workbook_to_member("wb1", "")

    c.get_member = AsyncMock(return_value={"memberId": "m1"})
    res_no_home = await srv.sigma_copy_workbook_to_member("wb1", "m1")
    assert "Member has no homeFolderId" in res_no_home

    # With home folder but no explicit name — auto-fetches workbook name
    c.get_member = AsyncMock(return_value={"memberId": "m1", "homeFolderId": "hf1"})
    c.get_workbook = AsyncMock(return_value={"name": "My Workbook"})
    c.duplicate_workbook = AsyncMock(return_value={"workbookId": "wb_dup"})
    res_dup_ok = await srv.sigma_copy_workbook_to_member("wb1", "m1")
    assert "wb_dup" in res_dup_ok

    # Non-dict workbook response falls back to "Copy of {workbook_id}"
    c.get_member = AsyncMock(return_value={"memberId": "m1", "homeFolderId": "hf1"})
    c.get_workbook = AsyncMock(return_value="not-a-dict")
    c.duplicate_workbook = AsyncMock(return_value={"workbookId": "wb_dup2"})
    res_dup_fallback = await srv.sigma_copy_workbook_to_member("wb1", "m1")
    assert "wb_dup2" in res_dup_fallback

    # Explicit name bypasses get_workbook call
    c.get_member = AsyncMock(return_value={"memberId": "m1", "homeFolderId": "hf1"})
    c.duplicate_workbook = AsyncMock(return_value={"workbookId": "wb_dup3"})
    res_dup_named = await srv.sigma_copy_workbook_to_member("wb1", "m1", name="Custom Name")
    assert "wb_dup3" in res_dup_named

    c.get_member = AsyncMock(return_value={"memberId": "m1", "homeFolderId": "hf1"})
    c.get_workbook = AsyncMock(return_value={"name": "My Workbook"})
    c.duplicate_workbook = AsyncMock(side_effect=Exception("Duplicate failed"))
    res_dup_err = await srv.sigma_copy_workbook_to_member("wb1", "m1")
    assert "Duplicate failed" in res_dup_err

    # sigma_duplicate_workbook validation branches
    assert "name is required" in await srv.sigma_duplicate_workbook("wb1", "", "folder1")
    # When destination_folder_id is empty, it auto-discovers home folder; mock get_current_user + get_member
    c.get_current_user = AsyncMock(return_value={"userId": "u1"})
    c.get_member = AsyncMock(return_value={"memberId": "u1"})  # no homeFolderId
    assert "home folder" in await srv.sigma_duplicate_workbook("wb1", "My Copy", "")

    # sigma_convert_workbook_to_report validation branch
    assert "name is required" in await srv.sigma_convert_workbook_to_report("wb1", "")

    # sigma_reassign_workbook_ownership missing members & non-dict return
    c.search_members = AsyncMock(side_effect=[{"entries": []}, {"entries": []}])
    res_no_old = await srv.sigma_reassign_workbook_ownership("old@ex.com", "new@ex.com")
    assert "No member found for email: old@ex.com" in res_no_old

    c.search_members = AsyncMock(side_effect=[{"entries": [{"memberId": "m1"}]}, {"entries": []}])
    res_no_new = await srv.sigma_reassign_workbook_ownership("old@ex.com", "new@ex.com")
    assert "No member found for email: new@ex.com" in res_no_new

    c.search_members = AsyncMock(return_value={"entries": [{"memberId": "m1"}]})
    c.get = AsyncMock(return_value=["non_dict_response"])
    res_non_dict = await srv.sigma_reassign_workbook_ownership("a@b.com", "c@d.com", dry_run=True)
    assert "workbooks_to_transfer" in res_non_dict


@pytest.mark.asyncio
async def test_input_tables_pagination_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    c.list_all_workbooks = AsyncMock(return_value=[{"workbookId": "wb1", "name": "Workbook 1"}])
    c.list_workbook_pages = AsyncMock(
        side_effect=[
            {"entries": [{"pageId": "p1", "name": "Page 1"}], "nextPage": "cursor1"},
            {"entries": [{"pageId": "p1", "name": "Page 1"}], "nextPage": "cursor1"},
        ]
    )
    c.list_workbook_page_elements = AsyncMock(
        side_effect=[
            {"entries": [{"elementId": "el1", "name": "Input 1", "type": "input-table"}], "nextPage": "el_cur"},
            {"entries": [{"elementId": "el1", "name": "Input 1", "type": "input-table"}], "nextPage": "el_cur"},
        ]
    )

    res = await srv.sigma_list_all_input_tables()
    assert "Input 1" in res


@pytest.mark.asyncio
async def test_materialize_and_wait_timeout_and_no_job(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    # No job_id extracted
    c.materialize_workbook = AsyncMock(return_value={})
    res_no_job = await srv.sigma_materialize_and_wait("wb1", "el1")
    assert "Could not extract job ID" in res_no_job

    # Timeout scenario
    c.materialize_workbook = AsyncMock(return_value={"materializationId": "job123"})
    c.get_materialization_job = AsyncMock(return_value={"status": "running"})
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    res_timeout = await srv.sigma_materialize_and_wait("wb1", "el1", timeout_seconds=0)
    assert "timeout" in res_timeout


@pytest.mark.asyncio
async def test_additional_server_error_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    # sigma_reassign_workbook_ownership repeating cursor loop break
    c.search_members = AsyncMock(return_value={"entries": [{"memberId": "m1"}]})
    c.get = AsyncMock(
        side_effect=[
            {"entries": [{"id": "f1", "ownerId": "m1", "name": "WB 1"}], "nextPage": "same_cursor"},
            {"entries": [{"id": "f1", "ownerId": "m1", "name": "WB 1"}], "nextPage": "same_cursor"},
        ]
    )
    res_reassign_pag = await srv.sigma_reassign_workbook_ownership("a@b.com", "c@d.com", dry_run=True)
    assert "workbooks_to_transfer" in res_reassign_pag

    # sigma_onboard_member team update exception
    c.create_member = AsyncMock(return_value={"memberId": "m1", "email": "new@ex.com"})
    c.update_team_members = AsyncMock(side_effect=Exception("Team error"))
    res_onboard_err = await srv.sigma_onboard_member("new@ex.com", "F", "L", team_ids=["t1"])
    assert "FAILED" in res_onboard_err

    # sigma_bulk_remove_team_members exception
    c.search_members = AsyncMock(return_value={"entries": [{"memberId": "m1"}]})
    c.update_team_members = AsyncMock(side_effect=Exception("Team removal error"))
    res_rem_err = await srv.sigma_bulk_remove_team_members("t1", ["a@b.com"], confirm=True)
    assert "Team removal error" in res_rem_err

    # list_tenants_paginated non-dict response
    c.get = AsyncMock(return_value=["non_dict_response"])
    res_tenants_non_dict = await srv.sigma_list_tenants_paginated()
    assert "total" in res_tenants_non_dict

    # list_workbooks_shared_with_member
    assert "member_id is required" in await srv.sigma_list_workbooks_shared_with_member("")

    c.auto_paginate = AsyncMock(return_value=[{"workbookId": "wb1"}])
    c.list_all_workbooks = AsyncMock(return_value=[{"workbookId": "wb1", "name": "Shared WB"}])
    res_shared = await srv.sigma_list_workbooks_shared_with_member("m1")
    assert "Shared WB" in res_shared
