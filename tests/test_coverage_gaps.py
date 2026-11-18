"""New Sigma tools: method, path, query and body, plus confirm and secret gates.

Uses a real SigmaClient and an in-process httpx transport. Nothing leaves the process.
"""

from __future__ import annotations

import base64
import importlib
import json
import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastmcp.exceptions import ToolError

from sigma_mcp.client import SigmaClient
from sigma_mcp.errors import MASK

_NO_BODY = object()

# (module, function, kwargs, method, path, query, body, needs_confirm)
Case = tuple[str, str, dict[str, Any], str, str, dict[str, str], Any, bool]

_PAGE = {"entries": [{"id": "1"}], "nextPage": None, "nextPageToken": None, "total": 4, "hasMore": False}
_DETAILS = {"type": "snowflake", "account": "acct"}
_RULE = {"columns": ["amount"], "criteria": {"op": "eq"}}
_SCHEDULE = {"schedule": {"cron": "0 0 * * *"}}
_BOOKMARK = {"workbookVersion": 1, "name": "Saved", "isShared": True, "exploreKey": "k"}


def _cases() -> list[Case]:
    cred = {"ownerId": "member-1", "name": "reader", "description": "d", "scopes": ["read"]}
    storage = {"provider": "s3", "bucketName": "b", "pathPrefix": "p", "storageAwsRoleArn": "arn"}
    audit = {"provider": "s3", "allowedLocation": "s3://b", "storageAwsRoleArn": "arn"}
    return [
        (
            "admin",
            "sigma_list_client_credentials",
            {"page_token": "pt", "page_size": 25},
            "GET",
            "/v2/credentials",
            {"pageToken": "pt", "pageSize": "25"},
            _NO_BODY,
            False,
        ),
        (
            "admin",
            "sigma_create_client_credential",
            {"body": cred, "confirm": True},
            "POST",
            "/v2/credentials",
            {},
            cred,
            True,
        ),
        (
            "admin",
            "sigma_delete_client_credential",
            {"client_id": "client-1", "confirm": True},
            "DELETE",
            "/v2/credentials/client-1",
            {},
            _NO_BODY,
            True,
        ),
        (
            "admin",
            "sigma_list_json_web_keys",
            {"page_token": "pt", "page_size": 25},
            "GET",
            "/v2/jsonWebKeys",
            {"pageToken": "pt", "pageSize": "25"},
            _NO_BODY,
            False,
        ),
        (
            "admin",
            "sigma_list_jwk_set_keys",
            {"jwk_set_id": "set-1"},
            "GET",
            "/v2/jwkSets/set-1/jsonWebKeys",
            {},
            _NO_BODY,
            False,
        ),
        (
            "admin",
            "sigma_create_jwk_set",
            {"body": {"validForDays": 30}, "confirm": True},
            "POST",
            "/v2/jwkSets",
            {},
            {"validForDays": 30},
            True,
        ),
        (
            "admin",
            "sigma_create_json_web_key",
            {"jwk_set_id": "set-1", "body": {"validForDays": 7}, "confirm": True},
            "POST",
            "/v2/jwkSets/set-1/jsonWebKeys",
            {},
            {"validForDays": 7},
            True,
        ),
        (
            "admin",
            "sigma_activate_json_web_key",
            {"jwk_set_id": "set-1", "json_web_key_id": "key-1", "confirm": True},
            "POST",
            "/v2/jwkSets/set-1/jsonWebKeys/key-1:activate",
            {},
            {},
            True,
        ),
        (
            "admin",
            "sigma_delete_jwk_set",
            {"jwk_set_id": "set-1", "confirm": True},
            "DELETE",
            "/v2/jwkSets/set-1",
            {},
            _NO_BODY,
            True,
        ),
        (
            "admin",
            "sigma_delete_json_web_key",
            {"jwk_set_id": "set-1", "json_web_key_id": "key-1", "confirm": True},
            "DELETE",
            "/v2/jwkSets/set-1/jsonWebKeys/key-1",
            {},
            _NO_BODY,
            True,
        ),
        (
            "admin",
            "sigma_list_saml_service_providers",
            {"page_token": "pt", "page_size": 25},
            "GET",
            "/v2/saml/service-providers",
            {"pageToken": "pt", "pageSize": "25"},
            _NO_BODY,
            False,
        ),
        (
            "admin",
            "sigma_list_saml_certificates",
            {"saml_service_provider_id": "sp-1", "page_token": "pt", "page_size": 25},
            "GET",
            "/v2/saml/service-providers/sp-1/certificates",
            {"pageToken": "pt", "pageSize": "25"},
            _NO_BODY,
            False,
        ),
        (
            "admin",
            "sigma_get_saml_certificate",
            {"saml_service_provider_id": "sp-1", "certificate_id": "cert-1"},
            "GET",
            "/v2/saml/service-providers/sp-1/certificates/cert-1",
            {},
            _NO_BODY,
            False,
        ),
        (
            "admin",
            "sigma_create_saml_certificate",
            {"saml_service_provider_id": "sp-1", "body": {"purpose": "signing", "validForDays": 365}, "confirm": True},
            "POST",
            "/v2/saml/service-providers/sp-1/certificates",
            {},
            {"purpose": "signing", "validForDays": 365},
            True,
        ),
        (
            "admin",
            "sigma_activate_saml_certificate",
            {"saml_service_provider_id": "sp-1", "certificate_id": "cert-1", "confirm": True},
            "POST",
            "/v2/saml/service-providers/sp-1/certificates/cert-1/activate",
            {},
            {},
            True,
        ),
        (
            "admin",
            "sigma_deactivate_saml_certificate",
            {"saml_service_provider_id": "sp-1", "certificate_id": "cert-1", "confirm": True},
            "POST",
            "/v2/saml/service-providers/sp-1/certificates/cert-1/deactivate",
            {},
            {},
            True,
        ),
        (
            "admin",
            "sigma_delete_saml_certificate",
            {"saml_service_provider_id": "sp-1", "certificate_id": "cert-1", "confirm": True},
            "DELETE",
            "/v2/saml/service-providers/sp-1/certificates/cert-1",
            {},
            _NO_BODY,
            True,
        ),
        (
            "admin",
            "sigma_list_plugins",
            {"page_token": "pt", "page_size": 25},
            "GET",
            "/v2/plugins",
            {"pageToken": "pt", "pageSize": "25"},
            _NO_BODY,
            False,
        ),
        ("admin", "sigma_get_plugin", {"plugin_id": "plugin-1"}, "GET", "/v2/plugins/plugin-1", {}, _NO_BODY, False),
        (
            "admin",
            "sigma_create_plugin",
            {"body": {"name": "plug", "url": "https://example.com"}},
            "POST",
            "/v2/plugins",
            {},
            {"name": "plug", "url": "https://example.com"},
            False,
        ),
        (
            "admin",
            "sigma_update_plugin",
            {"plugin_id": "plugin-1", "body": {"name": "plug2"}},
            "PATCH",
            "/v2/plugins/plugin-1",
            {},
            {"name": "plug2"},
            False,
        ),
        (
            "admin",
            "sigma_delete_plugin",
            {"plugin_id": "plugin-1", "confirm": True},
            "DELETE",
            "/v2/plugins/plugin-1",
            {},
            _NO_BODY,
            True,
        ),
        (
            "admin",
            "sigma_reset_org_smtp",
            {"confirm": True},
            "DELETE",
            "/v2/organizations/settings/smtp",
            {},
            _NO_BODY,
            True,
        ),
        (
            "admin",
            "sigma_list_export_email_templates",
            {"page": "p9", "limit": 10},
            "GET",
            "/v2/organizations/settings/exportEmailTemplates",
            {"page": "p9", "limit": "10"},
            _NO_BODY,
            False,
        ),
        (
            "admin",
            "sigma_list_storage_integrations",
            {},
            "GET",
            "/v2/organizations/settings/storageIntegrations",
            {},
            _NO_BODY,
            False,
        ),
        (
            "admin",
            "sigma_create_storage_integration",
            {"body": storage, "confirm": True},
            "POST",
            "/v2/organizations/settings/storageIntegrations",
            {},
            storage,
            True,
        ),
        (
            "admin",
            "sigma_update_storage_integration",
            {"storage_integration_id": "si-1", "body": storage, "confirm": True},
            "PATCH",
            "/v2/organizations/settings/storageIntegrations/si-1",
            {},
            storage,
            True,
        ),
        (
            "admin",
            "sigma_delete_storage_integration",
            {"storage_integration_id": "si-1", "confirm": True},
            "DELETE",
            "/v2/organizations/settings/storageIntegrations/si-1",
            {},
            _NO_BODY,
            True,
        ),
        (
            "admin",
            "sigma_get_audit_log_storage",
            {},
            "GET",
            "/v2/organizations/settings/storageIntegration/auditLogging",
            {},
            _NO_BODY,
            False,
        ),
        (
            "admin",
            "sigma_create_audit_log_storage",
            {"body": audit, "confirm": True},
            "POST",
            "/v2/organizations/settings/storageIntegration/auditLogging",
            {},
            audit,
            True,
        ),
        (
            "admin",
            "sigma_update_audit_log_storage",
            {"body": {"allowedLocation": "s3://b", "updateScheduledExports": True}, "confirm": True},
            "PATCH",
            "/v2/organizations/settings/storageIntegration/auditLogging",
            {},
            {"allowedLocation": "s3://b", "updateScheduledExports": True},
            True,
        ),
        (
            "admin",
            "sigma_delete_audit_log_storage",
            {"delete_scheduled_exports": True, "confirm": True},
            "DELETE",
            "/v2/organizations/settings/storageIntegration/auditLogging",
            {"deleteScheduledExports": "true"},
            _NO_BODY,
            True,
        ),
        (
            "admin",
            "sigma_list_ai_budget_assignments",
            {
                "page_token": "pt",
                "page_size": 25,
                "assignment_type": "member",
                "member_id": "m-1",
                "team_id": "t-1",
                "account_type_id": "at-1",
            },
            "GET",
            "/v2/organizations/settings/aiBudgets/assignments",
            {
                "pageToken": "pt",
                "pageSize": "25",
                "type": "member",
                "memberId": "m-1",
                "teamId": "t-1",
                "accountTypeId": "at-1",
            },
            _NO_BODY,
            False,
        ),
        (
            "admin",
            "sigma_assign_ai_budget",
            {"body": {"assignee": {"type": "member", "memberId": "m-1"}, "monthlyBudgetCents": 100}, "confirm": True},
            "POST",
            "/v2/organizations/settings/aiBudgets/assignments",
            {},
            {"assignee": {"type": "member", "memberId": "m-1"}, "monthlyBudgetCents": 100},
            True,
        ),
        (
            "admin",
            "sigma_delete_ai_budget_assignment",
            {"assignment_id": "as-1", "confirm": True},
            "DELETE",
            "/v2/organizations/settings/aiBudgets/assignments/as-1",
            {},
            _NO_BODY,
            True,
        ),
        (
            "admin",
            "sigma_list_account_type_permissions",
            {"account_type_id": "at-1"},
            "GET",
            "/v2/accountTypes/at-1/permissions",
            {},
            _NO_BODY,
            False,
        ),
        (
            "admin",
            "sigma_update_account_type_permissions",
            {"account_type_id": "at-1", "body": {"add": ["a"], "remove": ["b"]}, "confirm": True},
            "PATCH",
            "/v2/accountTypes/at-1/permissions",
            {},
            {"add": ["a"], "remove": ["b"]},
            True,
        ),
        (
            "admin",
            "sigma_list_team_user_attributes",
            {"team_id": "t-1", "page": "p9", "limit": 10},
            "GET",
            "/v2/teams/t-1/user-attributes",
            {"page": "p9", "limit": "10"},
            _NO_BODY,
            False,
        ),
        (
            "admin",
            "sigma_list_tenant_workbooks",
            {"tenant_organization_id": "org-1", "parent_workbook_id": "wb-1", "deployment_policy_id": "dp-1"},
            "GET",
            "/v2/tenants/org-1/workbooks",
            {"parentWorkbookId": "wb-1", "deploymentPolicyId": "dp-1"},
            _NO_BODY,
            False,
        ),
        (
            "admin",
            "sigma_get_webhook_schema",
            {"workbook_id": "wb-1", "sequence_id": "seq-1"},
            "GET",
            "/v2/webhooks/wb-1/seq-1/schema",
            {},
            _NO_BODY,
            False,
        ),
        (
            "admin",
            "sigma_send_webhook",
            {"workbook_id": "wb-1", "sequence_id": "seq-1", "body": {"event": "ping"}, "confirm": True},
            "POST",
            "/v2/webhooks/wb-1/seq-1",
            {},
            {"event": "ping"},
            True,
        ),
        (
            "datasets",
            "sigma_update_connection",
            {"connection_id": "conn-1", "body": {"name": "wh", "details": _DETAILS, "restore": True}},
            "PUT",
            "/v2/connections/conn-1",
            {},
            {"name": "wh", "details": _DETAILS, "restore": True},
            False,
        ),
        (
            "datasets",
            "sigma_get_connection_path",
            {"inode_id": "inode-1"},
            "GET",
            "/v2/connections/paths/inode-1",
            {},
            _NO_BODY,
            False,
        ),
        (
            "datasets",
            "sigma_get_warehouse_connection",
            {"connection_id": "conn-1"},
            "GET",
            "/v3alpha/connections/conn-1",
            {},
            _NO_BODY,
            False,
        ),
        (
            "datasets",
            "sigma_create_warehouse_connection",
            {"body": {"name": "wh", "details": _DETAILS}},
            "POST",
            "/v3alpha/connections",
            {},
            {"name": "wh", "details": _DETAILS},
            False,
        ),
        (
            "datasets",
            "sigma_update_warehouse_connection",
            {"connection_id": "conn-1", "body": {"name": "wh2", "details": _DETAILS}},
            "PATCH",
            "/v3alpha/connections/conn-1",
            {},
            {"name": "wh2", "details": _DETAILS},
            False,
        ),
        (
            "datasets",
            "sigma_swap_data_model_source_mapping",
            {"data_model_id": "dm-1", "body": {"sourceMapping": [{"from": "a", "to": "b"}]}},
            "POST",
            "/v3alpha/dataModels/dm-1:swapSources",
            {},
            {"sourceMapping": [{"from": "a", "to": "b"}]},
            False,
        ),
        (
            "datasets",
            "sigma_list_column_security_rules",
            {"data_model_id": "dm-1", "element_id": "el-1", "page": "p9", "limit": 10},
            "GET",
            "/v2/dataModels/dm-1/elements/el-1/columnSecurityRules",
            {"page": "p9", "limit": "10"},
            _NO_BODY,
            False,
        ),
        (
            "datasets",
            "sigma_create_column_security_rules",
            {"data_model_id": "dm-1", "element_id": "el-1", "rules": [_RULE]},
            "POST",
            "/v2/dataModels/dm-1/elements/el-1/columnSecurityRules",
            {},
            [_RULE],
            False,
        ),
        (
            "datasets",
            "sigma_update_column_security_rule",
            {"data_model_id": "dm-1", "element_id": "el-1", "column_security_rule_id": "rule-1", "body": _RULE},
            "PUT",
            "/v2/dataModels/dm-1/elements/el-1/columnSecurityRules/rule-1",
            {},
            _RULE,
            False,
        ),
        (
            "datasets",
            "sigma_delete_column_security_rule",
            {"data_model_id": "dm-1", "element_id": "el-1", "column_security_rule_id": "rule-1", "confirm": True},
            "DELETE",
            "/v2/dataModels/dm-1/elements/el-1/columnSecurityRules/rule-1",
            {},
            _NO_BODY,
            True,
        ),
        (
            "datasets",
            "sigma_create_data_model_materialization_schedule",
            {"data_model_id": "dm-1", "element_id": "el-1", "body": _SCHEDULE},
            "POST",
            "/v2/dataModels/dm-1/elements/el-1/materializationSchedules",
            {},
            _SCHEDULE,
            False,
        ),
        (
            "datasets",
            "sigma_update_data_model_materialization_schedule",
            {"data_model_id": "dm-1", "element_id": "el-1", "body": _SCHEDULE},
            "PATCH",
            "/v2/dataModels/dm-1/elements/el-1/materializationSchedules",
            {},
            _SCHEDULE,
            False,
        ),
        (
            "datasets",
            "sigma_delete_data_model_materialization_schedule",
            {"data_model_id": "dm-1", "element_id": "el-1", "confirm": True},
            "DELETE",
            "/v2/dataModels/dm-1/elements/el-1/materializationSchedules",
            {},
            _NO_BODY,
            True,
        ),
        (
            "datasets",
            "sigma_get_data_model_element_query",
            {"data_model_id": "dm-1", "element_id": "el-1", "tag_name": "prod"},
            "GET",
            "/v2/dataModels/dm-1/elements/el-1/query",
            {"tagName": "prod"},
            _NO_BODY,
            False,
        ),
        (
            "datasets",
            "sigma_migrate_dataset",
            {"dataset_id": "ds-1", "body": {"shouldUpdateReferences": True, "destFolderId": "f-1"}},
            "POST",
            "/v2/datasets/ds-1/migrate",
            {},
            {"shouldUpdateReferences": True, "destFolderId": "f-1"},
            False,
        ),
        (
            "elements",
            "sigma_create_materialization_schedule",
            {"workbook_id": "wb-1", "element_id": "el-1", "body": _SCHEDULE},
            "POST",
            "/v2/workbooks/wb-1/elements/el-1/materializationSchedules",
            {},
            _SCHEDULE,
            False,
        ),
        (
            "elements",
            "sigma_update_materialization_schedule",
            {"workbook_id": "wb-1", "element_id": "el-1", "body": _SCHEDULE},
            "PATCH",
            "/v2/workbooks/wb-1/elements/el-1/materializationSchedules",
            {},
            _SCHEDULE,
            False,
        ),
        (
            "elements",
            "sigma_delete_materialization_schedule",
            {"workbook_id": "wb-1", "element_id": "el-1", "confirm": True},
            "DELETE",
            "/v2/workbooks/wb-1/elements/el-1/materializationSchedules",
            {},
            _NO_BODY,
            True,
        ),
        (
            "elements",
            "sigma_swap_workbook_source_mapping",
            {"workbook_id": "wb-1", "body": {"sourceMapping": [{"from": "a", "to": "b"}]}},
            "POST",
            "/v3alpha/workbooks/wb-1:swapSources",
            {},
            {"sourceMapping": [{"from": "a", "to": "b"}]},
            False,
        ),
        (
            "workbooks",
            "sigma_get_workbook_bookmark",
            {"workbook_id": "wb-1", "bookmark_id": "bm-1"},
            "GET",
            "/v2/workbooks/wb-1/bookmarks/bm-1",
            {},
            _NO_BODY,
            False,
        ),
        (
            "workbooks",
            "sigma_list_tagged_workbook_bookmarks",
            {"workbook_id": "wb-1", "tag_name": "prod", "page": "p9", "limit": 10},
            "GET",
            "/v2/workbooks/wb-1/tags/prod/bookmarks",
            {"page": "p9", "limit": "10"},
            _NO_BODY,
            False,
        ),
        (
            "workbooks",
            "sigma_get_tagged_workbook_bookmark",
            {"workbook_id": "wb-1", "tag_name": "prod", "bookmark_id": "bm-1"},
            "GET",
            "/v2/workbooks/wb-1/tags/prod/bookmarks/bm-1",
            {},
            _NO_BODY,
            False,
        ),
        (
            "workbooks",
            "sigma_add_tagged_workbook_bookmark",
            {"workbook_id": "wb-1", "tag_name": "prod", "body": _BOOKMARK},
            "POST",
            "/v2/workbooks/wb-1/tags/prod/bookmarks/",
            {},
            _BOOKMARK,
            False,
        ),
        (
            "workbooks",
            "sigma_update_tagged_workbook_bookmark",
            {
                "workbook_id": "wb-1",
                "tag_name": "prod",
                "bookmark_id": "bm-1",
                "body": {"name": "Renamed", "isShared": False},
            },
            "PATCH",
            "/v2/workbooks/wb-1/tags/prod/bookmarks/bm-1",
            {},
            {"name": "Renamed", "isShared": False},
            False,
        ),
        (
            "workbooks",
            "sigma_delete_tagged_workbook_bookmark",
            {"workbook_id": "wb-1", "tag_name": "prod", "bookmark_id": "bm-1", "confirm": True},
            "DELETE",
            "/v2/workbooks/wb-1/tags/prod/bookmarks/bm-1",
            {},
            _NO_BODY,
            True,
        ),
        (
            "workbooks",
            "sigma_get_report_element_columns",
            {"report_id": "r-1", "element_id": "el-1", "page_token": "pt", "page_size": 25, "tag_name": "prod"},
            "GET",
            "/v2/reports/r-1/elements/el-1/columns",
            {"pageToken": "pt", "pageSize": "25", "tagName": "prod"},
            _NO_BODY,
            False,
        ),
        (
            "workbooks",
            "sigma_list_report_element_lineage",
            {"report_id": "r-1", "element_id": "el-1"},
            "GET",
            "/v2/reports/r-1/lineage/elements/el-1",
            {},
            _NO_BODY,
            False,
        ),
        (
            "workspace",
            "sigma_list_member_favorite_files",
            {"member_id": "m-1", "page": "p9", "limit": 10},
            "GET",
            "/v2/members/m-1/files/favorites",
            {"page": "p9", "limit": "10"},
            _NO_BODY,
            False,
        ),
        (
            "workspace",
            "sigma_create_shortcut",
            {"body": {"parentId": "folder-1", "target": {"id": "wb-1"}, "name": "Link"}},
            "POST",
            "/v2/shortcuts",
            {},
            {"parentId": "folder-1", "target": {"id": "wb-1"}, "name": "Link"},
            False,
        ),
    ]


