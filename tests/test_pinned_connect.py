"""Connect pinning: every outbound request dials only an address that passed the check.

``SSRFSafeAsyncTransport`` resolves the host once per request, checks every address, and
hands the checked addresses to a network backend that dials only those, in resolver order.
The URL keeps the hostname, so the Host header, TLS SNI and certificate verification use
it, and httpcore pools connections per hostname. The fake network patches httpcore's
public backends, so no test opens a socket.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import patch

import httpcore
import httpx
import pytest
from scripts.check_openapi_drift import _fetch_url, fetch_spec
from tests.fake_network import FakeNetwork, answer

from sigma_mcp.client import SigmaClient, SSRFSafeAsyncTransport, _PinnedNetworkBackend, _PinRefusedError
from sigma_mcp.errors import SigmaAPIError

V6 = "2606:2800:21f:cb07:6820:80da:af6b:8b2c"
V4 = "93.184.216.34"
API_HOST = "api.sigmacomputing.com"


async def _send(transport: SSRFSafeAsyncTransport, request: httpx.Request) -> httpx.Response:
    response = await transport.handle_async_request(request)
    await response.aread()
    await response.aclose()
    return response


async def test_connects_to_the_checked_address_and_keeps_the_hostname() -> None:
    """One lookup; the connect goes to a checked address; Host and SNI stay on the hostname."""
    net = FakeNetwork()
    request = httpx.Request("POST", f"https://{API_HOST}:8443/v2/data?q=1", json={"a": 1})
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V6, V4)) as dns:
            response = await _send(transport, request)
    assert response.status_code == 200
    dns.assert_called_once()
    assert dns.call_args.args[0] == API_HOST
    assert net.connects == [(V6, 8443)]
    assert net.tls_server_names == [API_HOST]
    assert net.sent.startswith(b"POST /v2/data?q=1 HTTP/1.1\r\n")
    assert f"\r\nHost: {API_HOST}:8443\r\n".encode() in net.sent
    assert net.sent.endswith(b'{"a":1}')
    # The caller's request is left as it was: no URL rewrite, no sni_hostname.
    assert str(request.url) == f"https://{API_HOST}:8443/v2/data?q=1"
    assert "sni_hostname" not in request.extensions


async def test_sigma_client_end_to_end() -> None:
    """The API client's token and API calls are checked per request and share one connection."""
    net = FakeNetwork(body={"access_token": "tok", "expires_in": 3600, "ok": True})
    client = SigmaClient("id", "secret", f"https://{API_HOST}", max_retries=0, base_delay=0.001)
    try:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)) as dns:
            assert await client.get("/v2/whoami") == {"access_token": "tok", "expires_in": 3600, "ok": True}
    finally:
        await client.aclose()
    assert [c.args[0] for c in dns.call_args_list] == [API_HOST, API_HOST]
    assert net.connects == [(V4, 443)]
    assert net.tls_server_names == [API_HOST]
    # Both requests rode the one connection to the checked address.
    assert len(net.streams) == 1
    assert net.sent.startswith(b"POST /v2/auth/token HTTP/1.1\r\n")
    assert b"GET /v2/whoami HTTP/1.1\r\n" in net.sent
    assert f"\r\nHost: {API_HOST}\r\n".encode() in net.sent


def test_drift_fetch_end_to_end() -> None:
    """The drift spec fetch goes through the same pin: one lookup, a checked address, the hostname."""
    host = "assets.sigmacomputing.com"
    net = FakeNetwork(body={"paths": {"/v2/workbooks": {"get": {}}}})
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)) as dns:
        spec = fetch_spec([f"https://{host}/openapi/spec.json"])
    assert spec == {"paths": {"/v2/workbooks": {"get": {}}}}
    dns.assert_called_once()
    assert dns.call_args.args[0] == host
    assert net.connects == [(V4, 443)]
    assert net.tls_server_names == [host]
    assert net.sent.startswith(b"GET /openapi/spec.json HTTP/1.1\r\n")
    assert f"\r\nHost: {host}\r\n".encode() in net.sent


def test_drift_fetch_ignores_env_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    """An env proxy is never used: the fetch connects directly to the checked address."""
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.9:3128")
    monkeypatch.setenv("ALL_PROXY", "http://10.0.0.9:3128")
    net = FakeNetwork(body={"paths": {}})
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)):
        assert _fetch_url("https://assets.sigmacomputing.com/spec.json").json() == {"paths": {}}
    assert net.attempts == [(V4, 443)]


async def test_falls_back_to_the_next_checked_address() -> None:
    """A failed connect moves to the next checked address in resolver order, never further."""
    url = f"https://{API_HOST}/v2/data"
    net = FakeNetwork(fail={V6})
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V6, V4)) as dns:
            response = await _send(transport, httpx.Request("GET", url))
    assert response.status_code == 200
    dns.assert_called_once()
    assert net.attempts == [(V6, 443), (V4, 443)]
    assert net.connects == [(V4, 443)]
    assert net.tls_server_names == [API_HOST]

    # Every checked address failing is a connect error; nothing unchecked is tried.
    net = FakeNetwork(fail={V6, V4})
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V6, V4)) as dns:
            with pytest.raises(httpx.ConnectError, match=f"connect to {V4} failed"):
                await transport.handle_async_request(httpx.Request("GET", url))
    dns.assert_called_once()
    assert net.attempts == [(V6, 443), (V4, 443)]
    assert net.connects == []


