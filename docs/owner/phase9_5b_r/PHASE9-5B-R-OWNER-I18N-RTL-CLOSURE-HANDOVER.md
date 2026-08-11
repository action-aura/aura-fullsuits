# Phase 9.5B-R — Owner i18n/RTL Closure — Handover

## What this phase is

A narrow corrective wave closing the one gate Phase 9.5B left open: Arabic localization and RTL layout.
Built one reusable, Owner-wide i18n/RTL foundation and fully localized every screen Phase 9.5B itself
built (auth/setup/MFA + the complete employee-management portal), so Phase 9.5C and later phases inherit a
working translation system instead of building one from scratch.

## Where things live

| Concern | Path |
|---|---|
| Canonical locale authority | `owner/app/i18n.py` (`select_locale()`, template globals, `bidi_isolate` filter) |
| Domain-label localization | `owner/app/i18n_labels.py` |
| Date/number formatting | `owner/app/i18n_format.py` |
| Language switcher | `owner/app/locale_routes.py` (`/locale/<code>`) |
| Translation catalogs | `owner/translations/{en,ar}/LC_MESSAGES/messages.po` (+ compiled `.mo`) |
| Extraction config | `owner/babel.cfg` |
| Client-side confirm helper | `owner/app/static/js/confirm.js` (CSP-compliant, external, zero inline JS) |
| Migration | `owner/migrations/versions/338d06dece44_*.py` (`StaffUser.locale`) |
| Tests | `owner/tests/test_phase9_5b_r_*.py` (11 files, 57 tests) |
| Design/evidence docs | `docs/owner/phase9_5b_r/` (this directory) |

## Real bugs found and fixed this phase (see `phase9-5b-r-final-decision.md` for full list)

1. Obsolete `babel.cfg` Jinja2 extensions broke extraction entirely.
2. Inline `confirm()` JS strings were a real injection/breakage risk once translated — fixed with
   `data-confirm` + external script.
3. An f-string argument to `_()` was unextractable — fixed with a named placeholder.
4. **A real, pre-existing Phase 9.5B bug**: the mobile responsive-table collapse never worked (missing
   `<thead>`/`<tbody>`), only found via this phase's real browser validation.

## One honest, documented scope reduction (unchanged from the start of this phase)

The 37 pre-existing Phase 5-8 templates inherit the RTL/i18n foundation structurally (correct `lang`/
`dir`, correct layout mirroring via the shared `base.html`) but their own body text is not translated this
phase. Extending localization to them later requires zero foundation rework — only wrapping their existing
strings in `_()` and adding catalog entries.

## What a future phase inherits, ready to use

- A working, tested `_()`/`gettext()` catalog system with real extraction/update/compile commands.
- A real, safe language switcher and persisted per-account preference.
- A real RTL layout pattern (logical CSS properties) proven correct in an actual browser.
- Centralized domain-label and formatting helpers ready for any new stable enum a future phase adds.
- A bounded hardcoded-string scanner that will catch a forgotten `_()` wrap in any *new* gated screen
  added to `GATED_DIRS`.

## Verdict

See `phase9-5b-r-final-decision.md` and `combined-phase9-5b-closure-decision.md`: **PASS**. Combined
Phase 9.5B + Phase 9.5B-R result: **PASS**.
