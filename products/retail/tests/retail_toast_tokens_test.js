/**
 * retail_toast_tokens_test.js — the JS-BUILT surfaces follow the theme.
 *
 * SCOPE GREW 2026-09-08. This file started as a guard on showToast() alone
 * (sections 1-4 below, and everything the header says about it still holds).
 * It now also covers the four sync-banner tiers, the admin-device claim bar
 * and the import wizard's injected stylesheet — every remaining surface in
 * the retail frontend that builds paint in JAVASCRIPT rather than in
 * css/main.css, which is precisely the blind spot named under SCOPE below.
 * They live together because the guard is identical (run the real function,
 * read the real string) and because keeping them apart is what let three of
 * them be missed when the toast was converted.
 *
 * WHY THIS TEST EXISTS
 * showToast() used to build its inline style from hardcoded hex: a success
 * green (#34d399), an error red (#f87171), a near-black background
 * (#1e1e2e), and plain `color:white`. The toast is the ONE surface a user
 * is guaranteed to see over every other screen in the product, so it was
 * the single most visible thing left ignoring the theme after the
 * 2026-09-08 palette re-grounding (DESIGN.md §4): a shop on Sand or Night
 * still got a fixed near-black snackbar with fixed accent colours, exactly
 * the "one dark island" failure mode retail_design_tokens_test.js's own
 * header comment describes for the stylesheet.
 *
 * THE FIX
 * Every literal is now a `var(--state-*-surface|-border|-text)` reference,
 * the same success/danger/info triad `.btn-mini.approve` / `.btn-mini.reject`
 * already use in css/main.css -- so success/error/info are correct AND
 * already contrast-proven (DESIGN.md §4.4) in all five themes, not just the
 * two shapes (light text on dark background) the old literals assumed.
 *
 * SCOPE
 * retail_design_tokens_test.js only scans css/main.css for stray colour
 * literals -- it has no visibility into inline styles built by JS, which is
 * exactly how this toast's literals survived every existing colour-literal
 * guard. This file closes that gap for showToast() specifically: it RUNS
 * the real function (via Node's vm module, not a reimplementation) for
 * every toast type and inspects the actual style string it builds.
 *
 * MUTATION-PROVED: the "no hex literal" assertion is re-run against a
 * deliberately mutated copy of app-shell.js that reintroduces exactly the
 * original bug (a literal #1e1e2e background), and is required to fail
 * there -- see testHexLiteralCheckIsMutationProved().
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_toast_tokens_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SHELL_FILE = path.join(__dirname, '..', 'frontend', 'app-shell.js');
const SHELL_SRC = fs.readFileSync(SHELL_FILE, 'utf8');

// ─────────────────────────────────────────────────────────────────────────────
// Minimal sandbox -- showToast() only ever touches document.createElement,
// the returned element's .style/.textContent, document.body.appendChild and
// setTimeout, so nothing heavier than retail_shell_chrome_test.js's stub is
// needed. The whole file is loaded (not just showToast in isolation) so this
// runs the REAL function, byte for byte, the same discipline every other
// standalone test in this directory follows.
// ─────────────────────────────────────────────────────────────────────────────

function makeElementStub() {
  return {
    style: {},
    textContent: '',
    innerHTML: '',
    title: '',
    appendChild() {},
    // The admin-device claim bar (section 6) builds its children as real
    // elements and attaches them with append(...) rather than innerHTML.
    append() {},
    addEventListener() {},
    remove() {},
  };
}

function loadShellForToast(shellSrc) {
  const appended = [];
  const sandbox = {
    console,
    t: (s) => s,
    setTimeout: () => 0,   // never actually fires -- nothing here waits 3s to see a toast removed
    clearTimeout: () => {},
    navigator: { userAgent: 'Mozilla/5.0 (test)' },
    location: { hash: '', href: 'http://localhost/' },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    // _maybeOfferAdminDeviceClaim() reads a session-scoped dismissal flag
    // before it renders anything (section 6).
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    document: {
      readyState: 'complete',
      body: { appendChild(el) { appended.push(el); } },
      head: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', setAttribute() {}, style: { setProperty() {} } },
      createElement: () => makeElementStub(),
      getElementById: () => null,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(shellSrc, sandbox, { filename: SHELL_FILE });
  assert.ok(sandbox.SubsystemApp, 'app-shell.js did not expose window.SubsystemApp');
  assert.strictEqual(typeof sandbox.SubsystemApp.showToast, 'function',
    'SubsystemApp.showToast is not a function -- the fixture is asserting about nothing');
  return { sandbox, App: sandbox.SubsystemApp, appended };
}

// One call, one captured toast element -- showToast() appends synchronously.
function fireToast(App, appended, msg, type) {
  const before = appended.length;
  if (type === undefined) App.showToast(msg);
  else App.showToast(msg, type);
  assert.strictEqual(appended.length, before + 1,
    `showToast(${JSON.stringify(msg)}, ${JSON.stringify(type)}) did not append exactly one element ` +
    `(before=${before}, after=${appended.length}) -- the fixture cannot see what it renders.`);
  return appended[appended.length - 1];
}

const HEX_LITERAL = /#[0-9a-fA-F]{3,8}\b/;

// ═════════════════════════════════════════════════════════════════════════════
// 1 — no hex literal in any toast type's rendered style, for real, per type
// ═════════════════════════════════════════════════════════════════════════════

const CASES = [
  { type: 'success', tokens: ['--state-success-surface', '--state-success-border', '--state-success-text'] },
  { type: 'error',    tokens: ['--state-danger-surface',  '--state-danger-border',  '--state-danger-text'] },
  { type: 'info',     tokens: ['--state-info-surface',    '--state-info-border',    '--state-info-text'] },
];

function testNoHexLiteralPerType() {
  const { App, appended } = loadShellForToast(SHELL_SRC);
  for (const { type } of CASES) {
    const toast = fireToast(App, appended, 'a message', type);
    const css = toast.style.cssText || '';
    assert.ok(css.length > 20, `showToast(..., '${type}') produced an implausibly short style string: ${JSON.stringify(css)}`);
    assert.ok(!HEX_LITERAL.test(css),
      `showToast(..., '${type}') still contains a hex colour literal: ${css}`);
    assert.ok(!/color:\s*white\b/i.test(css),
      `showToast(..., '${type}') still hardcodes color:white instead of a text token: ${css}`);
  }
  console.log('PASS: no hex colour literal or hardcoded white text in any toast type\'s rendered style');
}

// =============================================================================
// 1b - the toast is positioned LOGICALLY, so it mirrors with the shell
//
// retail_design_rtl_test.js ratchets physical properties in the STYLESHEETS.
// This style string is assembled in JavaScript, so that ratchet cannot see it
// at all -- and a `right:24px` duly appeared here during the token rewrite on
// 2026-09-08. The toast floats over every screen, and the whole shell mirrors
// in Arabic, so a physical corner is a real defect rather than a nitpick.
// This is the only guard that can see it.
// =============================================================================

const PHYSICAL_IN_INLINE_STYLE = /(?:^|;)\s*(left|right|margin-left|margin-right|padding-left|padding-right)\s*:/i;

function testToastIsPositionedLogically() {
  const { App, appended } = loadShellForToast(SHELL_SRC);
  for (const { type } of CASES) {
    const toast = fireToast(App, appended, 'a message', type);
    const css = toast.style.cssText || '';
    assert.ok(!PHYSICAL_IN_INLINE_STYLE.test(css),
      `showToast(..., '${type}') pins itself with a PHYSICAL property, so it lands in the wrong ` +
      `corner when the shell mirrors for Arabic. Use inset-inline-end. Got: ${css}`);
    assert.ok(/inset-inline-end\s*:/.test(css),
      `showToast(..., '${type}') should position its trailing edge with inset-inline-end. Got: ${css}`);
  }
  console.log('PASS: the toast positions itself logically, so it mirrors in Arabic');
}

// ═════════════════════════════════════════════════════════════════════════════
// 2 — every toast type resolves through its own surface/border/text token
// ═════════════════════════════════════════════════════════════════════════════

function testEveryTypeUsesItsOwnStateTokens() {
  const { App, appended } = loadShellForToast(SHELL_SRC);
  for (const { type, tokens } of CASES) {
    const toast = fireToast(App, appended, 'a message', type);
    const css = toast.style.cssText || '';
    for (const tok of tokens) {
      assert.ok(css.includes(`var(${tok})`),
        `showToast(..., '${type}') does not reference var(${tok}) -- it should resolve THROUGH the token, ` +
        `not just avoid a literal, so it moves with the palette. Got: ${css}`);
    }
  }
  console.log('PASS: success/error/info each resolve through their own --state-*-surface/-border/-text tokens');
}

// ═════════════════════════════════════════════════════════════════════════════
// 3 — an unknown/omitted type falls back to 'info', not to `undefined` styling
// ═════════════════════════════════════════════════════════════════════════════

function testUnknownOrOmittedTypeFallsBackToInfo() {
  const { App, appended } = loadShellForToast(SHELL_SRC);
  for (const type of [undefined, 'not-a-real-type']) {
    const toast = fireToast(App, appended, 'a message', type);
    const css = toast.style.cssText || '';
    assert.ok(css.includes('var(--state-info-surface)') && css.includes('var(--state-info-text)'),
      `showToast(..., ${JSON.stringify(type)}) did not fall back to the info tokens. Got: ${css}`);
    assert.ok(!HEX_LITERAL.test(css), `fallback toast for type ${JSON.stringify(type)} contains a hex literal: ${css}`);
  }
  console.log('PASS: an omitted or unrecognised type falls back to the info tokens, never to undefined styling');
}

// ═════════════════════════════════════════════════════════════════════════════
// 4 — MUTATION PROOF: reintroducing the original #1e1e2e background is caught
// ═════════════════════════════════════════════════════════════════════════════

function mutateBackgroundToOriginalHex(src) {
  const find = 'background:var(${tok.surface});';
  const hits = src.split(find).length - 1;
  assert.strictEqual(hits, 1,
    `Mutation anchor occurs ${hits} time(s), expected exactly 1: ${JSON.stringify(find)}\n` +
    'A mutation that no longer applies would let the proof below pass while proving nothing. Re-anchor it.');
  // Reintroduces exactly the original bug: a literal near-black background,
  // the one the intro of this file describes.
  return src.split(find).join('background:#1e1e2e;');
}

function testHexLiteralCheckIsMutationProved() {
  const mutated = mutateBackgroundToOriginalHex(SHELL_SRC);
  const { App, appended } = loadShellForToast(mutated);
  let threw = null;
  try {
    for (const { type } of CASES) {
      const toast = fireToast(App, appended, 'a message', type);
      const css = toast.style.cssText || '';
      assert.ok(!HEX_LITERAL.test(css), `showToast(..., '${type}') still contains a hex colour literal: ${css}`);
    }
  } catch (err) {
    threw = err;
  }
  assert.ok(
    threw,
    'MUTATION SURVIVED — the "no hex literal" check passed against a build with the original ' +
    '#1e1e2e background literal deliberately reintroduced, so it is not actually watching for it. ' +
    'Fix the check, not the mutation.'
  );
  console.log(`PASS: the "no hex literal" check is mutation-proved  [caught: ${String(threw.message).split('\n')[0].slice(0, 120)}]`);
}

// ═════════════════════════════════════════════════════════════════════════════
// 5 — the sync banner's four tiers, held to the same two rules as the toast
//
// showToast() was converted on 2026-09-08 and _renderSyncCalmState had already
// been moved onto --state-success-*; the OTHER THREE tiers were simply not
// done, and kept building `background:#1e1e2e; ... color:white` with #60a5fa /
// #ef4444 / #fbbf24 accents -- DESIGN.md §2's rejected "near-black HUD with
// neon accents" register, and §4.4's two explicit nevers (a colour literal
// outside the token block; #FFFFFF text in a dark theme). The banner is
// second only to the toast in visibility: it is full-width and pinned to the
// top of every screen.
//
// Nothing could see it. retail_design_tokens_test.js scans css/main.css only
// and passed reporting "no stray colour literals"; the two offline-banner
// suites assert on the banner's WORDS and (until this change) on one exact
// hex. This section runs each real tier function against a real element and
// reads the style string it actually builds -- the same discipline sections 1
// and 2 apply to the toast.
//
// The calm tier is here for the OTHER rule as well: it pinned itself with a
// physical `right:14px`, so in Arabic the toast moved to the bottom-left and
// the pill stayed bottom-right, 186 lines apart in the same file.
// ═════════════════════════════════════════════════════════════════════════════

/** A realistic /sync/health payload; each tier is called directly, so the
 *  routing thresholds do not matter here (retail_offline_banner_*_test.js own
 *  the tier SELECTION -- this file owns what each tier PAINTS). */
