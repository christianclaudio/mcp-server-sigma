"""Badge tools and Git repository tagging (#103), checked on the wire.

With a real ``SigmaClient`` over a mocked transport, each tool sends the HTTP method, path and JSON
body of its operation in the Sigma spec: setWorkbookBadge, setReportBadge and setDataModelBadge
(PUT .../badge) and tagGitRepository (POST /v2/gitIntegration/tagRepository).
"""

from __future__ import annotations

import importlib
import json
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastmcp.exceptions import ToolError

from sigma_mcp import server
from sigma_mcp.client import SigmaClient
from sigma_mcp.server import DOMAIN_SERVERS

BASE = "https://api.sigmacomputing.com"

# (domain, tool, ID argument, path segment)
BADGE_CASES = [
    ("workbooks", "set_workbook_badge", "workbook_id", "workbooks"),
    ("workbooks", "set_report_badge", "report_id", "reports"),
    ("datasets", "set_data_model_badge", "data_model_id", "dataModels"),
]


def _tool(domain: str, name: str) -> Any:
    return getattr(importlib.import_module(f"sigma_mcp.tools.{domain}"), f"sigma_{name}")


@pytest.fixture
def http() -> Iterator[AsyncMock]:
    c = SigmaClient("cid", "csecret", BASE, max_retries=0)
    c._token = "tok"
    c._token_expiry = time.time() + 3600
    mock = AsyncMock()
    c._http = mock
    server._client = c
    yield mock
    server._client = None


@pytest.mark.parametrize(("domain", "name", "id_arg", "segment"), BADGE_CASES)
async def test_badge_tools_call_put(http: AsyncMock, domain: str, name: str, id_arg: str, segment: str) -> None:
    http.request.return_value = httpx.Response(200, json={"badge": "Endorsed", "badgeNote": "n"})
    out = json.loads(await _tool(domain, name)(**{id_arg: "x-1", "badge": "Endorsed", "badge_note": "n"}))
    args, kw = http.request.call_args
    assert args[0] == "PUT"
    assert args[1] == f"{BASE}/v2/{segment}/x-1/badge"
    assert kw["json"] == {"badge": "Endorsed", "badgeNote": "n"}
    assert out == {"badge": "Endorsed", "badgeNote": "n"}


@pytest.mark.parametrize(("domain", "name", "id_arg", "segment"), BADGE_CASES)
async def test_badge_note_omitted_is_not_sent(
    http: AsyncMock, domain: str, name: str, id_arg: str, segment: str
) -> None:
    http.request.return_value = httpx.Response(200, json={"badge": "Warning", "badgeNote": None})
    await _tool(domain, name)(**{id_arg: "x-1", "badge": "Warning"})
    assert http.request.call_args[1]["json"] == {"badge": "Warning"}


@pytest.mark.parametrize(("domain", "name", "id_arg", "segment"), BADGE_CASES)
async def test_badge_blank_id_rejected(http: AsyncMock, domain: str, name: str, id_arg: str, segment: str) -> None:
    with pytest.raises(ToolError, match=f"{id_arg} is required"):
        await _tool(domain, name)(**{id_arg: "  ", "badge": "Endorsed"})
    http.request.assert_not_called()


async def test_tag_git_repository_needs_confirm(http: AsyncMock) -> None:
    out = json.loads(await _tool("datasets", "tag_git_repository")("acme/models", "v1.2.0", "Production"))
    assert out["status"] == "confirmation_required"
    assert out["executed"] is False
    http.request.assert_not_called()


async def test_tag_git_repository_posts_body(http: AsyncMock) -> None:
    http.request.return_value = httpx.Response(200, json={"success": True})
    tool = _tool("datasets", "tag_git_repository")
    out = json.loads(await tool("acme/models", "abc123", "Production", confirm=True))
    args, kw = http.request.call_args
    assert args[0] == "POST"
    assert args[1] == f"{BASE}/v2/gitIntegration/tagRepository"
    assert kw["json"] == {"repositoryPath": "acme/models", "ref": "abc123", "tagName": "Production"}
    assert out == {"success": True}

    mapping = [{"fromConnectionId": "c-1", "toConnectionId": "c-2", "paths": [{"fromPath": ["A"], "toPath": ["B"]}]}]
    versions = [{"dataModelId": "dm-1", "fromVersionTagId": None, "toVersionTagId": "t-2"}]
    await tool(
        "acme/models",
        "v1.2.0",
        "Production",
        source_mapping_config=mapping,
        data_model_source_tagged_versions=versions,
        is_default=False,
        confirm=True,
    )
    assert http.request.call_args[1]["json"] == {
        "repositoryPath": "acme/models",
        "ref": "v1.2.0",
        "tagName": "Production",
        "sourceMappingConfig": mapping,
        "dataModelSourceTaggedVersions": versions,
        "isDefault": False,
    }


@pytest.mark.parametrize("field", ["repository_path", "ref", "tag_name"])
async def test_tag_git_repository_blank_fields_rejected(http: AsyncMock, field: str) -> None:
    kwargs: dict[str, Any] = {"repository_path": "acme/models", "ref": "abc123", "tag_name": "Production"}
    kwargs[field] = " "
    with pytest.raises(ToolError, match=f"{field} is required"):
        await _tool("datasets", "tag_git_repository")(**kwargs, confirm=True)
    http.request.assert_not_called()


async def test_new_tool_annotations() -> None:
    tools = {}
    for domain, sub in DOMAIN_SERVERS:
        for tool in await sub.list_tools():
            tools[f"{domain}_{tool.name}"] = tool
    for name in (
        "workbooks_set_workbook_badge",
        "workbooks_set_report_badge",
        "datasets_set_data_model_badge",
        "datasets_tag_git_repository",
    ):
        a = tools[name].annotations
        assert a is not None
        assert a.read_only_hint is False
        assert a.destructive_hint is False
        assert a.idempotent_hint is True
        assert a.open_world_hint is True
