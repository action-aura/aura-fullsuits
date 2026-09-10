/**
 * retail_loyalty_redemption_ui_test.js — the POS "Current Sale" loyalty-point
 * redemption control (launch-readiness wave 1, schema v27).
 *
 * The backend (retail_api.py's create_sale, GET /customers/<id>/loyalty, GET
 * /settings/credit) already ships and is exercised by
 * retail_loyalty_redemption_test.py. This file covers the ONE thing that
 * suite cannot see: the POS control itself, loaded for real through Node's
 * vm module (never reimplemented here) with a controllable fetch mock, the
 * same technique retail_surface_pos_test.js / retail_checkout_error_toast_
 * test.js already use for this build-step-free vanilla-JS frontend.
 *
 * Four states, matching the spec this control was built against:
 *   (a) loyalty_point_value = 0            -> control absent entirely
 *   (b) value set, Walk-in selected        -> control absent
 *   (c) value set, a real customer         -> control visible, balance shown
 *   (d) typing points shows the right money value, and "use max" never
 *       exceeds the balance or what the sale is worth in points
 *
 * Plus the two things a UI test can prove that a backend test cannot:
 *   * switching the customer selector resets any pending redemption to zero
 *     (including back to Walk-in) -- the backend has no opinion on this,
 *     it is purely a client-side contract.
 *   * the checkout payload carries the EXACT field name the backend reads
 *     (`points_redeemed`, an integer point COUNT -- retail_api.py's own
 *     "CLIENT-SUBMITTED INTENT ONLY" comment), and `amount_paid` is computed
 *     against the amount still due AFTER redemption, not the invoice total
 *     (retail_api.py's "THE CASH TRAP" comment) -- a wrong client value here
 *     would still be caught by create_sale's own re-derivation, but would
 *     show the cashier a false "change due" while the real sale netted the
 *     correct amount, which is a real cashier-facing bug this test would
 *     have caught.
 *
 * Run: node products/retail/tests/retail_loyalty_redemption_ui_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const FRONTEND_FILE = path.join(FRONTEND_DIR, 'subsystem-retail.js');

// ─────────────────────────────────────────────────────────────────────────────
// Harness — a stateful element stub (unlike retail_surface_i18n_test.js's
// static markup parse, this file drives LIVE interaction: selecting a
// customer, typing into the redeem input, clicking "Max"), and a fetch mock
// whose responses this file controls per-scenario.
// ─────────────────────────────────────────────────────────────────────────────

function makeElementStub(overrides) {
  const classes = [];
  const el = Object.assign({
    innerHTML: '',
    textContent: '',
    value: '',
    id: '',
    disabled: false,
    style: {},
    classList: {
      add(c) { if (!classes.includes(c)) classes.push(c); },
      remove(c) { const i = classes.indexOf(c); if (i !== -1) classes.splice(i, 1); },
      toggle(c, on) { if (on) this.add(c); else this.remove(c); },
      contains(c) { return classes.includes(c); },
    },
    appendChild() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelectorAll() { return []; },
    addEventListener() {},
    focus() {},
    // _showReceipt()'s auto-dismiss timer calls this on the overlay it
    // creates via document.createElement() -- irrelevant to what this file
    // checks, but stubbed so that background timer (8s, real wall-clock)
    // never throws once it fires.
    remove() {},
  }, overrides);
  return el;
}

/**
 * Loads the REAL subsystem-retail.js into a sandbox with a controllable
 * fetch mock. `settings.loyaltyPointValue` / `settings.customers` /
 * `settings.balances` (a map of customer id -> point balance) drive what the
 * mocked GET /settings/credit, GET /customers and GET /customers/<id>/loyalty
 * responses carry; every other route the till calls on mount (products,
 * categories, held-sales, promotions/active, settings/tax) answers a generic
 * empty-success response so _loadPOSData() never throws.
 */