function healthFixture() {
  const iso = new Date(Date.now() - 3 * 3600 * 1000).toISOString();
  return {
    configured: true, running: true, healthy: false,
    pending_count: 11, never_synced: false, seconds_since_last_success: 3 * 3600,
    push: { last_success_at: iso, last_failure_at: null, last_failure_reason: null, consecutive_failures: 0 },
    pull: { last_success_at: iso, last_failure_at: null, last_failure_reason: null, consecutive_failures: 0 },
  };
}

const SYNC_TIERS = [
  {
    name: '_renderSyncBehindState',
    tokens: ['--state-info-surface', '--state-info-border', '--state-info-text'],
    call: (App) => App._renderSyncBehindState(healthFixture(), false),
  },
  {
    name: '_renderSyncBehindWarningState',
    tokens: ['--state-danger-surface', '--state-danger-border', '--state-danger-text'],
    call: (App) => App._renderSyncBehindWarningState(healthFixture(), false),
  },
  {
    name: '_renderSyncAlarmState',
    tokens: ['--state-warning-surface', '--state-warning-border', '--state-warning-text'],
    call: (App) => App._renderSyncAlarmState(healthFixture(), true, true, false),
  },
  {
    name: '_renderSyncCalmState',
    tokens: ['--state-success-surface', '--state-success-border', '--state-success-text'],
    call: (App) => App._renderSyncCalmState(healthFixture(), false),
  },
];

