/**
 * Aura Retail — the CASH FLOW screen (financial_statements.py's THIRD
 * GL-derived statement, retail-hardware-viewports).
 *
 * core/retail/financial_statements.py::cash_flow derives a real, DIRECT-
 * method cash flow statement off the same GL retail_gl_trial_balance_ui_
 * test.js's/retail_profit_and_loss_ui_test.js's/retail_balance_sheet_ui_
 * test.js's own screens already surface. GET /api/sub/retail/reports/
 * cash-flow (retail_api.py's report_cash_flow) is this wave's new route.
 * See _renderCashFlow / _loadCashFlow in subsystem-retail.js for the full
 * reasoning; this file proves the screen actually does what those comments
 * claim.
 *
 * ── WHAT THIS FILE REFUSES TO LET SHIP ──────────────────────────────────────
 *
 * 1. A SILENTLY WRONG RECONCILIATION. `reconciliation.is_balanced` must
 *    render a GREEN "Reconciled" badge when true and a LOUD RED "Not
 *    Reconciled" badge (plus the signed difference) when false -- never the
 *    reverse, and never a badge that renders green regardless of the flag.
 *    This is the one figure the whole screen exists to surface (matching
 *    retail_balance_sheet_ui_test.js's identical posture for its own
 *    balance_check), so it is mutation-proved in BOTH directions below.
 * 2. AN EMPTY PERIOD READ AS "ZERO MOVEMENT". `has_activity === false` must
 *    render "nothing moved", never a statement full of formatted zeros with
 *    a trivially-agreeing reconciliation badge over them -- two different
 *    claims about the books.
 * 3. A REFUSAL RENDERED AS AN EMPTY PERIOD. report_cash_flow returns a 400
 *    with no `data` key when a date is invalid (StatementPeriodError) --
 *    the exact class of miss retail_profit_and_loss_ui_test.js/retail_
 *    balance_sheet_ui_test.js already guard for their own sibling screens.
 *    `resp.data || {}` on that envelope resolves to `{}`, and `{}.has_
 *    activity` is falsy, so an unread refusal would render as "nothing
 *    moved" -- a true-sounding statement about a request the server never
 *    actually evaluated.
 * 4. A LINE LABEL REACHING innerHTML RAW. A cash-movement label is server-
 *    derived text (a `source_type` reshaped into a title), exactly the
 *    trust boundary the sibling GL screens already guard for account names.
 * 5. A REPORTS-CAPABILITY GATE THAT DOES NOT ACTUALLY GATE. A viewer
 *    without retail.reports must see the capability-restricted panel and
 *    the screen must not even ATTEMPT a fetch -- asserted by counting
 *    calls, not just reading the rendered outcome.
 * 6. A LABEL THAT NEVER WENT THROUGH t(). Every user-visible string on the
 *    statement and the nothing-moved state must be a catalog lookup, not a
 *    hardcoded literal.
 * 7. THE SCREEN BECOMING UNREACHABLE. The router case and the nav entry
 *    (app-shell.js, capability: 'retail.reports') must both exist.
 *
 * ── MUTATION-PROVED ─────────────────────────────────────────────────────────
 * Every behavioural claim below is re-run against a DELIBERATELY BROKEN copy
 * of subsystem-retail.js and is required to FAIL there. See
 * testEveryGuardIsProvenByBreakingIt at the bottom -- its own console
 * output, printed on a passing run, names each mutation and the assertion
 * message it tripped. #3 below ("make the reconciliation always report
 * agreement") is the sharpest one: it forces the badge-selection ternary to
 * ALWAYS pick the green "Reconciled" branch, regardless of what the server
 * actually reported, and is proved against a fixture where the server says
 * the two figures do NOT agree -- the deny-half of ENGINEERING.md's "prove
 * both directions of anything that both denies and allows", and the single
 * most likely way this exact ternary goes wrong in practice (rendering
 * green is what an unwired false-branch does by default).
 *
 * WHY THIS FILE DOES NOT ALSO TRY TO MUTATION-PROVE "A CREDIT SALE COUNTS AS
 * CASH IN" HERE. That claim lives entirely in core/retail/financial_
 * statements.py::cash_flow's own SQL (which account the direct-method query
 * joins against) -- this screen only ever renders whatever `total_cash_in`/
 * `operating_activities.cash_in` the server already computed. It is
 * mutation-proved at the BACKEND level instead, in retail_cash_flow_test.py
 * (performed by hand against financial_statements.py during development,
 * matching retail_balance_sheet_test.py's own established convention for
 * that file -- see this task's own report for the exact RED output quoted).
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_cash_flow_ui_test.js
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

const SECTION_ID = 'cash-flow';
const CF_URL = '/api/sub/retail/reports/cash-flow';
const BRANCHES_URL = '/api/sub/retail/branches';

// ─────────────────────────────────────────────────────────────────────────────
// FIXTURES — the exact shape report_cash_flow (retail_api.py) and cash_flow
// (financial_statements.py) put on the wire. Success is {success:true,
// data:{...}} (NOT {status:'success', ...} -- that shape is what a REFUSAL
// uses, {status:'error', message}). Getting this backwards would make every
// "renders real values" assertion below pass against a screen that is
// silently reading the wrong envelope.
// ─────────────────────────────────────────────────────────────────────────────

const CASH_IN_LINES = [
  { label: 'Cash Sales Receipts', amount: 3900.000, source_types: ['sale'] },
  { label: 'Customer Account Payments Received', amount: 500.000, source_types: ['customer_payment'] },
];
const CASH_OUT_LINES = [
  { label: 'Cash Paid for Inventory Purchases', amount: 800.000, source_types: ['purchase_order'] },
  { label: 'Payments to Suppliers', amount: 2200.000, source_types: ['po_payment', 'supplier_payment'] },
  { label: 'Refunds Paid to Customers', amount: 300.000, source_types: ['return'] },
];

/* Internally consistent, the same discipline retail_balance_sheet_ui_test.
 * js's BS_OK fixture holds itself to: opening_cash (2100) + net_change_in_
 * cash (4400 - 3300 = 1100) = closing_cash (3200), and reconciliation
 * reports agreement with difference 0. */
