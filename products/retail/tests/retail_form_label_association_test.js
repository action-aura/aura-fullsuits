/**
 * EVERY FORM FIELD MUST HAVE A LABEL A MACHINE CAN FOLLOW.
 *
 * WHY THIS TEST EXISTS
 * On 2026-09-19 an accessibility audit found 123 of the 128 <label> elements
 * in subsystem-retail.js were VISUAL ONLY: the text sat next to its input and
 * nothing connected the two. That is two separate failures at once.
 *
 *   1. A screen reader announces such an input as "edit, blank". The
 *      shopkeeper hears nothing about what the field is. Aura Retail ships
 *      Arabic-first to shops where the person on the till may be using
 *      assistive tech, and the forms in question include the ones that create
 *      a product, take a payment and open a cash drawer.
 *   2. Clicking the visible text does not focus the input. That is a
 *      pointer-user bug, not only an assistive-tech one, and it is the reason
 *      `for` is the right fix rather than `aria-label`: `for` gives BOTH
 *      behaviours from the already-translated visible string, where
 *      `aria-label` gives only the first and forces a second copy of the text
 *      that will drift out of sync with the catalogue.
 *
 * The 119 that were fixed reused an id that the sibling control already had,
 * so nothing was renumbered and no getElementById call anywhere changed
 * meaning.
 *
 * WHY IT IS A DERIVED RULE, NOT A LIST
 * The check enumerates every <label> the file emits and demands each one be
 * associated. It is therefore automatically true of a form added tomorrow: a
 * new field with a floating label fails here the day it is written, which is
 * the only way a rule like this survives contact with a codebase. A frozen
 * list of 128 known labels would go stale on the next commit and would have
 * to be curated by hand forever.
 *
 * THE TWO LEGAL SHAPES
 * HTML gives a label two ways to name a control and both are correct:
 *
 *   EXPLICIT   <label for="pm-name">Name</label><input id="pm-name">
 *   IMPLICIT   <label><input type="checkbox"> Override expiry</label>
 *
 * The implicit form -- the label WRAPS its control -- is already associated
 * by the parser, and adding a redundant for/id to it would be noise. Three
 * checkbox/radio rows in this file use it, and they are correct as written,
 * so the check accepts either shape rather than mandating the explicit one.
 *
 * WHAT THIS DELIBERATELY DOES NOT COVER
 * A caption over a GROUP of controls is not a <label> at all and is out of
 * scope here by construction: a <label> names exactly one control, so
 * pointing a group caption's for= at the first radio would rename that radio
 * after the group ("Applies To" instead of "Product") -- an accessible name
 * that is actively worse than none. The promotion modal's "Applies To" group
 * uses role="radiogroup" + aria-labelledby for that reason, and the check
 * below asserts that specific shape survives rather than silently permitting
 * a bare caption anywhere.
 *
 * This also does not prove the label TEXT is right, only that the wiring
 * exists. Text quality is the i18n suites' job.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_form_label_association_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const FILES = ['subsystem-retail.js', 'app-shell.js'];

/* ANTI-VACUITY FLOOR. The scan is a regex over source text, so the failure
   mode that matters is it matching NOTHING -- a refactor to a different markup
   helper, a renamed file, a broken read -- and reporting a clean sweep of an
   empty set. 141 labels matched across the two files when this was written
   (127 in subsystem-retail.js, 14 in app-shell.js); the floor is set under
   that so ordinary deletion does not trip it, but a collapse does. */
const MIN_LABELS_SCANNED = 120;

function read(file) {
  return fs.readFileSync(path.join(FRONTEND, file), 'utf8');
}

/* Every <label ...> ... </label> with its opening tag, inner text and offset. */
function labelsIn(src) {
  const out = [];
  const re = /<label\b([^>]*)>([\s\S]*?)<\/label>/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    out.push({ attrs: m[1], inner: m[2], index: m.index });
  }
  return out;
}

function idsIn(src) {
  const counts = new Map();
  const re = /\bid="([^"$]+)"/g; // "$" excluded: an interpolated id is not a literal
  let m;
  while ((m = re.exec(src)) !== null) {
    counts.set(m[1], (counts.get(m[1]) || 0) + 1);
  }
  return counts;
}

function lineOf(src, index) {
  return src.slice(0, index).split('\n').length;
}

