/**
 * retail_brand_ring_contrast_test.js — the brand ring must be VISIBLE on the
 * ground it is drawn on.
 *
 * THE BUG THIS PINS. The Aura mark's ring is a gradient ending at the brand
 * teal, at the ring's opening where the beacon sits — the brightest and most
 * distinctive part of the mark. That endpoint was the single literal #5fe3d0
 * on every theme. Measured against the grounds in main.css:
 *
 *     #5fe3d0 on Day  app #eaeef3 = 1.35:1     on Sand app #efe8dc = 1.29:1
 *     #5fe3d0 on Calm app #0f1319 = 11.87:1    Night 12.56:1   Dusk 11.90:1
 *
 * So on the two LIGHT themes roughly a third of the ring was not there at all,
 * while scoring ~12:1 on the three dark themes it had been designed against.
 * That asymmetry is exactly why nobody caught it by looking: the mark was only
 * ever judged on the palette it was born in.
 *
 * A ring is a graphical object, not text, so the bar here is the 3:1 non-text
 * minimum rather than 4.5:1.
 *
 * WHAT THIS TEST DOES NOT ASSERT, deliberately. It does NOT require the ring to
 * match the theme's accent. retail_design_tokens_test.js exempts `.aura-logo`
 * because "its colours are the company identity and must not shift with the
 * product theme", and that stands: --brand-ring-end holds exactly TWO values
 * across five themes, one per GROUND, and they are the same hue 0.3 degrees
 * apart. This file pins visibility, not palette agreement — so it cannot be
 * satisfied by quietly turning the mark into a theme-coloured blob.
 *
 * Run: node products/retail/tests/retail_brand_ring_contrast_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const CSS = fs.readFileSync(path.join(FRONTEND, 'css', 'main.css'), 'utf8');
const ICONS = fs.readFileSync(path.join(FRONTEND, 'icons.js'), 'utf8');
const BRAND = path.join(FRONTEND, 'brand');

/** A graphical object (a stroke, not text) needs 3:1 against its background. */
const MIN_RATIO = 3.0;

const THEMES = [
  ['Day', ':root {'],
  ['Sand', 'html[data-theme="sand"] {'],
  ['Calm', 'html[data-theme="dark"] {'],
  ['Night', 'html[data-theme="night"] {'],
  ['Dusk', 'html[data-theme="dusk"] {'],
];

