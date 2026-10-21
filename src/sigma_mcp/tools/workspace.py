"""Workspace domain sub-server for Sigma MCP."""

from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP

from sigma_mcp.tools.common import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_IDEMPOTENT,
    ANNOTATION_READ_ONLY,
    ANNOTATION_WRITE_SAFE,
    _confirm_required,
    _invalid_request,
    _require_body_fields,
    _require_ids,
    sigma_tool,
)
from sigma_mcp.tools.common import (
    resolve_client as get_client,
)

workspace_server = FastMCP("sigma-workspace", instructions="Workspaces, files, folders, and tags.")


@workspace_server.tool(name="delete_file", annotations=ANNOTATION_DESTRUCTIVE, tags={"workspace", "destructive"})
@sigma_tool
async def sigma_delete_file(inode_id: str, confirm: bool = False) -> str:
    """Delete a file/workbook/data model by its inode ID. Requires confirm=True."""
    if not confirm:
        return _confirm_required("This destructive operation")
    code = await (await get_client()).delete_file(inode_id)
    return json.dumps({"status": code})


delete_file = sigma_delete_file


@workspace_server.tool(name="list_files", annotations=ANNOTATION_READ_ONLY, tags={"workspace", "read_only"})
@sigma_tool
async def sigma_list_files(parent_id: str | None = None, type_filter: str | None = None) -> str:
    """List files/folders. Optionally filter by parentId or type ('workbook', 'folder', 'data-model', 'template')."""
    params: dict[str, Any] = {"limit": 200}
    if parent_id:
        params["parentId"] = parent_id
    if type_filter:
        params["typeFilters"] = type_filter
    return json.dumps(await (await get_client()).list_files(params), indent=2)


list_files = sigma_list_files


@workspace_server.tool(name="create_folder", annotations=ANNOTATION_WRITE_SAFE, tags={"workspace", "mutation"})
@sigma_tool
async def sigma_create_folder(name: str, parent_id: str) -> str:
    """Create a folder."""
    return json.dumps(
        await (await get_client()).create_file({"name": name, "parentId": parent_id, "type": "folder"}), indent=2
    )


create_folder = sigma_create_folder


@workspace_server.tool(name="update_file", annotations=ANNOTATION_WRITE_SAFE, tags={"workspace", "mutation"})
@sigma_tool
async def sigma_update_file(inode_id: str, body: dict[str, Any]) -> str:
    """Update file properties (name, parentId for moving)."""
    return json.dumps(await (await get_client()).update_file(inode_id, body), indent=2)


update_file = sigma_update_file


@workspace_server.tool(name="list_tags", annotations=ANNOTATION_READ_ONLY, tags={"workspace", "read_only"})
@sigma_tool
async def sigma_list_tags(page: str | None = None, limit: int = 200) -> str:
    """List all tags in the organization. Supports pagination via page token."""
    return json.dumps(await (await get_client()).list_tags(page=page, limit=limit), indent=2)


list_tags = sigma_list_tags


@workspace_server.tool(name="create_tag", annotations=ANNOTATION_WRITE_SAFE, tags={"workspace", "mutation"})
@sigma_tool
async def sigma_create_tag(name: str, color: str = "cyan") -> str:
    """Create a new tag (used for version promotion like 'Production', 'Staging').

    color: Tag color, required by the Sigma API. One of: cyan, grass, violet, plum, amber, bronze.
           Defaults to 'cyan'.
    """
    body = {"name": name, "color": color}
    return json.dumps(await (await get_client()).create_tag(body), indent=2)


create_tag = sigma_create_tag


@workspace_server.tool(name="delete_tag", annotations=ANNOTATION_DESTRUCTIVE, tags={"workspace", "destructive"})
@sigma_tool
async def sigma_delete_tag(tag_id: str, confirm: bool = False) -> str:
    """Delete a tag. Requires confirm=True."""
    if not confirm:
        return _confirm_required("This destructive operation")
    code = await (await get_client()).delete_tag(tag_id)
    return json.dumps({"status": code})


delete_tag = sigma_delete_tag


@workspace_server.tool(name="list_workspaces", annotations=ANNOTATION_READ_ONLY, tags={"workspace", "read_only"})
@sigma_tool
async def sigma_list_workspaces(limit: int = 200, page: str | None = None) -> str:
    """List all workspaces. Supports pagination via page token."""
    return json.dumps(await (await get_client()).list_workspaces(limit=limit, page=page), indent=2)


list_workspaces = sigma_list_workspaces


