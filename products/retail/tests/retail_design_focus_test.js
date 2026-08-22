/**
 * OPERATIONAL CALM — nothing is discoverable by hover alone.
 *
 * WHY THIS TEST EXISTS
 * Two facts about this product decide this rule:
 *
 *   1. A till is driven by KEYBOARD and BARCODE SCANNER at least as often as
 *      by pointer. A scanner is a keyboard: it types a code and an Enter.
 *      An operator working a queue tabs between fields without looking at
 *      the mouse. If the focused control has no visible ring, that operator
 *      is navigating blind.
 *   2. A large share of installs are TOUCHSCREENS, which have no hover state
 *      at all. Any affordance that only appears on :hover is, on those
 *      machines, invisible — permanently.
 *
 * Before this change the stylesheet had 94 distinct :hover selectors and 4
 * :focus-visible selectors. Ninety-one interactive controls gave pointer
 * users feedback and gave keyboard and touch users nothing.
 *
 * WHAT IT ASSERTS, AND WHY THAT SHAPE
 * The property, not a list: EVERY selector that has a :hover rule and is a
 * focusable control must also have a :focus-visible rule. Because it is
 * derived from the stylesheet's own :hover rules rather than a fixed list,
 * adding a new hover-styled button tomorrow puts it under test
 * automatically — which is the only way a rule like this survives contact
 * with a codebase.
 *
 * Purely decorative hover targets are excluded: a glow layer, an arrow, an
 * icon inside a button, a ::before. Those receive :hover as descendants of a
 * control but can never receive focus themselves, so demanding a ring on
 * them would be noise that trains people to add exemptions. The classifier
 * looks at the LAST COMPOUND of the selector (the element actually hovered)
 * and matches whole hyphen-segments, never substrings — a substring test
 * wrongly excluded `.sub-btn-primary` and `.sub-nav-item`, the two most
 * important controls in the shell, because the token "sub" appeared inside
 * them.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_design_focus_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const CSS_FILE = path.join(__dirname, '..', 'frontend', 'css', 'main.css');

/* Whole-segment vocabularies. A control is named for what it does. */
const CONTROL_SEGMENTS = new Set(['btn', 'button', 'link', 'item', 'tab', 'card', 'toggle',
  'close', 'cta', 'chip', 'row', 'option', 'opt', 'input', 'select', 'send', 'submit', 'tile',
  'zone', 'swatch', 'action', 'entry', 'handle', 'step', 'cell', 'picker', 'menu', 'nav',
  'control', 'search', 'upload', 'sugg', 'refresh', 'accept', 'reject', 'end', 'back', 'exit',
  'import']);

/* If the hovered element's name ENDS in one of these, it is a decorative
   layer inside a control rather than the control itself. */
const DECORATIVE_TAILS = new Set(['bg', 'glow', 'arrow', 'ring', 'shine', 'overlay', 'halo',
  'icon', 'svg', 'line', 'dot', 'img', 'thumb', 'track', 'fill', 'indicator', 'underline']);

