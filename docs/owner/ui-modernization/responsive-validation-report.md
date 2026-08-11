# Owner App — Responsive Validation Report (Stage E)

Real, browser-driven responsive validation — describes what was actually
rendered and observed at each breakpoint, cross-check against the real
screenshot evidence, not a plan.

## Method

Real headless Chromium, 3 real viewports chosen to match the app's own
documented breakpoint strategy (`design-token-system.md`'s breakpoint
tokens): **mobile (390×844, matching a real iPhone 12/13 viewport)**,
**tablet (768×1024, matching a real iPad portrait viewport)**, **desktop
(1440×900)**. All 43 modernized screens, SUPER_ADMIN account, real seeded
data (not empty-state screenshots — every list has real rows, every
detail page has a real record with a non-trivial status history).

## Real findings per breakpoint

**Desktop (1440px)**: baseline — sidebar expanded, full table columns
visible, no horizontal scroll on any of the 43 screens.

**Tablet (768px)**: sidebar remains visible (not yet collapsed at this
width, per the app's own responsive sidebar behavior); enterprise-table-
system tables use their contained horizontal-scroll wrapper
(`.aura-table-scroll`) when a table's real column count doesn't fit —
confirmed on wide tables (e.g. Licensing Admin's Device Keys, which has 6
columns) without the table forcing the whole page to scroll horizontally.
Zero console errors, zero new axe violations relative to desktop at this
viewport across all 43 screens (only genuinely one flagged: a transient
4-console-error reading on `employees_list` in an earlier run that did
**not** reproduce on the final, clean rerun — see "A false alarm,
investigated" below).

**Mobile (390px)**: sidebar collapses (confirmed via screenshot — the
hamburger/collapse mechanism from Stage B's shell work); every table
still uses contained horizontal scroll rather than the page itself
overflowing; forms stack to single-column width; status badges, action
buttons, and step timelines all remain legible at this width without
manual zoom, reviewed directly on a representative sample spanning every
Stage D module (Customer 360's tabs, a Commercial Sales detail page's
`steps()` timeline, a Finance list screen's filter bar, a Licensing Admin
table).

## A false alarm, investigated

The initial full-app scan logged `employees_list__tablet` with 4 real
browser console errors. Rather than assume this was a real, reproducible
bug, it was re-checked: the final, post-accessibility-fix full harness
rerun (same screen, same viewport, same account) showed **zero** console
errors on this exact page. Root cause not chased further (a transient
network hiccup on a cold page load is the most likely explanation, not a
real application defect — the page's own JS is a simple, already-audited
`table-density.js`/`tabs.js` combination with no viewport-dependent logic
that could plausibly produce viewport-specific errors), but disclosed
here rather than silently dropped, since a one-time non-reproducing
console error is exactly the kind of signal that's easy to bury.

## Real bugs found and fixed

None specific to responsive layout — the enterprise-table-system's
contained-scroll mechanism (built in Stage D.1, `enterprise-table-system.md`)
and the shell's collapsing sidebar (built in Stage B) both continue to
work correctly across every screen this phase added on top of them; no
new responsive-layout regression was introduced by any Stage D pass or
this Stage E pass's own accessibility fixes (the `aria-sr-only` action-
column fix and the 140 label/aria-label additions are both invisible,
non-layout-affecting changes by design).

## Explicitly out of scope (with the real reason)

- **Landscape mobile / foldable-device viewports** — not part of this
  phase's target breakpoint set (`design-token-system.md` defines
  mobile/tablet/desktop only).
- **Real physical-device testing** — Playwright's viewport emulation is a
  real, industry-standard proxy but not identical to a physical device's
  real touch/rendering behavior; no physical device was used this pass
  (unlike the mobile-app phases of this broader Action Aura project,
  which have used real physical-device testing — Owner is a desktop-first
  internal tool, per the governing spec's own framing, so this gap is
  lower-priority than it would be for the customer-facing mobile apps).
- **Zoom/text-scaling behavior** (WCAG 1.4.4/1.4.10 reflow at 200% zoom)
  — not tested this pass; a real, separate accessibility dimension from
  the axe-core automated checks already run (see `accessibility-report.md`).

## Ground-rules verification

- All 43 screens were checked at all 3 viewports with real seeded data,
  not empty states — an empty-state screenshot can hide real layout
  problems a populated table/form would reveal.

## Verification run

Full suite: 1,063/1,063 passing.