CASES = _cases()


def _tool(module: str, name: str) -> Any:
    return getattr(importlib.import_module(f"sigma_mcp.tools.{module}"), name)


@pytest.fixture
def seen() -> Iterator[list[httpx.Request]]:
    from sigma_mcp import server

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.content and "application/json" in request.headers.get("content-type", ""):
            payload: Any = json.loads(request.content)
        else:
            payload = dict(_PAGE)
        return httpx.Response(200, json=payload)

    client = SigmaClient("cid", "csecret", "https://api.sigmacomputing.com", max_retries=0, base_delay=0)
    client._token = "tok"
    client._token_expiry = time.time() + 3600
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    server._client = client
    yield captured
    server._client = None


def test_case_count_is_the_new_tool_surface() -> None:
    assert len(CASES) == 70  # upload_dbt_artifacts is covered by its own multipart test


@pytest.mark.parametrize(("module", "fn", "kwargs", "method", "path", "query", "body", "_confirm"), CASES)
async def test_tool_hits_the_spec_operation(
    seen: list[httpx.Request],
    module: str,
    fn: str,
    kwargs: dict[str, Any],
    method: str,
    path: str,
    query: dict[str, str],
    body: Any,
    _confirm: bool,
) -> None:
    await _tool(module, fn)(**kwargs)
    assert len(seen) == 1
    request = seen[0]
    assert request.method == method
    assert request.url.path == path
    assert dict(request.url.params) == query
    if body is _NO_BODY:
        assert request.content in (b"", b"null")
    else:
        assert json.loads(request.content) == body


