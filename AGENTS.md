---
vcs:
  system: github
  remote: https://github.com/christianclaudio/mcp-server-sigma
  owner: christianclaudio
  repo: mcp-server-sigma
  default_branch: main
  branch_policy: pr_only
  merge_method: squash
  delete_branch_on_merge: true
---

# AGENTS.md

Instructions for AI coding agents (Antigravity, Claude Code, Copilot, Cursor, Windsurf) working on this repository or integrating Sigma Computing cloud analytics capabilities.

---

## 🎯 Project Overview

This is `mcp-server-sigma` — an enterprise Python Model Context Protocol (MCP) server for **Sigma Computing**, built on its public REST API (v2 and v3alpha); `scripts/check_openapi_drift.py` lists the spec endpoints the client does not cover. Bulk-destructive tools are listed in `full` and refused at call time unless enabled; the expected per-profile tool counts live in `scripts/check_tool_contract.py`.

**Primary Purpose**:
Expose deep cloud business intelligence, embedded analytics, workbook lineage, SQL data modeling, user/team administration, and tenant token exchange to AI agents with strict enterprise safety gates, offline testing, and multi-tenant token isolation.

---

## 🏗️ Key Paths

- `src/sigma_mcp/server.py` — root gateway: `create_server` mounts the domain sub-servers (`workbooks`, `datasets`, `elements`, `workspace`, `admin`) with matching namespaces, then applies the job allowlist, the read-only filter, and (on `full` only) Tool Search or Code Mode; prompts, resources.
- `src/sigma_mcp/profiles.py` — `PROFILES` (`full`, `readonly`, and the job profiles `analyst`, `author`, `modeler`, `embed`, `access_admin`), their tool allowlists, `FULL_ONLY_TOOLS`, `BULK_DESTRUCTIVE_TOOLS`, and the `readOnlyHint` filter.
- `src/sigma_mcp/tools/<domain>.py` — domain sub-servers (`admin`, `datasets`, `elements`, `workbooks`, `workspace`); `tools/common.py` holds the `ANNOTATION_*` constants and shared helpers.
- `src/sigma_mcp/client.py` — async HTTP client (`SigmaClient`, `_encode_segment()`, RFC 8693 token exchange). `errors.py` — exceptions and secret redaction. `webhooks.py` — HMAC verification. `middleware.py` — read-only gate (`readOnlyHint` only, unwraps the Tool Search `call_tool` proxy) and the admin bulk-destructive gate. `config.py` — settings.
- `scripts/check_tool_contract.py` — source of truth for expected tool counts and annotations. `README.md` and three test files repeat some of these counts (`SIGNED_OFF` in `tests/test_profiles.py`, the 172 in `tests/test_unit.py` and `tests/test_enterprise_assertion.py`), so change them together; do not add new copies.
- `scripts/check_openapi_drift.py`, `scripts/write_ops_check.py` (live create-and-teardown against a Sigma org; needs `SIGMA_CLIENT_ID`, `SIGMA_CLIENT_SECRET`, and `SIGMA_API_BASE_URL`), `scripts/check_conformance.sh` + `conformance-baseline.yml`.
- `scripts/release_notes.py` — release body from squash commits since the previous `v*` tag. `scripts/check_version.py` — runs after the build and reads the version from the single wheel in `dist/` (the file that ships, as release.yml's tag check does); fails on `0.0.0` (no git metadata) or `0.0.1.devN` (no reachable tag, a shallow checkout).
- `tests/` — unit tests are offline; live network tests live in `tests/test_e2e_live.py` (marked `pytest.mark.e2e`, deselected by default pytest `addopts` `-m 'not e2e'`, run with `-m e2e`). That test sets no skip and no env check; tool calls use `get_client`, which requires `SIGMA_CLIENT_ID` and `SIGMA_CLIENT_SECRET`. The unmarked `test_dispatch_tool_call_offline` in that file stays in the default suite. `tests/test_integration_live.py` skips unless `SIGMA_LIVE_TESTS=1` and `SIGMA_CLIENT_ID` are set. Security-hardening, webhook, and protocol tests are `tests/test_security_hardening.py`, `tests/test_webhooks.py`, and `tests/test_protocol.py`. Profile, read-only, and composition tests are `tests/test_profiles.py` and `tests/test_layered.py`. Version and release-tooling tests are `tests/test_version.py`, `tests/test_release_notes.py`, and `tests/test_check_version.py`.
- `.github/workflows/` — `ci.yml`, `release.yml` (on a `v*` tag: build with full history, check the wheel version matches the tag, build the release notes, publish to PyPI, create the GitHub Release from `scripts/release_notes.py`, then push the Docker image only after the publish job succeeds, then stamp the tag version into `server.json` and publish to the MCP Registry in a last `registry` job that needs the Docker job), `sigma-drift-monitor.yml`, `dependabot-automerge.yml` (squash auto-merge only for Dependabot PRs whose highest update is minor or patch; major updates wait for a human review).
- `server.json` (MCP Registry metadata), `Dockerfile`, `pyproject.toml`.

---

## ⚡ The Canonical Workflow: Building Tools from API / llm.txt

When translating an API documentation page or OpenAPI specification into an MCP tool, follow these 4 steps in exact order:

### 1. Client Method (`client.py`)
- Implement a dedicated `async def` method on `SigmaClient`.
- Type all arguments strictly. Never use bare `dict` or `Any` when a concrete schema or literal is known.
- URL path parameters are encoded per segment by `_encode_segment()` inside `_request()`, preserving colons on custom methods (e.g. `{id}:materialize`) while eliminating path traversal vulnerabilities (`..` $\rightarrow$ `%2E%2E`). Pass raw IDs in the path; do not pre-quote them, or they are encoded twice.
- Call `await self._request("METHOD", path, params=..., json_data=...)`.

### 2. Tool Handler (`tools/<domain>.py`)
- Register the tool on its domain sub-server with `@<domain>_server.tool(name=..., annotations=...)` and wrap with `@sigma_tool` (e.g. `tools/workbooks.py`). The root gateway in `server.py` mounts the domain with its namespace.
- Provide an explicit, agent-friendly docstring describing capabilities, parameters, and return shape.
- Destructive tools (delete, archive, deactivate, and bulk removal) **must** accept `confirm: bool = False`. Not every `POST`, `PUT`, or `PATCH` does (for example `workbooks_create_workbook` has no `confirm`).

### 3. Tool Annotations & Gating
- Pass MCP `ToolAnnotations` at registration with the `ANNOTATION_*` constants from `tools/common.py`:
  - `readOnlyHint`: `True` for inspection/GET; `False` for mutations. Every tool sets it explicitly; the read-only gate reads nothing else (a missing hint is treated as a write).
  - `destructiveHint`: `True` for delete/archive/deactivate actions; `False` otherwise.
  - `idempotentHint`: among the constants, `True` only on `ANNOTATION_IDEMPOTENT`; `ANNOTATION_READ_ONLY`, `ANNOTATION_WRITE_SAFE` and `ANNOTATION_DESTRUCTIVE` leave it unset. The Tool Search and Code Mode discovery tools get `READ_ONLY_ANNOTATIONS` from `profiles.py` (`readOnlyHint` and `idempotentHint` `True`, `openWorldHint` `False`).
  - `openWorldHint`: `True` when interacting with external networks/APIs.
- Gating:
  - Read-only mode (`SIGMA_MCP_READONLY=1` or profile `readonly`) keeps only tools annotated `readOnlyHint=True` and refuses every other call, directly or through `call_tool`, with a `SafetyViolationError` (a FastMCP `ToolError`, so `isError: true`).
  - Bulk protection: `admin_bulk_deactivate_members` and `admin_bulk_remove_team_members` are listed in `full` and refused at call time unless `SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1`.
  - Profiles (`SIGMA_MCP_PROFILE` or `--profile`): `full`, `readonly`, `analyst`, `author`, `modeler`, `embed`, `access_admin`. A new tool goes into the job profiles in `profiles.py` or into `FULL_ONLY_TOOLS`; unknown names fail at build. Tool Search and Code Mode attach on `full` only and never together.

### 4. Pure Offline Testing & Contract Sync (`tests/`)
- Add unit tests in `tests/` mocking responses via `unittest.mock.AsyncMock`.
- **Zero live network calls in the default suite.** Tests must run 100% offline in CI. Opt-in live modules are `tests/test_e2e_live.py` (`-m e2e`) and `tests/test_integration_live.py` (`SIGMA_LIVE_TESTS=1` plus `SIGMA_CLIENT_ID`).
- Update expected tool counts (per profile) in `scripts/check_tool_contract.py`, the `README.md` tables, and the copies in `tests/test_profiles.py` (`SIGNED_OFF`), `tests/test_unit.py` and `tests/test_enterprise_assertion.py`. A full-only tool also goes in `EXPECTED_FULL_ONLY` in `scripts/check_tool_contract.py`.
- Ensure test statement coverage remains at **100.0%** (`--cov-fail-under=100`). Branch coverage is not enabled.

---

## 🛡️ Non-Negotiable Safety & Security Rules

1. **Destructive Confirmation Gate**:
   - Delete, archive, deactivate, and bulk-removal tools must accept `confirm: bool = False`. If `False`, do not execute the side-effect (`_invalid_request` refusal, or a dry-run preview on `admin_bulk_deactivate_members`).
2. **Secret Redaction**:
   - Error messages, logs, and tracebacks must pass through regex redaction (`_redact_secrets`) stripping Bearer tokens, client secrets, access tokens, subject tokens, raw JWTs, and `ghs_` tokens.
3. **Multi-Tenant RFC 8693 Token Exchange**:
   - Strictly validate tenant delegation via `SIGMA_ALLOWED_TENANTS`. If `SIGMA_STRICT_TENANT_ALLOWLIST=1` is set, fail-closed with HTTP 403 on unrecognized tenants.
   - Delegation JWTs must include standard claims: `ver: "1.1"`, `aud: "sigmacomputing"`, `iat`, `exp` (+300s), and `jti` (UUID).
4. **Path Traversal Protection**:
   - All dynamic URL path segments must pass through `_encode_segment()`, preventing traversal attacks.
5. **Bulk Destructive Caps**:
   - Mass operations require `SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1`. `admin_bulk_deactivate_members` defaults to `dry_run=True` and caps at 10 active members. `admin_bulk_remove_team_members` has no `dry_run` parameter, requires `confirm=True`, and caps at 50 emails.
6. **Webhook Signature Validation**:
   - Webhook payloads must be verified using `hmac.compare_digest` to prevent timing attacks.
7. **Registry Metadata Constraint**:
   - In `server.json`, the root `description` must be **strictly $\le$ 100 characters** to pass MCP Registry schema validation (longer strings trigger HTTP 422).
8. **Git Safety & Releases**:
   - Never commit API secrets or tenant credentials. All changes proceed via feature branches and PRs.
   - **The git tag is the version.** `uv-dynamic-versioning` reads the `vX.Y.Z` tag at build time; `pyproject.toml` declares `dynamic = ["version"]`, `__version__` comes from `importlib.metadata`, and `server.json` commits `0.0.0` (the release workflow stamps the tag version into it). PRs never edit a version: no bump in `pyproject.toml`, `src/sigma_mcp/__init__.py`, `server.json`, `uv.lock`, or `CHANGELOG.md`. Untagged builds report `X.Y.(Z+1).devN+<sha>`; a build with no git metadata reports the fallback `0.0.0`, which `scripts/check_version.py` rejects when run on the built wheel after the build.
   - **Breaking changes:** every `feat!` / `fix!` PR (any `type!:` title) carries a `BREAKING CHANGE:` footer as the final paragraph of the PR body, and the footer text must include the migration steps. `BREAKING CHANGE:` (or its synonym `BREAKING-CHANGE:`) is the only footer token; do not add a separate migration token. `scripts/release_notes.py` stops at the CodeRabbit marker line (outside a code fence) `<!-- This is an auto-generated comment: release notes by coderabbit.ai -->` and ignores everything after it, so the footer goes before CodeRabbit's generated summary, never inside it.
   - **Squash merges use the PR body as the commit message** (repo settings: PR title as squash title, PR body as squash message). Keep the PR body accurate up to the merge, because `scripts/release_notes.py` reads it from the squash commit.
   - **`CHANGELOG.md` is frozen** as of 1.2.2. GitHub Releases are the changelog: `scripts/release_notes.py` builds each release body from the squash commits since the previous tag (every `BREAKING CHANGE:` footer verbatim, then the commit subjects). Do not add CHANGELOG entries.
   - `skills/sigma-mcp/SKILL.md` carries no version: the [Agent Skills specification](https://agentskills.io/specification) has no top-level `version` field. Do not add one. Update the skill only when its operator guidance changes.
   - **README is outside the release version ceremony.** Do not add or chase `README.md` `==X.Y.Z` install pins. Update `README.md` only when project behavior, install method, config, or commands actually change. Prefer unpinned install examples (`uvx --from mcp-server-sigma sigma-mcp`) or point readers to GitHub Releases.

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
uv run pytest tests/test_protocol.py --no-cov

# Build version guard (reads the single wheel in dist/; rejects 0.0.0 and the untagged 0.0.1.devN)
rm -rf dist && uv build && uv run python scripts/check_version.py

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

---

## 🔄 CI & Releases

CI is defined in `.github/workflows/ci.yml` (jobs: lint and types, tests on Python 3.10–3.13 at 100% coverage, tool contract and env gating, OpenAPI drift and conformance, build + `scripts/check_version.py` + `twine check`, Docker build, CodeQL). Jobs that install or build the package check out with `fetch-depth: 0`, because a shallow checkout has no reachable tag and reports `0.0.1.devN`. The Docker build context has no `.git`: the CI Docker build is an entrypoint check that keeps the `0.0.0` fallback, and `release.yml` passes the tag version as `UV_DYNAMIC_VERSIONING_BYPASS`. Run the commands above before opening a PR. Scheduled upstream drift runs in `sigma-drift-monitor.yml`.

Do not create tags or releases unless the maintainer asks. There is no release PR: merged commits accumulate on `main`, and releases go out on any weekday on the maintainer's go; no fixed release day. Before the tag:

- The release owner previews the release body on an up-to-date `main` with full history and tags (`git fetch --tags && python3 scripts/release_notes.py`) and posts it with the release Ask.
- The reviewer checks the proposed version against the commit types since the last tag (`!` / `BREAKING CHANGE:` → major, `feat` → minor, otherwise patch), that every breaking commit carries its footer with migration steps, and that the version is unused in all three places it could already exist:
  ```bash
  git ls-remote --tags origin vX.Y.Z                                                    # prints nothing
  curl -s -o /dev/null -w '%{http_code}\n' https://pypi.org/pypi/mcp-server-sigma/X.Y.Z/json   # prints 404
  curl -s -o /dev/null -w '%{http_code}\n' https://registry.modelcontextprotocol.io/v0.1/servers/io.github.christianclaudio%2Fsigma/versions/X.Y.Z   # prints 404
  ```
  In a throwaway clone, the reviewer tags the release commit locally, runs `rm -rf dist && uv build`, and confirms the wheel is `mcp_server_sigma-X.Y.Z-py3-none-any.whl` and `scripts/check_version.py` passes; then discards the clone without pushing.
- Only the maintainer's go creates the tag. PyPI never accepts the same version twice: if a release fails after the PyPI upload, do not re-run it; merge a fix and tag the next patch.
