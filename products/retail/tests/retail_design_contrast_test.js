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
 * WHAT IT ASSERTS NOW — FIVE PROPERTIES, DELIBERATELY DIFFERENT ONES
 *
 *  1. THE PALETTE (unchanged, still a cross product over the token names):
 *       every --text-* x every solid --surface-*  >= 4.5:1 (AA)
 *       every --text-money* x every solid --surface-*  >= 7.0:1 (AAA)
 *       --text-on-accent x every --accent-action* fill >= 4.5:1 (AA)
 *     Money gets the stricter floor because a misread total is a till's worst
 *     failure mode. Derived from token NAMES, so the palette cannot quietly
 *     grow a failing member.
 *
 *  2. THE SCREEN. Every element that paints text on a RENDERED till screen,
 *     resting and hovered, with its colour and its surface resolved through the
 *     real cascade and the real ancestor chain -- alpha fills composited down
 *     to the opaque surface underneath. This is the tier that catches an
 *     injected literal, because it never asks where a colour came from.
 *     See retail_design_render_test.js for the corpus and the cascade engine.
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
 * ANTI-VACUITY
 * Every assertion here is a loop. A loop over nothing passes. So the palette
 * tier asserts it parsed a realistic number of tokens before asserting they
 * pass, and the rendered tiers lean on retail_design_render_test.js, which
 * asserts the corpus reaches the specific elements the defects were measured
 * on. The check RUNNING is asserted separately from the check PASSING.
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

/* The states a pairing is evaluated in. Resting is the obvious one; :hover is
   included because a hover rule that changes only the BACKGROUND (which is what
   `.ret-btn-ghost:hover` does) moves the ratio without touching the colour, and
   that is a pairing nobody ever looks at. The other state pseudo-classes are
   deliberately out: :focus-visible draws a ring rather than repainting text,
   and :disabled is a documented, intentional de-emphasis with its own rule.

   The hover pass treats EVERY element as hovered at once, which is not a state
   the DOM can be in -- but it is sound for this question. :hover applies to the
   element under the pointer AND to all of its ancestors, so every element's own
   chain (which is all a contrast resolution reads) is genuinely reachable. The
   over-approximation is limited to siblings, and a sibling's background has no
   bearing on this element's text. */
const EVALUATED_STATES = [['resting', new Set()], ['hovered', new Set(['hover'])]];

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

   The rest of the list is genuinely unreachable: the KPI card family, the PO
   split preview, the supplier tabs, and the `.ret-field`/`.ret-search` inputs
   -- whose value is real text an operator reads, but lives in an attribute, so
   this DOM model has no character to hang a pairing on. `.ret-search`'s
   #fff-on-a-5%-white-tint (1.00:1) is in that group and is still wrong. */
const UNEXERCISED_CHROME_BUDGET = 25;

/* ── Token parsing ─────────────────────────────────────────────────────────
   Only the block between the [design-tokens:begin]/[design-tokens:end]
   markers is the palette. Reading the whole file would sweep up the legacy
   bridge aliases (which are var() indirections, not colours) and every
   unrelated literal further down. */
function readTokenBlock() {
  const css = fs.readFileSync(CSS_FILE, 'utf8');
  const start = css.indexOf('[design-tokens:begin]');
  const end = css.indexOf('[design-tokens:end]');
  assert.ok(start !== -1, 'main.css is missing the [design-tokens:begin] marker');
  assert.ok(end !== -1, 'main.css is missing the [design-tokens:end] marker');
  assert.ok(end > start, '[design-tokens:end] appears before [design-tokens:begin]');
  return css.slice(start, end);
}

function parseTokens(block) {
  const tokens = new Map();
  // Strip comments first: the token block is heavily commented and a comment
  // containing a `#` or a `;` would otherwise be parsed as a declaration.
  const clean = block.replace(/\/\*[\s\S]*?\*\//g, '');
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

function testParseFoundARealPalette(groups) {
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
    `PASS: parsed a real palette — ${groups.textOnSurface.length} text tokens x ` +
    `${groups.surfaces.length} surfaces = ${pairings} pairings, ` +
    `${groups.money.length} money tokens, ${groups.accentFills.length} accent fills`
  );
}

function testEveryTextOnSurfaceReachesAA(groups) {
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
    `${failures.length} text-on-surface pairing(s) fall below WCAG AA (${AA}:1).\n  ` +
    failures.join('\n  ') +
    '\n\nEvery --text-* token must be legible on every --surface-* token, ' +
    'because the shell composes them freely (a label lands on a card, a card ' +
    'lands on the shell, a row highlights on hover).'
  );
  console.log(`PASS: all ${groups.textOnSurface.length * groups.surfaces.length} text-on-surface pairings reach WCAG AA (${AA}:1)`);
}

