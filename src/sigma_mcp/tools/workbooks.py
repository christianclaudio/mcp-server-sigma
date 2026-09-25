"""Workbooks domain sub-server for Sigma MCP."""

from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP

from sigma_mcp.tools.common import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_IDEMPOTENT,
    ANNOTATION_READ_ONLY,
    ANNOTATION_WRITE_SAFE,
    _invalid_request,
    _summarize_list,
    sigma_tool,
)
from sigma_mcp.tools.common import (
    resolve_client as get_client,
)

workbooks_server = FastMCP(
    "sigma-workbooks", instructions="Workbooks, templates, reports, embeds, exports, bookmarks, and schedules."
)


@workbooks_server.tool(name="sigma_list_workbooks", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"})
@sigma_tool
async def sigma_list_workbooks(limit: int = 200, summary_only: bool = False) -> str:
    """List all workbooks in the organization. Pass summary_only=True for concise token-efficient response."""
    data = await (await get_client()).list_workbooks(limit)
    if summary_only:
        data = _summarize_list(data, ["workbookId", "name", "folderId", "ownerId"])
    return json.dumps(data, indent=2)


list_workbooks = sigma_list_workbooks


@workbooks_server.tool(name="sigma_get_workbook", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"})
@sigma_tool
async def sigma_get_workbook(workbook_id: str) -> str:
    """Get workbook metadata."""
    return json.dumps(await (await get_client()).get_workbook(workbook_id), indent=2)


get_workbook = sigma_get_workbook


@workbooks_server.tool(name="sigma_create_workbook", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"})
@sigma_tool
async def sigma_create_workbook(name: str, folder_id: str, description: str = "") -> str:
    """Create an empty workbook in a folder."""
    return json.dumps(await (await get_client()).create_workbook(name, folder_id, description), indent=2)


create_workbook = sigma_create_workbook


@workbooks_server.tool(
    name="sigma_duplicate_workbook", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"}
)
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


duplicate_workbook = sigma_duplicate_workbook


@workbooks_server.tool(
    name="sigma_list_workbook_pages", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"}
)
@sigma_tool
async def sigma_list_workbook_pages(workbook_id: str) -> str:
    """List pages in a workbook."""
    return json.dumps(await (await get_client()).list_workbook_pages(workbook_id), indent=2)


list_workbook_pages = sigma_list_workbook_pages


@workbooks_server.tool(
    name="sigma_list_workbook_lineage", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"}
)
@sigma_tool
async def sigma_list_workbook_lineage(workbook_id: str) -> str:
    """List data lineage for a workbook."""
    return json.dumps(await (await get_client()).list_workbook_lineage(workbook_id), indent=2)


list_workbook_lineage = sigma_list_workbook_lineage


@workbooks_server.tool(
    name="sigma_get_workbook_version_history", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"}
)
@sigma_tool
async def sigma_get_workbook_version_history(workbook_id: str) -> str:
    """Get version history for a workbook."""
    return json.dumps(await (await get_client()).get_workbook_version_history(workbook_id), indent=2)


get_workbook_version_history = sigma_get_workbook_version_history


@workbooks_server.tool(
    name="sigma_restore_workbook_version", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"}
)
@sigma_tool
async def sigma_restore_workbook_version(workbook_id: str, version: int) -> str:
    """Restore a workbook to a previous version."""
    return json.dumps(await (await get_client()).restore_workbook_version(workbook_id, version), indent=2)


restore_workbook_version = sigma_restore_workbook_version


@workbooks_server.tool(
    name="sigma_convert_workbook_to_report", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"}
)
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


convert_workbook_to_report = sigma_convert_workbook_to_report


@workbooks_server.tool(
    name="sigma_list_workbook_grants", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"}
)
@sigma_tool
async def sigma_list_workbook_grants(workbook_id: str) -> str:
    """List permission grants on a workbook."""
    return json.dumps(await (await get_client()).list_workbook_grants(workbook_id), indent=2)


list_workbook_grants = sigma_list_workbook_grants


@workbooks_server.tool(
    name="sigma_grant_workbook_access", annotations=ANNOTATION_IDEMPOTENT, tags={"workbooks", "idempotent"}
)
@sigma_tool
async def sigma_grant_workbook_access(workbook_id: str, grant_type: str, grantee_id: str, permission: str) -> str:
    """Grant access to a workbook. grant_type: 'member' or 'team'. permission: 'view', 'explore', 'edit'."""
    grantee_key = "memberId" if grant_type.lower() == "member" else "teamId"
    body = {"grants": [{"grantee": {grantee_key: grantee_id}, "permission": permission}]}
    return json.dumps(await (await get_client()).grant_workbook_access(workbook_id, body), indent=2)


grant_workbook_access = sigma_grant_workbook_access


@workbooks_server.tool(
    name="sigma_list_workbook_embeds", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"}
)
@sigma_tool
async def sigma_list_workbook_embeds(workbook_id: str) -> str:
    """List embed configurations for a workbook."""
    return json.dumps(await (await get_client()).list_workbook_embeds(workbook_id), indent=2)


list_workbook_embeds = sigma_list_workbook_embeds


@workbooks_server.tool(
    name="sigma_create_workbook_embed", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"}
)
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


create_workbook_embed = sigma_create_workbook_embed


@workbooks_server.tool(name="sigma_export_workbook", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"})
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


export_workbook = sigma_export_workbook


@workbooks_server.tool(
    name="sigma_list_workbook_schedules", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"}
)
@sigma_tool
async def sigma_list_workbook_schedules(workbook_id: str) -> str:
    """List scheduled exports for a workbook."""
    return json.dumps(await (await get_client()).list_workbook_schedules(workbook_id), indent=2)


list_workbook_schedules = sigma_list_workbook_schedules


@workbooks_server.tool(
    name="sigma_add_workbook_schedule", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"}
)
@sigma_tool
async def sigma_add_workbook_schedule(workbook_id: str, body: dict[str, Any]) -> str:
    """Create a scheduled export for a workbook. See Sigma docs for schedule body schema."""
    return json.dumps(await (await get_client()).add_workbook_schedule(workbook_id, body), indent=2)


add_workbook_schedule = sigma_add_workbook_schedule


@workbooks_server.tool(
    name="sigma_delete_workbook_schedule", annotations=ANNOTATION_DESTRUCTIVE, tags={"workbooks", "destructive"}
)
@sigma_tool
async def sigma_delete_workbook_schedule(workbook_id: str, schedule_id: str, confirm: bool = False) -> str:
    """Delete a scheduled export. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).delete_workbook_schedule(workbook_id, schedule_id)
    return json.dumps({"status": code})


delete_workbook_schedule = sigma_delete_workbook_schedule


@workbooks_server.tool(
    name="sigma_list_workbook_bookmarks", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"}
)
@sigma_tool
async def sigma_list_workbook_bookmarks(workbook_id: str) -> str:
    """List bookmarks (saved filter states) in a workbook."""
    return json.dumps(await (await get_client()).list_workbook_bookmarks(workbook_id), indent=2)


list_workbook_bookmarks = sigma_list_workbook_bookmarks


@workbooks_server.tool(
    name="sigma_add_workbook_bookmark", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"}
)
@sigma_tool
async def sigma_add_workbook_bookmark(workbook_id: str, body: dict[str, Any]) -> str:
    """Add a bookmark to a workbook."""
    return json.dumps(await (await get_client()).add_workbook_bookmark(workbook_id, body), indent=2)


add_workbook_bookmark = sigma_add_workbook_bookmark


@workbooks_server.tool(
    name="sigma_get_workbook_tags", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"}
)
@sigma_tool
async def sigma_get_workbook_tags(workbook_id: str) -> str:
    """List tags on a workbook."""
    return json.dumps(await (await get_client()).get_workbook_tags(workbook_id), indent=2)


get_workbook_tags = sigma_get_workbook_tags


@workbooks_server.tool(name="sigma_tag_workbook", annotations=ANNOTATION_IDEMPOTENT, tags={"workbooks", "idempotent"})
@sigma_tool
async def sigma_tag_workbook(workbook_id: str, tag_name: str) -> str:
    """Apply a version tag to a workbook by tag NAME (e.g. 'Production'). The Sigma API takes the tag name here, not its ID."""
    return json.dumps(await (await get_client()).tag_workbook(workbook_id, tag_name), indent=2)


tag_workbook = sigma_tag_workbook


@workbooks_server.tool(
    name="sigma_remove_workbook_tag", annotations=ANNOTATION_DESTRUCTIVE, tags={"workbooks", "destructive"}
)
@sigma_tool
async def sigma_remove_workbook_tag(workbook_id: str, tag_id: str, confirm: bool = False) -> str:
    """Remove a tag from a workbook. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).remove_workbook_tag(workbook_id, tag_id)
    return json.dumps({"status": code})


remove_workbook_tag = sigma_remove_workbook_tag


@workbooks_server.tool(name="sigma_list_templates", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"})
@sigma_tool
async def sigma_list_templates(limit: int = 200) -> str:
    """List all templates in the organization."""
    return json.dumps(await (await get_client()).list_templates(limit), indent=2)


list_templates = sigma_list_templates


@workbooks_server.tool(name="sigma_get_template", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"})
@sigma_tool
async def sigma_get_template(template_id: str) -> str:
    """Get template details."""
    return json.dumps(await (await get_client()).get_template(template_id), indent=2)


get_template = sigma_get_template


@workbooks_server.tool(
    name="sigma_create_workbook_from_template", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"}
)
@sigma_tool
async def sigma_create_workbook_from_template(template_id: str, folder_id: str, name: str | None = None) -> str:
    """Create a workbook from a template. This brings real visuals — the only programmatic way to get charts/tables."""
    return json.dumps(await (await get_client()).save_workbook_from_template(template_id, folder_id, name), indent=2)


create_workbook_from_template = sigma_create_workbook_from_template


@workbooks_server.tool(
    name="sigma_save_template_from_workbook", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"}
)
@sigma_tool
async def sigma_save_template_from_workbook(workbook_id: str, folder_id: str, name: str | None = None) -> str:
    """Save a workbook as a reusable template."""
    body: dict[str, Any] = {"folderId": folder_id}
    if name:
        body["name"] = name
    return json.dumps(await (await get_client()).save_template_from_workbook(workbook_id, body), indent=2)


save_template_from_workbook = sigma_save_template_from_workbook


@workbooks_server.tool(name="sigma_list_reports", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"})
@sigma_tool
async def sigma_list_reports(limit: int = 200) -> str:
    """List all reports in the organization."""
    return json.dumps(await (await get_client()).list_reports(limit), indent=2)


list_reports = sigma_list_reports


@workbooks_server.tool(name="sigma_get_report", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"})
@sigma_tool
async def sigma_get_report(report_id: str) -> str:
    """Get report metadata."""
    return json.dumps(await (await get_client()).get_report(report_id), indent=2)


get_report = sigma_get_report


@workbooks_server.tool(name="sigma_create_report", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"})
@sigma_tool
async def sigma_create_report(body: dict[str, Any]) -> str:
    """Create a report."""
    return json.dumps(await (await get_client()).create_report(body), indent=2)


create_report = sigma_create_report


@workbooks_server.tool(name="sigma_duplicate_report", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"})
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


duplicate_report = sigma_duplicate_report


@workbooks_server.tool(
    name="sigma_list_report_sources", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"}
)
@sigma_tool
async def sigma_list_report_sources(report_id: str) -> str:
    """List data sources used by a report."""
    return json.dumps(await (await get_client()).list_report_sources(report_id), indent=2)


list_report_sources = sigma_list_report_sources


@workbooks_server.tool(
    name="sigma_list_report_elements", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"}
)
@sigma_tool
async def sigma_list_report_elements(report_id: str) -> str:
    """List elements in a report."""
    return json.dumps(await (await get_client()).list_report_elements(report_id), indent=2)


list_report_elements = sigma_list_report_elements


@workbooks_server.tool(
    name="sigma_list_report_queries", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"}
)
@sigma_tool
async def sigma_list_report_queries(report_id: str) -> str:
    """List SQL queries in a report."""
    return json.dumps(await (await get_client()).list_report_queries(report_id), indent=2)


list_report_queries = sigma_list_report_queries


@workbooks_server.tool(
    name="sigma_list_report_lineage", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"}
)
@sigma_tool
async def sigma_list_report_lineage(report_id: str) -> str:
    """List lineage for a report."""
    return json.dumps(await (await get_client()).list_report_lineage(report_id), indent=2)


list_report_lineage = sigma_list_report_lineage


@workbooks_server.tool(name="sigma_export_report", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"})
@sigma_tool
async def sigma_export_report(report_id: str, format: str = "pdf") -> str:
    """Export a report. format: 'pdf', 'png', 'csv', 'xlsx'."""
    return json.dumps(await (await get_client()).export_report(report_id, {"format": format}), indent=2)


export_report = sigma_export_report


@workbooks_server.tool(
    name="sigma_list_report_schedules", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"}
)
@sigma_tool
async def sigma_list_report_schedules(report_id: str) -> str:
    """List scheduled exports for a report."""
    return json.dumps(await (await get_client()).list_report_schedules(report_id), indent=2)


list_report_schedules = sigma_list_report_schedules


@workbooks_server.tool(
    name="sigma_create_report_schedule", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"}
)
@sigma_tool
async def sigma_create_report_schedule(report_id: str, body: dict[str, Any]) -> str:
    """Create a scheduled export for a report."""
    return json.dumps(await (await get_client()).create_report_schedule(report_id, body), indent=2)


create_report_schedule = sigma_create_report_schedule


@workbooks_server.tool(
    name="sigma_list_shared_templates", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"}
)
@sigma_tool
async def sigma_list_shared_templates() -> str:
    """List templates shared with your organization from other orgs."""
    return json.dumps(await (await get_client()).list_shared_templates(), indent=2)


list_shared_templates = sigma_list_shared_templates


@workbooks_server.tool(
    name="sigma_accept_shared_template", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"}
)
@sigma_tool
async def sigma_accept_shared_template(share_id: str) -> str:
    """Accept a pending template share from another organization."""
    return json.dumps(await (await get_client()).accept_shared_template(share_id), indent=2)


accept_shared_template = sigma_accept_shared_template


@workbooks_server.tool(
    name="sigma_copy_workbook_to_member", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"}
)
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


copy_workbook_to_member = sigma_copy_workbook_to_member


@workbooks_server.tool(
    name="sigma_deploy_template_to_folder", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"}
)
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


deploy_template_to_folder = sigma_deploy_template_to_folder


@workbooks_server.tool(name="sigma_promote_workbook", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"})
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


promote_workbook = sigma_promote_workbook


@workbooks_server.tool(
    name="sigma_export_and_download", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"}
)
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

    if not workbook_id or not workbook_id.strip():
        return _invalid_request("workbook_id is required")

    from sigma_mcp import server as _srv

    c = await _srv.get_client()
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

    deadline = _srv.time.time() + timeout_seconds
    backoff = 2.0
    while _srv.time.time() < deadline:
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
        await _srv.asyncio.sleep(backoff)
        backoff = min(backoff * 1.5, 15.0)

    return json.dumps({"error": "timeout", "query_id": query_id, "timeout_seconds": timeout_seconds})


export_and_download = sigma_export_and_download


@workbooks_server.tool(
    name="sigma_reassign_workbook_ownership", annotations=ANNOTATION_WRITE_SAFE, tags={"workbooks", "mutation"}
)
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


reassign_workbook_ownership = sigma_reassign_workbook_ownership


@workbooks_server.tool(
    name="sigma_list_workbooks_shared_with_member", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"}
)
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


list_workbooks_shared_with_member = sigma_list_workbooks_shared_with_member


@workbooks_server.tool(
    name="sigma_list_all_workbooks", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"}
)
@sigma_tool
async def sigma_list_all_workbooks() -> str:
    """List ALL workbooks in the organization, automatically following pagination."""
    return json.dumps(await (await get_client()).list_all_workbooks(), indent=2)


list_all_workbooks = sigma_list_all_workbooks


@workbooks_server.tool(name="sigma_list_all_reports", annotations=ANNOTATION_READ_ONLY, tags={"workbooks", "read_only"})
@sigma_tool
async def sigma_list_all_reports() -> str:
    """List ALL reports in the organization, automatically following pagination."""
    return json.dumps(await (await get_client()).list_all_reports(), indent=2)


list_all_reports = sigma_list_all_reports
