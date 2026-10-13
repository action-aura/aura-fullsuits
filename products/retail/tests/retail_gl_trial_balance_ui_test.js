/**
 * Aura Retail — the TRIAL BALANCE screen (schema v38 general ledger,
 * retail-hardware-viewports, 2026-09-21).
 *
 * core/retail/general_ledger.py has posted every sale/return/payment/
 * purchase order to gl_journal_lines since v38 shipped, with no route ever
 * calling gl.trial_balance -- the ledger existed and was correct but was
 * reachable only from Python. GET /api/sub/retail/reports/trial-balance
 * (retail_api.py's report_trial_balance) is the desktop half of making it
 * visible. See _renderTrialBalance / _loadTrialBalance in subsystem-retail.js
 * for the full reasoning; this file proves the screen actually does what
 * those comments claim.
 *
 * ── WHAT THIS FILE REFUSES TO LET SHIP ──────────────────────────────────────
 *
 * 1. AN UNBALANCED LEDGER THAT LOOKS FINE. `is_balanced` drives a green/red
 *    badge -- a build that always paints green regardless of the flag would
 *    hide the one failure this screen exists to surface.
 * 2. A SUBLEDGER THAT HAS DRIFTED FROM THE GL, SHOWN AS "TIES". This is the
 *    POINT of the screen (report_trial_balance's own docstring: `subledger`
 *    is why the route exists, not a bonus field) -- ar_ties/ap_ties each
 *    drive their own badge, independently.
 * 3. AN ACCOUNT NAME REACHING innerHTML RAW. An account name is shop-editable
 *    data, exactly the trust boundary retail_pos_name_xss_test.js /
 *    retail_exceptions_screen_test.js already guard elsewhere in this
 *    frontend.
 * 4. A REPORTS-CAPABILITY GATE THAT DOES NOT ACTUALLY GATE. A viewer without
 *    retail.reports must see the capability-restricted panel and the screen
 *    must not even ATTEMPT the fetch -- asserted by counting calls, not just
 *    reading the rendered outcome (see ENGINEERING.md: asserting an outcome
 *    where you should assert the check ran).
 * 5. THE SCREEN BECOMING UNREACHABLE. The router case and the nav entry
 *    (app-shell.js, capability: 'retail.reports') must both exist -- the
 *    exact "shipped, gated, and nobody could click it" bug class Stock
 *    Transfers hit once (see retail_nav_groups_test.js's own history).
 *
 * ── MUTATION-PROVED ─────────────────────────────────────────────────────────
 * Every behavioural claim below is re-run against a DELIBERATELY BROKEN copy
 * of subsystem-retail.js and is required to FAIL there. See
 * testEveryGuardIsProvenByBreakingIt at the bottom -- its own console output,
 * printed on a passing run, names each mutation and the assertion message it
 * tripped.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_gl_trial_balance_ui_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const dom = require('./retail_surface_domlite.js');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const RETAIL_JS = path.join(FRONTEND, 'subsystem-retail.js');
const APP_SHELL_JS = path.join(FRONTEND, 'app-shell.js');

const SRC = fs.readFileSync(RETAIL_JS, 'utf8');

const SECTION_ID = 'trial-balance';
const TB_URL = '/api/sub/retail/reports/trial-balance';

// ─────────────────────────────────────────────────────────────────────────────
// FIXTURES
// ─────────────────────────────────────────────────────────────────────────────

/* A balanced trial balance: three accounts, debits == credits, both
 * subledgers tying to the GL. Matches the exact shape report_trial_balance
 * (retail_api.py) puts on the wire: {success, data:{accounts, total_debit,
 * total_credit, is_balanced, subledger}} -- NOT the bare object alone. */
