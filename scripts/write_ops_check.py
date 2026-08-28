"""Write-safe integration check — creates resources then deletes them in teardown.

Creates a test folder, tag, and workspace, verifies they exist, then cleans up.
Requires SIGMA_CLIENT_ID, SIGMA_CLIENT_SECRET, SIGMA_API_BASE_URL env vars.

    source .env && PYTHONPATH=src python3 scripts/write_ops_check.py
"""

import asyncio
import json
import os
import sys
import uuid
from typing import Any

sys.path.insert(0, "src")

from sigma_mcp.server import mcp  # noqa: E402

PREFIX = f"mcptest-{uuid.uuid4().hex[:8]}"

# Track created resource IDs for cleanup
_created: dict[str, str] = {}


def payload(result: Any) -> dict[str, Any] | list[Any]:
    """Extract the JSON payload from an MCP CallToolResult."""
    return json.loads(result.content[0].text)


async def get_member_home() -> str:
    """Get the current user's memberId to use as parent for folder creation."""
    result = await mcp.call_tool("sigma_get_current_user", {})
    payload(result)
    result = await mcp.call_tool("sigma_list_files", {})
    files = payload(result)
    entries = files.get("entries", []) if isinstance(files, dict) else files
    for entry in entries:
        if isinstance(entry, dict) and entry.get("type") == "folder" and entry.get("name") == "My Documents":
            return str(entry.get("inodeId") or entry.get("id"))
    for entry in entries:
        if isinstance(entry, dict) and entry.get("type") == "folder":
            return str(entry.get("inodeId") or entry.get("id"))
    raise RuntimeError("No parent folder found for test folder creation")


def _extract_id(data: Any, *keys: str) -> str:
    if not isinstance(data, dict):
        return ""
    for k in keys:
        val = data.get(k)
        if val is not None:
            return str(val)
    return ""


async def create_folder(parent_id: str) -> str:
    """Create a test folder and return its inodeId."""
    result = await mcp.call_tool(
        "sigma_create_folder",
        {
            "name": f"{PREFIX}_folder",
            "parent_id": parent_id,
        },
    )
    data = payload(result)
    inode_id = _extract_id(data, "inodeId", "id")
    if not inode_id:
        raise RuntimeError(f"No inodeId in response: {data}")
    _created["folder"] = inode_id
    return inode_id


async def create_tag() -> str:
    """Create a test tag and return its tagId."""
    result = await mcp.call_tool(
        "sigma_create_tag",
        {
            "name": f"{PREFIX}_tag",
        },
    )
    data = payload(result)
    tag_id = _extract_id(data, "tagId", "id")
    if not tag_id:
        raise RuntimeError(f"No tagId in response: {data}")
    _created["tag"] = tag_id
    return tag_id


async def create_workspace() -> str:
    """Create a test workspace and return its workspaceId."""
    result = await mcp.call_tool(
        "sigma_create_workspace",
        {
            "name": f"{PREFIX}_ws",
        },
    )
    data = payload(result)
    ws_id = _extract_id(data, "workspaceId", "id")
    if not ws_id:
        raise RuntimeError(f"No workspaceId in response: {data}")
    _created["workspace"] = ws_id
    return ws_id


