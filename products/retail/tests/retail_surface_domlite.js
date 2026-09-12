/**
 * retail_surface_domlite.js — a very small HTML + CSS reader, shared by the
 * retail_surface_* structural tests.
 *
 * WHY THIS EXISTS AT ALL
 *
 * The claims those tests make are STRUCTURAL: "the sale total is the largest
 * money element on the screen", "the remove-line control is not adjacent to
 * the quantity control". Neither can be checked with a regex over an HTML
 * string, because both are questions about a TREE (who is whose sibling) and
 * about the CASCADE (which font-size actually wins). A substring assertion
 * would pass the moment someone reflowed the markup, which is precisely the
 * kind of test that is worth nothing.
 *
 * There is no jsdom here and there cannot be: this frontend has no bundler, no
 * package.json and no node_modules (CLAUDE.md), and every other .js test in
 * this directory runs on Node built-ins alone. So the tree and the cascade are
 * reconstructed here, in about two hundred lines, with the approximations
 * stated out loud below rather than hidden.
 *
 * DELIBERATE APPROXIMATIONS — read these before trusting a result:
 *
 *   1. Selector matching is RIGHTMOST-COMPOUND ONLY. `.pos-wrap .money` is
 *      treated as matching any `.money`. That is sound for the way this file
 *      is used -- each test parses ONE screen's <style> block, whose selectors
 *      are all scoped to that screen anyway -- and it errs toward matching MORE
 *      rules, never fewer, so it cannot make a size look smaller than it is.
 *
 *   2. Specificity is not modelled. Where several rules set the same property,
 *      the callers here ask for ALL of the values rather than "the winner",
 *      and then reason over the whole range. For the "largest element" claim
 *      that is strictly stronger than picking a winner: it compares the total
 *      at its SMALLEST possible size against everything else at its LARGEST.
 *
 *   3. Viewport-relative units inside clamp() are not evaluated. clamp(a,b,c)
 *      is read as "never below a, never above c", which is exactly what the
 *      CSS guarantees and all these tests need.
 *
 * Not a general-purpose parser. It handles the markup this repo actually
 * emits; anything it cannot parse it reports rather than silently skips.
 */
'use strict';

const VOID_ELEMENTS = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
  'link', 'meta', 'param', 'source', 'track', 'wbr',
]);

// Elements whose content is text, not markup. Their bodies must not be walked
// for tags, or a `>` inside a CSS selector starts inventing elements.
const RAW_TEXT_ELEMENTS = new Set(['script', 'style']);

// ─────────────────────────────────────────────────────────────────────────────
// HTML
// ─────────────────────────────────────────────────────────────────────────────

