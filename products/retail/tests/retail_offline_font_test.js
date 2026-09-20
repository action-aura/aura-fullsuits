/**
 * NO SCREEN IN THIS PRODUCT MAY FETCH A TYPEFACE FROM THE INTERNET.
 *
 * WHY THIS TEST EXISTS
 * This is an offline-first desktop POS. A till in a Jordanian shop may have
 * no internet at all -- the whole LAN site-relay subsystem exists precisely
 * because a shop has to converge over its own wifi with nothing upstream.
 * main.css already states the rule in its own comment ("Bundled webfont
 * (offline -- no CDN)") and bundles Plus Jakarta Sans and IBM Plex Sans
 * Arabic as local woff2 for exactly that reason.
 *
 * brand/intro.html did not follow it. It carried
 *
 *     <link rel="preconnect" href="https://fonts.googleapis.com">
 *     <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Outfit...">
 *
 * and set `font-family: Outfit, "Segoe UI", system-ui, sans-serif`. It was
 * the ONLY file in the entire retail frontend reaching the network for a
 * face. The consequences were not hypothetical:
 *
 *   1. AN OFFLINE TILL RENDERED THE BRAND INTRO IN SEGOE UI. The intro is
 *      the first thing a customer ever sees of this product, and on the
 *      machine most likely to be offline -- a fresh first-run install,
 *      before any network is configured -- it silently rendered in the
 *      Windows system face. A brand intro that renders in the OS default is
 *      worse than no intro, because it looks like a bug rather than a brand.
 *   2. IT WAS THE WRONG FACE EVEN WHEN IT LOADED. Outfit is not a brand
 *      face. Action-Aura-Brand-Guide.md names Space Grotesk for wordmarks
 *      and the tagline. main.css had already REMOVED its own 'Outfit'
 *      @font-face alias (see the "OPERATIONAL CALM" comment there) on the
 *      grounds that it was an alias pointing at a different family -- "an
 *      identity that drifted rather than one that was designed". That sweep
 *      recorded "zero references in any .html or .js", which was true of
 *      the alias but missed this file's independent CDN copy.
 *   3. IT LEAKED. Every first run told Google that this install existed,
 *      with its IP and User-Agent, before the shopkeeper had typed anything.
 *
 * WHAT THIS TEST CHECKS, and why it is three checks rather than one
 *
 *   A. No frontend file references a known webfont CDN host. The direct
 *      form of the defect above.
 *   B. Every `@font-face` src resolves to a file that EXISTS on disk. This
 *      is a different failure with the identical symptom: a local @font-face
 *      whose woff2 was renamed, or never committed, does not error --
 *      the browser silently falls back, exactly like the CDN did when
 *      offline. A test that only banned CDN hosts would report green on it.
 *   C. The brand intro actually asks for a brand face. Checks A and B are
 *      both satisfied by an intro that simply names no font at all and
 *      inherits Segoe UI -- which is the very outcome this test exists to
 *      prevent. Without C, the cheapest way to make A pass is to reintroduce
 *      the bug.
 *
 * WHAT THIS DOES NOT CLAIM
 * It does not render anything and does not prove the glyphs are legible, or
 * that the subset covers the text drawn -- it reads source and checks the
 * filesystem. It does not police non-font network references (images,
 * scripts); those are a different rule and would need their own reasoning.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_offline_font_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const FRONTEND = path.join(__dirname, '..', 'frontend');

/* Hosts that serve typefaces. Deliberately a list of HOSTS rather than a
   catch-all "https://" ban: this frontend legitimately mentions external
   URLs in other contexts, and a rule that flagged all of them would be
   turned off rather than obeyed. */
const FONT_CDN_HOSTS = [
  'fonts.googleapis.com',
  'fonts.gstatic.com',
  'use.typekit.net',
  'fonts.bunny.net',
  'cdn.jsdelivr.net/npm/@fontsource',
  'fonts.cdnfonts.com',
];

/* `src: url('/static/fonts/x.woff2')` -- capture the path. */
const FONT_FACE_SRC = /src\s*:\s*url\(\s*['"]?([^'")]+)['"]?\s*\)/g;

/* Anti-vacuity: the sweep must still be reading a real frontend. A rename,
   a bad join, or a read failure would otherwise report a clean result while
   measuring nothing -- the failure shape this repo's ENGINEERING.md calls
   out by name. */
const MIN_BYTES_SCANNED = 500000;
const MIN_FILES_SCANNED = 10;

const SCAN_EXT = new Set(['.html', '.css', '.js', '.svg']);

function walk(dir, out) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name === 'fonts') continue;
      walk(full, out);
    } else if (SCAN_EXT.has(path.extname(entry.name).toLowerCase())) {
      out.push(full);
    }
  }
  return out;
}

