# Phase 9 Milestone 2 — Retail Test-Suite Isolation: Root Cause

## Reproduction (clean process, this session)

Command: `.venv/Scripts/python.exe -m pytest products/retail/tests -q --no-header`
Python: 3.11 (repo `.venv`).
Result: **73 failed, 11 errors, 110 passed** (194 total).

Reproduced identically:
- With the Phase 8V-P9 `commercial_runtime` fix fully reverted via `git stash` (ruling out that fix as
  the cause).
- With every real background Windows/proxy process this repo's Phase 8V-P9 session had started fully
  stopped first (ruling out port/resource contention).
- Per-file (`pytest <single file>`) and per-file-alone-in-directory runs: **all 12 files pass
  individually**, summing to the exact canonical 194/194.

## Actual root cause (found in the repository's own prior engineering record, not new)

`products/run_all_tests.py`'s own module docstring already documents this exact defect, from an
earlier phase ("Wave 1B, AUDIT-010"):

> Root cause of the cross-file pytest pollution this replaces: `registry_db.py`'s `DB_PATH`,
> `schema.py`'s `BASE_DIR`/`SUBSYS_DIR`, and `config.py`'s `DATABASE_DIR` are all computed once at
> module import time from the `AURA_APP_DATA` environment variable. Each test file sets a fresh temp
> directory via `os.environ.update(AURA_APP_DATA=...)` before importing `app.py` — but Python caches
> imported modules in `sys.modules` for the life of the process, so only the FIRST test file's import
> of these modules actually takes effect; every later file's env var change is silently ignored, and
> when the first file's `teardown_module()` deletes ITS temp directory, every other file still points
> at that now-deleted path and fails with "no such table: users".

Confirmed directly in `products/retail/tests/wave1c_financial_gate_test.py` (and the same pattern in
every other Retail test file): at **module import time** (not inside a fixture), each file does:

```python
DATA = Path(tempfile.mkdtemp(prefix="aura_retail_wave1c_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
```

then imports the product's `app.py`, whose `config.py`/`registry_db.py`/`schema.py` resolve
`AURA_APP_DATA` into module-level constants **once**, at first import. When pytest collects the whole
`products/retail/tests/` directory into one process, only the first file's temp directory actually
takes effect for the shared `app`/`config`/`registry_db` modules already sitting in `sys.modules`;
every later file runs against the first file's (soon-to-be-deleted) database.

## Why this is not a production defect

A real deployed process (the Windows executable, the embedded Android backend, and — as of Phase 9 —
the staging Gunicorn worker) imports these modules exactly once per process lifetime. Module-level
caching of a value that is genuinely fixed for the life of that one process is correct and intentional
there. The defect only exists when many independent test files, each simulating "a fresh process," are
collected into a single actual process by a monolithic `pytest products/retail/tests` invocation.

## Why the "real" fix was rejected before, and is rejected again here

The obvious code-level fix — re-resolving `AURA_APP_DATA` on every call in `config.py`/`registry_db.py`/
`schema.py` instead of caching it at import time — was already considered and explicitly rejected in
Wave 1B's own engineering record:

> Reworking `config.py`/`registry_db.py`/`schema.py` to re-resolve `AURA_APP_DATA` on every call would
> be architecture surgery motivated purely by test convenience — exactly what Wave 1B was told not to
> do ("do not alter production behavior merely to make bad tests pass").

Phase 9's own governing instruction states the same principle independently ("do not repeat completed
scenarios without a technical reason" / no unrelated product redesign). Re-litigating and reversing an
already-considered, already-justified prior engineering decision, purely to make a raw single-process
`pytest` invocation pass, would be exactly the kind of unjustified product-code change both this phase
and Wave 1B explicitly warn against. It is not attempted here.

## Severity

**P3** — a test-tooling ergonomics issue, not a functional product defect and not a CI-reliability
blocker, because a correct, deterministic, already-existing canonical runner
(`products/run_all_tests.py`) already produces stable, ordering-independent, 100%-reproducible results
in one command. See `retail-test-isolation-resolution.md`.
