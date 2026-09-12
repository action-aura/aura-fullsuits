/**
 * retail_handheld_nav_test.js — the phone nav switch nobody was driving.
 *
 * WHY THIS TEST EXISTS
 * Below 640px, docs/design/phone-ui-redesign.md §2/§7 Step 1 replaces the
 * left icon rail with a fixed bottom tab bar: the rail's .sub-sidebar goes
 * display:none, .sub-tabbar goes display:flex, and the till's slot in that
 * bar is labelled "Till", not "Point of Sale" (that string is the desktop
 * rail's nav-item label only, unrelated markup entirely). Nothing in this
 * suite drove a viewport before this file: retail_shell_chrome_test.js pins
 * the desktop shell's corners with string-level assertions and never sets a
 * width, and no other file here touches the ≤640px breakpoint at all.
 *
 * That gap already cost real time. An automated pass was pointed at 360x640
 * and told to find "Point of Sale". It found the rail -- present in the DOM,
 * as it always is, just display:none at that width -- and reported the
 * till unreachable on a handheld. It was reachable the whole time, one tap
 * away on the tab bar, under a label the check never looked for. A reader of
 * that report would reasonably conclude the handheld layout was broken; it
 * was the check that was blind to the surface it was supposed to be looking
 * at. ENGINEERING.md section 1's shape 3 names this exactly: a pass
 * condition ("Point of Sale is present") that a working implementation can
 * fail and a broken one cannot even trigger, because the element it wants
 * is sitting right there, inert.
 *
 * NO BROWSER IS AVAILABLE TO THIS SUITE (see CLAUDE.md -- vanilla JS, no
 * build step, no headless-browser dependency is going to be introduced for
 * one test file), so this cannot drive a real 360x640 or 412x915 viewport
 * itself. What it CAN do is read the two files that jointly decide this
 * behaviour -- app-shell.js (what renders) and main.css (what a browser
 * does with it at that width) -- and pin the facts that were true when this
 * file was written, measured directly in a running till at both sizes:
 *
 *   rail computed display : none        tab bar computed display : flex
 *   tab labels             : Home, Stock, Till, Customers, More
 *   horizontal scroll      : none at either size
 *   page height            : exactly the viewport height, 0px below the fold
 *
 * Those numbers are not re-derived here -- there is nothing in Node to
 * derive them with -- they are the reason the source-level assertions below
 * are worth pinning at all, not something this file can re-measure itself.
 * A real Playwright pass against 360x640/412x915 remains the only way to
 * catch a REGRESSION in the computed values; this file's job is narrower:
 * make sure the source facts those computed values depend on cannot drift
 * un-noticed (a renamed label, a moved breakpoint, a deleted hide rule)
 * while nobody happens to be looking at a phone.
 *
 * WHAT WOULD DEFEAT A LAZIER VERSION OF THIS FILE
 * A check that just greps main.css for ".sub-tabbar" and "display: flex"
 * anywhere in the file is exactly the "stray rule elsewhere" trap: this
 * stylesheet has THREE separate `@media (max-width: 640px)` blocks (a
 * :root token override, a visually-hidden group-label rule, and this one),
 * and .sub-tabbar's OWN default rule earlier in the file is display:none.
 * A global substring match can be satisfied by any of the wrong blocks, or
 * by the default rule and the override rule both being true in ways that
 * say nothing about each other. Every CSS assertion below therefore locates
 * the SPECIFIC max-width:640px block that actually mentions both
 * .sub-tabbar and .sub-sidebar, by brace-counting to that block's own
 * closing brace, and reads properties only from inside it.
 *
 * Standalone Node, no test framework, no new dependency -- this frontend has
 * no build step and this suite has no browser:
 *
 *   node products/retail/tests/retail_handheld_nav_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const APP_SHELL_FILE = path.join(FRONTEND, 'app-shell.js');
const MAIN_CSS_FILE = path.join(FRONTEND, 'css', 'main.css');

/* Strip CSS comments the way a browser does -- first close wins -- so a
   stray "{" or "}" mentioned in prose can never be mistaken for real
   structure. Same approach as retail_design_css_parse_test.js; duplicated
   rather than imported because this file has no shared-helpers module to
   pull it from and a four-line function is not worth inventing one. */
