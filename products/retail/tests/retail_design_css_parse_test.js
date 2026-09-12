/**
 * THE STYLESHEET A BROWSER SEES, NOT THE ONE THE FILE LOOKS LIKE.
 *
 * WHY THIS TEST EXISTS
 * On 2026-09-08 twenty-two of main.css's design tokens were undefined in the
 * shipped product -- --text-main, --bg-panel, --header-h, --sidebar-w,
 * --trans-fast, --surface-accent-soft and sixteen more -- and every guard in
 * this suite was green.
 *
 * The cause was two characters of prose. A CSS comment ends at the FIRST star
 * followed by a slash, wherever that lands. Two comments described token
 * families using a wildcard immediately before a slash, which spells exactly
 * that sequence, so each comment ended mid-sentence. The leaked prose was then
 * handed to the CSS parser, which discarded it together with whatever followed
 * -- in one case a single token, in the other the entire legacy-alias :root
 * block. Measured in a real browser before and after the repair: 28 of 126
 * tokens empty, then 6, and the 6 are declared only inside dead
 * body[data-domain=...] blocks, which is a separate matter.
 *
 * The reason no existing guard noticed is the point of this file.
 * retail_design_contrast_test.js and retail_design_theme_safety_test.js both
 * find tokens by regex-scanning the stylesheet TEXT for `--name: value`. Text
 * scanning cannot tell a live declaration from one the parser threw away, so
 * both reported those tokens present, computed 3,240 contrast pairings against
 * values the browser never had, and printed PASS. That is ENGINEERING.md
 * section 1's first failure shape: a success condition a broken implementation
 * also satisfies.
 *
 * WHAT THIS TEST DOES DIFFERENTLY
 * It strips comments the way a browser does -- first close wins -- and then
 * walks the result with brace tracking, accepting a `--name:` only where a
 * declaration may legally begin: nothing but whitespace between it and the
 * `{` or `;` before it. Any token the text promises that the walk does not
 * accept is a token the browser will not have.
 *
 * PER BLOCK, and that is not a refinement. The first version of this file
 * compared whole-file token sets and was very nearly theatre: a swallowed
 * token is usually still declared somewhere else -- --surface-accent-soft was
 * lost from :root while four theme blocks below still declared it -- so the
 * name is found, the diff is empty, and the guard passes in exactly the case
 * it exists to catch. Measured with the original typo restored: 125 tokens
 * against 125, empty diff, PASS. Judging each block on its own fixes that.
 *
 * MUTATION-PROVED. Restoring either original typo makes this file fail and
 * name the block and token lost; the two existing design guards stay green on
 * the same file. Both outputs are quoted in the commit.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_design_css_parse_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const SHEETS = ['css/main.css', 'css/rtl.css']
  .map((rel) => path.join(FRONTEND, rel))
  .filter((p) => fs.existsSync(p));

// Never written as a literal: this file is JavaScript, not CSS, but the whole
// subject is a two-character sequence that is easy to paste somewhere fatal.
const OPEN = '/*';
const CLOSE = '*' + '/';

/* Remove comments exactly as a CSS parser does: each runs from the opener to
   the FIRST closer after it, and an unterminated one runs to end of file.
   Replaced with spaces rather than deleted so every reported line number
   still matches the file on disk. */
function stripComments(text) {
  let out = '';
  let i = 0;
  for (;;) {
    const start = text.indexOf(OPEN, i);
    if (start < 0) { out += text.slice(i); return out; }
    out += text.slice(i, start);
    const end = text.indexOf(CLOSE, start + 2);
    const body = end < 0 ? text.slice(start) : text.slice(start, end + 2);
    out += body.replace(/[^\n]/g, ' ');
    if (end < 0) return out;
    i = end + 2;
  }
}

function lineOf(text, index) {
  return text.slice(0, index).split('\n').length;
}

/* A custom property name can only begin where the preceding character is not
   part of an identifier. Without that guard this also matches the tail of a
   class selector -- `.money--negative::before` reads as a token `--negative`
   -- which it did on this file's first run. A pattern that matches text
   rather than structure is the very mistake this test exists to correct. */
const TOKEN_RE = /(?<![\w-])(--[a-z0-9-]+)\s*:/gi;

/* One walk, two answers.
 *
 * `promised` is what a text scan would claim each block declares: every
 * `--name:` in that block's OWN text, boundary or not, nested blocks excluded.
 * `real` is what the browser will accept: the same, restricted to positions
 * where a declaration may legally start.
 *
 * Both are keyed "selector\ntoken" so two blocks declaring one token stay
 * distinct -- that separation is what makes a token lost from :root visible
 * while the dark block still declares it. */
