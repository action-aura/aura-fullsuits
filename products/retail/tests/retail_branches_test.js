/**
 * retail_branches_test.js — Branches screen (ci-hardening-w0.3 continuation,
 * "the doorway").
 *
 * `POST /api/sub/retail/branches` (retail_api.py:7894, create_branch) has
 * been complete, gated (CAP_EMPLOYEES + licence guard) and correct since
 * Phase 5 wave A. Nothing in the frontend ever called it: zero POST-to-
 * branches anywhere in products/retail/frontend/. Every install therefore
 * self-healed exactly one branch (_default_branch) and a shop had no way to
 * add a second, ever -- with an entire multi-store programme already shipped
 * on top of a second branch existing (device->branch pinning dc22b04,
 * branch-scoped accounts ab6b3c1, branch managers 93cef8c, the head-office
 * comparison chart), all real, tested, and unreachable.
 *
 * THIS FILE'S ONE JOB: prove the doorway actually opens, and prove it by
 * running the real code, not by reading the diff. Four things specifically:
 *
 *   1. The screen lists branches returned by the API.
 *   2. Creating one POSTs to /api/sub/retail/branches with the typed name --
 *      the entire defect being fixed is that this call never happened, so
 *      the assertion is on the REQUEST, not just on a success toast.
 *   3. A blank name is refused client-side, before any network round trip.
 *   4. THE NAV ENTRY EXISTS and points at the screen -- the regression that
 *      actually matters here (four features have now shipped unreachable on
 *      this branch).
 *   5. A user without CAP_EMPLOYEES does not see the screen (matching
 *      create_branch's own @mt_require_capability(CAP_EMPLOYEES) decorator).
 *   6. The pin-your-tills guidance appears once more than one branch exists,
 *      and NOT when there is only one.
 *
 * UPDATE (2026-09-06): PUT /branches/<id> (update_branch, retail_api.py) now
 * exists too -- rename / fix address / fix phone, name/address/phone ONLY.
 * Covered below as (7) and (8): the rendered Edit control, and that saving
 * the edit modal for an existing branch PUTs rather than POSTs. Deactivate
 * is still NOT built -- a branch carries stock balances, sale history,
 * scoped user accounts and pinned tills, so "what happens to those on
 * retirement" remains a real design question, not a CRUD gap, and is
 * intentionally out of scope for this route and this file.
 *
 * ── MUTATION-PROVED ─────────────────────────────────────────────────────────
 * Every behavioural claim below is re-run against a DELIBERATELY BROKEN copy
 * of subsystem-retail.js (or app-shell.js, for the nav entry) and is required
 * to FAIL there -- including an "allow-half" mutation that renders nothing at
 * all, which would otherwise pass every "does not show the wrong thing" check
 * by showing nothing (ENGINEERING.md's failure-shape #4/#5: a guard that
 * denies everything passes every deny test and proves nothing about the
 * allow half).
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_branches_test.js
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
   while breaking nothing (see retail_exceptions_screen_test.js's identical
   comment on this exact hazard). */
function eolOf(src) { return src.indexOf('\r\n') !== -1 ? '\r\n' : '\n'; }
function nlFor(src) { const eol = eolOf(src); return (s) => s.replace(/\n/g, eol); }

const SECTION_ID = 'branches';
const BRANCHES_URL = '/api/sub/retail/branches';

// ─────────────────────────────────────────────────────────────────────────────
// FIXTURES — shapes list_branches/create_branch actually put on the wire
// ─────────────────────────────────────────────────────────────────────────────

const BRANCH_MAIN = { id: 1, company_id: 1, name: 'Main Branch', address: '', phone: '', status: 'active', uid: 'br-1' };
const BRANCH_DOWNTOWN = { id: 2, company_id: 1, name: 'Downtown Branch', address: '12 King St', phone: '0791234567', status: 'active', uid: 'br-2' };

