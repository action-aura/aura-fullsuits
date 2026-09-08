/**
 * Aura Retail -- there are exactly FIVE sanctioned themes, and every path into
 * every non-light one is validated the same way.
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
 * comments demanded was actually done: the dark theme is TOKEN VALUES ONLY
 * (the html[data-theme="dark"] block in main.css), the compatibility layer is
 * theme-agnostic (html[data-theme]), and the injected chrome is fully
 * tokenised. Then the owner asked again -- "night mode back" plus "some
 * themes for both mobile and desktop" -- and three more sanctioned themes
 * landed the same way: Night (deep ink, aurora-teal accent), Dusk (violet
 * charcoal, lavender accent) and Sand (warm paper, amber ink -- a LIGHT
 * theme, not a member of the dark family). Every one of them is held to the
 * exact same argument dark established, so this file generalized from
 * "exactly light and dark" to "light, plus a closed BLOCK_THEMES list, each
 * one exercised through the same checks". What it pins is unchanged PER
 * THEME; only the theme count grew:
 *
 *   1. THE STRUCTURE. Every non-light theme is a token-value block, not a
 *      rule set: main.css may not grow a single `html[data-theme="<name>"] ...`
 *      PAINT rule for ANY sanctioned theme, and every colour token the light
 *      palette defines must be redefined in that theme's block. Either drift
 *      reintroduces "a rule only one theme gets", which is the shape the
 *      1.00:1 bug had.
 *   2. THE COMPATIBILITY LAYER IS NOT LIGHT-ONLY. The selectors that used to
 *      be scoped html[data-theme="light"] are a closed, justified list now;
 *      anything new scoped that way is the old hazard growing back.
 *   3. THE UPGRADE PATH. The broken era PERSISTED its state. So: the choice
 *      lives under a NEW key ('aura_theme_v2') the broken era never wrote;
 *      only a name in the THEME_NAMES allowlist (app-shell.js) /
 *      AURA_BOOT_THEME_NAMES (index.html) produces itself (anything else --
 *      absent, corrupt, 'midnight' from the retired accent picker -- lands
 *      light); the legacy keys ('aura_theme', 'aura_app_theme') are never
 *      read and are actively removed. The allowlist has to be declared
 *      TWICE (index.html's boot script runs before app-shell.js can be
 *      imported, so it cannot just read app-shell.js's constant) and this
 *      file pins the two declarations equal BY VALUE, so they cannot
 *      silently drift apart.
 *   4. ONE SANITIZER. Every data-theme write in the shell flows through
 *      ThemeEngine._sanitize(); no source writes a theme-name literal
 *      directly.
 *   5. NO INLINE THEME STYLES. The retired accent picker wrote --bg-dark /
 *      --bg-panel as inline styles on <html> -- near-black literals no
 *      stylesheet could override, applied on every boot. The engine may never
 *      grow that back, and nothing may set data-app-theme again.
 *   6. THE TWO DECLARATIONS OF (3) MUST NEVER SHARE A BARE NAME. index.html
 *      and app-shell.js are both classic (non-module) <script> tags on the
 *      same page, so they share ONE global scope -- a real incident this
 *      generalization pass shipped: index.html declared `var THEME_NAMES`
 *      and app-shell.js separately declared `const THEME_NAMES`, and the
 *      second declaration is a SyntaxError the browser throws before
 *      app-shell.js runs a single statement, which surfaced as "Identifier
 *      'THEME_NAMES' has already been declared" followed by "SubsystemApp is
 *      not defined" -- the whole shell dead, on the real page, while every
 *      check in THIS file (which reads each source file as TEXT and never
 *      loads them together in one JS realm) stayed green. index.html's copy
 *      is therefore named AURA_BOOT_THEME_NAMES, and check (3c) pins that
 *      index.html never redeclares the bare name THEME_NAMES.
 *
 * WHAT THIS FILE CAN NO LONGER CATCH: nothing new from the five-theme
 * generalization -- every check that used to name "dark" specifically now
 * runs once per theme in BLOCK_THEMES, so a broken Night or Dusk block is
 * caught by exactly the same assertion that would have caught a broken Dark
 * block. It DOES still have the gap that let (6) ship: this file parses
 * index.html and app-shell.js as independent text, never as two scripts
 * sharing a real global scope, so it cannot discover an UNANTICIPATED name
 * collision -- only the one it now knows to name. Anyone adding a new
 * top-level `var`/`let`/`const` to index.html should grep app-shell.js's
 * top-level declarations by hand; loading both in one browser page (or a
 * jsdom realm) is the only check that would generalize this properly, and
 * is out of scope for a build-step-free vanilla-JS suite.
 *
 * The COLOURS of every theme are someone else's job: see the light + per-theme
 * palette tiers and the per-theme dark-family rendered-corpus tier in
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

// The one sanctioned set, in the one sanctioned order. Every per-theme check
// below is a loop over these, so a sixth theme (or a dropped one) changes
// coverage by construction rather than by someone remembering to update a list
// here too.
const THEME_NAMES = ['light', 'sand', 'dark', 'night', 'dusk'];
const BLOCK_THEMES = THEME_NAMES.filter((n) => n !== 'light');

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

// ── 1a. every non-light theme is a token block, not a rule set ──────────────
for (const name of BLOCK_THEMES) {
  check(`main.css has no ${name}-scoped PAINT rules — the ${name} theme is token values only`, () => {
    const css = blankCssComments(mainCss);
    // Every selector occurrence that scopes to this theme...
    const selectorRe = new RegExp(`html\\[data-theme="${name}"\\][^{]*\\{`, 'g');
    const occurrences = [...css.matchAll(selectorRe)].map((m) => m[0]);
    assert(occurrences.length >= 1,
      `main.css contains no html[data-theme="${name}"] block at all -- the ${name} theme has no palette`);
    // ...must be exactly the bare token block: `html[data-theme="<name>"] {`.
    // A descendant/compound selector (html[data-theme="<name>"] .foo) is a
    // rule only one theme gets -- the exact drift shape that produced 1.00:1.
    const bareRe = new RegExp(`^html\\[data-theme="${name}"\\]\\s*\\{$`);
    const paintScoped = occurrences.filter((s) => !bareRe.test(s.trim()));
    assert(paintScoped.length === 0,
      `main.css scopes PAINT rules to the ${name} theme:\n    ` + paintScoped.join('\n    ') +
      `\n  ${name} must stay token-values-only. Express the difference as a token ` +
      'the light block also defines, so every theme keeps the same rule set.');
  });
}

// ── 1b. every light colour token is redefined in every block theme ──────────
for (const name of BLOCK_THEMES) {
  check(`every colour token in the light palette is redefined in the ${name} block`, () => {
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
    const theme = tokensIn(`[design-tokens-${name}:begin]`, `[design-tokens-${name}:end]`);
    assert(light.size >= 40, `only ${light.size} light tokens parsed -- the scan is broken`);
    assert(theme.size >= 30, `only ${theme.size} ${name} tokens parsed -- the ${name} block is not a palette`);

    // The prefixes that can carry COLOUR, narrowed to the tokens whose light
    // VALUE actually holds one (--text-size-* etc. share the prefix but hold
    // lengths; duplicating those into a theme would shadow a later light retune).
    const COLOUR_PREFIX = /^--(surface-|text-|state-|accent-|border-|focus-ring-color|elevation-|sheet-scrim)/;
    const HOLDS_COLOUR = /(#[0-9a-f]{3,8}\b|rgba?\()/i;
    // Deliberate exception, with the reason on the token itself: the focus
    // halo's one job is the pre-login canvas, which is dark in EVERY theme.
    const EXEMPT = new Set(['--focus-ring-halo']);
    const colourTokens = [...light.entries()]
      .filter(([n, v]) => COLOUR_PREFIX.test(n) && HOLDS_COLOUR.test(v))
      .map(([n]) => n);
    assert(colourTokens.length >= 30,
      `only ${colourTokens.length} light colour tokens matched -- the value filter is broken`);
    const missing = colourTokens.filter((n) => !EXEMPT.has(n) && !theme.has(n));
    assert(missing.length === 0,
      `light colour token(s) with NO ${name} redefinition:\n    ` + missing.join('\n    ') +
      `\n  A colour the ${name} block forgets stays light-valued under ${name} ` +
      'surfaces. The contrast test would usually also catch the resulting ' +
      'ratio, but this names the missing token directly.');
    console.log(`      (${colourTokens.length} light colour tokens, all covered in ${name})`);
  });
}

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

// ── 3a. the boot script validates, on the new key, through the allowlist ────
//
// index.html's copy of the allowlist is named AURA_BOOT_THEME_NAMES, NOT
// THEME_NAMES -- see (3c) below for why: both this script and app-shell.js
// are plain global-scope <script> tags on the same page, so a bare
// `THEME_NAMES` declared in both is a SyntaxError that kills app-shell.js
// (and with it, the whole shell -- SubsystemApp never gets defined) the
// instant the browser parses it. Node-only tests that load these two files
// separately cannot see that collision at all, which is exactly how it
// shipped once already.
check('the boot script reads only aura_theme_v2 and validates through the AURA_BOOT_THEME_NAMES allowlist', () => {
  assert(/localStorage\.getItem\(\s*['"]aura_theme_v2['"]\s*\)/.test(indexHtml),
    'index.html does not read aura_theme_v2 -- the persisted choice is dead');
  assert(!/localStorage\.getItem\(\s*['"]aura_theme['"]\s*\)/.test(indexHtml),
    'index.html READS the legacy aura_theme key. A theme value persisted by ' +
    'the broken era must never be honoured; the user re-opts-in via the picker.');

  // The allowlist has to be hand-mirrored here (this script runs before any
  // <script src> loads, app-shell.js included), so pin the two declarations
  // equal rather than trusting a copy-paste to stay correct.
  const arrayMatch = /var\s+AURA_BOOT_THEME_NAMES\s*=\s*\[([^\]]*)\]/.exec(indexHtml);
  assert(arrayMatch, 'index.html does not declare `var AURA_BOOT_THEME_NAMES = [...]`');
  const bootNames = arrayMatch[1].split(',').map((s) => s.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean);
  const bootSet = new Set(bootNames);
  const expectedSet = new Set(THEME_NAMES);
  const missingFromBoot = THEME_NAMES.filter((n) => !bootSet.has(n));
  const extraInBoot = bootNames.filter((n) => !expectedSet.has(n));
  assert(missingFromBoot.length === 0 && extraInBoot.length === 0,
    `index.html's AURA_BOOT_THEME_NAMES [${bootNames.join(', ')}] does not match the ` +
    `sanctioned set [${THEME_NAMES.join(', ')}] -- missing: [${missingFromBoot.join(', ')}], ` +
    `extra: [${extraInBoot.join(', ')}]`);

  assert(/AURA_BOOT_THEME_NAMES\.indexOf\(savedTheme\)\s*!==\s*-1\s*\?\s*savedTheme\s*:\s*['"]light['"]/.test(indexHtml),
    'index.html no longer validates the stored value through ' +
    'AURA_BOOT_THEME_NAMES.indexOf(savedTheme) !== -1 with a light fallback. Any weaker ' +
    'read (truthiness, != null, includes) lets a corrupt or legacy value select a theme.');
  assert(!/savedTheme\s*\|\|/.test(indexHtml),
    'index.html falls back on a truthy savedTheme (||) instead of the AURA_BOOT_THEME_NAMES allowlist');
  assert(!/savedTheme\s*\?/.test(indexHtml),
    'index.html has a savedTheme truthiness ternary (savedTheme ? ...) instead of ' +
    'routing through AURA_BOOT_THEME_NAMES.indexOf(...)');
});

// ── 3c. index.html must never redeclare app-shell.js's global THEME_NAMES ───
// This is the regression this section exists to catch: index.html and
// app-shell.js are both parsed as classic (non-module) scripts sharing ONE
// global scope, so `const THEME_NAMES` in app-shell.js plus ANY top-level
// var/let/const of the same bare name in index.html is a SyntaxError the
// browser throws before app-shell.js runs a single statement -- the exact
// shape of "Identifier 'THEME_NAMES' has already been declared" followed by
// "SubsystemApp is not defined". No node-only check of either file alone can
// see this; it only exists where both files share a scope, i.e. the real
// page. So this file pins the two DECLARATIONS at NAME level instead: only
// app-shell.js may own the bare identifier THEME_NAMES at top scope.
check('index.html never declares a global THEME_NAMES (that name belongs to app-shell.js alone)', () => {
  const collision = /\bvar\s+THEME_NAMES\b|\blet\s+THEME_NAMES\b|\bconst\s+THEME_NAMES\b/.exec(indexHtml);
  assert(!collision,
    `index.html declares a global THEME_NAMES ("${collision && collision[0]}"). index.html and ` +
    'app-shell.js are both classic <script> tags sharing one global scope, and app-shell.js ' +
    'already declares `const THEME_NAMES` at top level -- a second declaration of the same ' +
    'bare name here is a SyntaxError that kills app-shell.js entirely before it runs (the shell ' +
    'never boots: "SubsystemApp is not defined"). index.html\'s own copy of the allowlist must ' +
    'use a distinct name (AURA_BOOT_THEME_NAMES).');
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
  assert(/_sanitize\(name\)\s*\{\s*return THEME_NAMES\.includes\(name\) \? name : ['"]light['"];\s*\}/.test(appShell),
    'ThemeEngine._sanitize is missing or weaker than "allowlist match, else light"');

  // app-shell.js's THEME_NAMES must be the exact same set as this file's --
  // see the lineage note at the top of the file for why the allowlist has to
  // be declared twice at all.
  const arrayMatch = /const\s+THEME_NAMES\s*=\s*Object\.freeze\(\s*\[([^\]]*)\]\s*\)/.exec(appShell);
  assert(arrayMatch, 'app-shell.js does not declare `const THEME_NAMES = Object.freeze([...])`');
  const shellNames = arrayMatch[1].split(',').map((s) => s.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean);
  const shellSet = new Set(shellNames);
  const expectedSet = new Set(THEME_NAMES);
  const missingFromShell = THEME_NAMES.filter((n) => !shellSet.has(n));
  const extraInShell = shellNames.filter((n) => !expectedSet.has(n));
  assert(missingFromShell.length === 0 && extraInShell.length === 0,
    `app-shell.js's THEME_NAMES [${shellNames.join(', ')}] does not match the sanctioned set ` +
    `[${THEME_NAMES.join(', ')}] -- missing: [${missingFromShell.join(', ')}], ` +
    `extra: [${extraInShell.join(', ')}]`);

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

// ── 6. every theme is reachable, through the engine, in the sanctioned order ─
//
// UPDATED 2026-09-08 (owner brief): the picker used to offer all five themes
// as one flat list, in THEME_NAMES order. It now shows three PRIMARY themes
// (Day, Calm, Sand) with Night and Dusk moved behind an "Advanced"
// disclosure -- a PRESENTATION change only. THEME_NAMES, `_sanitize()`, and
// `ThemeEngine.themes` (the backing label/dot/edge definition for all five
// palettes) are untouched, so the first half of this check is unchanged: the
// themes object must still carry the exact five, in the exact order. What
// changed is the assertion this used to make about the PICKER -- "flat list
// of five" is no longer true, and weakening it to "at least five appear
// somewhere" would stop catching a theme silently dropped from the picker
// entirely. So this now asserts the stronger, equivalent claim for the new
// shape: PRIMARY_THEMES + ADVANCED_THEMES together are EXACTLY the five
// sanctioned themes, with no overlap and no omission, ADVANCED_THEMES is
// exactly {night, dusk}, and openPicker() actually renders the disclosure
// AND opens it when the active theme is one of the two behind it (so a shop
// already on Night/Dusk lands on a picker showing what it's on, not a
// collapsed section hiding its own current choice).
check('the picker offers all five sanctioned themes -- three primary, two under an Advanced disclosure that opens for a shop already on one -- and toggleTheme delegates', () => {
  const themesBlock = /themes:\s*\{([\s\S]*?)\n  \}/.exec(appShell);
  assert(themesBlock, 'ThemeEngine.themes not found');
  const keys = [...themesBlock[1].matchAll(/^\s{4}(\w+):/gm)].map((m) => m[1]);
  assert(JSON.stringify(keys) === JSON.stringify(THEME_NAMES),
    `ThemeEngine.themes offers [${keys.join(', ')}] in that order; expected exactly ` +
    `[${THEME_NAMES.join(', ')}] in that order. A mismatched set or order means the ` +
    'allowlists have drifted, or the old accent-palette picker is growing back.');

  const parseNames = (s) => s.split(',').map((x) => x.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean);
  const primaryMatch = /PRIMARY_THEMES:\s*Object\.freeze\(\s*\[([^\]]*)\]\s*\)/.exec(appShell);
  const advancedMatch = /ADVANCED_THEMES:\s*Object\.freeze\(\s*\[([^\]]*)\]\s*\)/.exec(appShell);
  assert(primaryMatch,
    'ThemeEngine.PRIMARY_THEMES not found -- the picker no longer declares which themes are primary');
  assert(advancedMatch,
    'ThemeEngine.ADVANCED_THEMES not found -- the picker no longer declares which themes sit under Advanced');
  const primary = parseNames(primaryMatch[1]);
  const advanced = parseNames(advancedMatch[1]);

  assert(primary.length === 3,
    `PRIMARY_THEMES has ${primary.length} entries [${primary.join(', ')}]; the owner brief asks for ` +
    'exactly three primary choices (Day, Calm, Sand).');
  assert(advanced.length === 2,
    `ADVANCED_THEMES has ${advanced.length} entries [${advanced.join(', ')}]; Night and Dusk are the ` +
    'only two that should move behind Advanced.');
  assert(!primary.some((n) => advanced.includes(n)),
    `a theme name appears in BOTH PRIMARY_THEMES [${primary.join(', ')}] and ` +
    `ADVANCED_THEMES [${advanced.join(', ')}] -- it would render twice in the picker.`);
  const combined = [...primary, ...advanced].sort();
  const expected = [...THEME_NAMES].sort();
  assert(JSON.stringify(combined) === JSON.stringify(expected),
    `PRIMARY_THEMES + ADVANCED_THEMES = [${combined.join(', ')}] does not cover exactly the five ` +
    `sanctioned themes [${expected.join(', ')}] -- a theme has gone missing from the picker entirely.`);
  assert(advanced.includes('night') && advanced.includes('dusk'),
    `ADVANCED_THEMES must be exactly night and dusk; found [${advanced.join(', ')}].`);

  // openPicker() must actually build the disclosure, and must open it when
  // the active theme is one of the advanced two -- the requirement that a
  // shop already on Night or Dusk can still see, and change, what it is on.
  const js = blankJsComments(appShell);
  assert(/createElement\(\s*['"]details['"]\s*\)/.test(js),
    'openPicker() no longer builds a <details> disclosure for the advanced themes.');
  assert(/ADVANCED_THEMES\.includes\(\s*this\.current\s*\)/.test(js),
    "openPicker() does not gate the Advanced disclosure's open state on whether the active theme is " +
    'one of the advanced two -- a shop on Night or Dusk would land on a COLLAPSED picker showing ' +
    'neither their own theme nor which one is active.');

  assert(/toggleTheme\(\)\s*\{\s*ThemeEngine\.toggle\(\);\s*\}/.test(appShell),
    'SubsystemApp.toggleTheme no longer delegates to ThemeEngine -- a second ' +
    'write path is a second place validation can be forgotten');
  assert(/ThemeEngine\.openPicker\(\)/.test(appShell),
    'nothing opens the theme picker; some themes exist but are unreachable');
});

console.log(failures === 0
  ? 'PASS: retail_design_theme_safety_test.js'
  : 'FAIL: retail_design_theme_safety_test.js (' + failures + ' failing check(s))');
process.exit(failures === 0 ? 0 : 1);
