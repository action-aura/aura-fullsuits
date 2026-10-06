/**
 * Aura Retail -- STOCK-BOOKS screens (crew B, B8).
 *
 * What the owner sees of the stock-in-the-books wave:
 *   1. the Inventory card on the trial balance, in four states -- ties (green),
 *      rounding (amber), NOT OPENED (neutral, never red), differs (red);
 *   2. a Cancel button on PENDING purchase orders only, and the server's
 *      "void the payment first" refusal shown in the shop's language;
 *   3. a Recall button on IN-TRANSIT transfers only;
 *   4. the money written off on a short transfer receipt.
 *
 * Each claim is mutation-proved at the bottom: the real subsystem-retail.js is
 * broken in one place at a time and the matching assertion must go red.
 *
 *   node products/retail/tests/retail_stock_books_ui_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const RETAIL_JS = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');
const SRC = fs.readFileSync(RETAIL_JS, 'utf8');

function makeStub(over) {
  return Object.assign({
    innerHTML: '', outerHTML: '', textContent: '', value: '', id: '', disabled: false,
    style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {},
    querySelectorAll() { return []; }, addEventListener() {}, removeEventListener() {},
    focus() {}, blur() {}, remove() {}, closest() { return null; }, getContext() { return {}; },
  }, over || {});
}

/** routes: [{match, method, payload}] -- first whose `match` is in the URL (and
    method equal) answers; anything else is a 404 the test will notice. */
function load(opts) {
  const o = opts || {};
  const calls = [];
  const toasts = [];
  const els = Object.create(null);
  const getEl = (id) => (els[id] || (els[id] = makeStub({ id })));
  const sandbox = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    t: (s) => s,
    fetch: (url, init) => {
      const method = ((init && init.method) || 'GET').toUpperCase();
      const u = String(url);
      calls.push({ method, url: u });
      const hit = (o.routes || []).find((r) => u.indexOf(r.match) !== -1 && (r.method || 'GET') === method);
      if (!hit) {
        return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({ status: 'error', message: 'unmapped ' + method + ' ' + u }) });
      }
      return Promise.resolve({ ok: hit.status ? hit.status < 400 : true, status: hit.status || 200, json: () => Promise.resolve(hit.payload) });
    },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    URLSearchParams,
    document: {
      activeElement: null,
      getElementById(id) { if (id === 'ret-styles') return null; return getEl(id); },
      createElement() { return makeStub(); },
      querySelector(sel) {
        const m = /^#([\w-]+)\s+tbody$/.exec(sel);
        if (m) return getEl(m[1] + '__tbody');
        return makeStub();
      },
      querySelectorAll() { return []; },
      head: { appendChild() {} }, body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
  };
  sandbox.SubsystemApp = {
    active: 'retail', _navigate() {}, role: 'admin',
    showToast(msg, kind) { toasts.push({ msg, kind }); },
    hasCapability: () => true,
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(o.source || SRC, sandbox, { filename: RETAIL_JS });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  sandbox.RetailSystem._confirm = async () => true;
  return { rs: sandbox.RetailSystem, calls, toasts, els };
}

