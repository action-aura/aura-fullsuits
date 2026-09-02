/**
 * Aura Retail -- there are exactly TWO themes, and every path into the dark one
 * is validated.
 *
 * ── LINEAGE: THIS FILE REPLACES retail_design_single_theme_test.js ──────────
 * Its predecessor pinned "there is exactly ONE theme": the boot script FORCED
 * light, no source could write data-theme="dark", and toggleTheme() was a
 * repair no-op. That was the correct guard for its era, because the dark state
 * was BROKEN BY CONSTRUCTION: the compatibility layer in main.css was scoped
 * to [data-theme="light"] while the JS-injected chrome carried dark-HUD white
 * literals, so a document set to "dark" rendered .ret-table white-on-white at
 * 1.00:1 -- and a PERSISTED 'aura_theme' value meant a terminal where anyone
 * had ever tapped the toggle booted straight into that on upgrade day.
 *
 * The owner asked for dark mode back (2026-09), and the work the old guard's
 * comments demanded was actually done: the dark theme is now TOKEN VALUES ONLY
 * (the html[data-theme="dark"] block in main.css), the compatibility layer is
 * theme-agnostic (html[data-theme]), and the injected chrome is fully
 * tokenised. So the old assertions are deliberately retired, and what this
 * file can NO LONGER catch is stated plainly: it no longer refuses the dark
 * state itself. What it pins instead is the set of properties that made
 * re-enabling dark safe -- because each of them is exactly one careless edit
 * away from recreating the original incident:
 *
 *   1. THE STRUCTURE. Dark is a token-value block, not a rule set: main.css
 *      may not grow a single `html[data-theme="dark"] ...` PAINT rule, and
 *      every colour token the light palette defines must be redefined in the
 *      dark block. Either drift reintroduces "a rule only one theme gets",
 *      which is the shape the 1.00:1 bug had.
 *   2. THE COMPATIBILITY LAYER IS NOT LIGHT-ONLY. The selectors that used to
 *      be scoped html[data-theme="light"] are a closed, justified list now;
 *      anything new scoped that way is the old hazard growing back.
 *   3. THE UPGRADE PATH. The broken era PERSISTED its state. So: the choice
 *      lives under a NEW key ('aura_theme_v2') the broken era never wrote;
 *      only the EXACT string 'dark' produces dark (anything else -- absent,
 *      corrupt, 'midnight' from the retired accent picker -- lands light);
 *      the legacy keys ('aura_theme', 'aura_app_theme') are never read and
 *      are actively removed.
 *   4. ONE SANITIZER. Every data-theme write in the shell flows through
 *      ThemeEngine._sanitize(); no source writes the 'dark' literal directly.
 *   5. NO INLINE THEME STYLES. The retired accent picker wrote --bg-dark /
 *      --bg-panel as inline styles on <html> -- near-black literals no
 *      stylesheet could override, applied on every boot. The engine may never
 *      grow that back, and nothing may set data-app-theme again.
 *
 * The COLOURS of both themes are someone else's job: see the light+dark
 * palette tiers and the dark rendered-corpus tier in
 * retail_design_contrast_test.js.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_design_theme_safety_test.js
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
const mainCss = read('css/main.css');

/* Length-preserving comment blanking, same trick as the tokens test: the scan
   must not read prose as selectors, but marker positions must survive. */
function blankCssComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
}
function blankJsComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:])\/\/[^\n]*/g, (m, pre) => pre + ' '.repeat(m.length - pre.length));
}

// ── anti-vacuity: the files must be the ones we think they are ───────────────
check('the fixture actually loaded the real files', () => {
  assert(indexHtml.length > 500, 'index.html is implausibly small');
  assert(appShell.length > 5000, 'app-shell.js is implausibly small');
  assert(mainCss.length > 50000, 'main.css is implausibly small');
  assert(/data-theme/.test(indexHtml),
    'index.html mentions no data-theme at all -- this test is asserting about nothing');
});