function testMoneyReachesAAA(groups) {
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
    `${failures.length} money pairing(s) fall below WCAG AAA (${AAA}:1).\n  ` +
    failures.join('\n  ') +
    '\n\nMoney is held to AAA, not AA: a misread total is the worst thing a ' +
    'till can do, and amounts get read at a glance and at an angle all shift.'
  );
  console.log(
    `PASS: all ${groups.money.length * groups.surfaces.length} money pairings reach WCAG AAA (${AAA}:1) ` +
    `— tightest is ${worst.label} at ${worst.ratio.toFixed(2)}:1`
  );
}

function testTextOnAccentReachesAA(tokens, groups) {
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
    `Accent-filled controls have unreadable labels:\n  ${failures.join('\n  ')}\n\n` +
    'These are the primary actions -- "Open POS", "Charge", "Confirm". A ' +
    'primary button nobody can read is worse than no primary button.'
  );
  console.log(`PASS: --text-on-accent reaches AA on all ${groups.accentFills.length} accent fills`);
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
    for (const [stateName, states] of EVALUATED_STATES) {
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
          where, ratio: h.contrastRatio(through.colour, through.surface),
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

function testEveryRenderedPairingReachesAA(h) {
  const { pairings, unresolved } = resolveRenderedPairings(h);

  // ANTI-VACUITY, and it is PER SCREEN on purpose. A corpus-wide floor of 200
  // was satisfied by three screens and would still be satisfied if nine of the
  // thirteen silently stopped resolving, because the four largest carry more
  // than 200 pairings between them. Every screen the corpus declares must
  // contribute, or the number below is an average hiding a hole.
  const perScreen = new Map();
  for (const p of pairings) {
    const screen = p.where.split('/')[0];
    perScreen.set(screen, (perScreen.get(screen) || 0) + 1);
  }
  const silent = h.screens.map((s) => s.name).filter((name) => (perScreen.get(name) || 0) < EVALUATED_STATES.length * 4);
  assert.deepStrictEqual(
    silent, [],
    'Screen(s) contributed almost no resolved pairings:\n  ' +
    silent.map((n) => `${n}: ${perScreen.get(n) || 0}`).join('\n  ') +
    '\n\nA screen that resolves nothing is a screen this tier proves nothing ' +
    'about, and it is indistinguishable from a screen that passes.'
  );

  const failures = pairings.filter((p) => p.ratio < AA).sort((a, b) => a.ratio - b.ratio);
  assert.deepStrictEqual(
    failures.map(describePairing), [],
    `${failures.length} pairing(s) the till actually RENDERS fall below WCAG AA (${AA}:1):\n  ` +
    failures.map(describePairing).join('\n  ') +
    '\n\nThis tier does not care where a colour came from -- a token, a literal ' +
    'in an injected stylesheet, an inline style attribute, or a perfectly legible ' +
    'pair inside an `opacity` group -- they all land on the same screen. Fix the ' +
    'colour, not the scope of the test.'
  );

  const worst = pairings.reduce((a, b) => (b.ratio < a.ratio ? b : a));
  const washed = pairings.filter((p) => p.alpha < 1).length;
  console.log(
    `PASS: all ${pairings.length} RENDERED pairings across ${h.screens.length} screens ` +
    `x ${EVALUATED_STATES.length} states reach AA (${AA}:1) — tightest is ` +
    `${worst.ratio.toFixed(2)}:1 at ${worst.where.split(' ')[0]}; ${washed} of them ` +
    'measured through an opacity group'
  );
  return unresolved;
}

function testNothingResolvesToUnknownInSilence(h, unresolved) {
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
    `${unresolved.length} rendered pairing(s) could not be resolved statically:\n  ` +
    unresolved.join('\n  ') +
    '\n\nEach one is a place where this suite proves nothing. Either make the ' +
    'value resolvable (a token instead of a gradient under text, an opaque ' +
    'surface instead of alpha over an unknown) or teach the resolver the case. ' +
    'Do not let the population grow.'
  );
  console.log(`PASS: 0 rendered pairings unresolved (${media.length} @media colour rules counted separately as conditional)`);
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
    for (const [, states] of EVALUATED_STATES) {
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

function testBadgeVariantsAreSelfContained(h) {
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
    `${failures.length} badge variant(s) are not self-contained:\n  ` + failures.join('\n  ') +
    '\n\nThe injected sheet gives every badge an alpha tint of its own text ' +
    'colour, which is unreadable on any surface and unmeasurable on an unknown ' +
    'one. Pair an opaque --state-*-surface with its --state-*-text.'
  );
  console.log(`PASS: all ${variants.size} .ret-badge-* variants carry a self-contained, opaque, AA-clearing override`);
}

/* The states a chrome rule can legitimately win in. Evaluated SEPARATELY, never
   unioned: a single set containing both `hover` and `disabled` would let a rule
   that only ever applies to a disabled control count as exercised by a hovered
   one, which is not a state any element is ever in. */
const CHROME_STATES = [
  new Set(),
  new Set(['hover']),
  new Set(['focus', 'focus-visible']),
  new Set(['active']),
  new Set(['disabled']),
];

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

    const fg = h.parseColour(rule.decls.color, h.tokens);
    const bgRaw = rule.decls['background-color'] !== undefined ? rule.decls['background-color'] : rule.decls.background;
    const bg = bgRaw === undefined ? null : h.backgroundColourOf(bgRaw, h.tokens);
    let note;
    if (!fg) note = `colour "${rule.decls.color}" does not resolve`;
    else if (bg && !bg.gradient && bg.a >= 1) note = `self-contained ${h.contrastRatio(fg, bg).toFixed(2)}:1`;
    else if (bg && !bg.gradient && bg.a > 0) {
      let worst = Infinity; let on = '';
      for (const [name, surface] of surfaces) {
        const under = h.composite(bg, surface);
        const r = h.contrastRatio(fg.a < 1 ? h.composite(fg, under) : fg, under);
        if (r < worst) { worst = r; on = name; }
      }
      note = `alpha fill ${bgRaw} over an unknown surface — would be ${worst.toFixed(2)}:1 on ${on}`;
    } else {
      let worst = Infinity; let on = '';
      for (const [name, surface] of surfaces) {
        const r = h.contrastRatio(fg.a < 1 ? h.composite(fg, surface) : fg, surface);
        if (r < worst) { worst = r; on = name; }
      }
      note = `inherits its surface — would be ${worst.toFixed(2)}:1 on ${on}`;
    }
    unexercised.push(`${rule.selector.padEnd(32)} color:${String(rule.decls.color).padEnd(22)} ${note}\n            ${why}`);
  }

  assert.ok(colourRules >= 25, `Expected >=25 colour rules in the shared chrome, parsed ${colourRules}.`);

  console.log(`      ${unexercised.length}/${colourRules} chrome colour rule(s) NOT exercised — the rendered tier proves nothing about these:`);
  for (const line of unexercised) console.log('        ' + line);
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

  assert.ok(
    unexercised.length <= UNEXERCISED_CHROME_BUDGET,
    `${unexercised.length} colour rules in the shared retail chrome are not exercised by the ` +
    `corpus; the recorded budget is ${UNEXERCISED_CHROME_BUDGET}:\n        ` + unexercised.join('\n        ') +
    '\n\nThe blind spot grew. Render the screen that would exercise these ' +
    '(retail_design_render_test.js: buildCorpus) rather than raising the ' +
    'budget -- a number that only ever goes up is how a 1.48:1 button ships ' +
    'under a green suite.'
  );
  console.log(
    `PASS: ${colourRules - unexercised.length}/${colourRules} shared-chrome colour rules are exercised by the ` +
    `corpus; the other ${unexercised.length} are named above and within the recorded budget of ${UNEXERCISED_CHROME_BUDGET}`
  );
}

async function main() {
  const tokens = parseTokens(readTokenBlock());
  const groups = groupTokens(tokens);

  testParseFoundARealPalette(groups);
  testEveryTextOnSurfaceReachesAA(groups);
  testMoneyReachesAAA(groups);
  testTextOnAccentReachesAA(tokens, groups);
  testMoneyNegativeIsNotColourAlone();

  const h = await render.harness();
  const unresolved = testEveryRenderedPairingReachesAA(h);
  testNothingResolvesToUnknownInSilence(h, unresolved);
  testNoColourSemanticIsSilentlyOverridden(h);
  testBadgeVariantsAreSelfContained(h);
  testUnexercisedChromeIsCountedNotAssumedFine(h);

  console.log('PASS: retail_design_contrast_test.js');
}

main().catch((err) => {
  console.error('FAIL: retail_design_contrast_test.js');
  console.error(err.message || err);
  process.exitCode = 1;
});
