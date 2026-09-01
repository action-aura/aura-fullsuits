/**
 * retail_backup_export_test.js — Backup & Export screen (ci-hardening-w0.3
 * continuation, "the doorway", third one on this branch -- the one that
 * matters most).
 *
 * products/retail/frontend/licensing.js tells a customer whose licence is
 * RESTRICTED, SUSPENDED, EXPIRED or REVOKED -- the moment they are most
 * anxious about whether they can get their data out -- exactly this:
 *
 *   "Your existing data is safe and remains viewable; backup, restore, and
 *    export remain available."
 *
 * commercial_runtime/backup/routes.py has four complete, correctly-gated
 * routes (POST /create, GET /list, GET /download/<filename>, POST /restore)
 * and retail_api.py has three complete, correctly-gated CSV export routes
 * (/reports/export/{sales,payments,cash-sessions}). All seven are real,
 * tested and correct. Nothing in the frontend ever called any of them --
 * verified, zero requests anywhere. So the promise above was false at the UI
 * level for exactly the customer it is made to.
 *
 * THIS FILE'S ONE JOB: prove the doorway actually opens, and prove it by
 * running the real code, not by reading the diff. Seven things specifically:
 *
 *   1. The screen works under a RESTRICTED licence -- the promise itself.
 *   2. Creating a backup POSTs to the create route (the request, not a toast --
 *      "the call never happens" is the exact defect class being fixed).
 *   3. Existing backups are listed from the list route.
 *   4. Each of the three CSV exports is reachable and hits its own distinct
 *      route.
 *   5. Restore requires an explicit confirmation -- it does not fire from a
 *      single click.
 *   6. THE NAV ENTRY EXISTS and points at the screen.
 *   7. A user without the required capability does not see it (plus, beyond
 *      the brief's seven: a user WITH the capability but not the store
 *      owner does not see it either -- backup/routes.py's four routes are
 *      admin-only with no capability code at all, so this screen bundles
 *      two axes, same shape as _renderStockAccuracy).
 *
 * ── MUTATION-PROVED ─────────────────────────────────────────────────────────
 * Every behavioural claim below is re-run against a DELIBERATELY BROKEN copy
 * of subsystem-retail.js (or app-shell.js, for the nav entry) and is required
 * to FAIL there -- including an "allow-half" mutation that renders nothing at
 * all, which would otherwise pass every "does not show the wrong thing" check
 * by showing nothing (ENGINEERING.md's failure-shape #4/#5).
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_backup_export_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const RETAIL_JS = path.join(FRONTEND, 'subsystem-retail.js');
const APP_SHELL_JS = path.join(FRONTEND, 'app-shell.js');

const SRC = fs.readFileSync(RETAIL_JS, 'utf8');
const SHELL_SRC = fs.readFileSync(APP_SHELL_JS, 'utf8');

/* Match each file's OWN line ending -- an anchor written with the wrong one
   would silently match nothing, making every mutation proof below pass
   while breaking nothing (see retail_branches_test.js's identical comment
   on this exact hazard). */
function eolOf(src) { return src.indexOf('\r\n') !== -1 ? '\r\n' : '\n'; }
function nlFor(src) { const eol = eolOf(src); return (s) => s.replace(/\n/g, eol); }

const SECTION_ID = 'backup-export';
const BACKUP_LIST_URL = '/api/backup/list';
const BACKUP_CREATE_URL = '/api/backup/create';
const BACKUP_RESTORE_URL = '/api/backup/restore';
const EXPORT_SALES_URL = '/api/sub/retail/reports/export/sales';
const EXPORT_PAYMENTS_URL = '/api/sub/retail/reports/export/payments';
const EXPORT_CASH_SESSIONS_URL = '/api/sub/retail/reports/export/cash-sessions';
// A NON-allowlisted, mutating route -- used only to prove the simulated
// "restricted licence" session in testWorksUnderRestrictedLicence is real,
// not a session that happens never to differ (ENGINEERING.md failure-shape
// #2: the fixture must not manufacture the state that hides the bug).
const NON_ALLOWLISTED_URL = '/api/sub/retail/products';

// ─────────────────────────────────────────────────────────────────────────────
// FIXTURES — shapes the real routes actually put on the wire
// ─────────────────────────────────────────────────────────────────────────────

