# AGENTS.md

Instructions for AI coding agents (Claude Code, Copilot, Cursor, Windsurf, etc.) working on this repository.

## Project Overview

This is `mcp-server-sigma` — a Python MCP server exposing 155 tools for Sigma Computing's REST API. It runs over stdio and is consumed by AI clients (Claude Desktop, VS Code, etc.).

## Architecture

```
src/sigma_mcp/
├── server.py      # MCP tool definitions (2600+ lines, all @mcp.tool() handlers)
├── client.py      # Async HTTP client (OAuth2, retries, rate-limiting, tenant token exchange)
├── errors.py      # Error formatting with secret redaction
├── webhooks.py    # Webhook signature verification + in-memory event buffer
└── __init__.py    # Version only
```

## Key Patterns

- **Every tool** is an `async def` decorated with `@mcp.tool()` and `@sigma_tool`
- `@sigma_tool` is a decorator that adds timing, structured logging, and error handling
- **Destructive tools** require `confirm: bool = False` — reject if not `True`
- **Composite tools** (promote, deploy, onboard) orchestrate multiple API calls
- **Annotations** are applied post-registration via `mcp._tool_manager._tools` (private API, pinned SDK version)

## Development Commands

```bash
# Install
pip install -e ".[dev]"

# Lint + format
ruff check . && ruff format --check .

# Type check
mypy --strict src/

# Tests (100% coverage required)
pytest --cov=src/sigma_mcp --cov-fail-under=100 -q

# Tool contract validation
python scripts/check_tool_contract.py

# OpenAPI drift check
python scripts/check_openapi_drift.py
```

## Testing Conventions

- Tests use `unittest.mock.AsyncMock` to mock `SigmaClient` methods
- Monkeypatch `srv._client` to inject a mock client
- No `conftest.py` — each test file is self-contained
- Coverage must stay at 100% — CI enforces this

## Adding a New Tool

1. Add the client method in `client.py` (async, typed)
2. Add the `@mcp.tool()` handler in `server.py` with proper docstring
3. Add the tool name to the appropriate annotation set (`_DESTRUCTIVE_NAMES`, `_IDEMPOTENT_NAMES`, or let it default to `_WRITE_SAFE`)
4. Update `scripts/check_tool_contract.py` expected counts
5. Update `README.md` tool count
6. Add test coverage (must maintain 100%)

## Safety Rules

- Never remove `confirm=True` gates from destructive tools
- Never expose credentials in error messages (secret redaction is automatic)
- Bulk-destructive tools are gated behind `SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1`
- Read-only mode (`SIGMA_MCP_READONLY=1`) filters out all write tools at registration time

## CI Pipeline

The CI runs 7 jobs: lint, test (4 Python versions), contract validation, OpenAPI drift, build+twine, CodeQL, and live smoke (main-only with secrets). All must pass for merge.
