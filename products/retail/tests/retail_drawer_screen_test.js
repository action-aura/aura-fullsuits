/**
 * retail_drawer_screen_test.js — the cash-drawer surface, rendered.
 *
 * WHY THIS FILE EXISTS
 *
 * frontend/cash-drawer.js has never been in ANY test corpus. It is loaded by
 * index.html and mounted by RetailSystem._renderPOS behind
 * `if (window.CashDrawer)`, and retail_design_render_test.js evaluates
 * subsystem-retail.js ALONE — so that guard is false in the corpus, the drawer
 * bar is never built, and every contrast, focus, i18n and money sweep in this
 * directory has been passing over a file none of them can see.
 *
 * What was living in that blind spot, before this file:
 *
 *   * `color:#a7f3d0` on `background:rgba(16,185,129,0.08)` — a HUD mint on a
 *     near-white till, roughly 1.5:1. Worse than the 2.64:1 grey-on-white and
 *     the 1.48:1 "Held" button that two previous rounds were celebrated for
 *     fixing, on a bar that sits permanently across the top of the POS.
 *   * Hairlines drawn as `rgba(255,255,255,0.06)` — white at 6% alpha, which
 *     is invisible on every surface in the current palette.
 *   * Twenty-nine bare English literals in a product that ships Arabic. Not
 *     one of them was in either catalog, so an Arabic install rendered an
 *     English drawer bar inside an Arabic till.
 *   * `catch (e) { this._session = null; }` — a dropped connection, a 500 and
 *     a permission refusal all rendered as "no cash session open", with an
 *     "Open Shift" button that would fail for the same reason the read did.
 *
 * WHAT THIS FILE ASSERTS, and how each claim is kept from being vacuous:
 *
 *   1. The four bar states are four DIFFERENT screens. Proven by driving
 *      `refresh()` against four different server responses through the real
 *      code path, then asserting the results are pairwise distinct — not by
 *      calling four renderers and trusting they were reached.
 *   2. Every colour in the injected stylesheet is a token that exists in
 *      css/main.css, and every resolved text/background pairing clears AA.
 *   3. Every user-visible string is in BOTH catalogs.
 *   4. A force-closed drawer renders NO amount in its variance cell, and an
 *      ended one renders a `.money` element. Asserted as a pair, because
 *      "no dollar sign" is also what an empty fixture produces.
 *   5. Money is `.money` (tabular figures + rtl.css's direction isolation),
 *      and the sign is carried by a translated WORD as well as by colour.
 *
 * Mutation-proven. Each of these fails this file:
 *   - collapsing 'error' back into 'none' in refresh()
 *   - putting any hex literal back into _injectStyles()
 *   - dropping t() from any rendered string
 *   - printing 0.00 for a force-closed drawer's variance
 *   - replacing this._money(...) with a bare _fmt() string
 *   - dropping the terminal label from any bar state
 *
 * Run: node products/retail/tests/retail_drawer_screen_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const dom = require('./retail_surface_domlite.js');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const RETAIL_JS = path.join(FRONTEND_DIR, 'subsystem-retail.js');
const DRAWER_JS = path.join(FRONTEND_DIR, 'cash-drawer.js');
const MAIN_CSS = path.join(FRONTEND_DIR, 'css', 'main.css');
const RTL_CSS = path.join(FRONTEND_DIR, 'css', 'rtl.css');
const EN = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, 'locales', 'en.json'), 'utf8'));
const AR = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, 'locales', 'ar.json'), 'utf8'));

let checks = 0;
const pass = (msg) => { checks += 1; console.log('  ok  ' + msg); };

// ─────────────────────────────────────────────────────────────────────────────
// Harness
// ─────────────────────────────────────────────────────────────────────────────

function makeElementStub(overrides) {
  const classes = [];
  return Object.assign({
    innerHTML: '', textContent: '', value: '', id: '', className: '', disabled: false,
    style: {}, classes,
    classList: {
      add(c) { if (!classes.includes(c)) classes.push(c); },
      remove(c) { const i = classes.indexOf(c); if (i !== -1) classes.splice(i, 1); },
      toggle(c, on) { if (on) this.add(c); else this.remove(c); },
      contains(c) { return classes.includes(c); },
    },
    appendChild() {}, remove() {},
    getAttribute() { return null; }, setAttribute() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    addEventListener() {}, closest() { return makeElementStub(); },
  }, overrides);
}

/**
 * Load subsystem-retail.js AND cash-drawer.js into ONE sandbox.
 *
 * Both, in that order, because CashDrawer delegates its escaping, its money
 * formatting and its bidi isolation to RetailSystem — which is the point: two
 * spellings of "how this product prints money" is how a drawer bar ends up
 * printing an ASCII hyphen with no tabular figures while the till beside it
 * prints a typographic minus.
 *
 * `t` is identity, exactly as retail_surface_i18n_test.js does it: the
 * rendered text is then the ENGLISH SOURCE STRING, which is what the catalog
 * sweep below needs to look up. A stub that returned Arabic would make every
 * string look translated whether or not it had ever been through t().
 */
