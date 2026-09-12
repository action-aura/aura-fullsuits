/**
 * retail_surface_i18n_test.js — every user-visible string on the reworked
 * till and dashboard surfaces must exist in BOTH locale catalogs.
 *
 * WHY A RENDER-TIME CHECK, WHEN retail_localization_test.py ALREADY EXISTS
 *
 * That suite proves the two catalogs agree with EACH OTHER: same key set, no
 * empty Arabic, no Arabic value byte-identical to its English. All necessary,
 * none of it sufficient — because it can only see the catalogs. It cannot see
 * a string that was never put in a catalog at all, and that is the failure
 * this product keeps shipping:
 *
 *   * The cashier landing's primary button read "🛒 Open POS" as a bare
 *     literal from the day it shipped. Never passed through t(); 'Open POS'
 *     was in neither catalog; and because the emoji shared the text node,
 *     i18n.js's DOM sweep (which rescues an untagged node whose FULL trimmed
 *     text is a catalog key) could not have matched even if the key existed.
 *     An Arabic cashier's first screen after login was three Arabic sentences
 *     under an English button. It was declared fixed twice.
 *
 *   * The dashboard built "312 active products", "avg $68.58 ticket" and
 *     "210 transactions" as interpolated sentences. Number and words in one
 *     text node, so the sweep could never match those either. Permanently
 *     English, silently, on every Arabic install.
 *
 *   * Both screens printed their date through toLocaleDateString('en-US'),
 *     hardcoded, so an Arabic page rendered "Saturday, August 22, 2026" under
 *     an Arabic heading.
 *
 * None of those is visible to a catalog-vs-catalog test, and none is obvious
 * reading the source. All three are obvious the moment you render the screen
 * and ask of each text node: could this ever be translated? That is what this
 * file does.
 *
 * It also covers the OTHER half of shipping Arabic, which is not translation at
 * all: a run of digits with no strong directional character in it takes its
 * direction from the surrounding paragraph, so an unisolated "2026-08-21 18:42"
 * renders as "18:42 2026-08-21" on an Arabic page — a wrong date, not a mirrored
 * one. See testNeutralNumberRunsAreDirectionIsolated below.
 *
 * WHAT CHANGED, AND WHY IT MATTERED MORE THAN ANY OF THE ABOVE
 *
 * This file used to render five surfaces: the POS shell, the cart, the product
 * grid, the cashier landing and the dashboard. Every sweep in it is a loop over
 * those fragments, so every claim it made was, in practice, a claim about one
 * site. An adversarial verifier deleted `this._bdi(...)` from FIVE call sites —
 * the Sales History date, the Returns date and return number, the customer
 * Purchase-History date, the PO number — and all six design/surface suites
 * stayed green, because the only date column in the corpus was the dashboard's
 * and the anti-vacuity floor was `hazards.length >= 1`, which that one cell
 * satisfied on its own.
 *
 * It now renders thirty: the list screens, their rows, and the three modals.
 * The bidi floor is derived from the product — the number of places
 * subsystem-retail.js renders a truncated `created_at` — so deleting a <bdi>
 * cannot lower the bar it is measured against, and dropping a screen from the
 * corpus fails by name. See expectedTimestampCells().
 *
 * The catalog-coverage claim is scoped, deliberately and out loud: see
 * LOCALIZED_SURFACES. Screens this programme never localized are rendered,
 * swept with the same rule, and their offenders counted and named rather than
 * asserted away or passed over in silence.
 *
 * Mutation-proven: reintroducing a bare literal, merging a number back into a
 * translatable sentence, re-hardcoding the date locale, dropping the <bdi> from
 * ANY of the five date cells, or dropping a screen from the corpus all fail here.
 *
 * Run: node products/retail/tests/retail_surface_i18n_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const dom = require('./retail_surface_domlite.js');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const FRONTEND_FILE = path.join(FRONTEND_DIR, 'subsystem-retail.js');
const EN = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, 'locales', 'en.json'), 'utf8'));
const AR = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, 'locales', 'ar.json'), 'utf8'));

// Characters that carry no language: digits, currency, punctuation, arithmetic
// and UI glyphs. Built from escapes rather than literal glyphs so an invisible
// character (a non-breaking space, say) cannot hide inside the class.
//
// Note what is NOT in here, and why:
//   * PLAIN SPACE. A separator must contain at least one of the characters
//     below; whitespace alone never splits. Catalog keys are whole phrases
//     ("Scan or tap a product to begin"), so splitting on spaces would demand
//     a dictionary entry per word and report every real key as missing.
//   * PLAIN HYPHEN, so "Walk-in" stays one token rather than becoming
//     "Walk" + "in".
//   * `#` IS in here. It is the number sign, and this product uses it exactly
//     that way -- `'#' + entity_id` in the audit log, `'#'+i.product_id` in the
//     sale detail. Leaving it out made "sale #1" segment as "sale #", which is
//     not the database value "sale" and not copy either, so a real data column
//     reported as an untranslated literal that no catalog could ever fix.
const NON_LINGUISTIC_CHARS =
  '\\u00a0\\d.,:;%$()\\[\\]{}+|/#\\\\\'"' +
  '\\u2013\\u2014\\u2212\\u2011' +          // en/em dash, minus sign, non-breaking hyphen
  '\\u00b7\\u2022\\u00d7\\u2715' +          // middot, bullet, multiplication sign, cross
  '\\u2317\\u25b2\\u25bc\\u2192\\u2190';    // viewfinder, up/down triangle, arrows

// A separator is a run holding at least one of the above, with any surrounding
// or interior whitespace swallowed: " — $12.34" is one separator, "is" is not.
const NON_LINGUISTIC_RUN = new RegExp(
  '\\s*[' + NON_LINGUISTIC_CHARS + ']+(?:\\s*[' + NON_LINGUISTIC_CHARS + ']+)*\\s*'
);
const EMOJI_ONLY = /^[\p{Extended_Pictographic}️\s]+$/u;
const HAS_LETTER = /\p{L}/u;

// Sentinels the marking t() stub wraps every translated value in. C0 controls,
// so _esc() leaves them alone and no real copy can contain them.
const MARK_OPEN  = String.fromCharCode(1);   // written as an escape, never as a raw control char in source
const MARK_CLOSE = String.fromCharCode(2);
const MARKED_REGION = new RegExp(String.fromCharCode(1) + String.fromCharCode(40, 91, 94) + String.fromCharCode(2) + String.fromCharCode(93, 42, 41) + String.fromCharCode(2), String.fromCharCode(103));

/**
 * Undo RetailSystem._esc(), which runs AFTER t() on every value destined for an
 * attribute or an innerHTML sink.
 *
 * Without this the test compares an escaped string against the catalog and
 * reports a false failure: the key "Browse and resume sales you've held" is
 * rendered as "...you&#39;ve held", which is not a key, even though the code is
 * completely correct. A browser decodes these before the operator ever sees
 * them, so decoding here is what makes the comparison faithful rather than
 * pedantic. &amp; is decoded last so "&amp;lt;" cannot collapse twice.
 */
