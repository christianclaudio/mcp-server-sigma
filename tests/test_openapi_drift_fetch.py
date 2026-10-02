"""SSRF checks for the OpenAPI drift spec fetch."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from scripts.check_openapi_drift import _fetch_url, fetch_spec


def test_fetch_spec_rejects_loopback_without_request() -> None:
    with patch("scripts.check_openapi_drift.httpx.get") as mock_get:
        spec = fetch_spec(["http://127.0.0.1/openapi.json"])
    assert spec == {"paths": {}}
    mock_get.assert_not_called()


def test_fetch_url_refuses_redirect() -> None:
    redirected = httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})
    with patch("sigma_mcp.client._validate_hostname_dns", return_value="93.184.216.34"):
        with patch("scripts.check_openapi_drift.httpx.get", return_value=redirected) as mock_get:
            with pytest.raises(RuntimeError, match="Refusing to follow redirect"):
                _fetch_url("https://example.com/spec.json")
    assert mock_get.call_args.kwargs["follow_redirects"] is False


def test_fetch_spec_falls_back_after_redirect() -> None:
    redirected = httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})
    fallback_request = httpx.Request("GET", "https://help.sigmacomputing.com/openapi/sigma-rest-api.json")
    ok = httpx.Response(
        200,
        json={"paths": {"/v2/workbooks": {"get": {}}}},
        request=fallback_request,
    )
    urls = [
        "https://assets.sigmacomputing.com/spec.json",
        "https://help.sigmacomputing.com/openapi/sigma-rest-api.json",
    ]
    with patch("sigma_mcp.client._validate_hostname_dns", return_value="93.184.216.34"):
        with patch("scripts.check_openapi_drift.httpx.get", side_effect=[redirected, ok]) as mock_get:
            spec = fetch_spec(urls)
    assert "/v2/workbooks" in spec["paths"]
    assert all(call.kwargs["follow_redirects"] is False for call in mock_get.call_args_list)


def test_fetch_spec_metadata_literal_is_rejected() -> None:
    with patch("scripts.check_openapi_drift.httpx.get") as mock_get:
        spec = fetch_spec(["https://169.254.169.254/latest/meta-data"])
    assert spec == {"paths": {}}
    mock_get.assert_not_called()