@workspace_server.tool(name="get_workspace", annotations=ANNOTATION_READ_ONLY, tags={"workspace", "read_only"})
@sigma_tool
async def sigma_get_workspace(workspace_id: str) -> str:
    """Get workspace details."""
    return json.dumps(await (await get_client()).get_workspace(workspace_id), indent=2)


get_workspace = sigma_get_workspace


@workspace_server.tool(name="create_workspace", annotations=ANNOTATION_WRITE_SAFE, tags={"workspace", "mutation"})
@sigma_tool
async def sigma_create_workspace(name: str) -> str:
    """Create a new workspace."""
    return json.dumps(await (await get_client()).create_workspace({"name": name}), indent=2)


create_workspace = sigma_create_workspace


@workspace_server.tool(name="delete_workspace", annotations=ANNOTATION_DESTRUCTIVE, tags={"workspace", "destructive"})
@sigma_tool
async def sigma_delete_workspace(workspace_id: str, confirm: bool = False) -> str:
    """Delete a workspace. Requires confirm=True."""
    if not confirm:
        return _confirm_required("This destructive operation")
    code = await (await get_client()).delete_workspace(workspace_id)
    return json.dumps({"status": code})


delete_workspace = sigma_delete_workspace


@workspace_server.tool(name="list_workspace_grants", annotations=ANNOTATION_READ_ONLY, tags={"workspace", "read_only"})
@sigma_tool
async def sigma_list_workspace_grants(workspace_id: str, page: str | None = None, limit: int = 200) -> str:
    """List permission grants on a workspace. Supports pagination via page token."""
    return json.dumps(await (await get_client()).list_workspace_grants(workspace_id, page=page, limit=limit), indent=2)


list_workspace_grants = sigma_list_workspace_grants


@workspace_server.tool(
    name="grant_workspace_access", annotations=ANNOTATION_IDEMPOTENT, tags={"workspace", "idempotent"}
)
@sigma_tool
async def sigma_grant_workspace_access(workspace_id: str, grant_type: str, grantee_id: str, permission: str) -> str:
    """Grant workspace access.

    grant_type: 'member' or 'team'.
    permission: 'view', 'explore', 'organize' (Contribute), or 'edit' (Manage).
    """
    gt = grant_type.strip().lower()
    if gt not in ("member", "team"):
        _invalid_request("grant_type must be 'member' or 'team'")
    grantee_key = "memberId" if gt == "member" else "teamId"
    body = {"grants": [{"grantee": {grantee_key: grantee_id}, "permission": permission}]}
    return json.dumps(await (await get_client()).grant_workspace_access(workspace_id, body), indent=2)


grant_workspace_access = sigma_grant_workspace_access


@workspace_server.tool(
    name="delete_workspace_grant", annotations=ANNOTATION_DESTRUCTIVE, tags={"workspace", "destructive"}
)
@sigma_tool
async def sigma_delete_workspace_grant(workspace_id: str, grant_id: str, confirm: bool = False) -> str:
    """Delete a permission grant from a workspace. DESTRUCTIVE. Requires confirm=True."""
    if not confirm:
        return _confirm_required("This destructive operation")
    code = await (await get_client()).delete_workspace_grant(workspace_id, grant_id)
    return json.dumps({"status": code})


delete_workspace_grant = sigma_delete_workspace_grant


@workspace_server.tool(name="list_all_files", annotations=ANNOTATION_READ_ONLY, tags={"workspace", "read_only"})
@sigma_tool
async def sigma_list_all_files(parent_id: str | None = None, type_filter: str | None = None) -> str:
    """List files/folders in the organization, automatically following pagination.

    parent_id: Optional parent folder ID to list files within.
    type_filter: Optional file type filter ('workbook', 'folder', 'data-model', 'template', 'symlink').
                 Note: Shortcuts/symlinks are excluded by default unless type_filter='symlink'.
    """
    params: dict[str, Any] = {}
    if parent_id:
        params["parentId"] = parent_id
    if type_filter:
        params["typeFilters"] = type_filter
    return json.dumps(await (await get_client()).list_all_files(params or None), indent=2)


list_all_files = sigma_list_all_files


# Tools for client methods that had none (API coverage audit, 2026-10-20).


@workspace_server.tool(name="list_member_files", annotations=ANNOTATION_READ_ONLY, tags={"workspace", "read_only"})
@sigma_tool
async def sigma_list_member_files(member_id: str, limit: int = 200) -> str:
    """List the files (workbooks, data models, folders) a member can access.

    Follows Sigma's pagination to the last page (``limit`` is the page size) and returns
    ``{"entries": [...]}``.
    """
    _require_ids(member_id=member_id)
    return json.dumps(await (await get_client()).list_member_files(member_id, limit), indent=2)


