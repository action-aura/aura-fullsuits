/**
 * A <option> label is the one piece of UI text the runtime cannot rescue.
 *
 * i18n.js translates in two ways: explicit `t('...')` at build time, and a DOM
 * sweep that walks text nodes afterwards and swaps any whose full trimmed text
 * matches a catalogue key. The sweep has a _SKIP list, and OPTION is on it --
 * along with SCRIPT, STYLE, TEXTAREA, INPUT, SELECT, CANVAS, CODE and PRE.
 *
 * So a <div> or a <label> with bare English text still comes out Arabic; an
 * <option> with bare English text is stuck in English forever. That makes this
 * class of bug invisible from the locale catalogue -- the key is often present
 * and correctly translated, it is simply never looked up -- and it can only be
 * found by reading an Arabic screen.
 *
 * Which is how it WAS found, on 2026-09-12: the Reports branch filter read
 * "All branches" beside a period filter reading "آخر 14 يومًا". Behind it were
 * 23 more across five dropdowns -- the return reasons, refund methods and
 * stock-adjustment reasons a shopkeeper picks from every day.
 *
 * NOTE FOR WHOEVER TRIPS THIS: translate the LABEL, never the value. Several of
 * these options carry their English text as the submitted value, and #ret-reason
 * had no value attribute at all, so its visible text was what reached the API.
 * Give the option an explicit English `value` and wrap only the text in t().
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');

// Anti-vacuity floor. A regex that silently stops matching passes every
// assertion below, which is the quiet way a guard stops guarding. There are 111
// option elements today across the frontend; well under that, high enough that
// a broken scan fails loudly.
const MIN_EXPECTED_OPTIONS = 60;

const OPTION_RE = /<option\b[^>]*>([\s\S]*?)<\/option>/g;

/** Does this option's text come from somewhere translatable? */
function isTranslatable(text) {
  const content = text.trim();
  if (!content) return true;                 // empty placeholder option
  if (content.includes('${')) return true;   // t(), a variable, any interpolation
  // Numbers, currency codes, symbols and bare punctuation are not prose and
  // have nothing to translate -- "80" or "—" is the same in every language.
  if (!/[A-Za-z]{2,}/.test(content)) return true;
  return false;
}

function scanFrontend() {
  const offenders = [];
  let total = 0;
  const files = fs.readdirSync(FRONTEND_DIR).filter((f) => f.endsWith('.js')).sort();
  for (const file of files) {
    const src = fs.readFileSync(path.join(FRONTEND_DIR, file), 'utf8');
    let m;
    OPTION_RE.lastIndex = 0;
    while ((m = OPTION_RE.exec(src)) !== null) {
      total += 1;
      if (isTranslatable(m[1])) continue;
      const line = src.slice(0, m.index).split('\n').length;
      offenders.push(`${file}:${line}  ${m[1].trim().slice(0, 60)}`);
    }
  }
  return { offenders, total };
}

function testEveryOptionLabelIsTranslatable() {
  const { offenders, total } = scanFrontend();

  assert.ok(
    total >= MIN_EXPECTED_OPTIONS,
    `Only ${total} <option> element(s) found across the frontend (expected at least ` +
    `${MIN_EXPECTED_OPTIONS}). The scan is broken or the markup moved, so this check ` +
    'is no longer looking at anything.'
  );

  assert.deepStrictEqual(
    offenders, [],
    `${offenders.length} <option> label(s) are hardcoded English and can never be ` +
    'translated at runtime -- i18n.js\'s DOM sweep skips OPTION elements:\n  ' +
    offenders.join('\n  ') +
    '\n\nWrap the LABEL in t(). If the option has no value attribute, add one ' +
    'carrying the original English string FIRST -- for several of these the ' +
    'visible text is what gets submitted to the API, and translating it would ' +
    'start writing Arabic into the database.'
  );

  console.log(`PASS: all ${total} <option> labels are translatable`);
}

// -- Mutation proof ---------------------------------------------------------
// A scanner that never sees an offender is indistinguishable from one whose
// regex stopped matching. Plant the exact defect this file was written for and
// require the classifier to catch it, then require the fixed form to pass.
function testTheScanCatchesAHardcodedLabel() {
  assert.ok(
    !isTranslatable('All branches'),
    'A bare English option label was classified as translatable -- this check ' +
    'cannot see the defect it exists for.'
  );
  assert.ok(
    isTranslatable("${t('All branches')}"),
    'An option label routed through t() was classified as a violation, so this ' +
    'check would fail on correct code.'
  );
  // And the carve-outs, both directions.
  assert.ok(isTranslatable(''), 'an empty placeholder option should be allowed');
  assert.ok(isTranslatable('80'), 'a bare number has nothing to translate');
  console.log('PASS: the classifier catches a hardcoded label and passes a t() one');
}

const CHECKS = [
  ['every option label is translatable', testEveryOptionLabelIsTranslatable],
  ['the scan catches a hardcoded label', testTheScanCatchesAHardcodedLabel],
];

function main() {
  const failures = [];
  for (const [name, fn] of CHECKS) {
    try {
      fn();
    } catch (err) {
      failures.push(name);
      console.error(`FAIL: ${name}`);
      console.error('      ' + String((err && err.message) || err).replace(/\n/g, '\n      '));
    }
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_option_i18n_test.js - ${failures.length} of ${CHECKS.length} failed`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_option_i18n_test.js - ${CHECKS.length} checks`);
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_option_i18n_test.js (runner)');
  console.error((err && err.message) || err);
  process.exitCode = 1;
}
