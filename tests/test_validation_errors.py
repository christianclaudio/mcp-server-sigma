"""Input validation failures are tool execution errors; the confirm two-step is not.

MCP spec 2026-07-28, server/tools, Error Handling: input validation errors are tool
execution errors, "reported in tool results with `isError: true`", so the model can
correct the call. A destructive call without ``confirm=True`` is the designed two-step:
it changes nothing and returns a normal result telling the caller to re-call.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from sigma_mcp import server
from sigma_mcp.server import create_server
from sigma_mcp.tools.common import _confirm_required, _invalid_request, sigma_tool

_SECRET = "s3cr3t-client-secret-value-for-redaction-test"


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """A Sigma client the validation and confirm paths must never reach."""
    client = AsyncMock()
    monkeypatch.setattr(server, "get_client", AsyncMock(return_value=client))
    return client


@pytest.mark.asyncio
async def test_bad_argument_is_error_true_with_redacted_text(monkeypatch: pytest.MonkeyPatch, api: AsyncMock) -> None:
    """Over the wire, a bad argument comes back as isError: true with secrets redacted."""
    monkeypatch.setenv("SIGMA_CLIENT_SECRET", _SECRET)
    bad = f"Bearer abc.def-{_SECRET}"
    async with Client(create_server(profile="full")) as client:
        res = await client.call_tool(
            "admin_onboard_member",
            {"email": "a@b.co", "first_name": "A", "last_name": "B", "member_type": bad},
            raise_on_error=False,
        )
    assert res.is_error is True
    text = res.content[0].text  # type: ignore[union-attr]
    payload = json.loads(text)
    assert payload["error"]["type"] == "invalid_request"
    assert payload["error"]["message"].startswith("member_type must be one of")
    assert _SECRET not in text
    assert "abc.def" not in text
    assert "***REDACTED***" in text
    api.create_member.assert_not_called()


@pytest.mark.asyncio
async def test_missing_required_argument_is_error_true(api: AsyncMock) -> None:
    """An empty required argument is a failed call, not a successful result."""
    async with Client(create_server(profile="full")) as client:
        res = await client.call_tool(
            "admin_change_member_email", {"member_id": "", "new_email": "x@y.co"}, raise_on_error=False
        )
    assert res.is_error is True
    payload = json.loads(res.content[0].text)  # type: ignore[union-attr]
    assert payload == {"error": {"type": "invalid_request", "message": "member_id is required"}}
    api.update_member.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_prompt_is_a_normal_result(api: AsyncMock) -> None:
    """The confirm two-step stays isError: false with a clear re-call message, and changes nothing."""
    async with Client(create_server(profile="full")) as client:
        res = await client.call_tool("workspace_delete_file", {"inode_id": "in-1"}, raise_on_error=False)
    assert res.is_error is False
    payload = json.loads(res.content[0].text)  # type: ignore[union-attr]
    assert payload == {
        "status": "confirmation_required",
        "executed": False,
        "message": "This destructive operation was not executed. Re-call this tool with confirm=true to proceed.",
    }
    api.delete_file.assert_not_called()


def test_invalid_request_raises_redacted_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reverting the helper to return a string, or dropping its redaction, fails here."""
    monkeypatch.setenv("SIGMA_CLIENT_SECRET", _SECRET)
    with pytest.raises(ToolError) as exc_info:
        _invalid_request(f"bad value {_SECRET}")
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert exc_info.value.__suppress_context__ is True
    assert json.loads(str(exc_info.value)) == {
        "error": {"type": "invalid_request", "message": "bad value ***REDACTED***"}
    }


@pytest.mark.asyncio
async def test_sigma_tool_passes_tool_error_through_unchanged() -> None:
    """sigma_tool must not rewrap a validation ToolError as an internal error."""

    @sigma_tool
    async def handler() -> str:
        _invalid_request("x is required")

    with pytest.raises(ToolError) as exc_info:
        await handler()
    assert json.loads(str(exc_info.value))["error"] == {"type": "invalid_request", "message": "x is required"}


def test_confirm_required_is_not_an_error_payload() -> None:
    payload = json.loads(_confirm_required("Deleting the thing"))
    assert "error" not in payload
    assert payload["executed"] is False
    assert payload["message"] == "Deleting the thing was not executed. Re-call this tool with confirm=true to proceed."
