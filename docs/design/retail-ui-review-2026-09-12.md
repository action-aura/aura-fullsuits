# Aura Retail — UI/UX review, 2026-09-12

Method, findings, what was fixed, and what was deliberately left. Measured
against `docs/design/retail-ui-standards-research.md` (the standards study) and
`DESIGN.md` (the product's own brief).

## How this was done

By **running the product and looking at it**, then by auditing the code for the
specific things looking had raised. Not by reading code and reasoning about it —
that method had already failed several times in this repo, and failed twice more
during this review (two layout "fixes" written from reading CSS changed nothing
on screen; the third, written from a measurement, worked).

Concretely:

- A fresh install booted on a real Chromium, signed up through the real
  first-run form, navigated by clicking the real sidebar, screenshotted on every
  screen, in all five themes, in English and Arabic.
- Element geometry read out of the live DOM (`getBoundingClientRect`,
  `getComputedStyle`, `elementFromPoint`) rather than inferred from stylesheets.
- Four parallel code audits on questions the screenshots raised: money colour,
  Arabic typography, theme token integrity, overlay stacking.

Every defect below is user-visible. **None of the 68 JavaScript tests could see
any of them beforehand** — which is the point worth keeping: this suite is good
at invariants and blind to appearance.

## The headline

**The Charge button — the control that takes money — was off screen on every
unlicensed till.** At 1366×768 (the most common cheap POS panel) with the
licence banner showing, it rendered at y 739–792 in a 768px viewport.

An unlicensed till is not an edge case. It is every shop's first hour, before
the key is activated.

Cause: `.pos-wrap` sized itself `calc(100vh - 120px)` — it measured the
**viewport** while sitting 161px down the page, and hard-coded the chrome above
it as 120px. Even with no banner it cleared by 3px, which is why the first thing
ever added to that screen broke it.

    before   1366x768, banner up    Charge bottom 792   fullyVisible false
    after                           Charge bottom 731   fullyVisible true

It now sizes from its scroll container, so the measurement is identical with and
without the banner: the layout self-corrects for whatever chrome appears above.

## Fixed

### The money path

| What | Was | Now |
|---|---|---|
| Charge button | Off screen on unlicensed tills | Pinned and visible; banner-independent |
| Tender buttons | Second row (Transfer/Credit/Voucher) behind the sticky Charge button, or clipped away | All six reachable at rest, worst case |
| Refund amount | Red with **no minus sign** — colour was the only cue | Real U+2212 minus + accounting parentheses + colour |
| Gross Profit | Hardcoded green; a **loss rendered green** | Three states; a loss is red, signed and bracketed |
| Chart axes | "JD 0.8" beside a KPI reading "JD 0.000" | "JD 0.800" and "JD 1,200" — one precision per screen |
| Chart legend | "Revenue ($)" in a dinar product | Built from the live currency mark |
| Zero day-over-day | Green ▲ — a flat day reported as growth | Neutral, its own state |

The refund one is the sharpest. `refund_amount` is stored as a positive
magnitude (`create_return` never negates it; `metrics.py` subtracts it
elsewhere), so the formatter emitted no sign and red was doing all the work.
That is WCAG 1.4.1 Level A, and it is invisible on the cheap monitors these
tills run on. The product's own CSS says, in as many words, that colour must be
the redundant cue here — one inline style bypassed the class that enforces it.

### Themes

**The login screen was built for two themes, and there are five.**
`.auth-overlay`/`.auth-card` painted a near-black ground with an
`html[data-theme="light"]` override for Day. That selector names one theme.
**Sand is the other light theme** — it fell through to the near-black default,
so every shop on Sand met a black backdrop behind a warm-paper card on the very
first screen the product shows. Night and Dusk wore Dark's ground.

Both rules were **on the theme-safety test's allowed list**, exempted by a
comment reading "deliberately stays dark in BOTH THEMES" — true when written,
false once the themes went from two to five. The exemption did not merely fail
to catch this; it is what permitted it.

Fixed as tokens (`--surface-auth-ground`, `--elevation-auth-card`) in all five
theme blocks, which puts them under the parity check. Light and Dark keep their
exact previous values — the two themes anyone had looked at are pixel-identical.
The exemption is gone.

**Token parity itself is clean.** All five themes define all 42 colour tokens.
That part of the system is working.

### Arabic

Three findings, two live:

1. **`letter-spacing` breaks cursive joins.** Arabic letters connect; tracking
   severs them. To an Arabic reader that is not loose type, it is broken text —
   and it is the clearest possible tell that nobody looked at the Arabic build.
   It was on the sidebar's SELL/STOCK/INSIGHT headers, the dashboard's largest
   headline label, KPI labels, the theme picker, the AI panel, the import
   wizard. Nothing reset it anywhere. Fixed with one rule in `rtl.css`.
2. **Eastern Arabic-Indic digits.** CLDR's `ar` defaults to the `arab` numbering
   system, so the dashboard date rendered ٢٠٢٦. Jordan writes numbers in Western
   digits in commerce. `_fmtNum` passed no locale at all, so counts followed the
   **OS** display language — an Arabic-configured Windows till showed counts in
   ١٬٢٣٤ while the money beside them stayed Western.
3. **Bidi isolation — already correct.** `_bdi()` exists, is well-reasoned and is
   used pervasively. JOD's three decimals are also correct throughout. Credit
   where due; these were hypotheses that turned out to be handled.

### Overlays

- **A healthy till carried a permanent dead strip.** `#aura-sync-banner` is one
  element reused by every sync tier, and its *calm* tier — meaning sync is
  working, so the normal state — is a pill in the **bottom** corner. Its height
  was being measured and reserved at the **top**. Now tested by position, not by
  a hand-maintained tier name.
- **Toasts stacked on top of each other, literally.** No toast knew about any
  other; two inside the same 3-second window occupied one box and the later one
  hid the earlier. Scanning fires one toast per barcode. Now a measured stack,
  capped at four.
- **The post-signup overlay** — full screen, every new customer — hardcoded a
  near-black ground and `#fff` text (which DESIGN.md forbids outright), and
  neither of its two sentences was translated, so an Arabic shop's first screen
  was English.

### Sidebar hierarchy

The active nav item fills with the accent at 12%. The AI Assistant button filled
with the **same accent at 8%, permanently, on every screen**. A 4% difference in
one hue is not a distinction anyone can see, so the sidebar showed two
accent-filled pills and only a 3px bar actually answered "which screen am I on".
The AI button is now outlined; the filled pill means exactly one thing.

Its dot ran `animation: pulse 2s infinite` — forever, on a screen a cashier
looks at all shift — while carrying **no state at all**. It was also missing
from the `prefers-reduced-motion` block, so it kept pulsing for someone who had
asked their OS to stop motion. Now static, and positioned logically (it was
pinned with a physical `right`, so in Arabic it landed on the wrong side).

### A guard that was missing

A one-character edit — a backtick inside a CSS comment in `_injectStyles` —
terminated the template literal holding the stylesheet and made
`subsystem-retail.js` a syntax error. The browser then never defined
`SubsystemApp`, so the till booted to a shell with **no POS, no dashboard, no
products screen**. No error a shopkeeper would see: just an app that does
nothing.

I did this twice during this review. That file is 650KB of JS with a large
stylesheet embedded in a template literal, and the comment reads perfectly well
as English, so it is near-invisible in review. `retail_frontend_parse_test.js`
now parses all 12 frontend scripts in milliseconds and is mutation-proved
against exactly that defect. It caught the second occurrence immediately.

## Found, not fixed

Ranked by what it costs a shopkeeper.

1. **~1,500 lines of dead CSS** in `main.css` (a 5,400-line file). `#page-landing`
   and `#page-login` are never created anywhere; `[data-app-theme]` is disabled by
   the shell's own comment; the CRM/PM/Mfg chrome is inherited from the old
   monorepo and referenced by nothing. This is the single biggest obstacle to
   understanding the stylesheet, and it is also what made the dead theming code
   look live during this review. Deleting it is safe **only for rules whose root
   container is provably never created** — that is the conservative rule to apply,
   and it should be its own commit.
2. **Empty states are weak.** Reports shows three different "no data" treatments
   on one screen, all plain grey centred text, with ~700px of empty card below
   them. On a fresh install — a new customer's first look — the product presents
   as blank rather than as ready. These should say what to do next ("Ring your
   first sale to see trends here"), next to the control that does it.
3. **The sidebar overflows with no affordance.** At 900px tall the nav is cut off
   mid-list; on the Reports screen the *active* item is not even visible. Nothing
   indicates the rail scrolls.
4. **Native `<select>` elements** ("All branches", "Last 14 days") render with OS
   default styling and read as unfinished next to the rest of the chrome.
5. **Double titling.** The header says "Reports" and the page immediately says
   "Analytics & Reports". Same on Products. Pick one.
6. **The Customers tile's sub-note reads "N active products"** — a mismatched
   metric, deliberate in code but wrong on screen.
7. **Chart series colours** (`#38bdf8`, `#a855f7`, `#8b5cf6`, `#10b981`) are
   Tailwind defaults and off-palette. They are categorical identity colours so
   they are defensible, but they were not *chosen*.

## The logo

You said you are struggling to make it appropriate and to match the themes.
There are two separate problems, and the second is the one you are feeling.

**It fails at small sizes.** The ring's `stroke-width: 15` in a 256 box scales to
**0.94px at 16px** — sub-pixel, so it renders as a hairline or vanishes, while
the A's counter fills in. The result is a dark blob. The beacon diamond is 1.9px
at that size and reads as a rendering artefact. This is normal: real design
systems draw an optical size for small use rather than scaling one master down.

**It does not match the themes, and here is exactly why.** The ring gradient ends
at `#5fe3d0` — a saturated cyan that appears in **no theme's token block**. It is
the same teal family the product already removed elsewhere, where the code calls
it "the old hardcoded teal that matched nothing else in the app". The logo still
carries the colour the rest of the product deliberately abandoned. Nothing you do
to the geometry will fix that; it is one gradient stop.

Recommendation, in order of impact:

- **Move that third stop into the accent family** (e.g. `#1745a9 → #3f7be6 →
  #8ec5ff`). One value, in five files plus `icons.js`. This is the whole
  "doesn't match" problem.
- **Keep the ring mark for ≥32px** — sidebar, splash, lockup, documents.
- **Use the app-icon tile for ≤24px and every OS surface** (favicon, taskbar,
  Android launcher). The tile gives the glyph mass, which is why it survives
  16px when the bare mark does not. The favicon already points at it.
- **Drop the beacon below 32px** and open the A's counter, as in option C of the
  comparison sheet.

A rendered comparison at 16/24/32/48/64/128 on all five theme grounds was
produced for this review. The choice is yours — a brand mark is not a defect to
be fixed unilaterally.

## What the guards can and cannot catch

Worth knowing, because it explains how all of the above shipped:

- `retail_design_theme_safety_test.js` **would** catch a new theme-scoped paint
  rule and a colour token missing from a theme. It **cannot** catch anything on
  its hard-coded allowed list (which is how the Sand login shipped), and it
  cannot see colours chosen in JavaScript.
- `retail_design_contrast_test.js` resolves real rendered colours — genuinely
  strong. But **canvas pixels are outside the DOM entirely**, so no Chart.js
  colour can ever be checked by it. That is structural, not a coverage gap.
- The RTL ratchet scans physical `margin`/`padding`/`border`/`text-align` in
  stylesheets. It never looked at `letter-spacing`, and it cannot see a style
  string built in JavaScript.

The general shape: **this suite is excellent at invariants and blind to
appearance.** Screenshotting the running product found, in one pass, a class of
defect that 68 green tests could not. That is not a criticism of the tests — it
is an argument for keeping a pair of eyes in the loop, and for the browser smoke
suite being the thing that has to stay green.

## Verification

    retail JS suite                 68/68
    Playwright smoke suite          11/11 scenarios
    POS pay controls at 1366x768    7/7 reachable at rest, banner + claim bar up
    favicon                         resolved and served (was a 404)
    new guards                      2, both mutation-proved in both directions
