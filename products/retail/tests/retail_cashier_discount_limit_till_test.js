/**
 * retail_cashier_discount_limit_till_test.js -- the till half of the owner-set
 * cashier discount limit: a session without retail.discount sees a % box capped
 * at the shop's limit, an over-limit discount never reaches the server, a
 * holder of the capability is never limited. Server half:
 * retail_cashier_discount_limit_test.py. Loads the REAL subsystem-retail.js
 * (AURA_SR_SRC points a mutation proof at a broken copy).
 *
 * Run: node products/retail/tests/retail_cashier_discount_limit_till_test.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

// AURA_SR_SRC lets a mutation proof point this same test at a deliberately
// broken copy of the source (see the till keyboard model's mutation proofs);
// unset, it is always the real file.
const SRC_PATH = process.env.AURA_SR_SRC || path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');
const source = fs.readFileSync(SRC_PATH, 'utf8');

function makeStubElement(overrides) {
  return Object.assign({
    id: '', value: '', disabled: false, textContent: '', style: {},
    tagName: 'DIV', isContentEditable: false,
    focus() {},
  }, overrides);
}

/**
 * Loads a fresh RetailSystem from the real source. `activeEl` seeds
 * document.activeElement (the "is the cashier typing somewhere" signal both
 * _onScannerKey and _onPOSShortcut read). Element ids are created lazily and
 * cached, exactly like retail_surface_pos_test.js's own harness, so the same
 * #pos-tendered element instance is read back after a shortcut writes to it.
 */
function loadRetailSystem(opts) {
  const options = opts || {};
  const sandbox = {};
  sandbox.window = sandbox;
  sandbox.navigator = { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' }; // desktop scanner path
  sandbox.t = (s) => s; // i18n.js's global shorthand
  sandbox.console = console;
  sandbox.setTimeout = setTimeout; sandbox.clearTimeout = clearTimeout; // _filterPOS debounces its search
  sandbox.localStorage = { getItem: () => null, setItem: () => {} };
  sandbox.performance = { now: () => sandbox.__clock };
  sandbox.__clock = 10000; // see retail_scanner_input_mode_test.js for why not 0

  const toasts = [];
  const els = Object.create(null);
  const state = { activeEl: options.activeEl || null, focusLog: [] };
  const INPUT_IDS = ['pos-search', 'pos-tendered', 'pos-disc', 'pos-customer'];
  const getEl = (id) => {
    if (!els[id]) {
      els[id] = makeStubElement({
        id,
        tagName: INPUT_IDS.indexOf(id) !== -1 ? (id === 'pos-customer' ? 'SELECT' : 'INPUT') : 'DIV',
        // Records WHO took focus and moves activeElement, so a test can tell
        // "Enter went to the tendered field" from "nothing happened".
        focus() { state.focusLog.push(id); state.activeEl = els[id]; },
        select() { state.selectLog = (state.selectLog || []).concat(id); },
      });
    }
    return els[id];
  };

  const buttons = ['cash', 'card', 'mobile', 'transfer', 'credit', 'voucher'].map((m) => {
    const cls = new Set();
    return {
      dataset: { method: m }, getAttribute: () => null,
      classList: { add: (c) => cls.add(c), remove: (c) => cls.delete(c), toggle: (c, on) => (on ? cls.add(c) : cls.delete(c)), contains: (c) => cls.has(c) },
    };
  });
  sandbox.document = {
    get activeElement() { return state.activeEl; },
    getElementById: getEl,
    // The six tender tiles, so F1-F6 / _pickTender find a real button.
    querySelectorAll: (sel) => (sel === '.pos-pay-btn' ? buttons : []),
    addEventListener: () => {},
  };

  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: SRC_PATH });
  const RetailSystem = sandbox.RetailSystem;
  assert.ok(RetailSystem, 'RetailSystem failed to load from subsystem-retail.js');

  sandbox.SubsystemApp = { active: 'retail', showToast: (msg, type) => { toasts.push({ msg, type }); } };
  RetailSystem._currencyDecimals = 3;
  RetailSystem._section = 'pos';
  RetailSystem._cart = [];
  RetailSystem._currentTotals = { subtotal: 0, discount: 0, tax: 0, total: 0 };
  RetailSystem._paymentMethod = 'cash';
  RetailSystem._pendingQty = null;
  RetailSystem._qtyKeyBuffer = '';
  RetailSystem._qtyKeyLastAt = 0;
  RetailSystem._products = [
    { id: 'p1', name: 'Widget', sell_price: 10, tax_rate: 0, total_stock: 999 },
    { id: 'p2', name: 'Gadget', sell_price: 5,  tax_rate: 0, total_stock: 999 },
  ];

  // Isolate the receipt/rendering pipeline (see file header) -- the cart and
  // checkout logic itself stays real.
  RetailSystem._clearCart = () => {};
  RetailSystem._applyLocalStockDecrement = () => {};
  RetailSystem._showReceipt = () => {};
  RetailSystem._renderCart = () => {};

  const posted = [];
  const dispatchedScans = [];
  RetailSystem._post = async (url, body) => {
    posted.push({ url, body });
    return { status: 'success', data: { lines: [] } };
  };
  RetailSystem._dispatchScan = (code) => { dispatchedScans.push(code); };

  return { RetailSystem, sandbox, els, state, posted, dispatchedScans, getEl, toasts, buttons };
}

