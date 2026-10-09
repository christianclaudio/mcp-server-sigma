"""Tests for job allowlist profiles and the annotation-driven (readOnlyHint-only) readonly gate."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.server.transforms.search import RegexSearchTransform
from mcp.types import ToolAnnotations
from scripts.check_tool_contract import (
    EXPECTED_DEFAULT,
    EXPECTED_FULL_ONLY,
    EXPECTED_PROFILE_COUNTS,
    EXPECTED_READ_ONLY,
)

from sigma_mcp import middleware, profiles, server
from sigma_mcp.config import settings
from sigma_mcp.errors import SafetyViolationError
from sigma_mcp.middleware import ReadOnlyGateMiddleware
from sigma_mcp.profiles import (
    BULK_DESTRUCTIVE_TOOLS,
    FULL_ONLY_TOOLS,
    PROFILES,
    Profile,
    ReadOnlyToolFilter,
    is_read_only_tool,
)
from sigma_mcp.server import create_server

JOB_PROFILES = [p for p in PROFILES.values() if p.is_allowlist]
ANALYST = PROFILES["analyst"]

READ = "workbooks_list_workbooks"
WRITE = "workbooks_create_workbook"
WRITE_ARGS = {"name": "blocked", "folder_id": "f-1"}


@pytest.fixture
def api() -> MagicMock:
    """Offline Sigma client: reads return a fixture; write methods are spies."""
    client = MagicMock()
    client.list_workbooks = AsyncMock(return_value={"entries": [{"workbookId": "wb-7", "name": "Apollo"}]})
    client.get_current_user = AsyncMock(return_value={"memberId": "m-1", "email": "me@example.com"})
    client.create_workbook = AsyncMock(return_value={"workbookId": "wb-new"})
    client.aclose = AsyncMock()
    return client


@pytest.fixture(autouse=True)
def _offline_client(monkeypatch: pytest.MonkeyPatch, api: MagicMock) -> Iterator[None]:
    """Gate switches off and every tool resolves the offline client."""
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
    monkeypatch.setattr(server, "get_client", AsyncMock(return_value=api))
    yield


async def _tool_names(app: FastMCP) -> set[str]:
    return {t.name for t in await app.list_tools()}


async def _read_only_names(app: FastMCP) -> set[str]:
    return {t.name for t in await app.list_tools() if is_read_only_tool(t)}


# ---------------------------------------------------------------------------
# Profile counts (signed off) and catalog placement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("profile", "expected"), sorted(EXPECTED_PROFILE_COUNTS.items()))
async def test_profile_counts_match_signed_off(profile: str, expected: tuple[int, int]) -> None:
    """Every signed-off profile builds with exactly its total and read-only tool counts.

    The signed-off counts live in ``scripts/check_tool_contract.py`` (``EXPECTED_PROFILE_COUNTS``).
    """
    app = create_server(profile=profile)
    assert (len(await _tool_names(app)), len(await _read_only_names(app))) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", sorted(EXPECTED_PROFILE_COUNTS))
async def test_profile_counts_compose_with_readonly_env(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """SIGMA_MCP_READONLY=1 on any profile keeps exactly that profile's read-only tools."""
    plain = await _read_only_names(create_server(profile=profile))
    monkeypatch.setenv("SIGMA_MCP_READONLY", "1")
    names = await _tool_names(create_server(profile=profile))
    assert names == plain
    assert len(names) == EXPECTED_PROFILE_COUNTS[profile][1]


@pytest.mark.asyncio
async def test_full_is_exhaustive_and_every_tool_is_placed() -> None:
    """full lists every tool; each tool is in a job profile or marked full-only."""
    full_names = await _tool_names(create_server(profile="full"))
    in_jobs = set().union(*(p.tools or frozenset() for p in JOB_PROFILES))
    assert in_jobs | FULL_ONLY_TOOLS == full_names
    assert not in_jobs & FULL_ONLY_TOOLS


def test_full_only_tools() -> None:
    """The full-only tools: both bulk tools, four workspace file/tag mutators, three admin lookups."""
    assert FULL_ONLY_TOOLS == EXPECTED_FULL_ONLY
    assert BULK_DESTRUCTIVE_TOOLS <= FULL_ONLY_TOOLS
    assert server.FULL_ONLY_TOOLS is FULL_ONLY_TOOLS


