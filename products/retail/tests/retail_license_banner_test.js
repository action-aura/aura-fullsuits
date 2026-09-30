/**
 * retail_license_banner_test.js -- a fresh or restricted install is
 * READ-ONLY, not unlocked (deliberate, pinned by capability_guard.py's own
 * tests: see test_pre_activation_states_deny_new_mutation). The UX around
 * that IS a bug: the only way the shopkeeper used to learn about it was a
 * 403 in a network tab the first time they pressed Charge, and the only fix
 * was a separate static page outside the app shell's theming and navigation.
 *
 * app-shell.js now renders a persistent, in-shell banner
 * (SubsystemApp._renderLicenseBanner) whenever the license state is one
 * where selling is blocked but the shop's data stays safe. This file RUNS
 * that method against real state values rather than grepping for its
 * existence -- a banner function that exists but never actually paints
 * anything, or that paints on every state including a healthy one, would
 * satisfy a static check and still be the exact bug this fixes.
 *
 * Harness: app-shell.js evaluated in a vm against a thin DOM/fetch stub, the
 * same technique retail_signout_unsynced_guard_test.js uses. Run standalone
 * with plain Node, no build step:
 *
 *     node products/retail/tests/retail_license_banner_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP_SHELL = path.join(__dirname, '..', 'frontend', 'app-shell.js');
const SHELL_SRC = fs.readFileSync(APP_SHELL, 'utf8');
const EN_LOCALE = path.join(__dirname, '..', 'frontend', 'locales', 'en.json');
const AR_LOCALE = path.join(__dirname, '..', 'frontend', 'locales', 'ar.json');
const STATE_MACHINE = path.join(
  __dirname, '..', '..', '..', 'commercial_runtime', 'licensing_contracts', 'state_machine.py'
);

function fakeElement() {
  return {
    style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {}, remove() {},
    appendChild() {}, setAttribute() {}, removeAttribute() {}, focus() {}, click() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    innerHTML: '', textContent: '', title: '', hidden: false, value: '',
  };
}

/** Boots the shell (real source by default, or a mutated copy) with a
 * controllable-enough world that app-shell.js's top-level code (IIFEs,
 * event wiring) runs without throwing. Nothing here needs a real network:
 * every test calls _renderLicenseBanner()/openLicensing() directly, the same
 * way retail_signout_unsynced_guard_test.js calls logout() directly. */
