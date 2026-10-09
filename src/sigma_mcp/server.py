"""MCP server for Sigma Computing — modular architecture with FastMCP 4 Server Composition.

Exposes every Sigma REST API operation as an MCP tool, organized by domain sub-servers:
  - workbooks: Workbooks, templates, reports, embeds, exports, bookmarks, and schedules.
  - datasets: Connections, data models, warehouse tables, and schema synchronization.
  - elements: Workbook pages, elements, queries, columns, controls, materializations, and documentation.
  - workspace: Workspaces, files, folders, and tags.
  - admin: Members, teams, account types, tenants, user attributes, deployments, and connectors.

Environment variables controlling the gateway:
  SIGMA_MCP_PROFILE          - Profile (default: full): full, readonly, or a job allowlist profile
                               analyst, author, modeler, embed, access_admin (see profiles.PROFILES).
  SIGMA_MCP_READONLY=1       - Keep only tools annotated readOnlyHint=True and refuse every other call.
                               Composes with SIGMA_MCP_PROFILE.
  SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1 - Required to execute admin_bulk_deactivate_members and
                               admin_bulk_remove_team_members (listed in full, refused without it).
  SIGMA_MCP_ENABLE_TOOL_SEARCH=1 - Opt-in Tool Search (profile=full only).
  SIGMA_MCP_TOOL_SEARCH_BACKEND  - Tool Search backend: regex (default) or bm25.
  SIGMA_MCP_ENABLE_CODE_MODE=1   - Opt-in experimental Code Mode (profile=full only; not with Tool Search).
  SIGMA_MCP_STATELESS_HTTP=1 - Stateless Streamable HTTP mode (Spec 2026-07-28 / SEP-1049).

Build order: domain mounts -> job allowlist -> read-only filter -> discovery (full only).
"""

from __future__ import annotations

import argparse
import asyncio as asyncio
import importlib.resources
import importlib.util
import json
import logging
import os
import signal
import sys
import time as time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Literal, cast

from fastmcp import FastMCP
from fastmcp.server.transforms.search import BM25SearchTransform, RegexSearchTransform
from fastmcp.tools import FunctionTool, Tool
from mcp.server.caching import CacheHint

from sigma_mcp import __version__
from sigma_mcp.auth import AUTH_TOKEN_ENV, SharedTokenVerifier
from sigma_mcp.client import SigmaClient
from sigma_mcp.config import readonly_enabled, settings
from sigma_mcp.errors import redact_secrets
from sigma_mcp.middleware import (
    ParentAuditMiddleware,
    ReadOnlyGateMiddleware,
)
from sigma_mcp.profiles import (
    BULK_DESTRUCTIVE_TOOLS,  # noqa: F401 - re-exported for callers of sigma_mcp.server
    FULL_ONLY_TOOLS,  # noqa: F401 - re-exported for callers of sigma_mcp.server
    PROFILES,
    ReadOnlyAnnotations,
    ReadOnlyToolFilter,
    get_profile,
    validate_allowlist,
)
from sigma_mcp.tools import (
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


class _UriCompat(str):
    """String subclass that compares equal to AnyUrl and str."""

    def __eq__(self, other: Any) -> bool:
        return str(self) == str(other)

    def __hash__(self) -> int:
        return hash(str(self))


ToolSearchBackend = Literal["regex", "bm25"]

# Domain sub-servers by mount namespace (hybrid A+B: the namespace is the domain, the wire prefix).
# create_server only mounts them; every profile, read-only and discovery transform is applied on
# the per-call root, so no profile state is written to these shared sub-servers.
DOMAIN_SERVERS: tuple[tuple[str, FastMCP], ...] = (
    ("workbooks", workbooks_server),
    ("datasets", datasets_server),
    ("elements", elements_server),
    ("workspace", workspace_server),
    ("admin", admin_server),
)

# FastMCP synthetic discovery tools that only read the catalog (annotated readOnlyHint=True).
TOOL_SEARCH_READ_ONLY_TOOLS = ("search_tools",)
CODE_MODE_READ_ONLY_TOOLS = ("search", "get_schema")


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
                "Use template-then-stamp pattern (workbooks_deploy_template_to_folder &"
                " elements_swap_workbook_sources) for automated workbook creation."
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
        f"1. Call `workbooks_deploy_template_to_folder` with template_id='{template_id}', folder_id='{folder_id}', and name='{dashboard_name}'.\n"
        f"2. Inspect the created workbook sources using `elements_list_workbook_sources`.\n"
        f"3. Swap the workbook sources for tenant '{tenant_id}' using `elements_swap_workbook_sources`.\n"
        f"4. Verify the new workbook status and report success."
    )


