/**
 * retail_standalone_page_i18n_test.js -- closes the standalone-page blind
 * spot in this product's Arabic coverage.
 *
 * WHY THIS FILE EXISTS
 *
 * einvoicing.html, licensing.html and whatsapp.html never load app-shell.js
 * -- CLAUDE.md records that no shared shell existed when the first of them
 * was written, and each one says so in its own header. They load i18n.js
 * directly and define their own `t()` fallback, so they are real, reachable
 * Arabic surfaces, but nothing that scans "the frontend" by walking
 * SubsystemApp's rendered screens (retail_surface_i18n_test.js) or by
 * requiring subsystem-retail.js/app-shell.js (retail_attribution_i18n_test.py)
 * ever executes these three files at all.
 *
 * The concrete bug that motivated this file: einvoicing.js's status card
 * builds its "not connected to JoFotara" sentence as three string literals
 * joined with `+` inside a single `t(...)` call --
 *
 *   text: t('E-invoicing is on and every sale is being recorded, ... yet ' +
 *     'because this installation is not connected to JoFotara. ... JoFotara ' +
 *     'portal and enter the Client-ID, Secret-Key and activity number it issues.'),
 *
 * -- and the CONCATENATED sentence was never a catalog key. t() has an
 * identity fallback (i18n.js: `return (text in d) ? d[text] : text`), so it
 * silently rendered in English on an Arabic page. Two more instances of the
 * same class turned up widening this file to cover every t() call in these
 * three scripts, not just the ones with plain string-literal arguments:
 * 'Transport not configured' and 'Enabled' were themselves entirely absent
 * from the catalog, reached through the inline `stateBadge({ text: '...' })`
 * call shape rather than a plain `t('...')`.
 *
 * WHAT "DERIVED FROM THE REAL ARTEFACT" MEANS HERE
 *
 * Both sides of every comparison below come from a live read: the JSON
 * catalogs, and a structural parse of the three scripts' actual source (a
 * hand-rolled string-literal/object-literal reader, not a hand-typed list of
 * strings -- see the parser section). The one place this file DOES hand-type
 * anything is the small, self-verifying inventory of "translation source"
 * const names (TRANSLATION_SOURCE_CONSTS / EXCLUDED_CONSTS below) -- mirroring
 * retail_attribution_i18n_test.py's REFUSAL_PANELS list, which the same test
 * file proves complete against the code (test_every_refusal_panel_is_listed_
 * here) rather than trusting the list on its own. testEveryUpperSnakeConstIs
 * AccountedFor does the same job here: every UPPER_SNAKE const in these three
 * files must be in one list or the other, or the test fails naming it.
 *
 * Run: node products/retail/tests/retail_standalone_page_i18n_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const EN = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, 'locales', 'en.json'), 'utf8'));
const AR = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, 'locales', 'ar.json'), 'utf8'));

// The standalone pages: script + the HTML shell that loads it. Every entry
// here is a page that never loads app-shell.js -- add a sibling page here
// the day one is created, not the day someone notices it was never covered.
const PAGES = [
  { js: 'einvoicing.js', html: 'einvoicing.html' },
  { js: 'licensing.js', html: 'licensing.html' },
  { js: 'whatsapp.js', html: 'whatsapp.html' },
];

// ═════════════════════════════════════════════════════════════════════════
// A small hand-rolled reader for the JS shapes these three files actually
// use: string literals (single/double quoted, with escapes), `'a' + 'b'`
// concatenation, `//` and `/* */` comments, and balanced {..}/[..] literals.
// Not a general JS parser -- these files are simple, disciplined, generated
// code (no innerHTML, everything through el()/t()) and this reads exactly
// the subset of syntax they use.
// ═════════════════════════════════════════════════════════════════════════

function parseLiteral(src, pos) {
  const qc = src[pos];
  if (qc !== "'" && qc !== '"') return null;
  let i = pos + 1, s = '';
  while (i < src.length && src[i] !== qc) {
    if (src[i] === '\\') { s += src[i + 1]; i += 2; continue; }
    s += src[i]; i++;
  }
  if (i >= src.length) return null;
  return { value: s, end: i + 1 };
}

function skipTrivia(src, i) {
  for (;;) {
    while (i < src.length && /\s/.test(src[i])) i++;
    if (src[i] === '/' && src[i + 1] === '/') {
      i += 2;
      while (i < src.length && src[i] !== '\n') i++;
      continue;
    }
    if (src[i] === '/' && src[i + 1] === '*') {
      i += 2;
      while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) i++;
      i += 2;
      continue;
    }
    break;
  }
  return i;
}

/** STRING (+ STRING)* starting at `pos`. Returns {value, end} or null if
 * `pos` is not the start of a string literal. `end` is the index right
 * after the last literal consumed -- trailing non-literal content (a `+`
 * followed by a variable, say) is left unconsumed rather than swallowed. */
function parseConcat(src, pos) {
  let i = skipTrivia(src, pos);
  const first = parseLiteral(src, i);
  if (!first) return null;
  let value = first.value;
  i = first.end;
  for (;;) {
    const j = skipTrivia(src, i);
    if (src[j] !== '+') return { value, end: i };
    const k = skipTrivia(src, j + 1);
    const next = parseLiteral(src, k);
    if (!next) return { value, end: i };
    value += next.value;
    i = next.end;
  }
}

/** Balanced-bracket scan for a `{...}`/`[...]` literal starting at `pos`
 * (which must be `{` or `[`). Returns the index just past the match. Comment-
 * and string-aware so a stray bracket-looking character inside either can
 * never throw the depth count off. */
function scanBalanced(src, pos) {
  const open = src[pos];
  const close = open === '{' ? '}' : ']';
  let depth = 0, i = pos;
  while (i < src.length) {
    if (src[i] === '/' && src[i + 1] === '/') { while (i < src.length && src[i] !== '\n') i++; continue; }
    if (src[i] === '/' && src[i + 1] === '*') { i += 2; while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) i++; i += 2; continue; }
    if (src[i] === "'" || src[i] === '"') {
      const lit = parseLiteral(src, i);
      if (lit) { i = lit.end; continue; }
    }
    if (src[i] === open) { depth++; i++; continue; }
    if (src[i] === close) { depth--; i++; if (depth === 0) return i; continue; }
    i++;
  }
  return src.length;
}

/** Top-level entries of a `{...}` or `[...]` literal (brackets included in
 * `region`). Each entry is {field, kind, value|region}: `field` is the
 * object key (null for a bare array element), `kind` is 'string' | 'nested'
 * | 'other' (an expression this reader does not resolve -- a variable, a
 * ternary, a `t(...)` call as a value). Comment- and trivia-aware, so a
 * `//` line between two entries (REASON_MESSAGES carries one) cannot desync
 * the scan the way a naive character walk would. */
function splitTopLevelEntries(region) {
  const isObj = region[0] === '{';
  const entries = [];
  let i = skipTrivia(region, 1);
  while (i < region.length - 1) {
    if (region[i] === ',') { i = skipTrivia(region, i + 1); continue; }
    if (i >= region.length - 1) break;
    let field = null;
    if (isObj) {
      const m = /^([A-Za-z_$][A-Za-z0-9_$]*|'[^']*'|"[^"]*")\s*:\s*/.exec(region.slice(i));
      if (m) { field = m[1].replace(/^['"]|['"]$/g, ''); i = skipTrivia(region, i + m[0].length); }
    }
    const vch = region[i];
    if (vch === "'" || vch === '"') {
      const parsed = parseConcat(region, i);
      if (parsed) { entries.push({ field, kind: 'string', value: parsed.value }); i = skipTrivia(region, parsed.end); continue; }
    }
    if (vch === '{' || vch === '[') {
      const end = scanBalanced(region, i);
      entries.push({ field, kind: 'nested', region: region.slice(i, end) });
      i = skipTrivia(region, end);
      continue;
    }
    // An expression this reader does not resolve (a t() call, a variable, a
    // ternary...). Recorded, not harvested, and advanced ONE character at a
    // time deliberately: the alternative -- skipping to the next top-level
    // comma -- was tried and is WRONG for `text: t('Paused:') + ' ' + x`,
    // where the comma inside `{ text: ..., cls: ... }` sits past a nested
    // string this reader must not mistake for a fresh bare entry. One
    // character at a time re-enters normal scanning the instant a real
    // entry boundary is reached, at the cost of also visiting embedded
    // literals from inside the unresolved expression -- which is exactly
    // why 'string' entries with `field === null` are NOT harvested by
    // harvestRecordFields below except via the explicit two-element-tuple
    // shape: that is what stops a stray `'Paused:'` or `' '` recovered this
    // way from being reported as if it were real, unwrapped copy.
    entries.push({ field, kind: 'other' });
    i = skipTrivia(region, i + 1);
  }
  return entries;
}

const PROSE_FIELDS = ['text', 'label'];

/** A single `{ text: 'Queued', cls: '...' }` record or `['jordan', 'Label']`
 * tuple: pull the copy out of it. */
function harvestRecordFields(region) {
  const entries = splitTopLevelEntries(region);
  // A clean two-element bare array IS a `[value, label]` option pair -- the
  // only place a field-less bare string is legitimately copy.
  if (region[0] === '[' && entries.length === 2 && entries.every((e) => e.field === null && e.kind === 'string')) {
    return [entries[1].value];
  }
  const out = [];
  for (const e of entries) {
    if (e.kind === 'string' && (e.field === 'text' || e.field === 'label')) out.push(e.value);
  }
  return out;
}

/** `[ ['jordan','Label'], ['none','Label2'] ]`: every tuple's label. */
function harvestTupleListLabels(region) {
  const entries = splitTopLevelEntries(region);
  const out = [];
  for (const e of entries) {
    if (e.kind === 'nested' && e.region[0] === '[') out.push(...harvestRecordFields(e.region));
  }
  return out;
}

/** The declared value of a translation-source const: a string-concat, a flat
 * `{ CODE: 'sentence', ... }` map (REASON_MESSAGES, PROVIDER_LABELS -- every
 * entry IS copy), or a collection of nested records (STATE_LABELS'
 * `{ text: '...', cls: '...' }`, REPORT_TYPES' `{ key, label, ... }`) --
 * decided structurally (are the top-level values themselves strings, or are
 * they all nested?), never by a hand-typed field allowlist beyond `text`/
 * `label`, which are this file family's own el()/confirmDialog() vocabulary. */
function harvestTranslationSource(src, valueStart) {
  const ch = src[valueStart];
  if (ch === "'" || ch === '"') {
    const parsed = parseConcat(src, valueStart);
    return parsed ? [parsed.value] : [];
  }
  if (ch !== '{' && ch !== '[') return [];
  const end = scanBalanced(src, valueStart);
  const region = src.slice(valueStart, end);
  const entries = splitTopLevelEntries(region);
  const allStrings = entries.length > 0 && entries.every((e) => e.kind === 'string');
  const allNested = entries.length > 0 && entries.every((e) => e.kind === 'nested');
  if (allStrings) return entries.map((e) => e.value);
  if (allNested) {
    const out = [];
    for (const e of entries) out.push(...harvestRecordFields(e.region));
    return out;
  }
  return [];
}

function findConstValueStart(src, name) {
  const m = new RegExp('\\bconst\\s+' + name + '\\s*=\\s*').exec(src);
  return m ? m.index + m[0].length : -1;
}

/** Every top-level `t(...)` call whose argument is a plain string literal or
 * a `'a' + 'b' + ...` concatenation of them -- resolved to its runtime value,
 * so a call built from concatenated parts (the einvoicing bug) is treated
 * identically to a single literal rather than being invisible to a regex
 * that only matches `t('single string')`. {key: [line, ...]}. */
function findLiteralTCalls(src) {
  const found = new Map();
  const re = /\bt\(/g;
  let m;
  while ((m = re.exec(src))) {
    const argStart = m.index + 2;
    let depth = 1, i = argStart;
    while (i < src.length && depth > 0) {
      if (src[i] === '(') depth++;
      else if (src[i] === ')') { depth--; if (depth === 0) break; }
      i++;
    }
    const closeParen = i;
    const parsed = parseConcat(src, argStart);
    if (parsed && skipTrivia(src, parsed.end) === closeParen) {
      const line = src.slice(0, m.index).split('\n').length;
      if (!found.has(parsed.value)) found.set(parsed.value, []);
      found.get(parsed.value).push(line);
    }
  }
  return found;
}

/** The `[[...],[...]].forEach(([value, label]) => { ... t(label) ... })`
 * idiom these three files use for <select> options: an INLINE array (never
 * assigned to a const, so the const-based harvest above cannot see it),
 * immediately walked with a callback that passes its second destructured
 * parameter to t(). Found from the idiom itself, not from a hand-typed list
 * of which arrays use it. */
function findForEachTupleSources(src) {
  const results = [];
  const re = /\]\s*\.forEach\(\(\[\s*(\w+)\s*,\s*(\w+)\s*\]\)\s*=>\s*\{/g;
  let m;
  while ((m = re.exec(src))) {
    const closeBracket = m.index;
    let depth = 0, i = closeBracket;
    for (; i >= 0; i--) {
      if (src[i] === ']') depth++;
      else if (src[i] === '[') { depth--; if (depth === 0) break; }
    }
    if (i < 0) continue;
    const arrLiteral = src.slice(i, closeBracket + 1);
    const body = src.slice(re.lastIndex, Math.min(src.length, re.lastIndex + 400));
    const secondParam = m[2];
    if (!new RegExp('\\bt\\(\\s*' + secondParam + '\\b').test(body)) continue;
    results.push({ at: i, end: closeBracket + 1, strings: harvestTupleListLabels(arrLiteral) });
  }
  return results;
}

/** `function NAME(PARAM) { ... t(PARAM.text|label) ... }` -- a "translating
 * accessor" like stateBadge(label). Found by the pattern itself (the
 * function's own parameter feeding straight into a t() field access), not
 * by naming stateBadge here. */
function findAccessorFunctions(src) {
  const re = /function\s+(\w+)\s*\(\s*(\w+)\s*\)\s*\{/g;
  const found = [];
  let m;
  while ((m = re.exec(src))) {
    const name = m[1], param = m[2];
    const body = src.slice(re.lastIndex, Math.min(src.length, re.lastIndex + 300));
    const fm = new RegExp('\\bt\\(\\s*' + param + '\\.(text|label)\\b').exec(body);
    if (fm) found.push({ name, field: fm[1] });
  }
  return found;
}

/** Every inline-object-literal call site of a translating accessor:
 * `stateBadge({ text: '...', cls: '...' })` -- the shape that hid 'Transport
 * not configured' and 'Enabled': neither is a plain `t('...')` call, and
 * neither is inside a named const, so nothing else here would see them. */
function findInlineAccessorCallLiterals(src, funcName) {
  const strings = [];
  const spans = [];
  const g = new RegExp('\\b' + funcName + '\\(\\s*\\{', 'g');
  let m;
  while ((m = g.exec(src))) {
    const braceStart = g.lastIndex - 1;
    const end = scanBalanced(src, braceStart);
    strings.push(...harvestRecordFields(src.slice(braceStart, end)));
    spans.push([braceStart, end]);
  }
  return { strings, spans };
}

// ── Part 2 instrument: a PROSE_FIELD literal that never reaches t() at all ──
//
// `FIELD: '...'`/`FIELD: "..."` matched directly, so `text: t('...')` never
// matches (there is a `t(` between the colon and the quote) -- every hit
// here is, by construction, a literal sitting at that exact call site with
// no t() wrapper around it anywhere. A hit inside a translation-source span
// (a *_LABELS definition, an inline stateBadge({...}) call, an option-tuple
// array) is expected and excluded via `excludedSpans`; anything left over is
// either a real defect or belongs in EXCLUSIONS with a written reason.
const PROSE_FIELD_LITERAL_RE = /\b(text|placeholder|title|message|confirmLabel|cancelLabel)\s*:\s*('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")/g;
function findHardcodedProseFieldLiterals(src, excludedSpans) {
  const out = [];
  let m;
  PROSE_FIELD_LITERAL_RE.lastIndex = 0;
  while ((m = PROSE_FIELD_LITERAL_RE.exec(src))) {
    if (excludedSpans.some(([s, e]) => m.index >= s && m.index < e)) continue;
    const line = src.slice(0, m.index).split('\n').length;
    out.push({ line, field: m[1], value: m[2].slice(1, -1) });
  }
  return out;
}

// ═════════════════════════════════════════════════════════════════════════
// The inventory: every UPPER_SNAKE const in these three files is either a
// translation source (harvested) or an explained exclusion. Self-verifying:
// testEveryUpperSnakeConstIsAccountedFor below fails by name if a NEW
// UPPER_SNAKE const shows up in neither list, so this cannot silently rot
// the way a plain hand-typed list would -- see retail_attribution_i18n_
// test.py's REFUSAL_PANELS for the precedent this mirrors.
// ═════════════════════════════════════════════════════════════════════════

const TRANSLATION_SOURCE_CONSTS = {
  'einvoicing.js': ['OUTBOX_STATE_LABELS', 'PROVIDER_LABELS'],
  'licensing.js': ['STATE_LABELS', 'REASON_MESSAGES', 'LOCAL_VERIFICATION_MESSAGE'],
  'whatsapp.js': ['REPORT_TYPES', 'OUTBOX_STATE_LABELS'],
};

const EXCLUDED_CONSTS = {
  'einvoicing.js': {
    KNOWN_REGIMES: 'value codes fed to a <select> (\'jordan\'/\'none\'), not display copy -- ' +
      'the human labels are a separate inline [value,label] array, covered by the ' +
      'forEach-tuple detector.',
  },
  'licensing.js': {
    LOCAL_VERIFICATION_REASON_CODES: 'a membership set (every value is the number 1), not strings.',
    CLOCK_FIXABLE_REASON_CODES: 'a membership set (every value is the number 1), not strings.',
    TRANSIENT_REASON_CODES: 'a membership set (every value is the number 1), not strings.',
    PENDING_ACTIVATION_KEY: 'an internal localStorage key, never rendered to a user.',
    PENDING_MAX_AGE_MS: 'a numeric constant.',
    AWAITING_POLL_MS: 'a numeric constant.',
    ACTIVATION_HANDOFF_KEY: 'an internal sessionStorage key, never rendered to a user.',
  },
  'whatsapp.js': {},
};

// Genuinely-not-prose literals that sit directly in a PROSE_FIELD (so the
// scan above finds them) and are correctly excluded from the catalog
// requirement -- each with a reason a reviewer can check, per the brief's
// "explicit exclusion with a written reason" requirement. A missing reason
// fails testEveryExclusionHasAWrittenReason below.
const HARDCODED_LITERAL_EXCLUSIONS = {
  'licensing.js': [
    {
      field: 'placeholder', value: 'AURA-RETAIL-XXXX-YYYY-ZZZZ',
      reason: 'a license-key FORMAT template shown as input placeholder text (dashes and ' +
        'segment layout) -- it carries no words in any language, it is a code pattern, ' +
        'the same way a SKU or a UUID is data rather than copy elsewhere in this product.',
    },
  ],
};

// ═════════════════════════════════════════════════════════════════════════
// Per-file harvest: everything a real render of this page could hand to
// t(), plus every hardcoded literal that never reaches t() at all.
// ═════════════════════════════════════════════════════════════════════════

function harvestFile(file) {
  const src = fs.readFileSync(path.join(FRONTEND_DIR, file), 'utf8');
  const excludedSpans = [];
  const dynamicStrings = []; // [{value, where}]

  for (const name of TRANSLATION_SOURCE_CONSTS[file] || []) {
    const vs = findConstValueStart(src, name);
    assert.ok(vs !== -1, `${file}: declared translation source '${name}' was not found as a const declaration -- TRANSLATION_SOURCE_CONSTS has drifted from the code.`);
    const strings = harvestTranslationSource(src, vs);
    for (const s of strings) dynamicStrings.push({ value: s, where: `const ${name}` });
    if (src[vs] === '{' || src[vs] === '[') excludedSpans.push([vs, scanBalanced(src, vs)]);
  }

  for (const t of findForEachTupleSources(src)) {
    for (const s of t.strings) dynamicStrings.push({ value: s, where: `inline option array @${t.at}` });
    excludedSpans.push([t.at, t.end]);
  }

  const accessorSpans = [];
  for (const a of findAccessorFunctions(src)) {
    const { strings, spans } = findInlineAccessorCallLiterals(src, a.name);
    for (const s of strings) dynamicStrings.push({ value: s, where: `inline ${a.name}({${a.field}: ...}) call` });
    accessorSpans.push(...spans);
  }
  excludedSpans.push(...accessorSpans);

  const literalTCalls = findLiteralTCalls(src);

  const allConsts = [...src.matchAll(/^\s*const\s+([A-Z][A-Z0-9_]*)\s*=/gm)].map((m) => m[1]);

  const hardcoded = findHardcodedProseFieldLiterals(src, excludedSpans);

  return { src, dynamicStrings, literalTCalls, allConsts, hardcoded };
}

// ═════════════════════════════════════════════════════════════════════════
// Checks
// ═════════════════════════════════════════════════════════════════════════

function hasRealArabic(key) {
  const value = AR[key];
  return typeof value === 'string' && value.trim() && value !== EN[key];
}

function testEveryLiteralTCallExistsInBothCatalogs() {
  let totalKeys = 0;
  const missing = [];
  for (const { js } of PAGES) {
    const { literalTCalls } = harvestFile(js);
    for (const [key, lines] of literalTCalls) {
      totalKeys++;
      const inEn = key in EN;
      const inAr = key in AR && hasRealArabic(key);
      if (!inEn || !inAr) {
        missing.push(`${js}:${lines.join(',')} ${JSON.stringify(key.slice(0, 90))} en:${inEn ? 'yes' : 'MISSING'} ar:${inAr ? 'yes' : 'MISSING/untranslated'}`);
      }
    }
  }
  assert.ok(totalKeys >= 150, `Only ${totalKeys} literal t() call(s) found across the standalone pages; expected at least 150. The parser likely stopped matching.`);
  assert.deepStrictEqual(missing, [], 'These t() calls (literal or string-concatenated) resolve to a key missing or untranslated in a locale catalog:\n  ' + missing.join('\n  ') + '\n\nAdd each to BOTH locales/en.json and locales/ar.json with real Arabic. t() falls back to the English key for anything it cannot find, so these render in English on an Arabic page.');
  console.log(`PASS: all ${totalKeys} literal/concatenated t() call keys exist in both catalogs with real Arabic`);
}

function testEveryDynamicTranslationSourceExistsInBothCatalogs() {
  let total = 0;
  const missing = [];
  for (const { js } of PAGES) {
    const { dynamicStrings } = harvestFile(js);
    for (const { value, where } of dynamicStrings) {
      total++;
      const inEn = value in EN;
      const inAr = value in AR && hasRealArabic(value);
      if (!inEn || !inAr) {
        missing.push(`${js} (${where}): ${JSON.stringify(value.slice(0, 90))} en:${inEn ? 'yes' : 'MISSING'} ar:${inAr ? 'yes' : 'MISSING/untranslated'}`);
      }
    }
  }
  assert.ok(total >= 55, `Only ${total} dynamic translation-source string(s) found across the standalone pages (STATE_LABELS/OUTBOX_STATE_LABELS/REASON_MESSAGES/PROVIDER_LABELS/REPORT_TYPES, the <select> option arrays, and the inline stateBadge({text:...}) call sites); expected at least 55. The harvester likely stopped matching one of the known shapes.`);
  assert.deepStrictEqual(missing, [], 'These strings are handed to t() only indirectly (through a *_LABELS/REASON_MESSAGES-style map, an inline stateBadge({text:...}) call, or a <select> option array) and are missing or untranslated in a locale catalog:\n  ' + missing.join('\n  ') + '\n\nThis is exactly the shape that hid \'Transport not configured\' and \'Enabled\' -- neither is a plain t(\'...\') literal, so a scan of only direct t() calls cannot see them.');
  console.log(`PASS: all ${total} dynamic translation-source strings exist in both catalogs with real Arabic`);
}

function testEveryUpperSnakeConstIsAccountedFor() {
  const offenders = [];
  for (const { js } of PAGES) {
    const { allConsts } = harvestFile(js);
    const known = new Set([...(TRANSLATION_SOURCE_CONSTS[js] || []), ...Object.keys(EXCLUDED_CONSTS[js] || {})]);
    for (const name of allConsts) {
      if (!known.has(name)) offenders.push(`${js}: const ${name}`);
    }
  }
  assert.deepStrictEqual(offenders, [], 'UPPER_SNAKE const(s) not classified as a translation source or an excluded const:\n  ' + offenders.join('\n  ') + '\n\nAdd each to TRANSLATION_SOURCE_CONSTS (if it carries copy that reaches t()) or to EXCLUDED_CONSTS with a written reason (if it does not) in retail_standalone_page_i18n_test.js.');
  console.log('PASS: every UPPER_SNAKE const in the three standalone scripts is a declared translation source or an explained exclusion');
}

function testNoHardcodedProseFieldLiteralReachesARenderCallSite() {
  let totalExclusions = 0;
  for (const { js } of PAGES) {
    const { hardcoded } = harvestFile(js);
    const allowed = HARDCODED_LITERAL_EXCLUSIONS[js] || [];
    const offenders = hardcoded.filter((h) => !allowed.some((a) => a.field === h.field && a.value === h.value));
    assert.deepStrictEqual(
      offenders.map((o) => `${js}:${o.line} ${o.field}: ${JSON.stringify(o.value)}`), [],
      `${js} has a hardcoded PROSE_FIELD literal that never reaches t() at all:\n  ` +
      offenders.map((o) => `${js}:${o.line} ${o.field}: ${JSON.stringify(o.value)}`).join('\n  ') +
      '\n\nRoute it through t() and add it to both catalogs, or -- if it genuinely carries no ' +
      'translatable language (a code, an identifier, a brand name) -- add it to ' +
      'HARDCODED_LITERAL_EXCLUSIONS with a written reason.'
    );
    // The other half: every declared exclusion must still be FOUND (i.e.
    // still genuinely hardcoded) -- an exclusion for a string that got
    // wrapped in t() later is stale and grants a permanent pass to nothing.
    for (const a of allowed) {
      totalExclusions++;
      assert.ok(
        hardcoded.some((h) => h.field === a.field && h.value === a.value),
        `${js}: HARDCODED_LITERAL_EXCLUSIONS entry ${JSON.stringify(a.value)} is no longer found ` +
        'as a hardcoded literal in the file -- remove the stale exclusion.'
      );
    }
  }
  console.log(`PASS: no unexplained hardcoded PROSE_FIELD literal in any standalone script (${totalExclusions} written exclusion(s), all still live)`);
}

function testEveryExclusionHasAWrittenReason() {
  const bad = [];
  for (const [file, entries] of Object.entries(EXCLUDED_CONSTS)) {
    for (const [name, reason] of Object.entries(entries)) {
      if (!reason || !reason.trim()) bad.push(`${file}: EXCLUDED_CONSTS.${name}`);
    }
  }
  for (const [file, entries] of Object.entries(HARDCODED_LITERAL_EXCLUSIONS)) {
    for (const e of entries) {
      if (!e.reason || !e.reason.trim()) bad.push(`${file}: HARDCODED_LITERAL_EXCLUSIONS ${JSON.stringify(e.value)}`);
    }
  }
  assert.deepStrictEqual(bad, [], 'Exclusion(s) with no written reason: ' + bad.join(', ') + '. A reasonless exclusion is exactly the silent exemption this test exists to prevent.');
  console.log('PASS: every exclusion (const or literal) carries a non-empty written reason');
}

// ── The standalone pages' own HTML: data-i18n / data-i18n-ph attributes ────
// i18n.js applies these the same way it applies t() (same dictionaries, same
// identity fallback -- see apply() in i18n.js), so a heading or label tagged
// data-i18n with no catalog entry is exactly the same defect wearing HTML
// clothes instead of a t() call. These files are read-only here: this test
// does not own einvoicing.html/licensing.html/whatsapp.html and never edits
// them, only scans them.
const DATA_I18N_RE = /data-i18n(?:-ph)?="([^"]*)"/g;

function findHtmlI18nKeys(html) {
  const keys = [];
  let m;
  DATA_I18N_RE.lastIndex = 0;
  while ((m = DATA_I18N_RE.exec(html))) keys.push(m[1]);
  return keys;
}

function testEveryHtmlDataI18nKeyExistsInBothCatalogs() {
  let total = 0;
  const missing = [];
  for (const { html } of PAGES) {
    const src = fs.readFileSync(path.join(FRONTEND_DIR, html), 'utf8');
    const keys = findHtmlI18nKeys(src);
    for (const key of keys) {
      total++;
      const inEn = key in EN;
      const inAr = key in AR && hasRealArabic(key);
      if (!inEn || !inAr) missing.push(`${html}: ${JSON.stringify(key.slice(0, 90))} en:${inEn ? 'yes' : 'MISSING'} ar:${inAr ? 'yes' : 'MISSING/untranslated'}`);
    }
  }
  assert.ok(total >= 10, `Only ${total} data-i18n key(s) found across the standalone pages' HTML; expected at least 10.`);
  assert.deepStrictEqual(missing, [], 'These data-i18n keys are missing or untranslated in a locale catalog:\n  ' + missing.join('\n  '));
  console.log(`PASS: all ${total} data-i18n key(s) in the standalone pages' own HTML exist in both catalogs with real Arabic`);
}

function testCatalogsRemainInParity() {
  const enKeys = Object.keys(EN);
  const arKeys = Object.keys(AR);
  assert.deepStrictEqual(enKeys.filter((k) => !(k in AR)), [], 'keys present in en.json but missing from ar.json');
  assert.deepStrictEqual(arKeys.filter((k) => !(k in EN)), [], 'keys present in ar.json but missing from en.json');
  console.log(`PASS: ${enKeys.length} catalog keys remain in parity`);
}

// ═════════════════════════════════════════════════════════════════════════
// Mutation self-test: the parser catches the shapes it exists for, on small
// fixtures it controls -- not the real files, which are proven separately in
// VERIFY (mutate the real file, see this test go red, restore, see it go
// green). A scanner that cannot be shown failing on a planted defect is
// indistinguishable from one whose pattern silently stopped matching.
// ═════════════════════════════════════════════════════════════════════════

function testTheParserCatchesTheKnownDefectShapes() {
  // 1. The named bug: a t() call built from concatenated literals.
  const concatSrc = "const x = t('Part one ' +\n  'part two.');";
  const found = findLiteralTCalls(concatSrc);
  assert.ok(found.has('Part one part two.'), 'findLiteralTCalls did not resolve a concatenated t() argument to one key.');

  // 2. A t() call on a variable/expression must NOT be reported as a literal
  // key (it has none) -- proving the concat-parser does not over-match.
  const dynamicSrc = "const y = t(SOME_VAR.text);";
  assert.deepStrictEqual([...findLiteralTCalls(dynamicSrc).keys()], [], 'findLiteralTCalls treated a member-expression argument as a literal key.');

  // 3. A flat sentence map (REASON_MESSAGES/PROVIDER_LABELS shape).
  const flatSrc = "{ A: 'First sentence.', B: 'Second sentence.' }";
  assert.deepStrictEqual(
    harvestTranslationSource(flatSrc, 0).sort(),
    ['First sentence.', 'Second sentence.'],
    'harvestTranslationSource did not harvest a flat CODE: \'sentence\' map.'
  );

  // 4. A nested record map (STATE_LABELS shape) -- only text:/label: fields,
  // never the sibling metadata field.
  const nestedSrc = "{ OK: { text: 'Ready', cls: 'state-active' }, BAD: { text: 'Broken', cls: 'state-restricted' } }";
  assert.deepStrictEqual(
    harvestTranslationSource(nestedSrc, 0).sort(),
    ['Broken', 'Ready'],
    'harvestTranslationSource did not harvest text: fields from a nested record map.'
  );
  assert.ok(!harvestTranslationSource(nestedSrc, 0).includes('state-active'), 'harvestTranslationSource harvested a non-prose field (cls) from a nested record.');

  // 5. A comment sitting between two flat-map entries must not desync the
  // scan -- this is the exact shape that made REASON_MESSAGES read as empty
  // during development of this file.
  const commentedSrc = "{ A: 'One.',\n  // an explanatory comment\n  B: 'Two.' }";
  assert.deepStrictEqual(harvestTranslationSource(commentedSrc, 0).sort(), ['One.', 'Two.'], 'a // comment between two flat-map entries broke the harvest.');

  // 6. The option-tuple shape: only index 1 (the label), never index 0 (the
  // value code).
  assert.deepStrictEqual(harvestRecordFields("['jordan', 'Jordan label']"), ['Jordan label'], 'harvestRecordFields did not extract the label half of a [value,label] tuple.');
  assert.ok(!harvestRecordFields("['jordan', 'Jordan label']").includes('jordan'), 'harvestRecordFields harvested the CODE half of a [value,label] tuple, not just the label.');

  // 7. The exact false-positive this file's own development hit: a `text:`
  // value that is an unresolved expression (t() call plus concatenation)
  // must contribute NOTHING, not a stray fragment recovered mid-expression.
  const messyRecord = "{ text: t('Paused:') + ' ' + data.killswitch.reason, cls: 'state-warning' }";
  assert.deepStrictEqual(harvestRecordFields(messyRecord), [], 'harvestRecordFields harvested a fragment out of an unresolved text: expression (the exact false positive found while writing this file).');

  console.log('PASS: the parser catches all seven known defect/false-positive shapes on fixtures');
}

// ═════════════════════════════════════════════════════════════════════════

const CHECKS = [
  ['catalogs remain in parity', testCatalogsRemainInParity],
  ['the parser catches the known defect shapes (fixtures)', testTheParserCatchesTheKnownDefectShapes],
  ['every exclusion has a written reason', testEveryExclusionHasAWrittenReason],
  ['every UPPER_SNAKE const is a translation source or an explained exclusion', testEveryUpperSnakeConstIsAccountedFor],
  ['every literal/concatenated t() call exists in both catalogs', testEveryLiteralTCallExistsInBothCatalogs],
  ['every dynamic translation-source string exists in both catalogs', testEveryDynamicTranslationSourceExistsInBothCatalogs],
  ['no hardcoded PROSE_FIELD literal reaches a render call site unexplained', testNoHardcodedProseFieldLiteralReachesARenderCallSite],
  ['every HTML data-i18n key exists in both catalogs', testEveryHtmlDataI18nKeyExistsInBothCatalogs],
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
    console.error(`\nFAIL: retail_standalone_page_i18n_test.js - ${failures.length} of ${CHECKS.length} failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_standalone_page_i18n_test.js - ${CHECKS.length} checks`);
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_standalone_page_i18n_test.js (runner)');
  console.error((err && err.message) || err);
  process.exitCode = 1;
}
