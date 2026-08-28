"""Tests for pagination: auto_paginate, cursor pagination, list_all_ helpers."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

from sigma_mcp.client import SigmaClient


def _preauth_client() -> SigmaClient:
    c = SigmaClient("id", "secret", "https://api.example.com", max_retries=0, base_delay=0.001)
    c._token = "tok"
    c._token_expiry = time.time() + 3600
    return c


# ─── auto_paginate offset-style ──────────────────────────────────────────────


class TestAutoPaginate:
    async def test_follows_three_pages_preserving_order(self) -> None:
        c = _preauth_client()
        pages = [
            {"entries": [{"id": 1}, {"id": 2}], "nextPage": "p2"},
            {"entries": [{"id": 3}, {"id": 4}], "nextPage": "p3"},
            {"entries": [{"id": 5}]},
        ]
        call_idx = [0]

        async def mock_get(path: str, params: dict | None = None) -> dict:
            idx = call_idx[0]
            call_idx[0] += 1
            return pages[idx]

        with patch.object(c, "get", side_effect=mock_get):
            result = await c.auto_paginate("/v2/things")

        assert [e["id"] for e in result] == [1, 2, 3, 4, 5]

    async def test_terminates_when_next_page_absent(self) -> None:
        c = _preauth_client()

        async def mock_get(path: str, params: dict | None = None) -> dict:
            return {"entries": [{"id": "only"}]}

        with patch.object(c, "get", side_effect=mock_get):
            result = await c.auto_paginate("/v2/items")

        assert len(result) == 1

    async def test_handles_empty_entries(self) -> None:
        c = _preauth_client()

        async def mock_get(path: str, params: dict | None = None) -> dict:
            return {"entries": []}

        with patch.object(c, "get", side_effect=mock_get):
            result = await c.auto_paginate("/v2/empty")

        assert result == []

    async def test_no_infinite_loop_on_repeated_page_token(self) -> None:
        c = _preauth_client()
        call_count = [0]

        async def mock_get(path: str, params: dict | None = None) -> dict:
            call_count[0] += 1
            # Always echo back the same nextPage
            return {"entries": [{"id": call_count[0]}], "nextPage": "stuck-token"}

        with patch.object(c, "get", side_effect=mock_get):
            result = await c.auto_paginate("/v2/loop")

        # Should stop after seeing the repeated token (2 pages max: first + one with stuck-token)
        assert call_count[0] == 2
        assert len(result) == 2


# ─── Tenants cursor pagination ────────────────────────────────────────────────


class TestTenantsCursorPagination:
    async def test_cursor_pagination_terminates(self) -> None:
        from sigma_mcp.server import sigma_list_tenants_paginated

        mc = MagicMock()
        pages = [
            {"entries": [{"orgId": "t1"}], "nextPageToken": "cursor-2"},
            {"entries": [{"orgId": "t2"}], "nextPageToken": "cursor-3"},
            {"entries": [{"orgId": "t3"}]},  # no nextPageToken
        ]
        call_idx = [0]

        async def mock_get(path: str, params: dict | None = None) -> dict:
            idx = call_idx[0]
            call_idx[0] += 1
            return pages[idx]

        mc.get = AsyncMock(side_effect=mock_get)

        with patch("sigma_mcp.server.get_client", AsyncMock(return_value=mc)):
            result_str = await sigma_list_tenants_paginated()

        result = json.loads(result_str)
        assert result["total"] == 3
        assert [t["orgId"] for t in result["tenants"]] == ["t1", "t2", "t3"]


# ─── list_all_* helpers ───────────────────────────────────────────────────────


class TestListAllHelpers:
    async def test_list_all_workbooks_returns_concatenated(self) -> None:
        c = _preauth_client()
        pages = [
            {"entries": [{"workbookId": "w1"}], "nextPage": "p2"},
            {"entries": [{"workbookId": "w2"}]},
        ]
        call_idx = [0]

        async def mock_get(path: str, params: dict | None = None) -> dict:
            idx = call_idx[0]
            call_idx[0] += 1
            return pages[idx]

        with patch.object(c, "get", side_effect=mock_get):
            result = await c.list_all_workbooks()

        assert len(result) == 2
        assert result[0]["workbookId"] == "w1"
        assert result[1]["workbookId"] == "w2"

    async def test_list_all_members_returns_full_set(self) -> None:
        c = _preauth_client()

        async def mock_get(path: str, params: dict | None = None) -> dict:
            return {"entries": [{"memberId": "m1"}, {"memberId": "m2"}]}

        with patch.object(c, "get", side_effect=mock_get):
            result = await c.list_all_members()

        assert len(result) == 2
