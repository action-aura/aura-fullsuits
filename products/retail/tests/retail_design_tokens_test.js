/**
 * OPERATIONAL CALM — the token layer is the ONLY place a colour is decided.
 *
 * WHY THIS TEST EXISTS
 * The retail stylesheet is 3,900+ lines. Before this redesign it contained
 * 221 hex literals and 521 rgb()/rgba() literals scattered through it, of
 * which 271 were `rgba(255,255,255,α)` -- white at low alpha. White-at-low-
 * alpha is a DARK-THEME ASSUMPTION: it is only visible over black, and it
 * vanishes completely over a light surface. That is the concrete reason the
 * old palette could not simply be re-themed by swapping :root, and it is
 * why the light theme that already existed had to be a 100-line second
 * stylesheet chasing individual selectors near the end of the file.
 *
 * A literal further down the file does not move when the tokens move. This
 * test fails the build when a new one appears in the operational surfaces,
 * so the migration cannot silently regress one rule at a time.
 *
 * SCOPE, HONESTLY STATED
 * This is a RATCHET, not a clean sweep. The pre-login brand surfaces (the
 * welcome splash, landing page and login, which sit on a full-screen
 * animated near-black canvas) are deliberately still dark and still carry
 * literals: making them light would put white text on a white canvas, and
 * they are not the working surface a cashier stares at for eight hours.
 * They are listed explicitly in EXEMPTIONS below, each with a reason. The
 * exemption list is asserted to be a CLOSED list -- adding a selector to it
 * is a visible, reviewable act, not something that happens by accident.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_design_tokens_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const CSS_FILE = path.join(__dirname, '..', 'frontend', 'css', 'main.css');

/* ── Exemptions ────────────────────────────────────────────────────────────
   Each entry is a selector-prefix plus the reason it may still hold literal
   colour. "Because it was already there" is not a reason; every line below
   states what would break if the literal were tokenised. */
// RETIRED 2026-09-12: the '[data-l-theme' and '[data-app-theme' exemptions
// were removed along with the CSS they excused. Both described a
// user-selectable theme feature that did not exist -- nothing had set either
// attribute for a long time, and app-shell.js says so itself -- so the
// justification was the reason ~110 lines of un-tokenised colour went
// unchallenged. If an entry here ever stops being true, delete it; a stale
// reason is worse than no exemption at all.
const EXEMPTIONS = [
  { prefix: '.ws-', reason: 'Welcome splash: a timed intro over a full-screen animated black-hole canvas. Its palette is the canvas\'s, not the till\'s; light surfaces here would be white-on-white.' },
  { prefix: '.bh-', reason: 'Black-hole canvas layers themselves — colours are sampled by the WebGL/2D animation, not by the theme.' },
  { prefix: '#bh-', reason: 'Black-hole cursor glow; same animation, id-scoped.' },
  { prefix: '.global-canvas', reason: 'Container for the same background canvas.' },
  { prefix: '.modern-', reason: 'Landing/marketing surfaces rendered over the dark canvas before any shop data exists.' },
  { prefix: '.hero-', reason: 'Landing hero, over the dark canvas.' },
  { prefix: '.l-', reason: 'Landing page title/button fragments, over the dark canvas.' },
  { prefix: '.lp-', reason: 'Landing page chrome (language/theme pickers) shown before login.' },
  { prefix: '.ml-', reason: 'Modern login screen, over the dark canvas.' },
  { prefix: '.auth-', reason: 'Auth overlay/card; pre-login, over the dark canvas.' },
  { prefix: '#page-landing', reason: 'Landing page root element; sets the dark ground the canvas animation is composited against.' },
  { prefix: '#page-login', reason: 'Login page root element; same dark ground as the landing page, shown before any till surface exists.' },
  { prefix: '.subs-', reason: 'Subsystem chooser tiles shown between login and the till; brand moment over the dark canvas.' },
  { prefix: '.domain-', reason: 'Per-domain brand tinting; --domain-* tokens are overwritten at runtime by JS per subsystem.' },
  { prefix: '.aura-logo', reason: 'Fixed brand mark; its colours are the company identity and must not shift with the product theme.' },
  { prefix: '#aura-', reason: 'Fixed brand/beta badges positioned outside the themed shell.' },
  { prefix: '.b2c-', reason: 'Customer-facing portal preview; separate brand surface from the staff till.' },
  { prefix: '.theme-swatch', reason: 'Theme picker swatches must render their literal colour — that IS their content.' },
  { prefix: '-glow', reason: 'Decorative glow/bloom layers composited over the dark canvas; they are light sources, not surfaces, and a surface token would flatten them.' },
  { prefix: '.card-glass', reason: 'Glassmorphism panel on the landing page: its fill is a translucent tint of the canvas behind it, which has no token equivalent on an opaque till surface.' },
  { prefix: '.customer-', reason: 'Customer-facing portal preview — a separate brand surface from the staff till, with its own palette (mirrors the .b2c- exemption).' },
  { prefix: '.primary-icon', reason: 'Landing feature icon; its colour is part of the marketing illustration rather than the product theme.' },
  { prefix: '.secondary-icon', reason: 'Landing feature icon; same marketing illustration palette as .primary-icon.' },
  { prefix: '.sleek-icon', reason: 'Landing decorative icon chrome sitting on the dark canvas.' },
  { prefix: '.eyebrow-', reason: 'Landing eyebrow rule above the hero headline; a hairline drawn on the dark canvas.' },
  { prefix: '.sys-option', reason: 'E-invoicing system picker highlight; its cyan bloom is a deliberate one-off attention state on a dark configuration panel.' },
];

