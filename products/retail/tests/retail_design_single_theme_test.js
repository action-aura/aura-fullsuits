/**
 * Aura Retail -- there is exactly ONE theme, and nothing may put the document
 * into the other one.
 *
 * ── THE DEFECT THIS PINS ─────────────────────────────────────────────────────
 * The Operational Calm token layer points `:root` and `html[data-theme="light"]`
 * at the SAME light palette. But the large compatibility layer in main.css is
 * still scoped to `[data-theme="light"]`.
 *
 * So a document carrying `data-theme="dark"` gets light surfaces WITHOUT that
 * layer, and the dark theme's white literals that subsystem-retail.js injects
 * then paint white text on white surfaces. Measured at 1.00:1 on `.ret-table`,
 * which takes the products, customers, sales-history and dashboard grids with
 * it -- the app is unusable, not merely ugly.
 *
 * It was reachable two ways, and the second is the dangerous one:
 *   1. the light/dark button in the shell header;
 *   2. a PERSISTED `aura_theme` value in localStorage, read before first paint.
 *
 * (2) means a terminal where anyone ever tapped that button boots straight into
 * the broken state on upgrade day, having touched nothing. That is why the boot
 * script now FORCES light rather than defaulting to it: forcing is what makes
 * an already-broken install repair itself.
 *
 * ── WHY THESE ASSERTIONS AND NOT A CONTRAST CHECK ────────────────────────────
 * A contrast test over the token block cannot see this: every token is fine.
 * The failure is that a whole compatibility LAYER stops applying. The thing
 * worth pinning is therefore the reachability of the dark state, not the colours.
 */
'use strict';

const fs = require('fs');
const path = require('path');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const read = (p) => fs.readFileSync(path.join(FRONTEND, p), 'utf8');

let failures = 0;
function check(name, fn) {
  try {
    fn();
    console.log('PASS: ' + name);
  } catch (err) {
    failures += 1;
    console.error('FAIL: ' + name);
    console.error('  ' + err.message);
  }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

const indexHtml = read('index.html');
const appShell = read('app-shell.js');

// ── anti-vacuity: the files must be the ones we think they are ───────────────
check('the fixture actually loaded the real files', () => {
  assert(indexHtml.length > 500, 'index.html is implausibly small');
  assert(appShell.length > 5000, 'app-shell.js is implausibly small');
  assert(/data-theme/.test(indexHtml),
    'index.html mentions no data-theme at all -- this test is asserting about nothing');
});

// ── 1. nothing may WRITE a non-light theme ──────────────────────────────────
check('no source sets data-theme to anything but light', () => {
  const sources = { 'index.html': indexHtml, 'app-shell.js': appShell };
  const offenders = [];
  for (const [name, src] of Object.entries(sources)) {
    // setAttribute('data-theme', <something not the literal 'light'>)
    const re = /setAttribute\(\s*['"]data-theme['"]\s*,\s*([^)]+)\)/g;
    let m;
    while ((m = re.exec(src)) !== null) {
      const arg = m[1].trim();
      if (!/^['"]light['"]$/.test(arg)) {
        offenders.push(name + ': setAttribute(data-theme, ' + arg + ')');
      }
    }
  }
  assert(offenders.length === 0,
    'these write a theme that is not the literal "light":\n    ' + offenders.join('\n    ') +
    '\n  A document set to "dark" loses the [data-theme="light"] compatibility ' +
    'layer and renders .ret-table white-on-white at 1.00:1.');
});

// ── 2. the boot script must FORCE, not default ──────────────────────────────
check('the pre-paint script forces light rather than reading a saved value', () => {
  assert(/setAttribute\(\s*['"]data-theme['"]\s*,\s*['"]light['"]\s*\)/.test(indexHtml),
    'index.html does not force data-theme="light" before first paint');
  assert(!/localStorage\.getItem\(\s*['"]aura_theme['"]\s*\)/.test(indexHtml),
    'index.html still READS a persisted aura_theme. A terminal where anyone ' +
    'ever selected dark would boot into the broken state on upgrade day.');
});

// ── 3. the stale key must be cleared, so it cannot be revived by accident ────
check('the persisted theme key is removed rather than left lying around', () => {
  assert(/localStorage\.removeItem\(\s*['"]aura_theme['"]\s*\)/.test(indexHtml),
    'index.html should clear the stale aura_theme key');
  assert(!/localStorage\.setItem\(\s*['"]aura_theme['"]/.test(appShell),
    'app-shell.js still PERSISTS a theme choice');
});

// ── 4. the control that produced it is gone from the UI ─────────────────────
check('the light/dark toggle is no longer rendered', () => {
  assert(!/id="aura-theme-toggle"/.test(appShell),
    'the light/dark toggle button is still in the header markup');
});

// ── 5. ...but the accent picker, which is a DIFFERENT thing, still works ─────
//
// Without this the obvious "fix" is to rip out every theme-ish control, which
// would remove a feature that is fine. The palette picker chooses an ACCENT,
// not a light/dark mode.
check('the accent picker is untouched', () => {
  assert(/ThemeEngine\.openPicker\(\)/.test(appShell),
    'the accent palette picker was removed too -- it is a different feature ' +
    'and it was not the problem');
});

// ── 6. toggleTheme survives as a repair, because callers may remain ─────────
check('toggleTheme still exists and repairs instead of switching', () => {
  assert(/toggleTheme\s*\(\s*\)\s*\{/.test(appShell),
    'toggleTheme was deleted outright; a caller elsewhere would now throw');
  const body = appShell.slice(appShell.indexOf('toggleTheme'));
  const end = body.indexOf('\n  },');
  const fn = body.slice(0, end === -1 ? 600 : end);
  assert(!/['"]dark['"]/.test(fn),
    'toggleTheme can still produce the dark state');
});

console.log(failures === 0
  ? 'PASS: retail_design_single_theme_test.js'
  : 'FAIL: retail_design_single_theme_test.js (' + failures + ' failing check(s))');
process.exit(failures === 0 ? 0 : 1);
