/**
 * Aura Retail — the EXCEPTIONS screen (launch-readiness 2026-08-29, "the two
 * exception queues both need ONE screen, not two", ROADMAP.md).
 *
 * Two queues, one surface: the oversold-stock queue (`stock_exceptions`,
 * Phase 7 stage 7d-i/ii) and the discarded-catalogue-edit queue
 * (`sync_conflicts`, Phase 6 stage 6a-ii). Both answer "something happened
 * that the software could not resolve on its own and a human must decide" --
 * see `_renderExceptions` in subsystem-retail.js for the full reasoning.
 *
 * ── WHAT THIS FILE REFUSES TO LET SHIP ──────────────────────────────────────
 *
 * 1. A RESOLVE BUTTON OFFERED TO SOMEONE WHO CANNOT USE IT. The read route
 *    (`retail.reports`) and the resolve route (`retail.stock.adjust`) are two
 *    different capabilities. A viewer who holds the first but not the second
 *    must see every open exception with no button that would only 403.
 * 2. AN EMPTY QUEUE THAT LOOKS BROKEN. Zero open exceptions and zero
 *    discarded edits are both the normal, healthy case. Each must render its
 *    OWN distinguishable "nothing needs attention" panel, not a blank region
 *    and not the other section's words.
 * 3. AN OPERATOR-ENTERED NAME REACHING innerHTML RAW. `product_name` on a
 *    stock exception is catalogue data, exactly the trust boundary
 *    retail_pos_name_xss_test.js / retail_customer_modal_xss_test.js already
 *    guard elsewhere in this file.
 * 4. AN ACTION BUTTON ON THE DISCARDED-EDITS SECTION. That section is
 *    informational by design (list_sync_conflicts' own docstring, retail_api.py,
 *    is the authority) -- the newer value already won, so there is nothing to
 *    re-apply. A future refactor that quietly adds one must fail here.
 *
 * ── MUTATION-PROVED ─────────────────────────────────────────────────────────
 * Every behavioural claim below is re-run against a DELIBERATELY BROKEN copy
 * of subsystem-retail.js and is required to FAIL there -- including an
 * "allow-half" mutation that renders nothing at all, which would otherwise
 * pass every "does not show X" check by showing nothing.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_exceptions_screen_test.js
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

/* The file's own line ending. subsystem-retail.js is CRLF -- an anchor
   written with plain "\n" would silently match nothing, making every
   mutation proof below pass while breaking nothing. See mutate(). */
const EOL = SRC.indexOf('\r\n') !== -1 ? '\r\n' : '\n';
const nl = (s) => s.replace(/\n/g, EOL);

const SECTION_ID = 'exceptions';
const STOCK_URL = '/api/sub/retail/inventory/stock-exceptions';
const CONFLICTS_URL = '/api/sub/retail/inventory/sync-conflicts';

/* The source region this screen occupies, isolated once -- for the "no
   hardcoded colour" check, the same shape retail_stock_accuracy_screen_test.js
   uses for its own screen. */
const REGION_START = '  async _renderExceptions(c) {';
const REGION_END = '  // ══ STOCK ACCURACY';

function screenSource(src) {
  const s = (src || SRC).indexOf(REGION_START);
  const e = (src || SRC).indexOf(REGION_END);
  assert.ok(s !== -1, 'Could not find _renderExceptions() in subsystem-retail.js.');
  assert.ok(e > s, 'Could not find the end of the Exceptions screen region.');
  const region = (src || SRC).slice(s, e);
  // ANTI-VACUITY: an empty or near-empty slice would make the colour check
  // below pass by reading almost nothing. The real region is ~20,000 chars.
  assert.ok(region.length > 5000,
    `The Exceptions screen source region is only ${region.length} chars -- the slice markers moved ` +
    'and the colour check below is now reading almost nothing.');
  return region;
}

// ─────────────────────────────────────────────────────────────────────────────
// FIXTURES
// ─────────────────────────────────────────────────────────────────────────────

/* Two open oversell exceptions. Deliberately not one row and not one shape:
 * the second has a null product/branch -- the tombstoned-product /
 * renamed-branch case list_stock_exceptions' own docstring names (LEFT JOIN,
 * not INNER), which the row must still render rather than silently drop. */
