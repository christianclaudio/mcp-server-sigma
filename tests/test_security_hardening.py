"""Tests for security hardening: path traversal prevention, token redaction, and tenant allowlists."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from sigma_mcp.client import SigmaClient
from sigma_mcp.errors import SigmaAPIError
from sigma_mcp.server import _redact_secrets


def test_redact_secrets_bearer_and_jwt() -> None:
    raw_error = "Authorization header Bearer eyJhbGciOiJIUzI1NiJ9.eyJ0ZW5hbnQiOiJ0ZXN0In0.signature failed"
    redacted = _redact_secrets(raw_error)
    assert "eyJhbGciOiJIUzI1NiJ9" not in redacted
    assert "***REDACTED***" in redacted

    ghs_token = "ghs_1234567890abcdef1234567890abcdef1234567890"
    ghs_token_log = f"Failed request using {ghs_token}"
    redacted_ghs = _redact_secrets(ghs_token_log)
    assert ghs_token not in redacted_ghs
    assert "***REDACTED***" in redacted_ghs


@pytest.mark.asyncio
async def test_tenant_allowlist_enforcement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGMA_ALLOWED_TENANTS", "org-allowed-1, org-allowed-2")
    c = SigmaClient("id", "secret", "https://api.sigmacomputing.com")

    # Forbidden tenant is rejected with 403 on allowlist check
    with pytest.raises(SigmaAPIError) as exc_info:
        await c.for_tenant("org-forbidden")

    assert exc_info.value.status_code == 403
    assert "not in SIGMA_ALLOWED_TENANTS allowlist" in (exc_info.value.detail or "")

    # Allowed tenant passes allowlist check (trims whitespace)
    c._get_token = AsyncMock(return_value="token123")  # type: ignore[method-assign]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"access_token": "tenant_token", "expires_in": 3600}
    c._http.post = AsyncMock(return_value=mock_resp)

    tenant_client = await c.for_tenant("org-allowed-2")
    assert isinstance(tenant_client, SigmaClient)


@pytest.mark.asyncio
async def test_strict_tenant_allowlist_enforcement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIGMA_ALLOWED_TENANTS", raising=False)
    monkeypatch.setenv("SIGMA_STRICT_TENANT_ALLOWLIST", "1")
    c = SigmaClient("id", "secret", "https://api.sigmacomputing.com")

    with pytest.raises(SigmaAPIError) as exc_info:
        await c.for_tenant("any-tenant")

    assert exc_info.value.status_code == 403
    assert "SIGMA_STRICT_TENANT_ALLOWLIST is active" in (exc_info.value.detail or "")


@pytest.mark.asyncio
async def test_get_client_rejects_untrusted_base_url() -> None:
    from sigma_mcp import server as srv

    async def _reject(base_url: str, match: str) -> None:
        ctx = {
            "headers": {
                "x-sigma-client-id": "req-id",
                "x-sigma-client-secret": "req-secret",
                "x-sigma-base-url": base_url,
            }
        }
        with pytest.raises(ValueError, match=match):
            await srv.get_client(ctx)

    await _reject("http://127.0.0.1:9", "Only HTTPS")
    await _reject("https://169.254.169.254", "Blocked private/reserved IP")
    await _reject("https://evil.example", "not in allowed hosts")


@pytest.mark.asyncio
async def test_delete_confirm_parameter_required() -> None:
    from sigma_mcp.server import sigma_delete_workspace, sigma_delete_workspace_grant

    # The confirm two-step is a normal result (not an error) that tells the caller to re-call.
    for res in (await sigma_delete_workspace("ws1"), await sigma_delete_workspace_grant("ws1", "g1")):
        payload = json.loads(res)
        assert payload["status"] == "confirmation_required"
        assert payload["executed"] is False
        assert "Re-call this tool with confirm=true" in payload["message"]
