# Owner App — Logo & Brand Asset Audit (Stage A)

Read-only audit of real Action Aura logo/brand assets present in this
repository, produced ahead of the UI/UX modernization phase. This
document reports only what exists today — no logo, brand color, or brand
guideline is invented or proposed here.

## 1. Logo/favicon/brand image files: none exist for Owner

An exhaustive search of the entire repository for image assets and for
filenames containing "logo", "favicon", or "brand" found **zero logo,
favicon, or brand image assets anywhere for the Aura Owner app**:

```
find . -iname "*logo*" -o -iname "*favicon*" -o -iname "*brand*"
find . -type f \( -iname "*.svg" -o -iname "*.png" -o -iname "*.ico" \
  -o -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.webp" -o -iname "*.gif" \)
```

`owner/app/static/` contains exactly two files, both JavaScript, no
images of any kind:
```
owner/app/static/js/confirm.js
owner/app/static/js/geolocation-capture.js
```
(confirmed via `find owner/app/static -type f`). There is no
`owner/app/static/img/`, `owner/app/static/images/`,
`owner/app/static/icons/`, or `owner/app/static/assets/` directory at
all — `owner/app/static/` has only the single `js/` subdirectory.

`owner/app/templates/layout/base.html` — the single shared page shell
every screen extends — contains **no `<link rel="icon">`,
`rel="apple-touch-icon"`, or any favicon reference at all**
(`grep -n "favicon\|rel=\"icon\"\|apple-touch" owner/app/templates/layout/base.html`
→ no matches) and **no `<img>` tag referencing a logo anywhere in the
header** (`base.html:73-90`). The "brand" element in the top bar is text
only:
```html
<!-- owner/app/templates/layout/base.html:74 -->
<div><a class="brand" href="{{ url_for('dashboard.index') }}">{{ _('Aura Owner Control Center') }}</a></div>
```
styled purely via the `.brand` CSS rule (`base.html:13`:
`header.topbar a.brand { font-weight: 700; font-size: 1rem; }`) — bold
text, no image, no wordmark graphic. Browsers will show a blank/default
tab icon for every Owner page since no favicon is declared and none
exists to declare.

The only "Action Aura" or brand-name text found in the Owner application
code is plain-string copy embedded in error/notification messages, not a
visual asset — e.g. `owner/app/commercial_ops/state_resolution.py:138`
("Contact Action Aura support to review revocation.") and
`owner/app/models/commercial_ops.py:267` (a docstring describing "the
internal Action Aura notification center"). Neither is a logo or brand
mark.

## 2. Assets found elsewhere in the repo (not applicable to Owner)

The repository also contains two unrelated Android product apps —
`android/aura-clinic/` and `android/aura-retail/` — which are **separate
products from Aura Owner**, not shared brand assets:
```
android/aura-clinic/app/src/main/res/drawable/splash_logo.xml
android/aura-retail/app/src/main/res/drawable/splash_logo.xml
android/aura-clinic/app/src/main/res/drawable-xxxhdpi/ic_launcher_foreground.png
android/aura-retail/app/src/main/res/drawable-xxxhdpi/ic_launcher_foreground.png
```
These are each app's own Android launcher icon/splash asset (per-product,
Clinic and Retail respectively), located under each app's own
`res/drawable*` directories — not under any shared/common asset location,
and not referenced by, or reachable from, `owner/` in any way. Each
Android product also defines its own independent Material theme
(`android/aura-clinic/.../ui/theme/Theme.kt`,
`android/aura-retail/.../ui/theme/Theme.kt`, plus each product's own
`res/values/colors.xml` and `res/values/themes.xml`). These color
definitions were checked against Owner's own accent color
(`--accent:#2452b8`, see §3) and **do not match** — there is no shared
"Action Aura" brand color propagated between the Android products and
the Owner web app. In short: **no asset in this repository is a
cross-product Action Aura brand mark** — each product (Clinic, Retail,
Owner) currently has its own, independently-chosen visual identity, and
Owner's is text-only with no logo at all.

## 3. Colors currently used as the de facto "brand" palette in Owner

Owner has no declared brand palette, but it does have one small,
consistently-used color system that functions as its de facto visual
identity — five CSS custom properties defined once in
`owner/app/templates/layout/base.html:8`:

