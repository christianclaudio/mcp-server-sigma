"""Unit tests covering workbook code representation, agents, query export download, org settings, and IP allowlist."""

from __future__ import annotations

import base64
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from sigma_mcp.client import SigmaClient
from sigma_mcp.tools import (
    add_allowed_ips,
    configure_org_ai,
    download_query_export,
    get_org_setting,
    list_allowed_ips,
    list_org_workbook_agents,
    list_workbook_agents,
    remove_allowed_ips,
    reset_org_email_branding,
    run_workbook_agent,
    sigma_add_allowed_ips,
    sigma_configure_org_ai,
    sigma_download_query_export,
    sigma_get_org_setting,
    sigma_list_allowed_ips,
    sigma_list_org_workbook_agents,
    sigma_list_workbook_agents,
    sigma_remove_allowed_ips,
    sigma_reset_org_email_branding,
    sigma_run_workbook_agent,
    sigma_update_org_setting,
    sigma_update_report_contents,
    sigma_update_workbook_contents,
    sigma_verify_report_spec,
    sigma_verify_workbook_spec,
    update_org_setting,
    update_report_contents,
    update_workbook_contents,
    verify_report_spec,
    verify_workbook_spec,
)


def _mock_res(
    status_code: int = 200,
    json_data: Any = None,
    text: str = "",
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.headers = headers or {}
    if json_data is not None:
        raw_text = json.dumps(json_data)
        r.json.return_value = json_data
        r.text = raw_text
        r.content = raw_text.encode()
    elif content is not None:
        r.content = content
        r.text = text or content.decode("utf-8", errors="replace")
        r.json.side_effect = json.JSONDecodeError("Invalid JSON", r.text, 0)
    else:
        r.content = text.encode()
        r.text = text
        r.json.side_effect = json.JSONDecodeError("Invalid JSON", text, 0)
    return r


@pytest.fixture
def mock_client():
    from sigma_mcp import server

    c = SigmaClient("cid", "csecret", "https://api.sigmacomputing.com")
    c._token = "valid-token"
    c._token_expiry = time.time() + 3600
    server._client = c
    yield c
    server._client = None


@pytest.mark.asyncio
async def test_client_methods_direct(mock_client: SigmaClient):
    client = mock_client
    http_mock = AsyncMock()
    client._http = http_mock

    # update_workbook_contents
    http_mock.request.return_value = _mock_res(200, {"updated": True})
    res = await client.update_workbook_contents("wb-1", {"contents": {"pages": []}})
    assert res == {"updated": True}
    assert http_mock.request.call_args[0][0] == "PUT"
    assert "/v2/workbooks/wb-1/contents" in http_mock.request.call_args[0][1]

    # verify_workbook_spec
    http_mock.request.return_value = _mock_res(200, {"valid": True})
    res = await client.verify_workbook_spec({"name": "Test", "folderId": "f-1", "document": {}})
    assert res == {"valid": True}
    assert http_mock.request.call_args[0][0] == "POST"
    assert "/v2/workbooks/spec/verify" in http_mock.request.call_args[0][1]

    # update_report_contents
    http_mock.request.return_value = _mock_res(200, {"updatedReport": True})
    res = await client.update_report_contents("rep-1", {"contents": {}})
    assert res == {"updatedReport": True}
    assert http_mock.request.call_args[0][0] == "PUT"
    assert "/v2/reports/rep-1/contents" in http_mock.request.call_args[0][1]

    # verify_report_spec
    http_mock.request.return_value = _mock_res(200, {"validReport": True})
    res = await client.verify_report_spec({"name": "Report", "folderId": "f-1", "document": {}})
    assert res == {"validReport": True}
    assert http_mock.request.call_args[0][0] == "POST"
    assert "/v2/reports/spec/verify" in http_mock.request.call_args[0][1]

    # download_query_export 204 processing
    http_mock.request.return_value = _mock_res(204)
    res = await client.download_query_export("q-123")
    assert res["status"] == "processing"
    assert res["queryId"] == "q-123"

    # download_query_export payload size exceeded
    http_mock.request.return_value = _mock_res(200, content=b"123456789012345")
    res_exceeded = await client.download_query_export("q-123", max_bytes=10)
    assert res_exceeded["status"] == "ready"
    assert "Export size exceeds maximum allowed bytes" in res_exceeded["error"]

    # download_query_export JSON
    http_mock.request.return_value = _mock_res(200, {"rows": [1, 2, 3]}, headers={"content-type": "application/json"})
    res = await client.download_query_export("q-123")
    assert res["status"] == "ready"
    assert res["data"] == {"rows": [1, 2, 3]}

    # download_query_export JSON invalid json fallback
    http_mock.request.return_value = _mock_res(200, content=b"not json", headers={"content-type": "application/json"})
    res = await client.download_query_export("q-123")
    assert res["status"] == "ready"
    assert "dataBase64" in res

    # download_query_export text/csv
    http_mock.request.return_value = _mock_res(200, text="a,b\n1,2", headers={"content-type": "text/csv"})
    res = await client.download_query_export("q-123")
    assert res["status"] == "ready"
    assert res["content"] == "a,b\n1,2"

    # download_query_export binary pdf
    pdf_bytes = b"%PDF-1.4 test binary data"
    http_mock.request.return_value = _mock_res(200, content=pdf_bytes, headers={"content-type": "application/pdf"})
    res = await client.download_query_export("q-123")
    assert res["status"] == "ready"
    assert res["sizeBytes"] == len(pdf_bytes)
    assert res["dataBase64"] == base64.b64encode(pdf_bytes).decode("ascii")

    # list_workbook_agents
    http_mock.request.return_value = _mock_res(200, {"agents": []})
    res = await client.list_workbook_agents("wb-1")
    assert res == {"agents": []}
    assert "/v2/workbooks/wb-1/agents" in http_mock.request.call_args[0][1]

    # list_workbook_agents with version_tag_name
    http_mock.request.return_value = _mock_res(200, {"agents": ["tagged"]})
    res = await client.list_workbook_agents("wb-1", version_tag_name="Production")
    assert res == {"agents": ["tagged"]}
    assert http_mock.request.call_args[1]["params"] == {"versionTagName": "Production"}

    # run_workbook_agent
    http_mock.request.return_value = _mock_res(200, {"agentResult": "ok"})
    res = await client.run_workbook_agent("wb-1", "ag-1", {"messages": []})
    assert res == {"agentResult": "ok"}
    assert "/v2/workbooks/wb-1/agents/ag-1" in http_mock.request.call_args[0][1]

    # list_org_workbook_agents
    http_mock.request.return_value = _mock_res(200, {"entries": []})
    res = await client.list_org_workbook_agents(page_token="tok1", page_size=25)
    assert res == {"entries": []}
    assert "/v2/workbookAgents" in http_mock.request.call_args[0][1]
    assert http_mock.request.call_args[1]["params"] == {"pageToken": "tok1", "pageSize": 25}

    # configure_org_ai
    http_mock.request.return_value = _mock_res(200, {"provider": "openAI"})
    res = await client.configure_org_ai({"provider": "openAI", "apiKey": "sk-test"})
    assert res == {"provider": "openAI"}
    assert "/v2/organizations/settings/aiConfigs" in http_mock.request.call_args[0][1]

    # get_org_setting
    http_mock.request.return_value = _mock_res(200, {"enabled": True})
    res = await client.get_org_setting("auditLogging")
    assert res == {"enabled": True}
    assert "/v2/organizations/settings/auditLogging" in http_mock.request.call_args[0][1]

    # update_org_setting
    http_mock.request.return_value = _mock_res(200, {"auditLogging": "updated"})
    res = await client.update_org_setting("auditLogging", {"enabled": False})
    assert res == {"auditLogging": "updated"}
    assert "/v2/organizations/settings/auditLogging" in http_mock.request.call_args[0][1]

    # reset_org_email_branding
    http_mock.request.return_value = _mock_res(204)
    status = await client.reset_org_email_branding()
    assert status == 204
    assert "/v2/organizations/settings/emailBranding" in http_mock.request.call_args[0][1]

    # list_allowed_ips
    http_mock.request.return_value = _mock_res(200, {"entries": []})
    res = await client.list_allowed_ips(page_token="ip-tok", page_size=10)
    assert res == {"entries": []}
    assert "/v3alpha/allowedIps" in http_mock.request.call_args[0][1]
    assert http_mock.request.call_args[1]["params"] == {"pageToken": "ip-tok", "pageSize": 10}

    # batch_create_allowed_ips
    http_mock.request.return_value = _mock_res(200, {"created": 1})
    res = await client.batch_create_allowed_ips([{"ip": "1.2.3.4", "scope": "both"}])
    assert res == {"created": 1}
    assert "/v3alpha/allowedIps:batchCreate" in http_mock.request.call_args[0][1]

    # batch_delete_allowed_ips
    http_mock.request.return_value = _mock_res(200, {"deleted": 1})
    res = await client.batch_delete_allowed_ips(["entry-1"])
    assert res == {"deleted": 1}
    assert "/v3alpha/allowedIps:batchDelete" in http_mock.request.call_args[0][1]


@pytest.mark.asyncio
async def test_workbooks_tools(mock_client: SigmaClient):
    client = mock_client
    http_mock = AsyncMock()
    client._http = http_mock

    # sigma_update_workbook_contents
    assert "confirm=True" in await sigma_update_workbook_contents("wb-1", {}, document_version=1, confirm=False)
    assert "workbook_id is required" in await sigma_update_workbook_contents("", {}, document_version=1, confirm=True)
    assert "positive integer" in await sigma_update_workbook_contents("wb-1", {}, document_version=0, confirm=True)
    http_mock.request.return_value = _mock_res(200, {"status": "ok"})
    out = await sigma_update_workbook_contents("wb-1", {"k": "v"}, document_version=2, confirm=True)
    assert json.loads(out) == {"status": "ok"}
    assert http_mock.request.call_args[1]["json"] == {"contents": {"k": "v"}, "documentVersion": 2}

    # sigma_verify_workbook_spec
    assert "name is required" in await sigma_verify_workbook_spec("", "f-1", {})
    assert "folder_id is required" in await sigma_verify_workbook_spec("Name", "", {})
    http_mock.request.return_value = _mock_res(200, {"specValid": True})
    out = await sigma_verify_workbook_spec("Name", "f-1", {"doc": 1}, description="test desc")
    assert json.loads(out) == {"specValid": True}
    assert http_mock.request.call_args[1]["json"] == {
        "name": "Name",
        "folderId": "f-1",
        "document": {"doc": 1},
        "description": "test desc",
    }

    # sigma_update_report_contents
    assert "confirm=True" in await sigma_update_report_contents("rep-1", {}, document_version=1, confirm=False)
    assert "report_id is required" in await sigma_update_report_contents("", {}, document_version=1, confirm=True)
    assert "positive integer" in await sigma_update_report_contents("rep-1", {}, document_version=-1, confirm=True)
    http_mock.request.return_value = _mock_res(200, {"reportUpdated": True})
    out = await sigma_update_report_contents("rep-1", {"pages": []}, document_version=5, confirm=True)
    assert json.loads(out) == {"reportUpdated": True}
    assert http_mock.request.call_args[1]["json"] == {"contents": {"pages": []}, "documentVersion": 5}

    # sigma_verify_report_spec
    assert "name is required" in await sigma_verify_report_spec("", "f-1", {})
    assert "folder_id is required" in await sigma_verify_report_spec("Rep", "", {})
    http_mock.request.return_value = _mock_res(200, {"repValid": True})
    out = await sigma_verify_report_spec("Rep", "f-1", {}, description="report description")
    assert json.loads(out) == {"repValid": True}
    assert http_mock.request.call_args[1]["json"]["description"] == "report description"

    # sigma_download_query_export
    assert "query_id is required" in await sigma_download_query_export("")
    assert "positive integer" in await sigma_download_query_export("q-456", max_bytes=-1)
    assert "positive integer" in await sigma_download_query_export("q-456", max_bytes=0)
    http_mock.request.return_value = _mock_res(200, text="col1,col2\nval1,val2", headers={"content-type": "text/csv"})
    out = await sigma_download_query_export("q-456")
    parsed = json.loads(out)
    assert parsed["status"] == "ready"
    assert parsed["content"] == "col1,col2\nval1,val2"

    out_oversized = await sigma_download_query_export("q-456", max_bytes=5)
    parsed_oversized = json.loads(out_oversized)
    assert parsed_oversized["status"] == "ready"
    assert "Export size exceeds maximum allowed bytes" in parsed_oversized["error"]

    # sigma_list_workbook_agents
    assert "workbook_id is required" in await sigma_list_workbook_agents("")
    http_mock.request.return_value = _mock_res(200, {"entries": [{"agentId": "ag-1"}]})
    out = await sigma_list_workbook_agents("wb-123", version_tag_name="v1")
    assert json.loads(out) == {"entries": [{"agentId": "ag-1"}]}

    # sigma_run_workbook_agent
    assert "confirm=True" in await sigma_run_workbook_agent("wb-1", "ag-1", [], confirm=False)
    assert "workbook_id is required" in await sigma_run_workbook_agent(
        "", "ag-1", [{"role": "user", "content": "hi"}], confirm=True
    )
    assert "agent_id is required" in await sigma_run_workbook_agent(
        "wb-1", "", [{"role": "user", "content": "hi"}], confirm=True
    )
    assert "cannot be empty" in await sigma_run_workbook_agent("wb-1", "ag-1", [], confirm=True)

    http_mock.request.return_value = _mock_res(200, {"reply": "Hello!"})
    out = await sigma_run_workbook_agent(
        "wb-1",
        "ag-1",
        [{"role": "user", "content": "hi"}],
        version_tag_name="v2",
        max_turns=10,
        max_output_tokens=500,
        response_format={"type": "text"},
        metadata={"user": "tester"},
        confirm=True,
    )
    assert json.loads(out) == {"reply": "Hello!"}
    call_body = http_mock.request.call_args[1]["json"]
    assert call_body["sigma:versionTagName"] == "v2"
    assert call_body["maxTurns"] == 10
    assert call_body["maxOutputTokens"] == 500
    assert call_body["responseFormat"] == {"type": "text"}
    assert call_body["metadata"] == {"user": "tester"}


@pytest.mark.asyncio
async def test_admin_tools(mock_client: SigmaClient):
    client = mock_client
    http_mock = AsyncMock()
    client._http = http_mock

    # sigma_list_org_workbook_agents
    http_mock.request.return_value = _mock_res(200, {"entries": []})
    out = await sigma_list_org_workbook_agents(page_token="tok", page_size=50)
    assert json.loads(out) == {"entries": []}

    # sigma_get_org_setting
    assert "setting_name is required" in await sigma_get_org_setting("")
    assert "Invalid setting_name" in await sigma_get_org_setting("invalidSetting")
    http_mock.request.return_value = _mock_res(200, {"timezone": "UTC"})
    out = await sigma_get_org_setting("timezone")
    assert json.loads(out) == {"timezone": "UTC"}

    # sigma_update_org_setting
    assert "confirm=True" in await sigma_update_org_setting("timezone", {}, confirm=False)
    assert "setting_name is required" in await sigma_update_org_setting("", {}, confirm=True)
    assert "Invalid setting_name" in await sigma_update_org_setting("notReal", {}, confirm=True)
    http_mock.request.return_value = _mock_res(200, {"timezone": "America/New_York"})
    out = await sigma_update_org_setting("timezone", {"timezone": "America/New_York"}, confirm=True)
    assert json.loads(out) == {"timezone": "America/New_York"}

    # sigma_configure_org_ai
    assert "confirm=True" in await sigma_configure_org_ai({}, confirm=False)
    assert "provider_config is required" in await sigma_configure_org_ai({}, confirm=True)
    http_mock.request.return_value = _mock_res(200, {"configured": True})
    out = await sigma_configure_org_ai({"provider": "anthropic", "apiKey": "sk-ant"}, confirm=True)
    assert json.loads(out) == {"configured": True}

    # sigma_reset_org_email_branding
    assert "confirm=True" in await sigma_reset_org_email_branding(confirm=False)
    http_mock.request.return_value = _mock_res(204)
    out = await sigma_reset_org_email_branding(confirm=True)
    assert json.loads(out)["status"] == "reset"

    # sigma_list_allowed_ips
    http_mock.request.return_value = _mock_res(200, {"entries": [{"ip": "10.0.0.1"}]})
    out = await sigma_list_allowed_ips()
    assert json.loads(out) == {"entries": [{"ip": "10.0.0.1"}]}

    # sigma_add_allowed_ips
    assert "confirm=True" in await sigma_add_allowed_ips([], confirm=False)
    assert "cannot be empty" in await sigma_add_allowed_ips([], confirm=True)
    http_mock.request.return_value = _mock_res(200, {"createdCount": 1})
    out = await sigma_add_allowed_ips([{"ip": "192.168.1.1/32", "scope": "public-api"}], confirm=True)
    assert json.loads(out) == {"createdCount": 1}

    # sigma_remove_allowed_ips
    assert "confirm=True" in await sigma_remove_allowed_ips([], confirm=False)
    assert "cannot be empty" in await sigma_remove_allowed_ips([], confirm=True)
    http_mock.request.return_value = _mock_res(200, {"deletedCount": 2})
    out = await sigma_remove_allowed_ips(["id-1", "id-2"], confirm=True)
    assert json.loads(out) == {"deletedCount": 2}


def test_aliases_equality():
    assert update_workbook_contents == sigma_update_workbook_contents
    assert verify_workbook_spec == sigma_verify_workbook_spec
    assert update_report_contents == sigma_update_report_contents
    assert verify_report_spec == sigma_verify_report_spec
    assert download_query_export == sigma_download_query_export
    assert list_workbook_agents == sigma_list_workbook_agents
    assert run_workbook_agent == sigma_run_workbook_agent
    assert list_org_workbook_agents == sigma_list_org_workbook_agents
    assert get_org_setting == sigma_get_org_setting
    assert update_org_setting == sigma_update_org_setting
    assert configure_org_ai == sigma_configure_org_ai
    assert reset_org_email_branding == sigma_reset_org_email_branding
    assert list_allowed_ips == sigma_list_allowed_ips
    assert add_allowed_ips == sigma_add_allowed_ips
    assert remove_allowed_ips == sigma_remove_allowed_ips
