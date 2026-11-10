"""docker/build-backend.txt matches the build-backend dependency group in uv.lock.

The group in pyproject.toml holds exact hatchling and uv-dynamic-versioning pins, uv.lock carries
their closure with hashes, and scripts/gen_build_backend.py writes the Docker requirements file
from the lock. These tests fail if the file drifts from the lock or the pins leave the
[build-system] ranges.
"""

from __future__ import annotations

import tomllib
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version
from scripts import gen_build_backend

HASH = "0" * 64


def test_build_backend_file_matches_uv_lock() -> None:
    assert gen_build_backend.check() == []


def test_build_backend_file_is_rendered_from_uv_lock() -> None:
    text = gen_build_backend.TARGET.read_text(encoding="utf-8")
    assert text == gen_build_backend.render(gen_build_backend.expected())


def test_group_pins_are_exact_and_inside_build_system_ranges() -> None:
    pyproject = tomllib.loads((gen_build_backend.ROOT / "pyproject.toml").read_text("utf-8"))
    group = [Requirement(item) for item in pyproject["dependency-groups"]["build-backend"]]
    required = {canonicalize_name(req.name): req for req in map(Requirement, pyproject["build-system"]["requires"])}
    assert {canonicalize_name(req.name) for req in group} == set(required)
    pins = gen_build_backend.expected()
    for req in group:
        (spec,) = req.specifier
        assert spec.operator == "==", req
        name = canonicalize_name(req.name)
        assert Version(spec.version) in required[name].specifier, (req, required[name])
        assert pins[name][0] == spec.version, (name, pins[name][0])


def test_base_python_reads_the_dockerfile() -> None:
    assert gen_build_backend.base_python("FROM python:3.14-slim@sha256:abc AS builder\n") == "3.14"
    assert gen_build_backend.base_python("FROM python:3.12-slim-trixie@sha256:abc AS builder\n") == "3.12"
    with pytest.raises(ValueError, match="one python"):
        gen_build_backend.base_python("FROM python:3.13-slim\nFROM python:3.14-slim\n")