async def test_pools_connections_per_hostname() -> None:
    """Two hostnames on one IP never share a TLS connection; one hostname reuses its own."""
    net = FakeNetwork()
    hosts = ("a.sigmacomputing.com", "b.sigmacomputing.com", "a.sigmacomputing.com")
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)) as dns:
            for host in hosts:
                await _send(transport, httpx.Request("GET", f"https://{host}/{host[0]}"))
    # The address is checked for every request, pooled or not.
    assert [c.args[0] for c in dns.call_args_list] == list(hosts)
    assert net.connects == [(V4, 443), (V4, 443)]
    assert net.tls_server_names == ["a.sigmacomputing.com", "b.sigmacomputing.com"]
    assert net.requests_by_connection() == [[b"GET /a HTTP/1.1", b"GET /a HTTP/1.1"], [b"GET /b HTTP/1.1"]]


async def test_checks_the_idna_name_it_dials() -> None:
    """An internationalized host is checked, pinned and dialed under its ASCII (IDNA) name."""
    ascii_host = "xn--fa-hia.sigmacomputing.com"
    net = FakeNetwork()
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)) as dns:
            await _send(transport, httpx.Request("GET", "https://fa\u00df.sigmacomputing.com/x"))
    dns.assert_called_once()
    assert dns.call_args.args[0] == ascii_host
    assert net.connects == [(V4, 443)]
    assert net.tls_server_names == [ascii_host]
    assert f"\r\nHost: {ascii_host}\r\n".encode() in net.sent


async def test_ip_literal_and_hostless_requests() -> None:
    """An IP literal is its own checked address; a hostless request goes straight to httpx."""
    net = FakeNetwork()
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches(), patch("socket.getaddrinfo") as dns:
            await _send(transport, httpx.Request("GET", f"https://{V4}/x"))
            with pytest.raises(httpx.UnsupportedProtocol):
                await transport.handle_async_request(httpx.Request("GET", "/relative"))
    dns.assert_not_called()
    assert net.connects == [(V4, 443)]
    assert net.tls_server_names == [V4]


@pytest.mark.parametrize("blocked", ["10.0.0.5", "198.18.0.1", "169.254.169.254"])
async def test_refuses_non_global_addresses_before_any_connect(blocked: str) -> None:
    """A non-global literal, or a name resolving to one, is refused and nothing is dialed."""
    net = FakeNetwork()
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches():
            with pytest.raises(SigmaAPIError, match="returned 400") as literal:
                await transport.handle_async_request(httpx.Request("GET", f"https://{blocked}/x"))
            with patch("socket.getaddrinfo", return_value=answer(V4, blocked)):
                with pytest.raises(SigmaAPIError) as resolved:
                    await transport.handle_async_request(httpx.Request("GET", f"https://{API_HOST}/x"))
    assert "SSRF validation blocked request" in str(literal.value.detail)
    assert f"resolving to private/reserved IP: {blocked}" in str(resolved.value.detail)
    assert net.attempts == []


async def test_refuses_a_host_with_no_checked_address() -> None:
    """With nothing checked for the host, the backend dials nothing and the request is refused."""
    net = FakeNetwork()
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches(), patch("sigma_mcp.client._validate_hostname_dns", return_value=()):
            with pytest.raises(SigmaAPIError) as exc:
                await transport.handle_async_request(httpx.Request("GET", f"https://{API_HOST}/x"))
    assert f"no checked address for {API_HOST}" in str(exc.value.detail)
    assert net.attempts == []

    # Outside a transport request nothing is pinned, so the backend dials nothing.
    backend = _PinnedNetworkBackend(httpcore.AsyncMockBackend([]))
    with pytest.raises(_PinRefusedError, match="no checked address"):
        await backend.connect_tcp(API_HOST, 443)
    await backend.sleep(0)


async def test_refuses_unix_sockets() -> None:
    """The pinned backend never dials a Unix socket."""
    backend = _PinnedNetworkBackend(httpcore.AsyncMockBackend([]))
    with pytest.raises(_PinRefusedError, match="unix sockets are not allowed"):
        await backend.connect_unix_socket("/var/run/docker.sock")


def test_builds_its_pool_through_public_httpcore_api() -> None:
    """Guard for dependency bumps: fail loudly if the pinned pool stops being used.

    ``network_backend`` is a documented ``httpcore.AsyncConnectionPool`` argument; httpx
    serves requests through the transport's ``_pool``. The connect tests above fail too if
    httpx stops doing that, but this names the cause.
    """
    assert "network_backend" in inspect.signature(httpcore.AsyncConnectionPool).parameters
    transport = SSRFSafeAsyncTransport(limits=httpx.Limits(max_connections=7))
    pool = vars(transport).get("_pool")
    assert isinstance(pool, httpcore.AsyncConnectionPool), (
        "httpx.AsyncHTTPTransport no longer serves requests through self._pool; "
        "SSRFSafeAsyncTransport must be rebuilt for this httpx version"
    )
    assert "_pool" in inspect.getsource(httpx.AsyncHTTPTransport.handle_async_request)
    backends: list[Any] = [v for v in vars(pool).values() if isinstance(v, _PinnedNetworkBackend)]
    assert len(backends) == 1
