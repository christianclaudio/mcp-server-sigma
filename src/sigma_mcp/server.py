"""MCP server for Sigma Computing — modular architecture with FastMCP 4 Server Composition.

Exposes every Sigma REST API operation as an MCP tool, organized by domain sub-servers:
  - workbooks: Workbooks, templates, reports, embeds, exports, bookmarks, and schedules.
  - datasets: Connections, data models, warehouse tables, and schema synchronization.
  - elements: Workbook pages, elements, queries, columns, controls, materializations, and documentation.
  - workspace: Workspaces, files, folders, and tags.
  - admin: Members, teams, account types, tenants, user attributes, deployments, and connectors.

Environment variables controlling tool registration:
  SIGMA_MCP_PROFILE          - Tool subset: core, admin, embed, full (default: full).
  SIGMA_MCP_READONLY=1       - When set, only tools annotated read_only_hint=True are
                               registered. Composes with SIGMA_MCP_PROFILE.
  SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1 - Required to register bulk-destructive operations.
  SIGMA_MCP_ENABLE_TOOL_SEARCH=1 - Opt-in dynamic regex tool discovery transform.
  SIGMA_MCP_STATELESS_HTTP=1 - Stateless Streamable HTTP mode (Spec 2026-07-28 / SEP-1049).
"""

from __future__ import annotations

import argparse
import asyncio as asyncio
import importlib.resources
import json
import logging
import os
import signal
import sys
import time as time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError
from fastmcp.server.transforms.search import RegexSearchTransform
from fastmcp.tools import FunctionTool, Tool
from mcp.server.caching import CacheHint
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from sigma_mcp import __version__
from sigma_mcp.client import SigmaClient
from sigma_mcp.config import settings
from sigma_mcp.errors import redact_secrets
from sigma_mcp.middleware import (
    ParentAuditMiddleware,
    ReadOnlyGateMiddleware,
)
from sigma_mcp.tools import (
    LEGACY_TOOL_ALIAS_MAP,
    admin_server,
    datasets_server,
    elements_server,
    workbooks_server,
    workspace_server,
)
from sigma_mcp.webhooks import get_recent_webhooks

logger = logging.getLogger("sigma_mcp")

CacheableMethod = Literal[
    "prompts/list",
    "resources/list",
    "resources/read",
    "resources/templates/list",
    "server/discover",
    "tools/list",
]

# MCP 2026-07-28 Deterministic Caching Hints (SEP-2549)
CACHE_HINTS: dict[CacheableMethod, CacheHint] = {
    "tools/list": CacheHint(ttl_ms=3600000, scope="private"),
    "prompts/list": CacheHint(ttl_ms=3600000, scope="public"),
    "resources/list": CacheHint(ttl_ms=3600000, scope="public"),
    "resources/templates/list": CacheHint(ttl_ms=3600000, scope="public"),
    "server/discover": CacheHint(ttl_ms=3600000, scope="public"),
}


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
    """Configure logging format based on settings.LOG_FORMAT or SIGMA_MCP_LOG_FORMAT."""
    log_format = (settings.LOG_FORMAT or os.environ.get("SIGMA_MCP_LOG_FORMAT", "")).lower()
    if log_format == "json":
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredJSONFormatter())
        logging.root.handlers = [handler]
        logging.root.setLevel(logging.INFO)


def _invalid_request(message: str) -> str:
    """Return a uniform nested error response for validation failures."""
    return json.dumps({"type": "invalid_request", "message": message})


def _summarize_list(data: Any, allowed_fields: list[str]) -> Any:
    """Helper to summarize high-cardinality list responses when summary_only=True."""
    if not isinstance(data, dict):
        return data
    result = dict(data)
    if "entries" in result and isinstance(result["entries"], list):
        summary_entries: list[Any] = []
        for item in result["entries"]:
            if isinstance(item, dict):
                summary_entries.append({k: item[k] for k in allowed_fields if k in item})
            else:
                summary_entries.append(item)
        result["entries"] = summary_entries
    return result


