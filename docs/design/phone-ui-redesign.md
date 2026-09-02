# Aura Retail — Phone UI Redesign Specification

**Status:** DESIGN SPEC — no code changed. Owner decides what gets built.
**Author context:** written after studying all 20 phone screenshots at 390×844
(`~/.claude/jobs/b602c1c7/tmp/shots4/phone-*.png`), the desktop references, and
the real code (`app-shell.js`, `subsystem-retail.js` `_injectStyles()`/
`_renderPOS`/`_renderProducts`/`_renderReports`, `css/main.css`, `css/rtl.css`,
`i18n.js`, `icons.js`). Every selector and file path below was verified against
the source on branch `feat/launch-readiness`.

**What the screenshots actually show** (the diagnosis this design answers):
every phone screen is the desktop shell squeezed into 390px. A ~41px icon rail
eats 15% of the width and paints amputated captions ("SELL", "STOCK",
"INSIGH…") down its edge; the header spends two full rows on a brand name, a
section chip, a LIVE pill, and four more chips before any content; the POS
money column — cart, total, tender, Charge — is entirely off-screen to the
right, which is the "swipe left and there is 2 things" the owner is describing;
tables clip mid-first-column; the reports KPI row squeezes four cards until
"REVENUE" reads "REVENI" and the money wraps; and the admin-device prompt
buries the bottom third of every single screen. The rail-bottom AI/License/
Log-Out boxes are the "bottom icons that look bad": three unlabeled squares
stacked in a 41px gutter. None of this is a styling bug. It is the absence of
a phone layout, and this document specifies one.

---

## 1. Design direction: **The One-Thumb Till** — an appliance, not a dashboard

The phone build of Aura Retail is not a small desktop. It is a second till and
a shopkeeper's pocket companion in a Jordanian دكان: used one-handed while the
other hand bags goods, in harsh daylight near an open shop front, interrupted
mid-sale constantly, cash-first (JD with three-decimal fils precision), and as
often in Arabic as in English. That context dictates five commitments, and
every layout decision below is downstream of them:

1. **Bottom-anchored.** Everything a thumb must reach — navigation, the cart,
   the total, Charge — lives in the bottom 40% of the screen. The top of the
   screen is for reading (scan field, product grid, figures), never for
   actions that happen twenty times an hour.
2. **One primary action per screen.** The POS screen's action is Charge. The
   dashboard's is Open the till. A data screen's is Add. Everything else is
   visually subordinate. Hierarchy comes from scale (the existing token
   system's stated philosophy — `--text-size-total`'s comment in main.css says
   exactly this), not from more boxes.
3. **Zero horizontal page panning.** The page never scrolls sideways. The only
   horizontal scrolling permitted is inside a deliberately designed row region
   (the POS category chip rail), where it is a browsing gesture, not a
   navigation requirement. This is the owner's most explicit complaint and it
   becomes a hard rule, enforceable by the existing overflow measurement
   harness (the one that counted 106 violations).
4. **Cash-grade money rendering.** `JD 3.750` is never clipped, never wrapped,
   never ellipsized, always tabular-numeral, always LTR even in Arabic. Money
   gets space first; decoration yields.
5. **Light, loud, high-contrast.** The product forces `data-theme="light"`;
   this design leans into it — near-white surfaces, the existing single
   action-blue accent (`--accent-action #1745a9`), money-green only on Charge
   and money-in figures. It must survive direct sunlight at a shop entrance,
   which a subtle gray-on-gray design does not.

This is deliberately **not** "clean and modern," not a bento grid, not a
glassmorphism exercise. It is closer to the design language of a good cash
register: instantly legible, thumb-operable, boring in the right places, with
exactly one loud number on the money screen.

The phone breakpoint is **≤640px**, written as a literal in media queries
(plain CSS cannot use custom properties in `@media`; leave a comment saying
640 is the canonical phone cut wherever it appears). Tablet (641–920px) keeps
the existing icon-rail behavior; this spec does not touch it.

---

## 2. Navigation model: bottom tab bar (5 fixed slots) + "More" bottom sheet

### The decision

At ≤640px the sidebar disappears entirely (hidden with CSS, kept in the DOM —
see "Tests that pin this" below) and is replaced by:

- **A fixed bottom tab bar with exactly 5 slots**, identical for every role:

  | Slot (LTR order) | Destination | Icon (AuraIcons key) | EN label | AR label |
  |---|---|---|---|---|
  | 1 | `dashboard` | `🏠` | Home | الرئيسية |
  | 2 | `products` | `📦` | Stock | المخزون |
  | 3 (center, emphasized) | `pos` | `🛒` | Till | البيع |
  | 4 | `customers` | `👥` | Customers | العملاء |
  | 5 | More sheet | `☰` (fallback char if no AuraIcons key) | More | المزيد |

- **A "More" bottom sheet** holding the other **15 destinations** in the
  existing four groups, plus the utility actions that currently render as the
  ugly rail-bottom boxes.

### Why these five, argued against the 19 destinations

