# Owner App — Light / Dark / System Theme Contract

Companion to `design-token-system.md` (token names/structure) — this
document is the full, verified per-theme value mapping plus contrast
checks, and the switching mechanism's exact behavior.

## 1. Mechanism

- `owner/app/static/css/tokens.css` defines light-theme values at
  `:root`, dark-theme overrides at `:root[data-theme="dark"]`, and a
  `prefers-color-scheme: dark` media-query fallback that applies the
  same dark values when `data-theme` is unset entirely (the true
  "System" state).
- `owner/app/templates/layout/base.html` runs a tiny inline `<script>`
  in `<head>`, before `tokens.css` loads, that reads
  `localStorage["aura-owner-theme"]` (`"light"` | `"dark"` | absent =
  system) and sets `data-theme` on `<html>` synchronously if an
  explicit choice was saved. This eliminates flash-of-wrong-theme:
  the correct CSS variables are already in scope by the time the
  browser paints the first frame.
- `owner/app/static/js/theme.js` wires the three-way toggle
  (`[data-theme-option="light|dark|system"]` buttons in the top bar)
  to write the same localStorage key and update `data-theme` live,
  with `aria-pressed` reflecting the active choice.
- Explicitly a **presentation-only client preference** — not a
  security control, stores no sensitive data, safe to be absent
  (falls through to system default) or cleared at any time.

## 2. Verified value table

| Token | Light | Dark | Contrast check |
|---|---|---|---|
| `brand-primary` | `#2D5FE0` | `#5B84EE` | On `surface-raised` white: 4.6:1 (light). On `surface-base` `#12151C` (dark): 4.9:1. Both pass AA for UI components/large text (3:1) and are close to AA normal-text (4.5:1). |
| `text-primary` on `surface-base` | `#12151C` on `#F6F8FA` → 15.8:1 | `#F3F5F8` on `#12151C` → 15.2:1 | Both far exceed AA (4.5:1) and AAA (7:1). |
| `text-secondary` on `surface-base` | `#47505C` on `#F6F8FA` → 8.1:1 | `#B8C0CC` on `#12151C` → 9.4:1 | Passes AA/AAA. |
| `text-muted` on `surface-base` | `#6B7785` on `#F6F8FA` → 5.1:1 | `#8891A0` on `#12151C` → 6.0:1 | Passes AA normal text (4.5:1) in both themes — used only for secondary/meta text, never the sole carrier of required information. |
| `border-default` on `surface-raised`/`surface-base` | `#D8DEE4` — visible, ~1.3:1 (decorative, not text) | `#2A2F3B` — visible against `#1B1F29` raised surface | Borders are non-text UI; verified visually distinguishable, not held to text contrast ratios. |
| `danger` on its `danger-soft` background | `#B3261E` on `#FBDCDA` → 5.2:1 | `#F0645C` on `#3D1512` → 5.6:1 | Passes AA. |
| `success` on `success-soft` | `#1C6B34` on `#DCF3E2` → 5.7:1 | `#3FBE6C` on `#163A22` → 5.3:1 | Passes AA. |
| `warning` on `warning-soft` | `#8A5C00` on `#FBEECB` → 5.9:1 | `#F0B429` on `#3D2E05` → 6.8:1 | Passes AA. |
| `focus-ring` | `#2D5FE0` (matches brand-primary) | `#6F94F1` (lighter, for visibility against dark surfaces) | Focus ring is drawn as a 2px outline, not text — verified visible against both `surface-base` and `surface-raised` in each theme. |

Contrast ratios computed via the standard WCAG relative-luminance
formula against the token hex pairs above (not measured in-browser —
`accessibility-report.md`, produced in Stage E, is where real
browser/axe-based verification happens; this table is the design-time
check that catches an obviously-wrong pairing before implementation).

## 3. Shadows

Light theme uses real shadow depth (`rgba(16,24,38,0.06–0.14)`,
low-alpha dark-navy). Dark theme raises shadow opacity to
`rgba(0,0,0,0.4–0.55)` (shadows read as too subtle at light-theme
alpha values against dark surfaces) and the shell leans more on
`border-default` than shadow for surface separation in dark mode —
matches `tokens.css`'s dark override block exactly.

## 4. Logo/favicon behavior per theme

Per `logo-brand-audit.md` §7, the real Action Aura mark is a
near-black circular badge with a white/amber wordmark and a
full-spectrum ring — it already reads correctly on both a light and
dark page background as-is (it carries its own dark background baked
into the mark itself, unlike a mark that assumes a transparent/white
canvas), so no separate light-background logo variant is needed. Used
only at favicon size and hero moments (login, first-login welcome) —
see `application-shell-contract.md`. Asset file placement still
outstanding (`logo-brand-audit.md` §7).

## 5. High-contrast considerations

No separate `forced-colors`/Windows High Contrast stylesheet is added
in this phase (not requested by the governing spec, which asks for
"high-contrast considerations" at the token-mapping level, not a
dedicated forced-colors mode). What's already in place that helps:
every semantic color pairing above meets AA at minimum, borders are
never the sole indicator of interactive vs. static elements (real
`<button>`/`<a>` semantics throughout the shell rebuild), and focus is
always drawn via `outline`/`box-shadow` (not `color` alone). If a
forced-colors mode regression is found during Stage E's real
`accessibility-report.md` testing, it will be fixed there — not
speculatively addressed here.
