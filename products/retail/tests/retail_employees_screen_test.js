/**
 * Runtime tests for the Retail employee-management screen
 * (products/retail/frontend/employees.js), multi-device Phase 1, design §3.
 *
 * retail_employee_management_test.py covers the routes and asserts the served
 * assets contain what they must. Three of this screen's properties are not
 * assertable that way, because they are about what the code DOES rather than
 * what it contains:
 *
 *   1. "Degrade honestly for a non-admin rather than appearing and then
 *      failing." A screen that renders its table and only then discovers the
 *      403 has already failed that requirement, and it would still pass every
 *      static check -- the refusal string is in the file either way. What
 *      distinguishes the two is whether a non-owner render performs any FETCH
 *      AT ALL, which only a run can answer.
 *   2. The owner row must offer no role and no deactivate control. Grepping
 *      finds the branch; running it proves the branch produces the markup.
 *   3. Escaping. `users` is a shared, cross-device table in design §4, so an
 *      email on this screen is not guaranteed to be locally-typed text -- the
 *      same trust boundary the supplier/category modals already escape for.
 *
 * Same standalone shape as the other .js tests in this directory (this
 * frontend has no build step and no JS test framework -- see CLAUDE.md), with
 * only Node built-ins, loading the REAL file through `vm` rather than a
 * reimplementation:
 *
 *   node products/retail/tests/retail_employees_screen_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'employees.js');

// Breaks out of a double-quoted attribute and injects a tag -- the same two
// contexts the supplier/customer modal regressions cover, aimed here at the
// one field this screen renders from stored data.
const MALICIOUS_EMAIL = `a@b.com" onmouseover="alert(document.cookie)"><img src=x onerror=alert(1)>`;

function makeElementStub() {
  return {
    innerHTML: '',
    textContent: '',
    value: '',
    disabled: false,
    id: '',
    className: '',
    style: {},
    dataset: {},
    appendChild() {},
    // Stores the callback under `_on<type>` (e.g. `_onclick`) rather than a
    // real listener list -- this stub is only ever asked to hold ONE
    // listener per element/event, which is all production code registers,
    // and tests read it back directly (`el._onclick()`) to simulate a click
    // without needing a real DOM's event dispatch.
    addEventListener(type, fn) { this['_on' + type] = fn; },
    removeEventListener() {},
    remove() {},
    focus() {},
    select() {},
    setSelectionRange() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelector() { return makeElementStub(); },
    querySelectorAll() { return []; },
  };
}

/**
 * @param {object} opts
 *   role      -- SubsystemApp.role, i.e. session['mt_role']
 *   employees -- rows GET /api/admin/employees would return
 * Returns { screen, calls, toasts, overlays, content }
 */
