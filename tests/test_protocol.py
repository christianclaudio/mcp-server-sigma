"""Protocol integration tests for MCPServer JSON-RPC initialization and tool discovery."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from mcp.types import CallToolResult

from sigma_mcp.server import main, mcp


def test_stdio_initialize_handshake() -> None:
    """Verify end-to-end JSON-RPC initialization handshake over stdio."""
    repo_dir = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(repo_dir / "src")}

    proc = subprocess.Popen(
        [sys.executable, "-m", "sigma_mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    init_payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest-protocol-client", "version": "1.0.0"},
        },
    }

    try:
        stdout_data, stderr_data = proc.communicate(input=json.dumps(init_payload) + "\n", timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        pytest.fail("Stdio initialization handshake timed out.")

    lines = [line.strip() for line in stdout_data.split("\n") if line.strip()]
    assert lines, f"No stdio response received from server. Stderr: {stderr_data}"

    response = json.loads(lines[0])
    assert "result" in response, f"Invalid handshake response: {response}"
    assert "protocolVersion" in response["result"]
    assert response["result"]["serverInfo"]["name"] == "mcp-server-sigma"


@pytest.mark.asyncio
async def test_dynamic_tools_listing() -> None:
    """Verify MCPServer dynamically advertises tools with valid schemas and annotations."""
    tools = await mcp.list_tools()
    assert len(tools) >= 155

    for tool in tools:
        assert tool.name
        assert tool.description
        assert tool.input_schema is not None
        assert tool.annotations is not None
        assert hasattr(tool.annotations, "read_only_hint")
        assert hasattr(tool.annotations, "destructive_hint")
        assert hasattr(tool.annotations, "idempotent_hint")
        assert hasattr(tool.annotations, "open_world_hint")


@pytest.mark.asyncio
async def test_dynamic_tool_call_dispatch() -> None:
    """Verify offline tool dispatch via MCPServer.call_tool."""
    res = await mcp.call_tool("sigma_api_capabilities", {})
    assert res is not None
    assert isinstance(res, CallToolResult)
    assert len(res.content) > 0
    assert not res.is_error

    parsed = json.loads(res.content[0].text)
    assert "supported" in parsed
    assert "not_supported" in parsed
    assert "data_models_as_code" in parsed["supported"]


@pytest.mark.asyncio
async def test_dynamic_resources_and_prompts() -> None:
    """Verify native resources and prompts discovery on MCPServer."""
    resources = await mcp.list_resources()
    assert any(str(r.uri) == "sigma://reference/formulas" for r in resources)
    assert any(str(r.uri) == "sigma://reference/capabilities" for r in resources)
    assert any(str(r.uri) == "sigma://reference/docs-index" for r in resources)

    prompts = await mcp.list_prompts()
    assert any(p.name == "audit_tenant_connections" for p in prompts)


@pytest.mark.asyncio
async def test_stateless_streamable_http_standalone_post() -> None:
    """Verify Streamable HTTP in stateless mode accepts standalone requests without session ID."""
    app = mcp.streamable_http_app(stateless_http=True, json_response=True)
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            # 1. Standalone server/discover (MCP 2026-07-28 negotiation)
            discover_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {"_meta": meta},
            }
            res = await client.post(
                "/mcp",
                json=discover_payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "server/discover",
                },
            )
            assert res.status_code == 200
            assert "Mcp-Session-Id" not in res.headers
            data = res.json()
            assert "result" in data
            assert "supportedVersions" in data["result"]
            assert "2026-07-28" in data["result"]["supportedVersions"]

            # 2. Standalone tools/list with routing headers and _meta
            list_payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {"_meta": meta},
            }
            res_list = await client.post(
                "/mcp",
                json=list_payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/list",
                },
            )
            assert res_list.status_code == 200
            assert "Mcp-Session-Id" not in res_list.headers
            list_data = res_list.json()
            assert "result" in list_data
            assert "tools" in list_data["result"]
            assert len(list_data["result"]["tools"]) >= 155

            # 3. Standalone tools/call with Mcp-Name and _meta
            tool_payload = {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "sigma_api_capabilities",
                    "arguments": {},
                    "_meta": meta,
                },
            }
            res_tool = await client.post(
                "/mcp",
                json=tool_payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "sigma_api_capabilities",
                },
            )
            assert res_tool.status_code == 200
            assert "Mcp-Session-Id" not in res_tool.headers
            tool_data = res_tool.json()
            assert "result" in tool_data
            assert "content" in tool_data["result"]
            parsed_payload = json.loads(tool_data["result"]["content"][0]["text"])
            assert "supported" in parsed_payload
            assert "not_supported" in parsed_payload
            assert "data_models_as_code" in parsed_payload["supported"]


def test_main_streamable_http_and_warning_branches(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Verify main() argument parsing for Streamable HTTP options and deprecation warnings."""
    # 1. Warn on --stateless and --json-response passed to stdio
    ns_stdio = argparse.Namespace(
        transport="stdio",
        host="127.0.0.1",
        port=8000,
        stateless=True,
        json_response=True,
    )
    with patch("argparse.ArgumentParser.parse_args", return_value=ns_stdio):
        with patch("sigma_mcp.server.mcp.run") as mock_run:
            with caplog.at_level(logging.WARNING):
                main()
                mock_run.assert_called_once_with(transport="stdio")
                assert "--stateless flag is only applicable" in caplog.text
                assert "--json-response flag is only applicable" in caplog.text

    # 2. SSE deprecation warning
    caplog.clear()
    ns_sse = argparse.Namespace(
        transport="sse",
        host="127.0.0.1",
        port=8000,
        stateless=False,
        json_response=False,
    )
    with patch("argparse.ArgumentParser.parse_args", return_value=ns_sse):
        with patch("sigma_mcp.server.mcp.run") as mock_run:
            with caplog.at_level(logging.WARNING):
                main()
                mock_run.assert_called_once_with(transport="sse", host="127.0.0.1", port=8000)
                assert "HTTP+SSE transport is deprecated" in caplog.text

    # 3. Streamable HTTP execution with options
    ns_streamable = argparse.Namespace(
        transport="streamable-http",
        host="0.0.0.0",
        port=9000,
        stateless=True,
        json_response=True,
    )
    with patch("argparse.ArgumentParser.parse_args", return_value=ns_streamable):
        with patch("sigma_mcp.server.mcp.run") as mock_run:
            main()
            mock_run.assert_called_once_with(
                transport="streamable-http",
                host="0.0.0.0",
                port=9000,
                stateless_http=True,
                json_response=True,
            )


