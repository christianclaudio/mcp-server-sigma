"""Tools added for client methods that had none (API coverage audit, 2026-10-20).

Each tool is checked on the wire: with a real ``SigmaClient`` over a mocked transport, the tool
sends the HTTP method, path and JSON body of its endpoint in Sigma's published OpenAPI spec.
Destructive tools make no request without ``confirm=True``; blank IDs and request bodies
missing a field the spec marks required fail with ``invalid_request`` before any request.
"""

from __future__ import annotations

import importlib
import json
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastmcp.exceptions import ToolError

from sigma_mcp import server
from sigma_mcp.client import SigmaClient

BASE = "https://api.sigmacomputing.com"

# (domain, tool, kwargs, HTTP method, path, JSON body or None, client returns a status code)
CASES: list[tuple[str, str, dict[str, Any], str, str, Any, bool]] = [
    (
        "workbooks",
        "delete_workbook_grant",
        {"workbook_id": "workbook-1", "grant_id": "grant-1", "confirm": True},
        "DELETE",
        "/v2/workbooks/workbook-1/grants/grant-1",
        None,
        True,
    ),
    (
        "workbooks",
        "delete_workbook_embed",
        {"workbook_id": "workbook-1", "embed_id": "embed-1", "confirm": True},
        "DELETE",
        "/v2/workbooks/workbook-1/embeds/embed-1",
        None,
        True,
    ),
    (
        "workbooks",
        "send_workbook",
        {"workbook_id": "workbook-1", "body": {"name": "New name"}},
        "POST",
        "/v2/workbooks/workbook-1/send",
        {"name": "New name"},
        False,
    ),
    (
        "workbooks",
        "update_workbook_schedule",
        {"workbook_id": "workbook-1", "schedule_id": "schedule-1", "body": {"name": "New name"}},
        "PATCH",
        "/v2/workbooks/workbook-1/schedules/schedule-1",
        {"name": "New name"},
        False,
    ),
    (
        "workbooks",
        "update_workbook_bookmark",
        {"workbook_id": "workbook-1", "bookmark_id": "bookmark-1", "body": {"name": "New name"}},
        "PATCH",
        "/v2/workbooks/workbook-1/bookmarks/bookmark-1",
        {"name": "New name"},
        False,
    ),
    (
        "workbooks",
        "delete_workbook_bookmark",
        {"workbook_id": "workbook-1", "bookmark_id": "bookmark-1", "confirm": True},
        "DELETE",
        "/v2/workbooks/workbook-1/bookmarks/bookmark-1",
        None,
        True,
    ),
    (
        "workbooks",
        "duplicate_tagged_workbook",
        {
            "workbook_id": "workbook-1",
            "tag_name": "tag_name-1",
            "destination_folder_id": "destination_folder-1",
            "name": "name-1",
        },
        "POST",
        "/v2/workbooks/workbook-1/tag/tag_name-1/copy",
        {"destinationFolderId": "destination_folder-1", "name": "name-1"},
        False,
    ),
    (
        "workbooks",
        "share_workbook_cross_org",
        {"workbook_id": "workbook-1", "body": {"orgSlugs": "v-orgSlugs"}},
        "POST",
        "/v2/workbooks/workbook-1/shareCrossOrg",
        {"orgSlugs": "v-orgSlugs"},
        False,
    ),
    (
        "workbooks",
        "duplicate_tagged_report",
        {
            "report_id": "report-1",
            "tag_name": "tag_name-1",
            "destination_folder_id": "destination_folder-1",
            "name": "name-1",
        },
        "POST",
        "/v2/reports/report-1/tag/tag_name-1/copy",
        {"destinationFolderId": "destination_folder-1", "name": "name-1"},
        False,
    ),
    ("workbooks", "list_report_pages", {"report_id": "report-1"}, "GET", "/v2/reports/report-1/pages", None, False),
    (
        "workbooks",
        "list_report_page_elements",
        {"report_id": "report-1", "page_id": "page-1"},
        "GET",
        "/v2/reports/report-1/pages/page-1/elements",
        None,
        False,
    ),
    ("workbooks", "list_report_columns", {"report_id": "report-1"}, "GET", "/v2/reports/report-1/columns", None, False),
    (
        "workbooks",
        "get_report_element_query",
        {"report_id": "report-1", "element_id": "element-1"},
        "GET",
        "/v2/reports/report-1/elements/element-1/query",
        None,
        False,
    ),
    (
        "workbooks",
        "list_report_controls",
        {"report_id": "report-1"},
        "GET",
        "/v2/reports/report-1/controls",
        None,
        False,
    ),
    (
        "workbooks",
        "get_report_version_history",
        {"report_id": "report-1"},
        "GET",
        "/v2/reports/report-1/version-history",
        None,
        False,
    ),
    (
        "workbooks",
        "send_report",
        {
            "report_id": "report-1",
            "body": {"targets": "v-targets", "config": "v-config", "attachments": "v-attachments"},
        },
        "POST",
        "/v2/reports/report-1/send",
        {"targets": "v-targets", "config": "v-config", "attachments": "v-attachments"},
        False,
    ),
    (
        "workbooks",
        "update_report_schedule",
        {"report_id": "report-1", "schedule_id": "schedule-1", "body": {"name": "New name"}},
        "PATCH",
        "/v2/reports/report-1/schedules/schedule-1",
        {"name": "New name"},
        False,
    ),
    (
        "workbooks",
        "delete_report_schedule",
        {"report_id": "report-1", "schedule_id": "schedule-1", "confirm": True},
        "DELETE",
        "/v2/reports/report-1/schedules/schedule-1",
        None,
        True,
    ),
    (
        "workbooks",
        "create_report_grant",
        {"report_id": "report-1", "body": {"grants": "v-grants"}},
        "POST",
        "/v2/reports/report-1/grants",
        {"grants": "v-grants"},
        False,
    ),
    (
        "workbooks",
        "delete_report_grant",
        {"report_id": "report-1", "grant_id": "grant-1", "confirm": True},
        "DELETE",
        "/v2/reports/report-1/grants/grant-1",
        None,
        True,
    ),
    ("workbooks", "list_report_tags", {"report_id": "report-1"}, "GET", "/v2/reports/report-1/tags", None, False),
    (
        "workbooks",
        "tag_report",
        {"report_id": "report-1", "tag_name": "tag_name-1"},
        "POST",
        "/v2/reports/tag",
        {"reportId": "report-1", "tagName": "tag_name-1"},
        False,
    ),
    (
        "workbooks",
        "remove_report_tag",
        {"report_id": "report-1", "tag_id": "tag-1", "confirm": True},
        "DELETE",
        "/v2/reports/report-1/tags/tag-1",
        None,
        True,
    ),
    (
        "workbooks",
        "delete_shared_template",
        {"share_id": "share-1", "confirm": True},
        "DELETE",
        "/v2/shared_templates/share-1",
        None,
        True,
    ),
    (
        "elements",
        "list_element_lineage",
        {"workbook_id": "workbook-1", "element_id": "element-1"},
        "GET",
        "/v2/workbooks/workbook-1/lineage/elements/element-1",
        None,
        False,
    ),
    (
        "elements",
        "update_source_swap_policy",
        {"policy_id": "policy-1", "body": {"name": "New name"}},
        "PATCH",
        "/v2/sourceSwapPolicies/policy-1",
        {"name": "New name"},
        False,
    ),
    (
        "elements",
        "delete_source_swap_policy",
        {"policy_id": "policy-1", "confirm": True},
        "DELETE",
        "/v2/sourceSwapPolicies/policy-1",
        None,
        True,
    ),
    (
        "datasets",
        "create_connection",
        {"body": {"name": "v-name", "details": "v-details"}},
        "POST",
        "/v2/connections",
        {"name": "v-name", "details": "v-details"},
        False,
    ),
    (
        "datasets",
        "delete_connection",
        {"connection_id": "connection-1", "confirm": True},
        "DELETE",
        "/v2/connections/connection-1",
        None,
        True,
    ),
    ("datasets", "list_connection_paths", {}, "GET", "/v2/connections/paths", None, False),
    (
        "datasets",
        "lookup_connection_path",
        {"connection_id": "connection-1", "path": ["DB", "SCHEMA", "TABLE"]},
        "POST",
        "/v2/connection/connection-1/lookup",
        {"path": ["DB", "SCHEMA", "TABLE"]},
        False,
    ),
    (
        "datasets",
        "delete_connection_grant",
        {"connection_id": "connection-1", "grant_id": "grant-1", "confirm": True},
        "DELETE",
        "/v2/connections/connection-1/grants/grant-1",
        None,
        True,
    ),
    (
        "datasets",
        "list_connection_path_grants",
        {"connection_path_id": "connection_path-1"},
        "GET",
        "/v2/connections/paths/connection_path-1/grants",
        None,
        False,
    ),
    (
        "datasets",
        "add_connection_path_grant",
        {"connection_path_id": "connection_path-1", "body": {"grants": "v-grants"}},
        "POST",
        "/v2/connections/paths/connection_path-1/grants",
        {"grants": "v-grants"},
        False,
    ),
    (
        "datasets",
        "list_data_model_sources",
        {"data_model_id": "data_model-1"},
        "GET",
        "/v2/dataModels/data_model-1/sources",
        None,
        False,
    ),
    (
        "datasets",
        "materialize_data_model",
        {"data_model_id": "data_model-1", "sheet_id": "sheet-1"},
        "POST",
        "/v2/dataModels/data_model-1:materialize",
        {"sheetId": "sheet-1"},
        False,
    ),
    (
        "datasets",
        "get_data_model_materialization",
        {"data_model_id": "data_model-1", "materialization_id": "materialization-1"},
        "GET",
        "/v2/dataModels/data_model-1/materializations/materialization-1",
        None,
        False,
    ),
    (
        "datasets",
        "list_data_model_materialization_schedules",
        {"data_model_id": "data_model-1"},
        "GET",
        "/v2/dataModels/data_model-1/materializationSchedules",
        None,
        False,
    ),
    (
        "datasets",
        "list_data_model_tags",
        {"data_model_id": "data_model-1"},
        "GET",
        "/v2/dataModels/data_model-1/tags",
        None,
        False,
    ),
    ("workspace", "list_member_files", {"member_id": "member-1"}, "GET", "/v2/members/member-1/files", None, False),
    (
        "workspace",
        "list_recent_files",
        {"member_id": "member-1"},
        "GET",
        "/v2/members/member-1/files/recents",
        None,
        False,
    ),
    ("workspace", "get_file", {"inode_id": "inode-1"}, "GET", "/v2/files/inode-1", None, False),
    (
        "workspace",
        "update_tag",
        {"tag_id": "tag-1", "body": {"name": "New name"}},
        "PATCH",
        "/v2/tags/tag-1",
        {"name": "New name"},
        False,
    ),
    ("workspace", "list_workbooks_for_tag", {"tag_id": "tag-1"}, "GET", "/v2/tags/tag-1/workbooks", None, False),
    (
        "workspace",
        "update_workspace",
        {"workspace_id": "workspace-1", "body": {"name": "v-name"}},
        "PATCH",
        "/v2/workspaces/workspace-1",
        {"name": "v-name"},
        False,
    ),
    (
        "workspace",
        "favorite_file",
        {"member_id": "member-1", "inode_id": "inode-1"},
        "POST",
        "/v2/favorites",
        {"memberId": "member-1", "inodeId": "inode-1"},
        False,
    ),
    (
        "workspace",
        "unfavorite_file",
        {"member_id": "member-1", "inode_id": "inode-1", "confirm": True},
        "DELETE",
        "/v2/favorites/member/member-1/file/inode-1",
        None,
        True,
    ),
    (
        "workspace",
        "list_favorites_for_member",
        {"member_id": "member-1"},
        "GET",
        "/v2/favorites/member/member-1",
        None,
        False,
    ),
    (
        "admin",
        "update_team",
        {"team_id": "team-1", "body": {"name": "New name"}},
        "PATCH",
        "/v2/teams/team-1",
        {"name": "New name"},
        False,
    ),
    (
        "admin",
        "get_user_attribute",
        {"attribute_id": "attribute-1"},
        "GET",
        "/v2/user-attributes/attribute-1",
        None,
        False,
    ),
    (
        "admin",
        "update_user_attribute",
        {"attribute_id": "attribute-1", "body": {"name": "New name"}},
        "PATCH",
        "/v2/user-attributes/attribute-1",
        {"name": "New name"},
        False,
    ),
    (
        "admin",
        "delete_user_attribute",
        {"attribute_id": "attribute-1", "confirm": True},
        "DELETE",
        "/v2/user-attributes/attribute-1",
        None,
        True,
    ),
    (
        "admin",
        "set_user_attribute_for_users",
        {"attribute_id": "attribute-1", "assignments": [{"userId": "u-1", "value": "US"}]},
        "POST",
        "/v2/user-attributes/attribute-1/users",
        {"assignments": [{"userId": "u-1", "value": {"type": "string", "val": "US"}}]},
        False,
    ),
    (
        "admin",
        "create_account_type",
        {"body": {"name": "v-name", "description": "v-description", "permissions": "v-permissions"}},
        "POST",
        "/v2/accountTypes",
        {"name": "v-name", "description": "v-description", "permissions": "v-permissions"},
        False,
    ),
    (
        "admin",
        "delete_account_type",
        {"account_type_id": "account_type-1", "confirm": True},
        "DELETE",
        "/v2/accountTypes/account_type-1",
        None,
        True,
    ),
    ("admin", "get_grant", {"grant_id": "grant-1"}, "GET", "/v2/grants/grant-1", None, False),
    ("admin", "delete_grant", {"grant_id": "grant-1", "confirm": True}, "DELETE", "/v2/grants/grant-1", None, True),
    (
        "admin",
        "update_deployment",
        {"policy_id": "policy-1", "body": {"name": "New name"}},
        "PATCH",
        "/v2/deploymentPolicies/policy-1",
        {"name": "New name"},
        False,
    ),
    (
        "admin",
        "remove_deployment_document",
        {"policy_id": "policy-1", "inode_id": "inode-1", "confirm": True},
        "DELETE",
        "/v2/deploymentPolicies/policy-1/files/inode-1",
        None,
        True,
    ),
    ("admin", "list_deployable_tenants", {}, "GET", "/v2/deploymentPolicies/tenants", None, False),
    (
        "admin",
        "list_deployment_tenants",
        {"policy_id": "policy-1"},
        "GET",
        "/v2/deploymentPolicies/policy-1/tenants",
        None,
        False,
    ),
    (
        "admin",
        "add_deployment_tenant",
        {"policy_id": "policy-1", "body": {"tenantOrganizationId": "v-tenantOrganizationId"}},
        "POST",
        "/v2/deploymentPolicies/policy-1/tenants",
        {"tenantOrganizationId": "v-tenantOrganizationId"},
        False,
    ),
    (
        "admin",
        "remove_deployment_tenant",
        {"policy_id": "policy-1", "tenant_org_id": "tenant_org-1", "confirm": True},
        "DELETE",
        "/v2/deploymentPolicies/policy-1/tenants/tenant_org-1",
        None,
        True,
    ),
    (
        "admin",
        "update_tenant",
        {"tenant_id": "tenant-1", "body": {"name": "New name"}},
        "PATCH",
        "/v2/tenants/tenant-1",
        {"name": "New name"},
        False,
    ),
    (
        "admin",
        "delete_tenant",
        {"tenant_id": "tenant-1", "confirm": True},
        "DELETE",
        "/v2/tenants/tenant-1",
        None,
        True,
    ),
    (
        "admin",
        "list_tenant_deployment_capabilities",
        {"tenant_org_id": "tenant_org-1"},
        "GET",
        "/v2/tenants/tenant_org-1/capabilities/deployments",
        None,
        False,
    ),
    (
        "admin",
        "batch_add_tenant_capabilities",
        {"tenant_org_id": "tenant_org-1", "body": {"deployToTenantOrganizationIds": "v-deployToTenantOrganizationIds"}},
        "POST",
        "/v2/tenants/tenant_org-1/capabilities/deployments:batchAdd",
        {"deployToTenantOrganizationIds": "v-deployToTenantOrganizationIds"},
        False,
    ),
    (
        "admin",
        "batch_remove_tenant_capabilities",
        {
            "tenant_org_id": "tenant_org-1",
            "body": {"deployToTenantOrganizationIds": "v-deployToTenantOrganizationIds"},
            "confirm": True,
        },
        "POST",
        "/v2/tenants/tenant_org-1/capabilities/deployments:batchRemove",
        {"deployToTenantOrganizationIds": "v-deployToTenantOrganizationIds"},
        False,
    ),
    (
        "admin",
        "create_api_connector",
        {"body": {"name": "v-name", "params": "v-params"}},
        "POST",
        "/v2/api-connectors",
        {"name": "v-name", "params": "v-params"},
        False,
    ),
    (
        "admin",
        "update_api_connector",
        {"connector_id": "connector-1", "body": {"name": "New name"}},
        "PATCH",
        "/v2/api-connectors/connector-1",
        {"name": "New name"},
        False,
    ),
    (
        "admin",
        "delete_api_connector",
        {"connector_id": "connector-1", "confirm": True},
        "DELETE",
        "/v2/api-connectors/connector-1",
        None,
        True,
    ),
    ("admin", "list_api_credentials", {}, "GET", "/v2/api-credentials", None, False),
    (
        "admin",
        "get_api_credential",
        {"credential_id": "credential-1"},
        "GET",
        "/v2/api-credentials/credential-1",
        None,
        False,
    ),
    (
        "admin",
        "create_api_credential",
        {"body": {"name": "v-name", "allowlist": "v-allowlist", "credential": "v-credential"}},
        "POST",
        "/v2/api-credentials",
        {"name": "v-name", "allowlist": "v-allowlist", "credential": "v-credential"},
        False,
    ),
    (
        "admin",
        "update_api_credential",
        {"credential_id": "credential-1", "body": {"name": "New name"}},
        "PATCH",
        "/v2/api-credentials/credential-1",
        {"name": "New name"},
        False,
    ),
    (
        "admin",
        "delete_api_credential",
        {"credential_id": "credential-1", "confirm": True},
        "DELETE",
        "/v2/api-credentials/credential-1",
        None,
        True,
    ),
    ("admin", "get_translation", {"lng": "fr"}, "GET", "/v2/translations/organization/fr", None, False),
    (
        "admin",
        "get_translation_variant",
        {"lng": "fr", "variant": "formal"},
        "GET",
        "/v2/translations/organization/fr/formal",
        None,
        False,
    ),
    (
        "admin",
        "create_translation",
        {"lng": "fr", "body": {"name": "New name"}},
        "POST",
        "/v2/translations/organization",
        {"name": "New name", "lng": "fr"},
        False,
    ),
    (
        "admin",
        "update_translation",
        {"lng": "fr", "body": {"translations": "v-translations"}},
        "PUT",
        "/v2/translations/organization/fr",
        {"translations": "v-translations"},
        False,
    ),
    (
        "admin",
        "update_translation_variant",
        {"lng": "fr", "variant": "formal", "body": {"translations": "v-translations"}},
        "PUT",
        "/v2/translations/organization/fr/formal",
        {"translations": "v-translations"},
        False,
    ),
    (
        "admin",
        "delete_translation",
        {"lng": "fr", "confirm": True},
        "DELETE",
        "/v2/translations/organization/fr",
        None,
        True,
    ),
    (
        "admin",
        "delete_translation_variant",
        {"lng": "fr", "variant": "formal", "confirm": True},
        "DELETE",
        "/v2/translations/organization/fr/formal",
        None,
        True,
    ),
    ("admin", "list_member_schedules", {"member_id": "member-1"}, "GET", "/v2/members/member-1/schedules", None, False),
    (
        "admin",
        "revoke_member_tokens",
        {"member_id": "member-1", "confirm": True},
        "POST",
        "/v2/members/member-1/revoke",
        None,
        False,
    ),
]

