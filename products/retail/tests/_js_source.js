/**
 * Shared source-text helpers for the retail SOURCE-SCANNING tests (the ones that
 * read frontend/*.js as text and count patterns in it).
 *
 * Extracted 2026-09-29 from retail_design_rtl_test.js so that every JS scanner
 * measures through ONE comment stripper. Two tests had each grown a home-made
 * regex stripper with the same defect (see below); fixing one and leaving the
 * other is how a suite stays green while measuring a mangled file.
 *
 * This is a plain CommonJS module (no other test file shares helpers yet, so
 * there is no convention to follow). It is deliberately NOT named *_test.js:
 * products/run_all_tests.py globs *_test.js, so this file is never run as a test.
 *
 * Blanks comments only; strings, templates and regexes are left as code:
 *   const { stripJsComments, codeLines } = require('./_js_source');
 */
'use strict';

/* JS comments in this frontend carry the very words the scanners look for
   (e.g. the word "margin-left" explaining a physical/logical decision, or a
   quoted `_wireModalA11y(` call), so they are blanked before any scan --
   otherwise documenting a fix would change a count.

   WHY THIS IS A LEXER AND NOT A REGEX. This function used to be two regexes,
   and the block-comment one (`/\/\*[\s\S]*?\*\//g`) cannot tell a comment
   opener from the same two characters INSIDE a string, a template literal or a
   regex literal -- a glob string, a regex with a slash-star in it, a template
   holding a quoted slash-star. Each of those opened
   a FAKE comment that ran on to the next real closer and blanked all the real
   code in between. Measured 2026-09-29: about 508k of subsystem-retail.js's
   1.14M non-space characters were blanked (840k of 1.05M at e39fe657), and
   138k of app-shell.js's 313k. The RTL suite's physical-property count was
   therefore measured on a mangled file, and read 5 where a plain grep finds
   about 30. The line-based `line.replace(/\/\/.*$/, '')` idiom has the mirror
   defect: it cuts a line at the `//` of an 'https://...' string literal, which
   dropped 6 real modal-overlay build sites from the modal coverage count.

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

/* Line-based view of the comment-stripped source, for callers that report
   file:line. Same line numbers as the original file (newlines are kept). */
function codeLines(src) {
  return stripJsComments(src).split('\n');
}

module.exports = { stripJsComments, codeLines };
