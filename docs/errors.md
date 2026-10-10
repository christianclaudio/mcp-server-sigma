# Error Taxonomy

Every tool in this MCP server returns JSON text on both success and failure. Tools
never pass an unhandled exception or stack trace to the caller.

## Guarantee

The `@sigma_tool` decorator wraps all tool functions. When a tool fails, it catches
the exception and raises FastMCP `ToolError` whose text is a structured JSON error
payload, so the client receives a `tools/call` result with `isError: true`. This means:

- **Success**: JSON response from the Sigma API (`isError: false`)
- **Failure**: JSON error object with diagnostic information (`isError: true`)
- **Confirmation required**: a destructive or guarded write called without
  `confirm=True` makes no change and returns a normal result (`isError: false`); see
  [Confirmation prompt](#confirmation-prompt-not-an-error)

The MCP client always receives valid JSON — never a stack trace or unstructured
error message.

## Error Contract

The JSON text of a failed call has one of four error shapes — never an unhandled exception or stack
trace. Client secrets are redacted from all error messages before they reach the MCP
client. Redaction follows the template v1.6.0 house rules (`errors.py`): a secret is masked
whole with the fixed `***REDACTED***` marker, whatever its length. A quoted value is masked to
its closing unescaped quote (spaces, commas and escaped quotes included), an unquoted
`password=`/`api_key=`/`client_secret=`/`private_key=` value runs to the end of the line, any
`-----BEGIN ...-----` PEM block is masked, and JSON inside a message is parsed and redacted value
by value (`redact_message` / `redact_payload`) so it stays valid. Every tool error goes through `redact_message` (`sigma_tool` and `_tool_failure` in
`tools/common.py`), so a number or other non-string value under a credential key, for example
`{"password": 12345}`, is masked too. Sigma-specific patterns
(`subject_token`, raw JWTs, `ghs_` tokens, and the configured `SIGMA_CLIENT_SECRET` itself) run
alongside them.

### 1. API errors (`SigmaAPIError`)

Returned with `isError: true` when the Sigma REST API responds with a non-2xx status:

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

Returned with `isError: true` when an unhandled exception occurs inside a tool function:

```json
{
  "error": {
    "type": "internal",
    "message": "description of the unexpected failure"
  }
}
```

### 3. Validation errors (invalid request parameters)

Returned with `isError: true` when a tool detects missing or invalid arguments
before calling the API. The MCP specification lists input validation errors as tool
execution errors, reported in the tool result with `isError: true` so the model can
correct the call and retry
([Tools: Error Handling](https://modelcontextprotocol.io/specification/2026-07-28/server/tools#error-handling)).
`_invalid_request` raises FastMCP `ToolError` with the redacted payload, the same
path the decorator uses for the other two shapes:

```json
{
  "error": {
    "type": "invalid_request",
    "message": "connection_id is required"
  }
}
```

The `admin_bulk_deactivate_members` pattern checks use this shape too: a catch-all pattern,
a pattern that matches the empty string, an invalid regex, and a pattern that matches more
than 10 active members. The safety-cap error also carries `count` and `first_10` (the first
ten matching names). `elements_get_doc_page` reports a slug it rejects (bad characters,
`..`, or a host other than `help.sigmacomputing.com`) as `invalid_request`.

### 4. Tool failures detected by the tool

Returned with `isError: true` when the call cannot do what was asked even though the API
calls succeeded. `_tool_failure` raises FastMCP `ToolError` with the redacted payload,
`from None`, the same way `_invalid_request` does. The message is built by the tool; a raw
upstream response body is never included.

```json
{
  "error": {
    "type": "timeout",
    "message": "Export did not finish within timeout_seconds=300",
    "query_id": "q-123",
    "timeout_seconds": 300
  }
}
```

| `type` | Raised by | Extra fields |
|---|---|---|
| `not_found` | `workbooks_reassign_workbook_ownership` (no member for an email), `workbooks_copy_workbook_to_member` (member has no `homeFolderId`), `admin_bulk_remove_team_members` (no email resolved to a member) | `member_id` or `not_found` where relevant |
| `timeout` | `workbooks_export_and_download` (export not ready within `timeout_seconds`) | `query_id`, `timeout_seconds` |
| `upstream_response` | `workbooks_export_and_download` (no `queryId` in the export response), `elements_materialize_and_wait` (no job ID in the materialization response) | none |
| `docs_search_failed` | `elements_search_docs` (docs host returned a non-200 status) | `status` |
| `docs_search_empty` | `elements_search_docs` (no passage returned) | none |
| `page_not_found` | `elements_get_doc_page` (docs host returned a non-200 status) | `slug`, `status` |
| `not_supported` | `datasets_bulk_sync_tenant_connections` (client has no tenant token exchange) | none |
| `batch_failed` | a batch tool in which every item failed (see below) | context fields, `failed_count`, `errors` |

`workbooks_export_and_download` returns a normal result with `truncated: true` and the size,
without content, when the file exceeds `max_bytes`.

### Batch results

`admin_bulk_deactivate_members`, `workbooks_reassign_workbook_ownership`,
`elements_list_all_input_tables` and `datasets_bulk_sync_tenant_connections` act on many items.
Every batch result has one shape. Each item is an entry with the item key `id`, a `status`,
and either `result` (the item was done) or `error` (`"status": "failed"`). The top level always
has `status` (`success` or `partial_success`), a `<verb>_count` of the items done
(`deactivated_count`, `transferred_count`, `scanned_count`, `synced_count`), `failed_count`,
`results` and `errors`, plus the tool's own context fields. The field names these tools
returned before keep their place alongside: entries keep `memberId` and `name`
(deactivations), `name` (transfers), `workbookId`, `workbookName`, `pageId` and `stage`
(scans), and `orgId`, `name`, `connections_synced` and `connectionId` (tenant syncs); the top
level keeps `deactivated` and `failed`, `transferred` and `failed`, `workbooks_scanned`, and
`tenants_processed`. As on earlier releases, `results` of the deactivate, transfer and
tenant-sync tools lists every item: a failed item stays there with `"status": "failed"` and
its redacted message as the string `error` (deactivations also keep `"status": "skipped"`
with a `reason`), and is listed again in `errors` with the error object; `<verb>_count`
counts only the items done. `elements_list_all_input_tables` keeps its earlier top-level
`errors` list, one entry per failed stage (`pages`, or a page's `elements`) with the redacted
message as the string `error` and the error object as `error_detail`; its `failed_count`
counts failed workbooks. When at least one item succeeds, the call is a normal result
(`isError: false`):

```json
{
  "status": "partial_success",
  "transferred_count": 1,
  "failed_count": 1,
  "results": [
    { "id": "f0", "name": "WB 0", "status": "transferred",
      "result": { "id": "f0", "ownerId": "new-id" } },
    { "id": "f1", "name": "WB 1", "status": "failed",
      "error": "Sigma API PATCH /v2/files/f1 returned 403" }
  ],
  "errors": [
    {
      "id": "f1",
      "name": "WB 1",
      "status": "failed",
      "error": { "type": "sigma_api_error", "status_code": 403, "method": "PATCH",
                 "path": "/v2/files/f1", "detail": null, "request_id": null,
                 "message": "Sigma API PATCH /v2/files/f1 returned 403" }
    }
  ],
  "old_owner": { "email": "old@example.com", "memberId": "old-id" },
  "new_owner": { "email": "new@example.com", "memberId": "new-id" },
  "transferred": 1,
  "failed": 1
}
```

When every item fails, nothing was done, so the call is a tool error (`isError: true`) with the
same context fields, `failed_count` and `errors`:

```json
{
  "error": {
    "type": "batch_failed",
    "message": "All 2 workbook transfers failed; nothing was transferred.",
    "old_owner": { "email": "old@example.com", "memberId": "old-id" },
    "new_owner": { "email": "new@example.com", "memberId": "new-id" },
    "failed_count": 2,
    "errors": [
      { "id": "f0", "name": "WB 0", "status": "failed",
        "error": { "type": "sigma_api_error", "status_code": 403, "method": "PATCH",
                   "path": "/v2/files/f0", "detail": null, "request_id": null,
                   "message": "Sigma API PATCH /v2/files/f0 returned 403" } },
      { "id": "f1", "name": "WB 1", "status": "failed",
        "error": { "type": "internal", "message": "boom Authorization: Bearer ***REDACTED***" } }
    ]
  }
}
```

The other messages are "All N member deactivations failed; nothing was deactivated.",
"All N workbook scans failed; nothing was scanned." and "All N tenant syncs failed; nothing
was synced.". A scanned workbook's `result` has `name`, `input_table_count` and `page_errors`
(page entries in the same shape); a synced tenant's `result` has `name`, `status`,
`synced_count`, `failed_count`, `results` and `errors` for its connections.

A workbook scan fails when its pages cannot be listed or every page's elements fail (its `error`
has `"type": "batch_failed"`, `stage` and `pages`); a tenant sync fails when the tenant cannot
be reached or every connection sync fails (its `error` has `"type": "batch_failed"` and
`connections`). Each item is
attempted on its own: any `Exception` from one item is recorded and the batch continues. Each
failed item's `error` is an object with at least a non-empty `message`; an API failure also
keeps the `sigma_api_error` fields, and anything else has `type` (`internal`, or
`invalid_item` for a workbook with no file ID). Secrets (including bearer tokens; API, access, refresh, auth, id and session tokens; `X-Auth-Token` and `Authorization: Token` values; JSON `"token"` values; and a bare `token` key followed by `=` or `:`, with optional whitespace on either side and an optional quote before the value, as in `token=x`, `token: x`, `token = x` and `token: "x"`) are redacted in every string of the
per-item errors, in a partial result as well as in `batch_failed`, and the log line for a failed item names only its zero-based index and error type (never the message), without a traceback. A cancelled call
(`asyncio.CancelledError`) is not an item failure: it stops the call and propagates.

Every tool error is raised after the `except` block that caught the original exception has
ended, `from None`, so the unredacted exception is on neither `__cause__` nor `__context__`.

### Confirmation prompt (not an error)

Destructive tools and the guarded writes listed in the skill take `confirm: bool = False`.
Called without `confirm=True`, the tool makes no change and returns a normal result
(`isError: false`). The call did what it was designed to do, so it is not a failure:

```json
{
  "status": "confirmation_required",
  "executed": false,
  "message": "This destructive operation was not executed. Re-call this tool with confirm=true to proceed."
}
```

`admin_bulk_deactivate_members` keeps its own dry-run preview instead.

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
