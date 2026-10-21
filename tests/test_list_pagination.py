"""List tools added for client methods that had none follow Sigma's pagination to the last page.

Sigma pages its lists two ways: ``limit``/``page`` with a ``nextPage`` token, or
``pageSize``/``pageToken`` with a ``nextPageToken``. Each tool here uses the style its
endpoint's spec names, follows it to the last page and returns the keys of Sigma's page with ``entries`` from every page.
"""

from __future__ import annotations

import importlib
import json
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from sigma_mcp.client import SigmaClient

# (tool module, tool and client method, positional args, path, pagination style)
LISTS: list[tuple[str, str, tuple[str, ...], str, str]] = [
    ("admin", "list_member_schedules", ("m-1",), "/v2/members/m-1/schedules", "page"),
    ("datasets", "list_connection_paths", (), "/v2/connections/paths", "page"),
    ("datasets", "list_connection_path_grants", ("cp-1",), "/v2/connections/paths/cp-1/grants", "page"),
    ("datasets", "list_data_model_tags", ("dm-1",), "/v2/dataModels/dm-1/tags", "page"),
    ("workspace", "list_member_files", ("m-1",), "/v2/members/m-1/files", "page"),
    ("workspace", "list_recent_files", ("m-1",), "/v2/members/m-1/files/recents", "page"),
    ("workspace", "list_workbooks_for_tag", ("t-1",), "/v2/tags/t-1/workbooks", "page"),
    ("workspace", "list_favorites_for_member", ("m-1",), "/v2/favorites/member/m-1", "page"),
    ("admin", "list_deployable_tenants", (), "/v2/deploymentPolicies/tenants", "token"),
    ("admin", "list_deployment_tenants", ("dp-1",), "/v2/deploymentPolicies/dp-1/tenants", "token"),
    (
        "admin",
        "list_tenant_deployment_capabilities",
        ("org-1",),
        "/v2/tenants/org-1/capabilities/deployments",
        "token",
    ),
    ("admin", "list_api_credentials", (), "/v2/api-credentials", "token"),
    ("datasets", "list_data_model_sources", ("dm-1",), "/v2/dataModels/dm-1/sources", "token"),
    (
        "datasets",
        "list_data_model_materialization_schedules",
        ("dm-1",),
        "/v2/dataModels/dm-1/materializationSchedules",
        "token",
    ),
    ("workbooks", "list_report_pages", ("r-1",), "/v2/reports/r-1/pages", "token"),
    ("workbooks", "list_report_page_elements", ("r-1", "p-1"), "/v2/reports/r-1/pages/p-1/elements", "token"),
    ("workbooks", "list_report_columns", ("r-1",), "/v2/reports/r-1/columns", "token"),
    ("workbooks", "list_report_controls", ("r-1",), "/v2/reports/r-1/controls", "token"),
    ("workbooks", "get_report_version_history", ("r-1",), "/v2/reports/r-1/version-history", "token"),
    ("workbooks", "list_report_tags", ("r-1",), "/v2/reports/r-1/tags", "token"),
]

_NEXT = {"page": ("nextPage", "limit", "page"), "token": ("nextPageToken", "pageSize", "pageToken")}


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


def _three_pages(style: str) -> tuple[dict[str, Any], ...]:
    nxt = _NEXT[style][0]
    return (
        {"entries": [{"id": "a"}, {"id": "b"}], nxt: "p2"},
        {"entries": [{"id": "c"}], nxt: "p3"},
        {"entries": [{"id": "d"}], nxt: None},
    )


def test_table_lists_every_paginated_new_tool() -> None:
    assert len(LISTS) == 20
    assert len({name for _, name, *_ in LISTS}) == 20


@pytest.mark.parametrize(("module", "name", "args", "path", "style"), LISTS)
async def test_client_follows_every_page(module: str, name: str, args: tuple[str, ...], path: str, style: str) -> None:
    c = _client()
    get, calls = _pages(*_three_pages(style))
    with patch.object(c, "get", get):
        result = await getattr(c, name)(*args)
    assert result == {"entries": [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}], _NEXT[style][0]: None}
    _, size, cursor = _NEXT[style]
    assert [p for p, _ in calls] == [path, path, path]
    assert calls[0][1] == {size: 200}
    assert calls[1][1] == {size: 200, cursor: "p2"}
    assert calls[2][1] == {size: 200, cursor: "p3"}


@pytest.mark.parametrize(("module", "name", "args", "path", "style"), LISTS)
async def test_tool_returns_every_page(module: str, name: str, args: tuple[str, ...], path: str, style: str) -> None:
    mod = importlib.import_module(f"sigma_mcp.tools.{module}")
    c = _client()
    get, calls = _pages(*_three_pages(style))
    with patch.object(c, "get", get), patch.object(mod, "get_client", AsyncMock(return_value=c)):
        out = json.loads(await getattr(mod, f"sigma_{name}")(*args))
    assert out == {"entries": [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}], _NEXT[style][0]: None}
    assert len(calls) == 3


async def test_list_member_files_limit_is_the_page_size() -> None:
    c = _client()
    get, calls = _pages({"entries": [{"id": "a"}], "nextPage": "p2"}, {"entries": [{"id": "b"}]})
    with patch.object(c, "get", get):
        result = await c.list_member_files("m-1", limit=25)
    assert result == {"entries": [{"id": "a"}, {"id": "b"}], "nextPage": None}
    assert [params for _, params in calls] == [{"limit": 25}, {"limit": 25, "page": "p2"}]


async def test_token_pagination_keeps_a_bare_array() -> None:
    c = _client()
    get, calls = _pages([{"id": "a"}, "not-an-entry", {"id": "b"}])
    with patch.object(c, "get", get):
        assert await c.auto_paginate_token("/v2/things") == [{"id": "a"}, {"id": "b"}]
    assert len(calls) == 1


async def test_token_pagination_stops_on_a_repeated_token() -> None:
    c = _client()
    get, calls = _pages(
        {"entries": [{"id": "a"}], "nextPageToken": "t2"},
        {"entries": [{"id": "b"}], "nextPageToken": "t2"},
    )
    with patch.object(c, "get", get):
        assert await c.auto_paginate_token("/v2/things", {"name": "x"}) == [{"id": "a"}, {"id": "b"}]
    assert calls[0][1] == {"name": "x", "pageSize": 200}
    assert len(calls) == 2


async def test_token_pagination_stops_at_max_pages() -> None:
    c = _client()
    get, calls = _pages(
        {"entries": [{"id": "a"}], "nextPageToken": "t2"},
        {"entries": [{"id": "b"}], "nextPageToken": "t3"},
    )
    with patch.object(c, "get", get):
        assert await c.auto_paginate_token("/v2/things", page_size=10, max_pages=2) == [{"id": "a"}, {"id": "b"}]
    assert [params for _, params in calls] == [{"pageSize": 10}, {"pageSize": 10, "pageToken": "t2"}]
