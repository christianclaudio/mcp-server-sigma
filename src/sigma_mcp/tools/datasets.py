"""Datasets domain sub-server for Sigma MCP."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastmcp import FastMCP

from sigma_mcp.client import (
    SigmaClient,
)
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

datasets_server = FastMCP(
    "sigma-datasets", instructions="Connections, data models, warehouse tables, and schema synchronization."
)


@datasets_server.tool(name="sigma_list_connections", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_list_connections(summary_only: bool = False) -> str:
    """List all connections in the Sigma organization. Pass summary_only=True for concise token-efficient response."""
    data = await (await get_client()).list_connections()
    if summary_only:
        data = _summarize_list(data, ["connectionId", "name", "type"])
    return json.dumps(data, indent=2)


list_connections = sigma_list_connections


@datasets_server.tool(name="sigma_get_connection", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_get_connection(connection_id: str) -> str:
    """Get details for a specific connection."""
    return json.dumps(await (await get_client()).get_connection(connection_id), indent=2)


get_connection = sigma_get_connection


@datasets_server.tool(name="sigma_sync_connection", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"})
@sigma_tool
async def sigma_sync_connection(connection_id: str, path: list[str] | None = None) -> str:
    """Force Sigma to re-index a warehouse path. Pass empty list for full sync."""
    return json.dumps(await (await get_client()).sync_connection(connection_id, path), indent=2)


sync_connection = sigma_sync_connection


@datasets_server.tool(name="sigma_test_connection", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"})
@sigma_tool
async def sigma_test_connection(connection_id: str) -> str:
    """Test connectivity for a connection."""
    return json.dumps(await (await get_client()).test_connection(connection_id), indent=2)


test_connection = sigma_test_connection


@datasets_server.tool(
    name="sigma_list_columns_for_table", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"}
)
@sigma_tool
async def sigma_list_columns_for_table(table_id: str) -> str:
    """List columns for a warehouse table by its tableId."""
    return json.dumps(await (await get_client()).list_columns_for_table(table_id), indent=2)


list_columns_for_table = sigma_list_columns_for_table


@datasets_server.tool(
    name="sigma_list_connection_grants", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"}
)
@sigma_tool
async def sigma_list_connection_grants(connection_id: str) -> str:
    """List permission grants on a connection."""
    return json.dumps(await (await get_client()).list_connection_grants(connection_id), indent=2)


list_connection_grants = sigma_list_connection_grants


@datasets_server.tool(
    name="sigma_add_connection_grant", annotations=ANNOTATION_IDEMPOTENT, tags={"datasets", "idempotent"}
)
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


add_connection_grant = sigma_add_connection_grant


@datasets_server.tool(
    name="sigma_delete_connection_path_grant", annotations=ANNOTATION_DESTRUCTIVE, tags={"datasets", "destructive"}
)
@sigma_tool
async def sigma_delete_connection_path_grant(connection_path_id: str, grant_id: str, confirm: bool = False) -> str:
    """Delete a grant from a connection path. DESTRUCTIVE. Requires confirm=True."""
    if not confirm:
        return _invalid_request("Destructive operation requires explicit confirm=True parameter.")
    code = await (await get_client()).delete_connection_path_grant(connection_path_id, grant_id)
    return json.dumps({"status": code})


delete_connection_path_grant = sigma_delete_connection_path_grant


@datasets_server.tool(name="sigma_list_data_models", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_list_data_models(limit: int = 200, summary_only: bool = False) -> str:
    """List all data models in the organization. Pass summary_only=True for concise token-efficient response."""
    data = await (await get_client()).list_data_models(limit)
    if summary_only:
        data = _summarize_list(data, ["dataModelId", "name", "connectionId"])
    return json.dumps(data, indent=2)


list_data_models = sigma_list_data_models


@datasets_server.tool(name="sigma_get_data_model", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_get_data_model(data_model_id: str) -> str:
    """Get data model metadata."""
    return json.dumps(await (await get_client()).get_data_model(data_model_id), indent=2)


get_data_model = sigma_get_data_model


@datasets_server.tool(
    name="sigma_get_data_model_spec", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"}
)
@sigma_tool
async def sigma_get_data_model_spec(data_model_id: str) -> str:
    """Get the full code representation (JSON spec) of a data model — tables, columns, metrics, relationships."""
    return json.dumps(await (await get_client()).get_data_model_spec(data_model_id), indent=2)


get_data_model_spec = sigma_get_data_model_spec


@datasets_server.tool(name="sigma_create_data_model", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"})
@sigma_tool
async def sigma_create_data_model(spec: dict[str, Any]) -> str:
    """Create a data model from a JSON code representation. Must include name, folderId, schemaVersion, and pages with elements."""
    return json.dumps(await (await get_client()).create_data_model_spec(spec), indent=2)


create_data_model = sigma_create_data_model


@datasets_server.tool(name="sigma_update_data_model", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"})
@sigma_tool
async def sigma_update_data_model(data_model_id: str, spec: dict[str, Any]) -> str:
    """Update an existing data model from a JSON code representation (full replacement via PUT)."""
    return json.dumps(await (await get_client()).update_data_model_spec(data_model_id, spec), indent=2)


update_data_model = sigma_update_data_model


@datasets_server.tool(
    name="sigma_list_data_model_elements", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"}
)
@sigma_tool
async def sigma_list_data_model_elements(data_model_id: str) -> str:
    """List elements in a data model."""
    return json.dumps(await (await get_client()).list_data_model_elements(data_model_id), indent=2)


list_data_model_elements = sigma_list_data_model_elements


@datasets_server.tool(
    name="sigma_list_data_model_columns", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"}
)
@sigma_tool
async def sigma_list_data_model_columns(data_model_id: str) -> str:
    """List all columns across all elements in a data model."""
    return json.dumps(await (await get_client()).list_data_model_columns(data_model_id), indent=2)


list_data_model_columns = sigma_list_data_model_columns


@datasets_server.tool(
    name="sigma_swap_data_model_sources", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"}
)
@sigma_tool
async def sigma_swap_data_model_sources(data_model_id: str, body: dict[str, Any]) -> str:
    """Swap data sources for a data model."""
    return json.dumps(await (await get_client()).swap_data_model_sources(data_model_id, body), indent=2)


swap_data_model_sources = sigma_swap_data_model_sources


@datasets_server.tool(
    name="sigma_list_data_model_lineage", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"}
)
@sigma_tool
async def sigma_list_data_model_lineage(data_model_id: str) -> str:
    """List lineage for a data model."""
    return json.dumps(await (await get_client()).list_data_model_lineage(data_model_id), indent=2)


list_data_model_lineage = sigma_list_data_model_lineage


@datasets_server.tool(name="sigma_tag_data_model", annotations=ANNOTATION_IDEMPOTENT, tags={"datasets", "idempotent"})
@sigma_tool
async def sigma_tag_data_model(data_model_id: str, tag_name: str) -> str:
    """Apply a version tag to a data model by tag NAME. The Sigma API takes the tag name here, not its ID."""
    return json.dumps(await (await get_client()).tag_data_model(data_model_id, tag_name), indent=2)


tag_data_model = sigma_tag_data_model


@datasets_server.tool(
    name="sigma_swap_report_sources", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"}
)
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


swap_report_sources = sigma_swap_report_sources


@datasets_server.tool(
    name="sigma_sync_all_tables_in_schema", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"}
)
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


sync_all_tables_in_schema = sigma_sync_all_tables_in_schema


@datasets_server.tool(
    name="sigma_bulk_sync_tenant_connections", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"}
)
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


bulk_sync_tenant_connections = sigma_bulk_sync_tenant_connections


@datasets_server.tool(
    name="sigma_list_all_data_models", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"}
)
@sigma_tool
async def sigma_list_all_data_models() -> str:
    """List ALL data models in the organization, automatically following pagination."""
    return json.dumps(await (await get_client()).list_all_data_models(), indent=2)


list_all_data_models = sigma_list_all_data_models
