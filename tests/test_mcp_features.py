"""Unit tests for MCP native Resources, Prompts, and Structured Logging."""

from __future__ import annotations

import json
import logging
import re
from unittest.mock import patch

import pytest
from fastmcp import Client
from fastmcp.resources import ResourceResult
from mcp import MCPError
from mcp.types import TextResourceContents

from sigma_mcp.profiles import PROFILES
from sigma_mcp.server import (
    StructuredJSONFormatter,
    configure_logging,
    create_server,
    mcp,
)


@pytest.mark.asyncio
async def test_mcp_resources_registered() -> None:
    resources = await mcp.list_resources()
    resource_uris = [r.uri for r in resources]
    assert "elements://reference/formulas" in resource_uris
    assert "admin://reference/capabilities" in resource_uris

    formula_res = await mcp.read_resource("elements://reference/formulas")
    assert isinstance(formula_res, ResourceResult) and len(formula_res.contents) == 1
    assert "Sigma" in str(formula_res.contents[0].content)

    caps_res = await mcp.read_resource("admin://reference/capabilities")
    assert isinstance(caps_res, ResourceResult) and len(caps_res.contents) == 1
    caps_data = json.loads(str(caps_res.contents[0].content))
    assert "connections" in caps_data["supported_domains"]


# Every static resource the root registers, with its declared MIME type.
EXPECTED_RESOURCES = {
    "elements://reference/formulas": "text/markdown",
    "admin://reference/capabilities": "application/json",
    "elements://reference/docs-index": "text/plain",
    "admin://webhooks/recent": "text/plain",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", sorted(PROFILES))
async def test_resources_read_over_the_wire(profile: str) -> None:
    """resources/read through an MCP client returns contents for every resource and template.

    Calls the protocol handler, not ``FastMCP.read_resource`` directly, so a return value
    the handler cannot convert surfaces here instead of as ``Internal server error``.
    """
    server = create_server(profile=profile)
    async with Client(server) as client:
        resources = await client.list_resources()
        assert {str(r.uri): r.mime_type for r in resources} == EXPECTED_RESOURCES
        for resource in resources:
            contents = await client.read_resource(str(resource.uri))
            assert len(contents) == 1
            item = contents[0]
            assert isinstance(item, TextResourceContents)
            assert str(item.uri) == str(resource.uri)
            assert item.mime_type == resource.mime_type
            assert item.text

        # No templates are registered today; any added later is read here too.
        templates = await client.list_resource_templates()
        for template in templates:
            uri = re.sub(r"\{[^}]+\}", "x", template.uri_template)
            assert await client.read_resource(uri)

        formulas = await client.read_resource("elements://reference/formulas")
        assert isinstance(formulas[0], TextResourceContents) and "Sigma" in formulas[0].text
        caps = await client.read_resource("admin://reference/capabilities")
        assert isinstance(caps[0], TextResourceContents)
        assert "connections" in json.loads(caps[0].text)["supported_domains"]


@pytest.mark.asyncio
async def test_resources_read_unknown_uri_over_the_wire() -> None:
    """An unknown URI is a not-found error naming the URI, not an internal error."""
    async with Client(create_server(profile="full")) as client:
        with pytest.raises(MCPError, match="elements://reference/missing") as exc:
            await client.read_resource("elements://reference/missing")
        assert "Internal server error" not in str(exc.value)


@pytest.mark.asyncio
async def test_mcp_prompts_registered() -> None:
    prompts = await mcp.list_prompts()
    prompt_names = [p.name for p in prompts]
    assert "workbooks_provision_tenant_dashboard" in prompt_names
    assert "admin_audit_organization_permissions" in prompt_names
    assert "datasets_prepare_data_model" in prompt_names

    p_result = await mcp.get_prompt(
        "workbooks_provision_tenant_dashboard",
        {"template_id": "tmpl-123", "folder_id": "fld-456", "tenant_id": "org-789"},
    )
    assert hasattr(p_result, "messages")
    msg_text = getattr(p_result.messages[0].content, "text", "")
    assert "tmpl-123" in msg_text
    assert "org-789" in msg_text


def test_structured_json_formatter() -> None:
    formatter = StructuredJSONFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    setattr(record, "tool_name", "workbooks_get_workbook")
    setattr(record, "duration_ms", 42.5)

    formatted = formatter.format(record)
    data = json.loads(formatted)
    assert data["message"] == "Test message"
    assert data["mcp_tool"] == "workbooks_get_workbook"
    assert data["duration_ms"] == 42.5


def test_configure_logging_json() -> None:
    with patch.dict("os.environ", {"SIGMA_MCP_LOG_FORMAT": "json"}):
        configure_logging()
        assert len(logging.root.handlers) >= 1
        assert isinstance(logging.root.handlers[0].formatter, StructuredJSONFormatter)
