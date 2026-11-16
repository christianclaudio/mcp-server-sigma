"""The live marker stays off unless SIGMA_LIVE_TESTS=1 and credentials are set."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import (
    LIVE_REQUIRED_ENV,
    live_skip_reason,
    pytest_collection_modifyitems,
)

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

_OPEN = {
    "SIGMA_LIVE_TESTS": "1",
    "SIGMA_CLIENT_ID": "client-id",
    "SIGMA_CLIENT_SECRET": "super-secret",
    "SIGMA_BASE_URL": "https://api.sigmacomputing.com",
}


class _Item:
    """Minimal item: only the marker lookup and add_marker the hook uses."""

    def __init__(self, live: bool) -> None:
        self._live = live
        self.markers: list[Any] = []

    def get_closest_marker(self, name: str) -> object | None:
        if self._live and name == "live":
            return object()
        return None

    def add_marker(self, marker: Any) -> None:
        self.markers.append(marker)


def _reason(marker: Any) -> str:
    reason = marker.kwargs["reason"]
    assert isinstance(reason, str)
    return reason


def test_opt_in_absent() -> None:
    """No SIGMA_LIVE_TESTS skips, and the reason does not echo a credential."""
    env = {name: _OPEN[name] for name in LIVE_REQUIRED_ENV}
    reason = live_skip_reason(env)
    assert reason is not None
    assert "SIGMA_LIVE_TESTS" in reason
    assert "super-secret" not in reason


def test_missing_credential() -> None:
    """A missing required name is listed, and the value that is set is not."""
    env = {"SIGMA_LIVE_TESTS": "1", "SIGMA_CLIENT_SECRET": "super-secret"}
    reason = live_skip_reason(env)
    assert reason is not None
    assert "SIGMA_CLIENT_ID" in reason
    assert "SIGMA_BASE_URL" in reason
    assert "super-secret" not in reason


def test_empty_credential() -> None:
    """An empty or whitespace-only value counts as missing and is not echoed."""
    env = {
        "SIGMA_LIVE_TESTS": "1",
        "SIGMA_CLIENT_ID": "   ",
        "SIGMA_CLIENT_SECRET": "super-secret",
        "SIGMA_BASE_URL": "https://api.sigmacomputing.com",
    }
    reason = live_skip_reason(env)
    assert reason is not None
    assert "SIGMA_CLIENT_ID" in reason
    assert "super-secret" not in reason
    assert "   " not in reason


def test_everything_present() -> None:
    """Opt-in and every required name may run."""
    assert live_skip_reason(_OPEN) is None


def test_default_addopts_deselect_live() -> None:
    """The default run deselects live, and the e2e marker is gone."""
    ini = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
    assert "-m 'not live'" in ini["addopts"]
    assert "not e2e" not in ini["addopts"]
    markers = list(ini["markers"])
    assert markers == [
        "live: needs a real Sigma org; opt in with SIGMA_LIVE_TESTS=1 plus credentials",
    ]


def test_hook_skips_live_items_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The collection hook skips live items and leaves every other item alone."""
    monkeypatch.delenv("SIGMA_LIVE_TESTS", raising=False)
    monkeypatch.setenv("SIGMA_CLIENT_SECRET", "super-secret")
    live = _Item(True)
    other = _Item(False)
    pytest_collection_modifyitems(None, [live, other])  # type: ignore[arg-type]
    assert other.markers == []
    assert len(live.markers) == 1
    reason = _reason(live.markers[0])
    assert "super-secret" not in reason


def test_hook_leaves_live_items_when_the_gate_is_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the pure gate returns None, the hook adds no skip."""
    monkeypatch.setattr("tests.conftest.live_skip_reason", lambda environ=None: None)
    live = _Item(True)
    pytest_collection_modifyitems(None, [live])  # type: ignore[arg-type]
    assert live.markers == []
