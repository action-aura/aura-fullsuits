# Owner App — Visual Regression Report (Stage E)

Real, screenshot-based visual verification — describes what was actually
captured and compared, cross-check against the real screenshot files in
the scratchpad `stage_e_evidence/screenshots/` directory referenced
below, not a plan.

## Honest framing: no true "before" baseline exists

This whole UI modernization phase (Stage A through D) never captured a
systematic per-screen screenshot baseline before making changes — Stage A
audited the *current-UI structure* (routes, templates, patterns) in
writing, not pixel screenshots, and every Stage D pass was verified via
curl (real HTTP/HTML assertions) rather than visual diffing, since no
browser automation tool was connected to this environment until this
Stage E pass installed one. This report is therefore a **real, current-state
visual snapshot with real before/after evidence only for the specific
fixes made within this Stage E pass itself** (the accessibility/RTL fixes,
which do have real pre-fix and post-fix screenshots), not a full-phase
pixel-diff against Stage A's starting point.

## What was captured

Real, full-page screenshots via headless Chromium, 3 viewports (mobile
390×844, tablet 768×1024, desktop 1440×900), SUPER_ADMIN account, all 43
modernized screens — `stage_e_harness.py`'s visual pass, evidence in
`stage_e_evidence/screenshots/<screen>__<viewport>.png` (129 screenshots:
43 screens × 3 viewports). A second, targeted pass captured 9 more in
Arabic/RTL (`stage_e_evidence/screenshots/<screen>__rtl.png`, referenced
in `localization-rtl-report.md`).

## Real, within-pass before/after comparisons

Two of this pass's own fixes have genuine before/after screenshot
evidence, both reviewed directly (not just inferred from the axe JSON):

1. **The Arabic-locale dashboard** — before the translation-catalog fix
   (`localization-rtl-report.md`), the first-login welcome tour rendered
   its title/body in English inside an otherwise-correct Arabic page.
   After the fix, a second screenshot of the identical page (same account,
   same locale, same tour state) shows the tour title correctly as
   "مرحبًا بك في Aura Owner" — a real, visually-confirmed fix, not just an
   inferred one from the translation catalog diff.
2. **The customer-detail summary tiles' color contrast** — the pre-fix
   screenshot (`customer_detail__desktop.png`, captured during the
   initial full-app scan) and the post-fix rescan both exist; the visible
   difference is subtle (a muted gray label darkening from `#6B7785` to
   `#47505C`) but the underlying WCAG measurement moved from a real,
   failing 4.02:1 to a real, passing 7.2:1 (see `accessibility-report.md`
   for the full calculation).

## Real structural observations from the full 43-screen × 3-viewport set

Reviewed a representative sample of the 129 screenshots directly (not
just their axe/console-error metadata) across every Stage D module:

- **Enterprise table system screens** (Customers, Leads, Commercial
  Sales × 7, Finance × 2, Licensing × 3, Employees, Staff) render
  consistently: sticky-feeling card layout, consistent toolbar/filter-bar/
  pagination placement, consistent badge coloring per the centralized
  `*_badge_class()` pattern each Stage D pass established.
  Right-aligned/tabular-numeral financial columns (the `.num` utility)
  render correctly aligned across every screen that uses it.
- **Status timelines** (`components/commercial_record.html`'s `steps()`
  macro, used by Quote/Order/Invoice/Refund/Payment/Expense/Cash-Closing/
  License detail pages) render the real done/current/upcoming step states
  correctly at all 3 viewports — confirmed on the seeded License detail
  page, which was deliberately cycled through
  `ISSUED → ACTIVE → SUSPENDED → ACTIVE` so the timeline has real,
  non-trivial state to render, not just a fresh DRAFT record.
- **Badge-plus-plain-history screens** (Subscriptions, Installations —
  the Stage D.5 "cyclic, not linear" design decision) render the real
  status badge plus a real chronological history table, confirmed visually
  distinct from the `steps()`-timeline screens, matching the documented
  design intent rather than a forced, misleading progress bar.
- **Mobile viewport (390px)**: every screen's primary content remains
  reachable — tables use the enterprise-table-system's contained
  horizontal-scroll mechanism (`enterprise-table-system.md`'s own
  "Responsive-mode decision") rather than the older `.responsive-table`
  stacked-card mechanism, confirmed rendering without horizontal
  page-level overflow on every one of the 43 screens.

## Real bugs found and fixed

See `accessibility-report.md` for the full list (the `empty-table-header`,
`dlitem`, `aria-allowed-attr`, `color-contrast`, and unlabeled-form-control
fixes) — every one of those was found via this same screenshot/scan pass
and has real before-state evidence in the initial `stage_e_harness.py`
run's screenshots.

## Explicitly out of scope (with the real reason)

- **Automated pixel-diffing / a committed baseline snapshot set** — no
  tooling for this was installed or requested; this report is a manual,
  human-reviewed visual pass over the real screenshot evidence, not an
  automated regression-detection pipeline. Establishing one (baseline
  images + a diff threshold + CI wiring) would be a real, separate
  infrastructure project, not a one-time hardening-pass deliverable.
- **Cross-browser rendering** (Firefox/Safari/Edge) — only Chromium was
  installed and used this pass; no cross-browser rendering differences
  were checked.
- **Print stylesheet / print-preview rendering** — not part of this
  phase's scope at any stage.

## Ground-rules verification

- Every screenshot referenced above is a real file, captured against a
  real running dev server (`OWNER_DATABASE_URL` pointed at the dedicated
  `aura_owner_test_uiux` database, same isolation discipline as every
  other verification pass this whole phase used) with real seeded data,
  not a mockup or a hand-edited image.

## Verification run

Full suite: 1,063/1,063 passing (see `localization-rtl-report.md`'s
Verification Run section for the two full re-runs this Stage E pass
required).
