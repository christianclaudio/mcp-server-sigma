"""Retry-After handling on 429 retries in ``SigmaClient._request``.

Only GET, HEAD, and OPTIONS are retried. A delta-seconds or HTTP-date value at or
under ``max_retry_after`` (default 120s) is waited exactly. A longer value, or a
header that cannot be parsed, fails at once. 5xx responses are not retried.
``asyncio.sleep`` is faked, so nothing here really sleeps.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

import sigma_mcp.client as client_mod
from sigma_mcp.client import SigmaClient
from sigma_mcp.errors import SigmaAPIError

BASE = "https://api.sigmacomputing.com"


def _client(http: httpx.AsyncClient, **kwargs: Any) -> SigmaClient:
    client = SigmaClient("cid", "csecret-32-bytes-long-for-tests!!", BASE, http_client=http, **kwargs)
    client._token = "cached-token"
    client._token_expiry = time.time() + 3600
    return client


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every retry wait instead of sleeping, with the jitter fixed at its high end."""
    recorded: list[float] = []

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(client_mod.random, "uniform", lambda a, b: b)
    return recorded


async def _run(
    statuses: list[tuple[int, dict[str, str]]],
    *,
    method: str = "GET",
    max_retries: int = 3,
    max_retry_after: float | None = None,
) -> tuple[Any, int]:
    """Serve the given (status, headers) responses in order, then 200."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= len(statuses):
            status, headers = statuses[calls - 1]
            return httpx.Response(status, headers=headers, text="busy")
        return httpx.Response(200, json={"ok": True})

    kwargs: dict[str, Any] = {"max_retries": max_retries}
    if max_retry_after is not None:
        kwargs["max_retry_after"] = max_retry_after
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE) as http:
        client = _client(http, **kwargs)
        result = await client._request(method, "/v2/retry")
    return result, calls


@pytest.mark.asyncio
async def test_retry_after_seconds_is_waited_exactly(sleeps: list[float]) -> None:
    result, calls = await _run([(429, {"Retry-After": "7"})])
    assert result.status_code == 200
    assert calls == 2
    assert sleeps == [7.0]


@pytest.mark.asyncio
async def test_short_retry_after_is_waited_exactly(sleeps: list[float]) -> None:
    """A header shorter than the backoff is still honored exactly, with no jitter."""
    result, _calls = await _run([(429, {"Retry-After": "0"}), (429, {"Retry-After": "1"}), (429, {"Retry-After": "2"})])
    assert result.status_code == 200
    assert sleeps == [0.0, 1.0, 2.0]


@pytest.mark.asyncio
async def test_retry_after_http_date(sleeps: list[float]) -> None:
    when = datetime.now(UTC) + timedelta(seconds=20)
    await _run([(429, {"Retry-After": format_datetime(when, usegmt=True)})])
    assert len(sleeps) == 1
    assert 17.0 <= sleeps[0] <= 20.0


@pytest.mark.asyncio
async def test_retry_after_past_http_date_waits_zero(sleeps: list[float]) -> None:
    when = datetime.now(UTC) - timedelta(hours=1)
    await _run([(429, {"Retry-After": format_datetime(when, usegmt=True)})])
    assert sleeps == [0.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["soon", "-5", "1.5", "+3", "", "Wed, 99 Foo 2026"])
async def test_unparseable_retry_after_fails_immediately(sleeps: list[float], value: str) -> None:
    """A Retry-After that cannot be parsed is hostile and is not waited out."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": value}, text="busy")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE) as http:
        client = _client(http, max_retries=3)
        with pytest.raises(SigmaAPIError) as exc_info:
            await client._request("GET", "/v2/retry")
    assert exc_info.value.status_code == 429
    assert "cannot be parsed" in str(exc_info.value.detail)
    assert calls == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_missing_retry_after_uses_backoff(sleeps: list[float]) -> None:
    await _run([(429, {})])
    # base_delay 1 * 2**0 * jitter 1.5
    assert sleeps == [1.5]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["100000", "9" * 400, "121"], ids=["large", "400-digit", "just-over"])
async def test_retry_after_over_cap_fails_immediately(sleeps: list[float], value: str) -> None:
    """A Retry-After above 120 seconds is not shortened; the call fails at once."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": value}, text="busy")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE) as http:
        client = _client(http, max_retries=3)
        with pytest.raises(SigmaAPIError) as exc_info:
            await client._request("GET", "/v2/retry")
    assert exc_info.value.status_code == 429
    assert "max_retry_after ceiling" in str(exc_info.value.detail)
    assert calls == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_retry_after_at_cap_is_honored(sleeps: list[float]) -> None:
    """120 seconds is waited out in full."""
    result, calls = await _run([(429, {"Retry-After": "120"})])
    assert result.status_code == 200
    assert calls == 2
    assert sleeps == [120.0]


@pytest.mark.asyncio
async def test_retry_after_http_date_over_cap_fails_immediately(sleeps: list[float]) -> None:
    when = datetime.now(UTC) + timedelta(days=365)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": format_datetime(when, usegmt=True)}, text="busy")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE) as http:
        client = _client(http, max_retries=3)
        with pytest.raises(SigmaAPIError) as exc_info:
            await client._request("GET", "/v2/retry")
    assert "max_retry_after ceiling" in str(exc_info.value.detail)
    assert calls == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_retry_count_unchanged_with_retry_after(sleeps: list[float]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "8"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE) as http:
        client = _client(http, max_retries=2)
        with pytest.raises(SigmaAPIError):
            await client._request("GET", "/v2/retry")
    assert calls == 3
    assert sleeps == [8.0, 8.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_non_idempotent_429_is_not_retried(sleeps: list[float], method: str) -> None:
    """A 429 on a non-idempotent method fails on the first response."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "1"}, text="no")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE) as http:
        client = _client(http, max_retries=2)
        with pytest.raises(SigmaAPIError) as exc_info:
            await client._request(method, "/v2/retry")
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == f"Rate limit exceeded; {method} is not retried"
    assert calls == 1
    assert sleeps == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["HEAD", "OPTIONS"])
async def test_idempotent_methods_other_than_get_are_retried(sleeps: list[float], method: str) -> None:
    result, calls = await _run([(429, {"Retry-After": "1"})], method=method)
    assert result.status_code == 200
    assert calls == 2
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_503_is_not_retried(sleeps: list[float]) -> None:
    """5xx is raised once. A Retry-After on it does not schedule a retry."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, headers={"Retry-After": "9"}, text="down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE) as http:
        client = _client(http, max_retries=3)
        with pytest.raises(SigmaAPIError) as exc_info:
            await client._request("GET", "/v2/retry")
    assert exc_info.value.status_code == 503
    assert calls == 1
    assert sleeps == []


def test_retry_after_parser_naive_date_is_utc() -> None:
    # "-0000" makes parsedate_to_datetime return a naive datetime; it is read as UTC.
    seconds = SigmaClient._parse_retry_after("Thu, 01 Jan 2099 00:00:00 -0000")
    assert seconds is not None and seconds > 0


def test_retry_after_parser_absent() -> None:
    assert SigmaClient._parse_retry_after(None) is None


def test_retry_after_parser_rejects_non_ascii_digits() -> None:
    # A superscript two passes str.isdigit() but is not delta-seconds.
    assert SigmaClient._parse_retry_after("\u00b2") is None
