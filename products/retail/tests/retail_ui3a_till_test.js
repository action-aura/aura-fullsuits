/**
 * retail_ui3a_till_test.js -- the TILL half of UI-3a (amount discounts, "make the total X",
 * per-line price / discount, manager PIN). Server half: retail_ui3a_authority_test.py and
 * retail_ui3a_cart_discount_test.py. Loads the REAL subsystem-retail.js (AURA_SR_SRC points a
 * mutation proof at a broken copy).
 *
 * The money maths is held to the Python by a GOLDEN FILE (retail_ui3a_golden.json) that the Python
 * test regenerates and compares: the till's _cartDiscPlan must reproduce, for ~120 seeded carts in
 * both tax modes, the server's per-line shares (minor units), the resulting total, the smallest
 * exact discount for a target total, and the nearest reachable totals when a target is a miss.
 *
 * Run: node products/retail/tests/retail_ui3a_till_test.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const SRC_PATH = process.env.AURA_SR_SRC || path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');
const source = fs.readFileSync(SRC_PATH, 'utf8');
const GOLDEN = JSON.parse(fs.readFileSync(path.join(__dirname, 'retail_ui3a_golden.json'), 'utf8'));

function loadRetailSystem() {
  const sandbox = {};
  sandbox.window = sandbox;
  sandbox.navigator = { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' };
  sandbox.t = (s) => s;
  sandbox.console = console;
  sandbox.setTimeout = setTimeout; sandbox.clearTimeout = clearTimeout;
  sandbox.localStorage = { getItem: () => null, setItem: () => {} };
  sandbox.performance = { now: () => 10000 };
  const toasts = [];
  const els = Object.create(null);
  const getEl = (id) => {
    if (!els[id]) {
      els[id] = {
        id, value: '', disabled: false, textContent: '', style: {}, tagName: 'DIV', focus() {}, select() {},
        setAttribute() {}, removeAttribute() {}, classList: { toggle() {}, add() {}, remove() {} },
      };
    }
    return els[id];
  };
  sandbox.document = {
    get activeElement() { return null; },
    getElementById: getEl,
    querySelectorAll: () => [],
    addEventListener: () => {},
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: SRC_PATH });
  const RS = sandbox.RetailSystem;
  assert.ok(RS, 'RetailSystem failed to load from subsystem-retail.js');
  sandbox.SubsystemApp = { active: 'retail', showToast: (msg, type) => { toasts.push({ msg, type }); },
                           hasCapability: () => true };
  RS._currencyDecimals = 3;
  RS._section = 'pos';
  RS._cart = [];
  RS._taxMode = 'after_discount';
  RS._paymentMethod = 'cash';
  RS._currentTotals = { subtotal: 0, discount: 0, tax: 0, total: 0 };
  RS._products = [];
  RS._clearCart = () => {};
  RS._applyLocalStockDecrement = () => {};
  RS._showReceipt = () => {};
  RS._renderCart = () => {};
  RS._broadcastDisplayState = () => {};
  RS._dropStaleTenderParts = () => {};
  RS._syncTenderUI = () => {};
  RS._calcChange = () => {};
  RS._postToCustomerDisplay = () => {};
  RS._refocusScan = () => {};
  const posted = [];
  RS._post = async (url, body) => { posted.push({ url, body }); return { status: 'success', data: { lines: [], total: body.total } }; };
  return { RS, sandbox, getEl, toasts, posted };
}

const LINE = (id, name, qty, price, rate) => ({
  product_id: id, name, quantity: qty, unit_price: price, tax_rate: rate, line_total: qty * price, max_stock: 999,
});
// the brief's cart: 3 lines, 16% tax, subtotal 9.181, total 10.650
const BRIEF = () => [LINE('a', 'A', 1, 3.5, 16), LINE('b', 'B', 2, 2.75, 16), LINE('c', 'C', 1, 0.181, 16)];
const settle = () => new Promise((r) => setImmediate(r));

async function run() {
  // 1. golden vectors -----------------------------------------------------------------------
  {
    const { RS } = loadRetailSystem();
    let hit = 0, miss = 0;
    GOLDEN.forEach((rec, n) => {
      RS._taxMode = rec.tax_mode;
      const lines = rec.gross_minor.map((g, i) => ({ grossM: g, rate: rec.rates[i] }));
      const plan = RS._cartDiscPlan(lines, { mode: rec.kind, value: rec.value_minor / 1000 });
      const tag = `vector ${n} (${rec.kind} ${rec.value_minor}, ${rec.tax_mode})`;
      if (rec.expect.ok) {
        assert.ok(plan.ok, `${tag}: the till could not reach what the server reaches (${plan.code})`);
        assert.deepStrictEqual(plan.shares, rec.expect.shares, `${tag}: per-line shares differ from the server's`);
        assert.strictEqual(plan.totalM, rec.expect.total_minor, `${tag}: total differs from the server's`);
        if (rec.kind === 'target') assert.strictEqual(plan.discountM, rec.expect.discount_minor, `${tag}: not the smallest exact discount`);
        hit++;
      } else if (rec.kind === 'target') {
        assert.ok(!plan.ok && plan.code === 'TARGET_UNREACHABLE', `${tag}: the server cannot reach it but the till claims to`);
        assert.strictEqual(plan.nearestBelowM == null ? null : plan.nearestBelowM, rec.expect.nearest_below_minor, `${tag}: nearest below`);
        assert.strictEqual(plan.nearestAboveM == null ? null : plan.nearestAboveM, rec.expect.nearest_above_minor, `${tag}: nearest above`);
        miss++;
      } else {
        assert.ok(!plan.ok, `${tag}: the server refuses an amount the till accepts`);
        miss++;
      }
    });
    assert.ok(hit >= 100 && miss >= 1, `golden file too thin: ${hit} hits, ${miss} misses`);
    console.log(`PASS: ${hit} hits + ${miss} misses of the golden file match the server to the minor unit`);
  }

  // 2. the pad's edit rules (shared with the cash pad) ---------------------------------------
  {
    const { RS } = loadRetailSystem();
    const E = (v, k, dp) => RS._padEdit(v, k, dp == null ? 3 : dp);
    assert.strictEqual(E('', '5'), '5');
    assert.strictEqual(E('0', '5'), '5');                 // no leading zero
    assert.strictEqual(E('', '.'), '0.');
    assert.strictEqual(E('1.5', '.'), '1.5');             // one dot only
    assert.strictEqual(E('1.234', '5'), '1.234');         // a 4th decimal is ignored, never rounded
    assert.strictEqual(E('', '00'), '');
    assert.strictEqual(E('12', '00'), '1200');
    assert.strictEqual(E('12', 'back'), '1');
    assert.strictEqual(E('5', '.', 0), '5');              // a 0-decimal currency takes no dot
    assert.strictEqual(E('123456789', '1'), '123456789'); // length cap
    console.log('PASS: the keypad edit rules');
  }

  // 3. the till preview of an applied target total equals the server's figure -----------------
  {
    const { RS, getEl } = loadRetailSystem();
    RS._cart = BRIEF();
    RS._recalc();
    const M = (x) => Math.round(x * 1000);                      // float noise is the till's existing way; judge in fils
    assert.strictEqual(M(RS._currentTotals.total), 10650, 'baseline cart is the brief cart');
    RS._cartDisc = { mode: 'target', value: 10, reason: 'Round down', sig: RS._cartDiscSig() };
    RS._recalc();
    assert.strictEqual(M(RS._currentTotals.total), 10000, 'target 10.000 did not land exactly in the till preview');
    assert.ok(RS._cartDisc, 'the discount was cleared by its own recalc');
    assert.strictEqual(getEl('pos-disc').disabled, true, 'the % box must be locked while a cart discount is applied');
    RS._cartDisc = { mode: 'amount', value: 0.65, reason: 'Damaged', sig: RS._cartDiscSig() };
    RS._recalc();
    assert.strictEqual(M(RS._currentTotals.discount), 650);
    assert.strictEqual(M(RS._currentTotals.subtotal), 9181);
    console.log('PASS: applied target / amount discounts price the cart exactly like the server');
  }

  // 4. a cart change clears the discount with a word, never re-spreads it ----------------------
  {
    const { RS, toasts } = loadRetailSystem();
    RS._cart = BRIEF();
    RS._cartDisc = { mode: 'amount', value: 0.65, reason: 'Damaged', sig: RS._cartDiscSig() };
    RS._recalc();
    RS._cart[0].quantity = 2; RS._cart[0].line_total = 7;
    RS._recalc();
    assert.strictEqual(RS._cartDisc, null, 'a discount survived a cart change');
    assert.ok(toasts.some((t) => /cart changed/.test(t.msg)), JSON.stringify(toasts));
    assert.strictEqual(Math.round(RS._currentTotals.discount * 1000), 0);
    console.log('PASS: a changed cart clears the amount discount and says so');
  }

  // 5. checkout: a cashier over the limit is asked for a manager, and the payload carries it ----
  {
    const ctx = loadRetailSystem();
    const { RS, posted } = ctx;
    ctx.sandbox.SubsystemApp.hasCapability = (c) => c !== 'retail.discount';
    RS._cashierDiscountLimit = 5;
    RS._cart = BRIEF();
    RS._cartDisc = { mode: 'target', value: 10, reason: 'Round down', sig: RS._cartDiscSig() };
    RS._recalc();
    assert.strictEqual(RS._saleNeedsManager(), true, '6.1% against a 5% limit must need a manager');
    let asked = 0;
    RS._askManagerApproval = async () => { asked++; return { approver: 'mgr-uid', pin: '4321' }; };
    await RS._checkout(); await settle();
    assert.strictEqual(asked, 1, 'the manager dialog was not raised');
    assert.strictEqual(posted.length, 1);
    const body = posted[0].body;
    assert.strictEqual(body.cart_target_total, 10);
    assert.strictEqual(body.discount_reason, 'Round down');
    assert.deepStrictEqual(body.approval, { approver: 'mgr-uid', pin: '4321' });
    assert.ok(body.items.every((i) => i.discount_pct === 0), 'per-line pct must be 0 beside a cart discount');
    assert.strictEqual('cart_discount_amount' in body, false);
    console.log('PASS: over the limit -> manager asked -> payload carries target, reason, approval');
  }

  // 6. cancelling the manager dialog sends nothing -----------------------------------------------
  {
    const ctx = loadRetailSystem();
    const { RS, posted } = ctx;
    ctx.sandbox.SubsystemApp.hasCapability = (c) => c !== 'retail.discount';
    RS._cashierDiscountLimit = 0;
    RS._cart = BRIEF();
    RS._cartDisc = { mode: 'amount', value: 0.65, reason: 'Damaged', sig: RS._cartDiscSig() };
    RS._recalc();
    RS._askManagerApproval = async () => null;
    await RS._checkout(); await settle();
    assert.strictEqual(posted.length, 0, 'a sale was sent without the manager approving it');
    console.log('PASS: a cancelled approval sends nothing');
  }

  // 7. a holder of retail.discount is never asked, and within-limit cashiers are not either ------
  {
    const ctx = loadRetailSystem();
    const { RS, posted } = ctx;
    RS._cart = BRIEF();
    RS._cartDisc = { mode: 'amount', value: 0.65, reason: 'Damaged', sig: RS._cartDiscSig() };
    RS._recalc();
    let asked = 0;
    RS._askManagerApproval = async () => { asked++; return null; };
    await RS._checkout(); await settle();
    assert.strictEqual(asked, 0, 'a holder of retail.discount was asked for a PIN');
    assert.strictEqual(posted.length, 1);
    assert.strictEqual(posted[0].body.cart_discount_amount, 0.65);
    assert.strictEqual('approval' in posted[0].body, false);

    const c2 = loadRetailSystem();
    c2.sandbox.SubsystemApp.hasCapability = (c) => c !== 'retail.discount';
    c2.RS._cashierDiscountLimit = 10;                 // 0.65 / 9.181 = 7.08% -> within
    c2.RS._cart = BRIEF();
    c2.RS._cartDisc = { mode: 'amount', value: 0.65, reason: 'Damaged', sig: c2.RS._cartDiscSig() };
    c2.RS._recalc();
    assert.strictEqual(c2.RS._saleNeedsManager(), false, 'within the limit must not ask a manager');
    console.log('PASS: holders and within-limit cashiers are never asked');
  }

  // 8. line menu: a line's own % and a price override travel, and an override needs a manager -----
  {
    const ctx = loadRetailSystem();
    const { RS, posted } = ctx;
    ctx.sandbox.SubsystemApp.hasCapability = (c) => c !== 'retail.discount';
    RS._cashierDiscountLimit = 10;
    RS._cart = BRIEF();
    RS._cart[0].unit_price_override = 3;
    RS._cart[1].disc_pct = 5;
    RS._recalc();
    assert.strictEqual(RS._lineGross(RS._cart[0]), 3, 'the override is the line price');
    assert.strictEqual(RS._saleNeedsManager(), true, 'a price override without the capability needs a manager');
    RS._askManagerApproval = async () => ({ approver: 'm', pin: '1234' });
    await RS._checkout(); await settle();
    const items = posted[0].body.items;
    assert.strictEqual(items[0].unit_price_override, 3);
    assert.strictEqual('unit_price_override' in items[1], false, 'a line without an override must not send the key');
    assert.strictEqual(items[1].discount_pct, 5, "a line's own % must win over the sale-wide box");
    assert.strictEqual(items[2].discount_pct, 0);
    console.log('PASS: line % and price override travel; override without the capability asks a manager');
  }

  // 9. round-down chips: reachable totals strictly below the current total -----------------------
  {
    const { RS } = loadRetailSystem();
    RS._cart = BRIEF();
    const chips = RS._dsRoundDownChips();
    assert.ok(chips.length >= 1, 'no round-down chips for 10.650');
    const baseM = RS._dsBaseTotalM();
    assert.strictEqual(baseM, 10650);
    chips.forEach((c) => {
      assert.ok(c.valueM < baseM, 'a chip is not below the total: ' + c.text);
      const plan = RS._cartDiscPlan(RS._cartDiscLines(), { mode: 'target', value: c.valueM / 1000 });
      assert.ok(plan.ok, 'a chip offers a total the server could not reach: ' + c.text);
    });
    assert.ok(chips.some((c) => c.valueM === 10000), 'the nearest whole 1.000 below 10.650 is missing: ' + JSON.stringify(chips));
    console.log('PASS: round-down chips are reachable and below the total: ' + chips.map((c) => c.text).join(', '));
  }

  // 10. the over-limit % chosen in the sheet is allowed through ONLY at that exact value -----------
  {
    const ctx = loadRetailSystem();
    const { RS, getEl } = ctx;
    ctx.sandbox.SubsystemApp.hasCapability = (c) => c !== 'retail.discount';
    RS._cashierDiscountLimit = 10;
    getEl('pos-disc').value = '15';
    assert.strictEqual(RS._discountOverLimit(), 10, 'typed over-limit % must still be capped');
    RS._pctApprovedValue = 15;
    assert.strictEqual(RS._discountOverLimit(), null, 'the sheet-approved value must pass');
    getEl('pos-disc').value = '16';
    assert.strictEqual(RS._discountOverLimit(), 10, 'any other over-limit value must still be capped');
    console.log('PASS: only the sheet-chosen % passes the over-limit cap');
  }
}

run().then(() => console.log('ALL PASS: retail_ui3a_till_test.js')).catch((e) => { console.error(e); process.exit(1); });
