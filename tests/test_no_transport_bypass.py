"""Guard against transport bypasses in the client.

Every API call must go through ``SigmaClient._request`` so it inherits 429
retry, Retry-After handling, and the max-delay cap. Twice now a method has
called ``self._http`` directly and silently lost that behaviour:

  * ``download_query`` (fixed earlier), and then
  * ``download_query_raw``, a new variant that reintroduced the same bug on the
    export polling loop — the code most likely to be throttled.

This test pins the small set of legitimate direct uses so a third instance
fails in CI instead of in review.
"""

from __future__ import annotations

import ast
from pathlib import Path

CLIENT_PATH = Path(__file__).parent.parent / "src" / "sigma_mcp" / "client.py"

# Functions permitted to touch the transport directly, with the reason.
#
# The two token calls CANNOT route through _request: _request builds headers via
# _headers(), which calls _get_token(), which would recurse forever. They are
# therefore the one deliberate gap — a 429 on token fetch is not retried.
ALLOWED_DIRECT_TRANSPORT_USE = {
    "aclose": "closes the transport it owns",
    "_request": "is the wrapper itself",
    "for_tenant": "posts to /v2/auth/token; routing via _request would recurse",
    "_get_token": "posts to /v2/auth/token; routing via _request would recurse",
}


def _functions_touching_transport() -> dict[str, list[int]]:
    """Map function name -> line numbers where it uses self._http directly."""
    tree = ast.parse(CLIENT_PATH.read_text())
    found: dict[str, list[int]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            # Match attribute access on self._http, e.g. self._http.post(...)
            if not isinstance(inner, ast.Attribute):
                continue
            base = inner.value
            if (
                isinstance(base, ast.Attribute)
                and base.attr == "_http"
                and isinstance(base.value, ast.Name)
                and base.value.id == "self"
            ):
                found.setdefault(node.name, []).append(inner.lineno)
    return found


def test_no_new_transport_bypasses() -> None:
    """No new client method may call self._http directly."""
    offenders = {
        name: lines
        for name, lines in _functions_touching_transport().items()
        if name not in ALLOWED_DIRECT_TRANSPORT_USE
    }
    assert not offenders, (
        "These functions call self._http directly and so skip retry / "
        f"Retry-After handling: {offenders}. Route them through self._request "
        "instead. If a bypass is genuinely required, add it to "
        "ALLOWED_DIRECT_TRANSPORT_USE with a justification."
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Every allowlisted function must still exist and still use the transport."""
    actual = _functions_touching_transport()
    stale = [name for name in ALLOWED_DIRECT_TRANSPORT_USE if name not in actual]
    assert not stale, (
        f"These allowlist entries no longer use self._http directly: {stale}. "
        "Remove them so the allowlist stays meaningful."
    )


def test_public_verbs_route_through_request() -> None:
    """get/post/put/patch/delete must delegate to _request."""
    tree = ast.parse(CLIENT_PATH.read_text())
    verbs = {"get", "post", "put", "patch", "delete"}
    seen: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name not in verbs:
            continue
        calls_request = any(
            isinstance(inner, ast.Attribute)
            and inner.attr == "_request"
            and isinstance(inner.value, ast.Name)
            and inner.value.id == "self"
            for inner in ast.walk(node)
        )
        assert calls_request, f"{node.name}() must delegate to self._request"
        seen.add(node.name)

    assert seen == verbs, f"missing HTTP verb methods: {sorted(verbs - seen)}"