// commercial_runtime/backup/routes.py answers {status:'ok', backups:[...]},
// a DIFFERENT envelope from every retail_api.py route's {status:'success',
// data:...} -- a different module with its own convention.
const BACKUP_A = { filename: 'aura-retail-backup-20260115-090000.zip', size: 2097152, modified_at: 1768467600 };
const BACKUP_B = { filename: 'aura-retail-backup-20260201-030000.zip', size: 3145728, modified_at: 1769916000 };
const LIST_OK = { status: 'ok', backups: [BACKUP_A, BACKUP_B] };
const LIST_EMPTY = { status: 'ok', backups: [] };
const CREATE_OK = { status: 'ok', filename: 'aura-retail-backup-20260215-000000.zip', manifest: {} };
const RESTORE_OK = { status: 'ok', restored: true, rollback_dir: '/tmp/rollback-1', message: 'Restore complete. Restart the application before continuing.' };
const LICENSE_RESTRICTED_REFUSAL = { status: 'error', code: 'LICENSE_RESTRICTED', message: 'This licence is restricted.' };

// ─────────────────────────────────────────────────────────────────────────────
// THE SANDBOX — the real subsystem-retail.js, never a reimplementation
// ─────────────────────────────────────────────────────────────────────────────

function makeStub(over) {
  return Object.assign({
    innerHTML: '', outerHTML: '', textContent: '', value: '', id: '', disabled: false,
    style: {}, dataset: {}, href: undefined,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    addEventListener() {}, removeEventListener() {},
    focus() {}, blur() {}, remove() {}, click() {}, closest() { return null; },
  }, over || {});
}

/**
 * @param {object} opts
 *   source           — subsystem-retail.js text (a mutant, for the proofs below)
 *   capabilities     — what SubsystemApp.hasCapability answers yes to (default ['retail.reports'])
 *   role             — SubsystemApp.role (default '' -- UNRESOLVED, matching
 *                       retail_stock_accuracy_screen_test.js's convention for
 *                       the identical dual-gate shape; pass 'admin' explicitly
 *                       for the success paths)
 *   list             — GET /api/backup/list payload
 *   create           — POST /api/backup/create payload
 *   restore          — POST /api/backup/restore payload
 *   licenseRestricted — when true, a NON-allowlisted mutating route (creating
 *                       a product) is refused the way a real RESTRICTED-state
 *                       backend refuses it (RETAIL_RESTRICTED_ALLOWLIST,
 *                       retail_api.py:141-149) -- anti-vacuity proof that the
 *                       simulated session is genuinely restricted.
 */
function loadRetailSystem(opts) {
  const o = opts || {};
  const calls = [];
  const toasts = [];
  const downloads = [];
  const overlays = [];
  const els = Object.create(null);
  const getEl = (id) => (els[id] || (els[id] = makeStub({ id })));

  const sandbox = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    t: (s) => s, // identity stub -- this file checks WIRE/gating behaviour, catalog coverage is retail_surface_i18n_test.js's job
    fetch: (url, init) => {
      const method = ((init && init.method) || 'GET').toUpperCase();
      let body = null;
      if (init && typeof init.body === 'string') {
        try { body = JSON.parse(init.body); } catch (e) { body = init.body; }
      }
      const u = String(url);
      calls.push({ method, url: u, body });

      if (o.licenseRestricted && method === 'POST' && u.indexOf(NON_ALLOWLISTED_URL) === 0) {
        return Promise.resolve({ ok: true, status: 403, json: () => Promise.resolve(LICENSE_RESTRICTED_REFUSAL) });
      }

      let payload;
      if (u.indexOf(BACKUP_LIST_URL) === 0 && method === 'GET') {
        payload = o.list === undefined ? LIST_EMPTY : o.list;
      } else if (u.indexOf(BACKUP_CREATE_URL) === 0 && method === 'POST') {
        payload = o.create === undefined ? CREATE_OK : o.create;
      } else if (u.indexOf(BACKUP_RESTORE_URL) === 0 && method === 'POST') {
        payload = o.restore === undefined ? RESTORE_OK : o.restore;
      } else {
        payload = { status: 'error', message: 'unmapped fixture route: ' + method + ' ' + u };
      }
      if (payload instanceof Error) return Promise.reject(payload);
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
    },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    document: {
      activeElement: null,
      getElementById(id) { if (id === 'ret-styles') return null; return getEl(id); },
      createElement() { return makeStub(); },
      // _loadBackups reads its table body via getElementById (matching
      // _loadEmailNotifications' own convention, not _loadBranches'
      // querySelector one) -- see #bex-backup-tbody in the production code.
      querySelector() { return null; },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      // Downloads and the restore-confirm modal both reach document.body via
      // appendChild. An anchor <a> carries an .href (set before appendChild
      // runs, exactly like the browser needs it to); the restore-confirm
      // <div> never does. That single distinction is enough to route each to
      // its own tracked array without special-casing element tags.
      body: {
        appendChild(el) {
          if (el && el.href !== undefined) downloads.push(el.href);
          else if (el) overlays.push(el);
        },
      },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
  };
  sandbox.SubsystemApp = {
    active: 'retail',
    showToast(msg, type) { toasts.push({ msg, type }); },
    checkAuthAndSetup() {},
    _navigate() {},
    role: o.role === undefined ? '' : o.role,
    hasCapability: (code) => (o.capabilities || ['retail.reports']).includes(code),
    // Not read by any REAL code in this file (see _renderBackupExport's own
    // comment on why there is no licence-state field to key on) -- set here
    // only so M2 below, which mutates in a HYPOTHETICAL such check, has a
    // real signal to observe. Without this the mutation would be inert
    // regardless of whether the guard it adds is wired up correctly.
    licenseState: o.licenseRestricted ? 'RESTRICTED' : 'ACTIVE_ONLINE',
  };
  sandbox.window = sandbox;
  sandbox.Chart = function ChartStub() { return { destroy() {} }; };
  sandbox.Chart.getChart = () => null;

  vm.createContext(sandbox);
  vm.runInContext(o.source || SRC, sandbox, { filename: RETAIL_JS });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return { rs: sandbox.RetailSystem, calls, toasts, downloads, overlays, els, getEl };
}