function bootShell(src) {
  const sandbox = {
    console: { log() {}, warn() {}, error() {} },
    document: {
      readyState: 'complete',
      body: fakeElement(),
      documentElement: fakeElement(),
      head: fakeElement(),
      getElementById() { return null; },
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
    app && typeof app._renderLicenseBanner === 'function' && Array.isArray(app.LICENSE_BANNER_BLOCKED_STATES),
    'app-shell.js did not yield a SubsystemApp with a _renderLicenseBanner()/LICENSE_BANNER_BLOCKED_STATES -- '
    + 'this harness has nothing to test'
  );
  return { app, sandbox };
}

/* ── Derive the blocked-state list from the ACTUAL state machine, the same
 * way app-shell.js's own comment says LICENSE_BANNER_BLOCKED_STATES was
 * derived: DATA_PRESERVED_FAMILY minus ACTIVE_FAMILY. state_machine.py
 * writes DATA_PRESERVED_FAMILY as `ACTIVE_FAMILY | frozenset({...extra...})`
 * with no overlap between the two operands, so that "...extra..." block IS
 * the difference -- parsed from source, never re-typed, so a state added to
 * the Python module without a matching addition to app-shell.js's list is
 * caught here instead of silently losing banner coverage. ── */
function blockedStatesFromStateMachine() {
  const src = fs.readFileSync(STATE_MACHINE, 'utf8');
  const m = /DATA_PRESERVED_FAMILY\s*=\s*ACTIVE_FAMILY\s*\|\s*frozenset\(\s*\{([\s\S]*?)\}\s*\)/.exec(src);
  assert.ok(
    m,
    "Could not find DATA_PRESERVED_FAMILY = ACTIVE_FAMILY | frozenset({...}) in state_machine.py -- "
    + 'this parser needs re-anchoring to the current source shape.'
  );
  const states = [...m[1].matchAll(/LicenseState\.([A-Z_]+)/g)].map((mm) => mm[1]);
  assert.ok(
    states.length >= 5,
    `Only parsed ${states.length} state(s) out of DATA_PRESERVED_FAMILY's extra set -- the parse is probably broken.`
  );
  return states;
}

/* ── Derive the banner's own t(...) keys FROM THE SOURCE, not a hardcoded
 * array here -- a frozen list in the test cannot catch a key added to the
 * function later, which is exactly the kind of drift this project has been
 * bitten by before (see licensing.js's own divergent copy of
 * _needsActivation). ── */
function extractBannerTranslationKeys() {
  // \r?\n, not \n: app-shell.js is CRLF (see the plan's own note that the
  // locale files are CRLF too) -- a bare \n anchor never matches immediately
  // after "}," on a CRLF file, because the character right there is \r.
  const m = /_renderLicenseBanner\(state\) \{([\s\S]*?)\r?\n  \},\r?\n/.exec(SHELL_SRC);
  assert.ok(m, 'Could not find _renderLicenseBanner(state) {...} in app-shell.js -- re-anchor the extractor.');
  // Negative lookbehind, not a bare `t(`: "document.createElement('div')" ends
  // in "...ElemenT('div')", which a bare /t\('...'\)/ matches as if `t()` had
  // been called with 'div' -- a real false positive this extractor hit on its
  // first run. Requiring no identifier character right before the `t` rules
  // that out without excluding the real `t(...)` calls, none of which are
  // ever preceded by one.
  const keys = [...m[1].matchAll(/(?<![A-Za-z0-9_])t\('([^']+)'\)/g)].map((mm) => mm[1]);
  assert.ok(
    keys.length >= 2,
    `Only found ${keys.length} t(...) call(s) inside _renderLicenseBanner -- the extractor is probably broken.`
  );
  return keys;
}

/* ── Mutation harness (same shape as retail_shell_chrome_test.js): re-anchor
 * to exact source text, require the anchor to occur exactly once so a stale
 * mutation cannot silently no-op. ── */
function eolOf(src) { return src.indexOf('\r\n') !== -1 ? '\r\n' : '\n'; }
function nlFor(src) { const eol = eolOf(src); return (s) => s.replace(/\n/g, eol); }

function mutate(src, pairs) {
  const nl = nlFor(src);
  let out = src;
  for (const [rawFind, rawReplace] of pairs) {
    const find = nl(rawFind);
    const replace = nl(rawReplace);
    const hits = out.split(find).length - 1;
    assert.strictEqual(
      hits, 1,
      `Mutation anchor occurs ${hits} time(s), expected exactly 1:\n  ${JSON.stringify(find)}\n\n`
      + 'A mutation that no longer applies would let the proof below pass while proving nothing. Re-anchor it.'
    );
    out = out.replace(find, replace);
  }
  return out;
}

async function provesMutation(what, check) {
  let threw = null;
  try {
    await check();
  } catch (err) {
    threw = err;
  }
  assert.ok(
    threw,
    `MUTATION SURVIVED -- ${what}\n`
    + 'The guard for this passed against a build with the behaviour deliberately broken, so it is '
    + 'not actually watching it. Fix the check, not the mutation.'
  );
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 160)}]`;
}

/* ═══ 1 ═══ a blocked state renders the banner, action control included ══ */
function testBlockedStateRendersBanner() {
  const { app } = bootShell();
  app._renderLicenseBanner('NOT_CONFIGURED');
  assert.ok(app._licenseBannerEl, 'NOT_CONFIGURED (a blocked state) did not render a banner element at all.');
  const html = app._licenseBannerEl.innerHTML;
  assert.ok(/<button/.test(html), 'The rendered banner markup contains no action control (<button>).');
  assert.ok(/openLicensing\(\)/.test(html), 'The banner\'s action control does not reach openLicensing().');
  console.log('PASS: a blocked state (NOT_CONFIGURED) renders the banner with an action control');
}

/* ═══ 2 ═══ THE ONE THAT PROTECTS PAYING CUSTOMERS: no banner on ACTIVE ══ */
function testActiveStateRendersNoBanner() {
  const { app } = bootShell();
  app._renderLicenseBanner('ACTIVE_ONLINE');
  assert.strictEqual(
    app._licenseBannerEl, null,
    'ACTIVE_ONLINE (a healthy, activated install) rendered a banner -- a permanent scary banner on a paying '
    + 'customer\'s till is worse than the onboarding bug this change fixes.'
  );
  console.log('PASS: a healthy ACTIVE_ONLINE install renders no banner at all');
}

/* ═══ 3 ═══ every state derived from the state machine produces the banner ═
 * Looped, not enumerated by hand, so a state added to state_machine.py's
 * DATA_PRESERVED_FAMILY later cannot silently lose banner coverage -- and
 * cross-checked against a fresh parse of that exact file, so a hand-edit
 * that only touches app-shell.js's copy of the list is caught too. */
function testEveryDerivedBlockedStateRendersTheBanner() {
  const fromPython = blockedStatesFromStateMachine();
  const { app } = bootShell();

  assert.deepStrictEqual(
    [...app.LICENSE_BANNER_BLOCKED_STATES].sort(),
    [...fromPython].sort(),
    "app-shell.js's LICENSE_BANNER_BLOCKED_STATES has drifted from state_machine.py's "
    + 'DATA_PRESERVED_FAMILY minus ACTIVE_FAMILY.'
  );

  for (const state of app.LICENSE_BANNER_BLOCKED_STATES) {
    app._licenseBannerEl = null; // isolate each iteration from the last
    app._renderLicenseBanner(state);
    assert.ok(app._licenseBannerEl, `${state} (a derived blocked state) did not render a banner.`);
    assert.ok(
      /openLicensing\(\)/.test(app._licenseBannerEl.innerHTML),
      `${state}'s banner has no working action control.`
    );
  }
  console.log(
    `PASS: all ${app.LICENSE_BANNER_BLOCKED_STATES.length} derived blocked states render the banner `
    + '(list matches state_machine.py)'
  );
}

