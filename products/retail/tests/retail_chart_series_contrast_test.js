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

// FIX ROUND 3 (M3): CIEDE2000, the metric that weights lightness and chroma
// the way the eye does. The CIE76 check above (kept, unchanged) overstates
// how far apart two saturated purples are, which is how five of six tenders
// once sat in one violet arc and still "passed". Standard Sharma/Wu/Dalal
// formulation.
function deltaE2000(hexA, hexB) {
  const rad = Math.PI / 180; const deg = 180 / Math.PI;
  const [L1, a1, b1] = lab(hexA); const [L2, a2, b2] = lab(hexB);
  const C1 = Math.hypot(a1, b1); const C2 = Math.hypot(a2, b2); const Cb = (C1 + C2) / 2;
  const G = 0.5 * (1 - Math.sqrt(Math.pow(Cb, 7) / (Math.pow(Cb, 7) + Math.pow(25, 7))));
  const ap1 = (1 + G) * a1; const ap2 = (1 + G) * a2;
  const Cp1 = Math.hypot(ap1, b1); const Cp2 = Math.hypot(ap2, b2);
  let hp1 = Math.atan2(b1, ap1) * deg; if (hp1 < 0) hp1 += 360;
  let hp2 = Math.atan2(b2, ap2) * deg; if (hp2 < 0) hp2 += 360;
  const dLp = L2 - L1; const dCp = Cp2 - Cp1;
  let dhp = 0;
  if (Cp1 * Cp2 !== 0) { dhp = hp2 - hp1; if (dhp > 180) dhp -= 360; else if (dhp < -180) dhp += 360; }
  const dHp = 2 * Math.sqrt(Cp1 * Cp2) * Math.sin((dhp * rad) / 2);
  const Lbp = (L1 + L2) / 2; const Cbp = (Cp1 + Cp2) / 2;
  let hbp = hp1 + hp2;
  if (Cp1 * Cp2 !== 0) { if (Math.abs(hp1 - hp2) > 180) hbp += hp1 + hp2 < 360 ? 360 : -360; hbp /= 2; }
  const T = 1 - 0.17 * Math.cos((hbp - 30) * rad) + 0.24 * Math.cos(2 * hbp * rad)
    + 0.32 * Math.cos((3 * hbp + 6) * rad) - 0.20 * Math.cos((4 * hbp - 63) * rad);
  const dth = 30 * Math.exp(-Math.pow((hbp - 275) / 25, 2));
  const Rc = 2 * Math.sqrt(Math.pow(Cbp, 7) / (Math.pow(Cbp, 7) + Math.pow(25, 7)));
  const Sl = 1 + (0.015 * Math.pow(Lbp - 50, 2)) / Math.sqrt(20 + Math.pow(Lbp - 50, 2));
  const Sc = 1 + 0.045 * Cbp; const Sh = 1 + 0.015 * Cbp * T;
  const Rt = -Math.sin(2 * dth * rad) * Rc;
  return Math.sqrt(Math.pow(dLp / Sl, 2) + Math.pow(dCp / Sc, 2) + Math.pow(dHp / Sh, 2)
    + Rt * (dCp / Sc) * (dHp / Sh));
}
function lchHue(hex) { const [, a, b] = lab(hex); const h = (Math.atan2(b, a) * 180) / Math.PI; return h < 0 ? h + 360 : h; }
function lchChroma(hex) { const [, a, b] = lab(hex); return Math.hypot(a, b); }
function hslHue(hex) {
  const [r, g, b] = rgb(hex).map((v) => v / 255);
  const mx = Math.max(r, g, b); const mn = Math.min(r, g, b); const d = mx - mn;
  if (!d) return 0;
  let h;
  if (mx === r) h = ((g - b) / d) % 6; else if (mx === g) h = (b - r) / d + 2; else h = (r - g) / d + 4;
  h *= 60;
  return h < 0 ? h + 360 : h;
}
function hueGap(a, b) { const d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d; }

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

/** Each theme's four MEANING tokens (DESIGN.md section 2.2: "Green is money
 *  in and success; red is money out, refusal and danger; amber is a
 *  warning; blue is information. Each meaning has one colour, and no
 *  meaning shares a colour with another"). Read the same way cardGrounds()
 *  reads --surface-panel, from the real CSS, never hand-typed. */