The tab bar must be **stable across roles** — a tab bar that gains and loses
tabs as capability gates resolve feels broken and retrains muscle memory per
login. Reading `_isNavItemVisible()` (app-shell.js:329) against the nav list:
exactly four content destinations carry **no gate of any kind** — `dashboard`,
`pos`, `products`, `customers` (plus `returns`, `categories`, `suppliers`,
`purchases`, and `scanner` which is `desktopOnly` and already hidden on
Android). Every other destination is gated by capability, role, or device.
So the bar is built from the ungated set, chosen by frequency at a till:

- **Till** is the product. It gets the center slot, visually raised (see
  wireframe), one thumb-tap from anywhere, always.
- **Home** is the owner's pulse check and the app's landing.
- **Stock** (products) is the highest-frequency lookup: price checks, stock
  checks, quick edits.
- **Customers** earns the fourth slot over Returns because of the Jordanian
  credit-book (دَين) reality this product already serves: the POS has a
  `credit` payment method and a customer selector, AR statements exist, and
  looking up a customer's balance is a daily act in a neighborhood shop.
  Returns are weekly, not hourly, and remain two taps away in More.
- **More** is the fifth slot, not a hamburger in the header, because it must
  be thumb-reachable and because it inherits the full grouped nav.

The 15 in the More sheet: returns, scanner, promotions (Sell); categories,
suppliers, purchases (Stock); reports, stock-accuracy, exceptions, audit-log
(Insight); employees, branches, admin-center, email-notifications,
backup-export (Admin). 4 + 15 = 19; nothing becomes unreachable.

### Why this answers the owner's actual complaints

- "Inconvenient to swipe left / there is 2 things": the two-panel POS squeeze
  is gone (section 3); nothing anywhere requires horizontal panning.
- "The bottom icons look bad": the current bottom icons are three unlabeled
  41px squares (AI, license key, power) stacked in the rail. The replacement
  is a real tab bar — five **labeled**, 64px-tall, evenly divided targets —
  and the AI/License/Log Out trio moves into the More sheet's footer where
  they get full-width labeled rows instead of mystery squares.

### The More sheet, concretely

A bottom sheet (not a side drawer — side drawers are where the horizontal
gesture complaint came from, and a bottom sheet is one-hand reachable):

- Slides up over a scrim, max height 80vh, `--radius-panel` top corners,
  drag-handle bar, internal vertical scroll.
- Content, in order:
  1. The four groups, reusing `sys.navGroups` verbatim and re-applying
     `_isNavItemVisible()` per item with the **empty-group rule** (a group
     with zero visible items renders no header — same rule `_renderShell`
     already enforces, pinned by `retail_nav_groups_test.js`).
  2. Rows are 52px (`--touch-target-comfortable`), icon + label, active row
     highlighted with `--surface-accent-soft` exactly like `.sub-nav-item.active`.
  3. Footer group (no label): **AI Assistant** (when `hasAI`), **Language
     EN/ع**, **Theme accent** (ThemeEngine picker), **License**, **Log Out**
     (danger-tinted, last).
- Tapping any row calls the existing `SubsystemApp._navigate(id)` and closes
  the sheet. The sheet never persists across navigations.
- The More tab shows the active state whenever the current section is one of
  the 15 it owns, so the bar always shows where you are.

### Header at ≤640px (one slim row, ~48px)

- Show: the **section name as the title** (hide `.sub-header-title`, promote
  `.sub-header-section` to title styling — the product name is redundant when
  the brand lives in the More sheet), the LIVE/sync badge, and the language
  toggle (`#aura-lang-toggle` — bilingual switching is too important in this
  market to bury).
- Hide on phone: the accent brand pill (`.sub-header-badge`), the theme
  button, the duplicate AI header button. All remain on desktop.

### Tests that pin this (do not break them)

- The `<aside class="sub-sidebar">` stays **in the DOM** at all widths,
  hidden by CSS — `retail_nav_groups_test.js` asserts every group header
  renders, and `.sub-nav-group-label` already uses the visually-hidden
  pattern at ≤640px for exactly this reason (main.css:2307).
- `systems.retail.nav` stays a flat array; `retail_employee_management_test.py`
  greps app-shell.js source for the literal `!item.ownerOnly || this.role === 'admin'`.
  The tab bar and sheet are render-time arrangements only, like `navGroups`.
- All new CSS uses logical properties only — `retail_design_rtl_test.js`
  scans for physical `left`/`right`.

---

## 3. Screen-by-screen layout at 390px

### 3.1 POS — the money screen

The current phone POS has the cart panel literally off-screen. The redesign
splits the screen into a **scan-and-pick surface** (top, scrolls) and a
**money edge** (bottom, fixed): a persistent cart **peek bar** showing count +
total + Charge, which opens the **cart sheet** for review and tender.

**Closed state (scanning/picking):**

