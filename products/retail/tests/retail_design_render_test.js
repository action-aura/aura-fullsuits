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
 *   * Screens the corpus does not render (modals, products, suppliers,
 *     reports). Nothing here pretends otherwise: retail_design_contrast_test.js
 *     enumerates every shared-chrome colour rule this corpus fails to exercise,
 *     prints each one with the ratio it would measure, and caps the total, so
 *     the blind spot is a published number rather than a silence.
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

function makeStub(over) {
  const el = Object.assign({
    innerHTML: '', textContent: '', value: '', id: '', disabled: false, style: {},
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
      json: () => Promise.resolve({ data: /held-sales/.test(String(url)) ? [] : STATS }),
    }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
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
      body: { appendChild() {} },
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
  return { RetailSystem: sandbox.RetailSystem, els, chartHosts, injected, namedQueries };
}

/**
 * The real shell chain. Every screen is rendered into <main class="sub-content"
 * id="sub-content">, which app-shell.js's _renderShell() builds inside
 * .sub-shell > .sub-main. That chain is what supplies the background under
 * anything the screen leaves transparent, so reconstructing it is not
 * decoration -- omitting it would make every transparent element unresolvable.
 */
const SHELL_OPEN = '<body><div class="sub-shell"><div class="sub-main"><main class="sub-content" id="sub-content">';
const SHELL_CLOSE = '</main></div></div></body>';

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

  for (const [id, stub] of Object.entries(ctx.els)) {
    const host = byId.get(id);
    if (!host) continue;
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

function screenFrom(name, ctx, contentHtml) {
  const root = dom.parseFragment(SHELL_OPEN + contentHtml + SHELL_CLOSE);
  const spliced = spliceDeferredWrites(root, ctx);
  return { name, root, spliced, injectedCss: ctx.injected.css };
}

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

function testCorpusRendersRealScreens(h) {
  assert.strictEqual(h.screens.length, 3, `Expected 3 rendered screens, got ${h.screens.length}`);
  let total = 0;
  for (const s of h.screens) {
    const els = h.allElements(s.root);
    total += els.length;
    // The cashier landing is genuinely small (one card, one primary action);
    // the floor is per-screen "it rendered at all", with the real size guard on
    // the corpus total below.
    assert.ok(els.length >= 10, `Screen "${s.name}" produced only ${els.length} elements — the render or the splice broke.`);
    // The landing makes no fetch and defers nothing, by design, so a zero here
    // is only a defect on the screens that DO fill regions after a fetch.
    if (s.name !== 'cashier-landing') {
      assert.ok(s.spliced >= 4, `Screen "${s.name}" spliced back only ${s.spliced} deferred writes; ` +
        'the badges, the money breakdown and the cart all arrive that way, so a low count means ' +
        'the corpus is missing most of what it exists to look at.');
    }
    // Every screen must actually be INSIDE the shell chain, or its background
    // walk would terminate on nothing and every pairing would read unresolved.
    const anchored = h.allElements(s.root).some((el) => el.attrs.id === 'sub-content');
    assert.ok(anchored, `Screen "${s.name}" is not anchored in the .sub-content shell chain.`);
  }
  assert.ok(total >= 200, `The whole corpus is only ${total} elements. It was 249 when written; ` +
    'a corpus that shrinks is a suite that quietly stops checking.');
  const counts = h.screens.map((s) => `${s.name}=${h.allElements(s.root).length}el/${s.spliced}spliced`);
  console.log(`PASS: corpus renders 3 real screens, ${total} elements — ${counts.join(', ')}`);
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
  assert.ok(total >= 60, `Only ${total} text-painting elements across the whole corpus — the text walk is broken.`);
  console.log(`PASS: ${total} text-painting elements across the corpus`);
}

async function main() {
  const h = await harness();
  testStylesheetSourcesAreAllPresent(h);
  testRuleTableParsed(h);
  testTokensResolve(h);
  testCorpusRendersRealScreens(h);
  testCorpusContainsTheKnownHazards(h);
  testTextPaintingElementsAreFound(h);
  console.log('PASS: retail_design_render_test.js');
}

module.exports = { harness, AA: 4.5, AAA: 7.0 };

if (require.main === module) {
  main().catch((err) => {
    console.error('FAIL: retail_design_render_test.js');
    console.error(err.message || err);
    process.exitCode = 1;
  });
}