const ONE_BRANCH_OK = { status: 'success', data: [BRANCH_MAIN] };
const TWO_BRANCHES_OK = { status: 'success', data: [BRANCH_MAIN, BRANCH_DOWNTOWN] };
const EMPTY_OK = { status: 'success', data: [] };
const CREATE_OK = { status: 'success', data: { id: 3 } };
const UPDATE_OK = { status: 'success', data: { id: 2 } };

// ─────────────────────────────────────────────────────────────────────────────
// THE SANDBOX — the real subsystem-retail.js, never a reimplementation
// ─────────────────────────────────────────────────────────────────────────────

function makeStub(over) {
  return Object.assign({
    innerHTML: '', outerHTML: '', textContent: '', value: '', id: '', disabled: false,
    style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    addEventListener() {}, removeEventListener() {},
    focus() {}, blur() {}, remove() {}, closest() { return null; },
  }, over || {});
}

/**
 * @param {object} opts
 *   source       — subsystem-retail.js text (a mutant, for the proofs below)
 *   capabilities — what SubsystemApp.hasCapability answers yes to
 *   list         — the GET /api/sub/retail/branches payload
 *   create       — the POST /api/sub/retail/branches payload
 */
function loadRetailSystem(opts) {
  const o = opts || {};
  const calls = [];
  const toasts = [];
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
      let payload;
      if (u.indexOf(BRANCHES_URL) === 0 && method === 'GET') {
        payload = o.list === undefined ? EMPTY_OK : o.list;
      } else if (u.indexOf(BRANCHES_URL) === 0 && method === 'POST') {
        payload = o.create === undefined ? CREATE_OK : o.create;
      } else if (u.indexOf(BRANCHES_URL) === 0 && method === 'PUT') {
        payload = o.update === undefined ? UPDATE_OK : o.update;
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
      // _loadBranches reads its table body via querySelector (matching
      // _loadCategories/_loadSuppliers' own convention), so THAT selector
      // specifically is routed to a tracked stub; everything else gets a
      // throwaway one, same as retail_exceptions_screen_test.js's document mock.
      querySelector(sel) {
        if (sel === '#branch-table tbody') return getEl('branch-table-tbody');
        return makeStub();
      },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
  };
  sandbox.SubsystemApp = {
    active: 'retail',
    showToast(msg, type) { toasts.push({ msg, type }); },
    checkAuthAndSetup() {},
    _navigate() {},
    role: 'admin',
    hasCapability: (code) => (o.capabilities || ['retail.employees']).includes(code),
  };
  sandbox.window = sandbox;
  sandbox.Chart = function ChartStub() { return { destroy() {} }; };
  sandbox.Chart.getChart = () => null;

  vm.createContext(sandbox);
  vm.runInContext(o.source || SRC, sandbox, { filename: RETAIL_JS });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return { rs: sandbox.RetailSystem, calls, toasts, els, getEl };
}

async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

/** Render the screen through the ROUTER, so the route id is load-bearing --
 *  matches retail_exceptions_screen_test.js's renderScreen() exactly. */
async function renderScreen(opts) {
  const ctx = loadRetailSystem(opts);
  const host = makeStub({ id: 'sub-content' });
  ctx.els['sub-content'] = host;
  await ctx.rs.render(SECTION_ID);
  await settle();
  ctx.frame = host.innerHTML;
  ctx.tbodyHtml = () => (ctx.els['branch-table-tbody'] ? ctx.els['branch-table-tbody'].innerHTML : '');
  ctx.noticeHtml = () => (ctx.els['branches-pin-notice'] ? ctx.els['branches-pin-notice'].innerHTML : '');
  return ctx;
}

function postsTo(calls, url) {
  return calls.filter((c) => c.method === 'POST' && c.url.indexOf(url) === 0);
}

