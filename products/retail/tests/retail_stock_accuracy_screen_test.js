/**
 * Aura Retail — the STOCK ACCURACY screen (Phase 3, docs/launch-readiness/
 * phase3-ledger-truth.md).
 *
 * The screen exists to turn "the stock is not accurate" into a specific,
 * checkable list. Everything this file asserts follows from that one sentence,
 * and every check here is written against the failure it is meant to catch
 * rather than against the code as written.
 *
 * ── WHAT THIS FILE REFUSES TO LET SHIP ──────────────────────────────────────
 *
 * 1. AN EMPTY LIST THAT MEANS THREE DIFFERENT THINGS. "Everything agrees",
 *    "there was nothing to compare" and "the check did not run" all produce
 *    zero rows. This programme has already shipped one screen where an error
 *    rendered as emptiness and one where a refusal rendered as "nobody sold
 *    anything". So the three are asserted to be DIFFERENT rendered panels, by
 *    `data-sa-state` AND by the words in them — not merely "the code has three
 *    branches".
 *
 * 2. A REPAIR THAT RUNS ON ITS OWN. Automatically repairing a drifted shop
 *    turns a visible discrepancy into an invisible one. The check is
 *    behavioural: render the screen with drift, let it settle, and require
 *    that ZERO requests went to the repair route — and then that calling the
 *    repair function directly, outside the confirm step, still sends nothing.
 *
 * 3. A REFUSED REPAIR REPORTED AS A SUCCESSFUL ONE. A restricted licence
 *    refuses the repair route while leaving the report working, so "repaired
 *    0, skipped 0" is a real thing a real install could be shown instead of
 *    "you are not allowed to do this".
 *
 * ── MUTATION-PROVED ─────────────────────────────────────────────────────────
 *
 * A guard nobody has watched fail is a guard nobody knows is connected. Every
 * behavioural claim below is re-run against a DELIBERATELY BROKEN copy of
 * subsystem-retail.js (see `mutate` / `provesMutation`) and is required to
 * FAIL there. Each mutation anchor must appear exactly once in the source, so
 * a mutation that silently stopped applying is a failure rather than a pass.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_stock_accuracy_screen_test.js
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
const EN = JSON.parse(fs.readFileSync(path.join(FRONTEND, 'locales', 'en.json'), 'utf8'));
const AR = JSON.parse(fs.readFileSync(path.join(FRONTEND, 'locales', 'ar.json'), 'utf8'));

const SRC = fs.readFileSync(RETAIL_JS, 'utf8');

/* The file's own line ending. The mutation anchors below are written with
   "\n" because that is what a source listing looks like, and this repository's
   frontend files are CRLF — an anchor that silently matched nothing would make
   every proof in the last section pass while proving nothing. `mutate` asserts
   each anchor occurs exactly once, so this is belt AND braces. */
const EOL = SRC.indexOf('\r\n') !== -1 ? '\r\n' : '\n';
const nl = (s) => s.replace(/\n/g, EOL);

const SECTION_ID = 'stock-accuracy';
const REPORT_URL = '/api/sub/retail/inventory/reconciliation';
const REPAIR_URL = '/api/sub/retail/inventory/reconciliation/repair';

/* The source region this screen occupies, isolated once. Several checks below
   read the SOURCE rather than the render — "no hardcoded colour anywhere on
   this screen" is a property of every branch, including the ones a given
   fixture never reaches. */
const REGION_START = '  async _renderStockAccuracy(c) {';
const REGION_END = '  // ── SALES BY EMPLOYEE';

function screenSource(src) {
  const s = (src || SRC).indexOf(REGION_START);
  const e = (src || SRC).indexOf(REGION_END);
  assert.ok(s !== -1, 'Could not find _renderStockAccuracy() in subsystem-retail.js.');
  assert.ok(e > s, 'Could not find the end of the Stock accuracy region.');
  const region = (src || SRC).slice(s, e);
  // ANTI-VACUITY: an empty or near-empty slice would make every source-level
  // assertion below pass by reading nothing.
  assert.ok(region.length > 8000,
    `The Stock accuracy source region is only ${region.length} chars — the slice markers moved ` +
    'and every source-level check in this file is now reading almost nothing.');
  return region;
}

// ─────────────────────────────────────────────────────────────────────────────
// FIXTURES
// ─────────────────────────────────────────────────────────────────────────────

/* The drifted shop. Deliberately not one row and not one shape:
 *   * a POSITIVE drift — the balance claims MORE than the ledger can account
 *     for, which is the double-received-PO / resurrected-by-import signature
 *     the screen is supposed to name;
 *   * a NEGATIVE drift, so both sign glyphs are real rendered characters;
 *   * a NULL branch_id with repairable:false — the legacy movement with no
 *     balance row that could ever hold it, which the repair must visibly skip
 *     rather than silently include.
 *
 * net_drift is 5 while the three rows are wrong by 10 units between them, and
 * that gap is the point of the fixture: a screen that showed only the net
 * would be reporting a smaller problem than the shop has.
 *
 * `product_name: null` on the third row is not tidiness — compute_drift LEFT
 * JOINs products, so a movement whose product row is gone comes back with a
 * null name, and rendering it as a blank cell would hide one of the two cases
 * the drift query exists to surface.
 */
const DRIFT = {
  drift_count: 3,
  // 7 - 2.5 + 0.5. Deliberately different from drift_count and from
  // pairs_examined, so an assertion about one of the three headline figures
  // cannot be satisfied by another of them.
  net_drift: 5,
  pairs_examined: 512,
  repair_confirmation: 'RECONCILE-11111111-2222-3333-4444-555555555555',
  rows: [
    { product_id: 'p1', product_name: 'Coffee beans 250g', sku: 'CB250', branch_id: 1,
      branch_name: 'Main Branch', stored_balance: 24, ledger_balance: 17, drift: 7, repairable: true },
    { product_id: 'p3', product_name: 'Sold out item', sku: 'SO1', branch_id: 2,
      branch_name: 'Airport Kiosk', stored_balance: 0, ledger_balance: 2.5, drift: -2.5, repairable: true },
    { product_id: 'p9', product_name: null, sku: null, branch_id: null, branch_name: null,
      stored_balance: 0, ledger_balance: -0.5, drift: 0.5, repairable: false },
  ],
};

/** A healthy shop: 512 pairs were compared and every one agreed. */
const CLEAN = {
  drift_count: 0, net_drift: 0, pairs_examined: 512,
  repair_confirmation: DRIFT.repair_confirmation, rows: [],
};

/** A company with no stock records at all. Same zero rows, different fact. */
const NOTHING = {
  drift_count: 0, net_drift: 0, pairs_examined: 0,
  repair_confirmation: DRIFT.repair_confirmation, rows: [],
};

/* The ENVELOPES the routes actually put on the wire. Kept separate from the
   payloads above because `{status: 'success', data: ...}` versus a bare
   `{error: ...}` with no `status` key at all IS the difference between the
   states this screen has to tell apart, and a fixture that blurred the two
   would be testing a client contract the server does not have. */
