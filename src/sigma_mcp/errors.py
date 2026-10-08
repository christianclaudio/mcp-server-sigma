"""Structured error types and credential redaction for the Sigma MCP server."""

from __future__ import annotations

import os
import re
from typing import Any

from fastmcp.exceptions import ToolError

_REDACT_KEYS = {"email", "userEmail", "memberEmail", "token", "access_token", "client_secret", "secret"}


_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)bearer\s+[a-zA-Z0-9\-\._~\+\/]+=*"),
    re.compile(r"(?i)client_secret=[a-zA-Z0-9\-\._~\+\/]+=*"),
    re.compile(r"(?i)(access_token=|\"access_token\":\s*\")[a-zA-Z0-9\-\._~\+\/]+=*\"?"),
    re.compile(r"(?i)(subject_token=|\"subject_token\":\s*\")[a-zA-Z0-9\-\._~\+\/]+=*\"?"),
    re.compile(r"eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+"),
    re.compile(r"ghs_[A-Za-z0-9\.\-_]{36,}"),
]


def redact_secrets(text: str, extra_secret: str | None = None) -> str:
    """Remove client_secret, OAuth tokens, and raw JWTs from error messages and logs."""
    if not text:
        return text
    secret = os.environ.get("SIGMA_CLIENT_SECRET", "")
    if secret and secret in text:
        text = text.replace(secret, "***REDACTED***")
    if extra_secret and extra_secret in text:
        text = text.replace(extra_secret, "***REDACTED***")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("***REDACTED***", text)
    return text


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[redacted]" if k in _REDACT_KEYS else _sanitize(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, str):
        return value[:500]
    return value


class SigmaError(Exception):
    """Base exception for all Sigma MCP errors with automatic secret redaction."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.message = redact_secrets(message)
        self.details = details or {}
        super().__init__(self.message)


class SafetyViolationError(SigmaError, ToolError):
    """Raised when an operation violates the bulk-destructive or read-only safety gates.

    Also a FastMCP ``ToolError``, so a refusal raised from middleware reaches the client as
    a ``tools/call`` result with ``isError: true`` instead of a JSON-RPC internal error.
    """


class SigmaAPIError(SigmaError):
    """Raised when the Sigma REST API returns a non-2xx response."""

    def __init__(
        self,
        status_code: int,
        path: str,
        method: str,
        detail: Any = None,
        request_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.path = path
        self.method = method
        self.detail = detail
        self.request_id = request_id
        super().__init__(f"Sigma API {method} {path} returned {status_code}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "sigma_api_error",
            "status_code": self.status_code,
            "method": self.method,
            "path": self.path,
            "detail": _sanitize(self.detail),
            "request_id": self.request_id,
        }