async def verify_folder(folder_id: str) -> None:
    """Verify the folder exists in list_files."""
    if not folder_id:
        raise AssertionError("No folder_id to verify")
    result = await mcp.call_tool("sigma_list_files", {})
    data = payload(result)
    entries = data.get("entries", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    found = any((e.get("inodeId") == folder_id or e.get("id") == folder_id) for e in entries if isinstance(e, dict))
    assert found, f"Folder {folder_id} not found in sigma_list_files"


async def verify_tag(tag_id: str) -> None:
    """Verify the tag exists in list_tags."""
    result = await mcp.call_tool("sigma_list_tags", {})
    data = payload(result)
    entries = data.get("entries", []) if isinstance(data, dict) else []
    if isinstance(data, list):
        entries = data
    found = any(t.get("tagId") == tag_id or t.get("id") == tag_id for t in entries if isinstance(t, dict))
    assert found, f"Tag {tag_id} not found in list_tags"


async def verify_workspace(ws_id: str) -> None:
    """Verify the workspace exists via get_workspace."""
    result = await mcp.call_tool("sigma_get_workspace", {"workspace_id": ws_id})
    data = payload(result)
    actual_name = data.get("name", "") if isinstance(data, dict) else ""
    assert f"{PREFIX}_ws" == actual_name, f"Workspace name mismatch: got {actual_name!r}"


async def teardown() -> list[str]:
    """Delete all created resources. Returns list of failures."""
    errors: list[str] = []

    if "folder" in _created:
        try:
            await mcp.call_tool("sigma_delete_file", {"inode_id": _created["folder"], "confirm": True})
        except Exception as e:
            errors.append(f"delete folder: {e}")

    if "tag" in _created:
        try:
            await mcp.call_tool("sigma_delete_tag", {"tag_id": _created["tag"], "confirm": True})
        except Exception as e:
            errors.append(f"delete tag: {e}")

    if "workspace" in _created:
        try:
            await mcp.call_tool("sigma_delete_workspace", {"workspace_id": _created["workspace"], "confirm": True})
        except Exception as e:
            errors.append(f"delete workspace: {e}")

    return errors


async def main() -> int:
    if (
        not os.environ.get("SIGMA_CLIENT_ID")
        or not os.environ.get("SIGMA_CLIENT_SECRET")
        or not os.environ.get("SIGMA_API_BASE_URL")
    ):
        print("SIGMA_CLIENT_ID, SIGMA_CLIENT_SECRET, and SIGMA_API_BASE_URL not set; skipping live write check.")
        return 0

    print("=== Write-safe integration check ===\n")
    test_errors: list[str] = []

    try:
        parent_id = await get_member_home()
        print(f"  INFO  Using parent folder: {parent_id}")
    except Exception as e:
        print(f"  FAIL  Could not find parent folder: {e}")
        return 1

    print("\nCreating resources...")
    try:
        folder_id = await create_folder(parent_id)
        print(f"  PASS  create_folder ({folder_id})")
    except Exception as e:
        test_errors.append(f"create_folder: {e}")
        print(f"  FAIL  create_folder: {e}")
        folder_id = ""

    try:
        tag_id = await create_tag()
        print(f"  PASS  create_tag ({tag_id})")
    except Exception as e:
        test_errors.append(f"create_tag: {e}")
        print(f"  FAIL  create_tag: {e}")
        tag_id = ""

    try:
        ws_id = await create_workspace()
        print(f"  PASS  create_workspace ({ws_id})")
    except Exception as e:
        test_errors.append(f"create_workspace: {e}")
        print(f"  FAIL  create_workspace: {e}")
        ws_id = ""

    print("\nVerifying resources...")
    if folder_id:
        try:
            await verify_folder(folder_id)
            print("  PASS  verify_folder")
        except Exception as e:
            test_errors.append(f"verify_folder: {e}")
            print(f"  FAIL  verify_folder: {e}")

    if tag_id:
        try:
            await verify_tag(tag_id)
            print("  PASS  verify_tag")
        except Exception as e:
            test_errors.append(f"verify_tag: {e}")
            print(f"  FAIL  verify_tag: {e}")

    if ws_id:
        try:
            await verify_workspace(ws_id)
            print("  PASS  verify_workspace")
        except Exception as e:
            test_errors.append(f"verify_workspace: {e}")
            print(f"  FAIL  verify_workspace: {e}")

    print("\nCleaning up...")
    cleanup_errors = await teardown()
    if cleanup_errors:
        for err in cleanup_errors:
            print(f"  WARN  {err}")
            test_errors.append(err)
    else:
        print("  PASS  all resources deleted")

    if test_errors:
        print(f"\n{len(test_errors)} error(s):")
        for e in test_errors:
            print(f"  - {e}")
        return 1

    print("\nAll write operations passed and cleaned up successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
