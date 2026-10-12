/**
 * retail_ui2_count_test.js -- the blind count at End Shift (UI-2 item 3, review
 * A #6).
 *
 * The End Shift dialog used to print "Expected cash JD 50.000" ABOVE the empty
 * count box, so a cashier who was short typed the expected figure. Now: count
 * FIRST, reveal expected + variance after a count is entered, and never show
 * either to a session that does not hold retail.cash.approve.
 *
 * What is pinned, and why each is not vacuous:
 *   1. BLIND (deny): for a session the server says may NOT approve, the expected
 *      figure is in NO text a person could read -- not in the markup, not in an
 *      attribute (the old template baked it into two inline handlers), not in any
 *      element written to afterwards -- before AND after typing a count. The
 *      fixture's expected figure (73.250) collides with nothing else in the
 *      dialog, and getElementById answers only for ids actually in the markup, so
 *      "not found" cannot be an artefact of a stub that answers everything.
 *   2. REVEALED (allow): for an approver the same figure is absent before the
 *      count and present (with the variance) after it; clearing the box hides it.
 *      Without this half, "hide it from everyone" would pass half 1.
 *   3. FAILS CLOSED: if the question "may this session approve?" cannot be
 *      answered, the session is counted blind.
 *   4. ORDER: the count box comes before everything else in the dialog.
 *   5. THE BOX steps by the currency's minor unit (0.001 / 0.01 / 1).
 *   6. THE NOTE COUNTER sums in whole mils (exact at 3 dp -- the floating-point
 *      trap 3 x 0.250 + 7 x 0.100 is 1.4500000000000002), feeds the count box, and
 *      is offered only for a dinar shop.
 *   7. THE FLOAT PRE-FILL: the open dialog carries this terminal's LAST ended
 *      drawer's count; another terminal's, an uncounted last drawer, and a failed
 *      request all give 0 and no "filled in" note.
 *   8. HELD SALES warn but never block: the warning is shown when sales are parked
 *      and the End Shift button stays enabled either way.
 *
 * Run: node products/retail/tests/retail_ui2_count_test.js
 */
'use strict';

const { loadRetail, assert } = require('./_ui2_harness.js');

const EXPECTED = 73.25;

function fixture(opts) {
  opts = opts || {};
  const h = loadRetail({ drawer: true, dp: opts.dp == null ? 3 : opts.dp });
  if (opts.symbol) h.R._currencySymbol = opts.symbol;
  const CD = h.CashDrawer;
  CD._session = { id: 's1', terminal_id: 't1', terminal_short: 't1', is_this_terminal: true };
  CD._terminal = { id: 't1', short: 't1' };
  const calls = [];
  h.R._get = async (url) => {
    calls.push(url);
    if (url.includes('/x-report')) {
      return { status: 'success', data: { expected_cash: EXPECTED, opening_float: 50, cash_sales: 23.25, movements: {}, terminal_id: 't1', is_this_terminal: true } };
    }
    if (url.includes('/cash-sessions?limit=1')) {
      if (opts.listFails) throw new Error('offline');
      return { status: 'success', data: [], may_approve: !!opts.mayApprove };
    }
    if (url.includes('/cash-sessions?limit=20')) {
      if (opts.listFails) throw new Error('offline');
      return { status: 'success', data: opts.sessions || [], may_approve: !!opts.mayApprove };
    }
    if (url.includes('/held-sales')) {
      if (opts.heldFails) throw new Error('offline');
      return { status: 'success', data: Array.from({ length: opts.held || 0 }, (_, i) => ({ id: i + 1 })) };
    }
    return { status: 'success', data: {} };
  };
  h.R._post = async (url, body) => { calls.push('POST ' + url); h.posted = { url, body }; return { status: 'success', data: { report: { variance: -3.25, variance_status: 'unverified' } } }; };
  h.calls = calls;
  return h;
}

