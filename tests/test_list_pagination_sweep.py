"""Every older list method on a paged endpoint follows Sigma's pagination to the last page.

The audit of every list-like ``SigmaClient`` method against Sigma's spec found 46 that sent
one GET to an endpoint the spec pages, so each stopped at the first page (50 entries by
default, or the hardcoded ``limit``), and 5 that returned one page and left the cursor to the
caller. Each now uses the style its endpoint's spec names:
``limit``/``page`` with ``nextPage`` (``auto_paginate``) or ``pageSize``/``pageToken`` with
``nextPageToken`` (``auto_paginate_token``), 200 per page. The result keeps the keys of
Sigma's page: ``entries`` from every page, ``total`` as the first page sent it, and the
cursor (``nextPage`` or ``nextPageToken``) null, since every page has been read.
A tool's ``limit`` argument, where it has one, is the page size.
"""

from __future__ import annotations

import importlib
import json
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from sigma_mcp.client import SigmaClient

# (client method, positional args, path, style, fixed query params, tool module, tool, tool args)
SWEEP: list[tuple[str, tuple[Any, ...], str, str, dict[str, Any], str, str, tuple[Any, ...]]] = [
    # limit/page, nextPage
    ("list_connections", (), "/v2/connections", "page", {}, "datasets", "list_connections", ()),
    (
        "list_connection_grants",
        ("c-1",),
        "/v2/connections/c-1/grants",
        "page",
        {},
        "datasets",
        "list_connection_grants",
        ("c-1",),
    ),
    ("list_workbooks", (), "/v2/workbooks", "page", {}, "workbooks", "list_workbooks", ()),
    (
        "list_workbook_pages",
        ("w-1",),
        "/v2/workbooks/w-1/pages",
        "page",
        {},
        "workbooks",
        "list_workbook_pages",
        ("w-1",),
    ),
    (
        "list_workbook_elements",
        ("w-1",),
        "/v2/workbooks/w-1/elements",
        "page",
        {},
        "elements",
        "list_workbook_elements",
        ("w-1",),
    ),
    (
        "list_workbook_columns",
        ("w-1",),
        "/v2/workbooks/w-1/columns",
        "page",
        {},
        "elements",
        "list_workbook_columns",
        ("w-1",),
    ),
    (
        "list_workbook_queries",
        ("w-1",),
        "/v2/workbooks/w-1/queries",
        "page",
        {},
        "elements",
        "list_workbook_queries",
        ("w-1",),
    ),
    (
        "list_workbook_controls",
        ("w-1",),
        "/v2/workbooks/w-1/controls",
        "page",
        {},
        "elements",
        "list_workbook_controls",
        ("w-1",),
    ),
    (
        "get_workbook_version_history",
        ("w-1",),
        "/v2/workbooks/w-1/version-history",
        "page",
        {},
        "workbooks",
        "get_workbook_version_history",
        ("w-1",),
    ),
    (
        "list_workbook_lineage",
        ("w-1",),
        "/v2/workbooks/w-1/lineage",
        "page",
        {},
        "workbooks",
        "list_workbook_lineage",
        ("w-1",),
    ),
    (
        "list_workbook_grants",
        ("w-1",),
        "/v2/grants",
        "page",
        {"inodeId": "w-1"},
        "workbooks",
        "list_workbook_grants",
        ("w-1",),
    ),
    (
        "list_workbook_embeds",
        ("w-1",),
        "/v2/workbooks/w-1/embeds",
        "page",
        {},
        "workbooks",
        "list_workbook_embeds",
        ("w-1",),
    ),
    (
        "list_workbook_bookmarks",
        ("w-1",),
        "/v2/workbooks/w-1/bookmarks",
        "page",
        {},
        "workbooks",
        "list_workbook_bookmarks",
        ("w-1",),
    ),
    ("get_workbook_tags", ("w-1",), "/v2/workbooks/w-1/tags", "page", {}, "workbooks", "get_workbook_tags", ("w-1",)),
    (
        "list_workbook_page_elements",
        ("w-1", "p-1"),
        "/v2/workbooks/w-1/pages/p-1/elements",
        "page",
        {},
        "elements",
        "list_workbook_page_elements",
        ("w-1", "p-1"),
    ),
    (
        "get_element_columns",
        ("w-1", "e-1"),
        "/v2/workbooks/w-1/elements/e-1/columns",
        "page",
        {},
        "elements",
        "get_element_columns",
        ("w-1", "e-1"),
    ),
    ("list_templates", (), "/v2/templates", "page", {}, "workbooks", "list_templates", ()),
    ("list_data_models", (), "/v2/dataModels", "page", {}, "datasets", "list_data_models", ()),
    (
        "list_data_model_elements",
        ("d-1",),
        "/v2/dataModels/d-1/elements",
        "page",
        {},
        "datasets",
        "list_data_model_elements",
        ("d-1",),
    ),
    (
        "list_data_model_columns",
        ("d-1",),
        "/v2/dataModels/d-1/columns",
        "page",
        {},
        "datasets",
        "list_data_model_columns",
        ("d-1",),
    ),
    (
        "list_data_model_lineage",
        ("d-1",),
        "/v2/dataModels/d-1/lineage",
        "page",
        {},
        "datasets",
        "list_data_model_lineage",
        ("d-1",),
    ),
    ("list_members", (), "/v2/members", "page", {}, "admin", "list_members", ()),
    ("list_member_teams", ("m-1",), "/v2/members/m-1/teams", "page", {}, "admin", "list_member_teams", ("m-1",)),
    ("list_teams", (), "/v2/teams", "page", {}, "admin", "list_teams", ()),
    ("list_team_members", ("t-1",), "/v2/teams/t-1/members", "page", {}, "admin", "list_team_members", ("t-1",)),
    ("list_files", ({"limit": 200},), "/v2/files", "page", {}, "workspace", "list_files", ()),
    ("list_user_attributes", (), "/v2/user-attributes", "page", {}, "admin", "list_user_attributes", ()),
    (
        "get_user_attribute_user_assignments",
        ("ua-1",),
        "/v2/user-attributes/ua-1/users",
        "page",
        {},
        "admin",
        "get_user_attribute_users",
        ("ua-1",),
    ),
    (
        "get_user_attribute_team_assignments",
        ("ua-1",),
        "/v2/user-attributes/ua-1/teams",
        "page",
        {},
        "admin",
        "get_user_attribute_teams",
        ("ua-1",),
    ),
    (
        "get_user_attribute_tenant_assignments",
        ("ua-1",),
        "/v2/user-attributes/ua-1/tenants",
        "page",
        {},
        "admin",
        "get_user_attribute_tenants",
        ("ua-1",),
    ),
    ("list_grants", ({"inodeId": "i-1"},), "/v2/grants", "page", {"inodeId": "i-1"}, "admin", "list_grants", ("i-1",)),
    ("list_reports", (), "/v2/reports", "page", {}, "workbooks", "list_reports", ()),
    (
        "list_report_schedules",
        ("r-1",),
        "/v2/reports/r-1/schedules",
        "page",
        {},
        "workbooks",
        "list_report_schedules",
        ("r-1",),
    ),
    (
        "list_source_swap_policies",
        (),
        "/v2/sourceSwapPolicies",
        "page",
        {},
        "elements",
        "list_source_swap_policies",
        (),
    ),
    (
        "list_shared_templates",
        (),
        "/v2/shared_templates/shared_with_you",
        "page",
        {},
        "workbooks",
        "list_shared_templates",
        (),
    ),
    ("list_translations", (), "/v2/translations/organization", "page", {}, "admin", "list_translations", ()),
    # pageSize/pageToken, nextPageToken
    (
        "list_columns_for_table",
        ("tb-1",),
        "/v2/connections/tables/tb-1/columns",
        "token",
        {},
        "datasets",
        "list_columns_for_table",
        ("tb-1",),
    ),
    ("list_account_types", (), "/v2/accountTypes", "token", {}, "admin", "list_account_types", ()),
    (
        "list_report_elements",
        ("r-1",),
        "/v2/reports/r-1/elements",
        "token",
        {},
        "workbooks",
        "list_report_elements",
        ("r-1",),
    ),
    (
        "list_report_queries",
        ("r-1",),
        "/v2/reports/r-1/queries",
        "token",
        {},
        "workbooks",
        "list_report_queries",
        ("r-1",),
    ),
    (
        "list_report_lineage",
        ("r-1",),
        "/v2/reports/r-1/lineage",
        "token",
        {},
        "workbooks",
        "list_report_lineage",
        ("r-1",),
    ),
    ("list_deployments", (), "/v2/deploymentPolicies", "token", {}, "admin", "list_deployments", ()),
    (
        "list_deployment_documents",
        ("dp-1",),
        "/v2/deploymentPolicies/dp-1/files",
        "token",
        {},
        "admin",
        "list_deployment_documents",
        ("dp-1",),
    ),
    ("list_tenants", (), "/v2/tenants", "token", {}, "admin", "list_tenants", ()),
    ("list_api_connectors", (), "/v2/api-connectors", "token", {}, "admin", "list_api_connectors", ()),
]

