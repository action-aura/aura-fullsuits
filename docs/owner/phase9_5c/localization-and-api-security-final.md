# Phase 9.5C — Milestone 22: Localization and Security Retest

## Scope

Re-run the existing Phase 9.5B-R/R2/R3 localization and security test
suites against the final Phase 9.5C code state, to confirm the CRM
additions did not regress anything already verified:

- `test_phase9_5b_r_locale_security.py` (locale-switch identity/
  permission safety)
- `test_phase9_5b_r_bidi_safety.py` (mixed-direction identifier
  handling)
- `test_phase9_5b_r_hardcoded_strings.py` (hardcoded-English-string
  scanner — must now also correctly scan the new `leads/*.html`
  templates and the CRM additions to `customers/detail.html`)
- `test_phase9_5b_r2_owner_wide_template_rendering.py` (every
  no-fixture-required page renders in both locales)
- `test_phase9_5b_r2_catalog_drift.py` / `test_phase9_5b_r_catalog_
  completeness.py` (zero empty/fuzzy — re-verified after Milestone 18's
  117 new Arabic translations + 118 English identity-fills)
- `test_phase9_5b_r_api_boundary.py` (existing JSON API error-code
  contract, e.g. `RenewalApplicationError`'s `"SELF_APPROVAL"` string
  match — confirms this phase's new stable-code error dispatch pattern
  in `api_operations/crm.py` didn't disturb the older one)
- `test_security.py` (XSS/SQL-injection-style-input safety — extended
  this phase by `test_xss_payload_in_lead_note_is_escaped_on_render`,
  Milestone 21)
- `test_rbac.py` (permission enforcement baseline)

## Result

Deferred to `final-deterministic-regression-report.md` (Milestone 27) —
these files are part of the canonical `pytest tests/ -q` command, not
re-run as a separate standalone step. The mid-wave 28-failure and
69-failure runs recorded earlier in this wave's development were both
diagnosed as artifacts of editing migration/route/template files while
an earlier long-running suite was still executing against a frozen
snapshot (Jinja templates auto-reload live; Python route registration
and the test-DB migration state do not) — not real regressions,
confirmed by rerunning cleanly with zero concurrent file changes.

## Untranslated machine states — reconfirmed

`Lead.status`/`.source`/`.priority`, `LOCATION_SOURCES`,
`INTERACTION_TYPES`, `NOTE_VISIBILITIES`, and every stable API `.code`
remain raw `UPPER_SNAKE_CASE` values at the data/API layer — only their
corresponding `*_label()`/`localize_*_error()` presentation-layer
functions produce translated text, called exclusively from templates and
route handlers (never from `app/leads/*.py` or `app/customers/services.py`
service functions themselves — grep-confirmed no `_()`/`gettext` call
exists in any service-layer file this phase touched).

## Licensing API unchanged

Confirmed: this phase added zero code to `app/api_external/` or
`app/api/` (the licensing-facing API surface) — the CRM API lives
entirely under `/api/operations/v1`, the cookie-session-authenticated
internal surface, never touching the device-signature-authenticated
licensing API's request/response contract.
