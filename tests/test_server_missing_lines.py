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