async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

/** Render the screen through the ROUTER, so the route id is load-bearing --
 *  matches retail_branches_test.js's renderScreen() exactly. */
async function renderScreen(opts) {
  const ctx = loadRetailSystem(opts);
  const host = makeStub({ id: 'sub-content' });
  ctx.els['sub-content'] = host;
  await ctx.rs.render(SECTION_ID);
  await settle();
  ctx.frame = host.innerHTML;
  ctx.tbodyHtml = () => (ctx.els['bex-backup-tbody'] ? ctx.els['bex-backup-tbody'].innerHTML : '');
  return ctx;
}

function postsTo(calls, url) {
  return calls.filter((c) => c.method === 'POST' && c.url.indexOf(url) === 0);
}
function getsTo(calls, url) {
  return calls.filter((c) => c.method === 'GET' && c.url.indexOf(url) === 0);
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION HARNESS — same shape as retail_branches_test.js
// ─────────────────────────────────────────────────────────────────────────────

function mutate(src, pairs) {
  const nl = nlFor(src);
  let out = src;
  for (const [rawFind, rawReplace] of pairs) {
    const find = nl(rawFind);
    const replace = nl(rawReplace);
    const hits = out.split(find).length - 1;
    assert.strictEqual(
      hits, 1,
      `Mutation anchor occurs ${hits} time(s), expected exactly 1:\n  ${JSON.stringify(find)}\n\n` +
      'A mutation that no longer applies would let the proof below pass while proving nothing. Re-anchor it.'
    );
    out = out.replace(find, replace);
  }
  return out;
}

async function provesMutation(what, mutatedSrcOrShell, check) {
  let threw = null;
  try {
    await check(mutatedSrcOrShell);
  } catch (err) {
    threw = err;
  }
  assert.ok(
    threw,
    `MUTATION SURVIVED — ${what}\n` +
    'The guard for this passed against a build with the behaviour deliberately broken, so it is ' +
    'not actually watching it. Fix the check, not the mutation.'
  );
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 100)}]`;
}

// ─────────────────────────────────────────────────────────────────────────────
// (6) THE NAV ENTRY — static check against app-shell.js + the router case
// ─────────────────────────────────────────────────────────────────────────────

function assertNavEntryAndRouteOk(shellSrc, retailSrc) {
  const entry = (shellSrc || SHELL_SRC).split('\n').find((l) => l.indexOf(`id: '${SECTION_ID}'`) !== -1);
  assert.ok(entry, `app-shell.js has no nav entry for '${SECTION_ID}'.`);
  assert.ok(/ownerOnly:\s*true/.test(entry),
    `The '${SECTION_ID}' nav entry is not ownerOnly. backup/routes.py's _require_admin() reads ` +
    `session['mt_role'] -- the USER axis. Entry:\n  ${entry.trim()}`);
  assert.ok(!/adminOnly/.test(entry),
    `The '${SECTION_ID}' nav entry uses adminOnly (the DEVICE axis), which is the wrong one: the ` +
    `owner would lose this screen on any second terminal. Entry:\n  ${entry.trim()}`);
  assert.ok(/capability:\s*'retail\.reports'/.test(entry),
    `The '${SECTION_ID}' nav entry does not gate on retail.reports, but every CSV export route does ` +
    `(@mt_require_capability(CAP_REPORTS)). Entry:\n  ${entry.trim()}`);

  const start = (retailSrc || SRC).indexOf('  render(sectionId) {');
  assert.ok(start !== -1, 'Could not find RetailSystem.render(sectionId).');
  const body = (retailSrc || SRC).slice(start, (retailSrc || SRC).indexOf('\n  },', start));
  assert.ok(
    new RegExp(`case\\s+'${SECTION_ID}'\\s*:`).test(body),
    `RetailSystem.render() has no case for '${SECTION_ID}', so the screen is unreachable through the router.`
  );
}

