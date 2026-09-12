/**
 * retail_icons_test.js -- the icon-signature redesign (icons.js).
 *
 * WHY THIS FILE EXISTS
 *
 * The product owner's complaint was concrete: `icons.js` was an unmodified
 * Lucide port -- the single most common open-source icon set, uniform
 * stroke-width, no accent, no motion, no state, no RTL handling. That is
 * "the clichés" by definition. The fix is not a second icon library and not
 * 50+ hand-drawn SVG paths (nobody in this pipeline can render one to check,
 * so blind authoring would ship something worse than what it replaced) --
 * it is a deliberate, testable TREATMENT layered on the same geometry: two
 * stroke weights, one filled accent per icon at most, six real state icons,
 * two bounded animations, and one RTL mirror rule. Every one of those is a
 * structural claim about the rendered `<svg>` markup, not a look-and-feel
 * judgement, so this file asserts the markup directly rather than eyeballing
 * a screenshot.
 *
 * MUTATION-PROVEN (see testEveryGuardIsProvenByBreakingIt): reverting the
 * heavy stroke weight to match the light one, dropping the reduced-motion
 * coverage for the two icon animation classes, marking a non-directional
 * icon as RTL-mirrored, and making render() draw nothing at all are all
 * proven to turn the corresponding check red. A mutation whose anchor no
 * longer matches the live source fails LOUDLY (see mutate() below) rather
 * than silently proving nothing -- the same discipline
 * retail_exceptions_screen_test.js already established in this suite.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone with only Node
 * built-ins:
 *
 *   node products/retail/tests/retail_icons_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const ICONS_FILE = path.join(FRONTEND_DIR, 'icons.js');
const MAIN_CSS_FILE = path.join(FRONTEND_DIR, 'css', 'main.css');
const RTL_CSS_FILE = path.join(FRONTEND_DIR, 'css', 'rtl.css');
const CASH_DRAWER_FILE = path.join(FRONTEND_DIR, 'cash-drawer.js');
const RETAIL_FILE = path.join(FRONTEND_DIR, 'subsystem-retail.js');

const ICONS_SRC = fs.readFileSync(ICONS_FILE, 'utf8');
const MAIN_CSS_SRC = fs.readFileSync(MAIN_CSS_FILE, 'utf8');
const RTL_CSS_SRC = fs.readFileSync(RTL_CSS_FILE, 'utf8');
const CASH_DRAWER_SRC = fs.readFileSync(CASH_DRAWER_FILE, 'utf8');
const RETAIL_SRC = fs.readFileSync(RETAIL_FILE, 'utf8');

/** Load icons.js (or a mutated copy of its source) into a fresh sandbox and
 *  return window.AuraIcons. When `warnLog` is passed, console.warn calls are
 *  captured into it (as joined-argument strings) instead of reaching the
 *  real console -- log/error still pass through so a genuine crash is still
 *  visible. */
function loadIcons(src, warnLog) {
  const sandbox = {
    window: {},
    console: warnLog
      ? { log: console.log, error: console.error, warn: (...args) => warnLog.push(args.map(String).join(' ')) }
      : console,
  };
  vm.createContext(sandbox);
  vm.runInContext(src || ICONS_SRC, sandbox, { filename: ICONS_FILE });
  assert.ok(sandbox.window.AuraIcons, 'icons.js did not expose window.AuraIcons');
  return sandbox.window.AuraIcons;
}

