"""Server profiles and annotation-driven tool visibility.

Every profile mounts all five domain sub-servers (``workbooks``, ``datasets``,
``elements``, ``workspace``, ``admin``). Selected with ``--profile`` / ``SIGMA_MCP_PROFILE``:

* ``full`` — the complete catalog (bulk tools listed, refused at call time without the bulk env).
* ``readonly`` — every tool annotated ``readOnlyHint=True``.
* **Job (allowlist) profiles** — ``tools`` is an explicit set of client-visible tool names
  applied over the full mounted catalog (``analyst``, ``author``, ``modeler``, ``embed``,
  ``access_admin``). Filtering is tools-only: prompts and resources stay.

``readonly=True`` keeps only tools whose MCP ``readOnlyHint`` annotation is ``True``.
The annotation is the single source of truth for read-only classification; a missing
annotation or ``readOnlyHint`` other than ``True`` counts as a write (fail closed).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools import Tool
from fastmcp.utilities.versions import VersionSpec
from mcp.types import ToolAnnotations

ALL_DOMAINS: tuple[str, ...] = ("workbooks", "datasets", "elements", "workspace", "admin")

# Bulk destructive tools: listed in ``full`` but refused at call time by the admin domain
# guard unless SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1.
BULK_DESTRUCTIVE_TOOLS: frozenset[str] = frozenset({"admin_bulk_deactivate_members", "admin_bulk_remove_team_members"})

# Job profile ``analyst`` (50 tools), in catalog order.
ANALYST_TOOLS: frozenset[str] = frozenset(
    {
        "workbooks_list_workbooks",
        "workbooks_get_workbook",
        "workbooks_list_workbook_pages",
        "workbooks_export_workbook",
        "workbooks_list_workbook_schedules",
        "workbooks_add_workbook_schedule",
        "workbooks_delete_workbook_schedule",
        "workbooks_list_workbook_bookmarks",
        "workbooks_add_workbook_bookmark",
        "workbooks_get_workbook_tags",
        "workbooks_list_reports",
        "workbooks_get_report",
        "workbooks_export_report",
        "workbooks_list_report_schedules",
        "workbooks_create_report_schedule",
        "workbooks_export_and_download",
        "workbooks_list_workbooks_shared_with_member",
        "workbooks_list_all_workbooks",
        "workbooks_list_all_reports",
        "workbooks_download_query_export",
        "workbooks_list_workbook_agents",
        "workbooks_run_workbook_agent",
        "elements_list_workbook_page_elements",
        "elements_list_workbook_elements",
        "elements_list_workbook_columns",
        "elements_list_workbook_controls",
        "workspace_list_files",
        "workspace_list_tags",
        "workspace_list_workspaces",
        "workspace_list_all_files",
        "admin_get_current_user",
        "workbooks_send_workbook",
        "workbooks_update_workbook_schedule",
        "workbooks_update_workbook_bookmark",
        "workbooks_delete_workbook_bookmark",
        "workbooks_list_report_pages",
        "workbooks_list_report_page_elements",
        "workbooks_list_report_columns",
        "workbooks_list_report_controls",
        "workbooks_send_report",
        "workbooks_update_report_schedule",
        "workbooks_delete_report_schedule",
        "workspace_list_member_files",
        "workspace_list_recent_files",
        "workspace_get_file",
        "workspace_list_workbooks_for_tag",
        "workspace_favorite_file",
        "workspace_unfavorite_file",
        "workspace_list_favorites_for_member",
        "admin_list_member_schedules",
    }
)

# Job profile ``author`` (53 tools), in catalog order.
AUTHOR_TOOLS: frozenset[str] = frozenset(
    {
        "workbooks_list_workbooks",
        "workbooks_get_workbook",
        "workbooks_create_workbook",
        "workbooks_duplicate_workbook",
        "workbooks_list_workbook_pages",
        "workbooks_list_workbook_lineage",
        "workbooks_get_workbook_version_history",
        "workbooks_restore_workbook_version",
        "workbooks_convert_workbook_to_report",
        "workbooks_get_workbook_tags",
        "workbooks_tag_workbook",
        "workbooks_remove_workbook_tag",
        "workbooks_list_templates",
        "workbooks_get_template",
        "workbooks_create_workbook_from_template",
        "workbooks_save_template_from_workbook",
        "workbooks_list_reports",
        "workbooks_get_report",
        "workbooks_create_report",
        "workbooks_duplicate_report",
        "workbooks_list_report_sources",
        "workbooks_list_report_elements",
        "workbooks_list_report_queries",
        "workbooks_list_report_lineage",
        "workbooks_update_workbook_contents",
        "workbooks_verify_workbook_spec",
        "workbooks_update_report_contents",
        "workbooks_verify_report_spec",
        "elements_list_workbook_page_elements",
        "elements_list_workbook_elements",
        "elements_list_workbook_columns",
        "elements_list_workbook_queries",
        "elements_list_workbook_controls",
        "elements_get_element_query",
        "elements_get_element_columns",
        "elements_formula_pitfalls",
        "elements_search_docs",
        "elements_get_doc_page",
        "workspace_list_files",
        "admin_get_current_user",
        "workbooks_duplicate_tagged_workbook",
        "workbooks_duplicate_tagged_report",
        "workbooks_list_report_pages",
        "workbooks_list_report_page_elements",
        "workbooks_list_report_columns",
        "workbooks_get_report_element_query",
        "workbooks_list_report_controls",
        "workbooks_get_report_version_history",
        "workbooks_list_report_tags",
        "workbooks_tag_report",
        "workbooks_remove_report_tag",
        "elements_list_element_lineage",
        "workspace_get_file",
    }
)

# Job profile ``modeler`` (42 tools), in catalog order.
MODELER_TOOLS: frozenset[str] = frozenset(
    {
        "workbooks_list_workbooks",
        "workbooks_get_workbook",
        "workbooks_list_reports",
        "workbooks_get_report",
        "workbooks_list_report_sources",
        "datasets_list_connections",
        "datasets_get_connection",
        "datasets_list_columns_for_table",
        "datasets_list_data_models",
        "datasets_get_data_model",
        "datasets_get_data_model_spec",
        "datasets_create_data_model",
        "datasets_update_data_model",
        "datasets_list_data_model_elements",
        "datasets_list_data_model_columns",
        "datasets_swap_data_model_sources",
        "datasets_list_data_model_lineage",
        "datasets_tag_data_model",
        "datasets_swap_report_sources",
        "datasets_sync_all_tables_in_schema",
        "datasets_list_all_data_models",
        "elements_list_workbook_page_elements",
        "elements_list_workbook_elements",
        "elements_list_workbook_sources",
        "elements_swap_workbook_sources",
        "elements_get_element_query",
        "elements_get_element_columns",
        "elements_materialize_element",
        "elements_get_materialization_job",
        "elements_list_materialization_schedules",
        "elements_materialize_and_wait",
        "elements_list_all_input_tables",
        "admin_get_current_user",
        "datasets_create_connection",
        "datasets_delete_connection",
        "datasets_list_connection_paths",
        "datasets_lookup_connection_path",
        "datasets_list_data_model_sources",
        "datasets_materialize_data_model",
        "datasets_get_data_model_materialization",
        "datasets_list_data_model_materialization_schedules",
        "datasets_list_data_model_tags",
    }
)

# Job profile ``embed`` (72 tools), in catalog order.
EMBED_TOOLS: frozenset[str] = frozenset(
    {
        "workbooks_list_workbooks",
        "workbooks_get_workbook",
        "workbooks_list_workbook_grants",
        "workbooks_grant_workbook_access",
        "workbooks_list_workbook_embeds",
        "workbooks_create_workbook_embed",
        "workbooks_list_templates",
        "workbooks_get_template",
        "workbooks_create_workbook_from_template",
        "workbooks_list_shared_templates",
        "workbooks_accept_shared_template",
        "workbooks_deploy_template_to_folder",
        "workbooks_promote_workbook",
        "datasets_list_connections",
        "datasets_get_connection",
        "datasets_sync_connection",
        "datasets_test_connection",
        "datasets_list_data_models",
        "datasets_swap_data_model_sources",
        "datasets_bulk_sync_tenant_connections",
        "elements_list_workbook_sources",
        "elements_swap_workbook_sources",
        "elements_swap_template_sources",
        "elements_list_source_swap_policies",
        "elements_get_source_swap_policy",
        "elements_create_source_swap_policy",
        "workspace_list_files",
        "workspace_create_folder",
        "workspace_list_workspaces",
        "workspace_grant_workspace_access",
        "admin_list_deployments",
        "admin_get_deployment",
        "admin_create_deployment",
        "admin_archive_deployment",
        "admin_list_deployment_documents",
        "admin_add_deployment_documents",
        "admin_list_tenants",
        "admin_get_tenant",
        "admin_create_tenant",
        "admin_list_api_connectors",
        "admin_get_api_connector",
        "admin_get_current_user",
        "admin_list_user_attributes",
        "admin_create_user_attribute",
        "admin_set_user_attribute_for_tenants",
        "admin_get_user_attribute_tenants",
        "admin_update_user_attribute_for_tenants",
        "admin_delete_user_attribute_for_tenant",
        "admin_list_tenants_paginated",
        "admin_get_tenant_scoped_info",
        "workbooks_delete_workbook_embed",
        "workbooks_share_workbook_cross_org",
        "workbooks_delete_shared_template",
        "elements_update_source_swap_policy",
        "elements_delete_source_swap_policy",
        "admin_get_user_attribute",
        "admin_update_user_attribute",
        "admin_delete_user_attribute",
        "admin_update_deployment",
        "admin_remove_deployment_document",
        "admin_list_deployable_tenants",
        "admin_list_deployment_tenants",
        "admin_add_deployment_tenant",
        "admin_remove_deployment_tenant",
        "admin_update_tenant",
        "admin_delete_tenant",
        "admin_list_tenant_deployment_capabilities",
        "admin_batch_add_tenant_capabilities",
        "admin_batch_remove_tenant_capabilities",
        "admin_create_api_connector",
        "admin_update_api_connector",
        "admin_delete_api_connector",
    }
)

# Job profile ``access_admin`` (70 tools), in catalog order.
ACCESS_ADMIN_TOOLS: frozenset[str] = frozenset(
    {
        "workbooks_list_workbook_grants",
        "workbooks_grant_workbook_access",
        "workbooks_copy_workbook_to_member",
        "workbooks_reassign_workbook_ownership",
        "datasets_list_connection_grants",
        "datasets_add_connection_grant",
        "datasets_delete_connection_path_grant",
        "workspace_list_workspaces",
        "workspace_get_workspace",
        "workspace_create_workspace",
        "workspace_delete_workspace",
        "workspace_list_workspace_grants",
        "workspace_grant_workspace_access",
        "workspace_delete_workspace_grant",
        "admin_deactivate_member",
        "admin_list_members",
        "admin_get_member",
        "admin_create_member",
        "admin_update_member",
        "admin_get_current_user",
        "admin_list_member_teams",
        "admin_list_teams",
        "admin_get_team",
        "admin_create_team",
        "admin_delete_team",
        "admin_list_team_members",
        "admin_update_team_members",
        "admin_list_user_attributes",
        "admin_create_user_attribute",
        "admin_set_user_attribute_for_teams",
        "admin_get_user_attribute_users",
        "admin_get_user_attribute_teams",
        "admin_update_user_attribute_for_users",
        "admin_update_user_attribute_for_teams",
        "admin_delete_user_attribute_for_user",
        "admin_delete_user_attribute_for_team",
        "admin_list_account_types",
        "admin_list_grants",
        "admin_create_grant",
        "admin_onboard_member",
        "admin_bulk_assign_team_members",
        "admin_change_member_email",
        "admin_list_all_members",
        "admin_list_all_teams",
        "admin_list_org_workbook_agents",
        "admin_get_org_setting",
        "admin_update_org_setting",
        "admin_configure_org_ai",
        "admin_reset_org_email_branding",
        "admin_list_allowed_ips",
        "admin_add_allowed_ips",
        "admin_remove_allowed_ips",
        "workbooks_delete_workbook_grant",
        "workbooks_create_report_grant",
        "workbooks_delete_report_grant",
        "datasets_delete_connection_grant",
        "datasets_list_connection_path_grants",
        "datasets_add_connection_path_grant",
        "workspace_update_workspace",
        "admin_update_team",
        "admin_get_user_attribute",
        "admin_update_user_attribute",
        "admin_delete_user_attribute",
        "admin_set_user_attribute_for_users",
        "admin_create_account_type",
        "admin_delete_account_type",
        "admin_get_grant",
        "admin_delete_grant",
        "admin_list_member_schedules",
        "admin_revoke_member_tokens",
    }
)

# Tools in no job (allowlist) profile. They stay reachable in ``full``. Tests require every
# tool in ``full`` to be in a job profile or listed here, so a new tool is placed on purpose.
FULL_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "workspace_delete_file",
        "workspace_update_file",
        "workspace_create_tag",
        "workspace_delete_tag",
        "admin_list_translations",
        "admin_bulk_deactivate_members",
        "admin_bulk_remove_team_members",
        "admin_api_capabilities",
        "admin_list_recent_webhooks",
        "workspace_update_tag",
        "admin_list_api_credentials",
        "admin_get_api_credential",
        "admin_create_api_credential",
        "admin_update_api_credential",
        "admin_delete_api_credential",
        "admin_get_translation",
        "admin_get_translation_variant",
        "admin_create_translation",
        "admin_update_translation",
        "admin_update_translation_variant",
        "admin_delete_translation",
        "admin_delete_translation_variant",
    }
)


@dataclass(frozen=True)
class Profile:
    """A named server profile: the full catalog, read-only, or a tool-name allowlist."""

    name: str
    job: str
    tools: frozenset[str] | None = None
    readonly: bool = False

    @property
    def is_allowlist(self) -> bool:
        """True when the profile is an explicit tool-name allowlist."""
        return self.tools is not None


PROFILES: dict[str, Profile] = {
    profile.name: profile
    for profile in (
        Profile(
            name="full",
            job="Complete catalog: every tool, including bulk/destructive tools (still gated).",
        ),
        Profile(
            name="readonly",
            job="Auditor / safe exploration: every tool annotated readOnlyHint=True across all mounts.",
            readonly=True,
        ),
        Profile(
            name="analyst",
            job=(
                "Business user finds, reads, exports and schedules workbooks and reports, bookmarks views, and "
                "asks workbook agents."
            ),
            tools=ANALYST_TOOLS,
        ),
        Profile(
            name="author",
            job=(
                "Workbook author builds, versions and verifies workbooks, reports and templates, and inspects "
                "elements while editing."
            ),
            tools=AUTHOR_TOOLS,
        ),
        Profile(
            name="modeler",
            job=(
                "Data modeler maintains connections and data models, swaps workbook/report/model sources and "
                "materializes elements."
            ),
            tools=MODELER_TOOLS,
        ),
        Profile(
            name="embed",
            job=(
                "Embedded-analytics engineer provisions tenants and tenant dashboards: templates, deployments, "
                "source swaps, embeds, tenant user attributes and connection syncs."
            ),
            tools=EMBED_TOOLS,
        ),
        Profile(
            name="access_admin",
            job=(
                "Org admin onboards and offboards members, manages teams, grants and user attributes, "
                "workspace/workbook/connection access, and org security settings."
            ),
            tools=ACCESS_ADMIN_TOOLS,
        ),
    )
}

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def get_profile(name: str) -> Profile:
    """Return the named profile or raise ``ValueError`` listing valid profiles."""
    key = name.strip().lower()
    if key not in PROFILES:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown profile {name!r}; valid profiles: {valid}.")
    return PROFILES[key]


def validate_allowlist(profile: Profile, catalog: Collection[str]) -> frozenset[str]:
    """Return the profile allowlist, raising ``ValueError`` on any name not in ``catalog``."""
    allowlist = profile.tools or frozenset()
    unknown = sorted(allowlist - set(catalog))
    if unknown:
        raise ValueError(f"Profile {profile.name!r} allowlists tools not in the full catalog: {', '.join(unknown)}.")
    return allowlist


def is_read_only_tool(tool: Tool | None) -> bool:
    """Return True only for a tool annotated ``readOnlyHint=True`` (fail closed)."""
    if tool is None or tool.annotations is None:
        return False
    return tool.annotations.read_only_hint is True


class ReadOnlyToolFilter(Transform):
    """Keep only tools annotated ``readOnlyHint=True``; other component types pass through."""

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [tool for tool in tools if is_read_only_tool(tool)]

    async def get_tool(self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None) -> Tool | None:
        tool = await call_next(name, version=version)
        return tool if is_read_only_tool(tool) else None


class ReadOnlyAnnotations(Transform):
    """Annotate named synthetic discovery tools as ``readOnlyHint=True``.

    FastMCP's synthetic discovery tools (``search_tools``; Code Mode ``search`` /
    ``get_schema``) ship without annotations. They only read the catalog, so the
    server marks them read-only to keep discovery usable under the readonly gate.
    ``call_tool`` / ``execute`` are deliberately not annotated: the gate classifies
    ``call_tool`` by the tool it proxies, and ``execute`` stays refused under readonly.
    """

    def __init__(self, names: Collection[str]) -> None:
        self.names = frozenset(names)

    def _annotate(self, tool: Tool) -> Tool:
        if tool.name not in self.names:
            return tool
        return tool.model_copy(update={"annotations": READ_ONLY_ANNOTATIONS})

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._annotate(tool) for tool in tools]

    async def get_tool(self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None) -> Tool | None:
        tool = await call_next(name, version=version)
        return None if tool is None else self._annotate(tool)
