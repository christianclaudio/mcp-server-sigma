"""Comprehensive coverage tests targeting 100.00% statement parity.

Covers:
- config.py: lenient boolean parsing
- tools/common.py: _summarize_list edge cases
- middleware.py: is_read_only_tool prefixes, ReadOnlyGateMiddleware, AdminDomainGuardMiddleware
- server.py: _summarize_list, _ToolManagerCompat, create_server profiles and transforms, main()
- client.py: Recipe 10 formatters, SSRFSafeAsyncTransport, _validate_base_url, _validate_hostname_dns
"""

from __future__ import annotations

import socket
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sigma_mcp.client import (
    SSRFSafeAsyncTransport,
    _validate_base_url,
    _validate_hostname_dns,
    extract_dict,
    extract_list,
    is_terminal_status,
    normalize_refs_and_prune,
    safe_dict,
    safe_int_or_zero,
    safe_list,
    validate_candidate,
)
from sigma_mcp.config import Settings
from sigma_mcp.errors import SafetyViolationError, SigmaAPIError
from sigma_mcp.middleware import (
    AdminDomainGuardMiddleware,
    ReadOnlyGateMiddleware,
    is_read_only_tool,
)
from sigma_mcp.server import (
    _summarize_list as server_summarize_list,
)
from sigma_mcp.server import (
    _ToolManagerCompat,
    create_server,
    main,
)
from sigma_mcp.tools.common import _summarize_list as common_summarize_list

# ============================================================================
# 1. Config Lenient Bool Coverage
# ============================================================================


def test_config_lenient_bool_parsing() -> None:
    for false_val in ("", "0", "false", "no", "off", "  off  ", "False"):
        s = Settings(MCP_READONLY=false_val)  # type: ignore[arg-type]
        assert s.MCP_READONLY is False

    for true_val in ("1", "true", "yes", "on", "  yes  ", "True"):
        s = Settings(MCP_READONLY=true_val)  # type: ignore[arg-type]
        assert s.MCP_READONLY is True

    # Fallback to bool(v)
    s = Settings(MCP_READONLY=True)
    assert s.MCP_READONLY is True
    s = Settings(MCP_READONLY=False)
    assert s.MCP_READONLY is False


# ============================================================================
# 2. tools/common.py _summarize_list
# ============================================================================


def test_common_summarize_list_non_entries() -> None:
    data_dict_no_entries = {"foo": "bar"}
    assert common_summarize_list(data_dict_no_entries, ["id"]) == data_dict_no_entries

    data_list = [1, 2, 3]
    assert common_summarize_list(data_list, ["id"]) == data_list


# ============================================================================
# 3. middleware.py Coverage
# ============================================================================


def test_middleware_is_read_only_tool_prefixes() -> None:
    assert is_read_only_tool("workbooks_list_workbooks") is True
    assert is_read_only_tool("datasets_get_dataset") is True
    assert is_read_only_tool("elements_get_element") is True
    assert is_read_only_tool("workspace_list_files") is True
    assert is_read_only_tool("admin_list_members") is True

    # Mutating with sub-server prefix
    assert is_read_only_tool("admin_delete_file") is False
    assert is_read_only_tool("workbooks_delete_workbook") is False
    assert is_read_only_tool("custom_unknown_prefix_test") is False


@pytest.mark.asyncio
async def test_read_only_gate_middleware_blocks_mutation() -> None:
    mw = ReadOnlyGateMiddleware()
    ctx = SimpleNamespace(
        method="tools/call",
        message=SimpleNamespace(name="sigma_delete_file"),
    )
    next_called = False

    async def dummy_next(_c: Any) -> str:
        nonlocal next_called
        next_called = True
        return "ok"

    with patch("sigma_mcp.middleware.settings.MCP_READONLY", True):
        with pytest.raises(SafetyViolationError, match="read-only mode"):
            await mw.on_message(ctx, dummy_next)  # type: ignore[arg-type]
        assert not next_called

    # When tool is read-only, it passes
    ctx_ro = SimpleNamespace(
        method="tools/call",
        message=SimpleNamespace(name="sigma_list_workbooks"),
    )
    with patch("sigma_mcp.middleware.settings.MCP_READONLY", True):
        res = await mw.on_message(ctx_ro, dummy_next)  # type: ignore[arg-type]
        assert res == "ok"
        assert next_called


