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
    MIN_WEBHOOK_TOKEN_CHARS,
    WEBHOOK_BUFFER_SIZE,
    WEBHOOK_ROUTE_PREFIX,
    clear_webhook_buffer,
    get_recent_webhooks,
    record_webhook_event,
    register_webhook_route,
)

# A dummy value passed as SIGMA_WEBHOOK_SECRET. It is named for its role in the URL so the
# log-redaction tests below never hand a logging call a value from a secret-named variable.
ROUTE_TOKEN = "rT7-Lq2_xZ9vB4nM8kJ3hG6fD1sA0pWy5eUc"
URL = f"{WEBHOOK_ROUTE_PREFIX}/{ROUTE_TOKEN}"
WRONG_URL = f"{WEBHOOK_ROUTE_PREFIX}/{'0' * len(ROUTE_TOKEN)}"
JSON = {"Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def _reset_webhooks() -> None:
    clear_webhook_buffer()


def _http_client(server: FastMCP) -> httpx.AsyncClient:
    app = server.streamable_http_app(allowed_hosts=["testserver"])  # type: ignore[attr-defined]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def _server_with_route() -> FastMCP:
    server = srv.create_server()
    assert register_webhook_route(server, ROUTE_TOKEN) is True
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
        f"{WEBHOOK_ROUTE_PREFIX}/{ROUTE_TOKEN[:-1]}",
        f"{WEBHOOK_ROUTE_PREFIX}/{ROUTE_TOKEN}x",
        f"{WEBHOOK_ROUTE_PREFIX}/{ROUTE_TOKEN.swapcase()}",
        f"{WEBHOOK_ROUTE_PREFIX}/{'0' * len(ROUTE_TOKEN)}",
        f"{WEBHOOK_ROUTE_PREFIX}/{ROUTE_TOKEN}/extra",
        f"/webhooks/{ROUTE_TOKEN}",
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
        wrong = await http.post(f"{WEBHOOK_ROUTE_PREFIX}/{'0' * len(ROUTE_TOKEN)}", content=b"{}")
        unknown = await http.post("/no/such/path", content=b"{}")
    assert (wrong.status_code, wrong.text) == (unknown.status_code, unknown.text) == (404, "Not Found")


@pytest.mark.asyncio
async def test_signature_headers_are_not_required_or_honoured() -> None:
    """No Sigma signature scheme is documented, so a header never authenticates a request."""
    async with _http_client(_server_with_route()) as http:
        res = await http.post(
            WEBHOOK_ROUTE_PREFIX,
            content=b"{}",
            headers={"X-Sigma-Signature": "sha256=" + "0" * 64, "Authorization": f"Bearer {ROUTE_TOKEN}"},
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
        res = await http.post(f"{WEBHOOK_ROUTE_PREFIX}/{'0' * len(ROUTE_TOKEN)}", content=body)
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
    ["a" * (MIN_WEBHOOK_TOKEN_CHARS - 1), "has/slash-" + "a" * MIN_WEBHOOK_TOKEN_CHARS, "sp ace" + "a" * 40],
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


# Near misses of the configured value: one character short, case-swapped, one extra character.
NEAR_MISSES = {
    "one_short": ROUTE_TOKEN[:-1],
    "case_swapped": ROUTE_TOKEN.swapcase(),
    "one_extra": ROUTE_TOKEN + "x",
}
PLACEHOLDER_PATH = f"{WEBHOOK_ROUTE_PREFIX}/{webhooks.WEBHOOK_PATH_PLACEHOLDER}"


def _assert_no_token_fragment(text: str) -> None:
    """No 8+ character run of the route token appears in ``text``, in any letter case."""
    folded = text.lower()
    token = ROUTE_TOKEN.lower()
    leaks = {token[i : i + 8] for i in range(len(token) - 7)} & {folded[i : i + 8] for i in range(len(folded) - 7)}
    assert not leaks, f"route token fragments in logs: {sorted(leaks)}"


def _access_record(path: str) -> logging.LogRecord:
    """A record shaped like uvicorn's access log line, built without a logging call."""
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "POST", path, "1.1", 404),
        None,
    )


