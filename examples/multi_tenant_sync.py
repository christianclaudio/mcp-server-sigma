"""Sync workbooks across multiple tenants using RFC 8693 token exchange.

Demonstrates:
  - Listing all tenants
  - Obtaining tenant-scoped clients
  - Listing workbooks per tenant for audit/inventory

Usage:
    export SIGMA_CLIENT_ID='...'
    export SIGMA_CLIENT_SECRET='...'
    export SIGMA_API_BASE_URL='https://api.us-a.aws.sigmacomputing.com'
    python examples/multi_tenant_sync.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, "src")

from sigma_mcp.client import SigmaClient


async def audit_all_tenants(client: SigmaClient) -> list[dict]:
    """List workbooks across all tenants for inventory."""
    tenants = await client.list_tenants()
    tenant_entries = tenants.get("entries", []) if isinstance(tenants, dict) else []
    results = []

    for tenant in tenant_entries:
        tenant_id = tenant.get("organizationId") if isinstance(tenant, dict) else None
        if not isinstance(tenant_id, str):
            continue
        tenant_name = tenant.get("name", "unknown")
        print(f"  Scanning tenant: {tenant_name} ({tenant_id})...")

        try:
            # Tenant clients share the parent's HTTP transport — do not close them.
            tenant_client = await client.for_tenant(tenant_id)
            workbooks = await tenant_client.list_workbooks()
            entries = workbooks.get("entries", []) if isinstance(workbooks, dict) else []
            results.append(
                {
                    "tenant": tenant_name,
                    "tenant_id": tenant_id,
                    "workbook_count": len(entries),
                    "workbooks": [
                        {"name": wb.get("name"), "id": wb.get("workbookId")}
                        for wb in entries[:5]  # First 5 for brevity
                    ],
                }
            )
        except Exception as e:
            results.append(
                {
                    "tenant": tenant_name,
                    "tenant_id": tenant_id,
                    "error": str(e),
                }
            )

    return results


async def main() -> None:
    async with SigmaClient(
        os.environ["SIGMA_CLIENT_ID"],
        os.environ["SIGMA_CLIENT_SECRET"],
        os.environ["SIGMA_API_BASE_URL"],
    ) as client:
        print("Auditing all tenants...")
        results = await audit_all_tenants(client)

        print(f"\nScanned {len(results)} tenants:")
        for r in results:
            if "error" in r:
                print(f"  {r['tenant']}: ERROR — {r['error']}")
            else:
                print(f"  {r['tenant']}: {r['workbook_count']} workbooks")

        print("\nFull report:")
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