function testNavEntryExistsAndPointsAtScreen() {
  assertNavEntryAndRouteOk(SHELL_SRC, SRC);
  // Sanity: the group config lists it too, so it actually renders in the
  // sidebar (retail_nav_groups_test.js pins the full arrangement; this is
  // just "did the id get lost between nav[] and navGroups[]").
  assert.ok(/items:\s*\[[^\]]*'backup-export'[^\]]*\]/.test(SHELL_SRC),
    "'backup-export' is in nav[] but not listed in any navGroups[] entry -- it would render nowhere in the sidebar.");
  console.log(`PASS: '${SECTION_ID}' is routed, listed in a nav group, and gates on both retail.reports and ownerOnly`);
}

// ─────────────────────────────────────────────────────────────────────────────
// (7) GATES — a viewer without retail.reports, OR without the store-owner
//     role, sees no screen; an UNRESOLVED role still renders (anti-vacuity)
// ─────────────────────────────────────────────────────────────────────────────

async function testGatesHideTheScreenFromNonOwners(src) {
  // No retail.reports at all -- the capability gate, matching every CSV
  // export route's @mt_require_capability(CAP_REPORTS). NO REQUEST: a screen
  // that fetches, collects a 403 and only then hides has already generated
  // the error it was supposed to prevent.
  const noCap = await renderScreen({ source: src, capabilities: [], role: 'admin', list: LIST_OK });
  assert.deepStrictEqual(noCap.calls, [],
    'The screen fetched before checking retail.reports. Calls:\n  ' +
    noCap.calls.map((c) => c.method + ' ' + c.url).join('\n  '));
  assert.ok(
    noCap.frame.indexOf('Backups and data exports are limited to managers and the store owner. Open the till to start ringing sales.') !== -1,
    `The capability-restricted message did not render for a viewer without retail.reports. Frame:\n${noCap.frame.slice(0, 300)}`
  );
  assert.ok(!/id="bex-backup-table"/.test(noCap.frame), 'The backups table rendered for a viewer without retail.reports.');
  assert.ok(!/_createBackup\(\)/.test(noCap.frame), 'The Create Backup control rendered for a viewer without retail.reports.');

  // retail.reports but NOT the store owner -- a manager. backup/routes.py's
  // four routes are ALL session-admin-only with no capability code at all,
  // so a manager reaching this screen would see a Create/Restore button
  // that 403s on every click -- the same click-through-to-a-403 bug the
  // Reports nav entry once had. NO REQUEST here either.
  const manager = await renderScreen({ source: src, capabilities: ['retail.reports'], role: 'manager', list: LIST_OK });
  assert.deepStrictEqual(manager.calls, [],
    'A non-admin role with retail.reports reached the request. The backup routes answer 403 for ' +
    'anyone but the store owner. Calls:\n  ' + manager.calls.map((c) => c.method + ' ' + c.url).join('\n  '));
  assert.ok(
    manager.frame.indexOf('Backups and restoring data are limited to the store owner. Open the till to start ringing sales.') !== -1,
    `The role-restricted message did not render for a manager. Frame:\n${manager.frame.slice(0, 300)}`
  );
  assert.ok(!/id="bex-backup-table"/.test(manager.frame), 'The backups table rendered for a manager who is not the store owner.');

  // ANTI-VACUITY, AND A REAL BEHAVIOUR: an UNRESOLVED session (role === '',
  // before /api/auth/session answers) must still render. A guard that
  // refused here would break this screen on every build that cannot answer
  // the question yet -- including this harness's OWN default, at which
  // point the two assertions above would pass even with both gates deleted.
  const unknown = await renderScreen({ source: src, capabilities: ['retail.reports'], role: '', list: LIST_OK });
  assert.ok(/id="bex-backup-table"/.test(unknown.frame),
    'The screen refused a session whose role has not resolved yet ("" is unresolved, not denied).');
  assert.strictEqual(getsTo(unknown.calls, BACKUP_LIST_URL).length, 1,
    'An unresolved-role session did not reach GET /api/backup/list.');

  console.log('PASS: a viewer without retail.reports, and a non-owner with it, both see the restricted ' +
    'panel with no request sent; an unresolved role still renders');
}

// ─────────────────────────────────────────────────────────────────────────────
// (1) THE HEADLINE — the screen works under a RESTRICTED licence
// ─────────────────────────────────────────────────────────────────────────────