/** Run one tier against a fresh element and hand back what it painted. */
function paintSyncTier(shellSrc, tier) {
  const { App } = loadShellForToast(shellSrc);
  const el = makeElementStub();
  App._syncBannerEl = el;
  tier.call(App);
  const css = el.style.cssText || '';
  assert.ok(css.length > 40,
    `${tier.name} produced an implausibly short style string: ${JSON.stringify(css)} ` +
    '-- the fixture cannot see what it renders.');
  return { css, html: el.innerHTML || '' };
}

function testSyncBannerTiersCarryNoColourLiteral() {
  for (const tier of SYNC_TIERS) {
    const { css, html } = paintSyncTier(SHELL_SRC, tier);
    // Both halves: the tier sets the ground/border on style.cssText and the
    // accent colour on an inline style INSIDE innerHTML, and the original
    // defect lived in both.
    for (const [what, text] of [['style.cssText', css], ['innerHTML', html]]) {
      assert.ok(!HEX_LITERAL.test(text),
        `${tier.name}'s ${what} still contains a hex colour literal: ${text}`);
      assert.ok(!/color:\s*white\b/i.test(text),
        `${tier.name}'s ${what} still hardcodes color:white instead of a text token: ${text}`);
    }
  }
  console.log('PASS: none of the four sync-banner tiers paints from a hex literal or a hardcoded white');
}

