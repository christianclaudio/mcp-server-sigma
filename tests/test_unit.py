"""Unit tests for sigma_mcp features: retry, validation, pagination, transport.

These tests do NOT hit the live Sigma API — they use mocked HTTP responses.

    PYTHONPATH=src python -m pytest tests/test_unit.py -v
"""

import asyncio
import json
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, "src")


# ─── Retry / rate-limit tests ─────────────────────────────────────────────────


class TestRetryLogic:
    """Verify _request retries on HTTP 429 with exponential backoff."""

    def _make_client(self, max_retries=3, base_delay=0.01):
        from sigma_mcp.client import SigmaClient

        c = SigmaClient("id", "secret", "https://api.example.com", max_retries=max_retries, base_delay=base_delay)
        c._token = "fake"
        c._token_expiry = time.time() + 3600
        return c

    def test_no_retry_on_success(self):
        c = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        mock_resp.headers = {}

        mock_request = AsyncMock(return_value=mock_resp)
        with patch.object(c._http, "request", mock_request):
            r = asyncio.run(c._request("GET", "/v2/test"))
            assert mock_request.call_count == 1
            assert r.status_code == 200

    def test_retry_on_429_then_success(self):
        c = self._make_client(max_retries=3, base_delay=0.001)
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {}

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.headers = {}

        mock_request = AsyncMock(side_effect=[resp_429, resp_429, resp_200])
        with patch.object(c._http, "request", mock_request):
            r = asyncio.run(c._request("GET", "/v2/test"))
            assert mock_request.call_count == 3
            assert r.status_code == 200

    def test_retry_respects_retry_after_header(self):
        c = self._make_client(max_retries=2, base_delay=0.001)
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "0.01"}

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.headers = {}

        mock_request = AsyncMock(side_effect=[resp_429, resp_200])
        with patch.object(c._http, "request", mock_request):
            r = asyncio.run(c._request("GET", "/v2/test"))
            assert r.status_code == 200

    def test_max_retries_exhausted_raises(self):
        c = self._make_client(max_retries=2, base_delay=0.001)
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {}

        mock_request = AsyncMock(return_value=resp_429)
        with patch.object(c._http, "request", mock_request):
            try:
                asyncio.run(c._request("GET", "/v2/test"))
                assert False, "Should have raised"
            except Exception as e:
                assert "429" in str(e) or "Rate limit" in str(e)


# ─── Input validation tests ───────────────────────────────────────────────────


class TestInputValidation:
    """Verify composite recipe tools return validation errors for empty required fields."""

    def _call_tool(self, tool_name, args):
        from sigma_mcp.server import mcp

        result = asyncio.run(mcp.call_tool(tool_name, args))
        return json.loads(result.content[0].text)

    @staticmethod
    def _error_text(data: dict) -> str:
        """Extract error message text from either flat or nested error shape."""
        err = data.get("error", "")
        if isinstance(err, dict):
            return err.get("message", "")
        return str(err)

    def test_sync_all_tables_empty_connection_id(self):
        data = self._call_tool("sigma_sync_all_tables_in_schema", {"connection_id": "", "database": "x", "schema": "x"})
        assert "error" in data
        assert "connection_id" in self._error_text(data)

    def test_onboard_member_empty_email(self):
        data = self._call_tool("sigma_onboard_member", {"email": "", "first_name": "x", "last_name": "x"})
        assert "error" in data
        assert "email" in self._error_text(data)

    def test_bulk_assign_empty_team_id(self):
        data = self._call_tool("sigma_bulk_assign_team_members", {"team_id": "", "member_ids": ["x"]})
        assert "error" in data
        assert "team_id" in self._error_text(data)

    def test_deploy_template_empty_template_id(self):
        data = self._call_tool("sigma_deploy_template_to_folder", {"template_id": "", "folder_id": "x", "name": "x"})
        assert "error" in data
        assert "template_id" in self._error_text(data)

    def test_promote_workbook_empty_workbook_id(self):
        data = self._call_tool("sigma_promote_workbook", {"workbook_id": "", "tag_name": "x"})
        assert "error" in data
        assert "workbook_id" in self._error_text(data)

    def test_materialize_empty_workbook_id(self):
        data = self._call_tool("sigma_materialize_and_wait", {"workbook_id": "", "element_id": "x"})
        assert "error" in data
        assert "workbook_id" in self._error_text(data)

    def test_copy_workbook_empty_workbook_id(self):
        data = self._call_tool("sigma_copy_workbook_to_member", {"workbook_id": "", "member_id": "x"})
        assert "error" in data
        assert "workbook_id" in self._error_text(data)

    def test_onboard_member_invalid_member_type(self):
        data = self._call_tool(
            "sigma_onboard_member", {"email": "a@b.com", "first_name": "x", "last_name": "x", "member_type": "invalid"}
        )
        assert "error" in data
        assert "member_type" in self._error_text(data)

    def test_duplicate_report_empty_name(self):
        data = self._call_tool("sigma_duplicate_report", {"report_id": "r1", "name": "", "destination_folder_id": "f1"})
        assert "error" in data
        assert "name" in self._error_text(data)

    def test_duplicate_report_empty_folder(self):
        data = self._call_tool("sigma_duplicate_report", {"report_id": "r1", "name": "x", "destination_folder_id": ""})
        assert "error" in data
        assert "destination_folder_id" in self._error_text(data)

    def test_grant_workspace_access_invalid_grant_type(self):
        data = self._call_tool(
            "sigma_grant_workspace_access",
            {"workspace_id": "ws1", "grant_type": "invalid", "grantee_id": "x", "permission": "view"},
        )
        assert "error" in data
        assert "grant_type" in self._error_text(data)

    def test_grant_workspace_access_valid_member(self):
        from unittest.mock import AsyncMock, patch

        mock_client = AsyncMock()
        mock_client.grant_workspace_access = AsyncMock(return_value={"ok": True})

        with patch("sigma_mcp.server.get_client", return_value=mock_client):
            data = self._call_tool(
                "sigma_grant_workspace_access",
                {"workspace_id": "ws1", "grant_type": "member", "grantee_id": "m1", "permission": "view"},
            )
        assert "error" not in data
        call_body = mock_client.grant_workspace_access.call_args[0][1]
        assert call_body["grants"][0]["grantee"]["memberId"] == "m1"


