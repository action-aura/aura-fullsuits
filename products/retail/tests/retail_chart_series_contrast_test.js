/**
 * retail_chart_series_contrast_test.js — a chart series has to be visible on
 * the card it is drawn on, and legible against the OTHER series.
 *
 * THE BUG THIS PINS. The dashboard and Reports charts use CATEGORICAL colours:
 * one fixed value per series, identical on every theme, so "Revenue" and "cash"
 * are always the same colour. That is correct data-viz practice and is
 * deliberate — a series that changed colour per theme would destroy
 * recognition, and this file does not challenge it.
 *
 * What that decision never asked is whether a categorical colour is VISIBLE on
 * the surface it lands on. These charts render inside .sub-chart-card, which
 * paints --surface-panel, and that ranges from #ffffff to #0b111b across the
 * five themes. Measured 2026-09-13, against a 3:1 floor because a line, bar or
 * doughnut slice is a graphical object rather than text:
 *
 *     #38bdf8 Revenue        Day 2.14:1  Sand 2.01:1   (dark themes 8.1-8.8)
 *     #10b981 Revenue, cash  Day 2.54:1  Sand 2.38:1   (dark themes 6.9-7.5)
 *     #f59e0b mobile         Day 2.14:1  Sand 2.01:1
 *     #06b6d4 voucher        Day 2.42:1  Sand 2.27:1
 *
 * Four of eight, all chosen against the dark palette and inherited unchecked by
 * the two light ones — the same failure the brand mark's ring had on the same
 * day. Both Reports failures were REVENUE, the series that screen exists for.
 *
 * TWO HALVES, because fixing only the first would have been a regression. A
 * contrast bar can only ever ask for MORE contrast, so it would happily accept
 * every categorical colour collapsing into one dark mid-tone — perfectly
 * visible on the card and completely useless as a legend. The separability
 * check is the half that refuses that.
 *
 * THE COLOUR LIST IS DERIVED FROM THE SOURCE, never hand-typed here. A list
 * copied into this file would keep passing after someone added a seventh
 * payment method or a third series in the very colour that fails — which is
 * exactly the class of miss this file exists for.
 *
 * Run: node products/retail/tests/retail_chart_series_contrast_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const SRC = fs.readFileSync(path.join(FRONTEND, 'subsystem-retail.js'), 'utf8');
const CSS = fs.readFileSync(path.join(FRONTEND, 'css', 'main.css'), 'utf8');

/** A line, bar or slice is a graphical object, not text. */
const MIN_RATIO = 3.0;
/** Below roughly this, two categorical colours stop being tellable apart. */
const MIN_SEPARATION = 20.0;

const THEMES = [
  ['Day', ':root {'],
  ['Sand', 'html[data-theme="sand"] {'],
  ['Calm', 'html[data-theme="dark"] {'],
  ['Night', 'html[data-theme="night"] {'],
  ['Dusk', 'html[data-theme="dusk"] {'],
];

