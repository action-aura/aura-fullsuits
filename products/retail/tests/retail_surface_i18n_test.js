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
 * Mutation-proven: reintroducing a bare literal, merging a number back into a
 * translatable sentence, or re-hardcoding the date locale all fail here.
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
const NON_LINGUISTIC_CHARS =
  '\\u00a0\\d.,:;%$()\\[\\]{}+|/\\\\\'"' +
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
    innerHTML: '', textContent: '', value: '', id: '', disabled: false,
    style: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {},
    querySelectorAll() { return []; }, addEventListener() {}, focus() {},
    getContext() { return {}; },
  }, overrides);
}

const STATS = {
  today_sales: 1284.5, today_transactions: 18, month_sales: 21450.75,
  month_transactions: 310, low_stock_alerts: 7, total_customers: 84,
  total_products: 312, today_returns: 65.44, sales_change_pct: 12,
  hourly_labels: [], hourly_data: [], payment_methods: {},
  recent_sales: [
    { id: 1, sale_number: 'S-1041', customer_name: 'Walk-in', item_count: 3,
      payment_method: 'cash', total: 42.5, created_at: '2026-08-21T18:42:00' },
  ],
};

const PRODUCTS = [
  { id: 'p1', name: 'Espresso Beans 1kg', sku: 'EB1', barcode: '111', sell_price: 18.5,
    tax_rate: 16, total_stock: 40, reorder_level: 5, unit: 'bag', category_name: 'Beverages' },
  { id: 'p2', name: 'Paper Cups', sku: 'PC', barcode: '222', sell_price: 4.25,
    tax_rate: 16, total_stock: 0, reorder_level: 10, unit: 'pack', category_name: 'Groceries' },
];

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
]);

function loadRetailSystem() {
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');
  const els = Object.create(null);
  const tbody = makeElementStub();
  const chartHosts = {};

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
    fetch: () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ data: STATS }) }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem: () => {} },
    document: {
      activeElement: null,
      getElementById(id) { return id === 'ret-styles' ? makeElementStub() : getEl(id); },
      createElement() { return makeElementStub(); },
      querySelector(sel) { return sel === '#r-dash-recent tbody' ? tbody : makeElementStub(); },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
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
  return { RetailSystem: sandbox.RetailSystem, els, tbody, chartHosts };
}

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

  return fragments;
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

function testEveryKeyPassedToTranslateExistsInBothCatalogs() {
  return renderAllSurfaces().then((fragments) => {
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

function testNoUntranslatedLiteralReachesTheOperator() {
  return renderAllSurfaces().then((fragments) => {
    const strings = visibleStrings(fragments);
    assert.ok(
      strings.length >= 30,
      'Only ' + strings.length + ' operator-visible strings were found; harness failure.'
    );

    // Anything OUTSIDE a t() region is a raw literal from this build. It is
    // acceptable only if it carries no language (an amount, a separator), if it
    // is user data from the database, or — for TEXT NODES ONLY — if the node's
    // full rendered text happens to be a catalog key, because i18n.js's DOM
    // sweep rescues exactly that case. Attributes get no such rescue: the sweep
    // never looks at them.
    const offenders = [];
    for (const s of strings) {
      const rendered = asRendered(s.raw);
      const literalPart = outsideTranslation(s.raw);
      const segments = languageSegments(literalPart)
        .filter((seg) => !RENDERED_DATA_VALUES.has(seg));
      if (!segments.length) continue;
      if (s.sweepCanRescue && rendered in EN && rendered in AR) continue;
      offenders.push(
        `[${s.surface}] ${s.kind} ${JSON.stringify(rendered)} in ${s.where} — ` +
        `untranslated literal(s): ${JSON.stringify(segments)}` +
        (s.sweepCanRescue
          ? ' (and the full node text is not a catalog key, so the DOM sweep cannot rescue it)'
          : ' (an attribute — i18n.js only sweeps text nodes, so there is no rescue)')
      );
    }

    assert.deepStrictEqual(
      offenders, [],
      'These strings reach the operator without ever passing through t(), and cannot ' +
      'be rescued by i18n.js:\n  ' + offenders.join('\n  ') +
      '\n\nWrap the string in t() and add it to BOTH catalogs. If a number is glued to ' +
      'words, split them into separate nodes — the sweep only matches a node whose ' +
      'FULL trimmed text is a catalog key, so "312 active products" is unreachable ' +
      'while "312" + "active products" is fine.'
    );

    console.log(
      `PASS: none of the ${strings.length} operator-visible strings across ` +
      `${fragments.length} surfaces is an unreachable literal`
    );
  });
}

function testNoTranslatableStringSharesANodeWithAnEmoji() {
  return renderAllSurfaces().then((fragments) => {
    // The specific defect that made "🛒 Open POS" unreachable by the DOM sweep,
    // and that this file has now shipped twice.
    const offenders = [];
    for (const n of visibleStrings(fragments)) {
      if (n.kind !== 'text') continue;   // only the DOM sweep is defeated this way
      const rendered = asRendered(n.raw);
      if (/\p{Extended_Pictographic}/u.test(rendered)) {
        offenders.push(`[${n.surface}] ${JSON.stringify(rendered)} in ${n.where}`);
      }
    }
    assert.deepStrictEqual(
      offenders, [],
      'These text nodes mix an emoji with translatable words:\n  ' + offenders.join('\n  ') +
      '\n\ni18n.js matches a node only when its FULL trimmed text is a catalog key, so ' +
      'an emoji sharing the node makes the string unreachable by the sweep even when ' +
      'the key exists. Put the emoji in its own aria-hidden span.'
    );
    console.log('PASS: no translatable string shares a text node with an emoji');
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

async function main() {
  testCatalogsRemainInParity();
  testDatesFollowTheActiveLanguage();
  await testEveryKeyPassedToTranslateExistsInBothCatalogs();
  await testNoUntranslatedLiteralReachesTheOperator();
  await testNoTranslatableStringSharesANodeWithAnEmoji();
  console.log('PASS: retail_surface_i18n_test.js');
}

main().catch((err) => {
  console.error('FAIL: retail_surface_i18n_test.js');
  console.error(err && err.message ? err.message : err);
  process.exitCode = 1;
});
