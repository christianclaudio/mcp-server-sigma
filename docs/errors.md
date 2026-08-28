# Error Taxonomy

Every tool in this MCP server returns JSON on both success and failure. Tools
never raise unhandled exceptions to the caller.

## Guarantee

The `@sigma_tool` decorator wraps all tool functions. On any `SigmaAPIError`,
it catches the exception and returns a structured JSON error payload. This means:

- **Success**: JSON response from the Sigma API
- **Failure**: JSON error object with diagnostic information

The MCP client always receives valid JSON — never a stack trace or unstructured
error message.

## Error Contract

The `@sigma_tool` decorator guarantees that three possible error shapes are
returned — never an unhandled exception or stack trace. Client secrets are
redacted from all error messages before they reach the MCP client.

### 1. API errors (`SigmaAPIError`)

Returned when the Sigma REST API responds with a non-2xx status:

```json
{
  "error": {
    "type": "sigma_api_error",
    "status_code": 404,
    "method": "GET",
    "path": "/v2/workbooks/abc-123",
    "detail": { "message": "Workbook not found" },
    "request_id": "req-xyz-789"
  }
}
```

### 2. Internal errors (unexpected exceptions)

Returned when an unhandled exception occurs inside a tool function:

```json
{
  "error": {
    "type": "internal",
    "message": "description of the unexpected failure"
  }
}
```

### 3. Validation errors (invalid request parameters)

Returned early when a tool detects missing or invalid arguments before calling
the API:

```json
{
  "error": {
    "type": "invalid_request",
    "message": "connection_id is required"
  }
}
```

## Status Code Reference

### 400 Bad Request

Invalid parameters or malformed request body.

```json
{
  "type": "sigma_api_error",
  "status_code": 400,
  "method": "POST",
  "path": "/v2/workbooks",
  "detail": { "message": "Missing required field: name" },
  "request_id": "req-001"
}
```

### 401 Unauthorized

Token expired or invalid credentials.

```json
{
  "type": "sigma_api_error",
  "status_code": 401,
  "method": "GET",
  "path": "/v2/members",
  "detail": { "message": "Invalid or expired token" },
  "request_id": "req-002"
}
```

### 403 Forbidden

Insufficient permissions for the operation.

```json
{
  "type": "sigma_api_error",
  "status_code": 403,
  "method": "DELETE",
  "path": "/v2/files/inode-123",
  "detail": { "message": "Admin role required" },
  "request_id": "req-003"
}
```

### 404 Not Found

Resource does not exist or was deleted.

```json
{
  "type": "sigma_api_error",
  "status_code": 404,
  "method": "GET",
  "path": "/v2/workbooks/nonexistent",
  "detail": { "message": "Workbook not found" },
  "request_id": "req-004"
}
```

### 429 Too Many Requests

Rate limit exceeded. The client retries with exponential backoff (up to
`max_retries` attempts). If all retries fail, this error is returned.

```json
{
  "type": "sigma_api_error",
  "status_code": 429,
  "method": "GET",
  "path": "/v2/workbooks",
  "detail": "Rate limit exceeded after max retries",
  "request_id": "req-005"
}
```

### 500 Internal Server Error

Sigma API server error. Transient — safe to retry.

```json
{
  "type": "sigma_api_error",
  "status_code": 500,
  "method": "POST",
  "path": "/v2/connections/abc/sync",
  "detail": { "message": "Internal server error" },
  "request_id": "req-006"
}
```

## Retry Behavior

The client automatically retries on `429` responses:

1. Check `Retry-After` header for server-suggested delay
2. Fall back to exponential backoff: `base_delay * 2^attempt`
3. Default: 3 retries with 1s base delay (1s, 2s, 4s)
4. After exhausting retries, return the 429 error as structured JSON
