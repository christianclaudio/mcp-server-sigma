# API Recipes Coverage

Maps all 25 official [Sigma API recipes](https://help.sigmacomputing.com/recipes/llms.txt) to our MCP tools.

## Coverage Table

| # | Recipe | Our Tool(s) | Status | Improvement |
|---|--------|-------------|--------|-------------|
| 1 | List connections | `sigma_list_connections` | Covered | — |
| 2 | Test a connection | `sigma_test_connection` | Covered | — |
| 3 | Sync connection schemas | `sigma_sync_connection` | Covered | Accepts optional `path` filter |
| 4 | List workbooks | `sigma_list_workbooks` | Covered | — |
| 5 | Create a workbook | `sigma_create_workbook` | Covered | — |
| 6 | Duplicate a workbook | `sigma_duplicate_workbook` | Covered | — |
| 7 | Delete a workbook | `sigma_delete_file` | Covered | Uses inode deletion (correct endpoint) |
| 8 | Export a workbook | `sigma_export_workbook` | Covered | Bounded polling with timeout vs infinite loop |
| 9 | Grant workbook access | `sigma_grant_workbook_access` | Covered | — |
| 10 | Swap workbook sources | `sigma_swap_workbook_sources` | Covered | Supports both connection_mapping and source_mapping |
| 11 | Create a workbook embed | `sigma_create_workbook_embed` | Covered | — |
| 12 | Schedule a workbook | `sigma_add_workbook_schedule` | Covered | — |
| 13 | Materialize an element | `sigma_materialize_element` | Covered | — |
| 14 | List members | `sigma_list_members` | Covered | — |
| 15 | Create a member | `sigma_create_member` | Covered | — |
| 16 | Update a member | `sigma_update_member` | Covered | — |
| 17 | Deactivate a member | `sigma_deactivate_member` | Covered | — |
| 18 | List teams | `sigma_list_teams` | Covered | — |
| 19 | Create a team | `sigma_create_team` | Covered | — |
| 20 | Manage team members | `sigma_update_team_members` | Covered | Batched add/remove in one call vs one-at-a-time |
| 21 | Deploy a template | `sigma_create_workbook_from_template` + `sigma_swap_template_sources` | Covered | Combines two steps into workflow |
| 22 | Version promotion (tags) | `sigma_tag_workbook` + `sigma_remove_workbook_tag` | Covered | — |
| 23 | Create a data model | `sigma_create_data_model` | Covered | — |
| 24 | List deployments | `sigma_list_deployments` | Covered | — |
| 25 | Multi-tenant operations | `sigma_list_tenants` + `sigma_create_tenant` | Covered | RFC 8693 token exchange with per-tenant caching |

## Key Improvements Over Official Recipes

- **Batched team-member operations**: `sigma_update_team_members` accepts `add` and `remove` lists in one call, vs the official recipe's one-member-at-a-time approach.
- **Auto-pagination**: The `sigma_list_all_*` tools (`sigma_list_all_workbooks`, `sigma_list_all_members`, `sigma_list_all_teams`, `sigma_list_all_files`, `sigma_list_all_reports`, `sigma_list_all_data_models`) paginate automatically and return all records, vs hardcoded `limit` values that silently drop data beyond the first page.
- **Collected error arrays**: The `@sigma_tool` decorator catches all `SigmaAPIError` exceptions and returns structured JSON — never raises unhandled.
- **Bounded polling with timeouts**: Export operations poll with exponential backoff and a hard timeout, vs infinite polling loops in official examples.
- **Dry-run gating on destructive operations**: `sigma_bulk_deactivate_members`, `sigma_reassign_workbook_ownership`, and `sigma_bulk_sync_tenant_connections` default to `dry_run=True`, reporting what would change without executing.
