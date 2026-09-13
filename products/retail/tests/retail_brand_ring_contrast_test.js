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

test('the dark themes keep the bright brand teal, not a darkened one', () => {
  // THE ALLOW HALF, and it exists because mutation-proving this file found the
  // hole: darkening all five CSS tokens satisfied the contrast check above
  // (a darker teal still clears 3:1 on a dark ground) while dimming the mark's
  // signature colour on the three themes where it scores ~12:1 and is the
  // whole point of the ring. The contrast bar alone cannot object to that,
  // because it only ever asks for MORE contrast.
  //
  // The identity colour is fixed by definition -- retail_design_tokens_test.js
  // says so -- so pinning the literal here is the correct assertion, not a
  // magic number: these three must be the brand teal itself.
  const BRAND_TEAL = '#5fe3d0';
  const wrong = [];
  for (const name of ['Calm', 'Night', 'Dusk']) {
    const marker = THEMES.find(([n]) => n === name)[1];
    const end = tokensOf(marker)['brand-ring-end'];
    if (end && end !== BRAND_TEAL) wrong.push(`${name}=${end}`);
  }
  assert.deepStrictEqual(wrong, [],
    `these dark themes no longer use the brand teal ${BRAND_TEAL} for the ring end: ` +
    `${wrong.join(', ')}. Only the LIGHT grounds may darken it; darkening the dark ` +
    'themes dims the mark where it was already at about 12:1.');
});

test('the in-app mark actually reads the token, so it is not dead config', () => {
  assert.ok(/stop-color="var\(--brand-ring-end/.test(ICONS),
    "icons.js mark() does not read var(--brand-ring-end) for the ring's end stop, so " +
    'the per-ground token above is decorative and the mark still paints one fixed teal.');
  assert.ok(/var\(--brand-ring-end,\s*#5fe3d0\)/.test(ICONS),
    'the var() has no dark-ground literal fallback — the mark must still render ' +
    'correctly anywhere the stylesheet has not applied.');
});

test('the light-ground static exports do not carry the dark-ground teal', () => {
  const offenders = [];
  for (const f of ['aura-mark.svg', 'aura-lockup.svg']) {
    const p = path.join(BRAND, f);
    if (!fs.existsSync(p)) continue;
    if (fs.readFileSync(p, 'utf8').toLowerCase().includes('#5fe3d0')) offenders.push(f);
  }
  assert.deepStrictEqual(offenders, [],
    'these are the LIGHT-ground exports and they still use the dark-ground teal, ' +
    `which measures 1.29-1.35:1 on the light themes: ${offenders.join(', ')}`);
});

test('the dark-ground exports DO keep the bright brand teal', () => {
  // The allow-half. Darkening every file would "fix" the contrast failure by
  // dimming the mark everywhere, including the three themes where the bright
  // teal already scores ~12:1 and is the point of the mark.
  const p = path.join(BRAND, 'aura-mark-on-dark.svg');
  if (!fs.existsSync(p)) return;
  assert.ok(fs.readFileSync(p, 'utf8').toLowerCase().includes('#5fe3d0'),
    'aura-mark-on-dark.svg lost the bright brand teal. On dark grounds it scores ' +
    'about 12:1 and is the most recognisable part of the mark; the light-ground ' +
    'fix must not have been applied to it.');
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