/** Every self-closing element tag inside a rendered <svg>...</svg> string. */
function elementsOf(svgMarkup) {
  return svgMarkup.match(/<(?:path|circle|rect|line|polyline)\b[^>]*\/>/g) || [];
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION HARNESS -- same shape as retail_exceptions_screen_test.js's own,
// so a mutation whose anchor text has drifted out of sync with the real
// file fails with an explicit "re-anchor it" rather than silently applying
// to zero occurrences and proving nothing.
// ─────────────────────────────────────────────────────────────────────────────

function mutate(src, find, replace) {
  const hits = src.split(find).length - 1;
  assert.strictEqual(
    hits, 1,
    `Mutation anchor occurs ${hits} time(s), expected exactly 1:\n  ${JSON.stringify(find)}\n\n` +
    'A mutation that no longer applies would let the proof below pass while proving nothing. ' +
    'Re-anchor it.'
  );
  return src.replace(find, replace);
}

async function provesMutation(what, src, find, replace, check) {
  const broken = mutate(src, find, replace);
  let threw = null;
  try {
    await check(broken);
  } catch (err) {
    threw = err;
  }
  assert.ok(
    threw,
    `MUTATION SURVIVED -- ${what}\n` +
    'The guard for this passed against a build with the behaviour deliberately broken, so it ' +
    'is not actually watching it. Fix the check, not the mutation.'
  );
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 100)}]`;
}

// ─────────────────────────────────────────────────────────────────────────────
// CHECKS -- each takes the icons.js SOURCE TEXT (not the loaded module) so
// the mutation proofs below can run the identical check against a broken
// copy without duplicating the assertions.
// ─────────────────────────────────────────────────────────────────────────────

/** (1) Every icon still renders for every name and emoji the app passes
 *  today -- enumerated from the live map, not a hand-written list, so a
 *  future icon addition/removal is covered automatically. */
function checkEveryNameAndEmojiRenders(src) {
  const A = loadIcons(src);
  const names = Object.keys(A.ICONS);
  const emoji = Object.keys(A.EMOJI);
  assert.ok(names.length >= 50, `Expected at least the original 50 icon names, found ${names.length}.`);
  assert.ok(emoji.length >= 44, `Expected at least the original 44 emoji mappings, found ${emoji.length}.`);

  const blankNames = names.filter((n) => !/^<svg[\s\S]*<\/svg>$/.test(A.render(n, 18)));
  assert.deepStrictEqual(blankNames, [],
    `${blankNames.length} icon name(s) render blank/malformed instead of an <svg>: ${blankNames.join(', ')}`);

  const blankEmoji = emoji.filter((e) => !/^<svg[\s\S]*<\/svg>$/.test(A.render(e, 18)));
  assert.deepStrictEqual(blankEmoji, [],
    `${blankEmoji.length} emoji value(s) render blank/malformed instead of an <svg>: ${JSON.stringify(blankEmoji)}`);
}

/** (2) The two-weight treatment is actually applied -- structural, not a
 *  string-eyeball: every icon's rendered markup must contain the literal
 *  HEAVY stroke-width, the <svg> root must carry the literal LIGHT
 *  stroke-width, and any icon with more than one non-accented element must
 *  show at least one element WITHOUT the heavy weight (real contrast, not
 *  every element bumped). */
function checkTwoWeightTreatment(src) {
  const A = loadIcons(src);
  const names = Object.keys(A.ICONS);

  const missingHeavy = [];
  const missingLightRoot = [];
  const noContrast = [];

  for (const name of names) {
    const markup = A.render(name, 18);
    const rootTag = (markup.match(/^<svg[^>]*>/) || [''])[0];

    if (!/stroke-width="2\.25"/.test(markup)) missingHeavy.push(name);
    if (!/stroke-width="1\.5"/.test(rootTag)) missingLightRoot.push(name);

    const icon = A.ICONS[name];
    const elCount = icon.el.length;
    const accentCount = (icon.accent || []).length;
    const heavyMarks = elementsOf(markup).filter((el) => /stroke-width="2\.25"/.test(el)).length;
    // A genuine contrast claim only applies where a non-accented element
    // remains to be light: an icon whose every element is either heavy or
    // filled (accent) has nothing left to compare against, and that is
    // itself a legitimate shape (shopping-cart: body heavy, both wheels
    // filled, nothing else).
    if (elCount - accentCount >= 2 && heavyMarks >= elCount - accentCount) {
      noContrast.push(name);
    }
  }

  assert.deepStrictEqual(missingHeavy, [],
    `${missingHeavy.length} icon(s) never render the heavy stroke-width (2.25) anywhere: ` +
    `${missingHeavy.join(', ')}`);
  assert.deepStrictEqual(missingLightRoot, [],
    `${missingLightRoot.length} icon(s)' <svg> root does not carry the light stroke-width ` +
    `(1.5) baseline: ${missingLightRoot.join(', ')}`);
  assert.deepStrictEqual(noContrast, [],
    `${noContrast.length} icon(s) have 2+ non-accented elements but EVERY one of them is heavy ` +
    `-- no light/heavy contrast survives: ${noContrast.join(', ')}`);
}

