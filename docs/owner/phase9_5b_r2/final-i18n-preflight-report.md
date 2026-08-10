# Phase 9.5B-R2 — Final i18n Preflight Report

## Extension made

`_check_i18n_configuration()` (`app/commercial_ops/preflight.py`) extended
with a new blocking check per locale: `i18n_catalog_complete_{code}`. Reads
the real source `.po` file (not just checking `.mo` file presence/size, as
the pre-existing Phase 9.5B-R check did) and fails if any entry is empty or
`fuzzy`. This directly targets the real bug class found this wave
(`pybabel update`'s mismatched-fuzzy-translation defect, 221 real
occurrences, see `rtl-defect-and-fix-log.md` item 5) — a fuzzy entry
compiles to *something* (so the old `.mo`-size check would have missed it
entirely), just not the correct translation.

## Real result against the current catalog

```
i18n_catalog_complete_en: OK -- Locale 'en' catalog has zero empty/fuzzy entries.
i18n_catalog_complete_ar: OK -- Locale 'ar' catalog has zero empty/fuzzy entries.
```

Verified by `test_phase9_5b_r2_i18n_preflight.py` (2 tests, both passing)
and by direct execution of `_check_i18n_configuration()` against the real
compiled catalog.

## Full preflight check inventory (Phase 9.5B-R + Phase 9.5B-R2 combined)

- `i18n_supported_locales_configured`
- `i18n_default_locale_valid`
- `i18n_catalog_compiled_{en,ar}` (`.mo` presence/size — Phase 9.5B-R)
- `i18n_catalog_complete_{en,ar}` (empty/fuzzy `.po` entries — Phase 9.5B-R2, new)
- `no_invalid_stored_staff_locale`

All are blocking checks (failure sets the overall preflight result to FAIL,
nonzero exit per the existing preflight CLI wiring — unchanged this wave).

## Personal data / secret exposure

Unchanged from Phase 9.5B-R: the new check reads only locale codes, file
paths, and .po message counts — no staff PII, session, or credential data
is read or printed by this check.
