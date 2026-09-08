/**
 * OPERATIONAL CALM — WCAG contrast, at BOTH the level the palette declares and
 * the level the cashier actually sees.
 *
 * WHY THIS TEST EXISTS
 * The retail frontend previously shipped a near-black "HUD" palette
 * (--bg-dark:#080808, neon accents) whose light-theme override was added
 * later and incompletely. That produced real, shipped contrast bugs: the
 * dashboard's Recent Transactions cells rendered ~2.64:1 grey-on-white
 * (see retail_dashboard_transaction_contrast_test.js), and --text-faint
 * resolved to #8794a8 = ~2.9:1 on a white card. Both passed code review,
 * because contrast is exactly the property a human eye cannot audit by
 * looking -- especially on the developer's calibrated monitor rather than
 * the cheap fluorescent-lit shop monitor the till actually runs on.
 *
 * WHY IT WAS NOT ENOUGH, WHICH IS THE MORE IMPORTANT HALF
 * The first version of this file computed contrast over the TOKEN BLOCK: every
 * --text-* against every --surface-*. That property is true and is still
 * asserted below. It was also worth nothing to the two worst pairings in the
 * product, because neither of them is made of tokens:
 *
 *     POS "Held" button                     1.48:1
 *     Recent Transactions payment badges    1.91 - 2.20:1
 *
 * Both are WORSE than the 2.64:1 trap the pass that wrote this test was
 * celebrated for fixing, on the same two screens, and this file went green.
 * They come from `RetailSystem._injectStyles()` in subsystem-retail.js -- a
 * stylesheet injected into <head> at runtime, invisible to a token scan, and
 * because it is injected AFTER main.css it beats the token layer at equal
 * specificity. So the headline claim "every pairing clears AA" was true of the
 * token block and false of the screen.
 *
 * That is not a missing assertion. It is a test whose SCOPE manufactured the
 * state that hid the bug -- and a token block is a scope that can never see an
 * injected literal, no matter how many tokens get added to it.
 *
 * WHAT IT ASSERTS NOW — EIGHT PROPERTIES, DELIBERATELY DIFFERENT ONES
 *
 *  1. THE PALETTE (unchanged, still a cross product over the token names):
 *       every --text-* x every solid --surface-*  >= 4.5:1 (AA)
 *       every --text-money* x every solid --surface-*  >= 7.0:1 (AAA)
 *       --text-on-accent x every --accent-action* fill >= 4.5:1 (AA)
 *     Money gets the stricter floor because a misread total is a till's worst
 *     failure mode. Derived from token NAMES, so the palette cannot quietly
 *     grow a failing member.
 *
 *  2. THE SCREEN. Every element that paints text on a RENDERED till screen, in
 *     EVERY state the stylesheets can paint in -- resting, hovered, focused
 *     (:focus/:focus-visible/:focus-within), active, checked and disabled --
 *     with its colour and its surface resolved through the real cascade and the
 *     real ancestor chain, alpha fills composited down to the opaque surface
 *     underneath. This is the tier that catches an injected literal, because it
 *     never asks where a colour came from.
 *     See retail_design_render_test.js for the corpus and the cascade engine.
 *
 *     The state list used to be {resting, hovered} while the chrome ledger's
 *     notion of "exercised" spanned five states, so a rule that won only in
 *     `:active` counted as measured by a tier that never entered `:active`.
 *     One list now, derived, and checked against the stylesheets themselves.
 *     See EVALUATED_STATES and testEveryStateTheCascadeUsesIsEvaluated.
 *
 *  3. NOTHING RESOLVES TO "UNKNOWN" IN SILENCE. A pairing whose colour or
 *     surface cannot be determined statically is COUNTED and NAMED, never
 *     skipped. A quietly growing population of unresolvable pairings is
 *     precisely how the two bugs above survived a green suite.
 *
 *  4. A MONEY SEMANTIC CANNOT BE SILENTLY OVERRIDDEN. `_money()` returns a
 *     nested <span class="money">, so a container that has already declared a
 *     money colour holds no text of its own and `.money` re-declares colour on
 *     the child. `.rdash-bd-value.is-in` was dead from the day it was written
 *     and the dashboard's Sales figure rendered neutral instead of money-in
 *     green. The selector matched; there was simply nothing to paint.
 *
 *  5. THE BADGE FAMILY IS SELF-CONTAINED. Badges appear on white cards, on
 *     hovered rows AND inside the dark .ret-modal, and the corpus can only ever
 *     render the two payment variants. So every variant the injected sheet
 *     declares must carry an opaque token pair, checked here by derivation from
 *     that sheet rather than from a list somebody has to remember to extend.
 *
 *  6. AND THE CHROME RULES THE CORPUS NEVER REACHES ARE MEASURED ANYWAY. The
 *     unexercised ledger used to compute each rule's real contrast ratio, PRINT
 *     it -- "would be 1.00:1 on --surface-till", in those words -- and then
 *     assert only that the list was no longer than a budget. Six chrome fixes
 *     therefore had no guard at all, and reverting any of them was silent on a
 *     green tree. The ratio is now the assertion; the count stays alongside it,
 *     because "the blind spot grew" is a different fact from any one ratio.
 *
 *  7. ALL OF IT, AGAIN, FOR EVERY NON-LIGHT THEME. Since 2026-09 the product
 *     ships four more sanctioned themes beyond base Light: Dark, then (a
 *     second owner request the same month) Night, Dusk and Sand -- each its
 *     own html[data-theme="<name>"] block in main.css, token VALUES only,
 *     zero theme-scoped rules. Property 1 therefore re-runs on each theme's
 *     merged map (same cross-products, same AA/AAA floors), property 2
 *     re-resolves the ENTIRE rendered corpus through each theme's values
 *     (sound because the rule table and cascade winners are theme-independent
 *     by construction -- only the map changes, exactly as in the browser),
 *     the badge self-containment re-checks with each theme's state pairs, and
 *     a polarity check refuses the one cheap green: a block that is a paste
 *     of the light values (or, for Sand -- a light theme -- a paste of a dark
 *     one). The first dark theme died of a failure no token scan could see;
 *     these per-theme rendered tiers are the check that would have caught it.
 *
 *  8. THE FIVE THEMES MUST BE DISTINGUISHABLE FROM EACH OTHER, not merely
 *     individually contrast-compliant. Properties 1-7 all measure a theme
 *     against ITSELF; none of them can catch two themes converging on each
 *     other, which is exactly what happened when Night's accent moved off
 *     teal to a lapis/indigo that landed 7.02 ΔE76 from Dusk's lavender --
 *     every AA/AAA guard above stayed green throughout. See
 *     testThemeAccentsAreDistinguishable() for the ΔE76 threshold and why.
 *
 * ANTI-VACUITY
 * Every assertion here is a loop. A loop over nothing passes. So the palette
 * tier asserts it parsed a realistic number of tokens before asserting they
 * pass, and the rendered tiers lean on retail_design_render_test.js, which
 * asserts the corpus reaches the specific elements the defects were measured
 * on. The check RUNNING is asserted separately from the check PASSING -- down
 * to the runner itself, which asserts it attempted every check it knows about
 * before reporting that none of them failed.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_design_contrast_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const render = require('./retail_design_render_test.js');

const CSS_FILE = path.join(__dirname, '..', 'frontend', 'css', 'main.css');

const AA = 4.5;   // WCAG 2.2 §1.4.3 -- normal-size body text
const AAA = 7.0;  // WCAG 2.2 §1.4.6 -- enhanced; required here for money

/* ── THE STATES A PAIRING IS EVALUATED IN ───────────────────────────────────
 *
 * ONE list. It used to be two, and they disagreed.
 *
 * The measuring tier ran {resting, hovered}. The chrome ledger's notion of
 * "exercised" ran {resting, hover, focus/focus-visible, active, disabled}. A
 * rule that won the cascade ONLY in `:active` therefore counted as exercised --
 * and the comment on that ledger said, in as many words, that exercised means
 * "the rendered tier has measured this" -- while the rendered tier never
 * entered `:active` at all. Adding
 *
 *     .ret-btn-danger:active { color:#ffffff; background:#fefefe }
 *
 * renders the Delete/Discard button at 1.01:1 and was SILENT on a fully green
 * suite, BECAUSE it counted as exercised. That is the same matched-but-not-won
 * laundering the ledger was rewritten to remove, relocated one level up into the
 * state dimension. Re-measured against this list: it now fails on SEVEN screens
 * (products, customers, suppliers, categories, the customer modal, the
 * held-sales modal, and the sale modal) -- and re-measured against the old
 * two-state tier with everything else here unchanged, it goes green again at
 * "all 1056 RENDERED pairings ... reach AA" while the ledger reports it as one
 * MORE rule exercised. Both directions were run; that is what the number 1056
 * is doing in this comment.
 *
 * `:focus-within` was in NEITHER set, so a focus band at 1.02:1 was reachable by
 * neither tier and surfaced only as a +1 on a budget -- the kind of finding a
 * one-line budget raise disposes of.
 *
 * So CHROME_STATES is now DERIVED from this list (see below) and cannot drift
 * from it, and testEveryStateTheCascadeUsesIsEvaluated() asserts this list
 * covers every state pseudo-class any colour, background or opacity rule in the
 * cascade actually uses. Adding `:visited { color: ... }` to a stylesheet fails
 * that check by name rather than quietly opening a state nothing looks at. That
 * second check is what makes the mismatch impossible to EXPRESS rather than
 * merely fixed once: shrinking this list back to {resting, hovered} does not
 * restore the old silence, it fails, naming all six states it just abandoned.
 *
 * ON THE OVER-APPROXIMATION. Each pass treats EVERY element as being in the
 * state at once, which is not a configuration the DOM can hold. It is sound for
 * this question: a contrast resolution reads only the element's own ancestor
 * chain, and :hover / :focus-within genuinely do apply to an element AND all of
 * its ancestors simultaneously. The over-approximation is therefore confined to
 * siblings, whose backgrounds have no bearing on this element's text.
 *
 * ON THE DISABLED FLOOR, which is the one exception and is stated rather than
 * hidden. WCAG 2.2 §1.4.3 exempts "text ... that is part of an inactive user
 * interface component" from the contrast minimum, so the product's disabled
 * buttons (measured 1.84:1 and 2.00:1, all of them a documented `opacity` wash
 * over an AA-clearing resting pair) are conformant, not defects. Those pairings
 * are still RESOLVED -- an unresolvable one still fails -- still counted, and
 * their tightest ratio is printed every run, so a regression is visible. They
 * are simply not held to a floor that does not apply to them. `floor: null`
 * means "measured and reported, deliberately not asserted"; it never means
 * "skipped".
 */
