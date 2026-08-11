# Phase 9.5B-R Milestone 6 — RTL Component Review

Real component-by-component review, performed via actual Playwright browser sessions against a running
Owner dev server (not template-only inspection) — see `browser-and-responsive-validation.md` for the
full session log and screenshot evidence.

| Component | Reviewed | Result |
|---|---|---|
| Header (brand/language switcher/user/logout) | ✅ real screenshot | Correctly mirrored: brand at the visual right, language switcher + user + logout at the visual left, in RTL reading order |
| Global navigation (`nav.tabs`) | ✅ real screenshot | Correctly reverses to RTL reading order; wraps correctly |
| Language switcher | ✅ real accessibility snapshot | Proper `navigation` landmark, `aria-label="اللغة"`, `aria-current="true"` on the active locale, both links keyboard-reachable |
| Dashboard KV metric grid (`.kv`) | ✅ real screenshot | Label/value columns correctly swapped (grid direction follows `dir` automatically, no explicit CSS needed — confirmed, not just theorized) |
| Employee list filters/table | ✅ real screenshot + accessibility snapshot | Real `columnheader`/`row`/`cell` semantics; all `<select>` filter options show correct Arabic labels; search placeholder correctly translated |
| Employee list — **mobile card view** | ✅ real screenshot, **real bug found and fixed** | See below |
| Employee detail (all 7 sections) | ✅ real full-page screenshot | Every section (overview, account security, roles/permissions, edit form, lifecycle actions, sessions, audit timeline) renders correctly RTL-mirrored, zero clipping/overlap observed |
| Forms (labels, inputs, buttons) | ✅ real screenshots | Labels correctly positioned above/before their inputs in both directions (block-direction label placement is inherently direction-agnostic, confirmed) |
| Badges (status/presence) | ✅ real screenshot | Render correctly, color+text (never color-only, satisfies Milestone 18's own accessibility requirement) |
| Lifecycle confirm dialogs | ✅ real screenshot (Arabic confirm text visible in the DOM via `data-confirm`) | Real Arabic confirmation text present and correctly attached |

## Real bug found and fixed: mobile responsive table collapse

**Finding**: at 390×844 (the spec's own mobile-width validation size), the employee list's mobile "card"
view (Phase 9.5B's own `.responsive-table` CSS, `table.responsive-table thead { display: none; }`)
**did not actually collapse the header row** — a real screenshot showed the raw, horizontally-overflowing
header row rendered *above* the correctly-stacked card. **Root cause**: the `.responsive-table` CSS rule
targets a `<thead>` element, but none of the four gated table templates (`employees/list.html`,
`employees/detail.html` ×2, `employees/invitations.html`, `profile/sessions.html`) ever wrapped their
header `<tr>` in a real `<thead>` — a **pre-existing Phase 9.5B gap**, never caught because Phase 9.5B's
own browser validation was itself deferred (its final gate matrix marked RTL/browser validation
`NOT VERIFIED`). This phase's real Playwright pass is what caught it. **Fixed**: added real `<thead>`/
`<tbody>` to all four templates; re-screenshotted at the same 390×844 width and confirmed the header row
is now correctly hidden, leaving only the clean stacked card. Regression-guarded by
`test_phase9_5b_r_rtl_table_structure.py`.

## Icon mirroring

No directional icons exist anywhere in the gated template set (confirmed by the string audit) — nothing
to mirror. Recorded as genuinely not applicable, not skipped.
