# Owner App — Canonical Design Token System (Stage B)

One semantic token system for the whole Aura Owner UI modernization.
Every color, spacing, radius, shadow, motion, and layout value used in
any redesigned template or stylesheet must come from a token defined
here — no arbitrary hex/px/ms literals scattered across templates (see
`current-ui-audit.md` §2 for why the *current* app violates this: 0
CSS variables reused via `var()` outside the one file that defines
them, every hex color hand-retyped per template).

Implementation: one root stylesheet,
`owner/app/static/css/tokens.css`, defining every `--aura-*` custom
property below at `:root`, with dark-theme overrides scoped under
`:root[data-theme="dark"]` (mechanism detailed in
`light-dark-theme-contract.md`, which also carries the full verified
light-vs-dark value table and contrast-ratio checks). This document
defines the token *names*, *scale*, and *source of truth* — the sibling
theme-contract doc is the place for the complete per-theme value
mapping and WCAG contrast verification.

## 1. Brand source

Real brand mark provided directly by the Product Owner
(`logo-brand-audit.md` §7, 2026-08-05) — a circular emblem: near-black
badge, "ACTION" in amber/gold, "AURA" in white, ringed by a full-
spectrum conic gradient with radiating circuit traces. Direction given:
"use as a base and be creative."

Real colors extracted from the mark (visually sampled, not invented —
see `logo-brand-audit.md` §7 for the full extraction table):

