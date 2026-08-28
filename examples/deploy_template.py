"""Deploy a Sigma template to multiple folders with source swapping.

Usage:
    export SIGMA_CLIENT_ID='...'
    export SIGMA_CLIENT_SECRET='...'
    export SIGMA_API_BASE_URL='https://api.us-a.aws.sigmacomputing.com'
    python examples/deploy_template.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, "src")

from sigma_mcp.client import SigmaClient


async def deploy_template(
    client: SigmaClient,
    template_id: str,
    folder_id: str,
    name: str,
    connection_mapping: list[dict] | None = None,
) -> dict:
    """Deploy a template into a target folder with optional source swap."""
    wb = await client.save_workbook_from_template(template_id, folder_id, name)
    workbook_id = wb.get("workbookId")

    if connection_mapping and workbook_id:
        swap = await client.swap_workbook_sources(workbook_id, {"connectionMapping": connection_mapping})
        return {"workbook": wb, "swap": swap}

    return {"workbook": wb}


async def main() -> None:
    async with SigmaClient(
        os.environ["SIGMA_CLIENT_ID"],
        os.environ["SIGMA_CLIENT_SECRET"],
        os.environ["SIGMA_API_BASE_URL"],
    ) as client:
        # Configuration — replace with your actual IDs
        TEMPLATE_ID = "your-template-uuid"
        CONNECTION_DEV = "your-dev-connection-uuid"
        CONNECTION_PROD = "your-prod-connection-uuid"

        # Deploy to multiple teams/folders
        deployments = [
            {"folder_id": "folder-sales-uuid", "name": "Sales Dashboard - Prod"},
            {"folder_id": "folder-finance-uuid", "name": "Finance Dashboard - Prod"},
            {"folder_id": "folder-ops-uuid", "name": "Operations Dashboard - Prod"},
        ]

        mapping = [
            {
                "fromId": CONNECTION_DEV,
                "toId": CONNECTION_PROD,
                "paths": [{"fromPath": ["DEV", "MARTS"], "toPath": ["PROD", "MARTS"]}],
            }
        ]

        results = []
        for dep in deployments:
            print(f"Deploying: {dep['name']}...")
            result = await deploy_template(
                client,
                template_id=TEMPLATE_ID,
                folder_id=dep["folder_id"],
                name=dep["name"],
                connection_mapping=mapping,
            )
            results.append(result)
            print(f"  Created workbook: {result['workbook'].get('workbookId')}")

        print(f"\nDeployed {len(results)} workbooks.")
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
