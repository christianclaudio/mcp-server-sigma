"""Additional unit tests to boost line coverage across client.py and server.py."""

import logging

import pytest

from sigma_mcp import server as srv


def test_redact_secrets_empty_and_no_match() -> None:
    assert srv._redact_secrets("") == ""
    assert srv._redact_secrets("clean text") == "clean text"


def test_invalid_request_helper() -> None:
    res = srv._invalid_request("Missing param")
    assert '"type": "invalid_request"' in res
    assert '"message": "Missing param"' in res


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
    # Test empty parameter validation error returns
    res1 = await srv.sigma_deploy_template_to_folder("", "f1", {})
    assert "template_id is required" in res1

    res2 = await srv.sigma_onboard_member("", "A", "B")
    assert "email is required" in res2

    res3 = await srv.sigma_bulk_assign_team_members("", ["a@b.com"])
    assert "team_id is required" in res3
