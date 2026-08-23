/**
 * retail_surface_dashboard_test.js — structural guards for the dashboard /
 * landing redesign ("Operational Calm").
 *
 * Claims checked here:
 *
 *   1. The cashier landing still renders, and still makes ZERO API calls for
 *      what it cannot read — and, critically, the CAPABILITY CHECK ACTUALLY
 *      RAN. See the long comment on that test for why asserting "no fetch
 *      happened" on its own is worthless.
 *   2. The 9am answer (today's net revenue) is the largest money element on
 *      the dashboard.
 *   3. The needs-attention band distinguishes "something to do" from "nothing
 *      to do" by WORDS and STATE, not by colour alone.
 *   4. The recent-transactions money column is tabular and end-aligned, so it
 *      is a column a human can add up, and stays correct mirrored under RTL.
 *   4b. A clickable row can be reached and operated WITHOUT A POINTER — and it
 *      is still a row, not a <tr> retrofitted into a button.
 *   4c. A negative headline revenue is marked on the VALUE, with cues that
 *      survive greyscale; a positive one is not marked at all.
 *   5. No dashboard surface reaches for the un-themed --text-muted token.
 *
 * All claims are mutation-proven; the specific mutation is named on each test.
 *
 * Run: node products/retail/tests/retail_surface_dashboard_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const dom = require('./retail_surface_domlite.js');
// The 13-screen rendered corpus. Required here rather than re-stubbed, because
// the keyboard-path claim below is about EVERY clickable row in the product,
// not the dashboard's — and the dashboard's was the only one this file could
// see for two rounds.
const render = require('./retail_design_render_test.js');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const FRONTEND_FILE = path.join(FRONTEND_DIR, 'subsystem-retail.js');
const CSS_FILE = path.join(FRONTEND_DIR, 'css', 'main.css');

// `created_at` is the SPACE form the server actually writes
// (retail_api.py::create_sale stores `%Y-%m-%d %H:%M:%S`), not an ISO 'T'. The
// difference is not cosmetic: a 'T' is a strong LTR character that anchors the
// whole run, so the ISO form this fixture used to carry made the Date column's
// bidi hazard unreproducible in test while it was live on every Arabic install.
// retail_surface_i18n_test.js is where that claim is asserted; the fixture is
// kept honest in both files so neither can drift back.
const STATS = {
  today_sales: 1284.5, today_transactions: 18, month_sales: 21450.75,
  month_transactions: 310, low_stock_alerts: 7, total_customers: 84,
  total_products: 312, today_returns: 65.44, sales_change_pct: 12,
  hourly_labels: [], hourly_data: [], payment_methods: {},
  recent_sales: [
    { id: 1, sale_number: 'S-1041', customer_name: 'Walk-in', item_count: 3,
      payment_method: 'cash', total: 42.5, created_at: '2026-08-21 18:42:00' },
  ],
};

// ─────────────────────────────────────────────────────────────────────────────
// Harness
// ─────────────────────────────────────────────────────────────────────────────

function makeElementStub(overrides) {
  // classList RECORDS rather than swallowing. A no-op classList is fine while
  // nothing under test manipulates classes, and worthless the moment something
  // does: RetailSystem._setMoney() marks a negative amount by toggling
  // .money--negative on the value element, and a swallowing stub makes that
  // marking — the only cue that survives greyscale — permanently invisible to
  // every assertion in this file. `classes` is the observable.
  const classes = [];
  const el = Object.assign({
    innerHTML: '', textContent: '', value: '', id: '', disabled: false,
    style: {},
    classes,
    classList: {
      add(c) { if (!classes.includes(c)) classes.push(c); },
      remove(c) { const i = classes.indexOf(c); if (i !== -1) classes.splice(i, 1); },
      toggle(c, on) { if (on) this.add(c); else this.remove(c); },
      contains(c) { return classes.includes(c); },
    },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {},
    querySelectorAll() { return []; }, addEventListener() {}, focus() {},
    // Canvas-shaped, so the chart branches are reachable. Without getContext
    // and parentElement the whole `if (window.Chart)` block silently no-ops and
    // the two chart EMPTY STATES -- which are real, user-visible surfaces --
    // never render, so nothing downstream can inspect them. That gap let a
    // --text-muted regression on the empty states survive an earlier version
    // of this harness.
    getContext() { return {}; },
  }, overrides);
  return el;
}

/**
 * @param opts.capabilities  array the stubbed hasCapability() consults, or null
 *                           to omit SubsystemApp entirely
 */
