"""Unit tests for MCP native Resources, Prompts, and Structured Logging."""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest

from sigma_mcp.server import (
    StructuredJSONFormatter,
    configure_logging,
    mcp,
)


@pytest.mark.asyncio
async def test_mcp_resources_registered() -> None:
    resources = await mcp.list_resources()
    resource_uris = [r.uri for r in resources]
    assert "sigma://reference/formulas" in resource_uris
    assert "sigma://reference/capabilities" in resource_uris

    formula_res = await mcp.read_resource("sigma://reference/formulas")
    assert isinstance(formula_res, list) and len(formula_res) == 1
    assert hasattr(formula_res[0], "content") and "Sigma" in str(formula_res[0].content)

    caps_res = await mcp.read_resource("sigma://reference/capabilities")
    assert isinstance(caps_res, list) and len(caps_res) == 1
    caps_data = json.loads(str(caps_res[0].content))
    assert "connections" in caps_data["supported_domains"]


@pytest.mark.asyncio
async def test_mcp_prompts_registered() -> None:
    prompts = await mcp.list_prompts()
    prompt_names = [p.name for p in prompts]
    assert "provision_tenant_dashboard" in prompt_names
    assert "audit_organization_permissions" in prompt_names
    assert "prepare_data_model" in prompt_names

    p_result = await mcp.get_prompt(
        "provision_tenant_dashboard",
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
    setattr(record, "tool_name", "sigma_get_workbook")
    setattr(record, "duration_ms", 42.5)

    formatted = formatter.format(record)
    data = json.loads(formatted)
    assert data["message"] == "Test message"
    assert data["mcp_tool"] == "sigma_get_workbook"
    assert data["duration_ms"] == 42.5


def test_configure_logging_json() -> None:
    with patch.dict("os.environ", {"SIGMA_MCP_LOG_FORMAT": "json"}):
        configure_logging()
        assert len(logging.root.handlers) >= 1
        assert isinstance(logging.root.handlers[0].formatter, StructuredJSONFormatter)