const REPORT_DRIFT = { status: 'success', data: DRIFT };
const REPORT_CLEAN = { status: 'success', data: CLEAN };
const REPORT_NOTHING = { status: 'success', data: NOTHING };
// _require_company_admin's 403: `{'error': ...}`, no `status` key.
const CHECK_REFUSED = { error: 'Administrator permission required.' };
// The route's own 400: the `{'status': 'error', 'message': ...}` envelope.
const CHECK_BROKEN = { status: 'error', message: 'Could not reconcile inventory.' };
const REPAIR_REFUSED = { error: 'This licence is restricted; stock cannot be adjusted.' };

/* A repair that ran and refused TWO rows for TWO DIFFERENT REASONS, which is
 * the shape repair_drift actually returns (core/retail/stock_reconciliation.py
 * SKIP_NO_BRANCH / SKIP_NO_LEDGER_HISTORY).
 *
 * The second row matters more than the first: it is `repairable: true` in the
 * REPORT and still refused by the REPAIR, because "this (product, branch) has
 * no movement rows at all" is decided at repair time and is invisible to
 * compute_drift. A screen that derived its skip reasons from the report's own
 * `repairable` flag would render this row under the wrong reason and send the
 * owner off to assign a branch that is already there.
 */
const REPAIR_OK = {
  status: 'success',
  data: {
    repaired_count: 2,
    skipped_count: 2,
    repaired: [],
    skipped: [
      { product_id: 'p9', branch_id: null, drift: 0.5, repairable: false, skip_reason: 'no_branch' },
      { product_id: 'p7', branch_id: 1, drift: 12, repairable: true, skip_reason: 'no_ledger_history' },
    ],
  },
};

/* Values that come from the DATABASE (or from the server's own error strings)
   and are therefore not this build's copy. Listed by hand so the
   untranslated-literal sweep below cannot be satisfied by quietly widening a
   pattern. */
const RENDERED_DATA_VALUES = new Set([
  'Coffee beans 250g', 'CB250', 'Main Branch',
  'Sold out item', 'SO1', 'Airport Kiosk',
  'p9', 'bogus-state',
  CHECK_REFUSED.error, CHECK_BROKEN.message, REPAIR_REFUSED.error,
]);

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
 *   role         — SubsystemApp.role ('' = the session has not resolved yet)
 *   report       — the GET /inventory/reconciliation payload
 *   repair       — the POST .../repair payload
 *   translate    — t() implementation (used for the Arabic pass)
 */
function loadRetailSystem(opts) {
  const o = opts || {};
  const calls = [];
  const tKeys = new Set();
  const els = Object.create(null);
  const getEl = (id) => (els[id] || (els[id] = makeStub({ id })));

  const sandbox = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    t: (s) => { tKeys.add(s); return o.translate ? o.translate(s) : s; },
    fetch: (url, init) => {
      const method = ((init && init.method) || 'GET').toUpperCase();
      let body = null;
      if (init && typeof init.body === 'string') {
        try { body = JSON.parse(init.body); } catch (e) { body = init.body; }
      }
      calls.push({ method, url: String(url), body });
      const payload = method === 'POST'
        ? (o.repair === undefined ? REPAIR_OK : o.repair)
        : (o.report === undefined ? REPORT_CLEAN : o.report);
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
    role: o.role === undefined ? '' : o.role,
    hasCapability: (code) => (o.capabilities || ['retail.reports']).includes(code),
  };
  sandbox.window = sandbox;
  sandbox.Chart = function ChartStub() { return { destroy() {} }; };
  sandbox.Chart.getChart = () => null;

  vm.createContext(sandbox);
  vm.runInContext(o.source || SRC, sandbox, { filename: RETAIL_JS });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return { rs: sandbox.RetailSystem, calls, tKeys, els };
}

async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

/** Render the screen through the ROUTER, so the route id is load-bearing. */
async function renderScreen(opts) {
  const ctx = loadRetailSystem(opts);
  const host = makeStub({ id: 'sub-content' });
  ctx.els['sub-content'] = host;
  await ctx.rs.render(SECTION_ID);
  await settle();
  ctx.frame = host.innerHTML;
  ctx.body = () => (ctx.els['stka-body'] ? ctx.els['stka-body'].innerHTML : '');
  return ctx;
}

/** The one panel currently in #stka-body, as a parsed element. */
function panelOf(ctx) {
  const html = ctx.body();
  const root = dom.parseFragment(html);
  const stated = dom.allElements(root).filter((el) => el.attrs['data-sa-state'] !== undefined);
  assert.strictEqual(
    stated.length, 1,
    `#stka-body holds ${stated.length} elements carrying data-sa-state, expected exactly 1. ` +
    'One state must render exactly one panel: zero is the empty screen this file exists to ' +
    'refuse, and two means two states are on screen at once.\n' + html.slice(0, 400)
  );
  return { el: stated[0], state: stated[0].attrs['data-sa-state'], html, root };
}

function visibleText(node) {
  return dom.textOf(node).replace(/\s+/g, ' ').trim();
}

const POSTS_TO_REPAIR = (ctx) => ctx.calls.filter((c) => c.method === 'POST' && c.url.indexOf(REPAIR_URL) !== -1);
const GETS_TO_REPORT = (ctx) => ctx.calls.filter((c) => c.method === 'GET' && c.url.indexOf(REPORT_URL) !== -1);

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION HARNESS
// ─────────────────────────────────────────────────────────────────────────────

/** Apply textual mutations, requiring each anchor to be present EXACTLY once. */
function mutate(pairs) {
  let src = SRC;
  for (const [rawFind, rawReplace] of pairs) {
    const find = nl(rawFind);
    const replace = nl(rawReplace);
    const hits = src.split(find).length - 1;
    assert.strictEqual(
      hits, 1,
      `Mutation anchor occurs ${hits} time(s), expected exactly 1:\n  ${JSON.stringify(find)}\n\n` +
      'A mutation that no longer applies would let the proof below pass while proving ' +
      'nothing — which is exactly the vacuity these proofs exist to close. Re-anchor it.'
    );
    src = src.replace(find, replace);
  }
  return src;
}

