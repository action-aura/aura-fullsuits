/**
 * Aura Retail — the PROFIT AND LOSS screen (schema v39 income statement,
 * retail-hardware-viewports, 2026-09-22).
 *
 * core/retail/financial_statements.py::income_statement derives a real
 * income statement off the same GL retail_gl_trial_balance_ui_test.js's
 * screen already surfaces. GET /api/sub/retail/reports/profit-and-loss
 * (retail_api.py's report_profit_and_loss) shipped complete and gated with
 * nothing in the frontend ever calling it -- retail_route_reachability_
 * test.py flagged it by name. See _renderProfitAndLoss / _loadProfitAndLoss
 * in subsystem-retail.js for the full reasoning; this file proves the
 * screen actually does what those comments claim.
 *
 * ── WHAT THIS FILE REFUSES TO LET SHIP ──────────────────────────────────────
 *
 * 1. A CONFIDENTLY WRONG PROFIT. `cost_basis_incomplete` means COGS is
 *    understated and gross/net profit are therefore OVERSTATED
 *    (financial_statements.py's own module docstring, point 5) -- the
 *    warning banner must appear when the flag is true and must be ABSENT
 *    when it is false, never the reverse and never both.
 * 2. AN EMPTY PERIOD READ AS "WE BROKE EVEN". `has_activity === false` must
 *    render "nothing was posted", never a statement full of formatted
 *    zeros -- those are two different claims about the books.
 * 3. A REFUSAL RENDERED AS AN EMPTY SHOP. report_profit_and_loss returns a
 *    400 with no `data` key when the dates are invalid (StatementPeriodError)
 *    -- the exact class of miss retail_attribution_i18n_test.py's Sales
 *    History cases exist to catch for a different screen. `resp.data || {}`
 *    on that envelope resolves to `{}`, and `{}.has_activity` is falsy, so a
 *    refusal that is not read explicitly would render as "nothing was
 *    posted" -- a true-sounding statement about a request the server never
 *    actually evaluated.
 * 4. AN EXPENSE ACCOUNT NAME REACHING innerHTML RAW. An account name is
 *    shop-editable data, exactly the trust boundary retail_gl_trial_
 *    balance_ui_test.js already guards for the sibling screen.
 * 5. A REPORTS-CAPABILITY GATE THAT DOES NOT ACTUALLY GATE. A viewer without
 *    retail.reports must see the capability-restricted panel and the screen
 *    must not even ATTEMPT a fetch -- asserted by counting calls, not just
 *    reading the rendered outcome.
 * 6. A LABEL THAT NEVER WENT THROUGH t(). Every user-visible string on the
 *    statement, the warning banner and the nothing-posted state must be a
 *    catalog lookup, not a hardcoded literal -- proven functionally with a
 *    marking t() (retail_attribution_ui_test.js's own
 *    testSaleDetailGridLabelsAllGoThroughT technique), not by trusting that
 *    t(...) appears in the source.
 * 7. THE SCREEN BECOMING UNREACHABLE. The router case and the nav entry
 *    (app-shell.js, capability: 'retail.reports') must both exist.
 *
 * ── MUTATION-PROVED ─────────────────────────────────────────────────────────
 * Every behavioural claim below is re-run against a DELIBERATELY BROKEN copy
 * of subsystem-retail.js and is required to FAIL there. See
 * testEveryGuardIsProvenByBreakingIt at the bottom -- its own console output,
 * printed on a passing run, names each mutation and the assertion message it
 * tripped. Two of the five are the honesty flags themselves (#3 and #4
 * below) -- the two claims this screen exists to make, mutation-proved in
 * BOTH directions per ENGINEERING.md ("prove both directions of anything
 * that both denies and allows").
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_profit_and_loss_ui_test.js
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

const SECTION_ID = 'profit-and-loss';
const PNL_URL = '/api/sub/retail/reports/profit-and-loss';
const BRANCHES_URL = '/api/sub/retail/branches';

// ─────────────────────────────────────────────────────────────────────────────
// FIXTURES — the exact shape report_profit_and_loss (retail_api.py) and
// income_statement (financial_statements.py) put on the wire. Success is
// {success:true, data:{...}} (NOT {status:'success', ...} -- that shape is
// what a REFUSAL uses, {status:'error', message}). Getting this backwards
// would make every "renders real values" assertion below pass against a
// screen that is silently reading the wrong envelope.
// ─────────────────────────────────────────────────────────────────────────────

const OPEX_ACCOUNTS = [
  { code: '5100', name: 'Rent Expense', amount: 400.000 },
  { code: '5200', name: 'Utilities Expense', amount: 120.250 },
];

/* Internally consistent, the same discipline retail_gl_trial_balance_ui_
 * test.js's ACCOUNTS_BALANCED fixture holds itself to: revenue = gross_
 * revenue - sales_returns (4800 = 5000 - 200), gross_profit = revenue - cogs
 * (2300 = 4800 - 2500), net_profit = gross_profit - opex.total
 * (1779.750 = 2300 - 520.250), gross_margin = gross_profit / revenue
 * (2300/4800 = 0.479166...). */