_client: SigmaClient | None = None
_HEADER_CLIENT_CACHE: dict[tuple[str, str], SigmaClient] = {}


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Manage server lifecycle and persistent client resources."""
    global _client
    logger.info("Starting up Sigma MCP server")
    try:
        yield {"client": _client}
    finally:
        logger.info("Shutting down Sigma MCP resources")
        if _client is not None:
            await _client.aclose()
            _client = None
        for c in _HEADER_CLIENT_CACHE.values():
            await c.aclose()
        _HEADER_CLIENT_CACHE.clear()


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
        req_base_url = headers.get("x-sigma-base-url") or os.environ.get("SIGMA_API_BASE_URL", settings.BASE_URL)

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
        base_url = os.environ.get("SIGMA_API_BASE_URL", settings.BASE_URL)
        if not client_id or not client_secret:
            raise ValueError("SIGMA_CLIENT_ID and SIGMA_CLIENT_SECRET must be set")
        _client = SigmaClient(client_id, client_secret, base_url)
    return _client


def _streamable_http_app(
    self: FastMCP,
    path: str | None = None,
    stateless_http: bool | None = None,
    json_response: bool | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    **kwargs: Any,
) -> Any:
    """Compatibility bridge for streamable HTTP ASGI application."""
    allowed_hosts = kwargs.pop("allowed_hosts", None)
    if allowed_hosts is None and host in ("0.0.0.0", "::"):
        raise ValueError("Explicit allowed_hosts required for wildcard host binding (0.0.0.0 or ::)")
    if allowed_hosts is None:
        allowed_hosts = [host, "localhost", f"{host}:{port}", f"localhost:{port}"]
    return self.http_app(
        path=path,
        transport="streamable-http",
        stateless_http=stateless_http,
        json_response=json_response,
        host_origin_protection=True,
        allowed_hosts=allowed_hosts,
        **kwargs,
    )


if not hasattr(FunctionTool, "input_schema"):
    FunctionTool.input_schema = property(lambda self: getattr(self, "parameters", {}))  # type: ignore[attr-defined]

if not hasattr(Tool, "input_schema"):
    Tool.input_schema = property(lambda self: getattr(self, "parameters", {}))  # type: ignore[attr-defined]


class _ToolManagerCompat:
    """Compatibility bridge for internal _tool_manager access."""

    def __init__(self, server: FastMCP) -> None:
        self._server = server

    @property
    def _tools(self) -> dict[str, Any]:
        tools: dict[str, Any] = {}
        disabled_names: set[str] = set()
        disabled_tags: set[str] = set()

        servers_to_check = [self._server]
        for p in self._server.providers:
            if hasattr(p, "server") and isinstance(p.server, FastMCP):
                servers_to_check.append(p.server)

        for s in servers_to_check:
            for t in s.transforms:
                if getattr(t, "_enabled", True) is False:
                    t_names = getattr(t, "names", None)
                    if isinstance(t_names, (set, list)):
                        disabled_names.update(t_names)
                    t_tags = getattr(t, "tags", None)
                    if isinstance(t_tags, (set, list)):
                        disabled_tags.update(t_tags)

        for s in servers_to_check:
            for k, c in s._local_provider._components.items():
                if not (k.startswith("tool:") or type(c).__name__.endswith("Tool")):
                    continue
                name = getattr(c, "name", None)
                if not name:
                    continue
                if name in disabled_names:
                    continue
                comp_tags = set(getattr(c, "tags", None) or [])
                if disabled_tags and (comp_tags & disabled_tags):
                    continue
                tools[name] = c
                if not name.startswith("sigma_"):
                    tools[f"sigma_{name}"] = c
        return tools

    def remove_tool(self, name: str) -> None:
        self._server.disable(names={name, f"sigma_{name}"})
        for p in self._server.providers:
            if hasattr(p, "disable"):
                p.disable(names={name, f"sigma_{name}"})


class _ResourceList(list[Any]):
    """Compatibility list wrapper for read_resource return type."""

    def __init__(self, result: Any) -> None:
        super().__init__(getattr(result, "contents", []))
        self._result = result

    @property
    def contents(self) -> list[Any]:
        return getattr(self._result, "contents", [])

    @property
    def meta(self) -> Any:
        return getattr(self._result, "meta", None)


class _UriCompat(str):
    """String subclass that compares equal to AnyUrl and str."""

    def __eq__(self, other: Any) -> bool:
        return str(self) == str(other)

    def __hash__(self) -> int:
        return hash(str(self))


# Profile tool definitions
_CORE_NAMES = {
    "list_connections",
    "get_connection",
    "sync_connection",
    "list_workbooks",
    "get_workbook",
    "create_workbook",
    "duplicate_workbook",
    "delete_file",
    "list_workbook_pages",
    "list_workbook_elements",
    "list_workbook_columns",
    "list_workbook_queries",
    "list_workbook_sources",
    "swap_workbook_sources",
    "list_data_models",
    "get_data_model",
    "get_data_model_spec",
    "create_data_model",
    "update_data_model",
    "list_members",
    "get_member",
    "get_current_user",
    "list_teams",
    "get_team",
    "list_files",
    "create_folder",
    "list_tags",
    "create_tag",
    "list_templates",
    "create_workbook_from_template",
    "deploy_template_to_folder",
    "materialize_and_wait",
    "promote_workbook",
    "api_capabilities",
    "export_and_download",
    "formula_pitfalls",
    "search_docs",
    "get_doc_page",
}

_ADMIN_NAMES = _CORE_NAMES | {
    "create_member",
    "update_member",
    "deactivate_member",
    "onboard_member",
    "bulk_assign_team_members",
    "bulk_deactivate_members",
    "change_member_email",
    "bulk_remove_team_members",
    "reassign_workbook_ownership",
    "create_team",
    "delete_team",
    "update_team_members",
    "list_user_attributes",
    "create_user_attribute",
    "get_user_attribute_users",
    "get_user_attribute_teams",
    "get_user_attribute_tenants",
    "list_account_types",
}

_EMBED_NAMES = _CORE_NAMES | {
    "list_workbook_embeds",
    "create_workbook_embed",
    "list_user_attributes",
    "create_user_attribute",
    "set_user_attribute_for_teams",
    "get_user_attribute_users",
    "get_user_attribute_teams",
    "get_user_attribute_tenants",
    "list_tenants",
    "get_tenant",
    "list_tenants_paginated",
    "get_tenant_scoped_info",
    "bulk_sync_tenant_connections",
    "swap_data_model_sources",
    "swap_template_sources",
    "list_workbook_grants",
    "grant_workbook_access",
    "list_workspaces",
    "grant_workspace_access",
}

_BULK_DESTRUCTIVE_TOOLS = {
    "bulk_deactivate_members",
    "admin_bulk_deactivate_members",
    "sigma_bulk_deactivate_members",
    "bulk_remove_team_members",
    "admin_bulk_remove_team_members",
    "sigma_bulk_remove_team_members",
}

_CORE_TOOLS = {f"sigma_{name}" for name in _CORE_NAMES}
_ADMIN_TOOLS = {f"sigma_{name}" for name in _ADMIN_NAMES}
_EMBED_TOOLS = {f"sigma_{name}" for name in _EMBED_NAMES}

_PROFILES = {
    "core": _CORE_TOOLS | _CORE_NAMES,
    "admin": _ADMIN_TOOLS | _ADMIN_NAMES,
    "embed": _EMBED_TOOLS | _EMBED_NAMES,
}

_profile_env = os.environ.get("SIGMA_MCP_PROFILE", settings.MCP_PROFILE).lower()
if _profile_env not in {"full", "core", "admin", "embed", "readonly"}:
    raise ValueError(f"Unknown SIGMA_MCP_PROFILE: '{_profile_env}'. Valid values: full, core, admin, embed, readonly")


def _redact_secrets(text: str, extra_secret: str | None = None) -> str:
    """Compatibility bridge for internal secret redaction."""
    return redact_secrets(text, extra_secret=extra_secret)


# Native MCP Resource Functions
def resource_sigma_formula_reference() -> str:
    ref = importlib.resources.files("sigma_mcp").joinpath("reference/formulas.md")
    return ref.read_text(encoding="utf-8")


def resource_sigma_capabilities() -> str:
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


def resource_sigma_docs_index() -> str:
    ref = importlib.resources.files("sigma_mcp").joinpath("reference/sigma_api_index.txt")
    return ref.read_text(encoding="utf-8")


def resource_webhooks_recent() -> str:
    return json.dumps(get_recent_webhooks(limit=20), indent=2)


# Native MCP Prompt Functions
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


def prompt_audit_organization_permissions(team_name: str = "") -> str:
    team_filter = f" for team '{team_name}'" if team_name else ""
    return (
        f"Perform an organization security & permission audit{team_filter}:\n"
        "1. Retrieve organization members using `sigma_list_members`.\n"
        "2. Retrieve teams using `sigma_list_teams` and examine memberships.\n"
        "3. Retrieve assigned user attributes using `sigma_list_user_attributes`.\n"
        "4. Highlight any inactive accounts, orphaned team assignments, or unexpected attribute overrides."
    )


def prompt_prepare_data_model(connection_id: str, model_name: str) -> str:
    return (
        f"You are creating a new data model '{model_name}' on connection '{connection_id}':\n"
        f"1. Verify connection validity using `sigma_get_connection(connection_id='{connection_id}')`.\n"
        "2. Draft the data model spec JSON containing SQL query or source table, columns, types, and join relationships.\n"
        "3. Create the data model using `sigma_create_data_model`.\n"
        "4. Retrieve and confirm the registered spec using `sigma_get_data_model_spec`."
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


def prompt_swap_warehouse_source(workbook_id: str, target_connection_id: str) -> str:
    return (
        f"Re-binding data sources for workbook '{workbook_id}' to connection '{target_connection_id}':\n"
        f"1. Retrieve existing sources using `sigma_list_workbook_sources(workbook_id='{workbook_id}')`.\n"
        f"2. Inspect target connection paths using `sigma_get_connection(connection_id='{target_connection_id}')`.\n"
        f"3. Rebind sources using `sigma_swap_workbook_sources`."
    )


def prompt_audit_tenant_connections() -> str:
    return (
        "Multi-tenant connection audit:\n"
        "1. List all active tenant orgs using `sigma_bulk_sync_tenant_connections(dry_run=True)`.\n"
        "2. Review tenant databases and schema synchronization status.\n"
        "3. Execute synchronized schema updates if needed."
    )


def create_server(
    profile: str | None = None,
    enable_tool_search: bool | None = None,
) -> FastMCP:
    """Build root gateway FastMCP instance using Server Composition and Hierarchical Middleware.

    Architecture:
    Gateway (FastMCP)
    ├── Parent Middleware (ParentAuditMiddleware, ReadOnlyGateMiddleware)
    ├── mount(workbooks_server)
    ├── mount(datasets_server)
    ├── mount(elements_server)
    ├── mount(workspace_server)
    ├── mount(admin_server)
    └── (Optional) RegexSearchTransform if enable_tool_search=True
    """
    env_profile = os.environ.get("SIGMA_MCP_PROFILE")
    active_profile = (profile or env_profile or settings.MCP_PROFILE or "full").lower()
    use_tool_search = (
        enable_tool_search
        if enable_tool_search is not None
        else (
            settings.MCP_ENABLE_TOOL_SEARCH
            or os.environ.get("SIGMA_MCP_ENABLE_TOOL_SEARCH", "").strip() in ("1", "true")
        )
    )

    if active_profile not in ("full", "core", "admin", "embed", "readonly"):
        raise ValueError(f"Unknown SIGMA_MCP_PROFILE {active_profile!r}. Valid: core, admin, embed, full, readonly.")

    # Reset any transforms on sub-servers
    for sub in (workbooks_server, datasets_server, elements_server, workspace_server, admin_server):
        sub._transforms.clear()

    root = FastMCP(
        "mcp-server-sigma",
        version=__version__,
        lifespan=server_lifespan,
        cache_ttl=3600,
        cache_scope="public",
    )

    # Attach compatibility bridges
    root.streamable_http_app = _streamable_http_app.__get__(root, FastMCP)  # type: ignore[attr-defined]

    # Global Parent Middleware
    root.add_middleware(ParentAuditMiddleware())
    root.add_middleware(ReadOnlyGateMiddleware())

    # Sub-server mounting (flat default for wire-format compatibility)
    root.mount(workbooks_server)
    root.mount(datasets_server)
    root.mount(elements_server)
    root.mount(workspace_server)
    root.mount(admin_server)

    # Native MCP Resources
    root.resource(
        "sigma://reference/formulas",
        name="Sigma Formula Reference",
        description="Curated reference guide for writing valid Sigma formulas.",
        mime_type="text/markdown",
    )(resource_sigma_formula_reference)

    root.resource(
        "sigma://reference/capabilities",
        name="Sigma API Capabilities",
        description="Overview of supported and unsupported Sigma API operations.",
        mime_type="application/json",
    )(resource_sigma_capabilities)

    root.resource(
        "sigma://reference/docs-index",
        name="Sigma Documentation Index",
        description="Full index of all Sigma documentation pages with URLs. Use to discover available doc pages.",
        mime_type="text/plain",
    )(resource_sigma_docs_index)

    root.resource("sigma://webhooks/recent")(resource_webhooks_recent)

    # Native MCP Prompts
    root.prompt(
        "provision_tenant_dashboard",
        description="Guide the agent through deploying a Sigma template into a folder and swapping data sources for a target tenant.",
    )(prompt_provision_tenant_dashboard)

    root.prompt(
        "audit_organization_permissions",
        description="Guide the agent through auditing organization members, team memberships, and assigned user attributes.",
    )(prompt_audit_organization_permissions)

    root.prompt(
        "prepare_data_model",
        description="Guide the agent in defining a production data model specification, columns, and relations.",
    )(prompt_prepare_data_model)

    root.prompt(
        "onboard_team_member",
        description="Guide the agent through creating a new member, assigning team memberships, and verifying home folder setup.",
    )(prompt_onboard_team_member)

    root.prompt(
        "swap_warehouse_source",
        description="Guide the agent through re-binding workbook or template data sources to a new connection or table.",
    )(prompt_swap_warehouse_source)

    root.prompt(
        "audit_tenant_connections",
        description="Guide the agent through reviewing multi-tenant connections and running dry-run syncs.",
    )(prompt_audit_tenant_connections)

    # Bulk-destructive gating:
    allow_bulk = (
        settings.MCP_ALLOW_BULK_DESTRUCTIVE or os.environ.get("SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE", "").strip() == "1"
    )
    if not allow_bulk:
        admin_server.disable(names=_BULK_DESTRUCTIVE_TOOLS)
    else:
        admin_server.enable(names=_BULK_DESTRUCTIVE_TOOLS)

    # Profile filtering
    if active_profile in _PROFILES:
        allowed_set = _PROFILES[active_profile]
        for sub in (workbooks_server, datasets_server, elements_server, workspace_server, admin_server):
            for c in list(sub._local_provider._components.values()):
                if hasattr(c, "name") and (getattr(c, "type", None) == "tool" or hasattr(c, "parameters")):
                    if c.name not in allowed_set:
                        sub.disable(names={c.name})
                    else:
                        sub.enable(names={c.name})

    # Read-only filtering
    is_ro = (
        settings.MCP_READONLY or os.environ.get("SIGMA_MCP_READONLY", "").strip() == "1" or active_profile == "readonly"
    )
    if is_ro:
        root.disable(tags={"mutation", "destructive", "idempotent"})
        for sub in (workbooks_server, datasets_server, elements_server, workspace_server, admin_server):
            sub.disable(tags={"mutation", "destructive", "idempotent"})

    # Optional ToolSearch transform
    if use_tool_search:
        root.add_transform(RegexSearchTransform())

    # Compatibility wrappers on root instance
    _orig_call_tool = root.call_tool

    async def _call_tool_compat(name: str, arguments: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        target_name = LEGACY_TOOL_ALIAS_MAP.get(name, name)
        try:
            orig = getattr(sys.modules.get("sigma_mcp.server"), "_orig_call_tool", _orig_call_tool)
            if orig == _call_tool_compat:
                orig = _orig_call_tool
            raw_res = await orig(target_name, arguments or {}, **kwargs)
        except NotFoundError as exc:
            raise ToolError(f"Unknown tool: '{name}'") from exc
        except Exception:
            raise
        if isinstance(raw_res, CallToolResult):
            return raw_res
        return CallToolResult(
            content=getattr(raw_res, "content", []),
            structured_content=getattr(raw_res, "structured_content", None),
            is_error=getattr(raw_res, "is_error", False),
            _meta=getattr(raw_res, "meta", None),
        )

    root.call_tool = _call_tool_compat  # type: ignore[method-assign]

    _orig_get_prompt = root.get_prompt

    async def _get_prompt_compat(name: str, arguments: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        if isinstance(arguments, dict):
            return await root.render_prompt(name, arguments)
        return await _orig_get_prompt(name, **kwargs)

    root.get_prompt = _get_prompt_compat  # type: ignore[assignment]

    _orig_read_resource = root.read_resource

    async def _read_resource_compat(uri: Any, **kwargs: Any) -> Any:
        raw_res = await _orig_read_resource(uri, **kwargs)
        return _ResourceList(raw_res)

    root.read_resource = _read_resource_compat  # type: ignore[method-assign]

    _orig_list_resources = root.list_resources

    async def _list_resources_compat(**kwargs: Any) -> Any:
        res_list = await _orig_list_resources(**kwargs)
        for r in res_list:
            if hasattr(r, "uri"):
                r.uri = _UriCompat(r.uri)  # type: ignore[assignment]
        return res_list

    root.list_resources = _list_resources_compat  # type: ignore[method-assign]
    root._tool_manager = _ToolManagerCompat(root)  # type: ignore[attr-defined]

    return root


# Default canonical server gateway instance
mcp = create_server()
_tool_mgr = mcp._tool_manager  # type: ignore[attr-defined]
_orig_call_tool = mcp.call_tool


# Re-export all flat tool functions for backward compatibility
from sigma_mcp.tools import *  # noqa: F401, F403, E402


def _handle_shutdown(signum: int, frame: Any) -> None:
    """Gracefully handle SIGTERM/SIGINT from host supervisor to exit with status 0 immediately."""
    sys.exit(0)


def main() -> None:
    """Run MCPServer with transport selection and graceful shutdown handling."""
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)
    parser = argparse.ArgumentParser(
        description="mcp-server-sigma: Enterprise Model Context Protocol Server for Sigma Computing"
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="Transport protocol: 'stdio' (default), 'streamable-http' (modern), or 'sse'.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8000")),
        help="Port for network transports (default: 8000 or $PORT)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "127.0.0.1"),
        help="Host binding for network transports (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--profile",
        choices=["full", "core", "admin", "embed", "readonly"],
        default=None,
        help="Server profile: 'full' (default), 'core', 'admin', 'embed', or 'readonly'.",
    )
    parser.add_argument(
        "--enable-tool-search",
        action="store_true",
        default=settings.MCP_ENABLE_TOOL_SEARCH,
        help="Enable dynamic ToolSearch transform replacing flat tools/list.",
    )
    parser.add_argument(
        "--stateless",
        action=argparse.BooleanOptionalAction,
        default=settings.MCP_STATELESS_HTTP
        or os.environ.get("SIGMA_MCP_STATELESS_HTTP", "").lower() in ("1", "true", "yes"),
        help="Run Streamable HTTP in stateless mode (fresh connection per request, no Mcp-Session-Id).",
    )
    parser.add_argument(
        "--json-response",
        action=argparse.BooleanOptionalAction,
        default=settings.MCP_JSON_RESPONSE
        or os.environ.get("SIGMA_MCP_JSON_RESPONSE", "").lower() in ("1", "true", "yes"),
        help="Return direct JSON responses instead of SSE text/event-stream over Streamable HTTP.",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        dest="allowed_hosts",
        default=None,
        help="Allowed host for HTTP transports (can be specified multiple times).",
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        dest="allowed_origins",
        default=None,
        help="Allowed origin for HTTP transports (can be specified multiple times).",
    )
    args = parser.parse_args()

    configure_logging()

    global mcp
    env_profile = os.environ.get("SIGMA_MCP_PROFILE")
    default_profile = env_profile if env_profile else settings.MCP_PROFILE
    raw_profile = getattr(args, "profile", None)
    profile = raw_profile if raw_profile is not None else default_profile
    enable_search = getattr(args, "enable_tool_search", settings.MCP_ENABLE_TOOL_SEARCH)
    if profile != default_profile or enable_search != settings.MCP_ENABLE_TOOL_SEARCH:
        mcp = create_server(profile=profile, enable_tool_search=enable_search)

    auth_token = os.environ.get("SIGMA_MCP_AUTH_TOKEN", "")
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8000)
    stateless = getattr(args, "stateless", False)
    json_response = getattr(args, "json_response", False)

    if args.transport != "streamable-http":
        if stateless:
            logger.warning("--stateless flag is only applicable to 'streamable-http' transport.")
        if json_response:
            logger.warning("--json-response flag is only applicable to 'streamable-http' transport.")

    if auth_token and args.transport in ("sse", "streamable-http"):
        logger.info("Enforcing bearer token authentication on network transport")

    hosts = getattr(args, "allowed_hosts", None)
    if hosts is None:
        if args.transport == "streamable-http" and host in ("0.0.0.0", "::"):
            parser.error("--allowed-host is required when binding to a wildcard host")
        hosts = [host, "localhost", f"{host}:{port}", f"localhost:{port}"]
    elif any(h.strip() == "*" for h in hosts):
        parser.error("Wildcard '*' is not permitted in --allowed-host; specify explicit hostnames.")
    run_kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "host_origin_protection": True,
        "allowed_hosts": hosts,
    }
    if getattr(args, "allowed_origins", None) is not None:
        run_kwargs["allowed_origins"] = args.allowed_origins

    if args.transport == "sse":
        logger.warning(
            "Deprecation Warning: HTTP+SSE transport is deprecated per MCP 2026-07-28 spec "
            "(SEP-2577). Please migrate to Streamable HTTP (--transport streamable-http)."
        )
        mcp.run(
            transport="sse",
            **run_kwargs,
        )  # pragma: no cover
    elif args.transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            stateless_http=stateless,
            json_response=json_response,
            **run_kwargs,
        )  # pragma: no cover
    else:
        mcp.run(transport="stdio")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    main()
