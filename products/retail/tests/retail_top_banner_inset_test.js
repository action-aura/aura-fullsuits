/**
 * `--top-banner-inset` must reserve space for bands at the TOP, and only those.
 *
 * The shell publishes this custom property from `_reflowTopBanners()`, and
 * css/main.css's `.page { top: var(--top-banner-inset, 0px) }` plus index.html's
 * `height: calc(100vh - var(--top-banner-inset, 0px))` consume it to push and
 * shrink every screen clear of a top banner. So whatever this number says, the
 * whole app moves by.
 *
 * THE BUG THIS PINS (measured 2026-09-12): `#aura-sync-banner` is ONE persistent
 * element reused by every sync tier. Its "calm" tier -- which means sync is
 * HEALTHY, so the overwhelmingly common state on a working till -- is not a
 * banner at all. `_renderSyncCalmState()` styles it `position:fixed;bottom:14px`
 * as a small pill in the corner. `_reflowTopBanners()` measured it by id and
 * reserved its height at the TOP regardless, so a licensed, healthy, syncing
 * till carried a permanent ~30px empty strip across the top of every screen,
 * produced by an element sitting in the opposite corner of the window.
 *
 * It is checked by POSITION rather than against `_syncBannerTier` because that
 * tier is a hand-maintained string: a future tier would have to remember to
 * update a list, which is the stale-list failure this codebase keeps hitting.
 * Where the element actually IS cannot drift from where it is drawn.
 *
 * Harness shape and the fake world below follow retail_license_banner_test.js,
 * which boots the same real app-shell.js source in a vm and calls the method
 * under test directly.
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP_SHELL = path.join(__dirname, '..', 'frontend', 'app-shell.js');
const SHELL_SRC = fs.readFileSync(APP_SHELL, 'utf8');

// Heights are arbitrary but DISTINCT, so an assertion can name which element a
// wrong answer came from instead of just reporting a mismatched number.
const LICENSE_BANNER_H = 64;
const TOP_SYNC_BANNER_H = 40;
const CALM_PILL_H = 30;
// Where the calm pill actually sits: pinned near the bottom of an 800px window.
const CALM_PILL_TOP = 770;

function fakeElement(rect) {
  return {
    style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {}, remove() {},
    appendChild() {}, setAttribute() {}, removeAttribute() {}, focus() {}, click() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    innerHTML: '', textContent: '', title: '', hidden: false, value: '',
    getBoundingClientRect() { return rect || { top: 0, height: 0, bottom: 0, left: 0, right: 0 }; },
  };
}

/** Boots the real shell source with a document we can steer element-by-element,
 * and a documentElement whose setProperty we can read back. */