function testEverySyncTierUsesItsOwnStateTokens() {
  for (const tier of SYNC_TIERS) {
    const { css } = paintSyncTier(SHELL_SRC, tier);
    for (const tok of tier.tokens) {
      assert.ok(css.includes(`var(${tok})`),
        `${tier.name} does not reference var(${tok}) -- each tier should resolve THROUGH its own ` +
        `state family so the four stay tellable apart in all five themes. Got: ${css}`);
    }
  }
  console.log('PASS: behind/24h-warning/alarm/calm each resolve through their own --state-* family');
}

function testSyncBannerTiersArePositionedLogically() {
  for (const tier of SYNC_TIERS) {
    const { css } = paintSyncTier(SHELL_SRC, tier);
    assert.ok(!PHYSICAL_IN_INLINE_STYLE.test(css),
      `${tier.name} pins itself with a PHYSICAL property, so it lands in the wrong place when ` +
      `the shell mirrors for Arabic. Use inset-inline / inset-inline-end. Got: ${css}`);
  }
  // The calm pill is the one tier with a LEADING/TRAILING edge rather than a
  // full-width span, so it is the one that must name a logical inset -- the
  // same assertion section 1b makes of the toast, now covering both floating
  // elements in the shell.
  const calm = paintSyncTier(SHELL_SRC, SYNC_TIERS[3]);
  assert.ok(/inset-inline-end\s*:/.test(calm.css),
    'The ambient sync pill should pin its trailing edge with inset-inline-end, exactly as the ' +
    `toast does. Got: ${calm.css}`);
  console.log('PASS: all four sync-banner tiers position logically, and the calm pill uses inset-inline-end');
}

