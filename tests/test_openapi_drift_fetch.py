"""SSRF checks for the OpenAPI drift spec fetch.

Every test runs under the fake network, so nothing here can open a socket.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from scripts.check_openapi_drift import _fetch_url, fetch_spec
from tests.fake_network import FakeNetwork, answer

from sigma_mcp.client import SSRFSafeAsyncTransport


@pytest.fixture(autouse=True)
def _offline() -> Iterator[FakeNetwork]:
    net = FakeNetwork()
    with net.patches():
        yield net
    assert net.attempts == []


def test_fetch_spec_rejects_loopback_without_request() -> None:
    with patch("sigma_mcp.client.SSRFSafeAsyncTransport.handle_async_request") as mock_handle:
        spec = fetch_spec(["http://127.0.0.1/openapi.json"])
    assert spec == {"paths": {}}
    mock_handle.assert_not_called()


def test_fetch_url_checks_the_host_and_refuses_redirect() -> None:
    redirected = httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})
    with patch("socket.getaddrinfo", return_value=answer("93.184.216.34")) as dns:
        with patch("scripts.check_openapi_drift.httpx.AsyncClient", wraps=httpx.AsyncClient) as client_cls:
            with patch.object(
                httpx.AsyncHTTPTransport, "handle_async_request", new_callable=AsyncMock, return_value=redirected
            ) as mock_handle:
                with pytest.raises(RuntimeError, match="Refusing to follow redirect"):
                    _fetch_url("https://example.com/spec.json")
    kwargs = client_cls.call_args.kwargs
    assert kwargs["follow_redirects"] is False
    assert kwargs["trust_env"] is False
    assert isinstance(kwargs["transport"], SSRFSafeAsyncTransport)
    dns.assert_called_once()
    assert dns.call_args.args[0] == "example.com"
    # The URL keeps the hostname; the transport, not a URL rewrite, picks the address.
    request = mock_handle.call_args.args[0]
    assert request.url.host == "example.com"
    assert "sni_hostname" not in request.extensions


def test_fetch_spec_falls_back_after_redirect() -> None:
    redirected = httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})
    ok = httpx.Response(200, json={"paths": {"/v2/workbooks": {"get": {}}}})
    urls = [
        "https://assets.sigmacomputing.com/spec.json",
        "https://help.sigmacomputing.com/openapi/sigma-rest-api.json",
    ]
    with patch("socket.getaddrinfo", return_value=answer("93.184.216.34")) as dns:
        with patch.object(
            httpx.AsyncHTTPTransport, "handle_async_request", new_callable=AsyncMock, side_effect=[redirected, ok]
        ) as mock_handle:
            spec = fetch_spec(urls)
    assert "/v2/workbooks" in spec["paths"]
    assert mock_handle.call_count == 2
    assert [c.args[0] for c in dns.call_args_list] == ["assets.sigmacomputing.com", "help.sigmacomputing.com"]
    assert [c.args[0].url.host for c in mock_handle.call_args_list] == [
        "assets.sigmacomputing.com",
        "help.sigmacomputing.com",
    ]


def test_fetch_spec_metadata_literal_is_rejected() -> None:
    with patch("sigma_mcp.client.SSRFSafeAsyncTransport.handle_async_request") as mock_handle:
        spec = fetch_spec(["https://169.254.169.254/latest/meta-data"])
    assert spec == {"paths": {}}
    mock_handle.assert_not_called()
