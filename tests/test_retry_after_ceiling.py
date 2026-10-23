"""429 Retry-After handling (#58): the server's wait is honored exactly, up to ``max_retry_after``.

A ``Retry-After`` wait is not jittered and not cut to ``max_retry_delay``; one above the
``max_retry_after`` ceiling (default 300s) fails at once with a ``SigmaAPIError`` that names the
requested wait and the ceiling. The exponential backoff without the header keeps its jitter and
its 60s ``max_retry_delay`` cap. ``asyncio.sleep``, ``random.uniform`` and the clock are faked, so
nothing here really sleeps.
"""

from __future__ import annotations

import datetime as real_datetime
import email.utils
import time
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import sigma_mcp.client as client_mod
from sigma_mcp.client import SigmaClient
from sigma_mcp.config import Settings
from sigma_mcp.errors import SigmaAPIError

BASE = "https://api.sigmacomputing.com"
FIXED_NOW = real_datetime.datetime(2026, 10, 23, 12, 0, 0, tzinfo=real_datetime.timezone.utc)


def _client(**kwargs: Any) -> SigmaClient:
    c = SigmaClient("cid", "csecret-32-bytes-long-for-tests!!", BASE, **kwargs)
    c._token = "cached-token"
    c._token_expiry = time.time() + 3600
    return c


def _r429(retry_after: str | None = None) -> httpx.Response:
    headers = {"x-request-id": "req-429"}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return httpx.Response(429, headers=headers)


def _r200() -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