async function testWorksUnderRestrictedLicence(src) {
  const ctx = await renderScreen({
    source: src, role: 'admin', capabilities: ['retail.reports'],
    licenseRestricted: true, list: LIST_OK, create: CREATE_OK, restore: RESTORE_OK,
  });

  // ANTI-VACUITY FIRST: prove the simulated session really IS under a
  // restricted licence, not an ordinary one that happens never to differ.
  // A NON-allowlisted mutating route is refused exactly the way a real
  // RESTRICTED-state backend refuses it.
  const refused = await ctx.rs._post(NON_ALLOWLISTED_URL, { name: 'x' });
  assert.strictEqual(refused.code, 'LICENSE_RESTRICTED',
    'Harness broken: a non-allowlisted route did not refuse under the simulated restricted licence, ' +
    'so nothing below proves anything about a restricted session.');

  // The screen itself: no restricted panel, the real screen, real data --
  // proving retail.backup.create/retail.backup.restore/retail.data.export
  // are genuinely reachable, not merely listed in RETAIL_RESTRICTED_ALLOWLIST
  // on paper.
  assert.ok(/id="bex-backup-table"/.test(ctx.frame),
    'The screen rendered its restricted panel under a restricted licence -- the exact promise ' +
    'licensing.js makes at this exact moment was broken.');
  assert.ok(ctx.tbodyHtml().indexOf(BACKUP_A.filename) !== -1,
    'Existing backups did not list under a restricted licence.');

  // Create a backup -- retail.backup.create is allowlisted, and the route
  // itself carries no license check at all.
  await ctx.rs._createBackup();
  await settle();
  assert.strictEqual(postsTo(ctx.calls, BACKUP_CREATE_URL).length, 1,
    'Creating a backup did not reach the server under a restricted licence.');
  assert.ok(ctx.toasts.some((t) => t.type === 'success'), 'No success toast after creating a backup under a restricted licence.');

  // Restore -- retail.backup.restore is allowlisted too. Goes through the
  // real two-step confirm/execute path, not a shortcut.
  ctx.rs._openRestoreConfirm(BACKUP_A.filename);
  await ctx.rs._confirmRestore(BACKUP_A.filename);
  await settle();
  const restorePosts = postsTo(ctx.calls, BACKUP_RESTORE_URL);
  assert.strictEqual(restorePosts.length, 1, 'Restoring a backup did not reach the server under a restricted licence.');
  assert.strictEqual(restorePosts[0].body.filename, BACKUP_A.filename, 'The restore POST did not carry the chosen filename.');

  // Export -- retail.data.export is allowlisted, and none of the three
  // export routes carry a license check either.
  ctx.getEl('bex-date-from').value = '2026-01-01';
  ctx.getEl('bex-date-to').value = '2026-01-31';
  ctx.rs._exportCsv('sales');
  ctx.rs._exportCsv('payments');
  ctx.rs._exportCsv('cash-sessions');
  assert.strictEqual(ctx.downloads.length, 3,
    `Not all three exports produced a download under a restricted licence. Downloads:\n  ` +
    ctx.downloads.join('\n  '));

  console.log('PASS: the screen renders, lists backups, creates a backup, restores one, and exports ' +
    'all three CSVs under a RESTRICTED licence -- licensing.js\'s promise holds');
}

// ─────────────────────────────────────────────────────────────────────────────
// (3) LIST — existing backups are listed from the list route
// ─────────────────────────────────────────────────────────────────────────────

async function testExistingBackupsAreListed(src) {
  const ctx = await renderScreen({ source: src, role: 'admin', list: LIST_OK });
  const tbody = ctx.tbodyHtml();
  assert.ok(tbody.indexOf(BACKUP_A.filename) !== -1 && tbody.indexOf(BACKUP_B.filename) !== -1,
    `Both fixture backup filenames are missing from the rendered table. Table body:\n${tbody}`);
  assert.ok(tbody.indexOf('2.00 MB') !== -1 && tbody.indexOf('3.00 MB') !== -1,
    `Backup sizes are not formatted into the row. Table body:\n${tbody}`);
  console.log('PASS: the screen lists existing backups returned by the list route, with size');
}

// ─────────────────────────────────────────────────────────────────────────────
// (2) CREATE — POSTs to /api/backup/create (the REQUEST, not a toast)
// ─────────────────────────────────────────────────────────────────────────────

async function testCreateBackupPostsToCreateRoute(src) {
  const ctx = await renderScreen({ source: src, role: 'admin', list: LIST_EMPTY, create: CREATE_OK });
  // Structural half first: the trigger a real operator would click must
  // actually be present in the RENDERED screen -- what makes the "render
  // nothing for everyone" mutation (M5) also fail THIS test, not just the
  // list test.
  assert.ok(/_createBackup\(\)/.test(ctx.frame), 'No "Create Backup" control found in the rendered screen.');

  await ctx.rs._createBackup();
  await settle();

  const posts = postsTo(ctx.calls, BACKUP_CREATE_URL);
  assert.strictEqual(posts.length, 1,
    `Expected exactly 1 POST to ${BACKUP_CREATE_URL}, saw ${posts.length}. Calls:\n` +
    JSON.stringify(ctx.calls, null, 2));
  assert.ok(ctx.toasts.some((t) => t.type === 'success'), 'No success toast after a successful create.');
  console.log('PASS: creating a backup POSTs to /api/backup/create');
}