function loadRetailSystem(opts) {
  const options = opts || {};
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');

  const fetchCalls = [];
  const capabilityChecks = [];
  const els = Object.create(null);
  const tbody = makeElementStub();
  const attention = makeElementStub({ id: 'r-dash-attention' });
  const classToggles = [];
  attention.classList = {
    add(c) { classToggles.push([c, true]); },
    remove(c) { classToggles.push([c, false]); },
    toggle(c, on) { classToggles.push([c, !!on]); },
    contains() { return false; },
  };

  // Each canvas gets a real parent whose innerHTML the chart empty-state
  // branches overwrite, so those surfaces can be inspected like any other.
  const chartHosts = {};
  const getEl = (id) => {
    if (id === 'r-dash-attention') return attention;
    if (!els[id]) {
      const el = makeElementStub({ id });
      if (id === 'r-dash-hourly' || id === 'r-dash-pay') {
        chartHosts[id] = makeElementStub({ id: id + '-host' });
        el.parentElement = chartHosts[id];
      }
      els[id] = el;
    }
    return els[id];
  };

  const sandbox = {
    console,
    t: (s) => s,
    fetch: (url, init) => {
      fetchCalls.push(String(url));
      if (options.fetchFails) return Promise.reject(new Error('simulated failure'));
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ data: STATS }) });
    },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem: () => {} },
    document: {
      activeElement: null,
      getElementById(id) {
        if (id === 'ret-styles') return makeElementStub();  // styles already injected
        return getEl(id);
      },
      createElement() { return makeElementStub(); },
      querySelector(sel) {
        if (sel === '#r-dash-recent tbody') return tbody;
        return makeElementStub();
      },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
  };

  if (options.capabilities !== null) {
    sandbox.SubsystemApp = {
      active: 'retail',
      showToast() {},
      _navigate() {},
      hasCapability(code) {
        capabilityChecks.push(code);
        return (options.capabilities || []).includes(code);
      },
    };
  }
  sandbox.window = sandbox;
  // Chart.js stand-in. Its presence is what makes the `if (window.Chart)`
  // block -- and therefore both chart EMPTY STATES -- actually execute.
  sandbox.Chart = function ChartStub() { return { destroy() {} }; };
  sandbox.Chart.getChart = () => null;

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');

  return {
    RetailSystem: sandbox.RetailSystem,
    fetchCalls, capabilityChecks, tbody, els, classToggles, chartHosts,
  };
}

function parseDashboardStyle(html) {
  const root = dom.parseFragment(html);
  const styleEl = dom.allElements(root).find((el) => el.tag === 'style');
  assert.ok(styleEl, 'The dashboard render emitted no <style> block to reason about.');
  return { root, rules: dom.parseCss(dom.textOf(styleEl)) };
}

const MONEY_TEXT = /[$][\s]*\d/;

/**
 * Everything in a rendered fragment that can ACTUALLY APPLY a colour: every
 * inline style attribute, plus the text of every <style> block.
 *
 * Deliberately not a regex over innerHTML. innerHTML includes HTML comments,
 * and this file's markup carries long explanatory comments that name the very
 * tokens being banned -- a raw scan flags those and the obvious "fix" is to
 * delete the explanation, which is the wrong thing to do to a codebase whose
 * stated convention is to leave a trail. A comment cannot style anything, so
 * it is not a styling surface and is not examined.
 */
function stylingSurfaces(root) {
  const out = [];
  for (const el of dom.allElements(root)) {
    if (el.attrs.style) out.push({ where: dom.describe(el), css: el.attrs.style });
    if (el.tag === 'style') out.push({ where: '<style> block', css: dom.textOf(el) });
  }
  return out;
}