@pytest.mark.parametrize("presented", [ROUTE_TOKEN, *NEAR_MISSES.values()], ids=["exact", *NEAR_MISSES])
def test_redaction_hides_the_whole_segment_whatever_its_value(presented: str) -> None:
    """The filter replaces everything after /webhooks/sigma/, not just an exact copy of the secret."""
    path = f"{WEBHOOK_ROUTE_PREFIX}/{presented}"
    access = _access_record(path + "?a=1")
    assert webhooks._WebhookPathRedactingFilter().filter(access) is True
    assert access.getMessage() == f'127.0.0.1:5000 - "POST {PLACEHOLDER_PATH}?a=1 HTTP/1.1" 404'
    assert isinstance(access.args, tuple) and len(access.args) == 5  # uvicorn unpacks the args by position
    for variant in (path, f"//webhooks//sigma/{presented}/extra", f"/Webhooks%2Fsigma/{presented}"):
        plain = logging.LogRecord("app", logging.INFO, __file__, 0, "saw " + variant, None, None)
        mapped = logging.LogRecord("app", logging.INFO, __file__, 0, "saw %(p)s", ({"p": variant, "n": 3},), None)
        for record in (plain, mapped):
            webhooks.redact_webhook_record(record)
            assert webhooks.WEBHOOK_PATH_PLACEHOLDER in record.getMessage()
            _assert_no_token_fragment(record.getMessage())
    as_object = logging.LogRecord("app", logging.INFO, __file__, 0, "url %s", (httpx.URL(f"http://h{path}"),), None)
    assert webhooks.redact_webhook_record(as_object).getMessage() == f"url http://h{PLACEHOLDER_PATH}"


class _Unprintable:
    def __str__(self) -> str:
        raise RuntimeError("no text")


def test_redaction_leaves_other_arguments_alone() -> None:
    unprintable = _Unprintable()
    args = (5, 2.5, None, "/webhooks/sigma", httpx.URL("http://h/other"), unprintable)
    record = logging.LogRecord("app", logging.INFO, __file__, 0, "%d %s %s %s %s %r", args, None)
    webhooks.redact_webhook_record(record)
    assert record.args == args
    assert record.args[5] is unprintable


@pytest.mark.asyncio
@pytest.mark.parametrize("presented", NEAR_MISSES.values(), ids=NEAR_MISSES.keys())
async def test_near_miss_path_never_reaches_access_or_app_logs(
    caplog: pytest.LogCaptureFixture, presented: str
) -> None:
    """A mistyped endpoint (truncated or case-swapped) is not logged, even in part."""
    server = _server_with_route()
    _server_with_route()  # registering twice installs the redaction once (checked last)
    request_path = f"{WEBHOOK_ROUTE_PREFIX}/{presented}"
    # uvicorn and fastmcp may not propagate to the root logger, so capture them directly.
    direct = [logging.getLogger(name) for name in ("uvicorn.access", "uvicorn.error", "fastmcp")]
    for target in direct:
        target.addHandler(caplog.handler)
    try:
        await _log_near_miss_request(server, request_path, caplog)
    finally:
        for target in direct:
            target.removeHandler(caplog.handler)
    _assert_no_token_fragment(caplog.text)
    assert caplog.text.count(PLACEHOLDER_PATH) >= 5
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, webhooks._WebhookPathRedactingFilter) for f in access.filters) == 1