/**
 * Run `check` against a broken build and require it to FAIL.
 *
 * `check` receives the mutated source and must perform the same assertion the
 * real check performs. If it passes, the guard is not connected to the thing
 * it claims to guard, and that is reported as this file's own failure.
 */
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
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 90)}]`;
}

// ─────────────────────────────────────────────────────────────────────────────
// CHECKS
// ─────────────────────────────────────────────────────────────────────────────

function testTheScreenIsReachable() {
  // The route, read out of the product rather than assumed.
  const start = SRC.indexOf('  render(sectionId) {');
  assert.ok(start !== -1, 'Could not find RetailSystem.render(sectionId).');
  const body = SRC.slice(start, SRC.indexOf('\n  },', start));
  assert.ok(
    new RegExp(`case\\s+'${SECTION_ID}'\\s*:`).test(body),
    `RetailSystem.render() has no case for '${SECTION_ID}', so the screen is unreachable ` +
    'through the router — the same served-but-unlinked shape licensing.html shipped in.'
  );

  // ...and the nav entry that points at it, on BOTH the axes the route gates
  // on. `ownerOnly` (this USER's role) is what _require_company_admin checks;
  // `adminOnly` would be the DEVICE axis, and picking it would hide the shop's
  // own stock report from the owner on a second terminal while offering it to
  // a manager standing at the admin till.
  const shell = fs.readFileSync(APP_SHELL_JS, 'utf8');
  const entry = shell.split('\n').find((l) => l.indexOf(`id: '${SECTION_ID}'`) !== -1);
  assert.ok(entry, `app-shell.js has no nav entry for '${SECTION_ID}'.`);
  assert.ok(/ownerOnly:\s*true/.test(entry),
    `The '${SECTION_ID}' nav entry is not ownerOnly. Its route calls _require_company_admin(), ` +
    `which reads session['mt_role'] — the USER axis. Entry:\n  ${entry.trim()}`);
  assert.ok(!/adminOnly/.test(entry),
    `The '${SECTION_ID}' nav entry uses adminOnly (the DEVICE axis), which is the wrong one: ` +
    'the owner loses this screen on any second terminal. Entry:\n  ' + entry.trim());
  assert.ok(/capability:\s*'retail\.reports'/.test(entry),
    `The '${SECTION_ID}' nav entry carries no retail.reports capability, but the route does ` +
    `(@mt_require_capability(CAP_REPORTS)). Entry:\n  ${entry.trim()}`);

  console.log(`PASS: '${SECTION_ID}' is routed, and its nav entry gates on both axes the route does`);
}

async function testTheThreeEmptyStatesAreDistinguishable(src) {
  const clean = await renderScreen({ source: src, report: REPORT_CLEAN });
  const nothing = await renderScreen({ source: src, report: REPORT_NOTHING });
  const refused = await renderScreen({ source: src, report: CHECK_REFUSED });
  const broken = await renderScreen({ source: src, report: CHECK_BROKEN });

  // ANTI-VACUITY. Every comparison below is over rendered panels; a screen
  // that failed to render would give four empty strings that are all
  // "different from each other" in no useful sense.
  for (const [name, ctx] of [['clean', clean], ['nothing', nothing], ['refused', refused], ['broken', broken]]) {
    assert.ok(ctx.body().length > 120,
      `The '${name}' case rendered ${ctx.body().length} chars into #stka-body. The screen did not ` +
      'build, so the comparison below is between empty strings.');
  }

  const seen = {
    clean: panelOf(clean), nothing: panelOf(nothing),
    refused: panelOf(refused), broken: panelOf(broken),
  };

  assert.strictEqual(seen.clean.state, 'clean',
    'A check that ran over 512 pairs and found nothing wrong must say so as its own state.');
  assert.strictEqual(seen.nothing.state, 'nothing',
    'pairs_examined === 0 means NOTHING WAS COMPARED. Rendering that as "everything agrees" is ' +
    'a claim about evidence that does not exist — the same shape as the screen that reported ' +
    '"nobody sold anything" for a refusal.');
  assert.strictEqual(seen.refused.state, 'failed',
    'A 403 from _require_company_admin must render as "the check did not run", never as a ' +
    'clean bill of health. An error that renders as emptiness has shipped here before.');
  assert.strictEqual(seen.broken.state, 'failed',
    'A 400 error envelope must render as "the check did not run".');

  // Different STATE is not enough — the operator reads words, not attributes.
  const texts = {
    clean: visibleText(seen.clean.el), nothing: visibleText(seen.nothing.el),
    refused: visibleText(seen.refused.el), broken: visibleText(seen.broken.el),
  };
  const names = Object.keys(texts);
  for (let i = 0; i < names.length; i++) {
    for (let j = i + 1; j < names.length; j++) {
      if (texts[names[i]] === texts[names[j]]) {
        assert.fail(
          `The '${names[i]}' and '${names[j]}' states render IDENTICAL text, so the operator ` +
          `cannot tell them apart:\n  ${texts[names[i]]}`
        );
      }
    }
  }

  // The server's own words survive, because "why did it not run" is the whole
  // content of a refusal.
  assert.ok(texts.refused.indexOf(CHECK_REFUSED.error) !== -1,
    `The refusal panel does not carry the server's message. Text:\n  ${texts.refused}`);
  assert.ok(texts.broken.indexOf(CHECK_BROKEN.message) !== -1,
    `The failure panel does not carry the server's message. Text:\n  ${texts.broken}`);

  console.log('PASS: no-drift, nothing-to-check and check-failed are three different panels with ' +
    'three different sentences');
}

async function testTheDriftPanelNamesWhatIsWrong(src) {
  const ctx = await renderScreen({ source: src, report: REPORT_DRIFT });

  // ANTI-VACUITY, AND THE MOST IMPORTANT ONE IN THIS FILE. A fixture with no
  // drift proves nothing about a screen whose entire job is showing drift.
  assert.ok(DRIFT.drift_count > 0 && DRIFT.rows.length === DRIFT.drift_count,
    'The drift fixture carries no drift, so every assertion below is about a screen in its ' +
    'empty state.');
  assert.ok(DRIFT.rows.some((r) => r.drift > 0) && DRIFT.rows.some((r) => r.drift < 0),
    'The drift fixture has no signed pair, so "the sign is drawn" would be a claim about one ' +
    'direction only.');
  assert.ok(Math.abs(DRIFT.net_drift) < DRIFT.rows.reduce((n, r) => n + Math.abs(r.drift), 0),
    'The fixture\'s net drift equals its gross, so it cannot show why the net alone lies.');

  const panel = panelOf(ctx);
  assert.strictEqual(panel.state, 'drift');
  const text = visibleText(panel.el);

  // NET AND COUNT, TOGETHER. Net alone lies: a +10 and a -10 net to zero
  // while two products are wrong.
  const els = dom.allElements(panel.root);
  const kpiValues = els.filter((el) => el.classes.includes('ret-kpi-value')).map(visibleText);
  assert.ok(kpiValues.length >= 3,
    `Only ${kpiValues.length} headline figures. The net, the count and how much was compared ` +
    'are three different facts and all three belong in the headline.');
  assert.ok(kpiValues.some((v) => v === '+' + DRIFT.net_drift),
    `The net drift is not in the headline as a signed figure. Headline values: ${JSON.stringify(kpiValues)}`);
  assert.ok(kpiValues.some((v) => v === String(DRIFT.drift_count)),
    `The COUNT of disagreeing figures is not beside the net. A screen showing only the net ` +
    `reports a smaller problem than the shop has. Headline values: ${JSON.stringify(kpiValues)}`);
  assert.ok(kpiValues.some((v) => v === String(DRIFT.pairs_examined)),
    `How many pairs were compared is not shown, so "3 wrong" has no denominator. ` +
    `Headline values: ${JSON.stringify(kpiValues)}`);

  // WHICH (product, branch) — a row per disagreement, with both figures and
  // the signed difference.
  const bodyRows = els.filter((el) => el.tag === 'tr' &&
    (el.children || []).filter((n) => n.type === 'element' && n.tag === 'td').length >= 3);
  assert.strictEqual(bodyRows.length, DRIFT.rows.length,
    `The table rendered ${bodyRows.length} data rows for ${DRIFT.rows.length} drifted pairs. ` +
    'A screen that lists fewer than it found is worse than one that lists none.');

  const rowTexts = bodyRows.map(visibleText);
  assert.ok(rowTexts.some((r) => r.indexOf('Coffee beans 250g') !== -1 && r.indexOf('Main Branch') !== -1),
    `The product and its BRANCH must be on the same row — "which figures disagree" is a ` +
    `(product, branch) question. Rows:\n  ${rowTexts.join('\n  ')}`);
  assert.ok(rowTexts.some((r) => r.indexOf('24') !== -1 && r.indexOf('17') !== -1),
    `The row does not carry both figures. "+7" with nothing to compare it against is not ` +
    `checkable. Rows:\n  ${rowTexts.join('\n  ')}`);

  // THE SIGN IS DRAWN, BOTH WAYS. Positive means the balance claims MORE than
  // the ledger accounts for, and an unsigned drift column would hide the
  // direction — which is the only part of the number that names the bug.
  assert.ok(/\+7\b/.test(text),
    `A positive drift is not rendered with an explicit "+". Positive is the direction that ` +
    `means the balance overstates the shelf, and unsigned it is unreadable. Panel:\n  ${text}`);
  assert.ok(text.indexOf('−2.5') !== -1,
    'A negative drift is not rendered with U+2212 MINUS. A hyphen inside a column of quantities ' +
    'reads as punctuation; the codebase already made this decision for money (_moneyDigits).');

  // The row a repair CANNOT touch is visible as such, not silently listed as
  // if it were fixable.
  assert.ok(text.indexOf('Needs a branch') !== -1,
    'The NULL-branch row is not marked. inventory_balances.branch_id is NOT NULL, so there is ' +
    'no cached figure for these movements to correct — presenting them as ordinary drift ' +
    'promises a repair that will skip them.');

  console.log('PASS: the drift panel names which (product, branch) disagree, signed, with the net ' +
    'AND the count in the headline');
}