_NEXT = {"page": ("nextPage", "limit", "page"), "token": ("nextPageToken", "pageSize", "pageToken")}
_ALL = [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}]


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
        {"entries": [{"id": "a"}, {"id": "b"}], nxt: "p2", "total": 4},
        {"entries": [{"id": "c"}], nxt: "p3", "total": 4},
        {"entries": [{"id": "d"}], nxt: None, "total": 4},
    )


def test_sweep_covers_every_method_once() -> None:
    assert len(SWEEP) == 45
    assert len({row[0] for row in SWEEP}) == 45
    assert sum(1 for row in SWEEP if row[3] == "page") == 36
    assert sum(1 for row in SWEEP if row[3] == "token") == 9


@pytest.mark.parametrize(("method", "args", "path", "style", "fixed", "module", "tool", "tool_args"), SWEEP)
async def test_client_follows_every_page(
    method: str,
    args: tuple[Any, ...],
    path: str,
    style: str,
    fixed: dict[str, Any],
    module: str,
    tool: str,
    tool_args: tuple[Any, ...],
) -> None:
    c = _client()
    get, calls = _pages(*_three_pages(style))
    with patch.object(c, "get", get):
        result = await getattr(c, method)(*args)
    assert result == {"entries": _ALL, _NEXT[style][0]: None, "total": 4}
    _, size, cursor = _NEXT[style]
    assert [p for p, _ in calls] == [path, path, path]
    assert calls[0][1] == {**fixed, size: 200}
    assert calls[1][1] == {**fixed, size: 200, cursor: "p2"}
    assert calls[2][1] == {**fixed, size: 200, cursor: "p3"}
    # The other style's parameters are never sent.
    other = "token" if style == "page" else "page"
    assert all(_NEXT[other][1] not in params and _NEXT[other][2] not in params for _, params in calls)