const CF_OK = {
  success: true,
  data: {
    currency: 'JOD',
    method: 'direct',
    period: { start_date: '2026-09-01', end_date: '2026-09-22', branch_id: null },
    opening_cash: 2100.000,
    closing_cash: 3200.000,
    total_cash_in: 4400.000,
    total_cash_out: 3300.000,
    net_change_in_cash: 1100.000,
    operating_activities: {
      cash_in: { total: 4400.000, lines: CASH_IN_LINES },
      cash_out: { total: 3300.000, lines: CASH_OUT_LINES },
      net: 1100.000,
    },
    investing_activities: null,
    financing_activities: null,
    investing_and_financing_note:
      "No investing or financing accounts exist in this company's chart of accounts, so every cash movement this period is operating.",
    reconciliation: {
      opening_cash: 2100.000, net_change_in_cash: 1100.000,
      computed_closing_cash: 3200.000, closing_cash: 3200.000,
      is_balanced: true, difference: 0,
    },
    has_activity: true,
  },
};

/* A statement the reconciliation itself must report LOUDLY -- computed_
 * closing_cash no longer equals closing_cash, is_balanced is false, and
 * difference carries the exact gap (65.000), matching the shape retail_
 * cash_flow_test.py's own boundary-arithmetic backend test proves at the
 * function level -- this fixture is the UI-level sibling of that proof. */
const CF_NOT_RECONCILED = {
  success: true,
  data: Object.assign({}, CF_OK.data, {
    closing_cash: 3265.000,
    reconciliation: {
      opening_cash: 2100.000, net_change_in_cash: 1100.000,
      computed_closing_cash: 3200.000, closing_cash: 3265.000,
      is_balanced: false, difference: -65.000,
    },
  }),
};

/* NOT ONE journal posted a cash movement in the period -- has_activity:
 * false. Every money field is 0 exactly as cash_flow would actually return
 * them for an empty period (see retail_cash_flow_test.py's own
 * test_empty_period_reads_as_nothing_moved) -- the screen's job is to never
 * SHOW these zeros as the statement. */
