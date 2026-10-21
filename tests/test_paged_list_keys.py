"""Paged list results keep the top-level keys each method returned before it read every page.

Before the lists followed Sigma's pagination, each of these methods sent one GET and returned
Sigma's page as sent: ``entries`` plus, on ``limit``/``page`` endpoints, ``nextPage`` and
``total`` (and ``hasMore`` where the spec has it), or ``nextPageToken`` on ``pageSize``/
``pageToken`` endpoints. Reading every page keeps that key set: ``entries`` holds every page's
entries, ``total`` is the first page's value and is never computed, and the cursor keys are
null (``hasMore`` false), since there is no further page. The five caller-driven tools return
their cursor key as null too.
"""

from __future__ import annotations

import importlib
import json
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from sigma_mcp.client import SigmaClient

# The key sets of Sigma's page by paging style: the shape each method returned before.
OLD_LIMIT_PAGE_KEYS = {"entries", "nextPage", "total", "hasMore"}
OLD_LIMIT_PAGE_KEYS_NO_TOTAL = {"entries", "nextPage"}
OLD_TOKEN_KEYS = {"entries", "nextPageToken"}

# (client method, positional args) on limit/page endpoints: 36 plus search_members.
LIMIT_PAGE: list[tuple[str, tuple[Any, ...]]] = [
    ("list_connections", ()),
    ("list_connection_grants", ("c-1",)),
    ("list_workbooks", ()),
    ("list_workbook_pages", ("w-1",)),
    ("list_workbook_elements", ("w-1",)),
    ("list_workbook_columns", ("w-1",)),
    ("list_workbook_queries", ("w-1",)),
    ("list_workbook_controls", ("w-1",)),
    ("get_workbook_version_history", ("w-1",)),
    ("list_workbook_lineage", ("w-1",)),
    ("list_workbook_grants", ("w-1",)),
    ("list_workbook_embeds", ("w-1",)),
    ("list_workbook_bookmarks", ("w-1",)),
    ("get_workbook_tags", ("w-1",)),
    ("list_workbook_page_elements", ("w-1", "p-1")),
    ("get_element_columns", ("w-1", "e-1")),
    ("list_templates", ()),
    ("list_data_models", ()),
    ("list_data_model_elements", ("d-1",)),
    ("list_data_model_columns", ("d-1",)),
    ("list_data_model_lineage", ("d-1",)),
    ("list_members", ()),
    ("list_member_teams", ("m-1",)),
    ("list_teams", ()),
    ("list_team_members", ("t-1",)),
    ("list_files", ({"limit": 200},)),
    ("list_user_attributes", ()),
    ("get_user_attribute_user_assignments", ("ua-1",)),
    ("get_user_attribute_team_assignments", ("ua-1",)),
    ("get_user_attribute_tenant_assignments", ("ua-1",)),
    ("list_grants", ({"inodeId": "i-1"},)),
    ("list_reports", ()),
    ("list_report_schedules", ("r-1",)),
    ("list_source_swap_policies", ()),
    ("list_shared_templates", ()),
    ("list_translations", ()),
    ("search_members", ("ada@example.com",)),
]

TOKEN: list[tuple[str, tuple[Any, ...]]] = [
    ("list_columns_for_table", ("tb-1",)),
    ("list_account_types", ()),
    ("list_report_elements", ("r-1",)),
    ("list_report_queries", ("r-1",)),
    ("list_report_lineage", ("r-1",)),
    ("list_deployments", ()),
    ("list_deployment_documents", ("dp-1",)),
    ("list_tenants", ()),
    ("list_api_connectors", ()),
]

# The two schedule lists (on main a single GET) and the 20 lists that gained a tool in this
# PR read the same way, so they return the same keys.
LIMIT_PAGE_MORE: list[tuple[str, tuple[Any, ...]]] = [
    ("list_workbook_schedules", ("w-1",)),
    ("list_materialization_schedules", ("w-1",)),
    ("list_member_schedules", ("m-1",)),
    ("list_connection_paths", ()),
    ("list_connection_path_grants", ("cp-1",)),
    ("list_data_model_tags", ("dm-1",)),
    ("list_member_files", ("m-1",)),
    ("list_recent_files", ("m-1",)),
    ("list_workbooks_for_tag", ("t-1",)),
    ("list_favorites_for_member", ("m-1",)),
]