def test_image_glibc_reads_the_debian_release() -> None:
    pinned = "FROM python:3.12-slim-trixie@sha256:" + HASH + " AS builder\n"
    pinned += "FROM python:3.12-slim-trixie@sha256:" + HASH + " AS runtime\n"
    assert gen_build_backend.image_glibc(pinned) == (2, 41)
    bookworm = "FROM python:3.12-slim-bookworm@sha256:" + HASH + " AS builder\n"
    assert gen_build_backend.image_glibc(bookworm) == (2, 36)


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("", "pinned python"),
        ("FROM python:3.12-slim-trixie AS builder\n", "not pinned"),
        ("FROM python:3.12-slim AS builder\n", "not pinned"),
        ("FROM python:3.12-slim-bullseye AS builder\n", "not pinned"),
        ("FROM python:3.12-slim@sha256:" + HASH + " AS builder\n", "no Debian release"),
        (
            "FROM python:3.12-slim-trixie@sha256:" + HASH + " AS builder\n"
            "FROM python:3.12-slim-bookworm@sha256:" + HASH + " AS runtime\n",
            "mixed Debian releases",
        ),
        ("FROM python:3.12-slim-bullseye@sha256:" + HASH + " AS builder\n", "unknown Debian release"),
        (
            "FROM python:3.12-slim-bullseye@sha256:" + HASH + " AS builder\n"
            "FROM python:3.12-slim-bullseye@sha256:" + HASH + " AS runtime\n",
            "unknown Debian release",
        ),
    ],
)
def test_image_glibc_rejects_a_base_it_cannot_key(text: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        gen_build_backend.image_glibc(text)


@pytest.mark.parametrize(
    "text",
    [
        "hatchling==1.33.0\n",
        "hatchling>=1.33.0 --hash=sha256:" + HASH + "\n",
        "hatchling==1.33.0 --hash=sha256:abc\n",
        "-r other.txt\n",
    ],
)
def test_parse_rejects_lines_that_are_not_exact_and_hashed(text: str) -> None:
    with pytest.raises(ValueError, match="not an exact"):
        gen_build_backend.parse(text)


def test_parse_rejects_a_repeated_name() -> None:
    line = "--hash=sha256:" + HASH
    text = f"MarkupSafe==3.0.4 {line}\nmarkupsafe==3.0.4 \\\n    {line}\n"
    with pytest.raises(ValueError, match="more than once"):
        gen_build_backend.parse(text)


def test_render_round_trips_through_parse() -> None:
    pins = {"hatchling": ("1.33.0", [HASH]), "markupsafe": ("3.0.4", ["1" * 64, HASH])}
    text = gen_build_backend.render(pins)
    assert gen_build_backend.parse(text) == {
        "hatchling": ("1.33.0", [HASH]),
        "markupsafe": ("3.0.4", [HASH, "1" * 64]),
    }


def _forked_lock(first: str, second: str) -> dict[str, Any]:
    """A lock with a forked package inside the build-backend closure and one outside it."""
    root = {"name": "demo", "version": "1.0.0", "source": {"editable": "."}}
    root["dev-dependencies"] = {"build-backend": [{"name": "hatchling"}]}
    dependencies = [
        {"name": "shared", "version": "1.0", "marker": first},
        {"name": "shared", "version": "2.0", "marker": second},
    ]
    backend = {"name": "hatchling", "version": "1.33.0", "dependencies": dependencies}
    shared = [{"name": "shared", "version": version} for version in ("1.0", "2.0")]
    # Two entries for a name outside the closure, as uv writes for a marker fork.
    outside = [{"name": "beartype", "version": version} for version in ("0.22.9", "0.23.0")]
    return {"package": [root, backend, *shared, *outside]}


def test_closure_picks_the_locked_version_of_a_forked_package() -> None:
    lock = _forked_lock('python_version < "3.15"', 'python_version >= "3.15"')
    closure = gen_build_backend._closure(lock, "3.14")
    assert [(p["name"], p["version"]) for p in closure] == [
        ("hatchling", "1.33.0"),
        ("shared", "1.0"),
    ]


def test_closure_rejects_two_versions_of_one_package() -> None:
    lock = _forked_lock('platform_machine == "x86_64"', 'platform_machine == "aarch64"')
    with pytest.raises(ValueError, match="more than one version"):
        gen_build_backend._closure(lock, "3.14")


def _wheel(filename: str, digest: str) -> dict[str, str]:
    return {"url": f"https://files.example/{filename}", "hash": f"sha256:{digest}"}


def test_digests_picks_one_native_wheel_per_arch() -> None:
    x86_64 = "1" * 64
    aarch64 = "2" * 64
    package = {
        "name": "markupsafe",
        "wheels": [
            _wheel("markupsafe-3.0.4-cp313-cp313-manylinux2014_x86_64.whl", "3" * 64),
            _wheel("markupsafe-3.0.4-cp312-cp312-manylinux2014_x86_64.whl", x86_64),
            _wheel("markupsafe-3.0.4-cp312-cp312-manylinux2014_aarch64.whl", aarch64),
            _wheel("markupsafe-3.0.4-cp312-cp312-musllinux_1_2_x86_64.whl", "4" * 64),
            _wheel("markupsafe-3.0.4-cp312-cp312-win_amd64.whl", "5" * 64),
        ],
    }
    assert gen_build_backend._digests(package, "3.12", (2, 41)) == [x86_64, aarch64]


def test_digests_prefers_the_py3_none_any_wheel() -> None:
    universal = "6" * 64
    package = {"name": "pluggy", "wheels": [_wheel("pluggy-1.6.0-py3-none-any.whl", universal)]}
    assert gen_build_backend._digests(package, "3.12", (2, 41)) == [universal]


def test_digests_rejects_a_missing_arch() -> None:
    wheel = _wheel("markupsafe-3.0.4-cp312-cp312-manylinux2014_x86_64.whl", "1" * 64)
    with pytest.raises(ValueError, match="no single cp312 wheel per arch"):
        gen_build_backend._digests({"name": "markupsafe", "wheels": [wheel]}, "3.12", (2, 41))


def test_digests_keeps_a_wheel_at_the_image_glibc_floor() -> None:
    x86_64 = "1" * 64
    aarch64 = "2" * 64
    package = {
        "name": "markupsafe",
        "wheels": [
            _wheel("markupsafe-3.0.4-cp312-cp312-manylinux_2_36_x86_64.whl", x86_64),
            _wheel("markupsafe-3.0.4-cp312-cp312-manylinux_2_36_aarch64.whl", aarch64),
        ],
    }
    assert gen_build_backend._digests(package, "3.12", (2, 36)) == [x86_64, aarch64]


def test_digests_skips_a_manylinux_floor_above_the_image_glibc() -> None:
    """Bookworm is glibc 2.36: manylinux_2_28 loads, manylinux_2_39 and musllinux do not."""
    old_x86_64 = "1" * 64
    old_aarch64 = "2" * 64
    package = {
        "name": "markupsafe",
        "wheels": [
            _wheel("markupsafe-3.0.4-cp312-cp312-manylinux_2_28_x86_64.whl", old_x86_64),
            _wheel("markupsafe-3.0.4-cp312-cp312-manylinux_2_39_x86_64.whl", "9" * 64),
            _wheel("markupsafe-3.0.4-cp312-cp312-manylinux_2_28_aarch64.whl", old_aarch64),
            _wheel("markupsafe-3.0.4-cp312-cp312-manylinux_2_39_aarch64.whl", "8" * 64),
            _wheel("markupsafe-3.0.4-cp312-cp312-musllinux_1_2_x86_64.whl", "4" * 64),
            _wheel("markupsafe-3.0.4-cp312-cp312-musllinux_1_2_aarch64.whl", "5" * 64),
        ],
    }
    assert gen_build_backend._digests(package, "3.12", (2, 36)) == [old_x86_64, old_aarch64]


def test_digests_accepts_legacy_manylinux_aliases() -> None:
    """manylinux1 = 2.5, manylinux2010 = 2.12, and manylinux2014 = 2.17."""
    x86_64 = "1" * 64
    aarch64 = "2" * 64
    aliases = {
        "name": "markupsafe",
        "wheels": [
            _wheel("markupsafe-3.0.4-cp312-cp312-manylinux1_x86_64.whl", x86_64),
            _wheel("markupsafe-3.0.4-cp312-cp312-manylinux2010_aarch64.whl", aarch64),
        ],
    }
    assert gen_build_backend._digests(aliases, "3.12", (2, 12)) == [x86_64, aarch64]
    perennial = {
        "name": "markupsafe",
        "wheels": [
            _wheel("markupsafe-3.0.4-cp312-cp312-manylinux2014_x86_64.whl", x86_64),
            _wheel("markupsafe-3.0.4-cp312-cp312-manylinux_2_17_aarch64.whl", aarch64),
        ],
    }
    assert gen_build_backend._digests(perennial, "3.12", (2, 17)) == [x86_64, aarch64]


def test_digests_uses_the_lowest_manylinux_tag_on_a_wheel() -> None:
    """A dotted tag loads when any manylinux tag on it fits, including a legacy alias."""
    x86_64 = "a" * 64
    aarch64 = "b" * 64
    name = "markupsafe-3.0.4-cp312-cp312-manylinux_2_39_x86_64.manylinux2014_x86_64.whl"
    package = {
        "name": "markupsafe",
        "wheels": [
            _wheel(name, x86_64),
            _wheel("markupsafe-3.0.4-cp312-cp312-manylinux2014_aarch64.whl", aarch64),
        ],
    }
    assert gen_build_backend._digests(package, "3.12", (2, 17)) == [x86_64, aarch64]


def test_digests_rejects_musllinux() -> None:
    package = {
        "name": "markupsafe",
        "wheels": [
            _wheel("markupsafe-3.0.4-cp312-cp312-musllinux_1_1_x86_64.whl", "1" * 64),
            _wheel("markupsafe-3.0.4-cp312-cp312-musllinux_1_1_aarch64.whl", "2" * 64),
        ],
    }
    with pytest.raises(ValueError, match="no single cp312 wheel per arch"):
        gen_build_backend._digests(package, "3.12", (2, 41))
