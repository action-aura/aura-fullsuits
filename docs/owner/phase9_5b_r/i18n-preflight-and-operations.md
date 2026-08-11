# Phase 9.5B-R Milestone 21 — i18n Preflight and Operations

Extended (not replaced) `owner/app/commercial_ops/preflight.py::_check_i18n_configuration()`, wired into
the existing `run_preflight()` / `flask commercial preflight` command alongside every other check.

## Checks added

- **`i18n_supported_locales_configured`** — `app.config["LANGUAGES"]` is non-empty.
- **`i18n_default_locale_valid`** — `BABEL_DEFAULT_LOCALE` is itself a member of the supported allowlist.
- **`i18n_catalog_compiled_en`** / **`i18n_catalog_compiled_ar`** — the real compiled `.mo` file exists and
  is non-empty for every supported locale (catches a missed `compile` step in a frozen/staging deployment
  that never ran the Babel toolchain).
- **`no_invalid_stored_staff_locale`** — defense-in-depth: no `StaffUser.locale` value exists outside the
  allowlist (already structurally guaranteed by the real `CHECK` constraint).

## No secrets/personal data printed

Every check's `detail` string contains only locale codes, file paths, byte-size/existence facts, and
counts — `test_i18n_check_reports_no_personal_data_or_secrets` asserts directly that a real staff email
and UUID never appear in any check's rendered output.

## Real commands (all verified working this phase)

```
cd owner
python -m babel.messages.frontend extract -F babel.cfg -o translations/messages.pot .   # extract
python -m babel.messages.frontend update -i translations/messages.pot -d translations   # update
# edit translations/ar/LC_MESSAGES/messages.po by hand -- review
python -m babel.messages.frontend compile -d translations -f                            # compile
flask commercial preflight                                                              # test (incl. i18n)
```

## Real, tested proof

`test_phase9_5b_r_i18n_preflight.py` (2 tests) + the 15 pre-existing preflight tests (Phase 9.5B/8V-P2),
17/17 passing, confirming zero regression to the existing preflight surface.