/* Properties that actually paint a surface or text. box-shadow and gradients
   are excluded: a shadow is a rgba(0,0,0,α) depth cue that reads correctly on
   light and dark alike, and a gradient colour-stop is a texture, not a
   surface. Tokenising those was tried and produced semantically wrong output
   (a dot-grid texture became a surface token). */
/* NOTE: the per-edge COLOUR longhands (border-left-color, border-top-color,
   ...) must be listed explicitly. An earlier version of this pattern covered
   `border-left` but not `border-left-color`, and two literal colours
   (.anomaly-item.high / .medium) sat in that hole -- invisible to both the
   migration and to this test, which is the worst combination: a guard whose
   blind spot is exactly where the bug lives. */
const PAINT_PROPS = /^(background(-color)?|color|outline(-color)?|border(-(top|right|bottom|left))?(-color)?)$/;

const COLOUR_LITERAL = /(#[0-9a-fA-F]{3,8}\b|\brgba?\(\s*\d+\s*,)/;

function stripComments(css) {
  // Must run before any selector parsing: a comment containing a comma gets
  // glued onto the first selector of the following rule otherwise, and that
  // selector then silently fails to register.
  return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

/* Length-preserving comment blanking. The scan needs BOTH "comments cannot be
   mistaken for declarations" AND "byte offsets still line up with the raw
   file", because the [design-tokens:begin]/[design-tokens:end] markers
   themselves live inside comments -- stripping comments outright deletes the
   markers the scan is trying to locate. Replacing each comment with an
   equal-length run of spaces (newlines kept, so line numbers survive) gives
   both properties at once. */
function blankComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
}

function tokenBlockRange(css) {
  const begin = css.indexOf('[design-tokens:begin]');
  const end = css.indexOf('[design-tokens:end]');
  assert.ok(begin !== -1 && end > begin, 'main.css token-block markers are missing or out of order');
  // The legacy bridge immediately follows the token block and is also allowed
  // to hold literals: it exists to translate old names, and the --domain-*
  // entries are runtime-overwritten brand tints.
  const bridgeEnd = css.indexOf('--trans-slow:');
  const protectedUntil = bridgeEnd === -1 ? end : css.indexOf('\n', bridgeEnd);
  return { begin, protectedUntil };
}

function isExempt(selector) {
  return EXEMPTIONS.some((e) => selector.includes(e.prefix));
}

function findStrayLiterals() {
  const raw = fs.readFileSync(CSS_FILE, 'utf8');
  // Markers are located in the RAW text (they live inside comments); the scan
  // then walks the blanked text, whose offsets are identical by construction.
  const { protectedUntil } = tokenBlockRange(raw);
  const css = blankComments(raw);

  const strays = [];
  const exemptHits = [];
  let selector = '';
  let offset = 0;

  for (const line of css.split(/\r?\n/)) {
    const lineStart = offset;
    offset += line.length + 1;
    if (lineStart < protectedUntil) continue;

    const brace = line.indexOf('{');
    if (brace >= 0) {
      const s = line.slice(0, brace).trim();
      if (s && !s.startsWith('@')) selector = s;
    } else if (/,\s*$/.test(line) && !line.includes(':')) {
      selector = line.trim();
    }

    let m;
    const declRe = /([-a-zA-Z]+)\s*:\s*([^;{}]+)/g;
    while ((m = declRe.exec(line)) !== null) {
      const [, prop, value] = m;
      if (prop.startsWith('--')) continue;           // token definitions
      if (!PAINT_PROPS.test(prop)) continue;
      if (/(gradient|url\()/i.test(value)) continue; // texture, not a surface
      if (!COLOUR_LITERAL.test(value)) continue;

      const entry = { selector: selector.slice(0, 80), prop, value: value.trim().slice(0, 60) };
      if (isExempt(selector)) exemptHits.push(entry);
      else strays.push(entry);
    }
  }
  return { strays, exemptHits };
}

function testNoStrayLiteralsInOperationalSurfaces() {
  const { strays, exemptHits } = findStrayLiterals();

  // ANTI-VACUITY: if the parser broke, `strays` would be empty and this test
  // would pass while checking nothing. The exempt list is known non-empty
  // (the splash/landing surfaces genuinely still carry literals), so seeing
  // zero exempt hits proves the scan itself stopped working.
  assert.ok(
    exemptHits.length > 0,
    'The literal scan found ZERO colour literals anywhere, including in the ' +
    'known-exempt splash/landing surfaces that definitely still contain them. ' +
    'The parser is broken, so a green result here would be meaningless.'
  );

  if (strays.length) {
    const shown = strays.slice(0, 25)
      .map((s) => `  ${s.selector}\n      ${s.prop}: ${s.value}`)
      .join('\n');
    assert.fail(
      `${strays.length} hardcoded colour literal(s) outside the token block, in ` +
      'operational till surfaces:\n' + shown +
      (strays.length > 25 ? `\n  ...and ${strays.length - 25} more` : '') +
      '\n\nA literal here will not move when the palette moves. Use a ' +
      '--surface-*/--text-*/--border-* token, or, if this really is a brand ' +
      'surface that must stay dark, add its selector prefix to EXEMPTIONS in ' +
      'this file WITH A REASON.'
    );
  }

  console.log(
    `PASS: no stray colour literals in operational surfaces ` +
    `(scan alive: ${exemptHits.length} literals seen in ${EXEMPTIONS.length} documented exempt surfaces)`
  );
}

function testEveryExemptionCarriesAReason() {
  // An exemption list without reasons decays into "things we gave up on".
  const bad = EXEMPTIONS.filter((e) => !e.reason || e.reason.trim().length < 25);
  assert.deepStrictEqual(
    bad.map((e) => e.prefix), [],
    'Every exemption must state what would break if the literal were tokenised.'
  );
  console.log(`PASS: all ${EXEMPTIONS.length} exemptions carry a stated reason`);
}

function testTokensAreNamedForPurposeNotAppearance() {
  /* A palette named by appearance cannot be re-themed: the moment --grey-3
     stops being grey, its name is a lie, and every consumer has to be found
     and re-read to know what it MEANT. Assert the token block contains no
     appearance-named colour tokens. */
  const css = fs.readFileSync(CSS_FILE, 'utf8');
  const begin = css.indexOf('[design-tokens:begin]');
  const end = css.indexOf('[design-tokens:end]');
  const block = stripComments(css.slice(begin, end));

  const names = [...block.matchAll(/(--[a-z0-9-]+)\s*:/gi)].map((m) => m[1]);
  assert.ok(names.length >= 40, `Expected a substantial token block, parsed only ${names.length} tokens`);

  const APPEARANCE = /^--(grey|gray|white|black|red|blue|green|yellow|orange|purple|violet|pink|teal|cyan|magenta|navy|dark|light|neon)[-0-9]?/i;
  const offenders = names.filter((n) => APPEARANCE.test(n));
  assert.deepStrictEqual(
    offenders, [],
    `Token(s) named for appearance rather than purpose: ${offenders.join(', ')}.\n` +
    'Name a token for its JOB (--surface-till, --text-money-negative). An ' +
    'appearance name survives exactly until the colour changes.'
  );
  console.log(`PASS: all ${names.length} tokens are purpose-named, none appearance-named`);
}

/* The sanctioned bundled typefaces. A CLOSED list, in the same spirit as this
   file's exemption list and DESIGN.md's frozen THEME_NAMES: adding a face is a
   visible, reviewable edit here, never something that happens by accident.

   Each entry names the licence file that must ship beside the font. Both faces
   are under the SIL Open Font License 1.1, which requires the licence and its
   copyright notice to travel with any distribution of the font -- and this one
   is distributed inside a commercial installer that a customer pays for. The
   packaging spec bundles the whole frontend directory, so a licence file placed
   next to the woff2 reaches every install; one that is missing here is missing
   there too, silently. Neither licence shipped until 2026-09-09. */
const SANCTIONED_FAMILIES = [
  // Arabic-only subset; carries no Latin on purpose.
  { name: 'IBM Plex Sans Arabic', licence: 'IBMPlexSansArabic-OFL.txt' },
  // latin subset; the product's Latin face.
  { name: 'Plus Jakarta Sans', licence: 'PlusJakartaSans-OFL.txt' },
];
const SANCTIONED_FAMILY_NAMES = SANCTIONED_FAMILIES.map((f) => f.name);

function testBundledTypefacesAreSanctionedAndDoNotShareFiles() {
  /* The stylesheet used to declare three families -- Inter, Outfit and Plus
     Jakarta Sans -- whose @font-face rules all pointed at the SAME pjs-*.woff2
     files. That is one typeface wearing three names: evidence of an identity
     that drifted rather than one that was designed.

     WIDENED 2026-09-09, and the reasoning matters more than the change. This
     asserted `families === ['Plus Jakarta Sans']` -- "exactly one" -- which was
     a PROXY for the real rule, and only a correct proxy while exactly one real
     typeface was bundled. Adding IBM Plex Sans Arabic (its own files, its own
     script, no Latin at all) tripped it, even though it is the opposite of the
     bug the comment above describes.

     So the count check is replaced by the two properties it was standing in
     for: the families must be the sanctioned set, and no two of them may share
     a file. The second is the actual anti-aliasing rule and is STRICTER than
     what it replaces -- the old assertion could only ever notice a second name,
     never that two names pointed at one woff2, which is precisely what Inter
     and Outfit did.

     What it can no longer catch: the mere existence of a second family. That is
     deliberate, and the closed list above is what bounds it -- a third face
     fails here until someone adds it on purpose. */
  const css = fs.readFileSync(CSS_FILE, 'utf8');
  const faces = [...css.matchAll(/@font-face\s*\{([^}]*)\}/gi)].map((m) => m[1]);
  const families = new Set();
  const filesByFamily = new Map();
  for (const body of faces) {
    const fam = /font-family\s*:\s*['"]([^'"]+)['"]/i.exec(body);
    if (!fam) continue;
    families.add(fam[1]);
    for (const src of body.matchAll(/url\(\s*['"]?([^'")]+)['"]?\s*\)/gi)) {
      const file = src[1].split('/').pop();
      if (!filesByFamily.has(file)) filesByFamily.set(file, new Set());
      filesByFamily.get(file).add(fam[1]);
    }
  }

  assert.deepStrictEqual(
    [...families].sort(), [...SANCTIONED_FAMILY_NAMES].sort(),
    `Bundled @font-face families are ${[...families].sort().join(', ')}, but the ` +
    `sanctioned set is ${[...SANCTIONED_FAMILY_NAMES].sort().join(', ')}. Adding a ` +
    'typeface is a design decision: put it in SANCTIONED_FAMILIES above, with a ' +
    'comment saying what it is for, so the type system stays something that was ' +
    'chosen rather than something that accumulated.'
  );

  const shared = [...filesByFamily.entries()]
    .filter(([, fams]) => fams.size > 1)
    .map(([file, fams]) => `${file} is claimed by ${[...fams].join(' and ')}`);
  assert.deepStrictEqual(
    shared, [],
    `One typeface is wearing more than one name: ${shared.join('; ')}. This is ` +
    'exactly the Inter/Outfit/Jakarta drift this test was written for -- three ' +
    'names, one set of files, a type system that looked intentional and was not. ' +
    'Two families must never share a font file.'
  );

  /* Every bundled face must ship its licence. Both are SIL Open Font License
     1.1, which requires the licence and its copyright notice to be distributed
     WITH the font -- and this font is distributed inside an installer a customer
     pays for. The packaging spec bundles the whole frontend directory, so a
     licence sitting next to the woff2 reaches every install and a missing one is
     missing on every install, with nothing to notice it. Neither licence shipped
     at all until 2026-09-09; this check is why that cannot recur. */
  const FONT_DIR = path.join(__dirname, '..', 'frontend', 'fonts');
  const missingLicences = SANCTIONED_FAMILIES
    .filter((f) => {
      const p = path.join(FONT_DIR, f.licence);
      return !fs.existsSync(p) || fs.statSync(p).size < 1000;
    })
    .map((f) => `${f.name} -> ${f.licence}`);
  assert.deepStrictEqual(
    missingLicences, [],
    `Bundled typeface(s) shipping without their licence: ${missingLicences.join('; ')}. ` +
    'The SIL Open Font License requires its text and copyright notice to travel ' +
    'with the font, including inside a paid installer. Put the real licence file ' +
    'in frontend/fonts/ -- download the font\'s own OFL.txt, never copy another ' +
    'font\'s and edit the copyright line.'
  );

  const clean = stripComments(css);
  const stragglers = [...clean.matchAll(/font-family\s*:\s*([^;}]+)/gi)]
    .map((m) => m[1])
    .filter((v) => /['"](Inter|Outfit)['"]/.test(v));
  assert.deepStrictEqual(
    stragglers, [],
    `${stragglers.length} rule(s) still reference the removed 'Inter'/'Outfit' ` +
    'aliases. With the alias @font-face gone these fall back to a system font, ' +
    'so they must resolve through var(--font-base) instead.'
  );
  console.log(`PASS: ${families.size} sanctioned font famil${families.size === 1 ? 'y' : 'ies'}, none sharing a file, no orphaned Inter/Outfit references`);
}

/* PER-TEST ISOLATION -- see retail_design_money_test.js for the reasoning and
   the measurement. */
const CHECKS = [
  ['every exemption carries a reason', testEveryExemptionCarriesAReason],
  ['tokens are named for purpose, not appearance', testTokensAreNamedForPurposeNotAppearance],
  ['bundled typefaces are sanctioned and do not share files', testBundledTypefacesAreSanctionedAndDoNotShareFiles],
  ['no stray literals in the operational surfaces', testNoStrayLiteralsInOperationalSurfaces],
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
    console.error(`\nFAIL: retail_design_tokens_test.js — ${failures.length} of ${CHECKS.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_design_tokens_test.js — ${CHECKS.length} checks`);
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_design_tokens_test.js (runner)');
  console.error(err.message || err);
  process.exitCode = 1;
}
