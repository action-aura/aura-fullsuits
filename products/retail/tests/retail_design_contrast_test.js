/**
 * OPERATIONAL CALM — WCAG contrast proof for the retail till palette.
 *
 * WHY THIS TEST EXISTS
 * The retail frontend previously shipped a near-black "HUD" palette
 * (--bg-dark:#080808, neon accents) whose light-theme override was added
 * later and incompletely. That produced real, shipped contrast bugs: the
 * dashboard's Recent Transactions cells rendered ~2.64:1 grey-on-white
 * (see retail_dashboard_transaction_contrast_test.js), and --text-faint
 * resolved to #8794a8 = ~2.9:1 on a white card. Both passed code review,
 * because contrast is exactly the property a human eye cannot audit by
 * looking -- especially on the developer's calibrated monitor rather than
 * the cheap fluorescent-lit shop monitor the till actually runs on.
 *
 * So this test does not eyeball anything. It parses the token block out of
 * css/main.css, converts each token to sRGB luminance, and COMPUTES the
 * WCAG 2.x contrast ratio for every text-on-surface pairing the palette can
 * produce. It is the one assertion in this redesign that cannot be argued
 * with.
 *
 * WHAT IT ASSERTS, AND WHY THAT SHAPE
 * Not "these N pairings I happened to pick are fine" -- that would let a new
 * token be added tomorrow and never checked. Instead it asserts a PROPERTY
 * over the whole cross product:
 *
 *   every --text-* colour token  x  every solid --surface-* token  >= 4.5:1 (AA)
 *   every --text-money* token    x  every solid --surface-* token  >= 7.0:1 (AAA)
 *   --text-on-accent             x  every --accent-action* fill    >= 4.5:1 (AA)
 *
 * Money gets the stricter AAA floor because a misread total is a till's
 * worst failure mode, and because money is read at a glance, at an angle,
 * over a customer's shoulder, all day.
 *
 * Because the groups are derived from the token NAMES rather than a hand
 * list, adding a new --text-* or --surface-* token automatically brings it
 * under test. That is the point: the palette cannot quietly grow a failing
 * member.
 *
 * ANTI-VACUITY
 * A cross-product test that matches zero tokens passes trivially and tells
 * you nothing. If the token block is renamed, reformatted, or the parser
 * breaks, every loop below iterates zero times and the suite would go green
 * while asserting nothing at all. So the test first asserts it actually
 * FOUND a realistic number of text tokens, surface tokens and pairings, and
 * fails loudly if the parse came back thin. The check running is asserted
 * separately from the check passing.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_design_contrast_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const CSS_FILE = path.join(__dirname, '..', 'frontend', 'css', 'main.css');

const AA = 4.5;   // WCAG 2.2 §1.4.3 -- normal-size body text
const AAA = 7.0;  // WCAG 2.2 §1.4.6 -- enhanced; required here for money

/* ── Token parsing ─────────────────────────────────────────────────────────
   Only the block between the [design-tokens:begin]/[design-tokens:end]
   markers is the palette. Reading the whole file would sweep up the legacy
   bridge aliases (which are var() indirections, not colours) and every
   unrelated literal further down. */
function readTokenBlock() {
  const css = fs.readFileSync(CSS_FILE, 'utf8');
  const start = css.indexOf('[design-tokens:begin]');
  const end = css.indexOf('[design-tokens:end]');
  assert.ok(start !== -1, 'main.css is missing the [design-tokens:begin] marker');
  assert.ok(end !== -1, 'main.css is missing the [design-tokens:end] marker');
  assert.ok(end > start, '[design-tokens:end] appears before [design-tokens:begin]');
  return css.slice(start, end);
}

function parseTokens(block) {
  const tokens = new Map();
  // Strip comments first: the token block is heavily commented and a comment
  // containing a `#` or a `;` would otherwise be parsed as a declaration.
  const clean = block.replace(/\/\*[\s\S]*?\*\//g, '');
  const re = /(--[a-z0-9-]+)\s*:\s*([^;]+);/gi;
  let m;
  while ((m = re.exec(clean)) !== null) tokens.set(m[1], m[2].trim());
  return tokens;
}

/* ── Colour maths (WCAG 2.x relative luminance) ───────────────────────────── */
function parseHex(value) {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value.trim());
  if (!m) return null;
  let h = m[1];
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}

