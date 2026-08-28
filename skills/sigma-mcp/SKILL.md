---
name: sigma-mcp
description: Enterprise Agent Skill for orchestrating Cloud BI, Workbooks, Data Models, Permissions, and Multi-tenant Governance with mcp-server-sigma.
---

# Sigma Computing MCP Server (`mcp-server-sigma`) Agent Skill

This skill provides expert instructions, architectural workflows, and safety protocols for AI agents operating against Cloud BI & Analytics environments via `mcp-server-sigma`.

---

## 🎯 Core Agent Workflows

### 1. Data Model & Visual Provisioning Workflow
- **Step 1: Connection Verification** — Call `sigma_get_connection(connection_id=...)` to inspect active database paths and schemas.
- **Step 2: Schema Synchronization** — Invoke `sigma_sync_all_tables_in_schema(connection_id=..., database=..., schema=...)` to ensure all remote warehouse metadata is up to date.
- **Step 3: Template Instantiation** — Use `sigma_create_workbook_from_template(template_id=..., folder_id=..., name=...)` to programmatically build real visual charts and pivot tables.
- **Step 4: Source Swapping** — Apply `sigma_swap_template_sources(template_id=..., connection_mapping=[...])` to rebind template tables to new warehouse targets.

### 2. Tenant Provisioning & Multi-Tenant Governance (RFC 8693)
- **Token Exchange & Allowlist**: For tenant-scoped access, pass `tenant_org_id` to `sigma_get_tenant_scoped_info`. In strict tenant mode (`SIGMA_STRICT_TENANT_ALLOWLIST=1`), access is rejected unless `tenant_org_id` is explicitly present in `SIGMA_ALLOWED_TENANTS`.
- **Bulk Tenant Connection Sync**: Execute `sigma_bulk_sync_tenant_connections(dry_run=True)` first to preview all tenant databases present in the allowed tenant set. Set `dry_run=False` to synchronize schemas across configured organization tenants concurrently.

### 3. Security, Permission & User Lifecycle Management
- **Member Onboarding**: Call `sigma_onboard_member(email=..., first_name=..., last_name=..., team_ids=[...])` to create the member and automatically assign team memberships.
- **Workbook Transfer**: Use `sigma_reassign_workbook_ownership(old_owner_email=..., new_owner_email=..., dry_run=True)` to preview and transfer all owned workbooks during employee offboarding.
- **Bulk Offboarding Safety**: Use `sigma_bulk_deactivate_members(name_pattern=..., confirm=True)`. Safety cap enforces maximum 10 deactivations per run.

---

## 🛡️ Safety & Execution Rules for AI Agents

1. **Confirmation Gating on Destructive Tools**:
   All destructive tools **MUST** explicitly receive `confirm=True`. This includes:
   - Delete operations: `sigma_delete_file`, `sigma_delete_workspace`, `sigma_delete_team`, `sigma_delete_tag`, `sigma_delete_workbook_schedule`, `sigma_delete_workspace_grant`, `sigma_delete_connection_path_grant`, `sigma_delete_user_attribute_for_user`, `sigma_delete_user_attribute_for_team`
   - Deactivation: `sigma_deactivate_member`, `sigma_bulk_deactivate_members`
   - Revocation: `sigma_update_user_attribute_for_users`, `sigma_update_user_attribute_for_teams`, `sigma_update_user_attribute_for_tenants`, `sigma_remove_workbook_tag`
   - Bulk operations: `sigma_bulk_remove_team_members`

2. **Input Validation**:
   - `sigma_add_connection_grant`: `grant_type` must be `'member'` or `'team'`; `permission` must be `'annotate'` or `'usage'`.
   - `sigma_create_workbook_embed`: `source_id` is required when `source_type` is `'page'` or `'element'`.

3. **Always Run Dry-Run First**:
   For composite operations (`sigma_reassign_workbook_ownership`, `sigma_bulk_deactivate_members`, `sigma_bulk_sync_tenant_connections`), invoke with `dry_run=True` first to report expected changes before executing. Bulk destructive tools also require `SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1`.

4. **Formula Syntax Validation**:
   Read `sigma://reference/formulas` before crafting Sigma workbook formulas. Note key differences from Excel/SQL:
   - String concatenation uses `Concat(a, b)`, not `+`.
   - Null handling requires `IfNull(val, fallback)`.
   - Date functions put the unit FIRST: `DateDiff("day", [Start], [End])`.

---

## 📚 Documentation Search & Reference

### Tools
- `sigma_search_docs(query=...)` — AI-powered semantic search across all Sigma documentation. Returns relevant passages with source URLs. Use for "how do I..." questions about Sigma features.
- `sigma_get_doc_page(page_slug=...)` — Fetch a specific docs page as full Markdown. Pass a slug like `"create-a-workbook"` or a full URL. Use when you need the complete reference for a known page.
- `sigma_formula_pitfalls()` — Curated pitfall reference for Sigma formula expressions.

### Resources
- `sigma://reference/docs-index` — Full index of all Sigma documentation pages (1000+ entries with URLs). Use to discover which page to fetch.
- `sigma://reference/formulas` — Cheat sheet of common formula syntax traps and corrections.
- `sigma://reference/capabilities` — Returns dynamic server metadata (total tools registered, active profiles, read-only status).

### Workflow
1. Start with `sigma_search_docs(query="...")` for broad questions
2. If a specific page is referenced in results, fetch it with `sigma_get_doc_page(page_slug="...")`
3. For formula-specific questions, use `sigma_formula_pitfalls()` first

---

## 💡 Available Agent Prompts

- `prompt_audit_organization_permissions(team_name=...)` — Standard auditing checklist for security reviews.
- `prompt_prepare_data_model(connection_id=..., model_name=...)` — Step-by-step assistant guide for data model creation.
- `prompt_audit_tenant_connections()` — Multi-tenant connection health check workflow.
