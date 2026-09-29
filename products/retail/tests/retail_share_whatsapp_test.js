/**
 * retail_share_whatsapp_test.js — P1a item #6 ("Send on WhatsApp").
 *
 * Covers products/retail/frontend/share.js's `window.AuraShare` (loaded here
 * via plain CommonJS `require`, since share.js guards every export with
 * `typeof window`/`typeof module` -- see that file's own header comment):
 *
 *   1. normalizeJoPhone: the full table contract.md asks for -- 07…, 7…,
 *      009627…, +9627…, Arabic-Indic digits, spaces/dashes, and a table of
 *      genuinely invalid inputs that must all come back `false`.
 *   2. whatsapp(): builds https://wa.me/9627XXXXXXXX?text=... with the text
 *      correctly percent-encoded, and returns `false` (opening nothing) for
 *      an unresolvable number.
 *   3. buildReceiptText(): a receipt-text builder test proving every product
 *      name, the total and the short sale number are present in the
 *      DECODED wa.me link text -- the exact evidence contract.md's exit
 *      criteria names.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md) -- this runs standalone on Node built-ins, same
 * shape as retail_payment_chart_negative_bucket_test.js.
 *
 * Run: node products/retail/tests/retail_share_whatsapp_test.js
 */
'use strict';

const assert = require('assert');
const path = require('path');

const AuraShare = require(path.join(__dirname, '..', 'frontend', 'share.js'));

const cases = [];
function test(name, fn) { cases.push([name, fn]); }

// ── normalizeJoPhone ────────────────────────────────────────────────────────

test('07XXXXXXXX (local dialling form) normalises to +9627XXXXXXXX', () => {
  assert.strictEqual(AuraShare.normalizeJoPhone('0791234567'), '+962791234567');
  assert.strictEqual(AuraShare.normalizeJoPhone('0781234567'), '+962781234567');
  assert.strictEqual(AuraShare.normalizeJoPhone('0771234567'), '+962771234567');
});

test('7XXXXXXXX (no leading 0) normalises to +9627XXXXXXXX', () => {
  assert.strictEqual(AuraShare.normalizeJoPhone('791234567'), '+962791234567');
});

test('009627XXXXXXXX (00 international prefix) normalises to +9627XXXXXXXX', () => {
  assert.strictEqual(AuraShare.normalizeJoPhone('00962791234567'), '+962791234567');
});

test('+9627XXXXXXXX (already E.164) round-trips to itself', () => {
  assert.strictEqual(AuraShare.normalizeJoPhone('+962791234567'), '+962791234567');
});

test('bare 9627XXXXXXXX (no + and no 00) normalises to +9627XXXXXXXX', () => {
  assert.strictEqual(AuraShare.normalizeJoPhone('962791234567'), '+962791234567');
});

test('Arabic-Indic digits (٠-٩) normalise the same as Western digits', () => {
  // ٠٧٩١٢٣٤٥٦٧ is 0791234567 written in Arabic-Indic numerals.
  assert.strictEqual(AuraShare.normalizeJoPhone('٠٧٩١٢٣٤٥٦٧'), '+962791234567');
});

test('spaces and dashes are stripped before normalising', () => {
  assert.strictEqual(AuraShare.normalizeJoPhone('07 9123 4567'), '+962791234567');
  assert.strictEqual(AuraShare.normalizeJoPhone('079-123-4567'), '+962791234567');
  assert.strictEqual(AuraShare.normalizeJoPhone('+962 79 123 4567'), '+962791234567');
});

test('invalid numbers all resolve to false, never a best-effort guess', () => {
  const invalid = [
    '',                    // empty
    null,                  // null
    undefined,             // undefined
    '12345',               // too short, no recognisable prefix
    '0691234567',          // starts 06, not a Jordan mobile prefix (07 only)
    '069123456',            // 9 digits but does not start with 7
    '07912345',             // 07 + only 6 digits (9 total, not 10)
    '0079123456789',        // 00-prefixed but wrong length after stripping
    'not-a-number-at-all',  // no digits at all
  ];
  invalid.forEach((v) => {
    assert.strictEqual(AuraShare.normalizeJoPhone(v), false, `expected false for ${JSON.stringify(v)}`);
  });
});

// ── whatsapp() / URL + text encoding ───────────────────────────────────────

test('whatsapp() opens a correctly formed, correctly encoded wa.me link', () => {
  let openedUrl = null;
  const originalWindow = global.window;
  global.window = {
    open: (url) => { openedUrl = url; },
  };
  try {
    const text = 'مرحباً — إجمالي: JD 12.500';
    const ok = AuraShare.whatsapp('0791234567', text);
    assert.strictEqual(ok, true);
    assert.ok(openedUrl, 'expected window.open to have been called');
    assert.ok(openedUrl.indexOf('https://wa.me/962791234567?text=') === 0,
      `expected the wa.me link to start with the E.164 number, got: ${openedUrl}`);
    const encodedText = openedUrl.slice('https://wa.me/962791234567?text='.length);
    assert.strictEqual(decodeURIComponent(encodedText), text,
      'the DECODED link text must exactly equal the Arabic message that was sent in');
  } finally {
    global.window = originalWindow;
  }
});

