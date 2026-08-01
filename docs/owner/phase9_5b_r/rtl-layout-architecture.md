# Phase 9.5B-R Milestone 6 — RTL Layout Architecture

## Logical properties, not physical left/right (real changes in `layout/base.html`)

| Before (physical) | After (logical) | Effect in RTL |
|---|---|---|
| `margin-right: 1rem` (nav/header links) | `margin-inline-end: 1rem` | Gap moves to the correct trailing side automatically |
| `text-align: left` (table cells) | `text-align: start` | Text aligns to the reading-direction start, not always the physical left |
| `margin: 0.5rem 0 0.2rem` (label) | `margin-block: 0.5rem 0.2rem` | Vertical margin, direction-agnostic by nature — converted for consistency |
| `padding: 0 1rem` (responsive `main`) | `padding-inline: 0.6rem` | Symmetric, but logical for consistency with the rest of the sheet |

## What did not need changing (real, verified, not assumed)

Flexbox (`.filters`, `header.topbar`) and CSS Grid (`.kv`) both already respect the document's
`direction` property per the CSS spec — `<html dir="rtl">` alone causes `display:flex`'s row axis and
`display:grid`'s column order to reverse, with zero explicit CSS changes needed. Verified by real browser
screenshot (`browser-and-responsive-validation.md`) showing the `.kv` definition-list's label/value columns
correctly swapped and the top-bar's brand/language-switcher/logout order correctly mirrored in Arabic.

## Sidebar / navigation

Owner's `nav.tabs` is a horizontal, wrapping flex row (not a fixed sidebar) — it reverses direction
automatically under `dir="rtl"` (confirmed by screenshot); the `overflow-x: auto` mobile-width rule
(Phase 9.5B's own responsive CSS) continues to work unchanged since `overflow-x` is direction-agnostic.

## Icon mirroring

No directional icons (chevrons, arrows) exist anywhere in the gated template set — confirmed by grep
(`current-owner-string-inventory.md`'s audit found none). Nothing to mirror or deliberately not-mirror
this phase; this is recorded honestly as "not applicable" rather than a false claim of review.

## Non-gated templates

The 37 non-gated Phase 5-8 templates inherit `dir="rtl"`/logical-property-safe flex/grid behavior from the
shared `base.html` automatically (they all extend it) — their own body markup was not audited component-
by-component (documented scope reduction, `phase9-5b-r-scope-and-boundaries.md`), but nothing in
`base.html`'s own CSS was left physical/directional, so the shared chrome (header, nav, `.card`, `.badge`,
`table`, `.filters`, `.kv`, form inputs) is RTL-correct for every screen that uses it, gated or not.