def prompt_audit_organization_permissions(team_name: str = "") -> str:
    team_filter = f" for team '{team_name}'" if team_name else ""
    return (
        f"Perform an organization security & permission audit{team_filter}:\n"
        "1. Retrieve organization members using `admin_list_members`.\n"
        "2. Retrieve teams using `admin_list_teams` and examine memberships.\n"
        "3. Retrieve assigned user attributes using `admin_list_user_attributes`.\n"
        "4. Highlight any inactive accounts, orphaned team assignments, or unexpected attribute overrides."
    )


def prompt_prepare_data_model(connection_id: str, model_name: str) -> str:
    return (
        f"You are creating a new data model '{model_name}' on connection '{connection_id}':\n"
        f"1. Verify connection validity using `datasets_get_connection(connection_id='{connection_id}')`.\n"
        "2. Draft the data model spec JSON containing SQL query or source table, columns, types, and join relationships.\n"
        "3. Create the data model using `datasets_create_data_model`.\n"
        "4. Retrieve and confirm the registered spec using `datasets_get_data_model_spec`."
    )


def prompt_onboard_team_member(email: str, first_name: str, last_name: str, team_name: str = "") -> str:
    steps = [
        f"1. Onboard member using `admin_onboard_member(email='{email}', first_name='{first_name}', last_name='{last_name}')`."
    ]
    if team_name and team_name.strip():
        steps.append(f"2. Assign to team '{team_name}' using `admin_bulk_assign_team_members`.")
    step_num = len(steps) + 1
    steps.append(f"{step_num}. Confirm homeFolderId is created using `admin_get_member`.")
    return f"You are onboarding a new user '{first_name} {last_name}' ({email}):\n" + "\n".join(steps)


def prompt_swap_warehouse_source(workbook_id: str, target_connection_id: str) -> str:
    return (
        f"Re-binding data sources for workbook '{workbook_id}' to connection '{target_connection_id}':\n"
        f"1. Retrieve existing sources using `elements_list_workbook_sources(workbook_id='{workbook_id}')`.\n"
        f"2. Inspect target connection paths using `datasets_get_connection(connection_id='{target_connection_id}')`.\n"
        f"3. Rebind sources using `elements_swap_workbook_sources`."
    )


def prompt_audit_tenant_connections() -> str:
    return (
        "Multi-tenant connection audit:\n"
        "1. List all active tenant orgs using `datasets_bulk_sync_tenant_connections(dry_run=True)`.\n"
        "2. Review tenant databases and schema synchronization status.\n"
        "3. Execute synchronized schema updates if needed."
    )


def _catalog_tool_names(root: FastMCP) -> set[str]:
    """Return the client-visible tool names of ``root`` via the public ``list_tools()``.

    Runs on a worker thread with its own event loop so ``create_server`` stays synchronous
    and safe to call from inside a running loop (tests, hosts).
    """

    async def _collect() -> set[str]:
        return {tool.name for tool in await root.list_tools()}

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _collect()).result()


def _apply_tool_allowlist(root: FastMCP, allowlist: frozenset[str]) -> None:
    """Expose only ``allowlist`` tools; prompts, resources, and templates are untouched.

    Public visibility API: disable every tool, then re-enable the named tools. The later
    ``enable`` wins. ``enable(only=True)`` is not used because it disables every component
    type first, which would also hide prompts and resources.
    """
    root.disable(components={"tool"})
    root.enable(names=set(allowlist), components={"tool"})


