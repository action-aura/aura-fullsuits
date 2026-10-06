/**
 * Aura Retail -- W4a-2 receive dialog (partial receive + close short).
 *
 * What the owner sees:
 *   1. Receive on pending AND partial purchase orders; Close short on PARTIAL
 *      only; Cancel on pending only;
 *   2. a dialog with one row per line still on order, its quantity box
 *      defaulting to what REMAINS;
 *   3. Receive sends {lines:[{item_id, quantity}]} carrying only quantities
 *      above zero, and refuses an empty delivery without calling the server;
 *   4. the server's refusals (OVER_RECEIPT, PO_HAS_RECEIPTS ...) shown in the
 *      shop's language, never the raw sentence;
 *   5. keyboard: Enter inside a quantity box submits;
 *   6. (W4a-2 verify R1) the delivery carries a receipt_key minted once per
 *      dialog open, a double Enter sends ONE request, a retry reuses the key.
 *
 * Each claim is mutation-proved at the bottom: the real subsystem-retail.js is
 * broken in one place at a time and the matching claim must go red.
 *
 *   node products/retail/tests/retail_po_receive_dialog_ui_test.js
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

function load(opts) {
  const o = opts || {};
  const calls = [];
  const toasts = [];
  const els = Object.create(null);
  const appended = [];
  const getEl = (id) => (els[id] || (els[id] = makeStub({ id })));
  const sandbox = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    t: (s) => s,
    fetch: (url, init) => {
      const method = ((init && init.method) || 'GET').toUpperCase();
      const u = String(url);
      calls.push({ method, url: u, body: init && init.body ? JSON.parse(init.body) : null });
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
      getElementById(id) {
        if (id === 'ret-styles') return null;
        // inputs the dialog rendered answer with the value the test "typed"
        const m = /^po-recv-(\d+)$/.exec(id);
        if (m && o.typed && Object.prototype.hasOwnProperty.call(o.typed, m[1])) {
          return makeStub({ id, value: String(o.typed[m[1]]) });
        }
        return getEl(id);
      },
      createElement() { return makeStub(); },
      querySelector(sel) {
        const m = /^#([\w-]+)\s+tbody$/.exec(sel);
        if (m) return getEl(m[1] + '__tbody');
        return makeStub();
      },
      querySelectorAll() { return []; },
      head: { appendChild() {} }, body: { appendChild(el) { appended.push(el); } },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {}, removeEventListener() {},
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
  sandbox.RetailSystem._closeModalOverlay = () => {};
  return { rs: sandbox.RetailSystem, calls, toasts, els, appended, doc: sandbox.document };
}

async function settle() {
  for (let i = 0; i < 10; i++) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

const PO_DETAIL = { data: {
  po: { id: 5, po_number: 'PO-5', status: 'partial', supplier_name: 'S', total: 100 },
  items: [
    { id: 11, product_name: 'Widget', sku: 'W1', quantity: 10, received_qty: 6 },
    { id: 12, product_name: 'Gadget', sku: 'G1', quantity: 4, received_qty: 4 },   // already complete
    { id: 13, product_name: 'Gizmo', sku: 'Z1', quantity: 5, received_qty: 0 },
  ],
} };

async function poListHtml(source) {
  const ctx = load({
    source,
    routes: [{ match: '/purchase-orders', payload: { data: [
      { id: 1, po_number: 'PO-1', status: 'pending', total: 40, supplier_name: 'S' },
      { id: 2, po_number: 'PO-2', status: 'partial', total: 40, supplier_name: 'S' },
      { id: 3, po_number: 'PO-3', status: 'received', total: 40, supplier_name: 'S' },
      { id: 4, po_number: 'PO-4', status: 'closed', total: 40, supplier_name: 'S' },
      { id: 6, po_number: 'PO-6', status: 'cancelled', total: 40, supplier_name: 'S' },
    ] } }],
  });
  await ctx.rs._loadPurchaseOrders();
  await settle();
  return ctx.els['po-table__tbody'].innerHTML;
}

async function openDialog(source, extra) {
  const ctx = load(Object.assign({
    source,
    routes: [
      { match: '/purchase-orders/5/receive', method: 'POST', payload: { status: 'success' } },
      { match: '/purchase-orders/5', payload: PO_DETAIL },
      { match: '/purchase-orders', payload: { data: [] } },
    ],
  }, extra || {}));
  await ctx.rs._receivePO(5, 'PO-5');
  await settle();
  return ctx;
}

const CLAIMS = {
  async receiveOnPendingAndPartialOnly(src) {
    const h = await poListHtml(src);
    assert.strictEqual((h.match(/_receivePO\(/g) || []).length, 2, 'Receive on exactly pending + partial: ' + h);
    assert.ok(/_receivePO\(1,/.test(h) && /_receivePO\(2,/.test(h), 'Receive belongs to PO 1 and PO 2');
  },
  async closeShortOnlyOnPartial(src) {
    const h = await poListHtml(src);
    assert.strictEqual((h.match(/_closeShortPO\(/g) || []).length, 1, 'exactly one Close short: ' + h);
    assert.ok(/_closeShortPO\(2,/.test(h), 'Close short belongs to the partial PO');
  },
  async cancelOnlyOnPending(src) {
    const h = await poListHtml(src);
    assert.strictEqual((h.match(/_cancelPO\(/g) || []).length, 1, 'exactly one Cancel: ' + h);
    assert.ok(/_cancelPO\(1,/.test(h), 'Cancel belongs to the pending PO');
  },
  async partialAndClosedHaveTheirOwnBadges(src) {
    const h = await poListHtml(src);
    assert.ok(/>Partly received</.test(h), 'partial badge text: ' + h);
    assert.ok(/>Closed short</.test(h), 'closed badge text: ' + h);
  },
  async dialogHasOneRowPerOpenLineDefaultingToRemaining(src) {
    const ctx = await openDialog(src);
    assert.strictEqual(ctx.appended.length, 1, 'one overlay appended');
    const h = ctx.appended[0].innerHTML;
    assert.ok(/role="dialog"/.test(h) && /aria-modal="true"/.test(h), 'dialog semantics');
    assert.ok(/id="po-recv-11"[^>]*value="4"/.test(h), 'line 11 defaults to remaining 4 (10 ordered, 6 in): ' + h);
    assert.ok(/id="po-recv-13"[^>]*value="5"/.test(h), 'line 13 defaults to remaining 5');
    assert.ok(!/po-recv-12"/.test(h), 'a fully received line gets no input');
  },
  async dialogIsKeyboardOperable(src) {
    const ctx = await openDialog(src);
    const h = ctx.appended[0].innerHTML;
    assert.ok(/onkeydown="[^"]*Enter[^"]*_submitReceivePO/.test(h), 'Enter must submit: ' + h);
    assert.ok(/<input[^>]*aria-label=/.test(h), 'every quantity box is named');
  },
  async receiveSendsOnlyPositiveQuantities(src) {
    const ctx = await openDialog(src, { typed: { 11: 3, 13: 0 } });
    await ctx.rs._submitReceivePO();
    await settle();
    const post = ctx.calls.find((c) => c.method === 'POST' && /\/purchase-orders\/5\/receive/.test(c.url));
    assert.ok(post, 'a receive POST was sent: ' + JSON.stringify(ctx.calls));
    assert.deepStrictEqual(post.body.lines, [{ item_id: 11, quantity: 3 }]);
  },
  // W4a-2 verify R1: the receipt key names the delivery. A double Enter (the
  // keydown handler calls _submitReceivePO directly, past the button's disabled
  // state) must send ONE request; a retry after a failure must carry the SAME
  // key (the first request may have landed); each dialog open mints its own.
  async receiptKeyIsSentAndValid(src) {
    const ctx = await openDialog(src, { typed: { 11: 3, 13: 0 } });
    await ctx.rs._submitReceivePO();
    await settle();
    const post = ctx.calls.find((c) => c.method === 'POST' && /\/receive/.test(c.url));
    // (typeof first: String(undefined) is "undefined" -- 9 letters, a valid-looking key)
    assert.ok(post && typeof post.body.receipt_key === 'string' && /^[A-Za-z0-9-]{8,64}$/.test(post.body.receipt_key),
      'receipt_key must be 8..64 of [A-Za-z0-9-]: ' + JSON.stringify(post && post.body));
  },
  async doubleEnterSendsOneRequest(src) {
    const ctx = await openDialog(src, { typed: { 11: 3, 13: 0 } });
    ctx.rs._submitReceivePO();
    ctx.rs._submitReceivePO();
    await settle();
    const posts = ctx.calls.filter((c) => c.method === 'POST' && /\/receive/.test(c.url));
    assert.strictEqual(posts.length, 1, 'two Enter presses -> one POST, got ' + posts.length);
  },
  async retryAfterFailureReusesTheKeyEachDialogMintsItsOwn(src) {
    const failing = [
      { match: '/purchase-orders/5/receive', method: 'POST', status: 409,
        payload: { status: 'error', code: 'PO_LINES_NOT_ARRIVED', message: 'raw' } },
      { match: '/purchase-orders/5', payload: PO_DETAIL },
      { match: '/purchase-orders', payload: { data: [] } },
    ];
    const ctx = await openDialog(src, { typed: { 11: 3, 13: 0 }, routes: failing });
    await ctx.rs._submitReceivePO();
    await settle();
    await ctx.rs._submitReceivePO();           // the user presses Receive again
    await settle();
    const posts = ctx.calls.filter((c) => c.method === 'POST' && /\/receive/.test(c.url));
    assert.strictEqual(posts.length, 2, 'a failed request can be retried: ' + posts.length);
    assert.strictEqual(posts[0].body.receipt_key, posts[1].body.receipt_key, 'a retry must carry the SAME key');
    const other = await openDialog(src, { typed: { 11: 3, 13: 0 } });
    await other.rs._submitReceivePO();
    await settle();
    const k2 = other.calls.find((c) => c.method === 'POST' && /\/receive/.test(c.url)).body.receipt_key;
    assert.notStrictEqual(k2, posts[0].body.receipt_key, 'a new dialog is a new delivery: its own key');
  },
  async emptyDeliveryNeverReachesTheServer(src) {
    const ctx = await openDialog(src, { typed: { 11: 0, 13: 0 } });
    await ctx.rs._submitReceivePO();
    await settle();
    assert.ok(!ctx.calls.some((c) => c.method === 'POST'), 'no POST for an empty delivery: ' + JSON.stringify(ctx.calls));
    assert.ok(ctx.toasts.some((x) => x.msg === 'Enter a quantity for at least one line.' && x.kind === 'error'), JSON.stringify(ctx.toasts));
  },
  async overRemainingIsRefusedBeforeSending(src) {
    const ctx = await openDialog(src, { typed: { 11: 9, 13: 1 } });
    await ctx.rs._submitReceivePO();
    await settle();
    assert.ok(!ctx.calls.some((c) => c.method === 'POST'), 'no POST for an over-receipt');
    assert.ok(ctx.toasts.some((x) => /more than is still on order/.test(x.msg)), JSON.stringify(ctx.toasts));
  },
  async serverRefusalIsTranslatedNotRaw(src) {
    const ctx = await openDialog(src, {
      typed: { 11: 4, 13: 5 },
      routes: [
        { match: '/purchase-orders/5/receive', method: 'POST', status: 409,
          payload: { status: 'error', code: 'PO_RECEIPTS_NOT_ARRIVED', message: 'raw server sentence' } },
        { match: '/purchase-orders/5', payload: PO_DETAIL },
        { match: '/purchase-orders', payload: { data: [] } },
      ],
    });
    await ctx.rs._submitReceivePO();
    await settle();
    assert.strictEqual(ctx.toasts.length, 1, JSON.stringify(ctx.toasts));
    assert.ok(/earlier delivery has not finished syncing/.test(ctx.toasts[0].msg), ctx.toasts[0].msg);
  },
  async cancelAfterReceiptsPointsAtCloseShort(src) {
    const ctx = load({
      source: src,
      routes: [
        { match: '/purchase-orders/5/cancel', method: 'POST', status: 409,
          payload: { status: 'error', code: 'PO_HAS_RECEIPTS', message: 'raw server sentence' } },
        { match: '/purchase-orders', payload: { data: [] } },
      ],
    });
    await ctx.rs._cancelPO(5, 'PO-5');
    await settle();
    assert.strictEqual(ctx.toasts.length, 1, JSON.stringify(ctx.toasts));
    assert.ok(/Use Close short instead of Cancel/.test(ctx.toasts[0].msg), ctx.toasts[0].msg);
  },
  async closeShortPostsToTheCloseRoute(src) {
    const ctx = load({
      source: src,
      routes: [
        { match: '/purchase-orders/5/close', method: 'POST', payload: { status: 'success' } },
        { match: '/purchase-orders', payload: { data: [] } },
      ],
    });
    await ctx.rs._closeShortPO(5, 'PO-5');
    await settle();
    assert.ok(ctx.calls.some((c) => c.method === 'POST' && /\/purchase-orders\/5\/close$/.test(c.url)), JSON.stringify(ctx.calls));
    assert.ok(ctx.toasts.some((x) => x.kind === 'success'), JSON.stringify(ctx.toasts));
  },
  // W4a-2 fix (F4): the supplier statement emits kind 'closed_short' (a credit
  // line for the undelivered part of a closed-short order). It must render as
  // its own translated label, never fall through to the "Payment" default.
  async closedShortStatementLineHasItsOwnLabel(src) {
    const ctx = load({ source: src });
    const k = ctx.rs._partyStatementKind('closed_short');
    assert.strictEqual(k.label, 'Closed short (undelivered balance released)', JSON.stringify(k));
    assert.strictEqual(k.sign, -1, 'a credit line');
    const en = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'frontend', 'locales', 'en.json'), 'utf8'));
    const ar = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'frontend', 'locales', 'ar.json'), 'utf8'));
    assert.ok(en[k.label], 'en.json carries the label');
    assert.ok(ar[k.label] && /[؀-ۿ]/.test(ar[k.label]), 'ar.json carries a real Arabic label');
  },
  // W4a-2 UI: a supplier's goods charge is a purchase, not a "Credit sale".
  async supplierChargeIsLabelledPurchase(src) {
    const ctx = load({ source: src });
    assert.strictEqual(ctx.rs._partyStatementKind('charge', 'supplier').label, 'Purchase');
    assert.strictEqual(ctx.rs._partyStatementKind('charge', 'supplier').sign, 1);
    assert.strictEqual(ctx.rs._partyStatementKind('charge', 'customer').label, 'Credit sale', 'customer wording unchanged');
    assert.strictEqual(ctx.rs._partyStatementKind('charge').label, 'Credit sale');
    assert.strictEqual(ctx.rs._partyStatementKind('payment', 'supplier').label, 'Payment', 'other kinds shared');
    // the on-screen statement actually passes the party type through
    const ev = [{ kind: 'charge', amount: 5, running_balance: 5, date: '2026-10-01', ref: 'PO-1' }];
    const html = ctx.rs._partyStatementMarkup('supplier', 1, { name: 'S' }, ev, 5, { active: false }, {});
    assert.ok(html.indexOf('Purchase') !== -1 && html.indexOf('Credit sale') === -1, 'supplier statement row says Purchase');
    const en = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'frontend', 'locales', 'en.json'), 'utf8'));
    const ar = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'frontend', 'locales', 'ar.json'), 'utf8'));
    assert.ok(en['Purchase'] && /[؀-ۿ]/.test(ar['Purchase']), 'both locales carry Purchase');
  },
  // W4a-2 UI: Tab cycles inside the top modal; a stacked layer owns the keys.
  async tabIsTrappedInsideTheTopModal(src) {
    const ctx = load({ source: src });
    const mk = (name) => ({ name, disabled: false, type: 'button', offsetWidth: 10, offsetHeight: 10,
      getAttribute() { return null; }, closest() { return null; }, getClientRects() { return [1]; },
      focus() { ctx.doc.activeElement = this; } });
    const run = (items, active, shift, stacked) => {
      const listeners = [];
      const dialog = makeStub({ id: 'dlg', querySelectorAll: () => items, contains: (el) => items.indexOf(el) !== -1 });
      ctx.doc.addEventListener = (type, fn) => { if (type === 'keydown') listeners.push(fn); };
      ctx.doc.getElementById = (id) => (id === 'dlg' ? dialog : null);
      ctx.doc.activeElement = active;
      ctx.rs._modalKeyStack.length = 0;
      ctx.rs._wireModalA11y({ remove() {} }, 'dlg');
      if (stacked) ctx.rs._modalKeyStack.push({ overlay: {} });
      let prevented = false;
      listeners[0]({ key: 'Tab', shiftKey: !!shift, preventDefault() { prevented = true; }, stopPropagation() {} });
      return { prevented, active: ctx.doc.activeElement };
    };
    const a = mk('a'), b = mk('b'), c = mk('c');
    let r = run([a, b, c], c, false, false);
    assert.ok(r.prevented && r.active === a, 'Tab from last wraps to first');
    r = run([a, b, c], a, true, false);
    assert.ok(r.prevented && r.active === c, 'Shift+Tab from first wraps to last');
    r = run([a, b, c], b, false, false);
    assert.ok(!r.prevented, 'Tab in the middle is left to the browser');
    r = run([a, b, c], c, false, true);
    assert.ok(!r.prevented && r.active === c, 'a layer stacked on top owns Tab');
    r = run([], null, false, false);
    assert.ok(r.prevented, 'zero focusables: Tab swallowed, no throw');
    const hidden = Object.assign(mk('h'), { offsetWidth: 0, offsetHeight: 0, getClientRects() { return []; } });
    r = run([a, hidden], a, false, false);
    assert.ok(r.prevented && r.active === a, 'invisible controls are skipped when finding the last');
  },
  // W4a-2 UI: in Arabic the device suffix landed on the wrong side of the number.
  async docNumberAndTagAreOneLtrIsolate(src) {
    const ctx = load({ source: src });
    const html = ctx.rs._docNoHtml('PO-000004-0123abcd-60ee1234');
    assert.ok(html.startsWith('<bdi dir="ltr">'), html);
    assert.ok(html.endsWith('</bdi>') && html.indexOf('60ee') !== -1 && html.indexOf('PO-000004') !== -1, html);
    assert.ok(html.indexOf('<bdi dir="ltr" title=') !== -1, 'inner number isolate kept');
    const evil = ctx.rs._docNoHtml('<img src=x onerror=1>');
    assert.ok(evil.indexOf('<img') === -1, 'still escaped: ' + evil);
  },
};

function mutate(src, from, to) {
  const n = src.split(from).length - 1;
  assert.strictEqual(n, 1, `mutation anchor must match exactly once, matched ${n}: ${from.slice(0, 60)}`);
  return src.replace(from, () => to);
}

const MUTANTS = [
  ['Receive hidden on partial', 'receiveNotOnPartial', 'receiveOnPendingAndPartialOnly',
    "(po.status==='pending'||po.status==='partial')?`<button class=\"ret-btn ret-btn-primary", "(po.status==='pending')?`<button class=\"ret-btn ret-btn-primary"],
  ['Close short offered on every PO', 'closeShortEverywhere', 'closeShortOnlyOnPartial',
    "${po.status==='partial'?`<button class=\"ret-btn ret-btn-ghost ret-btn-sm\" style=\"margin-inline-start:6px\" onclick=\"RetailSystem._closeShortPO", "${true?`<button class=\"ret-btn ret-btn-ghost ret-btn-sm\" style=\"margin-inline-start:6px\" onclick=\"RetailSystem._closeShortPO"],
  ['dialog defaults to ordered, not remaining', 'defaultOrdered', 'dialogHasOneRowPerOpenLineDefaultingToRemaining',
    'step="any" value="${l.remaining}"', 'step="any" value="${l.ordered}"'],
  ['zero quantities sent too', 'sendZeros', 'receiveSendsOnlyPositiveQuantities',
    'if (q > 0) out.push', 'if (q >= 0) out.push'],
  ['empty delivery sent anyway', 'sendEmpty', 'emptyDeliveryNeverReachesTheServer',
    'if (!payloadLines.length) {', 'if (false) {'],
  ['Enter no longer submits', 'noEnter', 'dialogIsKeyboardOperable',
    "event.key==='Enter'&&event.target.tagName==='INPUT'){event.preventDefault();RetailSystem._submitReceivePO();}", "event.key==='Enter'&&false){}"],
  ['server refusal shown raw', 'rawRefusal', 'serverRefusalIsTranslatedNotRaw',
    "return msg ? t(msg) : ((d && d.message) || t('Error'));", "return (d && d.message) || t('Error');"],
  ['closed_short statement kind removed', 'noClosedShortKind', 'closedShortStatementLineHasItsOwnLabel',
    "    closed_short: { label: 'Closed short (undelivered balance released)', sign: -1 },", ''],
  ['no in-flight guard (double Enter sends twice)', 'noInFlightGuard', 'doubleEnterSendsOneRequest',
    'if (st.inFlight) return;', ''],
  ['receipt key not sent', 'noKey', 'receiptKeyIsSentAndValid',
    '{ lines: payloadLines, receipt_key: st.receiptKey }', '{ lines: payloadLines }'],
  ['key minted per request, not per dialog', 'keyPerRequest', 'retryAfterFailureReusesTheKeyEachDialogMintsItsOwn',
    'receipt_key: st.receiptKey }', 'receipt_key: this._newReceiptKey() }'],
  ['one key for every dialog', 'sharedKey', 'retryAfterFailureReusesTheKeyEachDialogMintsItsOwn',
    'receiptKey: this._newReceiptKey(), inFlight: false', "receiptKey: 'fixed-key-00000000', inFlight: false"],
  ['in-flight flag never cleared after a failed request', 'stuckInFlight', 'retryAfterFailureReusesTheKeyEachDialogMintsItsOwn',
    "        st.inFlight = false;\n        if (btn) btn.disabled = false;\n      }\n    } catch",
    "        if (btn) btn.disabled = false;\n      }\n    } catch"],
  ['supplier charge keeps customer wording', 'custWording', 'supplierChargeIsLabelledPurchase',
    "partyType === 'supplier' && this._PARTY_STATEMENT_KIND_SUPPLIER[kind]", 'false && this._PARTY_STATEMENT_KIND_SUPPLIER[kind]'],
  ['statement markup drops the party type', 'markupNoType', 'supplierChargeIsLabelledPurchase',
    'const meta = this._partyStatementKind(e.kind, kind);', 'const meta = this._partyStatementKind(e.kind);'],
  ['Tab trap removed', 'noTrap', 'tabIsTrappedInsideTheTopModal',
    'this._trapTab(e, dialogEl || overlay);', ''],
  ['Tab trap ignores the top-layer gate', 'trapNoGate', 'tabIsTrappedInsideTheTopModal',
    "if (e.key === 'Tab') {\n        if (this._topModalKeyLayer() !== layer) return;", "if (e.key === 'Tab') {"],
  ['doc number tag outside the isolate', 'tagOutside', 'docNumberAndTagAreOneLtrIsolate',
    'return `<bdi dir="ltr">${this._bdi(main, full)}${tag}</bdi>`;', 'return this._bdi(main, full) + tag;'],
  ['over-receipt not checked client-side', 'noOverCheck', 'overRemainingIsRefusedBeforeSending',
    'if (q < 0 || q > ln.remaining) {', 'if (false) {'],
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
  console.log('retail_po_receive_dialog_ui_test: all claims hold and every mutation is caught');
})().catch((e) => { console.error('FAIL', e); process.exit(1); });