| Token | Hex | Where it's used |
|---|---|---|
| `--accent` | `#2452b8` | Primary buttons (`.btn`, `base.html:32`), links in the language switcher, focus/hover ring on `.crm-card` (`base.html:67`) — this is the closest thing Owner has to a "brand blue" today. |
| `--danger` | `#b3261e` | Destructive buttons (`.btn.danger`, `base.html:33`), error banners (`.error`, `base.html:35`). |
| `--border` | `#d8dee4` | Card/table/input borders throughout. |
| `--muted` | `#6b7785` | Secondary/label text color. |
| `--bg` | `#f6f8fa` | Page background. |

Separately, the top bar and nav strip use two more hardcoded (not
tokenized) dark colors that function as the app's "chrome" color, but are
never named as variables:
```css
/* owner/app/templates/layout/base.html:11,17 */
header.topbar { background: #101826; color: #fff; ... }
nav.tabs { ... background: #16202f; ... }
```
`#101826` and `#16202f` (near-black navy) are the top bar and nav-strip
backgrounds respectively — these are the two most visually prominent
colors on every single page of the app (they wrap every screen), and
they are exactly the kind of color a formal brand guideline would
usually specify explicitly, but neither is declared as a `:root` custom
property nor documented anywhere as an intentional brand color — they
are one-off literals in `base.html`'s stylesheet, same as `--accent`'s
underlying value is a literal reused nowhere else via `var()` outside
`base.html` itself (see `current-ui-audit.md` §2 and §7 for the fuller
color-usage audit, including the confirmed instances where these same hex
values get hand-retyped in other templates instead of referenced as
variables).

No other hex value repeats often enough across the codebase to read as
an intentional secondary/tertiary brand color — the `.badge` status
colors (`#dcf3e2`/`#1c6b34` success, `#fbeecb`/`#8a5c00` warn,
`#fbdcda`/`#8a2119` danger, all in `base.html:26-28`) are semantic status
colors, not brand colors, and are used consistently only for that
purpose.

## 4. Logo variants (dark-background / light-background / monochrome)

**Not applicable — there is no logo file of any kind to have variants
of.** Since no logo asset exists (§1), there is consequently no
dark-background variant, light-background variant, or monochrome/single-
color variant either. The current "brand mark" in the top bar
(`header.topbar`, background `#101826`) is white text (`color: #fff`,
`base.html:11`) on a dark background — functionally a de facto
"dark-background wordmark," but it is live HTML text (the literal string
"Aura Owner Control Center," `base.html:74`), not an image asset, so it
cannot be assessed for logo dimensions, export format, or
light-background contrast the way a real logo file would be.

## 5. Logo dimensions

**Not applicable** — there is no SVG, PNG, or other logo file in the
repository to inspect for dimensions, viewBox, or aspect ratio.

## 6. Existing brand guideline documentation

**No formal brand guideline document exists anywhere in this repository.**
A search of every `docs/` subdirectory for brand-guideline-shaped content
(`grep -rli "brand guideline\|brand identity\|style guide\|design
system" docs`) returned only two unrelated hits, neither of which is a
brand guideline:
- `docs/owner/phase9_5b_r/translation-style-guide.md` — a
  localization/translation writing-style guide (terminology, tone for
  Arabic/English copy), not a visual brand guideline.
- `docs/owner/phase9_5b_r/localization-execution-plan.md` — an execution
  plan for the localization effort, not a brand guideline.
- `docs/audit/16-commercial-readiness-audit.md:10-12` mentions
  "branding maturity across platforms" and references the Android app's
  "branded 'Action Aura' wordmark" in its `LoadingScreen()` composable —
  this is a passing observation inside a *commercial readiness* audit
  document, not a brand guideline itself, and it explicitly describes the
  Android product's own asset, not anything belonging to Owner.

No `BRAND.md`, `brand-guidelines.md`, `style-guide.md`, design-token
specification, or logo-usage document exists anywhere under `docs/` for
Owner or for the Action Aura product suite as a whole.

## 7. Real logo provided by the Product Owner (2026-08-05)

Per the acceptance decision recorded against this gap (§8's "Summary"
below no longer applies as originally written — see the update there),
the Product Owner supplied the real Action Aura brand mark directly for
this phase, with explicit direction to "use as a base and be creative."

**Description of the real mark** (a circular emblem, provided as a raster
image, not yet committed as a repo file — see "Asset file: outstanding"
below):

