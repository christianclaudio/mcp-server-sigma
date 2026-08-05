"""Tests for SigmaClient internals: token caching, retry, errors, tenant JWT."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest

from sigma_mcp.client import SigmaClient
from sigma_mcp.errors import SigmaAPIError

TEST_CLIENT_SECRET = "test-client-secret-32-bytes-long!"


def _make_client(max_retries: int = 3, base_delay: float = 0.001) -> SigmaClient:
    c = SigmaClient(
        "test-client-id",
        TEST_CLIENT_SECRET,
        "https://api.example.com",
        max_retries=max_retries,
        base_delay=base_delay,
    )
    return c


def _preauth_client(max_retries: int = 3) -> SigmaClient:
    c = _make_client(max_retries=max_retries)
    c._token = "cached-token"
    c._token_expiry = time.time() + 3600
    return c


# ─── Token caching ────────────────────────────────────────────────────────────


class TestTokenCaching:
    async def test_reuses_token_until_near_expiry(self) -> None:
        c = _make_client()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"access_token": "tok1", "expires_in": 3600}
        resp.raise_for_status = MagicMock()
        mock_post = AsyncMock(return_value=resp)
        with patch.object(c._http, "post", mock_post):
            t1 = await c._get_token()
            t2 = await c._get_token()
        assert t1 == "tok1"
        assert t2 == "tok1"
        assert mock_post.call_count == 1

    async def test_refreshes_when_expired(self) -> None:
        c = _make_client()
        c._token = "old-token"
        c._token_expiry = time.time() - 10  # already expired

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"access_token": "new-token", "expires_in": 3600}
        resp.raise_for_status = MagicMock()
        mock_post = AsyncMock(return_value=resp)
        with patch.object(c._http, "post", mock_post):
            t = await c._get_token()
        assert t == "new-token"
        assert mock_post.call_count == 1


# ─── 429 Retry ────────────────────────────────────────────────────────────────


class TestRetry429:
    async def test_honors_retry_after_header(self) -> None:
        c = _preauth_client(max_retries=2)
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "0.001"}

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.headers = {}
        resp_200.json.return_value = {"ok": True}

        mock_request = AsyncMock(side_effect=[resp_429, resp_200])
        with patch.object(c._http, "request", mock_request):
            r = await c._request("GET", "/v2/test")
        assert r.status_code == 200
        assert mock_request.call_count == 2

    async def test_raises_after_max_retries(self) -> None:
        c = _preauth_client(max_retries=2)
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {}

        mock_request = AsyncMock(return_value=resp_429)
        with patch.object(c._http, "request", mock_request):
            with pytest.raises(SigmaAPIError) as exc_info:
                await c._request("GET", "/v2/test")
        assert exc_info.value.status_code == 429
        # 1 initial + max_retries retries
        assert mock_request.call_count == 3


# ─── Non-429 errors ───────────────────────────────────────────────────────────


class TestNon429Errors:
    async def test_4xx_raises_with_attrs(self) -> None:
        c = _preauth_client()
        resp = MagicMock()
        resp.status_code = 403
        resp.headers = {"x-request-id": "req-123"}
        resp.json.return_value = {"message": "forbidden"}

        mock_request = AsyncMock(return_value=resp)
        with patch.object(c._http, "request", mock_request):
            with pytest.raises(SigmaAPIError) as exc_info:
                await c._request("POST", "/v2/workbooks")
        err = exc_info.value
        assert err.status_code == 403
        assert err.method == "POST"
        assert err.path == "/v2/workbooks"
        assert err.detail == {"message": "forbidden"}
        assert err.request_id == "req-123"

    async def test_5xx_raises(self) -> None:
        c = _preauth_client()
        resp = MagicMock()
        resp.status_code = 500
        resp.headers = {}
        resp.json.side_effect = Exception("not json")
        resp.text = "Internal Server Error"

        mock_request = AsyncMock(return_value=resp)
        with patch.object(c._http, "request", mock_request):
            with pytest.raises(SigmaAPIError) as exc_info:
                await c._request("GET", "/v2/health")
        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Internal Server Error"


# ─── Tenant JWT ───────────────────────────────────────────────────────────────


class TestTenantJWT:
    async def test_jwt_structure_and_signature(self) -> None:
        c = _make_client()
        c._token = "parent-access-token"
        c._token_expiry = time.time() + 3600

        exchange_resp = MagicMock()
        exchange_resp.status_code = 200
        exchange_resp.json.return_value = {"access_token": "tenant-tok", "expires_in": 3600}
        exchange_resp.headers = {}

        captured_data: dict[str, str] = {}

        async def capture_post(url: str, **kwargs: object) -> MagicMock:
            if "data" in kwargs:
                captured_data.update(kwargs["data"])  # type: ignore[arg-type]
            return exchange_resp

        with patch.object(c._http, "post", side_effect=capture_post):
            await c.for_tenant("org-abc-123")

        # Verify the subject_token is a valid HS256 JWT
        subject_jwt_str = captured_data["subject_token"]
        header = pyjwt.get_unverified_header(subject_jwt_str)
        assert header["alg"] == "HS256"
        assert header["kid"] == "test-client-id"

        # Verify signature with client_secret
        payload = pyjwt.decode(subject_jwt_str, TEST_CLIENT_SECRET, algorithms=["HS256"], audience="sigmacomputing")
        assert payload["tenant"] == "org-abc-123"
        assert payload["ver"] == "1.1"
        assert payload["aud"] == "sigmacomputing"

    async def test_token_exchange_request_body(self) -> None:
        c = _make_client()
        c._token = "parent-tok"
        c._token_expiry = time.time() + 3600

        exchange_resp = MagicMock()
        exchange_resp.status_code = 200
        exchange_resp.json.return_value = {"access_token": "t-tok", "expires_in": 3600}
        exchange_resp.headers = {}

        captured_data: dict[str, str] = {}

        async def capture_post(url: str, **kwargs: object) -> MagicMock:
            if "data" in kwargs:
                captured_data.update(kwargs["data"])  # type: ignore[arg-type]
            return exchange_resp

        with patch.object(c._http, "post", side_effect=capture_post):
            await c.for_tenant("org-xyz")

        assert captured_data["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
        assert captured_data["subject_token_type"] == "urn:ietf:params:oauth:token-type:jwt"
        assert captured_data["actor_token_type"] == "urn:ietf:params:oauth:token-type:access_token"
        assert captured_data["actor_token"] == "parent-tok"

    async def test_per_tenant_cache(self) -> None:
        c = _make_client()
        c._token = "parent-tok"
        c._token_expiry = time.time() + 3600

        exchange_resp = MagicMock()
        exchange_resp.status_code = 200
        exchange_resp.json.return_value = {"access_token": "tenant-tok-cached", "expires_in": 3600}
        exchange_resp.headers = {}

        mock_post = AsyncMock(return_value=exchange_resp)
        with patch.object(c._http, "post", mock_post):
            tc1 = await c.for_tenant("org-same")
            tc2 = await c.for_tenant("org-same")

        # Only 1 exchange call (second is cached)
        assert mock_post.call_count == 1
        assert tc1._token == "tenant-tok-cached"
        assert tc2._token == "tenant-tok-cached"
        # No manual cache cleanup needed: _tenant_cache is an instance attribute,
        # so each _make_client() call gets an isolated cache that is GC'd with it.


# ─── Secret redaction ─────────────────────────────────────────────────────────


class TestSecretRedaction:
    def test_client_secret_not_in_error_str(self) -> None:
        err = SigmaAPIError(
            status_code=401,
            path="/v2/auth/token",
            method="POST",
            detail="client_secret=test-client-secret is invalid",
        )
        assert "test-client-secret" in (err.detail or "")
        # The error __str__ and __repr__ should not reveal the secret on their own,
        # but the detail field can contain it.  The server's sigma_tool wrapper
        # applies _redact_secrets before returning to the user.
        # Here we just confirm the error type works without exposing secret in str().
        assert "test-client-secret" not in str(err)
        assert "test-client-secret" not in repr(err)


# ─── Transport ownership (regression: concurrent-edit bug) ───────────────────
# Tenant clients borrow the parent's connection pool. An earlier revision closed
# them explicitly, which killed the parent's pool and broke every later request.


async def test_tenant_client_shares_parent_transport() -> None:
    parent = SigmaClient("id", "secret", "https://api.example.com")
    tc = parent._build_tenant_client("tok", 3600, "org-1")
    assert tc._http is parent._http, "tenant client must reuse the parent pool"
    assert parent._owns_http is True
    assert tc._owns_http is False, "tenant client must not claim ownership"
    await parent.aclose()


async def test_closing_tenant_client_does_not_close_parent_pool() -> None:
    parent = SigmaClient("id", "secret", "https://api.example.com")
    tc = parent._build_tenant_client("tok", 3600, "org-1")
    await tc.aclose()
    assert not parent._http.is_closed, "tenant aclose() must not close the parent pool"
    await parent.aclose()
    assert parent._http.is_closed, "parent aclose() must close its own pool"


async def test_tenant_client_expiry_comes_from_exchange() -> None:
    """Expiry must derive from the exchange response, not a hardcoded value."""
    parent = SigmaClient("id", "secret", "https://api.example.com")
    tc = parent._build_tenant_client("tok", 120, "org-1")
    # Refresh happens slightly before true expiry, so allow a small margin.
    assert 0 < tc._token_expiry - time.time() <= 120
    await parent.aclose()


# ─── Tenant token refresh: re-exchanges, never falls back ─────────────────────


class TestTenantTokenRefresh:
    async def test_expired_tenant_re_exchanges_not_client_credentials(self) -> None:
        """When a tenant client's token expires, _get_token must perform a
        token-exchange (tenant-scoped) via the parent, NOT a client_credentials grant."""
        parent = _make_client()
        parent._token = "parent-tok"
        parent._token_expiry = time.time() + 3600

        # Build a tenant client with an already-expired token
        tc = parent._build_tenant_client("expired-tenant-tok", 0, "org-tenant-1")
        tc._token_expiry = time.time() - 10  # force expiry

        exchange_resp = MagicMock()
        exchange_resp.status_code = 200
        exchange_resp.json.return_value = {"access_token": "fresh-tenant-tok", "expires_in": 3600}
        exchange_resp.headers = {}

        captured_data: list[dict[str, str]] = []

        async def capture_post(url: str, **kwargs: object) -> MagicMock:
            if "data" in kwargs:
                captured_data.append(dict(kwargs["data"]))  # type: ignore[arg-type]
            return exchange_resp

        with patch.object(parent._http, "post", side_effect=capture_post):
            refreshed_token = await tc._get_token()

        # Must have done a token EXCHANGE, not client_credentials
        assert len(captured_data) == 1
        assert captured_data[0]["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
        assert refreshed_token == "fresh-tenant-tok"
        assert tc._token == "fresh-tenant-tok"


# ─── Cache-hit buffer: 60s applied exactly once ───────────────────────────────


class TestCacheHitBuffer:
    async def test_cache_hit_does_not_double_buffer(self) -> None:
        """A cached tenant token's expiry has the 60s buffer already baked in;
        the cache-hit path must NOT subtract 60s again."""
        parent = _make_client()
        parent._token = "parent-tok"
        parent._token_expiry = time.time() + 3600

        exchange_resp = MagicMock()
        exchange_resp.status_code = 200
        # Token valid for 600s
        exchange_resp.json.return_value = {"access_token": "tenant-cached", "expires_in": 600}
        exchange_resp.headers = {}

        mock_post = AsyncMock(return_value=exchange_resp)
        with patch.object(parent._http, "post", mock_post):
            # First call: does the exchange, populates cache
            tc1 = await parent.for_tenant("org-buf-test")
            # Second call: should hit cache
            tc2 = await parent.for_tenant("org-buf-test")

        assert mock_post.call_count == 1  # second hit cache

        # Both clients must have essentially the same expiry (within 1s of each other),
        # proving no double-buffer was applied.
        assert abs(tc1._token_expiry - tc2._token_expiry) < 1.0

        # The expiry should be approximately now + 600 - 60 (single buffer),
        # NOT now + 600 - 120 (double buffer).
        expected_expiry = time.time() + 600 - 60
        assert abs(tc2._token_expiry - expected_expiry) < 2.0