@pytest.mark.parametrize(
    ("module", "fn", "kwargs"),
    [(module, fn, kwargs) for module, fn, kwargs, *_rest in CASES if _rest[-1]],
)
async def test_guarded_write_refuses_without_confirm(
    seen: list[httpx.Request], module: str, fn: str, kwargs: dict[str, Any]
) -> None:
    refused = dict(kwargs)
    refused["confirm"] = False
    result = await _tool(module, fn)(**refused)
    assert json.loads(result)["status"] == "confirmation_required"
    assert seen == []


async def test_shortcut_requires_target_id(seen: list[httpx.Request]) -> None:
    with pytest.raises(ToolError, match="target.id"):
        await _tool("workspace", "sigma_create_shortcut")({"parentId": "folder-1", "target": {}})
    assert seen == []


async def test_smtp_password_is_masked_and_other_settings_are_not(seen: list[httpx.Request]) -> None:
    from sigma_mcp.tools.admin import sigma_update_org_setting

    secret = "dummy-smtp-password"
    out = await sigma_update_org_setting(
        "smtp",
        {"host": "smtp.example", "password": secret},
        confirm=True,
    )
    assert secret not in out
    assert MASK in out
    assert "smtp.example" in out
    seen.clear()
    plain = await sigma_update_org_setting("timezone", {"timezone": "UTC", "password": secret}, confirm=True)
    echoed = json.loads(plain)
    assert echoed["timezone"] == "UTC"
    assert echoed["password"] == secret


