/**
 * retail_render_race_test.js — the Dashboard render-generation race.
 *
 * THE BUG: RetailSystem._renderDashboard() (products/retail/frontend/
 * subsystem-retail.js, around line 1729 at the time this was measured) set
 * #sub-content's markup SYNCHRONOUSLY, then awaited GET /dashboard/stats
 * and wrote the response into elements by id -- with no check that the
 * render it belonged to was still the current one.
 *
 * Open Dashboard, navigate away (or back to Dashboard again) before that
 * fetch settles, and the late response either:
 *   - ran into a screen whose r-k-* ids no longer exist, measured in a real
 *     browser as `TypeError: Cannot set properties of null`
 *     (subsystem-retail.js:1729), reproduced with an identical stack on 2
 *     of 3 runs and not on the next 5 (timing-dependent, not absent -- see
 *     retail_smoke_e2e.py scenario 10, which still probes this and
 *     tolerates either outcome by design), or
 *   - found the SAME ids again (a second landing on Dashboard) and silently
 *     painted the PREVIOUS render's stale revenue/transaction figures over
 *     the screen the user is now looking at. No exception, no console line
 *     -- just wrong money on screen.
 *
 * THE FIX: a render-generation counter (RetailSystem._renderGeneration,
 * bumped by _beginRender()/_isStaleRender()). _renderDashboard captures a
 * token at the start and, right after its one await, compares it against
 * the live counter -- if another render has started since, this one
 * returns immediately and writes nothing.
 *
 * This file drives that race DETERMINISTICALLY: both fetches are promises
 * this file resolves by hand, in a chosen order, never via timers or real
 * timing. See _renderDashboard's own comment block (and the "Render
 * generation guard" section just above RetailSystem.render()) for the full
 * writeup.
 *
 * AUDIT NOTE (retail-hardware-viewports): every other async render/load
 * function in subsystem-retail.js was read against this same shape
 * ("sets markup, awaits, writes by id") as part of fixing this bug. Every
 * one of them already either (a) re-queries its target element fresh and
 * null-checks before writing, or (b) never writes to the DOM after its own
 * await at all -- so none of them reproduce the crash this file's Dashboard
 * fix addresses, and none needed the same generation-token treatment.
 * _renderDashboard was the one render in the whole file that wrote by id
 * with NO null check and NO staleness check at all. There is therefore no
 * second fixed render to cover here (the task's item 5) -- this file's
 * scope is the one real fix.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_render_race_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const RETAIL_JS = path.join(FRONTEND, 'subsystem-retail.js');
const SRC = fs.readFileSync(RETAIL_JS, 'utf8');

/* Match the file's OWN line ending -- an anchor written with the wrong one
   would silently match nothing, making a mutation proof below pass while
   proving nothing (see retail_branches_test.js's identical comment). */
function eolOf(src) { return src.indexOf('\r\n') !== -1 ? '\r\n' : '\n'; }
function nlFor(src) { const eol = eolOf(src); return (s) => s.replace(/\n/g, eol); }

// ─────────────────────────────────────────────────────────────────────────────
// FIXTURES — two clearly-distinguishable /dashboard/stats payloads. STALE's
// values are poison: if they ever appear on screen, the race was lost.
// ─────────────────────────────────────────────────────────────────────────────

const STATS_CURRENT = {
  today_sales: 250, today_transactions: 12, month_sales: 900,
  low_stock_alerts: 0, total_customers: 5, total_products: 40,
  today_returns: 0, sales_change_pct: 5, month_transactions: 30,
  hourly_labels: [], hourly_data: [], payment_methods: {}, recent_sales: [],
};

const STATS_STALE = {
  today_sales: 999999, today_transactions: 777, month_sales: 555555,
  low_stock_alerts: 9, total_customers: 111, total_products: 222,
  today_returns: 0, sales_change_pct: -50, month_transactions: 333,
  hourly_labels: [], hourly_data: [], payment_methods: {}, recent_sales: [],
};

const STATS_NORMAL = {
  today_sales: 123.45, today_transactions: 7, month_sales: 4000,
  low_stock_alerts: 2, total_customers: 9, total_products: 50,
  today_returns: 0, sales_change_pct: 3, month_transactions: 20,
  hourly_labels: [], hourly_data: [], payment_methods: {}, recent_sales: [],
};

// Currency defaults RetailSystem constructs with ('JD', 3 decimals -- see
// subsystem-retail.js's own _currencySymbol/_currencyDecimals comment) --
// spelled out literally here, not derived from the module under test, so
// the expected value does not share a bug with the code producing it.
const REV_CURRENT = 'JD 250.000';
const REV_STALE = 'JD 999999.000';
const REV_NORMAL = 'JD 123.450';

