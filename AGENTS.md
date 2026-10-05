# AGENTS.md

Instructions for AI coding agents (Antigravity, Claude Code, Copilot, Cursor, Windsurf) working on this repository or integrating Sigma Computing cloud analytics capabilities.

---

## 🎯 Project Overview

This is `mcp-server-sigma` — an enterprise Python Model Context Protocol (MCP) server covering the entire REST API surface (v2 and v3alpha) for **Sigma Computing**. Bulk-destructive tools register only when enabled; the expected default and bulk tool counts live in `scripts/check_tool_contract.py`.

**Primary Purpose**:
Expose deep cloud business intelligence, embedded analytics, workbook lineage, SQL data modeling, user/team administration, and tenant token exchange to AI agents with strict enterprise safety gates, offline testing, and multi-tenant token isolation.

---

## 🏗️ Key Paths

- `src/sigma_mcp/server.py` — root gateway: `create_server` mounts the domain sub-servers (`workbooks`, `datasets`, `elements`, `workspace`, `admin`) with matching namespaces; profiles, prompts, resources.
- `src/sigma_mcp/tools/<domain>.py` — domain sub-servers (`admin`, `datasets`, `elements`, `workbooks`, `workspace`); `tools/common.py` holds the `ANNOTATION_*` constants and shared helpers.
- `src/sigma_mcp/client.py` — async HTTP client (`SigmaClient`, `_encode_segment()`, RFC 8693 token exchange). `errors.py` — exceptions and secret redaction. `webhooks.py` — HMAC verification. `middleware.py` — read-only and bulk-destructive gates. `config.py` — settings.
- `scripts/check_tool_contract.py` — source of truth for expected tool counts and annotations. Do not hard-code tool counts elsewhere.
- `scripts/check_openapi_drift.py`, `scripts/write_ops_check.py` (every write op has `confirm`), `scripts/check_conformance.sh` + `conformance-baseline.yml`, `scripts/determine_bump.py`.
- `tests/` — offline unit, security-hardening, webhook, and protocol tests.
- `.github/workflows/` — `ci.yml`, `release.yml`, `sigma-drift-monitor.yml`, `dependabot-automerge.yml`.
- `server.json` (MCP Registry metadata), `Dockerfile`, `pyproject.toml`.

---

## ⚡ The Canonical Workflow: Building Tools from API / llm.txt

When translating an API documentation page or OpenAPI specification into an MCP tool, follow these 4 steps in exact order:

### 1. Client Method (`client.py`)
- Implement a dedicated `async def` method on `SigmaClient`.
- Type all arguments strictly. Never use bare `dict` or `Any` when a concrete schema or literal is known.
- URL path parameters **must** be safely quoted using `_encode_segment()` preserving colons on custom methods (e.g. `{id}:materialize`) while eliminating path traversal vulnerabilities (`..` $\rightarrow$ `%2E%2E`).
- Call `await self._request("METHOD", path, params=..., json=...)`.

### 2. Tool Handler (`tools/<domain>.py`)
- Register the tool on its domain sub-server with `@<domain>_server.tool(name=..., annotations=...)` and wrap with `@sigma_tool` (e.g. `tools/workbooks.py`). The root gateway in `server.py` mounts the domain with its namespace.
- Provide an explicit, agent-friendly docstring describing capabilities, parameters, and return shape.
- Destructive operations (`POST`, `PUT`, `PATCH`, `DELETE` mutating state) **must** accept `confirm: bool = False`.

### 3. Tool Annotations & Gating
- Pass MCP `ToolAnnotations` at registration with the `ANNOTATION_*` constants from `tools/common.py`:
  - `readOnlyHint`: `True` for inspection/GET; `False` for mutations.
  - `destructiveHint`: `True` for delete/archive/deactivate actions; `False` otherwise.
  - `idempotentHint`: `True` for GET, PUT, idempotent operations; `False` for creations.
  - `openWorldHint`: `True` when interacting with external networks/APIs.
- Gating:
  - Support `READONLY` mode (`SIGMA_MCP_READONLY=1` or profile `readonly`) to filter out mutating tools.
  - Support bulk protection (`SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1`) for mass-destructive tools (`admin_bulk_deactivate_members`, `admin_bulk_remove_team_members`).
  - Support profile filtering (`SIGMA_MCP_PROFILE` or `--profile`: `core`, `admin`, `embed`, `full`, `readonly`).

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
uv sync --locked --extra dev

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

# Protocol integration tests (stdio handshake & stateless streamable HTTP)
uv run pytest tests/test_protocol.py

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

---

## 🔄 CI & Releases

CI is defined in `.github/workflows/ci.yml` (jobs: lint and types, tests on Python 3.10–3.13 at 100% coverage, tool contract and env gating, OpenAPI drift and conformance, build + `twine check`, Docker build, CodeQL). Run the commands above before opening a PR. Scheduled upstream drift runs in `sigma-drift-monitor.yml`.

Do not create tags or releases unless the maintainer asks.
