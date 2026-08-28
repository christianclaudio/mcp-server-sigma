"""Export a workbook to PDF and download it.

Demonstrates the async export workflow with bounded polling.

Usage:
    export SIGMA_CLIENT_ID='...'
    export SIGMA_CLIENT_SECRET='...'
    export SIGMA_API_BASE_URL='https://api.us-a.aws.sigmacomputing.com'
    python examples/export_and_download.py
"""

import asyncio
import os
import sys

sys.path.insert(0, "src")

from sigma_mcp.client import SigmaClient


async def export_workbook(client: SigmaClient, workbook_id: str, format: str = "pdf") -> dict:
    """Export a workbook and poll until the job completes."""
    job = await client.export_workbook(workbook_id, format)
    query_id = job.get("queryId")
    if not query_id:
        return {"error": "No queryId returned", "job": job}

    # Poll with bounded timeout
    max_attempts = 30
    for attempt in range(max_attempts):
        status = await client.get_export_status(workbook_id, query_id)
        state = status.get("status", "")
        if state == "completed":
            return {"status": "completed", "download_url": status.get("url")}
        if state == "failed":
            return {"status": "failed", "detail": status}
        await asyncio.sleep(2 * (1.2**attempt))  # Exponential backoff

    return {"status": "timeout", "last_check": status}


async def main() -> None:
    async with SigmaClient(
        os.environ["SIGMA_CLIENT_ID"],
        os.environ["SIGMA_CLIENT_SECRET"],
        os.environ["SIGMA_API_BASE_URL"],
    ) as client:
        workbook_id = "your-workbook-uuid"

        print(f"Exporting workbook {workbook_id} to PDF...")
        result = await export_workbook(client, workbook_id, format="pdf")
        print(f"Result: {result}")

        if result.get("download_url"):
            print(f"Download: {result['download_url']}")


if __name__ == "__main__":
    asyncio.run(main())