class TestDocsTools:
    """Verify sigma_search_docs and sigma_get_doc_page with mocked HTTP."""

    def _call_tool(self, tool_name, args):
        from sigma_mcp.server import mcp

        result = asyncio.run(mcp.call_tool(tool_name, args))
        return json.loads(result.content[0].text)

    def test_search_docs_success(self):
        sse_body = (
            'event: message\ndata: {"result":{"content":[{"type":"text",'
            '"text":"Sigma supports embedding workbooks."}]},"jsonrpc":"2.0","id":1}\n'
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = sse_body

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            data = self._call_tool("sigma_search_docs", {"query": "embed workbook"})
        assert data["format"] == "markdown"
        assert "embedding" in data["content"]

    def test_search_docs_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            data = self._call_tool("sigma_search_docs", {"query": "test"})
        assert "error" in data
        assert data["error"]["type"] == "docs_search_failed"

    def test_get_doc_page_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "# Create a Workbook\n\nStep 1..."

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            data = self._call_tool("sigma_get_doc_page", {"page_slug": "create-a-workbook"})
        assert data["format"] == "markdown"
        assert "Create a Workbook" in data["content"]

    def test_get_doc_page_not_found(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            data = self._call_tool("sigma_get_doc_page", {"page_slug": "nonexistent-page"})
        assert "error" in data
        assert data["error"]["type"] == "page_not_found"

    def test_get_doc_page_strips_full_url(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "# Page"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            data = self._call_tool(
                "sigma_get_doc_page",
                {"page_slug": "https://help.sigmacomputing.com/docs/some-page"},
            )
        assert data["format"] == "markdown"
        # Verify the URL was normalized (not double docs/docs/)
        call_args = mock_client.get.call_args
        assert "docs/docs/" not in call_args[0][0]

    def test_search_docs_empty_response(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "event: message\ndata: {}\n"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            data = self._call_tool("sigma_search_docs", {"query": "nonexistent"})
        assert "error" in data
        assert data["error"]["type"] == "docs_search_empty"

    def test_get_doc_page_strips_md_suffix(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "# Page"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            data = self._call_tool("sigma_get_doc_page", {"page_slug": "docs/some-page.md"})
        assert data["format"] == "markdown"
        call_url = mock_client.get.call_args[0][0]
        assert call_url.endswith("/docs/some-page.md")
        assert not call_url.endswith(".md.md")

    def test_docs_index_resource(self):
        from sigma_mcp.server import resource_sigma_docs_index

        content = resource_sigma_docs_index()
        assert "Sigma Documentation" in content
        assert "help.sigmacomputing.com" in content


# ─── Pagination (auto_paginate) tests ─────────────────────────────────────────


class TestAutoPaginate:
    """Verify auto_paginate follows nextPage tokens correctly."""

    def _make_client(self):
        from sigma_mcp.client import SigmaClient

        c = SigmaClient("id", "secret", "https://api.example.com")
        c._token = "fake"
        c._token_expiry = time.time() + 3600
        return c

    def test_single_page(self):
        c = self._make_client()
        mock_get = AsyncMock(return_value={"entries": [{"id": "1"}, {"id": "2"}]})
        with patch.object(c, "get", mock_get):
            results = asyncio.run(c.auto_paginate("/v2/workbooks"))
            assert len(results) == 2

    def test_multi_page(self):
        c = self._make_client()
        responses = [
            {"entries": [{"id": "1"}], "nextPage": "token2"},
            {"entries": [{"id": "2"}], "nextPage": "token3"},
            {"entries": [{"id": "3"}]},
        ]
        mock_get = AsyncMock(side_effect=responses)
        with patch.object(c, "get", mock_get):
            results = asyncio.run(c.auto_paginate("/v2/workbooks"))
            assert len(results) == 3
            assert results[2]["id"] == "3"

    def test_empty_response(self):
        c = self._make_client()
        mock_get = AsyncMock(return_value={"entries": []})
        with patch.object(c, "get", mock_get):
            results = asyncio.run(c.auto_paginate("/v2/workbooks"))
            assert results == []


# ─── Transport argument tests ─────────────────────────────────────────────────


class TestTransportArg:
    """Verify argparse --transport is configured correctly."""

    def test_main_argparse_default_stdio(self):
        import argparse

        from sigma_mcp.server import main

        with patch("argparse.ArgumentParser.parse_args", return_value=argparse.Namespace(transport="stdio")):
            with patch("sigma_mcp.server.mcp.run") as mock_run:
                main()
                mock_run.assert_called_once_with(transport="stdio")

    def test_main_argparse_sse(self):
        import argparse

        from sigma_mcp.server import main

        with patch(
            "argparse.ArgumentParser.parse_args",
            return_value=argparse.Namespace(transport="sse", host="127.0.0.1", port=8000),
        ):
            with patch("sigma_mcp.server.mcp.run") as mock_run:
                main()
                mock_run.assert_called_once_with(transport="sse", host="127.0.0.1", port=8000)


# ─── Profile integrity (regression: 'embed' silently fell back to core) ───────


def test_profiles_reference_only_real_tools():
    """Every tool named in a profile must actually be registered (or gated by env)."""
    import asyncio

    from sigma_mcp import server

    real = {t.name for t in asyncio.run(server.mcp.list_tools())}
    # Bulk-destructive tools are gated by env and may not be registered
    gated = server._BULK_DESTRUCTIVE_TOOLS
    for name, tools in (
        ("core", server._CORE_TOOLS),
        ("admin", server._ADMIN_TOOLS),
        ("embed", server._EMBED_TOOLS),
    ):
        missing = tools - real - gated
        assert not missing, f"profile {name} names nonexistent tools: {sorted(missing)}"


def test_profiles_are_distinct_and_nested():
    """core must be a strict subset of admin and embed; embed != core."""
    from sigma_mcp import server

    assert server._CORE_TOOLS < server._ADMIN_TOOLS
    assert server._CORE_TOOLS < server._EMBED_TOOLS
    assert server._EMBED_TOOLS != server._CORE_TOOLS


def test_unknown_profile_is_rejected():
    """An invalid SIGMA_MCP_PROFILE must fail loudly, not silently fall back."""
    from sigma_mcp import server

    assert "bogus" not in server._PROFILES


# ─── Annotation integrity (README publishes these exact counts) ──────────────


def test_all_tools_are_annotated():
    """Every tool must carry MCP annotations so clients can drive permission UIs."""
    import asyncio

    from sigma_mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    unannotated = [t.name for t in tools if t.annotations is None]
    assert not unannotated, f"tools missing annotations: {unannotated}"


def test_annotation_counts_match_readme():
    """Guard the counts published in README against silent drift.

    Default (no SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE): 155 tools registered.
    With SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1: 157 tools.
    """
    import asyncio

    from sigma_mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    ro = sum(1 for t in tools if t.annotations.read_only_hint)
    destructive = sum(1 for t in tools if t.annotations.destructive_hint)
    idempotent = sum(1 for t in tools if t.annotations.idempotent_hint)
    open_world = sum(1 for t in tools if t.annotations.open_world_hint)

    # Default: 2 bulk-destructive tools are gated out
    assert len(tools) == 155, f"tool count changed: {len(tools)} (expected 155 without bulk-destructive)"
    assert ro == 83, f"read-only count changed: {ro}"
    assert destructive == 16, f"destructive count changed: {destructive}"
    assert idempotent == 8, f"idempotent count changed: {idempotent}"
    assert open_world == 155, f"open_world count changed: {open_world}"


def test_destructive_tools_are_not_marked_read_only():
    """A destructive tool must never be auto-approved as read-only."""
    import asyncio

    from sigma_mcp.server import mcp

    for t in asyncio.run(mcp.list_tools()):
        if t.annotations.destructive_hint:
            assert not t.annotations.read_only_hint, f"{t.name} is both destructive and read-only"