ID_CASES: list[tuple[str, str]] = [
    ("workbooks", "delete_workbook_grant"),
    ("workbooks", "delete_workbook_embed"),
    ("workbooks", "send_workbook"),
    ("workbooks", "update_workbook_schedule"),
    ("workbooks", "update_workbook_bookmark"),
    ("workbooks", "delete_workbook_bookmark"),
    ("workbooks", "duplicate_tagged_workbook"),
    ("workbooks", "share_workbook_cross_org"),
    ("workbooks", "duplicate_tagged_report"),
    ("workbooks", "list_report_pages"),
    ("workbooks", "list_report_page_elements"),
    ("workbooks", "list_report_columns"),
    ("workbooks", "get_report_element_query"),
    ("workbooks", "list_report_controls"),
    ("workbooks", "get_report_version_history"),
    ("workbooks", "send_report"),
    ("workbooks", "update_report_schedule"),
    ("workbooks", "delete_report_schedule"),
    ("workbooks", "create_report_grant"),
    ("workbooks", "delete_report_grant"),
    ("workbooks", "list_report_tags"),
    ("workbooks", "tag_report"),
    ("workbooks", "remove_report_tag"),
    ("workbooks", "delete_shared_template"),
    ("elements", "list_element_lineage"),
    ("elements", "update_source_swap_policy"),
    ("elements", "delete_source_swap_policy"),
    ("datasets", "delete_connection"),
    ("datasets", "lookup_connection_path"),
    ("datasets", "delete_connection_grant"),
    ("datasets", "list_connection_path_grants"),
    ("datasets", "add_connection_path_grant"),
    ("datasets", "list_data_model_sources"),
    ("datasets", "materialize_data_model"),
    ("datasets", "get_data_model_materialization"),
    ("datasets", "list_data_model_materialization_schedules"),
    ("datasets", "list_data_model_tags"),
    ("workspace", "list_member_files"),
    ("workspace", "list_recent_files"),
    ("workspace", "get_file"),
    ("workspace", "update_tag"),
    ("workspace", "list_workbooks_for_tag"),
    ("workspace", "update_workspace"),
    ("workspace", "favorite_file"),
    ("workspace", "unfavorite_file"),
    ("workspace", "list_favorites_for_member"),
    ("admin", "update_team"),
    ("admin", "get_user_attribute"),
    ("admin", "update_user_attribute"),
    ("admin", "delete_user_attribute"),
    ("admin", "set_user_attribute_for_users"),
    ("admin", "delete_account_type"),
    ("admin", "get_grant"),
    ("admin", "delete_grant"),
    ("admin", "update_deployment"),
    ("admin", "remove_deployment_document"),
    ("admin", "list_deployment_tenants"),
    ("admin", "add_deployment_tenant"),
    ("admin", "remove_deployment_tenant"),
    ("admin", "update_tenant"),
    ("admin", "delete_tenant"),
    ("admin", "list_tenant_deployment_capabilities"),
    ("admin", "batch_add_tenant_capabilities"),
    ("admin", "batch_remove_tenant_capabilities"),
    ("admin", "update_api_connector"),
    ("admin", "delete_api_connector"),
    ("admin", "get_api_credential"),
    ("admin", "update_api_credential"),
    ("admin", "delete_api_credential"),
    ("admin", "get_translation"),
    ("admin", "get_translation_variant"),
    ("admin", "create_translation"),
    ("admin", "update_translation"),
    ("admin", "update_translation_variant"),
    ("admin", "delete_translation"),
    ("admin", "delete_translation_variant"),
    ("admin", "list_member_schedules"),
    ("admin", "revoke_member_tokens"),
]

