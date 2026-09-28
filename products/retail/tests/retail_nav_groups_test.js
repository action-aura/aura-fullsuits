/**
 * retail_nav_groups_test.js — the sidebar's grouped nav, and how P1a
 * regrouped it into a COLLAPSIBLE sidebar that fits 1366x768.
 *
 * REWRITTEN FOR P1a ("a collapsible sidebar that fits 1366x768", contract
 * 2026-09-28). The GROUPS fixture, the destination count, the cashier case
 * and the one-member-gated case are all rewritten ON PURPOSE -- the plan
 * names this file explicitly. What follows states what changed and, for
 * each rewritten case, what it can no longer catch.
 *
 * THE OLD SHAPE (four groups: Sell, Stock, Insight, Admin) is gone. The new
 * shape is the owner's own 768px height budget (see
 * systems.retail.navGroups' own comment in app-shell.js for the
 * arithmetic): Sell, Grow, Stock, Money, Books, Reports, Connected services
 * -- seven headers, none anywhere near the old 11-item Insight worst case.
 * 'admin-center' (the Settings gear) is no longer IN any group at all: it
 * renders as the sidebar's own FOOTER button now (next to the AI button),
 * the same way 'dashboard' renders above every group rather than inside
 * one.
 *
 * UPDATED (fix round, H1): 'employees', 'branches' and 'backup-export' are
 * still UNGROUPED -- not navGroups members, so still not one click from the
 * flat sidebar list -- but they are NOT unreachable any more. The footer
 * gear is now a small MENU (the same pattern as the identity chip's own
 * menu), and it lists all five settings-shaped screens: Settings itself,
 * Employees, Branches, Backup & Export and License & devices, each gated
 * exactly like its own nav entry. That is TWO clicks (gear, then the row),
 * not one, which is why they stay out of navGroups' flat one-click list --
 * but "reachable in two clicks" is a real path, and the previous phase's
 * "no path at all this phase" claim was wrong the moment this landed.
 * testFiveSettingsScreensReachableInTwoClicksForOwner below is the guard.
 *
 * 'whatsapp'/'einvoicing'/'site-relay' are new destinations (P1a item 5):
 * real standalone pages, not `RetailSystem.render()` cases, so their click
 * action is `location.href='/static/<page>.html'`, not `_navigate(id)` --
 * see testAllDestinationsReachableForOwner's REACHABLE regex, which accepts
 * either shape.
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

// The P1a grouping, written down once so every test below checks against
// the SAME expectation. `key` is given only where it differs from the
// label (the connected-services group uses a stable non-translated key,
// since `label` alone is what gets translated for display).
const GROUPS = [
  { label: 'Sell',    items: ['pos', 'returns', 'scanner', 'customers', 'quotations'] },
  { label: 'Grow',    items: ['promotions'] },
  { label: 'Stock',   items: ['products', 'categories', 'suppliers', 'purchases', 'transfers'] },
  { label: 'Money',   items: ['receivables', 'payables', 'cheques'] },
  { label: 'Books',   items: ['trial-balance', 'profit-and-loss', 'balance-sheet', 'cash-flow'] },
  { label: 'Reports', items: ['reports', 'stock-accuracy', 'exceptions', 'audit-log'] },
  { label: 'Connected services', key: 'connected', items: ['whatsapp', 'einvoicing', 'email-notifications', 'site-relay'] },
];
// Ungrouped BY DESIGN, i.e. NOT a bug this file should catch: every id here
// has a real, deliberate render path OUTSIDE #sub-nav's group list.
// 'dashboard' (the home screen, rendered above every group) and
// 'admin-center' (the footer gear button itself) -- see
// testAdminCenterRendersAsFooterGearNotAGroupMember. 'employees' /
// 'branches' / 'backup-export' (fix round, H1) -- reachable through the
// SAME footer gear's own menu, two clicks instead of one -- see
// testFiveSettingsScreensReachableInTwoClicksForOwner. None of the five are
// navGroups members, and none of them should be: the flat one-click list is
// for the shopkeeper's daily screens, not the settings cluster.
const UNGROUPED_BY_DESIGN = new Set(['dashboard', 'admin-center', 'employees', 'branches', 'backup-export']);
// Every id that must show up as `_navigate('<id>')` (or the external-page
// click shape, for whatsapp/einvoicing/site-relay) somewhere in the shell:
// every UNGROUPED_BY_DESIGN id PLUS every grouped id.
const ALL_DESTINATIONS = ['dashboard', 'admin-center', ...GROUPS.flatMap((g) => g.items)];
// 28 = dashboard + admin-center (2 ungrouped, footer-rendered) + 26 grouped.
// 'employees'/'branches'/'backup-export' are DELIBERATELY NOT in this list
// -- they are reachable (see UNGROUPED_BY_DESIGN's own comment above), but
// through the gear's own MENU, not through anything `_navigate`-shaped this
// array's reachability check below can see; testFiveSettingsScreensReachable
// InTwoClicksForOwner proves them separately, on their own real click path.
// The count and the message are bumped TOGETHER on purpose -- see
// testAllDestinationsReachableForOwner's own sanity assertion.

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
// Mutation harness (same shape as retail_shell_chrome_test.js's mutate()):
// re-anchor to exact source text, require the anchor to occur exactly once
// so a stale mutation cannot silently no-op. app-shell.js is CRLF
// throughout (measured: 5394 "\r\n", 0 bare "\n") -- an anchor written with
// a plain "\n" (any multi-line find/replace) never matches on its own, so
// both strings are normalised to the FILE's own line ending first, the
// same fix retail_shell_chrome_test.js's mutate()/nlFor() already applies.
function mutateSource(src, find, replace) {
  const eol = src.indexOf('\r\n') !== -1 ? '\r\n' : '\n';
  const nl = (s) => s.replace(/\n/g, eol);
  const f = nl(find), r = nl(replace);
  const hits = src.split(f).length - 1;
  assert.strictEqual(hits, 1,
    `Mutation anchor occurs ${hits} time(s), expected exactly 1:\n  ${JSON.stringify(f)}\n\n` +
    'A mutation that no longer applies would let the proof below pass while proving nothing. Re-anchor it.');
  return src.split(f).join(r);
}

function renderNavFromSource(src, { capabilities, role, isAdminDevice }) {
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
      documentElement: { getAttribute() { return null; }, setAttribute() {}, style: { setProperty() {} } },
      getElementById() { return null; },
      createElement() { return makeElementStub(); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      addEventListener() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: SHELL_FILE + ' (mutated)' });
  const App = sandbox.SubsystemApp;
  App.capabilities = capabilities;
  App.role = role || 'cashier';
  App.isAdminDevice = !!isAdminDevice;
  App.activeModules = [];
  App.active = 'retail';
  App.currentSection = 'dashboard';
  App.currentUser = { id: 'u1', email: 'test@example.com' };
  const shellEl = makeElementStub();
  sandbox.document.getElementById = (id) => (id === 'subsystem-shell' ? shellEl : makeElementStub());
  App._renderShell(App.systems.retail, 'retail');
  return shellEl.innerHTML;
}

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
  App.currentSection = 'dashboard';
  App.currentUser = { id: 'u1', email: 'test@example.com' };

  const shellEl = makeElementStub();
  shell.document.getElementById = (id) => (id === 'subsystem-shell' ? shellEl : makeElementStub());
  App._renderShell(App.systems.retail, 'retail');
  return shellEl.innerHTML;
}

// Extracts ONLY the sidebar's <nav id="sub-nav">...</nav> region, so a
// token scan cannot be confused by the footer's own Settings-gear anchor
// (data-section="admin-center", OUTSIDE #sub-nav by design -- see
// testAdminCenterRendersAsFooterGearNotAGroupMember) or by anything in the
// phone tab bar/More sheet this same render call also emits.
function subNavRegion(html) {
  const start = html.indexOf('id="sub-nav"');
  assert.ok(start >= 0, 'no #sub-nav element in the rendered shell -- the fixture cannot see what it renders.');
  const navOpenEnd = html.indexOf('>', start) + 1;
  const closeIdx = html.indexOf('</nav>', navOpenEnd);
  assert.ok(closeIdx >= 0, 'no closing </nav> found after #sub-nav.');
  return html.slice(navOpenEnd, closeIdx);
}

function ownerEverythingVisibleHTML() {
  // capabilities: null -> hasCapability() fails OPEN (see hasCapability's own
  // documented default), so this owner sees every capability-gated entry
  // without this file having to enumerate all eight grant codes.
  return renderNav({ capabilities: null, role: 'admin', isAdminDevice: true });
}

// The rendered #sub-nav region, reduced to an ORDERED token stream:
// 'ITEM:<id>' for every nav link (from its data-section attribute) and
// 'GROUP:<label>' for every group header (P1a: the header text now lives in
// its own <span class="sub-nav-group-label-text">, not the header element's
// own full text, because the header is a real <button> that also carries a
// status dot and a chevron -- see app-shell.js's groupsHTML). A single
// alternation (not two separate regexes run independently) is what makes
// the order comparable at all.
function orderedTokens(html) {
  const region = subNavRegion(html);
  const re = /<span class="sub-nav-group-label-text">([^<]*)<\/span>|data-section="([^"]+)"/g;
  const tokens = [];
  let m;
  while ((m = re.exec(region)) !== null) {
    tokens.push(m[1] !== undefined ? `GROUP:${m[1]}` : `ITEM:${m[2]}`);
  }
  return tokens;
}

// ═════════════════════════════════════════════════════════════════════════════
// 1 — every GROUPED nav entry is reachable; the ungrouped-by-design set is
//     named exactly, so a future nav entry that forgets a group fails loudly
//
// WHY THIS EXISTS, and why a hand-kept expectation list did not catch it.
//
// Stock Transfers (the bug that first justified this file's shape) shipped
// with a complete nav entry, a render function, six gated backend routes
// and its own test suites -- and NO WAY FOR A USER TO OPEN IT. The entry
// went into `nav`; the sidebar draws from `navGroups`; an id in the first
// and not the second is simply never painted.
//
// Every OTHER guard in the repo passed. retail_route_reachability_test.py
// asks whether a shipped CLIENT calls each route, and subsystem-retail.js
// does call it, so it was satisfied. A version of this test with a
// hand-written expectation list asks whether each destination IT ALREADY
// KNOWS ABOUT renders -- and that list is a hand-written copy of navGroups,
// so an entry present in neither is invisible to it. A hand-kept
// expectation cannot notice something nobody told it about.
//
// So this one derives BOTH sides -- the flat `nav` array AND navGroups'
// membership -- from app-shell.js's own SOURCE at run time. It cannot fall
// behind the code, because it has no list of its own to forget to update;
// the only hand-kept list left is UNGROUPED_BY_DESIGN, and that one is
// deliberately narrow and independently tested (section 2 and the fix
// round's own reachability test below).
// ═════════════════════════════════════════════════════════════════════════════

function testEveryNavEntryIsInAGroup() {
  const src = fs.readFileSync(SHELL_FILE, 'utf8');

  // Ids declared in the flat `nav` array: `{ id: 'x', label: ... }`.
  const navIds = [];
  const navRe = /\{\s*id:\s*'([a-z0-9-]+)'\s*,\s*label:/g;
  let m;
  while ((m = navRe.exec(src))) navIds.push(m[1]);

  // Ids listed inside navGroups' `items:` arrays. Window widened (was 2000
  // chars): the new comment block ABOVE navGroups explaining the P1a
  // regroup is long enough that a 2000-char window from the FIRST
  // `navGroups:` match would have clipped into the array itself. Both
  // floors below stay in force so a truncated window still fails loudly
  // rather than silently scoring an empty match set as "0 orphans".
  const groupsBlock = src.slice(src.indexOf('navGroups:'), src.indexOf('navGroups:') + 4000);
  const groupedIds = new Set();
  const itemsRe = /items:\s*\[([^\]]*)\]/g;
  while ((m = itemsRe.exec(groupsBlock))) {
    for (const raw of m[1].split(',')) {
      const id = raw.trim().replace(/^'|'$/g, '');
      if (id) groupedIds.add(id);
    }
  }

  // ANTI-VACUITY: if either scrape came back thin, every assertion below would
  // pass over nothing. That is precisely how the guard this replaces failed.
  assert.ok(navIds.length >= 15,
    `only ${navIds.length} nav ids scraped from app-shell.js -- this check has nothing to inspect`);
  assert.ok(groupedIds.size >= 15,
    `only ${groupedIds.size} grouped ids scraped from navGroups -- this check has nothing to inspect`);

  const orphans = navIds.filter((id) => !groupedIds.has(id) && !UNGROUPED_BY_DESIGN.has(id));
  assert.deepStrictEqual(orphans, [],
    'nav entries that belong to no navGroup AND are not in the named UNGROUPED_BY_DESIGN set are ' +
    'UNREACHABLE by accident, not on purpose: ' + orphans.join(', '));
  console.log(`PASS: all ${navIds.length} nav entries are grouped, footer-rendered, or named as an intentional P1a gap (${groupedIds.size} grouped)`);
}

// ═════════════════════════════════════════════════════════════════════════════
// 2 — the gear ('admin-center') renders in the FOOTER, not as a group member
// ═════════════════════════════════════════════════════════════════════════════

function testAdminCenterRendersAsFooterGearNotAGroupMember() {
  const html = ownerEverythingVisibleHTML();
  assert.ok(
    !subNavRegion(html).includes('data-section="admin-center"'),
    "'admin-center' still renders INSIDE #sub-nav -- P1a moved it to the sidebar's own footer " +
    "button (next to the AI button), same treatment as 'dashboard'. If this fires, either the " +
    "gear is still a navGroups member (it must not be) or the footer gear was deleted."
  );
  // ANCHOR UPDATED (fix round, H1): the gear button itself no longer
  // carries data-section="admin-center" -- it is a MENU TOGGLE now (see
  // that item's own comment in app-shell.js), and the attribute moved onto
  // the 'Settings' row INSIDE the gear's menu (testFiveSettingsScreens
  // ReachableInTwoClicksForOwner, below, is the dedicated proof for that
  // whole menu). Same underlying question -- is admin-center reachable
  // through the gear, anywhere -- just no longer both on the SAME element.
  assert.ok(
    /sub-settings-gear/.test(html) && /data-section="admin-center"/.test(html),
    "no '.sub-settings-gear' footer button, or no data-section=\"admin-center\" anywhere in the " +
    "rendered shell -- Settings has no way in at all for an owner."
  );
  console.log('PASS: admin-center renders as the sidebar footer gear, not a group member');
}

// ═════════════════════════════════════════════════════════════════════════════
// 3 — every destination that HAS a render path is reachable for an owner
// ═════════════════════════════════════════════════════════════════════════════

function testAllDestinationsReachableForOwner() {
  const html = ownerEverythingVisibleHTML();
  // whatsapp/einvoicing/site-relay (P1a item 5) are real standalone pages:
  // their click action is `location.href='/static/<page>.html'`, not
  // `_navigate(id)`. Either shape counts as "reachable" here.
  const EXTERNAL_PAGE = { whatsapp: 'whatsapp.html', einvoicing: 'einvoicing.html', 'site-relay': 'site-relay.html' };
  const isReachable = (id) => {
    if (EXTERNAL_PAGE[id]) return html.includes(`location.href='/static/${EXTERNAL_PAGE[id]}'`);
    return new RegExp(`_navigate\\('${id}'\\)`).test(html);
  };
  const missing = ALL_DESTINATIONS.filter((id) => !isReachable(id));
  assert.deepStrictEqual(
    missing, [],
    `${missing.length} destination(s) unreachable for an owner after grouping: ${missing.join(', ')}. ` +
    'Every id in systems.retail.navGroups must resolve to a real nav entry with a working click ' +
    'action, and every non-dashboard, non-admin-center nav entry must be listed in exactly one ' +
    'group UNLESS it is named in UNGROUPED_BY_DESIGN (employees/branches/backup-export reach ' +
    'through the gear menu instead -- tested separately, below).'
  );
  // 28 = dashboard + admin-center (both ungrouped-by-design but reachable)
  // + 26 grouped entries. Bumped together with this message on purpose --
  // this assertion exists to catch ALL_DESTINATIONS drifting from the
  // product, so a count that disagrees with its own prose is the exact
  // failure it is supposed to make loud.
  assert.strictEqual(ALL_DESTINATIONS.length, 28, "sanity: this file's own expectation list drifted from 28 reachable destinations");
}

// ═════════════════════════════════════════════════════════════════════════════
// 4 — REVERTED (fix round, H1): the five settings-shaped screens are each
//     reachable for the owner in <=2 clicks (gear, then the row), and
//     absent for a cashier. This REPLACES
//     testTheThreeSettingsScreensHaveNoSidebarPathThisPhase, which asserted
//     the opposite (employees/branches/backup-export had NO path at all) --
//     that was correct for the phase it described, and is wrong now that
//     H1 gives the gear a real menu. Both directions proved: an owner sees
//     all five reachable, a cashier sees none of them (nor the gear at
//     all, already covered by testCashierOnTheAdminDeviceStillSeesNoGear,
//     restated here specifically for these five ids so this section stands
//     on its own).
// ═════════════════════════════════════════════════════════════════════════════

const GEAR_MENU_SCREENS = [
  ['admin-center', 'Settings'],
  ['employees', 'Employees'],
  ['branches', 'Branches'],
  ['backup-export', 'Backup & Export'],
];

function testFiveSettingsScreensReachableInTwoClicksForOwner() {
  const html = ownerEverythingVisibleHTML();
  assert.ok(/id="sub-settings-gear-btn"/.test(html), 'no gear button (click 1) found for an owner.');
  const menuMatch = /id="sub-settings-menu"[^>]*>([\s\S]*?)<\/div>\s*<\/div>/.exec(html);
  assert.ok(menuMatch, 'no #sub-settings-menu panel found for an owner -- the gear has nothing to open.');
  const menuHTML = menuMatch[1];
  for (const [id, label] of GEAR_MENU_SCREENS) {
    assert.ok(
      new RegExp(`_navigate\\('${id}'\\)`).test(menuHTML),
      `'${id}' (${label}) has no _navigate('${id}') row inside the gear menu for an owner -- ` +
      'H1 says every one of the five settings screens must be reachable in <=2 clicks (gear, ' +
      'then this row); this menu is click 2 and it is missing.'
    );
  }
  // License & devices has no nav entry (it opens the standalone licensing
  // page, not a `_navigate` section) -- its own real action, checked by name.
  assert.ok(
    /SubsystemApp\.openLicensing\(\)/.test(menuHTML),
    "the gear menu has no 'License & devices' row calling SubsystemApp.openLicensing() for an owner."
  );
  console.log('PASS: all five settings screens (Settings/Employees/Branches/Backup & Export/License) are reachable in <=2 clicks for an owner');
}

function testFiveSettingsScreensAbsentForCashier() {
  const html = renderNav({ capabilities: CASHIER_CAPS, role: 'cashier', isAdminDevice: true });
  assert.ok(!/id="sub-settings-gear-btn"/.test(html), 'a cashier can see the settings gear button at all.');
  assert.ok(!/id="sub-settings-menu"/.test(html), 'a cashier has a #sub-settings-menu panel rendered at all.');
  for (const [id] of GEAR_MENU_SCREENS) {
    if (id === 'admin-center') continue; // covered by testCashierOnTheAdminDeviceStillSeesNoGear already
    assert.ok(
      !new RegExp(`_navigate\\('${id}'\\)`).test(html),
      `'${id}' is reachable for a cashier -- the gear menu (or something else) leaked it.`
    );
  }
  assert.ok(!/SubsystemApp\.openLicensing\(\)/.test(html), "a cashier can reach 'License & devices' through the gear menu.");
  console.log('PASS: none of the five settings screens are reachable for a cashier, both directions proved with the owner case above');
}

// ═════════════════════════════════════════════════════════════════════════════
// 5 — the groups render in the specified order with their specified members
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
  console.log('PASS: all 7 P1a groups render in order with their specified members');
}

// ═════════════════════════════════════════════════════════════════════════════
// 6 — a cashier sees NO Money, Books, Reports, Grow or Connected services
//     header (the P1a exit criterion, both directions: absent for cashier,
//     present for owner)
// ═════════════════════════════════════════════════════════════════════════════

function testCashierSeesNoMoneyBooksReportsGrowConnectedHeader() {
  // isAdminDevice: false is the ordinary case for a till a cashier is
  // actually standing at.
  const html = renderNav({ capabilities: CASHIER_CAPS, role: 'cashier', isAdminDevice: false });
  const tokens = orderedTokens(html);

  for (const label of ['Money', 'Books', 'Reports', 'Grow', 'Connected services']) {
    assert.ok(
      !tokens.includes(`GROUP:${label}`),
      `The ${label} header rendered for a cashier holding only ${JSON.stringify(CASHIER_CAPS)}. ` +
      'An empty group must render no header, not a header over nothing. Tokens: ' + JSON.stringify(tokens)
    );
  }
  // Sanity: this cashier must still see the groups that DO apply to them
  // (Sell, Stock), and Categories/Suppliers/Purchase Orders (P1a item 3:
  // gated on retail.stock.adjust, which this cashier fixture lacks) must
  // specifically be absent from Stock while Products (ungated) remains --
  // or this test would pass on a nav that renders nothing, or on the wrong
  // reason for Stock surviving.
  assert.ok(
    tokens.includes('GROUP:Sell') && tokens.includes('GROUP:Stock'),
    'Sanity: Sell and Stock must still render for a cashier. Tokens: ' + JSON.stringify(tokens)
  );
  assert.ok(/_navigate\('pos'\)/.test(html), "Sanity: the ungated Point of Sale entry must still be present for a cashier.");
  assert.ok(/_navigate\('products'\)/.test(html), "Sanity: 'products' (no capability gate) must still be present for a cashier.");
  for (const gated of ['categories', 'suppliers', 'purchases']) {
    assert.ok(
      !new RegExp(`_navigate\\('${gated}'\\)`).test(html),
      `P1a item 3: '${gated}' must be hidden from a cashier (gated on retail.stock.adjust, which ` +
      'this cashier fixture does not hold) -- see the nav entry\'s own comment in app-shell.js.'
    );
  }

  // BOTH DIRECTIONS (ENGINEERING.md §1: "prove both directions"): an owner
  // must see every one of these five headers, or "absent for a cashier"
  // could be satisfied by a group that is simply broken for everyone.
  const ownerTokens = orderedTokens(ownerEverythingVisibleHTML());
  for (const label of ['Money', 'Books', 'Reports', 'Grow', 'Connected services']) {
    assert.ok(
      ownerTokens.includes(`GROUP:${label}`),
      `The ${label} header did NOT render for an owner (capabilities: null, fail-open). If this ` +
      'group can never render for anyone, "absent for a cashier" above is checking a group that ' +
      "is broken, not gated. Tokens: " + JSON.stringify(ownerTokens)
    );
  }
  for (const id of ['categories', 'suppliers', 'purchases']) {
    assert.ok(
      new RegExp(`_navigate\\('${id}'\\)`).test(ownerEverythingVisibleHTML()),
      `P1a item 3, other direction: an owner (capabilities: null, fail-open) must still see '${id}'.`
    );
  }
  console.log('PASS: Money/Books/Reports/Grow/Connected services are gone for a cashier and present for an owner, both directions proved');
}

// ═════════════════════════════════════════════════════════════════════════════
// 6b — P1a item 1: a cashier standing at the MAIN COMPUTER still sees no
//      gear. THE EXACT CASE THAT LEAKED under the old `adminOnly` gate
//      (this.isAdminDevice): a cashier working the admin till used to see
//      Settings and the Audit Log because the gate answered a DEVICE
//      question ("is this the shop's main computer?"), not a PERSON
//      question ("can this person manage staff?"). Every other cashier
//      case in this file runs isAdminDevice:false (the ordinary case for a
//      till a cashier is actually standing at); this one runs it TRUE
//      specifically to isolate the regression the old gate had.
// ═════════════════════════════════════════════════════════════════════════════

function testCashierOnTheAdminDeviceStillSeesNoGear() {
  const html = renderNav({ capabilities: CASHIER_CAPS, role: 'cashier', isAdminDevice: true });
  assert.ok(
    !/sub-settings-gear/.test(html) && !html.includes('data-section="admin-center"'),
    'A cashier on isAdminDevice:true (the shop\'s main computer) can see the Settings gear -- ' +
    'this is the exact leak P1a item 1 fixes: the OLD `adminOnly` gate answered "is this the main ' +
    'computer", which is true here, instead of "can this person manage staff", which is false for ' +
    'a cashier.'
  );
  // BOTH DIRECTIONS: an owner on the SAME admin device must still see it,
  // or this could be satisfied by a gear that never renders for anyone.
  const ownerOnAdminDeviceHTML = renderNav({ capabilities: null, role: 'admin', isAdminDevice: true });
  assert.ok(
    /sub-settings-gear/.test(ownerOnAdminDeviceHTML) && ownerOnAdminDeviceHTML.includes('data-section="admin-center"'),
    'Sanity: an owner on isAdminDevice:true must still see the Settings gear, or this test would ' +
    'pass because the gear is broken for everyone, not because it correctly reads the person.'
  );
  console.log('PASS: a cashier on the admin device sees no gear; an owner on the same device does, both proved');
}

// ═════════════════════════════════════════════════════════════════════════════
// 7 — the "hide when empty, not when incomplete" rule, moved to Stock
//     (P1a item 3 gave Stock its own gated/ungated split; Insight no longer
//     exists as a single group to demonstrate this on)
// ═════════════════════════════════════════════════════════════════════════════

function testStockGroupSurvivesWhenItsGatedMembersAreHidden() {
  // WHAT THIS CASE USED TO BE, stated rather than silently dropped: the old
  // Insight group's four differently-gated members (capability-only,
  // capability+ownerOnly, capability+adminOnly) let this file hide EXACTLY
  // ONE of four and show the rule surviving with three still visible.
  // Stock's four stock-adjust-gated siblings (categories/suppliers/
  // purchases/transfers, P1a item 3) all now share the IDENTICAL gate, so
  // there is no longer a combination that hides exactly one of them alone --
  // hiding the capability hides all four together. What this case proves
  // instead is the SAME rule (a group survives on ONE remaining visible
  // member, not zero) using the real split Stock actually has: 'products'
  // carries NO gate at all, so a viewer missing retail.stock.adjust loses
  // four of Stock's five members and the group must still render on
  // 'products' alone -- not vanish just because it is down to one.
  const html = renderNav({ capabilities: ['retail.sell'], role: 'cashier', isAdminDevice: false });
  const tokens = orderedTokens(html);

  assert.ok(
    tokens.includes('GROUP:Stock'),
    'The Stock header disappeared when FOUR of its five members (categories/suppliers/purchases/' +
    'transfers, all gated retail.stock.adjust) were hidden and only \'products\' (ungated) remained. ' +
    'The rule must be "hide when EMPTY", not "hide when down to one". Tokens: ' + JSON.stringify(tokens)
  );
  assert.ok(/_navigate\('products'\)/.test(html), "Sanity: 'products' must actually be the one member still visible.");
  for (const hidden of ['categories', 'suppliers', 'purchases', 'transfers']) {
    assert.ok(!new RegExp(`_navigate\\('${hidden}'\\)`).test(html),
      `Sanity: '${hidden}' must actually be hidden in this fixture, or this test would pass for the wrong reason.`);
  }
  console.log('PASS: Stock survives on its one ungated member (products) when its four gated siblings are all hidden');
}

// ═════════════════════════════════════════════════════════════════════════════
// 8 — Dashboard renders outside any group, before the first group header
// ═════════════════════════════════════════════════════════════════════════════

function testDashboardRendersOutsideAnyGroup() {
  const html = ownerEverythingVisibleHTML();
  const tokens = orderedTokens(html);

  assert.strictEqual(tokens[0], 'ITEM:dashboard', 'Dashboard must be the first thing rendered in the sidebar.');
  assert.ok(
    !GROUPS.some((g) => g.items.includes('dashboard')),
    "Sanity: this file's own GROUPS fixture must not list dashboard as a member of any group."
  );
  assert.ok(
    tokens.indexOf('GROUP:Sell') > tokens.indexOf('ITEM:dashboard'),
    'Dashboard must render before the first group header, i.e. outside of any group.'
  );
  console.log('PASS: Dashboard renders outside every group, first in the sidebar');
}

// ═════════════════════════════════════════════════════════════════════════════
// 8b — coordinator fix round #2: a sell-only cashier has NO 'Dashboard' nav
//      entry at all (it used to render unconditionally and open straight
//      onto crew T's real refusal sentence -- a dead end item 2's whole
//      home-routing story exists to avoid); a manager/owner still has it.
//      Unknown capabilities fail OPEN (dashboard stays visible), the same
//      direction _isSellOnlyCapabilities() itself already documents.
// ═════════════════════════════════════════════════════════════════════════════

function testDashboardHiddenForSellOnlyCashierPresentForOthers() {
  const cashierHtml = renderNav({ capabilities: CASHIER_CAPS, role: 'cashier', isAdminDevice: false });
  assert.ok(
    !/data-section="dashboard"/.test(cashierHtml),
    "a sell-only cashier still has a 'Dashboard' nav entry -- it must be hidden so they can never " +
    'click through to the real refusal sentence underneath it.'
  );

  const ownerHtml = ownerEverythingVisibleHTML();
  assert.ok(
    /data-section="dashboard"/.test(ownerHtml),
    'Sanity, other direction: an owner (capabilities: null, fail-open) must still see Dashboard.'
  );

  const managerHtml = renderNav({
    capabilities: ['retail.cash.close', 'retail.discount', 'retail.refund', 'retail.reports', 'retail.sell', 'retail.stock.adjust'],
    role: 'manager', isAdminDevice: false,
  });
  assert.ok(
    /data-section="dashboard"/.test(managerHtml),
    'Sanity: a manager (holds retail.reports, i.e. NOT sell-only) must still see Dashboard.'
  );

  const unknownCapsHtml = renderNav({ capabilities: null, role: 'cashier', isAdminDevice: false });
  assert.ok(
    /data-section="dashboard"/.test(unknownCapsHtml),
    "Unknown capabilities (null) must fail OPEN -- Dashboard must stay visible, not be hidden by " +
    'a fail-closed guess.'
  );
  console.log('PASS: Dashboard is hidden for a sell-only cashier, present for manager/owner, and fails open on unknown capabilities');
}

// ═════════════════════════════════════════════════════════════════════════════
// 9 — MUTATION PROOF: admin-center leaking back into a group is caught
// ═════════════════════════════════════════════════════════════════════════════

function testAdminCenterGroupLeakIsCaught() {
  const src = fs.readFileSync(SHELL_FILE, 'utf8');
  // Puts 'admin-center' back where it used to live: as a member of Reports
  // (an arbitrary real group), the exact regression "the gear leaks back
  // into a group" would look like.
  const mutated = mutateSource(
    src,
    "{ label: 'Reports', items: ['reports', 'stock-accuracy', 'exceptions', 'audit-log'] },",
    "{ label: 'Reports', items: ['reports', 'stock-accuracy', 'exceptions', 'audit-log', 'admin-center'] },"
  );
  let threw = null;
  try {
    const html = renderNavFromSource(mutated, { capabilities: null, role: 'admin', isAdminDevice: true });
    assert.ok(
      !subNavRegion(html).includes('data-section="admin-center"'),
      "'admin-center' rendered inside #sub-nav"
    );
  } catch (err) {
    threw = err;
  }
  assert.ok(threw,
    'MUTATION SURVIVED — testAdminCenterRendersAsFooterGearNotAGroupMember\'s check passed even ' +
    "with 'admin-center' pushed back into the Reports group's items array, so it is not actually " +
    'watching for that regression. Fix the check, not the mutation.');
  console.log(`PASS: the admin-center-must-not-be-a-group-member check is mutation-proved  [caught: ${String(threw.message).split('\n')[0].slice(0, 140)}]`);
}

// ═════════════════════════════════════════════════════════════════════════════
// 10 — MUTATION PROOF: a cashier seeing Money because its gate was dropped
// ═════════════════════════════════════════════════════════════════════════════

function testCashierMoneyLeakIsCaught() {
  const src = fs.readFileSync(SHELL_FILE, 'utf8');
  // Strips the capability gate off 'receivables' -- the exact regression
  // "a P1a group's gate quietly falls off one of its members" would look
  // like: that one entry (and therefore the whole Money header) would leak
  // to a cashier who holds no retail.reports grant.
  const mutated = mutateSource(
    src,
    "{ id: 'receivables', label: 'Receivables', icon: '💰', capability: 'retail.reports' },",
    "{ id: 'receivables', label: 'Receivables', icon: '💰' },"
  );
  let threw = null;
  try {
    const html = renderNavFromSource(mutated, { capabilities: CASHIER_CAPS, role: 'cashier', isAdminDevice: false });
    const tokens = orderedTokens(html);
    assert.ok(!tokens.includes('GROUP:Money'), 'The Money header rendered for a cashier');
  } catch (err) {
    threw = err;
  }
  assert.ok(threw,
    'MUTATION SURVIVED — testCashierSeesNoMoneyBooksReportsGrowConnectedHeader\'s Money assertion ' +
    "passed even with 'receivables'\' capability gate deleted, so a cashier can now see the Money " +
    'group and this file would not notice. Fix the check, not the mutation.');
  console.log(`PASS: the cashier-sees-no-Money check is mutation-proved  [caught: ${String(threw.message).split('\n')[0].slice(0, 140)}]`);
}

// ═════════════════════════════════════════════════════════════════════════════
// 11 — every status dot has a WORD next to it, never colour alone
//      (fix round: SURVIVED against the previous suite -- verifier's
//      dot_word_removed mutation deleted the .sub-nav-status-word span and
//      nothing in this file's PREVIOUS revision noticed)
// ═════════════════════════════════════════════════════════════════════════════

function testEveryStatusDotHasAWord() {
  const html = ownerEverythingVisibleHTML();
  const dots = [...html.matchAll(/<span class="status-dot [^"]*"[^>]*><\/span><span class="sub-nav-status-word">([^<]*)<\/span>/g)];
  assert.ok(dots.length >= 5, `only ${dots.length} status-dot+word pairs found -- expected at least 5 (4 Connected services items + the group header). Fixture is asserting about nothing.`);
  for (const m of dots) {
    assert.ok(m[1] && m[1].trim().length > 0,
      `a status-dot rendered with an EMPTY word span -- design_system's "always a dot plus a word, ` +
      `never colour alone" is violated. Full match: ${JSON.stringify(m[0])}`);
  }
  console.log(`PASS: all ${dots.length} status dots (owner) carry a non-empty word`);
}

function testStatusDotWordMutationIsCaught() {
  const src = fs.readFileSync(SHELL_FILE, 'utf8');
  const mutated = mutateSource(
    src,
    '<span class="sub-nav-status"><span class="status-dot ${CONNECTED_STATUS_CLASS[s]}" aria-hidden="true"></span><span class="sub-nav-status-word">${CONNECTED_STATUS_WORD[s]}</span></span>',
    '<span class="sub-nav-status"><span class="status-dot ${CONNECTED_STATUS_CLASS[s]}" aria-hidden="true"></span><span class="sub-nav-status-word"></span></span>'
  );
  let threw = null;
  try {
    const html = renderNavFromSource(mutated, { capabilities: null, role: 'admin', isAdminDevice: true });
    const dots = [...html.matchAll(/<span class="status-dot [^"]*"[^>]*><\/span><span class="sub-nav-status-word">([^<]*)<\/span>/g)];
    assert.ok(dots.length > 0, 'no dots found at all -- fixture broke');
    for (const m of dots) assert.ok(m[1] && m[1].trim().length > 0, 'empty status word');
  } catch (err) {
    threw = err;
  }
  assert.ok(threw,
    'MUTATION SURVIVED — testEveryStatusDotHasAWord passed even with every status word span emptied ' +
    'out, so it is not actually watching for a dot rendered with no word next to it. Fix the check, not the mutation.');
  console.log(`PASS: the dot-always-has-a-word check is mutation-proved  [caught: ${String(threw.message).split('\n')[0].slice(0, 140)}]`);
}

// ═════════════════════════════════════════════════════════════════════════════
// 12 — 'Log Out' is present in the identity chip menu
//      (fix round: SURVIVED against the previous suite -- verifier's
//      chip_logout_removed mutation deleted the whole Log Out row and
//      nothing in this file's PREVIOUS revision noticed)
// ═════════════════════════════════════════════════════════════════════════════

function testLogOutPresentInIdentityChipMenu() {
  const html = ownerEverythingVisibleHTML();
  const menuMatch = /id="sub-identity-menu"[^>]*>([\s\S]*?)<\/div>\s*<\/div>/.exec(html);
  assert.ok(menuMatch, 'no #sub-identity-menu panel found in the rendered shell.');
  const menuHTML = menuMatch[1];
  assert.ok(
    /SubsystemApp\.logout\(\)/.test(menuHTML) && />\s*Log Out\s*</.test(menuHTML.replace(/\s+/g, ' ')),
    "the identity chip menu has no 'Log Out' row calling SubsystemApp.logout() -- a signed-in owner " +
    'would have no way to sign out at all.'
  );
  console.log("PASS: 'Log Out' is present and wired to SubsystemApp.logout() in the identity chip menu");
}

function testLogOutRemovalMutationIsCaught() {
  const src = fs.readFileSync(SHELL_FILE, 'utf8');
  const mutated = mutateSource(
    src,
    `          <button type="button" class="sub-identity-menu-item is-danger" role="menuitem" onclick="SubsystemApp._closeIdentityMenu();SubsystemApp.logout()">\${t('Log Out')}</button>\n`,
    ''
  );
  let threw = null;
  try {
    const html = renderNavFromSource(mutated, { capabilities: null, role: 'admin', isAdminDevice: true });
    const menuMatch = /id="sub-identity-menu"[^>]*>([\s\S]*?)<\/div>\s*<\/div>/.exec(html);
    assert.ok(menuMatch, 'no menu panel found');
    assert.ok(/SubsystemApp\.logout\(\)/.test(menuMatch[1]), 'no logout() call found in menu');
  } catch (err) {
    threw = err;
  }
  assert.ok(threw,
    'MUTATION SURVIVED — testLogOutPresentInIdentityChipMenu passed even with the entire Log Out row ' +
    'deleted from the identity chip menu, so it is not actually watching for it. Fix the check, not the mutation.');
  console.log(`PASS: the Log-Out-present check is mutation-proved  [caught: ${String(threw.message).split('\n')[0].slice(0, 140)}]`);
}

// Every case is run even after one fails, and each failure is printed with
// its own name -- see retail_reports_capability_gate_test.js for why a flat
// sequence that aborts on the first throw is the wrong shape here.
const CASES = [
  testEveryNavEntryIsInAGroup,
  testAdminCenterRendersAsFooterGearNotAGroupMember,
  testAllDestinationsReachableForOwner,
  testFiveSettingsScreensReachableInTwoClicksForOwner,
  testFiveSettingsScreensAbsentForCashier,
  testGroupsRenderInSpecifiedOrderWithMembers,
  testCashierSeesNoMoneyBooksReportsGrowConnectedHeader,
  testCashierOnTheAdminDeviceStillSeesNoGear,
  testStockGroupSurvivesWhenItsGatedMembersAreHidden,
  testDashboardRendersOutsideAnyGroup,
  testDashboardHiddenForSellOnlyCashierPresentForOthers,
  testAdminCenterGroupLeakIsCaught,
  testCashierMoneyLeakIsCaught,
  testEveryStatusDotHasAWord,
  testStatusDotWordMutationIsCaught,
  testLogOutPresentInIdentityChipMenu,
  testLogOutRemovalMutationIsCaught,
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
