"""verify_workbook_spec / verify_report_spec use the create endpoints' dry run.

Sigma deprecated POST /v2/{workbooks,reports}/spec/verify; its spec names the replacement as
POST /v2/workbooks (or /v2/reports) with ``contents`` and ``dryRun: true``. A failed dry run
answers 400 with ``{dryRun, valid: false, errors, warnings}``.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from sigma_mcp.client import SigmaClient
from sigma_mcp.errors import SigmaAPIError


def _res(status: int, payload: object = None, text: str | None = None) -> httpx.Response:
    if payload is not None:
        return httpx.Response(status, json=payload)
    return httpx.Response(status, text=text or "")


@pytest.fixture
def client() -> SigmaClient:
    c = SigmaClient("cid", "csecret", "https://api.sigmacomputing.com", max_retries=0)
    c._token = "tok"
    c._token_expiry = time.time() + 3600
    c._http = AsyncMock()
    return c


CASES = [("verify_workbook_spec", "/v2/workbooks"), ("verify_report_spec", "/v2/reports")]


@pytest.mark.parametrize(("method", "path"), CASES)
async def test_posts_dry_run_to_create_endpoint(client: SigmaClient, method: str, path: str) -> None:
    client._http.request.return_value = _res(200, {"dryRun": True, "warnings": [{"summary": "w"}]})
    out = await getattr(client, method)({"name": "N", "folderId": "f", "document": {"pages": []}})
    assert out == {"valid": True, "errors": [], "warnings": [{"summary": "w"}]}
    args, kwargs = client._http.request.call_args
    assert args[0] == "POST"
    assert args[1] == f"https://api.sigmacomputing.com{path}"
    assert "spec/verify" not in args[1]
    assert kwargs["json"] == {"name": "N", "folderId": "f", "contents": {"pages": []}, "dryRun": True}


@pytest.mark.parametrize(("method", "path"), CASES)
async def test_failed_dry_run_is_a_result_not_an_error(client: SigmaClient, method: str, path: str) -> None:
    failed = {"dryRun": True, "valid": False, "errors": [{"summary": "bad"}], "warnings": []}
    client._http.request.return_value = _res(400, failed)
    out = await getattr(client, method)({"name": "N", "folderId": "f", "document": {}})
    assert out == {"valid": False, "errors": [{"summary": "bad"}], "warnings": []}


@pytest.mark.parametrize(("method", "path"), CASES)
async def test_other_400_still_raises(client: SigmaClient, method: str, path: str) -> None:
    client._http.request.return_value = _res(400, {"message": "folderId is invalid"})
    with pytest.raises(SigmaAPIError) as exc:
        await getattr(client, method)({"name": "N", "folderId": "f", "document": {}})
    assert exc.value.status_code == 400
    assert exc.value.path == path


async def test_non_json_400_raises_with_text(client: SigmaClient) -> None:
    client._http.request.return_value = _res(400, text="Bad Request")
    with pytest.raises(SigmaAPIError) as exc:
        await client.verify_workbook_spec({"name": "N", "folderId": "f"})
    assert exc.value.status_code == 400


async def test_empty_400_raises(client: SigmaClient) -> None:
    client._http.request.return_value = _res(400)
    with pytest.raises(SigmaAPIError):
        await client.verify_report_spec({"name": "N", "folderId": "f"})


async def test_500_raises(client: SigmaClient) -> None:
    client._http.request.return_value = _res(500, {"message": "boom"})
    with pytest.raises(SigmaAPIError) as exc:
        await client.verify_workbook_spec({"name": "N", "folderId": "f", "document": {}})
    assert exc.value.status_code == 500


async def test_success_without_body(client: SigmaClient) -> None:
    client._http.request.return_value = _res(200)
    out = await client.verify_workbook_spec({"name": "N", "folderId": "f", "document": {}})
    assert out == {"valid": True, "errors": [], "warnings": []}


async def test_tool_returns_failed_validation() -> None:
    from sigma_mcp import server
    from sigma_mcp.tools.workbooks import sigma_verify_report_spec

    c = SigmaClient("cid", "csecret", "https://api.sigmacomputing.com", max_retries=0)
    c._token = "tok"
    c._token_expiry = time.time() + 3600
    c._http = AsyncMock()
    c._http.request.return_value = _res(
        400, {"dryRun": True, "valid": False, "errors": [{"summary": "e"}], "warnings": [{"summary": "w"}]}
    )
    server._client = c
    try:
        out = json.loads(await sigma_verify_report_spec("R", "f", {"pages": []}))
    finally:
        server._client = None
    assert out == {"valid": False, "errors": [{"summary": "e"}], "warnings": [{"summary": "w"}]}