@pytest.mark.parametrize(("method", "args", "path", "style", "fixed", "module", "tool", "tool_args"), SWEEP)
async def test_tool_returns_every_page(
    method: str,
    args: tuple[Any, ...],
    path: str,
    style: str,
    fixed: dict[str, Any],
    module: str,
    tool: str,
    tool_args: tuple[Any, ...],
) -> None:
    mod = importlib.import_module(f"sigma_mcp.tools.{module}")
    c = _client()
    get, calls = _pages(*_three_pages(style))
    with patch.object(c, "get", get), patch.object(mod, "get_client", AsyncMock(return_value=c)):
        out = json.loads(await getattr(mod, f"sigma_{tool}")(*tool_args))
    assert out == {"entries": _ALL, _NEXT[style][0]: None, "total": 4}
    assert [p for p, _ in calls] == [path, path, path]


@pytest.mark.parametrize(
    ("module", "tool", "method", "path"),
    [
        ("workbooks", "list_workbooks", "list_workbooks", "/v2/workbooks"),
        ("workbooks", "list_templates", "list_templates", "/v2/templates"),
        ("workbooks", "list_reports", "list_reports", "/v2/reports"),
        ("datasets", "list_data_models", "list_data_models", "/v2/dataModels"),
        ("admin", "list_members", "list_members", "/v2/members"),
        ("admin", "list_teams", "list_teams", "/v2/teams"),
    ],
)
async def test_limit_is_the_page_size(module: str, tool: str, method: str, path: str) -> None:
    c = _client()
    get, calls = _pages({"entries": [{"id": "a"}], "nextPage": "p2"}, {"entries": [{"id": "b"}]})
    with patch.object(c, "get", get):
        assert await getattr(c, method)(limit=25) == {"entries": [{"id": "a"}, {"id": "b"}], "nextPage": None}
    assert calls == [(path, {"limit": 25}), (path, {"limit": 25, "page": "p2"})]

    mod = importlib.import_module(f"sigma_mcp.tools.{module}")
    get, calls = _pages({"entries": [{"id": "a"}], "nextPage": "p2"}, {"entries": [{"id": "b"}]})
    with patch.object(c, "get", get), patch.object(mod, "get_client", AsyncMock(return_value=c)):
        out = json.loads(await getattr(mod, f"sigma_{tool}")(limit=25))
    assert out == {"entries": [{"id": "a"}, {"id": "b"}], "nextPage": None}
    assert [params for _, params in calls] == [{"limit": 25}, {"limit": 25, "page": "p2"}]


