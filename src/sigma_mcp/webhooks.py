"""Async webhook receiver and event log buffer for Sigma Computing events.

Enables push-based event processing for Sigma alerts, export completions,
and scheduled report webhooks.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from collections import deque
from collections.abc import Callable, Coroutine
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("sigma_mcp.webhooks")

# In-memory buffer storing up to 100 most recent webhook events
_WEBHOOK_BUFFER: deque[dict[str, Any]] = deque(maxlen=100)
_EVENT_LISTENERS: list[Callable[[dict[str, Any]], Coroutine[Any, Any, None]]] = []


def verify_webhook_signature(payload_bytes: bytes, signature_header: str | None, secret: str) -> bool:
    """Verify HMAC SHA256 signature for incoming webhook payloads."""
    if not secret or not signature_header:
        return False
    expected = hmac.HMAC(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    # Normalize header format (e.g. 'sha256=...' or raw hex string)
    actual = signature_header.removeprefix("sha256=").strip()
    return hmac.compare_digest(expected.lower(), actual.lower())


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
    return events[: min(limit, 100)]


def clear_webhook_buffer() -> None:
    """Clear all recorded webhook events (useful for testing)."""
    _WEBHOOK_BUFFER.clear()


async def process_incoming_webhook(
    body_bytes: bytes,
    headers: dict[str, str],
    webhook_secret: str | None = None,
) -> dict[str, Any]:
    """Parse, verify, and buffer an incoming raw webhook HTTP request."""
    normalized_headers = {k.lower(): v for k, v in headers.items()}
    sig_header = normalized_headers.get("x-sigma-signature")

    if webhook_secret and not verify_webhook_signature(body_bytes, sig_header, webhook_secret):
        return {"error": "Invalid webhook signature", "status_code": 401}

    if not body_bytes:
        data: dict[str, Any] = {}
    else:
        try:
            parsed = json.loads(body_bytes.decode("utf-8"))
            if not isinstance(parsed, dict):
                return {"error": "Payload must be a JSON object", "status_code": 400}
            data = parsed
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"error": "Invalid JSON payload", "status_code": 400}

    event_type = str(
        data.get("event_type") or data.get("type") or normalized_headers.get("x-sigma-event") or "general_event"
    )
    event = record_webhook_event(event_type, data, body_bytes.decode("utf-8", errors="replace"))

    # Non-blocking listener execution with per-listener timeout
    for listener in _EVENT_LISTENERS:
        try:
            await asyncio.wait_for(listener(event), timeout=2.0)
        except Exception as e:
            logger.error("Error executing webhook listener", extra={"error": str(e)})

    return {"status": "accepted", "event_id": event["event_id"], "status_code": 200}