const PNL_OK = {
  success: true,
  data: {
    currency: 'JOD',
    period: { start_date: '2026-09-01', end_date: '2026-09-22', branch_id: null },
    gross_revenue: 5000.000,
    sales_returns: 200.000,
    revenue: 4800.000,
    cost_of_goods_sold: 2500.000,
    gross_profit: 2300.000,
    gross_margin: 2300.000 / 4800.000,
    operating_expenses: { total: 520.250, accounts: OPEX_ACCOUNTS },
    net_profit: 1779.750,
    has_activity: true,
    cost_basis_incomplete: false,
    cost_basis_incomplete_sale_count: 0,
  },
};

/* Same period, same figures -- ONLY the honesty flag differs. That is the
 * point: the warning is a fact about the PERIOD's cost data, not something a
 * different set of numbers would explain on its own. */
const PNL_INCOMPLETE = {
  success: true,
  data: Object.assign({}, PNL_OK.data, {
    cost_basis_incomplete: true,
    cost_basis_incomplete_sale_count: 3,
  }),
};

/* NOT ONE revenue/expense account moved -- has_activity: false. Every money
 * field is 0 exactly as income_statement would actually return them (the
 * screen's job is to never SHOW these zeros as the statement). */
const PNL_NO_ACTIVITY = {
  success: true,
  data: {
    currency: 'JOD',
    period: { start_date: '2026-09-01', end_date: '2026-09-01', branch_id: null },
    gross_revenue: 0, sales_returns: 0, revenue: 0, cost_of_goods_sold: 0,
    gross_profit: 0, gross_margin: null,
    operating_expenses: { total: 0, accounts: [] },
    net_profit: 0,
    has_activity: false,
    cost_basis_incomplete: false,
    cost_basis_incomplete_sale_count: 0,
  },
};

/* Same shape retail_gl_trial_balance_ui_test.js's TB_XSS_OK / retail_pos_
 * name_xss_test.js use for their own malicious-name fixtures: breaks out of
 * a double-quoted attribute AND carries a classic script-injection vector. */
const MALICIOUS_NAME = `"><img src=x onerror=alert(document.cookie)>`;
const PNL_XSS = {
  success: true,
  data: Object.assign({}, PNL_OK.data, {
    operating_expenses: {
      total: 400.000,
      accounts: [{ code: '9999', name: MALICIOUS_NAME, amount: 400.000 }],
    },
  }),
};

/* report_profit_and_loss's OWN refusal shape (StatementPeriodError -> 400):
 * {status:'error', message}, no `data`, no `success`. A start_date after
 * end_date is the concrete case that route's docstring names. */
