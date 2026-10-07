/**
 * retail_stock_adjust_reason_test.js -- REHEARSAL-1007 B3-UI.
 *
 * The "Adjust stock" dialog sends `reason` = 'added' | 'count' so the books
 * can tell NEW STOCK THE OWNER PUT IN (Dr Inventory / Cr Owner's capital, no
 * profit) from a COUNT CORRECTION (gain or loss). Before this the dialog sent
 * a free-text label and every increase landed in the stock-count account as
 * profit.
 *
 * What is pinned:
 *   1. an increase offers "New stock added" first (the default) and "Stock count
 *      correction"; a reduction offers ONLY "Count correction / loss";
 *   2. the POST body carries reason 'added' for an increase left on its
 *      default, 'count' when the owner picks the correction, and ALWAYS 'count'
 *      for a reduction -- even if the select were stale and still said 'added'
 *      (the allow-half: a reduction must never be sent as owner money).
 *   Mutants prove (1) and (2) each go red.
 *
 *   node products/retail/tests/retail_stock_adjust_reason_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC_PATH = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');
const SOURCE = fs.readFileSync(SRC_PATH, 'utf8');

function stub(o) {
  return Object.assign({
    innerHTML: '', textContent: '', value: '', style: {}, dataset: {}, id: '', disabled: false,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {}, remove() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    addEventListener() {}, removeEventListener() {}, focus() {}, closest() { return null; },
  }, o);
}

function load(source) {
  const els = {};
  const posts = [];
  const sandbox = {
    console, t: (s) => s,
    fetch: () => Promise.reject(new Error('no network')),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval() {}, clearInterval() {},
    document: {
      activeElement: null,
      getElementById: (id) => (els[id] = els[id] || stub({ id })),
      createElement() { return stub(); },
      querySelector: () => stub(), querySelectorAll: () => [],
      head: { appendChild() {} }, body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {}, removeEventListener() {},
    },
    SubsystemApp: { active: 'retail', showToast() {}, checkAuthAndSetup() {}, hasCapability: () => true },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: SRC_PATH });
  const R = sandbox.RetailSystem;
  assert.ok(R, 'RetailSystem not exposed');
  R._wireModalA11y = () => () => {};
  R._post = async (url, body) => { posts.push({ url, body }); return { status: 'success', new_stock: 1 }; };
  R._loadProducts = () => {};
  R._closeModalOverlay = () => {};
  return { R, els, posts };
}

const values = (html) => Array.from(html.matchAll(/value="([^"]+)"/g)).map((m) => m[1]);

async function suite(source, report) {
  await report('an increase offers added (default) + count; a reduction offers only count', async () => {
    const { R } = load(source);
    const up = R._stockAdjustReasonOptions(5, '');
    assert.deepStrictEqual(values(up), ['added', 'count']);
    assert.ok(/value="added" selected/.test(up), 'increase must default to "added"');
    assert.deepStrictEqual(values(R._stockAdjustReasonOptions(-3, 'added')), ['count'],
      'a reduction must never offer owner-money stock, even if "added" was selected before the sign flipped');
    // flipping the sign in the open dialog rebuilds the select
    const { R: R2, els } = load(source);
    els['sa-qty'] = stub({ id: 'sa-qty', value: '-2' });
    els['sa-reason'] = stub({ id: 'sa-reason', value: 'added' });
    R2._syncStockAdjustReasons();
    assert.deepStrictEqual(values(els['sa-reason'].innerHTML), ['count']);
  });

  await report('POST carries reason: added by default, count when chosen', async () => {
    const a = load(source);
    a.els['sa-qty'] = stub({ value: '4' });
    a.els['sa-reason'] = stub({ value: 'added' });
    await a.R._saveStockAdjust('p1');
    assert.strictEqual(a.posts[0].body.reason, 'added');
    assert.strictEqual(a.posts[0].body.quantity, 4);
    const b = load(source);
    b.els['sa-qty'] = stub({ value: '4' });
    b.els['sa-reason'] = stub({ value: 'count' });
    await b.R._saveStockAdjust('p1');
    assert.strictEqual(b.posts[0].body.reason, 'count');
    // no choice readable at all -> the increase default, not the profit path
    const d = load(source);
    d.els['sa-qty'] = stub({ value: '4' });
    d.els['sa-reason'] = stub({ value: '' });
    await d.R._saveStockAdjust('p1');
    assert.strictEqual(d.posts[0].body.reason, 'added');
  });

  await report('a reduction is ALWAYS sent as count, even with a stale "added" select', async () => {
    const c = load(source);
    c.els['sa-qty'] = stub({ value: '-2' });
    c.els['sa-reason'] = stub({ value: 'added' });
    await c.R._saveStockAdjust('p1');
    assert.strictEqual(c.posts[0].body.reason, 'count');
    assert.strictEqual(c.posts[0].body.quantity, -2);
  });
}

(async () => {
  let failed = 0, passed = 0;
  const results = [];
  await suite(SOURCE, async (n, f) => { try { await f(); results.push([n, true]); } catch (e) { results.push([n, false, e]); } });
  for (const [n, ok, e] of results) {
    if (ok) { passed++; console.log('PASS: ' + n); } else { failed++; console.error('FAIL: ' + n + '\n  ' + (e && e.message || e)); }
  }
  const mutants = [
    ['reduction also offers "added"', "const opts = qty < 0\n      ? [['count', countLabel]]", "const opts = qty < 0\n      ? [['added', 'x'], ['count', countLabel]]"],
    ['reduction sent as picked reason', "const reason = qty > 0 ? (picked === 'count' ? 'count' : 'added') : 'count';", "const reason = picked || 'added';"],
    ['increase defaults to count', "(picked === 'count' ? 'count' : 'added')", "(picked === 'added' ? 'added' : 'count')"],
  ];
  for (const [label, from, to] of mutants) {
    assert.ok(SOURCE.includes(from), 'mutant anchor missing: ' + label);
    const r = [];
    await suite(SOURCE.replace(from, to), async (n, f) => { try { await f(); r.push(true); } catch (e) { r.push(false); } });
    const red = r.filter((x) => !x).length;
    if (red > 0) { passed++; console.log(`PASS: mutant "${label}" turns ${red} check(s) red`); }
    else { failed++; console.error('FAIL: MUTANT SURVIVED: ' + label); }
  }
  console.log(`\nretail_stock_adjust_reason_test.js -- ${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
