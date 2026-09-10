# AGENTS.md

Instructions for AI coding agents (Antigravity, Claude Code, Copilot, Cursor, Windsurf) working on this repository or integrating Sigma Computing cloud analytics capabilities.

---

## 🎯 Project Overview & Scaffolding Purpose

This is `mcp-server-sigma` — an enterprise Python Model Context Protocol (MCP) server exposing 155 tools by default (157 with bulk-destructive operations enabled) covering the entire REST API surface (v2 and v3alpha) for **Sigma Computing**.

**Primary Purpose**:
Expose deep cloud business intelligence, embedded analytics, workbook lineage, SQL data modeling, user/team administration, and tenant token exchange to AI agents with strict enterprise safety gates, offline testing, and multi-tenant token isolation.

---

## 🏗️ Architecture Blueprint

```
mcp-server-sigma/
├── src/sigma_mcp/
│   ├── __init__.py               # Package version (__version__) and public exports
│   ├── server.py                 # FastMCP server instance, 155+ @mcp.tool() handlers, prompts, resources
│   ├── client.py                 # Async HTTP client (httpx.AsyncClient, retries, jitter, RFC 8693 token exchange)
│   ├── errors.py                 # Structured API exceptions and automatic secret redaction
│   └── webhooks.py               # Webhook HMAC signature verification and event buffer
├── scripts/
│   ├── check_tool_contract.py    # AST/reflection contract testing total tool & annotation counts
│   ├── check_openapi_drift.py    # AST visitor checking client methods against upstream OpenAPI specs
│   ├── smoke_test.py             # Stdio JSON-RPC protocol handshake verification
│   └── write_ops_check.py        # Audit verifying all write operations have confirm parameter
├── tests/
│   ├── test_client.py            # Unit tests for HTTP client, retries, headers, and error handling
│   ├── test_server_*.py          # Tests for tool execution, parameter validation, and confirmation gating
│   ├── test_security_hardening.py# Tenant allowlist, token exchange, and credential sanitization tests
│   ├── test_webhooks.py          # HMAC signature and replay protection tests
│   └── smoke_test.py             # Live smoke test suite against live Sigma credentials (main only)
├── .github/workflows/
│   ├── ci.yml                    # Multi-job matrix: lint, py3.10-3.13 tests, contracts, CodeQL, docker build
│   ├── release.yml               # Automated release on v* tags: wheels, sdist, CycloneDX SBOM, GHCR docker
│   └── drift-monitor.yml         # Scheduled upstream schema drift check
├── Dockerfile                    # Multi-stage container build running as non-root USER mcp
├── server.json                   # MCP Registry catalog metadata (runtimeHint: uvx, stdio transport)
├── pyproject.toml                # Packaging metadata, entrypoint CLI, dependency pinning
├── COOKBOOK.md                   # Operational maintainer runbook (9-step release SOP, recipes)
├── AGENTS.md                     # Agent guidance map, gotchas, and conventions (this file)
└── README.md                     # User-facing installation, quickstart, and tool index
```

---

## ⚡ The Canonical Workflow: Building Tools from API / llm.txt

When translating an API documentation page or OpenAPI specification into an MCP tool, follow these 4 steps in exact order:

### 1. Client Method (`client.py`)
- Implement a dedicated `async def` method on `SigmaClient`.
- Type all arguments strictly. Never use bare `dict` or `Any` when a concrete schema or literal is known.
- URL path parameters **must** be safely quoted using `_encode_segment()` preserving colons on custom methods (e.g. `{id}:materialize`) while eliminating path traversal vulnerabilities (`..` $\rightarrow$ `%2E%2E`).
- Call `await self._request("METHOD", path, params=..., json=...)`.

### 2. Tool Handler (`server.py`)
- Register the tool with `@mcp.tool()` and wrap with the server decorator (`@sigma_tool`).
- Provide an explicit, agent-friendly docstring describing capabilities, parameters, and return shape.
- Destructive operations (`POST`, `PUT`, `PATCH`, `DELETE` mutating state) **must** accept `confirm: bool = False`.