@pytest.mark.asyncio
async def test_admin_domain_guard_middleware_blocks_bulk() -> None:
    mw = AdminDomainGuardMiddleware()
    ctx = SimpleNamespace(
        method="tools/call",
        message=SimpleNamespace(name="sigma_bulk_deactivate_members"),
    )
    next_called = False

    async def dummy_next(_c: Any) -> str:
        nonlocal next_called
        next_called = True
        return "ok"

    with patch("sigma_mcp.middleware.settings.MCP_ALLOW_BULK_DESTRUCTIVE", False):
        with patch.dict("os.environ", {"SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE": ""}, clear=False):
            with pytest.raises(SafetyViolationError, match="Bulk destructive operations disabled"):
                await mw.on_message(ctx, dummy_next)  # type: ignore[arg-type]
            assert not next_called

    # When bulk destructive is allowed
    with patch("sigma_mcp.middleware.settings.MCP_ALLOW_BULK_DESTRUCTIVE", True):
        res = await mw.on_message(ctx, dummy_next)  # type: ignore[arg-type]
        assert res == "ok"
        assert next_called


# ============================================================================
# 4. server.py Coverage
# ============================================================================


def test_server_summarize_list_branches() -> None:
    assert server_summarize_list("plain string", ["id"]) == "plain string"
    input_data = {
        "entries": [
            {"id": "w1", "name": "Workbook 1", "secret": "hidden"},
            "non_dict_element",
        ],
        "total": 2,
    }
    summarized = server_summarize_list(input_data, ["id", "name"])
    assert summarized["entries"] == [
        {"id": "w1", "name": "Workbook 1"},
        "non_dict_element",
    ]


def test_server_tool_manager_compat_uncovered_branches() -> None:
    mock_server = MagicMock()
    mock_provider = MagicMock()
    mock_server.providers = [mock_provider]
    mock_server._local_provider = mock_provider

    # Tool with no name (empty)
    unnamed_tool = MagicMock()
    unnamed_tool.name = None

    # Tool with non-sigma name
    custom_tool = MagicMock()
    custom_tool.name = "custom_lookup"
    custom_tool.tags = []

    mock_provider._components = {
        "tool:unnamed": unnamed_tool,
        "tool:custom": custom_tool,
    }

    compat = _ToolManagerCompat(mock_server)
    tools = compat._tools
    assert "custom_lookup" in tools
    assert "sigma_custom_lookup" in tools

    # Test remove_tool
    compat.remove_tool("custom_lookup")
    mock_server.disable.assert_called_with(names={"custom_lookup", "sigma_custom_lookup"})
    mock_provider.disable.assert_called_with(names={"custom_lookup", "sigma_custom_lookup"})


def test_create_server_invalid_profile() -> None:
    with pytest.raises(ValueError, match="Unknown SIGMA_MCP_PROFILE"):
        create_server(profile="non_existent_profile")


def test_create_server_with_bulk_destructive_and_profiles() -> None:
    with patch.dict("os.environ", {"SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE": "1"}):
        srv = create_server(profile="core")
        assert srv is not None

    srv_admin = create_server(profile="admin")
    assert srv_admin is not None

    srv_embed = create_server(profile="embed")
    assert srv_embed is not None


def test_create_server_enable_tool_search() -> None:
    srv = create_server(enable_tool_search=True)
    assert srv is not None
    assert len(srv._transforms) >= 1


def test_main_cli_profile_override() -> None:
    import sys

    with patch.object(sys, "argv", ["sigma_mcp", "--profile", "core", "--transport", "stdio"]):
        with patch("sigma_mcp.server.create_server", wraps=create_server) as mock_create:
            with patch("fastmcp.FastMCP.run"):
                main()
                mock_create.assert_called()


# ============================================================================
# 5. client.py Coverage (Recipe 10 + Network/SSRF Hardening)
# ============================================================================


def test_validate_base_url_branches() -> None:
    # Empty url fallback
    with patch("sigma_mcp.client.settings.BASE_URL", "https://default.sigmacomputing.com"):
        assert _validate_base_url("") == "https://default.sigmacomputing.com"

    # Non-HTTPS external
    with pytest.raises(ValueError, match="Only HTTPS is permitted"):
        _validate_base_url("http://external.sigmacomputing.com")

    # Missing hostname
    with pytest.raises(ValueError, match="missing hostname"):
        _validate_base_url("https://")

    # Internal / loopback hostname
    with pytest.raises(ValueError, match="Blocked internal/loopback"):
        _validate_base_url("https://localhost")
    with pytest.raises(ValueError, match="Blocked internal/loopback"):
        _validate_base_url("https://service.internal")
    with pytest.raises(ValueError, match="Blocked internal/loopback"):
        _validate_base_url("https://node.local")

    # Allowed hosts check
    with pytest.raises(ValueError, match="not in allowed hosts"):
        _validate_base_url("https://evil.com", allowed_hosts_str="api.sigmacomputing.com")

    # Private IP
    with pytest.raises(ValueError, match="Blocked private/reserved IP"):
        _validate_base_url("https://10.0.0.1")
    with pytest.raises(ValueError, match="Blocked private/reserved IP"):
        _validate_base_url("https://192.168.1.1")

    # Public IP allowed when in allowlist
    valid_ip_url = _validate_base_url("https://8.8.8.8", allowed_hosts_str="8.8.8.8")
    assert valid_ip_url == "https://8.8.8.8"

    # check_dns=True flag
    with patch("sigma_mcp.client._validate_hostname_dns") as mock_dns:
        assert _validate_base_url("https://api.sigmacomputing.com", check_dns=True) == "https://api.sigmacomputing.com"
        mock_dns.assert_called_once_with("api.sigmacomputing.com")