// ── 1a. dark is a token block, not a rule set ────────────────────────────────
check('main.css has no dark-scoped PAINT rules — the dark theme is token values only', () => {
  const css = blankCssComments(mainCss);
  // Every selector occurrence that scopes to the dark theme...
  const occurrences = [...css.matchAll(/html\[data-theme="dark"\][^{]*\{/g)].map((m) => m[0]);
  assert(occurrences.length >= 1,
    'main.css contains no html[data-theme="dark"] block at all -- the dark theme has no palette');
  // ...must be exactly the bare token block: `html[data-theme="dark"] {`.
  // A descendant/compound selector (html[data-theme="dark"] .foo) is a rule
  // only one theme gets -- the exact drift shape that produced 1.00:1.
  const paintScoped = occurrences.filter((s) => !/^html\[data-theme="dark"\]\s*\{$/.test(s.trim()));
  assert(paintScoped.length === 0,
    'main.css scopes PAINT rules to the dark theme:\n    ' + paintScoped.join('\n    ') +
    '\n  Dark must stay token-values-only. Express the difference as a token ' +
    'the light block also defines, so both themes keep the same rule set.');
});

// ── 1b. every light colour token is redefined in dark ────────────────────────
check('every colour token in the light palette is redefined in the dark block', () => {
  const tokensIn = (begin, end) => {
    const b = mainCss.indexOf(begin);
    const e = mainCss.indexOf(end);
    assert(b !== -1 && e > b, `markers ${begin}/${end} missing from main.css`);
    // Slice starts mid-comment (the marker lives in the header comment), so
    // drop everything up to the header's closer before parsing.
    let block = mainCss.slice(b, e);
    const close = block.indexOf('*/');
    if (close !== -1) block = block.slice(close + 2);
    block = block.replace(/\/\*[\s\S]*?\*\//g, '');
    const out = new Map();
    for (const m of block.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/gi)) out.set(m[1], m[2].trim());
    return out;
  };
  const light = tokensIn('[design-tokens:begin]', '[design-tokens:end]');
  const dark = tokensIn('[design-tokens-dark:begin]', '[design-tokens-dark:end]');
  assert(light.size >= 40, `only ${light.size} light tokens parsed -- the scan is broken`);
  assert(dark.size >= 30, `only ${dark.size} dark tokens parsed -- the dark block is not a palette`);

  // The prefixes that can carry COLOUR, narrowed to the tokens whose light
  // VALUE actually holds one (--text-size-* etc. share the prefix but hold
  // lengths; duplicating those into dark would shadow a later light retune).
  const COLOUR_PREFIX = /^--(surface-|text-|state-|accent-|border-|focus-ring-color|elevation-|sheet-scrim)/;
  const HOLDS_COLOUR = /(#[0-9a-f]{3,8}\b|rgba?\()/i;
  // Deliberate exception, with the reason on the token itself: the white
  // focus halo's one job is the pre-login canvas, which is dark in BOTH themes.
  const EXEMPT = new Set(['--focus-ring-halo']);
  const colourTokens = [...light.entries()]
    .filter(([n, v]) => COLOUR_PREFIX.test(n) && HOLDS_COLOUR.test(v))
    .map(([n]) => n);
  assert(colourTokens.length >= 30,
    `only ${colourTokens.length} light colour tokens matched -- the value filter is broken`);
  const missing = colourTokens.filter((n) => !EXEMPT.has(n) && !dark.has(n));
  assert(missing.length === 0,
    'light colour token(s) with NO dark redefinition:\n    ' + missing.join('\n    ') +
    '\n  A colour the dark block forgets stays light-valued under dark ' +
    'surfaces. The contrast test would usually also catch the resulting ' +
    'ratio, but this names the missing token directly.');
  console.log(`      (${colourTokens.length} light colour tokens, all covered)`);
});

// ── 2. the light-only compatibility scoping is a closed list ─────────────────
check('html[data-theme="light"] scoping is limited to the justified closed list', () => {
  const css = blankCssComments(mainCss);
  const uses = [...css.matchAll(/html\[data-theme="light"\][^,{]*/g)].map((m) => m[0].trim());
  // Each survivor is justified where it stands:
  //   * the bare token-bridge arm (identical to :root; kept so a stale higher-
  //     specificity value cannot resurrect -- see its comment in main.css);
  //   * the two .auth-* rules: the auth overlay sits on the pre-login dark
  //     canvas, which deliberately stays dark in both themes, so its LIGHT
  //     variant genuinely is light-only styling.
  const allowed = [
    /^html\[data-theme="light"\]$/,
    /^html\[data-theme="light"\]\s+\.auth-overlay$/,
    /^html\[data-theme="light"\]\s+\.auth-card$/,
  ];
  const strays = uses.filter((u) => !allowed.some((re) => re.test(u)));
  assert(uses.length >= 3,
    `only ${uses.length} html[data-theme="light"] occurrences found -- the scan is broken ` +
    '(the token bridge and two auth rules are known to exist)');
  assert(strays.length === 0,
    'new LIGHT-ONLY scoped rule(s) in main.css:\n    ' + strays.join('\n    ') +
    '\n  A rule only one theme gets is how the original dark theme lost the ' +
    'whole compatibility layer and rendered white-on-white. Scope shared ' +
    'chrome as html[data-theme], and express theme differences as tokens.');
});

// ── 3a. the boot script validates, on the new key only ───────────────────────
check('the boot script reads only aura_theme_v2 and exact-matches "dark"', () => {
  assert(/localStorage\.getItem\(\s*['"]aura_theme_v2['"]\s*\)/.test(indexHtml),
    'index.html does not read aura_theme_v2 -- the persisted choice is dead');
  assert(!/localStorage\.getItem\(\s*['"]aura_theme['"]\s*\)/.test(indexHtml),
    'index.html READS the legacy aura_theme key. A dark value persisted by ' +
    'the broken era must never be honoured; the user re-opts-in via the picker.');
  assert(/===\s*['"]dark['"]\s*\?\s*['"]dark['"]\s*:\s*['"]light['"]/.test(indexHtml),
    'index.html no longer exact-matches the stored value against "dark" with a ' +
    'light fallback. Any weaker read (truthiness, != null, includes) lets a ' +
    'corrupt or legacy value select a theme.');
});

// ── 3b. the legacy keys stay banished ────────────────────────────────────────
check('legacy theme keys are removed and never written', () => {
  assert(/localStorage\.removeItem\(\s*['"]aura_theme['"]\s*\)/.test(indexHtml),
    'index.html should still clear the stale aura_theme key');
  assert(/localStorage\.removeItem\(\s*['"]aura_app_theme['"]\s*\)/.test(appShell),
    'app-shell.js should clear the retired accent-picker key aura_app_theme ' +
    '(its stored value is "midnight" on most installs -- an invitation to rewire)');
  const js = blankJsComments(appShell);
  assert(!/setItem\(\s*['"]aura_theme['"]/.test(js) && !/setItem\(\s*['"]aura_app_theme['"]/.test(js),
    'app-shell.js writes a legacy theme key');
  assert(/KEY:\s*['"]aura_theme_v2['"]/.test(appShell),
    'ThemeEngine.KEY is not aura_theme_v2 -- persistence moved off the v2 key');
});

// ── 4. one sanitizer, and every attribute write flows through it ─────────────
check('every data-theme write is the boot validator or ThemeEngine\'s sanitizer output', () => {
  assert(/_sanitize\(name\)\s*\{\s*return name === ['"]dark['"] \? ['"]dark['"] : ['"]light['"];\s*\}/.test(appShell),
    'ThemeEngine._sanitize is missing or weaker than "exact-match dark, else light"');

  const js = blankJsComments(appShell);
  const writes = [...js.matchAll(/setAttribute\(\s*['"]data-theme['"]\s*,\s*([^)]+)\)/g)].map((m) => m[1].trim());
  assert(writes.length >= 2,
    `only ${writes.length} data-theme write(s) found in app-shell.js -- the scan is broken ` +
    '(apply() and init() are known to write it)');
  // Allowed arguments: the two locals that are, by the assertions below,
  // always _sanitize() output. A string literal -- 'dark' especially -- or any
  // other expression is a write that bypasses the sanitizer.
  const allowed = new Set(['theme', 'this.current']);
  const strays = writes.filter((w) => !allowed.has(w));
  assert(strays.length === 0,
    'data-theme write(s) that bypass the sanitizer:\n    setAttribute(data-theme, ' +
    strays.join(')\n    setAttribute(data-theme, ') + ')');
  assert(/const theme = this\._sanitize\(name\)/.test(appShell),
    "apply()'s `theme` local is no longer _sanitize() output");
  assert(/this\.current = this\._sanitize\(saved\)/.test(appShell),
    "init()'s this.current is no longer _sanitize() output");
});

// ── 5. the inline-style era stays dead ───────────────────────────────────────
check('ThemeEngine writes no inline styles and nothing sets data-app-theme', () => {
  const js = blankJsComments(appShell);
  assert(!/setProperty\(\s*['"]--bg-/.test(js),
    'something writes --bg-* inline again -- the accent-picker hazard ' +
    '(near-black literals on <html> that no stylesheet can override) is back');
  assert(!/setAttribute\(\s*['"]data-app-theme['"]/.test(js),
    'something sets data-app-theme again; the [data-app-theme] rules in ' +
    'main.css are retained only as protection against a STALE cached shell');
});

// ── 6. both themes are reachable, through the engine ─────────────────────────
check('the picker offers exactly light and dark, and toggleTheme delegates', () => {
  const themesBlock = /themes:\s*\{([\s\S]*?)\n  \}/.exec(appShell);
  assert(themesBlock, 'ThemeEngine.themes not found');
  const keys = [...themesBlock[1].matchAll(/^\s{4}(\w+):/gm)].map((m) => m[1]);
  assert(keys.length === 2 && keys.includes('light') && keys.includes('dark'),
    `ThemeEngine.themes offers [${keys.join(', ')}]; expected exactly light and dark. ` +
    'More entries means the accent-palette picker is growing back.');
  assert(/toggleTheme\(\)\s*\{\s*ThemeEngine\.toggle\(\);\s*\}/.test(appShell),
    'SubsystemApp.toggleTheme no longer delegates to ThemeEngine -- a second ' +
    'write path is a second place validation can be forgotten');
  assert(/ThemeEngine\.openPicker\(\)/.test(appShell),
    'nothing opens the theme picker; dark exists but is unreachable');
});

console.log(failures === 0
  ? 'PASS: retail_design_theme_safety_test.js'
  : 'FAIL: retail_design_theme_safety_test.js (' + failures + ' failing check(s))');
process.exit(failures === 0 ? 0 : 1);