TOKEN_MORE: list[tuple[str, tuple[Any, ...]]] = [
    ("list_deployable_tenants", ()),
    ("list_deployment_tenants", ("dp-1",)),
    ("list_tenant_deployment_capabilities", ("org-1",)),
    ("list_api_credentials", ()),
    ("list_data_model_sources", ("dm-1",)),
    ("list_data_model_materialization_schedules", ("dm-1",)),
    ("list_report_pages", ("r-1",)),
    ("list_report_page_elements", ("r-1", "p-1")),
    ("list_report_columns", ("r-1",)),
    ("list_report_controls", ("r-1",)),
    ("get_report_version_history", ("r-1",)),
    ("list_report_tags", ("r-1",)),
]

# (tool module, tool and client method, kwargs, cursor key)
CALLER_DRIVEN: list[tuple[str, str, dict[str, Any], str]] = [
    ("workspace", "list_tags", {"page": "p1"}, "nextPage"),
    ("workspace", "list_workspaces", {"page": "p1"}, "nextPage"),
    ("workspace", "list_workspace_grants", {"workspace_id": "ws-1", "page": "p1"}, "nextPage"),
    ("admin", "list_org_workbook_agents", {"page_token": "p1"}, "nextPageToken"),
    ("admin", "list_allowed_ips", {"page_token": "p1"}, "nextPageToken"),
]


def _client() -> SigmaClient:
    c = SigmaClient("id", "secret", "https://api.sigmacomputing.com", max_retries=0, base_delay=0.001)
    c._token = "tok"
    c._token_expiry = time.time() + 3600
    return c


def _get(*pages: Any) -> AsyncMock:
    remaining = list(pages)

    async def fake_get(path: str, params: dict[str, Any] | None = None) -> Any:
        return remaining.pop(0)

    return AsyncMock(side_effect=fake_get)


def test_tables_cover_every_paged_method_once() -> None:
    names = [m for m, _ in LIMIT_PAGE] + [m for m, _ in TOKEN] + [m for _, m, _, _ in CALLER_DRIVEN]
    assert (len(LIMIT_PAGE), len(TOKEN), len(CALLER_DRIVEN)) == (37, 9, 5)
    assert len(set(names)) == 51
    more = [m for m, _ in LIMIT_PAGE_MORE] + [m for m, _ in TOKEN_MORE]
    assert len(set(more)) == 22
    assert not set(more) & set(names)


@pytest.mark.parametrize(("method", "args"), LIMIT_PAGE + LIMIT_PAGE_MORE)
async def test_limit_page_keeps_the_old_keys(method: str, args: tuple[Any, ...]) -> None:
    first = {"entries": [{"id": "a"}], "nextPage": "p2", "total": 2, "hasMore": True}
    c = _client()
    with patch.object(
        c, "get", _get(first, {"entries": [{"id": "b"}], "nextPage": None, "total": 2, "hasMore": False})
    ):
        result = await getattr(c, method)(*args)
    assert set(result) == set(first) == OLD_LIMIT_PAGE_KEYS
    assert result == {"entries": [{"id": "a"}, {"id": "b"}], "nextPage": None, "total": 2, "hasMore": False}


@pytest.mark.parametrize(("method", "args"), LIMIT_PAGE + LIMIT_PAGE_MORE)
async def test_limit_page_without_total_has_no_total(method: str, args: tuple[Any, ...]) -> None:
    first = {"entries": [{"id": "a"}], "nextPage": "p2"}
    c = _client()
    with patch.object(c, "get", _get(first, {"entries": [{"id": "b"}], "nextPage": None})):
        result = await getattr(c, method)(*args)
    assert set(result) == set(first) == OLD_LIMIT_PAGE_KEYS_NO_TOTAL
    assert result["nextPage"] is None


