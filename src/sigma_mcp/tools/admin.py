"""Admin domain sub-server for Sigma MCP."""

from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP

from sigma_mcp.middleware import AdminDomainGuardMiddleware
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
from sigma_mcp.webhooks import get_recent_webhooks

admin_server = FastMCP(
    "sigma-admin", instructions="Members, teams, account types, tenants, user attributes, deployments, and connectors."
)
admin_server.add_middleware(AdminDomainGuardMiddleware())


@admin_server.tool(name="sigma_list_deployments", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_list_deployments() -> str:
    """List all deployment policies."""
    return json.dumps(await (await get_client()).list_deployments(), indent=2)


list_deployments = sigma_list_deployments


@admin_server.tool(name="sigma_get_deployment", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_get_deployment(policy_id: str) -> str:
    """Get a deployment policy."""
    return json.dumps(await (await get_client()).get_deployment(policy_id), indent=2)


get_deployment = sigma_get_deployment


@admin_server.tool(name="sigma_create_deployment", annotations=ANNOTATION_WRITE_SAFE, tags={"admin", "mutation"})
@sigma_tool
async def sigma_create_deployment(name: str, body: dict[str, Any]) -> str:
    """Create a deployment policy."""
    body["name"] = name
    return json.dumps(await (await get_client()).create_deployment(body), indent=2)


create_deployment = sigma_create_deployment


@admin_server.tool(name="sigma_archive_deployment", annotations=ANNOTATION_DESTRUCTIVE, tags={"admin", "destructive"})
@sigma_tool
async def sigma_archive_deployment(policy_id: str, confirm: bool = False) -> str:
    """Delete (archive) a deployment policy. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    result = await (await get_client()).delete_deployment(policy_id)
    return json.dumps({"status": "deleted", "statusCode": result})


archive_deployment = sigma_archive_deployment


@admin_server.tool(name="sigma_deactivate_member", annotations=ANNOTATION_DESTRUCTIVE, tags={"admin", "destructive"})
@sigma_tool
async def sigma_deactivate_member(member_id: str, confirm: bool = False) -> str:
    """Deactivate a member. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    return json.dumps(await (await get_client()).deactivate_member(member_id), indent=2)


deactivate_member = sigma_deactivate_member


@admin_server.tool(
    name="sigma_list_deployment_documents", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"}
)
@sigma_tool
async def sigma_list_deployment_documents(policy_id: str) -> str:
    """List documents in a deployment policy."""
    return json.dumps(await (await get_client()).list_deployment_documents(policy_id), indent=2)


list_deployment_documents = sigma_list_deployment_documents


@admin_server.tool(name="sigma_add_deployment_documents", annotations=ANNOTATION_WRITE_SAFE, tags={"admin", "mutation"})
@sigma_tool
async def sigma_add_deployment_documents(policy_id: str, inode_ids: list[str]) -> str:
    """Add workbooks/reports to a deployment policy."""
    return json.dumps(await (await get_client()).add_deployment_documents(policy_id, {"inodeIds": inode_ids}), indent=2)


add_deployment_documents = sigma_add_deployment_documents


@admin_server.tool(name="sigma_list_tenants", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_list_tenants() -> str:
    """List all tenant organizations (for multi-tenant deployments)."""
    return json.dumps(await (await get_client()).list_tenants(), indent=2)


list_tenants = sigma_list_tenants


@admin_server.tool(name="sigma_get_tenant", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_get_tenant(tenant_id: str) -> str:
    """Get tenant organization details."""
    return json.dumps(await (await get_client()).get_tenant(tenant_id), indent=2)


get_tenant = sigma_get_tenant


@admin_server.tool(name="sigma_create_tenant", annotations=ANNOTATION_WRITE_SAFE, tags={"admin", "mutation"})
@sigma_tool
async def sigma_create_tenant(name: str, body: dict[str, Any] | None = None) -> str:
    """Create a tenant organization."""
    data = body or {}
    data["name"] = name
    return json.dumps(await (await get_client()).create_tenant(data), indent=2)


create_tenant = sigma_create_tenant


@admin_server.tool(name="sigma_list_api_connectors", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_list_api_connectors() -> str:
    """List all API connectors (custom data integrations)."""
    return json.dumps(await (await get_client()).list_api_connectors(), indent=2)


list_api_connectors = sigma_list_api_connectors


@admin_server.tool(name="sigma_get_api_connector", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_get_api_connector(connector_id: str) -> str:
    """Get details for an API connector."""
    return json.dumps(await (await get_client()).get_api_connector(connector_id), indent=2)


get_api_connector = sigma_get_api_connector


@admin_server.tool(name="sigma_list_members", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_list_members(limit: int = 200, summary_only: bool = False) -> str:
    """List all members in the organization. Pass summary_only=True for concise token-efficient response."""
    data = await (await get_client()).list_members(limit)
    if summary_only:
        data = _summarize_list(data, ["memberId", "email", "firstName", "lastName", "memberType"])
    return json.dumps(data, indent=2)


list_members = sigma_list_members


@admin_server.tool(name="sigma_get_member", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_get_member(member_id: str) -> str:
    """Get member details including homeFolderId."""
    return json.dumps(await (await get_client()).get_member(member_id), indent=2)


get_member = sigma_get_member


@admin_server.tool(name="sigma_create_member", annotations=ANNOTATION_WRITE_SAFE, tags={"admin", "mutation"})
@sigma_tool
async def sigma_create_member(email: str, first_name: str, last_name: str, member_type: str = "viewer") -> str:
    """Create a new member. member_type: 'admin', 'creator', 'viewer'."""
    return json.dumps(
        await (await get_client()).create_member(
            {"email": email, "firstName": first_name, "lastName": last_name, "memberType": member_type}
        ),
        indent=2,
    )


create_member = sigma_create_member


@admin_server.tool(name="sigma_update_member", annotations=ANNOTATION_WRITE_SAFE, tags={"admin", "mutation"})
@sigma_tool
async def sigma_update_member(member_id: str, body: dict[str, Any]) -> str:
    """Update member properties (firstName, lastName, memberType, isActive, etc.)."""
    return json.dumps(await (await get_client()).update_member(member_id, body), indent=2)


update_member = sigma_update_member


@admin_server.tool(name="sigma_get_current_user", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_get_current_user() -> str:
    """Get the current authenticated user's details."""
    return json.dumps(await (await get_client()).get_current_user(), indent=2)


get_current_user = sigma_get_current_user


@admin_server.tool(name="sigma_list_member_teams", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_list_member_teams(member_id: str) -> str:
    """List teams a member belongs to."""
    return json.dumps(await (await get_client()).list_member_teams(member_id), indent=2)


list_member_teams = sigma_list_member_teams


@admin_server.tool(name="sigma_list_teams", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_list_teams(limit: int = 200, summary_only: bool = False) -> str:
    """List all teams. Pass summary_only=True for concise token-efficient response."""
    data = await (await get_client()).list_teams(limit)
    if summary_only:
        data = _summarize_list(data, ["teamId", "name", "description"])
    return json.dumps(data, indent=2)


list_teams = sigma_list_teams


@admin_server.tool(name="sigma_get_team", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_get_team(team_id: str) -> str:
    """Get team details."""
    return json.dumps(await (await get_client()).get_team(team_id), indent=2)


get_team = sigma_get_team


@admin_server.tool(name="sigma_create_team", annotations=ANNOTATION_WRITE_SAFE, tags={"admin", "mutation"})
@sigma_tool
async def sigma_create_team(name: str, description: str = "") -> str:
    """Create a new team."""
    return json.dumps(await (await get_client()).create_team({"name": name, "description": description}), indent=2)


create_team = sigma_create_team


@admin_server.tool(name="sigma_delete_team", annotations=ANNOTATION_DESTRUCTIVE, tags={"admin", "destructive"})
@sigma_tool
async def sigma_delete_team(team_id: str, confirm: bool = False) -> str:
    """Delete a team. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).delete_team(team_id)
    return json.dumps({"status": code})


delete_team = sigma_delete_team


@admin_server.tool(name="sigma_list_team_members", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_list_team_members(team_id: str) -> str:
    """List members of a team."""
    return json.dumps(await (await get_client()).list_team_members(team_id), indent=2)


list_team_members = sigma_list_team_members


@admin_server.tool(name="sigma_update_team_members", annotations=ANNOTATION_WRITE_SAFE, tags={"admin", "mutation"})
@sigma_tool
async def sigma_update_team_members(team_id: str, add: list[str] | None = None, remove: list[str] | None = None) -> str:
    """Add or remove members from a team. Provide lists of member IDs."""
    body: dict[str, Any] = {}
    if add:
        body["add"] = add
    if remove:
        body["remove"] = remove
    return json.dumps(await (await get_client()).update_team_members(team_id, body), indent=2)


update_team_members = sigma_update_team_members


@admin_server.tool(name="sigma_list_user_attributes", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_list_user_attributes() -> str:
    """List all user attributes (used for row-level security and dynamic parameters)."""
    return json.dumps(await (await get_client()).list_user_attributes(), indent=2)


list_user_attributes = sigma_list_user_attributes


@admin_server.tool(name="sigma_create_user_attribute", annotations=ANNOTATION_WRITE_SAFE, tags={"admin", "mutation"})
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


create_user_attribute = sigma_create_user_attribute


@admin_server.tool(
    name="sigma_set_user_attribute_for_teams", annotations=ANNOTATION_IDEMPOTENT, tags={"admin", "idempotent"}
)
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


set_user_attribute_for_teams = sigma_set_user_attribute_for_teams


@admin_server.tool(
    name="sigma_set_user_attribute_for_tenants", annotations=ANNOTATION_IDEMPOTENT, tags={"admin", "idempotent"}
)
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


set_user_attribute_for_tenants = sigma_set_user_attribute_for_tenants


@admin_server.tool(name="sigma_get_user_attribute_users", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_get_user_attribute_users(attribute_id: str) -> str:
    """Get all user assignments for a user attribute."""
    return json.dumps(await (await get_client()).get_user_attribute_user_assignments(attribute_id), indent=2)


get_user_attribute_users = sigma_get_user_attribute_users


@admin_server.tool(name="sigma_get_user_attribute_teams", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_get_user_attribute_teams(attribute_id: str) -> str:
    """Get all team assignments for a user attribute."""
    return json.dumps(await (await get_client()).get_user_attribute_team_assignments(attribute_id), indent=2)


get_user_attribute_teams = sigma_get_user_attribute_teams


@admin_server.tool(
    name="sigma_get_user_attribute_tenants", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"}
)
@sigma_tool
async def sigma_get_user_attribute_tenants(attribute_id: str) -> str:
    """Get all tenant assignments for a user attribute."""
    return json.dumps(await (await get_client()).get_user_attribute_tenant_assignments(attribute_id), indent=2)


get_user_attribute_tenants = sigma_get_user_attribute_tenants


@admin_server.tool(
    name="sigma_update_user_attribute_for_users", annotations=ANNOTATION_DESTRUCTIVE, tags={"admin", "destructive"}
)
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


update_user_attribute_for_users = sigma_update_user_attribute_for_users


@admin_server.tool(
    name="sigma_update_user_attribute_for_teams", annotations=ANNOTATION_DESTRUCTIVE, tags={"admin", "destructive"}
)
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


update_user_attribute_for_teams = sigma_update_user_attribute_for_teams


@admin_server.tool(
    name="sigma_update_user_attribute_for_tenants", annotations=ANNOTATION_DESTRUCTIVE, tags={"admin", "destructive"}
)
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


update_user_attribute_for_tenants = sigma_update_user_attribute_for_tenants


@admin_server.tool(
    name="sigma_delete_user_attribute_for_user", annotations=ANNOTATION_DESTRUCTIVE, tags={"admin", "destructive"}
)
@sigma_tool
async def sigma_delete_user_attribute_for_user(attribute_id: str, user_id: str, confirm: bool = False) -> str:
    """Delete a user attribute assignment for a specific user. DESTRUCTIVE. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).delete_user_attribute_for_user(attribute_id, user_id)
    return json.dumps({"status": code})


delete_user_attribute_for_user = sigma_delete_user_attribute_for_user


@admin_server.tool(
    name="sigma_delete_user_attribute_for_team", annotations=ANNOTATION_DESTRUCTIVE, tags={"admin", "destructive"}
)
@sigma_tool
async def sigma_delete_user_attribute_for_team(attribute_id: str, team_id: str, confirm: bool = False) -> str:
    """Delete a user attribute assignment for a specific team. DESTRUCTIVE. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).delete_user_attribute_for_team(attribute_id, team_id)
    return json.dumps({"status": code})


delete_user_attribute_for_team = sigma_delete_user_attribute_for_team


@admin_server.tool(
    name="sigma_delete_user_attribute_for_tenant", annotations=ANNOTATION_DESTRUCTIVE, tags={"admin", "destructive"}
)
@sigma_tool
async def sigma_delete_user_attribute_for_tenant(attribute_id: str, tenant_org_id: str, confirm: bool = False) -> str:
    """Delete a user attribute assignment for a specific tenant. DESTRUCTIVE. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).delete_user_attribute_for_tenant(attribute_id, tenant_org_id)
    return json.dumps({"status": code})


delete_user_attribute_for_tenant = sigma_delete_user_attribute_for_tenant


@admin_server.tool(name="sigma_list_account_types", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_list_account_types() -> str:
    """List all account types (license types) in the organization."""
    return json.dumps(await (await get_client()).list_account_types(), indent=2)


list_account_types = sigma_list_account_types


@admin_server.tool(name="sigma_list_grants", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_list_grants(inode_id: str) -> str:
    """List grants for a specific file/workbook/data model by inodeId. Required: inodeId."""
    return json.dumps(await (await get_client()).list_grants({"inodeId": inode_id, "limit": 200}), indent=2)


list_grants = sigma_list_grants


@admin_server.tool(name="sigma_create_grant", annotations=ANNOTATION_IDEMPOTENT, tags={"admin", "idempotent"})
@sigma_tool
async def sigma_create_grant(body: dict[str, Any]) -> str:
    """Create or update a grant. Body must include inodeId, granteeId, permission, type."""
    return json.dumps(await (await get_client()).create_or_update_grant(body), indent=2)


create_grant = sigma_create_grant


@admin_server.tool(name="sigma_list_translations", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_list_translations() -> str:
    """List organization translation files."""
    return json.dumps(await (await get_client()).list_translations(), indent=2)


list_translations = sigma_list_translations


@admin_server.tool(name="sigma_onboard_member", annotations=ANNOTATION_WRITE_SAFE, tags={"admin", "mutation"})
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


onboard_member = sigma_onboard_member


@admin_server.tool(name="sigma_bulk_assign_team_members", annotations=ANNOTATION_WRITE_SAFE, tags={"admin", "mutation"})
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


bulk_assign_team_members = sigma_bulk_assign_team_members


@admin_server.tool(
    name="sigma_bulk_deactivate_members", annotations=ANNOTATION_DESTRUCTIVE, tags={"admin", "destructive"}
)
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


bulk_deactivate_members = sigma_bulk_deactivate_members


@admin_server.tool(name="sigma_change_member_email", annotations=ANNOTATION_WRITE_SAFE, tags={"admin", "mutation"})
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


change_member_email = sigma_change_member_email


@admin_server.tool(
    name="sigma_bulk_remove_team_members", annotations=ANNOTATION_DESTRUCTIVE, tags={"admin", "destructive"}
)
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


bulk_remove_team_members = sigma_bulk_remove_team_members


@admin_server.tool(name="sigma_list_tenants_paginated", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
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


list_tenants_paginated = sigma_list_tenants_paginated


@admin_server.tool(name="sigma_get_tenant_scoped_info", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
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


get_tenant_scoped_info = sigma_get_tenant_scoped_info


@admin_server.tool(name="sigma_api_capabilities", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_api_capabilities() -> str:
    """Describe what the Sigma REST API can and cannot do programmatically."""
    return json.dumps(
        {
            "supported": {
                "data_models_as_code": "Create/update full semantic layers (tables, calculated columns, metrics) from JSON",
                "template_instantiation": "Create workbooks from templates — the primary way to get charts/tables programmatically (or use the Beta /v2/workbooks/spec endpoints for code representation)",
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
                "workbook_as_code": "Supported by this server via template instantiation; see the Beta /v2/workbooks/spec endpoints for raw layout representation.",
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
                "regional_base_url": "Ensure you are using your organization's specific regional base URL (e.g. do not use the default aws-api host if your organization is hosted on aws-us-east).",
                "new_schemas": "Call connection sync before referencing newly created warehouse schemas.",
                "swap_source_body": "connectionMapping uses fromId/toId and paths[].fromPath/toPath — not source/target.",
            },
        },
        indent=2,
    )


api_capabilities = sigma_api_capabilities


@admin_server.tool(name="sigma_list_all_members", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_list_all_members() -> str:
    """List ALL members in the organization, automatically following pagination."""
    return json.dumps(await (await get_client()).list_all_members(), indent=2)


list_all_members = sigma_list_all_members


@admin_server.tool(name="sigma_list_all_teams", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_list_all_teams() -> str:
    """List ALL teams in the organization, automatically following pagination."""
    return json.dumps(await (await get_client()).list_all_teams(), indent=2)


list_all_teams = sigma_list_all_teams


@admin_server.tool(name="sigma_list_recent_webhooks", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_list_recent_webhooks(limit: int = 20, event_type: str | None = None) -> str:
    """List recently recorded incoming Sigma webhook events.

    read_only_hint: True
    """
    events = get_recent_webhooks(limit=limit, event_type=event_type)
    return json.dumps({"count": len(events), "events": events}, indent=2)


list_recent_webhooks = sigma_list_recent_webhooks


VALID_ORG_SETTINGS = frozenset(
    {
        "aiChatHistory",
        "auditLogging",
        "bulkCopy",
        "comments",
        "csvUpload",
        "emailBranding",
        "licenseUpgradeRequests",
        "publicEmbeds",
        "sampleConnections",
        "timezone",
    }
)


@admin_server.tool(name="sigma_list_org_workbook_agents", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_list_org_workbook_agents(page_token: str | None = None, page_size: int | None = None) -> str:
    """List all workbook agents across the organization."""
    c = await get_client()
    result = await c.list_org_workbook_agents(page_token, page_size)
    return json.dumps(result, indent=2)


list_org_workbook_agents = sigma_list_org_workbook_agents


@admin_server.tool(name="sigma_get_org_setting", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_get_org_setting(setting_name: str) -> str:
    """Get organization setting value.

    setting_name: One of aiChatHistory, auditLogging, bulkCopy, comments, csvUpload,
                  emailBranding, licenseUpgradeRequests, publicEmbeds, sampleConnections, timezone.
    """
    if not setting_name or not setting_name.strip():
        return _invalid_request("setting_name is required")
    if setting_name not in VALID_ORG_SETTINGS:
        return _invalid_request(
            f"Invalid setting_name '{setting_name}'. Must be one of: {', '.join(sorted(VALID_ORG_SETTINGS))}"
        )
    c = await get_client()
    result = await c.get_org_setting(setting_name)
    return json.dumps(result, indent=2)


get_org_setting = sigma_get_org_setting


@admin_server.tool(name="sigma_update_org_setting", annotations=ANNOTATION_WRITE_SAFE, tags={"admin", "mutation"})
@sigma_tool
async def sigma_update_org_setting(setting_name: str, setting_value: dict[str, Any], confirm: bool = False) -> str:
    """Update organization setting value.

    Mutating operation. Requires confirm=True.
    setting_name: One of aiChatHistory, auditLogging, bulkCopy, comments, csvUpload,
                  emailBranding, licenseUpgradeRequests, publicEmbeds, sampleConnections, timezone.
    setting_value: Dict containing setting fields to update.
    """
    if not confirm:
        return _invalid_request("Must specify confirm=True to update organization setting")
    if not setting_name or not setting_name.strip():
        return _invalid_request("setting_name is required")
    if setting_name not in VALID_ORG_SETTINGS:
        return _invalid_request(
            f"Invalid setting_name '{setting_name}'. Must be one of: {', '.join(sorted(VALID_ORG_SETTINGS))}"
        )
    c = await get_client()
    result = await c.update_org_setting(setting_name, setting_value)
    return json.dumps(result, indent=2)


update_org_setting = sigma_update_org_setting


@admin_server.tool(name="sigma_configure_org_ai", annotations=ANNOTATION_WRITE_SAFE, tags={"admin", "mutation"})
@sigma_tool
async def sigma_configure_org_ai(provider_config: dict[str, Any], confirm: bool = False) -> str:
    """Configure the organization AI provider and models.

    Mutating operation. Requires confirm=True.
    provider_config: Provider specification dict (e.g. provider='openAI'/'anthropic'/'gemini'/'snowflake'/'databricks'/'bedrock'/'azureOpenAI').
    """
    if not confirm:
        return _invalid_request("Must specify confirm=True to configure the organization AI provider")
    if not provider_config:
        return _invalid_request("provider_config is required")
    c = await get_client()
    result = await c.configure_org_ai(provider_config)
    return json.dumps(result, indent=2)


configure_org_ai = sigma_configure_org_ai


@admin_server.tool(
    name="sigma_reset_org_email_branding", annotations=ANNOTATION_DESTRUCTIVE, tags={"admin", "destructive"}
)
@sigma_tool
async def sigma_reset_org_email_branding(confirm: bool = False) -> str:
    """Reset the organization email branding settings back to defaults.

    Destructive operation. Requires confirm=True.
    """
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    c = await get_client()
    status = await c.reset_org_email_branding()
    return json.dumps({"status": "reset", "statusCode": status}, indent=2)


reset_org_email_branding = sigma_reset_org_email_branding


@admin_server.tool(name="sigma_list_allowed_ips", annotations=ANNOTATION_READ_ONLY, tags={"admin", "read_only"})
@sigma_tool
async def sigma_list_allowed_ips(page_token: str | None = None, page_size: int | None = None) -> str:
    """List IP allowlist entries configured for the organization (v3alpha)."""
    c = await get_client()
    result = await c.list_allowed_ips(page_token, page_size)
    return json.dumps(result, indent=2)


list_allowed_ips = sigma_list_allowed_ips


@admin_server.tool(name="sigma_add_allowed_ips", annotations=ANNOTATION_WRITE_SAFE, tags={"admin", "mutation"})
@sigma_tool
async def sigma_add_allowed_ips(entries: list[dict[str, Any]], confirm: bool = False) -> str:
    """Create/add IP allowlist entries for the organization (v3alpha).

    Mutating operation. Requires confirm=True.
    entries: List of dicts, each with 'ip' (IPv4/IPv6 or CIDR), 'scope' ('public-api', 'ui', or 'both'),
             and optional 'description'.
    """
    if not confirm:
        return _invalid_request("Must specify confirm=True to add IP allowlist entries")
    if not entries:
        return _invalid_request("entries list is required and cannot be empty")
    c = await get_client()
    result = await c.batch_create_allowed_ips(entries)
    return json.dumps(result, indent=2)


add_allowed_ips = sigma_add_allowed_ips


@admin_server.tool(name="sigma_remove_allowed_ips", annotations=ANNOTATION_DESTRUCTIVE, tags={"admin", "destructive"})
@sigma_tool
async def sigma_remove_allowed_ips(entry_ids: list[str], confirm: bool = False) -> str:
    """Delete IP allowlist entries by ID from the organization (v3alpha).

    Destructive operation. Requires confirm=True.
    entry_ids: List of IP allowlist entry ID strings to delete.
    """
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    if not entry_ids:
        return _invalid_request("entry_ids list is required and cannot be empty")
    c = await get_client()
    result = await c.batch_delete_allowed_ips(entry_ids)
    return json.dumps(result, indent=2)


remove_allowed_ips = sigma_remove_allowed_ips
