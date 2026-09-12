/**
 * retail_list_reload_race_test.js — SAME-SCREEN reload races that the
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
 * THE ORIGINAL TWO RACES this file drove deterministically:
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
 * FOLLOW-UP (retail-hardware-viewports, continuation): the original AUDIT
 * NOTE here named six more sites sharing this exact defect shape --
 * _loadCustomers' debounced search, _loadReports' branch/day filters (which
 * also drive _loadEmployeeSales, awaited as one step of the same reload),
 * _loadStockAccuracy, and the three Exceptions-queue loaders
 * (_loadExceptionStock / _loadExceptionConflicts / _loadExceptionRegistry) --
 * and deliberately left them unfixed as scoped follow-up candidates rather
 * than silently widening that task. All six are now fixed the same way, with
 * their own distinctly-named counters ('customers', 'reports',
 * 'stockAccuracy', 'exceptionStock', 'exceptionConflicts',
 * 'exceptionRegistry'), and are proved by the races below. _loadStockAccuracy
 * and the three Exceptions loaders additionally mutate a SHARED, READ-LATER
 * state object in place (`this._stockAccuracy` / `this._exceptionQueue.*`)
 * rather than only writing the DOM, so their guard sits before that mutation,
 * not just before the repaint -- see subsystem-retail.js's own comment on
 * _loadStockAccuracy for why a stale write into that object would corrupt it
 * even when the paint itself is skipped, and the state-corruption assertions
 * in this file's stock-accuracy race test below.
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

// ── Follow-up fixtures (retail-hardware-viewports, continuation) ───────────
// One STALE/A and one CURRENT/B poison pair per site, plus a NORMAL fixture
// for the single-load allow-half, same convention as the two pairs above.

// _loadCustomers has no `status` envelope check at all (`(await
// this._get(url)).data || []`), unlike every other site here -- these
// fixtures match that shape deliberately, not by omission.
const CUSTOMERS_STALE_A_RESPONSE = {
  data: [{
    id: 'cust-stale-a', name: 'STALE Customer A -- must never appear after B has painted',
    phone: '000-000', email: 'stale-a@test.invalid', loyalty_points: 1,
    total_spent: 999999, order_count: 1,
  }],
};
const CUSTOMERS_CURRENT_B_RESPONSE = {
  data: [{
    id: 'cust-current-b', name: 'CURRENT Customer B',
    phone: '111-111', email: 'current-b@test.invalid', loyalty_points: 2,
    total_spent: 42, order_count: 2,
  }],
};
const CUSTOMERS_NORMAL_RESPONSE = {
  data: [{
    id: 'cust-normal', name: 'Normal Customer',
    phone: '222-222', email: 'normal@test.invalid', loyalty_points: 3,
    total_spent: 15, order_count: 3,
  }],
};

// _loadReports' KPI tiles (real revenue figures) plus the four chart
// fetches, whose content is irrelevant here: loadRetailSystem()'s sandbox
// defines no `window.Chart`, so _loadReports' entire chart-building block
// (guarded by `if (window.Chart)`) never executes -- only the `.labels`/
// `.data` `||[]` defaults inside that dead branch would ever touch these.
const REPORTS_CHART_MINIMAL_RESPONSE = {};
const REPORTS_EMPLOYEE_EMPTY_RESPONSE = { status: 'success', data: [] };
const REPORTS_SUMMARY_STALE_RESPONSE = {
  data: { revenue: 999999, transactions: 999, gross_profit: 999, margin_pct: 9, avg_ticket: 999 },
};
const REPORTS_SUMMARY_CURRENT_RESPONSE = {
  data: { revenue: 42, transactions: 4, gross_profit: 10, margin_pct: 24, avg_ticket: 10.5 },
};
const REPORTS_SUMMARY_NORMAL_RESPONSE = {
  data: { revenue: 777, transactions: 7, gross_profit: 77, margin_pct: 77, avg_ticket: 77.7 },
};

// _loadStockAccuracy mutates `this._stockAccuracy` (state/data/error) in
// place, read later by _paintStockAccuracy/_askRepairStockAccuracy/
// _repairStockAccuracy -- so its race test asserts the OBJECT, not only the
// DOM. STALE is a refusal (state 'failed'); CURRENT/NORMAL are clean checks
// distinguished only by `pairs_examined`, which _stkaClean() prints.
const STOCK_ACCURACY_STALE_FAILED_RESPONSE = {
  status: 'error',
  message: 'STALE_STOCK_ACCURACY_REFUSAL -- must never appear after the current check has painted',
};
const STOCK_ACCURACY_CURRENT_CLEAN_RESPONSE = {
  status: 'success', data: { pairs_examined: 555, drift_count: 0, net_drift: 0, rows: [] },
};
const STOCK_ACCURACY_NORMAL_CLEAN_RESPONSE = {
  status: 'success', data: { pairs_examined: 321, drift_count: 0, net_drift: 0, rows: [] },
};

// The three Exceptions-queue loaders mutate `this._exceptionQueue.<section>`
// in place, the same shared-state shape as Stock Accuracy (their own header
// comment in subsystem-retail.js says they mirror it) -- same STALE-failed /
// CURRENT-rows / NORMAL-rows fixture shape per queue, using each queue's own
// response envelope key (`exceptions` / `conflicts` / `items`).
const EXQ_STOCK_STALE_FAILED_RESPONSE = {
  status: 'error',
  message: 'STALE_EXQ_STOCK_REFUSAL -- must never appear after the current check has painted',
};
const EXQ_STOCK_CURRENT_ROWS_RESPONSE = {
  status: 'success',
  data: { exceptions: [{
    id: 'exq-stock-current', product_name: 'CURRENT Oversold Product', sku: 'SKU-CUR',
    branch_name: 'Main', observed_quantity_on_hand: -3, detected_at_utc: '2026-09-01T09:00:00+00:00',
  }] },
};
const EXQ_STOCK_NORMAL_ROWS_RESPONSE = {
  status: 'success',
  data: { exceptions: [{
    id: 'exq-stock-normal', product_name: 'Normal Oversold Product', sku: 'SKU-N',
    branch_name: 'Main', observed_quantity_on_hand: -1, detected_at_utc: '2026-09-01T08:00:00+00:00',
  }] },
};

const EXQ_CONFLICTS_STALE_FAILED_RESPONSE = {
  status: 'error',
  message: 'STALE_EXQ_CONFLICTS_REFUSAL -- must never appear after the current check has painted',
};
const EXQ_CONFLICTS_CURRENT_ROWS_RESPONSE = {
  status: 'success',
  data: { conflicts: [{
    entity_type: 'product', entity_id: 'CUR-777', event_type: 'update',
    changed_fields: ['name'], local_row_version: 2, incoming_row_version: 1,
    detected_at_utc: '2026-09-01T09:00:00+00:00',
  }] },
};
const EXQ_CONFLICTS_NORMAL_ROWS_RESPONSE = {
  status: 'success',
  data: { conflicts: [{
    entity_type: 'product', entity_id: 'NORM-1', event_type: 'update',
    changed_fields: ['price'], local_row_version: 4, incoming_row_version: 3,
    detected_at_utc: '2026-09-01T08:00:00+00:00',
  }] },
};

const EXQ_REGISTRY_STALE_FAILED_RESPONSE = {
  status: 'error',
  message: 'STALE_EXQ_REGISTRY_REFUSAL -- must never appear after the current check has painted',
};
const EXQ_REGISTRY_CURRENT_ROWS_RESPONSE = {
  status: 'success',
  data: { items: [{
    entity_type: 'user', event_type: 'create', reason: 'duplicate',
    detail: 'CURRENT_REGISTRY_MARKER_777', quarantined_at: '2026-09-01T09:00:00+00:00',
  }] },
};
const EXQ_REGISTRY_NORMAL_ROWS_RESPONSE = {
  status: 'success',
  data: { items: [{
    entity_type: 'user', event_type: 'create', reason: 'duplicate',
    detail: 'NORMAL_REGISTRY_MARKER', quarantined_at: '2026-09-01T08:00:00+00:00',
  }] },
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
      // else gets a throwaway stub. _loadCustomers (`#cust-table tbody`) and
      // _loadEmployeeSales (`#rep-emp-table tbody`) use the identical
      // querySelector pattern, added here for the same reason.
      querySelector(sel) {
        if (sel === '#aud-table tbody') return getEl('aud-table-tbody');
        if (sel === '#sh-table tbody') return getEl('sh-table-tbody');
        if (sel === '#cust-table tbody') return getEl('cust-table-tbody');
        if (sel === '#rep-emp-table tbody') return getEl('rep-emp-table-tbody');
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

// ─────────────────────────────────────────────────────────────────────────────
// TESTS 7-8 — CUSTOMERS debounced-search race (follow-up site 1). Same shape
// as Sales History's filter race, PLUS a state assertion: `this._customers`
// is read later by _openEditCustomer/_viewCustomer/_deleteCustomer, so a
// stale write there would corrupt what those act on even if the table paint
// were somehow skipped.
// ─────────────────────────────────────────────────────────────────────────────

async function testCustomersSupersededWriteNothingAndResolvesCleanly(src) {
  const ctx = loadRetailSystem(src);

  // Load A: the cashier types a first search term. _filterCustomers'
  // 350ms debounce is bypassed here by calling _loadCustomers(q) directly --
  // the debounce only delays WHEN this fires, never whether two calls can
  // still race, so driving it directly is the same race, deterministically.
  const pA = ctx.rs._loadCustomers('first-search');
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 1,
    `expected load A (first search) to have reached its own fetch by now. Calls: ${ctx.fetchCalls.length}`);

  // Load B: the search box changes again before A's response settles --
  // same screen throughout, no navigation.
  const pB = ctx.rs._loadCustomers('second-search');
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 2,
    `expected load B (second search) to have reached its own fetch by now. Calls: ${ctx.fetchCalls.length}`);

  // B (the NEWER search) resolves FIRST and paints the screen.
  ctx.fetchCalls[1].resolve(CUSTOMERS_CURRENT_B_RESPONSE);
  await pB;
  await settle();
  const tbody = ctx.getEl('cust-table-tbody');
  assert.ok(tbody.innerHTML.includes('CURRENT Customer B'),
    `expected search B's row to have painted before search A resolved. Got: ${tbody.innerHTML}`);
  assert.deepStrictEqual(ctx.rs._customers.map(c => c.id), ['cust-current-b'],
    `expected this._customers to hold search B's result after B resolved. Got: ${JSON.stringify(ctx.rs._customers)}`);

  // THEN A's (older, first-search) response arrives late -- after the user
  // is already looking at search B's results.
  ctx.fetchCalls[0].resolve(CUSTOMERS_STALE_A_RESPONSE);
  let threw = null;
  try { await pA; } catch (e) { threw = e; }
  await settle();

  assert.strictEqual(threw, null,
    'a superseded customers load must resolve cleanly, not throw. Got: ' +
    (threw && (threw.stack || threw.message)));

  // THE ASSERTION THAT MATTERS, TWICE OVER: the table AND `this._customers`
  // must both still reflect search B, never the stale search A.
  assert.ok(tbody.innerHTML.includes('CURRENT Customer B'),
    `load A (superseded) overwrote the customers table. Got: ${tbody.innerHTML}`);
  assert.ok(!tbody.innerHTML.includes('STALE Customer A'),
    'the stale search-A customer leaked onto the screen after search B had already painted.');
  assert.deepStrictEqual(ctx.rs._customers.map(c => c.id), ['cust-current-b'],
    'load A (superseded) overwrote this._customers -- _openEditCustomer/_viewCustomer/_deleteCustomer ' +
    `would now act on the wrong search's rows. Got: ${JSON.stringify(ctx.rs._customers)}`);

  console.log("PASS: a superseded customers reload writes nothing (table AND this._customers keep search B's result) and resolves without throwing");
}

async function testCustomersNormalPathRendersItsRows(src) {
  const ctx = loadRetailSystem(src);

  const p = ctx.rs._loadCustomers();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 1, 'expected exactly one fetch for a lone, uncontested customers load');

  ctx.fetchCalls[0].resolve(CUSTOMERS_NORMAL_RESPONSE);
  await p;
  await settle();

  // ANTI-VACUITY: a guard that makes every load a no-op would pass the race
  // test above trivially. Assert the ACTUAL row and state landed.
  const tbody = ctx.getEl('cust-table-tbody');
  assert.ok(tbody.innerHTML.includes('Normal Customer'),
    `a normal, uncontested customers load did not write its row. Got: ${JSON.stringify(tbody.innerHTML)}`);
  assert.deepStrictEqual(ctx.rs._customers.map(c => c.id), ['cust-normal'],
    `a normal, uncontested customers load did not write this._customers. Got: ${JSON.stringify(ctx.rs._customers)}`);

  console.log('PASS: a normal, uncontested customers load still writes its rows and this._customers (anti-vacuity)');
}

// ─────────────────────────────────────────────────────────────────────────────
// TESTS 9-10 — REPORTS branch/period filter race (follow-up site 2). THESE
// ARE REAL REVENUE FIGURES -- an older filter's numbers landing over a newer
// selection is the worst case in this whole group. _loadReports awaits
// _loadEmployeeSales (one fetch) and then five more in a Promise.all, all
// sharing the SAME 'reports' opToken (see _loadReports' own comment for why
// sharing is correct here, not a violation of "give each its own name").
// window.Chart is undefined in this sandbox, so the entire chart-building
// block never runs -- only the KPI tiles (#rep-rev etc.) are asserted.
// ─────────────────────────────────────────────────────────────────────────────

function fetchesMatching(ctx, re) {
  return ctx.fetchCalls.filter(c => re.test(c.url));
}

async function testReportsSupersededWriteNothingAndResolvesCleanly(src) {
  const ctx = loadRetailSystem(src);

  // Load A: the manager opens Reports with the default filter.
  const pA = ctx.rs._loadReports();
  await settle();
  let empCalls = fetchesMatching(ctx, /\/reports\/by-employee\?/);
  assert.strictEqual(empCalls.length, 1,
    `expected load A's employee-sales sub-fetch to have fired first. URLs so far: ${ctx.fetchCalls.map(c => c.url).join(', ')}`);
  empCalls[0].resolve(REPORTS_EMPLOYEE_EMPTY_RESPONSE);
  await settle();

  let summaryCalls = fetchesMatching(ctx, /\/reports\/summary\?/);
  assert.strictEqual(summaryCalls.length, 1,
    `expected load A to have reached its KPI/chart fetches by now. URLs: ${ctx.fetchCalls.map(c => c.url).join(', ')}`);

  // Load B: the manager picks a different branch before A's chain settles --
  // same screen throughout, no navigation.
  ctx.getEl('rep-branch').value = 'branch-2';
  const pB = ctx.rs._loadReports();
  await settle();
  empCalls = fetchesMatching(ctx, /\/reports\/by-employee\?/);
  assert.strictEqual(empCalls.length, 2,
    `expected load B to have fired its own employee-sales sub-fetch. URLs: ${ctx.fetchCalls.map(c => c.url).join(', ')}`);
  empCalls[1].resolve(REPORTS_EMPLOYEE_EMPTY_RESPONSE);
  await settle();

  const trendCalls = fetchesMatching(ctx, /\/reports\/sales-trend\?/);
  const topCalls = fetchesMatching(ctx, /\/reports\/top-products\?/);
  const payCalls = fetchesMatching(ctx, /\/reports\/payment-methods\?/);
  summaryCalls = fetchesMatching(ctx, /\/reports\/summary\?/);
  const byBranchCalls = fetchesMatching(ctx, /\/reports\/by-branch\?/);
  assert.strictEqual(summaryCalls.length, 2,
    `expected both load A and load B's summary fetch by now. URLs: ${ctx.fetchCalls.map(c => c.url).join(', ')}`);

  // B (the NEWER branch selection) resolves FIRST and paints the KPI tiles.
  trendCalls[1].resolve(REPORTS_CHART_MINIMAL_RESPONSE);
  topCalls[1].resolve(REPORTS_CHART_MINIMAL_RESPONSE);
  payCalls[1].resolve(REPORTS_CHART_MINIMAL_RESPONSE);
  summaryCalls[1].resolve(REPORTS_SUMMARY_CURRENT_RESPONSE);
  byBranchCalls[1].resolve(REPORTS_CHART_MINIMAL_RESPONSE);
  await pB;
  await settle();

  const revEl = ctx.getEl('rep-rev');
  assert.ok(revEl.textContent.includes('42'),
    `expected the newer branch selection's revenue to have painted before the older one resolved. Got: ${revEl.textContent}`);

  // THEN A's (older, superseded) KPI figures arrive late -- after the user
  // is already looking at the newer branch's revenue.
  trendCalls[0].resolve(REPORTS_CHART_MINIMAL_RESPONSE);
  topCalls[0].resolve(REPORTS_CHART_MINIMAL_RESPONSE);
  payCalls[0].resolve(REPORTS_CHART_MINIMAL_RESPONSE);
  summaryCalls[0].resolve(REPORTS_SUMMARY_STALE_RESPONSE);
  byBranchCalls[0].resolve(REPORTS_CHART_MINIMAL_RESPONSE);
  let threw = null;
  try { await pA; } catch (e) { threw = e; }
  await settle();

  assert.strictEqual(threw, null,
    'a superseded reports load must resolve cleanly, not throw. Got: ' +
    (threw && (threw.stack || threw.message)));

  // THE ASSERTION THAT MATTERS: these are real revenue figures.
  assert.ok(revEl.textContent.includes('42'),
    `load A (superseded) overwrote the Reports KPI tiles with a stale revenue figure. Got: ${revEl.textContent}`);
  assert.ok(!revEl.textContent.includes('999999'),
    'the stale, superseded revenue figure leaked onto the Reports screen after the newer branch selection had already painted.');

  console.log("PASS: a superseded reports reload writes nothing onto the KPI tiles (they keep the newer selection's revenue) and resolves without throwing");
}

async function testReportsNormalPathRendersItsRows(src) {
  const ctx = loadRetailSystem(src);

  const p = ctx.rs._loadReports();
  await settle();
  let empCalls = fetchesMatching(ctx, /\/reports\/by-employee\?/);
  assert.strictEqual(empCalls.length, 1, 'expected exactly one employee-sales fetch for a lone, uncontested reports load');
  empCalls[0].resolve(REPORTS_EMPLOYEE_EMPTY_RESPONSE);
  await settle();

  const trendCalls = fetchesMatching(ctx, /\/reports\/sales-trend\?/);
  const topCalls = fetchesMatching(ctx, /\/reports\/top-products\?/);
  const payCalls = fetchesMatching(ctx, /\/reports\/payment-methods\?/);
  const summaryCalls = fetchesMatching(ctx, /\/reports\/summary\?/);
  const byBranchCalls = fetchesMatching(ctx, /\/reports\/by-branch\?/);
  assert.strictEqual(summaryCalls.length, 1, 'expected exactly one summary fetch for a lone, uncontested reports load');

  trendCalls[0].resolve(REPORTS_CHART_MINIMAL_RESPONSE);
  topCalls[0].resolve(REPORTS_CHART_MINIMAL_RESPONSE);
  payCalls[0].resolve(REPORTS_CHART_MINIMAL_RESPONSE);
  summaryCalls[0].resolve(REPORTS_SUMMARY_NORMAL_RESPONSE);
  byBranchCalls[0].resolve(REPORTS_CHART_MINIMAL_RESPONSE);
  await p;
  await settle();

  // ANTI-VACUITY, same reasoning as every allow-half above: the dangerous
  // wrong fix here is one that silently stops the KPI tiles ever updating.
  const revEl = ctx.getEl('rep-rev');
  assert.ok(revEl.textContent.includes('777'),
    `a normal, uncontested reports load did not write its revenue KPI. Got: ${JSON.stringify(revEl.textContent)}`);
  const txnEl = ctx.getEl('rep-txn');
  assert.ok(txnEl.textContent.includes('7'),
    `a normal, uncontested reports load did not write its transactions KPI. Got: ${JSON.stringify(txnEl.textContent)}`);

  console.log('PASS: a normal, uncontested reports load still writes its KPI tiles (anti-vacuity: exact revenue landed)');
}

// ─────────────────────────────────────────────────────────────────────────────
// TESTS 11-12 — STOCK ACCURACY un-disabled "Run/Try the check again" button
// race (follow-up site 3). `this._stockAccuracy` is mutated IN PLACE and
// read later by _paintStockAccuracy, _askRepairStockAccuracy and
// _repairStockAccuracy -- so the state object itself is asserted, not only
// the DOM (see subsystem-retail.js's own comment on _loadStockAccuracy for
// why a stale write there would corrupt those functions' view of the check
// even if the paint were somehow skipped).
// ─────────────────────────────────────────────────────────────────────────────

async function testStockAccuracySupersededWriteNothingAndResolvesCleanly(src) {
  const ctx = loadRetailSystem(src);

  // Load A: the owner clicks "Run the check again".
  const pA = ctx.rs._loadStockAccuracy();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 1,
    `expected load A to have reached its own fetch by now. Calls: ${ctx.fetchCalls.length}`);

  // Load B: the button is never disabled while a check is in flight (see
  // _loadStockAccuracy's own comment), so the owner clicks it again before
  // A's response lands -- same screen throughout, no navigation.
  const pB = ctx.rs._loadStockAccuracy();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 2,
    `expected load B to have reached its own fetch by now. Calls: ${ctx.fetchCalls.length}`);

  // B (the NEWER check) resolves FIRST and paints the screen.
  ctx.fetchCalls[1].resolve(STOCK_ACCURACY_CURRENT_CLEAN_RESPONSE);
  await pB;
  await settle();
  const host = ctx.getEl('stka-body');
  assert.ok(host.innerHTML.includes('data-sa-state="clean"') && host.innerHTML.includes('555'),
    `expected check B's clean result to have painted before check A resolved. Got: ${host.innerHTML}`);
  assert.strictEqual(ctx.rs._stockAccuracy.state, 'clean',
    `expected this._stockAccuracy.state to be 'clean' after check B resolved. Got: ${JSON.stringify(ctx.rs._stockAccuracy)}`);

  // THEN A's (older, superseded) refusal arrives late -- after the owner is
  // already looking at check B's clean result.
  ctx.fetchCalls[0].resolve(STOCK_ACCURACY_STALE_FAILED_RESPONSE);
  let threw = null;
  try { await pA; } catch (e) { threw = e; }
  await settle();

  assert.strictEqual(threw, null,
    'a superseded stock-accuracy check must resolve cleanly, not throw. Got: ' +
    (threw && (threw.stack || threw.message)));

  // THE ASSERTION THAT MATTERS, TWICE OVER: `this._stockAccuracy` must not
  // be corrupted by the stale response, and neither must the DOM. A
  // corrupted `.state` here would silently break _repairStockAccuracy's own
  // `s.state !== 'confirm'` guard the next time the owner clicks anything.
  assert.strictEqual(ctx.rs._stockAccuracy.state, 'clean',
    "load A (superseded) overwrote this._stockAccuracy.state -- _repairStockAccuracy's own 'confirm' " +
    `guard would now be reading a corrupted state. Got: ${JSON.stringify(ctx.rs._stockAccuracy)}`);
  assert.ok(host.innerHTML.includes('data-sa-state="clean"'),
    `load A (superseded) overwrote the stock-accuracy screen. Got: ${host.innerHTML}`);
  assert.ok(!host.innerHTML.includes('STALE_STOCK_ACCURACY_REFUSAL'),
    'the stale, superseded refusal leaked onto the screen after the current check had already painted.');

  console.log("PASS: a superseded stock-accuracy check writes nothing (this._stockAccuracy AND the DOM keep the current check's result) and resolves without throwing");
}

async function testStockAccuracyNormalPathRendersItsRows(src) {
  const ctx = loadRetailSystem(src);

  const p = ctx.rs._loadStockAccuracy();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 1, 'expected exactly one fetch for a lone, uncontested stock-accuracy check');

  ctx.fetchCalls[0].resolve(STOCK_ACCURACY_NORMAL_CLEAN_RESPONSE);
  await p;
  await settle();

  // ANTI-VACUITY: assert the actual content landed in BOTH the state object
  // and the DOM, so a guard that makes every load a no-op cannot pass both
  // this test and the race test above.
  assert.strictEqual(ctx.rs._stockAccuracy.state, 'clean',
    `a normal, uncontested stock-accuracy check did not write this._stockAccuracy.state. Got: ${JSON.stringify(ctx.rs._stockAccuracy)}`);
  assert.strictEqual(ctx.rs._stockAccuracy.data.pairs_examined, 321,
    `a normal, uncontested stock-accuracy check did not write this._stockAccuracy.data. Got: ${JSON.stringify(ctx.rs._stockAccuracy)}`);
  const host = ctx.getEl('stka-body');
  assert.ok(host.innerHTML.includes('data-sa-state="clean"') && host.innerHTML.includes('321'),
    `a normal, uncontested stock-accuracy check did not paint its result. Got: ${host.innerHTML}`);

  console.log('PASS: a normal, uncontested stock-accuracy check still writes this._stockAccuracy and the DOM (anti-vacuity)');
}

// ─────────────────────────────────────────────────────────────────────────────
// TESTS 13-18 — the three EXCEPTIONS-queue loaders (follow-up sites 4-6).
// Their own header comment in subsystem-retail.js says they mirror
// _loadStockAccuracy's shared, mutated-in-place state object
// (`this._exceptionQueue.<section>`) and its un-disabled retry button --
// same state-plus-DOM assertions as Stock Accuracy above, once per queue,
// each with its OWN counter ('exceptionStock' / 'exceptionConflicts' /
// 'exceptionRegistry').
// ─────────────────────────────────────────────────────────────────────────────

async function testExceptionStockSupersededWriteNothingAndResolvesCleanly(src) {
  const ctx = loadRetailSystem(src);

  const pA = ctx.rs._loadExceptionStock();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 1,
    `expected load A to have reached its own fetch by now. Calls: ${ctx.fetchCalls.length}`);

  // The "Try again" button is never disabled while a check is in flight
  // (mirrors _loadStockAccuracy), so a second click fires before A settles.
  const pB = ctx.rs._loadExceptionStock();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 2,
    `expected load B to have reached its own fetch by now. Calls: ${ctx.fetchCalls.length}`);

  ctx.fetchCalls[1].resolve(EXQ_STOCK_CURRENT_ROWS_RESPONSE);
  await pB;
  await settle();
  const host = ctx.getEl('exq-stock-body');
  assert.ok(host.innerHTML.includes('CURRENT Oversold Product'),
    `expected check B's row to have painted before check A resolved. Got: ${host.innerHTML}`);
  assert.strictEqual(ctx.rs._exceptionQueue.stock.state, 'rows',
    `expected this._exceptionQueue.stock.state to be 'rows' after check B resolved. Got: ${JSON.stringify(ctx.rs._exceptionQueue.stock)}`);

  ctx.fetchCalls[0].resolve(EXQ_STOCK_STALE_FAILED_RESPONSE);
  let threw = null;
  try { await pA; } catch (e) { threw = e; }
  await settle();

  assert.strictEqual(threw, null,
    'a superseded oversold-stock check must resolve cleanly, not throw. Got: ' +
    (threw && (threw.stack || threw.message)));

  assert.strictEqual(ctx.rs._exceptionQueue.stock.state, 'rows',
    `load A (superseded) overwrote this._exceptionQueue.stock.state. Got: ${JSON.stringify(ctx.rs._exceptionQueue.stock)}`);
  assert.ok(host.innerHTML.includes('CURRENT Oversold Product'),
    `load A (superseded) overwrote the oversold-stock queue. Got: ${host.innerHTML}`);
  assert.ok(!host.innerHTML.includes('STALE_EXQ_STOCK_REFUSAL'),
    'the stale, superseded refusal leaked onto the oversold-stock queue after the current check had already painted.');

  console.log("PASS: a superseded oversold-stock check writes nothing (state AND DOM keep the current check's rows) and resolves without throwing");
}

async function testExceptionStockNormalPathRendersItsRows(src) {
  const ctx = loadRetailSystem(src);

  const p = ctx.rs._loadExceptionStock();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 1, 'expected exactly one fetch for a lone, uncontested oversold-stock check');

  ctx.fetchCalls[0].resolve(EXQ_STOCK_NORMAL_ROWS_RESPONSE);
  await p;
  await settle();

  assert.strictEqual(ctx.rs._exceptionQueue.stock.state, 'rows',
    `a normal, uncontested oversold-stock check did not write its state. Got: ${JSON.stringify(ctx.rs._exceptionQueue.stock)}`);
  const host = ctx.getEl('exq-stock-body');
  assert.ok(host.innerHTML.includes('Normal Oversold Product'),
    `a normal, uncontested oversold-stock check did not paint its row. Got: ${host.innerHTML}`);

  console.log('PASS: a normal, uncontested oversold-stock check still writes its state and DOM (anti-vacuity)');
}

async function testExceptionConflictsSupersededWriteNothingAndResolvesCleanly(src) {
  const ctx = loadRetailSystem(src);

  const pA = ctx.rs._loadExceptionConflicts();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 1,
    `expected load A to have reached its own fetch by now. Calls: ${ctx.fetchCalls.length}`);

  const pB = ctx.rs._loadExceptionConflicts();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 2,
    `expected load B to have reached its own fetch by now. Calls: ${ctx.fetchCalls.length}`);

  ctx.fetchCalls[1].resolve(EXQ_CONFLICTS_CURRENT_ROWS_RESPONSE);
  await pB;
  await settle();
  const host = ctx.getEl('exq-conflicts-body');
  assert.ok(host.innerHTML.includes('CUR-777'),
    `expected check B's row to have painted before check A resolved. Got: ${host.innerHTML}`);
  assert.strictEqual(ctx.rs._exceptionQueue.conflicts.state, 'rows',
    `expected this._exceptionQueue.conflicts.state to be 'rows' after check B resolved. Got: ${JSON.stringify(ctx.rs._exceptionQueue.conflicts)}`);

  ctx.fetchCalls[0].resolve(EXQ_CONFLICTS_STALE_FAILED_RESPONSE);
  let threw = null;
  try { await pA; } catch (e) { threw = e; }
  await settle();

  assert.strictEqual(threw, null,
    'a superseded sync-conflicts check must resolve cleanly, not throw. Got: ' +
    (threw && (threw.stack || threw.message)));

  assert.strictEqual(ctx.rs._exceptionQueue.conflicts.state, 'rows',
    `load A (superseded) overwrote this._exceptionQueue.conflicts.state. Got: ${JSON.stringify(ctx.rs._exceptionQueue.conflicts)}`);
  assert.ok(host.innerHTML.includes('CUR-777'),
    `load A (superseded) overwrote the sync-conflicts queue. Got: ${host.innerHTML}`);
  assert.ok(!host.innerHTML.includes('STALE_EXQ_CONFLICTS_REFUSAL'),
    'the stale, superseded refusal leaked onto the sync-conflicts queue after the current check had already painted.');

  console.log("PASS: a superseded sync-conflicts check writes nothing (state AND DOM keep the current check's rows) and resolves without throwing");
}

async function testExceptionConflictsNormalPathRendersItsRows(src) {
  const ctx = loadRetailSystem(src);

  const p = ctx.rs._loadExceptionConflicts();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 1, 'expected exactly one fetch for a lone, uncontested sync-conflicts check');

  ctx.fetchCalls[0].resolve(EXQ_CONFLICTS_NORMAL_ROWS_RESPONSE);
  await p;
  await settle();

  assert.strictEqual(ctx.rs._exceptionQueue.conflicts.state, 'rows',
    `a normal, uncontested sync-conflicts check did not write its state. Got: ${JSON.stringify(ctx.rs._exceptionQueue.conflicts)}`);
  const host = ctx.getEl('exq-conflicts-body');
  assert.ok(host.innerHTML.includes('NORM-1'),
    `a normal, uncontested sync-conflicts check did not paint its row. Got: ${host.innerHTML}`);

  console.log('PASS: a normal, uncontested sync-conflicts check still writes its state and DOM (anti-vacuity)');
}

async function testExceptionRegistrySupersededWriteNothingAndResolvesCleanly(src) {
  const ctx = loadRetailSystem(src);

  const pA = ctx.rs._loadExceptionRegistry();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 1,
    `expected load A to have reached its own fetch by now. Calls: ${ctx.fetchCalls.length}`);

  const pB = ctx.rs._loadExceptionRegistry();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 2,
    `expected load B to have reached its own fetch by now. Calls: ${ctx.fetchCalls.length}`);

  ctx.fetchCalls[1].resolve(EXQ_REGISTRY_CURRENT_ROWS_RESPONSE);
  await pB;
  await settle();
  const host = ctx.getEl('exq-registry-body');
  assert.ok(host.innerHTML.includes('CURRENT_REGISTRY_MARKER_777'),
    `expected check B's row to have painted before check A resolved. Got: ${host.innerHTML}`);
  assert.strictEqual(ctx.rs._exceptionQueue.registry.state, 'rows',
    `expected this._exceptionQueue.registry.state to be 'rows' after check B resolved. Got: ${JSON.stringify(ctx.rs._exceptionQueue.registry)}`);

  ctx.fetchCalls[0].resolve(EXQ_REGISTRY_STALE_FAILED_RESPONSE);
  let threw = null;
  try { await pA; } catch (e) { threw = e; }
  await settle();

  assert.strictEqual(threw, null,
    'a superseded account-quarantine check must resolve cleanly, not throw. Got: ' +
    (threw && (threw.stack || threw.message)));

  assert.strictEqual(ctx.rs._exceptionQueue.registry.state, 'rows',
    `load A (superseded) overwrote this._exceptionQueue.registry.state. Got: ${JSON.stringify(ctx.rs._exceptionQueue.registry)}`);
  assert.ok(host.innerHTML.includes('CURRENT_REGISTRY_MARKER_777'),
    `load A (superseded) overwrote the account-quarantine queue. Got: ${host.innerHTML}`);
  assert.ok(!host.innerHTML.includes('STALE_EXQ_REGISTRY_REFUSAL'),
    'the stale, superseded refusal leaked onto the account-quarantine queue after the current check had already painted.');

  console.log("PASS: a superseded account-quarantine check writes nothing (state AND DOM keep the current check's rows) and resolves without throwing");
}

async function testExceptionRegistryNormalPathRendersItsRows(src) {
  const ctx = loadRetailSystem(src);

  const p = ctx.rs._loadExceptionRegistry();
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 1, 'expected exactly one fetch for a lone, uncontested account-quarantine check');

  ctx.fetchCalls[0].resolve(EXQ_REGISTRY_NORMAL_ROWS_RESPONSE);
  await p;
  await settle();

  assert.strictEqual(ctx.rs._exceptionQueue.registry.state, 'rows',
    `a normal, uncontested account-quarantine check did not write its state. Got: ${JSON.stringify(ctx.rs._exceptionQueue.registry)}`);
  const host = ctx.getEl('exq-registry-body');
  assert.ok(host.innerHTML.includes('NORMAL_REGISTRY_MARKER'),
    `a normal, uncontested account-quarantine check did not paint its row. Got: ${host.innerHTML}`);

  console.log('PASS: a normal, uncontested account-quarantine check still writes its state and DOM (anti-vacuity)');
}

// ─────────────────────────────────────────────────────────────────────────────
// TEST 19 — all EIGHT named operation counters (the original two plus the
// six follow-up sites) stay fully independent of one another. Extends TEST
// 6's Case 4 (which proved only 'auditLog'/'salesHistory' don't cross-
// invalidate) to every name now in use, so retrying one Exceptions queue
// can never invalidate an in-flight reload of a different queue, of Stock
// Accuracy, of Reports, of Customers, or of either original site.
// ─────────────────────────────────────────────────────────────────────────────

function testFollowUpCountersAreAllIndependent(src) {
  const ctx = loadRetailSystem(src);
  const rs = ctx.rs;
  const names = [
    'auditLog', 'salesHistory', 'customers', 'reports', 'stockAccuracy',
    'exceptionStock', 'exceptionConflicts', 'exceptionRegistry',
  ];
  for (const probeName of names) {
    const token = rs._beginOp(probeName);
    for (const otherName of names) {
      if (otherName === probeName) continue;
      rs._beginOp(otherName);
      rs._beginOp(otherName);
    }
    assert.strictEqual(rs._isStaleOp(probeName, token), false,
      `bumping every OTHER named counter must never invalidate '${probeName}''s own token -- ` +
      'all eight counters must stay fully independent');
  }

  console.log('PASS: all eight named operation counters (the original two plus the six follow-up sites) are fully independent of one another');
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

  // ── Follow-up sites (retail-hardware-viewports, continuation) ──────────────
  await run('testCustomersSupersededWriteNothingAndResolvesCleanly',
    () => testCustomersSupersededWriteNothingAndResolvesCleanly(SRC));
  await run('testCustomersNormalPathRendersItsRows',
    () => testCustomersNormalPathRendersItsRows(SRC));
  await run('testReportsSupersededWriteNothingAndResolvesCleanly',
    () => testReportsSupersededWriteNothingAndResolvesCleanly(SRC));
  await run('testReportsNormalPathRendersItsRows',
    () => testReportsNormalPathRendersItsRows(SRC));
  await run('testStockAccuracySupersededWriteNothingAndResolvesCleanly',
    () => testStockAccuracySupersededWriteNothingAndResolvesCleanly(SRC));
  await run('testStockAccuracyNormalPathRendersItsRows',
    () => testStockAccuracyNormalPathRendersItsRows(SRC));
  await run('testExceptionStockSupersededWriteNothingAndResolvesCleanly',
    () => testExceptionStockSupersededWriteNothingAndResolvesCleanly(SRC));
  await run('testExceptionStockNormalPathRendersItsRows',
    () => testExceptionStockNormalPathRendersItsRows(SRC));
  await run('testExceptionConflictsSupersededWriteNothingAndResolvesCleanly',
    () => testExceptionConflictsSupersededWriteNothingAndResolvesCleanly(SRC));
  await run('testExceptionConflictsNormalPathRendersItsRows',
    () => testExceptionConflictsNormalPathRendersItsRows(SRC));
  await run('testExceptionRegistrySupersededWriteNothingAndResolvesCleanly',
    () => testExceptionRegistrySupersededWriteNothingAndResolvesCleanly(SRC));
  await run('testExceptionRegistryNormalPathRendersItsRows',
    () => testExceptionRegistryNormalPathRendersItsRows(SRC));
  await run('testFollowUpCountersAreAllIndependent',
    () => testFollowUpCountersAreAllIndependent(SRC));

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

  // ── MUTATION-PROVED, both directions, for the six FOLLOW-UP sites ─────────
  // Same two directions as M1-M4 above, per site: remove the check (race
  // test must go RED), then make it always-early (allow-half must go RED).

  await run('M5: customers operation check removed => testCustomersSupersededWriteNothingAndResolvesCleanly FAILS', async () => {
    const broken = mutate(SRC, [[
      "      const data = (await this._get(url)).data || [];\n" +
      "      // Superseded by a later _loadCustomers() call (the search box changed\n" +
      "      // again before this one's response arrived)? Genuine early return,\n" +
      "      // same contract as _isStaleRender: write NOTHING -- not even\n" +
      "      // `this._customers` itself -- rather than let an older search's\n" +
      "      // result set land on top of whatever the latest call already wrote.\n" +
      "      // The assignment matters as much as the table paint: _openEditCustomer/\n" +
      "      // _viewCustomer/_deleteCustomer all look the clicked row up in\n" +
      "      // `this._customers` afterwards, so a stale assignment here would\n" +
      "      // silently corrupt what Edit/View/Delete act on even if the table\n" +
      "      // itself still matched the newer search.\n" +
      "      if (this._isStaleOp('customers', opToken)) return;\n",
      "      const data = (await this._get(url)).data || [];\n" +
      "      // MUTATED: operation-staleness check removed\n",
    ]]);
    return provesMutation(
      'M5 customers operation check removed',
      broken,
      (b) => testCustomersSupersededWriteNothingAndResolvesCleanly(b)
    );
  });

  await run('M6: customers guard always returns early (allow-half destroyed) => testCustomersNormalPathRendersItsRows FAILS', async () => {
    const broken = mutate(SRC, [[
      "      const data = (await this._get(url)).data || [];\n" +
      "      // Superseded by a later _loadCustomers() call (the search box changed\n" +
      "      // again before this one's response arrived)? Genuine early return,\n" +
      "      // same contract as _isStaleRender: write NOTHING -- not even\n" +
      "      // `this._customers` itself -- rather than let an older search's\n" +
      "      // result set land on top of whatever the latest call already wrote.\n" +
      "      // The assignment matters as much as the table paint: _openEditCustomer/\n" +
      "      // _viewCustomer/_deleteCustomer all look the clicked row up in\n" +
      "      // `this._customers` afterwards, so a stale assignment here would\n" +
      "      // silently corrupt what Edit/View/Delete act on even if the table\n" +
      "      // itself still matched the newer search.\n" +
      "      if (this._isStaleOp('customers', opToken)) return;\n",
      "      const data = (await this._get(url)).data || [];\n" +
      "      if (true) return; // MUTATED: every customers load treated as superseded\n",
    ]]);
    return provesMutation(
      'M6 customers guard always returns early',
      broken,
      (b) => testCustomersNormalPathRendersItsRows(b)
    );
  });

  await run('M7: reports operation check removed (post-Promise.all) => testReportsSupersededWriteNothingAndResolvesCleanly FAILS', async () => {
    const broken = mutate(SRC, [[
      "      ]);\n" +
      "      // Superseded while those five fetches were in flight? Write NOTHING\n" +
      "      // -- not even the KPI tiles -- rather than paint an older selection's\n" +
      "      // revenue, transactions or charts over whatever the latest selection\n" +
      "      // already wrote.\n" +
      "      if (this._isStaleOp('reports', opToken)) return;\n",
      "      ]);\n" +
      "      // MUTATED: operation-staleness check removed\n",
    ]]);
    return provesMutation(
      'M7 reports operation check removed (post-Promise.all)',
      broken,
      (b) => testReportsSupersededWriteNothingAndResolvesCleanly(b)
    );
  });

  await run('M8: reports guard always returns early (allow-half destroyed) => testReportsNormalPathRendersItsRows FAILS', async () => {
    const broken = mutate(SRC, [[
      "      ]);\n" +
      "      // Superseded while those five fetches were in flight? Write NOTHING\n" +
      "      // -- not even the KPI tiles -- rather than paint an older selection's\n" +
      "      // revenue, transactions or charts over whatever the latest selection\n" +
      "      // already wrote.\n" +
      "      if (this._isStaleOp('reports', opToken)) return;\n",
      "      ]);\n" +
      "      if (true) return; // MUTATED: every reports load treated as superseded\n",
    ]]);
    return provesMutation(
      'M8 reports guard always returns early',
      broken,
      (b) => testReportsNormalPathRendersItsRows(b)
    );
  });

  await run('M9: stock-accuracy operation check removed => testStockAccuracySupersededWriteNothingAndResolvesCleanly FAILS', async () => {
    const broken = mutate(SRC, [[
      "      const res = await this._get('/api/sub/retail/inventory/reconciliation');\n" +
      "      // Genuine early return, same contract as _isStaleRender: write\n" +
      "      // NOTHING -- not even into `s` -- rather than let an older check's\n" +
      "      // result (or refusal) land on top of whatever the latest check\n" +
      "      // already wrote.\n" +
      "      if (this._isStaleOp('stockAccuracy', opToken)) return;\n",
      "      const res = await this._get('/api/sub/retail/inventory/reconciliation');\n" +
      "      // MUTATED: operation-staleness check removed\n",
    ]]);
    return provesMutation(
      'M9 stock-accuracy operation check removed',
      broken,
      (b) => testStockAccuracySupersededWriteNothingAndResolvesCleanly(b)
    );
  });

  await run('M10: stock-accuracy guard always returns early (allow-half destroyed) => testStockAccuracyNormalPathRendersItsRows FAILS', async () => {
    const broken = mutate(SRC, [[
      "      const res = await this._get('/api/sub/retail/inventory/reconciliation');\n" +
      "      // Genuine early return, same contract as _isStaleRender: write\n" +
      "      // NOTHING -- not even into `s` -- rather than let an older check's\n" +
      "      // result (or refusal) land on top of whatever the latest check\n" +
      "      // already wrote.\n" +
      "      if (this._isStaleOp('stockAccuracy', opToken)) return;\n",
      "      const res = await this._get('/api/sub/retail/inventory/reconciliation');\n" +
      "      if (true) return; // MUTATED: every stock-accuracy check treated as superseded\n",
    ]]);
    return provesMutation(
      'M10 stock-accuracy guard always returns early',
      broken,
      (b) => testStockAccuracyNormalPathRendersItsRows(b)
    );
  });

  await run('M11: exception-stock operation check removed => testExceptionStockSupersededWriteNothingAndResolvesCleanly FAILS', async () => {
    const broken = mutate(SRC, [[
      "      const res = await this._get('/api/sub/retail/inventory/stock-exceptions');\n" +
      "      // Genuine early return, same contract as _isStaleRender: write\n" +
      "      // NOTHING -- not even into `s` -- rather than let an older check's\n" +
      "      // result land on top of whatever the latest check already wrote.\n" +
      "      if (this._isStaleOp('exceptionStock', opToken)) return;\n",
      "      const res = await this._get('/api/sub/retail/inventory/stock-exceptions');\n" +
      "      // MUTATED: operation-staleness check removed\n",
    ]]);
    return provesMutation(
      'M11 exception-stock operation check removed',
      broken,
      (b) => testExceptionStockSupersededWriteNothingAndResolvesCleanly(b)
    );
  });

  await run('M12: exception-stock guard always returns early (allow-half destroyed) => testExceptionStockNormalPathRendersItsRows FAILS', async () => {
    const broken = mutate(SRC, [[
      "      const res = await this._get('/api/sub/retail/inventory/stock-exceptions');\n" +
      "      // Genuine early return, same contract as _isStaleRender: write\n" +
      "      // NOTHING -- not even into `s` -- rather than let an older check's\n" +
      "      // result land on top of whatever the latest check already wrote.\n" +
      "      if (this._isStaleOp('exceptionStock', opToken)) return;\n",
      "      const res = await this._get('/api/sub/retail/inventory/stock-exceptions');\n" +
      "      if (true) return; // MUTATED: every oversold-stock check treated as superseded\n",
    ]]);
    return provesMutation(
      'M12 exception-stock guard always returns early',
      broken,
      (b) => testExceptionStockNormalPathRendersItsRows(b)
    );
  });

  await run('M13: exception-conflicts operation check removed => testExceptionConflictsSupersededWriteNothingAndResolvesCleanly FAILS', async () => {
    const broken = mutate(SRC, [[
      "      const res = await this._get('/api/sub/retail/inventory/sync-conflicts');\n" +
      "      // Genuine early return, same contract as _isStaleRender: write\n" +
      "      // NOTHING -- not even into `s` -- rather than let an older check's\n" +
      "      // result land on top of whatever the latest check already wrote.\n" +
      "      if (this._isStaleOp('exceptionConflicts', opToken)) return;\n",
      "      const res = await this._get('/api/sub/retail/inventory/sync-conflicts');\n" +
      "      // MUTATED: operation-staleness check removed\n",
    ]]);
    return provesMutation(
      'M13 exception-conflicts operation check removed',
      broken,
      (b) => testExceptionConflictsSupersededWriteNothingAndResolvesCleanly(b)
    );
  });

  await run('M14: exception-conflicts guard always returns early (allow-half destroyed) => testExceptionConflictsNormalPathRendersItsRows FAILS', async () => {
    const broken = mutate(SRC, [[
      "      const res = await this._get('/api/sub/retail/inventory/sync-conflicts');\n" +
      "      // Genuine early return, same contract as _isStaleRender: write\n" +
      "      // NOTHING -- not even into `s` -- rather than let an older check's\n" +
      "      // result land on top of whatever the latest check already wrote.\n" +
      "      if (this._isStaleOp('exceptionConflicts', opToken)) return;\n",
      "      const res = await this._get('/api/sub/retail/inventory/sync-conflicts');\n" +
      "      if (true) return; // MUTATED: every sync-conflicts check treated as superseded\n",
    ]]);
    return provesMutation(
      'M14 exception-conflicts guard always returns early',
      broken,
      (b) => testExceptionConflictsNormalPathRendersItsRows(b)
    );
  });

  await run('M15: exception-registry operation check removed => testExceptionRegistrySupersededWriteNothingAndResolvesCleanly FAILS', async () => {
    const broken = mutate(SRC, [[
      "      const res = await this._get('/api/sub/retail/account-quarantine');\n" +
      "      // Genuine early return, same contract as _isStaleRender: write\n" +
      "      // NOTHING -- not even into `s` -- rather than let an older check's\n" +
      "      // result land on top of whatever the latest check already wrote.\n" +
      "      if (this._isStaleOp('exceptionRegistry', opToken)) return;\n",
      "      const res = await this._get('/api/sub/retail/account-quarantine');\n" +
      "      // MUTATED: operation-staleness check removed\n",
    ]]);
    return provesMutation(
      'M15 exception-registry operation check removed',
      broken,
      (b) => testExceptionRegistrySupersededWriteNothingAndResolvesCleanly(b)
    );
  });

  await run('M16: exception-registry guard always returns early (allow-half destroyed) => testExceptionRegistryNormalPathRendersItsRows FAILS', async () => {
    const broken = mutate(SRC, [[
      "      const res = await this._get('/api/sub/retail/account-quarantine');\n" +
      "      // Genuine early return, same contract as _isStaleRender: write\n" +
      "      // NOTHING -- not even into `s` -- rather than let an older check's\n" +
      "      // result land on top of whatever the latest check already wrote.\n" +
      "      if (this._isStaleOp('exceptionRegistry', opToken)) return;\n",
      "      const res = await this._get('/api/sub/retail/account-quarantine');\n" +
      "      if (true) return; // MUTATED: every account-quarantine check treated as superseded\n",
    ]]);
    return provesMutation(
      'M16 exception-registry guard always returns early',
      broken,
      (b) => testExceptionRegistryNormalPathRendersItsRows(b)
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