def test_job_profiles_are_the_signed_off_five() -> None:
    """builder is split into author and modeler; the old core/admin profiles are gone."""
    assert {p.name for p in JOB_PROFILES} == {"analyst", "author", "modeler", "embed", "access_admin"}
    assert set(PROFILES) == set(EXPECTED_PROFILE_COUNTS)
    for removed in ("core", "admin", "builder"):
        assert removed not in PROFILES


def test_every_profile_has_a_one_line_job() -> None:
    """Each profile documents the job it serves in a single line."""
    for profile in PROFILES.values():
        assert profile.job.strip()
        assert "\n" not in profile.job


@pytest.mark.asyncio
async def test_every_tool_has_explicit_read_only_hint() -> None:
    """Reads carry readOnlyHint=True and writes readOnlyHint=False; none leave it unset."""
    tools = await create_server(profile="full").list_tools()
    missing = sorted(t.name for t in tools if t.annotations is None or t.annotations.read_only_hint is None)
    assert missing == []
    reads = {t.name for t in tools if t.annotations and t.annotations.read_only_hint is True}
    writes = {t.name for t in tools if t.annotations and t.annotations.read_only_hint is False}
    assert len(reads) == EXPECTED_READ_ONLY
    assert len(writes) == EXPECTED_DEFAULT - EXPECTED_READ_ONLY


@pytest.mark.asyncio
async def test_author_and_modeler_split_overlap_on_lookups_only() -> None:
    """The former builder job is split: author and modeler share 10 read-only lookups."""
    author = await _tool_names(create_server(profile="author"))
    modeler = await _tool_names(create_server(profile="modeler"))
    shared = author & modeler
    assert len(shared) == 10
    full = {t.name: t for t in await create_server(profile="full").list_tools()}
    assert all(is_read_only_tool(full[name]) for name in shared)
    assert "workbooks_promote_workbook" not in author | modeler
    assert "workbooks_promote_workbook" in await _tool_names(create_server(profile="embed"))


# ---------------------------------------------------------------------------
# Allowlist profiles: tools-only filter over every domain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowlist_profile_exposes_exactly_its_tools() -> None:
    """analyst exposes exactly its allowlist, spanning several domains."""
    assert ANALYST.tools is not None
    names = await _tool_names(create_server(profile="analyst"))
    assert names == set(ANALYST.tools)
    assert {n.split("_", 1)[0] for n in names} == {"workbooks", "elements", "workspace", "admin"}


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [p.name for p in JOB_PROFILES])
async def test_allowlist_keeps_prompts_and_resources(profile: str) -> None:
    """Allowlist filtering is tools-only: prompts and resources match the full profile."""
    full = create_server(profile="full")
    job = create_server(profile=profile)
    assert {p.name for p in await job.list_prompts()} == {p.name for p in await full.list_prompts()}
    assert {str(r.uri) for r in await job.list_resources()} == {str(r.uri) for r in await full.list_resources()}
    assert {p.name for p in await job.list_prompts()} == {
        "workbooks_provision_tenant_dashboard",
        "datasets_prepare_data_model",
        "datasets_audit_tenant_connections",
        "elements_swap_warehouse_source",
        "admin_audit_organization_permissions",
        "admin_onboard_team_member",
    }
    assert {str(r.uri) for r in await job.list_resources()} == {
        "elements://reference/formulas",
        "admin://reference/capabilities",
        "elements://reference/docs-index",
        "admin://webhooks/recent",
    }
    rendered = await job.render_prompt("datasets_prepare_data_model", {"connection_id": "c-42", "model_name": "m"})
    assert "c-42" in str(rendered)


@pytest.mark.asyncio
async def test_allowlist_hides_tools_outside_the_list() -> None:
    """A tool outside the allowlist cannot be called on that profile."""
    app = create_server(profile="analyst")
    with pytest.raises(NotFoundError, match="Unknown tool"):
        await app.call_tool("admin_delete_team", {"team_id": "t-1", "confirm": True})


