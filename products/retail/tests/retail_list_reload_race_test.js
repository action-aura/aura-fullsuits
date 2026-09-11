/**
 * retail_list_reload_race_test.js — two SAME-SCREEN reload races that the
 * Dashboard's render-generation fix (retail_render_race_test.js) deliberately
 * did not cover, because they are a different question.
 *
 * BACKGROUND: RetailSystem._renderGeneration / _beginRender() / _isStaleRender()
 * (subsystem-retail.js, "Render generation guard" above the Router) answer
 * "did the user navigate to a different SCREEN while this fetch was in
 * flight". That guard fixed a real bug on the Dashboard, and
 * retail_render_race_test.js's own audit note says every OTHER render/load
 * in the file was checked against that exact crash-and-cross-navigation
 * shape and found not to reproduce it. That audit was correct as far as it
 * went -- but it never asked a narrower question: can the SAME list, on the
 * SAME still-open screen, be asked to reload TWICE before the first reload's
 * response lands? The navigation generation never changes in that case, so
 * _isStaleRender would report "not stale" even while an OLDER response is
 * about to overwrite a NEWER one.
 *
 * THE TWO RACES this file drives deterministically:
 *
 *   1. _loadAuditLog -- pagination. Click page 2, then page 3, before page
 *      2's response lands. If page 2 arrives after page 3, the table shows
 *      page 2's rows while the pager and summary line both say "Page 3".
 *
 *   2. _loadSalesHistory -- the search/date-filter reload. Type a filter,
 *      change it again before the first settles, and an older result set
 *      can overwrite a newer one. On this screen those rows are money.
 *
 * THE FIX: a per-operation sequence, separate from the navigation counter --
 * see "Per-operation reload guard" above the Router in subsystem-retail.js.
 * `_beginOp(name)` bumps and returns a NAMED counter (`_opSeq[name]`);
 * `_isStaleOp(name, token)` compares a captured token against the CURRENT
 * value for that name. Both _loadAuditLog and _loadSalesHistory capture a
 * token at the very start of the call and, right after their fetch resolves
 * -- before any DOM write, including the error branch -- compare it and
 * return immediately if superseded. Same genuine-early-return contract as
 * _isStaleRender: never a try/catch.
 *
 * This file also proves the two guards are INDEPENDENT: _beginOp/_isStaleOp
 * track their own state (`_opSeq`), entirely separate from `_renderGeneration`,
 * so navigating around cannot invalidate an operation token and re-firing an
 * operation cannot invalidate a render token. That is the property that
 * stops someone later "simplifying" the two guards into a single counter.
 *
 * AUDIT NOTE (retail-hardware-viewports, this file's own task): every other
 * `_load*` function in subsystem-retail.js that can be re-fired on the SAME
 * screen (pagination, search, filter, tab switch, refresh button) was read
 * against this exact shape. The large majority are mutation-triggered only
 * (a Save/Delete/Accept/Decline button behind its own confirm dialog and a
 * full-list refetch with no page/filter indicator to contradict) and are not
 * fixed here -- see this task's final report for the complete list of sites
 * examined and judged safe, with the reason for each. A few OTHER sites
 * (_loadCustomers' debounced search, _loadReports' branch/day filters,
 * _loadStockAccuracy and the three Exceptions-queue loaders' un-disabled
 * "try again" buttons) were found to share this exact defect shape but are
 * NOT fixed by this file -- fixing them was judged to be widening this
 * task's explicitly scoped two races, and they are reported as follow-up
 * candidates instead of being silently patched or silently left undocumented.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins,
 * modelled on retail_render_race_test.js's hand-resolved-promise harness and
 * the loadRetailSystem() vm-sandbox pattern used across this directory:
 *
 *   node products/retail/tests/retail_list_reload_race_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const RETAIL_JS = path.join(FRONTEND, 'subsystem-retail.js');
const SRC = fs.readFileSync(RETAIL_JS, 'utf8');

// Match the file's OWN line ending -- an anchor written with the wrong one
// would silently match nothing, making a mutation proof below pass while
// proving nothing (see retail_render_race_test.js / retail_branches_test.js's
// identical comment).
function eolOf(src) { return src.indexOf('\r\n') !== -1 ? '\r\n' : '\n'; }
function nlFor(src) { const eol = eolOf(src); return (s) => s.replace(/\n/g, eol); }

// ─────────────────────────────────────────────────────────────────────────────
// FIXTURES — clearly-distinguishable payloads per screen. The "STALE"/"A"
// fixtures are poison: if their content ever appears on screen after the
// "CURRENT"/"B" fixture already painted, the race was lost.
// ─────────────────────────────────────────────────────────────────────────────

const AUDIT_PAGE2_RESPONSE = {
  status: 'success',
  data: [{
    timestamp: '2026-09-01 10:00:00', user_id: 'user-page2', action: 'STALE_ACTION',
    entity: 'stale_entity', entity_id: 2,
    details: 'STALE PAGE 2 ROW -- must never appear after page 3 has painted',
  }],
  meta: { actions: ['STALE_ACTION'], entities: ['stale_entity'], total: 120, limit: 50, page: 2 },
};

const AUDIT_PAGE3_RESPONSE = {
  status: 'success',
  data: [{
    timestamp: '2026-09-01 11:00:00', user_id: 'user-page3', action: 'CURRENT_ACTION',
    entity: 'current_entity', entity_id: 3,
    details: 'CURRENT PAGE 3 ROW',
  }],
  meta: { actions: ['CURRENT_ACTION'], entities: ['current_entity'], total: 120, limit: 50, page: 3 },
};

const AUDIT_NORMAL_RESPONSE = {
  status: 'success',
  data: [{
    timestamp: '2026-09-01 09:00:00', user_id: 'user-normal', action: 'NORMAL_ACTION',
    entity: 'normal_entity', entity_id: 1,
    details: 'NORMAL SINGLE-LOAD ROW',
  }],
  meta: { actions: ['NORMAL_ACTION'], entities: ['normal_entity'], total: 1, limit: 50, page: 1 },
};

const SALES_FILTER_A_RESPONSE = {
  status: 'success',
  data: [{
    id: 501, sale_number: 'S-STALE-A', created_at: '2026-09-01 08:00:00',
    customer_name: 'Stale Customer A', item_count: 1, payment_method: 'cash',
    total: 999999, status: 'completed',
  }],
};

const SALES_FILTER_B_RESPONSE = {
  status: 'success',
  data: [{
    id: 502, sale_number: 'S-CURRENT-B', created_at: '2026-09-01 09:00:00',
    customer_name: 'Current Customer B', item_count: 2, payment_method: 'card',
    total: 42, status: 'completed',
  }],
};

const SALES_NORMAL_RESPONSE = {
  status: 'success',
  data: [{
    id: 601, sale_number: 'S-NORMAL', created_at: '2026-09-01 07:00:00',
    customer_name: 'Normal Customer', item_count: 3, payment_method: 'cash',
    total: 15, status: 'completed',
  }],
};

// ─────────────────────────────────────────────────────────────────────────────
// THE SANDBOX — the real subsystem-retail.js in a vm context, never a
// reimplementation. Modelled directly on retail_render_race_test.js's
// loadRetailSystem(): every fetch() call is deferred and resolved by hand,
// in whatever order the test chooses, so the race is deterministic instead
// of timing-dependent.
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

function loadRetailSystem(source) {
  const els = Object.create(null);
  const getEl = (id) => (els[id] || (els[id] = makeStub({ id })));
  const fetchCalls = []; // { url, resolve(payload, status) }

  const sandbox = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    t: (s) => s, // identity stub -- this file checks WRITE-ORDERING behaviour, not catalogs
    // Both _loadAuditLog and _loadSalesHistory build their query string with
    // URLSearchParams -- a Node global, but a vm.createContext sandbox gets
    // none of the host's globals for free, so it must be handed in explicitly.
    URLSearchParams,
    fetch: (url) => {
      let settleFn;
      const promise = new Promise((resolve) => { settleFn = resolve; });
      fetchCalls.push({
        url: String(url),
        resolve(payload, status) {
          settleFn({
            ok: (status || 200) < 400,
            status: status || 200,
            json: () => Promise.resolve(payload),
          });
        },
      });
      return promise;
    },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    document: {
      activeElement: null,
      getElementById(id) { if (id === 'ret-styles') return null; return getEl(id); },
      createElement() { return makeStub({}); },
      // Both _loadAuditLog and _loadSalesHistory read their table body via
      // querySelector (`#aud-table tbody` / `#sh-table tbody`), matching
      // _renderDashboard's own `#r-dash-recent tbody` routing in
      // retail_render_race_test.js -- those two selectors specifically are
      // routed to STABLE, tracked stubs (same identity on every call) so a
      // later assertion reads the same object the code wrote into; anything
      // else gets a throwaway stub.
      querySelector(sel) {
        if (sel === '#aud-table tbody') return getEl('aud-table-tbody');
        if (sel === '#sh-table tbody') return getEl('sh-table-tbody');
        return makeStub({});
      },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
  };
  sandbox.window = sandbox; // enough for the `window.SubsystemApp` refs used here
  // window.SubsystemApp is deliberately left undefined, matching
  // retail_render_race_test.js's own reasoning: _mayBrowseTheSalesBook()
  // fails OPEN with no shell present ("the same contract as every other
  // capability check in this file"), so Sales History's date-range inputs
  // are exercised too, not skipped.

  vm.createContext(sandbox);
  vm.runInContext(source || SRC, sandbox, { filename: RETAIL_JS });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return { rs: sandbox.RetailSystem, fetchCalls, getEl };
}

async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION HARNESS — same shape as retail_render_race_test.js
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

async function provesMutation(what, mutatedSrc, check) {
  let threw = null;
  try {
    await check(mutatedSrc);
  } catch (err) {
    threw = err;
  }
  assert.ok(
    threw,
    `MUTATION SURVIVED — ${what}\n` +
    'The guard for this passed against a build with the behaviour deliberately broken, so it is ' +
    'not actually watching it. Fix the check, not the mutation.'
  );
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 140)}]`;
}

// ─────────────────────────────────────────────────────────────────────────────
// TESTS 1 & 4 — AUDIT LOG pagination race, driven deterministically. A
// superseded page load must write NOTHING (the table keeps showing the
// newer page) and must resolve cleanly rather than throw.
// ─────────────────────────────────────────────────────────────────────────────

async function testAuditLogSupersededWriteNothingAndResolvesCleanly(src) {
  const ctx = loadRetailSystem(src);
  // Local UI state _renderAuditLog would normally set up -- start on page 2,
  // exactly as if the cashier had already clicked Next once.
  ctx.rs._auditLog = { page: 2, limit: 50, date_from: '', date_to: '', action: '', entity: '', totalPages: 1 };

  // Load A: page 2, left hanging on its own fetch.
  const pA = ctx.rs._loadAuditLog();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 1,
    `expected load A (page 2) to have reached its own fetch by now. Calls: ${ctx.fetchCalls.length}`);

  // Load B: the cashier clicks Next again (page 3) before A's response
  // arrives. Same screen throughout -- no navigation, so _isStaleRender
  // could never see this.
  ctx.rs._auditLog.page = 3;
  const pB = ctx.rs._loadAuditLog();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 2,
    `expected load B (page 3) to have reached its own fetch by now. Calls: ${ctx.fetchCalls.length}`);

  // B (page 3, the NEWER request) resolves FIRST and paints the screen.
  ctx.fetchCalls[1].resolve(AUDIT_PAGE3_RESPONSE);
  await pB;
  await settle();
  const tbody = ctx.getEl('aud-table-tbody');
  assert.ok(tbody.innerHTML.includes('CURRENT PAGE 3 ROW'),
    `expected page 3's row to have painted before page 2 resolved. Got: ${tbody.innerHTML}`);

  // THEN A's (page 2, the OLDER request) response arrives late -- after the
  // user is already looking at page 3.
  ctx.fetchCalls[0].resolve(AUDIT_PAGE2_RESPONSE);
  let threw = null;
  try { await pA; } catch (e) { threw = e; }
  await settle();

  assert.strictEqual(threw, null,
    'a superseded audit-log load must resolve cleanly, not throw. Got: ' +
    (threw && (threw.stack || threw.message)));

  // THE ASSERTION THAT MATTERS: the table must STILL show page 3, and the
  // summary must not contradict the pager (see _loadAuditLog's own comment:
  // "the table shows page 2's rows while the pager and summary line both
  // say Page 3" is the exact bug this guard exists to stop).
  assert.ok(tbody.innerHTML.includes('CURRENT PAGE 3 ROW'),
    `load A (superseded, page 2) overwrote the screen. Got: ${tbody.innerHTML}`);
  assert.ok(!tbody.innerHTML.includes('STALE PAGE 2 ROW'),
    'the stale page-2 row leaked onto the screen after page 3 had already painted.');
  const summary = ctx.getEl('aud-summary');
  assert.ok(summary.textContent.includes('3'),
    `the summary line must still describe page 3, not the late page-2 response. Got: ${summary.textContent}`);

  console.log('PASS: a superseded audit-log page load writes nothing (table keeps the newer page) and resolves without throwing');
}

async function testAuditLogNormalPathRendersItsRows(src) {
  const ctx = loadRetailSystem(src);
  ctx.rs._auditLog = { page: 1, limit: 50, date_from: '', date_to: '', action: '', entity: '', totalPages: 1 };

  const p = ctx.rs._loadAuditLog();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 1, 'expected exactly one fetch for a lone, uncontested audit-log load');

  ctx.fetchCalls[0].resolve(AUDIT_NORMAL_RESPONSE);
  await p;
  await settle();

  // ANTI-VACUITY: a guard that makes every load a no-op would pass the
  // superseded-load test above trivially (nothing ever writes, so nothing
  // is ever overwritten) while silently turning this screen into a
  // permanent no-op. Assert the ACTUAL row text landed, not just "the table
  // changed somehow".
  const tbody = ctx.getEl('aud-table-tbody');
  assert.ok(tbody.innerHTML.includes('NORMAL SINGLE-LOAD ROW'),
    `a normal, uncontested audit-log load did not write its row. Got: ${JSON.stringify(tbody.innerHTML)}`);
  assert.ok(tbody.innerHTML.includes('NORMAL_ACTION'),
    `a normal, uncontested audit-log load did not write its action badge. Got: ${JSON.stringify(tbody.innerHTML)}`);
  const summary = ctx.getEl('aud-summary');
  assert.ok(summary.textContent.includes('1'),
    `a normal, uncontested audit-log load did not write its page summary. Got: ${JSON.stringify(summary.textContent)}`);

  console.log('PASS: a normal, uncontested audit-log load still writes its rows (anti-vacuity: exact row text landed)');
}

// ─────────────────────────────────────────────────────────────────────────────
// TESTS 2 & 4 — SALES HISTORY search/filter race. Same shape, and these
// rows are money: a superseded search must write NOTHING and must not
// throw; the allow-half (a single uncontested search) must still render.
// ─────────────────────────────────────────────────────────────────────────────

async function testSalesHistorySupersededWriteNothingAndResolvesCleanly(src) {
  const ctx = loadRetailSystem(src);

  // Load A: the cashier types a first filter.
  ctx.getEl('sh-search').value = 'first-filter';
  const pA = ctx.rs._loadSalesHistory();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 1,
    `expected load A (first filter) to have reached its own fetch by now. Calls: ${ctx.fetchCalls.length}`);

  // Load B: the filter changes again before A's response settles -- same
  // screen throughout, no navigation.
  ctx.getEl('sh-search').value = 'second-filter';
  const pB = ctx.rs._loadSalesHistory();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 2,
    `expected load B (second filter) to have reached its own fetch by now. Calls: ${ctx.fetchCalls.length}`);

  // B (the NEWER filter) resolves FIRST and paints the screen.
  ctx.fetchCalls[1].resolve(SALES_FILTER_B_RESPONSE);
  await pB;
  await settle();
  const tbody = ctx.getEl('sh-table-tbody');
  assert.ok(tbody.innerHTML.includes('S-CURRENT-B'),
    `expected filter B's row to have painted before filter A resolved. Got: ${tbody.innerHTML}`);

  // THEN A's (older, first-filter) response arrives late -- after the user
  // is already looking at filter B's results.
  ctx.fetchCalls[0].resolve(SALES_FILTER_A_RESPONSE);
  let threw = null;
  try { await pA; } catch (e) { threw = e; }
  await settle();

  assert.strictEqual(threw, null,
    'a superseded sales-history load must resolve cleanly, not throw. Got: ' +
    (threw && (threw.stack || threw.message)));

  // THE ASSERTION THAT MATTERS: these rows are money. Filter A's late,
  // stale sale must not have overwritten filter B's screen.
  assert.ok(tbody.innerHTML.includes('S-CURRENT-B'),
    `load A (superseded) overwrote the sales history screen. Got: ${tbody.innerHTML}`);
  assert.ok(!tbody.innerHTML.includes('S-STALE-A'),
    'the stale filter-A sale leaked onto the screen after filter B had already painted.');

  console.log("PASS: a superseded sales-history reload writes nothing (table keeps the newer filter's rows) and resolves without throwing");
}

async function testSalesHistoryNormalPathRendersItsRows(src) {
  const ctx = loadRetailSystem(src);
  ctx.getEl('sh-search').value = '';

  const p = ctx.rs._loadSalesHistory();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 1, 'expected exactly one fetch for a lone, uncontested sales-history load');

  ctx.fetchCalls[0].resolve(SALES_NORMAL_RESPONSE);
  await p;
  await settle();

  // ANTI-VACUITY, same reasoning as the audit-log allow-half above: the
  // dangerous wrong fix here is one that silently stops the list loading.
  const tbody = ctx.getEl('sh-table-tbody');
  assert.ok(tbody.innerHTML.includes('S-NORMAL'),
    `a normal, uncontested sales-history load did not write its row. Got: ${JSON.stringify(tbody.innerHTML)}`);
  assert.ok(tbody.innerHTML.includes('Normal Customer'),
    `a normal, uncontested sales-history load did not write its customer name. Got: ${JSON.stringify(tbody.innerHTML)}`);
  const countEl = ctx.getEl('sh-count');
  assert.ok(countEl.innerHTML.includes('1'),
    `a normal, uncontested sales-history load did not write its count line. Got: ${JSON.stringify(countEl.innerHTML)}`);

  console.log('PASS: a normal, uncontested sales-history load still writes its rows (anti-vacuity: exact row text landed)');
}

// ─────────────────────────────────────────────────────────────────────────────
// TEST 6 — the two guards are INDEPENDENT. Exercises the real, shipped
// _beginRender/_isStaleRender/_beginOp/_isStaleOp directly: this is what
// stops someone later "simplifying" the per-operation guard into reusing
// the render generation counter (or vice versa), which would silently
// reintroduce either race this file exists to catch.
// ─────────────────────────────────────────────────────────────────────────────

function testOperationGuardIsIndependentOfRenderGuard(src) {
  const ctx = loadRetailSystem(src);
  const rs = ctx.rs;

  // Case 1: operation-current but navigation-stale. An operation token must
  // stay valid purely by its OWN sequence, no matter how many times the
  // user has navigated elsewhere and back in the meantime.
  const opToken = rs._beginOp('probe');
  rs._beginRender();
  rs._beginRender();
  rs._beginRender();
  assert.strictEqual(rs._isStaleOp('probe', opToken), false,
    'navigating (bumping _renderGeneration) must not invalidate an unrelated operation token -- the two guards must stay independent');

  // Case 2: the reverse -- a render token captured before those navigations
  // IS correctly stale under the render guard, proving the two mechanisms
  // answer genuinely different questions rather than one silently
  // subsuming the other.
  const renderToken = rs._beginRender();
  assert.strictEqual(rs._isStaleRender(renderToken), false,
    'sanity: a token captured against the CURRENT generation must not itself already read as stale');
  rs._beginRender();
  assert.strictEqual(rs._isStaleRender(renderToken), true,
    'a render token captured before a later navigation must be navigation-stale');

  // Case 3: re-firing the SAME operation many times must never perturb the
  // render generation.
  const renderTokenBefore = rs._beginRender();
  rs._beginOp('probe');
  rs._beginOp('probe');
  rs._beginOp('probe');
  assert.strictEqual(rs._isStaleRender(renderTokenBefore), false,
    'an unrelated _beginOp() call must never advance _renderGeneration');

  // Case 4: two independently-NAMED operations (as _loadAuditLog's
  // 'auditLog' and _loadSalesHistory's 'salesHistory' are) must not share
  // one counter -- reloading Sales History must never invalidate an
  // in-flight Audit Log page load, and vice versa.
  const auditToken = rs._beginOp('auditLog');
  rs._beginOp('salesHistory');
  rs._beginOp('salesHistory');
  assert.strictEqual(rs._isStaleOp('auditLog', auditToken), false,
    "a differently-named operation ('salesHistory') must not share or invalidate another operation's ('auditLog') sequence");

  console.log('PASS: the per-operation guard and the per-navigation render guard are tracked independently, and two differently-named operations do not share a sequence');
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

  // ── Against the REAL, unmutated build ──────────────────────────────────────
  await run('testAuditLogSupersededWriteNothingAndResolvesCleanly',
    () => testAuditLogSupersededWriteNothingAndResolvesCleanly(SRC));
  await run('testAuditLogNormalPathRendersItsRows',
    () => testAuditLogNormalPathRendersItsRows(SRC));
  await run('testSalesHistorySupersededWriteNothingAndResolvesCleanly',
    () => testSalesHistorySupersededWriteNothingAndResolvesCleanly(SRC));
  await run('testSalesHistoryNormalPathRendersItsRows',
    () => testSalesHistoryNormalPathRendersItsRows(SRC));
  await run('testOperationGuardIsIndependentOfRenderGuard',
    () => testOperationGuardIsIndependentOfRenderGuard(SRC));

  // ── MUTATION-PROVED, both directions, for EACH of the two fixed sites ──────
  //
  // Direction 1 (per site): remove the operation-staleness check entirely =>
  // the race test for that site must go RED, and with the REAL message (the
  // stale page/filter actually overwriting the screen), not an unrelated
  // crash.
  await run('M1: audit-log operation check removed => testAuditLogSupersededWriteNothingAndResolvesCleanly FAILS', async () => {
    const broken = mutate(SRC, [[
      "      const res = await this._get(`/api/sub/retail/audit-log?${qs.toString()}`);\n" +
      "      // Superseded by a later _loadAuditLog() call (another page/filter\n" +
      "      // change fired before this one's response arrived)? Genuine early\n" +
      "      // return, same contract as _isStaleRender: write NOTHING -- not even\n" +
      "      // the error branch below -- rather than paint an older page's rows or\n" +
      "      // refusal message over whatever the latest call already wrote.\n" +
      "      if (this._isStaleOp('auditLog', opToken)) return;\n",
      "      const res = await this._get(`/api/sub/retail/audit-log?${qs.toString()}`);\n" +
      "      // MUTATED: operation-staleness check removed\n",
    ]]);
    return provesMutation(
      'M1 audit-log operation check removed',
      broken,
      (b) => testAuditLogSupersededWriteNothingAndResolvesCleanly(b)
    );
  });

  await run('M2: sales-history operation check removed => testSalesHistorySupersededWriteNothingAndResolvesCleanly FAILS', async () => {
    const broken = mutate(SRC, [[
      "      const res = await this._get(`/api/sub/retail/sales/recent?${params}`);\n" +
      "      // Superseded by a later _loadSalesHistory() call (the search box or a\n" +
      "      // date filter changed again before this one's response arrived)?\n" +
      "      // Genuine early return, same contract as _isStaleRender: write\n" +
      "      // NOTHING -- not even the error branch below -- rather than let an\n" +
      "      // older query's result set (or refusal message) land on top of\n" +
      "      // whatever the latest call already wrote.\n" +
      "      if (this._isStaleOp('salesHistory', opToken)) return;\n",
      "      const res = await this._get(`/api/sub/retail/sales/recent?${params}`);\n" +
      "      // MUTATED: operation-staleness check removed\n",
    ]]);
    return provesMutation(
      'M2 sales-history operation check removed',
      broken,
      (b) => testSalesHistorySupersededWriteNothingAndResolvesCleanly(b)
    );
  });

  // Direction 2 (per site, the one that matters most -- ENGINEERING.md #1
  // "prove both directions of anything that both denies and allows"): make
  // the check ALWAYS treat the load as superseded, i.e. always return
  // early. This is the dangerous wrong fix -- it would pass the race tests
  // above trivially (nothing ever writes, so nothing is ever overwritten)
  // while silently turning the screen into a permanent no-op. The
  // allow-half (normal-path) test must catch it.
  await run('M3: audit-log guard always returns early (allow-half destroyed) => testAuditLogNormalPathRendersItsRows FAILS', async () => {
    const broken = mutate(SRC, [[
      "      const res = await this._get(`/api/sub/retail/audit-log?${qs.toString()}`);\n" +
      "      // Superseded by a later _loadAuditLog() call (another page/filter\n" +
      "      // change fired before this one's response arrived)? Genuine early\n" +
      "      // return, same contract as _isStaleRender: write NOTHING -- not even\n" +
      "      // the error branch below -- rather than paint an older page's rows or\n" +
      "      // refusal message over whatever the latest call already wrote.\n" +
      "      if (this._isStaleOp('auditLog', opToken)) return;\n",
      "      const res = await this._get(`/api/sub/retail/audit-log?${qs.toString()}`);\n" +
      "      if (true) return; // MUTATED: every audit-log load treated as superseded\n",
    ]]);
    return provesMutation(
      'M3 audit-log guard always returns early',
      broken,
      (b) => testAuditLogNormalPathRendersItsRows(b)
    );
  });

  await run('M4: sales-history guard always returns early (allow-half destroyed) => testSalesHistoryNormalPathRendersItsRows FAILS', async () => {
    const broken = mutate(SRC, [[
      "      const res = await this._get(`/api/sub/retail/sales/recent?${params}`);\n" +
      "      // Superseded by a later _loadSalesHistory() call (the search box or a\n" +
      "      // date filter changed again before this one's response arrived)?\n" +
      "      // Genuine early return, same contract as _isStaleRender: write\n" +
      "      // NOTHING -- not even the error branch below -- rather than let an\n" +
      "      // older query's result set (or refusal message) land on top of\n" +
      "      // whatever the latest call already wrote.\n" +
      "      if (this._isStaleOp('salesHistory', opToken)) return;\n",
      "      const res = await this._get(`/api/sub/retail/sales/recent?${params}`);\n" +
      "      if (true) return; // MUTATED: every sales-history load treated as superseded\n",
    ]]);
    return provesMutation(
      'M4 sales-history guard always returns early',
      broken,
      (b) => testSalesHistoryNormalPathRendersItsRows(b)
    );
  });

  console.log(results.join('\n'));
  if (failed) {
    console.error(`\nFAIL: retail_list_reload_race_test.js — ${failed} of ${results.length} check(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`\nPASS: retail_list_reload_race_test.js — ${results.length} check(s)`);
  }
}

main().catch((err) => {
  console.error('FAIL: retail_list_reload_race_test.js (runner)');
  console.error((err && err.stack) || err);
  process.exitCode = 1;
});