/**
 * Fires one keydown through BOTH real handlers in the same order the
 * browser would: capture phase (_onScannerKey) first, bubble phase
 * (_onPOSShortcut) only if capture did not stopPropagation(). This is the
 * exact mechanism that keeps a completed scan from ever reaching the
 * shortcut layer -- see the file header.
 */
function fireKeydown(RetailSystem, key, extra) {
  let stopped = false;
  const evt = Object.assign({
    key,
    ctrlKey: false, metaKey: false, altKey: false,
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { stopped = true; },
  }, extra || {});
  RetailSystem._onScannerKey(evt);
  if (!stopped) RetailSystem._onPOSShortcut(evt);
  return evt;
}

// Advances the mocked scanner clock by `gapMs` and feeds `code` + Enter as a
// keystroke burst through fireKeydown -- a real barcode scan, keystroke by
// keystroke, through BOTH handlers exactly as described above.
function simulateScan(ctx, code, gapMs) {
  const press = (key) => { ctx.sandbox.__clock += gapMs; fireKeydown(ctx.RetailSystem, key); };
  for (const ch of code) press(ch);
  press('Enter');
}

// A single DELIBERATE keystroke, spaced a realistic, unhurried gap (well
// over the scanner's own default 50ms burst window) from whatever came
// before it -- so the wedge listener's own reset-window logic naturally
// treats it as the start of its own thing, not a burst continuation, the
// same way a real cashier pressing one shortcut key at a time would. Tests
// that fire more than one deliberate key in a row (the "3*" multiplier,
// Escape) use this rather than fireKeydown directly; a run of zero-gap
// keydowns is not a real typing speed for anyone, human or scanner.
const HUMAN_GAP_MS = 400;
function pressKey(ctx, key, extra) {
  ctx.sandbox.__clock += HUMAN_GAP_MS;
  return fireKeydown(ctx.RetailSystem, key, extra);
}

// A PHYSICAL key press: `code` is what the keyboard reports, `key` is whatever
// character the active layout made of it (pass an Arabic letter to prove the
// till does not care).
function pressCode(ctx, code, key, extra) {
  return pressKey(ctx, key == null ? '' : key, Object.assign({ code }, extra || {}));
}

const LINE = (id, name, qty, price) => ({ product_id: id, name, quantity: qty, unit_price: price, tax_rate: 0, line_total: qty * price, max_stock: 999 });
function seedCart(ctx, lines) {
  ctx.RetailSystem._cart = lines;
  const total = lines.reduce((a, l) => a + l.line_total, 0);
  ctx.RetailSystem._currentTotals = { subtotal: total, discount: 0, tax: 0, total };
}
const qtyOf = (ctx, id) => { const l = ctx.RetailSystem._cart.find((x) => x.product_id === id); return l ? l.quantity : null; };
const settle = () => new Promise((r) => setImmediate(r));