def test_main_cli_argparse_boolean_optional_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify paired boolean flags (--stateless/--no-stateless, --json-response/--no-json-response)."""
    captured: list[dict[str, Any]] = []

    def fake_run(**kwargs: Any) -> None:
        captured.append(kwargs)

    monkeypatch.setattr("sigma_mcp.server.mcp.run", fake_run)

    # Test explicit disabling flags override truthy environment variables
    monkeypatch.setenv("SIGMA_MCP_STATELESS_HTTP", "1")
    monkeypatch.setenv("SIGMA_MCP_JSON_RESPONSE", "1")
    monkeypatch.setattr(
        "sys.argv",
        [
            "sigma-mcp",
            "--transport",
            "streamable-http",
            "--no-stateless",
            "--no-json-response",
        ],
    )
    main()
    assert len(captured) == 1
    assert captured[0]["stateless_http"] is False
    assert captured[0]["json_response"] is False

    # Test explicit enabling flags
    captured.clear()
    monkeypatch.setenv("SIGMA_MCP_STATELESS_HTTP", "0")
    monkeypatch.setenv("SIGMA_MCP_JSON_RESPONSE", "0")
    monkeypatch.setattr(
        "sys.argv",
        [
            "sigma-mcp",
            "--transport",
            "streamable-http",
            "--stateless",
            "--json-response",
        ],
    )
    main()
    assert len(captured) == 1
    assert captured[0]["stateless_http"] is True
    assert captured[0]["json_response"] is True


def _mock_sigma_backend(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    if "/v2/whoami" in p:
        return httpx.Response(200, json={"memberId": "m-1", "email": "admin@example.com", "name": "Admin"})
    if "/v2/workbooks" in p:
        if request.method == "POST":
            return httpx.Response(200, json={"workbookId": "wb-new-123", "name": "New Workbook"})
        return httpx.Response(200, json={"entries": [{"workbookId": "wb-1", "name": "Executive Dashboard"}]})
    if "/v2/connections" in p:
        return httpx.Response(200, json={"entries": [{"connectionId": "c-1", "name": "Snowflake DW"}]})
    if "/v2/dataModels" in p:
        return httpx.Response(200, json={"entries": [{"dataModelId": "dm-1", "name": "Revenue Model"}]})
    if "/v2/templates" in p:
        return httpx.Response(200, json={"entries": [{"templateId": "tmpl-1", "name": "KPI Template"}]})
    if "/v2/members" in p:
        return httpx.Response(200, json={"entries": [{"memberId": "m-1", "email": "admin@example.com"}]})
    if "/v2/teams" in p:
        return httpx.Response(200, json={"entries": [{"teamId": "t-1", "name": "Data Analytics"}]})
    if "/v2/files" in p:
        return httpx.Response(200, json={"entries": [{"inodeId": "in-1", "name": "Shared Files"}]})
    if "/v2/tags" in p:
        return httpx.Response(200, json={"entries": [{"tagId": "tag-1", "name": "Production"}]})
    if "/v2/user-attributes" in p:
        return httpx.Response(200, json={"entries": [{"userAttributeId": "ua-1", "name": "Department"}]})
    if "/v2/workspaces" in p:
        return httpx.Response(200, json={"entries": [{"workspaceId": "ws-1", "name": "Finance"}]})
    if "/v2/accountTypes" in p:
        return httpx.Response(200, json={"entries": [{"accountTypeId": "act-1", "name": "Creator"}]})
    if "/v2/reports" in p:
        return httpx.Response(200, json={"entries": [{"reportId": "rep-1", "name": "Weekly Report"}]})
    if "/v2/api-connectors" in p:
        return httpx.Response(200, json={"entries": [{"connectorId": "apic-1", "name": "Salesforce Connector"}]})
    return httpx.Response(200, json={"entries": [], "status": 200})


def _setup_mock_sigma_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    import time

    from sigma_mcp import server as srv
    from sigma_mcp.client import SigmaClient

    mock_client = SigmaClient(
        "test-client-id",
        "test-secret-32-chars-long-key-12345",
        "https://api.example.com",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_mock_sigma_backend)),
    )
    mock_client._token = "fake-valid-token"
    mock_client._token_expiry = time.time() + 3600
    monkeypatch.setattr(srv, "_client", mock_client)
    return mock_client


@pytest.mark.asyncio
async def test_stateless_streamable_http_core_tools_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify end-to-end execution of core read tools over Streamable HTTP without a live Sigma org."""
    _setup_mock_sigma_client(monkeypatch)

    tools_to_test = [
        ("sigma_get_current_user", {}),
        ("sigma_list_connections", {}),
        ("sigma_list_workbooks", {"limit": 5}),
        ("sigma_list_data_models", {"limit": 5}),
        ("sigma_list_templates", {"limit": 5}),
        ("sigma_list_members", {"limit": 5}),
        ("sigma_list_teams", {"limit": 5}),
        ("sigma_list_files", {}),
        ("sigma_list_tags", {}),
        ("sigma_list_user_attributes", {}),
        ("sigma_list_workspaces", {}),
        ("sigma_list_account_types", {}),
        ("sigma_list_reports", {"limit": 5}),
        ("sigma_list_api_connectors", {}),
        ("sigma_api_capabilities", {}),
    ]

    app = mcp.streamable_http_app(stateless_http=True, json_response=True)
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            for tool_name, args in tools_to_test:
                res = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": args, "_meta": meta},
                    },
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "MCP-Protocol-Version": "2026-07-28",
                        "Mcp-Method": "tools/call",
                        "Mcp-Name": tool_name,
                    },
                )
                assert res.status_code == 200, f"{tool_name} returned status {res.status_code}"
                data = res.json()
                assert "result" in data, f"{tool_name} missing result: {data}"
                assert "content" in data["result"], f"{tool_name} missing content: {data}"
                payload = json.loads(data["result"]["content"][0]["text"])
                assert isinstance(payload, dict), f"{tool_name} output must be JSON dict"


