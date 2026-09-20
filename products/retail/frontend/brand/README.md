# Aura brand assets — brand kit v2, 2026-09-20

**This supersedes the 2026-09-19 pass described in every earlier revision of
this file.** The owner supplied a second, more complete brand kit —
`Action-Aura-Brand-Guide.md` (now checked in alongside its source SVGs, which
it was not in the previous pass) plus updated source SVGs — and this pass
brings it in. Read the guide itself for the full construction spec and the
sibling-product colour table; this file only tracks what lives here and who
consumes it.

## What changed from v1

v1 shipped ONE primary mark per product/master (a light-ground SVG, plus a
*derived* dark-ground brightening computed by ratio because the owner had not
delivered dark-ground artwork). **v2 is dark-first and ships a genuine
matched pair for every surface** — `aura-mark-dark.svg` / `aura-mark-light.svg`
(master), `aura-mark-flat-dark.svg` / `aura-mark-flat-light.svg` (master,
flat/mono), and, new for this product, `aura-retail-dark.svg` /
`aura-retail-light.svg` (Retail's own primary mark, both surfaces, real
delivered artwork replacing the old derived brightening).

v2 also names a colour v1 lacked: **Lifted navy `#22304A`** — the shadow
facet's value on **dark** surfaces, "so the letter doesn't sink" (the
light-surface value, shared brand navy `#16233D`, is unchanged). This repo
had already hit the underlying defect independently and patched it with
`#223B5E` (pulled from the v1 kit's own dark master, because `#22304A` did
not exist yet) — see `intro.html`'s comment, now updated to the official
value.

## The mark: a "pierced A"

A faceted letter **A** read through the Greek **delta** (Δ, the sign of
change), pierced by a tilted orbit ring — the aura. The ring passes **behind**
the letter's shoulders, shows **through the counter** (the triangular window
at the ridge), and sweeps **across the front** of the legs, ending in a
bright energy node. Two facets meet at a lit ridge line: light-left,
shadow-right. The shadow facet falls to the shared brand navy — `#16233D` on
light surfaces, `#22304A` on dark surfaces — the constant (per surface) that
makes every product's mark read as a sibling of the others.

**Aura Retail** — this repo's product — owns exactly two things per the
guide's family rule: its **accent colour** (dark `#A06030` / mid `#C97B3D` /
light `#F0B87A`) and its **counter glyph**, a small **square** ("the module
on the shelf") sitting in the counter where the ring shows through.

### The 64px rule, and now a surface split too

Per the guide's "Usage" section: **the 3D/gradient construction has a 64px
minimum.** Below that, use the flat geometry. `AuraIcons.mark(size)` in
`../icons.js` enforces the size split at runtime — `size < 64` renders the
flat branch, `size >= 64` renders the 3D one — and, new in this pass, the 3D
branch also picks its shadow facet by **surface**, reading `html[data-theme]`
at render time: `light`/`sand` get `#16233D`, `dark`/`night`/`dusk` get
`#22304A`. See `mark()`'s own comment in `../icons.js` for the reasoning, the
theme-name allowlist duplication rationale, and a checked-not-assumed gap:
`ThemeEngine.apply()`'s theme-switch re-render (`app-shell.js`, not an owned
path for this change) rebuilds `#sub-content` but not the sidebar shell, so
toggling the theme picker while already inside a subsystem does not
repaint the sidebar mark in place — only a subsystem re-entry or reload does.

