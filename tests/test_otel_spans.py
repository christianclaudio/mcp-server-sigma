"""Recorded OpenTelemetry spans of a failing tool hold no secret (template v1.6.0).

FastMCP traces every ``tools/call`` with OpenTelemetry and records the raised exception
on the span (``exception`` event with ``exception.message`` and ``exception.stacktrace``).
A chain-walking exporter would bring back an unredacted original if the redacted
``ToolError`` still carried it on ``__cause__`` / ``__context__``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastmcp import Client
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import sigma_mcp.server as server

EXPORTER = InMemorySpanExporter()


@pytest.fixture(autouse=True)
def _provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Install an in-memory provider for this test only, then restore the global one.

    ``trace.set_tracer_provider`` can be called once per process, so the test patches the
    module globals it sets (and the set-once guard) and monkeypatch puts them back after.
    """
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER_SET_ONCE", trace.Once())
    yield
    provider.shutdown()


def _span_text(span: Any) -> str:
    parts = [span.name, str(span.status.description or "")]
    parts += [f"{k}={v}" for k, v in (span.attributes or {}).items()]
    for event in span.events:
        parts.append(event.name)
        parts += [f"{k}={v}" for k, v in (event.attributes or {}).items()]
    return "\n".join(parts)


@pytest.mark.asyncio
async def test_failing_tool_spans_hold_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    inner, outer = "hunter2-inner-otel", "sk-outer-otel-secret-12345"
    quoted = "FAKE part2-otel"

    class _FailingClient:
        async def list_files(self, params: Any) -> Any:
            try:
                raise RuntimeError(f"pool failed password={inner}")
            except RuntimeError as exc:
                raise ValueError(f'upstream rejected Authorization: Bearer {outer} {{"token": "{quoted}"}}') from exc

    async def get_client(ctx: Any = None) -> Any:
        return _FailingClient()

    monkeypatch.setattr(server, "get_client", get_client)
    EXPORTER.clear()
    async with Client(server.create_server(profile="full")) as client:
        names = [t.name for t in await client.list_tools()]
        tool = next(n for n in names if n.endswith("list_files"))
        result = await client.call_tool(tool, {}, raise_on_error=False)
    assert result.is_error

    spans = EXPORTER.get_finished_spans()
    assert spans, "FastMCP recorded no spans"
    tool_spans = [s for s in spans if "list_files" in _span_text(s)]
    assert tool_spans, [s.name for s in spans]
    assert any(e.name == "exception" for s in tool_spans for e in s.events), (
        "no exception event recorded, so the check below would prove nothing"
    )
    text = "\n".join(_span_text(s) for s in spans)
    assert "Bearer ***REDACTED***" in text
    for secret in (inner, outer, quoted, "part2-otel"):
        assert secret not in text
