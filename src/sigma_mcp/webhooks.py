"""Webhook receiver and in-memory event buffer for Sigma Computing export deliveries.

``register_webhook_route`` adds the opt-in ``POST /webhooks/sigma/{token}`` ingest route;
``server.main`` calls it only on the HTTP transports and only when ``SIGMA_WEBHOOK_SECRET``
is set to a usable value.

Sigma documents no signature or auth header for its "Export to webhook" deliveries, and the
destination lets the user set only the endpoint URL ("This feature does not currently support export to authenticated
endpoints": https://help.sigmacomputing.com/docs/export-to-webhook). So the only thing that
can prove a delivery was configured by someone holding the secret is the URL itself: the
secret is a high-entropy path segment, compared in constant time, and a wrong or missing
segment gets the same ``404`` as an unknown path. So does every method but ``POST``, with
or without the right secret.

Sigma exports CSV, JSON, PDF and PNG to webhooks. A JSON body (``application/json`` or a
``+json`` type) is parsed and buffered. Any other body is not kept: the buffer records only
its content type, size and receipt time.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import uuid
from collections import deque
from collections.abc import Callable, Coroutine, Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger("sigma_mcp.webhooks")

# In-memory buffer storing up to WEBHOOK_BUFFER_SIZE most recent webhook events (oldest dropped)
WEBHOOK_BUFFER_SIZE = 100
_WEBHOOK_BUFFER: deque[dict[str, Any]] = deque(maxlen=WEBHOOK_BUFFER_SIZE)

# Ingest route: the public prefix, the Starlette pattern with the secret segment, and the
# largest request body it reads before answering 413
WEBHOOK_ROUTE_PREFIX = "/webhooks/sigma"
WEBHOOK_ROUTE_PATH = WEBHOOK_ROUTE_PREFIX + "/{token:path}"
MAX_WEBHOOK_BODY_BYTES = 1_048_576

# Every RFC 9110 method plus PATCH is routed to the handler, which answers 404 for all but
# POST, so a method probe cannot tell the ingest path from an unknown one.
_ROUTED_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"]

# Event type and payload for a non-JSON body (CSV, PDF, PNG exports): the body is dropped
NON_JSON_EVENT_TYPE = "non_json_payload"

# The secret travels in the URL, so it must be URL-safe and long enough to be unguessable
# (``secrets.token_urlsafe(32)`` gives 43 characters).
MIN_WEBHOOK_TOKEN_CHARS = 32
_WEBHOOK_SECRET_PATTERN = re.compile(r"[A-Za-z0-9_-]+")

# Log redaction: everything after ``/webhooks/sigma/`` up to whitespace, a quote, ``?`` or
# ``#`` is replaced, whatever it is, so a near-miss of the secret (one character short,
# case-swapped, an extra segment) is hidden as well as the exact value. The prefix match is
# case-insensitive and tolerates repeated slashes and a %-encoded ``/``.
_WEBHOOK_PATH_SEGMENT = re.compile(r"(?i)(/+webhooks(?:/|%2f)+sigma(?:/|%2f)+)[^\s?#\"']+")
WEBHOOK_PATH_PLACEHOLDER = "[redacted]"

_EVENT_LISTENERS: list[Callable[[dict[str, Any]], Coroutine[Any, Any, None]]] = []


def webhook_secret_is_usable(secret: str) -> bool:
    """Return whether ``secret`` can serve as the URL path secret.

    It must be at least ``MIN_WEBHOOK_TOKEN_CHARS`` characters of ``A-Z a-z 0-9 _ -`` so it
    survives in a URL unencoded and cannot be guessed.
    """
    return len(secret) >= MIN_WEBHOOK_TOKEN_CHARS and _WEBHOOK_SECRET_PATTERN.fullmatch(secret) is not None


def verify_webhook_token(presented: str, secret: str) -> bool:
    """Compare the path segment a request presented with the secret, in constant time."""
    if not secret or not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), secret.encode("utf-8"))


def redact_webhook_paths(text: str) -> str:
    """Replace the path segment after ``/webhooks/sigma/`` in ``text`` with a placeholder."""
    return _WEBHOOK_PATH_SEGMENT.sub(r"\1" + WEBHOOK_PATH_PLACEHOLDER, text)


def _redact_value(value: object) -> object:
    """Redact one format argument; a non-string (an ``httpx.URL``, say) becomes its redacted text."""
    if value is None or isinstance(value, (int, float)):
        return value
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:
        return value
    redacted = redact_webhook_paths(text)
    return value if redacted == text else redacted


def redact_webhook_record(record: logging.LogRecord) -> logging.LogRecord:
    """Redact webhook path segments in a record's message and its %-format arguments.

    The arguments stay a tuple (or mapping) of the same shape, because uvicorn's access
    formatter unpacks them by position.
    """
    if isinstance(record.msg, str):
        record.msg = redact_webhook_paths(record.msg)
    if isinstance(record.args, tuple):
        record.args = tuple(_redact_value(a) for a in record.args)
    elif isinstance(record.args, Mapping):
        record.args = {k: _redact_value(v) for k, v in record.args.items()}
    return record


class _WebhookPathRedactingFilter(logging.Filter):
    """Logger filter form of ``redact_webhook_record`` (never drops a record)."""

    def filter(self, record: logging.LogRecord) -> bool:
        redact_webhook_record(record)
        return True


# Uvicorn's access log records the full request path, and its error log can too.
_REDACTED_LOGGERS = ("uvicorn.access", "uvicorn.error")


def _install_webhook_path_redaction() -> None:
    """Redact webhook path segments from every log record (idempotent).

    A logger filter only sees records logged on that exact logger, not ones propagated from
    its children, so the redaction is installed as a log record factory, which every
    ``Logger`` call goes through. The uvicorn loggers also get the filter, which covers a
    record handed to them that was not built by the factory.
    """
    previous = logging.getLogRecordFactory()
    if not getattr(previous, "_sigma_webhook_redaction", False):

        def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            return redact_webhook_record(previous(*args, **kwargs))

        factory._sigma_webhook_redaction = True  # type: ignore[attr-defined]
        logging.setLogRecordFactory(factory)
    for name in _REDACTED_LOGGERS:
        target = logging.getLogger(name)
        if not any(isinstance(f, _WebhookPathRedactingFilter) for f in target.filters):
            target.addFilter(_WebhookPathRedactingFilter())


def record_webhook_event(event_type: str, payload: dict[str, Any], raw_body: str = "") -> dict[str, Any]:
    """Record a structured webhook event into the in-memory log buffer."""
    event: dict[str, Any] = {
        "event_id": f"evt_{uuid.uuid4().hex[:12]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "payload": payload,
    }
    _WEBHOOK_BUFFER.appendleft(event)
    logger.info("Recorded webhook event", extra={"event_type": event_type, "event_id": event["event_id"]})
    return event


def get_recent_webhooks(limit: int = 20, event_type: str | None = None) -> list[dict[str, Any]]:
    """Retrieve recent webhook events from the buffer."""
    if limit <= 0:
        return []
    events = list(_WEBHOOK_BUFFER)
    if event_type:
        events = [e for e in events if e.get("event_type") == event_type]
    return events[: min(limit, WEBHOOK_BUFFER_SIZE)]


def clear_webhook_buffer() -> None:
    """Clear all recorded webhook events (useful for testing)."""
    _WEBHOOK_BUFFER.clear()


def is_json_content_type(content_type: str) -> bool:
    """Return whether a Content-Type header names JSON: ``application/json`` or a ``+json`` type."""
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


async def process_incoming_webhook(body_bytes: bytes, content_type: str) -> dict[str, Any]:
    """Record an already-authenticated webhook body in the buffer.

    The caller authenticates the request first (``register_webhook_route`` checks the secret
    path segment); Sigma documents no signature or auth header to check here.

    * Empty body: a ``general_event`` with an empty payload.
    * JSON ``content_type``: the body must be a JSON object (else ``400``); it is the payload,
      and the event type is its ``event_type`` or ``type`` field, otherwise ``general_event``.
    * Any other or no ``content_type``: a ``non_json_payload`` event whose payload is
      ``{"content_type": <header as sent, or "">, "size_bytes": <int>, "received_at": <ISO 8601
      UTC>, "body_stored": false}``. The body itself is neither kept nor logged.
    """
    if not body_bytes:
        data: dict[str, Any] = {}
    elif not is_json_content_type(content_type):
        payload = {
            "content_type": content_type,
            "size_bytes": len(body_bytes),
            "received_at": datetime.now(timezone.utc).isoformat(),
            "body_stored": False,
        }
        return await _record_and_notify(NON_JSON_EVENT_TYPE, payload)
    else:
        try:
            parsed = json.loads(body_bytes.decode("utf-8"))
            if not isinstance(parsed, dict):
                return {"error": "Payload must be a JSON object", "status_code": 400}
            data = parsed
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"error": "Invalid JSON payload", "status_code": 400}

    return await _record_and_notify(str(data.get("event_type") or data.get("type") or "general_event"), data)


async def _record_and_notify(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Buffer one event, run the listeners, and return the accepted result."""
    event = record_webhook_event(event_type, payload)

    # Non-blocking listener execution with per-listener timeout
    for listener in _EVENT_LISTENERS:
        try:
            await asyncio.wait_for(listener(event), timeout=2.0)
        except Exception as e:
            logger.error("Error executing webhook listener", extra={"error": str(e)})

    return {"status": "accepted", "event_id": event["event_id"], "status_code": 200}


