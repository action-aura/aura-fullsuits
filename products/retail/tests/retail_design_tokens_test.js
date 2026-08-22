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
  { prefix: '[data-l-theme', reason: 'User-selectable alternate LANDING themes (e.g. "neon"). The literal colours ARE the theme the user picked; tokenising them would collapse every alternate theme into the default one.' },
  { prefix: '[data-app-theme', reason: 'User-selectable alternate app accents. Same reasoning as [data-l-theme]: the literal is the choice.' },
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

function testOnlyOneFontFamilyIsDeclared() {
  /* The stylesheet used to declare three families -- Inter, Outfit and Plus
     Jakarta Sans -- whose @font-face rules all pointed at the SAME pjs-*.woff2
     files. That is one typeface wearing three names: evidence of an identity
     that drifted rather than one that was designed. */
  const css = fs.readFileSync(CSS_FILE, 'utf8');
  const families = new Set(
    [...css.matchAll(/@font-face\s*\{[^}]*?font-family\s*:\s*['"]([^'"]+)['"]/gi)].map((m) => m[1])
  );
  assert.deepStrictEqual(
    [...families].sort(), ['Plus Jakarta Sans'],
    `Expected exactly one bundled @font-face family, found: ${[...families].join(', ')}. ` +
    'Aliasing extra families onto the same woff2 makes the type system look ' +
    'intentional when it is not.'
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
  console.log('PASS: exactly one font family is declared, with no orphaned Inter/Outfit references');
}

function main() {
  testEveryExemptionCarriesAReason();
  testTokensAreNamedForPurposeNotAppearance();
  testOnlyOneFontFamilyIsDeclared();
  testNoStrayLiteralsInOperationalSurfaces();
  console.log('PASS: retail_design_tokens_test.js');
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_design_tokens_test.js');
  console.error(err.message || err);
  process.exitCode = 1;
}
