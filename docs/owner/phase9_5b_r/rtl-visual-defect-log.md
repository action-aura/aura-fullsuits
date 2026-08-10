# Phase 9.5B-R — RTL Visual Defect Log

## Defect #1 — FIXED

**Found**: real Playwright screenshot, `/employees` at 390×844, Arabic locale.
**Symptom**: mobile "card" view showed the raw, un-collapsed table header row rendered above the correctly
stacked employee card — a visually broken, doubled header.
**Root cause**: `layout/base.html`'s `@media (max-width: 720px) { table.responsive-table thead { display:
none; } ... }` rule (Phase 9.5B) targets a `<thead>` element that none of the four gated table templates
actually had — their header `<tr>` was a bare child of `<table>`, not wrapped in `<thead>`.
**Fix**: added real `<thead>`/`<tbody>` to `employees/list.html`, `employees/detail.html` (both tables),
`employees/invitations.html`, `profile/sessions.html`.
**Verified fixed**: second real screenshot at the same viewport/locale shows the clean, correctly-collapsed
card layout, header row gone. Regression-guarded by `test_phase9_5b_r_rtl_table_structure.py`.
**Language-specific?** No — this was a structural HTML/CSS bug affecting English too (a pre-existing
Phase 9.5B gap); RTL/Arabic simply happened to be the language active during the real browser pass that
caught it. Recorded here per Milestone 17's own instruction ("RTL and Responsive Validation") since that
is the validation pass that surfaced it.

## No other visual defects found

Every other screen/component reviewed (see `rtl-component-review.md`'s full table) rendered correctly on
first real-browser inspection — no additional RTL-specific mirroring, clipping, overlap, or alignment
defects observed at either 1440×900 or 390×844.