REQUIRED_BODY_CASES: list[tuple[str, str]] = [
    ("workbooks", "share_workbook_cross_org"),
    ("workbooks", "send_report"),
    ("workbooks", "create_report_grant"),
    ("datasets", "create_connection"),
    ("datasets", "add_connection_path_grant"),
    ("workspace", "update_workspace"),
    ("admin", "create_account_type"),
    ("admin", "add_deployment_tenant"),
    ("admin", "batch_add_tenant_capabilities"),
    ("admin", "batch_remove_tenant_capabilities"),
    ("admin", "create_api_connector"),
    ("admin", "create_api_credential"),
    ("admin", "update_translation"),
    ("admin", "update_translation_variant"),
]

DESTRUCTIVE_CASES: list[tuple[str, str]] = [
    ("workbooks", "delete_workbook_grant"),
    ("workbooks", "delete_workbook_embed"),
    ("workbooks", "delete_workbook_bookmark"),
    ("workbooks", "delete_report_schedule"),
    ("workbooks", "delete_report_grant"),
    ("workbooks", "remove_report_tag"),
    ("workbooks", "delete_shared_template"),
    ("elements", "delete_source_swap_policy"),
    ("datasets", "delete_connection"),
    ("datasets", "delete_connection_grant"),
    ("workspace", "unfavorite_file"),
    ("admin", "delete_user_attribute"),
    ("admin", "delete_account_type"),
    ("admin", "delete_grant"),
    ("admin", "remove_deployment_document"),
    ("admin", "remove_deployment_tenant"),
    ("admin", "delete_tenant"),
    ("admin", "batch_remove_tenant_capabilities"),
    ("admin", "delete_api_connector"),
    ("admin", "delete_api_credential"),
    ("admin", "delete_translation"),
    ("admin", "delete_translation_variant"),
    ("admin", "revoke_member_tokens"),
]