const EVALUATED_STATES = [
  { name: 'resting', states: new Set(), floor: AA },
  { name: 'hovered', states: new Set(['hover']), floor: AA },
  // :focus and :focus-visible travel together because a keyboard-focused
  // element is both; :focus-within is added to the same pass because the
  // element that HAS focus is also within itself, and its ancestors are the
  // only other elements the rule can reach.
  { name: 'focused', states: new Set(['focus', 'focus-visible', 'focus-within']), floor: AA },
  { name: 'active', states: new Set(['active']), floor: AA },
  { name: 'checked', states: new Set(['checked']), floor: AA },
  { name: 'disabled', states: new Set(['disabled']), floor: null },
];

/* The states a chrome rule can legitimately win in -- DERIVED, never restated.
   Evaluated separately and never unioned: a single set containing both `hover`
   and `disabled` would let a rule that only ever applies to a disabled control
   count as exercised by a hovered one, which is not a state any element is ever
   in. The point of deriving it is that "exercised" can no longer name a state
   the measuring tier does not enter. */
const CHROME_STATES = EVALUATED_STATES.map((s) => s.states);

/* State names whose pairings carry no asserted floor (see the WCAG note above).
   Used by the chrome ledger too, so a `:disabled` colour rule that the corpus
   never exercises is not held to a floor WCAG does not impose on it. */
const UNFLOORED_STATE_NAMES = new Set(
  EVALUATED_STATES.filter((s) => s.floor === null).flatMap((s) => [...s.states])
);

/* How many colour-declaring rules in the shared retail chrome the RENDERED tier
   does not exercise -- i.e. how many never WIN the cascade on a rendered
   character. Each is printed by name, with the ratio it would measure and the
   reason it is not exercised, every single run.

   RAISING THIS NUMBER IS A REVIEWABLE ACT. It means the corpus's blind spot
   grew, which is the exact drift that let a 1.48:1 button ship under a green
   suite. Lowering it by rendering another screen is always the better move.

   26 -> 25, reviewed 2026-08-23, and the arithmetic behind that one-line move
   is worth reading because two large changes landed at once and nearly
   cancelled out:

     * THE CORPUS GREW from 3 screens to 13 -- Sales History, Returns, Purchase
       Orders, Products, Customers, Suppliers, the audit log, and the customer /
       sale-detail / held-sales modals. On its own that took the old
       "did anything match it" count from 26 to 16.
     * THE ACCOUNTING WAS FIXED. "Exercised" used to mean MATCHED, and matching
       is not painting. `.ret-table { color:#fff }` matched the dashboard's
       table, so it was filed as covered -- while on that one screen
       `.rdash .ret-table` outranked it and the white never reached the glass.
       The rendered tier proved nothing about it, the ledger said it needed no
       attention, and five list screens shipped white text on white cards.
       Counting only rules that WIN put 9 rules back on the list: 16 -> 25.

   The nine that entered are named in the printed output as
   "OUTRANKED on every rendered element it matches", and they are the dangerous
   half of the list, not the harmless half. `.ret-btn-ghost { color:#cbd5e1 }`
   is among them -- the exact literal behind the reported 1.48:1 "Held" button.
   It is off the glass today only because `button.ret-btn.ret-btn-ghost` in
   css/main.css outranks it at (0,2,1); nothing about the chrome sheet itself
   changed, and an edit to that main.css rule hands the 1.48:1 version straight
   back. The old accounting called that "exercised".

   25 -> 23, reviewed 2026-08-23, and this one is a plain LOWERING with no
   offsetting change hidden inside it: closing the corpus against the router
   added the Reports, Categories and Scanner screens, and Reports is the only
   place in the product that renders the `.ret-kpi` family. `.ret-kpi-label` and
   `.ret-kpi-value` moved from "no rendered element matches it" to exercised.
   Nothing left the exercised set.

   The rest of the list is genuinely unreachable from this corpus: the remaining
   KPI members (`-sub`, `-breakdown-item`, `-change-up/-down`, which the
   dashboard's own KPI markup does not use), the PO split preview, the supplier
   tabs, and the `.ret-field`/`.ret-search`/`.ret-input` inputs -- whose value is
   real text an operator reads, but lives in an attribute, so this DOM model has
   no character to hang a pairing on.

   AND EVERY ONE OF THEM IS NOW MEASURED. The count below is no longer the only
   claim this ledger makes: each unexercised rule's real contrast ratio is
   computed against the till's own surfaces and held to AA. See
   testUnexercisedChromeIsCountedNotAssumedFine -- for two rounds the ratios
   were computed, printed, and asserted on by nothing, which is why six chrome
   fixes could be reverted in silence. */
const UNEXERCISED_CHROME_BUDGET = 23;

/* ── Token parsing ─────────────────────────────────────────────────────────
   Only the block between the [design-tokens:begin]/[design-tokens:end]
   markers is the palette. Reading the whole file would sweep up the legacy
   bridge aliases (which are var() indirections, not colours) and every
   unrelated literal further down. */
/* The begin marker lives INSIDE the block's header comment, so a slice taken
   from the marker starts mid-comment: its `/*` opener is behind the slice, the
   comment-stripping regex cannot see the body, and the header PROSE leaks into
   the parse as plain text. That was not cosmetic: a `--name: ...;`-shaped run
   of prose swallowed the real `--surface-app` declaration behind it, so tier 1
   crossed 7 surfaces while the palette declares 8 -- silently, for as long as
   the marker scheme has existed (the anti-vacuity floor is >=5, so nothing
   objected). Dropping everything up to the header comment's own closer makes
   the slice start at real CSS. */
function afterHeaderComment(slice) {
  const close = slice.indexOf('*/');
  return close === -1 ? slice : slice.slice(close + 2);
}

function readTokenBlock() {
  const css = fs.readFileSync(CSS_FILE, 'utf8');
  const start = css.indexOf('[design-tokens:begin]');
  const end = css.indexOf('[design-tokens:end]');
  assert.ok(start !== -1, 'main.css is missing the [design-tokens:begin] marker');
  assert.ok(end !== -1, 'main.css is missing the [design-tokens:end] marker');
  assert.ok(end > start, '[design-tokens:end] appears before [design-tokens:begin]');
  return afterHeaderComment(css.slice(start, end));
}

/* Every non-light theme's whole existence is the html[data-theme="<name>"]
   block between its markers: token VALUES only, no rules, so every check that
   holds for the light palette must hold for these values on the same rule
   set. The merged map returned here is light OVERLAID with the theme, which
   is exactly the cascade a document in that theme resolves -- and it is also
   what makes a MISSING override self-detecting: a --surface-* a theme block
   forgot stays light-valued, dark text lands on it, and the AA/AAA
   cross-products below go red rather than quietly testing a smaller palette.
   ('dark' uses the original [design-tokens-dark:*] markers; night/dusk/sand
   each get their own [design-tokens-<name>:*] pair.) */
function readThemeTokenBlock(name) {
  const css = fs.readFileSync(CSS_FILE, 'utf8');
  const beginMarker = `[design-tokens-${name}:begin]`;
  const endMarker = `[design-tokens-${name}:end]`;
  const start = css.indexOf(beginMarker);
  const end = css.indexOf(endMarker);
  assert.ok(start !== -1, `main.css is missing the ${beginMarker} marker — the ${name} theme has no palette to test`);
  assert.ok(end > start, `${endMarker} is missing or appears before its begin marker`);
  return afterHeaderComment(css.slice(start, end));
}

function themeTokenMaps(lightTokens, name) {
  const overrides = parseTokens(readThemeTokenBlock(name));
  assert.ok(
    overrides.size >= 30,
    `The ${name} token block defines only ${overrides.size} tokens. The light palette has ` +
    `over 30 colour tokens; a ${name} block this thin means most of the theme still ` +
    `resolves to light values, i.e. the ${name} theme is mostly not a theme.`
  );
  const merged = new Map(lightTokens);
  for (const [k, v] of overrides) merged.set(k, v);
  return { overrides, merged };
}

// Never written as a literal in this file: the whole subject below is a
// two-character sequence that ends a CSS comment, and it must not appear in a
// JS string that could later be pasted into a stylesheet comment.
const COMMENT_CLOSE = '*' + '/';