const ACCOUNTS_BALANCED = [
  { code: '1000', name: 'Cash', account_type: 'asset', normal_balance: 'debit', debit: 1234.5, credit: 0, balance: 1234.5 },
  { code: '2000', name: 'Accounts Payable', account_type: 'liability', normal_balance: 'credit', debit: 0, credit: 500, balance: 500 },
  // account_type 'revenue', NOT 'income' -- this fixture said 'income' and
  // was wrong about the product. general_ledger.py's DEFAULT_CHART seeds
  // 4000 Sales Revenue and 4900 Sales Returns and Allowances as 'revenue',
  // so the screen's label map (which keyed 'income') matched nothing and
  // rendered every revenue account as "Other" on every trial balance ever
  // shown. The fixture agreeing with the buggy code instead of with the
  // backend is exactly why this file stayed green through it: a fixture
  // that manufactures the state which hides the defect is one of this
  // repo's named recurring failure shapes.
  { code: '4000', name: 'Sales Revenue', account_type: 'revenue', normal_balance: 'credit', debit: 0, credit: 734.5, balance: 734.5 },
];

const TB_BALANCED_OK = {
  success: true,
  data: {
    currency: 'JOD',
    accounts: ACCOUNTS_BALANCED,
    total_debit: 1234.5,
    total_credit: 1234.5,
    is_balanced: true,
    subledger: { ar_gl: 120, ar_subledger: 120, ar_ties: true, ap_gl: 80, ap_subledger: 80, ap_ties: true },
  },
};

/* Same accounts, but the ledger disagrees with itself AND with both
 * subledgers -- the exact "quiet" failure the subledger block exists to
 * catch (a GL that balances against its own arithmetic can still have
 * drifted from what the till actually collects/pays against). */
const TB_UNBALANCED = {
  success: true,
  data: {
    currency: 'JOD',
    accounts: ACCOUNTS_BALANCED,
    total_debit: 1300,
    total_credit: 1234.5,
    is_balanced: false,
    subledger: { ar_gl: 120, ar_subledger: 90, ar_ties: false, ap_gl: 80, ap_subledger: 50, ap_ties: false },
  },
};

const TB_EMPTY_OK = {
  success: true,
  data: {
    currency: 'JOD', accounts: [], total_debit: 0, total_credit: 0, is_balanced: true,
    subledger: { ar_gl: 0, ar_subledger: 0, ar_ties: true, ap_gl: 0, ap_subledger: 0, ap_ties: true },
  },
};

/* Same payload shape retail_pos_name_xss_test.js / retail_exceptions_screen_
 * test.js use for their own malicious-name fixtures: breaks out of a
 * double-quoted attribute AND carries a classic script-injection vector. */
