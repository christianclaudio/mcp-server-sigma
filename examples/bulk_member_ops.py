"""Bulk member operations with dry-run support.

Demonstrates:
  - Adding or removing team members via update_team_members
  - Dry-run mode to preview changes without executing

Usage:
    export SIGMA_CLIENT_ID='...'
    export SIGMA_CLIENT_SECRET='...'
    export SIGMA_API_BASE_URL='https://api.us-a.aws.sigmacomputing.com'
    python examples/bulk_member_ops.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, "src")

from sigma_mcp.client import SigmaClient


async def bulk_remove_team_members(
    client: SigmaClient,
    team_id: str,
    member_ids: list[str],
    *,
    dry_run: bool = True,
) -> dict:
    """Remove multiple members from a team.

    With dry_run=True (default), only previews what would happen.
    Set dry_run=False to execute the removal.
    """
    if dry_run:
        return {
            "action": "remove_team_members",
            "team_id": team_id,
            "member_ids": member_ids,
            "dry_run": True,
            "message": f"Would remove {len(member_ids)} members from team {team_id}",
        }

    result = await client.update_team_members(team_id, {"remove": member_ids})
    return {
        "action": "remove_team_members",
        "team_id": team_id,
        "member_ids": member_ids,
        "dry_run": False,
        "result": result,
    }


async def bulk_add_team_members(
    client: SigmaClient,
    team_id: str,
    member_ids: list[str],
    *,
    dry_run: bool = True,
) -> dict:
    """Add multiple members to a team.

    With dry_run=True (default), only previews what would happen.
    Set dry_run=False to execute the addition.
    """
    if dry_run:
        return {
            "action": "add_team_members",
            "team_id": team_id,
            "member_ids": member_ids,
            "dry_run": True,
            "message": f"Would add {len(member_ids)} members to team {team_id}",
        }

    result = await client.update_team_members(team_id, {"add": member_ids})
    return {
        "action": "add_team_members",
        "team_id": team_id,
        "member_ids": member_ids,
        "dry_run": False,
        "result": result,
    }


async def main() -> None:
    async with SigmaClient(
        os.environ["SIGMA_CLIENT_ID"],
        os.environ["SIGMA_CLIENT_SECRET"],
        os.environ["SIGMA_API_BASE_URL"],
    ) as client:
        team_id = "your-team-uuid"
        members_to_remove = ["member-uuid-1", "member-uuid-2", "member-uuid-3"]

        # Dry run first (safe preview)
        print("=== DRY RUN ===")
        preview = await bulk_remove_team_members(client, team_id, members_to_remove, dry_run=True)
        print(json.dumps(preview, indent=2))

        # Uncomment to execute:
        # print("\n=== EXECUTING ===")
        # result = await bulk_remove_team_members(client, team_id, members_to_remove, dry_run=False)
        # print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