async function settle() {
  for (let i = 0; i < 10; i++) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

const TB = (inventory) => ({
  success: true,
  data: {
    currency: 'JOD', accounts: [], total_debit: 0, total_credit: 0, is_balanced: true,
    subledger: { ar_gl: 0, ar_subledger: 0, ar_ties: true, ap_gl: 0, ap_subledger: 0, ap_ties: true },
    inventory,
  },
});
const INV = (status, extra) => Object.assign({
  inventory_gl: 12, stock_value: 12, in_transit_gl: 8, in_transit_stock: 8, difference: 0,
  unbooked_movements: 0, status, applicable: true,
}, extra || {});

async function inventoryHtml(inventory, source) {
  const ctx = load({ source, routes: [{ match: '/reports/trial-balance', payload: TB(inventory) }] });
  ctx.els['sub-content'] = makeStub({ id: 'sub-content' });
  await ctx.rs.render('trial-balance');
  await settle();
  return ctx.els['tb-inventory'] ? ctx.els['tb-inventory'].innerHTML : '';
}

async function poListHtml(source) {
  const ctx = load({
    source,
    routes: [{ match: '/purchase-orders', payload: { data: [
      { id: 1, po_number: 'PO-1', status: 'pending', total: 40, supplier_name: 'S' },
      { id: 2, po_number: 'PO-2', status: 'received', total: 40, supplier_name: 'S' },
      { id: 3, po_number: 'PO-3', status: 'cancelled', total: 40, supplier_name: 'S' },
    ] } }],
  });
  await ctx.rs._loadPurchaseOrders();
  await settle();
  return ctx.els['po-table__tbody'].innerHTML;
}

async function transferListHtml(source) {
  const ctx = load({
    source,
    routes: [{ match: '/stock-transfers', payload: { data: [
      { id: 'aaaaaaaa-1', status: 'pending', source_branch_name: 'A', destination_branch_name: 'B' },
      { id: 'bbbbbbbb-2', status: 'in_transit', source_branch_name: 'A', destination_branch_name: 'B' },
      { id: 'cccccccc-3', status: 'received', source_branch_name: 'A', destination_branch_name: 'B' },
    ] } }],
  });
  await ctx.rs._loadTransfers();
  await settle();
  return ctx.els['transfer-table__tbody'].innerHTML;
}

async function cancelRefusedToast(source) {
  const ctx = load({
    source,
    routes: [
      { match: '/purchase-orders/7/cancel', method: 'POST', status: 409,
        payload: { status: 'error', code: 'PO_HAS_PAYMENTS', message: 'raw server sentence' } },
      { match: '/purchase-orders', payload: { data: [] } },
    ],
  });
  await ctx.rs._cancelPO(7, 'PO-7');
  await settle();
  return ctx;
}

async function acceptNeedsSupplierToast(source) {
  const ctx = load({
    source,
    routes: [
      { match: '/reorder-requests/r1/accept', method: 'POST', status: 409,
        payload: { status: 'error', code: 'SUPPLIER_REQUIRED_FOR_CREDIT_PO', message: 'raw server sentence' } },
      { match: '/reorder-requests', payload: { data: [] } },
      { match: '/purchase-orders', payload: { data: [] } },
    ],
  });
  await ctx.rs._acceptReorderRequest('r1');
  await settle();
  return ctx;
}

async function shortReceiptToast(source) {
  const ctx = load({
    source,
    routes: [
      { match: '/stock-transfers/t1/receive', method: 'POST',
        payload: { status: 'success', data: { shortages: [{ product_id: 'p', quantity_sent: 4, quantity_received: 3, written_off_value: 2 }] } } },
      { match: '/stock-transfers', payload: { data: [] } },
    ],
  });
  ctx.rs._closeModalOverlay = () => {};
  await ctx.rs._confirmReceiveTransfer('t1');
  await settle();
  return ctx;
}

// ── the claims, each as a function of the source so the mutants reuse them ──

const CLAIMS = {
  async inventoryTiesIsGreen(src) {
    const h = await inventoryHtml(INV('ties'), src);
    assert.ok(/ret-badge-green[^>]*>Ties</.test(h), 'ties must be a green "Ties" badge: ' + h);
  },
  async inventoryRoundingIsAmber(src) {
    const h = await inventoryHtml(INV('rounding', { difference: 0.003 }), src);
    assert.ok(/ret-badge-yellow[^>]*>Rounding difference</.test(h), 'rounding must be amber: ' + h);
  },
  async notOpenedIsNeutralNeverRed(src) {
    const h = await inventoryHtml(INV('not_opened', { unbooked_movements: 3, inventory_gl: 0 }), src);
    assert.ok(/Stock not opened yet/.test(h), '"Stock not opened yet" must show: ' + h);
    assert.ok(!/ret-badge-red/.test(h), 'not opened must never be red: ' + h);
    assert.ok(!/Does Not Tie/.test(h), 'not opened must not say "Does Not Tie": ' + h);
  },
  async differsIsRed(src) {
    const h = await inventoryHtml(INV('differs', { difference: 5 }), src);
    assert.ok(/ret-badge-red[^>]*>Does Not Tie</.test(h), 'differs must be red: ' + h);
  },
  async cancelOnlyOnPendingPOs(src) {
    const h = await poListHtml(src);
    assert.strictEqual((h.match(/_cancelPO\(/g) || []).length, 1, 'exactly one Cancel (the pending PO): ' + h);
    assert.ok(/_cancelPO\(1,/.test(h), 'the Cancel belongs to PO 1 (pending)');
  },
  async recallOnlyOnInTransit(src) {
    const h = await transferListHtml(src);
    assert.strictEqual((h.match(/_recallTransfer\(/g) || []).length, 1, 'exactly one Recall: ' + h);
    assert.ok(/_recallTransfer\('bbbbbbbb-2'\)/.test(h), 'Recall belongs to the in-transit transfer');
  },
  async paymentRefusalIsShownTranslated(src) {
    const ctx = await cancelRefusedToast(src);
    assert.strictEqual(ctx.toasts.length, 1, 'one toast: ' + JSON.stringify(ctx.toasts));
    assert.strictEqual(ctx.toasts[0].msg, 'This order has a payment. Void the payment first.');
    assert.strictEqual(ctx.toasts[0].kind, 'error');
  },
  async acceptRefusalNamesTheSupplierStep(src) {
    const ctx = await acceptNeedsSupplierToast(src);
    assert.strictEqual(ctx.toasts.length, 1, 'one toast: ' + JSON.stringify(ctx.toasts));
    assert.strictEqual(ctx.toasts[0].msg, 'Set a supplier for this product first.');
    assert.strictEqual(ctx.toasts[0].kind, 'error');
  },
  async shortReceiptStatesTheMoneyWrittenOff(src) {
    const ctx = await shortReceiptToast(src);
    assert.ok(ctx.toasts.some((x) => /written off/.test(x.msg) && /2/.test(x.msg)), JSON.stringify(ctx.toasts));
  },
};

function mutate(src, from, to) {
  const n = src.split(from).length - 1;
  assert.strictEqual(n, 1, `mutation anchor must match exactly once, matched ${n}: ${from.slice(0, 60)}`);
  return src.replace(from, () => to);
}

const MUTANTS = [
  ['not opened painted red', 'inventoryNotOpened', 'notOpenedIsNeutralNeverRed',
    "not_opened: this._badge(t('Stock not opened yet'), 'purple')", "not_opened: this._badge(t('Stock not opened yet'), 'red')"],
  ['differs painted green', 'inventoryDiffers', 'differsIsRed',
    "differs: this._badge(t('Does Not Tie'), 'red')", "differs: this._badge(t('Does Not Tie'), 'green')"],
  ['cancel offered on every PO', 'cancelEverywhere', 'cancelOnlyOnPendingPOs',
    "${po.status==='pending'?`<button class=\"ret-btn ret-btn-danger ret-btn-sm\" style=\"margin-inline-start:6px\" onclick=\"RetailSystem._cancelPO", "${true?`<button class=\"ret-btn ret-btn-danger ret-btn-sm\" style=\"margin-inline-start:6px\" onclick=\"RetailSystem._cancelPO"],
  ['recall offered on received transfers too', 'recallEverywhere', 'recallOnlyOnInTransit',
    "} else if (xfer.status === 'in_transit') {", "} else {"],
  ['payment refusal shows the raw server text', 'rawRefusal', 'paymentRefusalIsShownTranslated',
    // W4a-2: the branch also covers PO_HAS_RECEIPTS now; the mutant still
    // disables the whole branch, so the claim it guards is unchanged.
    "} else if (d.code === 'PO_HAS_PAYMENTS' || d.code === 'PO_HAS_RECEIPTS') {", "} else if (false) {"],
  ['reorder accept refusal shows the raw server text', 'rawSupplierRefusal', 'acceptRefusalNamesTheSupplierStep',
    "} else if (d && d.code === 'SUPPLIER_REQUIRED_FOR_CREDIT_PO') {", "} else if (false) {"],
  ['written-off money dropped from the toast', 'noWrittenOff', 'shortReceiptStatesTheMoneyWrittenOff',
    "(writtenOff > 0 ?", "(false ?"],
];

(async () => {
  for (const [name, fn] of Object.entries(CLAIMS)) {
    await fn(SRC);
    console.log('ok   ' + name);
  }
  for (const [label, _id, claim, from, to] of MUTANTS) {
    let broken;
    try { broken = mutate(SRC, from, to); } catch (e) { console.log('MUTANT ANCHOR LOST: ' + label); throw e; }
    let red = false;
    try { await CLAIMS[claim](broken); } catch (e) { red = true; console.log('red  (' + label + ') -> ' + String(e.message).split('\n')[0].slice(0, 110)); }
    assert.ok(red, `mutation "${label}" did not turn "${claim}" red -- that claim guards nothing`);
  }
  console.log('retail_stock_books_ui_test: all claims hold and every mutation is caught');
})().catch((e) => { console.error('FAIL', e); process.exit(1); });