def _attach_tool_search(root: FastMCP, backend: ToolSearchBackend) -> None:
    """Attach Regex (default) or BM25 Tool Search transform to the root gateway."""
    if backend == "bm25":
        root.add_transform(BM25SearchTransform())
    else:
        root.add_transform(RegexSearchTransform())
    root.add_transform(ReadOnlyAnnotations(TOOL_SEARCH_READ_ONLY_TOOLS))


def _code_mode_sandbox_available() -> bool:
    """True when ``pydantic_monty`` (shipped by ``fastmcp[code-mode]``) is importable.

    Code Mode imports without it, but every ``execute`` call then fails, so attach is skipped.
    """
    return importlib.util.find_spec("pydantic_monty") is not None


def _attach_code_mode(root: FastMCP) -> bool:
    """Attach experimental Code Mode when its ``pydantic_monty`` sandbox is installed.

    Returns True when the transform was attached; False when a missing ``pydantic_monty``
    skipped it (install ``fastmcp[code-mode]``).
    """
    if not _code_mode_sandbox_available():
        logger.warning(
            "Code Mode requested but pydantic-monty (the Code Mode sandbox) is not installed; "
            "skipping attach. Install fastmcp[code-mode] or omit --enable-code-mode."
        )
        return False
    from fastmcp.experimental.transforms.code_mode import CodeMode

    root.add_transform(CodeMode())
    root.add_transform(ReadOnlyAnnotations(CODE_MODE_READ_ONLY_TOOLS))
    return True


def _env_bool(name: str, configured: bool) -> bool:
    """Return ``configured`` or the live ``name=1``/``name=true`` environment flag."""
    return configured or os.environ.get(name, "").strip().lower() in ("1", "true")


def _configured_search_backend() -> ToolSearchBackend:
    """Return the Tool Search backend from the live env or settings; reject unknown values."""
    raw = (os.environ.get("SIGMA_MCP_TOOL_SEARCH_BACKEND") or settings.MCP_TOOL_SEARCH_BACKEND).strip().lower()
    if raw not in ("regex", "bm25"):
        raise ValueError(f"Unknown SIGMA_MCP_TOOL_SEARCH_BACKEND {raw!r}; valid: bm25, regex.")
    return cast(ToolSearchBackend, raw)


