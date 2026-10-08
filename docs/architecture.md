# Architecture

## Client/Server Layering

```
┌─────────────────────────────────────────────┐
│  MCP Host (Claude, Cursor, etc.)            │
└─────────────┬───────────────────────────────┘
              │ MCP Protocol (stdio/http)
┌─────────────▼───────────────────────────────┐
│  server.py — MCP tool registration layer    │
│  - @mcp.tool() decorators                   │
│  - @sigma_tool error handling decorator     │
│  - JSON serialization                       │
└─────────────┬───────────────────────────────┘
              │ async method calls
┌─────────────▼───────────────────────────────┐
│  client.py — SigmaClient (async httpx)      │
│  - OAuth token lifecycle                    │
│  - Exponential backoff on 429               │
│  - Multi-tenant token exchange (RFC 8693)   │
│  - Pagination helpers                       │
└─────────────┬───────────────────────────────┘
              │ HTTPS
┌─────────────▼───────────────────────────────┐
│  Sigma Computing REST API v2                │
│  (region-specific base URL)                 │
└─────────────────────────────────────────────┘
```

## Why Async

The client uses `httpx.AsyncClient` because:

1. MCP servers run an async event loop
2. Token refresh, pagination, and polling are I/O-bound
3. Enables concurrent operations in examples (e.g., multi-tenant sync)

## Retry and Backoff Design

On receiving a `429 Too Many Requests`:

1. Read `Retry-After` header if present
2. Otherwise compute delay: `base_delay * 2**attempt` (default: 1s, 2s, 4s)
3. Sleep and retry up to `max_retries` (default 3)
4. On exhaustion, raise `SigmaAPIError(429)` with context

All other 4xx/5xx errors are raised immediately (no retry).

## Pagination Models

### Cursor-based (nextPageToken)

Used by: `/v2/tenants`

```python
results = []
token = None
while True:
    params = {"nextPageToken": token} if token else {}
    page = await client.get("/v2/tenants", params)
    results.extend(page.get("entries", []))
    token = page.get("nextPageToken")
    if not token:
        break
```

### Offset-based (limit/offset)

Used by: `/v2/workbooks`, `/v2/members`, `/v2/teams`, `/v2/connections`

```python
results = []
offset = 0
limit = 200
while True:
    page = await client.get(path, {"limit": limit, "offset": offset})
    results.extend(page.get("entries", []))
    if not page.get("hasMore", False):
        break
    offset += limit
```

## Profiles and Build Order

`create_server()` builds a fresh root gateway per call and never mutates the shared
domain sub-servers. Build order:

1. **Domain mounts** — `workbooks`, `datasets`, `elements`, `workspace`, `admin`, each
   mounted with its namespace (wire names are `<domain>_<tool>`).
2. **Job allowlist** — job profiles hide every tool, then re-enable their allowlist
   (`root.disable(components={"tool"})` then `root.enable(names=..., components={"tool"})`).
   Prompts and resources are untouched. An allowlisted name missing from the catalog
   raises `ValueError` at build.
3. **Read-only filter** — under `profile=readonly` or `SIGMA_MCP_READONLY=1`, only tools
   annotated `readOnlyHint=True` stay listed. A missing hint counts as a write.
4. **Discovery** — Tool Search (`search_tools` + `call_tool`, regex or BM25) or
   experimental Code Mode, on `full` only and never both. Other profiles log a warning
   and stay flat.

| Profile | Job | Tools | Read-only |
|---------|-----|------:|----------:|
| `full` (default) | Every tool, including the 9 in `FULL_ONLY_TOOLS` | 172 | 90 |
| `readonly` | Every `readOnlyHint=True` tool | 90 | 90 |
| `analyst` | Consume workbooks and reports | 31 | 23 |
| `author` | Build workbooks and reports | 40 | 28 |
| `modeler` | Build data models and manage connections | 33 | 24 |
| `embed` | Embedded analytics and multi-tenant | 50 | 27 |
| `access_admin` | Users, teams, grants, and access | 52 | 22 |

The authoritative lists live in `src/sigma_mcp/profiles.py`; the expected counts live
in `scripts/check_tool_contract.py`.

Call-time gates (root and admin middleware):

- **`ReadOnlyGateMiddleware`** — in read-only mode refuses any real tool not annotated
  `readOnlyHint=True`, looking the tool up with the public `get_tool`. It unwraps the
  Tool Search `call_tool` proxy and judges the proxied tool, so reads through search work
  and writes through search are refused. Unknown names pass through to FastMCP's
  `Unknown tool` error. With no server context the call is refused (fail closed).
- **`AdminDomainGuardMiddleware`** — `admin_bulk_deactivate_members` and
  `admin_bulk_remove_team_members` are listed in `full` and refused at call time unless
  `SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1`.

Refusals raise `SafetyViolationError`, a FastMCP `ToolError`, so clients receive a
`tools/call` result with `isError: true`.

All tools are registered via `@mcp.tool()` decorators. The `@sigma_tool`
decorator provides:

- Automatic `SigmaAPIError` catching → structured JSON error response
- Consistent return type (always `str` — JSON-serialized)

## OpenAPI Drift-Check Safety Net

The `scripts/` directory contains tooling to compare the registered MCP tools
against the official Sigma OpenAPI specification. This catches:

- New API endpoints not yet exposed as tools
- Removed endpoints still registered
- Parameter signature mismatches
