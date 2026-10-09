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

# The fleet redaction set, copied verbatim from the template's SECRET_PATTERNS (template #60,
# c343e86), in its order. Group 1 keeps the key; only the marker differs from the template
# (``***REDACTED***``). It runs after the sigma patterns above, which stay: sigma's own bearer
# pattern, client_secret, access and subject tokens, raw JWTs and ghs_ tokens.
_FLEET_PATTERNS: list[re.Pattern[str]] = [
    # Bearer value: base64url and base64 characters (``~``, ``+``, ``/``) plus ``=`` padding.
    re.compile(r"(?i)(bearer\s+)[a-z0-9_\-\.~+/]{8,}=*", re.IGNORECASE),
    re.compile(r"(?i)(api[_-]?key[\"'\s:=]+)[a-z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(r"(?i)(client[_-]?secret[\"'\s:=]+)[a-z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(r"(?i)(password[\"'\s:=]+)[^\s\"',]{4,}", re.IGNORECASE),
    # api/access/refresh/auth/id/session tokens as key=value, key: value, an
    # ``X-Auth-Token:`` header and JSON ("key": "value", also backslash-escaped inside an
    # already-serialized JSON string).
    re.compile(
        r"(?i)((?:api|access|refresh|auth|id|session)[_-]?token(?:\\?[\"'])?\s*[:=]\s*"
        r"(?:\\?[\"'])?)[^\s\"'\\&,;]+",
        re.IGNORECASE,
    ),
    # The same keys URL-encoded (``access_token%3D...``); the value stops at an encoded
    # ``%26`` (&) or ``%23`` (#), so the parameters after it survive.
    re.compile(
        r"(?i)((?:api|access|refresh|auth|id|session)[_-]?token%3D)"
        r"(?:[^\s\"'\\&,;#%]|%(?!26|23))+",
        re.IGNORECASE,
    ),
    # ``Authorization: Token <value>`` scheme, also as a quoted JSON or dict entry.
    re.compile(
        r"(?i)(authorization(?:\\?[\"'])?\s*[:=]\s*(?:\\?[\"'])?token\s+)[^\s\"'\\&,;]+",
        re.IGNORECASE,
    ),
    # JSON ``"token": "value"``; the opening quote right before ``token`` keeps keys such
    # as ``"next_token"`` and ``"page_token"`` untouched.
    re.compile(r"(?i)(\\?[\"']token\\?[\"']\s*:\s*\\?[\"'])[^\s\"'\\&,;]+", re.IGNORECASE),
    # Bare ``token`` key with ``:`` or ``=``, optional spaces and an optional opening quote
    # (``token=``, ``token: x``, ``token = x``, ``token: "x"``); the lookbehind keeps
    # ``page_token``, ``next_token``, ``csrf_token`` and ``max_tokens`` untouched.
    re.compile(
        r"(?i)((?<![A-Za-z0-9_])token\s*[:=]\s*(?:\\?[\"'])?)[^\s\"'\\&#]+",
        re.IGNORECASE,
    ),
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
    for pattern in _FLEET_PATTERNS:
        text = pattern.sub(r"\1***REDACTED***", text)
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
