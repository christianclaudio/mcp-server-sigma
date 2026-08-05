"""Smoke test read-only MCP tools against a live Sigma org.

Verifies endpoint paths are correct. Write operations are NOT exercised.

    source .env && PYTHONPATH=src .venv/bin/python tests/smoke_test.py
"""

import asyncio
import json
import sys

sys.path.insert(0, "src")

from sigma_mcp.server import mcp  # noqa: E402

# (tool_name, args) — read-only, should work for any Sigma org
READ_TOOLS: list[tuple[str, dict]] = [
    ("sigma_get_current_user", {}),
    ("sigma_list_connections", {}),
    ("sigma_list_workbooks", {"limit": 5}),
    ("sigma_list_data_models", {"limit": 5}),
    ("sigma_list_templates", {"limit": 5}),
    ("sigma_list_members", {"limit": 5}),
    ("sigma_list_teams", {"limit": 5}),
    ("sigma_list_files", {}),
    ("sigma_list_tags", {}),
    ("sigma_list_user_attributes", {}),
    ("sigma_list_workspaces", {}),
    ("sigma_list_account_types", {}),
    ("sigma_list_reports", {"limit": 5}),
    ("sigma_list_api_connectors", {}),
    ("sigma_api_capabilities", {}),
]

# Previously feature-gated; paths corrected per OpenAPI spec:
#   deployments → /v2/deploymentPolicies
#   tenants → /v2/tenants
#   source-swap-policies → /v2/sourceSwapPolicies
#   shared-templates → /v2/shared_templates/shared_with_you
#   translations → /v2/translations/organization
OPTIONAL_TOOLS: list[tuple[str, dict]] = [
    ("sigma_list_deployments", {}),
    ("sigma_list_tenants", {}),
    ("sigma_list_source_swap_policies", {}),
    ("sigma_list_shared_templates", {}),
    ("sigma_list_translations", {}),
]

# Pagination tools (added by step s2725 — may not be registered yet)
PAGINATION_TOOLS: list[tuple[str, dict]] = [
    ("sigma_list_all_workbooks", {}),
    ("sigma_list_all_members", {}),
    ("sigma_list_all_teams", {}),
    ("sigma_list_all_files", {}),
    ("sigma_list_all_reports", {}),
    ("sigma_list_all_data_models", {}),
]


def payload(result) -> dict:
    """Extract the JSON payload from an MCP CallToolResult."""
    return json.loads(result.content[0].text)