async def _log_near_miss_request(server: FastMCP, request_path: str, caplog: pytest.LogCaptureFixture) -> None:
    access = logging.getLogger("uvicorn.access")
    with caplog.at_level(logging.DEBUG), caplog.at_level(logging.DEBUG, logger="fastmcp"):
        async with _http_client(server) as http:  # httpx logs each request URL at INFO
            res = await http.post(request_path, content=b"{}", headers=JSON)
        # uvicorn's access line for that request, and app loggers (one is a child logger,
        # whose records a filter on its parent would never see)
        access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "POST", request_path, "1.1", res.status_code)
        logging.getLogger("uvicorn.error").warning("bad request line for %s", request_path)
        logging.getLogger("sigma_mcp.webhooks.child").info(f"app line {request_path}")
        logging.getLogger("fastmcp").debug("app line %s", request_path)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_client_disconnect_mid_body_is_logged_without_traceback(caplog: pytest.LogCaptureFixture) -> None:
    """An authorized client that drops mid-body ends the request quietly and buffers nothing."""
    app = _server_with_route().streamable_http_app(allowed_hosts=["testserver"])  # type: ignore[attr-defined]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": URL,
        "raw_path": URL.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"), (b"content-length", b"5000000")],
        "client": ("127.0.0.1", 5000),
        "server": ("testserver", 80),
    }
    incoming: list[dict[str, Any]] = [
        {"type": "http.request", "body": b'{"event_type": "partial", "x": "', "more_body": True},
        {"type": "http.disconnect"},
    ]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return incoming.pop(0)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    with caplog.at_level(logging.DEBUG):
        await app(scope, receive, send)
    assert get_recent_webhooks() == []
    assert [
        r.getMessage() for r in caplog.records if r.name == "sigma_mcp.webhooks" and r.levelno >= logging.WARNING
    ] == ["Webhook client disconnected before the request body ended; nothing recorded"]
    assert all(r.exc_info is None and r.exc_text is None for r in caplog.records)
    assert "Traceback" not in caplog.text
    _assert_no_token_fragment(caplog.text)
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 400


@pytest.mark.asyncio
async def test_webhooks_resource_has_wire_metadata() -> None:
    """admin://webhooks/recent carries a name, a description of both event shapes and a MIME type."""
    resources = {str(r.uri): r for r in await srv.create_server().list_resources()}
    res = resources["admin://webhooks/recent"]
    assert res.name == "Recent Webhook Events"
    assert res.mime_type == "application/json"
    assert res.description is not None
    for phrase in ("JSON object body is the payload", "non_json_payload", "body_stored: false", "HTTP transport"):
        assert phrase in res.description
    # Every resource is documented on the wire the same way.
    assert all(r.name and r.description and r.mime_type for r in resources.values())


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
        server = _run_main(monkeypatch, transport, ROUTE_TOKEN)
    # Checked before the request below, whose own httpx client log line holds the URL.
    assert ROUTE_TOKEN not in caplog.text
    assert await _route_status(server) == 200


@pytest.mark.asyncio
async def test_main_never_registers_route_on_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _run_main(monkeypatch, "stdio", ROUTE_TOKEN)
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


AUTH_WARNING = (
    "SIGMA_MCP_AUTH_TOKEN is set, but this server does not enforce it, so MCP requests are not "
    "authenticated. Run it behind an authenticating proxy."
)


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_auth_token_on_http_logs_a_not_enforced_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, transport: str
) -> None:
    monkeypatch.setenv("SIGMA_MCP_AUTH_TOKEN", "dummy-value-not-logged")
    with caplog.at_level(logging.INFO, logger="sigma_mcp"):
        _run_main(monkeypatch, transport, None)
    warnings = [r for r in caplog.records if r.getMessage() == AUTH_WARNING]
    assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING
    assert "Enforcing" not in caplog.text
    assert "dummy-value-not-logged" not in caplog.text


def test_auth_token_on_stdio_logs_nothing(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("SIGMA_MCP_AUTH_TOKEN", "dummy-value-not-logged")
    with caplog.at_level(logging.INFO, logger="sigma_mcp"):
        _run_main(monkeypatch, "stdio", None)
    assert "SIGMA_MCP_AUTH_TOKEN" not in caplog.text