function decodeEntities(text) {
  return String(text)
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}

/** The text with every t()-translated span removed — i.e. the raw literals. */
function outsideTranslation(text) {
  return text.replace(MARKED_REGION, ' ');
}

/** The text as the operator sees it, sentinels stripped. */
function asRendered(text) {
  return text.split(MARK_OPEN).join('').split(MARK_CLOSE).join('');
}

/** Every catalog key that t() was actually called with in this text. */
function translatedKeys(text) {
  return [...text.matchAll(MARKED_REGION)].map((m) => m[1]);
}

// Attributes that put words in front of the operator. i18n.js's DOM sweep only
// ever touches TEXT NODES, so an attribute has no safety net at all: if it did
// not pass through t() at render time it is English forever. The scan field's
// own placeholder is the most visible example on the whole till.
const VISIBLE_ATTRIBUTES = ['placeholder', 'title', 'aria-label', 'alt'];

function isTranslatable(text) {
  const s = text.replace(/\s+/g, ' ').trim();
  if (!s) return false;
  if (!HAS_LETTER.test(s)) return false;
  if (EMOJI_ONLY.test(s)) return false;
  return true;
}

/**
 * Split a rendered text node into the LANGUAGE-BEARING segments a catalog
 * would actually have to cover.
 *
 * A node is very often a composition, and composition is legitimate: the
 * Charge button renders t('Charge') + " — " + "$12.34", so the node reads
 * "Charge — $12.34". The translatable half already went through t(); only the
 * amount is glued on, and an amount needs no catalog entry. Demanding that the
 * WHOLE node be a key would flag that as broken and push someone toward
 * "fixing" code that is already correct.
 *
 * What must NOT be tolerated is the opposite shape — "312 active products",
 * where the WORDS never reached t() and are unreachable by i18n.js's node
 * sweep. Segmenting catches exactly that: "active products" falls out as a
 * segment, and it is not a key.
 *
 * Single characters are dropped: they are separators and ISO-timestamp
 * artefacts (the "T" in 2026-08-21T18:42), never copy.
 */
function languageSegments(text) {
  return text
    .split(NON_LINGUISTIC_RUN)
    // Trim whitespace and any dangling hyphen left at a segment edge (the "S-"
    // of a receipt number "S-1041" once its digits are consumed).
    .map((s) => s.replace(/^[\s-]+|[\s-]+$/g, ''))
    .filter((s) => s.length > 1 && HAS_LETTER.test(s) && !EMOJI_ONLY.test(s));
}

function makeElementStub(overrides) {
  return Object.assign({
    innerHTML: '', outerHTML: '', textContent: '', value: '', id: '', disabled: false,
    style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {}, remove() {},
    querySelectorAll() { return []; }, addEventListener() {}, focus() {}, closest() { return null; },
    getContext() { return {}; },
  }, overrides);
}

// `created_at` is deliberately the SPACE form, not an ISO 'T'.
//
// This is what the server actually writes: retail_api.py's create_sale stores
// `datetime.now().strftime('%Y-%m-%d %H:%M:%S')`, so what the Date column
// slices to 16 characters is "2026-08-21 18:42" — two number runs, a space
// between them, and NOT ONE STRONG DIRECTIONAL CHARACTER in the string.
//
// The fixture used to say '2026-08-21T18:42:00'. That 'T' is a strong LTR
// character, and it silently anchored the whole run left-to-right — so the
// bidi hazard this file now checks for could not occur in the test data even
// while it was occurring on every Arabic install. A fixture that manufactures
// the state which hides the bug is worse than no fixture, because it reports
// the bug as absent.
const STATS = {
  today_sales: 1284.5, today_transactions: 18, month_sales: 21450.75,
  month_transactions: 310, low_stock_alerts: 7, total_customers: 84,
  total_products: 312, today_returns: 65.44, sales_change_pct: 12,
  hourly_labels: [], hourly_data: [], payment_methods: {},
  recent_sales: [
    { id: 1, sale_number: 'S-1041', customer_name: 'Walk-in', item_count: 3,
      payment_method: 'cash', total: 42.5, created_at: '2026-08-21 18:42:00' },
  ],
};

const PRODUCTS = [
  { id: 'p1', name: 'Espresso Beans 1kg', sku: 'EB1', barcode: '111', sell_price: 18.5,
    tax_rate: 16, total_stock: 40, reorder_level: 5, unit: 'bag', category_name: 'Beverages' },
  { id: 'p2', name: 'Paper Cups', sku: 'PC', barcode: '222', sell_price: 4.25,
    tax_rate: 16, total_stock: 0, reorder_level: 10, unit: 'pack', category_name: 'Groceries' },
];

/* ── The list screens' server data ─────────────────────────────────────────
   Same `created_at` discipline as STATS above, for the same reason, on every
   record type that renders a date: the SPACE form, never an ISO 'T'. */
const SALES_ROWS = [
  { id: 1, sale_number: 'S-1041', customer_name: 'Walk-in', item_count: 3, items: 3,
    payment_method: 'cash', total: 42.5, status: 'completed', created_at: '2026-08-21 18:42:00' },
];
const SALE_DETAIL = {
  sale: Object.assign({}, SALES_ROWS[0], {
    subtotal: 38.0, tax_amount: 4.5, discount_amount: 0, amount_paid: 50, change_amount: 7.5,
    cashier: null, actor_user_uid: null, terminal_id: null, notes: '',
  }),
  items: [{ product_id: 'p1', product_name: 'Espresso Beans 1kg', sku: 'EB1', quantity: 2,
    unit_price: 18.5, discount_pct: 0, tax_rate: 16, line_total: 42.92 }],
};
const LIST_DATA = {
  returns: [{ id: 9, return_number: 'R-0007', sale_number: 'S-1041', customer_name: 'Walk-in',
    refund_method: 'cash', refund_amount: 12.25, created_at: '2026-08-22 09:05:00' }],
  purchaseOrders: [{ id: 4, po_number: 'PO-0031', supplier_name: 'Acme Trading', status: 'pending',
    total: 430.75, ordered_at: '2026-08-18', received_at: null }],
  categories: [{ id: 'c1', name: 'Beverages' }],
  suppliers: [{ id: 's1', name: 'Acme Trading', phone: '0790000000', email: 'ops@acme.example',
    address: 'Amman', order_count: 4 }],
  customers: [{ id: 'cu1', name: 'Ann Q', phone: '0791111111', email: 'ann@example.co',
    loyalty_points: 120, total_spent: 512.25, order_count: 7 }],
  heldSales: [{ id: 3, hold_number: 'H-0003', label: 'blue jacket', item_count: 2,
    customer_name: 'Walk-in', total: 31.4, created_at: '2026-08-22 10:15:00' }],
  auditLog: [{ id: 1, timestamp: '2026-08-22 11:00:00', user_id: '6f1c2d34-aa11-4b22-9c33-7d44e55f6677',
    action: 'create', entity: 'sale', entity_id: 1, details: 'Sale S-1041 created' }],
};

