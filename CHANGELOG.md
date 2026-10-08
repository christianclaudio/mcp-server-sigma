# Changelog

> **This file is frozen as of 1.2.2. Release notes now live on [GitHub Releases](https://github.com/christianclaudio/mcp-server-sigma/releases).**
> Each release body is generated from the squash commits since the previous tag by `scripts/release_notes.py`, including every `BREAKING CHANGE:` footer and its migration steps. Do not add entries here; the history below is kept for reference.

All notable changes through 1.2.2 are documented in this file. The `[Unreleased]`, `2.0.1` and `2.0.0` entries were pending at the freeze: 2.0.0 and 2.0.1 were never tagged or published, so all three ship in the first release after 1.2.2.

## [Unreleased]

*Frozen: these entries were pending at the freeze. They are carried into the first GitHub Release after 1.2.2; later changes are listed on [GitHub Releases](https://github.com/christianclaudio/mcp-server-sigma/releases).*

### Breaking Changes
- **Read-only fails closed on `readOnlyHint` alone**: `ReadOnlyGateMiddleware` no longer matches tool-name prefixes or the `_RO_NAMES` list (`middleware.is_read_only_tool(name)` is removed; `profiles.is_read_only_tool(tool)` reads the annotation). Under `--profile readonly` or `SIGMA_MCP_READONLY=1` it refuses any real tool not annotated `readOnlyHint=True` (a missing annotation counts as a write), including writes the read-only filter hid. The read-only listing now filters on the annotation instead of the `mutation`/`destructive`/`idempotent` tags. A gate with no serving server context refuses. Names that are not tools on the server get FastMCP's `Unknown tool`, directly or through `call_tool`.
- **Refusals are `isError` results**: `SafetyViolationError` now subclasses FastMCP `ToolError`. Read-only and bulk refusals reach clients as a `tools/call` result with `isError: true` and the refusal message; before, a read-only refusal surfaced as a JSON-RPC `Internal server error`. Messages are now `Server running in read-only mode; tool '<name>' blocked (not annotated readOnlyHint=True).` and `Bulk destructive operations disabled; tool '<name>' blocked. Set SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1.`
- **Profiles**: `core` and `admin` are removed; `embed` is now a job allowlist (50 tools, was 57). New job profiles `analyst`, `author`, `modeler` (the builder job split in two) and `access_admin`. An unknown or removed profile raises `ValueError("Unknown profile ...")` at build and is rejected by `--profile`.
- **Bulk tools listed and gated at call time**: `admin_bulk_deactivate_members` and `admin_bulk_remove_team_members` are always listed in `full` (170 → 172 tools) and refused with `isError: true` unless `SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1`. Before, they were hidden from the catalog without the env. No job profile lists them.
- **Discovery is `full`-only**: Tool Search no longer attaches on `readonly` or the job profiles; requesting it there logs a warning and keeps the flat list. Tool Search and Code Mode together raise `ValueError`.
- **`create_server` and module surface**: `create_server(profile, enable_tool_search, enable_code_mode, tool_search_backend)` adds two keyword arguments. The returned server no longer wraps `call_tool`: it returns FastMCP `ToolResult` (not `mcp.types.CallToolResult`) and raises `NotFoundError` for unknown names, and it has no `_tool_manager`. Removed module attributes: `_tool_mgr`, `_orig_call_tool`, `_ToolManagerCompat`, `_PROFILES`, `_CORE_TOOLS`, `_ADMIN_TOOLS`, `_EMBED_TOOLS`, `_BULK_DESTRUCTIVE_TOOLS`, `_DOMAIN_SERVERS`, `_wire_name`. Use `profiles.PROFILES`, `profiles.BULK_DESTRUCTIVE_TOOLS`, `server.DOMAIN_SERVERS`, and the public `list_tools()` / `get_tool()` / `call_tool()`.

### Added
- **Job profiles** (`profiles.py`): `analyst` (31 tools, 23 read-only), `author` (40, 28), `modeler` (33, 24), `embed` (50, 27) and `access_admin` (52, 22). They mount every domain and expose an explicit tool-name allowlist; prompts and resources stay available. Every profile has a one-line `job`. Unknown allowlisted names raise `ValueError` at build.
- **`FULL_ONLY_TOOLS`**: the 9 tools in no job profile (both bulk tools, `workspace_delete_file`, `workspace_update_file`, `workspace_create_tag`, `workspace_delete_tag`, `admin_list_translations`, `admin_api_capabilities`, `admin_list_recent_webhooks`). Tests require every tool to be in a job profile or this set.
- **BM25 Tool Search and Code Mode**: `SIGMA_MCP_TOOL_SEARCH_BACKEND` / `--tool-search-backend` (`regex` or `bm25`) and `SIGMA_MCP_ENABLE_CODE_MODE` / `--enable-code-mode` (experimental, `full` only). `search_tools`, `search` and `get_schema` are annotated `readOnlyHint=True`; Code Mode `execute` is refused under read-only.
- **Tests**: `tests/test_profiles.py` and `tests/test_layered.py` cover per-profile counts, read-only composition, prompts and resources on job profiles, unknown names, the `call_tool` unwrap (with a spy and an unwrap-removed regression test), `Unknown tool` paths, hidden writes, `isError` on refusals, `FULL_ONLY_TOOLS`, explicit `readOnlyHint` on every tool, and the bulk gate.

### Fixed
- **Read-only through Tool Search**: with `full` + Tool Search + read-only, every `call_tool` was refused as `call_tool` itself, so reads were blocked too. The gate now unwraps `call_tool` (only when it is a real tool on the server) and judges the proxied tool: reads succeed, writes are refused.
- **`create_server()` dispatched to the module default**: the `call_tool` compatibility wrapper looked up the module-level `mcp` dispatcher, so a server built with another profile ran calls against the default `full` catalog. The wrapper is removed and each server dispatches on itself.
- **Shared sub-servers are no longer mutated**: profile, read-only and bulk filters used to disable tools on the module-level domain servers, so one `create_server()` call changed the catalog of later ones. All filters now apply to the per-call root.

### Changed
- **Public FastMCP API only**: filtering uses `root.disable`/`root.enable` visibility and `ReadOnlyToolFilter`/`ReadOnlyAnnotations` transforms instead of `_local_provider._components`, `sub._transforms.clear()` and the `_tool_manager` shim. The read-only gate looks tools up with the public `get_tool`.
- **Stale `sigma_*` names removed from the gate**: the `sigma_list_`/`sigma_get_` prefixes are gone. Python function names (`sigma_*`) are unchanged.
- **Contract script**: `scripts/check_tool_contract.py` asserts every profile's total and read-only counts, the README profile table, `FULL_ONLY_TOOLS`, explicit `readOnlyHint` on every tool, and that read-only composes with every profile.
- **Conformance baseline**: `tools-call-simple-text` and `tools-call-error` are removed from `conformance-baseline.yml`. They pass now that unknown tool names get FastMCP's native `Unknown tool` result instead of going through the removed `call_tool` compatibility wrapper.
- **Docs**: README, `AGENTS.md` (now with a `vcs:` block), `SECURITY.md`, `TESTING.md`, `docs/architecture.md` and the skill describe the profiles, read-only behavior, the call-time bulk gate and `full`-only discovery.

## 2.0.1 (2026-10-05)

*Never tagged or published. These changes ship in the first release after 1.2.2.*

### Security
- **FastMCP floor**: Raised the dependency floor from `fastmcp>=4.0.10` to `fastmcp>=4.0.11` and refreshed `uv.lock` (also `fastmcp.json`). This picks up the 4.0.11 security release. No server code changes.
- **SSE Host/Origin (#5427)**: Applies here. `--transport sse` is still accepted, and `main()` already passes `host_origin_protection`, `allowed_hosts`, and optional `allowed_origins` into `mcp.run()`. On 4.0.10 those settings configured Streamable HTTP only; 4.0.11 applies the same policy to the legacy SSE connection and message endpoints. The default transport remains stdio.
- **OpenAPI declared arguments (#5423)**: Does not apply. Tools are hand-written FastMCP functions. `scripts/check_openapi_drift.py` compares `SigmaClient` routes to Sigma's public REST spec; it does not build FastMCP OpenAPI components or a `RequestDirector`.
- **Component-manager auth (#5425)**: Does not apply. No FastMCP auth provider is configured and the component manager is not mounted. `SIGMA_MCP_AUTH_TOKEN` is logged for network transports and is not passed as FastMCP `auth`.
- **Hashed tool lookups (#5415)**: Does not apply to the published surface. Enable/disable gates (profiles, read-only, bulk-destructive) and the optional `RegexSearchTransform` run on public tool names. This server does not register FastMCP apps, which are the callers that invoke tools by hashed name.

## 2.0.0 (2026-10-03)

*Never tagged or published. These changes ship in the first release after 1.2.2.*

### Breaking
- **Wire names**: Tools, prompts, and resources no longer use a product-wide `sigma_` prefix. Domain FastMCP mounts expose `{domain}_{name}` (for example `sigma_list_workbooks` is now `workbooks_list_workbooks`). Server identity is unchanged (`mcp-server-sigma`, package `sigma_mcp`). This release is not tagged.

#### Prompts
- `provision_tenant_dashboard` → `workbooks_provision_tenant_dashboard`
- `audit_organization_permissions` → `admin_audit_organization_permissions`
- `prepare_data_model` → `datasets_prepare_data_model`
- `onboard_team_member` → `admin_onboard_team_member`
- `swap_warehouse_source` → `elements_swap_warehouse_source`
- `audit_tenant_connections` → `datasets_audit_tenant_connections`

#### Resources
- `sigma://reference/formulas` → `elements://reference/formulas`
- `sigma://reference/capabilities` → `admin://reference/capabilities`
- `sigma://reference/docs-index` → `elements://reference/docs-index`
- `sigma://webhooks/recent` → `admin://webhooks/recent`

#### Tools
#### workbooks
- `sigma_accept_shared_template` → `workbooks_accept_shared_template`
- `sigma_add_workbook_bookmark` → `workbooks_add_workbook_bookmark`
- `sigma_add_workbook_schedule` → `workbooks_add_workbook_schedule`
- `sigma_convert_workbook_to_report` → `workbooks_convert_workbook_to_report`
- `sigma_copy_workbook_to_member` → `workbooks_copy_workbook_to_member`
- `sigma_create_report` → `workbooks_create_report`
- `sigma_create_report_schedule` → `workbooks_create_report_schedule`
- `sigma_create_workbook` → `workbooks_create_workbook`
- `sigma_create_workbook_embed` → `workbooks_create_workbook_embed`
- `sigma_create_workbook_from_template` → `workbooks_create_workbook_from_template`
- `sigma_delete_workbook_schedule` → `workbooks_delete_workbook_schedule`
- `sigma_deploy_template_to_folder` → `workbooks_deploy_template_to_folder`
- `sigma_download_query_export` → `workbooks_download_query_export`
- `sigma_duplicate_report` → `workbooks_duplicate_report`
- `sigma_duplicate_workbook` → `workbooks_duplicate_workbook`
- `sigma_export_and_download` → `workbooks_export_and_download`
- `sigma_export_report` → `workbooks_export_report`
- `sigma_export_workbook` → `workbooks_export_workbook`
- `sigma_get_report` → `workbooks_get_report`
- `sigma_get_template` → `workbooks_get_template`
- `sigma_get_workbook` → `workbooks_get_workbook`
- `sigma_get_workbook_tags` → `workbooks_get_workbook_tags`
- `sigma_get_workbook_version_history` → `workbooks_get_workbook_version_history`
- `sigma_grant_workbook_access` → `workbooks_grant_workbook_access`
- `sigma_list_all_reports` → `workbooks_list_all_reports`
- `sigma_list_all_workbooks` → `workbooks_list_all_workbooks`
- `sigma_list_report_elements` → `workbooks_list_report_elements`
- `sigma_list_report_lineage` → `workbooks_list_report_lineage`
- `sigma_list_report_queries` → `workbooks_list_report_queries`
- `sigma_list_report_schedules` → `workbooks_list_report_schedules`
- `sigma_list_report_sources` → `workbooks_list_report_sources`
- `sigma_list_reports` → `workbooks_list_reports`
- `sigma_list_shared_templates` → `workbooks_list_shared_templates`
- `sigma_list_templates` → `workbooks_list_templates`
- `sigma_list_workbook_agents` → `workbooks_list_workbook_agents`
- `sigma_list_workbook_bookmarks` → `workbooks_list_workbook_bookmarks`
- `sigma_list_workbook_embeds` → `workbooks_list_workbook_embeds`
- `sigma_list_workbook_grants` → `workbooks_list_workbook_grants`
- `sigma_list_workbook_lineage` → `workbooks_list_workbook_lineage`
- `sigma_list_workbook_pages` → `workbooks_list_workbook_pages`
- `sigma_list_workbook_schedules` → `workbooks_list_workbook_schedules`
- `sigma_list_workbooks` → `workbooks_list_workbooks`
- `sigma_list_workbooks_shared_with_member` → `workbooks_list_workbooks_shared_with_member`
- `sigma_promote_workbook` → `workbooks_promote_workbook`
- `sigma_reassign_workbook_ownership` → `workbooks_reassign_workbook_ownership`
- `sigma_remove_workbook_tag` → `workbooks_remove_workbook_tag`
- `sigma_restore_workbook_version` → `workbooks_restore_workbook_version`
- `sigma_run_workbook_agent` → `workbooks_run_workbook_agent`
- `sigma_save_template_from_workbook` → `workbooks_save_template_from_workbook`
- `sigma_tag_workbook` → `workbooks_tag_workbook`
- `sigma_update_report_contents` → `workbooks_update_report_contents`
- `sigma_update_workbook_contents` → `workbooks_update_workbook_contents`
- `sigma_verify_report_spec` → `workbooks_verify_report_spec`
- `sigma_verify_workbook_spec` → `workbooks_verify_workbook_spec`

#### datasets
- `sigma_add_connection_grant` → `datasets_add_connection_grant`
- `sigma_bulk_sync_tenant_connections` → `datasets_bulk_sync_tenant_connections`
- `sigma_create_data_model` → `datasets_create_data_model`
- `sigma_delete_connection_path_grant` → `datasets_delete_connection_path_grant`
- `sigma_get_connection` → `datasets_get_connection`
- `sigma_get_data_model` → `datasets_get_data_model`
- `sigma_get_data_model_spec` → `datasets_get_data_model_spec`
- `sigma_list_all_data_models` → `datasets_list_all_data_models`
- `sigma_list_columns_for_table` → `datasets_list_columns_for_table`
- `sigma_list_connection_grants` → `datasets_list_connection_grants`
- `sigma_list_connections` → `datasets_list_connections`
- `sigma_list_data_model_columns` → `datasets_list_data_model_columns`
- `sigma_list_data_model_elements` → `datasets_list_data_model_elements`
- `sigma_list_data_model_lineage` → `datasets_list_data_model_lineage`
- `sigma_list_data_models` → `datasets_list_data_models`
- `sigma_swap_data_model_sources` → `datasets_swap_data_model_sources`
- `sigma_swap_report_sources` → `datasets_swap_report_sources`
- `sigma_sync_all_tables_in_schema` → `datasets_sync_all_tables_in_schema`
- `sigma_sync_connection` → `datasets_sync_connection`
- `sigma_tag_data_model` → `datasets_tag_data_model`
- `sigma_test_connection` → `datasets_test_connection`
- `sigma_update_data_model` → `datasets_update_data_model`

#### elements
- `sigma_create_source_swap_policy` → `elements_create_source_swap_policy`
- `sigma_formula_pitfalls` → `elements_formula_pitfalls`
- `sigma_get_doc_page` → `elements_get_doc_page`
- `sigma_get_element_columns` → `elements_get_element_columns`
- `sigma_get_element_query` → `elements_get_element_query`
- `sigma_get_materialization_job` → `elements_get_materialization_job`
- `sigma_get_source_swap_policy` → `elements_get_source_swap_policy`
- `sigma_list_all_input_tables` → `elements_list_all_input_tables`
- `sigma_list_materialization_schedules` → `elements_list_materialization_schedules`
- `sigma_list_source_swap_policies` → `elements_list_source_swap_policies`
- `sigma_list_workbook_columns` → `elements_list_workbook_columns`
- `sigma_list_workbook_controls` → `elements_list_workbook_controls`
- `sigma_list_workbook_elements` → `elements_list_workbook_elements`
- `sigma_list_workbook_page_elements` → `elements_list_workbook_page_elements`
- `sigma_list_workbook_queries` → `elements_list_workbook_queries`
- `sigma_list_workbook_sources` → `elements_list_workbook_sources`
- `sigma_materialize_and_wait` → `elements_materialize_and_wait`
- `sigma_materialize_element` → `elements_materialize_element`
- `sigma_search_docs` → `elements_search_docs`
- `sigma_swap_template_sources` → `elements_swap_template_sources`
- `sigma_swap_workbook_sources` → `elements_swap_workbook_sources`

#### workspace
- `sigma_create_folder` → `workspace_create_folder`
- `sigma_create_tag` → `workspace_create_tag`
- `sigma_create_workspace` → `workspace_create_workspace`
- `sigma_delete_file` → `workspace_delete_file`
- `sigma_delete_tag` → `workspace_delete_tag`
- `sigma_delete_workspace` → `workspace_delete_workspace`
- `sigma_delete_workspace_grant` → `workspace_delete_workspace_grant`
- `sigma_get_workspace` → `workspace_get_workspace`
- `sigma_grant_workspace_access` → `workspace_grant_workspace_access`
- `sigma_list_all_files` → `workspace_list_all_files`
- `sigma_list_files` → `workspace_list_files`
- `sigma_list_tags` → `workspace_list_tags`
- `sigma_list_workspace_grants` → `workspace_list_workspace_grants`
- `sigma_list_workspaces` → `workspace_list_workspaces`
- `sigma_update_file` → `workspace_update_file`

#### admin
- `sigma_add_allowed_ips` → `admin_add_allowed_ips`
- `sigma_add_deployment_documents` → `admin_add_deployment_documents`
- `sigma_api_capabilities` → `admin_api_capabilities`
- `sigma_archive_deployment` → `admin_archive_deployment`
- `sigma_bulk_assign_team_members` → `admin_bulk_assign_team_members`
- `sigma_bulk_deactivate_members` → `admin_bulk_deactivate_members`
- `sigma_bulk_remove_team_members` → `admin_bulk_remove_team_members`
- `sigma_change_member_email` → `admin_change_member_email`
- `sigma_configure_org_ai` → `admin_configure_org_ai`
- `sigma_create_deployment` → `admin_create_deployment`
- `sigma_create_grant` → `admin_create_grant`
- `sigma_create_member` → `admin_create_member`
- `sigma_create_team` → `admin_create_team`
- `sigma_create_tenant` → `admin_create_tenant`
- `sigma_create_user_attribute` → `admin_create_user_attribute`
- `sigma_deactivate_member` → `admin_deactivate_member`
- `sigma_delete_team` → `admin_delete_team`
- `sigma_delete_user_attribute_for_team` → `admin_delete_user_attribute_for_team`
- `sigma_delete_user_attribute_for_tenant` → `admin_delete_user_attribute_for_tenant`
- `sigma_delete_user_attribute_for_user` → `admin_delete_user_attribute_for_user`
- `sigma_get_api_connector` → `admin_get_api_connector`
- `sigma_get_current_user` → `admin_get_current_user`
- `sigma_get_deployment` → `admin_get_deployment`
- `sigma_get_member` → `admin_get_member`
- `sigma_get_org_setting` → `admin_get_org_setting`
- `sigma_get_team` → `admin_get_team`
- `sigma_get_tenant` → `admin_get_tenant`
- `sigma_get_tenant_scoped_info` → `admin_get_tenant_scoped_info`
- `sigma_get_user_attribute_teams` → `admin_get_user_attribute_teams`
- `sigma_get_user_attribute_tenants` → `admin_get_user_attribute_tenants`
- `sigma_get_user_attribute_users` → `admin_get_user_attribute_users`
- `sigma_list_account_types` → `admin_list_account_types`
- `sigma_list_all_members` → `admin_list_all_members`
- `sigma_list_all_teams` → `admin_list_all_teams`
- `sigma_list_allowed_ips` → `admin_list_allowed_ips`
- `sigma_list_api_connectors` → `admin_list_api_connectors`
- `sigma_list_deployment_documents` → `admin_list_deployment_documents`
- `sigma_list_deployments` → `admin_list_deployments`
- `sigma_list_grants` → `admin_list_grants`
- `sigma_list_member_teams` → `admin_list_member_teams`
- `sigma_list_members` → `admin_list_members`
- `sigma_list_org_workbook_agents` → `admin_list_org_workbook_agents`
- `sigma_list_recent_webhooks` → `admin_list_recent_webhooks`
- `sigma_list_team_members` → `admin_list_team_members`
- `sigma_list_teams` → `admin_list_teams`
- `sigma_list_tenants` → `admin_list_tenants`
- `sigma_list_tenants_paginated` → `admin_list_tenants_paginated`
- `sigma_list_translations` → `admin_list_translations`
- `sigma_list_user_attributes` → `admin_list_user_attributes`
- `sigma_onboard_member` → `admin_onboard_member`
- `sigma_remove_allowed_ips` → `admin_remove_allowed_ips`
- `sigma_reset_org_email_branding` → `admin_reset_org_email_branding`
- `sigma_set_user_attribute_for_teams` → `admin_set_user_attribute_for_teams`
- `sigma_set_user_attribute_for_tenants` → `admin_set_user_attribute_for_tenants`
- `sigma_update_member` → `admin_update_member`
- `sigma_update_org_setting` → `admin_update_org_setting`
- `sigma_update_team_members` → `admin_update_team_members`
- `sigma_update_user_attribute_for_teams` → `admin_update_user_attribute_for_teams`
- `sigma_update_user_attribute_for_tenants` → `admin_update_user_attribute_for_tenants`
- `sigma_update_user_attribute_for_users` → `admin_update_user_attribute_for_users`
## 1.2.2 (2026-10-02)

### Security
- **Outbound SSRF checks**: Removed the HTTP loopback exception from base-URL validation and `SSRFSafeAsyncTransport`. `http://localhost`, `http://127.0.0.1`, and `http://[::1]` are rejected. Private, loopback, link-local, and cloud-metadata destinations are blocked, including IPv4-mapped IPv6 addresses and decimal or hex IP literals.
- **Default API host allowlist**: When `SIGMA_ALLOWED_HOSTS` is unset or empty, outbound API base URLs must use an official Sigma regional API host. Set the variable to replace that list. An empty value no longer disables the allowlist.
- **Documentation fetches**: `sigma_get_doc_page` and `sigma_search_docs` validate the docs host, do not follow redirects, and connect through `SSRFSafeAsyncTransport`, which pins the socket to the validated public IP. `Host` and TLS SNI stay on the original hostname.
- **OpenAPI drift**: `scripts/check_openapi_drift.py --spec-url` uses the same pin via sync `SSRFSafeTransport`. The TCP connection targets the DNS address that passed the public-IP checks, and redirects are not followed.

## 1.2.1 (2026-09-28)

### Changed
- **Locked dependency resolution**: Added a tracked `uv.lock` (removed from `.gitignore`) generated from the current `pyproject.toml`. The `fastmcp` floor stays `>=4.0.10`; the lock pins FastMCP **4.0.10**.
- **Locked CI installs**: Replaced unbound `pip install -e ".[dev]"`, `pip install -e .`, and `pip install build twine` steps in `.github/workflows/ci.yml` with `uv sync --locked` (`--extra dev` for lint, test, contract, OpenAPI drift, and package build). Commands run via `uv run`. The weekly drift monitor installs with `uv sync --locked` instead of an unbound `pip install httpx`.
- **License check**: Moved `pip-licenses` into the `dev` extra so the existing GPL fail-closed check is covered by the lockfile.

No host or credential changes.

## 1.2.0 (2026-09-26)

### Added
- **FastMCP 4 Server Composition & Domain Partitioning**: Decomposed the monolithic server into modular domain sub-servers in `src/sigma_mcp/tools/` (`workbooks.py`, `datasets.py`, `elements.py`, `workspace.py`, `admin.py`, and `common.py`).
- **Gateway Server Factory**: Introduced `create_server(profile=..., enable_tool_search=...)` mounting domain sub-servers without prefixes, preserving universal flat `sigma_*` tool naming on the wire for client compatibility.
- **Hierarchical Middleware Pipeline**: Integrated `ParentAuditMiddleware` (operation timing, structured JSON audit logs, secret scrubbing, JSON-RPC error preservation), `ReadOnlyGateMiddleware` (`SIGMA_MCP_READONLY=1`), and `AdminDomainGuardMiddleware` for bulk-destructive tool isolation (`SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1`).
- **Defensive Formatters & Payload Bounding**: Applied template Recipe 10 formatters (`safe_dict`, `safe_list`, boundary-aware status checkers, candidate completeness validation, and integer preservation). Implemented bounded chunk streaming (`max_bytes`) directly within `SigmaClient._request()` for query and export downloads to prevent memory exhaustion.
- **DNS Rebinding & SSRF Hardening**: Offloaded DNS hostname checks off the event loop via worker threads in `SSRFSafeAsyncTransport` and pinned outbound socket connections to the pre-validated IP address while preserving original `Host` headers and TLS SNI verification.
- **Expanded API Surface**: Added support for workbook specifications, code representations, query exports, IP allowlists, organization settings, workbook agents, and content updates, bringing default tool catalog to 170 tools (172 with bulk).

### Changed
- **FastMCP Dependency Upgrade**: Upgraded FastMCP dependency to `fastmcp>=4.0.10`.
- **Read-Only Gate Allowlist**: Explicitly allowlisted `sigma_verify_workbook_spec`, `sigma_verify_report_spec`, and `sigma_download_query_export` under read-only mode.
- **Tool Annotations**: Corrected `sigma_bulk_remove_team_members` annotation to destructive.

## 1.1.5 (2026-09-12)

### Added
- **Streamable HTTP Transport (MCP Spec 2026-07-28)**: Added full support for `--transport streamable-http` with paired boolean flags `--stateless` / `--no-stateless` and `--json-response` / `--no-json-response`.
- **Testing Architecture Standardization**: Added `tests/test_protocol.py` and `tests/test_e2e_live.py` (`@pytest.mark.e2e`) establishing the standardized testing pyramid with 100% statement and branch coverage.
- **Deprecation Warning**: Emits deprecation warning for legacy HTTP+SSE transport per MCP Spec 2026-07-28.

### Removed
- **Ad-Hoc Scripts**: Retired legacy `scripts/smoke_test.py` in favor of standard pytest test suites.

## 1.1.4 (2026-09-03)

### Changed
- **Proactive Parameter Deprecation Mitigation**: Updated `search_members` in `client.py` to prefer the `email` query parameter when looking up members by email address, resolving upstream Sigma deprecation of the `search` parameter on `GET /v2/members`.
- **Synchronized Sunday Maintenance Schedule**: Standardized upstream API drift monitoring to Sunday 12:00 AM EDT / 04:00 UTC (`cron: '0 4 * * 0'`) and Dependabot dependency reconciliation to Sunday 12:30 AM EDT / 04:30 UTC (`time: "04:30"`).
- **Enhanced Parameter & Schema Drift Engine**: Upgraded `scripts/check_openapi_drift.py` with AST client inspection and parameter deprecation auditing.

## 1.1.3 (2026-08-30)

### Fixed
- **Graceful Shutdown Interceptor**: Registered custom `SIGTERM` and `SIGINT` signal handlers in `server.py` to exit with status code `0`, preventing supervisor `exit status 143` errors on client restarts.

## 1.1.0 (2026-08-27)

### Changed
- **License Standardization**: Upgraded repository license to Apache 2.0 for enterprise patent indemnity and legal uniformity.
- **Suite Baseline**: Synchronized versioning across the enterprise MCP server suite.

## 1.0.5 (2026-08-14)

### Added
- **CodeRabbit Pull Request Reviews Badge**: Added the official shields.io automated code reviews badge to `README.md`.
- **CodeRabbit Noise Reduction**: Excluded documentation markdown files (`CHANGELOG.md`, `README.md`), manifests (`server.json`), local cookbooks, and `.gitignore` from auto-reviews to focus comments strictly on code changes.
- **Cookbook Improvements**: Documented the MCP Registry 100-character description constraints and the PyPI file-overwrite collision release recovery procedure inside the developer `COOKBOOK.md`.
- **Contribution Merge Strategy**: Documented the repository's strict git squash-merging and Conventional Commit pull request title conventions in `CONTRIBUTING.md` and `COOKBOOK.md`.
- **Grouped Dependabot Updates**: Configured `.github/dependabot.yml` to group all dependency updates (both pip and GitHub Actions) into single consolidated weekly pull requests to reduce PR noise.

## 1.0.4 (2026-08-09)

### Fixed
- **Description Length Validation**: Shortened the `server.json` description to comply with the MCP Registry's 100-character constraint.

## 1.0.3 (2026-08-09)

### Added
- **MCP Registry Integration**: Created the `server.json` manifest specifying standard environment variables and metadata for OIDC publishing to the official Model Context Protocol Registry.
- **Development Cookbook**: Added a git-ignored developer `COOKBOOK.md` to define strict local type checks, AI self-reviews, and CodeRabbit review wait-states prior to merging.

### Fixed
- **Release CI Workflow**: Checked out the workspace in the PyPI/MCP publish job so `server.json` is present during version bumps, and pinned the `mcp-publisher` tool to release version `v1.8.1` as recommended by CodeRabbit reviews.

## 1.0.2 (2026-08-07)

### Fixed
- **OpenAPI Drift Target**: Pointed the drift monitor target to the canonical unified public REST API spec (`assets.sigmacomputing.com`), bringing visibility to 33 previously hidden endpoints (including the Beta code-representation spec APIs).
- **Layout & Spec Wording**: Softened the workbook layout descriptions in `sigma_api_capabilities` and `README.md` to reference the Beta `/v2/workbooks/spec` code representation spec.
- **Base URL Troubleshooting**: Clarified region-specific connection troubleshooting text in `server.py` to avoid false alerts for AWS US-West users.
- **Profile Counts**: Corrected tool registration count figures in the `README.md` configuration tables.

## 1.0.1 (2026-08-05)

### Fixed
- **PyPI Rendering**: Converted relative documentation links to absolute GitHub URLs to prevent 404 errors on PyPI project pages.

## 1.0.0 (2026-08-02)

### Breaking Changes
- **Bulk-destructive tools are no longer registered by default.** `sigma_bulk_deactivate_members` and `sigma_bulk_remove_team_members` require `SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1` to appear in the tool list. Without it, they do not exist from the model's perspective. This is intentional: an unprompted "clean up inactive users" from a model should not have access to bulk deactivation.
- **Single-Delete Confirmation Gating**: Mandatory `confirm: bool = False` opt-in parameter required on all atomic delete, archive, deactivate, and bulk-remove tools. Callers performing destructive operations must explicitly pass `confirm=True`.

### Added
- **Native MCP Resources**: Registered `sigma://reference/formulas`, `sigma://reference/capabilities`, `sigma://reference/docs-index`, and `sigma://webhooks/recent` native resources.
- **Native MCP Prompts**: Registered `provision_tenant_dashboard`, `audit_organization_permissions`, `prepare_data_model`, `onboard_team_member`, `swap_warehouse_source`, and `audit_tenant_connections` native prompts.
- **Documentation tools**: `sigma_search_docs` (AI-powered semantic search via Sigma's docs MCP) and `sigma_get_doc_page` (fetch any docs page as Markdown).
- **Structured JSON Logging**: Support for `SIGMA_MCP_LOG_FORMAT=json` with structured log records including execution duration (`duration_ms`).
- **Network Transport CLI Flags**: Added `--host` and `--port` CLI options for network transport server deployment.
- **Path Segment Sanitization**: Automated path parameter quoting (`quote(seg, safe="").replace("..", "%2E%2E")`) across all 217 client API methods to prevent path traversal attacks.
- **Secret & Token Redaction**: Multi-pattern regex scrubbing (`Bearer` tokens, `client_secret`, `access_token`, `subject_token`, and raw JWT `eyJ...`) across error responses and log payloads.
- **Multi-Tenant Security**: Added `SIGMA_ALLOWED_TENANTS` allowlist check and `SIGMA_STRICT_TENANT_ALLOWLIST=1` fail-closed enforcement for RFC 8693 token exchange.
- **Single-Delete Confirmation Gating**: Mandatory `confirm: bool = False` opt-in parameter required on all atomic delete, archive, deactivate, and bulk-remove tool calls.
- **Safety gating**: `SIGMA_MCP_READONLY=1` removes all non-read-only tools from registration (83 tools remain). Composes with profiles (e.g. `admin` + `readonly` = read-only subset of admin tools).
- **`embed` profile**: `SIGMA_MCP_PROFILE=embed` (55 tools) for embedded analytics workflows — core + embeds, user attributes, tenants, source swap, workspace grants.
- **Catch-all regex rejection** in `sigma_bulk_deactivate_members`: patterns like `.*`, `.+`, `^.*$`, `.`, or empty string are refused because they would match every member in the org.
- **10-member hard cap** on `sigma_bulk_deactivate_members`: if the pattern matches more than 10 members, the tool refuses and reports the match list. Prevents accidental org-wide deactivation.
- **`sigma_formula_pitfalls` tool**: returns a curated Sigma formula reference to prevent hallucinated function names and type errors. Backed by `src/sigma_mcp/reference/formulas.md`.
- **11 new endpoints**: reports (CRUD, schedules, elements, queries, lineage, sources, duplicate, export), source-swap policies, deployment documents, workbook version history, report schedules.
- **Tool count now 155** (default), 157 with bulk-destructive opt-in, 83 read-only.
- **Expanded test suite** covering profile composition, readonly filtering, bulk-gating, annotation completeness, and catch-all regex rejection.
- **OSS files**: SECURITY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md, LICENSE.
- **`scripts/check_tool_contract.py`**: validates counts, annotations, profiles, and gating. Runs in CI.
- **docs/formulas.md**: full Sigma formula reference for agent consumption.

### Fixed
- Annotation counts corrected: 83 read-only, 16 destructive, 8 idempotent.
- `sigma_promote_workbook` now creates the tag if it doesn't already exist (idempotent).

## 0.2.0 (2026-08-01)

### Breaking Changes
- Client is now fully async (`httpx.AsyncClient`). All methods are `async def`.
- All MCP tools are now `async def`. Requires MCP SDK >= 2.0.0.

### Added
- **Structured error handling**: `SigmaAPIError` class with status, path, method, detail, request_id. `@sigma_tool` decorator wraps all tools — no raw exceptions escape.
- **8 recipe tools**: export+download, ownership transfer, shared workbooks, input table scan, bulk deactivate, change email, bulk team remove, tenant connection sync.
- **Multi-tenant auth**: RFC 8693 token exchange via `SigmaClient.for_tenant()`. Per-tenant token cache with auto-refresh.
- **MCP 2.0 annotations**: All 141 tools annotated with `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`.
- **Tool profiles**: `SIGMA_MCP_PROFILE=core|admin|full` to control tool registration.
- **OpenAPI drift detection**: `scripts/check_openapi_drift.py` compares client paths against official spec.
- **GitHub Actions CI**: Matrix on Python 3.10-3.13 with ruff + pytest.
- **Auto-pagination tools**: `sigma_list_all_*` tools that follow pagination tokens.
- **PyJWT dependency** for tenant token exchange.
- Secret redaction in all error messages.

### Fixed
- `download_query()` now routes through `_request()` for retry/rate-limit handling.
- `time.sleep` replaced with `await asyncio.sleep` throughout.

## 0.1.0 (2025-06-15)

Initial release. 131 tools covering the Sigma v2 API surface.
