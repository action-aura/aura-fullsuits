/**
 * retail_money5_till_rounding_test.js -- the till's MIRROR of cash rounding (MONEY-5, review #3).
 *
 * The SERVER rounds and is the authority (core/retail/cash_rounding.py, pinned in Python by
 * retail_money5_cash_rounding_test.py). The till only DISPLAYS it: the coins to take, the change
 * against them, the "Rounding -0.007" line, and the amount it sends as `amount_paid`. What is
 * pinned here:
 *
 *   1. OFF (the default / unreadable setting) changes nothing: due, change and the line are exactly
 *      the unrounded figures.
 *   2. 3.457 cash at 0.050 nearest -> 3.450 due, rounding -0.007; 'up' -> 3.500 (+0.043);
 *      'down' -> 3.450. Same answers as the Python module for the same inputs (a table).
 *   3. Only the CASH method rounds: a card total is untouched.
 *   4. Change is computed against the ROUNDED due (5.000 handed over -> 1.550, not 1.543).
 *   5. The rounding line text carries the sign.
 *
 * Mutations (each must turn a check RED): the method gate removed; the rule ignored; the sign dropped.
 *
 * Run: node products/retail/tests/retail_money5_till_rounding_test.js
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
    SubsystemApp: { active: 'retail', showToast() {}, _navigate() {}, hasCapability: () => true },
  };
  sandbox.window = sandbox;
  sandbox.Chart = function ChartStub() { return { destroy() {} }; };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: FRONTEND_FILE });
  const R = sandbox.RetailSystem;
  assert.ok(R && typeof R._roundCashMinor === 'function', 'RetailSystem._roundCashMinor is missing -- cash rounding is not in the build');
  R._currencyDecimals = 3;
  R._currencySymbol = 'JD';
  R._refocusScan = () => {};
  return { R, get };
}

function setup(src, cfg, method, total) {
  const { R, get } = load(src);
  R._cashRounding = cfg;
  R._paymentMethod = method;
  R._currentTotals = { total, amountDue: total };
  R._tenderParts = [];
  return { R, get };
}

function testTable(src) {
  const cases = [
    // [total, interval, rule, expectedDueMinor, expectedRoundingMinor]
    [3.457, 0.05, 'nearest', 3450, -7],
    [3.475, 0.05, 'nearest', 3500, 25],      // a tie goes up, like the server
    [3.457, 0.05, 'down', 3450, -7],
    [3.451, 0.05, 'up', 3500, 49],
    [3.457, 0.005, 'nearest', 3455, -2],
    [3.457, 0.01, 'nearest', 3460, 3],
    [3.457, 0.1, 'nearest', 3500, 43],
    [3.45, 0.05, 'up', 3450, 0],             // already a coin multiple
    [3.457, 0, 'nearest', 3457, 0],          // off
  ];
  for (const [total, interval, rule, due, rnd] of cases) {
    const { R } = setup(src, { interval, rule }, 'cash', total);
    assert.strictEqual(R._cashDueMinor(), due, `due for ${total} @${interval} ${rule}`);
    assert.strictEqual(R._cashRoundingMinor(), rnd, `rounding for ${total} @${interval} ${rule}`);
  }
  console.log('PASS: due and rounding match the server table (nearest ties up, down, up, off)');
}

function testOnlyCashRounds(src) {
  for (const method of ['card', 'mobile', 'transfer', 'credit', 'voucher']) {
    const { R } = setup(src, { interval: 0.05, rule: 'nearest' }, method, 3.457);
    assert.strictEqual(R._cashDueMinor(), 3457, `${method} must not round`);
    assert.strictEqual(R._cashRoundingMinor(), 0);
  }
  const { R } = setup(src, null, 'cash', 3.457);                   // setting never loaded
  assert.strictEqual(R._cashDueMinor(), 3457, 'no setting loaded = off');
  console.log('PASS: only the cash method rounds; no setting means off');
}

function testChangeAgainstRoundedDue(src) {
  const { R, get } = setup(src, { interval: 0.05, rule: 'nearest' }, 'cash', 3.457);
  get('pos-tendered').value = '5';
  R._calcChange();
  const row = get('pos-change-row');
  assert.notStrictEqual(row.style.display, 'none', 'the change row must show');
  assert.strictEqual(get('pos-change').textContent.replace(/[^0-9.]/g, ''), '1.550', 'change against the rounded due');
  const off = setup(src, { interval: 0, rule: 'nearest' }, 'cash', 3.457);
  off.get('pos-tendered').value = '5';
  off.R._calcChange();
  assert.strictEqual(off.get('pos-change').textContent.replace(/[^0-9.]/g, ''), '1.543', 'off: the old exact change');
  console.log('PASS: change is computed against the rounded due (1.550), and unchanged when off (1.543)');
}

function testRoundingText(src) {
  const { R } = load(src);
  assert.strictEqual(R._roundingText({ rounding_amount: -0.007 }), '−0.007');
  assert.strictEqual(R._roundingText({ rounding_amount: 0.043 }), '+0.043');
  assert.strictEqual(R._roundingText({ rounding_amount: null }), '');
  assert.strictEqual(R._roundingText({}), '');
  assert.strictEqual(R._roundingText({ rounding_amount: 0 }), '');
  console.log('PASS: the receipt line carries the sign and is empty when there was none');
}

function testShortGuardUsesTheRoundedRemainder(src) {
  // A card part of 5.000 is already taken; 5.457 is left; at 0.050 nearest the cash due is 5.450.
  const { R, get } = setup(src, { interval: 0.05, rule: 'nearest' }, 'cash', 10.457);
  R._tenderParts = [{ method: 'card', minor: 5000 }];
  get('pos-tendered').value = '5.45';
  let r = R._buildTenderPayload();
  assert.strictEqual(r.ok, true, 'exactly the rounded remainder must be accepted, got ' + JSON.stringify(r));
  get('pos-tendered').value = '5.4';
  r = R._buildTenderPayload();
  assert.strictEqual(r.ok, false, 'less than the rounded remainder is still short');
  const off = setup(src, { interval: 0, rule: 'nearest' }, 'cash', 10.457);
  off.R._tenderParts = [{ method: 'card', minor: 5000 }];
  off.get('pos-tendered').value = '5.45';
  assert.strictEqual(off.R._buildTenderPayload().ok, false, 'rounding off: 5.450 is short of 5.457 as before');
  console.log('PASS: the short-cash guard judges against the rounded remainder (and the old exact one when off)');
}

function mutant(src, from, to) {
  assert.strictEqual(src.split(from).length - 1, 1, `mutation anchor is not unique: ${from}`);
  return src.replace(from, to);
}

function mustFail(label, fn, src) {
  let failed = false;
  try { fn(src); } catch (e) { failed = true; }
  assert.ok(failed, `mutation NOT caught: ${label}`);
  console.log(`PASS: mutation caught -- ${label}`);
}

function main() {
  testTable(SRC);
  testOnlyCashRounds(SRC);
  testChangeAgainstRoundedDue(SRC);
  testRoundingText(SRC);
  testShortGuardUsesTheRoundedRemainder(SRC);
  mustFail('the cash-method gate removed (a card total rounds)',
    testOnlyCashRounds, mutant(SRC, "|| this._paymentMethod !== 'cash' ||", '||'));
  mustFail('the rule ignored (always nearest)',
    testTable, mutant(SRC, "const units = r.rule === 'down' ? Math.floor(q + 1e-9) : r.rule === 'up' ? Math.ceil(q - 1e-9) : Math.floor(q + 0.5 + 1e-9);",
      'const units = Math.floor(q + 0.5 + 1e-9);'));
  mustFail('change against the exact due again',
    testChangeAgainstRoundedDue, mutant(SRC, '    amountDue = this._fromMinor(this._roundCashMinor(this._toMinor(amountDue)));   // MONEY-5: coins, not fils\n', ''));
  mustFail('the sign dropped from the rounding text',
    testRoundingText, mutant(SRC, "return (v < 0 ? '−' : '+') + Math.abs(v)", "return '' + Math.abs(v)"));
  mustFail('the split guard back on the exact remainder',
    testShortGuardUsesTheRoundedRemainder, mutant(SRC, 'if (finalMinor < this._roundCashMinor(remaining)) {', 'if (finalMinor < remaining) {'));
  console.log('PASS: retail_money5_till_rounding_test.js');
}

main();
