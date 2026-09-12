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

### Navigation: the rail could not say where you were

The nav rail is `overflow-y: auto` and holds **1017px of destinations in a 442px
viewport** at 1366×768 — so it scrolls, but nothing ever scrolled it. Landing on
Reports left the active marker at y787–831, below the rail entirely; Stock
Transfers at y702–746. The item that answers "which screen am I on" was off
screen on exactly the destinations far enough down the list to need it.

A taller monitor did not help: the rail's content height does not change, so a
1440×900 laptop clipped precisely the same destinations as the 768px till.

Now `scrollIntoView({ block: 'nearest' })` after the active class is set — which
scrolls only when the item is genuinely out of view, so navigating to Dashboard
or POS moves nothing and the rail never twitches. Covered by a new browser
scenario, mutation-proved: with the call removed, the scenario goes red.

### Empty states

Every list screen printed one muted sentence — "No products found." — in the
middle of an otherwise blank card, with several hundred pixels of nothing under
it. On a fresh install that is the first thing a new shop sees on most screens,
and it reads as *this product is blank* rather than *you have not added anything
yet*. It is also what a buyer sees in a demo.

Three of them were never passed through the translation helper at all, so an
Arabic shop got English there while the rest of the screen was Arabic.

Now one shared helper — icon, a statement of fact, one line of what to do next,
and the control that does it (Products offers **Add Product** and **Import** in
place). And the first-run empty state is now part of the measured contrast
corpus, which had only ever rendered these screens *with* rows.

### The floating prompt — fixed where it costs money, partial elsewhere

The admin-device claim bar is bottom-centred, up to 92vw wide, and bound to
`document.body` so it survives every navigation and floats over whichever screen
is open. On the POS it covered the Transfer tender and sat beside Charge.

`_reflowBottomOverlays()` now measures whatever is genuinely anchored to the
bottom and publishes `--bottom-overlay-inset`, which `.sub-content` — the scroll
container every screen uses — reserves as tail padding.

**On the POS this is a complete fix**: the till is a fixed-height layout at 100%
of that container, so shrinking the container moves every control up, and all
seven pay controls are reachable at rest.

**Elsewhere it took a second attempt, and the reason is worth keeping.** The
first version reserved the space as bottom *padding*. That fixed the POS and did
nothing for Reports, because **`overflow` clips at the padding box** — bottom
padding on a scroll container only adds scrollable length at the end, so content
is still painted in that strip while you are scrolled anywhere else. A *margin*
shrinks the box itself, which moves the clip edge. Nothing is painted under the
bar on any screen now.

### The first screen was never measured

Both corpora covered post-login screens only. So the one screen every shop sees
before it has an account — and the one a buyer is shown first — had never been
checked by anything. That is exactly where the Sand bug shipped.

Putting it in found four things immediately:

- **The primary button was unverifiable and probably failing.** "Create Account
  & Launch" painted `color: #fff` — which the brief forbids outright — on a
  *gradient* whose far stop is 72% alpha. That end composites with the card
  beneath it, landing near `rgb(88,121,193)`, which puts white 15px bold text at
  roughly **4.4:1**, under the 4.5:1 AA floor. And a gradient has no single
  surface luminance, so nothing could have measured it either way. Now a flat
  accent fill with the sanctioned on-accent token — which is what every other
  primary button in the product already uses.
- **`.auth-note`** used a 7%-alpha tint whose effective colour depends on
  whatever is behind it. Now the opaque per-theme info triad.
- **Seven controls declared no touch floor at all** — every signup field, the
  language toggle and the submit button. They came out near 40–48px from padding
  alone, which drifts with font size, locale and zoom. The language toggle needed
  *both* axes: a two-character button at ~38×24, and the only way to switch the
  product into Arabic on the one screen where Settings isn't reachable yet.
- The measured control count went **161 → 168**.

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

## The phone

Everything above was measured at 1366×768. The product also has a phone surface
below 640px — the nav rail gives way to a tab bar and a More sheet — and there is
a shipped Android build, so it is a real surface with real users. Nobody had
looked at it in this pass. Measured at 390×844, touch:

    #sub-content        1,379px of content in a 343px viewport
    Charge button       y1378-1431, in an 844px window
    grand total         y1111
    payment grid        y1225-1366
    licence banner      141px (17% of the screen), three lines plus a button
    admin prompt        211px

**It is a bad till, not a broken one.** Scrolling `#sub-content` to its end does
bring Charge and the tender grid on screen — but it is a ~1,000px scroll, and by
the time you are there the cart has gone off the top and, with a licence banner
up, the grand total sits *behind* it.

