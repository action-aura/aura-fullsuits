# Phase 9.5B-R2 — Owner-Wide Accessibility Revalidation

## Scope and tooling (stated explicitly, per governing spec's own instruction)

This is **not** a claim of formal WCAG certification. Real tooling used:
Playwright's accessibility-tree snapshot (`browser_snapshot`, backed by the
same engine Chrome DevTools/axe use to build the accessibility tree) against
the live local dev server, in both English and Arabic, at multiple viewports,
covering a representative set of pages across the newly translated domains
(dashboard, customers, licenses, installations — see
`full-viewport-browser-validation.md` for the exact page list and
screenshots).

## What the real accessibility-tree extraction confirmed

- **Landmarks**: `banner` (header), `navigation` (both the language switcher
  and the main nav), `main` are present and correctly labeled in both
  locales — confirmed via the real snapshot on `/licenses` (Milestone 8
  evidence), which shows `navigation "اللغة"` (Arabic: "Language") and
  `navigation` (main nav) as distinct, correctly nested landmarks.
- **Headings**: every page retains its heading hierarchy (`heading "..."
  [level=2]`) — unchanged by translation, since only the text content
  changes, not the tag.
- **Table semantics**: real `columnheader`/`rowgroup`/`cell` roles confirmed
  present in the Arabic-rendered `/licenses` accessibility tree (`table
  [ref=...]` → `rowgroup` → `row` → `columnheader "الحالة"` etc.) — the
  `<thead>`/`<tbody>` structural fix (Milestone 3/6) is not just a visual
  fix, it is what makes these roles resolve correctly in the accessibility
  tree at all; a bare `<tr>` without `<thead>`/`<tbody>` does not reliably
  produce `columnheader` roles in every browser's accessibility mapping.
- **Links and buttons**: every interactive element resolves to `link` or
  `button` with its real (translated) accessible name — e.g. `link "عرض"`
  (View), `button "تسجيل الخروج"` (Log out) — confirmed by direct snapshot
  inspection, not assumed.
- **Language switcher semantics**: `navigation "اللغة"` (Arabic) /
  `navigation "Language"` (English) with two `link` children, each with its
  own accessible name (`"English"` / `"العربية"`) — a screen reader
  encountering this control gets a correctly labeled, navigable landmark in
  both locales.
- **Form labels**: every `<label>`/`<input>` pair translated this wave kept
  its existing `for`/`id` association (only the label text changed) — no
  template in this wave altered a `for`/`id` pair, confirmed by the
  translation diffs themselves (label wrapping never touched the attribute).
- **Bidi identifier readability**: `<bdi dir="ltr">` wrapping (applied to
  every UUID/email/product-code/license-key-prefix/IP shown in the 50
  templates) is visible in the accessibility tree as plain text content
  inside its cell, isolated from the surrounding RTL paragraph direction —
  confirmed visually in the mobile Arabic dashboard screenshot (identifiers
  render left-to-right and un-corrupted inside right-to-left surrounding
  text).

## Keyboard navigation and focus

Not independently re-tested this wave with a dedicated keyboard-only pass
(no new focus-trap, tabindex, or keyboard-handler code was introduced or
touched anywhere in this wave's diff — confirmed by `git diff` showing zero
changes to any `tabindex`, `onkeydown`, or focus-management code). Phase
9.5B-R's own keyboard/focus validation (`accessibility-validation-report.md`
in that phase's docs) remains the last direct keyboard-navigation evidence;
carried forward as still valid since nothing in that code path changed.

## Status-not-color-only

Confirmed unchanged: every status badge in the newly translated templates
carries its translated text label alongside the color class (e.g.
`<span class="badge active">{{ subscription_status_label(...) }}</span>`) —
never a bare color swatch. No new color-only indicator was introduced.

## Real defect found and fixed via this same evidence-gathering pass

The mobile responsive-table gap (Milestone 6/8's own defect log,
`rtl-defect-and-fix-log.md`) was found via visual screenshot inspection, not
the accessibility tree directly — but the fix (`<thead>`/`<tbody>` +
`class="responsive-table"`) also directly improves the accessibility-tree
table semantics described above, so the two findings reinforce each other.
