/**
 * retail_till_roomy_keys_test.js -- the till-roomy key additions, driven through
 * the REAL _onScannerKey (capture) + _onPOSShortcut (bubble) handlers of
 * products/retail/frontend/subsystem-retail.js, the same way
 * retail_pos_keyboard_shortcuts_test.js does.
 *
 * What is pinned, and the guard each one protects (every one is mutation-proved:
 * see the MUTANTS list in the run notes / commit message -- AURA_SR_SRC points
 * this file at a deliberately broken copy of the source and it must go red):
 *
 *   1. "1.25*" arms a pending quantity of 1.25 (a weight), a 4th decimal is
 *      ignored, and a typed "1.25" is stripped from the search box. The server
 *      takes decimal quantities for any product (create_sale: float(quantity)).
 *   2. A quantity stays exact: 0.1 + 0.2 is 0.3, not 0.30000000000000004.
 *   3. Shift+F5..F8 put a cash NOTE (5/10/20/50) in the amount box from ANY
 *      focus (the scan box included), switch the tender to cash, and NEVER
 *      charge. Both halves: an empty cart does nothing.
 *   4. Shift+F10 prints the LAST sale again (and says so when there is none),
 *      Shift+F11 opens the selected (else last) line's options on Discount --
 *      and a person with no discount right gets the explanation, not a dialog --
 *      Shift+F12 opens Returns.
 *   5. X in the EMPTY Cash Tendered box charges exact cash -- after a short
 *      wait, cancelled by any further keystroke (a scanner burst starting with
 *      "X" must not charge) -- and X in the SEARCH box never does ("Xbox").
 *   6. The line options dialog edits the quantity, refuses 0 / garbage / more
 *      than is in stock, and a refusal leaves the line untouched.
 *   7. Change Due shows for ANY cash amount: "Change Due" once it covers the
 *      total, "Still to pay" with the shortfall before.
 *   8. The F1-F6 badges are no longer hidden on a short till (the <=920px
 *      block), the Mobile tile reads "CliQ" (full name in its title) and Cash
 *      carries both F1 and X.
 *
 * Run: node products/retail/tests/retail_till_roomy_keys_test.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const SRC_PATH = process.env.AURA_SR_SRC || path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');
const source = fs.readFileSync(SRC_PATH, 'utf8');

const INPUT_IDS = ['pos-search', 'pos-tendered', 'pos-disc', 'pos-customer', 'lm-qty', 'lm-pct', 'lm-price'];

function loadRetailSystem(opts) {
  const options = opts || {};
  const sandbox = {};
  sandbox.window = sandbox;
  sandbox.navigator = { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' };
  sandbox.t = (s) => s;
  sandbox.console = console;
  sandbox.setTimeout = setTimeout; sandbox.clearTimeout = clearTimeout;
  sandbox.localStorage = { getItem: () => null, setItem: () => {} };
  sandbox.performance = { now: () => sandbox.__clock };
  sandbox.__clock = 10000;

  const els = Object.create(null);
  const state = { activeEl: null };
  const makeEl = (id) => {
    const attrs = {};
    return {
      id, value: '', disabled: false, textContent: '', innerHTML: '', style: {}, hidden: false,
      tagName: INPUT_IDS.indexOf(id) !== -1 ? 'INPUT' : 'DIV', isContentEditable: false,
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      setAttribute(k, v) { attrs[k] = v; }, getAttribute(k) { return attrs[k] == null ? null : attrs[k]; },
      removeAttribute(k) { delete attrs[k]; }, attrs,
      focus() { state.activeEl = els[id]; }, select() {},
    };
  };
  const getEl = (id) => els[id] || (els[id] = makeEl(id));
  const focusOn = (id) => { state.activeEl = id ? getEl(id) : null; };

  sandbox.document = {
    get activeElement() { return state.activeEl; },
    getElementById: getEl,
    querySelectorAll: () => [],
    querySelector: () => null,
    addEventListener: () => {},
    documentElement: { getAttribute: () => null },
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: SRC_PATH });
  const R = sandbox.RetailSystem;
  assert.ok(R, 'RetailSystem failed to load');

  const toasts = [];
  const navigated = [];
  sandbox.SubsystemApp = {
    active: 'retail',
    showToast: (m, k) => { toasts.push([m, k]); },
    _navigate: (s) => { navigated.push(s); },
  };
  R._section = 'pos';
  R._cart = [];
  R._currentTotals = { subtotal: 0, discount: 0, tax: 0, total: 0 };
  R._paymentMethod = 'cash';
  R._pendingQty = null;
  R._qtyKeyBuffer = '';
  R._qtyKeyLastAt = 0;
  R._clearCart = () => {};
  R._applyLocalStockDecrement = () => {};
  R._showReceipt = () => {};
  R._renderCart = () => {};
  const posted = [];
  // Only the SALE posts are recorded: checkout also posts to the customer-display relay.
  R._post = async (url, body) => { if (/\/sales(\?|$)/.test(url)) posted.push({ url, body }); return { status: 'success', data: { lines: [] } }; };
  R._dispatchScan = () => {};
  return { R, sandbox, els, getEl, focusOn, state, posted, toasts, navigated };
}

function fire(ctx, code, key, extra) {
  let stopped = false;
  const evt = Object.assign({
    key: key == null ? '' : key, code, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
    defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() { stopped = true; },
  }, extra || {});
  ctx.R._onScannerKey(evt);
  if (!stopped) ctx.R._onPOSShortcut(evt);
  return evt;
}
// A human-paced press (well over the scanner's burst window).
function press(ctx, code, key, extra) { ctx.sandbox.__clock += 400; return fire(ctx, code, key, extra); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const LINE = (id, name, qty, price, max) => ({ product_id: id, name, quantity: qty, unit_price: price, tax_rate: 0, line_total: qty * price, max_stock: max == null ? 999 : max });
function seed(ctx, lines) {
  ctx.R._cart = lines;
  const total = lines.reduce((a, l) => a + l.line_total, 0);
  ctx.R._currentTotals = { subtotal: total, discount: 0, tax: 0, total };
}
function typeRun(ctx, text) {
  for (const ch of text) {
    if (ch === '.') press(ctx, 'Period', '.');
    else press(ctx, 'Digit' + ch, ch);
  }
}
const star = (ctx) => press(ctx, 'Digit8', '*', { shiftKey: true });

let passed = 0;
function pass(msg) { passed += 1; console.log('PASS: ' + msg); }

async function main() {
  // ── 1. decimal quantity ────────────────────────────────────────────────
  {
    const ctx = loadRetailSystem();
    typeRun(ctx, '1.25'); star(ctx);
    assert.strictEqual(ctx.R._pendingQty, 1.25, `"1.25*" armed ${ctx.R._pendingQty}, not 1.25`);
    ctx.R._pendingQty = null;
    typeRun(ctx, '1.2345'); star(ctx);
    assert.strictEqual(ctx.R._pendingQty, 1.234, `a 4th decimal must be ignored: got ${ctx.R._pendingQty}`);
    ctx.R._pendingQty = null;
    typeRun(ctx, '.5'); star(ctx);
    assert.strictEqual(ctx.R._pendingQty, 5, `a leading "." must not start a decimal: got ${ctx.R._pendingQty}`);
    // an integer still works, and is still an integer
    ctx.R._pendingQty = null;
    typeRun(ctx, '3'); star(ctx);
    assert.strictEqual(ctx.R._pendingQty, 3);
    pass('"1.25*" arms 1.25, a 4th decimal is ignored, a leading "." is not a number, "3*" is still 3');
  }
  {
    // typed into the search box, the "1.25" must not stay there
    const ctx = loadRetailSystem();
    ctx.focusOn('pos-search');
    const box = ctx.getEl('pos-search');
    ctx.R._filterPOS = () => {};
    for (const ch of '1.25') { box.value += ch; press(ctx, ch === '.' ? 'Period' : 'Digit' + ch, ch); }
    star(ctx);
    assert.strictEqual(box.value, '', `the typed decimal stayed in the search box: ${JSON.stringify(box.value)}`);
    assert.strictEqual(ctx.R._pendingQty, 1.25);
    pass('the typed "1.25" is stripped from the search box when "*" arrives');
  }

  // ── 2. exact quantity arithmetic ────────────────────────────────────────
  {
    const ctx = loadRetailSystem();
    seed(ctx, [LINE('p1', 'Tomatoes', 0.1, 2)]);
    ctx.R._updateQty(0, 0.2);
    assert.strictEqual(ctx.R._cart[0].quantity, 0.3, `0.1 + 0.2 became ${ctx.R._cart[0].quantity}`);
    pass('a quantity stays exact: 0.1 + 0.2 = 0.3');
  }

  // ── 3. cash note keys ───────────────────────────────────────────────────
  {
    for (const [fcode, note] of [['F5', '5'], ['F6', '10'], ['F7', '20'], ['F8', '50']]) {
      const ctx = loadRetailSystem();
      seed(ctx, [LINE('p1', 'Widget', 1, 15.15)]);
      ctx.focusOn('pos-search');                    // the home position: a text field has focus
      const evt = press(ctx, fcode, fcode, { shiftKey: true });
      assert.strictEqual(ctx.getEl('pos-tendered').value, note, `Shift+${fcode} put ${JSON.stringify(ctx.getEl('pos-tendered').value)} in the amount box, not ${note}`);
      assert.ok(evt.defaultPrevented, `Shift+${fcode} was not preventDefault()ed`);
      await sleep(5);
      assert.strictEqual(ctx.posted.length, 0, `Shift+${fcode} charged the sale`);
    }
    // not cash -> it switches to cash first
    const ctx = loadRetailSystem();
    seed(ctx, [LINE('p1', 'Widget', 1, 15.15)]);
    ctx.R._paymentMethod = 'card';
    const cashBtn = { dataset: { method: 'cash' }, classList: { add() {}, remove() {} } };
    ctx.sandbox.document.querySelector = (q) => (q.indexOf('data-method="cash"') !== -1 ? cashBtn : null);
    const switched = [];
    ctx.R._setPayment = (m) => { switched.push(m); ctx.R._paymentMethod = m; };
    press(ctx, 'F6', 'F6', { shiftKey: true });
    assert.deepStrictEqual(switched, ['cash'], 'a cash note key did not switch the tender to cash');
    // the other half: an empty cart does nothing
    const empty = loadRetailSystem();
    press(empty, 'F6', 'F6', { shiftKey: true });
    assert.strictEqual(empty.getEl('pos-tendered').value, '', 'a cash note key filled the amount box of an EMPTY cart');
    assert.ok(empty.toasts.some(([m]) => m === 'Cart is empty'), 'no "Cart is empty" answer');
    pass('Shift+F5-F8 set 5/10/20/50 from the scan box, switch to cash, never charge; an empty cart is refused');
  }

  // ── 4. reprint / line discount / returns ───────────────────────────────
  {
    const ctx = loadRetailSystem();
    const printed = [];
    ctx.R._printReceipt = async (s) => { printed.push(s); };
    press(ctx, 'F10', 'F10', { shiftKey: true });
    assert.strictEqual(printed.length, 0, 'reprint with no sale in memory still printed');
    assert.ok(ctx.toasts.some(([m]) => /No receipt to reprint/.test(m)), 'no answer when there is nothing to reprint');
    ctx.R._lastSaleData = { sale_number: 'S-1', total: 9 };
    press(ctx, 'F10', 'F10', { shiftKey: true });
    await sleep(5);
    assert.deepStrictEqual(printed, [ctx.R._lastSaleData], 'Shift+F10 did not print the last sale');
    pass('Shift+F10 prints the last sale again, and answers when there is none');
  }
  {
    const ctx = loadRetailSystem();
    const opened = [];
    ctx.R._openLineMenu = (i, f) => { opened.push([i, f]); };
    ctx.R._hasDiscountCap = () => true;
    seed(ctx, [LINE('p1', 'A', 1, 1), LINE('p2', 'B', 1, 2), LINE('p3', 'C', 1, 3)]);
    ctx.focusOn('pos-search');
    press(ctx, 'F11', 'F11', { shiftKey: true });
    assert.deepStrictEqual(opened, [[2, 'pct']], `no selection should open the LAST line on Discount: ${JSON.stringify(opened)}`);
    ctx.R._selLine = 0;
    press(ctx, 'F11', 'F11', { shiftKey: true });
    assert.deepStrictEqual(opened[1], [0, 'pct'], `the selected line was not the one opened: ${JSON.stringify(opened)}`);
    // no discount right at all: explain, do not open
    const noRight = loadRetailSystem();
    const o2 = []; let explained = 0;
    noRight.R._openLineMenu = (i) => { o2.push(i); };
    noRight.R._hasDiscountCap = () => false; noRight.R._cashierDiscountLimit = 0;
    noRight.R._explainDiscountNeedsManager = () => { explained += 1; };
    seed(noRight, [LINE('p1', 'A', 1, 1)]);
    press(noRight, 'F11', 'F11', { shiftKey: true });
    assert.strictEqual(o2.length, 0, 'a person with no discount right got the dialog');
    assert.strictEqual(explained, 1, 'a person with no discount right got no explanation');
    pass('Shift+F11 opens the selected (else last) line on Discount; no discount right -> the explanation');
  }
  {
    const ctx = loadRetailSystem();
    press(ctx, 'F12', 'F12', { shiftKey: true });
    assert.deepStrictEqual(ctx.navigated, ['returns'], 'Shift+F12 did not open Returns');
    pass('Shift+F12 opens Returns');
  }

  // ── 5. X in the amount box ──────────────────────────────────────────────
  {
    // a lone X in the empty amount box charges exact cash, after the wait
    const ctx = loadRetailSystem();
    seed(ctx, [LINE('p1', 'Widget', 3, 10)]);
    ctx.focusOn('pos-tendered');
    press(ctx, 'KeyX', 'x');
    assert.strictEqual(ctx.posted.length, 0, 'X charged INSTANTLY in the amount box (the burst guard is not there)');
    await sleep(ctx.R.EXACT_ARM_MS + 120);
    assert.strictEqual(ctx.posted.length, 1, 'a lone X in the empty amount box did not charge');
    assert.strictEqual(ctx.posted[0].body.amount_paid, 30);
    assert.strictEqual(ctx.posted[0].body.payment_method, 'cash');
    // a key right behind it = a burst / typing: nothing charged
    const burst = loadRetailSystem();
    seed(burst, [LINE('p1', 'Widget', 3, 10)]);
    burst.focusOn('pos-tendered');
    fire(burst, 'KeyX', 'x');
    fire(burst, 'Digit1', '1');                      // 0 ms later: a scanner's next character
    await sleep(burst.R.EXACT_ARM_MS + 120);
    assert.strictEqual(burst.posted.length, 0, 'an X followed at once by another key (a scanner burst) charged the sale');
    // X in the SEARCH box never charges (a product called "Xbox")
    const search = loadRetailSystem();
    seed(search, [LINE('p1', 'Widget', 3, 10)]);
    search.focusOn('pos-search');
    press(search, 'KeyX', 'x');
    await sleep(search.R.EXACT_ARM_MS + 120);
    assert.strictEqual(search.posted.length, 0, 'X in the search box charged the sale');
    // X in an amount box that already holds digits is typing
    const typed = loadRetailSystem();
    seed(typed, [LINE('p1', 'Widget', 3, 10)]);
    typed.focusOn('pos-tendered'); typed.getEl('pos-tendered').value = '5';
    press(typed, 'KeyX', 'x');
    await sleep(typed.R.EXACT_ARM_MS + 120);
    assert.strictEqual(typed.posted.length, 0, 'X charged although an amount had been typed');
    pass('X in the empty amount box charges after the burst wait; a burst, the search box and a typed amount never do');
  }

  // ── 6. the line options dialog edits the quantity ───────────────────────
  {
    const mk = () => {
      const ctx = loadRetailSystem();
      seed(ctx, [LINE('p1', 'Tomatoes', 1, 2, 5)]);
      ctx.R._hasDiscountCap = () => false; ctx.R._cashierDiscountLimit = 10;
      return ctx;
    };
    let ctx = mk();
    ctx.getEl('lm-qty').value = '1.25';
    ctx.R._lineMenuApply(0);
    assert.strictEqual(ctx.R._cart[0].quantity, 1.25, 'the dialog did not set a decimal quantity');
    assert.strictEqual(ctx.R._cart[0].line_total, 2.5, 'the line total did not follow the new quantity');
    for (const bad of ['0', 'abc', '-1', '1.2345', '']) {
      ctx = mk(); ctx.getEl('lm-qty').value = bad; ctx.R._lineMenuApply(0);
      assert.strictEqual(ctx.R._cart[0].quantity, 1, `the quantity ${JSON.stringify(bad)} was accepted`);
      assert.ok(ctx.getEl('lm-error').textContent, `no error shown for ${JSON.stringify(bad)}`);
    }
    ctx = mk(); ctx.getEl('lm-qty').value = '6'; ctx.R._lineMenuApply(0);
    assert.strictEqual(ctx.R._cart[0].quantity, 1, 'more than is in stock was accepted');
    // a refusal further down (discount over the limit) must leave the quantity alone too
    ctx = mk(); ctx.getEl('lm-qty').value = '2'; ctx.getEl('lm-pct').value = '50'; ctx.R._lineMenuApply(0);
    assert.strictEqual(ctx.R._cart[0].quantity, 1, 'a refused discount still changed the quantity');
    pass('the line dialog sets decimals, refuses 0 / garbage / over-stock, and a refusal leaves the line alone');
  }

  // ── 7. Change Due ───────────────────────────────────────────────────────
  {
    const ctx = loadRetailSystem();
    seed(ctx, [LINE('p1', 'Widget', 1, 15.15)]);
    ctx.R._currencyDecimals = 3;
    ctx.R._cashRounding = null;
    const row = ctx.getEl('pos-change-row'); row.style.display = 'none';
    ctx.getEl('pos-tendered').value = '10';
    ctx.R._calcChange();
    assert.strictEqual(row.style.display, '', 'Change Due stayed hidden for a cash amount below the total');
    assert.strictEqual(ctx.getEl('pos-change-lbl').textContent, 'Still to pay');
    assert.strictEqual(row.attrs['data-state'], 'short');
    assert.ok(/5\.150/.test(ctx.getEl('pos-change').textContent), `shortfall shown as ${ctx.getEl('pos-change').textContent}`);
    ctx.getEl('pos-tendered').value = '20';
    ctx.R._calcChange();
    assert.strictEqual(ctx.getEl('pos-change-lbl').textContent, 'Change Due');
    assert.strictEqual(row.attrs['data-state'], 'change');
    assert.ok(/4\.850/.test(ctx.getEl('pos-change').textContent), `change shown as ${ctx.getEl('pos-change').textContent}`);
    ctx.getEl('pos-tendered').value = '';
    ctx.R._calcChange();
    assert.strictEqual(row.style.display, 'none', 'the row stayed up with no amount');
    pass('Change Due: "Still to pay" short of the total, "Change Due" once covered, hidden with no amount');
  }

  // ── 8. tender tiles + the short-till CSS ───────────────────────────────
  {
    const ctx = loadRetailSystem();
    const html = ctx.R._renderPayButtons();
    assert.ok(/pos-pay-label">CliQ</.test(html), 'the Mobile tile does not read "CliQ"');
    assert.ok(/title="Mobile \/ CliQ"/.test(html), 'the full tender name is not kept in the tile title');
    const cash = html.split('data-method="card"')[0];
    assert.ok(/pos-pay-kbd" aria-hidden="true">F1</.test(cash) && /pos-pay-kbd-x" aria-hidden="true">X</.test(cash), 'the Cash tile does not carry F1 and X');
    const m = /@media \(max-height: 920px\) \{/.exec(source);
    assert.ok(m, 'the short-till media block is gone');
    let depth = 0; let end = -1;
    for (let i = m.index + m[0].length - 1; i < source.length; i++) {
      if (source[i] === '{') depth += 1;
      else if (source[i] === '}') { depth -= 1; if (depth === 0) { end = i; break; } }
    }
    const block = source.slice(m.index, end);
    assert.ok(!/^\s*\.pos-pay-kbd \{ display:none; \}/m.test(block),
      'the <=920px block hides EVERY .pos-pay-kbd again -- the F1-F6 tender badges vanish on a 1366x768 till');
    assert.ok(/\.pos-pane-hdr \.pos-pay-kbd/.test(block), 'the other badges no longer step aside in the pane header');
    pass('tender tiles: "CliQ" short label, F1 + X on Cash, F1-F6 badges survive the <=920px block');
  }

  console.log(`\nretail_till_roomy_keys_test.js: ${passed} checks passed`);
}

main().catch((e) => { console.error('FAIL: retail_till_roomy_keys_test.js'); console.error(e); process.exit(1); });