function stateTokens() {
  const out = {};
  for (const [name, marker] of THEMES) {
    const i = CSS.indexOf(marker);
    assert.ok(i >= 0, `main.css has no ${marker} block`);
    const bs = i + marker.length - 1;
    const block = CSS.slice(bs + 1, CSS.indexOf('}', bs));
    const vals = {};
    for (const key of ['success', 'danger', 'warning', 'info']) {
      const m = new RegExp(`--state-${key}-text:\\s*(#[0-9a-fA-F]{3,6})\\s*;`).exec(block);
      assert.ok(m, `${name} declares no --state-${key}-text`);
      vals[key] = m[1].toLowerCase();
    }
    out[name] = vals;
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

// FIX ROUND F4 (this crew now owns this file): DESIGN.md section 2.2 --
// "Green is money in and success; red is money out, refusal and danger;
// amber is a warning; blue is information. Each meaning has one colour,
// and no meaning shares a colour with another." The payment-method donut
// is six categories that are ALL "money in" -- there is no danger tender,
// no warning tender -- so none of the six may equal any theme's own
// success/danger/warning/info text token. This is a NEW check, added
// alongside the two pre-existing ones above, not a replacement for either.
test('no payment-method colour equals a state-meaning token, in any theme', () => {
  const pay = paymentColours();
  const states = stateTokens();
  const collisions = [];
  for (const [method, hex] of Object.entries(pay)) {
    for (const [theme, vals] of Object.entries(states)) {
      for (const [meaning, stateHex] of Object.entries(vals)) {
        if (hex === stateHex) {
          collisions.push(`${method} ${hex} equals ${theme}'s --state-${meaning}-text`);
        }
      }
    }
  }
  assert.deepStrictEqual(collisions, [],
    `these payment-method colours collide with a state-meaning token:\n  ${collisions.join('\n  ')}`);
});

/** Below this CIEDE2000 two tenders in one doughnut read as one. */
const MIN_DE2000 = 15;
/** A tender must sit at least this many degrees of hue from every meaning colour. */
const MIN_STATE_HUE_GAP = 25;
/** "Moderate chroma": above this a slice shouts beside the KPI tiles (old mobile #db33cd measured ~84). */
const MAX_CHROMA = 70;

test('M3: every payment pair is >= 15 CIEDE2000 apart', () => {
  const pay = paymentColours();
  const names = Object.keys(pay);
  assert.ok(names.length >= 6, 'payment map parser went blind');
  const close = [];
  let pairs = 0;
  for (let i = 0; i < names.length; i += 1) {
    for (let j = i + 1; j < names.length; j += 1) {
      pairs += 1;
      const d = deltaE2000(pay[names[i]], pay[names[j]]);
      if (d < MIN_DE2000) close.push(`${names[i]} ${pay[names[i]]} vs ${names[j]} ${pay[names[j]]} = dE00 ${d.toFixed(1)}`);
    }
  }
  assert.strictEqual(pairs, (names.length * (names.length - 1)) / 2, 'pair loop skipped pairs');
  // Anti-vacuity on the metric itself: identical colours are 0, black/white ~100.
  assert.ok(deltaE2000('#123456', '#123456') < 0.001, 'deltaE2000(x, x) must be 0');
  assert.ok(deltaE2000('#000000', '#ffffff') > 90, 'deltaE2000 black/white must be ~100');
  assert.deepStrictEqual(close, [],
    `payment tenders that read as one colour (CIEDE2000 below ${MIN_DE2000}):\n  ${close.join('\n  ')}`);
});

test('M3: every payment hue is >= 25 degrees from every state hue in every theme (LCh and HSL), chroma moderate', () => {
  const pay = paymentColours();
  const states = stateTokens();
  const tooNear = [];
  let compared = 0;
  for (const [method, hex] of Object.entries(pay)) {
    if (lchChroma(hex) > MAX_CHROMA) tooNear.push(`${method} ${hex} chroma ${lchChroma(hex).toFixed(0)} > ${MAX_CHROMA}`);
    for (const [theme, vals] of Object.entries(states)) {
      for (const [meaning, stateHex] of Object.entries(vals)) {
        compared += 1;
        const gl = hueGap(lchHue(hex), lchHue(stateHex));
        const gh = hueGap(hslHue(hex), hslHue(stateHex));
        if (gl < MIN_STATE_HUE_GAP) tooNear.push(`${method} ${hex} is ${gl.toFixed(0)} deg (LCh) from ${theme} --state-${meaning}-text ${stateHex}`);
        if (gh < MIN_STATE_HUE_GAP) tooNear.push(`${method} ${hex} is ${gh.toFixed(0)} deg (HSL) from ${theme} --state-${meaning}-text ${stateHex}`);
      }
    }
  }
  // 6 tenders x 5 themes x 4 meanings.
  assert.strictEqual(compared, Object.keys(pay).length * 5 * 4, 'hue comparison loop is not covering every tender/theme/meaning');
  assert.deepStrictEqual(tooNear, [],
    `payment tenders inside a meaning-colour family or too loud:\n  ${tooNear.join('\n  ')}`);
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