def test_validate_hostname_dns_branches() -> None:
    # Public hostname resolving to public IP
    with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.215.14", 443))]):
        _validate_hostname_dns("example.com")
        _validate_hostname_dns("api.sigmacomputing.com")

    # IP literal branches
    _validate_hostname_dns("8.8.8.8")
    with pytest.raises(ValueError, match="Blocked private/reserved IP"):
        _validate_hostname_dns("127.0.0.1")
    with pytest.raises(ValueError, match="Blocked private/reserved IP"):
        _validate_hostname_dns("10.1.2.3")

    # DNS resolution failure
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("lookup failure")):
        with pytest.raises(ValueError, match="Could not resolve hostname"):
            _validate_hostname_dns("non-existent-lookup-failure.test")

    # DNS resolving to private IP
    with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("192.168.1.50", 443))]):
        with pytest.raises(ValueError, match="resolving to private/reserved IP"):
            _validate_hostname_dns("rebinding-attack.test")


@pytest.mark.asyncio
async def test_ssrf_safe_async_transport() -> None:
    transport = SSRFSafeAsyncTransport()

    # Success case (DNS mock returns public IP)
    req_ok = httpx.Request("GET", "https://example.com/api/test")
    with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.215.14", 443))]):
        with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", new_callable=AsyncMock) as mock_super:
            mock_super.return_value = httpx.Response(200, request=req_ok)
            resp = await transport.handle_async_request(req_ok)
            assert resp.status_code == 200
            mock_super.assert_called_once_with(req_ok)

    # Blocked case (private IP)
    req_bad = httpx.Request("GET", "https://10.0.0.5/api/test")
    with pytest.raises(SigmaAPIError) as exc_info:
        await transport.handle_async_request(req_bad)
    assert "SSRF validation blocked request" in (exc_info.value.detail or "")

    # Loopback case (http://localhost permitted)
    req_loopback = httpx.Request("GET", "http://localhost:8000/api/test")
    with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", new_callable=AsyncMock) as mock_super:
        mock_super.return_value = httpx.Response(200, request=req_loopback)
        resp = await transport.handle_async_request(req_loopback)
        assert resp.status_code == 200

    # Loopback case (http://127.0.0.1 permitted)
    req_loopback_ip = httpx.Request("GET", "http://127.0.0.1:8000/api/test")
    with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", new_callable=AsyncMock) as mock_super:
        mock_super.return_value = httpx.Response(200, request=req_loopback_ip)
        resp = await transport.handle_async_request(req_loopback_ip)
        assert resp.status_code == 200

    # DNS timeout case
    req_timeout = httpx.Request("GET", "https://slow-dns.example.com/api/test")
    fast_timeout_transport = SSRFSafeAsyncTransport(dns_timeout=0.001)

    def _stalled_dns(h: str) -> None:
        import time

        time.sleep(0.05)

    with patch("sigma_mcp.client._validate_hostname_dns", side_effect=_stalled_dns):
        with pytest.raises(SigmaAPIError) as exc_timeout:
            await fast_timeout_transport.handle_async_request(req_timeout)
        assert "DNS resolution timed out" in (exc_timeout.value.detail or "")


@pytest.mark.asyncio
async def test_ssrf_transport_dns_runs_off_event_loop() -> None:
    """Assert that the DNS resolution lookup thread is not the asyncio event-loop thread."""
    import threading

    transport = SSRFSafeAsyncTransport()
    loop_thread_id = threading.get_ident()
    lookup_thread_id: int | None = None

    def _spy_dns(hostname: str) -> None:
        nonlocal lookup_thread_id
        lookup_thread_id = threading.get_ident()

    req = httpx.Request("GET", "https://api.sigmacomputing.com/v2/workbooks")
    with patch("sigma_mcp.client._validate_hostname_dns", side_effect=_spy_dns):
        with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", new_callable=AsyncMock) as mock_super:
            mock_super.return_value = httpx.Response(200, request=req)
            await transport.handle_async_request(req)

    assert lookup_thread_id is not None
    assert lookup_thread_id != loop_thread_id


