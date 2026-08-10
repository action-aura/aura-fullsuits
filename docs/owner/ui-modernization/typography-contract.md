# Owner App — Typography Contract

## 1. Font stack (system-first, no bundled/CDN fonts)

```
--aura-font-sans: system-ui, -apple-system, "Segoe UI", Roboto,
  "Helvetica Neue", Arial, "Noto Sans Arabic", "Noto Sans", sans-serif;
--aura-font-mono: ui-monospace, "SFMono-Regular", Consolas,
  "Liberation Mono", Menlo, monospace;
```

`"Segoe UI"` is the real, already-present Windows system font (the
deployment target per `dev-environment` history) and ships with strong
Arabic glyph coverage; `"Noto Sans Arabic"` is listed as an explicit
fallback for non-Windows/non-Segoe environments, matching what
`current-ui-audit.md` found the *old* inline stylesheet already
referenced (`font-family: system-ui, ..., "Noto Sans Arabic", ...` —
carried forward unchanged, not invented). No font file is bundled, no
`@font-face`, no external font CDN request — zero new network
dependency, per the governing spec's explicit prohibition.

## 2. Scale

| Token | Size | Real use |
|---|---|---|
| `--aura-font-size-xs` | 12px | Table meta text, timestamps, helper text |
| `--aura-font-size-sm` | 13px | Table body, dense form labels |
| `--aura-font-size-base` | 14px | Default body text (matches the old inline stylesheet's own `0.88rem`/`0.9rem` table/input sizes — this phase's base size is not a jarring change from what staff already read daily) |
| `--aura-font-size-md` | 16px | Form input values, emphasized body text |
| `--aura-font-size-lg` | 18px | Section headings, card titles |
| `--aura-font-size-xl` | 22px | Page headings (top bar `<h1>`) |
| `--aura-font-size-2xl` | 28px | KPI values only — the largest text anywhere in the app, per the governing spec's "no excessively large dashboard headings" rule |

Line heights: `--aura-line-height-tight` (1.25, headings),
`--aura-line-height-base` (1.5, body — never "compressed" per the
governing requirement), `--aura-line-height-dense` (1.35, table rows,
where 1.5 would waste vertical space in an information-dense grid
without hurting readability at 13-14px).

## 3. Numbers, money, identifiers

- Money, quantities, and any column of numbers meant to be visually
  compared use `font-variant-numeric: tabular-nums` so digits align
  to a fixed width — a real, common failure mode in the *old* app
  (proportional digits in `<td>` cells, `current-ui-audit.md`), fixed
  structurally here rather than per-template.
- License serials, correlation IDs, and other opaque identifiers use
  `--aura-font-mono` (monospace) — easier to visually diff/scan than
  proportional text, and matches the existing `code.masked` class's
  intent (`components.css`, carried forward from the old inline
  stylesheet).
- Every bidirectional identifier (email, UUID, employee number,
  correlation ID) already goes through the existing `bidi_isolate`
  Jinja filter (`<bdi dir="ltr">`, per `base.html`'s original comment,
  preserved unchanged) — this phase does not touch that mechanism,
  only the surrounding typography.

## 4. Arabic / mixed-script verification

Checked against real content shapes found during the Stage A audit
(`screen-route-inventory.md`, `current-ui-audit.md`):

- **Long Arabic customer names**: `text-overflow: ellipsis` with a
  real `title` attribute fallback is the table-cell pattern going
  into `enterprise-table-system.md` (Stage D) — not solved by
  typography alone, flagged here so that component doc inherits the
  requirement.
- **Mixed English/Arabic in one string** (e.g. a customer name with
  an embedded English brand term): the system font stack renders both
  scripts from the same font run without a visible weight/baseline
  mismatch (verified visually against Segoe UI's real Arabic
  metrics — both scripts share the same design system on Windows).
- **Large financial values**: tabular numerals (§3) prevent a
  six-figure amount from visually "wobbling" against a four-figure one
  in the same column.
- **Serials/identifiers**: monospace + `bdi` isolation (§3) — a
  License serial containing Latin letters and digits stays LTR even
  inside an RTL page, matching the existing, already-correct
  mechanism.

Full RTL layout verification (mirroring rules, logical-property
audit across every redesigned screen) is `localization-rtl-report.md`
in Stage E — this document covers font/number readability only, not
layout direction.

## 5. What did not change

Heading/body distinction, form-label hierarchy, and table hierarchy
are all now driven by the shared scale above instead of ad hoc
per-template sizes — but no *content* wording, no translation string,
and no `_()` call site changed. Only presentation.