function rel(file) {
  return path.relative(FRONTEND, file).replace(/\\/g, '/');
}

/* Blank out comment bodies, PRESERVING newlines so reported line numbers
   still match the file. This codebase documents the defects it bans in long
   comments sitting right next to the fix -- intro.html's own @font-face
   comment quotes "fonts.googleapis.com" while explaining why it no longer
   uses it -- so scanning raw text would report the documentation as the
   defect. A commented-out <link> is inert, so ignoring it is correct rather
   than merely convenient. */
function stripComments(src) {
  const blank = (m) => m.replace(/[^\n]/g, ' ');
  return src
    .replace(/<!--[\s\S]*?-->/g, blank)   // HTML
    .replace(/\/\*[\s\S]*?\*\//g, blank); // CSS and JS block
}

function scan() {
  const files = walk(FRONTEND, []).sort();
  let bytes = 0;
  const sources = files.map((f) => {
    const raw = fs.readFileSync(f, 'utf8');
    bytes += raw.length;
    return { file: f, raw, code: stripComments(raw) };
  });
  assert.ok(
    bytes >= MIN_BYTES_SCANNED && files.length >= MIN_FILES_SCANNED,
    `Only ${files.length} file(s) / ${bytes} bytes of frontend were scanned ` +
    `(floors ${MIN_FILES_SCANNED} files, ${MIN_BYTES_SCANNED} bytes). The sweep ` +
    'is not reading the frontend, so a green result would be measuring nothing.'
  );
  return sources;
}

function testNoFrontendFileFetchesAFontFromTheInternet(sources) {
  const offenders = [];
  for (const { file, code } of sources) {
    code.split('\n').forEach((line, i) => {
      /* A JS line comment, and ONLY that. The obvious `/\/\/.*$/` is a trap
         this test fell into and was caught by its own mutation proof: it
         also matches the `//` in `https://`, so it deleted the very URLs
         the scan exists to find, and a reintroduced CDN <link> passed
         cleanly. Requiring whitespace or line-start before the slashes
         keeps `https://` (preceded by `:`) and protocol-relative
         `url(//host/...)` (preceded by `(`) intact, while still dropping a
         genuine trailing comment. */
      const bare = line.replace(/(?:^|\s)\/\/.*$/, '');
      for (const host of FONT_CDN_HOSTS) {
        if (bare.includes(host)) {
          offenders.push(`${rel(file)}:${i + 1}  ${line.trim().slice(0, 120)}`);
          break;
        }
      }
    });
  }
  assert.deepStrictEqual(
    offenders, [],
    `${offenders.length} external webfont reference(s) found:\n  ` +
    offenders.join('\n  ') +
    '\n\nThis is an offline-first POS. A till with no internet renders these ' +
    'screens in the OS fallback face. Bundle the woff2 under ' +
    'frontend/fonts/ and point @font-face at /static/fonts/ instead, the way ' +
    'main.css already does for Plus Jakarta Sans and IBM Plex Sans Arabic.'
  );
}

function testEveryBundledFontFileActuallyExists(sources) {
  const missing = [];
  let declared = 0;

  for (const { file, code } of sources) {
    FONT_FACE_SRC.lastIndex = 0;
    let m;
    while ((m = FONT_FACE_SRC.exec(code)) !== null) {
      const url = m[1].trim();
      if (/^(data:|https?:|\/\/)/i.test(url)) continue;  // check A owns remote URLs
      declared++;
      // '/static/...' is Flask's static_url_path over frontend/ (app.py);
      // anything else is resolved relative to the declaring file.
      const onDisk = url.startsWith('/static/')
        ? path.join(FRONTEND, url.slice('/static/'.length))
        : path.resolve(path.dirname(file), url);
      if (!fs.existsSync(onDisk)) {
        missing.push(`${rel(file)}  ->  ${url}  (looked for ${onDisk})`);
      }
    }
  }

  assert.ok(
    declared >= 8,
    `Only ${declared} local @font-face src(s) were found; this frontend ` +
    'bundles at least the five Plus Jakarta Sans and three IBM Plex Sans ' +
    'Arabic weights. The matcher is not finding them, so a green result ' +
    'would be measuring nothing.'
  );

  assert.deepStrictEqual(
    missing, [],
    `${missing.length} @font-face src(es) point at a file that is not on disk:\n  ` +
    missing.join('\n  ') +
    '\n\nA missing local font does NOT raise an error -- the browser silently ' +
    'falls back to the next family in the stack, which is the same invisible ' +
    'failure as an offline CDN.'
  );
}

function testTheBrandIntroAsksForABrandFace(sources) {
  const intro = sources.find(({ file }) => rel(file) === 'brand/intro.html');
  assert.ok(intro, 'brand/intro.html was not found by the sweep.');

  // The guide's wordmark/tagline face. Checked by NAME because that is what
  // the browser resolves; the @font-face that backs it is covered by check B.
  //
  // @font-face BLOCKS ARE REMOVED FIRST, and that is the whole point of this
  // check rather than an optimisation. An @font-face declaration CONTAINS
  // `font-family: 'Space Grotesk'` -- so matching the raw text cannot tell
  // "the page USES the face" from "the page merely DECLARES it and then
  // renders in Segoe UI". Mutation-proved: deleting the face from the
  // html/body rule while leaving the @font-face in place passed this check
  // before the blocks were stripped, i.e. the exact defect was invisible.
  const uses = intro.code.replace(/@font-face\s*\{[\s\S]*?\}/g, ' ');
  assert.ok(
    /font-family\s*:\s*[^;]*Space\s*Grotesk/i.test(uses),
    'brand/intro.html does not set Space Grotesk anywhere. ' +
    'Action-Aura-Brand-Guide.md names Space Grotesk 700 for wordmarks and ' +
    '500 for the tagline. An intro that names no brand face inherits the OS ' +
    'default -- which is the exact outcome the CDN removal was meant to fix, ' +
    'so removing the font-family would "pass" the other checks by ' +
    'reintroducing the original bug.'
  );

  assert.ok(
    /@font-face/i.test(intro.code),
    'brand/intro.html names Space Grotesk but declares no @font-face for it, ' +
    'so it can only render if the host machine happens to have the font ' +
    'installed. It must load the bundled woff2.'
  );
}

/* ── D ── a bundled .woff2 must actually BE woff2 ───────────────────────
   Added after shipping one that was not. `grotesk-var.woff2` was written by
   driving fontTools' subsetter through its Python API, where
   `subset.Options().flavor` is read by the pyftsubset COMMAND LINE and
   assigned to `font.flavor` by the CLI -- a step the API path skips. The
   result was a raw uncompressed TrueType file under a .woff2 name: 68,308
   bytes, magic 00 01 00 00 instead of "wOF2".

   IT LOADED. Chromium sniffs the real container and ignores the `format()`
   hint, so every render check passed and the face measured correctly. That
   is what makes this worth a test rather than a one-off fix: the defect was
   invisible at runtime while (a) roughly doubling the download -- the real
   woff2 is 26,208 bytes, 62% smaller -- and (b) making the stylesheet's
   `format('woff2-variations')` a false statement, which a stricter engine is
   entitled to refuse, falling back to the system face. That fallback is the
   exact failure this whole file exists to prevent, so the bug would have
   re-created it on some other browser while looking fine here.

   Checks the magic number, because the extension and the CSS `format()` are
   both just claims. */
function testBundledFontFilesAreTheFormatTheyClaim() {
  const FONT_DIR = path.join(FRONTEND, 'fonts');
  const MAGIC = {
    '.woff2': Buffer.from('wOF2', 'ascii'),
    '.woff': Buffer.from('wOFF', 'ascii'),
  };

  const files = fs.readdirSync(FONT_DIR)
    .filter((f) => MAGIC[path.extname(f).toLowerCase()]);
  assert.ok(
    files.length >= 8,
    `Only ${files.length} bundled webfont file(s) found in frontend/fonts; ` +
    'this product bundles at least five Plus Jakarta Sans weights and three ' +
    'IBM Plex Sans Arabic weights. The scan is not reading the font directory.'
  );

  const liars = [];
  for (const f of files) {
    const want = MAGIC[path.extname(f).toLowerCase()];
    const fd = fs.openSync(path.join(FONT_DIR, f), 'r');
    const head = Buffer.alloc(4);
    fs.readSync(fd, head, 0, 4, 0);
    fs.closeSync(fd);
    if (!head.equals(want)) {
      liars.push(`${f}: magic ${[...head].map((b) => b.toString(16).padStart(2, '0')).join(' ')}` +
        `, expected "${want.toString('ascii')}"`);
    }
  }

  assert.deepStrictEqual(
    liars, [],
    `${liars.length} bundled font file(s) are not the format their name claims:\n  ` +
    liars.join('\n  ') +
    '\n\nA raw TTF under a .woff2 name still LOADS in Chromium, which sniffs the ' +
    'container and ignores the CSS format() hint — so this does not show up in ' +
    'any render check. It ships a much larger download and makes the ' +
    "stylesheet's format() a false claim that a stricter engine may refuse, " +
    'falling back to the system face.'
  );
}

const sources = scan();
testNoFrontendFileFetchesAFontFromTheInternet(sources);
testEveryBundledFontFileActuallyExists(sources);
testTheBrandIntroAsksForABrandFace(sources);
testBundledFontFilesAreTheFormatTheyClaim();

console.log(`PASS retail_offline_font_test.js (4 checks, ${sources.length} frontend files scanned)`);
