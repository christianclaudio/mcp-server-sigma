"""Release workflow shape (template v1.6.0): provenance comes from a separate attest job."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"


def _jobs() -> dict[str, Any]:
    jobs: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    return jobs


def _steps_using(job: dict[str, Any], action: str) -> list[dict[str, Any]]:
    return [s for s in job.get("steps", []) if str(s.get("uses", "")).startswith(action + "@")]


def test_only_the_attest_job_can_write_attestations() -> None:
    jobs = _jobs()
    for name, job in jobs.items():
        perms = job.get("permissions", {})
        if name == "attest":
            assert perms == {"contents": "read", "id-token": "write", "attestations": "write"}
        else:
            assert "attestations" not in perms, name
            assert not _steps_using(job, "actions/attest-build-provenance"), name


def test_attest_job_covers_dist_and_image_after_publish() -> None:
    jobs = _jobs()
    attest = jobs["attest"]
    assert set(attest["needs"]) == {"build", "docker"}
    assert "publish" in jobs["docker"]["needs"]
    steps = _steps_using(attest, "actions/attest-build-provenance")
    withs = [s["with"] for s in steps]
    assert {"subject-path": "dist/*"} in withs
    assert {
        "subject-name": "${{ needs.docker.outputs.image }}",
        "subject-digest": "${{ needs.docker.outputs.digest }}",
    } in withs
    assert all("push-to-registry" not in w for w in withs)
    assert "attest" in jobs["registry"]["needs"]


def test_publish_keeps_trusted_publishing_and_drops_attestations() -> None:
    publish = _jobs()["publish"]
    assert publish["permissions"] == {"id-token": "write", "contents": "write"}
    assert _steps_using(publish, "pypa/gh-action-pypi-publish")


def test_docker_job_exposes_digest_and_pushes_a_plain_manifest() -> None:
    docker = _jobs()["docker"]
    assert docker["permissions"] == {"contents": "read", "packages": "write"}
    assert docker["outputs"]["digest"] == "${{ steps.build.outputs.digest }}"
    (build,) = _steps_using(docker, "docker/build-push-action")
    assert build["id"] == "build"
    assert build["with"]["provenance"] is False
    assert build["with"]["sbom"] is False


def test_every_action_is_pinned_to_a_commit_sha() -> None:
    for job in _jobs().values():
        for step in job.get("steps", []):
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
