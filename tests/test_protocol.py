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
            "protocolVersion": "2026-07-28",
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
