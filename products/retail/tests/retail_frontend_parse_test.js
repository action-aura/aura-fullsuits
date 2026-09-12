/**
 * Every shipped frontend script must PARSE. Nothing else is checked here.
 *
 * Why this exists, dated 2026-09-12: a one-character edit -- a backtick inside a
 * CSS comment in `_injectStyles` -- terminated the template literal holding the
 * stylesheet and made subsystem-retail.js a syntax error. The browser then
 * silently refused to define SubsystemApp at all, so the till booted to a shell
 * with no POS, no dashboard, no products screen. No banner, no dialog a
 * shopkeeper would see: just an app that does nothing.
 *
 * That file is 650KB of JS with a large stylesheet embedded as a template
 * literal, so a stray backtick in a comment is a live, repeatable hazard rather
 * than a freak accident -- and it is close to invisible in review, because the
 * comment reads perfectly well as English.
 *
 * The heavier render tests do exercise these files, but they are slow and their
 * failure output points at whatever they were rendering rather than at the
 * syntax. This runs in milliseconds and names the file and the line directly.
 *
 * Parsed as CLASSIC SCRIPTS, which is what they are -- they are loaded by plain
 * <script src> tags; there is no bundler and no module graph in this product
 * (see CLAUDE.md). If a file ever legitimately becomes an ES module, this test
 * should learn about it deliberately, not be loosened to make it pass.
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');

// Anti-vacuity floor. A glob that silently starts matching nothing still
// "passes" every assertion inside the loop, which is the quiet way a guard like
// this stops guarding -- a folder rename is enough. Well below the 12 files
// present today, high enough that an empty or near-empty match is a failure.
const MIN_EXPECTED_FILES = 8;

function testEveryFrontendScriptParses() {
  const files = fs.readdirSync(FRONTEND_DIR)
    .filter((f) => f.endsWith('.js'))
    .sort();

  assert.ok(
    files.length >= MIN_EXPECTED_FILES,
    `Only ${files.length} frontend .js file(s) found in ${FRONTEND_DIR} (expected at ` +
    `least ${MIN_EXPECTED_FILES}). Either the scripts moved, or this test is no ` +
    'longer looking where they live and has stopped checking anything.'
  );

  for (const file of files) {
    const full = path.join(FRONTEND_DIR, file);
    const source = fs.readFileSync(full, 'utf8');
    try {
      // Compile only. new vm.Script() parses without executing, so nothing in
      // these files touches a DOM that does not exist here.
      new vm.Script(source, { filename: full });
    } catch (err) {
      assert.fail(
        `${file} is not valid JavaScript and would not load in a browser at all: ` +
        `${err.message}\n` +
        'A parse error in a shipped script means the objects it defines never ' +
        'exist, so every screen that depends on them renders as nothing. If the ' +
        'file embeds CSS in a template literal, check for a stray backtick.'
      );
    }
  }
  console.log(`PASS: all ${files.length} frontend scripts parse as classic scripts`);
}

// -- Mutation proof ---------------------------------------------------------
// Proves the check above can actually fail, on the exact defect that motivated
// it: a backtick inside a CSS comment in an embedded stylesheet. Without this, a
// test that only ever sees valid files is indistinguishable from one that parses
// nothing at all.
function testParseCheckCatchesAStrayBacktickInEmbeddedCss() {
  const mutant = [
    'const App = {',
    '  _injectStyles() {',
    '    const css = `',
    '      /* a comment mentioning the `up` chip -- this backtick ends the string */',
    '      .x { color: red; }',
    '    `;',
    '    return css;',
    '  }',
    '};',
  ].join('\n');

  let threw = false;
  try {
    new vm.Script(mutant, { filename: 'mutant.js' });
  } catch (err) {
    threw = true;
  }
  assert.ok(
    threw,
    'A backtick inside a CSS comment in a template literal did NOT produce a ' +
    'parse error -- this whole test is then checking nothing, because that is ' +
    'the exact defect it was written for.'
  );

  // And the allow-half: the same file WITHOUT the stray backtick must parse, or
  // the check would be rejecting everything and passing for the wrong reason.
  const healthy = mutant.replace('the `up` chip', 'the up chip');
  new vm.Script(healthy, { filename: 'healthy.js' });

  console.log('PASS: the parse check survived a mutant with a backtick in embedded CSS');
}

testEveryFrontendScriptParses();
testParseCheckCatchesAStrayBacktickInEmbeddedCss();
console.log('PASS: retail_frontend_parse_test.js - 2 case(s)');
