"""add_dbt_metadata sends the documented multipart/form-data upload (issue #56).

Sigma's "Add dbt metadata for a connection" endpoint
(https://help.sigmacomputing.com/reference/post-connection-dbt-artifacts) takes
``multipart/form-data`` with one required file field, ``artifacts`` (a tar.gz of the dbt
``target`` directory), and answers 201. Every request here goes to an in-process
``httpx.MockTransport``; nothing leaves the process.
"""

from __future__ import annotations

import gzip
import io
import json
import tarfile
import time

import httpx
import pytest

from sigma_mcp.client import SigmaClient
from sigma_mcp.errors import SigmaAPIError


def _tar_gz() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b'{"metadata": {"dbt_version": "1.8.0"}}'
        info = tarfile.TarInfo("target/manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _client(handler: httpx.MockTransport) -> SigmaClient:
    c = SigmaClient("cid", "csecret", "https://api.sigmacomputing.com", max_retries=1, base_delay=0.0)
    c._token = "tok"
    c._token_expiry = time.time() + 3600
    c._http = httpx.AsyncClient(transport=handler)
    return c


def _multipart_parts(req: httpx.Request) -> dict[str, tuple[dict[str, str], bytes]]:
    ctype = req.headers["content-type"]
    assert ctype.startswith("multipart/form-data; boundary=")
    boundary = ctype.split("boundary=", 1)[1].encode()
    body = req.content
    parts: dict[str, tuple[dict[str, str], bytes]] = {}
    for chunk in body.split(b"--" + boundary)[1:]:
        if chunk.startswith(b"--"):
            break
        head, _, payload = chunk.removeprefix(b"\r\n").partition(b"\r\n\r\n")
        headers = {}
        for line in head.decode().split("\r\n"):
            k, _, v = line.partition(": ")
            headers[k.lower()] = v
        name = headers["content-disposition"].split('name="', 1)[1].split('"', 1)[0]
        parts[name] = (headers, payload.removesuffix(b"\r\n"))
    return parts


async def test_add_dbt_metadata_posts_multipart_artifacts_file() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(201, json={})

    archive = _tar_gz()
    c = _client(httpx.MockTransport(handler))
    out = await c.add_dbt_metadata("conn-1", archive)
    assert out == {}

    (req,) = seen
    assert req.method == "POST"
    assert str(req.url) == "https://api.sigmacomputing.com/v2/connections/conn-1/dbtArtifacts"
    assert req.headers["authorization"] == "Bearer tok"
    assert req.headers["user-agent"].startswith("mcp-server-sigma/")
    assert "application/json" not in req.headers["content-type"]
    parts = _multipart_parts(req)
    assert list(parts) == ["artifacts"]
    headers, payload = parts["artifacts"]
    assert 'filename="target.tar.gz"' in headers["content-disposition"]
    assert headers["content-type"] == "application/gzip"
    assert payload == archive
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(payload)), mode="r:") as tar:
        assert tar.getnames() == ["target/manifest.json"]


async def test_add_dbt_metadata_custom_filename_and_path_encoding() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(201)

    c = _client(httpx.MockTransport(handler))
    assert await c.add_dbt_metadata("conn 1..x", b"\x1f\x8bdata", filename="run-42.tar.gz") is None
    (req,) = seen
    assert req.url.raw_path == b"/v2/connections/conn%201%2E%2Ex/dbtArtifacts"
    headers, payload = _multipart_parts(req)["artifacts"]
    assert 'filename="run-42.tar.gz"' in headers["content-disposition"]
    assert payload == b"\x1f\x8bdata"


async def test_multipart_upload_is_resent_intact_after_429() -> None:
    bodies: list[bytes] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(req.content)
        if len(bodies) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(201, json={})

    archive = _tar_gz()
    c = _client(httpx.MockTransport(handler))
    assert await c.add_dbt_metadata("conn-1", archive) == {}
    assert len(bodies) == 2
    assert archive in bodies[0] and archive in bodies[1]


async def test_add_dbt_metadata_error_status_raises() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(415, json={"message": "unsupported"})

    c = _client(httpx.MockTransport(handler))
    with pytest.raises(SigmaAPIError) as exc:
        await c.add_dbt_metadata("conn-1", _tar_gz())
    assert exc.value.status_code == 415


async def test_streamed_multipart_request_has_no_json_content_type() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(201, json={"ok": True})

    c = _client(httpx.MockTransport(handler))
    r = await c._request(
        "POST",
        "/v2/connections/conn-1/dbtArtifacts",
        files={"artifacts": ("target.tar.gz", b"abc", "application/gzip")},
        max_bytes=1024,
    )
    assert r.status_code == 201
    assert seen[0].headers["content-type"].startswith("multipart/form-data; boundary=")
    assert _multipart_parts(seen[0])["artifacts"][1] == b"abc"


async def test_request_refuses_json_and_files_together() -> None:
    def handler(req: httpx.Request) -> httpx.Response:  # pragma: no cover - never reached
        raise AssertionError("no request expected")

    c = _client(httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="json_data or files"):
        await c._request("POST", "/v2/x", json_data={"a": 1}, files={"f": ("f", b"x", "application/gzip")})


@pytest.mark.parametrize("max_bytes", [None, 1024])
async def test_json_requests_still_send_application_json(max_bytes: int | None) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"ok": True})

    c = _client(httpx.MockTransport(handler))
    await c._request("POST", "/v2/workbooks", json_data={"name": "N"}, max_bytes=max_bytes)
    assert seen[0].headers["content-type"] == "application/json"
    assert json.loads(seen[0].content) == {"name": "N"}
    assert await c.post("/v2/workbooks", {"name": "M"}) == {"ok": True}
    assert seen[1].headers["content-type"] == "application/json"
    assert json.loads(seen[1].content) == {"name": "M"}
