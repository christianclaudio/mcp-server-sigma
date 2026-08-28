## What changed

<!-- One or two sentences. Focus on why, not just what. -->

## Checklist

- [ ] `mypy --strict src/` is clean
- [ ] `ruff check .` and `ruff format --check .` are clean
- [ ] `pytest tests/test_unit.py tests/test_tools_mocked.py tests/test_enterprise_assertion.py` passes **without a live Sigma org**
- [ ] `python scripts/check_openapi_drift.py` exits 0 (no wrong-path drift)
- [ ] If tools were added/removed: tool-count and annotation assertions updated
- [ ] If a tool writes or deletes: correct annotation (`destructiveHint`) and, where relevant, `dry_run`/`confirm` gating
- [ ] If behavior changed: README / `docs/` updated
- [ ] `CHANGELOG.md` updated
- [ ] No secrets, tokens, or real org IDs committed

## Verification

<!-- Paste the actual command output you ran. -->