def test_unknown_allowlist_name_fails_at_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every allowlisted name must exist in the full catalog; no silent drop."""
    broken = Profile(
        name="broken",
        job="Test only.",
        tools=frozenset({"workbooks_list_workbooks", "workbooks_nonexistent", "sigma_list_workbooks"}),
    )
    monkeypatch.setitem(profiles.PROFILES, "broken", broken)
    with pytest.raises(ValueError, match="sigma_list_workbooks, workbooks_nonexistent"):
        create_server(profile="broken")


@pytest.mark.parametrize("name", ["nope", "core", "admin", "builder"])
def test_unknown_profile_fails_at_build(name: str) -> None:
    """An unknown or removed profile name raises instead of building an empty server."""
    with pytest.raises(ValueError, match=f"Unknown profile '{name}'"):
        create_server(profile=name)


def test_unknown_profile_from_env_fails_at_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """SIGMA_MCP_PROFILE is validated the same way as the argument."""
    monkeypatch.setenv("SIGMA_MCP_PROFILE", "core")
    with pytest.raises(ValueError, match="Unknown profile 'core'"):
        create_server()


@pytest.mark.parametrize("profile", ["analyst", "author", "modeler", "embed", "access_admin"])
def test_main_accepts_job_profile(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """Every job profile is a valid CLI choice."""
    fake_run = MagicMock()
    monkeypatch.setattr(FastMCP, "run", fake_run)
    # main() rebinds the module-level server; restore it for later tests.
    monkeypatch.setattr(server, "mcp", server.mcp)
    monkeypatch.setattr("sys.argv", ["sigma-mcp", "--profile", profile])
    server.main()
    fake_run.assert_called_once()


# ---------------------------------------------------------------------------
# Bulk destructive tools: listed in full, refused at call time without the env
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("admin_bulk_deactivate_members", {"name_pattern": "^probe$", "dry_run": False, "confirm": True}),
        ("admin_bulk_remove_team_members", {"team_id": "t-1", "member_emails": ["a@b.co"], "confirm": True}),
    ],
)
async def test_bulk_tool_listed_in_full_but_blocked_without_gate(
    api: MagicMock, tool: str, arguments: dict[str, Any]
) -> None:
    """Bulk destructive tools are listed in full but refused (isError) while the bulk gate is off."""
    app = create_server(profile="full")
    assert tool in await _tool_names(app)
    with pytest.raises(SafetyViolationError, match="Bulk destructive operations disabled"):
        await app.call_tool(tool, arguments)
    async with Client(app) as client:
        res = await client.call_tool(tool, arguments, raise_on_error=False)
    assert res.is_error
    assert "SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1" in str(res.content)
    assert not api.method_calls


@pytest.mark.asyncio
async def test_bulk_tool_runs_with_gate_and_still_needs_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """With SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1 the call reaches the handler's confirm gate."""
    monkeypatch.setenv("SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE", "1")
    app = create_server(profile="full")
    res = await app.call_tool(
        "admin_bulk_remove_team_members", {"team_id": "t-1", "member_emails": ["a@b.co"], "confirm": False}
    )
    assert not res.is_error
    message = json.loads(res.content[0].text)["error"]["message"]  # type: ignore[union-attr]
    assert "confirm=True" in message


@pytest.mark.asyncio
async def test_bulk_tools_absent_from_every_job_profile() -> None:
    """Bulk tools are full-only: no job profile lists them."""
    for profile in JOB_PROFILES:
        assert not BULK_DESTRUCTIVE_TOOLS & await _tool_names(create_server(profile=profile.name))


# ---------------------------------------------------------------------------
# Discovery stays full-only on allowlist profiles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowlist_profile_with_search_stays_flat(caplog: pytest.LogCaptureFixture) -> None:
    """Requesting Tool Search or Code Mode on an allowlist profile logs and stays flat."""
    with caplog.at_level(logging.WARNING):
        searched = create_server(profile="analyst", enable_tool_search=True)
        coded = create_server(profile="analyst", enable_code_mode=True)
    assert ANALYST.tools is not None
    assert await _tool_names(searched) == set(ANALYST.tools)
    assert await _tool_names(coded) == set(ANALYST.tools)
    messages = [r.message for r in caplog.records]
    assert any("Tool Search requested with profile='analyst'" in m for m in messages)
    assert any("Code Mode requested with profile='analyst'" in m for m in messages)


