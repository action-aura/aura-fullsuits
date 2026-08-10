# Phase 9.5B-R2 — Localization Security Regression

## Reused, re-verified security surface (Phase 9.5B-R's own 14 tests, unchanged)

`test_phase9_5b_r_locale_security.py` (14 tests) covers invalid locale
codes, path-traversal attempts in the locale-switch `next` parameter,
open-redirect attempts, malformed locale cookies, malformed stored
`StaffUser.locale` values, and confirms locale switching never changes
authentication/authorization identity. None of this logic (`app/i18n.py`,
`app/locale_routes.py`) was touched this wave — re-run as part of the full
605+ Owner suite, all still passing.

## New this wave

- **Template injection through translated variables**: every `_()` call
  across the 50 new templates uses gettext's own placeholder substitution
  (`%(name)s`), never Python f-string/`.format()` interpolation of
  user-controlled data into a translation string — confirmed by direct
  review during translation (the same discipline as every `_()` call in
  Phase 9.5B-R).
- **HTML autoescaping**: unchanged — Jinja's default autoescape was never
  disabled anywhere in this wave's templates; no `| safe` filter was
  introduced on any translated or user-supplied value.
- **JS escaping / confirmation dialogs**: the 15 real `onsubmit`/`onclick`
  `confirm('...')` → `data-confirm` fixes (`rtl-defect-and-fix-log.md` item
  1) close the exact injection-risk class already fixed once in Phase
  9.5B-R — translated text is never interpolated into a JS string literal
  anywhere in this wave's templates.
- **Bidi-control injection in user fields**: every free-text user-entered
  field displayed in the 50 templates (customer legal/trade name, contact
  name/email/phone, notes, reasons) passes through Jinja's normal
  autoescaping; identifiers specifically at risk of bidi-corruption (UUIDs,
  license keys, emails, IPs, product/plan codes) are additionally wrapped in
  `<bdi dir="ltr">` (`markupsafe.escape()`-backed, per `i18n.py`'s existing
  `bidi_isolate` filter) — real bidi-isolation, not just visual.
- **Unknown-enum fallback**: every new `*_label()` function added to
  `i18n_labels.py` uses the same `.get(code, code)` safe-fallback pattern —
  confirmed by code review of all ~30 new functions; none can raise or
  render blank for an unrecognized code.
- **Secrets never exposed in translated output**: `licensing/detail.html`'s
  revealed one-time license key, `licensing_admin/signing_keys.html`'s
  private-key-material-never-shown notice, and every masked-key display
  (`key_prefix`/`key_suffix_masked`) were reviewed line-by-line during
  translation — only the surrounding UI text was translated, the secret
  values themselves are untouched, unformatted, `dir="ltr"`-wrapped raw
  output, identical in both locales.
- **API stable-code preservation**: `test_phase9_5b_r_api_boundary.py` (4
  tests, unchanged) plus real code review of every new domain-label
  function confirms none of them is called from `app/api_operations/routes.py`
  or the external licensing API — those remain JSON-only, zero files
  touched this wave.
- **Authentication/authorization parity**: no `@require_permission` or
  `@require_recent_auth` decorator was added, removed, or modified on any
  route this wave — confirmed by `git diff` showing zero changes to any
  decorator line across all touched route files.

## Real bug found and fixed (architectural, not injection-class, but security-adjacent)

`app/commercial_ops/pilot_lifecycle.py` and `renewal_requests.py` initially
wrapped raised business-exception messages in `gettext()`, which requires
an active Flask request/session context. Found via the full regression
(8 real test failures: `RuntimeError: Working outside of request context`)
because these service functions are called both from HTTP routes and
directly (tests, and potentially future non-HTTP callers like scheduled
jobs). Reverted to plain English messages at the exception-raise site — see
`rtl-defect-and-fix-log.md` item 2. This is recorded here because the
failure mode (an unhandled `RuntimeError` instead of the intended domain
exception) is itself a real, if narrow, availability/robustness regression
class worth flagging under "security regression," not just a test breakage.

## Dependency and secret scan

Not re-run as a fresh `pip-audit`/secret-scan pass this wave (no new
third-party dependency was added — Flask-Babel/Babel were already Phase
9.5B-R dependencies, unchanged this wave). No new secret-shaped literal was
introduced in any commit this wave (translations, label dictionaries, and
template edits contain only UI strings).
