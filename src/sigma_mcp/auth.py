"""Bearer token authentication for the HTTP transports.

``SIGMA_MCP_AUTH_TOKEN`` is enforced through FastMCP's built-in server auth: a
``TokenVerifier`` subclass passed as the server's ``auth`` provider
(https://gofastmcp.com/servers/auth/token-verification#tokenverifier-class).
FastMCP applies it only to HTTP transports; stdio is unaffected
(https://gofastmcp.com/servers/auth/authentication).
"""

from __future__ import annotations

import hmac

from fastmcp.server.auth import AccessToken, TokenVerifier

AUTH_TOKEN_ENV = "SIGMA_MCP_AUTH_TOKEN"


class SharedTokenVerifier(TokenVerifier):
    """Accept exactly one shared bearer token, compared in constant time."""

    def __init__(self, expected_token: str) -> None:
        stripped = expected_token.strip()
        if not stripped:
            raise ValueError(f"{AUTH_TOKEN_ENV} must be non-empty to enable authentication")
        super().__init__()
        self._expected = stripped.encode("utf-8")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(expected_token=***REDACTED***)"

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return an access token when ``token`` matches, ``None`` otherwise (FastMCP answers 401)."""
        if not hmac.compare_digest(token.encode("utf-8"), self._expected):
            return None
        return AccessToken(token=token, client_id="sigma-mcp-shared-token", scopes=[])
