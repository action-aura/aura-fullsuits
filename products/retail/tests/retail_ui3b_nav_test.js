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

//#BEGIN-ITEM 2
// ═════════════════════════════════════════════════════════════════════════════════════════════════
// 3 -- remember where I was
// ═════════════════════════════════════════════════════════════════════════════════════════════════
function testRememberedSection(shellSrc = SHELL_SRC) {
  const store = {};
  const a = boot(shellSrc, HUB_SRC, ROLES.owner, { store, userId: 'u1' });
  a.App._rememberSection('sales');
  assert.strictEqual(a.App._landingSection(), 'sales', 'the owner returns to where they were');
  // per user
  const b = boot(shellSrc, HUB_SRC, ROLES.owner, { store, userId: 'u2' });
  assert.strictEqual(b.App._landingSection(), 'dashboard', 'another user does not inherit the first one\'s section');
  // a tab of Reports & Books is restorable (deep link into a tab)
  a.App._rememberSection('cash-flow');
  assert.strictEqual(a.App._landingSection(), 'cash-flow');
  // sell-only is ALWAYS the till, even with a stored report screen
  const cs = {};
  const c = boot(shellSrc, HUB_SRC, ROLES.cashier, { store: cs });
  c.App._rememberSection('reports');
  assert.strictEqual(c.App._landingSection(), 'pos', 'a sell-only person lands on the till');
  // a screen no longer offered falls back to Today
  const ms = {};
  const m1 = boot(shellSrc, HUB_SRC, ROLES.manager, { store: ms });
  m1.App._rememberSection('employees');
  assert.strictEqual(m1.App._landingSection(), 'dashboard', 'a stored screen the person may no longer open falls back to Today');
  m1.App._rememberSection('does-not-exist');
  assert.strictEqual(m1.App._landingSection(), 'dashboard');
  m1.App._rememberSection('whatsapp');
  assert.strictEqual(m1.App._landingSection(), 'dashboard', 'an external page is never restored as an in-shell section');
  // storage that throws neither crashes the save nor the read
  const boom = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }, removeItem() {} };
  const d = boot(shellSrc, HUB_SRC, ROLES.owner, { localStorage: boom });
  assert.doesNotThrow(() => d.App._rememberSection('sales'));
  assert.strictEqual(d.App._landingSection(), 'dashboard');
  // wiring: launch uses it, navigation records it
  assert.ok(/this\.launch\('retail', this\._landingSection\(\)\)/.test(shellSrc), 'init() must launch on _landingSection()');
  assert.ok(/this\._rememberSection\(sectionId\)/.test(shellSrc), '_navigateNow must record the section');
  console.log('PASS: section remembered per user, restored on launch, till for sell-only, safe when storage throws');
}

function testRememberedSectionMutationsAreCaught() {
  mustFail('sell-only rule dropped => test fails', () =>
    testRememberedSection(mutate(SHELL_SRC, "    if (this._isSellOnlyCapabilities()) return 'pos';\n    let stored", "    let stored")));
  mustFail('offered-check dropped => test fails', () =>
    testRememberedSection(mutate(SHELL_SRC, "if (!item || item.externalPage || !this._isPageOffered(item)) return 'dashboard';", "if (!item) return 'dashboard';")));
  mustFail('not keyed per user => test fails', () =>
    testRememberedSection(mutate(SHELL_SRC, "return 'aura_last_section_' + uid;", "return 'aura_last_section_x';")));
  mustFail('init no longer restores => test fails', () =>
    testRememberedSection(mutate(SHELL_SRC, "this.launch('retail', this._landingSection());", "this.launch('retail', this._isSellOnlyCapabilities() ? 'pos' : 'dashboard');")));
  mustFail('storage errors not swallowed => test fails', () =>
    testRememberedSection(mutate(SHELL_SRC, "try { localStorage.setItem(this._lastSectionStorageKey(), String(sectionId)); } catch (e) { /* private mode */ }", "localStorage.setItem(this._lastSectionStorageKey(), String(sectionId));")));
}

//#END-ITEM 2

