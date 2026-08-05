"""Promote a workbook through environments using Sigma tags.

Tags in Sigma serve as version promotion markers (e.g., "Staging", "Production").
This script demonstrates the multi-step promote pattern:
  1. List existing tags (or create one)
  2. Apply the tag to a workbook
  3. Optionally remove the previous environment tag

Usage:
    export SIGMA_CLIENT_ID='...'
    export SIGMA_CLIENT_SECRET='...'
    export SIGMA_API_BASE_URL='https://api.us-a.aws.sigmacomputing.com'
    python examples/promote_workbook.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, "src")

from sigma_mcp.client import SigmaClient


async def get_or_create_tag(client: SigmaClient, tag_name: str) -> str:
    """Find an existing tag by name, or create it. Returns the tag ID."""
    tags = await client.list_tags()
    for t in tags.get("entries", []):
        if t.get("name", "").lower() == tag_name.lower():
            return t["tagId"]
    new_tag = await client.create_tag({"name": tag_name})
    return new_tag["tagId"]


async def find_tag(client: SigmaClient, tag_name: str) -> str | None:
    """Find an existing tag by name. Returns the tag ID or None if not found."""
    tags = await client.list_tags()
    for t in tags.get("entries", []):
        if t.get("name", "").lower() == tag_name.lower():
            return t["tagId"]
    return None


async def promote_workbook(
    client: SigmaClient,
    workbook_id: str,
    target_tag: str,
    remove_tag: str | None = None,
) -> dict:
    """Promote a workbook by applying target_tag and optionally removing remove_tag."""
    target_id = await get_or_create_tag(client, target_tag)

    # Apply the new tag first so the workbook is never left untagged
    result = await client.tag_workbook(workbook_id, target_tag)

    # Then remove the old environment tag if specified
    removal_error: str | None = None
    if remove_tag:
        remove_id = await find_tag(client, remove_tag)
        if remove_id:
            try:
                await client.remove_workbook_tag(workbook_id, remove_id)
            except Exception as exc:  # noqa: BLE001
                removal_error = str(exc)

    outcome: dict = {
        "workbook_id": workbook_id,
        "tag": target_tag,
        "tag_id": target_id,
        "result": result,
    }
    if removal_error is not None:
        outcome["removal_failed"] = removal_error
    return outcome


async def main() -> None:
    async with SigmaClient(
        os.environ["SIGMA_CLIENT_ID"],
        os.environ["SIGMA_CLIENT_SECRET"],
        os.environ["SIGMA_API_BASE_URL"],
    ) as client:
        # Configuration — replace with your actual workbook ID
        WORKBOOK_ID = "your-workbook-uuid"

        # Promote from Staging → Production
        print(f"Promoting workbook {WORKBOOK_ID} to Production...")
        result = await promote_workbook(
            client,
            workbook_id=WORKBOOK_ID,
            target_tag="Production",
            remove_tag="Staging",
        )
        print(json.dumps(result, indent=2))

        # You can also promote multiple workbooks at once
        workbooks_to_promote = [
            "workbook-sales-uuid",
            "workbook-finance-uuid",
            "workbook-ops-uuid",
        ]

        print(f"\nBatch promoting {len(workbooks_to_promote)} workbooks...")
        for wb_id in workbooks_to_promote:
            result = await promote_workbook(client, wb_id, target_tag="Production", remove_tag="Staging")
            print(f"  {wb_id}: tagged as {result['tag']}")


if __name__ == "__main__":
    asyncio.run(main())
