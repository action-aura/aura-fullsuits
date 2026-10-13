/**
 * retail_cash_panel_test.js -- the till's cash panel (UI-1, review A #1).
 *
 * Cash used to be a 100px number box with a browser spinner: no notes to tap,
 * no pad, and the figure the cashier says aloud (change) was the smallest text
 * on screen. The panel adds a 3x4 pad, amount chips ("Exact" then the next JOD
 * notes) and a display-size figure. What is pinned here, and why each is not
 * vacuous:
 *
 *   1. CHIPS are computed from the total in minor units, never rounded: 3.250
 *      -> Exact 3.250, 5, 10, 20, 50; 47.5 -> Exact, 50; 0.75 -> Exact + four
 *      notes (the cap is five chips); a total at/over the biggest note still
 *      gets one note to hand over. A note at or below the total is NOT a chip
 *      (it cannot pay the sale) -- the filter is mutation-proved.
 *   2. The PAD edits #pos-tendered the way a calculator does: no leading zeros,
 *      one ".", at most _currencyDp() decimals (a 4th JOD digit is IGNORED, not
 *      rounded), "00" does nothing on an empty box, backspace drops one char.
 *   3. The FIGURE says the right thing in each state, in integer minor units:
 *      Amount due (nothing typed) / Still to pay (short) / Change due (covers),
 *      including when part payments or a loyalty redemption lowered what is due.
 *   4. OPEN / CLOSE is wired to the real entry points: F1 / go-to-pay (via
 *      _focusTendered) and the Cash tile open it; Esc, another tender and an
 *      emptied cart close it -- and a harness whose elements merely default to
 *      hidden:false must NOT read as "open" (the DOM flag alone is not trusted).
 *   5. Physical typing still works and is sanitised (digits and one dot only,
 *      Arabic-Indic digits folded, decimals capped).
 *
 * Run: node products/retail/tests/retail_cash_panel_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');
const SRC = fs.readFileSync(FRONTEND_FILE, 'utf8');

function el(id, extra) {
  return Object.assign({
    id, value: '', textContent: '', innerHTML: '', hidden: false, style: {}, dataset: {}, attrs: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k] == null ? null : this.attrs[k]; },
    appendChild() {}, focus() {}, select() {}, querySelector() { return null; }, closest() { return null; },
    addEventListener() {}, remove() {},
  }, extra);
}

function load(src) {
  const els = Object.create(null);
  const get = (id) => els[id] || (els[id] = el(id));
  const sandbox = {
    console,
    t: (s) => s,
    fetch: () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: 'success', data: {} }) }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (test)' },
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    URLSearchParams,
    document: {
      activeElement: null,
      getElementById: (id) => (id === 'ret-styles' ? el('ret-styles') : get(id)),
      createElement: () => el('::created'), querySelector: () => null, querySelectorAll: () => [],
      head: { appendChild() {} }, body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } }, addEventListener() {},
    },
    SubsystemApp: { active: 'retail', showToast() {}, _navigate() {}, hasCapability: () => true },
  };
  sandbox.window = sandbox;
  sandbox.Chart = function ChartStub() { return { destroy() {} }; };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: FRONTEND_FILE });
  const R = sandbox.RetailSystem;
  assert.ok(R && typeof R._cashChips === 'function', 'RetailSystem._cashChips is missing -- the cash panel is not in the build');
  R._currencyDecimals = 3;
  R._currencySymbol = 'JD';
  R._refocusScan = () => {};
  return { R, get, sandbox };
}

const vals = (chips) => JSON.parse(JSON.stringify(chips.map((c) => c.value)));   // clone: arrays from the vm realm fail deepStrictEqual on prototype

function testChips(src) {
  const { R } = load(src);
  const cases = [
    // [total, dp, expected values (first is the exact total)]
    [3.25, 3, [3.25, 5, 10, 20, 50]],
    [47.5, 3, [47.5, 50]],
    [0.75, 3, [0.75, 1, 5, 10, 20]],            // capped at five chips: the 50 is dropped
    [60, 3, [60, 100]],                         // beyond the biggest note: the next multiple of it
    [50, 3, [50, 100]],                         // a note equal to the total cannot pay it more than "Exact" does
    [5, 3, [5, 10, 20, 50]],
    [3.25, 2, [3.25, 5, 10, 20, 50, 100].slice(0, 5)],
    [120, 2, [120, 200]],
  ];
  for (const [total, dp, expected] of cases) {
    R._currencyDecimals = dp;
    const chips = R._cashChips(total, dp);
    assert.deepStrictEqual(vals(chips), expected, `chips for ${total} at ${dp}dp`);
    assert.strictEqual(chips[0].exact, true, 'the first chip is Exact');
    assert.ok(chips.slice(1).every((c) => !c.exact), 'only the first chip is Exact');
    assert.ok(chips.length <= 5, 'more than five chips');
  }
  assert.strictEqual(R._cashChips(0, 3).length, 0, 'a zero total must offer no chips');
  // never rounded: the exact chip is the total to the minor unit
  assert.strictEqual(R._cashChips(12.3456, 3)[0].value, 12.346);
  console.log('PASS: chips -- Exact + the notes above the total, max five, never rounded');
}

function testPad(src) {
  const { R, get } = load(src);
  const box = get('pos-tendered');
  const type = (...keys) => { box.value = ''; keys.forEach((k) => R._cashPadKey(k)); return box.value; };
  assert.strictEqual(type('7', '.', '5'), '7.5');
  assert.strictEqual(type('.'), '0.', '"." on an empty box starts "0."');
  assert.strictEqual(type('0', '5'), '5', 'no leading zero');
  assert.strictEqual(type('1', '.', '2', '5', '0', '9'), '1.250', 'a 4th JOD decimal is ignored, not rounded');
  assert.strictEqual(type('1', '.', '2', '.', '3'), '1.23', 'a second "." is ignored');
  assert.strictEqual(type('00'), '', '"00" on an empty box does nothing');
  assert.strictEqual(type('5', '00'), '500');
  assert.strictEqual(type('1', '2', 'back'), '1');
  assert.strictEqual(type('back'), '', 'backspace on an empty box is a no-op, not an error');
  assert.strictEqual(type('1', '2', '3', '4', '5', '6', '7', '8', '9', '0'), '123456789', 'capped at nine digits');
  R._currencyDecimals = 0;
  assert.strictEqual(type('4', '.', '5'), '45', 'a 0-decimal currency has no "."');
  R._currencyDecimals = 2;
  assert.strictEqual(type('1', '.', '2', '5', '9'), '1.25');
  console.log('PASS: pad -- calculator rules, decimals capped at _currencyDp(), "00" and backspace');
}

function seed(R, total, extra) {
  R._cart = [{ product_id: 'p1', name: 'W', quantity: 1, unit_price: total, line_total: total }];
  R._currentTotals = Object.assign({ subtotal: total, discount: 0, tax: 0, total, amountDue: total }, extra || {});
  R._paymentMethod = 'cash';
  R._tenderParts = [];
}

function testFigure(src) {
  const { R, get } = load(src);
  seed(R, 6.9);
  R._openCashPanel();
  assert.strictEqual(get('pos-cashpanel').hidden, false, 'the panel did not open');
  const state = () => get('pos-cp-status').attrs['data-state'];
  const text = () => get('pos-cp-fig').textContent;
  const label = () => get('pos-cp-lbl').textContent;
  assert.strictEqual(state(), 'due'); assert.strictEqual(label(), 'Amount due'); assert.ok(/6\.900/.test(text()), text());
  get('pos-tendered').value = '3'; R._calcChange();
  assert.strictEqual(state(), 'short'); assert.strictEqual(label(), 'Still to pay'); assert.ok(/3\.900/.test(text()), text());
  get('pos-tendered').value = '10'; R._calcChange();
  assert.strictEqual(state(), 'change'); assert.strictEqual(label(), 'Change Due'); assert.ok(/3\.100/.test(text()), 'change 10 - 6.900 must read 3.100, got ' + text());
  get('pos-tendered').value = '6.9'; R._calcChange();
  assert.strictEqual(state(), 'change'); assert.ok(/0\.000/.test(text()), 'an exact amount is zero change, got ' + text());
  // Part payments lower what is due (the same figure _calcChange compares against).
  seed(R, 11.832);
  R._tenderParts = [{ method: 'card', minor: 5000 }];
  R._openCashPanel();
  get('pos-tendered').value = '10'; R._calcChange();
  assert.ok(/3\.168/.test(text()), 'with a 5.000 card part taken, 10 against 6.832 is 3.168 change, got ' + text());
  // A loyalty redemption lowers amountDue below the total.
  seed(R, 20, { amountDue: 17.5 });
  R._openCashPanel();
  get('pos-tendered').value = '20'; R._calcChange();
  assert.ok(/2\.500/.test(text()), 'change must be owed against amountDue (17.500), got ' + text());
  // The chips follow what is due and a chip sets the amount.
  assert.deepStrictEqual(vals(R._cpChips), [17.5, 20, 50], 'chips are for the amount due, not the total');
  R._cashChipPick(1);
  assert.strictEqual(get('pos-tendered').value, '20');
  R._cashChipPick(0);
  assert.strictEqual(get('pos-tendered').value, '17.500', 'the Exact chip sets the amount due to the minor unit');
  console.log('PASS: figure -- Amount due / Still to pay / Change due, in minor units, against what is really due');
}

function testOpenClose(src) {
  const { R, get } = load(src);
  const open = () => get('pos-cashpanel').hidden === false;
  // Stub elements default to hidden:false. That alone must not read as "open".
  seed(R, 4);
  R._calcChange();
  assert.strictEqual(R._cashPanelIsOpen(), false, 'a never-opened panel read as open because the stub defaults to hidden:false');
  // F1 / Enter-to-pay land in _focusTendered().
  get('pos-cashpanel').hidden = true;
  R._focusTendered();
  assert.ok(open(), 'F1 / go-to-pay (_focusTendered) did not open the cash panel');
  // Esc closes the panel first and nothing else.
  R._onEscapeKey({}, get('pos-tendered'));
  assert.ok(!open(), 'Esc did not close the cash panel');
  // The Cash tile opens it; another tender closes it.
  R._pickTender('cash', get('btn-cash'));
  assert.ok(open(), 'choosing Cash did not open the panel');
  const card = el('btn-card'); card.getAttribute = () => null; card.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
  R._setPayment('card', card);
  assert.ok(!open(), 'choosing another tender did not close the panel');
  // An emptied cart closes it.
  seed(R, 4); R._openCashPanel();
  assert.ok(open());
  R._clearCart();
  assert.ok(!open(), 'clearing the cart did not close the panel');
  // No cart, no panel.
  R._cart = []; get('pos-cashpanel').hidden = true;
  R._openCashPanel();
  assert.ok(!open(), 'the panel opened on an empty cart');
  console.log('PASS: open on Cash / F1 / go-to-pay; close on Esc, another tender, an emptied cart; not on an empty cart');
}

function testTyping(src) {
  const { R } = load(src);
  const typed = (v) => { const e = el('x', { value: v }); R._onTenderedInput(e); return e.value; };
  assert.strictEqual(typed('12.3456'), '12.345');
  assert.strictEqual(typed('abc5'), '5');
  assert.strictEqual(typed('1.2.3'), '1.23');
  assert.strictEqual(typed('١٢'), '12', 'Arabic-Indic digits are folded to 0-9');
  assert.strictEqual(typed(''), '');
  console.log('PASS: physical typing is sanitised (digits and one dot, decimals capped, Arabic digits folded)');
}

// Mutation proofs: each breaks ONE thing the claims above protect and the
// matching check must go RED.
function mutant(find, repl) {
  assert.strictEqual(SRC.split(find).length - 1, 1, 'mutation anchor is not unique: ' + find.slice(0, 70));
  return SRC.replace(find, repl);
}
function mustFail(name, find, repl, check) {
  // The mutant is built OUTSIDE the try: an anchor that stopped matching must be
  // reported as a broken proof, not swallowed as "the check went red".
  const broken = mutant(find, repl);
  let failed = false;
  try { check(broken); } catch (e) { failed = true; }
  assert.ok(failed, `MUTATION SURVIVED: ${name} -- the test above does not protect it`);
  console.log(`PASS: mutation caught -- ${name}`);
}

function main() {
  testChips(SRC);
  testPad(SRC);
  testFigure(SRC);
  testOpenClose(SRC);
  testTyping(SRC);

  mustFail('a note at or below the total becomes a chip',
    'const notes = ladder.filter((d) => d * unit > totalMinor);', 'const notes = ladder.filter((d) => d * unit >= 0);', testChips);
  // UI-3a moved the pad's edit rules out of _cashPadKey into the shared pure _padEdit (the Discount
  // sheet uses the same keypad). The guard is the same line, now `return v;` instead of a bare
  // `return;`; this anchor follows it, and the mutation must still turn testPad red.
  mustFail('the pad stops capping decimals at _currencyDp()',
    "if (dot !== -1 && next.length - dot - 1 > dp) return v;", '', testPad);
  mustFail('change is computed against the total instead of what is due',
    "let due = cu.amountDue != null ? cu.amountDue : (cu.total || 0);", 'let due = cu.total || 0;', testFigure);
  mustFail('F1 / go-to-pay no longer opens the panel',
    "    this._openCashPanel();\n  },\n\n  _focusDiscount()", '  },\n\n  _focusDiscount()', testOpenClose);
  mustFail('Esc no longer closes the panel first',
    "if (this._cashPanelIsOpen()) { this._closeCashPanel(true); return; }", '', testOpenClose);
  mustFail('the DOM flag alone is trusted (a stub defaulting to hidden:false reads as open)',
    "return !!panel && panel.hidden === false && !!this._cashPanelOpen;", 'return !!panel && panel.hidden === false;', testOpenClose);

  console.log('PASS: retail_cash_panel_test.js');
}

main();
