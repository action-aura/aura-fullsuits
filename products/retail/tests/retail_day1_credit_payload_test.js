/**
 * DAY-1a regression: a sale ON ACCOUNT must not tell the server the customer
 * already paid it in full.
 *
 * Bug (measured in the day-in-shop run, products/retail/frontend/
 * subsystem-retail.js `_checkout`): `amount_paid` was `amountDue` for EVERY
 * non-cash tender, 'credit' included. The server computed balance_due = due -
 * paid = 0, charged the customer nothing, and recorded the "paid" amount as a
 * cash deposit that fed the drawer's cash_sales. This till has no
 * upfront-deposit field for credit, so a credit sale must send 0.
 *
 * Checks BOTH directions (ENGINEERING.md): credit sends amount_paid 0, and
 * cash / card still send the full amount (the change is not collateral damage
 * of the fix). Loads the REAL subsystem-retail.js via vm, same harness as
 * retail_loyalty_redemption_ui_test.js.
 *
 * Run: node products/retail/tests/retail_day1_credit_payload_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

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


async function checkoutPayload(method, customerId) {
  const ctx = await mountPOS({ loyaltyPointValue: '0', customers: [{ id: 'cu1', name: 'Ahmad', phone: '' }], balances: {} });
  if (customerId) {
    ctx.els['pos-customer'].value = customerId;
    await ctx.RetailSystem._onCustomerChange();
  }
  addCartLine(ctx.RetailSystem, { line_total: 100, unit_price: 100 });
  // Set the tender directly: the credit tile's own gating (customer credit
  // mode) is not what this file checks, the payload is.
  ctx.RetailSystem._paymentMethod = method;
  ctx.els['pos-tendered'].value = '';
  await ctx.RetailSystem._checkout();
  const salesPosts = ctx.posts.filter(p => /\/api\/sub\/retail\/sales$/.test(p.url));
  assert.strictEqual(salesPosts.length, 1, `checkout must POST /sales once (saw ${salesPosts.length}; toasts ${JSON.stringify(ctx.toasts)})`);
  return salesPosts[0].body;
}

async function main() {
  const credit = await checkoutPayload('credit', 'cu1');
  assert.strictEqual(credit.payment_method, 'credit');
  assert.strictEqual(credit.amount_paid, 0,
    'a sale on account must send amount_paid 0 (no deposit field exists); sending the whole amount charges the customer nothing');
  console.log('PASS: credit sale sends amount_paid=0');

  const card = await checkoutPayload('card', null);
  assert.strictEqual(card.amount_paid, 100, 'a card sale still sends the full amount due');
  console.log('PASS: card sale still sends amount_paid=100');

  const cash = await checkoutPayload('cash', null);
  assert.strictEqual(cash.amount_paid, 100, 'a cash sale with nothing tendered still sends the full amount due');
  console.log('PASS: cash sale still sends amount_paid=100');
  process.exit(0);
}

main().catch((err) => {
  console.error('FAIL: retail_day1_credit_payload_test.js');
  console.error(err);
  process.exit(1);
});
