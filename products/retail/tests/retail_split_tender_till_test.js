/**
 * retail_split_tender_till_test.js -- the till half of split tender: type an
 * amount, press a method key, and that amount is taken as a PART; the rest is
 * paid by whatever is selected at Charge. Loads the REAL subsystem-retail.js
 * (AURA_SR_SRC points a mutation proof at a broken copy) and drives the real
 * key handlers and _checkout down to the (mocked) POST, asserting the payload.
 * The server half is retail_split_tender_test.py.
 *
 * Cart used: one line, 11.832 (a JOD price with fils).
 *
 * Run: node products/retail/tests/retail_split_tender_till_test.js
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

const F = (n) => 'F' + n;
const cartOf = (ctx, price) => {
  seedCart(ctx, [LINE('p1', 'Widget', 1, price)]);
  ctx.RetailSystem._currentTotals = { subtotal: price, discount: 0, tax: 0, total: price, amountDue: price };
};
const typeAmount = (ctx, v) => { ctx.getEl('pos-tendered').value = String(v); ctx.state.activeEl = ctx.getEl('pos-tendered'); };

async function run() {
  // 1. Amount + F2 takes a PART; box clears; cash re-selected; remaining shown
  {
    const ctx = loadRetailSystem({});
    cartOf(ctx, 11.832);
    typeAmount(ctx, '5');
    pressCode(ctx, 'F2', 'F2');
    const R = ctx.RetailSystem;
    assert.deepStrictEqual(JSON.parse(JSON.stringify(R._tenderParts)), [{ method: 'card', minor: 5000 }], 'F2 with 5 typed did not take a 5.000 card part.');
    assert.strictEqual(ctx.getEl('pos-tendered').value, '', 'the amount box was not cleared after a part.');
    assert.strictEqual(R._paymentMethod, 'cash', 'the selected tender did not go back to Cash after a part.');
    assert.strictEqual(R._remainingMinor(), 6832);
    assert.ok(ctx.getEl('pos-remaining').textContent.indexOf('6.832') !== -1 || ctx.getEl('pos-tender-parts').innerHTML.indexOf('6.832') !== -1, 'Remaining 6.832 not shown.');
    assert.ok(ctx.getEl('pos-checkout-btn').textContent.indexOf('6.832') !== -1, 'Charge label is not the remainder: ' + ctx.getEl('pos-checkout-btn').textContent);
    console.log('PASS: amount + F2 takes a card part, clears the box, shows the remainder 6.832');
  }

  // 2. Backspace in the EMPTY box removes the last part; not when text is there / badInput
  {
    const ctx = loadRetailSystem({});
    cartOf(ctx, 11.832);
    typeAmount(ctx, '5'); pressCode(ctx, 'F2', 'F2');
    ctx.state.activeEl = ctx.getEl('pos-tendered');
    ctx.getEl('pos-tendered').value = '3';
    const keep = pressCode(ctx, 'Backspace', 'Backspace');
    assert.strictEqual(ctx.RetailSystem._tenderParts.length, 1, 'Backspace with text in the box removed a part.');
    assert.strictEqual(keep.defaultPrevented, false);
    ctx.getEl('pos-tendered').value = '';
    ctx.getEl('pos-tendered').validity = { badInput: true };
    pressCode(ctx, 'Backspace', 'Backspace');
    assert.strictEqual(ctx.RetailSystem._tenderParts.length, 1, 'Backspace over a half-typed (badInput) value removed a part.');
    ctx.getEl('pos-tendered').validity = { badInput: false };
    const ev = pressCode(ctx, 'Backspace', 'Backspace');
    assert.strictEqual(ctx.RetailSystem._tenderParts.length, 0, 'Backspace in the empty box did not remove the part.');
    assert.strictEqual(ev.defaultPrevented, true);
    assert.ok(ctx.toasts.some((t) => /Part removed/.test(t.msg)), 'no "Part removed" toast');
    console.log('PASS: Backspace removes the last part only from an empty, valid box');
  }

  // 3. Card 5 + Cash 10 then Enter -> POST split with the right tenders, no amount_paid
  {
    const ctx = loadRetailSystem({});
    cartOf(ctx, 11.832);
    typeAmount(ctx, '5'); pressCode(ctx, 'F2', 'F2');
    typeAmount(ctx, '10');
    pressCode(ctx, 'Enter', 'Enter');
    await settle();
    assert.strictEqual(ctx.posted.length, 1, 'Enter did not charge the split sale.');
    const b = ctx.posted[0].body;
    assert.strictEqual(b.payment_method, 'split');
    assert.deepStrictEqual(JSON.parse(JSON.stringify(b.tenders)), [{ method: 'card', amount: 5 }, { method: 'cash', amount: 10 }]);
    assert.ok(!('amount_paid' in b), 'a split sale must not send a client amount_paid.');
    console.log('PASS: card 5 + cash 10 posts payment_method split with both tenders and no amount_paid');
  }

  // 4. The selected non-cash tender pays the remainder at Charge
  {
    const ctx = loadRetailSystem({});
    cartOf(ctx, 11.832);
    typeAmount(ctx, '5'); pressCode(ctx, 'F1', 'F1');       // cash part 5
    pressCode(ctx, 'F3', 'F3');                              // mobile selected for the rest (no amount typed)
    assert.strictEqual(ctx.RetailSystem._paymentMethod, 'mobile');
    pressCode(ctx, 'F10', 'F10');
    await settle();
    const b = ctx.posted[0].body;
    assert.deepStrictEqual(JSON.parse(JSON.stringify(b.tenders)), [{ method: 'cash', amount: 5 }, { method: 'mobile', amount: 6.832 }]);
    console.log('PASS: the selected non-cash tender pays the remainder at Charge');
  }

  // 5. F5 with an amount typed: refused, no part
  {
    const ctx = loadRetailSystem({});
    cartOf(ctx, 11.832);
    typeAmount(ctx, '5');
    pressCode(ctx, 'F5', 'F5');
    assert.strictEqual(ctx.RetailSystem._tenderParts.length, 0);
    assert.ok(ctx.toasts.some((t) => /On account takes whatever is left/.test(t.msg)), 'no on-account refusal toast');
    console.log('PASS: F5 with an amount typed is refused, no part is made');
  }

  // 6. Amount >= remaining + F2 is today's behaviour: select card, no part
  {
    const ctx = loadRetailSystem({});
    cartOf(ctx, 11.832);
    typeAmount(ctx, '20');
    pressCode(ctx, 'F2', 'F2');
    assert.strictEqual(ctx.RetailSystem._tenderParts.length, 0, 'a whole-remainder amount became a part.');
    assert.strictEqual(ctx.RetailSystem._paymentMethod, 'card');
    console.log('PASS: an amount covering the whole sale + F2 just selects Card (unchanged)');
  }

  // 7. Parts that now cover the (edited) cart are dropped by _recalc
  {
    const ctx = loadRetailSystem({});
    const R = ctx.RetailSystem;
    cartOf(ctx, 11.832);
    typeAmount(ctx, '5'); pressCode(ctx, 'F2', 'F2');
    R._cart = [LINE('p1', 'Widget', 1, 4)];
    // _recalc needs the whole till DOM; the judging step is a named method so it is testable alone
    assert.strictEqual(typeof R._dropStaleTenderParts, 'function', '_dropStaleTenderParts missing');
    R._currentTotals = { subtotal: 4, discount: 0, tax: 0, total: 4, amountDue: 4 };
    R._dropStaleTenderParts();
    assert.strictEqual(R._tenderParts.length, 0, 'parts covering the whole edited cart were kept.');
    assert.ok(ctx.toasts.some((t) => /Cart changed/.test(t.msg)), 'no "Cart changed" toast');
    // and the allow half: parts below the amount due survive a recalc
    const ctx2 = loadRetailSystem({});
    cartOf(ctx2, 11.832);
    typeAmount(ctx2, '5'); pressCode(ctx2, 'F2', 'F2');
    ctx2.RetailSystem._dropStaleTenderParts();
    assert.strictEqual(ctx2.RetailSystem._tenderParts.length, 1, 'a still-valid part was dropped.');
    console.log('PASS: parts that cover the edited cart are dropped; still-valid parts survive');
  }

  // 8. Final credit without a customer is refused BEFORE any POST
  {
    const ctx = loadRetailSystem({});
    cartOf(ctx, 11.832);
    typeAmount(ctx, '5'); pressCode(ctx, 'F1', 'F1');
    ctx.RetailSystem._paymentMethod = 'credit';
    ctx.getEl('pos-customer').value = '';
    pressCode(ctx, 'F10', 'F10');
    await settle();
    assert.strictEqual(ctx.posted.length, 0, 'a final on-account tender with no customer reached the server.');
    assert.ok(ctx.toasts.some((t) => /Credit sales require a customer/.test(t.msg)));
    console.log('PASS: cash part + on-account remainder with no customer is refused before the POST');
  }

  // 9. Cash final LESS than the remainder is refused with the how-to message
  {
    const ctx = loadRetailSystem({});
    cartOf(ctx, 11.832);
    typeAmount(ctx, '5'); pressCode(ctx, 'F2', 'F2');
    typeAmount(ctx, '3');
    pressCode(ctx, 'Enter', 'Enter');
    await settle();
    assert.strictEqual(ctx.posted.length, 0);
    assert.ok(ctx.toasts.some((t) => /Cash is less than what is left/.test(t.msg)));
    console.log('PASS: cash short of the remainder is refused, with the way out');
  }

  // 10. No parts: payload exactly today's
  {
    const ctx = loadRetailSystem({});
    cartOf(ctx, 10);
    pressCode(ctx, 'F10', 'F10'); await settle();
    const b = ctx.posted[0].body;
    assert.strictEqual(b.payment_method, 'cash'); assert.strictEqual(b.amount_paid, 10);
    assert.ok(!('tenders' in b), 'a plain sale grew a tenders key.');
    console.log('PASS: a sale with no parts posts exactly the legacy payload');
  }

  console.log('\nAll split-tender till tests passed.');
}

run().then(() => process.exit(0)).catch((err) => {
  console.error('FAIL:', err.message);
  process.exit(1);
});