// ═════════════════════════════════════════════════════════════════════════════
// 6 — the admin-device claim bar, the fourth site on the old HUD palette
//
// Not a sync tier, but the same defect in the same file: a fixed #1e1e2e bar
// with #e8e8f0 text and a #f43f5e button, floating over whichever theme the
// shop actually chose. Included here rather than in its own file because the
// guard is identical and this is the only harness that runs app-shell.js's
// real render functions.
// ═════════════════════════════════════════════════════════════════════════════

function testAdminDeviceClaimBarCarriesNoColourLiteral() {
  const { App, appended } = loadShellForToast(SHELL_SRC);
  App.isAdminDevice = false;
  App.canClaimAdminDevice = true;
  const before = appended.length;
  App._maybeOfferAdminDeviceClaim();
  assert.ok(appended.length > before,
    '_maybeOfferAdminDeviceClaim() appended nothing with a claim genuinely available -- ' +
    'the fixture is asserting about nothing.');
  const painted = appended.slice(before).map((el) => el.style.cssText || '').filter(Boolean);
  assert.ok(painted.length >= 1,
    '_maybeOfferAdminDeviceClaim() produced no inline style at all; the fixture cannot see what it renders.');
  for (const css of painted) {
    assert.ok(!HEX_LITERAL.test(css),
      `The admin-device claim bar still contains a hex colour literal: ${css}`);
  }
  assert.ok(painted.some((css) => css.includes('var(--state-danger-surface)')),
    'The admin-device claim bar should resolve its ground through the danger state family -- ' +
    'it exists because the install is in a state that hides Settings and the Audit Log. ' +
    `Got: ${JSON.stringify(painted)}`);
  console.log('PASS: the admin-device claim bar paints from tokens, not from the old HUD literals');
}

// ═════════════════════════════════════════════════════════════════════════════
// 7 — MUTATION PROOF for the banner check: the original #1e1e2e ground again
// ═════════════════════════════════════════════════════════════════════════════

function mutateBannerGroundToOriginalHex(src) {
  const find = "'position:fixed;top:0;inset-inline:0;background:var(--state-info-surface);'";
  const hits = src.split(find).length - 1;
  assert.strictEqual(hits, 1,
    `Mutation anchor occurs ${hits} time(s), expected exactly 1: ${JSON.stringify(find)}\n` +
    'A mutation that no longer applies would let the proof below pass while proving nothing. Re-anchor it.');
  // Reintroduces exactly the original bug on the "behind" tier.
  return src.split(find).join("'position:fixed;top:0;left:0;right:0;background:#1e1e2e;'");
}

