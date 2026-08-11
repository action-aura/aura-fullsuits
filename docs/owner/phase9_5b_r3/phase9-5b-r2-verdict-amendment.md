# Phase 9.5B-R3 — Milestone 14: Additive Amendment to the Phase 9.5B-R2 Verdict

This is an **additive** amendment. It does not alter, delete, or
retroactively edit any Phase 9.5B-R2 document; it records what Phase
9.5B-R3 closed.

## Gaps Phase 9.5B-R2 itself identified as open

1. A nondeterministic Owner test failure (`ObjectDeletedError` in
   `test_commercial_ops_renewal_requests.py`), observed once under heavy
   concurrent multi-process test execution.
2. Three known raw-English service-layer exception messages reaching
   staff-facing templates untranslated.
3. Real dependency/secret/infrastructure security scans not yet executed
   against the final Phase 9.5B-R2 source state.
4. Browser-family coverage self-described as "bounded to five families."

## Phase 9.5B-R3 closure, each gap

1. **Closed.** Root cause identified (external concurrent-process
   contention against the shared test database — see
   `flaky-test-root-cause-and-fix.md`), a real structural fix applied
   (`_serialize_concurrent_test_runs` Postgres advisory-lock fixture in
   `tests/conftest.py`), and 4 consecutive clean full-suite runs
   recorded, the last against the final tagged HEAD
   (`final-deterministic-regression-report.md`).
2. **Closed, and expanded.** The call-path audit
   (`service-message-call-path-audit.md`) found the real defect was
   broader than "three messages" — 7 exception classes, ~38 raise
   sites. All were converted to the `StableCodeError` architecture with
   presentation-boundary localization
   (`service-error-architecture-result.md`,
   `service-message-localization-final.md`), and the resulting 38 new
   translatable strings were given real Arabic authorship
   (`final-catalog-and-surface-report.md`).
3. **Closed.** `pip-audit` and `detect-secrets` both actually executed
   against the final dependency set and the full git-tracked file tree
   (`dependency-scan-final.md`, `secret-scan-final.md`,
   `secret-scan-false-positive-review.md`). One dependency finding
   (pytest, DoS-class, Windows-inapplicable precondition) remains, with
   written justification, not hidden.
4. **Closed.** All 16 current route families now have real browser
   evidence at minimum the mobile viewport in both locales, with the 4
   data/form-heavy families additionally covered at all 4 required
   viewports (`complete-browser-family-validation.md`,
   `browser-family-evidence-matrix.md`).

## Standing Phase 9.5B-R2 conclusions unaffected

Everything else Phase 9.5B-R2 verified (English/Arabic localization
architecture, RTL layout correctness, the bulk of route-family coverage,
the i18n preflight machinery itself) stands unchanged — Phase 9.5B-R3
did not rebuild or repeat that work, per the governing spec's explicit
"do not" boundary.
