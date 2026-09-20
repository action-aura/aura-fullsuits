/**
 * EVERY SANCTIONED THEME MUST BE CLASSIFIED LIGHT OR DARK, EXPLICITLY.
 *
 * WHY THIS TEST EXISTS
 * `icons.js` carries
 *
 *     var DARK_SURFACE_THEMES = { dark: true, night: true, dusk: true };
 *
 * and `_surfaceShadowFacet()` returns the brand guide's dark-surface shadow
 * facet (#22304A) for those three and the light-surface one (#16233D) for
 * everything else. The "everything else" arm is the problem: it is a silent
 * default, not a decision. Add a sixth theme -- a darker Night variant, an
 * OLED black, anything -- and it is classified LIGHT by omission, which paints
 * a light-surface shadow facet on a dark ground. That is precisely the defect
 * this whole line of work exists to fix: a single fixed #16233D measured
 * 1.19-1.26:1 against the app's own dark grounds, i.e. invisible.
 *
 * CLAUDE.md's themes section names this hazard directly -- "never a second
 * allowlist" -- because the sanctioned names already live in exactly one
 * frozen place, `THEME_NAMES` in app-shell.js. `DARK_SURFACE_THEMES` is a
 * second list of theme names by any honest reading. The properly idiomatic
 * fix is a per-theme TOKEN in the `[design-tokens-<name>]` blocks, so a new
 * theme cannot exist without declaring its own darkness; that change touches
 * all five theme blocks plus Android's DesktopTokenParityContractTest, and is
 * disproportionate while the 3D branch this feeds renders nowhere (both real
 * `AuraIcons.mark()` call sites pass 40, which takes the flat branch).
 *
 * So this test is the proportionate guard instead: it does not remove the
 * second list, it makes the second list unable to fall out of step with the
 * first WITHOUT SOMEONE NOTICING.
 *
 * WHAT IT CHECKS
 *   A. Every name in DARK_SURFACE_THEMES is a real sanctioned theme. A typo
 *      ("nigth") would otherwise sit there forever, silently classifying the
 *      real theme as light.
 *   B. Every sanctioned theme is accounted for -- either listed as dark, or
 *      present in this file's own LIGHT_BY_DESIGN list. A new theme fails
 *      here until a human writes down which it is. That is the entire point:
 *      the failure is the prompt.
 *   C. The two sets do not overlap and together cover THEME_NAMES exactly.
 *
 * WHAT THIS DOES NOT CLAIM
 * It does not verify that a theme CALLED dark actually has a dark ground --
 * that is `retail_design_contrast_test.js`'s job, which measures the rendered
 * corpus. This is a bookkeeping guard over a name list, which is what the
 * hazard actually is.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_theme_darkness_classification_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const FRONTEND = path.join(__dirname, '..', 'frontend');

/* The themes whose grounds are LIGHT, so the light-surface shadow facet
   (#16233D) is the correct paint for them. Maintained by hand ON PURPOSE:
   adding a theme should force a person to state which side it falls on,
   and the failure message below says so. */
const LIGHT_BY_DESIGN = ['light', 'sand'];

function read(rel) {
  return fs.readFileSync(path.join(FRONTEND, rel), 'utf8');
}

/* app-shell.js's frozen list is the ONE sanctioned source of theme names. */
function sanctionedThemes() {
  const src = read('app-shell.js');
  const m = /const\s+THEME_NAMES\s*=\s*Object\.freeze\(\s*\[([^\]]*)\]/.exec(src);
  assert.ok(m, 'THEME_NAMES not found in app-shell.js — this test cannot ' +
    'establish the sanctioned set, so a green result would mean nothing.');
  const names = m[1].split(',')
    .map((s) => s.trim().replace(/^['"]|['"]$/g, ''))
    .filter(Boolean);
  assert.ok(names.length >= 5,
    `Only ${names.length} theme name(s) parsed out of THEME_NAMES; expected at ` +
    'least the five sanctioned ones. The matcher is not reading the real list.');
  return names;
}

function darkClassified() {
  const src = read('icons.js');
  const m = /DARK_SURFACE_THEMES\s*=\s*\{([^}]*)\}/.exec(src);
  assert.ok(m, 'DARK_SURFACE_THEMES not found in icons.js. If the darkness ' +
    'classification moved (for example into a per-theme design token, which ' +
    'is the better long-term shape), this test must move with it rather than ' +
    'be deleted — otherwise nothing checks the coverage again.');
  return m[1].split(',')
    .map((s) => s.split(':')[0].trim().replace(/^['"]|['"]$/g, ''))
    .filter(Boolean);
}

const themes = sanctionedThemes();
const dark = darkClassified();

/* ── A ── every classified-dark name is a real theme ───────────────────── */
const unknown = dark.filter((t) => !themes.includes(t));
assert.deepStrictEqual(
  unknown, [],
  `DARK_SURFACE_THEMES names ${unknown.join(', ')}, which is not in ` +
  `THEME_NAMES (${themes.join(', ')}). A misspelt key does not error — it ` +
  'silently leaves the real theme classified as LIGHT, which paints the ' +
  'light-surface shadow facet (#16233D) on a dark ground. That measured ' +
  '1.19-1.26:1 against this product\'s dark grounds: an invisible mark.'
);

/* ── B ── every theme is accounted for ─────────────────────────────────── */
const unclassified = themes.filter((t) => !dark.includes(t) && !LIGHT_BY_DESIGN.includes(t));
assert.deepStrictEqual(
  unclassified, [],
  `Theme(s) ${unclassified.join(', ')} are in THEME_NAMES but classified ` +
  'neither dark (icons.js DARK_SURFACE_THEMES) nor light (LIGHT_BY_DESIGN in ' +
  'this test). icons.js defaults anything unlisted to LIGHT, so an unclassified ' +
  'theme is not an open question at runtime — it silently becomes light. ' +
  'Decide which it is and say so in both places.'
);

/* ── C ── the two sets partition THEME_NAMES exactly ───────────────────── */
const both = dark.filter((t) => LIGHT_BY_DESIGN.includes(t));
assert.deepStrictEqual(
  both, [],
  `Theme(s) ${both.join(', ')} are classified BOTH dark and light. icons.js ` +
  'would win at runtime; the disagreement means one of the two lists is stale.'
);

const staleLight = LIGHT_BY_DESIGN.filter((t) => !themes.includes(t));
assert.deepStrictEqual(
  staleLight, [],
  `LIGHT_BY_DESIGN names ${staleLight.join(', ')}, which no longer exists in ` +
  'THEME_NAMES. A removed theme left behind here would mask a future theme ' +
  'reusing the same name.'
);

console.log(
  `PASS retail_theme_darkness_classification_test.js ` +
  `(${themes.length} sanctioned themes: ${dark.length} dark [${dark.join(', ')}], ` +
  `${LIGHT_BY_DESIGN.length} light [${LIGHT_BY_DESIGN.join(', ')}], 0 unclassified)`
);