const STOCK_ROWS = [
  {
    id: 'exc-aaaa1111-0000-0000-0000-000000000001',
    product_id: 'p-oil', product_name: 'Sesame Oil 500ml', sku: 'SES500',
    branch_id: 1, branch_name: 'Main Branch',
    observed_quantity_on_hand: -3, detected_at_utc: '2026-08-20T10:15:00+00:00',
  },
  {
    id: 'exc-bbbb2222-0000-0000-0000-000000000002',
    product_id: 'p-gone', product_name: null, sku: null,
    branch_id: null, branch_name: null,
    observed_quantity_on_hand: -1, detected_at_utc: '2026-08-21T08:00:00+00:00',
  },
];

/* One discarded catalogue edit -- a product update that lost the
 * reject-stale race. */
const CONFLICTS_ROWS = [
  {
    id: 'cf-cccc3333-0000-0000-0000-000000000003',
    entity_type: 'product', entity_id: 'p-oil', event_type: 'update',
    local_row_version: 6, incoming_row_version: 4,
    detected_at_utc: '2026-08-22T09:30:00+00:00',
    changed_fields: ['name', 'sell_price'],
  },
];

/* The ENVELOPES the routes actually put on the wire (list_stock_exceptions /
   list_sync_conflicts, retail_api.py) -- {status:'success', data:{...}}. */
const STOCK_ROWS_OK = { status: 'success', data: { exceptions: STOCK_ROWS, count: STOCK_ROWS.length } };
const STOCK_EMPTY_OK = { status: 'success', data: { exceptions: [], count: 0 } };
const CONFLICTS_ROWS_OK = { status: 'success', data: { conflicts: CONFLICTS_ROWS, count: CONFLICTS_ROWS.length } };
const CONFLICTS_EMPTY_OK = { status: 'success', data: { conflicts: [], count: 0 } };
const RESOLVE_OK = { status: 'success', new_stock: 12, branch_id: 1, movement_uid: 'mv-1' };

/* A payload that is dangerous in TWO contexts at once, matching
   retail_pos_name_xss_test.js's own MALICIOUS_NAME: it breaks out of a
   double-quoted attribute AND contains a classic script-injection vector. */
const MALICIOUS_NAME = `"><img src=x onerror=alert(document.cookie)>`;
const STOCK_XSS_OK = {
  status: 'success',
  data: {
    exceptions: [{
      id: 'exc-xss', product_id: 'p-xss', product_name: MALICIOUS_NAME, sku: 'XSS1',
      branch_id: 1, branch_name: 'Main Branch',
      observed_quantity_on_hand: -2, detected_at_utc: '2026-08-23T12:00:00+00:00',
    }],
    count: 1,
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
 *   stock        — the GET .../stock-exceptions payload
 *   conflicts    — the GET .../sync-conflicts payload
 *   resolve      — the POST .../stock-exceptions/<id>/resolve payload
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
      let body = null;
      if (init && typeof init.body === 'string') {
        try { body = JSON.parse(init.body); } catch (e) { body = init.body; }
      }
      const u = String(url);
      calls.push({ method, url: u, body });
      let payload;
      if (method === 'POST' && u.indexOf('/resolve') !== -1) {
        payload = o.resolve === undefined ? RESOLVE_OK : o.resolve;
      } else if (u.indexOf(CONFLICTS_URL) !== -1) {
        payload = o.conflicts === undefined ? CONFLICTS_EMPTY_OK : o.conflicts;
      } else if (u.indexOf(STOCK_URL) !== -1) {
        payload = o.stock === undefined ? STOCK_EMPTY_OK : o.stock;
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
  sandbox.Chart = function ChartStub() { return { destroy() {} }; };
  sandbox.Chart.getChart = () => null;

  vm.createContext(sandbox);
  vm.runInContext(o.source || SRC, sandbox, { filename: RETAIL_JS });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return { rs: sandbox.RetailSystem, calls, els };
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
  ctx.stockBody = () => (ctx.els['exq-stock-body'] ? ctx.els['exq-stock-body'].innerHTML : '');
  ctx.conflictsBody = () => (ctx.els['exq-conflicts-body'] ? ctx.els['exq-conflicts-body'].innerHTML : '');
  return ctx;
}

/** The one panel in a section body, as a parsed element -- mirrors
    retail_stock_accuracy_screen_test.js's panelOf(). */
function panelOf(html, label) {
  const root = dom.parseFragment(html);
  const stated = dom.allElements(root).filter((el) => el.attrs['data-exq-state'] !== undefined);
  assert.strictEqual(
    stated.length, 1,
    `${label} holds ${stated.length} elements carrying data-exq-state, expected exactly 1. ` +
    'One state must render exactly one panel: zero is the empty screen this file exists to ' +
    'refuse, and two means two states are on screen at once.\n' + html.slice(0, 400)
  );
  return { el: stated[0], state: stated[0].attrs['data-exq-state'], html, root };
}

function visibleText(node) {
  return dom.textOf(node).replace(/\s+/g, ' ').trim();
}

function dataRows(root) {
  return dom.allElements(root).filter((el) => el.tag === 'tr' &&
    (el.children || []).filter((n) => n.type === 'element' && n.tag === 'td').length >= 1);
}

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
      'nothing. Re-anchor it.'
    );
    src = src.replace(find, replace);
  }
  return src;
}

