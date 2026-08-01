# Phase 9.5B-R2 — Complete Owner String Inventory

## Real, source-extracted totals

- Total catalog messages (English + Arabic, identical ID sets): **742**.
- Carried from Phase 9.5B-R (layout/auth/employees/profile): 203.
- New this wave: 539 (319 genuinely new + 220 corrected from `pybabel
  update`'s fuzzy-mismatch bug — see `rtl-defect-and-fix-log.md` item 5).

## Classification

- **Translatable UI text**: all 742 — headings, labels, buttons, table
  headers, status labels (via `i18n_labels.py`), form help text,
  confirmation-dialog text, error/validation messages passed as literals
  from route code.
- **Intentional machine identifiers (never translated)**: stable enum/status
  *values* (e.g. `"ACTIVE"`, `"DRAFT"`), UUIDs, license keys/prefixes,
  emails, IPs, product/plan codes, audit action codes, permission codes,
  CLI command names (`flask import-release-manifest`,
  `flask seed-offline-policy`), the brand name "Aura Owner" — bounded,
  reviewed allowlist of 7 items in the hardcoded-string scanner plus every
  value passed through a `*_label()` function's `.get(code, code)` fallback
  (the code itself, never altered, only its *display* wrapped).
- **Stored data, never translated by design**: entitlement-definition
  descriptions, customer/contact free-text fields, audit reasons/notes —
  real user- or seed-authored content, not template literals.

## Extraction method

`pybabel extract -F babel.cfg -o translations/messages.pot .` against the
real, expanded `babel.cfg` scope covering every `.py` file under `app/` and
every `.html` file under the now-15-directory `app/templates/**`.
