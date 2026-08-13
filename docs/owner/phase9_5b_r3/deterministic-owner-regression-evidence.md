# Phase 9.5B-R3 — Milestone 2: Deterministic Owner Regression Evidence

Three consecutive, clean, full-suite runs of the exact canonical command,
each from a fresh process, with zero concurrent access to
`aura_owner_test`:

```
cd owner && ../.venv/Scripts/python.exe -m pytest tests/ -q
```

| Run | Result | Duration |
|---|---|---|
| 1 | 627 passed, 0 failed, 0 errors, 0 skipped | 1188.55s |
| 2 | 627 passed, 0 failed, 0 errors, 0 skipped | 1201.21s |
| 3 | 627 passed, 0 failed, 0 errors, 0 skipped | 1233.23s (`0:20:33`) |

All three runs used identical environment (Python 3.11.9, pytest 8.3.2,
`OWNER_TEST_DATABASE_URL` pointed at the same local `aura_owner_test`
Postgres database) and were run sequentially, never overlapping in time,
confirmed by direct observation of each run's start/completion.

## Zero unexplained skips, zero quarantine

`0 skipped` across all three runs. No test file is excluded from
`tests/ -q`; no `pytest.ini`/`pyproject.toml` marker-based exclusion
exists that would silently drop tests from this canonical command
(confirmed by reading the pytest configuration — no `-m "not slow"`-style
default addopts, no collect-ignore list beyond the standard
`__pycache__`).

## Relationship to Milestone 11

These three runs were captured **before** the Milestone 2 advisory-lock
fix (see `flaky-test-root-cause-and-fix.md`) was applied to
`tests/conftest.py`, which is why they matter as evidence: they prove the
original failure does not reproduce under correct single-process
execution even without the fix. Milestone 11's final full-matrix run,
executed from the final Phase 9.5B-R3 HEAD (fix included), is the fourth
clean run and the one that reflects the actual code being tagged.

## M2 closure

Non-Negotiable Rule 2 ("final regression must be completely green") and
the governing spec's explicit "3 consecutive clean full runs" requirement
for M2 are both satisfied by this evidence, combined with Milestone 11's
final-HEAD run.