function apiResponseFor(url) {
  const u = String(url);
  const ok = (data, meta) => ({ status: 'success', data, meta });
  if (/\/customers\/[^/?]+\/sales/.test(u)) return ok(SALES_ROWS);
  if (/\/sales\/recent/.test(u)) return ok(SALES_ROWS);
  if (/\/sales\/\d+/.test(u)) return ok(SALE_DETAIL);
  if (/\/audit-log/.test(u)) return ok(LIST_DATA.auditLog, { total: 1, page: 1, limit: 50, actions: ['create'], entities: ['sale'] });
  if (/\/held-sales/.test(u)) return ok(LIST_DATA.heldSales);
  if (/\/purchase-orders/.test(u)) return ok(LIST_DATA.purchaseOrders);
  if (/\/returns/.test(u)) return ok(LIST_DATA.returns);
  if (/\/products/.test(u)) return ok(PRODUCTS);
  if (/\/categories/.test(u)) return ok(LIST_DATA.categories);
  if (/\/suppliers/.test(u)) return ok(LIST_DATA.suppliers);
  if (/\/customers/.test(u)) return ok(LIST_DATA.customers);
  return ok(STATS);
}

/**
 * Values that come from the DATABASE, not from this build: product names,
 * units, category names, customer names, receipt numbers. They are user DATA
 * and must never be translated, so they are exempt from the catalog
 * requirement. Listed explicitly, from the fixtures above, rather than
 * pattern-matched — so a new untranslated literal can never be waved through
 * as "probably data".
 */
const RENDERED_DATA_VALUES = new Set([
  'Espresso Beans', 'Espresso Beans 1kg', 'Paper Cups', 'kg',
  'bag', 'pack', 'Beverages', 'Groceries',
  'Walk-in', 'cash', 'S-1041', 'PC', 'EB1',
  // From the list-screen fixtures. Each is a COLUMN VALUE the server wrote,
  // named with the field it comes from so nobody has to guess later:
  'completed',                       // sales.status
  'create',                          // audit_log.action
  'sale',                            // audit_log.entity
  'Sale S', 'created',               // audit_log.details, free text from the backend
]);

/** A canonical uuid4, i.e. a database identifier rather than copy.
 *  Mirrors RetailSystem._looksLikeUuid. Derived rather than listed, because a
 *  uuid's hex groups produce accidental two-letter "words" ("aa", "de", "ff")
 *  that the language segmenter cannot distinguish from real ones — and pinning
 *  the fixture to a uuid that happens not to contain any would be a fixture
 *  chosen to avoid the check rather than to survive it. */
const UUID4 = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function loadRetailSystem() {
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');
  const els = Object.create(null);
  const tbody = makeElementStub();
  const chartHosts = {};
  const namedQueries = Object.create(null);
  const overlays = [];

  const getEl = (id) => {
    if (!els[id]) {
      const el = makeElementStub({ id });
      if (id === 'r-dash-hourly' || id === 'r-dash-pay') {
        chartHosts[id] = makeElementStub();
        el.parentElement = chartHosts[id];
      }
      els[id] = el;
    }
    return els[id];
  };

  const sandbox = {
    console,
    // A MARKING stand-in for i18n.js's t(), not the usual identity stub.
    //
    // Identity is what every other test in this directory uses, and for them it
    // is right. It is useless here, because it makes `t('Current Sale')` and a
    // bare literal `Current Sale` render byte-identically — so a test built on
    // it cannot tell a translated string from an untranslated one, which is the
    // entire question this file exists to answer. (Verified the hard way: with
    // an identity stub, re-gluing "active products" onto a number sailed
    // straight through.)
    //
    // Wrapping each translated value in sentinels means the rendered DOM says
    // which spans went through t() and which did not. The sentinels are C0
    // control characters, so _esc() passes them through untouched and they
    // cannot collide with real copy.
    t: (s) => MARK_OPEN + s + MARK_CLOSE,
    fetch: (url) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(apiResponseFor(url)) }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem: () => {} },
    setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
    // A host global, not a JS intrinsic: without it _loadSalesHistory and
    // _loadAuditLog throw inside their own try/catch and render their error
    // state, i.e. a screen with no rows on it for this sweep to read.
    URLSearchParams,
    document: {
      activeElement: null,
      getElementById(id) { return id === 'ret-styles' ? makeElementStub() : getEl(id); },
      createElement() { const el = makeElementStub(); return el; },
      querySelector(sel) {
        if (sel === '#r-dash-recent tbody') return tbody;
        if (!namedQueries[sel]) namedQueries[sel] = makeElementStub({ id: '::query::' + sel });
        return namedQueries[sel];
      },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      // Modals never touch #sub-content — they are appended to <body>. A stub
      // that swallowed this made every modal in the product invisible to this
      // file, which is where the customer Purchase-History table lives.
      body: { appendChild(node) { if (node) overlays.push(node); } },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
    SubsystemApp: { active: 'retail', showToast() {}, _navigate() {}, hasCapability: () => true },
  };
  sandbox.window = sandbox;
  sandbox.Chart = function ChartStub() { return { destroy() {} }; };
  sandbox.Chart.getChart = () => null;

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return { RetailSystem: sandbox.RetailSystem, els, tbody, chartHosts, namedQueries, overlays };
}

/** Drain the microtask queue so a render that fires an un-awaited load finishes. */
async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

/* ── SCOPE, STATED RATHER THAN IMPLIED ──────────────────────────────────────
 *
 * Two different questions are asked of the surfaces below, and conflating them
 * is what would make this file either useless or permanently red.
 *
 *   STRUCTURAL claims — bidi isolation, an emoji sharing a node with words, a
 *   key handed to t() that no catalog answers — are properties of the RENDER.
 *   They hold, or fail, on every screen in the product. They are swept over
 *   ALL of it, with no exemption anywhere.
 *
 *   The CATALOG-COVERAGE claim — "no literal reaches the operator without
 *   passing through t()" — is a property of what has been LOCALIZED. This
 *   programme localized the till and the dashboard (see this file's header);
 *   Products, Customers, Suppliers, Returns, Purchase Orders and the modals
 *   were never in it and are, today, English-only by construction.
 *
 * Rendering the second group and asserting the first claim over it would
 * produce a permanently failing suite that says nothing new. Rendering it and
 * saying NOTHING would be the silence this whole programme exists to remove.
 * So it is rendered, swept with the same rule, and its offenders are COUNTED,
 * NAMED and CAPPED — the same treatment retail_design_contrast_test.js gives
 * chrome rules its corpus cannot reach. The cap is a ratchet: it may fall when
 * a screen is localized, and raising it is a reviewable act.
 */
const LOCALIZED_SURFACES = new Set([
  'POS shell', 'cart lines', 'product grid', 'empty cart',
  'cashier landing', 'dashboard', 'recent transactions',
  'hourly chart empty state', 'payment chart empty state',
  'dashboard bound values', 'POS bound values',
  'sales history', 'sales history rows', 'audit log', 'audit log rows',
  // Localized 2026-09-08. It is on the money path -- it is what the till shows
  // the instant a sale is rung -- so it belongs under the catalog requirement,
  // not in the un-localized ledger below.
  'sale complete modal',
]);

