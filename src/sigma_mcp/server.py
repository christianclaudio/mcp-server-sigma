"""MCP server for Sigma Computing — full API surface.

Exposes every Sigma REST API operation as an MCP tool, organized by domain.
Connect via: cortex mcp add sigma-tools http://localhost:8080 --transport http

Environment variables controlling tool registration:
  SIGMA_MCP_PROFILE          - Tool subset: core, admin, embed, full (default: full).
  SIGMA_MCP_READONLY=1       - When set, only tools annotated read_only_hint=True are
                               registered. Composes with SIGMA_MCP_PROFILE (e.g. profile=admin
                               + readonly = read-only subset of admin tools).
  SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1 - Required to register sigma_bulk_deactivate_members
                               and sigma_bulk_remove_team_members. Without it these tools
                               are removed at startup.

Filter application order: annotations -> profile -> readonly -> bulk-destructive gating.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import logging
import os
import re
import time
from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from .client import SigmaClient
from .errors import SigmaAPIError
from .webhooks import get_recent_webhooks

logger = logging.getLogger("sigma_mcp")


class StructuredJSONFormatter(logging.Formatter):
    """JSON formatter for enterprise log aggregators (Datadog/CloudWatch/Splunk)."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "tool_name"):
            log_obj["mcp_tool"] = record.tool_name
        if hasattr(record, "duration_ms"):
            log_obj["duration_ms"] = record.duration_ms
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


def configure_logging() -> None:
    """Configure logging format based on SIGMA_MCP_LOG_FORMAT."""
    log_format = os.environ.get("SIGMA_MCP_LOG_FORMAT", "").lower()
    if log_format == "json":
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredJSONFormatter())
        logging.root.handlers = [handler]
        logging.root.setLevel(logging.INFO)


mcp = MCPServer(
    "mcp-server-sigma",
    description="Full-surface MCP server for Sigma Computing. Create data models, provision dashboards, swap sources, manage your org programmatically.",
)

_client: SigmaClient | None = None


def _invalid_request(message: str) -> str:
    """Return a uniform nested error response for validation failures."""
    return json.dumps({"error": {"type": "invalid_request", "message": message}})


_HEADER_CLIENT_CACHE: dict[tuple[str, str], SigmaClient] = {}


async def get_client(ctx: Any | None = None) -> SigmaClient:
    """Retrieve or construct the SigmaClient instance.

    Checks FastMCP request context for per-request credentials (headers:
    X-Sigma-Client-Id, X-Sigma-Client-Secret, X-Sigma-Base-Url), falling back to
    standard environment variables if not present in request context.
    """
    global _client
    if ctx is not None:
        raw_headers: dict[str, Any] = {}
        if hasattr(ctx, "request_context") and ctx.request_context:
            raw_headers = getattr(ctx.request_context, "headers", {}) or {}
        elif isinstance(ctx, dict):
            raw_headers = ctx.get("headers", {})

        headers = {k.lower(): str(v) for k, v in raw_headers.items() if v is not None}
        req_client_id = headers.get("x-sigma-client-id")
        req_client_secret = headers.get("x-sigma-client-secret")
        req_base_url = headers.get("x-sigma-base-url") or os.environ.get(
            "SIGMA_API_BASE_URL", "https://api.us-a.aws.sigmacomputing.com"
        )

        if req_client_id and req_client_secret:
            cache_key = (req_client_id, req_base_url)
            if cache_key not in _HEADER_CLIENT_CACHE:
                if len(_HEADER_CLIENT_CACHE) >= 100:
                    oldest_key = next(iter(_HEADER_CLIENT_CACHE))
                    old_c = _HEADER_CLIENT_CACHE.pop(oldest_key)
                    await old_c.aclose()
                _HEADER_CLIENT_CACHE[cache_key] = SigmaClient(req_client_id, req_client_secret, req_base_url)
            return _HEADER_CLIENT_CACHE[cache_key]
        if req_client_id or req_client_secret:
            raise ValueError("Both X-Sigma-Client-Id and X-Sigma-Client-Secret must be provided")

    if _client is None:
        client_id = os.environ.get("SIGMA_CLIENT_ID", "")
        client_secret = os.environ.get("SIGMA_CLIENT_SECRET", "")
        base_url = os.environ.get("SIGMA_API_BASE_URL", "https://api.us-a.aws.sigmacomputing.com")
        if not client_id or not client_secret:
            raise ValueError("SIGMA_CLIENT_ID and SIGMA_CLIENT_SECRET must be set")
        _client = SigmaClient(client_id, client_secret, base_url)
    return _client


def _summarize_list(data: Any, key_fields: list[str]) -> Any:
    """Helper to summarize high-cardinality list responses when summary_only=True."""
    if not isinstance(data, dict) or "entries" not in data:
        return data
    entries = data.get("entries", [])
    summarized = [{k: item[k] for k in key_fields if k in item} for item in entries if isinstance(item, dict)]
    total = data.get("total", len(entries))
    res: dict[str, Any] = {"total": total, "entries": summarized}
    if "nextPage" in data:
        res["nextPage"] = data["nextPage"]
    return res


_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)bearer\s+[a-zA-Z0-9\-\._~\+\/]+=*"),
    re.compile(r"(?i)client_secret=[a-zA-Z0-9\-\._~\+\/]+=*"),
    re.compile(r"(?i)(access_token=|\"access_token\":\s*\")[a-zA-Z0-9\-\._~\+\/]+=*\"?"),
    re.compile(r"(?i)(subject_token=|\"subject_token\":\s*\")[a-zA-Z0-9\-\._~\+\/]+=*\"?"),
    re.compile(r"eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+"),
    re.compile(r"ghs_[A-Za-z0-9\.\-_]{36,}"),
]


def _redact_secrets(text: str, extra_secret: str | None = None) -> str:
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


def sigma_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that wraps MCP tools with structured error handling and execution metrics."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        start_t = time.perf_counter()
        try:
            result: str = await fn(*args, **kwargs)
            duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.info("Tool executed successfully", extra={"tool_name": fn.__name__, "duration_ms": duration_ms})
            return result
        except SigmaAPIError as e:
            duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.error("Tool failed with API error", extra={"tool_name": fn.__name__, "duration_ms": duration_ms})
            err = e.to_dict()
            if isinstance(err.get("detail"), str):
                err["detail"] = _redact_secrets(err["detail"])
            return json.dumps({"error": err})
        except Exception as e:
            duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.error(
                "Tool failed with internal error", extra={"tool_name": fn.__name__, "duration_ms": duration_ms}
            )
            msg = _redact_secrets(str(e))
            return json.dumps({"error": {"type": "internal", "message": msg}})

    return wrapper