async def test_connection_details_are_masked(seen: list[httpx.Request]) -> None:
    from sigma_mcp.tools.datasets import (
        sigma_create_warehouse_connection,
        sigma_update_connection,
        sigma_update_warehouse_connection,
    )

    secret = "dummy-password-value"
    body = {"name": "wh", "details": {"password": secret, "account": "acct"}}
    for call in (
        sigma_update_connection("conn-1", body),
        sigma_create_warehouse_connection(body),
        sigma_update_warehouse_connection("conn-1", body),
    ):
        out = await call
        assert secret not in out
        assert MASK in out
    assert [request.method for request in seen] == ["PUT", "POST", "PATCH"]


async def test_client_credential_secret_is_returned_once_and_not_logged(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sigma_mcp import server
    from sigma_mcp.tools.admin import sigma_create_client_credential

    dummy = "dummy-client-secret-value"
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"clientId": "client-1", "clientSecret": dummy})

    client = SigmaClient("cid", "csecret", "https://api.sigmacomputing.com", max_retries=0, base_delay=0)
    client._token = "tok"
    client._token_expiry = time.time() + 3600
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(server, "_client", client)
    with caplog.at_level(logging.DEBUG, logger="sigma_mcp"):
        out = await sigma_create_client_credential({"ownerId": "member-1", "name": "reader"}, confirm=True)
    assert json.loads(out)["clientSecret"] == dummy
    assert dummy not in caplog.text
    assert captured[0].method == "POST"
    assert captured[0].url.path == "/v2/credentials"