/* Offenders on the surfaces this programme never localized, at the time of
   writing. Printed by name every run — read the list, not the number.
   Lowering it by localizing a screen is always the better move; raising it
   means a NEW English literal was added to a screen that was already behind,
   which is the drift this ratchet exists to make visible.

   Three of the emoji-node entries are a PARSER artefact rather than a product
   defect, and are counted anyway rather than filtered: the "⬆ Import" buttons
   carry an onclick containing nested single quotes
   (`ImportWizard.open('retail','products',()=>...)`), which
   retail_surface_domlite.js's attribute reader terminates early, so part of the
   handler leaks into the button's text node. Filtering them here would mean
   this file quietly deciding which of a shared parser's outputs to believe;
   counting them keeps the artefact visible to whoever owns that parser. */
const UNLOCALIZED_LITERAL_BUDGET = 59;
const UNLOCALIZED_EMOJI_NODE_BUDGET = 6;

/** Render every surface this agent reworked, as named fragments. */
async function renderAllSurfaces() {
  const fragments = [];

  const pos = loadRetailSystem();
  const posHost = makeElementStub();
  pos.RetailSystem._renderPOS(posHost);
  pos.RetailSystem._products = PRODUCTS;
  pos.RetailSystem._cart = [];
  pos.RetailSystem._addToCart('p1');
  pos.RetailSystem._renderPOSGrid();
  fragments.push(['POS shell', posHost.innerHTML]);
  fragments.push(['cart lines', pos.els['pos-cart'].innerHTML]);
  fragments.push(['product grid', pos.els['pos-product-grid'].innerHTML]);

  // The empty cart is a separate surface with its own copy.
  pos.RetailSystem._cart = [];
  pos.RetailSystem._renderCart();
  fragments.push(['empty cart', pos.els['pos-cart'].innerHTML]);

  const cashier = loadRetailSystem();
  const landHost = makeElementStub();
  cashier.RetailSystem._renderCashierLanding(landHost);
  fragments.push(['cashier landing', landHost.innerHTML]);

  const dash = loadRetailSystem();
  const dashHost = makeElementStub();
  await dash.RetailSystem._renderDashboard(dashHost);
  fragments.push(['dashboard', dashHost.innerHTML]);
  fragments.push(['recent transactions', dash.tbody.innerHTML]);
  fragments.push(['hourly chart empty state', dash.chartHosts['r-dash-hourly'].innerHTML]);
  fragments.push(['payment chart empty state', dash.chartHosts['r-dash-pay'].innerHTML]);

  // Values written after the fetch with `el.textContent = ...` never appear in
  // any innerHTML, so a sweep over markup alone cannot see them — and that is
  // exactly where the "312 active products" class of bug lives: an interpolated
  // sentence assigned to a KPI element. Harvest them and present each as its
  // own text node, which is what the browser ends up with too.
  fragments.push(['dashboard bound values', boundValuesAsMarkup(dash.els)]);
  fragments.push(['POS bound values', boundValuesAsMarkup(pos.els)]);

  // ── THE LIST SCREENS AND THE MODALS ─────────────────────────────────────
  //
  // Absent from this corpus for two rounds, and an adversarial verifier proved
  // what that bought: `this._bdi(...)` could be deleted from the Sales History
  // date cell, the Returns date cell, the Returns return_number, the customer
  // Purchase-History date and the PO number — FIVE sites — and every one of the
  // six design/surface suites stayed green, because the only date column any of
  // them rendered was the dashboard's.
  const listScreens = [
    ['sales history', '#sh-table tbody', 'sales history rows', (rs, c) => rs._renderSalesHistory(c)],
    ['returns', '#ret-table tbody', 'returns rows', (rs, c) => rs._renderReturns(c)],
    ['purchase orders', '#po-table tbody', 'purchase order rows', (rs, c) => rs._renderPurchases(c)],
    ['products', '#prod-table tbody', 'product rows', (rs, c) => rs._renderProducts(c)],
    ['customers', '#cust-table tbody', 'customer rows', (rs, c) => rs._renderCustomers(c)],
    ['suppliers', '#sup-table tbody', 'supplier rows', (rs, c) => rs._renderSuppliers(c)],
    ['audit log', '#aud-table tbody', 'audit log rows', (rs, c) => rs._renderAuditLog(c)],
  ];
  for (const [name, rowSelector, rowName, render] of listScreens) {
    const ctx = loadRetailSystem();
    const host = makeElementStub();
    await render(ctx.RetailSystem, host);
    await settle();
    fragments.push([name, host.innerHTML]);
    const rows = ctx.namedQueries[rowSelector];
    fragments.push([rowName, rows ? rows.innerHTML : '']);
  }

  {
    const ctx = loadRetailSystem();
    const host = makeElementStub();
    await ctx.RetailSystem._renderCustomers(host);
    await settle();
    await ctx.RetailSystem._viewCustomer('cu1');
    await settle();
    const overlay = ctx.overlays[ctx.overlays.length - 1];
    fragments.push(['customer modal', overlay ? overlay.innerHTML : '']);
    // The Purchase-History table replaces its placeholder via `outerHTML`, so
    // it exists in NO innerHTML anywhere and a sweep over markup alone cannot
    // see it — the same class of gap as the textContent-bound KPI values above.
    const hist = ctx.els['cu-hist-loading'];
    fragments.push(['customer purchase history', hist ? (hist.outerHTML || '') : '']);
  }
  {
    const ctx = loadRetailSystem();
    const host = makeElementStub();
    await ctx.RetailSystem._renderSalesHistory(host);
    await settle();
    await ctx.RetailSystem._viewSale(1);
    await settle();
    const overlay = ctx.overlays[ctx.overlays.length - 1];
    fragments.push(['sale detail modal', overlay ? overlay.innerHTML : '']);
  }
  {
    const ctx = loadRetailSystem();
    ctx.RetailSystem._renderPOS(makeElementStub());
    ctx.RetailSystem._openHeldSalesModal();
    await settle();
    const overlay = ctx.overlays[ctx.overlays.length - 1];
    fragments.push(['held sales modal', overlay ? overlay.innerHTML : '']);
    fragments.push(['held sales list', ctx.els['held-list'] ? ctx.els['held-list'].innerHTML : '']);
  }
  {
    // The modal shown after EVERY completed sale, and the last thing a cashier
    // reads before the next customer. It was absent from this corpus and had
    // zero t() calls: "Sale Complete!", "Receipt #S-1001", "Print" and "New
    // Sale" were bare literals in neither catalog, and the receipt number was
    // glued into the sentence -- the "312 active products" shape this file's
    // header documents. `_viewSale` builds the OTHER, similarly-named modal
    // ("sale detail modal" above), which is why the gap read as covered.
    const ctx = loadRetailSystem();
    ctx.RetailSystem._showReceipt({
      sale_number: 'S-1001',
      total: 12.345,
      change: 2.655,
      // A product name already in RENDERED_DATA_VALUES, so this fixture adds
      // no new "is it data or copy?" exemption to that set.
      lines: [{ name: 'Espresso Beans', quantity: 2, line_total: 9.0, product_id: 7 }],
    });
    await settle();
    const overlay = ctx.overlays[ctx.overlays.length - 1];
    fragments.push(['sale complete modal', overlay ? overlay.innerHTML : '']);
  }

  return fragments;
}

