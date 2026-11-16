"""Session guard: CI must run the Code Mode tests, never skip them.

Live items stay skipped unless ``SIGMA_LIVE_TESTS=1`` and the required names are set.
"""

import importlib.util
import os

import pytest

# The floor test drives a child pytest with this plugin's pytester fixture.
pytest_plugins = ["pytester"]


def code_mode_ci_guard_error(environ: dict[str, str] | None = None) -> str | None:
    """Return an error when CI runs without the Code Mode sandbox, else None.

    Code Mode tests ``pytest.importorskip("pydantic_monty")`` so a local run without the
    ``code-mode`` extra still passes. In CI (``CI`` set, as on GitHub Actions) a missing
    sandbox would turn those tests into silent skips, so the session aborts instead.
    """
    env = os.environ if environ is None else environ
    if not env.get("CI"):
        return None
    if importlib.util.find_spec("pydantic_monty") is not None:
        return None
    return (
        "CI is set but pydantic_monty is not installed: install the code-mode extra "
        "(uv sync --locked --extra dev --extra code-mode) so Code Mode tests run, not skip."
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    """Abort a CI session that would silently skip the Code Mode tests."""
    error = code_mode_ci_guard_error()
    if error is not None:
        pytest.exit(error, returncode=3)


LIVE_REQUIRED_ENV = ("SIGMA_CLIENT_ID", "SIGMA_CLIENT_SECRET", "SIGMA_BASE_URL")


def live_skip_reason(environ: dict[str, str] | None = None) -> str | None:
    """Return why a live test must skip, or None when it may run.

    Live tests run only when ``SIGMA_LIVE_TESTS`` is ``1`` and every name in
    ``LIVE_REQUIRED_ENV`` is non-empty. The reason lists missing names and never a value.
    ``SIGMA_LIVE_TESTS`` is the same opt-in the integration suite already required.
    """
    env = os.environ if environ is None else environ
    if env.get("SIGMA_LIVE_TESTS") != "1":
        return "SIGMA_LIVE_TESTS is not 1"
    missing = [name for name in LIVE_REQUIRED_ENV if not env.get(name, "").strip()]
    if missing:
        return "missing or empty: " + ", ".join(missing)
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip live items unless the opt-in and the required names are set."""
    del config
    reason = live_skip_reason()
    if reason is None:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if item.get_closest_marker("live") is not None:
            item.add_marker(skip)