_KWARGS = {(d, n): kw for d, n, kw, *_ in CASES}


def _tool(domain: str, name: str) -> Any:
    return getattr(importlib.import_module(f"sigma_mcp.tools.{domain}"), f"sigma_{name}")


@pytest.fixture
def http() -> Iterator[AsyncMock]:
    c = SigmaClient("cid", "csecret", BASE, max_retries=0)
    c._token = "tok"
    c._token_expiry = time.time() + 3600
    mock = AsyncMock()
    c._http = mock
    server._client = c
    yield mock
    server._client = None


def test_case_table_covers_every_new_tool() -> None:
    assert len(CASES) == 85
    assert len({(d, n) for d, n, *_ in CASES}) == 85


@pytest.mark.parametrize(("domain", "name", "kwargs", "method", "path", "body", "status_only"), CASES)
async def test_tool_calls_its_endpoint(
    http: AsyncMock,
    domain: str,
    name: str,
    kwargs: dict[str, Any],
    method: str,
    path: str,
    body: Any,
    status_only: bool,
) -> None:
    http.request.return_value = httpx.Response(204) if status_only else httpx.Response(200, json={"ok": True})
    out = json.loads(await _tool(domain, name)(**json.loads(json.dumps(kwargs))))
    args, kw = http.request.call_args
    assert args[0] == method
    assert args[1] == BASE + path
    assert kw["json"] == body
    if status_only:
        assert out == {"status": "deleted", "statusCode": 204}
    else:
        assert out == {"ok": True}