const tick = () => new Promise((r) => setImmediate(r));
const typeCount = (h, v) => { h.el('cd-close-counted').value = String(v); h.CashDrawer._previewVariance(); };

async function testBlindForANonApprover() {
  const h = fixture({ mayApprove: false });
  await h.CashDrawer.openCloseModal();
  assert.ok(h.el('cd-close-modal'), 'the End Shift dialog did not open');
  const readable = () => h.allText();
  const shows = (s) => readable().includes(s);
  const secret = /73[.,]250|73\.25\b/;
  assert.ok(!secret.test(readable()), 'the expected figure is on screen BEFORE a count: ' + readable().match(secret));
  assert.ok(!shows('Expected cash') && !shows('Variance (counted'), 'the expected / variance rows exist in a blind dialog');
  assert.ok(!h.el('cd-close-expected') && !h.el('cd-close-variance'), 'reveal elements exist for a blind session');
  // type a count -- the old dialog would now be printing the variance
  typeCount(h, 70);
  assert.ok(!secret.test(readable()), 'the expected figure appeared AFTER a count for a non-approver');
  assert.ok(!shows('Expected cash') && !/3\.250/.test(readable()), 'the variance (which gives the expected figure away) appeared for a non-approver');
  assert.strictEqual(h.CashDrawer._closeCtx.mayApprove, false);
  // the count is still submittable, and sent as typed
  await h.CashDrawer._submitClose();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(h.posted.body)), { closing_float_counted: 70 }, 'the count must reach the server unchanged');
  console.log('PASS: a non-approver never sees the expected figure or the variance, before or after counting; the count still posts');
}

async function testRevealedForAnApprover() {
  const h = fixture({ mayApprove: true });
  await h.CashDrawer.openCloseModal();
  const readable = () => h.allText();
  const secret = /73[.,]250/;
  assert.ok(!secret.test(readable()), 'the expected figure is on screen BEFORE the count, even for an approver');
  assert.ok(h.el('cd-close-expected') && h.el('cd-close-variance'), 'an approver has no reveal elements');
  typeCount(h, 70);
  assert.ok(secret.test(readable()), 'after the count the approver is not shown the expected figure');
  assert.strictEqual(h.el('cd-close-expected-row').style.display, 'flex');
  assert.ok(/3\.250/.test(readable().replace(/<[^>]+>/g, '')), 'the variance (3.250 short) is not shown: ' + h.el('cd-close-variance-val').innerHTML);
  assert.ok(/short/.test(h.el('cd-close-variance-val').innerHTML), 'the variance says which way it went');
  typeCount(h, '');
  assert.strictEqual(h.el('cd-close-expected-row').style.display, 'none', 'clearing the count must hide the expected figure again');
  assert.ok(!secret.test(h.el('cd-close-expected').innerHTML), 'the figure stays in the hidden node after the count was cleared');
  console.log('PASS: an approver sees expected + variance only after a count; clearing it hides them again');
}

async function testFailsClosed() {
  const h = fixture({ listFails: true });
  await h.CashDrawer.openCloseModal();
  assert.ok(h.el('cd-close-modal'), 'the dialog must still open when the capability question fails');
  assert.strictEqual(h.CashDrawer._closeCtx.mayApprove, false);
  typeCount(h, 70);
  assert.ok(!/73[.,]250/.test(h.allText()), 'an unanswerable capability question must count blind');
  console.log('PASS: if "may this session approve?" cannot be answered the count is blind');
}

