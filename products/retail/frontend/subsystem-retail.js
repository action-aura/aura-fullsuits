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
    switch (sectionId) {
      case 'dashboard': return this._renderDashboard(c);
      case 'pos':       return this._renderPOS(c);
      case 'products':  return this._renderProducts(c);
      case 'categories':return this._renderCategories(c);
      case 'customers': return this._renderCustomers(c);
      case 'suppliers': return this._renderSuppliers(c);
      case 'purchases': return this._renderPurchases(c);
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
      case 'audit-log':    return this._renderAuditLog(c);
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
      .ret-table { width:100%;border-collapse:collapse;color:#fff;font-size:13px; }
      .ret-table th { color:var(--text-muted);font-weight:500;padding:10px 12px;border-bottom:1px solid rgba(255,255,255,0.08);text-align:left; }
      .ret-table td { padding:11px 12px;border-bottom:1px solid rgba(255,255,255,0.04);vertical-align:middle; }
      .ret-table tr:last-child td { border:none; }
      .ret-table tbody tr:hover { background:rgba(255,255,255,0.03); }
      .ret-badge { display:inline-block;padding:2px 10px;border-radius:10px;font-size:11px;font-weight:600; }
      .ret-badge-green { background:rgba(16,185,129,0.15);color:#10b981; }
      .ret-badge-red   { background:rgba(239,68,68,0.15);color:#ef4444; }
      .ret-badge-yellow{ background:rgba(251,191,36,0.15);color:#fbbf24; }
      .ret-badge-blue  { background:rgba(56,189,248,0.15);color:#38bdf8; }
      .ret-badge-purple{ background:rgba(168,85,247,0.15);color:#a855f7; }
      .ret-modal-overlay { position:fixed;inset:0;background:rgba(0,0,0,0.75);display:flex;align-items:center;justify-content:center;z-index:9999;backdrop-filter:blur(5px); }
      .ret-modal { background:#0f172a;border:1px solid rgba(255,255,255,0.1);border-radius:18px;padding:32px;width:520px;max-height:88vh;overflow-y:auto;box-shadow:0 30px 80px rgba(0,0,0,0.7); }
      .ret-modal-wide { width:680px; }
      .ret-modal h3 { color:#fff;margin:0 0 24px;font-size:20px;font-weight:700; }
      .ret-field { margin-bottom:15px; }
      .ret-field label { display:block;color:var(--text-muted);font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px; }
      .ret-field input,.ret-field select,.ret-field textarea {
        width:100%;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);
        border-radius:8px;color:#fff;padding:10px 14px;font-size:14px;outline:none;
        box-sizing:border-box;font-family:inherit;transition:.2s; }
      .ret-field input:focus,.ret-field select:focus,.ret-field textarea:focus { border-color:var(--sub-accent);box-shadow:0 0 0 3px rgba(244,63,94,0.12); }
      .ret-field select option { background:#0f172a; }
      .ret-field-row { display:grid;grid-template-columns:1fr 1fr;gap:12px; }
      .ret-field-row3 { display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px; }
      .ret-modal-footer { display:flex;gap:10px;justify-content:flex-end;margin-top:22px; }
      .ret-btn { padding:10px 20px;border-radius:8px;font-weight:600;font-size:14px;cursor:pointer;border:none;transition:all .2s; }
      .ret-btn-primary { background:var(--sub-accent);color:#fff; }
      .ret-btn-primary:hover { opacity:.88;transform:translateY(-1px); }
      .ret-btn-primary:disabled { opacity:.5;cursor:not-allowed;transform:none; }
      .ret-btn-ghost { background:rgba(255,255,255,0.05);color:#cbd5e1;border:1px solid rgba(255,255,255,0.1); }
      .ret-btn-ghost:hover { background:rgba(255,255,255,0.1); }
      .ret-btn-danger { background:rgba(239,68,68,0.12);color:#ef4444;border:1px solid rgba(239,68,68,0.25); }
      .ret-btn-danger:hover { background:rgba(239,68,68,0.22); }
      .ret-btn-sm { padding:4px 12px;font-size:12px; }
      .ret-search { background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:8px;color:#fff;padding:9px 14px;font-size:14px;outline:none;width:240px; }
      .ret-search:focus { border-color:var(--sub-accent); }
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
      .ret-kpi-change-up   { color:#10b981;font-size:12px; }
      .ret-kpi-change-down { color:#ef4444;font-size:12px; }
      .ret-po-item { display:grid;grid-template-columns:2fr 1fr 1fr 1fr auto;gap:8px;align-items:center;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05); }
      /* Supplier modal tabs (Details / Contacts) -- no prior tab pattern existed
         in this file, so this is the new baseline; kept visually consistent
         with .ret-btn-ghost's muted/active language rather than inventing a
         new color language. */
      .ret-tabs { display:flex;gap:18px;margin-bottom:18px;border-bottom:1px solid rgba(255,255,255,0.08); }
      .ret-tab { background:none;border:none;color:var(--text-muted);padding:10px 2px;font-size:13px;font-weight:600;cursor:pointer;border-bottom:2px solid transparent; }
      .ret-tab:hover { color:#fff; }
      .ret-tab.active { color:#fff;border-bottom-color:var(--sub-accent); }
      /* PO split-preview cards (Thursday demo, Stream B) -- one per supplier
         group, plus the Unassigned bucket which reuses the same card shape
         with an amber border to flag it needs operator action. */
      .ret-po-split-card { background:rgba(0,0,0,0.2);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:16px;margin-bottom:14px; }
      .ret-po-split-card-hdr { display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;gap:12px; }
      .ret-po-split-supplier { color:#fff;font-weight:700;font-size:15px; }
      .ret-po-split-meta { color:var(--text-muted);font-size:12px;margin-top:2px; }
      .ret-po-split-total { color:#10b981;font-weight:700;font-size:16px;white-space:nowrap; }
      .ret-po-split-warning { background:rgba(251,191,36,0.12);color:#fbbf24;border-radius:8px;padding:8px 12px;font-size:12px;font-weight:600;margin-bottom:10px; }
      .ret-po-split-contact { display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap; }
      .ret-po-split-contact-detail { color:var(--text-muted);font-size:12px; }
      .ret-po-split-unassigned { border-color:rgba(251,191,36,0.35); }
    `;
    document.head.appendChild(s);
  },

  // Negative amounts read as "-$120.00", not "$-120.00" — matters now that a
  // net-negative revenue figure (returns exceeding sales) is displayed as-is
  // rather than hidden/clamped.
  _fmt(n) {
    const v = +(n || 0);
    return (v < 0 ? '-$' : '$') + Math.abs(v).toFixed(2);
  },
  _fmtNum(n) { return (+(n||0)).toLocaleString(); },
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

    c.innerHTML = `
      <style>
        .rdash .ret-title { color:var(--text);font-size:26px;letter-spacing:-0.4px; }
        .rdash-date { color:var(--text-faint);font-size:13px;margin:4px 0 0; }
        /* KPI grid: the hero card gets more track than the four supporting
           cards; below 1300px it spans a full row of its own. */
        .rdash .ret-kpi-grid { grid-template-columns:minmax(230px,1.4fr) repeat(4,minmax(0,1fr)); }
        @media(max-width:1300px){
          .rdash .ret-kpi-grid { grid-template-columns:repeat(2,1fr); }
          .rdash .ret-kpi-hero { grid-column:1/-1; }
        }
        .rdash .ret-kpi {
          background:var(--surface-card);border-color:var(--border-soft);
          transition:transform .22s cubic-bezier(.2,.8,.2,1), box-shadow .22s ease, border-color .22s ease;
          will-change:transform;
          animation:rdashRise .4s cubic-bezier(.22,1,.36,1) both;
        }
        .rdash .ret-kpi:nth-child(2){animation-delay:.05s} .rdash .ret-kpi:nth-child(3){animation-delay:.1s}
        .rdash .ret-kpi:nth-child(4){animation-delay:.15s} .rdash .ret-kpi:nth-child(5){animation-delay:.2s}
        @keyframes rdashRise { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:none} }
        .rdash .ret-kpi::before { opacity:.06; }
        .rdash .ret-kpi:hover {
          transform:translateY(-3px);
          border-color:rgba(var(--sub-accent-rgb),0.35);
          box-shadow:0 12px 28px rgba(0,0,0,0.22);
        }
        .rdash .ret-kpi-value { color:var(--text);font-variant-numeric:tabular-nums; }
        .rdash .ret-kpi-value.is-danger { color:#ef4444; }
        .rdash .ret-kpi-sub { color:var(--text-faint); }
        .rdash .ret-kpi-label { color:var(--text-faint); }
        .rdash .ret-kpi-breakdown { border-top-color:var(--border-soft); }
        .rdash .ret-kpi-breakdown-item { color:var(--text-faint); }
        /* Hero (Revenue Net): the single accent-loud element on the screen. */
        .rdash .ret-kpi-hero {
          border-color:rgba(var(--sub-accent-rgb),0.35);
          background:linear-gradient(135deg, rgba(var(--sub-accent-rgb),0.10), var(--surface-card) 60%);
        }
        .rdash .ret-kpi-hero::before { opacity:.14; }
        .rdash .ret-kpi-hero .ret-kpi-label { color:var(--sub-accent);font-weight:700; }
        .rdash .ret-kpi-hero .ret-kpi-value { font-size:34px;letter-spacing:-1px; }
        /* Day-over-day delta as a designed chip, not raw colored text. */
        .rdash-delta {
          display:inline-flex;align-items:center;gap:4px;padding:2px 9px;margin-top:2px;
          border-radius:20px;font-size:11px;font-weight:700;font-variant-numeric:tabular-nums;
        }
        .rdash-delta.up   { background:rgba(16,185,129,0.14);color:#10b981; }
        .rdash-delta.down { background:rgba(239,68,68,0.14);color:#ef4444; }
        /* Recent-transactions table: theme-correct + a visible row affordance
           (rows are clickable — cursor alone wasn't signalling that). */
        .rdash .ret-table { color:var(--text); }
        .rdash .ret-table th { color:var(--text-faint);border-bottom-color:var(--border-mid); }
        .rdash .ret-table td { border-bottom-color:var(--border-soft); }
        .rdash .ret-table tbody tr { transition:background .15s ease; }
        .rdash .ret-table tbody tr:hover { background:rgba(var(--sub-accent-rgb),0.06); }
        .rdash .ret-btn-ghost {
          background:var(--surface-soft);color:var(--text-dim);border-color:var(--border-mid);
          transition:background .15s ease, color .15s ease, transform .15s ease;
        }
        .rdash .ret-btn-ghost:hover { background:var(--surface-hover);color:var(--text);transform:translateY(-1px); }
        .rdash .ret-btn-ghost:focus-visible { outline:2px solid var(--sub-accent);outline-offset:2px; }
        @media (prefers-reduced-motion: reduce) {
          .rdash .ret-kpi { animation:none;transition:none; }
        }
      </style>
      <div class="rdash">
      <div class="ret-hdr">
        <div>
          <h2 class="ret-title">${t('Retail Overview')}</h2>
          <p class="rdash-date">${new Date().toLocaleDateString('en-US',{weekday:'long',year:'numeric',month:'long',day:'numeric'})}</p>
        </div>
        <div style="display:flex;gap:10px">
          <button class="sub-btn-primary" onclick="SubsystemApp._navigate('pos')">🛒 Open POS</button>
        </div>
      </div>
      <div class="ret-kpi-grid">
        <div class="ret-kpi ret-kpi-hero"><div class="ret-kpi-label">Revenue (Net)</div><div class="ret-kpi-value" id="r-k-rev">—</div><div class="ret-kpi-sub" id="r-k-rev-chg"></div><div class="ret-kpi-breakdown" id="r-k-rev-breakdown"></div></div>
        <div class="ret-kpi"><div class="ret-kpi-label">Transactions</div><div class="ret-kpi-value" id="r-k-txn">—</div><div class="ret-kpi-sub" id="r-k-txn-sub"></div></div>
        <div class="ret-kpi"><div class="ret-kpi-label">Month-to-Date</div><div class="ret-kpi-value" id="r-k-mtd">—</div><div class="ret-kpi-sub" id="r-k-mtd-sub"></div></div>
        <div class="ret-kpi"><div class="ret-kpi-label">Low Stock</div><div class="ret-kpi-value is-danger" id="r-k-low">—</div><div class="ret-kpi-sub">items need reorder</div></div>
        <div class="ret-kpi"><div class="ret-kpi-label">Customers</div><div class="ret-kpi-value" id="r-k-cust">—</div><div class="ret-kpi-sub" id="r-k-prod"></div></div>
      </div>
      <div style="display:grid;grid-template-columns:2fr 1fr;gap:20px;margin-bottom:20px">
        <div class="sub-chart-card">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
            <div class="sub-chart-title">Revenue Today (by Hour)</div>
          </div>
          <div style="height:200px"><canvas id="r-dash-hourly"></canvas></div>
        </div>
        <div class="sub-chart-card">
          <div class="sub-chart-title" style="margin-bottom:14px">Payment Methods</div>
          <div style="height:200px"><canvas id="r-dash-pay"></canvas></div>
        </div>
      </div>
      <div class="sub-chart-card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
          <div class="sub-chart-title">Recent Transactions</div>
          <div style="display:flex;gap:8px">
            <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="SubsystemApp._navigate('sales')">🧾 Sales History</button>
            <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="SubsystemApp._navigate('reports')">Full Report →</button>
          </div>
        </div>
        <div style="overflow-x:auto">
          <table class="ret-table" id="r-dash-recent">
            <thead><tr><th>Receipt #</th><th>Customer</th><th>Items</th><th>Payment</th><th>Total</th><th>Date</th></tr></thead>
            <tbody><tr><td colspan="6" style="text-align:center;color:var(--text-dim);padding:30px">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>
      </div>`;

    try {
      const d = (await this._get('/api/sub/retail/dashboard/stats')).data || {};
      document.getElementById('r-k-rev').textContent   = this._fmt(d.today_sales);
      document.getElementById('r-k-txn').textContent   = d.today_transactions || 0;
      document.getElementById('r-k-mtd').textContent   = this._fmt(d.month_sales);
      document.getElementById('r-k-low').textContent   = d.low_stock_alerts || 0;
      document.getElementById('r-k-cust').textContent  = this._fmtNum(d.total_customers);
      document.getElementById('r-k-prod').textContent  = `${d.total_products||0} active products`;
      // Avg ticket = average sale size, so use GROSS (net + returns) ÷ transactions —
      // returns shouldn't distort the average sale value.
      const grossToday = (d.today_sales || 0) + (d.today_returns || 0);
      document.getElementById('r-k-txn-sub').textContent = `avg ${this._fmt(grossToday / (d.today_transactions || 1))} ticket`;
      document.getElementById('r-k-mtd-sub').textContent = `${d.month_transactions||0} transactions`;

      const chg = d.sales_change_pct || 0;
      const chgEl = document.getElementById('r-k-rev-chg');
      // Same data, presented as a designed chip (.rdash-delta) instead of a
      // raw colored text run — the old ret-kpi-change-* spans still exist in
      // _injectStyles for any other consumer.
      chgEl.innerHTML = chg >= 0
        ? `<span class="rdash-delta up">▲ ${Math.abs(chg)}% vs yesterday</span>`
        : `<span class="rdash-delta down">▼ ${Math.abs(chg)}% vs yesterday</span>`;

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
        breakdownEl.innerHTML = `
          <div class="ret-kpi-breakdown-item">Sales<b style="color:#10b981">${this._fmt(grossSales)}</b></div>
          <div class="ret-kpi-breakdown-item">Returns<b style="color:${retToday > 0 ? '#ef4444' : 'var(--text-muted)'}">${retToday > 0 ? '-' : ''}${this._fmt(retToday)}</b></div>
        `;
      }

      if (window.Chart) {
        // Presentational only: Chart.js can't consume CSS var() strings, so
        // resolve the live token values once per render. The dashboard
        // re-renders every 60 s (auto-refresh in app-shell), so a theme
        // toggle is picked up on the next refresh/navigation.
        const isLight  = document.documentElement.getAttribute('data-theme') === 'light';
        const accentRgb = (getComputedStyle(document.documentElement)
          .getPropertyValue('--sub-accent-rgb') || '244,63,94').trim();
        const tickClr = isLight ? '#51607a' : '#94a3b8';
        const gridClr = isLight ? 'rgba(20,32,60,0.08)' : 'rgba(255,255,255,0.05)';

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
          hCtx.parentElement.innerHTML = '<div style="height:200px;display:flex;align-items:center;justify-content:center;color:var(--text-muted);font-size:14px">No sales today yet</div>';
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
          pCtx.parentElement.innerHTML = '<div style="height:200px;display:flex;align-items:center;justify-content:center;color:var(--text-muted)">No transactions today</div>';
        }
      }

      // Recent transactions table
      const tbody = document.querySelector('#r-dash-recent tbody');
      const recent = d.recent_sales || [];
      if (recent.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-dim);padding:30px">No transactions yet today</td></tr>';
      } else {
        tbody.innerHTML = recent.map(s => `<tr style="cursor:pointer" onclick="RetailSystem._viewSale(${s.id})" title="View invoice">
          <td style="font-family:monospace;color:var(--sub-accent)">${s.sale_number}</td>
          <td>${s.customer_name||'Walk-in'}</td>
          <td style="color:var(--text-dim)">${s.item_count||0} items</td>
          <td>${this._badge(s.payment_method||'cash', s.payment_method==='cash'?'green':'blue')}</td>
          <td style="font-weight:700">${this._fmt(s.total)}</td>
          <!-- AUDIT: the recent-sales query (retail_api.py) has no date filter,
               just ORDER BY created_at DESC LIMIT 8, so on a day with fewer than
               8 sales so far, rows here can be from earlier days. HH:MM-only used
               to make those indistinguishable from today's sales; show the full
               date+time here (matches the Date column convention used by the
               Sales History and Purchase History tables elsewhere in this file). -->
          <td style="color:var(--text-dim);font-family:monospace">${(s.created_at||'').slice(0,16)}</td>
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
          <p class="rdash-date" style="color:var(--text-faint);font-size:13px;margin:4px 0 0">${new Date().toLocaleDateString('en-US',{weekday:'long',year:'numeric',month:'long',day:'numeric'})}</p>
        </div>
      </div>
      <div class="sub-chart-card" style="text-align:center;padding:56px 32px">
        <div style="font-size:40px;margin-bottom:14px">🛒</div>
        <h3 style="color:var(--text);margin:0 0 10px;font-size:18px">${t('Ready to sell')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 24px;line-height:1.7;max-width:420px;margin-left:auto;margin-right:auto">
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
             destination, one sentence to keep translated. -->
        <button class="sub-btn-primary" onclick="SubsystemApp._navigate('pos')">🛒 ${t('Point of Sale')}</button>
      </div>`;
  },

  // ── POS ───────────────────────────────────────────────────────────────────
  // UI/UX polish (feat/retail-ui-ux-polish). Design intents, in order:
  //   1. Selected ≠ hovered — category pills and payment buttons previously
  //      used the SAME visual for :hover and .active, so a cashier scanning
  //      the bar couldn't tell what was selected. Hover is now a tinted
  //      outline; selected is a solid/inset accent fill.
  //   2. The grand total is the most important number in the whole app —
  //      it gets the largest type on the screen (26px tabular numerals).
  //   3. Press feedback everywhere a finger lands (product card, qty, pay,
  //      charge) via transform-only :active states — compositor-friendly,
  //      matches this repo's animation performance rules.
  //   4. All hardcoded dark values (#0f172a, #fff, rgba(255,255,255,…))
  //      replaced with the semantic tokens so light mode renders correctly.
  _renderPOS(c) {
    this._injectStyles();
    c.innerHTML = `
      <style>
        .pos-wrap { display:grid;grid-template-columns:2fr 1fr;gap:20px;height:calc(100vh - 120px); }
        .pos-left { background:var(--surface-card);border:1px solid var(--border-soft);border-radius:16px;display:flex;flex-direction:column;overflow:hidden; }
        .pos-right { background:var(--surface);border:1px solid rgba(var(--sub-accent-rgb),0.4);border-radius:16px;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 0 44px rgba(var(--sub-accent-rgb),0.10); }
        .pos-pane-hdr { padding:14px 20px;border-bottom:1px solid var(--border-soft);display:flex;justify-content:space-between;align-items:center;flex-shrink:0; }
        .pos-pane-title { margin:0;color:var(--text);font-size:16px;font-weight:700; }
        /* Cart header carries a faint accent wash so the "money side" of the
           screen reads as one zone at a glance. */
        .pos-cart-hdr { background:linear-gradient(180deg, rgba(var(--sub-accent-rgb),0.10), transparent); }
        .pos-search { background:var(--input-bg);border:1px solid var(--border-mid);padding:9px 14px;border-radius:9px;color:var(--text);width:280px;outline:none;transition:border-color .18s ease, box-shadow .18s ease; }
        .pos-search::placeholder { color:var(--text-faint); }
        .pos-search:focus { border-color:var(--sub-accent);box-shadow:0 0 0 3px rgba(var(--sub-accent-rgb),0.15); }
        .pos-cat-bar { display:flex;gap:8px;padding:12px 16px;border-bottom:1px solid var(--border-soft);overflow-x:auto;flex-shrink:0; }
        .pos-cat-btn { padding:6px 14px;border-radius:20px;font-size:12px;font-weight:600;cursor:pointer;border:1px solid var(--border-mid);background:transparent;color:var(--text-dim);white-space:nowrap;transition:background .15s ease, color .15s ease, border-color .15s ease, transform .12s ease; }
        .pos-cat-btn:hover { border-color:rgba(var(--sub-accent-rgb),0.5);color:var(--text);background:rgba(var(--sub-accent-rgb),0.08); }
        .pos-cat-btn.active { background:var(--sub-accent);border-color:var(--sub-accent);color:#fff;box-shadow:0 4px 14px rgba(var(--sub-accent-rgb),0.35); }
        .pos-cat-btn:active { transform:scale(0.96); }
        .pos-product-grid { display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:12px;padding:16px;overflow-y:auto;flex:1; }
        .pos-card { background:var(--surface-soft);border:1px solid var(--border-soft);border-radius:12px;padding:14px 10px;text-align:center;cursor:pointer;user-select:none;position:relative;will-change:transform;transition:transform .16s cubic-bezier(.2,.8,.2,1), border-color .16s ease, box-shadow .16s ease; }
        .pos-card:hover { transform:translateY(-3px);border-color:rgba(var(--sub-accent-rgb),0.55);box-shadow:0 10px 22px rgba(0,0,0,0.22); }
        .pos-card:active { transform:translateY(-1px) scale(0.97); }
        .pos-card-outofstock { opacity:.35;cursor:not-allowed; }
        .pos-card-outofstock:hover, .pos-card-outofstock:active { transform:none;border-color:var(--border-soft);box-shadow:none; }
        .pos-card-icon { font-size:28px;margin-bottom:8px; }
        .pos-card-name { color:var(--text);font-size:12px;font-weight:600;margin-bottom:4px;line-height:1.3;height:30px;overflow:hidden; }
        .pos-card-price { color:var(--sub-accent);font-weight:800;font-size:15px;font-variant-numeric:tabular-nums; }
        .pos-card-stock { font-size:10px;color:var(--text-faint);margin-top:3px; }
        .pos-cart-items { flex:1;overflow-y:auto;padding:12px 14px; }
        .pos-cart-row { display:flex;align-items:center;gap:10px;padding:10px 12px;background:var(--surface-soft);border:1px solid var(--border-soft);margin-bottom:6px;border-radius:10px; }
        /* Only the row just added animates (see _renderCart) — re-animating
           the whole list on every qty change would read as flicker. */
        .pos-row-new { animation:posRowIn .28s cubic-bezier(.22,1,.36,1) both; }
        @keyframes posRowIn { from{opacity:0;transform:translateY(-6px) scale(0.98)} to{opacity:1;transform:none} }
        .pos-item-name { color:var(--text);font-size:13px;font-weight:600; }
        .pos-item-meta { color:var(--text-faint);font-size:11px;font-variant-numeric:tabular-nums; }
        .pos-qty-wrap { display:flex;align-items:center;gap:3px;background:var(--input-bg);border:1px solid var(--border-soft);padding:2px;border-radius:8px; }
        .pos-qty-btn { background:transparent;border:none;color:var(--text);width:28px;height:28px;border-radius:6px;cursor:pointer;font-size:14px;line-height:1;transition:background .12s ease, color .12s ease, transform .12s ease; }
        .pos-qty-btn:hover { background:var(--sub-accent);color:#fff; }
        .pos-qty-btn:active { transform:scale(0.88); }
        .pos-qty-val { color:var(--text);font-size:13px;font-weight:700;min-width:22px;text-align:center;font-variant-numeric:tabular-nums; }
        .pos-line-total { color:var(--text);font-weight:700;width:64px;text-align:right;font-variant-numeric:tabular-nums; }
        .pos-remove-btn { background:none;border:none;color:var(--text-faint);cursor:pointer;font-size:14px;width:26px;height:26px;border-radius:6px;transition:background .12s ease, color .12s ease; }
        .pos-remove-btn:hover { color:#ef4444;background:rgba(239,68,68,0.12); }
        .pos-empty { display:flex;flex-direction:column;align-items:center;gap:8px;text-align:center;color:var(--text-faint);padding-top:44px;font-size:13px; }
        .pos-empty-icon { font-size:34px;opacity:.5; }
        .pos-summary { padding:16px;background:var(--surface-soft);border-top:1px solid var(--border-soft); }
        .pos-sum-row { display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;color:var(--text-dim);font-size:13px;font-variant-numeric:tabular-nums; }
        .pos-mini-input { background:var(--input-bg);border:1px solid var(--border-mid);border-radius:6px;color:var(--text);text-align:right;outline:none;transition:border-color .15s ease, box-shadow .15s ease; }
        .pos-mini-input:focus { border-color:var(--sub-accent);box-shadow:0 0 0 2px rgba(var(--sub-accent-rgb),0.15); }
        .pos-grand { display:flex;justify-content:space-between;align-items:baseline;padding-top:12px;border-top:1px dashed var(--border-mid);margin-bottom:12px; }
        .pos-grand-label { font-size:12px;font-weight:700;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px; }
        .pos-grand-value { font-size:26px;font-weight:800;letter-spacing:-0.5px;color:var(--text);font-variant-numeric:tabular-nums; }
        .pos-pay-btns { display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:12px; }
        .pos-pay-btn { padding:9px 4px;border-radius:9px;font-size:12px;font-weight:600;cursor:pointer;border:1px solid var(--border-mid);background:transparent;color:var(--text-dim);transition:background .15s ease, color .15s ease, border-color .15s ease, transform .12s ease; }
        .pos-pay-btn:hover { border-color:rgba(var(--sub-accent-rgb),0.5);color:var(--text);background:rgba(var(--sub-accent-rgb),0.07); }
        .pos-pay-btn.active { background:rgba(var(--sub-accent-rgb),0.15);border-color:var(--sub-accent);color:var(--sub-accent);box-shadow:inset 0 0 0 1px var(--sub-accent); }
        .pos-pay-btn:active { transform:scale(0.96); }
        .pos-clear-btn { background:none;border:none;color:#ef4444;cursor:pointer;font-size:12px;font-weight:600;padding:4px 8px;border-radius:6px;transition:background .12s ease; }
        .pos-clear-btn:hover { background:rgba(239,68,68,0.10); }
        .pos-hold-btn { background:none;border:none;color:#fbbf24;cursor:pointer;font-size:12px;font-weight:600;padding:4px 8px;border-radius:6px;transition:background .12s ease; }
        .pos-hold-btn:hover { background:rgba(251,191,36,0.10); }
        .pos-cust-select { background:var(--input-bg);border:1px solid var(--border-mid);border-radius:7px;color:var(--text-dim);padding:6px 10px;font-size:12px;outline:none;transition:border-color .15s ease; }
        .pos-cust-select:focus { border-color:var(--sub-accent); }
        .pos-checkout-btn { width:100%;padding:15px;background:linear-gradient(135deg,#10b981,#059669);border:none;border-radius:12px;color:#fff;font-weight:800;font-size:17px;letter-spacing:.2px;cursor:pointer;font-variant-numeric:tabular-nums;box-shadow:0 6px 20px rgba(16,185,129,0.30);transition:transform .16s ease, box-shadow .16s ease, opacity .16s ease; }
        .pos-checkout-btn:hover { transform:translateY(-2px);box-shadow:0 12px 28px rgba(16,185,129,0.40); }
        .pos-checkout-btn:active { transform:translateY(0) scale(0.98); }
        .pos-checkout-btn:disabled { opacity:.45;cursor:not-allowed;transform:none;box-shadow:none; }
        .pos-cat-btn:focus-visible,.pos-pay-btn:focus-visible,.pos-qty-btn:focus-visible,
        .pos-remove-btn:focus-visible,.pos-clear-btn:focus-visible,.pos-hold-btn:focus-visible,.pos-checkout-btn:focus-visible,
        .pos-search:focus-visible,.pos-cust-select:focus-visible { outline:2px solid var(--sub-accent);outline-offset:2px; }
        @media (prefers-reduced-motion: reduce) {
          .pos-row-new { animation:none; }
          .pos-card,.pos-checkout-btn,.pos-cat-btn,.pos-pay-btn,.pos-qty-btn { transition:none; }
        }
      </style>
      <div class="pos-wrap">
        <div class="pos-left">
          <div class="pos-pane-hdr">
            <h3 class="pos-pane-title">Products</h3>
            <div style="display:flex;gap:10px;align-items:center">
              <button class="ret-btn ret-btn-ghost ret-btn-sm" id="pos-held-btn" onclick="RetailSystem._openHeldSalesModal()"
                title="Browse and resume sales you've held">📋 Held (<span id="pos-held-count">0</span>)</button>
              <input class="pos-search" id="pos-search" placeholder="Search or scan barcode…" oninput="RetailSystem._filterPOS()" />
            </div>
          </div>
          <div class="pos-cat-bar" id="pos-cats">
            <button class="pos-cat-btn active" onclick="RetailSystem._setCat(null,this)">All</button>
          </div>
          <div class="pos-product-grid" id="pos-product-grid">
            <div style="grid-column:1/-1;text-align:center;padding:50px;color:var(--text-muted)">Loading…</div>
          </div>
        </div>
        <div class="pos-right">
          <div class="pos-pane-hdr pos-cart-hdr">
            <h3 class="pos-pane-title">Current Sale</h3>
            <div style="display:flex;gap:8px;align-items:center">
              <select id="pos-customer" class="pos-cust-select">
                <option value="">Walk-in</option>
              </select>
              <button class="pos-hold-btn" onclick="RetailSystem._holdSale()" title="Park this sale and start a new one">⏸ Hold</button>
              <button class="pos-clear-btn" onclick="RetailSystem._clearCart()">Clear</button>
            </div>
          </div>
          <div class="pos-cart-items" id="pos-cart">
            <div class="pos-empty"><span class="pos-empty-icon">🛒</span><span>Cart is empty — tap a product to add</span></div>
          </div>
          <div class="pos-summary">
            <div class="pos-sum-row"><span>Subtotal</span><span id="pos-sub">$0.00</span></div>
            <div class="pos-sum-row">
              <span>Discount</span>
              <span style="display:flex;gap:6px;align-items:center">
                <input type="number" id="pos-disc" value="0" min="0" max="100" step="0.5"
                  class="pos-mini-input" style="width:55px;padding:3px 7px;font-size:12px"
                  oninput="RetailSystem._recalc()" /> %
              </span>
            </div>
            <div class="pos-sum-row"><span>Tax</span><span id="pos-tax">$0.00</span></div>
            <div class="pos-grand"><span class="pos-grand-label">Total</span><span class="pos-grand-value" id="pos-total">$0.00</span></div>
            <div class="pos-sum-row">
              <span>Cash Tendered</span>
              <input type="number" id="pos-tendered" placeholder="0.00" min="0" step="0.01"
                class="pos-mini-input" style="width:90px;padding:4px 8px;font-size:14px"
                oninput="RetailSystem._calcChange()" />
            </div>
            <div class="pos-sum-row" id="pos-change-row" style="color:#10b981;font-weight:700;display:none">
              <span>Change Due</span><span id="pos-change">$0.00</span>
            </div>
            <div class="pos-pay-btns" id="pos-pay-btns">
              <button class="pos-pay-btn active" data-method="cash"     onclick="RetailSystem._setPayment('cash',this)">💵 Cash</button>
              <button class="pos-pay-btn"         data-method="card"     onclick="RetailSystem._setPayment('card',this)">💳 Card</button>
              <button class="pos-pay-btn"         data-method="mobile"   onclick="RetailSystem._setPayment('mobile',this)">📱 Mobile</button>
              <button class="pos-pay-btn"         data-method="transfer" onclick="RetailSystem._setPayment('transfer',this)">🏦 Transfer</button>
              <button class="pos-pay-btn"         data-method="credit"   onclick="RetailSystem._setPayment('credit',this)">📋 Credit</button>
              <button class="pos-pay-btn"         data-method="voucher"  onclick="RetailSystem._setPayment('voucher',this)">🎟 Voucher</button>
            </div>
            <button class="pos-checkout-btn" id="pos-checkout-btn" onclick="RetailSystem._checkout()">Charge — $0.00</button>
          </div>
        </div>
      </div>`;

    this._cart = [];
    this._paymentMethod = 'cash';
    this._activeCat = null;
    this._loadPOSData();
    // The barcode scanner engine is initialised globally in render(); nothing to do here.

    // feat/shift-cash-drawer: mounts a status bar just below the POS header
    // showing whether a cash session is open for this branch (soft warning
    // if not -- see cash-drawer.js's own module docstring for why this is
    // never a hard checkout gate). Guarded so a build that hasn't loaded
    // cash-drawer.js (e.g. an older cached index.html) still renders the
    // rest of the POS screen exactly as before.
    if (window.CashDrawer) CashDrawer.mount(c);
  },

  async _loadPOSData() {
    try {
      const [prods, cats, custs, taxSettings, held] = await Promise.all([
        this._get('/api/sub/retail/products'),
        this._get('/api/sub/retail/categories'),
        this._get('/api/sub/retail/customers'),
        this._get('/api/sub/retail/settings/tax').catch(() => null),
        this._get('/api/sub/retail/held-sales').catch(() => null),
      ]);
      this._products   = prods.data || [];
      this._categories = cats.data  || [];
      this._customers  = custs.data || [];
      // Company's configured tax-calculation policy (core/retail/pricing.py
      // is the authoritative spec for what these two modes compute).
      this._taxMode = (taxSettings && taxSettings.data && taxSettings.data.tax_calculation_mode) || 'after_discount';
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
      if (grid) grid.innerHTML = '<div style="grid-column:1/-1;color:#ef4444;padding:20px">Failed to load products.</div>';
    }
  },

  _setCat(catId, btn) {
    this._activeCat = catId;
    document.querySelectorAll('.pos-cat-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    this._renderPOSGrid();
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
  _renderPOSGrid(filter = '') {
    const grid = document.getElementById('pos-product-grid');
    if (!grid) return;
    const term = filter.toLowerCase();
    const _ICONS = { Electronics:'💻', Clothing:'👕', 'Food & Beverages':'🍔', Beverages:'🥤',
      Groceries:'🛒', Accessories:'💍', Footwear:'👟', Sports:'⚽', Beauty:'💄', Default:'📦' };
    const visible = this._products.filter(p => {
      if (this._activeCat && p.category_id !== this._activeCat) return false;
      if (term && !p.name.toLowerCase().includes(term) && !p.sku.toLowerCase().includes(term) &&
          !(p.barcode||'').toLowerCase().includes(term)) return false;
      return true;
    });
    if (!visible.length) {
      grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:var(--text-muted);padding:40px">No products found.</div>';
      return;
    }
    grid.innerHTML = visible.map(p => {
      const outOfStock = p.total_stock <= 0;
      const icon = _ICONS[p.category_name] || _ICONS.Default;
      const stockClr = p.total_stock <= (p.reorder_level||0) ? '#ef4444' : 'var(--text-muted)';
      return `<div class="pos-card${outOfStock?' pos-card-outofstock':''}"
          onclick="${outOfStock ? "SubsystemApp.showToast('Out of stock','error')" : `RetailSystem._addToCart('${this._esc(p.id)}')`}">
        <div class="pos-card-icon">${icon}</div>
        <div class="pos-card-name" title="${this._esc(p.name)}">${this._esc(p.name)}</div>
        <div class="pos-card-price">${this._fmt(p.sell_price)}</div>
        <div class="pos-card-stock" style="color:${stockClr}">
          ${outOfStock ? 'Out of stock' : `Stock: ${p.total_stock} ${p.unit||''}`}
        </div>
      </div>`;
    }).join('');
  },

  _filterPOS() {
    this._renderPOSGrid(document.getElementById('pos-search')?.value || '');
  },

  _addToCart(productId) {
    const p = this._products.find(x => x.id === productId);
    if (!p) return;
    const existing = this._cart.find(i => i.product_id === productId);
    const newQty = existing ? existing.quantity + 1 : 1;
    if (newQty > p.total_stock) {
      SubsystemApp.showToast(`Only ${p.total_stock} in stock`, 'error');
      return;
    }
    if (existing) {
      existing.quantity = newQty;
      existing.line_total = newQty * existing.unit_price;
    } else {
      this._cart.push({
        product_id: p.id, name: p.name, quantity: 1,
        unit_price: p.sell_price, tax_rate: p.tax_rate || 0,
        line_total: p.sell_price, max_stock: p.total_stock
      });
    }
    // Presentational: mark which row _renderCart should animate in. Only the
    // touched row moves — re-animating the whole list on every add/increment
    // reads as flicker at real cashier speed.
    this._lastAddedId = productId;
    this._renderCart();
  },

  _updateQty(idx, delta) {
    const item = this._cart[idx];
    const newQty = item.quantity + delta;
    if (newQty > item.max_stock) { SubsystemApp.showToast(`Max stock: ${item.max_stock}`, 'error'); return; }
    if (newQty <= 0) { this._cart.splice(idx, 1); }
    else { item.quantity = newQty; item.line_total = newQty * item.unit_price; }
    this._renderCart();
  },

  _clearCart() {
    this._cart = [];
    this._renderCart();
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
        const when = new Date((r.created_at || '').replace(' ', 'T')).toLocaleString();
        const noteHtml = r.label ? ` — ${this._esc(r.label)}` : '';
        return `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:12px 4px;border-bottom:1px solid rgba(255,255,255,0.06)">
          <div>
            <div style="color:#fff;font-weight:600;font-size:13px">${this._esc(r.hold_number)}${noteHtml}</div>
            <div style="color:var(--text-muted);font-size:11px">${r.item_count} item${r.item_count===1?'':'s'} · ${this._esc(r.customer_name)} · ${when}</div>
          </div>
          <div style="display:flex;align-items:center;gap:10px">
            <span style="color:#fff;font-weight:700">${this._fmt(r.total)}</span>
            <button class="ret-btn ret-btn-primary ret-btn-sm" onclick="RetailSystem._resumeHeldSale(${r.id})">Resume</button>
            <button class="ret-btn ret-btn-danger ret-btn-sm" onclick="RetailSystem._discardHeldSale(${r.id})">Discard</button>
          </div>
        </div>`;
      }).join('');
    } catch (e) {
      box.innerHTML = '<div style="text-align:center;color:#ef4444;padding:20px">Failed to load held sales.</div>';
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

  _renderCart() {
    const container = document.getElementById('pos-cart');
    if (!container) return;
    if (!this._cart.length) {
      container.innerHTML = '<div class="pos-empty"><span class="pos-empty-icon">🛒</span><span>Cart is empty — tap a product to add</span></div>';
      this._recalc();
      return;
    }
    // pos-row-new: entrance animation for the row the cashier just touched
    // (set by _addToCart), cleared immediately after so qty edits and
    // removals don't re-trigger it.
    const newId = this._lastAddedId;
    this._lastAddedId = null;
    container.innerHTML = this._cart.map((item, i) => `
      <div class="pos-cart-row${item.product_id === newId ? ' pos-row-new' : ''}">
        <div style="flex:1">
          <div class="pos-item-name">${this._esc(item.name)}</div>
          <div class="pos-item-meta">${this._fmt(item.unit_price)} × ${item.quantity}</div>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <div class="pos-qty-wrap">
            <button class="pos-qty-btn" onclick="RetailSystem._updateQty(${i},-1)" title="Decrease">−</button>
            <span class="pos-qty-val">${item.quantity}</span>
            <button class="pos-qty-btn" onclick="RetailSystem._updateQty(${i},1)" title="Increase">+</button>
          </div>
          <span class="pos-line-total">${this._fmt(item.line_total)}</span>
          <button class="pos-remove-btn" onclick="RetailSystem._cart.splice(${i},1);RetailSystem._renderCart()" title="Remove">✕</button>
        </div>
      </div>`).join('');
    this._recalc();
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
  _recalc() {
    // AUDIT-fix: the #pos-disc input's min/max=0/100 is decorative HTML
    // only -- a typed value outside that range still reaches parseFloat
    // unclamped, which made discAmt exceed subtotal and total go negative
    // in this preview. Server-side create_sale() independently clamps via
    // tax_engine.clamp_discount_pct(), so the charged amount was never at
    // risk -- this was a client display bug only, fixed at the source read
    // so every downstream use (subtotal/tax/total/change) is consistent.
    const discPct = Math.min(100, Math.max(0, parseFloat(document.getElementById('pos-disc')?.value || 0)));
    const discFrac = discPct / 100;
    const beforeDiscount = this._taxMode === 'before_discount';
    let subtotal = 0, tax = 0;
    this._cart.forEach(i => {
      subtotal += i.line_total;
      const taxableBase = beforeDiscount ? i.line_total : (i.line_total * (1 - discFrac));
      tax += taxableBase * (i.tax_rate / 100);
    });
    const discAmt = subtotal * discFrac;
    const total   = subtotal - discAmt + tax;
    this._currentTotals = { subtotal, discount: discAmt, tax, total };
    if (document.getElementById('pos-sub'))   document.getElementById('pos-sub').textContent   = this._fmt(subtotal);
    if (document.getElementById('pos-tax'))   document.getElementById('pos-tax').textContent   = this._fmt(tax);
    if (document.getElementById('pos-total')) document.getElementById('pos-total').textContent = this._fmt(total);
    const checkoutBtn = document.getElementById('pos-checkout-btn');
    if (checkoutBtn) {
      checkoutBtn.textContent = `Charge — ${this._fmt(total)}`;
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
      if (changeEl)  changeEl.textContent = this._fmt(change);
    } else {
      if (changeRow) changeRow.style.display = 'none';
    }
  },

  _setPayment(method, btn) {
    this._paymentMethod = method;
    document.querySelectorAll('.pos-pay-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const tenderedRow = document.getElementById('pos-tendered')?.parentElement?.parentElement;
    const changeRow = document.getElementById('pos-change-row');
    if (method !== 'cash') {
      if (document.getElementById('pos-tendered')) document.getElementById('pos-tendered').value = '';
      if (changeRow) changeRow.style.display = 'none';
    }
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

  // Lookup helper shared by every scan handler.
  _findByCode(code) {
    const c = (code || '').toLowerCase();
    return (this._products || []).find(x =>
      (x.barcode || '').toLowerCase() === c || (x.sku || '').toLowerCase() === c);
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
  _posScan(code) {
    const search = document.getElementById('pos-search');
    if (search) { search.value = ''; this._filterPOS(); }
    const p = this._findByCode(code);
    if (p) {
      this._addToCart(p.id);              // reuses existing stock checks + cart merge
      SubsystemApp.showToast(`Added: ${p.name}`, 'success');
    } else {
      this._showScanNotFound(code);
    }
  },

  _productsScan(code) {
    const inp = document.getElementById('prod-search');
    if (inp) { inp.value = code; this._filterProducts(); }
    const p = this._findByCode(code);
    SubsystemApp.showToast(p ? `Found: ${p.name}` : `No product matches ${code}`, p ? 'success' : 'error');
  },

  _poScan(code) {
    const p = this._findByCode(code);
    if (!p) { SubsystemApp.showToast(`No product matches ${code}`, 'error'); return; }
    const sel = document.getElementById('po-item-prod');
    if (sel) { sel.value = String(p.id); sel.dispatchEvent(new Event('change')); }
    document.getElementById('po-item-qty')?.focus();
    SubsystemApp.showToast(`Scanned: ${p.name}`, 'success');
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
          <span style="font-family:monospace;color:#fff">${code}</span></p>
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
    this._showProductModal({ barcode: code }, catOpts);
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
    if (btn) { btn.textContent = 'Processing…'; btn.disabled = true; }
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
        // if the receipt modal hiccups — then show the receipt + refresh.
        this._clearCart();
        this._loadPOSData();
        this._showReceipt(data.data);
      } else {
        SubsystemApp.showToast(data.message || 'Checkout failed', 'error');
        if (btn) { btn.textContent = `Charge — ${this._fmt(total)}`; btn.disabled = false; }
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
      if (btn) { btn.textContent = `Charge — ${this._fmt(total)}`; btn.disabled = false; }
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
        <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:16px;text-align:left;margin-bottom:20px">
          ${(saleData.lines||[]).map(i=>`<div style="display:flex;justify-content:space-between;margin-bottom:6px;font-size:13px">
            <span style="color:#94a3b8">${this._esc(i.name || ('#'+i.product_id))} ×${i.quantity}</span>
            <span style="color:#fff">${this._fmt(i.line_total)}</span>
          </div>`).join('')}
          <div style="border-top:1px dashed rgba(255,255,255,0.1);margin:10px 0;padding-top:10px">
            <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px">
              <span style="color:#94a3b8">Total</span><span style="color:#fff;font-weight:700">${this._fmt(saleData.total)}</span>
            </div>
            ${saleData.change > 0 ? `<div style="display:flex;justify-content:space-between;font-size:13px">
              <span style="color:#94a3b8">Change</span><span style="color:#10b981;font-weight:700">${this._fmt(saleData.change)}</span>
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

  async _printReceipt(saleData) {
    const cfg = this._printerCfg();
    const widthMm = cfg.paperWidth === '58mm' ? 58 : 80;
    const lines = (saleData.lines || []).map(i => `
      <div class="rcpt-line">
        <span>${this._esc(i.name || ('#'+i.product_id))} ×${i.quantity}</span>
        <span>${this._fmt(i.line_total)}</span>
      </div>`).join('');
    const einvoiceBlock = await this._einvoiceReceiptBlock(saleData);
    const html = `<!doctype html><html><head><meta charset="utf-8"><title>Receipt ${saleData.sale_number}</title>
      <style>
        @page { size: ${widthMm}mm auto; margin: 2mm; }
        body { font-family: 'Courier New', monospace; width: ${widthMm}mm; margin: 0; font-size: 12px; }
        .rcpt-center { text-align: center; }
        .rcpt-line { display: flex; justify-content: space-between; }
        .rcpt-hr { border-top: 1px dashed #000; margin: 6px 0; }
        .rcpt-bold { font-weight: bold; }
      </style></head><body>
      <div class="rcpt-center rcpt-bold">Aura Retail</div>
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
    tbody.innerHTML = prods.map(p => {
      const lowStock = p.total_stock <= (p.reorder_level||0);
      return `<tr>
        <td style="font-family:monospace;color:var(--sub-accent)">${this._esc(p.sku)}</td>
        <td style="font-weight:600">${this._esc(p.name)}${p.barcode?`<div style="font-size:10px;color:var(--text-muted);font-family:monospace">${this._esc(p.barcode)}</div>`:''}</td>
        <td style="color:var(--text-muted)">${p.category_name?this._esc(p.category_name):'—'}</td>
        <td>${this._fmt(p.cost_price)}</td>
        <td style="font-weight:600;color:#10b981">${this._fmt(p.sell_price)}</td>
        <td style="font-weight:700;color:${lowStock?'#ef4444':'#10b981'}">${lowStock?'⚠ ':''}${p.total_stock} ${p.unit||''}</td>
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

  _openAddProduct() {
    const catOpts = this._categories.map(c => `<option value="${this._esc(c.id)}">${this._esc(c.name)}</option>`).join('');
    const supOpts = (this._suppliers||[]).map(s => `<option value="${this._esc(s.id)}">${this._esc(s.name)}</option>`).join('');
    this._showProductModal({}, catOpts, supOpts);
  },

  _openEditProduct(pid) {
    const p = this._products.find(x => String(x.id) === String(pid));
    if (!p) return;
    const catOpts = this._categories.map(c =>
      `<option value="${this._esc(c.id)}" ${c.id===p.category_id?'selected':''}>${this._esc(c.name)}</option>`).join('');
    const supOpts = (this._suppliers||[]).map(s =>
      `<option value="${this._esc(s.id)}" ${s.id===p.supplier_id?'selected':''}>${this._esc(s.name)}</option>`).join('');
    this._showProductModal(p, catOpts, supOpts);
  },

  _showProductModal(p, catOpts, supOpts) {
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
        <p style="color:var(--text-muted);margin:0 0 20px">Current stock: <strong style="color:#fff">${currentStock}</strong></p>
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
        <td style="font-weight:600">${this._esc(cu.name)}</td>
        <td style="color:var(--text-muted)">${cu.phone?this._esc(cu.phone):'—'}</td>
        <td style="color:var(--text-muted)">${cu.email?this._esc(cu.email):'—'}</td>
        <td><span style="color:#fbbf24;font-weight:700">${this._esc(cu.loyalty_points||0)} pts</span></td>
        <td style="font-weight:600;color:#10b981">${this._fmt(cu.total_spent)}</td>
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
            <div style="color:#10b981;font-size:22px;font-weight:700">${this._fmt(cu.total_spent)}</div>
          </div>
          <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;text-align:center">
            <div style="color:var(--text-muted);font-size:11px;text-transform:uppercase;margin-bottom:6px">Orders</div>
            <div style="color:#fff;font-size:22px;font-weight:700">${cu.order_count||0}</div>
          </div>
          <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;text-align:center">
            <div style="color:var(--text-muted);font-size:11px;text-transform:uppercase;margin-bottom:6px">Loyalty Points</div>
            <div style="color:#fbbf24;font-size:22px;font-weight:700">${cu.loyalty_points||0}</div>
          </div>
          <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;text-align:center">
            <div style="color:var(--text-muted);font-size:11px;text-transform:uppercase;margin-bottom:6px">Phone</div>
            <div style="color:#fff;font-size:16px;font-weight:600">${cu.phone?this._esc(cu.phone):'—'}</div>
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
        <tbody>${hist.map(s=>`<tr style="cursor:pointer" onclick="RetailSystem._viewSale(${s.id})" title="View invoice">
          <td style="font-family:monospace;color:var(--sub-accent)">${s.sale_number}</td>
          <td>${s.items||0}</td>
          <td>${this._badge(s.payment_method,'blue')}</td>
          <td style="font-weight:700">${this._fmt(s.total)}</td>
          <td style="color:var(--text-muted)">${(s.created_at||'').slice(0,16)}</td>
        </tr>`).join('')}</tbody>
      </table>`;
    } catch(e) {}
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
      <div style="margin-top:14px;padding-top:14px;border-top:1px solid rgba(255,255,255,0.08)">
        <div style="color:#fff;font-weight:600;margin-bottom:10px">${isEdit ? t('Edit Contact') : t('Add Contact')}</div>
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
          <label style="margin:0;text-transform:none;font-size:13px;color:#fff" for="scf-primary">${t('Primary contact for this role')}</label>
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
        <td style="font-family:monospace;color:var(--sub-accent)">${po.po_number}</td>
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
          <div style="margin:0 0 8px;color:#fff;font-weight:600">Order Items</div>
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
          <div style="text-align:right;color:#fff;font-size:16px;font-weight:700;margin-bottom:16px">
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
          <td><input type="number" value="${item.quantity}" min="1" style="width:60px;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:5px;color:#fff;padding:4px 8px;text-align:center;outline:none"
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
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">Supplier</div><div style="color:#fff;font-weight:600">${po.supplier_name||'—'}</div></div>
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">Status</div><div>${this._badge(po.status,'green')}</div></div>
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">Total</div><div style="color:#10b981;font-weight:700">${this._fmt(po.total)}</div></div>
          </div>
          <table class="ret-table">
            <thead><tr><th>Product</th><th>SKU</th><th>Qty Ordered</th><th>Qty Received</th><th>Unit Cost</th><th>Total</th></tr></thead>
            <tbody>${items.map(i=>`<tr>
              <td>${i.product_name||'—'}</td>
              <td style="font-family:monospace;color:var(--text-muted)">${i.sku||'—'}</td>
              <td>${i.quantity}</td>
              <td style="color:${i.received_qty>=i.quantity?'#10b981':'#fbbf24'}">${i.received_qty||0}</td>
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
      <div class="sub-chart-card">
        <h3 style="color:#fff;margin:0 0 14px;font-size:15px">${t('Low-Stock Reorder Requests')}</h3>
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
        <h3 style="color:#fff;margin:0 0 14px;font-size:15px">${t('WhatsApp Reports')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 16px">
          ${t('Send daily sales, shift-close, low-stock, and overdue-balance reports to multiple phone numbers by role or branch. Off by default.')}
        </p>
        <button class="ret-btn ret-btn-primary" onclick="location.href='/static/whatsapp.html'">${t('Manage WhatsApp Reports')}</button>
      </div>`;
    await this._loadReorderRequests();
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
        <td style="color:var(--text-muted)">${r.created_at ? new Date(r.created_at).toLocaleString() : '—'}</td>
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
        if (tbody) tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:#ef4444;padding:30px">${this._esc(res.message || t('Could not load the audit log.'))}</td></tr>`;
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
          <td style="color:var(--text-muted);white-space:nowrap">${this._auditTimestamp(r.timestamp)}</td>
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
      if (tbody) tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:#ef4444;padding:30px">${t('Could not load the audit log.')}</td></tr>`;
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
  _auditTimestamp(ts) {
    if (!ts) return '—';
    const d = new Date(String(ts).replace(' ', 'T') + 'Z');
    return isNaN(d.getTime()) ? this._esc(ts) : d.toLocaleString();
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
      tbody.innerHTML = data.map(r => `<tr>
        <td style="font-family:monospace;color:var(--sub-accent)">${r.return_number}</td>
        <td style="color:var(--text-muted)">${r.sale_number||'—'}</td>
        <td>${r.customer_name||'Walk-in'}</td>
        <td>${this._badge(r.refund_method||'cash','blue')}</td>
        <td style="font-weight:600;color:#ef4444">${this._fmt(r.refund_amount)}</td>
        <td style="color:var(--text-muted)">${(r.created_at||'').slice(0,16)}</td>
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
              <input id="ret-sale-search" placeholder="e.g. S-1234567890" style="flex:1;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:8px;color:#fff;padding:10px 14px;font-size:14px;outline:none" />
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
        <div style="color:#fff;font-weight:600;margin-bottom:10px">Items from ${saleNum}</div>
        <table class="ret-table">
          <thead><tr><th><input type="checkbox" id="ret-check-all" onchange="document.querySelectorAll('.ret-item-cb').forEach(cb=>cb.checked=this.checked)" /></th><th>Product</th><th>Sold Qty</th><th>Return Qty</th><th>Unit Price</th></tr></thead>
          <tbody>${items.map((item,i)=>`<tr>
            <td><input type="checkbox" class="ret-item-cb" data-idx="${i}" data-pid="${item.product_id}" data-price="${item.unit_price}" data-max="${item.quantity}" /></td>
            <td>${item.product_name}</td>
            <td>${item.quantity}</td>
            <td><input type="number" class="ret-item-qty" data-idx="${i}" value="${item.quantity}" min="1" max="${item.quantity}" style="width:60px;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:5px;color:#fff;padding:4px 8px;text-align:center;outline:none" /></td>
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
    const dateInputStyle = 'background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:8px;color:#fff;padding:9px 12px;outline:none';
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">🧾 ${t('Sales History')}</h2>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <input class="ret-search" id="sh-search" placeholder="${t('Search receipt # or customer…')}" oninput="RetailSystem._debounceSalesSearch()" />
          ${mayBrowse ? `
          <input type="date" id="sh-date-from" title="${t('From date')}"
            style="${dateInputStyle}" onchange="RetailSystem._loadSalesHistory()" />
          <input type="date" id="sh-date-to" title="${t('To date')}"
            style="${dateInputStyle}" onchange="RetailSystem._loadSalesHistory()" />` : ''}
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
    const color = isError ? '#ef4444' : 'var(--text-muted)';
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
      tbody.innerHTML = data.map(s => `<tr style="cursor:pointer" onclick="RetailSystem._viewSale(${s.id})" title="${t('View invoice')}">
        <td style="font-family:monospace;color:var(--sub-accent)">${s.sale_number}</td>
        <td style="color:var(--text-muted)">${(s.created_at||'').slice(0,16)}</td>
        <td>${s.customer_name||t('Walk-in')}</td>
        <td style="color:var(--text-muted)">${s.item_count||0}</td>
        <td>${this._badge(s.payment_method||'cash', s.payment_method==='cash'?'green':'blue')}</td>
        <td style="font-weight:700">${this._fmt(s.total)}</td>
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
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">${t('Customer')}</div><div style="color:#fff;font-weight:600">${this._esc(sale.customer_name||'Walk-in')}</div></div>
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">${t('Cashier')}</div><div style="color:#fff;font-weight:600">${this._attributionCell(this._saleIdentityRow(sale), sale.cashier)}</div></div>
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">${t('Till')}</div><div style="color:#fff;font-weight:600">${this._attribution(sale.terminal_id)}</div></div>
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">${t('Payment')}</div><div>${this._badge(sale.payment_method||'cash','blue')}</div></div>
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">${t('Status')}</div><div>${this._badge(sale.status||'completed', statusColor[sale.status]||'green')}</div></div>
            <div><div style="color:var(--text-muted);font-size:11px;text-transform:uppercase">${t('Total')}</div><div style="color:#10b981;font-weight:700">${this._fmt(sale.total)}</div></div>
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
              <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px"><span style="color:var(--text-muted)">Subtotal</span><span style="color:#fff">${this._fmt(sale.subtotal)}</span></div>
              ${sale.discount_amount>0?`<div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px"><span style="color:var(--text-muted)">Discount</span><span style="color:#ef4444">-${this._fmt(sale.discount_amount)}</span></div>`:''}
              ${sale.tax_amount>0?`<div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px"><span style="color:var(--text-muted)">Tax</span><span style="color:#fff">${this._fmt(sale.tax_amount)}</span></div>`:''}
              <div style="display:flex;justify-content:space-between;font-size:15px;font-weight:700;border-top:1px dashed rgba(255,255,255,0.1);padding-top:8px;margin-top:4px"><span style="color:#fff">Total</span><span style="color:#fff">${this._fmt(sale.total)}</span></div>
              <div style="display:flex;justify-content:space-between;font-size:13px;margin-top:6px"><span style="color:var(--text-muted)">Paid</span><span style="color:#fff">${this._fmt(sale.amount_paid)}</span></div>
              ${sale.change_amount>0?`<div style="display:flex;justify-content:space-between;font-size:13px"><span style="color:var(--text-muted)">Change</span><span style="color:#10b981">${this._fmt(sale.change_amount)}</span></div>`:''}
              ${sale.due_date?`<div style="display:flex;justify-content:space-between;font-size:13px"><span style="color:var(--text-muted)">Due date</span><span style="color:#fbbf24">${sale.due_date}</span></div>`:''}
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
          <select id="rep-branch" onchange="RetailSystem._loadReports()"
            style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:8px;color:#fff;padding:8px 12px;outline:none">
            <option value="">All branches</option>
          </select>
          <select id="rep-days" onchange="RetailSystem._loadReports()"
            style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:8px;color:#fff;padding:8px 12px;outline:none">
            <option value="7">Last 7 days</option>
            <option value="14" selected>Last 14 days</option>
            <option value="30">Last 30 days</option>
            <option value="90">Last 90 days</option>
          </select>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:16px;margin-bottom:8px" id="rep-kpis">
        <div class="ret-kpi"><div class="ret-kpi-label">Revenue</div><div class="ret-kpi-value" id="rep-rev">—</div></div>
        <div class="ret-kpi"><div class="ret-kpi-label">Transactions</div><div class="ret-kpi-value" id="rep-txn">—</div></div>
        <div class="ret-kpi"><div class="ret-kpi-label">Gross Profit</div><div class="ret-kpi-value" id="rep-profit" style="color:#10b981">—</div></div>
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

      ${!desktop ? `<div class="sub-chart-card" style="margin-bottom:18px;border-color:#fbbf24">
        <div style="color:#fbbf24">⚠️ Hardware barcode scanning is available on the Windows desktop app only. Manual barcode entry still works here.</div>
      </div>` : ''}

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start">
        <!-- Status + Test -->
        <div class="sub-chart-card">
          <div class="sub-chart-title" style="margin-bottom:14px">Scanner Status</div>
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
            <span id="sc-status-dot" style="width:12px;height:12px;border-radius:50%;background:#10b981;display:inline-block"></span>
            <span id="sc-status-text" style="color:#fff;font-size:16px;font-weight:600">Ready</span>
          </div>
          <div id="sc-last-scan" style="color:var(--text-muted);font-size:13px;margin-bottom:18px">No scans yet this session</div>

          <div class="sub-chart-title" style="margin:0 0 10px">Test Scanner</div>
          <p style="color:var(--text-muted);font-size:12px;margin:0 0 10px">Click below, then scan any barcode — the captured value appears instantly.</p>
          <div style="display:flex;gap:8px;align-items:center">
            <input id="sc-test-value" readonly placeholder="Captured value will appear here…"
              style="flex:1;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:8px;color:#fff;padding:10px 14px;font-size:14px;font-family:monospace;outline:none" />
            <button class="ret-btn ret-btn-primary" id="sc-test-btn" onclick="RetailSystem._armTestScan()">Start Test</button>
          </div>

          <div style="margin-top:18px;background:rgba(56,189,248,0.08);border:1px solid rgba(56,189,248,0.2);border-radius:10px;padding:12px 14px;color:#94a3b8;font-size:12px;line-height:1.5">
            ℹ️ <strong style="color:#cbd5e1">Note:</strong> USB &amp; Bluetooth HID scanners behave exactly like a keyboard,
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
      if (dot) dot.style.background = '#64748b';
      if (last) last.textContent = 'Scanner listening is turned off';
      return;
    }
    if (st.lastScanAt) {
      const secs = Math.round((Date.now() - st.lastScanAt) / 1000);
      const ago  = secs < 60 ? `${secs}s ago` : `${Math.round(secs / 60)}m ago`;
      // "Ready" within the last 10s of a scan, otherwise idle-but-waiting.
      text.textContent = secs < 10 ? 'Ready' : 'Waiting for Scanner';
      if (dot) dot.style.background = secs < 10 ? '#10b981' : '#fbbf24';
      if (last) last.innerHTML = `Last Scan: <span style="color:#fff">${ago}</span> · <span style="font-family:monospace">${st.lastScanCode || ''}</span>`;
    } else {
      text.textContent = 'Waiting for Scanner';
      if (dot) dot.style.background = '#fbbf24';
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