const cartOf = (ctx, price) => {
  seedCart(ctx, [LINE('p1', 'Widget', 1, price)]);
  ctx.RetailSystem._currentTotals = { subtotal: price, discount: 0, tax: 0, total: price, amountDue: price };
};
const asCashier = (ctx, limit) => {
  ctx.sandbox.SubsystemApp.hasCapability = (c) => c !== 'retail.discount';
  ctx.RetailSystem._cashierDiscountLimit = limit;
};

async function run() {
  // 1. limit 10, no capability: the box exists and is capped at 10
  {
    const ctx = loadRetailSystem({});
    asCashier(ctx, 10);
    const html = ctx.RetailSystem._discountControlHtml();
    assert.ok(/id="pos-disc"/.test(html) && /max="10"/.test(html), 'the capped % box was not rendered: ' + html);
    assert.ok(html.indexOf('10%') !== -1, 'the cap is not shown beside the box');
    assert.ok(html.indexOf('pos-disc-link') === -1);
    console.log('PASS: limit 10 without the capability renders a % box capped at 10');
  }

  // 2. over the limit + F10: no POST, a toast saying the limit
  {
    const ctx = loadRetailSystem({});
    asCashier(ctx, 10);
    cartOf(ctx, 100);
    ctx.getEl('pos-disc').value = '15';
    pressCode(ctx, 'F10', 'F10');
    await settle();
    assert.strictEqual(ctx.posted.length, 0, 'an over-limit discount reached the server.');
    assert.ok(ctx.toasts.some((t) => /at most 10% discount/.test(t.msg)), 'no limit toast: ' + JSON.stringify(ctx.toasts));
    console.log('PASS: 15% against a 10% limit never posts and says why');
  }

  // 3. exactly the limit posts, per-line discount_pct 10
  {
    const ctx = loadRetailSystem({});
    asCashier(ctx, 10);
    cartOf(ctx, 100);
    ctx.getEl('pos-disc').value = '10';
    pressCode(ctx, 'F10', 'F10');
    await settle();
    assert.strictEqual(ctx.posted.length, 1, 'a discount exactly at the limit was blocked.');
    assert.strictEqual(ctx.posted[0].body.items[0].discount_pct, 10);
    console.log('PASS: 10% against a 10% limit posts with discount_pct 10');
  }

  // 4. limit 0, no capability: the link, no box
  {
    const ctx = loadRetailSystem({});
    asCashier(ctx, 0);
    const html = ctx.RetailSystem._discountControlHtml();
    assert.ok(html.indexOf('pos-disc-link') !== -1 && !/id="pos-disc"/.test(html), 'limit 0 should keep the manager link only');
    console.log('PASS: limit 0 keeps the "needs a manager" link and no box');
  }

  // 5. capability: full 0-100 box, never limited (the allow half)
  {
    const ctx = loadRetailSystem({});
    ctx.sandbox.SubsystemApp.hasCapability = () => true;
    ctx.RetailSystem._cashierDiscountLimit = 10;
    const html = ctx.RetailSystem._discountControlHtml();
    assert.ok(/max="100"/.test(html), 'a manager/owner box must stay 0-100');
    cartOf(ctx, 100);
    ctx.getEl('pos-disc').value = '50';
    pressCode(ctx, 'F10', 'F10');
    await settle();
    assert.strictEqual(ctx.posted.length, 1, 'a holder of retail.discount was limited.');
    console.log('PASS: with the capability the box is 0-100 and 50% posts');
  }

  // 6. the server's DISCOUNT_OVER_LIMIT is shown as the translated message
  {
    const ctx = loadRetailSystem({});
    ctx.sandbox.SubsystemApp.hasCapability = () => true;   // client check passes; server refuses
    cartOf(ctx, 100);
    ctx.RetailSystem._post = async () => ({ status: 'error', code: 'DISCOUNT_OVER_LIMIT', message: 'x', data: { limit_pct: 7.5 } });
    pressCode(ctx, 'F10', 'F10');
    await settle();
    assert.ok(ctx.toasts.some((t) => /at most 7.5% discount/.test(t.msg)), JSON.stringify(ctx.toasts));
    console.log('PASS: a server DISCOUNT_OVER_LIMIT becomes the limit message');
  }

  console.log('\nAll cashier-discount-limit till tests passed.');
}

run().then(() => process.exit(0)).catch((err) => {
  console.error('FAIL:', err.message);
  process.exit(1);
});
