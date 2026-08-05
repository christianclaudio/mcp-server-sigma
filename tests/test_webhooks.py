"""Unit tests for webhooks module, summary_only list tools, prompts, and per-request context header resolution."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest

from sigma_mcp import server as srv
from sigma_mcp.client import SigmaClient
from sigma_mcp.webhooks import (
    clear_webhook_buffer,
    get_recent_webhooks,
    process_incoming_webhook,
    record_webhook_event,
    verify_webhook_signature,
)


@pytest.fixture(autouse=True)
def _reset_webhooks() -> None:
    clear_webhook_buffer()


def test_verify_webhook_signature() -> None:
    secret = "my_webhook_secret_123"
    body = b'{"event_type": "export_completed", "exportId": "exp1"}'

    # Missing secret or header returns False
    assert verify_webhook_signature(body, None, secret) is False
    assert verify_webhook_signature(body, "sha256=123", "") is False

    # Valid signature calculation
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, f"sha256={expected}", secret) is True
    assert verify_webhook_signature(body, expected, secret) is True
    assert verify_webhook_signature(body, "sha256=bad_sig", secret) is False


def test_record_and_get_recent_webhooks() -> None:
    clear_webhook_buffer()
    record_webhook_event("export_completed", {"id": "1"})
    evt2 = record_webhook_event("alert_triggered", {"id": "2"})

    assert len(get_recent_webhooks()) == 2
    assert get_recent_webhooks(limit=1)[0]["event_id"] == evt2["event_id"]
    assert len(get_recent_webhooks(event_type="export_completed")) == 1
    assert get_recent_webhooks(limit=0) == []
    assert get_recent_webhooks(limit=-5) == []


@pytest.mark.asyncio
async def test_process_incoming_webhook_success() -> None:
    payload = b'{"event_type": "scheduled_export", "id": "exp_100"}'
    res = await process_incoming_webhook(payload, {"x-sigma-event": "scheduled_export"})
    assert res["status"] == "accepted"
    assert res["status_code"] == 200

    recent = get_recent_webhooks()
    assert len(recent) == 1
    assert recent[0]["payload"]["id"] == "exp_100"

    # Test fallback event_type when no event_type is in body or header
    res_fallback = await process_incoming_webhook(b'{"key": "val"}', {})
    assert res_fallback["status_code"] == 200

    # Test empty body
    res_empty = await process_incoming_webhook(b"", {})
    assert res_empty["status_code"] == 200


@pytest.mark.asyncio
async def test_process_incoming_webhook_errors() -> None:
    secret = "test_secret_32"
    payload = b'{"event_type": "alert", "id": "123"}'
    expected_sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    # Valid signature pass case
    res_valid = await process_incoming_webhook(
        payload, {"x-sigma-signature": f"sha256={expected_sig}"}, webhook_secret=secret
    )
    assert res_valid["status_code"] == 200

    # Invalid signature
    res_sig = await process_incoming_webhook(payload, {"x-sigma-signature": "bad"}, webhook_secret=secret)
    assert res_sig["status_code"] == 401
    assert "Invalid webhook signature" in res_sig["error"]

    # Invalid JSON
    res_json = await process_incoming_webhook(b"not json", {})
    assert res_json["status_code"] == 400
    assert "Invalid JSON payload" in res_json["error"]

    # Non-dict JSON payload
    res_arr = await process_incoming_webhook(b"[1, 2, 3]", {})
    assert res_arr["status_code"] == 400
    assert "Payload must be a JSON object" in res_arr["error"]


@pytest.mark.asyncio
async def test_webhook_listener_exception() -> None:
    from sigma_mcp import webhooks

    async def bad_listener(evt: dict) -> None:
        raise RuntimeError("Listener failure")

    webhooks._EVENT_LISTENERS.append(bad_listener)
    try:
        res = await process_incoming_webhook(b'{"event_type": "test"}', {})
        assert res["status_code"] == 200
    finally:
        webhooks._EVENT_LISTENERS.remove(bad_listener)


@pytest.mark.asyncio
async def test_sigma_list_recent_webhooks_tool_and_resource() -> None:
    record_webhook_event("alert_triggered", {"alertId": "a1"})

    res_tool = await srv.sigma_list_recent_webhooks(limit=10)
    data = json.loads(res_tool)
    assert data["count"] == 1
    assert data["events"][0]["payload"]["alertId"] == "a1"

    res_resource = srv.resource_webhooks_recent()
    assert "alertId" in res_resource


@pytest.mark.asyncio
async def test_get_client_per_request_context_headers() -> None:
    class MockRequestContext:
        def __init__(self, headers: dict[str, str]) -> None:
            self.headers = headers

    class MockContext:
        def __init__(self, headers: dict[str, str]) -> None:
            self.request_context = MockRequestContext(headers)

    ctx_headers = {
        "X-Sigma-Client-Id": "req-client-id",
        "X-Sigma-Client-Secret": "req-client-secret-32-bytes-long",
        "X-Sigma-Base-Url": "https://api.us-a.aws.sigmacomputing.com",
    }
    ctx = MockContext(ctx_headers)
    c = await srv.get_client(ctx)
    assert c.client_id == "req-client-id"
    assert c.client_secret == "req-client-secret-32-bytes-long"
    assert c.base_url == "https://api.us-a.aws.sigmacomputing.com"

    # Dict context mock
    dict_ctx = {"headers": ctx_headers}
    c_dict = await srv.get_client(dict_ctx)
    assert c_dict.client_id == "req-client-id"
    assert c_dict.client_secret == "req-client-secret-32-bytes-long"
    assert c_dict.base_url == "https://api.us-a.aws.sigmacomputing.com"

    # Lower-case headers mock
    lower_headers = {
        "x-sigma-client-id": "req-client-id-lower",
        "x-sigma-client-secret": "req-client-secret-lower-32-bytes",
        "x-sigma-base-url": "https://api.us-a.aws.sigmacomputing.com",
    }
    c_lower = await srv.get_client(MockContext(lower_headers))
    assert c_lower.client_id == "req-client-id-lower"
    assert c_lower.client_secret == "req-client-secret-lower-32-bytes"
    assert c_lower.base_url == "https://api.us-a.aws.sigmacomputing.com"

    c_dict_lower = await srv.get_client({"headers": lower_headers})
    assert c_dict_lower.client_id == "req-client-id-lower"
    assert c_dict_lower.client_secret == "req-client-secret-lower-32-bytes"
    assert c_dict_lower.base_url == "https://api.us-a.aws.sigmacomputing.com"

    # Partial headers fail-closed error
    partial_ctx = {"headers": {"X-Sigma-Client-Id": "only-id"}}
    with pytest.raises(ValueError, match="Both X-Sigma-Client-Id and X-Sigma-Client-Secret must be provided"):
        await srv.get_client(partial_ctx)


@pytest.mark.asyncio
async def test_summary_only_list_tools_and_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    monkeypatch.setattr(srv, "_client", c)

    c.list_connections = AsyncMock(
        return_value={"entries": [{"connectionId": "c1", "name": "Conn 1", "type": "snowflake"}]}
    )
    res_conn_sum = await srv.sigma_list_connections(summary_only=True)
    assert "snowflake" in res_conn_sum

    c.list_workbooks = AsyncMock(
        return_value={
            "entries": [{"workbookId": "wb1", "name": "Workbook 1", "extraField": "extra", "folderId": "f1"}],
            "nextPage": "cursor123",
        }
    )
    res_full = await srv.sigma_list_workbooks(summary_only=False)
    assert "extraField" in res_full

    res_sum = await srv.sigma_list_workbooks(summary_only=True)
    assert "extraField" not in res_sum
    assert "workbookId" in res_sum

    c.list_data_models = AsyncMock(
        return_value={"entries": [{"dataModelId": "dm1", "name": "Model 1", "connectionId": "c1"}]}
    )
    res_dm_sum = await srv.sigma_list_data_models(summary_only=True)
    assert "dm1" in res_dm_sum

    c.list_members = AsyncMock(
        return_value={
            "entries": [{"memberId": "m1", "email": "m1@ex.com", "firstName": "Alice", "sensitive": "secret"}]
        }
    )
    res_mem_sum = await srv.sigma_list_members(summary_only=True)
    assert "sensitive" not in res_mem_sum
    assert "m1@ex.com" in res_mem_sum

    c.list_teams = AsyncMock(return_value={"entries": [{"teamId": "t1", "name": "Team 1", "description": "Desc"}]})
    res_team_sum = await srv.sigma_list_teams(summary_only=True)
    assert "Team 1" in res_team_sum

    # Non-dict summarize fallback test
    assert srv._summarize_list("not_a_dict", ["field"]) == "not_a_dict"

    # Test new prompt functions
    p1 = srv.prompt_onboard_team_member("john@ex.com", "John", "Doe", "Engineering")
    assert "John Doe" in p1

    p2 = srv.prompt_swap_warehouse_source("wb123", "conn456")
    assert "wb123" in p2

    p3 = srv.prompt_audit_tenant_connections()
    assert "dry_run=True" in p3
