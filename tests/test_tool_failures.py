"""Tool failures in admin, elements and workbooks are tool execution errors (``isError: true``).

MCP spec 2026-07-28, server/tools, Error Handling: a call that fails reports it in the tool
result with ``isError: true``. These calls used to return an ``{"error": ...}`` dict as a
normal result; each now raises a redacted ``ToolError`` ``from None``. Each test here fails
if its site goes back to returning the dict.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from sigma_mcp import server
from sigma_mcp.errors import SigmaAPIError
from sigma_mcp.server import create_server
from sigma_mcp.tools.common import _item_error, _tool_failure, sigma_tool

_SECRET = "s3cr3t-client-secret-value-for-redaction-test"


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    client = AsyncMock()
    monkeypatch.setattr(server, "get_client", AsyncMock(return_value=client))
    monkeypatch.setenv("SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE", "1")
    monkeypatch.setenv("SIGMA_CLIENT_SECRET", _SECRET)
    return client


async def _call_error(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call a tool over an in-memory Client; assert isError true and return the error object."""
    async with Client(create_server(profile="full")) as client:
        res = await client.call_tool(name, args, raise_on_error=False)
    assert res.is_error is True, res
    payload = json.loads(res.content[0].text)  # type: ignore[union-attr]
    assert set(payload) == {"error"}
    err: dict[str, Any] = payload["error"]
    return err


def _members(n: int) -> list[dict[str, Any]]:
    return [
        {"memberId": f"m-{i}", "firstName": f"Test{i}", "lastName": "User", "isActive": True, "isInactive": False}
        for i in range(n)
    ]


# ─── admin: bulk-deactivate pattern checks ────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_catchall_pattern_is_error_true(api: AsyncMock) -> None:
    err = await _call_error("admin_bulk_deactivate_members", {"name_pattern": ".*", "dry_run": False, "confirm": True})
    assert err == {
        "type": "invalid_request",
        "message": "Catch-all pattern '.*' is rejected for safety. "
        "Use a specific name pattern to target individual members.",
    }
    api.auto_paginate.assert_not_called()
    api.deactivate_member.assert_not_called()


@pytest.mark.asyncio
async def test_admin_empty_match_pattern_is_error_true(api: AsyncMock) -> None:
    err = await _call_error("admin_bulk_deactivate_members", {"name_pattern": "a*", "dry_run": False, "confirm": True})
    assert err["type"] == "invalid_request"
    assert err["message"] == "Pattern 'a*' matches empty string and is too broad. Use a specific name pattern."
    api.deactivate_member.assert_not_called()


@pytest.mark.asyncio
async def test_admin_invalid_regex_is_error_true_without_chain(api: AsyncMock) -> None:
    api.auto_paginate = AsyncMock(return_value=_members(1))
    err = await _call_error(
        "admin_bulk_deactivate_members", {"name_pattern": "[bad", "dry_run": False, "confirm": True}
    )
    assert err["type"] == "invalid_request"
    assert err["message"].startswith("Invalid regex pattern: unterminated character set")
    # Raised after ``except re.error`` ends: no cause and no context.
    with pytest.raises(ToolError) as exc_info:
        await server.sigma_bulk_deactivate_members("[bad", dry_run=False, confirm=True)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert exc_info.value.__suppress_context__ is True
    api.deactivate_member.assert_not_called()


@pytest.mark.asyncio
async def test_admin_safety_cap_is_error_true(api: AsyncMock) -> None:
    api.auto_paginate = AsyncMock(return_value=_members(15))
    err = await _call_error(
        "admin_bulk_deactivate_members", {"name_pattern": "Test", "dry_run": False, "confirm": True}
    )
    assert err["type"] == "invalid_request"
    assert err["message"] == (
        "Pattern matches 15 active members, exceeding the safety cap of 10. Use a narrower pattern."
    )
    assert err["count"] == 15
    assert err["first_10"] == [f"Test{i} User" for i in range(10)]
    api.deactivate_member.assert_not_called()


