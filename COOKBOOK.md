# 📖 mcp-server-sigma Runbook & Cookbook

This document serves as the official operational guide for developers and AI agents maintaining the `mcp-server-sigma` project.

---

## 🍳 Recipe 1: The Release & Version Bump Lifecycle (Canonical 9-Step SOP)

Follow these steps in exact sequential order:

*   **Step 1: Create a Feature Branch**
    *   Never develop on `main`. Create a new branch: `git checkout -b <type>/<description>`.
*   **Step 2: Implement and Stage Changes**
    *   Write clean, type-safe Python code conforming to `mypy --strict`.
    *   Stage the files: `git add <files>`.
*   **Step 3: Run Local Static Analysis**
    *   Verify type safety: `uv run mypy --strict src/`.
    *   Verify code coverage is at 100%: `uv run pytest --cov=src/sigma_mcp --cov-fail-under=100`.
    *   Verify tool contract counts: `uv run python scripts/check_tool_contract.py`.
    *   Verify OpenAPI drift: `uv run python scripts/check_openapi_drift.py`.
    *   Verify stdio protocol: `uv run python scripts/smoke_test.py`.
*   **Step 4: Execute Local AI Self-Review Loop**
    *   Instruct the active AI assistant: *"Analyze the git diff --cached. Audit for secret leaks, traversal vulnerabilities, type safety, and correctness."*
    *   If the AI flags any issues, fix them, stage the changes, and repeat Steps 3 and 4 until 100% clean.
*   **Step 5: Document Changes (Changelog, Readme, Server Manifest)**
    *   Increment the version in `pyproject.toml` and `src/sigma_mcp/__init__.py`.
    *   Sync version details and environment variables inside `server.json`.
    *   *Constraint*: The `description` field in `server.json` **must be strictly 100 characters or fewer** to pass the MCP Registry schema validation (longer strings will fail with HTTP 422).
    *   Add release notes to `CHANGELOG.md`.
    *   If tool configurations or base URLs changed, update `README.md` and `skills/sigma-mcp/SKILL.md`.
*   **Step 6: Commit and Push**
    *   Commit with a conventional commit message: `git commit -m "conventional_prefix: description"`.
    *   Push to GitHub: `git push origin <branch>`.
    *   *Tip (Branch Updates)*: If the branch falls behind `main`, you can click "Update branch" directly on the GitHub PR page (or run `gh pr merge --update-branch` equivalent). If merging locally, complete with `git commit -m "merge: sync branch with main"`.
*   **Step 7: Create Pull Request and Wait for CodeRabbit**
    *   Open a Pull Request: `gh pr create --fill`.
    *   **Wait-State**: Do not merge immediately. Wait for the online CodeRabbit bot to finish analyzing the PR and post its review comment.
*   **Step 8: Review CodeRabbit Comments and Finalize**
    *   Read the online CodeRabbit PR review comments.
    *   If suggestions are valid, apply them locally, commit, and push.
    *   Once CodeRabbit review is resolved, queue auto-merge: `gh pr merge --auto --squash`.
    *   *Squash Merging constraint*: This repository enforces **Squash Merging only** on GitHub. Ensure the PR title is written as a Conventional Commit (e.g. `feat: ...`). During merge, verify the squash commit title/body to ensure it follows Conventional Commits.
    *   *Rate Limit Fallback*: If CodeRabbit reports a review rate-limit block, verify that Step 3 and Step 4 (Local AI Self-Review Loop) passed with 100% success, and then bypass and merge via `gh pr merge --squash --admin`.
*   **Step 9: Tag and Publish Release**
    *   Once merged to `main`, checkout `main` and pull: `git checkout main && git pull`.
    *   Tag the release matching `pyproject.toml` version: `git tag vX.Y.Z`.
    *   Push tag to trigger GitHub Action release to PyPI and MCP Registry: `git push origin vX.Y.Z`.
    *   *CI Failure/PyPI Duplicate Fallback*: PyPI has a strict **no-overwrite policy** for files. If a release workflow fails *after* PyPI upload completes, you **cannot** re-run or re-push the same tag. You **must** increment the patch version in `pyproject.toml`, `__init__.py`, and `server.json` (e.g. `1.1.0` -> `1.1.1`), open a new PR, merge it, and push the new version tag.

---

## 🍳 Recipe 2: Schema Drift Checks

*   **Step 1**: Run drift monitor script: `uv run python scripts/check_openapi_drift.py`.
*   **Step 2**: If path mismatches are found, update URL routes in `src/sigma_mcp/client.py` and run tests.

---

## 🍳 Recipe 3: Local Verification & MCP Inspector

```bash
# 1. Run stdio handshake smoke test
uv run python scripts/smoke_test.py

# 2. Interactive debugging with official MCP Inspector
npx -y @modelcontextprotocol/inspector sigma-mcp
```

---

## 🍳 Recipe 4: Composite Multi-Step Workflows

### 1. Zero-Downtime Workbook Promotion (Dev $\rightarrow$ Prod)
```python
# 1. Export source workbook spec
spec = await client.get_workbook_spec(workbook_id=dev_wb_id)

# 2. Swap data source mappings to production connection
prod_spec = await client.swap_sources_in_spec(spec, source_mapping={"conn-dev": "conn-prod"})

# 3. Create or update target workbook in production folder
target = await client.create_workbook_from_spec(folder_id=prod_folder_id, spec=prod_spec, name="Executive Dashboard")
```

### 2. Multi-Tenant Organization Provisioning
```python
# 1. Create tenant organization
tenant = await client.create_tenant(name="Acme Corp", cloud_provider="aws")

# 2. Assign initial workspace and team
team = await client.create_team(tenant_id=tenant["tenantId"], name="Analytics Team")
```
