/**
 * REGRESSION GUARD — `.ret-btn-primary` (and its siblings `.ret-btn-danger`,
 * `.ret-btn-ghost`) must clear WCAG AA (4.5:1) text-on-background contrast in
 * BOTH the light and the dark theme, computed from the tokens each rule
 * actually resolves through — never eyeballed.
 *
 * WHY THIS EXISTS
 * `.ret-btn-primary` is the main call-to-action across the retail UI (POS
 * "Resume", Products "Add Product", Suppliers "+ PO", the sale-receipt
 * modal's "New Sale", etc — see subsystem-retail.js's own AUDIT comment
 * above `button.ret-btn` in css/main.css for the touch-target history of
 * this exact class). It is painted by ONE rule
 * (subsystem-retail.js `_injectStyles()`, `.ret-btn-primary`):
 *
 *     background: var(--sub-accent);
 *     color:      var(--text-on-accent);
 *
 * `--sub-accent` is itself a var() indirection onto `--accent-action`
 * (css/main.css, the "Subsystem-shell token bridge" comment), so its real
 * colour in each theme depends on a two-hop chain: `.ret-btn-primary` ->
 * `--sub-accent` -> `--accent-action`, resolved against whichever theme's
 * token block is in effect. A THIRD token, `--text-on-accent`, has to flip
 * in the same direction the accent does (light ink on a light-mode fill,
 * dark ink on the light-blue dark-mode fill main.css's dark block uses —
 * see its "ACCENT" comment for why the strategy flips). Three names, one
 * chain, two themes: exactly the shape a specificity fight or a half-updated
 * bridge alias breaks silently, because nothing here is a literal colour
 * that a human reviewer can eyeball in a diff.
 *
 * An earlier attempt to raise this contrast landed on 4.17:1 (still short of
 * AA) by repainting the button BLUE — trading the product's accent identity
 * for a number, which is a worse outcome than the number it was chasing. That
 * attempt was reverted. This guard exists so any future attempt is checked
 * against the real WCAG threshold before it ships, in whichever colour it
 * uses, rather than trusted on sight.
 *
 * METHOD
 * This file parses the SAME two structures the browser cascades over:
 *   1. main.css's token blocks — every `--name: value;` declared on a rule
 *      whose selector includes `:root` (the LIGHT/base map, since
 *      `html[data-theme="light"]` is always grouped with `:root` in this
 *      file — see main.css's own "Subsystem-shell token bridge" comment for
 *      why), and every `--name: value;` declared on `html[data-theme="dark"]`
 *      (layered on top of the light map for the DARK map — token VALUES
 *      only, exactly how css/main.css's own dark-theme comment describes the
 *      cascade a dark document resolves).
 *   2. subsystem-retail.js's injected `_injectStyles()` sheet, for the three
 *      button rules' own `background`/`color` declarations.
 * It then resolves each rule's var() chain against each theme's map and
 * computes WCAG 2.x relative-luminance contrast — the same formula
 * retail_design_contrast_test.js uses, reimplemented here rather than
 * imported so this guard keeps working even if that heavier file's cascade
 * engine changes shape (matches this test suite's existing convention: each
 * file carries its own parseHex/contrastRatio rather than sharing one).
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_btn_primary_contrast_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const AA = 4.5; // WCAG 2.2 §1.4.3 -- normal-size body/label text

const MAIN_CSS = path.join(__dirname, '..', 'frontend', 'css', 'main.css');
const CHROME_JS = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

/* ── Colour maths (WCAG 2.x relative luminance) — same formula as
   retail_design_contrast_test.js, reimplemented per that file's own
   convention of not sharing this across test files. ── */
function parseHex(value) {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(value).trim());
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

/* ── Token maps ────────────────────────────────────────────────────────────
   Deliberately NOT scoped to the [design-tokens:begin/end] markers the way
   retail_design_contrast_test.js's TIER 1 is: `--sub-accent` itself lives in
   the "Subsystem-shell token bridge" section further down the file, outside
   those markers, and IS the two-hop indirection this guard exists to check.
   Scanning the whole file for `:root` / `html[data-theme="dark"]` rules is
   what makes that bridge visible at all. */