function tokensOf(marker) {
  const i = CSS.indexOf(marker);
  assert.ok(i >= 0, `main.css has no ${marker} block`);
  const braceStart = i + marker.length - 1;
  const braceEnd = CSS.indexOf('}', braceStart);
  const block = CSS.slice(braceStart + 1, braceEnd);
  const out = {};
  const re = /--([a-z0-9-]+):\s*(#[0-9a-fA-F]{3,6})\s*;/g;
  let m;
  while ((m = re.exec(block)) !== null) out[m[1]] = m[2].toLowerCase();
  return out;
}

function rgb(hex) {
  let h = hex.replace('#', '');
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}
function lin(c) {
  const v = c / 255;
  return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
}
function lum([r, g, b]) { return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b); }
function contrast(a, b) {
  const la = lum(rgb(a)); const lb = lum(rgb(b));
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

const cases = [];
function test(name, fn) { cases.push([name, fn]); }

test('every theme declares --brand-ring-end', () => {
  const missing = THEMES.filter(([, marker]) => !tokensOf(marker)['brand-ring-end'])
    .map(([n]) => n);
  assert.deepStrictEqual(missing, [],
    `these themes have no --brand-ring-end, so the mark falls back to the dark-ground ` +
    `teal there regardless of their surfaces: ${missing.join(', ')}`);
});

test('the ring end clears 3:1 on its own theme app and panel surfaces', () => {
  const failures = [];
  let checked = 0;
  for (const [name, marker] of THEMES) {
    const t = tokensOf(marker);
    const end = t['brand-ring-end'];
    if (!end) continue;
    for (const surf of ['surface-app', 'surface-panel']) {
      const ground = t[surf];
      if (!ground) continue;
      checked += 1;
      const ratio = contrast(end, ground);
      if (ratio < MIN_RATIO) {
        failures.push(`${name} ${end} on ${surf} ${ground} = ${ratio.toFixed(2)}:1`);
      }
    }
  }
  // Anti-vacuity: an empty sweep would pass silently and prove nothing. Five
  // themes times two surfaces is ten comparisons; anything less means the
  // parser stopped seeing blocks, not that the product got better.
  assert.ok(checked >= 10,
    `only ${checked} surface comparisons ran — the token/ground parser has gone ` +
    'blind, so a pass here would mean nothing.');
  assert.deepStrictEqual(failures, [],
    `the brand ring's gradient endpoint is effectively invisible on these grounds ` +
    `(a graphical object needs ${MIN_RATIO}:1):\n  ` + failures.join('\n  '));
});

test('the dark themes keep the bright retail accent, not a darkened one', () => {
  // THE ALLOW HALF, and it exists because mutation-proving this file found the
  // hole: darkening all five CSS tokens satisfied the contrast check above
  // (a darker teal still clears 3:1 on a dark ground) while dimming the mark's
  // signature colour on the three themes where it scores ~12:1 and is the
  // whole point of the ring. The contrast bar alone cannot object to that,
  // because it only ever asks for MORE contrast.
  //
  // The identity colour is fixed by definition -- retail_design_tokens_test.js
  // says so -- so pinning the literal here is the correct assertion, not a
  // magic number: these three must be the brand's own value.
  //
  // THE VALUE CHANGED ON 2026-09-19, by owner instruction. Action Aura shipped
  // a finished brand identity (Action-Aura-Brand-Guide.md) in which TEAL
  // #2F7B7B is the MASTER brand accent and belongs to the family, not to any
  // one product; Aura Retail owns exactly two things, its accent trio
  // (#A06030 dark / #C97B3D mid / #F0B87A light) and its counter glyph. So the
  // ring on the Retail mark is amber now, and the old #5fe3d0 was not merely
  // recoloured -- it was never Retail's to begin with.
  //
  // The dark grounds take the LIGHT accent and the light grounds the DARK one,
  // which is the same light/dark split the guide ships as aura-mark-light.svg
  // and aura-mark-dark.svg. Measured on each theme's own app/panel:
  //     light 4.27 / 4.83   sand 4.10 / 4.67
  //     dark 10.65 / 10.01  night 11.11 / 10.67  dusk 10.53 / 10.00
  const RETAIL_ACCENT_LIGHT = '#F0B87A';
  const wrong = [];
  for (const name of ['Calm', 'Night', 'Dusk']) {
    const marker = THEMES.find(([n]) => n === name)[1];
    const end = tokensOf(marker)['brand-ring-end'];
    if (end && end.toLowerCase() !== RETAIL_ACCENT_LIGHT.toLowerCase()) wrong.push(`${name}=${end}`);
  }
  assert.deepStrictEqual(wrong, [],
    `these dark themes no longer use the bright retail accent ${RETAIL_ACCENT_LIGHT} for ` +
    `the ring end: ${wrong.join(', ')}. Only the LIGHT grounds take the darker #A06030; ` +
    'darkening the dark themes dims the mark where it already scores about 10:1.');
});

test('the in-app mark actually reads the token, so it is not dead config', () => {
  // EITHER a gradient stop OR a stroke. The 2026-09-08 mark drew its ring as a
  // gradient and ended it on this token; the 2026-09-19 brand mark draws the
  // flat/mono variant's ring as a single stroke. What must hold is that the
  // mark READS the per-theme token at all -- pinning `stop-color=` specifically
  // was pinning the old drawing technique, and it went red on a revision that
  // kept the property perfectly.
  assert.ok(/(stop-color|stroke)="var\(--brand-ring-end/.test(ICONS),
    "icons.js mark() does not read var(--brand-ring-end) for the ring, so the " +
    'per-ground token above is decorative and the mark paints one fixed colour ' +
    'on every theme.');
  // The fallback must be a REAL colour from Retail's trio, so the mark still
  // renders correctly anywhere the stylesheet has not applied. It is the
  // light-ground value, because that is the safer default: an un-themed page
  // is a white page.
  assert.ok(/var\(--brand-ring-end,\s*#A06030\)/i.test(ICONS),
    'the var() has no light-ground literal fallback (#A06030 from the brand guide) — ' +
    'the mark must still render correctly before or without the stylesheet.');
});

/* The two static-export checks below are PROPERTY-based, not literal-based.
   They used to name #5fe3d0 on both sides, which made them a second copy of
   the palette: when the owner replaced the identity on 2026-09-19 they failed
   for describing the old brand rather than for anything being wrong. Asking
   "is this export legible on the ground it is FOR" survives a revision; asking
   "does it contain this exact hex" does not. */

const LIGHT_GROUND = '#fcfbfa';   // Day --surface-panel
const DARK_GROUND = '#0f1829';    // Calm --surface-panel

function hexesIn(file) {
  const p = path.join(BRAND, file);
  if (!fs.existsSync(p)) return null;
  const found = fs.readFileSync(p, 'utf8').match(/#[0-9a-fA-F]{6}/g) || [];
  return [...new Set(found.map((h) => h.toLowerCase()))];
}

test('the light-ground exports are legible on a light ground', () => {
  const offenders = [];
  for (const f of ['aura-mark.svg', 'aura-lockup.svg']) {
    const hexes = hexesIn(f);
    if (!hexes) continue;
    // At least one ink in the file must actually read against a light panel.
    // A mark drawn entirely in dark-ground colours scores ~1.3:1 and is simply
    // absent, which is the defect this file was created for.
    const best = Math.max(...hexes.map((h) => contrast(h, LIGHT_GROUND)));
    if (best < MIN_RATIO) offenders.push(`${f} best=${best.toFixed(2)}:1`);
  }
  assert.deepStrictEqual(offenders, [],
    'these are the LIGHT-ground exports and nothing in them reaches ' +
    `${MIN_RATIO}:1 against a light panel, so the mark is effectively absent ` +
    `there: ${offenders.join(', ')}`);
});

test('the dark-ground export keeps a genuinely bright ink', () => {
  // The ALLOW HALF, and it is the reason this file was mutation-proved in the
  // first place: darkening every export would satisfy the light-ground check
  // above while dimming the mark on the three dark themes, where its brightest
  // ink is the most recognisable thing about it. A contrast floor alone cannot
  // object to that, because it only ever asks for MORE contrast on ONE ground.
  const hexes = hexesIn('aura-mark-on-dark.svg');
  if (!hexes) return;
  const best = Math.max(...hexes.map((h) => contrast(h, DARK_GROUND)));
  assert.ok(best >= 7,
    `aura-mark-on-dark.svg's brightest ink reaches only ${best.toFixed(2)}:1 on a dark ` +
    'panel. This is the DARK-ground master; the guide describes it as having ' +
    '"brightened facets and ring", and a light-ground fix must not have been ' +
    'applied to it wholesale.');
});

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