@pytest.mark.asyncio
async def test_stateless_streamable_http_mutations_and_safety_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify mutating tools and safety confirmation gates over Streamable HTTP."""
    from unittest.mock import AsyncMock

    mock_client = _setup_mock_sigma_client(monkeypatch)
    mock_delete = AsyncMock(return_value=204)
    monkeypatch.setattr(mock_client, "delete_file", mock_delete)

    app = mcp.streamable_http_app(stateless_http=True, json_response=True)
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            # 1. Create workbook confirmed
            res_create = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "tools/call",
                    "params": {
                        "name": "sigma_create_workbook",
                        "arguments": {"name": "Q3 Board Deck", "folder_id": "fld-1"},
                        "_meta": meta,
                    },
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "sigma_create_workbook",
                },
            )
            assert res_create.status_code == 200
            create_data = json.loads(res_create.json()["result"]["content"][0]["text"])
            assert create_data.get("workbookId") == "wb-new-123"

            # 2. Delete file unconfirmed (confirm=False safety gate)
            res_del_preview = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 12,
                    "method": "tools/call",
                    "params": {
                        "name": "sigma_delete_file",
                        "arguments": {"inode_id": "in-old", "confirm": False},
                        "_meta": meta,
                    },
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "sigma_delete_file",
                },
            )
            assert res_del_preview.status_code == 200
            del_preview_data = json.loads(res_del_preview.json()["result"]["content"][0]["text"])
            assert del_preview_data["error"]["type"] == "invalid_request"
            assert "confirm=True" in del_preview_data["error"]["message"]
            mock_delete.assert_not_called()

            # 3. Delete file confirmed (confirm=True)
            res_del = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 13,
                    "method": "tools/call",
                    "params": {
                        "name": "sigma_delete_file",
                        "arguments": {"inode_id": "in-old", "confirm": True},
                        "_meta": meta,
                    },
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "sigma_delete_file",
                },
            )
            assert res_del.status_code == 200
            del_data = json.loads(res_del.json()["result"]["content"][0]["text"])
            assert del_data.get("status") == 204
            mock_delete.assert_awaited_once_with("in-old")
