/**
 * Retail — capability gating for the Reports surface.
 *
 * Two separate defects live here, and they compound. Read both before
 * changing either, because fixing only the second one produces a gate that
 * looks correct in the source and does nothing at runtime.
 *
 * ── DEFECT 1: the capability list was never actually read ──────────────────
 *
 * app-shell.js resolved the signed-in user's grants like this:
 *
 *     this.capabilities = (sess.user && Array.isArray(sess.user.capabilities))
 *       ? sess.user.capabilities
 *       : null;
 *
 * The backend does not put it there. `/api/auth/session`
 * (commercial_runtime/identity/onboarding_routes.py, get_session) returns
 * `capabilities` as a TOP-LEVEL key, a sibling of `user`, not a member of it.
 * Verified by booting the real app and logging in as each role rather than by
 * reading the handler — see retail_attribution_i18n_test.py, which pins that
 * payload shape from the server side so this file's fixtures cannot drift
 * away from it:
 *
 *     {"authenticated": true,
 *      "capabilities": ["retail.cash.close", "retail.refund", "retail.sell"],
 *      "is_mt": true, "language": "en",
 *      "user": {"id": "...", "role": "cashier", "email": "...", ...}}
 *
 * So `sess.user.capabilities` was always `undefined`, never an array, and
 * `this.capabilities` was pinned at `null` forever. `hasCapability()` answers
 * `true` for `null` by design — it fails OPEN, which is safe and deliberate
 * for rendering advice (see that method's own comment) but means the whole
 * mechanism was inert on every install for every role.
 *
 * The consequence is not theoretical: the cashier-landing panel in
 * subsystem-retail.js's `_renderDashboard` exists specifically so a cashier's
 * first screen after login is not a 403, and it is guarded by exactly this
 * call. With the read pointing at the wrong key, that guard never fired, the
 * dashboard rendered for cashiers as before, and GET /dashboard/stats 403'd
 * on every login — the precise bug the panel was built to remove, shipped
 * with its fix wired to nothing. Nothing failed loudly because failing open
 * IS the documented `null` behavior; there was no test on `hasCapability` at
 * all, which is how a one-word path error survived a whole wave.
 *
 * ── DEFECT 2: the Reports nav entry had no gate ────────────────────────────
 *
 * Same bug class as the cashier dashboard, not covered by it. Every panel on
 * the Reports screen reads a `@mt_require_capability(CAP_REPORTS)` route
 * (report_summary / report_sales_trend / report_top_products /
 * report_payment_methods / report_by_branch in retail_api.py), and
 * `retail.reports` is not in ROLE_CAPABILITIES[cashier] — a cashier holds
 * {sell, refund, cash.close} and nothing else. The nav entry was
 * unconditional, so a cashier could click "Reports" and collect five 403s
 * from one page load.
 *
 * Hiding the nav entry is necessary but NOT sufficient, and this file asserts
 * both halves. `_navigate('reports')` is reachable without the sidebar: the
 * hash router (AuraRouter.save/restore, app-shell.js `_navigate`) persists the
 * last section to the URL and replays it on the next launch, so a shared till
 * where a manager last opened Reports lands the next cashier straight on that
 * screen with no nav click involved. The render function is the one choke
 * point every path goes through, so the fetch-suppressing guard belongs
 * there — mirroring `_renderDashboard`'s cashier landing exactly.
 *
 * "Renders nothing" is not the requirement either. A screen that fetches,
 * takes a 403 and then hides has still generated the error. These tests
 * assert ZERO fetch calls, not merely absent output.
 *
 * Standalone Node, no framework — this frontend has no build step (CLAUDE.md):
 *
 *   node products/retail/tests/retail_reports_capability_gate_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const SHELL_FILE = path.join(FRONTEND_DIR, 'app-shell.js');
const RETAIL_FILE = path.join(FRONTEND_DIR, 'subsystem-retail.js');

// The exact grant sets the backend computes, copied from a real login rather
// than invented: ROLE_CAPABILITIES in commercial_runtime/identity/
// user_accounts.py, as observed through /api/auth/session. An admin reads the
// ROLE (all eight codes) instead of the permission table; a cashier reads
// their actual user_permissions rows.
const CASHIER_CAPS = ['retail.cash.close', 'retail.refund', 'retail.sell'];
const ADMIN_CAPS = [
  'retail.cash.approve', 'retail.cash.close', 'retail.discount',
  'retail.employees', 'retail.refund', 'retail.reports',
  'retail.sell', 'retail.stock.adjust',
];

function makeElementStub() {
  return {
    innerHTML: '',
    textContent: '',
    innerText: '',
    value: '',
    disabled: false,
    id: '',
    className: '',
    style: {},
    dataset: {},
    classList: { toggle() {}, add() {}, remove() {} },
    appendChild() {},
    addEventListener(type, fn) { this['_on' + type] = fn; },
    removeEventListener() {},
    remove() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelector() { return makeElementStub(); },
    querySelectorAll() { return []; },
  };
}

// ── app-shell.js loader ─────────────────────────────────────────────────────
// The file ends with two load-time side effects (a DOMContentLoaded listener
// and a table-labelling IIFE that opens a MutationObserver inside its own
// try/catch). Neither is under test; the stubs below are just enough for the
// module body to finish evaluating so `window.SubsystemApp` exists.
function loadShell() {
  const code = fs.readFileSync(SHELL_FILE, 'utf8');
  const store = {};
  const sandbox = {
    console,
    t: (s) => s,
    setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    location: { hash: '', href: 'http://localhost/' },
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    document: {
      readyState: 'complete',
      body: makeElementStub(),
      head: { appendChild() {} },
      documentElement: {
        getAttribute() { return null; },
        setAttribute() {},
        style: { setProperty() {} },
      },
      getElementById() { return null; },
      createElement() { return makeElementStub(); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      addEventListener() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: SHELL_FILE });
  assert.ok(sandbox.SubsystemApp, 'app-shell.js did not expose window.SubsystemApp');
  return sandbox;
}

// ── subsystem-retail.js loader ──────────────────────────────────────────────
// `fetchImpl` records every call. The gated cases pass one that THROWS, so a
// suppressed-fetch assertion cannot pass by accident on a stub that quietly
// returns something plausible.
//
// `elements` maps an id or a querySelector string to a specific stub. Default-
// stubbing every lookup is fine for the screens that only READ their own
// markup back, but it is actively dangerous for the Sales History cases below:
// a fresh stub carries `value: ''`, so a date-filter test run against default
// stubs would find both date inputs empty and observe a request with no date
// params -- on a build with no gate at all. That is the fixture manufacturing
// the state that hides the bug. Those cases pass real stubs holding real dates.
function loadRetail({ capabilities, fetchImpl, elements }) {
  const code = fs.readFileSync(RETAIL_FILE, 'utf8');
  const els = elements || {};
  const sandbox = {
    console,
    t: (s) => s,
    setTimeout, clearTimeout,
    // WHATWG global, not an ECMAScript one -- a bare vm context does not carry
    // it and _loadAuditLog builds its query string with it.
    URLSearchParams,
    fetch: fetchImpl,
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    document: {
      getElementById(id) { return els[id] || makeElementStub(); },
      createElement() { return makeElementStub(); },
      querySelector(sel) { return els[sel] || makeElementStub(); },
      head: { appendChild() {} },
      body: makeElementStub(),
      documentElement: { getAttribute() { return null; } },
    },
    SubsystemApp: {
      capabilities: capabilities,
      hasCapability(code) {
        if (!Array.isArray(this.capabilities)) return true;
        return this.capabilities.includes(code);
      },
      showToast() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: RETAIL_FILE });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return sandbox;
}

// ═══════════════════════════════════════════════════════════════════════════
// DEFECT 1 — the shell must read capabilities from where the server puts them
// ═══════════════════════════════════════════════════════════════════════════

// The single assertion that would have caught the whole dead-gate wave: feed
// the shell the REAL session payload and ask it the question the cashier
// landing asks.
function testShellReadsTopLevelCapabilities() {
  const shell = loadShell();
  const App = shell.SubsystemApp;

  App._adoptSessionCapabilities({
    authenticated: true,
    capabilities: CASHIER_CAPS,
    is_mt: true,
    language: 'en',
    user: { id: 'u-1', role: 'cashier', email: 'c@x.local', clinic_role: '' },
  });

  assert.deepStrictEqual(
    App.capabilities, CASHIER_CAPS,
    'SubsystemApp did not pick up the top-level `capabilities` array that ' +
    '/api/auth/session actually returns. This is the exact shape the server ' +
    'sends; reading `sess.user.capabilities` instead leaves this null and ' +
    'makes every capability gate in the app inert.'
  );
  assert.strictEqual(
    App.hasCapability('retail.reports'), false,
    'A cashier must not be told they hold retail.reports. ROLE_CAPABILITIES ' +
    'grants a cashier only {sell, refund, cash.close}; answering true here is ' +
    'what let the dashboard and Reports screens render and then collect 403s.'
  );
  assert.strictEqual(
    App.hasCapability('retail.sell'), true,
    'A capability the cashier genuinely holds must still answer true.'
  );
}

// ── The three wire states, parsed from wire bytes ───────────────────────────
//
// `capabilities` arrives over HTTP as JSON, and JSON has three distinguishable
// answers here that the shell treats three different ways:
//
//   absent key   -> UNKNOWN, fail OPEN  (render; let the server refuse)
//   null         -> UNKNOWN, fail OPEN  (same state, different bytes)
//   []           -> genuinely DENIED, fail CLOSED
//   [codes...]   -> use them
//
// The middle one had no test on either fixture axis: the cases below used to
// pass hand-built JS object literals, which can express "absent" and "[]" but
// which nobody had written a `null` for. That gap matters because `null` is
// the state a JS author is most likely to fold into `[]` -- both are "falsy-
// ish, no codes in it" to the eye, and `Array.isArray` is the only thing that
// tells them apart. Collapse null into [] and every account on a build that
// sends an explicit null is denied every gated screen at once.
//
// Parsed with JSON.parse from raw response text rather than written as
// literals, for the same reason Android's SessionCapabilityContractTest runs
// its three through a real Gson parse: a literal is the test author's opinion
// of the wire, and the whole capability bug was a case of everyone checking
// the client against the client. `_wire()` also asserts the parsed shape is
// the one the case claims -- absent-vs-null is a one-character difference in
// the fixture and an invisible one in a debugger, so a case meant to pin
// `null` that silently degraded into the absent-key case would still pass and
// would still be testing nothing new.
function _wire(text, expectKeyPresent) {
  const body = JSON.parse(text);
  assert.strictEqual(
    Object.prototype.hasOwnProperty.call(body, 'capabilities'), expectKeyPresent,
    'Fixture check: this case is about a `capabilities` key that is ' +
    (expectKeyPresent ? 'PRESENT' : 'ABSENT') + ', and the parsed body disagrees. ' +
    'Parsed: ' + text
  );
  return body;
}

// Empty-array and absent-field are different states and must stay different:
// one means "this user was granted nothing", the other means "the server did
// not tell us". Folding them together would either blank the UI for a user
// with no grants or, worse, silently re-open every gate.
function testEmptyGrantListIsNotTreatedAsUnknown() {
  const shell = loadShell();
  const App = shell.SubsystemApp;

  App._adoptSessionCapabilities(_wire(
    '{"authenticated": true, "capabilities": [], "user": {"id": "u", "role": "cashier"}}', true));
  assert.deepStrictEqual(App.capabilities, [], 'An explicitly empty grant list must be stored as an empty array, not collapsed to null.');
  assert.strictEqual(
    App.hasCapability('retail.sell'), false,
    'A user denied every capability must be denied every capability -- an ' +
    'empty array must not fall into the same "unknown, fail open" bucket as a ' +
    'missing field.'
  );
}

// The fail-open default is deliberate (rendering advice only, every route
// keeps its own server-side gate) and must survive this fix: a build whose
// session response carries no capability information at all must not blank
// out every gated screen for every role.
function testMissingCapabilityFieldStillFailsOpen() {
  const shell = loadShell();
  const App = shell.SubsystemApp;

  App._adoptSessionCapabilities(_wire(
    '{"authenticated": true, "user": {"id": "u", "role": "admin"}}', false));
  assert.strictEqual(App.capabilities, null, 'A session response with no capability information must leave `capabilities` null.');
  assert.strictEqual(
    App.hasCapability('retail.reports'), true,
    'With no capability information available the shell must fail OPEN, so a ' +
    'server build that predates this field does not hide every gated screen.'
  );
}

// The third state, and the one that had no fixture. `{"capabilities": null}`
// is a key the server DID send carrying no answer -- a serializer that emits
// nulls, a session not resolved yet, a route that computed the list and got
// None. It means exactly what an absent key means (nothing has been
// established) and must land in the same fail-OPEN bucket, NOT in `[]`'s
// fail-closed one.
//
// The failure this pins is not hypothetical arithmetic: `null` and `[]` are
// the two shapes a reader glances past as "empty", and the natural-looking
// normalisation `this.capabilities = sess.capabilities || []` produces exactly
// the collapse. On a build whose session route ever answered null, that one
// line denies the Reports screen, the Audit Log and the cashier dashboard to
// every role in the shop including the owner -- an outage produced by a line
// that reads like tidying up. Android pins the same three states through Gson
// (SessionCapabilityContractTest::an_absent_capabilities_key_stays_unknown_
// and_keeps_failing_open); this is the desktop twin of that case.
function testExplicitNullCapabilitiesIsUnknownNotDenied() {
  const shell = loadShell();
  const App = shell.SubsystemApp;

  App._adoptSessionCapabilities(_wire(
    '{"authenticated": true, "capabilities": null, "user": {"id": "u", "role": "admin"}}', true));

  assert.strictEqual(
    App.capabilities, null,
    'An explicit JSON `null` must resolve to the UNKNOWN state. Storing `[]` ' +
    'here would turn "the server told us nothing" into "the server granted ' +
    'nothing" and deny every gated screen to every role.'
  );
  assert.strictEqual(
    App.hasCapability('retail.reports'), true,
    'With `capabilities: null` on the wire the shell must fail OPEN, exactly ' +
    'as it does for an absent key. Failing closed here is an outage, not a ' +
    'tightening: the server-side gate on every route is what actually enforces ' +
    'this, and the client list is rendering advice.'
  );

  // The other half, in the same case: proving `null` fails open is only worth
  // something if `[]` still fails closed on the same shell. A shell that
  // simply answered true for everything would satisfy the two assertions
  // above and would be the original dead-gate bug restored.
  App._adoptSessionCapabilities(_wire(
    '{"authenticated": true, "capabilities": [], "user": {"id": "u", "role": "admin"}}', true));
  assert.strictEqual(
    App.hasCapability('retail.reports'), false,
    'Control: `[]` must still DENY on the same shell that let `null` through. ' +
    'Without this, "null fails open" is satisfied by a gate that never refuses ' +
    'anyone -- which is the bug this whole file was written for.'
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// DEFECT 2 — the Reports nav entry
// ═══════════════════════════════════════════════════════════════════════════

function renderNav(capabilities, role) {
  const shell = loadShell();
  const App = shell.SubsystemApp;
  App.capabilities = capabilities;
  App.role = role || 'cashier';
  App.isAdminDevice = true;      // isolate the capability axis from the device axis
  App.activeModules = [];
  App.active = 'retail';

  const shellEl = makeElementStub();
  shell.document.getElementById = (id) => (id === 'subsystem-shell' ? shellEl : makeElementStub());
  App._renderShell(App.systems.retail, 'retail');
  return shellEl.innerHTML;
}

function testReportsNavHiddenForCashier() {
  const html = renderNav(CASHIER_CAPS, 'cashier');
  assert.ok(
    !/_navigate\('reports'\)/.test(html),
    'The Reports nav entry is still rendered for a cashier. Every panel on ' +
    'that screen reads a retail.reports-gated route, so this link is an ' +
    'invitation to collect five 403s in one page load.'
  );
  assert.ok(
    /_navigate\('pos'\)/.test(html),
    'Sanity: the ungated Point of Sale entry must still be present for a ' +
    'cashier -- otherwise this test would pass on a nav that renders nothing.'
  );
}

function testReportsNavVisibleForOwner() {
  const html = renderNav(ADMIN_CAPS, 'admin');
  assert.ok(
    /_navigate\('reports'\)/.test(html),
    'The Reports nav entry disappeared for an owner who holds retail.reports. ' +
    'The gate is meant to hide a screen the server would refuse, not the screen itself.'
  );
}

// The nav filter must not start denying on a build where capabilities are
// unknown -- same fail-open contract as hasCapability(), asserted at the nav
// layer because that is a second, independent place it could be got wrong.
function testReportsNavVisibleWhenCapabilitiesUnknown() {
  const html = renderNav(null, 'admin');
  assert.ok(
    /_navigate\('reports'\)/.test(html),
    'With capabilities unknown (null) the nav must fail OPEN and still show ' +
    'Reports, matching hasCapability()\'s documented default.'
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// DEFECT 2b — the render path, which the nav does not protect
// ═══════════════════════════════════════════════════════════════════════════

async function testRenderReportsMakesNoRequestForCashier() {
  const calls = [];
  const loudFetch = (url) => {
    calls.push(url);
    throw new Error('Reports fetched ' + url + ' for a user without retail.reports');
  };
  const sandbox = loadRetail({ capabilities: CASHIER_CAPS, fetchImpl: loudFetch });
  const content = makeElementStub();

  await sandbox.RetailSystem._renderReports(content);

  assert.deepStrictEqual(
    calls, [],
    'RetailSystem._renderReports() issued ' + calls.length + ' request(s) for a ' +
    'cashier: ' + JSON.stringify(calls) + '. Hiding the nav entry does not stop ' +
    'this -- the hash router replays the last section on launch, so a shared ' +
    'till lands the next cashier here with no nav click. A screen that fetches, ' +
    'gets 403 and then hides has still generated the error.'
  );
  assert.ok(
    content.innerHTML.length > 0,
    'The Reports screen rendered nothing at all for a cashier. Degrading has to ' +
    'mean an honest explanation, not a blank panel that reads like a crash.'
  );
  assert.ok(
    /limited to managers and the store owner/.test(content.innerHTML),
    'Expected the same plain-English explanation the cashier dashboard landing ' +
    'already uses (it is an existing translated catalog key). Got: ' +
    content.innerHTML.slice(0, 400)
  );
}

async function testRenderReportsStillLoadsForOwner() {
  const calls = [];
  const okFetch = (url) => {
    calls.push(url);
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: 'success', data: [], labels: [] }) });
  };
  const sandbox = loadRetail({ capabilities: ADMIN_CAPS, fetchImpl: okFetch });
  const content = makeElementStub();

  await sandbox.RetailSystem._renderReports(content);

  assert.ok(
    calls.some(u => /\/reports\/summary/.test(u)),
    'An owner holding retail.reports must still get the real Reports screen. ' +
    'Requests seen: ' + JSON.stringify(calls)
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// DEFECT 2c — the SECOND retail.reports-gated screen, which got half the fix
// ═══════════════════════════════════════════════════════════════════════════
//
// Audit Log carries `capability: 'retail.reports'` in the same nav list, and
// GET /api/sub/retail/audit-log is gated on that capability server-side (see
// list_audit_log's decorator stack and its docstring: the device check answers
// "is this the shop's admin terminal", the capability answers "is this person
// allowed to read the shop's records"). Its nav entry was gated in the same
// change that gated Reports -- and that change's own comment says, of Reports,
// "Hiding the entry is half the fix. The other half is in
// subsystem-retail.js's `_renderReports`". Audit Log never got the other half.
//
// The reachability argument is identical and is not hypothetical: AuraRouter
// persists the last section into the URL hash and _navigate() replays it on
// the next launch, and `audit-log` is a live case in that switch. An owner who
// last looked at the audit trail on the shop's admin terminal leaves the next
// cashier on that screen with no nav click involved.
//
// One extra wrinkle that makes the render guard MORE necessary here rather
// than less: _loadAuditLog's error branch already renders the server's refusal
// message. So today this path does not even look broken -- it fetches, takes
// the 403, and prints "permission denied" in the table. The request still
// happened, and the guard exists so it doesn't.
async function testRenderAuditLogMakesNoRequestForCashier() {
  const calls = [];
  const loudFetch = (url) => {
    calls.push(url);
    throw new Error('Audit Log fetched ' + url + ' for a user without retail.reports');
  };
  const sandbox = loadRetail({ capabilities: CASHIER_CAPS, fetchImpl: loudFetch });
  const content = makeElementStub();

  await sandbox.RetailSystem._renderAuditLog(content);

  assert.deepStrictEqual(
    calls, [],
    'RetailSystem._renderAuditLog() issued ' + calls.length + ' request(s) for a ' +
    'cashier: ' + JSON.stringify(calls) + '. The nav entry is hidden, which is ' +
    'half the fix; the hash router reaches this render function without it.'
  );
  assert.ok(
    content.innerHTML.length > 0,
    'The Audit Log screen rendered nothing at all for a cashier. Degrading has ' +
    'to mean an honest explanation, not a blank panel that reads like a crash.'
  );
}

async function testRenderAuditLogStillLoadsForOwner() {
  const calls = [];
  const okFetch = (url) => {
    calls.push(url);
    return Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve({ status: 'success', data: [], meta: { total: 0, page: 1, limit: 50, actions: [], entities: [] } }),
    });
  };
  const sandbox = loadRetail({ capabilities: ADMIN_CAPS, fetchImpl: okFetch });
  const content = makeElementStub();

  await sandbox.RetailSystem._renderAuditLog(content);

  assert.ok(
    calls.some(u => /\/audit-log/.test(u)),
    'An owner holding retail.reports must still get the real Audit Log. ' +
    'Requests seen: ' + JSON.stringify(calls)
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// DEFECT 3 — Sales History, the reports-consuming screen with NO gate at all
// ═══════════════════════════════════════════════════════════════════════════
//
// GET /sales/recent is deliberately NOT route-gated on retail.reports: the
// returns counter needs it (`_findSaleForReturn` resolves a receipt number
// before a refund, and retail.refund is a cashier default), so refusing the
// whole route would refuse a cashier a lookup the product grants them. The
// route splits instead (see recent_sales' docstring):
//
//   * `q` and a plain recent page  -> served at any capability. TILL HALF.
//   * `date_from` / `date_to`      -> 403 without retail.reports, each bound
//                                     refused on its own. REPORTS HALF.
//   * `limit`                      -> clamped to TILL_SALES_LOOKUP_MAX_LIMIT
//                                     (200) without it, SALES_HISTORY_MAX_LIMIT
//                                     (500) with it.
//
// The client knew none of that. `_loadSalesHistory` asked for `limit=300`
// unconditionally and put whatever was in the two date inputs on the wire.
//
// ── What the refused caller actually saw, which is worse than a console line ──
//
// `_fetch` only throws on 401. A 403 comes back as a resolved Response, so
// `(await this._get(url)).data || []` reads `undefined` off the error envelope,
// falls through to `[]`, and the screen renders "No sales found." with a count
// of 0. Not an error, not an empty screen, not a console line: a cashier who
// picks a date range is TOLD THE SHOP HAS NO SALES IN IT. A false statement
// about the books, rendered as if it were an answer. The `catch (e) {
// console.error(e) }` arm is real but is not the path a 403 takes -- it is
// reached only on 401 or a transport failure, and it too shows the user
// nothing.
//
// Both halves are fixed and both are pinned below: the screen no longer OFFERS
// the reports-gated control to a caller who cannot use it, `_loadSalesHistory`
// refuses to put a date bound on the wire regardless of what is in the DOM, and
// an error envelope is no longer rendered as an empty shop.

// A stub that behaves like a filled-in <input>. `_loadSalesHistory` reads
// `.value` off these, so this is where the bug's own input state comes from.
function inputStub(value) {
  const el = makeElementStub();
  el.value = value;
  return el;
}

// The DOM a cashier's Sales History screen is asked to load from when both
// date inputs are populated -- the exact state the old code turned into a 403.
function salesHistoryDom(from, to, q) {
  return {
    'sh-search': inputStub(q || ''),
    'sh-date-from': inputStub(from || ''),
    'sh-date-to': inputStub(to || ''),
    'sh-count': makeElementStub(),
    '#sh-table tbody': makeElementStub(),
  };
}

function paramsOf(url) {
  return new URLSearchParams(String(url).split('?')[1] || '');
}

// The count line AS READ, not as marked up. The count is wrapped in <bdi> (an
// integer beside Arabic words in an RTL line reorders without it), and a naive
// `innerHTML` assertion sees `<bdi>1</bdi> sales shown` -- where "1" and "sales"
// are not adjacent, so a plural-form check written against the raw HTML matches
// nothing and passes on the bug. Measured: the singular-form mutation stayed
// GREEN until this stripped the tags.
function countLine(dom) {
  const el = dom['sh-count'];
  return String(el.innerHTML + el.textContent).replace(/<[^>]*>/g, '');
}

function okSales(rows) {
  return () => Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve({ status: 'success', data: rows }),
  });
}

// The real refusal body, copied from recent_sales' own `jsonify` call rather
// than invented. retail_attribution_i18n_test.py asserts the live server still
// answers exactly this, so this fixture cannot drift away from the wire.
const REFUSAL_403 = {
  status: 'error',
  error: 'You do not have permission for this action. Ask your store administrator.',
  message: 'You do not have permission for this action. Ask your store administrator.',
  code: 403,
};

// The load path, not the render path. A cashier reaches this with dates in the
// DOM by a route the render guard cannot cover: `_renderSalesHistory` runs
// once, and `onchange`/`oninput`/`_clearSalesFilters` all call
// `_loadSalesHistory` directly afterwards. Populating both inputs here is the
// point of the case -- run it against default stubs and it passes on a build
// with no gate whatsoever, because empty inputs contribute no params.
async function testSalesHistoryNeverPutsARefusedDateBoundOnTheWire() {
  const calls = [];
  const sandbox = loadRetail({
    capabilities: CASHIER_CAPS,
    fetchImpl: (url) => { calls.push(url); return okSales([])(); },
    elements: salesHistoryDom('2019-01-01', '2026-12-31'),
  });

  await sandbox.RetailSystem._renderSalesHistory(makeElementStub());
  calls.length = 0;                       // isolate the reload the filters trigger
  await sandbox.RetailSystem._loadSalesHistory();

  assert.ok(calls.length > 0, 'Sanity: _loadSalesHistory issued no request at all.');
  const offenders = calls.filter(u => paramsOf(u).has('date_from') || paramsOf(u).has('date_to'));
  assert.deepStrictEqual(
    offenders, [],
    'Sales History sent a date bound for a user without retail.reports: ' +
    JSON.stringify(offenders) + '. recent_sales refuses date_from and date_to ' +
    'individually for that caller, and the refusal comes back as a resolved 403 ' +
    'whose envelope has no `data` -- which this screen renders as "No sales ' +
    'found.". The user is told the shop has no sales in the range they asked ' +
    'for. The guard has to be in _loadSalesHistory, not only in the markup: ' +
    'the date inputs are read on every reload, from a DOM this function does ' +
    'not own.'
  );
}

// The till half. The whole reason /sales/recent is not route-gated is that
// looking a receipt up to take a return is a cashier's job, so "gated" must not
// degrade into "blank".
async function testSalesHistoryStillServesTheTillForACashier() {
  const calls = [];
  const sandbox = loadRetail({
    capabilities: CASHIER_CAPS,
    fetchImpl: (url) => { calls.push(url); return okSales([{ id: 1, sale_number: 'S-1', total: 5 }])(); },
    elements: salesHistoryDom('', '', 'S-1'),
  });
  const content = makeElementStub();

  await sandbox.RetailSystem._renderSalesHistory(content);

  assert.ok(
    calls.some(u => /\/sales\/recent/.test(u)),
    'A cashier got no recent-sales request at all. Requests seen: ' +
    JSON.stringify(calls) + '. Gating the date filters must not gate the ' +
    'lookup: retail.refund is a cashier default and the returns flow starts by ' +
    'finding the receipt.'
  );
  assert.ok(
    /S-1/.test(content.innerHTML) || calls.length > 0,
    'The till half of Sales History rendered nothing.'
  );
  assert.ok(
    paramsOf(calls.find(u => /\/sales\/recent/.test(u))).get('q') === 'S-1',
    'The receipt search must still reach the server for a cashier -- `q` stays ' +
    'open at any capability precisely because a receipt number is a single-sale ' +
    'question.'
  );
}

// The honest explanation. Not "renders something": the specific control that
// was taken away has to be accounted for in words, or the screen reads as a
// build that forgot the feature.
async function testSalesHistoryExplainsTheMissingDateFiltersToACashier() {
  const sandbox = loadRetail({
    capabilities: CASHIER_CAPS,
    fetchImpl: okSales([]),
    elements: salesHistoryDom(),
  });
  const content = makeElementStub();

  await sandbox.RetailSystem._renderSalesHistory(content);

  assert.ok(
    !/id="sh-date-from"/.test(content.innerHTML) && !/id="sh-date-to"/.test(content.innerHTML),
    'The date inputs are still offered to a cashier. Every use of them is a 403 ' +
    'the user cannot act on.'
  );
  assert.ok(
    /limited to managers and the store owner/.test(content.innerHTML),
    'No explanation of the missing date filters. Got: ' + content.innerHTML.slice(0, 600)
  );
  assert.ok(
    /id="sh-search"/.test(content.innerHTML),
    'Sanity: the receipt search box must survive -- otherwise this case would ' +
    'pass on a screen that renders no controls at all.'
  );
}

async function testSalesHistoryKeepsTheDateFiltersForAnOwner() {
  const calls = [];
  const sandbox = loadRetail({
    capabilities: ADMIN_CAPS,
    fetchImpl: (url) => { calls.push(url); return okSales([])(); },
    elements: salesHistoryDom('2026-01-01', '2026-02-01'),
  });
  const content = makeElementStub();

  await sandbox.RetailSystem._renderSalesHistory(content);

  assert.ok(
    /id="sh-date-from"/.test(content.innerHTML) && /id="sh-date-to"/.test(content.innerHTML),
    'The date filters disappeared for an owner holding retail.reports. The gate ' +
    'is meant to hide a control the server would refuse, not the feature.'
  );
  const p = paramsOf(calls.find(u => /\/sales\/recent/.test(u)));
  assert.strictEqual(p.get('date_from'), '2026-01-01', 'An owner\'s date_from must reach the server.');
  assert.strictEqual(p.get('date_to'), '2026-02-01', 'An owner\'s date_to must reach the server.');
}

// "Do not ask for more than you show." The server clamps /sales/recent at both
// ends (clamp_page_limit, ceiling SALES_HISTORY_MAX_LIMIT=500) and applies a
// tighter TILL_SALES_LOOKUP_MAX_LIMIT=200 to a caller without retail.reports.
// A client that asks for 300 anyway is served 200 and then reports the 200 rows
// it received as if they were all the shop had -- the same class of false
// statement as "No sales found.", one page deeper. The fix is to ask for what
// the server will actually give, so the truncation notice fires correctly.
//
// Asserted against the constants the file itself publishes, and those are in
// turn pinned to the backend's own numbers by
// retail_attribution_i18n_test.py::test_the_client_page_sizes_match_the_server_caps.
// Hardcoding 200 here would make this test agree with the client no matter what
// the server does, which is the whole failure being defended against.
async function testSalesHistoryAsksForNoMoreRowsThanTheServerWillServe() {
  const calls = [];
  const cashier = loadRetail({
    capabilities: CASHIER_CAPS,
    fetchImpl: (url) => { calls.push(url); return okSales([])(); },
    elements: salesHistoryDom(),
  });
  await cashier.RetailSystem._renderSalesHistory(makeElementStub());

  const tillLimit = cashier.RetailSystem._SH_TILL_LIMIT;
  assert.ok(
    Number.isInteger(tillLimit),
    'RetailSystem._SH_TILL_LIMIT is not published as a number, so neither this ' +
    'test nor the cross-language pin can check the client against the server cap.'
  );
  assert.strictEqual(
    paramsOf(calls.find(u => /\/sales\/recent/.test(u))).get('limit'), String(tillLimit),
    'A caller without retail.reports asked for a page the server will silently ' +
    'shrink. Requests seen: ' + JSON.stringify(calls)
  );

  const ownerCalls = [];
  const owner = loadRetail({
    capabilities: ADMIN_CAPS,
    fetchImpl: (url) => { ownerCalls.push(url); return okSales([])(); },
    elements: salesHistoryDom(),
  });
  await owner.RetailSystem._renderSalesHistory(makeElementStub());
  assert.strictEqual(
    paramsOf(ownerCalls.find(u => /\/sales\/recent/.test(u))).get('limit'),
    String(owner.RetailSystem._SH_PAGE_LIMIT),
    'A reports-holding caller must ask for the full viewer page size.'
  );
}

// The truncation notice has to key off the page size actually requested, not a
// literal. The old code asked for 300 and compared `data.length === 300` in a
// second, independent literal -- so a cashier served the till's 200 rows was
// told "200 sales", full stop, as though that were the whole book.
async function testATruncatedPageSaysSoRatherThanReportingItselfAsTheWholeBook() {
  const cashierRows = [];
  const cashier = loadRetail({ capabilities: CASHIER_CAPS, fetchImpl: () => okSales(cashierRows)(), elements: salesHistoryDom() });
  const tillLimit = cashier.RetailSystem._SH_TILL_LIMIT;
  for (let i = 0; i < tillLimit; i += 1) cashierRows.push({ id: i, sale_number: 'S' + i, total: 1 });

  const dom = salesHistoryDom();
  const full = loadRetail({ capabilities: CASHIER_CAPS, fetchImpl: () => okSales(cashierRows)(), elements: dom });
  await full.RetailSystem._renderSalesHistory(makeElementStub());
  const truncated = countLine(dom);
  assert.ok(
    /most recent/.test(truncated),
    'A page filled exactly to the requested limit must say it is the most ' +
    'recent slice, not present itself as the shop\'s whole history. Count line: ' +
    JSON.stringify(truncated)
  );
  // Told to search rather than to pick a date range, because a date range is
  // the one thing this caller is refused.
  assert.ok(
    !/date range/.test(truncated),
    'A caller without retail.reports was advised to narrow with a date range -- ' +
    'the exact control the server refuses them. Count line: ' + JSON.stringify(truncated)
  );

  const shortDom = salesHistoryDom();
  const short = loadRetail({
    capabilities: CASHIER_CAPS,
    fetchImpl: () => okSales([{ id: 1, sale_number: 'S1', total: 1 }])(),
    elements: shortDom,
  });
  await short.RetailSystem._renderSalesHistory(makeElementStub());
  const partial = countLine(shortDom);
  assert.ok(
    !/most recent/.test(partial),
    'A page the server did NOT fill must not claim to be truncated. Count line: ' +
    JSON.stringify(partial)
  );
  // The count line used to read `sale${n===1?'':'s'}` -- English pluralisation
  // welded into the render, which cannot survive translation. Replacing it with
  // one plural key made "1 sales shown", so both forms are keys now and this
  // pins the singular. Cheap to assert and exactly the kind of detail a
  // translation refactor drops on the floor.
  assert.ok(
    !/\b1 sales\b/.test(partial),
    'One row was counted with the plural form. Count line: ' + JSON.stringify(partial)
  );
}

// The live regression, asserted on the body the server really sends. `_fetch`
// resolves a 403, so this never reaches the catch arm; the old code read
// `.data` off the error envelope, got undefined, and rendered the empty state.
async function testARefusalIsNotRenderedAsAnEmptyShop() {
  const dom = salesHistoryDom();
  const sandbox = loadRetail({
    capabilities: ADMIN_CAPS,          // owner: the gate is NOT what is under test here
    fetchImpl: () => Promise.resolve({ ok: false, status: 403, json: () => Promise.resolve(REFUSAL_403) }),
    elements: dom,
  });

  await sandbox.RetailSystem._renderSalesHistory(makeElementStub());

  const body = dom['#sh-table tbody'].innerHTML;
  assert.ok(
    !/No sales found/.test(body),
    'A refused (or failed) request was rendered as "No sales found." -- a ' +
    'statement about the shop\'s books that the client has no evidence for. ' +
    'Table body: ' + JSON.stringify(body)
  );
  assert.ok(
    /permission/i.test(body) || /Could not load/i.test(body),
    'A failed load must say so. Table body: ' + JSON.stringify(body)
  );
}

// The other failure arm: a transport error, which DOES reach the catch. It used
// to be `console.error(e)` and nothing else, leaving the "Loading…" placeholder
// on screen forever.
async function testATransportFailureIsNotSwallowedIntoTheConsole() {
  const dom = salesHistoryDom();
  const sandbox = loadRetail({
    capabilities: ADMIN_CAPS,
    fetchImpl: () => Promise.reject(new Error('ECONNRESET')),
    elements: dom,
  });

  await sandbox.RetailSystem._renderSalesHistory(makeElementStub());

  const body = dom['#sh-table tbody'].innerHTML;
  assert.ok(
    body.length > 0 && !/Loading/.test(body),
    'A failed request wrote nothing into the table body, so the "Loading…" ' +
    'placeholder the render put there stays on screen forever on a screen that ' +
    'has already given up -- the only trace is a console.error nobody reads. ' +
    'Table body: ' + JSON.stringify(body)
  );
  assert.ok(
    !/No sales found/.test(body),
    'A transport failure was reported as an empty shop. Table body: ' + JSON.stringify(body)
  );
}

// Every case is run even after one fails, and each failure is printed with
// its own name. Aborting on the first would hide how much of the surface is
// broken, which is precisely the signal wanted from a first (red) run.
const CASES = [
  testShellReadsTopLevelCapabilities,
  testEmptyGrantListIsNotTreatedAsUnknown,
  testMissingCapabilityFieldStillFailsOpen,
  testExplicitNullCapabilitiesIsUnknownNotDenied,
  testReportsNavHiddenForCashier,
  testReportsNavVisibleForOwner,
  testReportsNavVisibleWhenCapabilitiesUnknown,
  testRenderReportsMakesNoRequestForCashier,
  testRenderReportsStillLoadsForOwner,
  testRenderAuditLogMakesNoRequestForCashier,
  testRenderAuditLogStillLoadsForOwner,
  testSalesHistoryNeverPutsARefusedDateBoundOnTheWire,
  testSalesHistoryStillServesTheTillForACashier,
  testSalesHistoryExplainsTheMissingDateFiltersToACashier,
  testSalesHistoryKeepsTheDateFiltersForAnOwner,
  testSalesHistoryAsksForNoMoreRowsThanTheServerWillServe,
  testATruncatedPageSaysSoRatherThanReportingItselfAsTheWholeBook,
  testARefusalIsNotRenderedAsAnEmptyShop,
  testATransportFailureIsNotSwallowedIntoTheConsole,
];

async function main() {
  let failed = 0;
  for (const fn of CASES) {
    try {
      await fn();
      console.log('  ok   ' + fn.name);
    } catch (err) {
      failed += 1;
      console.error('  FAIL ' + fn.name);
      console.error('       ' + (err && err.message ? err.message : err));
    }
  }
  if (failed) {
    console.error(`FAIL: retail_reports_capability_gate_test.js — ${failed} of ${CASES.length} case(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`PASS: retail_reports_capability_gate_test.js — ${CASES.length} case(s)`);
  }
}

main();