function load(opts) {
  const options = opts || {};
  const calls = [];
  const toasts = [];
  const overlays = [];
  const content = makeElementStub();

  // URL-aware: _load() now ALSO fetches /api/sub/retail/branches (launch-
  // readiness account-hierarchy design §3.3/§4.2 D9, the branch-scope
  // picker) alongside the employee list -- a generic responder would have
  // handed that call `{employees: [...]}` too, silently masking the
  // `.data` shape the branch fetch actually expects.
  const respond = (url) => {
    if (String(url).includes('/api/sub/retail/branches')) {
      return { status: 'success', data: options.branches || [] };
    }
    return { success: true, employees: options.employees || [] };
  };

  const RetailSystemStub = {
    _injectStyles() {},
    _esc(v) {
      return String(v == null ? '' : v)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    },
    _badge(text, color) { return `<span class="ret-badge ret-badge-${color || 'blue'}">${text}</span>`; },
    async _get(url) { calls.push(['GET', url]); return respond(url); },
    async _post(url, body) { calls.push(['POST', url, body]); return { success: true, setup_link: 'http://x/#setup/tok' }; },
    async _put(url, body) { calls.push(['PUT', url, body]); return { success: true }; },
    async _del(url) { calls.push(['DELETE', url]); return { success: true }; },
  };

  const sandbox = {
    console,
    t: (s) => s,                       // stand-in for i18n.js's global t()
    confirm: () => true,
    navigator: {},                     // no Clipboard API -- the pywebview case
    RetailSystem: RetailSystemStub,
    SubsystemApp: {
      // `'role' in options`, not `options.role === undefined`: one of the
      // cases below is precisely "role is undefined", and the looser test
      // would silently hand it the 'admin' default and assert nothing.
      role: 'role' in options ? options.role : 'admin',
      showToast(msg, type) { toasts.push([msg, type]); },
    },
    document: {
      // Cached by id, unlike a fresh stub per call: production code (C6)
      // registers a listener via `document.getElementById('emp-role-btn')
      // .addEventListener(...)` right after building a modal, and a test
      // then fetches the SAME id to fire that listener and assert what it
      // was bound to do. A fresh object per call would silently detach the
      // two -- the listener would land on an object nobody could reach again.
      _byId: {},
      getElementById(id) {
        if (!this._byId[id]) this._byId[id] = makeElementStub();
        return this._byId[id];
      },
      createElement() { const el = makeElementStub(); overlays.push(el); return el; },
      querySelector() { return makeElementStub(); },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      body: { appendChild() {} },
    },
  };
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(FRONTEND_FILE, 'utf8'), sandbox, { filename: FRONTEND_FILE });

  assert.ok(sandbox.RetailEmployees, 'employees.js did not expose window.RetailEmployees');
  return { screen: sandbox.RetailEmployees, calls, toasts, overlays, content, doc: sandbox.document };
}

const OWNER_ROW = {
  id: 'u-owner', employee_id: 'ADMIN-0001', email: 'owner@shop.local',
  role: 'admin', effective_role: 'admin', status: 'active', has_pin: false,
};
const CASHIER_ROW = {
  id: 'u-cashier', employee_id: 'EMP-0002', email: 'till@shop.local',
  role: 'cashier', effective_role: 'cashier', status: 'active', has_pin: true,
};
const LEGACY_ROW = {
  id: 'u-legacy', employee_id: 'EMP-0003', email: 'legacy@shop.local',
  role: 'employee', effective_role: 'cashier', status: 'disabled', has_pin: false,
};

// ── 1. Honest degradation ────────────────────────────────────────────────────

async function testNonOwnerIsRefusedWithoutTouchingTheNetwork() {
  const { screen, calls, content } = load({ role: 'cashier' });
  await screen.render(content);

  assert.deepStrictEqual(
    calls, [],
    'a non-owner render must not call the API at all -- fetching first and ' +
    'showing the 403 afterwards is exactly the "appears and then fails" ' +
    'behaviour this screen is required not to have. Got: ' + JSON.stringify(calls)
  );
  assert.ok(
    content.innerHTML.includes('Employee management is available to the store owner only.'),
    'the refusal panel was not rendered. Got: ' + content.innerHTML
  );
  assert.ok(
    !content.innerHTML.includes('<table'),
    'a non-owner must not be shown the employee table at all'
  );
  console.log('PASS: a non-owner is refused up front, with no API call');
}

async function testAnEmptyRoleIsTreatedAsNotTheOwner() {
  // SubsystemApp.role is '' until GET /api/auth/session resolves. Fail closed.
  for (const role of ['', undefined, null, 'manager']) {
    const { screen, calls, content } = load({ role });
    await screen.render(content);
    assert.deepStrictEqual(calls, [], `role=${JSON.stringify(role)} reached the API`);
    assert.ok(content.innerHTML.includes('store owner only.'),
      `role=${JSON.stringify(role)} was treated as the owner`);
  }
  console.log('PASS: every non-admin role value fails closed');
}

async function testOwnerSeesTheTableAndItLoads() {
  const { screen, calls, content } = load({ role: 'admin', employees: [CASHIER_ROW] });
  await screen.render(content);
  assert.ok(content.innerHTML.includes('<table'), 'the owner was not shown the table');
  // _load() now fetches the branch list FIRST (for the scope picker/badge --
  // design §3.3/§4.2 D9), then the employee list -- two calls, not one.
  assert.deepStrictEqual(calls, [
    ['GET', '/api/sub/retail/branches'],
    ['GET', '/api/admin/employees'],
  ]);
  console.log('PASS: the owner gets the table and both list calls');
}

