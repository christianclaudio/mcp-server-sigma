"""Retry delays must be finite (#58).

``SIGMA_BASE_DELAY``, ``SIGMA_MAX_RETRY_DELAY`` and ``SIGMA_MAX_RETRY_AFTER`` reject ``inf`` and
``nan`` at startup: an infinite ceiling would let a ``Retry-After: inf`` (or ``1e400``, which
parses to infinity) through, and the client would sleep forever. With a finite ceiling such a
header fails fast, and the fail-fast message renders waits compactly. ``asyncio.sleep`` is faked,
so nothing here really sleeps.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

import sigma_mcp.client as client_mod
from sigma_mcp.client import SigmaClient
from sigma_mcp.config import Settings
from sigma_mcp.errors import SigmaAPIError

BASE = "https://api.sigmacomputing.com"
DELAY_FIELDS = ("BASE_DELAY", "MAX_RETRY_DELAY", "MAX_RETRY_AFTER")


def _client(**kwargs: float) -> SigmaClient:
    c = SigmaClient("cid", "csecret-32-bytes-long-for-tests!!", BASE, **kwargs)  # type: ignore[arg-type]
    c._token = "cached-token"
    c._token_expiry = time.time() + 3600
    return c


@pytest.fixture
def fake_sleep(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    sleep = AsyncMock()
    monkeypatch.setattr(client_mod.asyncio, "sleep", sleep)
    return sleep


@pytest.mark.parametrize("raw", ["inf", "nan", "-inf", "Infinity"])
@pytest.mark.parametrize("field", DELAY_FIELDS)
def test_non_finite_delay_setting_is_rejected(field: str, raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(f"SIGMA_{field}", raw)
    with pytest.raises(ValidationError, match=field):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("field", DELAY_FIELDS)
def test_finite_delay_setting_is_still_accepted(field: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(f"SIGMA_{field}", "12.5")
    assert getattr(Settings(_env_file=None), field) == 12.5  # type: ignore[call-arg]


@pytest.mark.parametrize("header", ["inf", "1e400"])
async def test_infinite_retry_after_fails_fast_under_a_finite_ceiling(header: str, fake_sleep: AsyncMock) -> None:
    c = _client(max_retries=3, max_retry_after=300.0)
    request = AsyncMock(side_effect=[httpx.Response(429, headers={"Retry-After": header}), httpx.Response(200)])
    c._http.request = request  # type: ignore[method-assign]
    with pytest.raises(SigmaAPIError) as exc_info:
        await c._request("GET", "/v2/workbooks")
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == (
        "Rate limit exceeded: the server asked to retry after infs, which is above the "
        "300s max_retry_after ceiling; not retrying"
    )
    fake_sleep.assert_not_awaited()
    assert request.await_count == 1


async def test_huge_retry_after_is_rendered_compactly(fake_sleep: AsyncMock) -> None:
    c = _client(max_retries=3, max_retry_after=0.25)
    request = AsyncMock(side_effect=[httpx.Response(429, headers={"Retry-After": "1e308"}), httpx.Response(200)])
    c._http.request = request  # type: ignore[method-assign]
    with pytest.raises(SigmaAPIError) as exc_info:
        await c._request("GET", "/v2/workbooks")
    assert exc_info.value.detail == (
        "Rate limit exceeded: the server asked to retry after 1e+308s, which is above the "
        "0.25s max_retry_after ceiling; not retrying"
    )
    fake_sleep.assert_not_awaited()
    assert request.await_count == 1