/* ═══ 4 ═══ the action invokes the shell's EXISTING openLicensing() ═══════
 * Runs the onclick text through the same vm context app-shell.js was loaded
 * into (the `SubsystemApp` bare identifier resolves to the same object,
 * exactly like retail_shell_chrome_test.js's `vm.runInContext('ThemeEngine',
 * sandbox)`), so this is a real invocation, not a substring guess. */
function testActionInvokesExistingOpenLicensing() {
  const { app, sandbox } = bootShell();
  let opens = 0;
  app.openLicensing = () => { opens++; };
  app._renderLicenseBanner('RESTRICTED');
  const html = app._licenseBannerEl.innerHTML;
  assert.ok(
    !/licensing\.html/.test(html),
    'The banner hardcodes a licensing URL of its own instead of reusing openLicensing().'
  );
  const m = /onclick="([^"]+)"/.exec(html);
  assert.ok(m, 'No onclick handler found on the banner\'s action control.');
  vm.runInContext(m[1], sandbox);
  assert.strictEqual(opens, 1, "Clicking the banner's action did not call the shell's existing openLicensing().");
  console.log("PASS: the banner's action invokes the shell's existing openLicensing()");
}

/* ═══ 5 ═══ every t(...) key the banner uses exists in BOTH locales, with a
 * REAL Arabic value (not identical to the English key) ═══════════════════ */
function testBannerStringsHaveRealArabicTranslations() {
  const keys = extractBannerTranslationKeys();
  const en = JSON.parse(fs.readFileSync(EN_LOCALE, 'utf8'));
  const ar = JSON.parse(fs.readFileSync(AR_LOCALE, 'utf8'));
  const problems = [];
  for (const key of keys) {
    if (!(key in en)) problems.push(`${JSON.stringify(key)} missing from en.json`);
    if (!(key in ar)) problems.push(`${JSON.stringify(key)} missing from ar.json`);
    else if (ar[key] === key) problems.push(`${JSON.stringify(key)} has no real Arabic translation in ar.json`);
  }
  assert.deepStrictEqual(problems, [], `Banner string(s) with missing/fake translations:\n  ${problems.join('\n  ')}`);
  console.log(`PASS: all ${keys.length} banner string(s) exist in both locale files with a real Arabic translation`);
}