async function testRepairNeverRunsByItself(src) {
  const ctx = await renderScreen({ source: src, report: REPORT_DRIFT });

  // ANTI-VACUITY: the screen really did talk to the server, so "no POST" is
  // not simply "no requests at all".
  assert.strictEqual(GETS_TO_REPORT(ctx).length, 1,
    `The screen made ${GETS_TO_REPORT(ctx).length} calls to the report route, expected 1. ` +
    'If it made none, "it did not repair" is true of a screen that did nothing.');
  assert.strictEqual(panelOf(ctx).state, 'drift', 'The screen is not showing the drift it was given.');

  assert.deepStrictEqual(POSTS_TO_REPAIR(ctx), [],
    'Loading the Stock accuracy screen sent a repair request. Automatically repairing a drifted ' +
    'shop turns a visible discrepancy into an invisible one — and destroys the evidence that one ' +
    'of the five balance writers is broken.');

  // ...and the repair function is inert when it is called outside the confirm
  // step, which is what makes "owner-initiated" a property of the code rather
  // than of the current call sites.
  await ctx.rs._repairStockAccuracy();
  await settle();
  assert.deepStrictEqual(POSTS_TO_REPAIR(ctx), [],
    '_repairStockAccuracy() sent a request without the confirm step. Any future caller — a ' +
    'retry, a keyboard shortcut, a stale handler — would then be able to rewrite a shop\'s stock ' +
    'with no human having been told what it was about to do.');

  console.log('PASS: the repair does not run on load, and does not run when called outside the ' +
    'confirm step');
}

async function testTheRepairSaysWhatItWillDoAndReportsWhatItDid(src) {
  const ctx = await renderScreen({ source: src, report: REPORT_DRIFT });

  // Step one: the confirm panel, with COUNTS rather than a general promise.
  ctx.rs._askRepairStockAccuracy();
  const confirm = panelOf(ctx);
  assert.strictEqual(confirm.state, 'confirm');
  const confirmText = visibleText(confirm.el);
  const fixable = DRIFT.rows.filter((r) => r.repairable).length;
  const stranded = DRIFT.rows.length - fixable;
  assert.ok(fixable > 0 && stranded > 0,
    'The fixture has no repairable/unrepairable split, so the confirm panel cannot be shown to ' +
    'distinguish them.');
  assert.ok(new RegExp('at most\\s*' + fixable).test(confirmText),
    `The confirm step does not say how many balances it will rewrite (${fixable}). ` +
    `"Are you sure?" without a number is not a confirmation. Panel:\n  ${confirmText}`);
  assert.ok(new RegExp('no branch\\s*' + stranded).test(confirmText),
    `The confirm step does not say how many rows it will LEAVE ALONE (${stranded}). An owner ` +
    `who is not told will read a clean result afterwards as "all fixed". Panel:\n  ${confirmText}`);
  // ...and it does not PROMISE the number it cannot know. repair_drift also
  // refuses a balance the ledger has no history for, per row, at repair time,
  // and that refusal is invisible to the report this panel is drawn from.
  assert.ok(/no history for at all/.test(confirmText),
    'The confirm step claims an exact repair count while saying nothing about the second ' +
    'refusal (a balance with no ledger history at all). Every time that guard fires, the ' +
    `result panel would then read as a failure against a promise this screen made.\n  ${confirmText}`);
  assert.ok(/create or change\s*0/.test(confirmText),
    'The confirm step does not state that the repair creates no stock movement. That is the ' +
    'single most important thing about it: a correcting movement would move the very total ' +
    'being reconciled against.\n  ' + confirmText);
  assert.deepStrictEqual(POSTS_TO_REPAIR(ctx), [],
    'Opening the confirm step already sent the repair.');

  // Cancelling really cancels.
  ctx.rs._cancelRepairStockAccuracy();
  assert.strictEqual(panelOf(ctx).state, 'drift', 'Cancel did not return to the drift report.');
  assert.deepStrictEqual(POSTS_TO_REPAIR(ctx), [], 'Cancelling sent the repair anyway.');

  // Step two: confirmed. Exactly one request, carrying the token the SERVER
  // supplied — not one this build assembled from a format it copied.
  ctx.rs._askRepairStockAccuracy();
  await ctx.rs._repairStockAccuracy();
  await settle();
  const posts = POSTS_TO_REPAIR(ctx);
  assert.strictEqual(posts.length, 1,
    `The confirmed repair sent ${posts.length} requests, expected exactly 1.`);
  assert.strictEqual(posts[0].body && posts[0].body.confirm, DRIFT.repair_confirmation,
    'The repair did not echo the confirmation token the report handed it. The token embeds the ' +
    'company id and is compared against the caller\'s own session, so a client that spells it ' +
    'out itself is a second copy of a format that will drift.\n  sent: ' +
    JSON.stringify(posts[0].body));

  // ...and it reports what it did, INCLUDING what it skipped.
  const done = panelOf(ctx);
  assert.strictEqual(done.state, 'repaired');
  const doneText = visibleText(done.el);
  assert.ok(new RegExp('rewritten\\s*' + REPAIR_OK.data.repaired_count).test(doneText),
    `The result does not say how many balances were rewritten. Panel:\n  ${doneText}`);
  assert.ok(new RegExp('left alone\\s*' + REPAIR_OK.data.skipped_count).test(doneText),
    `The result does not say how many rows were SKIPPED. A repair that silently leaves rows ` +
    `wrong, reported as a success, is the invisible-discrepancy failure again. Panel:\n  ${doneText}`);

  // EVERY REFUSAL UNDER ITS OWN REASON. The owner ACTS on the reason: one
  // says "go and assign a branch", the other says "this balance has no ledger
  // behind it at all". Collapsing the two, or attributing one row's refusal
  // to the other's cause, sends somebody to fix the wrong thing.
  assert.ok(/carry no branch/.test(doneText),
    `The result does not name the no-branch refusal. Panel:\n  ${doneText}`);
  assert.ok(/no history for them at all/.test(doneText),
    'The result does not name the no-ledger-history refusal. That row is `repairable: true` in ' +
    'the report, so a screen deriving reasons from the report instead of from the repair would ' +
    `label it as a missing branch — a branch it already has.\n  ${doneText}`);
  assert.ok(!/did not say why/.test(doneText),
    `A known skip reason was rendered as unrecognised. Panel:\n  ${doneText}`);

  // An UNKNOWN reason must say so rather than being folded into a known one.
  const future = await renderScreen({
    source: src, report: REPORT_DRIFT,
    repair: { status: 'success', data: { repaired_count: 1, skipped_count: 1, repaired: [],
      skipped: [{ product_id: 'p5', branch_id: 1, skip_reason: 'a_reason_this_build_predates' }] } },
  });
  future.rs._askRepairStockAccuracy();
  await future.rs._repairStockAccuracy();
  await settle();
  const futureText = visibleText(panelOf(future).el);
  assert.ok(/did not say why/.test(futureText),
    'A skip reason this build does not recognise was rendered as one it does. Guessing which ' +
    `refusal happened is how a screen tells an owner the wrong thing about their stock.\n  ${futureText}`);
  assert.ok(!/carry no branch/.test(futureText) && !/no history for them at all/.test(futureText),
    `An unrecognised reason was labelled with a known one. Panel:\n  ${futureText}`);

  // The screen does NOT silently re-run the check on top of that report:
  // replacing "2 rewritten, 1 skipped" with a green tick is how a repair
  // becomes something nobody can audit afterwards.
  assert.strictEqual(GETS_TO_REPORT(ctx).length, 1,
    'The screen re-ran the check by itself after repairing, so the report of what happened was ' +
    'overwritten before anyone could read it.');

  console.log('PASS: the repair is a two-step, says what it will do with numbers, sends the ' +
    "server's own token, and reports what it did including what it skipped");
}