function putsTo(calls, url) {
  return calls.filter((c) => c.method === 'PUT' && c.url.indexOf(url) === 0);
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION HARNESS — same shape as retail_exceptions_screen_test.js
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
// (4) THE NAV ENTRY — static check against app-shell.js + the router case
// ─────────────────────────────────────────────────────────────────────────────

function assertNavEntryAndRouteOk(shellSrc, retailSrc) {
  const entry = (shellSrc || SHELL_SRC).split('\n').find((l) => l.indexOf(`id: '${SECTION_ID}'`) !== -1);
  assert.ok(entry, `app-shell.js has no nav entry for '${SECTION_ID}'.`);
  assert.ok(/capability:\s*'retail\.employees'/.test(entry),
    `The '${SECTION_ID}' nav entry does not gate on retail.employees, but create_branch carries ` +
    `@mt_require_capability(CAP_EMPLOYEES). Entry:\n  ${entry.trim()}`);

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
  assert.ok(/items:\s*\[[^\]]*'branches'[^\]]*\]/.test(SHELL_SRC),
    "'branches' is in nav[] but not listed in any navGroups[] entry -- it would render nowhere in the sidebar.");
  console.log(`PASS: '${SECTION_ID}' is routed, listed in a nav group, and gates on retail.employees`);
}

// ─────────────────────────────────────────────────────────────────────────────
// (5) CAPABILITY GATE — a viewer without retail.employees sees no screen
// ─────────────────────────────────────────────────────────────────────────────

async function testCapabilityGateHidesScreenFromNonEmployee(src) {
  const ctx = await renderScreen({ source: src, capabilities: ['retail.sell'], list: TWO_BRANCHES_OK });
  // POSITIVE assertion first: the restricted panel actually rendered (not
  // just "the table is missing", which an empty-render mutation would also
  // satisfy vacuously).
  assert.ok(
    ctx.frame.indexOf('Managing branches is limited to managers and the store owner. Open the till to start ringing sales.') !== -1,
    `The capability-restricted message did not render for a viewer without retail.employees. Frame:\n${ctx.frame.slice(0, 300)}`
  );
  assert.ok(!/id="branch-table"/.test(ctx.frame),
    'The branches table rendered for a viewer without retail.employees.');
  assert.ok(!/_openAddBranch\(\)/.test(ctx.frame),
    'The "+ Add Branch" control rendered for a viewer without retail.employees.');
  console.log('PASS: a viewer without retail.employees sees the restricted panel, not the branches screen');
}

// ─────────────────────────────────────────────────────────────────────────────
// (1) LIST — the screen lists branches returned by the API
// ─────────────────────────────────────────────────────────────────────────────

async function testScreenListsBranchesFromAPI(src) {
  const ctx = await renderScreen({ source: src, list: TWO_BRANCHES_OK });
  const tbody = ctx.tbodyHtml();
  assert.ok(tbody.indexOf('Main Branch') !== -1 && tbody.indexOf('Downtown Branch') !== -1,
    `Both fixture branch names are missing from the rendered table. Table body:\n${tbody}`);
  assert.ok(tbody.indexOf('12 King St') !== -1 && tbody.indexOf('0791234567') !== -1,
    `Downtown Branch's address/phone are missing from the rendered row. Table body:\n${tbody}`);
  console.log('PASS: the screen lists branches returned by the API');
}

// ─────────────────────────────────────────────────────────────────────────────
// (2) CREATE — POSTs to /api/sub/retail/branches with the typed name
// ─────────────────────────────────────────────────────────────────────────────

async function testCreatePostsWithTypedName(src) {
  const ctx = await renderScreen({ source: src, list: EMPTY_OK, create: CREATE_OK });
  // Structural half: the trigger a real operator would click must actually
  // be present in the RENDERED screen -- not merely callable as a method.
  // This is what makes the "render nothing for everyone" mutation (M4) also
  // fail THIS test, not just the list test.
  assert.ok(/_openAddBranch\(\)/.test(ctx.frame),
    'No "+ Add Branch" control found in the rendered screen.');

  ctx.rs._openAddBranch();
  ctx.getEl('brm-name').value = 'Downtown Branch';
  ctx.getEl('brm-address').value = '12 King St';
  ctx.getEl('brm-phone').value = '0791234567';
  await ctx.rs._saveBranch();
  await settle();

  const posts = postsTo(ctx.calls, BRANCHES_URL);
  assert.strictEqual(posts.length, 1,
    `Expected exactly 1 POST to ${BRANCHES_URL}, saw ${posts.length}. Calls:\n` +
    JSON.stringify(ctx.calls, null, 2));
  assert.strictEqual(posts[0].body.name, 'Downtown Branch',
    `The POST body's name did not carry the typed value. Body: ${JSON.stringify(posts[0].body)}`);
  console.log('PASS: creating a branch POSTs to /api/sub/retail/branches with the typed name');
}