// ── 2. The owner row offers no role or status control ────────────────────────

// NOTE (C6): _row()'s action buttons carry the target id as a `data-id`
// attribute plus a `data-action="role"|"pin"|"status"` marker, read back by
// a delegated listener (_onTableClick), rather than an inline
// `onclick="RetailEmployees._openRole('${id}')"` call -- see the comment on
// _row() in employees.js. The assertions below therefore check for
// `data-action="..."` markers rather than for `_openRole`/`_setStatus`
// method-call substrings, which no longer appear in this markup at all.

function testOwnerRowHasNoRoleOrDeactivateControl() {
  const { screen } = load({});
  const html = screen._row(OWNER_ROW);

  assert.ok(!html.includes('data-action="role"'),
    'the owner row offers a role control -- update_role answers 409 for it, so ' +
    'the button could only ever produce an error');
  assert.ok(!html.includes('data-action="status"'),
    'the owner row offers a deactivate control. update_status has NO owner bar: ' +
    'it would succeed, and a disabled sole admin is an unrecoverable lockout ' +
    '(onboarding_status keeps saying needs_setup:false, create-admin keeps ' +
    'answering 409, and login is refused)');
  assert.ok(html.includes('data-action="pin"'),
    'the owner must still be able to set their own PIN -- they ring sales too, ' +
    'and a PIN grants nothing');
  console.log('PASS: the owner row exposes PIN only, no role and no deactivate');
}

function testEmployeeRowHasEveryControl() {
  const { screen } = load({});
  const html = screen._row(CASHIER_ROW);
  assert.ok(html.includes('data-action="role"'), 'no role control on an employee row');
  assert.ok(html.includes('data-action="pin"'), 'no PIN control on an employee row');
  assert.ok(html.includes('data-action="status"'), 'no status control on an employee row');
  assert.ok(html.includes('Reset PIN'), 'a row with has_pin should offer Reset, not Set');
  assert.ok(html.includes('Deactivate'), 'an active row should offer Deactivate');
  console.log('PASS: an employee row exposes role, PIN and deactivate');
}

function testDisabledRowOffersReactivation() {
  const { screen } = load({});
  const html = screen._row(LEGACY_ROW);
  assert.ok(html.includes('Reactivate'),
    'deactivation is reversible and the row has to say so -- a disabled row ' +
    'showing only "Deactivate" would look like a one-way door');
  assert.ok(html.includes('data-action="status" data-status="active" data-id="u-legacy"'),
    'the reactivate control does not send status=active for the right id');
  assert.ok(html.includes('Set PIN'), 'a row without a PIN should offer Set, not Reset');
  console.log('PASS: a deactivated row offers reactivation');
}

function testLegacyEmployeeRoleRendersAsItsEffectiveRole() {
  const { screen } = load({});
  // Stored 'employee', which normalize_role() reads as cashier everywhere a
  // capability decision is made. Showing the stored spelling would name a role
  // that is not in the widened domain and that no control can set.
  assert.ok(screen._row(LEGACY_ROW).includes('Cashier'),
    'a legacy role=employee row must render as its effective role');
  assert.ok(screen._row(OWNER_ROW).includes('Owner'));
  console.log('PASS: the role column shows the effective role');
}

// ── 3. Escaping ──────────────────────────────────────────────────────────────

function testRowEscapesTheEmail() {
  const { screen } = load({});
  const html = screen._row(Object.assign({}, CASHIER_ROW, { email: MALICIOUS_EMAIL }));
  assert.ok(!html.includes(MALICIOUS_EMAIL),
    'the raw email reached innerHTML unescaped. Got: ' + html);
  assert.ok(html.includes('&quot;'), 'expected the payload quotes to be escaped');
  console.log('PASS: the employee row escapes the email');
}

