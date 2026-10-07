/**
 * retail_connected_status_test.js -- the shell logic a real browser cannot
 * cheaply reach, run straight against app-shell.js in a node vm.
 *
 * Fix round 3 (P1a shell crew), HIGH 2 + HIGH 4. Every guard below is
 * MUTATION-PROVED at the bottom: the source is broken on purpose and the
 * guard must go red. A guard that stays green against its own mutation is
 * reported as MUTATION SURVIVED and fails the whole file.
 *
 *   1. _classifyConnectedStatus -- the pure classifier. It used to be a
 *      closure inside the poll, which is why a verifier's mutation that
 *      dropped FAILED_PERMANENT from the failed count survived every test:
 *      no test could reach it. Real outboxes use FAILED_PERMANENT, never a
 *      bare FAILED, so dropping it means "failing" can never fire.
 *   2. Every dot always has a word after a poll (a dot with no word is a
 *      colour the owner cannot name, and colour alone fails colour-blind
 *      users) -- _repaintConnectedServiceDots writing '' must go red.
 *   3. Before the first poll the neutral word is "Checking...", not
 *      "Can't check" (asking is not failing to ask); after a poll that
 *      could not read a channel it IS "Can't check".
 *   4. HIGH 2: the brand logo (exit()) and the phone Home tab must NOT land
 *      a sell-only cashier on the dashboard's refusal sentence. Both
 *      directions: cashier -> till, owner/manager -> dashboard, and unknown
 *      capabilities FAIL OPEN to the dashboard.
 *   5. LOW: the phone More sheet offers "License" only to the population
 *      that also sees the desktop gear (never to a cashier).
 *   6. LOW: the collapsed-group chevron points LEFT in Arabic/RTL (now a
 *      CSS rotation of one chevron-down SVG -- see testChevronMirrorsInArabic).
 *
 * What this cannot catch: anything that needs real layout (the gear menu's
 * placement, the header status being painted, the 44px touch floor, the rail
 * scroll) -- retail_p1a_shell_e2e.py drives those in a real browser.
 *
 *   node products/retail/tests/retail_connected_status_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SHELL_FILE = path.join(__dirname, '..', 'frontend', 'app-shell.js');
const SHELL_SRC = fs.readFileSync(SHELL_FILE, 'utf8');
const CSS_FILE = path.join(__dirname, '..', 'frontend', 'css', 'main.css');
const CSS_SRC = fs.readFileSync(CSS_FILE, 'utf8');

const CASHIER_CAPS = ['retail.cash.close', 'retail.refund', 'retail.sell'];
const MANAGER_CAPS = [...CASHIER_CAPS, 'retail.discount', 'retail.reports', 'retail.stock.adjust'];
const ADMIN_CAPS = [...MANAGER_CAPS, 'retail.employees', 'retail.cash.approve'];

function stub() {
  return {
    innerHTML: '', textContent: '', innerText: '', value: '', disabled: false, id: '',
    className: '', style: {}, dataset: {},
    classList: { toggle() {}, add() {}, remove() {} },
    appendChild() {}, addEventListener() {}, removeEventListener() {}, remove() {},
    getAttribute() { return null; }, setAttribute() {},
    querySelector() { return stub(); }, querySelectorAll() { return []; },
  };
}

function mutateSource(src, find, replace) {
  const eol = src.indexOf('\r\n') !== -1 ? '\r\n' : '\n';
  const nl = (s) => s.replace(/\n/g, eol);
  const f = nl(find), r = nl(replace);
  const hits = src.split(f).length - 1;
  assert.strictEqual(hits, 1,
    `Mutation anchor occurs ${hits} time(s), expected exactly 1:\n  ${JSON.stringify(f)}\n` +
    'A mutation that no longer applies would let its proof pass while proving nothing. Re-anchor it.');
  return src.split(f).join(r);
}

function load(src, { dir = null, lang = 'en', fetchImpl } = {}) {
  const created = [];
  const sandbox = {
    console, t: (s) => s, setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    location: { hash: '', href: 'http://localhost/' },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    AuraI18n: { current: lang },
    document: {
      readyState: 'complete', body: stub(), head: { appendChild() {} },
      documentElement: { getAttribute: (n) => (n === 'dir' ? dir : null), setAttribute() {}, style: { setProperty() {} } },
      getElementById() { return null; },
      createElement() { const e = stub(); created.push(e); return e; },
      querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {},
    },
  };
  if (fetchImpl) sandbox.fetch = fetchImpl;
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: SHELL_FILE });
  assert.ok(sandbox.SubsystemApp, 'app-shell.js did not expose window.SubsystemApp');
  sandbox.__created = created;
  return sandbox;
}

function appFor(src, { caps, role = 'cashier', opts } = {}) {
  const sb = load(src, opts);
  const App = sb.SubsystemApp;
  App.capabilities = caps;
  App.role = role;
  App.isAdminDevice = false;
  App.activeModules = [];
  App.active = 'retail';
  App.currentSection = 'pos';
  App.currentUser = { id: 'u1', email: 'test@example.com' };
  return { sb, App };
}

function renderedShell(src, who, opts) {
  const { sb, App } = appFor(src, { ...who, opts });
  const shellEl = stub();
  sb.document.getElementById = (id) => (id === 'subsystem-shell' ? shellEl : stub());
  App._renderShell(App.systems.retail, 'retail');
  return shellEl.innerHTML;
}

const CASHIER = { caps: CASHIER_CAPS, role: 'cashier' };
const MANAGER = { caps: MANAGER_CAPS, role: 'manager' };
const OWNER = { caps: ADMIN_CAPS, role: 'admin' };
const UNKNOWN = { caps: null, role: 'cashier' };

// ── 1. classifier ────────────────────────────────────────────────────────────

function testClassifier(src) {
  const { App } = appFor(src, OWNER);
  const c = (id, d) => App._classifyConnectedStatus(id, d);
  // The real key names, measured off the real routes.
  assert.strictEqual(c('whatsapp', { enabled: true, whatsapp_configured: true, counts_by_state: { FAILED_PERMANENT: 1 } }), 'failing',
    'a WhatsApp outbox with a FAILED_PERMANENT row must read Failing');
  assert.strictEqual(c('email-notifications', { enabled: true, smtp_configured: true, counts_by_state: { FAILED_PERMANENT: 2 } }), 'failing');
  assert.strictEqual(c('einvoicing', { enabled: true, provider_configured: true, counts_by_state: { FAILED_PERMANENT: 1 } }), 'failing');
  // Failing wins over queued, and over "off" (a switched-off channel with dead rows is still failing).
  assert.strictEqual(c('whatsapp', { enabled: false, whatsapp_configured: false, counts_by_state: { FAILED_PERMANENT: 1, QUEUED: 3 } }), 'failing');
  // The allow half: in-flight rows are attention, not failing; clean and configured is ok.
  assert.strictEqual(c('whatsapp', { enabled: true, whatsapp_configured: true, counts_by_state: { QUEUED: 1 } }), 'attention');
  assert.strictEqual(c('einvoicing', { enabled: true, provider_configured: true, counts_by_state: { SUBMITTING_UNKNOWN: 1 } }), 'attention');
  assert.strictEqual(c('einvoicing', { enabled: true, provider_configured: true, counts_by_state: { AWAITING_CLEARANCE: 1 } }), 'attention');
  assert.strictEqual(c('whatsapp', { enabled: true, whatsapp_configured: true, counts_by_state: { SENT: 40 } }), 'ok',
    'a clean, configured channel must read Connected (the deny half must not swallow the allow half)');
  assert.strictEqual(c('whatsapp', { enabled: false, whatsapp_configured: false, counts_by_state: {} }), 'off');
  assert.strictEqual(c('email-notifications', { enabled: true, smtp_configured: false }), 'off');
  assert.strictEqual(c('einvoicing', { killswitch: { disabled: true }, enabled: true, provider_configured: true }), 'failing');
  assert.strictEqual(c('site-relay', { enabled: false }), 'off');
  assert.strictEqual(c('site-relay', { enabled: true, devices: [] }), 'attention');
  assert.strictEqual(c('site-relay', { enabled: true, devices: [{ revoked: true }] }), 'attention');
  assert.strictEqual(c('site-relay', { enabled: true, devices: [{ revoked: false }] }), 'ok');
  assert.strictEqual(c('no-such-channel', {}), 'unknown');
  assert.strictEqual(c('whatsapp', null), 'off', 'a null payload must not throw');
  console.log('PASS: _classifyConnectedStatus -- every branch, deny and allow halves');
}

// ── 2/3. dots always carry a word; neutral before the first poll ─────────────

function fakeDom(App) {
  const rows = {};
  for (const id of ['whatsapp', 'einvoicing', 'email-notifications', 'site-relay']) {
    rows[id] = { dot: { className: '' }, word: { textContent: '' } };
  }
  const header = { dot: { className: '' }, word: { textContent: '' } };
  const mk = (r) => ({ querySelector: (sel) => (sel === '.status-dot' ? r.dot : sel === '.sub-nav-status-word' ? r.word : null) });
  return {
    rows, header,
    install(sb) {
      sb.document.querySelectorAll = () => [];
      sb.document.querySelector = (sel) => {
        const m = /data-section="([^"]+)"/.exec(sel);
        if (m && rows[m[1]]) return mk(rows[m[1]]);
        if (sel === '[data-group-toggle="connected"]') return mk(header);
        return null;
      };
    },
  };
}

async function testDotsAlwaysHaveAWord(src) {
  const responses = {
    '/api/notifications/whatsapp/status': { data: { enabled: true, whatsapp_configured: true, counts_by_state: { FAILED_PERMANENT: 1 } } },
    '/api/notifications/status': { data: { enabled: true, smtp_configured: true, counts_by_state: {} } },
    '/api/einvoicing/status': { data: { enabled: false } },
    // site-relay: server error -> 'unknown'
  };
  const fetchImpl = async (url) => {
    if (url in responses) return { ok: true, json: async () => responses[url] };
    return { ok: false, json: async () => ({}) };
  };
  const { sb, App } = appFor(src, { ...OWNER, opts: { fetchImpl } });
  const dom = fakeDom(App);
  dom.install(sb);

  // Before any poll: neutral "Checking..." everywhere -- NOT "Can't check".
  App._repaintConnectedServiceDots();
  for (const [id, r] of Object.entries(dom.rows)) {
    assert.strictEqual(r.word.textContent, 'Checking…', `${id}: before the first poll the word must be the neutral "Checking…", got ${JSON.stringify(r.word.textContent)}`);
    assert.ok(/is-checking/.test(r.dot.className), `${id}: dot class before first poll should be is-checking, got ${r.dot.className}`);
  }

  await App._pollConnectedServicesStatus();
  const want = { whatsapp: 'Failing', 'email-notifications': 'Connected', einvoicing: 'Off', 'site-relay': 'Can\'t check' };
  for (const [id, r] of Object.entries(dom.rows)) {
    assert.ok(r.word.textContent && r.word.textContent.length > 0, `${id}: after a poll the dot has an EMPTY word`);
    assert.strictEqual(r.word.textContent, want[id], `${id}: wrong word after poll`);
  }
  assert.ok(dom.header.word.textContent.length > 0, 'the collapsed header has an empty word after a poll');
  assert.strictEqual(dom.header.word.textContent, 'Failing', 'the header must show the WORST state (Failing)');
  console.log('PASS: after a poll every dot (and the header) has a non-empty word; neutral before the first poll');
}

// ── 4. HIGH 2: where "home" goes ─────────────────────────────────────────────

function homeTarget(src, who) {
  const { App } = appFor(src, who);
  const calls = [];
  App.launch = (a, b) => { calls.push([a, b]); };
  App.exit();
  assert.strictEqual(calls.length, 1, 'exit() did not launch anything');
  return calls[0][1];
}

function testLogoGoesToTillForCashierOnly(src) {
  assert.strictEqual(homeTarget(src, CASHIER), 'pos', 'a sell-only cashier clicking the brand logo must land on the till, not the dashboard refusal');
  assert.strictEqual(homeTarget(src, OWNER), 'dashboard', 'the owner\'s logo must still go to the dashboard');
  assert.strictEqual(homeTarget(src, MANAGER), 'dashboard', 'a manager\'s logo must still go to the dashboard');
  assert.strictEqual(homeTarget(src, UNKNOWN), 'dashboard', 'unknown capabilities must FAIL OPEN to the dashboard, never route to the till');
  console.log('PASS: logo/exit() goes to the till for a sell-only cashier; owner, manager and unknown stay on the dashboard');
}

function testPhoneHomeTab(src) {
  const cashier = renderedShell(src, CASHIER);
  assert.ok(!/data-tab="dashboard"/.test(cashier), 'a cashier\'s phone bar still has a tab that opens the dashboard');
  assert.ok(/data-tab="pos"/.test(cashier), 'a cashier\'s phone bar has no Till tab');
  assert.strictEqual((cashier.match(/data-tab="pos"/g) || []).length, 1, 'the Till tab must appear exactly once (Home slot is dropped, not duplicated)');
  for (const who of [OWNER, MANAGER, UNKNOWN]) {
    const html = renderedShell(src, who);
    assert.ok(/data-tab="dashboard"/.test(html), 'owner/manager/unknown phone bar lost its Home tab');
    assert.ok(/data-tab="pos"/.test(html));
  }
  console.log('PASS: phone Home tab -- cashier bar has Till and no dashboard tab; owner/manager/unknown keep Home');
}

// ── 5. More sheet License ────────────────────────────────────────────────────

function moreSheetHTML(src, who) {
  const { sb, App } = appFor(src, who);
  sb.document.getElementById = () => null;
  App._openMoreSheet();
  const sheet = sb.__created.find((e) => e.id === 'sub-more-sheet');
  assert.ok(sheet, 'the More sheet element was not created');
  return sheet.innerHTML;
}

function testMoreSheetLicenseGating(src) {
  assert.ok(!/openLicensing\(\)/.test(moreSheetHTML(src, CASHIER)), 'a cashier\'s More sheet offers License (the desktop gear does not)');
  assert.ok(/openLicensing\(\)/.test(moreSheetHTML(src, OWNER)), 'the owner\'s More sheet lost its License row');
  assert.ok(/License &amp; devices|License & devices/.test(moreSheetHTML(src, OWNER)), 'the More sheet must use the one spelling "License & devices"');
  assert.ok(!/Licence/.test(moreSheetHTML(src, OWNER)), 'the British spelling "Licence" crept back into the More sheet');
  // The cashier must still get Log Out (the deny half must not eat the footer).
  assert.ok(/logout\(\)/.test(moreSheetHTML(src, CASHIER)), 'the cashier lost Log Out from the More sheet');
  console.log('PASS: More sheet -- License only for someone who can see the gear; Log Out stays for everyone');
}

// ── 6. chevron ───────────────────────────────────────────────────────────────

// 2026-10-07: the chevron is no longer two text glyphs swapped by
// _chevronGlyph (deleted). It is ONE chevron-down SVG that main.css rotates
// by aria-expanded, so this check moved from "the JS returns the right
// glyph" to "the stylesheet rotates it to the right side". What it still
// catches: a closed group's chevron pointing the wrong way in Arabic (the
// 2026-09 LOW this guarded). What it can no longer catch: the glyph the JS
// writes (there is none) -- the real-browser probe measures the painted
// rotation.
function testChevronMirrorsInArabic(css) {
  const closed = /button\.sub-nav-group-label\[aria-expanded="false"\]\s+\.sub-nav-group-chevron\s*\{\s*transform:\s*rotate\((-?\d+)deg\)/.exec(css);
  assert.ok(closed, 'no closed-state rotation rule for the group chevron');
  assert.strictEqual(closed[1], '-90', 'a collapsed chevron in English must point right (rotate -90deg from chevron-down)');
  const rtl = /(?:html\[dir="rtl"\]|body\.rtl)\s+button\.sub-nav-group-label\[aria-expanded="false"\]\s+\.sub-nav-group-chevron\s*(?:,[^{]*)?\{\s*transform:\s*rotate\((-?\d+)deg\)/.exec(css);
  assert.ok(rtl, 'no RTL override for the closed chevron');
  assert.strictEqual(rtl[1], '90', 'a collapsed chevron in Arabic must point LEFT (rotate 90deg)');
  assert.ok(!/button\.sub-nav-group-label\[aria-expanded="true"\][^{]*chevron[^{]*\{[^}]*rotate/.test(css),
    'an open chevron must not be rotated: it points down in both directions');
  assert.ok(/AuraIcons\.render\('chevron-down'/.test(SHELL_SRC), 'the group header no longer renders the chevron-down icon');
  console.log('PASS: chevron -- collapsed rotates to point right in EN and left in AR; open stays down');
}

// ── 7. rendered header before the first poll ─────────────────────────────────

function testHeaderNeutralBeforePoll(src) {
  const html = renderedShell(src, OWNER);
  assert.ok(/Checking…/.test(html), 'the owner\'s Connected services rows show no "Checking…" before the first poll');
  assert.ok(!/Can't check|Can\\'t check/.test(html), 'the owner\'s rows say "Can\'t check" before anything has been asked');
  console.log('PASS: before the first poll the rows read "Checking…", never "Can\'t check"');
}

// ── runner + mutations ───────────────────────────────────────────────────────

async function provesMutation(what, brokenSrc, check) {
  let threw = null;
  try { await check(brokenSrc); } catch (e) { threw = e; }
  assert.ok(threw, `MUTATION SURVIVED -- ${what}\nThe guard passed against a build with the behaviour deliberately broken.`);
  return `${what}  [RED: ${String(threw.message || threw).split('\n')[0].slice(0, 110)}]`;
}

let failures = 0;
async function run(name, fn) {
  try {
    const out = await fn();
    console.log(`  ok   ${name}`);
    if (typeof out === 'string') console.log(`       ${out}`);
  } catch (e) {
    failures += 1;
    console.log(`  FAIL ${name}\n       ${String(e && e.message || e).split('\n').join('\n       ')}`);
  }
}

(async () => {
  await run('classifier', () => testClassifier(SHELL_SRC));
  await run('dots always have a word', () => testDotsAlwaysHaveAWord(SHELL_SRC));
  await run('logo goes to the till for a cashier only', () => testLogoGoesToTillForCashierOnly(SHELL_SRC));
  await run('phone Home tab', () => testPhoneHomeTab(SHELL_SRC));
  await run('More sheet License gating', () => testMoreSheetLicenseGating(SHELL_SRC));
  await run('chevron mirrors in Arabic', () => testChevronMirrorsInArabic(CSS_SRC));
  await run('header neutral before the first poll', () => testHeaderNeutralBeforePoll(SHELL_SRC));

  await run('M1: FAILED_PERMANENT dropped from the failed count => classifier RED', async () => {
    const b = mutateSource(SHELL_SRC,
      'const failed = (c.FAILED_PERMANENT || 0) + (c.FAILED || 0) + (c.failed || 0);',
      'const failed = (c.FAILED || 0) + (c.failed || 0);');
    return provesMutation('M1 FAILED_PERMANENT dropped', b, testClassifier);
  });
  await run('M2: a repaint that writes an empty word => dots RED', async () => {
    const b = mutateSource(SHELL_SRC,
      'if (wordEl) wordEl.textContent = word[s];',
      "if (wordEl) wordEl.textContent = '';");
    return provesMutation('M2 empty status word', b, testDotsAlwaysHaveAWord);
  });
  await run('M3: exit() always goes to the dashboard => logo RED', async () => {
    const b = mutateSource(SHELL_SRC,
      "const home = () => this.launch('retail', this._isSellOnlyCapabilities() ? 'pos' : 'dashboard');",
      "const home = () => this.launch('retail', 'dashboard');");
    return provesMutation('M3 exit() -> dashboard for everyone', b, testLogoGoesToTillForCashierOnly);
  });
  await run('M3b: exit() always goes to the till => fail-open half RED', async () => {
    const b = mutateSource(SHELL_SRC,
      "const home = () => this.launch('retail', this._isSellOnlyCapabilities() ? 'pos' : 'dashboard');",
      "const home = () => this.launch('retail', 'pos');");
    return provesMutation('M3b exit() -> till for everyone', b, testLogoGoesToTillForCashierOnly);
  });
  await run('M4: the Home tab put back for a cashier => phone tab RED', async () => {
    const b = mutateSource(SHELL_SRC,
      "${sellOnly ? '' : tabHTML('dashboard', '🏠', 'Home')}",
      "${tabHTML('dashboard', '🏠', 'Home')}");
    return provesMutation('M4 Home tab back for a cashier', b, testPhoneHomeTab);
  });
  await run('M5: License row shown to everyone in the More sheet => RED', async () => {
    const b = mutateSource(SHELL_SRC,
      '(hasSettings || hasEmployees || hasBranches || hasBackupExport) ? `\n            <button class="sub-more-row" onclick="SubsystemApp._closeMoreSheet();SubsystemApp.openLicensing()">',
      'true ? `\n            <button class="sub-more-row" onclick="SubsystemApp._closeMoreSheet();SubsystemApp.openLicensing()">');
    return provesMutation('M5 License for a cashier', b, testMoreSheetLicenseGating);
  });
  await run('M6: chevron never mirrors => RED', async () => {
    const b = mutateSource(CSS_SRC,
      'body.rtl button.sub-nav-group-label[aria-expanded="false"] .sub-nav-group-chevron { transform: rotate(90deg); }',
      'body.rtl button.sub-nav-group-label[aria-expanded="false"] .sub-nav-group-chevron { transform: rotate(-90deg); }');
    return provesMutation('M6 chevron does not mirror', b, testChevronMirrorsInArabic);
  });
  await run('M7: neutral state removed (unknown before the first poll) => RED', async () => {
    const b = mutateSource(SHELL_SRC,
      "const connectedStateOf = (id) => (this._connectedStatus && this._connectedStatus[id]) || (this._connectedPolled ? 'unknown' : 'checking');",
      "const connectedStateOf = (id) => (this._connectedStatus && this._connectedStatus[id]) || 'unknown';");
    return provesMutation('M7 "Can\'t check" before the first poll', b, testHeaderNeutralBeforePoll);
  });

  if (failures) {
    console.log(`\nFAIL: retail_connected_status_test.js -- ${failures} check(s) failed`);
    process.exit(1);
  }
  console.log('\nPASS: retail_connected_status_test.js');
})();
