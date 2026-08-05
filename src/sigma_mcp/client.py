"""Sigma Computing REST API client (async).

Covers the full v2 API surface (250 operations). Handles OAuth token lifecycle
and provides typed methods organized by resource domain.

Gotchas discovered through production use:
- POST /v2/workbooks with templateId is SILENTLY IGNORED. Use POST /v2/templates/save_workbook.
- Data model update is PUT /v2/dataModels/{id}/spec (not PATCH).
- Workbook deletion is DELETE /v2/files/{inodeId} (no delete-workbook endpoint).
- GET /v2/workbooks/{id}/sources rejects query params (400 on ?limit=).
- New schemas need POST /v2/connections/{id}/sync before API can resolve paths.
- The base URL must be region-specific (e.g. api.us-a.aws.sigmacomputing.com).
"""

from __future__ import annotations

import asyncio
import datetime
import email.utils
import os
import random
import time
import uuid
from typing import Any
from urllib.parse import quote

import httpx
from httpx import Response

from .errors import SigmaAPIError

# JSON type alias for return annotations
JSONValue = dict[str, Any] | list[Any] | str | int | float | bool | None

# Regional base URLs
REGIONS = {
    "aws-us-west": "https://aws-api.sigmacomputing.com",
    "aws-us-east": "https://api.us-a.aws.sigmacomputing.com",
    "aws-ca": "https://api.ca.aws.sigmacomputing.com",
    "aws-eu": "https://api.eu.aws.sigmacomputing.com",
    "aws-uk": "https://api.uk.aws.sigmacomputing.com",
    "aws-au": "https://api.au.aws.sigmacomputing.com",
    "azure-us": "https://api.us.azure.sigmacomputing.com",
    "azure-eu": "https://api.eu.azure.sigmacomputing.com",
    "azure-ca": "https://api.ca.azure.sigmacomputing.com",
    "azure-uk": "https://api.uk.azure.sigmacomputing.com",
    "azure-au": "https://api.au.azure.sigmacomputing.com",
    "gcp": "https://api.sigmacomputing.com",
}


class SigmaClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str,
        *,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_retry_delay: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_retry_delay = max_retry_delay
        self._token: str | None = None
        self._token_expiry: float = 0
        # When http_client is supplied the caller owns its lifecycle; this is how
        # tenant-scoped clients share the parent's connection pool without
        # creating (and leaking) a second one.
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx.AsyncClient(timeout=60.0)
        self._tenant_cache: dict[str, tuple[str, float]] = {}
        # Tenant-scoped clients record their org and parent for token refresh.
        self._tenant_org_id: str | None = None
        self._parent: SigmaClient | None = None

    async def aclose(self) -> None:
        """Close the HTTP transport, if this client owns it.

        A client built by ``for_tenant`` borrows the parent's transport, so
        calling this on one is a no-op rather than closing the parent's pool.
        """
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> SigmaClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    # ─── Multi-tenant token exchange (RFC 8693) ────────────────────────────

    async def for_tenant(self, tenant_org_id: str) -> SigmaClient:
        """Create a tenant-scoped client via RFC 8693 token exchange.

        Signs a self-signed HS256 JWT with {tenant: org_id}, kid=client_id,
        key=client_secret. Exchanges it for a tenant-scoped access token.
        Caches tokens per tenant with TTL refresh 60s before expiry.
        """
        import jwt as pyjwt

        allowed_tenants_env = os.environ.get("SIGMA_ALLOWED_TENANTS", "").strip()
        strict_mode = os.environ.get("SIGMA_STRICT_TENANT_ALLOWLIST", "").strip() in ("1", "true", "yes")

        if allowed_tenants_env:
            allowed_set = {t.strip() for t in allowed_tenants_env.split(",") if t.strip()}
            if tenant_org_id not in allowed_set:
                raise SigmaAPIError(
                    status_code=403,
                    path="/v2/auth/token",
                    method="POST",
                    detail=f"Tenant '{tenant_org_id}' is not in SIGMA_ALLOWED_TENANTS allowlist",
                )
        elif strict_mode:
            raise SigmaAPIError(
                status_code=403,
                path="/v2/auth/token",
                method="POST",
                detail=(
                    f"Tenant '{tenant_org_id}' denied: SIGMA_STRICT_TENANT_ALLOWLIST is active and no allowlist"
                    " configured"
                ),
            )

        now = time.time()

        # Check cache — evict if expired (expiry already has 60s buffer applied)
        if tenant_org_id in self._tenant_cache:
            cached_token, expiry = self._tenant_cache[tenant_org_id]
            if now < expiry:
                # expiry already has buffer; pass remaining seconds raw (no 2nd buffer)
                return self._build_tenant_client(cached_token, expiry - now, tenant_org_id, apply_buffer=False)
            del self._tenant_cache[tenant_org_id]

        # Get parent org token first
        parent_token = await self._get_token()

        # Build self-signed JWT
        now_ts = int(time.time())
        subject_jwt = pyjwt.encode(
            {
                "tenant": tenant_org_id,
                "ver": "1.1",
                "aud": "sigmacomputing",
                "iat": now_ts,
                "exp": now_ts + 300,
                "jti": str(uuid.uuid4()),
            },
            self.client_secret,
            algorithm="HS256",
            headers={"kid": self.client_id},
        )

        # Token exchange
        r = await self._http.post(
            f"{self.base_url}/v2/auth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": subject_jwt,
                "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                "actor_token": parent_token,
                "actor_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )
        if r.status_code >= 400:
            raise SigmaAPIError(
                status_code=r.status_code,
                path="/v2/auth/token",
                method="POST",
                detail=r.text[:500],
                request_id=r.headers.get("x-request-id"),
            )
        data = r.json()
        tenant_token = data["access_token"]
        expires_in: float = data.get("expires_in", 3600)
        # Cache with 60s buffer
        self._tenant_cache[tenant_org_id] = (tenant_token, now + expires_in - 60)

        return self._build_tenant_client(tenant_token, expires_in, tenant_org_id)

    def _build_tenant_client(
        self, token: str, expires_in: float, tenant_org_id: str, *, apply_buffer: bool = True
    ) -> SigmaClient:
        """Create a client pre-loaded with a tenant-scoped token.

        Reuses the parent client's HTTP transport. Closing the parent client
        invalidates derived tenant clients.

        When *apply_buffer* is False the caller guarantees the buffer has already
        been applied (e.g. cache-hit path where expiry is pre-buffered).
        """
        tc = SigmaClient(
            self.client_id,
            self.client_secret,
            self.base_url,
            max_retries=self.max_retries,
            base_delay=self.base_delay,
            max_retry_delay=self.max_retry_delay,
            http_client=self._http,
        )
        tc._token = token
        tc._token_expiry = time.time() + expires_in - (60 if apply_buffer else 0)
        tc._tenant_org_id = tenant_org_id
        tc._parent = self
        return tc

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry:
            return self._token
        # Tenant-scoped clients must re-exchange via the parent, never fall back
        # to a client_credentials grant (which would mint a parent-org token).
        if self._tenant_org_id is not None and self._parent is not None:
            refreshed = await self._parent.for_tenant(self._tenant_org_id)
            self._token = refreshed._token
            self._token_expiry = refreshed._token_expiry
            return self._token  # type: ignore[return-value]
        r = await self._http.post(
            f"{self.base_url}/v2/auth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        r.raise_for_status()
        data = r.json()
        token: str = data["access_token"]
        self._token = token
        self._token_expiry = time.time() + data.get("expires_in", 3600) - 60
        return token

    async def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {await self._get_token()}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: JSONValue = None,
        allow_statuses: frozenset[int] | None = None,
    ) -> Response:
        """Execute an HTTP request with exponential backoff on 429 responses.

        *allow_statuses* — additional non-error status codes (beyond 2xx) that
        should be returned without raising.  For example ``frozenset({204})``
        lets callers treat 204 as success.
        """

        def _encode_segment(seg: str) -> str:
            if seg in ("", "v2", "v2.1"):
                return seg
            resource, sep, custom_method = seg.partition(":")
            encoded = quote(resource, safe="").replace("..", "%2E%2E")
            return f"{encoded}{sep}{custom_method}" if sep else encoded

        safe_segments = [_encode_segment(seg) for seg in path.split("/")]
        safe_path = "/".join(safe_segments)
        url = f"{self.base_url}{safe_path}"
        for attempt in range(self.max_retries + 1):
            r = await self._http.request(
                method, url, headers=await self._headers(), params=params, json=json_data, timeout=60.0
            )
            if r.status_code != 429:
                if r.status_code >= 400 and not (allow_statuses and r.status_code in allow_statuses):
                    detail = None
                    try:
                        detail = r.json()
                    except Exception:
                        detail = r.text[:500] if r.text else None
                    request_id = r.headers.get("x-request-id")
                    raise SigmaAPIError(
                        status_code=r.status_code,
                        path=path,
                        method=method,
                        detail=detail,
                        request_id=request_id,
                    )
                return r
            if attempt == self.max_retries:
                raise SigmaAPIError(
                    status_code=429,
                    path=path,
                    method=method,
                    detail="Rate limit exceeded after max retries",
                    request_id=r.headers.get("x-request-id"),
                )
            retry_after = r.headers.get("Retry-After")
            parsed_delay = self._parse_retry_after(retry_after)
            if parsed_delay is not None:
                delay = parsed_delay * random.uniform(1.0, 1.3)
            else:
                delay = self.base_delay * (2**attempt) * random.uniform(0.5, 1.5)
            delay = min(delay, self.max_retry_delay)
            await asyncio.sleep(delay)
        # unreachable, satisfies type checker
        raise SigmaAPIError(status_code=429, path=path, method=method, detail="Rate limit exceeded")  # pragma: no cover

    @staticmethod
    def _parse_retry_after(header_val: str | None) -> float | None:
        if not header_val:
            return None
        try:
            val = float(header_val)
            return max(0.0, val)
        except (ValueError, OverflowError):
            pass
        try:
            dt = email.utils.parsedate_to_datetime(header_val)
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                now = datetime.datetime.now(datetime.timezone.utc)
                return max(0.0, (dt - now).total_seconds())
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_json_body(r: Response) -> JSONValue:
        """Parse JSON from a response, returning None for 204 or empty bodies."""
        if r.status_code == 204 or not r.content:
            return None
        return r.json()  # type: ignore[no-any-return]

    async def get(self, path: str, params: dict[str, Any] | None = None) -> JSONValue:
        r = await self._request("GET", path, params=params)
        return self._parse_json_body(r)

    async def post(self, path: str, json_data: JSONValue = None) -> JSONValue:
        r = await self._request("POST", path, json_data=json_data)
        return self._parse_json_body(r)

    async def put(self, path: str, json_data: JSONValue = None) -> JSONValue:
        r = await self._request("PUT", path, json_data=json_data)
        return self._parse_json_body(r)

    async def patch(self, path: str, json_data: JSONValue = None) -> JSONValue:
        r = await self._request("PATCH", path, json_data=json_data)
        return self._parse_json_body(r)

    async def delete(self, path: str) -> int:
        r = await self._request("DELETE", path)
        return r.status_code

    async def auto_paginate(
        self, path: str, params: dict[str, Any] | None = None, limit: int = 200, max_pages: int = 1000
    ) -> list[dict[str, Any]]:
        """Follow nextPage tokens to retrieve all entries from a paginated endpoint."""
        all_entries: list[dict[str, Any]] = []
        p: dict[str, Any] = dict(params or {})
        p.setdefault("limit", limit)
        seen_pages: set[str] = set()
        page_count = 0
        while page_count < max_pages:
            page_count += 1
            data = await self.get(path, p)
            if isinstance(data, dict):
                all_entries.extend(data.get("entries", []))
                next_page = data.get("nextPage")
            else:
                break
            if not next_page:
                break
            if next_page in seen_pages:
                break
            seen_pages.add(next_page)
            p["page"] = next_page
        return all_entries

    # ─── Connections ───────────────────────────────────────────────────────
    async def list_connections(self) -> JSONValue:
        return await self.get("/v2/connections")

    async def get_connection(self, connection_id: str) -> JSONValue:
        return await self.get(f"/v2/connections/{connection_id}")

    async def create_connection(self, body: dict[str, Any]) -> JSONValue:
        return await self.post("/v2/connections", body)

    async def update_connection(self, connection_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/connections/{connection_id}", body)

    async def delete_connection(self, connection_id: str) -> int:
        return await self.delete(f"/v2/connections/{connection_id}")

    async def test_connection(self, connection_id: str) -> JSONValue:
        return await self.get(f"/v2/connections/{connection_id}/test")

    async def sync_connection(self, connection_id: str, path: list[str] | None = None) -> JSONValue:
        return await self.post(f"/v2/connections/{connection_id}/sync", {"path": path or []})

    async def list_connection_paths(self) -> JSONValue:
        """List all connection paths (global, not per-connection)."""
        return await self.get("/v2/connections/paths")

    async def list_columns_for_table(self, table_id: str) -> JSONValue:
        """Get columns for a table by tableId."""
        return await self.get(f"/v2/connections/tables/{table_id}/columns")

    async def lookup_connection_path(self, connection_id: str, path: list[str]) -> JSONValue:
        return await self.post(f"/v2/connection/{connection_id}/lookup", {"path": path})

    # ─── Connection Grants ─────────────────────────────────────────────────
    async def list_connection_grants(self, connection_id: str) -> JSONValue:
        return await self.get(f"/v2/connections/{connection_id}/grants")

    async def add_connection_grant(self, connection_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/connections/{connection_id}/grants", body)

    async def delete_connection_grant(self, connection_id: str, grant_id: str) -> int:
        return await self.delete(f"/v2/connections/{connection_id}/grants/{grant_id}")

    async def list_connection_path_grants(self, connection_path_id: str) -> JSONValue:
        return await self.get(f"/v2/connections/paths/{connection_path_id}/grants")

    async def add_connection_path_grant(self, connection_path_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/connections/paths/{connection_path_id}/grants", body)

    async def delete_connection_path_grant(self, connection_path_id: str, grant_id: str) -> int:
        return await self.delete(f"/v2/connections/paths/{connection_path_id}/grants/{grant_id}")

    # ─── Workbooks ─────────────────────────────────────────────────────────
    async def list_workbooks(self, limit: int = 200) -> JSONValue:
        return await self.get("/v2/workbooks", {"limit": limit})

    async def get_workbook(self, workbook_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}")

    async def create_workbook(self, name: str, folder_id: str, description: str = "") -> JSONValue:
        return await self.post("/v2/workbooks", {"name": name, "folderId": folder_id, "description": description})

    async def duplicate_workbook(self, workbook_id: str, body: dict[str, Any] | None = None) -> JSONValue:
        return await self.post(f"/v2/workbooks/{workbook_id}/copy", body or {})

    async def delete_file(self, inode_id: str) -> int:
        return await self.delete(f"/v2/files/{inode_id}")

    async def get_workbook_schema(self, workbook_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/schema")

    async def list_workbook_pages(self, workbook_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/pages")

    async def list_workbook_elements(self, workbook_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/elements", {"limit": 200})

    async def list_workbook_columns(self, workbook_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/columns", {"limit": 500})

    async def list_workbook_queries(self, workbook_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/queries", {"limit": 50})

    async def list_workbook_controls(self, workbook_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/controls", {"limit": 50})

    async def list_workbook_sources(self, workbook_id: str) -> JSONValue:
        r = await self._request("GET", f"/v2/workbooks/{workbook_id}/sources")
        return self._parse_json_body(r)

    async def swap_workbook_sources(self, workbook_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/workbooks/{workbook_id}/swapSources", body)

    async def get_workbook_version_history(self, workbook_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/version-history")

    async def restore_workbook_version(self, workbook_id: str, version: int) -> JSONValue:
        return await self.post(f"/v2/workbooks/{workbook_id}/restoreVersion", {"version": version})

    async def list_workbook_lineage(self, workbook_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/lineage")

    # ─── Workbook Grants ───────────────────────────────────────────────────
    async def list_workbook_grants(self, workbook_id: str) -> JSONValue:
        return await self.get("/v2/grants", {"inodeId": workbook_id, "limit": 200})

    async def grant_workbook_access(self, workbook_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/workbooks/{workbook_id}/grants", body)

    async def delete_workbook_grant(self, workbook_id: str, grant_id: str) -> int:
        return await self.delete(f"/v2/workbooks/{workbook_id}/grants/{grant_id}")

    # ─── Workbook Embeds ───────────────────────────────────────────────────
    async def list_workbook_embeds(self, workbook_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/embeds")

    async def create_workbook_embed(self, workbook_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/workbooks/{workbook_id}/embeds", body)

    async def delete_workbook_embed(self, workbook_id: str, embed_id: str) -> int:
        return await self.delete(f"/v2/workbooks/{workbook_id}/embeds/{embed_id}")

    # ─── Workbook Exports ──────────────────────────────────────────────────
    async def export_workbook(self, workbook_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/workbooks/{workbook_id}/export", body)

    async def send_workbook(self, workbook_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/workbooks/{workbook_id}/send", body)

    # ─── Workbook Schedules ────────────────────────────────────────────────
    async def list_workbook_schedules(self, workbook_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/schedules")

    async def add_workbook_schedule(self, workbook_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/workbooks/{workbook_id}/schedules", body)

    async def update_workbook_schedule(self, workbook_id: str, schedule_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/workbooks/{workbook_id}/schedules/{schedule_id}", body)

    async def delete_workbook_schedule(self, workbook_id: str, schedule_id: str) -> int:
        return await self.delete(f"/v2/workbooks/{workbook_id}/schedules/{schedule_id}")

    # ─── Workbook Materializations ─────────────────────────────────────────
    async def materialize_workbook(self, workbook_id: str, body: dict[str, Any] | None = None) -> JSONValue:
        """Create a materialization job for a workbook."""
        return await self.post(f"/v2/workbooks/{workbook_id}/materializations", body or {})

    async def get_materialization_job(self, workbook_id: str, materialization_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/materializations/{materialization_id}")

    async def list_materialization_schedules(self, workbook_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/materialization-schedules")

    # ─── Workbook Bookmarks ────────────────────────────────────────────────
    async def list_workbook_bookmarks(self, workbook_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/bookmarks")

    async def add_workbook_bookmark(self, workbook_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/workbooks/{workbook_id}/bookmarks", body)

    async def update_workbook_bookmark(self, workbook_id: str, bookmark_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/workbooks/{workbook_id}/bookmarks/{bookmark_id}", body)

    async def delete_workbook_bookmark(self, workbook_id: str, bookmark_id: str) -> int:
        return await self.delete(f"/v2/workbooks/{workbook_id}/bookmarks/{bookmark_id}")

    # ─── Workbook Tags ─────────────────────────────────────────────────────
    async def get_workbook_tags(self, workbook_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/tags")

    async def tag_workbook(self, workbook_id: str, tag_name: str) -> JSONValue:
        """Apply a version tag to a workbook.

        POST /v2/workbooks/tag takes the tag NAME (not its ID) under the key
        "tag" — per the published spec, required fields are workbookId + tag.
        """
        return await self.post("/v2/workbooks/tag", {"workbookId": workbook_id, "tag": tag_name})

    async def remove_workbook_tag(self, workbook_id: str, tag_id: str) -> int:
        return await self.delete(f"/v2/workbooks/{workbook_id}/tags/{tag_id}")

    # ─── Templates ─────────────────────────────────────────────────────────
    async def list_templates(self, limit: int = 200) -> JSONValue:
        return await self.get("/v2/templates", {"limit": limit})

    async def get_template(self, template_id: str) -> JSONValue:
        return await self.get(f"/v2/templates/{template_id}")

    async def save_workbook_from_template(self, template_id: str, folder_id: str, name: str | None = None) -> JSONValue:
        body: dict[str, Any] = {"templateId": template_id, "folderId": folder_id}
        if name:
            body["name"] = name
        return await self.post("/v2/templates/save_workbook", body)

    async def save_template_from_workbook(self, workbook_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/workbooks/{workbook_id}/saveTemplate", body)

    async def swap_template_sources(self, template_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/templates/{template_id}/swapSources", body)

    # ─── Data Models ───────────────────────────────────────────────────────
    async def list_data_models(self, limit: int = 200) -> JSONValue:
        return await self.get("/v2/dataModels", {"limit": limit})

    async def get_data_model(self, data_model_id: str) -> JSONValue:
        return await self.get(f"/v2/dataModels/{data_model_id}")

    async def get_data_model_spec(self, data_model_id: str) -> JSONValue:
        return await self.get(f"/v2/dataModels/{data_model_id}/spec")

    async def create_data_model_spec(self, spec: dict[str, Any]) -> JSONValue:
        return await self.post("/v2/dataModels/spec", spec)

    async def update_data_model_spec(self, data_model_id: str, spec: dict[str, Any]) -> JSONValue:
        return await self.put(f"/v2/dataModels/{data_model_id}/spec", spec)

    async def list_data_model_elements(self, data_model_id: str) -> JSONValue:
        return await self.get(f"/v2/dataModels/{data_model_id}/elements", {"limit": 200})

    async def list_data_model_columns(self, data_model_id: str) -> JSONValue:
        return await self.get(f"/v2/dataModels/{data_model_id}/columns", {"limit": 500})

    async def list_data_model_sources(self, data_model_id: str) -> JSONValue:
        return await self.get(f"/v2/dataModels/{data_model_id}/sources")

    async def swap_data_model_sources(self, data_model_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/dataModels/{data_model_id}/swapSources", body)

    async def materialize_data_model(self, data_model_id: str, body: dict[str, Any] | None = None) -> JSONValue:
        """Trigger materialization for a data model."""
        return await self.post(f"/v2/dataModels/{data_model_id}:materialize", body or {})

    async def get_data_model_materialization(self, data_model_id: str, materialization_id: str) -> JSONValue:
        return await self.get(f"/v2/dataModels/{data_model_id}/materializations/{materialization_id}")

    async def list_data_model_materialization_schedules(self, data_model_id: str) -> JSONValue:
        return await self.get(f"/v2/dataModels/{data_model_id}/materializationSchedules")

    async def list_data_model_lineage(self, data_model_id: str) -> JSONValue:
        return await self.get(f"/v2/dataModels/{data_model_id}/lineage")

    async def tag_data_model(self, data_model_id: str, tag_name: str) -> JSONValue:
        """Apply a version tag to a data model.

        POST /v2/dataModels/tag takes the tag NAME under the key "tag".
        """
        return await self.post("/v2/dataModels/tag", {"dataModelId": data_model_id, "tag": tag_name})

    async def list_data_model_tags(self, data_model_id: str) -> JSONValue:
        return await self.get(f"/v2/dataModels/{data_model_id}/tags")

    # ─── Members ───────────────────────────────────────────────────────────
    async def list_members(self, limit: int = 200) -> JSONValue:
        return await self.get("/v2/members", {"limit": limit})

    async def get_member(self, member_id: str) -> JSONValue:
        return await self.get(f"/v2/members/{member_id}")

    async def create_member(self, body: dict[str, Any]) -> JSONValue:
        return await self.post("/v2/members", body)

    async def update_member(self, member_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/members/{member_id}", body)

    async def deactivate_member(self, member_id: str) -> int:
        """Deactivate a member (Sigma's soft-delete semantic via DELETE /v2/members/{memberId})."""
        return await self.delete(f"/v2/members/{member_id}")

    async def get_current_user(self) -> JSONValue:
        return await self.get("/v2/whoami")

    async def list_member_files(self, member_id: str, limit: int = 200) -> JSONValue:
        return await self.get(f"/v2/members/{member_id}/files", {"limit": limit})

    async def list_recent_files(self, member_id: str) -> JSONValue:
        return await self.get(f"/v2/members/{member_id}/files/recents")

    # ─── Teams ─────────────────────────────────────────────────────────────
    async def list_teams(self, limit: int = 200) -> JSONValue:
        return await self.get("/v2/teams", {"limit": limit})

    async def get_team(self, team_id: str) -> JSONValue:
        return await self.get(f"/v2/teams/{team_id}")

    async def create_team(self, body: dict[str, Any]) -> JSONValue:
        return await self.post("/v2/teams", body)

    async def update_team(self, team_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/teams/{team_id}", body)

    async def delete_team(self, team_id: str) -> int:
        return await self.delete(f"/v2/teams/{team_id}")

    async def list_team_members(self, team_id: str) -> JSONValue:
        return await self.get(f"/v2/teams/{team_id}/members")

    async def update_team_members(self, team_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/teams/{team_id}/members", body)

    # ─── Files / Folders ───────────────────────────────────────────────────
    async def list_files(self, params: dict[str, Any] | None = None) -> JSONValue:
        return await self.get("/v2/files", params)

    async def get_file(self, inode_id: str) -> JSONValue:
        return await self.get(f"/v2/files/{inode_id}")

    async def create_file(self, body: dict[str, Any]) -> JSONValue:
        return await self.post("/v2/files", body)

    async def update_file(self, inode_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/files/{inode_id}", body)

    # ─── Tags ──────────────────────────────────────────────────────────────
    async def list_tags(self) -> JSONValue:
        return await self.get("/v2/tags")

    async def create_tag(self, body: dict[str, Any]) -> JSONValue:
        return await self.post("/v2/tags", body)

    async def update_tag(self, tag_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/tags/{tag_id}", body)

    async def delete_tag(self, tag_id: str) -> int:
        return await self.delete(f"/v2/tags/{tag_id}")

    async def list_workbooks_for_tag(self, tag_id: str) -> JSONValue:
        return await self.get(f"/v2/tags/{tag_id}/workbooks")

    # ─── User Attributes ───────────────────────────────────────────────────
    async def list_user_attributes(self) -> JSONValue:
        return await self.get("/v2/user-attributes")

    async def get_user_attribute(self, attr_id: str) -> JSONValue:
        return await self.get(f"/v2/user-attributes/{attr_id}")

    async def create_user_attribute(self, body: dict[str, Any]) -> JSONValue:
        return await self.post("/v2/user-attributes", body)

    async def update_user_attribute(self, attr_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/user-attributes/{attr_id}", body)

    async def delete_user_attribute(self, attr_id: str) -> int:
        return await self.delete(f"/v2/user-attributes/{attr_id}")

    async def set_user_attribute_for_users(self, attr_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/user-attributes/{attr_id}/users", body)

    async def set_user_attribute_for_teams(self, attr_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/user-attributes/{attr_id}/teams", body)

    async def set_user_attribute_for_tenants(self, attr_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/user-attributes/{attr_id}/tenants", body)

    async def get_user_attribute_user_assignments(self, attr_id: str) -> JSONValue:
        return await self.get(f"/v2/user-attributes/{attr_id}/users")

    async def get_user_attribute_team_assignments(self, attr_id: str) -> JSONValue:
        return await self.get(f"/v2/user-attributes/{attr_id}/teams")

    async def get_user_attribute_tenant_assignments(self, attr_id: str) -> JSONValue:
        return await self.get(f"/v2/user-attributes/{attr_id}/tenants")

    async def update_user_attribute_for_users(self, attr_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/user-attributes/{attr_id}/users", body)

    async def update_user_attribute_for_teams(self, attr_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/user-attributes/{attr_id}/teams", body)

    async def update_user_attribute_for_tenants(self, attr_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/user-attributes/{attr_id}/tenants", body)

    async def delete_user_attribute_for_user(self, attr_id: str, user_id: str) -> int:
        return await self.delete(f"/v2/user-attributes/{attr_id}/users/{user_id}")

    async def delete_user_attribute_for_team(self, attr_id: str, team_id: str) -> int:
        return await self.delete(f"/v2/user-attributes/{attr_id}/teams/{team_id}")

    async def delete_user_attribute_for_tenant(self, attr_id: str, tenant_org_id: str) -> int:
        return await self.delete(f"/v2/user-attributes/{attr_id}/tenants/{tenant_org_id}")

    # ─── Account Types ─────────────────────────────────────────────────────
    async def list_account_types(self) -> JSONValue:
        return await self.get("/v2/accountTypes")

    async def create_account_type(self, body: dict[str, Any]) -> JSONValue:
        return await self.post("/v2/accountTypes", body)

    async def delete_account_type(self, account_type_id: str) -> int:
        return await self.delete(f"/v2/accountTypes/{account_type_id}")

    # ─── Workspaces ────────────────────────────────────────────────────────
    async def list_workspaces(self, limit: int = 200) -> JSONValue:
        return await self.get("/v2/workspaces", {"limit": limit})

    async def get_workspace(self, workspace_id: str) -> JSONValue:
        return await self.get(f"/v2/workspaces/{workspace_id}")

    async def create_workspace(self, body: dict[str, Any]) -> JSONValue:
        return await self.post("/v2/workspaces", body)

    async def update_workspace(self, workspace_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/workspaces/{workspace_id}", body)

    async def delete_workspace(self, workspace_id: str) -> int:
        return await self.delete(f"/v2/workspaces/{workspace_id}")

    async def list_workspace_grants(self, workspace_id: str) -> JSONValue:
        return await self.get(f"/v2/workspaces/{workspace_id}/grants")

    async def grant_workspace_access(self, workspace_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/workspaces/{workspace_id}/grants", body)

    async def delete_workspace_grant(self, workspace_id: str, grant_id: str) -> int:
        return await self.delete(f"/v2/workspaces/{workspace_id}/grants/{grant_id}")

    # ─── Favorites ─────────────────────────────────────────────────────────
    async def favorite(self, member_id: str, inode_id: str) -> JSONValue:
        """Add a file to a member's favorites."""
        return await self.post("/v2/favorites", {"memberId": member_id, "inodeId": inode_id})

    async def unfavorite(self, member_id: str, inode_id: str) -> int:
        """Remove a file from a member's favorites."""
        return await self.delete(f"/v2/favorites/member/{member_id}/file/{inode_id}")

    async def list_favorites_for_member(self, member_id: str) -> JSONValue:
        """List a member's favorited files."""
        return await self.get(f"/v2/favorites/member/{member_id}")

    # ─── Grants (generic) ─────────────────────────────────────────────────
    async def create_or_update_grant(self, body: dict[str, Any]) -> JSONValue:
        return await self.post("/v2/grants", body)

    async def get_grant(self, grant_id: str) -> JSONValue:
        return await self.get(f"/v2/grants/{grant_id}")

    async def delete_grant(self, grant_id: str) -> int:
        return await self.delete(f"/v2/grants/{grant_id}")

    async def list_grants(self, params: dict[str, Any] | None = None) -> JSONValue:
        return await self.get("/v2/grants", params)

    # ─── Reports ──────────────────────────────────────────────────────────
    async def list_reports(self, limit: int = 200) -> JSONValue:
        return await self.get("/v2/reports", {"limit": limit})

    async def get_report(self, report_id: str) -> JSONValue:
        return await self.get(f"/v2/reports/{report_id}")

    async def create_report(self, body: dict[str, Any]) -> JSONValue:
        return await self.post("/v2/reports", body)

    async def duplicate_report(self, report_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/reports/{report_id}/copy", body)

    async def duplicate_tagged_report(
        self, report_id: str, tag_name: str, body: dict[str, Any] | None = None
    ) -> JSONValue:
        return await self.post(f"/v2/reports/{report_id}/tag/{tag_name}/copy", body or {})

    async def list_report_sources(self, report_id: str) -> JSONValue:
        return await self.get(f"/v2/reports/{report_id}/sources")

    async def swap_report_sources(self, report_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/reports/{report_id}/swapSources", body)

    async def list_report_pages(self, report_id: str) -> JSONValue:
        return await self.get(f"/v2/reports/{report_id}/pages")

    async def list_report_page_elements(self, report_id: str, page_id: str) -> JSONValue:
        return await self.get(f"/v2/reports/{report_id}/pages/{page_id}/elements")

    async def list_report_elements(self, report_id: str) -> JSONValue:
        return await self.get(f"/v2/reports/{report_id}/elements", {"limit": 200})

    async def list_report_columns(self, report_id: str) -> JSONValue:
        return await self.get(f"/v2/reports/{report_id}/columns", {"limit": 500})

    async def get_report_element_query(self, report_id: str, element_id: str) -> JSONValue:
        return await self.get(f"/v2/reports/{report_id}/elements/{element_id}/query")

    async def list_report_queries(self, report_id: str) -> JSONValue:
        return await self.get(f"/v2/reports/{report_id}/queries", {"limit": 50})

    async def list_report_controls(self, report_id: str) -> JSONValue:
        return await self.get(f"/v2/reports/{report_id}/controls", {"limit": 50})

    async def list_report_lineage(self, report_id: str) -> JSONValue:
        return await self.get(f"/v2/reports/{report_id}/lineage")

    async def get_report_version_history(self, report_id: str) -> JSONValue:
        return await self.get(f"/v2/reports/{report_id}/version-history")

    async def export_report(self, report_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/reports/{report_id}/export", body)

    async def send_report(self, report_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/reports/{report_id}/send", body)

    async def list_report_schedules(self, report_id: str) -> JSONValue:
        return await self.get(f"/v2/reports/{report_id}/schedules")

    async def create_report_schedule(self, report_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/reports/{report_id}/schedules", body)

    async def update_report_schedule(self, report_id: str, schedule_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/reports/{report_id}/schedules/{schedule_id}", body)

    async def delete_report_schedule(self, report_id: str, schedule_id: str) -> int:
        return await self.delete(f"/v2/reports/{report_id}/schedules/{schedule_id}")

    async def create_report_grant(self, report_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/reports/{report_id}/grants", body)

    async def delete_report_grant(self, report_id: str, grant_id: str) -> int:
        return await self.delete(f"/v2/reports/{report_id}/grants/{grant_id}")

    async def list_report_tags(self, report_id: str) -> JSONValue:
        return await self.get(f"/v2/reports/{report_id}/tags")

    async def tag_report(self, report_id: str, tag_name: str) -> JSONValue:
        """Apply a version tag to a report.

        NOTE: reports differ from workbooks and data models — the spec requires
        the key "tagName" here, not "tag".
        """
        return await self.post("/v2/reports/tag", {"reportId": report_id, "tagName": tag_name})

    async def remove_report_tag(self, report_id: str, tag_id: str) -> int:
        return await self.delete(f"/v2/reports/{report_id}/tags/{tag_id}")

    async def convert_workbook_to_report(self, workbook_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/workbooks/{workbook_id}/convertToReport", body)

    # ─── Deployment Policies ──────────────────────────────────────────────
    async def list_deployments(self) -> JSONValue:
        return await self.get("/v2/deploymentPolicies")

    async def get_deployment(self, policy_id: str) -> JSONValue:
        return await self.get(f"/v2/deploymentPolicies/{policy_id}")

    async def create_deployment(self, body: dict[str, Any]) -> JSONValue:
        return await self.post("/v2/deploymentPolicies", body)

    async def update_deployment(self, policy_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/deploymentPolicies/{policy_id}", body)

    async def delete_deployment(self, policy_id: str) -> int:
        """Delete (archive) a deployment policy."""
        return await self.delete(f"/v2/deploymentPolicies/{policy_id}")

    async def list_deployment_documents(self, policy_id: str) -> JSONValue:
        return await self.get(f"/v2/deploymentPolicies/{policy_id}/files")

    async def add_deployment_documents(self, policy_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/deploymentPolicies/{policy_id}/files", body)

    async def remove_deployment_document(self, policy_id: str, inode_id: str) -> int:
        return await self.delete(f"/v2/deploymentPolicies/{policy_id}/files/{inode_id}")

    async def list_deployable_tenants(self) -> JSONValue:
        return await self.get("/v2/deploymentPolicies/tenants")

    async def list_deployment_tenants(self, policy_id: str) -> JSONValue:
        return await self.get(f"/v2/deploymentPolicies/{policy_id}/tenants")

    async def add_deployment_tenant(self, policy_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/deploymentPolicies/{policy_id}/tenants", body)

    async def remove_deployment_tenant(self, policy_id: str, tenant_org_id: str) -> int:
        return await self.delete(f"/v2/deploymentPolicies/{policy_id}/tenants/{tenant_org_id}")

    # ─── Tenants ──────────────────────────────────────────────────────────
    async def list_tenants(self) -> JSONValue:
        return await self.get("/v2/tenants")

    async def get_tenant(self, tenant_id: str) -> JSONValue:
        return await self.get(f"/v2/tenants/{tenant_id}")

    async def create_tenant(self, body: dict[str, Any]) -> JSONValue:
        return await self.post("/v2/tenants", body)

    async def update_tenant(self, tenant_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/tenants/{tenant_id}", body)

    async def delete_tenant(self, tenant_id: str) -> int:
        return await self.delete(f"/v2/tenants/{tenant_id}")

    async def list_tenant_deployment_capabilities(self, tenant_org_id: str) -> JSONValue:
        return await self.get(f"/v2/tenants/{tenant_org_id}/capabilities/deployments")

    async def batch_add_tenant_capabilities(self, tenant_org_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/tenants/{tenant_org_id}/capabilities/deployments:batchAdd", body)

    async def batch_remove_tenant_capabilities(self, tenant_org_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/tenants/{tenant_org_id}/capabilities/deployments:batchRemove", body)

    # ─── API Connectors ───────────────────────────────────────────────────
    async def list_api_connectors(self) -> JSONValue:
        return await self.get("/v2/api-connectors")

    async def get_api_connector(self, connector_id: str) -> JSONValue:
        return await self.get(f"/v2/api-connectors/{connector_id}")

    async def create_api_connector(self, body: dict[str, Any]) -> JSONValue:
        return await self.post("/v2/api-connectors", body)

    async def update_api_connector(self, connector_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/api-connectors/{connector_id}", body)

    async def delete_api_connector(self, connector_id: str) -> int:
        return await self.delete(f"/v2/api-connectors/{connector_id}")

    # ─── API Credentials ──────────────────────────────────────────────────
    async def list_api_credentials(self) -> JSONValue:
        return await self.get("/v2/api-credentials")

    async def get_api_credential(self, credential_id: str) -> JSONValue:
        return await self.get(f"/v2/api-credentials/{credential_id}")

    async def create_api_credential(self, body: dict[str, Any]) -> JSONValue:
        return await self.post("/v2/api-credentials", body)

    async def update_api_credential(self, credential_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/api-credentials/{credential_id}", body)

    async def delete_api_credential(self, credential_id: str) -> int:
        return await self.delete(f"/v2/api-credentials/{credential_id}")

    # ─── Source Swap Policies ─────────────────────────────────────────────
    async def list_source_swap_policies(self) -> JSONValue:
        return await self.get("/v2/sourceSwapPolicies")

    async def get_source_swap_policy(self, policy_id: str) -> JSONValue:
        return await self.get(f"/v2/sourceSwapPolicies/{policy_id}")

    async def create_source_swap_policy(self, body: dict[str, Any]) -> JSONValue:
        return await self.post("/v2/sourceSwapPolicies", body)

    async def update_source_swap_policy(self, policy_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.patch(f"/v2/sourceSwapPolicies/{policy_id}", body)

    async def delete_source_swap_policy(self, policy_id: str) -> int:
        return await self.delete(f"/v2/sourceSwapPolicies/{policy_id}")

    # ─── Shared Templates ─────────────────────────────────────────────────
    async def list_shared_templates(self) -> JSONValue:
        return await self.get("/v2/shared_templates/shared_with_you")

    async def accept_shared_template(self, share_id: str) -> JSONValue:
        """Accept a shared template. The shareId goes in the body."""
        return await self.post("/v2/shared_templates/accept", {"shareId": share_id})

    async def delete_shared_template(self, share_id: str) -> int:
        return await self.delete(f"/v2/shared_templates/{share_id}")

    # ─── Translations ─────────────────────────────────────────────────────
    async def list_translations(self) -> JSONValue:
        return await self.get("/v2/translations/organization")

    async def get_translation(self, lng: str) -> JSONValue:
        return await self.get(f"/v2/translations/organization/{lng}")

    async def get_translation_variant(self, lng: str, variant: str) -> JSONValue:
        return await self.get(f"/v2/translations/organization/{lng}/{variant}")

    async def create_translation(self, lng: str, body: dict[str, Any]) -> JSONValue:
        payload = {**body, "lng": lng}
        return await self.post("/v2/translations/organization", payload)

    async def update_translation(self, lng: str, body: dict[str, Any]) -> JSONValue:
        return await self.put(f"/v2/translations/organization/{lng}", body)

    async def update_translation_variant(self, lng: str, variant: str, body: dict[str, Any]) -> JSONValue:
        return await self.put(f"/v2/translations/organization/{lng}/{variant}", body)

    async def delete_translation(self, lng: str) -> int:
        return await self.delete(f"/v2/translations/organization/{lng}")

    async def delete_translation_variant(self, lng: str, variant: str) -> int:
        return await self.delete(f"/v2/translations/organization/{lng}/{variant}")

    # ─── Member extended ──────────────────────────────────────────────────
    async def list_member_teams(self, member_id: str) -> JSONValue:
        return await self.get(f"/v2/members/{member_id}/teams")

    async def list_member_schedules(self, member_id: str) -> JSONValue:
        return await self.get(f"/v2/members/{member_id}/schedules")

    async def revoke_member_tokens(self, member_id: str) -> JSONValue:
        return await self.post(f"/v2/members/{member_id}/revoke")

    # ─── Connection extended ──────────────────────────────────────────────
    async def add_dbt_metadata(self, connection_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/connections/{connection_id}/dbtArtifacts", body)

    # ─── Workbook extended ────────────────────────────────────────────────
    async def share_workbook_cross_org(self, workbook_id: str, body: dict[str, Any]) -> JSONValue:
        return await self.post(f"/v2/workbooks/{workbook_id}/shareCrossOrg", body)

    async def duplicate_tagged_workbook(
        self, workbook_id: str, tag_name: str, body: dict[str, Any] | None = None
    ) -> JSONValue:
        return await self.post(f"/v2/workbooks/{workbook_id}/tag/{tag_name}/copy", body or {})

    async def list_workbook_page_elements(self, workbook_id: str, page_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/pages/{page_id}/elements")

    async def get_element_query(self, workbook_id: str, element_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/elements/{element_id}/query")

    async def get_element_columns(self, workbook_id: str, element_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/elements/{element_id}/columns")

    async def list_element_lineage(self, workbook_id: str, element_id: str) -> JSONValue:
        return await self.get(f"/v2/workbooks/{workbook_id}/lineage/elements/{element_id}")

    # ─── Query/Download (A3: routed through _request for retry/rate-limit) ─
    async def download_query(self, query_id: str) -> bytes:
        r = await self.download_query_raw(query_id)
        return r.content

    # ─── Paginated list helpers (return complete result sets) ──────────────
    async def list_all_workbooks(self) -> list[dict[str, Any]]:
        return await self.auto_paginate("/v2/workbooks")

    async def list_all_members(self) -> list[dict[str, Any]]:
        return await self.auto_paginate("/v2/members")

    async def list_all_teams(self) -> list[dict[str, Any]]:
        return await self.auto_paginate("/v2/teams")

    async def list_all_files(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.auto_paginate("/v2/files", params)

    async def list_all_reports(self) -> list[dict[str, Any]]:
        return await self.auto_paginate("/v2/reports")

    async def list_all_data_models(self) -> list[dict[str, Any]]:
        return await self.auto_paginate("/v2/dataModels")

    # ─── Query/Download (raw response for polling) ────────────────────────
    async def download_query_raw(self, query_id: str) -> Response:
        """GET /v2/query/{queryId}/download returning the raw Response (for 204 vs 200 checking)."""
        return await self._request("GET", f"/v2/query/{query_id}/download", allow_statuses=frozenset({204}))

    # ─── Member search ────────────────────────────────────────────────────
    async def search_members(self, search: str, limit: int = 120) -> JSONValue:
        return await self.get("/v2/members", {"search": search, "limit": limit})