function parseAttributes(raw) {
  const attrs = {};
  const re = /([a-zA-Z_:@][-a-zA-Z0-9_:.]*)\s*(?:=\s*("([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;
  let m;
  while ((m = re.exec(raw)) !== null) {
    const name = m[1].toLowerCase();
    const value = m[3] !== undefined ? m[3]
                : m[4] !== undefined ? m[4]
                : m[5] !== undefined ? m[5]
                : '';
    attrs[name] = value;
  }
  return attrs;
}

function makeElement(tag, attrs, parent) {
  const cls = (attrs.class || '').trim();
  return {
    type: 'element',
    tag,
    attrs,
    classes: cls ? cls.split(/\s+/) : [],
    children: [],
    parent: parent || null,
  };
}

/**
 * Parse an HTML fragment into a tree. Returns the synthetic root element,
 * whose `children` are the fragment's top-level nodes.
 */
function parseFragment(html) {
  const root = makeElement('#fragment', {}, null);
  let current = root;
  let i = 0;

  const pushText = (text) => {
    if (!text) return;
    current.children.push({ type: 'text', text, parent: current });
  };

  while (i < html.length) {
    const lt = html.indexOf('<', i);
    if (lt === -1) { pushText(html.slice(i)); break; }
    pushText(html.slice(i, lt));

    // Comment
    if (html.startsWith('<!--', lt)) {
      const end = html.indexOf('-->', lt + 4);
      i = end === -1 ? html.length : end + 3;
      continue;
    }
    // Doctype / processing instruction
    if (html.startsWith('<!', lt)) {
      const end = html.indexOf('>', lt);
      i = end === -1 ? html.length : end + 1;
      continue;
    }

    // Closing tag
    if (html[lt + 1] === '/') {
      const end = html.indexOf('>', lt);
      if (end === -1) { i = html.length; break; }
      const tag = html.slice(lt + 2, end).trim().toLowerCase();
      // Walk up to the nearest matching open element. Tolerates the stray
      // unclosed tag rather than reparenting the rest of the document under it.
      let node = current;
      while (node && node !== root && node.tag !== tag) node = node.parent;
      current = (node && node !== root) ? node.parent : current;
      i = end + 1;
      continue;
    }

    // Opening tag
    const end = html.indexOf('>', lt);
    if (end === -1) { pushText(html.slice(lt)); break; }
    let inner = html.slice(lt + 1, end);
    const selfClosing = inner.endsWith('/');
    if (selfClosing) inner = inner.slice(0, -1);

    const sp = inner.search(/\s/);
    const tag = (sp === -1 ? inner : inner.slice(0, sp)).toLowerCase();
    const attrs = parseAttributes(sp === -1 ? '' : inner.slice(sp));
    const el = makeElement(tag, attrs, current);
    current.children.push(el);

    if (RAW_TEXT_ELEMENTS.has(tag)) {
      const closeIdx = html.toLowerCase().indexOf(`</${tag}`, end + 1);
      const body = html.slice(end + 1, closeIdx === -1 ? html.length : closeIdx);
      el.children.push({ type: 'text', text: body, parent: el });
      const closeEnd = closeIdx === -1 ? html.length : html.indexOf('>', closeIdx);
      i = closeEnd === -1 ? html.length : closeEnd + 1;
      continue;
    }

    if (!selfClosing && !VOID_ELEMENTS.has(tag)) current = el;
    i = end + 1;
  }

  return root;
}

/** Depth-first walk over element nodes only. */
function walkElements(node, fn) {
  for (const child of node.children || []) {
    if (child.type !== 'element') continue;
    fn(child);
    walkElements(child, fn);
  }
}

function allElements(root) {
  const out = [];
  walkElements(root, (el) => out.push(el));
  return out;
}

/** Concatenated text of an element's subtree, whitespace-collapsed. */
function textOf(node) {
  let out = '';
  const visit = (n) => {
    if (n.type === 'text') { out += n.text; return; }
    for (const c of n.children || []) visit(c);
  };
  visit(node);
  return out.replace(/\s+/g, ' ').trim();
}

/** Text belonging directly to this element, excluding descendants. */
function ownText(node) {
  return (node.children || [])
    .filter((c) => c.type === 'text')
    .map((c) => c.text)
    .join('')
    .replace(/\s+/g, ' ')
    .trim();
}

/** The element children of this node's parent, in document order. */
function elementSiblings(node) {
  if (!node.parent) return [];
  return (node.parent.children || []).filter((c) => c.type === 'element');
}

/** Immediate previous/next element siblings (the ones a finger can confuse). */
function adjacentSiblings(node) {
  const sibs = elementSiblings(node);
  const idx = sibs.indexOf(node);
  if (idx === -1) return [];
  return [sibs[idx - 1], sibs[idx + 1]].filter(Boolean);
}

function describe(node) {
  if (!node || node.type !== 'element') return String(node);
  const id = node.attrs.id ? `#${node.attrs.id}` : '';
  const cls = node.classes.length ? `.${node.classes.join('.')}` : '';
  const txt = textOf(node);
  return `<${node.tag}${id}${cls}>` + (txt ? ` "${txt.slice(0, 40)}"` : '');
}

// ─────────────────────────────────────────────────────────────────────────────
// CSS
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Flatten a stylesheet into `{ selectors: [...], decls: {prop: value}, at }`
 * records in source order. Rules nested inside @media/@supports are INCLUDED
 * (with `at` naming the wrapper) rather than dropped -- a media query that
 * shrinks an element is exactly the kind of override these tests must see.
 * @keyframes bodies are skipped: their `from`/`to` blocks are not selectors.
 */
function parseCss(cssText) {
  const rules = [];
  const src = cssText.replace(/\/\*[\s\S]*?\*\//g, '');

  const consumeBlock = (text, openIdx) => {
    let depth = 0;
    for (let j = openIdx; j < text.length; j++) {
      if (text[j] === '{') depth++;
      else if (text[j] === '}') { depth--; if (depth === 0) return j; }
    }
    return text.length;
  };

  const parseInto = (text, atName) => {
    let i = 0;
    while (i < text.length) {
      const open = text.indexOf('{', i);
      if (open === -1) break;
      const prelude = text.slice(i, open).trim();
      const close = consumeBlock(text, open);
      const body = text.slice(open + 1, close);

      if (prelude.startsWith('@')) {
        const name = prelude.split(/\s/)[0].toLowerCase();
        if (name !== '@keyframes' && name !== '@-webkit-keyframes' && name !== '@font-face') {
          parseInto(body, prelude);
        }
      } else if (prelude) {
        const decls = {};
        for (const part of body.split(';')) {
          const c = part.indexOf(':');
          if (c === -1) continue;
          const prop = part.slice(0, c).trim().toLowerCase();
          const val = part.slice(c + 1).trim();
          if (prop && val && !prop.startsWith('/')) decls[prop] = val;
        }
        rules.push({
          selectors: prelude.split(',').map((s) => s.trim()).filter(Boolean),
          decls,
          at: atName || null,
        });
      }
      i = close + 1;
    }
  };

  parseInto(src, null);
  return rules;
}

/**
 * Does `selector` match `el`, judging by its RIGHTMOST compound only?
 * (Approximation #1 in the header comment.) Pseudo-classes and pseudo-elements
 * are stripped first, so `.x:hover` and `.x` both match `.x` -- callers that
 * care about resting state must filter on the raw selector themselves.
 */
function selectorMatchesElement(selector, el) {
  const parts = selector.split(/\s+|>|\+|~/).filter(Boolean);
  if (!parts.length) return false;
  const compound = parts[parts.length - 1].replace(/::?[a-zA-Z-]+(\([^)]*\))?/g, '');
  if (!compound || compound === '*') return compound === '*';

  const tokens = compound.match(/[#.]?[-a-zA-Z0-9_]+/g) || [];
  for (const tok of tokens) {
    if (tok.startsWith('#')) { if (el.attrs.id !== tok.slice(1)) return false; }
    else if (tok.startsWith('.')) { if (!el.classes.includes(tok.slice(1))) return false; }
    else if (tok.toLowerCase() !== el.tag) return false;
  }
  return tokens.length > 0;
}

/**
 * Every value `prop` takes for `el` across the sheet, plus any inline style.
 * Returns [{value, source}] so a failure message can name where a size came
 * from. Rules carrying a state pseudo-class (:hover/:focus/...) are excluded --
 * a resting-size comparison must not be perturbed by a hover rule.
 */
function declaredValues(rules, el, prop, inlineStyle) {
  const out = [];
  for (const rule of rules) {
    if (!(prop in rule.decls)) continue;
    for (const sel of rule.selectors) {
      if (/::?(hover|focus|focus-visible|focus-within|active|disabled|checked)\b/.test(sel)) continue;
      if (selectorMatchesElement(sel, el)) {
        out.push({ value: rule.decls[prop], source: sel + (rule.at ? ` (${rule.at})` : '') });
        break;
      }
    }
  }
  const style = inlineStyle !== undefined ? inlineStyle : (el.attrs.style || '');
  const m = new RegExp(`(?:^|;)\\s*${prop}\\s*:\\s*([^;]+)`, 'i').exec(style);
  if (m) out.push({ value: m[1].trim(), source: 'inline style' });
  return out;
}

/** Parse `:root { --x: v; }` custom properties out of a stylesheet. */
function parseTokens(cssText) {
  const tokens = {};
  const rules = parseCss(cssText);
  for (const rule of rules) {
    if (!rule.selectors.some((s) => /(^|\s):root\b|^html\[data-theme="light"\]$/.test(s))) continue;
    for (const [k, v] of Object.entries(rule.decls)) {
      if (k.startsWith('--')) tokens[k] = v;
    }
  }
  return tokens;
}

/**
 * Resolve a length expression to the px range it can actually render at:
 * `{min, max}`. Handles px, var() with token lookup and fallback, and clamp().
 * Anything genuinely unresolvable comes back as null so a caller FAILS on it
 * rather than quietly treating it as zero.
 */
function lengthRange(value, tokens, depth) {
  if (value == null) return null;
  if ((depth || 0) > 8) return null;
  const v = String(value).trim();

  const clamp = /^clamp\(\s*(.+)\s*\)$/i.exec(v);
  if (clamp) {
    const args = splitTopLevel(clamp[1]);
    if (args.length !== 3) return null;
    const lo = lengthRange(args[0], tokens, (depth || 0) + 1);
    const hi = lengthRange(args[2], tokens, (depth || 0) + 1);
    if (!lo || !hi) return null;
    // CSS guarantees the result never leaves [lo, hi]; the preferred middle
    // term is viewport-dependent and deliberately not evaluated.
    return { min: lo.min, max: hi.max };
  }

  const varMatch = /^var\(\s*(--[-a-zA-Z0-9_]+)\s*(?:,\s*([\s\S]+))?\)$/.exec(v);
  if (varMatch) {
    const tokenVal = tokens ? tokens[varMatch[1]] : undefined;
    if (tokenVal !== undefined) return lengthRange(tokenVal, tokens, (depth || 0) + 1);
    if (varMatch[2] !== undefined) return lengthRange(varMatch[2], tokens, (depth || 0) + 1);
    return null;
  }

  const px = /^(-?\d*\.?\d+)px$/i.exec(v);
  if (px) { const n = parseFloat(px[1]); return { min: n, max: n }; }

  const unitless = /^(-?\d*\.?\d+)$/.exec(v);
  if (unitless) { const n = parseFloat(unitless[1]); return { min: n, max: n }; }

  return null;
}

/** Split "a, b, c" on top-level commas only (parens are honoured). */
function splitTopLevel(str) {
  const out = [];
  let depth = 0;
  let cur = '';
  for (const ch of str) {
    if (ch === '(') depth++;
    else if (ch === ')') depth--;
    if (ch === ',' && depth === 0) { out.push(cur.trim()); cur = ''; continue; }
    cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

/**
 * The full px range an element's font-size can render at, across every rule
 * that touches it. Returns null (never a guess) if any contributing value
 * cannot be resolved, so callers fail loudly instead of comparing against 0.
 */
function fontSizeRange(rules, el, tokens) {
  const decls = declaredValues(rules, el, 'font-size');
  if (!decls.length) return null;
  let min = Infinity;
  let max = -Infinity;
  const sources = [];
  for (const d of decls) {
    const r = lengthRange(d.value, tokens);
    if (!r) return null;
    min = Math.min(min, r.min);
    max = Math.max(max, r.max);
    sources.push(`${d.value} [${d.source}]`);
  }
  return { min, max, sources };
}

/**
 * font-size as it actually RESOLVES, walking up the ancestor chain the way
 * inheritance does.
 *
 * This matters: an amount is very often a bare <span class="money"> whose size
 * comes from the row or card around it. Reading only the element's own rules
 * would report "unknown" for those and quietly drop them from a
 * largest-element comparison — which is exactly how a size regression would
 * slip through unnoticed. Returns null only when NOTHING in the chain declares
 * a size, i.e. the element genuinely inherits the shell's base type; callers
 * must bound that case explicitly rather than treating it as zero.
 */
function effectiveFontSizeRange(rules, el, tokens) {
  let node = el;
  while (node && node.type === 'element') {
    const own = fontSizeRange(rules, node, tokens);
    if (own) {
      return node === el
        ? own
        : { min: own.min, max: own.max, sources: own.sources.map((s) => s + ' (inherited)') };
    }
    node = node.parent;
  }
  return null;
}

/**
 * CSS properties that pin a box to a PHYSICAL edge and therefore do not mirror
 * when the document flips to RTL.
 *
 * This product ships Arabic. The stated direction is that RTL should be correct
 * BY CONSTRUCTION -- logical properties in the rule itself -- rather than by a
 * second stylesheet chasing the first and fixing up whatever it missed. Chasing
 * is how rtl.css ended up with a hand-written rule for a chat-bubble corner
 * radius that a logical property would have handled for free.
 *
 * `width`/`height` are deliberately NOT flagged: in a horizontal writing mode
 * they map exactly onto inline-size/block-size and mirror correctly already.
 * Only the direction-bearing properties are hazards.
 */
const PHYSICAL_DIRECTION_PATTERNS = [
  [/\b(?:margin|padding)-(?:left|right)\s*:/g, 'physical margin/padding (use margin-inline-* / padding-inline-*)'],
  [/\bborder-(?:left|right)(?:-[a-z]+)?\s*:/g, 'physical border side (use border-inline-*)'],
  [/(?:^|[;{\s"])(?:left|right)\s*:\s*[^;"}]+/gm, 'physical inset (use inset-inline-*)'],
  [/text-align\s*:\s*(?:left|right)\b/g, 'physical text-align (use start / end)'],
  [/\bfloat\s*:\s*(?:left|right)\b/g, 'float (use a logical layout)'],
  [/\bborder-(?:top|bottom)-(?:left|right)-radius\s*:/g, 'physical corner radius (use border-start-start-radius etc.)'],
];

/** Every physical-direction usage in a chunk of CSS, as {snippet, why}. */
function physicalDirectionHits(cssText) {
  const hits = [];
  const css = String(cssText || '');
  for (const [re, why] of PHYSICAL_DIRECTION_PATTERNS) {
    for (const m of css.matchAll(re)) hits.push({ snippet: m[0].trim(), why });
  }
  return hits;
}

module.exports = {
  parseFragment,
  walkElements,
  allElements,
  textOf,
  ownText,
  elementSiblings,
  adjacentSiblings,
  describe,
  parseCss,
  parseTokens,
  selectorMatchesElement,
  declaredValues,
  lengthRange,
  fontSizeRange,
  effectiveFontSizeRange,
  physicalDirectionHits,
};
