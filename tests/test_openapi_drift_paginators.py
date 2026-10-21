"""The drift check sees paths the client reads through its paging helpers.

``auto_paginate``, ``paged_list`` and their ``_token`` forms GET a path page by page; the drift
visitor treats them like ``self.get`` (with the paging parameters each adds), so a wrong path or a deprecated
parameter behind a paged list is still caught.
"""

from __future__ import annotations

from pathlib import Path

from scripts.check_openapi_drift import extract_client_calls

SOURCE = """
class C:
    async def a(self):
        return {"entries": await self.auto_paginate(f"/v2/teams/{team_id}/members", {"name": "x"})}

    async def b(self):
        return {"entries": await self.auto_paginate_token("/v2/tenants")}

    async def c(self, path):
        return await self.auto_paginate(path)

    async def d(self, workbook_id):
        return await self.paged_list(f"/v2/workbooks/{workbook_id}/pages")

    async def e(self, report_id):
        return await self.paged_list_token(f"/v2/reports/{report_id}/lineage", {"x": 1})
"""


def test_paging_helpers_are_checked_as_gets(tmp_path: Path) -> None:
    client = tmp_path / "client.py"
    client.write_text(SOURCE, encoding="utf-8")
    calls = {(c.method, c.normalized_path): c.query_params for c in extract_client_calls(client)}
    assert calls == {
        ("GET", "/v2/teams/{}/members"): {"limit", "page", "name"},
        ("GET", "/v2/tenants"): {"pageSize", "pageToken"},
        ("GET", "/v2/workbooks/{}/pages"): {"limit", "page"},
        ("GET", "/v2/reports/{}/lineage"): {"pageSize", "pageToken", "x"},
    }


def test_real_client_paged_paths_are_seen() -> None:
    calls = extract_client_calls(Path("src/sigma_mcp/client.py"))
    paths = {c.normalized_path for c in calls if c.method == "GET"}
    assert {"/v2/connections", "/v2/tenants", "/v2/reports/{}/elements", "/v2/workbookAgents"} <= paths
    # Seen through paged_list / paged_list_token, which read every page of the older lists.
    assert {"/v2/workbooks/{}/pages", "/v2/accountTypes", "/v3alpha/allowedIps", "/v2/members"} <= paths