```
┌───────────────────────────────────────┐
│ Point of Sale            ● LIVE   ع  │ ← slim header (48px)
├───────────────────────────────────────┤
│ ┌───────────────────────────┐ ●ready │
│ │ ⌗  Scan or search…        │        │ ← sticky scan bar (56px)
│ └───────────────────────────┘        │   input stays focused
│ ( All )( Drinks )( Snacks )( Dai… →  │ ← category chips, x-scroll
│                                       │   inside this rail only
│ ┌────────┐ ┌────────┐ ┌────────┐     │
│ │ Cola   │ │ Water  │ │ Bread  │     │
│ │ 330ml  │ │ 1.5L   │ │        │     │ ← product grid, 3-up,
│ │ 0.450  │ │ 0.250  │ │ 0.350  │     │   vertical scroll only
│ └────────┘ └────────┘ └────────┘     │
│ ┌────────┐ ┌────────┐ ┌────────┐     │
│ │ …      │ │ …      │ │ …      │     │
├───────────────────────────────────────┤
│ 🛒 3 · Held(1)  JD 4.750  [Charge ▸] │ ← peek bar (56px, fixed)
├───────────────────────────────────────┤
│ الرئيسية المخزون (البيع) العملاء المزيد │ ← tab bar (64px, fixed)
└───────────────────────────────────────┘
```

**Open state (cart sheet, ~85% height over a scrim):**

```
┌───────────────────────────────────────┐
│ ░░░░░░░░ scrim (tap to close) ░░░░░░ │
├──────────────── ▬▬ ──────────────────┤ ← drag handle
│ Current Sale     [Walk-in ▾] [⏸ Hold]│
│───────────────────────────────────────│
│ Cola 330ml        −  2  +     0.900  │
│ Bread             −  1  +     0.350  │ ← lines: name, qty
│ Water 1.5L        −  14 +     3.500  │   stepper, line total
│   (scrolls if long)                   │
│───────────────────────────────────────│
│                          [Void Sale] │ ← destructive, alone
│ Subtotal                    JD 4.750 │
│ Discount [ 0 ]%                      │
│ Tax                         JD 0.000 │
│ TOTAL                    JD 4.750    │ ← --text-size-total,
│ Cash tendered [  5.000 ]             │   largest thing on screen
│ Change due                  JD 0.250 │
│ (Cash)(Card)(Mobile)                 │
│ (Transfer)(Credit)(Voucher)          │ ← 3×2 method grid, 48px
│ ┌───────────────────────────────────┐│
│ │      Charge — JD 4.750            ││ ← 56px, money-green,
│ └───────────────────────────────────┘│   pinned above safe area
└───────────────────────────────────────┘
```

**Rules and mechanics:**

- **Reading order (phone):** scan field → chips → grid → peek bar. In the
  sheet: customer/hold → lines → void → totals → tender → Charge. Charge is
  always the last element, always pinned.
- **The peek bar** is the POS's own fixed element sitting directly above the
  tab bar. It shows: cart icon + line count (and the Held count, folded here
  from the desktop "Held (0)" button), the running total (`.money`, tabular,
  LTR), and a Charge affordance. It updates in `_recalc()` alongside
  `#pos-total`. Empty cart: "Cart is empty" muted, Charge disabled.
- **Charge on the peek bar opens the sheet scrolled to tender — it never
  commits directly.** One accidental tap must not ring a sale. The commit
  button is only ever the big one inside the sheet (`#pos-checkout-btn`,
  unchanged id, unchanged `_checkout()` handler).
- **Scanner focus is sacred.** The peek bar, sheet scrim, chips, and grid all
  get the existing `onmousedown="RetailSystem._keepScanFocus(event)"`
  treatment so a hardware scanner paired to the phone keeps its target. A
  scan while the sheet is open adds the line and the visible cart updates
  (all existing behavior — the sheet IS `.pos-right`, just repositioned).
- **Implementation shape (low-risk on purpose):** at ≤640px, `.pos-right`
  becomes the sheet (`position:fixed; inset-inline:0; inset-block-end:0;
  block-size:85dvh; transform:translateY(100%)`, `translateY(0)` when
  `.pos-wrap` carries a new `pos-sheet-open` class). No DOM re-parenting, no
  duplicated cart markup; every existing id (`#pos-cart`, `#pos-sub`,
  `#pos-tax`, `#pos-total`, `#pos-tendered`, `#pos-change`, `#pos-pay-btns`,
  `#pos-checkout-btn`, `#pos-customer`) keeps its single instance, so
  `_recalc`/`_setMoney`/`_calcChange`/`_checkout` are untouched. The peek bar
  is the only new DOM (`#pos-peek`, with `#pos-peek-count` and
  `#pos-peek-total`), hidden ≥641px.
- **Dropped/deferred at 390px:** the keyboard-shortcut hint strip
  (`.pos-kbd-hints`) is hidden under `@media (hover:none) and (pointer:coarse)`
  — hints for Enter/Esc/Del on a touch screen are noise (and this correctly
  keeps them on a desktop window narrowed to 390px). The "Products" pane
  title row is dropped; the grid is self-evident. The cash-drawer status bar
  (CashDrawer.mount) renders as a single compact line under the scan bar,
  never the 3-line banner in the screenshots.
- The desktop `block-size:calc(100vh - 120px)` on `.pos-wrap` does not apply
  ≤640px (natural height; only the grid region scrolls).

