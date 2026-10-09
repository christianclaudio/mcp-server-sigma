"""Additional unit tests to boost line coverage across client.py and server.py."""

import logging

import pytest
from fastmcp.exceptions import ToolError

from sigma_mcp import server as srv


def test_redact_secrets_empty_and_no_match() -> None:
    assert srv._redact_secrets("") == ""
    assert srv._redact_secrets("clean text") == "clean text"


def test_structured_json_formatter_default() -> None:
    formatter = srv.StructuredJSONFormatter()
    record = logging.LogRecord("sigma_mcp", logging.INFO, "server.py", 100, "Test message", (), None)
    formatted = formatter.format(record)
    assert '"level": "INFO"' in formatted
    assert '"message": "Test message"' in formatted


@pytest.mark.asyncio
async def test_get_client_uninitialized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIGMA_CLIENT_ID", raising=False)
    monkeypatch.delenv("SIGMA_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(srv, "_client", None)
    with pytest.raises(ValueError, match="SIGMA_CLIENT_ID and SIGMA_CLIENT_SECRET must be set"):
        await srv.get_client()


def test_main_cli_variations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["sigma-mcp", "--transport", "stdio"])
    monkeypatch.setattr(srv.mcp, "run", lambda **kwargs: None)
    srv.main()

    monkeypatch.setenv("SIGMA_MCP_AUTH_TOKEN", "secret-token")
    monkeypatch.setattr("sys.argv", ["sigma-mcp", "--transport", "sse", "--host", "0.0.0.0", "--port", "9000"])
    srv.main()

    monkeypatch.setattr("sys.argv", ["sigma-mcp", "--transport", "streamable-http"])
    srv.main()


@pytest.mark.asyncio
async def test_recipe_error_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    # Empty required parameters raise ToolError (a tool execution error, isError: true)
    with pytest.raises(ToolError, match="template_id is required"):
        await srv.sigma_deploy_template_to_folder("", "f1", {})

    with pytest.raises(ToolError, match="email is required"):
        await srv.sigma_onboard_member("", "A", "B")

    with pytest.raises(ToolError, match="team_id is required"):
        await srv.sigma_bulk_assign_team_members("", ["a@b.com"])


@pytest.mark.asyncio
async def test_search_members_email_and_name_branches() -> None:
    from unittest.mock import AsyncMock

    from sigma_mcp.client import SigmaClient

    client = SigmaClient("cid", "secret-32-chars-long-client-secret!", "https://api.sigmacomputing.com")
    client.get = AsyncMock(return_value={"entries": []})  # type: ignore[method-assign]

    # Test email branch (uses "email" parameter)
    await client.search_members("user@example.com", limit=50)
    client.get.assert_called_with("/v2/members", {"email": "user@example.com", "limit": 50})

    # Test non-email branch (uses "search" parameter fallback)
    await client.search_members("Jordan", limit=100)
    client.get.assert_called_with("/v2/members", {"search": "Jordan", "limit": 100})
