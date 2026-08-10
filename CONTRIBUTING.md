# 🤝 Contributing to mcp-server-sigma

Welcome! We are thrilled that you want to contribute to `mcp-server-sigma`! 🚀  
Whether you're fixing a bug, adding support for a new Sigma API endpoint, polishing documentation, or writing tests, your help is warmly appreciated.

---

## 🌟 Quickstart Development Setup

Get your local dev environment up and running in 60 seconds:

#### POSIX (bash/zsh):
```bash
# 1. Clone & enter repository
git clone https://github.com/christianclaudio/mcp-server-sigma.git
cd mcp-server-sigma

# 2. Create & activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install in editable mode
pip install -e ".[dev]"
```

#### Windows (PowerShell):
```powershell
# 1. Clone & enter repository
git clone https://github.com/christianclaudio/mcp-server-sigma.git
cd mcp-server-sigma

# 2. Create & activate virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3. Install in editable mode
pip install -e ".[dev]"
```

#### Windows (Command Prompt `cmd.exe`):
```cmd
# 1. Clone & enter repository
git clone https://github.com/christianclaudio/mcp-server-sigma.git
cd mcp-server-sigma

# 2. Create & activate virtual environment
python -m venv .venv
.venv\Scripts\activate.bat

# 3. Install in editable mode
pip install -e ".[dev]"
```

---

## 🧪 Running Tests (No Sigma Org Needed!)

You don't need a live Sigma account to develop or test! Our comprehensive unit test suite mocks the HTTP layer completely:

```bash
# Run unit & safety tests with coverage report (100% target!)
pytest --cov=src/sigma_mcp --cov-report=term-missing -v
```

---

## 🔍 Verification Scripts

Before submitting your pull request, run our automated verification scripts:

### 1. OpenAPI Drift Check
Guards against broken or altered API paths by comparing `client.py` against official Sigma OpenAPI specs:
```bash
python scripts/check_openapi_drift.py
```

### 2. Tool Contract Validation
Asserts registered tool counts, MCP 2.0 annotations (`readOnlyHint`, `destructiveHint`), and profile gating:
```bash
python scripts/check_tool_contract.py
```

### 3. Code Formatting & Static Analysis
```bash
ruff check src/ tests/
ruff format --check .
mypy --strict src/
```

---

## 🛠️ How to Add a New Sigma MCP Tool

Adding a tool takes just 3 simple steps:

1. **Add Client Method** (`src/sigma_mcp/client.py`):
   ```python
   async def get_something(self, resource_id: str) -> JSONValue:
       path = f"/v2/resource/{urllib.parse.quote(resource_id, safe='')}"
       return (await self._request("GET", path)).json()
   ```

2. **Register Server Tool** (`src/sigma_mcp/server.py`):
   ```python
   @mcp.tool(
       annotations=ToolAnnotations(
           readOnlyHint=True,
           idempotentHint=True,
           openWorldHint=True,
       )
   )
   @sigma_tool
   async def sigma_get_something(resource_id: str) -> str:
       """Retrieve details for a specific resource."""
       client = await get_client()
       return json.dumps(await client.get_something(resource_id), indent=2)
   ```

3. **Add Unit Test** (`tests/test_tools_mocked.py`):
   Add a mocked test asserting clean JSON output and proper error handling.

---

## 🛡️ Live Integration Tests (Optional)

If you have a test Sigma organization and want to run live smoke tests:

#### POSIX (bash/zsh):
```bash
export SIGMA_CLIENT_ID="your-client-id"
export SIGMA_CLIENT_SECRET="your-client-secret"
export SIGMA_API_BASE_URL="https://api.us-a.aws.sigmacomputing.com"
SIGMA_LIVE_TESTS=1 pytest tests/test_integration_live.py -v
```

#### Windows (PowerShell):
```powershell
$env:SIGMA_CLIENT_ID="your-client-id"
$env:SIGMA_CLIENT_SECRET="your-client-secret"
$env:SIGMA_API_BASE_URL="https://api.us-a.aws.sigmacomputing.com"
$env:SIGMA_LIVE_TESTS="1"
pytest tests/test_integration_live.py -v
```

#### Windows (Command Prompt `cmd.exe`):
```cmd
set SIGMA_CLIENT_ID=your-client-id
set SIGMA_CLIENT_SECRET=your-client-secret
set SIGMA_API_BASE_URL=https://api.us-a.aws.sigmacomputing.com
set SIGMA_LIVE_TESTS=1
pytest tests/test_integration_live.py -v
```

> **Safety Guarantee:** Live tests register every created resource ID in a strict registry and clean them up when pytest teardown runs, including after test failures or exceptions. If execution is forcefully interrupted (e.g., SIGKILL or hard crash) before teardown runs, manually delete any remaining `mcptest-*` resources or rely on the pre-run sweep fixture on the next run.

---

## 🔀 Pull Request Merging & Git History

To maintain a clean, linear, and readable commit history on the default branch:
*   **Squash Merging**: This repository enforces **Squash Merging only**. When your PR is merged, all commits on your branch will be squashed into a single commit on `main`.
*   **PR Title Convention**: Because the Pull Request title becomes the final commit message on `main`, please ensure it follows the **Conventional Commits** specification (e.g., `feat: add user attribute tools`, `fix: sanitize path inputs`).

---

## 💬 Community Standards

Please adhere to our [Code of Conduct](CODE_OF_CONDUCT.md) in all interactions. Let's build something awesome together! 🎉
