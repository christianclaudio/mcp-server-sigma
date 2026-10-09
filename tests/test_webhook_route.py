"""HTTP webhook ingest route (#25): registered only on HTTP transports with a usable path secret."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastmcp import Client, FastMCP

from sigma_mcp import server as srv
from sigma_mcp import webhooks
from sigma_mcp.webhooks import (
    MAX_WEBHOOK_BODY_BYTES,
    MIN_WEBHOOK_SECRET_LENGTH,
    WEBHOOK_BUFFER_SIZE,
    WEBHOOK_ROUTE_PREFIX,
    clear_webhook_buffer,
    get_recent_webhooks,
    record_webhook_event,
    register_webhook_route,
)

SECRET = "rT7-Lq2_xZ9vB4nM8kJ3hG6fD1sA0pWy5eUc"
URL = f"{WEBHOOK_ROUTE_PREFIX}/{SECRET}"
WRONG_URL = f"{WEBHOOK_ROUTE_PREFIX}/{'0' * len(SECRET)}"
JSON = {"Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def _reset_webhooks() -> None:
    clear_webhook_buffer()


def _http_client(server: FastMCP) -> httpx.AsyncClient:
    app = server.streamable_http_app(allowed_hosts=["testserver"])  # type: ignore[attr-defined]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def _server_with_route() -> FastMCP:
    server = srv.create_server()
    assert register_webhook_route(server, SECRET) is True
    return server


@pytest.mark.asyncio
async def test_event_at_secret_path_reaches_buffer_tool_and_resource() -> None:
    server = _server_with_route()
    body = json.dumps({"event_type": "export_completed", "exportId": "exp-1"}).encode()
    async with _http_client(server) as http:
        res = await http.post(URL, content=body, headers=JSON)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "accepted"
    assert data["event_id"].startswith("evt_")
    assert "status_code" not in data

    recent = get_recent_webhooks()
    assert [e["payload"]["exportId"] for e in recent] == ["exp-1"]

    async with Client(server) as mcp_client:
        tool_res = await mcp_client.call_tool("admin_list_recent_webhooks", {})
        listed = json.loads(tool_res.content[0].text)  # type: ignore[union-attr]
        assert listed["count"] == 1
        assert listed["events"][0]["event_type"] == "export_completed"
    # The resource function behind admin://webhooks/recent reads the same buffer.
    assert json.loads(srv.resource_webhooks_recent())[0]["payload"]["exportId"] == "exp-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        WEBHOOK_ROUTE_PREFIX,
        WEBHOOK_ROUTE_PREFIX + "/",
        f"{WEBHOOK_ROUTE_PREFIX}/{SECRET[:-1]}",
        f"{WEBHOOK_ROUTE_PREFIX}/{SECRET}x",
        f"{WEBHOOK_ROUTE_PREFIX}/{SECRET.swapcase()}",
        f"{WEBHOOK_ROUTE_PREFIX}/{'0' * len(SECRET)}",
        f"{WEBHOOK_ROUTE_PREFIX}/{SECRET}/extra",
        f"/webhooks/{SECRET}",
    ],
)
async def test_wrong_or_missing_secret_is_404_and_buffers_nothing(path: str) -> None:
    async with _http_client(_server_with_route()) as http:
        res = await http.post(path, content=b'{"event_type": "x"}')
    assert res.status_code == 404
    assert get_recent_webhooks() == []


@pytest.mark.asyncio
async def test_wrong_secret_is_indistinguishable_from_unknown_path() -> None:
    async with _http_client(_server_with_route()) as http:
        wrong = await http.post(f"{WEBHOOK_ROUTE_PREFIX}/{'0' * len(SECRET)}", content=b"{}")
        unknown = await http.post("/no/such/path", content=b"{}")
    assert (wrong.status_code, wrong.text) == (unknown.status_code, unknown.text) == (404, "Not Found")


@pytest.mark.asyncio
async def test_signature_headers_are_not_required_or_honoured() -> None:
    """No Sigma signature scheme is documented, so a header never authenticates a request."""
    async with _http_client(_server_with_route()) as http:
        res = await http.post(
            WEBHOOK_ROUTE_PREFIX,
            content=b"{}",
            headers={"X-Sigma-Signature": "sha256=" + "0" * 64, "Authorization": f"Bearer {SECRET}"},
        )
        ok = await http.post(URL, content=b'{"type": "plain"}', headers=JSON)
    assert res.status_code == 404
    assert ok.status_code == 200
    assert get_recent_webhooks()[0]["event_type"] == "plain"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "error"),
    [
        (b"not json", "Invalid JSON payload"),
        (b"\xff\xfe", "Invalid JSON payload"),
        (b"[1, 2, 3]", "Payload must be a JSON object"),
    ],
)
async def test_bad_payload_is_400(body: bytes, error: str) -> None:
    async with _http_client(_server_with_route()) as http:
        res = await http.post(URL, content=body, headers=JSON)
    assert res.status_code == 400
    assert res.json() == {"error": error}
    assert get_recent_webhooks() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["application/json", "image/png", None])
async def test_oversized_body_is_413(content_type: str | None) -> None:
    body = b"{" + b" " * MAX_WEBHOOK_BODY_BYTES + b"}"
    headers = {"Content-Type": content_type} if content_type else {}
    async with _http_client(_server_with_route()) as http:
        res = await http.post(URL, content=body, headers=headers)
    assert res.status_code == 413
    assert res.json() == {"error": "Payload too large"}
    assert get_recent_webhooks() == []


@pytest.mark.asyncio
async def test_oversized_body_with_wrong_secret_is_404() -> None:
    """The secret is checked before any of the body is read."""
    body = b"{" + b" " * MAX_WEBHOOK_BODY_BYTES + b"}"
    async with _http_client(_server_with_route()) as http:
        res = await http.post(f"{WEBHOOK_ROUTE_PREFIX}/{'0' * len(SECRET)}", content=body)
    assert res.status_code == 404


async def _not_found_reference(http: httpx.AsyncClient, method: str) -> httpx.Response:
    return await http.request(method, WRONG_URL, content=b"{}")


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def test_non_post_with_correct_secret_matches_wrong_secret_404(method: str) -> None:
    """A method probe must not tell the real ingest path from a wrong secret or an unknown path."""
    async with _http_client(_server_with_route()) as http:
        right = await http.request(method, URL, content=b'{"event_type": "x"}', headers=JSON)
        wrong_post = await http.post(WRONG_URL, content=b"{}", headers=JSON)
        wrong_same_method = await _not_found_reference(http, method)
        unknown = await http.request(method, "/no/such/path", content=b"{}")
    for other in (wrong_same_method, unknown):
        assert (right.status_code, right.content, right.headers.multi_items()) == (
            other.status_code,
            other.content,
            other.headers.multi_items(),
        )
    # A HEAD response carries no body; status and headers still match a wrong-secret POST.
    assert (right.status_code, right.headers.multi_items()) == (404, wrong_post.headers.multi_items())
    assert right.content == (b"" if method == "HEAD" else wrong_post.content)
    assert get_recent_webhooks() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_prefix_and_trailing_slash_paths_are_404_not_redirects(method: str) -> None:
    """Neither the bare prefix nor a trailing slash after a (right or wrong) secret redirects."""
    async with _http_client(_server_with_route()) as http:
        unknown = await http.request(method, "/no/such/path", content=b"{}", headers=JSON)
        for path in (WEBHOOK_ROUTE_PREFIX, WEBHOOK_ROUTE_PREFIX + "/", f"{URL}/", f"{WRONG_URL}/"):
            res = await http.request(method, path, content=b"{}", headers=JSON)
            assert (res.status_code, res.content, res.headers.multi_items()) == (
                unknown.status_code,
                unknown.content,
                unknown.headers.multi_items(),
            ), path
    assert unknown.status_code == 404
    assert get_recent_webhooks() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        ("text/csv", b"region,revenue\nEast,1200\nWest,980\n"),
        ("text/csv; charset=utf-8", "r\u00e9gion,total\nNord,5\n".encode()),
        ("application/pdf", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"),
        ("image/png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"),
        (None, b"region,revenue\nEast,1200\n"),
        ("text/plain", b'{"event_type": "looks_like_json"}'),
    ],
)
async def test_non_json_body_is_recorded_as_metadata_without_the_body(
    caplog: pytest.LogCaptureFixture, content_type: str | None, body: bytes
) -> None:
    headers = {"Content-Type": content_type} if content_type else {}
    with caplog.at_level(logging.DEBUG, logger="sigma_mcp"):
        async with _http_client(_server_with_route()) as http:
            res = await http.post(URL, content=body, headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "accepted"
    [event] = get_recent_webhooks()
    assert event["event_type"] == "non_json_payload"
    payload = event["payload"]
    assert set(payload) == {"content_type", "size_bytes", "received_at", "body_stored"}
    assert payload["content_type"] == (content_type or "")
    assert payload["size_bytes"] == len(body)
    assert payload["body_stored"] is False
    received = datetime.fromisoformat(payload["received_at"])
    assert received.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - received).total_seconds()) < 60
    # The body is neither buffered nor logged.
    dumped = json.dumps(get_recent_webhooks())
    for fragment in (body[:12].decode("latin-1"), body.decode("utf-8", errors="replace")[:12]):
        assert fragment not in dumped
    assert body.decode("utf-8", errors="replace")[:12] not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["image/png", "text/csv", None])
async def test_empty_body_with_any_content_type_is_general_event(content_type: str | None) -> None:
    """An empty body behaves as before (a general_event), and is not mistaken for a file."""
    headers = {"Content-Type": content_type} if content_type else {}
    async with _http_client(_server_with_route()) as http:
        res = await http.post(URL, content=b"", headers=headers)
        csv = await http.post(URL, content=b"a,b\n", headers={"Content-Type": "text/csv"})
    assert res.status_code == csv.status_code == 200
    assert [e["event_type"] for e in get_recent_webhooks()] == ["non_json_payload", "general_event"]
    assert get_recent_webhooks()[1]["payload"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "json_type", ["application/json", "application/json; charset=utf-8", "application/vnd.api+json", "APPLICATION/JSON"]
)
@pytest.mark.parametrize(
    ("body", "error"),
    [
        (b"not json", "Invalid JSON payload"),
        (b"\xff\xfe", "Invalid JSON payload"),
        (b"[1, 2, 3]", "Payload must be a JSON object"),
    ],
)
async def test_bad_json_is_400_only_under_a_json_content_type(json_type: str, body: bytes, error: str) -> None:
    """The same bytes are 400 under a JSON content type and a metadata event under any other."""
    async with _http_client(_server_with_route()) as http:
        as_json = await http.post(URL, content=body, headers={"Content-Type": json_type})
        as_text = await http.post(URL, content=body, headers={"Content-Type": "text/plain"})
    assert as_json.status_code == 400
    assert as_json.json() == {"error": error}
    assert as_text.status_code == 200
    assert [e["event_type"] for e in get_recent_webhooks()] == ["non_json_payload"]


@pytest.mark.parametrize(
    ("content_type", "is_json"),
    [
        ("application/json", True),
        ("Application/JSON ; charset=utf-8", True),
        ("application/vnd.api+json", True),
        ("application/problem+json; charset=utf-8", True),
        ("text/json", False),
        ("text/csv", False),
        ("application/pdf", False),
        ("", False),
    ],
)
def test_is_json_content_type(content_type: str, is_json: bool) -> None:
    from sigma_mcp.webhooks import is_json_content_type

    assert is_json_content_type(content_type) is is_json


@pytest.mark.asyncio
async def test_no_secret_registers_no_route(caplog: pytest.LogCaptureFixture) -> None:
    server = srv.create_server()
    with caplog.at_level(logging.INFO, logger="sigma_mcp.webhooks"):
        assert register_webhook_route(server, "") is False
    assert any("SIGMA_WEBHOOK_SECRET is not set" in r.getMessage() for r in caplog.records)
    async with _http_client(server) as http:
        res = await http.post(URL, content=b"{}")
    assert res.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "weak",
    ["a" * (MIN_WEBHOOK_SECRET_LENGTH - 1), "has/slash-" + "a" * MIN_WEBHOOK_SECRET_LENGTH, "sp ace" + "a" * 40],
)
async def test_weak_secret_registers_no_route(caplog: pytest.LogCaptureFixture, weak: str) -> None:
    server = srv.create_server()
    with caplog.at_level(logging.INFO, logger="sigma_mcp.webhooks"):
        assert register_webhook_route(server, weak) is False
    assert any("must be at least" in r.getMessage() for r in caplog.records)
    assert weak not in caplog.text
    async with _http_client(server) as http:
        res = await http.post(f"{WEBHOOK_ROUTE_PREFIX}/{weak}", content=b"{}")
    assert res.status_code == 404


def test_access_log_redacts_secret(caplog: pytest.LogCaptureFixture) -> None:
    _server_with_route()
    _server_with_route()  # registering twice adds no second filter
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, webhooks._SecretRedactingFilter) and f.secret == SECRET for f in access.filters) == 1
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "POST", URL, "1.1", 200)
        access.info(f"plain message for {URL}")
        access.info("args %s", 5)
    assert SECRET not in caplog.text
    assert f"{WEBHOOK_ROUTE_PREFIX}/***REDACTED***" in caplog.text
    assert "args 5" in caplog.text


def test_buffer_is_bounded() -> None:
    for i in range(WEBHOOK_BUFFER_SIZE + 5):
        record_webhook_event("bulk", {"i": i})
    assert len(webhooks._WEBHOOK_BUFFER) == WEBHOOK_BUFFER_SIZE
    recent = get_recent_webhooks(limit=WEBHOOK_BUFFER_SIZE + 50)
    assert len(recent) == WEBHOOK_BUFFER_SIZE
    assert recent[0]["payload"]["i"] == WEBHOOK_BUFFER_SIZE + 4


async def _route_status(server: FastMCP) -> int:
    async with _http_client(server) as http:
        res = await http.post(URL, content=b'{"event_type": "probe"}', headers=JSON)
    return res.status_code


def _run_main(monkeypatch: pytest.MonkeyPatch, transport: str, secret: str | None) -> FastMCP:
    server = srv.create_server()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(srv, "mcp", server)
    monkeypatch.setattr(FastMCP, "run", lambda self, **kwargs: calls.append(kwargs))
    monkeypatch.delenv("SIGMA_MCP_PROFILE", raising=False)
    if secret is None:
        monkeypatch.delenv("SIGMA_WEBHOOK_SECRET", raising=False)
        monkeypatch.setattr(srv.settings, "WEBHOOK_SECRET", "")
    else:
        monkeypatch.setenv("SIGMA_WEBHOOK_SECRET", secret)
    monkeypatch.setattr("sys.argv", ["sigma-mcp", "--transport", transport])
    srv.main()
    assert calls and calls[0]["transport"] == transport
    return server


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
async def test_main_registers_route_on_http_transports(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, transport: str
) -> None:
    with caplog.at_level(logging.DEBUG):
        server = _run_main(monkeypatch, transport, SECRET)
    # Checked before the request below, whose own httpx client log line holds the URL.
    assert SECRET not in caplog.text
    assert await _route_status(server) == 200


@pytest.mark.asyncio
async def test_main_never_registers_route_on_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _run_main(monkeypatch, "stdio", SECRET)
    assert await _route_status(server) == 404


@pytest.mark.asyncio
async def test_main_http_without_secret_registers_no_route(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _run_main(monkeypatch, "streamable-http", None)
    assert await _route_status(server) == 404


def test_webhook_secret_falls_back_to_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIGMA_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(srv.settings, "WEBHOOK_SECRET", "from-settings")
    assert srv._webhook_secret() == "from-settings"
    monkeypatch.setenv("SIGMA_WEBHOOK_SECRET", "from-env")
    assert srv._webhook_secret() == "from-env"
