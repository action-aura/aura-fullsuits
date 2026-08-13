# Phase 9.5B-R3 — Milestone 12: Final Catalog and Surface Report

## Real, executed catalog pipeline after the Milestone 3 exception refactor

```
pybabel extract -F babel.cfg -o translations/messages.pot .
pybabel update -i translations/messages.pot -d translations
pybabel compile -d translations
```

## Findings and real authorship (not carried forward)

The Milestone 3 refactor introduced 8 new `localize_*_error` functions in
`app/i18n_labels.py`, each containing multiple new `_("...")`-wrapped
message templates. Extraction/update surfaced:

- **35 msgids** in the Arabic catalog that were either **fully empty**
  (first-time strings) or **fuzzy-flagged** (Babel's approximate-match
  guess against a similar pre-existing string, several of which were
  concretely *wrong* matches — e.g. `"Unknown activation mode: %(mode)s"`
  was fuzzy-matched to `"لا توجد مراجعات تفعيل."` ["No activation
  reviews."], an unrelated sentence).
- A follow-up test-driven pass (`test_phase9_5b_r2_catalog_drift.py`,
  `test_phase9_5b_r_catalog_completeness.py`) caught **3 additional**
  empty Arabic entries the first authorship pass missed (multi-line
  wrapped msgids that a hand-rolled detection script under-matched):
  `"The renewal request must be applied before this pilot can be marked
  converted."`, `"Duration must be between 1 and %(max_hours)s hours
  (explicit, short-lived only)."`, `"This subscription already has an
  active emergency extension until %(expires_at)s."`.
- **38 msgids** in the English catalog needed the project's established
  identity-translation convention applied (`msgstr` = `msgid` verbatim —
  the pre-existing pattern throughout `translations/en/LC_MESSAGES/
  messages.po`, confirmed by inspection before applying it here).

All 38 Arabic strings were given real, individually-authored Modern
Standard Arabic translations (not machine-translated placeholders),
consistent in register and terminology with the existing catalog (e.g.
reused the established `"لا يمكن ... في حالة %(status)s"` /
`"يلزم إدخال سبب لـ..."` phrasing patterns already present for
structurally identical existing messages).

## Verification (both emptiness AND fuzzy flag checked, per established discipline)

```
grep -c "^#, fuzzy" translations/ar/LC_MESSAGES/messages.po  -> 0
grep -c "^#, fuzzy" translations/en/LC_MESSAGES/messages.po  -> 0
```

Plus the project's own automated catalog tests, re-run after authorship:

```
pytest tests/ -q -k "i18n or catalog or locale or fuzzy or babel"
-> 45 passed, 582 deselected
```

`flask commercial preflight` (real CLI execution against `aura_owner_dev`)
re-confirms both `i18n_catalog_complete_en` and `i18n_catalog_complete_ar`
as `OK` — "zero empty/fuzzy entries" — at the real, current catalog
state, not the pre-Milestone-3 state.

## Route/template/surface re-check

No new routes, blueprints, or templates were added in Phase 9.5B-R3 (the
Milestone 3 change touched only Python exception classes and the
existing `i18n_labels.py`/`ui_routes.py` files — zero new `.html` files,
zero new `@blueprint.route` decorators). The 173-route/67-template/
16-family inventory from `current-route-family-matrix.md` remains
accurate and complete at the final HEAD.

## Fuzzy/hardcoded-string scan

The existing Owner test suite's own hardcoded-string-scan and template-
rendering tests (part of the `pytest tests/ -q -k "i18n or catalog..."`
run above, and re-confirmed in Milestone 11's full run) cover this;
no new hardcoded English string was introduced outside the
`_()`-wrapped `localize_*_error` message tables.