def create_server(
    profile: str | None = None,
    enable_tool_search: bool | None = None,
    enable_code_mode: bool | None = None,
    tool_search_backend: ToolSearchBackend | None = None,
) -> FastMCP:
    """Build root gateway FastMCP instance using Server Composition and Hierarchical Middleware.

    Architecture:
    Gateway (FastMCP)
    ├── Parent Middleware (ParentAuditMiddleware, ReadOnlyGateMiddleware)
    ├── mount(workbooks_server, namespace="workbooks")   # domain mount, not a product stamp
    ├── mount(datasets_server, namespace="datasets")
    ├── mount(elements_server, namespace="elements")
    ├── mount(workspace_server, namespace="workspace")
    ├── mount(admin_server, namespace="admin")
    ├── (job profiles) tools-only visibility allowlist over the full catalog
    ├── (readonly) ReadOnlyToolFilter: keep tools annotated readOnlyHint=True
    └── (Optional, profile==full only) Tool Search XOR experimental Code Mode

    Profile rules:
    * Every profile mounts all five domains. Job profiles expose only their allowlisted
      tool names (prompts/resources stay).
    * An unknown profile, or an allowlisted name missing from the full catalog, raises
      ``ValueError`` at build time.
    * Bulk destructive tools stay listed in ``full``; the admin domain guard refuses them at
      call time unless ``SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1``.

    Discovery rules:
    * Default: flat ``tools/list`` of the profile's tools.
    * Tool Search / Code Mode attach only when explicitly enabled **and** ``profile == "full"``.
    * Requesting either discovery mode on another profile logs a warning and skips attach.
    * Enabling both Tool Search and Code Mode raises ``ValueError`` (mutual exclusion).
    """
    active = get_profile(profile or os.environ.get("SIGMA_MCP_PROFILE") or settings.MCP_PROFILE or "full")
    active_profile = active.name
    use_tool_search = (
        enable_tool_search
        if enable_tool_search is not None
        else _env_bool("SIGMA_MCP_ENABLE_TOOL_SEARCH", settings.MCP_ENABLE_TOOL_SEARCH)
    )
    use_code_mode = (
        enable_code_mode
        if enable_code_mode is not None
        else _env_bool("SIGMA_MCP_ENABLE_CODE_MODE", settings.MCP_ENABLE_CODE_MODE)
    )
    search_backend: ToolSearchBackend = (
        tool_search_backend if tool_search_backend is not None else _configured_search_backend()
    )

    if use_tool_search and use_code_mode:
        raise ValueError("Tool Search and Code Mode are mutually exclusive; enable only one discovery mode.")

    root = FastMCP(
        "mcp-server-sigma",
        version=__version__,
        lifespan=server_lifespan,
        cache_ttl=3600,
        cache_scope="public",
    )

    # Attach compatibility bridges
    root.streamable_http_app = _streamable_http_app.__get__(root, FastMCP)  # type: ignore[attr-defined]

    # 1. Global Parent Middleware (audit logging, timing, and the read-only gate)
    readonly_gate = ReadOnlyGateMiddleware(enforce=active.readonly)
    root.add_middleware(ParentAuditMiddleware())
    root.add_middleware(readonly_gate)

    # 2. Domain namespaces are the wire prefix (workbooks_list_workbooks, not sigma_list_workbooks).
    for domain, sub in DOMAIN_SERVERS:
        root.mount(sub, namespace=domain)

    # Resources stay on the root. A domain URI scheme is already the prefix; mounting
    # them on a namespaced server would insert a second path segment.
    root.resource(
        "elements://reference/formulas",
        name="Formula Reference",
        description="Curated reference guide for writing valid Sigma formulas.",
        mime_type="text/markdown",
    )(resource_sigma_formula_reference)

    root.resource(
        "admin://reference/capabilities",
        name="API Capabilities",
        description="Overview of supported and unsupported Sigma API operations.",
        mime_type="application/json",
    )(resource_sigma_capabilities)

    root.resource(
        "elements://reference/docs-index",
        name="Documentation Index",
        description="Full index of all Sigma documentation pages with URLs. Use to discover available doc pages.",
        mime_type="text/plain",
    )(resource_sigma_docs_index)

    root.resource("admin://webhooks/recent")(resource_webhooks_recent)

    # 3. Job profiles: validate against the full mounted catalog, then filter tools
    if active.is_allowlist:
        allowlist = validate_allowlist(active, _catalog_tool_names(root))
        _apply_tool_allowlist(root, allowlist)

    # 4. Read-only: keep only tools annotated readOnlyHint=True (annotation is the truth)
    if active.readonly or readonly_enabled():
        # Capture the profile catalog before the filter hides writes, so the gate refuses a
        # hidden real tool but lets an unknown name reach FastMCP's "Unknown tool" error.
        readonly_gate.catalog = frozenset(_catalog_tool_names(root))
        root.add_transform(ReadOnlyToolFilter())

    # 5. Opt-in discovery transforms — full profile only (never dump the catalog by default)
    if use_tool_search:
        if active_profile != "full":
            logger.warning(
                "Tool Search requested with profile=%r; attach is allowed only on profile='full'. "
                "Keeping flat curated tools/list.",
                active_profile,
            )
        else:
            _attach_tool_search(root, search_backend)

    if use_code_mode:
        if active_profile != "full":
            logger.warning(
                "Code Mode requested with profile=%r; attach is allowed only on profile='full'. "
                "Keeping flat curated tools/list.",
                active_profile,
            )
        else:
            _attach_code_mode(root)

    # Compatibility wrappers on the root instance (public prompt/resource APIs only)
    _orig_get_prompt = root.get_prompt

    async def _get_prompt_compat(name: str, arguments: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        if isinstance(arguments, dict):
            return await root.render_prompt(name, arguments)
        return await _orig_get_prompt(name, **kwargs)

    root.get_prompt = _get_prompt_compat  # type: ignore[assignment]

    _orig_list_resources = root.list_resources

    async def _list_resources_compat(**kwargs: Any) -> Any:
        res_list = await _orig_list_resources(**kwargs)
        for r in res_list:
            if hasattr(r, "uri"):
                r.uri = _UriCompat(r.uri)  # type: ignore[assignment]
        return res_list

    root.list_resources = _list_resources_compat  # type: ignore[method-assign]

    return root


def _register_domain_prompts() -> None:
    """Register prompts on domain servers once so mounts expose ``{domain}_{prompt}``.

    Domain servers are module singletons. Reloading this module must not register
    the same prompt twice (FastMCP rejects duplicate component keys).
    """
    if getattr(workbooks_server, "_domain_prompts_registered", False):
        return
    workbooks_server.prompt(
        "provision_tenant_dashboard",
        description="Guide the agent through deploying a Sigma template into a folder and swapping data sources for a target tenant.",
    )(prompt_provision_tenant_dashboard)
    admin_server.prompt(
        "audit_organization_permissions",
        description="Guide the agent through auditing organization members, team memberships, and assigned user attributes.",
    )(prompt_audit_organization_permissions)
    datasets_server.prompt(
        "prepare_data_model",
        description="Guide the agent in defining a production data model specification, columns, and relations.",
    )(prompt_prepare_data_model)
    admin_server.prompt(
        "onboard_team_member",
        description="Guide the agent through creating a new member, assigning team memberships, and verifying home folder setup.",
    )(prompt_onboard_team_member)
    elements_server.prompt(
        "swap_warehouse_source",
        description="Guide the agent through re-binding workbook or template data sources to a new connection or table.",
    )(prompt_swap_warehouse_source)
    datasets_server.prompt(
        "audit_tenant_connections",
        description="Guide the agent through reviewing multi-tenant connections and running dry-run syncs.",
    )(prompt_audit_tenant_connections)
    workbooks_server._domain_prompts_registered = True  # type: ignore[attr-defined]


_register_domain_prompts()

# Default canonical server gateway instance
mcp = create_server()


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
        type=str.lower,
        choices=sorted(PROFILES),
        default=None,
        help=(
            "Server profile (default 'full'): full, readonly, or a job allowlist profile "
            "analyst/author/modeler/embed/access_admin."
        ),
    )
    parser.add_argument(
        "--enable-tool-search",
        action="store_true",
        default=settings.MCP_ENABLE_TOOL_SEARCH,
        help="Enable Tool Search on profile=full only (replaces tools/list with search_tools + call_tool).",
    )
    parser.add_argument(
        "--tool-search-backend",
        choices=["regex", "bm25"],
        default=None,
        help="Tool Search backend: 'regex' (default) or 'bm25'.",
    )
    parser.add_argument(
        "--enable-code-mode",
        action="store_true",
        default=settings.MCP_ENABLE_CODE_MODE,
        help=(
            "Enable experimental Code Mode on profile=full only (search + execute). "
            "Mutually exclusive with --enable-tool-search."
        ),
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
    enable_code = getattr(args, "enable_code_mode", settings.MCP_ENABLE_CODE_MODE)
    backend = getattr(args, "tool_search_backend", None)
    if (
        profile != default_profile
        or enable_search != settings.MCP_ENABLE_TOOL_SEARCH
        or enable_code != settings.MCP_ENABLE_CODE_MODE
        or backend is not None
    ):
        mcp = create_server(
            profile=profile,
            enable_tool_search=enable_search,
            enable_code_mode=enable_code,
            tool_search_backend=backend,
        )

    auth_token = os.environ.get(AUTH_TOKEN_ENV, "").strip()
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8000)
    stateless = getattr(args, "stateless", False)
    json_response = getattr(args, "json_response", False)

    if args.transport != "streamable-http":
        if stateless:
            logger.warning("--stateless flag is only applicable to 'streamable-http' transport.")
        if json_response:
            logger.warning("--json-response flag is only applicable to 'streamable-http' transport.")

    if args.transport in ("sse", "streamable-http"):
        if auth_token:
            mcp.auth = SharedTokenVerifier(auth_token)
            logger.info("Bearer token authentication is on for the %s transport", args.transport)
        else:
            logger.warning(
                "%s is not set, so MCP requests on the %s transport are not authenticated.",
                AUTH_TOKEN_ENV,
                args.transport,
            )

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
