"""Tests for FastMCP 4 Server Composition: build order, discovery, middleware, and domain guards."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP
from fastmcp.server.middleware import MiddlewareContext
from pydantic import ValidationError

from sigma_mcp import server
from sigma_mcp.config import Settings, settings
from sigma_mcp.errors import SafetyViolationError
from sigma_mcp.middleware import AdminDomainGuardMiddleware, ParentAuditMiddleware, ReadOnlyGateMiddleware
from sigma_mcp.server import DOMAIN_SERVERS, create_server, main


@pytest.fixture(autouse=True)
def _clean_gate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start every test with read-only, bulk, and discovery switches off."""
    for var in (
        "SIGMA_MCP_PROFILE",
        "SIGMA_MCP_READONLY",
        "SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE",
        "SIGMA_MCP_ENABLE_TOOL_SEARCH",
        "SIGMA_MCP_ENABLE_CODE_MODE",
        "SIGMA_MCP_TOOL_SEARCH_BACKEND",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(settings, "MCP_READONLY", False)
    monkeypatch.setattr(settings, "MCP_ALLOW_BULK_DESTRUCTIVE", False)
    # main() rebinds the module-level server; restore it after every test.
    monkeypatch.setattr(server, "mcp", server.mcp)
    yield


@pytest.mark.asyncio
async def test_full_mounts_every_domain() -> None:
    """full mounts all five domain sub-servers under their namespace prefix."""
    tools = await create_server(profile="full").list_tools()
    assert len(tools) == 172
    assert {t.name.split("_", 1)[0] for t in tools} == {domain for domain, _ in DOMAIN_SERVERS}
    assert {"admin_bulk_deactivate_members", "admin_bulk_remove_team_members"} <= {t.name for t in tools}
    assert not [t.name for t in tools if t.name.startswith("sigma_")]


@pytest.mark.asyncio
async def test_create_server_does_not_mutate_domain_sub_servers() -> None:
    """Profiles and read-only act on the per-call root; shared sub-servers stay complete."""
    before = {domain: len(await sub.list_tools()) for domain, sub in DOMAIN_SERVERS}
    create_server(profile="analyst")
    create_server(profile="readonly")
    create_server(profile="full", enable_tool_search=True)
    after = {domain: len(await sub.list_tools()) for domain, sub in DOMAIN_SERVERS}
    assert after == before
    assert sum(after.values()) == 172
    assert len(await create_server(profile="full").list_tools()) == 172


@pytest.mark.asyncio
async def test_create_server_tool_search() -> None:
    """full + Tool Search replaces tools/list with the discovery meta-tools."""
    app = create_server(enable_tool_search=True)
    assert [t.name for t in await app.list_tools()] == ["search_tools", "call_tool"]
    res = await app.call_tool("search_tools", {"pattern": "workbooks_list"})
    assert not res.is_error
    assert "workbooks_list_workbooks" in str(res.content)


@pytest.mark.asyncio
async def test_bm25_tool_search_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """tool_search_backend='bm25' (argument or env) still collapses tools/list to meta-tools."""
    app = create_server(profile="full", enable_tool_search=True, tool_search_backend="bm25")
    assert [t.name for t in await app.list_tools()] == ["search_tools", "call_tool"]
    res = await app.call_tool("search_tools", {"query": "workbook"})
    assert not res.is_error
    assert "workbooks_" in str(res.content)

    monkeypatch.setenv("SIGMA_MCP_TOOL_SEARCH_BACKEND", "BM25")
    env_app = create_server(profile="full", enable_tool_search=True)
    assert [t.name for t in await env_app.list_tools()] == ["search_tools", "call_tool"]

    monkeypatch.setenv("SIGMA_MCP_TOOL_SEARCH_BACKEND", "fuzzy")
    with pytest.raises(ValueError, match="Unknown SIGMA_MCP_TOOL_SEARCH_BACKEND 'fuzzy'"):
        create_server(profile="full", enable_tool_search=True)


@pytest.mark.asyncio
async def test_discovery_flags_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """SIGMA_MCP_ENABLE_TOOL_SEARCH=1 / SIGMA_MCP_ENABLE_CODE_MODE=1 drive discovery."""
    monkeypatch.setenv("SIGMA_MCP_ENABLE_TOOL_SEARCH", "1")
    assert [t.name for t in await create_server(profile="full").list_tools()] == ["search_tools", "call_tool"]
    monkeypatch.setenv("SIGMA_MCP_ENABLE_CODE_MODE", "true")
    with pytest.raises(ValueError, match="mutually exclusive"):
        create_server(profile="full")


@pytest.mark.asyncio
async def test_job_profile_stays_flat_without_discovery_meta(caplog: pytest.LogCaptureFixture) -> None:
    """Job and readonly profiles expose a flat list; never search/Code Mode meta-tools."""
    with caplog.at_level(logging.WARNING):
        author = create_server(profile="author", enable_tool_search=True, enable_code_mode=False)
        readonly = create_server(profile="readonly", enable_tool_search=True)
    author_names = {t.name for t in await author.list_tools()}
    assert len(author_names) == 40
    assert not author_names & {"search_tools", "call_tool", "search", "execute"}
    assert len(await readonly.list_tools()) == 90
    messages = [r.message for r in caplog.records]
    assert any("Tool Search requested with profile='author'" in m for m in messages)
    assert any("Tool Search requested with profile='readonly'" in m for m in messages)


def test_tool_search_and_code_mode_are_mutually_exclusive() -> None:
    """Enabling both discovery modes raises ValueError."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        create_server(profile="full", enable_tool_search=True, enable_code_mode=True)


@pytest.mark.asyncio
async def test_full_code_mode_attaches_when_available(caplog: pytest.LogCaptureFixture) -> None:
    """full + enable_code_mode attaches experimental meta-tools; job profiles refuse it."""
    app = create_server(profile="full", enable_code_mode=True, enable_tool_search=False)
    names = {t.name for t in await app.list_tools()}
    assert "execute" in names
    assert "search_tools" not in names
    assert "workbooks_list_workbooks" not in names

    with caplog.at_level(logging.WARNING):
        job = create_server(profile="modeler", enable_code_mode=True)
    job_names = {t.name for t in await job.list_tools()}
    assert "execute" not in job_names
    assert "datasets_list_data_models" in job_names
    assert any("Code Mode requested with profile='modeler'" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_code_mode_skips_attach_without_sandbox(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Without pydantic-monty (no fastmcp[code-mode]), Code Mode is skipped with a warning.

    The CodeMode import itself succeeds on a plain install; only the sandbox is missing.
    """
    real_find_spec = server.importlib.util.find_spec

    def fake_find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pydantic_monty":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(server.importlib.util, "find_spec", fake_find_spec)
    with caplog.at_level(logging.WARNING):
        app = create_server(profile="full", enable_code_mode=True, enable_tool_search=False)
    names = {t.name for t in await app.list_tools()}
    assert "execute" not in names
    assert "search" not in names
    assert "workbooks_list_workbooks" in names
    assert any("pydantic-monty" in r.message and "fastmcp[code-mode]" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_parent_audit_middleware_success_and_redaction() -> None:
    """ParentAuditMiddleware returns results and redacts secrets from re-raised errors."""
    mw = ParentAuditMiddleware()
    ctx = MagicMock(spec=MiddlewareContext)
    ctx.message = MagicMock()
    ctx.message.name = "workbooks_list_workbooks"
    ctx.method = "tools/call"

    expected = MagicMock()

    async def ok_next(_ctx: MiddlewareContext) -> MagicMock:
        return expected

    assert await mw.on_message(ctx, ok_next) is expected

    async def failing_next(_ctx: MiddlewareContext) -> None:
        raise ValueError("Failed with Bearer secret-auth-token-xyz")

    with pytest.raises(ValueError) as exc_info:
        await mw.on_message(ctx, failing_next)
    assert "secret-auth-token-xyz" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_read_only_gate_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    """ReadOnlyGateMiddleware passes everything when off; when on, judges by readOnlyHint."""
    serving = SimpleNamespace(fastmcp=create_server(profile="full"))
    gate = ReadOnlyGateMiddleware()

    async def fake_next(_ctx: MiddlewareContext) -> str:
        return "success"

    def ctx(name: str) -> MiddlewareContext:
        return MiddlewareContext(
            method="tools/call",
            message=SimpleNamespace(name=name, arguments={}),
            fastmcp_context=serving,  # type: ignore[arg-type]
        )

    # Read-only off: writes pass
    assert await gate.on_message(ctx("workbooks_delete_workbook_schedule"), fake_next) == "success"

    # Read-only on via the live env flag: writes and recipe writes are refused
    monkeypatch.setenv("SIGMA_MCP_READONLY", "1")
    with pytest.raises(SafetyViolationError, match="'workbooks_delete_workbook_schedule' blocked"):
        await gate.on_message(ctx("workbooks_delete_workbook_schedule"), fake_next)
    with pytest.raises(SafetyViolationError, match="workbooks_deploy_template_to_folder"):
        await gate.on_message(ctx("workbooks_deploy_template_to_folder"), fake_next)

    # Reads and non-tools/call methods pass
    assert await gate.on_message(ctx("workbooks_list_workbooks"), fake_next) == "success"
    list_ctx = MiddlewareContext(method="tools/list", message=SimpleNamespace())
    assert await gate.on_message(list_ctx, fake_next) == "success"


@pytest.mark.asyncio
async def test_readonly_gate_unwraps_call_tool_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit: ReadOnlyGateMiddleware resolves the nested name inside call_tool arguments."""
    serving = SimpleNamespace(fastmcp=create_server(profile="full", enable_tool_search=True))
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    gate = ReadOnlyGateMiddleware()

    async def allowed_call_next(_ctx: MiddlewareContext) -> str:
        return "allowed"

    proxy_ctx = MiddlewareContext(
        method="tools/call",
        message=SimpleNamespace(
            name="call_tool",
            arguments={"name": "admin_delete_team", "arguments": {"team_id": "t-1"}},
        ),
        fastmcp_context=serving,  # type: ignore[arg-type]
    )
    with pytest.raises(SafetyViolationError) as exc_info:
        await gate.on_message(proxy_ctx, allowed_call_next)
    assert "admin_delete_team" in str(exc_info.value)

    read_ctx = MiddlewareContext(
        method="tools/call",
        message=SimpleNamespace(name="call_tool", arguments={"name": "admin_list_teams", "arguments": {}}),
        fastmcp_context=serving,  # type: ignore[arg-type]
    )
    assert await gate.on_message(read_ctx, allowed_call_next) == "allowed"


@pytest.mark.asyncio
async def test_readonly_gate_call_tool_without_nested_name_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """call_tool without a usable nested name is classified as itself: unannotated → refused."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    gate = ReadOnlyGateMiddleware()
    serving = SimpleNamespace(fastmcp=create_server(profile="full", enable_tool_search=True))

    async def allowed_call_next(_ctx: MiddlewareContext) -> str:
        return "allowed"

    for message in (
        SimpleNamespace(name="call_tool", arguments=None),
        SimpleNamespace(name="call_tool", arguments={"name": 123}),
        SimpleNamespace(name="call_tool", arguments={"name": ""}),
    ):
        ctx = MiddlewareContext(
            method="tools/call",
            message=message,
            fastmcp_context=serving,  # type: ignore[arg-type]
        )
        with pytest.raises(SafetyViolationError, match="'call_tool' blocked"):
            await gate.on_message(ctx, allowed_call_next)


@pytest.mark.asyncio
async def test_readonly_gate_without_server_context_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no serving FastMCP context the annotation cannot be read, so the call is refused."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    gate = ReadOnlyGateMiddleware()

    async def allowed_call_next(_ctx: MiddlewareContext) -> str:
        return "allowed"

    for message in (SimpleNamespace(name="workbooks_list_workbooks", arguments={}), None):
        ctx = MiddlewareContext(method="tools/call", message=message)
        with pytest.raises(SafetyViolationError, match="read-only mode"):
            await gate.on_message(ctx, allowed_call_next)


@pytest.mark.asyncio
async def test_admin_domain_guard_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    """AdminDomainGuardMiddleware refuses bulk tools (local or namespaced) unless the gate is on."""
    mw = AdminDomainGuardMiddleware()

    async def fake_next(_ctx: MiddlewareContext) -> str:
        return "ok"

    def ctx(name: str, method: str = "tools/call") -> MiddlewareContext:
        return MiddlewareContext(method=method, message=SimpleNamespace(name=name, arguments={}))

    for name in (
        "bulk_deactivate_members",
        "admin_bulk_deactivate_members",
        "bulk_remove_team_members",
        "admin_bulk_remove_team_members",
    ):
        with pytest.raises(SafetyViolationError, match="SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1"):
            await mw.on_message(ctx(name), fake_next)

    # Non-bulk tools, non-call methods, and an empty message pass.
    assert await mw.on_message(ctx("list_members"), fake_next) == "ok"
    assert await mw.on_message(ctx("bulk_deactivate_members", "tools/list"), fake_next) == "ok"
    assert await mw.on_message(MiddlewareContext(method="tools/call", message=None), fake_next) == "ok"

    monkeypatch.setenv("SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE", "1")
    assert await mw.on_message(ctx("admin_bulk_deactivate_members"), fake_next) == "ok"


def test_main_cli_profile_and_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI accepts --profile with --enable-tool-search (flat on a job profile)."""
    fake_run = MagicMock()
    monkeypatch.setattr(FastMCP, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["sigma-mcp", "--profile", "embed", "--enable-tool-search"])
    main()
    fake_run.assert_called_once()


@pytest.mark.parametrize(
    "argv",
    [
        ["--profile", "full", "--enable-code-mode"],
        ["--profile", "full", "--enable-tool-search", "--tool-search-backend", "bm25"],
        ["--profile", "access_admin"],
        ["--profile", "readonly"],
        ["--profile", "Access_Admin"],
    ],
)
def test_main_cli_discovery_and_job_profiles(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    """CLI accepts --enable-code-mode, --tool-search-backend, and job allowlist profiles."""
    fake_run = MagicMock()
    monkeypatch.setattr(FastMCP, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["sigma-mcp", *argv])
    main()
    fake_run.assert_called_once()


@pytest.mark.parametrize("profile", ["core", "admin", "builder"])
def test_main_cli_rejects_removed_profiles(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """Removed profile names are not CLI choices (argparse exits 2)."""
    monkeypatch.setattr("sys.argv", ["sigma-mcp", "--profile", profile])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2


def test_sigma_settings_discovery_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings defaults for profile, read-only, bulk, and discovery switches."""
    for var in (
        "SIGMA_MCP_PROFILE",
        "SIGMA_MCP_READONLY",
        "SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE",
        "SIGMA_MCP_ENABLE_TOOL_SEARCH",
        "SIGMA_MCP_TOOL_SEARCH_BACKEND",
        "SIGMA_MCP_ENABLE_CODE_MODE",
    ):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.MCP_PROFILE == "full"
    assert s.MCP_READONLY is False
    assert s.MCP_ALLOW_BULK_DESTRUCTIVE is False
    assert s.MCP_ENABLE_TOOL_SEARCH is False
    assert s.MCP_TOOL_SEARCH_BACKEND == "regex"
    assert s.MCP_ENABLE_CODE_MODE is False

    monkeypatch.setenv("SIGMA_MCP_ENABLE_CODE_MODE", "yes")
    monkeypatch.setenv("SIGMA_MCP_TOOL_SEARCH_BACKEND", "bm25")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.MCP_ENABLE_CODE_MODE is True
    assert s.MCP_TOOL_SEARCH_BACKEND == "bm25"


def test_sigma_settings_search_backend_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """An env value set before Settings() is stripped and lowercased; unknown values still fail."""
    monkeypatch.setenv("SIGMA_MCP_TOOL_SEARCH_BACKEND", " BM25 ")
    assert Settings(_env_file=None).MCP_TOOL_SEARCH_BACKEND == "bm25"  # type: ignore[call-arg]
    monkeypatch.setenv("SIGMA_MCP_TOOL_SEARCH_BACKEND", "Regex")
    assert Settings(_env_file=None).MCP_TOOL_SEARCH_BACKEND == "regex"  # type: ignore[call-arg]
    monkeypatch.setenv("SIGMA_MCP_TOOL_SEARCH_BACKEND", "fuzzy")
    with pytest.raises(ValidationError, match="literal_error"):
        Settings(_env_file=None)  # type: ignore[call-arg]
    monkeypatch.delenv("SIGMA_MCP_TOOL_SEARCH_BACKEND")
    with pytest.raises(ValidationError, match="literal_error"):
        Settings(_env_file=None, MCP_TOOL_SEARCH_BACKEND=25)  # type: ignore[call-arg, arg-type]
