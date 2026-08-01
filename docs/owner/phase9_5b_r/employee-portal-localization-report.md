# Phase 9.5B-R Milestone 10 — Employee Portal Localization Report

Every real string in `employees/list.html`, `new.html`, `detail.html`, `dashboard.html`,
`invitation_created.html`, `invitations.html`, `not_found.html`, `profile/index.html`,
`profile/sessions.html` wrapped in `{{ _(...) }}`, cross-referencing `current-owner-string-inventory.md`'s
own per-template breakdown for the exhaustive list. Status/presence/role/session/MFA/invitation labels use
the centralized `owner/app/i18n_labels.py` functions (`employment_status_label()`, `presence_label()`,
`role_label()`, etc.) rather than inline conditionals — one call site change updates every screen.

## Dashboard

All 14 metric labels translated; `%(ts)s` placeholder for the generated-at timestamp, formatted via
`format_owner_datetime()` (locale-aware, Western digits — `locale-formatting-contract.md`).

## List

Search/filter/sort/pagination/status/presence/role/department labels translated; the pagination summary
(`"Page %(page)s of %(total_pages)s -- %(total)s employee(s)."`) uses named placeholders so Arabic word
order can differ from English without breaking the sentence.

## Detail

All 7 real sections (overview, account security, roles/permissions, edit-profile form, lifecycle actions,
sessions, audit timeline) translated. The 2 `confirm()` dialog strings (suspend/terminate) moved from
inline `onsubmit` JS to `data-confirm` attributes + `static/js/confirm.js` — a real security fix (an
apostrophe in a translated string could have broken out of a single-quoted inline JS string; see
`javascript-localization-report.md`).

## Creation/editing

Every form label/placeholder/help text/checkbox label translated; role checkboxes use `role_label()`
instead of the raw role code.

## Self-service

Full translation including the explicit "which fields are self-editable" explanatory text — a
security-relevant string (tells the user what management controls vs. what they control), translated
faithfully rather than paraphrased.

## Not translated (deliberately, per `phase9-5b-r-scope-and-boundaries.md`)

Internal enum values submitted in forms (`role_codes` checkbox `value=`, `employment_status` filter
`value=`, presence filter `value=`) — always the raw code (`SALES`, `ACTIVE`, `ONLINE`), never the
translated label, so form submission behavior is 100% locale-independent (Non-Negotiable Principle 3/4).

## Real, tested proof

`test_phase9_5b_r_template_rendering.py` (all 9 templates, both locales) +
`test_phase9_5b_r_bilingual_e2e.py` (full Arabic functional scenario, Milestone 19) +
`test_phase9_5b_r_domain_labels.py` (label-mapping correctness/fallback safety).