function collect(stripped) {
  const promised = new Map();
  const real = new Map();
  const stack = [];            // { selector, own } per open block
  let boundary = 0;            // index just after the last `{`, `}` or `;`

  const flush = (frame) => {
    let m;
    TOKEN_RE.lastIndex = 0;
    while ((m = TOKEN_RE.exec(frame.own.text))) {
      const key = frame.selector + '\n' + m[1];
      if (!promised.has(key)) {
        promised.set(key, lineOf(stripped, frame.own.at[m.index] ?? 0));
      }
    }
  };

  for (let i = 0; i < stripped.length; i++) {
    const ch = stripped[i];
    if (ch === '{') {
      const selector = stripped.slice(boundary, i).trim().replace(/\s+/g, ' ');
      stack.push({ selector, own: { text: '', at: [] } });
      boundary = i + 1;
      continue;
    }
    if (ch === '}') {
      const frame = stack.pop();
      if (frame) flush(frame);
      boundary = i + 1;
      continue;
    }
    const top = stack[stack.length - 1];
    if (top) {                              // this char belongs to this block
      top.own.at[top.own.text.length] = i;  // map own-text offset -> file index
      top.own.text += ch;
    }
    if (ch === ';') { boundary = i + 1; continue; }
    if (ch !== '-' || !top) continue;
    if (stripped[i + 1] !== '-') continue;
    if (stripped.slice(boundary, i).trim() !== '') continue;
    const m = /^(--[a-z0-9-]+)\s*:/i.exec(stripped.slice(i));
    if (!m) continue;
    const key = top.selector + '\n' + m[1];
    if (!real.has(key)) real.set(key, lineOf(stripped, i));
  }
  return { promised, real };
}

/* ── check 1 ── every comment closes exactly once ────────────────────────── */
function testNoCommentClosesEarly() {
  for (const file of SHEETS) {
    const text = fs.readFileSync(file, 'utf8');
    const opens = text.split(OPEN).length - 1;
    const closes = text.split(CLOSE).length - 1;
    const name = path.relative(FRONTEND, file);
    assert.strictEqual(
      closes, opens,
      `${name}: ${opens} comment opener(s) but ${closes} closer(s). A CSS ` +
      'comment ends at the first closing sequence, so a surplus closer means ' +
      'a comment ends mid-sentence and the prose after it is handed to the ' +
      'CSS parser, which discards it along with the next declaration. This ' +
      'is almost always a token wildcard written as a star immediately ' +
      'followed by a slash; write "the surface and text tokens" instead.');
  }
  console.log(`PASS testNoCommentClosesEarly (${SHEETS.length} stylesheet(s))`);
}

/* ── check 2 ── nothing a block promises is missing from the real sheet ──── */
function testEveryApparentTokenSurvivesParsing() {
  for (const file of SHEETS) {
    const stripped = stripComments(fs.readFileSync(file, 'utf8'));
    const { promised, real } = collect(stripped);
    const lost = [...promised.keys()].filter((k) => !real.has(k));
    const name = path.relative(FRONTEND, file);
    assert.deepStrictEqual(
      lost, [],
      `${name}: ${lost.length} token(s) appear inside a block but not at a ` +
      'legal declaration boundary, so the browser does not define them there: ' +
      lost.map((k) => {
        const [sel, tok] = k.split('\n');
        return `${tok} in \`${sel}\` (line ${promised.get(k)})`;
      }).join(', ') +
      '. Every text-scanning guard in this suite will still report these ' +
      'present and will compute contrast against values that do not exist.');
  }
  console.log('PASS testEveryApparentTokenSurvivesParsing');
}

/* ── check 3 ── the walk above is not vacuous ────────────────────────────── */
function testTheWalkActuallyDistinguishesTheTwo() {
  // A guard that cannot fail is worse than none, so prove on fixtures that the
  // two collectors agree on healthy CSS and disagree on exactly this bug --
  // including the case that fooled the first version of this file, where the
  // swallowed token is still declared correctly in a second block.
  const healthy = ':root {\n  --a: 1px;\n  --b: 2px;\n}\n';
  const ok = collect(stripComments(healthy));
  assert.deepStrictEqual(
    [...ok.promised.keys()].filter((k) => !ok.real.has(k)), [],
    'ordinary CSS must produce no disagreement at all');

  const broken =
    ':root {\n  ' + OPEN + ' names the --x' + CLOSE + ' family ' + CLOSE + '\n' +
    '  --swallowed: 3px;\n}\n' +
    'html[data-theme="dark"] {\n  --swallowed: 4px;\n}\n';
  const bad = collect(stripComments(broken));
  assert.ok(
    bad.promised.has(':root\n--swallowed'),
    'the text scan must still see the swallowed token -- that is the bug');
  assert.ok(
    !bad.real.has(':root\n--swallowed'),
    'the walk must NOT accept it in :root; if it does, check 2 can never ' +
    'fail and this whole file is theatre');
  assert.ok(
    bad.real.has('html[data-theme="dark"]\n--swallowed'),
    'the SAME token declared correctly in a second block must still be ' +
    'accepted there -- this is the case that defeated the whole-file version ' +
    'of check 2, and the reason both maps are keyed by block');
  console.log('PASS testTheWalkActuallyDistinguishesTheTwo');
}

const tests = [
  testNoCommentClosesEarly,
  testEveryApparentTokenSurvivesParsing,
  testTheWalkActuallyDistinguishesTheTwo,
];

let failed = 0;
for (const t of tests) {
  try {
    t();
  } catch (err) {
    failed++;
    console.error(`FAIL ${t.name} -- ${err.message}`);
  }
}
if (failed) {
  console.error(`\n${failed} of ${tests.length} checks failed`);
  process.exit(1);
}
console.log(`\nPASS retail_design_css_parse_test.js -- ${tests.length} checks`);