function testModalsEscapeTheEmail() {
  const evil = Object.assign({}, CASHIER_ROW, { email: MALICIOUS_EMAIL });
  for (const [name, open] of [['role', '_openRole'], ['PIN', '_openPin']]) {
    const ctx = load({});
    ctx.screen._rows = [evil];
    ctx.screen[open](evil.id);
    const overlay = ctx.overlays[ctx.overlays.length - 1];
    assert.ok(overlay, `the ${name} modal was never created`);
    assert.ok(!overlay.innerHTML.includes(MALICIOUS_EMAIL),
      `the ${name} modal leaked the raw email. Got: ` + overlay.innerHTML);
  }
  const ctx = load({});
  ctx.screen._showInviteLink(MALICIOUS_EMAIL, 'http://x/#setup/tok');
  const overlay = ctx.overlays[ctx.overlays.length - 1];
  assert.ok(!overlay.innerHTML.includes(MALICIOUS_EMAIL),
    'the invite-link dialog leaked the raw email. Got: ' + overlay.innerHTML);
  console.log('PASS: the role, PIN and invite dialogs escape the email');
}

// ── 4. The invite link ───────────────────────────────────────────────────────

function testInviteDialogStatesTheLinkTerms() {
  const ctx = load({});
  ctx.screen._showInviteLink('new@shop.local', 'http://x/#setup/tok');
  const html = ctx.overlays[ctx.overlays.length - 1].innerHTML;
  assert.ok(html.includes('This link works once and expires in 7 days.'),
    'the single-use / 7-day terms are not stated');
  assert.ok(html.includes('http://x/#setup/tok'), 'the link itself is not shown');
  assert.ok(html.includes('direction:ltr'),
    'the link input must be forced LTR -- rtl.css right-aligns every input, ' +
    'which renders a URL visually reordered and impossible to transcribe');
  console.log('PASS: the invite dialog shows the link and states its terms');
}

async function testAnEmptyEmailIsRefusedBeforeTheRequest() {
  // The form inputs are element stubs whose `.value` is '', so submitting
  // straight away exercises the empty-email path exactly.
  const ctx = load({});
  ctx.screen._openInvite();
  await ctx.screen._submitInvite();

  assert.deepStrictEqual(ctx.calls, [],
    'an empty email must not be sent to the server. Got: ' + JSON.stringify(ctx.calls));
  assert.ok(ctx.toasts.some(([m]) => m === 'Enter an email address.'),
    'no message was shown for the empty email. Got: ' + JSON.stringify(ctx.toasts));
  console.log('PASS: an empty email is refused before the request');
}

// ── 5. PIN ───────────────────────────────────────────────────────────────────

function testPinDialogStatesTheRuleAndAcceptsNonAsciiDigits() {
  const ctx = load({});
  ctx.screen._rows = [CASHIER_ROW];
  ctx.screen._openPin(CASHIER_ROW.id);
  const html = ctx.overlays[ctx.overlays.length - 1].innerHTML;

  assert.ok(html.includes('A PIN identifies who is acting. It does not grant permission.'),
    'the PIN dialog does not state design §3 rule');
  assert.ok(/type="text"/.test(html) && /inputmode="numeric"/.test(html),
    'the PIN input must be type=text + inputmode=numeric, never type=number: a ' +
    'number input will not hold the ARABIC-INDIC digits an Arabic soft keyboard ' +
    'emits, which would defeat the folding _normalize_pin exists to do');
  assert.ok(!/type="number"/.test(html), 'the PIN input is a number input');
  assert.ok(html.includes('Remove PIN'), 'a row with a PIN must offer to remove it');
  console.log('PASS: the PIN dialog states the rule and accepts non-ASCII digits');
}

function testPinDialogOffersNoRemovalWhenThereIsNoPin() {
  const ctx = load({});
  ctx.screen._rows = [OWNER_ROW];
  ctx.screen._openPin(OWNER_ROW.id);
  const html = ctx.overlays[ctx.overlays.length - 1].innerHTML;
  assert.ok(!html.includes('Remove PIN'), 'offered to remove a PIN that is not set');
  console.log('PASS: no removal control when no PIN is set');
}

