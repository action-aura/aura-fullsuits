/**
 * retail_today_screen_test.js -- Aura Today (the owner's home), rendered.
 *
 * WHY THIS FILE EXISTS
 *
 * frontend/today.js is a screen built from ONE server answer. The server sends
 * figures and codes and no sentences; every sentence is built in the browser
 * with t(). That split is exactly where a screen goes quietly wrong: a string
 * that skipped t() renders English inside an Arabic till, a figure written
 * without `.money` loses tabular digits and RTL isolation, a card that says
 * "this computer only" about shop-wide data (or the other way round) misleads
 * the owner about what they are looking at. None of that throws.
 *
 * WHAT THIS FILE ASSERTS, and how each claim is kept from being vacuous:
 *
 *   1. A cashier causes ZERO requests, and the cashier landing is provably what
 *      ran (a marker on RetailSystem._renderCashierLanding), so "no fetch" is
 *      not true merely because nothing ran. A manager causes exactly ONE.
 *   2. Cards come out in the server's order, each with exactly ONE primary
 *      button and at most three rows. A resolved thing is an "All clear" line,
 *      never a card; the same fixture with the branches card OPEN shows the
 *      card and no clear line (both directions of one decision).
 *   3. "On this computer only" is on the drawers card and the tax card and on no
 *      other card, and the card for something the person may not do carries no
 *      button (a manager cannot connect e-invoicing; no stock.adjust, no
 *      "Review reorders").
 *   4. Every string is translated: rendered with an ARABIC catalogue and
 *      fixture data written in Arabic, no Latin word may survive (only the
 *      brand name). A dropped t() leaves an English word and fails here.
 *      Every key used exists in BOTH catalogues.
 *   5. Money is `.money`, every other figure sits in <bdi>/<time>: after
 *      removing those, no digit and no currency symbol is left in the text.
 *   6. The chart mirrors in RTL (first day on the right) and reads left to
 *      right in LTR, measured on the bar positions.
 *   7. No colour literal, and every var(--token) exists in css/main.css.
 *   8. A render another render superseded writes nothing.
 *
 * Mutation-proven. Each of these fails this file:
 *   - dropping t() from any rendered string                       (check 4)
 *   - dropping a <bdi>/.money from a figure                       (check 5)
 *   - removing the retail.reports check from render()             (check 1)
 *   - drawers scope 'shop' reaching the card unlabelled           (check 3)
 *   - removing the delegation in _renderDashboard                 (check 0)
 *
 * Run: node products/retail/tests/retail_today_screen_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const dom = require('./retail_surface_domlite.js');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const RETAIL_JS = path.join(FRONTEND_DIR, 'subsystem-retail.js');
const TODAY_JS = path.join(FRONTEND_DIR, 'today.js');
const MAIN_CSS = path.join(FRONTEND_DIR, 'css', 'main.css');
const EN = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, 'locales', 'en.json'), 'utf8'));
const AR = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, 'locales', 'ar.json'), 'utf8'));
const ROUTE = '/api/sub/retail/reports/today';

let checks = 0;
const pass = (msg) => { checks += 1; console.log('  ok  ' + msg); };

// ─────────────────────────────────────────────────────────────────────────────
// Fixture: the JSON shape of the plan, with data written in ARABIC so that any
// Latin word left in the rendered text can only have come from the product.
// ─────────────────────────────────────────────────────────────────────────────

function days14() {
  const out = [];
  for (let i = 0; i < 14; i++) {
    const d = new Date(Date.UTC(2026, 8, 22 + i));
    out.push({ day: d.toISOString().slice(0, 10), revenue: i === 13 ? 95.5 : 20 + i * 7.5, transactions: i });
  }
  return out;
}

function fixture() {
  return {
    currency: 'JOD', business_date: '2026-10-05', as_of: '16:12',
    scope: { branch_id: null, branch_name: null, scoped: false, branch_count: 2 },
    hero: {
      revenue: 95.5, gross_sales: 100.5, refunds: 5, transactions: 4, avg_ticket: 23.875,
      yesterday_by_now: 50, change_pct: 91.0, gross_profit: 47.75, margin_pct: 50.0, cost_known: true,
      tenders: [{ method: 'cash', revenue: 55.5, share_pct: 58.1 }, { method: 'card', revenue: 40, share_pct: 41.9 }],
    },
    trend: { days: days14(), average: 62.5, yesterday_by_now: 50 },
    branches: [
      { branch_id: 1, name: 'العبدلي', revenue: 55.5, transactions: 3, share_pct: 58.1, yesterday_by_now: 50, change_pct: 11.0 },
      { branch_id: 2, name: 'إربد', revenue: 40, transactions: 1, share_pct: 41.9, yesterday_by_now: 0, change_pct: null },
    ],
    cards: [
      { kind: 'cheques', state: 'open', scope: 'shop', stake: 120, count: 1, amount: 120, more: 0,
        rows: [{ id: 1, party_name: 'محلات خليل', bank_name: 'البنك العربي', cheque_number: '004512', due_date: '2026-10-07', amount: 120 }],
        extra: { next_week_count: 1, next_week_amount: 200, out_due_count: 1, out_due_amount: 75 } },
      { kind: 'collect', state: 'open', scope: 'shop', stake: 85, count: 2, amount: 85, more: 0,
        rows: [{ id: 11, name: 'هالة ناصر', has_phone: true, amount: 70, days_overdue: 40 }, { id: 12, name: 'رانيا خوري', has_phone: false, amount: 15, days_overdue: 45 }],
        extra: {} },
      { kind: 'restock', state: 'open', scope: 'shop', stake: 26, count: 5, amount: 26, more: 2,
        rows: [
          { product_id: 1, name: 'حليب نيدو', branch_name: 'إربد', on_hand: 3, reorder_level: 10 },
          { product_id: 2, name: 'نسكافيه', branch_name: 'العبدلي', on_hand: 4, reorder_level: 4 },
          { product_id: 3, name: 'زيت دوار الشمس', branch_name: 'العبدلي', on_hand: 1, reorder_level: 6 }],
        extra: { supplier_count: 2, pending_requests: 1 } },
      { kind: 'drawers', state: 'open', scope: 'this_computer', stake: 2.35, count: 2, amount: null, more: 0,
        rows: [
          { session_id: 's1', terminal_short: '1234', ended_at: '2026-10-04T21:05:00', variance_status: 'unverified', variance: -2.35 },
          { session_id: 's2', terminal_short: '5678', ended_at: '2026-10-03T21:00:00', variance_status: 'not_counted', variance: null }],
        extra: { open_count: 1, open_expected_cash: 350, not_counted: 1 } },
      { kind: 'tax', state: 'open', scope: 'this_computer', stake: null, count: 1, amount: null, more: 0,
        rows: [{ key: 'sales_today', n: 4 }, { key: 'reported_today', n: 0 }], extra: { reason: 'not_connected' } },
    ],
    needs_count: 5,
    all_clear: [{ kind: 'branches', detail: { trading: 2 } }],
    aura_did: [
      { kind: 'reorder_suggested', count: 7, last_at: '11:48', scope: 'shop' },
      { kind: 'backup_made', count: 1, last_at: '03:00', scope: 'this_computer' },
    ],
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Harness: the REAL subsystem-retail.js and today.js in one sandbox. Only the
// transport (fetch) and the DOM are replaced.
// ─────────────────────────────────────────────────────────────────────────────

function makeEl(id) {
  return {
    id, innerHTML: '', textContent: '', style: {}, children: [],
    getAttribute() { return null; }, setAttribute() {}, appendChild() {}, remove() {},
    querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
  };
}

function load(opts) {
  const o = Object.assign({ caps: ['retail.reports', 'retail.stock.adjust'], role: 'admin', lang: 'en', dir: 'ltr', answer: fixture(), delay: null }, opts || {});
  const els = Object.create(null);
  const getEl = (id) => (els[id] || (els[id] = makeEl(id)));
  const calls = [];
  const headStyles = [];
  const cashierMarker = { ran: 0 };

  const arabic = o.lang === 'ar';
  const missing = [];
  const used = new Set();
  const sandbox = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    t: (s) => {
      // RetailSystem._money passes the currency symbol through t() itself; that
      // is the money helper's business (and tested elsewhere), not a Today string.
      if (s === 'JD' || s === 'د.أ') return s;
      used.add(s);
      if (!arabic) return s;
      if (!(s in AR)) { missing.push(s); return '§' + s + '§'; }
      return AR[s];
    },
    fetch: (url) => {
      calls.push(String(url));
      // The route's envelope is {status:'success', data:{...}}; a fixture that
      // already carries a `status` (an error answer) is sent as it is.
      const raw = () => (typeof o.answer === 'function' ? o.answer(url) : o.answer);
      const body = () => { const a = raw(); return a && a.status ? a : { status: 'success', data: a }; };
      const respond = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body()) });
      return o.delay ? o.delay().then(respond) : respond();
    },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem() {} },
    URLSearchParams,
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    document: {
      activeElement: null,
      getElementById(id) {
        if (id === 'aura-today-css') return headStyles.length ? headStyles[0] : null;
        if (id === 'ret-styles') return null;
        return getEl(id);
      },
      createElement() { return makeEl('::created'); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      head: { appendChild(el) { headStyles.push(el); } },
      body: { appendChild() {}, classList: { toggle() {} } },
      documentElement: { dir: o.dir, getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
    SubsystemApp: {
      active: 'retail', role: o.role, currentUser: { id: 7, display_name: 'طارق' },
      hasCapability: (c) => o.caps.includes(c),
      _navigate(screen) { calls.push('NAV:' + screen); },
      _identityDisplayName: () => 'طارق',
    },
  };
  if (arabic) sandbox.AuraI18n = { current: 'ar' };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(RETAIL_JS, 'utf8'), sandbox, { filename: RETAIL_JS });
  vm.runInContext(fs.readFileSync(TODAY_JS, 'utf8'), sandbox, { filename: TODAY_JS });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose RetailSystem');
  assert.ok(sandbox.AuraToday && typeof sandbox.AuraToday.render === 'function', 'today.js did not expose AuraToday.render');
  sandbox.RetailSystem._currencySymbol = arabic ? 'د.أ' : 'JD';
  sandbox.RetailSystem._currencyDecimals = 3;
  sandbox.RetailSystem._renderCashierLanding = () => { cashierMarker.ran += 1; };
  return { sandbox, RS: sandbox.RetailSystem, Today: sandbox.AuraToday, els, getEl, calls, headStyles, used, missing, cashierMarker };
}

async function renderOwner(opts) {
  const h = load(opts);
  const c = makeEl('content');
  const token = h.RS._beginRender();
  await h.Today.render(c, token);
  const body = h.getEl('td-body').innerHTML;
  const head = h.getEl('td-head').innerHTML;
  return Object.assign(h, { c, body, head, html: head + body });
}

const cardsOf = (html) => html.split('<article ').slice(1).map((chunk) => {
  const kind = (/data-kind="([^"]+)"/.exec(chunk) || [])[1];
  return { kind, html: chunk };
});
const count = (s, re) => (s.match(re) || []).length;

// ─────────────────────────────────────────────────────────────────────────────
// 0. The home screen really is this file: _renderDashboard delegates.
// ─────────────────────────────────────────────────────────────────────────────

async function testDashboardDelegatesToToday() {
  const h = load({});
  const c = makeEl('content');
  await h.RS._renderDashboard(c);
  assert.ok(h.calls.includes(ROUTE), 'RetailSystem._renderDashboard did not call ' + ROUTE +
    ' (the delegation to AuraToday.render is missing); calls: ' + JSON.stringify(h.calls));
  assert.ok(!h.calls.some((u) => /dashboard\/stats/.test(u)), 'the old dashboard still fetched /dashboard/stats');
  assert.ok(/td-card/.test(h.getEl('td-body').innerHTML), 'the delegated render drew no Today cards');
  pass('_renderDashboard hands the owner to AuraToday (one /reports/today call, no /dashboard/stats)');
}

// ─────────────────────────────────────────────────────────────────────────────
// 1. Capability: cashier no fetch (and provably the landing ran), manager one.
// ─────────────────────────────────────────────────────────────────────────────

async function testCashierMakesNoRequest() {
  const h = load({ caps: [] });
  const c = makeEl('content');
  await h.Today.render(c, h.RS._beginRender());
  assert.deepStrictEqual(h.calls, [], 'a person without retail.reports caused requests: ' + h.calls.join(', '));
  assert.strictEqual(h.cashierMarker.ran, 1,
    'the cashier landing did not run, so "no request" proves nothing (render() returned without doing the cashier thing)');
  assert.strictEqual(h.getEl('td-body').innerHTML, '', 'a cashier got Today markup');
  pass('a cashier: zero requests, cashier landing ran, no Today markup');
}

async function testManagerMakesExactlyOneRequest() {
  const h = await renderOwner({ caps: ['retail.reports'], role: 'manager' });
  assert.deepStrictEqual(h.calls, [ROUTE], 'a manager must cause exactly one request: ' + JSON.stringify(h.calls));
  assert.ok(/td-card/.test(h.body), 'the manager got no Today cards');
  pass('a manager: exactly one /reports/today request, screen drawn');
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. Cards: server order, one primary each, <=3 rows; resolved = line, not card.
// ─────────────────────────────────────────────────────────────────────────────

async function testCardsOrderPrimaryAndRows() {
  const h = await renderOwner({});
  const cards = cardsOf(h.body).filter((x) => x.kind && x.kind !== 'all_clear');
  assert.deepStrictEqual(cards.map((x) => x.kind), ['cheques', 'collect', 'restock', 'drawers', 'tax'], 'cards are not in the server\'s order');
  for (const card of cards) {
    assert.strictEqual(count(card.html, /ret-btn-primary/g), 1, `card ${card.kind}: expected exactly one primary button`);
    assert.ok(count(card.html, /<li class="td-row">/g) <= 3, `card ${card.kind}: more than three rows`);
  }
  assert.strictEqual(count(cards.find((x) => x.kind === 'restock').html, /<li class="td-row">/g), 3, 'restock rows: fixture has 3 and the card drew a different number (anti-vacuity)');
  assert.ok(/and <bdi dir="ltr">2<\/bdi> more/.test(cards.find((x) => x.kind === 'restock').html), 'the "more" count was not drawn');
  pass('five cards in server order, one primary each, three rows at most, "and 2 more" shown');
}

async function testResolvedIsALineAndOpenIsACard() {
  const resolved = await renderOwner({});
  const resolvedCards = cardsOf(resolved.body);
  assert.ok(!resolvedCards.some((x) => x.kind === 'branches'), 'a resolved branches thing was drawn as a card');
  const clear = resolvedCards.find((x) => x.kind === 'all_clear');
  assert.ok(clear && /All <bdi dir="ltr">2<\/bdi> branches have sold today/.test(clear.html), 'the resolved thing is not an "All clear" line');

  const data = fixture();
  data.cards.push({ kind: 'branches', state: 'open', scope: 'shop', stake: null, count: 1, amount: null, more: 0,
    rows: [{ branch_id: 2, name: 'إربد', yesterday_by_now: 40 }], extra: {} });
  data.all_clear = [];
  data.needs_count = 6;
  const open = await renderOwner({ answer: data });
  const openCards = cardsOf(open.body);
  assert.ok(openCards.some((x) => x.kind === 'branches'), 'the open branches thing is not a card');
  assert.ok(!/have sold today/.test(open.body), 'an open thing is ALSO claimed fine in an All clear line');
  pass('resolved => "All clear" line and no card; open => card and no clear line (both directions)');
}

async function testEmptyShopSaysSoAndDrawsNoInventedFigures() {
  const data = fixture();
  data.cards = []; data.needs_count = 0;
  data.all_clear = [{ kind: 'collect', detail: { owed: 0 } }, { kind: 'cheques', detail: {} }, { kind: 'restock', detail: {} }, { kind: 'drawers', detail: {} }];
  data.hero = Object.assign({}, data.hero, { revenue: 0, transactions: 0, avg_ticket: null, yesterday_by_now: 0, change_pct: null, gross_profit: 0, margin_pct: null, tenders: [] });
  data.trend = { days: days14().map((d) => Object.assign(d, { revenue: 0 })), average: 0, yesterday_by_now: 0 };
  data.branches = []; data.aura_did = [];
  const h = await renderOwner({ answer: data });
  assert.ok(/Nothing needs you right now/.test(h.body), 'an empty shop did not say that nothing needs the owner');
  assert.strictEqual(count(h.body, /class="td-delta/g), 0, 'a percentage change was drawn with nothing to compare against');
  assert.ok(!/margin/.test(h.body), 'a margin was drawn with no base');
  assert.ok(!/td-did/.test(h.body), 'an empty "What Aura did" section was drawn');
  assert.strictEqual(count(h.body, /<li class="td-row"><svg/g), 4, 'the four calm lines were not all drawn');
  pass('an empty shop: calm lines, no percentage, no margin, no empty did-section');
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. Scope label and who-may-do-what.
// ─────────────────────────────────────────────────────────────────────────────

async function testThisComputerLabelOnExactlyDrawersAndTax() {
  const h = await renderOwner({});
  const labelled = cardsOf(h.body).filter((x) => x.kind && x.kind !== 'all_clear' && /On this computer only/.test(x.html)).map((x) => x.kind);
  assert.deepStrictEqual(labelled.sort(), ['drawers', 'tax'], '"On this computer only" must be on the drawers and tax cards and nowhere else; saw ' + labelled.join(','));
  pass('"On this computer only" is on drawers and tax, and on no other card');
}

async function testButtonsFollowWhatThePersonMayDo() {
  const owner = await renderOwner({});
  const ownerTax = cardsOf(owner.body).find((x) => x.kind === 'tax');
  assert.ok(/AuraToday\.act\('tax'\)/.test(ownerTax.html), 'the owner has no way to open e-invoicing');

  const mgr = await renderOwner({ caps: ['retail.reports'], role: 'manager' });
  const mgrCards = cardsOf(mgr.body);
  const mgrTax = mgrCards.find((x) => x.kind === 'tax');
  assert.ok(!/ret-btn-primary/.test(mgrTax.html), 'a manager was offered a button to connect e-invoicing');
  assert.ok(/Only the owner can connect this\./.test(mgrTax.html), 'a manager got no explanation for the missing button');
  const mgrRestock = mgrCards.find((x) => x.kind === 'restock');
  assert.ok(!/AuraToday\.act\('restock'\)/.test(mgrRestock.html), 'a person without stock.adjust was offered "Review reorders"');
  assert.ok(/AuraToday\.act\('stock'\)/.test(mgrRestock.html), 'the restock card lost its "See all" way in');
  pass('owner can open e-invoicing; a manager gets the reason and no button; no stock.adjust => no "Review reorders"');
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. Translation: Arabic catalogue + Arabic data => no Latin word survives.
// ─────────────────────────────────────────────────────────────────────────────

const visibleText = (html) => html
  .replace(/<svg[\s\S]*?<\/svg>/g, ' ')
  .replace(/<table class="td-sr">[\s\S]*?<\/table>/g, ' ')
  .replace(/<[^>]*>/g, ' ')
  .replace(/&[a-z#0-9]+;/g, ' ');

async function testNoEnglishLeftInArabic() {
  const h = await renderOwner({ lang: 'ar', dir: 'rtl' });
  assert.deepStrictEqual(h.missing, [], 'strings sent to t() that the Arabic catalogue lacks: ' + h.missing.join(' | '));
  const text = visibleText(h.html).replace(/Aura/g, '');
  const latin = text.match(/[A-Za-z]{2,}/g) || [];
  assert.deepStrictEqual(latin, [], 'Latin words survived in the Arabic render (a string that skipped t()?): ' + latin.join(', '));
  assert.ok(text.length > 400, 'the Arabic render is suspiciously short; the scan is checking nothing');
  assert.ok(count(h.html, /<article /g) >= 6, 'the Arabic fixture did not draw the cards the scan is meant to cover');
  pass('Arabic render: every string translated, no Latin word left (' + text.length + ' chars scanned)');
}

async function testEveryKeyIsInBothCatalogues() {
  const used = new Set();
  for (const lang of ['en', 'ar']) {
    const h = await renderOwner({ lang, dir: lang === 'ar' ? 'rtl' : 'ltr' });
    h.used.forEach((k) => used.add(k));
  }
  // Variants the fixture does not draw: singular forms and the other reasons.
  const extra = ['Good morning', 'Good evening', 'Good morning, {name}', 'Good evening, {name}', 'Today so far · {branch}',
    'E-invoicing is switched off', 'E-invoice sending is paused', 'E-invoices are waiting to be sent', 'Some e-invoices were rejected by the tax office',
    '{count} customer owes {amount} for more than 30 days', '{count} drawer needs a check', 'From {count} supplier', '{n} sale', 'Emails sent: {count}',
    'WhatsApp messages sent: {count}', 'E-invoices prepared for the tax office: {count}', 'Could not load Today. Check the connection and try again.'];
  extra.forEach((k) => used.add(k));
  const notEn = [...used].filter((k) => !(k in EN));
  const notAr = [...used].filter((k) => !(k in AR));
  assert.deepStrictEqual(notEn, [], 'not in en.json: ' + notEn.join(' | '));
  assert.deepStrictEqual(notAr, [], 'not in ar.json: ' + notAr.join(' | '));
  assert.ok(used.size > 60, 'only ' + used.size + ' keys were exercised; the sweep is not covering the screen');
  pass(`all ${used.size} strings the screen uses are in both catalogues`);
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. Figures: money is .money; other numbers are isolated.
// ─────────────────────────────────────────────────────────────────────────────

async function testMoneyAndNumbersAreIsolated() {
  for (const lang of ['en', 'ar']) {
    const h = await renderOwner({ lang, dir: lang === 'ar' ? 'rtl' : 'ltr' });
    const money = count(h.html, /<span class="money[ "]/g);
    assert.ok(money >= 14, `${lang}: only ${money} .money spans; the figures are not going through RetailSystem._money`);
    const stripped = h.html
      .replace(/<svg[\s\S]*?<\/svg>/g, ' ')
      .replace(/<span class="money[^>]*>[\s\S]*?<\/span>/g, ' ')
      .replace(/<bdi[^>]*>[\s\S]*?<\/bdi>/g, ' ')
      .replace(/<time[^>]*>[\s\S]*?<\/time>/g, ' ')
      .replace(/onclick="[^"]*"/g, ' ').replace(/style="[^"]*"/g, ' ')
      .replace(/<[^>]*>/g, ' ')
      // The only digits that are part of the WORDING itself: "last 14 days" and
      // "more than 30 days" (the same thresholds the server uses). Anything else
      // numeric must be a figure and so must be isolated.
      .replace(/\b(14|30)(?= days| يومًا)/g, ' ');
    assert.deepStrictEqual(stripped.match(/[0-9]+/g) || [], [], `${lang}: digits outside .money / <bdi> / <time>: ` + (stripped.match(/.{0,20}[0-9]+.{0,10}/g) || []).join(' | '));
    // The chart's unit label ("JD per day") names the currency on purpose; it
    // carries no figure. Any other bare symbol is a figure that skipped .money.
    const noUnit = stripped.replace(/(JD|د\.أ)\s+(per day|يوميًا)/g, ' ');
    assert.ok(!/JD|د\.أ/.test(noUnit), `${lang}: a currency symbol outside a .money span`);
  }
  pass('money in .money, every other figure in <bdi>/<time>, in English and Arabic');
}

// ─────────────────────────────────────────────────────────────────────────────
// 6. The chart mirrors in RTL.
// ─────────────────────────────────────────────────────────────────────────────

const barXs = (html) => [...html.matchAll(/<rect class="td-bar(?:-now)?" x="([0-9.]+)"/g)].map((m) => parseFloat(m[1]));

async function testChartMirrorsInRtl() {
  const ltr = barXs((await renderOwner({ dir: 'ltr' })).body);
  const rtl = barXs((await renderOwner({ lang: 'ar', dir: 'rtl' })).body);
  assert.strictEqual(ltr.length, 14, 'LTR chart did not draw 14 bars');
  assert.strictEqual(rtl.length, 14, 'RTL chart did not draw 14 bars');
  assert.ok(ltr[0] < ltr[13], 'LTR: the first day must be on the left (today on the right)');
  assert.ok(rtl[0] > rtl[13], 'RTL: the first day must be on the right (today on the left)');
  const rtlHtml = (await renderOwner({ lang: 'ar', dir: 'rtl' })).body;
  assert.ok(/class="td-ylab"[^>]*text-anchor="start"/.test(rtlHtml), 'RTL: the value axis did not move to the right edge');
  assert.ok(/<table class="td-sr">/.test(rtlHtml) && /<caption>/.test(rtlHtml), 'the chart has no hidden table carrying its figures');
  pass('chart: LTR first bar left of today, RTL first bar right of today, hidden table present');
}

// ─────────────────────────────────────────────────────────────────────────────
// 7. Stylesheet: tokens only.
// ─────────────────────────────────────────────────────────────────────────────

async function testStylesheetIsTokensOnly() {
  const h = await renderOwner({});
  const css = h.headStyles.map((s) => s.textContent).join('\n');
  assert.ok(css.length > 3000, 'the injected stylesheet is suspiciously small');
  assert.deepStrictEqual(css.match(/#[0-9a-fA-F]{3,8}\b/g) || [], [], 'hex colour literals in the Today stylesheet');
  assert.deepStrictEqual(css.match(/\brgba?\s*\(/g) || [], [], 'rgb()/rgba() literals in the Today stylesheet');
  assert.deepStrictEqual(css.match(/:\s*(white|black|red|green|orange|yellow|silver|gray|grey)\b/g) || [], [], 'named colours in the Today stylesheet');
  const tokens = dom.parseTokens(fs.readFileSync(MAIN_CSS, 'utf8'));
  const used = [...new Set((css.match(/var\((--[a-z0-9-]+)/g) || []).map((m) => m.slice(4)))];
  assert.ok(used.length >= 40, 'only ' + used.length + ' tokens referenced');
  const missing = used.filter((n) => !(n in tokens));
  assert.deepStrictEqual(missing, [], 'tokens not defined in css/main.css: ' + missing.join(', '));
  pass(`the Today stylesheet is ${used.length} real tokens and no colour literal`);
}

// ─────────────────────────────────────────────────────────────────────────────
// 8. Stale render, and an honest failure.
// ─────────────────────────────────────────────────────────────────────────────

async function testStaleRenderWritesNothing() {
  let release;
  const gate = new Promise((r) => { release = r; });
  const h = load({ delay: () => gate });
  const c = makeEl('content');
  const first = h.Today.render(c, h.RS._beginRender());
  h.RS._beginRender();                       // the user navigated away / back
  release();
  await first;
  // The frame (with its "Loading..." line) went into the screen container; the
  // figures go into #td-body / #td-head. A superseded render must leave both
  // of those untouched. The container check proves the render really started.
  assert.ok(/td-loading/.test(c.innerHTML), 'anti-vacuity: the superseded render never started (no loading frame)');
  assert.strictEqual(h.getEl('td-body').innerHTML, '', 'a superseded render painted its (stale) figures into #td-body');
  assert.strictEqual(h.getEl('td-head').innerHTML, '', 'a superseded render painted its (stale) header');

  const fresh = load({});
  const c2 = makeEl('content');
  await fresh.Today.render(c2, fresh.RS._beginRender());
  assert.ok(/td-card/.test(fresh.getEl('td-body').innerHTML), 'control: a current render did not paint');
  pass('a superseded render writes nothing; the current one does');
}

async function testFailureIsShownNotHidden() {
  const h = await renderOwner({ answer: { status: 'error', message: 'boom' } });
  assert.ok(/role="alert"/.test(h.body) && /Could not load Today/.test(h.body), 'a failed load was not shown as an error');
  assert.ok(!/td-card/.test(h.body), 'a failed load still drew cards');
  pass('a failed load shows an error with a retry, never an empty or stale screen');
}

(async () => {
  try {
    await testDashboardDelegatesToToday();
    await testCashierMakesNoRequest();
    await testManagerMakesExactlyOneRequest();
    await testCardsOrderPrimaryAndRows();
    await testResolvedIsALineAndOpenIsACard();
    await testEmptyShopSaysSoAndDrawsNoInventedFigures();
    await testThisComputerLabelOnExactlyDrawersAndTax();
    await testButtonsFollowWhatThePersonMayDo();
    await testNoEnglishLeftInArabic();
    await testEveryKeyIsInBothCatalogues();
    await testMoneyAndNumbersAreIsolated();
    await testChartMirrorsInRtl();
    await testStylesheetIsTokensOnly();
    await testStaleRenderWritesNothing();
    await testFailureIsShownNotHidden();
    console.log(`\nPASS: retail_today_screen_test.js (${checks} checks)`);
  } catch (e) {
    console.error('\nFAIL: ' + (e && e.stack || e));
    process.exit(1);
  }
})();