test('whatsapp() opens nothing and returns false for an unresolvable number', () => {
  let openCalled = false;
  const originalWindow = global.window;
  global.window = { open: () => { openCalled = true; } };
  try {
    const ok = AuraShare.whatsapp('not-a-number', 'hello');
    assert.strictEqual(ok, false);
    assert.strictEqual(openCalled, false, 'a broken/unresolvable number must never open a wa.me link at all');
  } finally {
    global.window = originalWindow;
  }
});

// ── buildReceiptText() ──────────────────────────────────────────────────────

test('buildReceiptText: every product name, the total and the short sale number survive into the DECODED wa.me link text', () => {
  let openedUrl = null;
  const originalWindow = global.window;
  global.window = { open: (url) => { openedUrl = url; } };
  try {
    const text = AuraShare.buildReceiptText({
      shopName: 'Aura Test Shop',
      saleNumber: 'SALE-000124',
      items: [
        { name: 'Arabic Coffee 250g', qty: 2, total: 5.5 },
        { name: 'زيت زيتون 1 لتر', qty: 1, total: 7.0 },
      ],
      total: 12.5,
      saved: 1.25,
      currency: 'JD',
    });
    const ok = AuraShare.whatsapp('0791234567', text);
    assert.strictEqual(ok, true);
    const encodedText = openedUrl.slice(openedUrl.indexOf('?text=') + '?text='.length);
    const decoded = decodeURIComponent(encodedText);

    // Anti-vacuity: fail loudly if the link somehow carried no text at all,
    // rather than every .includes() below passing vacuously on an empty
    // string comparison quirk.
    assert.ok(decoded.length > 20, `decoded link text looked suspiciously short: ${JSON.stringify(decoded)}`);

    ['Arabic Coffee 250g', 'زيت زيتون 1 لتر', 'SALE-000124'].forEach((needle) => {
      assert.ok(decoded.indexOf(needle) !== -1,
        `expected "${needle}" in the decoded WhatsApp text, got:\n${decoded}`);
    });
    // The total, tabular JD to 3 decimals (design_system's money rule).
    assert.ok(decoded.indexOf('12.500') !== -1,
      `expected the total (12.500) in the decoded WhatsApp text, got:\n${decoded}`);
  } finally {
    global.window = originalWindow;
  }
});

test('buildReceiptText: "you saved" only appears when a discount/promotion actually applied', () => {
  const withSaving = AuraShare.buildReceiptText({ items: [{ name: 'X', qty: 1, total: 1 }], total: 1, saved: 2 });
  const withoutSaving = AuraShare.buildReceiptText({ items: [{ name: 'X', qty: 1, total: 1 }], total: 1, saved: 0 });
  assert.ok(withSaving.indexOf('وفّرت') !== -1, 'expected the "you saved" line when saved > 0');
  assert.strictEqual(withoutSaving.indexOf('وفّرت'), -1, 'must not claim a saving of zero');
});

// ── buildStatementText() / buildReminderText() ─────────────────────────────

test('buildStatementText: balance, oldest unpaid and up to 5 recent lines all appear', () => {
  const text = AuraShare.buildStatementText({
    shopName: 'Aura Test Shop',
    customerName: 'Test Customer',
    balance: 45.5,
    oldestUnpaid: { amount: 20, date: '2026-08-01' },
    recentLines: [
      { label: 'Credit sale', amount: 10, date: '2026-09-01' },
      { label: 'Credit sale', amount: 15, date: '2026-09-05' },
      { label: 'Payment', amount: -5, date: '2026-09-10' },
      { label: 'Credit sale', amount: 20, date: '2026-09-15' },
      { label: 'Credit sale', amount: 5, date: '2026-09-20' },
      { label: 'Credit sale', amount: 5, date: '2026-09-25' }, // 6th line -- must NOT appear
    ],
  });
  assert.ok(text.indexOf('45.500') !== -1, 'expected the balance in the statement text');
  assert.ok(text.indexOf('20.000') !== -1, 'expected the oldest-unpaid amount in the statement text');
  assert.strictEqual((text.match(/Credit sale|Payment/g) || []).length, 5,
    'expected at most the first 5 recent lines, never a 6th');
});