@pytest.mark.parametrize(
    ("module", "tool", "path", "field"),
    [
        ("workbooks", "list_workbooks", "/v2/workbooks", "workbookId"),
        ("datasets", "list_connections", "/v2/connections", "connectionId"),
        ("datasets", "list_data_models", "/v2/dataModels", "dataModelId"),
        ("admin", "list_members", "/v2/members", "memberId"),
        ("admin", "list_teams", "/v2/teams", "teamId"),
    ],
)
async def test_summary_counts_every_page(module: str, tool: str, path: str, field: str) -> None:
    mod = importlib.import_module(f"sigma_mcp.tools.{module}")
    c = _client()
    get, calls = _pages(
        {"entries": [{field: "1", "extra": "x"}], "nextPage": "p2", "total": 2},
        {"entries": [{field: "2", "extra": "y"}], "total": 2},
    )
    with patch.object(c, "get", get), patch.object(mod, "get_client", AsyncMock(return_value=c)):
        out = json.loads(await getattr(mod, f"sigma_{tool}")(summary_only=True))
    assert out == {"total": 2, "entries": [{field: "1"}, {field: "2"}], "nextPage": None}
    assert len(calls) == 2


async def test_list_files_keeps_its_filters_on_every_page() -> None:
    from sigma_mcp.tools import workspace

    c = _client()
    get, calls = _pages({"entries": [{"id": "a"}], "nextPage": "p2"}, {"entries": [{"id": "b"}]})
    with patch.object(c, "get", get), patch.object(workspace, "get_client", AsyncMock(return_value=c)):
        out = json.loads(await workspace.sigma_list_files(parent_id="f-1", type_filter="workbook"))
    assert out == {"entries": [{"id": "a"}, {"id": "b"}], "nextPage": None}
    base = {"limit": 200, "parentId": "f-1", "typeFilters": "workbook"}
    assert [params for _, params in calls] == [base, {**base, "page": "p2"}]


async def test_list_grants_keeps_inode_filter_on_every_page() -> None:
    from sigma_mcp.tools import admin

    c = _client()
    get, calls = _pages({"entries": [{"id": "a"}], "nextPage": "p2"}, {"entries": [{"id": "b"}]})
    with patch.object(c, "get", get), patch.object(admin, "get_client", AsyncMock(return_value=c)):
        out = json.loads(await admin.sigma_list_grants("i-1"))
    assert out == {"entries": [{"id": "a"}, {"id": "b"}], "nextPage": None}
    assert [params for _, params in calls] == [
        {"inodeId": "i-1", "limit": 200},
        {"inodeId": "i-1", "limit": 200, "page": "p2"},
    ]


async def test_promote_workbook_finds_a_tag_past_the_first_page() -> None:
    from sigma_mcp.tools import workbooks

    c = _client()
    get, calls = _pages(
        {"entries": [{"versionTagId": "t-1", "name": "Staging"}], "nextPage": "p2"},
        {"entries": [{"versionTagId": "t-2", "name": "Production"}]},
    )
    c.create_tag = AsyncMock()  # type: ignore[method-assign]
    c.tag_workbook = AsyncMock(return_value={"ok": True})  # type: ignore[method-assign]
    with patch.object(c, "get", get), patch.object(workbooks, "get_client", AsyncMock(return_value=c)):
        out = json.loads(await workbooks.sigma_promote_workbook("w-1", "production"))
    assert out == {"tag_id": "t-2", "tag_name": "production", "result": {"ok": True}}
    c.create_tag.assert_not_awaited()
    assert calls == [("/v2/tags", {"limit": 200}), ("/v2/tags", {"limit": 200, "page": "p2"})]


async def test_bulk_sync_reads_every_tenant_page() -> None:
    from sigma_mcp.tools import datasets

    c = _client()
    get, calls = _pages(
        {"entries": [{"orgId": "o-1", "name": "One"}], "nextPageToken": "t2"},
        {"entries": [{"orgId": "o-2", "name": "Two"}]},
    )
    with patch.object(c, "get", get), patch.object(datasets, "get_client", AsyncMock(return_value=c)):
        out = json.loads(await datasets.sigma_bulk_sync_tenant_connections(dry_run=True))
    assert out["tenants_found"] == 2
    assert calls == [("/v2/tenants", {"pageSize": 200}), ("/v2/tenants", {"pageSize": 200, "pageToken": "t2"})]