async def test_audit_log_delete_reports_sigma_202(monkeypatch: pytest.MonkeyPatch) -> None:
    from sigma_mcp import server
    from sigma_mcp.tools.admin import sigma_delete_audit_log_storage

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["deleteScheduledExports"] == "false"
        return httpx.Response(202, json={})

    client = SigmaClient("cid", "csecret", "https://api.sigmacomputing.com", max_retries=0, base_delay=0)
    client._token = "tok"
    client._token_expiry = time.time() + 3600
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(server, "_client", client)
    out = json.loads(await sigma_delete_audit_log_storage(delete_scheduled_exports=False, confirm=True))
    assert out == {"status": "deleted", "statusCode": 202}


async def test_upload_dbt_artifacts_sends_multipart_and_enforces_the_cap(seen: list[httpx.Request]) -> None:
    from sigma_mcp.tools.datasets import sigma_upload_dbt_artifacts

    raw = b"dummy-tar-gz"
    out = await sigma_upload_dbt_artifacts("conn-1", base64.b64encode(raw).decode("ascii"), "target.tar.gz")
    assert json.loads(out) == {} or isinstance(json.loads(out), dict)
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/v2/connections/conn-1/dbtArtifacts"
    assert "multipart/form-data" in request.headers["content-type"]
    assert b'name="artifacts"' in request.content
    assert b"target.tar.gz" in request.content
    assert raw in request.content
    seen.clear()
    with pytest.raises(ToolError, match="not valid base64"):
        await sigma_upload_dbt_artifacts("conn-1", "!!!", "target.tar.gz")
    with pytest.raises(ToolError, match="filename is required"):
        await sigma_upload_dbt_artifacts("conn-1", base64.b64encode(raw).decode("ascii"), "  ")
    huge = base64.b64encode(b"a" * (10_000_000 + 1)).decode("ascii")
    with pytest.raises(ToolError, match="10_?000_?000|10000000"):
        await sigma_upload_dbt_artifacts("conn-1", huge, "target.tar.gz")
    assert seen == []