async def _read_bounded_body(request: Request) -> bytes | None:
    """Read the request body, or return ``None`` once it exceeds ``MAX_WEBHOOK_BODY_BYTES``.

    Raises Starlette's ``ClientDisconnect`` if the client goes away before the body ends.
    """
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_WEBHOOK_BODY_BYTES:
            return None
    return bytes(body)


def register_webhook_route(server: FastMCP, secret: str) -> bool:
    """Register ``POST /webhooks/sigma/{token}`` on ``server`` so Sigma deliveries feed the buffer.

    Call this only for an HTTP transport: stdio serves no HTTP routes. Nothing is registered
    without a secret, or with one ``webhook_secret_is_usable`` rejects, so an unauthenticated
    or guessable ingest route cannot exist. A path segment that does not match the secret
    (constant-time compare) gets ``404`` before the body is read, the same answer as an
    unknown path. Otherwise the handler answers ``413`` for a body over
    ``MAX_WEBHOOK_BODY_BYTES`` and then ``process_incoming_webhook``'s ``status_code``
    (200 or 400). Every method but ``POST`` gets the same ``404`` as a wrong secret, even with
    the right one. A client that disconnects before its body ends gets one warning line (no
    traceback) and nothing is buffered. The secret and the body are never logged, and once a
    secret is set, every log record has the path segment after ``/webhooks/sigma/`` redacted,
    whatever its value.
    """
    if not secret:
        logger.info("Webhook ingest route not registered: SIGMA_WEBHOOK_SECRET is not set")
        return False
    _install_webhook_path_redaction()
    if not webhook_secret_is_usable(secret):
        logger.warning(
            "Webhook ingest route not registered: SIGMA_WEBHOOK_SECRET must be at least %d characters "
            "of A-Z, a-z, 0-9, '_' or '-'",
            MIN_WEBHOOK_TOKEN_CHARS,
        )
        return False

    async def receive_sigma_webhook(request: Request) -> Response:
        token = str(request.path_params.get("token", ""))
        if request.method != "POST" or not verify_webhook_token(token, secret):
            logger.warning("Rejected webhook request", extra={"status_code": 404})
            return PlainTextResponse("Not Found", status_code=404)
        try:
            body = await _read_bounded_body(request)
        except ClientDisconnect:
            # The client is gone, so no response reaches it (uvicorn drops sends after a
            # disconnect); return one anyway so the handler ends normally, without a traceback.
            logger.warning("Webhook client disconnected before the request body ended; nothing recorded")
            return Response(status_code=400)
        if body is None:
            return JSONResponse({"error": "Payload too large"}, status_code=413)
        result = await process_incoming_webhook(body, request.headers.get("content-type", ""))
        status_code = int(result.pop("status_code"))
        if status_code != 200:
            logger.warning("Rejected webhook request", extra={"status_code": status_code})
        return JSONResponse(result, status_code=status_code)

    # The bare prefix is routed too, so neither it nor a trailing-slash variant draws a
    # redirect that would reveal the route; ``{token:path}`` keeps extra segments in the token.
    for path, name in ((WEBHOOK_ROUTE_PREFIX, "sigma_webhook_prefix"), (WEBHOOK_ROUTE_PATH, "sigma_webhook")):
        server.custom_route(path, methods=_ROUTED_METHODS, name=name, include_in_schema=False)(receive_sigma_webhook)
    logger.info("Webhook ingest route registered", extra={"path": WEBHOOK_ROUTE_PREFIX + "/<secret>"})
    return True