# Caller-driven methods: a cursor, when given, is where paging resumes; it still follows to the end.
CURSORED: list[tuple[str, str, str, dict[str, Any], dict[str, Any], int]] = [
    # (module, tool / method, path, extra args, fixed params, default page size)
    ("workspace", "list_tags", "/v2/tags", {}, {}, 200),
    ("workspace", "list_workspaces", "/v2/workspaces", {}, {}, 200),
    ("workspace", "list_workspace_grants", "/v2/workspaces/ws-1/grants", {"workspace_id": "ws-1"}, {}, 200),
    ("admin", "list_org_workbook_agents", "/v2/workbookAgents", {}, {}, 100),
    ("admin", "list_allowed_ips", "/v3alpha/allowedIps", {}, {}, 200),
]


def _style(path: str) -> str:
    return "token" if path in ("/v2/workbookAgents", "/v3alpha/allowedIps") else "page"


@pytest.mark.parametrize(("module", "name", "path", "extra", "fixed", "default_size"), CURSORED)
async def test_cursored_tool_follows_every_page(
    module: str, name: str, path: str, extra: dict[str, Any], fixed: dict[str, Any], default_size: int
) -> None:
    style = _style(path)
    _, size, cursor = _NEXT[style]
    mod = importlib.import_module(f"sigma_mcp.tools.{module}")
    c = _client()
    get, calls = _pages(*_three_pages(style))
    with patch.object(c, "get", get), patch.object(mod, "get_client", AsyncMock(return_value=c)):
        out = json.loads(await getattr(mod, f"sigma_{name}")(**extra))
    assert out == {"entries": _ALL, _NEXT[style][0]: None, "total": 4}
    assert [p for p, _ in calls] == [path, path, path]
    assert calls[0][1] == {size: default_size}
    assert calls[2][1] == {size: default_size, cursor: "p3"}


@pytest.mark.parametrize(("module", "name", "path", "extra", "fixed", "default_size"), CURSORED)
async def test_cursored_tool_resumes_from_the_given_cursor(
    module: str, name: str, path: str, extra: dict[str, Any], fixed: dict[str, Any], default_size: int
) -> None:
    style = _style(path)
    nxt, size, cursor = _NEXT[style]
    arg_cursor, arg_size = ("page", "limit") if style == "page" else ("page_token", "page_size")
    mod = importlib.import_module(f"sigma_mcp.tools.{module}")
    c = _client()
    get, calls = _pages({"entries": [{"id": "c"}], nxt: "p3"}, {"entries": [{"id": "d"}]})
    with patch.object(c, "get", get), patch.object(mod, "get_client", AsyncMock(return_value=c)):
        out = json.loads(await getattr(mod, f"sigma_{name}")(**extra, **{arg_cursor: "p2", arg_size: 25}))
    assert out == {"entries": [{"id": "c"}, {"id": "d"}], nxt: None}
    assert [params for _, params in calls] == [{size: 25, cursor: "p2"}, {size: 25, cursor: "p3"}]


@pytest.mark.parametrize(("search", "param"), [("a@example.com", "email")])
async def test_search_members_follows_every_page(search: str, param: str) -> None:
    c = _client()
    get, calls = _pages({"entries": [{"memberId": "m1"}], "nextPage": "p2"}, {"entries": [{"memberId": "m2"}]})
    with patch.object(c, "get", get):
        out = await c.search_members(search)
    assert out == {"entries": [{"memberId": "m1"}, {"memberId": "m2"}], "nextPage": None}
    assert calls == [
        ("/v2/members", {param: search, "limit": 120}),
        ("/v2/members", {param: search, "limit": 120, "page": "p2"}),
    ]


async def test_list_workbook_agents_stays_one_get() -> None:
    # The spec documents no pageToken request parameter for /v2/workbooks/{id}/agents.
    c = _client()
    get, calls = _pages({"entries": [{"agentId": "a"}], "nextPageToken": "t2"})
    with patch.object(c, "get", get):
        out = await c.list_workbook_agents("w-1", "Production")
    assert out == {"entries": [{"agentId": "a"}], "nextPageToken": "t2"}
    assert calls == [("/v2/workbooks/w-1/agents", {"versionTagName": "Production"})]