// ── 6. Onclick quote breakout (C6) ────────────────────────────────────────────
//
// Same class of bug retail_supplier_name_apostrophe_onclick_test.js covers
// for supplier names: `_esc()` turns a `'` into `&#39;`, which is correct for
// HTML text/attribute VALUE but does not survive an attribute being decoded
// back into JS SOURCE, which is what a browser does before running an inline
// `onclick="...('${id}')"` handler. `id` here is a server-minted uuid4, so
// this was not reachable in practice -- but the fix is structural rather than
// relying on that: the id now travels only as attribute DATA
// (`data-id`/`dataset.id`), read by a delegated listener or bound directly
// into a JS closure, and is never serialized back into an inline handler's
// JS source at all. These tests prove that by construction rather than by
// re-deriving a browser's attribute-decode step for a mechanism that no
// longer has anything for it to decode INTO.

// A payload combining both the original apostrophe-breakout vector and a
// direct double-quote/tag breakout of the attribute itself, mirroring
// MALICIOUS_EMAIL above but aimed at `id`.
const MALICIOUS_ID = `u1" onmouseover="alert(document.cookie)"><img src=x onerror=alert(1)>`;

function testRowActionButtonsCarryIdAsDataNotAsInlineJs() {
  const { screen } = load({});
  const evilRow = Object.assign({}, CASHIER_ROW, { id: MALICIOUS_ID });
  const html = screen._row(evilRow);

  assert.ok(!/onclick="RetailEmployees\._(openRole|openPin|setStatus)\(/.test(html),
    'a row action button still builds an inline onclick call -- the id-in-JS-' +
    'source vector this fix removes is still present. Got: ' + html);
  assert.ok(!html.includes(MALICIOUS_ID),
    'the raw id reached the row markup unescaped. Got: ' + html);
  assert.ok(html.includes(`data-id="${'u1&quot; onmouseover=&quot;alert(document.cookie)&quot;&gt;&lt;img src=x onerror=alert(1)&gt;'}"`),
    'the escaped id was not found in a data-id attribute. Got: ' + html);
  console.log('PASS: row action buttons carry the id as data-id, never as inline onclick JS source');
}

function testDelegatedTableClickDispatchesOnDataActionWithTheExactId() {
  const { screen } = load({});
  const calls = [];
  screen._openRole = (id) => calls.push(['role', id]);
  screen._openPin  = (id) => calls.push(['pin', id]);
  screen._setStatus = (id, status) => calls.push(['status', id, status]);

  screen._onTableClick({ target: { closest: () => ({ dataset: { action: 'role', id: MALICIOUS_ID } }) } });
  screen._onTableClick({ target: { closest: () => ({ dataset: { action: 'pin', id: MALICIOUS_ID } }) } });
  screen._onTableClick({ target: { closest: () => ({ dataset: { action: 'status', id: MALICIOUS_ID, status: 'disabled' } }) } });
  // A click that lands somewhere other than an action button (closest()
  // finds nothing) must dispatch nothing at all.
  screen._onTableClick({ target: { closest: () => null } });

  assert.deepStrictEqual(calls, [
    ['role', MALICIOUS_ID],
    ['pin', MALICIOUS_ID],
    ['status', MALICIOUS_ID, 'disabled'],
  ], 'the delegated handler did not forward the exact original (unescaped, ' +
     'un-decoded) id for every action. Got: ' + JSON.stringify(calls));
  console.log('PASS: the delegated table-click handler dispatches on data-action/data-id with the exact original id');
}

function testRoleModalSaveButtonIsWiredByClosureNotInterpolatedId() {
  const ctx = load({});
  const evilRow = Object.assign({}, CASHIER_ROW, { id: MALICIOUS_ID });
  ctx.screen._rows = [evilRow];

  let savedWithId = null;
  ctx.screen._saveRole = (id) => { savedWithId = id; };
  ctx.screen._openRole(MALICIOUS_ID);

  const overlay = ctx.overlays[ctx.overlays.length - 1];
  assert.ok(!overlay.innerHTML.includes('_saveRole('),
    'the role modal Save button still embeds a call to _saveRole in its markup. Got: ' + overlay.innerHTML);

  const btn = ctx.doc.getElementById('emp-role-btn');
  assert.ok(btn && typeof btn._onclick === 'function',
    'the role modal Save button never registered a click listener');
  btn._onclick();
  assert.strictEqual(savedWithId, MALICIOUS_ID,
    'the Save button did not call _saveRole with the exact original id -- expected ' +
    'a JS closure, not a value round-tripped through HTML source');
  console.log('PASS: the role modal Save button is wired via a closure, immune to quote breakout by construction');
}

