"""The documented retry env vars reach the SigmaClient the server builds (#58).

``SIGMA_MAX_RETRIES``, ``SIGMA_BASE_DELAY``, ``SIGMA_MAX_RETRY_DELAY`` and ``SIGMA_MAX_RETRY_AFTER``
were declared in ``Settings`` but never passed to the client. Every value below differs from
its default (3, 1.0, 60.0, 300.0), so each one is shown to arrive.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from sigma_mcp import server
from sigma_mcp.client import SigmaClient

NON_DEFAULT = {
    "SIGMA_MAX_RETRIES": ("7", "max_retries", 7),
    "SIGMA_BASE_DELAY": ("0.25", "base_delay", 0.25),
    "SIGMA_MAX_RETRY_DELAY": ("12.5", "max_retry_delay", 12.5),
    "SIGMA_MAX_RETRY_AFTER": ("45", "max_retry_after", 45.0),
}
DEFAULTS = {"max_retries": 3, "base_delay": 1.0, "max_retry_delay": 60.0, "max_retry_after": 300.0}


@pytest.fixture
async def fresh_server(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    for name in NON_DEFAULT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SIGMA_CLIENT_ID", "cid")
    monkeypatch.setenv("SIGMA_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("SIGMA_API_BASE_URL", "https://aws-api.sigmacomputing.com")
    saved_client, saved_cache = server._client, dict(server._HEADER_CLIENT_CACHE)
    server._client = None
    server._HEADER_CLIENT_CACHE.clear()
    yield
    if server._client is not None:
        await server._client.aclose()
    for c in server._HEADER_CLIENT_CACHE.values():
        await c.aclose()
    server._client = saved_client
    server._HEADER_CLIENT_CACHE.clear()
    server._HEADER_CLIENT_CACHE.update(saved_cache)


def _retry_values(c: SigmaClient) -> dict[str, Any]:
    return {key: getattr(c, key) for key in DEFAULTS}


def _set_all(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    for env, (raw, _attr, _value) in NON_DEFAULT.items():
        monkeypatch.setenv(env, raw)
    expected = {attr: value for _raw, attr, value in NON_DEFAULT.values()}
    assert all(expected[k] != DEFAULTS[k] for k in DEFAULTS)
    return expected


async def test_env_vars_reach_the_env_credential_client(fresh_server: None, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _set_all(monkeypatch)
    c = await server.get_client()
    assert _retry_values(c) == expected


async def test_env_vars_reach_the_header_credential_client(fresh_server: None, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _set_all(monkeypatch)
    ctx = {"headers": {"x-sigma-client-id": "hdr-id", "x-sigma-client-secret": "hdr-secret"}}
    c = await server.get_client(ctx)
    assert _retry_values(c) == expected


async def test_env_vars_reach_tenant_clients(fresh_server: None, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _set_all(monkeypatch)
    c = await server.get_client()
    tenant = c._build_tenant_client("tenant-token", 3600, "org-1")
    assert _retry_values(tenant) == expected


@pytest.mark.parametrize("env", list(NON_DEFAULT))
async def test_each_env_var_alone_reaches_the_client(
    env: str, fresh_server: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw, attr, value = NON_DEFAULT[env]
    monkeypatch.setenv(env, raw)
    c = await server.get_client()
    assert _retry_values(c) == {**DEFAULTS, attr: value}


async def test_defaults_without_env(fresh_server: None) -> None:
    c = await server.get_client()
    assert _retry_values(c) == DEFAULTS


async def test_out_of_range_env_value_fails_loudly(fresh_server: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGMA_MAX_RETRIES", "11")
    with pytest.raises(ValueError, match="MAX_RETRIES"):
        await server.get_client()
