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


def _tool_failure(error_type: str, message: str, **details: Any) -> NoReturn:
    """Raise a failed tool call as a tool execution error (``isError: true``).

    Raises FastMCP ``ToolError`` with the redacted ``{"error": {"type": ..., "message": ...}}``
    JSON, ``from None`` so no original exception rides along as the cause. Every string in the
    details, including strings nested in lists and dicts (per-item batch errors), is redacted too.
    ``sigma_tool`` re-raises it unchanged. Callers pass only fields they built themselves, never a
    raw upstream body.
    """
    payload: dict[str, Any] = {"type": error_type, "message": redact_secrets(message)}
    for key, value in details.items():
        payload[key] = _redact_value(value)
    raise ToolError(json.dumps({"error": payload})) from None


def _redact_value(value: Any) -> Any:
    """Redact secrets in every string of a JSON-like value."""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    return value


def _invalid_request(message: str, **details: Any) -> NoReturn:
    """Raise an input validation failure as a tool execution error.

    The MCP spec lists input validation errors among tool execution errors, reported in the
    tool result with ``isError: true`` so the model can correct the call. This raises FastMCP
    ``ToolError`` with the redacted ``{"error": {"type": "invalid_request", ...}}`` JSON, the same
    path ``sigma_tool`` uses for API and internal failures, which re-raises it unchanged.
    """
    _tool_failure("invalid_request", message, **details)


def _item_error(exc: BaseException | str, error_type: str = "internal", *, index: int) -> dict[str, Any]:
    """Build the redacted ``error`` object for one failed item in a batch result.

    Every per-item error carries a non-empty ``message``. A ``SigmaAPIError`` keeps its
    structured fields (``type``, ``status_code``, ``method``, ``path``, ``detail``,
    ``request_id``); any other exception, or a plain reason string, gets ``type`` and ``message``.
    Callers catch ``Exception`` (never ``BaseException``) per item, so one failure is recorded
    and the batch continues. ``index`` is the item's zero-based position in the list the caller
    is iterating; it is used only in the log line.
    """
    if isinstance(exc, SigmaAPIError):
        item: dict[str, Any] = exc.to_dict()
        item["message"] = str(exc)
    else:
        message = exc if isinstance(exc, str) else (str(exc) or type(exc).__name__)
        item = {"type": error_type, "message": message}
    redacted: dict[str, Any] = _redact_value(item)
    # Log only the item index and the error type (the exception class, or ``error_type`` for a
    # reason string). No message and no traceback reach the logs; the redacted message stays in
    # the returned error.
    logger.warning("Batch item %d failed: %s", index, error_type if isinstance(exc, str) else type(exc).__name__)
    return redacted


def _batch_outcome(
    items: str,
    verb: str,
    results: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Return a batch result in the fleet shape, or raise ``batch_failed`` if every item failed.

    Each entry is ``{"id", "status", "result"}`` or ``{"id", "status": "failed", "error"}``, where
    ``error`` comes from ``_item_error``. The result always has ``status`` (``success`` or
    ``partial_success``), ``<verb>_count``, ``failed_count``, ``results`` and ``errors``;
    ``context`` is added to both the result and the ``batch_failed`` error, ``extra`` only to the
    result. The raise happens outside any ``except`` block, so it carries no ``__context__``.
    """
    ctx = context or {}
    # Failed items may also sit in ``results`` (the field layout from main); only entries whose
    # status is ``verb`` count as done.
    done = sum(1 for r in results if r.get("status") == verb)
    if errors and not done:
        _tool_failure(
            "batch_failed",
            f"All {len(errors)} {items} failed; nothing was {verb}.",
            **ctx,
            failed_count=len(errors),
            errors=errors,
        )
    return json.dumps(
        {
            "status": "success" if not errors else "partial_success",
            f"{verb}_count": done,
            "failed_count": len(errors),
            "results": results,
            "errors": errors,
            **ctx,
            **(extra or {}),
        },
        indent=2,
    )


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
    ``from None`` after the ``except`` block ends, so the unredacted original exception is on
    neither ``__cause__`` nor ``__context__``.
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
            failure = json.dumps({"error": err})
        except Exception as e:
            duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.error(
                "Tool failed with internal error", extra={"tool_name": fn.__name__, "duration_ms": duration_ms}
            )
            msg = redact_secrets(str(e))
            failure = json.dumps({"error": {"type": "internal", "message": msg}})
        # Raised after the except blocks end, so the unredacted original exception is on neither
        # __cause__ nor __context__.
        raise ToolError(failure) from None

    return wrapper


async def resolve_client(ctx: Any | None = None) -> Any:
    """Dynamically resolve shared client from server module to ensure test mock propagation."""
    from sigma_mcp import server

    return await server.get_client(ctx)