const MALICIOUS_NAME = `"><img src=x onerror=alert(document.cookie)>`;
const TB_XSS_OK = {
  success: true,
  data: {
    currency: 'JOD',
    accounts: [
      { code: '9999', name: MALICIOUS_NAME, account_type: 'asset', normal_balance: 'debit', debit: 10, credit: 0, balance: 10 },
    ],
    total_debit: 10, total_credit: 10, is_balanced: true,
    subledger: { ar_gl: 0, ar_subledger: 0, ar_ties: true, ap_gl: 0, ap_subledger: 0, ap_ties: true },
  },
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
 *   trialBalance — the GET .../reports/trial-balance payload (or an Error
 *                  instance, to exercise the catch path)
 */
function loadRetailSystem(opts) {
  const o = opts || {};
  const calls = [];
  const els = Object.create(null);
  const getEl = (id) => (els[id] || (els[id] = makeStub({ id })));

  const sandbox = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    t: (s) => s,
    fetch: (url, init) => {
      const method = ((init && init.method) || 'GET').toUpperCase();
      const u = String(url);
      calls.push({ method, url: u });
      if (u.indexOf(TB_URL) === -1) {
        return Promise.resolve({
          ok: false, status: 404,
          json: () => Promise.resolve({ success: false, error: 'unmapped fixture route: ' + method + ' ' + u }),
        });
      }
      const payload = o.trialBalance === undefined ? TB_BALANCED_OK : o.trialBalance;
      if (payload instanceof Error) return Promise.reject(payload);
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
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
      // '#id tbody' is the one compound selector _loadTrialBalance actually
      // issues (matching _loadPartyLedger's identical `#pl-table-<kind>
      // tbody` pattern above it) -- routed to a STABLE stub keyed off the
      // id, not a fresh disconnected one each call, so the test can read
      // back what got painted into it.
      querySelector(sel) {
        const m = /^#([\w-]+)\s+tbody$/.exec(sel);
        if (m) return getEl(m[1] + '__tbody');
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
    (matching retail_exceptions_screen_test.js's renderScreen). */
async function renderScreen(opts) {
  const ctx = loadRetailSystem(opts);
  const host = makeStub({ id: 'sub-content' });
  ctx.els['sub-content'] = host;
  await ctx.rs.render(SECTION_ID);
  await settle();
  ctx.frame = host.innerHTML;
  ctx.tbody = () => (ctx.els['tb-table__tbody'] ? ctx.els['tb-table__tbody'].innerHTML : '');
  ctx.status = () => (ctx.els['tb-status'] ? ctx.els['tb-status'].innerHTML : '');
  ctx.subledger = () => (ctx.els['tb-subledger'] ? ctx.els['tb-subledger'].innerHTML : '');
  ctx.totalDebit = () => (ctx.els['tb-total-debit'] ? ctx.els['tb-total-debit'].textContent : '');
  ctx.totalCredit = () => (ctx.els['tb-total-credit'] ? ctx.els['tb-total-credit'].textContent : '');
  return ctx;
}

function dataRows(html) {
  const root = dom.parseFragment(html);
  return dom.allElements(root).filter((el) => el.tag === 'tr');
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION HARNESS (same shape as retail_exceptions_screen_test.js /
// retail_stock_accuracy_screen_test.js)
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
    `The '${SECTION_ID}' nav entry does not gate on retail.reports, but report_trial_balance ` +
    `carries @mt_require_capability(CAP_REPORTS). Entry:\n  ${entry.trim()}`);
}

async function testCapabilityRestrictedWhenMissingReports() {
  // No retail.reports grant at all -- hasCapability() must refuse before any
  // fetch is attempted, matching _renderPartyLedger's identical guard.
  const ctx = await renderScreen({ capabilities: ['retail.sell'] });
  assert.strictEqual(
    ctx.calls.length, 0,
    `the capability guard let ${ctx.calls.length} fetch(es) through for a viewer without ` +
    'retail.reports -- the guard must refuse BEFORE any network call, not just hide the ' +
    'result. calls: ' + JSON.stringify(ctx.calls)
  );
  assert.ok(ctx.frame.indexOf('id="tb-table"') === -1,
    'the accounts table rendered even though the viewer lacks retail.reports.');
  assert.ok(ctx.frame.indexOf('Trial Balance') !== -1,
    'the capability-restricted panel must still name the screen it is standing in for.');
}

async function testEverySeededAccountTypeGetsARealLabel() {
  // The regression this exists for: `_TRIAL_BALANCE_TYPE_LABEL` keyed
  // 'income' while the backend emits 'revenue', so both revenue accounts
  // rendered as "Other" -- silently, because `_glAccountTypeLabel` degrades
  // to `|| 'Other'` rather than failing. Found by the Android client's
  // parity work, not here, because the tests above only assert that a ROW
  // exists, and a row with the wrong type label is still a row.
  //
  // Driven from general_ledger.py's DEFAULT_CHART rather than a typed list,
  // so a future account type added to the seeded chart fails this test
  // instead of quietly joining "Other".
  const glSrc = fs.readFileSync(
    path.join(__dirname, '..', 'backend', 'core', 'retail', 'general_ledger.py'), 'utf8');
  // Slice to the tuple's own closing line (`\n)`), NOT to the first ')' --
  // that one sits inside the FIRST entry, which made this parser see a
  // single account type. Its own vacuity assertion below is what caught
  // that, which is the point of having one.
  const chartStart = glSrc.indexOf('DEFAULT_CHART');
  const chart = glSrc.slice(chartStart, glSrc.indexOf('\n)', chartStart));
  const seeded = new Set();
  // Third field of each tuple. The NAME may be single- OR double-quoted --
  // "Owner's Equity" carries an apostrophe and so is double-quoted in the
  // source; a pattern that assumed single quotes would silently skip it.
  for (const m of chart.matchAll(/\(\s*'[^']*'\s*,\s*(?:'[^']*'|"[^"]*")\s*,\s*'([a-z_]+)'/g)) seeded.add(m[1]);
  assert.ok(seeded.size >= 5,
    `parsed only ${seeded.size} account types out of DEFAULT_CHART -- the parser has drifted ` +
    `from general_ledger.py and would pass vacuously. Got: ${[...seeded].join(', ')}`);

  const ctx = await renderScreen({ trialBalance: TB_BALANCED_OK });
  const RS = ctx.rs;
  const unlabelled = [...seeded].filter((ty) => RS._glAccountTypeLabel(ty) === 'Other');
  assert.deepStrictEqual(unlabelled, [],
    `account type(s) ${unlabelled.join(', ')} are seeded by general_ledger.py's DEFAULT_CHART ` +
    `but have no entry in _TRIAL_BALANCE_TYPE_LABEL, so they render as "Other" on the trial ` +
    'balance. Add them to the map rather than to this test.');

  // And the rendered table must actually show it, not merely the map.
  assert.ok(ctx.tbody().indexOf('Other') === -1,
    `a seeded account rendered with the fallback "Other" type label:\n${ctx.tbody()}`);
}

async function testAccountsTableRendersRowsAndTotals() {
  const ctx = await renderScreen({ trialBalance: TB_BALANCED_OK });
  const rows = dataRows(ctx.tbody());
  // 3 account rows + 1 totals row.
  assert.strictEqual(rows.length, 4,
    `expected 3 account rows + 1 totals row (4 <tr>), got ${rows.length}.\n${ctx.tbody()}`);

  for (const a of ACCOUNTS_BALANCED) {
    assert.ok(ctx.tbody().indexOf(a.code) !== -1, `account code ${a.code} missing from the table`);
    assert.ok(ctx.tbody().indexOf(a.name) !== -1, `account name "${a.name}" missing from the table`);
  }
  // _fmt() with the sandbox's default currency (JD, 3dp) and identity t():
  // 1234.5 -> "JD 1234.500".
  assert.ok(ctx.tbody().indexOf('JD 1234.500') !== -1,
    `the Cash row's debit figure is not formatted as expected.\n${ctx.tbody()}`);
  assert.ok(ctx.totalDebit().indexOf('JD 1234.500') !== -1,
    `the KPI strip's total debit does not read JD 1234.500, got "${ctx.totalDebit()}"`);
  assert.ok(ctx.totalCredit().indexOf('JD 1234.500') !== -1,
    `the KPI strip's total credit does not read JD 1234.500, got "${ctx.totalCredit()}"`);
  // The totals row restates the same two figures at the foot of the table.
  assert.ok(/Total[\s\S]*JD 1234\.500[\s\S]*JD 1234\.500/.test(ctx.tbody()),
    `the totals footer row does not restate total_debit/total_credit.\n${ctx.tbody()}`);
}

async function testEmptyStateWhenNoAccounts() {
  const ctx = await renderScreen({ trialBalance: TB_EMPTY_OK });
  assert.ok(ctx.tbody().indexOf('Loading') === -1,
    'the table is stuck on "Loading…" with a resolved empty response.');
  assert.ok(ctx.tbody().indexOf('No accounts posted yet') !== -1,
    `expected the empty-state title; got:\n${ctx.tbody()}`);
}

async function testBalancedBadgeReflectsIsBalanced() {
  const okCtx = await renderScreen({ trialBalance: TB_BALANCED_OK });
  assert.ok(okCtx.status().indexOf('Balanced') !== -1 && okCtx.status().indexOf('ret-badge-green') !== -1,
    `a balanced ledger must show a GREEN "Balanced" badge; got: ${okCtx.status()}`);
  assert.ok(okCtx.status().indexOf('Out of Balance') === -1,
    `a balanced ledger must not also say "Out of Balance"; got: ${okCtx.status()}`);

  const badCtx = await renderScreen({ trialBalance: TB_UNBALANCED });
  assert.ok(badCtx.status().indexOf('Out of Balance') !== -1 && badCtx.status().indexOf('ret-badge-red') !== -1,
    `an unbalanced ledger (total_debit != total_credit) must show a RED "Out of Balance" ` +
    `badge; got: ${badCtx.status()}`);
  assert.ok(badCtx.status().indexOf('>Balanced<') === -1,
    `an unbalanced ledger must not also say plain "Balanced"; got: ${badCtx.status()}`);
}

async function testSubledgerTieBadgesReflectTiesFlags() {
  const okCtx = await renderScreen({ trialBalance: TB_BALANCED_OK });
  const okSl = okCtx.subledger();
  // Two 'Ties' badges (AR and AP), zero 'Does Not Tie'.
  assert.strictEqual((okSl.match(/>Ties</g) || []).length, 2,
    `expected 2 green "Ties" badges when both ar_ties/ap_ties are true; got:\n${okSl}`);
  assert.ok(okSl.indexOf('Does Not Tie') === -1,
    `no subledger should read "Does Not Tie" when both flags are true; got:\n${okSl}`);
  // Both the GL figure and the subledger figure must be visible, not just
  // the verdict -- the whole point is a reader can see WHY it ties.
  assert.ok(okSl.indexOf('JD 120.000') !== -1 && okSl.indexOf('JD 80.000') !== -1,
    `the GL/subledger AR (120) and AP (80) figures are not both shown.\n${okSl}`);

  const badCtx = await renderScreen({ trialBalance: TB_UNBALANCED });
  const badSl = badCtx.subledger();
  assert.strictEqual((badSl.match(/Does Not Tie/g) || []).length, 2,
    `expected 2 red "Does Not Tie" badges when both ar_ties/ap_ties are false; got:\n${badSl}`);
  assert.ok((badSl.match(/>Ties</g) || []).length === 0,
    `no subledger should read plain "Ties" when both flags are false; got:\n${badSl}`);
}

async function testMaliciousAccountNameIsEscaped() {
  const ctx = await renderScreen({ trialBalance: TB_XSS_OK });
  assert.ok(ctx.tbody().indexOf('<img src=x onerror=') === -1,
    `a raw, unescaped <img onerror=...> tag reached the table's innerHTML:\n${ctx.tbody()}`);
  assert.ok(ctx.tbody().indexOf('&lt;img src=x onerror=') !== -1,
    `the malicious account name should appear ESCAPED (as text, not markup):\n${ctx.tbody()}`);
}

async function testFetchFailureShowsVisibleError() {
  const ctx = await renderScreen({ trialBalance: new Error('network down') });
  assert.ok(ctx.tbody().indexOf('Loading') === -1,
    'a failed fetch must not leave the table silently stuck on "Loading…" forever -- the ' +
    'exact bug class _loadPartyLedger/_loadSalesHistory were already fixed for once.');
  assert.ok(ctx.tbody().indexOf('Could not load this list') !== -1,
    `expected a visible error row; got:\n${ctx.tbody()}`);
  assert.strictEqual(ctx.totalDebit(), '—', 'a failed fetch must reset the debit total to a dash, not leave a stale/zero figure.');
  assert.strictEqual(ctx.totalCredit(), '—', 'a failed fetch must reset the credit total to a dash, not leave a stale/zero figure.');
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION PROOFS — every guard above, proven by breaking the thing it watches
// ─────────────────────────────────────────────────────────────────────────────

async function testEveryGuardIsProvenByBreakingIt() {
  const proved = [];

  proved.push(await provesMutation(
    '1. the router case for trial-balance is deleted (screen becomes unreachable)',
    [["      case 'trial-balance': return this._renderTrialBalance(c);\n", '']],
    async (src) => testTheScreenIsReachable(src)));

  proved.push(await provesMutation(
    "2. the retail.reports capability guard is deleted from _renderTrialBalance",
    [[`    if (window.SubsystemApp && !SubsystemApp.hasCapability('retail.reports')) {
      return this._renderCapabilityRestricted(c, {
        icon: 'scale',
        title: t('Trial Balance'),
        message: t('Sales totals and reports are limited to managers and the store owner. Open the till to start ringing sales.'),
      });
    }
`, '']],
    async (src) => {
      const ctx = await renderScreen({ source: src, capabilities: ['retail.sell'] });
      assert.strictEqual(ctx.calls.length, 0, 'guard removed but no fetch happened anyway');
    }));

  proved.push(await provesMutation(
    '3. this._esc() is removed from the account name interpolation',
    // Re-anchored 2026-10-13: POLISH-1's P&L "Other income" table added a
    // 14-space-indented copy of this line, which contains the old 10-space
    // anchor as a substring. Anchoring on the trial balance's own `rows` map
    // keeps the mutation on THIS screen's interpolation and exactly once.
    [['        const rows = accounts.map(a => `<tr>\n          <td style="font-family:monospace;color:var(--sub-accent)">${this._bdi(a.code)}</td>\n          <td style="font-weight:600">${this._esc(a.name)}</td>\n',
      '        const rows = accounts.map(a => `<tr>\n          <td style="font-family:monospace;color:var(--sub-accent)">${this._bdi(a.code)}</td>\n          <td style="font-weight:600">${a.name}</td>\n']],
    async (src) => {
      const ctx = await renderScreen({ source: src, trialBalance: TB_XSS_OK });
      assert.ok(ctx.tbody().indexOf('<img src=x onerror=') === -1, 'still escaped somehow');
    }));

  proved.push(await provesMutation(
    "4. the balanced badge is forced to always render green (ALLOW-HALF mutation -- " +
    "the dangerous direction: an unbalanced ledger must not read as balanced)",
    [[`        statusEl.innerHTML = data.is_balanced
          ? this._badge(t('Balanced'), 'green')
          : this._badge(t('Out of Balance'), 'red');`,
      `        statusEl.innerHTML = this._badge(t('Balanced'), 'green');`]],
    async (src) => {
      const ctx = await renderScreen({ source: src, trialBalance: TB_UNBALANCED });
      assert.ok(ctx.status().indexOf('Out of Balance') !== -1, 'still shows Out of Balance somehow');
    }));

  proved.push(await provesMutation(
    '5. both subledger tie badges are forced to always render "Ties" (green)',
    [
      [`<div style="margin-top:8px">\${sl.ar_ties ? this._badge(t('Ties'), 'green') : this._badge(t('Does Not Tie'), 'red')}</div>`,
       `<div style="margin-top:8px">\${this._badge(t('Ties'), 'green')}</div>`],
      [`<div style="margin-top:8px">\${sl.ap_ties ? this._badge(t('Ties'), 'green') : this._badge(t('Does Not Tie'), 'red')}</div>`,
       `<div style="margin-top:8px">\${this._badge(t('Ties'), 'green')}</div>`],
    ],
    async (src) => {
      const ctx = await renderScreen({ source: src, trialBalance: TB_UNBALANCED });
      assert.strictEqual((ctx.subledger().match(/Does Not Tie/g) || []).length, 2, 'still shows Does Not Tie somehow');
    }));

  console.log(`PASS: ${proved.length} guards proved by breaking the behaviour they watch:`);
  for (const line of proved) console.log('      ' + line);
}

// ─────────────────────────────────────────────────────────────────────────────
// RUNNER — every check runs, every failure is collected
// ─────────────────────────────────────────────────────────────────────────────

const EXPECTED_CHECKS = 8;

async function main() {
  const checks = [
    ['the screen is reachable and gated on retail.reports', testTheScreenIsReachable],
    ['capability-restricted panel shown, no fetch, when retail.reports is missing', testCapabilityRestrictedWhenMissingReports],
    ['every seeded account type gets a real label, not "Other"', testEverySeededAccountTypeGetsARealLabel],
    ['the accounts table renders rows and totals', testAccountsTableRendersRowsAndTotals],
    ['the empty state renders when there are no accounts', testEmptyStateWhenNoAccounts],
    ['the balanced badge reflects is_balanced', testBalancedBadgeReflectsIsBalanced],
    ['the subledger tie badges reflect ar_ties/ap_ties', testSubledgerTieBadgesReflectTiesFlags],
    ['a malicious account name is escaped', testMaliciousAccountNameIsEscaped],
    ['a fetch failure shows a visible error, not a stuck Loading…', testFetchFailureShowsVisibleError],
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
    console.error(`FAIL: retail_gl_trial_balance_ui_test.js ran only ${checks.length - 1} of ` +
      `${EXPECTED_CHECKS} known behavioural checks (plus the mutation-proof meta-check).`);
    process.exitCode = 1;
    return;
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_gl_trial_balance_ui_test.js — ${failures.length} of ` +
      `${checks.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_gl_trial_balance_ui_test.js — ${checks.length} checks`);
}

if (require.main === module) {
  main().catch((err) => {
    console.error('FAIL: retail_gl_trial_balance_ui_test.js (runner)');
    console.error(err && err.stack || err);
    process.exitCode = 1;
  });
}
