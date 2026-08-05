"""Comprehensive test suite targeting 100% line coverage across client.py and server.py."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import Response

from sigma_mcp import server as srv
from sigma_mcp.client import SigmaAPIError, SigmaClient


def _mock_response(status_code: int = 200, json_data: dict | list | None = None, text: str = "") -> Response:
    resp = MagicMock(spec=Response)
    resp.status_code = status_code
    resp.headers = {}
    resp.content = b"{}" if json_data is None and not text else (text.encode("utf-8") if text else b"{}")
    resp.text = text or ("{}" if json_data is None else (str(json_data) if isinstance(json_data, str) else ""))
    resp.json = MagicMock(return_value={} if json_data is None else (json_data if json_data is not None else {}))
    return resp


@pytest.mark.asyncio
async def test_client_missing_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    c._http = MagicMock()
    c._http.aclose = AsyncMock()

    # Context manager __aenter__ and __aexit__
    async with c as client_ctx:
        assert client_ctx is c

    c._token = "valid_token"
    import time

    c._token_expiry = time.time() + 3600

    # retry_after non-numeric fallback
    r_429 = _mock_response(429, {"error": "rate_limit"})
    r_429.headers = {"Retry-After": "invalid_number"}
    r_200 = _mock_response(200, {"status": "ok"})
    c._http.request = AsyncMock(side_effect=[r_429, r_200])

    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    res = await c._request("GET", "/v2/test")
    assert res.status_code == 200

    # 204 No Content / empty response
    r_204 = _mock_response(204)
    r_204.content = b""
    c._http.request = AsyncMock(return_value=r_204)
    assert await c.get("/v2/empty") is None

    # put and patch helper methods
    c._http.request = AsyncMock(return_value=_mock_response(200, {"updated": True}))
    assert (await c.put("/v2/item", {"a": 1})) == {"updated": True}
    assert (await c.patch("/v2/item", {"a": 2})) == {"updated": True}

    # auto_paginate non-dict response
    c._http.request = AsyncMock(return_value=_mock_response(200, ["raw", "list"]))
    assert await c.auto_paginate("/v2/list") == []

    # for_tenant cache eviction on expired token
    c._tenant_cache["expired_tenant"] = ("old_token", 0.0)
    c.client_secret = "test-secret-32-bytes-long-key-123"
    c._get_token = AsyncMock(return_value="parent_token")
    c._http.post = AsyncMock(return_value=_mock_response(200, {"access_token": "new_t_token", "expires_in": 3600}))
    t_client = await c.for_tenant("expired_tenant")
    assert t_client._token == "new_t_token"

    # for_tenant token exchange HTTP error
    c._http.post = AsyncMock(return_value=_mock_response(403, {"error": "forbidden"}, text="Forbidden tenant"))
    with pytest.raises(SigmaAPIError, match="returned 403"):
        await c.for_tenant("bad_tenant")

    # Additional client methods
    c._http.request = AsyncMock(return_value=_mock_response(200, {"sources": []}))
    assert await c.list_workbook_sources("wb1") == {"sources": []}
    assert await c.download_query("q1") == b"{}"


@pytest.mark.asyncio
async def test_server_logging_and_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    # StructuredJSONFormatter with exception info
    formatter = srv.StructuredJSONFormatter()
    try:
        raise ValueError("Simulated exception")
    except ValueError:
        import sys

        record = logging.LogRecord("sigma_mcp", logging.ERROR, "server.py", 100, "Error msg", (), sys.exc_info())
        formatted = formatter.format(record)
        assert "Simulated exception" in formatted

    # configure_logging with json format
    monkeypatch.setenv("SIGMA_MCP_LOG_FORMAT", "json")
    srv.configure_logging()

    # Client delete endpoints returning 200/204
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    c.delete_connection_path_grant = AsyncMock(return_value=200)
    c.delete_file = AsyncMock(return_value=204)
    c.delete_workspace = AsyncMock(return_value=200)
    c.delete_team = AsyncMock(return_value=200)
    c.delete_user_attribute_for_user = AsyncMock(return_value=200)
    c.delete_user_attribute_for_team = AsyncMock(return_value=200)
    c.delete_user_attribute_for_tenant = AsyncMock(return_value=200)
    c.delete_workbook_schedule = AsyncMock(return_value=200)
    c.delete_deployment = AsyncMock(return_value=200)
    c.deactivate_member = AsyncMock(return_value=200)
    c.remove_workbook_tag = AsyncMock(return_value=200)
    c.delete_tag = AsyncMock(return_value=200)
    c.delete_workspace_grant = AsyncMock(return_value=200)
    monkeypatch.setattr(srv, "_client", c)

    assert "200" in await srv.sigma_delete_connection_path_grant("cp1", "g1", confirm=True)
    assert "204" in await srv.sigma_delete_file("f1", confirm=True)
    assert "200" in await srv.sigma_delete_workspace("ws1", confirm=True)
    assert "200" in await srv.sigma_delete_team("t1", confirm=True)
    assert "200" in await srv.sigma_delete_user_attribute_for_user("ua1", "m1", confirm=True)
    assert "200" in await srv.sigma_delete_user_attribute_for_team("ua1", "t1", confirm=True)
    assert "200" in await srv.sigma_delete_user_attribute_for_tenant("ua1", "org1", confirm=True)
    assert "200" in await srv.sigma_delete_workbook_schedule("wb1", "s1", confirm=True)
    assert "deleted" in await srv.sigma_archive_deployment("dep1", confirm=True)
    assert "200" in await srv.sigma_deactivate_member("m1", confirm=True)
    assert "200" in await srv.sigma_remove_workbook_tag("wb1", "tag1", confirm=True)
    assert "200" in await srv.sigma_delete_tag("tag1", confirm=True)
    assert "200" in await srv.sigma_delete_workspace_grant("ws1", "g1", confirm=True)


@pytest.mark.asyncio
async def test_reassign_workbook_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    c.search_members = AsyncMock(return_value={"entries": [{"memberId": "m1"}]})
    c.get = AsyncMock(
        return_value={
            "entries": [
                {"id": "f1", "ownerId": "old_m1", "name": "Workbook 1"},
                {"id": "f2", "ownerId": "old_m1", "name": "Workbook 2"},
                {"id": None, "ownerId": "old_m1", "name": "Bad File"},
            ]
        }
    )
    c.update_file = AsyncMock(side_effect=[{"status": "ok"}, Exception("Update failed")])
    res_dry = await srv.sigma_reassign_workbook_ownership("old@ex.com", "new@ex.com", dry_run=True)
    assert "dry_run" in res_dry

    res_exec = await srv.sigma_reassign_workbook_ownership("old@ex.com", "new@ex.com", dry_run=False)
    assert "transferred" in res_exec
    assert "failed" in res_exec


@pytest.mark.asyncio
async def test_scan_input_tables_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    c.list_all_workbooks = AsyncMock(return_value=[{"workbookId": "wb_err", "name": "Err Workbook"}])
    c.list_workbook_pages = AsyncMock(side_effect=Exception("Page fetch failed"))
    res1 = await srv.sigma_list_all_input_tables()
    assert "Page fetch failed" in res1

    c.list_workbook_pages = AsyncMock(return_value={"entries": [{"pageId": "p1", "name": "P1"}]})
    c.list_workbook_page_elements = AsyncMock(side_effect=Exception("Element fetch failed"))
    res2 = await srv.sigma_list_all_input_tables()
    assert "Element fetch failed" in res2


@pytest.mark.asyncio
async def test_bulk_sync_tenant_connections_error(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    c.list_tenants = AsyncMock(return_value={"entries": [{"orgId": "org_err", "name": "Err Tenant"}]})
    tc_err = MagicMock(spec=SigmaClient)
    tc_err.list_connections = AsyncMock(return_value={"entries": [{"connectionId": "c_err"}]})
    tc_err.sync_connection = AsyncMock(side_effect=Exception("Sync failed"))
    tc_err.aclose = AsyncMock()
    c.for_tenant = AsyncMock(return_value=tc_err)

    res = await srv.sigma_bulk_sync_tenant_connections(dry_run=False)
    assert "Sync failed" in res


@pytest.mark.asyncio
async def test_extra_coverage_branches() -> None:
    from sigma_mcp.errors import _sanitize

    # Retry-After HTTP-date parsing (with GMT and naive without GMT)
    assert SigmaClient._parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is not None
    assert SigmaClient._parse_retry_after("21 Oct 2026 07:28:00") is not None
    assert SigmaClient._parse_retry_after("invalid-http-date") is None

    # _sanitize list, string truncation, and non-string primitives (int/bool/None)
    sanitized = _sanitize(["plain", {"email": "secret@test.com", "code": 500, "ok": False, "extra": None}, "x" * 600])
    assert isinstance(sanitized, list)
    assert sanitized[1]["email"] == "[redacted]"
    assert sanitized[1]["code"] == 500
    assert sanitized[1]["ok"] is False
    assert len(sanitized[2]) == 500

    # _redact_secrets extra_secret parameter
    redacted = srv._redact_secrets("custom-secret-value in string", extra_secret="custom-secret-value")
    assert "custom-secret-value" not in redacted
    assert "***REDACTED***" in redacted

    # _HEADER_CLIENT_CACHE eviction logic
    for i in range(105):
        h = {"headers": {"x-sigma-client-id": f"cid_{i}", "x-sigma-client-secret": f"sec_{i}"}}
        await srv.get_client(h)
    assert len(srv._HEADER_CLIENT_CACHE) <= 100
