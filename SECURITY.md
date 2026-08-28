# 🛡️ Security Policy & Best Practices

> **Disclaimer:** `mcp-server-sigma` is an independent open-source community project and is **not** affiliated with, endorsed by, or supported by Sigma Computing, Inc. *"Sigma Computing"* is a registered trademark of Sigma Computing, Inc.

---

## 🔒 Supported Versions

| Version | Supported |
|---------|-----------|
| `1.0.x` | ✅ Yes    |
| `< 1.0` | ❌ No     |

---

## 🚨 Reporting a Vulnerability

**Please do NOT open public issues for security vulnerabilities.**

Report security vulnerabilities privately via [GitHub Security Advisories](https://github.com/christianclaudio/mcp-server-sigma/security/advisories/new).

You will receive an acknowledgement within 5 business days and a status update within 15 business days. If a patch is warranted, we will publish a fix and credit you in the release notes!

---

## 🔐 Operator Security Guidelines

This MCP server holds credentials scoped to your **entire Sigma organization**. Please review the following recommendations before deployment.

### 1. Credentials are Org-Scoped
`SIGMA_CLIENT_ID` and `SIGMA_CLIENT_SECRET` operate at the organization level. We strongly recommend creating a **dedicated API Client** in Sigma for this server rather than reusing administrator credentials, allowing you to scope permissions and rotate keys independently.

### 2. Multi-Tenant Token Security
For multi-tenant token exchange, this server signs an **HS256 JWT using your client secret** as the HMAC key to exchange for tenant-scoped access tokens via Sigma's documented [RFC 8693](https://datatracker.ietf.org/doc/html/rfc8693) flow.
- Never commit secrets to git.
- Keep secrets in environment variables or your MCP host's secret manager.
- Client secrets are automatically redacted from error messages and logs by `_redact_secrets()`.

### 3. Read-Only Mode for Agent Deployments
When connecting this server to autonomous agents or public assistant interfaces, run with `SIGMA_MCP_READONLY=1`:
```bash
SIGMA_MCP_READONLY=1 sigma-mcp
```
This restricts registration exclusively to **83 read-only tools**, completely removing all mutation endpoints from the model's tool context.

### 4. Safety Gates for Single & Bulk Operations
- **Single Delete Operations:** Require explicit `confirm=True` on all atomic delete/deactivate endpoints (`sigma_delete_workspace`, `sigma_delete_file`, etc.).
- **Bulk Destructive Tools:** `sigma_bulk_deactivate_members` and `sigma_bulk_remove_team_members` are disabled by default and require `SIGMA_MCP_ALLOW_BULK_DESTRUCTIVE=1`.
- **Bulk Deactivation Protections:** Includes `dry_run=True` default, `confirm=False` gate, rejection of catch-all regexes (`.*`, `.+`), and a 10-member safety cap.

### 5. Network Transport Exposure
When using network transport (`--transport streamable-http`), bind to `127.0.0.1` or place an authenticating HTTPS proxy in front of the listener.

---

## 🛡️ Summary of Deployment Postures

| Use Case | Recommended Configuration |
|----------|---------------------------|
| **Agent Exploration & Shared Assistants** | `SIGMA_MCP_READONLY=1` |
| **Building Dashboards & Data Models** | `SIGMA_MCP_PROFILE=core` |
| **Embedded Analytics & Multi-Tenant** | `SIGMA_MCP_PROFILE=embed` |
| **Organization Administration** | `SIGMA_MCP_PROFILE=admin` |
| **Full Operations (Default)** | `SIGMA_MCP_PROFILE=full` |