//#BEGIN-ITEM 4
// ═════════════════════════════════════════════════════════════════════════════════════════════════
// 4 -- header: language, tooltips, identity chip
// ═════════════════════════════════════════════════════════════════════════════════════════════════
function testHeader(shellSrc = SHELL_SRC) {
  const ctx = boot(shellSrc, HUB_SRC, ROLES.owner, { help: true, deviceNamed: null, devicePlatform: 'WINDOWS', branchName: 'Main Branch' });
  const h = ctx.html;
  const lang = /<button[^>]*id="aura-lang-toggle"[\s\S]*?<\/button>/.exec(h);
  assert.ok(lang, 'no language button');
  assert.ok(lang[0].includes('>EN<') && lang[0].includes('>ع<') && lang[0].includes('sub-lang-sep'),
    'the language button spells out both languages with a divider (EN | ع)');
  assert.strictEqual((lang[0].match(/is-current/g) || []).length, 1, 'exactly one language is marked current');
  assert.ok(/aria-label="[^"]+"/.test(lang[0]) && /title="[^"]+"/.test(lang[0]), 'language button needs a tooltip and an aria-label');
  for (const [id, what] of [['aura-palette-trigger', 'search'], ['aura-lang-toggle', 'language'], ['sub-fullscreen-btn', 'full screen']]) {
    const m = new RegExp(`<button[^>]*id="${id}"[^>]*>`).exec(h);
    assert.ok(m && /title="[^"]+"/.test(m[0]) && /aria-label="[^"]+"/.test(m[0]), `${what} button needs title + aria-label`);
  }
  const theme = /<button[^>]*onclick="ThemeEngine\.openPicker\(\)"[^>]*title=[^>]*>/.exec(h.slice(h.indexOf('sub-header-right')));
  assert.ok(theme && /aria-label="[^"]+"/.test(theme[0]), 'theme button needs an aria-label');
  // the chip: "Windows" only if the device has a typed name
  const chip = region(h, 'id="sub-identity-chip-btn"', '</button>');
  assert.ok(!/Windows/i.test(chip), 'the platform word must not appear without a typed device name');
  assert.ok(chip.includes('Main Branch'), 'the branch still shows');
  const named = boot(shellSrc, HUB_SRC, ROLES.owner, { deviceNamed: 'Front till', devicePlatform: 'WINDOWS', branchName: 'Main Branch' });
  assert.ok(region(named.html, 'id="sub-identity-chip-btn"', '</button>').includes('Front till'), 'a typed device name still shows');
  // the identity menu keeps Language, Appearance, Help, Log out and drops Plan & devices
  const menu = region(h, 'id="sub-identity-menu"', '</div>');
  for (const keep of ['AuraI18n.toggle()', 'ThemeEngine.openPicker()', 'AuraHelp.open()', 'SubsystemApp.logout()']) {
    assert.ok(menu.includes(keep), `identity menu lost ${keep}`);
  }
  assert.ok(!menu.includes('openLicensing'), 'Plan & devices must live in the hub only');
  console.log('PASS: header -- EN | ع, tooltips + aria-labels on all four buttons, no platform word without a device name, lean identity menu');
}

function testHeaderMutationsAreCaught() {
  mustFail('chip shows the platform again => header test fails', () =>
    testHeader(mutate(SHELL_SRC, "    return this.deviceNamed || null;\n", "    return this.deviceNamed || this._platformLabel(this.devicePlatform) || null;\n")));
  mustFail('theme button loses its aria-label => header test fails', () =>
    testHeader(mutate(SHELL_SRC, " title=\"${t('Change UI theme')}\" aria-label=\"${t('Change UI theme')}\">", " title=\"${t('Change UI theme')}\">")));
  mustFail('language button back to one letter => header test fails', () =>
    testHeader(mutate(SHELL_SRC, "<span class=\"sub-lang-sep\" aria-hidden=\"true\">|</span>", "")));
  mustFail('Plan & devices back in the identity menu => header test fails', () =>
    testHeader(mutate(SHELL_SRC, "          <button type=\"button\" class=\"sub-identity-menu-item\" role=\"menuitem\" onclick=\"SubsystemApp._closeIdentityMenu();AuraI18n.toggle()\">${t('Language')}</button>",
      "          <button type=\"button\" class=\"sub-identity-menu-item\" role=\"menuitem\" onclick=\"SubsystemApp._closeIdentityMenu();SubsystemApp.openLicensing()\">x</button><button type=\"button\" class=\"sub-identity-menu-item\" role=\"menuitem\" onclick=\"SubsystemApp._closeIdentityMenu();AuraI18n.toggle()\">${t('Language')}</button>")));
}

