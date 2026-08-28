---
name: Bug Report
about: Report a bug or unexpected behavior
title: "[Bug] "
labels: bug
assignees: ''
---

## Description

A clear description of the bug.

## Steps to Reproduce

1. Configure environment with `...`
2. Call tool `sigma_...` with parameters `...`
3. Observe error

## Expected Behavior

What you expected to happen.

## Actual Behavior

What actually happened. Include the JSON error response if available.

> **Before pasting:** redact any credentials, tokens, cookies, personal data,
> and private resource identifiers (member IDs, org IDs, etc.) from the response.

```json
{
  "type": "sigma_api_error",
  "status_code": ...,
  "method": "...",
  "path": "...",
  "detail": ...,
  "request_id": "..."
}
```

> If your report contains sensitive information that cannot be safely redacted,
> use the [private security advisory](https://github.com/christianclaudio/mcp-server-sigma/security/advisories/new)
> instead of a public issue.

## Environment

- Python version:
- OS:
- Sigma region (e.g., aws-us-east):
- MCP host (e.g., Claude Desktop, Cursor):

## Additional Context

Any other relevant information.