@pytest.mark.parametrize(("domain", "name"), DESTRUCTIVE_CASES)
async def test_destructive_tools_need_confirm(http: AsyncMock, domain: str, name: str) -> None:
    kwargs = {**_KWARGS[(domain, name)], "confirm": False}
    out = json.loads(await _tool(domain, name)(**kwargs))
    assert out["status"] == "confirmation_required"
    assert out["executed"] is False
    http.request.assert_not_called()


@pytest.mark.parametrize(("domain", "name"), ID_CASES)
async def test_blank_ids_are_rejected(http: AsyncMock, domain: str, name: str) -> None:
    kwargs = dict(_KWARGS[(domain, name)])
    first_id = next(k for k, v in kwargs.items() if isinstance(v, str))
    kwargs[first_id] = "  "
    with pytest.raises(ToolError, match=f"{first_id} is required"):
        await _tool(domain, name)(**kwargs)
    http.request.assert_not_called()


@pytest.mark.parametrize(("domain", "name"), REQUIRED_BODY_CASES)
async def test_missing_required_body_fields_are_rejected(http: AsyncMock, domain: str, name: str) -> None:
    kwargs = {**_KWARGS[(domain, name)], "body": {}}
    with pytest.raises(ToolError, match="body is missing required field"):
        await _tool(domain, name)(**kwargs)
    http.request.assert_not_called()