//#END-ITEM 4

//#BEGIN-ITEM 3
// ═════════════════════════════════════════════════════════════════════════════════════════════════
// 3 (item 3) -- CSS: the till rail shows a label under every icon
// ═════════════════════════════════════════════════════════════════════════════════════════════════
function testCssRail(cssSrc = CSS_SRC) {
  const css = cssSrc.replace(/\r\n/g, '\n');
  assert.ok(/\.sub-sidebar\.sub-sidebar-rail\s*\{\s*inline-size:\s*96px;\s*\}/.test(css), 'rail must be 96px wide');
  // the hide-list must NOT contain the label any more
  const hide = /\.sub-sidebar\.sub-sidebar-rail \.sub-brand-text,[\s\S]*?\{\s*display:\s*none;/.exec(css);
  assert.ok(hide && !/sub-nav-label/.test(hide[0]), 'rail still hides .sub-nav-label');
  const lab = /\.sub-sidebar\.sub-sidebar-rail \.sub-nav-item \.sub-nav-label\s*\{([^}]*)\}/.exec(css);
  assert.ok(lab && /font-size:\s*10\.5px/.test(lab[1]) && /display:\s*-webkit-box/.test(lab[1]), 'rail labels: 10.5px, shown');
  assert.ok(/overflow-wrap:\s*break-word/.test(lab[1]) && !/overflow-wrap:\s*anywhere/.test(lab[1]), 'a long label must break between words, not inside them ("Quotation / s")');
  assert.ok(/\.sub-sidebar\.sub-sidebar-rail \.sub-nav \{[^}]*padding-inline:\s*4px/.test(css), 'the rail list must give labels their room (4px side padding)');
  console.log('PASS: css rail -- 96px rail, 10.5px labels shown, breaking between words');
}

function testCssRailMutationsAreCaught() {
  mustFail('rail back to 64px => css rail test fails', () => testCssRail(mutate(CSS_SRC, ".sub-sidebar.sub-sidebar-rail { inline-size: 96px; }", ".sub-sidebar.sub-sidebar-rail { inline-size: 64px; }")));
  mustFail('rail label hidden again => css rail test fails', () =>
    testCssRail(mutate(CSS_SRC, ".sub-sidebar.sub-sidebar-rail .sub-brand-text,\n.sub-sidebar.sub-sidebar-rail .sub-nav-hint,", ".sub-sidebar.sub-sidebar-rail .sub-brand-text,\n.sub-sidebar.sub-sidebar-rail .sub-nav-label,\n.sub-sidebar.sub-sidebar-rail .sub-nav-hint,")));
  mustFail('labels break inside words => css rail test fails', () =>
    testCssRail(mutate(CSS_SRC, "max-inline-size: 100%; text-align: center; overflow-wrap: break-word;", "max-inline-size: 100%; text-align: center; overflow-wrap: anywhere;")));
}
//#END-ITEM 3

const CASES = [
  testReachabilityTableBeforeVsAfter, testReachabilityTableMutationsAreCaught,   //#ITEM 1
  testTabBar, testTabBarMutationsAreCaught,                                      //#ITEM 1
  testRememberedSection, testRememberedSectionMutationsAreCaught,                //#ITEM 2
  testHeader, testHeaderMutationsAreCaught,                                      //#ITEM 4
  testCssRail, testCssRailMutationsAreCaught,                                    //#ITEM 3
];
let failed = 0;
for (const fn of CASES) {
  try { fn(); } catch (e) { failed += 1; console.error('  FAIL ' + fn.name + '\n       ' + (e && e.message ? e.message : e)); }
}
if (failed) { console.error(`FAIL: retail_ui3b_nav_test.js -- ${failed} of ${CASES.length} case(s) failed`); process.exitCode = 1; }
else console.log(`PASS: retail_ui3b_nav_test.js -- ${CASES.length} case(s)`);