/* ═══ 6 ═══ ANTI-VACUITY: the harness can tell "present" from "absent" apart,
 * so a stub that always renders empty (or never renders at all) could not
 * pass both test 1 and test 2 above. ══════════════════════════════════════ */
function testHarnessDistinguishesPresenceFromAbsence() {
  const { app } = bootShell();

  app._renderLicenseBanner('SUSPENDED');
  const first = app._licenseBannerEl;
  assert.ok(first, 'Setup for the anti-vacuity check failed -- no element rendered for a blocked state.');
  const presentHTML = first.innerHTML;
  assert.ok(
    presentHTML.length > 40,
    'The "present" case rendered suspiciously little markup -- too little to prove it apart from empty.'
  );

  app._renderLicenseBanner('ACTIVE_ONLINE');
  assert.strictEqual(
    app._licenseBannerEl, null,
    'Setup for the anti-vacuity check failed -- an active state still left an element behind.'
  );

  // Toggle back: proves the SAME function produces both outcomes for two
  // different inputs, rather than two branches that coincidentally look
  // alike from the outside (e.g. a stub that always builds an empty node).
  app._renderLicenseBanner('SUSPENDED');
  assert.ok(app._licenseBannerEl, 'Toggling back to a blocked state did not re-render the banner.');
  assert.strictEqual(
    app._licenseBannerEl.innerHTML, presentHTML,
    'The same blocked state produced different markup on a second render.'
  );
  console.log('PASS: the harness genuinely distinguishes "banner present" from "banner absent" (anti-vacuity)');
}

/* ═══ MUTATION PROOF, direction 1 ═══════════════════════════════════════
 * Removes the banner render entirely (an early `return;`). Test 1's own
 * assertion, re-run against this mutant, must fail -- proving test 1 would
 * actually catch a build that silently stopped rendering the banner. */
async function testMutationRemovingTheBannerRenderIsCaught() {
  const mutated = mutate(SHELL_SRC, [[
    // P1b: the condition grew a second line (`&& !this._setupGuideOwnsFirstRun`)
    // so the anchor is the head of the function only. Mutation intent unchanged.
    `  _renderLicenseBanner(state) {
    this._watchGuideCard(state);
    const blocked = this.LICENSE_BANNER_BLOCKED_STATES.includes(state)`,
    `  _renderLicenseBanner(state) {
    return; // MUTATION: banner render removed entirely
    const blocked = this.LICENSE_BANNER_BLOCKED_STATES.includes(state)`,
  ]]);
  const msg = await provesMutation(
    'test 1 (a blocked state renders the banner) survived a mutant that removes the banner render entirely',
    async () => {
      const { app } = bootShell(mutated);
      app._renderLicenseBanner('NOT_CONFIGURED');
      assert.ok(app._licenseBannerEl, 'NOT_CONFIGURED did not render a banner element at all.');
    }
  );
  console.log('PASS: ' + msg);
}

/* ═══ MUTATION PROOF, direction 2 ═══════════════════════════════════════
 * Forces the banner to render UNCONDITIONALLY, ignoring state. Test 2's own
 * assertion, re-run against this mutant, must fail -- proving test 2 would
 * actually catch the single worst regression here: a banner that shows on a
 * healthy, paying customer's till. */
async function testMutationAlwaysRenderingIsCaught() {
  const mutated = mutate(SHELL_SRC, [[
    // P1b: replaces the WHOLE two-line condition, including the setup-guide
    // exemption, so this mutant still means "ignore state entirely".
    `    const blocked = this.LICENSE_BANNER_BLOCKED_STATES.includes(state)
      && !this._setupGuideOwnsFirstRun(state);`,
    `    const blocked = true; // MUTATION: renders unconditionally, ignoring state`,
  ]]);
  const msg = await provesMutation(
    'test 2 (a healthy ACTIVE state renders no banner) survived a mutant that renders the banner unconditionally',
    async () => {
      const { app } = bootShell(mutated);
      app._renderLicenseBanner('ACTIVE_ONLINE');
      assert.strictEqual(app._licenseBannerEl, null, 'ACTIVE_ONLINE rendered a banner.');
    }
  );
  console.log('PASS: ' + msg);
}