const CF_NO_ACTIVITY = {
  success: true,
  data: {
    currency: 'JOD',
    method: 'direct',
    period: { start_date: '2026-01-01', end_date: '2026-01-01', branch_id: null },
    opening_cash: 0, closing_cash: 0, total_cash_in: 0, total_cash_out: 0, net_change_in_cash: 0,
    operating_activities: {
      cash_in: { total: 0, lines: [] },
      cash_out: { total: 0, lines: [] },
      net: 0,
    },
    investing_activities: null,
    financing_activities: null,
    investing_and_financing_note: "No investing or financing accounts exist in this company's chart of accounts.",
    reconciliation: {
      opening_cash: 0, net_change_in_cash: 0, computed_closing_cash: 0, closing_cash: 0,
      is_balanced: true, difference: 0,
    },
    has_activity: false,
  },
};

/* Same shape retail_gl_trial_balance_ui_test.js's TB_XSS_OK / retail_
 * balance_sheet_ui_test.js's BS_XSS use for their own malicious-name
 * fixtures: breaks out of a double-quoted attribute AND carries a classic
 * script-injection vector, on a LINE LABEL rather than an account name. */
const MALICIOUS_LABEL = `"><img src=x onerror=alert(document.cookie)>`;
const CF_XSS = {
  success: true,
  data: Object.assign({}, CF_OK.data, {
    operating_activities: Object.assign({}, CF_OK.data.operating_activities, {
      cash_in: { total: 400.000, lines: [{ label: MALICIOUS_LABEL, amount: 400.000, source_types: ['sale'] }] },
    }),
  }),
};

/* report_cash_flow's OWN refusal shape (StatementPeriodError -> 400):
 * {status:'error', message}, no `data`, no `success`. */
const CF_VALIDATION_ERROR = {
  status: 'error',
  message: "start_date 'not-a-date' is not a valid date in 'YYYY-MM-DD' format.",
};

// ─────────────────────────────────────────────────────────────────────────────
// THE SANDBOX — the real subsystem-retail.js, never a reimplementation
// ─────────────────────────────────────────────────────────────────────────────

function makeStub(over) {
  return Object.assign({
    innerHTML: '', outerHTML: '', textContent: '', value: '', id: '', disabled: false,
    style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {},
    querySelectorAll() { return []; }, addEventListener() {}, removeEventListener() {},
    focus() {}, blur() {}, remove() {}, closest() { return null; }, getContext() { return {}; },
  }, over || {});
}

/**
 * @param {object} opts
 *   source       — subsystem-retail.js text (a mutant, for the proofs below)
 *   capabilities — what SubsystemApp.hasCapability answers yes to
 *   role         — SubsystemApp.role
 *   cf           — the GET .../reports/cash-flow payload (or an Error
 *                  instance, to exercise the catch path)
 *   branches     — the GET .../branches payload (array); defaults to []
 *   t            — override for window.t (default: identity) -- the
 *                  functional "did this go through t()" proof uses a
 *                  MARKING t, matching retail_balance_sheet_ui_test.js's
 *                  markingT technique exactly.
 */
function loadRetailSystem(opts) {
  const o = opts || {};
  const calls = [];
  const els = Object.create(null);
  const getEl = (id) => (els[id] || (els[id] = makeStub({ id })));

  const sandbox = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    t: o.t || ((s) => s),
    fetch: (url, init) => {
      const method = ((init && init.method) || 'GET').toUpperCase();
      const u = String(url);
      calls.push({ method, url: u });
      if (u.indexOf(CF_URL) !== -1) {
        const payload = o.cf === undefined ? CF_OK : o.cf;
        if (payload instanceof Error) return Promise.reject(payload);
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
      }
      if (u.indexOf(BRANCHES_URL) !== -1) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ success: true, data: o.branches || [] }),
        });
      }
      return Promise.resolve({
        ok: false, status: 404,
        json: () => Promise.resolve({ status: 'error', message: 'unmapped fixture route: ' + method + ' ' + u }),
      });
    },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    URLSearchParams,
    document: {
      activeElement: null,
      getElementById(id) { if (id === 'ret-styles') return null; return getEl(id); },
      createElement() { return makeStub(); },
      querySelector() { return makeStub(); },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
  };
  sandbox.SubsystemApp = {
    active: 'retail', showToast() {}, _navigate() {},
    role: o.role === undefined ? 'admin' : o.role,
    hasCapability: (code) => (o.capabilities || ['retail.reports']).includes(code),
  };
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(o.source || SRC, sandbox, { filename: RETAIL_JS });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return { rs: sandbox.RetailSystem, calls, els };
}

