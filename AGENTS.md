# AGENTS.md

Instructions for AI coding agents working on `mcp-server-sigma` or integrating Sigma Computing cloud analytics capabilities.

---

## 🎯 Project Overview

`mcp-server-sigma` is an enterprise Python MCP server for **Sigma Computing**, built on its public REST API (`v2` and `v3alpha`). `scripts/check_openapi_drift.py` tracks uncovered spec endpoints. The scheduled drift check passes `--fail-on-new-uncovered` and fails when a spec operation is in neither the client nor `scripts/drift_uncovered_baseline.txt`. A run that falls back to the split help specs skips that gate and the wrong-path check and exits 2. Bulk-destructive tools are listed in `full` and refused at call time unless enabled; expected per-profile counts live in `scripts/check_tool_contract.py`.

---

## 🏗️ Key Paths

- `src/sigma_mcp/server.py` — root gateway (`create_server`): mounts domain sub-servers (`workbooks`, `datasets`, `elements`, `workspace`, `admin`) with matching namespaces, then applies job allowlist, read-only filter, and (`full` only) Tool Search or Code Mode; prompts, resources.
- `src/sigma_mcp/profiles.py` — `PROFILES` (`full`, `readonly`, `analyst`, `author`, `modeler`, `embed`, `access_admin`), allowlists, `FULL_ONLY_TOOLS`, `BULK_DESTRUCTIVE_TOOLS`, `readOnlyHint` filter.
- `src/sigma_mcp/tools/<domain>.py` — domain sub-servers (`admin`, `datasets`, `elements`, `workbooks`, `workspace`); `tools/common.py` holds `ANNOTATION_*` constants and shared helpers.
- `src/sigma_mcp/client.py` — `SigmaClient`, `_encode_segment()`, RFC 8693 token exchange, and `SSRFSafeAsyncTransport` (HTTPS, blocks private/loopback IPs, off-loop DNS, then connects only to a checked address, trying the next while the request's connect timeout lasts (one deadline shared by all addresses), and keeps the hostname for the Host header, TLS SNI, certificate verification, and per-hostname pooling; httpx ignores env proxies when a transport is passed, so the client and drift fetch always connect directly through the pin; `trust_env=False` on the drift fetch).
- `src/sigma_mcp/{auth,errors,webhooks,middleware,config}.py` — `SharedTokenVerifier` for `SIGMA_MCP_AUTH_TOKEN` (enforced across `main()`, `fastmcp run`, `http_app()`; exits 2 on tokenless non-localhost HTTP bind unless `SIGMA_MCP_ALLOW_UNAUTHENTICATED_BIND=1`), redaction/exceptions, HMAC webhooks, read-only & bulk gates, settings.
- `scripts/check_tool_contract.py` — source of truth for expected counts and annotations. Update together with `README.md` and the 4 test files that repeat counts (`SIGNED_OFF` in `tests/test_profiles.py`, the full-profile count in `tests/test_unit.py`, `tests/test_enterprise_assertion.py`, `tests/test_layered.py`), plus `EXPECTED_FULL_ONLY`.
- `scripts/check_openapi_drift.py` (wrong-path drift is always an error; `--fail-on-new-uncovered` also fails on spec operations missing from `scripts/drift_uncovered_baseline.txt`; a fallback spec skips both and exits 2), `scripts/write_ops_check.py` (live create/teardown; needs `SIGMA_CLIENT_ID`, `SIGMA_CLIENT_SECRET`, `SIGMA_API_BASE_URL`), `scripts/check_conformance.sh` + `conformance-baseline.yml`, `scripts/release_notes.py`, `scripts/check_version.py`.
- `tests/` — offline suite (`addopts` uses `-m 'not live'`). Live modules (`test_live.py`, `test_integration_live.py`, `-m live`) require `SIGMA_LIVE_TESTS=1` + `SIGMA_CLIENT_ID`, `SIGMA_CLIENT_SECRET`, `SIGMA_BASE_URL` (unmarked `test_dispatch_tool_call_offline` stays in default run). Must keep `tests/test_client_surface.py` passing across every profile (add fixture URIs/args to its tables, never skip).
- `.github/workflows/` — `ci.yml`, `release.yml`, `dependabot-automerge.yml`.
- `server.json`, `Dockerfile`, `pyproject.toml`.

---

## ⚡ Canonical Workflow: Building Tools from API / `llms.txt`

Follow in exact order:
1. **Client method (`client.py`):** typed `async def` on `SigmaClient` (no bare `dict`/`Any` when a schema is known). Pass raw IDs into `_request()` (`_encode_segment()` preserves colons like `{id}:materialize` and blocks `..` traversal — never pre-quote). Call `await self._request("METHOD", path, params=..., json_data=...)`; for `multipart/form-data`, pass `files={"field": (filename, content, content_type)}` instead of `json_data` (e.g., `add_dbt_metadata`).
2. **Tool handler (`tools/<domain>.py`):** register with `@<domain>_server.tool(name=..., annotations=...)`, wrap with `@sigma_tool`, write explicit docstrings (capabilities, params, return shape). Delete, archive, deactivate, and bulk tools require `confirm: bool = False` (non-destructive creates/updates like `workbooks_create_workbook` do not).
3. **Annotations & gating:**
   - Pass `ANNOTATION_*` constants from `tools/common.py` (`readOnlyHint` explicit on every tool — missing treats as write; `destructiveHint` for delete/archive/deactivate; `idempotentHint` `True` only on `ANNOTATION_IDEMPOTENT`; `READ_ONLY_ANNOTATIONS` for discovery tools).
   - **Read-only (`SIGMA_MCP_READONLY=1` / `--profile readonly`):** keeps only `readOnlyHint=True` tools; refuses all others (directly or via `call_tool`) with `SafetyViolationError` (`ToolError`, `isError: true`).
   - **Bulk protection:** `admin_bulk_deactivate_members` and `admin_bulk_remove_team_members` require `SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1`.
   - **Profiles (`SIGMA_MCP_PROFILE` / `--profile`):** map every tool in `profiles.py` or `FULL_ONLY_TOOLS`. Tool Search and Code Mode attach on `full` only and are mutually exclusive (Code Mode needs `fastmcp[code-mode]` / `pydantic_monty`; `tests/conftest.py` aborts CI if missing; tests use `pytest.importorskip`).
4. **Offline tests & contract sync (`tests/`):** mock HTTP with `AsyncMock` (**zero live calls in default suite**), keep `tests/test_client_surface.py` green, sync `scripts/check_tool_contract.py` + `README.md` + count tests, and maintain **100.0%** statement coverage (`--cov-fail-under=100`).

---

## 🛡️ Non-Negotiable Safety & Security Rules

1. **Destructive Confirmation Gate & Errors:** destructive tools default `confirm: bool = False` and return `_confirm_required(...)` (`isError: false`) without side effects. Validation failures call `_invalid_request(...)` and detected/batch-total failures call `_tool_failure(...)` (`ToolError`, `isError: true`); never return `{"error": ...}` as a normal result or leak raw upstream bodies. Build batch outcomes via `_batch_outcome(items, verb, results, errors, context=...)` (catch `Exception` not `BaseException`, use `_item_error(exc, index=i)`, keep additive fields, no `logger.exception`/`exc_info`), and raise every redacted `ToolError` `from None` **after** `except` blocks end.
2. **Secret Redaction:** `_redact_secrets` / `redact_message` in `errors.py` mask Bearer tokens, client/access/subject tokens, raw JWTs, `ghs_` tokens, passwords, AWS `secretAccessKey`, connection `privateKeyPassphrase`/`pythonSecrets`, and PEM blocks while leaving `tokenizer`, `page_token=`, `max_tokens=` intact (see `errors.py` & redaction tests for known multiline/bracket limits).
3. **Multi-Tenant RFC 8693 Token Exchange:** validate tenants against `SIGMA_ALLOWED_TENANTS`; when `SIGMA_STRICT_TENANT_ALLOWLIST=1`, fail closed with HTTP 403 on unrecognized tenants. Delegation JWTs must include `ver: "1.1"`, `aud: "sigmacomputing"`, `iat`, `exp` (`+300s`), and `jti` (UUID).
4. **Path Traversal & Webhook Security:** dynamic path segments must pass through `_encode_segment()`; verify webhooks with `hmac.compare_digest`.
5. **Bulk Destructive Caps:** require `SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1`. `admin_bulk_deactivate_members` defaults `dry_run=True` (max 10 members); `admin_bulk_remove_team_members` has no `dry_run`, requires `confirm=True` (max 50 emails).
6. **Multi-Stage Non-Root Containers (`Dockerfile`):**
   - Multi-stage `python:3.12-slim-trixie AS builder` → runtime pinned by digest (both stages share the same pin; glibc is keyed by the Debian release in the tag, not the digest), non-root `USER mcp`.
   - Trust-store floor (FastMCP Oct 2026 notice): both stages upgrade OS `ca-certificates` and fail below `ARG CA_CERTIFICATES_FLOOR` (`20250419+deb13u1`, before any pip runs in builder), builder bootstraps pip from `docker/pip-bootstrap.txt` (`--require-hashes`; Dependabot bumps it), `release.yml` installs `pip>=26.2.2`, and `certifi>=2026.10.23` is a direct dependency. Never lower `CA_CERTIFICATES_FLOOR` via `--build-arg` (the build-arg exists only so CI can raise it for the negative floor check; CI and the release workflow never pass it otherwise).
   - Reproducible lock install: builder installs uv (`docker/uv-bootstrap.txt`, `--require-hashes`, outside venv; Dependabot bumps it), exports `uv export --locked --no-dev --no-emit-project --format requirements.txt` and installs that file with pip in hash-checking mode, then installs `docker/build-backend.txt` (`--require-hashes --only-binary=:all:` outside venv), and runs `PYTHONPATH=/tmp/build-backend pip install --no-deps --no-build-isolation .` (never `pip install .`). Regenerate `docker/build-backend.txt` after `uv lock` via `uv run python scripts/gen_build_backend.py` (never hand-edit; Dependabot excludes it). Enforced by `tests/test_dockerfile.py` & `tests/test_build_backend_lock.py`.
7. **Registry Metadata (`server.json`):** root `description` **≤ 100 chars** (HTTP 422 otherwise).
8. **Git Safety & Releases:**
   - Never commit secrets; all work via feature branches and PRs.
   - **Tag is version (`uv-dynamic-versioning`):** `pyproject.toml` declares `dynamic = ["version"]`, `__version__` uses `importlib.metadata`, `server.json` commits `0.0.0`. Never bump versions in PRs (`pyproject.toml`, `src/sigma_mcp/__init__.py`, `server.json`, `uv.lock`, `CHANGELOG.md`). Untagged builds report `X.Y.(Z+1).devN+<sha>`; `scripts/check_version.py` rejects `0.0.0` / `0.0.1.devN`.
   - **Breaking changes (`feat!` / `fix!`):** carry `BREAKING CHANGE:` as the final PR body paragraph (before `<!-- This is an auto-generated comment: release notes by coderabbit.ai -->` if present) and **must include migration steps** (`scripts/release_notes.py` stops at the CodeRabbit marker). Keep PR body accurate before squash merge.
   - **`CHANGELOG.md` frozen at 1.2.2:** GitHub Releases are the changelog. Keep `README.md` unpinned (`uvx --from mcp-server-sigma sigma-mcp`).
   - **Version level:** before tagging, check the proposed version against the commit types since the last tag (`!` / `BREAKING CHANGE:` → major, `feat` → minor, otherwise patch), and that every breaking commit carries its footer with migration steps.
   - **Tagging (maintainer go only, no release PR):** preview `git fetch --tags && python3 scripts/release_notes.py` on `main` and post it with the release Ask; verify version unused on git tags, PyPI (`https://pypi.org/pypi/mcp-server-sigma/X.Y.Z/json`), and MCP Registry (`https://registry.modelcontextprotocol.io/v0.1/servers/io.github.christianclaudio%2Fsigma/versions/X.Y.Z`). In a throwaway clone, tag the release commit locally, run `rm -rf dist && uv build`, and confirm the wheel is `mcp_server_sigma-X.Y.Z-py3-none-any.whl` and `scripts/check_version.py` passes; discard the clone without pushing. PyPI rejects re-uploads: on post-PyPI failure, merge a fix and tag next patch.

---

## 🛠️ Development & Verification Commands

Requires `uv >= 0.13.4` (`[tool.uv] required-version` in `pyproject.toml`).

```bash
# Install editable with dev dependencies
uv sync --locked --extra dev --extra code-mode

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
uv run pytest tests/test_protocol.py --no-cov

# Build version guard (reads the single wheel in dist/; rejects 0.0.0 and the untagged 0.0.1.devN)
rm -rf dist && uv build && uv run python scripts/check_version.py

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

---

## 🔄 CI & Releases

- **CI (`.github/workflows/ci.yml`):** runs on `push`/`pull_request` to `main`, weekly schedule, and `workflow_dispatch` (`fetch-depth: 0` on package jobs). Jobs: Lint and types, Tests (Python 3.11–3.14, 100% cov + aggregate Tests), Tool contract assertions, OpenAPI drift, Protocol conformance, Build and package check, Container image build (no push; verifies stripped build deps, stdio `initialize`/`tools/list`, negative `CA_CERTIFICATES_FLOOR`), and Check API Drift (schedule/manual only; opens issue on drift). CodeQL runs via GitHub default setup.
- **Dependabot (`dependabot-automerge.yml`):** squash-merges minor/patch PRs after CI passes; major & docker updates wait for human review.
- **Releases (`.github/workflows/release.yml`):** triggered on `v*` tags (`fetch-depth: 0`); verifies wheel matches tag, publishes to PyPI, creates GitHub Release from `scripts/release_notes.py`, pushes Docker image (`UV_DYNAMIC_VERSIONING_BYPASS` set to tag version because the Docker build context has no `.git`), and stamps `server.json` & publishes to MCP Registry.

---

## VCS

```yaml
vcs:
  host: github
  remote: origin
  repository: christianclaudio/mcp-server-sigma
  default_branch: main
  protected_branches: [main]
  branch_policy: pr_only
  merge_strategy: squash
  delete_branch_on_merge: true
  require_coderabbit: true
  branch_naming: type/scope-desc
```