function parseTokens(block) {
  const tokens = new Map();
  // Strip comments first: the token block is heavily commented and a comment
  // containing a `#` or a `;` would otherwise be parsed as a declaration.
  const clean = block.replace(/\/\*[\s\S]*?\*\//g, '');

  // A LEFTOVER CLOSER MEANS A DECLARATION WAS EATEN. Added 2026-09-08 after
  // this function reported a token the browser did not have.
  //
  // The regex above is non-greedy, so it pairs openers and closers the way a
  // CSS parser does -- first close wins. What survives it, then, is prose that
  // leaked OUT of a comment that ended early, and the parser hands that prose
  // to the declaration grammar, where it swallows itself plus whatever follows
  // as one invalid declaration. Measured on main.css that day: a comment
  // describing a token family with a wildcard written immediately before a
  // slash closed four lines early and took `--surface-accent-soft: #eaedf9;`
  // out of :root. Edge reported [] for that token on :root; THIS function,
  // scanning text with no model of declaration boundaries, happily returned
  // "#eaedf9" -- and 3,240 contrast pairings were then computed against a
  // value the product does not have, and printed PASS.
  //
  // A text scanner cannot tell a live declaration from a discarded one. It CAN
  // tell that the comment structure is broken, which is the same signal one
  // step earlier, so it refuses rather than guessing. retail_design_css_parse_
  // test.js implements the structural version of this over whole stylesheets
  // (its testNoCommentClosesEarly, plus a brace-tracking walk that proves which
  // token was lost); this is the cheap local guard for the SLICE this function
  // was handed, which that file's whole-file check does not cover.
  const orphan = clean.indexOf(COMMENT_CLOSE);
  if (orphan !== -1) {
    const line = clean.slice(0, orphan).split('\n').length;
    assert.fail(
      `a stray comment closer survives comment-stripping at line ${line} of this ` +
      `token block: ...${JSON.stringify(clean.slice(Math.max(0, orphan - 70), orphan + 4))}. ` +
      'A CSS comment ends at the FIRST closing sequence, so a surplus closer ' +
      'means a comment ended mid-sentence and the browser discarded the leaked ' +
      'prose together with the next declaration -- while this text scan still ' +
      'reports that declaration present and every contrast pairing below is ' +
      'computed against a value the product does not have. Fix the stylesheet ' +
      '(write "the surface and text tokens", never a star immediately followed ' +
      'by a slash); do not relax this check.');
  }

  const re = /(--[a-z0-9-]+)\s*:\s*([^;]+);/gi;
  let m;
  while ((m = re.exec(clean)) !== null) tokens.set(m[1], m[2].trim());
  return tokens;
}

/* ── Colour maths (WCAG 2.x relative luminance) ───────────────────────────── */
function parseHex(value) {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value.trim());
  if (!m) return null;
  let h = m[1];
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}

function channelLuminance(c) {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}