function bootShell(src) {
  const published = {};
  const elements = new Map();

  const documentElement = fakeElement();
  documentElement.style = {
    setProperty(name, value) { published[name] = value; },
    removeProperty(name) { delete published[name]; },
  };

  const sandbox = {
    console: { log() {}, warn() {}, error() {} },
    document: {
      readyState: 'complete',
      body: fakeElement(),
      documentElement,
      head: fakeElement(),
      getElementById(id) { return elements.has(id) ? elements.get(id) : null; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      createElement() { return fakeElement(); },
      addEventListener() {},
    },
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    setTimeout, clearTimeout, setInterval, clearInterval,
    location: { href: '', hash: '', reload() {} },
    navigator: { language: 'en', userAgent: 'node' },
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    CustomEvent: function () {}, Event: function () {},
    BroadcastChannel: undefined,
    t: (s) => s,
    async fetch() { return { ok: true, status: 200, json: async () => ({}) }; },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(src || SHELL_SRC, sandbox, { filename: 'app-shell.js' });

  const app = sandbox.SubsystemApp;
  assert.ok(
    app && typeof app._reflowTopBanners === 'function',
    'app-shell.js did not yield a SubsystemApp with a _reflowTopBanners() -- this harness has nothing to test.'
  );
  return { app, published, elements };
}

/** Places the named overlays, runs the reflow, returns the published value. */
function insetWith(world, overlays) {
  world.elements.clear();
  for (const [id, rect] of Object.entries(overlays)) {
    world.elements.set(id, fakeElement(rect));
  }
  world.app._reflowTopBanners();
  return world.published['--top-banner-inset'];
}

const AT_TOP = (h) => ({ top: 0, height: h, bottom: h, left: 0, right: 1366 });
const AT_BOTTOM = (h) => ({ top: CALM_PILL_TOP, height: h, bottom: CALM_PILL_TOP + h, left: 1200, right: 1352 });

// ── 1. A real top banner still reserves its height ──────────────────────────
function testATopBannerStillReservesItsHeight() {
  const world = bootShell();
  const inset = insetWith(world, { 'aura-license-banner': AT_TOP(LICENSE_BANNER_H) });
  assert.strictEqual(
    inset, `${LICENSE_BANNER_H}px`,
    `A licence banner ${LICENSE_BANNER_H}px tall at the top of the viewport must reserve ` +
    `${LICENSE_BANNER_H}px, got ${inset}. Without this the banner overlaps the header, which ` +
    'is the bug --top-banner-inset was introduced to fix -- do not let the fix for the ' +
    'bottom-corner pill undo it.'
  );
  console.log('PASS: a banner at the top still reserves its own height');
}

// ── 2. THE REGRESSION: a pill in the bottom corner reserves nothing ─────────
function testTheCalmSyncPillReservesNothing() {
  const world = bootShell();
  const inset = insetWith(world, { 'aura-sync-banner': AT_BOTTOM(CALM_PILL_H) });
  assert.strictEqual(
    inset, '0px',
    `The calm sync pill sits at the BOTTOM (top: ${CALM_PILL_TOP} in an 800px window) and must ` +
    `reserve nothing at the top, got ${inset}. Reserving ${CALM_PILL_H}px here puts a permanent ` +
    'empty strip across the top of every screen on a healthy, licensed, syncing till -- which ' +
    'is the normal state of every shop that has finished setting up.'
  );
  console.log('PASS: the calm sync pill, pinned bottom-right, reserves no space at the top');
}

// ── 3. Both halves together: the common healthy case and the blocked case ──
function testTopBannerAndBottomPillTogether() {
  const world = bootShell();
  const inset = insetWith(world, {
    'aura-license-banner': AT_TOP(LICENSE_BANNER_H),
    'aura-sync-banner': AT_BOTTOM(CALM_PILL_H),
  });
  assert.strictEqual(
    inset, `${LICENSE_BANNER_H}px`,
    `With a licence banner at the top AND the calm pill at the bottom, only the banner counts ` +
    `(${LICENSE_BANNER_H}px), got ${inset}.`
  );
  console.log('PASS: with both on screen, only the one at the top counts');
}

// ── 4. Two genuine top banners overlap, so reserve the TALLEST, never the sum ─
function testTwoTopBannersReserveTheTallestNotTheSum() {
  const world = bootShell();
  const inset = insetWith(world, {
    'aura-license-banner': AT_TOP(LICENSE_BANNER_H),
    'aura-sync-banner': AT_TOP(TOP_SYNC_BANNER_H),
  });
  assert.strictEqual(
    inset, `${LICENSE_BANNER_H}px`,
    'Both banners stack at top:0, so they overlap rather than sum -- the reservation must be ' +
    `the tallest (${LICENSE_BANNER_H}px), not ${LICENSE_BANNER_H + TOP_SYNC_BANNER_H}px. Got ${inset}.`
  );
  console.log('PASS: two overlapping top banners reserve the tallest, not their sum');
}

// ── 5. Nothing on screen reserves nothing ───────────────────────────────────
function testNoOverlaysReserveNothing() {
  const world = bootShell();
  const inset = insetWith(world, {});
  assert.strictEqual(
    inset, '0px',
    `A till with no banner at all must reserve 0px, got ${inset}. Reserving on show and ` +
    'forgetting to release on hide leaves a permanent empty strip -- a subtler bug than the ' +
    'one the inset exists to fix.'
  );
  console.log('PASS: with no overlays at all, nothing is reserved');
}

// ── 6. Mutation proof ───────────────────────────────────────────────────────
// Check 2 is the whole point of this file, and a check that passes both before
// and after the defect is reintroduced proves nothing. Strip the position guard
// out of the real source, re-boot, and require check 2's scenario to produce
// the WRONG answer -- if it still says 0px, this file is not measuring what it
// claims to.
function testTheseChecksSurviveRemovingThePositionGuard() {
  const guard = /if \(rect\.top > TOP_EDGE_TOLERANCE_PX\) continue;/;
  assert.ok(
    guard.test(SHELL_SRC),
    'Could not find the `if (rect.top > TOP_EDGE_TOLERANCE_PX) continue;` guard in app-shell.js. ' +
    'Either it was removed -- in which case the bottom-corner pill is being reserved at the top ' +
    'again -- or it was renamed and this mutation proof needs re-anchoring. Either way this file ' +
    'is no longer proving anything and must not be left passing.'
  );

  const mutated = SHELL_SRC.replace(guard, '/* guard removed by mutation proof */');
  const world = bootShell(mutated);
  const inset = insetWith(world, { 'aura-sync-banner': AT_BOTTOM(CALM_PILL_H) });
  assert.strictEqual(
    inset, `${CALM_PILL_H}px`,
    'With the position guard removed, the bottom-corner pill should once again be reserved at ' +
    `the top (${CALM_PILL_H}px), but the harness reported ${inset}. That means these checks ` +
    'cannot actually see the defect they exist to catch.'
  );
  console.log('PASS: the checks survived a mutant that drops the top-edge position guard  ' +
    `[caught: the bottom pill was reserved as ${CALM_PILL_H}px of top inset]`);
}

/* PER-CHECK ISOLATION -- same reasoning as retail_design_rtl_test.js: a flat
   sequence reports one failure per run however many exist, and hides the rest
   as "not run", which is indistinguishable from "passed". */
const CHECKS = [
  ['a top banner still reserves its height', testATopBannerStillReservesItsHeight],
  ['the calm sync pill reserves nothing', testTheCalmSyncPillReservesNothing],
  ['a top banner and the bottom pill together', testTopBannerAndBottomPillTogether],
  ['two top banners reserve the tallest, not the sum', testTwoTopBannersReserveTheTallestNotTheSum],
  ['no overlays reserve nothing', testNoOverlaysReserveNothing],
  ['the checks survive removing the position guard', testTheseChecksSurviveRemovingThePositionGuard],
];

function main() {
  const failures = [];
  for (const [name, fn] of CHECKS) {
    try {
      fn();
    } catch (err) {
      failures.push(name);
      console.error(`FAIL: ${name}`);
      console.error('      ' + String((err && err.message) || err).replace(/\n/g, '\n      '));
    }
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_top_banner_inset_test.js - ${failures.length} of ${CHECKS.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_top_banner_inset_test.js - ${CHECKS.length} checks`);
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_top_banner_inset_test.js (runner)');
  console.error((err && err.message) || err);
  process.exitCode = 1;
}
