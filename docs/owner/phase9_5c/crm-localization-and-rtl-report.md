# Phase 9.5C — Milestone 18: Localization and RTL Report

## Pipeline (reused, unchanged)

```
pybabel extract -F babel.cfg -o translations/messages.pot .
pybabel update -i translations/messages.pot -d translations
pybabel compile -d translations
```

Same Flask-Babel authority every prior Owner phase has used — no second
localization system introduced.

## Real findings and authorship

Extraction/update after all Phase 9.5C code (Milestones 1-17) surfaced:

- **117 unique msgids** newly empty or fuzzy-matched in the Arabic
  catalog — covering every new label helper (`lead_status_label`,
  `lead_source_label`, `lead_priority_label`, `interaction_type_label`,
  `followup_status_label`, `note_visibility_label`,
  `location_source_label`, `location_verification_label`,
  `duplicate_review_marker_label`), every new stable-code error message
  (`localize_lead_error`/`localize_customer_crm_error`/
  `localize_location_error`), and every new visible string across
  `leads/{list,new,detail,dashboard}.html` and the CRM additions to
  `customers/detail.html`.
- Several fuzzy-matches were concretely wrong (e.g. `"New"` fuzzy-matched
  to `"التجديد"` [Renewal], `"Lost"` to `"تجربة"` [Trial/Pilot], `"CRM
  Dashboard"` to `"لوحة التحكم"` [generic Dashboard] — all corrected with
  real, contextually-accurate Modern Standard Arabic, not left as the
  heuristic's guess).
- All 117 given real, individually-authored Arabic translations
  consistent with this catalog's established register (reusing the same
  `"لا يمكن ... في حالة"` / `"يلزم إدخال سبب لـ..."` phrasing patterns
  already present for structurally identical existing messages, per the
  discipline established in Phase 9.5B-R3).
- English catalog: 118 identity-fills (`msgstr` = `msgid`), matching this
  project's established convention for the English locale throughout.

## Verification

```
grep -c "^#, fuzzy" translations/ar/LC_MESSAGES/messages.po  -> 0
grep -c "^#, fuzzy" translations/en/LC_MESSAGES/messages.po  -> 0
```

Both catalogs re-checked for genuinely empty `msgstr` entries after
patching (a second, independent scan) — zero remaining in either locale.
Full catalog-completeness/fuzzy-detection test re-run tracked as part of
Milestone 27's final regression (this milestone's catalog work was
completed after the first full-suite run of this wave had already
started, so that specific run's catalog-related results predate this
authorship — expected and resolved by the final regression run).

## Route/template manifest and hardcoded-string scanner

Not re-run as a standalone step in this milestone; both are exercised as
part of the existing Owner test suite (`test_phase9_5b_r_*` files) which
Milestone 27's final regression covers. New CRM templates
(`leads/list.html`, `leads/new.html`, `leads/detail.html`,
`leads/dashboard.html`) were written using only `_()`/label-helper calls
for user-facing text — no hardcoded English string was intentionally
left in any of them (verified by inspection during authorship, not yet
by an automated scanner pass specific to this milestone).

## RTL

No new CSS was added that assumes a fixed reading direction — the new
`.responsive-cards`/`.crm-card`/`.pagination` rules (Milestone 6) use
only logical properties (`margin-block`, `padding-inline` where
directional spacing is used) or direction-agnostic properties (`grid`,
`gap`), matching the base stylesheet's own established RTL-safety
discipline. Every bidirectional identifier (staff/employee UUIDs, phone,
email, coordinates) is wrapped in `<bdi dir="ltr">`, matching every other
template in this codebase.

## Machine states remain untranslated

Confirmed: `Lead.status`/`.source`/`.priority`, `LOCATION_SOURCES`,
`INTERACTION_TYPES`, `NOTE_VISIBILITIES`, and every stable error `.code`
are stored and compared as their raw `UPPER_SNAKE_CASE` values
everywhere in `app/leads/`/`app/customers/` service and API code —
translation happens only in the label-helper/`localize_*_error`
functions called from templates/route handlers, never in the values
themselves.