function testPinModalSaveAndClearButtonsAreWiredByClosureNotInterpolatedId() {
  const ctx = load({});
  const evilRow = Object.assign({}, CASHIER_ROW, { id: MALICIOUS_ID });
  ctx.screen._rows = [evilRow];

  let savedWithId = null;
  let clearedWithId = null;
  ctx.screen._savePin = (id) => { savedWithId = id; };
  ctx.screen._clearPin = (id) => { clearedWithId = id; };
  ctx.screen._openPin(MALICIOUS_ID);

  const overlay = ctx.overlays[ctx.overlays.length - 1];
  assert.ok(!overlay.innerHTML.includes('_savePin(') && !overlay.innerHTML.includes('_clearPin('),
    'the PIN modal Save/Remove buttons still embed calls in their markup. Got: ' + overlay.innerHTML);

  const saveBtn = ctx.doc.getElementById('emp-pin-btn');
  const clearBtn = ctx.doc.getElementById('emp-pin-clear-btn');
  assert.ok(saveBtn && typeof saveBtn._onclick === 'function', 'the PIN modal Save button never registered a click listener');
  assert.ok(clearBtn && typeof clearBtn._onclick === 'function', 'the PIN modal Remove PIN button never registered a click listener');

  saveBtn._onclick();
  clearBtn._onclick();
  assert.strictEqual(savedWithId, MALICIOUS_ID, '_savePin was not called with the exact original id via closure');
  assert.strictEqual(clearedWithId, MALICIOUS_ID, '_clearPin was not called with the exact original id via closure');
  console.log('PASS: the PIN modal Save/Remove buttons are wired via closures, immune to quote breakout by construction');
}

// ── 7. Failure surfacing ─────────────────────────────────────────────────────

function testServerRefusalsAreSurfacedRatherThanSwallowed() {
  const ctx = load({});
  ctx.screen._fail({ error: 'Email already registered.' }, 'FALLBACK');
  ctx.screen._fail({ success: false }, 'FALLBACK');
  assert.deepStrictEqual(ctx.toasts, [
    ['Email already registered.', 'error'],
    ['FALLBACK', 'error'],
  ], 'server refusals must reach the user verbatim, and a bodyless failure ' +
     'must still say something. Got: ' + JSON.stringify(ctx.toasts));
  console.log('PASS: server refusals are surfaced, and a silent failure still speaks');
}

async function main() {
  await testNonOwnerIsRefusedWithoutTouchingTheNetwork();
  await testAnEmptyRoleIsTreatedAsNotTheOwner();
  await testOwnerSeesTheTableAndItLoads();
  testOwnerRowHasNoRoleOrDeactivateControl();
  testEmployeeRowHasEveryControl();
  testDisabledRowOffersReactivation();
  testLegacyEmployeeRoleRendersAsItsEffectiveRole();
  testRowEscapesTheEmail();
  testModalsEscapeTheEmail();
  testInviteDialogStatesTheLinkTerms();
  await testAnEmptyEmailIsRefusedBeforeTheRequest();
  testPinDialogStatesTheRuleAndAcceptsNonAsciiDigits();
  testPinDialogOffersNoRemovalWhenThereIsNoPin();
  testRowActionButtonsCarryIdAsDataNotAsInlineJs();
  testDelegatedTableClickDispatchesOnDataActionWithTheExactId();
  testRoleModalSaveButtonIsWiredByClosureNotInterpolatedId();
  testPinModalSaveAndClearButtonsAreWiredByClosureNotInterpolatedId();
  testServerRefusalsAreSurfacedRatherThanSwallowed();
  console.log('PASS: retail_employees_screen_test.js');
}

main().catch((err) => {
  console.error('FAIL: retail_employees_screen_test.js');
  console.error(err);
  process.exitCode = 1;
});