async function testARefusedRepairIsNotAReportOfSuccess(src) {
  const ctx = await renderScreen({ source: src, report: REPORT_DRIFT, repair: REPAIR_REFUSED });
  ctx.rs._askRepairStockAccuracy();
  await ctx.rs._repairStockAccuracy();
  await settle();

  assert.strictEqual(POSTS_TO_REPAIR(ctx).length, 1, 'The repair was never attempted.');
  const panel = panelOf(ctx);
  assert.strictEqual(panel.state, 'repair-failed',
    'A refused repair rendered as state "' + panel.state + '". A restricted licence refuses this ' +
    'route while leaving the report working, so "repaired 0, skipped 0" is a real thing a real ' +
    'install would be shown instead of "you are not allowed to do this".');
  const text = visibleText(panel.el);
  assert.ok(text.indexOf(REPAIR_REFUSED.error) !== -1,
    `The refusal panel does not carry the server's reason. Panel:\n  ${text}`);
  assert.ok(/nothing|no cached balance was changed/i.test(text),
    `The refusal panel does not say that nothing changed. Panel:\n  ${text}`);

  // ...and it is a different panel from a repair that RAN and moved nothing.
  const ran = await renderScreen({
    source: src, report: REPORT_DRIFT,
    repair: { status: 'success', data: { repaired_count: 0, skipped_count: 0, repaired: [], skipped: [] } },
  });
  ran.rs._askRepairStockAccuracy();
  await ran.rs._repairStockAccuracy();
  await settle();
  const ranPanel = panelOf(ran);
  assert.strictEqual(ranPanel.state, 'repaired');
  assert.notStrictEqual(visibleText(ranPanel.el), text,
    'A refused repair and a repair that ran and changed nothing render the same words.');

  console.log('PASS: a refused repair is its own panel, distinct from a repair that ran and moved ' +
    'nothing');
}

async function testTheGatesReturnBeforeTheRequest(src) {
  // No retail.reports: the restricted panel, and — the point — NO REQUEST.
  // A screen that fetches, collects a 403 and only then hides has already
  // generated the error it was supposed to prevent.
  const noCap = await renderScreen({ source: src, capabilities: [], report: REPORT_DRIFT });
  assert.deepStrictEqual(noCap.calls, [],
    'The screen fetched before checking the capability. Calls:\n  ' +
    noCap.calls.map((c) => c.method + ' ' + c.url).join('\n  '));
  assert.ok(/limited to managers and the store owner/.test(noCap.frame),
    'A user without retail.reports was not shown the restricted panel. Frame:\n' +
    noCap.frame.slice(0, 400));

  // A manager (role !== 'admin') holds retail.reports and is still refused,
  // because _require_company_admin is the USER axis and no capability code
  // expresses "owns the shop".
  const manager = await renderScreen({ source: src, role: 'cashier', report: REPORT_DRIFT });
  assert.deepStrictEqual(manager.calls, [],
    'A non-admin role reached the request. The route answers 403, so this is the ' +
    'click-through-to-a-403 bug the Reports nav entry already had once.');
  assert.ok(/limited to the store owner/.test(manager.frame),
    'A non-admin was not shown a restricted panel. Frame:\n' + manager.frame.slice(0, 400));

  // ANTI-VACUITY, AND A REAL BEHAVIOUR: an UNRESOLVED session ('' role) must
  // still render. Fail-open on "unknown" is the convention hasCapability()
  // documents, and a guard that refused here would break the screen on every
  // build that cannot answer the question — including this test harness, at
  // which point the two assertions above would be passing for the wrong
  // reason.
  const unknown = await renderScreen({ source: src, role: '', report: REPORT_DRIFT });
  assert.strictEqual(GETS_TO_REPORT(unknown).length, 1,
    'The screen refused a session whose role has not resolved yet. "Unknown" is not "denied": ' +
    'refusing here means the two checks above would pass even with the guards deleted.');
  assert.strictEqual(panelOf(unknown).state, 'drift');

  console.log('PASS: both gates return BEFORE the request, and an unresolved session still renders');
}

