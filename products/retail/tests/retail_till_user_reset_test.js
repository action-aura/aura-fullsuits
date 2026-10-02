/**
 * retail_till_user_reset_test.js -- the till keeps a sale in progress when a
 * cashier leaves the till SCREEN, so it must not also survive a sign-out or a
 * change of signed-in person: the next cashier could otherwise see and charge
 * the previous cashier's basket.
 *
 * COVERS (real subsystem-retail.js and real app-shell.js, each in its own vm)
 *   1. RetailSystem._resetTillForNewUser empties the cart and every piece of
 *      till state _clearCart resets, and POSTs / prints / holds NOTHING.
 *   2. SubsystemApp.logout(): with a sale in progress it asks first (danger
 *      dialog, the shop's own _confirm), "Cancel" leaves everything and does
 *      not even call /api/auth/logout; "confirm" resets the till. With an
 *      empty cart there is no extra question, but the reset still runs.
 *   3. _resetTillIfUserChanged: a DIFFERENT person (or an unprovable one)
 *      resets; the SAME person (lock screen / expiry) keeps the cart. This is
 *      the allow half -- a "reset always" build must fail it.
 *   4. The three new strings exist in both catalogs with real Arabic.
 *
 * Every guard is mutation-proved at the bottom (anchors must occur once).
 * Run: node products/retail/tests/retail_till_user_reset_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const SR_FILE = path.join(FRONTEND_DIR, 'subsystem-retail.js');
const SHELL_FILE = path.join(FRONTEND_DIR, 'app-shell.js');
const ICONS_FILE = path.join(FRONTEND_DIR, 'icons.js');
const SR_SRC = fs.readFileSync(SR_FILE, 'utf8');
const SHELL_SRC = fs.readFileSync(SHELL_FILE, 'utf8');
const ICONS_SRC = fs.readFileSync(ICONS_FILE, 'utf8');
const EN = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, 'locales', 'en.json'), 'utf8'));
const AR = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, 'locales', 'ar.json'), 'utf8'));

const STRINGS = [
  'Sign out?',
  'A sale is in progress. Signing out will clear it.',
  'Sign out and clear sale',
];

function eolOf(src) { return src.indexOf('\r\n') !== -1 ? '\r\n' : '\n'; }
function mutate(src, pairs) {
  const eol = eolOf(src);
  let out = src;
  for (const [f, r] of pairs) {
    const find = f.replace(/\n/g, eol);
    const hits = out.split(find).length - 1;
    assert.strictEqual(hits, 1, `Mutation anchor occurs ${hits} time(s), expected 1:\n  ${JSON.stringify(find)}`);
    out = out.replace(find, () => r.replace(/\n/g, eol));
  }
  return out;
}
async function provesMutation(what, check) {
  let threw = null;
  try { await check(); } catch (e) { threw = e; }
  assert.ok(threw, `MUTATION SURVIVED -- ${what}: the guard passed against a deliberately broken build.`);
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 120)}]`;
}

// ── element stub ────────────────────────────────────────────────────────────
function el(tag) {
  const n = {
    tagName: String(tag || 'div').toUpperCase(), children: [], parentNode: null,
    value: '', id: '', className: '', textContent: '', innerHTML: '', style: {}, dataset: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    appendChild(c) { n.children.push(c); c.parentNode = n; return c; },
    removeChild(c) { const i = n.children.indexOf(c); if (i !== -1) n.children.splice(i, 1); c.parentNode = null; return c; },
    remove() { if (n.parentNode) n.parentNode.removeChild(n); },
    addEventListener() {}, removeEventListener() {}, focus() {}, blur() {}, select() {},
    getAttribute() { return null; }, setAttribute() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
  return n;
}

// ── 1. RetailSystem ─────────────────────────────────────────────────────────
function loadRetail(src) {
  const els = Object.create(null);
  const sandbox = {};
  sandbox.window = sandbox;
  sandbox.navigator = { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' };
  sandbox.t = (s) => s;
  sandbox.console = console;
  sandbox.setTimeout = setTimeout; sandbox.clearTimeout = clearTimeout;
  sandbox.localStorage = { getItem: () => null, setItem: () => {} };
  sandbox.performance = { now: () => 10000 };
  sandbox.document = {
    activeElement: null,
    getElementById(id) { return (els[id] = els[id] || Object.assign(el('input'), { id })); },
    querySelectorAll: () => [],
    querySelector: () => null,
    addEventListener: () => {},
  };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: SR_FILE });
  const R = sandbox.RetailSystem;
  assert.ok(R, 'RetailSystem failed to load');
  const posted = [];
  R._post = async (u, b) => { posted.push({ u, b }); return { status: 'success', data: {} }; };
  R._get = async () => ({ data: [] });
  R._broadcastDisplayState = () => {};
  R._cart = [
    { product_id: 'p1', name: 'Widget', quantity: 2, unit_price: 10, line_total: 20, max_stock: 9 },
    { product_id: 'p2', name: 'Gadget', quantity: 1, unit_price: 5, line_total: 5, max_stock: 9 },
  ];
  R._currentTotals = { subtotal: 25, discount: 0, tax: 0, total: 25 };
  R._selLine = 1; R._resSel = 2; R._pendingQty = 3; R._qtyKeyBuffer = '3'; R._qtyKeyLastAt = 5;
  R._posResultsTerm = 'wid'; R._heldSel = 4; R._loyaltyBalance = 50; R._loyaltyRedeemPoints = 10;
  R._paymentMethod = 'card';
  R._posDraft = { customerId: 'c1', disc: '10', method: 'card' };
  els['pos-search'] = Object.assign(el('input'), { id: 'pos-search', value: 'wid' });
  els['pos-disc'] = Object.assign(el('input'), { id: 'pos-disc', value: '10' });
  els['pos-customer'] = Object.assign(el('select'), { id: 'pos-customer', value: 'c1' });
  els['pos-tendered'] = Object.assign(el('input'), { id: 'pos-tendered', value: '50' });
  return { R, els, posted };
}

function checkRetailReset(src) {
  const { R, els, posted } = loadRetail(src);
  R._resetTillForNewUser('logout');
  assert.strictEqual(R._cart.length, 0, 'cart not emptied');
  assert.strictEqual(R._selLine, null, 'line selection survived');
  assert.strictEqual(R._resSel, null, 'result selection survived');
  assert.strictEqual(R._pendingQty, null, 'pending "3*" quantity survived');
  assert.strictEqual(R._qtyKeyBuffer, '', 'qty key buffer survived');
  assert.strictEqual(R._posResultsTerm, '', 'search-results term survived');
  assert.strictEqual(R._posDraft, null, 'draft (customer/discount/tender) survived');
  assert.strictEqual(R._paymentMethod, 'cash', 'tender method survived');
  assert.strictEqual(R._loyaltyRedeemPoints, 0, 'loyalty redemption survived');
  assert.strictEqual(els['pos-search'].value, '', 'search text survived');
  assert.strictEqual(els['pos-disc'].value, 0, 'discount input survived');
  assert.strictEqual(els['pos-tendered'].value, '', 'cash tendered survived');
  assert.strictEqual(els['pos-customer'].value, '', 'customer survived');
  assert.strictEqual(posted.length, 0, 'the reset must never POST (charge/hold/print) anything');
}

// ── 2/3. SubsystemApp ───────────────────────────────────────────────────────
function loadShell(src) {
  const sandbox = {
    console, t: (s) => s, setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    location: { hash: '', href: 'http://localhost/', search: '' },
    fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
    matchMedia: () => ({ matches: false }),
    MutationObserver: class { observe() {} disconnect() {} },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    document: {
      readyState: 'complete', head: { appendChild() {} },
      documentElement: { getAttribute() { return null; }, setAttribute() {}, style: { setProperty() {} } },
      createElement: el, createTextNode: (x) => ({ nodeValue: String(x) }),
      querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {},
      getElementById() { return null; },
    },
  };
  sandbox.document.body = el('body');
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(ICONS_SRC, sandbox, { filename: ICONS_FILE });
  vm.runInContext(src, sandbox, { filename: SHELL_FILE });
  const App = sandbox.SubsystemApp;
  assert.ok(App, 'SubsystemApp failed to load');
  const calls = { fetches: [], resets: [], confirms: [] };
  sandbox.fetch = (u) => { calls.fetches.push(String(u)); return Promise.resolve({ ok: true, json: () => Promise.resolve({}) }); };
  App._askLeaveGuards = async () => true;
  App._confirmSignOutWhileUnsynced = async () => true;
  App._stopSyncHealthPoll = () => {};
  App.showReloginModal = () => {};
  return { App, sandbox, calls };
}
function installRetailSpy(sandbox, calls, { inProgress, answer }) {
  sandbox.RetailSystem = {
    _hasSaleInProgress: () => inProgress,
    _confirm: async (o) => { calls.confirms.push(o); return answer; },
    _resetTillForNewUser: (why) => { calls.resets.push(why); },
  };
}

async function checkLogoutAsksAndResets(src) {
  // sale in progress, cashier says Cancel
  let { App, sandbox, calls } = loadShell(src);
  installRetailSpy(sandbox, calls, { inProgress: true, answer: false });
  await App.logout();
  assert.strictEqual(calls.confirms.length, 1, 'no question asked about the sale in progress');
  assert.strictEqual(calls.confirms[0].danger, true, 'dialog must be the destructive (Cancel-focused) kind');
  assert.ok(String(calls.confirms[0].message).includes('A sale is in progress. Signing out will clear it.'), 'wrong message');
  assert.deepStrictEqual(calls.resets, [], 'Cancel must not clear the sale');
  assert.ok(!calls.fetches.some((u) => u.includes('/api/auth/logout')), 'Cancel must not sign out');

  // sale in progress, cashier confirms
  ({ App, sandbox, calls } = loadShell(src));
  installRetailSpy(sandbox, calls, { inProgress: true, answer: true });
  await App.logout();
  assert.deepStrictEqual(calls.resets, ['logout'], 'confirmed sign-out must clear the till');
  assert.ok(calls.fetches.some((u) => u.includes('/api/auth/logout')), 'confirmed sign-out must sign out');

  // empty cart: no extra question, but the reset still runs
  ({ App, sandbox, calls } = loadShell(src));
  installRetailSpy(sandbox, calls, { inProgress: false, answer: false });
  await App.logout();
  assert.strictEqual(calls.confirms.length, 0, 'asked about a sale that does not exist');
  assert.deepStrictEqual(calls.resets, ['logout'], 'sign-out with an empty cart must still reset the till state');
}

function checkUserChangeRule(src) {
  const A = { id: 7, email: 'a@x.com' };
  const B = { id: 8, email: 'b@x.com' };
  const run = (prev, next) => {
    const { App, sandbox, calls } = loadShell(src);
    installRetailSpy(sandbox, calls, { inProgress: true, answer: true });
    App._resetTillIfUserChanged(prev, next, 'user-change');
    return calls.resets;
  };
  assert.deepStrictEqual(run(A, B), ['user-change'], 'a DIFFERENT user must wipe the till');
  assert.deepStrictEqual(run({ email: 'a@x.com' }, { email: 'A@X.com' }), [], 'same person by email (no id): cart kept');
  assert.deepStrictEqual(run(A, A), [], 'ALLOW HALF: the SAME user signing back in must keep the cart');
  assert.deepStrictEqual(run(A, { id: 7 }), [], 'ALLOW HALF: same id must keep the cart');
  assert.deepStrictEqual(run(A, null), ['user-change'], 'cannot prove same person: wipe');
  assert.deepStrictEqual(run(undefined, B), [], 'first boot (nobody before): nothing to protect');
}

function checkStrings() {
  for (const k of STRINGS) {
    assert.ok(EN[k], `en.json missing "${k}"`);
    assert.ok(AR[k] && AR[k] !== k && /[؀-ۿ]/.test(AR[k]), `ar.json missing real Arabic for "${k}"`);
  }
}

(async () => {
  const out = [];
  checkRetailReset(SR_SRC); out.push('PASS  _resetTillForNewUser empties every till control and POSTs nothing');
  await checkLogoutAsksAndResets(SHELL_SRC); out.push('PASS  logout asks (danger) when a sale is in progress, Cancel keeps it, confirm clears it');
  checkUserChangeRule(SHELL_SRC); out.push('PASS  different user clears; same user keeps (allow half)');
  checkStrings(); out.push('PASS  new strings in en + ar');
  assert.ok(SHELL_SRC.includes("this._resetTillIfUserChanged(this.currentUser, sess.user, 'user-change')"),
    'init() no longer compares the new session user with the previous one');
  out.push('PASS  init() wires the user-change check (source-asserted only; no behavioural proof of that line, see report)');

  out.push('--- mutation proofs ---');
  out.push(await provesMutation('SR reset forgets the pending "3*" quantity', () =>
    checkRetailReset(mutate(SR_SRC, [['    this._pendingQty = null;\n    this._qtyKeyBuffer = \'\';\n    this._qtyKeyLastAt = 0;\n    this._posResultsTerm = \'\';\n    this._heldSel = 0;', '    this._qtyKeyBuffer = \'\';\n    this._qtyKeyLastAt = 0;\n    this._posResultsTerm = \'\';\n    this._heldSel = 0;']]))));
  out.push(await provesMutation('SR reset leaves the cart', () =>
    checkRetailReset(mutate(SR_SRC, [['    clearTimeout(this._posSearchTimer);\n    this._cart = [];\n    this._selLine = null;', '    clearTimeout(this._posSearchTimer);\n    this._selLine = null;']]))));
  out.push(await provesMutation('logout no longer resets the till', () =>
    checkLogoutAsksAndResets(mutate(SHELL_SRC, [["    this._resetTillForNewUser('logout');\n", '']]))));
  out.push(await provesMutation('logout no longer asks about a sale in progress', () =>
    checkLogoutAsksAndResets(mutate(SHELL_SRC, [['      if (!ok) return;\n    }\n    // Stopped BEFORE', '    }\n    // Stopped BEFORE'], ['      const ok = await window.RetailSystem._confirm({', '      const ok = true || await window.RetailSystem._confirm({']]))));
  out.push(await provesMutation('same-user relogin clears too (allow half)', () =>
    checkUserChangeRule(mutate(SHELL_SRC, [['    if (b && a === b) return;\n', '']]))));
  out.push(await provesMutation('different user keeps the cart (deny half)', () =>
    checkUserChangeRule(mutate(SHELL_SRC, [["    if (!a) return;   // nobody was signed in before (first boot): nothing to protect\n    if (b && a === b) return;\n    this._resetTillForNewUser(reason);", "    return;"]]))));
  console.log(out.join('\n'));
})().catch((e) => { console.error('FAIL', e); process.exit(1); });
