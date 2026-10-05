/* Aura Today: the owner's home screen (P2 Track B, frontend half).
 *
 * WHAT IT IS. One screen that answers "how is the shop doing, and what needs me
 * right now?". Top: today's takings against the same hour yesterday, how the
 * money arrived, and the last 14 days. Below: a short list of CARDS, one per
 * thing that has money or a decision attached (customers who owe, cheques due,
 * stock to reorder, drawers to check, tax reporting, a quiet branch). Each card
 * has ONE primary button that goes to the place the work is done. Anything that
 * is fine is NOT a card: it is one line in the "All clear" card, so calm is
 * visible and not just an empty space.
 *
 * WHERE THE ANSWERS COME FROM. One call, GET /api/sub/retail/reports/today. The
 * server sends figures and codes, never sentences: every sentence is built HERE
 * with t(), so Arabic is a catalogue entry and not a server change. Counts go in
 * slots ({count}, {amount}) after translation, and each noun phrase has a
 * singular and a plural English key; the Arabic is count-neutral so it reads
 * naturally for 1 and for 12 with the same text.
 *
 * WHAT IT MUST NEVER DO.
 *   - Show to a cashier. RetailSystem._renderDashboard sends cashiers to the
 *     cashier landing before it ever gets here, and render() checks again and
 *     makes ZERO requests when the person lacks retail.reports.
 *   - Say a thing is fine when it does not know. A figure the server could not
 *     compute arrives as null and is simply not drawn (a percentage is never
 *     "0%" when there was nothing to compare against).
 *   - Imply a card covers the whole shop when it covers only this computer.
 *     Drawers and e-invoicing live on this computer until the cash-session
 *     sync wave, so those two cards carry a "this computer only" label.
 *   - Paint a stale answer. A render another render superseded writes nothing
 *     (RetailSystem._isStaleRender), same rule as the old dashboard.
 *
 * LOOK. Tokens and logical properties only (no colour literals), so the same
 * rules serve every theme and RTL. The chart is inline SVG drawn here (no chart
 * library), mirrored in RTL, with a visually hidden table carrying the figures.
 *
 * The old dashboard body in subsystem-retail.js stays as the fallback for an
 * install where this file is missing; _renderDashboard delegates here first.
 */