function testSyncBannerChecksAreMutationProved() {
  const mutated = mutateBannerGroundToOriginalHex(SHELL_SRC);
  const tier = SYNC_TIERS[0];
  const caught = [];
  for (const [what, check] of [
    ['no hex literal', (css) => assert.ok(!HEX_LITERAL.test(css), `hex literal: ${css}`)],
    ['no physical inset', (css) => assert.ok(!PHYSICAL_IN_INLINE_STYLE.test(css), `physical inset: ${css}`)],
  ]) {
    try {
      check(paintSyncTier(mutated, tier).css);
    } catch (err) {
      caught.push(`${what} [${String(err.message).split('\n')[0].slice(0, 80)}]`);
    }
  }
  assert.strictEqual(caught.length, 2,
    'MUTATION SURVIVED — reintroducing the original `left:0;right:0;background:#1e1e2e` on the ' +
    '"behind" tier was NOT caught by both checks (caught: ' + JSON.stringify(caught) + '). ' +
    'Fix the checks, not the mutation.');
  console.log(`PASS: the sync-banner checks are mutation-proved  [caught: ${caught.join('; ')}]`);
}

// ═════════════════════════════════════════════════════════════════════════════
// 8 — the import wizard's injected stylesheet
//
// import-wizard.js's _injectStyles() shipped a COMPLETE SECOND DESIGN SYSTEM
// into a page index.html has already styled: roughly 160 paint literals, a
// near-black slate modal on a near-opaque slate scrim, pure-white headings and
// the aurora teal DESIGN.md §4.3 retired on 2026-09-08. Opening Import from
// Products, Customers or Suppliers dropped that dark island over a light-theme
// till in all five themes, and retail_design_tokens_test.js -- which scans
// css/main.css only -- reported PASS the whole time.
//
// Guarded here rather than in its own file because the shape is identical to
// the toast's and the sync banner's: run the REAL function, read the REAL
// string it builds. The wizard has no DOM of its own to render, so this reads
// the <style> element's textContent directly.
// ═════════════════════════════════════════════════════════════════════════════

const WIZARD_FILE = path.join(__dirname, '..', 'frontend', 'import-wizard.js');
const WIZARD_SRC = fs.readFileSync(WIZARD_FILE, 'utf8');

