"""API credential secrets stay out of logs, error text and echoed results.

``create_api_credential`` and ``update_api_credential`` send a secret (password, bearer token,
API key value, OAuth client secret, AWS secret access key) to Sigma, which says it never
returns one. If an upstream error or result echoes the request anyway, the server masks it:
the calls go through the MCP protocol with every log record captured.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastmcp import Client

from sigma_mcp import server
from sigma_mcp.client import SigmaClient
from sigma_mcp.errors import MASK

BASE = "https://api.sigmacomputing.com"

# One credential per auth method in the spec's request schema; each secret is unique.
CREDENTIALS: dict[str, tuple[dict[str, Any], str]] = {
    "basic": ({"authMethod": "basic", "basic": {"username": "u", "password": "pw-S3cret-basic"}}, "pw-S3cret-basic"),
    "bearer": ({"authMethod": "bearer", "bearer": {"token": "tok-S3cret-bearer"}}, "tok-S3cret-bearer"),
    "apiKey": (
        {"authMethod": "apiKey", "apiKey": {"key": "X-Key", "value": "key-S3cret-value", "isQueryParam": False}},
        "key-S3cret-value",
    ),
    "oAuthClientCredentials": (
        {
            "authMethod": "oAuthClientCredentials",
            "oAuthClientCredentials": {
                "clientId": "cid",
                "clientSecret": "cs-S3cret-oauth",
                "accessTokenUrl": "https://auth.example.com/token",
                "scopes": [],
                "accessTokenAuthMethod": "httpBasic",
            },
        },
        "cs-S3cret-oauth",
    ),
    "awsSigV4": (
        {
            "authMethod": "awsSigV4",
            "awsSigV4": {"accessKeyId": "AKIA1", "secretAccessKey": "sak-S3cret-aws", "region": "us-east-1"},
        },
        "sak-S3cret-aws",
    ),
}

CALLS = [
    ("admin_create_api_credential", {}),
    ("admin_update_api_credential", {"credential_id": "cred-1"}),
]


@pytest.fixture
def http() -> Iterator[AsyncMock]:
    c = SigmaClient("cid", "csecret", BASE, max_retries=0)
    c._token = "tok"
    c._token_expiry = time.time() + 3600
    mock = AsyncMock()
    c._http = mock
    server._client = c
    yield mock
    server._client = None


async def _call(tool: str, args: dict[str, Any]) -> tuple[bool, str]:
    async with Client(server.mcp) as client:
        result = await client.call_tool(tool, args, raise_on_error=False)
    text = json.dumps([getattr(block, "text", "") for block in result.content])
    return result.is_error, text + json.dumps(result.structured_content)


@pytest.mark.parametrize("method", list(CREDENTIALS))
@pytest.mark.parametrize(("tool", "extra"), CALLS)
async def test_secret_is_masked_in_an_upstream_error_and_the_logs(
    http: AsyncMock, caplog: pytest.LogCaptureFixture, tool: str, extra: dict[str, Any], method: str
) -> None:
    credential, secret = CREDENTIALS[method]
    body = {"name": "n", "allowlist": ["*"], "credential": credential}
    http.request.return_value = httpx.Response(400, json={"message": "rejected", "request": body})
    with caplog.at_level(logging.DEBUG):
        is_error, text = await _call(tool, {**extra, "body": body})
    assert is_error
    assert secret not in text
    assert secret not in caplog.text
    # Sigma still received the secret: only what comes back is masked.
    assert http.request.call_args[1]["json"]["credential"] == credential


@pytest.mark.parametrize("method", list(CREDENTIALS))
@pytest.mark.parametrize(("tool", "extra"), CALLS)
async def test_secret_is_masked_in_an_echoed_result(
    http: AsyncMock, caplog: pytest.LogCaptureFixture, tool: str, extra: dict[str, Any], method: str
) -> None:
    credential, secret = CREDENTIALS[method]
    body = {"name": "n", "allowlist": ["*"], "credential": credential}
    echo = {"apiCredentialId": "cred-1", "credential": credential, "note": f"stored {secret}"}
    http.request.return_value = httpx.Response(200, json=echo)
    with caplog.at_level(logging.DEBUG):
        is_error, text = await _call(tool, {**extra, "body": body})
    assert not is_error
    assert secret not in text
    assert MASK in text
    assert "cred-1" in text
    assert secret not in caplog.text


def test_secrets_inside_lists_are_collected_longest_first() -> None:
    from sigma_mcp.tools.common import _credential_secrets, _mask_literals

    body = {"credential": {"extra": [{"value": "ab"}, {"password": "abcd"}, "plain", {"value": ""}]}}
    secrets = _credential_secrets(body)
    assert secrets == ["abcd", "ab"]
    assert _mask_literals({"x": ["abcd ab", 3]}, secrets) == {"x": [f"{MASK} {MASK}", 3]}
    assert _credential_secrets({"name": "n"}) == []
