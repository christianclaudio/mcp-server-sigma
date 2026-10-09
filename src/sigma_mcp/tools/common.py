"""Common utilities, decorators, and annotation constants for domain sub-servers."""

from __future__ import annotations

import functools
import json
import logging
import time
from collections.abc import Callable
from typing import Any, NoReturn

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from sigma_mcp.errors import SigmaAPIError, redact_secrets

logger = logging.getLogger("sigma_mcp")

ANNOTATION_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
ANNOTATION_WRITE_SAFE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
ANNOTATION_DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)
ANNOTATION_IDEMPOTENT = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)


def _invalid_request(message: str) -> NoReturn:
    """Raise an input validation failure as a tool execution error.

    The MCP spec lists input validation errors among tool execution errors, reported in the
    tool result with ``isError: true`` so the model can correct the call. This raises FastMCP
    ``ToolError`` with the redacted ``{"error": {"type": "invalid_request", ...}}`` JSON, the same
    path ``sigma_tool`` uses for API and internal failures, which re-raises it unchanged.
    """
    raise ToolError(json.dumps({"error": {"type": "invalid_request", "message": redact_secrets(message)}})) from None


def _confirm_required(action: str) -> str:
    """Return the confirm two-step prompt as a normal tool result (``isError: false``).

    A destructive or guarded write called without ``confirm=True`` did what it was designed to
    do: it made no change and tells the model how to proceed. That is not a failed call.
    """
    return json.dumps(
        {
            "status": "confirmation_required",
            "executed": False,
            "message": f"{action} was not executed. Re-call this tool with confirm=true to proceed.",
        }
    )


def _summarize_list(data: Any, key_fields: list[str]) -> Any:
    """Helper to summarize high-cardinality list responses when summary_only=True."""
    if not isinstance(data, dict) or "entries" not in data:
        return data
    entries = data.get("entries", [])
    summarized = [{k: item[k] for k in key_fields if k in item} for item in entries if isinstance(item, dict)]
    total = data.get("total", len(entries))
    res: dict[str, Any] = {"total": total, "entries": summarized}
    if "nextPage" in data:
        res["nextPage"] = data["nextPage"]
    return res


def sigma_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that wraps MCP tools with structured error handling and execution metrics.

    A failed call raises FastMCP ``ToolError`` carrying the redacted ``{"error": ...}`` JSON,
    so the client receives a ``tools/call`` result with ``isError: true``. It is raised
    ``from None`` so the unredacted original exception does not ride along as the cause.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        start_t = time.perf_counter()
        try:
            result: str = await fn(*args, **kwargs)
            duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.info("Tool executed successfully", extra={"tool_name": fn.__name__, "duration_ms": duration_ms})
            return result
        except ToolError:
            # Already a redacted tool execution error (``_invalid_request``); keep its payload.
            raise
        except SigmaAPIError as e:
            duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.error("Tool failed with API error", extra={"tool_name": fn.__name__, "duration_ms": duration_ms})
            err = e.to_dict()
            if isinstance(err.get("detail"), str):
                err["detail"] = redact_secrets(err["detail"])
            raise ToolError(json.dumps({"error": err})) from None
        except Exception as e:
            duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.error(
                "Tool failed with internal error", extra={"tool_name": fn.__name__, "duration_ms": duration_ms}
            )
            msg = redact_secrets(str(e))
            raise ToolError(json.dumps({"error": {"type": "internal", "message": msg}})) from None

    return wrapper


async def resolve_client(ctx: Any | None = None) -> Any:
    """Dynamically resolve shared client from server module to ensure test mock propagation."""
    from sigma_mcp import server

    return await server.get_client(ctx)
