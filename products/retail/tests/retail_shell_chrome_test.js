/**
 * retail_shell_chrome_test.js — the desktop shell's "corners" (owner,
 * 2026-09-07, looking at the running till): "the interface has so many
 * stuff in the corners ... why is the 2 ai assistant buttons ... shouldn't
 * the logo be where it says aura retail ... did you create the responsive
 * animated icons to the left?"
 *
 * Measured on the running app before this change: top-left a generic bag
 * emoji + "Retail & POS / ActionAura"; header right = language button, theme
 * button, a "Retail & POS" badge repeating the title already on the left of
 * the SAME header, and a second robot AI button; sidebar footer = AI
 * Assistant / License / Log Out, all raw emoji; sidebar nav icons static.
 * This file pins the replacement:
 *
 *   1. exactly one SubAI.open(...) call reaches the rendered shell (the
 *      sidebar's .sub-ai-btn) -- the header's duplicate is gone. The More
 *      sheet (phone layout, a SEPARATE render call onto document.body, not
 *      part of the desktop shell's innerHTML) legitimately keeps its own
 *      AI row, checked separately below.
 *   2. no .sub-header-badge -- the header no longer repeats the section
 *      title a few px from itself.
 *   3. the brand slot renders AuraIcons.mark(), not a boxed sys.icon emoji.
 *   4. none of the raw glyphs 🤖 🎨 🔑 ⏻ 🌐 remain anywhere in the shell
 *      chrome (sidebar+header) or the More sheet -- AuraIcons.render() now
 *      carries all of them.
 *   5. #sub-header-section IS the header's title now, showing the CURRENT
 *      SECTION ("Dashboard" on first render) -- the separate sub-header-title
 *      <h2> this pass deletes is gone entirely. The id/element itself is
 *      UNCHANGED: _navigate() has always written into #sub-header-section,
 *      and eleven scripts/ops + ui-sweep Playwright scripts (plus
 *      docs/design/phone-ui-redesign.md's own ≤640px plan, now made the ONE
 *      header for every width) wait on that id as the "shell is ready"
 *      selector -- moving it would have broken all of them silently.
 *   6. index.html's favicon points at the real brand icon, not a blank
 *      data: URI.
 *   7. AuraIcons.mark() -- the A and its beacon stroke/fill currentColor (so
 *      they survive every theme, not just the ink the static
 *      brand/aura-mark.svg hardcodes), and two calls never collide on the
 *      ring's gradient id (see icons.js mark()'s own comment on why a
 *      collision there fails silently, not loudly). 2026-09-08: the A's
 *      weight/apex and the beacon's shape (flat diamond, not a soft
 *      radial-gradient spark) both changed -- see icons.js's own MARK
 *      EVOLUTION comment; this file's case 7 was re-anchored to the new
 *      geometry, not deleted.
 *
 * MUTATION-PROVED: case 1 (no header AI button) is re-run against a
 * deliberately mutated copy of app-shell.js that re-adds exactly the header
 * button this pass removed, and is required to fail there.
 *
 * Standalone Node, no framework -- this frontend has no build step
 * (CLAUDE.md):
 *
 *   node products/retail/tests/retail_shell_chrome_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const SHELL_FILE = path.join(FRONTEND_DIR, 'app-shell.js');
const ICONS_FILE = path.join(FRONTEND_DIR, 'icons.js');
const INDEX_FILE = path.join(FRONTEND_DIR, 'index.html');
const SHELL_SRC = fs.readFileSync(SHELL_FILE, 'utf8');
const ICONS_SRC = fs.readFileSync(ICONS_FILE, 'utf8');

const RAW_GLYPHS = ['\u{1F916}', '\u{1F3A8}', '\u{1F511}', '⏻', '\u{1F310}']; // 🤖 🎨 🔑 ⏻ 🌐

// ─────────────────────────────────────────────────────────────────────────────
// Mutation harness (same shape as retail_join_shop_modal_test.js /
// retail_branches_test.js): re-anchor to exact source text, require the
// anchor to occur exactly once so a stale mutation cannot silently no-op.
// ─────────────────────────────────────────────────────────────────────────────

function eolOf(src) { return src.indexOf('\r\n') !== -1 ? '\r\n' : '\n'; }
function nlFor(src) { const eol = eolOf(src); return (s) => s.replace(/\n/g, eol); }

function mutate(src, pairs) {
  const nl = nlFor(src);
  let out = src;
  for (const [rawFind, rawReplace] of pairs) {
    const find = nl(rawFind);
    const replace = nl(rawReplace);
    const hits = out.split(find).length - 1;
    assert.strictEqual(
      hits, 1,
      `Mutation anchor occurs ${hits} time(s), expected exactly 1:\n  ${JSON.stringify(find)}\n\n` +
      'A mutation that no longer applies would let the proof below pass while proving nothing. Re-anchor it.'
    );
    out = out.replace(find, replace);
  }
  return out;
}

async function provesMutation(what, check) {
  let threw = null;
  try {
    await check();
  } catch (err) {
    threw = err;
  }
  assert.ok(
    threw,
    `MUTATION SURVIVED — ${what}\n` +
    'The guard for this passed against a build with the behaviour deliberately broken, so it is ' +
    'not actually watching it. Fix the check, not the mutation.'
  );
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 140)}]`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Lightweight element stub (same shape as retail_nav_groups_test.js) -- no
// real DOM parsing needed: every assertion below is a substring/regex check
// against the innerHTML STRING _renderShell()/_openMoreSheet() build, not a
// tree walk, so a full live DOM (retail_join_shop_modal_test.js's shape) would
// be more machinery than this file needs.
// ─────────────────────────────────────────────────────────────────────────────

function makeElementStub() {
  return {
    innerHTML: '',
    textContent: '',
    innerText: '',
    value: '',
    id: '',
    className: '',
    style: {},
    dataset: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    appendChild() {},
    addEventListener() {},
    removeEventListener() {},
    remove() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelector() { return makeElementStub(); },
    querySelectorAll() { return []; },
  };
}

// Loads the REAL icons.js into the SAME sandbox as app-shell.js, so
// window.AuraIcons is the genuine module (not the `{render:()=>''}` stub
// retail_join_shop_modal_test.js uses for a test that doesn't care about icon
// output) -- this file's whole point is checking what AuraIcons actually
// renders into the shell.
function loadShell(shellSrc) {
  const store = {};
  const sandbox = {
    console,
    t: (s) => s,
    setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    location: { hash: '', href: 'http://localhost/' },
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    document: {
      readyState: 'complete',
      body: makeElementStub(),
      head: { appendChild() {} },
      documentElement: {
        getAttribute() { return null; },
        setAttribute() {},
        style: { setProperty() {} },
      },
      getElementById() { return null; },
      createElement() { return makeElementStub(); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      addEventListener() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(ICONS_SRC, sandbox, { filename: ICONS_FILE });
  assert.ok(sandbox.AuraIcons, 'icons.js did not expose window.AuraIcons');
  vm.runInContext(shellSrc || SHELL_SRC, sandbox, { filename: SHELL_FILE });
  assert.ok(sandbox.SubsystemApp, 'app-shell.js did not expose window.SubsystemApp');
  return sandbox;
}

// Renders the desktop shell exactly the way retail_nav_groups_test.js does
// (App._renderShell into a stubbed #subsystem-shell), with hasAI forced true
// via activeModules -- the plan's own fixture ("hasAI true") -- so the
// sidebar's single surviving AI button is actually present to count.
function renderShellHTML(shellSrc) {
  const sandbox = loadShell(shellSrc);
  const App = sandbox.SubsystemApp;
  App.capabilities = null; // hasCapability() fails OPEN -- see retail_nav_groups_test.js's own note
  App.role = 'admin';
  App.isAdminDevice = true;
  App.activeModules = ['all']; // -> hasAI true
  App.active = 'retail';
  App.branding = {};

  const shellEl = makeElementStub();
  sandbox.document.getElementById = (id) => (id === 'subsystem-shell' ? shellEl : null);
  App._renderShell(App.systems.retail, 'retail');
  return { sandbox, App, html: shellEl.innerHTML };
}

// Renders the More sheet (phone layout) the same way, capturing the sheet's
// own innerHTML off document.body.appendChild -- _openMoreSheet() builds it
// with document.createElement + a plain .innerHTML string assignment (see
// app-shell.js), never a live tree, so recording appended stubs is enough.
function renderMoreSheetHTML(sandbox, App) {
  const appended = [];
  sandbox.document.body = Object.assign(makeElementStub(), {
    appendChild(el) { appended.push(el); },
  });
  sandbox.document.getElementById = (id) => appended.find((el) => el.id === id) || null;
  App.currentSection = 'dashboard';
  App._openMoreSheet();
  const sheet = appended.find((el) => el.id === 'sub-more-sheet');
  assert.ok(sheet, '_openMoreSheet() never appended a #sub-more-sheet element.');
  return sheet.innerHTML;
}

// ═════════════════════════════════════════════════════════════════════════════
// 1 — exactly one SubAI.open( reaches the rendered desktop shell
//     [MUTATION-PROVED below]
// ═════════════════════════════════════════════════════════════════════════════

function countOccurrences(haystack, needle) {
  return haystack.split(needle).length - 1;
}

function assertExactlyOneShellAIButton(html) {
  const n = countOccurrences(html, 'SubAI.open(');
  assert.strictEqual(
    n, 1,
    `Expected exactly 1 SubAI.open( call in the rendered desktop shell (the sidebar .sub-ai-btn), found ${n}. ` +
    'The header used to carry a second, duplicate AI button -- the owner\'s "why is the 2 ai assistant buttons" ' +
    'complaint. HTML:\n' + html
  );
}

function testExactlyOneAIButtonInShell() {
  const { html } = renderShellHTML();
  assertExactlyOneShellAIButton(html);
  console.log('PASS: exactly one SubAI.open( call reaches the rendered desktop shell');
}

// The More sheet is a SEPARATE render call onto document.body, not part of
// the string case 1 checks -- it legitimately keeps its own single AI row
// (mobile layout, see this file's header comment). Checked on its own string
// so a regression that duplicates it there is still caught, without folding
// it into case 1's count and making that assertion mean two different things
// depending on screen width.
function testExactlyOneAIButtonInMoreSheet() {
  const { sandbox, App } = renderShellHTML();
  const sheetHTML = renderMoreSheetHTML(sandbox, App);
  const n = countOccurrences(sheetHTML, 'SubAI.open(');
  assert.strictEqual(n, 1, `Expected exactly 1 SubAI.open( call in the More sheet, found ${n}.`);
  console.log('PASS: the More sheet (mobile layout) still carries its own single AI row');
}

async function testHeaderAIButtonMutationIsCaught() {
  const mutated = mutate(SHELL_SRC, [[
    `            <button class="sub-header-btn" onclick="ThemeEngine.openPicker()" title="Change UI theme">${'${window.AuraIcons ? AuraIcons.render(\'palette\', 18) : \'🎨\'}'}</button>
          </div>
        </header>`,
    `            <button class="sub-header-btn" onclick="ThemeEngine.openPicker()" title="Change UI theme">${'${window.AuraIcons ? AuraIcons.render(\'palette\', 18) : \'🎨\'}'}</button>
            <button class="sub-header-btn" onclick="SubAI.open('${'${systemId}'}')" title="AI Assistant">🤖</button>
          </div>
        </header>`,
  ]]);
  const msg = await provesMutation(
    'the "exactly one AI button in the shell" check survived a mutant that re-adds the header AI button',
    async () => {
      const { html } = renderShellHTML(mutated);
      assertExactlyOneShellAIButton(html);
    }
  );
  console.log('PASS: ' + msg);
}

// ═════════════════════════════════════════════════════════════════════════════
// 2 — no .sub-header-badge anywhere in the rendered shell
// ═════════════════════════════════════════════════════════════════════════════

function testNoHeaderBadge() {
  const { html } = renderShellHTML();
  assert.ok(
    !html.includes('sub-header-badge'),
    'sub-header-badge is still in the rendered shell -- the header repeated the section title a second time, ' +
    'the owner\'s "a Retail & POS badge repeating the title" complaint.'
  );
  console.log('PASS: the header badge is gone');
}

// ═════════════════════════════════════════════════════════════════════════════
// 3 — the brand slot renders AuraIcons.mark(), not a boxed sys.icon emoji
// ═════════════════════════════════════════════════════════════════════════════

function testBrandSlotRendersTheMark() {
  const { html } = renderShellHTML();
  assert.ok(
    html.includes('data-aura-mark'),
    'The brand slot does not contain data-aura-mark -- AuraIcons.mark() did not render there.'
  );
  assert.ok(
    !html.includes('🛍️'),
    'The brand slot still renders the boxed sys.icon emoji instead of the Aura mark.'
  );
  console.log('PASS: the brand slot renders the Aura mark');
}

// ═════════════════════════════════════════════════════════════════════════════
// 4 — none of the raw glyphs remain in the shell chrome or the More sheet
// ═════════════════════════════════════════════════════════════════════════════

function testNoRawGlyphsAnywhereInChrome() {
  const { sandbox, App, html } = renderShellHTML();
  const sheetHTML = renderMoreSheetHTML(sandbox, App);
  for (const g of RAW_GLYPHS) {
    assert.ok(!html.includes(g), `Raw glyph ${JSON.stringify(g)} still appears in the rendered desktop shell.`);
    assert.ok(!sheetHTML.includes(g), `Raw glyph ${JSON.stringify(g)} still appears in the rendered More sheet.`);
  }
  console.log('PASS: no raw emoji glyphs remain in the shell chrome or the More sheet');
}

// ═════════════════════════════════════════════════════════════════════════════
// 5 — the header title shows the current section ("Dashboard" on first render)
// ═════════════════════════════════════════════════════════════════════════════

function testHeaderTitleShowsCurrentSection() {
  const { html } = renderShellHTML();
  // #sub-header-section is UNCHANGED from before this pass -- eleven repo
  // scripts/ops + ui-sweep Playwright scripts and docs/design/
  // phone-ui-redesign.md wait on that exact id as the "shell is ready"
  // selector (see app-shell.js's header comment), so it must still exist
  // and _navigate() must still write into it. What changed is that the
  // separate sub-header-title <h2> that used to sit next to it is gone --
  // #sub-header-section is now the ONLY title.
  const m = html.match(/<span class="sub-header-section" id="sub-header-section">([^<]*)<\/span>/);
  assert.ok(m, 'No #sub-header-section element found in the rendered shell.');
  assert.strictEqual(m[1], 'Dashboard', `Expected #sub-header-section to read "Dashboard" on first render, got "${m[1]}".`);
  // Checked as an actual class/id attribute, not a bare substring: this
  // file's own explanatory comments legitimately name "sub-header-title" in
  // prose (documenting what used to be there), and a substring check would
  // trip over its own documentation rather than a real regression.
  assert.ok(
    !html.includes('id="sub-header-title"'),
    'A sub-header-title element is still in the rendered shell -- it should have been deleted, ' +
    'not left next to the promoted #sub-header-section.'
  );
  console.log('PASS: #sub-header-section is the header\'s only title, showing "Dashboard" on first render');
}

// ═════════════════════════════════════════════════════════════════════════════
// 6 — index.html's favicon points at the real brand icon
// ═════════════════════════════════════════════════════════════════════════════

function testFaviconPointsAtBrandIcon() {
  const indexHTML = fs.readFileSync(INDEX_FILE, 'utf8');
  assert.ok(
    /<link rel="icon" type="image\/svg\+xml" href="brand\/aura-app-icon\.svg">/.test(indexHTML),
    'index.html\'s favicon link does not point at brand/aura-app-icon.svg.'
  );
  assert.ok(
    !/<link rel="icon" href="data:,">/.test(indexHTML),
    'index.html still ships the blank data: URI favicon.'
  );
  console.log('PASS: index.html\'s favicon points at the real brand icon');
}

// ═════════════════════════════════════════════════════════════════════════════
// 7 — AuraIcons.mark(): the A strokes currentColor; two calls never collide
//     on a gradient id
// ═════════════════════════════════════════════════════════════════════════════

function testMarkStrokesCurrentColorAndUsesUniqueGradientIds() {
  const sandbox = loadShell();
  const A = sandbox.AuraIcons;
  const m1 = A.mark(40);
  const m2 = A.mark(40);
  assert.ok(
    /<path d="M 84 178 L 128 70 L 172 178" fill="none" stroke="currentColor" stroke-width="26"/.test(m1),
    'AuraIcons.mark()\'s A path does not stroke currentColor at the 2026-09-08 weight/geometry -- ' +
    'it would not survive a theme change, or it has drifted from the redesigned A.'
  );
  const ring1 = m1.match(/aura-ring-(\d+)/);
  const ring2 = m2.match(/aura-ring-(\d+)/);
  assert.ok(ring1 && ring2, 'AuraIcons.mark() output is missing an aura-ring-N gradient id.');
  assert.notStrictEqual(
    ring1[1], ring2[1],
    'Two AuraIcons.mark() calls produced the SAME gradient id -- a second <svg> on the same page would silently ' +
    'reuse the first fragment\'s gradient (duplicate ids resolve to the first match), not fail loudly.'
  );
  // The beacon (formerly a soft radial-gradient spark) is now a flat
  // currentColor diamond -- no gradient, so no id to collide on. Pin BOTH
  // that the diamond is there and that no radialGradient survives, so a
  // regression back to the soft blur fails loudly here.
  assert.ok(
    /<path d="M 196 45 L 211 60 L 196 75 L 181 60 Z" fill="currentColor" \/>/.test(m1),
    'AuraIcons.mark() does not render the flat currentColor beacon diamond at the ring\'s opening.'
  );
  assert.ok(
    !/radialGradient/.test(m1),
    'AuraIcons.mark() still defines a radialGradient -- the beacon must be a flat, hard-edged shape, not a soft glow.'
  );
  console.log('PASS: AuraIcons.mark() strokes currentColor on the A/beacon and never reuses a gradient id across calls');
}

// ─────────────────────────────────────────────────────────────────────────────

const CASES = [
  testExactlyOneAIButtonInShell,
  testExactlyOneAIButtonInMoreSheet,
  testHeaderAIButtonMutationIsCaught,
  testNoHeaderBadge,
  testBrandSlotRendersTheMark,
  testNoRawGlyphsAnywhereInChrome,
  testHeaderTitleShowsCurrentSection,
  testFaviconPointsAtBrandIcon,
  testMarkStrokesCurrentColorAndUsesUniqueGradientIds,
];

async function main() {
  let failed = 0;
  for (const fn of CASES) {
    try {
      await fn();
    } catch (err) {
      failed += 1;
      console.error('  FAIL ' + fn.name);
      console.error('       ' + (err && err.message ? err.message : err));
    }
  }
  if (failed) {
    console.error(`FAIL: retail_shell_chrome_test.js — ${failed} of ${CASES.length} case(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`PASS: retail_shell_chrome_test.js — ${CASES.length} case(s)`);
  }
}

main();
