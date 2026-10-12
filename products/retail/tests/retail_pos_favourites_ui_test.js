/**
 * retail_pos_favourites_ui_test.js -- the till's Favourites page and the
 * quieter product tile (UI-2 item 1, review A #3).
 *
 * The owner pins up to 40 products to a Favourites pill that leads the category
 * rail and is the till's default page when non-empty. The list is ONE
 * retail_settings row (`pos_favourites`) written through the existing
 * owner-only settings route (the server half is retail_pos_favourites_test.py).
 *
 * What is pinned, and why each is not vacuous:
 *   1. PERSISTENCE ACROSS A RELOAD goes through the real _loadPOSData: a fake
 *      server keeps whatever the pin POSTed, a brand-new mount reads it back,
 *      the Favourites pill leads the rail, it is the default page, and the grid
 *      shows the pinned products in PIN ORDER and nothing else.
 *   2. THE CAP: the 40th pin is accepted, the 41st is refused with a message
 *      naming 40, no request is sent, and the list is unchanged. Mutation:
 *      raise the constant and the 41st goes through.
 *   3. WHO CAN PIN (deny + allow): a non-owner gets no star, no request and a
 *      toast; the owner gets the star and the request. A refused save rolls the
 *      screen back to what the server holds.
 *   4. THE TILE STOCK LINE: hidden when stock is normal, "Low stock: N" when at
 *      or under the reorder level, "Out of stock" when out, in the same
 *      renderer, so a regression in either direction shows.
 *   5. THUMBNAIL: an http(s) image_url replaces the monogram; a javascript:
 *      url is ignored (the allow-list is the XSS guard).
 *   6. RAIL OVERFLOW CUE toggles with the scroll geometry, in both directions.
 *   7. The client cap equals the server cap (both read from source).
 *
 * Run: node products/retail/tests/retail_pos_favourites_ui_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');
const BACKEND_FILE = path.join(__dirname, '..', 'backend', 'api', 'retail_api.py');
const SRC = fs.readFileSync(FRONTEND_FILE, 'utf8');

function el(id) {
  const o = {
    id, value: '', textContent: '', hidden: false, style: {}, dataset: {}, attrs: {}, children: [],
    className: '', _cls: new Set(),
    classList: {
      add(c) { o._cls.add(c); }, remove(c) { o._cls.delete(c); },
      toggle(c, f) { if (f === undefined ? !o._cls.has(c) : f) o._cls.add(c); else o._cls.delete(c); },
      contains(c) { return o._cls.has(c); },
    },
    setAttribute(k, v) { o.attrs[k] = v; }, getAttribute(k) { return o.attrs[k] == null ? null : o.attrs[k]; },
    appendChild(c) { o.children.push(c); return c; }, focus() {}, select() {},
    querySelector() { return null; }, closest() { return null; }, addEventListener() {}, remove() {},
    scrollWidth: 0, clientWidth: 0, scrollLeft: 0,
  };
  // Assigning innerHTML replaces children, like the real DOM -- _renderCatBar
  // clears the rail with `bar.innerHTML = ''` before re-adding its pills.
  let html = '';
  Object.defineProperty(o, 'innerHTML', { get() { return html; }, set(v) { html = v; o.children = []; } });
  return o;
}

function load(opts) {
  opts = opts || {};
  const els = Object.create(null);
  const get = (id) => els[id] || (els[id] = el(id));
  const toasts = [];
  const sandbox = {
    console, t: (s) => s,
    fetch: () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: 'success', data: {} }) }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (test)' }, localStorage: { getItem: () => null, setItem() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {}, URLSearchParams,
    document: {
      activeElement: null,
      getElementById: (id) => (id === 'ret-styles' ? el('ret-styles') : get(id)),
      createElement: () => el('::created'), querySelector: () => null, querySelectorAll: () => [],
      head: { appendChild() {} }, body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } }, addEventListener() {},
    },
    SubsystemApp: {
      active: 'retail', role: opts.role === undefined ? 'admin' : opts.role,
      showToast(msg, kind) { toasts.push({ msg, kind }); }, _navigate() {}, hasCapability: () => true,
    },
  };
  sandbox.window = sandbox;
  sandbox.Chart = function ChartStub() { return { destroy() {} }; };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: FRONTEND_FILE });
  const R = sandbox.RetailSystem;
  assert.ok(R && typeof R._toggleFavourite === 'function', 'RetailSystem._toggleFavourite is missing -- Favourites is not in the build');
  R._currencyDecimals = 3; R._currencySymbol = 'JD';
  R._refocusScan = () => {};
  R._onCustomerChange = () => {};
  R._syncDiscountControl = () => {};
  R._broadcastDisplayState = () => {};
  return { R, get, sandbox, toasts, SubsystemApp: sandbox.SubsystemApp };
}

const mk = (id, extra) => Object.assign({
  id, name: 'Product ' + id, sku: 'SKU-' + id, category_id: null, sell_price: 1.5, total_stock: 50,
  reorder_level: 5, unit: 'pc',
}, extra || {});

// A fake server for the endpoints _loadPOSData and the pin POST touch.
function fakeServer(R, products, initial) {
  const server = { pos_favourites: initial || '', posts: [], refuse: false };
  R._get = async (url) => {
    if (url.includes('/products')) return { status: 'success', data: products };
    if (url.includes('/settings/credit')) return { status: 'success', data: { pos_favourites: server.pos_favourites, default_credit_mode: 'none' } };
    if (url.includes('/categories') || url.includes('/customers') || url.includes('/held-sales') || url.includes('/promotions')) return { status: 'success', data: [] };
    if (url.includes('/settings/tax')) return { status: 'success', data: { currency_symbol: 'JD', currency_decimals: 3 } };
    return { status: 'success', data: {} };
  };
  R._post = async (url, body) => {
    server.posts.push({ url, body: JSON.parse(JSON.stringify(body)) });
    if (server.refuse) return { status: 'error', message: 'Only the shop owner can change this.' };
    if (url.includes('/settings/credit')) server.pos_favourites = JSON.stringify(body.pos_favourites);
    return { status: 'success', data: {} };
  };
  return server;
}

const gridHtml = (get) => get('pos-product-grid').innerHTML;
const pillTexts = (get) => get('pos-cats').children.map((b) => (b.textContent || b.innerHTML.replace(/<[^>]+>/g, '')));

async function testPersistenceAcrossReload() {
  const { R, get } = load();
  const products = ['a', 'b', 'c', 'd'].map((i) => mk(i));
  const server = fakeServer(R, products);
  R._categories = []; R._favourites = [];
  R._activeCat = null;
  await R._loadPOSData();
  assert.deepStrictEqual(pillTexts(get), ['All'], 'with nothing pinned the rail is unchanged');
  assert.strictEqual(R._activeCat, null, 'with nothing pinned the default page is All');

  assert.strictEqual(await R._toggleFavourite('c'), true);
  assert.strictEqual(await R._toggleFavourite('a'), true);
  assert.deepStrictEqual(JSON.parse(server.pos_favourites), ['c', 'a'], 'the server holds the pins in pin order');

  // A brand-new mount: nothing remembered client-side.
  R._favourites = []; R._activeCat = null; R._productsById = Object.create(null);
  await R._loadPOSData();
  assert.deepStrictEqual(pillTexts(get).slice(0, 2), ['Favourites', 'All'], 'Favourites leads the rail');
  assert.strictEqual(R._activeCat, R.POS_FAV_CAT, 'Favourites is the default page when non-empty');
  const html = gridHtml(get);
  assert.ok(html.includes('Product c') && html.includes('Product a'), 'pinned products are shown');
  assert.ok(!html.includes('Product b') && !html.includes('Product d'), 'unpinned products are NOT shown on Favourites');
  assert.ok(html.indexOf('Product c') < html.indexOf('Product a'), 'pin order, not catalogue order');

  // While a search is active the page is the SEARCH RESULTS narrowed to the
  // pinned ones (server order = catalogue order), and must still read in pin
  // order -- this is the only path where the sort is doing any work.
  R._posResultsTerm = 'pro'; R._products = products;
  R._renderPOSGrid();
  const searched = gridHtml(get);
  assert.ok(searched.includes('Product c') && searched.includes('Product a') && !searched.includes('Product b'), 'search on Favourites is narrowed to the pins');
  assert.ok(searched.indexOf('Product c') < searched.indexOf('Product a'), 'pin order holds while searching');
  R._posResultsTerm = '';

  // Unpin both: the page falls back to All instead of stranding the cashier on an empty grid.
  await R._toggleFavourite('c'); await R._toggleFavourite('a');
  assert.strictEqual(R._activeCat, null, 'the last unpin on the Favourites page falls back to All');
  assert.deepStrictEqual(pillTexts(get), ['All'], 'the pill disappears with the last pin');
  assert.strictEqual(server.pos_favourites, '[]');
  console.log('PASS: pin/unpin persists across a reload; Favourites leads, is the default, shows pin order only');
}

async function testCapRefusesThe41st() {
  const { R, toasts } = load();
  const products = Array.from({ length: 45 }, (_, i) => mk('p' + i));
  const server = fakeServer(R, products);
  R._favourites = []; R._categories = [];
  for (let i = 0; i < 40; i++) assert.strictEqual(await R._toggleFavourite('p' + i), true, 'pin #' + (i + 1));
  assert.strictEqual(R._favourites.length, 40);
  const postsBefore = server.posts.length;
  const refused = await R._toggleFavourite('p41');
  assert.strictEqual(refused, false, 'the 41st pin must be refused');
  assert.strictEqual(R._favourites.length, 40, 'the list is unchanged');
  assert.strictEqual(server.posts.length, postsBefore, 'a refused pin sends nothing');
  const last = toasts[toasts.length - 1];
  assert.ok(last && last.kind === 'error' && /40/.test(last.msg), 'the refusal names the limit: ' + JSON.stringify(last));
  // unpinning at the cap is still allowed (the deny must not block the way out)
  assert.strictEqual(await R._toggleFavourite('p0'), true);
  assert.strictEqual(R._favourites.length, 39);
  console.log('PASS: 40 accepted, 41st refused with a message and no request; unpin at the cap still works');
}

async function testWhoCanPinAndRollback() {
  const cashier = load({ role: 'cashier' });
  const products = [mk('a'), mk('b')];
  const sCash = fakeServer(cashier.R, products);
  cashier.R._favourites = ['a']; cashier.R._categories = []; cashier.R._products = products;
  cashier.R._renderPOSGrid();
  assert.ok(!gridHtml(cashier.get).includes('pos-card-star'), 'a cashier is not offered the pin star');
  assert.strictEqual(await cashier.R._toggleFavourite('b'), false);
  assert.strictEqual(sCash.posts.length, 0, 'a cashier pin sends no request');
  assert.ok(cashier.toasts.length === 1, 'and says why');

  const owner = load({ role: 'admin' });
  const sOwn = fakeServer(owner.R, products);
  owner.R._favourites = ['a']; owner.R._categories = []; owner.R._products = products;
  owner.R._renderPOSGrid();
  const html = gridHtml(owner.get);
  assert.ok(html.includes('pos-card-star is-pinned'), 'the owner sees a filled star on a pinned tile');
  assert.ok((html.match(/pos-card-star/g) || []).length === 2, 'and a star on every tile');
  assert.ok(/data-pid="a"/.test(html) && /data-pid="b"/.test(html), 'every tile carries its product id for the gestures');

  // a server refusal rolls the optimistic pin back
  sOwn.refuse = true;
  assert.strictEqual(await owner.R._toggleFavourite('b'), false);
  assert.deepStrictEqual(owner.R._favourites, ['a'], 'a refused save leaves the screen as the server holds it');
  console.log('PASS: only the owner can pin (cashier: no star, no request); a refused save rolls back');
}

function testTileStockLine() {
  const { R, get } = load();
  R._categories = []; R._favourites = [];
  const render = (p) => { R._products = [p]; R._syncHealth = null; R._activeCat = null; R._renderPOSGrid(); return gridHtml(get); };
  const normal = render(mk('n', { total_stock: 120 }));
  assert.ok(!/Stock/i.test(normal) && !normal.includes('pos-card-stock'), 'a normal-stock tile shows no stock line: ' + normal);
  const low = render(mk('l', { total_stock: 5, reorder_level: 5 }));
  assert.ok(low.includes('Low stock: 5 pc') && low.includes('is-low'), 'a low tile shows the amber figure: ' + low);
  const out = render(mk('o', { total_stock: 0 }));
  assert.ok(out.includes('Out of stock') && out.includes('is-out'), 'an out tile shows the red word: ' + out);
  const justAbove = render(mk('j', { total_stock: 6, reorder_level: 5 }));
  assert.ok(!justAbove.includes('pos-card-stock'), 'one above the reorder level is still quiet');
  console.log('PASS: stock line hidden when normal, amber when low, red word when out');
}

function testThumbnail() {
  const { R, get } = load();
  R._categories = []; R._favourites = [];
  const render = (p) => { R._products = [p]; R._activeCat = null; R._renderPOSGrid(); return gridHtml(get); };
  const withImg = render(mk('i', { image_url: 'https://cdn.example/x.png' }));
  assert.ok(/<img class="pos-card-thumb" src="https:\/\/cdn\.example\/x\.png"/.test(withImg), withImg);
  assert.ok(!withImg.includes('pos-card-monogram'), 'the photo replaces the monogram');
  const bad = render(mk('j', { image_url: 'javascript:alert(1)' }));
  assert.ok(!bad.includes('<img') && bad.includes('pos-card-monogram'), 'a javascript: url is ignored: ' + bad);
  const none = render(mk('k'));
  assert.ok(none.includes('pos-card-monogram') && !none.includes('<img'), 'no image -> monogram');
  const quote = render(mk('q', { image_url: 'https://x/a.png" onerror="alert(1)' }));
  assert.ok(!/onerror="alert/.test(quote), 'the url is attribute-escaped: ' + quote);
  console.log('PASS: thumbnail replaces the monogram; unsafe urls are ignored and escaped');
}

function testRailOverflowCue() {
  const { R, get } = load();
  const bar = get('pos-cats'), wrap = get('pos-cat-wrap');
  bar.scrollWidth = 900; bar.clientWidth = 400; bar.scrollLeft = 0;
  R._updateCatOverflow();
  assert.ok(wrap.classList.contains('has-more'), 'an overflowing rail shows the cue');
  bar.scrollLeft = 500;                               // scrolled to the end
  R._updateCatOverflow();
  assert.ok(!wrap.classList.contains('has-more'), 'a rail scrolled to its end hides it');
  bar.scrollLeft = -100;                              // RTL scroller (negative), more still to reveal
  R._updateCatOverflow();
  assert.ok(wrap.classList.contains('has-more'), 'RTL (negative scrollLeft) measures distance with abs()');
  bar.scrollWidth = 300; bar.scrollLeft = 0;
  R._updateCatOverflow();
  assert.ok(!wrap.classList.contains('has-more'), 'a rail that fits shows no cue');
  console.log('PASS: the rail overflow cue follows the scroll geometry (LTR + RTL, both directions)');
}

function testClientCapEqualsServerCap() {
  const { R } = load();
  const py = fs.readFileSync(BACKEND_FILE, 'utf8');
  const m = /^POS_FAVOURITES_MAX\s*=\s*(\d+)/m.exec(py);
  assert.ok(m, 'POS_FAVOURITES_MAX not found in retail_api.py');
  assert.strictEqual(R.POS_FAVOURITES_MAX, Number(m[1]), 'client and server favourites caps drifted');
  assert.strictEqual(R.POS_FAVOURITES_MAX, 40, 'the cap is 40 (review A #3)');
  console.log('PASS: client cap == server cap == 40');
}

(async function main() {
  const checks = [
    ['persistence across reload', testPersistenceAcrossReload],
    ['cap', testCapRefusesThe41st],
    ['who can pin / rollback', testWhoCanPinAndRollback],
    ['tile stock line', testTileStockLine],
    ['thumbnail', testThumbnail],
    ['rail overflow cue', testRailOverflowCue],
    ['client cap == server cap', testClientCapEqualsServerCap],
  ];
  let failed = 0;
  for (const [name, fn] of checks) {
    try { await fn(); } catch (e) { failed++; console.log('FAIL: ' + name + '\n      ' + (e && e.stack || e)); }
  }
  if (failed) { console.log(`\nFAIL: retail_pos_favourites_ui_test.js -- ${failed} of ${checks.length} checks failed`); process.exit(1); }
  console.log(`\nPASS: retail_pos_favourites_ui_test.js -- ${checks.length} checks`);
})();