async def test_list_all_members_and_teams_follow_v21() -> None:
    from sigma_mcp.client import SigmaClient as Client

    calls: list[tuple[str, dict[str, Any]]] = []
    pages = {
        "/v2.1/members": [
            {"entries": [{"memberId": "m1"}], "nextPage": "p2", "total": 2, "hasMore": True},
            {"entries": [{"memberId": "m2"}], "nextPage": None, "total": 2, "hasMore": False},
        ],
        "/v2.1/teams": [
            {"entries": [{"teamId": "t1"}], "nextPage": None, "total": 1, "hasMore": False},
        ],
    }

    client = Client("cid", "csecret", "https://api.sigmacomputing.com", max_retries=0, base_delay=0)
    client._token = "tok"
    client._token_expiry = time.time() + 3600

    async def fake_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((path, dict(params or {})))
        return pages[path].pop(0)

    client.get = fake_get  # type: ignore[method-assign]
    assert await client.list_all_members() == [{"memberId": "m1"}, {"memberId": "m2"}]
    assert await client.list_all_teams() == [{"teamId": "t1"}]
    assert calls[0] == ("/v2.1/members", {"limit": 200})
    assert calls[1][0] == "/v2.1/members"
    assert calls[1][1]["page"] == "p2"
    assert calls[2] == ("/v2.1/teams", {"limit": 200})