function relativeLuminance(rgb) {
  const [r, g, b] = rgb.map(channelLuminance);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrastRatio(hexA, hexB) {
  const la = relativeLuminance(parseHex(hexA));
  const lb = relativeLuminance(parseHex(hexB));
  const hi = Math.max(la, lb);
  const lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

/* ── Colour maths (CIE Lab / ΔE76) ─────────────────────────────────────────
   WCAG contrast answers "can text be READ on this surface"; it says nothing
   about whether two SIBLING colours look alike, because two hues at the same
   relative luminance are equally (il)legible against a third colour while
   being visually indistinguishable from each other. §8 below needs the
   second question, so it converts sRGB to CIE L*a*b* (D65 reference white,
   the same illuminant WCAG's formula implicitly assumes) and measures the
   plain Euclidean distance in that space -- CIE76 ΔE, the simplest of the
   standard ΔE formulas and sufficient here because this is a coarse
   "obviously the same colour or not" gate, not a colour-matching tolerance.
   Reuses channelLuminance() above for the linearisation step; sRGB-to-linear
   is the same transform WCAG's relative luminance already needed. */
function toLab(hex) {
  const [r, g, b] = parseHex(hex).map(channelLuminance);
  const X = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b;
  const Y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b;
  const Z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b;
  const Xn = 0.95047, Yn = 1.0, Zn = 1.08883;
  const f = (t) => (t > Math.pow(6 / 29, 3) ? Math.cbrt(t) : t / (3 * Math.pow(6 / 29, 2)) + 4 / 29);
  const fx = f(X / Xn), fy = f(Y / Yn), fz = f(Z / Zn);
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}

function deltaE76(hexA, hexB) {
  const [L1, a1, b1] = toLab(hexA);
  const [L2, a2, b2] = toLab(hexB);
  return Math.sqrt((L1 - L2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2);
}

/* ── Grouping ──────────────────────────────────────────────────────────────
   Derived from naming, so the palette cannot grow an untested member.
   Non-colour tokens that share the --text- prefix (--text-size-*,
   --text-weight-*, --text-leading-*) are excluded by the simple fact that
   their values are not hex colours -- no name allowlist needed. */
function groupTokens(tokens) {
  const textOnSurface = [];
  const money = [];
  const surfaces = [];
  const accentFills = [];
  const nonHexSkipped = [];

  for (const [name, value] of tokens) {
    const isHex = parseHex(value) !== null;

    if (name.startsWith('--surface-')) {
      if (isHex) surfaces.push([name, value]);
      // --surface-scrim is deliberately rgba(): it is a translucent modal
      // backdrop, not a surface text ever sits directly on, so it has no
      // fixed luminance to test against.
      else nonHexSkipped.push(name);
      continue;
    }
    if (name.startsWith('--accent-action')) {
      if (isHex) accentFills.push([name, value]);
      continue;
    }
    if (name.startsWith('--text-')) {
      if (!isHex) { nonHexSkipped.push(name); continue; }
      // --text-on-accent is by definition NOT for use on a surface; it is
      // checked against the accent fills instead, further down.
      if (name === '--text-on-accent') continue;
      if (name.startsWith('--text-money')) money.push([name, value]);
      textOnSurface.push([name, value]);
      continue;
    }
  }
  return { textOnSurface, money, surfaces, accentFills, nonHexSkipped };
}

/* ── Tests ─────────────────────────────────────────────────────────────────*/

function testParseFoundARealPalette(groups, label = 'light') {
  // ANTI-VACUITY GUARD. Every assertion below is a loop over these arrays; if
  // the parse silently returned nothing, those loops would pass without
  // comparing a single colour. Assert the check CAN run before asserting it
  // passes. The floors are deliberately below the current counts so ordinary
  // palette growth does not trip them, but a broken parse (0, or 1) does.
  assert.ok(
    groups.textOnSurface.length >= 4,
    `Expected to parse at least 4 text colour tokens, found ${groups.textOnSurface.length}. ` +
    'The token block or its markers probably changed shape -- every contrast ' +
    'assertion in this file iterates over this list, so a thin parse would ' +
    'make the whole suite pass while checking nothing.'
  );
  assert.ok(
    groups.surfaces.length >= 5,
    `Expected at least 5 solid surface tokens, found ${groups.surfaces.length}.`
  );
  assert.ok(
    groups.money.length >= 3,
    `Expected at least 3 --text-money* tokens, found ${groups.money.length}.`
  );
  assert.ok(
    groups.accentFills.length >= 1,
    `Expected at least 1 --accent-action* fill, found ${groups.accentFills.length}.`
  );

  const pairings = groups.textOnSurface.length * groups.surfaces.length;
  assert.ok(pairings >= 20, `Expected >= 20 text-on-surface pairings, computed ${pairings}.`);

  console.log(
    `PASS: parsed a real ${label} palette — ${groups.textOnSurface.length} text tokens x ` +
    `${groups.surfaces.length} surfaces = ${pairings} pairings, ` +
    `${groups.money.length} money tokens, ${groups.accentFills.length} accent fills`
  );
}

function testEveryTextOnSurfaceReachesAA(groups, label = 'light') {
  const failures = [];
  for (const [tName, tVal] of groups.textOnSurface) {
    for (const [sName, sVal] of groups.surfaces) {
      const ratio = contrastRatio(tVal, sVal);
      if (ratio < AA) {
        failures.push(`${tName} (${tVal}) on ${sName} (${sVal}) = ${ratio.toFixed(2)}:1`);
      }
    }
  }
  assert.deepStrictEqual(
    failures, [],
    `${failures.length} ${label}-theme text-on-surface pairing(s) fall below WCAG AA (${AA}:1).\n  ` +
    failures.join('\n  ') +
    '\n\nEvery --text-* token must be legible on every --surface-* token, ' +
    'because the shell composes them freely (a label lands on a card, a card ' +
    'lands on the shell, a row highlights on hover).'
  );
  console.log(`PASS: all ${groups.textOnSurface.length * groups.surfaces.length} ${label} text-on-surface pairings reach WCAG AA (${AA}:1)`);
}

function testMoneyReachesAAA(groups, label = 'light') {
  const failures = [];
  const worst = { ratio: Infinity, label: '' };
  for (const [tName, tVal] of groups.money) {
    for (const [sName, sVal] of groups.surfaces) {
      const ratio = contrastRatio(tVal, sVal);
      if (ratio < worst.ratio) {
        worst.ratio = ratio;
        worst.label = `${tName} on ${sName}`;
      }
      if (ratio < AAA) {
        failures.push(`${tName} (${tVal}) on ${sName} (${sVal}) = ${ratio.toFixed(2)}:1`);
      }
    }
  }
  assert.deepStrictEqual(
    failures, [],
    `${failures.length} ${label}-theme money pairing(s) fall below WCAG AAA (${AAA}:1).\n  ` +
    failures.join('\n  ') +
    '\n\nMoney is held to AAA, not AA: a misread total is the worst thing a ' +
    'till can do, and amounts get read at a glance and at an angle all shift.'
  );
  console.log(
    `PASS: all ${groups.money.length * groups.surfaces.length} ${label} money pairings reach WCAG AAA (${AAA}:1) ` +
    `— tightest is ${worst.label} at ${worst.ratio.toFixed(2)}:1`
  );
}

function testTextOnAccentReachesAA(tokens, groups, label = 'light') {
  const onAccent = tokens.get('--text-on-accent');
  assert.ok(onAccent, 'Expected a --text-on-accent token (the label colour for accent-filled buttons)');
  assert.ok(parseHex(onAccent), `--text-on-accent must be a hex colour, got "${onAccent}"`);

  const failures = [];
  for (const [aName, aVal] of groups.accentFills) {
    const ratio = contrastRatio(onAccent, aVal);
    if (ratio < AA) failures.push(`--text-on-accent (${onAccent}) on ${aName} (${aVal}) = ${ratio.toFixed(2)}:1`);
  }
  assert.deepStrictEqual(
    failures, [],
    `Accent-filled controls have unreadable labels in the ${label} theme:\n  ${failures.join('\n  ')}\n\n` +
    'These are the primary actions -- "Open POS", "Charge", "Confirm". A ' +
    'primary button nobody can read is worse than no primary button.'
  );
  console.log(`PASS: ${label} --text-on-accent reaches AA on all ${groups.accentFills.length} accent fills`);
}

/* THE DARK PALETTE MUST ACTUALLY BE DARK. Without this, the one edit that
   silences a failing dark pairing without design work -- pasting the light
   values into the dark block -- goes green on every check above: light text
   values on light surfaces pass AA, the parse floors are satisfied, and "dark
   mode" is light mode wearing the attribute. Polarity is the property a copy
   cannot fake: in light the working surface out-luminates its text, in dark
   the text out-luminates its surface. Both directions are asserted, against
   the values, not the names. */
function testDarkPaletteIsActuallyDark(lightTokens, darkMerged, themeLabel = 'dark') {
  const lumOf = (map, name) => {
    const v = map.get(name);
    assert.ok(v && parseHex(v), `${name} missing or not hex ("${v}")`);
    return relativeLuminance(parseHex(v));
  };
  const lightSurface = lumOf(lightTokens, '--surface-till');
  const lightText = lumOf(lightTokens, '--text-primary');
  const darkSurface = lumOf(darkMerged, '--surface-till');
  const darkText = lumOf(darkMerged, '--text-primary');
  const upper = themeLabel.toUpperCase();

  assert.ok(
    lightSurface > lightText,
    `The LIGHT working surface (${lightSurface.toFixed(3)}) is darker than its own body text ` +
    `(${lightText.toFixed(3)}) — the light palette is not light.`
  );
  assert.ok(
    darkText > darkSurface,
    `The ${upper} working surface (luminance ${darkSurface.toFixed(3)}) is not darker than its own ` +
    `body text (${darkText.toFixed(3)}). A ${themeLabel} block whose values render a light screen is ` +
    `light mode wearing data-theme="${themeLabel}" — the exact non-theme this check exists to refuse.`
  );
  assert.ok(
    darkSurface < 0.2,
    `--surface-till resolves to luminance ${darkSurface.toFixed(3)} in ${themeLabel} — that is not a ` +
    'dark surface (the floor here is generous: 0.2 is already a mid grey).'
  );
  console.log(
    `PASS: the ${themeLabel} palette is genuinely dark (till surface luminance ` +
    `${darkSurface.toFixed(3)} vs text ${darkText.toFixed(3)}; light is the inverse)`
  );
}

/* SAND IS A LIGHT THEME, NOT A DARK ONE WEARING PAPER COLOURS. Same polarity
   argument as testDarkPaletteIsActuallyDark, inverted: a light theme's
   working surface must OUT-LUMINATE its own body text, the same way the base
   light palette's does above. */
function testLightFamilyPaletteIsActuallyLight(merged, themeLabel) {
  const lumOf = (name) => {
    const v = merged.get(name);
    assert.ok(v && parseHex(v), `${name} missing or not hex ("${v}")`);
    return relativeLuminance(parseHex(v));
  };
  const surface = lumOf('--surface-till');
  const text = lumOf('--text-primary');
  assert.ok(
    surface > text,
    `The ${themeLabel.toUpperCase()} working surface (luminance ${surface.toFixed(3)}) is not ` +
    `lighter than its own body text (${text.toFixed(3)}) — a light theme needs a light surface ` +
    'and dark text, the same polarity the base light palette has.'
  );
  console.log(
    `PASS: the ${themeLabel} palette is genuinely light (till surface luminance ` +
    `${surface.toFixed(3)} vs text ${text.toFixed(3)})`
  );
}

function testMoneyNegativeIsNotColourAlone() {
  // Direction requirement #3: a negative amount must be distinguishable
  // WITHOUT relying on colour. A red minus sign is invisible to a colourblind
  // cashier and disappears on a washed-out monitor. Assert the rule carries at
  // least one NON-colour differentiator, so the styling survives greyscale.
  const css = fs.readFileSync(CSS_FILE, 'utf8');
  const m = /\.money--negative\s*\{([^}]*)\}/.exec(css);
  assert.ok(m, 'Expected a .money--negative rule in main.css');
  const body = m[1];

  const nonColourCues = [/font-weight\s*:/, /text-decoration\s*:/, /font-style\s*:/, /content\s*:/];
  const found = nonColourCues.filter((re) => re.test(body));
  assert.ok(
    found.length >= 1,
    '.money--negative distinguishes negative amounts by colour ALONE:\n  ' + body.trim() +
    '\n\nA colourblind cashier, or anyone on a washed-out shop monitor, cannot ' +
    'see a red minus. Pair the colour with a non-colour cue (weight, ' +
    'decoration, or an accounting-parenthesis ::before/::after).'
  );
  console.log(`PASS: .money--negative carries ${found.length} non-colour cue(s) alongside its colour`);
}

/* ── TIER 8 — the five themes must be DISTINGUISHABLE from each other ───────
 *
 * Every check above measures a theme against ITSELF: its own text on its own
 * surfaces. None of them can catch two themes converging on EACH OTHER --
 * that is a different question with a different failure shape, and this file
 * shipped it once already. Night's accent moved off an aurora-teal to a
 * light lapis/indigo (#a29efe) that cleared every AA/AAA guard in this file
 * with margin to spare, while landing only 7.02 ΔE76 from Dusk's lavender
 * (#b9a6ff) -- two of five theme-picker entries rendering as the same
 * colour, found only by looking at both themes side by side, not by any
 * number this file computed.
 *
 * THRESHOLD: ΔE76 (CIE 1976, plain Euclidean distance in CIE L*a*b*) >= 12.
 * Chosen from the standard rule-of-thumb bands for this metric: 0-1
 * imperceptible, 1-2 perceptible only on close side-by-side inspection, 2-10
 * perceptible at a glance but still reads as "the same colour, slightly
 * off", 11-49 "more similar than opposite". 12 sits just past where two
 * colours stop reading as variations of one hue, and is calibrated against
 * this file's own history rather than picked in the abstract: the BROKEN
 * Night/Dusk pair measured 7.02 (below the floor, as it must), the
 * CORRECTED pair measures ~29 and Night-vs-Calm (the next closest pair,
 * both being members of the same lapis/indigo family) measures ~15 (both
 * comfortably above), and two pairs that were never in question -- the old
 * aurora-teal vs the old lavender, and the ink-blue Day accent vs the
 * sienna Sand accent -- measure 78 and >=90 respectively. A floor of 12
 * therefore fails the exact bug that motivated it and passes everything
 * that was never broken.
 *
 * ONLY --accent-action is compared: it is the one colour every theme picker
 * preview and every "primary action" surface shows regardless of what
 * screen happens to be open, so it is the pairing a shop owner's eye
 * actually uses to tell two themes apart.
 */
function testThemeAccentsAreDistinguishable(lightTokens, themeMerged) {
  const DELTA_E_FLOOR = 12;
  const accents = { light: lightTokens.get('--accent-action') };
  for (const [name, merged] of Object.entries(themeMerged)) {
    accents[name] = merged.get('--accent-action');
  }
  for (const [name, value] of Object.entries(accents)) {
    assert.ok(value && parseHex(value), `${name}'s --accent-action is missing or not hex ("${value}")`);
  }

  const names = Object.keys(accents);
  assert.ok(names.length >= 5, `Expected 5 themes' worth of --accent-action, got ${names.length}.`);

  const report = [];
  const failures = [];
  for (let i = 0; i < names.length; i++) {
    for (let j = i + 1; j < names.length; j++) {
      const a = names[i], b = names[j];
      const de = deltaE76(accents[a], accents[b]);
      report.push(`${a}/${b}=${de.toFixed(1)}`);
      if (de < DELTA_E_FLOOR) {
        failures.push(
          `${a} (${accents[a]}) vs ${b} (${accents[b]}): ΔE76 ${de.toFixed(2)} — below the ${DELTA_E_FLOOR} floor`
        );
      }
    }
  }
  assert.deepStrictEqual(
    failures, [],
    `${failures.length} theme pair(s) have --accent-action values too close to tell apart:\n  ` +
    failures.join('\n  ') +
    `\n\nAll pairwise separations (ΔE76): ${report.join(', ')}\n\n` +
    'Five themes only earn their maintenance cost if a shop owner can tell them ' +
    'apart in the picker. Move the closer theme\'s accent to a genuinely different ' +
    'hue or lightness -- never lower this floor to let a converged pair pass.'
  );
  console.log(`PASS: all ${report.length} theme-pair --accent-action separations clear ΔE76 >= ${DELTA_E_FLOOR} (${report.join(', ')})`);
}

/* ── TIER 2 — the pairings the app actually renders ────────────────────────── */

/**
 * Every text-painting element on every corpus screen, in every evaluated state,
 * with colour and surface resolved through the real cascade and the real
 * ancestor chain. Returns {pairings, unresolved}.
 */
function resolveRenderedPairings(h) {
  const pairings = [];
  const unresolved = [];
  for (const screen of h.screens) {
    for (const { name: stateName, states, floor } of EVALUATED_STATES) {
      for (const el of h.textPaintingElements(screen.root)) {
        const where = `${screen.name}/${stateName} ${h.describe(el).slice(0, 70)}`;
        const fg = h.effectiveColour(h.ruleTable, el, h.tokens, states);
        const bg = h.effectiveBackground(h.ruleTable, el, h.tokens, states);
        if (fg.unresolved || bg.unresolved) {
          unresolved.push(`${where}\n      ${fg.unresolved || bg.unresolved}`);
          continue;
        }
        // A translucent text colour is painted over its own surface before it
        // is measured -- exactly what the compositor does, and the difference
        // between "rgba(...,0.5) is fine" and the ratio a person sees.
        const painted = fg.colour.a < 1 ? h.composite(fg.colour, bg.colour) : fg.colour;
        // ...and THEN through every `opacity` group the element sits inside.
        // These are two different operations in a fixed order: alpha on the
        // colour channel tints the glyph against its own surface, group opacity
        // fades the glyph AND that surface together toward whatever is behind
        // the group. Skipping the second is how `.pos-card-outofstock`'s
        // "Out of stock" line was reported at 7.79:1 while the browser
        // composites it at 2.37:1.
        const through = h.paintThroughOpacity(h.ruleTable, el, h.tokens, states, painted, bg.colour);
        if (through.unresolved) {
          unresolved.push(`${where}\n      ${through.unresolved}`);
          continue;
        }
        pairings.push({
          where, stateName, floor,
          ratio: h.contrastRatio(through.colour, through.surface),
          colourFrom: fg.from,
          surfaceFrom: bg.layers.map((l) => l.from).join('  over  '),
          alpha: through.alpha,
          opacityFrom: through.groups.map((g) => `opacity:${g.alpha} <- ${g.from}`).join('  in  '),
        });
      }
    }
  }
  return { pairings, unresolved };
}

function describePairing(p) {
  return `${p.ratio.toFixed(2)}:1  ${p.where}\n      colour  <- ${p.colourFrom}\n      surface <- ${p.surfaceFrom}` +
    (p.alpha < 1 ? `\n      washed to ${(p.alpha * 100).toFixed(0)}% by ${p.opacityFrom}` : '');
}

function testEveryRenderedPairingReachesAA(h, rendered, themeLabel = 'light') {
  const { pairings } = rendered;

  // ANTI-VACUITY, and it is PER SCREEN on purpose. A corpus-wide floor of 200
  // was satisfied by three screens and would still be satisfied if most of the
  // sixteen silently stopped resolving, because the four largest carry more
  // than 200 pairings between them. Every screen the corpus declares must
  // contribute, or the number below is an average hiding a hole.
  const perScreen = new Map();
  for (const p of pairings) {
    const screen = p.where.split('/')[0];
    perScreen.set(screen, (perScreen.get(screen) || 0) + 1);
  }
  const silent = h.screens.map((s) => s.name).filter((name) => (perScreen.get(name) || 0) < EVALUATED_STATES.length * 4);
  // ...and per STATE, for the same reason. Six states whose pairings all come
  // from `resting` is a state list that reads as covering :active while
  // resolving nothing in it -- indistinguishable, in the output, from six
  // states that all worked.
  const perState = new Map();
  for (const p of pairings) perState.set(p.stateName, (perState.get(p.stateName) || 0) + 1);
  const silentStates = EVALUATED_STATES.map((s) => s.name).filter((n) => (perState.get(n) || 0) < h.screens.length * 4);
  assert.deepStrictEqual(
    silentStates, [],
    'Evaluated state(s) resolved almost no pairings:\n  ' +
    silentStates.map((n) => `${n}: ${perState.get(n) || 0}`).join('\n  ') +
    '\n\nA state the resolver enters and returns nothing from proves nothing, ' +
    'and reads in the summary line exactly like a state that passed.'
  );
  assert.deepStrictEqual(
    silent, [],
    'Screen(s) contributed almost no resolved pairings:\n  ' +
    silent.map((n) => `${n}: ${perScreen.get(n) || 0}`).join('\n  ') +
    '\n\nA screen that resolves nothing is a screen this tier proves nothing ' +
    'about, and it is indistinguishable from a screen that passes.'
  );

  const floored = pairings.filter((p) => p.floor !== null);
  const failures = floored.filter((p) => p.ratio < p.floor).sort((a, b) => a.ratio - b.ratio);
  assert.deepStrictEqual(
    failures.map(describePairing), [],
    `${failures.length} pairing(s) the till actually RENDERS in the ${themeLabel.toUpperCase()} theme ` +
    `fall below WCAG AA (${AA}:1):\n  ` +
    failures.map(describePairing).join('\n  ') +
    '\n\nThis tier does not care where a colour came from -- a token, a literal ' +
    'in an injected stylesheet, an inline style attribute, or a perfectly legible ' +
    'pair inside an `opacity` group -- they all land on the same screen. Fix the ' +
    'colour, not the scope of the test.'
  );

  const worst = floored.reduce((a, b) => (b.ratio < a.ratio ? b : a));
  const washed = pairings.filter((p) => p.alpha < 1).length;
  console.log(
    `PASS: all ${floored.length} RENDERED ${themeLabel} pairings across ${h.screens.length} screens ` +
    `x ${EVALUATED_STATES.filter((s) => s.floor !== null).length} floored states reach AA (${AA}:1) — tightest is ` +
    `${worst.ratio.toFixed(2)}:1 at ${worst.where.split(' ')[0]}; ${washed} of them ` +
    'measured through an opacity group'
  );

  // The unfloored states are REPORTED, never skipped -- see the WCAG 2.2 §1.4.3
  // "inactive user interface component" note on EVALUATED_STATES. They were
  // resolved on the same code path, so an unresolvable one still failed above.
  for (const s of EVALUATED_STATES.filter((st) => st.floor === null)) {
    const inState = pairings.filter((p) => p.stateName === s.name);
    const tightest = inState.reduce((a, b) => (b.ratio < a.ratio ? b : a));
    console.log(
      `      note: ${inState.length} pairing(s) measured in the "${s.name}" state carry no asserted ` +
      `floor (WCAG 2.2 §1.4.3 exempts inactive components) — tightest is ` +
      `${tightest.ratio.toFixed(2)}:1 at ${tightest.where}`
    );
  }
}

function testNothingResolvesToUnknownInSilence(h, unresolved, themeLabel = 'light') {
  // The mandate this file failed once already: a pairing that cannot be
  // resolved statically must be SAID OUT LOUD and counted, never skipped. A
  // skipped pairing is indistinguishable from a passing one, and a population
  // of them that grows quietly is how a 1.48:1 button ships under a green suite.
  const media = h.ruleTable.mediaColour;
  if (media.length) {
    console.log(`      note: ${media.length} colour rule(s) live inside @media blocks and are conditional, not unresolved:`);
    for (const m of media.slice(0, 6)) console.log(`        ${m.at} { ${m.selectors.join(', ').slice(0, 60)} }  [${m.source}]`);
  }
  assert.deepStrictEqual(
    unresolved, [],
    `${unresolved.length} rendered pairing(s) could not be resolved statically in the ` +
    `${themeLabel} theme:\n  ` +
    unresolved.join('\n  ') +
    '\n\nEach one is a place where this suite proves nothing. Either make the ' +
    'value resolvable (a token instead of a gradient under text, an opaque ' +
    'surface instead of alpha over an unknown) or teach the resolver the case. ' +
    'Do not let the population grow.'
  );
  console.log(`PASS: 0 rendered ${themeLabel} pairings unresolved (${media.length} @media colour rules counted separately as conditional)`);
}

/**
 * EVERY STATE THE CASCADE CAN PAINT IN IS A STATE THIS FILE EVALUATES.
 *
 * Deriving CHROME_STATES from EVALUATED_STATES stops the two lists disagreeing
 * with each other. It does nothing about the third list, which is not written
 * down anywhere: the states the STYLESHEETS actually use. `:focus-within` was in
 * that third list and in neither of the other two for the whole life of both --
 * `.ret-table tbody tr:focus-within` recolours a row in the injected chrome
 * sheet, and no tier here had ever entered the state, so a focus band at 1.02:1
 * would have shown up as a +1 on a budget rather than as a failure.
 *
 * So the state list is checked against the product, the same way the badge
 * variants and the bidi floor are: a state pseudo-class that any colour,
 * background or opacity rule uses and this file does not evaluate is a blind
 * state, and it fails by name. Adding one to a stylesheet cannot silently
 * create a sixth dimension nothing looks at.
 */
function testEveryStateTheCascadeUsesIsEvaluated(h) {
  const evaluated = new Set(EVALUATED_STATES.flatMap((s) => [...s.states]));
  const PAINTS = ['color', 'background', 'background-color', 'opacity'];

  const used = new Map();   // state name -> an example rule that uses it
  for (const rule of h.ruleTable.rules) {
    if (!PAINTS.some((p) => p in rule.decls)) continue;
    for (const part of rule.parts) {
      for (const raw of part.compound.states) {
        const name = /^:([-\w]+)/.exec(raw)[1];
        if (!used.has(name)) used.set(name, `${rule.selector}  [${rule.source}]`);
      }
    }
  }

  // ANTI-VACUITY. The comparison below is a filter over `used`. A selector
  // parser that stopped classifying state pseudo-classes would leave it empty,
  // and an empty list has no uncovered members -- so this check would pass
  // loudest exactly when it had stopped working.
  assert.ok(
    used.size >= 5,
    `Only ${used.size} distinct state pseudo-class(es) were found on colour/background/` +
    'opacity rules across the whole cascade. parseCompound() has stopped classifying ' +
    'them, so "every state is evaluated" is a claim about an empty list.'
  );

  const blind = [...used]
    .filter(([name]) => !evaluated.has(name))
    .map(([name, example]) => `:${name}   e.g. ${example}`);
  assert.deepStrictEqual(
    blind, [],
    `${blind.length} state pseudo-class(es) repaint text or its surface and are ` +
    'evaluated by no tier in this file:\n  ' + blind.join('\n  ') +
    '\n\nEvery contrast measurement here is taken in one of the EVALUATED_STATES ' +
    'sets. A state outside them is a state the product can render and this suite ' +
    'can only see as a +1 on the unexercised-chrome budget -- which is how a rule ' +
    'that wins only in `:active` came to count as "the rendered tier has measured ' +
    'this" while the rendered tier never entered `:active`.\n\n' +
    'Add the state to EVALUATED_STATES (CHROME_STATES derives from it) rather ' +
    'than leaving the dimension unmodelled.'
  );

  console.log(
    `PASS: all ${used.size} state pseudo-class(es) the cascade paints in are evaluated ` +
    `(${[...used.keys()].sort().join(', ')})`
  );
}

/* ── TIER 4 — a money semantic cannot be silently overridden ───────────────── */

/**
 * A colour declaration is DEAD AND CONTRADICTED when it wins the cascade on an
 * element that contains text, yet none of that text is painted in the colour it
 * asked for, because a nearer declaration on a descendant resolves to something
 * else.
 *
 * The "contradicted" half is what keeps this from becoming noise. Several
 * containers on the POS declare `color: var(--text)` around a nested
 * `<span class="money">` that resolves to the SAME colour -- redundant, but
 * nothing is lost and flagging it would train people to write exemptions. What
 * matters is the case where the two disagree: `.rdash-bd-value.is-in` asked for
 * money-in green and the Sales figure rendered neutral, silently, for the
 * lifetime of the feature.
 *
 * `color: inherit` is excluded: it is not a paint declaration, it is the
 * mechanism by which a container's decision reaches the amount.
 */
function findContradictedColourDeclarations(h) {
  const painted = new Map();            // declaration tag -> Set of rendered hex
  const winsOnTextBearing = new Map();  // declaration tag -> {value, examples[]}

  const subtreeText = (node) => {
    let out = '';
    for (const c of node.children || []) {
      if (c.type === 'text') out += c.text;
      else if (c.type === 'element' && c.tag !== 'style' && c.tag !== 'script') out += subtreeText(c);
    }
    return out.trim();
  };
  const key = (c) => `${Math.round(c.r)},${Math.round(c.g)},${Math.round(c.b)}`;

  for (const screen of h.screens) {
    for (const { states } of EVALUATED_STATES) {
      for (const el of h.textPaintingElements(screen.root)) {
        const fg = h.effectiveColour(h.ruleTable, el, h.tokens, states);
        if (!fg.colour) continue;
        // Credit the colour to EVERY declaration on the chain that could have
        // asked for it, so a container whose value happens to match the child's
        // is not reported as contradicted.
        for (let n = el; n && n.type === 'element'; n = n.parent) {
          const d = h.winningDeclaration(h.ruleTable, n, 'color', states);
          if (!d) continue;
          if (!painted.has(d.from)) painted.set(d.from, new Set());
          painted.get(d.from).add(key(fg.colour));
        }
      }
      for (const el of h.allElements(screen.root)) {
        if (el.tag === 'style' || el.tag === 'script') continue;
        if (!subtreeText(el)) continue;
        const d = h.winningDeclaration(h.ruleTable, el, 'color', states);
        if (!d) continue;
        if (/^(inherit|currentcolor|unset|initial|revert)$/i.test(String(d.value).trim())) continue;
        const asked = h.parseColour(d.value, h.tokens);
        if (!asked) continue;
        if (!winsOnTextBearing.has(d.from)) winsOnTextBearing.set(d.from, { asked, examples: [] });
        winsOnTextBearing.get(d.from).examples.push(`${screen.name} ${h.describe(el).slice(0, 60)}`);
      }
    }
  }

  const contradicted = [];
  for (const [tag, info] of winsOnTextBearing) {
    const rendered = painted.get(tag);
    if (!rendered || rendered.size === 0) continue;         // paints no text at all
    if (rendered.has(key(info.asked))) continue;            // its colour does reach the glass
    contradicted.push(
      `${tag}\n      asks for rgb(${key(info.asked)}) but every character under it renders as ` +
      `${[...rendered].map((k) => `rgb(${k})`).join(' / ')}\n      e.g. ${info.examples[0]}`
    );
  }
  return { contradicted, checked: winsOnTextBearing.size };
}

function testNoColourSemanticIsSilentlyOverridden(h) {
  const { contradicted, checked } = findContradictedColourDeclarations(h);
  assert.ok(
    checked >= 30,
    `Only ${checked} colour declarations were found winning on a text-bearing ` +
    'element. The cascade walk is broken, so a green result here proves nothing.'
  );
  assert.deepStrictEqual(
    contradicted, [],
    `${contradicted.length} colour declaration(s) win the cascade on an element ` +
    'that contains text, and paint none of it:\n  ' + contradicted.join('\n  ') +
    '\n\nThis is the shape `.rdash-bd-value.is-in` had: the selector matched, the ' +
    'element simply had no text of its own, because _money() puts every ' +
    'character inside a nested <span class="money"> that re-declares colour. ' +
    'A money-semantic colour that silently does not apply is exactly what the ' +
    'token layer exists to prevent. Let the container decide and the amount ' +
    'inherit -- do not restate the colour in a third place.'
  );
  console.log(`PASS: none of the ${checked} winning colour declarations is silently overridden`);
}

/* ── TIER 5 — the shared retail chrome ─────────────────────────────────────── */

const CHROME_SHEET = 'subsystem-retail.js _injectStyles()';

function testBadgeVariantsAreSelfContained(h, themeLabel = 'light') {
  /* A badge is rendered on a white card, on a hovered row, and inside the dark
     `.ret-modal` (see _viewPO / _viewSale) -- three surfaces, one class. The
     originals were a hue over a 15% tint OF THAT SAME HUE, which pins the ratio
     near 1:1 by construction AND has no fixed luminance, so no single fix to
     the text colour could be right on all three. The property asserted is
     therefore stronger than "clears AA": each variant must be SELF-CONTAINED --
     its own opaque fill plus its own colour -- so its ratio does not depend on
     what is behind it at all.

     The variant list is derived from the injected sheet, not written here, so a
     sixth badge colour added tomorrow is under test the moment it exists. */
  const variants = new Set();
  for (const rule of h.ruleTable.rules) {
    if (rule.source !== CHROME_SHEET) continue;
    const m = /^\.ret-badge-([a-z]+)$/.exec(rule.selector.trim());
    if (m) variants.add(m[1]);
  }
  assert.ok(
    variants.size >= 5,
    `Expected at least 5 .ret-badge-* variants in ${CHROME_SHEET}, found ${variants.size}. ` +
    'The parse is broken, so this check would pass while examining nothing.'
  );

  const failures = [];
  for (const variant of variants) {
    // The markup helper _badge() always emits `ret-badge ret-badge-<v>`, so the
    // override is looked for at that exact shape -- the one specificity that
    // actually beats the injected sheet.
    const override = h.ruleTable.rules.filter((r) =>
      r.source === 'css/main.css' &&
      new RegExp(`^\\.ret-badge\\.ret-badge-${variant}$`).test(r.selector.trim()));
    if (!override.length) {
      failures.push(`.ret-badge-${variant}: no css/main.css override at .ret-badge.ret-badge-${variant}`);
      continue;
    }
    const decls = Object.assign({}, ...override.map((r) => r.decls));
    const fg = h.parseColour(decls.color, h.tokens);
    const bgRaw = decls['background-color'] !== undefined ? decls['background-color'] : decls.background;
    const bg = bgRaw === undefined ? null : h.backgroundColourOf(bgRaw, h.tokens);
    if (!fg) { failures.push(`.ret-badge-${variant}: override colour "${decls.color}" does not resolve`); continue; }
    if (!bg || bg.gradient || bg.a < 1) {
      failures.push(`.ret-badge-${variant}: override background "${bgRaw}" is not an opaque colour, so the ` +
        'chip\'s ratio still depends on whatever is behind it');
      continue;
    }
    const ratio = h.contrastRatio(fg, bg);
    if (ratio < AA) failures.push(`.ret-badge-${variant}: ${ratio.toFixed(2)}:1 — below AA even self-contained`);
  }
  assert.deepStrictEqual(
    failures, [],
    `${failures.length} badge variant(s) are not self-contained in the ${themeLabel} theme:\n  ` + failures.join('\n  ') +
    '\n\nThe injected sheet gives every badge an alpha tint of its own text ' +
    'colour, which is unreadable on any surface and unmeasurable on an unknown ' +
    'one. Pair an opaque --state-*-surface with its --state-*-text.'
  );
  console.log(`PASS: all ${variants.size} .ret-badge-* variants carry a self-contained, opaque, AA-clearing override (${themeLabel})`);
}

/**
 * Every colour declaration that actually PAINTS A CHARACTER somewhere in the
 * corpus, keyed the way `winningDeclaration` reports it.
 *
 * ── WHY "MATCHED" WAS THE WRONG TEST, AND WHAT IT COST ────────────────────
 *
 * The ledger used to count a rule as exercised if ANY corpus element matched
 * its selector. Matching is not painting. `.ret-table { color:#fff }` matched
 * the dashboard's transactions table, so it was filed under "exercised, so the
 * rendered tier covers it" — while on that one screen `.rdash .ret-table` (a
 * higher-specificity rule) beat it, and the white it asked for never reached
 * the glass. The rendered tier therefore proved nothing about it, the ledger
 * said it needed no attention, and the same declaration went on rendering white
 * text on white cards on the five list screens that had no such override.
 *
 * So a rule is exercised where it WINS: where some rendered element's own text
 * is painted in the colour this declaration asked for. That is the only form of
 * the question whose answer is "the rendered tier has measured this".
 */
function paintingDeclarations(h) {
  const winners = new Set();
  for (const screen of h.screens) {
    for (const el of h.textPaintingElements(screen.root)) {
      for (const states of CHROME_STATES) {
        const fg = h.effectiveColour(h.ruleTable, el, h.tokens, states);
        if (fg && fg.from) winners.add(fg.from);
      }
    }
  }
  return winners;
}

function testUnexercisedChromeIsCountedNotAssumedFine(h) {
  /* The shared chrome styles every retail screen. Every colour rule in that
     sheet which never wins the cascade on a rendered character is a rule this
     suite proves NOTHING about -- so it is named, measured against the till's
     own surfaces, and counted. Passing silently over them is the precise
     failure that let the two reported bugs ship. */
  const winners = paintingDeclarations(h);

  // ANTI-VACUITY for the accounting itself. If effectiveColour() stopped
  // resolving, `winners` would be empty, EVERY chrome rule would be reported
  // unexercised, and the budget assertion below would fail loudly rather than
  // quietly -- but the reverse mistake (a resolver that returns a `from` for
  // everything) would silently empty the ledger, so the floor is stated.
  assert.ok(
    winners.size >= 40,
    `Only ${winners.size} distinct colour declarations paint any character across ` +
    `${h.screens.length} rendered screens. The cascade resolution is broken, so an ` +
    'empty unexercised list would mean "nothing resolved", not "everything is covered".'
  );

  const surfaces = Object.entries(h.tokens)
    .filter(([name]) => /^--surface-/.test(name))
    .map(([name, value]) => [name, h.parseColour(value, h.tokens)])
    .filter(([, c]) => c && c.a === 1);
  assert.ok(surfaces.length >= 5, `Expected >=5 solid --surface-* tokens to measure against, found ${surfaces.length}`);

  const unexercised = [];
  const outrankedButRendered = [];
  const inheritOnly = [];
  let colourRules = 0;
  for (const rule of h.ruleTable.rules) {
    if (rule.source !== CHROME_SHEET || !('color' in rule.decls)) continue;

    // `color: inherit` is not a colour claim, it is a RESET -- the mechanism by
    // which a control stops overriding whatever the row already decided
    // (.ret-rowbtn's whole job). It can never be "the colour a character was
    // painted in", so counting it as an unexercised colour rule is a category
    // error, not a finding. Tier 4 excludes it for exactly the same reason.
    if (/^(inherit|currentcolor|unset|initial|revert)$/i.test(String(rule.decls.color).trim())) {
      inheritOnly.push(rule.selector);
      continue;
    }
    colourRules++;
    if (winners.has(`${rule.selector}  [${rule.source}]`)) continue;

    // Why it is not exercised is the interesting part, and the two answers are
    // very different sizes of problem:
    //
    //   OUTRANKED — some rendered element matches it and a nearer rule wins on
    //     every one of them. This is the `.ret-table { color:#fff }` state, and
    //     the state the old "did anything match it" accounting laundered into
    //     the fine bucket. The literal is still in the sheet, and the only
    //     thing keeping it off the glass is a rule somewhere else that a future
    //     edit can remove without touching it.
    //   NO TEXT — it wins on an element that paints no text node of its own.
    //     An <input>'s value is real text a cashier reads, but it lives in an
    //     attribute, so this DOM model has no character to hang a pairing on
    //     and the rendered tier genuinely cannot measure it.
    let matchedBy = null;
    let winsSomewhere = false;
    for (const s of h.screens) {
      for (const el of h.allElements(s.root)) {
        if (!CHROME_STATES.some((states) => h.matchesSelectorParts(rule.parts, el, states))) continue;
        if (!matchedBy) matchedBy = `${s.name} ${h.describe(el).slice(0, 44)}`;
        if (CHROME_STATES.some((states) => {
          const d = h.winningDeclaration(h.ruleTable, el, 'color', states);
          return d && d.from === `${rule.selector}  [${rule.source}]`;
        })) winsSomewhere = true;
      }
    }
    const why = !matchedBy ? 'no rendered element matches it'
      : winsSomewhere ? `wins on ${matchedBy}, which paints no text of its own`
      : `OUTRANKED on every rendered element it matches (e.g. ${matchedBy})`;
    if (matchedBy && !winsSomewhere) outrankedButRendered.push(`${rule.selector}   (e.g. ${matchedBy})`);

    /* ── THE RATIO IS THE ASSERTION, NOT THE FOOTNOTE ────────────────────────
       This block already computed every one of these numbers and PRINTED them
       -- "would be 1.00:1 on --surface-till", in those words, every run -- and
       then asserted only that the LIST was not longer than a budget. So six
       chrome fixes had no guard at all: reverting `.ret-search` and `.ret-date`
       to #fff on a 5% white tint, `.ret-tab:hover` to #fff, `.ret-tab.active`
       to #fff, and `.ret-kpi-change-up` to #10b981 each changed a printed line
       and moved no count, and every one of them was silent on a green tree.

       A rule the rendered tier cannot measure is still a rule THIS block can
       measure, against the till's own surfaces, in the worst case. The count
       budget stays -- it is the thing that says "the corpus's blind spot grew"
       -- but it is no longer the only claim. */
    const fg = h.parseColour(rule.decls.color, h.tokens);
    const bgRaw = rule.decls['background-color'] !== undefined ? rule.decls['background-color'] : rule.decls.background;
    const bg = bgRaw === undefined ? null : h.backgroundColourOf(bgRaw, h.tokens);
    // A rule whose selector only ever applies to an inactive component carries
    // no floor, for the same WCAG 2.2 §1.4.3 reason the `disabled` render pass
    // carries none. Derived from EVALUATED_STATES so the two cannot disagree.
    const stateNames = rule.parts.flatMap((p) => p.compound.states.map((s) => /^:([-\w]+)/.exec(s)[1]));
    const unfloored = stateNames.some((n) => UNFLOORED_STATE_NAMES.has(n));

    let note;
    let ratio = null;
    let basis = null;
    if (!fg) {
      note = `colour "${rule.decls.color}" does not resolve — UNMEASURABLE`;
    } else if (bg && !bg.gradient && bg.a >= 1) {
      ratio = h.contrastRatio(fg, bg);
      basis = 'self-contained';
      note = `self-contained ${ratio.toFixed(2)}:1`;
    } else if (bg && !bg.gradient && bg.a > 0) {
      let worst = Infinity; let on = '';
      for (const [name, surface] of surfaces) {
        const under = h.composite(bg, surface);
        const r = h.contrastRatio(fg.a < 1 ? h.composite(fg, under) : fg, under);
        if (r < worst) { worst = r; on = name; }
      }
      ratio = worst;
      basis = `alpha fill ${bgRaw} on ${on}`;
      note = `alpha fill ${bgRaw} over an unknown surface — would be ${worst.toFixed(2)}:1 on ${on}`;
    } else {
      let worst = Infinity; let on = '';
      for (const [name, surface] of surfaces) {
        const r = h.contrastRatio(fg.a < 1 ? h.composite(fg, surface) : fg, surface);
        if (r < worst) { worst = r; on = name; }
      }
      ratio = worst;
      basis = `inherited surface ${on}`;
      note = `inherits its surface — would be ${worst.toFixed(2)}:1 on ${on}`;
    }
    unexercised.push({
      selector: rule.selector, colour: String(rule.decls.color), why, ratio, basis, unfloored,
      line: `${rule.selector.padEnd(32)} color:${String(rule.decls.color).padEnd(22)} ${note}` +
        (unfloored ? '  [no floor: inactive component]' : '') + `\n            ${why}`,
    });
  }

  assert.ok(colourRules >= 25, `Expected >=25 colour rules in the shared chrome, parsed ${colourRules}.`);

  console.log(`      ${unexercised.length}/${colourRules} chrome colour rule(s) NOT exercised — the rendered tier proves nothing about these:`);
  for (const entry of unexercised) console.log('        ' + entry.line);
  if (inheritOnly.length) {
    console.log(`      (${inheritOnly.length} further rule(s) declare color:inherit — a reset, not a colour claim: ${inheritOnly.join(', ')})`);
  }
  if (outrankedButRendered.length) {
    console.log(
      `      ${outrankedButRendered.length} of the unexercised rules DO match a rendered element and lose the cascade ` +
      'on every one of them — the exact shape `.ret-table { color:#fff }` was in when five screens shipped white on white:'
    );
    for (const line of outrankedButRendered) console.log('        ' + line);
  }

  // (a) NOTHING IN THE LEDGER MAY BE UNMEASURABLE. A colour that will not
  //     resolve is the one way onto this list that dodges the floor below, and
  //     "the resolver could not read it" must never be the reason a rule is
  //     unchecked -- that is the same silence tier 3 exists to refuse. It is
  //     also the obvious way to launder a failing rule past the floor.
  const unmeasurable = unexercised.filter((e) => e.ratio === null)
    .map((e) => `${e.selector}   color:${e.colour}`);
  assert.deepStrictEqual(
    unmeasurable, [],
    `${unmeasurable.length} unexercised chrome rule(s) declare a colour that does not ` +
    'resolve:\n  ' + unmeasurable.join('\n  ') +
    '\n\nThe rendered tier cannot measure these (that is what puts them on this ' +
    'list) and now neither can this one, so nothing in the suite has an opinion ' +
    'about them at all. Make the value resolvable -- a token, or a literal -- ' +
    'rather than leaving a colour no tier can read.'
  );

  // (b) THE FLOOR. This is the assertion the ledger was missing: it computed
  //     each ratio, printed it, and then only counted rows.
  const floored = unexercised.filter((e) => !e.unfloored);
  const below = floored.filter((e) => e.ratio < AA)
    .sort((a, b) => a.ratio - b.ratio)
    .map((e) => `${e.ratio.toFixed(2)}:1  ${e.selector}   color:${e.colour}   (${e.basis})\n      ${e.why}`);
  assert.deepStrictEqual(
    below, [],
    `${below.length} colour rule(s) in the shared retail chrome measure below WCAG AA ` +
    `(${AA}:1):\n  ` + below.join('\n  ') +
    '\n\nThese are the rules the CORPUS never exercises, so no rendered pairing ' +
    'will ever fail on them -- but the ratio is computable from the sheet and ' +
    'the till\'s own surfaces, and it is computed above. The number is measured ' +
    'against the WORST solid --surface-* the shell can put behind the rule, ' +
    'because an unexercised rule is by definition one whose real surface this ' +
    'suite cannot know.\n\n' +
    'This is what `.ret-search { color:#fff }` on a 5% white tint looks like: ' +
    '1.00:1, printed in this very list every run for two rounds, asserted on by ' +
    'nothing. Fix the colour. Do not move it off the list by making it ' +
    'unexercised in a different way.'
  );

  // ANTI-VACUITY for (b): the floor is a filter over `floored`, and an empty
  // ledger satisfies it without comparing anything. The budget assertion below
  // does not cover this -- a budget is an upper bound, and zero is under it.
  assert.ok(
    floored.length >= 12,
    `Only ${floored.length} unexercised chrome rule(s) carried a measurable, floored ratio. ` +
    'Either the chrome-rule scan broke, or `unfloored` is swallowing the list -- ' +
    'either way the AA floor above compared almost nothing.'
  );

  // (c) THE COUNT. Unchanged in meaning: this is the one that says the corpus's
  //     BLIND SPOT grew, which is a different fact from any single ratio.
  assert.ok(
    unexercised.length <= UNEXERCISED_CHROME_BUDGET,
    `${unexercised.length} colour rules in the shared retail chrome are not exercised by the ` +
    `corpus; the recorded budget is ${UNEXERCISED_CHROME_BUDGET}:\n        ` +
    unexercised.map((e) => e.line).join('\n        ') +
    '\n\nThe blind spot grew. Render the screen that would exercise these ' +
    '(retail_design_render_test.js: buildCorpus) rather than raising the ' +
    'budget -- a number that only ever goes up is how a 1.48:1 button ships ' +
    'under a green suite.'
  );

  const tightest = floored.reduce((a, b) => (b.ratio < a.ratio ? b : a));
  console.log(
    `PASS: ${colourRules - unexercised.length}/${colourRules} shared-chrome colour rules are exercised by the ` +
    `corpus; the other ${unexercised.length} are named above, all measurable, all within the recorded ` +
    `budget of ${UNEXERCISED_CHROME_BUDGET} — and all ${floored.length} floored ones clear AA, tightest ` +
    `${tightest.ratio.toFixed(2)}:1 at ${tightest.selector}`
  );
}

/* ── PER-TEST ISOLATION ─────────────────────────────────────────────────────
 *
 * main() used to be a flat sequence. The first assertion that threw ended the
 * process, so a run reported exactly ONE problem however many existed — and
 * every check after the thrower was not "passing", it was NOT RUN, which reads
 * identically in the output.
 *
 * That cost this file its own headline work. At 725d94b, seven of the round's
 * new guards never executed once — the whole unexercised-chrome ledger below
 * among them — because an earlier assertion threw first. It is also why fixing
 * these files took several passes: each run showed one defect, so the remaining
 * work was invisible and the job looked shallower than it was.
 *
 * Every check now runs, every failure is collected and printed, and the process
 * exits non-zero if any failed.
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

/* The closed set of sanctioned non-light themes. dark/night/dusk are the dark
   family (working surface out-luminated by its own text); sand is a LIGHT
   theme (paper ground, ink text) and gets the inverse polarity check. Every
   TIER 1 and TIER 2 dark-family/alt-theme check below is a loop over this
   list, so a sixth theme changes coverage by construction. */
const BLOCK_THEMES = ['dark', 'night', 'dusk', 'sand'];

/* How many checks this file is known to contain. The list below is built
   conditionally — a token block that will not parse, or a corpus that will not
   build, removes the checks that consume it — and a conditionally built list can
   be built EMPTY, at which point runAll() loops over nothing, collects no
   failures and the file exits 0 having compared not one colour. Every other
   guard in this file has an anti-vacuity floor; so does the runner.

   5 (light TIER 1) + 4 themes x 5 (per-theme TIER 1) + 1 (TIER 8 cross-theme
   distinguishability) + 6 (light TIER 2-5 rendered) + 4 themes x 3
   (per-theme TIER 2 rendered) = 44. */
const EXPECTED_CHECKS = 44;

async function main() {
  const checks = [];
  const setupFailures = [];
  const setupFailed = (name, err) => {
    setupFailures.push([name, err]);
    console.error(`FAIL: ${name}`);
    console.error('      ' + String((err && err.message) || err).replace(/\n/g, '\n      '));
  };

  // TIER 1 setup. If the token block will not parse, the palette checks
  // cannot run — but the rendered tiers still can, and used to be lost with it.
  let tokens = null;
  let groups = null;
  try {
    tokens = parseTokens(readTokenBlock());
    groups = groupTokens(tokens);
  } catch (err) {
    setupFailed('token block parse (5 palette checks could not run)', err);
  }
  if (groups) {
    checks.push(
      ['the parse found a real palette', () => testParseFoundARealPalette(groups)],
      ['every text-on-surface pairing reaches AA', () => testEveryTextOnSurfaceReachesAA(groups)],
      ['money reaches AAA', () => testMoneyReachesAAA(groups)],
      ['--text-on-accent reaches AA on the accent fills', () => testTextOnAccentReachesAA(tokens, groups)],
      ['.money--negative is not colour alone', testMoneyNegativeIsNotColourAlone],
    );
  }

  // TIER 1, EVERY NON-LIGHT THEME. Each is token values only
  // (html[data-theme="<name>"] in main.css) applied to the identical rule set,
  // so the palette obligations are identical for every one of them: the same
  // cross-products, the same AA/AAA floors, on the merged (light overlaid
  // with the theme) map a document in that theme actually resolves. Sand is a
  // LIGHT theme (paper ground, ink text) so it gets the light-polarity check
  // instead of the dark one; dark/night/dusk are the dark family.
  const themeMerged = {};
  for (const themeName of BLOCK_THEMES) {
    let merged = null;
    let themeGroups = null;
    if (tokens) {
      try {
        merged = themeTokenMaps(tokens, themeName).merged;
        themeGroups = groupTokens(merged);
      } catch (err) {
        setupFailed(`${themeName} token block parse (5 ${themeName} palette checks could not run)`, err);
      }
    } else {
      setupFailed(`${themeName} token block parse (5 ${themeName} palette checks could not run)`,
        new Error('light token parse already failed, so the theme overlay has no base'));
    }
    themeMerged[themeName] = merged;
    if (themeGroups) {
      const upper = themeName.toUpperCase();
      checks.push(
        [`the ${themeName} parse found a real palette`, () => testParseFoundARealPalette(themeGroups, themeName)],
        [`every ${upper} text-on-surface pairing reaches AA`, () => testEveryTextOnSurfaceReachesAA(themeGroups, themeName)],
        [`${upper} money reaches AAA`, () => testMoneyReachesAAA(themeGroups, themeName)],
        [`${upper} --text-on-accent reaches AA on the ${themeName} accent fills`, () => testTextOnAccentReachesAA(merged, themeGroups, themeName)],
      );
      if (themeName === 'sand') {
        checks.push(
          [`the ${themeName} palette is actually light, not a re-badged dark one`, () => testLightFamilyPaletteIsActuallyLight(merged, themeName)],
        );
      } else {
        checks.push(
          [`the ${themeName} palette is actually dark, not a re-badged light one`, () => testDarkPaletteIsActuallyDark(tokens, merged, themeName)],
        );
      }
    }
  }

  // TIER 8 — cross-theme: are the five --accent-action values actually
  // distinguishable from each other? Needs the light tokens plus all four
  // theme overlays, so it can only run once every one of those parsed.
  if (tokens && BLOCK_THEMES.every((n) => themeMerged[n])) {
    checks.push(
      ['theme --accent-action values are pairwise distinguishable (ΔE76)',
        () => testThemeAccentsAreDistinguishable(tokens, themeMerged)],
    );
  } else {
    setupFailed('theme accent distinguishability (1 check could not run)',
      new Error('the light token map or one of the four theme overlays failed to parse'));
  }

  // TIER 2-5 setup: the rendered corpus.
  let h = null;
  try {
    h = await render.harness();
  } catch (err) {
    setupFailed('render.harness() (9 rendered-tier checks could not run)', err);
  }
  if (h) {
    // Resolved once and shared, so the two checks that read it are ordinary
    // peers rather than one feeding the other — a dependency that made the
    // second unreachable whenever the first threw.
    let renderedCache = null;
    const rendered = () => (renderedCache || (renderedCache = resolveRenderedPairings(h)));
    checks.push(
      ['every RENDERED pairing reaches AA', () => testEveryRenderedPairingReachesAA(h, rendered())],
      ['nothing resolves to unknown in silence', () => testNothingResolvesToUnknownInSilence(h, rendered().unresolved)],
      ['every state the cascade paints in is evaluated', () => testEveryStateTheCascadeUsesIsEvaluated(h)],
      ['no colour semantic is silently overridden', () => testNoColourSemanticIsSilentlyOverridden(h)],
      ['badge variants are self-contained', () => testBadgeVariantsAreSelfContained(h)],
      ['unexercised chrome is measured, not assumed fine', () => testUnexercisedChromeIsCountedNotAssumedFine(h)],
    );

    // TIER 2, EVERY NON-LIGHT THEME — the whole rendered corpus again,
    // resolved through each theme's map. This is the tier that would have
    // caught the ORIGINAL dark theme (whose failure was never in a token
    // block): every element, every screen, every state, with the values that
    // theme's document serves. It is possible at this cost precisely because
    // the theme difference is token values only — the rule table, the
    // cascade, and the winners are shared, so a themed document differs from
    // the light one in nothing but the map handed to the resolver, exactly as
    // in the browser.
    for (const themeName of BLOCK_THEMES) {
      if (themeMerged[themeName]) {
        const themeTokensObj = Object.assign(Object.create(null), h.tokens);
        for (const [k, v] of parseTokens(readThemeTokenBlock(themeName))) themeTokensObj[k] = v;
        const hTheme = Object.assign({}, h, { tokens: themeTokensObj });
        let renderedThemeCache = null;
        const renderedTheme = () => (renderedThemeCache || (renderedThemeCache = resolveRenderedPairings(hTheme)));
        const upper = themeName.toUpperCase();
        checks.push(
          [`every RENDERED pairing reaches AA in ${upper}`, () => testEveryRenderedPairingReachesAA(hTheme, renderedTheme(), themeName)],
          [`nothing resolves to unknown in silence in ${upper}`, () => testNothingResolvesToUnknownInSilence(hTheme, renderedTheme().unresolved, themeName)],
          [`badge variants are self-contained in ${upper}`, () => testBadgeVariantsAreSelfContained(hTheme, themeName)],
        );
      } else {
        setupFailed(`${themeName} rendered tier (3 checks could not run)`,
          new Error(`the ${themeName} token map failed to build, so the rendered corpus cannot be resolved in ${themeName}`));
      }
    }
  }

  const failures = setupFailures.concat(await runAll(checks));
  const attempted = checks.length + setupFailures.length;

  if (attempted < EXPECTED_CHECKS) {
    console.error(
      `FAIL: retail_design_contrast_test.js ran only ${attempted} of ${EXPECTED_CHECKS} known checks. ` +
      'A runner that quietly loses a check reports a clean bill of health on work it never did.'
    );
    process.exitCode = 1;
    return;
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_design_contrast_test.js — ${failures.length} of ${attempted} checks failed:`);
    for (const [name] of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_design_contrast_test.js — ${attempted} checks`);
}

main().catch((err) => {
  // Only reachable if the runner ITSELF breaks; every check-level throw is
  // caught and collected above.
  console.error('FAIL: retail_design_contrast_test.js (runner)');
  console.error(err.message || err);
  process.exitCode = 1;
});
