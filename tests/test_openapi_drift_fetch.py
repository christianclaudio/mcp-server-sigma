"""SSRF checks for the OpenAPI drift spec fetch."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from scripts import check_openapi_drift
from scripts.check_openapi_drift import _fetch_url, fetch_spec

from sigma_mcp.client import SSRFSafeTransport


def test_fetch_spec_rejects_loopback_without_request() -> None:
    with patch("sigma_mcp.client.SSRFSafeTransport.handle_request") as mock_handle:
        with pytest.raises(RuntimeError, match="no fallback URL"):
            fetch_spec(["http://127.0.0.1/openapi.json"])
    mock_handle.assert_not_called()


def test_fetch_url_pins_ip_and_refuses_redirect() -> None:
    redirected = httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})
    with patch("sigma_mcp.client._validate_hostname_dns", return_value="93.184.216.34"):
        with patch("scripts.check_openapi_drift.httpx.Client", wraps=httpx.Client) as client_cls:
            with patch("httpx.HTTPTransport.handle_request", return_value=redirected) as mock_handle:
                with pytest.raises(RuntimeError, match="Refusing to follow redirect"):
                    _fetch_url("https://example.com/spec.json")
    assert client_cls.call_args.kwargs["follow_redirects"] is False
    assert isinstance(client_cls.call_args.kwargs["transport"], SSRFSafeTransport)
    request = mock_handle.call_args.args[0]
    assert request.url.host == "93.184.216.34"
    assert request.headers["host"] == "example.com"
    assert request.extensions["sni_hostname"] == "example.com"


def test_fetch_spec_falls_back_after_redirect() -> None:
    redirected = httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})
    ok = httpx.Response(200, json={"paths": {"/v2/workbooks": {"get": {}}}})
    urls = [
        "https://assets.sigmacomputing.com/spec.json",
        "https://help.sigmacomputing.com/openapi/sigma-rest-api.json",
    ]
    with patch("sigma_mcp.client._validate_hostname_dns", return_value="93.184.216.34"):
        with patch("httpx.HTTPTransport.handle_request", side_effect=[redirected, ok]) as mock_handle:
            spec = fetch_spec(urls)
    assert "/v2/workbooks" in spec["paths"]
    assert mock_handle.call_count == 2
    for call in mock_handle.call_args_list:
        pinned = call.args[0]
        assert pinned.url.host == "93.184.216.34"
        assert pinned.extensions["sni_hostname"] in {
            "assets.sigmacomputing.com",
            "help.sigmacomputing.com",
        }


def test_fetch_spec_metadata_literal_is_rejected() -> None:
    with patch("sigma_mcp.client.SSRFSafeTransport.handle_request") as mock_handle:
        with pytest.raises(RuntimeError, match="no fallback URL"):
            fetch_spec(["https://169.254.169.254/latest/meta-data"])
    mock_handle.assert_not_called()


def test_main_exits_2_when_single_spec_url_override_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed single --spec-url override is a fetch failure (exit 2), not drift (exit 1)."""
    monkeypatch.setattr("sys.argv", ["check_openapi_drift.py", "--spec-url", "https://example.com/spec.json"])
    with patch.object(check_openapi_drift, "_fetch_url", side_effect=httpx.ConnectError("boom")):
        assert check_openapi_drift.main() == 2
    captured = capsys.readouterr()
    assert "Could not fetch spec" in captured.err
    assert "wrong path" not in captured.out


def test_main_exits_2_when_spec_has_no_endpoints(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty spec is never compared against the client."""
    monkeypatch.setattr("sys.argv", ["check_openapi_drift.py", "--spec-url", "https://example.com/spec.json"])
    with patch.object(check_openapi_drift, "fetch_spec", return_value={"paths": {}}):
        assert check_openapi_drift.main() == 2
    assert "refusing to compare against an empty spec" in capsys.readouterr().err