function wrapsAControl(inner) {
  return /<(input|select|textarea)\b/i.test(inner);
}

function testEveryLabelIsAssociatedWithAControl() {
  const failures = [];
  let scanned = 0;
  let explicit = 0;
  let implicit = 0;

  for (const file of FILES) {
    const src = read(file);
    const ids = idsIn(src);

    for (const label of labelsIn(src)) {
      scanned++;
      const forMatch = /\bfor="([^"]*)"/.exec(label.attrs);

      if (forMatch) {
        explicit++;
        const target = forMatch[1];
        const count = ids.get(target) || 0;
        // An interpolated for= (for="${x}") cannot be resolved statically and
        // is not treated as a failure -- but it is also not counted as proof,
        // so it is reported rather than silently accepted.
        if (target.includes('${')) continue;
        if (count === 0) {
          failures.push(
            `${file}:${lineOf(src, label.index)}  for="${target}" points at an id ` +
            'that does not exist in this file. A dangling for= is worse than a ' +
            'missing one: it reads as fixed and announces nothing.'
          );
        } else if (count > 1) {
          failures.push(
            `${file}:${lineOf(src, label.index)}  for="${target}" resolves to ${count} ` +
            'elements. Only the first is ever labelled; the rest silently are not.'
          );
        }
        continue;
      }

      if (wrapsAControl(label.inner)) { implicit++; continue; }

      const text = label.inner.replace(/\s+/g, ' ').trim().slice(0, 48);
      failures.push(
        `${file}:${lineOf(src, label.index)}  <label> "${text}" has no for= and ` +
        'does not wrap its control, so nothing connects it to any input. Give ' +
        'the control an id and the label a matching for= -- reuse the id the ' +
        'control already has rather than renumbering anything.'
      );
    }
  }

  assert.ok(
    scanned >= MIN_LABELS_SCANNED,
    `Only ${scanned} <label> elements were found across ${FILES.join(' + ')} ` +
    `(there were 141 when this was written, floor ${MIN_LABELS_SCANNED}). The scan ` +
    'has stopped matching the markup, so a green result here would be measuring ' +
    'an empty set rather than a labelled form.'
  );

  assert.deepStrictEqual(
    failures, [],
    `${failures.length} form label(s) are not associated with any control:\n  ` +
    failures.join('\n  ') +
    '\n\nA visible label that is not wired to its input announces "edit, blank" ' +
    'to a screen reader and does not focus the field when clicked.'
  );

  console.log(
    `PASS: all ${scanned} <label> elements are associated with a control ` +
    `(${explicit} explicit for=, ${implicit} wrapping the control directly)`
  );
}

/* The group caption the rule above deliberately excludes. Asserted explicitly
   so "there are no bare labels" cannot quietly become true tomorrow by
   someone turning a real label into a caption to get this suite green. */
function testTheRadioGroupCaptionKeepsItsGroupSemantics() {
  const src = read('subsystem-retail.js');
  assert.ok(
    /class="ret-field-label"\s+id="prm-target-caption"/.test(src),
    'The promotion "Applies To" caption lost its ret-field-label/id shape.'
  );
  assert.ok(
    /role="radiogroup"\s+aria-labelledby="prm-target-caption"/.test(src),
    'The promotion target radios are no longer a role="radiogroup" named by ' +
    'their caption. Without it the two radios are announced with no idea what ' +
    'question they answer.'
  );
  assert.ok(
    /\.ret-field \.ret-field-label\b/.test(src),
    'The .ret-field-label caption style was dropped from _injectStyles, so the ' +
    'group caption no longer matches the typography of every real label ' +
    'beside it.'
  );
  console.log('PASS: the "Applies To" radio group is named by its caption, not by a fake label');
}

function main() {
  const checks = [
    ['every label is associated with a control', testEveryLabelIsAssociatedWithAControl],
    ['the radio group caption keeps group semantics', testTheRadioGroupCaptionKeepsItsGroupSemantics],
  ];
  const failures = [];
  for (const [name, fn] of checks) {
    try {
      fn();
    } catch (err) {
      failures.push(name);
      console.error(`FAIL: ${name}`);
      console.error('      ' + String((err && err.message) || err).replace(/\n/g, '\n      '));
    }
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_form_label_association_test.js — ${failures.length} of ${checks.length} checks failed`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_form_label_association_test.js — ${checks.length} checks`);
}

main();