// ─────────────────────────────────────────────────────────────────────────────
// (4) EXPORT — each of the three CSVs is reachable and hits its own route
// ─────────────────────────────────────────────────────────────────────────────

async function testEachExportHitsItsOwnDistinctRoute(src) {
  const ctx = await renderScreen({ source: src, role: 'admin', list: LIST_OK });
  assert.ok(
    /_exportCsv\('sales'\)/.test(ctx.frame) && /_exportCsv\('payments'\)/.test(ctx.frame) &&
    /_exportCsv\('cash-sessions'\)/.test(ctx.frame),
    'Not all three export controls are present in the rendered screen.'
  );

  ctx.getEl('bex-date-from').value = '2026-01-01';
  ctx.getEl('bex-date-to').value = '2026-01-31';
  ctx.rs._exportCsv('sales');
  ctx.rs._exportCsv('payments');
  ctx.rs._exportCsv('cash-sessions');

  assert.strictEqual(ctx.downloads.length, 3, `Expected 3 downloads, saw ${ctx.downloads.length}: ${JSON.stringify(ctx.downloads)}`);
  const routes = ctx.downloads.map((u) => u.split('?')[0]);
  assert.strictEqual(new Set(routes).size, 3,
    `The three exports did not hit three DISTINCT routes: ${JSON.stringify(routes)}`);
  assert.ok(routes.includes(EXPORT_SALES_URL), `Sales export did not hit ${EXPORT_SALES_URL}. Routes: ${JSON.stringify(routes)}`);
  assert.ok(routes.includes(EXPORT_PAYMENTS_URL), `Payments export did not hit ${EXPORT_PAYMENTS_URL}. Routes: ${JSON.stringify(routes)}`);
  assert.ok(routes.includes(EXPORT_CASH_SESSIONS_URL), `Cash-sessions export did not hit ${EXPORT_CASH_SESSIONS_URL}. Routes: ${JSON.stringify(routes)}`);
  assert.ok(
    ctx.downloads.every((u) => u.indexOf('date_from=2026-01-01') !== -1 && u.indexOf('date_to=2026-01-31') !== -1),
    `Not every export carried the chosen date range: ${JSON.stringify(ctx.downloads)}`
  );
  console.log('PASS: each of the three CSV exports hits its own distinct route, carrying the date range');
}

// ─────────────────────────────────────────────────────────────────────────────
// (5) RESTORE — requires an explicit confirmation, never a single click
// ─────────────────────────────────────────────────────────────────────────────