/* Every surface renderAllSurfaces() sets out to build. Compared EXACTLY, so a
   screen whose fetch was mis-routed, whose render threw inside its own
   try/catch, or that was quietly dropped fails BY NAME rather than being
   absorbed into a corpus-wide total. */
const DECLARED_SURFACES = [
  'POS shell', 'cart lines', 'product grid', 'empty cart', 'cashier landing',
  'dashboard', 'recent transactions', 'hourly chart empty state',
  'payment chart empty state', 'dashboard bound values', 'POS bound values',
  'sales history', 'sales history rows', 'returns', 'returns rows',
  'purchase orders', 'purchase order rows', 'products', 'product rows',
  'customers', 'customer rows', 'suppliers', 'supplier rows',
  'audit log', 'audit log rows',
  'customer modal', 'customer purchase history', 'sale detail modal',
  'held sales modal', 'held sales list', 'sale complete modal',
];

function testTheCorpusIsTheCorpusItDeclares(fragments) {
  assert.deepStrictEqual(
    fragments.map(([name]) => name), DECLARED_SURFACES,
    'renderAllSurfaces() did not produce the surfaces it declares.'
  );
  const empty = fragments.filter(([, html]) => !html || html.length < 40).map(([name]) => name);
  assert.deepStrictEqual(
    empty, [],
    'Surface(s) rendered (almost) nothing:\n  ' + empty.join('\n  ') +
    '\n\nEvery sweep in this file is a loop over these fragments. An empty one is ' +
    'a screen the file reports a clean bill of health on having read no markup.'
  );
  console.log(`PASS: all ${DECLARED_SURFACES.length} declared surfaces rendered`);
}

/** Wrap every textContent an element received into inspectable markup. */
function boundValuesAsMarkup(els) {
  return Object.keys(els)
    .filter((id) => typeof els[id].textContent === 'string' && els[id].textContent.trim())
    .map((id) => `<span data-bound-to="${id}">${els[id].textContent}</span>`)
    .join('');
}

/**
 * Collect every string the operator can read, PER TEXT NODE and per visible
 * attribute — not per element.
 *
 * Per-node is the whole point. i18n.js matches a node whose FULL trimmed text
 * is a catalog key, so "312 active products" as one node is unreachable while
 * "312" and "active products" as two nodes is fine. Flattening an element's
 * subtree into one string would report the reachable version as broken and,
 * worse, the broken version as fine.
 *
 * Attributes are collected too, and marked `sweepCanRescue: false`, because
 * i18n.js only ever walks text nodes — a placeholder or title that skipped t()
 * has no second chance.
 *
 * Skipped: aria-hidden decorative glyphs, and anything under
 * [data-intl-date] — a date is formatted by Intl against the ACTIVE language
 * (see RetailSystem._localeDate) and could never be a catalog key, since no
 * JSON file can enumerate every weekday/month combination. The marker lives in
 * the markup rather than in an allowlist here, so the exemption is visible to
 * whoever is reading the render code.
 */
function visibleStrings(fragments) {
  const found = [];
  for (const [surface, html] of fragments) {
    const root = dom.parseFragment(html);
    for (const el of dom.allElements(root)) {
      if (el.tag === 'style' || el.tag === 'script') continue;
      if (el.attrs['data-intl-date'] !== undefined) continue;

      for (const attr of VISIBLE_ATTRIBUTES) {
        const raw = decodeEntities(el.attrs[attr] || '');
        if (!raw) continue;
        if (!isTranslatable(asRendered(raw))) continue;
        found.push({
          surface, raw, kind: `@${attr}`, sweepCanRescue: false,
          where: dom.describe(el),
        });
      }

      if (el.attrs['aria-hidden'] === 'true') continue;
      for (const child of el.children || []) {
        if (child.type !== 'text') continue;
        const raw = decodeEntities(child.text).replace(/\s+/g, ' ').trim();
        if (!isTranslatable(asRendered(raw))) continue;
        found.push({
          surface, raw, kind: 'text', sweepCanRescue: true,
          where: dom.describe(el),
        });
      }
    }
  }
  return found;
}

function testEveryKeyPassedToTranslateExistsInBothCatalogs(fragments) {
  return Promise.resolve().then(() => {
    // Every value the render actually handed to t(). If one of these is not in
    // both catalogs, t() silently returns the English key and the string is
    // English on an Arabic page — the failure mode the brief calls out.
    const seen = new Map();
    for (const s of visibleStrings(fragments)) {
      for (const key of translatedKeys(s.raw)) {
        if (!seen.has(key)) seen.set(key, `${s.surface} / ${s.where}`);
      }
    }
    assert.ok(
      seen.size >= 25,
      'Only ' + seen.size + ' t() calls were observed across the reworked surfaces. ' +
      'These screens carry far more copy than that, so treat this as a harness ' +
      'failure — a sweep that finds nothing reports nothing.'
    );

    const missing = [];
    for (const [key, where] of seen) {
      const inEn = key in EN;
      const inAr = key in AR;
      if (!inEn || !inAr) {
        missing.push(`${JSON.stringify(key)} (${where}) — en:${inEn ? 'yes' : 'MISSING'} ar:${inAr ? 'yes' : 'MISSING'}`);
      }
    }
    assert.deepStrictEqual(
      missing, [],
      'These strings are passed to t() but are absent from a catalog, so t() returns ' +
      'the English key unchanged and they render in English on an Arabic install:\n  ' +
      missing.join('\n  ') + '\n\nAdd each to BOTH locales/en.json and locales/ar.json.'
    );

    console.log(`PASS: all ${seen.size} strings passed to t() exist in both catalogs`);
  });
}