# ─── elements: doc page slug check ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_elements_rejected_slug_is_error_true_without_chain(api: AsyncMock) -> None:
    http = AsyncMock()
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=False)
    with patch("httpx.AsyncClient", return_value=http):
        err = await _call_error("elements_get_doc_page", {"page_slug": "https://169.254.169.254/latest/meta-data"})
        assert err == {
            "type": "invalid_request",
            "message": "Documentation fetches are limited to https://help.sigmacomputing.com.",
        }
        # Raised after ``except ValueError`` ends: no cause and no context.
        with pytest.raises(ToolError) as exc_info:
            await server.sigma_get_doc_page("docs/%2e%2e/secret")
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert exc_info.value.__suppress_context__ is True
    assert json.loads(str(exc_info.value))["error"]["message"] == "Invalid documentation page slug."
    http.get.assert_not_called()


# ─── workbooks: not-found, timeout, missing ID ────────────────────────────────


@pytest.mark.asyncio
async def test_workbooks_member_not_found_is_error_true(api: AsyncMock) -> None:
    api.search_members = AsyncMock(return_value={"entries": []})
    err = await _call_error(
        "workbooks_reassign_workbook_ownership",
        {"old_owner_email": "old@example.com", "new_owner_email": "new@example.com"},
    )
    assert err == {"type": "not_found", "message": "No member found for email: old@example.com"}
    api.update_file.assert_not_called()


@pytest.mark.asyncio
async def test_workbooks_member_without_home_folder_is_error_true(api: AsyncMock) -> None:
    api.get_member = AsyncMock(return_value={"memberId": "m1"})
    err = await _call_error("workbooks_copy_workbook_to_member", {"workbook_id": "wb1", "member_id": "m1"})
    assert err == {"type": "not_found", "message": "Member has no homeFolderId", "member_id": "m1"}
    api.duplicate_workbook.assert_not_called()


@pytest.mark.asyncio
async def test_workbooks_export_timeout_is_error_true(api: AsyncMock) -> None:
    api.export_workbook = AsyncMock(return_value={"queryId": "q-1"})
    pending = MagicMock()
    pending.status_code = 204
    api.download_query_raw = AsyncMock(return_value=pending)
    with patch("sigma_mcp.server.asyncio.sleep", new_callable=AsyncMock):
        err = await _call_error("workbooks_export_and_download", {"workbook_id": "wb1", "timeout_seconds": 0})
    assert err == {
        "type": "timeout",
        "message": "Export did not finish within timeout_seconds=0",
        "query_id": "q-1",
        "timeout_seconds": 0,
    }


@pytest.mark.asyncio
async def test_workbooks_export_missing_query_id_is_error_true_without_raw_body(api: AsyncMock) -> None:
    api.export_workbook = AsyncMock(return_value={"detail": f"upstream body with {_SECRET}"})
    err = await _call_error("workbooks_export_and_download", {"workbook_id": "wb1"})
    assert err == {"type": "upstream_response", "message": "No queryId in export response"}
    assert _SECRET not in json.dumps(err)
    api.download_query_raw.assert_not_called()


# ─── the shared helper ────────────────────────────────────────────────────────