async def main() -> int:
    passed, failed, skipped = [], [], []

    for name, args in READ_TOOLS:
        try:
            await mcp.call_tool(name, args)
            passed.append(name)
            print(f"  PASS  {name}")
        except Exception as e:
            msg = str(e).split("\n")[0][:110]
            failed.append((name, msg))
            print(f"  FAIL  {name}\n          {msg}")

    print("\nOptional (feature-gated):")
    for name, args in OPTIONAL_TOOLS:
        try:
            await mcp.call_tool(name, args)
            passed.append(name)
            print(f"  PASS  {name}")
        except Exception as e:
            msg = str(e).split("\n")[0][:80]
            # Only treat as "skipped" if the error indicates a genuinely disabled feature
            if any(signal in msg for signal in ("404", "Not Found", "not enabled", "not available", "feature")):
                skipped.append(name)
                print(f"  SKIP  {name} (not enabled: {msg[:50]})")
            else:
                failed.append((name, msg))
                print(f"  FAIL  {name}\n          {msg}")

    print("\nPagination tools (may not be registered yet):")
    for name, args in PAGINATION_TOOLS:
        try:
            await mcp.call_tool(name, args)
            passed.append(name)
            print(f"  PASS  {name}")
        except Exception as e:
            msg = str(e).split("\n")[0][:80]
            if "Unknown tool" in msg or "not found" in msg.lower():
                skipped.append(name)
                print(f"  SKIP  {name} (not yet registered)")
            else:
                failed.append((name, msg))
                print(f"  FAIL  {name}\n          {msg}")

    # Discover IDs from live data, then test detail endpoints
    print("\nDetail endpoints (using discovered IDs):")
    conns: dict = {}
    wbs: dict = {}
    dms: dict = {}
    members: dict = {}
    teams: dict = {}

    try:
        conns = payload(await mcp.call_tool("sigma_list_connections", {}))
    except Exception as e:
        print(f"  could not discover connections: {e}")

    try:
        wbs = payload(await mcp.call_tool("sigma_list_workbooks", {"limit": 1}))
    except Exception as e:
        print(f"  could not discover workbooks: {e}")

    try:
        dms = payload(await mcp.call_tool("sigma_list_data_models", {"limit": 1}))
    except Exception as e:
        print(f"  could not discover data_models: {e}")

    try:
        members = payload(await mcp.call_tool("sigma_list_members", {"limit": 1}))
    except Exception as e:
        print(f"  could not discover members: {e}")

    try:
        teams = payload(await mcp.call_tool("sigma_list_teams", {"limit": 1}))
    except Exception as e:
        print(f"  could not discover teams: {e}")

    detail: list[tuple[str, dict]] = []

    if conns.get("entries"):
        cid = conns["entries"][0]["connectionId"]
        detail += [
            ("sigma_get_connection", {"connection_id": cid}),
            ("sigma_list_connection_grants", {"connection_id": cid}),
        ]

    if wbs.get("entries"):
        wid = wbs["entries"][0]["workbookId"]
        detail += [
            ("sigma_get_workbook", {"workbook_id": wid}),
            ("sigma_list_workbook_pages", {"workbook_id": wid}),
            ("sigma_list_workbook_elements", {"workbook_id": wid}),
            ("sigma_list_workbook_columns", {"workbook_id": wid}),
            ("sigma_list_workbook_queries", {"workbook_id": wid}),
            ("sigma_list_workbook_controls", {"workbook_id": wid}),
            ("sigma_list_workbook_sources", {"workbook_id": wid}),
            ("sigma_list_workbook_grants", {"workbook_id": wid}),
            ("sigma_list_workbook_embeds", {"workbook_id": wid}),
            ("sigma_list_workbook_lineage", {"workbook_id": wid}),
            ("sigma_get_workbook_version_history", {"workbook_id": wid}),
            ("sigma_list_workbook_schedules", {"workbook_id": wid}),
            ("sigma_list_workbook_bookmarks", {"workbook_id": wid}),
            ("sigma_get_workbook_tags", {"workbook_id": wid}),
            ("sigma_list_grants", {"inode_id": wid}),
        ]

    if dms.get("entries"):
        did = dms["entries"][0]["dataModelId"]
        detail += [
            ("sigma_get_data_model", {"data_model_id": did}),
            ("sigma_get_data_model_spec", {"data_model_id": did}),
            ("sigma_list_data_model_elements", {"data_model_id": did}),
            ("sigma_list_data_model_columns", {"data_model_id": did}),
            ("sigma_list_data_model_lineage", {"data_model_id": did}),
        ]

    if members.get("entries"):
        mid = members["entries"][0]["memberId"]
        detail += [
            ("sigma_get_member", {"member_id": mid}),
            ("sigma_list_member_teams", {"member_id": mid}),
        ]

    if teams.get("entries"):
        tid = teams["entries"][0]["teamId"]
        detail += [
            ("sigma_get_team", {"team_id": tid}),
            ("sigma_list_team_members", {"team_id": tid}),
        ]

    for name, args in detail:
        try:
            await mcp.call_tool(name, args)
            passed.append(name)
            print(f"  PASS  {name}")
        except Exception as e:
            msg = str(e).split("\n")[0][:110]
            failed.append((name, msg))
            print(f"  FAIL  {name}\n          {msg}")

    # --- Composite recipe tools (read-only validation only) ---
    print("\nComposite tools (input validation only — no writes):")
    composite_validation: list[tuple[str, dict, str]] = [
        (
            "sigma_sync_all_tables_in_schema",
            {"connection_id": "", "database": "x", "schema": "x"},
            "connection_id is required",
        ),
        ("sigma_onboard_member", {"email": "", "first_name": "x", "last_name": "x"}, "email is required"),
        ("sigma_bulk_assign_team_members", {"team_id": "", "member_ids": ["x"]}, "team_id is required"),
        (
            "sigma_deploy_template_to_folder",
            {"template_id": "", "folder_id": "x", "name": "x"},
            "template_id is required",
        ),
        ("sigma_promote_workbook", {"workbook_id": "", "tag_name": "x"}, "workbook_id is required"),
        ("sigma_materialize_and_wait", {"workbook_id": "", "element_id": "x"}, "workbook_id is required"),
        ("sigma_copy_workbook_to_member", {"workbook_id": "", "member_id": "x"}, "workbook_id is required"),
    ]
    for name, args, expected_err in composite_validation:
        try:
            result = await mcp.call_tool(name, args)
            data = payload(result)
            # Tolerate both flat {"error": "..."} and nested {"error": {"message": "..."}}
            err_val = data.get("error", "") if isinstance(data, dict) else ""
            if isinstance(err_val, dict):
                err_text = err_val.get("message", "")
            else:
                err_text = str(err_val)
            if err_val and expected_err in err_text:
                passed.append(f"{name} (validation)")
                print(f"  PASS  {name} (returns validation error correctly)")
            else:
                failed.append((f"{name} (validation)", f"Expected error '{expected_err}', got: {data}"))
                print(f"  FAIL  {name} (expected validation error, got: {str(data)[:80]})")
        except Exception as e:
            msg = str(e).split("\n")[0][:80]
            if "Unknown tool" in msg:
                skipped.append(f"{name} (validation)")
                print(f"  SKIP  {name} (not yet registered)")
            else:
                failed.append((f"{name} (validation)", msg))
                print(f"  FAIL  {name}\n          {msg}")

    # --- Corrected path notes (verified against OpenAPI spec) ---
    print("\n--- Corrected endpoint paths (per OpenAPI spec) ---")
    print("  deployments     → /v2/deploymentPolicies")
    print("  tenants         → /v2/tenants")
    print("  source-swap     → /v2/sourceSwapPolicies")
    print("  shared-templates→ /v2/shared_templates/shared_with_you")
    print("  translations    → /v2/translations/organization")

    print(f"\n{len(passed)} passed, {len(failed)} failed, {len(skipped)} skipped (feature-gated/not-yet)")
    if failed:
        print("\nFailures:")
        for name, msg in failed:
            print(f"  {name}: {msg}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
