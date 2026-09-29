/**
 * OPERATIONAL CALM — RTL correct by construction, not by a chasing stylesheet.
 *
 * WHY THIS TEST EXISTS
 * This product ships Arabic. The existing approach is css/rtl.css: a second
 * stylesheet, loaded after main.css, that re-states rules under `body.rtl`
 * to undo physical directions main.css baked in. That approach works only
 * for the rules somebody remembered to chase — and its own comments record
 * the cost, including a bug where a chat bubble's corner radius stayed on
 * its LTR corner while the avatar mirrored, so the tail pointed away from
 * its own avatar. That was found by inspecting computed styles in a real
 * RTL session, which is not a scalable way to find this class of bug.
 *
 * Logical properties remove the class of bug instead of chasing instances.
 * `margin-inline-start` is "the start edge" in whichever direction the
 * document runs, so it needs no mirror rule, no second stylesheet, and no
 * Playwright session to verify. `margin-left` is "the left edge" always, and
 * needs all three.
 *
 * WHAT IT ASSERTS, AND WHY THAT SHAPE
 * This is a RATCHET on NEW code, not a demand that 3,900 existing lines be
 * rewritten — rewriting them wholesale would be unreviewable and would touch
 * surfaces nobody has looked at, which is exactly the risk this redesign was
 * scoped to avoid. So:
 *
 *   - The blocks this change introduced (the token layer and the behaviour
 *     layer, both delimited by markers) must be free of physical
 *     left/right properties where a logical property exists.
 *   - The pre-existing body of the file is measured, and its physical-property
 *     count must not GROW. The current count is recorded below as a budget.
 *
 * A budget number in a test is only honest if it is checked in both
 * directions: this one fails if the count rises AND tells you to lower it if
 * the count falls, so the ratchet cannot silently slacken.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_design_rtl_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const CSS_FILE = path.join(__dirname, '..', 'frontend', 'css', 'main.css');

/* Physical properties that have a direct logical equivalent. `left`/`right`
   as positioning offsets are included via `inset-inline-*`. Properties with
   no logical equivalent (transform, background-position, text-shadow offsets)
   are deliberately absent — flagging them would be noise. */