test('buildReminderText: polite and firm templates both carry the amount and the days overdue', () => {
  const polite = AuraShare.buildReminderText({ customerName: 'Sara', amount: 30, daysOverdue: 10 }, 'polite');
  const firm = AuraShare.buildReminderText({ customerName: 'Sara', amount: 30, daysOverdue: 45 }, 'firm');
  assert.ok(polite.indexOf('30.000') !== -1 && polite.indexOf('10') !== -1,
    `polite template missing amount/days: ${polite}`);
  assert.ok(firm.indexOf('30.000') !== -1 && firm.indexOf('45') !== -1,
    `firm template missing amount/days: ${firm}`);
  assert.notStrictEqual(polite, firm, 'polite and firm must be genuinely different wording, not the same string with a label');
});

// ── FIX ROUND 3 (LOW-2): Arabic plural forms, one helper ───────────────────
// Every boundary of the four forms is pinned, not a sample: 0, 1, 2, 3, 10,
// 11, 12, 100. A helper that special-cased only 1 and 2 (or only "N يوماً")
// would still pass a single-value test.

test('daysPhrase (ar): singular, dual, 3-10 plural, 11+ accusative -- every boundary', () => {
  const want = {
    0: '0 يوماً', 1: 'يوم واحد', 2: 'يومين', 3: '3 أيام', 4: '4 أيام', 10: '10 أيام',
    11: '11 يوماً', 12: '12 يوماً', 30: '30 يوماً', 100: '100 يوماً',
  };
  for (const [n, phrase] of Object.entries(want)) {
    assert.strictEqual(AuraShare.daysPhrase(Number(n), 'ar'), phrase, `ar ${n}`);
  }
});

test('daysPhrase (en): "1 day" vs "N days", and bad input is 0 days, never NaN/blank', () => {
  assert.strictEqual(AuraShare.daysPhrase(1, 'en'), '1 day');
  assert.strictEqual(AuraShare.daysPhrase(2, 'en'), '2 days');
  assert.strictEqual(AuraShare.daysPhrase(0, 'en'), '0 days');
  for (const bad of [undefined, null, NaN, 'abc', -3]) {
    assert.strictEqual(AuraShare.daysPhrase(bad, 'en'), '0 days', `en bad ${String(bad)}`);
    assert.strictEqual(AuraShare.daysPhrase(bad, 'ar'), '0 يوماً', `ar bad ${String(bad)}`);
  }
});

test('buildReminderText: the days wording in BOTH tones and BOTH languages comes from the plural helper', () => {
  const cases2 = [
    ['ar', 1, 'يوم واحد'], ['ar', 2, 'يومين'], ['ar', 7, '7 أيام'], ['ar', 30, '30 يوماً'],
  ];
  for (const [lang, days, phrase] of cases2) {
    for (const tone of ['polite', 'firm']) {
      const text = AuraShare.buildReminderText({ lang, amount: 10, daysOverdue: days }, tone);
      assert.ok(text.indexOf('منذ ' + phrase + '.') !== -1,
        `${lang}/${tone}/${days}: expected "منذ ${phrase}." in: ${text}`);
    }
  }
  for (const tone of ['polite', 'firm']) {
    assert.ok(AuraShare.buildReminderText({ lang: 'en', amount: 10, daysOverdue: 1 }, tone).indexOf('from 1 day ago') !== -1, `en/${tone}/1`);
    assert.ok(AuraShare.buildReminderText({ lang: 'en', amount: 10, daysOverdue: 5 }, tone).indexOf('from 5 days ago') !== -1, `en/${tone}/5`);
  }
});

test('buildReminderText / buildStatementText: an injected formatter wins and the language picks the labels', () => {
  const fm = (n) => '$' + Number(n).toFixed(2);
  const en = AuraShare.buildReminderText({ lang: 'en', amount: 12.5, daysOverdue: 3, formatMoney: fm }, 'polite');
  assert.ok(en.indexOf('$12.50') !== -1 && en.indexOf('12.500') === -1, `en reminder must use the injected formatter only: ${en}`);
  const st = AuraShare.buildStatementText({
    lang: 'en', balance: 5, formatMoney: fm, oldestUnpaid: { amount: 5, date: '1 Sep 2026' },
    recentLines: [{ label: 'Payment', amount: -5, date: '2 Sep 2026' }],
  });
  assert.ok(st.indexOf('Current balance: $5.00') !== -1 && st.indexOf('Oldest unpaid: $5.00 (1 Sep 2026)') !== -1, `en statement labels: ${st}`);
  const ar = AuraShare.buildStatementText({ lang: 'ar', balance: 5, formatMoney: fm });
  assert.ok(ar.indexOf('الرصيد الحالي') !== -1 && ar.indexOf('Current balance') === -1, `ar statement labels: ${ar}`);
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    fn();
    console.log(`PASS: ${name}`);
  } catch (err) {
    failed += 1;
    console.error(`FAIL: ${name}\n      ${err && err.stack || err}`);
  }
}
console.log(`\n=== ${cases.length - failed} passed, ${failed} failed, ${cases.length} total ===`);
process.exit(failed ? 1 : 0);