function loadRetailSystem(settings) {
  const opts = settings || {};
  const loyaltyPointValue = opts.loyaltyPointValue != null ? opts.loyaltyPointValue : '0';
  const customers = opts.customers || [];
  const balances = opts.balances || {};

  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');
  const els = Object.create(null);
  const posts = []; // every POST body, so the checkout-payload test can inspect it
  const toasts = [];

  const getEl = (id) => {
    if (!els[id]) els[id] = makeElementStub({ id });
    return els[id];
  };

  function fetchImpl(url) {
    const has = (needle) => url.indexOf(needle) !== -1;
    let body = { status: 'success', data: [] };
    if (has('/settings/credit')) {
      body = { status: 'success', data: { base_currency: 'JOD', loyalty_point_value: loyaltyPointValue } };
    } else if (has('/settings/tax')) {
      body = { status: 'success', data: { tax_calculation_mode: 'after_discount', base_currency: 'JOD', currency_decimals: 3, currency_symbol: 'JD' } };
    } else if (/\/customers\/[^/]+\/loyalty/.test(url)) {
      const custId = url.match(/\/customers\/([^/]+)\/loyalty/)[1];
      body = { status: 'success', data: { customer_id: custId, balance: balances[custId] || 0, entries: [] } };
    } else if (has('/customers')) {
      body = { status: 'success', data: customers };
    } else {
      body = { status: 'success', data: [] };
    }
    return Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve(body) });
  }

  function postImpl(url, opts2) {
    const parsedBody = opts2 && opts2.body ? JSON.parse(opts2.body) : null;
    posts.push({ url, body: parsedBody });
    // A generic success is enough for every test here -- none of them assert
    // on the till's post-sale UI, only on the payload it sent.
    return Promise.resolve({
      status: 200, ok: true,
      json: () => Promise.resolve({ status: 'success', data: { sale_number: 'S-TEST', total: (parsedBody && parsedBody.total) || 0, change: 0, lines: [] } }),
    });
  }

  const sandbox = {
    console,
    t: (s) => s,
    fetch(url, fetchOpts) {
      if (fetchOpts && fetchOpts.method === 'POST') return postImpl(url, fetchOpts);
      return fetchImpl(url);
    },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem: () => {} },
    // _showReceipt()'s auto-dismiss timer only -- never fired here on
    // purpose (see the process.exit(0) at the end of main()), so this is a
    // deliberate no-op rather than a real timer.
    setTimeout: () => 0,
    document: {
      activeElement: null,
      getElementById: getEl,
      createElement() { return makeElementStub({}); },
      querySelector() { return makeElementStub({}); },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      // _showReceipt() (the post-checkout modal) appends straight to
      // document.body -- irrelevant to what this file checks (the payload,
      // asserted before this ever runs), but stubbed so _checkout()'s own
      // try/catch has nothing to swallow-and-log for an unrelated reason.
      body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
    SubsystemApp: {
      active: 'retail',
      showToast(msg, type) { toasts.push({ msg, type }); },
      hasCapability: () => true,
      checkAuthAndSetup() {},
    },
  };
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');

  return { RetailSystem: sandbox.RetailSystem, els, posts, toasts, getEl };
}

/** Mounts the POS screen and waits for _loadPOSData()'s Promise.all to settle. */
async function mountPOS(settings) {
  const ctx = loadRetailSystem(settings);
  ctx.RetailSystem._renderPOS(makeElementStub({}));
  // _loadPOSData() is fire-and-forget from _renderPOS(); give its
  // Promise.all (and the _onCustomerChange() it now chains afterward) real
  // microtask turns to resolve against the synchronous fetch mock above.
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  return ctx;
}

// Mirrors what _addToCart()/_renderCart() always do together in the real
// app: a cart mutation is never left un-recalculated. Doing the same here
// keeps `_currentTotals` (which _useMaxLoyaltyPoints() reads) in sync,
// exactly as it always is by the time a cashier could click "Max" for real.
function addCartLine(rs, overrides) {
  rs._cart.push(Object.assign({
    product_id: 'p1', name: 'Widget', quantity: 1,
    unit_price: 100, tax_rate: 0, line_total: 100,
  }, overrides));
  rs._recalc();
}

