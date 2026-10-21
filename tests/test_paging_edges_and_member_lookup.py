"""Paging edge cases (N1-N4) and member lookup by exact email.

- N1: the drift check sees ``GET /v2/workspaces/{}/grants``.
- N2: a cursor the caller passes in counts as read, so a stale echo of it ends the loop.
- N3: when the page cap runs out, the result carries the next cursor and ``hasMore: true``.
- N4: ``"entries": null`` is an empty page.
- ``search_members`` sends only ``email`` (Sigma removed ``search``) and refuses a name before
  any request; the two recipes that act on members accept only one exact (case-insensitive)
  email match. Reassignment changes nothing otherwise; bulk removal removes the emails that
  matched exactly one member and lists the rest in ``not_found``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.exceptions import ToolError
from scripts.check_openapi_drift import extract_client_calls

from sigma_mcp.client import SigmaClient
from sigma_mcp.tools import admin, workbooks


def _client() -> SigmaClient:
    c = SigmaClient("id", "secret", "https://api.sigmacomputing.com", max_retries=0, base_delay=0.001)
    c._token = "tok"
    c._token_expiry = time.time() + 3600
    return c


def _pages(*pages: Any) -> tuple[AsyncMock, list[tuple[str, dict[str, Any]]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    remaining = list(pages)

    async def fake_get(path: str, params: dict[str, Any] | None = None) -> Any:
        calls.append((path, dict(params or {})))
        return remaining.pop(0)

    return AsyncMock(side_effect=fake_get), calls


# ─── N1 ──────────────────────────────────────────────────────────────────
def test_drift_sees_the_workspace_grants_path() -> None:
    calls = extract_client_calls(Path("src/sigma_mcp/client.py"))
    gets = {c.normalized_path: c.query_params for c in calls if c.method == "GET"}
    assert gets["/v2/workspaces/{}/grants"] >= {"limit", "page"}


# ─── N2 ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("method", "kwargs", "cursor_key"),
    [
        ("list_tags", {"page": "p2"}, "nextPage"),
        ("list_workspaces", {"page": "p2"}, "nextPage"),
        ("list_workspace_grants", {"workspace_id": "ws-1", "page": "p2"}, "nextPage"),
        ("list_org_workbook_agents", {"page_token": "p2"}, "nextPageToken"),
        ("list_allowed_ips", {"page_token": "p2"}, "nextPageToken"),
    ],
)
async def test_a_resumed_cursor_echoed_back_is_not_read_twice(
    method: str, kwargs: dict[str, Any], cursor_key: str
) -> None:
    c = _client()
    get, calls = _pages({"entries": [{"id": "c"}], cursor_key: "p2"}, {"entries": [{"id": "c"}], cursor_key: None})
    with patch.object(c, "get", get):
        result = await getattr(c, method)(**kwargs)
    assert result["entries"] == [{"id": "c"}]
    assert len(calls) == 1


# ─── N3 ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("helper", "cursor_key", "request_key"),
    [("paged_list", "nextPage", "page"), ("paged_list_token", "nextPageToken", "pageToken")],
)
async def test_page_cap_returns_the_next_cursor_with_has_more(helper: str, cursor_key: str, request_key: str) -> None:
    c = _client()
    get, calls = _pages(
        {"entries": [{"id": "a"}], cursor_key: "p2", "total": 3},
        {"entries": [{"id": "b"}], cursor_key: "p3", "total": 3},
    )
    with patch.object(c, "get", get):
        result = await getattr(c, helper)("/v2/things", max_pages=2)
    assert result == {"entries": [{"id": "a"}, {"id": "b"}], cursor_key: "p3", "total": 3, "hasMore": True}
    assert calls[-1][1][request_key] == "p2"


async def test_a_list_past_the_cap_says_more_remain() -> None:
    c = _client()
    pages = [{"entries": [{"id": str(i)}], "nextPage": f"p{i + 1}", "hasMore": True} for i in range(1000)]
    get, calls = _pages(*pages)
    with patch.object(c, "get", get):
        result = await c.list_workbooks()
    assert len(calls) == 1000
    assert result["hasMore"] is True
    assert result["nextPage"] == "p1000"


# ─── N4 ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(("method", "cursor_key"), [("list_workbooks", "nextPage"), ("list_tenants", "nextPageToken")])
async def test_null_entries_is_an_empty_page(method: str, cursor_key: str) -> None:
    c = _client()
    get, calls = _pages({"entries": None, cursor_key: "p2"}, {"entries": [{"id": "b"}]})
    with patch.object(c, "get", get):
        result = await getattr(c, method)()
    assert result["entries"] == [{"id": "b"}]
    assert len(calls) == 2


async def test_auto_paginate_treats_null_entries_as_empty() -> None:
    c = _client()
    get, _ = _pages({"entries": None, "nextPage": "p2"}, {"entries": [{"id": "b"}]})
    with patch.object(c, "get", get):
        assert await c.auto_paginate("/v2/workbooks") == [{"id": "b"}]


# ─── search_members ──────────────────────────────────────────────────────
async def test_search_members_refuses_a_name_before_any_request() -> None:
    c = _client()
    c._request = AsyncMock()  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="email address"):
        await c.search_members("Ada Lovelace")
    c._request.assert_not_called()


async def test_search_members_sends_only_email() -> None:
    c = _client()
    get, calls = _pages({"entries": [{"memberId": "m1", "email": "ada@example.com"}]})
    with patch.object(c, "get", get):
        await c.search_members("ada@example.com")
    assert calls == [("/v2/members", {"email": "ada@example.com", "limit": 120})]


def _err(exc: pytest.ExceptionInfo[ToolError]) -> dict[str, Any]:
    return json.loads(str(exc.value))["error"]  # type: ignore[no-any-return]


def _api(members: dict[str, list[dict[str, Any]]]) -> AsyncMock:
    api = AsyncMock()

    async def search(email: str) -> dict[str, Any]:
        return {"entries": members.get(email, []), "nextPage": None}

    api.search_members = AsyncMock(side_effect=search)
    api.get = AsyncMock(return_value={"entries": [{"id": "f1", "ownerId": "old-id", "name": "WB"}]})
    api.update_file = AsyncMock(return_value={"ok": True})
    api.update_team_members = AsyncMock(return_value={"ok": True})
    return api


# Sigma's filter is not documented as exact: a look-alike must not be taken.
_LOOKALIKE = [{"memberId": "x-id", "email": "old@example.com.evil"}]
_TWO = [{"memberId": "a-id", "email": "old@example.com"}, {"memberId": "b-id", "email": "OLD@example.com"}]


@pytest.mark.parametrize("bad", ["Old Owner", "old-owner"])
async def test_reassign_refuses_a_name_before_any_request(bad: str) -> None:
    api = _api({})
    with patch.object(workbooks, "get_client", AsyncMock(return_value=api)):
        with pytest.raises(ToolError) as exc:
            await workbooks.sigma_reassign_workbook_ownership(bad, "new@example.com", dry_run=False)
    assert _err(exc)["type"] == "invalid_request"
    api.search_members.assert_not_called()
    api.update_file.assert_not_called()


@pytest.mark.parametrize(("found", "error_type"), [(_LOOKALIKE, "not_found"), (_TWO, "ambiguous_member")])
async def test_reassign_refuses_no_match_or_many(found: list[dict[str, Any]], error_type: str) -> None:
    api = _api({"old@example.com": found, "new@example.com": [{"memberId": "new-id", "email": "new@example.com"}]})
    with patch.object(workbooks, "get_client", AsyncMock(return_value=api)):
        with pytest.raises(ToolError) as exc:
            await workbooks.sigma_reassign_workbook_ownership("old@example.com", "new@example.com", dry_run=False)
    assert _err(exc)["type"] == error_type
    api.update_file.assert_not_called()


async def test_reassign_matches_email_ignoring_case() -> None:
    api = _api(
        {
            "Old@Example.com": [
                {"memberId": "x-id", "email": "old@example.com.evil"},
                {"memberId": "old-id", "email": "old@example.com"},
            ],
            "new@example.com": [{"memberId": "new-id", "email": "NEW@example.com"}],
        }
    )
    with patch.object(workbooks, "get_client", AsyncMock(return_value=api)):
        out = json.loads(
            await workbooks.sigma_reassign_workbook_ownership("Old@Example.com", "new@example.com", dry_run=False)
        )
    assert out["transferred"] == 1
    api.update_file.assert_awaited_once_with("f1", {"ownerId": "new-id"})


async def test_bulk_remove_refuses_a_name_before_any_request() -> None:
    api = _api({})
    with patch.object(admin, "get_client", AsyncMock(return_value=api)):
        with pytest.raises(ToolError) as exc:
            await admin.sigma_bulk_remove_team_members("t1", ["a@example.com", "Ada"], confirm=True)
    assert _err(exc)["type"] == "invalid_request"
    api.search_members.assert_not_called()
    api.update_team_members.assert_not_called()


@pytest.mark.parametrize("found", [_LOOKALIKE, _TWO])
async def test_bulk_remove_lists_no_match_or_many_in_not_found(found: list[dict[str, Any]]) -> None:
    """An email with no exact match or several is not acted on; it is listed in not_found."""
    api = _api({"old@example.com": found})
    with patch.object(admin, "get_client", AsyncMock(return_value=api)):
        with pytest.raises(ToolError) as exc:
            await admin.sigma_bulk_remove_team_members("t1", ["old@example.com"], confirm=True)
    err = _err(exc)
    assert err["type"] == "not_found"
    assert err["not_found"] == ["old@example.com"]
    api.update_team_members.assert_not_called()


async def test_bulk_remove_removes_only_the_single_match() -> None:
    """One match, one miss and one duplicate in one call: only the match is removed."""
    api = _api(
        {
            "ok@example.com": [{"memberId": "ok-id", "email": "ok@example.com"}],
            "old@example.com": _LOOKALIKE,
            "dup@example.com": [
                {"memberId": "a-id", "email": "dup@example.com"},
                {"memberId": "b-id", "email": "DUP@example.com"},
            ],
        }
    )
    emails = ["ok@example.com", "old@example.com", "dup@example.com"]
    with patch.object(admin, "get_client", AsyncMock(return_value=api)):
        out = json.loads(await admin.sigma_bulk_remove_team_members("t1", emails, confirm=True))
    assert out["removed"] == ["ok-id"]
    assert out["not_found"] == ["old@example.com", "dup@example.com"]
    assert set(out) == {"team_id", "removed", "not_found", "api_response"}
    api.update_team_members.assert_awaited_once_with("t1", {"remove": ["ok-id"]})


async def test_bulk_remove_matches_email_ignoring_case() -> None:
    api = _api(
        {
            "OK@Example.com": [
                {"memberId": "x-id", "email": "ok@example.com.evil"},
                {"memberId": "ok-id", "email": "ok@example.com"},
            ]
        }
    )
    with patch.object(admin, "get_client", AsyncMock(return_value=api)):
        out = json.loads(await admin.sigma_bulk_remove_team_members("t1", ["OK@Example.com"], confirm=True))
    assert out["removed"] == ["ok-id"]
    api.update_team_members.assert_awaited_once_with("t1", {"remove": ["ok-id"]})
