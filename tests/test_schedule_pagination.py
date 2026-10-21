"""Workbook schedule and materialization-schedule lists follow Sigma's pagination.

Sigma's list endpoints return only paginated responses since 2026-06-02 (``limit`` plus a
``nextPage`` token; 50 entries per page by default), so a single GET stops at page one.
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from sigma_mcp.client import SigmaClient


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


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("list_workbook_schedules", "/v2/workbooks/wb-1/schedules"),
        ("list_materialization_schedules", "/v2/workbooks/wb-1/materialization-schedules"),
    ],
)
async def test_follows_next_page_and_sends_limit(method: str, path: str) -> None:
    c = _client()
    get, calls = _pages(
        {"entries": [{"id": "a"}, {"id": "b"}], "nextPage": "p2"},
        {"entries": [{"id": "c"}], "nextPage": None},
    )
    with patch.object(c, "get", get):
        result = await getattr(c, method)("wb-1")
    assert result == {"entries": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    assert [p for p, _ in calls] == [path, path]
    assert calls[0][1] == {"limit": 200}
    assert calls[1][1] == {"limit": 200, "page": "p2"}


async def test_legacy_array_response_is_kept() -> None:
    c = _client()
    get, calls = _pages([{"id": "a"}, "not-an-entry", {"id": "b"}])
    with patch.object(c, "get", get):
        result = await c.list_workbook_schedules("wb-1")
    assert result == {"entries": [{"id": "a"}, {"id": "b"}]}
    assert len(calls) == 1


async def test_auto_paginate_returns_array_entries() -> None:
    c = _client()
    get, _ = _pages([{"id": 1}])
    with patch.object(c, "get", get):
        assert await c.auto_paginate("/v2/things") == [{"id": 1}]


@pytest.mark.parametrize(
    ("module", "tool", "client_method"),
    [
        ("sigma_mcp.tools.workbooks", "sigma_list_workbook_schedules", "list_workbook_schedules"),
        ("sigma_mcp.tools.elements", "sigma_list_materialization_schedules", "list_materialization_schedules"),
    ],
)
async def test_tools_return_every_page(module: str, tool: str, client_method: str) -> None:
    import importlib

    mod = importlib.import_module(module)
    c = _client()
    get, _ = _pages(
        {"entries": [{"id": "a"}], "nextPage": "p2"},
        {"entries": [{"id": "b"}]},
    )
    with patch.object(c, "get", get), patch.object(mod, "get_client", AsyncMock(return_value=c)):
        out = json.loads(await getattr(mod, tool)("wb-1"))
    assert out == {"entries": [{"id": "a"}, {"id": "b"}]}
    assert hasattr(c, client_method)
