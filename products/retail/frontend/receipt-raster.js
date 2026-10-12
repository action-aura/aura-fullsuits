/* Aura Retail -- receipt rasteriser (PRINT-1). No dependencies, no build step.
 *
 * WHY THIS EXISTS
 * ESC/POS text mode prints Arabic as '?' and the HTML/spooler path cannot open
 * the cash drawer. The one renderer in this stack that already shapes and joins
 * Arabic correctly -- right-to-left, mixed with Latin digits -- is the browser.
 * So the server sends the receipt as LAYOUT DATA (GET /printer/receipt-model,
 * core/retail/receipt_model.py), this file paints it on a <canvas>, thresholds
 * it to a 1-bit bitmap and the server wraps that in printer commands
 * (POST /printer/print-raster, core/retail/escpos_raster.py).
 *
 * The model decides WHAT is on the paper; this file decides only how it looks.
 *
 * FONTS. The Arabic UI face the product already ships (/static/fonts, the same
 * files main.css loads) is loaded explicitly with FontFace and AWAITED before
 * drawing: a canvas never waits for a font on its own, so without this the
 * first receipt after launch would come out in a fallback face (or tofu).
 * Latin first, Arabic second: canvas falls through the list per glyph.
 * Weights 600/700 on purpose -- a 1-bit threshold eats the thin strokes of
 * a 400 weight, especially Arabic joins at 22-24 px on 58 mm paper.
 *
 * BIDI. Chromium does Arabic shaping and bidi for fillText. Each text run is
 * drawn with ctx.direction 'rtl' when the receipt is Arabic or the run holds
 * Arabic script, else 'ltr'. Numbers and amounts arrive wrapped in Unicode
 * isolates (U+2066..U+2069) by the model so a figure keeps its digit order
 * inside an RTL line. Alignment is always PHYSICAL (left/right) here; the
 * model's start/end are logical and are mirrored for rtl below.
 *
 * Pure helpers (packBits, bytesToBase64, wrapWords) are exported for the node
 * test; everything touching document/canvas lives behind render().
 */
