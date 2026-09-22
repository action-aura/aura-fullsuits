/**
 * Aura Retail — the BALANCE SHEET screen (financial_statements.py's second
 * GL-derived statement, retail-hardware-viewports).
 *
 * core/retail/financial_statements.py::balance_sheet derives a real balance
 * sheet off the same GL retail_gl_trial_balance_ui_test.js's/retail_profit_
 * and_loss_ui_test.js's own screens already surface. GET /api/sub/retail/
 * reports/balance-sheet (retail_api.py's report_balance_sheet) is this
 * wave's new route. See _renderBalanceSheet / _loadBalanceSheet in
 * subsystem-retail.js for the full reasoning; this file proves the screen
 * actually does what those comments claim.
 *
 * ── WHAT THIS FILE REFUSES TO LET SHIP ──────────────────────────────────────
 *
 * 1. A SILENTLY WRONG BALANCE. `balance_check.is_balanced` must render a
 *    GREEN "Balanced" badge when true and a LOUD RED "Out of Balance" badge
 *    (plus the signed difference) when false -- never the reverse, and
 *    never a badge that renders green regardless of the flag.
 * 2. A CONFIDENTLY WRONG STATEMENT. `cost_basis_incomplete` means Inventory
 *    and retained earnings are OVERSTATED (financial_statements.py's own
 *    balance_sheet docstring) -- the warning banner must appear when the
 *    flag is true and be ABSENT when it is false, never the reverse.
 * 3. AN EMPTY LEDGER READ AS "ZERO BALANCE SHEET". `has_activity === false`
 *    must render "nothing has been posted", never a statement full of
 *    formatted zeros with a trivially-agreeing balance badge over them --
 *    those are two different claims about the books.
 * 4. A REFUSAL RENDERED AS AN EMPTY SHOP. report_balance_sheet returns a
 *    400 with no `data` key when the date is invalid (StatementPeriodError)
 *    -- the exact class of miss retail_profit_and_loss_ui_test.js already
 *    guards for the sibling screen. `resp.data || {}` on that envelope
 *    resolves to `{}`, and `{}.has_activity` is falsy, so an unread refusal
 *    would render as "nothing has been posted" -- a true-sounding statement
 *    about a request the server never actually evaluated.
 * 5. AN ACCOUNT NAME REACHING innerHTML RAW. An account name is
 *    shop-editable data, exactly the trust boundary retail_gl_trial_
 *    balance_ui_test.js already guards for the sibling screen.
 * 6. A REPORTS-CAPABILITY GATE THAT DOES NOT ACTUALLY GATE. A viewer
 *    without retail.reports must see the capability-restricted panel and
 *    the screen must not even ATTEMPT a fetch -- asserted by counting
 *    calls, not just reading the rendered outcome.
 * 7. A LABEL THAT NEVER WENT THROUGH t(). Every user-visible string on the
 *    statement, the warning banner and the nothing-posted state must be a
 *    catalog lookup, not a hardcoded literal.
 * 8. THE SCREEN BECOMING UNREACHABLE. The router case and the nav entry
 *    (app-shell.js, capability: 'retail.reports') must both exist.
 *
 * ── MUTATION-PROVED ─────────────────────────────────────────────────────────
 * Every behavioural claim below is re-run against a DELIBERATELY BROKEN copy
 * of subsystem-retail.js and is required to FAIL there. See
 * testEveryGuardIsProvenByBreakingIt at the bottom -- its own console
 * output, printed on a passing run, names each mutation and the assertion
 * message it tripped. #1 and #2 below are the two honesty-adjacent claims
 * this screen exists to make, mutation-proved in BOTH directions per
 * ENGINEERING.md ("prove both directions of anything that both denies and
 * allows").
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_balance_sheet_ui_test.js
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

const SECTION_ID = 'balance-sheet';
const BS_URL = '/api/sub/retail/reports/balance-sheet';
const BRANCHES_URL = '/api/sub/retail/branches';

// ─────────────────────────────────────────────────────────────────────────────
// FIXTURES — the exact shape report_balance_sheet (retail_api.py) and
// balance_sheet (financial_statements.py) put on the wire. Success is
// {success:true, data:{...}} (NOT {status:'success', ...} -- that shape is
// what a REFUSAL uses, {status:'error', message}). Getting this backwards
// would make every "renders real values" assertion below pass against a
// screen that is silently reading the wrong envelope.
// ─────────────────────────────────────────────────────────────────────────────

const ASSET_ACCOUNTS = [
  { code: '1000', name: 'Cash and Bank', amount: 3200.000 },
  { code: '1100', name: 'Accounts Receivable', amount: 512.000 },
  { code: '1200', name: 'Inventory', amount: 2060.000 },
];
const LIABILITY_ACCOUNTS = [
  { code: '2000', name: 'Accounts Payable', amount: 2000.000 },
  { code: '2100', name: 'Tax Payable', amount: 272.000 },
];
const EQUITY_ACCOUNTS = [
  { code: '3000', name: "Owner's Equity", amount: 3000.000 },
];

/* Internally consistent, the same discipline retail_gl_trial_balance_ui_
 * test.js's ACCOUNTS_BALANCED fixture and retail_profit_and_loss_ui_
 * test.js's PNL_OK fixture hold themselves to: assets.total (3200+512+2060
 * = 5772) equals liabilities.total (2000+272 = 2272) plus equity.total
 * (owners_equity 3000 + retained_earnings 500 = 3500) -- 2272+3500 = 5772,
 * and balance_check reports agreement with difference 0. */