/** (3) 2026-09-08: an unresolved name/emoji no longer degrades to the raw
 *  input (the pre-2026-09-08 behaviour that shipped 💾/📧/☰ as literal
 *  glyphs across all eleven sections without anyone noticing, because an
 *  unmapped icon looked exactly as "fine" as a mapped one). It must instead
 *  render the neutral 'circle-help' placeholder AND console.warn the
 *  unresolved value BY NAME -- never the raw character, in the markup or
 *  left silent. `null` is unchanged: it is an explicit "no icon" sentinel
 *  many callers pass on purpose, not an unmapped value. */
function checkUnknownValueRendersPlaceholderAndWarns(src) {
  const warnLog = [];
  const A = loadIcons(src, warnLog);

  const unknownName = A.render('totally-unknown-icon-xyz', 18);
  assert.ok(/^<svg[\s\S]*<\/svg>$/.test(unknownName),
    'An unresolved icon name must still render a real <svg> placeholder, not go blank.');
  assert.ok(!unknownName.includes('totally-unknown-icon-xyz'),
    'The raw unresolved NAME leaked into the rendered markup -- it must render the neutral placeholder instead.');
  assert.ok(warnLog.some((line) => line.includes('totally-unknown-icon-xyz')),
    'render() did not console.warn the unresolved name.');

  warnLog.length = 0;
  const unknownEmoji = A.render('🦄', 18);
  assert.ok(/^<svg[\s\S]*<\/svg>$/.test(unknownEmoji),
    'An unmapped emoji must still render a real <svg> placeholder, not the raw glyph.');
  assert.ok(!unknownEmoji.includes('🦄'),
    'The raw unmapped EMOJI leaked into the rendered markup -- it must render the neutral placeholder instead.');
  assert.ok(warnLog.some((line) => line.includes('🦄')),
    'render() did not console.warn the unmapped emoji.');

  // Both unresolved inputs above render the exact same placeholder shape --
  // the fallback does not try to be clever per-input, just loud.
  assert.strictEqual(unknownName, unknownEmoji,
    'Two different unresolved inputs rendered different placeholder markup.');

  assert.strictEqual(A.render(null, 18), '', 'A null value should still render as the empty string.');
}

/** (4) Directional icons mirror under RTL; non-directional ones do not --
 *  both the icons.js flag AND the rtl.css rule that reads it. */
function checkDirectionalMirroring(src) {
  const A = loadIcons(src);
  const names = Object.keys(A.ICONS);
  const mirrored = names.filter((n) => /data-aura-dir="mirror"/.test(A.render(n, 18)));

  assert.deepStrictEqual(mirrored, ['undo-2'],
    `Expected exactly ['undo-2'] to carry the RTL mirror flag; got ${JSON.stringify(mirrored)}. ` +
    'A directional icon (return/back/next) must mirror in Arabic; a non-directional one ' +
    '(a clock, a target, a padlock, a trend arrow that tracks a number) must not.');

  // The CSS side: rtl.css must actually read that attribute and mirror it,
  // scoped to body.rtl so LTR is never touched.
  assert.ok(
    /body\.rtl\s+\.aura-ic\[data-aura-dir="mirror"\]\s*\{[^}]*transform:\s*scaleX\(-1\)/.test(RTL_CSS_SRC),
    'css/rtl.css has no body.rtl rule mirroring [data-aura-dir="mirror"] with transform:scaleX(-1).'
  );
  // And it must be scoped under body.rtl, not applied globally -- an
  // unscoped rule would mirror undo-2 in English too.
  assert.ok(!/(?<!body\.rtl\s)\.aura-ic\[data-aura-dir="mirror"\]\s*\{[^}]*scaleX\(-1\)/.test(
    RTL_CSS_SRC.replace(/body\.rtl\s+\.aura-ic\[data-aura-dir="mirror"\]/, '')),
    'The mirror rule must not also exist unscoped (outside body.rtl).');
}

/** (5) Every animation is disabled under prefers-reduced-motion: reduce --
 *  both that the classes exist and do something, and that main.css's
 *  reduced-motion block actually covers both of them. */
