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
function loadRetail({ capabilities, fetchImpl }) {
  const code = fs.readFileSync(RETAIL_FILE, 'utf8');
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
      getElementById() { return makeElementStub(); },
      createElement() { return makeElementStub(); },
      querySelector() { return makeElementStub(); },
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