list_member_files = sigma_list_member_files


@workspace_server.tool(name="list_recent_files", annotations=ANNOTATION_READ_ONLY, tags={"workspace", "read_only"})
@sigma_tool
async def sigma_list_recent_files(member_id: str) -> str:
    """List the files a member opened recently.

    Follows Sigma's pagination to the last page and returns ``{"entries": [...]}``.
    """
    _require_ids(member_id=member_id)
    return json.dumps(await (await get_client()).list_recent_files(member_id), indent=2)


list_recent_files = sigma_list_recent_files


@workspace_server.tool(name="get_file", annotations=ANNOTATION_READ_ONLY, tags={"workspace", "read_only"})
@sigma_tool
async def sigma_get_file(inode_id: str) -> str:
    """Get a file's details (type, name, path, owner) by its inode ID."""
    _require_ids(inode_id=inode_id)
    return json.dumps(await (await get_client()).get_file(inode_id), indent=2)


get_file = sigma_get_file


@workspace_server.tool(name="update_tag", annotations=ANNOTATION_WRITE_SAFE, tags={"workspace", "mutation"})
@sigma_tool
async def sigma_update_tag(tag_id: str, body: dict[str, Any]) -> str:
    """Update a version tag.

    body may set name, description and color.
    """
    _require_ids(tag_id=tag_id)
    return json.dumps(await (await get_client()).update_tag(tag_id, body), indent=2)


update_tag = sigma_update_tag


@workspace_server.tool(name="list_workbooks_for_tag", annotations=ANNOTATION_READ_ONLY, tags={"workspace", "read_only"})
@sigma_tool
async def sigma_list_workbooks_for_tag(tag_id: str) -> str:
    """List the workbooks carrying a version tag.

    Follows Sigma's pagination to the last page and returns ``{"entries": [...]}``.
    """
    _require_ids(tag_id=tag_id)
    return json.dumps(await (await get_client()).list_workbooks_for_tag(tag_id), indent=2)


list_workbooks_for_tag = sigma_list_workbooks_for_tag


@workspace_server.tool(name="update_workspace", annotations=ANNOTATION_WRITE_SAFE, tags={"workspace", "mutation"})
@sigma_tool
async def sigma_update_workspace(workspace_id: str, body: dict[str, Any]) -> str:
    """Update (rename) a workspace.

    body requires name and may set noDuplicates.
    """
    _require_ids(workspace_id=workspace_id)
    _require_body_fields(body, "name")
    return json.dumps(await (await get_client()).update_workspace(workspace_id, body), indent=2)


update_workspace = sigma_update_workspace


@workspace_server.tool(name="favorite_file", annotations=ANNOTATION_WRITE_SAFE, tags={"workspace", "mutation"})
@sigma_tool
async def sigma_favorite_file(member_id: str, inode_id: str) -> str:
    """Add a document to a member's favorites."""
    _require_ids(member_id=member_id, inode_id=inode_id)
    return json.dumps(await (await get_client()).favorite(member_id, inode_id), indent=2)


favorite_file = sigma_favorite_file


@workspace_server.tool(name="unfavorite_file", annotations=ANNOTATION_DESTRUCTIVE, tags={"workspace", "destructive"})
@sigma_tool
async def sigma_unfavorite_file(member_id: str, inode_id: str, confirm: bool = False) -> str:
    """Remove a document from a member's favorites. Requires confirm=True."""
    if not confirm:
        return _confirm_required("This destructive operation")
    _require_ids(member_id=member_id, inode_id=inode_id)
    code = await (await get_client()).unfavorite(member_id, inode_id)
    return json.dumps({"status": "deleted", "statusCode": code})


unfavorite_file = sigma_unfavorite_file


@workspace_server.tool(
    name="list_favorites_for_member", annotations=ANNOTATION_READ_ONLY, tags={"workspace", "read_only"}
)
@sigma_tool
async def sigma_list_favorites_for_member(member_id: str) -> str:
    """List a member's favorite documents.

    Follows Sigma's pagination to the last page and returns ``{"entries": [...]}``.
    """
    _require_ids(member_id=member_id)
    return json.dumps(await (await get_client()).list_favorites_for_member(member_id), indent=2)


list_favorites_for_member = sigma_list_favorites_for_member
