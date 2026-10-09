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
from collections.abc import Callable, Coroutine
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
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
MIN_WEBHOOK_SECRET_LENGTH = 32
_WEBHOOK_SECRET_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
_REDACTED = "***REDACTED***"

_EVENT_LISTENERS: list[Callable[[dict[str, Any]], Coroutine[Any, Any, None]]] = []


def webhook_secret_is_usable(secret: str) -> bool:
    """Return whether ``secret`` can serve as the URL path secret.

    It must be at least ``MIN_WEBHOOK_SECRET_LENGTH`` characters of ``A-Z a-z 0-9 _ -`` so it
    survives in a URL unencoded and cannot be guessed.
    """
    return len(secret) >= MIN_WEBHOOK_SECRET_LENGTH and _WEBHOOK_SECRET_PATTERN.fullmatch(secret) is not None


def verify_webhook_token(presented: str, secret: str) -> bool:
    """Compare the path segment a request presented with the secret, in constant time."""
    if not secret or not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), secret.encode("utf-8"))


class _SecretRedactingFilter(logging.Filter):
    """Replace the webhook secret in log records (the access log prints each request path)."""

    def __init__(self, secret: str) -> None:
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and self.secret in record.msg:
            record.msg = record.msg.replace(self.secret, _REDACTED)
        if isinstance(record.args, tuple):
            record.args = tuple(
                a.replace(self.secret, _REDACTED) if isinstance(a, str) and self.secret in a else a for a in record.args
            )
        return True


# Uvicorn's access log records the full request path, and so would the secret segment.
_REDACTED_LOGGERS = ("uvicorn.access", "uvicorn.error")


def _install_secret_redaction(secret: str) -> None:
    """Attach one redacting filter per secret to the uvicorn loggers (idempotent)."""
    for name in _REDACTED_LOGGERS:
        target = logging.getLogger(name)
        if not any(isinstance(f, _SecretRedactingFilter) and f.secret == secret for f in target.filters):
            target.addFilter(_SecretRedactingFilter(secret))


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
    """Read the request body, or return ``None`` once it exceeds ``MAX_WEBHOOK_BODY_BYTES``."""
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
    the right one. The secret and the body are never logged, and the secret is redacted from
    uvicorn's access log.
    """
    if not secret:
        logger.info("Webhook ingest route not registered: SIGMA_WEBHOOK_SECRET is not set")
        return False
    if not webhook_secret_is_usable(secret):
        logger.warning(
            "Webhook ingest route not registered: SIGMA_WEBHOOK_SECRET must be at least %d characters "
            "of A-Z, a-z, 0-9, '_' or '-'",
            MIN_WEBHOOK_SECRET_LENGTH,
        )
        return False

    _install_secret_redaction(secret)

    async def receive_sigma_webhook(request: Request) -> Response:
        token = str(request.path_params.get("token", ""))
        if request.method != "POST" or not verify_webhook_token(token, secret):
            logger.warning("Rejected webhook request", extra={"status_code": 404})
            return PlainTextResponse("Not Found", status_code=404)
        body = await _read_bounded_body(request)
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