async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

/** Render the screen through the ROUTER, so the route id is load-bearing
    (matching retail_balance_sheet_ui_test.js's own renderScreen). */
async function renderScreen(opts) {
  const ctx = loadRetailSystem(opts);
  const host = makeStub({ id: 'sub-content' });
  ctx.els['sub-content'] = host;
  await ctx.rs.render(SECTION_ID);
  await settle();
  ctx.frame = host.innerHTML;
  ctx.body = () => (ctx.els['cf-body'] ? ctx.els['cf-body'].innerHTML : '');
  ctx.branchOptions = () => (ctx.els['cf-branch'] ? ctx.els['cf-branch'].innerHTML : '');
  return ctx;
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION HARNESS (same shape as retail_balance_sheet_ui_test.js)
// ─────────────────────────────────────────────────────────────────────────────

/** Apply textual mutations, requiring each anchor to be present EXACTLY once
    -- an anchor matching 0 or 2+ times means the proof below would pass (or
    fail) for the wrong reason, so this refuses to run rather than guess. */
function mutate(pairs) {
  let src = SRC;
  for (const [find, replace] of pairs) {
    const hits = src.split(find).length - 1;
    assert.strictEqual(
      hits, 1,
      `Mutation anchor occurs ${hits} time(s), expected exactly 1:\n  ${JSON.stringify(find)}\n\n` +
      'A mutation that no longer applies would let the proof below pass while proving ' +
      'nothing. Re-anchor it.'
    );
    src = src.replace(find, replace);
  }
  return src;
}

/** Run `check` against a broken build and require it to FAIL. */
async function provesMutation(what, mutations, check) {
  const broken = mutate(mutations);
  let threw = null;
  try {
    await check(broken);
  } catch (err) {
    threw = err;
  }
  assert.ok(
    threw,
    `MUTATION SURVIVED — ${what}\n` +
    'The guard for this passed against a build with the behaviour deliberately broken, so ' +
    'it is not actually watching it. Fix the check, not the mutation.'
  );
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 100)}]`;
}

// ─────────────────────────────────────────────────────────────────────────────
// CHECKS
// ─────────────────────────────────────────────────────────────────────────────

function testTheScreenIsReachable(src) {
  const source = src || SRC;
  const start = source.indexOf('  render(sectionId) {');
  assert.ok(start !== -1, 'Could not find RetailSystem.render(sectionId).');
  const body = source.slice(start, source.indexOf('\n  },', start));
  assert.ok(
    new RegExp(`case\\s+'${SECTION_ID}'\\s*:`).test(body),
    `RetailSystem.render() has no case for '${SECTION_ID}', so the screen is unreachable through ` +
    'the router.'
  );

  const shell = fs.readFileSync(APP_SHELL_JS, 'utf8');
  const entry = shell.split('\n').find((l) => l.indexOf(`id: '${SECTION_ID}'`) !== -1);
  assert.ok(entry, `app-shell.js has no nav entry for '${SECTION_ID}'.`);
  assert.ok(/capability:\s*'retail\.reports'/.test(entry),
    `The '${SECTION_ID}' nav entry does not gate on retail.reports, but report_cash_flow ` +
    `carries @mt_require_capability(CAP_REPORTS). Entry:\n  ${entry.trim()}`);
}

async function testCapabilityRestrictedWhenMissingReports() {
  // No retail.reports grant at all -- hasCapability() must refuse before any
  // fetch is attempted (neither the cash-flow fetch nor the branch-filter
  // fetch).
  const ctx = await renderScreen({ capabilities: ['retail.sell'] });
  assert.strictEqual(
    ctx.calls.length, 0,
    `the capability guard let ${ctx.calls.length} fetch(es) through for a viewer without ` +
    'retail.reports -- the guard must refuse BEFORE any network call, not just hide the ' +
    'result. calls: ' + JSON.stringify(ctx.calls)
  );
  assert.ok(ctx.frame.indexOf('id="cf-body"') === -1,
    'the statement rendered even though the viewer lacks retail.reports.');
  assert.ok(ctx.frame.indexOf('Cash Flow') !== -1,
    'the capability-restricted panel must still name the screen it is standing in for.');
}

async function testStatementRendersRealValuesAndTotalsAreCorrect() {
  const ctx = await renderScreen({ cf: CF_OK });
  const body = ctx.body();

  for (const l of CASH_IN_LINES.concat(CASH_OUT_LINES)) {
    assert.ok(body.indexOf(l.label) !== -1, `line label "${l.label}" missing.\n${body}`);
    // Traceability: the raw source_type(s) that fed this label must also
    // render, not just the pretty label.
    for (const st of l.source_types) {
      assert.ok(body.indexOf(st) !== -1, `source_type "${st}" for "${l.label}" missing.\n${body}`);
    }
  }

  // The figures a shopkeeper actually reads.
  assert.ok(body.indexOf('JD 2100.000') !== -1, `opening_cash not rendered.\n${body}`);
  assert.ok(body.indexOf('JD 3200.000') !== -1, `closing_cash not rendered.\n${body}`);
  assert.ok(body.indexOf('JD 4400.000') !== -1, `total cash in not rendered.\n${body}`);
  assert.ok(body.indexOf('JD 3300.000') !== -1, `total cash out not rendered.\n${body}`);
  assert.ok(body.indexOf('1100.000') !== -1, `net_change_in_cash (1100.000) not rendered.\n${body}`);

  // A reconciled statement shows the GREEN badge, never the red one.
  assert.ok(body.indexOf('ret-badge-green') !== -1, `no green reconciliation badge rendered.\n${body}`);
  assert.ok(body.indexOf('ret-badge-red') === -1, `a red badge rendered on a RECONCILED statement.\n${body}`);
  assert.ok(body.indexOf('Reconciled') !== -1, `"Reconciled" label missing.\n${body}`);
}

async function testNotReconciledRendersLoudlyWithTheDifference() {
  const ctx = await renderScreen({ cf: CF_NOT_RECONCILED });
  const body = ctx.body();
  assert.ok(body.indexOf('ret-badge-red') !== -1, `no red "Not Reconciled" badge rendered.\n${body}`);
  assert.ok(body.indexOf('Not Reconciled') !== -1, `"Not Reconciled" label missing.\n${body}`);
  assert.ok(body.indexOf('ret-badge-green') === -1, `a green badge rendered on a NOT-RECONCILED statement.\n${body}`);
  // The signed difference (-65.000) must be shown, not merely the fact of
  // disagreement.
  assert.ok(body.indexOf('65.000') !== -1, `the difference (65.000) is not shown.\n${body}`);
  assert.ok(body.indexOf('Difference') !== -1, `the "Difference" label is missing.\n${body}`);
}

async function testNoActivityRendersNothingMovedNotZeros() {
  const ctx = await renderScreen({ cf: CF_NO_ACTIVITY });
  const body = ctx.body();
  assert.ok(body.indexOf('Loading') === -1,
    'the statement is stuck on "Loading…" with a resolved has_activity:false response.');
  assert.ok(body.indexOf('Nothing moved in this period') !== -1,
    `expected the nothing-moved empty-state title; got:\n${body}`);
  assert.ok(body.indexOf('This is not a zero movement') !== -1,
    `expected the empty-state hint distinguishing "no data" from "moved at zero"; got:\n${body}`);
  // The one thing this whole test exists to refuse: a row of formatted
  // zeros standing in for the real statement, WITH a trivially-agreeing
  // reconciliation badge sitting over them as if that meant something.
  assert.ok(body.indexOf('JD 0.000') === -1,
    `a formatted zero money figure leaked into the "nothing moved" state -- this must read as ` +
    `an absence of data, never as a computed zero.\n${body}`);
  assert.ok(body.indexOf('Reconciled') === -1 && body.indexOf('Not Reconciled') === -1,
    `a reconciliation badge rendered over an empty period -- the nothing-moved state replaces ` +
    `the whole statement, including the reconciliation, it does not sit beside a trivially-` +
    `agreeing one.\n${body}`);
}

async function testValidationRefusalIsNotRenderedAsNothingMoved() {
  // The bug class this screen's own comment names: report_cash_flow's 400
  // (StatementPeriodError) carries no `data` key, and `{}.has_activity` is
  // undefined -- falsy, same as CF_NO_ACTIVITY's real `false`. A screen
  // that does not read `resp.status === 'error'` explicitly would render
  // this exactly like an empty period: a false statement about the books,
  // standing in for "the server refused the request".
  const ctx = await renderScreen({ cf: CF_VALIDATION_ERROR });
  const body = ctx.body();
  assert.ok(body.indexOf('Nothing moved in this period') === -1,
    `a 400 refusal rendered as "nothing moved" -- a refusal must never be shown as a true ` +
    `statement about an empty period.\n${body}`);
  assert.ok(body.indexOf('not a valid date') !== -1,
    `the server's own refusal message should reach the screen; got:\n${body}`);
}