// ─────────────────────────────────────────────────────────────────────────────
// (3) BLANK NAME — refused client-side, no network round trip
// ─────────────────────────────────────────────────────────────────────────────

async function testBlankNameRefusedClientSide(src) {
  const ctx = await renderScreen({ source: src, list: EMPTY_OK, create: CREATE_OK });
  ctx.rs._openAddBranch();
  ctx.getEl('brm-name').value = '   '; // whitespace-only, same as an empty field after .trim()
  await ctx.rs._saveBranch();
  await settle();

  const posts = postsTo(ctx.calls, BRANCHES_URL);
  assert.strictEqual(posts.length, 0,
    `A blank name reached the network -- create_branch would 400 on this instead of the client ` +
    `refusing it up front. Calls:\n${JSON.stringify(ctx.calls, null, 2)}`);
  assert.ok(ctx.toasts.some((t) => t.type === 'error'),
    'No error toast was shown when the name field was left blank.');
  console.log('PASS: a blank name is refused client-side with no network call');
}

// ─────────────────────────────────────────────────────────────────────────────
// (6) PIN GUIDANCE — appears with >1 branch, absent with exactly 1
// ─────────────────────────────────────────────────────────────────────────────

async function testPinGuidanceOnlyWithMultipleBranches(src) {
  const GUIDANCE = "Every till now needs its own branch pin, or its sales silently file under your first branch";

  const two = await renderScreen({ source: src, list: TWO_BRANCHES_OK });
  assert.ok(two.noticeHtml().indexOf(GUIDANCE) !== -1,
    `The pin-your-tills guidance did not appear with two branches. Notice:\n${two.noticeHtml()}`);

  const one = await renderScreen({ source: src, list: ONE_BRANCH_OK });
  assert.strictEqual(one.noticeHtml(), '',
    `The pin-your-tills guidance appeared for a single-branch shop, which has no second branch to ` +
    `pin against and must not be nagged about a concept it does not have. Notice:\n${one.noticeHtml()}`);

  console.log('PASS: the pin-your-tills guidance appears only once more than one branch exists');
}

// ─────────────────────────────────────────────────────────────────────────────
// (7) EDIT CONTROL — every rendered row carries its own Edit button
// ─────────────────────────────────────────────────────────────────────────────

