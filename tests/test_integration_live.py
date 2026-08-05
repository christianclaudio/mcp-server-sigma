"""Live integration tests against a REAL Sigma Computing organization.

GATING: Entire module is skipped unless SIGMA_LIVE_TESTS=1 AND SIGMA_CLIENT_ID is set.
This must NEVER run in CI by default.

Safety guarantees:
- Every created resource is named mcptest-{RUN_ID}-{purpose} (collision-free).
- A module-level registry tracks all created resource IDs.
- safe_delete() REFUSES to delete any ID not in the registry.
- Finalizers are registered BEFORE/immediately-after creation for crash safety.
- A pre-run sweep removes leaked mcptest-* resources older than 1 hour.

NOT tested live (deferred to mocked suite):
- Tenant token exchange / for_tenant
- sigma_bulk_deactivate_members, sigma_bulk_remove_team_members
- sigma_reassign_workbook_ownership
- member create/deactivate
- connection create/update/delete
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime

import pytest

# ─── Gating ───────────────────────────────────────────────────────────────────

_LIVE = os.environ.get("SIGMA_LIVE_TESTS") == "1"
_HAS_CREDS = bool(os.environ.get("SIGMA_CLIENT_ID"))

pytestmark = pytest.mark.skipif(
    not (_LIVE and _HAS_CREDS),
    reason="Live tests require SIGMA_LIVE_TESTS=1 and SIGMA_CLIENT_ID set",
)

# ─── Run ID & Registry ────────────────────────────────────────────────────────

RUN_ID = uuid.uuid4().hex[:8]
PREFIX = f"mcptest-{RUN_ID}"

# Module-level registry of resource IDs created during this run
_created_resources: set[str] = set()


def register(resource_id: str) -> None:
    """Register a resource ID as created by this test run."""
    _created_resources.add(resource_id)


def safe_delete(kind: str, resource_id: str, delete_fn) -> None:
    """Delete a resource ONLY if it was registered by this run. Raises otherwise."""
    if resource_id not in _created_resources:
        raise AssertionError(
            f"SAFETY VIOLATION: attempted to delete {kind} '{resource_id}' "
            f"which was NOT created by this test run (RUN_ID={RUN_ID}). "
            f"Registry contains: {_created_resources}"
        )
    run(delete_fn(resource_id))
    _created_resources.discard(resource_id)


# ─── Client helper ────────────────────────────────────────────────────────────


def _get_client():
    """Lazily import and instantiate the Sigma client."""
    from sigma_mcp.client import SigmaClient

    return SigmaClient(
        client_id=os.environ["SIGMA_CLIENT_ID"],
        client_secret=os.environ["SIGMA_CLIENT_SECRET"],
        base_url=os.environ.get("SIGMA_API_BASE_URL", "https://api.us-a.aws.sigmacomputing.com"),
    )


@pytest.fixture(scope="session")
def client():
    return _get_client()


def run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── Pre-run sweep (session-scoped autouse) ───────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def sweep_stale_mcptest_resources(client):
    """Delete any leftover mcptest-* resources older than 1 hour."""
    one_hour_ago = time.time() - 3600
    swept = []

    # Sweep files/folders
    files = run(client.list_files({"limit": 200}))
    entries = files.get("entries", []) if isinstance(files, dict) else []
    for f in entries:
        name = f.get("name", "")
        if not name.startswith("mcptest-"):
            continue
        created_at = f.get("createdAt") or f.get("created_at")
        if created_at:
            try:
                ts = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
                if ts > one_hour_ago:
                    continue  # Too recent, might be from another active run
            except (ValueError, AttributeError):
                continue  # Can't parse timestamp; skip rather than guess
        else:
            continue  # No timestamp available; skip rather than guess
        inode_id = f.get("id") or f.get("inodeId")
        if inode_id:
            try:
                run(client.delete_file(inode_id))
                swept.append(f"file:{name}({inode_id})")
            except Exception:
                pass

    # Sweep tags
    tags = run(client.list_tags())
    tag_entries = tags.get("entries", []) if isinstance(tags, dict) else []
    for t in tag_entries:
        name = t.get("name", "")
        if not name.startswith("mcptest-"):
            continue
        created_at = t.get("createdAt") or t.get("created_at")
        if created_at:
            try:
                ts = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
                if ts > one_hour_ago:
                    continue
            except (ValueError, AttributeError):
                continue
        else:
            continue
        tag_id = t.get("versionTagId") or t.get("tagId") or t.get("id")
        if tag_id:
            try:
                run(client.delete_tag(tag_id))
                swept.append(f"tag:{name}({tag_id})")
            except Exception:
                pass

    # Sweep workspaces
    workspaces = run(client.list_workspaces(200))
    ws_entries = workspaces.get("entries", []) if isinstance(workspaces, dict) else []
    for w in ws_entries:
        name = w.get("name", "")
        if not name.startswith("mcptest-"):
            continue
        created_at = w.get("createdAt") or w.get("created_at")
        if created_at:
            try:
                ts = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
                if ts > one_hour_ago:
                    continue
            except (ValueError, AttributeError):
                continue
        else:
            continue
        ws_id = w.get("workspaceId") or w.get("id")
        if ws_id:
            try:
                run(client.delete_workspace(ws_id))
                swept.append(f"workspace:{name}({ws_id})")
            except Exception:
                pass

    if swept:
        print(f"\n[SWEEP] Cleaned up {len(swept)} stale mcptest-* resources: {swept}")

    yield


# ─── Test: Folder lifecycle ───────────────────────────────────────────────────


class TestFolderLifecycle:
    """Create a folder, verify it appears in listings, then delete it."""

    def test_folder_create_list_delete(self, client, request):
        folder_name = f"{PREFIX}-folder"

        # Create
        result = run(client.create_file({"name": folder_name, "type": "folder"}))
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        folder_id = result.get("id") or result.get("inodeId")
        assert folder_id, f"No ID in response: {result}"
        register(folder_id)

        # Register finalizer IMMEDIATELY after creation
        def cleanup():
            try:
                safe_delete("folder", folder_id, client.delete_file)
            except Exception as e:
                print(f"[CLEANUP FAILED] folder {folder_id}: {e}")

        request.addfinalizer(cleanup)

        # Verify it exists via get_file (list_files may have eventual consistency)
        got = run(client.get_file(folder_id))
        assert isinstance(got, dict)
        assert got.get("name") == folder_name


# ─── Test: Workbook lifecycle ─────────────────────────────────────────────────


class TestWorkbookLifecycle:
    """Create a folder+workbook, inspect pages/elements, then delete both."""

    def test_workbook_create_inspect_delete(self, client, request):
        folder_name = f"{PREFIX}-wbfolder"
        wb_name = f"{PREFIX}-workbook"

        # Create folder first
        folder = run(client.create_file({"name": folder_name, "type": "folder"}))
        folder_id = folder.get("id") or folder.get("inodeId")
        assert folder_id
        register(folder_id)

        def cleanup_folder():
            try:
                safe_delete("folder", folder_id, client.delete_file)
            except Exception as e:
                print(f"[CLEANUP FAILED] folder {folder_id}: {e}")

        request.addfinalizer(cleanup_folder)

        # Create workbook inside folder
        wb = run(client.create_workbook(wb_name, folder_id, "integration test workbook"))
        wb_id = wb.get("workbookId") or wb.get("id")
        assert wb_id, f"No workbook ID in response: {wb}"
        register(wb_id)

        def cleanup_wb():
            try:
                safe_delete("workbook", wb_id, client.delete_file)
            except Exception as e:
                print(f"[CLEANUP FAILED] workbook {wb_id}: {e}")

        request.addfinalizer(cleanup_wb)

        # Get workbook
        wb_detail = run(client.get_workbook(wb_id))
        assert isinstance(wb_detail, dict)
        assert wb_detail.get("workbookId") == wb_id or wb_detail.get("id") == wb_id

        # List pages
        pages = run(client.list_workbook_pages(wb_id))
        # New workbooks may have 0 or 1 default page
        assert isinstance(pages, (dict, list))

        # List elements (may be empty for a blank workbook)
        elements = run(client.list_workbook_elements(wb_id))
        assert isinstance(elements, (dict, list))


# ─── Test: Tag lifecycle ──────────────────────────────────────────────────────


class TestTagLifecycle:
    """Create a tag, apply to workbook, list tags on workbook, remove, delete."""

    def test_tag_full_cycle(self, client, request):
        tag_name = f"{PREFIX}-tag"
        folder_name = f"{PREFIX}-tagfolder"
        wb_name = f"{PREFIX}-tagwb"

        # Create supporting folder + workbook
        folder = run(client.create_file({"name": folder_name, "type": "folder"}))
        folder_id = folder.get("id") or folder.get("inodeId")
        assert folder_id
        register(folder_id)
        request.addfinalizer(lambda: safe_delete("folder", folder_id, client.delete_file))

        wb = run(client.create_workbook(wb_name, folder_id))
        wb_id = wb.get("workbookId") or wb.get("id")
        assert wb_id
        register(wb_id)
        request.addfinalizer(lambda: safe_delete("workbook", wb_id, client.delete_file))

        # Create tag (Sigma requires a color field)
        tag = run(client.create_tag({"name": tag_name, "color": "cyan"}))
        tag_id = tag.get("versionTagId") or tag.get("tagId") or tag.get("id")
        assert tag_id, f"No tag ID in response: {tag}"
        register(tag_id)

        def cleanup_tag():
            try:
                safe_delete("tag", tag_id, client.delete_tag)
            except Exception as e:
                print(f"[CLEANUP FAILED] tag {tag_id}: {e}")

        request.addfinalizer(cleanup_tag)

        # Apply tag to workbook. The Sigma API takes the tag NAME here, not the
        # tag ID — see client.tag_workbook.
        run(client.tag_workbook(wb_id, tag_name))

        # List workbook tags
        wb_tags = run(client.get_workbook_tags(wb_id))
        if isinstance(wb_tags, dict):
            tag_entries = wb_tags.get("entries", wb_tags.get("tags", []))
        elif isinstance(wb_tags, list):
            tag_entries = wb_tags
        else:
            tag_entries = []
        tag_ids_on_wb = [
            t.get("versionTagId") or t.get("tagId") or t.get("id") for t in tag_entries if isinstance(t, dict)
        ]
        assert tag_id in tag_ids_on_wb, f"Tag {tag_id} not found on workbook. Got: {tag_ids_on_wb}"

        # Remove tag from workbook
        run(client.remove_workbook_tag(wb_id, tag_id))


# ─── Test: Workspace lifecycle ────────────────────────────────────────────────


class TestWorkspaceLifecycle:
    """Create a workspace, get it, list grants, delete it."""

    def test_workspace_create_get_grants_delete(self, client, request):
        ws_name = f"{PREFIX}-workspace"

        # Create
        ws = run(client.create_workspace({"name": ws_name}))
        ws_id = ws.get("workspaceId") or ws.get("id")
        assert ws_id, f"No workspace ID: {ws}"
        register(ws_id)

        def cleanup_ws():
            try:
                safe_delete("workspace", ws_id, client.delete_workspace)
            except Exception as e:
                print(f"[CLEANUP FAILED] workspace {ws_id}: {e}")

        request.addfinalizer(cleanup_ws)

        # Get
        detail = run(client.get_workspace(ws_id))
        assert isinstance(detail, dict)

        # List grants
        grants = run(client.list_workspace_grants(ws_id))
        assert isinstance(grants, (dict, list))


# ─── Test: User attributes (read-only) ───────────────────────────────────────


class TestUserAttributesReadOnly:
    """Exercise read-only user attribute GET tools against a real attribute."""

    def test_list_and_get_user_attributes(self, client):
        attrs = run(client.list_user_attributes())
        entries = attrs.get("entries", []) if isinstance(attrs, dict) else attrs if isinstance(attrs, list) else []
        if not entries:
            pytest.skip("No user attributes exist in this org to test read-only access")

        # Pick the first attribute and read its assignments
        first = entries[0]
        attr_id = first.get("userAttributeId") or first.get("id") or first.get("attributeId")
        assert attr_id, f"Cannot find attribute ID in keys: {list(first.keys())}"

        # Get team assignments (read-only, safe)
        team_assigns = run(client.get_user_attribute_team_assignments(attr_id))
        assert team_assigns is not None  # Just confirm it doesn't error


# ─── Test: Structured error on bad ID ─────────────────────────────────────────


class TestStructuredError:
    """Confirm calling get_workbook with a bogus UUID returns structured JSON error."""

    def test_get_workbook_bad_id_returns_json_error(self, client, monkeypatch):
        bogus_id = "00000000-0000-0000-0000-000000000000"
        # The server.py sigma_tool wrapper catches SigmaAPIError and returns JSON
        # We call the client directly which raises; but the requirement is about
        # the MCP tool layer. Let's import and call the tool function directly.
        import sigma_mcp.server as srv
        from sigma_mcp.server import sigma_get_workbook

        # Ensure the server module uses our client (restored automatically by monkeypatch)
        monkeypatch.setattr(srv, "_client", client)

        result = run(sigma_get_workbook(bogus_id))
        parsed = json.loads(result)
        assert "error" in parsed, f"Expected 'error' key in response, got: {parsed}"
        err = parsed["error"]
        assert "status_code" in err or "statusCode" in err or "type" in err, f"Error dict missing status info: {err}"


# ─── Post-run residue check (runs as final test) ─────────────────────────────


class TestZZZNoResidue:
    """Verify no mcptest-* resources remain after all tests complete.

    Named ZZZ to sort last alphabetically.
    """

    def test_no_residual_files(self, client):
        files = run(client.list_files({"limit": 200}))
        entries = files.get("entries", []) if isinstance(files, dict) else []
        residual = [e for e in entries if (e.get("name", "")).startswith(PREFIX)]
        assert residual == [], f"Residual mcptest files found: {residual}"

    def test_no_residual_tags(self, client):
        tags = run(client.list_tags())
        entries = tags.get("entries", []) if isinstance(tags, dict) else []
        residual = [t for t in entries if (t.get("name", "")).startswith(PREFIX)]
        assert residual == [], f"Residual mcptest tags found: {residual}"

    def test_no_residual_workspaces(self, client):
        ws = run(client.list_workspaces(200))
        entries = ws.get("entries", []) if isinstance(ws, dict) else []
        residual = [w for w in entries if (w.get("name", "")).startswith(PREFIX)]
        assert residual == [], f"Residual mcptest workspaces found: {residual}"