async function testMaliciousLineLabelIsEscaped() {
  const ctx = await renderScreen({ cf: CF_XSS });
  assert.ok(ctx.body().indexOf('<img src=x onerror=') === -1,
    `a raw, unescaped <img onerror=...> tag reached the statement's innerHTML:\n${ctx.body()}`);
  assert.ok(ctx.body().indexOf('&lt;img src=x onerror=') !== -1,
    `the malicious line label should appear ESCAPED (as text, not markup):\n${ctx.body()}`);
}

async function testFetchFailureShowsVisibleError() {
  const ctx = await renderScreen({ cf: new Error('network down') });
  assert.ok(ctx.body().indexOf('Loading') === -1,
    'a failed fetch must not leave the statement silently stuck on "Loading…" forever -- the ' +
    'exact bug class _loadPartyLedger/_loadProfitAndLoss/_loadBalanceSheet were already fixed for once.');
  assert.ok(ctx.body().indexOf('Could not load this list') !== -1,
    `expected a visible error message; got:\n${ctx.body()}`);
}

async function testMissingDatesAreRefusedClientSide() {
  // No #cf-from/#cf-to values set at all (an operator who lands on the
  // screen with a fresh els stub, matching _loadBalanceSheet's identical
  // "no default date" refusal for its own #bs-as-of).
  const ctx = loadRetailSystem({ cf: CF_OK });
  const body = makeStub({ id: 'cf-body' });
  ctx.els['cf-body'] = body;
  await ctx.rs._loadCashFlow();
  await settle();
  assert.strictEqual(ctx.calls.length, 0,
    `report_cash_flow requires both dates -- the screen must not send a request it already ` +
    `knows will be refused. calls: ${JSON.stringify(ctx.calls)}`);
  assert.ok(body.innerHTML.indexOf('Choose a start and end date') !== -1,
    `expected the client-side date prompt; got:\n${body.innerHTML}`);
}