async function main() {
  let passed = 0;
  const fail = (msg) => { throw new Error(msg); };

  // ── (a) loyalty_point_value = 0 -> control absent entirely, even with a
  // real customer selected. No empty row, no disabled stub: the section
  // stays display:none. ────────────────────────────────────────────────────
  {
    const ctx = await mountPOS({ loyaltyPointValue: '0', customers: [{ id: 'cu1', name: 'Ahmad', phone: '' }], balances: { cu1: 500 } });
    ctx.RetailSystem._loyaltyPointValue = 0; // belt-and-braces against a fetch-ordering fluke
    ctx.els['pos-customer'].value = 'cu1';
    await ctx.RetailSystem._onCustomerChange();
    assert.strictEqual(
      ctx.els['pos-loyalty-section'].style.display, 'none',
      '(a) loyalty_point_value=0 must hide the redemption section even with a real customer selected'
    );
    assert.strictEqual(ctx.RetailSystem._loyaltyRedemptionEnabled(), false, '(a) _loyaltyRedemptionEnabled() must read false when the shop never opened this setting');
    passed++;
    console.log('PASS: (a) loyalty_point_value=0 -> control absent entirely');
  }

  // ── (b) value set, Walk-in selected -> control absent. Also proves the
  // reset-to-Walk-in half of "switching customer resets any pending
  // redemption": select a real customer, type a redemption, THEN switch back
  // to Walk-in and confirm both the input and the clamped points return to
  // zero. ─────────────────────────────────────────────────────────────────
  {
    const ctx = await mountPOS({ loyaltyPointValue: '0.5', customers: [{ id: 'cu1', name: 'Ahmad', phone: '' }], balances: { cu1: 500 } });
    // Walk-in is the default selection at mount.
    assert.strictEqual(
      ctx.els['pos-loyalty-section'].style.display, 'none',
      '(b) a shop with redemption ON must still hide the control while Walk-in is selected'
    );

    // Select the real customer, type a redemption, confirm it is live.
    ctx.els['pos-customer'].value = 'cu1';
    await ctx.RetailSystem._onCustomerChange();
    addCartLine(ctx.RetailSystem);
    ctx.els['pos-loyalty-redeem'].value = '10';
    ctx.RetailSystem._recalc();
    assert.ok(ctx.RetailSystem._loyaltyRedeemPoints > 0, 'sanity: a redemption must be live before testing that switching back to Walk-in clears it');

    // Switch back to Walk-in.
    ctx.els['pos-customer'].value = '';
    await ctx.RetailSystem._onCustomerChange();
    assert.strictEqual(ctx.els['pos-loyalty-section'].style.display, 'none', '(b) switching back to Walk-in must hide the control again');
    assert.strictEqual(ctx.els['pos-loyalty-redeem'].value, '0', '(b) switching back to Walk-in must reset the redeem input to 0');
    assert.strictEqual(ctx.RetailSystem._loyaltyRedeemPoints, 0, '(b) switching back to Walk-in must reset the clamped redemption to 0');
    passed++;
    console.log('PASS: (b) value set + Walk-in -> control absent, and switching back to Walk-in resets any pending redemption');
  }

  // ── (c) value set, real customer -> control visible, balance shown. ─────
  {
    const ctx = await mountPOS({ loyaltyPointValue: '0.5', customers: [{ id: 'cu1', name: 'Ahmad', phone: '' }], balances: { cu1: 240 } });
    ctx.els['pos-customer'].value = 'cu1';
    await ctx.RetailSystem._onCustomerChange();
    assert.strictEqual(ctx.els['pos-loyalty-section'].style.display, '', '(c) the redemption section must be shown for a real customer once the shop has opted in');
    assert.strictEqual(ctx.els['pos-loyalty-balance'].textContent, '240', "(c) the customer's balance must be shown verbatim (from GET /customers/<id>/loyalty's own `balance` field)");
    passed++;
    console.log('PASS: (c) value set + real customer -> control visible, balance shown (240)');
  }

  // ── (d) typing points shows the right money value; "use max" never
  // exceeds the balance OR what the sale is worth in points. Two sub-cases:
  // sale-constrained (balance is generous, the CART is the limit) and
  // balance-constrained (the cart could absorb more than the customer has).
  // Also proves typing MORE than either limit is silently clamped in the
  // computed total (never rejected, never overshoots) -- the server remains
  // the authority; this is the client PREVIEW staying honest. ─────────────
  {
    const ctx = await mountPOS({ loyaltyPointValue: '0.5', customers: [{ id: 'cu1', name: 'Ahmad', phone: '' }], balances: { cu1: 500 } });
    ctx.els['pos-customer'].value = 'cu1';
    await ctx.RetailSystem._onCustomerChange();
    addCartLine(ctx.RetailSystem, { line_total: 100, unit_price: 100 }); // $100 sale, 0% tax/discount -> total=100

    // Sale-constrained: balance (500 pts = $250) exceeds what a $100 sale is
    // worth in points (200 pts). "Max" must land on 200, never 500.
    ctx.RetailSystem._useMaxLoyaltyPoints();
    assert.strictEqual(ctx.els['pos-loyalty-redeem'].value, '200', '(d) "Max" must be sale-constrained (200 pts = the full $100 sale), not the $250-worth balance');
    assert.strictEqual(ctx.RetailSystem._currentTotals.loyaltyValue, 100, '(d) redeeming the sale-constrained max must be worth exactly the sale total ($100)');
    assert.strictEqual(ctx.RetailSystem._currentTotals.amountDue, 0, '(d) a sale fully covered by points must show zero still due');
    assert.strictEqual(ctx.els['pos-loyalty-value-row'].style.display, '', '(d) the "Points Redeemed" summary row must be shown once a redemption is applied');
    assert.strictEqual(ctx.els['pos-loyalty-value'].textContent, ctx.RetailSystem._moneyDigits(100), '(d) the summary row must show the correct money value as it is typed');

    // Typing MORE than the sale is worth (9999) must clamp the COMPUTED
    // value, not reject the keystroke or overshoot the total.
    ctx.els['pos-loyalty-redeem'].value = '9999';
    ctx.RetailSystem._recalc();
    assert.ok(ctx.RetailSystem._loyaltyRedeemPoints <= 200, '(d) an over-typed redemption must clamp to at most what the sale is worth in points');
    assert.ok(ctx.RetailSystem._currentTotals.loyaltyValue <= 100, '(d) an over-typed redemption must never exceed the sale total in money');

    // Balance-constrained: a poorer customer (30 pts = $15) on the same $100
    // sale. "Max" must land on 30, never the 200 the sale could otherwise
    // absorb.
    const ctx2 = await mountPOS({ loyaltyPointValue: '0.5', customers: [{ id: 'cu2', name: 'Sara', phone: '' }], balances: { cu2: 30 } });
    ctx2.els['pos-customer'].value = 'cu2';
    await ctx2.RetailSystem._onCustomerChange();
    addCartLine(ctx2.RetailSystem, { line_total: 100, unit_price: 100 });
    ctx2.RetailSystem._useMaxLoyaltyPoints();
    assert.strictEqual(ctx2.els['pos-loyalty-redeem'].value, '30', '(d) "Max" must be balance-constrained (30 pts) when the balance is smaller than what the sale could absorb');
    assert.strictEqual(ctx2.RetailSystem._currentTotals.loyaltyValue, 15, '(d) a 30-point redemption at $0.50/point must be worth exactly $15');

    // Typing more than the balance (e.g. 999) must also clamp to the balance.
    ctx2.els['pos-loyalty-redeem'].value = '999';
    ctx2.RetailSystem._recalc();
    assert.strictEqual(ctx2.RetailSystem._loyaltyRedeemPoints, 30, '(d) an over-typed redemption must clamp to the customer\'s actual balance');

    passed += 2;
    console.log('PASS: (d) typed/maxed redemption values are correct and never exceed the sale or the balance (both constraint directions)');
  }

  // ── Checkout payload: exact field name, and amount_paid reflects what is
  // still due AFTER redemption (THE CASH TRAP), not the invoice total. ────
  {
    const ctx = await mountPOS({ loyaltyPointValue: '0.5', customers: [{ id: 'cu1', name: 'Ahmad', phone: '' }], balances: { cu1: 500 } });
    ctx.els['pos-customer'].value = 'cu1';
    await ctx.RetailSystem._onCustomerChange();
    addCartLine(ctx.RetailSystem, { line_total: 100, unit_price: 100 });
    ctx.els['pos-loyalty-redeem'].value = '40'; // 40 pts * $0.50 = $20
    ctx.RetailSystem._recalc();
    assert.strictEqual(ctx.RetailSystem._currentTotals.loyaltyValue, 20, 'sanity: a 40-point redemption at $0.50/point must be worth $20 before checkout');

    await ctx.RetailSystem._checkout();
    assert.strictEqual(ctx.posts.length, 1, 'checkout must POST exactly once');
    const payload = ctx.posts[0].body;
    assert.strictEqual(payload.points_redeemed, 40, 'the checkout payload must carry the EXACT field name create_sale (retail_api.py) reads: `points_redeemed`, an integer point count');
    assert.strictEqual(payload.total, 100, 'the invoice total sent to the server must be the full sale total, unaffected by the redemption (create_sale never adjusts it either)');
    assert.strictEqual(payload.amount_paid, 80, 'amount_paid must be computed against what is still due AFTER the $20 redemption (100-20=80), not the $100 invoice total -- sending the full total here would make the server report a phantom $20 "change due" (THE CASH TRAP)');
    passed++;
    console.log('PASS: checkout payload carries points_redeemed=40 and amount_paid=80 (the post-redemption amount due), not the raw $100 total');
  }

  // ── Touch target: the two controls this feature added both clear the
  // 44px floor on both axes, via the SAME classes (not new CSS) the
  // Discount input and the Held button already use and which main.css's
  // "TOUCH, SECOND AXIS" block already covers -- retail_design_focus_test.js
  // proves this against the real CSS cascade for the whole corpus; this is
  // a narrower, in-file cross-check that the two new controls carry those
  // exact class names. ──────────────────────────────────────────────────
  {
    const ctx = await mountPOS({ loyaltyPointValue: '0.5', customers: [{ id: 'cu1', name: 'Ahmad', phone: '' }], balances: { cu1: 500 } });
    // Read the RENDERED markup (not the live element stubs, which do not
    // carry class="..." strings) via the POS host's innerHTML.
    const host = makeElementStub({});
    ctx.RetailSystem._renderPOS(host);
    assert.ok(host.innerHTML.includes('id="pos-loyalty-redeem"') && host.innerHTML.includes('class="pos-mini-input"'),
      'the redeem-points input must carry .pos-mini-input, which main.css declares at min 44px on BOTH axes');
    assert.ok(host.innerHTML.includes('id="pos-loyalty-max-btn"') && /class="ret-btn ret-btn-ghost ret-btn-sm"/.test(host.innerHTML),
      'the Max button must carry .ret-btn.ret-btn-ghost, which main.css declares at min 44px on BOTH axes regardless of the -sm modifier');
    passed++;
    console.log('PASS: both new controls carry the classes main.css floors at 44px on both axes (input.pos-mini-input, button.ret-btn.ret-btn-ghost)');
  }

  console.log(`\nretail_loyalty_redemption_ui_test.js — ${passed} checks passed`);
  // _showReceipt()'s own auto-dismiss timer (real 8s setTimeout, unrelated to
  // anything this file checks) would otherwise hold the process open long
  // after every assertion above has already run.
  process.exit(0);
}

main().catch((err) => {
  console.error('FAIL: retail_loyalty_redemption_ui_test.js');
  console.error(err);
  process.exitCode = 1;
});