const ELEMENT_CONTROL = /(^|[\s>+~])(button|a|input|select|textarea|summary)([\s.:[]|$)/i;

function stripComments(css) {
  // A comment containing a comma glues itself to the first selector of the
  // following rule otherwise, and that selector silently fails to register --
  // which showed up as a phantom "missing focus ring" on the first selector
  // of the focus block itself.
  return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

function lastCompound(selector) {
  const parts = selector.split(/[\s>+~]+/).filter(Boolean);
  return parts[parts.length - 1] || selector;
}

function isFocusableControl(selector) {
  if (/::(before|after|marker|placeholder|selection)/.test(selector)) return false;
  const last = lastCompound(selector);
  const bare = last.replace(/^[.#]/, '').replace(/[:[].*$/, '');
  const segments = bare.split(/[-_.]/).filter(Boolean);
  if (segments.length && DECORATIVE_TAILS.has(segments[segments.length - 1].toLowerCase())) return false;
  if (/^(svg|path|i|span|em|strong)$/i.test(bare)) return false;
  if (ELEMENT_CONTROL.test(last)) return true;
  if (/\[role=["']?button|\[tabindex/.test(selector)) return true;
  return segments.some((s) => CONTROL_SEGMENTS.has(s.toLowerCase()));
}

function collectStates() {
  const css = stripComments(fs.readFileSync(CSS_FILE, 'utf8'));
  const hover = new Set();
  const focusVisible = new Set();
  css.replace(/([^{}]+)\{/g, (match, selectorList) => {
    selectorList.split(',').forEach((raw) => {
      const s = raw.trim();
      if (!s || s.startsWith('@')) return;
      if (/:hover\b/.test(s)) hover.add(s.replace(/:hover\b/g, '').trim());
      if (/:focus-visible\b/.test(s)) focusVisible.add(s.replace(/:focus-visible\b/g, '').trim());
    });
    return match;
  });
  return { hover, focusVisible };
}

function testEveryHoverableControlHasAFocusRing() {
  const { hover, focusVisible } = collectStates();

  // ANTI-VACUITY: the assertion below is a filter over `hover`. If the parser
  // broke, or the classifier stopped matching anything, the filter would
  // return an empty array and this test would report success while comparing
  // nothing at all. Assert the check CAN run before asserting it passes.
  assert.ok(
    hover.size >= 50,
    `Expected to parse at least 50 :hover selectors, found ${hover.size}. ` +
    'The CSS parse is broken, so a green result here would be meaningless.'
  );
  const controls = [...hover].filter(isFocusableControl);
  assert.ok(
    controls.length >= 40,
    `Expected at least 40 of the ${hover.size} hover selectors to classify as ` +
    `focusable controls, got ${controls.length}. The classifier is broken — a ` +
    'vocabulary that matches nothing turns this whole test into a no-op.'
  );

  const missing = controls.filter((s) => !focusVisible.has(s));
  assert.deepStrictEqual(
    missing, [],
    `${missing.length} interactive control(s) style :hover but not :focus-visible:\n  ` +
    missing.join('\n  ') +
    '\n\nOn a touchscreen these have NO discoverable state at all, and a ' +
    'keyboard or barcode-scanner operator cannot see where focus is. Add a ' +
    ':focus-visible rule alongside the :hover rule.'
  );
  console.log(
    `PASS: all ${controls.length} hoverable controls have a :focus-visible ring ` +
    `(${hover.size - controls.length} decorative hover targets correctly excluded)`
  );
}

function testFocusRingIsActuallyVisible() {
  /* A :focus-visible rule that sets `outline: none` is worse than no rule --
     it removes the browser default and replaces it with nothing. */
  const css = stripComments(fs.readFileSync(CSS_FILE, 'utf8'));
  const offenders = [];
  const ruleRe = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = ruleRe.exec(css)) !== null) {
    const [, sel, body] = m;
    if (!/:focus-visible\b/.test(sel)) continue;
    const killsOutline = /outline\s*:\s*(none|0)\b/.test(body);
    const restores = /(outline(-color|-width|-style|-offset)?\s*:\s*(?!none|0\b)|box-shadow\s*:|border-color\s*:|background\s*:)/.test(body);
    if (killsOutline && !restores) {
      offenders.push(sel.trim().slice(0, 90));
    }
  }
  assert.deepStrictEqual(
    offenders, [],
    'Rule(s) remove the focus outline without providing a replacement:\n  ' +
    offenders.join('\n  ') +
    '\n\nThis is strictly worse than no rule: it deletes the browser default ' +
    'and puts nothing in its place.'
  );
  console.log('PASS: no :focus-visible rule removes the outline without replacing it');
}

function testFocusRingIsTokenisedSoItCanAdaptToDarkBrandSurfaces() {
  /* The focus ring colour must be a token, not a literal, because the same
     ring has to sit on a near-white till surface AND on the near-black
     pre-login canvas. Tokenising lets those containers re-point the token
     instead of a second stylesheet chasing every selector. */
  const css = fs.readFileSync(CSS_FILE, 'utf8');
  assert.ok(
    /--focus-ring-color\s*:/.test(css),
    'Expected a --focus-ring-color token so the ring can adapt per surface.'
  );
  const darkOverride = /(\.ws-overlay|#page-landing|#page-login|\.auth-overlay)[^{]*\{[^}]*--focus-ring-color\s*:/.test(css);
  assert.ok(
    darkOverride,
    'The pre-login brand surfaces sit on a near-black canvas where the ' +
    'accent-blue ring is roughly 2:1 and effectively invisible. Re-point ' +
    '--focus-ring-color inside those containers.'
  );
  console.log('PASS: the focus ring is tokenised and re-pointed on the dark pre-login surfaces');
}

function testPrimaryActionsMeetTouchTargetMinimum() {
  /* 44px is the WCAG 2.5.5 / platform-HIG floor for a finger. A till with
     28px desktop-mouse buttons is unusable on the touchscreens a large share
     of these installs run on. */
  const css = fs.readFileSync(CSS_FILE, 'utf8');
  assert.ok(
    /--touch-target-min\s*:\s*44px/.test(css),
    'Expected --touch-target-min: 44px in the token block.'
  );
  const users = (css.match(/min-height\s*:\s*var\(--touch-target-min\)/g) || []).length;
  assert.ok(
    users >= 2,
    `--touch-target-min is defined but applied in only ${users} place(s). ` +
    'Primary actions and nav rows both need a real finger target.'
  );
  console.log(`PASS: --touch-target-min is 44px and applied to ${users} control groups`);
}

function main() {
  testEveryHoverableControlHasAFocusRing();
  testFocusRingIsActuallyVisible();
  testFocusRingIsTokenisedSoItCanAdaptToDarkBrandSurfaces();
  testPrimaryActionsMeetTouchTargetMinimum();
  console.log('PASS: retail_design_focus_test.js');
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_design_focus_test.js');
  console.error(err.message || err);
  process.exitCode = 1;
}