### 3.2 Dashboard

The dashboard's DOM order is already almost right (the `rdash` rewrite is
scale-led); the phone pass is about compression and bottom padding, not
reordering.

```
┌───────────────────────────────────────┐
│ Dashboard                ● LIVE   ع  │
├───────────────────────────────────────┤
│ Retail Overview                       │
│ Tuesday, September 1, 2026            │
│ ┌───────────────────────────────────┐│
│ │        🛒 Open the till           ││ ← full-width CTA
│ └───────────────────────────────────┘│
│ TODAY SO FAR                          │
│ JD 128.750            ▲ 12% vs yest. │ ← the one big number
│ 41 transactions · avg JD 3.140       │
│ Sales JD 130.500 · Returns JD 1.750  │
│───────────────────────────────────────│
│ ● Needs attention: 3 low-stock items │ ← one-line band
│───────────────────────────────────────│
│ Month-to-Date        Customers       │
│ JD 2,410.300         57              │ ← 2-up context row
│───────────────────────────────────────│
│ Revenue Today (by hour)  [chart]     │
│ Payment Methods          [chart]     │ ← stacked, 200px each
│ Recent sales (table→cards, §4)       │
├───────────────────────────────────────┤
│ 🛒 …peek bar only on POS…            │
│ tab bar                               │
└───────────────────────────────────────┘
```

- **Reading order:** date/CTA → today's money → attention → context pair →
  charts → recent activity. Identical to DOM order; no phone-only reordering.
