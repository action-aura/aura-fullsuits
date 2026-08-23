/**
 * OPERATIONAL CALM — the RENDERED till, reconstructed, so the design tests can
 * reason about what a cashier actually sees.
 *
 * WHY THIS FILE EXISTS
 *
 * The previous contrast test parsed the token block out of css/main.css and
 * proved a property over it: every --text-* token clears AA on every
 * --surface-* token. That property is true, it is still asserted (see
 * retail_design_contrast_test.js), and it was worth nothing to the two worst
 * pairings on the screen, because NEITHER OF THEM IS MADE OF TOKENS:
 *
 *   POS "Held" button                  1.48:1   #cbd5e1 on white
 *   Recent Transactions payment badges 1.91:1   #38bdf8 on a 15% tint of itself
 *
 * Both come from `RetailSystem._injectStyles()` in subsystem-retail.js — a
 * stylesheet the token layer never sees, injected into <head> at runtime, whose
 * literals were chosen for a near-black HUD and now land on a near-white till.
 * Both were WORSE than the 2.64:1 grey-on-white trap the previous pass was
 * celebrated for fixing, on the same two screens, and the test went green
 * anyway. That is not a missing assertion; it is a test whose SCOPE
 * manufactured the state that hid the bug.
 *
 * The only scope that cannot do that is the one the cashier has: the rendered
 * page. So this file builds it. It loads the real subsystem-retail.js into a
 * `vm` sandbox (never a reimplementation), renders the real screens, splices
 * every deferred innerHTML/textContent write back into the tree where the code
 * actually put it, wraps the result in the real shell chain from app-shell.js,
 * and hands the other design tests a DOM plus a cascade engine that resolves
 * what colour and what surface each element ACTUALLY gets.
 *
 * WHAT THIS FILE ITSELF ASSERTS
 *
 * That the corpus is real. Every downstream assertion is a loop over these
 * elements, so a corpus that quietly shrank — a render that threw and was
 * swallowed, a splice that stopped matching, a stub that silently returned an
 * empty string — would turn retail_design_contrast_test.js and
 * retail_design_focus_test.js green while checking almost nothing. That is
 * exactly the failure this whole file exists to make impossible, so the
 * corpus's own size and its known landmarks are asserted here, once, loudly.
 *
 * And that the corpus is CLOSED AGAINST THE PRODUCT. Everything above checks
 * that the declared screens really rendered; none of it could notice that the
 * router had a section nobody had declared. It had five, and the Reports page
 * shipped white-on-white through the round whose stated purpose was widening
 * this corpus, because `reports` was a live `case` in RetailSystem.render() the
 * whole time and no test compared the two lists. It does now — see
 * testTheCorpusIsClosedAgainstTheRouter, SCREEN_ROUTES and ROUTE_EXCLUSIONS.
 * A route is BUILT or it is EXCLUDED IN WRITING; silence is not an option.
 *
 * WHAT IT DELIBERATELY DOES NOT MODEL, so no caller mistakes silence for proof:
 *
 *   * ::before / ::after generated content. It is real text (the accounting
 *     parentheses are drawn this way), but it has no element to hang a
 *     resolved pairing on. Callers that care must assert on it separately.
 *   * @media-nested rules. A rule that only applies below 1100px is a real
 *     pairing at that width, but it is CONDITIONAL, and quietly folding it
 *     into the unconditional cascade would report a colour the wide till never
 *     shows. They are collected into `ruleTable.mediaColour` for callers to
 *     COUNT rather than to swallow.
 *   * Screens the corpus still does not render: the employee screen and the
 *     Admin Centre (both named in ROUTE_EXCLUSIONS with the reason, and both
 *     recoverable), plus the edit/create form modals. Nothing here pretends
 *     otherwise: retail_design_contrast_test.js enumerates every shared-chrome
 *     colour rule this corpus fails to exercise, prints each one with the ratio
 *     it would measure, MEASURES that ratio against AA, and caps the total — so
 *     the blind spot is a published number with a floor under it rather than a
 *     silence. Reports, Categories and the scanner settings panel used to be on
 *     this list and are now in the corpus.
 *
 * WHAT IT MODELS THAT A NAIVE CASCADE DOES NOT: `opacity`.
 *
 * `opacity` is not a colour property and never appears in a colour declaration,
 * so a resolver that only reads `color` and `background` cannot see it at all —
 * and this file could not, for two rounds. The consequence is not academic:
 * `.pos-card-outofstock { opacity:.45 }` washes the whole sold-out tile, so the
 * product name this harness used to report at 15.95:1 is composited by the
 * browser at 2.78:1 and its "Out of stock" line at 2.37:1 — WORSE than the
 * 2.64:1 grey-on-white defect an earlier round was celebrated for fixing, on a
 * screen that IS in the corpus, under a green suite. Setting that same rule to
 * `opacity:.06` — a tile you cannot read at all — passed every suite.
 *
 * So opacity is modelled the way the compositor implements it: as a GROUP.
 * An element with opacity < 1 is rendered into its own buffer WITH its
 * descendants, its background and its outline, and that buffer is then
 * composited over whatever is behind the group. See `opacityGroups()` and
 * `paintThroughOpacity()` for the arithmetic and for what stays approximate.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_design_render_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const dom = require('./retail_surface_domlite.js');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const RETAIL_JS = path.join(FRONTEND, 'subsystem-retail.js');
const MAIN_CSS = path.join(FRONTEND, 'css', 'main.css');
const RTL_CSS = path.join(FRONTEND, 'css', 'rtl.css');
const INDEX_HTML = path.join(FRONTEND, 'index.html');

// ─────────────────────────────────────────────────────────────────────────────
// 1. STYLESHEET SOURCES, IN CASCADE ORDER
// ─────────────────────────────────────────────────────────────────────────────
//
// Order matters and is not a guess -- it is the document order the browser
// sees, read off index.html and the code that injects the rest:
//
//   0 css/main.css              <link>, index.html line ~37
//   1 css/rtl.css               <link>, immediately after
//   2 index.html's own <style>  it is what gives <body> its background, and
//                               therefore what every otherwise-transparent
//                               element ultimately sits on
//   3 #ret-styles               RetailSystem._injectStyles() appends this to
//                               document.head AT RUNTIME, so it is LAST in the
//                               head and beats main.css at equal specificity.
//                               This is where both reported bugs live.
//   4 the screen's own <style>  emitted inside c.innerHTML, i.e. in <body>,
//                               so it is later still.
//
// Getting this order wrong would silently invert every override in the app, so
// the SOURCES list is asserted non-empty and each entry is labelled for the
// failure messages downstream.

function readInjectedStylesheet(jsSource) {
  // _injectStyles() builds one template literal and assigns it to
  // `s.textContent`. Read it out of the source rather than executing it, so
  // this works even for callers that never render a screen.
  const m = /s\.textContent\s*=\s*`([\s\S]*?)`;/.exec(jsSource);
  assert.ok(m, 'Could not find _injectStyles()\'s `s.textContent = `...`` block in subsystem-retail.js');
  return m[1];
}

/**
 * The per-screen <style> blocks emitted inside each render's template literal.
 *
 * An opener sitting inside a JS COMMENT is skipped, and that is not fussiness:
 * subsystem-retail.js's own dashboard comment says "scoped under .rdash in this
 * render's own <style> block", and a naive scan pairs that phrase with the next
 * real `</style>`, swallowing an entire real stylesheet into one bogus block of
 * prose. The rules inside it then vanish from the cascade -- the silent
 * shrinkage this whole file exists to prevent, reintroduced by its own reader.
 */
function readScreenStylesheets(jsSource) {
  const lines = jsSource.split('\n');
  const lineStart = [];
  let acc = 0;
  for (const line of lines) { lineStart.push(acc); acc += line.length + 1; }
  const lineOf = (idx) => {
    let lo = 0, hi = lineStart.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (lineStart[mid] <= idx) lo = mid; else hi = mid - 1; }
    return lo;
  };
  const isCommentLine = (idx) => /^\s*(\/\/|\*|\/\*)/.test(lines[lineOf(idx)]);

  const out = [];
  let cursor = 0;
  while (true) {
    const open = jsSource.indexOf('<style>', cursor);
    if (open === -1) break;
    if (isCommentLine(open)) { cursor = open + 7; continue; }
    const close = jsSource.indexOf('</style>', open);
    if (close === -1) break;
    out.push(jsSource.slice(open + 7, close));
    cursor = close + 8;
  }
  return out;
}