async def test_lookup_connection_path_rejects_empty_path(http: AsyncMock) -> None:
    for bad in ([], ["DB", " "]):
        with pytest.raises(ToolError, match="path must be a non-empty list"):
            await _tool("datasets", "lookup_connection_path")("connection-1", bad)
    http.request.assert_not_called()


async def test_set_user_attribute_for_users_rejects_empty_assignments(http: AsyncMock) -> None:
    with pytest.raises(ToolError, match="assignments must list"):
        await _tool("admin", "set_user_attribute_for_users")("attribute-1", [])
    http.request.assert_not_called()


async def test_duplicate_tagged_tools_send_optional_fields(http: AsyncMock) -> None:
    http.request.return_value = httpx.Response(200, json={"workbookId": "new"})
    await _tool("workbooks", "duplicate_tagged_workbook")(
        "wb-1", "Production", "folder-1", "Copy", description="d", copy_bookmarks=True
    )
    assert http.request.call_args[1]["json"] == {
        "destinationFolderId": "folder-1",
        "name": "Copy",
        "description": "d",
        "copyBookmarks": True,
    }
    await _tool("workbooks", "duplicate_tagged_report")("rep-1", "Production", "folder-1", "Copy", description="d")
    assert http.request.call_args[0][1] == f"{BASE}/v2/reports/rep-1/tag/Production/copy"
    assert http.request.call_args[1]["json"] == {"destinationFolderId": "folder-1", "name": "Copy", "description": "d"}


async def test_list_member_files_passes_limit(http: AsyncMock) -> None:
    http.request.return_value = httpx.Response(200, json={"entries": []})
    await _tool("workspace", "list_member_files")("member-1", limit=25)
    assert http.request.call_args[1]["params"] == {"limit": 25}


async def test_upstream_error_is_a_tool_error(http: AsyncMock) -> None:
    http.request.return_value = httpx.Response(404, json={"message": "not found"})
    with pytest.raises(ToolError):
        await _tool("admin", "get_grant")("grant-1")