- **Kept:** the "Open the till" CTA (redundant with the Till tab but it is
  the screen's stated primary action and teaches new users).
- **Deferred at 390px:** chart heights cap at 200px; the hourly chart shows
  the last 12 hours server-side data unchanged but with x-axis label
  thinning (Chart.js `maxTicksLimit`, already available); nothing is
  removed.

### 3.3 Data-table screens (Products shown; Customers identical pattern)

```
┌───────────────────────────────────────┐
│ Products                 ● LIVE   ع  │
├───────────────────────────────────────┤
│ ┌───────────────────────────┐        │
│ │ 🔍 Search products…       │        │ ← full width
│ └───────────────────────────┘        │
│ [⬆ Import]        [+ Add Product]    │ ← actions row, 44px
│───────────────────────────────────────│
│ ┌───────────────────────────────────┐│
│ │ Cola 330ml              JD 0.450 ││ ← title + money, bold
│ │ SKU COLA-330 · Drinks            ││ ← meta line, muted
│ │ Stock 14 · Reorder 5 · [Active]  ││
│ │ [Adjust stock] [Edit] [Delete]   ││ ← 44px buttons, wrap
│ └───────────────────────────────────┘│
│ ┌───────────────────────────────────┐│
│ │ Bread                   JD 0.350 ││
│ │ …                                ││
├───────────────────────────────────────┤
│ tab bar                               │
└───────────────────────────────────────┘
```

- **Reading order:** search → actions → cards (newest/filtered order
  unchanged from the table's row order).
- **Dropped at 390px:** none of the data — every column the desktop table
  shows appears in the card (see §4 for the mechanism); what is dropped is
  the tabular scanning affordance, which a 390px screen cannot honestly
  provide anyway. Cost price stays (it is in the desktop table and the owner
  is the phone's primary data-screen user); if it ever needs hiding from
  cashiers that is a capability question, not a layout one.

### 3.4 Reports

```
┌───────────────────────────────────────┐
│ Reports                  ● LIVE   ع  │
├───────────────────────────────────────┤
│ [All branches ▾]   [Last 14 days ▾]  │ ← 2-up filters, 44px
│ ┌────────────────┐ ┌────────────────┐│
│ │ REVENUE        │ │ TRANSACTIONS   ││
│ │ JD 2,410.300   │ │ 741            ││ ← 2×2 KPI grid,
│ └────────────────┘ └────────────────┘│   money clamps, never
│ ┌────────────────┐ ┌────────────────┐│   clips (see rules)
│ │ GROSS PROFIT   │ │ AVG TICKET     ││
│ │ JD 812.100 (34%)│ │ JD 3.250      ││
│ └────────────────┘ └────────────────┘│
│ Daily Revenue Trend      [chart]     │
│ Payment Methods          [chart]     │ ← stacked, 220px
│ Sales by Employee   (cards, §4)      │
│ Top Selling Products (cards, §4)     │
│ Revenue by Branch        [chart]     │
├───────────────────────────────────────┤
│ tab bar                               │
└───────────────────────────────────────┘
```

- **KPI money rule** (this is the screen that shipped clipped figures):
  `.ret-kpi-value` gets `font-size: clamp(20px, 6.5vw, 28px);
  font-variant-numeric: tabular-nums; white-space: nowrap; min-inline-size: 0;`
  and the KPI grid drops to a single column below 360px. A JD figure with
  thousands (`JD 12,410.300` = 12 glyphs) fits a 173px card at 24px
  tabular type; if the clamp floor still overflows, the card grows — the
  grid is `auto-fit,minmax(150px,1fr)` so overflow can never clip, only
  wrap the grid.
- **Reading order:** filters → KPIs → trend → payment → employee → top
  products → branch comparison. Matches DOM; the branch-filter note renders
  between KPIs and charts as today.
- **Deferred:** chart heights 220px; tables become cards per §4.

---

## 4. Wide tables on a phone: labelled cards. Committed, not hedged.

**Decision: every data table at ≤640px renders as a stack of labelled cards.**
Not horizontal scroll inside the row region, not priority-columns-only.

Why cards win here:

- **Horizontal row-scroll** fails the one-thumb rule, hides the money columns
  (which sit rightmost in every table in this product), and is precisely the
  gesture the owner just told us he hates. It also composes terribly with
  RTL, where "the hidden columns" flip sides.
- **Priority columns** (show 3, drop 6) destroys data with no recovery path:
  these screens have no row-detail page; the row IS the record. Hiding
  Stock or Cost on Products makes the phone build lie by omission.
- **Cards** show every field, cost only vertical space, and vertical space is
  the one thing a phone has. Search (already full-width, already wired to
  `_filterProducts` etc.) is the access path for long lists — nobody scans
  500 card rows, on any device.

**The mechanism already half-exists — finish it, don't replace it:**

- `app-shell.js` (~line 2707) already auto-stamps `data-label` onto every
  `td` from its `thead th` text, generically, via MutationObserver.
- `css/main.css` (~line 3624) already flips `#sub-content table` to stacked
  blocks with `td::before { content: attr(data-label) }` at ≤600px.

What the screenshots show is this mechanism producing **flat, unranked
label:value stacks** (see phone-16-employees) with the value column clipping.
The upgrade is a priority layer on the same mechanism:

1. Table authors add `data-card` hints on `<th>`: `data-card="title"` (one
   per table — Name/Employee/SKU-as-identity), `data-card="money"` (Price,
   Revenue — rendered on the title row, end-aligned, `.money`), and
   `data-card="meta"` (SKU, Category — rendered as a single muted run under
   the title, no label prefixes). Unmarked columns keep the labelled-row
   default. The labelize() function copies `data-card` from `th` to `td`
   exactly as it copies the label today.
2. CSS at ≤640px: `td[data-card="title"]` → block, bold,
   `--text-size-body-lg`, no `::before`; `td[data-card="money"]` →
   absolutely positioned `inset-inline-end` on the title row... **no** —
   simpler and RTL-safe: the card becomes `display:grid;
   grid-template-columns: minmax(0,1fr) auto;` with title and money on row
   one via `grid-row`/`grid-column`, everything else `grid-column: 1 / -1`.
   No physical properties, no absolute positioning.
3. Action cells (`data-label="Actions"`) keep the existing rule (buttons
   wrap end-aligned, label suppressed) but buttons get
   `min-block-size: var(--touch-target-min)`.
4. Empty cells stay suppressed (`td:empty { display:none }` — already
   present), which is what keeps sparse rows compact.

Tables in scope: products, categories, customers, promotions, suppliers,
purchases, returns, audit-log, employees (`employees.js`), branches,
reports' employee + top-products tables, dashboard recent-sales. They all
run through the same generic CSS; only the `data-card` hints are per-table.

---

## 5. Tokens: type, spacing, touch — as CSS custom properties

The existing token layer in `css/main.css` (:root, ~line 90–210) is genuinely
good and already role-named. **No existing token is renamed or removed.** The
phone pass adds the following, in the same `:root` block, same naming style:

```css
:root {
  /* ── PHONE CHROME (One-Thumb Till) ─────────────────────────────
     The two fixed bands at the bottom of every phone screen, named so
     content padding can reserve them without magic numbers. env() term
     covers gesture-nav phones (Android 10+) and iPhone home bars. */
  --tabbar-block-size:  64px;                 /* 5 slots ≥ 72px wide at 390 */
  --peekbar-block-size: 56px;                 /* POS only                   */
  --safe-area-bottom:   env(safe-area-inset-bottom, 0px);

  /* Bottom-sheet chrome (More sheet + POS cart sheet share these). */
  --sheet-max-block-size: 85dvh;
  --sheet-scrim: rgba(15, 23, 42, 0.45);

  /* Tab bar type: label under icon. Micro but never smaller ------------ */
  --tabbar-label-size: 11px;   /* = --text-size-micro; Arabic labels fit  */
  --tabbar-icon-size:  22px;
}

/* Phone-only overrides — 640 is the canonical phone breakpoint (literal:
   custom properties are not usable inside @media conditions). */
@media (max-width: 640px) {
  :root {
    --text-size-total:   34px;  /* POS grand total: still the largest    */
    --text-size-display: 26px;  /* dashboard hero money                  */
    --text-size-title:   20px;
    --space-section:     32px;  /* section rhythm tightens one step      */
    --space-roomy:       20px;
  }
}
```

Existing tokens this design consumes unchanged (do not fork them):

| Concern | Token | Value |
|---|---|---|
| Touch floor | `--touch-target-min` | 44px (floor, not target) |
| Comfortable target | `--touch-target-comfortable` | 52px |
| Type scale | `--text-size-micro/meta/body/body-lg/subhead/title/display/total` | 11/12/14/15/17/22/30/40px (desktop) |
| Spacing | `--space-hairline/tight/snug/base/comfy/roomy/loose/section` | 2/4/8/12/16/24/32/48px |
| Radius | `--radius-control/card/panel/pill` | 8/12/16/999px |
| Accent | `--accent-action` (+hover/active) | #1745a9 |
| Focus | `--focus-ring-*` | 3px ring + halo |
| Motion | `--motion-fast/base` for sheet transitions | 160/240ms |

Hard rules the tokens enforce:

- Every interactive element ≥ `--touch-target-min` in both axes. Tab bar
  slots and sheet rows use `--touch-target-comfortable`+.
- `.sub-content` on phone gets
  `padding-block-end: calc(var(--tabbar-block-size) + var(--safe-area-bottom) + var(--space-comfy));`
  and the POS adds `--peekbar-block-size` on top. The admin-device banner's
  `bottom` offset also stacks above the tab bar.
- Sheets animate `transform` only (compositor-friendly), `--motion-base`,
  disabled under `prefers-reduced-motion` (pattern already present in the
  POS injected styles).

---

## 6. The RTL story

Arabic is a first-class mode, toggled at runtime (`AuraI18n.apply()` sets
`dir="rtl"` + `body.rtl`). The design's RTL contract:

**What mirrors automatically (keep it that way by construction):**
- The tab bar is a plain flex row of logical-order children → under
  `dir="rtl"` Home renders at the inline-start (right) edge with zero extra
  CSS. Never position slots with physical offsets.
- The peek bar (`justify-content:space-between` + logical order: count →
  total → Charge) mirrors itself; Charge lands at the inline-end.
- Card grids (§4) use `grid-template-columns: minmax(0,1fr) auto` with
  logical placement → money lands inline-end in both directions.
- The category chip rail scrolls from the inline-start automatically under
  `direction:rtl`; do not script scroll positions.
- Sheets are full-width bottom surfaces — nothing to mirror except their
  internal rows, which are flex/grid and mirror themselves.

**What must NOT mirror (and how it's already handled — reuse, don't reinvent):**
- **Money.** `rtl.css` already forces `.money` to `direction:ltr`. Every new
  money surface (peek total, card money cell, KPI values) carries the
  `.money` class. Never build a money string by concatenating text around a
  number in markup — the total band's label/value are separate elements for
  exactly this reason.
- **Identifiers.** SKUs, barcodes, till IDs, emails, timezone strings follow
  the existing `<bdi dir="ltr">` convention (see subsystem-retail.js:7361,
  8019 and employees.js:643 — the codebase documents the trap: a signed or
  neutral-charactered run beside Arabic gets reordered by the bidi
  algorithm, not merely mirrored). New card meta lines interpolating SKU or
  barcode wrap them in `<bdi dir="ltr">`.
- **Charts** stay LTR (rtl.css keeps data logical). Chart cards mirror their
  titles only.
- **PIN/tender numeric inputs** keep `direction:ltr` internally with
  end-alignment (existing `.pos-mini-input` pattern: `text-align:end` is
  logical and correct in both directions).

**Label budget.** Arabic labels run longer (الإعدادات vs Settings). The tab
bar reserves 20% width per slot (≥72px at 390px) and the chosen labels
(الرئيسية، المخزون، البيع، العملاء، المزيد) all set in 11px within 60px.
Sheet rows and card labels are full-width and wrap; nothing anywhere may
`text-overflow:ellipsis` an Arabic action label (ellipsizing labels is how
the current rail got its amputated captions).

**Mechanical rule:** all new CSS uses logical properties exclusively
(`inset-inline-*`, `margin-inline`, `padding-block`, `border-start-start-radius`,
`inline-size`). This is not a style preference — `retail_design_rtl_test.js`
scans the stylesheet for physical properties and fails the build.

---

## 7. Implementation plan — ordered, independently shippable steps

Each step ships alone, behind `@media (max-width:640px)` so desktop/tablet are
untouched by construction. Risk flags: **[SAFE]** = additive, no behavior
change outside phone layout; **[MONEY PATH]** = touches POS calculation/
render surfaces — needs the full adversarial verification pass (ENGINEERING.md
§1/§6: mutation-prove, run the real artifact at 390px, both languages).

**Step 1 — Tokens + shell: kill the rail, mount the tab bar. [SAFE]**
- `css/main.css`: add the §5 token block to `:root`; add a new
  `@media (max-width:640px)` section: `.sub-sidebar { display:none; }`
  (DOM stays — see §2 test notes); `.sub-content` bottom padding per §5;
  new `.sub-tabbar`, `.sub-tab`, `.sub-tab.active`, `.sub-tab-icon`,
  `.sub-tab-label` rules (fixed, `inset-block-end:0`, `inset-inline:0`,
  5-slot flex, center slot `.sub-tab-till` raised: larger icon, accent
  circle). Tab bar `display:none` ≥641px.
- `products/retail/frontend/app-shell.js` `_renderShell()`: append after
  `.sub-main`:
  `<nav class="sub-tabbar" id="sub-tabbar">` with the five slots; slots 1–4
  call `SubsystemApp._navigate(id)`; slot 5 calls `_openMoreSheet()` (stub =
  navigate to nothing until Step 2 — acceptable to ship Steps 1+2 together
  if preferred). `_navigate()` gains three lines mirroring the existing
  `.sub-nav-item` active-state toggle for `.sub-tab` (More is active when
  the section is none of the four).
- Acceptance: at 390px no icon rail, no amputated captions, 5 labeled
  bottom tabs, content not hidden behind the bar; at 641px+ pixel-identical
  to today.

**Step 2 — The More sheet. [SAFE]**
- `app-shell.js`: new `_openMoreSheet()` / `_closeMoreSheet()` building a
  fixed bottom sheet + scrim on `document.body` (same persistence reasoning
  as the admin-claim bar, which lives on body because `_renderShell` wipes
  the shell's innerHTML). Reuses `sys.navGroups`, `_isNavItemVisible()`,
  the empty-group rule, `t()`, `AuraIcons.render`. Footer rows: AI
  (`SubAI.open`), language (`AuraI18n.toggle`), theme
  (`ThemeEngine.openPicker`), license (`openLicensing`), logout (`logout`).
  `_navigate()` closes any open sheet.
- `css/main.css`: `.sub-more-sheet`, `.sub-more-scrim`, `.sub-more-row`,
  reuse `.sub-nav-group-label` styling for group headers (un-hide it inside
  the sheet: the ≤640 visually-hidden rule gets `:not(.sub-more-sheet *)`
  scoping or the sheet uses its own label class — pick the second; simpler
  specificity).
- Acceptance: all 15 destinations reachable in ≤2 taps for a full-capability
  admin; a bare cashier sees Sell/Stock groups only (empty-group rule);
  language toggle works from the sheet; log out works.

**Step 3 — Header slim-down. [SAFE]**
- `css/main.css` ≤640 block: hide `.sub-header-badge`, the theme
  `.sub-header-btn`, and `.sub-header-title`; promote `.sub-header-section`
  to `--text-size-subhead`/bold/no-pill styling; `.sub-header` fixed 48px
  single row, `flex-wrap:nowrap` (replacing the current wrap-to-two-rows
  behavior at ≤560px).
- No JS change: `_navigate()` already writes the section name into
  `#sub-header-section`.
- Acceptance: one header row on every phone screen, section name as title,
  language toggle still present.

**Step 4 — Admin-device banner containment. [SAFE]**
- `app-shell.js` `_maybeOfferAdminDeviceClaim()` (line ~1006): move the
  inline `style.cssText` recipes to classes (`.adm-claim-bar` etc.) in
  main.css; at ≤640px: `inset-block-end: calc(var(--tabbar-block-size) +
  var(--safe-area-bottom) + var(--space-snug))`, single-line message with
  the two actions on one row beneath, `max-block-size` ~96px. Never covers
  the peek bar or tab bar.
- Acceptance: banner ≤ ~20% of screen height at 390px, till fully operable
  behind/above it, both languages.

**Step 5 — Generic table→card v2. [SAFE]**
- `app-shell.js` labelize() (~line 2712): also copy `data-card` from `th`
  to `td` (2 lines).
- `css/main.css`: move the existing ≤600px table-card block to the
  canonical ≤640px and extend per §4 (title/money/meta grid, 44px action
  buttons, keep `td:empty` suppression and `data-label="Actions"` rules).
- Acceptance: employees + audit-log + purchases render as cards with no
  value clipping at 390px, RTL mirrored, desktop tables untouched.

**Step 6 — Per-table card hints. [SAFE]**
- `subsystem-retail.js`: add `data-card` attributes to `<th>` in
  `_renderProducts` (Name=title, Price=money, SKU+Category=meta),
  `_renderCustomers`, `_renderSuppliers`, `_renderPurchases`,
  `_renderReturns`, reports' employee/top-products tables, dashboard
  recent-sales; `employees.js` employee table (Employee=title).
- Ship one screen per commit if desired — each table is independent.
- Acceptance per table: title bold, money end-aligned `.money`, meta muted,
  remaining fields labelled.

**Step 7 — Reports + dashboard phone pass. [SAFE]**
- `subsystem-retail.js` `_injectStyles()`: `.ret-kpi-value` clamp +
  tabular + nowrap per §3.4; ≤640: KPI grid `repeat(2, minmax(0,1fr))`
  (1-col ≤360); chart card heights 200–220px; `_renderReports`' inline
  `grid-template-columns:2fr 1fr` / `1fr 1fr` chart grids get a class
  (`.ret-charts-2col`) so the ≤640 stack rule can reach them (inline styles
  outrank injected media queries — this is why the KPI screenshot clipped).
- `css/main.css` or injected styles: `.rdash` phone compression (hero money
  `--text-size-display`, context row 2-up already flexes).
- Acceptance: zero clipped figures at 390px in EN and AR with real data
  (JD 12,345.678 test value), the exact bug class that shipped before.

**Step 8 — Touch hygiene. [SAFE]**
- `subsystem-retail.js` injected styles:
  `@media (hover:none) and (pointer:coarse){ .pos-kbd-hints{ display:none; } }`.
- Sweep: category chips (`.pos-cat-btn`), peek/tab/sheet rows, card action
  buttons all ≥44px (`min-block-size:var(--touch-target-min)`).
- Acceptance: no keyboard hints on a real phone; hint strip still present
  on a narrow desktop window; tap-target audit passes.

**Step 9 — POS peek bar + cart sheet. [MONEY PATH — do last, verify hardest]**
- `subsystem-retail.js` `_renderPOS()`:
  - Markup: add `#pos-peek` (peek bar: `#pos-peek-count`, `#pos-peek-total`
    with `.money`, `#pos-peek-charge`) after `.pos-wrap`; fold the Held
    count into it on phone. Add scrim element `#pos-sheet-scrim`.
  - Injected styles ≤640: `.pos-wrap` single column; `.pos-right` → fixed
    sheet per §3.1 (transform-only animation, `--motion-base`,
    reduced-motion exempt); `.pos-left` gets bottom padding for peek+tab;
    scan bar `position:sticky; inset-block-start:0`.
  - JS: `_toggleCartSheet(open)` toggles `pos-sheet-open` on `.pos-wrap`;
    peek Charge = `_toggleCartSheet(true)` then focus `#pos-tendered`;
    `_recalc()` additionally writes count + total into the peek ids (guarded
    `if (el)` so desktop, where the peek is display:none but present, and
    tests that drive `_recalc` without `_renderPOS`, stay unaffected);
    `_keepScanFocus` wired on peek + scrim.
- **Required verification (ENGINEERING.md §1):** run the real app at 390px,
  EN + AR: scan → line appears + peek total updates; peek Charge opens
  sheet, does NOT commit; sheet Charge rings the sale; mutation-prove the
  peek total (break `_setMoney`'s peek write → a peek assertion goes red;
  restore → green); prove the deny half (tap peek Charge with empty cart →
  no sale posted); verify hardware-scanner focus survives opening/closing
  the sheet; verify `JD 12,345.678` renders unclipped in the peek bar.
- Acceptance: the money column is reachable, the total is always visible via the
  peek bar, and no gesture on the POS screen pans horizontally.

Steps 1–4 are the visible transformation (the owner's three named complaints
all die there). Steps 5–8 are polish that can ship in any order after 1.
Step 9 is the deepest fix and deliberately rides last on a stable shell.

---

## 8. What this design deliberately does NOT do, and why

- **No swipe gestures anywhere** — no swipe-to-delete cart lines, no
  edge-swipe drawer, no swipeable tabs. The owner's core complaint is a
  horizontal gesture; the redesign's identity is that everything is a
  visible, labeled, tappable target. (Sheet drag-to-dismiss is the one
  exception, and it duplicates the scrim tap — never the only way.)
- **No numeric keypad / custom tender pad on the POS sheet.** The OS numeric
  keyboard (`inputmode="decimal"` on `#pos-tendered` is a candidate later)
  is adequate; a custom keypad is real estate and code the money path does
  not need in v1. Revisit only if cashiers report friction.
- **No desktop or tablet changes.** Every rule is fenced at ≤640px. The
  desktop layout is good (the screenshots confirm it); 641–920px keeps the
  existing icon rail. A tablet-specific pass is a separate, later decision.
- **No dark mode.** The product forces light; rebuilding a dark palette to
  the same AA bar is out of scope (app-shell.js documents why it was
  removed).
- **No framework, no bundler, no dependency, no icon-set swap.** Vanilla JS
  + plain CSS served by Flask, AuraIcons as-is — per the repo's own hard
  constraints.
- **No nav data-model change.** `systems.retail.nav` stays flat;
  `navGroups` stays the grouping authority; the tab bar and More sheet are
  render-time arrangements, like the sidebar grouping before them, so every
  capability/adminOnly/ownerOnly/desktopOnly gate and every test that pins
  them is untouched.
- **No offline/PWA work.** Real need eventually (a till on a phone will hit
  dead spots) but it is an architecture project owned by the sync
  programme, not a UI redesign deliverable.
- **No badge counts on tabs** (e.g. exceptions count on More). Wants a
  cheap aggregate endpoint first; a badge that lies is worse than none.
- **No landscape-phone layout.** Portrait is the till posture; landscape
  falls into the ≥641px rules naturally at typical phone heights, which is
  acceptable rather than designed-for.
- **No `products/clinic/` changes. Zero files.** Standing instruction. The
  clinic shares `main.css`'s generic `#sub-content` table rules; Step 5
  keeps those selectors' clinic behavior identical (same block, extended
  properties only) — if any clinic-visible delta shows up in review, that
  step must be re-scoped to retail-only selectors instead.

---

*End of specification.*