def test_discovery_modes_mutually_exclusive_on_allowlist_profile() -> None:
    """Mutual exclusion is checked before profile handling."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        create_server(profile="analyst", enable_tool_search=True, enable_code_mode=True)


# ---------------------------------------------------------------------------
# Read-only: readOnlyHint is the single source of truth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readonly_profile_equals_read_only_annotated_set() -> None:
    """The readonly profile exposes exactly the full tools annotated readOnlyHint=True."""
    annotated = await _read_only_names(create_server(profile="full"))
    assert await _tool_names(create_server(profile="readonly")) == annotated


@pytest.mark.asyncio
async def test_readonly_profile_gate_enforced_without_env(api: MagicMock) -> None:
    """profile=readonly enforces the gate itself, even with SIGMA_MCP_READONLY unset."""
    app = create_server(profile="readonly")
    with pytest.raises(SafetyViolationError, match=WRITE):
        await app.call_tool(WRITE, WRITE_ARGS)
    api.create_workbook.assert_not_awaited()
    res = await app.call_tool(READ, {})
    assert not res.is_error
    assert "Apollo" in str(res.content)


@pytest.mark.asyncio
async def test_readonly_enforced_under_allowlist_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """SIGMA_MCP_READONLY=1 on an allowlist profile hides and refuses its write tools."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="author")
    assert len(await _tool_names(app)) == 28
    with pytest.raises(SafetyViolationError, match=WRITE):
        await app.call_tool(WRITE, WRITE_ARGS)
    res = await app.call_tool(READ, {})
    assert not res.is_error


def _annotation_probe_server() -> FastMCP:
    """Minimal server with one tool per annotation state, gate on, and Tool Search."""
    app = FastMCP("annotation-probe")
    app.add_middleware(ReadOnlyGateMiddleware())

    @app.tool(annotations=ToolAnnotations(read_only_hint=True))
    def annotated_read() -> str:
        return "read"

    @app.tool(annotations=ToolAnnotations(read_only_hint=False))
    def annotated_write() -> str:
        return "write"

    @app.tool
    def unannotated() -> str:
        return "unannotated"

    @app.tool(annotations=ToolAnnotations(title="no hint"))
    def hint_missing() -> str:
        return "hint missing"

    app.add_transform(RegexSearchTransform())
    return app


@pytest.mark.asyncio
async def test_gate_allows_read_only_annotation_directly_and_via_call_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """readOnlyHint=True is allowed under readonly, directly and through call_tool."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = _annotation_probe_server()
    direct = await app.call_tool("annotated_read", {})
    assert "read" in str(direct.content)
    proxied = await app.call_tool("call_tool", {"name": "annotated_read", "arguments": {}})
    assert "read" in str(proxied.content)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["annotated_write", "unannotated", "hint_missing"])
async def test_gate_refuses_false_or_missing_annotation(monkeypatch: pytest.MonkeyPatch, tool_name: str) -> None:
    """readOnlyHint=False or no hint is a write: refused directly and via call_tool."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = _annotation_probe_server()
    with pytest.raises(SafetyViolationError, match=tool_name):
        await app.call_tool(tool_name, {})
    with pytest.raises(SafetyViolationError, match=tool_name):
        await app.call_tool("call_tool", {"name": tool_name, "arguments": {}})


@pytest.mark.asyncio
async def test_readonly_tool_filter_get_tool() -> None:
    """ReadOnlyToolFilter.get_tool returns read-only tools and drops the rest."""
    app = _annotation_probe_server()
    app.add_transform(ReadOnlyToolFilter())
    assert await app.get_tool("annotated_read") is not None
    assert await app.get_tool("annotated_write") is None
    assert await app.get_tool("unannotated") is None


def test_safety_violation_is_a_tool_error() -> None:
    """Refusals are FastMCP ToolErrors, so clients get a tools/call result with isError: true."""
    assert issubclass(SafetyViolationError, ToolError)


# ---------------------------------------------------------------------------
# Read-only through the Tool Search proxy on full (the fixed search bypass / over-block)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_search_readonly_read_succeeds_write_refused_on_wire(
    monkeypatch: pytest.MonkeyPatch, api: MagicMock
) -> None:
    """On full + Tool Search + readonly: a READ via call_tool works; a WRITE is isError."""
    monkeypatch.setenv("SIGMA_MCP_READONLY", "1")
    app = create_server(profile="full", enable_tool_search=True)
    async with Client(app) as client:
        read = await client.call_tool("call_tool", {"name": READ, "arguments": {}}, raise_on_error=False)
        assert not read.is_error
        assert "Apollo" in str(read.content)

        write = await client.call_tool("call_tool", {"name": WRITE, "arguments": WRITE_ARGS}, raise_on_error=False)
        assert write.is_error
        assert "read-only mode" in str(write.content)
        assert WRITE in str(write.content)
    api.create_workbook.assert_not_awaited()


