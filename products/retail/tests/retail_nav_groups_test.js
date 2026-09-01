/**
 * Retail — sidebar nav grouping (launch-readiness 2026-08-29).
 *
 * The product owner's complaint, verbatim: "The left panel needs
 * reorganizing -- it's so much stuff on the left you get distracted." The
 * sidebar's `nav` array in app-shell.js was 15 flat entries with no
 * hierarchy, so an owner saw all fifteen at once. This file pins the
 * grouped replacement:
 *
 *     (ungrouped)  Dashboard
 *     Sell         Point of Sale, Returns, Barcode Scanner, Customers, Promotions
 *     Stock        Products, Categories, Suppliers, Purchase Orders
 *     Insight      Reports, Stock Accuracy, Exceptions, Audit Log
 *     Admin        Employees, Branches, Settings
 *
 * UPDATED 2026-08-30 (ROADMAP.md "retail schema v23", promotions wave 1):
 * Promotions is a 16th destination, appended to Sell -- it is a per-product/
 * per-category discount rule the till resolves automatically, the same
 * authority as typing a manual discount at checkout (capability
 * retail.discount, same code a manager/admin already holds). ALL_FIFTEEN
 * below is renamed ALL_DESTINATIONS rather than bumped to a new hardcoded
 * name, so the NEXT nav addition does not have to rename it again.
 *
 * UPDATED (ci-hardening-w0.3 continuation, "the doorway"): Branches is a
 * 17th destination, appended to Admin. create_branch (retail_api.py) has
 * been a complete, gated POST /branches route since Phase 5 wave A with
 * nothing in the frontend ever calling it -- every install was stuck
 * self-healing exactly one branch, forever. `capability: 'retail.employees'`
 * matches that route's own @mt_require_capability(CAP_EMPLOYEES) decorator,
 * the same reasoning Employees (this group's other member) already uses for
 * its own gate.
 *
 * UPDATED (ci-hardening-w0.3 continuation, "the doorway", second one on this
 * branch): Email Notifications is an 18th destination, appended to Admin.
 * GET/POST /api/notifications/{status,settings,outbox,outbox/run-once}
 * (commercial_runtime/notifications/routes.py) have been complete with
 * nothing in the frontend ever calling them -- WhatsApp got a settings page;
 * email never did. `ownerOnly: true`, NOT `adminOnly`, because every
 * mutating route there reads `session['mt_role'] == 'admin'` (the USER
 * axis) via `_require_admin`, not `this.isAdminDevice` (the DEVICE axis
 * `adminOnly` actually means) -- the same reasoning Employees already uses
 * for its own gate.
 *
 * WHAT THIS FILE DOES NOT RE-TEST
 * The per-item visibility rule itself (capability / adminOnly / ownerOnly /
 * desktopOnly, and the fail-open/fail-closed contract around it) is already
 * covered exhaustively by retail_reports_capability_gate_test.js and
 * retail_attribution_i18n_test.py. Grouping must not change WHO sees WHAT --
 * only how the sidebar arranges it -- so this file asserts the ARRANGEMENT:
 * every destination still reachable, the groups in the specified order with
 * the specified members, and the one new behaviour grouping introduces: a
 * group with nothing visible in it renders no header at all.
 *
 * Standalone Node, no framework — this frontend has no build step (CLAUDE.md):
 *
 *   node products/retail/tests/retail_nav_groups_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const SHELL_FILE = path.join(FRONTEND_DIR, 'app-shell.js');

// The 15 destinations the flat nav used to list, and the exact grouping this
// change introduces. Written down once, here, so every test below checks
// against the SAME expectation rather than five ad-hoc lists that could drift
// from each other.
const GROUPS = [
  { label: 'Sell', items: ['pos', 'returns', 'scanner', 'customers', 'promotions'] },
  { label: 'Stock', items: ['products', 'categories', 'suppliers', 'purchases'] },
  { label: 'Insight', items: ['reports', 'stock-accuracy', 'exceptions', 'audit-log'] },
  { label: 'Admin', items: ['employees', 'branches', 'admin-center', 'email-notifications'] },
];
const ALL_DESTINATIONS = ['dashboard', ...GROUPS.flatMap((g) => g.items)];

// The real grant sets a role actually resolves to, copied from
// commercial_runtime/identity/user_accounts.py's ROLE_CAPABILITIES (same
// fixture retail_reports_capability_gate_test.js uses) rather than invented,
// so this fixture cannot silently drift from what the server actually grants.
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

// Same loader shape as retail_reports_capability_gate_test.js: just enough
// stubbing for app-shell.js's module body to finish evaluating so
// window.SubsystemApp exists. `t` is the identity function, matching the
// real window.t global shorthand (i18n.js) in English mode -- this file does
// not exercise translation, only arrangement.
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

function renderNav({ capabilities, role, isAdminDevice }) {
  const shell = loadShell();
  const App = shell.SubsystemApp;
  App.capabilities = capabilities;
  App.role = role || 'cashier';
  App.isAdminDevice = !!isAdminDevice;
  App.activeModules = [];
  App.active = 'retail';

  const shellEl = makeElementStub();
  shell.document.getElementById = (id) => (id === 'subsystem-shell' ? shellEl : makeElementStub());
  App._renderShell(App.systems.retail, 'retail');
  return shellEl.innerHTML;
}

// The rendered nav, reduced to an ORDERED token stream: 'ITEM:<id>' for every
// nav link (from its data-section attribute) and 'GROUP:<label>' for every
// group header, in document order. A single alternation (not two separate
// regexes run independently) is what makes the order comparable at all --
// two independent .match() passes would each be internally ordered but say
// nothing about how the two interleave.
function orderedTokens(html) {
  const re = /<div class="sub-nav-group-label">([^<]*)<\/div>|data-section="([^"]+)"/g;
  const tokens = [];
  let m;
  while ((m = re.exec(html)) !== null) {
    tokens.push(m[1] !== undefined ? `GROUP:${m[1]}` : `ITEM:${m[2]}`);
  }
  return tokens;
}

function ownerEverythingVisibleHTML() {
  // capabilities: null -> hasCapability() fails OPEN (see hasCapability's own
  // documented default), so this owner sees every capability-gated entry
  // without this file having to enumerate all eight grant codes.
  return renderNav({ capabilities: null, role: 'admin', isAdminDevice: true });
}

// ═════════════════════════════════════════════════════════════════════════════
// 1 — every one of the 18 destinations is still reachable for an owner
// ═════════════════════════════════════════════════════════════════════════════

function testAllDestinationsReachableForOwner() {
  const html = ownerEverythingVisibleHTML();
  const missing = ALL_DESTINATIONS.filter((id) => !new RegExp(`_navigate\\('${id}'\\)`).test(html));
  assert.deepStrictEqual(
    missing, [],
    `${missing.length} destination(s) unreachable for an owner after grouping: ${missing.join(', ')}. ` +
    'Grouping must not lose one -- that is the obvious way this change breaks. ' +
    'Every id in systems.retail.navGroups must resolve to a real nav entry, and ' +
    'every non-dashboard nav entry must be listed in exactly one group.'
  );
  assert.strictEqual(missing.length === 0 && ALL_DESTINATIONS.length, 18, 'sanity: this file\'s own expectation list drifted from 18 destinations');
}

// ═════════════════════════════════════════════════════════════════════════════
// 2 — the groups render in the specified order with their specified members
// ═════════════════════════════════════════════════════════════════════════════

function testGroupsRenderInSpecifiedOrderWithMembers() {
  const html = ownerEverythingVisibleHTML();
  const tokens = orderedTokens(html);

  const expected = ['ITEM:dashboard'];
  for (const g of GROUPS) {
    expected.push(`GROUP:${g.label}`);
    for (const id of g.items) expected.push(`ITEM:${id}`);
  }

  assert.deepStrictEqual(
    tokens, expected,
    'The rendered nav does not match the specified order/membership.\n' +
    '  got:      ' + JSON.stringify(tokens) + '\n' +
    '  expected: ' + JSON.stringify(expected)
  );
}

// ═════════════════════════════════════════════════════════════════════════════
// 3 — a cashier sees NO Insight and NO Admin header at all (the empty-group rule)
// ═════════════════════════════════════════════════════════════════════════════

function testCashierSeesNoInsightNoAdminHeader() {
  // isAdminDevice: false is the ordinary case for a till a cashier is
  // actually standing at -- with it true, admin-center (adminOnly) would
  // stay visible and Admin would not be the empty group this case is about.
  const html = renderNav({ capabilities: CASHIER_CAPS, role: 'cashier', isAdminDevice: false });
  const tokens = orderedTokens(html);

  assert.ok(
    !tokens.includes('GROUP:Insight'),
    'The Insight header rendered for a cashier who holds none of its four ' +
    'members\' required capability (retail.reports). An empty group must ' +
    'render no header, not a header over nothing. Tokens: ' + JSON.stringify(tokens)
  );
  assert.ok(
    !tokens.includes('GROUP:Admin'),
    'The Admin header rendered for a cashier who is neither the owner ' +
    '(employees, ownerOnly) nor on the admin device (admin-center, adminOnly). ' +
    'Tokens: ' + JSON.stringify(tokens)
  );
  // Sanity: this cashier must still see the groups that DO apply to them, or
  // this test would pass on a nav that renders no groups at all.
  assert.ok(
    tokens.includes('GROUP:Sell') && tokens.includes('GROUP:Stock'),
    'Sanity: Sell and Stock must still render for a cashier. Tokens: ' + JSON.stringify(tokens)
  );
  assert.ok(/_navigate\('pos'\)/.test(html), 'Sanity: the ungated Point of Sale entry must still be present for a cashier.');
}

// ═════════════════════════════════════════════════════════════════════════════
// 4 — lacking exactly one item's gate still shows that item's GROUP, because
//     its other members remain: "hide when empty", not "hide when incomplete"
// ═════════════════════════════════════════════════════════════════════════════

function testGroupSurvivesWhenExactlyOneMemberIsGatedOut() {
  // capabilities: ['retail.reports'] satisfies the capability half of every
  // Insight member. role: 'manager' (not 'admin') fails ONLY
  // stock-accuracy's ownerOnly requirement -- reports and exceptions carry no
  // role/device gate at all, and audit-log's adminOnly is satisfied by
  // isAdminDevice: true. So exactly one of Insight's four members
  // (stock-accuracy) is hidden, and the other three remain.
  const html = renderNav({ capabilities: ['retail.reports'], role: 'manager', isAdminDevice: true });
  const tokens = orderedTokens(html);

  assert.ok(
    tokens.includes('GROUP:Insight'),
    'The Insight header disappeared when only ONE of its four members ' +
    '(Stock Accuracy, gated ownerOnly) was hidden. The rule must be "hide ' +
    'when EMPTY", not "hide when incomplete" -- three siblings (Reports, ' +
    'Exceptions, Audit Log) are still visible to this viewer. Tokens: ' + JSON.stringify(tokens)
  );
  assert.ok(!/_navigate\('stock-accuracy'\)/.test(html), 'Sanity: stock-accuracy must actually be the hidden one in this fixture.');
  assert.ok(
    /_navigate\('reports'\)/.test(html) && /_navigate\('exceptions'\)/.test(html) && /_navigate\('audit-log'\)/.test(html),
    'Sanity: Insight\'s other three members must still be present, or this test would pass on a group hidden for an unrelated reason.'
  );
}

// ═════════════════════════════════════════════════════════════════════════════
// 5 — Dashboard renders outside any group
// ═════════════════════════════════════════════════════════════════════════════

function testDashboardRendersOutsideAnyGroup() {
  const html = ownerEverythingVisibleHTML();
  const tokens = orderedTokens(html);

  assert.strictEqual(tokens[0], 'ITEM:dashboard', 'Dashboard must be the first thing rendered in the sidebar.');
  assert.ok(
    !GROUPS.some((g) => g.items.includes('dashboard')),
    'Sanity: this file\'s own GROUPS fixture must not list dashboard as a member of any group.'
  );
  // Not merely first: nothing in the config's own group lists names it, so
  // there is no GROUP header anywhere before it either.
  assert.ok(
    tokens.indexOf('GROUP:Sell') > tokens.indexOf('ITEM:dashboard'),
    'Dashboard must render before the first group header, i.e. outside of any group.'
  );
}

// Every case is run even after one fails, and each failure is printed with
// its own name -- see retail_reports_capability_gate_test.js for why a flat
// sequence that aborts on the first throw is the wrong shape here.
const CASES = [
  testAllDestinationsReachableForOwner,
  testGroupsRenderInSpecifiedOrderWithMembers,
  testCashierSeesNoInsightNoAdminHeader,
  testGroupSurvivesWhenExactlyOneMemberIsGatedOut,
  testDashboardRendersOutsideAnyGroup,
];

function main() {
  let failed = 0;
  for (const fn of CASES) {
    try {
      fn();
      console.log('  ok   ' + fn.name);
    } catch (err) {
      failed += 1;
      console.error('  FAIL ' + fn.name);
      console.error('       ' + (err && err.message ? err.message : err));
    }
  }
  if (failed) {
    console.error(`FAIL: retail_nav_groups_test.js — ${failed} of ${CASES.length} case(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`PASS: retail_nav_groups_test.js — ${CASES.length} case(s)`);
  }
}

main();
