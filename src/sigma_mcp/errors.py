"""Structured error types for the Sigma MCP server."""

from __future__ import annotations

from typing import Any

_REDACT_KEYS = {"email", "userEmail", "memberEmail", "token", "access_token", "client_secret", "secret"}


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[redacted]" if k in _REDACT_KEYS else _sanitize(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, str):
        return value[:500]
    return value


class SigmaAPIError(Exception):
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
