"""datasets_create_connection masks the write-only secrets it sent wherever Sigma echoes them.

POST /v2/connections ``details`` may carry ``password``, ``privateKey``,
``privateKeyPassphrase``, ``clientSecret``, ``token`` and ``pythonSecrets``; Sigma says they are
write-only. An echoed 200 result and an echoed 400 error are checked, with every log record.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from unittest.mock import AsyncMock

import httpx
import pytest
from fastmcp import Client

from sigma_mcp import server
from sigma_mcp.client import SigmaClient
from sigma_mcp.errors import MASK

SECRETS = {
    "password": {"type": "password", "value": "pw-S3cret-conn"},
    "privateKey": "pk-S3cret-conn",
    "privateKeyPassphrase": "pp-S3cret-conn",
    "clientSecret": "cs-S3cret-conn",
    "token": "tk-S3cret-conn",
    "pythonSecrets": {"setupUser": {"username": "setup", "password": "py-S3cret-conn", "role": "r"}},
}
LITERALS = ["pw-S3cret-conn", "pk-S3cret-conn", "pp-S3cret-conn", "cs-S3cret-conn", "tk-S3cret-conn", "py-S3cret-conn"]
BODY = {"name": "wh", "details": {"type": "snowflake", "account": "acct", "user": "svc", **SECRETS}}


@pytest.fixture
def http() -> Iterator[AsyncMock]:
    c = SigmaClient("cid", "csecret", "https://api.sigmacomputing.com", max_retries=0)
    c._token = "tok"
    c._token_expiry = time.time() + 3600
    mock = AsyncMock()
    c._http = mock
    server._client = c
    yield mock
    server._client = None


async def _call(body: dict[str, object]) -> tuple[bool, str]:
    async with Client(server.mcp) as client:
        result = await client.call_tool("datasets_create_connection", {"body": body}, raise_on_error=False)
    return result.is_error, json.dumps([getattr(b, "text", "") for b in result.content])


@pytest.mark.parametrize("field", list(SECRETS))
async def test_each_secret_is_masked_in_an_echoed_result(
    http: AsyncMock, caplog: pytest.LogCaptureFixture, field: str
) -> None:
    echo = {"connectionId": "conn-1", "name": "wh", "details": BODY["details"], "note": "saved pk-S3cret-conn"}
    http.request.return_value = httpx.Response(200, json=echo)
    with caplog.at_level(logging.DEBUG):
        is_error, text = await _call(BODY)
    assert not is_error
    for secret in LITERALS:
        assert secret not in text
        assert secret not in caplog.text
    out = json.loads(json.loads(text)[0])
    assert out["details"][field] == MASK
    assert out["details"]["account"] == "acct"
    assert out["connectionId"] == "conn-1"
    # Sigma still received every secret.
    assert http.request.call_args[1]["json"] == BODY


async def test_secrets_are_masked_in_an_echoed_error(http: AsyncMock, caplog: pytest.LogCaptureFixture) -> None:
    http.request.return_value = httpx.Response(400, json={"message": "rejected", "request": BODY})
    with caplog.at_level(logging.DEBUG):
        is_error, text = await _call(BODY)
    assert is_error
    for secret in LITERALS:
        assert secret not in text
        assert secret not in caplog.text


async def test_shared_redactor_masks_passphrase_and_python_secrets_without_the_tool() -> None:
    # The key-based redaction alone (an error from any tool) masks the two fields it lacked.
    from sigma_mcp.errors import SigmaAPIError

    detail = {"details": {"privateKeyPassphrase": "pp-S3cret-conn", "pythonSecrets": SECRETS["pythonSecrets"]}}
    out = SigmaAPIError(400, "/v2/connections", "POST", detail=detail).to_dict()["detail"]
    assert out == {"details": {"privateKeyPassphrase": MASK, "pythonSecrets": MASK}}
