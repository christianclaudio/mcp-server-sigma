"""Mock-based tests for all MCP tools — no live Sigma org needed.

Uses unittest.mock to patch httpx.AsyncClient.request, simulating API responses.
Covers the structured error handling, async behavior, and tool output format.

    PYTHONPATH=src python -m pytest tests/test_tools_mocked.py -v
"""

import asyncio
import json
import sys
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.exceptions import ToolError

CODE_MODE_SKIP_REASON = "needs the code-mode extra (fastmcp[code-mode] / pydantic-monty)"

sys.path.insert(0, "src")


def _mock_response(status_code: int = 200, json_data=None, content: bytes | None = None, headers=None):
    """Create a mock httpx Response.

    When json_data is given and content is not, content is derived from it so the
    mock matches a real Response: the client treats an empty body as "no JSON"
    and returns None, so a mock with a body but empty .content would misrepresent
    a successful call.
    """
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data or {}
    body = json.dumps(json_data) if json_data is not None else ""
    r.content = content if content is not None else body.encode()
    r.text = body
    r.headers = headers or {}
    return r


def _setup_client_mock():
    """Patch the global _client to return mock responses."""
    from sigma_mcp.client import SigmaClient

    c = SigmaClient("test-id", "test-secret", "https://api.sigmacomputing.com")
    c._token = "fake-token"
    c._token_expiry = time.time() + 3600
    return c


@pytest.fixture(autouse=True)
def reset_client():
    """Reset the global client before each test."""
    import sigma_mcp.server as srv

    srv._client = None
    yield
    srv._client = None


class TestToolsReturnJSON:
    """Verify tools return valid JSON, and failures raise ToolError with JSON error text."""

    def _call(self, tool_name, args=None):
        from sigma_mcp.server import mcp

        result = asyncio.run(mcp.call_tool(tool_name, args or {}))
        text = result.content[0].text
        return json.loads(text)

    def _call_error(self, tool_name, args=None):
        """A failed call raises ToolError (isError: true) carrying the redacted error JSON."""
        from sigma_mcp.server import mcp

        with pytest.raises(ToolError) as exc_info:
            asyncio.run(mcp.call_tool(tool_name, args or {}))
        return json.loads(str(exc_info.value))

    def test_list_connections_success(self):
        from sigma_mcp import server as srv

        c = _setup_client_mock()
        c._http = MagicMock()
        c._http.request = AsyncMock(return_value=_mock_response(200, {"entries": [{"connectionId": "c1"}]}))
        srv._client = c
        data = self._call("datasets_list_connections")
        assert "entries" in data

    def test_get_workbook_not_found(self):
        from sigma_mcp import server as srv

        c = _setup_client_mock()
        c._http = MagicMock()
        c._http.request = AsyncMock(return_value=_mock_response(404, {"message": "Not found"}))
        srv._client = c
        data = self._call_error("workbooks_get_workbook", {"workbook_id": "nonexistent"})
        assert "error" in data
        assert data["error"]["status_code"] == 404

    def test_list_workbooks_success(self):
        from sigma_mcp import server as srv

        c = _setup_client_mock()
        c._http = MagicMock()
        c._http.request = AsyncMock(
            return_value=_mock_response(200, {"entries": [{"workbookId": "w1", "name": "Test"}]})
        )
        srv._client = c
        data = self._call("workbooks_list_workbooks", {"limit": 5})
        assert "entries" in data

    def test_create_workbook_success(self):
        from sigma_mcp import server as srv

        c = _setup_client_mock()
        c._http = MagicMock()
        c._http.request = AsyncMock(return_value=_mock_response(200, {"workbookId": "new-wb"}))
        srv._client = c
        data = self._call("workbooks_create_workbook", {"name": "test", "folder_id": "f1"})
        assert data.get("workbookId") == "new-wb"

    def test_delete_file_success(self):
        from sigma_mcp import server as srv

        c = _setup_client_mock()
        c._http = MagicMock()
        c._http.request = AsyncMock(return_value=_mock_response(204))
        srv._client = c
        data = self._call("workspace_delete_file", {"inode_id": "inode1", "confirm": True})
        assert data.get("status") == 204

    def test_list_members_success(self):
        from sigma_mcp import server as srv

        c = _setup_client_mock()
        c._http = MagicMock()
        c._http.request = AsyncMock(return_value=_mock_response(200, {"entries": [{"memberId": "m1"}]}))
        srv._client = c
        data = self._call("admin_list_members", {"limit": 5})
        assert "entries" in data

    def test_list_teams_success(self):
        from sigma_mcp import server as srv

        c = _setup_client_mock()
        c._http = MagicMock()
        c._http.request = AsyncMock(return_value=_mock_response(200, {"entries": []}))
        srv._client = c
        data = self._call("admin_list_teams", {"limit": 5})
        assert "entries" in data

    def test_api_capabilities(self):
        data = self._call("admin_api_capabilities")
        assert "supported" in data
        assert "not_supported" in data
        assert "gotchas" in data

    def test_server_error_returns_structured(self):
        from sigma_mcp import server as srv

        c = _setup_client_mock()
        c._http = MagicMock()
        c._http.request = AsyncMock(return_value=_mock_response(500, {"message": "Internal error"}))
        srv._client = c
        data = self._call_error("workspace_list_tags")
        assert "error" in data
        assert data["error"]["status_code"] == 500

    def test_rate_limit_error(self):
        from sigma_mcp import server as srv

        c = _setup_client_mock()
        c._http = MagicMock()
        c._http.request = AsyncMock(return_value=_mock_response(429, None, headers={}))
        c.max_retries = 0
        c.base_delay = 0.001
        srv._client = c
        data = self._call_error("datasets_list_connections")
        assert "error" in data
        assert data["error"]["status_code"] == 429