async def test_bulk_deactivate_lists_v21_members_with_pagination() -> None:
    from sigma_mcp import server
    from sigma_mcp.tools.admin import sigma_bulk_deactivate_members

    calls: list[tuple[str, dict[str, Any]]] = []
    pages = [
        {
            "entries": [
                {
                    "memberId": "m1",
                    "firstName": "Ann",
                    "lastName": "Lee",
                    "email": "ann@example.com",
                    "isInactive": False,
                    "isActive": True,
                }
            ],
            "nextPage": "p2",
            "total": 1,
            "hasMore": True,
        },
        {"entries": [], "nextPage": None, "total": 1, "hasMore": False},
    ]
    client = SigmaClient("cid", "csecret", "https://api.sigmacomputing.com", max_retries=0, base_delay=0)
    client._token = "tok"
    client._token_expiry = time.time() + 3600

    async def fake_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((path, dict(params or {})))
        return pages.pop(0)

    client.get = fake_get  # type: ignore[method-assign]
    server._client = client
    try:
        out = json.loads(await sigma_bulk_deactivate_members("Lee", dry_run=True))
    finally:
        server._client = None
    assert out["would_deactivate"] == 1
    assert calls[0][0] == "/v2.1/members"
    assert calls[0][1]["includeInactive"] == "true"
    assert calls[1][0] == "/v2.1/members"
    assert calls[1][1]["page"] == "p2"