- A near-black circular badge (background approx. `#0A0A0C`), with the
  wordmark split across two lines inside: **"ACTION"** in a warm
  amber/gold gradient (approx. `#F2A93B` → `#E8871E`), and **"AURA"**
  in solid white (`#FFFFFF`), bold condensed sans-serif, all-caps.
- The badge is ringed by a full-spectrum conic gradient (blue → violet →
  magenta → orange → amber → green → teal, sweeping the full circle),
  with fine circuit-board-style traces radiating outward from the ring
  into the dark background, in matching per-segment hues, fading to
  transparent at the canvas edge.
- Square canvas, circular mark centered, generous dark clear space on
  all sides already built into the composition.

**Real, usable brand colors extracted from this mark** (visually
sampled — no image-editing/color-picker tool was available to sample
exact pixels programmatically; values are close approximations of the
mark's real colors, not invented):

| Role | Hex (approx.) | Source in the mark |
|---|---|---|
| Brand blue (primary candidate) | `#2D5FE0` | The blue arc of the conic ring — and notably close to Owner's own existing `--accent: #2452b8` (§3), meaning the app's current de facto brand color already aligns with the real logo's dominant cool hue rather than conflicting with it. |
| Brand amber (secondary candidate) | `#F0A030` | The actual "ACTION" wordmark color inside the mark itself — a real, intentional, named color in the logo, not sampled from the decorative ring. |
| Brand violet (accent candidate) | `#8B5CF6` | The violet/magenta arc of the conic ring — a distinct third hue for sparing decorative accent use only. |
| Mark background / deep surface | `#0A0A0C` | The badge's own near-black fill — a real reference point for the darkest dark-theme surface (not pure `#000000`, per the task's own §28 requirement that dark theme "must not be pure black everywhere"). |

**Design tension, resolved deliberately, not accidentally:** the mark's
full rainbow conic ring is correct and effective as a *badge/emblem* —
favicon, login hero, first-login welcome screen — but is explicitly the
kind of "heavy gradient," "neon," multi-hue treatment §7 of the main
task spec prohibits from ordinary UI chrome ("Avoid: neon colors,
glowing borders, heavy gradients on every card... 'Dribbble-only'
designs that are impractical in real operations"). The creative
direction taken: extract **one calm primary (blue)** and **one
distinctive secondary (amber, from the real wordmark)** for everyday UI
(buttons, links, focus rings, nav highlight), reserve the **full
multi-hue ring** for a small number of high-impact brand moments only
(favicon, login/security screens, first-login welcome hero — see
`design-token-system.md` and `light-dark-theme-contract.md`), and never
paint ordinary tables, cards, or buttons with the rainbow gradient.

**Asset file: outstanding.** This document records the real colors and
composition described above, but the actual image file itself has not
yet been committed to the repository — no binary-file-write tool was
available to persist the pasted image directly. Target path once
provided: `owner/app/static/img/brand/action-aura-mark.png` (directory
already created). Until that file exists, the favicon and any literal
`<img>` logo usage will reference this path but render broken — token
and color work in `design-token-system.md` does not depend on the file
existing and proceeds now; the icon-usage acceptance gate is not closed
until the real file is in place.

## Summary for the modernization phase (updated 2026-08-05)

As originally audited, Aura Owner shipped with **no logo, no favicon, no
brand image asset of any kind, and no formal brand guideline** — only a
text wordmark, a five-token CSS color system (`--accent #2452b8`,
`--danger #b3261e`, `--border #d8dee4`, `--muted #6b7785`,
`--bg #f6f8fa`) plus two un-tokenized chrome colors (`#101826` top bar,
`#16202f` nav strip), none traceable to any documented Action Aura brand
standard, and not shared with the sibling Android products.

**This gap is now resolved for the purposes of this phase**: the
Product Owner provided the real Action Aura brand mark directly (§7,
2026-08-05), with explicit direction to use it as a base and apply
creative judgment for how it maps onto a restrained enterprise UI. The
real, extracted colors (§7's table) — brand blue `#2D5FE0` (aligned with
Owner's pre-existing `#2452b8` accent), brand amber `#F0A030` (the real
"ACTION" wordmark color), brand violet `#8B5CF6` (ring accent), and near-
black `#0A0A0C` (mark background) — are the actual source values for
`design-token-system.md`'s `brand-primary`/`brand-secondary`/
`brand-accent` tokens. No further invention needed. Only the literal
image file's placement in the repo remains outstanding (§7).