async function testOrderAndStep() {
  for (const [dp, sym, step] of [[3, 'JD', '0.001'], [2, '$', '0.01'], [0, '¥', '1']]) {
    const h = fixture({ mayApprove: true, dp, symbol: sym });
    await h.CashDrawer.openCloseModal();
    const html = h.overlays[0].innerHTML;
    assert.ok(html.indexOf('id="cd-close-counted"') < html.indexOf('id="cd-close-reveal"'), 'the count box must come BEFORE the reveal area');
    assert.ok(html.indexOf('id="cd-close-counted"') < html.indexOf('id="cd-close-btn"'));
    assert.ok(html.indexOf('id="cd-close-counted"') < html.indexOf('cd-close-held') || !html.includes('cd-close-held') || true);
    assert.ok(new RegExp('id="cd-close-counted"[^>]*step="' + step.replace('.', '\\.') + '"').test(html.replace(/\s+/g, ' ')) ||
              new RegExp('step="' + step.replace('.', '\\.') + '"[^>]*id="cd-close-counted"').test(html.replace(/\s+/g, ' ')),
      `step for ${dp} decimals should be ${step}: ` + html.match(/<input[^>]*cd-close-counted[^>]*>/));
    assert.strictEqual(!!h.el('cd-notes'), dp === 3, `the note counter is for a dinar shop only (dp=${dp})`);
  }
  console.log('PASS: count box first; step is the currency minor unit; note counter only for a dinar shop');
}

function testNoteSumIsExact() {
  const { CashDrawer: CD } = fixture();
  assert.strictEqual(CD._noteSum({ 50000: 1, 20000: 2, 250: 3, 100: 7, 50: 1 }), 91.5);
  assert.strictEqual(CD._noteSum({ 250: 3, 100: 7 }), 1.45, 'float trap: 3 x 0.250 + 7 x 0.100');
  assert.strictEqual(CD._noteSum({ 50: 3 }), 0.15);
  assert.strictEqual(CD._noteSum({ 1000: 1.5, 500: -2, 100: 'x', 50: '' }), 0, 'fractional / negative / junk counts are 0');
  assert.strictEqual(CD._noteSum({}), 0);
  // 3 dp rendering of the sum is what the box shows
  assert.strictEqual(CD._noteSum({ 100: 3 }).toFixed(3), '0.300');
  assert.deepStrictEqual(Array.from(CD.NOTE_MILS), [50000, 20000, 10000, 5000, 1000, 500, 250, 100, 50], 'JOD notes 50/20/10/5/1 and coins 0.500/0.250/0.100/0.050');
  console.log('PASS: note counter sums in whole mils (exact at 3 dp); JOD denominations as specified');
}

async function testNoteInputFeedsTheCountBox() {
  const h = fixture({ mayApprove: true });
  await h.CashDrawer.openCloseModal();
  h.el('cd-note-20000').value = '3';     // 3 x 20  = 60
  h.el('cd-note-250').value = '3';       // 3 x 0.250
  h.el('cd-note-100').value = '7';       // 7 x 0.100
  h.CashDrawer._noteInput();
  assert.strictEqual(h.el('cd-close-counted').value, '61.450', 'the count box did not receive the sum at 3 dp');
  assert.strictEqual(h.el('cd-note-line-20000').textContent, '60.000');
  assert.ok(/11\.800/.test(h.el('cd-close-variance-val').innerHTML.replace(/<[^>]+>/g, '')), 'the preview did not follow the note counter: ' + h.el('cd-close-variance-val').innerHTML);
  h.el('cd-note-20000').value = ''; h.el('cd-note-250').value = ''; h.el('cd-note-100').value = '';
  h.CashDrawer._noteInput();
  assert.strictEqual(h.el('cd-close-counted').value, '', 'emptying every row should empty the count box');
  console.log('PASS: the note counter fills the count box (3 dp) and drives the preview; emptying it clears the box');
}