class TestCompositeToolsValidation:
    """Verify composite tools handle validation correctly."""

    def _call(self, tool_name, args=None):
        from sigma_mcp.server import mcp

        result = asyncio.run(mcp.call_tool(tool_name, args or {}))
        return json.loads(result.content[0].text)

    def _call_invalid(self, tool_name, args=None):
        """Call a tool expected to fail input validation; it raises ToolError (isError: true)."""
        from sigma_mcp.server import mcp

        with pytest.raises(ToolError) as exc_info:
            asyncio.run(mcp.call_tool(tool_name, args or {}))
        data = json.loads(str(exc_info.value))
        assert data["error"]["type"] == "invalid_request"
        return data

    @staticmethod
    def _error_text(data: dict) -> str:
        """Extract error message text from either flat or nested error shape."""
        err = data.get("error", "")
        if isinstance(err, dict):
            return err.get("message", "")
        return str(err)

    def test_export_and_download_empty_workbook_id(self):
        data = self._call_invalid("workbooks_export_and_download", {"workbook_id": ""})
        assert "error" in data
        assert "workbook_id" in self._error_text(data)

    def test_reassign_ownership_empty_email(self):
        data = self._call_invalid(
            "workbooks_reassign_workbook_ownership", {"old_owner_email": "", "new_owner_email": "b@b.com"}
        )
        assert "error" in data
        assert "old_owner_email" in self._error_text(data)

    def test_list_shared_workbooks_empty_member(self):
        data = self._call_invalid("workbooks_list_workbooks_shared_with_member", {"member_id": ""})
        assert "error" in data
        assert "member_id" in self._error_text(data)

    def test_bulk_deactivate_empty_pattern(self, monkeypatch):
        """Listed in full; refused without SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1, validated with it."""
        import pytest

        from sigma_mcp.errors import SafetyViolationError

        monkeypatch.delenv("SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE", raising=False)
        with pytest.raises(SafetyViolationError, match="Bulk destructive operations disabled"):
            self._call("admin_bulk_deactivate_members", {"name_pattern": ""})
        monkeypatch.setenv("SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE", "1")
        data = self._call_invalid("admin_bulk_deactivate_members", {"name_pattern": ""})
        assert "error" in data

    def test_change_email_empty_member_id(self):
        data = self._call_invalid("admin_change_member_email", {"member_id": "", "new_email": "x@y.com"})
        assert "error" in data
        assert "member_id" in self._error_text(data)

    def test_bulk_remove_team_empty_team_id(self, monkeypatch):
        """Listed in full; refused without SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1, validated with it."""
        import pytest

        from sigma_mcp.errors import SafetyViolationError

        monkeypatch.delenv("SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE", raising=False)
        with pytest.raises(SafetyViolationError, match="Bulk destructive operations disabled"):
            self._call("admin_bulk_remove_team_members", {"team_id": "", "member_emails": ["x@y.com"]})
        monkeypatch.setenv("SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE", "1")
        data = self._call_invalid(
            "admin_bulk_remove_team_members", {"team_id": "", "member_emails": ["x@y.com"], "confirm": True}
        )
        assert "team_id" in self._error_text(data)


