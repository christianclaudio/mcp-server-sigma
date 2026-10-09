---
name: sigma-mcp
description: Enterprise Agent Skill for orchestrating Cloud BI, Workbooks, Data Models, Permissions, and Multi-tenant Governance with mcp-server-sigma.
---

# Sigma Computing MCP Server (`mcp-server-sigma`) Agent Skill

This skill provides expert instructions, architectural workflows, and safety protocols for AI agents operating against Cloud BI & Analytics environments via `mcp-server-sigma`.

---

## 🎯 Core Agent Workflows

### 1. Data Model & Visual Provisioning Workflow
- **Step 1: Connection Verification** — Call `datasets_get_connection(connection_id=...)` to inspect active database paths and schemas.
- **Step 2: Schema Synchronization** — Invoke `datasets_sync_all_tables_in_schema(connection_id=..., database=..., schema=...)` to ensure all remote warehouse metadata is up to date.
- **Step 3: Template Instantiation** — Use `workbooks_create_workbook_from_template(template_id=..., folder_id=..., name=...)` to programmatically build real visual charts and pivot tables.
- **Step 4: Source Swapping** — Apply `elements_swap_template_sources(template_id=..., connection_mapping=[...])` to rebind template tables to new warehouse targets.

### 2. Tenant Provisioning & Multi-Tenant Governance (RFC 8693)
- **Token Exchange & Allowlist**: For tenant-scoped access, pass `tenant_org_id` to `admin_get_tenant_scoped_info`. In strict tenant mode (`SIGMA_STRICT_TENANT_ALLOWLIST=1`), access is rejected unless `tenant_org_id` is explicitly present in `SIGMA_ALLOWED_TENANTS`.
- **Bulk Tenant Connection Sync**: Execute `datasets_bulk_sync_tenant_connections(dry_run=True)` first to preview all tenant databases present in the allowed tenant set. Set `dry_run=False` to synchronize schemas across configured organization tenants concurrently.

### 3. Security, Permission & User Lifecycle Management
- **Member Onboarding**: Call `admin_onboard_member(email=..., first_name=..., last_name=..., team_ids=[...])` to create the member and automatically assign team memberships.
- **Workbook Transfer**: Use `workbooks_reassign_workbook_ownership(old_owner_email=..., new_owner_email=..., dry_run=True)` to preview and transfer all owned workbooks during employee offboarding.
- **Bulk Offboarding Safety**: First call `admin_bulk_deactivate_members(name_pattern=...)`; the default `dry_run=True` only reports the matching members. To deactivate them, call it again with `admin_bulk_deactivate_members(name_pattern=..., dry_run=False, confirm=True)`. It refuses catch-all patterns and patterns that match more than 10 active members, and it is refused at call time unless `SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1`.

---

## 🛡️ Safety & Execution Rules for AI Agents

1. **Confirmation Gating on Destructive Tools**:
   Every tool annotated `destructiveHint=true` makes no changes unless it receives `confirm=True` (`admin_bulk_deactivate_members` also needs `dry_run=False`; otherwise it only previews). Without it the result is not an error (`isError: false`): it has `"status": "confirmation_required"` and says to re-call with `confirm=true`. Missing or invalid arguments are errors (`isError: true`, `"type": "invalid_request"`); fix the argument and call again. Other failed calls are errors too (`isError: true`), with `"type"` `not_found`, `timeout`, `upstream_response`, `docs_search_failed`, `docs_search_empty`, `page_not_found` or `not_supported`; a rejected bulk-deactivate pattern is `invalid_request`. A batch tool (`admin_bulk_deactivate_members`, `workbooks_reassign_workbook_ownership`, `elements_list_all_input_tables`, `datasets_bulk_sync_tenant_connections`) whose every item failed is an error (`isError: true`, `"type": "batch_failed"`, with per-item `errors`, each entry `{"id", "status": "failed", "error"}` with an `error.message`); a partial success is a normal result with `"status": "partial_success"`, a `<verb>_count`, `failed_count`, `results` (entries `{"id", "status", "result"}`) and `errors`. The complete list:
   - Delete operations: `workspace_delete_file`, `workspace_delete_workspace`, `workspace_delete_workspace_grant`, `workspace_delete_tag`, `admin_delete_team`, `workbooks_delete_workbook_schedule`, `datasets_delete_connection_path_grant`, `admin_delete_user_attribute_for_user`, `admin_delete_user_attribute_for_team`, `admin_delete_user_attribute_for_tenant`
   - Deactivation and archiving: `admin_deactivate_member`, `admin_bulk_deactivate_members`, `admin_archive_deployment`
   - Revocation and removal: `admin_update_user_attribute_for_users`, `admin_update_user_attribute_for_teams`, `admin_update_user_attribute_for_tenants`, `workbooks_remove_workbook_tag`, `admin_remove_allowed_ips`, `admin_bulk_remove_team_members`
   - Reset: `admin_reset_org_email_branding`

   These non-destructive writes also require `confirm=True`: `admin_add_allowed_ips`, `admin_configure_org_ai`, `admin_update_org_setting`, `workbooks_run_workbook_agent`, `workbooks_update_report_contents`, `workbooks_update_workbook_contents`.

