"""Tool-path redaction: a number under a credential key is masked in what a client receives.

Every tool goes through ``sigma_tool`` / ``_tool_failure`` in ``tools/common.py``. Those call
``redact_message``, which parses JSON in the message and masks credential keys by value, so
``{"password": 12345}`` comes back as ``{"password": "***REDACTED***"}``. The text redactor alone
only matches quoted or ``key=`` values and would leave the number in the clear.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client

import sigma_mcp.server as server
from sigma_mcp.errors import SigmaAPIError
from sigma_mcp.tools.common import _tool_failure

MASK = "***REDACTED***"


async def _call_failing(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> dict[str, Any]:
    class _FailingClient:
        async def list_files(self, params: Any) -> Any:
            raise exc

    async def get_client(ctx: Any = None) -> Any:
        return _FailingClient()

    monkeypatch.setattr(server, "get_client", get_client)
    async with Client(server.create_server(profile="full")) as client:
        tool = next(t.name for t in await client.list_tools() if t.name.endswith("list_files"))
        result = await client.call_tool(tool, {}, raise_on_error=False)
    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    payload: dict[str, Any] = json.loads(text)
    return payload


@pytest.mark.asyncio
async def test_internal_error_masks_a_number_under_a_credential_key(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = await _call_failing(monkeypatch, RuntimeError('bad config {"password": 12345}'))
    assert "12345" not in json.dumps(payload)
    assert payload["error"]["type"] == "internal"
    assert payload["error"]["message"] == f'bad config {{"password": "{MASK}"}}'


@pytest.mark.asyncio
async def test_upstream_401_body_masks_a_number_under_a_credential_key(monkeypatch: pytest.MonkeyPatch) -> None:
    exc = SigmaAPIError(401, "/v2/files", "GET", detail='{"password": 98765}')
    payload = await _call_failing(monkeypatch, exc)
    assert "98765" not in json.dumps(payload)
    assert json.loads(payload["error"]["detail"]) == {"password": MASK}


@pytest.mark.parametrize(
    ("detail", "secret"),
    [('{"client_secret": 4242}', "4242"), ('{"token": 777}', "777")],
    ids=["client_secret", "token"],
)
def test_tool_failure_details_mask_a_number_under_a_credential_key(detail: str, secret: str) -> None:
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError) as exc_info:
        _tool_failure("batch_failed", "all items failed", items=[{"id": "a", "error": detail}])
    payload = json.loads(str(exc_info.value))
    assert secret not in json.dumps(payload)
    assert MASK in payload["error"]["items"][0]["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "secret"),
    [('{"client_secret": 4242}', "4242"), ('{"token": 777}', "777")],
    ids=["client_secret", "token"],
)
async def test_tool_failure_message_masks_via_a_client_call(
    monkeypatch: pytest.MonkeyPatch, message: str, secret: str
) -> None:
    from fastmcp.exceptions import ToolError

    try:
        _tool_failure("internal", message)
    except ToolError as exc:
        raised = exc
    payload = await _call_failing(monkeypatch, raised)
    assert secret not in json.dumps(payload)