class TestErrorRedaction:
    """Verify client_secret is redacted from error messages."""

    def test_secret_redacted_in_error(self):
        import os

        os.environ["SIGMA_CLIENT_SECRET"] = "super-secret-value"
        from sigma_mcp.server import _redact_secrets

        msg = "Error with super-secret-value in message"
        assert "super-secret-value" not in _redact_secrets(msg)
        assert "***REDACTED***" in _redact_secrets(msg)
        del os.environ["SIGMA_CLIENT_SECRET"]


class TestSigmaAPIError:
    """Verify SigmaAPIError structure."""

    def test_to_dict(self):
        from sigma_mcp.errors import SigmaAPIError

        e = SigmaAPIError(403, "/v2/test", "POST", detail={"reason": "forbidden"}, request_id="req-1")
        d = e.to_dict()
        assert d["type"] == "sigma_api_error"
        assert d["status_code"] == 403
        assert d["path"] == "/v2/test"
        assert d["method"] == "POST"
        assert d["detail"] == {"reason": "forbidden"}
        assert d["request_id"] == "req-1"

    def test_str_representation(self):
        from sigma_mcp.errors import SigmaAPIError

        e = SigmaAPIError(404, "/v2/workbooks/abc", "GET")
        assert "404" in str(e)
        assert "/v2/workbooks/abc" in str(e)


@pytest.mark.asyncio
async def test_tool_failure_reaches_client_as_is_error(caplog):
    """An upstream failure reaches the client as isError: true, redacted on the wire and in logs."""
    import logging

    from fastmcp import Client

    from sigma_mcp import server as srv

    c = _setup_client_mock()
    c._http = MagicMock()
    c._http.aclose = AsyncMock()
    c._http.request = AsyncMock(side_effect=RuntimeError("upstream rejected Bearer secret-token-abc"))
    c.max_retries = 0
    c.base_delay = 0.001
    srv._client = c

    caplog.set_level(logging.DEBUG)
    async with Client(srv.create_server()) as mcp_client:
        result = await mcp_client.call_tool("workspace_list_tags", {}, raise_on_error=False)

    assert result.is_error
    text = result.content[0].text
    assert json.loads(text)["error"]["type"] == "internal"
    assert "***REDACTED***" in text
    assert "secret-token-abc" not in text
    logged = "\n".join(
        record.getMessage() + (logging.Formatter().formatException(record.exc_info) if record.exc_info else "")
        for record in caplog.records
    )
    assert "secret-token-abc" not in logged


@pytest.mark.asyncio
async def test_code_mode_execute_runs_real_tool():
    """execute runs Python in the Code Mode sandbox and reaches a real catalog tool."""
    pytest.importorskip("pydantic_monty", reason=CODE_MODE_SKIP_REASON)
    from fastmcp import Client

    from sigma_mcp import server as srv

    c = _setup_client_mock()
    c._http = MagicMock()
    c._http.aclose = AsyncMock()
    c._http.request = AsyncMock(return_value=_mock_response(200, {"entries": [{"tagId": "t1", "name": "Gold"}]}))
    srv._client = c

    app = srv.create_server(profile="full", enable_code_mode=True, enable_tool_search=False)
    code = "res = await call_tool('workspace_list_tags', {})\nreturn res"
    async with Client(app) as mcp_client:
        arith = await mcp_client.call_tool("execute", {"code": "return 1 + 1"}, raise_on_error=False)
        res = await mcp_client.call_tool("execute", {"code": code}, raise_on_error=False)

    assert not arith.is_error, arith.content
    assert [getattr(block, "text", None) for block in arith.content] == ["2"]
    assert not res.is_error, res.content
    assert "Gold" in str(res.content)
