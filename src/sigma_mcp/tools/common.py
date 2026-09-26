"""Common utilities, decorators, and annotation constants for domain sub-servers."""

from __future__ import annotations

import functools
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from mcp.types import ToolAnnotations

from sigma_mcp.errors import SigmaAPIError, redact_secrets

logger = logging.getLogger("sigma_mcp")

ANNOTATION_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
ANNOTATION_WRITE_SAFE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
ANNOTATION_DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)
ANNOTATION_IDEMPOTENT = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)


def _invalid_request(message: str) -> str:
    """Return a uniform nested error response for validation failures."""
    return json.dumps({"error": {"type": "invalid_request", "message": message}})


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
    """Decorator that wraps MCP tools with structured error handling and execution metrics."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        start_t = time.perf_counter()
        try:
            result: str = await fn(*args, **kwargs)
            duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.info("Tool executed successfully", extra={"tool_name": fn.__name__, "duration_ms": duration_ms})
            return result
        except SigmaAPIError as e:
            duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.error("Tool failed with API error", extra={"tool_name": fn.__name__, "duration_ms": duration_ms})
            err = e.to_dict()
            if isinstance(err.get("detail"), str):
                err["detail"] = redact_secrets(err["detail"])
            return json.dumps({"error": err})
        except Exception as e:
            duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.error(
                "Tool failed with internal error", extra={"tool_name": fn.__name__, "duration_ms": duration_ms}
            )
            msg = redact_secrets(str(e))
            return json.dumps({"error": {"type": "internal", "message": msg}})

    return wrapper


async def resolve_client(ctx: Any | None = None) -> Any:
    """Dynamically resolve shared client from server module to ensure test mock propagation."""
    from sigma_mcp import server

    return await server.get_client(ctx)