const PHYSICAL = [
  { re: /(^|[\s;{])margin-left\s*:/g, use: 'margin-inline-start' },
  { re: /(^|[\s;{])margin-right\s*:/g, use: 'margin-inline-end' },
  { re: /(^|[\s;{])padding-left\s*:/g, use: 'padding-inline-start' },
  { re: /(^|[\s;{])padding-right\s*:/g, use: 'padding-inline-end' },
  { re: /(^|[\s;{])border-left\s*:/g, use: 'border-inline-start' },
  { re: /(^|[\s;{])border-right\s*:/g, use: 'border-inline-end' },
  { re: /(^|[\s;{])border-left-color\s*:/g, use: 'border-inline-start-color' },
  { re: /(^|[\s;{])border-right-color\s*:/g, use: 'border-inline-end-color' },
  { re: /(^|[\s;{])border-top-left-radius\s*:/g, use: 'border-start-start-radius' },
  { re: /(^|[\s;{])border-top-right-radius\s*:/g, use: 'border-start-end-radius' },
  { re: /(^|[\s;{])border-bottom-left-radius\s*:/g, use: 'border-end-start-radius' },
  { re: /(^|[\s;{])border-bottom-right-radius\s*:/g, use: 'border-end-end-radius' },
  { re: /(^|[\s;{])text-align\s*:\s*left/g, use: 'text-align: start' },
  { re: /(^|[\s;{])text-align\s*:\s*right/g, use: 'text-align: end' },
];

/* The pre-existing body of main.css carries this many physical properties.
   The number is a BUDGET, not a target: it may fall, it must never rise. */
const LEGACY_PHYSICAL_BUDGET = 63;

/* ── THE SECOND SCAN: inline styles built in JAVASCRIPT ────────────────────
 *
 * Everything above looks at css/main.css and NOTHING ELSE, which was a real
 * blind spot rather than a theoretical one. This frontend writes most of its
 * new rules as inline `style="..."` strings inside template literals and as
 * `el.style.cssText = '...'` assignments, and 16 physical `margin-left:Npx`
 * declarations had accumulated there -- eleven on the SECOND BUTTON of a row
 * pair in subsystem-retail.js, two in app-shell.js's sync banner, three in
 * import-wizard.js. In Arabic the row reverses, so the gap lands on the outer
 * edge and the two buttons collide. This scan could not see a single one of
 * them, and app-shell.js's own comment already named `margin-left` (physical)
 * as a cost "a dark theme and an Arabic layout would both have had to pay".
 *
 * Same ratchet shape as the stylesheet half: a recorded count that may fall
 * and must never rise. What remains in the budget, deliberately:
 *   * FOUR `margin-left:auto;margin-right:auto` PAIRS (8 of the count) --
 *     direction-neutral centering, not a leading-edge pin, so converting them
 *     would be churn with no RTL meaning.
 *   * 21 `text-align:left|right` declarations, most of them money columns.
 *     Those are a DIFFERENT question (an amount column's alignment is about
 *     digits, not reading order) with its own money/POS suites pinning them,
 *     so they are counted here and left for whoever owns that decision --
 *     counted rather than filtered, so the number cannot quietly grow.
 *   * employees.js's setup-link input, which pairs `text-align:left` with an
 *     explicit `direction:ltr` and explains why three lines below it.
 *   * app-shell.js's kpiCard(), which sets `border-left-color` inline over a
 *     `.sub-kpi-card` rule that lives in css/main.css. That one IS a real
 *     leading-edge accent and should become border-inline-start-color, but
 *     the fix has to move with whatever main.css/rtl.css do for that class,
 *     which is a different owner. Recorded here so it is visible rather than
 *     invisible -- which is the whole point of adding this scan.
 */
const JS_FILES_DIR = path.join(__dirname, '..', 'frontend');
/* RE-MEASURED 2026-09-29 with the lexer-based stripJsComments: 31 (subsystem-
   retail.js 29, app-shell.js 1, employees.js 1). The number is unchanged, but
   the reading behind it is not: with the old regex stripper the same files read
   5, because a comment opener inside a string/regex/template started a fake
   comment and blanked real code. 31 is the true count on correct input, not a
   loosened budget. The lexer's output was also checked character-for-character
   against acorn's comment spans on all 15 frontend .js files (0 differences). */
const LEGACY_JS_PHYSICAL_BUDGET = 31;

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
}

function readSections() {
  const raw = fs.readFileSync(CSS_FILE, 'utf8');
  const tokenBegin = raw.indexOf('[design-tokens:begin]');
  const tokenEnd = raw.indexOf('[design-tokens:end]');
  const behaviourBegin = raw.indexOf('[operational-calm:begin]');
  const behaviourEnd = raw.indexOf('[operational-calm:end]');

  assert.ok(tokenBegin !== -1 && tokenEnd > tokenBegin, 'token-layer markers missing from main.css');
  assert.ok(behaviourBegin !== -1 && behaviourEnd > behaviourBegin, 'behaviour-layer markers missing from main.css');

  const clean = stripComments(raw);
  return {
    newCode: clean.slice(tokenBegin, tokenEnd) + '\n' + clean.slice(behaviourBegin, behaviourEnd),
    legacy: clean.slice(tokenEnd, behaviourBegin),
    raw,
  };
}

function countPhysical(css) {
  const hits = [];
  for (const { re, use } of PHYSICAL) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(css)) !== null) {
      const line = css.slice(0, m.index).split('\n').length;
      hits.push({ prop: m[0].trim().replace(/[;{]/, '').trim(), use, line });
    }
  }
  return hits;
}

function testNewCodeUsesLogicalProperties() {
  const { newCode } = readSections();

  // ANTI-VACUITY: if the markers moved or the slice came back empty, the scan
  // below would find nothing and pass while checking nothing.
  assert.ok(
    newCode.length > 2000,
    `The token + behaviour layers sliced to only ${newCode.length} characters. ` +
    'The section markers have probably moved, so this scan is not looking at ' +
    'the new code at all.'
  );

  const hits = countPhysical(newCode);
  assert.deepStrictEqual(
    hits.map((h) => `${h.prop} (use ${h.use})`), [],
    `${hits.length} physical direction propert(ies) in newly added CSS:\n  ` +
    hits.map((h) => `${h.prop} — use ${h.use} instead`).join('\n  ') +
    '\n\nThis product ships Arabic. A physical property needs a mirror rule in ' +
    'css/rtl.css to be correct in RTL; a logical property is correct by ' +
    'construction and needs nothing.'
  );
  console.log('PASS: the token and behaviour layers contain no physical direction properties');
}

function testLegacyPhysicalPropertyCountDoesNotGrow() {
  const { legacy } = readSections();
  const count = countPhysical(legacy).length;

  assert.ok(
    count <= LEGACY_PHYSICAL_BUDGET,
    `Physical direction properties in the pre-existing CSS rose from ` +
    `${LEGACY_PHYSICAL_BUDGET} to ${count}. Every one of these needs a mirror ` +
    'rule in css/rtl.css to be correct in Arabic. Use the logical equivalent ' +
    'in new rules.'
  );
  assert.ok(
    count >= LEGACY_PHYSICAL_BUDGET - 20,
    `Physical direction properties fell from ${LEGACY_PHYSICAL_BUDGET} to ${count}. ` +
    'Good — but lower LEGACY_PHYSICAL_BUDGET to ' + count + ' so the ratchet ' +
    'actually holds the new ground instead of leaving slack for a regression.'
  );
  console.log(`PASS: legacy physical-property count is ${count}, within the ${LEGACY_PHYSICAL_BUDGET} budget`);
}

function testLogicalPropertiesAreActuallyUsed() {
  /* Guard against "passes because it declares nothing": the new layers should
     not merely avoid physical properties, they should positively use logical
     ones (or box-model-free layout) where they set inline spacing. */
  const { raw } = readSections();
  /* Must include the longhands. An earlier pattern stopped at
     `border-inline-start:` and so counted 2 where the file actually has a
     dozen `border-inline-start-color:` declarations -- under-reporting the
     very thing it exists to confirm. */
  const logical = (raw.match(/(margin|padding|border|inset)-(inline|block)(-(start|end))?(-(color|width|style))?\s*:/g) || []).length;
  assert.ok(
    logical >= 1,
    'No logical direction properties appear anywhere in main.css. The new ' +
    'layers should be setting inline spacing logically, not avoiding the ' +
    'question by setting no spacing at all.'
  );
  console.log(`PASS: ${logical} logical direction propert(ies) present`);
}

/* JS comments carry the WORD "margin-left" all over this frontend (they are
   how the physical/logical decision is explained at each site), so they are
   blanked before the scan -- otherwise fixing a site and documenting the fix
   would RAISE the count.

   WHY THIS IS A LEXER AND NOT A REGEX. This function used to be two regexes,
   and the block-comment one (`/\/\*[\s\S]*?\*\//g`) cannot tell a comment
   opener from the same two characters INSIDE a string, a template literal or a
   regex literal -- a glob string, a regex with a slash-star in it, a template
   holding a quoted slash-star. Each of those opened
   a FAKE comment that ran on to the next real closer and blanked all the real
   code in between. Measured 2026-09-29: about 508k of subsystem-retail.js's
   1.14M non-space characters were blanked (840k of 1.05M at e39fe657), and
   138k of app-shell.js's 313k. The physical-property count below was therefore
   measured on a mangled file, and read 5 where a plain grep finds about 30.

   So this walks the source once, tracking whether it is in code, a '...' or
   "..." string, a `...` template (including nested ${ ... } expressions, which
   are code again), a regex literal, a // comment or a block comment, and blanks
   ONLY real comments -- every one of them, whole-line or trailing, because a
   lexer no longer has to guess. Comments become spaces and every newline is
   kept, so the line numbers this test reports stay true.

   The one judgement call is `/`: division or the start of a regex? It is a
   regex when the previous significant token cannot end an expression -- an
   operator or opening/separating punctuation, the start of the file, or a
   keyword such as `return`. After an identifier, a number, a string, `)` or
   `]` it is division. `}` counts as "regex may follow" (a block just ended)
   even though `({}) / 2` is division: a mis-read there costs at most one
   regex-shaped span, and only if it also contains a `/*`, which this
   frontend does not write. */
const REGEX_MAY_FOLLOW = new Set([...'(,=:[!&|?{};+-*%<>~^']);
const REGEX_KEYWORDS = new Set([
  'return', 'typeof', 'case', 'in', 'of', 'delete', 'void', 'throw', 'new', 'else', 'do', 'instanceof',
]);

function stripJsComments(js) {
  const out = js.split('');
  const n = js.length;
  const blank = (from, to) => {
    for (let k = from; k < to; k++) if (out[k] !== '\n' && out[k] !== '\r') out[k] = ' ';
  };
  const isWordChar = (c) => /[A-Za-z0-9_$]/.test(c) || c > '\x7f';

  // Scans a quoted string starting at the opening quote; returns the index
  // just past it. A raw newline ends it -- a JS string cannot span lines, so
  // an unterminated one must not swallow the rest of the file.
  const skipString = (start) => {
    const quote = js[start];
    let k = start + 1;
    while (k < n && js[k] !== quote && js[k] !== '\n') k += js[k] === '\\' ? 2 : 1;
    return Math.min(k + 1, n);
  };

  // Scans template text from `start` (just after a backtick or a closing `}`
  // of a ${...}). Returns { end, opensExpr }: `end` is the index after the
  // closing backtick, or after the `${` when an expression opens.
  const skipTemplateText = (start) => {
    let k = start;
    while (k < n) {
      if (js[k] === '\\') k += 2;
      else if (js[k] === '`') return { end: k + 1, opensExpr: false };
      else if (js[k] === '$' && js[k + 1] === '{') return { end: k + 2, opensExpr: true };
      else k++;
    }
    return { end: n, opensExpr: false };
  };

  // Scans a regex literal starting at its opening `/`; returns the index just
  // past the closing `/`, or -1 when no closing `/` appears on the line (then
  // it was not a regex after all). A `/` inside a [...] class does not close it.
  const skipRegex = (start) => {
    let k = start + 1;
    let inClass = false;
    while (k < n && js[k] !== '\n') {
      const c = js[k];
      if (c === '\\') k += 2;
      else if (c === '[') { inClass = true; k++; }
      else if (c === ']') { inClass = false; k++; }
      else if (c === '/' && !inClass) return k + 1;
      else k++;
    }
    return -1;
  };

  const exprDepths = []; // brace depth at which each open template ${ began
  let braceDepth = 0;
  let prev = '';         // last significant character ('w' = an identifier/number, 'v' = a finished value)
  let prevWord = '';     // the identifier when prev === 'w'
  let i = 0;

  while (i < n) {
    const c = js[i];
    const next = js[i + 1];

    if (c === ' ' || c === '\t' || c === '\n' || c === '\r') { i++; continue; }

    if (c === '/' && next === '/') {
      let end = js.indexOf('\n', i);
      if (end === -1) end = n;
      blank(i, end);
      i = end;
      continue;
    }
    if (c === '/' && next === '*') {
      let end = js.indexOf('*/', i + 2);
      end = end === -1 ? n : end + 2;
      blank(i, end);
      i = end;
      continue;
    }
    if (c === '"' || c === "'") { i = skipString(i); prev = 'v'; continue; }
    if (c === '`') {
      const t = skipTemplateText(i + 1);
      i = t.end;
      if (t.opensExpr) { exprDepths.push(braceDepth); prev = '{'; } else prev = 'v';
      continue;
    }
    if (c === '/') {
      const regexAllowed = prev === '' || REGEX_MAY_FOLLOW.has(prev) || (prev === 'w' && REGEX_KEYWORDS.has(prevWord));
      const end = regexAllowed ? skipRegex(i) : -1;
      if (end !== -1) { i = end; prev = 'v'; } else { i++; prev = '/'; }
      continue;
    }
    if (c === '{') { braceDepth++; prev = '{'; i++; continue; }
    if (c === '}') {
      if (exprDepths.length && exprDepths[exprDepths.length - 1] === braceDepth) {
        // This `}` closes a template's ${ ... }: back into the template text.
        exprDepths.pop();
        const t = skipTemplateText(i + 1);
        i = t.end;
        if (t.opensExpr) { exprDepths.push(braceDepth); prev = '{'; } else prev = 'v';
        continue;
      }
      braceDepth--;
      prev = '}';
      i++;
      continue;
    }
    if (isWordChar(c)) {
      let k = i + 1;
      while (k < n && isWordChar(js[k])) k++;
      prev = 'w';
      prevWord = js.slice(i, k);
      i = k;
      continue;
    }
    prev = c; // any other punctuation, including ) and ] (after which `/` is division)
    i++;
  }
  return out.join('');
}

/* The SAME property list as the stylesheet scan, with one widened boundary:
   an inline style begins immediately after the opening quote
   (`style="text-align:right`), so a declaration at the very start of the
   attribute has a QUOTE in front of it, not whitespace. Derived from PHYSICAL
   rather than retyped, so a property added there is covered here too -- an
   earlier cut of this scan missed 19 of 30 hits for exactly this reason. */
const JS_PHYSICAL = PHYSICAL.map(({ re, use }) => ({
  re: new RegExp(re.source.replace('(^|[\\s;{])', '(^|[\\s;{"\'])'), re.flags),
  use,
}));

// Physical-property hits in ALREADY-COMMENT-STRIPPED source, as {line, prop, use}.
function findJsPhysical(strippedSrc) {
  const hits = [];
  for (const { re, use } of JS_PHYSICAL) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(strippedSrc)) !== null) {
      const line = strippedSrc.slice(0, m.index).split('\n').length;
      hits.push({ line, prop: m[0].trim().replace(/^["';{]/, '').trim(), use });
    }
  }
  return hits;
}

function scanJsInlineStyles() {
  const files = fs.readdirSync(JS_FILES_DIR).filter((f) => f.endsWith('.js')).sort();
  assert.ok(files.length >= 5,
    `Only ${files.length} .js file(s) found in ${JS_FILES_DIR}. This scan would report a clean ` +
    'bill of health having read almost nothing.');
  const hits = [];
  let scanned = 0;
  for (const f of files) {
    const src = stripJsComments(fs.readFileSync(path.join(JS_FILES_DIR, f), 'utf8'));
    scanned += src.length;
    for (const h of findJsPhysical(src)) hits.push({ file: f, ...h });
  }
  assert.ok(scanned > 200000,
    `Only ${scanned} characters of JavaScript were scanned; the frontend is far larger than that, ` +
    'so treat this as a harness failure rather than a clean result.');
  return hits;
}

function testJsInlineStylePhysicalPropertyCountDoesNotGrow() {
  const hits = scanJsInlineStyles();
  const count = hits.length;

  assert.ok(
    count <= LEGACY_JS_PHYSICAL_BUDGET,
    `Physical direction properties in JS-built inline styles rose from ${LEGACY_JS_PHYSICAL_BUDGET} ` +
    `to ${count}. css/rtl.css cannot mirror an inline style at all -- an inline declaration beats ` +
    'every stylesheet rule -- so each of these is simply WRONG in Arabic, with no second file that ' +
    'could fix it. Use the logical equivalent:\n  ' +
    hits.map((h) => `${h.file}:${h.line}  ${h.prop} — use ${h.use}`).join('\n  ')
  );
  assert.ok(
    count >= LEGACY_JS_PHYSICAL_BUDGET - 10,
    `Physical direction properties in JS fell from ${LEGACY_JS_PHYSICAL_BUDGET} to ${count}. ` +
    'Good — but lower LEGACY_JS_PHYSICAL_BUDGET to ' + count + ' so the ratchet holds the new ' +
    'ground instead of leaving slack for a regression.'
  );
  console.log(`PASS: JS inline-style physical-property count is ${count}, within the ${LEGACY_JS_PHYSICAL_BUDGET} budget`);
}

/* THE SCANNER'S OWN TEST. Every JS-side check below trusts stripJsComments; the
   regex version of it silently blanked half of the two biggest files and this
   suite stayed green, reading a count of 5 where the truth was about 30. So the
   stripper is proven on its own, on small sources where the right answer is
   known by eye, BEFORE anything is measured with it.

   Each "must still count" case puts one trap (a comment opener inside a
   string / regex / template) in front of a REAL declaration and then a REAL
   closing comment after it -- the shape that made the regex version eat the
   declaration. Each trap is its own case so one cannot hide behind another. */
function testJsCommentStripperIsNotFooledByStringsAndRegexes() {
  const DECL = "el.style.cssText = 'margin-left:5px';";
  const REAL_CLOSER = '/* a real block comment that closes the fake one */';
  const mustCount = [
    ['a slash-star inside a single-quoted string', "var a = '*/*';"],
    ['a slash-star inside a double-quoted string', 'var a = "/*";'],
    ['a slash-star inside a regex literal', 'var re = /\\/*/g;'],
    ['a slash-star inside a regex character class', 'var re = /[/*]/;'],
    ['a slash-star inside a template expression', 'var t = `a${\'/*\'}b`;'],
    ['a slash-star inside plain template text', 'var t = `glob: /*`;'],
    ['a nested template inside a template expression', 'var t = `x${`y${1}`}z /*`;'],
    ['a division before a trailing note', 'var d = total / count; // margin-left:4px'],
  ];
  for (const [what, trap] of mustCount) {
    const src = `${trap}\n${DECL}\n${REAL_CLOSER}\n`;
    const hits = findJsPhysical(stripJsComments(src));
    // The division case carries a trailing "// margin-left:4px" that must NOT
    // count, so its expected hit count is the same one (the real DECL).
    assert.strictEqual(hits.length, 1,
      `stripJsComments mangled the source after ${what}: expected the real ` +
      `margin-left declaration to still be counted once, got ${hits.length}.\n` +
      `source:\n${src}\nstripped:\n${stripJsComments(src)}`);
    assert.strictEqual(hits[0].line, 2,
      `the surviving hit after ${what} should be on line 2, not ${hits[0].line}.`);
  }

  const comments = [
    '/* margin-left:1px; */',
    '// margin-left:2px',
    'var x = 1; // margin-left:3px trailing note',
    'var y = 2; /* margin-left:4px */ var z = 3;',
    '/*\n margin-left:5px\n padding-right:6px\n*/',
  ];
  for (const c of comments) {
    const hits = findJsPhysical(stripJsComments(c));
    assert.strictEqual(hits.length, 0,
      `a real comment was scanned as code: ${JSON.stringify(c)} -> ${JSON.stringify(hits)}`);
  }

  // Line numbers survive: same length, and every newline in the same place.
  const mixed = `/* one\ntwo */\nvar a = '*/*';\n// gone\n${DECL}\n${REAL_CLOSER}\n`;
  const stripped = stripJsComments(mixed);
  assert.strictEqual(stripped.length, mixed.length, 'stripping changed the length of the source');
  for (let k = 0; k < mixed.length; k++) {
    assert.strictEqual(stripped[k] === '\n', mixed[k] === '\n',
      `a newline moved at offset ${k}: line numbers would be wrong`);
  }
  const line = findJsPhysical(stripped)[0].line;
  assert.strictEqual(line, mixed.split('\n').findIndex((l) => l.includes('margin-left')) + 1,
    'the reported line does not match the declaration\'s real line');

  console.log(`PASS: stripJsComments keeps real code and drops real comments (${mustCount.length} traps, ${comments.length} comments, line numbers kept)`);
}

/* The margins this change actually removed, pinned BY NAME rather than only
   by the count above. A budget alone cannot tell "eleven margin-left:6px were
   converted" from "eleven were converted and eleven text-aligns were added";
   this says the specific defect is gone and stays gone. */
function testTheRowPairMarginsAreLogical() {
  const files = ['subsystem-retail.js', 'app-shell.js', 'import-wizard.js'];
  const offenders = [];
  let logical = 0;
  for (const f of files) {
    const src = stripJsComments(fs.readFileSync(path.join(JS_FILES_DIR, f), 'utf8'));
    // A leading-edge pin has a LENGTH; `margin-left:auto` is the centering
    // pair and is out of scope on purpose (see the budget comment above).
    const re = /margin-left\s*:\s*(-?[\d.]+[a-z%]*)/g;
    let m;
    while ((m = re.exec(src)) !== null) {
      const line = src.slice(0, m.index).split('\n').length;
      offenders.push(`${f}:${line}  margin-left:${m[1]}`);
    }
    logical += (src.match(/margin-inline-start\s*:/g) || []).length;
  }
  assert.deepStrictEqual(
    offenders, [],
    `${offenders.length} JS-built inline style(s) still pin a LENGTH to the physical left edge:\n  ` +
    offenders.join('\n  ') +
    '\n\nIn Arabic the row reverses, so the gap lands on the outer edge and the two controls ' +
    'collide. Use margin-inline-start.'
  );
  // ANTI-VACUITY: "no margin-left with a length" is also satisfied by a file
  // that sets no inline margin at all, which is exactly what a bad refactor
  // would look like. The conversions must be PRESENT, not merely absent.
  assert.ok(
    logical >= 11,
    `Only ${logical} margin-inline-start declaration(s) across ${files.join(', ')}. The eleven ` +
    'row-pair buttons, the two sync-banner detail spans and the wizard sites were converted, so ' +
    'a count this low means they were deleted rather than converted.'
  );
  console.log(`PASS: no JS inline style pins a length to the physical left edge (${logical} logical margins present)`);
}

function testRtlStylesheetStillCoversTheChartExceptions() {
  /* Charts and canvases must NOT mirror: flipping a chart reverses its axes,
     legend order and value order, which silently misreports the shop's own
     numbers. rtl.css forces them back to LTR; that rule is load-bearing and
     easy to delete by accident during a redesign. */
  const rtl = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'css', 'rtl.css'), 'utf8');
  assert.ok(
    /body\.rtl[^{]*canvas[^{]*\{[^}]*direction\s*:\s*ltr/s.test(rtl) ||
    /canvas/.test(rtl) && /direction\s*:\s*ltr/.test(rtl),
    'css/rtl.css no longer forces canvases back to direction:ltr. Mirroring a ' +
    'chart reverses its axes and value order, so the shop reads its own ' +
    'numbers backwards in Arabic.'
  );
  console.log('PASS: rtl.css still forces charts/canvases back to LTR');
}

/* PER-TEST ISOLATION -- see retail_design_money_test.js for the reasoning and
   the measurement. A flat sequence reports one failure per run however many
   exist, and hides the rest as "not run", which is indistinguishable from
   "passed". */
const CHECKS = [
  ['the JS comment stripper is not fooled by strings and regexes', testJsCommentStripperIsNotFooledByStringsAndRegexes],
  ['new code uses logical properties', testNewCodeUsesLogicalProperties],
  ['the legacy physical-property count does not grow', testLegacyPhysicalPropertyCountDoesNotGrow],
  ['logical properties are actually used', testLogicalPropertiesAreActuallyUsed],
  ['the JS inline-style physical count does not grow', testJsInlineStylePhysicalPropertyCountDoesNotGrow],
  ['no JS inline style pins a length to the left edge', testTheRowPairMarginsAreLogical],
  ['rtl.css still covers the chart exceptions', testRtlStylesheetStillCoversTheChartExceptions],
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
    console.error(`\nFAIL: retail_design_rtl_test.js — ${failures.length} of ${CHECKS.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_design_rtl_test.js — ${CHECKS.length} checks`);
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_design_rtl_test.js (runner)');
  console.error(err.message || err);
  process.exitCode = 1;
}