async function testEditControlRenderedPerBranch(src) {
  const ctx = await renderScreen({ source: src, list: TWO_BRANCHES_OK });
  const tbody = ctx.tbodyHtml();
  const editCalls = tbody.match(/_openEditBranch\('[^']+'\)/g) || [];
  assert.strictEqual(editCalls.length, 2,
    `Expected exactly 2 Edit controls (one per fixture branch), found ${editCalls.length}. Table body:\n${tbody}`);
  assert.ok(tbody.indexOf(`_openEditBranch('${BRANCH_MAIN.id}')`) !== -1,
    `No Edit control found for branch id ${BRANCH_MAIN.id}. Table body:\n${tbody}`);
  assert.ok(tbody.indexOf(`_openEditBranch('${BRANCH_DOWNTOWN.id}')`) !== -1,
    `No Edit control found for branch id ${BRANCH_DOWNTOWN.id}. Table body:\n${tbody}`);
  console.log('PASS: each rendered branch row carries its own Edit control');
}

// ─────────────────────────────────────────────────────────────────────────────
// (8) EDIT SAVE — PUTs to /api/sub/retail/branches/<id> with the typed fields
// ─────────────────────────────────────────────────────────────────────────────

async function testEditSavePutsToBranchIdWithTypedFields(src) {
  const ctx = await renderScreen({ source: src, list: TWO_BRANCHES_OK, update: UPDATE_OK });
  // Through the same entry point the rendered Edit button calls -- resolves
  // the record from this._branches, same as _openEditCategory.
  ctx.rs._openEditBranch(BRANCH_DOWNTOWN.id);
  ctx.getEl('brm-name').value = 'Downtown Renamed';
  ctx.getEl('brm-address').value = '99 New St';
  ctx.getEl('brm-phone').value = '0700000099';
  await ctx.rs._saveBranch(BRANCH_DOWNTOWN.id);
  await settle();

  const url = `${BRANCHES_URL}/${BRANCH_DOWNTOWN.id}`;
  const puts = putsTo(ctx.calls, url);
  assert.strictEqual(puts.length, 1,
    `Expected exactly 1 PUT to ${url}, saw ${puts.length}. Calls:\n${JSON.stringify(ctx.calls, null, 2)}`);
  assert.strictEqual(puts[0].body.name, 'Downtown Renamed',
    `The PUT body's name did not carry the typed value. Body: ${JSON.stringify(puts[0].body)}`);
  assert.strictEqual(puts[0].body.address, '99 New St');
  assert.strictEqual(puts[0].body.phone, '0700000099');

  // Allow-half: editing an existing branch must not ALSO create a new one.
  assert.strictEqual(postsTo(ctx.calls, BRANCHES_URL).length, 0,
    `Editing an existing branch also POSTed a new one. Calls:\n${JSON.stringify(ctx.calls, null, 2)}`);
  console.log('PASS: saving the edit modal for an existing branch PUTs to /api/sub/retail/branches/<id> with the typed fields');
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

  // ── The six required behaviours, against the REAL, unmutated build ────────
  await run('testNavEntryExistsAndPointsAtScreen', testNavEntryExistsAndPointsAtScreen);
  await run('testCapabilityGateHidesScreenFromNonEmployee', () => testCapabilityGateHidesScreenFromNonEmployee(SRC));
  await run('testScreenListsBranchesFromAPI', () => testScreenListsBranchesFromAPI(SRC));
  await run('testCreatePostsWithTypedName', () => testCreatePostsWithTypedName(SRC));
  await run('testBlankNameRefusedClientSide', () => testBlankNameRefusedClientSide(SRC));
  await run('testPinGuidanceOnlyWithMultipleBranches', () => testPinGuidanceOnlyWithMultipleBranches(SRC));
  await run('testEditControlRenderedPerBranch', () => testEditControlRenderedPerBranch(SRC));
  await run('testEditSavePutsToBranchIdWithTypedFields', () => testEditSavePutsToBranchIdWithTypedFields(SRC));

  // ── Mutation proofs — M1-M4 from the ci-hardening-w0.3 brief, plus M5/M6
  //    (ENGINEERING.md: mutation-prove every guard, not only the four named
  //    ones) ─────────────────────────────────────────────────────────────────

  // M1: remove the nav entry => test (4) FAILS.
  await run('M1: nav entry removed => testNavEntryExistsAndPointsAtScreen FAILS', async () => {
    const brokenShell = mutate(SHELL_SRC, [[
      "        { id: 'branches',     label: 'Branches',        icon: '🏦', capability: 'retail.employees' },\n",
      '',
    ]]);
    return provesMutation('M1 nav entry removed', null, () => assertNavEntryAndRouteOk(brokenShell, SRC));
  });

  // M2: make create() not POST => test (2) FAILS.
  await run('M2: create() stops POSTing => testCreatePostsWithTypedName FAILS', async () => {
    const broken = mutate(SRC, [[
      "        : await this._post('/api/sub/retail/branches', payload);",
      "        : { status: 'success', data: { id: 999 } }; // MUTATED: no network call",
    ]]);
    return provesMutation('M2 create() does not POST', broken, (b) => testCreatePostsWithTypedName(b));
  });

  // M3: show the pin guidance with one branch => test (6) FAILS.
  await run('M3: pin guidance shown with one branch => testPinGuidanceOnlyWithMultipleBranches FAILS', async () => {
    const broken = mutate(SRC, [[
      '        notice.innerHTML = this._branches.length > 1',
      '        notice.innerHTML = this._branches.length >= 1 // MUTATED: nags a single-branch shop too',
    ]]);
    return provesMutation('M3 pin guidance shown with one branch', broken, (b) => testPinGuidanceOnlyWithMultipleBranches(b));
  });

  // M4: ALLOW HALF — render the screen empty for everyone. Tests (1) and (2)
  // must FAIL. A screen that shows nothing passes every "does not show the
  // wrong thing" assertion (the capability-gate test would even look green).
  await run('M4: screen renders empty for everyone => tests (1) and (2) FAIL', async () => {
    const broken = mutate(SRC, [[
      '  async _renderBranches(c) {\n    this._injectStyles();',
      "  async _renderBranches(c) {\n    this._injectStyles();\n    c.innerHTML = ''; return; // MUTATED: allow-half, empty for everyone",
    ]]);
    const a = await provesMutation('M4a list test fails on an empty screen', broken, (b) => testScreenListsBranchesFromAPI(b));
    const b2 = await provesMutation('M4b create test fails on an empty screen', broken, (b) => testCreatePostsWithTypedName(b));
    return `${a}\n       ${b2}`;
  });

  // M5 (beyond the brief's four, per ENGINEERING.md "mutation-prove every
  // guard"): remove the blank-name guard => test (3) FAILS.
  await run('M5 (bonus): blank-name guard removed => testBlankNameRefusedClientSide FAILS', async () => {
    const broken = mutate(SRC, [[
      "    const name = document.getElementById('brm-name')?.value.trim();\n" +
      "    if (!name) { SubsystemApp.showToast(t('Name required'), 'error'); return; }",
      "    const name = document.getElementById('brm-name')?.value.trim();\n" +
      "    // MUTATED: blank-name guard removed",
    ]]);
    return provesMutation('M5 blank-name guard removed', broken, (b) => testBlankNameRefusedClientSide(b));
  });

  // M6 (beyond the brief's four, per ENGINEERING.md): the capability-gate
  // condition is short-circuited => test (5) FAILS.
  await run('M6 (bonus): capability gate short-circuited => testCapabilityGateHidesScreenFromNonEmployee FAILS', async () => {
    const broken = mutate(SRC, [[
      "    if (window.SubsystemApp && !SubsystemApp.hasCapability('retail.employees')) {",
      '    if (false) { // MUTATED: capability gate disabled',
    ]]);
    return provesMutation('M6 capability gate disabled', broken, (b) => testCapabilityGateHidesScreenFromNonEmployee(b));
  });

  // M7 (2026-09-06, PUT /branches/<id>): make the edit path fall through to
  // POST instead of PUT => test (8) FAILS. The allow/deny counterpart of M2:
  // M2 proves create still hits the network, this proves EDIT hits the
  // RIGHT verb/URL rather than silently creating a duplicate branch.
  await run('M7: edit path POSTs instead of PUTing => testEditSavePutsToBranchIdWithTypedFields FAILS', async () => {
    const broken = mutate(SRC, [[
      "      const d = branchId\n        ? await this._put(`/api/sub/retail/branches/${branchId}`, payload)\n        : await this._post('/api/sub/retail/branches', payload);",
      "      const d = await this._post('/api/sub/retail/branches', payload); // MUTATED: edit falls through to create",
    ]]);
    return provesMutation('M7 edit path POSTs instead of PUTing', broken, (b) => testEditSavePutsToBranchIdWithTypedFields(b));
  });

  console.log(results.join('\n'));
  if (failed) {
    console.error(`\nFAIL: retail_branches_test.js — ${failed} of ${results.length} check(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`\nPASS: retail_branches_test.js — ${results.length} check(s)`);
  }
}

main().catch((err) => {
  console.error('FAIL: retail_branches_test.js (runner)');
  console.error(err && err.stack || err);
  process.exitCode = 1;
});