function injectWizardStyles(wizardSrc) {
  const created = [];
  const sandbox = {
    console,
    document: {
      getElementById: () => null,       // "already injected?" -- always no, so it runs
      createElement: () => { const el = makeElementStub(); created.push(el); return el; },
      head: { appendChild() {} },
      body: { appendChild() {} },
      addEventListener() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(wizardSrc, sandbox, { filename: WIZARD_FILE });
  assert.ok(sandbox.ImportWizard, 'import-wizard.js did not expose window.ImportWizard');
  sandbox.ImportWizard._injectStyles();
  assert.strictEqual(created.length, 1,
    `_injectStyles() created ${created.length} element(s), expected exactly 1 <style> -- ` +
    'the fixture cannot see what it injects.');
  const css = created[0].textContent || '';
  assert.ok(css.length > 2000,
    `_injectStyles() produced only ${css.length} characters of CSS. This wizard carries the ` +
    'whole modal, both banners, the mapping grid and four confidence chips, so a short ' +
    'string means the scan below is looking at almost nothing.');
  return css;
}

function testWizardStylesheetCarriesNoColourLiteral() {
  const css = injectWizardStyles(WIZARD_SRC);
  const hits = css.match(/#[0-9a-fA-F]{3,8}\b/g) || [];
  assert.deepStrictEqual(hits, [],
    `The import wizard's injected stylesheet still contains ${hits.length} hex colour ` +
    `literal(s): ${JSON.stringify([...new Set(hits)])}\n\n` +
    'DESIGN.md §4.4: never a colour literal outside the token block. Use the --surface-*, ' +
    '--text-*, --border-* and --state-* tokens, so the wizard follows whichever of the five ' +
    'themes the shop actually chose instead of being a dark island inside a light till.');
  // rgba() tints are the same defect wearing a different notation -- the old
  // rgba(255,255,255,0.05) inputs and rgba(20,184,166,0.08) accent grounds were
  // exactly as theme-blind as the hexes. The one sanctioned exception is a
  // NEUTRAL BLACK shadow, which is what --elevation-* itself is built from and
  // which showToast() also keeps.
  const tints = (css.match(/rgba?\([^)]*\)/g) || []).filter((v) => !/rgba?\(\s*0\s*,\s*0\s*,\s*0\s*[,)]/.test(v));
  assert.deepStrictEqual(tints, [],
    `The import wizard's injected stylesheet still contains ${tints.length} rgb/rgba colour ` +
    `literal(s): ${JSON.stringify([...new Set(tints)])} -- as theme-blind as a hex.`);
  console.log(`PASS: the import wizard's injected stylesheet (${css.length} chars) paints entirely from tokens`);
}

function testWizardStylesheetIsPositionedLogically() {
  const css = injectWizardStyles(WIZARD_SRC);
  const hits = (css.match(/(?:^|[\s;{])(margin|padding|border)-(left|right)\s*:/gm) || [])
    .map((s) => s.trim());
  assert.deepStrictEqual(hits, [],
    `The import wizard's stylesheet uses ${hits.length} physical direction propert(ies): ` +
    `${JSON.stringify(hits)}. This product ships Arabic and the wizard mirrors with the shell; ` +
    'use the margin-inline-* / padding-inline-* / border-inline-* equivalents.');
  console.log('PASS: the import wizard\'s stylesheet uses only logical direction properties');
}

// ═════════════════════════════════════════════════════════════════════════════
// 9 — MUTATION PROOF for the wizard check: the original #0f172a modal again
// ═════════════════════════════════════════════════════════════════════════════

function testWizardStylesheetCheckIsMutationProved() {
  const find = '.iw-modal { background:var(--surface-panel);';
  const hits = WIZARD_SRC.split(find).length - 1;
  assert.strictEqual(hits, 1,
    `Mutation anchor occurs ${hits} time(s), expected exactly 1: ${JSON.stringify(find)}\n` +
    'A mutation that no longer applies would let the proof below pass while proving nothing.');
  // Reintroduces exactly the original bug: the dark-island modal ground.
  const mutated = WIZARD_SRC.split(find).join('.iw-modal { background:#0f172a;');
  let threw = null;
  try {
    const css = injectWizardStyles(mutated);
    assert.deepStrictEqual(css.match(/#[0-9a-fA-F]{3,8}\b/g) || [], [], 'hex literal present');
  } catch (err) {
    threw = err;
  }
  assert.ok(threw,
    'MUTATION SURVIVED — the wizard stylesheet check passed against a build with the original ' +
    '#0f172a modal background deliberately reintroduced, so it is not actually watching for it.');
  console.log(`PASS: the wizard stylesheet check is mutation-proved  [caught: ${String(threw.message).split('\n')[0].slice(0, 100)}]`);
}

// ─────────────────────────────────────────────────────────────────────────────

const RUN = [
  testNoHexLiteralPerType,
  testToastIsPositionedLogically,
  testEveryTypeUsesItsOwnStateTokens,
  testUnknownOrOmittedTypeFallsBackToInfo,
  testHexLiteralCheckIsMutationProved,
  testSyncBannerTiersCarryNoColourLiteral,
  testEverySyncTierUsesItsOwnStateTokens,
  testSyncBannerTiersArePositionedLogically,
  testAdminDeviceClaimBarCarriesNoColourLiteral,
  testSyncBannerChecksAreMutationProved,
  testWizardStylesheetCarriesNoColourLiteral,
  testWizardStylesheetIsPositionedLogically,
  testWizardStylesheetCheckIsMutationProved,
];

let failed = 0;
for (const fn of RUN) {
  try {
    fn();
  } catch (err) {
    failed += 1;
    console.error('FAIL: ' + fn.name);
    console.error('  ' + (err && err.message ? err.message : err));
  }
}
if (failed) {
  console.error(`FAIL: retail_toast_tokens_test.js — ${failed} of ${RUN.length} case(s) failed`);
  process.exitCode = 1;
} else {
  console.log(`PASS: retail_toast_tokens_test.js — ${RUN.length} case(s)`);
}