function moneyElements(root) {
  return dom.allElements(root).filter((el) => {
    if (el.classes.includes('money')) return true;
    return MONEY_TEXT.test(dom.ownText(el));
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// CLAIM 1 — the cashier landing renders, and makes zero API calls, BECAUSE a
//           capability check ran and said so
// ─────────────────────────────────────────────────────────────────────────────
//
// Mutation-proven, and the mutation set is the point of this test's shape:
//   * `if (window.SubsystemApp && !SubsystemApp.hasCapability(...))`
//        -> `if (false)`                     -> testCashierLanding* fails
//        -> `if (true)`                      -> the MANAGER control case fails
//   * delete the branch entirely             -> the zero-fetch assertion fails
//
// The two halves are load-bearing together. "The cashier landing makes no API
// calls" is trivially satisfiable by a screen that renders nothing, by a
// hardcoded `return this._renderCashierLanding(c)`, or by a build where fetch
// is broken — none of which is the behaviour anyone wants. So this asserts the
// CHECK RAN (hasCapability was consulted, with the right capability code), that
// the landing rendered something a cashier can actually use, AND that a
// manager on the same code path does still fetch. Only the three together mean
// "the gate works".

function testCashierLandingRendersWithZeroApiCalls() {
  // A cashier: holds sell/refund, does NOT hold retail.reports.
  const ctx = loadRetailSystem({ capabilities: ['retail.sell', 'retail.refund', 'retail.cash.close'] });
  const content = makeElementStub();

  ctx.RetailSystem._renderDashboard(content);

  // (a) the check RAN, and asked the question the server would actually gate on
  assert.ok(
    ctx.capabilityChecks.includes('retail.reports'),
    'The dashboard never consulted hasCapability("retail.reports") before deciding ' +
    'what to render. Without that call, "no API request was made" proves nothing -- ' +
    'a screen that renders nothing, or a build with a broken fetch, would look ' +
    'identical. Capability codes checked: ' + JSON.stringify(ctx.capabilityChecks)
  );

  // (b) zero API calls
  assert.deepStrictEqual(
    ctx.fetchCalls, [],
    'The cashier landing issued network requests. Every tile on the real dashboard ' +
    'reads from GET /dashboard/stats, which is gated on retail.reports -- a cashier ' +
    'does not hold it, so fetching would render a screen whose only content is a 403. ' +
    'Requests made: ' + JSON.stringify(ctx.fetchCalls)
  );

  // (c) it rendered a real destination, not an empty div
  const root = dom.parseFragment(content.innerHTML);
  const text = dom.textOf(root);
  assert.ok(
    content.innerHTML.length > 200 && /Ready to sell/.test(text),
    'The cashier landing rendered nothing recognisable. "Makes no API calls" is ' +
    'trivially true of a blank screen; the panel has to be a place a cashier can ' +
    'act from. Got: ' + JSON.stringify(content.innerHTML.slice(0, 200))
  );

  const posButton = dom.allElements(root).find(
    (el) => el.tag === 'button' && /_navigate\('pos'\)/.test(el.attrs.onclick || '')
  );
  assert.ok(
    posButton,
    'The cashier landing offers no route to the till. Its entire reason to exist is ' +
    'giving a cashier one action they can take; without that it is an error page ' +
    'with better manners.'
  );

  console.log('PASS: cashier landing renders a usable panel, checks retail.reports, fetches nothing');
}

function testManagerOnTheSamePathStillFetches() {
  // The control case. If this fails, the gate above is not a gate.
  const ctx = loadRetailSystem({ capabilities: ['retail.sell', 'retail.reports'] });
  const content = makeElementStub();

  ctx.RetailSystem._renderDashboard(content);

  assert.ok(
    ctx.capabilityChecks.includes('retail.reports'),
    'hasCapability("retail.reports") was not consulted for a manager either.'
  );
  assert.ok(
    ctx.fetchCalls.some((u) => /dashboard\/stats/.test(u)),
    'A user WHO HOLDS retail.reports did not trigger the dashboard stats fetch. ' +
    'That means the cashier-landing branch is not reading the capability at all -- ' +
    'it is an unconditional redirect, and the zero-fetch assertion in the test above ' +
    'is passing for the wrong reason. Requests made: ' + JSON.stringify(ctx.fetchCalls)
  );

  console.log('PASS: a manager on the same code path does fetch — the gate is a real gate');
}

function testLandingIsInertWhenCapabilitiesAreUnknown() {
  // hasCapability() deliberately FAILS OPEN when the session has not resolved
  // (see app-shell.js). The landing must not hijack the dashboard in that
  // state, or a transient boot condition blanks the screen for the owner.
  const ctx = loadRetailSystem({ capabilities: null });  // no SubsystemApp at all
  const content = makeElementStub();

  ctx.RetailSystem._renderDashboard(content);

  assert.ok(
    ctx.fetchCalls.some((u) => /dashboard\/stats/.test(u)),
    'With no SubsystemApp present (standalone load, or a session that has not ' +
    'resolved yet), the dashboard took the cashier-landing branch instead of ' +
    'rendering normally. This path must fail OPEN. Requests: ' + JSON.stringify(ctx.fetchCalls)
  );

  console.log('PASS: with no capability information the dashboard renders normally (fails open)');
}

function testCashierLandingAvoidsTheUnthemedMutedToken() {
  const ctx = loadRetailSystem({ capabilities: ['retail.sell'] });
  const content = makeElementStub();
  ctx.RetailSystem._renderDashboard(content);

  const surfaces = stylingSurfaces(dom.parseFragment(content.innerHTML));
  assert.ok(
    surfaces.length >= 3,
    'Found only ' + surfaces.length + ' styling surfaces on the cashier landing; ' +
    'this check would be near-vacuous.'
  );
  const offenders = surfaces.filter((s) => /var\(--text-muted\)/.test(s.css));

  assert.deepStrictEqual(
    offenders.map((s) => s.where), [],
    'The cashier landing styles text with var(--text-muted). That token is defined ' +
    'once at :root (#9aa0a6) and never redefined for the light theme, which is the ' +
    'default for a fresh install -- roughly 2.6:1 grey on a white card, under the ' +
    '4.5:1 WCAG AA floor, on the FIRST screen a cashier sees after logging in. ' +
    'The theme-aware token is --text-dim. Offending: ' + JSON.stringify(offenders)
  );

  console.log(`PASS: cashier landing's ${surfaces.length} styling surfaces are all theme-aware`);
}

// ─────────────────────────────────────────────────────────────────────────────
// CLAIM 2 — today's revenue is the largest money element on the dashboard
// ─────────────────────────────────────────────────────────────────────────────
//
// Mutation-proven: setting `.rdash-answer-value { font-size: 16px }` fails with
// the month-to-date figure named as at least as large.

async function testTodaysRevenueIsTheLargestMoneyElement() {
  const ctx = loadRetailSystem({ capabilities: ['retail.reports'] });
  const content = makeElementStub();
  await ctx.RetailSystem._renderDashboard(content);

  const { root, rules } = parseDashboardStyle(content.innerHTML);
  const tokens = dom.parseTokens(fs.readFileSync(CSS_FILE, 'utf8'));

  // The rendered rows land in the tbody stub, not in `content`, so both are
  // examined — an amount in a table is still an amount on the screen.
  const candidates = [...moneyElements(root), ...moneyElements(dom.parseFragment(ctx.tbody.innerHTML))];
  assert.ok(
    candidates.length >= 3,
    'Found only ' + candidates.length + ' money elements on the dashboard; this ' +
    'comparison would be vacuous. Found: ' + candidates.map(dom.describe).join(' | ')
  );

  const answer = candidates.find((el) => el.attrs.id === 'r-k-rev');
  assert.ok(answer, 'No #r-k-rev (today\'s net revenue) among the dashboard money elements.');

  const answerRange = dom.fontSizeRange(rules, answer, tokens);
  assert.ok(answerRange, 'Could not resolve a font-size for #r-k-rev — treated as a failure, not a skip.');

  const displayStep = dom.lengthRange(tokens['--text-size-display'] || '30px', tokens);
  const UNRESOLVED_CEILING = displayStep ? displayStep.max : 30;
  assert.ok(
    answerRange.min > UNRESOLVED_CEILING,
    `#r-k-rev can render as small as ${answerRange.min}px, below the ${UNRESOLVED_CEILING}px ` +
    'bound used for amounts that inherit their size.'
  );

  const bigger = [];
  for (const el of candidates) {
    if (el === answer) continue;
    const range = dom.effectiveFontSizeRange(rules, el, tokens);
    const max = range ? range.max : UNRESOLVED_CEILING;
    const why = range ? range.sources.join(', ') : `inherits shell type (bounded at ${UNRESOLVED_CEILING}px)`;
    if (max >= answerRange.min) bigger.push(`${dom.describe(el)} -> ${why} (up to ${max}px)`);
  }

  assert.deepStrictEqual(
    bigger, [],
    'Today\'s revenue is not the largest money element on the dashboard. These are ' +
    'at least as large:\n  ' + bigger.join('\n  ') +
    '\n\nThe screen is supposed to answer one question first. If several numbers ' +
    'share top billing, it answers none of them.'
  );

  console.log(
    `PASS: #r-k-rev (${answerRange.min}-${answerRange.max}px) is the largest of ` +
    `${candidates.length} money elements on the dashboard`
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// CLAIM 3 — needs-attention distinguishes its two states without colour
// ─────────────────────────────────────────────────────────────────────────────
//
// Mutation-proven: deleting the classList.toggle('is-calm', ...) line makes the
// "nothing to do" case fail, because the band never leaves its alert state.

async function testAttentionBandSwitchesStateOnRealData() {
  // 7 items need reorder -> alert state (no is-calm)
  const alertCtx = loadRetailSystem({ capabilities: ['retail.reports'] });
  await alertCtx.RetailSystem._renderDashboard(makeElementStub());
  const alertToggles = alertCtx.classToggles.filter(([c]) => c === 'is-calm');
  assert.ok(
    alertToggles.length >= 1,
    'The needs-attention band never had its state set at all. Toggles seen: ' +
    JSON.stringify(alertCtx.classToggles)
  );
  assert.strictEqual(
    alertToggles[alertToggles.length - 1][1], false,
    'With 7 items below reorder level the band was put into its CALM state. ' +
    'Low stock is the only thing on this screen an owner can act on at 9am.'
  );

  // 0 items -> calm state. Same code path, different data.
  const calmSource = Object.assign({}, STATS, { low_stock_alerts: 0 });
  const calmCtx = loadRetailSystem({ capabilities: ['retail.reports'] });
  calmCtx.RetailSystem._get = () => Promise.resolve({ data: calmSource });
  await calmCtx.RetailSystem._renderDashboard(makeElementStub());
  const calmToggles = calmCtx.classToggles.filter(([c]) => c === 'is-calm');
  assert.ok(calmToggles.length >= 1, 'The band was never given a state on the zero-alert path.');
  assert.strictEqual(
    calmToggles[calmToggles.length - 1][1], true,
    'With nothing below reorder level the band stayed in its ALERT state. A ' +
    'dashboard that shouts on a good morning stops being read on a bad one.'
  );

  console.log('PASS: the attention band tracks real data into both of its states');
}

function testAttentionStatesDifferByMoreThanColour() {
  const ctx = loadRetailSystem({ capabilities: ['retail.reports'] });
  const content = makeElementStub();
  ctx.RetailSystem._renderDashboard(content);
  const { root, rules } = parseDashboardStyle(content.innerHTML);

  // The two states must carry DIFFERENT WORDS, not just different colours --
  // that is the cue that survives greyscale, a washed-out panel, and
  // colourblindness.
  const alertText = dom.allElements(root).find((el) => el.classes.includes('rdash-attention-alert'));
  const calmText = dom.allElements(root).find((el) => el.classes.includes('rdash-attention-calm'));
  const headline = dom.allElements(root).find((el) => el.classes.includes('rdash-attention-headline'));
  assert.ok(alertText && calmText, 'The attention band does not carry both a busy and a calm message.');
  assert.ok(headline, 'The attention band has no headline element.');

  const alertWords = dom.textOf(alertText);
  const calmWords = dom.textOf(calmText);
  const headlineWords = dom.textOf(headline);

  assert.ok(calmWords.length > 0, 'The calm state has no message of its own.');
  assert.notStrictEqual(
    alertWords, calmWords,
    'The busy and calm messages read identically, so the state is communicated by ' +
    'colour alone.'
  );
  // The headline ("Needs attention") is the band's LABEL and is shown in both
  // states. If the calm message merely repeats it, the band reads "Needs
  // attention / Needs attention" on a good morning and the only thing actually
  // distinguishing the two states is colour again. Comparing the calm message
  // against the alert message alone does not catch that -- it was the gap that
  // let this exact mutation survive an earlier version of this test.
  assert.notStrictEqual(
    calmWords, headlineWords,
    `The calm message ("${calmWords}") is the same text as the band's headline ` +
    `("${headlineWords}"), so in the calm state the band says nothing the alert state ` +
    'does not, and the difference collapses back onto colour.'
  );

  // The full sentence a reader actually sees must differ between states too.
  assert.notStrictEqual(
    `${headlineWords} ${alertWords}`.trim(), `${headlineWords} ${calmWords}`.trim(),
    'The band reads the same in both states once the headline is included.'
  );

  // ...and the calm rule must change something structural too, not merely swap
  // a colour: display, border, background, transform or padding.
  const calmRules = rules.filter((r) => r.selectors.some((s) => /\.rdash-attention\.is-calm/.test(s)));
  assert.ok(calmRules.length, 'No .rdash-attention.is-calm rule exists — the calm state is not styled.');
  const structuralProps = ['display', 'border-color', 'background', 'transform', 'padding-inline'];
  const changed = new Set();
  for (const r of calmRules) {
    for (const p of Object.keys(r.decls)) if (structuralProps.includes(p)) changed.add(p);
  }
  assert.ok(
    changed.size >= 2,
    'The calm state differs from the alert state by too little to survive greyscale: ' +
    'only ' + JSON.stringify([...changed]) + ' changes. Expected at least two of ' +
    JSON.stringify(structuralProps) + '.'
  );

  console.log(`PASS: attention states differ by wording plus ${changed.size} structural properties`);
}

// ─────────────────────────────────────────────────────────────────────────────
// CLAIM 4 — the recent-transactions money column is a real, mirrorable column
// ─────────────────────────────────────────────────────────────────────────────
//
// Mutation-proven: changing `.col-money { text-align:end }` to `text-align:right`
// fails, because `right` does not mirror under RTL.

async function testRecentTransactionsMoneyColumnIsTabularAndLogical() {
  const ctx = loadRetailSystem({ capabilities: ['retail.reports'] });
  const content = makeElementStub();
  await ctx.RetailSystem._renderDashboard(content);

  const { rules } = parseDashboardStyle(content.innerHTML);
  const rowRoot = dom.parseFragment(ctx.tbody.innerHTML);
  const moneyCell = dom.allElements(rowRoot).find(
    (el) => el.tag === 'td' && el.classes.includes('col-money')
  );
  assert.ok(
    moneyCell,
    'The Total column of a rendered transaction row is not marked .col-money, so ' +
    'nothing aligns it as a money column. Row: ' + ctx.tbody.innerHTML.slice(0, 300)
  );

  const align = dom.declaredValues(rules, moneyCell, 'text-align');
  assert.ok(align.length, 'The money column declares no text-align at all.');
  assert.ok(
    align.every((d) => !/\b(right|left)\b/.test(d.value)),
    'The money column aligns with a PHYSICAL keyword (' +
    align.map((d) => d.value).join(', ') + '). This product ships Arabic; a physical ' +
    'alignment pins the column to the wrong edge once the layout mirrors. Use ' +
    'text-align: end.'
  );
  assert.ok(
    align.some((d) => /\bend\b/.test(d.value)),
    'The money column is not end-aligned, so the amounts do not form a column that ' +
    'can be scanned or summed by eye. Got: ' + align.map((d) => d.value).join(', ')
  );

  // The amount inside it must be tabular, or the column only looks like one.
  const money = dom.allElements(moneyCell).find((el) => el.classes.includes('money'));
  assert.ok(
    money,
    'The amount in the Total column is not rendered through the .money helper, so it ' +
    'gets neither tabular figures nor negative marking. Cell: ' + dom.describe(moneyCell)
  );

  console.log('PASS: the recent-transactions money column is end-aligned and tabular');
}

// ─────────────────────────────────────────────────────────────────────────────
// CLAIM 4b — a clickable row has a keyboard path, and the row stays a row
// ─────────────────────────────────────────────────────────────────────────────
//
// Every Recent Transactions row opens a sale on click. It shipped as
// `<tr style="cursor:pointer" onclick="..._viewSale(id)">` and nothing else, so
// the only way to open a sale from this table was a mouse: a <tr> is not
// focusable, Tab never reached it, Enter and Space could not activate it, and a
// screen reader was read six cells and no control. On a touchscreen there is no
// hover either, so `cursor:pointer` plus a hover background announced nothing.
//
// The fix is NOT to make the <tr> a button. A row that becomes a button stops
// being a row: the columns lose their association with their headers, and an AT
// user loses the grid navigation that makes a six-column table readable at all.
// The row CONTAINS a button instead.
//
// Mutation-proven, each one run and confirmed red:
//   * remove the button, leave the bare onclick <tr>  -> "no keyboard path"
//   * give the <tr> role="button"/tabindex instead    -> "the row stopped being a row"
//   * drop event.stopPropagation() from the button    -> "would open twice"
//
// NOT claimed: blanking the aria-label alone does not fail here, and should
// not. An accessible name legitimately falls back to the control's contents
// (accname step 2F), and the button's contents are the receipt number — a
// weaker name than "View invoice S-1041", but a real one. What fails is a
// control with neither, which is what the name check below actually tests.

const NATIVELY_FOCUSABLE = new Set(['button', 'a', 'input', 'select', 'textarea', 'summary']);

/** `event.stopPropagation();RetailSystem._viewSale(7)` -> `RetailSystem._viewSale(7)` */
function normalisedHandler(value) {
  return String(value || '')
    .replace(/event\.stopPropagation\(\)\s*;?/g, '')
    .replace(/\s+/g, '')
    .replace(/;$/, '');
}

/**
 * How many places subsystem-retail.js renders a CLICKABLE TABLE ROW.
 *
 * The floor for the sweep below, derived from the product rather than written
 * here. `rows.length >= 1` was not a floor: the dashboard's own table satisfied
 * it, so the receipt-opener button could be replaced with a bare escaped string
 * at BOTH of its non-dashboard call sites and this file stayed green — which is
 * exactly what an adversarial verifier did.
 *
 * What is counted is the HAZARD — a <tr> that carries a click handler — not the
 * remedy, so deleting an in-row button does not lower the bar it is measured
 * against. Deleting a SCREEN from the corpus drops the found count below it.
 *
 * Comment lines are excluded, and that is not fussiness: the long note above
 * quotes `<tr style="cursor:pointer" onclick="..._viewSale(id)">` verbatim, so
 * a naive scan of this very file's subject counts the prose as a render site.
 */
function clickableRowRenderSites() {
  const src = fs.readFileSync(FRONTEND_FILE, 'utf8');
  let n = 0;
  for (const line of src.split('\n')) {
    if (/^\s*(\/\/|\*|\/\*)/.test(line)) continue;
    for (const _hit of line.matchAll(/<tr[^>]*\bonclick=/g)) n++;
  }
  assert.ok(
    n >= 3,
    `Found only ${n} clickable-row render site(s) in subsystem-retail.js. Either the ` +
    'product stopped building rows that way (rewrite this derivation, do not delete ' +
    'it) or the scan is broken — and a broken scan sets this sweep\'s floor to zero.'
  );
  return n;
}

async function testEveryClickableRowHasAKeyboardPath(h) {
  const expectedSites = clickableRowRenderSites();

  // Derived from the render: every <tr> carrying a click handler, on every
  // screen the shared corpus builds. Never a hand-written list of rows, and —
  // this is the part that was wrong — never one screen.
  const rows = [];
  for (const screen of h.screens) {
    for (const el of h.allElements(screen.root)) {
      if (el.tag === 'tr' && el.attrs.onclick) rows.push({ screen: screen.name, el });
    }
  }

  // ANTI-VACUITY, DERIVED. Everything below is a loop over `rows`; a failed
  // fetch or a dropped handler on ONE screen must not be absorbed by the others.
  const screensWithRows = new Set(rows.map((r) => r.screen));
  assert.ok(
    screensWithRows.size >= expectedSites,
    `Clickable rows were found on only ${screensWithRows.size} screen(s) ` +
    `(${[...screensWithRows].join(', ') || 'none'}), but subsystem-retail.js renders a ` +
    `<tr ... onclick=> in ${expectedSites} places. At least one screen that makes a whole ` +
    'row clickable is not in the corpus, so this sweep proves nothing about it.'
  );

  const problems = [];
  for (const { screen, el: row } of rows) {
    const what = `${screen} ${dom.describe(row)}`;

    // The row must remain a row. role="button" on a <tr> removes it from the
    // table's grid for an AT user — the exact thing the columns exist for.
    if (row.attrs.role || row.attrs.tabindex !== undefined) {
      problems.push(
        `${what} carries role=${JSON.stringify(row.attrs.role)} / ` +
        `tabindex=${JSON.stringify(row.attrs.tabindex)}. A <tr> retrofitted into a ` +
        'control stops being a row: the cells lose their header association and the ' +
        'table stops being navigable as a table.'
      );
    }

    // What the row's own handler does — the action a keyboard user must be able
    // to reach by some other route.
    //
    // Compared as a NORMALISED CALL, not matched against a known function name.
    // The previous version recognised `_viewSale(<digits>)` and nothing else,
    // and reported anything else as "an onclick this test does not recognise" —
    // which on the one screen it rendered never happened, and on the screens it
    // did not render would have been the only thing it ever said. A row that
    // opens a CUSTOMER is the same defect as a row that opens a sale.
    const rowAction = normalisedHandler(row.attrs.onclick);
    const openers = dom.allElements(row).filter(
      (el) => NATIVELY_FOCUSABLE.has(el.tag) && normalisedHandler(el.attrs.onclick) === rowAction
    );
    if (!openers.length) {
      problems.push(
        `${what} runs ${JSON.stringify(rowAction)} on click, but contains no natively ` +
        'focusable element that does the same. There is therefore NO keyboard, ' +
        'scanner or screen-reader path to it — the row is mouse-only.'
      );
      continue;
    }

    for (const opener of openers) {
      const which = `${screen} ${dom.describe(opener)}`;
      if (opener.tag === 'button' && opener.attrs.type !== 'button') {
        problems.push(`${which} has no type="button"; the HTML default is submit.`);
      }
      // The row ALSO handles the click, so without stopPropagation one press
      // runs the action twice and opens the detail view on top of itself.
      if (!/stopPropagation/.test(opener.attrs.onclick || '')) {
        problems.push(
          `${which} does not stop propagation, but its ancestor row handles the ` +
          'same click. One activation would run the action twice — once for the ' +
          'button, once for the row it bubbled to.'
        );
      }
      // An accessible name, and one that identifies WHICH record.
      //
      // The rule is about IDENTITY, not about digits. It used to require a
      // digit in the name, which is true of a receipt number and false of a
      // customer — and on a corpus of one screen, all of whose rows opened a
      // sale, the difference never came up. What actually matters is that an
      // aria-label does not REPLACE the row's identifier with a bare verb:
      // aria-label overrides the contents entirely (accname step 2C beats 2F),
      // so "View invoice" on a button reading "S-1041" announces eight
      // identical controls where the screen shows eight different ones.
      const shown = dom.textOf(opener).replace(/\s+/g, ' ').trim();
      const aria = (opener.attrs['aria-label'] || '').replace(/\s+/g, ' ').trim();
      if (!aria && !shown) {
        problems.push(`${which} has no accessible name at all.`);
      } else if (aria && shown && !aria.includes(shown)) {
        problems.push(
          `${which} shows ${JSON.stringify(shown)} but is announced as ${JSON.stringify(aria)}, ` +
          'which does not contain it. The aria-label REPLACES the contents for an AT user, ' +
          'so this withholds the one thing that tells the rows apart.'
        );
      }
    }
  }

  assert.deepStrictEqual(
    problems, [],
    'Clickable rows are not operable without a pointer:\n  ' +
    problems.join('\n  ') +
    '\n\nA till is driven by a barcode scanner — which is a keyboard — and by a ' +
    'finger on a screen that has no hover state at all.'
  );

  console.log(
    `PASS: all ${rows.length} clickable row(s) across ${screensWithRows.size} screen(s) ` +
    `(${[...screensWithRows].join(', ')}) carry a real in-row control — floor of ` +
    `${expectedSites}, derived from the <tr onclick> render sites in subsystem-retail.js`
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// CLAIM 4c — the negative cue lands on the VALUE, not on its container
// ─────────────────────────────────────────────────────────────────────────────
//
// #r-k-rev is net revenue and is deliberately shown UNCLAMPED, so on a day when
// refunds exceed sales it goes negative — that is the whole reason the figure
// exists in that form. It was filled in with `textContent = _fmt(n)`, which can
// emit a hyphen and nothing else: no class, so css/main.css's .money--negative
// (bold + the negative token) and .money--accounting (parentheses) could never
// reach it. One ASCII hyphen, on a washed-out shop panel, is not a distinction.
//
// Mutation-proven:
//   * revert to `textContent = this._fmt(...)`   -> no class toggled, no U+2212
//   * make _setMoney mark EVERY amount negative  -> the positive case fails

async function testNegativeHeadlineRevenueIsMarkedOnTheValueItself() {
  // The real shape: today's refunds exceeded today's sales.
  const negCtx = loadRetailSystem({ capabilities: ['retail.reports'] });
  negCtx.RetailSystem._get = () => Promise.resolve({
    data: Object.assign({}, STATS, { today_sales: -120.5, today_returns: 300 }),
  });
  await negCtx.RetailSystem._renderDashboard(makeElementStub());
  const negRev = negCtx.els['r-k-rev'];

  assert.ok(negRev, 'The harness never saw #r-k-rev, so nothing below is checkable.');
  assert.ok(
    /−/.test(negRev.textContent),
    'A NEGATIVE net revenue does not carry a U+2212 MINUS SIGN in its own text. ' +
    'Strip every colour from this screen — which a sun-lit or badly calibrated ' +
    'panel does for free — and the figure must still read as negative. Got: ' +
    JSON.stringify(negRev.textContent)
  );
  assert.ok(
    negRev.classList.contains('money--negative'),
    'A negative #r-k-rev never received .money--negative, so css/main.css cannot ' +
    'give it either of its non-colour cues (bold weight, accounting parentheses). ' +
    'The marking has to land on the VALUE — putting it on the KPI card would mark ' +
    'the card, not the number. Classes seen: ' + JSON.stringify(negRev.classes)
  );
  assert.ok(
    negRev.classList.contains('money--accounting'),
    'A negative #r-k-rev did not opt into .money--accounting. Parentheses are the ' +
    'convention that survives greyscale and a photocopier. Classes seen: ' +
    JSON.stringify(negRev.classes)
  );

  // CONDITIONALITY. Without this the claim above is satisfiable by marking every
  // amount negative, which would make the marking carry no information at all —
  // the guard's pass condition would then be the bug signature.
  const posCtx = loadRetailSystem({ capabilities: ['retail.reports'] });
  await posCtx.RetailSystem._renderDashboard(makeElementStub());
  const posRev = posCtx.els['r-k-rev'];
  assert.ok(
    /\d/.test(posRev.textContent),
    'The positive control case rendered no figure at all: ' + JSON.stringify(posRev.textContent)
  );
  assert.ok(
    !posRev.classList.contains('money--negative') && !/−/.test(posRev.textContent),
    'A POSITIVE net revenue is also being marked negative, so the marking says ' +
    'nothing. Got: ' + JSON.stringify(posRev.textContent) + ' ' + JSON.stringify(posRev.classes)
  );

  console.log('PASS: a negative headline revenue is marked on the value; a positive one is not');
}

// ─────────────────────────────────────────────────────────────────────────────
// CLAIM 5 — no dashboard surface reaches for the un-themed --text-muted
// ─────────────────────────────────────────────────────────────────────────────
//
// Mutation-proven: putting var(--text-muted) back on the chart empty state
// fails. This widens retail_dashboard_transaction_contrast_test.js, which only
// guards the Recent Transactions tbody, to the whole screen.

async function testNoDashboardSurfaceUsesTheUnthemedMutedToken() {
  const ctx = loadRetailSystem({ capabilities: ['retail.reports'] });
  const content = makeElementStub();
  await ctx.RetailSystem._renderDashboard(content);

  // Every surface the render can produce, including the two chart empty states
  // -- which are only reachable because the harness supplies a Chart stub and
  // canvas parents. They were invisible to an earlier version of this sweep,
  // and a --text-muted regression on them survived it.
  const fragments = [
    content.innerHTML,
    ctx.tbody.innerHTML,
    ctx.chartHosts['r-dash-hourly'] ? ctx.chartHosts['r-dash-hourly'].innerHTML : '',
    ctx.chartHosts['r-dash-pay'] ? ctx.chartHosts['r-dash-pay'].innerHTML : '',
  ];
  assert.ok(
    fragments[2] && fragments[3],
    'The chart empty states never rendered, so this sweep cannot see them. That is a ' +
    'harness failure, not a pass: those two surfaces are exactly where an un-themed ' +
    'token last hid.'
  );
  const surfaces = fragments.flatMap((html) => stylingSurfaces(dom.parseFragment(html)));
  assert.ok(
    surfaces.length >= 12,
    'Found only ' + surfaces.length + ' styling surfaces across the dashboard; the ' +
    'sweep below would be near-vacuous. Either the render failed or the harness is stale.'
  );
  const offenders = surfaces.filter((s) => /var\(--text-muted\)/.test(s.css));

  assert.deepStrictEqual(
    offenders.map((s) => s.where), [],
    'These dashboard styling surfaces still use var(--text-muted):\n  ' +
    offenders.map((s) => `${s.where} -> ${s.css}`).join('\n  ') +
    '\n\n--text-muted is defined once at :root and never redefined for the light ' +
    'theme, so it renders around 2.6:1 on the default white card -- under the 4.5:1 ' +
    'AA floor. The theme-aware token is --text-dim. ' +
    'retail_dashboard_transaction_contrast_test.js guards the Recent Transactions ' +
    'tbody against exactly this; the trap was still live on the rest of the screen.'
  );

  console.log(`PASS: all ${surfaces.length} dashboard styling surfaces avoid --text-muted`);
}

// ─────────────────────────────────────────────────────────────────────────────
// RTL by construction
// ─────────────────────────────────────────────────────────────────────────────
//
// Mutation-proven: turning `.rdash .ret-table .col-money { text-align:end }`
// into `text-align:right` fails here as well as in the money-column test.

async function testDashboardLayoutIsMirrorSafeByConstruction() {
  const ctx = loadRetailSystem({ capabilities: ['retail.reports'] });
  const content = makeElementStub();
  await ctx.RetailSystem._renderDashboard(content);

  const surfaces = [
    ...stylingSurfaces(dom.parseFragment(content.innerHTML)),
    ...stylingSurfaces(dom.parseFragment(ctx.tbody.innerHTML)),
  ];
  assert.ok(surfaces.length >= 10, 'Too few styling surfaces to check the dashboard.');

  const violations = [];
  for (const s of surfaces) {
    for (const hit of dom.physicalDirectionHits(s.css)) {
      violations.push(`${s.where}: "${hit.snippet}" — ${hit.why}`);
    }
  }

  assert.deepStrictEqual(
    violations, [],
    'The dashboard pins layout to physical edges and will not mirror correctly in ' +
    'Arabic:\n  ' + violations.join('\n  ')
  );

  console.log(`PASS: all ${surfaces.length} dashboard styling surfaces use logical properties only`);
}

// ─────────────────────────────────────────────────────────────────────────────

/* PER-TEST ISOLATION -- see retail_design_money_test.js for the reasoning and
   the measurement. Twelve checks behind one abort, and the harness call used to
   sit INLINE in the middle of the sequence (`await test...(await
   render.harness())`), so a corpus that failed to build took the four checks
   after it down with it and reported neither fact. The floor below exists
   because this list is assembled conditionally. */
const EXPECTED_CHECKS = 12;

async function main() {
  const checks = [
    ['the cashier landing renders with zero API calls', testCashierLandingRendersWithZeroApiCalls],
    ['a manager on the same path still fetches', testManagerOnTheSamePathStillFetches],
    ['the landing is inert when capabilities are unknown', testLandingIsInertWhenCapabilitiesAreUnknown],
    ['the cashier landing avoids the unthemed muted token', testCashierLandingAvoidsTheUnthemedMutedToken],
    ["today's revenue is the largest money element", testTodaysRevenueIsTheLargestMoneyElement],
    ['the attention band switches state on real data', testAttentionBandSwitchesStateOnRealData],
    ['attention states differ by more than colour', testAttentionStatesDifferByMoreThanColour],
    ['the Recent Transactions money column is tabular and logical', testRecentTransactionsMoneyColumnIsTabularAndLogical],
    ['negative headline revenue is marked on the value itself', testNegativeHeadlineRevenueIsMarkedOnTheValueItself],
    ['no dashboard surface uses the unthemed muted token', testNoDashboardSurfaceUsesTheUnthemedMutedToken],
    ['the dashboard layout is mirror-safe by construction', testDashboardLayoutIsMirrorSafeByConstruction],
  ];

  const failures = [];
  const record = (name, err) => {
    failures.push(name);
    console.error(`FAIL: ${name}`);
    console.error('      ' + String((err && err.message) || err).replace(/\n/g, '\n      '));
  };

  let h = null;
  try {
    h = await render.harness();
  } catch (err) {
    record('render.harness() (the keyboard-path check could not run)', err);
  }
  if (h) checks.push(['every clickable row has a keyboard path', () => testEveryClickableRowHasAKeyboardPath(h)]);
  const attempted = checks.length + (h ? 0 : 1);

  for (const [name, fn] of checks) {
    try {
      await fn();
    } catch (err) {
      record(name, err);
    }
  }

  if (attempted < EXPECTED_CHECKS) {
    console.error(
      `FAIL: retail_surface_dashboard_test.js ran only ${attempted} of ${EXPECTED_CHECKS} known checks.`
    );
    process.exitCode = 1;
    return;
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_surface_dashboard_test.js — ${failures.length} of ${attempted} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_surface_dashboard_test.js — ${attempted} checks`);
}

main().catch((err) => {
  console.error('FAIL: retail_surface_dashboard_test.js (runner)');
  console.error(err && err.message ? err.message : err);
  process.exitCode = 1;
});
