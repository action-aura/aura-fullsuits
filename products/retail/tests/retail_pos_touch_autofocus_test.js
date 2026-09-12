/**
 * retail_pos_touch_autofocus_test.js — the till must not raise the on-screen
 * keyboard the moment a phone opens it.
 *
 * THE BUG THIS PINS. `_renderPOS()` ends by putting the caret in the scan box
 * so the first barcode of a shift lands somewhere useful. On a desktop till
 * with a wedge scanner that is exactly right. On a phone it summons the
 * on-screen keyboard, which covers roughly the bottom half of the viewport --
 * including `#pos-peek`, the bar that exists precisely so Charge is reachable
 * without scrolling. Measured on a real Mi Note 10 (393x851 CSS) on
 * 2026-09-13: the till opened with its own primary affordance behind a
 * keyboard nobody asked for.
 *
 * WHY NO EXISTING TEST CAUGHT IT, and why this one is written the way it is:
 * Playwright scrolls an element into view before clicking it, so a
 * click-driven check can never detect a control that is merely COVERED. The
 * observable fact is not "can I click Charge" -- it is "was the scan box
 * focused at all", which is what this file measures, by recording every
 * focus() call by element id while driving the REAL `_renderPOS`.
 *
 * BOTH DIRECTIONS ARE PINNED, because this change must deny one case while
 * still allowing another, and the deny-half is the obvious one:
 *
 *   1. touch-only device  -> the scan box is NOT focused on mount
 *   2. desktop            -> it IS focused, exactly as it always was
 *   3. no matchMedia      -> it IS focused (the detector fails CLOSED to
 *                            desktop behaviour, so an unexpected environment
 *                            never silently disarms the wedge scanner)
 *
 * Case 1 also asserts THE CHECK RAN, not merely that the outcome looked
 * right: it requires `matchMedia` to have been queried with the touch-only
 * query, and requires the render to have produced real markup. Without those,
 * "the scan box was never focused" would pass just as happily if `_renderPOS`
 * had thrown on line one -- the classic outcome-instead-of-check trap, and
 * the exact shape that let the covered Charge button ship in the first place.
 *
 * Run: node products/retail/tests/retail_pos_touch_autofocus_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');
const TOUCH_QUERY = '(hover: none) and (pointer: coarse)';

// ─────────────────────────────────────────────────────────────────────────────
// Harness — same shape as retail_surface_pos_test.js's loadRetailSystem: a vm
// sandbox, stubbed DOM, the REAL subsystem-retail.js. The only addition is a
// configurable matchMedia, which is the whole point of this file.
// ─────────────────────────────────────────────────────────────────────────────

function makeElementStub(overrides) {
  const classes = [];
  return Object.assign({
    innerHTML: '', textContent: '', value: '', id: '', disabled: false,
    style: {}, classes,
    classList: {
      add(c) { if (!classes.includes(c)) classes.push(c); },
      remove(c) { const i = classes.indexOf(c); if (i !== -1) classes.splice(i, 1); },
      toggle(c, on) { if (on) this.add(c); else this.remove(c); },
      contains(c) { return classes.includes(c); },
    },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {},
    querySelectorAll() { return []; }, addEventListener() {},
  }, overrides);
}

/**
 * @param {object} opts
 *   opts.matchMedia — 'touch' | 'desktop' | 'absent'. 'absent' removes the
 *   function entirely, which is what the existing test sandboxes look like
 *   and what a very old browser looks like.
 */