const BS_OK = {
  success: true,
  data: {
    currency: 'JOD',
    as_of_date: '2026-09-22',
    branch_id: null,
    assets: { total: 5772.000, accounts: ASSET_ACCOUNTS },
    liabilities: { total: 2272.000, accounts: LIABILITY_ACCOUNTS },
    equity: {
      owners_equity: 3000.000, retained_earnings: 500.000, total: 3500.000,
      accounts: EQUITY_ACCOUNTS,
    },
    total_liabilities_and_equity: 5772.000,
    balance_check: {
      assets_total: 5772.000, liabilities_and_equity_total: 5772.000,
      is_balanced: true, difference: 0,
    },
    has_activity: true,
    cost_basis_incomplete: false,
    cost_basis_incomplete_sale_count: 0,
  },
};

/* A ledger the balance check itself must report LOUDLY -- assets_total no
 * longer equals liabilities_and_equity_total, is_balanced is false, and
 * difference carries the exact gap (75.000, matching the shape retail_
 * balance_sheet_test.py's own corrupted-ledger backend test proves at the
 * function level -- this fixture is the UI-level sibling of that proof). */
const BS_OUT_OF_BALANCE = {
  success: true,
  data: Object.assign({}, BS_OK.data, {
    assets: { total: 5847.000, accounts: ASSET_ACCOUNTS.concat([
      { code: '1000', name: 'Cash and Bank (uncleared)', amount: 75.000 },
    ]) },
    balance_check: {
      assets_total: 5847.000, liabilities_and_equity_total: 5772.000,
      is_balanced: false, difference: 75.000,
    },
  }),
};

/* Same figures -- ONLY the honesty flag differs, matching retail_profit_
 * and_loss_ui_test.js's PNL_INCOMPLETE precedent exactly. */
const BS_COST_INCOMPLETE = {
  success: true,
  data: Object.assign({}, BS_OK.data, {
    cost_basis_incomplete: true,
    cost_basis_incomplete_sale_count: 5,
  }),
};

/* NOT ONE asset/liability/equity account has ever moved -- has_activity:
 * false. Every money field is 0 exactly as balance_sheet would actually
 * return them for an empty ledger (see retail_balance_sheet_test.py's own
 * test_empty_ledger_reports_no_activity_and_balances_trivially) -- the
 * screen's job is to never SHOW these zeros as the statement. */