(function () {
  'use strict';

  const ROUTE = '/api/sub/retail/reports/today';
  const CSS_ID = 'aura-today-css';
  const DID_KEY = 'aura_today_did';
  const RET = 'retail.reports';

  // window.t takes ONE argument (i18n.js), so {slots} are filled here, after
  // translation, exactly as setup-guide.js does it.
  const tr = (s, vars) => {
    let out = typeof window.t === 'function' ? window.t(s) : s;
    if (vars) Object.keys(vars).forEach((k) => { out = out.split('{' + k + '}').join(String(vars[k])); });
    return out;
  };
  const R = () => window.RetailSystem;
  const esc = (s) => (R() && R()._esc ? R()._esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])));

  // A sentence as HTML. The sentence text is escaped; the slot VALUES are
  // already-built HTML (a money span, a <bdi> number), never raw server text.
  const S = (key, vars) => {
    let out = esc(tr(key));
    if (vars) Object.keys(vars).forEach((k) => { out = out.split('{' + k + '}').join(vars[k]); });
    return out;
  };
  const plural = (n, one, many, vars) => S(Number(n) === 1 ? one : many, vars);

  const isFiniteNum = (n) => typeof n === 'number' && Number.isFinite(n);
  // Numbers sit in <bdi dir="ltr"> so an Arabic sentence cannot reorder "3 of 10".
  const num = (n) => '<bdi dir="ltr">' + esc(String(n)) + '</bdi>';
  // Free text that carries digits (a date, a cheque number) is isolated too.
  const bidi = (s) => '<bdi>' + esc(s) + '</bdi>';
  const money = (n) => (isFiniteNum(n) && R() ? R()._money(n) : esc('—'));
  const pct = (p, signed) => {
    if (!isFiniteNum(p)) return '';
    const sign = p < 0 ? '−' : (signed && p > 0 ? '+' : '');
    return '<bdi dir="ltr">' + sign + Math.abs(p).toFixed(1) + '%</bdi>';
  };

  function app() { return window.SubsystemApp || null; }
  function isOwner() { return !!(app() && app().role === 'admin'); }
  // Same fail-open rule as SubsystemApp.hasCapability itself; a standalone load
  // (no shell) behaves as "may".
  function hasCap(code) {
    const a = app();
    return !a || typeof a.hasCapability !== 'function' || !!a.hasCapability(code);
  }
  function isRtl() {
    try {
      const el = document.documentElement;
      if (el && el.dir) return el.dir === 'rtl';
    } catch (e) { /* fall through */ }
    return !!(window.AuraI18n && window.AuraI18n.current === 'ar');
  }
  function dateLang() { return (window.AuraI18n && window.AuraI18n.current === 'ar') ? 'ar-JO-u-nu-latn' : 'en-US'; }

  function shortDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    if (!m) return '';
    const d = new Date(+m[1], +m[2] - 1, +m[3]);
    const opts = { weekday: 'short', day: 'numeric', month: 'short' };
    try { return d.toLocaleDateString(dateLang(), opts); } catch (e) { return d.toLocaleDateString('en-US', opts); }
  }
  function clockOf(iso) {
    const m = /[T ](\d{2}:\d{2})/.exec(String(iso || ''));
    return m ? m[1] : '';
  }

  // ── icons: Lucide geometry, 2px round strokes, tinted by currentColor ──────
  const P = {
    coins: '<path d="M11 15h2a2 2 0 1 0 0-4h-3c-.6 0-1.1.2-1.4.6L3 17"/><path d="m7 21 1.6-1.4c.3-.4.8-.6 1.4-.6h4c1.1 0 2.1-.4 2.8-1.2l4.6-4.4a2 2 0 0 0-2.75-2.91l-4.2 3.9"/><path d="m2 16 6 6"/><circle cx="16" cy="9" r="2.9"/><circle cx="6" cy="5" r="3"/>',
    truck: '<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/><path d="M15 18H9"/><path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.624l-3.48-4.35A1 1 0 0 0 17.52 8H14"/><circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/>',
    bank: '<line x1="3" x2="21" y1="22" y2="22"/><line x1="6" x2="6" y1="18" y2="11"/><line x1="10" x2="10" y1="18" y2="11"/><line x1="14" x2="14" y1="18" y2="11"/><line x1="18" x2="18" y1="18" y2="11"/><polygon points="12 2 20 7 4 7"/>',
    note: '<rect width="20" height="12" x="2" y="6" rx="2"/><circle cx="12" cy="12" r="2"/><path d="M6 12h.01M18 12h.01"/>',
    receipt: '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M14 8H8"/><path d="M16 12H8"/><path d="M13 16H8"/>',
    store: '<path d="m2 7 4.41-4.41A2 2 0 0 1 7.83 2h8.34a2 2 0 0 1 1.42.59L22 7"/><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><path d="M15 22v-4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4"/><path d="M2 7h20"/>',
    up: '<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
    dn: '<polyline points="22 17 13.5 8.5 8.5 13.5 2 7"/><polyline points="16 17 22 17 22 11"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    spark: '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    cart: '<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"/>',
  };
  const ic = (name, cls) => '<svg class="td-ic ' + (cls || '') + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + (P[name] || '') + '</svg>';

  // ── styles ─────────────────────────────────────────────────────────────────
  function injectCss() {
    if (typeof document === 'undefined' || !document.head) return;
    if (document.getElementById(CSS_ID)) return;
    const st = document.createElement('style');
    st.id = CSS_ID;
    // Tokens and logical properties only. The container is the screen's own
    // content box (the sidebar already took its share of the window), so the
    // layout breakpoints are container queries: 1366x768 gives ~1126px of
    // content, which is the 2-column hero and 3-column cards case.
    st.textContent = `
      .td { container-type: inline-size; container-name: today; display: flex; flex-direction: column; gap: var(--space-comfy); }
      .td-head { display: flex; align-items: center; justify-content: space-between; gap: var(--space-comfy); flex-wrap: wrap; }
      .td-greet { margin: 0; font-size: var(--text-size-title); font-weight: var(--text-weight-bold); color: var(--text-primary); line-height: var(--text-leading-tight); }
      .td-meta { margin: var(--space-hairline) 0 0; font-size: var(--text-size-body); color: var(--text-tertiary); }
      .td-card { background: var(--surface-raised); border: 1px solid var(--border-hairline); border-radius: var(--radius-card); box-shadow: var(--elevation-card); min-inline-size: 0; }
      .td-loading { margin: 0; padding: var(--space-roomy); color: var(--text-tertiary); font-size: var(--text-size-body); }
      .td-error { display: flex; flex-direction: column; align-items: flex-start; gap: var(--space-base); padding: var(--space-comfy); color: var(--state-danger-text); background: var(--state-danger-surface); border: 1px solid var(--state-danger-border); border-radius: var(--radius-card); }
      .td-eyebrow { font-size: var(--text-size-meta); font-weight: var(--text-weight-bold); letter-spacing: .06em; text-transform: uppercase; color: var(--text-tertiary); }
      html[dir="rtl"] .td-eyebrow { letter-spacing: 0; text-transform: none; }

      .td-hero { overflow: hidden; }
      .td-hero-top { display: grid; grid-template-columns: minmax(0, 1fr); }
      .td-nums { padding: var(--space-comfy) var(--space-roomy); min-inline-size: 0; }
      .td-chart-wrap { padding: var(--space-base) var(--space-comfy) var(--space-snug); min-inline-size: 0; border-block-start: 1px solid var(--border-hairline); }
      @container today (min-width: 1000px) {
        .td-hero-top { grid-template-columns: minmax(0, 5fr) minmax(0, 7fr); }
        .td-chart-wrap { border-block-start: 0; border-inline-start: 1px solid var(--border-hairline); }
      }
      .td-bigrow { display: flex; align-items: flex-end; justify-content: space-between; gap: var(--space-snug) var(--space-comfy); flex-wrap: wrap; }
      .td-big { margin-block-start: var(--space-tight); font-size: var(--text-size-total); font-weight: var(--text-weight-bold); letter-spacing: -.02em; line-height: var(--text-leading-tight); color: var(--text-money); }
      .td-mini { font-size: var(--text-size-body); color: var(--text-secondary); text-align: end; line-height: var(--text-leading-body); }
      .td-mini b { color: var(--text-money); font-weight: var(--text-weight-bold); }
      .td-sub { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-tight) var(--space-base); margin-block-start: var(--space-snug); font-size: var(--text-size-body); color: var(--text-secondary); }
      .td-delta { display: inline-flex; align-items: center; gap: var(--space-tight); padding: var(--space-hairline) var(--space-snug); border-radius: var(--radius-pill); background: var(--state-success-surface); border: 1px solid var(--state-success-border); color: var(--state-success-text); font-weight: var(--text-weight-bold); font-size: var(--text-size-meta); }
      .td-delta.is-down { background: var(--surface-sunken); border-color: var(--border-default); color: var(--text-secondary); }
      .td-gp { margin-block-start: var(--space-snug); font-size: var(--text-size-body); color: var(--text-secondary); }
      .td-gp b { color: var(--text-money); font-weight: var(--text-weight-bold); }
      .td-gp em { font-style: normal; color: var(--text-tertiary); }
      .td-tbar { display: flex; gap: var(--space-hairline); block-size: 10px; margin-block-start: var(--space-base); border-radius: var(--radius-control); overflow: hidden; background: var(--surface-sunken); }
      .td-tbar i { display: block; block-size: 100%; }
      .td-t0 { background: var(--accent-action); }
      .td-t1 { background: var(--text-secondary); }
      .td-t2 { background: var(--text-tertiary); }
      .td-t3 { background: var(--border-strong); }
      .td-t4 { background: var(--surface-active); }
      .td-tleg { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-tight) var(--space-roomy); margin-block-start: var(--space-snug); font-size: var(--text-size-meta); color: var(--text-secondary); }
      .td-tleg div { display: flex; align-items: center; gap: var(--space-snug); min-inline-size: 0; }
      .td-sq { inline-size: 10px; block-size: 10px; border-radius: 3px; flex: none; }
      .td-tleg .money { margin-inline-start: auto; color: var(--text-money); font-weight: var(--text-weight-semibold); }
      .td-tpct { inline-size: 3.2em; text-align: end; color: var(--text-tertiary); }

      .td-ch-head { display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-base); flex-wrap: wrap; }
      .td-ch-title { margin: 0; font-size: var(--text-size-body); font-weight: var(--text-weight-bold); color: var(--text-primary); }
      .td-ch-title span { font-weight: var(--text-weight-medium); color: var(--text-tertiary); }
      .td-legend { display: flex; flex-wrap: wrap; gap: var(--space-tight) var(--space-base); font-size: var(--text-size-meta); color: var(--text-tertiary); }
      .td-legend span { display: inline-flex; align-items: center; gap: var(--space-snug); white-space: nowrap; }
      .td-lg { display: inline-block; inline-size: 10px; block-size: 10px; border-radius: 2px; }
      .td-lg.bar { background: var(--accent-action); opacity: .35; }
      .td-lg.now { background: var(--accent-action); }
      .td-lg.yd { block-size: 2px; inline-size: 12px; background: var(--text-primary); }
      .td-lg.av { block-size: 0; inline-size: 14px; border-block-start: 2px dashed var(--text-tertiary); border-radius: 0; }
      .td-chart { margin-block-start: var(--space-tight); }
      .td-chart svg { display: block; inline-size: 100%; block-size: auto; max-block-size: 210px; direction: ltr; }
      .td-grid { stroke: var(--border-hairline); }
      .td-base { stroke: var(--border-strong); }
      .td-ylab, .td-xlab { fill: var(--text-tertiary); font-size: var(--text-size-micro); font-variant-numeric: tabular-nums; }
      .td-xnow { fill: var(--text-primary); font-weight: var(--text-weight-bold); }
      .td-bar { fill: var(--accent-action); fill-opacity: .35; }
      .td-bar-now { fill: var(--accent-action); }
      .td-yday { stroke: var(--text-primary); stroke-width: 2; stroke-linecap: round; }
      .td-avg { stroke: var(--text-tertiary); stroke-width: 1.25; stroke-dasharray: 4 4; }
      .td-sofar { fill: var(--text-secondary); font-size: var(--text-size-micro); font-weight: var(--text-weight-bold); }

      .td-branches { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); border-block-start: 1px solid var(--border-hairline); background: var(--surface-till); }
      .td-br { padding: var(--space-snug) var(--space-comfy) var(--space-base); display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: baseline; gap: var(--space-hairline) var(--space-base); }
      .td-br + .td-br { border-inline-start: 1px solid var(--border-hairline); }
      .td-br-name { display: flex; align-items: center; gap: var(--space-tight); font-weight: var(--text-weight-bold); font-size: var(--text-size-body); color: var(--text-secondary); min-inline-size: 0; }
      .td-br-delta { display: inline-flex; align-items: center; gap: var(--space-hairline); font-size: var(--text-size-meta); font-weight: var(--text-weight-bold); color: var(--text-secondary); white-space: nowrap; }
      .td-br-delta.is-up { color: var(--state-success-text); }
      .td-br-amt { font-size: var(--text-size-subhead); font-weight: var(--text-weight-bold); color: var(--text-money); }
      .td-br-meta { font-size: var(--text-size-meta); color: var(--text-tertiary); text-align: end; }
      .td-share { grid-column: 1 / -1; block-size: 4px; margin-block-start: var(--space-snug); border-radius: 2px; background: var(--surface-active); overflow: hidden; }
      .td-share i { display: block; block-size: 100%; background: var(--accent-action); }
      .td-ic { inline-size: 16px; block-size: 16px; flex: none; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }

      .td-needs-h { margin-block-start: var(--space-snug); display: flex; align-items: baseline; gap: var(--space-base); flex-wrap: wrap; }
      .td-needs-h h2 { margin: 0; font-size: var(--text-size-subhead); font-weight: var(--text-weight-bold); color: var(--text-primary); }
      .td-count { display: inline-grid; place-items: center; min-inline-size: 22px; block-size: 20px; padding-inline: var(--space-snug); border-radius: var(--radius-pill); background: var(--surface-raised); box-shadow: inset 0 0 0 1px var(--border-default); color: var(--text-primary); font-size: var(--text-size-meta); font-weight: var(--text-weight-bold); }
      .td-needs-h p { margin: 0; color: var(--text-tertiary); font-size: var(--text-size-body); }
      .td-cards { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--space-comfy); align-items: stretch; }
      @container today (min-width: 760px) { .td-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
      @container today (min-width: 1000px) { .td-cards { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
      .td-act { display: flex; flex-direction: column; padding: var(--space-comfy); }
      .td-act-h { display: flex; align-items: center; gap: var(--space-base); }
      .td-act-ic { inline-size: 30px; block-size: 30px; flex: none; display: grid; place-items: center; border-radius: var(--radius-control); }
      .td-act-ic .td-ic { inline-size: 17px; block-size: 17px; }
      .td-risk .td-act-ic { background: var(--state-danger-surface); color: var(--state-danger-text); }
      .td-wait .td-act-ic { background: var(--state-warning-surface); color: var(--state-warning-text); }
      .td-info .td-act-ic { background: var(--state-info-surface); color: var(--state-info-text); }
      .td-okc .td-act-ic { background: var(--state-success-surface); color: var(--state-success-text); }
      .td-kind { font-weight: var(--text-weight-bold); font-size: var(--text-size-body); color: var(--text-primary); }
      .td-tag { margin-inline-start: auto; display: inline-flex; align-items: center; gap: var(--space-snug); font-size: var(--text-size-meta); font-weight: var(--text-weight-semibold); color: var(--text-tertiary); white-space: nowrap; }
      .td-tag::before { content: ""; inline-size: 8px; block-size: 8px; border-radius: 50%; background: currentColor; flex: none; }
      .td-risk .td-tag { color: var(--state-danger-text); }
      .td-wait .td-tag { color: var(--state-warning-text); }
      .td-info .td-tag { color: var(--state-info-text); }
      .td-scope { align-self: flex-start; margin-block-start: var(--space-snug); padding: var(--space-hairline) var(--space-snug); border-radius: var(--radius-pill); border: 1px solid var(--border-default); background: var(--surface-sunken); font-size: var(--text-size-meta); font-weight: var(--text-weight-semibold); color: var(--text-secondary); }
      .td-head-line { margin: var(--space-base) 0 var(--space-snug); font-size: var(--text-size-body-lg); line-height: 1.4; font-weight: var(--text-weight-medium); color: var(--text-primary); }
      .td-head-line .money { font-weight: var(--text-weight-bold); color: var(--text-money); }
      .td-body { margin: 0 0 var(--space-snug); font-size: var(--text-size-body); color: var(--text-secondary); line-height: var(--text-leading-body); }
      .td-rows { list-style: none; margin: 0; padding: 0; }
      .td-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: var(--space-base); min-block-size: 40px; padding-block: var(--space-hairline); border-block-start: 1px solid var(--border-hairline); font-size: var(--text-size-body); color: var(--text-primary); }
      .td-who { font-weight: var(--text-weight-semibold); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; line-height: 1.3; }
      .td-who small { display: block; font-size: var(--text-size-meta); font-weight: var(--text-weight-medium); color: var(--text-tertiary); white-space: normal; overflow-wrap: anywhere; }
      .td-who .ret-rowbtn { font: inherit; font-weight: var(--text-weight-semibold); color: var(--text-primary); text-align: start; padding: 0; background: none; border: 0; cursor: pointer; max-inline-size: 100%; overflow: hidden; text-overflow: ellipsis; }
      .td-amt { text-align: end; font-weight: var(--text-weight-bold); white-space: nowrap; font-variant-numeric: tabular-nums; color: var(--text-money); }
      .td-amt.is-plain { color: var(--text-secondary); font-weight: var(--text-weight-semibold); }
      .td-amt.is-short { color: var(--text-money-negative); }
      .td-note { margin: var(--space-snug) 0 0; font-size: var(--text-size-meta); color: var(--text-tertiary); line-height: 1.4; }
      .td-note .money { color: var(--text-secondary); font-weight: var(--text-weight-bold); }
      .td-foot { margin-block-start: auto; padding-block-start: var(--space-base); display: flex; align-items: center; gap: var(--space-base); flex-wrap: wrap; }
      .td-link { border: 0; background: none; padding: var(--space-tight) var(--space-hairline); cursor: pointer; color: var(--accent-action); font-size: var(--text-size-body); font-weight: var(--text-weight-bold); white-space: nowrap; text-decoration: underline; text-decoration-color: transparent; text-underline-offset: 3px; }
      .td-link:hover { text-decoration-color: currentColor; }
      .td-clear { box-shadow: none; background: var(--surface-till); }
      .td-clear .td-row { grid-template-columns: auto minmax(0, 1fr); justify-content: start; }
      .td-clear .td-ic { color: var(--state-success-text); }
      .td-clear-wide { grid-column: 1 / -1; }

      .td-did { padding: 0; }
      .td-did-sum { display: flex; align-items: center; gap: var(--space-base); padding: var(--space-base) var(--space-comfy); cursor: pointer; font-weight: var(--text-weight-bold); font-size: var(--text-size-body-lg); color: var(--text-primary); list-style: none; }
      .td-did-sum::-webkit-details-marker { display: none; }
      .td-did-sum .td-chev { margin-inline-start: auto; color: var(--text-tertiary); transition: transform var(--motion-fast); }
      .td-did[open] .td-chev { transform: rotate(180deg); }
      .td-did-list { list-style: none; margin: 0; padding: 0 var(--space-comfy) var(--space-base); }
      .td-did-list li { display: grid; grid-template-columns: 4em minmax(0, 1fr) auto; gap: var(--space-base); padding-block: var(--space-snug); border-block-start: 1px solid var(--border-hairline); font-size: var(--text-size-body); line-height: 1.4; color: var(--text-primary); }
      .td-did-list time { color: var(--text-tertiary); font-variant-numeric: tabular-nums; font-size: var(--text-size-meta); }
      .td-did-scope { font-size: var(--text-size-meta); color: var(--text-tertiary); white-space: nowrap; }

      .td-sr { position: absolute; inline-size: 1px; block-size: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
      .td :focus-visible { outline: var(--focus-ring-width, 3px) solid var(--focus-ring-color); outline-offset: var(--focus-ring-offset, 2px); }
      @media (prefers-reduced-motion: reduce) { .td * { transition: none; } }
    `;
    document.head.appendChild(st);
  }

  // ── the "Aura did today" open state: per company + user, like setup ticks ──
  function didKey() {
    const u = (app() && app().currentUser) || {};
    const company = u.company_id || (u.company && (u.company.id || u.company)) || 'co';
    return DID_KEY + ':' + String(company) + ':' + String(u.id || 'anon');
  }
  function readDidOpen() {
    try { return localStorage.getItem(didKey()) !== '0'; } catch (e) { return true; }
  }
  function didToggle(el) {
    try { localStorage.setItem(didKey(), el && el.open ? '1' : '0'); } catch (e) { /* the choice just does not stick */ }
  }

  // ── what each button does ──────────────────────────────────────────────────
  function act(name) {
    const a = app();
    const go = (screen) => { if (a && a._navigate) a._navigate(screen); };
    switch (name) {
      case 'till': go('pos'); break;
      case 'collect': go('receivables'); break;
      case 'cheques': go('cheques'); break;
      case 'restock': go('purchases'); break;
      case 'stock': go('products'); break;
      case 'branches': go('reports'); break;
      case 'drawers':
        if (window.CashDrawer && typeof window.CashDrawer.openHistoryModal === 'function') window.CashDrawer.openHistoryModal();
        else go('pos');
        break;
      case 'tax':
        if (a && a._leaveToPage) a._leaveToPage('/static/einvoicing.html');
        else window.location.href = '/static/einvoicing.html';
        break;
      default: break;
    }
  }

  // ── hero ───────────────────────────────────────────────────────────────────
  const METHOD_LABELS = {
    cash: 'Cash', card: 'Card', cliq: 'CliQ', credit: 'Credit', split: 'Split',
    mobile: 'Mobile', transfer: 'Transfer', voucher: 'Voucher',
  };
  const methodLabel = (m) => {
    const key = METHOD_LABELS[String(m || '').toLowerCase()];
    return esc(key ? tr(key) : String(m || ''));
  };

  function tendersHtml(tenders) {
    const rows = (tenders || []).filter((t) => t && isFiniteNum(t.revenue) && t.revenue > 0);
    if (!rows.length) return '';
    const bar = rows.map((t, i) => '<i class="td-t' + Math.min(i, 4) + '" style="flex:' + t.revenue + '"></i>').join('');
    const leg = rows.map((t, i) => '<div><span class="td-sq td-t' + Math.min(i, 4) + '"></span>' + methodLabel(t.method) + money(t.revenue) +
      '<span class="td-tpct">' + pct(t.share_pct) + '</span></div>').join('');
    return '<div class="td-tbar" aria-hidden="true">' + bar + '</div><div class="td-tleg">' + leg + '</div>';
  }

  function heroNumsHtml(d) {
    const h = d.hero || {};
    const sc = d.scope || {};
    let eyebrow;
    if (sc.scoped && sc.branch_name) eyebrow = S('Today so far · {branch}', { branch: esc(sc.branch_name) });
    else if (sc.branch_count > 1) eyebrow = esc(tr('Today so far · All branches'));
    else eyebrow = esc(tr('Today so far'));
    const delta = isFiniteNum(h.change_pct)
      ? '<span class="td-delta' + (h.change_pct < 0 ? ' is-down' : '') + '">' + ic(h.change_pct < 0 ? 'dn' : 'up') + pct(h.change_pct, true) + '</span>' +
        '<span>' + S('vs this time yesterday ({amount})', { amount: money(h.yesterday_by_now) }) + '</span>'
      : '';
    let gp = '';
    if (isFiniteNum(h.gross_profit)) {
      gp = '<div class="td-gp">' +
        (isFiniteNum(h.margin_pct)
          ? S('Gross profit {amount} · {pct} margin', { amount: '<b>' + money(h.gross_profit) + '</b>', pct: pct(h.margin_pct) })
          : S('Gross profit {amount}', { amount: '<b>' + money(h.gross_profit) + '</b>' })) +
        ' <em>(' + esc(tr(h.cost_known === false ? 'profit is approximate: some items have no cost' : 'before expenses')) + ')</em></div>';
    }
    return '<div class="td-nums">' +
      '<div class="td-eyebrow">' + eyebrow + '</div>' +
      '<div class="td-bigrow"><div class="td-big">' + money(h.revenue) + '</div>' +
      '<div class="td-mini"><div><b>' + num(h.transactions == null ? '—' : h.transactions) + '</b> ' + esc(tr('transactions')) + '</div>' +
      '<div>' + esc(tr('avg ticket')) + ' <b>' + money(h.avg_ticket) + '</b></div></div></div>' +
      (delta ? '<div class="td-sub">' + delta + '</div>' : '') + gp + tendersHtml(h.tenders) + '</div>';
  }

  // ── 14-day chart, drawn to scale from the data; mirrored in RTL ────────────
  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }

  function chartSvg(d) {
    const tr14 = d.trend || {};
    const days = tr14.days || [];
    const n = days.length;
    if (!n) return '';
    const W = 600, H = 176, padT = 22, padB = 24, axis = 44;
    const rtl = isRtl();
    const L = rtl ? 6 : axis;
    const Rr = rtl ? W - axis : W - 6;
    const band = (Rr - L) / n;
    const bw = Math.min(26, band * 0.62);
    const vals = days.map((x) => Math.max(0, Number(x.revenue) || 0));
    const yb = Number(tr14.yesterday_by_now) || 0;
    const avg = Number(tr14.average) || 0;
    const top = Math.max.apply(null, vals.concat([yb, avg, 0]));
    const step = top > 0 ? niceStep(top / 3) : 1;
    const yMax = top > 0 ? step * Math.ceil(top / step) : 1;
    const ph = H - padT - padB;
    const y = (v) => padT + ph * (1 - v / yMax);
    const x = (i) => (rtl ? Rr - (i + 0.5) * band : L + (i + 0.5) * band);
    const fx = (v) => v.toFixed(1);
    let s = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + esc(tr('Net sales per day, last 14 days')) + '">';
    const ticks = top > 0 ? Math.round(yMax / step) : 0;
    for (let k = 0; k <= ticks; k++) {
      const v = k * step;
      s += '<line class="' + (k ? 'td-grid' : 'td-base') + '" x1="' + L + '" x2="' + Rr + '" y1="' + fx(y(v)) + '" y2="' + fx(y(v)) + '"></line>' +
        '<text class="td-ylab" x="' + (rtl ? Rr + 8 : L - 8) + '" y="' + fx(y(v) + 4) + '" text-anchor="' + (rtl ? 'start' : 'end') + '">' + v.toLocaleString('en-US', { maximumFractionDigits: 0 }) + '</text>';
    }
    days.forEach((day, i) => {
      const last = i === n - 1;
      const cx = x(i);
      const yt = y(vals[i]);
      const dom = String(day.day || '').slice(8, 10).replace(/^0/, '');
      s += '<rect class="' + (last ? 'td-bar-now' : 'td-bar') + '" x="' + fx(cx - bw / 2) + '" y="' + fx(yt) + '" width="' + fx(bw) + '" height="' + fx(Math.max(0, y(0) - yt)) + '" rx="3"></rect>' +
        '<text class="td-xlab' + (last ? ' td-xnow' : '') + '" x="' + fx(cx) + '" y="' + (H - 7) + '" text-anchor="middle">' + (last ? esc(tr('Today')) : esc(dom)) + '</text>';
      if (last) s += '<text class="td-sofar" x="' + fx(cx) + '" y="' + fx(yt - 6) + '" text-anchor="middle">' + esc(tr('so far')) + '</text>';
    });
    if (n >= 2 && yb > 0) {
      const yx = x(n - 2);
      s += '<line class="td-yday" x1="' + fx(yx - bw / 2 - 3) + '" x2="' + fx(yx + bw / 2 + 3) + '" y1="' + fx(y(yb)) + '" y2="' + fx(y(yb)) + '"></line>';
    }
    if (avg > 0) s += '<line class="td-avg" x1="' + L + '" x2="' + Rr + '" y1="' + fx(y(avg)) + '" y2="' + fx(y(avg)) + '"></line>';
    return s + '</svg>';
  }

  function chartHtml(d) {
    const svg = chartSvg(d);
    if (!svg) return '';
    const days = (d.trend && d.trend.days) || [];
    const table = '<table class="td-sr"><caption>' + esc(tr('Net sales per day, last 14 days')) + '</caption>' +
      '<thead><tr><th scope="col">' + esc(tr('Date')) + '</th><th scope="col">' + esc(tr('Net sales')) + '</th><th scope="col">' + esc(tr('Transactions')) + '</th></tr></thead><tbody>' +
      days.map((x) => '<tr><td>' + bidi(shortDate(x.day) || x.day) + '</td><td>' + money(Number(x.revenue) || 0) + '</td><td>' + num(isFiniteNum(x.transactions) ? x.transactions : 0) + '</td></tr>').join('') + '</tbody></table>';
    return '<div class="td-chart-wrap"><div class="td-ch-head"><h3 class="td-ch-title">' + esc(tr('Last 14 days')) +
      ' <span>· ' + S('{currency} per day', { currency: esc(tr((R() && R()._currencySymbol) || d.currency || '')) }) + '</span></h3>' +
      '<div class="td-legend"><span><i class="td-lg bar"></i>' + esc(tr('Net sales')) + '</span>' +
      '<span><i class="td-lg now"></i>' + esc(tr('Today so far')) + '</span>' +
      '<span><i class="td-lg yd"></i>' + S('Yesterday by {time}', { time: num(d.as_of || '') }) + '</span>' +
      '<span><i class="td-lg av"></i>' + esc(tr('Daily average')) + '</span></div></div>' +
      '<div class="td-chart">' + svg + '</div>' + table + '</div>';
  }

  function branchesHtml(d) {
    const rows = (d.branches || []);
    if (rows.length < 2) return '';
    return '<div class="td-branches">' + rows.map((b) => {
      const up = isFiniteNum(b.change_pct) && b.change_pct >= 0;
      const delta = isFiniteNum(b.change_pct)
        ? '<span class="td-br-delta' + (up ? ' is-up' : '') + '">' + ic(up ? 'up' : 'dn') + pct(b.change_pct, true) + '</span>'
        : '<span></span>';
      const share = isFiniteNum(b.share_pct) ? Math.max(0, Math.min(100, b.share_pct)) : 0;
      return '<div class="td-br"><span class="td-br-name">' + ic('store') + esc(b.name) + '</span>' + delta +
        '<span class="td-br-amt">' + money(b.revenue) + '</span>' +
        '<span class="td-br-meta">' + plural(b.transactions, '{n} sale', '{n} sales', { n: num(b.transactions) }) +
        (isFiniteNum(b.share_pct) ? ' · ' + S('{pct} of today', { pct: pct(b.share_pct) }) : '') + '</span>' +
        '<span class="td-share" aria-hidden="true"><i style="inline-size:' + share + '%"></i></span></div>';
    }).join('') + '</div>';
  }

  function heroHtml(d) {
    return '<section class="td-card td-hero" aria-label="' + esc(tr('Today so far')) + '"><div class="td-hero-top">' +
      heroNumsHtml(d) + chartHtml(d) + '</div>' + branchesHtml(d) + '</section>';
  }

  // ── cards ──────────────────────────────────────────────────────────────────
  const row = (left, small, right, rightCls) =>
    '<li class="td-row"><span class="td-who">' + left + (small ? '<small>' + small + '</small>' : '') + '</span>' +
    '<span class="td-amt' + (rightCls ? ' ' + rightCls : '') + '">' + right + '</span></li>';
  const noteP = (html) => (html ? '<p class="td-note">' + html + '</p>' : '');
  const moreNote = (c) => (c.more > 0 ? noteP(S('and {count} more', { count: num(c.more) })) : '');
  const scopeLabel = (c) => (c.scope === 'this_computer' ? '<span class="td-scope">' + esc(tr('On this computer only')) + '</span>' : '');

  const CARDS = {
    collect(c) {
      const amount = isFiniteNum(c.amount) ? c.amount : c.stake;
      const rows = (c.rows || []).map((r) => row(
        R()._partyOpenerButton('customer', r.id, r.name, null),
        isFiniteNum(r.days_overdue) ? S('{days} days overdue', { days: num(r.days_overdue) }) : '',
        money(r.amount)));
      return {
        icon: 'coins', tone: 'risk', kind: 'Collect', tag: 'At risk',
        head: plural(c.count, '{count} customer owes {amount} for more than 30 days', '{count} customers owe {amount} for more than 30 days', { count: num(c.count), amount: money(amount) }),
        rows: rows.join(''), notes: moreNote(c),
        primary: ['Remind customers', 'collect'], link: ['All debts', 'collect'],
      };
    },
    cheques(c) {
      const x = c.extra || {};
      const rows = (c.rows || []).map((r) => row(
        esc(r.party_name || ''),
        [r.bank_name ? esc(r.bank_name) : '', r.cheque_number ? '<bdi dir="ltr">#' + esc(r.cheque_number) + '</bdi>' : '', S('due {date}', { date: bidi(shortDate(r.due_date)) })].filter(Boolean).join(' · '),
        money(r.amount)));
      let notes = moreNote(c);
      if (x.next_week_count > 0) notes += noteP(S('Next week: {count} more, {amount}', { count: num(x.next_week_count), amount: money(x.next_week_amount) }));
      if (x.out_due_count > 0) notes += noteP(S('You owe on {count} cheques due this week: {amount}', { count: num(x.out_due_count), amount: money(x.out_due_amount) }));
      return {
        icon: 'bank', tone: 'wait', kind: 'Cheques', tag: 'Money waiting',
        head: plural(c.count, '{count} cheque is due this week or overdue · {amount} to deposit', '{count} cheques are due this week or overdue · {amount} to deposit', { count: num(c.count), amount: money(isFiniteNum(c.amount) ? c.amount : c.stake) }),
        rows: rows.join(''), notes,
        primary: ['Review cheques', 'cheques'], link: ['Cheque book', 'cheques'],
      };
    },
    restock(c) {
      const x = c.extra || {};
      const rows = (c.rows || []).map((r) => row(
        esc(r.name || ''),
        S('{branch} · reorder at {n}', { branch: esc(r.branch_name || ''), n: num(r.reorder_level) }),
        S('{n} left', { n: num(r.on_hand) }), 'is-plain'));
      let notes = moreNote(c);
      if (x.supplier_count > 0) notes += noteP(plural(x.supplier_count, 'From {count} supplier', 'From {count} suppliers', { count: num(x.supplier_count) }));
      if (x.pending_requests > 0) notes += noteP(S('Reorder requests waiting: {count}', { count: num(x.pending_requests) }));
      return {
        icon: 'truck', tone: 'wait', kind: 'Restock', tag: 'Running low',
        head: plural(c.count, '{count} item is below its reorder level · about {amount} to restock', '{count} items are below their reorder level · about {amount} to restock', { count: num(c.count), amount: money(isFiniteNum(c.amount) ? c.amount : c.stake) }),
        rows: rows.join(''), notes,
        primary: hasCap('retail.stock.adjust') ? ['Review reorders', 'restock'] : null,
        link: ['See all {count}', 'stock', { count: c.count }],
      };
    },
    drawers(c) {
      const x = c.extra || {};
      const rows = (c.rows || []).map((r) => {
        const counted = r.variance_status !== 'not_counted' && isFiniteNum(r.variance);
        let right;
        let cls = '';
        if (!counted) { right = esc(tr('Not counted')); cls = 'is-plain'; }
        else if (r.variance < 0) { right = S('Short {amount}', { amount: money(Math.abs(r.variance)) }); cls = 'is-short'; }
        else if (r.variance > 0) right = S('Over {amount}', { amount: money(r.variance) });
        else right = money(0);
        const closed = [shortDate(r.ended_at), clockOf(r.ended_at)].filter(Boolean).join(' ');
        return row(S('Till {id}', { id: num(r.terminal_short || '') }), closed ? S('Closed {time}', { time: bidi(closed) }) : '', right, cls);
      });
      let notes = moreNote(c);
      if (isFiniteNum(c.stake) && c.stake > 0) notes += noteP(S('The differences add up to {amount}.', { amount: money(c.stake) }));
      if (x.not_counted > 0) notes += noteP(S('Closed without a count: {count}', { count: num(x.not_counted) }));
      if (x.open_count > 0) notes += noteP(S('Open now: {count}, with {amount} expected in the drawers', { count: num(x.open_count), amount: money(x.open_expected_cash) }));
      return {
        icon: 'note', tone: 'risk', kind: 'Cash drawers', tag: 'Needs a check',
        head: plural(c.count, '{count} drawer needs a check', '{count} drawers need a check', { count: num(c.count) }),
        rows: rows.join(''), notes,
        primary: ['Review drawers', 'drawers'], link: null,
      };
    },
    tax(c) {
      const reasons = {
        not_connected: 'Sales are not being reported to JoFotara',
        switched_off: 'E-invoicing is switched off',
        paused: 'E-invoice sending is paused',
        backlog: 'E-invoices are waiting to be sent',
        rejected: 'Some e-invoices were rejected by the tax office',
      };
      const labels = { sales_today: 'Sales today', reported_today: 'Reported to JoFotara', waiting: 'Waiting to be sent' };
      const x = c.extra || {};
      const rows = ((c.rows) || x.rows || []).filter((r) => labels[r.key]).map((r) => row(esc(tr(labels[r.key])), '', num(r.n), 'is-plain'));
      const owner = isOwner();
      return {
        icon: 'receipt', tone: 'wait', kind: 'Tax reporting', tag: 'Needs attention',
        headText: reasons[x.reason || c.reason] || reasons.not_connected,
        body: (x.reason || c.reason || 'not_connected') === 'not_connected' ? 'Nothing is sent to the tax office until your account is connected.' : null,
        rows: rows.join(''),
        notes: owner ? '' : noteP(esc(tr('Only the owner can connect this.'))),
        primary: owner ? ['Open e-invoicing', 'tax'] : null, link: null,
      };
    },
    branches(c) {
      const rows = (c.rows || []).map((r) => row(esc(r.name || ''), esc(tr('Yesterday by this time')), money(r.yesterday_by_now)));
      return {
        icon: 'store', tone: 'info', kind: 'Branches', tag: 'Quiet',
        head: plural(c.count, '{count} branch has no sales yet today', '{count} branches have no sales yet today', { count: num(c.count) }),
        rows: rows.join(''), notes: moreNote(c),
        primary: ['Compare branches', 'branches'], link: null,
      };
    },
  };

  function cardHtml(c) {
    const build = CARDS[c.kind];
    if (!build) return '';
    const m = build(c);
    const head = m.headText ? esc(tr(m.headText)) : m.head;
    const btn = m.primary
      ? '<button type="button" class="ret-btn ret-btn-primary" data-today-key="go-' + esc(c.kind) + '" onclick="AuraToday.act(\'' + m.primary[1] + '\')">' + esc(tr(m.primary[0])) + '</button>'
      : '';
    let link = '';
    if (m.link) {
      const label = m.link[2] ? S(m.link[0], { count: num(m.link[2].count) }) : esc(tr(m.link[0]));
      link = '<button type="button" class="td-link" data-today-key="more-' + esc(c.kind) + '" onclick="AuraToday.act(\'' + m.link[1] + '\')">' + label + '</button>';
    }
    return '<article class="td-card td-act td-' + m.tone + '" data-kind="' + esc(c.kind) + '">' +
      '<div class="td-act-h"><span class="td-act-ic">' + ic(m.icon) + '</span><span class="td-kind">' + esc(tr(m.kind)) + '</span>' +
      '<span class="td-tag">' + esc(tr(m.tag)) + '</span></div>' + scopeLabel(c) +
      '<p class="td-head-line">' + head + '</p>' + (m.body ? '<p class="td-body">' + esc(tr(m.body)) + '</p>' : '') +
      (m.rows ? '<ul class="td-rows">' + m.rows + '</ul>' : '') + (m.notes || '') +
      ((btn || link) ? '<div class="td-foot">' + btn + link + '</div>' : '') + '</article>';
  }

  // One calm line per thing that is fine. The sentence says what was MEASURED,
  // so "fine" is a claim with a number behind it, not an empty space.
  function clearLine(r) {
    const x = r.detail || {};
    switch (r.kind) {
      case 'collect':
        return isFiniteNum(x.owed) && x.owed > 0
          ? S('Customers owe {amount}, none of it over 30 days', { amount: money(x.owed) })
          : esc(tr('No customer owes you money'));
      case 'cheques': return esc(tr('No cheques are due this week'));
      case 'restock': return esc(tr('Nothing is below its reorder level'));
      case 'drawers': return esc(tr('No drawer on this computer needs a check'));
      case 'tax': return esc(tr('Sales are being reported to JoFotara'));
      case 'branches':
        return isFiniteNum(x.trading) ? S('All {count} branches have sold today', { count: num(x.trading) }) : esc(tr('Every branch has sold today'));
      default: return '';
    }
  }

  function needsHtml(d) {
    const cards = d.cards || [];
    const clear = (d.all_clear || []).map(clearLine).filter(Boolean);
    let h = '';
    if (cards.length) {
      h += '<div class="td-needs-h"><h2>' + esc(tr('Needs you')) + '</h2><span class="td-count">' + num(isFiniteNum(d.needs_count) ? d.needs_count : cards.length) +
        '</span><p>' + esc(tr('Ordered by money at stake')) + '</p></div>';
    }
    h += '<div class="td-cards">' + cards.map(cardHtml).join('');
    if (clear.length) {
      h += '<article class="td-card td-act td-okc td-clear' + (cards.length ? '' : ' td-clear-wide') + '" data-kind="all_clear">' +
        '<div class="td-act-h"><span class="td-act-ic">' + ic('shield') + '</span><span class="td-kind">' + esc(tr(cards.length ? 'All clear' : 'Nothing needs you right now')) + '</span></div>' +
        '<ul class="td-rows">' + clear.map((line) => '<li class="td-row">' + ic('check') + '<span>' + line + '</span></li>').join('') + '</ul></article>';
    }
    return h + '</div>';
  }

  // ── Aura did today ─────────────────────────────────────────────────────────
  function didLine(r) {
    const n = isFiniteNum(r.count) ? num(r.count) : '';
    switch (r.kind) {
      case 'reorder_suggested': return S('Low items Aura suggested restocking: {count}', { count: n });
      case 'backup_made': return n ? S('Backups made: {count}', { count: n }) : esc(tr('Made a backup of your data'));
      case 'whatsapp_sent': return S('WhatsApp messages sent: {count}', { count: n });
      case 'email_sent': return S('Emails sent: {count}', { count: n });
      case 'einvoices_prepared': return S('E-invoices prepared for the tax office: {count}', { count: n });
      default: return '';
    }
  }

  function didHtml(d) {
    const items = (d.aura_did || []).map((r) => ({ r, line: didLine(r) })).filter((x) => x.line);
    if (!items.length) return '';
    return '<details class="td-card td-did"' + (readDidOpen() ? ' open' : '') + ' ontoggle="AuraToday._didToggle(this)">' +
      '<summary class="td-did-sum" data-today-key="did">' + ic('spark') + '<span>' + esc(tr('What Aura did today')) + '</span>' + ic('down', 'td-chev') + '</summary>' +
      '<ul class="td-did-list">' + items.map((x) => '<li><time dir="ltr">' + esc(x.r.last_at || '') + '</time><span>' + x.line + '</span><span class="td-did-scope">' +
        (x.r.scope === 'this_computer' ? esc(tr('On this computer only')) : '') + '</span></li>').join('') + '</ul></details>';
  }

  // ── header ─────────────────────────────────────────────────────────────────
  function userName() {
    const a = app();
    if (!a) return '';
    const n = typeof a._identityDisplayName === 'function' ? a._identityDisplayName() : '';
    return n && n !== tr('Signed in') ? n : '';
  }

  function headHtml(d) {
    const hour = parseInt(String(d.as_of || '12:00').slice(0, 2), 10);
    const part = hour < 12 ? 'morning' : hour < 18 ? 'afternoon' : 'evening';
    const name = userName();
    const key = { morning: 'Good morning', afternoon: 'Good afternoon', evening: 'Good evening' }[part];
    const greet = name ? S(key + ', {name}', { name: '<bdi dir="auto">' + esc(name) + '</bdi>' }) : esc(tr(key));
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(d.business_date || ''));
    const date = m && R() ? R()._localeDate(new Date(+m[1], +m[2] - 1, +m[3])) : '';
    const sc = d.scope || {};
    const bits = [date ? bidi(date) : ''];
    if (!sc.scoped && sc.branch_count > 1) bits.push(S('{n} branches', { n: num(sc.branch_count) }));
    if (d.as_of) bits.push(S('updated {time}', { time: num(d.as_of) }));
    return '<div><h2 class="td-greet">' + greet + '</h2><p class="td-meta">' + bits.filter(Boolean).join(' · ') + '</p></div>' +
      '<button type="button" class="ret-btn" data-today-key="till" onclick="AuraToday.act(\'till\')">' + ic('cart') + ' ' + esc(tr('Open the till')) + '</button>';
  }

  // The whole body for one answer. Exposed (as _html) so tests and the corpus
  // can look at it without a network.
  function build(d) {
    return { head: headHtml(d), body: heroHtml(d) + needsHtml(d) + didHtml(d) };
  }

  // ── render ─────────────────────────────────────────────────────────────────
  const el = (id) => (typeof document !== 'undefined' ? document.getElementById(id) : null);

  async function render(c, token) {
    const rs = R();
    // A cashier never gets here (the caller sends them to the cashier landing),
    // and this is the second lock on the same door: no request at all.
    if (!hasCap(RET)) {
      if (rs && typeof rs._renderCashierLanding === 'function') return rs._renderCashierLanding(c);
      return undefined;
    }
    injectCss();
    // Remember which control had the keyboard: the 60 s refresh rebuilds the
    // screen, and a person tabbing through it must not lose their place.
    let focusKey = '';
    try {
      const ae = document.activeElement;
      if (ae && typeof ae.getAttribute === 'function') focusKey = ae.getAttribute('data-today-key') || '';
    } catch (e) { focusKey = ''; }

    // The frame is written at once (and the setup guide mounted into it) so the
    // owner sees structure while the figures load; the figures fill it in below.
    c.innerHTML = '<div class="td" data-today="1"><div class="td-head" id="td-head"></div><div id="setup-guide-mount"></div>' +
      '<div id="td-body" aria-live="off"><p class="td-loading">' + esc(tr('Loading...')) + '</p></div></div>';
    if (window.AuraSetupGuide && typeof window.AuraSetupGuide.mount === 'function') {
      const slot = el('setup-guide-mount');
      if (slot) Promise.resolve(window.AuraSetupGuide.mount(slot)).catch(() => {});
    }

    let data = null;
    let failed = false;
    try {
      const resp = await rs._get(ROUTE);
      if (resp && resp.status === 'success' && resp.data) data = resp.data; else failed = true;
    } catch (e) {
      failed = true;
      console.error(e);
    }
    if (token !== undefined && rs._isStaleRender(token)) return undefined;
    const body = el('td-body');
    if (!body) return undefined;
    if (failed) {
      body.innerHTML = '<div class="td-error" role="alert"><span>' + esc(tr('Could not load Today. Check the connection and try again.')) + '</span>' +
        '<button type="button" class="ret-btn" data-today-key="retry" onclick="SubsystemApp._navigate(\'dashboard\')">' + esc(tr('Try again')) + '</button></div>';
      return undefined;
    }
    const parts = build(data);
    const head = el('td-head');
    if (head) head.innerHTML = parts.head;
    body.innerHTML = parts.body;
    if (focusKey) {
      try {
        const again = document.querySelector('[data-today-key="' + focusKey + '"]');
        if (again && typeof again.focus === 'function') again.focus();
      } catch (e) { /* focus is a courtesy */ }
    }
    return undefined;
  }

  window.AuraToday = { render, act, _didToggle: didToggle, _html: build, _state: { route: ROUTE } };
})();