async function testEveryFigureIsIsolatedAndTabular(src) {
  const ctx = await renderScreen({ source: src, report: REPORT_DRIFT });
  const panel = panelOf(ctx);
  const els = dom.allElements(panel.root);

  // Every figure on this screen sits beside Arabic text in Arabic, and a
  // signed run like "+7" has NO strong directional character at all, so
  // without an explicit direction the bidi algorithm resolves it against the
  // paragraph and the sign lands on the far side of the digits.
  const nums = els.filter((el) => el.classes.includes('num'));
  assert.ok(nums.length >= 8,
    `Only ${nums.length} .num figures on a screen showing 3 rows x 3 figures plus 3 headline ` +
    'tiles. The figures are not going through the numeric class, so neither the tabular ' +
    'alignment nor rtl.css\'s direction:ltr reaches them.');
  const unisolated = [];
  for (const el of nums) {
    const bdi = (el.children || []).filter((n) => n.type === 'element' && n.tag === 'bdi');
    if (bdi.length !== 1) { unisolated.push(`${visibleText(el)} — no <bdi> child`); continue; }
    if (bdi[0].attrs.dir !== 'ltr') {
      unisolated.push(`${visibleText(el)} — <bdi dir="${bdi[0].attrs.dir}">, which resolves ` +
        'against the paragraph when the run carries no strong character (i.e. here)');
    }
  }
  assert.deepStrictEqual(unisolated, [],
    'These figures are not direction-isolated with an explicit direction:\n  ' +
    unisolated.join('\n  ') +
    '\n\nIn Arabic a "+7" or a "−2.5" takes its direction from the surrounding paragraph. ' +
    'Wrap it in RetailSystem._bdi(), which emits <bdi dir="ltr"> — isolation AND the stated ' +
    'direction are both required.');

  console.log(`PASS: all ${nums.length} figures are tabular (.num) and isolated with an explicit ` +
    'direction');
}