const BS_NO_ACTIVITY = {
  success: true,
  data: {
    currency: 'JOD',
    as_of_date: '2026-01-01',
    branch_id: null,
    assets: { total: 0, accounts: [] },
    liabilities: { total: 0, accounts: [] },
    equity: { owners_equity: 0, retained_earnings: 0, total: 0, accounts: [] },
    total_liabilities_and_equity: 0,
    balance_check: { assets_total: 0, liabilities_and_equity_total: 0, is_balanced: true, difference: 0 },
    has_activity: false,
    cost_basis_incomplete: false,
    cost_basis_incomplete_sale_count: 0,
  },
};

/* Same shape retail_gl_trial_balance_ui_test.js's TB_XSS_OK / retail_
 * profit_and_loss_ui_test.js's PNL_XSS use for their own malicious-name
 * fixtures: breaks out of a double-quoted attribute AND carries a classic
 * script-injection vector. */
const MALICIOUS_NAME = `"><img src=x onerror=alert(document.cookie)>`;
const BS_XSS = {
  success: true,
  data: Object.assign({}, BS_OK.data, {
    assets: { total: 400.000, accounts: [{ code: '1999', name: MALICIOUS_NAME, amount: 400.000 }] },
  }),
};

/* report_balance_sheet's OWN refusal shape (StatementPeriodError -> 400):
 * {status:'error', message}, no `data`, no `success`. */
