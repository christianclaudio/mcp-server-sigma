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

## Profile-Based Tool Registration

Four profiles control which tools are registered:

| Profile | Env value | Tools |
|---------|-----------|-------|
| `core` | `SIGMA_MCP_PROFILE=core` | 36 — connections, workbooks, data models |
| `admin` | `SIGMA_MCP_PROFILE=admin` | 52 — core + members, teams, deployments |
| `embed` | `SIGMA_MCP_PROFILE=embed` | 55 — core + embeds, multi-tenant |
| `full` | `SIGMA_MCP_PROFILE=full` (default) | 152 — all tools |

Additional env-var filters compose on top of the profile:

- **`SIGMA_MCP_READONLY=1`** — removes every tool whose MCP annotation does
  not set `readOnlyHint=true`. Results in 80 read-only tools (at `full`).
- **`SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1`** — registers the bulk-destructive
  tools (`sigma_bulk_deactivate_members`, `sigma_bulk_remove_team_members`).
  Without this, they are not present. Adds 2 tools (154 total at `full`).

**Filter application order:** profile → readonly → bulk-destructive gating.

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