The FLAT branch (`size < 64`) is **unchanged in this pass** and still uses
`fill="currentColor"` for the letterform and
`stroke="var(--brand-ring-end, #A06030)"` for the ring, deliberately NOT the
kit's flat masters (which are the MASTER brand — ivory/navy A with a TEAL
ring). Per the guide, below ~32px products differentiate by ring colour
only; `currentColor` + the per-theme token already delivers a correctly
per-theme-legible navy/ivory letterform with Retail's own amber ring, which
is what a previous pass fixed a real defect to get (a fixed `fill="#16233D"`
letterform measured ~1:1 on three of five dark-theme grounds — see
`../icons.js`'s comment on `mark()` for the numbers). Both of this repo's
real call sites (the sidebar brand slot and the sign-in overlay in
`app-shell.js`) pass `mark(40)`, so the FLAT geometry is what is actually on
screen today, not the 3D one.

## Files

| File | What it is |
|---|---|
| `Action-Aura-Brand-Guide.md` | the v2 guide itself, checked in for reference (v1's guide was never checked in here, only referenced by name) |
| `aura-mark.svg` | Retail's 3D mark, light-ground (== `aura-retail-light.svg`; kept under this older filename too so nothing that already references it 404s — Android's `MarkGeometryParityContractTest.kt` reads this exact path) |
| `aura-retail-light.svg` | same content as `aura-mark.svg` above, under the v2 kit's own naming convention |
| `aura-retail-dark.svg` | **new in v2.** Retail's 3D mark, dark-ground — real delivered artwork (shadow facet `#22304A`), replacing the old derived brightening |
| `aura-mark-on-dark.svg` | Retail's 3D mark, dark-ground (== `aura-retail-dark.svg`; kept under this older filename too — `retail_brand_ring_contrast_test.js` reads this exact path) |
| `aura-mark-1bit.svg` | Retail's FLAT/mono mark — navy letterform + one Retail-accent ring stroke, no gradients, no counter glyph. Reference/print export; **not** what `AuraIcons.mark()` renders inline for `size < 64` (that inlines `currentColor` + a token directly — see above), and not updated in this pass (no v2 source exists for it; it is Retail-specific, not part of the kit) |
| `aura-app-icon.svg` | 512px rounded-square app icon, navy ground, the dark-ground 3D mark centered inside — inner mark colours updated to `aura-retail-dark.svg`'s v2 palette. Consumed live as the browser-tab favicon by `index.html` and `customer-display.html`. See its own comment for why the guide's "delete the glow circles + shadow ellipse" app-icon recipe could not be mechanically applied — the delivered primary SVGs contain neither element |
| `aura-lockup.svg` / `aura-lockup-on-dark.svg` | mark + AURA / RETAIL wordmark, light / dark. **Not updated in this pass** — generated by `scripts/brand/lockup_to_paths.py` (not an owned path here), which itself still hardcodes pre-v2 mark geometry/colour; re-running it would not pick up this pass's changes either. Flagging as a gap rather than hand-patching a generated file out of sync with its own generator |
| `intro.html` | draws the mark's construction as a reveal animation; shadow facet corrected from `#223B5E` (a stand-in pulled from the v1 kit's dark master) to the v2 guide's official `#22304A` |
| `README.md` | this file |
| `aura-mark-light.svg` / `aura-mark-dark.svg` | the canonical, un-retitled **master** Action Aura identity (teal accent, no product counter), both surfaces — brought in verbatim from the v2 guide's own source files (metadata stripped — see below) |
| `aura-mark-flat.svg` | the master flat/mono mark, light-ground (teal ring) — content unchanged in this pass (already byte-identical to the v2 kit's own `aura-mark-flat-light.svg`); kept under this filename so nothing referencing it 404s |
| `aura-mark-flat-light.svg` | same content as `aura-mark-flat.svg` above, under the v2 kit's own naming convention, sitting alongside its new dark counterpart below |
| `aura-mark-flat-dark.svg` | **new in v2.** Master flat/mono mark, dark-ground (ivory letterform + teal ring) — v1 shipped no dark-ground flat master at all |

All SVGs here had the source files' embedded C2PA content-provenance
`<metadata>` block (≈8KB of base64 per file, plus an `xmlns:c2pa` attribute
on the root `<svg>`) stripped on import — verified, not assumed: the
ZIP-extracted v2 kit copies and the loose Downloads copies of the same files
were diffed after stripping and are geometrically identical, differing only
in that one inert namespace attribute. It carries no rendering-relevant
content, none of the previous-generation files in this directory had one,
and there is no reason to ship AI-content-credential metadata inside a
commercial product's shipped brand assets. Geometry and colour are otherwise
verbatim from the delivered sources.

## Known drift / not fixed here

- **`android/aura-retail/`'s `AuraMark.kt` still hand-draws the OLD
  (ring + upward A + beacon) geometry**, and
  `MarkGeometryParityContractTest.kt` reads `aura-mark.svg` in THIS
  directory to compare against it byte-for-coordinate. That test already
  failed before this pass (the 2026-09-19 redesign broke it) and still does
  — not fixed here: `android/` is a different owned surface.
- **`scripts/brand/lockup_to_paths.py`** (not under this directory) still
  hardcodes pre-v2 mark geometry/colour for the portion of `aura-lockup.svg`
  / `aura-lockup-on-dark.svg` it regenerates, and those two files were not
  hand-updated to v2 either (see the Files table above) — both are
  consistent with each other, just consistently behind.
- **`owner/app/static/img/brand/action-aura-mark.svg`** is a *separate* file
  (own copy, own directory, referenced from `owner/app/templates/layout/
  base.html`) carrying the old ring+A geometry. It is not the same asset as
  anything in this directory and was left untouched — Owner Control Center
  is a different team's surface.
- **`DESIGN.md`'s brand section** (root of the repo) documents the
  first-generation mark's exact construction numbers and file list in
  detail and is stale in the same way it once flagged `intro.html` as
  stale. Not editable from here — owned by another crew for this change.
- **`aura-mark-1bit.svg`** was not updated — no v2 source exists for a
  Retail-specific flat export, and `AuraIcons.mark()`'s actual flat branch
  does not read this file at runtime (see "The 64px rule" above), so nothing
  currently depends on its exact literal colours being current.
- Two test files that pinned pre-redesign mechanisms now fail, and were
  **not edited** to accommodate the redesign (both are outside this
  change's owned paths):
  - `products/retail/tests/retail_shell_chrome_test.js` —
    `testMarkStrokesCurrentColorAndUsesUniqueGradientIds` pins the
    first-generation A's literal `stroke="currentColor" stroke-width="26"`
    path and the old beacon diamond. The pierced-A mark is fixed-colour
    brand artwork, not a currentColor glyph.
  - `products/retail/tests/retail_brand_ring_contrast_test.js` — asserts
    `aura-mark-on-dark.svg` still contains the literal brand teal `#5fe3d0`,
    which the pierced-A mark never carried (Retail's own accent trio
    throughout, by design). Its other assertions (light-ground legibility,
    dark-ground brightness, the `--brand-ring-end` token read) still pass
    against the current files.
