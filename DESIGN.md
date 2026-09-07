# Aura — design brief

Written 2026-09-07 for anyone, human or AI agent, who designs for this product
without having seen it. Everything here is measured against the code as it is
on that date; where something is a plan rather than a fact, it says so. Read
`README.md` for what the software does and `CLAUDE.md` for how it is built;
this document is about how it should look, sound and feel, and why.

---

## 1. What you are designing for

**Aura** is Action Aura's product suite for small businesses in Jordan and the
region, sold and supported by a two-person company. Three products share one
identity:

| Product | Who uses it | Where it runs |
|---|---|---|
| **Aura Retail** | shop owners, managers, cashiers | a Windows laptop at the counter (the "till"), an Android phone in the owner's pocket, a second till in another branch |
| **Aura Clinic** | clinic owners, doctors, reception | same shapes as Retail |
| **Aura Owner Control Center** | Action Aura's own staff | a web app for licensing, billing, customers, CRM — internal, not customer-facing |

Realities that shape every design decision:

- **Money is the point.** A wrong digit costs someone real dinars. Amounts
  are Jordanian dinar with **three** decimals (`JD 12.345`, the fils). The
  total is the largest thing on the till screen and it is never decorated.
- **Cheap hardware, bad light.** Tills are inexpensive Windows laptops with
  high-contrast panels; the phone is a mid-range Android. Pure black plus
  light text halates on those panels, so dark themes never use `#000000`
  grounds and never `#FFFFFF` text. Shops are bright at noon and dim at
  night; stockrooms are dim always.
- **Two languages, two directions.** English and Arabic, switchable at any
  time, and Arabic mirrors the entire layout (right-to-left). Every visible
  string goes through the catalog (`t()` on the desktop, `tr()` on the
  phone); names typed by the shop (products, methods, categories) are data
  and are shown as typed.
- **Several devices, one shop.** A sale on the phone appears on the laptop
  within seconds. The brand line is *One shop. Every device.*
