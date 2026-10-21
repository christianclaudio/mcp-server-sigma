"""The shared redactor knows the API credential secret fields (Proxus ruling (a)).

``secretAccessKey`` (AWS SigV4) is a credential key everywhere, in structured payloads and in
free text. A bare ``value`` is the API key only inside an ``apiKey`` object (``{key, value,
isQueryParam}``); everywhere else in Sigma's API it is ordinary data (user attribute values,
control and filter values), so it is masked only in that shape. A ``SigmaAPIError`` with a
structured detail goes through the same key-based redaction before it reaches a tool result.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from sigma_mcp.errors import MASK, SigmaAPIError, redact_message, redact_payload, redact_secrets
from sigma_mcp.tools.common import sigma_tool

SAK = "wJalrXUtnFEMI-K7MDENG-bPxRfiCYEXAMPLEKEY"
API_KEY = "ak-0123456789-secret"


@pytest.mark.parametrize("key", ["secretAccessKey", "secret_access_key", "SECRET-ACCESS-KEY", "SecretAccessKey"])
def test_secret_access_key_masked_in_payload(key: str) -> None:
    out = redact_payload({"awsSigV4": {"accessKeyId": "AKIA1", key: SAK, "region": "us-east-1"}})
    assert out == {"awsSigV4": {"accessKeyId": "AKIA1", key: MASK, "region": "us-east-1"}}


@pytest.mark.parametrize(
    "text",
    [
        f"secretAccessKey={SAK}",
        f"secretAccessKey: {SAK}",
        f'{{"secretAccessKey": "{SAK}"}} trailing',
        f"{{'secretAccessKey': '{SAK}', 'region': 'us-east-1'}}",
        f'\\"secretAccessKey\\": \\"{SAK}\\"',
        f"AWS_SECRET_ACCESS_KEY={SAK}",
    ],
)
def test_secret_access_key_masked_in_text(text: str) -> None:
    for redact in (redact_secrets, redact_message):
        out = redact(text)
        assert SAK not in out
        assert MASK in out


def test_secret_access_key_json_message_keeps_shape() -> None:
    body = {"error": "bad", "credential": {"awsSigV4": {"accessKeyId": "AKIA1", "secretAccessKey": SAK}}}
    out = redact_message("HTTP 400: " + json.dumps(body))
    assert out.startswith("HTTP 400: ")
    parsed = json.loads(out.removeprefix("HTTP 400: "))
    assert parsed["credential"]["awsSigV4"] == {"accessKeyId": "AKIA1", "secretAccessKey": MASK}


def test_api_key_value_masked_in_api_key_object() -> None:
    # Echoed without its ``apiKey`` wrapper: the isQueryParam sibling marks the shape.
    inner = {"key": "X-Api-Key", "value": API_KEY, "isQueryParam": False}
    assert redact_payload(inner) == {"key": "X-Api-Key", "value": MASK, "isQueryParam": False}
    assert redact_payload([{"credential": inner}]) == [{"credential": {**inner, "value": MASK}}]
    # Under the wrapper the whole object is a credential key (``apiKey``) and is masked whole.
    assert redact_payload({"authMethod": "apiKey", "apiKey": inner}) == {"authMethod": "apiKey", "apiKey": MASK}
    out = redact_message("HTTP 400: " + json.dumps({"errors": [inner]}))
    assert API_KEY not in out
    assert json.loads(out.removeprefix("HTTP 400: ")) == {"errors": [{**inner, "value": MASK}]}


def test_api_key_value_none_stays() -> None:
    assert redact_payload({"key": "k", "value": None, "isQueryParam": True}) == {
        "key": "k",
        "value": None,
        "isQueryParam": True,
    }


@pytest.mark.parametrize(
    "payload",
    [
        # User attribute assignments and values.
        {"userAttributeId": "ua-1", "value": "Engineering"},
        [{"userId": "u-1", "value": "EMEA"}, {"teamId": "t-1", "value": "APAC"}],
        # Control and filter values, parameters.
        {"controlId": "c-1", "value": ["2026-01-01", "2026-12-31"]},
        {"filters": [{"column": "Region", "operator": "=", "value": "West"}]},
        {"name": "threshold", "value": 42},
        # A ``key``/``value`` pair that is not an apiKey object (no isQueryParam).
        {"key": "Region", "value": "West"},
    ],
)
def test_bare_value_not_redacted_elsewhere(payload: Any) -> None:
    assert redact_payload(payload) == payload
    text = json.dumps(payload)
    assert json.loads(redact_message(text)) == payload


def test_bare_value_text_not_redacted() -> None:
    text = "value=West and value: 42"
    assert redact_secrets(text) == text
    assert redact_message(text) == text


def test_api_error_structured_detail_redacted_by_key() -> None:
    detail = {
        "message": "invalid credential",
        "password": "pw-1",
        "credential": {
            "apiKey": {"key": "X", "value": API_KEY, "isQueryParam": False},
            "awsSigV4": {"accessKeyId": "AKIA1", "secretAccessKey": SAK},
        },
        "echo": {"key": "X", "value": API_KEY, "isQueryParam": True},
        "attribute": {"value": "Engineering"},
        "note": f"secretAccessKey={SAK}",
        "memberEmail": "a@example.com",
    }
    out = SigmaAPIError(400, "/v2/api-credentials", "POST", detail=detail).to_dict()["detail"]
    dumped = json.dumps(out)
    for secret in ("pw-1", API_KEY, SAK, "a@example.com"):
        assert secret not in dumped
    assert out["password"] == MASK
    assert out["credential"] == {"apiKey": MASK, "awsSigV4": {"accessKeyId": "AKIA1", "secretAccessKey": MASK}}
    assert out["echo"] == {"key": "X", "value": MASK, "isQueryParam": True}
    assert out["attribute"] == {"value": "Engineering"}
    assert out["note"] == f"secretAccessKey={MASK}"
    assert out["memberEmail"] == "[redacted]"
    assert out["message"] == "invalid credential"


def test_api_error_string_and_empty_detail_unchanged() -> None:
    assert SigmaAPIError(500, "/v2/x", "GET", detail="plain text").to_dict()["detail"] == "plain text"
    assert SigmaAPIError(500, "/v2/x", "GET").to_dict()["detail"] is None


async def test_sigma_tool_error_detail_redacted() -> None:
    @sigma_tool
    async def failing() -> str:
        raise SigmaAPIError(
            400,
            "/v2/api-credentials",
            "POST",
            detail={"errors": [{"secretAccessKey": SAK, "key": "X", "value": API_KEY, "isQueryParam": False}]},
        )

    with pytest.raises(ToolError) as info:
        await failing()
    err = json.loads(str(info.value))["error"]
    assert SAK not in str(info.value) and API_KEY not in str(info.value)
    assert err["detail"]["errors"][0] == {"secretAccessKey": MASK, "key": "X", "value": MASK, "isQueryParam": False}


@pytest.mark.parametrize("key", ["privateKeyPassphrase", "private_key_passphrase", "pythonSecrets", "python_secrets"])
def test_connection_secret_keys_masked(key: str) -> None:
    value: Any = {"setupUser": {"username": "u", "password": "pw"}} if "ython" in key else "pass-PHRASE-1"
    assert redact_payload({"details": {"type": "snowflake", key: value}}) == {
        "details": {"type": "snowflake", key: MASK}
    }
    text = f"{key}={json.dumps(value)}" if isinstance(value, dict) else f"{key}: {value}"
    out = redact_message(text)
    assert "pass-PHRASE-1" not in out and '"pw"' not in out
    assert MASK in out


def test_private_key_still_masked() -> None:
    assert redact_payload({"privateKey": "-----BEGIN"}) == {"privateKey": MASK}