function checkReducedMotionDisablesAnimation(mainCssSrc) {
  const A = loadIcons(ICONS_SRC); // icons.js itself is unaffected by a main.css mutation
  const withPop = A.render('circle-check-big', 18, { animate: 'pop' });
  const withFlip = A.render('triangle-alert', 18, { animate: 'flip' });
  const withNeither = A.render('triangle-alert', 18);

  assert.ok(/class="aura-ic aura-ic-pop"/.test(withPop), 'opts.animate:"pop" did not add the aura-ic-pop class.');
  assert.ok(/class="aura-ic aura-ic-flip"/.test(withFlip), 'opts.animate:"flip" did not add the aura-ic-flip class.');
  assert.ok(!/aura-ic-(pop|flip)/.test(withNeither), 'A render with no opts.animate must carry neither motion class.');

  const media = mainCssSrc.match(/@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?\n\}/g) || [];
  assert.ok(media.length >= 1, 'css/main.css has no @media (prefers-reduced-motion: reduce) block at all.');
  // Word-boundary-safe: a class RENAMED to e.g. .aura-ic-flip-mutated-out
  // must NOT still satisfy a plain substring test for ".aura-ic-flip".
  const covering = media.filter((block) => /\.aura-ic-pop(?![\w-])/.test(block) && /\.aura-ic-flip(?![\w-])/.test(block)
    && /animation-duration:\s*0\.01ms\s*!important/.test(block));
  assert.ok(covering.length >= 1,
    'No prefers-reduced-motion block disables BOTH .aura-ic-pop and .aura-ic-flip via ' +
    'animation-duration: 0.01ms !important -- the same technique this file already uses for ' +
    '.money/.btn-primary/etc.');
}

/** (6) A state-reflecting icon renders differently for its different
 *  states -- checked at the icons.js level (distinct markup per state icon
 *  in a real state PAIR) and, for the strongest proof, through the ACTUAL
 *  cash-drawer.js integration: the exact same _renderBar() call produces
 *  different icon markup for 'open' vs 'none', and the aura-ic-flip class
 *  appears only on the paint where the state actually changed. */