2. **Input Validation**:
   - `datasets_add_connection_grant`: `grant_type` must be `'member'` or `'team'`; `permission` must be `'annotate'` or `'usage'`.
   - `workbooks_create_workbook_embed`: `source_id` is required when `source_type` is `'page'` or `'element'`.

3. **Always Run Dry-Run First**:
   For composite operations (`workbooks_reassign_workbook_ownership`, `admin_bulk_deactivate_members`, `datasets_bulk_sync_tenant_connections`), invoke with `dry_run=True` first to report expected changes before executing. Bulk destructive tools also require `SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1`.

4. **Read-Only Mode and Profiles**:
   Under `SIGMA_MCP_READONLY=1` or `SIGMA_MCP_PROFILE=readonly`, only tools annotated `readOnlyHint=True` are callable; any other call, including through Tool Search `call_tool`, returns an error result (`isError: true`) naming the blocked tool. Job profiles (`analyst`, `author`, `modeler`, `embed`, `access_admin`) list only their job's tools; a tool outside the profile is `Unknown tool`.

5. **Formula Syntax Validation**:
   Read `elements://reference/formulas` before crafting Sigma workbook formulas. Note key differences from Excel/SQL:
   - String concatenation uses `Concat(a, b)`, not `+`.
   - Null handling requires `IfNull(val, fallback)`.
   - Date functions put the unit FIRST: `DateDiff("day", [Start], [End])`.

---

## 📚 Documentation Search & Reference

### Tools
- `elements_search_docs(query=...)` — AI-powered semantic search across all Sigma documentation. Returns relevant passages with source URLs. Use for "how do I..." questions about Sigma features.
- `elements_get_doc_page(page_slug=...)` — Fetch a specific docs page as full Markdown. Pass a slug like `"create-a-workbook"` or a full URL. Use when you need the complete reference for a known page.
- `elements_formula_pitfalls()` — Curated pitfall reference for Sigma formula expressions.

### Resources
- `elements://reference/docs-index` — Full index of all Sigma documentation pages (1000+ entries with URLs). Use to discover which page to fetch.
- `elements://reference/formulas` — Cheat sheet of common formula syntax traps and corrections.
- `admin://reference/capabilities` — Returns static JSON: the supported Sigma API domains, unsupported operations (direct element creation, direct page layout, SAML certificate management beta), and the recommended template-then-stamp workflow. It does not report the active profile, read-only status, or tool counts.
- `admin://webhooks/recent` — JSON list of up to 20 of the most recent events in this process's in-memory webhook buffer (the same data as `admin_list_recent_webhooks` with its defaults). The server does not expose an HTTP webhook endpoint, so the list stays empty unless the host application passes events to `sigma_mcp.webhooks.process_incoming_webhook`; nothing is persisted across restarts.

### Workflow
1. Start with `elements_search_docs(query="...")` for broad questions
2. If a specific page is referenced in results, fetch it with `elements_get_doc_page(page_slug="...")`
3. For formula-specific questions, use `elements_formula_pitfalls()` first

---

## 💡 Available Agent Prompts

- `admin_audit_organization_permissions(team_name=...)` — Standard auditing checklist for security reviews.
- `datasets_prepare_data_model(connection_id=..., model_name=...)` — Step-by-step assistant guide for data model creation.
- `datasets_audit_tenant_connections()` — Multi-tenant connection health check workflow.
- `workbooks_provision_tenant_dashboard(template_id=..., folder_id=..., tenant_id=..., dashboard_name=...)` — Deploy a template into a folder and swap its data sources for one tenant (`dashboard_name` is optional).
- `elements_swap_warehouse_source(workbook_id=..., target_connection_id=...)` — Re-bind workbook or template data sources to a new connection or table.
- `admin_onboard_team_member(email=..., first_name=..., last_name=..., team_name=...)` — Create a member, assign team memberships, and verify home folder setup (`team_name` is optional).
