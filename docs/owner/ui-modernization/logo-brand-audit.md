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

## Summary for the modernization phase

Aura Owner currently has **no logo, no favicon, no brand image asset of
any kind, and no formal brand guideline**. Its only visual identity today
is: a text wordmark ("Aura Owner Control Center") in the top bar, a
five-token CSS color system (`--accent #2452b8`, `--danger #b3261e`,
`--border #d8dee4`, `--muted #6b7785`, `--bg #f6f8fa`) plus two
un-tokenized chrome colors (`#101826` top bar, `#16202f` nav strip), and
a system-font stack with a Noto Sans Arabic fallback. None of this is
sourced from, or traceable to, any documented Action Aura brand standard
— the two sibling Android products (Clinic, Retail) each have their own
separate, non-matching visual identity and neither shares a color with
Owner. Per the task's ground rules, no new logo or brand color is
proposed here — the modernization phase should treat "define the actual
Action Aura brand" (or explicitly decide Owner keeps its own
independent, non-branded identity) as an open decision to make with the
product owner, not a gap this audit can fill by invention.