function checkStateReflectingIcons(src) {
  const A = loadIcons(src);

  // icons.js level: each real state PAIR this pass wired up must be
  // genuinely different markup, not the same icon relabelled.
  const pairs = [['lock', 'lock-open'], ['circle-check-big', 'triangle-alert'], ['timer', 'triangle-alert']];
  for (const [a, b] of pairs) {
    assert.notStrictEqual(A.render(a, 18), A.render(b, 18),
      `State pair '${a}' / '${b}' render identical markup -- they cannot carry a state visually.`);
  }

  // The real integration: cash-drawer.js's _renderBar(), with icons.js
  // ACTUALLY loaded this time (unlike retail_drawer_screen_test.js, which
  // deliberately loads cash-drawer.js standalone to prove the fallback
  // path -- see that file's own harness). subsystem-retail.js is loaded
  // too, in the same order index.html uses, because CashDrawer delegates
  // its money formatting and bidi isolation to RetailSystem -- same
  // reasoning retail_drawer_screen_test.js's own loadDrawer() gives.
  const els = Object.create(null);
  const getEl = (id) => (els[id] || (els[id] = { style: {}, appendChild() {}, remove() {}, className: '', innerHTML: '' }));
  const sandbox = {
    console,
    t: (s) => s,
    fetch: () => Promise.reject(new Error('no network in this test')),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (test)' },
    localStorage: { getItem: () => null, setItem: () => {} },
    document: {
      getElementById(id) {
        if (id === 'cd-styles' || id === 'ret-styles') return null;
        return getEl(id);
      },
      createElement() { return { style: {}, appendChild() {}, remove() {}, classList: { add() {}, remove() {} } }; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      body: { appendChild() {}, classList: { toggle() {} } },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
    SubsystemApp: { active: 'retail', showToast() {}, hasCapability: () => true, _navigate() {} },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(RETAIL_SRC, sandbox, { filename: RETAIL_FILE });
  vm.runInContext(ICONS_SRC, sandbox, { filename: ICONS_FILE });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem.');
  assert.ok(sandbox.AuraIcons, 'icons.js did not expose AuraIcons in the combined sandbox.');
  vm.runInContext(CASH_DRAWER_SRC, sandbox, { filename: CASH_DRAWER_FILE });
  assert.ok(sandbox.CashDrawer, 'cash-drawer.js did not expose window.CashDrawer.');
  sandbox.RetailSystem._currencySymbol = '$';
  sandbox.RetailSystem._currencyDecimals = 2;

  const bar = getEl('cash-drawer-bar');
  sandbox.CashDrawer._session = { opened_at: '2026-08-26 09:00:00', opening_float: 100, terminal_id: 't1' };
  sandbox.CashDrawer._state = 'open';
  sandbox.CashDrawer._renderBar();
  const openHtml = bar.innerHTML;

  sandbox.CashDrawer._state = 'none';
  sandbox.CashDrawer._otherTerminalsOpen = 0;
  sandbox.CashDrawer._renderBar();
  const noneHtml = bar.innerHTML;

  assert.notStrictEqual(openHtml, noneHtml, 'The drawer bar rendered identical markup for open vs none.');
  assert.ok(/M7 11V7a5 5 0 0 1 9\.9-1/.test(openHtml),
    'The open drawer bar does not contain lock-open\'s distinguishing shackle path.');
  assert.ok(/M7 11V7a5 5 0 0 1 10 0v4/.test(noneHtml),
    'The "no drawer" bar does not contain lock\'s distinguishing shackle path.');
  assert.ok(/aura-ic-flip/.test(noneHtml),
    'The FIRST paint after a real open -> none transition should carry aura-ic-flip; it did not.');

  // Same state again: a second 'none' paint must NOT re-flip -- this is the
  // literal claim "animates on the state CHANGE, not on an idle repaint".
  sandbox.CashDrawer._renderBar();
  const noneHtmlAgain = bar.innerHTML;
  assert.ok(!/aura-ic-flip/.test(noneHtmlAgain),
    'A same-state repaint (none -> none) re-triggered aura-ic-flip -- it should only fire on a ' +
    'real transition, per icons.js\'s own MOTION contract.');
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION PROOFS
// ─────────────────────────────────────────────────────────────────────────────

async function testEveryGuardIsProvenByBreakingIt() {
  const proved = [];

  proved.push(await provesMutation(
    'M1. revert to uniform stroke weight',
    ICONS_SRC,
    '  var HEAVY_WEIGHT = 2.25;',
    '  var HEAVY_WEIGHT = 1.5; // MUTATED: heavy == light, no contrast',
    async (broken) => checkTwoWeightTreatment(broken)));

  proved.push(await provesMutation(
    'M2. drop the reduced-motion guard for the icon animation classes',
    MAIN_CSS_SRC,
    // Re-anchored 2026-09-08: the reduced-motion selector list GREW (the
    // sidebar nav icons' hover/active motion was added to it), so this line
    // no longer ends the list and its old `{` anchor matched zero times.
    // The harness caught that itself and refused to run a proof that proves
    // nothing -- which is the whole reason it counts its hits. The guard is
    // unchanged: strike the icon classes out of the block and the
    // reduced-motion check must fail.
    '  .aura-ic-pop, .aura-ic-flip,',
    '  .aura-ic-pop-MUTATED-OUT, .aura-ic-flip-MUTATED-OUT,',
    async (broken) => checkReducedMotionDisablesAnimation(broken)));

  proved.push(await provesMutation(
    'M3. mirror a non-directional icon (timer) under RTL',
    ICONS_SRC,
    '  \'timer\': {  // the hand -- the moving, meaningful part; the knob and face stay light\n' +
    '    el: [\n' +
    '      "<line x1=\\"10\\" x2=\\"14\\" y1=\\"2\\" y2=\\"2\\" />",\n' +
    '      "<line x1=\\"12\\" x2=\\"15\\" y1=\\"14\\" y2=\\"11\\" />",\n' +
    '      "<circle cx=\\"12\\" cy=\\"14\\" r=\\"8\\" />"\n' +
    '    ],\n' +
    '    heavy: [1]\n' +
    '  },',
    '  \'timer\': {  // the hand -- the moving, meaningful part; the knob and face stay light\n' +
    '    el: [\n' +
    '      "<line x1=\\"10\\" x2=\\"14\\" y1=\\"2\\" y2=\\"2\\" />",\n' +
    '      "<line x1=\\"12\\" x2=\\"15\\" y1=\\"14\\" y2=\\"11\\" />",\n' +
    '      "<circle cx=\\"12\\" cy=\\"14\\" r=\\"8\\" />"\n' +
    '    ],\n' +
    '    heavy: [1],\n' +
    '    dir: "mirror" // MUTATED: timer is not directional, this must not be here\n' +
    '  },',
    async (broken) => checkDirectionalMirroring(broken)));

  proved.push(await provesMutation(
    'M4. ALLOW HALF -- render() draws nothing for everything',
    ICONS_SRC,
    '  function render(val, size, opts) {\n    if (val == null) return \'\';',
    '  function render(val, size, opts) {\n    return \'\'; // MUTATED: renders nothing, ever\n    if (val == null) return \'\';',
    async (broken) => checkEveryNameAndEmojiRenders(broken)));

  proved.push(await provesMutation(
    'M5. revert the loud fallback to the pre-2026-09-08 raw-value passthrough',
    ICONS_SRC,
    '    if (ICONS[val]) return svg(val, size, opts);\n' +
    '    console.warn(\'AuraIcons.render(): no icon mapped for \' + JSON.stringify(val) +\n' +
    '      \' -- rendering the placeholder glyph instead of the raw value.\');\n' +
    '    return svg(\'circle-help\', size, opts);\n' +
    '  }',
    '    if (ICONS[val]) return svg(val, size, opts);\n' +
    '    return String(val); // MUTATED: back to the raw-value leak this pass fixed\n' +
    '  }',
    async (broken) => checkUnknownValueRendersPlaceholderAndWarns(broken)));

  console.log(`PASS: ${proved.length} guards proved by breaking the behaviour they watch:`);
  for (const p of proved) console.log('      ' + p);
}

// ═════════════════════════════════════════════════════════════════════════════
// EVERY VALUE THE PRODUCT ACTUALLY ASKS FOR RESOLVES
//
// checkEveryNameAndEmojiRenders() above iterates the ICONS and EMOJI maps and
// asks "does everything we MAPPED render?". That is the wrong direction for
// the failure this product keeps having, which is a CALL SITE asking for
// something nobody mapped:
//
//   * eleven sections shipped a mapped-looking icon that was actually a raw
//     emoji, because render() used to fall through to its own argument (see
//     this file's LOUD FALLBACK note and icons.js's module comment);
//   * _renderPOSGrid's ten category tiles asked for 💻 👕 🍔 🥤 💍 👟 ⚽ 💄, of
//     which NONE was in EMOJI -- eight system emoji on the busiest screen in
//     the product;
//   * _renderCapabilityRestricted's 🔒 default asked for a glyph that had a
//     perfectly good 'lock' icon behind it and no EMOJI entry pointing there.
//
// Since 2026-09-08 an unresolved value renders circle-help and console.warns,
// so the defect is loud AT RUNTIME -- but only for whoever happens to have the
// console open on the right screen. This check harvests the values from the
// SOURCE and refuses them here instead.
// ═════════════════════════════════════════════════════════════════════════════

/* Every literal handed to a render helper anywhere in the frontend.
   `_icon(name, size, fallback)` / `_stkaIcon` / `_exqIcon` / `_syncIcon` all
   forward their first argument to AuraIcons.render(), so they are harvested
   together with direct render() calls. The FALLBACK argument is deliberately
   NOT harvested: it is the glyph shown when icons.js failed to load at all,
   which is exactly the case where nothing can resolve. */
const HELPER = '(?:AuraIcons\\.render|(?:this\\.)?_icon|(?:this\\.)?_stkaIcon|(?:this\\.)?_exqIcon|(?:this\\.)?_syncIcon)';

/* Four shapes, because a regex over only the simplest one would have missed
   the very defect this check was written for. The POS category tiles were the
   worst case in the product -- eight unmapped emoji on its busiest screen --
   and they are NOT written as `_icon('laptop', …)`; they are a lookup table
   resolved into a variable. Harvesting only the literal-first-argument form
   would have reported a clean bill of health over exactly that gap. */
const CALL_SITE_PATTERNS = [
  // _icon('name', …)
  new RegExp(HELPER + "\\(\\s*'([^']+)'", 'g'),
  // _icon(cond ? 'a' : 'b', …)  — both branches
  new RegExp(HELPER + "\\([^()']*\\?\\s*'([^']+)'\\s*:\\s*'([^']+)'", 'g'),
  // _icon(o.icon || 'lock', …)  — the documented default
  new RegExp(HELPER + "\\(\\s*[\\w.$]+\\s*\\|\\|\\s*'([^']+)'", 'g'),
  // the POS grid's category -> icon-name lookup table, resolved at 2541 and
  // handed to _icon() at the tile. Anchored on the table's own name so this
  // cannot silently start matching some unrelated object literal.
  /_ICONS\s*=\s*\{([^}]*)\}/g,
];

function harvestRequestedIconValues() {
  const files = fs.readdirSync(FRONTEND_DIR).filter((f) => f.endsWith('.js') && f !== 'icons.js');
  const requested = new Map();   // value -> "file:line"
  for (const f of files) {
    const src = fs.readFileSync(path.join(FRONTEND_DIR, f), 'utf8');
    for (const re of CALL_SITE_PATTERNS) {
      re.lastIndex = 0;
      let m;
      while ((m = re.exec(src)) !== null) {
        const line = src.slice(0, m.index).split('\n').length;
        // The _ICONS table capture is a whole object body; every quoted VALUE
        // in it is a requested icon name (the keys are category names).
        const values = re.source.startsWith('_ICONS')
          ? (m[1].match(/:\s*'([^']+)'/g) || []).map((s) => s.replace(/^:\s*'|'$/g, ''))
          : m.slice(1).filter(Boolean);
        for (const v of values) if (!requested.has(v)) requested.set(v, `${f}:${line}`);
      }
    }
  }
  return requested;
}

function checkEveryRequestedValueResolves(src) {
  const requested = harvestRequestedIconValues();
  // ANTI-VACUITY: the harvest finding nothing would make every claim below
  // true of the empty set. The shell alone carries well over twenty.
  assert.ok(
    requested.size >= 30,
    `Only ${requested.size} icon value(s) harvested from the frontend call sites. The shell, the ` +
    'POS grid, the state panels and the modals carry far more than that, so treat this as a ' +
    'harness failure rather than a clean result.'
  );

  const warns = [];
  const AuraIcons = loadIcons(src, warns);
  const unresolved = [];
  for (const [value, where] of requested) {
    const before = warns.length;
    const out = AuraIcons.render(value, 24);
    const isSvg = /^<svg[\s>]/.test(out) && out.includes('</svg>');
    const fellBack = warns.length > before || out.includes('circle-help');
    if (!isSvg || fellBack) {
      unresolved.push(`${JSON.stringify(value)} (${where}) — ${fellBack ? 'circle-help placeholder + console.warn' : JSON.stringify(out.slice(0, 60))}`);
    }
  }
  assert.deepStrictEqual(
    unresolved, [],
    `${unresolved.length} value(s) the product actually asks for do not resolve to a real icon:\n  ` +
    unresolved.join('\n  ') +
    '\n\nAdding an icon is two steps (icons.js\'s own module comment): a Lucide name in ICONS with ' +
    'geometry fetched verbatim from unpkg.com/lucide-static, and -- if it stands in for an emoji ' +
    'the app already uses -- an EMOJI entry pointing at it. Never hand-draw the path.'
  );
  return requested.size;
}

// ─────────────────────────────────────────────────────────────────────────────
// MAIN
// ─────────────────────────────────────────────────────────────────────────────

async function main() {
  checkEveryNameAndEmojiRenders(ICONS_SRC);
  console.log('PASS: every icon name and emoji the app maps today renders a real <svg>');

  const requestedCount = checkEveryRequestedValueResolves(ICONS_SRC);
  console.log(`PASS: all ${requestedCount} icon value(s) the frontend actually requests resolve to a real <svg>, none to the placeholder`);

  checkTwoWeightTreatment(ICONS_SRC);
  console.log('PASS: the two-weight signature (light structure, one heavy element) is structurally present');

  checkUnknownValueRendersPlaceholderAndWarns(ICONS_SRC);
  console.log('PASS: an unresolved name/emoji renders the neutral placeholder and warns loudly, never the raw value');

  checkDirectionalMirroring(ICONS_SRC);
  console.log('PASS: only the directional icon (undo-2) carries the RTL mirror flag, and rtl.css mirrors it');

  checkReducedMotionDisablesAnimation(MAIN_CSS_SRC);
  console.log('PASS: both icon animation classes exist and are disabled under prefers-reduced-motion');

  checkStateReflectingIcons(ICONS_SRC);
  console.log('PASS: state-pair icons render distinct markup, and the real drawer-bar integration flips only on an actual state change');

  await testEveryGuardIsProvenByBreakingIt();

  console.log('PASS: retail_icons_test.js — 8 checks');
}

main().catch((err) => {
  console.error('FAIL: retail_icons_test.js');
  console.error(err);
  process.exitCode = 1;
});