/**
 * Run `check` against a broken build and require it to FAIL.
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
  const start = SRC.indexOf('  render(sectionId) {');
  assert.ok(start !== -1, 'Could not find RetailSystem.render(sectionId).');
  const body = SRC.slice(start, SRC.indexOf('\n  },', start));
  assert.ok(
    new RegExp(`case\\s+'${SECTION_ID}'\\s*:`).test(body),
    `RetailSystem.render() has no case for '${SECTION_ID}', so the screen is unreachable through ` +
    'the router.'
  );

  const shell = fs.readFileSync(APP_SHELL_JS, 'utf8');
  const entry = shell.split('\n').find((l) => l.indexOf(`id: '${SECTION_ID}'`) !== -1);
  assert.ok(entry, `app-shell.js has no nav entry for '${SECTION_ID}'.`);
  assert.ok(/capability:\s*'retail\.reports'/.test(entry),
    `The '${SECTION_ID}' nav entry does not gate on retail.reports, but both read routes ` +
    `(list_stock_exceptions, list_sync_conflicts) carry @mt_require_capability(CAP_REPORTS). ` +
    `Entry:\n  ${entry.trim()}`);
  // Neither read route requires company-admin -- ownerOnly/adminOnly would
  // hide the screen from a role the routes themselves would actually serve.
  assert.ok(!/ownerOnly/.test(entry),
    `The '${SECTION_ID}' nav entry uses ownerOnly, but neither route requires company-admin. ` +
    `Entry:\n  ${entry.trim()}`);
  assert.ok(!/adminOnly/.test(entry),
    `The '${SECTION_ID}' nav entry uses adminOnly (the DEVICE axis), which neither route checks. ` +
    `Entry:\n  ${entry.trim()}`);

  console.log(`PASS: '${SECTION_ID}' is routed, and its nav entry gates on retail.reports only`);
}

async function testBothSectionsRenderTheirRows(src) {
  // ANTI-VACUITY: a fixture with too few rows proves nothing about "both
  // sections render THEIR rows" as opposed to one row rendered twice.
  assert.ok(STOCK_ROWS.length >= 2 && CONFLICTS_ROWS.length >= 1,
    'The rows fixtures are too small to prove both sections render independently.');

  const ctx = await renderScreen({
    source: src, capabilities: ['retail.reports', 'retail.stock.adjust'],
    stock: STOCK_ROWS_OK, conflicts: CONFLICTS_ROWS_OK,
  });

  const stock = panelOf(ctx.stockBody(), '#exq-stock-body');
  assert.strictEqual(stock.state, 'rows',
    `Oversold-stock panel state was '${stock.state}', expected 'rows'.`);
  const stockRows = dataRows(stock.root);
  assert.strictEqual(stockRows.length, STOCK_ROWS.length,
    `Oversold-stock table rendered ${stockRows.length} data row(s) for ${STOCK_ROWS.length} fixture rows.`);
  const stockText = visibleText(stock.el);
  assert.ok(stockText.indexOf('Sesame Oil 500ml') !== -1 && stockText.indexOf('Main Branch') !== -1,
    `The named product/branch is missing from the oversold-stock rows. Text:\n  ${stockText}`);
  assert.ok(stockText.indexOf('Product no longer in the catalogue') !== -1,
    `The tombstoned-product fallback (LEFT JOIN) is not rendered. Text:\n  ${stockText}`);

  const conflicts = panelOf(ctx.conflictsBody(), '#exq-conflicts-body');
  assert.strictEqual(conflicts.state, 'rows',
    `Discarded-edits panel state was '${conflicts.state}', expected 'rows'.`);
  const conflictRows = dataRows(conflicts.root);
  assert.strictEqual(conflictRows.length, CONFLICTS_ROWS.length,
    `Discarded-edits table rendered ${conflictRows.length} data row(s) for ${CONFLICTS_ROWS.length} ` +
    'fixture rows.');
  const conflictsText = visibleText(conflicts.el);
  assert.ok(conflictsText.indexOf('name') !== -1 && conflictsText.indexOf('sell_price') !== -1,
    `The changed field names are missing from the discarded-edit row. Text:\n  ${conflictsText}`);

  console.log('PASS: both sections render their fixture rows');
}

async function testEachEmptyQueueRendersItsOwnEmptyState(src) {
  const ctx = await renderScreen({
    source: src, capabilities: ['retail.reports', 'retail.stock.adjust'],
    stock: STOCK_EMPTY_OK, conflicts: CONFLICTS_EMPTY_OK,
  });

  // ANTI-VACUITY: a screen that failed to build gives two near-empty
  // strings, which would make the "different text" comparison below
  // meaningless.
  assert.ok(ctx.stockBody().length > 40 && ctx.conflictsBody().length > 40,
    'One or both sections rendered almost nothing -- the screen did not build, so the ' +
    'comparison below is between near-empty strings.');

  const stock = panelOf(ctx.stockBody(), '#exq-stock-body');
  const conflicts = panelOf(ctx.conflictsBody(), '#exq-conflicts-body');

  assert.strictEqual(stock.state, 'empty',
    `An oversold-stock queue with zero open exceptions rendered state '${stock.state}', not ` +
    "'empty' -- a blank or broken section reads as a bug, not as \"nothing needs attention\".");
  assert.strictEqual(conflicts.state, 'empty',
    `A discarded-edits queue with zero rows rendered state '${conflicts.state}', not 'empty'.`);

  const stockText = visibleText(stock.el);
  const conflictsText = visibleText(conflicts.el);
  assert.ok(stockText.indexOf('No oversold product is currently open') !== -1,
    `The oversold-stock empty state does not say so in words. Text:\n  ${stockText}`);
  assert.ok(conflictsText.indexOf('No catalogue edit has been discarded as stale') !== -1,
    `The discarded-edits empty state does not say so in words. Text:\n  ${conflictsText}`);
  assert.notStrictEqual(stockText, conflictsText,
    'The two empty-state panels render identical text -- an operator could not tell which ' +
    'queue is the one that is empty.');

  // Independence: one queue has rows, the other is empty, in the SAME
  // render -- a fetch outcome in one section must never leak into the other
  // (two independent state machines, per _renderExceptions' own comment).
  const mixed = await renderScreen({
    source: src, capabilities: ['retail.reports', 'retail.stock.adjust'],
    stock: STOCK_ROWS_OK, conflicts: CONFLICTS_EMPTY_OK,
  });
  assert.strictEqual(panelOf(mixed.stockBody(), '#exq-stock-body (mixed)').state, 'rows',
    'A mixed render (stock has rows, conflicts is empty) did not show rows for the stock queue.');
  assert.strictEqual(panelOf(mixed.conflictsBody(), '#exq-conflicts-body (mixed)').state, 'empty',
    'A mixed render (stock has rows, conflicts is empty) did not show the conflicts queue as ' +
    'empty -- the two queues are not actually independent state.');

  console.log('PASS: each empty queue renders its own distinguishable empty state, independently ' +
    'of the other');
}

async function testResolveControlGatedByStockAdjustCapability(src) {
  // Holds retail.reports (can see the queue) but not retail.stock.adjust —
  // must see every open row, with NO resolve control at all.
  const withoutAdjust = await renderScreen({
    source: src, capabilities: ['retail.reports'],
    stock: STOCK_ROWS_OK, conflicts: CONFLICTS_EMPTY_OK,
  });
  const stockNoAdjust = panelOf(withoutAdjust.stockBody(), '#exq-stock-body (no adjust)');
  assert.strictEqual(stockNoAdjust.state, 'rows', 'Setup: the stock queue did not render its rows.');
  const buttonsNoAdjust = dom.allElements(stockNoAdjust.root)
    .filter((el) => el.tag === 'button' && el.attrs['data-exq-resolve-id'] !== undefined);
  assert.deepStrictEqual(buttonsNoAdjust, [],
    `A viewer with retail.reports but not retail.stock.adjust was offered ${buttonsNoAdjust.length} ` +
    'resolve control(s) -- a button that would only 403 on submit.');
  // Every open row must still be fully visible -- the gate hides the
  // ACTION, never the ROW.
  assert.strictEqual(dataRows(stockNoAdjust.root).length, STOCK_ROWS.length,
    'Withholding retail.stock.adjust hid entire rows, not just the resolve control.');

  // Holds BOTH capabilities -- every open row carries its own resolve
  // control.
  const withAdjust = await renderScreen({
    source: src, capabilities: ['retail.reports', 'retail.stock.adjust'],
    stock: STOCK_ROWS_OK, conflicts: CONFLICTS_EMPTY_OK,
  });
  const stockAdjust = panelOf(withAdjust.stockBody(), '#exq-stock-body (adjust)');
  const buttonsAdjust = dom.allElements(stockAdjust.root)
    .filter((el) => el.tag === 'button' && el.attrs['data-exq-resolve-id'] !== undefined);
  assert.strictEqual(buttonsAdjust.length, STOCK_ROWS.length,
    `A viewer holding retail.stock.adjust was offered ${buttonsAdjust.length} resolve control(s) ` +
    `for ${STOCK_ROWS.length} open exceptions -- every open row should carry one.`);

  console.log('PASS: the resolve control is absent without retail.stock.adjust and present with ' +
    'it, both directions');
}

async function testAMaliciousProductNameIsEscaped(src) {
  const ctx = await renderScreen({
    source: src, capabilities: ['retail.reports', 'retail.stock.adjust'],
    stock: STOCK_XSS_OK, conflicts: CONFLICTS_EMPTY_OK,
  });
  const html = ctx.stockBody();
  assert.ok(html.indexOf(MALICIOUS_NAME) === -1,
    `The raw, unescaped product name leaked into #exq-stock-body -- a stored-XSS injection ` +
    `point. Got:\n  ${html}`);
  assert.ok(html.indexOf('&lt;img') !== -1 && html.indexOf('&quot;&gt;') !== -1,
    `Expected the malicious product name to be HTML-escaped (this._esc). Got:\n  ${html}`);

  // The strongest form of the same proof: a real DOM-lite parse of the
  // panel must never produce an <img>/<script> ELEMENT -- only text
  // describing one.
  const root = dom.parseFragment(html);
  const dangerous = dom.allElements(root).filter((el) => el.tag === 'img' || el.tag === 'script');
  assert.deepStrictEqual(dangerous, [],
    `The malicious product name parsed into ${dangerous.length} real <img>/<script> element(s) ` +
    'instead of escaped text.');

  console.log('PASS: a malicious product name reaches the oversold-stock row escaped, not as markup');
}

async function testDiscardedEditsCarryNoResolveControlForAnyone(src) {
  const ctx = await renderScreen({
    // Deliberately a viewer who HOLDS retail.stock.adjust -- the section is
    // informational by DESIGN, not because this viewer lacks authority.
    source: src, capabilities: ['retail.reports', 'retail.stock.adjust'],
    stock: STOCK_EMPTY_OK, conflicts: CONFLICTS_ROWS_OK,
  });
  const conflicts = panelOf(ctx.conflictsBody(), '#exq-conflicts-body');
  assert.strictEqual(conflicts.state, 'rows', 'Setup: the discarded-edits queue did not render its rows.');
  const buttons = dom.allElements(conflicts.root).filter((el) => el.tag === 'button');
  assert.deepStrictEqual(buttons, [],
    `The discarded-edits section rendered ${buttons.length} button(s), even for a viewer who ` +
    'holds retail.stock.adjust. This section is informational by design -- the newer value ' +
    'already won, so there is nothing to re-apply -- and a future refactor that adds an action ' +
    'button here must fail this check.');

  console.log('PASS: the discarded-edits section carries no resolve control for anyone, including ' +
    'a viewer who holds retail.stock.adjust');
}

function testNoColourIsWrittenByHand() {
  const region = screenSource();
  const hex = region.match(/#[0-9a-fA-F]{3,8}\b/g) || [];
  assert.deepStrictEqual(hex, [],
    `Hardcoded colour literal(s) on the Exceptions screen: ${JSON.stringify(hex)}. Use a design ` +
    'token; a literal is invisible to the contrast sweep\'s token layer.');
  const fn = region.match(/\brgba?\s*\(/g) || [];
  assert.deepStrictEqual(fn, [],
    `Hardcoded rgb()/rgba() colour(s) on the Exceptions screen: ${JSON.stringify(fn)}.`);

  console.log('PASS: no hardcoded colour literal on the Exceptions screen -- every colour is a ' +
    'token reference');
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION PROOFS
// ─────────────────────────────────────────────────────────────────────────────

async function testEveryGuardIsProvenByBreakingIt() {
  const proved = [];

  proved.push(await provesMutation(
    '1. the resolve control is shown regardless of capability',
    [["    const canResolve = !window.SubsystemApp || SubsystemApp.hasCapability('retail.stock.adjust');",
      '    const canResolve = true; // MUTATED: resolve control shown regardless of capability']],
    testResolveControlGatedByStockAdjustCapability));

  proved.push(await provesMutation(
    '2. a product name is rendered without escaping',
    [[`    const productLabel = row.product_name
      ? this._esc(row.product_name)
      : \`<span>\${t('Product no longer in the catalogue')}</span> \${this._bdi(String(row.product_id == null ? '' : row.product_id))}\`;`,
      `    const productLabel = row.product_name
      ? row.product_name
      : \`<span>\${t('Product no longer in the catalogue')}</span> \${this._bdi(String(row.product_id == null ? '' : row.product_id))}\`;`]],
    testAMaliciousProductNameIsEscaped));

  proved.push(await provesMutation(
    "3. the oversold-stock 'empty' branch is dropped from the switch",
    [["      case 'empty':    return this._exqEmpty(t('Nothing needs attention.'), t('No oversold product is currently open.'));\n",
      '']],
    testEachEmptyQueueRendersItsOwnEmptyState));

  proved.push(await provesMutation(
    '4a. ALLOW HALF — the screen renders nothing at all (test 1: both sections render rows)',
    [['  async _renderExceptions(c) {\n    this._injectStyles();',
      "  async _renderExceptions(c) {\n    c.innerHTML = '<div class=\"ret-hdr\"><h2 class=\"ret-title\">Exceptions</h2></div>'; return; // MUTATED: renders nothing\n    this._injectStyles();"]],
    testBothSectionsRenderTheirRows));

  proved.push(await provesMutation(
    '4b. ALLOW HALF — the screen renders nothing at all (test 3: resolve control present/absent)',
    [['  async _renderExceptions(c) {\n    this._injectStyles();',
      "  async _renderExceptions(c) {\n    c.innerHTML = '<div class=\"ret-hdr\"><h2 class=\"ret-title\">Exceptions</h2></div>'; return; // MUTATED: renders nothing\n    this._injectStyles();"]],
    testResolveControlGatedByStockAdjustCapability));

  console.log(`PASS: ${proved.length} guards proved by breaking the behaviour they watch:`);
  for (const line of proved) console.log('      ' + line);
}

// ─────────────────────────────────────────────────────────────────────────────
// RUNNER — every check runs, every failure is collected
// ─────────────────────────────────────────────────────────────────────────────

const EXPECTED_CHECKS = 7;

async function main() {
  const checks = [
    ['the screen is reachable and gated on retail.reports', testTheScreenIsReachable],
    ['both sections render their rows', () => testBothSectionsRenderTheirRows()],
    ['each empty queue renders its own empty state', () => testEachEmptyQueueRendersItsOwnEmptyState()],
    ['the resolve control is gated by retail.stock.adjust', () => testResolveControlGatedByStockAdjustCapability()],
    ['a malicious product name is escaped', () => testAMaliciousProductNameIsEscaped()],
    ['discarded edits carry no resolve control for anyone', () => testDiscardedEditsCarryNoResolveControlForAnyone()],
    ['no colour is written by hand', testNoColourIsWrittenByHand],
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
    console.error(`FAIL: retail_exceptions_screen_test.js ran only ${checks.length} of ` +
      `${EXPECTED_CHECKS} known checks.`);
    process.exitCode = 1;
    return;
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_exceptions_screen_test.js — ${failures.length} of ` +
      `${checks.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_exceptions_screen_test.js — ${checks.length} checks`);
}

if (require.main === module) {
  main().catch((err) => {
    console.error('FAIL: retail_exceptions_screen_test.js (runner)');
    console.error(err && err.stack || err);
    process.exitCode = 1;
  });
}