async function testRestoreRequiresExplicitConfirmation(src) {
  const ctx = await renderScreen({ source: src, role: 'admin', list: LIST_OK });
  // The Restore control is PER ROW, inside #bex-backup-tbody's own innerHTML
  // (a separate mocked node from `ctx.frame`, matching retail_branches_test.js's
  // identical tbody/frame split) -- not a static header control.
  assert.ok(/_openRestoreConfirm\(/.test(ctx.tbodyHtml()), 'No Restore control found in the rendered screen.');

  // The single click a real operator makes on the backups table.
  ctx.rs._openRestoreConfirm(BACKUP_A.filename);
  await settle();
  assert.strictEqual(postsTo(ctx.calls, BACKUP_RESTORE_URL).length, 0,
    'Restore reached the server from a single click, with no confirmation step. Calls:\n  ' +
    ctx.calls.map((c) => c.method + ' ' + c.url).join('\n  '));

  // The confirmation surface actually states the consequence in plain words
  // -- not merely "are you sure?".
  const overlay = ctx.overlays[ctx.overlays.length - 1];
  assert.ok(overlay, 'Clicking Restore did not open any confirmation surface.');
  assert.ok(
    overlay.innerHTML.indexOf('replaces every product, sale, customer and setting') !== -1,
    `The restore confirmation does not state what will happen in plain words. Overlay:\n${overlay.innerHTML.slice(0, 400)}`
  );

  // The SEPARATE confirm step -- a different function, wired to the modal's
  // own button, not the table row -- is what actually fires the request.
  await ctx.rs._confirmRestore(BACKUP_A.filename);
  await settle();
  const posts = postsTo(ctx.calls, BACKUP_RESTORE_URL);
  assert.strictEqual(posts.length, 1, `Expected exactly 1 POST to ${BACKUP_RESTORE_URL} after confirming, saw ${posts.length}.`);
  assert.strictEqual(posts[0].body.filename, BACKUP_A.filename, 'The restore POST did not carry the chosen filename.');

  console.log('PASS: restore requires a separate, explicit confirmation step and does not fire from a single click');
}

// ═════════════════════════════════════════════════════════════════════════════
// MAIN
// ═════════════════════════════════════════════════════════════════════════════

async function main() {
  const results = [];
  let failed = 0;

  async function run(name, fn) {
    try {
      const detail = await fn();
      results.push(`  ok   ${name}` + (detail ? `\n       ${detail}` : ''));
    } catch (err) {
      failed += 1;
      results.push(`  FAIL ${name}\n       ${(err && err.message) || err}`);
    }
  }

  // ── The seven required behaviours, against the REAL, unmutated build ──────
  await run('testNavEntryExistsAndPointsAtScreen', testNavEntryExistsAndPointsAtScreen);
  await run('testGatesHideTheScreenFromNonOwners', () => testGatesHideTheScreenFromNonOwners(SRC));
  await run('testWorksUnderRestrictedLicence', () => testWorksUnderRestrictedLicence(SRC));
  await run('testExistingBackupsAreListed', () => testExistingBackupsAreListed(SRC));
  await run('testCreateBackupPostsToCreateRoute', () => testCreateBackupPostsToCreateRoute(SRC));
  await run('testEachExportHitsItsOwnDistinctRoute', () => testEachExportHitsItsOwnDistinctRoute(SRC));
  await run('testRestoreRequiresExplicitConfirmation', () => testRestoreRequiresExplicitConfirmation(SRC));

  // ── Mutation proofs — M1-M5 from the ci-hardening-w0.3 brief, plus M6/M7
  //    (ENGINEERING.md: mutation-prove every guard, not only the named
  //    ones) ─────────────────────────────────────────────────────────────────

  // M1: remove the nav entry => testNavEntryExistsAndPointsAtScreen FAILS.
  await run('M1: nav entry removed => testNavEntryExistsAndPointsAtScreen FAILS', async () => {
    const brokenShell = mutate(SHELL_SRC, [[
      "        { id: 'backup-export', label: 'Backup & Export', icon: '💾', ownerOnly: true, capability: 'retail.reports' },\n",
      '',
    ]]);
    return provesMutation('M1 nav entry removed', null, () => assertNavEntryAndRouteOk(brokenShell, SRC));
  });

  // M2: hide the screen under a restricted licence => testWorksUnderRestrictedLicence FAILS.
  // There is no licence-state field in production code to key on today (see
  // _renderBackupExport's own comment on this), so this mutation ADDS a
  // plausible-but-wrong one, reading a property this harness sets regardless
  // (SubsystemApp.role/hasCapability are real; a hypothetical licenceState
  // check is exactly the kind of accidental regression this test exists to
  // catch if it is ever added).
  await run('M2: a licence-state gate is added => testWorksUnderRestrictedLicence FAILS', async () => {
    const broken = mutate(SRC, [[
      "  async _renderBackupExport(c) {\n    this._injectStyles();\n\n    // Capability gate: matches export_sales_csv / export_payments_csv /",
      "  async _renderBackupExport(c) {\n    this._injectStyles();\n    if (window.SubsystemApp && SubsystemApp.licenseState === 'RESTRICTED') { return this._renderCapabilityRestricted(c, { icon: '💾', title: t('Backup & Export'), message: t('Not available while your licence is restricted.') }); } // MUTATED: license-state gate added\n\n    // Capability gate: matches export_sales_csv / export_payments_csv /",
    ]]);
    return provesMutation('M2 licence-state gate added', broken, async (b) => {
      const ctx = await renderScreen({ source: b, role: 'admin', capabilities: ['retail.reports'], licenseRestricted: true, list: LIST_OK });
      assert.ok(/id="bex-backup-table"/.test(ctx.frame), 'expected the real screen, not the restricted panel');
    });
  });

  // M3: restore fires without confirmation => testRestoreRequiresExplicitConfirmation FAILS.
  await run('M3: restore fires on a single click => testRestoreRequiresExplicitConfirmation FAILS', async () => {
    const broken = mutate(SRC, [[
      "  _openRestoreConfirm(filename) {\n    const overlay = document.createElement('div');",
      "  _openRestoreConfirm(filename) {\n    this._confirmRestore(filename); // MUTATED: restore fires on a single click, no confirmation\n    const overlay = document.createElement('div');",
    ]]);
    return provesMutation('M3 restore fires without confirmation', broken, (b) => testRestoreRequiresExplicitConfirmation(b));
  });

  // M4: point two exports at the same route => testEachExportHitsItsOwnDistinctRoute FAILS.
  await run('M4: two exports share a route => testEachExportHitsItsOwnDistinctRoute FAILS', async () => {
    const broken = mutate(SRC, [[
      "    const routes = {\n      sales: '/api/sub/retail/reports/export/sales',\n      payments: '/api/sub/retail/reports/export/payments',\n      'cash-sessions': '/api/sub/retail/reports/export/cash-sessions',\n    };",
      "    const routes = {\n      sales: '/api/sub/retail/reports/export/sales',\n      payments: '/api/sub/retail/reports/export/sales', // MUTATED: same route as sales\n      'cash-sessions': '/api/sub/retail/reports/export/cash-sessions',\n    };",
    ]]);
    return provesMutation('M4 two exports share a route', broken, (b) => testEachExportHitsItsOwnDistinctRoute(b));
  });

  // M5: ALLOW HALF — render the screen empty for everyone. Tests (2), (3)
  // and (4) must FAIL. A screen that shows nothing passes every "does not
  // do the wrong thing" assertion (the gate tests would even look green).
  await run('M5: screen renders empty for everyone => tests (2), (3) and (4) FAIL', async () => {
    const broken = mutate(SRC, [[
      "    const today = new Date();\n    const monthAgo = new Date(today.getTime() - 29 * 24 * 60 * 60 * 1000);\n    const toISO = (d) => d.toISOString().slice(0, 10);",
      "    c.innerHTML = ''; return; // MUTATED: allow-half, empty for everyone\n    const today = new Date();\n    const monthAgo = new Date(today.getTime() - 29 * 24 * 60 * 60 * 1000);\n    const toISO = (d) => d.toISOString().slice(0, 10);",
    ]]);
    const a = await provesMutation('M5a create test fails on an empty screen', broken, (b) => testCreateBackupPostsToCreateRoute(b));
    const b2 = await provesMutation('M5b list test fails on an empty screen', broken, (b) => testExistingBackupsAreListed(b));
    const c2 = await provesMutation('M5c export test fails on an empty screen', broken, (b) => testEachExportHitsItsOwnDistinctRoute(b));
    return `${a}\n       ${b2}\n       ${c2}`;
  });

  // M6 (bonus, per ENGINEERING.md "mutation-prove every guard"): the
  // capability gate is short-circuited => testGatesHideTheScreenFromNonOwners FAILS.
  await run('M6 (bonus): capability gate disabled => testGatesHideTheScreenFromNonOwners FAILS', async () => {
    const broken = mutate(SRC, [[
      "    if (window.SubsystemApp && !SubsystemApp.hasCapability('retail.reports')) {\n" +
      "      return this._renderCapabilityRestricted(c, {\n" +
      "        icon: '💾',\n" +
      "        title: t('Backup & Export'),\n" +
      "        message: t('Backups and data exports are limited to managers and the store owner. Open the till to start ringing sales.'),\n" +
      "      });\n" +
      "    }",
      "    if (false) { // MUTATED: capability gate disabled\n" +
      "      return this._renderCapabilityRestricted(c, {\n" +
      "        icon: '💾',\n" +
      "        title: t('Backup & Export'),\n" +
      "        message: t('Backups and data exports are limited to managers and the store owner. Open the till to start ringing sales.'),\n" +
      "      });\n" +
      "    }",
    ]]);
    return provesMutation('M6 capability gate disabled', broken, (b) => testGatesHideTheScreenFromNonOwners(b));
  });

  // M7 (bonus): the role gate is short-circuited => testGatesHideTheScreenFromNonOwners FAILS.
  await run('M7 (bonus): role gate disabled => testGatesHideTheScreenFromNonOwners FAILS', async () => {
    const broken = mutate(SRC, [[
      "    if (window.SubsystemApp && SubsystemApp.role && SubsystemApp.role !== 'admin') {\n" +
      "      return this._renderCapabilityRestricted(c, {\n" +
      "        icon: '💾',\n" +
      "        title: t('Backup & Export'),\n" +
      "        message: t('Backups and restoring data are limited to the store owner. Open the till to start ringing sales.'),\n" +
      "      });\n" +
      "    }",
      "    if (false) { // MUTATED: role gate disabled\n" +
      "      return this._renderCapabilityRestricted(c, {\n" +
      "        icon: '💾',\n" +
      "        title: t('Backup & Export'),\n" +
      "        message: t('Backups and restoring data are limited to the store owner. Open the till to start ringing sales.'),\n" +
      "      });\n" +
      "    }",
    ]]);
    return provesMutation('M7 role gate disabled', broken, (b) => testGatesHideTheScreenFromNonOwners(b));
  });

  console.log(results.join('\n'));
  if (failed) {
    console.error(`\nFAIL: retail_backup_export_test.js — ${failed} of ${results.length} check(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`\nPASS: retail_backup_export_test.js — ${results.length} check(s)`);
  }
}

main().catch((err) => {
  console.error('FAIL: retail_backup_export_test.js (runner)');
  console.error(err && err.stack || err);
  process.exitCode = 1;
});