@pytest.mark.asyncio
async def test_gate_refuses_on_outer_call_tool_invocation(monkeypatch: pytest.MonkeyPatch, api: MagicMock) -> None:
    """Spy: the refusal comes from the gate unwrapping the outer call_tool, not an inner re-run."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    seen: list[tuple[str, str]] = []
    real_resolve = middleware._resolve_effective_tool_name

    def spy(context: MiddlewareContext) -> str:
        effective = real_resolve(context)
        seen.append((context.message.name, effective))
        return effective

    monkeypatch.setattr(middleware, "_resolve_effective_tool_name", spy)
    app = create_server(profile="full", enable_tool_search=True)
    with pytest.raises(SafetyViolationError, match=WRITE):
        await app.call_tool("call_tool", {"name": WRITE, "arguments": WRITE_ARGS})
    # Exactly one gate decision: on the outer proxy call. The inner call never ran.
    assert seen == [("call_tool", WRITE)]
    api.create_workbook.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_unwrap_the_proxy_gate_behaviour_breaks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative proof: with the unwrap removed, reads via call_tool are refused (the old sigma
    bug) and the write refusal names the proxy instead of the proxied tool."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)

    def no_unwrap(context: MiddlewareContext) -> str:
        name: Any = getattr(context.message, "name", "")
        return str(name)

    monkeypatch.setattr(middleware, "_resolve_effective_tool_name", no_unwrap)
    app = create_server(profile="full", enable_tool_search=True)
    with pytest.raises(SafetyViolationError, match="'call_tool' blocked"):
        await app.call_tool("call_tool", {"name": READ, "arguments": {}})
    with pytest.raises(SafetyViolationError) as exc_info:
        await app.call_tool("call_tool", {"name": WRITE, "arguments": WRITE_ARGS})
    assert WRITE not in str(exc_info.value)


@pytest.mark.asyncio
async def test_search_tools_annotated_read_only_and_usable_under_readonly(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_tools carries readOnlyHint=True, works under readonly, and finds only reads."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full", enable_tool_search=True)
    listed = {t.name: t for t in await app.list_tools()}
    assert is_read_only_tool(listed["search_tools"])
    assert not is_read_only_tool(listed["call_tool"])
    res = await app.call_tool("search_tools", {"pattern": "workbooks_"})
    assert not res.is_error
    assert READ in str(res.content)
    assert WRITE not in str(res.content)
    assert await app.get_tool("not_a_tool") is None


@pytest.mark.asyncio
async def test_code_mode_discovery_read_only_and_execute_refused_under_readonly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code Mode search/get_schema are annotated read-only; execute is refused under readonly."""
    try:
        from fastmcp.experimental.transforms.code_mode import CodeMode  # noqa: F401
    except ImportError:  # pragma: no cover - depends on FastMCP build
        pytest.skip("CodeMode not available in this FastMCP build")

    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full", enable_code_mode=True)
    listed = {t.name: t for t in await app.list_tools()}
    assert is_read_only_tool(listed["search"])
    assert is_read_only_tool(listed["get_schema"])
    assert not is_read_only_tool(listed["execute"])
    with pytest.raises(SafetyViolationError, match="'execute' blocked"):
        await app.call_tool("execute", {"code": "return 1"})


# ---------------------------------------------------------------------------
# Read-only: unknown names keep FastMCP's "Unknown tool"; real writes are refused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readonly_unknown_name_direct_is_unknown_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Direct call to a name outside the catalog gets Unknown tool, not the read-only refusal."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full")
    with pytest.raises(NotFoundError, match="Unknown tool: 'sigma_list_workbooks'"):
        await app.call_tool("sigma_list_workbooks", {})
    async with Client(app) as client:
        res = await client.call_tool("sigma_list_workbooks", {}, raise_on_error=False)
    assert res.is_error
    assert "Unknown tool" in str(res.content)
    assert "read-only" not in str(res.content)