function rgb(hex) {
  let h = hex.replace('#', '');
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}
function lin(c) {
  const v = c / 255;
  return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
}
function lum(c) { const [r, g, b] = rgb(c); return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b); }
function contrast(a, b) {
  const la = lum(a); const lb = lum(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}
function lab(hex) {
  const [R, G, B] = rgb(hex).map(lin);
  const x = (0.4124 * R + 0.3576 * G + 0.1805 * B) / 0.95047;
  const y = 0.2126 * R + 0.7152 * G + 0.0722 * B;
  const z = (0.0193 * R + 0.1192 * G + 0.9505 * B) / 1.08883;
  const f = (t) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  const [fx, fy, fz] = [f(x), f(y), f(z)];
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}
function deltaE(a, b) {
  const A = lab(a); const B = lab(b);
  return Math.sqrt(A.reduce((s, v, i) => s + (v - B[i]) ** 2, 0));
}

/** Each theme's chart-card ground: .sub-chart-card paints --surface-panel. */
function cardGrounds() {
  const out = {};
  for (const [name, marker] of THEMES) {
    const i = CSS.indexOf(marker);
    assert.ok(i >= 0, `main.css has no ${marker} block`);
    const bs = i + marker.length - 1;
    const block = CSS.slice(bs + 1, CSS.indexOf('}', bs));
    const m = /--surface-panel:\s*(#[0-9a-fA-F]{3,6})\s*;/.exec(block);
    assert.ok(m, `${name} declares no --surface-panel`);
    out[name] = m[1].toLowerCase();
  }
  return out;
}

/** The six payment-method colours, read out of the real shared map. */
function paymentColours() {
  const m = /RETAIL_PAYMENT_METHOD_COLORS\s*=\s*\{([\s\S]*?)\}/.exec(SRC);
  assert.ok(m, 'RETAIL_PAYMENT_METHOD_COLORS not found — has the map been renamed?');
  const out = {};
  const re = /(\w+)\s*:\s*'(#[0-9a-fA-F]{6})'/g;
  let hit;
  while ((hit = re.exec(m[1])) !== null) out[hit[1]] = hit[2].toLowerCase();
  return out;
}

/** Every Chart.js series colour written as a literal in this file. */
function seriesColours() {
  const out = new Set();
  const re = /(?:borderColor|backgroundColor)\s*:\s*'(#[0-9a-fA-F]{6})'/g;
  let hit;
  while ((hit = re.exec(SRC)) !== null) out.add(hit[1].toLowerCase());
  return [...out];
}

const cases = [];
function test(name, fn) { cases.push([name, fn]); }

test('every categorical chart colour clears 3:1 on every theme card', () => {
  const grounds = cardGrounds();
  const pay = paymentColours();
  const series = seriesColours();
  const all = [
    ...Object.entries(pay).map(([k, v]) => [`payment:${k}`, v]),
    ...series.map((v) => [`series ${v}`, v]),
  ];

  // Anti-vacuity. A regex that stopped matching would make every comparison
  // below vacuously true. Six payment methods plus at least three distinct
  // series literals exist today; five grounds each.
  assert.ok(Object.keys(pay).length >= 6,
    `only ${Object.keys(pay).length} payment colours parsed — the map parser has gone blind.`);
  assert.ok(series.length >= 3,
    `only ${series.length} series colours parsed — the series parser has gone blind.`);
  assert.strictEqual(Object.keys(grounds).length, 5, 'expected five theme card grounds');

  const failures = [];
  for (const [what, hex] of all) {
    for (const [theme, ground] of Object.entries(grounds)) {
      const r = contrast(hex, ground);
      if (r < MIN_RATIO) failures.push(`${what} ${hex} on ${theme} card ${ground} = ${r.toFixed(2)}:1`);
    }
  }
  assert.deepStrictEqual(failures, [],
    `these chart colours are below the ${MIN_RATIO}:1 graphical-object floor on the card ` +
    `they are drawn on:\n  ${failures.join('\n  ')}`);
});

test('the payment colours still tell each other apart in one doughnut', () => {
  // THE OTHER HALF. Without this, the check above is satisfied by collapsing
  // every method into the same dark mid-tone: maximum contrast against the
  // card, zero information in the chart.
  const pay = paymentColours();
  const names = Object.keys(pay);
  assert.ok(names.length >= 6, 'payment map parser went blind');

  const tooClose = [];
  for (let i = 0; i < names.length; i += 1) {
    for (let j = i + 1; j < names.length; j += 1) {
      const d = deltaE(pay[names[i]], pay[names[j]]);
      if (d < MIN_SEPARATION) {
        tooClose.push(`${names[i]} ${pay[names[i]]} vs ${names[j]} ${pay[names[j]]} = dE ${d.toFixed(1)}`);
      }
    }
  }
  assert.deepStrictEqual(tooClose, [],
    'these payment methods share one doughnut and are no longer distinguishable ' +
    `(Lab dE76 below ${MIN_SEPARATION}):\n  ${tooClose.join('\n  ')}`);
});

test('no payment method is left without a colour', () => {
  // A seventh method added to the POS but not to the map would fall through to
  // whatever Chart.js picks positionally — the exact bug the map was written to
  // stop, per its own comment.
  const pay = paymentColours();
  for (const method of ['cash', 'card', 'mobile', 'transfer', 'credit', 'voucher']) {
    assert.ok(pay[method], `RETAIL_PAYMENT_METHOD_COLORS has no entry for '${method}'`);
  }
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
