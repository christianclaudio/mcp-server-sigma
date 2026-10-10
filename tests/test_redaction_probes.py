"""The gap-audit redaction probes (template v1.6.0 house rules): no part of a secret survives."""

from __future__ import annotations

import pytest

from sigma_mcp.errors import MASK, redact_message, redact_payload, redact_secrets


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("password=ab cd user=bob", f"password={MASK}"),
        ('{"token": "ab cd ef"}', f'{{"token": "{MASK}"}}'),
        ('"password": "a,b\\"c d" next', f'"password": "{MASK}" next'),
        ("password=x", f"password={MASK}"),
        ('"api_key":"SECRETVAL"', f'"api_key":"{MASK}"'),
        ("client_secret=ab cd", f"client_secret={MASK}"),
        ('"access_token": "ab cd"', f'"access_token": "{MASK}"'),
        ("subject_token=abcdefgh1234&x=1", f"subject_token={MASK}&x=1"),
        ('"subject_token": "ab cd"', f'"subject_token": "{MASK}"'),
        ("ghs_" + "a" * 36, MASK),
        ("eyJabc.eyJdef.ghi", MASK),
    ],
)
def test_probe_is_masked_whole(raw: str, expected: str) -> None:
    assert redact_secrets(raw) == expected


@pytest.mark.parametrize("kind", ["CERTIFICATE", "PUBLIC KEY", "OPENSSH PRIVATE KEY", "PGP MESSAGE"])
def test_any_pem_block_is_masked(kind: str) -> None:
    pem = f"cert: -----BEGIN {kind}-----\nMIIabc\ndef==\n-----END {kind}-----\ntrailer"
    out = redact_secrets(pem)
    assert "MIIabc" not in out and "def==" not in out
    assert out.endswith("trailer")


def test_short_secret_gets_the_fixed_mask() -> None:
    assert redact_secrets("password=x") == redact_secrets("password=" + "y" * 64)


def test_client_secret_env_and_extra_secret_are_replaced_literally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGMA_CLIENT_SECRET", "literal-env-secret")
    assert redact_secrets("got literal-env-secret back") == f"got {MASK} back"
    assert redact_secrets("raw custom-value here", extra_secret="custom-value") == f"raw {MASK} here"


def test_subject_token_key_masks_any_type_in_payloads() -> None:
    assert redact_payload({"subject_token": 123, "page_token": "keep"}) == {"subject_token": MASK, "page_token": "keep"}


def test_redact_message_keeps_json_valid() -> None:
    out = redact_message('HTTP 400: {"token": "ab cd ef", "password": "x"}')
    assert out == f'HTTP 400: {{"token": "{MASK}", "password": "{MASK}"}}'
