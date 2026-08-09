# Changelog

## 1.0.4 (Unreleased)

### Fixed
- **Description Length Validation**: Shortened the `server.json` description to comply with the MCP Registry's 100-character constraint.

## 1.0.3 (2026-08-09)

### Added
- **MCP Registry Integration**: Created the `server.json` manifest specifying standard environment variables and metadata for OIDC publishing to the official Model Context Protocol Registry.
- **Development Cookbook**: Added a git-ignored developer `COOKBOOK.md` to define strict local type checks, AI self-reviews, and CodeRabbit review wait-states prior to merging.

### Fixed
- **Release CI Workflow**: Checked out the workspace in the PyPI/MCP publish job so `server.json` is present during version bumps, and pinned the `mcp-publisher` tool to release version `v1.8.1` as recommended by CodeRabbit reviews.

## 1.0.2 (2026-08-07)

### Fixed
- **OpenAPI Drift Target**: Pointed the drift monitor target to the canonical unified public REST API spec (`assets.sigmacomputing.com`), bringing visibility to 33 previously hidden endpoints (including the Beta code-representation spec APIs).
- **Layout & Spec Wording**: Softened the workbook layout descriptions in `sigma_api_capabilities` and `README.md` to reference the Beta `/v2/workbooks/spec` code representation spec.
- **Base URL Troubleshooting**: Clarified region-specific connection troubleshooting text in `server.py` to avoid false alerts for AWS US-West users.
- **Profile Counts**: Corrected tool registration count figures in the `README.md` configuration tables.

## 1.0.1 (2026-08-05)

### Fixed
- **PyPI Rendering**: Converted relative documentation links to absolute GitHub URLs to prevent 404 errors on PyPI project pages.

## 1.0.0 (2026-08-02)

### Breaking Changes
- **Bulk-destructive tools are no longer registered by default.** `sigma_bulk_deactivate_members` and `sigma_bulk_remove_team_members` require `SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1` to appear in the tool list. Without it, they do not exist from the model's perspective. This is intentional: an unprompted "clean up inactive users" from a model should not have access to bulk deactivation.
- **Single-Delete Confirmation Gating**: Mandatory `confirm: bool = False` opt-in parameter required on all atomic delete, archive, deactivate, and bulk-remove tools. Callers performing destructive operations must explicitly pass `confirm=True`.

### Added
- **Native MCP Resources**: Registered `sigma://reference/formulas`, `sigma://reference/capabilities`, `sigma://reference/docs-index`, and `sigma://webhooks/recent` native resources.
- **Native MCP Prompts**: Registered `provision_tenant_dashboard`, `audit_organization_permissions`, `prepare_data_model`, `onboard_team_member`, `swap_warehouse_source`, and `audit_tenant_connections` native prompts.
- **Documentation tools**: `sigma_search_docs` (AI-powered semantic search via Sigma's docs MCP) and `sigma_get_doc_page` (fetch any docs page as Markdown).
- **Structured JSON Logging**: Support for `SIGMA_MCP_LOG_FORMAT=json` with structured log records including execution duration (`duration_ms`).
- **Network Transport CLI Flags**: Added `--host` and `--port` CLI options for network transport server deployment.
- **Path Segment Sanitization**: Automated path parameter quoting (`quote(seg, safe="").replace("..", "%2E%2E")`) across all 217 client API methods to prevent path traversal attacks.
- **Secret & Token Redaction**: Multi-pattern regex scrubbing (`Bearer` tokens, `client_secret`, `access_token`, `subject_token`, and raw JWT `eyJ...`) across error responses and log payloads.
- **Multi-Tenant Security**: Added `SIGMA_ALLOWED_TENANTS` allowlist check and `SIGMA_STRICT_TENANT_ALLOWLIST=1` fail-closed enforcement for RFC 8693 token exchange.
- **Single-Delete Confirmation Gating**: Mandatory `confirm: bool = False` opt-in parameter required on all atomic delete, archive, deactivate, and bulk-remove tool calls.
- **Safety gating**: `SIGMA_MCP_READONLY=1` removes all non-read-only tools from registration (83 tools remain). Composes with profiles (e.g. `admin` + `readonly` = read-only subset of admin tools).
- **`embed` profile**: `SIGMA_MCP_PROFILE=embed` (55 tools) for embedded analytics workflows — core + embeds, user attributes, tenants, source swap, workspace grants.
- **Catch-all regex rejection** in `sigma_bulk_deactivate_members`: patterns like `.*`, `.+`, `^.*$`, `.`, or empty string are refused because they would match every member in the org.
- **10-member hard cap** on `sigma_bulk_deactivate_members`: if the pattern matches more than 10 members, the tool refuses and reports the match list. Prevents accidental org-wide deactivation.
- **`sigma_formula_pitfalls` tool**: returns a curated Sigma formula reference to prevent hallucinated function names and type errors. Backed by `src/sigma_mcp/reference/formulas.md`.
- **11 new endpoints**: reports (CRUD, schedules, elements, queries, lineage, sources, duplicate, export), source-swap policies, deployment documents, workbook version history, report schedules.
- **Tool count now 155** (default), 157 with bulk-destructive opt-in, 83 read-only.
- **Expanded test suite** covering profile composition, readonly filtering, bulk-gating, annotation completeness, and catch-all regex rejection.
- **OSS files**: SECURITY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md, LICENSE.
- **`scripts/check_tool_contract.py`**: validates counts, annotations, profiles, and gating. Runs in CI.
- **docs/formulas.md**: full Sigma formula reference for agent consumption.

### Fixed
- Annotation counts corrected: 83 read-only, 16 destructive, 8 idempotent.
- `sigma_promote_workbook` now creates the tag if it doesn't already exist (idempotent).

## 0.2.0 (2026-08-01)

### Breaking Changes
- Client is now fully async (`httpx.AsyncClient`). All methods are `async def`.
- All MCP tools are now `async def`. Requires MCP SDK >= 2.0.0.

### Added
- **Structured error handling**: `SigmaAPIError` class with status, path, method, detail, request_id. `@sigma_tool` decorator wraps all tools — no raw exceptions escape.
- **8 recipe tools**: export+download, ownership transfer, shared workbooks, input table scan, bulk deactivate, change email, bulk team remove, tenant connection sync.
- **Multi-tenant auth**: RFC 8693 token exchange via `SigmaClient.for_tenant()`. Per-tenant token cache with auto-refresh.
- **MCP 2.0 annotations**: All 141 tools annotated with `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`.
- **Tool profiles**: `SIGMA_MCP_PROFILE=core|admin|full` to control tool registration.
- **OpenAPI drift detection**: `scripts/check_openapi_drift.py` compares client paths against official spec.
- **GitHub Actions CI**: Matrix on Python 3.10-3.13 with ruff + pytest.
- **Auto-pagination tools**: `sigma_list_all_*` tools that follow pagination tokens.
- **PyJWT dependency** for tenant token exchange.
- Secret redaction in all error messages.

### Fixed
- `download_query()` now routes through `_request()` for retry/rate-limit handling.
- `time.sleep` replaced with `await asyncio.sleep` throughout.

## 0.1.0 (2025-06-15)

Initial release. 131 tools covering the Sigma v2 API surface.
