"""Configuration management for Sigma MCP server."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable bindings."""

    model_config = SettingsConfigDict(
        env_prefix="SIGMA_",
        env_file=".env",
        extra="ignore",
    )

    CLIENT_ID: str = Field(default="", description="Sigma OAuth Client ID")
    CLIENT_SECRET: str = Field(default="", description="Sigma OAuth Client Secret")
    BASE_URL: str = Field(
        default="https://aws-api.sigmacomputing.com",
        description="Sigma REST API base URL",
    )
    ALLOWED_HOSTS: str = Field(
        default="",
        description="Comma-separated hostname allowlist for SSRF defense",
    )
    ALLOWED_TENANTS: str = Field(
        default="",
        description="Comma-separated allowed tenant IDs for token exchange",
    )
    STRICT_TENANT_ALLOWLIST: bool = Field(
        default=False,
        description="Strictly fail with HTTP 403 on unrecognized tenant IDs",
    )
    TIMEOUT_SECONDS: float = Field(
        default=60.0,
        gt=0,
        description="HTTP request timeout in seconds",
    )
    MAX_RETRIES: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum retry attempts on HTTP 429 / 5xx",
    )
    BASE_DELAY: float = Field(
        default=1.0,
        ge=0,
        description="Base retry delay in seconds",
    )
    MAX_RETRY_DELAY: float = Field(
        default=60.0,
        ge=0,
        description="Maximum retry delay in seconds",
    )
    WEBHOOK_SECRET: str = Field(
        default="",
        description="Shared secret for webhook HMAC signature verification",
    )
    LOG_FORMAT: str = Field(
        default="",
        description="Logging format: 'json' or standard",
    )

    # Safety Gating
    MCP_READONLY: bool = Field(
        default=False,
        description="Restrict server strictly to tools marked readOnlyHint=True",
    )
    MCP_ALLOW_BULK_DESTRUCTIVE: bool = Field(
        default=False,
        description="Gate required to register and execute batch destructive mutations",
    )

    # Transport Options (Spec 2026-07-28 / SEP-1049)
    MCP_STATELESS_HTTP: bool = Field(
        default=False,
        description="Enable stateless Streamable HTTP transport mode (Spec 2026-07-28 / SEP-1049)",
    )
    MCP_JSON_RESPONSE: bool = Field(
        default=False,
        description="Return direct application/json responses instead of SSE text/event-stream",
    )

    # Composition and Discovery
    MCP_ENABLE_TOOL_SEARCH: bool = Field(
        default=False,
        description="Enable dynamic ToolSearch transform replacing flat tools/list",
    )
    MCP_PROFILE: str = Field(
        default="full",
        description="Server profile: 'full', 'core', 'admin', or 'embed'",
    )

    @field_validator(
        "STRICT_TENANT_ALLOWLIST",
        "MCP_READONLY",
        "MCP_ALLOW_BULK_DESTRUCTIVE",
        "MCP_STATELESS_HTTP",
        "MCP_JSON_RESPONSE",
        "MCP_ENABLE_TOOL_SEARCH",
        mode="before",
    )
    @classmethod
    def _parse_lenient_bool(cls, v: Any) -> bool:
        if isinstance(v, str):
            clean = v.strip().lower()
            if clean in ("", "0", "false", "no", "off"):
                return False
            if clean in ("1", "true", "yes", "on"):
                return True
        return bool(v)


settings = Settings()
