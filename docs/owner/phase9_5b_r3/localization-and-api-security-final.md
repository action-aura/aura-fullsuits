# Phase 9.5B-R3 — Milestone 10: Localization/API Security Retest, Final

Re-executed after the Milestone 3 service-error-architecture change
(new `StableCodeError`-based exceptions, new `localize_*_error`
functions, new `_localized_error_text` route dispatcher) and after the
Milestone 12 catalog authorship (38 new/updated translatable strings):

```
pytest tests/test_phase9_5b_r_locale_security.py tests/test_phase9_5b_r_api_boundary.py -q
```

Result: **18 passed, 0 failed** (14 locale-security + 4 API-boundary
tests, matching the counts recorded in Phase 9.5B-R2's own baseline).

## Why this matters specifically for the M3 change

`test_phase9_5b_r_api_boundary.py` exercises the JSON API error paths in
`commercial_ops/routes.py`, including the `"SELF_APPROVAL" in str(exc)`
string-match on `RenewalApplicationError` that was the exact reason that
class was deliberately left untouched in Milestone 3 (see
`service-message-call-path-audit.md`). Its continued passing is direct,
executed proof that the M3 refactor did not silently break that
dependency.

`test_phase9_5b_r_locale_security.py` exercises locale-switching and
locale-injection-safety behavior; its continued passing confirms the 8
new `localize_*_error` functions integrate correctly into the existing
Flask-Babel locale-selection machinery with no new attack surface (no
user-controlled input reaches `_()` template selection in any of the new
functions — the `code` argument is always one of a fixed, developer-
authored set of `UPPER_SNAKE_CASE` literals from the exception class's
own raise sites, never request data).

## Full-suite confirmation

Milestone 11's final matrix run includes both of these files as part of
the canonical `pytest tests/ -q` command — no separate exclusion.