@pytest.fixture
def fake_sleep(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    sleep = AsyncMock()
    monkeypatch.setattr(client_mod.asyncio, "sleep", sleep)
    return sleep


@pytest.fixture
def fake_uniform(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Jitter always at its top end (1.5 for backoff, 1.3 for the old Retry-After jitter)."""
    uniform = MagicMock(side_effect=lambda lo, hi: hi)
    monkeypatch.setattr(client_mod.random, "uniform", uniform)
    return uniform


@pytest.fixture
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FixedDatetime(real_datetime.datetime):
        @classmethod
        def now(cls, tz: real_datetime.tzinfo | None = None) -> _FixedDatetime:
            return cls.fromtimestamp(FIXED_NOW.timestamp(), tz)

    fake_module = types.SimpleNamespace(datetime=_FixedDatetime, timezone=real_datetime.timezone)
    monkeypatch.setattr(client_mod, "datetime", fake_module)


def _http_date(seconds_from_now: int) -> str:
    return email.utils.format_datetime(FIXED_NOW + real_datetime.timedelta(seconds=seconds_from_now), usegmt=True)


def _waits(sleep: AsyncMock) -> list[float]:
    return [call.args[0] for call in sleep.await_args_list]


async def test_retry_after_above_60s_is_waited_exactly(fake_sleep: AsyncMock, fake_uniform: MagicMock) -> None:
    c = _client(max_retries=3)
    request = AsyncMock(side_effect=[_r429("120"), _r200()])
    c._http.request = request  # type: ignore[method-assign]
    r = await c._request("GET", "/v2/workbooks")
    assert r.status_code == 200
    assert request.await_count == 2
    assert _waits(fake_sleep) == [120.0]
    fake_uniform.assert_not_called()


async def test_retry_after_is_not_jittered(fake_sleep: AsyncMock, fake_uniform: MagicMock) -> None:
    c = _client(max_retries=3)
    c._http.request = AsyncMock(side_effect=[_r429("2"), _r429("7.5"), _r200()])  # type: ignore[method-assign]
    await c._request("GET", "/v2/workbooks")
    assert _waits(fake_sleep) == [2.0, 7.5]
    fake_uniform.assert_not_called()


async def test_retry_after_at_the_default_ceiling_is_waited(fake_sleep: AsyncMock) -> None:
    c = _client(max_retries=1)
    assert c.max_retry_after == 300.0
    c._http.request = AsyncMock(side_effect=[_r429("300"), _r200()])  # type: ignore[method-assign]
    await c._request("GET", "/v2/workbooks")
    assert _waits(fake_sleep) == [300.0]


async def test_retry_after_above_the_ceiling_fails_fast(fake_sleep: AsyncMock) -> None:
    c = _client(max_retries=3)
    request = AsyncMock(side_effect=[_r429("301"), _r200()])
    c._http.request = request  # type: ignore[method-assign]
    with pytest.raises(SigmaAPIError) as exc_info:
        await c._request("GET", "/v2/workbooks")
    err = exc_info.value
    assert err.status_code == 429
    assert err.request_id == "req-429"
    assert isinstance(err.detail, str)
    assert "301s" in err.detail
    assert "300s max_retry_after" in err.detail
    assert "not retrying" in err.detail
    assert err.to_dict()["detail"] == err.detail
    fake_sleep.assert_not_awaited()
    assert request.await_count == 1


async def test_custom_ceiling(fake_sleep: AsyncMock) -> None:
    c = _client(max_retries=3, max_retry_after=30.0)
    c._http.request = AsyncMock(side_effect=[_r429("30"), _r200()])  # type: ignore[method-assign]
    await c._request("GET", "/v2/workbooks")
    assert _waits(fake_sleep) == [30.0]

    fake_sleep.reset_mock()
    request = AsyncMock(side_effect=[_r429("45"), _r200()])
    c._http.request = request  # type: ignore[method-assign]
    with pytest.raises(SigmaAPIError) as exc_info:
        await c._request("GET", "/v2/workbooks")
    assert "45s" in exc_info.value.detail
    assert "30s max_retry_after" in exc_info.value.detail
    fake_sleep.assert_not_awaited()
    assert request.await_count == 1


async def test_custom_ceiling_above_default_allows_longer_waits(fake_sleep: AsyncMock) -> None:
    c = _client(max_retries=1, max_retry_after=900.0)
    c._http.request = AsyncMock(side_effect=[_r429("600"), _r200()])  # type: ignore[method-assign]
    await c._request("GET", "/v2/workbooks")
    assert _waits(fake_sleep) == [600.0]


def test_tenant_client_inherits_the_ceiling() -> None:
    parent = _client(max_retry_after=42.0)
    tenant = parent._build_tenant_client("tenant-token", 3600, "org-1")
    assert tenant.max_retry_after == 42.0


async def test_http_date_retry_after_is_waited_exactly(
    fake_sleep: AsyncMock, fake_uniform: MagicMock, fixed_clock: None
) -> None:
    c = _client(max_retries=1)
    c._http.request = AsyncMock(side_effect=[_r429(_http_date(120)), _r200()])  # type: ignore[method-assign]
    await c._request("GET", "/v2/workbooks")
    assert _waits(fake_sleep) == [120.0]
    fake_uniform.assert_not_called()


async def test_http_date_retry_after_above_the_ceiling_fails_fast(fake_sleep: AsyncMock, fixed_clock: None) -> None:
    c = _client(max_retries=3)
    request = AsyncMock(side_effect=[_r429(_http_date(400)), _r200()])
    c._http.request = request  # type: ignore[method-assign]
    with pytest.raises(SigmaAPIError) as exc_info:
        await c._request("GET", "/v2/workbooks")
    assert "400s" in exc_info.value.detail
    fake_sleep.assert_not_awaited()
    assert request.await_count == 1


async def test_backoff_without_retry_after_keeps_jitter_and_60s_cap(
    fake_sleep: AsyncMock, fake_uniform: MagicMock
) -> None:
    c = _client(max_retries=3, base_delay=25.0)
    c._http.request = AsyncMock(side_effect=[_r429(), _r429(), _r429(), _r200()])  # type: ignore[method-assign]
    await c._request("GET", "/v2/workbooks")
    # 25*1*1.5=37.5, then 25*2*1.5=75 -> 60, then 25*4*1.5=150 -> 60
    assert _waits(fake_sleep) == [37.5, 60.0, 60.0]
    assert [call.args for call in fake_uniform.call_args_list] == [(0.5, 1.5)] * 3


async def test_backoff_jitter_low_end_and_exhaustion(fake_sleep: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod.random, "uniform", lambda lo, hi: lo)
    c = _client(max_retries=2, base_delay=1.0)
    c._http.request = AsyncMock(return_value=_r429())  # type: ignore[method-assign]
    with pytest.raises(SigmaAPIError) as exc_info:
        await c._request("GET", "/v2/workbooks")
    assert exc_info.value.detail == "Rate limit exceeded after max retries"
    assert _waits(fake_sleep) == [0.5, 1.0]


async def test_max_retry_delay_does_not_cap_retry_after(fake_sleep: AsyncMock) -> None:
    c = _client(max_retries=1, max_retry_delay=5.0)
    c._http.request = AsyncMock(side_effect=[_r429("90"), _r200()])  # type: ignore[method-assign]
    await c._request("GET", "/v2/workbooks")
    assert _waits(fake_sleep) == [90.0]


def test_settings_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIGMA_MAX_RETRY_AFTER", raising=False)
    assert Settings(_env_file=None).MAX_RETRY_AFTER == 300.0  # type: ignore[call-arg]
    monkeypatch.setenv("SIGMA_MAX_RETRY_AFTER", "120")
    assert Settings(_env_file=None).MAX_RETRY_AFTER == 120.0  # type: ignore[call-arg]
    assert Settings(_env_file=None).MAX_RETRY_DELAY == 60.0  # type: ignore[call-arg]


# ─── Edge cases ───────────────────────────────────────────────────────────────


async def test_http_date_in_the_past_waits_zero_not_negative(
    fake_sleep: AsyncMock, fake_uniform: MagicMock, fixed_clock: None
) -> None:
    c = _client(max_retries=1)
    c._http.request = AsyncMock(side_effect=[_r429(_http_date(-90)), _r200()])  # type: ignore[method-assign]
    r = await c._request("GET", "/v2/workbooks")
    assert r.status_code == 200
    assert _waits(fake_sleep) == [0.0]
    fake_uniform.assert_not_called()


@pytest.mark.parametrize("header", ["soon", "-5", "-0.5", "nan", "Mon, 99 Foo 2026 25:61:00 GMT"])
async def test_unusable_retry_after_falls_back_to_jittered_capped_backoff(
    header: str, fake_sleep: AsyncMock, fake_uniform: MagicMock
) -> None:
    c = _client(max_retries=2, base_delay=50.0)
    c._http.request = AsyncMock(side_effect=[_r429(header), _r429(header), _r200()])  # type: ignore[method-assign]
    r = await c._request("GET", "/v2/workbooks")
    assert r.status_code == 200
    # Our backoff: 50*1*1.5 = 75 -> 60 cap, then 50*2*1.5 = 150 -> 60 cap; never negative.
    assert _waits(fake_sleep) == [60.0, 60.0]
    assert [call.args for call in fake_uniform.call_args_list] == [(0.5, 1.5)] * 2


@pytest.mark.parametrize("header", ["-5", "nan", "soon"])
def test_parse_retry_after_rejects_negative_and_nan(header: str) -> None:
    assert SigmaClient._parse_retry_after(header) is None


@pytest.mark.parametrize(("ceiling", "header"), [(300.0, "300"), (300.0, "300.0"), (30.0, "30"), (0.0, "0")])
async def test_wait_exactly_at_the_ceiling_is_allowed(ceiling: float, header: str, fake_sleep: AsyncMock) -> None:
    c = _client(max_retries=1, max_retry_after=ceiling)
    c._http.request = AsyncMock(side_effect=[_r429(header), _r200()])  # type: ignore[method-assign]
    await c._request("GET", "/v2/workbooks")
    assert _waits(fake_sleep) == [ceiling]


@pytest.mark.parametrize(("ceiling", "header"), [(300.0, "300.001"), (30.0, "30.5"), (0.0, "0.001"), (300.0, "inf")])
async def test_wait_just_above_the_ceiling_fails_fast(ceiling: float, header: str, fake_sleep: AsyncMock) -> None:
    c = _client(max_retries=3, max_retry_after=ceiling)
    request = AsyncMock(side_effect=[_r429(header), _r200()])
    c._http.request = request  # type: ignore[method-assign]
    with pytest.raises(SigmaAPIError) as exc_info:
        await c._request("GET", "/v2/workbooks")
    assert "max_retry_after ceiling" in exc_info.value.detail
    fake_sleep.assert_not_awaited()
    assert request.await_count == 1


async def test_http_date_exactly_at_the_ceiling_is_allowed(fake_sleep: AsyncMock, fixed_clock: None) -> None:
    c = _client(max_retries=1)
    c._http.request = AsyncMock(side_effect=[_r429(_http_date(300)), _r200()])  # type: ignore[method-assign]
    await c._request("GET", "/v2/workbooks")
    assert _waits(fake_sleep) == [300.0]