@pytest.mark.asyncio
async def test_readonly_unknown_name_via_call_tool_is_unknown_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """call_tool with a name outside the catalog gets Unknown tool on full + search."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full", enable_tool_search=True)
    with pytest.raises(ToolError, match="Unknown tool: 'workbooks_nope'") as exc_info:
        await app.call_tool("call_tool", {"name": "workbooks_nope", "arguments": {}})
    assert not isinstance(exc_info.value, SafetyViolationError)
    async with Client(app) as client:
        res = await client.call_tool("call_tool", {"name": "workbooks_nope", "arguments": {}}, raise_on_error=False)
    assert res.is_error
    assert "Unknown tool" in str(res.content)
    assert "read-only" not in str(res.content)


@pytest.mark.asyncio
@pytest.mark.parametrize("via_proxy", [False, True])
async def test_readonly_hidden_write_refused_and_read_succeeds(
    monkeypatch: pytest.MonkeyPatch, api: MagicMock, via_proxy: bool
) -> None:
    """A real write hidden by the read-only filter is refused (isError); a real read still works."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full", enable_tool_search=via_proxy)
    if not via_proxy:
        assert WRITE not in await _tool_names(app)

    def call(name: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if via_proxy:
            return "call_tool", {"name": name, "arguments": arguments}
        return name, arguments

    async with Client(app) as client:
        write = await client.call_tool(*call(WRITE, WRITE_ARGS), raise_on_error=False)
        read = await client.call_tool(*call(READ, {}), raise_on_error=False)
    assert write.is_error
    assert "read-only mode" in str(write.content)
    assert WRITE in str(write.content)
    assert not read.is_error
    assert "Apollo" in str(read.content)
    with pytest.raises(SafetyViolationError, match=WRITE):
        await app.call_tool(*call(WRITE, WRITE_ARGS))
    api.create_workbook.assert_not_awaited()


@pytest.mark.asyncio
async def test_readonly_outside_allowlist_is_unknown_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under read-only, a tool outside the active allowlist profile is unknown, like without it."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="analyst")
    with pytest.raises(NotFoundError, match="Unknown tool"):
        await app.call_tool("admin_delete_team", {"team_id": "t-1"})
    with pytest.raises(SafetyViolationError, match="workbooks_add_workbook_bookmark"):
        await app.call_tool("workbooks_add_workbook_bookmark", {})


@pytest.mark.asyncio
async def test_readonly_switched_on_after_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """With read-only switched on at runtime (no filter, empty gate catalog), unknown names
    still reach Unknown tool and real writes are still refused."""
    app = create_server(profile="full")
    monkeypatch.setenv("SIGMA_MCP_READONLY", "1")
    with pytest.raises(NotFoundError, match="Unknown tool"):
        await app.call_tool("workbooks_nope", {})
    with pytest.raises(SafetyViolationError, match=WRITE):
        await app.call_tool(WRITE, WRITE_ARGS)


# ---------------------------------------------------------------------------
# Read-only without Tool Search: call_tool is an unknown name, not a proxy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["full", "analyst", "readonly"])
@pytest.mark.parametrize("inner", [WRITE, READ])
async def test_readonly_call_tool_without_search_is_unknown_tool(
    monkeypatch: pytest.MonkeyPatch, profile: str, inner: str
) -> None:
    """Without Tool Search, call_tool is not on the server: Unknown tool 'call_tool' for both
    a write and a read inner name, never the read-only refusal."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile=profile, enable_tool_search=False)
    arguments = {"name": inner, "arguments": {}}
    with pytest.raises(NotFoundError, match="Unknown tool: 'call_tool'") as exc_info:
        await app.call_tool("call_tool", arguments)
    assert not isinstance(exc_info.value, SafetyViolationError)
    async with Client(app) as client:
        res = await client.call_tool("call_tool", arguments, raise_on_error=False)
    assert res.is_error
    assert "Unknown tool: 'call_tool'" in str(res.content)
    assert "read-only" not in str(res.content)


@pytest.mark.asyncio
async def test_readonly_call_tool_with_search_still_unwraps(monkeypatch: pytest.MonkeyPatch) -> None:
    """With Tool Search on full, call_tool is real: unwrap and judge the proxied tool."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full", enable_tool_search=True)
    with pytest.raises(SafetyViolationError, match=WRITE):
        await app.call_tool("call_tool", {"name": WRITE, "arguments": WRITE_ARGS})
    read = await app.call_tool("call_tool", {"name": READ, "arguments": {}})
    assert not read.is_error


@pytest.mark.asyncio
async def test_full_search_without_readonly_runs_writes_through_call_tool(api: MagicMock) -> None:
    """Read-only off: call_tool proxies writes normally (the gate only acts in read-only mode)."""
    app = create_server(profile="full", enable_tool_search=True)
    res = await app.call_tool("call_tool", {"name": WRITE, "arguments": WRITE_ARGS})
    assert not res.is_error
    api.create_workbook.assert_awaited_once()
