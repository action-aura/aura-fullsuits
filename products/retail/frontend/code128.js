/* Aura Retail -- a small pure Code 128 (subset B) encoder (UI-2 item 2).
 *
 * WHY THIS EXISTS. Returns started from a typed receipt number because the
 * receipt carried nothing a scanner could read. A Code 128 of the sale number
 * lets the till's existing barcode scanner (the same one that rings products up)
 * find the sale: the scan router loads it into the open Returns dialog.
 * Nothing in this repo encoded Code 128 (a repo-wide search found no encoder and
 * no vendored library), and the product's rule is no new dependency -- so this
 * is ~60 lines of table + arithmetic with golden tests, not a package.
 *
 * WHAT IT ENCODES. Subset B covers printable ASCII (space .. '~'), which is every
 * character a sale number is made of (letters, digits, '-'). Anything outside
 * that range is REFUSED (null) rather than silently mangled: a barcode that
 * decodes to a different string than the text printed under it is worse than no
 * barcode. (Subset C would halve the width for all-digit numbers; sale numbers
 * carry a letter prefix, so it would almost never apply.)
 *
 * SHAPE. encode(text) -> { text, codes, checksum, runs, modules }
 *   codes     the symbol values: [START_B, ...data, checksum, STOP]
 *   checksum  (START_B + sum(position * value)) mod 103, position counted from 1
 *   runs      alternating widths in modules, FIRST ONE IS A BAR: bar, space, bar...
 *   modules   total width in modules (11 per symbol + 13 for the stop + no quiet zone)
 * The caller adds the quiet zone (>= 10 modules each side) and picks the module
 * width in device dots.
 *
 * The 107 patterns below are the standard Code 128 table (ISO/IEC 15417) as
 * bar/space widths, six per symbol (seven for STOP). The golden tests check the
 * table's structure (every symbol is 11 modules wide, STOP is 13), three known
 * strings including the check digit, and -- in a browser/Node run -- that an
 * independent decoder reads the rendered bars back to the same text.
 */
(function (root) {
  'use strict';

  var PATTERNS = [
    '212222', '222122', '222221', '121223', '121322', '131222', '122213', '122312', '132212', '221213',
    '221312', '231212', '112232', '122132', '122231', '113222', '123122', '123221', '223211', '221132',
    '221231', '213212', '223112', '312131', '311222', '321122', '321221', '312212', '322112', '322211',
    '212123', '212321', '232121', '111323', '131123', '131321', '112313', '132113', '132311', '211313',
    '231113', '231311', '112133', '112331', '132131', '113123', '113321', '133121', '313121', '211331',
    '231131', '213113', '213311', '213131', '311123', '311321', '331121', '312113', '312311', '332111',
    '314111', '221411', '431111', '111224', '111422', '121124', '121421', '141122', '141221', '112214',
    '112412', '122114', '122411', '142112', '142211', '241211', '221114', '413111', '241112', '134111',
    '111242', '121142', '121241', '114212', '124112', '124211', '411212', '421112', '421211', '212141',
    '214121', '412121', '111143', '111341', '131141', '114113', '114311', '411113', '411311', '113141',
    '114131', '311141', '411131', '211412', '211214', '211232', '2331112'
  ];
  var START_B = 104;
  var STOP = 106;

  function encode(text) {
    var s = text == null ? '' : String(text);
    if (!s.length) return null;
    var codes = [START_B];
    var sum = START_B;
    for (var i = 0; i < s.length; i++) {
      var c = s.charCodeAt(i);
      if (c < 32 || c > 126) return null;      // outside subset B: refuse, never mangle
      var v = c - 32;
      codes.push(v);
      sum += (i + 1) * v;
    }
    var checksum = sum % 103;
    codes.push(checksum, STOP);
    var runs = [];
    var modules = 0;
    codes.forEach(function (code) {
      var p = PATTERNS[code];
      for (var k = 0; k < p.length; k++) { var w = p.charCodeAt(k) - 48; runs.push(w); modules += w; }
    });
    return { text: s, codes: codes, checksum: checksum, runs: runs, modules: modules };
  }

  // Bars as [x, width] pairs in modules (spaces are the gaps) -- what a painter needs.
  function bars(enc) {
    var out = [], x = 0;
    enc.runs.forEach(function (w, i) { if (i % 2 === 0) out.push([x, w]); x += w; });
    return out;
  }

  // Inline SVG for the browser-print receipt: crisp at any print resolution,
  // no canvas, no image request. `moduleWidth` is in CSS px; `height` in CSS px.
  // shape-rendering:crispEdges keeps a 1px module from anti-aliasing to grey.
  function svg(text, opts) {
    var enc = encode(text);
    if (!enc) return '';
    var quiet = 10, h = (opts && opts.height) || 44;
    // moduleWidth wins when given; otherwise maxWidth (CSS px, the paper's usable
    // width) picks the widest quarter-pixel module that still fits, capped at 2.
    var mw = (opts && opts.moduleWidth) ||
      (opts && opts.maxWidth
        ? Math.max(0.75, Math.min(2, Math.floor(opts.maxWidth / (enc.modules + 2 * quiet) * 4) / 4))
        : 2);
    var width = (enc.modules + 2 * quiet) * mw;
    var rects = bars(enc).map(function (b) {
      return '<rect x="' + ((b[0] + quiet) * mw) + '" y="0" width="' + (b[1] * mw) + '" height="' + h + '"/>';
    }).join('');
    return '<svg xmlns="http://www.w3.org/2000/svg" class="rcpt-barcode" role="img" aria-label="' +
      String(enc.text).replace(/[&<>"']/g, '') + '" width="' + width + '" height="' + h +
      '" viewBox="0 0 ' + width + ' ' + h + '" shape-rendering="crispEdges"><rect width="' + width +
      '" height="' + h + '" fill="#fff"/><g fill="#000">' + rects + '</g></svg>';
  }

  var api = { encode: encode, bars: bars, svg: svg, PATTERNS: PATTERNS, START_B: START_B, STOP: STOP };
  root.AuraCode128 = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