/* ═══ P1b/P2S: the first-run bar hands over to the setup guide -- for OWNERS
 * only, only while the guide exists, AND only while the guide's checklist card
 * is really on screen and says the licence is the next step. Directions,
 * because this guard both DENIES the bar and must still ALLOW it:
 *   a. owner + guide + NOT_CONFIGURED + card showing the licence step -> no bar
 *   b. owner + guide ABSENT                    -> bar stays (nothing replaces it)
 *   c. cashier + guide present                 -> bar stays (never sees the checklist)
 *   d. owner + guide + a NON-first-run blocked state (SUSPENDED) -> bar stays
 *   e. owner + guide + EXPIRED (blocked, a real state, NOT first-run) -> bar stays
 *   f. owner + guide but NO card on this screen (the till)      -> bar stays
 *   g. owner + guide + a card whose next step is something else -> bar stays
 * (f) and (g) are the verifier's finding: "the guide exists" used to be enough,
 * so an owner on the till, or with the licence step not next, had a blocked till
 * and no explanation anywhere. */
const LICENCE_CARD = { textContent: '0 of 9 steps done · Next: Activate your licence' };
const OTHER_CARD = { textContent: '1 of 9 steps done · Next: Add your shop name' };

/** card: undefined = no #setup-guide-card in the document; else the .sg-sub node. */
function stubCard(sandbox, card) {
  sandbox.document.getElementById = (id) => (id === 'setup-guide-card' && card
    ? { querySelector: (sel) => (sel === '.sg-sub' ? card : null) }
    : null);
}

function bannerRun(src, role, hasGuide, state, card) {
  const { app, sandbox } = bootShell(src);
  app.role = role;
  if (hasGuide) sandbox.AuraSetupGuide = { open() {} };
  stubCard(sandbox, card);
  app._renderLicenseBanner(state);
  return !!app._licenseBannerEl;
}

function testSetupGuideOwnsFirstRunForOwnersOnly() {
  assert.strictEqual(bannerRun(null, 'admin', true, 'NOT_CONFIGURED', LICENCE_CARD), false, '(a) owner + guide + card + NOT_CONFIGURED still shows the bar');
  assert.strictEqual(bannerRun(null, 'admin', false, 'NOT_CONFIGURED', LICENCE_CARD), true, '(b) owner WITHOUT the guide lost the bar -- nothing replaces it');
  assert.strictEqual(bannerRun(null, 'cashier', true, 'NOT_CONFIGURED', LICENCE_CARD), true, '(c) a cashier lost the bar');
  assert.strictEqual(bannerRun(null, 'admin', true, 'SUSPENDED', LICENCE_CARD), true, '(d) owner + guide lost the SUSPENDED warning');
  assert.ok(bootShell().app.LICENSE_BANNER_BLOCKED_STATES.includes('EXPIRED'),
    'EXPIRED is no longer a blocked state -- direction (e) below would prove nothing');
  assert.strictEqual(bannerRun(null, 'admin', true, 'EXPIRED', LICENCE_CARD), true, '(e) owner + guide lost the EXPIRED warning');
  assert.strictEqual(bannerRun(null, 'admin', true, 'NOT_CONFIGURED', undefined), true, '(f) owner on a screen with no guide card lost the bar: blocked till, no explanation');
  assert.strictEqual(bannerRun(null, 'admin', true, 'NOT_CONFIGURED', OTHER_CARD), true, '(g) owner whose guide card does not name the licence step lost the bar');
  console.log('PASS: the first-run bar yields to the setup guide for owners only, only while its card shows the licence step (7 directions)');
}

