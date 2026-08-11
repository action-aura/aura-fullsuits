# Phase 9.5B-R3 — Milestone 14: Final Residual Risk Register

Real, unresolved-by-design items only — nothing here is a defect this
wave failed to close; each is a genuine residual condition with a
recorded owner-facing implication.

| # | Risk | Severity | Detail | Disposition |
|---|---|---|---|---|
| 1 | `pytest` 8.3.2 has a known DoS-class advisory (PYSEC-2026-1845) | Low | UNIX-only precondition (`/tmp/pytest-of-{user}` collision), dev-only dependency, never shipped to production | Deferred; upgrade to 9.0.3 recommended as a standalone, isolated change in a future wave — not bundled here to avoid confounding the flake investigation |
| 2 | 3 synthetic dev Super Admin accounts have `mfa_required=False` | Low, dev-only | Flagged by `flask commercial preflight` itself as an informational WARNING | Explicitly acceptable for local synthetic accounts; preflight already asserts this must never be true for a real production Super Admin |
| 3 | `pilot_lifecycle.py`/`renewal_requests.py` still define local `_StableCodeError` copies instead of importing the shared `errors.py` base | Cosmetic | Functionally identical contract, verified byte-for-byte equivalent behavior | No behavioral risk; a future pure-refactor wave could consolidate, not required for correctness |
| 4 | 2,190 accumulated local Ed25519 test-key `.pem` files on disk under `owner/var/signing-keys-test/` | None (repo-security) / minor (disk hygiene) | Gitignored, never tracked, confirmed via `git check-ignore`/`git ls-files` | Left in place deliberately (deleting mid-investigation risked being read as test-state manipulation); safe to clean up in routine maintenance |
| 5 | Browser-family validation used representative-page sampling at the 3 non-mobile viewports rather than exhaustively covering all 67 templates | None found | All 16 families verified at minimum the mobile viewport in both locales; the 4 data/form-heavy families (most likely to expose a wide-viewport defect) additionally verified at all 4 viewports | Documented judgment call in `complete-browser-family-validation.md`; no defect found in any sampled combination |
| 6 | Owner test suite runtime (~19 minutes) is long for a single-process canonical command | Low, operational | Not investigated for speed-up this wave (out of scope) | Acceptable for a pre-tag-gate regression; not a CI-blocking concern at current team size |

## Risks explicitly NOT present (verified, not assumed)

- No true secret committed anywhere in the tracked tree (`secret-scan-final.md`).
- No P0/P1 dependency vulnerability in any package shipped to a real deployment (`dependency-scan-final.md`).
- No remaining user-facing English-only service exception message reachable from any current route (`service-message-call-path-audit.md`).
- No legacy-repository mutation (`legacy-repository-preservation-final.md`).
- No migration drift (`infrastructure-security-regression-final.md`).