| Extracted from the mark | Hex | Becomes |
|---|---|---|
| Blue arc of the ring (matches Owner's pre-existing `--accent: #2452b8`) | `#2D5FE0` | `brand-primary` |
| "ACTION" wordmark color (the mark's own real text color, not the ring) | `#F0A030` | `brand-secondary` |
| Violet/magenta arc of the ring | `#8B5CF6` | `brand-accent` |
| Badge's own near-black fill | `#0A0A0C` | reference point for the darkest dark-theme surface (lifted slightly for readability — see §3) |

Deliberate scope decision (matches `logo-brand-audit.md` §7's "design
tension" note): the mark's full rainbow ring is reserved for a small
set of high-impact brand moments — favicon, login/security screens,
first-login welcome hero (`application-shell-contract.md`,
`first-login-welcome-contract.md`) — and is never used as literal UI
chrome (buttons, table rows, nav, cards). Everyday UI draws from
`brand-primary` (blue) and `brand-secondary` (amber) only, exactly as
§7/§8 of the governing task spec require ("Avoid... heavy gradients on
every card... Do not use a brand color as every status color").

## 2. Color roles

Full semantic list (per the governing spec's required minimum set).
Light-theme base values shown; dark-theme values live in
`light-dark-theme-contract.md`.

```css
:root {
  /* Brand */
  --aura-color-brand-primary:       #2D5FE0;
  --aura-color-brand-primary-hover: #244ECB;
  --aura-color-brand-primary-active:#1B3DA3;
  --aura-color-brand-primary-soft:  #E8EEFC;
  --aura-color-brand-secondary:     #F0A030;
  --aura-color-brand-accent:        #8B5CF6;

  /* Surfaces */
  --aura-color-surface-base:    #F6F8FA; /* = Owner's existing --bg, kept for continuity */
  --aura-color-surface-raised:  #FFFFFF;
  --aura-color-surface-overlay: #FFFFFF;
  --aura-color-surface-muted:   #EEF1F4;

  /* Text */
  --aura-color-text-primary:   #12151C;
  --aura-color-text-secondary: #47505C;
  --aura-color-text-muted:     #6B7785; /* = Owner's existing --muted */

  /* Borders */
  --aura-color-border-default: #D8DEE4; /* = Owner's existing --border */
  --aura-color-border-strong:  #B7C0CA;

  /* Focus */
  --aura-color-focus-ring: #2D5FE0;

  /* Status — red/green/amber meanings never reassigned to brand use */
  --aura-color-success:      #1C6B34;
  --aura-color-success-soft: #DCF3E2; /* = Owner's existing .badge success pair */
  --aura-color-warning:      #8A5C00;
  --aura-color-warning-soft: #FBEECB; /* = Owner's existing .badge warn pair */
  --aura-color-danger:       #B3261E; /* = Owner's existing --danger */
  --aura-color-danger-soft:  #FBDCDA; /* = Owner's existing .badge danger pair */
  --aura-color-info:         #0E7490;
  --aura-color-info-soft:    #DCEEF3;
}
```

Continuity note: five of these values are not new inventions either —
they carry forward Owner's own existing, already-shipping tokens
(`--accent`, `--bg`, `--muted`, `--border`, `--danger`, and all three
`.badge` status pairs, per `logo-brand-audit.md` §3) refined only
slightly (`#2452b8` → `#2D5FE0`) to align with the real logo's blue arc.
Nothing about Owner's current color identity is being discarded —
it's being formalized into tokens and extended with the amber/violet
brand colors the app never had before.

## 3. Dark-background source values

The mark's own near-black fill (`#0A0A0C`) is the reference point for
the darkest dark-theme surface, but is not used verbatim — per the
governing spec's own §28 requirement ("dark theme must not be pure
black everywhere... use layered surfaces"), dark-theme surfaces are
lifted to `#12151C`/`#1B1F29`/`#232837` (base/raised/overlay) so text
and borders retain contrast against a genuinely dark, but not flat-
black, background. Full values in `light-dark-theme-contract.md`.

## 4. Typography scale

```css
:root {
  --aura-font-sans: system-ui, -apple-system, "Segoe UI", Roboto,
    "Helvetica Neue", Arial, "Noto Sans Arabic", "Noto Sans", sans-serif;
  --aura-font-mono: ui-monospace, "SFMono-Regular", Consolas,
    "Liberation Mono", Menlo, monospace;

  --aura-font-size-xs:   0.75rem;  /* 12px — table meta, timestamps */
  --aura-font-size-sm:   0.8125rem;/* 13px — table body, dense forms */
  --aura-font-size-base: 0.875rem; /* 14px — default body text */
  --aura-font-size-md:   1rem;     /* 16px — form inputs, emphasis body */
  --aura-font-size-lg:   1.125rem; /* 18px — section headings */
  --aura-font-size-xl:   1.375rem; /* 22px — page headings */
  --aura-font-size-2xl:  1.75rem;  /* 28px — KPI values, max heading size */

  --aura-line-height-tight: 1.25;  /* headings */
  --aura-line-height-base:  1.5;   /* body */
  --aura-line-height-dense: 1.35;  /* table rows */

  --aura-font-weight-regular: 400;
  --aura-font-weight-medium:  500;
  --aura-font-weight-semibold:600;
  --aura-font-weight-bold:    700;
}
```

Rules: no dashboard heading exceeds `--aura-font-size-2xl` (matches the
spec's "no excessively large dashboard headings"). Money, quantities,
serials, and IDs always use `font-variant-numeric: tabular-nums` (or
`--aura-font-mono` for serials specifically) so columns of numbers
align — verified against real content classes during `current-ui-audit.md`
(long Arabic/English customer names, large financial values, mixed-
script rows) in `typography-contract.md`.

## 5. Spacing scale (4px grid)

```css
:root {
  --aura-space-0:  0;
  --aura-space-1:  0.25rem; /* 4px */
  --aura-space-2:  0.5rem;  /* 8px */
  --aura-space-3:  0.75rem; /* 12px */
  --aura-space-4:  1rem;    /* 16px */
  --aura-space-5:  1.25rem; /* 20px */
  --aura-space-6:  1.5rem;  /* 24px */
  --aura-space-8:  2rem;    /* 32px */
  --aura-space-10: 2.5rem;  /* 40px */
  --aura-space-12: 3rem;    /* 48px */
  --aura-space-16: 4rem;    /* 64px */
}
```

## 6. Radius, border width, elevation, z-index

```css
:root {
  --aura-radius-sm:   4px;
  --aura-radius-md:   8px;
  --aura-radius-lg:   12px;
  --aura-radius-full: 999px; /* pills, badges, avatars */

  --aura-border-width-sm: 1px;
  --aura-border-width-md: 2px; /* focus rings, active state */

  --aura-shadow-sm: 0 1px 2px rgba(16, 24, 38, 0.06);
  --aura-shadow-md: 0 2px 8px rgba(16, 24, 38, 0.08);
  --aura-shadow-lg: 0 8px 24px rgba(16, 24, 38, 0.14);

  --aura-z-base:            0;
  --aura-z-sticky:          10;  /* sticky table headers, top bar */
  --aura-z-dropdown:        100;
  --aura-z-overlay:         200; /* drawer/modal backdrop */
  --aura-z-modal:           300;
  --aura-z-toast:           400;
  --aura-z-command-palette: 500; /* always topmost */
}
```

Dark-theme shadows use higher-opacity black and lean more on
`--aura-color-border-default` than on shadow depth (shadows read poorly
on dark surfaces) — detailed in `light-dark-theme-contract.md`.

## 7. Motion

```css
:root {
  --aura-motion-instant: 90ms;  /* immediate feedback (hover, press) */
  --aura-motion-fast:    150ms; /* ordinary transitions */
  --aura-motion-base:    200ms;
  --aura-motion-slow:    240ms; /* larger panels (drawer, modal) */

  --aura-ease-standard:    cubic-bezier(0.2, 0, 0, 1);
  --aura-ease-decelerate:  cubic-bezier(0, 0, 0, 1);
  --aura-ease-accelerate:  cubic-bezier(0.3, 0, 1, 1);
}

@media (prefers-reduced-motion: reduce) {
  :root {
    --aura-motion-instant: 0ms;
    --aura-motion-fast:    0ms;
    --aura-motion-base:    0ms;
    --aura-motion-slow:    0ms;
  }
}
```

Full behavioral rules (what may animate, what must never block
critical work) live in `motion-microinteraction-contract.md`.

## 8. Icon sizes, touch/click targets

```css
:root {
  --aura-icon-sm: 16px;
  --aura-icon-md: 20px;
  --aura-icon-lg: 24px;
  --aura-icon-xl: 32px;

  --aura-target-compact:     32px; /* dense table row actions */
  --aura-target-comfortable: 40px; /* default interactive minimum */
}
```

## 9. Content widths, sidebar widths, breakpoints

```css
:root {
  --aura-content-max-width: 1440px; /* caps line length on ultrawide monitors */
  --aura-content-narrow:    720px;  /* forms, detail reading width */

  --aura-sidebar-expanded:  260px;
  --aura-sidebar-collapsed: 64px;

  --aura-bp-sm:  768px;  /* tablet */
  --aura-bp-md:  1024px;
  --aura-bp-lg:  1280px;
  --aura-bp-xl:  1440px;
  --aura-bp-2xl: 1920px;
}
```

Matches the exact widths `responsive-validation-report.md` validates
against (1280×720, 1366×768, 1440×900, 1920×1080, tablet, narrow).

## 10. Table and form density

```css
:root {
  --aura-table-row-compact:     32px;
  --aura-table-row-comfortable: 44px;
  --aura-table-cell-pad-compact:     0.375rem 0.5rem;
  --aura-table-cell-pad-comfortable: 0.625rem 0.75rem;

  --aura-form-control-height: 36px;
  --aura-form-label-gap:      0.375rem;
  --aura-form-group-gap:      1.25rem;
}
```

Real driver: `screen-route-inventory.md` found 58 tables opting into
`.responsive-table` but only 18 correctly setting `data-label` — the
new `enterprise-table-system.md` component fixes this structurally
(one table partial, not 58 hand-rolled copies) rather than patching
each template individually.

## 11. Chart semantic colors

Two separate chart palettes, kept deliberately distinct so a
categorical chart never accidentally implies a success/danger meaning
it doesn't have:

```css
:root {
  /* Neutral categorical series (e.g. revenue by product) — never
     implies status */
  --aura-chart-cat-1: #2D5FE0; /* brand-primary */
  --aura-chart-cat-2: #F0A030; /* brand-secondary */
  --aura-chart-cat-3: #0E7490; /* info */
  --aura-chart-cat-4: #8B5CF6; /* brand-accent */
  --aura-chart-cat-5: #47505C; /* text-secondary, neutral gray series */
  --aura-chart-cat-6: #B3261E; /* danger — last resort, only if 6 series needed */

  /* Status-meaningful series (e.g. paid vs. overdue) — reuses the
     real status tokens intentionally, only when the chart truly
     represents that status */
  --aura-chart-status-success: var(--aura-color-success);
  --aura-chart-status-warning: var(--aura-color-warning);
  --aura-chart-status-danger:  var(--aura-color-danger);
}
```

Rule (binding on every chart built in Stage D): a chart may use the
*categorical* palette for neutral breakdowns, or the *status* palette
only when the segments genuinely represent that status — never mix
the two meanings in one chart, and never let a categorical chart's
color choice be mistaken for a status signal.

## 12. What this document does not decide

- Exact per-theme (light/dark) values, contrast-ratio verification →
  `light-dark-theme-contract.md`.
- Where each token applies inside the shell → `application-shell-
  contract.md`.
- The literal logo/favicon image file's placement and usage rules →
  `logo-brand-audit.md` §7 (file itself still outstanding).
