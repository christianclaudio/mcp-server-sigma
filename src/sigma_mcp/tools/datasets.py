"""Datasets domain sub-server for Sigma MCP."""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any, Literal

from fastmcp import FastMCP

from sigma_mcp.client import (
    SigmaClient,
)
from sigma_mcp.tools.common import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_IDEMPOTENT,
    ANNOTATION_READ_ONLY,
    ANNOTATION_WRITE_SAFE,
    CONNECTION_SECRET_FIELDS,
    _batch_outcome,
    _call_masking_secrets,
    _confirm_required,
    _invalid_request,
    _item_error,
    _require_body_fields,
    _require_ids,
    _summarize_list,
    _tool_failure,
    sigma_tool,
)
from sigma_mcp.tools.common import (
    resolve_client as get_client,
)

datasets_server = FastMCP(
    "sigma-datasets", instructions="Connections, data models, warehouse tables, and schema synchronization."
)


@datasets_server.tool(name="list_connections", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_list_connections(summary_only: bool = False) -> str:
    """List all connections in the Sigma organization. Pass summary_only=True for concise token-efficient response.

    Follows Sigma's pagination to the last page and returns the keys of Sigma's page: ``entries``
    from every page, ``total`` when Sigma sends it (Sigma's value), and ``nextPage`` null (``hasMore``
    false where Sigma sends it) since every page is read.
    """
    data = await (await get_client()).list_connections()
    if summary_only:
        data = _summarize_list(data, ["connectionId", "name", "type"])
    return json.dumps(data, indent=2)


list_connections = sigma_list_connections


@datasets_server.tool(name="get_connection", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_get_connection(connection_id: str) -> str:
    """Get details for a specific connection."""
    return json.dumps(await (await get_client()).get_connection(connection_id), indent=2)


get_connection = sigma_get_connection


@datasets_server.tool(name="sync_connection", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"})
@sigma_tool
async def sigma_sync_connection(connection_id: str, path: list[str] | None = None) -> str:
    """Force Sigma to re-index a warehouse path. Pass empty list for full sync."""
    return json.dumps(await (await get_client()).sync_connection(connection_id, path), indent=2)


sync_connection = sigma_sync_connection


@datasets_server.tool(name="test_connection", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"})
@sigma_tool
async def sigma_test_connection(connection_id: str) -> str:
    """Test connectivity for a connection."""
    return json.dumps(await (await get_client()).test_connection(connection_id), indent=2)


test_connection = sigma_test_connection


@datasets_server.tool(name="list_columns_for_table", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_list_columns_for_table(table_id: str) -> str:
    """List columns for a warehouse table by its tableId.

    Follows Sigma's pagination to the last page and returns the keys of Sigma's page: ``entries``
    from every page and ``nextPageToken`` null, since every page is read.
    """
    return json.dumps(await (await get_client()).list_columns_for_table(table_id), indent=2)


list_columns_for_table = sigma_list_columns_for_table


@datasets_server.tool(name="list_connection_grants", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_list_connection_grants(connection_id: str) -> str:
    """List permission grants on a connection.

    Follows Sigma's pagination to the last page and returns the keys of Sigma's page: ``entries``
    from every page, ``total`` when Sigma sends it (Sigma's value), and ``nextPage`` null (``hasMore``
    false where Sigma sends it) since every page is read.
    """
    return json.dumps(await (await get_client()).list_connection_grants(connection_id), indent=2)


list_connection_grants = sigma_list_connection_grants


@datasets_server.tool(name="add_connection_grant", annotations=ANNOTATION_IDEMPOTENT, tags={"datasets", "idempotent"})
@sigma_tool
async def sigma_add_connection_grant(connection_id: str, grant_type: str, grantee_id: str, permission: str) -> str:
    """Add a grant to a connection.

    grant_type: 'member' or 'team'.
    permission: 'annotate' (Can Use & Annotate) or 'usage' (Can Use).
    """
    gt = grant_type.strip().lower()
    if gt not in ("member", "team"):
        _invalid_request("grant_type must be 'member' or 'team'")
    grantee_key = "memberId" if gt == "member" else "teamId"
    body = {"grants": [{"grantee": {grantee_key: grantee_id}, "permission": permission}]}
    return json.dumps(await (await get_client()).add_connection_grant(connection_id, body), indent=2)


add_connection_grant = sigma_add_connection_grant


@datasets_server.tool(
    name="delete_connection_path_grant", annotations=ANNOTATION_DESTRUCTIVE, tags={"datasets", "destructive"}
)
@sigma_tool
async def sigma_delete_connection_path_grant(connection_path_id: str, grant_id: str, confirm: bool = False) -> str:
    """Delete a grant from a connection path. DESTRUCTIVE. Requires confirm=True."""
    if not confirm:
        return _confirm_required("This destructive operation")
    code = await (await get_client()).delete_connection_path_grant(connection_path_id, grant_id)
    return json.dumps({"status": code})


delete_connection_path_grant = sigma_delete_connection_path_grant


@datasets_server.tool(name="list_data_models", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_list_data_models(limit: int = 200, summary_only: bool = False) -> str:
    """List all data models in the organization. Pass summary_only=True for concise token-efficient response.

    Follows Sigma's pagination to the last page and returns the keys of Sigma's page: ``entries``
    from every page, ``total`` when Sigma sends it (Sigma's value), and ``nextPage`` null (``hasMore``
    false where Sigma sends it) since every page is read. ``limit`` is the page size.
    """
    data = await (await get_client()).list_data_models(limit)
    if summary_only:
        data = _summarize_list(data, ["dataModelId", "name", "connectionId"])
    return json.dumps(data, indent=2)


list_data_models = sigma_list_data_models


@datasets_server.tool(name="get_data_model", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_get_data_model(data_model_id: str) -> str:
    """Get data model metadata."""
    return json.dumps(await (await get_client()).get_data_model(data_model_id), indent=2)


get_data_model = sigma_get_data_model


@datasets_server.tool(name="get_data_model_spec", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_get_data_model_spec(data_model_id: str) -> str:
    """Get the full code representation (JSON spec) of a data model — tables, columns, metrics, relationships."""
    return json.dumps(await (await get_client()).get_data_model_spec(data_model_id), indent=2)


get_data_model_spec = sigma_get_data_model_spec


@datasets_server.tool(name="create_data_model", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"})
@sigma_tool
async def sigma_create_data_model(spec: dict[str, Any]) -> str:
    """Create a data model from a JSON code representation. Must include name, folderId, schemaVersion, and pages with elements."""
    return json.dumps(await (await get_client()).create_data_model_spec(spec), indent=2)


create_data_model = sigma_create_data_model


@datasets_server.tool(name="update_data_model", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"})
@sigma_tool
async def sigma_update_data_model(data_model_id: str, spec: dict[str, Any]) -> str:
    """Update an existing data model from a JSON code representation (full replacement via PUT)."""
    return json.dumps(await (await get_client()).update_data_model_spec(data_model_id, spec), indent=2)


update_data_model = sigma_update_data_model


@datasets_server.tool(name="list_data_model_elements", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_list_data_model_elements(data_model_id: str) -> str:
    """List elements in a data model.

    Follows Sigma's pagination to the last page and returns the keys of Sigma's page: ``entries``
    from every page, ``total`` when Sigma sends it (Sigma's value), and ``nextPage`` null (``hasMore``
    false where Sigma sends it) since every page is read.
    """
    return json.dumps(await (await get_client()).list_data_model_elements(data_model_id), indent=2)


list_data_model_elements = sigma_list_data_model_elements


@datasets_server.tool(name="list_data_model_columns", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_list_data_model_columns(data_model_id: str) -> str:
    """List all columns across all elements in a data model.

    Follows Sigma's pagination to the last page and returns the keys of Sigma's page: ``entries``
    from every page, ``total`` when Sigma sends it (Sigma's value), and ``nextPage`` null (``hasMore``
    false where Sigma sends it) since every page is read.
    """
    return json.dumps(await (await get_client()).list_data_model_columns(data_model_id), indent=2)


list_data_model_columns = sigma_list_data_model_columns


@datasets_server.tool(name="swap_data_model_sources", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"})
@sigma_tool
async def sigma_swap_data_model_sources(data_model_id: str, body: dict[str, Any]) -> str:
    """Swap data sources for a data model."""
    return json.dumps(await (await get_client()).swap_data_model_sources(data_model_id, body), indent=2)


swap_data_model_sources = sigma_swap_data_model_sources


@datasets_server.tool(
    name="swap_data_model_connections", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"}
)
@sigma_tool
async def sigma_swap_data_model_connections(
    data_model_id: str,
    connection_mapping: list[dict[str, Any]],
    copy_input_table_data: bool | None = None,
) -> str:
    """Swap data model connections (Beta).

    OpenAPI: POST /v2/dataModels/{dataModelId}/swapConnections (dataModels_swapDataModelConnections).
    Spec: https://help.sigmacomputing.com/openapi/sigma-rest-api.json
    ``connection_mapping`` items require ``fromId`` and ``toId``; optional ``swapTables`` and ``paths``.
    """
    body: dict[str, Any] = {"connectionMapping": connection_mapping}
    if copy_input_table_data is not None:
        body["copyInputTableData"] = copy_input_table_data
    return json.dumps(await (await get_client()).swap_data_model_connections(data_model_id, body), indent=2)


swap_data_model_connections = sigma_swap_data_model_connections


@datasets_server.tool(
    name="swap_data_model_source_elements", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"}
)
@sigma_tool
async def sigma_swap_data_model_source_elements(
    data_model_id: str,
    source_mapping: list[dict[str, Any]],
) -> str:
    """Swap data model source elements (Beta).

    OpenAPI: POST /v2/dataModels/{dataModelId}/swapSourceElements (dataModels_swapDataModelSourceElements).
    Spec: https://help.sigmacomputing.com/openapi/sigma-rest-api.json
    ``source_mapping`` items require ``from`` and ``to``; optional ``metricMapping`` and ``columnMapping``.
    """
    body: dict[str, Any] = {"sourceMapping": source_mapping}
    return json.dumps(await (await get_client()).swap_data_model_source_elements(data_model_id, body), indent=2)


swap_data_model_source_elements = sigma_swap_data_model_source_elements


@datasets_server.tool(name="list_data_model_lineage", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_list_data_model_lineage(data_model_id: str) -> str:
    """List lineage for a data model.

    Follows Sigma's pagination to the last page and returns the keys of Sigma's page: ``entries``
    from every page, ``total`` when Sigma sends it (Sigma's value), and ``nextPage`` null (``hasMore``
    false where Sigma sends it) since every page is read.
    """
    return json.dumps(await (await get_client()).list_data_model_lineage(data_model_id), indent=2)


list_data_model_lineage = sigma_list_data_model_lineage


@datasets_server.tool(name="tag_data_model", annotations=ANNOTATION_IDEMPOTENT, tags={"datasets", "idempotent"})
@sigma_tool
async def sigma_tag_data_model(data_model_id: str, tag_name: str) -> str:
    """Apply a version tag to a data model by tag NAME. The Sigma API takes the tag name here, not its ID."""
    return json.dumps(await (await get_client()).tag_data_model(data_model_id, tag_name), indent=2)


tag_data_model = sigma_tag_data_model


@datasets_server.tool(name="set_data_model_badge", annotations=ANNOTATION_IDEMPOTENT, tags={"datasets", "idempotent"})
@sigma_tool
async def sigma_set_data_model_badge(
    data_model_id: str, badge: Literal["Deprecated", "Warning", "Endorsed"], badge_note: str | None = None
) -> str:
    """Set or replace the badge on a data model: Deprecated, Warning or Endorsed, with an optional note.

    Omitting badge_note CLEARS the existing badge note; pass the current note to keep it. Calling with a
    tagged-version ID sets the badge on the original document. The API cannot remove a badge (removal is
    UI-only, Set badge > Delete). Needs the Manage all badges permission; a 403 comes back as a tool error.
    Beta endpoint. Spec operation: setDataModelBadge.
    """
    _require_ids(data_model_id=data_model_id)
    return json.dumps(await (await get_client()).set_data_model_badge(data_model_id, badge, badge_note), indent=2)


set_data_model_badge = sigma_set_data_model_badge


@datasets_server.tool(name="tag_git_repository", annotations=ANNOTATION_IDEMPOTENT, tags={"datasets", "idempotent"})
@sigma_tool
async def sigma_tag_git_repository(
    repository_path: str,
    ref: str,
    tag_name: str,
    source_mapping_config: list[dict[str, Any]] | None = None,
    data_model_source_tagged_versions: list[dict[str, Any]] | None = None,
    is_default: bool | None = None,
    confirm: bool = False,
) -> str:
    """Tag every data model linked to a Git repository at a commit SHA or Git tag. Requires confirm=True.

    Guarded write: one call applies tag_name to every data model linked to the repository and can swap
    their sources. Needs the Admin account type; a non-Admin caller gets Sigma 403 back as a normal tool
    error. Private Beta endpoint. Spec operation: tagGitRepository (POST /v2/gitIntegration/tagRepository).
    repository_path: the repository as owner/repo. ref: a commit SHA or Git tag. tag_name: the version tag.
    source_mapping_config: optional items {fromConnectionId, toConnectionId, paths: [{fromPath, toPath}]}.
    data_model_source_tagged_versions: optional items {dataModelId, fromVersionTagId, toVersionTagId}.
    is_default: optional boolean, sent only when set.
    """
    if not confirm:
        return _confirm_required("Tagging every data model in the repository")
    _require_ids(repository_path=repository_path, ref=ref, tag_name=tag_name)
    client = await get_client()
    result = await client.tag_git_repository(
        repository_path, ref, tag_name, source_mapping_config, data_model_source_tagged_versions, is_default
    )
    return json.dumps(result, indent=2)


tag_git_repository = sigma_tag_git_repository


@datasets_server.tool(name="swap_report_sources", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"})
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
    name="sync_all_tables_in_schema", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"}
)
@sigma_tool
async def sigma_sync_all_tables_in_schema(connection_id: str, database: str, schema: str) -> str:
    """Sync all tables in a warehouse schema so they become visible in Sigma."""
    if not connection_id or not connection_id.strip():
        _invalid_request("connection_id is required")
    if not database or not database.strip():
        _invalid_request("database is required")
    if not schema or not schema.strip():
        _invalid_request("schema is required")
    c = await get_client()
    path = [database, schema]
    await c.sync_connection(connection_id, path)
    return json.dumps({"synced_path": path, "status": "ok"})


sync_all_tables_in_schema = sigma_sync_all_tables_in_schema


@datasets_server.tool(
    name="bulk_sync_tenant_connections", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"}
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
        _tool_failure(
            "not_supported", "Multi-tenant auth (for_tenant) not yet available. Phase C must be implemented first."
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
    errors: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(3)

    async def sync_tenant(index: int, tenant: dict[str, Any]) -> None:
        org_id = tenant.get("orgId", "")
        async with sem:
            tc: SigmaClient | None = None
            try:
                tc = await c.for_tenant(org_id)
                conns = await tc.list_connections()
                conn_entries = conns.get("entries", []) if isinstance(conns, dict) else []
                synced: list[dict[str, Any]] = []
                conn_errors: list[dict[str, Any]] = []
                for conn_index, conn in enumerate(conn_entries):
                    cid = conn.get("connectionId", "")
                    try:
                        res = await tc.sync_connection(cid, [])
                        synced.append({"id": cid, "connectionId": cid, "status": "synced", "result": res})
                    except Exception as e:
                        conn_errors.append(
                            {
                                "id": cid,
                                "connectionId": cid,
                                "status": "failed",
                                "error": _item_error(e, index=conn_index),
                            }
                        )
                # main's per-tenant connection errors (redacted message strings).
                legacy_conn_errors = [{"connectionId": e["id"], "error": e["error"]["message"]} for e in conn_errors]
                if conn_errors and not synced:
                    # A tenant whose every connection sync failed is a failed item; main's entry stays in results.
                    results.append(
                        {
                            "id": org_id,
                            "orgId": org_id,
                            "name": tenant.get("name"),
                            "connections_synced": 0,
                            "errors": legacy_conn_errors,
                            "status": "failed",
                        }
                    )
                    errors.append(
                        {
                            "id": org_id,
                            "orgId": org_id,
                            "name": tenant.get("name"),
                            "connections_synced": 0,
                            "status": "failed",
                            "error": {
                                "type": "batch_failed",
                                "message": f"All {len(conn_errors)} connection syncs failed.",
                                "connections": conn_errors,
                            },
                        }
                    )
                else:
                    entry: dict[str, Any] = {
                        "id": org_id,
                        "orgId": org_id,
                        "name": tenant.get("name"),
                        "connections_synced": len(synced),
                    }
                    if legacy_conn_errors:
                        entry["errors"] = legacy_conn_errors
                    results.append(
                        {
                            **entry,
                            "status": "synced",
                            "result": {
                                "name": tenant.get("name"),
                                "status": "partial_success" if conn_errors else "success",
                                "synced_count": len(synced),
                                "failed_count": len(conn_errors),
                                "results": synced,
                                "errors": conn_errors,
                            },
                        }
                    )
            except Exception as e:
                item_err = _item_error(e, index=index)
                failed = {"id": org_id, "orgId": org_id, "name": tenant.get("name"), "status": "failed"}
                # main's entry (redacted message string) stays in results; the error object is in errors.
                results.append({**failed, "error": item_err["message"]})
                errors.append({**failed, "error": item_err})
            finally:
                # Tenant clients borrow the parent's transport, so aclose() is a
                # no-op for them. Called anyway so the contract holds if that
                # ownership ever changes.
                if tc is not None:
                    await tc.aclose()

    await asyncio.gather(*[sync_tenant(i, t) for i, t in enumerate(tenants)])

    return _batch_outcome("tenant syncs", "synced", results, errors, extra={"tenants_processed": len(results)})


bulk_sync_tenant_connections = sigma_bulk_sync_tenant_connections


@datasets_server.tool(name="list_all_data_models", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_list_all_data_models() -> str:
    """List ALL data models in the organization, automatically following pagination."""
    return json.dumps(await (await get_client()).list_all_data_models(), indent=2)


list_all_data_models = sigma_list_all_data_models


# Tools for client methods that had none (API coverage audit, 2026-10-20).


@datasets_server.tool(name="create_connection", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"})
@sigma_tool
async def sigma_create_connection(body: dict[str, Any]) -> str:
    """Create a data warehouse connection.

    body requires name and details (the warehouse type and its settings) and may set description, poolSizes,
    timeoutSecs and useFriendlyNames. The details may hold write-only secrets (password, privateKey,
    privateKeyPassphrase, clientSecret, token, pythonSecrets): pass them only from the user's own input.
    They are masked wherever Sigma's result or error echoes them.
    """
    _require_body_fields(body, "name", "details")
    client = await get_client()
    result = await _call_masking_secrets(client.create_connection(body), CONNECTION_SECRET_FIELDS, body.get("details"))
    return json.dumps(result, indent=2)


create_connection = sigma_create_connection


@datasets_server.tool(name="delete_connection", annotations=ANNOTATION_DESTRUCTIVE, tags={"datasets", "destructive"})
@sigma_tool
async def sigma_delete_connection(connection_id: str, confirm: bool = False) -> str:
    """Delete a connection. Requires confirm=True. Workbooks and data models built on it lose their source."""
    if not confirm:
        return _confirm_required("This destructive operation")
    _require_ids(connection_id=connection_id)
    code = await (await get_client()).delete_connection(connection_id)
    return json.dumps({"status": "deleted", "statusCode": code})


delete_connection = sigma_delete_connection


@datasets_server.tool(name="list_connection_paths", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_list_connection_paths() -> str:
    """List connection paths (databases, schemas and tables) across all connections.

    Follows Sigma's pagination to the last page and returns the keys of Sigma's page: ``entries``
    from every page, ``total`` when Sigma sends it (Sigma's value), and ``nextPage`` null (``hasMore``
    false where Sigma sends it) since every page is read.
    """
    return json.dumps(await (await get_client()).list_connection_paths(), indent=2)


list_connection_paths = sigma_list_connection_paths


@datasets_server.tool(name="lookup_connection_path", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_lookup_connection_path(connection_id: str, path: list[str]) -> str:
    """Look up the object at a fully qualified path in a connection, e.g. ["DATABASE", "SCHEMA", "TABLE"] for Snowflake.

    Read-only: the POST only resolves the path.
    """
    _require_ids(connection_id=connection_id)
    if not path or not all(isinstance(p, str) and p.strip() for p in path):
        _invalid_request("path must be a non-empty list of non-blank names")
    return json.dumps(await (await get_client()).lookup_connection_path(connection_id, path), indent=2)


lookup_connection_path = sigma_lookup_connection_path


@datasets_server.tool(
    name="delete_connection_grant", annotations=ANNOTATION_DESTRUCTIVE, tags={"datasets", "destructive"}
)
@sigma_tool
async def sigma_delete_connection_grant(connection_id: str, grant_id: str, confirm: bool = False) -> str:
    """Delete a grant from a connection. Requires confirm=True. Retrieve grant IDs with datasets_list_connection_grants."""
    if not confirm:
        return _confirm_required("This destructive operation")
    _require_ids(connection_id=connection_id, grant_id=grant_id)
    code = await (await get_client()).delete_connection_grant(connection_id, grant_id)
    return json.dumps({"status": "deleted", "statusCode": code})


delete_connection_grant = sigma_delete_connection_grant


@datasets_server.tool(
    name="list_connection_path_grants", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"}
)
@sigma_tool
async def sigma_list_connection_path_grants(connection_path_id: str) -> str:
    """List grants on a connection path (database, schema or table).

    Follows Sigma's pagination to the last page and returns the keys of Sigma's page: ``entries``
    from every page, ``total`` when Sigma sends it (Sigma's value), and ``nextPage`` null (``hasMore``
    false where Sigma sends it) since every page is read.
    """
    _require_ids(connection_path_id=connection_path_id)
    return json.dumps(await (await get_client()).list_connection_path_grants(connection_path_id), indent=2)


list_connection_path_grants = sigma_list_connection_path_grants


@datasets_server.tool(
    name="add_connection_path_grant", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"}
)
@sigma_tool
async def sigma_add_connection_path_grant(connection_path_id: str, body: dict[str, Any]) -> str:
    """Add grants to a connection path.

    body requires grants: a list of {grantee: {memberId} or {teamId}, permission: 'annotate' | 'usage'}.
    """
    _require_ids(connection_path_id=connection_path_id)
    _require_body_fields(body, "grants")
    return json.dumps(await (await get_client()).add_connection_path_grant(connection_path_id, body), indent=2)


add_connection_path_grant = sigma_add_connection_path_grant


@datasets_server.tool(name="list_data_model_sources", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_list_data_model_sources(data_model_id: str) -> str:
    """List the sources (tables, datasets, other data models) a data model reads from.

    Follows Sigma's pagination to the last page and returns the keys of Sigma's page: ``entries``
    from every page and ``nextPageToken`` null, since every page is read.
    """
    _require_ids(data_model_id=data_model_id)
    return json.dumps(await (await get_client()).list_data_model_sources(data_model_id), indent=2)


list_data_model_sources = sigma_list_data_model_sources


@datasets_server.tool(name="materialize_data_model", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"})
@sigma_tool
async def sigma_materialize_data_model(data_model_id: str, sheet_id: str) -> str:
    """Run the scheduled materialization for one data model element now.

    sheet_id is the element's ID. Returns the materialization job; poll it with
    datasets_get_data_model_materialization.
    """
    _require_ids(data_model_id=data_model_id, sheet_id=sheet_id)
    return json.dumps(await (await get_client()).materialize_data_model(data_model_id, {"sheetId": sheet_id}), indent=2)


materialize_data_model = sigma_materialize_data_model


@datasets_server.tool(
    name="get_data_model_materialization", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"}
)
@sigma_tool
async def sigma_get_data_model_materialization(data_model_id: str, materialization_id: str) -> str:
    """Get the status of a data model materialization job."""
    _require_ids(data_model_id=data_model_id, materialization_id=materialization_id)
    return json.dumps(
        await (await get_client()).get_data_model_materialization(data_model_id, materialization_id), indent=2
    )


get_data_model_materialization = sigma_get_data_model_materialization


@datasets_server.tool(
    name="list_data_model_materialization_schedules", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"}
)
@sigma_tool
async def sigma_list_data_model_materialization_schedules(data_model_id: str) -> str:
    """List the materialization schedules of a data model.

    Follows Sigma's pagination to the last page and returns the keys of Sigma's page: ``entries``
    from every page and ``nextPageToken`` null, since every page is read.
    """
    _require_ids(data_model_id=data_model_id)
    return json.dumps(await (await get_client()).list_data_model_materialization_schedules(data_model_id), indent=2)


list_data_model_materialization_schedules = sigma_list_data_model_materialization_schedules


@datasets_server.tool(name="list_data_model_tags", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_list_data_model_tags(data_model_id: str) -> str:
    """List the version tags on a data model.

    Follows Sigma's pagination to the last page and returns the keys of Sigma's page: ``entries``
    from every page, ``total`` when Sigma sends it (Sigma's value), and ``nextPage`` null (``hasMore``
    false where Sigma sends it) since every page is read.
    """
    _require_ids(data_model_id=data_model_id)
    return json.dumps(await (await get_client()).list_data_model_tags(data_model_id), indent=2)


list_data_model_tags = sigma_list_data_model_tags

# Sigma's dbt artifacts operation describes a tar.gz of the dbt target directory and does not
# state a byte limit (https://help.sigmacomputing.com/reference/post-connection-dbt-artifacts).
# The cap matches the query-export download cap.
_DBT_ARTIFACT_MAX_BYTES = 10_000_000


def _paged(data: Any) -> str:
    return json.dumps(data, indent=2)


@datasets_server.tool(name="update_connection", annotations=ANNOTATION_IDEMPOTENT, tags={"datasets", "idempotent"})
@sigma_tool
async def sigma_update_connection(connection_id: str, body: dict[str, Any]) -> str:
    """Replace a connection (PUT /v2/connections/{connectionId}).

    body requires name and details, and may set description, poolSizes, timeoutSecs, useFriendlyNames
    and restore. Replaces the deprecated PATCH. details may hold write-only secrets (password, privateKey,
    privateKeyPassphrase, clientSecret, token, pythonSecrets). They are masked wherever Sigma's result or
    error echoes them.
    """
    _require_ids(connection_id=connection_id)
    _require_body_fields(body, "name", "details")
    name = body["name"]
    details = body["details"]
    fields = {key: value for key, value in body.items() if key not in {"name", "details"}}
    client = await get_client()
    result = await _call_masking_secrets(
        client.replace_connection(connection_id, name, details, **fields), CONNECTION_SECRET_FIELDS, details
    )
    return json.dumps(result, indent=2)


update_connection = sigma_update_connection


@datasets_server.tool(name="get_connection_path", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_get_connection_path(inode_id: str) -> str:
    """Get the connection path for a table inode."""
    _require_ids(inode_id=inode_id)
    return _paged(await (await get_client()).get_connection_path(inode_id))


get_connection_path = sigma_get_connection_path


@datasets_server.tool(name="get_warehouse_connection", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"})
@sigma_tool
async def sigma_get_warehouse_connection(connection_id: str) -> str:
    """Beta, v3alpha (private beta; request and response may change). Get a cloud data warehouse connection."""
    _require_ids(connection_id=connection_id)
    return _paged(await (await get_client()).get_warehouse_connection(connection_id))


get_warehouse_connection = sigma_get_warehouse_connection


@datasets_server.tool(
    name="create_warehouse_connection", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"}
)
@sigma_tool
async def sigma_create_warehouse_connection(body: dict[str, Any]) -> str:
    """Beta, v3alpha (private beta; request and response may change). Create a cloud data warehouse connection.

    body requires name and details. Write-only secrets in details are masked in the result and in errors.
    """
    _require_body_fields(body, "name", "details")
    client = await get_client()
    result = await _call_masking_secrets(
        client.create_warehouse_connection(body), CONNECTION_SECRET_FIELDS, body.get("details")
    )
    return json.dumps(result, indent=2)


create_warehouse_connection = sigma_create_warehouse_connection


@datasets_server.tool(
    name="update_warehouse_connection", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"}
)
@sigma_tool
async def sigma_update_warehouse_connection(connection_id: str, body: dict[str, Any]) -> str:
    """Beta, v3alpha (private beta; request and response may change). Update a cloud data warehouse connection.

    body may set name and details. Write-only secrets in details are masked in the result and in errors.
    """
    _require_ids(connection_id=connection_id)
    client = await get_client()
    result = await _call_masking_secrets(
        client.update_warehouse_connection(connection_id, body), CONNECTION_SECRET_FIELDS, body.get("details")
    )
    return json.dumps(result, indent=2)


update_warehouse_connection = sigma_update_warehouse_connection


@datasets_server.tool(
    name="swap_data_model_source_mapping", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"}
)
@sigma_tool
async def sigma_swap_data_model_source_mapping(data_model_id: str, body: dict[str, Any]) -> str:
    """Beta, v3alpha (private beta; request and response may change). Swap sources on a data model.

    body requires sourceMapping.
    """
    _require_ids(data_model_id=data_model_id)
    _require_body_fields(body, "sourceMapping")
    return _paged(await (await get_client()).swap_data_model_source_mapping(data_model_id, body))


swap_data_model_source_mapping = sigma_swap_data_model_source_mapping


@datasets_server.tool(
    name="list_column_security_rules", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"}
)
@sigma_tool
async def sigma_list_column_security_rules(
    data_model_id: str, element_id: str, page: str | None = None, limit: int | None = None
) -> str:
    """Beta: Sigma marks this a beta feature. List column-level security rules on a data model element.

    Follows nextPage. page resumes from a nextPage; limit is the page size.
    """
    _require_ids(data_model_id=data_model_id, element_id=element_id)
    return _paged(await (await get_client()).list_column_security_rules(data_model_id, element_id, page, limit))


list_column_security_rules = sigma_list_column_security_rules


@datasets_server.tool(
    name="create_column_security_rules", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"}
)
@sigma_tool
async def sigma_create_column_security_rules(data_model_id: str, element_id: str, rules: list[dict[str, Any]]) -> str:
    """Beta: Sigma marks this a beta feature. Create column-level security rules on a data model element.

    rules is an array of objects. Each object requires columns and criteria.
    """
    _require_ids(data_model_id=data_model_id, element_id=element_id)
    for rule in rules:
        _require_body_fields(rule, "columns", "criteria")
    return _paged(await (await get_client()).create_column_security_rules(data_model_id, element_id, rules))


create_column_security_rules = sigma_create_column_security_rules


@datasets_server.tool(
    name="update_column_security_rule", annotations=ANNOTATION_IDEMPOTENT, tags={"datasets", "idempotent"}
)
@sigma_tool
async def sigma_update_column_security_rule(
    data_model_id: str, element_id: str, column_security_rule_id: str, body: dict[str, Any]
) -> str:
    """Beta: Sigma marks this a beta feature. Replace a column-level security rule (PUT).

    body requires columns and criteria.
    """
    _require_ids(data_model_id=data_model_id, element_id=element_id, column_security_rule_id=column_security_rule_id)
    _require_body_fields(body, "columns", "criteria")
    result = await (await get_client()).update_column_security_rule(
        data_model_id, element_id, column_security_rule_id, body
    )
    return _paged(result)


update_column_security_rule = sigma_update_column_security_rule


@datasets_server.tool(
    name="delete_column_security_rule", annotations=ANNOTATION_DESTRUCTIVE, tags={"datasets", "destructive"}
)
@sigma_tool
async def sigma_delete_column_security_rule(
    data_model_id: str, element_id: str, column_security_rule_id: str, confirm: bool = False
) -> str:
    """Beta: Sigma marks this a beta feature. Delete a column-level security rule. Requires confirm=True."""
    if not confirm:
        return _confirm_required("This destructive operation")
    _require_ids(data_model_id=data_model_id, element_id=element_id, column_security_rule_id=column_security_rule_id)
    code = await (await get_client()).delete_column_security_rule(data_model_id, element_id, column_security_rule_id)
    return json.dumps({"status": "deleted", "statusCode": code})


delete_column_security_rule = sigma_delete_column_security_rule


@datasets_server.tool(
    name="create_data_model_materialization_schedule",
    annotations=ANNOTATION_WRITE_SAFE,
    tags={"datasets", "mutation"},
)
@sigma_tool
async def sigma_create_data_model_materialization_schedule(
    data_model_id: str, element_id: str, body: dict[str, Any]
) -> str:
    """Beta: Sigma marks this a beta feature. Requires Schedule materializations.

    Create a materialization schedule for a data model element. body requires schedule.
    """
    _require_ids(data_model_id=data_model_id, element_id=element_id)
    _require_body_fields(body, "schedule")
    result = await (await get_client()).create_data_model_materialization_schedule(data_model_id, element_id, body)
    return _paged(result)


create_data_model_materialization_schedule = sigma_create_data_model_materialization_schedule


@datasets_server.tool(
    name="update_data_model_materialization_schedule",
    annotations=ANNOTATION_WRITE_SAFE,
    tags={"datasets", "mutation"},
)
@sigma_tool
async def sigma_update_data_model_materialization_schedule(
    data_model_id: str, element_id: str, body: dict[str, Any]
) -> str:
    """Beta: Sigma marks this a beta feature. Requires Schedule materializations.

    Update a materialization schedule for a data model element. body requires schedule.
    """
    _require_ids(data_model_id=data_model_id, element_id=element_id)
    _require_body_fields(body, "schedule")
    result = await (await get_client()).update_data_model_materialization_schedule(data_model_id, element_id, body)
    return _paged(result)


update_data_model_materialization_schedule = sigma_update_data_model_materialization_schedule


@datasets_server.tool(
    name="delete_data_model_materialization_schedule",
    annotations=ANNOTATION_DESTRUCTIVE,
    tags={"datasets", "destructive"},
)
@sigma_tool
async def sigma_delete_data_model_materialization_schedule(
    data_model_id: str, element_id: str, confirm: bool = False
) -> str:
    """Beta: Sigma marks this a beta feature. Requires Schedule materializations.

    Delete a materialization schedule for a data model element. Requires confirm=True.
    """
    if not confirm:
        return _confirm_required("This destructive operation")
    _require_ids(data_model_id=data_model_id, element_id=element_id)
    code = await (await get_client()).delete_data_model_materialization_schedule(data_model_id, element_id)
    return json.dumps({"status": "deleted", "statusCode": code})


delete_data_model_materialization_schedule = sigma_delete_data_model_materialization_schedule


@datasets_server.tool(
    name="get_data_model_element_query", annotations=ANNOTATION_READ_ONLY, tags={"datasets", "read_only"}
)
@sigma_tool
async def sigma_get_data_model_element_query(data_model_id: str, element_id: str, tag_name: str | None = None) -> str:
    """Get the SQL query for a data model element. tag_name selects a tagged version."""
    _require_ids(data_model_id=data_model_id, element_id=element_id)
    return _paged(await (await get_client()).get_data_model_element_query(data_model_id, element_id, tag_name))


get_data_model_element_query = sigma_get_data_model_element_query


@datasets_server.tool(name="migrate_dataset", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"})
@sigma_tool
async def sigma_migrate_dataset(dataset_id: str, body: dict[str, Any]) -> str:
    """Beta: Sigma marks this a beta feature. Migrate a dataset to a data model.

    body requires shouldUpdateReferences, and may set destFolderId and dryRun.
    """
    _require_ids(dataset_id=dataset_id)
    _require_body_fields(body, "shouldUpdateReferences")
    return _paged(await (await get_client()).migrate_dataset(dataset_id, body))


migrate_dataset = sigma_migrate_dataset


@datasets_server.tool(name="upload_dbt_artifacts", annotations=ANNOTATION_WRITE_SAFE, tags={"datasets", "mutation"})
@sigma_tool
async def sigma_upload_dbt_artifacts(connection_id: str, artifacts_base64: str, filename: str = "target.tar.gz") -> str:
    """Beta: Sigma marks this a beta feature. Upload dbt Core run artifacts for a connection.

    artifacts_base64 is a base64-encoded tar.gz of the dbt project's target directory, sent as the
    multipart field artifacts. The decoded archive must be at most 10 MB.
    """
    _require_ids(connection_id=connection_id)
    if not filename or not filename.strip():
        _invalid_request("filename is required")
    decode_error = False
    try:
        raw = base64.b64decode(artifacts_base64, validate=True)
    except Exception:
        decode_error = True
        raw = b""
    if decode_error:
        _invalid_request("artifacts_base64 is not valid base64")
    if len(raw) > _DBT_ARTIFACT_MAX_BYTES:
        _invalid_request(f"artifacts exceed the {_DBT_ARTIFACT_MAX_BYTES} byte cap")
    return _paged(await (await get_client()).add_dbt_metadata(connection_id, raw, filename))


upload_dbt_artifacts = sigma_upload_dbt_artifacts
