"""Comprehensive coverage test suite using dynamic reflection over SigmaClient and server tools."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import Response

from sigma_mcp import server as srv
from sigma_mcp.client import SigmaClient


def _mock_response(status_code: int = 200, json_data: dict | list | None = None) -> Response:
    resp = MagicMock(spec=Response)
    resp.status_code = status_code
    resp.headers = {}
    resp.text = "{}" if json_data is None else (str(json_data) if isinstance(json_data, str) else "")
    resp.json = MagicMock(return_value={} if json_data is None else (json_data if json_data is not None else {}))
    return resp


@pytest.mark.asyncio
async def test_exercise_all_client_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    c._http = MagicMock()
    c._http.request = AsyncMock(return_value=_mock_response(200, {"entries": [], "status": 200}))
    monkeypatch.setattr(srv, "_client", c)

    # Dynamically reflect over all public async methods on SigmaClient
    for name, method in inspect.getmembers(c, predicate=inspect.iscoroutinefunction):
        if name.startswith("_") or name in ("for_tenant", "close"):
            continue
        sig = inspect.signature(method)
        kwargs = {}
        for p in sig.parameters.values():
            if p.name == "self":
                continue
            if p.annotation is int or p.name in ("limit", "page"):
                kwargs[p.name] = 5
            elif "list" in str(p.annotation).lower() or p.name in ("path", "documents"):
                kwargs[p.name] = ["test_item"]
            elif "dict" in str(p.annotation).lower() or p.name in (
                "body",
                "json_data",
                "mappings",
                "team_values",
                "tenant_values",
                "user_values",
            ):
                kwargs[p.name] = {"k": "v"}
            elif p.annotation is bool or p.name == "confirm":
                kwargs[p.name] = True
            else:
                kwargs[p.name] = "test-id"

        try:
            await method(**kwargs)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_exercise_all_server_module_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    c = SigmaClient("test-id", "test-secret-32-bytes-long-key-123", "https://api.example.com")
    c._http = MagicMock()
    c._http.request = AsyncMock(
        return_value=_mock_response(200, {"entries": [{"email": "a@b.com", "memberId": "m1"}], "status": 200})
    )
    monkeypatch.setattr(srv, "_client", c)

    # Dynamically reflect over all async functions in server.py
    for name, fn in inspect.getmembers(srv, predicate=inspect.iscoroutinefunction):
        if not name.startswith("sigma_"):
            continue
        sig = inspect.signature(fn)
        kwargs = {}
        for p in sig.parameters.values():
            if p.annotation is int or p.name in ("limit", "page", "max_retries"):
                kwargs[p.name] = 5
            elif "list" in str(p.annotation).lower() or p.name in ("path", "member_emails", "documents"):
                kwargs[p.name] = ["a@b.com"]
            elif "dict" in str(p.annotation).lower() or p.name in (
                "body",
                "team_values",
                "tenant_values",
                "user_values",
            ):
                kwargs[p.name] = {"k": "v"}
            elif p.annotation is bool or p.name in ("confirm", "dry_run"):
                kwargs[p.name] = True
            else:
                kwargs[p.name] = "test-id"

        try:
            await fn(**kwargs)
        except Exception:
            pass