def test_tool_failure_redacts_message_and_string_details(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGMA_CLIENT_SECRET", _SECRET)
    with pytest.raises(ToolError) as exc_info:
        _tool_failure("not_found", f"missing {_SECRET}", slug=f"x-{_SECRET}", status=404)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert exc_info.value.__suppress_context__ is True
    assert json.loads(str(exc_info.value)) == {
        "error": {
            "type": "not_found",
            "message": "missing ***REDACTED***",
            "slug": "x-***REDACTED***",
            "status": 404,
        }
    }


# ─── batch tools: every item failed is an error; a partial success is not ─────


async def _call_ok(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call a tool over an in-memory Client; assert isError false and return the parsed result."""
    async with Client(create_server(profile="full")) as client:
        res = await client.call_tool(name, args, raise_on_error=False)
    assert res.is_error is False, res
    data: dict[str, Any] = json.loads(res.content[0].text)  # type: ignore[union-attr]
    return data


def _assert_item_errors(items: list[dict[str, Any]]) -> None:
    """Every failed item's ``error`` is an object with a non-empty, redacted ``message``."""
    assert items
    for item in items:
        err = item["error"]
        assert isinstance(err, dict), item
        assert isinstance(err["message"], str) and err["message"], item
        assert _SECRET not in json.dumps(err)
        assert set(item) == {"id", "status", "error"}, item
        assert item["status"] == "failed", item
        for nested in err.get("pages", []) + err.get("connections", []):
            _assert_item_errors([nested])


def _assert_batch_shape(data: dict[str, Any], verb: str) -> None:
    """The one fleet batch shape: top-level status/<verb>_count/failed_count/results/errors."""
    assert data["status"] in {"success", "partial_success"}
    assert data[f"{verb}_count"] == len(data["results"])
    assert data["failed_count"] == len(data["errors"])
    for entry in data["results"]:
        assert set(entry) == {"id", "status", "result"}, entry
        assert entry["status"] == verb, entry
    if data["errors"]:
        _assert_item_errors(data["errors"])


@pytest.mark.asyncio
async def test_admin_bulk_deactivate_all_failed_is_error_true(api: AsyncMock) -> None:
    api.auto_paginate = AsyncMock(return_value=_members(2))
    api.deactivate_member = AsyncMock(side_effect=Exception(f"denied {_SECRET}"))
    err = await _call_error(
        "admin_bulk_deactivate_members", {"name_pattern": "Test", "dry_run": False, "confirm": True}
    )
    assert err["type"] == "batch_failed"
    assert err["message"] == "All 2 member deactivations failed; nothing was deactivated."
    assert err["pattern"] == "Test"
    assert err["failed_count"] == 2
    assert [e["id"] for e in err["errors"]] == ["m-0", "m-1"]
    _assert_item_errors(err["errors"])
    assert err["errors"][0]["error"]["message"] == "denied ***REDACTED***"
    assert all(e["status"] == "failed" for e in err["errors"])


@pytest.mark.asyncio
async def test_admin_bulk_deactivate_partial_is_normal_result(api: AsyncMock) -> None:
    api.auto_paginate = AsyncMock(return_value=_members(2))
    api.deactivate_member = AsyncMock(side_effect=[200, Exception(f"denied {_SECRET}")])
    data = await _call_ok("admin_bulk_deactivate_members", {"name_pattern": "Test", "dry_run": False, "confirm": True})
    assert data["status"] == "partial_success"
    assert data["deactivated_count"] == 1
    assert data["failed_count"] == 1
    _assert_batch_shape(data, "deactivated")
    assert [r["id"] for r in data["results"]] == ["m-0"]
    assert [e["id"] for e in data["errors"]] == ["m-1"]
    _assert_item_errors(data["errors"])


def _owned_files(n: int) -> dict[str, Any]:
    return {"entries": [{"id": f"f{i}", "ownerId": "old-id", "name": f"WB {i}"} for i in range(n)]}


@pytest.mark.asyncio
async def test_workbooks_reassign_all_failed_is_error_true(api: AsyncMock) -> None:
    api.search_members = AsyncMock(
        side_effect=[{"entries": [{"memberId": "old-id"}]}, {"entries": [{"memberId": "new-id"}]}]
    )
    api.get = AsyncMock(return_value=_owned_files(2))
    api.update_file = AsyncMock(side_effect=Exception(f"forbidden {_SECRET}"))
    err = await _call_error(
        "workbooks_reassign_workbook_ownership",
        {"old_owner_email": "old@example.com", "new_owner_email": "new@example.com", "dry_run": False},
    )
    assert err["type"] == "batch_failed"
    assert err["message"] == "All 2 workbook transfers failed; nothing was transferred."
    assert err["old_owner"] == {"email": "old@example.com", "memberId": "old-id"}
    assert err["new_owner"] == {"email": "new@example.com", "memberId": "new-id"}
    assert err["failed_count"] == 2
    assert [e["id"] for e in err["errors"]] == ["f0", "f1"]
    _assert_item_errors(err["errors"])


@pytest.mark.asyncio
async def test_workbooks_reassign_partial_is_normal_result(api: AsyncMock) -> None:
    api.search_members = AsyncMock(
        side_effect=[{"entries": [{"memberId": "old-id"}]}, {"entries": [{"memberId": "new-id"}]}]
    )
    api.get = AsyncMock(return_value=_owned_files(2))
    api.update_file = AsyncMock(side_effect=[{}, Exception(f"forbidden {_SECRET}")])
    data = await _call_ok(
        "workbooks_reassign_workbook_ownership",
        {"old_owner_email": "old@example.com", "new_owner_email": "new@example.com", "dry_run": False},
    )
    assert data["status"] == "partial_success"
    assert data["transferred_count"] == 1
    assert data["failed_count"] == 1
    _assert_batch_shape(data, "transferred")
    assert [r["id"] for r in data["results"]] == ["f0"]
    assert [e["id"] for e in data["errors"]] == ["f1"]
    _assert_item_errors(data["errors"])


@pytest.mark.asyncio
async def test_elements_scan_all_failed_is_error_true(api: AsyncMock) -> None:
    api.list_all_workbooks = AsyncMock(
        return_value=[{"workbookId": "wb1", "name": "A"}, {"workbookId": "wb2", "name": "B"}]
    )

    async def pages(wb_id: str) -> dict[str, Any]:
        if wb_id == "wb1":
            raise Exception(f"pages down {_SECRET}")
        return {"entries": [{"pageId": "p1", "name": "P1"}]}

    api.list_workbook_pages = AsyncMock(side_effect=pages)
    api.list_workbook_page_elements = AsyncMock(side_effect=Exception(f"elements down {_SECRET}"))
    err = await _call_error("elements_list_all_input_tables", {})
    assert err["type"] == "batch_failed"
    assert err["message"] == "All 2 workbook scans failed; nothing was scanned."
    assert err["failed_count"] == 2
    assert sorted(e["error"]["stage"] for e in err["errors"]) == ["elements", "pages"]
    assert [e["id"] for e in err["errors"]] == ["wb1", "wb2"]
    _assert_item_errors(err["errors"])


@pytest.mark.asyncio
async def test_elements_scan_partial_is_normal_result(api: AsyncMock) -> None:
    api.list_all_workbooks = AsyncMock(
        return_value=[{"workbookId": "wb1", "name": "A"}, {"workbookId": "wb2", "name": "B"}]
    )

    async def pages(wb_id: str) -> dict[str, Any]:
        if wb_id == "wb1":
            raise Exception(f"pages down {_SECRET}")
        return {"entries": [{"pageId": "p1", "name": "P1"}]}

    api.list_workbook_pages = AsyncMock(side_effect=pages)
    api.list_workbook_page_elements = AsyncMock(
        return_value={"entries": [{"type": "input-table", "elementId": "e1", "name": "Input"}]}
    )
    data = await _call_ok("elements_list_all_input_tables", {})
    assert data["status"] == "partial_success"
    assert data["failed_count"] == 1
    assert data["total_input_tables"] == 1
    assert len(data["errors"]) == 1
    _assert_batch_shape(data, "scanned")


def _tenant_client(sync_side_effect: Any) -> AsyncMock:
    tc = AsyncMock()
    tc.list_connections = AsyncMock(return_value={"entries": [{"connectionId": "c1"}]})
    tc.sync_connection = AsyncMock(side_effect=sync_side_effect, return_value={"status": "queued"})
    return tc


@pytest.mark.asyncio
async def test_datasets_tenant_sync_all_failed_is_error_true(api: AsyncMock) -> None:
    api.list_tenants = AsyncMock(
        return_value={"entries": [{"orgId": "t1", "name": "T1"}, {"orgId": "t2", "name": "T2"}]}
    )

    async def for_tenant(org_id: str) -> AsyncMock:
        if org_id == "t1":
            raise Exception(f"token exchange refused {_SECRET}")
        return _tenant_client(Exception(f"sync failed {_SECRET}"))

    api.for_tenant = AsyncMock(side_effect=for_tenant)
    err = await _call_error("datasets_bulk_sync_tenant_connections", {"dry_run": False})
    assert err["type"] == "batch_failed"
    assert err["message"] == "All 2 tenant syncs failed; nothing was synced."
    assert err["failed_count"] == 2
    assert sorted(e["id"] for e in err["errors"]) == ["t1", "t2"]
    _assert_item_errors(err["errors"])
    by_org = {e["id"]: e for e in err["errors"]}
    assert by_org["t2"]["error"]["type"] == "batch_failed"
    assert by_org["t2"]["error"]["message"] == "All 1 connection syncs failed."


@pytest.mark.asyncio
async def test_datasets_tenant_sync_partial_is_normal_result(api: AsyncMock) -> None:
    api.list_tenants = AsyncMock(
        return_value={"entries": [{"orgId": "t1", "name": "T1"}, {"orgId": "t2", "name": "T2"}]}
    )

    async def for_tenant(org_id: str) -> AsyncMock:
        if org_id == "t1":
            raise Exception(f"token exchange refused {_SECRET}")
        return _tenant_client(None)

    api.for_tenant = AsyncMock(side_effect=for_tenant)
    data = await _call_ok("datasets_bulk_sync_tenant_connections", {"dry_run": False})
    assert data["status"] == "partial_success"
    assert data["synced_count"] == 1
    assert data["failed_count"] == 1
    assert [r["id"] for r in data["results"]] == ["t2"]
    assert [e["id"] for e in data["errors"]] == ["t1"]
    _assert_batch_shape(data, "synced")
    _assert_batch_shape(data["results"][0]["result"], "synced")


def test_tool_failure_redacts_nested_batch_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGMA_CLIENT_SECRET", _SECRET)
    with pytest.raises(ToolError) as exc_info:
        _tool_failure("batch_failed", "All 1 failed", errors=[{"id": "x", "error": f"denied {_SECRET}"}])
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert exc_info.value.__suppress_context__ is True
    assert _SECRET not in str(exc_info.value)
    assert json.loads(str(exc_info.value))["error"]["errors"] == [{"id": "x", "error": "denied ***REDACTED***"}]


def test_item_error_always_has_a_redacted_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGMA_CLIENT_SECRET", _SECRET)
    api_err = _item_error(SigmaAPIError(403, "/v2/files/f1", "PATCH", f"no {_SECRET}", "req-1"))
    assert api_err["type"] == "sigma_api_error"
    assert api_err["message"] == "Sigma API PATCH /v2/files/f1 returned 403"
    assert api_err["status_code"] == 403
    assert _SECRET not in json.dumps(api_err)
    assert _item_error(ValueError("")) == {"type": "internal", "message": "ValueError"}
    assert _item_error("missing file ID", "invalid_item") == {"type": "invalid_item", "message": "missing file ID"}
    assert _item_error(RuntimeError(f"x {_SECRET}"))["message"] == "x ***REDACTED***"


# ─── batch items: a non-API exception is recorded, redacted, and the batch continues ──

_BEARER = "sk-live-abc123"
_LEAK = f"Authorization: Bearer {_BEARER}"


def _no_leak(data: Any, caplog: pytest.LogCaptureFixture) -> None:
    assert _BEARER not in json.dumps(data)
    assert _BEARER not in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_admin_bulk_deactivate_runtime_error_is_redacted(
    api: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    api.auto_paginate = AsyncMock(return_value=_members(2))
    api.deactivate_member = AsyncMock(side_effect=[RuntimeError(f"boom {_LEAK}"), 200])
    data = await _call_ok("admin_bulk_deactivate_members", {"name_pattern": "Test", "dry_run": False, "confirm": True})
    assert data["status"] == "partial_success"
    assert data["errors"][0]["error"] == {"type": "internal", "message": "boom Authorization: ***REDACTED***"}
    _no_leak(data, caplog)
    assert "Batch item failed: boom Authorization: ***REDACTED***" in caplog.text


@pytest.mark.asyncio
async def test_admin_bulk_deactivate_non_api_exception_does_not_stop_batch(api: AsyncMock) -> None:
    api.auto_paginate = AsyncMock(return_value=_members(3))
    api.deactivate_member = AsyncMock(side_effect=[KeyError("x"), 200, 200])
    data = await _call_ok("admin_bulk_deactivate_members", {"name_pattern": "Test", "dry_run": False, "confirm": True})
    assert api.deactivate_member.await_count == 3
    assert data["deactivated_count"] == 2
    assert data["failed_count"] == 1


@pytest.mark.asyncio
async def test_workbooks_reassign_runtime_error_is_redacted(api: AsyncMock, caplog: pytest.LogCaptureFixture) -> None:
    api.search_members = AsyncMock(
        side_effect=[{"entries": [{"memberId": "old-id"}]}, {"entries": [{"memberId": "new-id"}]}]
    )
    api.get = AsyncMock(return_value=_owned_files(2))
    api.update_file = AsyncMock(side_effect=[RuntimeError(f"boom {_LEAK}"), {}])
    data = await _call_ok(
        "workbooks_reassign_workbook_ownership",
        {"old_owner_email": "old@example.com", "new_owner_email": "new@example.com", "dry_run": False},
    )
    assert data["status"] == "partial_success"
    assert data["errors"][0]["error"]["message"] == "boom Authorization: ***REDACTED***"
    _no_leak(data, caplog)


@pytest.mark.asyncio
async def test_workbooks_reassign_non_api_exception_does_not_stop_batch(api: AsyncMock) -> None:
    api.search_members = AsyncMock(
        side_effect=[{"entries": [{"memberId": "old-id"}]}, {"entries": [{"memberId": "new-id"}]}]
    )
    api.get = AsyncMock(return_value=_owned_files(3))
    api.update_file = AsyncMock(side_effect=[TypeError("bad"), {}, {}])
    data = await _call_ok(
        "workbooks_reassign_workbook_ownership",
        {"old_owner_email": "old@example.com", "new_owner_email": "new@example.com", "dry_run": False},
    )
    assert api.update_file.await_count == 3
    assert data["transferred_count"] == 2
    assert data["failed_count"] == 1


@pytest.mark.asyncio
async def test_elements_scan_runtime_error_is_redacted(api: AsyncMock, caplog: pytest.LogCaptureFixture) -> None:
    api.list_all_workbooks = AsyncMock(
        return_value=[{"workbookId": "wb1", "name": "A"}, {"workbookId": "wb2", "name": "B"}]
    )

    async def pages(wb_id: str) -> dict[str, Any]:
        if wb_id == "wb1":
            raise RuntimeError(f"boom {_LEAK}")
        return {"entries": [{"pageId": "p1", "name": "P1"}]}

    api.list_workbook_pages = AsyncMock(side_effect=pages)
    api.list_workbook_page_elements = AsyncMock(return_value={"entries": []})
    data = await _call_ok("elements_list_all_input_tables", {})
    assert data["status"] == "partial_success"
    assert data["errors"][0]["error"]["message"] == "boom Authorization: ***REDACTED***"
    _no_leak(data, caplog)


@pytest.mark.asyncio
async def test_elements_scan_non_api_exception_does_not_stop_batch(api: AsyncMock) -> None:
    api.list_all_workbooks = AsyncMock(return_value=[{"workbookId": "wb1", "name": "A"}])
    api.list_workbook_pages = AsyncMock(return_value={"entries": [{"pageId": "p1"}, {"pageId": "p2"}]})
    api.list_workbook_page_elements = AsyncMock(
        side_effect=[ValueError("bad page"), {"entries": [{"type": "input-table", "elementId": "e2"}]}]
    )
    data = await _call_ok("elements_list_all_input_tables", {})
    assert api.list_workbook_page_elements.await_count == 2
    assert data["total_input_tables"] == 1
    page_errors = data["results"][0]["result"]["page_errors"]
    assert [e["id"] for e in page_errors] == ["p1"]
    _assert_item_errors(page_errors)


@pytest.mark.asyncio
async def test_datasets_tenant_sync_runtime_error_is_redacted(api: AsyncMock, caplog: pytest.LogCaptureFixture) -> None:
    api.list_tenants = AsyncMock(
        return_value={"entries": [{"orgId": "t1", "name": "T1"}, {"orgId": "t2", "name": "T2"}]}
    )

    async def for_tenant(org_id: str) -> AsyncMock:
        if org_id == "t1":
            raise RuntimeError(f"boom {_LEAK}")
        return _tenant_client(None)

    api.for_tenant = AsyncMock(side_effect=for_tenant)
    data = await _call_ok("datasets_bulk_sync_tenant_connections", {"dry_run": False})
    assert data["status"] == "partial_success"
    assert data["errors"][0]["error"]["message"] == "boom Authorization: ***REDACTED***"
    _no_leak(data, caplog)


@pytest.mark.asyncio
async def test_datasets_connection_non_api_exception_does_not_stop_batch(
    api: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    api.list_tenants = AsyncMock(return_value={"entries": [{"orgId": "t1", "name": "T1"}]})
    tc = AsyncMock()
    tc.list_connections = AsyncMock(return_value={"entries": [{"connectionId": "c1"}, {"connectionId": "c2"}]})
    tc.sync_connection = AsyncMock(side_effect=[RuntimeError(f"boom {_LEAK}"), {}])
    api.for_tenant = AsyncMock(return_value=tc)
    data = await _call_ok("datasets_bulk_sync_tenant_connections", {"dry_run": False})
    assert tc.sync_connection.await_count == 2
    assert data["status"] == "success"
    tenant = data["results"][0]["result"]
    _assert_batch_shape(tenant, "synced")
    assert tenant["synced_count"] == 1
    assert tenant["errors"][0]["error"]["message"] == "boom Authorization: ***REDACTED***"
    _no_leak(data, caplog)


# ─── the redacted error is raised after the except block: no __context__ ──────


def _chain_text(exc: BaseException) -> str:
    """Everything reachable from ``exc`` through ``__cause__`` and ``__context__``."""
    parts: list[str] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        parts.append(repr(cur))
        cur = cur.__cause__ or cur.__context__
    return "\n".join(parts)


@pytest.mark.asyncio
async def test_decorator_bearer_runtime_error_is_not_on_context() -> None:
    @sigma_tool
    async def handler() -> str:
        raise RuntimeError(f"boom {_LEAK}")

    with pytest.raises(ToolError) as exc_info:
        await handler()
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert _BEARER not in _chain_text(exc_info.value)
    assert _BEARER not in str(exc_info.value)


@pytest.mark.asyncio
async def test_decorator_bearer_api_error_is_not_on_context() -> None:
    @sigma_tool
    async def handler() -> str:
        raise SigmaAPIError(502, "/v2/members", "GET", detail=f"upstream echoed {_LEAK}")

    with pytest.raises(ToolError) as exc_info:
        await handler()
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert _BEARER not in _chain_text(exc_info.value)


@pytest.mark.asyncio
async def test_batch_failed_bearer_runtime_error_is_not_on_context(api: AsyncMock) -> None:
    api.auto_paginate = AsyncMock(return_value=_members(2))
    api.deactivate_member = AsyncMock(side_effect=RuntimeError(f"boom {_LEAK}"))
    with pytest.raises(ToolError) as exc_info:
        await server.sigma_bulk_deactivate_members("Test", dry_run=False, confirm=True)
    assert json.loads(str(exc_info.value))["error"]["type"] == "batch_failed"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert _BEARER not in _chain_text(exc_info.value)
    assert _BEARER not in str(exc_info.value)


# ─── batch items: cancellation is not an item failure ─────────────────────────
# One test per per-item catch: admin.py bulk_deactivate (sequential loop), workbooks.py reassign
# (sequential loop), elements.py scan pages stage and elements stage (gathered tasks), datasets.py
# tenant level and connection level (gathered tasks with a nested loop). Each catch is
# ``except Exception``; widening it to ``BaseException`` would swallow CancelledError as a failed item.


def _assert_cancel_not_recorded(caplog: pytest.LogCaptureFixture) -> None:
    assert "Batch item failed" not in caplog.text


@pytest.mark.asyncio
async def test_admin_bulk_deactivate_cancellation_propagates(api: AsyncMock, caplog: pytest.LogCaptureFixture) -> None:
    api.auto_paginate = AsyncMock(return_value=_members(3))
    api.deactivate_member = AsyncMock(side_effect=[200, asyncio.CancelledError(), 200])
    with pytest.raises(asyncio.CancelledError):
        await server.sigma_bulk_deactivate_members("Test", dry_run=False, confirm=True)
    assert api.deactivate_member.await_count == 2  # the call stopped at the cancelled item
    _assert_cancel_not_recorded(caplog)


@pytest.mark.asyncio
async def test_workbooks_reassign_cancellation_propagates(api: AsyncMock, caplog: pytest.LogCaptureFixture) -> None:
    api.search_members = AsyncMock(
        side_effect=[{"entries": [{"memberId": "old-id"}]}, {"entries": [{"memberId": "new-id"}]}]
    )
    api.get = AsyncMock(return_value=_owned_files(3))
    api.update_file = AsyncMock(side_effect=[{}, asyncio.CancelledError(), {}])
    with pytest.raises(asyncio.CancelledError):
        await server.sigma_reassign_workbook_ownership("old@example.com", "new@example.com", dry_run=False)
    assert api.update_file.await_count == 2
    _assert_cancel_not_recorded(caplog)


@pytest.mark.asyncio
async def test_elements_scan_pages_cancellation_propagates(api: AsyncMock, caplog: pytest.LogCaptureFixture) -> None:
    api.list_all_workbooks = AsyncMock(return_value=[{"workbookId": "wb1", "name": "A"}])
    api.list_workbook_pages = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await server.sigma_list_all_input_tables()
    api.list_workbook_page_elements.assert_not_called()
    _assert_cancel_not_recorded(caplog)


@pytest.mark.asyncio
async def test_elements_scan_elements_cancellation_propagates(api: AsyncMock, caplog: pytest.LogCaptureFixture) -> None:
    api.list_all_workbooks = AsyncMock(return_value=[{"workbookId": "wb1", "name": "A"}])
    api.list_workbook_pages = AsyncMock(return_value={"entries": [{"pageId": "p1"}, {"pageId": "p2"}]})
    api.list_workbook_page_elements = AsyncMock(side_effect=[asyncio.CancelledError(), {"entries": []}])
    with pytest.raises(asyncio.CancelledError):
        await server.sigma_list_all_input_tables()
    assert api.list_workbook_page_elements.await_count == 1
    _assert_cancel_not_recorded(caplog)


@pytest.mark.asyncio
async def test_datasets_tenant_cancellation_propagates(api: AsyncMock, caplog: pytest.LogCaptureFixture) -> None:
    api.list_tenants = AsyncMock(return_value={"entries": [{"orgId": "t1", "name": "T1"}]})
    api.for_tenant = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await server.sigma_bulk_sync_tenant_connections(dry_run=False)
    _assert_cancel_not_recorded(caplog)


@pytest.mark.asyncio
async def test_datasets_connection_cancellation_propagates(api: AsyncMock, caplog: pytest.LogCaptureFixture) -> None:
    api.list_tenants = AsyncMock(return_value={"entries": [{"orgId": "t1", "name": "T1"}]})
    tc = AsyncMock()
    tc.list_connections = AsyncMock(return_value={"entries": [{"connectionId": "c1"}, {"connectionId": "c2"}]})
    tc.sync_connection = AsyncMock(side_effect=[asyncio.CancelledError(), {}])
    api.for_tenant = AsyncMock(return_value=tc)
    with pytest.raises(asyncio.CancelledError):
        await server.sigma_bulk_sync_tenant_connections(dry_run=False)
    assert tc.sync_connection.await_count == 1
    tc.aclose.assert_awaited()  # the finally still closes the tenant client
    _assert_cancel_not_recorded(caplog)