@pytest.mark.parametrize(("method", "args"), TOKEN + TOKEN_MORE)
async def test_token_keeps_the_old_keys(method: str, args: tuple[Any, ...]) -> None:
    first = {"entries": [{"id": "a"}], "nextPageToken": "t2"}
    c = _client()
    with patch.object(c, "get", _get(first, {"entries": [{"id": "b"}]})):
        result = await getattr(c, method)(*args)
    assert set(result) == set(first) == OLD_TOKEN_KEYS
    assert result == {"entries": [{"id": "a"}, {"id": "b"}], "nextPageToken": None}


@pytest.mark.parametrize(("module", "name", "kwargs", "cursor"), CALLER_DRIVEN)
async def test_caller_driven_tool_returns_its_cursor_as_null(
    module: str, name: str, kwargs: dict[str, Any], cursor: str
) -> None:
    first = {"entries": [{"id": "a"}], cursor: "p2"}
    if cursor == "nextPage":
        first["total"] = 2
    mod = importlib.import_module(f"sigma_mcp.tools.{module}")
    c = _client()
    with patch.object(c, "get", _get(first, {"entries": [{"id": "b"}]})):
        with patch.object(mod, "get_client", AsyncMock(return_value=c)):
            out = json.loads(await getattr(mod, f"sigma_{name}")(**kwargs))
    assert set(out) == set(first)
    assert out[cursor] is None
    assert out["entries"] == [{"id": "a"}, {"id": "b"}]
    if cursor == "nextPage":
        assert out["total"] == 2


@pytest.mark.parametrize(("method", "cursor"), [("list_workbooks", "nextPage"), ("list_tenants", "nextPageToken")])
async def test_total_is_the_first_pages_value(method: str, cursor: str) -> None:
    c = _client()
    pages = ({"entries": [{"id": "a"}], cursor: "p2", "total": 7}, {"entries": [{"id": "b"}], "total": 9})
    with patch.object(c, "get", _get(*pages)):
        result = await getattr(c, method)()
    assert result["total"] == 7


@pytest.mark.parametrize(("method", "cursor"), [("list_workbooks", "nextPage"), ("list_tenants", "nextPageToken")])
async def test_total_absent_stays_absent(method: str, cursor: str) -> None:
    c = _client()
    pages = ({"entries": [{"id": "a"}], cursor: "p2"}, {"entries": [{"id": "b"}], "total": 9})
    with patch.object(c, "get", _get(*pages)):
        result = await getattr(c, method)()
    assert "total" not in result
    assert "hasMore" not in result


@pytest.mark.parametrize(
    ("module", "tool", "first", "keys"),
    [
        (
            "workbooks",
            "list_workbooks",
            {"entries": [], "nextPage": None, "total": 0, "hasMore": False},
            OLD_LIMIT_PAGE_KEYS,
        ),
        ("admin", "list_tenants", {"entries": [], "nextPageToken": None}, OLD_TOKEN_KEYS),
    ],
)
async def test_single_page_tool_output_has_the_old_keys(
    module: str, tool: str, first: dict[str, Any], keys: set[str]
) -> None:
    mod = importlib.import_module(f"sigma_mcp.tools.{module}")
    c = _client()
    with patch.object(c, "get", _get(first)), patch.object(mod, "get_client", AsyncMock(return_value=c)):
        out = json.loads(await getattr(mod, f"sigma_{tool}")())
    assert out == first
    assert set(out) == keys


async def test_cursor_key_is_null_when_sigma_omits_it_on_the_only_page() -> None:
    c = _client()
    with patch.object(c, "get", _get({"entries": [{"id": "a"}]})):
        assert await c.list_tenants() == {"entries": [{"id": "a"}], "nextPageToken": None}


@pytest.mark.parametrize("first", [[{"id": "a"}, {"id": "b"}], None])
async def test_a_page_that_is_not_an_object_is_returned_as_sent(first: Any) -> None:
    c = _client()
    with patch.object(c, "get", _get(first)):
        assert await c.list_members() == first