function channelLuminance(c) {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}

function relativeLuminance(rgb) {
  const [r, g, b] = rgb.map(channelLuminance);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrastRatio(hexA, hexB) {
  const la = relativeLuminance(parseHex(hexA));
  const lb = relativeLuminance(parseHex(hexB));
  const hi = Math.max(la, lb);
  const lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

/* ── Grouping ──────────────────────────────────────────────────────────────
   Derived from naming, so the palette cannot grow an untested member.
   Non-colour tokens that share the --text- prefix (--text-size-*,
   --text-weight-*, --text-leading-*) are excluded by the simple fact that
   their values are not hex colours -- no name allowlist needed. */
function groupTokens(tokens) {
  const textOnSurface = [];
  const money = [];
  const surfaces = [];
  const accentFills = [];
  const nonHexSkipped = [];

  for (const [name, value] of tokens) {
    const isHex = parseHex(value) !== null;

    if (name.startsWith('--surface-')) {
      if (isHex) surfaces.push([name, value]);
      // --surface-scrim is deliberately rgba(): it is a translucent modal
      // backdrop, not a surface text ever sits directly on, so it has no
      // fixed luminance to test against.
      else nonHexSkipped.push(name);
      continue;
    }
    if (name.startsWith('--accent-action')) {
      if (isHex) accentFills.push([name, value]);
      continue;
    }
    if (name.startsWith('--text-')) {
      if (!isHex) { nonHexSkipped.push(name); continue; }
      // --text-on-accent is by definition NOT for use on a surface; it is
      // checked against the accent fills instead, further down.
      if (name === '--text-on-accent') continue;
      if (name.startsWith('--text-money')) money.push([name, value]);
      textOnSurface.push([name, value]);
      continue;
    }
  }
  return { textOnSurface, money, surfaces, accentFills, nonHexSkipped };
}

/* ── Tests ─────────────────────────────────────────────────────────────────*/

function testParseFoundARealPalette(groups) {
  // ANTI-VACUITY GUARD. Every assertion below is a loop over these arrays; if
  // the parse silently returned nothing, those loops would pass without
  // comparing a single colour. Assert the check CAN run before asserting it
  // passes. The floors are deliberately below the current counts so ordinary
  // palette growth does not trip them, but a broken parse (0, or 1) does.
  assert.ok(
    groups.textOnSurface.length >= 4,
    `Expected to parse at least 4 text colour tokens, found ${groups.textOnSurface.length}. ` +
    'The token block or its markers probably changed shape -- every contrast ' +
    'assertion in this file iterates over this list, so a thin parse would ' +
    'make the whole suite pass while checking nothing.'
  );
  assert.ok(
    groups.surfaces.length >= 5,
    `Expected at least 5 solid surface tokens, found ${groups.surfaces.length}.`
  );
  assert.ok(
    groups.money.length >= 3,
    `Expected at least 3 --text-money* tokens, found ${groups.money.length}.`
  );
  assert.ok(
    groups.accentFills.length >= 1,
    `Expected at least 1 --accent-action* fill, found ${groups.accentFills.length}.`
  );

  const pairings = groups.textOnSurface.length * groups.surfaces.length;
  assert.ok(pairings >= 20, `Expected >= 20 text-on-surface pairings, computed ${pairings}.`);

  console.log(
    `PASS: parsed a real palette — ${groups.textOnSurface.length} text tokens x ` +
    `${groups.surfaces.length} surfaces = ${pairings} pairings, ` +
    `${groups.money.length} money tokens, ${groups.accentFills.length} accent fills`
  );
}

function testEveryTextOnSurfaceReachesAA(groups) {
  const failures = [];
  for (const [tName, tVal] of groups.textOnSurface) {
    for (const [sName, sVal] of groups.surfaces) {
      const ratio = contrastRatio(tVal, sVal);
      if (ratio < AA) {
        failures.push(`${tName} (${tVal}) on ${sName} (${sVal}) = ${ratio.toFixed(2)}:1`);
      }
    }
  }
  assert.deepStrictEqual(
    failures, [],
    `${failures.length} text-on-surface pairing(s) fall below WCAG AA (${AA}:1).\n  ` +
    failures.join('\n  ') +
    '\n\nEvery --text-* token must be legible on every --surface-* token, ' +
    'because the shell composes them freely (a label lands on a card, a card ' +
    'lands on the shell, a row highlights on hover).'
  );
  console.log(`PASS: all ${groups.textOnSurface.length * groups.surfaces.length} text-on-surface pairings reach WCAG AA (${AA}:1)`);
}

function testMoneyReachesAAA(groups) {
  const failures = [];
  const worst = { ratio: Infinity, label: '' };
  for (const [tName, tVal] of groups.money) {
    for (const [sName, sVal] of groups.surfaces) {
      const ratio = contrastRatio(tVal, sVal);
      if (ratio < worst.ratio) {
        worst.ratio = ratio;
        worst.label = `${tName} on ${sName}`;
      }
      if (ratio < AAA) {
        failures.push(`${tName} (${tVal}) on ${sName} (${sVal}) = ${ratio.toFixed(2)}:1`);
      }
    }
  }
  assert.deepStrictEqual(
    failures, [],
    `${failures.length} money pairing(s) fall below WCAG AAA (${AAA}:1).\n  ` +
    failures.join('\n  ') +
    '\n\nMoney is held to AAA, not AA: a misread total is the worst thing a ' +
    'till can do, and amounts get read at a glance and at an angle all shift.'
  );
  console.log(
    `PASS: all ${groups.money.length * groups.surfaces.length} money pairings reach WCAG AAA (${AAA}:1) ` +
    `— tightest is ${worst.label} at ${worst.ratio.toFixed(2)}:1`
  );
}

function testTextOnAccentReachesAA(tokens, groups) {
  const onAccent = tokens.get('--text-on-accent');
  assert.ok(onAccent, 'Expected a --text-on-accent token (the label colour for accent-filled buttons)');
  assert.ok(parseHex(onAccent), `--text-on-accent must be a hex colour, got "${onAccent}"`);

  const failures = [];
  for (const [aName, aVal] of groups.accentFills) {
    const ratio = contrastRatio(onAccent, aVal);
    if (ratio < AA) failures.push(`--text-on-accent (${onAccent}) on ${aName} (${aVal}) = ${ratio.toFixed(2)}:1`);
  }
  assert.deepStrictEqual(
    failures, [],
    `Accent-filled controls have unreadable labels:\n  ${failures.join('\n  ')}\n\n` +
    'These are the primary actions -- "Open POS", "Charge", "Confirm". A ' +
    'primary button nobody can read is worse than no primary button.'
  );
  console.log(`PASS: --text-on-accent reaches AA on all ${groups.accentFills.length} accent fills`);
}

function testMoneyNegativeIsNotColourAlone() {
  // Direction requirement #3: a negative amount must be distinguishable
  // WITHOUT relying on colour. A red minus sign is invisible to a colourblind
  // cashier and disappears on a washed-out monitor. Assert the rule carries at
  // least one NON-colour differentiator, so the styling survives greyscale.
  const css = fs.readFileSync(CSS_FILE, 'utf8');
  const m = /\.money--negative\s*\{([^}]*)\}/.exec(css);
  assert.ok(m, 'Expected a .money--negative rule in main.css');
  const body = m[1];

  const nonColourCues = [/font-weight\s*:/, /text-decoration\s*:/, /font-style\s*:/, /content\s*:/];
  const found = nonColourCues.filter((re) => re.test(body));
  assert.ok(
    found.length >= 1,
    '.money--negative distinguishes negative amounts by colour ALONE:\n  ' + body.trim() +
    '\n\nA colourblind cashier, or anyone on a washed-out shop monitor, cannot ' +
    'see a red minus. Pair the colour with a non-colour cue (weight, ' +
    'decoration, or an accounting-parenthesis ::before/::after).'
  );
  console.log(`PASS: .money--negative carries ${found.length} non-colour cue(s) alongside its colour`);
}

function main() {
  const tokens = parseTokens(readTokenBlock());
  const groups = groupTokens(tokens);

  testParseFoundARealPalette(groups);
  testEveryTextOnSurfaceReachesAA(groups);
  testMoneyReachesAAA(groups);
  testTextOnAccentReachesAA(tokens, groups);
  testMoneyNegativeIsNotColourAlone();

  console.log('PASS: retail_design_contrast_test.js');
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_design_contrast_test.js');
  console.error(err.message || err);
  process.exitCode = 1;
}