# ─── Connections ───────────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_connections(summary_only: bool = False) -> str:
    """List all connections in the Sigma organization. Pass summary_only=True for concise token-efficient response."""
    data = await (await get_client()).list_connections()
    if summary_only:
        data = _summarize_list(data, ["connectionId", "name", "type"])
    return json.dumps(data, indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_connection(connection_id: str) -> str:
    """Get details for a specific connection."""
    return json.dumps(await (await get_client()).get_connection(connection_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_sync_connection(connection_id: str, path: list[str] | None = None) -> str:
    """Force Sigma to re-index a warehouse path. Pass empty list for full sync."""
    return json.dumps(await (await get_client()).sync_connection(connection_id, path), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_test_connection(connection_id: str) -> str:
    """Test connectivity for a connection."""
    return json.dumps(await (await get_client()).test_connection(connection_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_columns_for_table(table_id: str) -> str:
    """List columns for a warehouse table by its tableId."""
    return json.dumps(await (await get_client()).list_columns_for_table(table_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_connection_grants(connection_id: str) -> str:
    """List permission grants on a connection."""
    return json.dumps(await (await get_client()).list_connection_grants(connection_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_add_connection_grant(connection_id: str, grant_type: str, grantee_id: str, permission: str) -> str:
    """Add a grant to a connection.

    grant_type: 'member' or 'team'.
    permission: 'annotate' (Can Use & Annotate) or 'usage' (Can Use).
    """
    gt = grant_type.strip().lower()
    if gt not in ("member", "team"):
        return _invalid_request("grant_type must be 'member' or 'team'")
    grantee_key = "memberId" if gt == "member" else "teamId"
    body = {"grants": [{"grantee": {grantee_key: grantee_id}, "permission": permission}]}
    return json.dumps(await (await get_client()).add_connection_grant(connection_id, body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_delete_connection_path_grant(connection_path_id: str, grant_id: str, confirm: bool = False) -> str:
    """Delete a grant from a connection path. DESTRUCTIVE. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).delete_connection_path_grant(connection_path_id, grant_id)
    return json.dumps({"status": code})


# ─── Workbooks ─────────────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_workbooks(limit: int = 200, summary_only: bool = False) -> str:
    """List all workbooks in the organization. Pass summary_only=True for concise token-efficient response."""
    data = await (await get_client()).list_workbooks(limit)
    if summary_only:
        data = _summarize_list(data, ["workbookId", "name", "folderId", "ownerId"])
    return json.dumps(data, indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_workbook(workbook_id: str) -> str:
    """Get workbook metadata."""
    return json.dumps(await (await get_client()).get_workbook(workbook_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_create_workbook(name: str, folder_id: str, description: str = "") -> str:
    """Create an empty workbook in a folder."""
    return json.dumps(await (await get_client()).create_workbook(name, folder_id, description), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_duplicate_workbook(
    workbook_id: str,
    name: str,
    destination_folder_id: str = "",
) -> str:
    """Duplicate an existing workbook.

    workbook_id: ID of the workbook to clone.
    name: Name for the new cloned workbook (required by the API).
    destination_folder_id: ID of the destination folder. If omitted, clones into the same folder as the original.
    """
    if not name or not name.strip():
        return _invalid_request("name is required by the Sigma copy API")
    if not destination_folder_id or not destination_folder_id.strip():
        # Auto-discover: use the current user's home folder
        client = await get_client()
        me = await client.get_current_user()
        me_dict = me if isinstance(me, dict) else {}
        member = await client.get_member(me_dict.get("userId", ""))
        member_dict = member if isinstance(member, dict) else {}
        destination_folder_id = str(member_dict.get("homeFolderId", ""))
        if not destination_folder_id:
            return _invalid_request("Could not determine home folder; provide destination_folder_id explicitly")
    body: dict[str, Any] = {"name": name, "destinationFolderId": destination_folder_id}
    return json.dumps(await (await get_client()).duplicate_workbook(workbook_id, body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_delete_file(inode_id: str, confirm: bool = False) -> str:
    """Delete a file/workbook/data model by its inode ID. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).delete_file(inode_id)
    return json.dumps({"status": code})


@mcp.tool()
@sigma_tool
async def sigma_list_workbook_pages(workbook_id: str) -> str:
    """List pages in a workbook."""
    return json.dumps(await (await get_client()).list_workbook_pages(workbook_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_workbook_page_elements(workbook_id: str, page_id: str) -> str:
    """List elements on a specific page of a workbook."""
    return json.dumps(await (await get_client()).list_workbook_page_elements(workbook_id, page_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_workbook_elements(workbook_id: str) -> str:
    """List all elements (tables, charts, controls, etc.) in a workbook."""
    return json.dumps(await (await get_client()).list_workbook_elements(workbook_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_workbook_columns(workbook_id: str) -> str:
    """List all columns across all elements in a workbook, including formulas and types."""
    return json.dumps(await (await get_client()).list_workbook_columns(workbook_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_workbook_queries(workbook_id: str) -> str:
    """List generated SQL queries for all elements in a workbook."""
    return json.dumps(await (await get_client()).list_workbook_queries(workbook_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_workbook_controls(workbook_id: str) -> str:
    """List control elements (filters, parameters) in a workbook."""
    return json.dumps(await (await get_client()).list_workbook_controls(workbook_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_workbook_sources(workbook_id: str) -> str:
    """List data sources used by a workbook."""
    return json.dumps(await (await get_client()).list_workbook_sources(workbook_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_swap_workbook_sources(
    workbook_id: str,
    connection_mapping: list[dict[str, Any]] | None = None,
    source_mapping: list[dict[str, Any]] | None = None,
) -> str:
    """Swap data sources for a workbook. Use connectionMapping for connection-level swaps, sourceMapping for table-level swaps."""
    body: dict[str, Any] = {}
    if connection_mapping:
        body["connectionMapping"] = connection_mapping
    if source_mapping:
        body["sourceMapping"] = source_mapping
    return json.dumps(await (await get_client()).swap_workbook_sources(workbook_id, body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_workbook_lineage(workbook_id: str) -> str:
    """List data lineage for a workbook."""
    return json.dumps(await (await get_client()).list_workbook_lineage(workbook_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_workbook_version_history(workbook_id: str) -> str:
    """Get version history for a workbook."""
    return json.dumps(await (await get_client()).get_workbook_version_history(workbook_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_restore_workbook_version(workbook_id: str, version: int) -> str:
    """Restore a workbook to a previous version."""
    return json.dumps(await (await get_client()).restore_workbook_version(workbook_id, version), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_element_query(workbook_id: str, element_id: str) -> str:
    """Get the generated SQL query for a specific element in a workbook."""
    return json.dumps(await (await get_client()).get_element_query(workbook_id, element_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_element_columns(workbook_id: str, element_id: str) -> str:
    """List columns for a specific element in a workbook."""
    return json.dumps(await (await get_client()).get_element_columns(workbook_id, element_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_convert_workbook_to_report(
    workbook_id: str,
    name: str,
    destination_folder_id: str | None = None,
) -> str:
    """Convert a workbook to a report (one-way, creates a new report).

    workbook_id: ID of the workbook to convert.
    name: Name for the new report (required by the API).
    destination_folder_id: Optional folder ID for the report; defaults to My Documents.
    """
    if not name or not name.strip():
        return _invalid_request("name is required by the Sigma convertToReport API")
    body: dict[str, Any] = {"name": name}
    if destination_folder_id:
        body["destinationFolderId"] = destination_folder_id
    return json.dumps(await (await get_client()).convert_workbook_to_report(workbook_id, body), indent=2)


# ─── Workbook Grants ───────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_workbook_grants(workbook_id: str) -> str:
    """List permission grants on a workbook."""
    return json.dumps(await (await get_client()).list_workbook_grants(workbook_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_grant_workbook_access(workbook_id: str, grant_type: str, grantee_id: str, permission: str) -> str:
    """Grant access to a workbook. grant_type: 'member' or 'team'. permission: 'view', 'explore', 'edit'."""
    grantee_key = "memberId" if grant_type.lower() == "member" else "teamId"
    body = {"grants": [{"grantee": {grantee_key: grantee_id}, "permission": permission}]}
    return json.dumps(await (await get_client()).grant_workbook_access(workbook_id, body), indent=2)


# ─── Workbook Embeds ───────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_workbook_embeds(workbook_id: str) -> str:
    """List embed configurations for a workbook."""
    return json.dumps(await (await get_client()).list_workbook_embeds(workbook_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_create_workbook_embed(
    workbook_id: str,
    embed_type: str = "public",
    source_type: str = "workbook",
    source_id: str | None = None,
) -> str:
    """Create an embed for a workbook.

    embed_type: Visibility of the embed. Only 'public' is currently supported.
    source_type: Scope of the embed — 'workbook' (entire workbook), 'page', or 'element'.
    source_id: Required when source_type is 'page' or 'element'.
    """
    if source_type in ("page", "element") and not (source_id and source_id.strip()):
        return _invalid_request("source_id is required when source_type is 'page' or 'element'")
    body: dict[str, Any] = {"embedType": embed_type, "sourceType": source_type}
    if source_id:
        body["sourceId"] = source_id
    return json.dumps(await (await get_client()).create_workbook_embed(workbook_id, body), indent=2)


# ─── Workbook Exports ──────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_export_workbook(
    workbook_id: str, element_id: str = "", format: str = "pdf", layout: str = "portrait"
) -> str:
    """Export a workbook element. If element_id is empty, exports the first page.

    format: 'pdf', 'png', 'csv', 'xlsx', 'json', 'jsonl'.
    layout: 'portrait' or 'landscape' (only for pdf/png).
    element_id: The specific element to export. If omitted, uses the first page element.
    """
    if not element_id:
        # Auto-discover: get first page's first element
        client = await get_client()
        pages = await client.list_workbook_pages(workbook_id)
        page_entries: list[Any] = (
            pages.get("entries", []) if isinstance(pages, dict) else (pages if isinstance(pages, list) else [])
        )
        if not page_entries:
            return _invalid_request("Workbook has no pages to export")
        page_id = str(page_entries[0]["pageId"])
        elems = await client.list_workbook_page_elements(workbook_id, page_id)
        elem_entries: list[Any] = (
            elems.get("entries", []) if isinstance(elems, dict) else (elems if isinstance(elems, list) else [])
        )
        if not elem_entries:
            return _invalid_request("First page has no elements to export")
        element_id = str(elem_entries[0]["elementId"])

    if format in ("pdf", "png"):
        fmt_obj: dict[str, Any] = {"type": format, "layout": layout}
    else:
        fmt_obj = {"type": format}

    body: dict[str, Any] = {"elementId": element_id, "format": fmt_obj}
    return json.dumps(await (await get_client()).export_workbook(workbook_id, body), indent=2)


# ─── Workbook Schedules ────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_workbook_schedules(workbook_id: str) -> str:
    """List scheduled exports for a workbook."""
    return json.dumps(await (await get_client()).list_workbook_schedules(workbook_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_add_workbook_schedule(workbook_id: str, body: dict[str, Any]) -> str:
    """Create a scheduled export for a workbook. See Sigma docs for schedule body schema."""
    return json.dumps(await (await get_client()).add_workbook_schedule(workbook_id, body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_delete_workbook_schedule(workbook_id: str, schedule_id: str, confirm: bool = False) -> str:
    """Delete a scheduled export. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).delete_workbook_schedule(workbook_id, schedule_id)
    return json.dumps({"status": code})


# ─── Workbook Materializations ─────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_materialize_element(workbook_id: str, element_id: str) -> str:
    """Trigger materialization for a workbook. Pass elementId in body if needed."""
    return json.dumps(await (await get_client()).materialize_workbook(workbook_id, {"elementId": element_id}), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_materialization_job(workbook_id: str, job_id: str) -> str:
    """Check status of a materialization job."""
    return json.dumps(await (await get_client()).get_materialization_job(workbook_id, job_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_materialization_schedules(workbook_id: str) -> str:
    """List materialization schedules for a workbook."""
    return json.dumps(await (await get_client()).list_materialization_schedules(workbook_id), indent=2)


# ─── Workbook Bookmarks ───────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_workbook_bookmarks(workbook_id: str) -> str:
    """List bookmarks (saved filter states) in a workbook."""
    return json.dumps(await (await get_client()).list_workbook_bookmarks(workbook_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_add_workbook_bookmark(workbook_id: str, body: dict[str, Any]) -> str:
    """Add a bookmark to a workbook."""
    return json.dumps(await (await get_client()).add_workbook_bookmark(workbook_id, body), indent=2)


# ─── Workbook Tags ─────────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_get_workbook_tags(workbook_id: str) -> str:
    """List tags on a workbook."""
    return json.dumps(await (await get_client()).get_workbook_tags(workbook_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_tag_workbook(workbook_id: str, tag_name: str) -> str:
    """Apply a version tag to a workbook by tag NAME (e.g. 'Production'). The Sigma API takes the tag name here, not its ID."""
    return json.dumps(await (await get_client()).tag_workbook(workbook_id, tag_name), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_remove_workbook_tag(workbook_id: str, tag_id: str, confirm: bool = False) -> str:
    """Remove a tag from a workbook. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).remove_workbook_tag(workbook_id, tag_id)
    return json.dumps({"status": code})


# ─── Templates ─────────────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_templates(limit: int = 200) -> str:
    """List all templates in the organization."""
    return json.dumps(await (await get_client()).list_templates(limit), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_template(template_id: str) -> str:
    """Get template details."""
    return json.dumps(await (await get_client()).get_template(template_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_create_workbook_from_template(template_id: str, folder_id: str, name: str | None = None) -> str:
    """Create a workbook from a template. This brings real visuals — the only programmatic way to get charts/tables."""
    return json.dumps(await (await get_client()).save_workbook_from_template(template_id, folder_id, name), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_save_template_from_workbook(workbook_id: str, folder_id: str, name: str | None = None) -> str:
    """Save a workbook as a reusable template."""
    body: dict[str, Any] = {"folderId": folder_id}
    if name:
        body["name"] = name
    return json.dumps(await (await get_client()).save_template_from_workbook(workbook_id, body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_swap_template_sources(
    template_id: str,
    connection_mapping: list[dict[str, Any]] | None = None,
    source_mapping: list[dict[str, Any]] | None = None,
) -> str:
    """Swap data sources on a template. Use connectionMapping for connection-level swaps, sourceMapping for table-level swaps."""
    body: dict[str, Any] = {}
    if connection_mapping:
        body["connectionMapping"] = connection_mapping
    if source_mapping:
        body["sourceMapping"] = source_mapping
    return json.dumps(await (await get_client()).swap_template_sources(template_id, body), indent=2)


# ─── Data Models ───────────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_data_models(limit: int = 200, summary_only: bool = False) -> str:
    """List all data models in the organization. Pass summary_only=True for concise token-efficient response."""
    data = await (await get_client()).list_data_models(limit)
    if summary_only:
        data = _summarize_list(data, ["dataModelId", "name", "connectionId"])
    return json.dumps(data, indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_data_model(data_model_id: str) -> str:
    """Get data model metadata."""
    return json.dumps(await (await get_client()).get_data_model(data_model_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_data_model_spec(data_model_id: str) -> str:
    """Get the full code representation (JSON spec) of a data model — tables, columns, metrics, relationships."""
    return json.dumps(await (await get_client()).get_data_model_spec(data_model_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_create_data_model(spec: dict[str, Any]) -> str:
    """Create a data model from a JSON code representation. Must include name, folderId, schemaVersion, and pages with elements."""
    return json.dumps(await (await get_client()).create_data_model_spec(spec), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_update_data_model(data_model_id: str, spec: dict[str, Any]) -> str:
    """Update an existing data model from a JSON code representation (full replacement via PUT)."""
    return json.dumps(await (await get_client()).update_data_model_spec(data_model_id, spec), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_data_model_elements(data_model_id: str) -> str:
    """List elements in a data model."""
    return json.dumps(await (await get_client()).list_data_model_elements(data_model_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_data_model_columns(data_model_id: str) -> str:
    """List all columns across all elements in a data model."""
    return json.dumps(await (await get_client()).list_data_model_columns(data_model_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_swap_data_model_sources(data_model_id: str, body: dict[str, Any]) -> str:
    """Swap data sources for a data model."""
    return json.dumps(await (await get_client()).swap_data_model_sources(data_model_id, body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_data_model_lineage(data_model_id: str) -> str:
    """List lineage for a data model."""
    return json.dumps(await (await get_client()).list_data_model_lineage(data_model_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_tag_data_model(data_model_id: str, tag_name: str) -> str:
    """Apply a version tag to a data model by tag NAME. The Sigma API takes the tag name here, not its ID."""
    return json.dumps(await (await get_client()).tag_data_model(data_model_id, tag_name), indent=2)


# ─── Reports ──────────────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_reports(limit: int = 200) -> str:
    """List all reports in the organization."""
    return json.dumps(await (await get_client()).list_reports(limit), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_report(report_id: str) -> str:
    """Get report metadata."""
    return json.dumps(await (await get_client()).get_report(report_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_create_report(body: dict[str, Any]) -> str:
    """Create a report."""
    return json.dumps(await (await get_client()).create_report(body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_duplicate_report(report_id: str, name: str, destination_folder_id: str) -> str:
    """Duplicate a report.

    report_id: ID of the report to clone.
    name: Name for the new cloned report (required by the API).
    destination_folder_id: ID of the destination folder to clone into (required by the API).
    """
    if not name or not name.strip():
        return _invalid_request("name is required by the Sigma copy API")
    if not destination_folder_id or not destination_folder_id.strip():
        return _invalid_request("destination_folder_id is required by the Sigma copy API")
    body = {"name": name, "destinationFolderId": destination_folder_id}
    return json.dumps(await (await get_client()).duplicate_report(report_id, body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_report_sources(report_id: str) -> str:
    """List data sources used by a report."""
    return json.dumps(await (await get_client()).list_report_sources(report_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_swap_report_sources(
    report_id: str,
    connection_mapping: list[dict[str, Any]] | None = None,
    source_mapping: list[dict[str, Any]] | None = None,
) -> str:
    """Swap data sources on a report. Use connectionMapping for connection-level swaps, sourceMapping for table-level swaps."""
    body: dict[str, Any] = {}
    if connection_mapping:
        body["connectionMapping"] = connection_mapping
    if source_mapping:
        body["sourceMapping"] = source_mapping
    return json.dumps(await (await get_client()).swap_report_sources(report_id, body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_report_elements(report_id: str) -> str:
    """List elements in a report."""
    return json.dumps(await (await get_client()).list_report_elements(report_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_report_queries(report_id: str) -> str:
    """List SQL queries in a report."""
    return json.dumps(await (await get_client()).list_report_queries(report_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_report_lineage(report_id: str) -> str:
    """List lineage for a report."""
    return json.dumps(await (await get_client()).list_report_lineage(report_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_export_report(report_id: str, format: str = "pdf") -> str:
    """Export a report. format: 'pdf', 'png', 'csv', 'xlsx'."""
    return json.dumps(await (await get_client()).export_report(report_id, {"format": format}), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_report_schedules(report_id: str) -> str:
    """List scheduled exports for a report."""
    return json.dumps(await (await get_client()).list_report_schedules(report_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_create_report_schedule(report_id: str, body: dict[str, Any]) -> str:
    """Create a scheduled export for a report."""
    return json.dumps(await (await get_client()).create_report_schedule(report_id, body), indent=2)


# ─── Deployment Policies ──────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_deployments() -> str:
    """List all deployment policies."""
    return json.dumps(await (await get_client()).list_deployments(), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_deployment(policy_id: str) -> str:
    """Get a deployment policy."""
    return json.dumps(await (await get_client()).get_deployment(policy_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_create_deployment(name: str, body: dict[str, Any]) -> str:
    """Create a deployment policy."""
    body["name"] = name
    return json.dumps(await (await get_client()).create_deployment(body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_archive_deployment(policy_id: str, confirm: bool = False) -> str:
    """Delete (archive) a deployment policy. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    result = await (await get_client()).delete_deployment(policy_id)
    return json.dumps({"status": "deleted", "statusCode": result})


@mcp.tool()
@sigma_tool
async def sigma_deactivate_member(member_id: str, confirm: bool = False) -> str:
    """Deactivate a member. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    return json.dumps(await (await get_client()).deactivate_member(member_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_deployment_documents(policy_id: str) -> str:
    """List documents in a deployment policy."""
    return json.dumps(await (await get_client()).list_deployment_documents(policy_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_add_deployment_documents(policy_id: str, inode_ids: list[str]) -> str:
    """Add workbooks/reports to a deployment policy."""
    return json.dumps(await (await get_client()).add_deployment_documents(policy_id, {"inodeIds": inode_ids}), indent=2)


# ─── Tenants ──────────────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_tenants() -> str:
    """List all tenant organizations (for multi-tenant deployments)."""
    return json.dumps(await (await get_client()).list_tenants(), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_tenant(tenant_id: str) -> str:
    """Get tenant organization details."""
    return json.dumps(await (await get_client()).get_tenant(tenant_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_create_tenant(name: str, body: dict[str, Any] | None = None) -> str:
    """Create a tenant organization."""
    data = body or {}
    data["name"] = name
    return json.dumps(await (await get_client()).create_tenant(data), indent=2)


# ─── API Connectors ───────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_api_connectors() -> str:
    """List all API connectors (custom data integrations)."""
    return json.dumps(await (await get_client()).list_api_connectors(), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_api_connector(connector_id: str) -> str:
    """Get details for an API connector."""
    return json.dumps(await (await get_client()).get_api_connector(connector_id), indent=2)


# ─── Source Swap Policies ─────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_source_swap_policies() -> str:
    """List all source swap policies (rules for automatic source swapping on deployment)."""
    return json.dumps(await (await get_client()).list_source_swap_policies(), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_source_swap_policy(policy_id: str) -> str:
    """Get a source swap policy."""
    return json.dumps(await (await get_client()).get_source_swap_policy(policy_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_create_source_swap_policy(body: dict[str, Any]) -> str:
    """Create a source swap policy for automated deployments."""
    return json.dumps(await (await get_client()).create_source_swap_policy(body), indent=2)


# ─── Shared Templates ─────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_shared_templates() -> str:
    """List templates shared with your organization from other orgs."""
    return json.dumps(await (await get_client()).list_shared_templates(), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_accept_shared_template(share_id: str) -> str:
    """Accept a pending template share from another organization."""
    return json.dumps(await (await get_client()).accept_shared_template(share_id), indent=2)


# ─── Members ──────────────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_members(limit: int = 200, summary_only: bool = False) -> str:
    """List all members in the organization. Pass summary_only=True for concise token-efficient response."""
    data = await (await get_client()).list_members(limit)
    if summary_only:
        data = _summarize_list(data, ["memberId", "email", "firstName", "lastName", "memberType"])
    return json.dumps(data, indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_member(member_id: str) -> str:
    """Get member details including homeFolderId."""
    return json.dumps(await (await get_client()).get_member(member_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_create_member(email: str, first_name: str, last_name: str, member_type: str = "viewer") -> str:
    """Create a new member. member_type: 'admin', 'creator', 'viewer'."""
    return json.dumps(
        await (await get_client()).create_member(
            {"email": email, "firstName": first_name, "lastName": last_name, "memberType": member_type}
        ),
        indent=2,
    )


@mcp.tool()
@sigma_tool
async def sigma_update_member(member_id: str, body: dict[str, Any]) -> str:
    """Update member properties (firstName, lastName, memberType, isActive, etc.)."""
    return json.dumps(await (await get_client()).update_member(member_id, body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_current_user() -> str:
    """Get the current authenticated user's details."""
    return json.dumps(await (await get_client()).get_current_user(), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_member_teams(member_id: str) -> str:
    """List teams a member belongs to."""
    return json.dumps(await (await get_client()).list_member_teams(member_id), indent=2)


# ─── Teams ────────────────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_teams(limit: int = 200, summary_only: bool = False) -> str:
    """List all teams. Pass summary_only=True for concise token-efficient response."""
    data = await (await get_client()).list_teams(limit)
    if summary_only:
        data = _summarize_list(data, ["teamId", "name", "description"])
    return json.dumps(data, indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_team(team_id: str) -> str:
    """Get team details."""
    return json.dumps(await (await get_client()).get_team(team_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_create_team(name: str, description: str = "") -> str:
    """Create a new team."""
    return json.dumps(await (await get_client()).create_team({"name": name, "description": description}), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_delete_team(team_id: str, confirm: bool = False) -> str:
    """Delete a team. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).delete_team(team_id)
    return json.dumps({"status": code})


@mcp.tool()
@sigma_tool
async def sigma_list_team_members(team_id: str) -> str:
    """List members of a team."""
    return json.dumps(await (await get_client()).list_team_members(team_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_update_team_members(team_id: str, add: list[str] | None = None, remove: list[str] | None = None) -> str:
    """Add or remove members from a team. Provide lists of member IDs."""
    body: dict[str, Any] = {}
    if add:
        body["add"] = add
    if remove:
        body["remove"] = remove
    return json.dumps(await (await get_client()).update_team_members(team_id, body), indent=2)


# ─── Files / Folders ──────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_files(parent_id: str | None = None, type_filter: str | None = None) -> str:
    """List files/folders. Optionally filter by parentId or type ('workbook', 'folder', 'data-model', 'template')."""
    params: dict[str, Any] = {"limit": 200}
    if parent_id:
        params["parentId"] = parent_id
    if type_filter:
        params["typeFilters"] = type_filter
    return json.dumps(await (await get_client()).list_files(params), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_create_folder(name: str, parent_id: str) -> str:
    """Create a folder."""
    return json.dumps(
        await (await get_client()).create_file({"name": name, "parentId": parent_id, "type": "folder"}), indent=2
    )


@mcp.tool()
@sigma_tool
async def sigma_update_file(inode_id: str, body: dict[str, Any]) -> str:
    """Update file properties (name, parentId for moving)."""
    return json.dumps(await (await get_client()).update_file(inode_id, body), indent=2)


# ─── Tags ─────────────────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_tags() -> str:
    """List all tags in the organization."""
    return json.dumps(await (await get_client()).list_tags(), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_create_tag(name: str, color: str = "cyan") -> str:
    """Create a new tag (used for version promotion like 'Production', 'Staging').

    color: Tag color, required by the Sigma API. One of: cyan, grass, violet, plum, amber, bronze.
           Defaults to 'cyan'.
    """
    body = {"name": name, "color": color}
    return json.dumps(await (await get_client()).create_tag(body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_delete_tag(tag_id: str, confirm: bool = False) -> str:
    """Delete a tag. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).delete_tag(tag_id)
    return json.dumps({"status": code})


# ─── User Attributes ─────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_user_attributes() -> str:
    """List all user attributes (used for row-level security and dynamic parameters)."""
    return json.dumps(await (await get_client()).list_user_attributes(), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_create_user_attribute(
    name: str,
    default_value: str = "",
    description: str = "",
) -> str:
    """Create a user attribute for RLS or dynamic parameters.

    name: Attribute name (e.g. 'Region', 'CustomerID').
    default_value: Default string value assigned when no override exists.
    description: Optional description of the attribute.
    """
    body: dict[str, Any] = {
        "name": name,
        "defaultValue": {"type": "string", "val": default_value},
    }
    if description:
        body["description"] = description
    return json.dumps(await (await get_client()).create_user_attribute(body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_set_user_attribute_for_teams(
    attribute_id: str,
    assignments: list[dict[str, Any]],
) -> str:
    """Set a user attribute value for specific teams.

    assignments: List of objects, each with 'teamId' (str) and 'value' (str).
    Example: [{"teamId": "abc123", "value": "US"}]
    """
    body = {
        "assignments": [
            {"teamId": a["teamId"], "value": {"type": "string", "val": str(a["value"])}} for a in assignments
        ]
    }
    return json.dumps(await (await get_client()).set_user_attribute_for_teams(attribute_id, body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_set_user_attribute_for_tenants(
    attribute_id: str,
    assignments: list[dict[str, Any]],
) -> str:
    """Set a user attribute value for specific tenants.

    assignments: List of objects, each with 'tenantOrganizationId' (str) and 'value' (str).
    Example: [{"tenantOrganizationId": "org123", "value": "US"}]
    """
    body = {
        "assignments": [
            {"tenantOrganizationId": a["tenantOrganizationId"], "value": {"type": "string", "val": str(a["value"])}}
            for a in assignments
        ]
    }
    return json.dumps(await (await get_client()).set_user_attribute_for_tenants(attribute_id, body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_user_attribute_users(attribute_id: str) -> str:
    """Get all user assignments for a user attribute."""
    return json.dumps(await (await get_client()).get_user_attribute_user_assignments(attribute_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_user_attribute_teams(attribute_id: str) -> str:
    """Get all team assignments for a user attribute."""
    return json.dumps(await (await get_client()).get_user_attribute_team_assignments(attribute_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_user_attribute_tenants(attribute_id: str) -> str:
    """Get all tenant assignments for a user attribute."""
    return json.dumps(await (await get_client()).get_user_attribute_tenant_assignments(attribute_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_update_user_attribute_for_users(
    attribute_id: str,
    user_ids: list[str],
    confirm: bool = False,
) -> str:
    """Revoke a user attribute assignment for specific users. Requires confirm=True.

    user_ids: List of user IDs whose attribute assignments should be removed.
    Example: ["user-uuid-1", "user-uuid-2"]

    To assign (not revoke), use sigma_set_user_attribute_for_users instead
    (direct POST to /v2/user-attributes/{id}/users not yet exposed as a
    dedicated tool — use sigma_create_grant with a raw body as a workaround).
    """
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    body = {"delete": [{"userId": uid} for uid in user_ids]}
    return json.dumps(await (await get_client()).update_user_attribute_for_users(attribute_id, body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_update_user_attribute_for_teams(
    attribute_id: str,
    team_ids: list[str],
    confirm: bool = False,
) -> str:
    """Revoke a user attribute assignment for specific teams. Requires confirm=True.

    team_ids: List of team IDs whose attribute assignments should be removed.
    Example: ["team-uuid-1", "team-uuid-2"]
    """
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    body = {"delete": [{"teamId": tid} for tid in team_ids]}
    return json.dumps(await (await get_client()).update_user_attribute_for_teams(attribute_id, body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_update_user_attribute_for_tenants(
    attribute_id: str,
    tenant_org_ids: list[str],
    confirm: bool = False,
) -> str:
    """Revoke a user attribute assignment for specific tenants. Requires confirm=True.

    tenant_org_ids: List of tenant organization IDs whose attribute assignments should be removed.
    Example: ["org-uuid-1", "org-uuid-2"]
    """
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    body = {"delete": [{"tenantOrganizationId": oid} for oid in tenant_org_ids]}
    return json.dumps(await (await get_client()).update_user_attribute_for_tenants(attribute_id, body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_delete_user_attribute_for_user(attribute_id: str, user_id: str, confirm: bool = False) -> str:
    """Delete a user attribute assignment for a specific user. DESTRUCTIVE. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).delete_user_attribute_for_user(attribute_id, user_id)
    return json.dumps({"status": code})


@mcp.tool()
@sigma_tool
async def sigma_delete_user_attribute_for_team(attribute_id: str, team_id: str, confirm: bool = False) -> str:
    """Delete a user attribute assignment for a specific team. DESTRUCTIVE. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).delete_user_attribute_for_team(attribute_id, team_id)
    return json.dumps({"status": code})


@mcp.tool()
@sigma_tool
async def sigma_delete_user_attribute_for_tenant(attribute_id: str, tenant_org_id: str, confirm: bool = False) -> str:
    """Delete a user attribute assignment for a specific tenant. DESTRUCTIVE. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).delete_user_attribute_for_tenant(attribute_id, tenant_org_id)
    return json.dumps({"status": code})


# ─── Workspaces ───────────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_workspaces(limit: int = 200) -> str:
    """List all workspaces."""
    return json.dumps(await (await get_client()).list_workspaces(limit), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_workspace(workspace_id: str) -> str:
    """Get workspace details."""
    return json.dumps(await (await get_client()).get_workspace(workspace_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_create_workspace(name: str) -> str:
    """Create a new workspace."""
    return json.dumps(await (await get_client()).create_workspace({"name": name}), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_delete_workspace(workspace_id: str, confirm: bool = False) -> str:
    """Delete a workspace. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).delete_workspace(workspace_id)
    return json.dumps({"status": code})


@mcp.tool()
@sigma_tool
async def sigma_list_workspace_grants(workspace_id: str) -> str:
    """List permission grants on a workspace."""
    return json.dumps(await (await get_client()).list_workspace_grants(workspace_id), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_grant_workspace_access(workspace_id: str, grant_type: str, grantee_id: str, permission: str) -> str:
    """Grant workspace access.

    grant_type: 'member' or 'team'.
    permission: 'view', 'explore', 'organize' (Contribute), or 'edit' (Manage).
    """
    gt = grant_type.strip().lower()
    if gt not in ("member", "team"):
        return _invalid_request("grant_type must be 'member' or 'team'")
    grantee_key = "memberId" if gt == "member" else "teamId"
    body = {"grants": [{"grantee": {grantee_key: grantee_id}, "permission": permission}]}
    return json.dumps(await (await get_client()).grant_workspace_access(workspace_id, body), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_delete_workspace_grant(workspace_id: str, grant_id: str, confirm: bool = False) -> str:
    """Delete a permission grant from a workspace. DESTRUCTIVE. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).delete_workspace_grant(workspace_id, grant_id)
    return json.dumps({"status": code})


# ─── Account Types ────────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_account_types() -> str:
    """List all account types (license types) in the organization."""
    return json.dumps(await (await get_client()).list_account_types(), indent=2)


# ─── Grants (generic) ─────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_grants(inode_id: str) -> str:
    """List grants for a specific file/workbook/data model by inodeId. Required: inodeId."""
    return json.dumps(await (await get_client()).list_grants({"inodeId": inode_id, "limit": 200}), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_create_grant(body: dict[str, Any]) -> str:
    """Create or update a grant. Body must include inodeId, granteeId, permission, type."""
    return json.dumps(await (await get_client()).create_or_update_grant(body), indent=2)


# ─── Translations ─────────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_translations() -> str:
    """List organization translation files."""
    return json.dumps(await (await get_client()).list_translations(), indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# COMPOSITE RECIPE TOOLS (multi-step workflows)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
@sigma_tool
async def sigma_sync_all_tables_in_schema(connection_id: str, database: str, schema: str) -> str:
    """Sync all tables in a warehouse schema so they become visible in Sigma."""
    if not connection_id or not connection_id.strip():
        return _invalid_request("connection_id is required")
    if not database or not database.strip():
        return _invalid_request("database is required")
    if not schema or not schema.strip():
        return _invalid_request("schema is required")
    c = await get_client()
    path = [database, schema]
    await c.sync_connection(connection_id, path)
    return json.dumps({"synced_path": path, "status": "ok"})


@mcp.tool()
@sigma_tool
async def sigma_copy_workbook_to_member(workbook_id: str, member_id: str, name: str | None = None) -> str:
    """Copy a workbook into a member's My Documents folder.

    name: Optional name for the copied workbook. Defaults to the original workbook name.
    """
    if not workbook_id or not workbook_id.strip():
        return _invalid_request("workbook_id is required")
    if not member_id or not member_id.strip():
        return _invalid_request("member_id is required")
    c = await get_client()
    member = await c.get_member(member_id)
    home_folder = member.get("homeFolderId") if isinstance(member, dict) else None
    if not home_folder:
        return json.dumps({"error": "Member has no homeFolderId"})
    # Resolve workbook name if not explicitly provided
    copy_name = name
    if not copy_name:
        wb = await c.get_workbook(workbook_id)
        copy_name = wb.get("name", f"Copy of {workbook_id}") if isinstance(wb, dict) else f"Copy of {workbook_id}"
    body: dict[str, Any] = {"name": copy_name, "destinationFolderId": home_folder}
    result = await c.duplicate_workbook(workbook_id, body)
    return json.dumps(result, indent=2)


@mcp.tool()
@sigma_tool
async def sigma_materialize_and_wait(workbook_id: str, element_id: str, timeout_seconds: int = 300) -> str:
    """Trigger materialization and poll until complete or timeout."""
    if not workbook_id or not workbook_id.strip():
        return _invalid_request("workbook_id is required")
    if not element_id or not element_id.strip():
        return _invalid_request("element_id is required")
    c = await get_client()
    job = await c.materialize_workbook(workbook_id, {"elementId": element_id})
    job_id = job.get("materializationId") or job.get("jobId") or job.get("id") if isinstance(job, dict) else None
    if not job_id:
        return json.dumps({"error": "Could not extract job ID from response", "raw": job})

    import time as _time

    deadline = _time.time() + timeout_seconds
    status: Any = None
    while _time.time() < deadline:
        status = await c.get_materialization_job(workbook_id, job_id)
        state = (status.get("status", "") if isinstance(status, dict) else "").lower()
        if state in ("completed", "complete", "done"):
            return json.dumps({"status": "completed", "job": status}, indent=2)
        if state in ("failed", "error", "cancelled"):
            return json.dumps({"status": state, "job": status}, indent=2)
        await asyncio.sleep(5)

    return json.dumps({"status": "timeout", "last_check": status}, indent=2)


@mcp.tool()
@sigma_tool
async def sigma_onboard_member(
    email: str, first_name: str, last_name: str, member_type: str = "viewer", team_ids: list[str] | None = None
) -> str:
    """Onboard a new member: create account then add to teams."""
    if not email or not email.strip():
        return _invalid_request("email is required")
    if not first_name or not first_name.strip():
        return _invalid_request("first_name is required")
    if not last_name or not last_name.strip():
        return _invalid_request("last_name is required")
    if member_type not in ("viewer", "creator", "admin"):
        return _invalid_request(f"member_type must be one of: viewer, creator, admin (got '{member_type}')")
    c = await get_client()
    member = await c.create_member(
        {"email": email, "firstName": first_name, "lastName": last_name, "memberType": member_type}
    )
    member_id = member.get("memberId") if isinstance(member, dict) else None
    teams_added: list[str] = []
    if team_ids and member_id:
        for tid in team_ids:
            try:
                await c.update_team_members(tid, {"add": [member_id]})
                teams_added.append(tid)
            except Exception as e:
                teams_added.append(f"{tid}: FAILED ({e})")
    return json.dumps({"member": member, "teams_added": teams_added}, indent=2)


@mcp.tool()
@sigma_tool
async def sigma_bulk_assign_team_members(team_id: str, member_ids: list[str]) -> str:
    """Add multiple members to a team in one call."""
    if not team_id or not team_id.strip():
        return _invalid_request("team_id is required")
    if not member_ids:
        return _invalid_request("member_ids must be a non-empty list")
    c = await get_client()
    result = await c.update_team_members(team_id, {"add": member_ids})
    return json.dumps(result, indent=2)


@mcp.tool()
@sigma_tool
async def sigma_deploy_template_to_folder(
    template_id: str, folder_id: str, name: str, connection_mapping: list[dict[str, Any]] | None = None
) -> str:
    """Full deployment: instantiate a template into a folder, then optionally swap its sources."""
    if not template_id or not template_id.strip():
        return _invalid_request("template_id is required")
    if not folder_id or not folder_id.strip():
        return _invalid_request("folder_id is required")
    if not name or not name.strip():
        return _invalid_request("name is required")
    c = await get_client()
    wb = await c.save_workbook_from_template(template_id, folder_id, name)
    workbook_id = wb.get("workbookId") if isinstance(wb, dict) else None
    if connection_mapping and workbook_id:
        swap_result = await c.swap_workbook_sources(workbook_id, {"connectionMapping": connection_mapping})
        return json.dumps({"workbook": wb, "swap": swap_result}, indent=2)
    return json.dumps({"workbook": wb}, indent=2)


@mcp.tool()
@sigma_tool
async def sigma_promote_workbook(workbook_id: str, tag_name: str, tag_color: str = "cyan") -> str:
    """Promote a workbook by tagging it (e.g., 'Production'). Creates tag if it doesn't exist.

    tag_color: Color for newly created tags. One of: cyan, grass, violet, plum, amber, bronze.
               Ignored if the tag already exists. Defaults to 'cyan'.
    """
    if not workbook_id or not workbook_id.strip():
        return _invalid_request("workbook_id is required")
    if not tag_name or not tag_name.strip():
        return _invalid_request("tag_name is required")
    c = await get_client()
    tags = await c.list_tags()
    tag_id: str | None = None
    entries = tags.get("entries", []) if isinstance(tags, dict) else []
    for t in entries:
        if isinstance(t, dict) and t.get("name", "").lower() == tag_name.lower():
            # Sigma returns versionTagId (not tagId or id)
            tag_id = t.get("versionTagId")
            break
    if not tag_id:
        new_tag = await c.create_tag({"name": tag_name, "color": tag_color})
        # Sigma create_tag response also uses versionTagId
        tag_id = new_tag.get("versionTagId") if isinstance(new_tag, dict) else None
    if not tag_id:
        return _invalid_request("Could not resolve or create tag")
    # The tag_workbook endpoint takes the tag NAME, not the ID. We still resolve/create
    # the tag first so the caller gets a stable tag_id back in the response.
    result = await c.tag_workbook(workbook_id, tag_name)
    return json.dumps({"tag_id": tag_id, "tag_name": tag_name, "result": result}, indent=2)


# ─── Phase B1 Recipe Tools ─────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_export_and_download(
    workbook_id: str,
    format: str = "csv",
    element_id: str | None = None,
    parameters: dict[str, Any] | None = None,
    layout: str = "portrait",
    timeout_seconds: int = 300,
    max_bytes: int = 10_000_000,
) -> str:
    """Export a workbook/element and download the result. Polls until ready.

    format: 'csv', 'pdf', 'xlsx', 'png'. layout: 'portrait' or 'landscape' (pdf only).
    parameters: Sigma control overrides e.g. {'DateRange': 'min:2024-01-01,max:2024-01-31'}.
    max_bytes: maximum allowed response size (default 10 MB). Returns size info without
    content if exceeded.
    Returns base64-encoded file content on success.
    """
    import base64
    import time as _time

    if not workbook_id or not workbook_id.strip():
        return _invalid_request("workbook_id is required")

    c = await get_client()
    export_body: dict[str, Any] = {"format": {"type": format}, "runAsynchronously": True}
    if format == "pdf":
        export_body["format"]["layout"] = layout
    if element_id:
        export_body["elementId"] = element_id
    if parameters:
        export_body["parameters"] = parameters

    result = await c.export_workbook(workbook_id, export_body)
    query_id = result.get("queryId") if isinstance(result, dict) else None
    if not query_id:
        return json.dumps({"error": "No queryId in export response", "raw": result})

    deadline = _time.time() + timeout_seconds
    backoff = 2.0
    while _time.time() < deadline:
        r = await c.download_query_raw(query_id)
        if r.status_code == 200:
            size = len(r.content)
            if size > max_bytes:
                return json.dumps(
                    {
                        "status": "completed",
                        "format": format,
                        "size_bytes": size,
                        "truncated": True,
                        "error": f"Response size {size} bytes exceeds max_bytes={max_bytes}",
                    }
                )
            content_b64 = base64.b64encode(r.content).decode("ascii")
            return json.dumps(
                {
                    "status": "completed",
                    "format": format,
                    "size_bytes": size,
                    "content_base64": content_b64,
                }
            )
        # 204 = not ready
        await asyncio.sleep(backoff)
        backoff = min(backoff * 1.5, 15.0)

    return json.dumps({"error": "timeout", "query_id": query_id, "timeout_seconds": timeout_seconds})


@mcp.tool()
@sigma_tool
async def sigma_reassign_workbook_ownership(old_owner_email: str, new_owner_email: str, dry_run: bool = True) -> str:
    """Transfer all workbooks from one member to another.

    Resolves members by email, finds all workbooks owned by old owner, then
    PATCHes /v2/files/{id} to reassign. dry_run=True (default) reports what
    would change without making changes.
    """
    if not old_owner_email or not old_owner_email.strip():
        return _invalid_request("old_owner_email is required")
    if not new_owner_email or not new_owner_email.strip():
        return _invalid_request("new_owner_email is required")

    c = await get_client()

    # Resolve member IDs
    old_results = await c.search_members(old_owner_email)
    old_entries = old_results.get("entries", []) if isinstance(old_results, dict) else []
    if not old_entries:
        return json.dumps({"error": f"No member found for email: {old_owner_email}"})
    old_member_id = old_entries[0]["memberId"]

    new_results = await c.search_members(new_owner_email)
    new_entries = new_results.get("entries", []) if isinstance(new_results, dict) else []
    if not new_entries:
        return json.dumps({"error": f"No member found for email: {new_owner_email}"})
    new_member_id = new_entries[0]["memberId"]

    # Get all workbooks owned by old member
    all_files: list[dict[str, Any]] = []
    params: dict[str, Any] = {"limit": 100, "typeFilters": "workbook"}
    _seen_cursors: set[str] = set()
    _MAX_PAGES = 200
    for _ in range(_MAX_PAGES):
        page_data = await c.get(f"/v2/members/{old_member_id}/files", params)
        if isinstance(page_data, dict):
            entries = page_data.get("entries", [])
            all_files.extend(entries)
            next_page = page_data.get("nextPage")
            if not next_page or next_page in _seen_cursors:
                break
            _seen_cursors.add(next_page)
            params["page"] = next_page
        else:
            break

    # Filter to only workbooks where ownerId matches old member
    owned = [f for f in all_files if f.get("ownerId") == old_member_id]

    if dry_run:
        return json.dumps(
            {
                "dry_run": True,
                "old_owner": {"email": old_owner_email, "memberId": old_member_id},
                "new_owner": {"email": new_owner_email, "memberId": new_member_id},
                "workbooks_to_transfer": len(owned),
                "workbooks": [{"id": f.get("id") or f.get("inodeId"), "name": f.get("name")} for f in owned],
            },
            indent=2,
        )

    # Execute transfers
    results: list[dict[str, Any]] = []
    for f in owned:
        fid = f.get("id") or f.get("inodeId")
        if not isinstance(fid, str):
            results.append({"id": fid, "name": f.get("name"), "status": "failed", "error": "missing file ID"})
            continue
        try:
            await c.update_file(fid, {"ownerId": new_member_id})
            results.append({"id": fid, "name": f.get("name"), "status": "transferred"})
        except Exception as e:
            results.append({"id": fid, "name": f.get("name"), "status": "failed", "error": str(e)})

    return json.dumps(
        {
            "old_owner": {"email": old_owner_email, "memberId": old_member_id},
            "new_owner": {"email": new_owner_email, "memberId": new_member_id},
            "results": results,
            "transferred": sum(1 for r in results if r["status"] == "transferred"),
            "failed": sum(1 for r in results if r["status"] == "failed"),
        },
        indent=2,
    )


@mcp.tool()
@sigma_tool
async def sigma_list_workbooks_shared_with_member(member_id: str) -> str:
    """List all workbooks accessible to a member with full workbook metadata.

    Cross-references member's file list against the full workbook catalog.
    """
    if not member_id or not member_id.strip():
        return _invalid_request("member_id is required")

    c = await get_client()

    # Get member's workbook files (auto-paginate)
    member_files = await c.auto_paginate(f"/v2/members/{member_id}/files", {"typeFilters": "workbook"})
    member_wb_ids = {f.get("workbookId") or f.get("id") for f in member_files}

    # Get all workbooks
    all_workbooks = await c.list_all_workbooks()

    # Cross-reference
    shared = [wb for wb in all_workbooks if wb.get("workbookId") in member_wb_ids]

    return json.dumps(
        {
            "member_id": member_id,
            "total_accessible": len(shared),
            "workbooks": shared,
        },
        indent=2,
    )


@mcp.tool()
@sigma_tool
async def sigma_list_all_input_tables() -> str:
    """Scan all workbooks to find input-table elements.

    Returns a list of input tables with their workbook, page, and element context.
    Errors on individual workbooks/pages are collected rather than silently swallowed.
    """
    c = await get_client()
    all_workbooks = await c.list_all_workbooks()

    input_tables: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(5)

    async def scan_workbook(wb: dict[str, Any]) -> None:
        wb_id = wb.get("workbookId", "")
        wb_name = wb.get("name", "")
        async with sem:
            try:
                pages_data = await c.list_workbook_pages(wb_id)
                pages = pages_data.get("entries", []) if isinstance(pages_data, dict) else []
            except Exception as e:
                errors.append({"workbookId": wb_id, "workbookName": wb_name, "stage": "pages", "error": str(e)})
                return

            for page in pages:
                page_id = page.get("pageId", "")
                page_name = page.get("name", "")
                try:
                    elements_data = await c.list_workbook_page_elements(wb_id, page_id)
                    elements = elements_data.get("entries", []) if isinstance(elements_data, dict) else []
                except Exception as e:
                    errors.append({"workbookId": wb_id, "pageId": page_id, "stage": "elements", "error": str(e)})
                    continue

                for el in elements:
                    if el.get("type") == "input-table":
                        input_tables.append(
                            {
                                "workbookId": wb_id,
                                "workbookName": wb_name,
                                "pageId": page_id,
                                "pageName": page_name,
                                "elementId": el.get("elementId", ""),
                                "elementName": el.get("name", ""),
                            }
                        )

    await asyncio.gather(*[scan_workbook(wb) for wb in all_workbooks])

    return json.dumps(
        {
            "total_input_tables": len(input_tables),
            "input_tables": input_tables,
            "workbooks_scanned": len(all_workbooks),
            "errors": errors,
        },
        indent=2,
    )


# ─── Phase B2 Recipe Tools ─────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_bulk_deactivate_members(name_pattern: str, dry_run: bool = True, confirm: bool = False) -> str:
    """Deactivate members matching a name pattern (regex on firstName+lastName).

    DESTRUCTIVE: requires both dry_run=False AND confirm=True to execute.
    Default behavior (dry_run=True) reports matches without making changes.
    Uses ?includeInactive=true to avoid re-deactivating already-inactive members.

    Safety:
      - Requires env SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1 for the tool to be registered.
      - Catch-all patterns (e.g. '.*', '.+', '.') are rejected.
      - Hard cap: refuses if pattern matches more than 10 active members.
    """
    import re

    if not name_pattern or not name_pattern.strip():
        return _invalid_request("name_pattern is required")

    # Reject catch-all patterns that would match every member
    _CATCHALL_PATTERNS = {".*", ".+", "^.*$", "^.+$", "", ".", "^$"}
    if name_pattern.strip() in _CATCHALL_PATTERNS:
        return json.dumps(
            {
                "error": f"Catch-all pattern {name_pattern!r} is rejected for safety. "
                "Use a specific name pattern to target individual members."
            }
        )
    # Also reject any pattern that matches an empty string
    try:
        if re.compile(name_pattern, re.IGNORECASE).search(""):
            return json.dumps(
                {
                    "error": f"Pattern {name_pattern!r} matches empty string and is too broad. "
                    "Use a specific name pattern."
                }
            )
    except re.error:
        pass  # Will be caught below

    c = await get_client()
    # Get all members including inactive to avoid double-deactivation
    all_members = await c.auto_paginate("/v2/members", {"includeInactive": "true"})

    # Match pattern against "firstName lastName"
    try:
        pattern = re.compile(name_pattern, re.IGNORECASE)
    except re.error as e:
        return json.dumps({"error": f"Invalid regex pattern: {e}"})

    matches = []
    for m in all_members:
        full_name = f"{m.get('firstName', '')} {m.get('lastName', '')}".strip()
        if pattern.search(full_name):
            matches.append(m)

    # Filter out already-inactive
    active_matches = [m for m in matches if not m.get("isInactive", False) and m.get("isActive", True)]

    # Hard cap: refuse if more than 10 active members match
    _MAX_BULK_DEACTIVATE = 10
    if len(active_matches) > _MAX_BULK_DEACTIVATE:
        return json.dumps(
            {
                "error": f"Pattern matches {len(active_matches)} active members, exceeding "
                f"the safety cap of {_MAX_BULK_DEACTIVATE}. Use a narrower pattern.",
                "count": len(active_matches),
                "first_10": [f"{m.get('firstName', '')} {m.get('lastName', '')}".strip() for m in active_matches[:10]],
            }
        )

    if dry_run or not confirm:
        return json.dumps(
            {
                "dry_run": dry_run,
                "pattern": name_pattern,
                "total_matches": len(matches),
                "already_inactive": len(matches) - len(active_matches),
                "would_deactivate": len(active_matches),
                "members": [
                    {
                        "memberId": m.get("memberId"),
                        "name": f"{m.get('firstName', '')} {m.get('lastName', '')}",
                        "email": m.get("email"),
                    }
                    for m in active_matches
                ],
                "note": "Set dry_run=False AND confirm=True to execute deactivation",
            },
            indent=2,
        )

    # Execute deactivation
    results: list[dict[str, Any]] = []
    for m in active_matches:
        mid = m.get("memberId", "")
        name = f"{m.get('firstName', '')} {m.get('lastName', '')}"
        if not mid:  # pragma: no cover
            results.append({"memberId": mid, "name": name, "status": "skipped", "reason": "missing memberId"})
            continue
        try:
            await c.deactivate_member(mid)
            results.append({"memberId": mid, "name": name, "status": "deactivated"})
        except Exception as e:
            results.append(
                {
                    "memberId": mid,
                    "name": name,
                    "status": "failed",
                    "error": str(e),
                }
            )

    return json.dumps(
        {
            "pattern": name_pattern,
            "deactivated": sum(1 for r in results if r["status"] == "deactivated"),
            "failed": sum(1 for r in results if r["status"] == "failed"),
            "results": results,
        },
        indent=2,
    )


@mcp.tool()
@sigma_tool
async def sigma_change_member_email(member_id: str, new_email: str) -> str:
    """Change a member's email address via PATCH /v2/members/{id}."""
    if not member_id or not member_id.strip():
        return _invalid_request("member_id is required")
    if not new_email or not new_email.strip():
        return _invalid_request("new_email is required")

    c = await get_client()
    result = await c.update_member(member_id, {"email": new_email})
    return json.dumps(result, indent=2)


@mcp.tool()
@sigma_tool
async def sigma_bulk_remove_team_members(team_id: str, member_emails: list[str], confirm: bool = False) -> str:
    """Remove multiple members from a team by their email addresses.

    Resolves emails to member IDs, then sends a single PATCH to remove all. Requires confirm=True.
    """
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    if not team_id or not team_id.strip():
        return _invalid_request("team_id is required")
    if not member_emails:
        return _invalid_request("member_emails must be a non-empty list")
    if len(member_emails) > 50:
        return _invalid_request(f"Bulk removal cap exceeded ({len(member_emails)} > 50). Process in smaller batches.")

    c = await get_client()
    member_ids: list[str] = []
    not_found: list[str] = []

    for email in member_emails:
        results = await c.search_members(email)
        entries = results.get("entries", []) if isinstance(results, dict) else []
        if entries:
            member_ids.append(entries[0]["memberId"])
        else:
            not_found.append(email)

    if not member_ids:
        return json.dumps({"error": "No valid members found", "not_found": not_found})

    result = await c.update_team_members(team_id, {"remove": member_ids})
    return json.dumps(
        {
            "team_id": team_id,
            "removed": member_ids,
            "not_found": not_found,
            "api_response": result,
        },
        indent=2,
    )


@mcp.tool()
@sigma_tool
async def sigma_bulk_sync_tenant_connections(dry_run: bool = True) -> str:
    """Sync connections across all tenant organizations.

    Lists all tenants, then for each tenant: obtains a tenant-scoped token,
    lists their connections, and syncs each one. Requires multi-tenant auth
    (for_tenant method). Uses bounded concurrency.

    NOTE: Requires PyJWT and the for_tenant() method (Phase C). Returns an
    error if token exchange is not available.
    """
    c = await get_client()

    # Check if for_tenant is available
    if not hasattr(c, "for_tenant"):
        return json.dumps(
            {"error": "Multi-tenant auth (for_tenant) not yet available. Phase C must be implemented first."}
        )

    tenants_data = await c.list_tenants()
    tenants = tenants_data.get("entries", []) if isinstance(tenants_data, dict) else []

    if dry_run:
        return json.dumps(
            {
                "dry_run": True,
                "tenants_found": len(tenants),
                "tenants": [{"orgId": t.get("orgId"), "name": t.get("name")} for t in tenants],
                "note": "Set dry_run=False to execute sync across all tenants",
            },
            indent=2,
        )

    results: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(3)

    async def sync_tenant(tenant: dict[str, Any]) -> None:
        org_id = tenant.get("orgId", "")
        async with sem:
            tc: SigmaClient | None = None
            try:
                tc = await c.for_tenant(org_id)
                conns = await tc.list_connections()
                conn_entries = conns.get("entries", []) if isinstance(conns, dict) else []
                synced = 0
                errors: list[dict[str, Any]] = []
                for conn in conn_entries:
                    cid = conn.get("connectionId", "")
                    try:
                        await tc.sync_connection(cid, [])
                        synced += 1
                    except Exception as e:
                        errors.append({"connectionId": cid, "error": str(e)})
                entry: dict[str, Any] = {"orgId": org_id, "name": tenant.get("name"), "connections_synced": synced}
                if errors:
                    entry["errors"] = errors
                results.append(entry)
            except Exception as e:
                results.append({"orgId": org_id, "name": tenant.get("name"), "error": str(e)})
            finally:
                # Tenant clients borrow the parent's transport, so aclose() is a
                # no-op for them. Called anyway so the contract holds if that
                # ownership ever changes.
                if tc is not None:
                    await tc.aclose()

    await asyncio.gather(*[sync_tenant(t) for t in tenants])

    return json.dumps(
        {
            "tenants_processed": len(results),
            "results": results,
        },
        indent=2,
    )


# ─── Phase C — Enterprise Auth Tools ──────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_tenants_paginated() -> str:
    """List all tenants using cursor-based pagination (nextPageToken model).

    Unlike offset-based pagination, tenants use cursor tokens.
    Returns the complete list of all tenant organizations.
    """
    c = await get_client()
    all_tenants: list[dict[str, Any]] = []
    params: dict[str, Any] = {"limit": 50}
    _seen_tokens: set[str] = set()
    _MAX_PAGES = 200
    for _ in range(_MAX_PAGES):
        data = await c.get("/v2/tenants", params)
        if isinstance(data, dict):
            all_tenants.extend(data.get("entries", []))
            next_token = data.get("nextPageToken")
            if not next_token or next_token in _seen_tokens:
                break
            _seen_tokens.add(next_token)
            params["pageToken"] = next_token
        else:
            break
    return json.dumps({"total": len(all_tenants), "tenants": all_tenants}, indent=2)


@mcp.tool()
@sigma_tool
async def sigma_get_tenant_scoped_info(tenant_org_id: str) -> str:
    """Demonstrate tenant token exchange by calling /whoami through a tenant-scoped client.

    Uses RFC 8693 token exchange to obtain a tenant-scoped token, then
    calls the whoami endpoint to verify the scoped identity.
    """
    if not tenant_org_id or not tenant_org_id.strip():
        return _invalid_request("tenant_org_id is required")

    c = await get_client()
    tc = await c.for_tenant(tenant_org_id)
    try:
        whoami = await tc.get_current_user()
        return json.dumps(
            {
                "tenant_org_id": tenant_org_id,
                "scoped_identity": whoami,
            },
            indent=2,
        )
    finally:
        await tc.aclose()


# ─── Reference ────────────────────────────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_api_capabilities() -> str:
    """Describe what the Sigma REST API can and cannot do programmatically."""
    return json.dumps(
        {
            "supported": {
                "data_models_as_code": "Create/update full semantic layers (tables, calculated columns, metrics) from JSON",
                "template_instantiation": "Create workbooks from templates — the ONLY way to get charts/tables programmatically",
                "save_template_from_workbook": "Capture a workbook's visual design as a reusable template",
                "source_swap": "Repoint a workbook/data model/template at different tables or connections",
                "duplicate_workbook": "Clone an existing workbook",
                "connection_sync": "Force Sigma to re-index a warehouse path so new tables resolve",
                "reports": "Full CRUD, exports, schedules, grants, tags, source swap for reports",
                "deployment_policies": "Manage multi-tenant deployment pipelines with tag-based promotion",
                "lifecycle": "Grants, exports, schedules, materializations, members, teams, tags, workspaces",
            },
            "not_supported": {
                "create_page": "No endpoint exists to add a page to a workbook",
                "create_element": "No endpoint exists to add a chart/table/KPI to a page",
                "workbook_as_code": "Does not exist, not even in beta. Only data models have a code representation.",
                "set_control_defaults": "Control/parameter default values are UI-only",
            },
            "composite_recipes": {
                "sigma_deploy_template_to_folder": "Instantiate template + swap sources in one call",
                "sigma_materialize_and_wait": "Trigger materialization + poll until done",
                "sigma_onboard_member": "Create member + add to teams",
                "sigma_bulk_assign_team_members": "Add N members to a team",
                "sigma_copy_workbook_to_member": "Copy workbook to member's My Documents",
                "sigma_promote_workbook": "Tag a workbook for version promotion (creates tag if needed)",
                "sigma_sync_all_tables_in_schema": "Sync a full schema path so tables resolve",
            },
            "gotchas": {
                "templateId_on_create_workbook": "POST /v2/workbooks silently ignores templateId. Use POST /v2/templates/save_workbook.",
                "data_model_update_verb": "Use PUT /v2/dataModels/{id}/spec — PATCH and POST both 404.",
                "workbook_deletion": "Use DELETE /v2/files/{inodeId} — there is no delete-workbook endpoint.",
                "sources_endpoint_params": "GET /v2/workbooks/{id}/sources returns 400 if given query params.",
                "regional_base_url": "The generic aws-api host authenticates but then fails; use your region-specific host.",
                "new_schemas": "Call connection sync before referencing newly created warehouse schemas.",
                "swap_source_body": "connectionMapping uses fromId/toId and paths[].fromPath/toPath — not source/target.",
            },
        },
        indent=2,
    )


# ─── Paginated (auto_paginate) tools ──────────────────────────────────────────


@mcp.tool()
@sigma_tool
async def sigma_list_all_workbooks() -> str:
    """List ALL workbooks in the organization, automatically following pagination."""
    return json.dumps(await (await get_client()).list_all_workbooks(), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_all_members() -> str:
    """List ALL members in the organization, automatically following pagination."""
    return json.dumps(await (await get_client()).list_all_members(), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_all_teams() -> str:
    """List ALL teams in the organization, automatically following pagination."""
    return json.dumps(await (await get_client()).list_all_teams(), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_all_files(parent_id: str | None = None, type_filter: str | None = None) -> str:
    """List ALL files/folders in the organization, automatically following pagination."""
    params: dict[str, Any] = {}
    if parent_id:
        params["parentId"] = parent_id
    if type_filter:
        params["typeFilters"] = type_filter
    return json.dumps(await (await get_client()).list_all_files(params or None), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_all_reports() -> str:
    """List ALL reports in the organization, automatically following pagination."""
    return json.dumps(await (await get_client()).list_all_reports(), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_all_data_models() -> str:
    """List ALL data models in the organization, automatically following pagination."""
    return json.dumps(await (await get_client()).list_all_data_models(), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_formula_pitfalls() -> str:
    """Return a curated reference of common Sigma formula pitfalls — column reference syntax, type requirements, NULL handling, aggregate vs row-level context, date function argument order, and metrics vs calculated columns. Use this before writing any Sigma formula expression."""
    import importlib.resources

    ref = importlib.resources.files("sigma_mcp").joinpath("reference/formulas.md")
    # Wrapped in JSON to preserve the server-wide contract that every tool
    # returns parseable JSON on both success and failure.
    return json.dumps({"format": "markdown", "content": ref.read_text(encoding="utf-8")}, indent=2)


@mcp.tool()
@sigma_tool
async def sigma_search_docs(query: str) -> str:
    """Search Sigma Computing documentation using AI-powered semantic search. Returns relevant doc passages with source URLs. Use this to answer questions about Sigma features, configuration, formulas, administration, embedding, and best practices.

    read_only_hint: True
    """
    import httpx

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "searchDocs", "arguments": {"query": query}},
    }
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            "https://help.sigmacomputing.com/_mcp/server",
            json=payload,
            headers={"Accept": "application/json, text/event-stream"},
        )
    if resp.status_code != 200:
        return json.dumps({"error": {"type": "docs_search_failed", "status": resp.status_code}})
    # Fern MCP returns SSE; parse the data line
    text = resp.text
    for line in text.splitlines():
        if line.startswith("data: "):
            data = json.loads(line[6:])
            result = data.get("result", {})
            content_list = result.get("content", [])
            if content_list:
                return json.dumps({"format": "markdown", "content": content_list[0].get("text", "")}, indent=2)
    return json.dumps({"error": {"type": "docs_search_empty", "message": "No results found"}})


@mcp.tool()
@sigma_tool
async def sigma_get_doc_page(page_slug: str) -> str:
    """Fetch a specific Sigma documentation page as clean Markdown. Pass the page slug (e.g. 'create-a-workbook') or a section path (e.g. 'docs/create-a-workbook'). Returns the full page content.

    read_only_hint: True
    """
    import httpx

    slug = page_slug.strip("/")
    if slug.startswith("https://help.sigmacomputing.com/"):
        slug = slug.replace("https://help.sigmacomputing.com/", "")
    if not slug.startswith("docs/"):
        slug = f"docs/{slug}"
    if slug.endswith(".md"):
        slug = slug[:-3]
    url = f"https://help.sigmacomputing.com/{slug}.md"
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
        resp = await http.get(url)
    if resp.status_code != 200:
        return json.dumps({"error": {"type": "page_not_found", "slug": page_slug, "status": resp.status_code}})
    return json.dumps({"format": "markdown", "content": resp.text}, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# MCP NATIVE RESOURCES & PROMPTS
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.resource(
    "sigma://reference/formulas",
    name="Sigma Formula Reference",
    description="Curated reference guide for writing valid Sigma formulas.",
    mime_type="text/markdown",
)
def resource_sigma_formula_reference() -> str:
    """Return the formula reference guide."""
    import importlib.resources

    ref = importlib.resources.files("sigma_mcp").joinpath("reference/formulas.md")
    return ref.read_text(encoding="utf-8")


@mcp.resource(
    "sigma://reference/capabilities",
    name="Sigma API Capabilities",
    description="Overview of supported and unsupported Sigma API operations.",
    mime_type="application/json",
)
def resource_sigma_capabilities() -> str:
    """Return API capabilities JSON."""
    return json.dumps(
        {
            "supported_domains": [
                "connections",
                "workbooks",
                "data_models",
                "members",
                "teams",
                "user_attributes",
                "tags",
                "deployments",
                "templates",
                "materializations",
                "exports",
                "webhooks",
            ],
            "unsupported_domains": [
                "direct_element_creation",
                "direct_page_layout",
                "saml_cert_management_beta",
            ],
            "workflow_recommendation": (
                "Use template-then-stamp pattern (sigma_deploy_template_to_folder & sigma_swap_workbook_sources) for"
                " automated workbook creation."
            ),
        },
        indent=2,
    )


@mcp.resource(
    "sigma://reference/docs-index",
    name="Sigma Documentation Index",
    description="Full index of all Sigma documentation pages with URLs. Use to discover available doc pages.",
    mime_type="text/plain",
)
def resource_sigma_docs_index() -> str:
    """Return the bundled Sigma docs index (llms.txt)."""
    import importlib.resources

    ref = importlib.resources.files("sigma_mcp").joinpath("reference/sigma_api_index.txt")
    return ref.read_text(encoding="utf-8")


@mcp.resource("sigma://webhooks/recent")
def resource_webhooks_recent() -> str:
    """Return recent webhook events received by the server."""
    return json.dumps(get_recent_webhooks(limit=20), indent=2)


@mcp.tool()
@sigma_tool
async def sigma_list_recent_webhooks(limit: int = 20, event_type: str | None = None) -> str:
    """List recently recorded incoming Sigma webhook events.

    read_only_hint: True
    """
    events = get_recent_webhooks(limit=limit, event_type=event_type)
    return json.dumps({"count": len(events), "events": events}, indent=2)


@mcp.prompt(
    "provision_tenant_dashboard",
    description="Guide the agent through deploying a Sigma template into a folder and swapping data sources for a target tenant.",
)
def prompt_provision_tenant_dashboard(
    template_id: str, folder_id: str, tenant_id: str, dashboard_name: str = "Tenant Dashboard"
) -> str:
    return (
        f"You are provisioning a new dashboard for tenant '{tenant_id}':\n"
        f"1. Call `sigma_deploy_template_to_folder` with template_id='{template_id}', folder_id='{folder_id}', and name='{dashboard_name}'.\n"
        f"2. Inspect the created workbook sources using `sigma_list_workbook_sources`.\n"
        f"3. Swap the workbook sources for tenant '{tenant_id}' using `sigma_swap_workbook_sources`.\n"
        f"4. Verify the new workbook status and report success."
    )


@mcp.prompt(
    "audit_organization_permissions",
    description="Guide the agent through auditing organization members, team memberships, and assigned user attributes.",
)
def prompt_audit_organization_permissions(team_name: str = "") -> str:
    team_filter = f" for team '{team_name}'" if team_name else ""
    return (
        f"Perform an organization security & permission audit{team_filter}:\n"
        "1. Retrieve organization members using `sigma_list_members`.\n"
        "2. Retrieve teams using `sigma_list_teams` and examine memberships.\n"
        "3. Retrieve assigned user attributes using `sigma_list_user_attributes`.\n"
        "4. Highlight any inactive accounts, orphaned team assignments, or unexpected attribute overrides."
    )


@mcp.prompt(
    "prepare_data_model",
    description="Guide the agent in defining a production data model specification, columns, and relations.",
)
def prompt_prepare_data_model(connection_id: str, model_name: str) -> str:
    return (
        f"You are creating a new data model '{model_name}' on connection '{connection_id}':\n"
        f"1. Verify connection validity using `sigma_get_connection(connection_id='{connection_id}')`.\n"
        "2. Draft the data model spec JSON containing SQL query or source table, columns, types, and join relationships.\n"
        "3. Create the data model using `sigma_create_data_model`.\n"
        "4. Retrieve and confirm the registered spec using `sigma_get_data_model_spec`."
    )


@mcp.prompt(
    "onboard_team_member",
    description="Guide the agent through creating a new member, assigning team memberships, and verifying home folder setup.",
)
def prompt_onboard_team_member(email: str, first_name: str, last_name: str, team_name: str = "") -> str:
    steps = [
        f"1. Onboard member using `sigma_onboard_member(email='{email}', first_name='{first_name}', last_name='{last_name}')`."
    ]
    if team_name and team_name.strip():
        steps.append(f"2. Assign to team '{team_name}' using `sigma_bulk_assign_team_members`.")
    step_num = len(steps) + 1
    steps.append(f"{step_num}. Confirm homeFolderId is created using `sigma_get_member`.")
    return f"You are onboarding a new user '{first_name} {last_name}' ({email}):\n" + "\n".join(steps)


@mcp.prompt(
    "swap_warehouse_source",
    description="Guide the agent through re-binding workbook or template data sources to a new connection or table.",
)
def prompt_swap_warehouse_source(workbook_id: str, target_connection_id: str) -> str:
    return (
        f"Re-binding data sources for workbook '{workbook_id}' to connection '{target_connection_id}':\n"
        f"1. Retrieve existing sources using `sigma_list_workbook_sources(workbook_id='{workbook_id}')`.\n"
        f"2. Inspect target connection paths using `sigma_get_connection(connection_id='{target_connection_id}')`.\n"
        f"3. Rebind sources using `sigma_swap_workbook_sources`."
    )


@mcp.prompt(
    "audit_tenant_connections",
    description="Guide the agent through reviewing multi-tenant connections and running dry-run syncs.",
)
def prompt_audit_tenant_connections() -> str:
    return (
        "Multi-tenant connection audit:\n"
        "1. List all active tenant orgs using `sigma_bulk_sync_tenant_connections(dry_run=True)`.\n"
        "2. Review tenant databases and schema synchronization status.\n"
        "3. Execute synchronized schema updates if needed."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="mcp-server-sigma: Enterprise Model Context Protocol Server for Sigma Computing"
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=os.environ.get("PORT", "8000"),
        help="Port for network transports (default: 8000 or $PORT)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "127.0.0.1"),
        help="Host binding for network transports (default: 127.0.0.1)",
    )
    args = parser.parse_args()

    configure_logging()

    auth_token = os.environ.get("SIGMA_MCP_AUTH_TOKEN", "")
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8000)

    if auth_token and args.transport in ("sse", "streamable-http"):
        logger.info("Enforcing bearer token authentication on network transport")

    if args.transport == "streamable-http":
        mcp.run(transport="streamable-http", host=host, port=port)  # pragma: no cover
    elif args.transport == "sse":
        mcp.run(transport="sse", host=host, port=port)  # pragma: no cover
    else:
        mcp.run(transport="stdio")  # pragma: no cover


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE D1: TOOL ANNOTATIONS (applied post-registration for cleanliness)
# ═══════════════════════════════════════════════════════════════════════════════

from mcp.types import ToolAnnotations  # noqa: E402

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
_WRITE_SAFE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
_DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)
_IDEMPOTENT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

_ANNOTATION_MAP: dict[str, ToolAnnotations] = {}

# Read-only tools (list/get operations)
_RO_PREFIXES = ("sigma_list_", "sigma_get_", "sigma_api_capabilities")
_RO_NAMES = {
    "sigma_get_current_user",
    "sigma_list_workbooks_shared_with_member",
    "sigma_list_all_input_tables",
    "sigma_list_tenants_paginated",
    "sigma_get_tenant_scoped_info",
    "sigma_formula_pitfalls",
    "sigma_search_docs",
    "sigma_get_doc_page",
}

# Destructive tools
_DESTRUCTIVE_NAMES = {
    "sigma_delete_file",
    "sigma_delete_tag",
    "sigma_delete_team",
    "sigma_delete_workspace",
    "sigma_delete_workbook_schedule",
    "sigma_archive_deployment",
    "sigma_deactivate_member",
    "sigma_bulk_deactivate_members",
    "sigma_remove_workbook_tag",
    "sigma_delete_workspace_grant",
    "sigma_delete_connection_path_grant",
    "sigma_delete_user_attribute_for_user",
    "sigma_delete_user_attribute_for_team",
    "sigma_delete_user_attribute_for_tenant",
    "sigma_update_user_attribute_for_users",
    "sigma_update_user_attribute_for_teams",
    "sigma_update_user_attribute_for_tenants",
}

# Idempotent tools (tags, grants)
_IDEMPOTENT_NAMES = {
    "sigma_tag_workbook",
    "sigma_tag_data_model",
    "sigma_grant_workbook_access",
    "sigma_grant_workspace_access",
    "sigma_add_connection_grant",
    "sigma_create_grant",
    "sigma_set_user_attribute_for_teams",
    "sigma_set_user_attribute_for_tenants",
}

# NOTE: Accessing mcp._tool_manager._tools is a private API of the MCP SDK.
# There is no public API to set annotations post-registration as of mcp 1.2.x.
# Pin the SDK version and re-verify on upgrades.
for tool_name, tool_obj in mcp._tool_manager._tools.items():
    if tool_name in _DESTRUCTIVE_NAMES:
        tool_obj.annotations = _DESTRUCTIVE
    elif tool_name in _IDEMPOTENT_NAMES:
        tool_obj.annotations = _IDEMPOTENT
    elif tool_name in _RO_NAMES or any(tool_name.startswith(p) for p in _RO_PREFIXES):
        tool_obj.annotations = _READ_ONLY
    else:
        tool_obj.annotations = _WRITE_SAFE


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE D4: TOOL PROFILES (filter which tools register based on env var)
# ═══════════════════════════════════════════════════════════════════════════════

_CORE_TOOLS = {
    "sigma_list_connections",
    "sigma_get_connection",
    "sigma_sync_connection",
    "sigma_list_workbooks",
    "sigma_get_workbook",
    "sigma_create_workbook",
    "sigma_duplicate_workbook",
    "sigma_delete_file",
    "sigma_list_workbook_pages",
    "sigma_list_workbook_elements",
    "sigma_list_workbook_columns",
    "sigma_list_workbook_queries",
    "sigma_list_workbook_sources",
    "sigma_swap_workbook_sources",
    "sigma_list_data_models",
    "sigma_get_data_model",
    "sigma_get_data_model_spec",
    "sigma_create_data_model",
    "sigma_update_data_model",
    "sigma_list_members",
    "sigma_get_member",
    "sigma_get_current_user",
    "sigma_list_teams",
    "sigma_get_team",
    "sigma_list_files",
    "sigma_create_folder",
    "sigma_list_tags",
    "sigma_create_tag",
    "sigma_list_templates",
    "sigma_create_workbook_from_template",
    "sigma_deploy_template_to_folder",
    "sigma_materialize_and_wait",
    "sigma_promote_workbook",
    "sigma_api_capabilities",
    "sigma_export_and_download",
    "sigma_formula_pitfalls",
    "sigma_search_docs",
    "sigma_get_doc_page",
}

_ADMIN_TOOLS = _CORE_TOOLS | {
    "sigma_create_member",
    "sigma_update_member",
    "sigma_deactivate_member",
    "sigma_onboard_member",
    "sigma_bulk_assign_team_members",
    "sigma_bulk_deactivate_members",
    "sigma_change_member_email",
    "sigma_bulk_remove_team_members",
    "sigma_reassign_workbook_ownership",
    "sigma_create_team",
    "sigma_delete_team",
    "sigma_update_team_members",
    "sigma_list_user_attributes",
    "sigma_create_user_attribute",
    "sigma_get_user_attribute_users",
    "sigma_get_user_attribute_teams",
    "sigma_get_user_attribute_tenants",
    "sigma_list_account_types",
}

_EMBED_TOOLS = _CORE_TOOLS | {
    "sigma_list_workbook_embeds",
    "sigma_create_workbook_embed",
    "sigma_list_user_attributes",
    "sigma_create_user_attribute",
    "sigma_set_user_attribute_for_teams",
    "sigma_get_user_attribute_users",
    "sigma_get_user_attribute_teams",
    "sigma_get_user_attribute_tenants",
    "sigma_list_tenants",
    "sigma_get_tenant",
    "sigma_list_tenants_paginated",
    "sigma_get_tenant_scoped_info",
    "sigma_bulk_sync_tenant_connections",
    "sigma_swap_data_model_sources",
    "sigma_swap_template_sources",
    "sigma_list_workbook_grants",
    "sigma_grant_workbook_access",
    "sigma_list_workspaces",
    "sigma_grant_workspace_access",
}

_PROFILES = {
    "core": _CORE_TOOLS,
    "admin": _ADMIN_TOOLS,
    "embed": _EMBED_TOOLS,
}

_profile = os.environ.get("SIGMA_MCP_PROFILE", "full").lower()
if _profile != "full":
    if _profile not in _PROFILES:
        raise ValueError(f"Unknown SIGMA_MCP_PROFILE {_profile!r}. Valid: core, admin, embed, full.")
    _allowed = _PROFILES[_profile]
    _to_remove = [name for name in mcp._tool_manager._tools if name not in _allowed]
    for name in _to_remove:
        mcp._tool_manager.remove_tool(name)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE D5: READONLY FILTER (SIGMA_MCP_READONLY=1 removes non-read-only tools)
# ═══════════════════════════════════════════════════════════════════════════════

if os.environ.get("SIGMA_MCP_READONLY", "").strip() == "1":
    _ro_remove = [
        name
        for name, tool_obj in mcp._tool_manager._tools.items()
        if not (tool_obj.annotations and tool_obj.annotations.read_only_hint)
    ]
    for name in _ro_remove:
        mcp._tool_manager.remove_tool(name)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE D6: BULK-DESTRUCTIVE GATING (SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1)
# ═══════════════════════════════════════════════════════════════════════════════

_BULK_DESTRUCTIVE_TOOLS = {"sigma_bulk_deactivate_members", "sigma_bulk_remove_team_members"}

if os.environ.get("SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE", "").strip() != "1":
    for name in _BULK_DESTRUCTIVE_TOOLS:
        if name in mcp._tool_manager._tools:
            mcp._tool_manager.remove_tool(name)


if __name__ == "__main__":  # pragma: no cover
    main()
