"""Mock tests for swapConnections, swapSourceElements, teamAdmins, assignedUserAttributes.

OpenAPI: https://help.sigmacomputing.com/openapi/sigma-rest-api.json
operationIds: workbooks_swapWorkbookConnections, workbooks_swapWorkbookSourceElements,
dataModels_swapDataModelConnections, dataModels_swapDataModelSourceElements,
teams_updateTeamAdmins, teams_assignUserAttributesToTeam.
"""

from __future__ import annotations

import time

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import Response

from sigma_mcp.client import SigmaClient
from sigma_mcp.tools import admin as admin_tools
from sigma_mcp.tools import datasets as datasets_tools
from sigma_mcp.tools import elements as elements_tools


def _resp(payload: dict[str, Any] | None = None) -> Response:
    r = MagicMock(spec=Response)
    r.status_code = 200
    r.headers = {}
    r.text = "{}"
    r.json = MagicMock(return_value=payload or {"ok": True})
    return r


@pytest.fixture
def client() -> SigmaClient:
    c = SigmaClient("cid", "csecret", "https://api.sigmacomputing.com", max_retries=0)
    c._token = "tok"
    c._token_expiry = time.time() + 3600
    c._http = MagicMock()
    c._http.request = AsyncMock(return_value=_resp({"ok": True}))
    return c


@pytest.mark.asyncio
async def test_client_swap_workbook_connections_posts_documented_path(client: SigmaClient) -> None:
    body = {"connectionMapping": [{"fromId": "a", "toId": "b"}], "copyInputTableData": True}
    await client.swap_workbook_connections("wb1", body)
    args, kwargs = client._http.request.call_args
    assert args[0] == "POST"
    assert args[1] == "https://api.sigmacomputing.com/v2/workbooks/wb1/swapConnections"
    assert kwargs["json"] == body


@pytest.mark.asyncio
async def test_client_swap_workbook_source_elements_posts_documented_path(client: SigmaClient) -> None:
    body = {"sourceMapping": [{"from": {"inodeId": "1"}, "to": {"inodeId": "2"}}]}
    await client.swap_workbook_source_elements("wb1", body)
    args, kwargs = client._http.request.call_args
    assert args[0] == "POST"
    assert args[1] == "https://api.sigmacomputing.com/v2/workbooks/wb1/swapSourceElements"
    assert kwargs["json"] == body


@pytest.mark.asyncio
async def test_client_swap_data_model_connections_and_source_elements(client: SigmaClient) -> None:
    await client.swap_data_model_connections("dm1", {"connectionMapping": [{"fromId": "a", "toId": "b"}]})
    await client.swap_data_model_source_elements("dm1", {"sourceMapping": [{"from": {}, "to": {}}]})
    paths = [c.args[1] for c in client._http.request.call_args_list]
    assert paths == [
        "https://api.sigmacomputing.com/v2/dataModels/dm1/swapConnections",
        "https://api.sigmacomputing.com/v2/dataModels/dm1/swapSourceElements",
    ]
    assert client._http.request.call_args_list[0].args[0] == "POST"
    assert client._http.request.call_args_list[1].args[0] == "POST"


@pytest.mark.asyncio
async def test_client_team_admins_patch_and_assigned_attributes_post(client: SigmaClient) -> None:
    await client.update_team_admins("t1", {"add": ["m1"], "remove": ["m2"]})
    await client.assign_user_attributes_to_team(
        "t1",
        {"assignments": [{"userAttributeId": "ua1", "value": {"type": "string", "val": "US"}}]},
    )
    assert client._http.request.call_args_list[0].args[0] == "PATCH"
    assert client._http.request.call_args_list[0].args[1].endswith("/v2/teams/t1/teamAdmins")
    assert client._http.request.call_args_list[1].args[0] == "POST"
    assert client._http.request.call_args_list[1].args[1].endswith("/v2/teams/t1/assignedUserAttributes")


@pytest.mark.asyncio
async def test_tools_forward_bodies(monkeypatch: pytest.MonkeyPatch, client: SigmaClient) -> None:
    monkeypatch.setattr(elements_tools, "get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(datasets_tools, "get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(admin_tools, "get_client", AsyncMock(return_value=client))

    out = await elements_tools.sigma_swap_workbook_connections(
        "wb1", [{"fromId": "a", "toId": "b"}], copy_input_table_data=False
    )
    assert json.loads(out)["ok"] is True
    assert client._http.request.call_args.kwargs["json"] == {
        "connectionMapping": [{"fromId": "a", "toId": "b"}],
        "copyInputTableData": False,
    }

    await elements_tools.sigma_swap_workbook_source_elements("wb1", [{"from": {"x": 1}, "to": {"y": 2}}])
    assert client._http.request.call_args.kwargs["json"] == {"sourceMapping": [{"from": {"x": 1}, "to": {"y": 2}}]}

    await datasets_tools.sigma_swap_data_model_connections("dm1", [{"fromId": "a", "toId": "b"}])
    assert "/dataModels/dm1/swapConnections" in client._http.request.call_args.args[1]

    await datasets_tools.sigma_swap_data_model_source_elements("dm1", [{"from": {}, "to": {}}])
    assert "/dataModels/dm1/swapSourceElements" in client._http.request.call_args.args[1]

    await admin_tools.sigma_update_team_admins("t1", add=["m1"], remove=["m2"])
    assert client._http.request.call_args.kwargs["json"] == {"add": ["m1"], "remove": ["m2"]}
    assert client._http.request.call_args.args[0] == "PATCH"

    await admin_tools.sigma_assign_user_attributes_to_team(
        "t1", [{"userAttributeId": "ua1", "value": {"type": "string", "val": "US"}}]
    )
    assert client._http.request.call_args.kwargs["json"] == {
        "assignments": [{"userAttributeId": "ua1", "value": {"type": "string", "val": "US"}}]
    }
    assert client._http.request.call_args.args[0] == "POST"