const BS_VALIDATION_ERROR = {
  status: 'error',
  message: "as_of_date 'not-a-date' is not a valid date in 'YYYY-MM-DD' format.",
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
 *   bs           — the GET .../reports/balance-sheet payload (or an Error
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
      if (u.indexOf(BS_URL) !== -1) {
        const payload = o.bs === undefined ? BS_OK : o.bs;
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
    (matching retail_gl_trial_balance_ui_test.js's/retail_profit_and_loss_
    ui_test.js's own renderScreen). */
async function renderScreen(opts) {
  const ctx = loadRetailSystem(opts);
  const host = makeStub({ id: 'sub-content' });
  ctx.els['sub-content'] = host;
  await ctx.rs.render(SECTION_ID);
  await settle();
  ctx.frame = host.innerHTML;
  ctx.body = () => (ctx.els['bs-body'] ? ctx.els['bs-body'].innerHTML : '');
  ctx.warning = () => (ctx.els['bs-warning'] ? ctx.els['bs-warning'].innerHTML : '');
  ctx.branchOptions = () => (ctx.els['bs-branch'] ? ctx.els['bs-branch'].innerHTML : '');
  return ctx;
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION HARNESS (same shape as retail_gl_trial_balance_ui_test.js /
// retail_profit_and_loss_ui_test.js)
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
    `The '${SECTION_ID}' nav entry does not gate on retail.reports, but report_balance_sheet ` +
    `carries @mt_require_capability(CAP_REPORTS). Entry:\n  ${entry.trim()}`);
}

async function testCapabilityRestrictedWhenMissingReports() {
  // No retail.reports grant at all -- hasCapability() must refuse before any
  // fetch is attempted (neither the balance-sheet fetch nor the branch-
  // filter fetch).
  const ctx = await renderScreen({ capabilities: ['retail.sell'] });
  assert.strictEqual(
    ctx.calls.length, 0,
    `the capability guard let ${ctx.calls.length} fetch(es) through for a viewer without ` +
    'retail.reports -- the guard must refuse BEFORE any network call, not just hide the ' +
    'result. calls: ' + JSON.stringify(ctx.calls)
  );
  assert.ok(ctx.frame.indexOf('id="bs-body"') === -1,
    'the statement rendered even though the viewer lacks retail.reports.');
  assert.ok(ctx.frame.indexOf('Balance Sheet') !== -1,
    'the capability-restricted panel must still name the screen it is standing in for.');
}

async function testStatementRendersRealValuesAndTotalsAreCorrect() {
  const ctx = await renderScreen({ bs: BS_OK });
  const body = ctx.body();

  // _esc() escapes a bare "'" to "&#39;" (subsystem-retail.js's own _esc,
  // matching retail_gl_trial_balance_ui_test.js's identical convention for
  // account names) -- "Owner's Equity" therefore renders as "Owner&#39;s
  // Equity", never the literal apostrophe.
  const escApos = (s) => s.replace(/'/g, '&#39;');
  for (const a of ASSET_ACCOUNTS.concat(LIABILITY_ACCOUNTS).concat(EQUITY_ACCOUNTS)) {
    assert.ok(body.indexOf(a.code) !== -1, `account code ${a.code} missing.\n${body}`);
    assert.ok(body.indexOf(escApos(a.name)) !== -1, `account name "${a.name}" missing.\n${body}`);
  }
  // The three totals a shopkeeper actually reads.
  assert.ok(body.indexOf('JD 5772.000') !== -1, `assets total not rendered.\n${body}`);
  assert.ok(body.indexOf('JD 2272.000') !== -1, `liabilities total not rendered.\n${body}`);
  assert.ok(body.indexOf('JD 3500.000') !== -1, `equity total not rendered.\n${body}`);
  // Retained earnings shown as its OWN figure, not folded silently into
  // owners_equity.
  assert.ok(body.indexOf('JD 500.000') !== -1, `retained_earnings (500.000) not rendered on its own line.\n${body}`);

  // A balanced sheet shows the GREEN badge, never the red one.
  assert.ok(body.indexOf('ret-badge-green') !== -1, `no green balance badge rendered.\n${body}`);
  assert.ok(body.indexOf('ret-badge-red') === -1, `a red badge rendered on a BALANCED sheet.\n${body}`);
  assert.ok(body.indexOf('Balanced') !== -1, `"Balanced" label missing.\n${body}`);

  // A clean sheet must show NO cost-basis warning at all.
  assert.strictEqual(ctx.warning(), '',
    `cost_basis_incomplete: false must render an EMPTY warning region; got:\n${ctx.warning()}`);
}

async function testOutOfBalanceRendersLoudlyWithTheDifference() {
  const ctx = await renderScreen({ bs: BS_OUT_OF_BALANCE });
  const body = ctx.body();
  assert.ok(body.indexOf('ret-badge-red') !== -1, `no red "Out of Balance" badge rendered.\n${body}`);
  assert.ok(body.indexOf('Out of Balance') !== -1, `"Out of Balance" label missing.\n${body}`);
  assert.ok(body.indexOf('ret-badge-green') === -1, `a green badge rendered on an OUT-OF-BALANCE sheet.\n${body}`);
  // The signed difference (75.000) must be shown, not merely the fact of
  // disagreement.
  assert.ok(body.indexOf('75.000') !== -1, `the difference (75.000) is not shown.\n${body}`);
  assert.ok(body.indexOf('Difference') !== -1, `the "Difference" label is missing.\n${body}`);
}

async function testCostBasisIncompleteWarningAppearsWhenTrueAndAbsentWhenFalse() {
  const dirty = await renderScreen({ bs: BS_COST_INCOMPLETE });
  assert.ok(dirty.warning().indexOf('Cost basis is incomplete') !== -1,
    `the honesty banner did not render when cost_basis_incomplete is true.\n${dirty.warning()}`);
  assert.ok(dirty.warning().indexOf('5') !== -1,
    `the affected-sale COUNT (5) is not shown in the banner.\n${dirty.warning()}`);
  assert.ok(
    dirty.warning().indexOf('Inventory') !== -1 && dirty.warning().indexOf('retained earnings') !== -1,
    `the banner does not name the TWO balance-sheet line items this statement's own cost-basis ` +
    `caveat distorts (Inventory, retained earnings) -- a bare "incomplete" warning with no ` +
    `named line items is not the disclosure the route was built to carry.\n${dirty.warning()}`
  );

  const clean = await renderScreen({ bs: BS_OK });
  assert.strictEqual(clean.warning(), '',
    `cost_basis_incomplete: false must not render the banner; got:\n${clean.warning()}`);
}

async function testNoActivityRendersNothingPostedNotZeros() {
  const ctx = await renderScreen({ bs: BS_NO_ACTIVITY });
  const body = ctx.body();
  assert.ok(body.indexOf('Loading') === -1,
    'the statement is stuck on "Loading…" with a resolved has_activity:false response.');
  assert.ok(body.indexOf('Nothing has been posted as of this date') !== -1,
    `expected the nothing-posted empty-state title; got:\n${body}`);
  assert.ok(body.indexOf('This is not a zero balance sheet') !== -1,
    `expected the empty-state hint distinguishing "no data" from "balances at zero"; got:\n${body}`);
  // The one thing this whole test exists to refuse: a row of formatted
  // zeros standing in for the real statement, WITH a trivially-agreeing
  // balance badge sitting over them as if that meant something.
  assert.ok(body.indexOf('JD 0.000') === -1,
    `a formatted zero money figure leaked into the "nothing posted" state -- this must read ` +
    `as an absence of data, never as a computed zero.\n${body}`);
  assert.ok(body.indexOf('Balanced') === -1 && body.indexOf('Out of Balance') === -1,
    `a balance badge rendered over an empty ledger -- the nothing-posted state replaces the ` +
    `whole statement, including the balance check, it does not sit beside a trivially-agreeing ` +
    `one.\n${body}`);
}

async function testValidationRefusalIsNotRenderedAsNothingPosted() {
  // The bug class this screen's own comment names: report_balance_sheet's
  // 400 (StatementPeriodError) carries no `data` key, and `{}.has_activity`
  // is undefined -- falsy, same as BS_NO_ACTIVITY's real `false`. A screen
  // that does not read `resp.status === 'error'` explicitly would render
  // this exactly like an empty ledger: a false statement about the books,
  // standing in for "the server refused the request".
  const ctx = await renderScreen({ bs: BS_VALIDATION_ERROR });
  const body = ctx.body();
  assert.ok(body.indexOf('Nothing has been posted as of this date') === -1,
    `a 400 refusal rendered as "nothing has been posted" -- a refusal must never be shown as a ` +
    `true statement about an empty ledger.\n${body}`);
  assert.ok(body.indexOf('not a valid date') !== -1,
    `the server's own refusal message should reach the screen; got:\n${body}`);
  assert.strictEqual(ctx.warning(), '',
    `a refused request must not leave a stale cost-basis banner on screen; got:\n${ctx.warning()}`);
}

async function testMaliciousAccountNameIsEscaped() {
  const ctx = await renderScreen({ bs: BS_XSS });
  assert.ok(ctx.body().indexOf('<img src=x onerror=') === -1,
    `a raw, unescaped <img onerror=...> tag reached the statement's innerHTML:\n${ctx.body()}`);
  assert.ok(ctx.body().indexOf('&lt;img src=x onerror=') !== -1,
    `the malicious account name should appear ESCAPED (as text, not markup):\n${ctx.body()}`);
}

async function testFetchFailureShowsVisibleError() {
  const ctx = await renderScreen({ bs: new Error('network down') });
  assert.ok(ctx.body().indexOf('Loading') === -1,
    'a failed fetch must not leave the statement silently stuck on "Loading…" forever -- the ' +
    'exact bug class _loadPartyLedger/_loadTrialBalance/_loadProfitAndLoss were already fixed for once.');
  assert.ok(ctx.body().indexOf('Could not load this list') !== -1,
    `expected a visible error message; got:\n${ctx.body()}`);
  assert.strictEqual(ctx.warning(), '',
    `a failed fetch must not leave a stale cost-basis banner on screen; got:\n${ctx.warning()}`);
}

// A t() that proves it ran. Any label rendered through t() comes back
// wrapped; a hardcoded English literal in the template does not. Same
// technique, same marker glyphs, as retail_attribution_ui_test.js's
// markingT / retail_profit_and_loss_ui_test.js's own identical proof.
const markingT = (s) => '‹' + s + '›';

async function testEveryUserVisibleStringGoesThroughT() {
  const statement = await renderScreen({ bs: BS_COST_INCOMPLETE, t: markingT });
  const statementLabels = [
    'Balance Sheet', 'All branches', 'As of Date',
    'Assets', 'Liabilities', 'Equity', 'Retained Earnings',
    'Total Equity', 'Total Assets', 'Total Liabilities and Equity',
    'Code', 'Account', 'Amount', 'Total', 'Balanced',
    'Cost basis is incomplete', 'sale(s) affected',
    'These sales have no recorded cost of goods. Inventory and retained earnings shown below are overstated.',
    'Accumulated profit since the ledger began. This product has no period-close step, so this figure is always current rather than a posted ledger entry.',
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

  const outOfBalance = await renderScreen({ bs: BS_OUT_OF_BALANCE, t: markingT });
  assert.ok(outOfBalance.body().indexOf('‹Out of Balance›') !== -1 && outOfBalance.body().indexOf('‹Difference›') !== -1,
    `the out-of-balance badge/label did not go through t().\n${outOfBalance.body()}`);

  const empty = await renderScreen({ bs: BS_NO_ACTIVITY, t: markingT });
  const emptyLabels = [
    'Nothing has been posted as of this date',
    'No sale, return, purchase or expense was recorded on or before this date. This is not a zero balance sheet -- there is nothing to measure yet.',
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
    '1. the router case for balance-sheet is deleted (screen becomes unreachable)',
    [["      case 'balance-sheet': return this._renderBalanceSheet(c);\n", '']],
    async (src) => testTheScreenIsReachable(src)));

  proved.push(await provesMutation(
    '2. the retail.reports capability guard is deleted from _renderBalanceSheet',
    [[`    if (window.SubsystemApp && !SubsystemApp.hasCapability('retail.reports')) {
      return this._renderCapabilityRestricted(c, {
        icon: 'landmark',
        title: t('Balance Sheet'),
        message: t('Sales totals and reports are limited to managers and the store owner. Open the till to start ringing sales.'),
      });
    }
`, '']],
    async (src) => {
      const ctx = await renderScreen({ source: src, capabilities: ['retail.sell'] });
      assert.strictEqual(ctx.calls.length, 0, 'guard removed but no fetch happened anyway');
    }));

  // ── THE BALANCE CHECK ITSELF — forced to ALWAYS read as agreeing ─────────
  // The DENY-half (a real disagreement must show red) is the one a bug is
  // most likely to break silently, because rendering green is what every
  // badge-selection ternary does when nobody wires the false branch at
  // all. Forcing the ternary's condition reproduces exactly that: a build
  // that compiles, renders a statement, and simply never shows red.
  proved.push(await provesMutation(
    '3. THE BALANCE CHECK — the badge always renders green, even when bc.is_balanced is false',
    [['bc.is_balanced\n        ? this._badge(t(\'Balanced\'), \'green\')', 'true\n        ? this._badge(t(\'Balanced\'), \'green\')']],
    async (src) => {
      const ctx = await renderScreen({ source: src, bs: BS_OUT_OF_BALANCE });
      assert.ok(ctx.body().indexOf('ret-badge-red') !== -1, 'still shows red somehow');
    }));

  // ── HONESTY FLAG — suppress the cost-basis warning ────────────────────────
  proved.push(await provesMutation(
    '4. HONESTY FLAG — the cost_basis_incomplete warning is suppressed (forced to never render)',
    [[
      "warnEl.innerHTML = (data.cost_basis_incomplete)\n          ? `<div style=\"background:var(--state-danger-surface);color:var(--state-danger-text);` +\n            `border:1px solid var(--state-danger-border);border-radius:10px;padding:14px 16px;margin-bottom:16px\" role=\"alert\">\n              <div style=\"font-weight:700;margin-bottom:6px\">${t('Cost basis is incomplete')}</div>",
      "warnEl.innerHTML = false\n          ? `<div style=\"background:var(--state-danger-surface);color:var(--state-danger-text);` +\n            `border:1px solid var(--state-danger-border);border-radius:10px;padding:14px 16px;margin-bottom:16px\" role=\"alert\">\n              <div style=\"font-weight:700;margin-bottom:6px\">${t('Cost basis is incomplete')}</div>",
    ]],
    async (src) => {
      const ctx = await renderScreen({ source: src, bs: BS_COST_INCOMPLETE });
      assert.ok(ctx.warning().indexOf('Cost basis is incomplete') !== -1,
        'still shows the warning somehow');
    }));

  // ── HONESTY FLAG — empty ledger renders zeros, not "nothing posted" ──────
  proved.push(await provesMutation(
    '5. HONESTY FLAG — has_activity:false falls through to the (all-zero) statement instead of the nothing-posted state',
    [[
      "      if (!(data.has_activity)) {\n        body.innerHTML = this._emptyState({\n          icon: 'landmark',",
      "      if (false) {\n        body.innerHTML = this._emptyState({\n          icon: 'landmark',",
    ]],
    async (src) => {
      const ctx = await renderScreen({ source: src, bs: BS_NO_ACTIVITY });
      assert.ok(ctx.body().indexOf('Nothing has been posted as of this date') !== -1,
        'still shows the nothing-posted state somehow');
      assert.ok(ctx.body().indexOf('JD 0.000') === -1,
        'still avoided rendering a formatted zero somehow');
    }));

  proved.push(await provesMutation(
    '6. this._esc() is removed from the account-name interpolation',
    [[
      '      const accountRows = (accounts) => accounts.map(a => `<tr>\n        <td style="font-family:monospace;color:var(--sub-accent)">${this._bdi(a.code)}</td>\n        <td style="font-weight:600">${this._esc(a.name)}</td>\n',
      '      const accountRows = (accounts) => accounts.map(a => `<tr>\n        <td style="font-family:monospace;color:var(--sub-accent)">${this._bdi(a.code)}</td>\n        <td style="font-weight:600">${a.name}</td>\n',
    ]],
    async (src) => {
      const ctx = await renderScreen({ source: src, bs: BS_XSS });
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
    ['the statement renders real values and the totals are arithmetically correct', testStatementRendersRealValuesAndTotalsAreCorrect],
    ['an out-of-balance sheet renders loudly, with the signed difference', testOutOfBalanceRendersLoudlyWithTheDifference],
    ['the cost-basis warning appears when true and is absent when false', testCostBasisIncompleteWarningAppearsWhenTrueAndAbsentWhenFalse],
    ['has_activity:false renders "nothing has been posted", never a set of zeros', testNoActivityRendersNothingPostedNotZeros],
    ['a validation refusal (400) is not rendered as "nothing has been posted"', testValidationRefusalIsNotRenderedAsNothingPosted],
    ['a malicious account name is escaped', testMaliciousAccountNameIsEscaped],
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
    console.error(`FAIL: retail_balance_sheet_ui_test.js ran only ${checks.length - 1} of ` +
      `${EXPECTED_CHECKS} known behavioural checks (plus the mutation-proof meta-check).`);
    process.exitCode = 1;
    return;
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_balance_sheet_ui_test.js — ${failures.length} of ` +
      `${checks.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_balance_sheet_ui_test.js — ${checks.length} checks`);
}

if (require.main === module) {
  main().catch((err) => {
    console.error('FAIL: retail_balance_sheet_ui_test.js (runner)');
    console.error(err && err.stack || err);
    process.exitCode = 1;
  });
}