function loadSources() {
  const jsSource = fs.readFileSync(RETAIL_JS, 'utf8');
  const html = fs.readFileSync(INDEX_HTML, 'utf8');
  const htmlStyle = [...html.matchAll(/<style>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n');

  const sources = [
    { label: 'css/main.css', css: fs.readFileSync(MAIN_CSS, 'utf8') },
    { label: 'css/rtl.css', css: fs.readFileSync(RTL_CSS, 'utf8') },
    { label: 'index.html <style>', css: htmlStyle },
    { label: 'subsystem-retail.js _injectStyles()', css: readInjectedStylesheet(jsSource) },
  ];
  readScreenStylesheets(jsSource).forEach((css, i) => {
    sources.push({ label: `subsystem-retail.js screen <style> #${i + 1}`, css });
  });
  return sources;
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. CASCADE
// ─────────────────────────────────────────────────────────────────────────────
//
// retail_surface_domlite.js matches on the RIGHTMOST COMPOUND ONLY, and says so
// in its own header. That approximation is sound for the structural questions
// it was written for ("is the total the largest money element"), because it
// errs toward matching MORE rules. It is NOT sound here: `.rdash .ret-table`
// and `.ret-table` disagree about colour, and picking the wrong one is the
// whole bug. So descendant matching and specificity are modelled properly.

const STATE_PSEUDOS = /^:(hover|focus|focus-visible|focus-within|active|disabled|checked|target|visited|indeterminate|placeholder-shown|autofill)\b/;

function parseCompound(text) {
  const c = { tag: null, id: null, classes: [], attrs: [], states: [], structural: [], pseudoElement: null, universal: false };
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (ch === '*') { c.universal = true; i++; continue; }
    if (ch === '#') {
      const m = /^#([-\w]+)/.exec(text.slice(i));
      if (!m) return null;
      c.id = m[1]; i += m[0].length; continue;
    }
    if (ch === '.') {
      const m = /^\.([-\w]+)/.exec(text.slice(i));
      if (!m) return null;
      c.classes.push(m[1]); i += m[0].length; continue;
    }
    if (ch === '[') {
      const end = text.indexOf(']', i);
      if (end === -1) return null;
      c.attrs.push(text.slice(i + 1, end)); i = end + 1; continue;
    }
    if (ch === ':') {
      if (text[i + 1] === ':') {
        const m = /^::([-\w]+)/.exec(text.slice(i));
        if (!m) return null;
        c.pseudoElement = m[1]; i += m[0].length; continue;
      }
      // functional pseudo-class -- take the balanced parens with it
      const m = /^:([-\w]+)(\()?/.exec(text.slice(i));
      if (!m) return null;
      let chunk = ':' + m[1];
      let j = i + m[0].length;
      if (m[2]) {
        let depth = 1;
        while (j < text.length && depth > 0) {
          if (text[j] === '(') depth++;
          else if (text[j] === ')') depth--;
          j++;
        }
        chunk = text.slice(i, j);
      }
      if (STATE_PSEUDOS.test(chunk)) c.states.push(chunk);
      else if (/^::/.test(chunk)) c.pseudoElement = chunk;
      else if (/^:(before|after|first-line|first-letter|placeholder|selection|marker)\b/.test(chunk)) c.pseudoElement = chunk;
      else c.structural.push(chunk);
      i = j > i ? j : i + chunk.length;
      continue;
    }
    const m = /^[-\w]+/.exec(text.slice(i));
    if (!m) return null;
    c.tag = m[0].toLowerCase(); i += m[0].length; continue;
  }
  return c;
}

/** `.a > .b .c` -> [{combinator:null,compound}, {combinator:'>' ,...}, ...] */
function parseSelector(selector) {
  const parts = [];
  const tokens = selector.trim().split(/\s*([>+~])\s*|\s+/).filter((t) => t !== undefined && t !== '');
  let combinator = null;
  for (const tok of tokens) {
    if (tok === '>' || tok === '+' || tok === '~') { combinator = tok; continue; }
    const compound = parseCompound(tok);
    if (!compound) return null;              // unparseable -> caller must not guess
    parts.push({ combinator, compound });
    combinator = ' ';
  }
  return parts.length ? parts : null;
}

function specificityOf(parts) {
  let a = 0, b = 0, c = 0;
  for (const { compound } of parts) {
    if (compound.id) a++;
    b += compound.classes.length + compound.attrs.length + compound.states.length + compound.structural.length;
    if (compound.tag) c++;
    if (compound.pseudoElement) c++;
  }
  return a * 10000 + b * 100 + c;
}

function elementSiblingIndex(el) {
  if (!el.parent) return { index: 0, count: 1 };
  const sibs = (el.parent.children || []).filter((n) => n.type === 'element');
  return { index: sibs.indexOf(el), count: sibs.length };
}

function compoundMatches(compound, el, activeStates) {
  if (compound.pseudoElement) return false;         // no element to paint
  if (compound.tag && compound.tag !== el.tag) return false;
  if (compound.id && el.attrs.id !== compound.id) return false;
  for (const cls of compound.classes) if (!el.classes.includes(cls)) return false;
  for (const raw of compound.attrs) {
    const m = /^([-\w]+)\s*(?:([~^$*|]?=)\s*["']?([^"']*)["']?)?$/.exec(raw.trim());
    if (!m) return false;
    const have = el.attrs[m[1].toLowerCase()];
    if (have === undefined) return false;
    if (m[2] && m[2] === '=' && have !== m[3]) return false;
  }
  for (const st of compound.states) {
    const name = /^:([-\w]+)/.exec(st)[1];
    if (!activeStates.has(name)) return false;
  }
  for (const st of compound.structural) {
    const { index, count } = elementSiblingIndex(el);
    if (/^:first-child\b/.test(st)) { if (index !== 0) return false; continue; }
    if (/^:last-child\b/.test(st)) { if (index !== count - 1) return false; continue; }
    if (/^:only-child\b/.test(st)) { if (count !== 1) return false; continue; }
    if (/^:not\(/.test(st)) {
      const inner = st.slice(5, -1);
      const innerParts = parseSelector(inner);
      if (!innerParts) return false;
      if (matchesSelectorParts(innerParts, el, activeStates)) return false;
      continue;
    }
    if (/^:root\b/.test(st)) return false;          // no <html> in a fragment corpus
    return false;                                    // unmodelled -> never match
  }
  return true;
}

function matchesSelectorParts(parts, el, activeStates) {
  const last = parts[parts.length - 1];
  if (!compoundMatches(last.compound, el, activeStates)) return false;
  let node = el;
  for (let i = parts.length - 2; i >= 0; i--) {
    const { compound } = parts[i];
    const combinator = parts[i + 1].combinator;
    if (combinator === '>') {
      node = node.parent;
      if (!node || node.type !== 'element') return false;
      if (!compoundMatches(compound, node, activeStates)) return false;
    } else if (combinator === '+' || combinator === '~') {
      // `+` looks at the immediately preceding sibling only; `~` walks back
      // through all of them. The POS uses `:focus-within + .pos-scan-lamp`, so
      // this is a real path, not defensive code.
      const sibs = node.parent ? (node.parent.children || []).filter((n) => n.type === 'element') : [];
      const stop = combinator === '+' ? sibs.indexOf(node) - 1 : 0;
      let found = null;
      for (let j = sibs.indexOf(node) - 1; j >= stop; j--) {
        if (sibs[j] && compoundMatches(compound, sibs[j], activeStates)) { found = sibs[j]; break; }
      }
      if (!found) return false;
      node = found;
    } else {
      let anc = node.parent;
      let found = null;
      while (anc && anc.type === 'element') {
        if (compoundMatches(compound, anc, activeStates)) { found = anc; break; }
        anc = anc.parent;
      }
      if (!found) return false;
      node = found;
    }
  }
  return true;
}

/**
 * Every rule in every source, flattened once, with its parsed selectors and
 * specificity. `order` is the cascade position: source index first, then
 * position within the source, which is exactly how the browser breaks a
 * specificity tie.
 */
function buildRuleTable(sources) {
  const rules = [];
  const mediaColour = [];
  const unparseable = [];
  sources.forEach((src, sourceIndex) => {
    dom.parseCss(src.css).forEach((rule, ruleIndex) => {
      const setsPaint = 'color' in rule.decls || 'background' in rule.decls || 'background-color' in rule.decls;
      if (rule.at) {
        if (setsPaint) {
          mediaColour.push({ at: rule.at, selectors: rule.selectors, decls: rule.decls, source: src.label });
        }
        return;   // conditional -- see the header note; counted, never folded in
      }
      for (const sel of rule.selectors) {
        const parts = parseSelector(sel);
        if (!parts) {
          if (setsPaint) unparseable.push({ selector: sel, source: src.label });
          continue;
        }
        rules.push({
          selector: sel,
          parts,
          decls: rule.decls,
          specificity: specificityOf(parts),
          order: sourceIndex * 1e6 + ruleIndex,
          source: src.label,
        });
      }
    });
  });
  return { rules, mediaColour, unparseable };
}

/**
 * The winning declaration for `prop` on `el`, or null. Inline `style` beats
 * every stylesheet rule, which is how the recent-transactions rows get their
 * per-cell colours.
 */
/**
 * Cascade origin tiers, highest first. main.css carries 31 `!important` colour
 * and background declarations, so ignoring the flag would let this resolver
 * report a colour the browser never paints -- the exact class of wrong answer
 * that makes an accessibility test worse than none.
 *   3  inline style, !important
 *   2  stylesheet,   !important
 *   1  inline style
 *   0  stylesheet
 * Within a tier: specificity, then document order. Cascade layers and
 * animations are not modelled because this codebase uses neither.
 */
function splitImportant(rawValue) {
  const v = String(rawValue).trim();
  const m = /^([\s\S]*?)\s*!\s*important\s*$/i.exec(v);
  return m ? { value: m[1].trim(), important: true } : { value: v, important: false };
}

function winningDeclaration(ruleTable, el, prop, activeStates) {
  const states = activeStates || new Set();
  let best = null;
  const better = (tier, specificity, order) => !best
    || tier > best.tier
    || (tier === best.tier && specificity > best.specificity)
    || (tier === best.tier && specificity === best.specificity && order > best.order);

  for (const rule of ruleTable.rules) {
    if (!(prop in rule.decls)) continue;
    const { value, important } = splitImportant(rule.decls[prop]);
    const tier = important ? 2 : 0;
    if (!better(tier, rule.specificity, rule.order)) continue;
    if (!matchesSelectorParts(rule.parts, el, states)) continue;
    best = { value, tier, specificity: rule.specificity, order: rule.order, from: `${rule.selector}  [${rule.source}]` };
  }

  const inline = el.attrs && el.attrs.style;
  if (inline) {
    const m = new RegExp(`(?:^|;)\\s*${prop}\\s*:\\s*([^;]+)`, 'i').exec(inline);
    if (m) {
      const { value, important } = splitImportant(m[1]);
      const tier = important ? 3 : 1;
      if (better(tier, Infinity, Infinity)) {
        best = { value, tier, specificity: Infinity, order: Infinity, from: 'inline style attribute' };
      }
    }
  }
  return best;
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. COLOUR
// ─────────────────────────────────────────────────────────────────────────────

function tokenMap(sources) {
  // :root and html[data-theme="light"] are declared identically on purpose (see
  // main.css) -- both are read, later definitions winning, which is the cascade.
  const tokens = Object.create(null);
  for (const src of sources) {
    for (const rule of dom.parseCss(src.css)) {
      if (!rule.selectors.some((s) => /(^|,|\s):root\b/.test(s) || /^html\[data-theme=["']?light/.test(s.trim()))) continue;
      for (const [k, v] of Object.entries(rule.decls)) if (k.startsWith('--')) tokens[k] = v;
    }
  }
  return tokens;
}

function resolveVar(value, tokens, depth) {
  if (value == null) return null;
  if ((depth || 0) > 12) return null;
  // Callers that read a rule's raw declaration (rather than going through
  // winningDeclaration) still hand the `!important` flag over; it changes the
  // cascade, never the colour.
  const v = splitImportant(value).value;
  const m = /^var\(\s*(--[-\w]+)\s*(?:,([\s\S]+))?\)$/.exec(v);
  if (!m) return v;
  if (tokens[m[1]] !== undefined) return resolveVar(tokens[m[1]], tokens, (depth || 0) + 1);
  if (m[2] !== undefined) return resolveVar(m[2], tokens, (depth || 0) + 1);
  return null;                                   // token genuinely absent
}

function parseColour(value, tokens) {
  const s = resolveVar(value, tokens);
  if (!s) return null;
  const t = s.trim();
  if (/^(transparent|none)$/i.test(t)) return { r: 0, g: 0, b: 0, a: 0 };
  let m = /^#([0-9a-fA-F]{3,8})$/.exec(t);
  if (m) {
    let h = m[1];
    if (h.length === 3 || h.length === 4) h = h.split('').map((c) => c + c).join('');
    if (h.length !== 6 && h.length !== 8) return null;
    return {
      r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16),
      a: h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1,
    };
  }
  m = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+%?)\s*)?\)$/.exec(t);
  if (m) {
    const a = m[4] === undefined ? 1 : (m[4].endsWith('%') ? parseFloat(m[4]) / 100 : parseFloat(m[4]));
    return { r: +m[1], g: +m[2], b: +m[3], a };
  }
  const NAMED = { white: '#ffffff', black: '#000000' };
  if (NAMED[t.toLowerCase()]) return parseColour(NAMED[t.toLowerCase()], tokens);
  return null;
}

/**
 * The colour inside a `background` shorthand, or null when the shorthand paints
 * something that has no single colour (a gradient, an image). Returning null
 * rather than a guess is what makes those land in the UNRESOLVED bucket instead
 * of being silently averaged into a pass.
 */
function backgroundColourOf(value, tokens) {
  const raw = splitImportant(value).value;
  if (/gradient|url\(/i.test(raw)) return { gradient: true };
  const direct = parseColour(raw, tokens);
  if (direct) return direct;
  for (const part of raw.split(/\s+(?![^(]*\))/)) {
    const c = parseColour(part, tokens);
    if (c) return c;
  }
  return null;
}

function composite(top, bottom) {
  const a = top.a;
  return {
    r: top.r * a + bottom.r * (1 - a),
    g: top.g * a + bottom.g * (1 - a),
    b: top.b * a + bottom.b * (1 - a),
    a: 1,
  };
}

function relativeLuminance(c) {
  const f = (v) => { const s = v / 255; return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4); };
  return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
}

function contrastRatio(fg, bg) {
  const la = relativeLuminance(fg);
  const lb = relativeLuminance(bg);
  const hi = Math.max(la, lb);
  const lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * The opaque surface an element's text is drawn on: its own background stacked
 * over its ancestors', composited downward, stopping at the first opaque layer.
 * Returns {colour, layers} or {unresolved: reason}.
 *
 * The walk terminates at <body>, whose background index.html sets from
 * --app-bg. That is a real declaration in a real file, not a fixture default,
 * which is the only reason this can claim to resolve rather than to assume.
 */
function effectiveBackground(ruleTable, el, tokens, activeStates) {
  const layers = [];
  let node = el;
  while (node && node.type === 'element') {
    const decl = winningDeclaration(ruleTable, node, 'background-color', activeStates)
      || winningDeclaration(ruleTable, node, 'background', activeStates);
    if (decl) {
      const c = backgroundColourOf(decl.value, tokens);
      if (c && c.gradient) {
        return { unresolved: `background is a gradient/image (${decl.from}) — no single surface luminance to test against` };
      }
      if (!c) {
        return { unresolved: `background "${decl.value}" (${decl.from}) does not resolve to a colour` };
      }
      if (c.a > 0) {
        layers.push({ colour: c, from: decl.from });
        if (c.a >= 1) {
          let out = layers[layers.length - 1].colour;
          for (let i = layers.length - 2; i >= 0; i--) out = composite(layers[i].colour, out);
          return { colour: out, layers };
        }
      }
    }
    node = node.parent;
  }
  return { unresolved: 'no opaque background anywhere up to the document root' };
}

/**
 * The colour an element's own text is painted in. `inherit`/`currentColor`
 * walk up, exactly as the cascade does.
 */
function effectiveColour(ruleTable, el, tokens, activeStates) {
  let node = el;
  let hops = 0;
  while (node && node.type === 'element' && hops < 64) {
    const decl = winningDeclaration(ruleTable, node, 'color', activeStates);
    if (decl) {
      const v = String(decl.value).trim();
      if (/^(inherit|currentcolor|unset|initial)$/i.test(v)) { node = node.parent; hops++; continue; }
      const c = parseColour(v, tokens);
      if (!c) return { unresolved: `color "${decl.value}" (${decl.from}) does not resolve to a colour` };
      return { colour: c, from: decl.from, declaredOn: node };
    }
    node = node.parent;
    hops++;
  }
  return { unresolved: 'no color declaration anywhere up to the document root' };
}

// ─────────────────────────────────────────────────────────────────────────────
// 3b. OPACITY — GROUP COMPOSITING
// ─────────────────────────────────────────────────────────────────────────────
//
// `opacity: .45` does NOT tint an element's text. It renders the element and
// its entire subtree — background, text, borders, outline — into an offscreen
// buffer, and composites that buffer over the backdrop at 45%. Two consequences
// decide how this has to be modelled, and both are why treating opacity as "a
// nearly-transparent text colour" would report the wrong number:
//
//   1. It moves the FOREGROUND *and* the BACKGROUND toward the same backdrop.
//      A white-on-dark tile at 45% is not white on a lighter dark; it is a
//      washed foreground on a washed background, and the ratio between them
//      collapses far faster than either one alone suggests.
//   2. It is INHERITED-BY-CONTAINMENT, not by the cascade. `opacity` is not an
//      inherited property, so `winningDeclaration(child,'opacity')` correctly
//      returns nothing for the tile's <span>s — and yet every one of them is
//      painted at 45%, because they are inside the group. Walking ancestors is
//      not an approximation here, it is the actual mechanism.
//
// WHAT STAYS APPROXIMATE, so no caller reads silence as proof:
//   * Nested groups are collapsed to the PRODUCT of their alphas over the
//     outermost group's backdrop. That is exact whenever the backgrounds
//     involved lie inside the outermost group (the case in this product) and a
//     close bound otherwise — and when a group paints no background of its own,
//     the backdrop and the element's own resolved surface are the same colour,
//     so the arithmetic reduces to an identity rather than to a guess.
//   * Values that do not resolve to a number (a var() with no definition, a
//     calc()) are returned as UNRESOLVED rather than defaulted to 1. Defaulting
//     to 1 is precisely the silence this file exists to remove.
//   * `opacity: 0` is a real, visible-to-nobody state, but it is also how this
//     codebase writes the START of a CSS animation (`opacity:0; animation:...
//     forwards`). A zero that is paired with an animation on the same element
//     is therefore reported as ANIMATED rather than measured, because measuring
//     the first frame of a reveal would report a 1.00:1 failure on text that is
//     fully opaque a third of a second later.

function opacityValue(ruleTable, el, tokens, activeStates) {
  const decl = winningDeclaration(ruleTable, el, 'opacity', activeStates);
  if (!decl) return null;
  const raw = resolveVar(decl.value, tokens);
  if (raw === null) return { unresolved: `opacity "${decl.value}" (${decl.from}) does not resolve`, from: decl.from };
  const text = String(raw).trim();
  const m = /^(\d*\.?\d+)(%?)$/.exec(text);
  if (!m) return { unresolved: `opacity "${text}" (${decl.from}) is not a plain number`, from: decl.from };
  const value = m[2] === '%' ? parseFloat(m[1]) / 100 : parseFloat(m[1]);
  if (!isFinite(value)) return { unresolved: `opacity "${text}" (${decl.from}) is not finite`, from: decl.from };
  const animated = !!winningDeclaration(ruleTable, el, 'animation', activeStates);
  return { value: Math.max(0, Math.min(1, value)), from: decl.from, animated };
}

/**
 * Every opacity group `el` is painted inside, innermost first. An element that
 * declares its own opacity is the innermost group of its own subtree, so the
 * walk starts AT `el`, not at its parent.
 */
function opacityGroups(ruleTable, el, tokens, activeStates) {
  const groups = [];
  for (let node = el; node && node.type === 'element'; node = node.parent) {
    const op = opacityValue(ruleTable, node, tokens, activeStates);
    if (!op) continue;
    if (op.unresolved) return { unresolved: op.unresolved };
    if (op.value >= 1) continue;                  // fully opaque group: no effect
    if (op.value === 0 && op.animated) continue;  // animation start frame — see the note above
    groups.push({ el: node, alpha: op.value, from: op.from });
  }
  return { groups };
}

/**
 * Push a resolved (colour, surface) pair through every opacity group that
 * contains the element, and return what the compositor actually puts on the
 * glass. Returns {colour, surface, alpha, groups} or {unresolved}.
 *
 * `colour` must already be the PAINTED foreground — i.e. a translucent text
 * colour composited over its own surface by the caller — because alpha on the
 * colour channel and alpha on the group are two different operations applied in
 * that order.
 */
function paintThroughOpacity(ruleTable, el, tokens, activeStates, colour, surface) {
  const found = opacityGroups(ruleTable, el, tokens, activeStates);
  if (found.unresolved) return { unresolved: found.unresolved };
  const groups = found.groups;
  if (!groups.length) return { colour, surface, alpha: 1, groups };

  const alpha = groups.reduce((a, g) => a * g.alpha, 1);
  const outermost = groups[groups.length - 1].el;
  // What the group is composited ONTO: the surface behind the outermost group,
  // which is its parent's resolved background. A group at the document root has
  // nothing behind it, and that is a corpus failure, not a colour to invent.
  if (!outermost.parent || outermost.parent.type !== 'element') {
    return { unresolved: `opacity group ${groups[groups.length - 1].from} has no ancestor to composite onto` };
  }
  const behind = effectiveBackground(ruleTable, outermost.parent, tokens, activeStates);
  if (behind.unresolved) {
    return { unresolved: `opacity group ${groups[groups.length - 1].from}: ${behind.unresolved}` };
  }
  const over = (c) => composite({ r: c.r, g: c.g, b: c.b, a: alpha }, behind.colour);
  return {
    colour: over(colour),
    surface: over(surface),
    alpha,
    groups,
    backdrop: behind.colour,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. THE CORPUS
// ─────────────────────────────────────────────────────────────────────────────

const STATS = {
  today_sales: 1240.50, today_transactions: 18, today_returns: 65.44,
  month_sales: 20100.00, month_transactions: 210, low_stock_alerts: 3,
  total_customers: 88, total_products: 312, sales_change_pct: 12,
  hourly_labels: ['09:00'], hourly_data: [120],
  payment_methods: { cash: 4, card: 2 },
  recent_sales: [
    { id: 1, sale_number: 'S-1041', customer_name: 'Walk-in', item_count: 3, payment_method: 'cash', total: 42.50, created_at: '2026-08-21T18:42:00' },
    { id: 2, sale_number: 'S-1042', customer_name: 'Ann Q', item_count: 1, payment_method: 'card', total: -12.00, created_at: '2026-08-21T19:02:00' },
  ],
};

const PRODUCTS = [
  { id: 'p1', name: 'Coffee beans 250g', price: 12.50, total_stock: 20, reorder_level: 5, category_id: 'c1' },
  { id: 'p2', name: 'Milk 1L', price: 3.20, total_stock: 2, reorder_level: 5, category_id: 'c1' },
  { id: 'p3', name: 'Sold out item', price: 9.90, total_stock: 0, reorder_level: 5, category_id: 'c1' },
];

/* ── The table screens' server data ─────────────────────────────────────────
   One row per list, because one row is enough to render every cell type and a
   second would only multiply the same pairings. Every value is the SHAPE the
   backend actually writes, not a convenient one:

     * `created_at` is the SPACE form (`retail_api.py::create_sale` stores
       `%Y-%m-%d %H:%M:%S`), never an ISO 'T'. The 'T' is a strong LTR
       character; a fixture carrying one silently anchors the whole run and
       makes the Date column's bidi hazard unreproducible in test while it is
       live on every Arabic install. Same reasoning, same fixture shape, as
       retail_surface_i18n_test.js — kept honest in both files so neither drifts.
     * A supplier/customer/product id is a client-generated UUID string, not an
       autoincrement int, because that is what the sync migration made them.
     * `total_stock: 0` on one product, so the out-of-stock row renders. */
const SALES_ROWS = [
  { id: 1, sale_number: 'S-1041', customer_name: 'Walk-in', item_count: 3, items: 3,
    payment_method: 'cash', total: 42.50, status: 'completed', created_at: '2026-08-21 18:42:00' },
  { id: 2, sale_number: 'S-1042', customer_name: 'Ann Q', item_count: 1, items: 1,
    payment_method: 'card', total: -12.00, status: 'voided', created_at: '2026-08-21 19:02:00' },
];

const SALE_DETAIL = {
  sale: Object.assign({}, SALES_ROWS[0], {
    subtotal: 38.00, tax_amount: 4.50, discount_amount: 0, amount_paid: 50, change_amount: 7.50,
    cashier: null, actor_user_uid: null, terminal_id: null, notes: '',
  }),
  items: [{ product_id: 'p1', product_name: 'Coffee beans 250g', sku: 'CB250', quantity: 2,
    unit_price: 12.50, discount_pct: 0, tax_rate: 16, line_total: 29.00 }],
};

const TABLE_DATA = {
  returns: [{ id: 9, return_number: 'R-0007', sale_number: 'S-1041', customer_name: 'Walk-in',
    refund_method: 'cash', refund_amount: 12.25, created_at: '2026-08-22 09:05:00' }],
  purchaseOrders: [{ id: 4, po_number: 'PO-0031', supplier_name: 'Acme Trading', status: 'pending',
    total: 430.75, ordered_at: '2026-08-18', received_at: null }],
  products: [
    { id: 'p1', name: 'Coffee beans 250g', sku: 'CB250', barcode: '1110001', cost_price: 8.00,
      sell_price: 12.50, total_stock: 20, reorder_level: 5, unit: 'bag', category_name: 'Beverages' },
    { id: 'p3', name: 'Sold out item', sku: 'SO1', barcode: '1110003', cost_price: 6.00,
      sell_price: 9.90, total_stock: 0, reorder_level: 5, unit: 'pack', category_name: 'Beverages' },
  ],
  categories: [{ id: 'c1', name: 'Beverages' }],
  suppliers: [{ id: 's1', name: 'Acme Trading', phone: '0790000000', email: 'ops@acme.example',
    address: 'Amman', order_count: 4 }],
  customers: [{ id: 'cu1', name: 'Ann Q', phone: '0791111111', email: 'ann@example.co',
    loyalty_points: 120, total_spent: 512.25, order_count: 7 }],
  heldSales: [{ id: 3, hold_number: 'H-0003', label: 'blue jacket', item_count: 2,
    customer_name: 'Walk-in', total: 31.40, created_at: '2026-08-22 10:15:00' }],
  auditLog: [{ id: 1, timestamp: '2026-08-22 11:00:00', user_id: '6f1c2d34-aa11-4b22-9c33-7d44e55f6677',
    action: 'create', entity: 'sale', entity_id: 1, details: 'Sale S-1041 created' }],

  /* Sales-by-employee, for the Reports screen. TWO rows, and the second one is
     not padding: `_attributionCell()` renders three visibly different answers
     and they do not share a colour. A resolved person is ordinary body text; the
     unattributed bucket is an inline `color:var(--text-faint)` span. One row
     would put exactly one of those in the corpus, and the faint one is the one
     worth measuring — a pre-v13 sale reports "Not recorded" on every install
     that has any history at all.

     Field NAMES matter here more than values: _resolutionAttempted() keys off
     key PRESENCE, so a row carrying `employee_name: undefined` claims the server
     looked and found nothing (state 'account_gone'), which is a different cell
     from a row that simply omits the key. Both spellings below are deliberate. */
  employeeSales: [
    { actor_user_uid: '6f1c2d34-aa11-4b22-9c33-7d44e55f6677', employee_name: 'Layla H',
      employee_id: 'EMP-0007', transactions: 12, revenue: 618.40, avg_ticket: 51.53 },
    { actor_user_uid: null, transactions: 6, revenue: 141.10, avg_ticket: 23.52 },
  ],
};

/* The Reports KPI tiles. Read off `summary.data` by _loadReports(); without it
   all four render the "—" em-dash placeholder the frame ships with, which is a
   screen that looks populated and carries no money at all. `margin_pct` is
   included because the Gross Profit tile interpolates it into the same cell. */
const REPORT_SUMMARY = {
  revenue: 20100.00, transactions: 210, gross_profit: 7412.55,
  margin_pct: 36.9, avg_ticket: 95.71,
};

/* GET /inventory/reconciliation, for the Stock accuracy screen.
 *
 * THE FIXTURE HAS DRIFT ON PURPOSE, and it is the branch of that screen with
 * the most to look at: a `.ret-kpi-grid` of headline figures, a `.ret-table`
 * of real rows, and the repair offer. A response with `drift_count: 0` would
 * render the clean panel — one card, four elements, no table at all — and
 * `testCorpusRendersRealScreens`'s empty-state check would then be the only
 * thing between this corpus and a screen it looks at without seeing.
 *
 * The three rows are three DIFFERENT cells, not three of the same one:
 *   * a positive drift (the balance claims MORE than the ledger accounts for
 *     — the double-received-PO signature the screen exists to name);
 *   * a negative drift, so both sign glyphs are rendered elements;
 *   * a NULL branch_id with `repairable: false`, which is the legacy
 *     movement with nowhere to put a balance. It renders the yellow
 *     "Needs a branch" badge and drives the stranded-rows card, so both are
 *     real elements in the corpus rather than dead branches.
 *
 * `pairs_examined` is deliberately much larger than `drift_count`: it is the
 * number that lets the screen say a check RAN, and a fixture where the two
 * were equal would make the two figures indistinguishable in the render.
 */
const STOCK_DRIFT = {
  drift_count: 3,
  net_drift: 5,               // 7 - 2.5 + 0.5
  pairs_examined: 512,
  repair_confirmation: 'RECONCILE-11111111-2222-3333-4444-555555555555',
  rows: [
    { product_id: 'p1', product_name: 'Coffee beans 250g', sku: 'CB250', branch_id: 1,
      branch_name: 'Main Branch', stored_balance: 24, ledger_balance: 17, drift: 7, repairable: true },
    { product_id: 'p3', product_name: 'Sold out item', sku: 'SO1', branch_id: 2,
      branch_name: 'Airport Kiosk', stored_balance: 0, ledger_balance: 2.5, drift: -2.5, repairable: true },
    { product_id: 'p9', product_name: null, sku: null, branch_id: null,
      branch_name: null, stored_balance: 0, ledger_balance: -0.5, drift: 0.5, repairable: false },
  ],
};

/**
 * The response for a URL. Routed, because the screens below fan out across nine
 * endpoints and a single blanket payload would render every table as its EMPTY
 * state — which is the one state with no badges, no money and no dates in it,
 * i.e. exactly the rows this corpus exists to look at.
 */
function apiResponseFor(url) {
  const u = String(url);
  const ok = (data, meta) => ({ status: 'success', data, meta });
  // The /reports/* family first: `top-products` would otherwise be swallowed by
  // the /products rule below and answer with the product LIST, and
  // `by-employee` would fall through to the catch-all and hand _loadEmployeeSales
  // an object where it expects an array -- which it correctly renders as
  // "No sales in this period.", i.e. the empty state, i.e. a Reports screen in
  // the corpus with no rows in it. That is the exact shape of miss
  // testCorpusRendersRealScreens exists to refuse.
  if (/\/reports\/by-employee/.test(u)) return ok(TABLE_DATA.employeeSales);
  if (/\/reports\/summary/.test(u)) return ok(REPORT_SUMMARY);
  // Before the /products rule below for the same reason `top-products` is:
  // this URL contains no "products", but it WOULD fall through to the
  // catch-all, and STATS carries no `rows`/`pairs_examined` — which the Stock
  // accuracy screen correctly reads as "nothing was compared" and renders as
  // its no-records panel. A screen in the corpus showing its empty state is
  // exactly the miss testCorpusRendersRealScreens exists to refuse.
  if (/\/inventory\/reconciliation/.test(u)) return ok(STOCK_DRIFT);
  if (/\/customers\/[^/?]+\/sales/.test(u)) return ok(SALES_ROWS);
  if (/\/sales\/recent/.test(u)) return ok(SALES_ROWS);
  if (/\/sales\/\d+/.test(u)) return ok(SALE_DETAIL);
  if (/\/audit-log/.test(u)) return ok(TABLE_DATA.auditLog, { total: 1, page: 1, limit: 50, actions: ['create'], entities: ['sale'] });
  if (/\/held-sales/.test(u)) return ok(TABLE_DATA.heldSales);
  if (/\/purchase-orders/.test(u)) return ok(TABLE_DATA.purchaseOrders);
  if (/\/returns/.test(u)) return ok(TABLE_DATA.returns);
  if (/\/products/.test(u)) return ok(TABLE_DATA.products);
  if (/\/categories/.test(u)) return ok(TABLE_DATA.categories);
  if (/\/suppliers/.test(u)) return ok(TABLE_DATA.suppliers);
  if (/\/customers/.test(u)) return ok(TABLE_DATA.customers);
  if (/dashboard\/stats/.test(u)) return ok(STATS);
  return ok(STATS);
}

function makeStub(over) {
  const el = Object.assign({
    innerHTML: '', outerHTML: '', textContent: '', value: '', id: '', disabled: false, style: {},
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {},
    querySelectorAll() { return []; }, addEventListener() {}, removeEventListener() {},
    focus() {}, blur() {}, remove() {}, closest() { return null; },
    getContext() { return {}; },
  }, over || {});
  return el;
}

/** Load the real subsystem-retail.js. Never a reimplementation. */
function loadRetailSystem(capabilities) {
  const code = fs.readFileSync(RETAIL_JS, 'utf8');
  const els = Object.create(null);
  const chartHosts = Object.create(null);
  const injected = { css: '' };
  const namedQueries = Object.create(null);
  const overlays = [];

  const getEl = (id) => {
    if (!els[id]) {
      const el = makeStub({ id });
      if (id === 'r-dash-hourly' || id === 'r-dash-pay') {
        chartHosts[id] = makeStub({ id: id + '::host' });
        el.parentElement = chartHosts[id];
      }
      els[id] = el;
    }
    return els[id];
  };

  const sandbox = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    t: (s) => s,
    fetch: (url) => Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve(apiResponseFor(url)),
    }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem() {} },
    // URLSearchParams is a HOST global, not a JS intrinsic, so a fresh vm
    // context does not have one — and _loadSalesHistory / _loadAuditLog both
    // build their query strings with it. Without this the two screens throw
    // inside their own try/catch and render as their error state, which is a
    // corpus that looks full and contains no rows.
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    URLSearchParams,
    document: {
      activeElement: null,
      getElementById(id) { if (id === 'ret-styles') return null; return getEl(id); },
      createElement() { return makeStub(); },
      querySelector(sel) {
        if (!namedQueries[sel]) namedQueries[sel] = makeStub({ id: '::query::' + sel });
        return namedQueries[sel];
      },
      querySelectorAll() { return []; },
      head: { appendChild(node) { if (node && node.textContent) injected.css += '\n' + node.textContent; } },
      // Modals are appended to <body>, not into #sub-content. Recording them is
      // what lets a modal be rendered as its own corpus screen rather than
      // vanishing — and the dark `.ret-modal` island was, for two rounds, the
      // single largest population of colour rules nothing exercised.
      body: { appendChild(node) { if (node) overlays.push(node); } },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
  };
  sandbox.SubsystemApp = {
    active: 'retail', showToast() {}, _navigate() {},
    hasCapability: (code) => (capabilities || []).includes(code),
  };
  sandbox.window = sandbox;
  sandbox.Chart = function ChartStub() { return { destroy() {} }; };
  sandbox.Chart.getChart = () => null;

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: RETAIL_JS });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return { RetailSystem: sandbox.RetailSystem, els, chartHosts, injected, namedQueries, overlays };
}

/**
 * The real shell chain. Every screen is rendered into <main class="sub-content"
 * id="sub-content">, which app-shell.js's _renderShell() builds inside
 * .sub-shell > .sub-main. That chain is what supplies the background under
 * anything the screen leaves transparent, so reconstructing it is not
 * decoration -- omitting it would make every transparent element unresolvable.
 */
const SHELL_OPEN = '<body><div class="sub-shell"><div class="sub-main"><main class="sub-content" id="sub-content">';
const SHELL_CLOSE_INNER = '</main></div></div>';
const BODY_CLOSE = '</body>';

function spliceDeferredWrites(root, ctx) {
  // Screens render their frame synchronously and then fill regions by id
  // (`getElementById(...).innerHTML = ...`). Those regions carry the badges,
  // the money breakdown and the cart -- i.e. most of what this corpus exists
  // to look at -- so they are put back exactly where the code put them.
  let spliced = 0;
  const byId = new Map();
  dom.walkElements(root, (el) => { if (el.attrs.id) byId.set(el.attrs.id, el); });

  const attach = (host, html) => {
    if (!host || !html) return false;
    const frag = dom.parseFragment(html);
    for (const child of frag.children) { child.parent = host; }
    host.children = frag.children;
    spliced++;
    return true;
  };

  // `el.outerHTML = ...` REPLACES the placeholder rather than filling it. The
  // customer modal's purchase-history table arrives this way and only this way
  // (_viewCustomer swaps out #cu-hist-loading), so treating outerHTML as if it
  // were innerHTML would nest a <table> inside the "Loading…" div and give
  // every cell in it a surface the browser never puts there.
  const replace = (host, html) => {
    if (!host || !html || !host.parent) return false;
    const frag = dom.parseFragment(html);
    const siblings = host.parent.children || [];
    const at = siblings.indexOf(host);
    if (at === -1) return false;
    for (const child of frag.children) { child.parent = host.parent; }
    siblings.splice(at, 1, ...frag.children);
    spliced++;
    return true;
  };

  for (const [id, stub] of Object.entries(ctx.els)) {
    const host = byId.get(id);
    if (!host) continue;
    if (stub.outerHTML) { replace(host, stub.outerHTML); continue; }
    if (stub.innerHTML) attach(host, stub.innerHTML);
    else if (stub.textContent) {
      host.children = [{ type: 'text', text: stub.textContent, parent: host }];
      spliced++;
    }
  }

  // `#r-dash-recent tbody` is reached by querySelector, not by id.
  for (const [sel, stub] of Object.entries(ctx.namedQueries)) {
    if (!stub.innerHTML) continue;
    const m = /^#([-\w]+)\s+(\w+)$/.exec(sel);
    if (!m) continue;
    const owner = byId.get(m[1]);
    if (!owner) continue;
    let target = null;
    dom.walkElements(owner, (el) => { if (!target && el.tag === m[2]) target = el; });
    if (target) attach(target, stub.innerHTML);
  }

  // A chart's empty state is written to the canvas's PARENT element.
  for (const [canvasId, host] of Object.entries(ctx.chartHosts)) {
    if (!host.innerHTML) continue;
    const canvas = byId.get(canvasId);
    if (canvas && canvas.parent) attach(canvas.parent, host.innerHTML);
  }
  return spliced;
}

/**
 * A modal is appended to <body>, NOT into #sub-content — it is a sibling of the
 * shell, over a scrim, and its own overlay is what supplies the surface its
 * contents sit on. Reconstructing it inside the shell would hand every cell in
 * it the till's white card as a background and report ratios the operator never
 * sees, which is the same class of wrong answer as omitting the shell entirely.
 */
function overlayMarkup(node) {
  const cls = node.className ? ` class="${node.className}"` : '';
  const id = node.id ? ` id="${node.id}"` : '';
  return `<div${cls}${id}>${node.innerHTML || ''}</div>`;
}

function screenFrom(name, ctx, contentHtml, overlayHtml) {
  const root = dom.parseFragment(
    SHELL_OPEN + (contentHtml || '') + SHELL_CLOSE_INNER + (overlayHtml || '') + BODY_CLOSE
  );
  const spliced = spliceDeferredWrites(root, ctx);
  return { name, root, spliced, injectedCss: ctx.injected.css };
}

/** Drain the microtask queue so a render that fires an un-awaited load finishes. */
async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

/**
 * ── WHY THE TABLE SCREENS ARE IN HERE ──────────────────────────────────────
 *
 * They were not, for two rounds, and an adversarial verifier showed exactly
 * what that bought: with only the POS, the cashier landing and the dashboard
 * rendered, `this._bdi(...)` could be deleted from FIVE call sites, the
 * receipt-opener <button> could be replaced with a bare escaped string at BOTH
 * of its non-dashboard call sites, and every suite stayed green — because each
 * of those guards had exactly one element in the corpus to look at, and that
 * element was on the one screen that was rendered.
 *
 * The blind spot was published rather than hidden (see
 * retail_design_contrast_test.js's unexercised-chrome ledger, which named
 * `.ret-search` at 1.00:1 and `.ret-badge-yellow` at 1.31:1 every single run)
 * and its own comment said "rendering the products, customers and PO screens
 * would surface them as failures instead of notes, which is the right next
 * move". This is that move. Expect the pairing count and the unexercised count
 * both to change; that is the point of it.
 */
async function buildCorpus() {
  const screens = [];

  // The manager dashboard: recent-transactions badges, the money breakdown,
  // the delta chip, the ghost buttons.
  {
    const ctx = loadRetailSystem(['retail.reports']);
    const content = makeStub();
    await ctx.RetailSystem._renderDashboard(content);
    screens.push(screenFrom('dashboard', ctx, content.innerHTML));
  }

  // The same entry point with NO reports capability: a cashier's real first
  // screen, and a different set of surfaces.
  {
    const ctx = loadRetailSystem([]);
    const content = makeStub();
    await ctx.RetailSystem._renderDashboard(content);
    screens.push(screenFrom('cashier-landing', ctx, content.innerHTML));
  }

  // The till itself, with a populated cart and a product grid that includes a
  // low-stock and an out-of-stock tile, so those state colours are real
  // elements rather than rules nobody exercises.
  {
    const ctx = loadRetailSystem(['retail.reports']);
    const content = makeStub();
    ctx.RetailSystem._renderPOS(content);
    ctx.RetailSystem._products = PRODUCTS;
    ctx.RetailSystem._cart = [];
    ctx.RetailSystem._addToCart('p1');
    ctx.RetailSystem._addToCart('p1');
    ctx.RetailSystem._addToCart('p2');
    ctx.RetailSystem._renderPOSGrid();
    ctx.RetailSystem._recalc();
    screens.push(screenFrom('pos', ctx, content.innerHTML));
  }

  // ── The list screens. Every one of them is a `.ret-table` on a
  //    `.sub-chart-card`, i.e. the shared chrome this suite could previously
  //    only measure on the single dashboard table that overrides it.
  const listScreens = [
    ['sales-history', (rs, c) => rs._renderSalesHistory(c)],
    ['returns', (rs, c) => rs._renderReturns(c)],
    ['purchase-orders', (rs, c) => rs._renderPurchases(c)],
    ['products', (rs, c) => rs._renderProducts(c)],
    ['customers', (rs, c) => rs._renderCustomers(c)],
    ['suppliers', (rs, c) => rs._renderSuppliers(c)],
    ['audit-log', (rs, c) => rs._renderAuditLog(c)],
    // Added when the corpus was closed against the router (see
    // testTheCorpusIsClosedAgainstTheRouter). Reports is the one that matters
    // most: it shipped white-on-white through a round whose stated purpose was
    // widening this corpus, precisely because nothing compared this list
    // against RetailSystem.render()'s switch.
    ['reports', (rs, c) => rs._renderReports(c)],
    ['categories', (rs, c) => rs._renderCategories(c)],
    ['scanner', (rs, c) => rs._renderScannerSettings(c)],
    // Phase 3's Stock accuracy screen. Renders into #stka-body by id, so the
    // deferred-write splice above is what puts its table back where the code
    // put it — same mechanism as every other screen here.
    ['stock-accuracy', (rs, c) => rs._renderStockAccuracy(c)],
  ];
  for (const [name, render] of listScreens) {
    const ctx = loadRetailSystem(['retail.reports']);
    const content = makeStub();
    await render(ctx.RetailSystem, content);
    await settle();
    screens.push(screenFrom(name, ctx, content.innerHTML));
  }

  // ── The three modals. The dark `.ret-modal` island and everything inside it
  //    (its fields, its <option>s, its wide invoice table) is a large family of
  //    colour rules that NO amount of list-screen rendering reaches, and the
  //    customer modal is one of the two non-dashboard homes of the receipt
  //    opener button.
  {
    const ctx = loadRetailSystem(['retail.reports']);
    const content = makeStub();
    await ctx.RetailSystem._renderCustomers(content);
    await settle();
    await ctx.RetailSystem._viewCustomer('cu1');
    await settle();
    const overlay = ctx.overlays[ctx.overlays.length - 1];
    assert.ok(overlay, 'The customer modal never reached document.body.');
    screens.push(screenFrom('customer-modal', ctx, content.innerHTML, overlayMarkup(overlay)));
  }
  {
    const ctx = loadRetailSystem(['retail.reports']);
    const content = makeStub();
    await ctx.RetailSystem._renderSalesHistory(content);
    await settle();
    await ctx.RetailSystem._viewSale(1);
    await settle();
    const overlay = ctx.overlays[ctx.overlays.length - 1];
    assert.ok(overlay, 'The sale-detail modal never reached document.body.');
    screens.push(screenFrom('sale-modal', ctx, content.innerHTML, overlayMarkup(overlay)));
  }
  {
    const ctx = loadRetailSystem(['retail.reports']);
    const content = makeStub();
    ctx.RetailSystem._renderPOS(content);
    ctx.RetailSystem._openHeldSalesModal();
    await settle();
    const overlay = ctx.overlays[ctx.overlays.length - 1];
    assert.ok(overlay, 'The held-sales modal never reached document.body.');
    screens.push(screenFrom('held-sales-modal', ctx, content.innerHTML, overlayMarkup(overlay)));
  }

  return screens;
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. WHAT CALLERS CONSUME
// ─────────────────────────────────────────────────────────────────────────────

const NON_RENDERING_TAGS = new Set(['style', 'script', 'template', 'head', 'title']);

/** Elements that paint at least one character of their OWN text. */
function textPaintingElements(root) {
  const out = [];
  dom.walkElements(root, (el) => {
    if (NON_RENDERING_TAGS.has(el.tag)) return;
    let inRaw = false;
    for (let p = el.parent; p; p = p.parent) if (p.type === 'element' && NON_RENDERING_TAGS.has(p.tag)) { inRaw = true; break; }
    if (inRaw) return;
    if (!dom.ownText(el)) return;
    out.push(el);
  });
  return out;
}

let CACHE = null;
async function harness() {
  if (CACHE) return CACHE;
  const sources = loadSources();
  const tokens = tokenMap(sources);
  const ruleTable = buildRuleTable(sources);
  const screens = await buildCorpus();
  CACHE = {
    sources, tokens, ruleTable, screens,
    textPaintingElements, effectiveColour, effectiveBackground,
    winningDeclaration, contrastRatio, parseColour, composite,
    resolveVar, backgroundColourOf, matchesSelectorParts, parseSelector, specificityOf,
    opacityValue, opacityGroups, paintThroughOpacity,
    describe: dom.describe, allElements: dom.allElements, ownText: dom.ownText,
    lengthRange: dom.lengthRange,
  };
  return CACHE;
}

// ─────────────────────────────────────────────────────────────────────────────
// 6. ASSERTIONS — the corpus is real
// ─────────────────────────────────────────────────────────────────────────────

function testStylesheetSourcesAreAllPresent(h) {
  // Cascade order is the whole ballgame: #ret-styles is injected into <head>
  // AFTER main.css, which is why its 2018-era literals beat the token layer at
  // equal specificity. Losing a source here would not fail loudly, it would
  // quietly stop the overrides from being modelled.
  const labels = h.sources.map((s) => s.label);
  assert.ok(labels.includes('css/main.css'), 'main.css missing from the cascade');
  assert.ok(
    labels.includes('subsystem-retail.js _injectStyles()'),
    'The runtime-injected #ret-styles stylesheet is missing from the cascade — ' +
    'that is the sheet BOTH reported contrast bugs live in, so a corpus without ' +
    'it proves nothing about them.'
  );
  assert.ok(
    h.sources.filter((s) => /screen <style>/.test(s.label)).length >= 3,
    `Expected at least 3 per-screen <style> blocks in subsystem-retail.js, found ` +
    `${h.sources.filter((s) => /screen <style>/.test(s.label)).length}.`
  );
  const total = h.sources.reduce((n, s) => n + s.css.length, 0);
  assert.ok(total > 150000, `Expected >150KB of CSS across the cascade, read ${total}.`);
  console.log(`PASS: ${h.sources.length} stylesheet sources in cascade order (${(total / 1024).toFixed(0)}KB)`);
}

function testRuleTableParsed(h) {
  const { rules, unparseable } = h.ruleTable;
  assert.ok(rules.length >= 1200, `Expected >=1200 parsed rules, got ${rules.length}. The CSS parse is broken.`);
  const colourRules = rules.filter((r) => 'color' in r.decls).length;
  assert.ok(colourRules >= 200, `Expected >=200 rules that set a colour, got ${colourRules}.`);
  const unparsed = unparseable.map((u) => `${u.selector} [${u.source}]`);
  assert.deepStrictEqual(
    unparsed, [],
    `${unparsed.length} selector(s) that set a colour could not be parsed:\n  ` +
    unparsed.join('\n  ') +
    '\n\nTheir pairings are ' +
    'invisible to every test built on this corpus. Teach parseSelector() the ' +
    'syntax rather than letting the rule go unchecked.'
  );
  console.log(`PASS: ${rules.length} rules parsed, ${colourRules} of them set a colour, 0 unparseable`);
}

function testTokensResolve(h) {
  const required = ['--surface-app', '--surface-till', '--text-primary', '--text-money-positive',
    '--state-success-text', '--touch-target-min', '--app-bg', '--text', '--surface-card'];
  const missing = required.filter((n) => h.tokens[n] === undefined);
  assert.deepStrictEqual(missing, [], `Token(s) missing from the resolved map: ${missing.join(', ')}`);
  const appBg = h.parseColour(h.tokens['--app-bg'], h.tokens);
  assert.ok(appBg && appBg.a === 1, '--app-bg must resolve to an OPAQUE colour: it is the surface every ' +
    'otherwise-transparent element ultimately sits on, and the background walk terminates on it.');
  console.log(`PASS: ${Object.keys(h.tokens).length} tokens resolved; --app-bg = ${JSON.stringify(appBg)}`);
}

/* Every screen buildCorpus() sets out to render, in order.
   The assertion below compares this against what came back, EXACTLY — not
   `>= 3`, and not a total element count. A floor on the total is the shape of
   guard that let this corpus sit at three screens: one screen can silently
   render its error state, or throw inside its own try/catch and produce four
   elements, while twelve others keep the total comfortably above any number
   written here. A screen that does not appear must fail by name. */
const DECLARED_SCREENS = [
  'dashboard', 'cashier-landing', 'pos',
  'sales-history', 'returns', 'purchase-orders', 'products', 'customers',
  'suppliers', 'audit-log', 'reports', 'categories', 'scanner',
  'stock-accuracy',
  'customer-modal', 'sale-modal', 'held-sales-modal',
];

/* ── THE CORPUS MUST BE CLOSED AGAINST THE ROUTER ───────────────────────────
 *
 * DECLARED_SCREENS above is a list somebody maintains by hand, and a hand-kept
 * list of screens has exactly one failure mode: the product grows a screen and
 * the list does not. Nothing noticed. That is not hypothetical — it is how the
 * Reports page shipped white-on-white THROUGH a round whose stated purpose was
 * widening this corpus: `reports` was a live section of
 * `RetailSystem.render()`'s switch the whole time, no test compared the two,
 * and its absence was silence rather than a failure.
 *
 * So the authority is the router, read out of the product at run time. Every
 * `case '<id>':` in `RetailSystem.render()` must be either
 *
 *   * BUILT — some corpus screen renders it (SCREEN_ROUTES), or
 *   * EXCLUDED IN WRITING — named in ROUTE_EXCLUSIONS with the reason it
 *     cannot be rendered headlessly.
 *
 * Silence is no longer one of the options. The chain is complete in both
 * directions: router -> SCREEN_ROUTES -> DECLARED_SCREENS ->
 * testCorpusRendersRealScreens, which asserts the screens actually built.
 */
const SCREEN_ROUTES = {
  dashboard: 'dashboard',
  // The same entry point with no `retail.reports` capability. One route, two
  // genuinely different surfaces; both are in the corpus.
  'cashier-landing': 'dashboard',
  pos: 'pos',
  'sales-history': 'sales',
  returns: 'returns',
  'purchase-orders': 'purchases',
  products: 'products',
  customers: 'customers',
  suppliers: 'suppliers',
  'audit-log': 'audit-log',
  reports: 'reports',
  categories: 'categories',
  scanner: 'scanner',
  'stock-accuracy': 'stock-accuracy',
};

/* The two router sections this corpus does NOT build, each with the reason.
   Written down rather than omitted, because an omission looks exactly like an
   oversight and a reason can be argued with. Both are recoverable: fix the
   stated cause and the exclusion goes away. */
const ROUTE_EXCLUSIONS = {
  employees:
    'render() dispatches this one to RetailEmployees.render(), which lives in a ' +
    'DIFFERENT file (frontend/employees.js). loadRetailSystem() evaluates ' +
    'subsystem-retail.js alone, so `RetailEmployees` is not defined in the vm ' +
    'context and the call throws before any markup exists. Rendering it here ' +
    'would mean loading a second file into the same sandbox — a real change to ' +
    'the harness, not a one-line corpus addition. Its own suite is ' +
    'retail_employees_screen_test.js; note that suite is structural and does ' +
    'NOT measure contrast, so this screen is genuinely unmeasured here.',
  'admin-center':
    '_renderAdminCenter() is a settings page whose only table is filled by a ' +
    'reorder-request endpoint the corpus fixture does not serve, so it captures ' +
    'the "Loading…" single-cell placeholder — the empty state ' +
    'testCorpusRendersRealScreens explicitly refuses to accept as a rendered ' +
    'list. Adding it needs a fixture route, not just a render call.',
};

/** Every `case '<id>':` in RetailSystem.render(), read out of the product. */
function routerSections() {
  const src = fs.readFileSync(RETAIL_JS, 'utf8');
  const start = src.indexOf('  render(sectionId) {');
  assert.ok(start !== -1, 'Could not find RetailSystem.render(sectionId) in subsystem-retail.js.');
  const end = src.indexOf('\n  },', start);
  assert.ok(end > start, 'Could not find the end of RetailSystem.render().');
  const body = src.slice(start, end);
  const ids = [];
  const re = /case\s+'([^']+)'\s*:/g;
  let m;
  while ((m = re.exec(body)) !== null) ids.push(m[1]);
  return ids;
}

function testTheCorpusIsClosedAgainstTheRouter() {
  const sections = routerSections();

  // ANTI-VACUITY. Every comparison below is a filter over this list. If the
  // scrape broke — render() renamed, reformatted, or the `case` syntax changed
  // — an empty list would make all three comparisons pass while proving that
  // the corpus is closed against nothing at all.
  assert.ok(
    sections.length >= 12,
    `Only ${sections.length} case labels were read out of RetailSystem.render(). ` +
    'The scrape is broken, so "the corpus covers every route" would be a claim ' +
    'about an empty list.'
  );
  assert.deepStrictEqual(
    sections.filter((id, i) => sections.indexOf(id) !== i), [],
    'RetailSystem.render() has duplicate case labels; the second is dead code.'
  );

  const built = new Set(Object.values(SCREEN_ROUTES));
  const excluded = new Set(Object.keys(ROUTE_EXCLUSIONS));

  /* All three mismatches are collected and reported TOGETHER. They are three
     symptoms of one edit -- renaming a route produces a stale entry AND an
     unaccounted section -- and reporting the first and stopping is the same
     one-defect-per-run blindness the file-level runner was rewritten to fix,
     just at a finer grain. */
  const problems = [];

  for (const id of sections.filter((s) => !built.has(s) && !excluded.has(s))) {
    problems.push(
      `UNACCOUNTED: render() routes to '${id}', which this corpus neither builds nor excludes in writing.\n` +
      '      Every downstream contrast, opacity and touch-target assertion is a loop over the\n' +
      '      corpus, so a route outside it is a whole screen the suite goes green without\n' +
      '      looking at — which is exactly how the Reports page shipped white-on-white through\n' +
      '      the round that was widening this corpus. Either add it to buildCorpus() +\n' +
      '      DECLARED_SCREENS + SCREEN_ROUTES, or add it to ROUTE_EXCLUSIONS with the reason it\n' +
      '      cannot be rendered headlessly. Do not just leave it out.'
    );
  }

  for (const id of [...built, ...excluded].filter((s) => !sections.includes(s))) {
    problems.push(
      `STALE: SCREEN_ROUTES/ROUTE_EXCLUSIONS names '${id}', which the router no longer has.\n` +
      '      A stale entry silently re-opens the hole it was written to close: it keeps\n' +
      '      "covered" ticking over for a route that has been renamed, while the NEW name\n' +
      '      falls through as unaccounted.'
    );
  }

  for (const id of [...excluded].filter((s) => built.has(s))) {
    problems.push(
      `SUPERSEDED: '${id}' is listed in ROUTE_EXCLUSIONS and the corpus now builds it.\n` +
      '      Delete the exclusion. A written reason that is no longer true is worse than no\n' +
      '      reason: it is the one entry a reader will trust without re-checking, and it goes\n' +
      '      on excusing the next screen that lands under the same name.'
    );
  }

  for (const name of Object.keys(SCREEN_ROUTES).filter((n) => !DECLARED_SCREENS.includes(n))) {
    problems.push(
      `UNDECLARED: SCREEN_ROUTES claims screen '${name}', which DECLARED_SCREENS does not list.\n` +
      '      "This route is covered" is only worth anything if the screen it names is one the\n' +
      '      corpus actually builds.'
    );
  }

  assert.deepStrictEqual(
    problems, [],
    `${problems.length} corpus/router mismatch(es):\n  ` + problems.join('\n  ')
  );

  console.log(
    `PASS: the corpus is closed against RetailSystem.render() — ${sections.length} router ` +
    `section(s), ${built.size} built by ${Object.keys(SCREEN_ROUTES).length} screen(s), ` +
    `${excluded.size} excluded in writing (${[...excluded].join(', ')})`
  );
}

/* The placeholder a list screen shows INSTEAD of its rows: while loading, when
   the fetch is refused, and when the result set is empty. Every one of them is
   a single full-width cell, and a corpus that captured one of these instead of
   real rows would look populated and contain none of the badges, money, dates
   or row controls this whole file exists to measure. */
function dataRowsOf(root) {
  const rows = [];
  dom.walkElements(root, (el) => {
    if (el.tag !== 'tr') return;
    const cells = (el.children || []).filter((n) => n.type === 'element' && n.tag === 'td');
    if (cells.length >= 3 && !cells.some((c) => c.attrs.colspan)) rows.push(el);
  });
  return rows;
}

function testCorpusRendersRealScreens(h) {
  assert.deepStrictEqual(
    h.screens.map((s) => s.name), DECLARED_SCREENS,
    'The corpus did not render the screens it declares. Every downstream ' +
    'assertion is a loop over these screens, so one that quietly failed to ' +
    'build is a whole surface this suite silently stops checking — which is ' +
    'precisely how the receipt-opener button and five <bdi> isolations could be ' +
    'deleted with the suite still green.'
  );

  const perScreen = [];
  const thin = [];
  const emptyStates = [];
  let total = 0;
  for (const s of h.screens) {
    const els = h.allElements(s.root);
    const text = textPaintingElements(s.root).length;
    total += els.length;
    perScreen.push(`${s.name}=${els.length}el/${text}text/${s.spliced}spliced`);

    // PER-SCREEN, not per-corpus. The whole point: thirteen screens cannot
    // cover for one that emptied.
    if (els.length < 10 || text < 4) thin.push(`${s.name}: ${els.length} elements, ${text} text-painting`);

    // A screen that OWNS a table must have rendered ROWS, not the "Loading…" /
    // "No sales found." single-cell placeholder. This is the check that makes
    // the fixture wiring load-bearing: mis-route one endpoint and the screen
    // still renders, still anchors, still has a hundred elements, and contains
    // not one badge, amount or date.
    const hasTable = els.some((el) => el.tag === 'tbody');
    if (hasTable && dataRowsOf(s.root).length === 0) {
      emptyStates.push(`${s.name}: has a <tbody> but rendered no multi-cell data row — ` +
        'it captured a loading/empty/refused placeholder, not the list.');
    }

    // Every screen must actually be INSIDE the shell chain, or its background
    // walk would terminate on nothing and every pairing would read unresolved.
    assert.ok(
      els.some((el) => el.attrs.id === 'sub-content'),
      `Screen "${s.name}" is not anchored in the .sub-content shell chain.`
    );
  }

  assert.deepStrictEqual(thin, [], 'Screen(s) rendered almost nothing:\n  ' + thin.join('\n  '));
  assert.deepStrictEqual(emptyStates, [], 'Screen(s) captured an empty state:\n  ' + emptyStates.join('\n  '));

  console.log(`PASS: corpus renders all ${DECLARED_SCREENS.length} declared screens, ${total} elements`);
  console.log(`      ${perScreen.join(', ')}`);
}

function testCorpusContainsTheKnownHazards(h) {
  // Named landmarks, because a corpus that silently stopped reaching them is
  // indistinguishable from a corpus that passes. Each of these is an element an
  // adversarial verifier actually measured on a real screen.
  const wanted = [
    ['pos', (el) => el.attrs.id === 'pos-held-btn', 'the POS "Held" button (measured 1.48:1)'],
    ['dashboard', (el) => el.classes.includes('ret-badge'), 'a Recent Transactions payment badge (measured 1.91–2.20:1)'],
    ['dashboard', (el) => el.classes.includes('rdash-bd-value') && el.classes.includes('is-in'), 'the dashboard Sales figure (.rdash-bd-value.is-in)'],
    ['dashboard', (el) => el.classes.includes('ret-btn-ghost'), 'a dashboard ghost button (hardcoded 40px block size)'],
    ['pos', (el) => el.classes.includes('pos-cat-btn'), 'a POS category pill'],
    ['pos', (el) => el.classes.includes('money'), 'a POS money amount'],
    // The sold-out tile. Invisible to this file until opacity was modelled: its
    // name reported 15.95:1 and composited at 2.78:1, and `opacity:.06` passed.
    ['pos', (el) => el.classes.includes('pos-card-outofstock'), 'the sold-out product tile (opacity:.45 group)'],
    // The chrome that five list screens are made of, and that the corpus could
    // previously only reach through the one dashboard table that overrides it.
    ['sales-history', (el) => el.classes.includes('ret-search'), 'the shared list search box (measured 1.00:1 — white on a 5% white tint)'],
    ['sales-history', (el) => el.classes.includes('ret-rowbtn'), 'the receipt-opener button OFF the dashboard (_saleOpenerButton)'],
    ['customer-modal', (el) => el.classes.includes('ret-rowbtn'), 'the receipt-opener button in the customer Purchase-History modal'],
    ['products', (el) => el.classes.includes('ret-btn-danger'), 'a destructive list-row button (measured 2.70:1)'],
    ['purchase-orders', (el) => el.classes.includes('ret-badge-yellow'), 'a yellow status badge (measured 1.31:1)'],
    ['sale-modal', (el) => el.classes.includes('ret-modal'), 'the sale-detail modal panel'],
    ['audit-log', (el) => el.tag === 'bdi', 'the audit log\'s bidi-isolated actor id'],
    ['held-sales-modal', (el) => el.classes.includes('ret-btn-danger'), 'the held-sale Discard button'],
    // The three screens added when the corpus was closed against the router.
    // Named individually for the usual reason: "reports is in DECLARED_SCREENS"
    // is satisfied by a Reports screen that rendered its capability-restricted
    // stub, and that stub contains none of the four things below.
    ['reports', (el) => el.classes.includes('ret-kpi-value'), 'a Reports KPI tile value (.ret-kpi-value — one of the chrome rules the ledger listed as reaching no rendered element at all)'],
    ['reports', (el) => el.tag === 'bdi', 'the Reports by-employee identity cell (_attributionCell\'s dir="auto" isolation)'],
    ['categories', (el) => el.classes.includes('ret-btn-danger'), 'the Categories Delete button — a fifth home for the destructive-button pairing, and the reason :active is now an evaluated state'],
    ['scanner', (el) => el.classes.includes('ret-input'), 'the scanner test readout (.ret-input)'],
    // Stock accuracy. Named individually for the reason the Reports entries
    // above are: "stock-accuracy is in DECLARED_SCREENS" is satisfied by the
    // clean panel, the no-records panel and the check-failed panel alike, and
    // none of those three contains a drifted row, a signed figure or the
    // repair control. If the fixture stops carrying drift, the screen still
    // renders and this suite still measures — the wrong screen.
    ['stock-accuracy', (el) => el.classes.includes('ret-kpi-sub'), 'the Stock accuracy headline caption (.ret-kpi-sub — a chrome rule the ledger listed as reaching no rendered element at all)'],
    ['stock-accuracy', (el) => el.classes.includes('num'), 'a Stock accuracy quantity (.num — tabular figures with no money colour, the class rtl.css forces back to LTR)'],
    ['stock-accuracy', (el) => el.tag === 'bdi', 'the bidi isolation around a signed drift figure (a "+7" run carries no strong directional character at all)'],
    ['stock-accuracy', (el) => el.classes.includes('ret-badge-yellow'), 'the "Needs a branch" badge on a NULL-branch movement (the row a repair cannot touch)'],
    ['stock-accuracy', (el) => el.attrs.id === 'stka-repair-btn', 'the repair control — the ONLY route into a stock repair, and the reason it is never automatic'],
  ];
  const missing = [];
  for (const [screenName, pred, what] of wanted) {
    const screen = h.screens.find((s) => s.name === screenName);
    if (!screen || !h.allElements(screen.root).some(pred)) missing.push(`${what} (on "${screenName}")`);
  }
  assert.deepStrictEqual(
    missing, [],
    'The corpus no longer reaches element(s) that a real contrast/touch defect ' +
    'was measured on:\n  ' + missing.join('\n  ') +
    '\n\nEvery downstream assertion is a loop over these elements. A corpus that ' +
    'stops reaching them goes green while checking nothing — which is precisely ' +
    'the failure this file was written to make impossible.'
  );
  console.log(`PASS: all ${wanted.length} known-hazard landmarks are present in the corpus`);
}

function testTextPaintingElementsAreFound(h) {
  let total = 0;
  for (const s of h.screens) total += h.textPaintingElements(s.root).length;
  // The per-screen floor lives in testCorpusRendersRealScreens, where it
  // belongs: a corpus-wide minimum is satisfiable by twelve healthy screens and
  // one empty one, which is the shape of guard this round exists to remove.
  assert.ok(total >= DECLARED_SCREENS.length * 4,
    `Only ${total} text-painting elements across ${DECLARED_SCREENS.length} screens — the text walk is broken.`);
  console.log(`PASS: ${total} text-painting elements across ${h.screens.length} screens`);
}

/* ── OPACITY — the dimension that did not exist ─────────────────────────────
 *
 * Two tests, and the split is deliberate.
 *
 * The ARITHMETIC is asserted against a synthetic stylesheet with hand-computed
 * answers, NOT against whatever the product happens to declare today. That is
 * the only shape that survives the product being fixed: the first version of
 * this check asserted "the corpus contains at least one opacity group", which
 * is a guard whose pass condition is the presence of the defect — the moment
 * `.pos-card-outofstock { opacity:.45 }` was removed (which is the correct
 * fix), the harness's own model became untested and would have been quietly
 * deletable. A dimension has to be provable when the product is CLEAN.
 *
 * The CORPUS SWEEP then asserts the model actually runs over what is rendered,
 * in every state the contrast tier evaluates, and never guesses: no element's
 * opacity may fail to resolve, and no element outside a group may be touched.
 */
const OPACITY_STATES = [
  ['resting', new Set()],
  ['hovered', new Set(['hover'])],
  ['focused', new Set(['focus', 'focus-visible'])],
  ['disabled', new Set(['disabled'])],
];

function testOpacityArithmeticIsGroupCompositing() {
  // Deliberately extreme, deliberately hand-computable. backdrop black, group
  // fill white, text mid-grey — so foreground and background move by different
  // amounts and a resolver that fudged either one cannot land on both answers.
  const sources = [{
    label: 'synthetic',
    css: `body { background:#000000 }
          .group { background:#ffffff; opacity:0.5 }
          .inner { opacity:0.5 }
          .plain { }
          .full  { opacity:1 }
          .reveal { opacity:0; animation:wsReveal 0.5s forwards }
          .t { color:#808080 }`,
  }];
  const rt = buildRuleTable(sources);
  const tokens = Object.create(null);
  const root = dom.parseFragment(
    '<body>' +
    '<div class="group"><span class="t one">a</span>' +
      '<div class="inner"><span class="t two">b</span></div></div>' +
    '<div class="plain"><span class="t three">c</span></div>' +
    '<div class="full"><span class="t four">d</span></div>' +
    '<div class="reveal"><span class="t five">e</span></div>' +
    '</body>'
  );
  const find = (cls) => {
    let out = null;
    dom.walkElements(root, (el) => { if (!out && el.classes.includes(cls)) out = el; });
    assert.ok(out, `synthetic fixture lost .${cls}`);
    return out;
  };
  const run = (cls) => {
    const el = find(cls);
    const fg = effectiveColour(rt, el, tokens, new Set());
    const bg = effectiveBackground(rt, el, tokens, new Set());
    assert.ok(!fg.unresolved, `synthetic .${cls} colour: ${fg.unresolved}`);
    assert.ok(!bg.unresolved, `synthetic .${cls} surface: ${bg.unresolved}`);
    return paintThroughOpacity(rt, el, tokens, new Set(), fg.colour, bg.colour);
  };
  const near = (a, b) => Math.abs(a - b) < 1e-6;

  // One group at 50%: mid-grey text (128) and a white fill (255) each fall
  // halfway to the black backdrop.
  const one = run('one');
  assert.ok(near(one.alpha, 0.5), `one group: alpha ${one.alpha}, expected 0.5`);
  assert.ok(near(one.colour.r, 64), `one group: foreground ${one.colour.r}, expected 64 (0.5*128 + 0.5*0)`);
  assert.ok(near(one.surface.r, 127.5), `one group: surface ${one.surface.r}, expected 127.5 (0.5*255 + 0.5*0)`);

  // Nested groups MULTIPLY, and both layers composite onto the OUTERMOST
  // group's backdrop — not onto each other's surfaces.
  const two = run('two');
  assert.ok(near(two.alpha, 0.25), `nested groups: alpha ${two.alpha}, expected 0.25`);
  assert.ok(near(two.colour.r, 32), `nested groups: foreground ${two.colour.r}, expected 32`);
  assert.ok(near(two.surface.r, 63.75), `nested groups: surface ${two.surface.r}, expected 63.75`);

  // No group, and an explicit opacity:1, must both be exact identities. Without
  // this the model would be satisfiable by washing the entire screen, which
  // would make every downstream ratio wrong in the other direction.
  for (const cls of ['three', 'four']) {
    const r = run(cls);
    assert.strictEqual(r.alpha, 1, `.${cls} should be untouched by the opacity model`);
    assert.strictEqual(r.groups.length, 0, `.${cls} should be inside no opacity group`);
    assert.ok(near(r.colour.r, 128) && near(r.surface.r, 0), `.${cls} colour/surface were altered`);
  }

  // `opacity:0` that is the first frame of a `forwards` animation is a reveal,
  // not a permanently invisible element. Measuring it would report a 1.00:1
  // failure on text that is fully opaque a third of a second later.
  const five = run('five');
  assert.strictEqual(five.alpha, 1, 'an animated opacity:0 reveal must not be measured as invisible');

  // ...and an opacity that cannot be read must SAY SO rather than default to 1.
  const bad = buildRuleTable([{ label: 'synthetic', css: 'body{background:#000} .group{opacity:var(--nope)} .t{color:#808080}' }]);
  const badRoot = dom.parseFragment('<body><div class="group"><span class="t">a</span></div></body>');
  let target = null;
  dom.walkElements(badRoot, (el) => { if (!target && el.classes.includes('t')) target = el; });
  const unresolvable = opacityGroups(bad, target, Object.create(null), new Set());
  assert.ok(
    unresolvable.unresolved,
    'An opacity whose value does not resolve was silently treated as 1. Defaulting ' +
    'to opaque is exactly the silence that let a 45% wash go unmeasured for two rounds.'
  );

  console.log('PASS: opacity arithmetic is group compositing — 1 group, nested groups, identity, reveal, and unresolvable all check out');
}

function testOpacityIsResolvedOverTheWholeCorpus(h) {
  const inGroup = [];
  let outside = 0;
  const spurious = [];

  for (const screen of h.screens) {
    for (const [stateName, states] of OPACITY_STATES) {
      for (const el of h.textPaintingElements(screen.root)) {
        const found = h.opacityGroups(h.ruleTable, el, h.tokens, states);
        assert.ok(!found.unresolved,
          `Opacity did not resolve on ${screen.name}/${stateName} ${h.describe(el).slice(0, 60)}: ` +
          `${found.unresolved}\nAn unresolvable opacity must not silently default to 1.`);

        const fg = h.effectiveColour(h.ruleTable, el, h.tokens, states);
        const bg = h.effectiveBackground(h.ruleTable, el, h.tokens, states);
        if (fg.unresolved || bg.unresolved) continue;
        const painted = h.paintThroughOpacity(h.ruleTable, el, h.tokens, states, fg.colour, bg.colour);
        if (painted.unresolved) {
          spurious.push(`${screen.name}/${stateName} ${h.describe(el).slice(0, 55)} — ${painted.unresolved}`);
          continue;
        }
        if (!found.groups.length) {
          outside++;
          if (painted.alpha !== 1 || painted.colour !== fg.colour || painted.surface !== bg.colour) {
            spurious.push(`${screen.name}/${stateName} ${h.describe(el).slice(0, 55)} — composited despite being in no opacity group`);
          }
          continue;
        }
        const alpha = found.groups.reduce((a, g) => a * g.alpha, 1);
        assert.ok(Math.abs(painted.alpha - alpha) < 1e-9,
          `Group alpha on ${screen.name}/${stateName} is ${painted.alpha}, expected the product ${alpha}.`);
        inGroup.push(`${screen.name}/${stateName} ${h.describe(el).slice(0, 45)} @${alpha.toFixed(2)} ` +
          `${h.contrastRatio(fg.colour, bg.colour).toFixed(2)}:1 -> ${h.contrastRatio(painted.colour, painted.surface).toFixed(2)}:1`);
      }
    }
  }

  assert.deepStrictEqual(spurious, [], 'The opacity model misfired on:\n  ' + spurious.join('\n  '));
  assert.ok(outside >= 100, `Only ${outside} corpus pairings resolved outside an opacity group — the sweep is broken.`);

  // NOT asserted: that the corpus still contains an opacity group. It did when
  // this dimension was written (.pos-card-outofstock, .pos-empty-icon) and both
  // were removed as the fix, which is the right outcome — a guard that required
  // them to stay would have been a guard whose pass condition is the bug. The
  // arithmetic is proven above, on a fixture that cannot be fixed away.
  console.log(
    `PASS: opacity resolved on every corpus pairing in ${OPACITY_STATES.length} states — ` +
    `${outside} outside any group, ${inGroup.length} inside one`
  );
  for (const line of inGroup.slice(0, 12)) console.log('      ' + line);
}

/* ── PER-TEST ISOLATION ─────────────────────────────────────────────────────
 * Same runner, same reasoning, as retail_design_contrast_test.js: a flat
 * sequence reports the FIRST failure and no others, and every check after it is
 * not "passing" but NOT RUN — which reads identically. Measured on this exact
 * file: with three checks deliberately broken (the CSS-size floor, a missing
 * required token, and the text-painting floor), the old shape reported one and
 * exited; the shape below reports all three.
 */
async function runAll(checks) {
  const failures = [];
  for (const [name, fn] of checks) {
    try {
      await fn();
    } catch (err) {
      failures.push([name, err]);
      console.error(`FAIL: ${name}`);
      console.error('      ' + String((err && err.message) || err).replace(/\n/g, '\n      '));
    }
  }
  return failures;
}

/* Anti-vacuity for the runner itself: the list below is built conditionally, a
   conditionally built list can be built empty, and a loop over nothing reports
   no failures. */
const EXPECTED_CHECKS = 9;

async function main() {
  const checks = [
    // Reads the product source only, so it stands even when the corpus will
    // not build — and "the corpus does not cover the router" is exactly the
    // kind of finding a corpus failure would otherwise bury.
    ['the corpus is closed against the router', testTheCorpusIsClosedAgainstTheRouter],
    ['opacity arithmetic is group compositing', testOpacityArithmeticIsGroupCompositing],
  ];

  const setupFailures = [];
  let h = null;
  try {
    h = await harness();
  } catch (err) {
    setupFailures.push(['harness() (7 corpus checks could not run)', err]);
    console.error('FAIL: harness() (7 corpus checks could not run)');
    console.error('      ' + String((err && err.message) || err).replace(/\n/g, '\n      '));
  }
  if (h) {
    checks.push(
      ['stylesheet sources are all present', () => testStylesheetSourcesAreAllPresent(h)],
      ['the rule table parsed', () => testRuleTableParsed(h)],
      ['tokens resolve', () => testTokensResolve(h)],
      ['the corpus renders real screens', () => testCorpusRendersRealScreens(h)],
      ['the corpus contains the known hazards', () => testCorpusContainsTheKnownHazards(h)],
      ['text-painting elements are found', () => testTextPaintingElementsAreFound(h)],
      ['opacity is resolved over the whole corpus', () => testOpacityIsResolvedOverTheWholeCorpus(h)],
    );
  }

  const failures = setupFailures.concat(await runAll(checks));
  const attempted = checks.length + setupFailures.length;

  if (attempted < EXPECTED_CHECKS) {
    console.error(
      `FAIL: retail_design_render_test.js ran only ${attempted} of ${EXPECTED_CHECKS} known checks. ` +
      'A runner that quietly loses a check reports a clean bill of health on work it never did.'
    );
    process.exitCode = 1;
    return;
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_design_render_test.js — ${failures.length} of ${attempted} checks failed:`);
    for (const [name] of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_design_render_test.js — ${attempted} checks`);
}

module.exports = { harness, AA: 4.5, AAA: 7.0 };

if (require.main === module) {
  main().catch((err) => {
    // Only reachable if the runner ITSELF breaks; every check-level throw is
    // caught and collected above.
    console.error('FAIL: retail_design_render_test.js (runner)');
    console.error(err.message || err);
    process.exitCode = 1;
  });
}