function loadDrawer(routes) {
  const els = Object.create(null);
  const appended = [];
  const toasts = [];
  const headStyles = [];

  const getEl = (id) => {
    if (!els[id]) els[id] = makeElementStub({ id });
    return els[id];
  };

  const sandbox = {
    console,
    t: (s) => s,
    fetch: () => Promise.reject(new Error('no network in this test')),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem: () => {} },
    document: {
      getElementById(id) {
        // The style guard has to answer "not yet injected" the first time, or
        // _injectStyles() returns early and the stylesheet is never built.
        if (id === 'cd-styles') return headStyles.length ? headStyles[0] : null;
        if (id === 'ret-styles') return null;
        return getEl(id);
      },
      createElement() { return makeElementStub(); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      head: { appendChild(el) { headStyles.push(el); } },
      body: { appendChild(el) { appended.push(el); }, classList: { toggle() {} } },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
    SubsystemApp: {
      active: 'retail',
      showToast(text, kind) { toasts.push({ text, kind }); },
      hasCapability: () => true,
      _navigate() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(RETAIL_JS, 'utf8'), sandbox, { filename: RETAIL_JS });
  vm.runInContext(fs.readFileSync(DRAWER_JS, 'utf8'), sandbox, { filename: DRAWER_JS });

  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  assert.ok(sandbox.CashDrawer, 'cash-drawer.js did not expose window.CashDrawer');

  const CashDrawer = sandbox.CashDrawer;
  // The ONLY thing replaced is the transport. Every render path below is the
  // product's own.
  CashDrawer._get = async (url) => {
    const hit = Object.keys(routes.get || {}).find((k) => url.startsWith(k));
    if (!hit) throw new Error('unrouted GET ' + url);
    const answer = (routes.get)[hit];
    if (answer instanceof Error) throw answer;
    return answer;
  };
  CashDrawer._post = async (url) => {
    const hit = Object.keys(routes.post || {}).find((k) => url.includes(k));
    if (!hit) throw new Error('unrouted POST ' + url);
    return (routes.post)[hit];
  };

  return { sandbox, CashDrawer, els, appended, toasts, headStyles, getEl };
}

const TERMINAL_ID = '8f0c4a2e-1111-4c3a-9d55-aaaaaaaa1234';
const OTHER_ID = '3b7e91d0-2222-4f18-8e21-bbbbbbbb5678';

const OPEN_SESSION = {
  id: 'sess-open', status: 'open', opened_at: '2026-08-24T09:15:00',
  opening_float: 150, closing_float_counted: null, closing_float_expected: null,
  variance: null, variance_status: 'pending', force_closed: false,
  terminal_id: TERMINAL_ID, terminal_short: '1234', is_this_terminal: true,
  ended_at: null, ended_by: null, approved_at: null, approved_by: null,
};

const CURRENT_OK = {
  status: 'success', data: OPEN_SESSION,
  terminal_id: TERMINAL_ID, terminal_short: '1234', other_terminals_open: 0,
};
const CURRENT_NONE = {
  status: 'success', data: null,
  terminal_id: TERMINAL_ID, terminal_short: '1234', other_terminals_open: 2,
};
const CURRENT_DENIED = {
  status: 'error', code: 403,
  message: 'You do not have permission for this action. Ask your store administrator.',
};

const X_REPORT = {
  status: 'success',
  data: {
    session_id: 'sess-open', status: 'open',
    terminal_id: TERMINAL_ID, terminal_short: '1234', is_this_terminal: true,
    opening_float: 150, cash_sales: 420.5, cash_refunds: 30,
    movements: { float_in: 20, float_out: 5, paid_in: 0, paid_out: 25 },
    expected_cash: 580.5, foreign_terminal_sales: 0, unattributed_sales: 0,
    window_start: '2026-08-24T09:15:00', window_end: '2026-08-24T17:02:00',
  },
};

/** The three history rows that matter, and they must not render alike. */
const HISTORY = {
  status: 'success',
  scope: 'all_terminals',
  may_approve: true,
  terminal_id: TERMINAL_ID, terminal_short: '1234',
  data: [
    // Properly ended, short by three dollars, awaiting approval.
    { id: 'sess-ended', status: 'ended', opened_at: '2026-08-23T09:00:00',
      opening_float: 100, closing_float_expected: 178, closing_float_counted: 175,
      variance: -3, variance_status: 'unverified', force_closed: false,
      terminal_id: TERMINAL_ID, terminal_short: '1234', is_this_terminal: true },
    // FORCE-CLOSED: nobody counted it. Variance UNKNOWN, not zero.
    { id: 'sess-forced', status: 'ended', opened_at: '2026-08-22T09:00:00',
      opening_float: 100, closing_float_expected: null, closing_float_counted: null,
      variance: null, variance_status: 'not_counted', force_closed: true,
      terminal_id: OTHER_ID, terminal_short: '5678', is_this_terminal: false },
    // Counted, balanced exactly, and accepted.
    { id: 'sess-closed', status: 'closed', opened_at: '2026-08-21T09:00:00',
      opening_float: 100, closing_float_expected: 100, closing_float_counted: 100,
      variance: 0, variance_status: 'approved', force_closed: false,
      terminal_id: OTHER_ID, terminal_short: '5678', is_this_terminal: false },
  ],
};

/**
 * The same history, plus the row a real shop ALWAYS has: the drawer that is
 * open right now.
 *
 * WHY THIS IS A SEPARATE FIXTURE. HISTORY above is indexed positionally by
 * three other tests (`rows.length === 3`, `badges.length === 3`, the
 * `[endedRow, forcedRow, closedRow]` destructuring), so growing it would
 * quietly rewrite assertions that are load-bearing elsewhere. This one exists
 * only to reach the fourth branch of `_statusBadge()`.
 *
 * WHY IT HAD TO EXIST AT ALL: `_statusBadge()`'s `pending` arm renders
 * `t('Open')`, and `_statusBadge()` is called from exactly one place -- the
 * history row renderer. With no open row in any fixture, that arm was rendered
 * by nothing in this file, retail_surface_i18n_test.js does not load
 * cash-drawer.js at all, and so the string an Arabic cashier sees on their own
 * live drawer was the one string on this screen that no test could see. Proven
 * by deleting "Open" from en.json: all 29 retail JS test files stayed green.
 */
const HISTORY_WITH_OPEN = {
  ...HISTORY,
  data: [
    { id: 'sess-live', status: 'open', opened_at: '2026-08-24T09:15:00',
      opening_float: 150, closing_float_expected: null, closing_float_counted: null,
      variance: null, variance_status: 'pending', force_closed: false,
      terminal_id: TERMINAL_ID, terminal_short: '1234', is_this_terminal: true },
    ...HISTORY.data,
  ],
};

/** Drive the real refresh() + _renderBar() and hand back the bar's markup. */
async function renderBar(currentResponse) {
  const ctx = loadDrawer({ get: { '/api/sub/retail/cash-sessions/current': currentResponse } });
  await ctx.CashDrawer.refresh();
  const bar = ctx.els['cash-drawer-bar'];
  return { ctx, state: ctx.CashDrawer._state, className: bar.className, html: bar.innerHTML };
}

async function renderModal(open, routes) {
  const ctx = loadDrawer(routes);
  await ctx.CashDrawer.refresh();
  await open(ctx.CashDrawer);
  assert.ok(ctx.appended.length >= 1, 'no modal was appended to document.body');
  const overlay = ctx.appended[ctx.appended.length - 1];
  return { ctx, html: overlay.innerHTML, root: dom.parseFragment(overlay.innerHTML) };
}

// ─────────────────────────────────────────────────────────────────────────────
// 1. FOUR STATES, AND THEY ARE FOUR DIFFERENT SCREENS
// ─────────────────────────────────────────────────────────────────────────────

async function testTheBarHasFourDistinctHonestStates() {
  const open = await renderBar(CURRENT_OK);
  const none = await renderBar(CURRENT_NONE);
  const denied = await renderBar(CURRENT_DENIED);
  const failed = await renderBar(new Error('socket hang up'));

  assert.strictEqual(open.state, 'open');
  assert.strictEqual(none.state, 'none');
  assert.strictEqual(denied.state, 'denied');
  assert.strictEqual(failed.state, 'error');

  // THE REGRESSION. A dropped connection used to render as "no cash session
  // open for this branch" — a confident statement about the drawer, made by
  // code that had just failed to find out anything about the drawer.
  assert.notStrictEqual(failed.html, none.html,
    'a failed load renders identically to "no drawer is open", which is the ' +
    'catch-all this file exists to keep closed');
  assert.notStrictEqual(denied.html, none.html,
    '"you do not operate a drawer" renders identically to "no drawer is open"');

  const bodies = [open.html, none.html, denied.html, failed.html];
  assert.strictEqual(new Set(bodies).size, 4, 'two bar states render the same markup');
  const classes = [open.className, none.className, denied.className, failed.className];
  assert.strictEqual(new Set(classes).size, 4, 'two bar states share a surface class');

  // The failure state offers a retry and does NOT offer to open a shift; the
  // empty state does the opposite. Offering "Open Shift" to somebody whose
  // read just failed is the interface guessing.
  assert.ok(/CashDrawer\.refresh\(\)/.test(failed.html), 'the error state offers no retry');
  assert.ok(!/openOpenShiftModal/.test(failed.html),
    'the error state offers to open a shift, having just failed to read one');
  assert.ok(/openOpenShiftModal/.test(none.html), 'the empty state offers no way to open a shift');
  assert.ok(!/openOpenShiftModal/.test(denied.html),
    'somebody without the capability is offered a button the server will refuse');

  pass('the drawer bar has four distinct states and a failed load is not an empty drawer');
}

async function testEveryBarStateNamesItsTerminal() {
  for (const [label, response] of [['open', CURRENT_OK], ['none', CURRENT_NONE],
                                   ['denied', CURRENT_DENIED]]) {
    const { html } = await renderBar(response);
    const root = dom.parseFragment(html);
    const pills = dom.allElements(root).filter((el) => el.classes.includes('cd-terminal'));
    assert.ok(pills.length >= 1, `the ${label} state does not say which terminal it is about`);
  }
  // The open state names the till the drawer BELONGS to, from the session row,
  // and marks it as this device's.
  const openBar = await renderBar(CURRENT_OK);
  assert.ok(/cd-terminal-mine/.test(openBar.html),
    'an open drawer on this terminal is not marked as this terminal');
  assert.ok(/This terminal/.test(openBar.html), openBar.html);

  // An unidentified device says so rather than printing a confident label.
  const anon = await renderBar({ status: 'success', data: null, terminal_id: null,
                                 terminal_short: null, other_terminals_open: 0 });
  assert.ok(/Unidentified terminal/.test(anon.html),
    'a device with no terminal identity is not disclosed as unidentified');
  pass('every bar state names the terminal, and an unidentified till says so');
}

async function testTheEmptyStateDistinguishesAQuietShopFromABusyOne() {
  const busy = await renderBar(CURRENT_NONE);            // other_terminals_open: 2
  const quiet = await renderBar({ ...CURRENT_NONE, other_terminals_open: 0 });
  assert.ok(/Other terminals have a drawer open/.test(busy.html), busy.html);
  assert.ok(!/Other terminals have a drawer open/.test(quiet.html), quiet.html);
  pass('"no drawer here" says whether other tills are trading');
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. TOKENS AND CONTRAST
// ─────────────────────────────────────────────────────────────────────────────

function drawerStylesheet() {
  const ctx = loadDrawer({ get: {} });
  ctx.CashDrawer._injectStyles();
  assert.strictEqual(ctx.headStyles.length, 1, 'the drawer injected no stylesheet');
  const css = ctx.headStyles[0].textContent;
  assert.ok(css.length > 400, 'the injected stylesheet is suspiciously small');
  return css;
}

function testNoHardcodedColoursInTheDrawerStylesheet() {
  const css = drawerStylesheet();
  const hex = css.match(/#[0-9a-fA-F]{3,8}\b/g) || [];
  const rgb = css.match(/\brgba?\s*\(/g) || [];
  const named = css.match(/:\s*(white|black|red|green|orange|yellow|silver|gray|grey)\b/g) || [];
  assert.deepStrictEqual(hex, [],
    'hex colour literals in the drawer stylesheet: ' + hex.join(', ') +
    ' — this file shipped a near-black HUD palette onto a near-white till exactly ' +
    'this way, and no token test could see it because the literals were not tokens');
  assert.deepStrictEqual(rgb, [], 'rgb()/rgba() literals in the drawer stylesheet');
  assert.deepStrictEqual(named, [], 'named CSS colours in the drawer stylesheet');

  // ANTI-VACUITY: it must actually be setting colours, or "no literals" is
  // true of a stylesheet that styles nothing.
  const varRefs = css.match(/var\(--[a-z0-9-]+\)/g) || [];
  assert.ok(varRefs.length >= 25,
    `only ${varRefs.length} token references in the drawer stylesheet; "no hardcoded ` +
    'colours" would be trivially true of a stylesheet that sets no colours');
  pass(`the drawer stylesheet is ${varRefs.length} token references and zero colour literals`);
}

function testEveryTokenTheDrawerUsesExists() {
  const css = drawerStylesheet();
  const tokens = dom.parseTokens(fs.readFileSync(MAIN_CSS, 'utf8'));
  const used = [...new Set((css.match(/var\((--[a-z0-9-]+)\)/g) || [])
    .map((m) => m.slice(4, -1)))];
  assert.ok(used.length >= 20, used);
  const missing = used.filter((name) => !(name in tokens));
  assert.deepStrictEqual(missing, [],
    'the drawer references design tokens that css/main.css does not define: ' +
    missing.join(', ') + ' — a var() that resolves to nothing renders as the ' +
    'inherited value, which is invisible in exactly the places it matters');
  pass(`all ${used.length} tokens the drawer uses are defined in main.css`);
}

// ── Contrast, computed from the tokens the stylesheet actually names ─────────

function srgbToLinear(c) {
  const v = c / 255;
  return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
}

function luminance(hex) {
  const h = hex.replace('#', '');
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b);
}

function contrast(a, b) {
  const la = luminance(a);
  const lb = luminance(b);
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * Every (colour, background) pairing the drawer stylesheet declares, resolved
 * through main.css's tokens.
 *
 * Read out of the RULES rather than from a hand-written list of pairs: a
 * hand-written list is the shape that let the "Held" button ship at 1.48:1
 * under a green suite.
 */
function declaredPairings(css, tokens) {
  const rules = dom.parseCss(css);
  const resolve = (value) => {
    const m = /var\((--[a-z0-9-]+)\)/.exec(value || '');
    if (!m) return null;
    const v = tokens[m[1]];
    return (v && /^#[0-9a-fA-F]{3,8}$/.test(v.trim())) ? v.trim() : null;
  };
  const pairs = [];
  for (const rule of rules) {
    // domlite's parseCss returns { selectors: [...], decls: {...}, at }, not
    // { selector, declarations } -- the earlier spelling matched nothing on
    // every rule, so `pairs` was always empty and the anti-vacuity guard
    // below was the ONLY thing standing between that and a silent "0 pairs,
    // 0 failures, all clear". Confirmed by forcing --text-secondary to
    // #fdfdfd (near-white-on-near-white): the old code produced the
    // identical "0 resolvable pairings" message for a broken palette AND a
    // clean one, because it never got far enough to look at either.
    const decls = rule.decls || {};
    const fg = resolve(decls.color);
    const bg = resolve(decls.background || decls['background-color']);
    if (!fg || !bg) continue;
    for (const selector of rule.selectors) {
      pairs.push({ selector, fg, bg });
    }
  }
  return pairs;
}

function testEveryDrawerPairingClearsAA() {
  const css = drawerStylesheet();
  const tokens = dom.parseTokens(fs.readFileSync(MAIN_CSS, 'utf8'));
  const pairs = declaredPairings(css, tokens);
  assert.ok(pairs.length >= 5,
    `only ${pairs.length} resolvable colour pairings were found in the drawer ` +
    'stylesheet; the scrape is broken and "everything clears AA" would be a ' +
    'claim about an empty list');

  const failures = pairs
    .map((p) => ({ ...p, ratio: contrast(p.fg, p.bg) }))
    .filter((p) => p.ratio < 4.5);
  assert.deepStrictEqual(failures.map((f) => `${f.selector} ${f.fg} on ${f.bg} = ${f.ratio.toFixed(2)}:1`), [],
    'drawer colour pairings below AA');

  // The bar states inherit their text colour rather than declaring it, so the
  // pairing that actually reaches a cashier is --text-secondary on each state
  // surface. Checked explicitly, because an inherited colour has no
  // declaration for the sweep above to find.
  let inheritedCount = 0;
  for (const surface of ['--state-success-surface', '--state-warning-surface',
                         '--state-danger-surface', '--state-info-surface',
                         '--surface-raised']) {
    for (const text of ['--text-secondary', '--text-primary']) {
      inheritedCount += 1;
      const ratio = contrast(tokens[text].trim(), tokens[surface].trim());
      assert.ok(ratio >= 4.5,
        `${text} on ${surface} is ${ratio.toFixed(2)}:1 — the drawer bar's own text ` +
        'on its own background');
    }
  }

  // .cd-note / .cd-uncounted / .cd-warn-note / .cd-table also declare only a
  // `color`, never a `background` -- they are asides and table cells that sit
  // on whatever container they were dropped into. Resolved against every
  // background each one is ACTUALLY rendered on (read the call sites in
  // cash-drawer.js rather than assumed), so this stays a claim about the real
  // screen rather than a padded cross-product:
  //   .cd-note        the "other terminals" aside inside the warning bar
  //                    (--state-warning-surface); the "belongs to" / scope /
  //                    ending-note asides inside every modal body
  //                    (--surface-panel, see subsystem-retail.js's
  //                    `.ret-modal` rule); and the em-dash placeholder /
  //                    word qualifier inside a highlighted "this terminal"
  //                    history row (--surface-accent-soft, `.cd-row-mine
  //                    td`).
  //   .cd-uncounted   the "Not counted" cell in a normal history row
  //                    (--surface-panel) and in a highlighted one
  //                    (--surface-accent-soft).
  //   .cd-warn-note   the contamination disclosure inside the X-report /
  //                    close-out modal body (--surface-panel).
  //   .cd-table td/th a normal row and a highlighted "this terminal" row
  //                    (--surface-panel, --surface-accent-soft); the header
  //                    row never gets the row-mine background, so `th` is
  //                    only checked against --surface-panel.
  const rules = dom.parseCss(css);
  const colorTokenOf = (selector) => {
    const rule = rules.find((r) => r.selectors.includes(selector) && r.decls.color);
    const m = rule && /var\((--[a-z0-9-]+)\)/.exec(rule.decls.color);
    return m ? m[1] : null;
  };
  const noteText = colorTokenOf('.cd-note');
  const uncountedText = colorTokenOf('.cd-uncounted');
  const warnNoteText = colorTokenOf('.cd-warn-note');
  const tableTdText = colorTokenOf('.cd-table td');
  const tableThText = colorTokenOf('.cd-table th');
  assert.ok(noteText && uncountedText && warnNoteText && tableTdText && tableThText,
    'could not resolve the declared colour token for .cd-note, .cd-uncounted, ' +
    '.cd-warn-note or .cd-table td/th -- the inherited-background sweep below ' +
    'would silently check nothing');

  const inheritedContexts = [
    ['.cd-note', noteText, '--state-warning-surface'],
    ['.cd-note', noteText, '--surface-panel'],
    ['.cd-note', noteText, '--surface-accent-soft'],
    ['.cd-uncounted', uncountedText, '--surface-panel'],
    ['.cd-uncounted', uncountedText, '--surface-accent-soft'],
    ['.cd-warn-note', warnNoteText, '--surface-panel'],
    ['.cd-table td', tableTdText, '--surface-panel'],
    ['.cd-table td', tableTdText, '--surface-accent-soft'],
    ['.cd-table th', tableThText, '--surface-panel'],
  ];
  const inheritedFailures = inheritedContexts
    .map(([selector, textToken, surfaceToken]) => ({
      selector, textToken, surfaceToken,
      ratio: contrast(tokens[textToken].trim(), tokens[surfaceToken].trim()),
    }))
    .filter((p) => p.ratio < 4.5);
  assert.deepStrictEqual(
    inheritedFailures.map((f) => `${f.selector} (${f.textToken}) on ${f.surfaceToken} = ${f.ratio.toFixed(2)}:1`),
    [], 'drawer inherited-background pairings below AA');
  inheritedCount += inheritedContexts.length;

  pass(`${pairs.length} declared drawer pairings plus ${inheritedCount} inherited ones clear AA`);
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. BOTH CATALOGS
// ─────────────────────────────────────────────────────────────────────────────

// Characters that carry no language. A text run made only of these is data,
// not copy, and must not be demanded of a translator.
const NEUTRAL = /^[\s\d.,:;%+\-–—()[\]{}<>/\\|*#@&$£€₪﷼~^_=!?'"`•·×÷…]*$/;
// Separators that join two independently-translated phrases in one text node.
const SEPARATORS = /[·•|]/;

// domlite is an HTML READER, not a parser with an entity table -- by design
// (see its own header: "not a general-purpose parser"). It hands back
// `&middot;` and `&mdash;` as the seven/six literal characters they are
// written as, not as the punctuation they render to in a real browser. Left
// undecoded, "&middot; Opening float" is one text run (no real `·` for
// SEPARATORS to split on) that matches neither catalog, and a bare
// "&mdash;" has letters in it so NEUTRAL rejects it too -- both permanently
// red, for markup, not copy. That is what made this guard red AT BASELINE
// before this fix: teaching it the two entities this file actually emits
// turns those two hazards into a real `·` (which SEPARATORS still splits on)
// and a real `—` (which NEUTRAL already allowed). A genuine untranslated
// literal has no entity to decode, so it still fails after this change --
// only markup noise is silenced.
const HTML_ENTITIES = {
  middot: '·', mdash: '—', ndash: '–',
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ',
};
function decodeEntities(text) {
  return String(text).replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (whole, body) => {
    if (body[0] === '#') {
      const code = body[1] === 'x' || body[1] === 'X'
        ? parseInt(body.slice(2), 16)
        : parseInt(body.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : whole;
    }
    return Object.prototype.hasOwnProperty.call(HTML_ENTITIES, body) ? HTML_ENTITIES[body] : whole;
  });
}

function translatableRuns(root) {
  const runs = [];
  const visit = (node) => {
    if (node.type === 'text') {
      decodeEntities(node.text).split(SEPARATORS).forEach((piece) => {
        const text = piece.replace(/\s+/g, ' ').trim();
        if (!text || NEUTRAL.test(text)) return;
        if (!/[A-Za-z]/.test(text)) return;
        runs.push(text);
      });
      return;
    }
    (node.children || []).forEach(visit);
  };
  (root.children || []).forEach(visit);
  return runs;
}

async function testEveryStringOnTheDrawerSurfaceIsInBothCatalogs() {
  const surfaces = [];

  for (const response of [CURRENT_OK, CURRENT_NONE, CURRENT_DENIED, new Error('down')]) {
    const { html } = await renderBar(response);
    surfaces.push(['bar', dom.parseFragment(html)]);
  }

  const routes = {
    get: {
      '/api/sub/retail/cash-sessions/current': CURRENT_OK,
      '/api/sub/retail/cash-sessions/sess-open/x-report': X_REPORT,
      '/api/sub/retail/cash-sessions?': HISTORY,
    },
    post: {},
  };
  surfaces.push(['open-shift', (await renderModal((cd) => cd.openOpenShiftModal(), routes)).root]);
  surfaces.push(['movement', (await renderModal((cd) => cd.openMovementModal(), routes)).root]);
  surfaces.push(['x-report', (await renderModal((cd) => cd.openXReportModal(), routes)).root]);
  surfaces.push(['close', (await renderModal((cd) => cd.openCloseModal(), routes)).root]);
  surfaces.push(['history', (await renderModal((cd) => cd.openHistoryModal(), routes)).root]);
  // The history as a trading shop actually sees it: the live drawer at the top.
  // This is the ONLY surface that reaches `_statusBadge()`'s `pending` arm.
  surfaces.push(['history-with-open', (await renderModal((cd) => cd.openHistoryModal(), {
    get: {
      '/api/sub/retail/cash-sessions/current': CURRENT_OK,
      '/api/sub/retail/cash-sessions?': HISTORY_WITH_OPEN,
    },
  })).root]);

  const hazards = [];
  let total = 0;
  for (const [name, root] of surfaces) {
    for (const run of translatableRuns(root)) {
      total += 1;
      if (!(run in EN) || !(run in AR)) hazards.push(`${name}: ${JSON.stringify(run)}`);
    }
  }
  // ANTI-VACUITY, and it is the assertion that matters most here: if the
  // renders produced nothing, "no untranslated strings" would be true of an
  // empty screen. Ten surfaces of real drawer copy is at least this many runs.
  assert.ok(total >= 60,
    `only ${total} translatable text runs were found across ${surfaces.length} drawer ` +
    'surfaces; the render is broken, so the catalog sweep is checking almost nothing');
  assert.deepStrictEqual(hazards, [],
    'drawer strings missing from a locale catalog:\n  ' + hazards.join('\n  '));

  // ...and the Arabic is real Arabic, not the English copied across.
  const arabic = /[؀-ۿ]/;
  const suspects = [...new Set(surfaces.flatMap(([, r]) => translatableRuns(r)))]
    .filter((k) => !arabic.test(AR[k] || ''));
  assert.deepStrictEqual(suspects, [],
    'these drawer strings have no Arabic script in their Arabic value: ' + suspects.join(', '));
  pass(`${total} drawer text runs, all in both catalogs, all genuinely translated`);
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. FORCE-CLOSED IS NOT ENDED, AND UNKNOWN IS NOT ZERO
// ─────────────────────────────────────────────────────────────────────────────

async function testAForceClosedDrawerRendersNoAmountAtAll() {
  const routes = {
    get: {
      '/api/sub/retail/cash-sessions/current': CURRENT_OK,
      '/api/sub/retail/cash-sessions?': HISTORY,
    },
  };
  const { root } = await renderModal((cd) => cd.openHistoryModal(), routes);
  const rows = dom.allElements(root).filter((el) => el.tag === 'tr' && el.children
    .some((c) => c.type === 'element' && c.tag === 'td'));
  assert.strictEqual(rows.length, 3, `expected 3 history rows, got ${rows.length}`);

  const cellsOf = (row) => row.children.filter((c) => c.type === 'element' && c.tag === 'td');
  const [endedRow, forcedRow, closedRow] = rows;

  const forcedVariance = cellsOf(forcedRow)[4];
  const endedVariance = cellsOf(endedRow)[4];
  const closedVariance = cellsOf(closedRow)[4];

  const moneyIn = (el) => dom.allElements(el).filter((e) => e.classes.includes('money'));

  // THE PAIR. "No dollar sign in the force-closed cell" alone would be
  // satisfied by a table that renders no money anywhere.
  assert.strictEqual(moneyIn(forcedVariance).length, 0,
    'a drawer nobody counted rendered an amount — 0.00 is the number a shop reads ' +
    'as "that drawer was fine"');
  assert.ok(!/\$/.test(dom.textOf(forcedVariance)), dom.textOf(forcedVariance));
  assert.ok(/Not counted/.test(dom.textOf(forcedVariance)), dom.textOf(forcedVariance));

  assert.strictEqual(moneyIn(endedVariance).length, 1,
    'a counted, short drawer rendered no amount');
  assert.ok(/short/.test(dom.textOf(endedVariance)), dom.textOf(endedVariance));

  // A drawer that balanced EXACTLY is a counted drawer, and must not read as
  // an uncounted one. `variance == 0` and `variance == null` are the two
  // values a falsy check collapses.
  assert.strictEqual(moneyIn(closedVariance).length, 1,
    'a drawer that balanced exactly rendered as though nobody counted it');
  assert.ok(/balanced/.test(dom.textOf(closedVariance)), dom.textOf(closedVariance));
  assert.ok(!/Not counted/.test(dom.textOf(closedVariance)), dom.textOf(closedVariance));

  // The three statuses are three different badges.
  const badges = dom.allElements(root).filter((el) => el.classes.includes('cd-badge'));
  assert.strictEqual(badges.length, 3, badges.length);
  assert.strictEqual(new Set(badges.map((b) => b.classes.join(' '))).size, 3,
    'two variance statuses render the same badge');
  pass('force-closed, short-and-unverified, and balanced-and-accepted render differently');
}

async function testApprovalIsOfferedSeparatelyForACountedAndAnUncountedDrawer() {
  const routes = {
    get: {
      '/api/sub/retail/cash-sessions/current': CURRENT_OK,
      '/api/sub/retail/cash-sessions?': HISTORY,
    },
  };
  const { html } = await renderModal((cd) => cd.openHistoryModal(), routes);
  assert.ok(/CashDrawer\.approve\('sess-ended', false\)/.test(html),
    'a counted, unverified drawer offers no way to accept its variance');
  assert.ok(/CashDrawer\.approve\('sess-forced', true\)/.test(html),
    'an uncounted drawer is not offered under its own, explicit acknowledgement');
  assert.ok(!/CashDrawer\.approve\('sess-closed'/.test(html),
    'an already-accepted drawer is offered for approval again');

  // ...and none of it is offered to somebody who cannot approve, so the two
  // buttons above are a capability decision rather than decoration.
  const noAuth = await renderModal((cd) => cd.openHistoryModal(), {
    get: {
      '/api/sub/retail/cash-sessions/current': CURRENT_OK,
      '/api/sub/retail/cash-sessions?': { ...HISTORY, may_approve: false, scope: 'this_terminal' },
    },
  });
  assert.ok(!/CashDrawer\.approve\(/.test(noAuth.html),
    'approval is offered to an account the server will refuse');
  pass('accepting a counted variance and acknowledging an uncounted drawer are separate acts');
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. MONEY AND BIDI
// ─────────────────────────────────────────────────────────────────────────────

async function testEveryAmountOnTheDrawerSurfaceIsAMoneyElement() {
  const routes = {
    get: {
      '/api/sub/retail/cash-sessions/current': CURRENT_OK,
      '/api/sub/retail/cash-sessions/sess-open/x-report': X_REPORT,
      '/api/sub/retail/cash-sessions?': HISTORY,
    },
  };
  const surfaces = [
    dom.parseFragment((await renderBar(CURRENT_OK)).html),
    (await renderModal((cd) => cd.openXReportModal(), routes)).root,
    (await renderModal((cd) => cd.openCloseModal(), routes)).root,
    (await renderModal((cd) => cd.openHistoryModal(), routes)).root,
  ];

  let amounts = 0;
  const bare = [];
  for (const root of surfaces) {
    for (const el of dom.allElements(root)) {
      const own = dom.ownText(el);
      if (!/\$\s*\d/.test(own)) continue;
      amounts += 1;
      if (!el.classes.includes('money')) bare.push(`${el.tag}.${el.classes.join('.')}: ${own.trim()}`);
    }
  }
  assert.ok(amounts >= 12,
    `only ${amounts} amounts were rendered across the drawer surfaces; the scrape is broken`);
  assert.deepStrictEqual(bare, [],
    'amounts rendered outside a .money element, so they get neither tabular figures, ' +
    "nor the typographic minus, nor rtl.css's direction isolation:\n  " + bare.join('\n  '));

  // The negative cue is not only colour: main.css pairs .money--negative with
  // a bold weight and .money--accounting's parentheses, and the helper marks
  // both. A negative that arrived without them would be distinguishable from
  // a positive by colour alone.
  const negatives = surfaces.flatMap((r) => dom.allElements(r))
    .filter((el) => el.classes.includes('money--negative'));
  assert.ok(negatives.length >= 3, `only ${negatives.length} negative amounts rendered`);
  for (const el of negatives) {
    assert.ok(el.classes.includes('money--accounting'),
      'a negative amount on the drawer carries the colour cue but not the ' +
      'accounting parentheses');
    assert.ok(/−/.test(dom.ownText(el)),
      'a negative amount uses an ASCII hyphen rather than U+2212 MINUS SIGN');
  }
  pass(`${amounts} drawer amounts, all .money, all ${negatives.length} negatives triple-cued`);
}

async function testTheSignedVarianceCarriesAWordAsWellAsAColour() {
  const routes = {
    get: {
      '/api/sub/retail/cash-sessions/current': CURRENT_OK,
      '/api/sub/retail/cash-sessions?': HISTORY,
    },
  };
  const { root } = await renderModal((cd) => cd.openHistoryModal(), routes);
  const text = dom.textOf(root);
  for (const word of ['short', 'balanced']) {
    assert.ok(text.includes(word), `the variance cell never says "${word}"`);
    assert.ok(word in EN && word in AR, `"${word}" is not in both catalogs`);
  }
  // The qualifier is what makes the sign survive both greyscale AND an Arabic
  // paragraph: rtl.css isolates `.money` to direction:ltr, and the word beside
  // it is a strong-directional character that anchors the pair.
  const rtl = fs.readFileSync(RTL_CSS, 'utf8');
  assert.ok(/body\.rtl[^{]*\.money[^{]*\{[^}]*direction:\s*ltr/.test(rtl),
    'rtl.css no longer isolates .money to direction:ltr, so every signed variance ' +
    'on an Arabic page can render with its sign on the wrong side');
  pass('the variance sign is carried by a translated word as well as by colour');
}

async function testTimestampsAndTerminalIdsAreDirectionIsolated() {
  const routes = {
    get: {
      '/api/sub/retail/cash-sessions/current': CURRENT_OK,
      '/api/sub/retail/cash-sessions?': HISTORY,
    },
  };
  const surfaces = [
    dom.parseFragment((await renderBar(CURRENT_OK)).html),
    (await renderModal((cd) => cd.openHistoryModal(), routes)).root,
  ];
  const bdis = surfaces.flatMap((r) => dom.allElements(r)).filter((el) => el.tag === 'bdi');
  // A run of digits with no strong directional character takes its direction
  // from the paragraph, so an unisolated "2026-08-24 09:15" renders as
  // "09:15 2026-08-24" on an Arabic page: a wrong date, not a mirrored one.
  assert.ok(bdis.length >= 4,
    `only ${bdis.length} <bdi> elements across the drawer surfaces — the drawer renders ` +
    'four timestamps and two terminal ids, and every one of them is a neutral run');
  for (const el of bdis) {
    assert.strictEqual(el.attrs.dir, 'ltr',
      '<bdi> without an explicit dir falls back to auto, which picks the direction ' +
      'from the first STRONG character — and a timestamp has none');
  }
  // The full terminal id travels in the title, because a truncated id cannot
  // be matched against device_registry.devices when somebody actually needs to
  // identify a till.
  const withTitle = bdis.filter((el) => el.attrs.title);
  assert.ok(withTitle.length >= 1, 'no terminal id carries its full value in a title');
  assert.ok(withTitle.some((el) => (el.attrs.title || '').length >= 32),
    'the terminal pill shows a truncated id and does not carry the full uuid anywhere');
  pass(`${bdis.length} neutral runs on the drawer surface are direction-isolated`);
}

// ─────────────────────────────────────────────────────────────────────────────
// 6. THE CONTAMINATION DISCLOSURE REACHES THE SCREEN
// ─────────────────────────────────────────────────────────────────────────────

async function testLegacyContaminationIsShownOnTheReport() {
  const contaminated = {
    status: 'success',
    data: { ...X_REPORT.data, foreign_terminal_sales: 7 },
  };
  const clean = await renderModal((cd) => cd.openXReportModal(), {
    get: {
      '/api/sub/retail/cash-sessions/current': CURRENT_OK,
      '/api/sub/retail/cash-sessions/sess-open/x-report': X_REPORT,
    },
  });
  const dirty = await renderModal((cd) => cd.openXReportModal(), {
    get: {
      '/api/sub/retail/cash-sessions/current': CURRENT_OK,
      '/api/sub/retail/cash-sessions/sess-open/x-report': contaminated,
    },
  });
  assert.ok(!/another terminal are included/.test(clean.html),
    'a clean drawer shows a contamination warning, so the warning means nothing');
  assert.ok(/another terminal are included/.test(dirty.html),
    'a drawer containing another till\'s sales does not say so');
  assert.ok(/>7</.test(dirty.html) || /7/.test(dom.textOf(dirty.root)), dirty.html);

  // "could not determine" is a THIRD state and must not read as "clean".
  const unknown = await renderModal((cd) => cd.openXReportModal(), {
    get: {
      '/api/sub/retail/cash-sessions/current': CURRENT_OK,
      '/api/sub/retail/cash-sessions/sess-open/x-report':
        { status: 'success', data: { ...X_REPORT.data, foreign_terminal_sales: -1 } },
    },
  });
  assert.ok(/Could not check whether other terminals/.test(unknown.html), unknown.html);
  pass('a legacy drawer discloses the other terminal\'s sales inside it');
}

async function testTheCloseModalRefusesToGuessTheExpectedCash() {
  // A close-out screen whose "expected" silently reads 0.00 because the
  // report fetch failed would report the entire drawer as a shortfall, and
  // the cashier would key their real count against it.
  const ctx = loadDrawer({
    get: {
      '/api/sub/retail/cash-sessions/current': CURRENT_OK,
      '/api/sub/retail/cash-sessions/sess-open/x-report': new Error('gone'),
    },
  });
  await ctx.CashDrawer.refresh();
  await ctx.CashDrawer.openCloseModal();
  assert.strictEqual(ctx.appended.length, 0,
    'the close-out modal opened without knowing the expected cash');
  assert.ok(ctx.toasts.some((x) => /Could not load the cash drawer/.test(x.text)), ctx.toasts);
  pass('the close-out modal refuses to open rather than guess the expected cash');
}

// ─────────────────────────────────────────────────────────────────────────────

// Every test function runs, no matter how many earlier ones failed.
//
// THE BUG THIS REPLACES: a flat `await` chain stops dead at the first
// rejection -- the process never reaches the functions listed after it, and
// node prints exactly one failure. Driving these same 14 functions through a
// per-function try/catch surfaced a SECOND, previously invisible failure at
// what was test 7 (the catalog sweep) and proved the 7 functions listed
// after it had never once executed in this file's short life. This project
// has already shipped seven guards that had silently never run; a runner
// that stops at the first red light is how that keeps happening.
const TESTS = [
  testTheBarHasFourDistinctHonestStates,
  testEveryBarStateNamesItsTerminal,
  testTheEmptyStateDistinguishesAQuietShopFromABusyOne,
  testNoHardcodedColoursInTheDrawerStylesheet,
  testEveryTokenTheDrawerUsesExists,
  testEveryDrawerPairingClearsAA,
  testEveryStringOnTheDrawerSurfaceIsInBothCatalogs,
  testAForceClosedDrawerRendersNoAmountAtAll,
  testApprovalIsOfferedSeparatelyForACountedAndAnUncountedDrawer,
  testEveryAmountOnTheDrawerSurfaceIsAMoneyElement,
  testTheSignedVarianceCarriesAWordAsWellAsAColour,
  testTimestampsAndTerminalIdsAreDirectionIsolated,
  testLegacyContaminationIsShownOnTheReport,
  testTheCloseModalRefusesToGuessTheExpectedCash,
];

(async () => {
  const failures = [];
  for (const fn of TESTS) {
    try {
      // `await` on a non-async function's plain return value resolves
      // immediately -- this is safe for the synchronous test functions too.
      await fn();
    } catch (err) {
      failures.push({ name: fn.name, err });
      console.error(`  FAIL ${fn.name}`);
      console.error('  ' + (err && err.stack ? err.stack : err));
    }
  }

  const ran = TESTS.length;
  const failed = failures.length;
  const passed = ran - failed;
  console.log(`\n${passed}/${ran} test functions passed (${checks} checks recorded)`);

  if (failed > 0) {
    console.error(`\nFAIL: retail_drawer_screen_test.js — ${failed} of ${ran} test ` +
      `functions failed: ${failures.map((f) => f.name).join(', ')}`);
    process.exit(1);
  }
  console.log(`PASS: retail_drawer_screen_test.js — ${checks} checks`);
})().catch((err) => {
  // A throw OUTSIDE the per-test try/catch (e.g. TESTS itself failing to
  // build) is still a hard failure -- this is not a substitute for the loop
  // above, it is the backstop for whatever the loop cannot reach.
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
