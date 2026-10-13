/**
 * UI-3b -- "one map": Reports & Books tabs, the gear opening the hub directly, a lean identity menu,
 * remembered section, header language/tooltips/chip, modals below a top banner, and the till rail labels.
 *
 * THE CENTREPIECE is a role x destination reachability TABLE, BEFORE vs AFTER, in both directions:
 *   - BEFORE is the set of destinations each role could open on main @4db70a0b, computed by running the
 *     OLD app-shell.js + settings-hub.js through reachBefore() (a gear MENU, a Books group, an identity
 *     "Plan & devices" row) and baked into BEFORE below. It can be regenerated:
 *         node retail_ui3b_nav_test.js --dump-before <dir holding the OLD app-shell.js + settings-hub.js>
 *   - AFTER is computed live from the current sources by reachAfter() (hub sections, tab bar, gear).
 *   Equality, not subset: a destination a role LOST is a regression (a door closed), and one a role GAINED
 *   is a leak (a door opened that was never theirs). Both fail.
 *
 * What a green run here does NOT prove: that each screen renders. The Playwright run does that (it clicks
 * through the real tabs, the real gear, the real hub). This file proves WHO is offered WHAT and WHERE.
 *
 *   node products/retail/tests/retail_ui3b_nav_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const SHELL_FILE = path.join(FRONTEND, 'app-shell.js');
const HUB_FILE = path.join(FRONTEND, 'settings-hub.js');
const CSS_FILE = path.join(FRONTEND, 'css', 'main.css');
const SHELL_SRC = fs.readFileSync(SHELL_FILE, 'utf8');
const HUB_SRC = fs.readFileSync(HUB_FILE, 'utf8');
const CSS_SRC = fs.readFileSync(CSS_FILE, 'utf8');

// ── real grant sets (commercial_runtime/identity/user_accounts.py ROLE_CAPABILITIES) ─────────────
const BOOKS = ['retail.books.view', 'retail.books.record', 'retail.books.approve', 'retail.books.close'];
const ADMIN_ALL = ['retail.cash.approve', 'retail.cash.close', 'retail.discount', 'retail.employees',
  'retail.refund', 'retail.reports', 'retail.sell', 'retail.stock.adjust', ...BOOKS];
const MANAGER = ADMIN_ALL.filter((c) => c !== 'retail.employees' && c !== 'retail.cash.approve' && !BOOKS.includes(c));
const ROLES = {
  owner:            { role: 'admin',   caps: ADMIN_ALL, device: true },
  ownerSecondTill:  { role: 'admin',   caps: ADMIN_ALL, device: false },
  manager:          { role: 'manager', caps: MANAGER, device: false },
  delegatedManager: { role: 'manager', caps: [...MANAGER, 'retail.employees'], device: false },
  cashier:          { role: 'cashier', caps: ['retail.cash.close', 'retail.refund', 'retail.sell'], device: false },
  accountant:       { role: 'manager', caps: ['retail.reports', ...BOOKS], device: false },
  // books.view alone: may open Expenses but NOT retail.reports -- the row must still exist and open on Expenses.
  expensesOnly:     { role: 'manager', caps: ['retail.books.view'], device: false },
};

// ── sandbox ───────────────────────────────────────────────────────────────────────────────────────
function el() {
  return { innerHTML: '', textContent: '', innerText: '', value: '', hidden: false, id: '', className: '', style: {},
    dataset: {}, classList: { toggle() {}, add() {}, remove() {} }, appendChild() {}, addEventListener() {},
    removeEventListener() {}, remove() {}, getAttribute() { return null; }, setAttribute() {},
    querySelector() { return el(); }, querySelectorAll() { return []; } };
}

function boot(shellSrc, hubSrc, who, extra = {}) {
  const store = extra.store || {};
  const sandbox = {
    console, t: (s) => s, setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    location: { hash: '', href: 'http://localhost/' },
    localStorage: extra.localStorage || {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    document: {
      readyState: 'complete', body: el(), head: { appendChild() {} },
      documentElement: { getAttribute() { return null; }, setAttribute() {}, style: { setProperty() {} } },
      getElementById() { return null; }, createElement() { return el(); },
      querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(shellSrc, sandbox, { filename: 'app-shell.js' });
  vm.runInContext(hubSrc, sandbox, { filename: 'settings-hub.js' });
  if (extra.help) sandbox.AuraHelp = { open() {} };
  sandbox.PlanDevices = { render() {} };
  const App = sandbox.SubsystemApp;
  App.capabilities = who.caps;
  App.role = who.role;
  App.isAdminDevice = !!who.device;
  App.canClaimAdminDevice = extra.canClaim !== false;
  App.activeModules = [];
  App.active = 'retail';
  App.currentSection = 'dashboard';
  App.currentUser = { id: extra.userId || 'u1', email: 'u@example.com' };
  if (extra.deviceNamed !== undefined) App.deviceNamed = extra.deviceNamed;
  if (extra.devicePlatform !== undefined) App.devicePlatform = extra.devicePlatform;
  if (extra.branchName !== undefined) App.branchName = extra.branchName;
  const shellEl = el();
  const tabsEl = el();
  tabsEl.hidden = true;
  sandbox.document.getElementById = (id) => (id === 'subsystem-shell' ? shellEl : id === 'sub-section-tabs' ? tabsEl : el());
  App._renderShell(App.systems.retail, 'retail');
  return { sandbox, App, html: shellEl.innerHTML, tabsEl };
}

function region(html, openMarker, closeMarker) {
  const s = html.indexOf(openMarker);
  if (s < 0) return '';
  const e = html.indexOf(closeMarker, s);
  return html.slice(s, e < 0 ? html.length : e);
}
const subNav = (html) => region(html, 'id="sub-nav"', '</nav>');
const gearMenu = (html) => region(html, 'id="sub-settings-menu"', '</aside>');

function hubVisible(sandbox) {
  return Array.from(sandbox.SettingsHub.sections()).filter((s) => s.visible).map((s) => s.id);
}

// ── logical destinations ───────────────────────────────────────────────────────────────────────────
// nav ids + hub sections + "plan-devices" (the licence/devices page) + "claim-main-computer".
function navIds(App) { return App.systems.retail.nav.map((i) => i.id); }

// BEFORE (main @4db70a0b): sidebar rows, the gear MENU, the identity menu, the hub.
function reachBefore({ sandbox, App, html }) {
  const out = new Set();
  const nav = subNav(html);
  const menu = gearMenu(html);
  for (const id of navIds(App)) {
    if (new RegExp(`data-section="${id}"`).test(nav) || menu.includes(`_navigate('${id}')`)) out.add(id);
  }
  const idMenu = region(html, 'id="sub-identity-menu"', '</div>');
  if (menu.includes('SubsystemApp.openLicensing()') || idMenu.includes('SubsystemApp.openLicensing()')) out.add('plan-devices');
  if (menu.includes('_claimAdminDeviceFromMenu')) out.add('claim-main-computer');
  // A hub section a person may open is a destination whether or not they have the gear: the command palette
  // lists every visible section (palette.js _hubSections) -- so this is where a section's own visibility gate
  // is observable for roles with no gear at all.
  hubVisible(sandbox).forEach((id) => out.add('hub:' + id));
  return out;
}

// AFTER: sidebar rows + the tab bar of the row's own set, the gear -> hub, hub sections.
function reachAfter({ sandbox, App, html, tabsEl }) {
  const out = new Set();
  const nav = subNav(html);
  for (const id of navIds(App)) {
    // 'reports' is the tab-set ROW's data-section, not proof the Reports screen is openable (an
    // Expenses-only person has the row but not Reports): it is judged below, as a tab.
    if (id === 'reports') continue;
    if (new RegExp(`data-section="${id}"`).test(nav)) out.add(id);
  }
  // A tab-set row opens on its first visible tab, and from there every visible tab is one click.
  const row = /data-section="reports"[\s\S]*?onclick="SubsystemApp\._navigate\('([^']+)'\)"/.exec(nav);
  if (row) {
    out.add(row[1]);
    App._syncSectionTabs(row[1]);
    for (const m of tabsEl.innerHTML.matchAll(/data-tab-section="([^"]+)"/g)) out.add(m[1]);
  }
  const gear = /id="sub-settings-gear-btn"[^>]*onclick="SubsystemApp\._navigate\('admin-center'\)"/.test(html);
  const hub = gear ? hubVisible(sandbox) : [];
  if (gear) out.add('admin-center');
  hubVisible(sandbox).forEach((id) => out.add('hub:' + id));   // palette door, see reachBefore
  // Doors that moved INTO the hub keep their logical name.
  // Employees (the old OWNER-only screen) is now the hub's Team section; the old nav gate (role admin) is kept,
  // because a delegated manager already had Team in the hub and never had the Employees screen.
  if (hub.includes('team') && App.role === 'admin') out.add('employees');
  if (hub.includes('branches')) out.add('branches');
  if (hub.includes('data-backup')) out.add('backup-export');
  if (hub.includes('plan-devices')) out.add('plan-devices');
  if (hub.includes('plan-devices') && App.canClaimAdminDevice) out.add('claim-main-computer');
  return out;
}

const sorted = (set) => Array.from(set).sort();

// ── regeneration of BEFORE ----------------------------------------------------------------------
if (process.argv[2] === '--dump-before') {
  const dir = process.argv[3];
  const oldShell = fs.readFileSync(path.join(dir, 'app-shell.js'), 'utf8');
  const oldHub = fs.readFileSync(path.join(dir, 'settings-hub.js'), 'utf8');
  const table = {};
  for (const [name, who] of Object.entries(ROLES)) table[name] = sorted(reachBefore(boot(oldShell, oldHub, who)));
  console.log(JSON.stringify(table, null, 1));
  process.exit(0);
}

const BEFORE = JSON.parse(fs.readFileSync(path.join(__dirname, 'retail_ui3b_nav_before.json'), 'utf8'));

// ── mutation harness (anchors must occur exactly once, CRLF-aware) ──────────────────────────────────
function mutate(src, find, replace) {
  const eol = src.indexOf('\r\n') !== -1 ? '\r\n' : '\n';
  const nl = (s) => s.replace(/\n/g, eol);
  const hits = src.split(nl(find)).length - 1;
  assert.strictEqual(hits, 1, `Mutation anchor occurs ${hits} time(s), expected exactly 1:\n  ${JSON.stringify(find)}`);
  return src.split(nl(find)).join(nl(replace));
}
function mustFail(label, fn) {
  let threw = null;
  try { fn(); } catch (e) { threw = e; }
  assert.ok(threw, `MUTATION SURVIVED -- ${label}: the guard passed against a build with this behaviour broken.`);
  console.log(`   ok   ${label}  [caught: ${String(threw.message).split('\n')[0].slice(0, 90)}]`);
}

// ═════════════════════════════════════════════════════════════════════════════════════════════════
// 1 -- reachability table: every role opens exactly what it could open before
// ═════════════════════════════════════════════════════════════════════════════════════════════════
function assertTableEqualsBefore(shellSrc, hubSrc) {
  for (const [name, who] of Object.entries(ROLES)) {
    const after = reachAfter(boot(shellSrc, hubSrc, who));
    const before = new Set(BEFORE[name]);
    const lost = sorted(before).filter((d) => !after.has(d));
    // The two INTENDED new hub names. 'hub:branches' replaces the old gear-menu Branches row, and
    // 'hub:plan-devices' now also exists (as a small door pane) for the delegated manager whom the old gear menu
    // let into "License & devices". Each is expected EXACTLY for the roles that had the old door -- never for anyone else.
    const TWINS = { 'hub:branches': 'branches', 'hub:plan-devices': 'plan-devices' };
    const gained = sorted(after).filter((d) => !before.has(d) && !(TWINS[d] && before.has(TWINS[d])));
    for (const [hubName, old] of Object.entries(TWINS)) if (before.has(old) && !after.has(hubName)) lost.push(hubName);
    assert.deepStrictEqual({ lost, gained }, { lost: [], gained: [] },
      `role ${name}: destinations LOST (a door closed): [${lost.join(', ')}]; GAINED (a door opened that was never theirs): [${gained.join(', ')}]`);
  }
}

function testReachabilityTableBeforeVsAfter() {
  // Anti-vacuity: the baked BEFORE must hold real content for every role we claim to test.
  assert.ok(BEFORE.owner.length >= 30, `BEFORE.owner has only ${BEFORE.owner.length} destinations`);
  assert.ok(BEFORE.cashier.includes('pos') && !BEFORE.cashier.includes('reports'), 'BEFORE.cashier is not the cashier we expect');
  assert.ok(BEFORE.expensesOnly.includes('expenses') && !BEFORE.expensesOnly.includes('reports'),
    'BEFORE.expensesOnly must hold Expenses but not Reports (the case a naive "tabs under Reports" rule loses)');
  assertTableEqualsBefore(SHELL_SRC, HUB_SRC);
  // Print the table: one row per destination that changed DOOR is the useful part; show counts per role.
  const rows = Object.keys(ROLES).map((n) => `${n}=${BEFORE[n].length}`).join(' ');
  console.log(`PASS: reachability table equal before vs after for 7 roles (destinations per role: ${rows})`);
}

// the proof the table can fail in BOTH directions
function testReachabilityTableMutationsAreCaught() {
  // LOST: hub drops Branches -> owner/delegated manager lose the Branches door.
  mustFail('hub Branches section hidden => table fails (door lost)', () =>
    assertTableEqualsBefore(SHELL_SRC, mutate(HUB_SRC, "{ id: 'branches', label: t('Branches'), visible: dev },", "{ id: 'branches', label: t('Branches'), visible: false },")));
  // LOST: gear does not open the hub.
  mustFail('gear opens nothing => table fails (every settings door lost)', () =>
    assertTableEqualsBefore(mutate(SHELL_SRC, "               onclick=\"SubsystemApp._navigate('admin-center')\">\n              <span class=\"sub-nav-icon\">${window.AuraIcons ? AuraIcons.render('settings', 17)",
      "               onclick=\"void 0\">\n              <span class=\"sub-nav-icon\">${window.AuraIcons ? AuraIcons.render('settings', 17)"), HUB_SRC));
  // LOST: a tab dropped from the set (Cash Flow).
  mustFail('Cash Flow tab removed from navTabs => table fails (door lost)', () =>
    assertTableEqualsBefore(mutate(SHELL_SRC, "'balance-sheet', 'cash-flow', 'expenses'] },", "'balance-sheet', 'expenses'] },"), HUB_SRC));
  // GAINED: tabs ignore the per-tab gate (show every tab to everyone who sees the row).
  mustFail('tabs stop asking _isPageOffered => table fails (a cashier-adjacent door opened)', () =>
    assertTableEqualsBefore(mutate(SHELL_SRC, ".filter((item) => item && this._isPageOffered(item));\n  },\n\n  // What the sidebar / More sheet draws",
      ".filter((item) => !!item);\n  },\n\n  // What the sidebar / More sheet draws"), HUB_SRC));
  // GAINED: the delegated-manager door pane shown to everyone (cashier would not see the gear, but a manager
  // without retail.employees would gain Plan & devices).
  mustFail('Plan & devices offered to every role => table fails (door gained)', () =>
    assertTableEqualsBefore(SHELL_SRC, mutate(HUB_SRC, "visible: owner ? !!window.PlanDevices : dev }", "visible: true }")));
}

// ═════════════════════════════════════════════════════════════════════════════════════════════════
// 2 -- the tab bar itself
// ═════════════════════════════════════════════════════════════════════════════════════════════════
function tabIds(ctx, sectionId) {
  ctx.App._syncSectionTabs(sectionId);
  return Array.from(ctx.tabsEl.innerHTML.matchAll(/data-tab-section="([^"]+)"/g)).map((m) => m[1]);
}

function testTabBar(shellSrc = SHELL_SRC) {
  const owner = boot(shellSrc, HUB_SRC, ROLES.owner);
  assert.deepStrictEqual(tabIds(owner, 'trial-balance'),
    ['reports', 'trial-balance', 'profit-and-loss', 'balance-sheet', 'cash-flow', 'expenses'],
    'owner tab order: Reports first, then the four books, then Expenses');
  assert.strictEqual(owner.tabsEl.hidden, false);
  assert.ok(/aria-selected="true"[^>]*>Trial Balance|data-tab-section="trial-balance" aria-selected="true"/.test(owner.tabsEl.innerHTML),
    'the active section is marked aria-selected');
  assert.strictEqual((owner.tabsEl.innerHTML.match(/is-active/g) || []).length, 1, 'exactly one tab is active');
  // a screen outside the set: no bar at all
  assert.deepStrictEqual(tabIds(owner, 'products'), []);
  assert.strictEqual(owner.tabsEl.hidden, true, 'a screen outside the set must hide the bar');
  // highlight: every tab lights the ONE sidebar row
  for (const id of ['reports', 'trial-balance', 'profit-and-loss', 'balance-sheet', 'cash-flow', 'expenses']) {
    assert.strictEqual(owner.App._navHomeId(id), 'reports', `${id} must light the Reports & Books row`);
  }
  assert.strictEqual(owner.App._navHomeId('products'), 'products');
  // cashier: nothing (row hidden, bar hidden) -- the other direction
  const cashier = boot(shellSrc, HUB_SRC, ROLES.cashier);
  assert.deepStrictEqual(tabIds(cashier, 'trial-balance'), []);
  assert.ok(!/data-section="reports"/.test(subNav(cashier.html)), 'a cashier has no Reports & Books row');
  // expenses-only: the row exists, opens on Expenses, and one tab is no bar
  const ex = boot(shellSrc, HUB_SRC, ROLES.expensesOnly);
  const exRow = /data-section="reports"[\s\S]*?_navigate\('([^']+)'\)/.exec(subNav(ex.html));
  assert.ok(exRow && exRow[1] === 'expenses', `expenses-only person's row must open on Expenses, got ${exRow && exRow[1]}`);
  assert.deepStrictEqual(tabIds(ex, 'expenses'), []);
  // accountant: Reports + the four books + Expenses
  const acc = boot(shellSrc, HUB_SRC, ROLES.accountant);
  assert.strictEqual(tabIds(acc, 'cash-flow').length, 6);
  // deep link: the palette/_navigate id of a tab stays a plain nav id with its own gate
  const nav = owner.App.systems.retail.nav;
  for (const id of ['trial-balance', 'profit-and-loss', 'balance-sheet', 'cash-flow', 'expenses']) {
    assert.ok(nav.some((i) => i.id === id && i.capability), `${id} lost its capability gate`);
  }
  console.log('PASS: tab bar -- order, active tab, hidden outside the set, absent for a cashier, Expenses-only opens on Expenses');
}

function testTabBarMutationsAreCaught() {
  mustFail('tab order changed (Reports no longer first) => tab test fails', () =>
    testTabBar(mutate(SHELL_SRC, "tabs: ['reports', 'trial-balance',", "tabs: ['trial-balance', 'reports',")));
  mustFail('a lone tab still draws a bar => tab test fails', () =>
    testTabBar(mutate(SHELL_SRC, "tabs.length < 2", "tabs.length < 1")));
  mustFail('tab sync never hides the bar outside the set => tab test fails', () =>
    testTabBar(mutate(SHELL_SRC, "if (!set || tabs.length < 2) { el.innerHTML = ''; el.hidden = true; return; }", "if (!set || tabs.length < 2) { el.innerHTML = ''; return; }")));
}

const CASES = [
  testReachabilityTableBeforeVsAfter, testReachabilityTableMutationsAreCaught,   //#ITEM 1
  testTabBar, testTabBarMutationsAreCaught,                                      //#ITEM 1
];
let failed = 0;
for (const fn of CASES) {
  try { fn(); } catch (e) { failed += 1; console.error('  FAIL ' + fn.name + '\n       ' + (e && e.message ? e.message : e)); }
}
if (failed) { console.error(`FAIL: retail_ui3b_nav_test.js -- ${failed} of ${CASES.length} case(s) failed`); process.exitCode = 1; }
else console.log(`PASS: retail_ui3b_nav_test.js -- ${CASES.length} case(s)`);