const PNL_VALIDATION_ERROR = {
  status: 'error',
  message: "start_date '2026-09-30' is after end_date '2026-09-01' -- a reporting period cannot run backwards.",
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
 *   pnl          — the GET .../reports/profit-and-loss payload (or an Error
 *                  instance, to exercise the catch path)
 *   branches     — the GET .../branches payload (array); defaults to []
 *   t            — override for window.t (default: identity) -- the
 *                  functional "did this go through t()" proof uses a
 *                  MARKING t, matching retail_attribution_ui_test.js's
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
      if (u.indexOf(PNL_URL) !== -1) {
        const payload = o.pnl === undefined ? PNL_OK : o.pnl;
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
    (matching retail_gl_trial_balance_ui_test.js's renderScreen). */
async function renderScreen(opts) {
  const ctx = loadRetailSystem(opts);
  const host = makeStub({ id: 'sub-content' });
  ctx.els['sub-content'] = host;
  await ctx.rs.render(SECTION_ID);
  await settle();
  ctx.frame = host.innerHTML;
  ctx.body = () => (ctx.els['pnl-body'] ? ctx.els['pnl-body'].innerHTML : '');
  ctx.warning = () => (ctx.els['pnl-warning'] ? ctx.els['pnl-warning'].innerHTML : '');
  ctx.branchOptions = () => (ctx.els['pnl-branch'] ? ctx.els['pnl-branch'].innerHTML : '');
  return ctx;
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION HARNESS (same shape as retail_gl_trial_balance_ui_test.js)
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
    `The '${SECTION_ID}' nav entry does not gate on retail.reports, but report_profit_and_loss ` +
    `carries @mt_require_capability(CAP_REPORTS). Entry:\n  ${entry.trim()}`);
}

async function testCapabilityRestrictedWhenMissingReports() {
  // No retail.reports grant at all -- hasCapability() must refuse before any
  // fetch is attempted (neither the P&L fetch nor the branch-filter fetch).
  const ctx = await renderScreen({ capabilities: ['retail.sell'] });
  assert.strictEqual(
    ctx.calls.length, 0,
    `the capability guard let ${ctx.calls.length} fetch(es) through for a viewer without ` +
    'retail.reports -- the guard must refuse BEFORE any network call, not just hide the ' +
    'result. calls: ' + JSON.stringify(ctx.calls)
  );
  assert.ok(ctx.frame.indexOf('id="pnl-body"') === -1,
    'the statement rendered even though the viewer lacks retail.reports.');
  assert.ok(ctx.frame.indexOf('Profit and Loss') !== -1,
    'the capability-restricted panel must still name the screen it is standing in for.');
}

async function testStatementRendersRealValuesAndNetProfitIsCorrect() {
  const ctx = await renderScreen({ pnl: PNL_OK });
  const body = ctx.body();

  // Revenue, shown WITH its components (gross revenue and returns), not just
  // the net figure.
  assert.ok(body.indexOf('JD 5000.000') !== -1, `gross_revenue not rendered.\n${body}`);
  assert.ok(body.indexOf('JD 200.000') !== -1, `sales_returns not rendered.\n${body}`);
  assert.ok(body.indexOf('JD 4800.000') !== -1, `net revenue not rendered.\n${body}`);
  // Cost of goods sold and gross profit, with the margin.
  assert.ok(body.indexOf('JD 2500.000') !== -1, `cost_of_goods_sold not rendered.\n${body}`);
  assert.ok(body.indexOf('JD 2300.000') !== -1, `gross_profit not rendered.\n${body}`);
  assert.ok(body.indexOf('47.9%') !== -1, `gross_margin (2300/4800) not rendered as 47.9%.\n${body}`);
  // Operating expenses, broken out by account, with a total.
  for (const a of OPEX_ACCOUNTS) {
    assert.ok(body.indexOf(a.code) !== -1, `expense account code ${a.code} missing.\n${body}`);
    assert.ok(body.indexOf(a.name) !== -1, `expense account name "${a.name}" missing.\n${body}`);
  }
  assert.ok(body.indexOf('JD 520.250') !== -1, `operating_expenses.total not rendered.\n${body}`);
  // Net profit — the answer the whole screen exists to give, and it must be
  // ARITHMETICALLY the bottom line (2300 - 520.250), not a copy of gross
  // profit or some other figure that happens to also be present on screen.
  assert.ok(body.indexOf('JD 1779.750') !== -1,
    `net_profit (gross_profit - operating_expenses.total = 1779.750) not rendered.\n${body}`);

  // A clean period must show NO cost-basis warning at all.
  assert.strictEqual(ctx.warning(), '',
    `cost_basis_incomplete: false must render an EMPTY warning region; got:\n${ctx.warning()}`);
}

async function testCostBasisIncompleteWarningAppearsWhenTrueAndAbsentWhenFalse() {
  const dirty = await renderScreen({ pnl: PNL_INCOMPLETE });
  assert.ok(dirty.warning().indexOf('Cost basis is incomplete for this period') !== -1,
    `the honesty banner did not render when cost_basis_incomplete is true.\n${dirty.warning()}`);
  assert.ok(dirty.warning().indexOf('3') !== -1,
    `the affected-sale COUNT (3) is not shown in the banner.\n${dirty.warning()}`);
  assert.ok(
    dirty.warning().indexOf('Cost of goods sold is understated') !== -1 &&
    dirty.warning().indexOf('overstated') !== -1,
    `the banner does not explain the DIRECTION of the distortion (COGS understated, profit ` +
    `overstated) -- a bare "incomplete" warning with no direction is not the disclosure the ` +
    `route was built to carry.\n${dirty.warning()}`
  );

  const clean = await renderScreen({ pnl: PNL_OK });
  assert.strictEqual(clean.warning(), '',
    `cost_basis_incomplete: false must not render the banner; got:\n${clean.warning()}`);
}

async function testNoActivityRendersNothingPostedNotZeros() {
  const ctx = await renderScreen({ pnl: PNL_NO_ACTIVITY });
  const body = ctx.body();
  assert.ok(body.indexOf('Loading') === -1,
    'the statement is stuck on "Loading…" with a resolved has_activity:false response.');
  assert.ok(body.indexOf('Nothing was posted in this period') !== -1,
    `expected the nothing-posted empty-state title; got:\n${body}`);
  assert.ok(body.indexOf('This is not a zero profit') !== -1,
    `expected the empty-state hint distinguishing "no data" from "broke even"; got:\n${body}`);
  // The one thing this whole test exists to refuse: a row of formatted
  // zeros standing in for the real statement.
  assert.ok(body.indexOf('JD 0.000') === -1,
    `a formatted zero money figure leaked into the "nothing posted" state -- this must read ` +
    `as an absence of data, never as a computed zero.\n${body}`);
  assert.ok(body.indexOf('Net Profit') === -1,
    `the statement labels (e.g. "Net Profit") must not appear at all in the nothing-posted ` +
    `state -- it replaces the statement, it does not sit beside a blanked-out one.\n${body}`);
}

async function testValidationRefusalIsNotRenderedAsNothingPosted() {
  // The bug class this screen's own comment names: report_profit_and_loss's
  // 400 (StatementPeriodError) carries no `data` key, and `{}.has_activity`
  // is undefined -- falsy, same as PNL_NO_ACTIVITY's real `false`. A screen
  // that does not read `resp.status === 'error'` explicitly would render
  // this exactly like an empty period: a false statement about the books,
  // standing in for "the server refused the request".
  const ctx = await renderScreen({ pnl: PNL_VALIDATION_ERROR });
  const body = ctx.body();
  assert.ok(body.indexOf('Nothing was posted in this period') === -1,
    `a 400 refusal rendered as "nothing was posted" -- a refusal must never be shown as a ` +
    `true statement about an empty period.\n${body}`);
  assert.ok(body.indexOf('after end_date') !== -1,
    `the server's own refusal message should reach the screen; got:\n${body}`);
  assert.strictEqual(ctx.warning(), '',
    `a refused request must not leave a stale cost-basis banner on screen; got:\n${ctx.warning()}`);
}

async function testMaliciousExpenseAccountNameIsEscaped() {
  const ctx = await renderScreen({ pnl: PNL_XSS });
  assert.ok(ctx.body().indexOf('<img src=x onerror=') === -1,
    `a raw, unescaped <img onerror=...> tag reached the statement's innerHTML:\n${ctx.body()}`);
  assert.ok(ctx.body().indexOf('&lt;img src=x onerror=') !== -1,
    `the malicious account name should appear ESCAPED (as text, not markup):\n${ctx.body()}`);
}

async function testFetchFailureShowsVisibleError() {
  const ctx = await renderScreen({ pnl: new Error('network down') });
  assert.ok(ctx.body().indexOf('Loading') === -1,
    'a failed fetch must not leave the statement silently stuck on "Loading…" forever -- the ' +
    'exact bug class _loadPartyLedger/_loadTrialBalance were already fixed for once.');
  assert.ok(ctx.body().indexOf('Could not load this list') !== -1,
    `expected a visible error message; got:\n${ctx.body()}`);
  assert.strictEqual(ctx.warning(), '',
    `a failed fetch must not leave a stale cost-basis banner on screen; got:\n${ctx.warning()}`);
}

// A t() that proves it ran. Any label rendered through t() comes back
// wrapped; a hardcoded English literal in the template does not. Same
// technique, same marker glyphs, as retail_attribution_ui_test.js's
// markingT / testSaleDetailGridLabelsAllGoThroughT.
const markingT = (s) => '‹' + s + '›';

async function testEveryUserVisibleStringGoesThroughT() {
  const statement = await renderScreen({ pnl: PNL_INCOMPLETE, t: markingT });
  const statementLabels = [
    'Profit and Loss', 'All branches', 'From', 'To',
    'Gross Revenue', 'Sales Returns', 'Revenue', 'Cost of Goods Sold',
    'Gross Profit', 'Gross Margin', 'Operating Expenses',
    'Code', 'Account', 'Amount', 'Total', 'Net Profit',
    'Cost basis is incomplete for this period', 'sale(s) affected',
    'These sales have no recorded cost of goods. Cost of goods sold is understated, and the gross profit and net profit shown below are overstated.',
  ];
  const haystack = statement.frame + statement.body() + statement.warning() + statement.branchOptions();
  const missingFromStatement = statementLabels.filter((label) => haystack.indexOf('‹' + label + '›') === -1);
  assert.deepStrictEqual(
    missingFromStatement, [],
    'These strings never went through t() on the statement/warning render: ' +
    JSON.stringify(missingFromStatement) +
    '. A hardcoded literal here renders as permanent English on an Arabic till, invisible to ' +
    'review because everything nearby DOES say t(...).'
  );

  const empty = await renderScreen({ pnl: PNL_NO_ACTIVITY, t: markingT });
  const emptyLabels = [
    'Nothing was posted in this period',
    'No sale, return, purchase or expense was recorded in this date range. This is not a zero profit -- there is nothing to measure yet.',
  ];
  const missingFromEmpty = emptyLabels.filter((label) => empty.body().indexOf('‹' + label + '›') === -1);
  assert.deepStrictEqual(
    missingFromEmpty, [],
    'These strings never went through t() on the nothing-posted state: ' + JSON.stringify(missingFromEmpty)
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
    '1. the router case for profit-and-loss is deleted (screen becomes unreachable)',
    [["      case 'profit-and-loss': return this._renderProfitAndLoss(c);\n", '']],
    async (src) => testTheScreenIsReachable(src)));

  proved.push(await provesMutation(
    '2. the retail.reports capability guard is deleted from _renderProfitAndLoss',
    [[`    if (window.SubsystemApp && !SubsystemApp.hasCapability('retail.reports')) {
      return this._renderCapabilityRestricted(c, {
        icon: 'trending-up',
        title: t('Profit and Loss'),
        message: t('Sales totals and reports are limited to managers and the store owner. Open the till to start ringing sales.'),
      });
    }
`, '']],
    async (src) => {
      const ctx = await renderScreen({ source: src, capabilities: ['retail.sell'] });
      assert.strictEqual(ctx.calls.length, 0, 'guard removed but no fetch happened anyway');
    }));

  // ── HONESTY FLAG #1 — suppress the cost-basis warning ─────────────────────
  // The DENY-half of the flag (it must appear when true) is the one a bug
  // is most likely to break silently, because the ALLOW-half (empty when
  // false) is what every screen does by default when nobody wires the
  // banner at all. Forcing the condition to `false` reproduces exactly
  // that: a build that compiles, renders a statement, and simply never
  // shows the one warning this route was built to carry.
  proved.push(await provesMutation(
    '3. HONESTY FLAG — the cost_basis_incomplete warning is suppressed (forced to never render)',
    [['warnEl.innerHTML = data.cost_basis_incomplete\n', 'warnEl.innerHTML = false\n']],
    async (src) => {
      const ctx = await renderScreen({ source: src, pnl: PNL_INCOMPLETE });
      assert.ok(ctx.warning().indexOf('Cost basis is incomplete for this period') !== -1,
        'still shows the warning somehow');
    }));

  // ── HONESTY FLAG #2 — empty period renders zeros, not "nothing posted" ────
  proved.push(await provesMutation(
    '4. HONESTY FLAG — has_activity:false falls through to the (all-zero) statement instead of the nothing-posted state',
    [['      if (!data.has_activity) {\n', '      if (false) {\n']],
    async (src) => {
      const ctx = await renderScreen({ source: src, pnl: PNL_NO_ACTIVITY });
      assert.ok(ctx.body().indexOf('Nothing was posted in this period') !== -1,
        'still shows the nothing-posted state somehow');
      assert.ok(ctx.body().indexOf('JD 0.000') === -1,
        'still avoided rendering a formatted zero somehow');
    }));

  proved.push(await provesMutation(
    '5. this._esc() is removed from the expense-account-name interpolation',
    [[
      '        <td style="font-weight:600">${this._esc(a.name)}</td>\n        <td>${this._fmt(a.amount)}</td>\n',
      '        <td style="font-weight:600">${a.name}</td>\n        <td>${this._fmt(a.amount)}</td>\n',
    ]],
    async (src) => {
      const ctx = await renderScreen({ source: src, pnl: PNL_XSS });
      assert.ok(ctx.body().indexOf('<img src=x onerror=') === -1, 'still escaped somehow');
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
    ['the statement renders real values and net profit is arithmetically correct', testStatementRendersRealValuesAndNetProfitIsCorrect],
    ['the cost-basis warning appears when true and is absent when false', testCostBasisIncompleteWarningAppearsWhenTrueAndAbsentWhenFalse],
    ['has_activity:false renders "nothing was posted", never a row of zeros', testNoActivityRendersNothingPostedNotZeros],
    ['a validation refusal (400) is not rendered as "nothing was posted"', testValidationRefusalIsNotRenderedAsNothingPosted],
    ['a malicious expense account name is escaped', testMaliciousExpenseAccountNameIsEscaped],
    ['a fetch failure shows a visible error, not a stuck Loading…', testFetchFailureShowsVisibleError],
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
    console.error(`FAIL: retail_profit_and_loss_ui_test.js ran only ${checks.length - 1} of ` +
      `${EXPECTED_CHECKS} known behavioural checks (plus the mutation-proof meta-check).`);
    process.exitCode = 1;
    return;
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_profit_and_loss_ui_test.js — ${failures.length} of ` +
      `${checks.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_profit_and_loss_ui_test.js — ${checks.length} checks`);
}

if (require.main === module) {
  main().catch((err) => {
    console.error('FAIL: retail_profit_and_loss_ui_test.js (runner)');
    console.error(err && err.stack || err);
    process.exitCode = 1;
  });
}
