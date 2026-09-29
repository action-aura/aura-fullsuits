/**
 * retail_p1a_return_toast_test.js -- FINAL polish N4.
 *
 * The "Return processed" toast was edited once and lost the space after its
 * middle dot: "... RET-000007 ·Refund: 10.000". Nothing asserted the toast's
 * text, so nothing noticed. This drives the real _saveReturn with a stubbed
 * server answer and reads the exact text handed to showToast.
 *
 * Asserted:
 *   - the dot has a space on BOTH sides (the bug was the trailing one);
 *   - the return number stays wrapped in the isolate marks (U+2066 / U+2069) so
 *     an Arabic toast cannot reorder it;
 *   - the real payout (tender_refund_amount) is shown, not refund_amount;
 *   - the optional "(+... credit forgiven)" suffix is still appended, after the
 *     payout, so a fix to the dot did not disturb the tail.
 *
 * Run: node products/retail/tests/retail_p1a_return_toast_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'subsystem-retail.js'), 'utf8');

function stubEl() {
  return {
    innerHTML: '', textContent: '', value: '', style: {}, dataset: {},
    appendChild() {}, getAttribute() { return null; }, setAttribute() {},
    querySelector() { return stubEl(); }, querySelectorAll() { return []; },
    getContext() { return {}; }, addEventListener() {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    parentElement: { innerHTML: '', style: {}, appendChild() {} },
  };
}

async function runSaveReturn(serverData) {
  const toasts = [];
  const sandbox = {
    console: { error() {}, warn() {}, log() {} },
    t: (s) => s, setTimeout, clearTimeout,
    fetch: () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: 'success', data: {} }) }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    SubsystemApp: {
      active: 'retail', currentSection: 'returns',
      showToast(msg, kind) { toasts.push({ msg, kind }); },
      hasCapability() { return true; },
    },
    document: {
      getElementById(id) {
        if (id === 'ret-refund-method') return { value: 'cash' };
        if (id === 'ret-reason') return { value: 'Customer return' };
        return stubEl();
      },
      createElement() { return stubEl(); },
      querySelector() { return stubEl(); },
      querySelectorAll(sel) {
        if (sel === '.ret-item-cb:checked') return [{ dataset: { idx: '0', pid: 'p1', price: '10' } }];
        if (sel === '.ret-item-qty') return [{ value: '1' }];
        return [];
      },
      head: { appendChild() {} }, body: { appendChild() {} },
      documentElement: { getAttribute() { return null; } }, addEventListener() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: 'subsystem-retail.js' });
  const RS = sandbox.RetailSystem;
  RS._returnSaleId = 'sale-1';
  RS._returnSaleOriginalMethod = null;
  RS._post = async () => ({ status: 'success', data: serverData });
  RS._closeModalOverlay = () => {};
  RS._loadReturns = () => {};
  await RS._saveReturn();
  return { toasts, fmt: (n) => RS._fmt(n) };
}

const cases = [];
function test(name, fn) { cases.push([name, fn]); }

test('the return toast has a space on both sides of the middle dot, isolates the number, shows the real payout', async () => {
  const { toasts, fmt } = await runSaveReturn({ return_number: 'RET-000007', refund_amount: 12, tender_refund_amount: 10 });
  assert.strictEqual(toasts.length, 1, 'expected exactly one toast');
  const m = toasts[0].msg;
  assert.strictEqual(toasts[0].kind, 'success');
  assert.ok(m.includes('⁦RET-000007⁩'), `the return number lost its isolate marks: ${JSON.stringify(m)}`);
  assert.ok(m.includes('⁩ · Refund: '), `the middle dot must sit between spaces, got ${JSON.stringify(m)}`);
  assert.ok(m.endsWith(`Refund: ${fmt(10)}`), `the payout shown is not the tender refund: ${JSON.stringify(m)}`);
  assert.ok(!m.includes(fmt(12)), 'the toast shows the full refund_amount instead of what left the till');
});

test('the credit-forgiven suffix is still appended after the payout', async () => {
  const { toasts, fmt } = await runSaveReturn({ return_number: 'RET-000008', refund_amount: 12, tender_refund_amount: 10, ar_forgiven_amount: 2 });
  const m = toasts[0].msg;
  assert.ok(m.includes('⁩ · Refund: '), `dot spacing lost in the forgiven variant: ${JSON.stringify(m)}`);
  assert.ok(m.endsWith(` (+${fmt(2)} credit forgiven)`), `the forgiven suffix is missing or moved: ${JSON.stringify(m)}`);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of cases) {
    try { await fn(); console.log(`PASS: ${name}`); }
    catch (err) { failed += 1; console.error(`FAIL: ${name}\n      ${err && err.stack || err}`); }
  }
  console.log(`\n=== ${cases.length - failed} passed, ${failed} failed, ${cases.length} total ===`);
  process.exit(failed ? 1 : 0);
})();
