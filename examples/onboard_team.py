"""Bulk onboard team members into Sigma with team assignments.

Usage:
    export SIGMA_CLIENT_ID='...'
    export SIGMA_CLIENT_SECRET='...'
    export SIGMA_API_BASE_URL='https://api.us-a.aws.sigmacomputing.com'
    python examples/onboard_team.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, "src")

from sigma_mcp.client import SigmaClient


async def onboard_member(
    client: SigmaClient,
    email: str,
    first_name: str,
    last_name: str,
    member_type: str = "viewer",
    team_ids: list[str] | None = None,
) -> dict:
    """Create a member and add them to teams."""
    member = await client.create_member(
        {
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "memberType": member_type,
        }
    )
    member_id = member.get("memberId")
    teams_added = []

    if team_ids and member_id:
        for tid in team_ids:
            try:
                await client.update_team_members(tid, {"add": [member_id]})
                teams_added.append(tid)
            except Exception as e:
                teams_added.append(f"{tid}: FAILED ({e})")

    return {"member": member, "teams_added": teams_added}


async def main() -> None:
    async with SigmaClient(
        os.environ["SIGMA_CLIENT_ID"],
        os.environ["SIGMA_CLIENT_SECRET"],
        os.environ["SIGMA_API_BASE_URL"],
    ) as client:
        # Configuration — replace with your actual team IDs
        ANALYTICS_TEAM = "team-analytics-uuid"
        FINANCE_TEAM = "team-finance-uuid"

        # New hires to onboard
        new_members = [
            {
                "email": "alice.smith@company.com",
                "first_name": "Alice",
                "last_name": "Smith",
                "member_type": "creator",
                "team_ids": [ANALYTICS_TEAM, FINANCE_TEAM],
            },
            {
                "email": "bob.jones@company.com",
                "first_name": "Bob",
                "last_name": "Jones",
                "member_type": "viewer",
                "team_ids": [ANALYTICS_TEAM],
            },
            {
                "email": "carol.wu@company.com",
                "first_name": "Carol",
                "last_name": "Wu",
                "member_type": "creator",
                "team_ids": [FINANCE_TEAM],
            },
        ]

        results = []
        for m in new_members:
            print(f"Onboarding: {m['email']}...")
            result = await onboard_member(
                client,
                email=m["email"],
                first_name=m["first_name"],
                last_name=m["last_name"],
                member_type=m["member_type"],
                team_ids=m.get("team_ids"),
            )
            results.append(result)
            member_id = result["member"].get("memberId", "unknown")
            print(f"  Created member: {member_id}, teams: {result['teams_added']}")

        print(f"\nOnboarded {len(results)} members.")
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
