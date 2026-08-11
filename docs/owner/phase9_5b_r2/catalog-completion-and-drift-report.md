# Phase 9.5B-R2 — Catalog Completion and Drift Report

## Real workflow executed

```
python -m babel.messages.frontend extract -F babel.cfg -o translations/messages.pot .
python -m babel.messages.frontend update -i translations/messages.pot -d translations
# fill/correct English (identity) and Arabic (real translation) entries
python -m babel.messages.frontend compile -d translations
```

Run from `owner/`, against the real, expanded `babel.cfg` scope (all 15
real template directories, all Python source under `app/`).

## Real counts

- Total real messages: **742** (203 carried from Phase 9.5B-R + 539 new
  this wave — 319 genuinely new strings + 221 fuzzy-mismatched by
  `pybabel update`'s heuristic, both categories corrected — see below).
- Missing English translations at close: **0**.
- Missing Arabic translations at close: **0**.
- Fuzzy entries at close: **0** (both locales).
- English and Arabic catalogs contain the exact same message-ID set
  (verified by `test_english_and_arabic_catalogs_have_the_same_message_set`).

## Real defect found: `pybabel update`'s fuzzy-matching produced 221 wrong
   translations, not just missing ones

Documented in full in `rtl-defect-and-fix-log.md` item 5. Summary: Babel's
approximate-match heuristic paired new msgids with unrelated old
translations and marked them `fuzzy` with an incorrect guessed string,
rather than leaving them empty. A naive "fill only empty entries" script
would have missed all 221 silently. Caught by explicitly checking `m.fuzzy`
on every catalog entry after the update step, not just string emptiness.

## Drift control going forward

`test_phase9_5b_r2_catalog_drift.py` (6 tests) added as a permanent CI-style
guard:

- Zero empty entries in either locale.
- Zero fuzzy entries in either locale (this specifically catches a repeat of
  the exact bug found this wave).
- English and Arabic catalogs cover the identical message set.
- Re-extracting from source finds nothing missing from the compiled English
  catalog (catches a forgotten `pybabel update` after adding a new
  translatable string).
- Both `.mo` files exist and are non-empty.
- Total message count never silently regresses below the real
  Phase 9.5B-R2 baseline (742).

## No automated/external translation service used

Every one of the 539 new strings (319 empty + 220 real corrections, one
figure differs from 221 because one fuzzy entry's correct Arabic happened to
already exist verbatim from a pre-existing catalog entry with the same
English source text) was translated by direct authorship in this session,
reviewed against the real UI context of the template it came from — no
call to any external translation API, matching Non-Negotiable Rule 7.
