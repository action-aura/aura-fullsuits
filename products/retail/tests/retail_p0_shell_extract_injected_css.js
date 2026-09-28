// Prints subsystem-retail.js's REAL injected stylesheet text
// (RetailSystem._injectStyles(), a CSS-in-JS template literal injected into
// <head> at runtime -- see that method's own docstring) to stdout. Never a
// reimplementation of what that stylesheet SAYS: the regex below is a
// byte-for-byte copy of retail_design_render_test.js's own
// readInjectedStylesheet(), so this reads the identical slice of the
// identical file that h.sources' matching entry would contain.
//
// WHY IT DOES NOT GO THROUGH render.harness() (a change from this script's
// first version, which did). harness() builds this exact string as one of
// its very first steps (loadSources(), before anything gets executed) but
// only RETURNS it bundled inside a single Promise that also builds the
// RENDERED SCREEN CORPUS -- which loads the whole of subsystem-retail.js
// into a `vm.Script` and therefore fails outright if ANY part of that
// 14,000-line, actively-edited-by-other-crews file has a syntax error
// anywhere in it, however unrelated to CSS. Measured directly: a
// P0-#14d-in-progress edit at subsystem-retail.js:524 (an unescaped
// backtick inside a /* comment */ that is itself inside the OUTER
// `s.textContent = \`...\`` template literal, which the backtick closes
// early) throws "Unexpected token ':'" out of buildCorpus(), and
// render.harness() has no partial-success path -- callers get nothing at
// all, not even the sources it had already assembled before the throw.
// That is the right failure mode for retail_design_contrast_test.js's OWN
// Tier 2-9 (they genuinely need the rendered corpus, and correctly report
// "N checks could not run" when it is missing), but this script needs
// NONE of that: it never executes subsystem-retail.js, only reads its
// TEXT, so tying it to a vm.Script over the whole file makes this crew's
// real-browser proof for P0 #14b hostage to an unrelated crew's unrelated,
// mid-edit syntax error in a screen this proof never touches. Reading the
// same slice directly removes that dependency without weakening what gets
// checked -- the extracted text is identical either way when the file is
// well-formed, and this script fails LOUDLY (not silently) if the
// s.textContent literal itself cannot be found, the one failure mode that
// actually matters to it.
//
// WHY THIS EXISTS AT ALL: retail_p0_shell_css_e2e.py (a real-browser
// Playwright check for P0 #14a/#14b) needs this text to build a static
// HTML fixture, because those two fixes use `:where()`/`:is()`, which
// retail_design_render_test.js's own hand-rolled selector matcher does not
// model (see that Python script's header for the full reasoning) -- so
// this crew's proof for them has to run in an actual browser instead, and
// an actual browser needs the actual CSS text, not a second copy of it
// typed by hand into a fixture.
'use strict';

const fs = require('fs');
const path = require('path');

const RETAIL_JS = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

function readInjectedStylesheet(jsSource) {
  const m = /s\.textContent\s*=\s*`([\s\S]*?)`;/.exec(jsSource);
  if (!m) {
    throw new Error('Could not find _injectStyles()\'s `s.textContent = `...`` block in subsystem-retail.js');
  }
  return m[1];
}

try {
  process.stdout.write(readInjectedStylesheet(fs.readFileSync(RETAIL_JS, 'utf8')));
} catch (err) {
  process.stderr.write(String((err && err.stack) || err) + '\n');
  process.exit(1);
}