def test_recipe10_defensive_extractors() -> None:
    # extract_dict
    assert extract_dict({"data": {"x": 1}}, "data") == {"x": 1}
    assert extract_dict({"data": None}, "data") == {}
    assert extract_dict("string_container", "data") == {}
    assert extract_dict(None) == {}
    assert extract_dict({"x": 1}) == {"x": 1}
    assert safe_dict({"y": 2}) == {"y": 2}

    # extract_list
    assert extract_list({"items": [1, 2]}, "items") == [1, 2]
    assert extract_list({"items": None}, "items") == []
    assert extract_list("string_container", "items") == []
    assert extract_list(None) == []
    assert extract_list([1, 2, 3]) == [1, 2, 3]
    assert safe_list([4, 5]) == [4, 5]


def test_recipe10_validate_candidate() -> None:
    assert validate_candidate("not_a_dict", ["id"]) is False
    assert validate_candidate({"id": None}, ["id"]) is False
    assert validate_candidate({"id": ""}, ["id"]) is False
    assert validate_candidate({"id": "valid_id", "name": "test"}, ["id", "name"]) is True
    assert validate_candidate({"id": "valid_id", "name": ""}, ["id", "name"]) is False


def test_recipe10_is_terminal_status() -> None:
    assert is_terminal_status("final", state="running") is False
    assert is_terminal_status("final", state="pending") is False
    assert is_terminal_status(12345) is False
    assert is_terminal_status("final") is True
    assert is_terminal_status("COMPLETED") is True
    assert is_terminal_status("final/ready") is True
    assert is_terminal_status("final - complete") is True
    assert is_terminal_status("failed: timeout") is True
    assert is_terminal_status("unknown_state") is False


def test_recipe10_normalize_refs_and_prune() -> None:
    assert normalize_refs_and_prune("plain_string") == "plain_string"

    # Single $ref dictionary
    assert normalize_refs_and_prune({"$ref": "https://api/elements/elem_123/"}) == {"id": "elem_123"}

    # Nested with pruning
    payload = {
        "$ref": None,
        "name": "Elem",
        "logos": "prune_me",
        "links": ["a", "b"],
        "sub_items": [
            {"$ref": "https://api/users/user_99"},
            {"keep": "value", "headshot": "remove_me"},
        ],
    }
    result = normalize_refs_and_prune(payload)
    assert result["name"] == "Elem"
    assert "logos" not in result
    assert "links" not in result
    assert result["sub_items"] == [{"id": "user_99"}, {"keep": "value"}]


def test_recipe10_safe_int_or_zero() -> None:
    assert safe_int_or_zero(None) is None
    assert safe_int_or_zero(0) == 0
    assert safe_int_or_zero(42) == 42
    assert safe_int_or_zero("0") == 0
    assert safe_int_or_zero("123") == 123
    assert safe_int_or_zero("invalid") is None
    assert safe_int_or_zero(3.14) is None


@pytest.mark.asyncio
async def test_workspace_pagination_coverage() -> None:
    from sigma_mcp.client import SigmaClient
    from sigma_mcp.tools.workspace import (
        sigma_list_all_files,
        sigma_list_tags,
        sigma_list_workspace_grants,
        sigma_list_workspaces,
    )

    client = SigmaClient("cid", "csec", "https://api.example.com", max_retries=0, base_delay=0.001)
    mock_resp = httpx.Response(200, json={"entries": [{"grantId": "g1"}]})
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_resp
        res = await client.list_workspace_grants("ws1", page="p1", limit=10)
        assert res == {"entries": [{"grantId": "g1"}]}
        mock_req.assert_called_once_with("GET", "/v2/workspaces/ws1/grants", params={"limit": 10, "page": "p1"})

    # Test tools execution with pagination arguments
    with patch("sigma_mcp.tools.workspace.get_client", AsyncMock(return_value=client)):
        with patch.object(client, "list_tags", new_callable=AsyncMock) as mock_tags:
            mock_tags.return_value = {"entries": []}
            await sigma_list_tags(page="p_tag", limit=50)
            mock_tags.assert_called_once_with(page="p_tag", limit=50)

        with patch.object(client, "list_workspaces", new_callable=AsyncMock) as mock_ws:
            mock_ws.return_value = {"entries": []}
            await sigma_list_workspaces(limit=25, page="p_ws")
            mock_ws.assert_called_once_with(limit=25, page="p_ws")

        with patch.object(client, "list_workspace_grants", new_callable=AsyncMock) as mock_wg:
            mock_wg.return_value = {"entries": []}
            await sigma_list_workspace_grants("ws_x", page="p_g", limit=15)
            mock_wg.assert_called_once_with("ws_x", page="p_g", limit=15)

        with patch.object(client, "list_all_files", new_callable=AsyncMock) as mock_laf:
            mock_laf.return_value = []
            await sigma_list_all_files(parent_id="folder1", type_filter="symlink")
            mock_laf.assert_called_once_with({"parentId": "folder1", "typeFilters": "symlink"})