/** Every string that reaches the operator without ever passing through t(). */
function unreachableLiterals(strings) {
  // Anything OUTSIDE a t() region is a raw literal from this build. It is
  // acceptable only if it carries no language (an amount, a separator), if it
  // is user data from the database, or — for TEXT NODES ONLY — if the node's
  // full rendered text happens to be a catalog key, because i18n.js's DOM
  // sweep rescues exactly that case. Attributes get no such rescue: the sweep
  // never looks at them.
  const offenders = [];
  for (const s of strings) {
    const rendered = asRendered(s.raw);
    if (UUID4.test(rendered)) continue;
    const literalPart = outsideTranslation(s.raw);
    const segments = languageSegments(literalPart)
      .filter((seg) => !RENDERED_DATA_VALUES.has(seg));
    if (!segments.length) continue;
    if (s.sweepCanRescue && rendered in EN && rendered in AR) continue;
    // A host-locale date is a DIFFERENT defect wearing the same clothes, and
    // "wrap it in t()" is the wrong advice for it: no catalog can enumerate
    // every weekday/month/meridiem combination. `toLocaleString()` with no
    // argument formats against the BROWSER's locale, not the language the
    // operator picked in this app -- so an Arabic-reading shopkeeper on an
    // English Windows gets an English timestamp under an Arabic heading. That
    // is the same bug as the hardcoded toLocaleDateString('en-US') this file
    // was written for; the fix is RetailSystem._localeDate, not a catalog key.
    const hostLocaleDate = /\b(AM|PM)\b/.test(rendered) || /\d{1,2}\/\d{1,2}\/\d{2,4}/.test(rendered);
    offenders.push({
      surface: s.surface,
      line: `[${s.surface}] ${s.kind} ${JSON.stringify(rendered)} in ${s.where} — ` +
        `untranslated literal(s): ${JSON.stringify(segments)}` +
        (s.sweepCanRescue
          ? ' (and the full node text is not a catalog key, so the DOM sweep cannot rescue it)'
          : ' (an attribute — i18n.js only sweeps text nodes, so there is no rescue)') +
        (hostLocaleDate
          ? '\n      ^ this is a DATE formatted against the host locale (a bare '
            + 'toLocaleString()/toLocaleDateString()), not a missing catalog key. Route it '
            + 'through RetailSystem._localeDate(), which keys off the active language.'
          : ''),
    });
  }
  return offenders;
}

function testNoUntranslatedLiteralReachesTheOperator(fragments) {
  return Promise.resolve().then(() => {
    const strings = visibleStrings(fragments);
    assert.ok(
      strings.length >= 30,
      'Only ' + strings.length + ' operator-visible strings were found; harness failure.'
    );

    const all = unreachableLiterals(strings);
    const localized = all.filter((o) => LOCALIZED_SURFACES.has(o.surface));
    const unlocalized = all.filter((o) => !LOCALIZED_SURFACES.has(o.surface));

    assert.deepStrictEqual(
      localized.map((o) => o.line), [],
      'These strings reach the operator without ever passing through t(), and cannot ' +
      'be rescued by i18n.js:\n  ' + localized.map((o) => o.line).join('\n  ') +
      '\n\nWrap the string in t() and add it to BOTH catalogs. If a number is glued to ' +
      'words, split them into separate nodes — the sweep only matches a node whose ' +
      'FULL trimmed text is a catalog key, so "312 active products" is unreachable ' +
      'while "312" + "active products" is fine.'
    );

    // The ledger. Every one of these is an English string a shopkeeper reading
    // Arabic will see; none is a defect THIS programme introduced, and every
    // one of them was invisible to this file until the corpus was widened.
    const bySurface = new Map();
    for (const o of unlocalized) bySurface.set(o.surface, (bySurface.get(o.surface) || 0) + 1);
    console.log(
      `      ${unlocalized.length} untranslated literal(s) on the ${bySurface.size} surface(s) this ` +
      'programme never localized — rendered and swept, NOT under the catalog requirement:'
    );
    for (const [surface, n] of [...bySurface].sort((a, b) => b[1] - a[1])) {
      console.log(`        ${String(n).padStart(3)}  ${surface}`);
    }
    assert.ok(
      unlocalized.length <= UNLOCALIZED_LITERAL_BUDGET,
      `${unlocalized.length} untranslated literals on the un-localized screens; the recorded ` +
      `budget is ${UNLOCALIZED_LITERAL_BUDGET}. A NEW English literal was added to a screen ` +
      'that is already English-only:\n  ' + unlocalized.map((o) => o.line).join('\n  ') +
      '\n\nWrap it in t() and add it to both catalogs rather than raising the budget.'
    );

    console.log(
      `PASS: none of the ${strings.length} operator-visible strings on the ` +
      `${LOCALIZED_SURFACES.size} localized surfaces is an unreachable literal ` +
      `(${unlocalized.length} on un-localized screens, within the recorded budget of ${UNLOCALIZED_LITERAL_BUDGET})`
    );
  });
}