// ─────────────────────────────────────────────────────────────────────────────
// THE SANDBOX — the real subsystem-retail.js in a vm context, never a
// reimplementation. Modelled on retail_branches_test.js's loadRetailSystem()
// and retail_dashboard_transaction_contrast_test.js's Dashboard-specific
// document shape (the `#r-dash-recent tbody` querySelector routing in
// particular comes from that file).
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
 * Every fetch() call is deferred and returned UNRESOLVED until this file
 * resolves it by hand, in whatever order the test chooses -- that is what
 * makes the race deterministic instead of timing-dependent.
 */
function loadRetailSystem(source) {
  const els = Object.create(null);
  const getEl = (id) => (els[id] || (els[id] = makeStub({ id })));
  const fetchCalls = []; // { url, resolve(payload, status) }

  const sandbox = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    t: (s) => s, // identity stub -- this file checks WRITE-ORDERING behaviour, not catalogs
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
      // _renderDashboard reads its recent-transactions tbody via
      // querySelector (matching _loadProducts/_loadCategories' own
      // convention elsewhere in the file), so that selector specifically is
      // routed to a tracked stub; everything else gets a throwaway one.
      querySelector(sel) {
        if (sel === '#r-dash-recent tbody') return getEl('r-dash-recent-tbody');
        return makeStub({});
      },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
  };
  sandbox.window = sandbox; // enough for the `window.Chart` / `window.SubsystemApp` / `window.RetailSystem` refs used here
  // window.Chart is deliberately left undefined: _renderDashboard's chart
  // block is `if (window.Chart) {...}`, so leaving it unset skips chart
  // construction entirely and keeps this file focused on the KPI writes the
  // bug actually measured against.
  // window.SubsystemApp is deliberately left undefined too: _renderDashboard
  // only takes the cashier-landing branch when `window.SubsystemApp &&
  // !SubsystemApp.hasCapability(...)`, so omitting it exercises the full
  // Dashboard render, matching retail_dashboard_transaction_contrast_test.js.

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
// TEST 1 / 3 — THE RACE, driven deterministically. A superseded render must
// write NOTHING (the DOM keeps showing the newer render's content) and must
// resolve cleanly rather than throw.
// ─────────────────────────────────────────────────────────────────────────────

async function testSupersededRenderWritesNothingAndResolvesCleanly(src) {
  const ctx = loadRetailSystem(src);
  const host = ctx.getEl('sub-content');

  // Render A starts first (e.g. the user opened Dashboard) and is left
  // hanging on its own fetch -- this is the "in-flight when superseded" render.
  const pA = ctx.rs._renderDashboard(host);
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 1,
    `expected render A to have reached its own fetch by now. Calls: ${ctx.fetchCalls.length}`);

  // Render B starts second, while A is still pending -- the user navigated
  // away and back to Dashboard (or navigated again) before A's fetch settled.
  const pB = ctx.rs._renderDashboard(host);
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 2,
    `expected render B to have reached its own fetch by now. Calls: ${ctx.fetchCalls.length}`);

  // B resolves and paints the screen FIRST.
  ctx.fetchCalls[1].resolve({ data: STATS_CURRENT });
  await pB;
  await settle();
  assert.strictEqual(ctx.getEl('r-k-rev').textContent, REV_CURRENT,
    `render B's own figures did not paint before A resolved. Got: ${ctx.getEl('r-k-rev').textContent}`);

  // THEN A's fetch resolves -- late, after B has already painted the screen
  // the user is currently looking at. Assert this does not throw.
  ctx.fetchCalls[0].resolve({ data: STATS_STALE });
  let threw = null;
  try { await pA; } catch (e) { threw = e; }
  await settle();

  assert.strictEqual(threw, null,
    'a superseded render must resolve cleanly, not throw. Got: ' +
    (threw && (threw.stack || threw.message)));

  // THE ASSERTION THAT MATTERS: the DOM must STILL show render B's content --
  // A's late, stale figures must not have overwritten what the user is
  // looking at. This is the money consequence the bug report describes.
  assert.strictEqual(ctx.getEl('r-k-rev').textContent, REV_CURRENT,
    `render A (superseded) overwrote the screen with its own stale revenue figure. Got: ${ctx.getEl('r-k-rev').textContent}`);
  assert.strictEqual(ctx.getEl('r-k-txn').textContent, STATS_CURRENT.today_transactions,
    `render A (superseded) overwrote the transaction count. Got: ${ctx.getEl('r-k-txn').textContent}`);
  assert.notStrictEqual(ctx.getEl('r-k-rev').textContent, REV_STALE,
    'the stale revenue figure leaked onto the screen.');

  console.log('PASS: a superseded render writes nothing (DOM keeps showing the newer render) and resolves without throwing');
}

