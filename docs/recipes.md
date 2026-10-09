# API Recipes Coverage

Maps all 25 official [Sigma API recipes](https://help.sigmacomputing.com/recipes/llms.txt) to our MCP tools.

## Coverage Table

| # | Recipe | Our Tool(s) | Status | Improvement |
|---|--------|-------------|--------|-------------|
| 1 | List connections | `datasets_list_connections` | Covered | — |
| 2 | Test a connection | `datasets_test_connection` | Covered | — |
| 3 | Sync connection schemas | `datasets_sync_connection` | Covered | Accepts optional `path` filter |
| 4 | List workbooks | `workbooks_list_workbooks` | Covered | — |
| 5 | Create a workbook | `workbooks_create_workbook` | Covered | — |
| 6 | Duplicate a workbook | `workbooks_duplicate_workbook` | Covered | — |
| 7 | Delete a workbook | `workspace_delete_file` | Covered | Uses inode deletion (correct endpoint) |
| 8 | Export a workbook | `workbooks_export_workbook` | Covered | Bounded polling with timeout vs infinite loop |
| 9 | Grant workbook access | `workbooks_grant_workbook_access` | Covered | — |
| 10 | Swap workbook sources | `elements_swap_workbook_sources` | Covered | Supports both connection_mapping and source_mapping |
| 11 | Create a workbook embed | `workbooks_create_workbook_embed` | Covered | — |
| 12 | Schedule a workbook | `workbooks_add_workbook_schedule` | Covered | — |
| 13 | Materialize an element | `elements_materialize_element` | Covered | — |
| 14 | List members | `admin_list_members` | Covered | — |
| 15 | Create a member | `admin_create_member` | Covered | — |
| 16 | Update a member | `admin_update_member` | Covered | — |
| 17 | Deactivate a member | `admin_deactivate_member` | Covered | — |
| 18 | List teams | `admin_list_teams` | Covered | — |
| 19 | Create a team | `admin_create_team` | Covered | — |
| 20 | Manage team members | `admin_update_team_members` | Covered | Batched add/remove in one call vs one-at-a-time |
| 21 | Deploy a template | `workbooks_create_workbook_from_template` + `elements_swap_template_sources` | Covered | Combines two steps into workflow |
| 22 | Version promotion (tags) | `workbooks_tag_workbook` + `workbooks_remove_workbook_tag` | Covered | — |
| 23 | Create a data model | `datasets_create_data_model` | Covered | — |
| 24 | List deployments | `admin_list_deployments` | Covered | — |
| 25 | Multi-tenant operations | `admin_list_tenants` + `admin_create_tenant` | Covered | RFC 8693 token exchange with per-tenant caching |

## Key Improvements Over Official Recipes

- **Batched team-member operations**: `admin_update_team_members` accepts `add` and `remove` lists in one call, vs the official recipe's one-member-at-a-time approach.
- **Auto-pagination**: The `list_all_*` tools (`workbooks_list_all_workbooks`, `admin_list_all_members`, `admin_list_all_teams`, `workspace_list_all_files`, `workbooks_list_all_reports`, `datasets_list_all_data_models`) paginate automatically and return all records, vs hardcoded `limit` values that silently drop data beyond the first page.
- **Collected error arrays**: The `@sigma_tool` decorator catches `SigmaAPIError` and unexpected exceptions and reports them as a structured JSON error with `isError: true` — never an unhandled exception.
- **Bounded polling with timeouts**: Export operations poll with exponential backoff and a hard timeout, vs infinite polling loops in official examples.
- **Dry-run gating on destructive operations**: `admin_bulk_deactivate_members`, `workbooks_reassign_workbook_ownership`, and `datasets_bulk_sync_tenant_connections` default to `dry_run=True`, reporting what would change without executing.
