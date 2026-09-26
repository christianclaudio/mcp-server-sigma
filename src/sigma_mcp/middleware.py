"""Hierarchical middleware for FastMCP 4 Server Composition.

Execution Order:
1. Parent Middleware (Global audit, timing, secret redaction, and global read-only gate).
2. Child Middleware (Domain-specific guards, bulk-destructive gating, and parameter limits).
3. Tool Handler (Core business logic).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

from sigma_mcp.config import settings
from sigma_mcp.errors import SafetyViolationError, redact_secrets

logger = logging.getLogger(__name__)

_RO_PREFIXES = ("sigma_list_", "sigma_get_", "sigma_api_capabilities", "list_", "get_", "api_capabilities")
_RO_NAMES = {
    "sigma_get_current_user",
    "get_current_user",
    "sigma_list_workbooks_shared_with_member",
    "list_workbooks_shared_with_member",
    "sigma_list_all_input_tables",
    "list_all_input_tables",
    "sigma_list_tenants_paginated",
    "list_tenants_paginated",
    "sigma_get_tenant_scoped_info",
    "get_tenant_scoped_info",
    "sigma_formula_pitfalls",
    "formula_pitfalls",
    "sigma_search_docs",
    "search_docs",
    "sigma_get_doc_page",
    "get_doc_page",
    "sigma_verify_workbook_spec",
    "verify_workbook_spec",
    "sigma_verify_report_spec",
    "verify_report_spec",
    "sigma_download_query_export",
    "download_query_export",
}


def is_read_only_tool(tool_name: str) -> bool:
    """Determine whether a tool is read-only based on naming conventions and allowlists."""
    clean_name = (
        tool_name.split("_", 1)[-1]
        if any(tool_name.startswith(p) for p in ("workbooks_", "datasets_", "elements_", "workspace_", "admin_"))
        else tool_name
    )
    return (
        tool_name in _RO_NAMES
        or clean_name in _RO_NAMES
        or any(tool_name.startswith(p) for p in _RO_PREFIXES)
        or any(clean_name.startswith(p) for p in _RO_PREFIXES)
    )


class ParentAuditMiddleware(Middleware):
    """Global gateway middleware logging operation timing, method, and sanitizing errors."""

    async def on_message(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Any],
    ) -> Any:
        """Intercept request, track timing, log lifecycle, and redact sensitive output."""
        start_time = time.perf_counter()
        method = getattr(context, "method", "unknown")
        tool_name = getattr(context.message, "name", None) if context.message else None
        target = f"{method}:{tool_name}" if tool_name else method

        logger.debug("→ MCP Request received: %s", target)
        try:
            result = await call_next(context)
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.debug("← MCP Request completed: %s in %.2fms", target, duration_ms)
            return result
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            sanitized_msg = redact_secrets(str(exc))
            logger.error("✗ MCP Request failed: %s in %.2fms: %s", target, duration_ms, sanitized_msg)
            if hasattr(exc, "args") and exc.args:
                exc.args = tuple(redact_secrets(str(a)) if isinstance(a, str) else a for a in exc.args)
            raise


class ReadOnlyGateMiddleware(Middleware):
    """Middleware enforcing read-only operation when settings.MCP_READONLY or SIGMA_MCP_READONLY is active."""

    async def on_message(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Any],
    ) -> Any:
        """Block mutating operations before reaching tool handlers when in read-only mode."""
        is_ro = settings.MCP_READONLY or os.environ.get("SIGMA_MCP_READONLY", "").strip() == "1"
        if is_ro and getattr(context, "method", None) == "tools/call":
            tool_name = getattr(context.message, "name", "") if context.message else ""
            if not is_read_only_tool(tool_name):
                raise SafetyViolationError(
                    f"Server running in read-only mode (SIGMA_MCP_READONLY=1); tool '{tool_name}' blocked."
                )
        return await call_next(context)


class AdminDomainGuardMiddleware(Middleware):
    """Child domain middleware mounted on admin sub-server for bulk mutation protection."""

    async def on_message(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Any],
    ) -> Any:
        """Enforce domain safety gates strictly within the admin namespace."""
        tool_name = getattr(context.message, "name", "") if context.message else ""
        if tool_name in (
            "bulk_deactivate_members",
            "sigma_bulk_deactivate_members",
            "admin_bulk_deactivate_members",
            "admin_sigma_bulk_deactivate_members",
            "bulk_remove_team_members",
            "sigma_bulk_remove_team_members",
            "admin_bulk_remove_team_members",
            "admin_sigma_bulk_remove_team_members",
        ):
            allow_bulk = (
                settings.MCP_ALLOW_BULK_DESTRUCTIVE
                or os.environ.get("SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE", "").strip() == "1"
            )
            if not allow_bulk:
                raise SafetyViolationError(
                    "Bulk destructive operations disabled. Set SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1."
                )
        return await call_next(context)