function loadRetailSystem(opts) {
  const mode = (opts || {}).matchMedia || 'absent';
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');
  const focusLog = [];
  const mediaQueries = [];
  const els = Object.create(null);
  const state = { activeId: null };

  const getEl = (id) => {
    if (!els[id]) {
      els[id] = makeElementStub({
        id,
        focus() { focusLog.push(id); state.activeId = id; },
      });
    }
    return els[id];
  };
  ['pos-cart', 'pos-product-grid', 'pos-search', 'pos-total', 'pos-sub', 'pos-tax',
   'pos-change', 'pos-change-row', 'pos-checkout-btn', 'pos-disc', 'pos-tendered',
   'pos-customer', 'pos-cats', 'pos-held-count'].forEach(getEl);

  const sandbox = {
    console,
    t: (s) => s,
    fetch: () => Promise.reject(new Error('no network in this test')),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem: () => {} },
    document: {
      get activeElement() { return state.activeId ? getEl(state.activeId) : null; },
      getElementById(id) { return id === 'ret-styles' ? null : getEl(id); },
      createElement() { return makeElementStub(); },
      querySelector() { return makeElementStub(); },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
    SubsystemApp: { active: 'retail', showToast() {}, hasCapability: () => true, _navigate() {} },
  };
  if (mode !== 'absent') {
    sandbox.matchMedia = (q) => {
      mediaQueries.push(q);
      return { matches: mode === 'touch' && q === TOUCH_QUERY, media: q };
    };
  }
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');

  return { RetailSystem: sandbox.RetailSystem, focusLog, mediaQueries };
}

/** Drive the REAL _renderPOS and report what it focused. */
function mountPos(mode) {
  const ctx = loadRetailSystem({ matchMedia: mode });
  const content = makeElementStub();
  ctx.RetailSystem._renderPOS(content);
  // Anti-vacuity: if the render died early, "nothing was focused" would be
  // true for the wrong reason and case 1 would pass while the product was
  // broken in a different way.
  assert.ok(content.innerHTML.length > 500,
    `_renderPOS produced ${content.innerHTML.length} chars of markup — it did not really run, ` +
    'so nothing below would be measuring what it claims to.');
  return ctx;
}

const cases = [];
function test(name, fn) { cases.push([name, fn]); }

// ── 1. The deny half ─────────────────────────────────────────────────────────
test('a touch-only device does NOT get the scan box focused on mount', () => {
  const ctx = mountPos('touch');
  assert.ok(!ctx.focusLog.includes('pos-search'),
    'The till focused #pos-search on a touch-only device. That raises the on-screen ' +
    'keyboard over the bottom half of the viewport, including #pos-peek — the bar that ' +
    `exists so Charge is reachable without scrolling. focus() calls: ${JSON.stringify(ctx.focusLog)}`);

  // ASSERT THE CHECK RAN. A 403 cannot tell "the guard refused" from "the
  // guard never ran"; neither can an absent focus() call.
  assert.ok(ctx.mediaQueries.includes(TOUCH_QUERY),
    'The scan box was not focused, but the touch-only media query was never asked — ' +
    `so this passed for some other reason. Queries seen: ${JSON.stringify(ctx.mediaQueries)}`);
});

// ── 2 & 3. The allow half — the one that is easy to forget ───────────────────
test('a desktop till still gets the scan box focused on mount', () => {
  const ctx = mountPos('desktop');
  assert.ok(ctx.focusLog.includes('pos-search'),
    'The desktop till no longer focuses #pos-search on mount. That is the wedge-scanner ' +
    'affordance: the first barcode of a shift must land in the scan box without the ' +
    `cashier clicking anything first. focus() calls: ${JSON.stringify(ctx.focusLog)}`);
});

test('an environment with no matchMedia falls back to desktop behaviour', () => {
  const ctx = mountPos('absent');
  assert.ok(ctx.focusLog.includes('pos-search'),
    'With no matchMedia available the detector must fail CLOSED to desktop behaviour. ' +
    'Failing the other way would silently disarm the wedge scanner on any environment ' +
    `that does not expose matchMedia. focus() calls: ${JSON.stringify(ctx.focusLog)}`);
});

// ─────────────────────────────────────────────────────────────────────────────
let failed = 0;
for (const [name, fn] of cases) {
  try {
    fn();
    console.log(`PASS: ${name}`);
  } catch (err) {
    failed += 1;
    console.error(`FAIL: ${name}\n      ${err && err.message}`);
  }
}
console.log(`\n=== ${cases.length - failed} passed, ${failed} failed, ${cases.length} total ===`);
process.exit(failed ? 1 : 0);