- **A cashier and an owner see different things.** Cashiers sell; managers
  and owners see totals, reports and settings. When the server refuses
  something, the screen says so honestly ("Ready to sell — totals are for
  managers and the owner"), never paints a zero over it, and never shows a
  button that can only fail.
- **Invisible unless opted in.** Features default off and cost nothing to a
  shop that does not use them (e-invoicing, sync, notifications). Design the
  same way: nothing that demands attention it has not earned.

## 2. The design philosophy: Operational Calm

The product went through a deliberate redesign that rejected the
"near-black HUD with neon accents" look as fatiguing and untrustworthy for
software that moves money. What replaced it, and what every new surface must
keep:

1. **One accent, used semantically.** The accent means *this is the action
   you take*. It is never decoration, never a second colour "for variety".
2. **Colour carries meaning.** Green is money in and success; red is money
   out, refusal and danger; amber is a warning; blue is information. Each
   meaning has one colour, and no meaning shares a colour with another.
3. **Calm surfaces, elevation by light.** Grounds are cool greys (or warm
   paper in Sand). Elevation reads the way daylight does: the surface the
   cashier works on is the brightest in dark themes and the whitest in light
   ones; chrome recedes; wells sink.
4. **Hierarchy by scale, not by boxes.** A type scale of 11 / 12 / 14 / 15 /
   17 / 22 / 30 / 40 px does the work. Do not draw another border to make
   something important.
5. **Honest states.** Loading shows a skeleton, refusal shows the reason,
   failure says "could not load", and nothing ever displays a default value
   as if it were a measurement.
6. **Contrast is computed, not eyeballed.** Every theme is solved with the
   WCAG formula and pinned by tests (see §8). Text on any surface it can
   land on is at least 4.5:1; money is at least 7:1; the label on an accent
   fill is at least 4.5:1.
7. **Nothing decorative in the money path.** The cart, the total, the
   tender buttons and the receipt are the last places for flourish.

## 3. The brand

**Name:** Aura. Company: Action Aura. Product lines are written in caps
under the wordmark: RETAIL, CLINIC, OWNER.

**The mark.** A ring that has just been lit, open at the top-right where a
spark sits, around an upward A with a short bar. The ring is the aura and
the sync loop; the gap and the spark make it "switched on" rather than a
badge; the A is plain ink so it survives one-colour printing and a 16 px
favicon. Geometry, in a 256 × 256 viewBox:

- ring: circle centre (128,128), r 94, stroke 15, round caps,
  `stroke-dasharray 492 99`, `stroke-dashoffset -32`, rotated −90°
- spark: centre (196,60), white core r 6.5, radial glow r 20–26
- A: `M 80 178 L 128 76 L 176 178` stroke 19; bar `M 106 142 L 150 142`
  stroke 15; round joins and caps
- ring gradient (light surfaces): `#1745A9` → `#3F7BE6` → `#5FE3D0`
  (bottom-left to top-right); on dark surfaces `#3F7BE6` → `#6EA8FF` →
  `#5FE3D0`; A ink `#0F1319` on light, `#EDF2F8` on dark

**Brand colours** (not to be confused with product tokens, §4):

| Name | Hex | Role |
|---|---|---|
| Ink blue | `#1745A9` | the ring's start; also the Day theme's action colour |
| Aura blue | `#3F7BE6` | the ring's middle |
| Aura teal | `#5FE3D0` | the ring's end, the spark, the Night theme's action colour |
| Night ground | `#070B12` | the intro's and app icon's ground |
| Calm ground | `#0F1319` | the phone's default dark |

**Wordmark and type.** AURA in **Outfit** 700, tracking 0.16 em (0.55 em
while animating in); product line in Outfit 300, tracking 0.5 em. Body copy
in brand material: **Inter Tight**. The lockup SVGs carry the wordmark as
outlines, so no font is required to render them.

**Tagline:** *One shop. Every device.*

**The intro:** about six seconds — the ring draws itself, the spark lights at
the opening, the A is drawn, the wordmark settles from wide tracking, the
tagline fades in. It honours `prefers-reduced-motion` by showing the final
frame. It is one self-contained HTML file.

**Rules.** Clear space of one ring-stroke width on every side. Minimum
size: mark 16 px, lockup 120 px wide. One-colour use: ink on light, off-white
on dark; the gradient is optional, the gap and the spark are not. Never close
the ring, move the spark, rotate the mark, or put text inside it.

**Files:** `products/retail/frontend/brand/` — `aura-mark.svg`,
`aura-mark-on-dark.svg`, `aura-app-icon.svg` (512, rounded square),
`aura-lockup.svg`, `aura-lockup-on-dark.svg`, `intro.html`, `README.md`.
Tools: `scripts/brand/lockup_to_paths.py` (wordmark → outlines),
`scripts/brand/theme_palettes.py` (palette solver, §4).

**Status:** first cut, 2026-09-07, published for the owner's review. Not yet
wired into any screen: the sign-in screens still show an older bolt glyph
and the desktop shell a bag icon.

## 4. Colour: the token system and the five themes

### 4.1 The rule that governs everything

A token is named for **what it is for**, never for what it looks like.
`--surface-till` is "the surface the cashier works on", not "white". A
theme is a block of token **values only**; there is no rule anywhere that
applies to one theme and not another. That single discipline is what makes
five themes safe where the first dark theme was killed for drift.

### 4.2 The tokens

Surfaces, by elevation: `--surface-app` (the window ground) →
`--surface-panel` (sidebar, header, tab bar) → `--surface-raised` (cards) →
`--surface-till` (**the** working surface: cart, active list) →
`--surface-hover` → `--surface-active` (pressed / selected). `--surface-sunken`
is anything you type into. `--surface-accent-soft` is a tinted backdrop for
the selected navigation row.

Text: `--text-primary` (headings, values), `--text-secondary` (body, labels),
`--text-tertiary` (meta, timestamps), `--text-on-accent` (only on accent
fills). Money: `--text-money`, `--text-money-positive` (in), `--text-money-negative` (out).

Accent: `--accent-action`, `-hover`, `-active`. States, each with `-text`,
`-surface`, `-border`: success, warning, danger, info. Borders:
`--border-hairline` (row rules), `--border-default`, `--border-strong`.
Focus: `--focus-ring-color`, `--focus-ring-halo`.

### 4.3 The five themes, exact values

Keys are what the apps persist (`aura_theme_v2` on the desktop, `app_theme`
in the phone's preferences). Day and Calm predate 2026-09-07; Sand, Night and
Dusk were added that day. "Calm" is the phone's original and default look.

| token | Day `light` | Sand `sand` | Calm `dark` | Night `night` | Dusk `dusk` |
|---|---|---|---|---|---|
| `--surface-app` | `#EAEEF3` | `#EFE8DC` | `#0F1319` | `#070B12` | `#13111C` |
| `--surface-panel` | `#FFFFFF` | `#FBF7F0` | `#151A23` | `#0B111B` | `#191626` |
| `--surface-till` | `#FFFFFF` | `#FFFDF8` | `#1A212C` | `#111A27` | `#211D31` |
| `--surface-raised` | `#F8FAFC` | `#FAF6EE` | `#171D27` | `#0E1520` | `#1C192A` |
| `--surface-sunken` | `#F2F5F8` | `#F4EEE4` | `#0B0F15` | `#04070C` | `#0E0C16` |
| `--surface-hover` | `#EEF2F7` | `#F1EADF` | `#212936` | `#172233` | `#29253D` |
| `--surface-active` | `#E6EAF0` | `#E8E0D2` | `#273140` | `#1D2B3F` | `#312C49` |
| `--surface-accent-soft` | `#E8EEFB` | `#F6E7D3` | `#1C2A44` | `#0F2A30` | `#2A2350` |
| `--text-primary` | `#141A24` | `#2A2119` | `#EDF2F8` | `#E9F1FB` | `#F0EDF9` |
| `--text-secondary` | `#3D4859` | `#4D4034` | `#C3CDDB` | `#BFCBDB` | `#C9C3DC` |
| `--text-tertiary` | `#566071` | `#63564A` | `#9FADC0` | `#9AAABD` | `#A49DBD` |
| `--text-on-accent` | `#FFFFFF` | `#FFFFFF` | `#0D1B2E` | `#04201D` | `#150F2E` |
| `--text-money` | `#141A24` | `#2A2119` | `#EDF2F8` | `#E9F1FB` | `#F0EDF9` |
| `--text-money-positive` | `#0A5832` | `#1A502E` | `#8FE6B3` | `#8FE8BD` | `#95E6B6` |
| `--text-money-negative` | `#98170F` | `#891B12` | `#FFB3A8` | `#FFB0A6` | `#FFB0A6` |
| `--accent-action` | `#1745A9` | `#9A4F12` | `#6EA8FF` | `#5FE3D0` | `#B9A6FF` |
| `--accent-action-hover` | `#123A8E` | `#84420D` | `#85B7FF` | `#7CEBDB` | `#C9BAFF` |
| `--accent-action-active` | `#0E2F74` | `#6D3609` | `#5B9BF8` | `#49D2BF` | `#A793F6` |
| `--state-success-text` | `#0A5832` | `#1D5A34` | `#7BD9A2` | `#7FDFA9` | `#86DFA8` |
| `--state-success-surface` | `#E7F4ED` | `#E5F1E6` | `#12301F` | `#0F2D1F` | `#132D22` |
| `--state-success-border` | `#B6DCC7` | `#B3D6BB` | `#1E4D33` | `#1C4A33` | `#1F4A36` |
| `--state-warning-text` | `#6E4300` | `#6E4300` | `#E6C67A` | `#E9C97E` | `#EBC97F` |
| `--state-warning-surface` | `#FBF1E0` | `#F8EDD6` | `#33270E` | `#30250D` | `#332711` |
| `--state-warning-border` | `#E8CFA2` | `#E3C896` | `#57431A` | `#544119` | `#57431C` |
| `--state-danger-text` | `#98170F` | `#9A1F14` | `#FF9D94` | `#FF9B92` | `#FF9D94` |
| `--state-danger-surface` | `#FDECEA` | `#F9E6E1` | `#3D1713` | `#3B1512` | `#3E1717` |
| `--state-danger-border` | `#F2C4BF` | `#EFC0B8` | `#66261F` | `#63241E` | `#672622` |
| `--state-info-text` | `#1745A9` | `#1C4A9E` | `#8AB5F8` | `#8FBAFF` | `#9DBCFF` |
| `--state-info-surface` | `#E8EEFB` | `#E6ECF7` | `#14243D` | `#12223C` | `#16233F` |
| `--state-info-border` | `#BFD0F0` | `#BCCBE8` | `#24406B` | `#233F6A` | `#27406D` |
| `--border-hairline` | `#E3E8EF` | `#E4DCCF` | `#232B37` | `#1A2432` | `#26223A` |
| `--border-default` | `#D3DAE3` | `#D4CABB` | `#2E3947` | `#26344A` | `#34304C` |
| `--border-strong` | `#B3BFCD` | `#B7AB99` | `#47566A` | `#3F5271` | `#4D4870` |
| `--focus-ring-color` | `#1745A9` | `#9A4F12` | `#6EA8FF` | `#5FE3D0` | `#B9A6FF` |

Character of each theme, for choosing and for extending:

- **Day** — the default. Cool grey shell, white till, ink-blue action.
- **Sand** — warm paper for shops that find cool grey clinical; amber-brown
  ink as the action colour; the same structure as Day.
- **Calm** — the phone's original dark and the desktop's dark: blue-grey
  ground, light-blue action, off-white text. "Same product, lights off."
- **Night** — deep ink with the brand's aurora teal as the action colour;
  the elevation lightens more steeply so cards read on a very dark ground.
- **Dusk** — violet charcoal with a lavender action colour; the warm
  counterpart to Night.

The phone holds the same five as `AuraColors` palettes and its parity test
compares every value to the desktop block of the same name; a one-digit
drift on either side fails the build.

### 4.4 Adding a theme (the recipe)

1. Design it as a full token set — every token in the table, no gaps.
2. Solve contrast with `scripts/brand/theme_palettes.py`: text tokens ≥ 4.5:1
   on **every** surface (`app`, `panel`, `till`, `raised`, `sunken`,
   `hover`, `active`, `accent-soft`); `--text-money-*` ≥ 7:1; each
   `state-*-text` ≥ 4.5:1 on its own `state-*-surface`; `--text-on-accent`
   ≥ 4.5:1 on all three accent values. In dark themes the accent is *light*
   and `--text-on-accent` is near-black (a single dark accent cannot both
   carry light text and be readable as text); in light themes the accent is
   an ink that takes white.
3. Desktop: add one `html[data-theme="<key>"] { … }` block to
   `products/retail/frontend/css/main.css` between
   `[design-tokens-<key>:begin]` / `:end]` markers placed in comments exactly
   like the existing blocks; add the key to `THEME_NAMES` in `app-shell.js`
   and to `AURA_BOOT_THEME_NAMES` in `index.html`; add the swatch (label,
   `dot`, `edge`) to `ThemeEngine.themes`; add the label to both locale
   catalogs.
4. Phone: add an `AuraColors` palette in `ui/theme/Color.kt`, list it in
   `AuraPalette.ALL`, add the label to `Strings.kt`.
5. Run the tests in §8. Then load the real page in a browser and the real
   app on a handset; screenshots of the dashboard and the POS in the new
   theme are the definition of done.

Never: a theme-scoped paint rule (`html[data-theme="x"] .foo`), a colour
literal outside the token block or `Color.kt`, a second allowlist, a
`#000000` ground or `#FFFFFF` text in a dark theme.

## 5. Typography

- **Desktop UI:** Plus Jakarta Sans (bundled woff2), fallback system sans.
  Mono: `ui-monospace, Cascadia Code, Consolas`. Scale: 11 / 12 / 14 / 15 /
  17 / 22 / 30 / 40 px; the 40 is the total a customer reads from a metre
  away. Uppercase labels get a touch of letter-spacing; tabular numerals
  wherever digits align.
- **Phone UI:** the same scale on the system sans (Compose cannot consume
  the web's woff2; shipping a TTF is a known gap, not a decision).
- **Brand material:** Outfit (wordmark, headings) and Inter Tight (copy).
- **Arabic:** currently the system Arabic fallback on both clients. Choosing
  and bundling an Arabic face that pairs with Plus Jakarta Sans is open
  design work (see §9).

## 6. Shape, space, motion, sound

- Radii: controls 8 px, cards 12 px, pills 999 px.
- Elevation: `--elevation-card` `0 1px 2px rgba(20,32,60,.06), 0 4px 12px
  rgba(20,32,60,.07)` in light themes; the dark themes use black shadows at
  .55 / .45. Panels and modals scale up from there. Elevation is spent by
  role: one thing lifted, not every block.
- Motion: fast 160 ms, slow 380 ms, easing `cubic-bezier(.4,0,.2,1)`.
  Animate `transform` and `opacity`; never layout properties. Reduced motion
  is respected everywhere; the intro shows its final frame.
- Sound: one scanner beep (optional, in settings). Nothing else.
- Layout: desktop sidebar 260 px, header 70 px; the POS is a two-column
  screen — products left, the cart and total right. The phone has five
  bottom tabs: Dashboard, Products, POS, Customers, More; the cart is a
  bottom sheet. In Arabic everything mirrors, including the tab order.

## 7. Icons and illustration

Icons are the **AuraIcons** set (`products/retail/frontend/icons.js`), a
curated port of Lucide: 2 px strokes, round joins, monochrome, tinted by the
current text or accent token. A few emoji remain in older chrome and are
being replaced. There is no illustration style yet (see §9).

## 8. The tests that hold the design, and what they can and cannot see

Run these before calling any visual change done (all from
`products/retail/tests`, node unless stated):

| Test | Pins |
|---|---|
| `retail_design_theme_safety_test.js` | every theme is a token block only; every light colour token redefined in every theme; one sanitizer over one frozen allowlist; the boot script mirrors it under a different global name; picker offers exactly the five |
| `retail_design_contrast_test.js` | for every theme: the palette cross-products (AA text on surfaces, AAA money, on-accent) and the whole rendered corpus re-resolved through that theme's map (43 checks) |
| `retail_design_tokens_test.js` | no colour literals outside the token layer (with a short, documented exemption list) |
| `retail_surface_i18n_test.js`, `retail_localization_test.py` | every `t()` string exists in both catalogs; Arabic values are real |
| Android `ColorTokenContractTest`, `DesktopTokenParityContractTest`, `ThemeChoiceContractTest` | no colour literal outside `Color.kt`; each palette equals its desktop block; the picker and persistence are wired |

What none of them can see: a page that fails to boot because two scripts
collide, a screen that renders the right tokens in the wrong place, a
cashier shown a control the server refuses. Those were all found in 2026 by
loading the real page in Chromium and walking the real phone. **Budget a
real-screen walk for every visual change.** Drivers exist:
`scripts/ops/laptop_ui/` (Playwright) and `scripts/ops/phone_ui.py`
(uiautomator).

## 9. Design work still open, in priority order

1. **Put the mark in the product.** Sign-in and first-run screens on both
   clients (they show an older bolt glyph), the desktop shell's header (a
   bag icon), the browser favicon, the Windows installer icon, the Android
   adaptive icon (foreground/background layers from `aura-app-icon.svg`).
   Waits for the owner's yes on the mark.
2. **The intro as a splash.** Desktop launcher (pywebview) and Android cold
   start; must not delay a cashier who is already signed in — play once per
   install, or only on first run.
3. **Receipt and print.** A receipt template (thermal 58/80 mm and A4) that
   carries the mark in one colour, the fils, Arabic and English, and the
   QR for e-invoicing when enabled.
4. **Messages the shop sends.** Email (SMTP notifications exist) and
   WhatsApp templates for low stock, shift close, daily summary and overdue
   receivables, in both languages; later, per-shop templates when each shop
   has its own WhatsApp number.
5. **Aura Clinic's identity.** Same shell and tokens; decide whether Clinic
   gets its own accent within the same rules or stays on the shared one; a
   CLINIC lockup exists by construction.
6. **Owner Control Center.** A separate stack today with its own look; bring
   it onto the same tokens and themes.
7. **An Arabic typeface** paired with Plus Jakarta Sans, bundled on both
   clients; Arabic numerals policy (Western digits on money is the current
   rule).
8. **Empty states and onboarding.** A small illustration language (line
   work in the accent, one weight, no gradients) for empty lists, first
   run, and the join-a-shop flow; a short guided tour for a new cashier.
9. **Marketing.** A one-page site and a vendor one-pager (POS vendors are a
   sales channel) using the brand, the five themes as a feature, and the
   intro as the hero.
10. **More palettes, only if asked for.** Candidates that fit the rules:
    *Forest* (deep green ground, mint action), *Rose* (warm dark with a
    coral action — check it never collides with danger red), *High
    contrast* (Day with black text and heavier borders). Every new palette
    goes through §4.4.

## 10. How to hand work back

- Colours as token tables (hex, one column per theme), never as a
  screenshot of a palette.
- Vectors as SVG with outlines, no embedded fonts, one artboard per file;
  raster at 1× and 2×.
- Screens as the real thing where possible (a branch that renders), else as
  static mockups **built on the token names**, so the reviewer can tell
  which token each colour is.
- Copy in English with the Arabic alongside; every new string is a catalog
  key.
- Say what you measured. A page you loaded, a contrast you computed, a
  device you held — and what you did not.
