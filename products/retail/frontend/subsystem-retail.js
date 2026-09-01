/**
 * Action Aura — Retail & POS Full Suite
 * Sections: Dashboard · POS · Products · Customers · Suppliers · Purchases · Returns · Reports
 */

// Shared across every payment-method donut chart in this file (dashboard +
// reports) so the same method always renders the same color regardless of
// which page's API call happens to order it differently (both endpoints
// sort by revenue, so a positional palette flipped colors between pages for
// the identical data). Covers all 6 POS payment methods (see pos-pay-btns).
const RETAIL_PAYMENT_METHOD_COLORS = {
  cash: '#10b981', card: '#3b82f6', mobile: '#f59e0b',
  transfer: '#a855f7', credit: '#ef4444', voucher: '#06b6d4',
};

// Shared builder for BOTH payment-method charts (dashboard #r-dash-pay and
// reports #rep-pay), because both now face the same problem and must answer
// it the same way.
//
// THE PROBLEM: /dashboard/stats and /reports/payment-methods used to return
// gross SUM(sales.total) per tender, which can never be negative. They are
// now NET of refunds, keyed by the tender the refund was PAID BACK IN
// (core/retail/metrics.py, decision #1), and that module says out loud that
// a bucket which saw only refunds reports negative revenue -- deliberately,
// because that is what makes the buckets sum back to the Revenue KPI. A
// card sale yesterday refunded to card today, with no card sale today, is
// exactly that: card = -57.50, and it is true.
//
// A doughnut cannot say that. Chart.js sizes an arc by |value|, so a
// -57.50 slice renders as a perfectly ordinary 57.50-sized wedge: it would
// read as "card took 57.50 today", the precise opposite of the truth, and
// the slices would no longer sum to the whole. The alternatives considered:
//
//   * Clamp negatives to 0 -- rejected. It silently deletes real money from
//     a financial screen and breaks the sum-to-KPI identity that the whole
//     metrics consolidation exists to establish (the doughnut would total
//     more than the Revenue card beside it, which is the ORIGINAL bug).
//   * Drop negative buckets -- rejected for the same reason, plus the
//     tender disappears from the legend entirely on the one day a manager
//     most needs to see it.
//   * Show |value| and mark it -- rejected: a chart whose geometry means
//     one thing and whose label means another is worse than no chart.
//
// So: keep the doughnut when every bucket is >= 0 (overwhelmingly the
// common case -- an ordinary trading day is unchanged, and it stays the
// share-of-total view users know), and fall back to a horizontal bar chart
// with a zero baseline when ANY bucket is negative. A bar chart is the
// honest shape for a signed quantity: -57.50 draws left of zero, the axis
// label reads -$57.50, and nothing is hidden, clamped or reordered.
// Colors stay the same named lookup in both shapes, so a tender keeps its
// identity across the switch.
function retailPaymentChartConfig(labels, values, tickColor) {
  const colors = labels.map(m => RETAIL_PAYMENT_METHOD_COLORS[m] || '#94a3b8');
  const hasNegative = values.some(v => Number(v) < 0);
  if (!hasNegative) {
    return {
      type: 'doughnut',
      data: { labels, datasets: [{ data: values, backgroundColor: colors, borderWidth: 0 }] },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'right', labels: { color: tickColor, font: { size: 12 } } } } },
    };
  }
  return {
    type: 'bar',
    data: { labels, datasets: [{ data: values, backgroundColor: colors, borderWidth: 0 }] },
    options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        // beginAtZero keeps the zero line on the axis so a negative bar is
        // visibly on the other side of it rather than merely shorter.
        x: { beginAtZero: true, ticks: { color: tickColor, callback: v => '$' + v },
             grid: { color: 'rgba(255,255,255,0.05)' } },
        y: { ticks: { color: tickColor }, grid: { display: false } },
      } },
  };
}

const RetailSystem = {
  _cart: [],
  _products: [],
  _categories: [],
  _customers: [],
  _branches: [],
  _currentTotals: {},
  // 'after_discount' (default) | 'before_discount' — loaded from
  // GET /api/sub/retail/settings/tax by _loadPOSData(). Kept in exact sync
  // with core/retail/pricing.py's two formulas; see the comment above
  // _recalc() below before changing either side.
  _taxMode: 'after_discount',

  // ── Currency, resolved by the SERVER ─────────────────────────────────────
  //
  // Loaded from GET /settings/tax alongside the tax mode, in the same round
  // trip _loadPOSData() already makes. The till holds NO opinion of its own
  // about how money looks: both the mark and the decimal count arrive
  // pre-resolved from core/retail/pricing.py, which is the single source of
  // truth for both.
  //
  // WHY NOT a lookup table in this file: the number of decimal places is not
  // cosmetic. The Jordanian dinar has THREE (1000 fils), and pricing.py rounds
  // persisted money to exactly that. A second copy of that knowledge here
  // could drift, and the symptom of drift would be the till DISPLAYING a
  // different number from the one it charges -- the precise defect a mutation
  // caught twice during the promotions work.
  //
  // The defaults below are the Jordanian ones, matching _DEFAULT_SETTINGS
  // server-side, so a till that somehow renders money before its first
  // settings response still shows something correct for the home market
  // rather than dollars.
  _currencySymbol: 'JD',
  _currencyDecimals: 3,

  // ── Auth-aware fetch ──────────────────────────────────────────────────────
  async _fetch(url, opts = {}) {
    opts.credentials = 'include';
    opts.cache = 'no-store';
    const res = await fetch(url, opts);
    if (res.status === 401) {
      const d = await res.json().catch(() => ({}));
      if (window.SubsystemApp?.checkAuthAndSetup) SubsystemApp.checkAuthAndSetup(d.error);
      throw new Error(d.error || 'Session expired');
    }
    return res;
  },
  async _get(url) { return (await this._fetch(url)).json(); },
  async _post(url, body) {
    return (await this._fetch(url, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) })).json();
  },
  async _patch(url, body) {
    return (await this._fetch(url, { method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) })).json();
  },
  // Categories are the one resource whose update route is PUT, not PATCH
  // (see products/retail/backend/api/retail_api.py's update_category) --
  // this mirrors _patch exactly, just with the method the backend expects.
  async _put(url, body) {
    return (await this._fetch(url, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) })).json();
  },
  async _del(url) { return (await this._fetch(url, { method: 'DELETE' })).json(); },

  // Final-review Fix 7 (2026-08-07): HTML-escape a value before it is
  // interpolated into innerHTML or into a quoted attribute. Every other
  // section in this file interpolates its own locally-created records raw --
  // Category is the first entity whose name/description can arrive from
  // ANOTHER DEVICE over the sync relay, which is a genuinely new trust
  // boundary, so the category rendering paths escape. Escapes `"` and `'`
  // too (not just the three characters needed for text nodes) because these
  // values also land inside double-quoted attribute values.
  _esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  },

  // ── Router ────────────────────────────────────────────────────────────────
  render(sectionId) {
    const c = document.getElementById('sub-content');
    if (!c) return;
    this._section = sectionId;     // remembered so the scanner can route scans
    this._initScanner();           // idempotent; desktop-only HID listener
    this._initShortcuts();         // idempotent; keyboard shortcuts, all platforms
    switch (sectionId) {
      case 'dashboard': return this._renderDashboard(c);
      case 'pos':       return this._renderPOS(c);
      case 'products':  return this._renderProducts(c);
      case 'categories':return this._renderCategories(c);
      case 'customers': return this._renderCustomers(c);
      // Launch-readiness 2026-08-30 (ROADMAP.md "retail schema v23",
      // promotions wave 1). See _renderPromotions for the capability guard.
      case 'promotions':return this._renderPromotions(c);
      case 'suppliers': return this._renderSuppliers(c);
      case 'purchases': return this._renderPurchases(c);
      // ci-hardening-w0.3 continuation ("the doorway"): see app-shell.js's
      // nav entry comment and _renderBranches below for the full story --
      // create_branch (retail_api.py) has been complete and gated since
      // Phase 5 wave A with nothing in this file ever calling it.
      case 'branches':  return this._renderBranches(c);
      case 'returns':   return this._renderReturns(c);
      case 'sales':     return this._renderSalesHistory(c);
      case 'reports':   return this._renderReports(c);
      case 'scanner':   return this._renderScannerSettings(c);
      // Multi-device Phase 1: employee management lives in its own feature
      // file (products/retail/frontend/employees.js), the same convention
      // cash-drawer.js follows -- dispatched from here rather than
      // monkey-patching this object from there, so the section list stays
      // readable in one place.
      case 'employees': return RetailEmployees.render(c);
      case 'admin-center': return this._renderAdminCenter(c);
      // ci-hardening-w0.3 continuation ("the doorway", second one on this
      // branch): see app-shell.js's nav entry comment and
      // _renderEmailNotifications below for the full story --
      // /api/notifications/{status,settings,outbox,outbox/run-once}
      // (commercial_runtime/notifications/routes.py) have been complete
      // with nothing in this file ever calling them.
      case 'email-notifications': return this._renderEmailNotifications(c);
      case 'audit-log':    return this._renderAuditLog(c);
      // Phase 3 (docs/launch-readiness/phase3-ledger-truth.md). Owner-facing
      // report over the inventory_balances/inventory_movements comparison --
      // see _renderStockAccuracy for the gating and for why the repair on it
      // is never automatic.
      case 'stock-accuracy': return this._renderStockAccuracy(c);
      // Launch-readiness 2026-08-29 ("the two exception queues both need
      // ONE screen, not two"): stock_exceptions (Phase 7) and
      // sync_conflicts (Phase 6) on one surface -- see _renderExceptions
      // for the gating and for why the discarded-edits section carries no
      // action button.
      case 'exceptions': return this._renderExceptions(c);
      default:
        c.innerHTML = `<div style="text-align:center;padding:80px;color:var(--text-muted)"><h2>${sectionId}</h2><p>Coming soon.</p></div>`;
    }
  },

  // ── Shared styles ─────────────────────────────────────────────────────────
  _injectStyles() {
    if (document.getElementById('ret-styles')) return;
    const s = document.createElement('style');
    s.id = 'ret-styles';
    s.textContent = `
      .ret-hdr { display:flex;justify-content:space-between;align-items:center;margin-bottom:22px; }
      .ret-title { color:var(--text);margin:0;font-size:24px;font-weight:700; }
      /* AUDIT -- this rule used to say color:#fff, a leftover from the dark HUD.
         With Operational Calm the panels behind it are WHITE, and .rdash was
         the only place anything overrode it. So on Sales History, Returns,
         Products, Customers and Suppliers every uncoloured cell -- customer
         name, status, reference -- rendered white on white at 1.00:1. Not
         faint: literally invisible, on five screens, in a shipped build.

         It hid from the contrast sweep because that sweep counted a rule as
         "exercised" when a corpus element MATCHED it, not when the rule WON.
         The dashboard matches .ret-table and then overrides it, which was
         enough to launder a live defect into the "already checked" bucket.
         The token is the fix; the sweep's accounting is fixed separately. */
      .ret-table { width:100%;border-collapse:collapse;color:var(--text);font-size:13px; }
      .ret-table th { color:var(--text-muted);font-weight:500;padding:10px 12px;border-bottom:1px solid var(--border-soft);text-align:left; }
      .ret-table td { padding:11px 12px;border-bottom:1px solid var(--border-subtle, var(--border-soft));vertical-align:middle; }
      .ret-table tr:last-child td { border:none; }
      /* :focus-within, not only :hover. A clickable row is now a row CONTAINING
         a button (see _saleOpenerButton), and a keyboard or barcode-scanner
         operator tabbing onto that button has to see which row they are on --
         the same thing a pointer user gets from hover. Without the second
         selector this highlight is unreachable on a touchscreen and unreachable
         from the keyboard, i.e. reachable only by the one input method a till
         is least often driven with.

         AUDIT -- and the band itself was rgba(255,255,255,0.03), one more HUD
         leftover: white at 3% alpha is visible over #080808 and is EXACTLY
         NOTHING over a white panel. 1.00:1. So the selector pair above was
         correct and the paint it applied was invisible, which is the worst of
         the two failure modes -- the code reads as if the keyboard operator is
         served, and reviewing it confirms that reading.

         --surface-active is the token for this: its own comment names it "the
         row under the cursor", and it is the DARKEST text-bearing surface, so
         every text and money token in the palette is already solved against it.
         A band drawn in it cannot push any cell it contains below its floor,
         whatever that cell turns out to hold. */
      .ret-table tbody tr:hover,
      .ret-table tbody tr:focus-within { background:var(--surface-active); }
      /* The in-row control. Styled to be visually indistinguishable from the
         cell text it replaced -- the row already reads as clickable -- so this
         adds a keyboard/AT path WITHOUT changing the layout. The underline is
         drawn in a transparent colour at rest and given a colour on hover/focus,
         so the text metrics never change and the row cannot reflow when focus
         lands on it. */
      .ret-rowbtn { appearance:none;-webkit-appearance:none;background:none;border:0;padding:0;margin:0;
        font:inherit;color:inherit;text-align:inherit;cursor:pointer;
        text-decoration:underline;text-decoration-color:transparent;text-underline-offset:3px; }
      .ret-rowbtn:hover,
      .ret-rowbtn:focus-visible { text-decoration-color:currentColor; }
      .ret-badge { display:inline-block;padding:2px 10px;border-radius:10px;font-size:11px;font-weight:600; }
      /* AUDIT -- each of these was a hue laid over a 12-15% tint OF ITSELF
         (#fbbf24 on rgba(251,191,36,0.15) = 1.31:1, #ef4444 on its own 15% =
         2.60:1). Text and fill being the same hue at two alphas pins the ratio
         near 1:1 BY CONSTRUCTION, so darkening the text rescues nothing while
         the fill tracks it -- and an alpha fill has no fixed luminance at all,
         so one badge class could not be right on the white card, the hovered
         row and the .ret-modal it all appears on simultaneously.

         css/main.css already restates these at .ret-badge.ret-badge-* (0,2,0),
         which is what makes the SCREEN correct today and what
         retail_design_contrast_test.js's self-contained-badge check reads. This
         restates the same token pairs at SOURCE, so the wrong values are gone
         from the codebase rather than merely outranked: _badge() emitting one
         class instead of two, or any ".ret-modal .ret-badge-red" added later,
         would have handed the 2.60:1 version straight back.

         (No backtick anywhere in this comment on purpose -- the whole sheet is
         a JS template literal, so one would end the string mid-stylesheet.)

         Same tokens, so the two declarations cannot disagree -- and if they
         ever do, the main.css copy is the one that wins and the one under test. */
      .ret-badge-green { background:var(--state-success-surface);color:var(--state-success-text); }
      .ret-badge-red   { background:var(--state-danger-surface);color:var(--state-danger-text); }
      .ret-badge-yellow{ background:var(--state-warning-surface);color:var(--state-warning-text); }
      .ret-badge-blue  { background:var(--state-info-surface);color:var(--state-info-text); }
      /* Purple carries no state -- see the note on the main.css override. */
      .ret-badge-purple{ background:var(--surface-active);color:var(--text-primary); }
      .ret-modal-overlay { position:fixed;inset:0;background:var(--surface-scrim);display:flex;align-items:center;justify-content:center;z-index:9999;backdrop-filter:blur(5px); }
      /* AUDIT -- the modal was a DARK ISLAND (#0f172a with #fff children) left
         over from the HUD, floating inside a now-light app. It was internally
         consistent, which is exactly why it survived the redesign: nothing
         inside it looked wrong.

         It could not stay. The moment .ret-table stopped forcing white text
         (see above), every table rendered inside a modal -- the customer
         Purchase History, the PO detail, the sale detail -- became dark on
         dark. And a money value converted to <span class="money"> in here
         rendered at 1.02:1, because .money sets its colour DIRECTLY and so
         beat the white the cell used to inherit. Both are the same root cause:
         a surface whose palette disagrees with the app's means every rule must
         know which of the two it is on, and eventually one of them forgets.

         Now light, on the same tokens as every other panel, so a rule written
         anywhere is correct here too. */
      .ret-modal { background:var(--surface-panel);border:1px solid var(--border-soft);border-radius:18px;padding:32px;width:520px;max-height:88vh;overflow-y:auto;box-shadow:var(--elevation-modal); }
      .ret-modal-wide { width:680px; }
      .ret-modal h3 { color:var(--text);margin:0 0 24px;font-size:20px;font-weight:700; }
      .ret-field { margin-bottom:15px; }
      .ret-field label { display:block;color:var(--text-muted);font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px; }
      .ret-field input,.ret-field select,.ret-field textarea {
        width:100%;background:var(--surface-sunken, #f2f5f8);border:1px solid var(--border-soft);
        border-radius:8px;color:var(--text);padding:10px 14px;font-size:14px;outline:none;
        min-block-size:44px;box-sizing:border-box;font-family:inherit;transition:.2s; }
      /* The focus halo was rgba(244,63,94,...) -- a ROSE tint, hardcoded, that
         stopped matching --sub-accent two redesigns ago, so a focused field
         glowed pink inside a blue-accented app. Built from --sub-accent-rgb,
         which exists precisely so a tint tracks the accent instead of dating
         from whenever it was typed. */
      .ret-field input:focus,.ret-field select:focus,.ret-field textarea:focus { border-color:var(--sub-accent);box-shadow:0 0 0 3px rgba(var(--sub-accent-rgb),0.18); }
      .ret-field select option { background:var(--surface-panel); color:var(--text); }
      .ret-field-row { display:grid;grid-template-columns:1fr 1fr;gap:12px; }
      .ret-field-row3 { display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px; }
      .ret-modal-footer { display:flex;gap:10px;justify-content:flex-end;margin-top:22px; }
      .ret-btn { padding:10px 20px;border-radius:8px;font-weight:600;font-size:14px;cursor:pointer;border:none;transition:all .2s; }
      .ret-btn-primary { background:var(--sub-accent);color:var(--text-on-accent); }
      .ret-btn-primary:hover { opacity:.88;transform:translateY(-1px); }
      .ret-btn-primary:disabled { opacity:.5;cursor:not-allowed;transform:none; }
      /* css/main.css owns this control at button.ret-btn.ret-btn-ghost (0,2,1),
         which is what fixed the 1.48:1 POS "Held" button. The literals it was
         outranking are corrected here too, so a .ret-btn-ghost that is not a
         <button> -- the one shape that selector does not reach -- is not still
         served #cbd5e1 on a 5% white tint. */
      .ret-btn-ghost { background:var(--surface-sunken);color:var(--text-secondary);border:1px solid var(--border-default); }
      .ret-btn-ghost:hover { background:var(--surface-hover); }
      /* AUDIT -- #ef4444 on a 12% tint of itself: 2.70:1 on the Delete/Discard/
         Process Refund buttons. Same self-tint construction as the badges above
         and the same fix: an opaque --state-danger-surface under
         --state-danger-text is 7.45:1 and does not care what is behind it.

         :hover DARKENS WITHIN THE FAMILY rather than inverting to a solid red
         fill, deliberately. An inverted fill would have to restate "color" as
         well, and the base rule's --state-danger-text is what makes this button
         legible; two colours that must be kept in step is how the base rule and
         its override drifted apart everywhere else in this file.
         --state-danger-border is the darkest step the danger family already
         has, so the hover is a real state change (5.45:1, still comfortably AA
         for the same text) with one declaration and no second colour to keep in
         sync. retail_btn_danger_hover_test.js guards that this rule exists at
         all -- it was missing entirely for a while, leaving the one destructive
         control in the product with no hover feedback. */
      .ret-btn-danger { background:var(--state-danger-surface);color:var(--state-danger-text);border:1px solid var(--state-danger-border); }
      .ret-btn-danger:hover { background:var(--state-danger-border); }
      .ret-btn-sm { padding:4px 12px;font-size:12px; }
      /* AUDIT -- #fff on rgba(255,255,255,0.05): 1.00:1. Not faint, INVISIBLE,
         and it is the search box on Products, Customers and Sales History, so a
         cashier filtering a product list could not see what they had typed. The
         white-on-white-alpha pair is the same shape as the .ret-table bug above
         and dates from the same dark HUD; it survived only because no rendered
         screen in the corpus builds a list header. Now a real sunken input on
         the same tokens as .ret-field's inputs, which is what it always was.

         .ret-date is the same control with a different job (the Sales History
         date bounds). It exists as a class because those two inputs were
         carrying this exact recipe as an INLINE style string -- a second copy
         of the paint that no stylesheet could correct, which is the hazard
         that put #10b981 on the Products table.

         .ret-input is the third consumer, and it is here because that hazard
         was still live on two screens nobody had ever rendered: the Reports
         branch/period dropdowns and the scanner test readout each carried this
         same recipe inline, outline:none included. An inline style CANNOT
         express a :focus state, so those three controls deleted the browser's
         focus ring and had no way to put anything back -- the Reports screen's
         only two controls, with no visible focus at all, on a machine driven by
         a scanner that is a keyboard. They also declared no inline-axis minimum
         (and the readout no minimum on either axis), which is the touch defect
         the same inline copy hid. Moving the paint here is what lets both be
         fixed in one place instead of three. One declaration, three consumers. */
      .ret-search,
      .ret-date,
      .ret-input { background:var(--surface-sunken);border:1px solid var(--border-default);border-radius:8px;color:var(--text-primary);padding:9px 14px;font-size:14px;outline:none; }
      .ret-search { width:240px; }
      /* AUDIT -- the base rule above sets outline:none, which DELETES the
         browser's own focus ring, and the only thing put back in its place was a
         1px border tint. retail_design_focus_test.js has a name for that shape:
         "strictly worse than no rule -- it deletes the browser default and puts
         nothing in its place". It is invisible to that test twice over, though:
         the outline:none lives on the BASE rule rather than on a :focus one, and
         this whole stylesheet is injected from JS at render time, which that
         test (which reads css/main.css only) never sees at all.

         The control it happens to is the list search box on Products, Customers
         and Sales History -- the field a cashier tabs into first, on the screens
         they spend the day in, on machines driven by a scanner that IS a
         keyboard.

         The answer was already four rules up: .ret-field's inputs take the same
         border colour PLUS a 3px accent halo built from --sub-accent-rgb. The
         note on .ret-search above says these two are "a real sunken input on the
         same tokens as .ret-field's inputs, which is what it always was" -- that
         was true of the resting paint and false of the focus state. Same tokens,
         same treatment, so the two input families cannot drift apart again.

         (No backtick anywhere in this comment on purpose -- the whole sheet is
         a JS template literal, so one would end the string mid-stylesheet.) */
      .ret-search:focus,
      .ret-date:focus,
      .ret-input:focus { border-color:var(--sub-accent);box-shadow:0 0 0 3px rgba(var(--sub-accent-rgb),0.18); }
      .ret-kpi-grid { display:grid;grid-template-columns:repeat(5,1fr);gap:16px;margin-bottom:22px; }
      @media(max-width:1300px){ .ret-kpi-grid{grid-template-columns:repeat(3,1fr);} }
      .ret-kpi { background:var(--surface-card);border:1px solid var(--border-soft);border-radius:14px;padding:20px;position:relative;overflow:hidden; }
      .ret-kpi::before { content:'';position:absolute;inset:0;background:radial-gradient(circle at 80% 20%,var(--sub-accent),transparent 65%);opacity:.1; }
      .ret-kpi-label { color:var(--text-faint);font-size:11px;text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px; }
      .ret-kpi-value { font-size:28px;font-weight:800;color:var(--text); }
      .ret-kpi-sub { font-size:12px;color:var(--text-faint);margin-top:4px; }
      .ret-kpi-breakdown { display:flex;gap:14px;margin-top:8px;padding-top:8px;border-top:1px solid var(--border-soft); }
      .ret-kpi-breakdown-item { font-size:11px;color:var(--text-faint); }
      .ret-kpi-breakdown-item b { display:block;font-size:14px;font-weight:700;margin-top:1px; }
      /* AUDIT -- the neon HUD greens/reds: #10b981 was 2.10:1 and #ef4444
         3.12:1 on a light KPI card. A period-over-period delta on a sales
         figure IS money moving, so these take the money tokens rather than the
         state ones -- money carries the palette's stricter AAA floor and is
         already solved against every surface a card can sit on. */
      .ret-kpi-change-up   { color:var(--text-money-positive);font-size:12px; }
      .ret-kpi-change-down { color:var(--text-money-negative);font-size:12px; }
      .ret-po-item { display:grid;grid-template-columns:2fr 1fr 1fr 1fr auto;gap:8px;align-items:center;padding:8px 0;border-bottom:1px solid var(--border-hairline); }
      /* Supplier modal tabs (Details / Contacts) -- no prior tab pattern existed
         in this file, so this is the new baseline; kept visually consistent
         with .ret-btn-ghost's muted/active language rather than inventing a
         new color language. */
      .ret-tabs { display:flex;gap:18px;margin-bottom:18px;border-bottom:1px solid var(--border-hairline); }
      .ret-tab { background:none;border:none;color:var(--text-muted);padding:10px 2px;font-size:13px;font-weight:600;cursor:pointer;border-bottom:2px solid transparent; }
      /* AUDIT -- both of these were #fff, i.e. 1.00:1 on the white modal these
         tabs live in: the resting tab was the only READABLE one, and pointing
         at a tab or selecting it erased its label. The muted->primary step is
         the emphasis the rest of the file uses for the same transition; the
         active tab additionally takes the accent, which is the one place the
         direction allows it -- "this is where you are" is semantic, and the
         underline beneath it already carries the same accent. */
      .ret-tab:hover { color:var(--text-primary); }
      .ret-tab.active { color:var(--accent-action);border-bottom-color:var(--sub-accent); }
      /* PO split-preview cards (Thursday demo, Stream B) -- one per supplier
         group, plus the Unassigned bucket which reuses the same card shape
         with an amber border to flag it needs operator action. */
      /* AUDIT -- the whole card was HUD-native: a 20%-BLACK wash for a surface
         (a dark-grey box floating in a white app, and an alpha fill with no
         fixed luminance, so nothing drawn on it had a measurable ratio), a
         white-alpha border that vanishes on white, a #fff supplier name at
         1.00:1, a #10b981 total at 2.10:1 and a #fbbf24-on-its-own-12%-tint
         warning at 1.32:1 -- the warning being the line whose entire job is to
         be noticed. Every one of the four is the same story as the badges.

         The total takes --text-money-positive, not a state green: it is an
         amount of money, so it belongs to the money palette and its AAA floor.
         The warning strip becomes an opaque --state-warning-* pair, so it reads
         the same whether the card is on the panel or nested in a modal. */
      .ret-po-split-card { background:var(--surface-raised);border:1px solid var(--border-default);border-radius:12px;padding:16px;margin-bottom:14px; }
      .ret-po-split-card-hdr { display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;gap:12px; }
      .ret-po-split-supplier { color:var(--text-primary);font-weight:700;font-size:15px; }
      .ret-po-split-meta { color:var(--text-muted);font-size:12px;margin-top:2px; }
      .ret-po-split-total { color:var(--text-money-positive);font-weight:700;font-size:16px;white-space:nowrap; }
      .ret-po-split-warning { background:var(--state-warning-surface);color:var(--state-warning-text);border-radius:8px;padding:8px 12px;font-size:12px;font-weight:600;margin-bottom:10px; }
      .ret-po-split-contact { display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap; }
      .ret-po-split-contact-detail { color:var(--text-muted);font-size:12px; }
      /* The Unassigned bucket's border is a FLAG -- "this one needs you" -- so
         it takes the warning family's text weight rather than its hairline
         border tint, which at 1px on a raised surface reads as no flag at all. */
      .ret-po-split-unassigned { border-color:var(--state-warning-text); }
    `;
    document.head.appendChild(s);
  },

  // ── Money: one number, three sinks ─────────────────────────────────────────
  //
  // Which helper a call site needs is decided by its SINK, not by taste. The
  // three are listed here together because the whole hazard is picking the
  // wrong one:
  //
  //   _fmt(n)          PLAIN STRING, ASCII hyphen. For a sink that cannot take
  //                    markup and is not itself a money element: showToast()
  //                    (which assigns textContent), the Charge button's label,
  //                    and _printReceipt()'s separate print document.
  //   _money(n, cls)   A <span class="money">. For an amount COMPOSED INTO
  //                    larger markup — a table cell, a sentence, a card.
  //   _setMoney(el,n)  For an element that IS the value (its template already
  //                    gives it `.money`) and is filled in after a fetch.
  //
  // Negative amounts read as "-$120.00", not "$-120.00" — matters now that a
  // net-negative revenue figure (returns exceeding sales) is displayed as-is
  // rather than hidden/clamped.
  _fmt(n) {
    const v = +(n || 0);
    // The mark and the decimal count both come from the server (see
    // _currencySymbol above). This used to be a hard-coded '$' and toFixed(2),
    // which meant a Jordanian shop -- the product's home market -- saw its
    // dinars labelled in dollars AND truncated to two decimals, losing the
    // fils. A non-negotiable fix: it is the first thing visible in a demo.
    //
    return (v < 0 ? '-' : '') + this._currencyPrefix() + Math.abs(v).toFixed(this._currencyDp());
  },
  _fmtNum(n) { return (+(n||0)).toLocaleString(); },

  // ── The two currency helpers _fmt and _moneyDigits share ─────────────────
  //
  // In ONE place so the plain-string formatter and the styled-element one can
  // never disagree about what money looks like — the same reasoning the
  // U+2212 note below gives for the minus glyph.

  //: Decimal places, from the server. Falls back to 2 only if a response
  //: somehow arrived without the field; the constructor default is 3 (JOD).
  _currencyDp() {
    const d = this._currencyDecimals;
    return d == null ? 2 : d;
  },

  //: The mark plus its separator. A SINGLE-CHARACTER mark hugs its digits
  //: ($100.00, €9.50) because that is the universal convention for symbol
  //: currencies; a multi-letter mark takes a space (JD 100.000, SEK 120.00)
  //: because "JD100.000" reads as one token and is genuinely hard to scan at a
  //: glance on a till.
  //:
  //: The rule is on LENGTH rather than a per-currency list deliberately: it
  //: gets every currency right, including ones nobody has added yet, and there
  //: is no table to forget to update. It is also why the existing dollar-based
  //: tests keep passing untouched — the format for $ is unchanged.
  _currencyPrefix() {
    const raw = this._currencySymbol || '';
    if (!raw) return '';
    // Through t(): a currency MARK is a user-visible string like any other.
    // 'JD' is what a Jordanian shop prints in English; the Arabic form is
    // د.أ, and a till running in Arabic that still said 'JD' would be the
    // same half-translated feel the surface i18n ratchet exists to prevent.
    // t() returns its input unchanged for anything not in the catalogues, so
    // an unlisted currency degrades to its own mark rather than to blank.
    const sym = (typeof t === 'function') ? t(raw) : raw;
    return sym.length === 1 ? sym : sym + ' ';
  },

  // The digits, in ONE place, so _money() and _setMoney() can never disagree
  // about the glyph. U+2212 MINUS SIGN, not U+002D HYPHEN: it is wider, sits on
  // the digit midline, and cannot be misread as a hyphen in a product name.
  //
  // _fmt() above deliberately does NOT delegate here. It feeds a toast, a
  // button label and a print document — sinks where the ASCII hyphen is the
  // safe character and where none of the .money styling exists to pair with a
  // typographic minus. Two spellings, each stated once, is the honest shape;
  // one spelling forced on both would be a silent change to what a receipt
  // prints.
  _moneyDigits(n) {
    const v = +(n || 0);
    // Currency-aware, same server-resolved source as _fmt (see
    // _currencySymbol). The U+2212 minus above is unchanged and deliberate --
    // only the MARK and the DECIMAL COUNT vary by currency, never the glyph
    // this function exists to standardise.
    return (v < 0 ? '−' : '') + this._currencyPrefix() + Math.abs(v).toFixed(this._currencyDp());
  },

  // Read a design token's resolved value for the one consumer that cannot use
  // var() at all: Chart.js, which takes plain colour strings. Everything else
  // on these screens references tokens directly in CSS; this exists so the
  // charts follow the same palette instead of carrying a private copy of it.
  // The fallback is only reached when the token is genuinely absent (an older
  // cached main.css), never as a styling choice.
  _cssToken(name, fallback) {
    try {
      const v = (getComputedStyle(document.documentElement).getPropertyValue(name) || '').trim();
      return v || fallback;
    } catch (e) {
      return fallback;   // no computed style (tests / non-DOM host)
    }
  },

  // ── Money as MARKUP (presentational; _fmt above stays the plain-text form) ──
  //
  // _fmt() is consumed by receipt printing, toasts and several tests, so its
  // return value is deliberately untouched. This is the on-screen form, and it
  // exists to satisfy one rule the plain string cannot:
  //
  //   A NEGATIVE AMOUNT MUST BE DISTINGUISHABLE WITHOUT COLOUR.
  //
  // _fmt() renders -120 as "-$120.00" and every previous screen then coloured
  // it red and called that done. A red minus sign is invisible to a
  // colourblind cashier, and on the washed-out 6-bit panel most shops actually
  // own it is invisible to everyone. So a negative gets THREE independent
  // signals here, only one of which is colour:
  //   * accounting parentheses -- the convention every finance professional
  //     already reads, and the one that survives greyscale and photocopying;
  //   * a real U+2212 MINUS SIGN rather than U+002D hyphen, which is wider,
  //     sits on the digit midline, and cannot be mistaken for a hyphenated
  //     product name;
  //   * .money--negative, which css/main.css colours from
  //     --text-money-negative AND sets bold.
  // Strip the colour entirely and the amount is still unambiguous. That is the
  // test.
  //
  // The class names below are the token layer's (css/main.css), not invented
  // here: `.money` supplies tabular-nums + slashed-zero + nowrap, and
  // `.money--negative` supplies the --text-money-negative colour AND a bold
  // weight. `.money--accounting` is that stylesheet's opt-in parenthesis pair,
  // rendered via ::before/::after -- which is why this function emits the
  // MINUS SIGN ONLY and never a literal bracket: doing both would render
  // "((−$65.44))". Opting every negative on the till and the dashboard into
  // the accounting form is deliberate; those are exactly the screens read on
  // washed-out shop monitors and printed in black and white.
  //
  // Net effect: three independent signals for a negative, of which only one is
  // colour -- bold weight, accounting parentheses, and the U+2212 glyph.
  //
  // rtl.css already forces .money to direction:ltr, so the mirrored Arabic
  // layout renders the figure itself correctly too.
  _money(n, extraClass) {
    const v = +(n || 0);
    const neg = v < 0;
    const digits = this._moneyDigits(v);
    const cls = 'money' + (neg ? ' money--negative money--accounting' : '') +
                (extraClass ? ' ' + extraClass : '');
    return `<span class="${cls}"${neg ? ' data-sign="negative"' : ''}>${digits}</span>`;
  },

  // Fill in an element that IS the amount — #pos-total, #r-k-rev and their
  // siblings, each of which already carries `.money` in its own template.
  //
  // WHY THIS EXISTS, AND WHY IT IS NOT `_money()` INTO innerHTML
  //
  // Those elements were written with `el.textContent = this._fmt(n)`, and that
  // one line is why the negative cue was INHERITED rather than exact. _fmt()
  // emits a bare ASCII hyphen and cannot touch a class, so:
  //   * `.money--negative` (bold + --text-money-negative) and
  //     `.money--accounting`'s parentheses had no way of reaching the value —
  //     nothing ever put those classes on the element;
  //   * a net-negative Revenue Today, which is the whole reason that figure is
  //     shown unclamped, was distinguishable from a positive one by a single
  //     hyphen and nothing else. On the washed-out 6-bit panels these installs
  //     run on, that is not a distinction.
  // The classes are toggled HERE, on the element the template already declared
  // as `.money`. Nesting a second `<span class="money">` inside a `.money`
  // element would double the rule and, worse, turn a textContent sink into an
  // innerHTML one for no gain — this writes TEXT, because an amount is text.
  //
  // classList is feature-tested rather than assumed: several harnesses in
  // products/retail/tests/ hand this file element stubs, and a helper that
  // throws on a stub would take the whole render down with it.
  _setMoney(el, n, extraClass) {
    if (!el) return el;
    const neg = +(n || 0) < 0;
    el.textContent = this._moneyDigits(n);
    const cl = el.classList;
    if (cl && typeof cl.toggle === 'function') {
      cl.toggle('money--negative', neg);
      // Paired with money--negative, never alone: css/main.css renders the
      // parentheses off `.money--accounting.money--negative`, so the two have
      // to move together or a positive amount grows brackets.
      cl.toggle('money--accounting', neg);
      if (extraClass) cl.add(extraClass);
    }
    return el;
  },

  // The Charge button's label, in one place. _recalc() and both of _checkout()'s
  // failure paths used to build this string independently, which is how the
  // three of them would have drifted the moment any of them was translated.
  _checkoutLabel(total) { return `${t('Charge')} — ${this._fmt(total)}`; },

  // Today's date, in the language the operator is actually using.
  //
  // Both the dashboard header and the cashier landing hardcoded 'en-US' here,
  // so an Arabic page rendered every other word in Arabic and then printed
  // "Saturday, August 22, 2026" underneath the heading. A date is not a
  // catalog string -- there is no sane way to enumerate every weekday/month
  // combination in a JSON file -- so the fix is to let Intl do the job it
  // exists for, keyed off the language the user already chose.
  //
  // Falls back to 'en-US' when AuraI18n has not loaded (standalone renders and
  // every test in products/retail/tests/ run this file without it), which is
  // the previous behaviour exactly.
  _localeDate(d) {
    const when = d || new Date();
    const lang = (window.AuraI18n && AuraI18n.current === 'ar') ? 'ar' : 'en-US';
    const opts = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
    try { return when.toLocaleDateString(lang, opts); }
    catch (e) { return when.toLocaleDateString('en-US', opts); }
  },

  // ── Scan focus retention ───────────────────────────────────────────────────
  //
  // Scanning is the till's primary input, and a HID scanner types into
  // whatever currently holds focus. _onScannerKey() below deliberately REFUSES
  // to auto-detect a burst while focus sits in a text field that is not one of
  // the search boxes -- otherwise a fast typist would lose characters. The
  // consequence nobody had wired up: any stray tap that moved focus (a product
  // tile, the pane background, a quantity button) left the till silently
  // unable to scan, with no visible symptom, until the cashier thought to
  // click back into the search box. In a queue, that reads as "the scanner
  // broke".
  //
  // These ids are the fields where a caret is there ON PURPOSE. Reclaiming
  // focus from them would be a worse bug than the one this fixes: it would
  // make the discount and cash-tendered boxes literally untypeable, because
  // every keystroke fires an oninput -> _recalc()/_calcChange() -> re-render
  // path that would yank the caret away mid-number.
  _POS_FOCUS_KEEPERS: ['pos-search', 'pos-disc', 'pos-tendered', 'pos-customer'],

  // Returns true only if focus was actually moved, so callers (and the tests)
  // can tell "the guard ran and declined" from "the guard never ran".
  _refocusScan(force) {
    const doc = typeof document !== 'undefined' ? document : null;
    if (!doc) return false;
    const scan = doc.getElementById('pos-search');
    // Guarded rather than assumed: this runs from _renderCart(), which several
    // existing tests drive against element stubs that have no focus().
    if (!scan || typeof scan.focus !== 'function') return false;
    if (!force) {
      const el = doc.activeElement;
      if (el && el.id && this._POS_FOCUS_KEEPERS.indexOf(el.id) !== -1) return false;
    }
    try { scan.focus(); } catch (e) { return false; }
    return true;
  },

  // Bound to mousedown (NOT click) on every non-input region of the POS.
  // The browser moves focus to the pressed element before any click handler
  // runs, so preventDefault() here is the only thing that stops the blur from
  // happening at all; refocusing after the fact would still blur-then-refocus,
  // which on a touchscreen dismisses the on-screen keyboard and visibly
  // flickers the caret. Text controls are exempted so they can still be
  // focused by tapping them, and <select> especially -- swallowing its
  // mousedown would stop the dropdown from opening.
  _keepScanFocus(e) {
    const el = e && e.target ? e.target : null;
    const tag = el && el.tagName ? String(el.tagName).toUpperCase() : '';
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || (el && el.isContentEditable)) return true;
    if (e && typeof e.preventDefault === 'function') e.preventDefault();
    this._refocusScan();
    return false;
  },

  _badge(text, color) { return `<span class="ret-badge ret-badge-${color||'blue'}">${text}</span>`; },
  // Held-sale labels are free text a cashier types on the spot (unlike
  // product/customer names, which come from admin-entered catalog data) --
  // escaped before going into innerHTML so a stray `<`/`"`/`'` can't break
  // the held-sales list markup or self-XSS the page.
  _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  },

  // ── ATTRIBUTION RENDERING (schema v13) ────────────────────────────────────
  //
  // v13 (_migrate_add_identity_and_attribution_columns, backend/database/
  // schema.py) put `actor_user_uid` / `terminal_id` / `created_at_utc` on
  // sales, returns, inventory_movements, cash_sessions and cash_movements.
  // Two of that migration's decisions dictate everything these three helpers
  // do, and both are deliberate rather than incidental:
  //
  //   1. Existing rows were left NULL. The migration would only have been
  //      able to stamp them with THIS machine's identity and THIS machine's
  //      current UTC offset, neither of which is evidence of anything about a
  //      sale rung last year on another terminal. So a permanent slice of
  //      every real install's history carries no attribution at all -- not a
  //      transient gap that backfills later, a fact about the data.
  //
  //   2. The pre-existing free-text `cashier` column was KEPT, and is never
  //      rewritten or mined to derive `actor_user_uid`, because "a wrong name
  //      on a sale is worse than no name". It is the only surviving evidence
  //      of who the shop believed rang a transaction.
  //
  // The display rule that falls out: show the best-resolved thing the row
  // actually carries, say "not recorded" in words when it carries nothing,
  // and never manufacture the difference. Specifically, do NOT try to detect
  // whether the free text "looks like a person's name" and substitute
  // something friendlier when it doesn't -- today it is almost always a raw
  // UUID (`create_sale` defaults it to `_uid()`, i.e. session['mt_user_id'],
  // and the POS has never sent a name), and dressing that up is the exact
  // guess v13 refused to make at the database layer.

  // An id/number-bearing value, isolated from the bidirectional algorithm.
  //
  // This product renders `dir="rtl"` on <html> in Arabic (i18n.js apply()),
  // and attribution values are ASCII identifiers sitting beside Arabic labels,
  // currency and dates -- the exact position where the Unicode bidi algorithm
  // misbehaves. An unisolated run of neutral/Latin characters takes its
  // direction from the surrounding paragraph, so a hyphenated UUID gets
  // visually reordered around its own hyphens and a receipt number lands on
  // the wrong side of its label. <bdi> is the element the HTML spec added for
  // exactly this case (it isolates a span of unknown-directionality text), it
  // needs no library, and this frontend has no build step to add one.
  //
  // `full` is kept whole in the title attribute even when the visible text is
  // shortened: a truncated-only id cannot be matched against
  // device_registry.devices when someone actually needs to identify a
  // terminal, and "which till" questions are asked precisely when something
  // has gone wrong.
  // `dir` defaults to "ltr" because every current caller passes a value that
  // is definitionally Latin/numeric -- a receipt number, a timestamp, a uuid.
  // For those, "ltr" is strictly better than <bdi>'s own dir="auto" default:
  // auto picks its direction from the first STRONG character, and a value
  // like "2026-08-19 14:30" contains none at all, so auto falls back to the
  // paragraph (RTL in Arabic) and the two number runs swap places -- the
  // timestamp renders as "14:30 2026-08-19". Isolation alone does not fix
  // that; the direction has to be stated.
  //
  // Callers with genuinely unknown content pass 'auto' instead. See
  // _attribution(), where the value may be an opaque id today and an
  // Arabic-script name tomorrow, and forcing ltr would be the mirror image
  // of the bug this helper exists to prevent.
  _bdi(text, full, dir) {
    const shown = this._esc(text);
    const title = (full != null && full !== text) ? ` title="${this._esc(full)}"` : '';
    return `<bdi dir="${dir || 'ltr'}"${title}>${shown}</bdi>`;
  },

  // ── The keyboard path into a clickable table row ───────────────────────────
  //
  // Three tables in this file list sales and open one on click: the dashboard's
  // Recent Transactions, Sales History, and a customer's Purchase History. All
  // three shipped as `<tr style="cursor:pointer" onclick="..._viewSale(id)">`
  // and NOTHING ELSE, which means:
  //
  //   * a <tr> is not focusable, so there was no keyboard route to the sale
  //     detail at all — not a poor one, none. A till is driven by a scanner and
  //     a keyboard more than by a mouse, and a scanner IS a keyboard;
  //   * `cursor:pointer` plus a `:hover` background is a POINTER-ONLY
  //     affordance. On the touchscreens a large share of these installs run,
  //     there is no hover, so the row never announced itself as clickable at
  //     all;
  //   * a screen reader read six table cells and no control, because a click
  //     handler on a <tr> confers no role.
  //
  // The row stays a row. Turning a <tr> into a button would break the table
  // semantics that make the columns readable in the first place (and a
  // `role="button"` on a <tr> would strip the row from the table's grid for an
  // AT user). What the row CONTAINS is a real <button>: native focusability,
  // native Enter AND Space activation, native `button` role, and a focus ring
  // it cannot drift out of sync with — none of which a `tabindex`/`role`/
  // `keydown` retrofit gives for free, and all of which that retrofit would
  // have to keep re-earning on every edit.
  //
  // The row keeps its own click handler, because a full-row pointer target is
  // genuinely better with a finger or a mouse. That is why the button stops
  // propagation: without it, one click on the button would run _viewSale twice
  // — once for the button, once for the row it bubbled to — and open the sale
  // detail on top of itself.
  //
  // The accessible name is the verb plus the receipt number, so an AT user
  // hears "View invoice SALE-000012, button" rather than a bare id. Both halves
  // are needed: the number alone does not say what the control does, and the
  // verb alone does not say WHICH sale, which is the only question a list of
  // eight rows raises.
  _saleOpenerButton(saleId, saleNumber) {
    const id = Number(saleId);
    const num = saleNumber == null ? '' : String(saleNumber);
    return `<button type="button" class="ret-rowbtn"` +
      ` onclick="event.stopPropagation();RetailSystem._viewSale(${Number.isFinite(id) ? id : 0})"` +
      ` aria-label="${this._esc(t('View invoice'))} ${this._esc(num)}"` +
      `>${this._bdi(num)}</button>`;
  },

  // The same control, for the Customers list -- which had the identical defect
  // and was simply not in the render corpus when the three sale tables were
  // fixed. `<tr onclick="..._viewCustomer(id)">` with nothing focusable inside
  // it is mouse-only: no Tab route, no Enter/Space, and a screen reader reads
  // six cells and no control.
  //
  // The NAME is the cell that becomes the button, for the same reason the
  // receipt number is on the sale rows: it is the value that identifies which
  // record the row is. Wrapping a different cell (the spend, say) would name
  // the control after a number that changes.
  //
  // THE PHONE JOINS THE ACCESSIBLE NAME, and that is the one real difference
  // from _saleOpenerButton. A receipt number is unique by construction; a
  // PERSON'S NAME IS NOT. Shops routinely carry two "Ahmad"s, and the phone is
  // the de-facto customer key here -- it is the column immediately after the
  // name and it is what the search box above the table offers to match on
  // ("Search name, phone, e…"). So an operator tabbing the list hears
  // "View customer Ahmad 0791111111" and can tell two same-named rows apart,
  // which is the entire question a list of eight rows raises. The visible text
  // stays the name alone: the phone is already its own column, and repeating it
  // on screen would be noise for everyone who can see it.
  //
  // A customer with no phone on file falls back to the name alone. That is a
  // correct, if less specific, label -- the same position a sale with no
  // receipt number is in.
  //
  // The id is interpolated exactly as the row's own handler already does it --
  // one escaping rule for both, so they cannot disagree about what a customer
  // id is allowed to contain.
  _customerOpenerButton(customerId, customerName, customerPhone) {
    const name = customerName == null ? '' : String(customerName);
    const phone = customerPhone == null ? '' : String(customerPhone).trim();
    const label = `${t('View customer')} ${name}${phone ? ' ' + phone : ''}`;
    return `<button type="button" class="ret-rowbtn"` +
      ` onclick="event.stopPropagation();RetailSystem._viewCustomer('${this._esc(customerId)}')"` +
      ` aria-label="${this._esc(label)}"` +
      `>${this._esc(name)}</button>`;
  },

  // Canonical uuid4 shape. Used ONLY to decide how many characters of a value
  // to show -- never what value to show, and never to reclassify a row as
  // attributed or unattributed. Shortening a 36-character uuid to a stable
  // leading fragment keeps the invoice header on one line on a till screen
  // while the full string stays one hover (or one DOM inspection) away; a
  // human-entered name, which does not match this, is never truncated.
  _looksLikeUuid(v) {
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(String(v || ''));
  },

  // Renders ONE RECORDED IDENTIFIER honestly: the value the row carries, or
  // the words "Not recorded" when it carries none. That last branch is the one
  // that matters. A blank cell and an em dash both read as "the page failed" to
  // the manager who opened this view because a till came up short; only words
  // distinguish "nobody wrote this down" from "something broke", and that
  // distinction is the whole point of showing attribution at all.
  //
  // This used to take a second `resolvedName` argument and print it when
  // present. That branch is gone, not merely unused: resolving an id to a
  // PERSON is a different question with a different answer set (see
  // _attributionState below), and keeping a name branch here meant this
  // function and _attributionCell held two independent opinions about how to
  // render an employee -- including opposite opinions on bidi isolation, which
  // is exactly the drift that produces two screens naming the same row two
  // ways. What is left is the till case and the identifier case, which is all
  // any caller now asks it for.
  _attribution(value) {
    const raw = (value == null ? '' : String(value)).trim();
    if (!raw) {
      return `<span style="color:var(--text-faint)">${t('Not recorded')}</span>`;
    }
    // 'auto', not the 'ltr' default. This branch renders whatever the row
    // recorded, and that is an opaque identifier today only because
    // create_sale happens to default `cashier` to session['mt_user_id'] --
    // the column is free text and an install that ever writes a real name
    // into it may write an Arabic one. dir="auto" isolates it either way and
    // lets the content choose its own direction, which is exactly the case
    // <bdi> was designed for.
    const shown = this._looksLikeUuid(raw) ? raw.slice(0, 8) + '…' : raw;
    return this._bdi(shown, raw, 'auto');
  },

  // ── WHO a row belongs to: one classifier, two screens ─────────────────────
  //
  // `_attribution()` above answers "what identifier does this row carry", which
  // is the whole question for a till. A PERSON is a harder question, because
  // the answer depends on something the row does not carry: whether the server
  // looked the uid up and what it found. The by-employee report and the sale
  // detail were each answering it separately and were about to answer it
  // differently, so it is answered once, here.
  //
  // The settled route contract (see this wave's contract document, §2.5-2.6)
  // gives every by-employee row four identity fields, all four null together
  // for the unattributed bucket:
  //
  //   actor_user_uid | employee_id | email | employee_name
  //
  // `employee_name` is the SERVER's pre-formatted display string, derived from
  // the same `email`/`employee_id` columns Android receives and in the same
  // order -- which is the point: two clients reading one value cannot disagree
  // about who a row is. This file reads `employee_name` first and falls back to
  // the raw columns, so it renders the same name whichever of the two a given
  // build sends.

  // The name to print, or null when there is nothing truthful to print.
  // Blank is NOT an identity: the contract names `"email": ""` explicitly as a
  // server bug to defend against, and a nameless name is worse than an honest
  // absence -- it looks resolved, points at nobody, and is indistinguishable
  // from a rendering fault. Mirrors attributedName() in
  // android/.../ui/screens/EmployeeSalesScreen.kt, email before employee_id,
  // because EmployeesScreen shows staff that way round and a report that
  // ordered them the other way reads as being about different people.
  _identityOf(row) {
    const r = row || {};
    const pick = (v) => {
      const s = (v == null ? '' : String(v)).trim();
      return s || null;
    };
    return pick(r.employee_name) || pick(r.email) || pick(r.employee_id);
  },

  // Did the SERVER try to resolve this row's uid to a person?
  //
  // KEY PRESENCE, deliberately, not value truthiness. "The route sent
  // `employee_name: null`" and "the route has no such field" are different
  // facts, and only the first licenses the inference "this uid could not be
  // resolved, therefore the account is gone".
  //
  // Both cases are live, not hypothetical. GET /sales/<id> is
  // `SELECT s.*, COALESCE(c.name,'Walk-in') AS customer_name` and has never
  // carried an identity column at all -- so on the sale detail nobody has
  // looked, and saying "Account removed" there would be a confident claim
  // derived from an absence, which is the exact move v13 refused to make when
  // it left old rows NULL rather than stamping them with this machine's
  // identity. The same applies across version skew: the Android build ships
  // its own embedded Python server, so a newer frontend regularly talks to an
  // older backend whose by-employee route returns metrics.py's raw rows with
  // no identity fields on them.
  _resolutionAttempted(row) {
    const r = row || {};
    if (typeof r !== 'object') return false;
    return ('employee_name' in r) || ('email' in r) || ('employee_id' in r);
  },

  //   'named'        a person, by name
  //   'account_gone' a recorded actor the server looked up and did not find
  //   'recorded_id'  a recorded actor nobody looked up
  //   'unattributed' no actor was ever recorded (every row written before v13)
  //
  // Three of these are the states the contract names; 'recorded_id' is the one
  // that keeps 'account_gone' honest, and dropping it is how the three become
  // a lie on any surface that does not resolve names.
  //
  // ── The uid is checked FIRST, and that ordering is the whole function ─────
  //
  // This used to test the name first and return 'named' without ever
  // consulting `actor_user_uid`, so `{actor_user_uid: null, employee_name:
  // "Sara Haddad"}` resolved 'named' here while Android's attributionOf()
  // (EmployeeSalesScreen.kt, uid checked first) resolved NOT_RECORDED for the
  // identical row. Two clients reading one response and naming two different
  // people is the exact drift the shared contract was settled to prevent, and
  // it is the desktop that was wrong.
  //
  // Untidy on the sale detail; dangerous on the by-employee report, because
  // there the null-uid row is not one sale -- it is the AGGREGATE of every
  // sale rung before v13 added the column. A name landing on it hands the
  // shop's entire pre-v13 history to one employee, which is the fabrication
  // v13 refused when it left old rows NULL rather than backfilling them from
  // the free-text `cashier` column ("a wrong name on a sale is worse than no
  // name"), arriving at the last step instead of the first. A name does not
  // need a malicious server to get there: a joined-in default, a "POS"
  // placeholder, or a display string computed before the uid was consulted all
  // produce it.
  //
  // So: a name is only ever shown when a NON-BLANK ACTOR UID resolved to it.
  // Both halves are required, and blank-is-absent applies to the uid for the
  // same reason it already applies to the name in _identityOf() -- Android
  // uses isNullOrBlank() on this field and a uid made of spaces is not a
  // recorded actor.
  //
  // The three attributed states stay genuinely three below the uid check: a
  // recorded uid with no resolvable name is 'account_gone' (or 'recorded_id'
  // where nobody looked), and folding either into 'unattributed' would make a
  // departed employee's takings vanish from the audit somebody is running
  // specifically to find them.
  _attributionState(row) {
    const r = row || {};
    const uid = (r.actor_user_uid == null ? '' : String(r.actor_user_uid)).trim();
    if (!uid) return 'unattributed';
    if (this._identityOf(r)) return 'named';
    return this._resolutionAttempted(r) ? 'account_gone' : 'recorded_id';
  },

  // GET /sales/<id> resolves the same identity through the same helper the
  // by-employee route uses, but names two of the three fields differently:
  // `actor_employee_id` / `actor_email` (Android reads those two directly)
  // alongside the shared `employee_name`. This maps them onto the row shape
  // above WITHOUT inventing keys -- key presence is what tells
  // _resolutionAttempted() that the server looked, so a field an older
  // backend did not send must not appear here carrying `undefined`. Get that
  // wrong and every sale on a pre-resolution build reports its cashier's
  // account as deleted.
  _saleIdentityRow(sale) {
    const s = sale || {};
    const row = { actor_user_uid: s.actor_user_uid };
    if ('employee_name' in s)     row.employee_name = s.employee_name;
    if ('actor_email' in s)       row.email = s.actor_email;
    if ('actor_employee_id' in s) row.employee_id = s.actor_employee_id;
    return row;
  },

  // The identity cell for one row. `legacyValue` is an optional free-text
  // value that is evidence but NOT an identity -- see the sale detail's
  // `cashier` column, kept reachable in the tooltip and kept out of the
  // rendered answer.
  _attributionCell(row, legacyValue) {
    const state = this._attributionState(row);

    if (state === 'named') {
      // Wrapped now, where the earlier version returned a bare escaped string.
      // The contract's display value is an EMAIL or an `EMP-000n` -- strongly
      // Latin text with neutral characters in it (@ . -) sitting in a table
      // cell that is right-to-left in Arabic, which is precisely where the
      // bidi algorithm resolves those neutrals against the wrong run and moves
      // them to the wrong side of the name. dir="auto" isolates WITHOUT
      // asserting a direction, so an Arabic name still renders right-to-left
      // inside the same wrapper -- the same choice Android made with FSI/PDI
      // (bidiIsolate(), EmployeeSalesScreen.kt), for the same reason.
      return this._bdi(this._identityOf(row), null, 'auto');
    }

    if (state === 'unattributed') {
      const legacy = (legacyValue == null ? '' : String(legacyValue)).trim();
      // Free text is not promoted into the visible answer, but it is not
      // destroyed either: v13 kept the column because it is the only surviving
      // evidence of who the shop BELIEVED rang a transaction. Tooltip, under
      // its own column name, so what is being looked at is never in doubt.
      const title = legacy ? ` title="${this._esc(t('Cashier') + ': ' + legacy)}"` : '';
      return `<span style="color:var(--text-faint)"${title}>${t('Not recorded')}</span>`;
    }

    const uid = String((row || {}).actor_user_uid).trim();
    const identifier = this._attribution(uid);
    if (state === 'recorded_id') return identifier;

    // 'account_gone'. The words and the uid are separate nodes on purpose:
    // i18n.js's catalog sweep matches a text node's FULL trimmed text, so a
    // label concatenated with an id can never be translated, and its leading
    // Latin character would drag the whole cell left-to-right under dir="rtl"
    // anyway. Same wording Android prints for this state, so the two clients
    // cannot describe the same row differently. The uid stays as evidence --
    // it is what makes the row traceable at all once the account is gone.
    return `<span style="color:var(--text-faint)">${t('Account removed')}</span> ${identifier}`;
  },

  // ── DASHBOARD ─────────────────────────────────────────────────────────────
  // UI/UX polish (feat/retail-ui-ux-polish): all dashboard-only styling is
  // scoped under .rdash in this render's own <style> block (same pattern the
  // POS screen already uses) so the shared .ret-* classes that Products/
  // Customers/etc. also consume are NOT restyled from here. Two goals:
  //   1. Hierarchy — Revenue (Net) is the number an owner opens this screen
  //      for, so it alone gets the accent "hero" treatment; the other four
  //      KPIs stay quiet.
  //   2. Theme correctness — the old hardcoded #fff/rgba(0,0,0,.25) values
  //      rendered white-on-white in light mode; everything now reads the
  //      semantic tokens (--text/--surface-card/--border-soft) instead.
  async _renderDashboard(c) {
    this._injectStyles();

    // Cashier landing (a cashier's first screen after login must not be a
    // 403). GET /api/sub/retail/dashboard/stats is gated on `retail.reports`,
    // which a cashier does not hold by default (user_accounts.
    // ROLE_CAPABILITIES) -- and EVERY tile below (both KPI cards, both
    // charts, the recent-transactions table) reads from that one endpoint's
    // response, so there is no partial/per-tile fetch to fall back to: it is
    // this whole screen or nothing. Rendering it anyway and eating the 403
    // would be exactly the "screen that generated an error" this capability
    // work exists to stop -- see employees.js's own render() for the
    // established "degrade honestly, check before fetching" pattern this
    // mirrors.
    //
    // `SubsystemApp.hasCapability` fails OPEN when `capabilities` is not an
    // array (session not resolved yet, or /api/auth/session hasn't shipped
    // the field yet -- see that method's own comment), so this branch is
    // INERT until the backend lands `user.capabilities`, not a dashboard
    // that blanks out for everyone the moment this file ships. The `window.
    // SubsystemApp &&` guard additionally keeps this file loadable standalone
    // (as every *_dashboard_*_test.js in products/retail/tests/ already does,
    // with no SubsystemApp stub at all) without throwing.
    if (window.SubsystemApp && !SubsystemApp.hasCapability('retail.reports')) {
      return this._renderCashierLanding(c);
    }

    // OPERATIONAL CALM — dashboard. What changed and why.
    //
    // This screen was five equally-weighted KPI cards in a row, plus two
    // charts, plus a table, every one of them in its own bordered panel.
    // Uniform emphasis is the same as no emphasis: there was no answer to
    // "what do I look at first", so the eye landed wherever it happened to.
    //
    // The question this screen now answers is a specific one -- WHAT DOES AN
    // OWNER OPENING THIS AT 9AM NEED TO KNOW? -- and that changes the ranking
    // materially, because at 9am today's revenue is close to zero. The old
    // design made that near-zero the hero: the loudest, largest, accent-washed
    // element on the page was "$0.00", every single morning. A hero tile that
    // is only meaningful after lunch is a hero tile designed for a screenshot,
    // not for the person who opens the shop.
    //
    // So, in order:
    //
    //   1. TODAY SO FAR — still first, because it is the live pulse, but
    //      LABELLED so that $0.00 at 9am reads as "we have not opened yet"
    //      rather than as a catastrophe. The figure carries the day's
    //      transaction count and average ticket with it, since a revenue
    //      number with no volume beside it cannot be interpreted.
    //
    //   2. NEEDS ATTENTION — promoted from the smallest tile on the screen to
    //      a band of its own directly under the headline, because low stock is
    //      the ONLY thing on this entire screen an owner can act on at 9am,
    //      and acting on it before the day starts is the whole point. It is
    //      also the one place colour genuinely carries meaning here, so it is
    //      one of the only places colour appears. When nothing needs doing it
    //      collapses to a quiet single line rather than a red zero — a
    //      dashboard that shouts on a good morning teaches people to ignore it.
    //
    //   3. CONTEXT (month-to-date, customers, catalogue size) — demoted to a
    //      small type strip with no card chrome at all. These are reference
    //      figures, not decisions. They are separated from the above by
    //      rhythm and a single rule, not by five more boxes.
    //
    //   4. RETROSPECTIVE (charts, recent transactions) — last, quieter
    //      headings. Useful, never urgent.
    //
    // Every element id the previous layout exposed is preserved verbatim
    // (r-k-rev, r-k-txn, r-k-mtd, r-k-low, r-k-cust, r-k-prod, r-k-rev-chg,
    // r-k-rev-breakdown, r-k-txn-sub, r-k-mtd-sub, r-dash-hourly, r-dash-pay,
    // r-dash-recent) — the binding block below and four existing regression
    // tests address them by id, and a visual rework is not a licence to
    // invalidate them.
    c.innerHTML = `
      <style>
        .rdash { max-inline-size:1400px; }
        .rdash .ret-title { color:var(--text);font-size:24px;letter-spacing:-0.02em;font-weight:700; }
        .rdash-date { color:var(--text-dim);font-size:13px;margin-block:4px 0; }

        /* ── 1. The answer ───────────────────────────────────────────────── */
        .rdash-answer { padding-block:6px 22px; }
        .rdash-answer-label { color:var(--text-dim);font-size:12px;font-weight:700;
          letter-spacing:.1em;text-transform:uppercase; }
        .rdash-answer-figure { display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-block:6px 8px; }
        /* The largest thing on the dashboard, by the same logic that makes the
           sale total the largest thing on the POS. */
        .rdash-answer-value { font-size:clamp(var(--text-size-total, 40px),4.6vw,58px);line-height:1;font-weight:800;
          letter-spacing:-0.025em;color:var(--text); }
        .rdash-answer-sub { color:var(--text-dim);font-size:14px;display:flex;gap:8px;
          align-items:baseline;flex-wrap:wrap; }
        .rdash-answer-sub b { color:var(--text);font-weight:700; }
        /* Day-over-day delta: a chip, and one that says ▲/▼ as well as
           colouring itself, so the direction survives greyscale. */
        .rdash-delta { display:inline-flex;align-items:center;gap:5px;padding-block:3px;padding-inline:10px;
          border-radius:20px;font-size:12px;font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap; }
        .rdash-delta.up   { color:var(--text-money-positive, var(--text));background:var(--state-success-surface, var(--surface-soft)); }
        .rdash-delta.down { color:var(--text-money-negative, var(--text));background:var(--state-danger-surface, var(--surface-soft)); }
        .rdash-breakdown { display:flex;gap:22px;flex-wrap:wrap;margin-block-start:12px; }
        .rdash-bd-item { display:flex;flex-direction:column;gap:2px; }
        .rdash-bd-label { color:var(--text-dim);font-size:12px; }
        .rdash-bd-value { font-size:15px;font-weight:700;color:var(--text); }
        .rdash-bd-value.is-in  { color:var(--text-money-positive, var(--text)); }
        .rdash-bd-value.is-out { color:var(--text-money-negative, var(--text)); }

        /* ── 2. Needs attention ──────────────────────────────────────────── */
        .rdash-attention { display:flex;align-items:center;justify-content:space-between;gap:16px;
          flex-wrap:wrap;padding-block:14px;padding-inline:18px;border-radius:12px;
          border:1px solid var(--state-warning-border, var(--border-mid));
          background:var(--state-warning-surface, var(--surface-soft));margin-block-end:22px; }
        .rdash-attention-main { display:flex;align-items:center;gap:12px;flex-wrap:wrap; }
        .rdash-attention-dot { inline-size:10px;block-size:10px;border-radius:50%;flex-shrink:0;
          background:var(--state-warning-text, var(--text-dim));transform:scale(1.2); }
        .rdash-attention-headline { font-size:15px;font-weight:700;color:var(--text); }
        .rdash-attention-count { font-size:22px;font-weight:800;color:var(--text);
          font-variant-numeric:tabular-nums; }
        .rdash-attention-calm { display:none;color:var(--text-dim);font-size:14px; }
        /* Calm state. The difference is carried by the WORDS first ("Nothing
           needs attention"), by the border/background dropping back to neutral
           tokens second, and by the dot shrinking third — three signals, only
           one of them colour. */
        .rdash-attention.is-calm { border-color:var(--border-soft);background:transparent;padding-inline:0; }
        .rdash-attention.is-calm .rdash-attention-dot { background:var(--border-mid);transform:scale(0.8); }
        .rdash-attention.is-calm .rdash-attention-alert,
        .rdash-attention.is-calm .rdash-attention-action { display:none; }
        .rdash-attention.is-calm .rdash-attention-calm { display:inline; }
        .rdash-attention-alert { display:flex;align-items:baseline;gap:8px;flex-wrap:wrap; }

        /* ── 3. Context strip: no cards, hierarchy by scale alone ─────────── */
        .rdash-context { display:flex;gap:40px;flex-wrap:wrap;padding-block:16px 20px;
          border-block-start:1px solid var(--border-soft);margin-block-end:24px; }
        .rdash-context-item { display:flex;flex-direction:column;gap:3px;min-inline-size:0; }
        .rdash-context-label { color:var(--text-dim);font-size:11px;font-weight:700;
          letter-spacing:.09em;text-transform:uppercase; }
        .rdash-context-value { font-size:20px;font-weight:700;color:var(--text);
          font-variant-numeric:tabular-nums; }
        .rdash-context-note { color:var(--text-dim);font-size:12px;font-variant-numeric:tabular-nums; }

        /* ── 4. Retrospective ────────────────────────────────────────────── */
        .rdash-panels { display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:18px;margin-block-end:18px; }
        @media(max-width:1000px){ .rdash-panels { grid-template-columns:minmax(0,1fr); } }
        .rdash .sub-chart-title { color:var(--text);font-size:14px;font-weight:700; }
        .rdash-panel-hdr { display:flex;justify-content:space-between;align-items:center;gap:12px;
          flex-wrap:wrap;margin-block-end:14px; }
        .rdash .ret-table { color:var(--text); }
        .rdash .ret-table th { color:var(--text-dim);border-bottom-color:var(--border-mid);
          font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase; }
        .rdash .ret-table td { border-bottom-color:var(--border-soft); }
        .rdash .ret-table tbody tr { transition:background .15s ease; }
        /* :focus-within alongside :hover, and this override is the reason the
           shared rule's invisible band went unnoticed for so long: the dashboard
           re-declared :hover with a REAL token, so on the one screen anybody
           looked at, the pointer user saw a band -- while the keyboard user, who
           matches only the shared rule's :focus-within, saw nothing here either.
           A half-fixed screen reads as a working screen. Both states, one
           declaration, so they cannot come apart again. */
        .rdash .ret-table tbody tr:hover,
        .rdash .ret-table tbody tr:focus-within { background:var(--surface-hover); }
        /* Money column: tabular + end-aligned so the amounts form a real
           column that can be scanned and summed by eye. text-align:end, not
           right, so it stays correct mirrored. */
        .rdash .ret-table .col-money { text-align:end; }
        /* .money / .money--negative / .money--accounting themselves are owned
           by css/main.css -- colour, bold weight, tabular figures and the
           accounting parentheses all live there. Nothing is restated here. */
        .rdash .money { font-variant-numeric:tabular-nums;white-space:nowrap; }
        .rdash .ret-btn-ghost { background:var(--surface-soft);color:var(--text-dim);border-color:var(--border-mid);
          min-block-size:40px;transition:background .15s ease, color .15s ease; }
        .rdash .ret-btn-ghost:hover { background:var(--surface-hover);color:var(--text); }
        .rdash :focus-visible { outline:var(--focus-ring-width, 3px) solid var(--focus-ring-color, var(--sub-accent));outline-offset:var(--focus-ring-offset, 2px); }
        @media (prefers-reduced-motion: reduce) { .rdash * { transition:none; } }
      </style>
      <div class="rdash">
      <div class="ret-hdr">
        <div>
          <h2 class="ret-title">${t('Retail Overview')}</h2>
          <p class="rdash-date" data-intl-date="true">${this._localeDate()}</p>
        </div>
        <div style="display:flex;gap:10px">
          <button class="sub-btn-primary" onclick="SubsystemApp._navigate('pos')"><span aria-hidden="true">🛒</span> <span>${t('Open the till')}</span></button>
        </div>
      </div>

      <section class="rdash-answer">
        <div class="rdash-answer-label">${t('Today so far')}</div>
        <div class="rdash-answer-figure">
          <span class="rdash-answer-value money" id="r-k-rev">—</span>
          <span id="r-k-rev-chg"></span>
        </div>
        <div class="rdash-answer-sub">
          <span><b id="r-k-txn">—</b> ${t('transactions')}</span>
          <span aria-hidden="true">·</span>
          <span>${t('avg ticket')} <b class="money" id="r-k-txn-sub">—</b></span>
        </div>
        <div class="rdash-breakdown" id="r-k-rev-breakdown"></div>
      </section>

      <section class="rdash-attention" id="r-dash-attention">
        <div class="rdash-attention-main">
          <span class="rdash-attention-dot" aria-hidden="true"></span>
          <span class="rdash-attention-headline">${t('Needs attention')}</span>
          <span class="rdash-attention-alert">
            <span class="rdash-attention-count" id="r-k-low">—</span>
            <span>${t('items need reorder')}</span>
          </span>
          <span class="rdash-attention-calm">${t('Nothing needs attention')}</span>
        </div>
        <button class="ret-btn ret-btn-ghost rdash-attention-action" onclick="SubsystemApp._navigate('products')">${t('Review stock')}</button>
      </section>

      <div class="rdash-context">
        <div class="rdash-context-item">
          <span class="rdash-context-label">${t('Month-to-Date')}</span>
          <span class="rdash-context-value money" id="r-k-mtd">—</span>
          <span class="rdash-context-note"><span id="r-k-mtd-sub">—</span> ${t('transactions')}</span>
        </div>
        <div class="rdash-context-item">
          <span class="rdash-context-label">${t('Customers')}</span>
          <span class="rdash-context-value" id="r-k-cust">—</span>
          <span class="rdash-context-note"><span id="r-k-prod">—</span> ${t('active products')}</span>
        </div>
      </div>

      <div class="rdash-panels">
        <div class="sub-chart-card">
          <div class="rdash-panel-hdr">
            <div class="sub-chart-title">${t('Revenue Today (by Hour)')}</div>
          </div>
          <div style="height:200px"><canvas id="r-dash-hourly"></canvas></div>
        </div>
        <div class="sub-chart-card">
          <div class="rdash-panel-hdr"><div class="sub-chart-title">${t('Payment Methods')}</div></div>
          <div style="height:200px"><canvas id="r-dash-pay"></canvas></div>
        </div>
      </div>
      <div class="sub-chart-card">
        <div class="rdash-panel-hdr">
          <div class="sub-chart-title">${t('Recent Transactions')}</div>
          <div style="display:flex;gap:8px">
            <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="SubsystemApp._navigate('sales')"><span aria-hidden="true">🧾</span> <span>${t('Sales History')}</span></button>
            <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="SubsystemApp._navigate('reports')">${t('Full Report')}</button>
          </div>
        </div>
        <div style="overflow-x:auto">
          <table class="ret-table" id="r-dash-recent">
            <thead><tr><th>${t('Receipt #')}</th><th>${t('Customer')}</th><th>${t('Items')}</th><th>${t('Payment')}</th><th class="col-money">${t('Total')}</th><th>${t('Date')}</th></tr></thead>
            <tbody><tr><td colspan="6" style="text-align:center;color:var(--text-dim);padding:30px">${t('Loading…')}</td></tr></tbody>
          </table>
        </div>
      </div>
      </div>`;

    try {
      const d = (await this._get('/api/sub/retail/dashboard/stats')).data || {};
      // _setMoney, not `textContent = _fmt(...)`: all three of these spans are
      // declared `.money` in the template above, so the negative marking now
      // lands on the VALUE. #r-k-rev is the one that makes this matter — it is
      // net revenue and is deliberately shown unclamped, so on a day when
      // refunds exceed sales it goes negative, and under the old assignment the
      // only cue was a hyphen the token layer could not reach.
      this._setMoney(document.getElementById('r-k-rev'), d.today_sales);
      document.getElementById('r-k-txn').textContent   = d.today_transactions || 0;
      this._setMoney(document.getElementById('r-k-mtd'), d.month_sales);
      document.getElementById('r-k-low').textContent   = d.low_stock_alerts || 0;
      document.getElementById('r-k-cust').textContent  = this._fmtNum(d.total_customers);
      // These three used to be built as interpolated sentences
      // ("312 active products", "avg $68.58 ticket", "210 transactions").
      // That put the number and the translatable words in ONE text node, which
      // i18n.js's DOM sweep can never match -- it only rescues a node whose
      // FULL trimmed text is a catalog key. So all three were permanently
      // English on an Arabic page, silently, exactly like the "🛒 Open POS"
      // button documented on _renderCashierLanding below. The words now live
      // in their own t()-rendered spans in the template above and only the
      // figure is injected here.
      document.getElementById('r-k-prod').textContent  = this._fmtNum(d.total_products || 0);
      // Avg ticket = average sale size, so use GROSS (net + returns) ÷ transactions —
      // returns shouldn't distort the average sale value.
      const grossToday = (d.today_sales || 0) + (d.today_returns || 0);
      this._setMoney(document.getElementById('r-k-txn-sub'), grossToday / (d.today_transactions || 1));
      document.getElementById('r-k-mtd-sub').textContent = this._fmtNum(d.month_transactions || 0);

      // Needs-attention band. Nothing to reorder is the GOOD case and is
      // presented as one calm line, not as a red zero — see the block comment
      // at the top of this method for why a dashboard that shouts on a good
      // morning stops being read at all.
      const attentionEl = document.getElementById('r-dash-attention');
      if (attentionEl && attentionEl.classList) {
        attentionEl.classList.toggle('is-calm', !(d.low_stock_alerts > 0));
      }

      const chg = d.sales_change_pct || 0;
      const chgEl = document.getElementById('r-k-rev-chg');
      // Same data, presented as a designed chip (.rdash-delta) instead of a
      // raw colored text run — the old ret-kpi-change-* spans still exist in
      // _injectStyles for any other consumer.
      // ▲/▼ carries the direction on its own, so the chip still reads correctly
      // with no colour at all. The percentage and the words are separate nodes
      // so 'vs yesterday' is actually translatable (as one interpolated string
      // it never could be).
      chgEl.innerHTML = chg >= 0
        ? `<span class="rdash-delta up"><span aria-hidden="true">▲</span> ${Math.abs(chg)}% <span>${t('vs yesterday')}</span></span>`
        : `<span class="rdash-delta down"><span aria-hidden="true">▼</span> ${Math.abs(chg)}% <span>${t('vs yesterday')}</span></span>`;

      // Revenue (Net) = Sales − Returns. Accounting logic is unchanged (the
      // backend already nets returns out of `today_sales`) — this is purely
      // presentational: show Sales and Returns as their own supporting
      // figures underneath the net headline so a negative net revenue reads
      // as "refunds exceeded today's sales," not as a broken dashboard. A
      // negative net figure is displayed as-is, never clamped to zero.
      const retToday   = d.today_returns || 0;
      const grossSales = (d.today_sales || 0) + retToday;
      const breakdownEl = document.getElementById('r-k-rev-breakdown');
      if (breakdownEl) {
        // Returns are passed to _money() as a NEGATIVE, so they render as
        // "(−$65.44)" — parentheses plus a real minus sign. That is what makes
        // "returns exceeded sales today" legible to a colourblind cashier and
        // on a washed-out monitor, where the old red hex was simply invisible.
        // The colour is now a token on the class, not an inline literal.
        breakdownEl.innerHTML = `
          <div class="rdash-bd-item"><span class="rdash-bd-label">${t('Sales')}</span><span class="rdash-bd-value is-in">${this._money(grossSales)}</span></div>
          <div class="rdash-bd-item"><span class="rdash-bd-label">${t('Returns')}</span><span class="rdash-bd-value${retToday > 0 ? ' is-out' : ''}">${this._money(retToday > 0 ? -retToday : 0)}</span></div>
        `;
      }

      if (window.Chart) {
        // Presentational only: Chart.js cannot consume CSS var() strings, so
        // the live token values are resolved once per render. The dashboard
        // re-renders every 60 s (auto-refresh in app-shell), so a theme
        // toggle is picked up on the next refresh/navigation.
        //
        // The axis and grid colours used to be a `isLight ? '#51607a' :
        // '#94a3b8'` ternary -- a second, private copy of the palette living
        // in JavaScript, which is precisely how a chart ends up in a colour
        // the rest of the screen abandoned two redesigns ago. They are read
        // from the same tokens the surrounding markup uses now, exactly as the
        // accent already was. `_cssToken` falls back to the previous literal
        // only if the token is genuinely absent (an older cached stylesheet),
        // so a chart can never render with no axis labels at all.
        const tickClr = this._cssToken('--text-secondary', '#51607a');
        const gridClr = this._cssToken('--border-hairline', '#e3e8ef');
        const accentRgb = this._cssToken('--sub-accent-rgb', '23,69,169');

        // Hourly chart
        const hCtx = document.getElementById('r-dash-hourly');
        if (hCtx && (d.hourly_labels||[]).length) {
          new Chart(hCtx.getContext('2d'), {
            type: 'bar',
            data: { labels: d.hourly_labels, datasets: [{ label: 'Revenue ($)', data: d.hourly_data,
              backgroundColor: `rgba(${accentRgb},0.5)`, borderColor: `rgb(${accentRgb})`, borderWidth: 1,
              borderRadius: 3 }] },
            options: { responsive:true, maintainAspectRatio:false,
              plugins:{ legend:{display:false} },
              scales:{ y:{grid:{color:gridClr},ticks:{color:tickClr,callback:v=>'$'+v}},
                       x:{grid:{display:false},ticks:{color:tickClr}} } }
          });
        } else if (hCtx) {
          hCtx.parentElement.innerHTML = `<div style="height:200px;display:flex;align-items:center;justify-content:center;color:var(--text-dim);font-size:14px">${t('No sales today yet')}</div>`;
        }

        // Payment method donut
        const pCtx = document.getElementById('r-dash-pay');
        const payMethods = d.payment_methods || {};
        const pmLabels = Object.keys(payMethods);
        const pmData   = pmLabels.map(k => payMethods[k].revenue);
        if (pCtx && pmLabels.length) {
          // Colors are keyed by method NAME, not array position -- the API
          // orders payment methods by revenue, so a positional palette
          // assigned a different color to the same method depending on which
          // one happened to earn more that period (e.g. cash green on one
          // page, blue on another). Named lookup, plus the doughnut/bar
          // decision for net-negative tenders, both live in
          // retailPaymentChartConfig() at the top of this file.
          new Chart(pCtx.getContext('2d'), retailPaymentChartConfig(pmLabels, pmData, tickClr));
        } else if (pCtx) {
          pCtx.parentElement.innerHTML = `<div style="height:200px;display:flex;align-items:center;justify-content:center;color:var(--text-dim);font-size:14px">${t('No transactions today')}</div>`;
        }
      }

      // Recent transactions table
      const tbody = document.querySelector('#r-dash-recent tbody');
      const recent = d.recent_sales || [];
      if (recent.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-dim);padding:30px">${t('No transactions yet today')}</td></tr>`;
      } else {
        tbody.innerHTML = recent.map(s => `<tr style="cursor:pointer" onclick="RetailSystem._viewSale(${s.id})" title="${this._esc(t('View invoice'))}">
          <td style="font-family:monospace;color:var(--sub-accent)">${this._saleOpenerButton(s.id, s.sale_number)}</td>
          <td>${this._esc(s.customer_name || t('Walk-in'))}</td>
          <td style="color:var(--text-dim)">${s.item_count||0} <span>${t('items')}</span></td>
          <td>${this._badge(this._esc(s.payment_method||'cash'), s.payment_method==='cash'?'green':'blue')}</td>
          <td class="col-money" style="font-weight:700">${this._money(s.total)}</td>
          <!-- AUDIT: the recent-sales query (retail_api.py) has no date filter,
               just ORDER BY created_at DESC LIMIT 8, so on a day with fewer than
               8 sales so far, rows here can be from earlier days. HH:MM-only used
               to make those indistinguishable from today's sales; show the full
               date+time here (matches the Date column convention used by the
               Sales History and Purchase History tables elsewhere in this file).

               _bdi(), and this is the exact case its docstring describes. The
               value create_sale writes is '%Y-%m-%d %H:%M:%S' (retail_api.py),
               so sliced to 16 it is "2026-08-21 18:42" -- TWO number runs with a
               space between them and NOT ONE STRONG DIRECTIONAL CHARACTER in the
               whole string. Unisolated, the bidi algorithm resolves that space
               against the paragraph, and on an Arabic page the two runs swap:
               the cell renders "18:42 2026-08-21", i.e. a date that is wrong
               rather than merely mirrored. dir="ltr" (the helper's default) is
               required as well as the isolation -- <bdi>'s own dir="auto" picks
               its direction from the first STRONG character, and there is not
               one here, so auto falls straight back to the paragraph and
               reproduces the bug inside the isolate. -->
          <td style="color:var(--text-dim);font-family:monospace">${this._bdi((s.created_at||'').slice(0,16))}</td>
        </tr>`).join('');
      }
    } catch(e) {
      console.error('Retail dashboard error:', e.message);
      // AUDIT: this used to swallow the error silently, leaving every KPI
      // tile frozen on its "—" placeholder and the recent-transactions table
      // stuck on "Loading…" forever, WHILE app-shell's _navigate() still saw
      // this render() call resolve cleanly and lit the green "● LIVE" badge
      // (app-shell.js's _updateLiveBadge(true)) -- actively telling the
      // operator the dashboard was live/current when it was dead. Rethrow so
      // _navigate's own try/catch (app-shell.js) takes over: it turns the
      // LIVE badge off and renders the existing "Failed to load" + Retry
      // panel that every other section already gets on a render failure.
      throw e;
    }
  },

  // The till, not an empty dashboard with holes where the reports tiles
  // were. A cashier's first screen after login has to be something they can
  // actually act on, and "start a sale" is that action -- so this offers one
  // primary next step (Open POS) instead of a second, cut-down dashboard
  // this file would then have to keep in sync with the real one. Same visual
  // language (.ret-hdr / .sub-chart-card) as every other screen here, so it
  // reads as a real destination rather than an error/empty state. Makes NO
  // network request at all -- there is nothing on this screen gated on a
  // capability the caller might lack, which is the whole point.
  _renderCashierLanding(c) {
    c.innerHTML = `
      <div class="ret-hdr">
        <div>
          <h2 class="ret-title">${t('Retail Overview')}</h2>
          <p class="rdash-date" data-intl-date="true" style="color:var(--text-faint);font-size:13px;margin:4px 0 0">${this._localeDate()}</p>
        </div>
      </div>
      <div class="sub-chart-card" style="text-align:center;padding-block:56px;padding-inline:32px">
        <div style="font-size:40px;margin-block-end:14px" aria-hidden="true">🛒</div>
        <h3 style="color:var(--text);margin-block:0 10px;font-size:20px;font-weight:700">${t('Ready to sell')}</h3>
        <!-- 2026-08-22: this paragraph used var(--text-muted), the legacy
             un-themed token that is defined once at :root (#9aa0a6) and never
             redefined for the light theme -- which is the default for a fresh
             install. That is ~2.64:1 grey-on-white, well under the 4.5:1 WCAG
             AA floor, on the FIRST screen a cashier sees after logging in.
             retail_dashboard_transaction_contrast_test.js documents this exact
             token trap for the Recent Transactions table; the same trap was
             still live here. --text-dim is theme-aware (#51607a light, ~6.4:1
             on a white card) and readable in both themes. -->
        <p style="color:var(--text-dim);font-size:14px;margin-block:0 24px;line-height:1.7;max-inline-size:440px;margin-inline:auto">
          ${t('Sales totals and reports are limited to managers and the store owner. Open the till to start ringing sales.')}
        </p>
        <!-- 2026-08-22: this button read "🛒 Open POS", a bare literal, and had
             done since the panel shipped. Three defects in one string, all of
             them silent:
               * it never reached t(), so no catalog could ever be consulted;
               * 'Open POS' is in neither locales/en.json nor ar.json, so even
                 i18n.js's DOM sweep (which rescues an untagged text node whose
                 FULL trimmed text is a catalog key) had nothing to look up;
               * the emoji shares the text node, so the sweep could not have
                 matched even if the key existed.
             Result: on an Arabic page a cashier's FIRST screen after login read
             three Arabic sentences under an English button. The reported
             "half-Arabic refusal panel" was declared fixed twice, because both
             the fix and the check that cleared it were aimed at
             _renderCapabilityRestricted below -- a DIFFERENT method, correct all
             along, which this panel merely resembles.
             Same key as that method's button on purpose: same button, same
             destination, one sentence to keep translated.
             2026-08-22: the emoji is now in its own aria-hidden span as well.
             t() already resolves this label, so the DOM sweep is not the load-
             bearing path here -- but splitting the node is what makes the
             sweep a working SAFETY NET rather than a decorative one, and this
             file has now shipped the shared-text-node bug twice. -->
        <button class="sub-btn-primary" style="min-block-size:48px;padding-inline:24px;font-size:15px"
          onclick="SubsystemApp._navigate('pos')"><span aria-hidden="true">🛒</span> <span>${t('Point of Sale')}</span></button>
      </div>`;
  },

  // ── POS ───────────────────────────────────────────────────────────────────
  // OPERATIONAL CALM. This is a till: a cashier looks at it for eight hours,
  // under fluorescent light, often on a cheap monitor or a touchscreen, with
  // a queue watching. Money passes through it all day. The rules encoded
  // below, in priority order, and the reason each one is a rule:
  //
  //   1. THE TOTAL IS THE LARGEST THING ON THE SCREEN. It was 26px, smaller
  //      than several headings elsewhere in the shell and barely larger than
  //      a product price. It is now clamp(38px,4.2vw,56px) -- decisively,
  //      not marginally, the biggest element in the POS. A cashier reads it
  //      to a customer without leaning in; a customer checks it from the
  //      other side of the counter. retail_surface_pos_test.js asserts this
  //      ordering against every other money element on the screen, so it
  //      cannot silently regress the way it did before.
  //
  //   2. SCANNING IS THE PRIMARY INPUT. The scan field was a 280px search box
  //      tucked in a pane header, indistinguishable from any other input, and
  //      nothing ever returned focus to it. It is now the largest control on
  //      the screen (56px tall, 19px text), carries a 4px focus ring plus a
  //      lit status lamp readable across a room, and _refocusScan() below
  //      returns focus after every interaction that had no business taking
  //      it. This is not polish: _onScannerKey() REFUSES to auto-detect a
  //      scan while focus sits in a non-search text field, so a stray tap
  //      used to kill scanning outright until the cashier noticed.
  //
  //   3. MONEY IS UNAMBIGUOUS. Every figure is tabular-nums so a column of
  //      amounts actually aligns and a 1 is as wide as a 7; every figure is
  //      nowrap so the currency mark can never break away from its digits;
  //      and a negative is marked with accounting parentheses AND a real
  //      U+2212 minus (see _money()), never by colour alone -- a red minus
  //      sign is invisible to a colourblind cashier and on a washed-out
  //      monitor, which is most monitors in most shops.
  //
  //   4. BUILT FOR FINGERS. Every control a finger lands on is >=44px in
  //      both axes, and nothing is discoverable only on :hover -- a
  //      touchscreen has no hover, so a hover-only affordance is invisible
  //      to half the installs. The remove-line button in particular used to
  //      be a 26px glyph that only turned red on hover.
  //
  //   5. DESTRUCTIVE ACTIONS ARE NOT ADJACENT TO FREQUENT ONES. "Clear" sat
  //      immediately beside "Hold" in the cart header, and the per-line "✕"
  //      sat immediately beside the "+" quantity button -- the two highest-
  //      frequency controls on the screen, each one finger-width from an
  //      action that destroys work. Mis-tapping either during a queue costs
  //      a real re-ring in front of a real customer. Void now lives in its
  //      own footer bar, and each cart line puts its remove control in a
  //      separate container behind a spacer, so no destructive control is a
  //      DOM sibling of a high-frequency one. Asserted structurally.
  //
  //   6. HIERARCHY FROM SCALE AND WEIGHT, NOT BOXES. The old screen gave
  //      every element its own panel/border, which is uniform emphasis,
  //      which is no emphasis. Borders now separate the two ZONES (catalog
  //      vs money) and nothing else; within a zone, rank is carried by size
  //      and weight.
  //
  // COLOUR comes from the token layer only -- no literal ever appears here.
  // Where a semantic token may not have landed yet, the fallback is ANOTHER
  // TOKEN (`var(--text-money-positive, var(--sub-accent))`), never a hex, so a missing
  // token degrades to legible body text rather than to an invalid declaration
  // that inherits from nothing. The tokens this screen asks for and does not
  // yet define anywhere are reported upstream, not invented here.
  //
  // RTL is by construction, not by chase: padding-inline / border-block /
  // border-inline-start / text-align:end / inset-inline throughout, so the
  // mirrored Arabic layout is correct without a single rule in rtl.css.
  _renderPOS(c) {
    this._injectStyles();
    c.innerHTML = `
      <style>
        .pos-wrap { display:grid;grid-template-columns:minmax(0,1.5fr) minmax(360px,0.95fr);gap:18px;block-size:calc(100vh - 120px); }
        @media(max-width:1100px){ .pos-wrap { grid-template-columns:minmax(0,1fr);block-size:auto; } }
        .pos-left { background:var(--surface-card);border:1px solid var(--border-soft);border-radius:14px;display:flex;flex-direction:column;overflow:hidden;min-block-size:0; }
        .pos-right { background:var(--surface);border:1px solid var(--border-mid);border-radius:14px;display:flex;flex-direction:column;overflow:hidden;min-block-size:0; }
        .pos-pane-hdr { padding-block:12px;padding-inline:16px;border-block-end:1px solid var(--border-soft);display:flex;justify-content:space-between;align-items:center;gap:12px;flex-shrink:0;flex-wrap:wrap; }
        .pos-pane-title { margin:0;color:var(--text);font-size:15px;font-weight:700;letter-spacing:-0.01em; }

        /* ── Scan bar: the primary input, sized like it ───────────────────── */
        .pos-scanbar { display:flex;align-items:center;gap:14px;padding-block:14px;padding-inline:16px;border-block-end:1px solid var(--border-soft);flex-shrink:0; }
        .pos-scan-field { position:relative;flex:1;min-inline-size:0;display:flex;align-items:center; }
        .pos-scan-icon { position:absolute;inset-inline-start:16px;font-size:20px;line-height:1;pointer-events:none; }
        .pos-search { inline-size:100%;min-block-size:56px;padding-block:0;padding-inline:52px 16px;
          font-size:19px;font-weight:600;font-family:inherit;
          background:var(--input-bg);color:var(--text);border:2px solid var(--border-mid);
          border-radius:12px;outline:none;box-sizing:border-box;
          transition:border-color .15s ease, box-shadow .15s ease, background .15s ease; }
        .pos-search::placeholder { color:var(--text-faint);font-weight:500; }
        /* Focus has to be legible from across the shop, not a 1px hairline:
           a 4px ring plus a background change on the field itself. */
        .pos-search:focus, .pos-search:focus-visible {
          border-color:var(--focus-ring-color, var(--sub-accent));
          box-shadow:0 0 0 4px rgba(var(--sub-accent-rgb),0.28);
          background:var(--surface-card); }
        /* Status lamp. Signals "this till is listening" without relying on
           colour alone -- the dot changes SIZE as well as token colour, so it
           still reads on a monochrome/washed-out panel. */
        .pos-scan-lamp { display:inline-flex;align-items:center;gap:8px;font-size:11px;font-weight:700;
          letter-spacing:.08em;text-transform:uppercase;color:var(--text-faint);white-space:nowrap; }
        .pos-scan-lamp::before { content:'';inline-size:9px;block-size:9px;border-radius:50%;
          background:var(--border-mid);transition:transform .15s ease, background .15s ease; }
        .pos-scan-field:focus-within + .pos-scan-lamp { color:var(--text-money-positive, var(--text)); }
        .pos-scan-field:focus-within + .pos-scan-lamp::before {
          background:var(--text-money-positive, currentColor);transform:scale(1.5); }

        /* ── Keyboard shortcut hints (discoverability) ───────────────────── */
        /* A senior review found this screen had no keyboard shortcuts at
           all. This strip is the only place they are documented -- quiet,
           always visible (not hover-only, not buried behind a "?"), reusing
           the same faint/dim tokens .pos-empty-hint already uses so it reads
           as a caption, not another control competing for attention. */
        .pos-kbd-hints { display:flex;flex-wrap:wrap;align-items:center;gap:5px 14px;
          padding-block:8px 2px;padding-inline:16px;color:var(--text-faint);font-size:11px; }
        .pos-kbd-item { display:inline-flex;align-items:center;gap:5px;white-space:nowrap; }
        .pos-kbd { display:inline-block;min-inline-size:20px;padding-block:1px;padding-inline:5px;
          border:1px solid var(--border-mid);border-radius:5px;background:var(--surface-soft);
          color:var(--text-dim);font-size:10px;font-weight:700;font-family:inherit;
          text-align:center;line-height:1.5; }

        /* ── Category rail ───────────────────────────────────────────────── */
        .pos-cat-bar { display:flex;gap:8px;padding-block:10px;padding-inline:16px;border-block-end:1px solid var(--border-soft);overflow-x:auto;flex-shrink:0; }
        .pos-cat-btn { min-block-size:var(--touch-target-min, 44px);padding-inline:18px;border-radius:var(--radius-pill, 22px);font-size:14px;font-weight:600;
          cursor:pointer;border:1px solid var(--border-mid);background:transparent;color:var(--text-dim);
          white-space:nowrap;font-family:inherit;transition:background .15s ease, color .15s ease, border-color .15s ease; }
        /* Paired :hover/:focus-visible, here and on every other control in
           this block. The pairing is not cosmetic and it is not covered by
           retail_design_focus_test.js -- that ratchet reads css/main.css,
           and this stylesheet is injected from JS at render time, so it was
           entirely outside its reach. retail_surface_pos_test.js::
           testEveryPosHoverAffordanceHasAFocusCounterpart closes that gap;
           it is what found these four rules hover-only. */
        .pos-cat-btn:hover, .pos-cat-btn:focus-visible { color:var(--text);background:var(--surface-hover); }
        /* Selected is a solid fill; hovered is a tint. They must never look
           the same -- a cashier scanning the rail has to see what is ON. */
        .pos-cat-btn.active { background:var(--sub-accent);border-color:var(--sub-accent);color:var(--text-on-accent, var(--text-inverse));font-weight:700; }

        /* ── Product grid ────────────────────────────────────────────────── */
        .pos-product-grid { display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;
          padding-block:16px;padding-inline:16px;overflow-y:auto;flex:1;align-content:start; }
        /* The tile is a <button> (see _renderPOSGrid). The first line is the
           button reset that makes it look exactly like the <div> it replaced:
           a UA-styled button would otherwise arrive with its own font, its own
           centred colour and a shrink-to-fit width, and the grid would reflow. */
        .pos-card { appearance:none;-webkit-appearance:none;font:inherit;color:inherit;inline-size:100%;
          min-block-size:118px;background:var(--surface-soft);border:1px solid var(--border-soft);
          border-radius:12px;padding-block:14px;padding-inline:12px;text-align:center;cursor:pointer;user-select:none;
          display:flex;flex-direction:column;justify-content:center;gap:4px;
          transition:background .14s ease, border-color .14s ease, transform .12s ease; }
        /* :focus-visible alongside :hover, and this is the pairing that could
           never have existed before: as a <div> the tile was unfocusable, so a
           :focus-visible rule on it would have been dead CSS. Now it is the
           keyboard and scanner operator's only sight of where they are, and it
           is the ONLY state feedback a touchscreen operator gets at all. */
        .pos-card:hover, .pos-card:focus-visible { border-color:var(--border-mid);background:var(--surface-hover); }
        .pos-card:active { transform:scale(0.98); }
        /* AUDIT -- this rule was "opacity:.45", and opacity is a GROUP
           operation: it renders the tile, its text, its border AND its focus
           outline into one buffer and composites the whole buffer at 45%. It
           does not tint the text, it drags foreground and background toward
           each other at the same time, so the ratio between them collapses far
           faster than 45% suggests. Measured: the product name 2.78:1, the
           words "Out of stock" 2.37:1 -- WORSE than the 2.64:1 grey-on-white
           defect an earlier round fixed, on this very screen -- and the focus
           ring 2.20-2.29:1 against a 3:1 floor, where the same ring is 8.53:1
           one tile to the left.

           That is the whole defect in one line: the tile is deliberately
           aria-disabled rather than disabled so it STAYS focusable and can
           explain itself, and then a group wash withheld the explanation from
           the low-vision and keyboard operators while still announcing it to a
           screen reader. The reasoning was right; the paint contradicted it.

           So "unavailable" is now carried by four cues, none of them a wash:
             * a flattened, recessed surface (the same language main.css's
               :disabled rule uses -- not-allowed plus a dead surface),
             * the words "Out of stock", already in the tile, in the danger
               token at its full AAA weight, because the explanation should be
               the MOST legible thing here and not the least,
             * a struck-through price, which is a non-colour cue and survives a
               monochrome monitor and a colourblind cashier,
             * a de-emphasised (not erased) product name -- --text-tertiary is
               the faintest step the palette has that still clears AA on this
               surface, and there is deliberately no fainter one.
           The focus ring is left entirely alone, so it is now the same ring at
           the same contrast as on any sellable tile.

           THE PRICE KEEPS ITS MONEY COLOUR, and that is not an oversight. An
           amount carries the palette's stricter AAA floor wherever it appears,
           and de-emphasising it here would have meant writing the money palette
           down in a third place. It would also have been DEAD: _money() returns
           a nested span.money that re-declares colour, so a rule aimed at this
           container matches an element holding no characters of its own -- the
           same shape as the .rdash-bd-value.is-in bug. The strikethrough is the
           right tool because text-decoration propagates INTO that nested span
           and cannot be cancelled by it. */
        .pos-card-outofstock { cursor:not-allowed;background:var(--surface-active);border-color:var(--border-default); }
        .pos-card-outofstock .pos-card-name { color:var(--text-tertiary); }
        .pos-card-outofstock .pos-card-price { text-decoration:line-through; }
        /* The unavailable tile stays FOCUSABLE (aria-disabled, not disabled), so
           it still takes the outline from .pos-wrap :focus-visible below -- what
           is suppressed here is only the "this will do something" surface lift,
           which would be a lie on a tile that cannot be sold. */
        .pos-card-outofstock:hover, .pos-card-outofstock:focus-visible, .pos-card-outofstock:active {
          transform:none;border-color:var(--border-default);background:var(--surface-active); }
        .pos-card-icon { font-size:26px;line-height:1; }
        .pos-card-name { color:var(--text);font-size:13px;font-weight:600;line-height:1.3;
          display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden; }
        .pos-card-price { color:var(--text);font-weight:700;font-size:15px; }
        .pos-card-stock { font-size:11px;color:var(--text-dim); }
        .pos-card-stock.is-low { color:var(--state-warning-text, var(--text-dim));font-weight:600; }
        .pos-card-stock.is-out { color:var(--state-danger-text, var(--text-dim));font-weight:700; }
        /* Phase 7 stage 7b: a stale figure gets no urgency colour at all --
           an amber "low stock" or red "out of stock" IS a confident claim,
           the exact thing dating the number exists to stop making. Italic +
           the palette's faintest text step reads as "this is a fact about
           the past", not a fact about right now. See _isStockStale() /
           _staleStockLabel() below. */
        .pos-card-stock.is-stale { color:var(--text-tertiary, var(--text-dim));font-style:italic;font-weight:400; }

        /* ── Cart lines ──────────────────────────────────────────────────── */
        .pos-cart-items { flex:1;overflow-y:auto;padding-block:4px;padding-inline:14px;min-block-size:0; }
        .pos-cart-row { display:flex;align-items:center;gap:10px;padding-block:10px;padding-inline:2px;
          border-block-end:1px solid var(--border-soft); }
        .pos-cart-row:last-child { border-block-end:none; }
        /* Only the row just touched animates (see _renderCart) -- re-animating
           the whole list on every qty change reads as flicker at till speed. */
        .pos-row-new { animation:posRowIn .24s cubic-bezier(.22,1,.36,1) both; }
        @keyframes posRowIn { from{opacity:0;transform:translateY(-5px)} to{opacity:1;transform:none} }
        .pos-line-main { flex:1;min-inline-size:0; }
        .pos-item-name { color:var(--text);font-size:14px;font-weight:600;line-height:1.35;overflow:hidden;text-overflow:ellipsis; }
        .pos-item-meta { color:var(--text-dim);font-size:12px;margin-block-start:2px; }
        /* Promotions wave 1 (ROADMAP.md "retail schema v23"). Only rendered
           when _bestPromoFor resolves a promotion for the line (see
           _renderCart) -- with none, .pos-item-meta is untouched from before
           this wave. The struck-through original price carries the same
           muted step .pos-card-outofstock already uses for a de-emphasised
           figure; the promo tag uses the success-state token because it is,
           for the customer, unambiguously good news, matching how a "you
           saved" figure reads anywhere else money is shown positive. */
        .pos-price-orig { text-decoration:line-through;color:var(--text-tertiary, var(--text-dim));margin-inline-end:4px; }
        .pos-promo-tag { display:flex;align-items:center;gap:4px;color:var(--state-success-text);font-size:11px;font-weight:600;margin-block-start:2px; }
        /* High-frequency zone: quantity stepper + the line's money. */
        .pos-line-freq { display:flex;align-items:center;gap:12px;flex-shrink:0; }
        .pos-qty-wrap { display:flex;align-items:center;background:var(--input-bg);border:1px solid var(--border-mid);border-radius:10px;overflow:hidden; }
        .pos-qty-btn { min-inline-size:var(--touch-target-min, 44px);min-block-size:var(--touch-target-min, 44px);background:transparent;border:none;color:var(--text);
          cursor:pointer;font-size:18px;font-weight:700;line-height:1;font-family:inherit;transition:background .12s ease; }
        /* The one control on this screen that was still hover-only. The pairing
           rule stated on .pos-cat-btn above applies here for the same reason,
           and testEveryPosHoverAffordanceHasAFocusCounterpart did not catch it
           because that check derives its control set from the shell and the
           product GRID, and the quantity stepper lives in the CART -- a third
           root it never reads. The stepper is the most-touched control on the
           till after the tiles, and on a touchscreen :hover never fires at all,
           so without this the only state feedback it had was for the one input
           method a till is least often driven with. */
        .pos-qty-btn:hover,
        .pos-qty-btn:focus-visible { background:var(--surface-hover); }
        .pos-qty-val { min-inline-size:34px;text-align:center;color:var(--text);font-size:15px;font-weight:700; }
        .pos-line-total { min-inline-size:84px;text-align:end;color:var(--text);font-size:15px;font-weight:700; }
        /* Physical separation between the frequent controls above and the
           destructive one below. This spacer is the reason the remove button
           is not a DOM sibling of the "+" button -- see rule 5 in the block
           comment above, and retail_surface_pos_test.js which asserts it. */
        .pos-line-sep { flex:0 0 16px;inline-size:16px;align-self:stretch; }
        .pos-line-danger { flex-shrink:0;border-inline-start:1px solid var(--border-soft);padding-inline-start:10px; }
        /* Visible at rest, not only on hover: a touchscreen never hovers. */
        .pos-remove-btn { min-inline-size:var(--touch-target-min, 44px);min-block-size:var(--touch-target-min, 44px);background:transparent;
          border:1px solid var(--border-soft);border-radius:10px;color:var(--text-dim);
          cursor:pointer;font-size:15px;font-family:inherit;transition:background .12s ease, color .12s ease, border-color .12s ease; }
        .pos-remove-btn:hover, .pos-remove-btn:focus-visible {
          color:var(--state-danger-text, var(--text));border-color:var(--state-danger-border, var(--border-mid));
          background:var(--state-danger-surface, var(--surface-hover)); }
        .pos-empty { display:flex;flex-direction:column;align-items:center;gap:8px;text-align:center;
          color:var(--text-dim);padding-block-start:56px;font-size:14px; }
        /* AUDIT -- this carried opacity:.6, which washes the glyph AND its
           backdrop together and left the empty-cart icon at 3.17:1. It is a
           decorative 🛒 next to text that already says the cart is empty, so
           the honest fix is to mark it decorative and stop painting it faintly:
           aria-hidden keeps it out of the accessible name (see _renderCart) and
           --text-tertiary is the palette's designed de-emphasis step, which
           unlike an opacity wash is still AA on every surface it can land on. */
        .pos-empty-icon { font-size:32px;color:var(--text-tertiary); }
        .pos-empty-hint { color:var(--text-faint);font-size:13px; }

        /* Void lives here -- its own bar, below the list, deliberately far
           from Hold (pane header) and from Charge (foot of the summary). */
        .pos-cart-tools { display:flex;justify-content:flex-end;padding-block:8px 10px;padding-inline:14px;
          border-block-start:1px solid var(--border-soft);flex-shrink:0; }
        .pos-clear-btn { min-block-size:var(--touch-target-min, 44px);padding-inline:16px;border-radius:10px;border:1px solid var(--border-soft);
          background:transparent;color:var(--text-dim);font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;
          transition:background .12s ease, color .12s ease, border-color .12s ease; }
        .pos-clear-btn:hover, .pos-clear-btn:focus-visible {
          color:var(--state-danger-text, var(--text));border-color:var(--state-danger-border, var(--border-mid));
          background:var(--state-danger-surface, var(--surface-hover)); }
        .pos-hold-btn { min-block-size:var(--touch-target-min, 44px);padding-inline:16px;border-radius:10px;border:1px solid var(--border-mid);
          background:var(--surface-soft);color:var(--text-dim);font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;
          transition:background .12s ease, color .12s ease; }
        .pos-hold-btn:hover, .pos-hold-btn:focus-visible { color:var(--text);background:var(--surface-hover); }
        .pos-cust-select { min-block-size:var(--touch-target-min, 44px);background:var(--input-bg);border:1px solid var(--border-mid);
          border-radius:10px;color:var(--text);padding-inline:12px;font-size:13px;font-family:inherit;outline:none;
          max-inline-size:190px;transition:border-color .15s ease; }
        .pos-cust-select:focus { border-color:var(--focus-ring-color, var(--sub-accent)); }

        /* ── Summary + the total ─────────────────────────────────────────── */
        .pos-summary { padding-block:14px 16px;padding-inline:16px;background:var(--surface-card);
          border-block-start:1px solid var(--border-mid);flex-shrink:0; }
        .pos-sum-row { display:flex;justify-content:space-between;align-items:center;gap:12px;
          margin-block-end:8px;color:var(--text-dim);font-size:14px; }
        .pos-sum-val { color:var(--text);font-weight:600; }
        .pos-mini-input { min-block-size:var(--touch-target-min, 44px);background:var(--input-bg);border:1px solid var(--border-mid);
          border-radius:10px;color:var(--text);text-align:end;outline:none;font-family:inherit;
          font-variant-numeric:tabular-nums;transition:border-color .15s ease, box-shadow .15s ease; }
        .pos-mini-input:focus { border-color:var(--focus-ring-color, var(--sub-accent));box-shadow:0 0 0 3px rgba(var(--sub-accent-rgb),0.18); }
        /* THE number. Largest element on the screen by a wide margin, and the
           only place on the POS that gets this weight of type. */
        .pos-total-band { display:flex;align-items:baseline;justify-content:space-between;gap:14px;
          padding-block:14px 16px;margin-block:10px;border-block:1px solid var(--border-mid); }
        .pos-grand-label { font-size:13px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--text-dim); }
        .pos-grand-value { font-size:clamp(var(--text-size-total, 40px),4.2vw,56px);line-height:1;font-weight:800;
          letter-spacing:-0.022em;color:var(--text); }
        .pos-change-row { color:var(--text-money-positive, var(--text));font-weight:700; }
        .pos-change-row .pos-sum-val { color:inherit; }

        /* ── Tender + charge ─────────────────────────────────────────────── */
        .pos-pay-btns { display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-block-end:12px; }
        .pos-pay-btn { min-block-size:var(--touch-target-comfortable, 48px);padding-block:6px;padding-inline:4px;border-radius:10px;font-size:13px;font-weight:600;
          cursor:pointer;border:1px solid var(--border-mid);background:var(--surface-soft);color:var(--text-dim);
          display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;font-family:inherit;
          transition:background .15s ease, color .15s ease, border-color .15s ease; }
        .pos-pay-btn:hover, .pos-pay-btn:focus-visible { color:var(--text);background:var(--surface-hover); }
        .pos-pay-btn.active { background:var(--sub-accent);border-color:var(--sub-accent);
          color:var(--text-on-accent, var(--text-inverse));font-weight:700; }
        .pos-pay-icon { font-size:15px;line-height:1; }
        /* Charge is money coming IN, so it carries the money-in token rather
           than a decorative gradient -- colour that means something. */
        .pos-checkout-btn { inline-size:100%;min-block-size:var(--touch-target-comfortable, 52px);padding-block:14px;padding-inline:18px;
          background:var(--text-money-positive, var(--sub-accent));border:1px solid transparent;border-radius:12px;
          color:var(--text-on-accent, var(--text-inverse));font-weight:800;font-size:18px;cursor:pointer;
          font-family:inherit;font-variant-numeric:tabular-nums;white-space:nowrap;
          transition:opacity .15s ease, transform .12s ease; }
        .pos-checkout-btn:hover, .pos-checkout-btn:focus-visible { opacity:.92; }
        .pos-checkout-btn:active { transform:scale(0.99); }
        .pos-checkout-btn:disabled { opacity:.45;cursor:not-allowed;transform:none; }

        /* ── Money typography (see rule 3) ───────────────────────────────── */
        /* The .money family (colour, bold weight, accounting parentheses,
           slashed zero) is owned by css/main.css and is not restated here.
           This rule only extends tabular figures + nowrap to the few POS
           elements that are numeric but are NOT tagged .money -- the quantity
           readout and the card price -- so the whole screen's digits share one
           metric and a column of them lines up. */
        .pos-wrap .money, .pos-grand-value, .pos-line-total, .pos-qty-val, .pos-card-price {
          font-variant-numeric:tabular-nums;white-space:nowrap; }

        /* A till is driven by keyboard and barcode scanner as much as by
           finger. An invisible focus ring makes that unusable, so this is
           deliberately heavier than a browser default. */
        .pos-wrap :focus-visible { outline:var(--focus-ring-width, 3px) solid var(--focus-ring-color, var(--sub-accent));outline-offset:var(--focus-ring-offset, 2px); }
        @media (prefers-reduced-motion: reduce) {
          .pos-row-new { animation:none; }
          .pos-wrap * { transition:none; }
        }
      </style>
      <div class="pos-wrap">
        <div class="pos-left">
          <!-- mousedown, not click: the browser moves focus to the pressed
               element BEFORE any click handler runs, so preventing the
               default here is the only thing that stops a tap on a product
               tile from blurring the scan field in the first place.
               Refocusing afterwards would still blur-then-refocus, which on
               a touchscreen closes the on-screen keyboard and flickers the
               caret. See _keepScanFocus(). -->
          <div class="pos-scanbar" onmousedown="RetailSystem._keepScanFocus(event)">
            <div class="pos-scan-field">
              <span class="pos-scan-icon" aria-hidden="true">⌗</span>
              <input class="pos-search" id="pos-search" type="text" autocomplete="off"
                     inputmode="search" aria-label="${this._esc(t('Scan a barcode or search'))}"
                     placeholder="${this._esc(t('Scan a barcode or search'))}"
                     oninput="RetailSystem._filterPOS()" />
            </div>
            <span class="pos-scan-lamp">${t('Ready to scan')}</span>
          </div>
          <div class="pos-kbd-hints" title="${this._esc(t('Keyboard shortcuts'))}" aria-label="${this._esc(t('Keyboard shortcuts'))}">
            <span class="pos-kbd-item"><kbd class="pos-kbd">${t('Enter')}</kbd> ${t('Charge')}</span>
            <span class="pos-kbd-item"><kbd class="pos-kbd">${t('N*')}</kbd> ${t('Quantity')}</span>
            <span class="pos-kbd-item"><kbd class="pos-kbd">X</kbd> ${t('Exact cash')}</span>
            <span class="pos-kbd-item"><kbd class="pos-kbd">/</kbd> ${t('Search')}</span>
            <span class="pos-kbd-item"><kbd class="pos-kbd">${t('Del')}</kbd> ${t('Remove line')}</span>
            <span class="pos-kbd-item"><kbd class="pos-kbd">${t('Esc')}</kbd> ${t('Cancel')}</span>
          </div>
          <div class="pos-pane-hdr" onmousedown="RetailSystem._keepScanFocus(event)">
            <h3 class="pos-pane-title">${t('Products')}</h3>
            <button class="ret-btn ret-btn-ghost ret-btn-sm" id="pos-held-btn" onclick="RetailSystem._openHeldSalesModal()"
              title="${this._esc(t("Browse and resume sales you've held"))}"><span aria-hidden="true">📋</span> <span>${t('Held')}</span> (<span id="pos-held-count">0</span>)</button>
          </div>
          <div class="pos-cat-bar" id="pos-cats" onmousedown="RetailSystem._keepScanFocus(event)">
            <button class="pos-cat-btn active" onclick="RetailSystem._setCat(null,this)">${t('All')}</button>
          </div>
          <div class="pos-product-grid" id="pos-product-grid" onmousedown="RetailSystem._keepScanFocus(event)">
            <div style="grid-column:1/-1;text-align:center;padding:50px;color:var(--text-dim)">${t('Loading…')}</div>
          </div>
        </div>
        <div class="pos-right">
          <div class="pos-pane-hdr">
            <h3 class="pos-pane-title">${t('Current Sale')}</h3>
            <div style="display:flex;gap:8px;align-items:center">
              <select id="pos-customer" class="pos-cust-select">
                <option value="">${this._esc(t('Walk-in'))}</option>
              </select>
              <button class="pos-hold-btn" onclick="RetailSystem._holdSale()"
                title="${this._esc(t('Park this sale and start a new one'))}"><span aria-hidden="true">⏸</span> <span>${t('Hold')}</span></button>
            </div>
          </div>
          <div class="pos-cart-items" id="pos-cart" onmousedown="RetailSystem._keepScanFocus(event)">
            <div class="pos-empty"><span class="pos-empty-icon" aria-hidden="true">🛒</span><span>${t('Cart is empty')}</span><span class="pos-empty-hint">${t('Scan or tap a product to begin')}</span></div>
          </div>
          <!-- Void: a destructive control, given its own bar so it is neither
               a sibling nor a neighbour of Hold (above) or Charge (below). -->
          <div class="pos-cart-tools">
            <button class="pos-clear-btn" onclick="RetailSystem._clearCart()"
              title="${this._esc(t('Void the whole sale'))}">${t('Void Sale')}</button>
          </div>
          <div class="pos-summary">
            <div class="pos-sum-row"><span>${t('Subtotal')}</span><span class="pos-sum-val money" id="pos-sub">$0.00</span></div>
            <div class="pos-sum-row">
              <span>${t('Discount')}</span>
              <span style="display:flex;gap:6px;align-items:center">
                <input type="number" id="pos-disc" value="0" min="0" max="100" step="0.5"
                  class="pos-mini-input" style="inline-size:76px;padding-inline:10px;font-size:14px"
                  aria-label="${this._esc(t('Discount'))}"
                  oninput="RetailSystem._recalc()" /> %
              </span>
            </div>
            <div class="pos-sum-row"><span>${t('Tax')}</span><span class="pos-sum-val money" id="pos-tax">$0.00</span></div>
            <div class="pos-total-band">
              <span class="pos-grand-label">${t('Total')}</span>
              <span class="pos-grand-value money" id="pos-total">$0.00</span>
            </div>
            <div class="pos-sum-row">
              <span>${t('Cash Tendered')}</span>
              <input type="number" id="pos-tendered" placeholder="0.00" min="0" step="0.01"
                class="pos-mini-input" style="inline-size:112px;padding-inline:12px;font-size:15px"
                aria-label="${this._esc(t('Cash Tendered'))}"
                oninput="RetailSystem._calcChange()" />
            </div>
            <div class="pos-sum-row pos-change-row" id="pos-change-row" style="display:none">
              <span>${t('Change Due')}</span><span class="pos-sum-val money" id="pos-change">$0.00</span>
            </div>
            <div class="pos-pay-btns" id="pos-pay-btns">
              <button class="pos-pay-btn active" data-method="cash"     onclick="RetailSystem._setPayment('cash',this)"><span class="pos-pay-icon" aria-hidden="true">💵</span><span>${t('Cash')}</span></button>
              <button class="pos-pay-btn"         data-method="card"     onclick="RetailSystem._setPayment('card',this)"><span class="pos-pay-icon" aria-hidden="true">💳</span><span>${t('Card')}</span></button>
              <button class="pos-pay-btn"         data-method="mobile"   onclick="RetailSystem._setPayment('mobile',this)"><span class="pos-pay-icon" aria-hidden="true">📱</span><span>${t('Mobile')}</span></button>
              <button class="pos-pay-btn"         data-method="transfer" onclick="RetailSystem._setPayment('transfer',this)"><span class="pos-pay-icon" aria-hidden="true">🏦</span><span>${t('Transfer')}</span></button>
              <button class="pos-pay-btn"         data-method="credit"   onclick="RetailSystem._setPayment('credit',this)"><span class="pos-pay-icon" aria-hidden="true">📋</span><span>${t('Credit')}</span></button>
              <button class="pos-pay-btn"         data-method="voucher"  onclick="RetailSystem._setPayment('voucher',this)"><span class="pos-pay-icon" aria-hidden="true">🎟</span><span>${t('Voucher')}</span></button>
            </div>
            <button class="pos-checkout-btn" id="pos-checkout-btn" onclick="RetailSystem._checkout()">${this._esc(this._checkoutLabel(0))}</button>
          </div>
        </div>
      </div>`;

    this._cart = [];
    this._paymentMethod = 'cash';
    this._activeCat = null;
    // Promotions wave 1 (ROADMAP.md "retail schema v23"). Reset synchronously
    // at mount, same as _cart above -- _loadPOSData()'s fetch is async and a
    // render that happens before it resolves (or a test that drives _addToCart
    // straight after this call, before awaiting) must see an empty array, not
    // undefined. _recalc/_renderCart/_bestPromoFor all read this defensively
    // too (`this._promotions || []`), because several existing test harnesses
    // exercise _addToCart/_recalc directly without ever calling _renderPOS at
    // all -- this reset is a courtesy for the ones that do, not the only guard.
    this._promotions = [];
    // A multiplier armed on a screen the cashier has since navigated away
    // from is a stale trap, not a convenience -- clear it on every (re)mount.
    this._pendingQty = null;
    this._qtyKeyBuffer = '';
    this._qtyKeyLastAt = 0;
    this._loadPOSData();
    // The barcode scanner engine is initialised globally in render(); nothing to do here.
    // Put the caret where the next barcode is going to land, so the very first
    // scan of a shift works without the cashier clicking anything first.
    this._refocusScan(true);

    // feat/shift-cash-drawer: mounts a status bar just below the POS header
    // showing whether a cash session is open for this branch (soft warning
    // if not -- see cash-drawer.js's own module docstring for why this is
    // never a hard checkout gate). Guarded so a build that hasn't loaded
    // cash-drawer.js (e.g. an older cached index.html) still renders the
    // rest of the POS screen exactly as before.
    if (window.CashDrawer) CashDrawer.mount(c);
  },

  // ── launch-readiness "the POS scale fix" (ROADMAP.md 2026-08-29 v21) ──────
  // This screen used to fetch the ENTIRE product catalogue on every mount,
  // and again after every single sale (see _checkout below) -- at 50,000
  // SKUs that is ~25MB re-sent over the wire per sale just to reflect a
  // stock decrement the server already applied, and a 50,000-button
  // innerHTML rebuild on every keystroke in the search box.
  //
  // POS_GRID_PAGE_SIZE bounds the first page and every search page --
  // matches PRODUCTS_LIST_DEFAULT_LIMIT in retail_api.py, so an unfiltered
  // page 1 here is exactly the server's own default page. Every fetch below
  // asks for POS_GRID_PAGE_SIZE+1 rows, never exactly the cap -- the extra
  // row is how the client tells "the server genuinely has no more" (<= cap
  // rows came back) apart from "there are more than we asked for" (cap+1
  // came back), with no truncation flag needed from the backend. See
  // _renderPOSGrid, which is where the cap and the truncation notice are
  // actually decided.
  POS_GRID_PAGE_SIZE: 200,
  // Long enough that a hand-typed/mis-detected 4-6 character barcode lands
  // as ONE request; short enough a cashier deliberately searching never
  // feels the lag.
  POS_SEARCH_DEBOUNCE_MS: 220,

  // Additive by-id lookup cache. this._products only ever holds ONE page --
  // page 1 or the latest search's results, capped at POS_GRID_PAGE_SIZE (see
  // _renderPOSGrid) -- so it is the wrong place to look up a product that
  // arrived a DIFFERENT way: a scan hit (_findByCode) or a search result for
  // a product outside page 1. Every product this device has seen from the
  // server this mount lands here BY ID as well, additively; _addToCart and
  // the post-checkout stock decrement below both fall back to this after
  // checking this._products.
  _cacheProduct(p) {
    if (!p || p.id == null) return;
    if (!this._productsById) this._productsById = Object.create(null);
    this._productsById[p.id] = p;
  },

  async _loadPOSData() {
    try {
      const [prods, cats, custs, taxSettings, held, promos] = await Promise.all([
        this._get(`/api/sub/retail/products?limit=${this.POS_GRID_PAGE_SIZE + 1}`),
        this._get('/api/sub/retail/categories'),
        this._get('/api/sub/retail/customers'),
        this._get('/api/sub/retail/settings/tax').catch(() => null),
        this._get('/api/sub/retail/held-sales').catch(() => null),
        // Promotions wave 1 (ROADMAP.md "retail schema v23"). `.catch(() =>
        // null)`, same as taxSettings/held immediately above -- a promotions
        // fetch failing (offline, a 500, an install with no branch context)
        // must never block the OTHER three from resolving and must never stop
        // the till from selling. No branch_id is sent: the POS has no
        // "current branch" concept anywhere client-side today (nothing else
        // in this file tracks one), so this asks for whatever the server
        // resolves with no branch filter rather than inventing new client
        // state to guess one.
        this._get('/api/sub/retail/promotions/active').catch(() => null),
      ]);
      this._products   = prods.data || [];
      // Defensive Array.isArray, not just `promos.data || []`: a non-2xx/
      // malformed response can still resolve with a `data` that is not an
      // array (several test fixtures in this suite answer an unmocked GET
      // with an unrelated stats OBJECT), and iterating that would throw
      // inside _bestPromoFor on every cart render rather than just showing
      // no promotions.
      this._promotions = (promos && Array.isArray(promos.data)) ? promos.data : [];
      // Snapshot of "page 1", restored instantly (no round trip) whenever the
      // search box is cleared -- see _filterPOS.
      this._posInitialPage = this._products;
      this._products.forEach(p => this._cacheProduct(p));
      this._categories = cats.data  || [];
      this._customers  = custs.data || [];
      // Company's configured tax-calculation policy (core/retail/pricing.py
      // is the authoritative spec for what these two modes compute).
      this._taxMode = (taxSettings && taxSettings.data && taxSettings.data.tax_calculation_mode) || 'after_discount';
      // Currency rides on the same response (see _currencySymbol's own note).
      // Guarded rather than assigned blindly: a response that predates this
      // field -- an older backend behind a newer till during a staged rollout
      // -- must leave the Jordanian defaults standing rather than blanking the
      // mark and silently rendering bare numbers.
      const _ts = (taxSettings && taxSettings.data) || {};
      if (_ts.currency_symbol != null) this._currencySymbol = _ts.currency_symbol;
      if (_ts.currency_decimals != null) this._currencyDecimals = _ts.currency_decimals;
      const heldCountEl = document.getElementById('pos-held-count');
      if (heldCountEl) heldCountEl.textContent = (held && held.data || []).length;

      // Populate category bar
      const catBar = document.getElementById('pos-cats');
      if (catBar) {
        this._categories.forEach(cat => {
          const btn = document.createElement('button');
          btn.className = 'pos-cat-btn';
          btn.textContent = cat.name;
          btn.onclick = () => this._setCat(cat.id, btn);
          catBar.appendChild(btn);
        });
      }
      // Populate customer dropdown
      const sel = document.getElementById('pos-customer');
      if (sel && this._customers.length) {
        this._customers.forEach(cu => {
          const opt = document.createElement('option');
          opt.value = cu.id;
          opt.textContent = `${cu.name}${cu.phone ? ' ('+cu.phone+')' : ''}`;
          sel.appendChild(opt);
        });
      }
      this._renderPOSGrid();
    } catch(e) {
      const grid = document.getElementById('pos-product-grid');
      if (grid) grid.innerHTML = `<div class="pos-card-stock is-out" style="grid-column:1/-1;padding:20px;font-size:14px">${t('Failed to load products.')}</div>`;
    }
  },

  _setCat(catId, btn) {
    this._activeCat = catId;
    document.querySelectorAll('.pos-cat-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    this._renderPOSGrid();
    this._refocusScan();
  },

  // Stored-XSS fix: p.name (and item.name / i.name at the cart, sale-complete
  // modal, and printed-receipt call sites below) is rendered here even
  // though it was set by _esc()'s own original rationale for "locally-
  // created records" -- that reasoning doesn't hold for product names.
  // Unlike this file's own hand-typed strings, a product name can arrive
  // from the CSV Import Wizard (no sanitization there) or from any logged-in
  // user, and CLAUDE.md is explicit that this app has "no real RBAC -- only
  // a bare role string" -- so a low-trust user can plant a name like
  // `"><img src=x onerror=...>` that then executes in a manager's or
  // cashier's browser (with cookies, via credentials:'include' fetches) the
  // moment the product is shown on the POS grid, added to the cart, shown on
  // the sale-complete receipt modal, or printed. _renderProductTable already
  // escapes this exact field (this._esc(p.name)) on the Products table --
  // this makes the POS-side rendering consistent with that existing
  // convention instead of trusting the same field raw.
  // ── Phase 7 stage 7b: telling the truth about being offline ────────────────
  // docs/launch-readiness/phase7-offline-ux.md, "Correction to Decision 1" +
  // "Two silence rules 7b must honour". `_syncHealth` is stashed here by
  // app-shell.js's EXISTING _pollSyncHealth() (which already polls
  // GET /api/sub/retail/sync/health every SYNC_POLL_MS to drive the offline
  // banner) -- this tile does not run a second poller, it just reads the
  // same snapshot app-shell.js already fetched.
  //
  // The 30-minute threshold lives HERE, not duplicated in app-shell.js: the
  // banner's own "behind" state (SubsystemApp._renderSyncBehindState) reads
  // RetailSystem.SYNC_STALE_THRESHOLD_SECONDS rather than declaring its own
  // copy, so the banner and the tile can never disagree about what "stale"
  // means.
  SYNC_STALE_THRESHOLD_SECONDS: 30 * 60,

  // Phase 7 stage 7c-i (docs/launch-readiness/phase7-offline-ux.md, "PART 2
  // -- the 24-hour soft warning"): a SECOND, longer threshold on the SAME
  // seconds_since_last_success figure above -- an escalation of the
  // ordinary "behind" banner into a visibly stronger one once a device has
  // been out of contact for more than a day. Lives right next to the
  // 30-minute threshold for the identical reason that one does: app-
  // shell.js reads RetailSystem.SYNC_STALE_WARNING_THRESHOLD_SECONDS
  // rather than declaring its own copy, so the two files can never
  // disagree about what "behind by a day" means. Purely informational --
  // it informs, it does not block (Decision 2: "The 24-hour soft warning
  // needs no capability"), unlike the SEPARATE 30-minute-threshold PO-
  // receive guard added server-side this same stage.
  SYNC_STALE_WARNING_THRESHOLD_SECONDS: 24 * 60 * 60,

  // Populated by app-shell.js's _pollSyncHealth(); null until the first poll
  // resolves (or forever, on an install with no `document`/fetch wiring --
  // e.g. these standalone node tests -- which is exactly the safe default:
  // no health data means "treat as not stale", never the reverse).
  _syncHealth: null,

  // Both silence rules from the doc apply here, not just to the banner:
  //   - unconfigured ({configured:false}, or no health fetched yet at all)
  //     -> never stale. A single-device install's local balance is not a
  //     stale copy of anything; it is the only ledger there is, exactly as
  //     authoritative 30 minutes after a sync as during one.
  //   - never_synced -> never stale. A configured install that has not
  //     completed its first sync has no basis for claiming the figure has
  //     drifted -- there is nothing to have drifted FROM yet.
  _isStockStale() {
    const health = this._syncHealth;
    if (!health || health.configured !== true) return false;
    if (health.never_synced) return false;
    const secs = health.seconds_since_last_success;
    return typeof secs === 'number' && secs > this.SYNC_STALE_THRESHOLD_SECONDS;
  },

  // Mirrors app-shell.js's _mostRecentSyncIso -- duplicated rather than
  // shared (this file has no module system; see _esc() just above for the
  // same established per-file-duplication convention). Whichever half
  // synced more recently is the true "last known" instant.
  _mostRecentSyncSuccess(health) {
    const a = health && health.push && health.push.last_success_at;
    const b = health && health.pull && health.pull.last_success_at;
    if (!a) return b || null;
    if (!b) return a;
    return new Date(a).getTime() >= new Date(b).getTime() ? a : b;
  },

  // HH:MM in local time -- deliberately NOT "45m ago". A relative label
  // goes stale on the SCREEN the moment the cashier glances back at the
  // same tile a few minutes later; a clock time does not.
  _formatClockTime(isoString) {
    if (!isoString) return null;
    const d = new Date(isoString);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  },

  // Corrected scope for 7b (see the doc section named above): the figure is
  // KEPT, dated, not blanked -- a bare "no number" removes the only
  // information the cashier had. Escapes p.unit for the same reason
  // _renderPOSGrid already escapes it on the fresh path below: unit is
  // catalog data (CSV import / any logged-in user), not this file's own
  // hand-typed strings.
  _staleStockLabel(p) {
    const clock = this._formatClockTime(this._mostRecentSyncSuccess(this._syncHealth));
    const qty = `${p.total_stock} ${this._esc(p.unit || '')}`;
    return clock ? `${t('Stock at')} ${this._esc(clock)}: ${qty}` : `${t('Stock')}: ${qty}`;
  },

  _renderPOSGrid() {
    const grid = document.getElementById('pos-product-grid');
    if (!grid) return;
    const _ICONS = { Electronics:'💻', Clothing:'👕', 'Food & Beverages':'🍔', Beverages:'🥤',
      Groceries:'🛒', Accessories:'💍', Footwear:'👟', Sports:'⚽', Beauty:'💄', Default:'📦' };
    // Text search moved server-side (_filterPOS/_runPOSSearch below) --
    // this._products IS the current result set already. Category is the
    // one filter the backend does not offer, so it still happens here,
    // client-side, over whatever page/search batch is loaded.
    //
    // launch-readiness "product variants, wave 1" (design section 3.7):
    // variant rows are excluded from the tile grid -- `parent_product_id`
    // set means "this is a child, not its own tile". The GROUPING product
    // (the parent) gets ONE tile with an "N options" badge (below) that
    // opens a variant picker instead of adding straight to the cart; the
    // scan path is untouched -- a variant's OWN barcode still resolves
    // straight through _posScan/_addToCart, this filter only affects the
    // browsed grid.
    const matches = (this._products || []).filter(p =>
      !p.parent_product_id && (!this._activeCat || p.category_id === this._activeCat));
    // How many live variants each parent in THIS batch has -- from
    // this._products (the whole loaded page/search batch), not `matches`,
    // so a variant that happens to sort outside the current category filter
    // still counts toward its parent's badge.
    const variantCountByParent = Object.create(null);
    (this._products || []).forEach(x => {
      if (x.parent_product_id) {
        variantCountByParent[x.parent_product_id] = (variantCountByParent[x.parent_product_id] || 0) + 1;
      }
    });
    if (!matches.length) {
      grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;color:var(--text-dim);padding:40px">${t('No products found.')}</div>`;
      return;
    }
    // The cap is enforced HERE, not merely trusted to already be true of
    // this._products -- _loadPOSData/_runPOSSearch both ask the server for
    // one row MORE than this, specifically so this line can tell "exactly
    // the cap came back" (truncated -- there is more) apart from "fewer
    // than the cap came back" (everything that matches is already on
    // screen), with no flag needed from the backend.
    const truncated = matches.length > this.POS_GRID_PAGE_SIZE;
    const visible = truncated ? matches.slice(0, this.POS_GRID_PAGE_SIZE) : matches;
    // Silently showing a subset would be worse than saying it is one.
    const notice = truncated
      ? `<div class="pos-grid-note" style="grid-column:1/-1;text-align:center;color:var(--text-dim);font-size:12px;padding-block:8px">${t('Showing the first 200 matches. Type to narrow the search.')}</div>`
      : '';
    grid.innerHTML = notice + visible.map(p => {
      const outOfStock = p.total_stock <= 0;
      const icon = _ICONS[p.category_name] || _ICONS.Default;
      // Phase 7 stage 7b: stale is checked for in-stock tiles only. The
      // out-of-stock branch (text, class, aria-disabled, click handler) is
      // untouched by this stage on purpose -- see the "Correction to
      // Decision 1" section of docs/launch-readiness/phase7-offline-ux.md.
      // That correction changed 7b's scope to "hide and date THE NUMBER,
      // change no enforcement": create_sale's server-side check reads the
      // same local balance _addToCart/_updateQty's max_stock cap does, so
      // suspending only the client half would not let a cashier sell past a
      // stale figure -- it would just move the refusal to a less-clear 400
      // from the server. That relaxation is stage 7d's job (the oversell
      // queue that can actually record the result), not this one's.
      const stale = !outOfStock && this._isStockStale();
      // Stock state is carried by a CLASS, not an inline colour: the class
      // picks up --warn / --danger from the token layer, and the state is
      // still legible as a word ("Out of stock") when colour is unavailable.
      // The old inline `style="color:#ef4444"` both hardcoded a hex and made
      // low-stock indistinguishable from out-of-stock without colour vision.
      // A stale figure gets NEITHER of those colours (see .is-stale's own
      // comment above) -- is-low/is-out are presentational only and carry
      // no enforcement of their own, but an amber/red urgency colour on a
      // number that might be half an hour wrong is exactly the "confident
      // lie" this stage exists to stop telling.
      const stockCls = stale ? ' is-stale' : (outOfStock ? ' is-out' : (p.total_stock <= (p.reorder_level || 0) ? ' is-low' : ''));
      // A REAL <button>, not a <div> with a click handler.
      //
      // This tile is the single most-used control on the whole product, and as
      // a <div onclick> it had no keyboard route at all: not focusable, so Tab
      // never reached it, so Enter and Space could never activate it, and a
      // screen reader announced a group of text with no control in it. Its
      // `:hover` rule was therefore the only affordance it had -- and hover
      // does not exist on a touchscreen, which is what a large share of these
      // installs are. Between the two, the tile was operable by mouse only.
      //
      // <button> is chosen over tabindex="0" + role="button" + a keydown
      // handler deliberately: the native element brings focusability, BOTH
      // activation keys (Space fires on keyup, Enter on keydown -- a hand-
      // rolled handler almost always ships one of the two), the role, and the
      // form-control focus ring, and none of those can drift away from the
      // markup later. The button resets in .pos-card (font, colour, width,
      // appearance) are what keep the tile looking exactly as it did.
      //
      // The accessible name comes from the tile's own contents -- product name,
      // price, stock -- rather than an aria-label, on purpose: an aria-label
      // REPLACES the contents for an AT user, so a label here would silently
      // withhold the price and the stock line from the one person who cannot
      // see them. The icon is already aria-hidden, so it contributes nothing.
      //
      // Out of stock is aria-disabled, NOT disabled. `disabled` removes the
      // control from the tab order and silences it, which would hide the one
      // piece of information the cashier actually needs ("this is out of
      // stock") from exactly the operator least able to infer it from a 45%
      // opacity wash. aria-disabled announces "unavailable" while keeping the
      // tile focusable and keeping its existing explain-why toast.
      // launch-readiness "product variants, wave 1": a parent tile (one
      // with live children) opens the variant picker on tap/scan-of-its-
      // own-barcode instead of adding straight to the cart -- create_sale
      // would refuse the grouping product outright (that route's own
      // `has_variants` guard), so adding it here first would only move the
      // refusal to checkout. Out-of-stock gating is SKIPPED for a parent
      // tile: `p.total_stock` is the PARENT's own balance, which is not a
      // meaningful "can this be sold" signal once its real stock lives on
      // each variant -- the picker shows each variant's own figure instead.
      const variantCount = variantCountByParent[p.id] || 0;
      const clickAction = variantCount
        ? `RetailSystem._openVariantPicker('${this._esc(p.id)}')`
        : (outOfStock ? `SubsystemApp.showToast('${this._esc(t('Out of stock'))}','error')` : `RetailSystem._addToCart('${this._esc(p.id)}')`);
      return `<button type="button" class="pos-card${(outOfStock && !variantCount)?' pos-card-outofstock':''}"
          ${(outOfStock && !variantCount) ? 'aria-disabled="true"' : ''}
          onclick="${clickAction}">
        <span class="pos-card-icon" aria-hidden="true">${icon}</span>
        <span class="pos-card-name" title="${this._esc(p.name)}">${this._esc(p.name)}</span>
        <span class="pos-card-price">${this._money(p.sell_price)}</span>
        ${variantCount ? `<span class="pos-card-stock" style="color:var(--text-muted)">${variantCount} ${t('options')}</span>` : `<span class="pos-card-stock${stockCls}">
          ${outOfStock ? t('Out of stock') : (stale ? this._staleStockLabel(p) : `${t('Stock')}: ${p.total_stock} ${this._esc(p.unit||'')}`)}
        </span>`}
      </button>`;
    }).join('');
  },

  // launch-readiness "product variants, wave 1" (design section 3.7): a
  // flat grid of one parent's children -- label, price, per-variant stock,
  // one tap to add. Opened from a parent tile's "N options" badge or from
  // scanning the PARENT's own barcode (see _posScan's has_variants branch).
  //
  // Variants are looked up from this._products FIRST (the already-loaded
  // POS batch/search result the grid itself is built from) -- no round trip
  // for the common case, since a parent tile only shows the badge when its
  // children are already in that same batch. Only falls back to
  // `GET /products/<id>/variants` when they are not (a category filter or a
  // narrow search hid them from THIS batch even though the parent matched),
  // so the picker never opens empty just because of pagination.
  async _openVariantPicker(parentId) {
    document.getElementById('ret-variant-picker')?.remove();
    const parent = (this._products || []).find(x => x.id === parentId) ||
                   (this._productsById && this._productsById[parentId]);
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'ret-variant-picker';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:420px">
        <h3>${this._esc(t('Choose a variant'))}${parent ? ` — ${this._esc(parent.name)}` : ''}</h3>
        <div id="ret-variant-picker-list" style="display:flex;flex-direction:column;gap:8px;max-height:50vh;overflow-y:auto">
          <div style="text-align:center;color:var(--text-muted);padding:20px">${this._esc(t('Loading…'))}</div>
        </div>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('ret-variant-picker').remove()">${this._esc(t('Cancel'))}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

    let variants = (this._products || []).filter(x => x.parent_product_id === parentId);
    if (!variants.length) {
      try {
        const res = await this._get(`/api/sub/retail/products/${encodeURIComponent(parentId)}/variants`);
        variants = res.data || [];
        variants.forEach(v => this._cacheProduct(v));
      } catch (e) { variants = []; }
    }
    // The picker may already be closed by the time an awaited fetch
    // resolves (the cashier gave up and dismissed it) -- write into a live
    // DOM node or not at all, never into a detached one nobody sees.
    const list = document.getElementById('ret-variant-picker-list');
    if (!list) return;
    if (!variants.length) {
      list.innerHTML = `<div style="text-align:center;color:var(--text-muted);padding:20px">${this._esc(t('No variants found.'))}</div>`;
      return;
    }
    list.innerHTML = variants.map(v => {
      const outOfStock = v.total_stock <= 0;
      return `<button type="button" class="ret-btn ret-btn-ghost" style="display:flex;justify-content:space-between;width:100%"
          ${outOfStock ? 'aria-disabled="true"' : ''}
          onclick="${outOfStock
            ? `SubsystemApp.showToast('${this._esc(t('Out of stock'))}','error')`
            : `RetailSystem._addToCart('${this._esc(v.id)}');document.getElementById('ret-variant-picker')?.remove()`}">
        <span>${this._esc(v.variant_label || v.name)}</span>
        <span>${this._money(v.sell_price)} · ${outOfStock ? this._esc(t('Out of stock')) : `${v.total_stock} ${this._esc(v.unit||'')}`}</span>
      </button>`;
    }).join('');
  },

  // Debounced: a search now reaches the server (below), so firing on every
  // keystroke would turn a 4-character hand-typed code into four requests.
  _filterPOS() {
    clearTimeout(this._posSearchTimer);
    const term = (document.getElementById('pos-search')?.value || '').trim();
    if (!term) {
      // Box cleared: restore page 1 from what _loadPOSData already cached --
      // no debounce, no round trip needed for "show me everything again".
      this._products = this._posInitialPage || [];
      this._renderPOSGrid();
      return;
    }
    this._posSearchTimer = setTimeout(() => this._runPOSSearch(term), this.POS_SEARCH_DEBOUNCE_MS);
  },

  // The debounced half of _filterPOS -- one request per pause in typing,
  // never one per keystroke.
  async _runPOSSearch(term) {
    try {
      const res = await this._get(`/api/sub/retail/products?q=${encodeURIComponent(term)}&limit=${this.POS_GRID_PAGE_SIZE + 1}`);
      this._products = res.data || [];
      this._products.forEach(p => this._cacheProduct(p));
      this._renderPOSGrid();
    } catch (e) {
      // A failed search must not blank an otherwise-working till -- leave
      // whatever is already on screen; the cashier can retry or clear the box.
    }
  },

  _addToCart(productId) {
    // this._products is only ever ONE page/search batch (see
    // _renderPOSGrid), so a product reached by SCAN -- or a search result
    // for a product outside that batch -- may not be in it. _productsById
    // is the additive fallback every server response feeds (_cacheProduct):
    // page loads, search results, and single-product scan lookups alike.
    const p = (this._products || []).find(x => x.id === productId) ||
              (this._productsById && this._productsById[productId]);
    if (!p) return;
    // Consumes and clears the pending keyboard-shortcut quantity multiplier
    // (see _onPOSShortcut / "3*") in the SAME step it is read, so it can
    // never survive to apply to a later, unrelated add -- e.g. "3*" meant
    // for one product silently multiplying the next unrelated scan too.
    // Defaults to 1, so a plain tap/scan with no multiplier armed is
    // byte-for-byte the old behaviour.
    const qty = this._consumePendingQty();
    const existing = this._cart.find(i => i.product_id === productId);
    const newQty = existing ? existing.quantity + qty : qty;
    if (newQty > p.total_stock) {
      SubsystemApp.showToast(`Only ${p.total_stock} in stock`, 'error');
      return;
    }
    if (existing) {
      existing.quantity = newQty;
      existing.line_total = newQty * existing.unit_price;
    } else {
      this._cart.push({
        product_id: p.id, name: p.name, quantity: qty,
        unit_price: p.sell_price, tax_rate: p.tax_rate || 0,
        line_total: qty * p.sell_price, max_stock: p.total_stock,
        // Promotions wave 1: category_id travels with the line so a
        // category-scoped promotion can resolve against it later (see
        // _bestPromoFor) without re-looking the product up. Not sent to the
        // server -- _checkout's payload is untouched, see that function's
        // own comment for why.
        category_id: p.category_id != null ? p.category_id : null,
        // launch-readiness "product variants" parent-tier promotions (schema
        // v25's parent_product_id column; this wave adds the PARENT TIER to
        // core/retail/promotions.py's resolve_line_discount_pct). Travels
        // with the line for the identical reason category_id does one line
        // up -- so _bestPromoFor can resolve a promotion configured on this
        // variant's PARENT product without re-looking the product up, and so
        // this preview mirrors the server's tier order EXACTLY. Same rule:
        // not sent to the server, _checkout's payload untouched.
        parent_product_id: p.parent_product_id != null ? p.parent_product_id : null,
      });
    }
    // Presentational: mark which row _renderCart should animate in. Only the
    // touched row moves — re-animating the whole list on every add/increment
    // reads as flicker at real cashier speed.
    this._lastAddedId = productId;
    this._renderCart();
  },

  // See _onPOSShortcut for how _pendingQty is armed ("3*"). Reset happens
  // HERE, not in the keydown handler -- consuming and clearing are the same
  // operation, so there is no window where a second caller could read a
  // value that has already been "spent."
  _consumePendingQty() {
    const n = this._pendingQty;
    this._pendingQty = null;
    return (n && n > 0) ? n : 1;
  },

  _updateQty(idx, delta) {
    const item = this._cart[idx];
    const newQty = item.quantity + delta;
    if (newQty > item.max_stock) { SubsystemApp.showToast(`Max stock: ${item.max_stock}`, 'error'); return; }
    if (newQty <= 0) { this._cart.splice(idx, 1); }
    else { item.quantity = newQty; item.line_total = newQty * item.unit_price; }
    this._renderCart();
  },

  // Void, and also the reset step of a completed sale (_checkout calls this on
  // success). Forced refocus, not the guarded kind: whatever the cashier was
  // typing belonged to the sale that just ended or was just voided, so the
  // caret genuinely does belong back at the scan field for the next customer.
  _clearCart() {
    this._cart = [];
    this._renderCart();
    this._refocusScan(true);
  },

  // ── Hold / Resume sale (park a cart, come back to it later) ────────────────
  // Real hands-on-testing feedback: a cashier mid-payment on one sale had no
  // way to set it aside, ring up a second customer, then come back and
  // finish the first — the cart lives only in `this._cart`, and _renderPOS()
  // resets it to [] every time the POS screen renders. This gives that cart
  // a server-side parking spot (POST /api/sub/retail/held-sales) so it
  // survives navigating away, without ever touching the real checkout
  // (/sales) endpoint or its financial-record guarantees.
  _holdSale() {
    if (!this._cart.length) { SubsystemApp.showToast('Cart is empty — nothing to hold', 'error'); return; }
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'ret-hold-modal';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:420px">
        <h3>⏸ Hold This Sale</h3>
        <p style="color:var(--text-muted);margin:0 0 18px;font-size:13px;line-height:1.5">
          The cart is parked and cleared here so you can start a new sale. Resume it later from "Held Sales".
        </p>
        <div class="ret-field">
          <label>Note (optional — e.g. "Table 4", customer name)</label>
          <input id="hold-label" placeholder="Helps you find it later" maxlength="200" />
        </div>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('ret-hold-modal').remove()">Cancel</button>
          <button class="ret-btn ret-btn-primary" id="hold-save-btn" onclick="RetailSystem._saveHold()">Hold Sale</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    document.getElementById('hold-label')?.focus();
  },

  async _saveHold() {
    const btn = document.getElementById('hold-save-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Holding…'; }
    const label = document.getElementById('hold-label')?.value.trim() || '';
    const customerId = document.getElementById('pos-customer')?.value || null;
    const payload = {
      items: this._cart,
      // customer_id is a UUID string on this schema (customers.id, since the
      // multi-device sync foundation's UUID migration) -- NOT coerced with
      // `+` the way an earlier version of this branch did. That coercion
      // was written back when customers.id was still a plain autoincrement
      // integer; against a real UUID it silently produces NaN, which
      // JSON.stringify then serializes as null, dropping the customer link
      // without any visible error. _checkout() below already sends
      // customer_id the same un-coerced way -- this mirrors that.
      customer_id: customerId || null,
      label,
      discount_pct: parseFloat(document.getElementById('pos-disc')?.value || 0),
      payment_method: this._paymentMethod,
      // Display-only figures for the resume picker (see held-sales route
      // comments in retail_api.py) — the real checkout total is always
      // recomputed server-side from live product data when this cart is
      // eventually resumed and actually charged.
      subtotal: this._currentTotals.subtotal || 0,
      total: this._currentTotals.total || 0,
    };
    try {
      const data = await this._post('/api/sub/retail/held-sales', payload);
      if (data.status === 'success') {
        document.getElementById('ret-hold-modal')?.remove();
        this._clearCart();
        const discInput = document.getElementById('pos-disc');
        if (discInput) discInput.value = 0;
        const tenderedInput = document.getElementById('pos-tendered');
        if (tenderedInput) tenderedInput.value = '';
        SubsystemApp.showToast(`Sale held — ${data.data.hold_number}`, 'success');
        this._refreshHeldCount();
      } else {
        SubsystemApp.showToast(data.message || 'Could not hold sale', 'error');
        if (btn) { btn.disabled = false; btn.textContent = 'Hold Sale'; }
      }
    } catch (e) {
      if (btn) { btn.disabled = false; btn.textContent = 'Hold Sale'; }
    }
  },

  async _refreshHeldCount() {
    try {
      const data = await this._get('/api/sub/retail/held-sales');
      const el = document.getElementById('pos-held-count');
      if (el) el.textContent = (data.data || []).length;
    } catch (e) { /* non-fatal — badge just stays stale until the next POS load */ }
  },

  _openHeldSalesModal() {
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'ret-held-modal';
    overlay.innerHTML = `
      <div class="ret-modal ret-modal-wide">
        <h3>📋 Held Sales</h3>
        <div id="held-list" style="max-height:50vh;overflow-y:auto">
          <div style="text-align:center;color:var(--text-muted);padding:30px">Loading…</div>
        </div>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('ret-held-modal').remove()">Close</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    this._loadHeldList();
  },

  async _loadHeldList() {
    const box = document.getElementById('held-list');
    if (!box) return;
    try {
      const data = await this._get('/api/sub/retail/held-sales');
      const rows = data.data || [];
      const countEl = document.getElementById('pos-held-count');
      if (countEl) countEl.textContent = rows.length;
      if (!rows.length) {
        box.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:30px">No held sales.</div>';
        return;
      }
      box.innerHTML = rows.map(r => {
        // Both isolated: the hold number is an identifier and `when` is a
        // formatted date+time, i.e. two neutral runs that swap places under an
        // RTL paragraph. This line already sits in a middot-separated sentence,
        // which is exactly the surrounding text that supplies the wrong
        // direction when the run itself carries none.
        // `_auditTimestamp` rather than a bare toLocaleString(): that formats
        // against the OPERATING SYSTEM's locale, not the language the operator
        // chose here, so an Arabic till on an English Windows showed
        // "8/22/2026, 10:15:00 AM" -- with an "AM" no catalog can reach,
        // because it never passes through t(). Same locale-neutral
        // YYYY-MM-DD HH:MM:SS the audit log uses, and for the same reason: a
        // held sale is compared against other held sales, not read as prose.
        const when = this._bdi(this._auditTimestamp(r.created_at));
        const noteHtml = r.label ? ` — ${this._esc(r.label)}` : '';
        // Pluralised through two whole catalog keys rather than "item" + "s":
        // Arabic does not pluralise by suffix, so a glued 's' is untranslatable
        // by construction. The count is a separate node so the sweep can see
        // the words on their own.
        const itemsLabel = r.item_count === 1 ? t('item') : t('items');
        return `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:12px 4px;border-bottom:1px solid var(--border-hairline)">
          <div>
            <div style="color:var(--text-primary);font-weight:600;font-size:13px">${this._bdi(r.hold_number||'')}${noteHtml}</div>
            <div style="color:var(--text-muted);font-size:11px">${this._bdi(String(r.item_count))} <span>${itemsLabel}</span> · ${this._esc(r.customer_name)} · ${when}</div>
          </div>
          <div style="display:flex;align-items:center;gap:10px">
            <span style="color:var(--text-money);font-weight:700">${this._fmt(r.total)}</span>
            <button class="ret-btn ret-btn-primary ret-btn-sm" onclick="RetailSystem._resumeHeldSale(${r.id})">${t('Resume')}</button>
            <button class="ret-btn ret-btn-danger ret-btn-sm" onclick="RetailSystem._discardHeldSale(${r.id})">${t('Discard')}</button>
          </div>
        </div>`;
      }).join('');
    } catch (e) {
      box.innerHTML = '<div style="text-align:center;color:var(--state-danger-text);padding:20px">Failed to load held sales.</div>';
    }
  },

  async _resumeHeldSale(id) {
    if (this._cart.length && !confirm('Your current cart is not empty. Resuming will replace it with the held sale. Continue?')) return;
    try {
      const data = await this._post(`/api/sub/retail/held-sales/${id}/resume`, {});
      if (data.status !== 'success') { SubsystemApp.showToast(data.message || 'Could not resume sale', 'error'); return; }
      const snap = data.data;
      this._cart = snap.items || [];
      this._paymentMethod = snap.payment_method || 'cash';
      document.getElementById('ret-held-modal')?.remove();
      this._renderCart();
      const discInput = document.getElementById('pos-disc');
      if (discInput) discInput.value = snap.discount_pct || 0;
      if (snap.customer_id) {
        const sel = document.getElementById('pos-customer');
        if (sel) sel.value = String(snap.customer_id);
      }
      document.querySelectorAll('.pos-pay-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.method === this._paymentMethod);
      });
      this._recalc();
      this._refreshHeldCount();
      SubsystemApp.showToast(`Resumed ${snap.hold_number}`, 'success');
    } catch (e) { /* _fetch already surfaces auth errors via toast/redirect */ }
  },

  async _discardHeldSale(id) {
    if (!confirm('Discard this held sale? This cannot be undone.')) return;
    try {
      const data = await this._del(`/api/sub/retail/held-sales/${id}`);
      if (data.status === 'success') {
        SubsystemApp.showToast('Held sale discarded', 'success');
        this._loadHeldList();
      } else {
        SubsystemApp.showToast(data.message || 'Could not discard held sale', 'error');
      }
    } catch (e) { /* _fetch already surfaces auth errors via toast/redirect */ }
  },

  // Cart line layout, and why the DOM is shaped the way it is.
  //
  // A line is scannable left-to-right (start-to-end, mirrored under RTL) as
  // NAME -> QUANTITY -> LINE TOTAL, with the amounts in a fixed-width tabular
  // column (.pos-line-total, min-inline-size + text-align:end) so the figures
  // in a ten-line cart actually form a column a cashier can add up by eye.
  //
  // The remove control is deliberately NOT a sibling of the quantity buttons.
  // It used to sit directly after the "+" button in the same flex container:
  // two of the highest-frequency taps on the whole screen, one finger-width
  // from an action that destroys a line. During a queue that misfire costs a
  // re-ring in front of the customer. It now lives in its own
  // .pos-line-danger container, behind a .pos-line-sep spacer and a rule,
  // so the frequent controls and the destructive one share no parent and are
  // not adjacent. retail_surface_pos_test.js asserts that separation against
  // the real rendered markup, because "we moved it" is exactly the kind of
  // claim that quietly reverts.
  _renderCart() {
    const container = document.getElementById('pos-cart');
    if (!container) return;
    if (!this._cart.length) {
      container.innerHTML = `<div class="pos-empty"><span class="pos-empty-icon" aria-hidden="true">🛒</span><span>${t('Cart is empty')}</span><span class="pos-empty-hint">${t('Scan or tap a product to begin')}</span></div>`;
      this._recalc();
      this._refocusScan();
      return;
    }
    // pos-row-new: entrance animation for the row the cashier just touched
    // (set by _addToCart), cleared immediately after so qty edits and
    // removals don't re-trigger it.
    const newId = this._lastAddedId;
    this._lastAddedId = null;
    const decLabel = this._esc(t('Decrease quantity'));
    const incLabel = this._esc(t('Increase quantity'));
    const remLabel = this._esc(t('Remove line'));
    // Promotions wave 1 (ROADMAP.md "retail schema v23"). Same clamp as
    // _recalc below -- read once here rather than per line, matching how
    // _recalc itself only reads #pos-disc once per pass.
    const manualFrac = Math.min(100, Math.max(0, parseFloat(document.getElementById('pos-disc')?.value || 0))) / 100;
    container.innerHTML = this._cart.map((item, i) => {
      const promo = this._bestPromoFor(item);
      // A promotion tag/price is shown whenever a promotion RESOLVES for this
      // line, even on the sale where the manual discount happens to already
      // beat it -- the cashier still gets told why a promo is in play, per
      // Task 1(e). BEST PRICE WINS decides the CHARGE (see _recalc); this only
      // decides what price is shown for that promoted line.
      const promoFrac = promo ? (promo.discount_pct || 0) / 100 : 0;
      const lineFrac = Math.max(manualFrac, promoFrac);
      const metaHTML = promo
        ? `<span class="pos-price-orig">${this._money(item.unit_price)}</span> ${this._money(item.unit_price * (1 - lineFrac))} × ${item.quantity}`
        : `${this._money(item.unit_price)} × ${item.quantity}`;
      // Icon and name are DELIBERATELY separate text nodes (see
      // retail_surface_i18n_test.js's "no translatable string shares a node
      // with an emoji" rule) -- promo.name is shop-authored data, like a
      // product or category name elsewhere in this file, so it is escaped,
      // never passed through t().
      const promoTagHTML = promo
        ? `<div class="pos-promo-tag" title="${this._esc(promo.name)}"><span aria-hidden="true">🏷️</span><span>${this._esc(t('Promo'))}: ${this._esc(promo.name)}</span></div>`
        : '';
      return `
      <div class="pos-cart-row${item.product_id === newId ? ' pos-row-new' : ''}">
        <div class="pos-line-main">
          <div class="pos-item-name" title="${this._esc(item.name)}">${this._esc(item.name)}</div>
          <div class="pos-item-meta">${metaHTML}</div>
          ${promoTagHTML}
        </div>
        <div class="pos-line-freq">
          <div class="pos-qty-wrap">
            <button class="pos-qty-btn" onclick="RetailSystem._updateQty(${i},-1)" title="${decLabel}" aria-label="${decLabel}">−</button>
            <span class="pos-qty-val">${item.quantity}</span>
            <button class="pos-qty-btn" onclick="RetailSystem._updateQty(${i},1)" title="${incLabel}" aria-label="${incLabel}">+</button>
          </div>
          <span class="pos-line-total">${this._money(item.line_total)}</span>
        </div>
        <span class="pos-line-sep" aria-hidden="true"></span>
        <div class="pos-line-danger">
          <button class="pos-remove-btn" onclick="RetailSystem._removeLine(${i})" title="${remLabel}" aria-label="${remLabel}">✕</button>
        </div>
      </div>`;
    }).join('');
    this._recalc();
    // A tap on a cart control is exactly the "interaction that should not
    // steal focus" case: the next thing that happens is almost always another
    // scan. Declines if the cashier is mid-edit in a money field.
    this._refocusScan();
  },

  // Was an inline `RetailSystem._cart.splice(i,1);RetailSystem._renderCart()`
  // in the onclick attribute. Named, so the destructive path is one greppable
  // thing rather than a statement pair hidden in markup.
  _removeLine(idx) {
    if (idx < 0 || idx >= this._cart.length) return;
    this._cart.splice(idx, 1);
    this._renderCart();
  },

  // Promotions wave 1 (ROADMAP.md "retail schema v23"). Resolves the single
  // best-priced ACTIVE promotion for one cart line, or null.
  //
  //   * product_id XOR category_id is set per the frozen API contract, so a
  //     given promotion is either a product match or a category match, never
  //     both -- and since a product can never be its own parent, one promo
  //     can also never be BOTH a product match and a parent match for the
  //     same line (see _validate_parent_product's self-reference refusal,
  //     retail_api.py). So each promo lands in exactly one of the three
  //     buckets below, never two.
  //   * A product-specific promotion beats a PARENT promotion (one
  //     configured on this line's parent_product_id -- launch-readiness
  //     "product variants" follow-up, mirroring
  //     core/retail/promotions.py's resolve_line_discount_pct EXACTLY, tier
  //     for tier), which in turn beats a category one on the same line, even
  //     if the lower tier's discount_pct is higher -- the more specific rule
  //     wins at every step, not the bigger number.
  //   * Among ties within the same specificity (two product promos on the
  //     same product, two parent promos on the same parent, or two category
  //     promos on the same category), the highest discount_pct wins.
  //
  // Reads `this._promotions || []` rather than assuming it is set: several
  // existing test harnesses in products/retail/tests/ drive _addToCart/
  // _recalc directly without ever calling _renderPOS (which is what
  // initialises it), so undefined must resolve to "no promotions", not throw.
  _bestPromoFor(item) {
    const promos = this._promotions || [];
    let productMatch = null, parentMatch = null, categoryMatch = null;
    promos.forEach(p => {
      if (p.product_id != null && String(p.product_id) === String(item.product_id)) {
        if (!productMatch || (p.discount_pct || 0) > (productMatch.discount_pct || 0)) productMatch = p;
      } else if (p.product_id != null && item.parent_product_id != null && String(p.product_id) === String(item.parent_product_id)) {
        if (!parentMatch || (p.discount_pct || 0) > (parentMatch.discount_pct || 0)) parentMatch = p;
      } else if (p.category_id != null && item.category_id != null && String(p.category_id) === String(item.category_id)) {
        if (!categoryMatch || (p.discount_pct || 0) > (categoryMatch.discount_pct || 0)) categoryMatch = p;
      }
    });
    return productMatch || parentMatch || categoryMatch || null;
  },

  // Mirrors core/retail/pricing.py's calculate_line() EXACTLY, mode for
  // mode — that Python module is this codebase's documented single source
  // of truth for the tax/discount formula; if either side changes, both
  // must change together (there is no shared runtime between browser JS
  // and the Python backend to enforce this automatically).
  //
  //   after_discount (default): taxable = line*(1-discFrac); tax = taxable*rate
  //   before_discount:          taxable = line (gross);       tax = taxable*rate
  //
  // Either way, discount is subtracted once at the subtotal level and tax is
  // added once at the end, so `total = subtotal - discAmt + tax` holds for
  // both modes — only what the tax is computed ON changes.
  //
  // Promotions wave 1 (ROADMAP.md "retail schema v23"): the single uniform
  // discFrac above became a PER-LINE lineFrac = max(manualFrac, promoFrac) --
  // BEST PRICE WINS, never summed (a 60% promotion plus a 50% manual discount
  // must charge 60%, not 110%). The aggregate discount below is written as
  // "the manual invoice-level discount on everything, plus whatever a
  // promotion adds ON TOP for lines where it beats the manual rate" rather
  // than a flat per-line sum, specifically so that with ZERO promotions
  // `promoExtra` never leaves its initial 0 and `discAmt` collapses to EXACTLY
  // `subtotal * manualFrac` -- the original formula, bit for bit. Summing
  // `line_total * frac` per line instead is algebraically equal but NOT
  // numerically identical in IEEE754 (measured ~33% divergence at full
  // precision against `subtotal * frac` across 300k realistic fixtures),
  // which would silently change this screen's own numbers on every sale with
  // no promotions configured -- exactly the regression
  // retail_promotions_ui_test.js's no-promotions-equivalence case exists to
  // catch. See that test for the mutation proof.
  _recalc() {
    // AUDIT-fix: the #pos-disc input's min/max=0/100 is decorative HTML
    // only -- a typed value outside that range still reaches parseFloat
    // unclamped, which made discAmt exceed subtotal and total go negative
    // in this preview. Server-side create_sale() independently clamps via
    // tax_engine.clamp_discount_pct(), so the charged amount was never at
    // risk -- this was a client display bug only, fixed at the source read
    // so every downstream use (subtotal/tax/total/change) is consistent.
    const discPct = Math.min(100, Math.max(0, parseFloat(document.getElementById('pos-disc')?.value || 0)));
    const manualFrac = discPct / 100;
    const beforeDiscount = this._taxMode === 'before_discount';
    let subtotal = 0, promoExtra = 0, tax = 0;
    this._cart.forEach(i => {
      subtotal += i.line_total;
      const promo = this._bestPromoFor(i);
      const promoFrac = promo ? (promo.discount_pct || 0) / 100 : 0;
      const lineFrac = Math.max(manualFrac, promoFrac);
      if (lineFrac > manualFrac) promoExtra += i.line_total * (lineFrac - manualFrac);
      const taxableBase = beforeDiscount ? i.line_total : (i.line_total * (1 - lineFrac));
      tax += taxableBase * (i.tax_rate / 100);
    });
    const discAmt = subtotal * manualFrac + promoExtra;
    const total   = subtotal - discAmt + tax;
    this._currentTotals = { subtotal, discount: discAmt, tax, total };
    // All three spans already carry `.money` in the POS template, so the amount
    // IS the money element -- _setMoney fills it in and marks a negative on
    // that same element rather than leaving the cue to whatever wraps it.
    this._setMoney(document.getElementById('pos-sub'),   subtotal);
    this._setMoney(document.getElementById('pos-tax'),   tax);
    this._setMoney(document.getElementById('pos-total'), total);
    const checkoutBtn = document.getElementById('pos-checkout-btn');
    if (checkoutBtn) {
      checkoutBtn.textContent = this._checkoutLabel(total);
      checkoutBtn.disabled = false;   // re-enable after a sale so the next receipt can be charged
    }
    this._calcChange();
  },

  _calcChange() {
    const tendered = parseFloat(document.getElementById('pos-tendered')?.value || 0);
    const total    = this._currentTotals.total || 0;
    const changeRow = document.getElementById('pos-change-row');
    const changeEl  = document.getElementById('pos-change');
    if (this._paymentMethod === 'cash' && tendered > 0 && tendered >= total) {
      const change = tendered - total;
      if (changeRow) changeRow.style.display = '';
      if (changeEl)  this._setMoney(changeEl, change);
    } else {
      if (changeRow) changeRow.style.display = 'none';
    }
  },

  _setPayment(method, btn) {
    this._paymentMethod = method;
    document.querySelectorAll('.pos-pay-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const changeRow = document.getElementById('pos-change-row');
    if (method !== 'cash') {
      if (document.getElementById('pos-tendered')) document.getElementById('pos-tendered').value = '';
      if (changeRow) changeRow.style.display = 'none';
    }
    // Picking a tender is a button press, not a typing target — hand the
    // keyboard back to the scanner.
    this._refocusScan();
  },

  // ══ BARCODE SCANNER ENGINE ════════════════════════════════════════════════
  // Driver-free support for USB / Bluetooth HID ("keyboard wedge") scanners on
  // the Windows desktop build. Such scanners type the barcode like a keyboard
  // and end with Enter; we recognise that by the rapid inter-keystroke timing a
  // human can't reproduce. This is an OPTIONAL enhancement — the manual product
  // search, tap-to-add and manual barcode entry all keep working untouched.
  //
  // The listener is gated to the desktop (non-Android) so the shared Android
  // build is unaffected, per the Windows-EXE-only scope.

  _scannerDefaults: { enabled: true, inputMode: 'auto', timeoutMs: 50, minLength: 3, prefix: '', suffix: '', sound: true },
  _scan: { buffer: '', firstAt: 0, lastAt: 0, lastScanAt: 0, lastScanCode: '', capture: null },

  // ── Keyboard shortcuts state ────────────────────────────────────────────
  // _pendingQty: set by "<digits>*" (see _onPOSShortcut) and consumed by the
  // very next _addToCart -- see _consumePendingQty(). _qtyKeyBuffer is the
  // digits typed so far, waiting for either "*" (commit) or any other key
  // (discard); it never survives past the keystroke that resolves it.
  // _qtyKeyLastAt: this layer's OWN inter-digit timestamp, independent of
  // the scanner's -- see the digit branch of _onPOSShortcut for why.
  _pendingQty: null,
  _qtyKeyBuffer: '',
  _qtyKeyLastAt: 0,

  _isDesktopScanner() { return !/Android/i.test(navigator.userAgent || ''); },

  scannerCfg() {
    try { return Object.assign({}, this._scannerDefaults, JSON.parse(localStorage.getItem('aura_scanner_cfg') || '{}')); }
    catch (e) { return Object.assign({}, this._scannerDefaults); }
  },
  saveScannerCfg(cfg) {
    try { localStorage.setItem('aura_scanner_cfg', JSON.stringify(cfg)); } catch (e) {}
  },

  // Init once, on first render of any retail section. Idempotent + desktop-only.
  _initScanner() {
    if (this._scannerBound || !this._isDesktopScanner()) return;
    this._scannerBound = true;
    // Capture phase: lets us swallow scanner keystrokes before inputs see them.
    document.addEventListener('keydown', (e) => this._onScannerKey(e), true);
  },

  _onScannerKey(e) {
    // Only while the Retail subsystem is on screen.
    if (!(window.SubsystemApp && SubsystemApp.active === 'retail')) return;
    const cfg = this.scannerCfg();
    const capturing = !!this._scan.capture;
    if (!cfg.enabled && !capturing) return;

    // In normal mode we must NEVER disturb keystrokes destined for a real text
    // field — a fast typist must not lose characters. So auto-detection only runs
    // when focus is outside any editable field, or inside one of our search boxes
    // (where a scan is expected). Explicit capture mode (Scan buttons / test box)
    // bypasses this and takes over the keyboard for one scan.
    if (!capturing) {
      const el = document.activeElement;
      const tag = el ? el.tagName : '';
      const editable = tag === 'INPUT' || tag === 'TEXTAREA' || (el && el.isContentEditable);
      const isSearchBox = !!el && (el.id === 'pos-search' || el.id === 'prod-search');
      if (editable && !isSearchBox) return;
    }

    const st = this._scan;
    const now = (window.performance && performance.now) ? performance.now() : Date.now();
    const gap = now - st.lastAt;
    st.lastAt = now;

    // cfg.inputMode ("Auto Detect" vs "Keyboard HID" in Scanner Settings) used
    // to be saved but never read here, so both options behaved identically --
    // a dead setting that looked live in the UI. Auto Detect keeps the tight
    // default window, sized to guess between an unconfirmed device and a
    // human typist. Keyboard HID means the admin has explicitly confirmed a
    // dedicated HID keyboard-wedge scanner is wired up (not shared with
    // manual typing), so we trust it with 3x the per-key timing slack --
    // enough to absorb a slower/jittery scanner or a laggy remote-desktop
    // session without falling back to manual entry, but still bounded (not
    // unlimited) so a stray, never-terminated keystroke can't linger
    // indefinitely and corrupt a later scan.
    const resetWindowMs = cfg.inputMode === 'keyboard' ? cfg.timeoutMs * 3 : cfg.timeoutMs;

    // A pause longer than the window starts a fresh sequence (human typing or a
    // new scan). This is what keeps manual typing + Enter from looking like a scan.
    if (gap > resetWindowMs) { st.buffer = ''; st.firstAt = now; }

    if (e.key === 'Enter') {
      const code = this._applyAffixes(st.buffer, cfg);
      const fastBurst = st.firstAt && ((now - st.firstAt) <= resetWindowMs * (st.buffer.length + 2));
      st.buffer = '';
      if (code.length >= cfg.minLength && (fastBurst || capturing)) {
        e.preventDefault(); e.stopPropagation();
        this._dispatchScan(code, cfg);
      }
      return;
    }

    if (e.key && e.key.length === 1) {
      st.buffer += e.key;
      // Only capture mode swallows characters. In normal mode we never preventDefault
      // printable keys, so human typing in search boxes is never disturbed.
      if (capturing) { e.preventDefault(); e.stopPropagation(); }
    }
  },

  _applyAffixes(raw, cfg) {
    let code = raw || '';
    if (cfg.prefix && code.startsWith(cfg.prefix)) code = code.slice(cfg.prefix.length);
    if (cfg.suffix && code.endsWith(cfg.suffix)) code = code.slice(0, code.length - cfg.suffix.length);
    return code.trim();
  },

  _dispatchScan(code, cfg) {
    const st = this._scan;
    st.lastScanAt = Date.now();
    st.lastScanCode = code;
    if (cfg.sound) this._beep();

    // Capture mode (Settings "Test Scanner" or a barcode field) wins over routing.
    if (st.capture) { const cb = st.capture; st.capture = null; try { cb(code); } catch (e) {} return; }

    // Route to whatever is on screen. Open modals take priority over the section.
    if (document.getElementById('ret-prod-modal')) {  // Add/Edit Product → barcode field
      const f = document.getElementById('pm-barcode');
      if (f) { f.value = code; f.focus(); SubsystemApp.showToast('Barcode captured', 'success'); }
      return;
    }
    if (document.getElementById('ret-po-modal'))   return this._poScan(code);
    switch (this._section) {
      case 'pos':       return this._posScan(code);
      case 'products':  return this._productsScan(code);
      case 'purchases': return this._poScan(code);
      default:          SubsystemApp.showToast(`Scanned: ${code}`, 'info');
    }
  },

  // Lookup helper shared by every scan handler. Used to be a linear scan
  // over this._products (the client's local catalogue array) -- that broke
  // the moment the POS stopped loading the whole catalogue (see the page-1
  // fix above): a code for a product outside the loaded page could never
  // resolve, silently. This now asks the server directly for the one row it
  // needs (GET /products/lookup, retail_api.py -- company-scoped,
  // index-backed as of schema v21).
  //
  // Returns { product, error } rather than a bare value, because a scan
  // handler has to tell THREE outcomes apart, not two:
  //   - product set             -> a real match.
  //   - product null, no error  -> the server said "no such product" (404),
  //     a normal outcome for a till -- see _showScanNotFound.
  //   - error set                -> the lookup itself failed (offline, a
  //     non-401 server error, a malformed body). Telling a cashier "not
  //     found" here would blame the product for a network problem.
  async _findByCode(code) {
    try {
      const res = await this._get(`/api/sub/retail/products/lookup?code=${encodeURIComponent(code)}`);
      if (res && res.status === 'success' && res.data) {
        this._cacheProduct(res.data);
        return { product: res.data, error: null };
      }
      return { product: null, error: null };
    } catch (e) {
      return { product: null, error: e };
    }
  },

  // Register a one-shot handler for the next scan (field capture / test box).
  captureNextScan(cb) { this._scan.capture = cb; },
  cancelCapture() { this._scan.capture = null; },

  _beep() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      this._audioCtx = this._audioCtx || new Ctx();
      const ctx = this._audioCtx;
      if (ctx.state === 'suspended') ctx.resume();
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = 'square'; o.frequency.value = 880;
      g.gain.setValueAtTime(0.05, ctx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.12);
      o.connect(g); g.connect(ctx.destination);
      o.start(); o.stop(ctx.currentTime + 0.12);
    } catch (e) {}
  },

  // ── Per-screen scan handlers ──────────────────────────────────────────────
  async _posScan(code) {
    const search = document.getElementById('pos-search');
    if (search) { search.value = ''; this._filterPOS(); }
    const { product, error } = await this._findByCode(code);
    if (product && product.has_variants) {
      // launch-readiness "product variants, wave 1": scanning the PARENT's
      // own barcode (parents may keep one) opens the picker instead of
      // adding the grouping product straight to the cart -- create_sale's
      // own `has_variants` guard would refuse it at checkout anyway, so
      // resolving here avoids a scan that "worked" only to fail later.
      this._openVariantPicker(product.id);
    } else if (product) {
      this._addToCart(product.id);              // reuses existing stock checks + cart merge
      SubsystemApp.showToast(`Added: ${product.name}`, 'success');
    } else if (error) {
      // Distinct from "not found": the lookup itself failed, so telling the
      // cashier this product doesn't exist would be a lie about why the
      // scan didn't resolve. Same phrasing _checkout's own network-failure
      // toast already uses.
      SubsystemApp.showToast('Scan failed — check the connection and try again', 'error');
    } else {
      this._showScanNotFound(code);
    }
  },

  async _productsScan(code) {
    const inp = document.getElementById('prod-search');
    if (inp) { inp.value = code; this._filterProducts(); }
    const { product, error } = await this._findByCode(code);
    if (error) { SubsystemApp.showToast('Scan failed — check the connection and try again', 'error'); return; }
    SubsystemApp.showToast(product ? `Found: ${product.name}` : `No product matches ${code}`, product ? 'success' : 'error');
  },

  async _poScan(code) {
    const { product, error } = await this._findByCode(code);
    if (!product) {
      SubsystemApp.showToast(error ? 'Scan failed — check the connection and try again' : `No product matches ${code}`, 'error');
      return;
    }
    const sel = document.getElementById('po-item-prod');
    if (sel) { sel.value = String(product.id); sel.dispatchEvent(new Event('change')); }
    document.getElementById('po-item-qty')?.focus();
    SubsystemApp.showToast(`Scanned: ${product.name}`, 'success');
  },

  // "Product not found" prompt — Add New Product / Scan Again / Cancel.
  _showScanNotFound(code) {
    document.getElementById('ret-scan-nf')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'ret-scan-nf';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:380px;text-align:center">
        <div style="font-size:46px;margin-bottom:10px">🔍</div>
        <h3 style="margin:0 0 6px">Product not found</h3>
        <p style="color:var(--text-muted);margin:0 0 22px">No item matches barcode
          <span style="font-family:monospace;color:var(--text-primary)">${code}</span></p>
        <div style="display:flex;flex-direction:column;gap:10px">
          <button class="ret-btn ret-btn-primary" onclick="RetailSystem._addProductFromScan('${String(code).replace(/'/g,"\\'")}')">➕ Add New Product</button>
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('ret-scan-nf').remove()">🔁 Scan Again</button>
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('ret-scan-nf').remove()">Cancel</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    // Auto-dismiss so the till is ready for the next scan if the cashier ignores it.
    setTimeout(() => overlay.remove(), 12000);
  },

  _addProductFromScan(code) {
    document.getElementById('ret-scan-nf')?.remove();
    const catOpts = (this._categories || []).map(c => `<option value="${this._esc(c.id)}">${this._esc(c.name)}</option>`).join('');
    this._showProductModal({ barcode: code }, catOpts, '', this._eligibleParentOpts(null, null));
  },

  // Explicit one-shot capture from the product modal's "Scan" button.
  _captureBarcodeField() {
    const btn = document.getElementById('pm-scan-btn');
    const field = document.getElementById('pm-barcode');
    if (btn) { btn.textContent = '⏳ Waiting…'; btn.disabled = true; }
    if (field) field.focus();
    this.captureNextScan((code) => {
      if (field) field.value = code;
      if (btn) { btn.textContent = '📷 Scan'; btn.disabled = false; }
      SubsystemApp.showToast('Barcode captured', 'success');
    });
    // If no scan arrives, restore the button so it never gets stuck.
    setTimeout(() => {
      if (btn && btn.disabled) { btn.textContent = '📷 Scan'; btn.disabled = false; this.cancelCapture(); }
    }, 15000);
  },

  // ══ KEYBOARD SHORTCUTS (POS) ═════════════════════════════════════════════
  // A senior review found this screen had NO keyboard shortcuts at all:
  // every sale required a mouse end to end (tap tiles, click Tendered, click
  // a payment method, click Charge). This layer drives the EXISTING flow
  // faster -- it changes no pricing, payment, or totals logic, only how fast
  // the cashier can reach it.
  //
  // Unlike _initScanner (desktop-only HID wedge), this runs on every
  // platform: a keyboard behaves the same in the packaged desktop app, a
  // browser tab, or via a Bluetooth keyboard paired to an Android till.
  //
  // Keys, all chosen to be scanner-safe (see _scannerBusy) and OS-safe (none
  // of them ever requires a modifier, so none of this can shadow a
  // Ctrl/Cmd/Alt combo the OS or browser owns):
  //   Enter          charge the sale (existing _checkout(), unchanged)
  //   <digits> *     quantity multiplier -- "3*" before a scan or a tile tap
  //                  sets the NEXT _addToCart's quantity (see
  //                  _consumePendingQty). A hypermarket cashier ringing 12
  //                  identical items presses 1, 2, *, then scans once --
  //                  four keys, not twelve taps.
  //   X              exact-cash pay -- tender = total, method = cash,
  //                  charge, all in one key. The single most common
  //                  transaction in a shop.
  //   /              focus the search box (the GitHub/Slack convention).
  //   Delete         void the most recently touched cart line.
  //   Escape         cancel a pending quantity multiplier.
  //
  // Init once, on first render of any retail section -- mirrors _initScanner.
  _initShortcuts() {
    if (this._shortcutsBound) return;
    this._shortcutsBound = true;
    // Bubble phase (no `true` third argument): _onScannerKey is registered
    // on the CAPTURE phase above, so for any single keydown it ALWAYS runs
    // first -- capture unconditionally precedes bubble for the same
    // dispatch. When it completes a real scan it calls stopPropagation(),
    // which aborts the event before it ever reaches this bubble-phase
    // listener, so a genuine scan can structurally never also be read as a
    // shortcut. The reverse direction (a shortcut keystroke landing inside
    // an in-progress scan burst) is guarded separately -- see
    // _scannerBusy().
    document.addEventListener('keydown', (e) => this._onPOSShortcut(e));
  },

  // True while the wedge listener DEFINITELY owns this keystroke, with no
  // ambiguity left to resolve: either an explicit capture is armed (Scanner
  // Settings "Test Scanner", or a barcode field's own Scan button -- see
  // captureNextScan), or the scan buffer already held a character BEFORE
  // this one (buffer.length > 1 means _onScannerKey, which always runs
  // first, just appended THIS keydown onto an accumulation that did not
  // start empty -- i.e. this is the 2nd-or-later character of a burst,
  // never a fresh isolated press).
  //
  // What this does NOT catch, on purpose: the very FIRST character of a
  // burst leaves the scan buffer at exactly length 1 -- structurally
  // identical to an isolated deliberate keypress, since both start from an
  // empty buffer. That one-character ambiguity cannot be resolved by buffer
  // state at all; the digit branch of _onPOSShortcut resolves it instead
  // with its own inter-keystroke timing check, the same signal
  // _onScannerKey itself uses to tell a burst from a human.
  _scannerBusy() {
    const st = this._scan;
    if (!st) return false;
    if (st.capture) return true;
    return !!(st.buffer && st.buffer.length > 1);
  },

  _onPOSShortcut(e) {
    if (!(window.SubsystemApp && SubsystemApp.active === 'retail')) return;
    if (this._section !== 'pos') return;               // this is the till, not every screen
    if (e.ctrlKey || e.metaKey || e.altKey) return;      // never shadow a browser/OS combo

    if (this._scannerBusy()) { this._qtyKeyBuffer = ''; return; }

    const isDigit = e.key.length === 1 && e.key >= '0' && e.key <= '9';

    if (e.key === '*') {
      // Real barcodes (EAN/UPC, the format this till's own Scanner Settings
      // and _findByCode target) are numeric-only and never contain "*", so
      // its arrival is unambiguous regardless of timing -- the digits that
      // led up to it are what need to have been trustworthy, and the digit
      // branch below is what keeps them that way.
      const n = parseInt(this._qtyKeyBuffer, 10);
      this._qtyKeyBuffer = '';
      if (n > 0) {
        this._pendingQty = n;
        SubsystemApp.showToast(`×${n} — next item added at this quantity`, 'info');
      }
      return;
    }

    if (isDigit) {
      // The one-character ambiguity _scannerBusy() cannot resolve (see its
      // comment): the FIRST digit of a fresh scan burst and an isolated
      // deliberate digit press are indistinguishable from buffer state
      // alone. Resolved here with an independent timing check, mirroring
      // _onScannerKey's own reset-window logic but tracked separately so
      // this layer never reads (or races) the scanner's timestamps: a
      // digit arriving at scanner speed relative to the PREVIOUS digit this
      // layer saw is almost certainly a barcode character, not the start of
      // a deliberate "N*", so the run is discarded rather than trusted.
      const cfg = this.scannerCfg();
      const now = (window.performance && performance.now) ? performance.now() : Date.now();
      const burstWindow = cfg.enabled ? ((cfg.inputMode === 'keyboard' ? cfg.timeoutMs * 3 : cfg.timeoutMs) || 50) : 0;
      const gap = now - this._qtyKeyLastAt;
      this._qtyKeyLastAt = now;
      if (cfg.enabled && gap <= burstWindow) { this._qtyKeyBuffer = ''; return; }
      // Capped at 4 digits (max 9999) -- long enough for any real
      // hypermarket multiplier, short enough that a runaway buffer can't
      // parse into something absurd.
      if (this._qtyKeyBuffer.length < 4) this._qtyKeyBuffer += e.key;
      return;
    }

    // Any other key ends a digit run that never reached "*", so a stray
    // earlier digit can never bleed into a later, unrelated "*". Everything
    // from here down is a real shortcut, not multiplier entry.
    this._qtyKeyBuffer = '';

    if (e.key === 'Escape') {
      // "cancel the current entry state" -- the only entry state this layer
      // introduces is a pending quantity multiplier. Deliberately not
      // gated on focus location: cancelling a stray "3" is always safe.
      this._pendingQty = null;
      return;
    }

    const el = document.activeElement;
    const tag = el ? el.tagName : '';
    const inTextField = tag === 'INPUT' || tag === 'TEXTAREA' || (el && el.isContentEditable);
    // Conservative on purpose, with NO exception for #pos-search (unlike
    // _onScannerKey's isSearchBox carve-out): a cashier who types a manual
    // search term and hits Enter out of habit must never accidentally
    // charge whatever is already sitting in the cart. Every shortcut below
    // shares this one guard.
    if (inTextField) return;

    if (e.key === 'Enter') {
      if (!this._cart.length) return;
      const btn = document.getElementById('pos-checkout-btn');
      if (btn && btn.disabled) return;   // a charge is already in flight
      e.preventDefault();
      this._checkout();
      return;
    }

    if (e.key === 'x' || e.key === 'X') {
      if (!this._cart.length) return;
      const btn = document.getElementById('pos-checkout-btn');
      if (btn && btn.disabled) return;
      e.preventDefault();
      this._exactCashCharge();
      return;
    }

    if (e.key === '/') {
      e.preventDefault();
      this._refocusScan(true);
      return;
    }

    if (e.key === 'Delete') {
      e.preventDefault();
      this._voidLastLine();
      return;
    }
  },

  // Tender = total, method = cash, charge -- the single most common
  // transaction in a shop, on one key. Mirrors _resumeHeldSale's own
  // pattern for syncing the tender buttons to a payment method set
  // programmatically (there is no clicked `btn` element here for
  // _setPayment's signature, which requires one).
  _exactCashCharge() {
    if (!this._cart.length) return;
    this._paymentMethod = 'cash';
    document.querySelectorAll('.pos-pay-btn').forEach(b => {
      b.classList.toggle('active', b.dataset && b.dataset.method === 'cash');
    });
    const tenderedField = document.getElementById('pos-tendered');
    const total = this._currentTotals.total || 0;
    if (tenderedField) tenderedField.value = String(total);
    this._calcChange();
    this._checkout();
  },

  // "Clear/void the current line" -- the most recently touched row, exactly
  // what _renderCart's own entrance animation already tracks via
  // _lastAddedId. Falls back to the last line in the cart if nothing has
  // been "touched" yet this render (e.g. the cart was restored from a held
  // sale). Reuses _removeLine, so this is exactly what pressing the row's
  // own ✕ button does -- no new removal logic.
  _voidLastLine() {
    if (!this._cart.length) return;
    const idx = this._lastAddedId != null
      ? this._cart.findIndex(i => i.product_id === this._lastAddedId)
      : this._cart.length - 1;
    this._removeLine(idx >= 0 ? idx : this._cart.length - 1);
  },

  // Mirrors the server's own stock decrement locally instead of re-fetching
  // the whole catalogue to observe it -- see _checkout below. Looks a
  // product up the SAME way _addToCart does (this._products first, then the
  // by-id cache), so a line for a product reached only via scan still gets
  // its cached stock corrected, not just whatever was on the loaded page.
  _applyLocalStockDecrement(lines) {
    (lines || []).forEach(line => {
      const p = (this._products || []).find(x => x.id === line.product_id) ||
                (this._productsById && this._productsById[line.product_id]);
      if (p && typeof p.total_stock === 'number') {
        p.total_stock = Math.max(0, p.total_stock - (line.quantity || 0));
      }
    });
    this._renderPOSGrid();
  },

  async _checkout() {
    if (!this._cart.length) { SubsystemApp.showToast('Cart is empty', 'error'); return; }
    const total    = this._currentTotals.total || 0;
    const tendered = parseFloat(document.getElementById('pos-tendered')?.value || 0);
    if (this._paymentMethod === 'cash' && tendered > 0 && tendered < total) {
      SubsystemApp.showToast('Cash tendered is less than total', 'error'); return;
    }
    const customerId = document.getElementById('pos-customer')?.value || null;
    // AUDIT: mirrors retail_api.py's credit-sale rule ("Credit sales require
    // a customer (walk-in not allowed).") on this side of the wire too.
    // Before this check, clicking Credit with no customer selected sailed
    // straight into _post() -- the button flipped to "Processing…", then
    // reverted a beat later once the server's 400 came back, with the only
    // explanation being a toast the cashier had to read fast. Catching it
    // here means the button never even starts processing for a sale that
    // was never going to succeed.
    if (this._paymentMethod === 'credit' && !customerId) {
      SubsystemApp.showToast('Credit sales require a customer (walk-in not allowed)', 'error'); return;
    }
    const btn = document.getElementById('pos-checkout-btn');
    if (btn) { btn.textContent = t('Processing…'); btn.disabled = true; }
    // Real bug fixed here: create_sale() (retail_api.py) is server-
    // authoritative on price by design (AUDIT-002/AUDIT-003) -- it computes
    // subtotal/discount_amount/tax_amount/total itself and IGNORES those
    // same top-level fields on this payload, deriving discount PER LINE
    // from each item's own discount_pct instead. _addToCart() never set
    // discount_pct on a cart item at all, so every line was silently 0%
    // regardless of what's typed in the POS discount field -- the on-screen
    // total was discounted (client-side display only, this._recalc()), but
    // the actual charge was always full price. The single POS discount
    // field means "X% off this whole sale," so the same discPct is applied
    // to every line here, matching what the cashier and customer both see
    // on screen.
    // Clamped the same way _recalc() clamps it (AUDIT-fix) -- keeps the
    // submitted per-line discount_pct consistent with what's on screen.
    // Server-side clamp_discount_pct() would catch an out-of-range value
    // regardless; this just keeps display and charge from ever disagreeing.
    const discPct = Math.min(100, Math.max(0, parseFloat(document.getElementById('pos-disc')?.value || 0)));
    const payload = {
      idempotency_key: `pos_${Date.now()}`,
      customer_id: customerId || null,
      subtotal:         this._currentTotals.subtotal,
      discount_amount:  this._currentTotals.discount,
      tax_amount:       this._currentTotals.tax,
      total,
      amount_paid:      this._paymentMethod === 'cash' ? Math.max(tendered, total) : total,
      payment_method:   this._paymentMethod,
      items: this._cart.map(i => ({ ...i, discount_pct: discPct })),
    };
    try {
      const data = await this._post('/api/sub/retail/sales', payload);
      if (data.status === 'success') {
        // Reset the cart/button FIRST so the till is ready for the next sale even
        // if the receipt modal hiccups — then show the receipt.
        this._clearCart();
        // AUDIT [launch-readiness "the POS scale fix"]: this used to call
        // _loadPOSData() here, re-fetching the ENTIRE product catalogue
        // after every single sale -- at 50,000 SKUs that's ~25MB re-sent
        // over the wire once per transaction, just to reflect a stock
        // decrement the server already applied. The sale response already
        // carries exactly what changed (`lines`: product_id + quantity per
        // line -- see create_sale's response_data in retail_api.py), so the
        // same decrement is applied locally instead of re-fetched -- see
        // _applyLocalStockDecrement. Any drift from a sale rung on ANOTHER
        // device is corrected by the next real _loadPOSData() (a category
        // switch, a search, or the next POS mount) -- and the stale-stock UI
        // (_isStockStale/_staleStockLabel, stage 7b) already dates the
        // figure once this device falls behind on sync, so a client-side
        // decrement is never presented as more certain than it actually is.
        this._applyLocalStockDecrement(data.data && data.data.lines);
        this._showReceipt(data.data);
      } else {
        SubsystemApp.showToast(data.message || 'Checkout failed', 'error');
        if (btn) { btn.textContent = this._checkoutLabel(total); btn.disabled = false; }
      }
    } catch(e) {
      // AUDIT: this used to reset the button with zero visible feedback on
      // any non-401 failure (server unreachable, a 500 with a non-JSON
      // body making _post's res.json() throw a SyntaxError, etc.) --
      // _fetch() above only throws for HTTP 401, which already shows its
      // own relogin modal via checkAuthAndSetup. Every other failure mode
      // landed here silently: the cashier watched "Processing…" flash back
      // to "Charge" with no indication of whether the sale went through,
      // whether to retry, or whether the customer's cash was recorded --
      // on the single most consequential action on this screen.
      console.error('Checkout failed:', e);
      SubsystemApp.showToast('Checkout failed — check the connection and try again', 'error');
      if (btn) { btn.textContent = this._checkoutLabel(total); btn.disabled = false; }
    }
  },

  // Wave 1B (Part P): every value below reads from `saleData` -- the
  // server's authoritative POST /api/sub/retail/sales response -- never
  // from `payload` (what the client submitted) or `this._cart` (the
  // client's pre-submit working state). Before this wave, the modal used
  // payload.total for Total and this._cart for line items, which could
  // silently diverge from what the server actually recorded (the same
  // class of bug as MOB-001 -- a client-side value standing in for the
  // authoritative one). `saleData.lines` (present on every real sale
  // response) is used for the line items instead of the client's cart.
  _showReceipt(saleData) {
    // Wave 1B security fix: stored on `this`, not interpolated into the
    // onclick attribute below -- embedding JSON.stringify(saleData) directly
    // into an inline HTML attribute is an injection risk if any field (e.g.
    // a product name an admin typed) contains a quote or HTML-special
    // character. The button now references this stored value by name only;
    // no untrusted data is ever placed inside attribute text.
    this._lastSaleData = saleData;
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:380px;text-align:center">
        <div style="font-size:52px;margin-bottom:12px">✅</div>
        <h3 style="margin:0 0 6px">Sale Complete!</h3>
        <p style="color:var(--text-muted);margin:0 0 20px">Receipt #${saleData.sale_number}</p>
        <div style="background:var(--surface-sunken);border-radius:10px;padding:16px;text-align:left;margin-bottom:20px">
          ${(saleData.lines||[]).map(i=>`<div style="display:flex;justify-content:space-between;margin-bottom:6px;font-size:13px">
            <span style="color:var(--text-secondary)">${this._esc(i.name || ('#'+i.product_id))} ×${i.quantity}</span>
            <span style="color:var(--text-money)">${this._fmt(i.line_total)}</span>
          </div>`).join('')}
          <div style="border-top:1px dashed var(--border-default);margin:10px 0;padding-top:10px">
            <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px">
              <span style="color:var(--text-secondary)">Total</span><span style="color:var(--text-money);font-weight:700">${this._fmt(saleData.total)}</span>
            </div>
            ${saleData.change > 0 ? `<div style="display:flex;justify-content:space-between;font-size:13px">
              <span style="color:var(--text-secondary)">Change</span><span style="color:var(--text-money-positive);font-weight:700">${this._fmt(saleData.change)}</span>
            </div>` : ''}
          </div>
        </div>
        <div style="display:flex;gap:10px">
          <button class="ret-btn ret-btn-ghost" style="flex:1" onclick="RetailSystem._printReceipt(RetailSystem._lastSaleData)">🖨️ Print</button>
          <button class="ret-btn ret-btn-primary" style="flex:1" onclick="this.closest('.ret-modal-overlay').remove()">New Sale</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    setTimeout(() => overlay.remove(), 8000);
  },

  // ══ RECEIPT PRINTING (Wave 1B, Part O/P) ═════════════════════════════════
  // Uses the OS print spooler via a hidden iframe + window.print() -- works
  // with any Windows-installed printer (thermal via its own driver, a
  // regular document printer, or a PDF printer), no ESC/POS protocol needed.
  // Every field is read directly from `saleData` (see _showReceipt's
  // docstring) -- this function does not compute or re-derive any total.
  _printerCfg() {
    try { return Object.assign({ paperWidth: '80mm' }, JSON.parse(localStorage.getItem('aura_printer_cfg') || '{}')); }
    catch (e) { return { paperWidth: '80mm' }; }
  },
  savePrinterCfg(cfg) { try { localStorage.setItem('aura_printer_cfg', JSON.stringify(cfg)); } catch (e) {} },

  // docs/einvoicing/phase1/ -- e-invoice submission is async (the outbox
  // worker, not the sale itself), so at the moment of printing it usually
  // hasn't cleared yet. This does ONE best-effort status check (never
  // blocks/fails printing on error) so a receipt printed after clearance
  // shows the real QR, and one printed right after checkout shows an
  // honest "pending" line instead of fabricating a QR that doesn't exist
  // yet. Entirely gated on saleData.einvoice being present -- a
  // flag-disabled install's receipt HTML is unaffected.
  async _einvoiceReceiptBlock(saleData) {
    if (!saleData.einvoice || !saleData.einvoice.invoice_ref) return '';
    try {
      const resp = await this._get('/api/einvoicing/outbox/' + encodeURIComponent(saleData.einvoice.invoice_ref));
      const entryStatus = resp?.data?.entry?.status;
      if (entryStatus === 'CLEARED') {
        const qrUrl = '/api/einvoicing/qr/' + encodeURIComponent(saleData.einvoice.invoice_ref) + '.png';
        return `
      <div class="rcpt-hr"></div>
      <div class="rcpt-center"><img src="${qrUrl}" style="width:32mm;height:auto" /></div>
      <div class="rcpt-center" style="font-size:9px">Jordan e-invoice cleared</div>`;
      }
    } catch (e) { /* best-effort only -- printing must never fail on this */ }
    return `
      <div class="rcpt-hr"></div>
      <div class="rcpt-center" style="font-size:9px">Jordan e-invoice: pending government clearance</div>`;
  },

  // Best-effort fetch of the company's receipt branding (business name,
  // address, phone, tax number, header/footer lines) -- read through its
  // OWN endpoint (GET /settings/branding), never through the general
  // settings dict tax/credit settings use. See retail_api.py's
  // _SETTINGS_BLOB_PREFIX comment for why the logo specifically must never
  // ride along on a hot settings read; this is the render-time-only path
  // that comment describes, and printing a receipt is exactly that --
  // never called from create_sale or any per-sale hot path, only from a
  // human clicking Print. A failed/unreachable fetch falls back to {}, so
  // _brandingReceiptBlock below renders the same unbranded receipt this
  // build always has.
  async _receiptBranding() {
    try {
      const resp = await this._get('/api/sub/retail/settings/branding');
      return (resp && resp.status === 'success' && resp.data) ? resp.data : {};
    } catch (e) { return {}; }
  },

  // Separate fetch, separate endpoint, fetched only when the settings call
  // above says a logo actually exists -- the whole point of keeping the
  // logo out of _settings()/branding_settings_get is that nothing pays for
  // it unless it is actually going to be drawn.
  async _receiptLogoDataUri(branding) {
    if (!branding || !branding.has_logo) return '';
    try {
      const resp = await this._get('/api/sub/retail/settings/branding/logo');
      return (resp && resp.data && resp.data.logo) || '';
    } catch (e) { return ''; }
  },

  // The identity block at the top of the receipt: logo, business name
  // (falling back to the product name so an unbranded install still prints
  // a legible receipt, never a blank header), address, phone, tax number,
  // and an optional custom header line. Every operator-entered value goes
  // through this._esc -- these are shop-typed strings landing in
  // doc.write()'d HTML, the same trust boundary this file already escapes
  // category/customer/reorder-request text for.
  _brandingReceiptBlock(branding, logoDataUri) {
    const b = branding || {};
    const rows = [];
    if (logoDataUri) {
      rows.push(`<div class="rcpt-center"><img src="${this._esc(logoDataUri)}" style="max-width:100%;max-height:20mm;margin-bottom:4px" /></div>`);
    }
    rows.push(`<div class="rcpt-center rcpt-bold">${this._esc(b.branding_business_name || 'Aura Retail')}</div>`);
    if (b.branding_address) rows.push(`<div class="rcpt-center" style="font-size:11px">${this._esc(b.branding_address)}</div>`);
    if (b.branding_phone) rows.push(`<div class="rcpt-center" style="font-size:11px">${this._esc(b.branding_phone)}</div>`);
    if (b.branding_tax_number) rows.push(`<div class="rcpt-center" style="font-size:11px">Tax #: ${this._esc(b.branding_tax_number)}</div>`);
    if (b.branding_receipt_header) rows.push(`<div class="rcpt-center" style="font-size:11px">${this._esc(b.branding_receipt_header)}</div>`);
    return rows.join('\n      ');
  },

  async _printReceipt(saleData) {
    const cfg = this._printerCfg();
    const widthMm = cfg.paperWidth === '58mm' ? 58 : 80;
    const lines = (saleData.lines || []).map(i => `
      <div class="rcpt-line">
        <span>${this._esc(i.name || ('#'+i.product_id))} ×${i.quantity}</span>
        <span>${this._fmt(i.line_total)}</span>
      </div>`).join('');
    const einvoiceBlock = await this._einvoiceReceiptBlock(saleData);
    const branding = await this._receiptBranding();
    const logoDataUri = await this._receiptLogoDataUri(branding);
    const brandingBlock = this._brandingReceiptBlock(branding, logoDataUri);
    const footerLine = branding.branding_receipt_footer
      ? `<div class="rcpt-center" style="font-size:11px">${this._esc(branding.branding_receipt_footer)}</div>` : '';
    const html = `<!doctype html><html><head><meta charset="utf-8"><title>Receipt ${saleData.sale_number}</title>
      <style>
        @page { size: ${widthMm}mm auto; margin: 2mm; }
        body { font-family: 'Courier New', monospace; width: ${widthMm}mm; margin: 0; font-size: 12px; }
        .rcpt-center { text-align: center; }
        .rcpt-line { display: flex; justify-content: space-between; }
        .rcpt-hr { border-top: 1px dashed #000; margin: 6px 0; }
        .rcpt-bold { font-weight: bold; }
      </style></head><body>
      ${brandingBlock}
      <div class="rcpt-center">Receipt #${saleData.sale_number}</div>
      <div class="rcpt-center">${saleData.created_at || new Date().toLocaleString()}</div>
      <div class="rcpt-hr"></div>
      ${lines}
      <div class="rcpt-hr"></div>
      <div class="rcpt-line"><span>Subtotal</span><span>${this._fmt(saleData.subtotal)}</span></div>
      ${saleData.discount_amount > 0 ? `<div class="rcpt-line"><span>Discount</span><span>-${this._fmt(saleData.discount_amount)}</span></div>` : ''}
      ${saleData.tax_amount > 0 ? `<div class="rcpt-line"><span>Tax</span><span>${this._fmt(saleData.tax_amount)}</span></div>` : ''}
      <div class="rcpt-line rcpt-bold"><span>Total</span><span>${this._fmt(saleData.total)}</span></div>
      <div class="rcpt-line"><span>Paid</span><span>${this._fmt(saleData.amount_paid)}</span></div>
      ${saleData.change > 0 ? `<div class="rcpt-line"><span>Change</span><span>${this._fmt(saleData.change)}</span></div>` : ''}
      <div class="rcpt-hr"></div>
      <div class="rcpt-center">Thank you</div>
      ${footerLine}
      ${einvoiceBlock}
      </body></html>`;

    let frame = document.getElementById('ret-print-frame');
    if (!frame) {
      frame = document.createElement('iframe');
      frame.id = 'ret-print-frame';
      frame.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0';
      document.body.appendChild(frame);
    }
    const doc = frame.contentWindow.document;
    doc.open(); doc.write(html); doc.close();
    // Give the iframe a tick to lay out before invoking the print dialog.
    setTimeout(() => { try { frame.contentWindow.focus(); frame.contentWindow.print(); } catch (e) {} }, 150);
  },

  // ── PRODUCTS ──────────────────────────────────────────────────────────────
  async _renderProducts(c) {
    this._injectStyles();
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">Products & Inventory</h2>
        <div style="display:flex;gap:10px">
          <input class="ret-search" id="prod-search" placeholder="Search products…" oninput="RetailSystem._filterProducts()" />
          <button class="ret-btn ret-btn-ghost" onclick="ImportWizard.open('retail','products',()=>RetailSystem._renderProducts(document.getElementById('sub-content')))">⬆ Import</button>
          <button class="sub-btn-primary" onclick="RetailSystem._openAddProduct()">+ Add Product</button>
        </div>
      </div>
      <div class="sub-chart-card">
        <div style="overflow-x:auto">
          <table class="ret-table" id="prod-table">
            <thead><tr><th>SKU</th><th>Name</th><th>Category</th><th>Cost</th><th>Price</th><th>Stock</th><th>Reorder</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody><tr><td colspan="9" style="text-align:center;color:var(--text-muted);padding:30px">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>`;
    await this._loadProducts();
  },

  async _loadProducts() {
    try {
      const [prods, cats, sups] = await Promise.all([
        this._get('/api/sub/retail/products'),
        this._get('/api/sub/retail/categories'),
        this._get('/api/sub/retail/suppliers'),
      ]);
      this._products   = prods.data || [];
      this._categories = cats.data  || [];
      this._suppliers  = sups.data  || [];
      this._renderProductTable(this._products);
    } catch(e) { console.error(e); }
  },

  _filterProducts() {
    const q = (document.getElementById('prod-search')?.value||'').toLowerCase();
    this._renderProductTable(this._products.filter(p =>
      !q || p.name.toLowerCase().includes(q) || p.sku.toLowerCase().includes(q) || (p.barcode||'').includes(q)
    ));
  },

  // Stored-XSS fix: the Stock/Delete buttons' onclick attribute used to
  // build the JS argument from p.name with only apostrophes
  // backslash-escaped (`p.name.replace(/'/g,"\\'")`), never routed through
  // this._esc() the way the name `<td>` two lines above already does. A
  // double quote in the name (e.g. from the CSV Import Wizard, which does
  // no sanitization, or from any logged-in user -- CLAUDE.md: "no real RBAC")
  // terminated the double-quoted onclick="..." attribute early, letting the
  // rest of the name plant a brand new attribute (like onmouseover=...) on
  // the <button> that the browser then executes. Matches the same
  // this._esc(name).replace(/'/g,"\\'") convention already used for
  // suppliers below, and this._esc(name) is applied again at the
  // Stock-adjust modal's title in _openStockAdjust since that's a second,
  // independent innerHTML sink for the same field.
  _renderProductTable(prods) {
    const tbody = document.querySelector('#prod-table tbody');
    if (!tbody) return;
    if (!prods.length) {
      tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--text-muted);padding:30px">No products found.</td></tr>';
      return;
    }
    // launch-readiness "product variants, wave 1": built from this._products
    // (the FULL catalogue), not `prods` (the current search/filter result)
    // -- a search that matches only a parent must still show how many of
    // ITS variants exist, even when none of them individually match.
    const parentNameById = Object.create(null);
    const variantCountByParent = Object.create(null);
    (this._products || []).forEach(x => {
      if (x.parent_product_id) {
        variantCountByParent[x.parent_product_id] = (variantCountByParent[x.parent_product_id] || 0) + 1;
      } else {
        parentNameById[x.id] = x.name;
      }
    });
    tbody.innerHTML = prods.map(p => {
      const lowStock = p.total_stock <= (p.reorder_level||0);
      const variantCount = variantCountByParent[p.id] || 0;
      const variantOfLine = p.parent_product_id
        ? `<div style="font-size:10px;color:var(--text-muted)">${this._esc(t('Variant of'))} ${this._esc(parentNameById[p.parent_product_id] || p.parent_product_id)}${p.variant_label ? ' · ' + this._esc(p.variant_label) : ''}</div>`
        : '';
      const variantCountBadge = variantCount
        ? ` <span style="font-size:10px;color:var(--text-muted)">(${variantCount} ${this._esc(t('variants'))})</span>`
        : '';
      return `<tr>
        <td style="font-family:monospace;color:var(--sub-accent)">${this._esc(p.sku)}</td>
        <td style="font-weight:600">${this._esc(p.name)}${variantCountBadge}${p.barcode?`<div style="font-size:10px;color:var(--text-muted);font-family:monospace">${this._esc(p.barcode)}</div>`:''}${variantOfLine}</td>
        <td style="color:var(--text-muted)">${p.category_name?this._esc(p.category_name):'—'}</td>
        <td>${this._fmt(p.cost_price)}</td>
        <!-- AUDIT -- these two cells carried style="color:#10b981" / "#ef4444"
             INLINE, which beats every stylesheet in the product: no token, no
             override and no :hover rule can reach an inline declaration, so
             they were the one class of colour bug fixable only at the emitting
             call site. Measured 2.1-3.1:1 on the white Products table.

             They are also the reason the literals survived: the token sweep
             reads css/main.css and the chrome sweep reads _injectStyles(), and
             an inline style is in neither.

             Sell price is MONEY, so it takes the neutral money token rather
             than a decorative green -- a green that means "this is a price"
             means nothing, and the direction rules out colour that carries no
             meaning. Stock level is a STATE, and it reuses the vocabulary the
             POS tiles already established one screen over: at-or-below reorder
             level is a WARNING (reorder now), not a danger (stockout), and it
             keeps its ⚠ prefix so the severity is not colour-only. Above the
             threshold is unremarkable, so it is simply ordinary text. -->
        <td style="font-weight:600;color:var(--text-money)">${this._fmt(p.sell_price)}</td>
        <td style="font-weight:700;color:${lowStock?'var(--state-warning-text)':'var(--text-primary)'}">${lowStock?'⚠ ':''}${p.total_stock} ${p.unit||''}</td>
        <td style="color:var(--text-muted)">${p.reorder_level||0}</td>
        <td>${this._badge('Active','green')}</td>
        <td onclick="event.stopPropagation()" style="display:flex;gap:6px">
          <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="RetailSystem._openEditProduct('${this._esc(p.id)}')">Edit</button>
          <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="RetailSystem._openStockAdjust('${this._esc(p.id)}','${this._esc(p.name).replace(/'/g,"\\'")}',${p.total_stock})">Stock</button>
          <button class="ret-btn ret-btn-danger ret-btn-sm" onclick="RetailSystem._deleteProduct('${this._esc(p.id)}','${this._esc(p.name).replace(/'/g,"\\'")}')">Delete</button>
        </td>
      </tr>`;
    }).join('');
  },

  // launch-readiness "product variants, wave 1" (design section 3.1's "no
  // grandchildren" rule): eligible parents are products that are NOT
  // themselves a variant -- offering a variant as a parent option would let
  // the modal build a request _validate_parent_product refuses anyway, so
  // it is filtered out here rather than left for the server 400 to explain.
  // `excludeId` drops the product being edited from its own parent list.
  _eligibleParentOpts(selectedParentId, excludeId) {
    return (this._products || [])
      .filter(x => !x.parent_product_id && x.id !== excludeId)
      .map(x => `<option value="${this._esc(x.id)}" ${x.id===selectedParentId?'selected':''}>${this._esc(x.name)}</option>`)
      .join('');
  },

  _openAddProduct() {
    const catOpts = this._categories.map(c => `<option value="${this._esc(c.id)}">${this._esc(c.name)}</option>`).join('');
    const supOpts = (this._suppliers||[]).map(s => `<option value="${this._esc(s.id)}">${this._esc(s.name)}</option>`).join('');
    const parentOpts = this._eligibleParentOpts(null, null);
    this._showProductModal({}, catOpts, supOpts, parentOpts);
  },

  _openEditProduct(pid) {
    const p = this._products.find(x => String(x.id) === String(pid));
    if (!p) return;
    const catOpts = this._categories.map(c =>
      `<option value="${this._esc(c.id)}" ${c.id===p.category_id?'selected':''}>${this._esc(c.name)}</option>`).join('');
    const supOpts = (this._suppliers||[]).map(s =>
      `<option value="${this._esc(s.id)}" ${s.id===p.supplier_id?'selected':''}>${this._esc(s.name)}</option>`).join('');
    const parentOpts = this._eligibleParentOpts(p.parent_product_id, p.id);
    this._showProductModal(p, catOpts, supOpts, parentOpts);
  },

  _showProductModal(p, catOpts, supOpts, parentOpts) {
    const isEdit = !!p.id;
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'ret-prod-modal';
    overlay.innerHTML = `
      <div class="ret-modal">
        <h3>${isEdit ? '✏️ Edit Product' : '➕ Add Product'}</h3>
        <div class="ret-field-row">
          <div class="ret-field"><label>Product Name *</label><input id="pm-name" value="${p.name||''}" placeholder="e.g. Blue T-Shirt" /></div>
          <div class="ret-field"><label>SKU *</label><input id="pm-sku" value="${p.sku||''}" placeholder="SKU-001" /></div>
        </div>
        <div class="ret-field-row">
          <div class="ret-field"><label>Barcode</label>
            <div style="display:flex;gap:8px">
              <input id="pm-barcode" value="${p.barcode||''}" placeholder="Type or scan…" style="flex:1" />
              <button type="button" class="ret-btn ret-btn-ghost" id="pm-scan-btn" onclick="RetailSystem._captureBarcodeField()" title="Scan barcode into this field">📷 Scan</button>
            </div>
          </div>
          <div class="ret-field"><label>Category</label><select id="pm-cat"><option value="">None</option>${catOpts}</select></div>
        </div>
        <div class="ret-field-row3">
          <div class="ret-field"><label>Cost Price</label><input type="number" id="pm-cost" value="${p.cost_price||0}" step="0.01" min="0" /></div>
          <div class="ret-field"><label>Sell Price *</label><input type="number" id="pm-sell" value="${p.sell_price||0}" step="0.01" min="0" /></div>
          <div class="ret-field"><label>Tax Rate %</label><input type="number" id="pm-tax" value="${p.tax_rate||0}" step="0.1" min="0" /></div>
        </div>
        <div class="ret-field-row3">
          <div class="ret-field"><label>${t('Supplier')}</label><select id="pm-sup"><option value="">None</option>${supOpts}</select></div>
          <div class="ret-field"><label>Unit</label>
            <select id="pm-unit">
              ${['pcs','kg','g','l','ml','box','pack','pair','m','cm'].map(u=>`<option ${p.unit===u?'selected':''}>${u}</option>`).join('')}
            </select>
          </div>
          <div class="ret-field"><label>Reorder Level</label><input type="number" id="pm-reorder" value="${p.reorder_level||5}" min="0" /></div>
        </div>
        ${!isEdit ? `<div class="ret-field"><label>Initial Stock</label><input type="number" id="pm-stock" value="0" min="0" /></div>` : ''}
        <!-- launch-readiness "product variants, wave 1" (design section 3.1):
             a variant is created by reusing THIS SAME form -- pick a parent,
             give it a label ("Red / L"), everything else (SKU, barcode,
             price, stock) is a normal product field because a variant IS a
             product. Only non-variant products are offered as a parent
             (_eligibleParentOpts) -- one level only, no grandchildren. -->
        <div class="ret-field-row">
          <div class="ret-field"><label>${this._esc(t('Variant of'))}</label>
            <select id="pm-parent"><option value="">${this._esc(t('None — a standalone product'))}</option>${parentOpts||''}</select>
          </div>
          <div class="ret-field"><label>${this._esc(t('Variant label'))}</label>
            <input id="pm-variant-label" value="${p.variant_label||''}" placeholder="${this._esc(t('e.g. Red / L'))}" />
          </div>
        </div>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('ret-prod-modal').remove()">Cancel</button>
          <button class="ret-btn ret-btn-primary" id="pm-save-btn" onclick="RetailSystem._saveProduct(${isEdit ? `'${this._esc(p.id)}'` : 'null'})">${isEdit?'Save Changes':'Add Product'}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if(e.target===overlay) overlay.remove(); });
    document.getElementById('pm-name')?.focus();
  },

  async _saveProduct(pid) {
    const name = document.getElementById('pm-name')?.value.trim();
    const sku  = document.getElementById('pm-sku')?.value.trim();
    if (!name || !sku) { SubsystemApp.showToast('Name and SKU are required','error'); return; }
    const btn = document.getElementById('pm-save-btn');
    if (btn) { btn.disabled=true; btn.textContent='Saving…'; }
    const payload = {
      name, sku,
      barcode:      document.getElementById('pm-barcode')?.value,
      category_id:  document.getElementById('pm-cat')?.value || null,
      supplier_id:  document.getElementById('pm-sup')?.value || null,
      cost_price:   +document.getElementById('pm-cost')?.value || 0,
      sell_price:   +document.getElementById('pm-sell')?.value || 0,
      tax_rate:     +document.getElementById('pm-tax')?.value  || 0,
      unit:         document.getElementById('pm-unit')?.value  || 'pcs',
      reorder_level:+document.getElementById('pm-reorder')?.value || 5,
      initial_stock:+document.getElementById('pm-stock')?.value  || 0,
      // launch-readiness "product variants, wave 1": both null when the
      // Parent field is left at "None" -- the server's own validation
      // (create_product/update_product's _validate_parent_product) is the
      // real gate; this is just intent.
      parent_product_id: document.getElementById('pm-parent')?.value || null,
      variant_label:     document.getElementById('pm-variant-label')?.value || null,
    };
    try {
      const d = pid
        ? await this._patch(`/api/sub/retail/products/${pid}`, payload)
        : await this._post('/api/sub/retail/products', payload);
      if (d.status === 'success') {
        SubsystemApp.showToast(pid ? 'Product updated' : 'Product added', 'success');
        document.getElementById('ret-prod-modal')?.remove();
        this._renderProducts(document.getElementById('sub-content'));
      } else {
        SubsystemApp.showToast(d.message||'Error','error');
        if(btn){btn.disabled=false;btn.textContent='Save';}
      }
    } catch(e) { if(btn){btn.disabled=false;btn.textContent='Save';} }
  },

  _openStockAdjust(pid, name, currentStock) {
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'ret-stock-modal';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:380px">
        <h3>📦 Adjust Stock — ${this._esc(name)}</h3>
        <p style="color:var(--text-muted);margin:0 0 20px">Current stock: <strong style="color:var(--text-primary)">${currentStock}</strong></p>
        <div class="ret-field"><label>Adjustment Quantity (+ to add, − to deduct)</label>
          <input type="number" id="sa-qty" placeholder="+10 or -5" step="1" /></div>
        <div class="ret-field"><label>Reason</label>
          <select id="sa-reason">
            <option value="Stock received">Stock received</option>
            <option value="Manual correction">Manual correction</option>
            <option value="Damaged goods">Damaged goods</option>
            <option value="Stock count">Stock count adjustment</option>
            <option value="Returned from customer">Returned from customer</option>
          </select>
        </div>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('ret-stock-modal').remove()">Cancel</button>
          <button class="ret-btn ret-btn-primary" id="sa-btn" onclick="RetailSystem._saveStockAdjust('${this._esc(pid)}')">Apply</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if(e.target===overlay) overlay.remove(); });
    document.getElementById('sa-qty')?.focus();
  },

  async _saveStockAdjust(pid) {
    const qty = parseFloat(document.getElementById('sa-qty')?.value || 0);
    const reason = document.getElementById('sa-reason')?.value || 'Manual adjustment';
    if (!qty) { SubsystemApp.showToast('Enter a quantity','error'); return; }
    const btn = document.getElementById('sa-btn');
    if (btn) { btn.disabled=true; btn.textContent='Saving…'; }
    try {
      const d = await this._post(`/api/sub/retail/products/${pid}/stock-adjust`, { quantity: qty, reason });
      if (d.status === 'success') {
        SubsystemApp.showToast(`Stock updated. New balance: ${d.new_stock}`, 'success');
        document.getElementById('ret-stock-modal')?.remove();
        this._loadProducts();
      } else {
        SubsystemApp.showToast(d.message||'Error','error');
        if(btn){btn.disabled=false;btn.textContent='Apply';}
      }
    } catch(e) { if(btn){btn.disabled=false;btn.textContent='Apply';} }
  },

  async _deleteProduct(pid, name) {
    if (!confirm(`Delete "${name}"? (The product will be deactivated, not permanently removed)`)) return;
    try {
      const d = await this._del(`/api/sub/retail/products/${pid}`);
      SubsystemApp.showToast(d.message||'Done', d.status==='success'?'success':'error');
      this._loadProducts();
    } catch(e) {}
  },

  // ── CATEGORIES ────────────────────────────────────────────────────────────
  // Symmetric desktop counterpart to what the POS category filter bar and the
  // Product modal's category dropdown already consume (this._categories) --
  // this is the first place a desktop user can create/edit/delete a category
  // rather than only doing it from the phone. Backend routes (GET/POST/PUT/
  // DELETE /api/sub/retail/categories[/<id>]) already exist and already queue
  // sync events; nothing here talks to the DB directly.
  async _renderCategories(c) {
    this._injectStyles();
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">${t('Categories')}</h2>
        <div style="display:flex;gap:10px">
          <button class="sub-btn-primary" onclick="RetailSystem._openAddCategory()">+ ${t('Add Category')}</button>
        </div>
      </div>
      <div class="sub-chart-card">
        <div style="overflow-x:auto">
          <table class="ret-table" id="cat-table">
            <thead><tr><th>${t('Category Name')}</th><th>${t('Description')}</th><th>${t('Products')}</th><th>${t('Actions')}</th></tr></thead>
            <tbody><tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:30px">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>`;
    await this._loadCategories();
  },

  async _loadCategories() {
    try {
      const data = (await this._get('/api/sub/retail/categories')).data || [];
      this._categories = data;
      const tbody = document.querySelector('#cat-table tbody');
      if (!tbody) return;
      if (!data.length) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:30px">${t('No categories found.')}</td></tr>`;
        return;
      }
      // Every interpolated value here is escaped (see this._esc): a category
      // name/description can have been authored on a DIFFERENT device and
      // relayed in, so it is untrusted input at this point. The Delete button
      // deliberately passes only the id -- _deleteCategory looks the name up
      // from this._categories itself, so no remote-authored string is ever
      // spliced into an inline event-handler attribute at all.
      tbody.innerHTML = data.map(cat => `<tr>
        <td style="font-weight:600">${this._esc(cat.name)}</td>
        <td style="color:var(--text-muted)">${cat.description ? this._esc(cat.description) : '—'}</td>
        <td style="color:var(--text-muted)">${this._esc(cat.product_count||0)}</td>
        <td>
          <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="RetailSystem._openEditCategory('${this._esc(cat.id)}')">${t('Edit')}</button>
          <button class="ret-btn ret-btn-danger ret-btn-sm" style="margin-left:6px" onclick="RetailSystem._deleteCategory('${this._esc(cat.id)}')">${t('Delete')}</button>
        </td>
      </tr>`).join('');
    } catch(e) { console.error(e); }
  },

  _openAddCategory() { this._showCategoryModal({}); },
  _openEditCategory(id) {
    const cat = this._categories.find(x => String(x.id) === String(id));
    if (cat) this._showCategoryModal(cat);
  },

  _showCategoryModal(cat) {
    const isEdit = !!cat.id;
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'ret-cat-modal';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:440px">
        <h3>${isEdit ? '✏️ '+t('Edit Category') : '🏷️ '+t('Add Category')}</h3>
        <div class="ret-field"><label>${t('Category Name')} *</label><input id="catm-name" value="${this._esc(cat.name)}" /></div>
        <div class="ret-field"><label>${t('Description')}</label><textarea id="catm-desc" rows="3">${this._esc(cat.description)}</textarea></div>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('ret-cat-modal').remove()">${t('Cancel')}</button>
          <button class="ret-btn ret-btn-primary" id="catm-btn" onclick="RetailSystem._saveCategory(${isEdit ? `'${this._esc(cat.id)}'` : 'null'})">${isEdit ? t('Save') : t('Add Category')}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if(e.target===overlay) overlay.remove(); });
    document.getElementById('catm-name')?.focus();
  },

  async _saveCategory(catId) {
    const name = document.getElementById('catm-name')?.value.trim();
    if (!name) { SubsystemApp.showToast(t('Name required'), 'error'); return; }
    const btn = document.getElementById('catm-btn');
    if (btn) { btn.disabled=true; btn.textContent='Saving…'; }
    const payload = { name, description: document.getElementById('catm-desc')?.value || '' };
    try {
      const d = catId
        ? await this._put(`/api/sub/retail/categories/${catId}`, payload)
        : await this._post('/api/sub/retail/categories', payload);
      if (d.status==='success') {
        SubsystemApp.showToast(catId ? t('Category updated') : t('Category added'), 'success');
        document.getElementById('ret-cat-modal')?.remove();
        this._loadCategories();
      } else { SubsystemApp.showToast(d.message||'Error','error'); if(btn){btn.disabled=false;btn.textContent=t('Save');} }
    } catch(e) { if(btn){btn.disabled=false;btn.textContent=t('Save');} }
  },

  // Final-review Fix 4 (2026-08-07): a failed delete must be VISIBLE. This
  // previously ended in a bare `catch(e){}` -- and `_del()` calls `.json()`,
  // which throws on the HTML body Flask's default 500 handler returns, so
  // every server-side failure landed in that empty catch: the user clicked
  // Delete, nothing happened, no error, no clue. The backend now answers with
  // a clean JSON 4xx (see delete_category), and both that and any remaining
  // transport/parse failure are surfaced as a toast here.
  async _deleteCategory(catId) {
    const cat = (this._categories || []).find(x => String(x.id) === String(catId));
    const name = (cat && cat.name) || '';
    if (!confirm(`${t('Delete')} "${name}"?`)) return;
    try {
      const d = await this._del(`/api/sub/retail/categories/${catId}`);
      if (d && d.status === 'success') {
        SubsystemApp.showToast(d.message || t('Category deleted'), 'success');
      } else {
        SubsystemApp.showToast((d && d.message) || t('Could not delete this category.'), 'error');
      }
      this._loadCategories();
    } catch(e) {
      console.error('Category delete failed', e);
      SubsystemApp.showToast(t('Could not delete this category.'), 'error');
    }
  },

  // ── CUSTOMERS ─────────────────────────────────────────────────────────────
  async _renderCustomers(c) {
    this._injectStyles();
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">Customers</h2>
        <div style="display:flex;gap:10px">
          <input class="ret-search" id="cust-search" placeholder="Search name, phone, email…" oninput="RetailSystem._filterCustomers()" />
          <button class="ret-btn ret-btn-ghost" onclick="ImportWizard.open('retail','customers',()=>RetailSystem._loadCustomers())">⬆ Import</button>
          <button class="sub-btn-primary" onclick="RetailSystem._openAddCustomer()">+ Add Customer</button>
        </div>
      </div>
      <div class="sub-chart-card">
        <div style="overflow-x:auto">
          <table class="ret-table" id="cust-table">
            <thead><tr><th>Name</th><th>Phone</th><th>Email</th><th>Loyalty Points</th><th>Total Spent</th><th>Orders</th><th>Actions</th></tr></thead>
            <tbody><tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:30px">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>`;
    await this._loadCustomers();
  },

  async _loadCustomers(q='') {
    try {
      const url = q ? `/api/sub/retail/customers?q=${encodeURIComponent(q)}` : '/api/sub/retail/customers';
      const data = (await this._get(url)).data || [];
      this._customers = data;
      const tbody = document.querySelector('#cust-table tbody');
      if (!tbody) return;
      if (!data.length) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:30px">No customers found.</td></tr>';
        return;
      }
      // Every interpolated value here is escaped (see this._esc): a customer
      // name/phone/email/address can now arrive from ANOTHER DEVICE over the
      // sync relay, which is a genuinely new trust boundary, mirroring
      // Category's Fix 7.
      tbody.innerHTML = data.map(cu => `<tr onclick="RetailSystem._viewCustomer('${this._esc(cu.id)}')">
        <td style="font-weight:600">${this._customerOpenerButton(cu.id, cu.name, cu.phone)}</td>
        <td style="color:var(--text-muted)">${cu.phone?this._esc(cu.phone):'—'}</td>
        <td style="color:var(--text-muted)">${cu.email?this._esc(cu.email):'—'}</td>
        <td><span style="color:var(--text-primary);font-weight:700">${this._esc(cu.loyalty_points||0)} pts</span></td>
        <td style="font-weight:600;color:var(--text-money-positive)">${this._fmt(cu.total_spent)}</td>
        <td style="color:var(--text-muted)">${this._esc(cu.order_count||0)}</td>
        <td onclick="event.stopPropagation()">
          <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="RetailSystem._openEditCustomer('${this._esc(cu.id)}')">Edit</button>
          <button class="ret-btn ret-btn-danger ret-btn-sm" style="margin-left:6px" onclick="RetailSystem._deleteCustomer('${this._esc(cu.id)}')">${t('Delete')}</button>
        </td>
      </tr>`).join('');
    } catch(e) { console.error(e); }
  },

  _filterCustomers() {
    clearTimeout(this._custTimer);
    this._custTimer = setTimeout(() => this._loadCustomers(document.getElementById('cust-search')?.value||''), 350);
  },

  _openAddCustomer() { this._showCustomerModal({}); },
  _openEditCustomer(id) {
    const cu = this._customers.find(x=>String(x.id)===String(id));
    if (cu) this._showCustomerModal(cu);
  },

  // Escape name/phone/email/address here too, same reasoning as the Fix 7
  // comment on _loadCustomers above: this record can arrive from ANOTHER
  // DEVICE over the sync relay. The table listing already escaped these
  // fields via this._esc(), but the Edit modal was rebuilding its inputs
  // from the raw `cu` object -- so a customer named `<img src=x onerror=...>`
  // rendered safely in the table but executed the moment its own row's
  // Edit/View was opened (by any user/device, including the one that
  // created it). Same class of bug as Fix 7, just missed on this path.
  _showCustomerModal(cu) {
    const isEdit = !!cu.id;
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'ret-cust-modal';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:440px">
        <h3>${isEdit?'✏️ Edit Customer':'👤 Add Customer'}</h3>
        <div class="ret-field"><label>Full Name *</label><input id="cm-name" value="${this._esc(cu.name||'')}" /></div>
        <div class="ret-field-row">
          <div class="ret-field"><label>Phone</label><input id="cm-phone" value="${this._esc(cu.phone||'')}" /></div>
          <div class="ret-field"><label>Email</label><input type="email" id="cm-email" value="${this._esc(cu.email||'')}" /></div>
        </div>
        <div class="ret-field"><label>Address</label><input id="cm-addr" value="${this._esc(cu.address||'')}" /></div>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('ret-cust-modal').remove()">Cancel</button>
          <button class="ret-btn ret-btn-primary" id="cm-btn" onclick="RetailSystem._saveCustomer(${cu.id ? `'${this._esc(cu.id)}'` : 'null'})">${isEdit?'Save':'Add Customer'}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if(e.target===overlay) overlay.remove(); });
    document.getElementById('cm-name')?.focus();
  },

  async _saveCustomer(cid) {
    const name = document.getElementById('cm-name')?.value.trim();
    if (!name) { SubsystemApp.showToast('Name required','error'); return; }
    const btn = document.getElementById('cm-btn');
    if (btn) { btn.disabled=true; btn.textContent='Saving…'; }
    const payload = { name, phone:document.getElementById('cm-phone')?.value||'',
                      email:document.getElementById('cm-email')?.value||'',
                      address:document.getElementById('cm-addr')?.value||'' };
    try {
      const d = cid
        ? await this._patch(`/api/sub/retail/customers/${cid}`, payload)
        : await this._post('/api/sub/retail/customers', payload);
      if (d.status==='success') {
        SubsystemApp.showToast(cid?'Customer updated':'Customer added','success');
        document.getElementById('ret-cust-modal')?.remove();
        this._loadCustomers();
      } else { SubsystemApp.showToast(d.message||'Error','error'); if(btn){btn.disabled=false;btn.textContent='Save';} }
    } catch(e) { if(btn){btn.disabled=false;btn.textContent='Save';} }
  },

  // Mirrors _deleteCategory exactly (see its comment for why: a failed
  // delete must be VISIBLE, not a silent no-op).
  async _deleteCustomer(custId) {
    const cu = (this._customers || []).find(x => String(x.id) === String(custId));
    const name = (cu && cu.name) || '';
    if (!confirm(`${t('Delete')} "${name}"?`)) return;
    try {
      const d = await this._del(`/api/sub/retail/customers/${custId}`);
      if (d && d.status === 'success') {
        SubsystemApp.showToast(d.message || t('Customer deleted'), 'success');
      } else {
        SubsystemApp.showToast((d && d.message) || t('Could not delete this customer.'), 'error');
      }
      this._loadCustomers();
    } catch(e) {
      console.error('Customer delete failed', e);
      SubsystemApp.showToast(t('Could not delete this customer.'), 'error');
    }
  },

  // Same escaping gap as _showCustomerModal above: cu.name/cu.phone were
  // going into innerHTML unescaped in this detail view too.
  async _viewCustomer(cid) {
    const cu = this._customers.find(x=>String(x.id)===String(cid));
    if (!cu) return;
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.innerHTML = `
      <div class="ret-modal ret-modal-wide">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
          <h3 style="margin:0">${this._esc(cu.name)}</h3>
          <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="this.closest('.ret-modal-overlay').remove()">✕ Close</button>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:14px;margin-bottom:20px">
          <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;text-align:center">
            <div style="color:var(--text-muted);font-size:11px;text-transform:uppercase;margin-bottom:6px">Total Spent</div>
            <div style="color:var(--text-money);font-size:22px;font-weight:700">${this._fmt(cu.total_spent)}</div>
          </div>
          <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;text-align:center">
            <div style="color:var(--text-muted);font-size:11px;text-transform:uppercase;margin-bottom:6px">Orders</div>
            <div style="color:var(--text-primary);font-size:22px;font-weight:700">${cu.order_count||0}</div>
          </div>
          <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;text-align:center">
            <div style="color:var(--text-muted);font-size:11px;text-transform:uppercase;margin-bottom:6px">Loyalty Points</div>
            <div style="color:var(--text-primary);font-size:22px;font-weight:700">${cu.loyalty_points||0}</div>
          </div>
          <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;text-align:center">
            <div style="color:var(--text-muted);font-size:11px;text-transform:uppercase;margin-bottom:6px">Phone</div>
            <div style="color:var(--text-primary);font-size:16px;font-weight:600">${cu.phone?this._esc(cu.phone):'—'}</div>
          </div>
        </div>
        <div class="sub-chart-title" style="margin-bottom:12px">Purchase History</div>
        <div id="cu-hist-loading" style="color:var(--text-muted);text-align:center;padding:20px">Loading…</div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if(e.target===overlay) overlay.remove(); });
    try {
      const hist = (await this._get(`/api/sub/retail/customers/${cid}/sales`)).data || [];
      const el = document.getElementById('cu-hist-loading');
      if (!el) return;
      if (!hist.length) { el.textContent = 'No purchases yet.'; return; }
      el.outerHTML = `<table class="ret-table">
        <thead><tr><th>Receipt #</th><th>Items</th><th>Method</th><th>Total</th><th>Date</th></tr></thead>
        <tbody>${hist.map(s=>`<tr style="cursor:pointer" onclick="RetailSystem._viewSale(${s.id})" title="${this._esc(t('View invoice'))}">
          <td style="font-family:monospace;color:var(--sub-accent)">${this._saleOpenerButton(s.id, s.sale_number)}</td>
          <td>${s.items||0}</td>
          <td>${this._badge(s.payment_method,'blue')}</td>
          <td style="font-weight:700">${this._money(s.total)}</td>
          <!-- Same unisolated two-number run as the dashboard's Date column;
               see the long note there. -->
          <td style="color:var(--text-muted)">${this._bdi((s.created_at||'').slice(0,16))}</td>
        </tr>`).join('')}</tbody>
      </table>`;
    } catch(e) {}
  },

  // ── PROMOTIONS (ROADMAP.md "retail schema v23", promotions wave 1) ────────
  //
  // CRUD against the frozen contract: GET/POST /api/sub/retail/promotions,
  // PATCH/DELETE /api/sub/retail/promotions/<id> (DELETE deactivates, it does
  // not remove the row -- see _deletePromotion). Structure mirrors
  // _renderCategories/_loadCategories/_showCategoryModal/_saveCategory/
  // _deleteCategory above: same table shell, same modal-overlay pattern, same
  // save/delete error handling. Deliberately NOT the same array as the POS's
  // own `this._promotions` (the /promotions/active cache _loadPOSData reads
  // for the checkout preview) -- this screen lists EVERY promotion, active or
  // not, and overwriting the till's active-only cache with that would let an
  // expired or inactive rule silently apply again the next time a cashier's
  // cart re-renders while this admin screen happens to be open on another
  // tab/session of the same device.
  async _renderPromotions(c) {
    this._injectStyles();
    // Capability gate, same mechanism and reasoning as _renderReports above --
    // read that comment first. retail.discount is CAP_DISCOUNT
    // (commercial_runtime/identity/user_accounts.py), the SAME code a manual
    // discount at checkout already requires -- reused rather than minted,
    // because configuring a standing discount is the same authority as typing
    // one in by hand. Hiding the nav entry (app-shell.js) is not the
    // enforcement; this guard is the second, real one, for the same reason
    // AuraRouter can replay this section from the URL hash with no nav click
    // in between.
    if (window.SubsystemApp && !SubsystemApp.hasCapability('retail.discount')) {
      return this._renderPromotionsRestricted(c);
    }
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">${t('Promotions')}</h2>
        <div style="display:flex;gap:10px">
          <button class="sub-btn-primary" onclick="RetailSystem._openAddPromotion()">+ ${t('Add Promotion')}</button>
        </div>
      </div>
      <div class="sub-chart-card">
        <div style="overflow-x:auto">
          <table class="ret-table" id="prm-table">
            <thead><tr>
              <th>${t('Name')}</th><th>${t('Discount %')}</th><th>${t('Applies To')}</th>
              <th>${t('Branch')}</th><th>${t('Status')}</th><th>${t('Actions')}</th>
            </tr></thead>
            <tbody><tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:30px">${t('Loading…')}</td></tr></tbody>
          </table>
        </div>
      </div>`;
    await this._loadPromotions();
  },

  // What a user without `retail.discount` gets instead of a screen every
  // button on which would 403. Same shape/wording pattern as
  // _renderReportsRestricted above, via the shared _renderCapabilityRestricted.
  _renderPromotionsRestricted(c) {
    this._renderCapabilityRestricted(c, {
      icon: '🎁',
      title: t('Promotions'),
      message: t('Configuring promotions is limited to managers and the store owner. Open the till to start ringing sales.'),
    });
  },

  async _loadPromotions() {
    try {
      const [promos, prods, cats, branches] = await Promise.all([
        this._get('/api/sub/retail/promotions'),
        this._get('/api/sub/retail/products'),
        this._get('/api/sub/retail/categories'),
        this._get('/api/sub/retail/branches').catch(() => null),
      ]);
      // `_promotionsList`, not `_promotions` -- see the block comment above
      // _renderPromotions for why the two must never be the same array.
      this._promotionsList  = promos.data || [];
      this._promoProducts   = prods.data  || [];
      this._promoCategories = cats.data   || [];
      this._promoBranches   = (branches && branches.data) || [];
      const tbody = document.querySelector('#prm-table tbody');
      if (!tbody) return;
      if (!this._promotionsList.length) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:30px">${t('No promotions found.')}</td></tr>`;
        return;
      }
      // Every interpolated value is escaped (see this._esc) -- a promotion
      // name can have been authored on a DIFFERENT device and relayed in via
      // sync, the same untrusted-input reasoning _loadCategories states for
      // category names. Edit/Deactivate pass only the id; the row's own data
      // is looked up back out of this._promotionsList, so no remote-authored
      // string is ever spliced into an inline event-handler attribute.
      tbody.innerHTML = this._promotionsList.map(p => {
        const prod = this._promoProducts.find(x => String(x.id) === String(p.product_id));
        const cat  = this._promoCategories.find(x => String(x.id) === String(p.category_id));
        const target = prod ? prod.name : (cat ? cat.name : '—');
        const branch = this._promoBranches.find(x => String(x.id) === String(p.branch_id));
        const isActive = p.status === 'active';
        return `<tr>
          <td style="font-weight:600">${this._esc(p.name)}</td>
          <td style="font-weight:700;color:var(--text-money)">${this._esc(p.discount_pct)}%</td>
          <td style="color:var(--text-muted)">${this._esc(target)}</td>
          <td style="color:var(--text-muted)">${branch ? this._esc(branch.name) : t('All branches')}</td>
          <td>${this._badge(isActive ? t('Active') : t('Inactive'), isActive ? 'green' : 'red')}</td>
          <td>
            <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="RetailSystem._openEditPromotion('${this._esc(p.id)}')">${t('Edit')}</button>
            <button class="ret-btn ret-btn-danger ret-btn-sm" style="margin-left:6px" onclick="RetailSystem._deletePromotion('${this._esc(p.id)}')">${t('Deactivate')}</button>
          </td>
        </tr>`;
      }).join('');
    } catch(e) { console.error(e); }
  },

  _openAddPromotion() {
    const prodOpts = (this._promoProducts||[]).map(x => `<option value="${this._esc(x.id)}">${this._esc(x.name)}</option>`).join('');
    const catOpts  = (this._promoCategories||[]).map(x => `<option value="${this._esc(x.id)}">${this._esc(x.name)}</option>`).join('');
    const branchOpts = (this._promoBranches||[]).map(x => `<option value="${this._esc(x.id)}">${this._esc(x.name)}</option>`).join('');
    this._showPromotionModal({}, prodOpts, catOpts, branchOpts);
  },

  _openEditPromotion(id) {
    const promo = (this._promotionsList||[]).find(x => String(x.id) === String(id));
    if (!promo) return;
    const prodOpts = (this._promoProducts||[]).map(x =>
      `<option value="${this._esc(x.id)}" ${String(x.id)===String(promo.product_id)?'selected':''}>${this._esc(x.name)}</option>`).join('');
    const catOpts  = (this._promoCategories||[]).map(x =>
      `<option value="${this._esc(x.id)}" ${String(x.id)===String(promo.category_id)?'selected':''}>${this._esc(x.name)}</option>`).join('');
    const branchOpts = (this._promoBranches||[]).map(x =>
      `<option value="${this._esc(x.id)}" ${String(x.id)===String(promo.branch_id)?'selected':''}>${this._esc(x.name)}</option>`).join('');
    this._showPromotionModal(promo, prodOpts, catOpts, branchOpts);
  },

  // Shows/hides the product-vs-category field pair when the radio toggle
  // changes -- the frozen contract is product_id XOR category_id, so the form
  // never lets both selects be live at once. See _savePromotion, which reads
  // ONLY the visible one via the checked radio, not both selects' values.
  _togglePromoTarget(kind) {
    const prodEl = document.getElementById('prm-target-product');
    const catEl  = document.getElementById('prm-target-category');
    if (prodEl) prodEl.style.display = kind === 'product'  ? '' : 'none';
    if (catEl)  catEl.style.display  = kind === 'category' ? '' : 'none';
  },

  _showPromotionModal(promo, prodOpts, catOpts, branchOpts) {
    const isEdit = !!promo.id;
    // A promotion editing FROM a category default shows the category picker
    // first; everything else (including a brand-new Add) defaults to product,
    // matching product_id being the first-listed field in the frozen contract.
    const startsCategory = promo.category_id != null;
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'ret-prm-modal';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:480px">
        <h3>${isEdit ? '✏️ '+t('Edit Promotion') : '🎁 '+t('Add Promotion')}</h3>
        <div class="ret-field-row">
          <div class="ret-field"><label>${t('Promotion Name')} *</label><input id="prm-name" value="${this._esc(promo.name||'')}" /></div>
          <div class="ret-field"><label>${t('Discount %')} *</label><input type="number" id="prm-disc" value="${promo.discount_pct||0}" min="0" max="100" step="0.1" /></div>
        </div>
        <div class="ret-field">
          <label>${t('Applies To')} *</label>
          <div style="display:flex;gap:16px;margin-block-start:4px">
            <label style="display:flex;align-items:center;gap:6px;font-weight:400">
              <input type="radio" name="prm-target-type" value="product" ${startsCategory?'':'checked'} onchange="RetailSystem._togglePromoTarget('product')" /> ${t('Product')}
            </label>
            <label style="display:flex;align-items:center;gap:6px;font-weight:400">
              <input type="radio" name="prm-target-type" value="category" ${startsCategory?'checked':''} onchange="RetailSystem._togglePromoTarget('category')" /> ${t('Category')}
            </label>
          </div>
        </div>
        <div class="ret-field" id="prm-target-product" style="${startsCategory?'display:none':''}">
          <label>${t('Product')}</label><select id="prm-product"><option value="">${t('None')}</option>${prodOpts}</select>
        </div>
        <div class="ret-field" id="prm-target-category" style="${startsCategory?'':'display:none'}">
          <label>${t('Category')}</label><select id="prm-category"><option value="">${t('None')}</option>${catOpts}</select>
        </div>
        <div class="ret-field"><label>${t('Branch')}</label><select id="prm-branch"><option value="">${t('All branches')}</option>${branchOpts}</select></div>
        <div class="ret-field-row">
          <div class="ret-field"><label>${t('Start Date')}</label><input type="date" id="prm-start" value="${promo.starts_at ? this._esc(String(promo.starts_at).slice(0,10)) : ''}" /></div>
          <div class="ret-field"><label>${t('End Date')}</label><input type="date" id="prm-end" value="${promo.ends_at ? this._esc(String(promo.ends_at).slice(0,10)) : ''}" /></div>
        </div>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('ret-prm-modal').remove()">${t('Cancel')}</button>
          <button class="ret-btn ret-btn-primary" id="prm-save-btn" onclick="RetailSystem._savePromotion(${isEdit ? `'${this._esc(promo.id)}'` : 'null'})">${isEdit ? t('Save') : t('Add Promotion')}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if(e.target===overlay) overlay.remove(); });
    document.getElementById('prm-name')?.focus();
  },

  async _savePromotion(pid) {
    const name = document.getElementById('prm-name')?.value.trim();
    if (!name) { SubsystemApp.showToast(t('Name required'), 'error'); return; }
    const discount_pct = Math.min(100, Math.max(0, +document.getElementById('prm-disc')?.value || 0));
    const targetType = document.querySelector('input[name="prm-target-type"]:checked')?.value || 'product';
    // Frozen contract: product_id XOR category_id. Reading only the RADIO-
    // selected field (not both selects' raw values) is what keeps that
    // exclusive even if the hidden select still carries a stale selection
    // from before the cashier toggled the radio.
    const productId  = targetType === 'product'  ? (document.getElementById('prm-product')?.value  || '') : '';
    const categoryId = targetType === 'category' ? (document.getElementById('prm-category')?.value || '') : '';
    if (!productId && !categoryId) {
      SubsystemApp.showToast(t('Choose a product or a category'), 'error');
      return;
    }
    const btn = document.getElementById('prm-save-btn');
    if (btn) { btn.disabled = true; btn.textContent = t('Saving…'); }
    const payload = {
      name, discount_pct,
      product_id:  productId  || null,
      category_id: categoryId || null,
      branch_id:   document.getElementById('prm-branch')?.value || null,
      starts_at:   document.getElementById('prm-start')?.value  || null,
      ends_at:     document.getElementById('prm-end')?.value    || null,
    };
    try {
      const d = pid
        ? await this._patch(`/api/sub/retail/promotions/${pid}`, payload)
        : await this._post('/api/sub/retail/promotions', payload);
      if (d.status === 'success') {
        SubsystemApp.showToast(pid ? t('Promotion updated') : t('Promotion added'), 'success');
        document.getElementById('ret-prm-modal')?.remove();
        this._loadPromotions();
      } else {
        SubsystemApp.showToast(d.message || t('Error'), 'error');
        if (btn) { btn.disabled = false; btn.textContent = t('Save'); }
      }
    } catch(e) { if (btn) { btn.disabled = false; btn.textContent = t('Save'); } }
  },

  // The frozen contract's DELETE deactivates -- it does not remove the row
  // (see the block comment above _renderPromotions) -- so this confirms and
  // labels the action as what it actually does, rather than reusing
  // _deleteCategory's "Delete" wording for a route that does something else.
  async _deletePromotion(pid) {
    const promo = (this._promotionsList || []).find(x => String(x.id) === String(pid));
    const name = (promo && promo.name) || '';
    if (!confirm(`${t('Deactivate')} "${name}"?`)) return;
    try {
      const d = await this._del(`/api/sub/retail/promotions/${pid}`);
      if (d && d.status === 'success') {
        SubsystemApp.showToast(d.message || t('Promotion deactivated'), 'success');
      } else {
        SubsystemApp.showToast((d && d.message) || t('Could not deactivate this promotion.'), 'error');
      }
      this._loadPromotions();
    } catch(e) {
      console.error('Promotion deactivate failed', e);
      SubsystemApp.showToast(t('Could not deactivate this promotion.'), 'error');
    }
  },

  // ── SUPPLIERS ─────────────────────────────────────────────────────────────
  async _renderSuppliers(c) {
    this._injectStyles();
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">Suppliers</h2>
        <div style="display:flex;gap:10px">
          <button class="ret-btn ret-btn-ghost" onclick="ImportWizard.open('retail','suppliers',()=>RetailSystem._loadSuppliers())">⬆ Import</button>
          <button class="sub-btn-primary" onclick="RetailSystem._openAddSupplier()">+ Add Supplier</button>
        </div>
      </div>
      <div class="sub-chart-card">
        <div style="overflow-x:auto">
          <table class="ret-table" id="sup-table">
            <thead><tr><th>Name</th><th>Phone</th><th>Email</th><th>Address</th><th>Orders</th><th>Actions</th></tr></thead>
            <tbody><tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:30px">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>`;
    await this._loadSuppliers();
  },

  async _loadSuppliers() {
    try {
      const data = (await this._get('/api/sub/retail/suppliers')).data || [];
      this._suppliers = data;
      const tbody = document.querySelector('#sup-table tbody');
      if (!tbody) return;
      if (!data.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:30px">No suppliers yet.</td></tr>';
        return;
      }
      // Every interpolated value here is escaped (see this._esc): a supplier
      // name/phone/email/address can now arrive from ANOTHER DEVICE over the
      // sync relay, mirroring Category/Customer's fix. supplier ids are real
      // client-generated UUIDs (not autoincrement ints), so every id must be
      // quoted, never spliced in as a bareword.
      tbody.innerHTML = data.map(s => `<tr>
        <td style="font-weight:600">${this._esc(s.name)}</td>
        <td>${s.phone?this._esc(s.phone):'—'}</td>
        <td>${s.email?this._esc(s.email):'—'}</td>
        <td style="color:var(--text-muted)">${s.address?this._esc(s.address):'—'}</td>
        <td>${this._esc(s.order_count||0)}</td>
        <td>
          <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="RetailSystem._openEditSupplier('${this._esc(s.id)}','${this._esc(s.name.replace(/'/g,"\\'"))}','${this._esc(s.phone||'')}','${this._esc(s.email||'')}','${this._esc(s.address||'')}')">Edit</button>
          <button class="ret-btn ret-btn-danger ret-btn-sm" style="margin-left:6px" onclick="RetailSystem._deleteSupplier('${this._esc(s.id)}','${this._esc(s.name.replace(/'/g,"\\'"))}')">${t('Delete')}</button>
          <button class="ret-btn ret-btn-primary ret-btn-sm" style="margin-left:6px" onclick="RetailSystem._openCreatePO('${this._esc(s.id)}','${this._esc(s.name.replace(/'/g,"\\'"))}')">+ PO</button>
        </td>
      </tr>`).join('');
    } catch(e) { console.error(e); }
  },

  _openAddSupplier() { this._showSupplierModal({}); },
  _openEditSupplier(id, name, phone, email, address) { this._showSupplierModal({id,name,phone,email,address}); },

  // isEdit gets a "Contacts" tab alongside "Details" -- a brand-new supplier
  // (no id yet) can't have contacts (supplier_contacts.supplier_id is
  // NOT NULL, references suppliers(id)), so Add Supplier stays the plain
  // single-panel form it always was; only Edit Supplier grows tabs. Details
  // panel below is otherwise byte-for-byte the pre-existing form (same
  // ids/fields) just moved inside #sm-panel-details.
  //
  // name/phone/email/address are escaped via this._esc() here for the same
  // reason as the Fix 7 / _showCustomerModal comment above: the supplier
  // table listing (_loadSuppliers) already escapes these fields, but this
  // modal was rebuilding its inputs from the raw `s` object -- so a supplier
  // named `<img src=x onerror=...>` rendered safely in the table but
  // executed the moment its own row's Edit was opened.
  _showSupplierModal(s) {
    const isEdit = !!s.id;
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'ret-sup-modal';
    overlay.innerHTML = `
      <div class="ret-modal ${isEdit ? 'ret-modal-wide' : ''}" ${isEdit ? '' : 'style="width:440px"'}>
        <h3>${isEdit?'✏️ Edit Supplier':'🏭 Add Supplier'}</h3>
        ${isEdit ? `
        <div class="ret-tabs">
          <button type="button" class="ret-tab active" id="sm-tab-details" onclick="RetailSystem._switchSupplierTab('details')">${t('Details')}</button>
          <button type="button" class="ret-tab" id="sm-tab-contacts" onclick="RetailSystem._switchSupplierTab('contacts')">${t('Contacts')}</button>
        </div>` : ''}
        <div id="sm-panel-details">
          <div class="ret-field"><label>Company Name *</label><input id="sm-name" value="${this._esc(s.name||'')}" /></div>
          <div class="ret-field-row">
            <div class="ret-field"><label>Phone</label><input id="sm-phone" value="${this._esc(s.phone||'')}" /></div>
            <div class="ret-field"><label>Email</label><input id="sm-email" value="${this._esc(s.email||'')}" /></div>
          </div>
          <div class="ret-field"><label>Address</label><input id="sm-addr" value="${this._esc(s.address||'')}" /></div>
          <div class="ret-modal-footer">
            <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('ret-sup-modal').remove()">Cancel</button>
            <button class="ret-btn ret-btn-primary" id="sm-btn" onclick="RetailSystem._saveSupplier(${s.id ? `'${this._esc(s.id)}'` : 'null'})">${isEdit?'Save':'Add Supplier'}</button>
          </div>
        </div>
        ${isEdit ? `
        <div id="sm-panel-contacts" style="display:none">
          <div id="sup-contacts-list"><p style="color:var(--text-muted)">${t('Loading…')}</p></div>
          <div id="sup-contact-form" style="display:none"></div>
          <div class="ret-modal-footer">
            <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('ret-sup-modal').remove()">${t('Cancel')}</button>
            <button class="ret-btn ret-btn-primary" onclick="RetailSystem._openAddSupplierContact('${this._esc(s.id)}')">+ ${t('Add Contact')}</button>
          </div>
        </div>` : ''}
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if(e.target===overlay) overlay.remove(); });
    document.getElementById('sm-name')?.focus();
    if (isEdit) this._loadSupplierContacts(s.id);
  },

  _switchSupplierTab(tab) {
    const details  = document.getElementById('sm-panel-details');
    const contacts = document.getElementById('sm-panel-contacts');
    if (!details || !contacts) return;
    const showContacts = tab === 'contacts';
    details.style.display  = showContacts ? 'none' : 'block';
    contacts.style.display = showContacts ? 'block' : 'none';
    document.getElementById('sm-tab-details')?.classList.toggle('active', !showContacts);
    document.getElementById('sm-tab-contacts')?.classList.toggle('active', showContacts);
  },

  // ── Supplier Contacts tab (schema v6 supplier_contacts, PO-split-preview
  // foundation) -- CRUD against /api/sub/retail/suppliers/<sid>/contacts.
  // list_supplier_contacts in retail_api.py deliberately returns EVERY
  // contact regardless of status (no `AND status='active'` filter -- see
  // that route's comment: soft-deleted rows may still be referenced by past
  // split-preview audit trails later). So "active list" is a CLIENT-side
  // filter here, not a server one -- that's what makes a DELETE (soft-
  // delete to status='inactive') visibly disappear from this list without
  // ever hard-deleting the row.
  async _loadSupplierContacts(sid) {
    const listEl = document.getElementById('sup-contacts-list');
    try {
      const d = await this._get(`/api/sub/retail/suppliers/${sid}/contacts`);
      this._supplierContacts = d.data || [];
      this._renderSupplierContactsList(sid);
    } catch (e) {
      if (listEl) listEl.innerHTML = `<p style="color:var(--text-muted)">${t('Error')}</p>`;
    }
  },

  _renderSupplierContactsList(sid) {
    const listEl = document.getElementById('sup-contacts-list');
    if (!listEl) return;
    const active = (this._supplierContacts || []).filter(c => c.status !== 'inactive');
    if (!active.length) {
      listEl.innerHTML = `<p style="color:var(--text-muted)">${t('No contacts yet.')}</p>`;
      return;
    }
    const roleLabels = { orders: t('Orders'), accounts: t('Accounts'), general: t('General') };
    listEl.innerHTML = `<table class="ret-table">
      <thead><tr><th>${t('Name')}</th><th>${t('Role')}</th><th>${t('Channel')}</th><th>${t('Actions')}</th></tr></thead>
      <tbody>${active.map(c => `<tr>
        <td style="font-weight:600">${this._esc(c.name)}${c.is_primary ? ` ${this._badge(t('Primary'),'green')}` : ''}</td>
        <td>${this._esc(roleLabels[c.role] || c.role)}</td>
        <td style="color:var(--text-muted)">${this._esc(c.channel_preference||'—')}<div style="font-size:11px">${this._esc(c.email||c.phone||c.whatsapp||'—')}</div></td>
        <td>
          <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="RetailSystem._openEditSupplierContact('${this._esc(sid)}','${this._esc(c.id)}')">${t('Edit')}</button>
          <button class="ret-btn ret-btn-danger ret-btn-sm" style="margin-left:6px" onclick="RetailSystem._deleteSupplierContact('${this._esc(sid)}','${this._esc(c.id)}','${this._esc(c.name.replace(/'/g,"\\'"))}')">${t('Delete')}</button>
        </td>
      </tr>`).join('')}</tbody>
    </table>`;
  },

  _openAddSupplierContact(sid) { this._showSupplierContactForm(sid, {}); },

  _openEditSupplierContact(sid, contactId) {
    const c = (this._supplierContacts || []).find(x => String(x.id) === String(contactId));
    if (!c) return;
    this._showSupplierContactForm(sid, c);
  },

  _showSupplierContactForm(sid, c) {
    const isEdit = !!c.id;
    const formEl = document.getElementById('sup-contact-form');
    if (!formEl) return;
    formEl.style.display = 'block';
    formEl.innerHTML = `
      <div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border-hairline)">
        <div style="color:var(--text-primary);font-weight:600;margin-bottom:10px">${isEdit ? t('Edit Contact') : t('Add Contact')}</div>
        <div class="ret-field-row">
          <div class="ret-field"><label>${t('Name')} *</label><input id="scf-name" value="${this._esc(c.name||'')}" /></div>
          <div class="ret-field"><label>${t('Role')}</label>
            <select id="scf-role">
              <option value="orders" ${!c.role||c.role==='orders'?'selected':''}>${t('Orders')}</option>
              <option value="accounts" ${c.role==='accounts'?'selected':''}>${t('Accounts')}</option>
              <option value="general" ${c.role==='general'?'selected':''}>${t('General')}</option>
            </select>
          </div>
        </div>
        <div class="ret-field-row">
          <div class="ret-field"><label>${t('Email')}</label><input id="scf-email" value="${this._esc(c.email||'')}" /></div>
          <div class="ret-field"><label>${t('Phone')}</label><input id="scf-phone" value="${this._esc(c.phone||'')}" /></div>
        </div>
        <div class="ret-field-row">
          <div class="ret-field"><label>${t('WhatsApp')}</label><input id="scf-whatsapp" value="${this._esc(c.whatsapp||'')}" placeholder="+962791234567" /></div>
          <div class="ret-field"><label>${t('Preferred Channel')}</label>
            <select id="scf-channel">
              <option value="whatsapp" ${(!c.channel_preference||c.channel_preference==='whatsapp')?'selected':''}>${t('WhatsApp')}</option>
              <option value="email" ${c.channel_preference==='email'?'selected':''}>${t('Email')}</option>
              <option value="phone" ${c.channel_preference==='phone'?'selected':''}>${t('Phone')}</option>
            </select>
          </div>
        </div>
        <div class="ret-field" style="display:flex;align-items:center;gap:8px">
          <input type="checkbox" id="scf-primary" ${c.is_primary?'checked':''} style="width:auto" />
          <label style="margin:0;text-transform:none;font-size:13px;color:var(--text-primary)" for="scf-primary">${t('Primary contact for this role')}</label>
        </div>
        <div class="ret-modal-footer" style="margin-top:10px">
          <button class="ret-btn ret-btn-ghost" onclick="RetailSystem._closeSupplierContactForm()">${t('Cancel')}</button>
          <button class="ret-btn ret-btn-primary" id="scf-save-btn" onclick="RetailSystem._saveSupplierContact('${this._esc(sid)}',${isEdit?`'${this._esc(c.id)}'`:'null'})">${isEdit?t('Save'):t('Add Contact')}</button>
        </div>
      </div>`;
    document.getElementById('scf-name')?.focus();
  },

  _closeSupplierContactForm() {
    const formEl = document.getElementById('sup-contact-form');
    if (formEl) { formEl.style.display = 'none'; formEl.innerHTML = ''; }
  },

  async _saveSupplierContact(sid, contactId) {
    const name = document.getElementById('scf-name')?.value.trim();
    if (!name) { SubsystemApp.showToast(t('Contact name required'),'error'); return; }
    const payload = {
      name,
      role: document.getElementById('scf-role')?.value || 'orders',
      email: document.getElementById('scf-email')?.value || '',
      phone: document.getElementById('scf-phone')?.value || '',
      whatsapp: document.getElementById('scf-whatsapp')?.value || '',
      channel_preference: document.getElementById('scf-channel')?.value || 'whatsapp',
      is_primary: !!document.getElementById('scf-primary')?.checked,
    };
    const btn = document.getElementById('scf-save-btn');
    if (btn) { btn.disabled = true; btn.textContent = t('Saving…'); }
    try {
      const d = contactId
        ? await this._patch(`/api/sub/retail/suppliers/${sid}/contacts/${contactId}`, payload)
        : await this._post(`/api/sub/retail/suppliers/${sid}/contacts`, payload);
      if (d.status === 'success') {
        SubsystemApp.showToast(contactId ? t('Contact updated') : t('Contact added'),'success');
        this._closeSupplierContactForm();
        await this._loadSupplierContacts(sid);
      } else {
        SubsystemApp.showToast(d.message || t('Error'),'error');
        if (btn) { btn.disabled=false; btn.textContent=t('Save'); }
      }
    } catch (e) { if (btn) { btn.disabled=false; btn.textContent=t('Save'); } }
  },

  // Mirrors _deleteSupplier's confirm-then-call-then-always-refresh shape --
  // a failed delete must be VISIBLE, not a silent no-op (same reasoning as
  // _deleteCategory/_deleteCustomer/_deleteSupplier).
  async _deleteSupplierContact(sid, contactId, name) {
    if (!confirm(`${t('Delete')} "${name}"?`)) return;
    try {
      const d = await this._del(`/api/sub/retail/suppliers/${sid}/contacts/${contactId}`);
      if (d && d.status === 'success') {
        SubsystemApp.showToast(d.message || t('Contact deactivated'),'success');
      } else {
        SubsystemApp.showToast((d && d.message) || t('Error'),'error');
      }
      await this._loadSupplierContacts(sid);
    } catch (e) {
      console.error('Supplier contact delete failed', e);
      SubsystemApp.showToast(t('Error'),'error');
    }
  },

  async _saveSupplier(sid) {
    const name = document.getElementById('sm-name')?.value.trim();
    if (!name) { SubsystemApp.showToast('Name required','error'); return; }
    const btn = document.getElementById('sm-btn');
    if (btn) { btn.disabled=true; btn.textContent='Saving…'; }
    const payload = { name, phone:document.getElementById('sm-phone')?.value||'',
                      email:document.getElementById('sm-email')?.value||'',
                      address:document.getElementById('sm-addr')?.value||'' };
    try {
      const d = sid
        ? await this._patch(`/api/sub/retail/suppliers/${sid}`, payload)
        : await this._post('/api/sub/retail/suppliers', payload);
      if (d.status==='success') {
        SubsystemApp.showToast(sid?'Updated':'Added','success');
        document.getElementById('ret-sup-modal')?.remove();
        this._loadSuppliers();
      } else { SubsystemApp.showToast(d.message||'Error','error'); if(btn){btn.disabled=false;btn.textContent='Save';} }
    } catch(e) { if(btn){btn.disabled=false;btn.textContent='Save';} }
  },

  // Mirrors _deleteCategory/_deleteCustomer exactly (see _deleteCategory's
  // comment for why: a failed delete must be VISIBLE, not a silent no-op).
  async _deleteSupplier(supId, name) {
    if (!confirm(`${t('Delete')} "${name}"?`)) return;
    try {
      const d = await this._del(`/api/sub/retail/suppliers/${supId}`);
      if (d && d.status === 'success') {
        SubsystemApp.showToast(d.message || t('Supplier deleted'), 'success');
      } else {
        SubsystemApp.showToast((d && d.message) || t('Could not delete this supplier.'), 'error');
      }
      this._loadSuppliers();
    } catch(e) {
      console.error('Supplier delete failed', e);
      SubsystemApp.showToast(t('Could not delete this supplier.'), 'error');
    }
  },

  // ── PURCHASE ORDERS ───────────────────────────────────────────────────────
  async _renderPurchases(c) {
    this._injectStyles();
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">Purchase Orders</h2>
        <button class="sub-btn-primary" onclick="RetailSystem._openCreatePO()">+ New PO</button>
      </div>
      <div class="sub-chart-card">
        <div style="overflow-x:auto">
          <table class="ret-table" id="po-table">
            <thead><tr><th>PO #</th><th>Supplier</th><th>Status</th><th>Total</th><th>Ordered</th><th>Received</th><th>Actions</th></tr></thead>
            <tbody><tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:30px">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>`;
    await this._loadPurchaseOrders();
  },

  async _loadPurchaseOrders() {
    try {
      const data = (await this._get('/api/sub/retail/purchase-orders')).data || [];
      const tbody = document.querySelector('#po-table tbody');
      if (!tbody) return;
      if (!data.length) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:30px">No purchase orders yet.</td></tr>';
        return;
      }
      const statusColor = { pending:'yellow', received:'green', cancelled:'red', partial:'blue' };
      tbody.innerHTML = data.map(po => `<tr>
        <td style="font-family:monospace;color:var(--sub-accent)">${this._bdi(po.po_number||'')}</td>
        <td style="font-weight:600">${po.supplier_name||'—'}</td>
        <td>${this._badge(po.status, statusColor[po.status]||'blue')}</td>
        <td style="font-weight:600">${this._fmt(po.total)}</td>
        <td style="color:var(--text-muted)">${po.ordered_at||'—'}</td>
        <td style="color:var(--text-muted)">${po.received_at||'—'}</td>
        <td>
          <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="RetailSystem._viewPO(${po.id})">View</button>
          ${po.status==='pending'?`<button class="ret-btn ret-btn-primary ret-btn-sm" style="margin-left:6px" onclick="RetailSystem._receivePO(${po.id},'${po.po_number}')">Receive</button>`:''}
        </td>
      </tr>`).join('');
    } catch(e) { console.error(e); }
  },

  // ── PO SPLIT PREVIEW (Thursday demo, Stream B) ─────────────────────────────
  // _openCreatePO used to be a single-step, single-supplier PO form (one
  // top-level "Supplier *" select, one PO created immediately via
  // POST .../purchase-orders). It is now a two-step preview flow:
  //   Step 1 (#po-step-basket) is the SAME basket-building mechanism as
  //   before -- _addPOItem/_renderPOItems are untouched -- just without the
  //   old top-level Supplier/Notes fields, because a basket can now span
  //   MULTIPLE suppliers: each line's supplier comes from its own product's
  //   supplier_id, resolved server-side in preview_po_split (never trusted
  //   from the client -- see that route's docstring in retail_api.py).
  //   Step 2 (#po-step-preview, _renderPOSplitPreview) calls
  //   POST .../purchase-orders/split-preview and renders one card per
  //   supplier group plus an Unassigned bucket. Nothing here writes a real
  //   PO yet -- the footer's "Create N Purchase Orders" button is
  //   deliberately disabled (see _renderPOSplitPreview) until the routes
  //   that persist split_group_id/routing_status land later this week.
  async _openCreatePO(supplierId=null, supplierName='') {
    const [sups, prods] = await Promise.all([
      this._get('/api/sub/retail/suppliers'),
      this._get('/api/sub/retail/products'),
    ]).catch(() => [{data:[]},{data:[]}]);
    this._suppliers = sups.data || [];
    const products  = prods.data || [];
    this._products  = products;
    this._poItems = [];
    this._poSplitResult = null;

    const prodOpts = products.map(p =>
      `<option value="${p.id}" data-cost="${p.cost_price}">${p.name} (${p.sku}) — Stock: ${p.total_stock}</option>`).join('');

    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'ret-po-modal';
    overlay.innerHTML = `
      <div class="ret-modal ret-modal-wide">
        <h3>📋 New Purchase Order${supplierName?' — '+supplierName:''}</h3>

        <div id="po-step-basket">
          <div style="margin:0 0 8px;color:var(--text-primary);font-weight:600">Order Items</div>
          <div id="po-items"></div>
          <div style="margin:12px 0">
            <div class="ret-field-row" style="grid-template-columns:3fr 1fr 1fr auto;gap:8px;align-items:end">
              <div class="ret-field" style="margin:0"><label>Product</label>
                <select id="po-item-prod"><option value="">Select product…</option>${prodOpts}</select></div>
              <div class="ret-field" style="margin:0"><label>Qty</label>
                <input type="number" id="po-item-qty" value="1" min="1" /></div>
              <div class="ret-field" style="margin:0"><label>Unit Cost</label>
                <input type="number" id="po-item-cost" step="0.01" min="0" /></div>
              <button class="ret-btn ret-btn-ghost" style="margin-bottom:1px" onclick="RetailSystem._addPOItem()">+ Add</button>
            </div>
          </div>
          <div style="text-align:right;color:var(--text-money);font-size:16px;font-weight:700;margin-bottom:16px">
            Total: <span id="po-total-display">$0.00</span>
          </div>
          <div class="ret-modal-footer">
            <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('ret-po-modal').remove()">Cancel</button>
            <button class="ret-btn ret-btn-primary" id="po-preview-btn" onclick="RetailSystem._previewPOSplit()">${t('Preview split')}</button>
          </div>
        </div>

        <div id="po-step-preview" style="display:none"></div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if(e.target===overlay) overlay.remove(); });
    document.getElementById('po-item-prod')?.addEventListener('change', function() {
      const opt = this.options[this.selectedIndex];
      const cost = opt?.dataset?.cost || 0;
      document.getElementById('po-item-cost').value = cost;
    });
  },

  _addPOItem() {
    const prodSel  = document.getElementById('po-item-prod');
    // AUDIT-follow-up (2026-08-10): product ids are UUID TEXT after the
    // catalog/party UUID migration, not integers -- unary `+` coerced any
    // real id to NaN, so "Select product and enter cost" fired even with a
    // product selected. Keep the id as the opaque string it actually is.
    const prodId   = prodSel?.value;
    const prodName = prodSel?.options[prodSel.selectedIndex]?.text?.split('(')[0]?.trim();
    const qty      = +document.getElementById('po-item-qty')?.value || 1;
    const cost     = +document.getElementById('po-item-cost')?.value || 0;
    if (!prodId || !cost) { SubsystemApp.showToast('Select product and enter cost','error'); return; }
    const existing = this._poItems.findIndex(i => i.product_id === prodId);
    if (existing >= 0) { this._poItems[existing].quantity += qty; }
    else { this._poItems.push({ product_id:prodId, product_name:prodName, quantity:qty, unit_cost:cost }); }
    this._renderPOItems();
    document.getElementById('po-item-prod').value = '';
    document.getElementById('po-item-qty').value  = 1;
    document.getElementById('po-item-cost').value = '';
  },

  _renderPOItems() {
    const container = document.getElementById('po-items');
    if (!container) return;
    if (!this._poItems.length) { container.innerHTML = ''; return; }
    let total = 0;
    container.innerHTML = `<table class="ret-table" style="margin-bottom:10px">
      <thead><tr><th>Product</th><th>Qty</th><th>Unit Cost</th><th>Total</th><th></th></tr></thead>
      <tbody>${this._poItems.map((item,i) => {
        const lineTotal = item.quantity * item.unit_cost;
        total += lineTotal;
        return `<tr>
          <td>${item.product_name}</td>
          <td><input type="number" value="${item.quantity}" min="1" style="width:60px;background:var(--surface-sunken);border:1px solid var(--border-default);border-radius:5px;color:var(--text-primary);padding:4px 8px;text-align:center;outline:none"
            oninput="RetailSystem._poItems[${i}].quantity=+this.value;RetailSystem._poItems[${i}].line_total=RetailSystem._poItems[${i}].quantity*RetailSystem._poItems[${i}].unit_cost;RetailSystem._renderPOItems()" /></td>
          <td>${this._fmt(item.unit_cost)}</td>
          <td style="font-weight:600">${this._fmt(lineTotal)}</td>
          <td><button class="ret-btn ret-btn-danger ret-btn-sm" onclick="RetailSystem._poItems.splice(${i},1);RetailSystem._renderPOItems()">✕</button></td>
        </tr>`;
      }).join('')}</tbody>
    </table>`;
    const totalEl = document.getElementById('po-total-display');
    if (totalEl) totalEl.textContent = this._fmt(total);
  },

  // Preview-only: POSTs the in-memory basket to preview_po_split, which
  // groups it by each product's OWN supplier_id (never a client-sent one --
  // see that route's docstring) and resolves each group's contact via
  // po_split.py's 5-rung ladder. Writes nothing; see _renderPOSplitPreview
  // for why the footer's create button stays disabled.
  async _previewPOSplit() {
    if (!this._poItems || !this._poItems.length) {
      SubsystemApp.showToast(t('Add at least one item'),'error');
      return;
    }
    const btn = document.getElementById('po-preview-btn');
    if (btn) { btn.disabled=true; btn.textContent=t('Loading…'); }
    try {
      const items = this._poItems.map(i => ({
        product_id: i.product_id, quantity: i.quantity, unit_cost: i.unit_cost,
      }));
      const d = await this._post('/api/sub/retail/purchase-orders/split-preview', { items });
      if (d.status === 'success') {
        this._poSplitResult = d.data;
        this._showPOSplitStep();
      } else {
        SubsystemApp.showToast(d.message || t('Error'),'error');
      }
    } catch (e) {
      SubsystemApp.showToast(t('Error'),'error');
    } finally {
      if (btn) { btn.disabled=false; btn.textContent=t('Preview split'); }
    }
  },

  _showPOSplitStep() {
    const basket  = document.getElementById('po-step-basket');
    const preview = document.getElementById('po-step-preview');
    if (basket)  basket.style.display  = 'none';
    if (preview) preview.style.display = 'block';
    this._renderPOSplitPreview();
  },

  _backToPOBasket() {
    const basket  = document.getElementById('po-step-basket');
    const preview = document.getElementById('po-step-preview');
    if (basket)  basket.style.display  = 'block';
    if (preview) preview.style.display = 'none';
  },

  // An "unassigned" line has no product.supplier_id yet. Assigning one here
  // is a real catalog edit (PATCH the product, same route/field the product
  // modal's own Supplier <select> already uses) rather than a preview-local
  // override -- supplier_id lives on `products`, not on a basket line, and
  // preview_po_split always re-reads it fresh from the DB. So the fix is to
  // update the product, then simply re-run the preview.
  async _assignItemSupplier(productId, supplierId) {
    if (!supplierId) return;
    try {
      const d = await this._patch(`/api/sub/retail/products/${productId}`, { supplier_id: supplierId });
      if (d.status === 'success') {
        const p = (this._products || []).find(x => String(x.id) === String(productId));
        if (p) p.supplier_id = supplierId;
        SubsystemApp.showToast(t('Supplier assigned'),'success');
        await this._previewPOSplit();
      } else {
        SubsystemApp.showToast(d.message || t('Error'),'error');
      }
    } catch (e) {
      SubsystemApp.showToast(t('Error'),'error');
    }
  },

  // Maps po_split.py's resolve_contact() source rung -> a short label + a
  // badge color, so the operator can see at a glance which of the 5 fixed
  // priority rungs matched (contact_primary_role > contact_role >
  // contact_primary > supplier_fallback > none -- see resolve_contact's
  // docstring). Each rung gets its OWN label/color rather than collapsing
  // contact_primary_role/contact_role into one -- rung 2 is explicitly a
  // NON-primary match (rung 1 already claims every primary role-match), so
  // labeling both "Primary…" would misreport rung 2's result.
  _CONTACT_SOURCE_META: {
    contact_primary_role: { label: 'Primary orders contact', color: 'green'  },
    contact_role:         { label: 'Orders contact',         color: 'blue'   },
    contact_primary:      { label: 'Primary contact',        color: 'purple' },
    supplier_fallback:    { label: 'Supplier fallback',      color: 'yellow' },
    none:                 { label: 'No contact',             color: 'red'    },
  },

  _renderPOSplitPreview() {
    const result = this._poSplitResult || { groups: [], unassigned: { lines: [] } };
    const groups = result.groups || [];
    const unassigned = result.unassigned || { lines: [] };

    const groupCards = groups.map(g => {
      const contact = g.contact || {};
      const meta = this._CONTACT_SOURCE_META[contact.source] || this._CONTACT_SOURCE_META.none;
      const contactLine = contact.name
        ? [contact.name, contact.email, contact.phone].filter(Boolean).map(v => this._esc(v)).join(' · ')
        : t('No contact');
      return `<div class="ret-po-split-card">
        <div class="ret-po-split-card-hdr">
          <div>
            <div class="ret-po-split-supplier">${this._esc(g.supplier_name || t('Unknown supplier'))}</div>
            <div class="ret-po-split-meta">${this._esc(g.line_count)} ${t('items')} · ${t('Subtotal')}: ${this._fmt(g.subtotal)}</div>
          </div>
          <div class="ret-po-split-total">${this._fmt(g.total)}</div>
        </div>
        ${g.below_min_order ? `<div class="ret-po-split-warning">⚠ ${t('Below minimum order value')}: ${this._fmt(g.min_order_value)}</div>` : ''}
        <div class="ret-po-split-contact">
          ${this._badge(t(meta.label), meta.color)}
          <span class="ret-po-split-contact-detail">${contactLine}</span>
        </div>
        <table class="ret-table">
          <thead><tr><th>${t('Product')}</th><th>${t('Qty')}</th><th>${t('Unit Cost')}</th><th>${t('Total')}</th></tr></thead>
          <tbody>${g.lines.map(l => `<tr>
            <td>${this._esc(l.product_name)}</td>
            <td>${this._esc(l.quantity)}</td>
            <td>${this._fmt(l.unit_cost)}</td>
            <td>${this._fmt(l.line_total)}</td>
          </tr>`).join('')}</tbody>
        </table>
      </div>`;
    }).join('');

    const unassignedLines = unassigned.lines || [];
    const unassignedBlock = unassignedLines.length ? `
      <div class="ret-po-split-card ret-po-split-unassigned">
        <div class="ret-po-split-card-hdr">
          <div class="ret-po-split-supplier">⚠ ${t('Unassigned')}</div>
          <div class="ret-po-split-total">${this._fmt(unassigned.subtotal)}</div>
        </div>
        <table class="ret-table">
          <thead><tr><th>${t('Product')}</th><th>${t('Qty')}</th><th>${t('Unit Cost')}</th><th>${t('Total')}</th><th>${t('Supplier')}</th></tr></thead>
          <tbody>${unassignedLines.map(l => `<tr>
            <td>${this._esc(l.product_name)}</td>
            <td>${this._esc(l.quantity)}</td>
            <td>${this._fmt(l.unit_cost)}</td>
            <td>${this._fmt(l.line_total)}</td>
            <td>
              <select onchange="RetailSystem._assignItemSupplier('${this._esc(l.product_id)}', this.value)">
                <option value="">${t('Assign supplier…')}</option>
                ${(this._suppliers||[]).map(s => `<option value="${this._esc(s.id)}">${this._esc(s.name)}</option>`).join('')}
              </select>
            </td>
          </tr>`).join('')}</tbody>
        </table>
      </div>` : '';

    const container = document.getElementById('po-step-preview');
    if (!container) return;
    container.innerHTML = `
      ${groupCards || `<p style="color:var(--text-muted)">${t('No supplier groups yet.')}</p>`}
      ${unassignedBlock}
      <div class="ret-modal-footer">
        <button class="ret-btn ret-btn-ghost" onclick="RetailSystem._backToPOBasket()">${t('Back')}</button>
        <button class="ret-btn ret-btn-primary" disabled title="${t('Not implemented yet')}">${t('Create')} ${groups.length} ${t('Purchase Orders')} (${t('coming next')})</button>
      </div>`;
  },

  async _receivePO(poId, poNumber) {
    if (!confirm(`Mark PO ${poNumber} as received? This will add the items to inventory.`)) return;
    try {
      const d = await this._post(`/api/sub/retail/purchase-orders/${poId}/receive`, {});
      if (d.status==='success') {
        SubsystemApp.showToast('Stock received and inventory updated','success');
        this._loadPurchaseOrders();
      } else { SubsystemApp.showToast(d.message||'Error','error'); }
    } catch(e) {}
  },

  async _viewPO(poId) {
    try {
      const resp = (await this._get(`/api/sub/retail/purchase-orders/${poId}`)).data || {};
      const po   = resp.po || {};
      const items = resp.items || [];
      const overlay = document.createElement('div');
      overlay.className = 'ret-modal-overlay';
      overlay.innerHTML = `
        <div class="ret-modal ret-modal-wide">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
            <h3 style="margin:0">PO: ${po.po_number}</h3>
            <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="this.closest('.ret-modal-overlay').remove()">✕</button>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:18px">
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">Supplier</div><div style="color:var(--text-primary);font-weight:600">${po.supplier_name||'—'}</div></div>
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">Status</div><div>${this._badge(po.status,'green')}</div></div>
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">Total</div><div style="color:var(--text-money);font-weight:700">${this._fmt(po.total)}</div></div>
          </div>
          <table class="ret-table">
            <thead><tr><th>Product</th><th>SKU</th><th>Qty Ordered</th><th>Qty Received</th><th>Unit Cost</th><th>Total</th></tr></thead>
            <tbody>${items.map(i=>`<tr>
              <td>${i.product_name||'—'}</td>
              <td style="font-family:monospace;color:var(--text-muted)">${i.sku||'—'}</td>
              <td>${i.quantity}</td>
              <td style="color:${i.received_qty>=i.quantity?'var(--state-success-text)':'var(--state-warning-text)'}">${i.received_qty||0}</td>
              <td>${this._fmt(i.unit_cost)}</td>
              <td style="font-weight:600">${this._fmt(i.total)}</td>
            </tr>`).join('')}</tbody>
          </table>
          ${po.notes?`<p style="color:var(--text-muted);margin-top:14px;font-size:13px">Notes: ${po.notes}</p>`:''}
        </div>`;
      document.body.appendChild(overlay);
      overlay.addEventListener('click', e => { if(e.target===overlay) overlay.remove(); });
    } catch(e) {}
  },

  // ── BRANCHES (ci-hardening-w0.3 continuation, "the doorway") ────────────────
  // create_branch (retail_api.py:7894) has been a complete, gated route
  // (CAP_EMPLOYEES + licence guard) since Phase 5 wave A -- nothing in this
  // file ever called it, so every install self-healed exactly one branch
  // (_default_branch) and a shop had no way to add a second. Whole features
  // shipped on top of a second branch existing (device-branch pinning,
  // branch-scoped accounts, branch managers, the head-office comparison
  // chart) were real, tested and unreachable. See app-shell.js's nav entry
  // for the same story from the other end.
  //
  // LIST AND CREATE ONLY, deliberately -- that is exactly the API surface
  // (GET /branches, POST /branches; no PATCH, no DELETE). Rename/deactivate
  // are NOT built here: a branch carries stock balances, sale history,
  // scoped user accounts and pinned tills, so "what happens to those" is a
  // real design question this screen does not answer, not a CRUD gap.
  async _renderBranches(c) {
    this._injectStyles();
    // Capability gate, same mechanism/reasoning as _renderPromotions and
    // _renderReports above -- read those comments first. 'retail.employees'
    // is CAP_EMPLOYEES (commercial_runtime/identity/user_accounts.py), the
    // SAME code create_branch's own @mt_require_capability(CAP_EMPLOYEES)
    // decorator requires. Hiding the nav entry (app-shell.js) is not the
    // enforcement; this guard is the real one, for the same reason
    // AuraRouter can replay this section from the URL hash with no nav
    // click in between.
    if (window.SubsystemApp && !SubsystemApp.hasCapability('retail.employees')) {
      return this._renderCapabilityRestricted(c, {
        icon: '🏦',
        title: t('Branches'),
        message: t('Managing branches is limited to managers and the store owner. Open the till to start ringing sales.'),
      });
    }
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">${t('Branches')}</h2>
        <div style="display:flex;gap:10px">
          <button class="sub-btn-primary" onclick="RetailSystem._openAddBranch()">+ ${t('Add Branch')}</button>
        </div>
      </div>
      <div id="branches-pin-notice"></div>
      <div class="sub-chart-card">
        <div style="overflow-x:auto">
          <table class="ret-table" id="branch-table">
            <thead><tr><th>${t('Branch Name')}</th><th>${t('Address')}</th><th>${t('Phone')}</th><th>${t('Status')}</th></tr></thead>
            <tbody><tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:30px">${t('Loading…')}</td></tr></tbody>
          </table>
        </div>
      </div>`;
    await this._loadBranches();
  },

  async _loadBranches() {
    try {
      const data = (await this._get('/api/sub/retail/branches')).data || [];
      this._branches = data;
      const notice = document.getElementById('branches-pin-notice');
      if (notice) {
        // The one piece of guidance this screen must carry (ci-hardening-
        // w0.3 brief): once a SECOND branch exists, an unpinned till files
        // its sales under the company's FIRST branch, silently -- the exact
        // defect dc22b04 fixed, and no report looks odd when it happens. A
        // single-branch shop has no such concept and must not be nagged
        // about it -- see the mutation proof on this exact condition in
        // retail_branches_test.js (M3: showing this with ONE branch must
        // turn that test red).
        notice.innerHTML = this._branches.length > 1
          ? `<p class="ret-branch-pin-notice" style="color:var(--state-warning-text);font-size:13px;margin:0 0 16px">${t('Every till now needs its own branch pin, or its sales silently file under your first branch -- set it per device in Settings, under This Device\'s Branch.')}</p>`
          : '';
      }
      const tbody = document.querySelector('#branch-table tbody');
      if (!tbody) return;
      if (!data.length) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:30px">${t('No branches found.')}</td></tr>`;
        return;
      }
      // Every interpolated value here is escaped (this._esc): `branch` is a
      // synced entity type (Wave B, sync_service.py) so a row can arrive
      // from ANOTHER DEVICE over the sync relay -- the same trust boundary
      // Category/Supplier/Customer already cross.
      tbody.innerHTML = data.map(b => `<tr>
        <td style="font-weight:600">${this._esc(b.name)}</td>
        <td style="color:var(--text-muted)">${b.address ? this._esc(b.address) : '—'}</td>
        <td style="color:var(--text-muted)">${b.phone ? this._esc(b.phone) : '—'}</td>
        <td>${this._badge(b.status === 'active' ? t('Active') : t('Inactive'), b.status === 'active' ? 'green' : 'red')}</td>
      </tr>`).join('');
    } catch (e) { console.error(e); }
  },

  _openAddBranch() { this._showBranchModal(); },

  _showBranchModal() {
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'ret-branch-modal';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:440px">
        <h3>🏦 ${t('Add Branch')}</h3>
        <div class="ret-field"><label>${t('Branch Name')} *</label><input id="brm-name" /></div>
        <div class="ret-field"><label>${t('Address')}</label><input id="brm-address" /></div>
        <div class="ret-field"><label>${t('Phone')}</label><input id="brm-phone" /></div>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('ret-branch-modal').remove()">${t('Cancel')}</button>
          <button class="ret-btn ret-btn-primary" id="brm-btn" onclick="RetailSystem._saveBranch()">${t('Add Branch')}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if(e.target===overlay) overlay.remove(); });
    document.getElementById('brm-name')?.focus();
  },

  async _saveBranch() {
    // Client-side refusal BEFORE the network call: create_branch 400s on a
    // blank name (retail_api.py) -- this guard means the operator sees an
    // immediate inline refusal instead of a round trip just to be told the
    // same thing. See the mutation proof on this exact guard in
    // retail_branches_test.js.
    const name = document.getElementById('brm-name')?.value.trim();
    if (!name) { SubsystemApp.showToast(t('Name required'), 'error'); return; }
    const btn = document.getElementById('brm-btn');
    if (btn) { btn.disabled = true; btn.textContent = t('Saving…'); }
    const payload = {
      name,
      address: document.getElementById('brm-address')?.value || '',
      phone: document.getElementById('brm-phone')?.value || '',
    };
    try {
      const d = await this._post('/api/sub/retail/branches', payload);
      if (d.status === 'success') {
        SubsystemApp.showToast(t('Branch added'), 'success');
        document.getElementById('ret-branch-modal')?.remove();
        this._loadBranches();
      } else {
        SubsystemApp.showToast(d.message || t('Error'), 'error');
        if (btn) { btn.disabled = false; btn.textContent = t('Add Branch'); }
      }
    } catch (e) {
      if (btn) { btn.disabled = false; btn.textContent = t('Add Branch'); }
    }
  },

  // ── ADMIN CENTER (feat/reorder-automation-foundation) ───────────────────────
  // Gated at the nav level (app-shell.js's adminOnly filter, resolved once
  // via GET /api/devices/me at shell init) -- this render function itself
  // assumes it was only ever reached through that gate, same as e.g.
  // _renderScannerSettings assumes desktopOnly already filtered Android.
  // Foundation scope: only the low-stock reorder request queue exists so
  // far. Future admin-only surfaces land as additional cards on this same
  // page rather than new top-level nav entries.
  async _renderAdminCenter(c) {
    this._injectStyles();
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">${t('Settings')}</h2>
      </div>
      <div class="sub-chart-card" id="branding-card">
        <h3 style="color:var(--text-primary);margin:0 0 14px;font-size:15px">${t('Branding')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 16px">
          ${t('Shown on printed receipts and the app sidebar. Configure this once for whatever shop, co-op or foundation is running this install.')}
        </p>
        <div class="ret-field-row" style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
          <div class="ret-field" style="margin:0"><label>${t('Business Name')}</label>
            <input type="text" id="brand-name" maxlength="120" /></div>
          <div class="ret-field" style="margin:0"><label>${t('Tax / Registration Number')}</label>
            <input type="text" id="brand-tax" maxlength="60" /></div>
        </div>
        <div class="ret-field-row" style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:12px">
          <div class="ret-field" style="margin:0"><label>${t('Address')}</label>
            <input type="text" id="brand-address" maxlength="200" /></div>
          <div class="ret-field" style="margin:0"><label>${t('Phone')}</label>
            <input type="text" id="brand-phone" maxlength="40" /></div>
        </div>
        <div class="ret-field" style="margin-top:12px"><label>${t('Receipt Header')}</label>
          <input type="text" id="brand-header" maxlength="120" /></div>
        <div class="ret-field" style="margin-top:12px"><label>${t('Receipt Footer')}</label>
          <input type="text" id="brand-footer" maxlength="120" /></div>
        <div class="ret-field" style="margin-top:12px">
          <label>${t('Logo')}</label>
          <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
            <img id="brand-logo-preview" alt="" style="display:none;max-height:48px;max-width:160px;border-radius:6px;border:1px solid var(--border-default)" />
            <input type="file" id="brand-logo-file" accept="image/png,image/jpeg,image/gif,image/webp" />
            <button class="ret-btn ret-btn-ghost ret-btn-sm" id="brand-logo-remove" style="display:none" onclick="RetailSystem._removeBrandingLogo()">${t('Remove')}</button>
          </div>
          <p style="color:var(--text-muted);font-size:11px;margin:6px 0 0">${t('PNG, JPEG, GIF or WebP, under 300KB.')}</p>
        </div>
        <div id="branding-einvoice-block" style="display:none;margin-top:16px;padding:12px;border-radius:10px;background:var(--surface-sunken)">
          <h4 style="color:var(--text-primary);margin:0 0 6px;font-size:13px">${t('E-Invoicing Seller Identity (read-only)')}</h4>
          <p style="color:var(--text-muted);font-size:12px;margin:0 0 8px">
            ${t('This is the name and tax number submitted on Jordan e-invoices, configured separately under E-Invoicing. It is shown here for comparison only and is never changed from this screen. If it differs from your receipt branding above, update the one you mean to change.')}
          </p>
          <div id="branding-einvoice-values" style="color:var(--text-secondary);font-size:12px"></div>
        </div>
        <button class="ret-btn ret-btn-primary" style="margin-top:16px" onclick="RetailSystem._saveBranding()">${t('Save')}</button>
      </div>
      <div class="sub-chart-card" id="device-branch-card">
        <h3 style="color:var(--text-primary);margin:0 0 14px;font-size:15px">${t('This Device\'s Branch')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 16px">
          ${t('Which physical store this till stands in. Sales and stock ring up under this branch instead of the company-wide default -- set this once per device on a chain with more than one store.')}
        </p>
        <div id="device-branch-unpinned-warning" style="display:none;margin-bottom:12px;padding:10px 12px;border-radius:8px;background:var(--state-warning-surface);color:var(--state-warning-text);font-size:12px;border:1px solid var(--border-default)">
          ${t('No branch is pinned to this device. Fine for a single-branch shop -- but on a chain, every sale here files under the company\'s first branch until you pin one.')}
        </div>
        <div class="ret-field" style="margin:0"><label>${t('Branch')}</label>
          <select id="device-branch-select"><option value="">${t('Unpinned')}</option></select>
        </div>
        <button class="ret-btn ret-btn-primary" style="margin-top:12px" onclick="RetailSystem._saveDeviceBranch()">${t('Save')}</button>
      </div>
      <div class="sub-chart-card">
        <h3 style="color:var(--text-primary);margin:0 0 14px;font-size:15px">${t('Low-Stock Reorder Requests')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 16px">
          ${t('Automatically drafted when a sale drops a product at or below its reorder level. Accept drafts a local purchase order for this device; Decline dismisses it.')}
        </p>
        <div style="overflow-x:auto">
          <table class="ret-table" id="reorder-req-table">
            <thead><tr><th>${t('Product')}</th><th>${t('Branch')}</th><th>${t('Note')}</th><th>${t('Requested')}</th><th>${t('Actions')}</th></tr></thead>
            <tbody><tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:30px">${t('Loading…')}</td></tr></tbody>
          </table>
        </div>
      </div>
      <div class="sub-chart-card">
        <h3 style="color:var(--text-primary);margin:0 0 14px;font-size:15px">${t('WhatsApp Reports')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 16px">
          ${t('Send daily sales, shift-close, low-stock, and overdue-balance reports to multiple phone numbers by role or branch. Off by default.')}
        </p>
        <button class="ret-btn ret-btn-primary" onclick="location.href='/static/whatsapp.html'">${t('Manage WhatsApp Reports')}</button>
      </div>`;
    await this._loadReorderRequests();
    await this._loadBrandingForm();
    await this._loadDeviceBranchForm();
  },

  // ── Branding (Admin Center) ─────────────────────────────────────────────
  async _loadBrandingForm() {
    try {
      const resp = await this._get('/api/sub/retail/settings/branding');
      const b = (resp && resp.data) || {};
      this._brandingLoaded = b;
      const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ''; };
      setVal('brand-name', b.branding_business_name);
      setVal('brand-tax', b.branding_tax_number);
      setVal('brand-address', b.branding_address);
      setVal('brand-phone', b.branding_phone);
      setVal('brand-header', b.branding_receipt_header);
      setVal('brand-footer', b.branding_receipt_footer);

      const preview = document.getElementById('brand-logo-preview');
      const removeBtn = document.getElementById('brand-logo-remove');
      if (b.has_logo) {
        if (removeBtn) removeBtn.style.display = '';
        try {
          const logoResp = await this._get('/api/sub/retail/settings/branding/logo');
          const uri = logoResp && logoResp.data && logoResp.data.logo;
          if (uri && preview) { preview.src = uri; preview.style.display = ''; }
        } catch (e) { /* best-effort preview only -- the save button below still works */ }
      }

      const einvoiceBlock = document.getElementById('branding-einvoice-block');
      const einvoiceValues = document.getElementById('branding-einvoice-values');
      if (b.einvoicing_seller && einvoiceBlock && einvoiceValues) {
        einvoiceBlock.style.display = '';
        einvoiceValues.innerHTML =
          `<div>${t('Seller name')}: ${this._esc(b.einvoicing_seller.seller_name || '—')}</div>` +
          `<div>${t('Seller TIN')}: ${this._esc(b.einvoicing_seller.seller_tin || '—')}</div>`;
      }
    } catch (e) { console.error(e); }
  },

  async _saveBranding() {
    const val = (id) => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
    const payload = {
      branding_business_name: val('brand-name'),
      branding_address: val('brand-address'),
      branding_phone: val('brand-phone'),
      branding_tax_number: val('brand-tax'),
      branding_receipt_header: val('brand-header'),
      branding_receipt_footer: val('brand-footer'),
    };
    try {
      const resp = await this._post('/api/sub/retail/settings/branding', payload);
      if (!resp || resp.status !== 'success') {
        SubsystemApp.showToast((resp && resp.message) || t('Could not save branding.'), 'error');
        return;
      }
      const fileInput = document.getElementById('brand-logo-file');
      if (fileInput && fileInput.files && fileInput.files[0]) {
        await this._uploadBrandingLogo(fileInput.files[0]);
      }
      SubsystemApp.showToast(t('Branding saved.'), 'success');
    } catch (e) {
      console.error(e);
      SubsystemApp.showToast(t('Could not save branding.'), 'error');
    }
  },

  // ── This device's branch pin (Admin Center) ─────────────────────────────
  // Launch-readiness chain wave C1 (ROADMAP.md's 2026-08-30 "the
  // multi-branch capture defect" entry). Device-local, never company-wide
  // -- see api/retail_api.py's get_device_branch/set_device_branch and
  // onboarding_routes.py's get_device_branch_uid/set_device_branch_uid for
  // why this is a uid, not an id, and why it lives in config.json.
  async _loadDeviceBranchForm() {
    try {
      const [branchesResp, pinResp] = await Promise.all([
        this._get('/api/sub/retail/branches'),
        this._get('/api/sub/retail/device/branch'),
      ]);
      const branches = (branchesResp && branchesResp.data) || [];
      const pin = (pinResp && pinResp.data) || {};
      const opts = branches.map(b =>
        `<option value="${this._esc(b.uid)}" ${b.uid === pin.branch_uid ? 'selected' : ''}>${this._esc(b.name)}</option>`
      ).join('');
      const select = document.getElementById('device-branch-select');
      if (select) select.innerHTML = `<option value="">${t('Unpinned')}</option>${opts}`;
      const warning = document.getElementById('device-branch-unpinned-warning');
      // Obvious when NO branch is pinned -- that is the state that
      // silently produces wrong data for a chain (see this card's own
      // intro paragraph).
      if (warning) warning.style.display = pin.branch_uid ? 'none' : '';
    } catch (e) { console.error(e); }
  },

  async _saveDeviceBranch() {
    const select = document.getElementById('device-branch-select');
    const uid = select ? select.value : '';
    try {
      const resp = await this._post('/api/sub/retail/device/branch', { branch_uid: uid || null });
      if (!resp || resp.status !== 'success') {
        SubsystemApp.showToast((resp && resp.message) || t('Could not save this device\'s branch.'), 'error');
        return;
      }
      SubsystemApp.showToast(t('Device branch saved.'), 'success');
      await this._loadDeviceBranchForm();
    } catch (e) {
      console.error(e);
      SubsystemApp.showToast(t('Could not save this device\'s branch.'), 'error');
    }
  },

  _readFileAsDataURL(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  },

  async _uploadBrandingLogo(file) {
    try {
      const dataUri = await this._readFileAsDataURL(file);
      const resp = await this._post('/api/sub/retail/settings/branding/logo', { logo: dataUri });
      if (!resp || resp.status !== 'success') {
        SubsystemApp.showToast((resp && resp.message) || t('Could not save the logo.'), 'error');
      }
    } catch (e) {
      console.error(e);
      SubsystemApp.showToast(t('Could not save the logo.'), 'error');
    }
  },

  async _removeBrandingLogo() {
    if (!confirm(t('Remove the receipt logo?'))) return;
    try {
      const resp = await this._del('/api/sub/retail/settings/branding/logo');
      if (resp && resp.status === 'success') {
        const preview = document.getElementById('brand-logo-preview');
        const removeBtn = document.getElementById('brand-logo-remove');
        const fileInput = document.getElementById('brand-logo-file');
        if (preview) { preview.style.display = 'none'; preview.src = ''; }
        if (removeBtn) removeBtn.style.display = 'none';
        if (fileInput) fileInput.value = '';
        SubsystemApp.showToast(t('Logo removed.'), 'success');
      } else {
        SubsystemApp.showToast((resp && resp.message) || t('Could not remove the logo.'), 'error');
      }
    } catch (e) {
      console.error(e);
      SubsystemApp.showToast(t('Could not remove the logo.'), 'error');
    }
  },

  async _loadReorderRequests() {
    try {
      const data = (await this._get('/api/sub/retail/reorder-requests')).data || [];
      this._reorderRequests = data;
      const tbody = document.querySelector('#reorder-req-table tbody');
      if (!tbody) return;
      if (!data.length) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:30px">${t('No pending reorder requests.')}</td></tr>`;
        return;
      }
      // Every interpolated value is escaped (see this._esc) -- reorder
      // request ids/product names can arrive from ANOTHER DEVICE over the
      // sync relay, same trust boundary as Category/Supplier rows.
      tbody.innerHTML = data.map(r => `<tr>
        <td style="font-weight:600">${this._esc(r.product_name)}<div style="font-size:11px;color:var(--text-muted)">${this._esc(r.sku||'')}</div></td>
        <td>${r.branch_name ? this._esc(r.branch_name) : '—'}</td>
        <td style="color:var(--text-muted);max-width:320px">${this._esc(r.draft_message||'')}</td>
        <td style="color:var(--text-muted)">${r.created_at ? this._bdi(new Date(r.created_at).toLocaleString()) : '—'}</td>
        <td>
          <button class="ret-btn ret-btn-primary ret-btn-sm" onclick="RetailSystem._acceptReorderRequest('${this._esc(r.id)}')">${t('Accept')}</button>
          <button class="ret-btn ret-btn-ghost ret-btn-sm" style="margin-left:6px" onclick="RetailSystem._declineReorderRequest('${this._esc(r.id)}')">${t('Decline')}</button>
        </td>
      </tr>`).join('');
    } catch(e) { console.error(e); }
  },

  async _acceptReorderRequest(rid) {
    if (!confirm(t('Accept this request and draft a local purchase order?'))) return;
    try {
      const d = await this._post(`/api/sub/retail/reorder-requests/${rid}/accept`, {});
      if (d && d.status === 'success') {
        SubsystemApp.showToast(d.data && d.data.po_number ? `${t('Purchase order drafted')}: ${d.data.po_number}` : t('Request accepted'), 'success');
      } else {
        SubsystemApp.showToast((d && d.message) || t('Could not accept this request.'), 'error');
      }
      this._loadReorderRequests();
    } catch(e) {
      console.error('Reorder request accept failed', e);
      SubsystemApp.showToast(t('Could not accept this request.'), 'error');
    }
  },

  async _declineReorderRequest(rid) {
    if (!confirm(t('Decline this reorder request?'))) return;
    try {
      const d = await this._post(`/api/sub/retail/reorder-requests/${rid}/decline`, {});
      if (d && d.status === 'success') {
        SubsystemApp.showToast(t('Request declined'), 'success');
      } else {
        SubsystemApp.showToast((d && d.message) || t('Could not decline this request.'), 'error');
      }
      this._loadReorderRequests();
    } catch(e) {
      console.error('Reorder request decline failed', e);
      SubsystemApp.showToast(t('Could not decline this request.'), 'error');
    }
  },

  // ── EMAIL NOTIFICATIONS (ci-hardening-w0.3 continuation, "the doorway",
  //    second one on this branch) ───────────────────────────────────────────
  // GET/POST /api/notifications/status, /settings, /outbox and
  // /outbox/run-once (commercial_runtime/notifications/routes.py) have been
  // complete since the outbox/worker/SMTP client shipped -- an outbox, a
  // retry worker, an SMTP client and a real trigger (low-stock alerts,
  // core/retail/whatsapp_hook.py's sibling) all real, all tested, and
  // reachable by nothing. WhatsApp got a settings page (whatsapp.html,
  // linked from Admin Center); email never did. So SMTP recipients could
  // not be configured by any user, ever, and the channel was inert in
  // practice. See app-shell.js's nav entry for the same story from the
  // other end.
  //
  // Structure mirrors _renderAdminCenter's card layout and _renderBranches'
  // gate/load/save shape -- both already live in this file, so this is not
  // a second pattern for the same job. The CONTENT (enable toggle, two
  // recipient fields, retry settings as a secondary/advanced block, outbox
  // counts, a manual run-once action, 400s surfaced verbatim) mirrors
  // whatsapp.js's screen, which solves the identical problem for the
  // sibling channel.
  async _renderEmailNotifications(c) {
    this._injectStyles();
    // Gated on the USER axis, matching `_require_admin` in routes.py
    // (`session['mt_role'] == 'admin'`) EXACTLY -- see app-shell.js's nav
    // entry comment for why this is `ownerOnly`, not `adminOnly`, and
    // _renderStockAccuracy's identical second check for the precedent.
    // AuraRouter can replay this section from the URL hash with no nav
    // click in between, so hiding the nav entry alone is not the
    // enforcement; this guard is the real one.
    if (window.SubsystemApp && SubsystemApp.role && SubsystemApp.role !== 'admin') {
      return this._renderCapabilityRestricted(c, {
        icon: '📧',
        title: t('Email Notifications'),
        message: t('Email notification settings are limited to the store owner. Open the till to start ringing sales.'),
      });
    }
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">${t('Email Notifications')}</h2>
      </div>
      <div class="sub-chart-card">
        <h3 style="color:var(--text-primary);margin:0 0 14px;font-size:15px">${t('Status')}</h3>
        <div id="email-smtp-warning"></div>
        <div id="email-smtp-note" style="color:var(--text-muted);font-size:12px;margin:0 0 14px"></div>
        <div class="ret-field" style="margin:0;display:flex;align-items:center;gap:10px">
          <input type="checkbox" id="email-enabled" style="width:auto" />
          <label for="email-enabled" style="margin:0;text-transform:none;font-size:14px;color:var(--text)">${t('Enable email notifications for this business')}</label>
        </div>
      </div>
      <div class="sub-chart-card">
        <h3 style="color:var(--text-primary);margin:0 0 14px;font-size:15px">${t('Recipients')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 16px">${t('Where automatic emails go. Leave blank to skip that kind of email entirely.')}</p>
        <div class="ret-field-row">
          <div class="ret-field" style="margin:0"><label>${t('Low-Stock Alert Recipient')}</label>
            <input type="email" id="email-low-stock" placeholder="owner@example.com" /></div>
          <div class="ret-field" style="margin:0"><label>${t('Reports Recipient')}</label>
            <input type="email" id="email-reports" placeholder="owner@example.com" /></div>
        </div>
      </div>
      <div class="sub-chart-card">
        <h3 style="color:var(--text-primary);margin:0 0 14px;font-size:15px">${t('Advanced (Retry Settings)')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 16px">${t('For whoever installed this system. A shopkeeper does not need to change these.')}</p>
        <div class="ret-field-row">
          <div class="ret-field" style="margin:0"><label>${t('Max Attempts')}</label>
            <input type="number" min="1" id="email-max-attempts" /></div>
          <div class="ret-field" style="margin:0"><label>${t('Retry Interval (Seconds)')}</label>
            <input type="number" min="1" id="email-submit-interval" /></div>
        </div>
      </div>
      <button class="ret-btn ret-btn-primary" id="email-save-btn" onclick="RetailSystem._saveEmailNotifications()">${t('Save')}</button>
      <div class="sub-chart-card" style="margin-top:20px">
        <h3 style="color:var(--text-primary);margin:0 0 14px;font-size:15px">${t('Outbox')}</h3>
        <div id="email-outbox-counts" style="display:flex;gap:10px;flex-wrap:wrap;margin:0 0 16px"></div>
        <button class="ret-btn ret-btn-ghost" id="email-run-once-btn" onclick="RetailSystem._runEmailOutboxOnce()">${t('Send Now')}</button>
      </div>`;
    await this._loadEmailNotifications();
  },

  async _loadEmailNotifications() {
    try {
      const [statusResp, settingsResp] = await Promise.all([
        this._get('/api/notifications/status'),
        this._get('/api/notifications/settings'),
      ]);
      const status = (statusResp && statusResp.data) || {};
      const settings = (settingsResp && settingsResp.data && settingsResp.data.settings) || {};
      // `settings.enabled` (the RAW, unfolded per-company toggle from
      // GET /settings) -- deliberately NOT `status.enabled`. is_enabled()
      // (commercial_runtime/notifications/settings.py) folds
      // smtp_client.is_configured() INTO its answer, so GET /status's
      // `enabled` can never read true while SMTP is unconfigured -- it
      // would always report the OFF state, which is exactly the silence
      // this screen exists to replace with a plain statement. The raw
      // toggle is what POST /settings actually persists (routes.py writes
      // it unconditionally; it only skips STARTING the worker when SMTP
      // isn't configured), so it is also the only value that can show a
      // shop what it asked for, correctly, even when the effective answer
      // differs.
      const enabledOn = settings.enabled === '1';
      this._emailSmtpConfigured = status.smtp_configured === true;

      const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = (v === undefined || v === null) ? '' : v; };
      const enabledCb = document.getElementById('email-enabled');
      if (enabledCb) enabledCb.checked = enabledOn;
      setVal('email-low-stock', settings.low_stock_recipient);
      setVal('email-reports', settings.reports_recipient);
      setVal('email-max-attempts', settings.max_attempts);
      setVal('email-submit-interval', settings.submit_interval_seconds);

      // THE MOST IMPORTANT BEHAVIOUR ON THIS SCREEN (ci-hardening-w0.3
      // brief). AURA_SMTP_HOST is an env var set at INSTALL time -- nothing
      // in this UI can change it. A shop CAN flip `enabled` on and save
      // successfully (POST /settings 200s regardless of SMTP configuration;
      // see routes.py::_post_settings, which only skips starting the worker)
      // and have nothing ever send. That state is invisible everywhere
      // else in the product, so it is said here, plainly, not as a subtle
      // badge -- see retail_email_notifications_test.js's mutation proofs
      // on both directions of this exact condition.
      const warning = document.getElementById('email-smtp-warning');
      if (warning) {
        warning.innerHTML = (enabledOn && !this._emailSmtpConfigured)
          ? `<p style="color:var(--state-warning-text);background:var(--state-warning-surface);border:1px solid var(--border-default);border-radius:8px;padding:10px 12px;font-size:13px;margin:0 0 16px">${t('Email is switched on, but this installation has no mail server configured -- nothing will actually send. Ask whoever installed this system to set AURA_SMTP_HOST.')}</p>`
          : '';
      }
      // The other direction: calm, once, ONLY in the healthy state -- never
      // repeated as a nag once SMTP is configured (brief: "do not nag when
      // SMTP IS configured. State it once, calmly").
      const note = document.getElementById('email-smtp-note');
      if (note) note.textContent = this._emailSmtpConfigured ? t('This installation can send email (a mail server is configured).') : '';

      this._paintEmailOutboxCounts(status.counts_by_state);
    } catch (e) { console.error(e); }
  },

  _paintEmailOutboxCounts(counts) {
    const box = document.getElementById('email-outbox-counts');
    if (!box) return;
    const c = counts || {};
    const badge = (label, n, color) => this._badge(`${this._esc(label)}: ${Number(n || 0)}`, color);
    box.innerHTML = [
      badge(t('Queued'), c.QUEUED, 'blue'),
      badge(t('Sending'), c.SENDING, 'blue'),
      badge(t('Sent'), c.SENT, 'green'),
      badge(t('Failed'), c.FAILED_PERMANENT, 'red'),
      badge(t('Cancelled'), c.CANCELLED, 'purple'),
    ].join(' ');
  },

  // /^[^\s@]+@[^\s@]+\.[^\s@]+$/ -- a permissive shape check, not a full
  // RFC 5322 validator. The server is the real judge (POST /settings 400s
  // on a value it rejects); this exists only to catch a typo before a
  // round trip, matching the brief's own framing ("the server 400s, and
  // sending a user to a 400 for a typo is avoidable").
  _looksLikeEmail(v) { return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v); },

  async _saveEmailNotifications() {
    const enabledCb = document.getElementById('email-enabled');
    const lowStock = (document.getElementById('email-low-stock')?.value || '').trim();
    const reports = (document.getElementById('email-reports')?.value || '').trim();

    // Client-side refusal BEFORE the network call. Empty is allowed (both
    // recipient keys default to '' -- see settings.py's DEFAULTS -- meaning
    // "not set", not "invalid"); only a NON-EMPTY value that does not look
    // like an address is refused here.
    if (lowStock && !this._looksLikeEmail(lowStock)) {
      SubsystemApp.showToast(t('That does not look like an email address.'), 'error');
      return;
    }
    if (reports && !this._looksLikeEmail(reports)) {
      SubsystemApp.showToast(t('That does not look like an email address.'), 'error');
      return;
    }

    const btn = document.getElementById('email-save-btn');
    if (btn) { btn.disabled = true; btn.textContent = t('Saving…'); }
    const payload = {
      enabled: (enabledCb && enabledCb.checked) ? '1' : '0',
      low_stock_recipient: lowStock,
      reports_recipient: reports,
      max_attempts: document.getElementById('email-max-attempts')?.value || '',
      submit_interval_seconds: document.getElementById('email-submit-interval')?.value || '',
    };
    try {
      const d = await this._post('/api/notifications/settings', payload);
      if (d && d.status === 'success') {
        SubsystemApp.showToast(t('Email settings saved.'), 'success');
      } else {
        // Surfaced VERBATIM -- routes.py's 400 message (e.g. a bad
        // max_attempts value) is written to be read, not replaced with a
        // generic failure toast. See requirement (6) / M-proof in
        // retail_email_notifications_test.js.
        SubsystemApp.showToast((d && d.message) || t('Error'), 'error');
      }
    } catch (e) {
      SubsystemApp.showToast(t('Error'), 'error');
    }
    if (btn) { btn.disabled = false; btn.textContent = t('Save'); }
    await this._loadEmailNotifications();
  },

  async _runEmailOutboxOnce() {
    const btn = document.getElementById('email-run-once-btn');
    if (btn) { btn.disabled = true; btn.textContent = t('Sending…'); }
    try {
      const d = await this._post('/api/notifications/outbox/run-once', {});
      if (d && d.status === 'success') {
        SubsystemApp.showToast(t('Outbox processed.'), 'success');
      } else {
        SubsystemApp.showToast((d && d.message) || t('Error'), 'error');
      }
    } catch (e) {
      SubsystemApp.showToast(t('Error'), 'error');
    }
    if (btn) { btn.disabled = false; btn.textContent = t('Send Now'); }
    await this._loadEmailNotifications();
  },

  // ── AUDIT LOG (read-only viewer, feat/audit-log-viewer) ─────────────────────
  // _audit() in retail_api.py has written every row this page shows since the
  // very first version of that file -- refunds, voids, product/customer/
  // supplier CRUD, PO lifecycle, reorder decisions, payments -- with real
  // user_id attribution. This is the first (and only) place any of it is
  // ever read back. Gated adminOnly in app-shell.js's nav AND enforced
  // server-side (list_audit_log 403s a non-admin device directly) -- see
  // that route's docstring for why this one doesn't rely on nav-hiding alone
  // the way Admin Center's reorder-requests page does.
  async _renderAuditLog(c) {
    this._injectStyles();
    // The other half of the nav gate, exactly as _renderReports has. The nav
    // entry for this screen carries `capability: 'retail.reports'` and the
    // route behind it is gated on the same code, but `_navigate('audit-log')`
    // does not go through the sidebar: AuraRouter persists the last section
    // into the URL hash and replays it on the next launch, so an owner who
    // last read the audit trail on the shop's admin terminal leaves the next
    // cashier standing on this screen.
    //
    // Worth being explicit about why this is a fix and not decoration, because
    // this path was the least visibly broken of the set: _loadAuditLog's error
    // branch already renders the server's own refusal message, so a cashier
    // landing here saw a sentence rather than a blank table. The request still
    // went out and still 403'd. "It looks handled" is what kept it unfixed.
    if (window.SubsystemApp && !SubsystemApp.hasCapability('retail.reports')) {
      return this._renderCapabilityRestricted(c, {
        icon: '📜',
        title: t('Audit Log'),
        message: t('The activity log is limited to managers and the store owner.'),
      });
    }
    // Local UI state, not persisted -- a fresh page visit always starts on
    // page 1 with no filters, same as every other list page in this file.
    this._auditLog = { page: 1, limit: 50, date_from: '', date_to: '', action: '', entity: '', totalPages: 1 };
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">${t('Audit Log')}</h2>
      </div>
      <div class="sub-chart-card">
        <div class="ret-field-row" style="grid-template-columns:1fr 1fr 1.3fr 1.3fr auto;gap:10px;align-items:end;margin-bottom:18px">
          <div class="ret-field" style="margin:0"><label>${t('From')}</label>
            <input type="date" id="aud-from" onchange="RetailSystem._applyAuditFilters()" /></div>
          <div class="ret-field" style="margin:0"><label>${t('To')}</label>
            <input type="date" id="aud-to" onchange="RetailSystem._applyAuditFilters()" /></div>
          <div class="ret-field" style="margin:0"><label>${t('Action')}</label>
            <select id="aud-action" onchange="RetailSystem._applyAuditFilters()"><option value="">${t('All actions')}</option></select></div>
          <div class="ret-field" style="margin:0"><label>${t('Entity')}</label>
            <select id="aud-entity" onchange="RetailSystem._applyAuditFilters()"><option value="">${t('All entities')}</option></select></div>
          <button class="ret-btn ret-btn-ghost" style="height:38px" onclick="RetailSystem._clearAuditFilters()">${t('Clear')}</button>
        </div>
        <div style="overflow-x:auto">
          <table class="ret-table" id="aud-table">
            <thead><tr><th>${t('Timestamp')}</th><th>${t('User')}</th><th>${t('Action')}</th><th>${t('Entity')}</th><th>${t('Details')}</th></tr></thead>
            <tbody><tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:30px">${t('Loading…')}</td></tr></tbody>
          </table>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:16px">
          <span id="aud-summary" style="color:var(--text-muted);font-size:12px"></span>
          <div>
            <button class="ret-btn ret-btn-ghost ret-btn-sm" id="aud-prev" onclick="RetailSystem._auditPage(-1)">${t('‹ Prev')}</button>
            <button class="ret-btn ret-btn-ghost ret-btn-sm" id="aud-next" style="margin-left:6px" onclick="RetailSystem._auditPage(1)">${t('Next ›')}</button>
          </div>
        </div>
      </div>`;
    await this._loadAuditLog();
  },

  async _loadAuditLog() {
    const s = this._auditLog;
    const qs = new URLSearchParams();
    qs.set('page', s.page);
    qs.set('limit', s.limit);
    if (s.date_from) qs.set('date_from', s.date_from);
    if (s.date_to)   qs.set('date_to', s.date_to);
    if (s.action)    qs.set('action', s.action);
    if (s.entity)    qs.set('entity', s.entity);

    const tbody = document.querySelector('#aud-table tbody');
    const summary = document.getElementById('aud-summary');
    try {
      const res = await this._get(`/api/sub/retail/audit-log?${qs.toString()}`);
      if (res.status !== 'success') {
        // Reachable if a non-admin device somehow lands on this page directly
        // (URL typed by hand, bookmark, etc) -- the nav entry is hidden, but
        // the server's own _is_admin_device check (list_audit_log) is what
        // actually enforces this, so show its real message rather than a
        // generic "no results" that would misrepresent a permission denial.
        if (tbody) tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--state-danger-text);padding:30px">${this._esc(res.message || t('Could not load the audit log.'))}</td></tr>`;
        if (summary) summary.textContent = '';
        return;
      }
      const data = res.data || [];
      const meta = res.meta || {};
      this._populateAuditFilterOptions(meta.actions || [], meta.entities || []);

      if (!tbody) return;
      if (!data.length) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:30px">${t('No audit entries match these filters.')}</td></tr>`;
      } else {
        tbody.innerHTML = data.map(r => `<tr>
          <td style="color:var(--text-muted);white-space:nowrap">${this._bdi(this._auditTimestamp(r.timestamp))}</td>
          <!-- Bidi-isolated for the same reason the sale detail's till id is.
               inventory_movements has no viewer anywhere in this frontend
               (its created_by is written on every stock movement and read
               back nowhere), so this column is the nearest live "who did it"
               surface the product has -- and it is a raw audit_log.user_id
               UUID, in a monospace cell, in a table that flips to RTL in
               Arabic. Left unisolated it reorders around its own hyphens. -->
          <td style="font-family:monospace;font-size:12px">${r.user_id ? this._bdi(r.user_id) : this._esc('system')}</td>
          <td>${this._badge(this._esc(r.action || ''), this._auditActionColor(r.action))}</td>
          <td style="color:var(--text-muted)">${this._esc(r.entity || '—')}${r.entity_id != null ? ' #' + this._esc(r.entity_id) : ''}</td>
          <td style="color:var(--text-muted);max-width:360px;white-space:normal">${this._esc(r.details || '')}</td>
        </tr>`).join('');
      }

      const total = meta.total || 0;
      const limit = meta.limit || s.limit;
      const page  = meta.page || s.page;
      s.totalPages = Math.max(1, Math.ceil(total / limit));
      if (summary) summary.textContent = `${t('Page')} ${page} ${t('of')} ${s.totalPages} · ${total} ${t('total entries')}`;
      const prevBtn = document.getElementById('aud-prev');
      const nextBtn = document.getElementById('aud-next');
      if (prevBtn) prevBtn.disabled = page <= 1;
      if (nextBtn) nextBtn.disabled = page >= s.totalPages;
    } catch (e) {
      console.error('Audit log load failed', e);
      if (tbody) tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--state-danger-text);padding:30px">${t('Could not load the audit log.')}</td></tr>`;
    }
  },

  // Only builds each <select>'s options once per page visit (guarded by
  // dataset.built) -- meta.actions/meta.entities are identical across pages
  // of the SAME filter set, and rebuilding on every _loadAuditLog() call
  // would blow away whatever the user just picked.
  _populateAuditFilterOptions(actions, entities) {
    const s = this._auditLog;
    const actionSel = document.getElementById('aud-action');
    const entitySel = document.getElementById('aud-entity');
    if (actionSel && !actionSel.dataset.built) {
      actionSel.innerHTML = `<option value="">${t('All actions')}</option>` +
        actions.map(a => `<option value="${this._esc(a)}">${this._esc(a)}</option>`).join('');
      actionSel.value = s.action;
      actionSel.dataset.built = '1';
    }
    if (entitySel && !entitySel.dataset.built) {
      entitySel.innerHTML = `<option value="">${t('All entities')}</option>` +
        entities.map(en => `<option value="${this._esc(en)}">${this._esc(en)}</option>`).join('');
      entitySel.value = s.entity;
      entitySel.dataset.built = '1';
    }
  },

  _applyAuditFilters() {
    const s = this._auditLog;
    s.date_from = document.getElementById('aud-from').value;
    s.date_to   = document.getElementById('aud-to').value;
    s.action    = document.getElementById('aud-action').value;
    s.entity    = document.getElementById('aud-entity').value;
    s.page = 1;
    this._loadAuditLog();
  },

  _clearAuditFilters() {
    document.getElementById('aud-from').value = '';
    document.getElementById('aud-to').value = '';
    document.getElementById('aud-action').value = '';
    document.getElementById('aud-entity').value = '';
    this._applyAuditFilters();
  },

  _auditPage(delta) {
    const s = this._auditLog;
    const next = s.page + delta;
    if (next < 1 || next > (s.totalPages || 1)) return;
    s.page = next;
    this._loadAuditLog();
  },

  _auditActionColor(action) {
    const a = String(action || '');
    if (a.includes('DELETE') || a.includes('VOID')) return 'red';
    if (a.includes('CREATE')) return 'green';
    if (a.includes('UPDATE') || a.includes('ADJUST')) return 'blue';
    if (a.includes('PAYMENT')) return 'purple';
    return 'yellow';
  },

  // audit_log.timestamp is SQLite's raw CURRENT_TIMESTAMP default -- UTC,
  // formatted "YYYY-MM-DD HH:MM:SS" with no 'T' and no zone suffix. Handing
  // that string to `new Date()` as-is gets parsed as LOCAL time (silently
  // wrong by the server's UTC offset) -- the exact class of bug
  // process_return's created_at comment above warns about, just the mirror
  // case: that route writes local time deliberately; this table stores UTC
  // deliberately (it's an append-only audit trail, never netted by local
  // calendar day), so the fix here is on the READ side, not the write side.
  //
  // LOCALE-NEUTRAL on purpose, and this is the one date in the product that
  // should be.
  //
  // It used to call a bare `toLocaleString()`, which formats against the
  // OPERATING SYSTEM's locale rather than the language the operator chose in
  // this app -- so an Arabic shop on an English Windows read
  // "8/22/2026, 2:00:00 PM" in the middle of an otherwise-Arabic audit log,
  // including the literal "PM", which no catalog can rescue because it never
  // passes through t().
  //
  // The obvious fix is to follow the active language, the way _localeDate()
  // does for the dashboard heading. That is right for a heading and wrong for
  // an audit trail. An audit row is EVIDENCE: it gets read next to other rows,
  // compared across devices, quoted in a support conversation, and sorted. A
  // format that changes shape with the reader's language makes two people
  // describing the same event disagree about when it happened, and "2:00 PM"
  // versus "14:00" versus an Arabic-numeral rendering is exactly the ambiguity
  // an audit log exists to remove.
  //
  // So: fixed `YYYY-MM-DD HH:MM:SS`, in the browser's local zone (the stored
  // value is UTC -- see the comment above about the 'Z' suffix). Unambiguous,
  // sortable as text, identical for every operator, and containing no word any
  // catalog would need to translate.
  _auditTimestamp(ts) {
    if (!ts) return '—';
    const d = new Date(String(ts).replace(' ', 'T') + 'Z');
    if (isNaN(d.getTime())) return this._esc(ts);
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
           `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  },

  // ── RETURNS ───────────────────────────────────────────────────────────────
  async _renderReturns(c) {
    this._injectStyles();
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">Returns & Refunds</h2>
        <button class="sub-btn-primary" onclick="RetailSystem._openCreateReturn()">+ Process Return</button>
      </div>
      <div class="sub-chart-card">
        <div style="overflow-x:auto">
          <table class="ret-table" id="ret-table">
            <thead><tr><th>Return #</th><th>Original Sale</th><th>Customer</th><th>Refund Method</th><th>Amount</th><th>Date</th></tr></thead>
            <tbody><tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:30px">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>`;
    await this._loadReturns();
  },

  async _loadReturns() {
    try {
      const data = (await this._get('/api/sub/retail/returns')).data || [];
      const tbody = document.querySelector('#ret-table tbody');
      if (!tbody) return;
      if (!data.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:30px">No returns yet.</td></tr>';
        return;
      }
      // Three isolated runs per row: two document identifiers and a timestamp.
      // The timestamp is the one that reorders outright (see the Date-column
      // note in _renderDashboard); the identifiers keep their neutral
      // characters (`-`) on the correct side of their digits.
      tbody.innerHTML = data.map(r => `<tr>
        <td style="font-family:monospace;color:var(--sub-accent)">${this._bdi(r.return_number||'')}</td>
        <td style="color:var(--text-muted)">${r.sale_number ? this._bdi(r.sale_number) : '—'}</td>
        <td>${r.customer_name||'Walk-in'}</td>
        <td>${this._badge(r.refund_method||'cash','blue')}</td>
        <td style="font-weight:600;color:var(--text-money-negative)">${this._fmt(r.refund_amount)}</td>
        <td style="color:var(--text-muted)">${this._bdi((r.created_at||'').slice(0,16))}</td>
      </tr>`).join('');
    } catch(e) { console.error(e); }
  },

  async _openCreateReturn() {
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'ret-return-modal';
    overlay.innerHTML = `
      <div class="ret-modal ret-modal-wide">
        <h3>↩️ Process Return</h3>
        <div class="ret-field-row">
          <div class="ret-field"><label>Sale / Receipt Number *</label>
            <div style="display:flex;gap:8px">
              <input id="ret-sale-search" placeholder="e.g. S-1234567890" style="flex:1;background:var(--surface-sunken);border:1px solid var(--border-default);border-radius:8px;color:var(--text-primary);padding:10px 14px;font-size:14px;outline:none" />
              <button class="ret-btn ret-btn-ghost" onclick="RetailSystem._findSaleForReturn()">Lookup</button>
            </div>
          </div>
          <div class="ret-field"><label>Refund Method</label>
            <select id="ret-refund-method">
              <option value="cash">Cash</option><option value="card">Card</option>
              <option value="store_credit">Store Credit</option>
            </select>
          </div>
        </div>
        <div class="ret-field"><label>Return Reason</label>
          <select id="ret-reason">
            <option>Customer return</option><option>Defective / damaged</option>
            <option>Wrong item</option><option>Not as described</option><option>Changed mind</option>
          </select>
        </div>
        <div id="ret-sale-items" style="margin-top:16px"></div>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('ret-return-modal').remove()">Cancel</button>
          <button class="ret-btn ret-btn-danger" id="ret-save-btn" onclick="RetailSystem._saveReturn()">Process Refund</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if(e.target===overlay) overlay.remove(); });
    this._returnItems = [];
    this._returnSaleId = null;
    document.getElementById('ret-sale-search')?.focus();
    document.getElementById('ret-sale-search')?.addEventListener('keydown', e => {
      if (e.key==='Enter') RetailSystem._findSaleForReturn();
    });
  },

  async _findSaleForReturn() {
    const saleNum = document.getElementById('ret-sale-search')?.value.trim();
    if (!saleNum) { SubsystemApp.showToast('Enter a receipt number','error'); return; }
    try {
      const recent = (await this._get('/api/sub/retail/sales/recent?limit=200')).data || [];
      const sale   = recent.find(s => s.sale_number.toLowerCase()===saleNum.toLowerCase());
      if (!sale) { SubsystemApp.showToast('Sale not found','error'); return; }
      const full = (await this._get(`/api/sub/retail/sales/${sale.id}`)).data || {};
      this._returnSaleId = sale.id;
      const items = full.items || [];
      const container = document.getElementById('ret-sale-items');
      if (!container) return;
      container.innerHTML = `
        <div style="color:var(--text-primary);font-weight:600;margin-bottom:10px">Items from ${saleNum}</div>
        <table class="ret-table">
          <thead><tr><th><input type="checkbox" id="ret-check-all" onchange="document.querySelectorAll('.ret-item-cb').forEach(cb=>cb.checked=this.checked)" /></th><th>Product</th><th>Sold Qty</th><th>Return Qty</th><th>Unit Price</th></tr></thead>
          <tbody>${items.map((item,i)=>`<tr>
            <td><input type="checkbox" class="ret-item-cb" data-idx="${i}" data-pid="${item.product_id}" data-price="${item.unit_price}" data-max="${item.quantity}" /></td>
            <td>${item.product_name}</td>
            <td>${item.quantity}</td>
            <td><input type="number" class="ret-item-qty" data-idx="${i}" value="${item.quantity}" min="1" max="${item.quantity}" style="width:60px;background:var(--surface-sunken);border:1px solid var(--border-default);border-radius:5px;color:var(--text-primary);padding:4px 8px;text-align:center;outline:none" /></td>
            <td>${this._fmt(item.unit_price)}</td>
          </tr>`).join('')}</tbody>
        </table>`;
    } catch(e) { SubsystemApp.showToast('Error looking up sale','error'); }
  },

  async _saveReturn() {
    if (!this._returnSaleId) { SubsystemApp.showToast('Look up a sale first','error'); return; }
    const checked = [...document.querySelectorAll('.ret-item-cb:checked')];
    if (!checked.length) { SubsystemApp.showToast('Select items to return','error'); return; }
    const items = checked.map(cb => {
      const idx = cb.dataset.idx;
      const qty = +document.querySelectorAll('.ret-item-qty')[idx]?.value || 1;
      const price = +cb.dataset.price;
      return { product_id:cb.dataset.pid, quantity:qty, unit_price:price, line_total:qty*price };
    });
    const btn = document.getElementById('ret-save-btn');
    if (btn) { btn.disabled=true; btn.textContent='Processing…'; }
    try {
      const d = await this._post('/api/sub/retail/returns', {
        sale_id: this._returnSaleId,
        reason: document.getElementById('ret-reason')?.value||'Customer return',
        refund_method: document.getElementById('ret-refund-method')?.value||'cash',
        items,
      });
      if (d.status==='success') {
        SubsystemApp.showToast(`Return processed — ${d.data.return_number} · Refund: ${this._fmt(d.data.refund_amount)}`,'success');
        document.getElementById('ret-return-modal')?.remove();
        this._loadReturns();
      } else { SubsystemApp.showToast(d.message||'Error','error'); if(btn){btn.disabled=false;btn.textContent='Process Refund';} }
    } catch(e) { if(btn){btn.disabled=false;btn.textContent='Process Refund';} }
  },

  // ── SALES HISTORY ────────────────────────────────────────────────────────
  // Dedicated invoices screen. Real hands-on-testing feedback: "The invoices
  // are there, but you can't add details to them. There's no dedicated
  // screen for invoices where I can see each sold invoice, its contents,
  // and whether to edit or reprint it." GET /sales/recent and GET
  // /sales/<id> already existed and already carried every field a receipt
  // needs (proof: _printReceipt below already knows how to render a full
  // receipt from this exact shape) -- the checkout-time receipt modal
  // (_showReceipt) was always the only place that data was ever shown, and
  // it auto-dismisses after 8s with no way to bring it back. The actual gap
  // was purely "no page ever lists past sales or re-opens one," which is
  // what this section adds. Editing a completed sale is intentionally NOT
  // offered here -- financial records like this should not be silently
  // mutable; a correction should go through the existing Returns flow (a
  // server-authoritative reversal, AUDIT-004), not an in-place edit.
  // ── The capability split this screen has to respect ───────────────────────
  //
  // GET /sales/recent is the one route a reports screen reads that is NOT
  // decorated @mt_require_capability(CAP_REPORTS), and deliberately so: the
  // returns counter resolves a receipt number through it before every refund
  // (_findSaleForReturn, `?limit=200`), and retail.refund is a CASHIER DEFAULT.
  // Gating the route would refuse a cashier a lookup the product grants them.
  //
  // So recent_sales splits instead (read its docstring, it is the contract):
  //
  //   TILL HALF, open at any capability
  //     a plain recent page, and `q` -- a receipt number is a single-sale
  //     question, and looking one up to take a return is the till's job.
  //   BOOK HALF, retail.reports only
  //     `date_from` / `date_to`, refused INDIVIDUALLY (either bound alone
  //     reaches the whole history), and a tighter `limit` cap.
  //
  // A split only works if both sides know about it, and this side did not.
  //
  // ── What the refused caller saw, which was not an error message ───────────
  //
  // `_fetch` re-throws only on 401. A 403 comes back as a RESOLVED response, so
  // the old `(await this._get(url)).data || []` read `undefined` off the refusal
  // envelope, substituted `[]`, and rendered "No sales found." with a count of
  // zero. A cashier who touched a date input was not shown an error and was not
  // shown a blank screen: they were told the shop had sold nothing in the range
  // they asked about. A false statement about the books, presented as an answer.
  // (The `catch` arm existed too and was `console.error(e)` alone -- that path
  // is 401s and transport failures, and it showed the user nothing at all.)
  //
  // Both halves are fixed here, and the gate is in _loadSalesHistory as well as
  // in the markup on purpose. Hiding the inputs is not enough: they are re-read
  // on every reload (`onchange`, the debounced search, _clearSalesFilters) from
  // a DOM this function does not own, and _mayBrowseTheSalesBook() is the one
  // place the question is asked.

  // Rows this screen asks for, per half of the split above.
  //:
  // Both mirror a backend constant -- _SH_TILL_LIMIT is
  // TILL_SALES_LOOKUP_MAX_LIMIT, and _SH_PAGE_LIMIT must stay at or under
  // SALES_HISTORY_MAX_LIMIT (500). Named properties rather than literals
  // because the number appears twice (the request, and the "is this page
  // truncated" test) and the two literals had already drifted from what the
  // server would actually serve: the client asked for 300, a cashier was
  // served 200 by the till cap, and the count line -- comparing against its own
  // 300 -- reported those 200 rows as the shop's entire history.
  // retail_attribution_i18n_test.py::test_the_client_page_sizes_match_the_server_caps
  // reads these two off the running object and asserts them against the
  // backend's own constants, which is the only place that agreement is visible.
  _SH_PAGE_LIMIT: 300,
  _SH_TILL_LIMIT: 200,

  // Asked, never cached. A stored flag would be one more thing that can be
  // stale when a reload arrives from a control the render did not create.
  // Fails OPEN when the shell cannot answer (standalone load, or a session that
  // carried no capability list) -- the same contract as every other capability
  // check in this file; see SubsystemApp.hasCapability's own comment.
  _mayBrowseTheSalesBook() {
    return !window.SubsystemApp || SubsystemApp.hasCapability('retail.reports');
  },

  async _renderSalesHistory(c) {
    this._injectStyles();
    const mayBrowse = this._mayBrowseTheSalesBook();
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title"><span aria-hidden="true">🧾</span> <span>${t('Sales History')}</span></h2>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <input class="ret-search" id="sh-search" placeholder="${t('Search receipt # or customer…')}" oninput="RetailSystem._debounceSalesSearch()" />
          ${mayBrowse ? `
          <input type="date" class="ret-date" id="sh-date-from" title="${this._esc(t('From date'))}"
            onchange="RetailSystem._loadSalesHistory()" />
          <input type="date" class="ret-date" id="sh-date-to" title="${this._esc(t('To date'))}"
            onchange="RetailSystem._loadSalesHistory()" />` : ''}
          <button class="ret-btn ret-btn-ghost" onclick="RetailSystem._clearSalesFilters()">${t('Clear')}</button>
        </div>
      </div>
      ${mayBrowse ? '' : `
      <!-- Not a blank space where two inputs used to be. A control that
           silently disappears reads as a build that forgot the feature, and
           the honest thing -- the thing the rest of this capability work
           exists to do -- is to say who may use it and what this user can do
           instead. What they can do instead is the point: finding a receipt to
           take a return is precisely the job this screen keeps doing for them. -->
      <p style="color:var(--text-muted);font-size:12px;margin:0 0 14px;line-height:1.7">
        ${t('Browsing sales by date is limited to managers and the store owner. Search by receipt number or customer name to find a sale.')}
      </p>`}
      <div class="sub-chart-card">
        <div style="overflow-x:auto">
          <table class="ret-table" id="sh-table">
            <thead><tr><th>${t('Receipt #')}</th><th>${t('Date')}</th><th>${t('Customer')}</th><th>${t('Items')}</th><th>${t('Payment')}</th><th>${t('Total')}</th><th>${t('Status')}</th></tr></thead>
            <tbody><tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:30px">${t('Loading…')}</td></tr></tbody>
          </table>
        </div>
        <div id="sh-count" style="color:var(--text-muted);font-size:12px;margin-top:10px"></div>
      </div>`;
    await this._loadSalesHistory();
  },

  // Debounced so every keystroke in the search box doesn't fire its own
  // request -- matches _filterProducts' instant client-side filtering in
  // spirit, but this list is server-searched (see /sales/recent's q param)
  // rather than client-filtered, so a company's full sales history is
  // searchable, not just whatever page happened to be loaded already.
  _debounceSalesSearch() {
    clearTimeout(this._shSearchTimer);
    this._shSearchTimer = setTimeout(() => this._loadSalesHistory(), 300);
  },

  _clearSalesFilters() {
    // `dateTo`, not `t`. The old local was named `t`, which SHADOWED the global
    // translation function for the whole body of this method -- harmless while
    // nothing here was translated, and a trap the moment anything is: a
    // translation call would have become a call on an <input> element.
    const search = document.getElementById('sh-search');    if (search) search.value = '';
    // Both may be absent: without retail.reports this screen does not render
    // them at all (see _renderSalesHistory).
    const dateFrom = document.getElementById('sh-date-from'); if (dateFrom) dateFrom.value = '';
    const dateTo   = document.getElementById('sh-date-to');   if (dateTo) dateTo.value = '';
    this._loadSalesHistory();
  },

  _salesHistoryMessage(tbody, text, isError) {
    const color = isError ? 'var(--state-danger-text)' : 'var(--text-muted)';
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:${color};padding:30px">${this._esc(text)}</td></tr>`;
  },

  async _loadSalesHistory() {
    const tbody = document.querySelector('#sh-table tbody');
    if (!tbody) return;
    const countEl = document.getElementById('sh-count');
    const mayBrowse = this._mayBrowseTheSalesBook();
    const limit = mayBrowse ? this._SH_PAGE_LIMIT : this._SH_TILL_LIMIT;
    const q = document.getElementById('sh-search')?.value.trim() || '';
    const params = new URLSearchParams({ limit: String(limit) });
    if (q) params.set('q', q);
    if (mayBrowse) {
      // Read INSIDE the branch. Reading them unconditionally and filtering
      // later is the same bug with an extra step -- a stale input left in the
      // DOM by a previous render would still reach the query string.
      const from = document.getElementById('sh-date-from')?.value || '';
      const to   = document.getElementById('sh-date-to')?.value || '';
      if (from) params.set('date_from', from);
      if (to)   params.set('date_to', to);
    }
    try {
      const res = await this._get(`/api/sub/retail/sales/recent?${params}`);
      if (!res || res.status !== 'success') {
        // An error envelope is NOT an empty result set, and the difference is
        // the whole defect: this branch used to not exist, `res.data` was
        // undefined, and a refusal rendered as "No sales found." The server's
        // own message goes through t() -- it is a fixed English sentence from
        // mt_auth (CAPABILITY_DENIED_MESSAGE), which is a catalog key here, so
        // an Arabic page reads an Arabic refusal rather than one English
        // sentence in the middle of an RTL table.
        this._salesHistoryMessage(tbody, t((res && res.message) || 'Could not load sales history.'), true);
        if (countEl) countEl.textContent = '';
        return;
      }
      const data = res.data || [];
      if (countEl) {
        // Compared against the limit ACTUALLY requested, not a second literal.
        // A page the server filled to the brim is a page with more behind it,
        // and saying so is the difference between "200 sales" (false) and "200
        // most recent sales shown" (true).
        const truncated = data.length >= limit;
        // Two keys rather than `sale${n===1?'':'s'}`. Suffixing an 's' onto a
        // translated word is English grammar hardcoded into the render, and the
        // old line did exactly that -- correct in English and meaningless in
        // Arabic, which does not form plurals that way. Two keys let each
        // catalog answer for itself. (Arabic distinguishes more cases than two;
        // one plural form for "many" is a deliberate simplification, not an
        // oversight -- it reads correctly, which "1 sales" did not.)
        const note = !truncated
          ? (data.length === 1 ? t('sale shown') : t('sales shown'))
          : (mayBrowse
              ? t('most recent sales shown. Narrow with search or a date range to reach older sales.')
              // Not "or a date range": that is the one control this caller is
              // refused, and advice to use it would be advice to collect a 403.
              : t('most recent sales shown. Search by receipt number or customer name to reach older sales.'));
        countEl.innerHTML = `<bdi>${data.length}</bdi> ${note}`;
      }
      if (!data.length) {
        this._salesHistoryMessage(tbody, t('No sales found.'), false);
        return;
      }
      const statusColor = { completed:'green', pending:'yellow', cancelled:'red', voided:'red' };
      // `this._esc(t(...))`, matching the identical title on the dashboard's and
      // the customer modal's sale rows. This one site had the raw t() -- and a
      // translation string is not a constant: it comes from a locale JSON, so
      // an apostrophe in the Arabic or French rendering ("Voir l'facture") ends
      // the attribute early and everything after it becomes markup. Escaping
      // here costs nothing and removes the difference between the three.
      tbody.innerHTML = data.map(s => `<tr style="cursor:pointer" onclick="RetailSystem._viewSale(${s.id})" title="${this._esc(t('View invoice'))}">
        <td style="font-family:monospace;color:var(--sub-accent)">${this._saleOpenerButton(s.id, s.sale_number)}</td>
        <!-- Same unisolated two-number run as the dashboard's Date column;
             see the long note there. -->
        <td style="color:var(--text-muted)">${this._bdi((s.created_at||'').slice(0,16))}</td>
        <td>${s.customer_name||t('Walk-in')}</td>
        <td style="color:var(--text-muted)">${s.item_count||0}</td>
        <td>${this._badge(s.payment_method||'cash', s.payment_method==='cash'?'green':'blue')}</td>
        <td style="font-weight:700">${this._money(s.total)}</td>
        <td>${this._badge(s.status||'completed', statusColor[s.status]||'green')}</td>
      </tr>`).join('');
    } catch(e) {
      // 401 (re-thrown by _fetch) or a transport failure. This used to be
      // `console.error(e)` and nothing else, so the "Loading…" placeholder the
      // render put in the table stayed there forever: the screen had given up
      // and the only trace was in a console the shopkeeper does not have open.
      console.error('Sales history load failed', e);
      this._salesHistoryMessage(tbody, t('Could not load sales history.'), true);
      if (countEl) countEl.textContent = '';
    }
  },

  // Sale detail / receipt view. Reads the full line-item breakdown from
  // GET /sales/<id> -- its `items` already carry a resolved product_name/sku
  // (via that route's JOIN), unlike POST /sales's own response `lines`,
  // which only ever resolves product_id (see _reprintSale's comment below).
  //
  // ── Why v13 attribution surfaces HERE and not on the list screens ────────
  //
  // This is the view a discrepancy lands in. Somebody is looking at one
  // specific transaction and asking who rang it and at which terminal --
  // a short till, a disputed refund, a customer complaint about a price. The
  // row already carries `terminal_id` and `actor_user_uid` (GET /sales/<id>
  // selects `s.*`, so both arrive with no backend change), so answering the
  // question here costs one grid column and one request that was already
  // being made.
  //
  // The Sales History table and the dashboard's recent-transactions list were
  // deliberately left alone. Both are scan-many-rows surfaces already inside
  // an `overflow-x:auto` wrapper on a till screen; two more columns push the
  // totals that ARE the point of those tables off the visible area, on every
  // row, to answer a question that is asked about one row at a time and is
  // one click away here. Attribution that is everywhere is attribution nobody
  // reads.
  //
  // The `Cashier` label is kept rather than renamed to `Employee`: it is the
  // POS domain word and it is already in both catalogs. What changed is the
  // value under it, twice.
  //
  // First it stopped being `sale.cashier` raw -- `session['mt_user_id']`, a
  // bare UUID on every sale this product has ever written, presented under a
  // label that implies a person's name.
  //
  // Then it stopped falling back to `sale.cashier` AT ALL when
  // `actor_user_uid` is absent, which is the change this wave made and the
  // less obvious of the two. Those two columns are different identity spaces:
  // `cashier` holds registry `users.ID`, `actor_user_uid` holds `users.UID`,
  // and both are uuid4 strings. Rendering either one in this cell, in the same
  // shape, under the same label, sent a manager who copied the shown fragment
  // to look someone up straight at the wrong column, where they found nobody
  // while the screen looked entirely correct -- the same wrong-column trap the
  // by-employee route's own comment warns about. It also made the two screens
  // contradict each other: revenue_by_employee() groups on `actor_user_uid`
  // and refuses to read `cashier` (metrics.py rule 8, "money grouped by free
  // text would look authoritative and be worthless"), so a sale the report
  // counts in its unattributed bucket used to read as attributed here.
  //
  // The free text is not destroyed -- it is v13's only surviving evidence of
  // who the shop BELIEVED rang the sale -- it is demoted to the cell's tooltip,
  // under its own column name. See _attributionCell().
  async _viewSale(saleId) {
    try {
      const resp  = (await this._get(`/api/sub/retail/sales/${saleId}`)).data || {};
      const sale  = resp.sale || {};
      const items = resp.items || [];
      // Cached for _reprintSale -- avoids a second round-trip just to print
      // what's already on screen, and keeps the print action working even
      // if this modal was opened from a stale/cached table row.
      this._lastViewedSale = { sale, items };
      const statusColor = { completed:'green', pending:'yellow', cancelled:'red', voided:'red' };
      const overlay = document.createElement('div');
      overlay.className = 'ret-modal-overlay';
      overlay.id = 'ret-sale-modal';
      overlay.innerHTML = `
        <div class="ret-modal ret-modal-wide">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
            <div>
              <h3 style="margin:0">${t('Invoice')} ${this._bdi(sale.sale_number||'')}</h3>
              <p style="color:var(--text-muted);margin:4px 0 0;font-size:13px">${this._bdi((sale.created_at||'').slice(0,16))}</p>
            </div>
            <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="this.closest('.ret-modal-overlay').remove()">✕ Close</button>
          </div>
          <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:20px">
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">${t('Customer')}</div><div style="color:var(--text-primary);font-weight:600">${this._esc(sale.customer_name||'Walk-in')}</div></div>
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">${t('Cashier')}</div><div style="color:var(--text-primary);font-weight:600">${this._attributionCell(this._saleIdentityRow(sale), sale.cashier)}</div></div>
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">${t('Till')}</div><div style="color:var(--text-primary);font-weight:600">${this._attribution(sale.terminal_id)}</div></div>
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">${t('Payment')}</div><div>${this._badge(sale.payment_method||'cash','blue')}</div></div>
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">${t('Status')}</div><div>${this._badge(sale.status||'completed', statusColor[sale.status]||'green')}</div></div>
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">${t('Total')}</div><div style="color:var(--text-money);font-weight:700">${this._fmt(sale.total)}</div></div>
          </div>
          ${(!sale.actor_user_uid && !sale.terminal_id)
            ? `<p style="color:var(--text-faint);font-size:12px;margin:-8px 0 16px">${t('Sales recorded before this release show no employee or till.')}</p>`
            : ''}
          <table class="ret-table">
            <thead><tr><th>Product</th><th>SKU</th><th>Qty</th><th>Unit Price</th><th>Discount</th><th>Tax</th><th>Line Total</th></tr></thead>
            <tbody>${items.map(i=>`<tr>
              <td>${i.product_name||('#'+i.product_id)}</td>
              <td style="font-family:monospace;color:var(--text-muted)">${i.sku||'—'}</td>
              <td>${i.quantity}</td>
              <td>${this._fmt(i.unit_price)}</td>
              <td style="color:var(--text-muted)">${i.discount_pct?i.discount_pct+'%':'—'}</td>
              <td style="color:var(--text-muted)">${i.tax_rate?i.tax_rate+'%':'—'}</td>
              <td style="font-weight:600">${this._fmt(i.line_total)}</td>
            </tr>`).join('')}</tbody>
          </table>
          <div style="display:flex;justify-content:flex-end;margin-top:16px">
            <div style="width:260px">
              <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px"><span style="color:var(--text-muted)">Subtotal</span><span style="color:var(--text-money)">${this._fmt(sale.subtotal)}</span></div>
              ${sale.discount_amount>0?`<div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px"><span style="color:var(--text-muted)">Discount</span><span style="color:var(--text-money-negative)">-${this._fmt(sale.discount_amount)}</span></div>`:''}
              ${sale.tax_amount>0?`<div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px"><span style="color:var(--text-muted)">Tax</span><span style="color:var(--text-money)">${this._fmt(sale.tax_amount)}</span></div>`:''}
              <div style="display:flex;justify-content:space-between;font-size:15px;font-weight:700;border-top:1px dashed var(--border-default);padding-top:8px;margin-top:4px"><span style="color:var(--text-primary)">Total</span><span style="color:var(--text-money)">${this._fmt(sale.total)}</span></div>
              <div style="display:flex;justify-content:space-between;font-size:13px;margin-top:6px"><span style="color:var(--text-muted)">Paid</span><span style="color:var(--text-money)">${this._fmt(sale.amount_paid)}</span></div>
              ${sale.change_amount>0?`<div style="display:flex;justify-content:space-between;font-size:13px"><span style="color:var(--text-muted)">Change</span><span style="color:var(--text-money-positive)">${this._fmt(sale.change_amount)}</span></div>`:''}
              ${sale.due_date?`<div style="display:flex;justify-content:space-between;font-size:13px"><span style="color:var(--text-muted)">Due date</span><span style="color:var(--state-warning-text)">${sale.due_date}</span></div>`:''}
            </div>
          </div>
          ${sale.notes?`<p style="color:var(--text-muted);margin-top:14px;font-size:13px">Notes: ${sale.notes}</p>`:''}
          <div class="ret-modal-footer">
            <button class="ret-btn ret-btn-primary" onclick="RetailSystem._reprintSale()">🖨️ Reprint Receipt</button>
          </div>
        </div>`;
      document.body.appendChild(overlay);
      overlay.addEventListener('click', e => { if(e.target===overlay) overlay.remove(); });
    } catch(e) { SubsystemApp.showToast('Could not load invoice','error'); }
  },

  // Reuses the existing checkout-time receipt printer (_printReceipt, Wave
  // 1B Part O/P above) rather than a second print implementation -- adapts
  // GET /sales/<id>'s {sale, items} shape into the `saleData` shape
  // _printReceipt already expects from POST /sales's response: same field
  // set, just renamed (change_amount -> change, items -> lines) and with
  // `name` resolved onto each line from items.product_name (POST /sales's
  // own `lines` never carries a product name -- only product_id -- so a
  // receipt reprinted from here actually shows product names where an
  // immediate post-checkout print would not). einvoice clearance status
  // isn't returned by the detail route, so that block is simply omitted;
  // _printReceipt / _einvoiceReceiptBlock already no-op cleanly when
  // saleData.einvoice is absent -- an e-invoicing-disabled install's
  // reprinted receipt is unaffected either way.
  async _reprintSale() {
    const cached = this._lastViewedSale;
    if (!cached) return;
    const { sale, items } = cached;
    const saleData = {
      sale_number:     sale.sale_number,
      created_at:      sale.created_at,
      subtotal:        sale.subtotal,
      discount_amount: sale.discount_amount,
      tax_amount:      sale.tax_amount,
      total:           sale.total,
      amount_paid:     sale.amount_paid,
      change:          sale.change_amount,
      lines: items.map(i => ({ name: i.product_name || ('#'+i.product_id), quantity: i.quantity, line_total: i.line_total })),
    };
    await this._printReceipt(saleData);
  },

  // ── REPORTS ───────────────────────────────────────────────────────────────
  async _renderReports(c) {
    this._injectStyles();

    // Capability gate, mirroring _renderDashboard's cashier landing above --
    // read that comment first; the reasoning is identical and the mechanism
    // is deliberately the same one.
    //
    // Every widget on this page reads a route decorated
    // @mt_require_capability(CAP_REPORTS): report_sales_trend,
    // report_top_products, report_payment_methods, report_summary,
    // report_by_branch. ROLE_CAPABILITIES gives a cashier {sell, refund,
    // cash.close} and nothing else, so for a cashier this screen was six
    // requests (the branch list plus five gated report routes) and five 403s,
    // on every visit. Measured, not assumed -- see
    // retail_reports_capability_gate_test.js, whose red run listed all six by
    // URL.
    //
    // Hiding the nav entry (app-shell.js, added alongside this) is NOT the
    // enforcement, which is why this guard exists as well. AuraRouter
    // persists the last-viewed section into the URL hash and _navigate()
    // replays it on the next launch, so a shared till where a manager last
    // opened Reports drops the next cashier onto this screen with no nav
    // click in between. The render function is the one choke point every
    // route into this screen passes through.
    //
    // Returning before the fetches, not after them, is the whole point: a
    // screen that fetches, collects a 403 and only then hides has already
    // generated the error it was supposed to prevent.
    //
    // `window.SubsystemApp &&` keeps this file loadable standalone (every
    // *_test.js in products/retail/tests/ that has no SubsystemApp stub), and
    // hasCapability() itself fails open when the session carried no
    // capability list -- so this is inert, not restrictive, on any build that
    // cannot answer the question.
    if (window.SubsystemApp && !SubsystemApp.hasCapability('retail.reports')) {
      return this._renderReportsRestricted(c);
    }

    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">Analytics & Reports</h2>
        <div style="display:flex;gap:8px;align-items:center">
          <!-- class, not an inline style copy of .ret-search's recipe. The
               inline version carried outline:none, and an inline style cannot
               express :focus, so these two -- the only controls on this screen
               -- deleted the browser's focus ring with no way to replace it.
               It also declared a block minimum and no inline one, which is the
               touch floor half of the same defect. See .ret-input in
               _injectStyles(); css/main.css sets both axes from
               --touch-target-min for every consumer of it. -->
          <select id="rep-branch" class="ret-input" onchange="RetailSystem._loadReports()">
            <option value="">${t('All branches')}</option>
          </select>
          <select id="rep-days" class="ret-input" onchange="RetailSystem._loadReports()">
            <option value="7">${t('Last 7 days')}</option>
            <option value="14" selected>${t('Last 14 days')}</option>
            <option value="30">${t('Last 30 days')}</option>
            <option value="90">${t('Last 90 days')}</option>
          </select>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:16px;margin-bottom:8px" id="rep-kpis">
        <div class="ret-kpi"><div class="ret-kpi-label">Revenue</div><div class="ret-kpi-value" id="rep-rev">—</div></div>
        <div class="ret-kpi"><div class="ret-kpi-label">Transactions</div><div class="ret-kpi-value" id="rep-txn">—</div></div>
        <div class="ret-kpi"><div class="ret-kpi-label">Gross Profit</div><div class="ret-kpi-value" id="rep-profit" style="color:var(--text-money-positive)">—</div></div>
        <div class="ret-kpi"><div class="ret-kpi-label">Avg Ticket</div><div class="ret-kpi-value" id="rep-avg">—</div></div>
      </div>
      <!-- This note used to warn that the branch filter reached the two charts
           but NOT the KPI tiles or Payment Methods -- a caveat for a real
           inconsistency rather than a fix for it. Every widget on this page is
           branch_id-filterable server-side now (see report_summary /
           report_payment_methods in retail_api.py), so the note states the one
           remaining, deliberate exception: Revenue by Branch is the
           all-branches comparison and scoping it to one branch is a
           contradiction. Plain English text, which IS the translation key for
           this surface -- AuraI18n.t()/its DOM sweep look up the exact English
           string in locales/ar.json (entry added there alongside this). -->
      <p id="rep-branch-note" style="display:none;color:var(--text-muted);font-size:12px;margin:0 0 16px">
        Branch filter applies to every figure on this page except Revenue by Branch, which always compares all branches.
      </p>
      <div class="sub-chart-card" style="margin-bottom:20px">
        <div class="sub-chart-title" style="margin-bottom:14px">${t('Sales by Employee')}</div>
        <div style="overflow-x:auto">
          <table class="ret-table" id="rep-emp-table">
            <thead><tr>
              <th>${t('Employee')}</th>
              <th style="text-align:right">${t('Transactions')}</th>
              <th style="text-align:right">${t('Revenue')}</th>
              <th style="text-align:right">${t('Avg Ticket')}</th>
            </tr></thead>
            <tbody><tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:24px">${t('Loading…')}</td></tr></tbody>
          </table>
        </div>
        <p id="rep-emp-note" style="display:none;color:var(--text-faint);font-size:12px;margin:12px 0 0">
          ${t('Sales recorded before this release show no employee or till.')}
        </p>
      </div>
      <div style="display:grid;grid-template-columns:2fr 1fr;gap:20px;margin-bottom:20px">
        <div class="sub-chart-card">
          <div class="sub-chart-title" style="margin-bottom:14px">Daily Revenue Trend</div>
          <div style="height:260px"><canvas id="rep-trend"></canvas></div>
        </div>
        <div class="sub-chart-card">
          <div class="sub-chart-title" style="margin-bottom:14px">Payment Methods</div>
          <div style="height:260px"><canvas id="rep-pay"></canvas></div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
        <div class="sub-chart-card">
          <div class="sub-chart-title" style="margin-bottom:14px">Top Selling Products</div>
          <div style="height:260px"><canvas id="rep-top"></canvas></div>
        </div>
        <div class="sub-chart-card">
          <div class="sub-chart-title" style="margin-bottom:14px">Revenue by Branch</div>
          <div style="height:260px"><canvas id="rep-branch-chart"></canvas></div>
        </div>
      </div>`;

    await this._loadBranchFilterOptions();
    await this._loadReports();
  },

  // What a user without `retail.reports` gets instead of six requests and
  // five 403s. Same shape and same wording as _renderCashierLanding above --
  // deliberately the same catalog key too ("Sales totals and reports are
  // limited to managers and the store owner."), because it is the same
  // sentence about the same policy and a second near-identical string would
  // be two things to keep translated and one more chance for them to drift
  // apart. Sends the user somewhere useful rather than leaving them staring
  // at a refusal.
  _renderReportsRestricted(c) {
    this._renderCapabilityRestricted(c, {
      icon: '📊',
      title: t('Reports'),
      message: t('Sales totals and reports are limited to managers and the store owner. Open the till to start ringing sales.'),
    });
  },

  // The same panel for the OTHER retail.reports screen -- see
  // _renderAuditLog's guard. One function rather than two near-identical
  // markup blocks, because the pair drifting apart is how a product ends up
  // refusing the same person in two different tones of voice.
  _renderCapabilityRestricted(c, opts) {
    const o = opts || {};
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">${o.title}</h2>
      </div>
      <div class="sub-chart-card" style="text-align:center;padding:56px 32px">
        <div style="font-size:40px;margin-bottom:14px">${o.icon || '🔒'}</div>
        <h3 style="color:var(--text);margin:0 0 10px;font-size:18px">${o.title}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 24px;line-height:1.7;max-width:420px;margin-left:auto;margin-right:auto">
          ${o.message}
        </p>
        <button class="sub-btn-primary" onclick="SubsystemApp._navigate('pos')">🛒 ${t('Point of Sale')}</button>
      </div>`;
  },

  // ── EXCEPTIONS (the two exception queues, one screen) ───────────────────────
  //
  // Launch-readiness 2026-08-29 ("the two exception queues both need ONE
  // screen, not two", ROADMAP.md). Both queues answer "something happened
  // that the software could not resolve on its own and a human must
  // decide":
  //
  //   * OVERSOLD STOCK -- `stock_exceptions` (Phase 7 stage 7d-i/ii). A sale
  //     merged in from another device pushed the balance negative. Carries
  //     the resolve action the API already has (GET .../inventory/
  //     stock-exceptions, POST .../inventory/stock-exceptions/<id>/resolve).
  //   * DISCARDED CATALOGUE EDITS -- `sync_conflicts` (Phase 6 stage
  //     6a-ii). An incoming catalogue write lost the reject-stale race. GET
  //     .../inventory/sync-conflicts (list_sync_conflicts, retail_api.py).
  //     INFORMATIONAL ONLY -- see that route's own docstring for why there
  //     is no honest "fix" action: the newer value already won, so nothing
  //     here can be re-applied. The remedy this section states in words is
  //     the only remedy that exists -- redo the edit, on a device that is
  //     caught up.
  //
  // Two independent state machines (`this._exceptionQueue.stock` /
  // `.conflicts`), each `checking` / `failed` / `empty` / `rows`, so one
  // queue's fetch failing can never blank the other's -- the same reason
  // Stock Accuracy's own load/paint split exists just above.

  async _renderExceptions(c) {
    this._injectStyles();

    // retail.reports -- the SAME capability both read routes carry
    // (list_stock_exceptions and list_sync_conflicts, both
    // @mt_require_capability(CAP_REPORTS), NEITHER with a company-admin
    // requirement). Deliberately not owner-only, unlike Stock Accuracy:
    // neither route discloses an unpaginated whole-catalogue dump.
    if (window.SubsystemApp && !SubsystemApp.hasCapability('retail.reports')) {
      return this._renderCapabilityRestricted(c, {
        icon: '⚠️',
        title: t('Exceptions'),
        message: t('The exception queues are limited to managers and the store owner. Open the till to start ringing sales.'),
      });
    }

    // 'checking' from the very first paint for BOTH sections, never
    // 'empty' -- the same reasoning _renderStockAccuracy's own comment
    // gives: an unfilled region between the frame and the first response
    // is indistinguishable from "found nothing", which is the failure this
    // screen exists to refuse.
    this._exceptionQueue = {
      stock: { state: 'checking', rows: [], error: '' },
      conflicts: { state: 'checking', rows: [], error: '' },
      registry: { state: 'checking', rows: [], error: '' },
    };
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">${t('Exceptions')}</h2>
      </div>
      <p style="color:var(--text-muted);font-size:13px;margin:0 0 22px;max-width:780px;line-height:1.7">
        ${t('Three queues for things the software could not resolve on its own. A human decides what happens next.')}
      </p>
      <section aria-labelledby="exq-stock-heading" style="margin-bottom:28px">
        <h3 id="exq-stock-heading" class="sub-chart-title" style="margin:0 0 6px">${t('Oversold Stock')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 14px;line-height:1.7;max-width:760px">
          ${t('A sale recorded on another device merged in after this one had already sold the same stock, and the balance went negative. Resolving one writes a real stock correction, so it needs stock-adjust authority.')}
        </p>
        <div id="exq-stock-body">${this._exqStockPanel()}</div>
      </section>
      <section aria-labelledby="exq-conflicts-heading" style="margin-bottom:28px">
        <h3 id="exq-conflicts-heading" class="sub-chart-title" style="margin:0 0 6px">${t('Discarded Catalogue Edits')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 14px;line-height:1.7;max-width:760px">
          ${t('An edit arrived from another device but was discarded because a newer version of the same record had already been saved here. The newer value is the one in use now -- there is nothing to re-apply. If this edit still matters, make it again.')}
        </p>
        <div id="exq-conflicts-body">${this._exqConflictsPanel()}</div>
      </section>
      <section aria-labelledby="exq-registry-heading">
        <h3 id="exq-registry-heading" class="sub-chart-title" style="margin:0 0 6px">${t('Staff Account Conflicts')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 14px;line-height:1.7;max-width:760px">
          ${t('A staff account created on two devices at the same time can collide here instead of disappearing silently. A duplicate needs a human decision; a permission grant waiting on its account will resolve itself once that account finishes syncing.')}
        </p>
        <div id="exq-registry-body">${this._exqRegistryPanel()}</div>
      </section>`;
    await Promise.all([
      this._loadExceptionStock(), this._loadExceptionConflicts(), this._loadExceptionRegistry(),
    ]);
  },

  // ── Loading (one pair per section, mirroring _loadStockAccuracy) ──────────

  async _loadExceptionStock() {
    const eq = this._exceptionQueue || (this._exceptionQueue = {});
    const s = eq.stock || (eq.stock = { state: 'checking', rows: [], error: '' });
    s.state = 'checking';
    s.error = '';
    this._paintExceptionStock();
    try {
      const res = await this._get('/api/sub/retail/inventory/stock-exceptions');
      if (!res || res.status !== 'success' || !res.data) {
        s.state = 'failed';
        s.rows = [];
        s.error = (res && (res.message || res.error)) || t('Could not check for oversold stock.');
      } else {
        const rows = Array.isArray(res.data.exceptions) ? res.data.exceptions : [];
        s.rows = rows;
        s.error = '';
        s.state = rows.length ? 'rows' : 'empty';
      }
    } catch (e) {
      console.error('Exceptions: oversold-stock load failed', e);
      s.state = 'failed';
      s.rows = [];
      s.error = t('Could not check for oversold stock.');
    }
    this._paintExceptionStock();
  },

  _paintExceptionStock() {
    const host = document.getElementById('exq-stock-body');
    if (!host) return;
    host.innerHTML = this._exqStockPanel();
  },

  async _loadExceptionConflicts() {
    const eq = this._exceptionQueue || (this._exceptionQueue = {});
    const s = eq.conflicts || (eq.conflicts = { state: 'checking', rows: [], error: '' });
    s.state = 'checking';
    s.error = '';
    this._paintExceptionConflicts();
    try {
      const res = await this._get('/api/sub/retail/inventory/sync-conflicts');
      if (!res || res.status !== 'success' || !res.data) {
        s.state = 'failed';
        s.rows = [];
        s.error = (res && (res.message || res.error)) || t('Could not check for discarded catalogue edits.');
      } else {
        const rows = Array.isArray(res.data.conflicts) ? res.data.conflicts : [];
        s.rows = rows;
        s.error = '';
        s.state = rows.length ? 'rows' : 'empty';
      }
    } catch (e) {
      console.error('Exceptions: sync-conflict load failed', e);
      s.state = 'failed';
      s.rows = [];
      s.error = t('Could not check for discarded catalogue edits.');
    }
    this._paintExceptionConflicts();
  },

  _paintExceptionConflicts() {
    const host = document.getElementById('exq-conflicts-body');
    if (!host) return;
    host.innerHTML = this._exqConflictsPanel();
  },

  // ── Shared per-state panels (checking / failed / empty), one instance
  //    parameterised by copy rather than duplicated per section ───────────

  // True the first time THIS section's rendered state differs from the last
  // one this tracker saw -- lets the shared per-state panels just below flip
  // their icon (opts.animate:'flip') only on a real transition (checking ->
  // empty, empty -> failed, ...), never on a same-state repaint from a
  // retry or a re-open of the screen. The three queues (stock/conflicts/
  // registry) share this one tracker, keyed by section, so one queue's
  // history can never be mistaken for another's.
  _exqStateChanged(sectionKey, state) {
    const last = (this._exqLastPaintedState || (this._exqLastPaintedState = {}))[sectionKey];
    this._exqLastPaintedState[sectionKey] = state;
    return state !== last;
  },

  // Renders one of the shared per-state icons through icons.js, falling
  // back to '' when AuraIcons isn't loaded (retail_exceptions_screen_test.js
  // loads this file standalone, without icons.js).
  _exqIcon(name, changed) {
    return window.AuraIcons ? AuraIcons.render(name, 28, changed ? { animate: 'flip' } : undefined) : '';
  },

  _exqChecking(message, sectionKey) {
    const changed = this._exqStateChanged(sectionKey, 'checking');
    return `
      <div class="sub-chart-card" data-exq-state="checking" style="text-align:center;padding:40px 32px">
        <div style="margin-bottom:10px" aria-hidden="true">${this._exqIcon('timer', changed)}</div>
        <p style="color:var(--text-muted);font-size:13px;margin:0;line-height:1.7">${message}</p>
      </div>`;
  },

  _exqFailed(errorText, retryCall, sectionKey) {
    const changed = this._exqStateChanged(sectionKey, 'failed');
    return `
      <div class="sub-chart-card" data-exq-state="failed" style="text-align:center;padding:40px 32px;border:1px solid var(--state-danger-border)">
        <div style="margin-bottom:10px" aria-hidden="true">${this._exqIcon('triangle-alert', changed)}</div>
        <p style="color:var(--state-danger-text);font-size:13px;margin:0 0 14px;line-height:1.7">${this._esc(errorText)}</p>
        <button class="ret-btn ret-btn-ghost" onclick="${retryCall}">${t('Try again')}</button>
      </div>`;
  },

  // An empty queue is the NORMAL, HEALTHY case -- the same circle-check-big
  // treatment _stkaClean() gives a reconciled shop, so this reads as
  // "nothing needs attention" rather than as a blank/broken section.
  _exqEmpty(title, message, sectionKey) {
    const changed = this._exqStateChanged(sectionKey, 'empty');
    return `
      <div class="sub-chart-card" data-exq-state="empty" style="text-align:center;padding:40px 32px">
        <div style="margin-bottom:10px;color:var(--state-success-text)" aria-hidden="true">${this._exqIcon('circle-check-big', changed)}</div>
        <h3 style="color:var(--text);margin:0 0 6px;font-size:15px">${title}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0;line-height:1.7">${message}</p>
      </div>`;
  },

  _exqUnknownState(state, sectionKey) {
    const changed = this._exqStateChanged(sectionKey, 'unknown:' + state);
    return `
      <div class="sub-chart-card" data-exq-state="unknown" style="text-align:center;padding:40px 32px;border:1px solid var(--state-danger-border)">
        <div style="margin-bottom:10px" aria-hidden="true">${this._exqIcon('triangle-alert', changed)}</div>
        <h3 style="color:var(--state-danger-text);margin:0 0 8px;font-size:15px">${t('This screen lost track of what it was showing.')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0;line-height:1.7"><bdi dir="ltr">${this._esc(String(state))}</bdi></p>
      </div>`;
  },

  // Local timestamp formatting -- `detected_at_utc` is a timezone-aware ISO
  // string ('...+00:00', see commercial_runtime's `now_utc_iso()`/
  // `_now_iso()`), which `new Date()` parses directly. Deliberately NOT
  // `_auditTimestamp()`: that helper appends a literal 'Z' after replacing
  // the FIRST space with 'T', which assumes a naive 'YYYY-MM-DD HH:MM:SS'
  // input (audit_log.timestamp's own shape) -- feeding it an
  // already-offset ISO string here would corrupt the parse.
  _exqTimestamp(ts) {
    if (!ts) return '—';
    const d = new Date(String(ts));
    if (isNaN(d.getTime())) return this._esc(ts);
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
           `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  },

  // ── Section 1: Oversold Stock ────────────────────────────────────────────

  _exqStockPanel() {
    const s = (this._exceptionQueue && this._exceptionQueue.stock) || { state: 'checking' };
    switch (s.state) {
      case 'checking': return this._exqChecking(t('Checking for oversold stock…'), 'stock');
      case 'failed':   return this._exqFailed(s.error || t('Could not check for oversold stock.'), 'RetailSystem._loadExceptionStock()', 'stock');
      case 'empty':    return this._exqEmpty(t('Nothing needs attention.'), t('No oversold product is currently open.'), 'stock');
      case 'rows':     return this._exqStockRows(s.rows);
      default:         return this._exqUnknownState(s.state, 'stock');
    }
  },

  // The resolve control is capability-gated PER ROW, not by hiding the
  // whole section -- a viewer who holds retail.reports but not
  // retail.stock.adjust must still see every open exception, just without
  // a button that would only 403. Same `SubsystemApp.hasCapability(...)`
  // mechanism the shell already uses to hide a control it cannot grant
  // (see `_mayBrowseTheSalesBook`'s identical shape for retail.reports),
  // computed ONCE per render rather than per row: the grant cannot change
  // mid-render, and repeating the check per row would just be the same
  // answer asked N times.
  _exqStockRows(rows) {
    const list = Array.isArray(rows) ? rows : [];
    const canResolve = !window.SubsystemApp || SubsystemApp.hasCapability('retail.stock.adjust');
    return `
      <div data-exq-state="rows">
        <div class="sub-chart-card">
          <div style="overflow-x:auto">
            <table class="ret-table" id="exq-stock-table">
              <thead><tr>
                <th>${t('Product')}</th>
                <th>${t('SKU')}</th>
                <th>${t('Branch')}</th>
                <th style="text-align:right">${t('Balance')}</th>
                <th>${t('Detected')}</th>
                ${canResolve ? '<th></th>' : ''}
              </tr></thead>
              <tbody>${list.map(r => this._exqStockRow(r, canResolve)).join('')}</tbody>
            </table>
          </div>
        </div>
      </div>`;
  },

  _exqStockRow(r, canResolve) {
    const row = r || {};
    // product_name is a LEFT JOIN and can be null -- same reasoning as
    // _stkaRow()'s own product cell, below in this file (Stock Accuracy):
    // the oversell already happened, and hiding the row because a foreign
    // name is gone would be exactly the silent-drop failure this queue
    // exists to replace.
    //
    // `productLabel`, not `name` -- deliberately a different local variable
    // (and therefore a different literal `<td>...</td>` line) than
    // `_stkaRow()`'s own `name`, so this row's markup can never collide
    // with that screen's own mutation-proof anchor text and make ITS
    // "occurs exactly once" check fail the moment both screens render.
    const productLabel = row.product_name
      ? this._esc(row.product_name)
      : `<span>${t('Product no longer in the catalogue')}</span> ${this._bdi(String(row.product_id == null ? '' : row.product_id))}`;
    const sku = row.sku ? this._bdi(row.sku) : '—';
    const branch = row.branch_name
      ? this._esc(row.branch_name)
      : (row.branch_id != null ? this._bdi('#' + String(row.branch_id)) : '—');
    const resolveCell = canResolve
      ? `<td><button class="ret-btn ret-btn-primary ret-btn-sm" data-exq-resolve-id="${this._esc(row.id)}"
           onclick="RetailSystem._openResolveException('${this._esc(row.id)}')">${t('Resolve')}</button></td>`
      : '';
    return `<tr>
      <td>${productLabel}</td>
      <td style="font-family:monospace;font-size:12px">${sku}</td>
      <td>${branch}</td>
      <td style="text-align:right">${this._stkaNum(this._signedQty(row.observed_quantity_on_hand))}</td>
      <td>${this._bdi(this._exqTimestamp(row.detected_at_utc))}</td>
      ${resolveCell}
    </tr>`;
  },

  // The ONE way into resolving an exception -- looks the row up from the
  // already-fetched list rather than re-fetching it, matching
  // _openStockAdjust's own modal shape elsewhere in this file.
  _openResolveException(id) {
    const s = this._exceptionQueue && this._exceptionQueue.stock;
    const row = ((s && Array.isArray(s.rows)) ? s.rows : []).find(r => String(r.id) === String(id));
    if (!row) return;
    const name = row.product_name || t('Product no longer in the catalogue');
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'exq-resolve-modal';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:420px">
        <h3>${t('Resolve Exception')} — ${this._esc(name)}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 18px;line-height:1.7">
          ${t('The recorded balance is')} <strong style="color:var(--text-primary)">${this._esc(this._signedQty(row.observed_quantity_on_hand))}</strong>.
          ${t('Count the shelf if you can, or leave the count blank for a backorder that stays negative until the delivery lands.')}
        </p>
        <div class="ret-field"><label>${t('Counted Quantity (optional)')}</label>
          <input type="number" id="exq-resolve-qty" step="any" placeholder="${t('Leave blank if not counted')}" /></div>
        <div class="ret-field"><label>${t('Note')} *</label>
          <textarea id="exq-resolve-note" rows="3" placeholder="${t('Required -- explain the decision')}"></textarea>
          <p style="color:var(--text-faint);font-size:11.5px;margin:4px 0 0">${t('A note is required. The server refuses to resolve without one.')}</p>
        </div>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('exq-resolve-modal').remove()">${t('Cancel')}</button>
          <button class="ret-btn ret-btn-primary" id="exq-resolve-btn" onclick="RetailSystem._saveResolveException('${this._esc(id)}')">${t('Resolve')}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    document.getElementById('exq-resolve-note')?.focus();
  },

  async _saveResolveException(id) {
    const noteEl = document.getElementById('exq-resolve-note');
    const note = (noteEl?.value || '').trim();
    // Client-side gate BEFORE the request, not just after -- the server
    // refuses a resolution with no note (resolve_stock_exception's own
    // 400), and letting someone submit only to be told that afterwards is
    // worse than a form that says so up front.
    if (!note) {
      SubsystemApp.showToast(t('A note explaining the resolution is required.'), 'error');
      noteEl?.focus();
      return;
    }
    const qtyRaw = document.getElementById('exq-resolve-qty')?.value;
    const body = { note };
    if (qtyRaw !== '' && qtyRaw != null) body.counted_quantity = parseFloat(qtyRaw);
    const btn = document.getElementById('exq-resolve-btn');
    if (btn) { btn.disabled = true; btn.textContent = t('Resolving…'); }
    try {
      const res = await this._post(`/api/sub/retail/inventory/stock-exceptions/${encodeURIComponent(id)}/resolve`, body);
      if (res && res.status === 'success') {
        SubsystemApp.showToast(t('Exception resolved.'), 'success');
        document.getElementById('exq-resolve-modal')?.remove();
        await this._loadExceptionStock();
      } else {
        SubsystemApp.showToast((res && res.message) || t('Could not resolve this exception.'), 'error');
        if (btn) { btn.disabled = false; btn.textContent = t('Resolve'); }
      }
    } catch (e) {
      SubsystemApp.showToast(t('Could not resolve this exception.'), 'error');
      if (btn) { btn.disabled = false; btn.textContent = t('Resolve'); }
    }
  },

  // ── Section 2: Discarded Catalogue Edits ─────────────────────────────────
  //
  // INFORMATIONAL ONLY -- no action button anywhere in this section, on
  // purpose. list_sync_conflicts' own docstring (retail_api.py) is the
  // authority: a discarded edit already lost to a newer one, so there is
  // no honest "fix" to offer here -- only the words above the table saying
  // so, and the remedy (redo the edit) in the section's own intro text.

  _exqConflictsPanel() {
    const s = (this._exceptionQueue && this._exceptionQueue.conflicts) || { state: 'checking' };
    switch (s.state) {
      case 'checking': return this._exqChecking(t('Checking for discarded edits…'), 'conflicts');
      case 'failed':   return this._exqFailed(s.error || t('Could not check for discarded catalogue edits.'), 'RetailSystem._loadExceptionConflicts()', 'conflicts');
      case 'empty':    return this._exqEmpty(t('Nothing needs attention.'), t('No catalogue edit has been discarded as stale.'), 'conflicts');
      case 'rows':     return this._exqConflictsRows(s.rows);
      default:         return this._exqUnknownState(s.state, 'conflicts');
    }
  },

  _exqConflictsRows(rows) {
    const list = Array.isArray(rows) ? rows : [];
    return `
      <div data-exq-state="rows">
        <div class="sub-chart-card">
          <div style="overflow-x:auto">
            <table class="ret-table" id="exq-conflicts-table">
              <thead><tr>
                <th>${t('Record')}</th>
                <th>${t('Change')}</th>
                <th>${t('Fields')}</th>
                <th style="text-align:right">${t('Local version')}</th>
                <th style="text-align:right">${t('Incoming version')}</th>
                <th>${t('Detected')}</th>
              </tr></thead>
              <tbody>${list.map(r => this._exqConflictRow(r)).join('')}</tbody>
            </table>
          </div>
        </div>
      </div>`;
  },

  _exqConflictRow(r) {
    const row = r || {};
    const entity = `${this._esc(this._exqEntityLabel(row.entity_type))} ${this._bdi(String(row.entity_id == null ? '' : row.entity_id))}`;
    // `changed_fields` is the summary list_sync_conflicts computes SERVER
    // SIDE (field NAMES only, from `_changed_fields`/the payload's own
    // keys) -- this table never sees `incoming_payload` at all, raw or
    // otherwise, because the route never sends it. See that route's own
    // docstring for why: the payload is arbitrary operator-entered data
    // from a DIFFERENT device, the exact trust boundary
    // retail_pos_name_xss_test.js / retail_customer_modal_xss_test.js
    // already guard on this device's own catalogue.
    const fields = Array.isArray(row.changed_fields) ? row.changed_fields : [];
    const fieldsText = fields.length ? fields.map(f => this._esc(f)).join(', ') : '—';
    return `<tr>
      <td>${entity}</td>
      <td>${this._esc(this._exqEventLabel(row.event_type))}</td>
      <td>${fieldsText}</td>
      <td style="text-align:right">${this._stkaNum(this._qty(row.local_row_version))}</td>
      <td style="text-align:right">${this._stkaNum(this._qty(row.incoming_row_version))}</td>
      <td>${this._bdi(this._exqTimestamp(row.detected_at_utc))}</td>
    </tr>`;
  },

  _exqEntityLabel(type) {
    const labels = {
      category: t('Category'), product: t('Product'), customer: t('Customer'),
      supplier: t('Supplier'), reorder_request: t('Reorder request'),
    };
    return labels[type] || (type || t('Record'));
  },

  _exqEventLabel(type) {
    const labels = { create: t('Create'), update: t('Update'), delete: t('Delete') };
    return labels[type] || (type || '—');
  },

  // ── Section 3: Staff Account Conflicts (launch-readiness account-
  //    hierarchy design §2.4/§4.2 -- "the quarantine screen is a
  //    PREREQUISITE, not a nice-to-have") ─────────────────────────────────
  //
  // registry.db's OWN `sync_apply_quarantine` (v6), read through
  // GET /api/sub/retail/account-quarantine (retail_api.py::list_account_
  // quarantine) -- the `user`/`user_permission` sync-apply collisions
  // wave D's delegation manufactures from routine HR work (an owner and a
  // branch manager independently inviting the same real person). Same
  // read-only, INFORMATIONAL treatment as Section 2 above -- no resolve
  // control anywhere in this section, for the identical reason: the fix
  // for a duplicate is a human decision made through an ordinary account
  // edit (rename the clashing email/employee code), not a special action
  // this queue would need to expose, and a `missing_parent:user` row
  // resolves itself once the owning account finishes arriving.

  async _loadExceptionRegistry() {
    const eq = this._exceptionQueue || (this._exceptionQueue = {});
    const s = eq.registry || (eq.registry = { state: 'checking', rows: [], error: '' });
    s.state = 'checking';
    s.error = '';
    this._paintExceptionRegistry();
    try {
      const res = await this._get('/api/sub/retail/account-quarantine');
      if (!res || res.status !== 'success' || !res.data) {
        s.state = 'failed';
        s.rows = [];
        s.error = (res && (res.message || res.error)) || t('Could not check for account sync issues.');
      } else {
        const rows = Array.isArray(res.data.items) ? res.data.items : [];
        s.rows = rows;
        s.error = '';
        s.state = rows.length ? 'rows' : 'empty';
      }
    } catch (e) {
      console.error('Exceptions: account-quarantine load failed', e);
      s.state = 'failed';
      s.rows = [];
      s.error = t('Could not check for account sync issues.');
    }
    this._paintExceptionRegistry();
  },

  _paintExceptionRegistry() {
    const host = document.getElementById('exq-registry-body');
    if (!host) return;
    host.innerHTML = this._exqRegistryPanel();
  },

  _exqRegistryPanel() {
    const s = (this._exceptionQueue && this._exceptionQueue.registry) || { state: 'checking' };
    switch (s.state) {
      case 'checking': return this._exqChecking(t('Checking for account sync issues…'), 'registry');
      case 'failed':   return this._exqFailed(s.error || t('Could not check for account sync issues.'), 'RetailSystem._loadExceptionRegistry()', 'registry');
      case 'empty':    return this._exqEmpty(t('Nothing needs attention.'), t('No staff account is stuck waiting to sync.'), 'registry');
      case 'rows':     return this._exqRegistryRows(s.rows);
      default:         return this._exqUnknownState(s.state, 'registry');
    }
  },

  _exqRegistryRows(rows) {
    const list = Array.isArray(rows) ? rows : [];
    return `
      <div data-exq-state="rows">
        <div class="sub-chart-card">
          <div style="overflow-x:auto">
            <table class="ret-table" id="exq-registry-table">
              <thead><tr>
                <th>${t('Record')}</th>
                <th>${t('Change')}</th>
                <th>${t('Reason')}</th>
                <th>${t('Detail')}</th>
                <th>${t('Detected')}</th>
              </tr></thead>
              <tbody>${list.map(r => this._exqRegistryRow(r)).join('')}</tbody>
            </table>
          </div>
        </div>
      </div>`;
  },

  _exqRegistryRow(r) {
    const row = r || {};
    return `<tr>
      <td>${this._exqRegistryEntityLabel(row.entity_type)}</td>
      <td>${this._esc(this._exqEventLabel(row.event_type))}</td>
      <td>${this._esc(this._exqRegistryReasonLabel(row.reason))}</td>
      <td>${row.detail ? this._esc(row.detail) : '—'}</td>
      <td>${this._bdi(this._exqTimestamp(row.quarantined_at))}</td>
    </tr>`;
  },

  _exqRegistryEntityLabel(type) {
    const labels = { user: t('Staff account'), user_permission: t('Permission grant') };
    return this._esc(labels[type] || (type || t('Record')));
  },

  // `detail` (registry_api's own docstring) is a pre-formatted, SCRUBBED
  // string -- never a credential -- but it also carries identifiers
  // (email/employee_id) this table already shows structured elsewhere,
  // so the REASON cell stays a short, translated label rather than
  // echoing that free text twice.
  _exqRegistryReasonLabel(reason) {
    const labels = {
      duplicate_email: t('Duplicate email address'),
      duplicate_employee_id: t('Duplicate employee code'),
      'missing_parent:user': t('Waiting for the linked account to sync'),
    };
    return labels[reason] || (reason || '—');
  },

  // ══ STOCK ACCURACY ════════════════════════════════════════════════════════
  //
  // The screen that answers "the stock is not accurate" by turning it into a
  // list somebody can check.
  //
  // Reads GET /api/sub/retail/inventory/reconciliation, which compares every
  // cached `inventory_balances.quantity_on_hand` against the
  // `inventory_movements` ledger it is supposed to be a cache of. See
  // backend/core/retail/stock_reconciliation.py for why the ledger is the
  // truth and the balance is the cache.
  //
  // ── THREE THINGS THIS SCREEN IS BUILT AROUND ──────────────────────────────
  //
  // 1. NET DRIFT IS NOT THE ANSWER ON ITS OWN. A +10 and a -10 net to zero
  //    while two products are wrong, so the count sits beside the net and the
  //    tile says as much in words. A headline that can read "0" over a broken
  //    shop is worse than no headline.
  //
  // 2. AN EMPTY LIST IS THREE DIFFERENT FACTS. "Everything agrees",
  //    "there was nothing to compare" and "the check did not run" all produce
  //    zero rows, and this programme has already shipped one screen where an
  //    error rendered as emptiness and one where a refusal rendered as
  //    "nobody sold anything". So the state is explicit -- one panel per
  //    state, each carrying its own `data-sa-state` -- and `pairs_examined`
  //    (how much was actually looked at) is what separates the first two.
  //    A green tick is only honest if something was compared.
  //
  // 3. REPAIR IS OWNER-INITIATED, ALWAYS. Repairing on load would take a
  //    visible discrepancy and make it invisible, which is the exact opposite
  //    of what this screen is for -- and it would destroy the evidence that
  //    one of the five balance writers is broken. _repairStockAccuracy()
  //    therefore refuses to run unless the state machine is sitting in
  //    'confirm', i.e. unless a human went through the step that says what it
  //    is about to do. Nothing on the load path can reach it.
  //
  // Gated on BOTH axes the route itself gates on, for the reason the Reports
  // and Audit Log guards above spell out: `_navigate('stock-accuracy')` is
  // reachable without the nav entry, because AuraRouter replays the last
  // section from the URL hash on the next launch.
  async _renderStockAccuracy(c) {
    this._injectStyles();

    // retail.reports -- the same capability inventory_reconciliation carries
    // (@mt_require_capability(CAP_REPORTS)), and the same code its sibling
    // read-only report routes use.
    if (window.SubsystemApp && !SubsystemApp.hasCapability('retail.reports')) {
      return this._renderCapabilityRestricted(c, {
        icon: '⚖️',
        title: t('Stock Accuracy'),
        message: t('Sales totals and reports are limited to managers and the store owner. Open the till to start ringing sales.'),
      });
    }
    // ...and company-admin, which the route ALSO enforces
    // (_require_company_admin -> session mt_role == 'admin'). That is the
    // USER axis, not the device axis: an owner who picks up a second terminal
    // still owns the shop, and a manager standing at the admin terminal still
    // does not. Checking only the capability would leave a manager with
    // retail.reports on a screen whose one request answers 403.
    //
    // Refuses only when the role is KNOWN and is not admin. `SubsystemApp.role`
    // is '' until /api/auth/session resolves, and that is "unknown", not
    // "denied" -- the same fail-open convention hasCapability() documents,
    // and the reason this file stays loadable with no SubsystemApp at all.
    if (window.SubsystemApp && SubsystemApp.role && SubsystemApp.role !== 'admin') {
      return this._renderCapabilityRestricted(c, {
        icon: '⚖️',
        title: t('Stock Accuracy'),
        message: t('Comparing the whole catalogue against the stock ledger is limited to the store owner.'),
      });
    }

    // 'checking' from the very first paint, never 'empty'. An unfilled region
    // between the frame and the first response is indistinguishable from
    // "found nothing", which is the failure this screen exists to refuse --
    // so #stka-body is never empty for a single tick.
    this._stockAccuracy = { state: 'checking', data: null, repair: null, error: '' };
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">${t('Stock Accuracy')}</h2>
        <button class="ret-btn ret-btn-ghost" id="stka-recheck"
                onclick="RetailSystem._loadStockAccuracy()">${t('Run the check again')}</button>
      </div>
      <p style="color:var(--text-muted);font-size:13px;margin:0 0 18px;max-width:780px;line-height:1.7">
        ${t('Every stock figure in this product is a cached total. This compares each one against the movement ledger it is supposed to be a cache of, and lists the ones that disagree.')}
      </p>
      <div id="stka-body">${this._stockAccuracyPanel()}</div>`;
    await this._loadStockAccuracy();
  },

  async _loadStockAccuracy() {
    const s = this._stockAccuracy ||
      (this._stockAccuracy = { state: 'checking', data: null, repair: null, error: '' });
    s.state = 'checking';
    s.error = '';
    s.repair = null;
    this._paintStockAccuracy();
    try {
      const res = await this._get('/api/sub/retail/inventory/reconciliation');
      // `status !== 'success'` covers the 400 envelope; `res.error` with no
      // `status` at all is what _require_company_admin's 403 looks like. Both
      // are shown as the server worded them -- a refusal and a database
      // failure are different facts and neither is "no drift".
      if (!res || res.status !== 'success' || !res.data) {
        s.state = 'failed';
        s.data = null;
        s.error = (res && (res.message || res.error)) || t('Could not check stock accuracy.');
      } else {
        s.data = res.data;
        s.error = '';
        // pairs_examined is what makes a clean result mean anything. Zero
        // pairs compared is NOT a clean bill of health, and gets its own
        // panel saying so.
        const examined = +(res.data.pairs_examined || 0);
        const drifted = +(res.data.drift_count || 0);
        s.state = drifted > 0 ? 'drift' : (examined > 0 ? 'clean' : 'nothing');
      }
    } catch (e) {
      console.error('Stock accuracy check failed', e);
      s.state = 'failed';
      s.data = null;
      s.error = t('Could not check stock accuracy.');
    }
    this._paintStockAccuracy();
  },

  _paintStockAccuracy() {
    const host = document.getElementById('stka-body');
    if (!host) return;
    host.innerHTML = this._stockAccuracyPanel();
  },

  // Step one of the repair, and the only way into step two. Shows what the
  // repair will do -- counted, not described in general terms -- before
  // anything is sent.
  _askRepairStockAccuracy() {
    const s = this._stockAccuracy;
    if (!s || s.state !== 'drift') return;
    s.state = 'confirm';
    this._paintStockAccuracy();
  },

  _cancelRepairStockAccuracy() {
    const s = this._stockAccuracy;
    if (!s || s.state !== 'confirm') return;
    s.state = 'drift';
    this._paintStockAccuracy();
  },

  async _repairStockAccuracy() {
    const s = this._stockAccuracy;
    // THE GUARD THAT MAKES "OWNER-INITIATED" STRUCTURAL RATHER THAN A HABIT.
    // 'confirm' is only ever set by _askRepairStockAccuracy, which is only
    // ever reached from a button on the drift panel. So this function is
    // inert on the load path, inert while the check is in flight, and inert
    // if it is called a second time from a stale handler after the first
    // repair already landed -- automatically repairing a drifted shop turns a
    // visible discrepancy into an invisible one.
    if (!s || s.state !== 'confirm') return;
    // The token the server will demand back, taken from the report response
    // rather than assembled here: its format is the backend's
    // (_confirmation_token), and a client that spells it out itself is a
    // second copy of that format waiting to drift.
    const token = s.data && s.data.repair_confirmation;
    if (!token) {
      s.state = 'repair-failed';
      s.error = t('This build cannot confirm the repair, so nothing was changed.');
      this._paintStockAccuracy();
      return;
    }
    s.state = 'repairing';
    s.error = '';
    this._paintStockAccuracy();
    try {
      const res = await this._post('/api/sub/retail/inventory/reconciliation/repair', { confirm: token });
      if (!res || res.status !== 'success' || !res.data) {
        // A refusal (403, a restricted licence, a 400) is NOT "repaired 0".
        // Its own panel, with the server's own words.
        s.state = 'repair-failed';
        s.error = (res && (res.message || res.error)) || t('The repair did not run, so nothing was changed.');
      } else {
        s.state = 'repaired';
        s.repair = res.data;
      }
    } catch (e) {
      console.error('Stock accuracy repair failed', e);
      s.state = 'repair-failed';
      s.error = t('The repair did not run, so nothing was changed.');
    }
    // Deliberately does NOT re-run the check here. Replacing "12 balances
    // rewritten, 2 skipped" with a green tick the instant it lands is how a
    // repair becomes something nobody can audit afterwards. The owner reads
    // what happened, then asks for the check again.
    this._paintStockAccuracy();
  },

  // ── ONE PANEL PER STATE ───────────────────────────────────────────────────
  //
  // Every branch returns exactly one element carrying its own
  // `data-sa-state`, so two states can never be on screen together and no
  // state can render as another one's markup. That attribute is the contract
  // retail_stock_accuracy_screen_test.js reads.
  _stockAccuracyPanel() {
    const s = this._stockAccuracy || { state: 'checking' };
    switch (s.state) {
      case 'checking':      return this._stkaChecking();
      case 'failed':        return this._stkaFailed();
      case 'nothing':       return this._stkaNothingToCheck();
      case 'clean':         return this._stkaClean();
      case 'drift':
      case 'confirm':
      case 'repairing':     return this._stkaDrift(s.state);
      case 'repaired':      return this._stkaRepaired();
      case 'repair-failed': return this._stkaRepairFailed();
      // No silent default. An unknown state is a bug in this file, and
      // rendering nothing for it would be the empty-screen failure again.
      default:              return this._stkaUnknownState(s.state);
    }
  },

  // A quantity, not an amount: no currency, and never through _money().
  // inventory_movements.quantity is REAL (goods are sold by weight here), so
  // a fixed 0dp would round half a kilo away and a fixed 2dp would print
  // every whole unit as "24.00".
  _qty(n) {
    const v = +(n || 0);
    if (!isFinite(v)) return '0';
    return String(Math.round(v * 1000) / 1000);
  },

  // The sign IS the message on this screen, so it is always drawn: U+002B for
  // "the balance claims MORE than the ledger can account for" (the
  // double-received-PO and resurrected-by-import signature) and U+2212 MINUS
  // -- not a hyphen -- for the other direction, matching _moneyDigits()'s
  // reasoning about a glyph that cannot be misread inside a product name.
  _signedQty(n) {
    const v = +(n || 0);
    if (!isFinite(v) || v === 0) return this._qty(0);
    return (v > 0 ? '+' : '−') + this._qty(Math.abs(v));
  },

  // A figure, isolated and tabular. `.num` is main.css's numeric class: it
  // supplies tabular figures and nowrap, declares no colour of its own (so
  // the cell decides), and rtl.css already forces it back to direction:ltr.
  // <bdi dir="ltr"> on top of that because a signed run like "+7" carries no
  // strong directional character at all, so in Arabic it would otherwise take
  // its direction from the paragraph and swap the sign to the far side of the
  // digits. See _bdi()'s own note on why "auto" is not enough here.
  _stkaNum(text) {
    return `<span class="num">${this._bdi(text)}</span>`;
  },

  _stkaChecking() {
    return `
      <div class="sub-chart-card" data-sa-state="checking" style="text-align:center;padding:48px 32px">
        <div style="font-size:34px;margin-bottom:12px" aria-hidden="true">⏳</div>
        <h3 style="color:var(--text);margin:0 0 8px;font-size:17px">${t('Comparing every stock figure against the ledger…')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0;line-height:1.7">${t('Nothing has been compared yet, so this screen has no result to show.')}</p>
      </div>`;
  },

  _stkaFailed() {
    const s = this._stockAccuracy || {};
    return `
      <div class="sub-chart-card" data-sa-state="failed" style="text-align:center;padding:48px 32px;border:1px solid var(--state-danger-border)">
        <div style="font-size:34px;margin-bottom:12px" aria-hidden="true">⚠️</div>
        <h3 style="color:var(--state-danger-text);margin:0 0 8px;font-size:17px">${t('The stock accuracy check did not run.')}</h3>
        <p style="color:var(--text);font-size:13px;margin:0 0 8px;line-height:1.7">${this._esc(s.error || '')}</p>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 20px;line-height:1.7;max-width:520px;margin-left:auto;margin-right:auto">${t('This is not the same as finding no problems. Nothing was compared, so nothing is known.')}</p>
        <button class="ret-btn ret-btn-ghost" onclick="RetailSystem._loadStockAccuracy()">${t('Try the check again')}</button>
      </div>`;
  },

  // pairs_examined === 0. A shop with no stock records produces the same
  // empty row list as a perfectly reconciled one, and calling that "accurate"
  // would be a claim about evidence that does not exist.
  _stkaNothingToCheck() {
    return `
      <div class="sub-chart-card" data-sa-state="nothing" style="text-align:center;padding:48px 32px">
        <div style="font-size:34px;margin-bottom:12px" aria-hidden="true">📭</div>
        <h3 style="color:var(--text);margin:0 0 8px;font-size:17px">${t('There is nothing to check yet.')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0;line-height:1.7;max-width:520px;margin-left:auto;margin-right:auto">${t('This company has no stock records, so the check compared nothing. That is not the same as being accurate.')}</p>
      </div>`;
  },

  _stkaClean() {
    const d = (this._stockAccuracy && this._stockAccuracy.data) || {};
    return `
      <div class="sub-chart-card" data-sa-state="clean" style="text-align:center;padding:48px 32px">
        <div style="font-size:34px;margin-bottom:12px" aria-hidden="true">✅</div>
        <h3 style="color:var(--text);margin:0 0 8px;font-size:17px">${t('Every stock figure agrees with the ledger.')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0;line-height:1.7">
          <span>${t('Product and branch pairs compared')}</span>
          ${this._stkaNum(this._qty(d.pairs_examined))}
        </p>
      </div>`;
  },

  _stkaUnknownState(state) {
    return `
      <div class="sub-chart-card" data-sa-state="unknown" style="text-align:center;padding:48px 32px;border:1px solid var(--state-danger-border)">
        <div style="font-size:34px;margin-bottom:12px" aria-hidden="true">⚠️</div>
        <h3 style="color:var(--state-danger-text);margin:0 0 8px;font-size:17px">${t('This screen lost track of what it was showing.')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 20px;line-height:1.7">
          <span>${t('Unrecognised state')}</span>
          <bdi dir="ltr">${this._esc(String(state))}</bdi>
        </p>
        <button class="ret-btn ret-btn-ghost" onclick="RetailSystem._loadStockAccuracy()">${t('Try the check again')}</button>
      </div>`;
  },

  _stkaDrift(mode) {
    const d = (this._stockAccuracy && this._stockAccuracy.data) || {};
    const rows = Array.isArray(d.rows) ? d.rows : [];
    // `repairable` is the server's own field and the exact predicate
    // repair_drift skips on -- not a second rule invented here about which
    // rows a repair will touch.
    const fixable = rows.filter(r => r && r.repairable).length;
    const stranded = rows.length - fixable;
    return `
      <div data-sa-state="${mode}">
        <div class="ret-kpi-grid" style="grid-template-columns:repeat(3,1fr)">
          <div class="ret-kpi">
            <div class="ret-kpi-label">${t('Net drift')}</div>
            <div class="ret-kpi-value">${this._stkaNum(this._signedQty(d.net_drift))}</div>
            <div class="ret-kpi-sub">${t('Units. Positive means the balances claim more than the ledger can account for.')}</div>
          </div>
          <div class="ret-kpi">
            <div class="ret-kpi-label">${t('Figures that disagree')}</div>
            <div class="ret-kpi-value">${this._stkaNum(this._qty(d.drift_count))}</div>
            <div class="ret-kpi-sub">${t('Read this beside the net. A +10 and a −10 net to zero while two products are wrong.')}</div>
          </div>
          <div class="ret-kpi">
            <div class="ret-kpi-label">${t('Pairs compared')}</div>
            <div class="ret-kpi-value">${this._stkaNum(this._qty(d.pairs_examined))}</div>
            <div class="ret-kpi-sub">${t('Every product and branch the check looked at, agreeing or not.')}</div>
          </div>
        </div>
        <div class="sub-chart-card">
          <div class="sub-chart-title" style="margin-bottom:14px">${t('Which figures disagree')}</div>
          <div style="overflow-x:auto">
            <table class="ret-table" id="stka-table">
              <thead><tr>
                <th>${t('Product')}</th>
                <th>${t('SKU')}</th>
                <th>${t('Branch')}</th>
                <th style="text-align:right">${t('Balance says')}</th>
                <th style="text-align:right">${t('Ledger says')}</th>
                <th style="text-align:right">${t('Drift')}</th>
              </tr></thead>
              <tbody>${rows.map(r => this._stkaRow(r)).join('')}</tbody>
            </table>
          </div>
          <p style="color:var(--text-faint);font-size:12px;margin:14px 0 0;line-height:1.7">${t('Drift is the cached balance minus the ledger total. The ledger is the record of what happened; the balance is only a running copy of it.')}</p>
        </div>
        ${stranded ? `
        <div class="sub-chart-card" style="margin-top:18px">
          <div class="sub-chart-title" style="margin-bottom:10px">${t('Rows that cannot be repaired yet')}</div>
          <p style="color:var(--text-muted);font-size:13px;margin:0;line-height:1.7;max-width:760px">
            <span>${t('These movements were recorded without a branch, and a cached balance always belongs to one, so there is no figure to correct. They need a branch before they can be reconciled.')}</span>
            ${this._stkaNum(this._qty(stranded))}
          </p>
        </div>` : ''}
        ${mode === 'confirm' ? this._stkaConfirm(fixable, stranded) : this._stkaRepairOffer(fixable, mode)}
      </div>`;
  },

  _stkaRow(r) {
    const row = r || {};
    // product_name is a LEFT JOIN and can be null -- a movement whose product
    // row is gone is one of the two shapes the drift query exists to surface,
    // and rendering it as a blank cell would hide exactly that case.
    const name = row.product_name
      ? this._esc(row.product_name)
      : `<span>${t('Product no longer in the catalogue')}</span> <bdi dir="ltr">${this._esc(String(row.product_id == null ? '' : row.product_id))}</bdi>`;
    const sku = row.sku ? this._bdi(row.sku) : '—';
    const branch = row.branch_id == null
      ? this._badge(t('Needs a branch'), 'yellow')
      : (row.branch_name
          ? this._esc(row.branch_name)
          : this._bdi('#' + String(row.branch_id)));
    return `<tr>
      <td>${name}</td>
      <td style="font-family:monospace;font-size:12px">${sku}</td>
      <td>${branch}</td>
      <td style="text-align:right">${this._stkaNum(this._qty(row.stored_balance))}</td>
      <td style="text-align:right">${this._stkaNum(this._qty(row.ledger_balance))}</td>
      <td style="text-align:right;font-weight:700">${this._stkaNum(this._signedQty(row.drift))}</td>
    </tr>`;
  },

  _stkaRepairOffer(fixable, mode) {
    if (!fixable) return '';
    const busy = mode === 'repairing';
    return `
      <div class="sub-chart-card" style="margin-top:18px">
        <div class="sub-chart-title" style="margin-bottom:10px">${t('Repair')}</div>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 8px;line-height:1.7;max-width:760px">${t('Repairing rewrites each cached balance to match its ledger total. It creates no stock movement and changes none, so the ledger stays the record of what happened.')}</p>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 18px;line-height:1.7;max-width:760px">${t('Count the shelves first if you can. A repair makes the two numbers agree; it cannot make either of them true.')}</p>
        <button class="ret-btn ret-btn-primary" id="stka-repair-btn"${busy ? ' disabled' : ''}
                onclick="RetailSystem._askRepairStockAccuracy()">${busy ? t('Rewriting…') : t('Repair balances from the ledger')}</button>
      </div>`;
  },

  // One line of a counted list. Its own helper so the confirm panel and the
  // result panel cannot drift into two different shapes for the same fact.
  _stkaTally(label, value) {
    return `<div style="display:flex;justify-content:space-between;gap:18px;color:var(--text);font-size:13px">
            <span>${label}</span>${this._stkaNum(this._qty(value))}
          </div>`;
  },

  // WHY THE REPAIR REFUSED A ROW, in the owner's words.
  //
  // The keys are backend constants -- core/retail/stock_reconciliation.py's
  // SKIP_NO_BRANCH / SKIP_NO_LEDGER_HISTORY -- and
  // retail_stock_accuracy_screen_route_test.py fails if that module grows a
  // reason this function does not name. That coupling is deliberate: a
  // skipped row rendered under the WRONG reason is worse than one rendered
  // under no reason, because the owner acts on the reason.
  //
  // An unrecognised reason therefore says so, rather than being folded into
  // whichever of the two is nearest. "It carried no branch" is an instruction
  // to go and assign a branch, and telling somebody that about a row that was
  // actually refused for having no ledger history at all sends them to fix
  // the wrong thing.
  _stkaSkipReasonLabel(reason) {
    switch (reason) {
      case 'no_branch':
        return t('Their movements carry no branch, and a cached balance always belongs to one.');
      case 'no_ledger_history':
        return t('The ledger has no history for them at all, so a repair would have written away an opening stock.');
      case 'no_product':
        return t('Their product record no longer exists, so no ledger entry can ever be written for them.');
      case 'ledger_not_established':
        return t('This install has not finished the stock-ledger upgrade, so lowering a balance to match the ledger could erase opening stock. Restart the app to finish it.');
      default:
        return t('The server did not say why.');
    }
  },

  // "It says what it will do" -- counted, from the same rows the table above
  // is drawn from, including the one that is deliberately zero.
  //
  // "AT MOST", and that word is load-bearing. The repair refuses FOUR kinds of
  // row and this report can see only two of them.
  //
  // VISIBLE here, because compute_drift reports `repairable: false` for both:
  //   no_branch    -- the movements carry no branch, and a cached balance
  //                   always belongs to one
  //   no_product   -- the product row is gone, so no ledger entry can ever be
  //                   written against that key
  //
  // DECIDED AT REPAIR TIME, per row, and therefore invisible to this report:
  //   no_ledger_history        -- the ledger has no history at all, so a
  //                               repair would write away an opening stock
  //   ledger_not_established   -- this install has not finished the v15
  //                               upgrade, so LOWERING a balance to match the
  //                               ledger could erase opening stock
  //
  // That last one is the guard that keeps a pre-v15 install from being told
  // its entire stock is zero. Before it existed, a legacy shop that followed
  // the migration's own recovery advice went from shelves of 112/57/7 to
  // -8/-3/-1. Promising an exact number this screen cannot know would make the
  // result panel underneath look like a failure every time a guard fired.
  _stkaConfirm(fixable, stranded) {
    return `
      <div class="sub-chart-card" data-sa-confirm="1" style="margin-top:18px;border:1px solid var(--state-warning-border)">
        <div class="sub-chart-title" style="margin-bottom:12px">${t('Confirm the repair')}</div>
        <div style="display:flex;flex-direction:column;gap:8px;margin-bottom:16px;max-width:760px">
          ${this._stkaTally(t('Cached balances this will rewrite, at most'), fixable)}
          ${this._stkaTally(t('Rows it cannot touch, because their movements carry no branch'), stranded)}
          ${this._stkaTally(t('Stock movements this will create or change'), 0)}
        </div>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 10px;line-height:1.7;max-width:760px">${t('The repair also refuses any balance the ledger has no history for at all, because the ledger saying nothing is not the ledger saying zero. Whatever it refuses is listed when it finishes.')}</p>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 18px;line-height:1.7;max-width:760px">${t('Each rewrite is written to the audit log with the figure it replaced and the figure it wrote.')}</p>
        <button class="ret-btn ret-btn-ghost" onclick="RetailSystem._cancelRepairStockAccuracy()">${t('Cancel')}</button>
        <button class="ret-btn ret-btn-primary" style="margin-left:8px" id="stka-confirm-btn"
                onclick="RetailSystem._repairStockAccuracy()">${t('Rewrite the balances')}</button>
      </div>`;
  },

  // "It reports what it did." Both totals, and the refusals broken down by
  // the reason THE SERVER gave for each one -- never by re-deriving it here
  // from the report's `repairable` flag, which cannot see one of the two
  // refusals at all. No automatic re-check underneath them.
  _stkaRepaired() {
    const r = (this._stockAccuracy && this._stockAccuracy.repair) || {};
    const skipped = Array.isArray(r.skipped) ? r.skipped : [];
    const byReason = new Map();
    for (const row of skipped) {
      const key = (row && row.skip_reason) || '';
      byReason.set(key, (byReason.get(key) || 0) + 1);
    }
    // The TOTAL comes from the server's own count, so it stays true even on a
    // build that sends the counts without the rows; the breakdown is detail
    // layered on top of it, never a substitute for it.
    const total = r.skipped_count == null ? skipped.length : r.skipped_count;
    const breakdown = [...byReason.entries()]
      .map(([reason, n]) => this._stkaTally(this._stkaSkipReasonLabel(reason), n))
      .join('');
    return `
      <div class="sub-chart-card" data-sa-state="repaired" style="padding:40px 32px">
        <div style="font-size:34px;margin-bottom:12px" aria-hidden="true">🧾</div>
        <h3 style="color:var(--text);margin:0 0 16px;font-size:17px">${t('The repair finished.')}</h3>
        <div style="display:flex;flex-direction:column;gap:8px;margin-bottom:16px;max-width:760px">
          ${this._stkaTally(t('Cached balances rewritten'), r.repaired_count)}
          ${this._stkaTally(t('Rows the repair left alone'), total)}
          ${breakdown}
        </div>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 20px;line-height:1.7;max-width:760px">${t('Nothing above has been re-checked yet. Run the check again to see where the shop stands now.')}</p>
        <button class="ret-btn ret-btn-primary" onclick="RetailSystem._loadStockAccuracy()">${t('Run the check again')}</button>
      </div>`;
  },

  // A refused or failed repair. Distinct from _stkaFailed (the CHECK failing)
  // and from _stkaRepaired with zeroes (a repair that ran and moved nothing).
  // A restricted licence refuses this route while leaving the report working,
  // so this panel is a real state on a real install, not a theoretical one.
  _stkaRepairFailed() {
    const s = this._stockAccuracy || {};
    return `
      <div class="sub-chart-card" data-sa-state="repair-failed" style="text-align:center;padding:48px 32px;border:1px solid var(--state-danger-border)">
        <div style="font-size:34px;margin-bottom:12px" aria-hidden="true">⚠️</div>
        <h3 style="color:var(--state-danger-text);margin:0 0 8px;font-size:17px">${t('The repair did not run.')}</h3>
        <p style="color:var(--text);font-size:13px;margin:0 0 8px;line-height:1.7">${this._esc(s.error || '')}</p>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 20px;line-height:1.7;max-width:520px;margin-left:auto;margin-right:auto">${t('No cached balance was changed. The figures are exactly as the check last found them.')}</p>
        <button class="ret-btn ret-btn-ghost" onclick="RetailSystem._loadStockAccuracy()">${t('Try the check again')}</button>
      </div>`;
  },


  // ── SALES BY EMPLOYEE ─────────────────────────────────────────────────────
  //
  // Takings and transaction count per person for the period the Reports page
  // is showing. Reads v13's `sales.actor_user_uid` -- the column the migration
  // added precisely so this question has an answer that is not a free-text
  // guess.
  //
  // ── The route contract, settled BEFORE either side was written ────────────
  //
  //   GET /api/sub/retail/reports/by-employee?days=<int>[&branch_id=<id>]
  //   @mt_require_capability(CAP_REPORTS)          <- same gate as every other
  //                                                   /reports/* route
  //   -> {"status": "success", "success": true, "data": [ <row>, ... ]}
  //   -> {"status": "error", "message": "..."} + a real HTTP status
  //
  // The envelope carries BOTH discriminators deliberately. `{"status":
  // "success"}` is what 74 of retail_api.py's handlers answer and what this
  // file reads; `{"success": true}` is what the five sibling /reports/* routes
  // this panel sits beside actually answer (sales-trend, top-products,
  // payment-methods, summary, by-branch all emit it and carry no `status` key
  // at all) and what Android's ByEmployeeResponse deserializes. One extra key
  // let both clients read the same response with no change to either. This
  // file accepts either marker for the same reason: a build that shipped only
  // the family's shape is still unambiguously reporting success, and an error
  // envelope carries neither.
  //
  // Row shape, all four identity fields null TOGETHER for the unattributed
  // bucket, and `null` rather than `""` because both clients treat blank as
  // absent:
  //
  //   {"actor_user_uid": "<uuid>|null",   "employee_id": "<EMP-000n>|null",
  //    "email": "<users.email>|null",     "employee_name": "<display>|null",
  //    "transactions": <int>, "gross_sales": <n>, "refunds": <n>,
  //    "revenue": <n>, "avg_ticket": <n>}
  //
  // `employee_name` is the server's single pre-formatted display string,
  // computed from the SAME two columns Android reads and in the same order
  // (email, then employee_id), so the two clients cannot disagree about who a
  // row is. `gross_sales`/`refunds` are not drawn by either client today and
  // are kept anyway: they are the audit trail that makes `revenue` checkable,
  // and without them the API cannot answer "why is this one negative?".
  //
  // The figures are metrics.py's, copied not invented -- and the two this file
  // reads most carefully are the ones whose obvious-looking reading is wrong:
  //
  //   * `revenue` is NET of refunds; `transactions` is NOT netted; a refund is
  //     charged to whoever processed it. So a returns-desk shift legitimately
  //     reports NEGATIVE revenue against ZERO transactions, and that is what
  //     makes the buckets sum back to the Revenue KPI at the top of this page.
  //     Neither figure may be clamped or absolutised here.
  //   * `avg_ticket` is read, never recomputed -- see the render loop.
  //
  // Two more things the route itself has to get right, both consequences of
  // how v13 left the data:
  //
  //   * The unattributed bucket (`actor_user_uid: null`) must be passed
  //     through, not filtered. metrics.py already returns it; a route that
  //     drops it would omit most of a real shop's money from a report whose
  //     columns still added up. It is every row written before v13 plus
  //     everything written before the write side began stamping the column.
  //   * There is EXACTLY ONE such row. metrics.py's GROUP BY guarantees a
  //     single NULL key, and this is a hard contract term rather than a
  //     nicety: Android's LazyColumn deliberately does not key on
  //     `actor_user_uid` because two null-uid rows would crash the reporting
  //     screen outright ("Key was already used").
  //   * The identity fields are the route's job, not the metrics module's:
  //     they resolve through registry.db's `users` table on **uid**, not `id`
  //     -- `_actor_user_uid()` in retail_api.py stores `users.uid` (the wire
  //     identity a peer device names a user by), and both columns are uuid4
  //     strings, so joining the wrong one returns nobody while looking
  //     entirely correct. NULL when it cannot be resolved is right and is
  //     handled here; the free-text `cashier` column dressed up as a name is
  //     not (v13's rule: a wrong name is worse than no name), and metrics.py
  //     rule 8 refuses to read that column for exactly this reason.
  //
  // What this file does with all of that is in _attributionCell() above: a
  // resolved person, an actor the route looked up and could not find, and the
  // unattributed bucket are three visibly different answers, and none of them
  // is a guess.
  async _loadEmployeeSales(days, branchQS) {
    const tbody = document.querySelector('#rep-emp-table tbody');
    if (!tbody) return;
    const note = document.getElementById('rep-emp-note');
    if (note) note.style.display = 'none';

    // Arguments when called from _loadReports (which has already resolved
    // both), DOM otherwise -- so this stays independently callable without
    // duplicating the period/branch resolution in two places that could
    // disagree about which period the page is showing.
    if (days == null) {
      const daysEl = document.getElementById('rep-days');
      days = daysEl ? +daysEl.value : 14;
    }
    if (branchQS == null) {
      const branchEl = document.getElementById('rep-branch');
      const branchId = branchEl ? branchEl.value : '';
      branchQS = branchId ? `&branch_id=${encodeURIComponent(branchId)}` : '';
    }

    const fail = (message) => {
      tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:24px">${this._esc(message)}</td></tr>`;
    };

    try {
      // _fetch rather than _get, because the HTTP status is load-bearing
      // here in a way it is nowhere else in this file. "This build has no
      // such route" (404) and "the request did not succeed" are different
      // facts and a manager acts differently on each; collapsing them into
      // one message would be the same category of dishonesty as showing an
      // empty table. (_fetch still handles the 401 re-auth path for us.)
      const res = await this._fetch(`/api/sub/retail/reports/by-employee?days=${days}${branchQS}`);
      if (res.status === 404) return fail(t('Sales by employee are not available on this version.'));
      const body = await res.json().catch(() => ({}));
      // Either discriminator counts -- see the envelope note above. An error
      // envelope carries neither, so this cannot read a failure as a success.
      const succeeded = body.status === 'success' || body.success === true;
      if (!res.ok || !succeeded) {
        return fail(body.message || t('Could not load sales by employee.'));
      }

      const rows = body.data || [];
      if (!rows.length) {
        // A successful response with no rows is a real answer about the shop:
        // nobody sold anything in this period. It must NOT share wording with
        // the failure branches above, which establish nothing about the shop
        // at all -- an empty table shown for a failed request tells an owner
        // their staff sold nothing, which is a worse lie than an error.
        return fail(t('No sales in this period.'));
      }

      let sawUnattributed = false;
      tbody.innerHTML = rows.map(r => {
        const txns = +(r.transactions || 0);
        const revenue = +(r.revenue || 0);
        // avg_ticket is READ, never recomputed here. An earlier draft of this
        // panel divided revenue by transactions, which looked harmless and was
        // not: core/retail/metrics.py exists specifically so that "revenue" is
        // spelled out once (its docstring records that the last time it was
        // spelled twice, this product shipped screens that contradicted each
        // other), and it carries a test whose only job is to stop a second
        // definition of this figure appearing. A division written up here is
        // that second definition, out of that test's reach.
        //
        // It also would not survive contact with the module's actual rules:
        // revenue is NET of refunds while transactions are NOT netted, and a
        // refund is charged to whoever processed it -- so a returns-desk shift
        // legitimately reports negative revenue against zero transactions.
        // revenue/txns is not a definition a frontend would rediscover, and
        // getting it wrong puts a wrong number beside two right ones.
        //
        // Absent (an older build, or a route that omitted the field) is shown
        // as "not recorded" rather than $0.00: printing a zero would state an
        // average nobody computed, next to two figures that were.
        const hasAvg = typeof r.avg_ticket === 'number' && isFinite(r.avg_ticket);
        // The footnote below explains the pre-v13 bucket specifically, so it
        // is driven by that state alone. An 'account_gone' row is NOT that
        // bucket -- it has a recorded actor, it just no longer has an account
        // -- and it carries its own words in the cell.
        if (this._attributionState(r) === 'unattributed') sawUnattributed = true;
        return `<tr>
          <td>${this._attributionCell(r)}</td>
          <td style="text-align:right;color:var(--text-muted)">${this._fmtNum(txns)}</td>
          <td style="text-align:right;font-weight:700">${this._fmt(revenue)}</td>
          <td style="text-align:right;color:var(--text-muted)">${hasAvg ? this._fmt(r.avg_ticket) : this._attribution(null)}</td>
        </tr>`;
      }).join('');

      // Only when a "Not recorded" row is actually on screen. A permanent
      // footnote would be noise on an install with no pre-v13 history, and
      // the note exists to explain a specific row the reader is looking at.
      if (note) note.style.display = sawUnattributed ? 'block' : 'none';
    } catch (e) {
      console.error('Sales-by-employee load failed', e);
      fail(t('Could not load sales by employee.'));
    }
  },

  // Populates the #rep-branch dropdown from GET /branches (already used
  // elsewhere for PO/reorder branch context). "All branches" is always the
  // first option and stays selected by default -- nothing here changes what
  // _loadReports() sends until a user explicitly picks a branch.
  async _loadBranchFilterOptions() {
    try {
      const branches = (await this._get('/api/sub/retail/branches')).data || [];
      this._branches = branches;
      const sel = document.getElementById('rep-branch');
      if (!sel) return;
      sel.innerHTML = '<option value="">All branches</option>' +
        branches.map(b => `<option value="${this._esc(b.id)}">${this._esc(b.name)}</option>`).join('');
    } catch(e) { console.error('Failed to load branches for report filter', e); }
  },

  async _loadReports() {
    const daysEl   = document.getElementById('rep-days');
    const branchEl = document.getElementById('rep-branch');
    const days     = daysEl ? +daysEl.value : 14;
    const branchId = branchEl ? branchEl.value : '';
    const note = document.getElementById('rep-branch-note');
    if (note) note.style.display = branchId ? 'block' : 'none';

    try {
      // branchQS now goes to EVERY widget that has a branch dimension.
      // It used to reach only sales-trend and top-products, because those
      // were the only two routes that read the param -- so picking a branch
      // filtered the charts and left the KPI cards (summary) and the
      // payment doughnut showing all branches, side by side, on one screen.
      // Both routes accept branch_id now (see report_summary /
      // report_payment_methods).
      //
      // by-branch is the deliberate exception: it IS the all-branches
      // comparison, and "compare branches" scoped to one branch is a
      // contradiction. `days` reaches every widget including top-products,
      // which used to be an all-time query on a days-scoped page.
      const branchQS = branchId ? `&branch_id=${encodeURIComponent(branchId)}` : '';

      // Awaited on its own rather than folded into the Promise.all below, and
      // deliberately so: _loadEmployeeSales swallows its own failures into an
      // honest in-table message, whereas anything inside that Promise.all
      // takes the whole chart block down with it via the outer catch. The
      // per-employee route is the newest thing on this page and the only one
      // that may legitimately be absent from a given backend build -- it must
      // not be able to blank the four charts that have always worked.
      await this._loadEmployeeSales(days, branchQS);

      const [trend, top, pay, summary, byBranch] = await Promise.all([
        this._get(`/api/sub/retail/reports/sales-trend?days=${days}${branchQS}`),
        this._get(`/api/sub/retail/reports/top-products?days=${days}&limit=8${branchQS}`),
        this._get(`/api/sub/retail/reports/payment-methods?days=${days}${branchQS}`),
        this._get(`/api/sub/retail/reports/summary?days=${days}${branchQS}`),
        this._get(`/api/sub/retail/reports/by-branch?days=${days}`),
      ]);

      const s = summary.data || {};
      const setEl = (id, val) => { const el = document.getElementById(id); if(el) el.textContent=val; };
      setEl('rep-rev',    this._fmt(s.revenue));
      setEl('rep-txn',    this._fmtNum(s.transactions));
      setEl('rep-profit', `${this._fmt(s.gross_profit)} (${s.margin_pct||0}%)`);
      setEl('rep-avg',    this._fmt(s.avg_ticket));

      if (window.Chart) {
        const chartDefs = [
          ['rep-trend', { type:'line',
            data:{ labels:trend.labels||[], datasets:[
              { label:'Revenue', data:trend.data||[], borderColor:'#38bdf8', backgroundColor:'rgba(56,189,248,0.1)', fill:true, tension:.4, yAxisID:'y' },
              { label:'Transactions', data:trend.transactions||[], borderColor:'#a855f7', backgroundColor:'transparent', borderDash:[4,4], type:'bar', yAxisID:'y1' }
            ]},
            opts:{ responsive:true, maintainAspectRatio:false,
              plugins:{ legend:{labels:{color:'#94a3b8'}} },
              scales:{ y:{ticks:{color:'#94a3b8',callback:v=>'$'+v},grid:{color:'rgba(255,255,255,0.05)'}},
                y1:{position:'right',ticks:{color:'#a855f7'},grid:{display:false}},
                x:{ticks:{color:'#94a3b8'},grid:{display:false}} } }
          }],
          // Built by the same shared helper as the dashboard's r-dash-pay
          // chart: identical named color lookup, and identical handling of a
          // net-negative tender (doughnut normally, horizontal bar when any
          // bucket is below zero). See retailPaymentChartConfig().
          (() => {
            const cfg = retailPaymentChartConfig(
              (pay.data||[]).map(r=>r.payment_method),
              (pay.data||[]).map(r=>r.revenue),
              '#94a3b8');
            return ['rep-pay', { type: cfg.type, data: cfg.data, opts: cfg.options }];
          })(),
          ['rep-top', { type:'bar',
            data:{ labels:top.labels||[], datasets:[
              { label:'Units Sold', data:top.data||[], backgroundColor:'#8b5cf6' },
              { label:'Revenue ($)', data:top.revenue||[], backgroundColor:'#10b981' }
            ]},
            opts:{ responsive:true, maintainAspectRatio:false,
              plugins:{ legend:{labels:{color:'#94a3b8'}} },
              scales:{ y:{ticks:{color:'#94a3b8'},grid:{color:'rgba(255,255,255,0.05)'}},
                x:{ticks:{color:'#94a3b8',maxRotation:30},grid:{display:false}} } }
          }],
          ['rep-branch-chart', { type:'bar',
            data:{ labels:byBranch.labels||[], datasets:[
              { label:'Revenue', data:byBranch.data||[], backgroundColor:'#38bdf8' },
              { label:'Transactions', data:byBranch.transactions||[], backgroundColor:'#a855f7', yAxisID:'y1' }
            ]},
            opts:{ responsive:true, maintainAspectRatio:false,
              plugins:{ legend:{labels:{color:'#94a3b8'}} },
              scales:{ y:{ticks:{color:'#94a3b8',callback:v=>'$'+v},grid:{color:'rgba(255,255,255,0.05)'}},
                y1:{position:'right',ticks:{color:'#a855f7'},grid:{display:false}},
                x:{ticks:{color:'#94a3b8'},grid:{display:false}} } }
          }],
        ];
        chartDefs.forEach(([id, cfg]) => {
          const el = document.getElementById(id);
          if (!el) return;
          try { Chart.getChart && Chart.getChart(el)?.destroy(); } catch(e) {}
          if ((cfg.data.labels||[]).length === 0) {
            el.parentElement.innerHTML = '<div style="height:260px;display:flex;align-items:center;justify-content:center;color:var(--text-muted)">No data for this period</div>';
            return;
          }
          new Chart(el.getContext('2d'), { type: cfg.type, data: cfg.data, options: cfg.opts });
        });
      }
    } catch(e) { console.error('Retail reports error:', e); }
  },

  // ── SCANNER SETTINGS ──────────────────────────────────────────────────────
  _renderScannerSettings(c) {
    this._injectStyles();
    const cfg = this.scannerCfg();
    const desktop = this._isDesktopScanner();
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">⚙️ Barcode Scanner</h2>
        <div style="display:flex;gap:10px">
          <button class="ret-btn ret-btn-ghost" onclick="RetailSystem._resetScannerSettings()">Reset</button>
          <button class="sub-btn-primary" onclick="RetailSystem._saveScannerSettings()">Save Settings</button>
        </div>
      </div>

      ${!desktop ? `<div class="sub-chart-card" style="margin-bottom:18px;border-color:var(--state-warning-text)">
        <div style="color:var(--state-warning-text)">⚠️ Hardware barcode scanning is available on the Windows desktop app only. Manual barcode entry still works here.</div>
      </div>` : ''}

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start">
        <!-- Status + Test -->
        <div class="sub-chart-card">
          <div class="sub-chart-title" style="margin-bottom:14px">Scanner Status</div>
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
            <span id="sc-status-dot" style="width:12px;height:12px;border-radius:50%;background:var(--state-success-text);display:inline-block"></span>
            <span id="sc-status-text" style="color:var(--text-primary);font-size:16px;font-weight:600">Ready</span>
          </div>
          <div id="sc-last-scan" style="color:var(--text-muted);font-size:13px;margin-bottom:18px">No scans yet this session</div>

          <div class="sub-chart-title" style="margin:0 0 10px">Test Scanner</div>
          <p style="color:var(--text-muted);font-size:12px;margin:0 0 10px">Click below, then scan any barcode — the captured value appears instantly.</p>
          <div style="display:flex;gap:8px;align-items:center">
            <!-- Same story as the Reports dropdowns: the paint was an inline
                 copy of .ret-search's recipe, outline:none included, on a
                 readonly input that is still focusable and is the one thing a
                 shopkeeper tabs to when testing a scanner. Only the LAYOUT
                 stays inline; the paint, the focus ring and both touch axes
                 come from .ret-input. -->
            <input id="sc-test-value" class="ret-input" readonly placeholder="Captured value will appear here…"
              style="flex:1;font-family:monospace" />
            <button class="ret-btn ret-btn-primary" id="sc-test-btn" onclick="RetailSystem._armTestScan()">Start Test</button>
          </div>

          <div style="margin-top:18px;background:var(--state-info-surface);border:1px solid var(--state-info-border);border-radius:10px;padding:12px 14px;color:var(--text-secondary);font-size:12px;line-height:1.5">
            ℹ️ <strong style="color:var(--text-primary)">Note:</strong> USB &amp; Bluetooth HID scanners behave exactly like a keyboard,
            so the app cannot reliably tell whether one is physically plugged in. Status is based on recent scan activity,
            not a hardware connection. A true connection status will be possible once Serial/COM support is added.
          </div>
        </div>

        <!-- Configuration -->
        <div class="sub-chart-card">
          <div class="sub-chart-title" style="margin-bottom:14px">Configuration</div>

          <div class="ret-field">
            <label>Scanner Enabled</label>
            <select id="sc-enabled">
              <option value="true"  ${cfg.enabled ? 'selected' : ''}>On — listen for scans</option>
              <option value="false" ${!cfg.enabled ? 'selected' : ''}>Off — manual entry only</option>
            </select>
          </div>

          <div class="ret-field">
            <label>Input Mode</label>
            <select id="sc-mode">
              <option value="auto"     ${cfg.inputMode === 'auto' ? 'selected' : ''}>Auto Detect (recommended)</option>
              <option value="keyboard" ${cfg.inputMode === 'keyboard' ? 'selected' : ''}>Keyboard HID</option>
              <option value="serial" disabled>Serial / COM — coming soon</option>
            </select>
          </div>

          <div class="ret-field-row">
            <div class="ret-field">
              <label>Barcode Timeout (ms)</label>
              <input type="number" id="sc-timeout" value="${cfg.timeoutMs}" min="10" max="500" step="5" />
            </div>
            <div class="ret-field">
              <label>Min Length</label>
              <input type="number" id="sc-minlen" value="${cfg.minLength}" min="1" max="64" />
            </div>
          </div>

          <div class="ret-field-row">
            <div class="ret-field"><label>Prefix (stripped)</label><input id="sc-prefix" value="${cfg.prefix || ''}" placeholder="None" /></div>
            <div class="ret-field"><label>Suffix (stripped)</label><input id="sc-suffix" value="${cfg.suffix || ''}" placeholder="None" /></div>
          </div>

          <div class="ret-field">
            <label>Success Sound</label>
            <select id="sc-sound">
              <option value="true"  ${cfg.sound ? 'selected' : ''}>On — beep on each scan</option>
              <option value="false" ${!cfg.sound ? 'selected' : ''}>Off</option>
            </select>
          </div>
        </div>

        <!-- Receipt Printer (Wave 1B, Part O/P/Q) -->
        <div class="sub-chart-card">
          <div class="sub-chart-title" style="margin-bottom:14px">🖨️ Receipt Printer</div>
          <p style="color:var(--text-muted);font-size:12px;margin:0 0 14px">
            Uses your Windows-installed printer through the normal print dialog — works with a
            thermal receipt printer (via its own Windows driver), a regular printer, or a PDF
            printer. There is no separate USB/Bluetooth thermal protocol implemented.
          </p>
          <div class="ret-field">
            <label>Paper Width</label>
            <select id="pr-width">
              <option value="80mm" ${(this._printerCfg().paperWidth !== '58mm') ? 'selected' : ''}>80mm (standard)</option>
              <option value="58mm" ${(this._printerCfg().paperWidth === '58mm') ? 'selected' : ''}>58mm (compact)</option>
            </select>
          </div>
          <div style="display:flex;gap:10px;margin-top:6px">
            <button class="ret-btn ret-btn-ghost" style="flex:1" onclick="RetailSystem._savePrinterSettings()">Save</button>
            <button class="ret-btn ret-btn-primary" style="flex:1" onclick="RetailSystem._testPrint()">Test Print</button>
          </div>
        </div>
      </div>`;

    // Live status refresh while the page is open.
    this._refreshScannerStatus();
    clearInterval(this._scStatusTimer);
    this._scStatusTimer = setInterval(() => {
      if (!document.getElementById('sc-status-text')) { clearInterval(this._scStatusTimer); return; }
      this._refreshScannerStatus();
    }, 1000);
  },

  _refreshScannerStatus() {
    const dot  = document.getElementById('sc-status-dot');
    const text = document.getElementById('sc-status-text');
    const last = document.getElementById('sc-last-scan');
    if (!text) return;
    const cfg = this.scannerCfg();
    const st  = this._scan;
    if (!cfg.enabled) {
      text.textContent = 'Disabled';
      if (dot) dot.style.background = 'var(--text-tertiary)';
      if (last) last.textContent = 'Scanner listening is turned off';
      return;
    }
    if (st.lastScanAt) {
      const secs = Math.round((Date.now() - st.lastScanAt) / 1000);
      const ago  = secs < 60 ? `${secs}s ago` : `${Math.round(secs / 60)}m ago`;
      // "Ready" within the last 10s of a scan, otherwise idle-but-waiting.
      text.textContent = secs < 10 ? 'Ready' : 'Waiting for Scanner';
      if (dot) dot.style.background = secs < 10 ? 'var(--state-success-text)' : 'var(--state-warning-text)';
      if (last) last.innerHTML = `Last Scan: <span style="color:var(--text-primary)">${ago}</span> · <span style="font-family:monospace">${st.lastScanCode || ''}</span>`;
    } else {
      text.textContent = 'Waiting for Scanner';
      if (dot) dot.style.background = 'var(--state-warning-text)';
      if (last) last.textContent = 'No scans yet this session';
    }
  },

  _armTestScan() {
    const btn = document.getElementById('sc-test-btn');
    const out = document.getElementById('sc-test-value');
    if (btn) { btn.textContent = 'Listening…'; btn.disabled = true; }
    if (out) { out.value = ''; out.placeholder = 'Scan now…'; }
    this.captureNextScan((code) => {
      if (out) out.value = code;
      if (btn) { btn.textContent = 'Start Test'; btn.disabled = false; }
      this._refreshScannerStatus();
    });
    setTimeout(() => {
      if (btn && btn.disabled) { btn.textContent = 'Start Test'; btn.disabled = false; this.cancelCapture(); }
    }, 20000);
  },

  _saveScannerSettings() {
    const cfg = {
      enabled:   document.getElementById('sc-enabled')?.value === 'true',
      inputMode: document.getElementById('sc-mode')?.value || 'auto',
      timeoutMs: Math.max(10, Math.min(500, +document.getElementById('sc-timeout')?.value || 50)),
      minLength: Math.max(1, Math.min(64, +document.getElementById('sc-minlen')?.value || 3)),
      prefix:    document.getElementById('sc-prefix')?.value || '',
      suffix:    document.getElementById('sc-suffix')?.value || '',
      sound:     document.getElementById('sc-sound')?.value === 'true',
    };
    this.saveScannerCfg(cfg);
    SubsystemApp.showToast('Scanner settings saved', 'success');
    this._refreshScannerStatus();
  },

  _resetScannerSettings() {
    this.saveScannerCfg(Object.assign({}, this._scannerDefaults));
    SubsystemApp.showToast('Scanner settings reset to defaults', 'success');
    this._renderScannerSettings(document.getElementById('sub-content'));
  },

  _savePrinterSettings() {
    const width = document.getElementById('pr-width')?.value === '58mm' ? '58mm' : '80mm';
    this.savePrinterCfg({ paperWidth: width });
    SubsystemApp.showToast('Printer settings saved', 'success');
  },

  // Uses clearly-marked synthetic data (never a real sale) so testing the
  // print path never risks printing/exposing real customer or financial
  // records -- matches this project's synthetic-test-data convention.
  _testPrint() {
    this._printReceipt({
      sale_number: 'TEST-0000',
      created_at: new Date().toLocaleString(),
      lines: [{ name: 'Test Product (sample)', quantity: 1, line_total: 10 }],
      subtotal: 10, discount_amount: 0, tax_amount: 0, total: 10, amount_paid: 10, change: 0,
    });
  },
};

window.RetailSystem = RetailSystem;