### 3. Tool Annotations & Gating
- Apply MCP `ToolAnnotations` post-registration via `mcp._tool_manager._tools`:
  - `readOnlyHint`: `True` for inspection/GET; `False` for mutations.
  - `destructiveHint`: `True` for delete/archive/deactivate actions; `False` otherwise.
  - `idempotentHint`: `True` for GET, PUT, idempotent operations; `False` for creations.
  - `openWorldHint`: `True` when interacting with external networks/APIs.
- Gating:
  - Support `READONLY` mode (`SIGMA_MCP_READONLY=1` or `--readonly`) to filter out mutating tools.
  - Support bulk protection (`SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1`) for mass-destructive tools (`sigma_bulk_deactivate_users`, `sigma_bulk_remove_team_members`).
  - Support profile filtering (`SIGMA_PROFILE`: `core`, `admin`, `embed`, `full`).

### 4. Pure Offline Testing & Contract Sync (`tests/`)
- Add unit tests in `tests/` mocking responses via `unittest.mock.AsyncMock`.
- **Zero live network calls during tests.** Tests must run 100% offline in CI.
- Update expected tool count in `scripts/check_tool_contract.py` and `README.md`.
- Ensure test statement and branch coverage remains at **100.0%**.

---

## 🛡️ Non-Negotiable Safety & Security Rules

1. **Destructive Confirmation Gate**:
   - Every mutating tool must accept `confirm: bool = False`. If `False`, return a dry-run / confirmation preview without executing the side-effect.
2. **Secret Redaction**:
   - Error messages, logs, and tracebacks must pass through regex redaction (`_redact_secrets`) stripping Bearer tokens, passwords, client secrets, access tokens, subject tokens, raw JWTs, and `ghs_` tokens.
3. **Multi-Tenant RFC 8693 Token Exchange**:
   - Strictly validate tenant delegation via `SIGMA_ALLOWED_TENANTS`. If `SIGMA_STRICT_TENANT_ALLOWLIST=1` is set, fail-closed with HTTP 403 on unrecognized tenants.
   - Delegation JWTs must include standard claims: `ver: "1.1"`, `aud: "sigmacomputing"`, `iat`, `exp` (+300s), and `jti` (UUID).
4. **Path Traversal Protection**:
   - All dynamic URL path segments must pass through `_encode_segment()`, preventing traversal attacks.
5. **Bulk Destructive Caps**:
   - Mass operations require `SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1`, default to `dry_run=True`, and enforce hard batch limits (10-member cap on user deactivation, 50-member cap on team member removal).
6. **Webhook Signature Validation**:
   - Webhook payloads must be verified using `hmac.compare_digest` to prevent timing attacks.
7. **Registry Metadata Constraint**:
   - In `server.json`, the root `description` must be **strictly $\le$ 100 characters** to pass MCP Registry schema validation (longer strings trigger HTTP 422).
8. **Git Safety**:
   - Never commit API secrets or tenant credentials. All changes proceed via feature branches and PRs.

---

## 🛠️ Development & Verification Commands

```bash
# Install editable with dev dependencies
uv sync --extra dev   # or pip install -e ".[dev]"

# Lint and formatting
uv run ruff check . && uv run ruff format --check .

# Strict type checking
uv run mypy --strict src/

# Test suite with 100% coverage requirement
uv run pytest --cov=src/sigma_mcp --cov-fail-under=100 -v

# Tool contract verification
uv run python scripts/check_tool_contract.py

# Upstream OpenAPI / route drift check
uv run python scripts/check_openapi_drift.py

# Stdio JSON-RPC protocol smoke test
uv run python scripts/smoke_test.py

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent
```

---

## 🔄 CI/CD Matrix & Operational Release SOP

The GitHub Actions CI matrix enforces:
- Ruff lint & format checks.
- Mypy `--strict` type checks.
- Python 3.10, 3.11, 3.12, 3.13 test matrix with 100% coverage.
- Tool contract & OpenAPI drift validation.
- Multi-stage Docker image build.
- CodeQL security scan.

For cutting releases, creating version bumps, and handling PyPI / GitHub tag workflows, refer to the step-by-step runbook in **`COOKBOOK.md`**.