function stripCssComments(text) {
  let out = '';
  let i = 0;
  for (;;) {
    const start = text.indexOf('/*', i);
    if (start < 0) { out += text.slice(i); return out; }
    out += text.slice(i, start);
    const end = text.indexOf('*/', start + 2);
    const body = end < 0 ? text.slice(start) : text.slice(start, end + 2);
    out += body.replace(/[^\n]/g, ' ');
    if (end < 0) return out;
    i = end + 2;
  }
}

/* Every `@media (max-width: 640px) { ... }` block in the stylesheet, found
   by brace-counting from each header to ITS OWN matching close -- not the
   next "}" a naive regex would stop at, which would truncate this file's
   block (it nests a @keyframes and a @media of its own) at the first
   unrelated inner rule. */
function findAllMax640Blocks(strippedText) {
  const HEADER_RE = /@media\s*\(\s*max-width\s*:\s*640px\s*\)/g;
  const blocks = [];
  let m;
  while ((m = HEADER_RE.exec(strippedText))) {
    const braceStart = strippedText.indexOf('{', m.index + m[0].length);
    if (braceStart < 0) continue;
    let depth = 1;
    let i = braceStart + 1;
    for (; i < strippedText.length; i++) {
      if (strippedText[i] === '{') depth++;
      else if (strippedText[i] === '}') { depth--; if (depth === 0) break; }
    }
    blocks.push({ headerIndex: m.index, inner: strippedText.slice(braceStart + 1, i) });
    HEADER_RE.lastIndex = i + 1;
  }
  return blocks;
}

/* Of however many max-width:640px blocks exist, the phone-nav one is the
   ONE that actually governs .sub-tabbar and .sub-sidebar. Picking it by
   content rather than by position (first match, last match, nth match) is
   what keeps this file correct if the stylesheet grows a fourth block
   before or after it. */
function findPhoneNavBlock(strippedText) {
  const blocks = findAllMax640Blocks(strippedText);
  return blocks.find((b) => /\.sub-tabbar\b/.test(b.inner) && /\.sub-sidebar\b/.test(b.inner)) || null;
}

/* ── check 1 ── the tab bar's section list still owns all four slots ────── */
function testTabBarSectionListOwnsAllFourSlots() {
  const src = fs.readFileSync(APP_SHELL_FILE, 'utf8');
  const m = /_TAB_BAR_SECTIONS:\s*\[([^\]]*)\]/.exec(src);
  assert.ok(m,
    'app-shell.js no longer declares _TAB_BAR_SECTIONS as an array literal: ' +
    'nothing tells the bottom bar which sections are its four mapped slots ' +
    'versus the More sheet, so a shopper on a phone could lose any of them ' +
    'without a single string in this file changing');
  const ids = [...m[1].matchAll(/'([^']+)'/g)].map((x) => x[1]);
  for (const required of ['dashboard', 'products', 'pos', 'customers']) {
    assert.ok(ids.includes(required),
      `_TAB_BAR_SECTIONS is missing '${required}': a shopper on a phone loses ` +
      'that entire destination from the bottom bar with no other way back to ' +
      'it, since the rail that used to reach it is display:none below 640px');
  }
  console.log('PASS testTabBarSectionListOwnsAllFourSlots');
}

