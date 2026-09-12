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
  // Anchor re-stated 2026-09-08 when the theme button's title= moved from a
  // bare English literal to `title="${t('Change UI theme')}"` -- i18n.js
  // sweeps [data-i18n] and [data-i18n-ph] only, never a title, so a hardcoded
  // one had no path to Arabic at all. This is an ANCHOR update, not an
  // assertion change: the mutation still injects the same duplicate header AI
  // button and the same check still has to catch it. mutate() requires the
  // anchor to occur exactly once, which is why a stale one fails loudly here
  // instead of silently no-opping.
  const mutated = mutate(SHELL_SRC, [[
    `            <button class="sub-header-btn" onclick="ThemeEngine.openPicker()" title="${'${t(\'Change UI theme\')}'}">${'${window.AuraIcons ? AuraIcons.render(\'palette\', 18) : \'🎨\'}'}</button>
          </div>
        </header>`,
    `            <button class="sub-header-btn" onclick="ThemeEngine.openPicker()" title="${'${t(\'Change UI theme\')}'}">${'${window.AuraIcons ? AuraIcons.render(\'palette\', 18) : \'🎨\'}'}</button>
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

// The theme picker's own panel is built as a TREE (createElement +
// appendChild + createTextNode), not as an innerHTML string, so the string
// stubs above cannot see a single character of it. That is exactly how
// `label.textContent = '🎨 ' + t('Theme')` survived while '🎨' sat in
// RAW_GLYPHS and this test passed: the ban list reached the button that OPENS
// the picker and the More-sheet row, and never the panel one click later --
// the palette therefore appeared twice, in two different visual languages.
//
// This recording stub is deliberately narrow: it records children and
// serialises their text and innerHTML, which is all a glyph ban needs. It is
// NOT a DOM, and nothing else in this file uses it.
function makeRecordingStub(tag) {
  const el = makeElementStub();
  el.tagName = String(tag || 'div').toUpperCase();
  el.children = [];
  el.appendChild = (child) => { el.children.push(child); return child; };
  return el;
}

function serializeTree(el) {
  if (!el) return '';
  if (el.nodeValue !== undefined) return String(el.nodeValue);   // text node
  const own = String(el.textContent || '') + String(el.innerHTML || '');
  return own + (el.children || []).map(serializeTree).join('');
}

function renderThemePickerText() {
  const sandbox = loadShell();
  const appended = [];
  sandbox.document.createElement = (tag) => makeRecordingStub(tag);
  sandbox.document.createTextNode = (text) => ({ nodeValue: String(text) });
  sandbox.document.getElementById = () => null;   // picker not already open
  sandbox.document.body = Object.assign(makeElementStub(), {
    appendChild(el) { appended.push(el); return el; },
  });
  // `const ThemeEngine` is a top-level lexical binding, so it never becomes a
  // property of the global object -- reachable as a bare identifier from an
  // onclick in the browser, and only through the context here.
  const engine = vm.runInContext('ThemeEngine', sandbox);
  assert.ok(engine && typeof engine.openPicker === 'function',
    'app-shell.js did not define a ThemeEngine with an openPicker() -- the fixture is asserting about nothing.');
  engine.current = 'light';
  engine.openPicker();
  const text = appended.map(serializeTree).join('');
  // ANTI-VACUITY: a picker that appended nothing, or whose panel serialised
  // empty, would satisfy every "glyph absent" assertion below having rendered
  // nothing at all -- which is the exact failure this extension exists to end.
  assert.ok(appended.length >= 1, 'ThemeEngine.openPicker() appended nothing to document.body.');
  assert.ok(/Theme/.test(text),
    `The serialised theme picker does not contain its own "Theme" heading, so this scan is ` +
    `not looking at the panel. Got: ${JSON.stringify(text.slice(0, 300))}`);
  assert.ok(/<svg/.test(text),
    `The serialised theme picker contains no <svg> at all -- its heading icon is missing, not ` +
    `merely un-emoji'd. Got: ${JSON.stringify(text.slice(0, 300))}`);
  return text;
}

function testNoRawGlyphsAnywhereInChrome() {
  const { sandbox, App, html } = renderShellHTML();
  const sheetHTML = renderMoreSheetHTML(sandbox, App);
  const pickerText = renderThemePickerText();
  for (const g of RAW_GLYPHS) {
    assert.ok(!html.includes(g), `Raw glyph ${JSON.stringify(g)} still appears in the rendered desktop shell.`);
    assert.ok(!sheetHTML.includes(g), `Raw glyph ${JSON.stringify(g)} still appears in the rendered More sheet.`);
    assert.ok(!pickerText.includes(g),
      `Raw glyph ${JSON.stringify(g)} still appears in ThemeEngine.openPicker()'s panel -- one ` +
      'click away from the button that opens it, which already renders an AuraIcons palette.');
  }
  console.log('PASS: no raw emoji glyphs remain in the shell chrome, the More sheet, or the theme picker');
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
  // This used to regex-match the literal href `brand/aura-app-icon.svg` and pass
  // -- on a link that 404s in the running app. A string match cannot tell "points
  // at the icon" from "points at nothing", which is the only thing worth
  // asserting here, so it pinned the bug in place instead of catching it.
  // Measured 2026-09-12: /brand/aura-app-icon.svg 404, /static/brand/... 200.
  // Resolve the href to a real path and stat it -- that also catches a renamed
  // or deleted icon, which the old regex never could.
  const iconHref = (indexHTML.match(/<link rel="icon"[^>]*href="([^"]+)"/) || [])[1];
  assert.ok(iconHref, 'index.html has no <link rel="icon"> at all.');
  assert.ok(
    iconHref.startsWith('/static/'),
    'index.html\'s favicon href is "' + iconHref + '". Flask serves the frontend ' +
    'folder at /static, so any other href resolves against the page URL and 404s.'
  );
  const iconOnDisk = path.join(FRONTEND_DIR, iconHref.replace('/static/', ''));
  assert.ok(
    fs.existsSync(iconOnDisk),
    'index.html\'s favicon points at ' + iconHref + ', which maps to ' + iconOnDisk +
    ' -- no such file, so the browser gets a 404 and shows a blank tab icon.'
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

// ═════════════════════════════════════════════════════════════════════════════
// 8 — the first-run brand intro (brand/intro.html): plays at most once, only
//     immediately ahead of the create-admin wizard, never on sign-in
//     [MUTATION-PROVED below]
// ═════════════════════════════════════════════════════════════════════════════
//
// Owner instruction (2026-09-08): never on a cash-register cold start or a
// login, restricted to the first-time setup wizard. _openFirstRun() is the
// one seam both init() and checkAuthAndSetup() funnel through for a genuine
// first run (see that method's own comment) and the ONLY caller of
// _maybeShowFirstRunIntro(); a joined device takes the early
// _waitForShopAccount() return above it and never reaches the intro, and
// showReloginModal() (the sign-in screen, and the target of every logout) is
// an entirely separate function that never calls it either. These cases
// drive SubsystemApp._openFirstRun()/.showReloginModal() directly against a
// small live-enough sandbox (same shape as retail_join_shop_modal_test.js's
// vm-loaded real app-shell.js, extended here with a mutable localStorage
// store and an instrumented document.createElement/body so the intro's own
// side effects -- the <iframe> it builds, the flag it persists -- are
// directly observable, not inferred from source text).

function makeIntroSandbox(shellSrc, opts) {
  const o = opts || {};
  const sandbox = loadShell(shellSrc);
  const store = Object.assign({}, o.store);
  sandbox.localStorage = {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  };
  // window.addEventListener -- loadShell()'s base sandbox has no window-level
  // listener API at all (only document's, which is a no-op stub); adding one
  // here lets _playIntroOverlay()'s real keydown-to-skip wiring run to
  // completion instead of throwing before it ever appends the overlay, which
  // would make "was an overlay actually appended" unobservable below.
  sandbox.addEventListener = () => {};
  sandbox.removeEventListener = () => {};
  const createdTags = [];
  sandbox.document.createElement = (tag) => {
    createdTags.push(tag);
    if (o.createElementThrowsFor && tag === o.createElementThrowsFor) {
      throw new Error('simulated createElement(' + tag + ') failure');
    }
    return makeElementStub();
  };
  const appended = [];
  sandbox.document.body = Object.assign(makeElementStub(), {
    appendChild(el) { appended.push(el); return el; },
  });
  // _isJoinedDevice() reads lic.installation_id; {} is falsy for it, so this
  // default fixture is always "not a joined device" -- a genuine first run.
  sandbox.fetch = o.fetch || (() => Promise.resolve({ json: () => Promise.resolve({}) }));
  return { sandbox, App: sandbox.SubsystemApp, store, createdTags, appended };
}

async function testIntroPlaysOnGenuineFirstRunWithFlagUnset() {
  const { App, store, createdTags, appended } = makeIntroSandbox();
  const introKey = App._INTRO_PLAYED_KEY;
  assert.ok(introKey, 'SubsystemApp._INTRO_PLAYED_KEY is not set -- the intro has no persistence key.');
  let setupCalled = false;
  App.showSetupModal = async () => { setupCalled = true; };
  await App._openFirstRun();
  assert.ok(createdTags.includes('iframe'),
    'A genuine first run with the intro flag unset did not create an <iframe> -- the intro never played.');
  assert.ok(appended.some((el) => el && el.id === 'aura-intro-overlay'),
    'No #aura-intro-overlay was appended to document.body on a genuine first run.');
  assert.strictEqual(store[introKey], '1',
    'The intro-played flag was not persisted to localStorage after a genuine first run.');
  assert.ok(setupCalled, 'showSetupModal() was not still called after the intro was triggered.');
  console.log('PASS: the intro plays on a genuine first run with the flag unset, and setup still renders');
}

async function testIntroDoesNotReplayWhenFlagAlreadySet() {
  const probe = makeIntroSandbox();
  const introKey = probe.App._INTRO_PLAYED_KEY;
  const { App, createdTags } = makeIntroSandbox(undefined, { store: { [introKey]: '1' } });
  let setupCalled = false;
  App.showSetupModal = async () => { setupCalled = true; };
  await App._openFirstRun();
  assert.ok(!createdTags.includes('iframe'),
    "The intro replayed even though its played-flag was already '1' -- a re-install onto an " +
    'existing data directory would see it a second time.');
  assert.ok(setupCalled, 'showSetupModal() was not called when the intro was correctly skipped.');
  console.log('PASS: the intro does not replay once its flag is set');
}

async function testSetupStillRendersWhenIntroThrows() {
  const { App } = makeIntroSandbox(undefined, { createElementThrowsFor: 'iframe' });
  let setupCalled = false;
  App.showSetupModal = async () => { setupCalled = true; };
  await App._openFirstRun();
  assert.ok(setupCalled,
    'A throw while building the intro overlay (createElement("iframe")) stranded the user -- ' +
    'showSetupModal() never ran.');
  console.log('PASS: the setup screen still renders even when building the intro overlay throws');
}

// Shared by the plain case and its mutation proof below, so both exercise
// the exact same assertions against whichever app-shell.js source is passed.
async function assertIntroNeverShownOnSignIn(shellSrc) {
  const { App, createdTags, store } = makeIntroSandbox(shellSrc);
  const introKey = App._INTRO_PLAYED_KEY;
  App.showReloginModal('Sign in to your store');
  assert.ok(!createdTags.includes('iframe'),
    'showReloginModal() (the sign-in path) created an <iframe> -- the first-run intro must never ' +
    'appear on sign-in.');
  assert.strictEqual(store[introKey], undefined,
    'showReloginModal() touched the intro-played flag -- the sign-in path must never even look at it.');
}

async function testIntroNeverShownOnSignInPath() {
  await assertIntroNeverShownOnSignIn();
  console.log('PASS: the first-run intro never appears on the sign-in screen');
}

async function testIntroNotOnSignInMutationIsCaught() {
  const mutated = mutate(SHELL_SRC, [[
    `  showReloginModal(msg = 'Your session has expired. Please log in again.') {
    document.getElementById('aura-relogin-modal')?.remove();
    this._authModalOpen = true;`,
    `  showReloginModal(msg = 'Your session has expired. Please log in again.') {
    document.getElementById('aura-relogin-modal')?.remove();
    this._authModalOpen = true;
    this._maybeShowFirstRunIntro();`,
  ]]);
  const msg = await provesMutation(
    'the "intro never shown on sign-in" check survived a mutant that plays the intro from showReloginModal',
    () => assertIntroNeverShownOnSignIn(mutated)
  );
  console.log('PASS: ' + msg);
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
  testIntroPlaysOnGenuineFirstRunWithFlagUnset,
  testIntroDoesNotReplayWhenFlagAlreadySet,
  testSetupStillRendersWhenIntroThrows,
  testIntroNeverShownOnSignInPath,
  testIntroNotOnSignInMutationIsCaught,
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