function testNoColourIsWrittenByHand() {
  const region = screenSource();
  // A hex literal or an rgb() on this screen would be a colour that the token
  // layer never sees — which is precisely how the POS "Held" button reached
  // 1.48:1 and stayed there through a redesign.
  const hex = region.match(/#[0-9a-fA-F]{3,8}\b/g) || [];
  assert.deepStrictEqual(hex, [],
    `Hardcoded colour literal(s) on the Stock accuracy screen: ${JSON.stringify(hex)}. ` +
    'Use a design token; a literal is invisible to the contrast sweep\'s token layer.');
  const fn = region.match(/\brgba?\s*\(/g) || [];
  assert.deepStrictEqual(fn, [],
    `Hardcoded rgb()/rgba() colour(s) on the Stock accuracy screen: ${JSON.stringify(fn)}.`);

  // ...and every colour it DOES name is a token reference.
  const declared = [...region.matchAll(/(?:^|[;"'\s])(color|background|border-color)\s*:\s*([^;"'`]+)/g)]
    .map((m) => `${m[1]}:${m[2].trim()}`);
  assert.ok(declared.length >= 10,
    `Only ${declared.length} colour declarations found on this screen — the scan is broken, so ` +
    '"they are all tokens" would be a claim about almost nothing.');
  const notTokens = declared.filter((d) => d.indexOf('var(--') === -1);
  assert.deepStrictEqual(notTokens, [],
    'These colour declarations do not reference a design token:\n  ' + notTokens.join('\n  '));

  // The quantity columns must not borrow the MONEY helpers. A drift is a
  // count of units; _money() would print a currency symbol on it and
  // .money--negative would claim a financial semantic this screen does not
  // have.
  // `this._money(` / `this._moneyDigits(` — the CALL, not the word, so the
  // comment on _qty() explaining why it does not use them is not itself a
  // violation.
  assert.ok(!/this\._money\(/.test(region) && !/this\._moneyDigits\(/.test(region),
    'The Stock accuracy screen formats a quantity through the money helpers. A drift of 7 units ' +
    'is not $7.00, and the accounting parentheses would read as an amount owed.');

  console.log(`PASS: all ${declared.length} colours on this screen are tokens, and no quantity ` +
    'borrows the money formatter');
}

async function testEveryStringOnEveryStateIsTranslated(src) {
  // Every state, because a catalog key that only exists on the happy path is
  // an English sentence waiting on an Arabic install's worst day.
  const states = [];
  const push = async (name, opts, drive) => {
    const ctx = await renderScreen(Object.assign({ source: src }, opts));
    if (drive) await drive(ctx);
    await settle();
    states.push([name, ctx]);
  };
  await push('clean', { report: REPORT_CLEAN });
  await push('nothing', { report: REPORT_NOTHING });
  await push('failed', { report: CHECK_REFUSED });
  await push('drift', { report: REPORT_DRIFT });
  await push('confirm', { report: REPORT_DRIFT }, (c) => c.rs._askRepairStockAccuracy());
  await push('repaired', { report: REPORT_DRIFT }, async (c) => {
    c.rs._askRepairStockAccuracy(); await c.rs._repairStockAccuracy();
  });
  await push('repair-failed', { report: REPORT_DRIFT, repair: REPAIR_REFUSED }, async (c) => {
    c.rs._askRepairStockAccuracy(); await c.rs._repairStockAccuracy();
  });
  // The state machine's own last resort. Rendered because "an unknown state
  // shows nothing" would be the empty screen again, arrived at from the
  // inside.
  await push('unknown', { report: REPORT_DRIFT }, (c) => {
    c.rs._stockAccuracy.state = 'bogus-state';
    c.rs._paintStockAccuracy();
  });

  const rendered = states.map(([n, c]) => `${n}=${panelOf(c).state}`);
  assert.ok(states.every(([, c]) => c.body().length > 100),
    `A state rendered (almost) nothing: ${rendered.join(', ')}`);
  assert.strictEqual(panelOf(states[states.length - 1][1]).state, 'unknown',
    'An unrecognised state renders no panel of its own, so a bug in this screen\'s own state ' +
    'machine would show the operator a blank card.');

  // (a) Every key the render handed to t() exists in both catalogs.
  const keys = new Set();
  for (const [, c] of states) for (const k of c.tKeys) keys.add(k);
  assert.ok(keys.size >= 30,
    `Only ${keys.size} strings passed through t() across ${states.length} states — this screen ` +
    'carries far more copy than that, so treat it as a harness failure.');
  const missing = [...keys].filter((k) => !(k in EN) || !(k in AR) || !String(AR[k]).trim() || EN[k] === AR[k]);
  assert.deepStrictEqual(missing, [],
    'These strings are passed to t() but have no real Arabic:\n  ' +
    missing.map((k) => `${JSON.stringify(k)} — en:${k in EN ? 'yes' : 'MISSING'} ` +
      `ar:${k in AR ? (String(AR[k]).trim() ? (EN[k] === AR[k] ? 'UNTRANSLATED' : 'yes') : 'EMPTY') : 'MISSING'}`)
      .join('\n  ') +
    '\n\nAdd each to BOTH locales/en.json and locales/ar.json with a real translation.');

  // (b) Nothing reaches the operator WITHOUT passing through t(). i18n.js's
  //     DOM sweep rescues a text node only when its FULL trimmed text is a
  //     catalog key, so a number glued to words is unreachable — that is why
  //     every figure on this screen is its own element.
  const offenders = [];
  for (const [name, c] of states) {
    const root = dom.parseFragment(c.frame + c.body());
    for (const el of dom.allElements(root)) {
      if (el.tag === 'style' || el.tag === 'script') continue;
      for (const attr of ['placeholder', 'title', 'aria-label', 'alt']) {
        const v = (el.attrs[attr] || '').trim();
        if (v && /[A-Za-z]{3}/.test(v) && !keys.has(v) && !RENDERED_DATA_VALUES.has(v)) {
          offenders.push(`[${name}] @${attr} ${JSON.stringify(v)} in ${dom.describe(el)} ` +
            '(i18n.js never sweeps attributes, so there is no rescue)');
        }
      }
      if (el.attrs['aria-hidden'] === 'true') continue;
      for (const child of el.children || []) {
        if (child.type !== 'text') continue;
        const raw = child.text.replace(/\s+/g, ' ').trim();
        if (!raw || !/[A-Za-z]{3}/.test(raw)) continue;
        if (keys.has(raw) || RENDERED_DATA_VALUES.has(raw)) continue;
        offenders.push(`[${name}] ${JSON.stringify(raw)} in ${dom.describe(el)}`);
      }
    }
  }
  assert.deepStrictEqual(offenders, [],
    'These strings reach the operator without passing through t(), and the DOM sweep cannot ' +
    'rescue them (their full node text is not a catalog key):\n  ' + offenders.join('\n  '));

  // (c) An emoji sharing a node with words defeats the sweep even when the key
  //     exists — the exact defect that made "🛒 Open POS" unreachable.
  const shared = [];
  for (const [name, c] of states) {
    const root = dom.parseFragment(c.frame + c.body());
    for (const el of dom.allElements(root)) {
      if (el.attrs['aria-hidden'] === 'true') continue;
      for (const child of el.children || []) {
        if (child.type !== 'text') continue;
        const raw = child.text.replace(/\s+/g, ' ').trim();
        if (!raw || !/\p{Extended_Pictographic}/u.test(raw)) continue;
        if (!/[A-Za-z؀-ۿ]/.test(raw)) continue;
        shared.push(`[${name}] ${JSON.stringify(raw)} in ${dom.describe(el)}`);
      }
    }
  }
  assert.deepStrictEqual(shared, [],
    'These text nodes mix an emoji with translatable words:\n  ' + shared.join('\n  ') +
    '\n\nPut the emoji in its own aria-hidden element.');

  console.log(`PASS: all ${keys.size} strings across ${states.length} rendered states are in both ` +
    'catalogs with real Arabic, and nothing else reaches the operator');
}

async function testArabicActuallyRendersArabic(src) {
  // The catalogs being in parity is not the same as the screen using them:
  // a key assembled at runtime, or an emoji-shared node, passes the parity
  // check and renders English anyway. So: render the whole screen under a t()
  // that behaves exactly like i18n.js in Arabic mode, and require that the
  // operator-visible copy really is Arabic.
  const ctx = await renderScreen({
    source: src, report: REPORT_DRIFT,
    translate: (s) => (s in AR ? AR[s] : s),
  });
  const panel = panelOf(ctx);
  const root = dom.parseFragment(ctx.frame + ctx.body());
  const english = [];
  for (const el of dom.allElements(root)) {
    if (el.tag === 'style' || el.tag === 'script') continue;
    if (el.attrs['aria-hidden'] === 'true') continue;
    for (const child of el.children || []) {
      if (child.type !== 'text') continue;
      const raw = child.text.replace(/\s+/g, ' ').trim();
      if (!raw || !/[A-Za-z]{3}/.test(raw)) continue;
      if (RENDERED_DATA_VALUES.has(raw)) continue;   // catalogue data, never translated
      english.push(`${JSON.stringify(raw)} in ${dom.describe(el)}`);
    }
  }
  assert.deepStrictEqual(english, [],
    'On an Arabic install these strings still render in English:\n  ' + english.join('\n  '));

  // ANTI-VACUITY: prove the Arabic pass rendered something at all, and that
  // the signed figures survived it intact — a swapped sign is the specific
  // bidi failure this screen is exposed to.
  const arabic = visibleText(panel.el);
  assert.ok(/[؀-ۿ]/.test(arabic),
    'The Arabic render produced no Arabic characters, so the sweep above compared nothing.');
  assert.ok(/\+7\b/.test(arabic) && arabic.indexOf('−2.5') !== -1,
    'The signed drift figures did not survive the Arabic render intact.');

  console.log('PASS: the whole screen renders in Arabic, with the signed figures intact');
}

async function testProductNamesFromTheDatabaseAreEscaped(src) {
  const hostile = { status: 'success', data: JSON.parse(JSON.stringify(DRIFT)) };
  hostile.data.rows[0].product_name = '<img src=x onerror="alert(1)">';
  hostile.data.rows[0].branch_name = '"><script>alert(2)</script>';
  hostile.data.rows[0].sku = "O'Brien<hr>";
  const ctx = await renderScreen({ source: src, report: hostile });
  const html = ctx.body();
  // ANTI-VACUITY BEFORE THE CLAIM: the screen must actually be showing the
  // drift table. Every "the payload is not present raw" assertion below is
  // trivially satisfied by a screen that rendered its error state instead.
  assert.strictEqual(panelOf(ctx).state, 'drift',
    'The hostile fixture did not reach the drift table, so "it was escaped" is a claim about ' +
    'markup that was never rendered.');
  assert.ok(html.indexOf('<img src=x') === -1 && html.indexOf('<script>') === -1 &&
    html.indexOf('<hr>') === -1,
    'A product/branch/SKU value from the database was interpolated into innerHTML unescaped. ' +
    'These arrive through the bulk importer and, once sync lands, from another device.\n' +
    html.slice(0, 500));
  assert.ok(html.indexOf('&lt;img') !== -1, 'The hostile value did not render at all, so the ' +
    'escaping check compared nothing.');
  console.log('PASS: catalogue values are escaped before reaching innerHTML');
}

// ─────────────────────────────────────────────────────────────────────────────
// THE MUTATION PROOFS
// ─────────────────────────────────────────────────────────────────────────────

async function testEveryGuardIsProvenByBreakingIt() {
  const proved = [];

  proved.push(await provesMutation(
    'the repair refuses to run outside the confirm step',
    [["if (!s || s.state !== 'confirm') return;\n    // The token the server will demand back",
      "if (!s) return;\n    // The token the server will demand back"]],
    testRepairNeverRunsByItself));

  proved.push(await provesMutation(
    '"nothing was compared" is distinguishable from "everything agrees"',
    [["s.state = drifted > 0 ? 'drift' : (examined > 0 ? 'clean' : 'nothing');",
      "s.state = drifted > 0 ? 'drift' : 'clean';"]],
    testTheThreeEmptyStatesAreDistinguishable));

  proved.push(await provesMutation(
    'a failed check does not render as a clean bill of health',
    [["        s.state = 'failed';\n        s.data = null;\n        s.error = (res && (res.message || res.error))",
      "        s.state = 'clean';\n        s.data = null;\n        s.error = (res && (res.message || res.error))"]],
    testTheThreeEmptyStatesAreDistinguishable));

  proved.push(await provesMutation(
    'the capability gate returns before the request',
    [["if (window.SubsystemApp && !SubsystemApp.hasCapability('retail.reports')) {\n      return this._renderCapabilityRestricted(c, {\n        icon: '⚖️',",
      "if (false) {\n      return this._renderCapabilityRestricted(c, {\n        icon: '⚖️',"]],
    testTheGatesReturnBeforeTheRequest));

  proved.push(await provesMutation(
    'the company-admin gate returns before the request',
    // Anchored past the `if` line itself and into the icon literal right
    // below it: ci-hardening-w0.3's Email Notifications screen
    // (_renderEmailNotifications) added a SECOND, textually identical
    // `SubsystemApp.role !== 'admin'` guard elsewhere in this file (the
    // same USER-axis check, reused deliberately -- see that screen's own
    // comment on why `ownerOnly`/this check, not `adminOnly`), so the bare
    // `if (...)` line alone no longer occurs exactly once. '⚖️' is Stock
    // Accuracy's own icon and disambiguates this anchor to this screen only.
    [["if (window.SubsystemApp && SubsystemApp.role && SubsystemApp.role !== 'admin') {\n" +
      "      return this._renderCapabilityRestricted(c, {\n" +
      "        icon: '⚖️',",
      "if (false) {\n" +
      "      return this._renderCapabilityRestricted(c, {\n" +
      "        icon: '⚖️',"]],
    testTheGatesReturnBeforeTheRequest));

  proved.push(await provesMutation(
    'every figure is bidi-isolated with an explicit direction',
    [['return `<span class="num">${this._bdi(text)}</span>`;',
      'return `<span class="num">${this._esc(text)}</span>`;']],
    testEveryFigureIsIsolatedAndTabular));

  proved.push(await provesMutation(
    'a positive drift is drawn with an explicit + sign',
    [["return (v > 0 ? '+' : '−') + this._qty(Math.abs(v));",
      "return (v > 0 ? '' : '−') + this._qty(Math.abs(v));"]],
    testTheDriftPanelNamesWhatIsWrong));

  proved.push(await provesMutation(
    'the drift COUNT is in the headline beside the net',
    [['<div class="ret-kpi-label">${t(\'Figures that disagree\')}</div>\n            <div class="ret-kpi-value">${this._stkaNum(this._qty(d.drift_count))}</div>',
      '<div class="ret-kpi-label">${t(\'Figures that disagree\')}</div>\n            <div class="ret-kpi-value">${this._stkaNum(this._qty(d.net_drift))}</div>']],
    testTheDriftPanelNamesWhatIsWrong));

  proved.push(await provesMutation(
    'the repair sends the token the SERVER supplied',
    [['const token = s.data && s.data.repair_confirmation;',
      "const token = 'RECONCILE-assembled-by-the-client';"]],
    testTheRepairSaysWhatItWillDoAndReportsWhatItDid));

  proved.push(await provesMutation(
    'a refused repair is not reported as a completed one',
    [["        s.state = 'repair-failed';\n        s.error = (res && (res.message || res.error)) || t('The repair did not run, so nothing was changed.');",
      "        s.state = 'repaired';\n        s.error = (res && (res.message || res.error)) || t('The repair did not run, so nothing was changed.');"]],
    testARefusedRepairIsNotAReportOfSuccess));

  proved.push(await provesMutation(
    'the skipped count is reported after a repair, not just the repaired one',
    [["${this._stkaTally(t('Rows the repair left alone'), total)}",
      "${this._stkaTally(t('Rows the repair left alone'), 0)}"]],
    testTheRepairSaysWhatItWillDoAndReportsWhatItDid));

  proved.push(await provesMutation(
    'each refusal is reported under the reason the SERVER gave for it',
    [['.map(([reason, n]) => this._stkaTally(this._stkaSkipReasonLabel(reason), n))',
      ".map(([reason, n]) => this._stkaTally(this._stkaSkipReasonLabel('no_branch'), n))"]],
    testTheRepairSaysWhatItWillDoAndReportsWhatItDid));

  proved.push(await provesMutation(
    'the confirm step does not promise a repair count it cannot know',
    [["<p style=\"color:var(--text-muted);font-size:13px;margin:0 0 10px;line-height:1.7;max-width:760px\">${t('The repair also refuses any balance the ledger has no history for at all, because the ledger saying nothing is not the ledger saying zero. Whatever it refuses is listed when it finishes.')}</p>\n        ",
      '']],
    testTheRepairSaysWhatItWillDoAndReportsWhatItDid));

  proved.push(await provesMutation(
    'catalogue values are escaped before reaching innerHTML',
    [['      <td>${name}</td>', '      <td>${row.product_name}</td>']],
    testProductNamesFromTheDatabaseAreEscaped));

  console.log(`PASS: ${proved.length} guards proved by breaking the behaviour they watch:`);
  for (const line of proved) console.log('      ' + line);
}

// ─────────────────────────────────────────────────────────────────────────────
// RUNNER — every check runs, every failure is collected
// ─────────────────────────────────────────────────────────────────────────────

/* A conditionally-built check list can be built EMPTY, at which point the
   runner loops over nothing and exits 0 having verified nothing. */
const EXPECTED_CHECKS = 11;

async function main() {
  const checks = [
    ['the screen is reachable and gated on both axes', testTheScreenIsReachable],
    ['the three empty states are distinguishable', () => testTheThreeEmptyStatesAreDistinguishable()],
    ['the drift panel names what is wrong', () => testTheDriftPanelNamesWhatIsWrong()],
    ['the repair never runs by itself', () => testRepairNeverRunsByItself()],
    ['the repair says what it will do and reports what it did',
      () => testTheRepairSaysWhatItWillDoAndReportsWhatItDid()],
    ['a refused repair is not a report of success', () => testARefusedRepairIsNotAReportOfSuccess()],
    ['the gates return before the request', () => testTheGatesReturnBeforeTheRequest()],
    ['every figure is isolated and tabular', () => testEveryFigureIsIsolatedAndTabular()],
    ['no colour is written by hand', testNoColourIsWrittenByHand],
    ['every string on every state is translated', () => testEveryStringOnEveryStateIsTranslated()],
    ['Arabic actually renders Arabic', () => testArabicActuallyRendersArabic()],
    ['catalogue values are escaped', () => testProductNamesFromTheDatabaseAreEscaped()],
    ['every guard is proven by breaking it', testEveryGuardIsProvenByBreakingIt],
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

  if (checks.length < EXPECTED_CHECKS) {
    console.error(`FAIL: retail_stock_accuracy_screen_test.js ran only ${checks.length} of ` +
      `${EXPECTED_CHECKS} known checks.`);
    process.exitCode = 1;
    return;
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_stock_accuracy_screen_test.js — ${failures.length} of ` +
      `${checks.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_stock_accuracy_screen_test.js — ${checks.length} checks`);
}

if (require.main === module) {
  main().catch((err) => {
    console.error('FAIL: retail_stock_accuracy_screen_test.js (runner)');
    console.error(err && err.stack || err);
    process.exitCode = 1;
  });
}