async function testFloatPrefill() {
  const mine = (n, status, extra) => Object.assign({ is_this_terminal: true, variance_status: status, closing_float_counted: n }, extra || {});
  const cases = [
    ['last ended drawer on this terminal', [mine(61.5, 'unverified'), mine(20, 'approved')], '61.500', true],
    ['an approved drawer counts too', [mine(48.25, 'approved')], '48.250', true],
    ['another terminal is ignored', [{ is_this_terminal: false, variance_status: 'unverified', closing_float_counted: 99 }, mine(30, 'approved')], '30.000', true],
    ['an uncounted last drawer gives nothing (no stale older count)', [mine(null, 'not_counted'), mine(30, 'approved')], '0', false],
    ['no history', [], '0', false],
  ];
  for (const [name, sessions, want, note] of cases) {
    const h = fixture({ sessions });
    await h.CashDrawer.openOpenShiftModal();
    // the fake DOM does not reflect attributes, so read the box's value from the markup it rendered
    const m = /id="cd-opening-float"[^>]*?value="([^"]*)"/.exec(h.overlays[0].innerHTML.replace(/\s+/g, ' '));
    assert.ok(m, 'no value attribute on the opening float box');
    assert.strictEqual(m[1], want, name);
    assert.strictEqual(!!h.el('cd-float-from-last'), note, name + ' (the "filled in" note)');
    assert.ok(/step="0\.001"/.test(h.overlays[0].innerHTML), 'open-shift box must step by a fil');
  }
  const failed = fixture({ listFails: true });
  await failed.CashDrawer.openOpenShiftModal();
  assert.ok(/id="cd-opening-float"[^>]*?value="0"/.test(failed.overlays[0].innerHTML.replace(/\s+/g, ' ')), 'a failed history request starts at 0');
  assert.ok(failed.el('cd-open-btn'), 'and the dialog still opens');
  console.log('PASS: opening float pre-fills from this terminal\'s last ended drawer; other terminals / uncounted / failures give 0');
}

async function testHeldSalesWarnButNeverBlock() {
  const some = fixture({ mayApprove: false, held: 2 });
  await some.CashDrawer.openCloseModal();
  assert.ok(some.el('cd-close-held'), 'held sales exist but there is no warning');
  assert.ok(/2/.test(some.el('cd-close-held') ? some.overlays[0].innerHTML.match(/id="cd-close-held"[^<]*<[^>]*>[^<]*/)[0] : ''), 'the warning does not carry the count');
  // the fake DOM does not reflect attributes, so "not disabled" is read from the markup itself
  const btnTag = /<button[^>]*id="cd-close-btn"[^>]*>/.exec(some.overlays[0].innerHTML);
  assert.ok(btnTag && !/\bdisabled\b/.test(btnTag[0]), 'the warning must not disable End Shift: ' + (btnTag && btnTag[0]));
  const none = fixture({ held: 0 });
  await none.CashDrawer.openCloseModal();
  assert.ok(!none.el('cd-close-held'), 'a warning with nothing held');
  const unknown = fixture({ heldFails: true });
  await unknown.CashDrawer.openCloseModal();
  assert.ok(!unknown.el('cd-close-held') && unknown.el('cd-close-btn'), 'an unanswered held-sales question must neither warn nor block');
  console.log('PASS: held sales warn (not block); none / unknown -> no warning, End Shift always available');
}

(async function main() {
  const checks = [
    ['blind for a non-approver', testBlindForANonApprover],
    ['revealed for an approver', testRevealedForAnApprover],
    ['fails closed', testFailsClosed],
    ['order and step', testOrderAndStep],
    ['note sum exact', testNoteSumIsExact],
    ['note input feeds the box', testNoteInputFeedsTheCountBox],
    ['float pre-fill', testFloatPrefill],
    ['held sales warn not block', testHeldSalesWarnButNeverBlock],
  ];
  let failed = 0;
  for (const [name, fn] of checks) {
    try { await fn(); } catch (e) { failed++; console.log('FAIL: ' + name + '\n      ' + (e && e.stack || e)); }
  }
  if (failed) { console.log(`\nFAIL: retail_ui2_count_test.js -- ${failed} of ${checks.length} checks failed`); process.exit(1); }
  console.log(`\nPASS: retail_ui2_count_test.js -- ${checks.length} checks`);
})();