// A t() that proves it ran. Any label rendered through t() comes back
// wrapped; a hardcoded English literal in the template does not. Same
// technique, same marker glyphs, as retail_balance_sheet_ui_test.js's
// markingT proof.
const markingT = (s) => '‹' + s + '›';

async function testEveryUserVisibleStringGoesThroughT() {
  const statement = await renderScreen({ cf: CF_OK, t: markingT });
  const statementLabels = [
    'Cash Flow', 'All branches', 'From', 'To',
    'Opening Cash', 'Net Change in Cash', 'Closing Cash', 'Reconciled',
    'Description', 'Type', 'Amount', 'Total',
    'Cash In (Operating)', 'Cash Out (Operating)',
    'This chart of accounts has no investing or financing accounts (no loans, no owner draws), so every cash movement shown here is from day-to-day operations.',
  ];
  const haystack = statement.frame + statement.body() + statement.branchOptions();
  const missingFromStatement = statementLabels.filter((label) => haystack.indexOf('‹' + label + '›') === -1);
  assert.deepStrictEqual(
    missingFromStatement, [],
    'These strings never went through t() on the statement render: ' +
    JSON.stringify(missingFromStatement) +
    '. A hardcoded literal here renders as permanent English on an Arabic till, invisible to ' +
    'review because everything nearby DOES say t(...).'
  );

  const notReconciled = await renderScreen({ cf: CF_NOT_RECONCILED, t: markingT });
  assert.ok(
    notReconciled.body().indexOf('‹Not Reconciled›') !== -1 && notReconciled.body().indexOf('‹Difference›') !== -1,
    `the not-reconciled badge/label did not go through t().\n${notReconciled.body()}`);

  const empty = await renderScreen({ cf: CF_NO_ACTIVITY, t: markingT });
  const emptyLabels = [
    'Nothing moved in this period',
    'No sale, return, payment or purchase moved cash in this date range. This is not a zero movement -- there is nothing to measure yet.',
  ];
  const missingFromEmpty = emptyLabels.filter((label) => empty.body().indexOf('‹' + label + '›') === -1);
  assert.deepStrictEqual(
    missingFromEmpty, [],
    'These strings never went through t() on the nothing-moved state: ' + JSON.stringify(missingFromEmpty)
  );

  const restricted = await renderScreen({ capabilities: ['retail.sell'], t: markingT });
  assert.ok(
    restricted.frame.indexOf('‹Sales totals and reports are limited to managers and the store owner. Open the till to start ringing sales.›') !== -1,
    `the capability-restricted message did not go through t().\n${restricted.frame}`
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION PROOFS — every guard above, proven by breaking the thing it watches
// ─────────────────────────────────────────────────────────────────────────────

async function testEveryGuardIsProvenByBreakingIt() {
  const proved = [];

  proved.push(await provesMutation(
    '1. the router case for cash-flow is deleted (screen becomes unreachable)',
    [["      case 'cash-flow': return this._renderCashFlow(c);\n", '']],
    async (src) => testTheScreenIsReachable(src)));

  proved.push(await provesMutation(
    '2. the retail.reports capability guard is deleted from _renderCashFlow',
    [[`    if (window.SubsystemApp && !SubsystemApp.hasCapability('retail.reports')) {
      return this._renderCapabilityRestricted(c, {
        icon: 'banknote',
        title: t('Cash Flow'),
        message: t('Sales totals and reports are limited to managers and the store owner. Open the till to start ringing sales.'),
      });
    }
`, '']],
    async (src) => {
      const ctx = await renderScreen({ source: src, capabilities: ['retail.sell'] });
      assert.strictEqual(ctx.calls.length, 0, 'guard removed but no fetch happened anyway');
    }));

  // ── THE RECONCILIATION ITSELF — forced to ALWAYS read as agreeing ───────
  // The DENY-half (a real disagreement must show red) is the one a bug is
  // most likely to break silently, because rendering green is what every
  // badge-selection ternary does when nobody wires the false branch at
  // all. Forcing the ternary's condition reproduces exactly that: a build
  // that compiles, renders a statement, and simply never shows red -- proved
  // against CF_NOT_RECONCILED, a server response that explicitly says the
  // two figures disagree.
  proved.push(await provesMutation(
    '3. THE RECONCILIATION — the badge always renders green, even when rec.is_balanced is false',
    [['const reconciledBadge = rec.is_balanced\n        ? this._badge(t(\'Reconciled\'), \'green\')',
      'const reconciledBadge = true\n        ? this._badge(t(\'Reconciled\'), \'green\')']],
    async (src) => {
      const ctx = await renderScreen({ source: src, cf: CF_NOT_RECONCILED });
      assert.ok(ctx.body().indexOf('ret-badge-red') !== -1, 'still shows red somehow');
    }));

  // ── HONESTY FLAG — empty period renders zeros, not "nothing moved" ──────
  proved.push(await provesMutation(
    '4. HONESTY FLAG — has_activity:false falls through to the (all-zero) statement instead of the nothing-moved state',
    [[
      "      if (!data.has_activity) {\n        body.innerHTML = this._emptyState({\n          icon: 'banknote',",
      "      if (false) {\n        body.innerHTML = this._emptyState({\n          icon: 'banknote',",
    ]],
    async (src) => {
      const ctx = await renderScreen({ source: src, cf: CF_NO_ACTIVITY });
      assert.ok(ctx.body().indexOf('Nothing moved in this period') !== -1,
        'still shows the nothing-moved state somehow');
      assert.ok(ctx.body().indexOf('JD 0.000') === -1,
        'still avoided rendering a formatted zero somehow');
    }));

  proved.push(await provesMutation(
    '5. this._esc() is removed from the line-label interpolation',
    [[
      '      const lineRows = (lines) => lines.map(l => `<tr>\n        <td style="font-weight:600">${this._esc(l.label)}</td>\n',
      '      const lineRows = (lines) => lines.map(l => `<tr>\n        <td style="font-weight:600">${l.label}</td>\n',
    ]],
    async (src) => {
      const ctx = await renderScreen({ source: src, cf: CF_XSS });
      assert.ok(ctx.body().indexOf('<img src=x onerror=') === -1, 'still escaped somehow');
    }));

  proved.push(await provesMutation(
    '6. the client-side missing-dates refusal is deleted (a request the server will 400 is sent anyway)',
    [["    if (!startDate || !endDate) {\n      if (body) body.innerHTML = `<div style=\"text-align:center;color:var(--text-muted);padding:30px\">${t('Choose a start and end date.')}</div>`;\n      return;\n    }\n\n",
      '']],
    async (src) => {
      const ctx = loadRetailSystem({ source: src, cf: CF_OK });
      const body = makeStub({ id: 'cf-body' });
      ctx.els['cf-body'] = body;
      await ctx.rs._loadCashFlow();
      await settle();
      assert.strictEqual(ctx.calls.length, 0, 'still refused client-side somehow');
    }));

  console.log(`PASS: ${proved.length} guards proved by breaking the behaviour they watch:`);
  for (const line of proved) console.log('      ' + line);
}

// ─────────────────────────────────────────────────────────────────────────────
// RUNNER — every check runs, every failure is collected
// ─────────────────────────────────────────────────────────────────────────────

const EXPECTED_CHECKS = 9;

async function main() {
  const checks = [
    ['the screen is reachable and gated on retail.reports', testTheScreenIsReachable],
    ['capability-restricted panel shown, no fetch, when retail.reports is missing', testCapabilityRestrictedWhenMissingReports],
    ['the statement renders real values and the totals are arithmetically correct', testStatementRendersRealValuesAndTotalsAreCorrect],
    ['a not-reconciled statement renders loudly, with the signed difference', testNotReconciledRendersLoudlyWithTheDifference],
    ['has_activity:false renders "nothing moved", never a set of zeros', testNoActivityRendersNothingMovedNotZeros],
    ['a validation refusal (400) is not rendered as "nothing moved"', testValidationRefusalIsNotRenderedAsNothingMoved],
    ['a malicious line label is escaped', testMaliciousLineLabelIsEscaped],
    ['a fetch failure shows a visible error, not a stuck Loading…', testFetchFailureShowsVisibleError],
    ['missing dates are refused client-side, no request sent', testMissingDatesAreRefusedClientSide],
    ['every user-visible string goes through t()', testEveryUserVisibleStringGoesThroughT],
    ['every guard above is proven by breaking it', testEveryGuardIsProvenByBreakingIt],
  ];

  const failures = [];
  for (const [name, fn] of checks) {
    try {
      await fn();
    } catch (err) {
      failures.push(name);
      console.error(`FAIL: ${name}`);
      console.error('      ' + String((err && err.message) || err).replace(/\n/g, '\n      '));
    }
  }

  if (checks.length - 1 < EXPECTED_CHECKS) {
    console.error(`FAIL: retail_cash_flow_ui_test.js ran only ${checks.length - 1} of ` +
      `${EXPECTED_CHECKS} known behavioural checks (plus the mutation-proof meta-check).`);
    process.exitCode = 1;
    return;
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_cash_flow_ui_test.js — ${failures.length} of ` +
      `${checks.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_cash_flow_ui_test.js — ${checks.length} checks`);
}

if (require.main === module) {
  main().catch((err) => {
    console.error('FAIL: retail_cash_flow_ui_test.js (runner)');
    console.error(err && err.stack || err);
    process.exitCode = 1;
  });
}