function testNoTranslatableStringSharesANodeWithAnEmoji(fragments) {
  return Promise.resolve().then(() => {
    // The specific defect that made "🛒 Open POS" unreachable by the DOM sweep,
    // and that this file has now shipped twice.
    const offenders = [];
    const unlocalized = [];
    for (const n of visibleStrings(fragments)) {
      if (n.kind !== 'text') continue;   // only the DOM sweep is defeated this way
      const rendered = asRendered(n.raw);
      if (!/\p{Extended_Pictographic}/u.test(rendered)) continue;
      const line = `[${n.surface}] ${JSON.stringify(rendered)} in ${n.where}`;
      (LOCALIZED_SURFACES.has(n.surface) ? offenders : unlocalized).push(line);
    }
    assert.deepStrictEqual(
      offenders, [],
      'These text nodes mix an emoji with translatable words:\n  ' + offenders.join('\n  ') +
      '\n\ni18n.js matches a node only when its FULL trimmed text is a catalog key, so ' +
      'an emoji sharing the node makes the string unreachable by the sweep even when ' +
      'the key exists. Put the emoji in its own aria-hidden span.'
    );
    // Same ledger treatment, same reason: on a screen with no catalog entries at
    // all, an emoji sharing the node is not what is keeping the string English.
    if (unlocalized.length) {
      console.log(`      ${unlocalized.length} emoji-sharing node(s) on un-localized surfaces:`);
      for (const line of unlocalized) console.log('        ' + line);
    }
    assert.ok(
      unlocalized.length <= UNLOCALIZED_EMOJI_NODE_BUDGET,
      `${unlocalized.length} emoji-sharing text nodes on un-localized screens; the recorded ` +
      `budget is ${UNLOCALIZED_EMOJI_NODE_BUDGET}:\n  ` + unlocalized.join('\n  ')
    );
    console.log(
      'PASS: no translatable string on a localized surface shares a text node with an emoji ' +
      `(${unlocalized.length} on un-localized screens, within the recorded budget of ${UNLOCALIZED_EMOJI_NODE_BUDGET})`
    );
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// BIDI — a run with no strong character must be isolated AND given a direction
// ─────────────────────────────────────────────────────────────────────────────
//
// The failure this catches is not "the text mirrors". It is that the text
// renders a DIFFERENT VALUE.
//
// "2026-08-21 18:42" is a date and a time with a space between them. Under the
// Unicode bidi algorithm every character in it is neutral or weak: the digits
// are EN, `-` is ES and `:` is CS (both of which merge into the number they sit
// between, so each half stays one run), and the separating space is WS with no
// strong character anywhere to resolve against. Rule N2 therefore resolves that
// space to the PARAGRAPH direction. On an Arabic page that is RTL, so the two
// number runs are laid out right-to-left as blocks and the cell reads
// "18:42 2026-08-21" — the reader sees a time where the date belongs and a date
// where the time belongs. Not a cosmetic mirror: a wrong date.
//
// <bdi> alone does not fix it. <bdi>'s default is dir="auto", which picks its
// direction from the first STRONG character — and this string has none, so auto
// falls back to the paragraph and reproduces the bug inside the isolate. The
// direction has to be STATED, which is why RetailSystem._bdi() defaults to
// dir="ltr" and why this test requires a dir attribute rather than just a <bdi>.
//
// Mutation-proven, three ways:
//   * drop `this._bdi(...)` from the Date column          -> fails (not isolated)
//   * change _bdi's default from 'ltr' to 'auto'          -> fails (no direction)
//   * put the ISO 'T' back in the STATS fixture           -> fails the anti-vacuity
//     assertion, because the strong 'T' means no hazard run is rendered at all
//     and the sweep would be checking nothing.

/** Any character that gives a run its own direction — Latin, Arabic, anything. */
const STRONG_DIRECTIONAL = /\p{L}/u;

/**
 * Two digit runs separated by whitespace: "2026-08-21 18:42", "12 34".
 *
 * Deliberately NOT "contains a digit". A single run cannot reorder — there is
 * nothing for it to swap with — so "$1,284.50", "3", "12%" and "40" are not
 * hazards and flagging them would be noise that teaches people to add
 * exemptions. `.`/`,`/`:`/`-`/`/` are allowed INSIDE a run because the bidi
 * algorithm merges them into the adjacent number; whitespace is the separator
 * that does not merge, and is therefore the one that reorders.
 */
const TWO_NEUTRAL_NUMBER_RUNS = /\d[\d.,:\-/]*\s+[\d.,:\-/]*\d/;

/** The nearest ancestor <bdi>, or null. */
function bdiAncestor(node) {
  let n = node.parent;
  while (n && n.type === 'element') {
    if (n.tag === 'bdi') return n;
    n = n.parent;
  }
  return null;
}

/**
 * How many TRUNCATED-TIMESTAMP CELLS the product renders, counted in
 * subsystem-retail.js itself.
 *
 * This is the corpus's floor, and the derivation is the whole point of it.
 * `hazards.length >= 1` was not a floor: the dashboard's Date column satisfied
 * it on its own, so `this._bdi(...)` could be deleted from the four OTHER
 * timestamp cells in the product and this sweep still reported a clean bill of
 * health — which is exactly what an adversarial verifier did.
 *
 * What is counted is the HAZARD SITE, not the fix: `(x.created_at||'').slice(0,16)`
 * renders "2026-08-21 18:42" — two neutral runs, no strong character — whether
 * or not anybody wrapped it in _bdi(). So deleting a `_bdi` leaves the expected
 * count unchanged, the run still appears in the DOM, and it fails the real
 * assertion below instead of quietly lowering the bar it is measured against.
 * Deleting a SCREEN from the corpus, on the other hand, drops the found count
 * below the expected one and fails here.
 */
function expectedTimestampCells() {
  const src = fs.readFileSync(FRONTEND_FILE, 'utf8');
  const sites = src.match(/created_at\s*\|\|\s*''\s*\)\s*\.slice\(\s*0\s*,\s*16\s*\)/g) || [];
  assert.ok(
    sites.length >= 4,
    `Found only ${sites.length} truncated-timestamp render site(s) in subsystem-retail.js. ` +
    'Either the product stopped rendering dates that way (in which case this ' +
    'derivation needs rewriting, not deleting) or the scan is broken — and a ' +
    'broken scan would set this sweep\'s floor to nearly zero, which is the ' +
    'failure it exists to prevent.'
  );
  return sites.length;
}

function testNeutralNumberRunsAreDirectionIsolated(fragments) {
  return Promise.resolve().then(() => {
    const hazards = [];
    const unisolated = [];

    for (const [surface, html] of fragments) {
      const root = dom.parseFragment(html);
      for (const el of dom.allElements(root)) {
        if (el.tag === 'style' || el.tag === 'script') continue;
        for (const child of el.children || []) {
          if (child.type !== 'text') continue;
          const text = asRendered(decodeEntities(child.text)).replace(/\s+/g, ' ').trim();
          if (!text) continue;
          if (STRONG_DIRECTIONAL.test(text)) continue;
          if (!TWO_NEUTRAL_NUMBER_RUNS.test(text)) continue;

          const where = `[${surface}] ${JSON.stringify(text)} in ${dom.describe(el)}`;
          hazards.push(where);

          const bdi = bdiAncestor(child);
          if (!bdi) {
            unisolated.push(`${where} — no <bdi> ancestor at all`);
          } else if (!bdi.attrs.dir) {
            unisolated.push(`${where} — inside <bdi> but with no dir attribute`);
          } else if (bdi.attrs.dir === 'auto') {
            unisolated.push(
              `${where} — inside <bdi dir="auto">, which resolves against the ` +
              'paragraph when the run has no strong character (i.e. here)'
            );
          }
        }
      }
    }

    // ANTI-VACUITY, DERIVED. Every check below is a filter over `hazards`; if
    // the fixtures stopped producing a bare number run — an ISO 'T' creeping
    // back into a created_at, a render that silently stopped emitting rows, a
    // screen dropped from the corpus — the filter would return a short list and
    // this test would report a clean bill of health having examined a fraction
    // of the product. "I found nothing to check" must never read as a pass, and
    // neither must "I found one".
    const expected = expectedTimestampCells();
    assert.ok(
      hazards.length >= expected,
      `Only ${hazards.length} unisolated-run CANDIDATE(s) were rendered across ${fragments.length} ` +
      `surfaces, but subsystem-retail.js renders a truncated "YYYY-MM-DD HH:MM" into ${expected} ` +
      'cells. So at least one screen that puts a bare two-run timestamp in front of an ' +
      'operator is NOT in this corpus, and the sweep below proves nothing about it.\n\n' +
      'Found:\n  ' + (hazards.join('\n  ') || '(none)') +
      '\n\nThe two likely causes: a screen was dropped from renderAllSurfaces(), or a ' +
      'fixture\'s created_at carries an ISO "T" — that T is a strong LTR character, it ' +
      'anchors the whole run, and it makes the hazard impossible to reproduce in the test ' +
      'while leaving it live in production. The server writes "%Y-%m-%d %H:%M:%S" with a ' +
      'SPACE (retail_api.py::create_sale).'
    );

    assert.deepStrictEqual(
      unisolated, [],
      'These rendered runs have NO strong directional character and are not ' +
      'direction-isolated:\n  ' + unisolated.join('\n  ') +
      '\n\nIn Arabic the paragraph direction resolves the whitespace between the ' +
      'two number runs, so they swap: a "2026-08-21 18:42" cell renders as ' +
      '"18:42 2026-08-21" and states the wrong date. Wrap the value in ' +
      'RetailSystem._bdi(), which emits <bdi dir="ltr"> — the isolation AND the ' +
      'stated direction are both required, because <bdi>\'s own dir="auto" picks ' +
      'its direction from the first strong character and there is not one here.'
    );

    console.log(
      `PASS: all ${hazards.length} rendered run(s) with no strong directional character are ` +
      `isolated with an explicit direction (floor: ${expected}, derived from the truncated-` +
      'timestamp render sites in subsystem-retail.js)'
    );
    for (const line of hazards) console.log('      ' + line.slice(0, 110));
  });
}

function testDatesFollowTheActiveLanguage() {
  const src = fs.readFileSync(FRONTEND_FILE, 'utf8');

  // (a) Neither reworked surface may pin a locale at the call site.
  const renderRegion = src.slice(
    src.indexOf('async _renderDashboard(c)'),
    src.indexOf('async _loadPOSData()')
  );
  assert.ok(renderRegion.length > 1000, 'Could not isolate the render region to check.');
  assert.ok(
    !/toLocaleDateString\(\s*['"]en-US['"]/.test(renderRegion),
    'The dashboard or cashier landing still calls toLocaleDateString("en-US") ' +
    'directly. That prints an English weekday and month under an Arabic heading. ' +
    'Route it through RetailSystem._localeDate(), which keys off the active language.'
  );
  assert.ok(
    /_localeDate\(\)/.test(renderRegion),
    'Neither reworked surface calls _localeDate(); the date is coming from somewhere ' +
    'this check cannot see.'
  );

  // (b) ...and the helper they delegate to must itself consult the active
  // language. Checking only the call sites would let someone hardcode 'en-US'
  // one level down and leave every assertion above still green — the helper
  // is defined outside the render region, so it is invisible to (a).
  const helperStart = src.indexOf('_localeDate(d) {');
  assert.ok(helperStart !== -1, 'RetailSystem._localeDate() is missing entirely.');
  const helperBody = src.slice(helperStart, src.indexOf('\n  },', helperStart));
  assert.ok(
    /AuraI18n/.test(helperBody),
    '_localeDate() never consults AuraI18n, so it cannot know which language the ' +
    'operator selected — every date renders in one fixed locale regardless. Body:\n' +
    helperBody
  );
  assert.ok(
    /['"]ar['"]/.test(helperBody),
    '_localeDate() has no Arabic branch, so an Arabic page still gets English ' +
    'weekday and month names. Body:\n' + helperBody
  );

  console.log('PASS: rendered dates follow the active language, at the call site and in the helper');
}

function testCatalogsRemainInParity() {
  // Cheap local mirror of retail_localization_test.py's contract, so a catalog
  // change made for these surfaces cannot break the (very slow) Python suite
  // without this fast Node run noticing first.
  const enKeys = Object.keys(EN);
  const arKeys = Object.keys(AR);
  assert.deepStrictEqual(
    enKeys.filter((k) => !(k in AR)), [], 'keys present in en.json but missing from ar.json'
  );
  assert.deepStrictEqual(
    arKeys.filter((k) => !(k in EN)), [], 'keys present in ar.json but missing from en.json'
  );
  assert.deepStrictEqual(
    enKeys.filter((k) => !(AR[k] || '').trim()), [], 'keys whose Arabic value is empty'
  );
  assert.deepStrictEqual(
    enKeys.filter((k) => EN[k] === AR[k]), [],
    'keys whose Arabic value is byte-identical to the English (i.e. untranslated)'
  );
  console.log(`PASS: ${enKeys.length} catalog keys are in parity with real Arabic values`);
}

/* ── PER-TEST ISOLATION ─────────────────────────────────────────────────────
 *
 * main() used to be a flat `await` sequence. One assertion that threw ended the
 * process, so a run reported exactly ONE problem no matter how many existed —
 * and every check after the thrower was not "passing", it was NOT RUN, which is
 * indistinguishable from passing in the output.
 *
 * That is not a cosmetic complaint. At 725d94b it meant seven of this round's
 * own new guards never executed once: the bidi sweep here and the entire
 * unexercised-chrome ledger in retail_design_contrast_test.js — i.e. the
 * headline structural work of the round that wrote them — sat behind an earlier
 * failure and were never observed to run at all. It is also why these files took
 * several passes to fix: each run surfaced one defect, so the remaining work was
 * invisible and the job looked smaller than it was.
 *
 * So: every check runs, every failure is collected and printed, the process
 * exits non-zero if any failed. A failure no longer hides its successors.
 */
async function runAll(checks) {
  const failures = [];
  for (const [name, fn] of checks) {
    try {
      await fn();
    } catch (err) {
      failures.push([name, err]);
      console.error(`FAIL: ${name}`);
      console.error('      ' + String((err && err.message) || err).replace(/\n/g, '\n      '));
    }
  }
  return failures;
}

/* How many checks this file is known to contain. The list below is built
   conditionally (a surface render that throws removes the checks that consume
   it), and a list built conditionally can be built EMPTY — at which point
   runAll() loops over nothing, reports no failures, and the file exits 0 having
   verified nothing at all. That is the exact vacuity every other guard in this
   file is written against, so the runner gets one too. */
const EXPECTED_CHECKS = 7;

async function main() {
  const checks = [
    ['catalogs remain in parity', testCatalogsRemainInParity],
    ['rendered dates follow the active language', testDatesFollowTheActiveLanguage],
  ];

  // The corpus is rendered ONCE here rather than inside each check, so that a
  // render that blows up is reported as its own named failure instead of being
  // re-thrown identically by four consumers — and so the four checks that
  // depend on it are visibly SKIPPED rather than silently absent.
  let fragments = null;
  const setupFailures = [];
  try {
    fragments = await renderAllSurfaces();
  } catch (err) {
    setupFailures.push(['renderAllSurfaces()', err]);
    console.error('FAIL: renderAllSurfaces()');
    console.error('      ' + String((err && err.message) || err).replace(/\n/g, '\n      '));
    console.error('      4 checks that sweep the rendered corpus could not run.');
  }
  if (fragments) {
    checks.push(
      ['the corpus is the corpus it declares', () => testTheCorpusIsTheCorpusItDeclares(fragments)],
      ['every key passed to t() exists in both catalogs', () => testEveryKeyPassedToTranslateExistsInBothCatalogs(fragments)],
      ['no untranslated literal reaches the operator', () => testNoUntranslatedLiteralReachesTheOperator(fragments)],
      ['no translatable string shares a node with an emoji', () => testNoTranslatableStringSharesANodeWithAnEmoji(fragments)],
      ['neutral number runs are direction-isolated', () => testNeutralNumberRunsAreDirectionIsolated(fragments)],
    );
  }

  const failures = setupFailures.concat(await runAll(checks));
  const attempted = checks.length + setupFailures.length;

  if (attempted < EXPECTED_CHECKS) {
    console.error(
      `FAIL: retail_surface_i18n_test.js ran only ${attempted} of ${EXPECTED_CHECKS} known checks. ` +
      'A runner that quietly loses a check reports a clean bill of health on work it never did.'
    );
    process.exitCode = 1;
    return;
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_surface_i18n_test.js — ${failures.length} of ${attempted} checks failed:`);
    for (const [name] of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_surface_i18n_test.js — ${attempted} checks`);
}

main().catch((err) => {
  // Only reachable if the runner ITSELF breaks; every check-level throw is
  // caught and collected above.
  console.error('FAIL: retail_surface_i18n_test.js (runner)');
  console.error(err && err.message ? err.message : err);
  process.exitCode = 1;
});
