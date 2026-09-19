/**
 * NO DATE OR NUMBER MAY BE FORMATTED AGAINST THE OPERATING SYSTEM'S LOCALE.
 *
 * WHY THIS TEST EXISTS
 * `toLocaleString()` / `toLocaleTimeString()` / `toLocaleDateString()` with no
 * locale argument -- or with the empty list `[]`, which is the same thing and
 * merely looks deliberate -- do NOT mean "neutral". They ask Intl for the
 * RUNTIME's default locale, which is the OS or browser locale and has nothing
 * to do with the language the shopkeeper picked inside the app.
 *
 * Measured for one instant, 15:45 UTC on 2026-09-19:
 *
 *     []        ->  "03:45 PM"        (whatever this machine happens to be)
 *     'en-US'   ->  "03:45 PM"
 *     'en-GB'   ->  "15:45"
 *     'ar'      ->  "٠٣:٤٥ م"
 *
 * Two consequences, both of which had shipped:
 *
 *   1. TWO TILLS IN ONE SHOP DISAGREED. The same sync timestamp, the same
 *      audit row, the same relay "last seen" rendered differently depending on
 *      how each machine's Windows had been installed. For values whose entire
 *      purpose is letting two devices agree on when something happened, that
 *      is the one thing they must not do.
 *   2. AN ARABIC-LOCALE DEVICE PRINTED EASTERN ARABIC-INDIC DIGITS (٠١٢٣).
 *      subsystem-retail.js's own axisMoney() comment already rules this out
 *      for money -- "Jordan's shops price in Western digits regardless of UI
 *      language" -- and the same is true of a clock on a till. One of the
 *      offending call sites was the PRINTED RECEIPT's date, i.e. the copy the
 *      customer walks out with.
 *
 * WHY A BAN AND NOT A PREFERENCE
 * The fix is never "follow the active language" either. `_auditTimestamp`'s
 * long comment in subsystem-retail.js makes the argument: an audit row is
 * EVIDENCE, compared across devices and quoted in support conversations, so
 * its format must not change shape with the reader. Every date in this
 * product is therefore one of two things -- a fixed `YYYY-MM-DD HH:MM:SS`, or
 * an explicitly-pinned locale -- and both are visible in the source. A bare
 * call is the only shape that is invisible, which is why it is the one banned.
 *
 * WHAT THIS DOES NOT CLAIM
 * It does not check that the pinned locale is the RIGHT one; a site that pins
 * 'ar' deliberately would pass. It closes the specific hole where no choice
 * was made at all and the machine silently made one.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_locale_formatting_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const FRONTEND = path.join(__dirname, '..', 'frontend');

/* A bare call: `toLocaleString()`, `toLocaleTimeString( )`, or the empty
   locales list `toLocaleDateString([], {...})`. A call that passes anything
   else -- a string literal, a variable, a resolved locale -- is a choice
   somebody made on purpose and is out of scope. */
const BARE_LOCALE = /\.toLocale(?:Date|Time)?String\(\s*(?:\)|\[\s*\])/g;

/* Anti-vacuity: the scan must still be looking at real code. If the frontend
   were renamed or the read failed, an empty sweep would report clean. */
const MIN_BYTES_SCANNED = 500000;

function jsFiles() {
  return fs.readdirSync(FRONTEND)
    .filter((f) => f.endsWith('.js'))
    .sort();
}

function stripped(src) {
  // Block comments only. The comments in this codebase QUOTE the banned
  // pattern while explaining why it is banned -- including the two long ones
  // this test was written alongside -- so scanning raw text would flag the
  // documentation as the defect. Line comments are handled below per line, so
  // that a real call followed by a trailing comment is still seen.
  return src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
}

function testNoBareLocaleFormattingInTheRetailFrontend() {
  const offenders = [];
  let bytes = 0;
  let files = 0;

  for (const file of jsFiles()) {
    const raw = fs.readFileSync(path.join(FRONTEND, file), 'utf8');
    bytes += raw.length;
    files++;
    const src = stripped(raw);

    src.split('\n').forEach((line, i) => {
      const code = line.replace(/\/\/.*$/, '');
      BARE_LOCALE.lastIndex = 0;
      if (BARE_LOCALE.test(code)) {
        offenders.push(`${file}:${i + 1}  ${line.trim().slice(0, 110)}`);
      }
    });
  }

  assert.ok(
    bytes >= MIN_BYTES_SCANNED && files >= 5,
    `Only ${files} file(s) / ${bytes} bytes of frontend JS were scanned ` +
    `(floor ${MIN_BYTES_SCANNED} bytes). The sweep is not reading the frontend, ` +
    'so a green result would be measuring nothing.'
  );

  assert.deepStrictEqual(
    offenders, [],
    `${offenders.length} bare locale-formatting call(s) found:\n  ` +
    offenders.join('\n  ') +
    '\n\nA bare toLocale*String() formats against the OPERATING SYSTEM locale, ' +
    'not the language chosen in the app -- so two tills in the same shop render ' +
    'the same instant differently, and an Arabic-locale device emits Eastern ' +
    'Arabic-Indic digits (٠١٢٣) that the shop\'s own price labels never use. ' +
    'Use the fixed YYYY-MM-DD HH:MM:SS helper (_fixedDateTime / _auditTimestamp ' +
    'in subsystem-retail.js) for anything compared across devices, or pin an ' +
    'explicit locale string for anything else.'
  );

  console.log(
    `PASS: no bare locale formatting in ${files} frontend file(s) (${bytes} bytes scanned)`
  );
}

function main() {
  try {
    testNoBareLocaleFormattingInTheRetailFrontend();
  } catch (err) {
    console.error('FAIL: retail_locale_formatting_test.js');
    console.error(String((err && err.message) || err));
    process.exitCode = 1;
    return;
  }
  console.log('PASS: retail_locale_formatting_test.js — 1 check');
}

main();