function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

function findRuleBlocks(css, selectorTest) {
  // Minimal, non-nested top-level rule scanner: main.css's token rules have
  // no nested `{}` in their bodies, so a balanced-brace walk is unnecessary --
  // the same simplifying assumption retail_design_contrast_test.js's own
  // parseTokens() makes (it scans a slice between markers for `--name: v;`
  // pairs without tracking selectors at all; this guard is slightly stricter
  // because it must tell `:root` apart from `html[data-theme="dark"]`).
  const blocks = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(css)) !== null) {
    const selectors = m[1];
    if (selectorTest(selectors)) blocks.push(m[2]);
  }
  return blocks;
}

function declsOf(block) {
  const tokens = new Map();
  const re = /(--[a-z0-9-]+)\s*:\s*([^;]+);/gi;
  let m;
  while ((m = re.exec(block)) !== null) tokens.set(m[1], m[2].trim());
  return tokens;
}

function buildTokenMaps() {
  const css = stripComments(fs.readFileSync(MAIN_CSS, 'utf8'));
  const isRoot = (sel) => /(^|,|\s):root\b/.test(sel);
  const isDark = (sel) => /html\[data-theme=["']?dark["']?\]/.test(sel);

  const light = new Map();
  for (const block of findRuleBlocks(css, isRoot)) {
    for (const [k, v] of declsOf(block)) light.set(k, v);
  }
  const dark = new Map(light);
  for (const block of findRuleBlocks(css, isDark)) {
    for (const [k, v] of declsOf(block)) dark.set(k, v);
  }
  return { light, dark };
}

function resolveVar(value, tokens, depth) {
  if (value == null) return null;
  if ((depth || 0) > 12) return null; // circular/too-deep reference guard
  const v = String(value).trim();
  const m = /^var\(\s*(--[-\w]+)\s*(?:,([\s\S]+))?\)$/.exec(v);
  if (!m) return v;
  if (tokens.has(m[1])) return resolveVar(tokens.get(m[1]), tokens, (depth || 0) + 1);
  if (m[2] !== undefined) return resolveVar(m[2], tokens, (depth || 0) + 1);
  return null; // token genuinely absent -- caller must treat as unresolved
}

/* ── The three chrome button rules under guard ───────────────────────────── */
function buttonRuleDecls(selector) {
  const js = fs.readFileSync(CHROME_JS, 'utf8');
  const re = new RegExp(`\\.${selector.replace(/^\./, '')}\\s*\\{([^}]*)\\}`);
  const m = re.exec(js);
  assert.ok(m, `Expected a "${selector}" rule in ${path.basename(CHROME_JS)}'s _injectStyles()`);
  const body = m[1];
  const bg = /\bbackground\s*:\s*([^;]+);/.exec(body);
  const fg = /\bcolor\s*:\s*([^;]+);/.exec(body);
  assert.ok(bg, `"${selector}" declares no background`);
  assert.ok(fg, `"${selector}" declares no color`);
  return { background: bg[1].trim(), color: fg[1].trim() };
}

const BUTTONS = ['ret-btn-primary', 'ret-btn-danger', 'ret-btn-ghost'];

function measureAll() {
  const { light, dark } = buildTokenMaps();
  const results = [];
  for (const name of BUTTONS) {
    const decls = buttonRuleDecls(name);
    for (const [theme, tokens] of [['light', light], ['dark', dark]]) {
      const bg = resolveVar(decls.background, tokens);
      const fg = resolveVar(decls.color, tokens);
      const bgHex = parseHex(bg);
      const fgHex = parseHex(fg);
      results.push({ name, theme, bgRaw: decls.background, fgRaw: decls.color, bg, fg, bgHex, fgHex });
    }
  }
  return results;
}

/* ── Tests ─────────────────────────────────────────────────────────────────*/

function testTokenMapsParsedSomething(maps) {
  // ANTI-VACUITY: a loop over an empty map passes every assertion below
  // without checking a single colour. Assert the parse actually found the
  // tokens this guard depends on before trusting anything it concludes.
  assert.ok(maps.light.size >= 20, `Expected >=20 light tokens, parsed ${maps.light.size}. The :root scan is probably broken.`);
  assert.ok(maps.dark.size >= 20, `Expected >=20 dark tokens, parsed ${maps.dark.size}. The html[data-theme="dark"] scan is probably broken.`);
  assert.ok(maps.light.has('--sub-accent'), '--sub-accent not found in the light token map -- the Subsystem-shell token bridge selector match is broken.');
  assert.ok(maps.light.has('--accent-action') && maps.dark.has('--accent-action'), '--accent-action missing from one of the theme maps.');
  console.log(`PASS: parsed ${maps.light.size} light / ${maps.dark.size} dark tokens from main.css`);
}

function testEveryButtonResolvesInBothThemes(results) {
  const unresolved = results.filter((r) => !r.bgHex || !r.fgHex);
  assert.deepStrictEqual(
    unresolved.map((r) => `${r.name} [${r.theme}]: background "${r.bgRaw}" -> ${r.bg}, color "${r.fgRaw}" -> ${r.fg}`),
    [],
    `${unresolved.length} button/theme pairing(s) did not resolve to a concrete colour -- ` +
    'a var() chain is broken (missing token, no fallback, or a rename on one side of the bridge).'
  );
  console.log(`PASS: all ${results.length} button/theme pairings resolved to concrete colours`);
}

function testEveryButtonClearsAA(results) {
  const failures = [];
  for (const r of results) {
    const bg = '#' + r.bgHex.map((c) => c.toString(16).padStart(2, '0')).join('');
    const fg = '#' + r.fgHex.map((c) => c.toString(16).padStart(2, '0')).join('');
    const ratio = contrastRatio(bg, fg);
    r.ratio = ratio;
    r.bgFinal = bg;
    r.fgFinal = fg;
    if (ratio < AA) {
      failures.push(
        `.${r.name} [${r.theme}]: color ${fg} on background ${bg} = ${ratio.toFixed(2)}:1 ` +
        `(needs >= ${AA}:1)\n      background <- "${r.bgRaw}"\n      color      <- "${r.fgRaw}"`
      );
    }
  }
  assert.deepStrictEqual(
    failures, [],
    `${failures.length} button/theme pairing(s) fall below WCAG AA (${AA}:1):\n  ` + failures.join('\n  ') +
    '\n\nThese are primary/danger/ghost actions across the retail till -- "Resume", ' +
    '"+ PO", "Delete", "Cancel". A control nobody can read is worse than no control.'
  );
  const worst = results.reduce((a, b) => (b.ratio < a.ratio ? b : a));
  const lines = results.map((r) => `  .${r.name.padEnd(16)} [${r.theme.padEnd(5)}] ${r.fgFinal} on ${r.bgFinal} = ${r.ratio.toFixed(2)}:1`);
  console.log(`PASS: all ${results.length} button/theme pairings clear AA (${AA}:1) -- tightest is ${worst.ratio.toFixed(2)}:1 at .${worst.name} [${worst.theme}]`);
  console.log(lines.join('\n'));
}

const CHECKS_SETUP = () => {
  const maps = buildTokenMaps();
  const results = measureAll();
  return { maps, results };
};

function main() {
  let ctx;
  try {
    ctx = CHECKS_SETUP();
  } catch (err) {
    console.error('FAIL: retail_btn_primary_contrast_test.js (setup)');
    console.error('      ' + String((err && err.message) || err));
    process.exitCode = 1;
    return;
  }

  const CHECKS = [
    ['token maps parsed something real', () => testTokenMapsParsedSomething(ctx.maps)],
    ['every button resolves in both themes', () => testEveryButtonResolvesInBothThemes(ctx.results)],
    ['every button clears AA in both themes', () => testEveryButtonClearsAA(ctx.results)],
  ];

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
    console.error(`\nFAIL: retail_btn_primary_contrast_test.js — ${failures.length} of ${CHECKS.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_btn_primary_contrast_test.js — ${CHECKS.length} checks`);
}

main();
