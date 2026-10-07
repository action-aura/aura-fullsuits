/**
 * LOYALTY-EARN: the settings hub exposes the loyalty earn rate ("Spend per
 * point", #credit-loyalty-earn) and it actually travels.
 *
 * settings-hub.js is one IIFE with no exports for the credit card, so this
 * pulls the REAL text of saveCredit() / loadCredit() / the card markup out of
 * the shipped file by brace matching and runs it against stubs. Nothing here
 * re-implements the logic under test.
 *
 * Claims:
 *   1. the input exists with a <label for=...> in the loyalty card;
 *   2. it is in the dirty-tracking field list of the credit group;
 *   3. saveCredit POSTs loyalty_earn_spend (value typed, '0' stays '0');
 *   4. bad values (empty, negative, NaN, > 1,000,000) never reach the POST;
 *   5. loadCredit shows the stored value, including "0" (never blank), so off
 *      and default look different;
 *   6. en + ar locales both carry the three new strings, Arabic non-empty.
 *
 * Run: node products/retail/tests/retail_loyalty_earn_ui_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONT = path.join(__dirname, '..', 'frontend');
const SRC = process.env.EARN_UI_SRC || path.join(FRONT, 'settings-hub.js');
const src = fs.readFileSync(SRC, 'utf8');

function extractFn(header) {
  const start = src.indexOf(header);
  assert.ok(start >= 0, 'missing in settings-hub.js: ' + header);
  let i = src.indexOf('{', start);
  let depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) return src.slice(start, i + 1); }
  }
  throw new Error('unbalanced: ' + header);
}

const saveText = extractFn('async function saveCredit()');
const loadText = extractFn('async function loadCredit()');

function run(values, stored) {
  const els = {};
  const get = (id) => (els[id] = els[id] || { value: '', hidden: false });
  const ids = ['credit-mode', 'credit-limit', 'credit-enforce', 'credit-loyalty', 'credit-loyalty-earn', 'credit-cashier-disc', 'credit-limit-wrap'];
  ids.forEach(get);
  Object.assign(get('credit-mode'), { value: 'none' });
  Object.assign(get('credit-enforce'), { value: 'warn' });
  Object.entries(values || {}).forEach(([k, v]) => { get(k).value = v; });
  const posts = [];
  const toasts = [];
  const sandbox = {
    $: get,
    t: (s) => s,
    toast: (m, ty) => toasts.push([m, ty]),
    console,
    rebaseline() {},
    syncCreditLimitVisibility() {},
    R: () => ({
      _post: async (u, b) => { posts.push([u, b]); return { status: 'success' }; },
      _get: async () => ({ data: stored || {} }),
    }),
  };
  vm.createContext(sandbox);
  vm.runInContext(saveText + '\n' + loadText + '\nthis.saveCredit = saveCredit; this.loadCredit = loadCredit;', sandbox);
  return { sandbox, els, posts, toasts };
}

(async () => {
  // 1. markup
  assert.ok(/<label for="credit-loyalty-earn">/.test(src), 'label for credit-loyalty-earn');
  assert.ok(/<input type="number" id="credit-loyalty-earn"/.test(src), 'input #credit-loyalty-earn');
  const cardStart = src.indexOf('id="loyalty-card"');
  const cardEnd = src.indexOf('id="cashier-discount-card"');
  const card = src.slice(cardStart, cardEnd);
  assert.ok(card.includes('credit-loyalty-earn'), 'earn field lives in the loyalty card');
  assert.ok(card.includes('Spend this much to earn 1 loyalty point. 0 turns earning off.'), 'lead text');

  // 2. dirty tracking
  const m = src.match(/credit: \{\s*section: 'credit',\s*fields: \[([^\]]*)\]/);
  assert.ok(m, 'credit group');
  assert.ok(m[1].includes("'credit-loyalty-earn'"), 'earn field is in the credit group dirty list');

  // 3. POST carries it
  let r = run({ 'credit-loyalty-earn': '5' });
  assert.strictEqual(await r.sandbox.saveCredit(), true);
  assert.strictEqual(r.posts.length, 1);
  assert.strictEqual(r.posts[0][1].loyalty_earn_spend, '5');
  r = run({ 'credit-loyalty-earn': '0' });
  assert.strictEqual(await r.sandbox.saveCredit(), true);
  assert.strictEqual(r.posts[0][1].loyalty_earn_spend, '0', '0 (off) is sent as 0');
  r = run({ 'credit-loyalty-earn': '2.5' });
  await r.sandbox.saveCredit();
  assert.strictEqual(r.posts[0][1].loyalty_earn_spend, '2.5');

  // 4. refused client-side, nothing sent
  for (const bad of ['', '-1', 'abc', '1000001']) {
    r = run({ 'credit-loyalty-earn': bad });
    assert.strictEqual(await r.sandbox.saveCredit(), false, 'refused: ' + JSON.stringify(bad));
    assert.strictEqual(r.posts.length, 0, 'no POST for ' + JSON.stringify(bad));
    assert.strictEqual(r.toasts.length, 1);
  }

  // 5. load shows the value, "0" included
  r = run({}, { loyalty_earn_spend: '0' });
  await r.sandbox.loadCredit();
  assert.strictEqual(r.els['credit-loyalty-earn'].value, '0', 'off shows 0, not blank');
  r = run({}, { loyalty_earn_spend: '2.5' });
  await r.sandbox.loadCredit();
  assert.strictEqual(r.els['credit-loyalty-earn'].value, '2.5');
  r = run({}, {});
  await r.sandbox.loadCredit();
  assert.strictEqual(r.els['credit-loyalty-earn'].value, '10', 'older server: shows the old default');

  // 6. locales
  for (const lang of ['en', 'ar']) {
    const j = JSON.parse(fs.readFileSync(path.join(FRONT, 'locales', lang + '.json'), 'utf8'));
    for (const k of ['Spend per point',
      'Spend this much to earn 1 loyalty point. 0 turns earning off.',
      'Spend per point must be a number from 0 to 1,000,000 (0 turns earning off).']) {
      assert.ok(typeof j[k] === 'string' && j[k].length > 0, lang + ' missing: ' + k);
      if (lang === 'ar') assert.ok(/[؀-ۿ]/.test(j[k]), 'ar is Arabic script: ' + k);
    }
  }
  console.log('retail_loyalty_earn_ui_test.js: all claims passed');
})().catch((e) => { console.error(e); process.exit(1); });
