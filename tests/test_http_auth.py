"""SIGMA_MCP_AUTH_TOKEN is enforced on HTTP transports through FastMCP auth (#39)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

import sigma_mcp.server as srv
from sigma_mcp.auth import SharedTokenVerifier

TOKEN = "correct-horse-battery-staple-39"
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2026-07-28",
    "Mcp-Method": "server/discover",
}
DISCOVER = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "server/discover",
    "params": {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    },
}


def _protected_server() -> Any:
    server = srv.create_server()
    server.auth = SharedTokenVerifier(TOKEN)
    return server


async def _client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            yield client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auth_header", "expected"),
    [(None, 401), ("Bearer wrong-token", 401), ("Basic Zm9vOmJhcg==", 401), (f"Bearer {TOKEN}", 200)],
)
async def test_streamable_http_enforces_token(auth_header: str | None, expected: int) -> None:
    app = _protected_server().streamable_http_app(stateless_http=True, json_response=True)
    headers = dict(HEADERS)
    if auth_header is not None:
        headers["Authorization"] = auth_header
    async for client in _client(app):
        res = await client.post("/mcp", json=DISCOVER, headers=headers)
        assert res.status_code == expected
        assert TOKEN not in res.text
        if expected == 200:
            assert "supportedVersions" in res.json()["result"]
        else:
            assert res.headers["www-authenticate"].lower().startswith("bearer")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auth_header", "expected"),
    # A request that passes auth reaches the SSE message handler, which rejects the missing session (400).
    [(None, 401), ("Bearer wrong-token", 401), (f"Bearer {TOKEN}", 400)],
)
async def test_sse_enforces_token(auth_header: str | None, expected: int) -> None:
    app = _protected_server().http_app(transport="sse", host_origin_protection=False)
    headers = {"Content-Type": "application/json"}
    if auth_header is not None:
        headers["Authorization"] = auth_header
    async for client in _client(app):
        res = await client.post("/messages/", json=DISCOVER, headers=headers)
        assert res.status_code == expected
        assert TOKEN not in res.text
        if auth_header is None or auth_header != f"Bearer {TOKEN}":
            unauth = await client.get("/sse", headers=headers)
            assert unauth.status_code == 401


@pytest.mark.asyncio
async def test_verifier_constant_time_and_redacted() -> None:
    verifier = SharedTokenVerifier(TOKEN)
    assert await verifier.verify_token("nope") is None
    assert await verifier.verify_token(TOKEN[:-1]) is None
    ok = await verifier.verify_token(TOKEN)
    assert ok is not None and ok.client_id == "sigma-mcp-shared-token"
    assert TOKEN not in repr(verifier)
    with pytest.raises(ValueError, match="must be non-empty") as exc:
        SharedTokenVerifier("")
    with pytest.raises(ValueError, match="must be non-empty"):
        SharedTokenVerifier(" \t\n ")
    assert TOKEN not in str(exc.value)


def _run_main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> Any:
    server = srv.create_server()
    monkeypatch.setattr(srv, "mcp", server)
    monkeypatch.setattr(server, "run", lambda **kwargs: None)
    monkeypatch.setattr("sys.argv", ["sigma-mcp", *argv])
    srv.main()
    return server


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_main_wires_auth_on_http(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, transport: str
) -> None:
    monkeypatch.setenv("SIGMA_MCP_AUTH_TOKEN", TOKEN)
    with caplog.at_level(logging.DEBUG):
        server = _run_main(monkeypatch, ["--transport", transport])
    assert isinstance(server.auth, SharedTokenVerifier)
    assert f"Bearer token authentication is on for the {transport} transport" in caplog.text
    assert TOKEN not in caplog.text


def test_main_http_without_token_warns(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.delenv("SIGMA_MCP_AUTH_TOKEN", raising=False)
    with caplog.at_level(logging.WARNING):
        server = _run_main(monkeypatch, ["--transport", "streamable-http"])
    assert server.auth is None
    assert "SIGMA_MCP_AUTH_TOKEN is not set" in caplog.text


def test_main_http_whitespace_token_is_unset(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("SIGMA_MCP_AUTH_TOKEN", "   ")
    with caplog.at_level(logging.WARNING):
        server = _run_main(monkeypatch, ["--transport", "streamable-http"])
    assert server.auth is None
    assert "SIGMA_MCP_AUTH_TOKEN is not set" in caplog.text


def test_main_stdio_ignores_token(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("SIGMA_MCP_AUTH_TOKEN", TOKEN)
    with caplog.at_level(logging.DEBUG):
        server = _run_main(monkeypatch, [])
    assert server.auth is None
    assert "SIGMA_MCP_AUTH_TOKEN" not in caplog.text
    assert "authentication" not in caplog.text.lower()