/* ── check 2 ── the till's phone-tab label is "Till", not the rail's label ── */
function testTillTabLabelIsTillNotPointOfSale() {
  const src = fs.readFileSync(APP_SHELL_FILE, 'utf8');
  const m = /tabHTML\(\s*'pos'\s*,\s*'[^']*'\s*,\s*'([^']*)'/.exec(src);
  assert.ok(m,
    "app-shell.js no longer calls tabHTML('pos', ...) to build the till's " +
    'bottom-tab slot: the phone tab bar has no till destination at all, and ' +
    'a shopper on a phone cannot ring anything up');
  assert.strictEqual(m[1], 'Till',
    `the pos slot in the phone tab bar is labelled '${m[1]}', not 'Till': ` +
    'both an automated pass and a shopper on a phone key off this exact ' +
    'visible label to find the till -- "Point of Sale" is the DESKTOP rail\'s ' +
    "nav-item label only, on separate markup, and this is the same mismatch " +
    "that once made a working phone till get reported as unreachable");
  console.log('PASS testTillTabLabelIsTillNotPointOfSale');
}

/* ── check 3 ── the 640px block hides the rail and reveals the tab bar ──── */
function testPhoneBreakpointHidesRailAndRevealsTabBar() {
  const stripped = stripCssComments(fs.readFileSync(MAIN_CSS_FILE, 'utf8'));
  const block = findPhoneNavBlock(stripped);
  assert.ok(block,
    'main.css has no max-width:640px block that mentions both .sub-tabbar ' +
    'and .sub-sidebar: without one, neither the rail hides nor the tab bar ' +
    'appears on a phone, and every destination -- the till included -- stays ' +
    'behind a rail nobody on a touchscreen can see or tap');

  const before = stripped.slice(0, block.headerIndex);
  assert.ok(/\.sub-tabbar\s*\{[^}]*display\s*:\s*none/.test(before),
    'main.css does not hide .sub-tabbar by default outside the 640px block: ' +
    'the fixed bottom bar would render on desktop and tablet too, permanently ' +
    'covering content nobody on those screens asked to give up');

  const tabbarRule = /\.sub-tabbar\s*\{([^}]*)\}/.exec(block.inner);
  assert.ok(tabbarRule && /display\s*:\s*flex/.test(tabbarRule[1]),
    'the phone-nav 640px block does not set .sub-tabbar to display:flex: a ' +
    'phone user would get neither the rail (hidden by the default rule) nor ' +
    'the tab bar (never revealed), leaving no way to navigate at all');

  const sidebarRule = /\.sub-sidebar\s*\{([^}]*)\}/.exec(block.inner);
  assert.ok(sidebarRule && /display\s*:\s*none/.test(sidebarRule[1]),
    'the phone-nav 640px block does not hide .sub-sidebar: the desktop rail ' +
    'would sit on screen at the same time as the tab bar, doubling the nav ' +
    'and eating the one-thumb reach space the till button depends on');

  console.log('PASS testPhoneBreakpointHidesRailAndRevealsTabBar');
}

/* ── check 4 ── the rail is hidden, never removed ────────────────────────── */
function testSidebarMarkupStillRendersUnconditionally() {
  const src = fs.readFileSync(APP_SHELL_FILE, 'utf8');
  const tag = '<aside class="sub-sidebar" id="sub-sidebar">';
  const idx = src.indexOf(tag);
  assert.ok(idx >= 0,
    '_renderShell no longer emits the sidebar\'s opening tag: the rail would ' +
    'be gone from the DOM outright rather than merely hidden by CSS, so a ' +
    'later fix that only touches the stylesheet could never bring it back');

  // A ternary or && guard placed just ahead of the tag would make it render
  // only sometimes; a "?" in the few characters immediately before it is
  // the one cheap, reliable tell for that from source text alone.
  const lookback = src.slice(Math.max(0, idx - 80), idx);
  assert.ok(!lookback.includes('?'),
    'the sidebar\'s opening tag now sits behind a conditional: an operator ' +
    'whose role or state makes that condition false gets no rail at all, and ' +
    'if a future CSS change ever mis-hides the tab bar too, that operator is ' +
    'left with no navigation on screen and no error to explain why -- the ' +
    'exact silent failure this test exists to catch, one layer earlier');
  console.log('PASS testSidebarMarkupStillRendersUnconditionally');
}

/* ── check 5 ── anti-vacuity: the matched block is the real one, not a stub ── */
function testPhoneNavBlockIsNotTriviallyEmpty() {
  const stripped = stripCssComments(fs.readFileSync(MAIN_CSS_FILE, 'utf8'));
  const block = findPhoneNavBlock(stripped);
  assert.ok(block, 'no phone-nav 640px block found -- see check 3 for the impact');
  assert.ok(block.inner.length > 200,
    `the matched max-width:640px block is only ${block.inner.length} ` +
    'characters long -- too small to be the real phone-nav block. A check ' +
    'that matched an empty or near-empty block could report every property ' +
    'above as absent-but-not-contradicted and pass for the wrong reason, ' +
    'hiding a real regression behind a technically-true empty diff');
  console.log('PASS testPhoneNavBlockIsNotTriviallyEmpty');
}

const tests = [
  testTabBarSectionListOwnsAllFourSlots,
  testTillTabLabelIsTillNotPointOfSale,
  testPhoneBreakpointHidesRailAndRevealsTabBar,
  testSidebarMarkupStillRendersUnconditionally,
  testPhoneNavBlockIsNotTriviallyEmpty,
];

let failed = 0;
for (const t of tests) {
  try {
    t();
  } catch (err) {
    failed++;
    console.error(`FAIL ${t.name} -- ${err.message}`);
  }
}
if (failed) {
  console.error(`\n${failed} of ${tests.length} checks failed`);
  process.exit(1);
}
console.log(`\nPASS retail_handheld_nav_test.js -- ${tests.length} checks`);
