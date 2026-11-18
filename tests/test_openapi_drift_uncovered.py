"""The uncovered-endpoint gate, settings expansion, and fallback skip."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
from scripts import check_openapi_drift as drift

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "drift_uncovered_baseline.txt"

_CLIENT = """
class SigmaClient:
    async def get_org_setting(self, setting_name):
        return await self.get(f"/v2/organizations/settings/{setting_name}")

    async def update_org_setting(self, setting_name, body):
        return await self.patch(f"/v2/organizations/settings/{setting_name}", body)

    async def other(self):
        return await self.get("/v2/covered")
"""


def _spec() -> dict[str, dict[str, dict[str, dict[str, object]]]]:
    from sigma_mcp.tools.admin import VALID_ORG_SETTINGS

    paths: dict[str, dict[str, dict[str, object]]] = {
        "/v2/organizations/settings/notARealSetting": {"get": {}},
        "/v2/covered": {"get": {}},
    }
    for name in VALID_ORG_SETTINGS:
        paths[f"/v2/organizations/settings/{name}"] = {"get": {}, "patch": {}}
    return {"paths": paths}


_SPEC = _spec()


def _write_client(tmp_path: Path) -> Path:
    path = tmp_path / "client.py"
    path.write_text(_CLIENT, encoding="utf-8")
    allow = tmp_path / "allow.txt"
    allow.write_text(
        "GET /v2/organizations/settings/{}  # template\nPATCH /v2/organizations/settings/{}  # template\n",
        encoding="utf-8",
    )
    return path


def test_committed_baseline_tags_are_valid() -> None:
    entries = drift.load_uncovered_baseline(BASELINE)
    assert entries is not None
    assert len(entries) == 24
    tags = [tag for _method, _path, tag, reason in entries]
    assert set(tags) <= {"deprecated", "excluded"}
    assert tags.count("deprecated") == 18
    assert tags.count("excluded") == 6
    assert "gap" not in tags
    assert all(reason for _m, _p, _t, reason in entries)


def test_settings_expansion_covers_smtp() -> None:
    from sigma_mcp.tools.admin import VALID_ORG_SETTINGS

    assert "smtp" in VALID_ORG_SETTINGS
    calls = {(c.method, c.normalized_path) for c in drift.extract_client_calls(drift.CLIENT_PATH)}
    expanded = drift.expand_org_settings(calls)
    assert ("GET", "/v2/organizations/settings/smtp") in expanded
    assert ("PATCH", "/v2/organizations/settings/smtp") in expanded
    assert ("GET", "/v2/organizations/settings/notARealSetting") not in expanded


def test_flag_fails_until_the_op_is_baselined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _write_client(tmp_path)
    baseline = tmp_path / "base.txt"
    baseline.write_text("", encoding="utf-8")
    monkeypatch.setattr(drift, "fetch_spec", lambda urls: (_SPEC, False))
    argv = [
        "--fail-on-new-uncovered",
        "--uncovered-baseline",
        str(baseline),
        "--client-path",
        str(client),
        "--allowlist",
        str(tmp_path / "allow.txt"),
    ]
    assert drift.main(argv) == 1
    failed = capsys.readouterr().out
    assert "❌ New uncovered endpoints: 1" in failed
    assert "GET /v2/organizations/settings/notARealSetting" in failed
    assert "GET /v2/organizations/settings/smtp" not in failed.split("❌ New uncovered")[-1]

    baseline.write_text(
        "GET /v2/organizations/settings/notARealSetting  # gap: waiting on a tool\n",
        encoding="utf-8",
    )
    assert drift.main(argv) == 0
    passed = capsys.readouterr().out
    assert "❌ New uncovered endpoints" not in passed


def test_without_the_flag_uncovered_stays_informational(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _write_client(tmp_path)
    monkeypatch.setattr(drift, "fetch_spec", lambda urls: (_SPEC, False))
    code = drift.main(["--client-path", str(client), "--allowlist", str(tmp_path / "allow.txt")])
    assert code == 0
    out = capsys.readouterr().out
    assert "uncovered" in out
    assert "❌ New uncovered endpoints" not in out
    assert "❌ Drift detected" not in out


def test_bad_baseline_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _write_client(tmp_path)
    monkeypatch.setattr(drift, "fetch_spec", lambda urls: (_SPEC, False))
    baseline = tmp_path / "base.txt"
    argv = [
        "--fail-on-new-uncovered",
        "--uncovered-baseline",
        str(baseline),
        "--client-path",
        str(client),
        "--allowlist",
        str(tmp_path / "allow.txt"),
    ]
    baseline.write_text("GET /v2/covered  # nope: not a real tag\n", encoding="utf-8")
    assert drift.main(argv) == 2
    baseline.write_text("GET /v2/covered  # gap:\n", encoding="utf-8")
    assert drift.main(argv) == 2
    baseline.write_text("GET /v2/covered  # gap: one\nGET /v2/covered  # gap: two\n", encoding="utf-8")
    assert drift.main(argv) == 2


def test_fallback_skips_both_checks_and_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_fetch(url: str) -> httpx.Response:
        if urlparse(url).hostname == "assets.sigmacomputing.com":
            raise RuntimeError("primary down")
        return httpx.Response(200, json={"paths": {"/only-in-fallback": {"put": {}}}})

    monkeypatch.setattr(drift, "_fetch_url", fake_fetch)
    spec, used_fallback = drift.fetch_spec(drift.SPEC_URLS)
    assert used_fallback is True
    assert "/only-in-fallback" in spec["paths"]
    assert drift.main(["--fail-on-new-uncovered"]) == 2
    out = capsys.readouterr().out
    assert drift._FALLBACK_WARNING in out
    assert "❌ Drift detected" not in out
    assert "❌ New uncovered endpoints" not in out
