"""Every public SigmaClient method that issues a request is reached from a tool.

Pagination and request helpers are the only names allowed to have no tool caller.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "src" / "sigma_mcp" / "client.py"
TOOLS = ROOT / "src" / "sigma_mcp" / "tools"

# A call to one of these on ``self`` is the method issuing an HTTP request.
_REQUEST_ATTRS = frozenset(
    {
        "_request",
        "_page_through",
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "auto_paginate",
        "auto_paginate_token",
        "paged_list",
        "paged_list_token",
    }
)

# Helpers only. A domain method that issues a request has to be called from a tool module.
ALLOWED_HELPERS = {
    "get": "request helper",
    "post": "request helper",
    "put": "request helper",
    "patch": "request helper",
    "delete": "request helper",
    "auto_paginate": "pagination helper",
    "auto_paginate_token": "pagination helper",
    "paged_list": "pagination helper",
    "paged_list_token": "pagination helper",
}


def _issues_request(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        value = node.func.value
        if isinstance(value, ast.Name) and value.id == "self" and node.func.attr in _REQUEST_ATTRS:
            return True
    return False


def _public_request_methods(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "SigmaClient":
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and not item.name.startswith("_"):
                if _issues_request(item):
                    names.add(item.name)
    return names


def _called_from_tools(name: str) -> bool:
    for path in TOOLS.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == name:
                return True
    return False


def test_request_methods_are_called_from_tools() -> None:
    tree = ast.parse(CLIENT.read_text(encoding="utf-8"))
    missing = sorted(
        name for name in _public_request_methods(tree) if name not in ALLOWED_HELPERS and not _called_from_tools(name)
    )
    assert missing == []
    assert set(ALLOWED_HELPERS) == {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "auto_paginate",
        "auto_paginate_token",
        "paged_list",
        "paged_list_token",
    }
    assert all(ALLOWED_HELPERS.values())