(function (root) {
  'use strict';

  var ARABIC_RE = /[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]/;
  var FONT_BASE = '/static/fonts/';
  var FONT_FILES = [
    ['AuraReceiptLatin', 'pjs-600.woff2', '600'], ['AuraReceiptLatin', 'pjs-700.woff2', '700'],
    ['AuraReceiptArabic', 'plex-ar-600.woff2', '600'], ['AuraReceiptArabic', 'plex-ar-700.woff2', '700'],
  ];
  var FONT_STACK = '"AuraReceiptLatin", "AuraReceiptArabic", "IBM Plex Sans Arabic", "Segoe UI", sans-serif';
  // Below this luminance (0 black .. 255 white) a pixel prints. 160 rather than
  // 128: anti-aliased strokes are grey, and a thermal head under-prints grey.
  var THRESHOLD = 160;
  var fontsPromise = null;

  function hasArabic(s) { return ARABIC_RE.test(s || ''); }

  // MSB-first row packing: the leftmost dot of a row is bit 7 of its first
  // byte, 1 = black -- exactly what GS v 0 wants. `rgba` is canvas ImageData.data.
  function packBits(rgba, width, height, threshold) {
    var th = threshold == null ? THRESHOLD : threshold;
    var rowBytes = width >> 3;
    var out = new Uint8Array(rowBytes * height);
    for (var y = 0; y < height; y++) {
      for (var x = 0; x < width; x++) {
        var i = (y * width + x) * 4;
        // Composite on white so a transparent pixel is paper, not black.
        var a = rgba[i + 3] / 255;
        var lum = (0.299 * rgba[i] + 0.587 * rgba[i + 1] + 0.114 * rgba[i + 2]) * a + 255 * (1 - a);
        if (lum < th) out[y * rowBytes + (x >> 3)] |= 0x80 >> (x & 7);
      }
    }
    return out;
  }

  function bytesToBase64(bytes) {
    var s = '';
    for (var i = 0; i < bytes.length; i += 0x8000) {
      s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(s);
  }

  // Greedy word wrap by measured width. A single word wider than maxW is
  // broken by characters (a long unbroken product code must not overflow).
  function wrapWords(text, maxW, measure) {
    var words = String(text).split(/\s+/).filter(Boolean);
    var lines = [], cur = '';
    words.forEach(function (w) {
      var trial = cur ? cur + ' ' + w : w;
      if (measure(trial) <= maxW) { cur = trial; return; }
      if (cur) { lines.push(cur); cur = ''; }
      if (measure(w) <= maxW) { cur = w; return; }
      var chunk = '';
      Array.from(w).forEach(function (ch) {
        if (measure(chunk + ch) > maxW && chunk) { lines.push(chunk); chunk = ch; } else { chunk += ch; }
      });
      cur = chunk;
    });
    if (cur) lines.push(cur);
    return lines.length ? lines : [''];
  }

  function loadFonts() {
    if (fontsPromise) return fontsPromise;
    fontsPromise = Promise.all(FONT_FILES.map(function (f) {
      var face = new FontFace(f[0], 'url(' + FONT_BASE + f[1] + ')', { weight: f[2] });
      return face.load().then(function (loaded) { document.fonts.add(loaded); });
    })).catch(function (e) {
      // A missing font must not stop a receipt printing: the stack falls back
      // to the system faces. Forget the failure so the next print retries.
      fontsPromise = null;
      console.error('Receipt fonts failed to load:', e);
    });
    return fontsPromise;
  }

  function sizes(widthDots) {
    // 576 dots (80 mm) vs 384 dots (58 mm): the narrow head gets slightly
    // smaller type so a product line still fits, but never below 22 px.
    var base = widthDots >= 576 ? 24 : 22;
    return { normal: base, small: base - 3, large: base + 8 };
  }

  // Pass 1: turn the model into positioned draw operations and a total height.
  // Done against a scratch 2d context so the real canvas can be created at its
  // final height (resizing a canvas clears it).
  function layout(model, widthDots, ctx) {
    var sz = sizes(widthDots);
    var PAD = 8, GAP = 12, rtl = !!model.rtl;
    var maxW = widthDots - 2 * PAD;
    var ops = [], y = 10;

    function font(size, bold) { return (bold ? '700 ' : '600 ') + size + 'px ' + FONT_STACK; }
    function dirOf(text) { return (rtl || hasArabic(text)) ? 'rtl' : 'ltr'; }
    function measure(text, f, dir) { ctx.font = f; ctx.direction = dir; return ctx.measureText(text).width; }

    (model.lines || []).forEach(function (ln) {
      if (ln.kind === 'blank') { y += sz.normal; return; }
      if (ln.kind === 'qr') { return; }           // the printer draws the QR itself
      if (ln.kind === 'rule') {
        ops.push({ t: 'rule', y: y + 6 }); y += 14; return;
      }
      var size = sz[ln.size] || sz.normal;
      var f = font(size, !!ln.bold || ln.size === 'large');
      var lh = Math.round(size * 1.4);

      if (ln.kind === 'text') {
        var dir = dirOf(ln.text);
        var align = ln.align === 'center' ? 'center'
          : (ln.align === 'start' ? (rtl ? 'right' : 'left')
            : (ln.align === 'end' ? (rtl ? 'left' : 'right') : 'center'));
        wrapWords(ln.text, maxW, function (s) { return measure(s, f, dir); }).forEach(function (piece) {
          ops.push({ t: 'text', text: piece, font: f, dir: dir, align: align, y: y + lh - Math.round(size * 0.3) });
          y += lh;
        });
        return;
      }

      if (ln.kind === 'row') {
        // Amount side first (never clipped), then the label wraps in what is left.
        var amountDir = 'ltr';
        var endW = measure(ln.end, f, amountDir);
        var strikeW = ln.strike ? measure(ln.strike, f, amountDir) + GAP : 0;
        var labelW = maxW - endW - strikeW - GAP;
        if (labelW < size * 4) labelW = Math.max(size * 4, maxW / 2);
        var labelDir = dirOf(ln.start);
        var pieces = wrapWords(ln.start, labelW, function (s) { return measure(s, f, labelDir); });
        var labelSide = rtl ? 'right' : 'left';
        var endSide = rtl ? 'left' : 'right';
        pieces.forEach(function (piece, idx) {
          var base = y + lh - Math.round(size * 0.3);
          ops.push({ t: 'text', text: piece, font: f, dir: labelDir, align: labelSide, y: base });
          if (idx === 0) {
            ops.push({ t: 'text', text: ln.end, font: f, dir: amountDir, align: endSide, y: base });
            if (ln.strike) {
              ops.push({ t: 'strike', text: ln.strike, font: f, dir: amountDir, side: endSide,
                         offset: endW + GAP, y: base, size: size, w: strikeW - GAP });
            }
          }
          y += lh;
        });
        return;
      }
    });
    return { ops: ops, height: y + 16, pad: PAD };
  }

  function paint(ctx, plan, widthDots) {
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, widthDots, plan.height);
    ctx.fillStyle = '#000';
    ctx.strokeStyle = '#000';
    ctx.textBaseline = 'alphabetic';
    plan.ops.forEach(function (op) {
      if (op.t === 'rule') {
        ctx.fillRect(plan.pad, op.y, widthDots - 2 * plan.pad, 2);
        return;
      }
      ctx.font = op.font;
      ctx.direction = op.dir;
      var x;
      if (op.t === 'strike') {
        ctx.textAlign = op.side;
        x = op.side === 'right' ? widthDots - plan.pad - op.offset : plan.pad + op.offset;
        ctx.fillText(op.text, x, op.y);
        var left = op.side === 'right' ? x - op.w : x;
        ctx.fillRect(left, op.y - Math.round(op.size * 0.35), op.w, 2);
        return;
      }
      ctx.textAlign = op.align;
      x = op.align === 'center' ? widthDots / 2
        : (op.align === 'right' ? widthDots - plan.pad : plan.pad);
      ctx.fillText(op.text, x, op.y);
    });
  }

  // model -> { bitmap_b64, width_dots, height, qr_payload }
  function render(model) {
    var widthDots = model.width_dots === 384 ? 384 : 576;
    return loadFonts().then(function () {
      var scratch = document.createElement('canvas');
      scratch.width = widthDots; scratch.height = 8;
      var plan = layout(model, widthDots, scratch.getContext('2d'));
      var canvas = document.createElement('canvas');
      canvas.width = widthDots; canvas.height = plan.height;
      var ctx = canvas.getContext('2d', { willReadFrequently: true });
      paint(ctx, plan, widthDots);
      var img = ctx.getImageData(0, 0, widthDots, plan.height);
      var packed = packBits(img.data, widthDots, plan.height);
      return {
        bitmap_b64: bytesToBase64(packed),
        width_dots: widthDots,
        height: plan.height,
        qr_payload: model.qr_payload || null,
        canvas: canvas,   // for tests and previews; never sent anywhere
      };
    });
  }

  var api = { render: render, packBits: packBits, bytesToBase64: bytesToBase64,
              wrapWords: wrapWords, hasArabic: hasArabic, THRESHOLD: THRESHOLD };
  root.ReceiptRaster = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
