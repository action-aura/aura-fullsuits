/**
 * retail_s_presets_test.js -- the four role presets on the Employees screen
 * (P2S crew U, item 1). Pure logic, no browser: employees.js runs in a vm with
 * a hand-rolled document, and every assertion reads what the screen DID (the
 * calls it made, the badges it drew), not what its source says.
 *
 * The expected sets below are typed out here on purpose, from the server's
 * ROLE_PRESETS (commercial_runtime/identity/user_accounts.py). They are NOT
 * read from employees.js -- comparing the page to itself proves nothing. The
 * page-versus-server comparison is done against the real server by
 * retail_s_users_roles_e2e.py (case_table).
 *
 * Runs on Node built-ins:  node products/retail/tests/retail_s_presets_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'employees.js');

// Codes on the wire carry the 'retail.' prefix; the sets are typed short and
// prefixed once here so they stay readable next to the server's table.
const pre = (list) => list.map((c) => 'retail.' + c);
const ALL_CAPS = pre([
  'sell', 'refund', 'discount', 'stock.adjust', 'reports', 'cash.close',
  'cash.approve', 'employees', 'books.view', 'books.record', 'books.approve', 'books.close',
]);
const EXPECT = {
  cashier: pre(['sell', 'refund', 'cash.close']),
  manager: pre(['sell', 'refund', 'discount', 'stock.adjust', 'reports', 'cash.close']),
  branch_manager: pre(['sell', 'refund', 'discount', 'stock.adjust', 'reports', 'cash.close', 'employees']),
  accountant: pre(['books.view', 'books.record', 'reports', 'cash.approve']),
};
const BASE = { cashier: 'cashier', manager: 'manager', branch_manager: 'manager', accountant: 'manager' };

function stub() {
  return {
    innerHTML: '', textContent: '', value: '', disabled: false, hidden: false, id: '', className: '',
    style: {}, dataset: {}, appendChild() {}, remove() {}, focus() {}, select() {}, setSelectionRange() {},
    addEventListener(type, fn) { this['_on' + type] = fn; }, removeEventListener() {}, click() {},
    getAttribute() { return null; }, setAttribute() {},
    querySelector() { return stub(); }, querySelectorAll() { return []; },
  };
}

function load(opts) {
  const o = opts || {};
  const calls = [];
  const toasts = [];
  const fields = {};                    // element id -> value the test typed
  const byId = {};
  const RetailSystem = {
    _injectStyles() {},
    _esc(v) { return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); },
    _badge(text, colour) { return `<span class="b-${colour}">${text}</span>`; },
    _wireModalA11y(overlay) { overlay._a11yClose = () => overlay.remove(); return overlay._a11yClose; },
    async _get(url) {
      calls.push(['GET', url]);
      if (String(url).includes('/api/devices')) return { role_editing: o.roleEditing || { allowed: true, blocking_devices: [] } };
      if (String(url).includes('/branches')) return { status: 'success', data: [] };
      return { success: true, employees: o.employees || [] };
    },
    async _post(url, body) { calls.push(['POST', url, body]); return { success: true, setup_link: 'x' }; },
    async _put(url, body) { calls.push(['PUT', url, body]); return { success: true }; },
    async _del(url) { calls.push(['DELETE', url]); return { success: true }; },
  };
  const document = {
    // Null unless the test put something there: production code branches on
    // whether an element EXISTS (the legacy role select is absent when the
    // cards are drawn), so a stub that answers every id would hide that.
    getElementById(id) {
      if (!(id in fields) && !(id in byId)) return null;
      if (!byId[id]) byId[id] = stub();
      if (id in fields) byId[id].value = fields[id];
      return byId[id];
    },
    createElement() { return stub(); },
    querySelector() { return stub(); }, querySelectorAll() { return []; },
    head: { appendChild() {} }, body: { appendChild() {} },
  };
  const sandbox = {
    console, t: (s) => s, confirm: () => true, navigator: {}, RetailSystem, document,
    SubsystemApp: { role: 'admin', currentUser: { id: 'me' }, showToast(m, k) { toasts.push([m, k]); } },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(FRONTEND_FILE, 'utf8'), sandbox, { filename: FRONTEND_FILE });
  return { screen: sandbox.RetailEmployees, calls, toasts, fields, byId };
}

const tests = [];
// objects made inside the vm have another realm's prototypes; compare as plain data
const plain = (v) => JSON.parse(JSON.stringify(v));
function test(name, fn) { tests.push([name, fn]); }

// ---------------------------------------------------------------- the table
test('the page carries exactly the four presets the server has, with the server caps', () => {
  const { screen } = load();
  assert.deepStrictEqual(plain(Object.keys(screen._PRESET_CAPS).sort()), Object.keys(EXPECT).sort());
  for (const [name, caps] of Object.entries(EXPECT)) {
    assert.deepStrictEqual(plain([...screen._PRESET_CAPS[name]].sort()), [...caps].sort(), `${name}: caps differ from the server's`);
    assert.strictEqual(screen._PRESET_BASE_ROLE[name], BASE[name], `${name}: base role`);
  }
});

test('the matrix lists all twelve codes, once each', () => {
  const { screen } = load();
  const codes = screen._capRows().map((r) => r[0]);
  assert.strictEqual(codes.length, 12);
  assert.deepStrictEqual(plain([...codes].sort()), [...ALL_CAPS].sort());
});

test('no non-owner preset can both close and approve a cash drawer (AUDIT-032)', () => {
  const { screen } = load();
  for (const [name, caps] of Object.entries(screen._PRESET_CAPS)) {
    assert.ok(!(caps.includes('retail.cash.close') && caps.includes('retail.cash.approve')), `${name} holds both cash.close and cash.approve`);
  }
});

test('the drawn matrix has 48 cells and each one says what the table says', () => {
  const { screen } = load();
  const html = screen._matrixHtml('cashier');
  const cells = [...html.matchAll(/<td[^>]*data-preset="([^"]+)"[^>]*data-cap="([^"]+)"[^>]*data-has="([01])"/g)];
  assert.strictEqual(cells.length, 48, 'matrix cell count');
  for (const [, preset, cap, has] of cells) {
    assert.strictEqual(has, EXPECT[preset].includes(cap) ? '1' : '0', `${preset}/${cap}`);
  }
});

// -------------------------------------------------------------- Custom badge
test('Custom shows only when the server says the rows disagree', () => {
  const { screen } = load();
  const base = { effective_role: 'manager', role_preset: 'accountant' };
  assert.ok(!screen._roleBadge({ ...base, preset_matches: true }).includes('data-emp-custom'), 'agrees: no badge');
  assert.ok(screen._roleBadge({ ...base, preset_matches: false }).includes('data-emp-custom'), 'disagrees: badge');
  assert.ok(!screen._roleBadge({ ...base }).includes('data-emp-custom'), 'unknown agreement is not disagreement');
  assert.ok(!screen._roleBadge({ effective_role: 'manager' }).includes('data-emp-custom'), 'legacy row without a preset');
  assert.ok(!screen._roleBadge({ effective_role: 'admin', role_preset: 'accountant', preset_matches: false }).includes('data-emp-custom'), 'owner never');
});

// The role dialog has its own "custom permissions" notice, separate from the
// list badge. Mutant E4 (notice on every preset person) survived the first
// version of this file, so the dialog is now read directly.
test('the role dialog warns about custom permissions only when the rows disagree', () => {
  const { screen } = load();
  const drawn = {};
  screen._modal = (id, html) => { drawn[id] = html; };
  const NOTICE = 'This person has custom permissions.';
  const emp = { id: 'u1', email: 'a@b.c', role_preset: 'accountant', effective_role: 'manager' };
  screen._openPreset({ ...emp, preset_matches: true });
  assert.ok(!drawn['emp-role-modal'].includes(NOTICE), 'agreeing rows got the custom notice');
  screen._openPreset({ ...emp, preset_matches: false });
  assert.ok(drawn['emp-role-modal'].includes(NOTICE), 'disagreeing rows got no custom notice');
  screen._openPreset({ ...emp });
  assert.ok(!drawn['emp-role-modal'].includes(NOTICE), 'unknown agreement is not disagreement');
});

// ---------------------------------------------------------- what it sends
test('rename: 61 characters never leave the page, 60 do', async () => {
  const { screen, calls, toasts, fields } = load();
  fields['emp-name-input'] = 'x'.repeat(61);
  await screen._saveName('u1');
  assert.strictEqual(calls.filter((c) => c[0] === 'PUT').length, 0, '61 characters were sent');
  assert.strictEqual(toasts.length, 1);
  fields['emp-name-input'] = 'x'.repeat(60);
  await screen._saveName('u1');
  const puts = calls.filter((c) => c[0] === 'PUT');
  assert.strictEqual(puts.length, 1);
  assert.strictEqual(puts[0][1], '/api/admin/employees/u1/name');
});

test('rename: an emptied field is sent as an empty name (that clears it)', async () => {
  const { screen, calls, fields } = load();
  fields['emp-name-input'] = '   ';
  await screen._saveName('u1');
  const put = calls.find((c) => c[0] === 'PUT');
  assert.ok(put, 'no PUT was made');
  assert.deepStrictEqual(plain(put[2]), { name: '' });
});

test('a role edit for a preset person goes to /preset and never /role', async () => {
  const { screen, calls } = load();
  screen._selPreset = 'accountant';
  await screen._savePreset('u1');
  const writes = calls.filter((c) => c[0] === 'PUT');
  assert.strictEqual(writes.length, 1);
  assert.strictEqual(writes[0][1], '/api/admin/employees/u1/preset');
  assert.deepStrictEqual(plain(writes[0][2]), { preset: 'accountant' });
});

test('Change Role on a preset person opens the cards, not the two-role select', async () => {
  const { screen, byId } = load({ employees: [] });
  // V2: the screen starts in "checking" (presets OFF) until the device list has
  // answered, so the cards only exist after that answer.
  await screen._refreshGate();
  screen._rows = [{ id: 'u1', email: 'a@b.c', role_preset: 'accountant', preset_matches: true, effective_role: 'manager' }];
  let opened = null;
  screen._openPreset = (emp) => { opened = emp.id; };
  screen._openRole('u1');
  assert.strictEqual(opened, 'u1');
  assert.ok(!byId['emp-role-select'], 'the legacy select must not be drawn for a preset person');
});

test('invite: preset body carries preset + name, branch manager also carries the branch', async () => {
  const { screen, calls, fields } = load();
  screen._selPreset = 'branch_manager';
  Object.assign(fields, { 'emp-inv-email': 'bm@x.test', 'emp-inv-name': '  Bassam ', 'emp-inv-branch': 'br-1' });
  await screen._submitInvite(false);
  const post = calls.find((c) => c[0] === 'POST');
  assert.ok(post, 'no POST was made');
  assert.deepStrictEqual(plain(post[2]), { email: 'bm@x.test', role: 'manager', preset: 'branch_manager', branch_scope_uid: 'br-1', name: 'Bassam' });
});

test('invite: a branch manager without a branch is refused before anything is sent', async () => {
  const { screen, calls, toasts, fields } = load();
  screen._selPreset = 'branch_manager';
  Object.assign(fields, { 'emp-inv-email': 'bm@x.test', 'emp-inv-branch': '' });
  await screen._submitInvite(false);
  assert.strictEqual(calls.filter((c) => c[0] === 'POST').length, 0);
  assert.strictEqual(toasts.length, 1);
});

test('invite: no name typed means no name key at all', async () => {
  const { screen, calls, fields } = load();
  screen._selPreset = 'cashier';
  Object.assign(fields, { 'emp-inv-email': 'c@x.test', 'emp-inv-name': '   ' });
  await screen._submitInvite(false);
  const post = calls.find((c) => c[0] === 'POST');
  assert.ok(!('name' in post[2]), 'a blank name was sent');
});

test('invite: 61 characters never leave the page', async () => {
  const { screen, calls, toasts, fields } = load();
  screen._selPreset = 'cashier';
  Object.assign(fields, { 'emp-inv-email': 'c@x.test', 'emp-inv-name': 'x'.repeat(61) });
  await screen._submitInvite(false);
  assert.strictEqual(calls.filter((c) => c[0] === 'POST').length, 0);
  assert.strictEqual(toasts.length, 1);
});

test('invite: 60 characters are allowed', async () => {
  const { screen, calls, fields } = load();
  screen._selPreset = 'cashier';
  Object.assign(fields, { 'emp-inv-email': 'c@x.test', 'emp-inv-name': 'x'.repeat(60) });
  await screen._submitInvite(false);
  assert.strictEqual(calls.filter((c) => c[0] === 'POST').length, 1);
});

test('a delegated branch manager sends only {email, role} and gets no cards', async () => {
  const { screen, calls, fields } = load();
  screen._reduced = true;
  Object.assign(fields, { 'emp-inv-email': 'c@x.test', 'emp-inv-name': '' });
  await screen._submitInvite(true);
  const post = calls.find((c) => c[0] === 'POST');
  assert.deepStrictEqual(plain(Object.keys(post[2]).sort()), ['email', 'role']);
});

// ---------------------------------------------------------------- the gate
test('gate: an old device locks preset people, names it, and leaves legacy rows alone', async () => {
  const { screen } = load({ roleEditing: { allowed: false, min_registry_schema_version: 9, blocking_devices: [{ device_label: 'Old phone' }] } });
  await screen._refreshGate();
  assert.strictEqual(screen._gateBlocks({ role_preset: 'cashier' }), true);
  assert.strictEqual(screen._gateBlocks({ role_preset: null }), false);
  assert.ok(screen._gateReason().includes('Old phone'));
});

// V2. The gate used to START open ("allowed") and close only if the answer said
// so, so on a slow answer the owner saw four preset cards that a still-old
// device could not honour, and an unreadable answer left them open for good.
// It now starts CLOSED ("checking"), opens only on an explicit allowed:true,
// and stays closed (state "failed", plain Cashier/Manager picker) if the
// answer never comes or cannot be read.
test('gate: before any answer, presets are OFF and the reason says it is checking', () => {
  const { screen } = load();
  assert.strictEqual(screen._gate.state, 'checking');
  assert.strictEqual(screen._gate.allowed, false);
  assert.strictEqual(screen._gateBlocks({ role_preset: 'cashier' }), true, 'a preset person is locked while checking');
  assert.strictEqual(screen._gateBlocks({ role_preset: null }), false, 'a legacy row is never locked');
  assert.ok(screen._gateReason().startsWith('Checking'), screen._gateReason());
  const html = screen._inviteRoleFieldHtml(false);
  assert.ok(!html.includes('emp-pcard'), 'preset cards drawn before the gate answered');
  assert.ok(html.includes('id="emp-inv-role"'), 'the plain role picker must still be there');
});

test('gate: an explicit allowed:true opens the cards', async () => {
  const { screen } = load();
  await screen._refreshGate();
  assert.strictEqual(screen._gate.state, 'ok');
  assert.strictEqual(screen._gate.allowed, true);
  const html = screen._inviteRoleFieldHtml(false);
  assert.ok(html.includes('emp-pcard'), 'no cards after an allowed answer');
  assert.ok(!html.includes('id="emp-inv-role"'), 'the plain picker is still drawn beside the cards');
});

test('gate: an unreadable answer FAILS CLOSED and says so', async () => {
  const { screen } = load();
  screen._get = async () => { throw new Error('offline'); };
  const realError = console.error;
  console.error = () => {};             // the screen logs the failure on purpose
  try { await screen._refreshGate(); } finally { console.error = realError; }
  assert.strictEqual(screen._gate.state, 'failed');
  assert.strictEqual(screen._gate.allowed, false);
  assert.ok(screen._gateReason().startsWith('Could not check'), screen._gateReason());
  assert.ok(!screen._inviteRoleFieldHtml(false).includes('emp-pcard'), 'cards drawn after a failed check');
});

test('gate: an answer with no role_editing block is not an "allowed" answer', async () => {
  const { screen } = load();
  screen._get = async () => ({ success: true, devices: [] });
  await screen._refreshGate();
  assert.strictEqual(screen._gate.allowed, false);
  assert.strictEqual(screen._gate.state, 'failed');
});

// The dialog can be opened BEFORE the answer lands. The answer must redraw its
// Role field, keep a Manager pick, and never touch what was typed elsewhere.
test('gate: an open invite dialog is redrawn with cards when the answer is allowed', async () => {
  const { screen, byId } = load();
  const holder = stub();
  holder.querySelector = () => null;                 // no cards drawn yet
  byId['emp-inv-rolefield'] = holder;
  byId['emp-inv-role'] = Object.assign(stub(), { value: 'manager' });
  byId['emp-invite-modal-dialog'] = stub();
  await screen._refreshGate();
  assert.ok(holder.innerHTML.includes('emp-pcard'), 'the open dialog never got the cards');
  assert.strictEqual(screen._selPreset, 'manager', 'a Manager pick on the plain picker was lost');
  assert.strictEqual(byId['emp-invite-modal-dialog'].style.width, '620px');
});

test('gate: an open invite dialog stays a plain picker when the answer is blocked', async () => {
  const { screen, byId } = load({ roleEditing: { allowed: false, blocking_devices: [{ device_label: 'Old phone' }] } });
  const holder = stub();
  holder.querySelector = () => null;
  byId['emp-inv-rolefield'] = holder;
  byId['emp-inv-role'] = Object.assign(stub(), { value: 'cashier' });
  await screen._refreshGate();
  assert.ok(!holder.innerHTML.includes('emp-pcard'), 'cards drawn for a blocked shop');
  assert.ok(holder.innerHTML.includes('Old phone'), 'the blocking device is not named');
});

// ---------------------------------------------------------------- runner
(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log('  ok   ' + name); } catch (e) { failed++; console.log('  FAIL ' + name + '\n       ' + (e && e.message)); }
  }
  console.log(failed ? `\n${failed} of ${tests.length} FAILED` : `\n${tests.length} of ${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