The real fix already has a written spec: `docs/design/phone-ui-redesign.md`
describes a **peek bar** — a pinned charge control carrying the running total.
`#pos-peek-charge` and `.pos-peek` are **not in the DOM**; it was never built.
That is a feature with a design behind it, not a polish pass, so it is named here
rather than improvised.

What was fixed: the POS rendered its **keyboard shortcut legend on a touch
device** — 49px of Enter/N×/X//Del/Esc on a phone with no keyboard. Hidden on
`(hover: none) and (pointer: coarse)`, which is a question about the device
rather than the width, so the Android build and a touch-only 1024px till get the
same answer and a narrow laptop window keeps its hints. Scroll depth 1,036 → 987.

Otherwise the phone layout holds up: no horizontal overflow on any screen, the
tab bar renders, and the raised Till button reads well.

## Found, not fixed

Ranked by what it costs a shopkeeper.

1. **The last of the dead CSS.** 1,098 lines are gone in total — two whole
   theming systems, then 307 rules of vestigial monorepo chrome (a CRM, an intel
   hub, an RPA console, a marketing landing page, a welcome splash), proven safe
   by a 28-frame pixel diff across every screen in two themes. `main.css` is
   5,489 → 4,414 lines. What remains are rules that mix a dead class with a live
   one in the same selector list, and splash/canvas styling interleaved with live
   rules — per-rule work, not a family sweep.
2. **Native `<select>` elements** — CONSIDERED AND DECLINED, with the
   measurement. Every control on the non-modal screens already clears the 44px
   touch floor (measured: the Reports filters, both search boxes and all four POS
   inputs are 44px or more; `min-block-size` is being applied). What remains is
   only the OS-drawn dropdown arrow. A `<select>` cannot take a pseudo-element,
   so a custom chevron means a background image with a **hardcoded colour** plus
   a theme-guard exemption to permit it — which is precisely the pattern this
   pass spent effort deleting. Not worth a literal and an exemption for an arrow.
3. **Double titling.** The header says "Reports" and the page immediately says
   "Analytics & Reports"; Customers, Stock Transfers and Settings show the *same
   word twice*. Left alone deliberately — renaming screens is a naming decision,
   not a defect fix.
4. **Nothing left ranked above cosmetic.** The pre-login screen is now in the
   corpus (see below), which closed the last verification gap this review opened.
5. **Chart series colours** (`#38bdf8`, `#a855f7`, `#8b5cf6`, `#10b981`) are
   framework defaults and off-palette. They are categorical identity colours so
   they are defensible, but they were not *chosen*.
6. **Four inert `if (window.AuraRouter)` branches** in app-shell.js. `AuraRouter`
   is not defined anywhere in `products/` — no router file exists, nothing assigns
   it, and at runtime it is `undefined` with an empty hash. The branches are
   correctly guarded and harmless; three *comments* citing it as the reason for a
   capability guard were corrected, because a wrong reason on a security-adjacent
   guard sends the next auditor looking for a code path that does not exist. It
   cost real time here: a browser test was built on the claim before that test's
   own anti-vacuity assertion caught it.

## Two more traps worth knowing about

Both cost time during this review and neither is visible at the point of edit.

**This file has two stylesheets.** `_injectStyles()` creates a persistent
`<style id="ret-styles">` that survives navigation. The dashboard *also* emits a
`<style>` block inside its own `c.innerHTML`, which is thrown away the moment the
user navigates. They look identical where you edit them. Shared chrome defined in
the second one silently stops applying everywhere else — the new empty states
rendered completely unstyled until they were moved.

**A click-driven test cannot catch a scroll bug.** Playwright scrolls an element
into view before clicking it, so any assertion made after a click sees a
conveniently scrolled container. The existing "navigate every screen" scenario
clicked every destination and passed throughout the entire period the nav rail
was failing to show the active item.

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
    Playwright smoke suite          12/12 scenarios
    POS pay controls at 1366x768    7/7 reachable at rest, banner + claim bar up
    nav rail, every screen          active item visible at 768 and 900
    favicon                         resolved and served (was a 404)
    new guards                      3, all mutation-proved in both directions
    contrast corpus                 22 screens, now incl. the first-run empty state
    main.css                        5,489 -> 4,414 lines (1,098 removed)
    pixel diff                      22/22 deterministic frames byte-identical
    corpus                          23 screens, incl. the pre-login surface
    touch floor                     168 rendered controls clear 44px on both axes