// ─────────────────────────────────────────────────────────────────────────────
// TEST 2 / 4 — THE NORMAL PATH still works, and anti-vacuity: a lone render
// whose fetch resolves with nothing else running DOES write its data. A
// guard that makes every render a no-op would pass test 1 above and
// destroy the product -- this is the test that catches that wrong fix.
// ─────────────────────────────────────────────────────────────────────────────

async function testNormalPathStillRendersData(src) {
  const ctx = loadRetailSystem(src);
  const host = ctx.getEl('sub-content');

  const p = ctx.rs._renderDashboard(host);
  await settle();
  assert.strictEqual(ctx.fetchCalls.length, 1, 'expected exactly one fetch for a lone render');

  ctx.fetchCalls[0].resolve({ data: STATS_NORMAL });
  await p;
  await settle();

  // ANTI-VACUITY: the element must carry the ACTUAL expected value, not just
  // "something" -- a stub that renders nothing could otherwise pass test 1
  // (nothing to overwrite) and this test both if this assertion were loose.
  assert.strictEqual(ctx.getEl('r-k-rev').textContent, REV_NORMAL,
    `a normal, uncontested render did not write its revenue figure. Got: ${JSON.stringify(ctx.getEl('r-k-rev').textContent)}`);
  assert.strictEqual(ctx.getEl('r-k-txn').textContent, STATS_NORMAL.today_transactions,
    `a normal, uncontested render did not write its transaction count. Got: ${JSON.stringify(ctx.getEl('r-k-txn').textContent)}`);
  assert.strictEqual(ctx.getEl('r-k-mtd').textContent, 'JD 4000.000',
    `a normal, uncontested render did not write its month-to-date figure. Got: ${JSON.stringify(ctx.getEl('r-k-mtd').textContent)}`);

  console.log('PASS: a normal, uncontested render still writes its data (anti-vacuity: exact expected values landed)');
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
  await run('testSupersededRenderWritesNothingAndResolvesCleanly',
    () => testSupersededRenderWritesNothingAndResolvesCleanly(SRC));
  await run('testNormalPathStillRendersData',
    () => testNormalPathStillRendersData(SRC));

  // ── MUTATION-PROVED, both directions ───────────────────────────────────────
  //
  // Direction 1: remove the generation check entirely => the race test must
  // go RED, and it must go red WITH THE REAL MESSAGE (the stale figure
  // actually overwriting the screen), not with an unrelated crash.
  await run('M1: generation check removed => testSupersededRenderWritesNothingAndResolvesCleanly FAILS', async () => {
    const broken = mutate(SRC, [[
      '      if (this._isStaleRender(renderToken)) return;\n',
      '      // MUTATED: generation check removed\n',
    ]]);
    return provesMutation(
      'M1 generation check removed',
      broken,
      (b) => testSupersededRenderWritesNothingAndResolvesCleanly(b)
    );
  });

  // Direction 2 (the one that matters most -- ENGINEERING.md #1 "prove both
  // directions of anything that both denies and allows"): make the check
  // ALWAYS treat the render as stale, i.e. always return early. This is the
  // dangerous wrong fix -- it would pass test 1 trivially (nothing ever
  // writes, so nothing is ever overwritten) while silently turning the
  // Dashboard into a permanent no-op. The normal-path test must catch it.
  await run('M2: guard always returns early (allow-half destroyed) => testNormalPathStillRendersData FAILS', async () => {
    const broken = mutate(SRC, [[
      '      if (this._isStaleRender(renderToken)) return;\n',
      '      if (true) return; // MUTATED: every render treated as superseded\n',
    ]]);
    return provesMutation(
      'M2 guard always returns early',
      broken,
      (b) => testNormalPathStillRendersData(b)
    );
  });

  console.log(results.join('\n'));
  if (failed) {
    console.error(`\nFAIL: retail_render_race_test.js — ${failed} of ${results.length} check(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`\nPASS: retail_render_race_test.js — ${results.length} check(s)`);
  }
}

main().catch((err) => {
  console.error('FAIL: retail_render_race_test.js (runner)');
  console.error((err && err.stack) || err);
  process.exitCode = 1;
});
