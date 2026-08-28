# Multi-Tenant Token Exchange

This document describes the RFC 8693 token exchange flow used to operate across
tenant organizations in Sigma Computing.

## Overview

Sigma's multi-tenant architecture allows a parent organization to manage child
tenants via API. Authentication uses OAuth 2.0 Token Exchange (RFC 8693) to
obtain tenant-scoped access tokens from a parent organization token.

## JWT Structure

The subject token is a self-signed JWT:

### Header

```json
{
  "alg": "HS256",
  "kid": "<client_id>"
}
```

### Payload

```json
{
  "tenant": "<tenant_org_id>",
  "ver": "1.1",
  "aud": "sigmacomputing",
  "iat": 1770000000,
  "exp": 1770000300,
  "jti": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

#### Required Claims & Validation Rules:
- `tenant`: The target child organization ID (`org-...`).
- `ver`: Protocol specification version (must be `"1.1"` for tenant scoping).
- `aud`: Intended audience (must be `"sigmacomputing"`).
- `iat`: Issued-at Unix timestamp (seconds).
- `exp`: Expiry timestamp (must be $\le 300$ seconds after `iat` to restrict validity window).
- `jti`: Unique JWT nonce UUID to prevent replay attacks.

### Signature

HMAC-SHA256 signed with `client_secret` as the secret key.

## Token Exchange Request

```
POST /v2/auth/token
Content-Type: application/x-www-form-urlencoded

grant_type=urn:ietf:params:oauth:grant-type:token-exchange
&subject_token=<self_signed_jwt>
&subject_token_type=urn:ietf:params:oauth:token-type:jwt
&actor_token=<parent_access_token>
&actor_token_type=urn:ietf:params:oauth:token-type:access_token
```

### Parameters

| Parameter | Value |
|-----------|-------|
| `grant_type` | `urn:ietf:params:oauth:grant-type:token-exchange` |
| `subject_token` | Self-signed HS256 JWT with `{tenant: orgId}` payload |
| `subject_token_type` | `urn:ietf:params:oauth:token-type:jwt` |
| `actor_token` | Parent org access token (from client_credentials flow) |
| `actor_token_type` | `urn:ietf:params:oauth:token-type:access_token` |

### Response

```json
{
  "access_token": "<tenant_scoped_token>",
  "token_type": "bearer",
  "expires_in": 3600
}
```

## Per-Tenant Token Caching

The `SigmaClient` caches tenant tokens in `_tenant_cache`:

```python
_tenant_cache: dict[str, tuple[str, float]] = {}
```

- Key: `tenant_org_id`
- Value: `(access_token, expiry_timestamp)`
- Tokens are refreshed 60 seconds before expiry

## Pagination Models

Sigma uses **two different pagination models** across its API:

### 1. Cursor-based (Tenants)

```
GET /v2/tenants?nextPageToken=<token>
```

Response includes `nextPageToken` — pass it as a query parameter to fetch the
next page. When `nextPageToken` is absent or null, you've reached the last page.

### 2. Offset-based (Connections, Workbooks, Members, etc.)

```
GET /v2/workbooks?limit=200&offset=0
```

Response includes `total` and `hasMore`. Increment `offset` by `limit` until
`hasMore` is false or `offset >= total`.

## Usage

```python
import asyncio
import os
from sigma_mcp.client import SigmaClient


async def main():
    client_id = os.environ["SIGMA_CLIENT_ID"]
    client_secret = os.environ["SIGMA_CLIENT_SECRET"]
    base_url = os.environ["SIGMA_API_BASE_URL"]

    async with SigmaClient(client_id, client_secret, base_url) as client:
        # Get tenant-scoped client (reuses parent's HTTP transport)
        tenant_client = await client.for_tenant("tenant-org-uuid")

        # Use it like a normal client
        workbooks = await tenant_client.list_workbooks()
        print(workbooks)

        # IMPORTANT: Do NOT close tenant_client separately.
        # It shares the parent's HTTP transport — closing the parent
        # invalidates all derived tenant clients.


asyncio.run(main())
```

> **Transport lifecycle:** `_build_tenant_client` reuses the parent's
> `httpx.AsyncClient` transport. Each transport must be closed exactly once —
> via the parent's `async with` block. Never call `await tenant_client.close()`
> or use a tenant client in its own `async with` context manager.