async function testSetupGuideGuardMutationsAreCaught() {
  const nl = nlFor(SHELL_SRC);
  const guard = nl(`    return this.role === 'admin'
      && this._hasSetupGuide()
      && this.FIRST_RUN_LICENSE_STATES.includes(state)
      && this._guideCardShowsLicenceStep();`);
  assert.strictEqual(SHELL_SRC.split(guard).length - 1, 1, 'guard anchor moved -- re-anchor this proof');
  const noRole = SHELL_SRC.replace(guard, nl(`    return true // MUTATION: role check dropped
      && this._hasSetupGuide()
      && this.FIRST_RUN_LICENSE_STATES.includes(state)
      && this._guideCardShowsLicenceStep();`));
  const noGuide = SHELL_SRC.replace(guard, nl(`    return this.role === 'admin'
      && true // MUTATION: guide-present check dropped
      && this.FIRST_RUN_LICENSE_STATES.includes(state)
      && this._guideCardShowsLicenceStep();`));
  const noState = SHELL_SRC.replace(guard, nl(`    return this.role === 'admin'
      && this._hasSetupGuide()
      && true // MUTATION: first-run-state check dropped
      && this._guideCardShowsLicenceStep();`));
  const noCard = SHELL_SRC.replace(guard, nl(`    return this.role === 'admin'
      && this._hasSetupGuide()
      && this.FIRST_RUN_LICENSE_STATES.includes(state)
      && true; // MUTATION: card-shows-the-licence-step check dropped`));
  // The list itself: EXPIRED slipped into the first-run list is the other way
  // this handover goes wrong (an expired shop would lose its warning).
  const listLine = `FIRST_RUN_LICENSE_STATES: ['NOT_CONFIGURED'],`;
  assert.strictEqual(SHELL_SRC.split(listLine).length - 1, 1, 'FIRST_RUN_LICENSE_STATES anchor moved -- re-anchor this proof');
  const expiredFirstRun = SHELL_SRC.replace(listLine, `FIRST_RUN_LICENSE_STATES: ['NOT_CONFIGURED', 'EXPIRED'], // MUTATION`);
  const msgs = [];
  msgs.push(await provesMutation('cashier-keeps-the-bar survived a mutant that drops the role check',
    async () => assert.strictEqual(bannerRun(noRole, 'cashier', true, 'NOT_CONFIGURED', LICENCE_CARD), true, 'cashier lost the bar')));
  msgs.push(await provesMutation('owner-without-guide-keeps-the-bar survived a mutant that drops the guide-present check',
    async () => assert.strictEqual(bannerRun(noGuide, 'admin', false, 'NOT_CONFIGURED', LICENCE_CARD), true, 'owner without guide lost the bar')));
  msgs.push(await provesMutation('suspended-keeps-the-bar survived a mutant that drops the first-run-state check',
    async () => assert.strictEqual(bannerRun(noState, 'admin', true, 'SUSPENDED', LICENCE_CARD), true, 'SUSPENDED lost the bar')));
  msgs.push(await provesMutation('no-card-keeps-the-bar survived a mutant that drops the card-shows-the-licence-step check',
    async () => assert.strictEqual(bannerRun(noCard, 'admin', true, 'NOT_CONFIGURED', undefined), true, 'owner with no guide card on screen lost the bar')));
  msgs.push(await provesMutation('other-step-card-keeps-the-bar survived a mutant that drops the card-shows-the-licence-step check',
    async () => assert.strictEqual(bannerRun(noCard, 'admin', true, 'NOT_CONFIGURED', OTHER_CARD), true, 'owner whose card names another step lost the bar')));
  msgs.push(await provesMutation('expired-keeps-the-bar survived a mutant that adds EXPIRED to FIRST_RUN_LICENSE_STATES',
    async () => assert.strictEqual(bannerRun(expiredFirstRun, 'admin', true, 'EXPIRED', LICENCE_CARD), true, 'EXPIRED lost the bar')));
  for (const m of msgs) console.log('PASS: ' + m);
}

/* The card comes and goes without telling the shell (navigation, the checklist
 * reloading). While a first-run state is on, a MutationObserver redraws the
 * banner when the answer changes -- and is released when the state stops being
 * first-run. Driven with a stub observer whose callback the test fires. */
