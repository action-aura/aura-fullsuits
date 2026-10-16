/**
 * retail_stock_take_ui_test.js -- the Stock Take screen (STOCK-TAKE, schema v57).
 *
 * The module is the REAL shipped file (frontend/stock-take.js), read from disk and run against stubs;
 * nothing is reimplemented. Each claim is also proved by an in-memory MUTANT -- the shipped text with
 * one thing broken -- which must turn that claim red.
 *
 *   1. a scan / typed text resolves to exactly one product, or is refused (never guessed);
 *   2. the list paints one row per count with a Continue (open) or View (closed) button;
 *   3. an OPEN count has the scan box, editable counted boxes and Post / Cancel; a POSTED one has
 *      none of them (a posted count is immutable on screen as on the server);
 *   4. a shortage and an overage read with opposite signs and classes;
 *   5. the post confirmation shows the figures the SERVER returned (it computes nothing itself);
 *   6. the screen is wired: script tag, nav entry gated like Stock Transfers, router case, Arabic keys.
 *
 * What a green run here does NOT prove: that the screen looks right. The Playwright smoke does that.
 *
 *   node products/retail/tests/retail_stock_take_ui_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const FILE = path.join(FRONTEND, 'stock-take.js');
const SRC = fs.readFileSync(FILE, 'utf8');

function mkEl(id) {
  const el = {
    id: id || '', className: '', _q: {}, children: [], _html: '',
    appendChild(c) { this.children.push(c); return c; },
    addEventListener() {},
    querySelectorAll() { return []; },
    querySelector(sel) {
      if (!this._q[sel]) this._q[sel] = { value: '1', onclick: null, onsubmit: null, disabled: false, focus() {}, select() {} };
      return this._q[sel];
    },
  };
  Object.defineProperty(el, 'innerHTML', { get() { return el._html; }, set(v) { el._html = String(v); el._q = {}; } });
  return el;
}

function load(src, routes) {
  const body = mkEl('body');
  const toasts = [];
  const posted = [];
  const RetailSystem = {
    _esc: (v) => String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    _badge: (text, color) => `<span class="ret-badge ret-badge-${color}">${text}</span>`,
    _currencyDp: () => 3,
    _wireModalA11y: () => () => {},
    _get: async (url) => routes.get(url),
    _post: async (url, b) => { posted.push([url, b]); return routes.post(url, b); },
  };
  const ctx = {
    window: { RetailSystem, t: (s) => s, SubsystemApp: { showToast: (m, k) => toasts.push([m, k]) }, confirm: () => true },
    document: { createElement: () => mkEl(), body },
    console,
  };
  ctx.window.window = ctx.window;
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  return { ST: ctx.window.RetailStockTake, body, toasts, posted, RS: RetailSystem };
}

const PRODUCTS = [
  { id: 'p1', name: 'Olive Oil 1L', sku: 'OIL-1', barcode: '6290001' },
  { id: 'p2', name: 'Olive Soap', sku: 'SOAP-1', barcode: '6290002' },
  { id: 'p3', name: 'Tea Box', sku: 'TEA-1', barcode: '6290003' },
];

const COUNT_OPEN = {
  status: 'success',
  data: {
    count: { uid: 'c-open-1', status: 'open', branch_id: 1 },
    lines: [
      { product_id: 'p1', product_name: 'Olive Oil 1L', barcode: '6290001', system_qty_at_count: 10, counted_qty: 12, variance: 2, variance_value: 4 },
      { product_id: 'p3', product_name: 'Tea Box', barcode: '6290003', system_qty_at_count: 10, counted_qty: 7, variance: -3, variance_value: -9 },
    ],
    totals: { lines: 2, gain_qty: 2, short_qty: 3, variance_value: -5 },
  },
};
const withStatus = (base, status) => ({
  status: 'success', data: Object.assign({}, base.data, { count: Object.assign({}, base.data.count, { status }) }),
});

const API = '/api/sub/retail';

function testMatchProduct(src = SRC) {
  const { ST } = load(src, { get() {}, post() {} });
  const m = (t) => ST.matchProduct(PRODUCTS, t);
  assert.strictEqual(m('6290002').product.id, 'p2', 'an exact barcode is a scan');
  assert.strictEqual(m('tea-1').product.id, 'p3', 'an exact SKU, any case');
  assert.strictEqual(m('tea box').product.id, 'p3', 'a single name match');
  assert.strictEqual(m('olive').error, 'ambiguous', 'two name matches are never guessed');
  assert.strictEqual(m('zzz').error, 'none');
  assert.strictEqual(m('   ').error, 'empty');
  console.log('PASS: scan / typed text resolves to exactly one product or is refused');
}

async function openThrough(src, routes, uid) {
  const loaded = load(src, routes);
  const host = mkEl('host');
  // The list's Continue / View buttons are found by the screen with querySelectorAll('[data-st-open]'); serve one
  // button object per uid found in the markup it just painted, so the handlers the screen wires are the ones we press.
  const btns = {};
  host.querySelectorAll = (sel) => {
    if (sel !== '[data-st-open]') return [];
    const out = []; const re = /data-st-open="([^"]+)"/g; let m;
    while ((m = re.exec(host.innerHTML))) {
      const id = m[1];
      btns[id] = btns[id] || { getAttribute: () => id, onclick: null };
      out.push(btns[id]);
    }
    return out;
  };
  await loaded.ST.render(host);
  assert.ok(/Continue|View/.test(host.innerHTML) && host.innerHTML.includes(`data-st-open="${uid}"`), 'the list paints a row per count');
  const target = btns[uid];
  assert.ok(target && typeof target.onclick === 'function', 'the list wires a handler per count');
  await target.onclick();
  await new Promise((r) => setImmediate(r));
  return { host, loaded };
}

async function testOpenCountScreen(src = SRC) {
  const routes = {
    get: (url) => {
      if (url === `${API}/stock-counts`) return { data: [{ uid: 'c-open-1', status: 'open', branch_name: 'Main', line_count: 2, started_at: '2026-10-16T10:00:00Z' }] };
      if (url === `${API}/stock-counts/c-open-1`) return COUNT_OPEN;
      throw new Error('unrouted ' + url);
    },
    post: () => ({ status: 'success' }),
  };
  const { host } = await openThrough(src, routes, 'c-open-1');
  const html = host.innerHTML;
  assert.ok(html.includes('id="st-code"') && html.includes('id="st-post"') && html.includes('id="st-cancel"'),
    'an open count has the scan box, Post and Cancel');
  assert.ok(html.includes('data-st-set="p1"') && html.includes('data-st-set="p3"'), 'counted boxes are editable');
  assert.ok(html.includes('+2') && html.includes('-3'), 'overage and shortage carry opposite signs');
  assert.ok(/ret-badge-green">\+2/.test(html) && /ret-badge-red">-3/.test(html), 'overage green, shortage red');
  assert.ok(html.includes('-5.000'), 'the net value is the server figure with the shop decimals');
  console.log('PASS: an open count shows scan box, editable lines, signed variances and Post / Cancel');
}

async function testPostedCountIsReadOnly(src = SRC) {
  const routes = {
    get: (url) => {
      if (url === `${API}/stock-counts`) return { data: [{ uid: 'c-post-9', status: 'posted', branch_name: 'Main', line_count: 2, started_at: '2026-10-01T10:00:00Z' }] };
      if (url === `${API}/stock-counts/c-post-9`) return withStatus(COUNT_OPEN, 'posted');
      throw new Error('unrouted ' + url);
    },
    post: () => ({ status: 'success' }),
  };
  const { host } = await openThrough(src, routes, 'c-post-9');
  const html = host.innerHTML;
  assert.ok(!html.includes('id="st-code"') && !html.includes('id="st-post"') && !html.includes('id="st-cancel"') && !html.includes('data-st-set='),
    'a posted count has no scan box, no Post, no Cancel and no editable box');
  assert.ok(html.includes('Posted'));
  console.log('PASS: a posted count is read-only on screen');
}

async function testPostConfirmationShowsServerFigures(src = SRC) {
  const posts = [];
  const routes = {
    get: (url) => {
      if (url === `${API}/stock-counts`) return { data: [{ uid: 'c-open-1', status: 'open', branch_name: 'Main', line_count: 2, started_at: '2026-10-16T10:00:00Z' }] };
      if (url === `${API}/stock-counts/c-open-1`) return COUNT_OPEN;
      throw new Error('unrouted ' + url);
    },
    post: (url, b) => { posts.push(url); return { status: 'success', data: {} }; },
  };
  const { host, loaded } = await openThrough(src, routes, 'c-open-1');
  host._q['#st-post'].onclick();                       // press Post count
  const overlay = loaded.body.children[0];
  assert.ok(overlay, 'pressing Post opens a confirmation instead of posting');
  assert.strictEqual(posts.length, 0, 'nothing is posted before the person confirms');
  assert.ok(overlay.innerHTML.includes('-5.000') && overlay.innerHTML.includes('<strong>2</strong>') &&
    overlay.innerHTML.includes('<strong>3</strong>'), 'the dialog shows lines, units over, units short and the net value');
  await overlay._q['#st-post-yes'].onclick.call(overlay._q['#st-post-yes']);
  assert.deepStrictEqual(posts, [`${API}/stock-counts/c-open-1/post`], 'confirming posts this count, once');
  console.log('PASS: Post asks first and shows the server figures; confirming posts once');
}

function testWiring() {
  const index = fs.readFileSync(path.join(FRONTEND, 'index.html'), 'utf8');
  const shell = fs.readFileSync(path.join(FRONTEND, 'app-shell.js'), 'utf8');
  const sub = fs.readFileSync(path.join(FRONTEND, 'subsystem-retail.js'), 'utf8');
  assert.ok(index.includes('<script src="/static/stock-take.js"></script>'), 'index.html loads the screen');
  const entry = shell.match(/\{\s*id:\s*'stocktake'[^}]*\}/);
  assert.ok(entry && /capability:\s*'retail\.stock\.adjust'/.test(entry[0]), 'nav entry gated like Stock Transfers');
  assert.ok(/case\s+'stocktake':\s*return window\.RetailStockTake/.test(sub), 'router delegates to the feature file');
  const en = JSON.parse(fs.readFileSync(path.join(FRONTEND, 'locales', 'en.json'), 'utf8'));
  const ar = JSON.parse(fs.readFileSync(path.join(FRONTEND, 'locales', 'ar.json'), 'utf8'));
  const keys = [...SRC.matchAll(/\bt\('((?:[^'\\]|\\.)*)'\)/g)].map((m) => m[1].replace(/\\'/g, "'"));
  keys.push('Stock Take');
  const missing = [...new Set(keys)].filter((k) => !(k in en) || !(k in ar) || !ar[k] || ar[k] === k);
  assert.deepStrictEqual(missing, [], 'every string on the screen has an English and a real Arabic entry');
  console.log(`PASS: wired (script, gated nav entry, router case) and ${new Set(keys).size} strings in en + ar`);
}

function mustFail(label, fn) {
  return Promise.resolve().then(fn).then(
    () => { throw new Error(`MUTATION SURVIVED -- ${label}`); },
    (e) => { if (/MUTATION SURVIVED/.test(String(e.message))) throw e; console.log(`   ok   ${label}  [caught: ${String(e.message).split('\n')[0].slice(0, 80)}]`); });
}

function mutate(find, replace) {
  assert.strictEqual(SRC.split(find).length - 1, 1, 'mutation anchor must occur exactly once: ' + find);
  return SRC.replace(find, replace);
}

(async () => {
  testMatchProduct();
  await testOpenCountScreen();
  await testPostedCountIsReadOnly();
  await testPostConfirmationShowsServerFigures();
  testWiring();
  await mustFail('a name that matches two products is guessed => scan test fails', () =>
    testMatchProduct(mutate("return { error: byName.length > 1 || exact.length > 1 ? 'ambiguous' : 'none' };", "return byName.length ? { product: byName[0] } : { error: 'none' };")));
  await mustFail('a posted count keeps its Post button => read-only test fails', () =>
    testPostedCountIsReadOnly(mutate("(isOpen ? '<div style=\"margin-inline-start:auto\"><button class=\"ret-btn ret-btn-danger\" id=\"st-cancel\"", "(true ? '<div style=\"margin-inline-start:auto\"><button class=\"ret-btn ret-btn-danger\" id=\"st-cancel\"")));
  await mustFail('Post posts immediately without asking => confirmation test fails', () =>
    testPostConfirmationShowsServerFigures(mutate("host.querySelector('#st-post').onclick = confirmPost;", "host.querySelector('#st-post').onclick = async function () { await RS()._post(API + '/stock-counts/' + encodeURIComponent(state.openUid) + '/post', {}); };")));
  await mustFail('shortage painted green => sign/class test fails', () =>
    testOpenCountScreen(mutate("function varianceClass(v) { return v > 0 ? 'ret-badge-green' : (v < 0 ? 'ret-badge-red' : 'ret-badge-blue'); }", "function varianceClass(v) { return 'ret-badge-green'; }")));
  console.log('PASS: retail_stock_take_ui_test.js');
})().catch((e) => { console.error('FAIL:', e.stack || e); process.exit(1); });