async function bannerWatchRun(src) {
  const { app, sandbox } = bootShell(src);
  const observers = [];
  sandbox.MutationObserver = function (cb) {
    const o = { cb, observed: 0, disconnected: false,
      observe() { o.observed++; }, disconnect() { o.disconnected = true; } };
    observers.push(o);
    return o;
  };
  app.role = 'admin';
  sandbox.AuraSetupGuide = { open() {} };
  let card;                                   // undefined = not on screen
  stubCard(sandbox, undefined);
  const setCard = (c) => { card = c; stubCard(sandbox, card); };
  const settle = () => new Promise((r) => setTimeout(r, 5));
  const out = {};
  app._renderLicenseBanner('NOT_CONFIGURED');
  out.barWithoutCard = !!app._licenseBannerEl;
  out.observersAfterFirstRender = observers.length;
  setCard(LICENCE_CARD);
  observers.forEach((o) => o.cb());
  await settle();
  out.barWithCard = !!app._licenseBannerEl;
  setCard(undefined);
  observers.forEach((o) => o.cb());
  await settle();
  out.barAfterCardGone = !!app._licenseBannerEl;
  app._renderLicenseBanner('ACTIVE_ONLINE');
  out.releasedOnActive = observers.length > 0 && observers.every((o) => o.disconnected);
  return out;
}

async function testBannerFollowsTheGuideCard() {
  const r = await bannerWatchRun();
  assert.strictEqual(r.barWithoutCard, true, 'no card on screen: the bar must show');
  assert.strictEqual(r.observersAfterFirstRender, 1, 'a first-run render should install exactly one observer');
  assert.strictEqual(r.barWithCard, false, 'the card appeared showing the licence step but the bar did not step aside');
  assert.strictEqual(r.barAfterCardGone, true, 'the card went away but the bar did not come back');
  assert.strictEqual(r.releasedOnActive, true, 'the observer was not released once the licence became active');
  console.log('PASS: the banner steps aside when the guide card appears, returns when it goes, and the watcher is released on an active licence');

  const src = SHELL_SRC;
  const noObserver = mutate(src, [[
    `    this._guideCardObserver = new MutationObserver(() => {`,
    `    this._guideCardObserver = { disconnect() {}, observe() {} }; void (() => {`,
  ]]);
  const msg1 = await provesMutation('the card-appears check survived a mutant that never watches the DOM', async () => {
    const m = await bannerWatchRun(noObserver);
    assert.strictEqual(m.barWithCard, false, 'the bar did not step aside when the card appeared');
  });
  const noRelease = mutate(src, [[
    `      if (this._guideCardObserver) { this._guideCardObserver.disconnect(); this._guideCardObserver = null; }`,
    `      /* MUTATION: never released */`,
  ]]);
  const msg2 = await provesMutation('the release check survived a mutant that never disconnects the watcher', async () => {
    const m = await bannerWatchRun(noRelease);
    assert.strictEqual(m.releasedOnActive, true, 'the observer was not released');
  });
  console.log('PASS: ' + msg1);
  console.log('PASS: ' + msg2);
}

(async () => {
  const tests = [
    testSetupGuideOwnsFirstRunForOwnersOnly,
    testSetupGuideGuardMutationsAreCaught,
    testBannerFollowsTheGuideCard,
    testBlockedStateRendersBanner,
    testActiveStateRendersNoBanner,
    testEveryDerivedBlockedStateRendersTheBanner,
    testActionInvokesExistingOpenLicensing,
    testBannerStringsHaveRealArabicTranslations,
    testHarnessDistinguishesPresenceFromAbsence,
    testMutationRemovingTheBannerRenderIsCaught,
    testMutationAlwaysRenderingIsCaught,
  ];
  let failed = 0;
  for (const fn of tests) {
    try {
      await fn();
    } catch (e) {
      failed++;
      console.log(`FAIL ${fn.name} -- ${e.message}`);
    }
  }
  console.log('');
  if (failed) {
    console.log(`${failed} of ${tests.length} checks failed`);
    process.exit(1);
  }
  console.log(`PASS retail_license_banner_test.js -- ${tests.length} checks`);
})();
