// Aura icon set. Originally an auto-generated 1:1 Lucide port (Wave 3 #15,
// window.AuraIcons.render(val, size) mapping an emoji or a Lucide name to
// inline SVG, uniform stroke-width, no accent, no motion, no RTL handling --
// exactly the "default open-source icon library" look this pass exists to
// move away from. This pass keeps every path's geometry untouched (no icon
// was hand-redrawn -- see the module comment in the launch-readiness brief:
// blind SVG authoring of 50+ icons produces worse output than Lucide, and
// nobody in this pipeline can render one to check) and layers three things
// on top of the existing 50 icons plus 6 new ones instead:
//
//   1. WEIGHT   structural strokes render at LIGHT_WEIGHT (1.5); the ONE
//      element per icon that carries its meaning -- a handle, a seam, a
//      torn edge, a diaphragm -- renders at HEAVY_WEIGHT (2.25), driven by
//      each icon's own `heavy` index list below rather than by editing any
//      `d=`/`cx=` value. Single-element icons (check, zap, folder, cross)
//      have nothing to contrast against, so their one element simply IS
//      the heavy element.
//   2. ACCENT   at most one small "on/active" sub-shape per icon -- a
//      bullseye centre, a coin, a person's head -- renders FILLED
//      (fill="currentColor") instead of stroked. Most icons have none. tag
//      and key-round already shipped a filled dot in their ORIGINAL Lucide
//      geometry; that is left exactly as it was, not reinjected.
//   3. STATE + MOTION   six icons were added new -- lock-open,
//      triangle-alert, cloud-off, gift, flashlight, scroll-text -- real
//      Lucide v0.469.0 geometry (unpkg.com/lucide-static), fetched rather
//      than hand-drawn, for the identical reason above. lock-open/
//      triangle-alert/cloud-off exist so three real, shipped states (the
//      cash-drawer bar's open/closed, the sync banner's degraded tiers, the
//      Exceptions screen's checking/failed/empty queues) can be told apart
//      by SHAPE, not just by colour or a raw emoji glyph -- see
//      cash-drawer.js's _renderBar, app-shell.js's _renderSyncBanner family
//      and subsystem-retail.js's _exq* panels for the call sites, all of
//      which pass opts.animate:'flip' only when the state actually changed
//      from the previous render (never on an idle repaint -- see each call
//      site's own state-tracking comment). gift/flashlight/scroll-text
//      close the last four nav icons (promotions/scanner/audit-log/
//      exceptions) that were still falling through render()'s raw-string
//      fallback to a literal emoji.
//
// Motion: opts.animate accepts 'pop' (a single-shot confirmation on mount,
// e.g. a just-created success screen) or 'flip' (a single-shot pulse a
// caller requests because it detected a real state change). Neither loops,
// and css/main.css's existing reduced-motion block now covers both classes
// -- see that file's MOTION section.
//
// RTL: exactly one icon in this set is DIRECTIONAL (undo-2 -- "return"
// flips meaning the way an arrow does). Its rendered <svg> carries
// data-aura-dir="mirror"; css/rtl.css's body.rtl rule reads that attribute
// rather than hardcoding a class list, so a non-directional icon (a clock,
// a target, a padlock, a trend arrow that tracks a NUMBER not a reading
// direction) can never be mirrored by accident.
//
// CHROME REDESIGN (owner, 2026-09-07 -- "why is the 2 ai assistant buttons
// ... did you create the responsive animated icons"): four more real Lucide
// v0.469.0 icons -- sparkles, palette, languages, log-out -- replace the
// last raw emoji glyphs left in app-shell.js's chrome (🤖/🎨/🌐/🔑/⏻), same
// fetched-not-drawn rule as the six above. AuraIcons.mark(size, opts), added
// alongside render()/svg() below, is a different thing from an icon: it is
// the Aura BRAND MARK (products/retail/frontend/brand/aura-mark.svg)
// reproduced inline so the shell's brand slot and sign-in screen can render
// it at any size and have its "A" inherit the surrounding text colour --
// see the comment on mark() itself for why and for the gradient-id caveat.
//
// MARK EVOLUTION (2026-09-08 -- the owner read the mark as a generic tech/
// crypto token, not a retail tool): two changes to mark(), applied
// identically everywhere this geometry is duplicated (the five brand SVGs,
// this function, AuraMark.kt, and the launcher vector) -- (1) the A's
// stroke weights raised (peak 19->26, bar 15->20) and its apex narrowed and
// raised (base 80/176->84/172, apex y 76->70) so the A, not the ring, is
// the thing you see first; (2) the soft radial-gradient spark (a blurred
// glow plus a white dot) replaced by a flat, hard-edged diamond in the same
// ink as the A -- no blur, no gradient. Proven, not asserted: rendering the
// OLD aura-mark.svg to a canvas and thresholding it to pure black/white at
// 50% vanished roughly a third of the ring's own sweep (the gradient's teal
// end crosses the threshold), which is why a dedicated
// products/retail/frontend/brand/aura-mark-1bit.svg (one flat ink, no
// gradients) now exists for that print path; the RING here is unchanged --
// this pass touches only the A and the spark.
//
// LOUD FALLBACK (2026-09-08 -- measured on the running till: all eleven
// sections showed emoji on screen, and U+1F4BE/U+1F4E7 rendered as raw
// glyphs on every one of them): render()'s old fallback returned an
// unresolved value AS-IS, so an icon nobody mapped looked exactly as "fine"
// to the author as one that was -- the raw character just shipped. It now
// renders a neutral placeholder (the 'circle-help' entry below) and
// console.warns the unresolved value BY NAME, so the gap is loud in the
// console the moment it ships instead of invisible on the till screen. The
// two confirmed culprits (💾 backup-export, 📧 email-notifications --
// app-shell.js nav items feeding every one of the eleven sections' shared
// chrome) are now mapped below. A third, ☰ (the More-tab hamburger,
// app-shell.js's tabIcon('☰')), turned up by actually running
// retail_shell_chrome_test.js against this fix and reading its own
// console.warn output -- exactly the loud failure this pass exists to
// produce, so it counts as the fallback working, not a miss to feel bad
// about. 'smartphone' and 'ticket' are for another agent's payment grid.
window.AuraIcons = (function () {
  var LIGHT_WEIGHT = 1.5;
  var HEAVY_WEIGHT = 2.25;

  var ICONS = {
  'home': {  // the door -- the entrance is the meaning, not the roofline
    el: [
      "<path d=\"M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8\" />",
      "<path d=\"M3 10a2 2 0 0 1 .709-1.528l7-6a2 2 0 0 1 2.582 0l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z\" />"
    ],
    heavy: [0]
  },
  'package': {  // silhouette-first: 3 thin seam lines stay light so the box does not smear at 18px
    el: [
      "<path d=\"M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z\" />",
      "<path d=\"M12 22V12\" />",
      "<polyline points=\"3.29 7 12 12 20.71 7\" />",
      "<path d=\"m7.5 4.27 9 5.15\" />"
    ],
    heavy: [0]
  },
  'clipboard-list': {  // the clip -- distinguishes it from a bare sheet of paper
    el: [
      "<rect width=\"8\" height=\"4\" x=\"8\" y=\"2\" rx=\"1\" ry=\"1\" />",
      "<path d=\"M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2\" />",
      "<path d=\"M12 11h4\" />",
      "<path d=\"M12 16h4\" />",
      "<path d=\"M8 11h.01\" />",
      "<path d=\"M8 16h.01\" />"
    ],
    heavy: [0]
  },
  'bar-chart-3': {  // the tallest bar -- the one data point that reads as "the point"
    el: [
      "<path d=\"M3 3v16a2 2 0 0 0 2 2h16\" />",
      "<path d=\"M18 17V9\" />",
      "<path d=\"M13 17V5\" />",
      "<path d=\"M8 17v-3\" />"
    ],
    heavy: [2]
  },
  'calendar': {  // the header rule -- separates month chrome from the grid, the calendar-specific tell
    el: [
      "<path d=\"M8 2v4\" />",
      "<path d=\"M16 2v4\" />",
      "<rect width=\"18\" height=\"18\" x=\"3\" y=\"4\" rx=\"2\" />",
      "<path d=\"M3 10h18\" />"
    ],
    heavy: [3]
  },
  'users': {  // foreground figure heavy, its head filled -- the "who" of the icon
    el: [
      "<path d=\"M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2\" />",
      "<path d=\"M16 3.128a4 4 0 0 1 0 7.744\" />",
      "<path d=\"M22 21v-2a4 4 0 0 0-3-3.87\" />",
      "<circle cx=\"9\" cy=\"7\" r=\"4\" />"
    ],
    heavy: [0],
    accent: [3]
  },
  'target': {  // middle ring heavy, bullseye centre filled
    el: [
      "<circle cx=\"12\" cy=\"12\" r=\"10\" />",
      "<circle cx=\"12\" cy=\"12\" r=\"6\" />",
      "<circle cx=\"12\" cy=\"12\" r=\"2\" />"
    ],
    heavy: [1],
    accent: [2]
  },
  'trending-up': {  // the arrowhead -- the direction indicator, not the trend line itself
    el: [
      "<path d=\"M16 7h6v6\" />",
      "<path d=\"m22 7-8.5 8.5-5-5L2 17\" />"
    ],
    heavy: [0]
  },
  'wallet': {  // the fold/clasp with the card-slot cutout -- what makes it a wallet, not a rounded box
    el: [
      "<path d=\"M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1\" />",
      "<path d=\"M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4\" />"
    ],
    heavy: [0]
  },
  'user': {  // shoulders heavy, head filled
    el: [
      "<path d=\"M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2\" />",
      "<circle cx=\"12\" cy=\"7\" r=\"4\" />"
    ],
    heavy: [0],
    accent: [1]
  },
  'factory': {  // outline heavy; the 3 window dots stay light to avoid a blob at 18px
    el: [
      "<path d=\"M12 16h.01\" />",
      "<path d=\"M16 16h.01\" />",
      "<path d=\"M3 19a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8.5a.5.5 0 0 0-.769-.422l-4.462 2.844A.5.5 0 0 1 15 10.5v-2a.5.5 0 0 0-.769-.422L9.77 10.922A.5.5 0 0 1 9 10.5V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2z\" />",
      "<path d=\"M8 16h.01\" />"
    ],
    heavy: [2]
  },
  'settings': {  // the centre hole -- small, identifying; the dense gear teeth stay light for legibility
    el: [
      "<path d=\"M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915\" />",
      "<circle cx=\"12\" cy=\"12\" r=\"3\" />"
    ],
    heavy: [1]
  },
  'stethoscope': {  // the tube leading to the diaphragm heavy, the diaphragm head filled -- the "listening" part
    el: [
      "<path d=\"M11 2v2\" />",
      "<path d=\"M5 2v2\" />",
      "<path d=\"M5 3H4a2 2 0 0 0-2 2v4a6 6 0 0 0 12 0V5a2 2 0 0 0-2-2h-1\" />",
      "<path d=\"M8 15a6 6 0 0 0 12 0v-3\" />",
      "<circle cx=\"20\" cy=\"10\" r=\"2\" />"
    ],
    heavy: [3],
    accent: [4]
  },
  'receipt': {  // the torn-edge outline heavy (this brief's own example); the $ mark stays light
    el: [
      "<path d=\"M12 17V7\" />",
      "<path d=\"M16 8h-6a2 2 0 0 0 0 4h4a2 2 0 0 1 0 4H8\" />",
      "<path d=\"M4 3a1 1 0 0 1 1-1 1.3 1.3 0 0 1 .7.2l.933.6a1.3 1.3 0 0 0 1.4 0l.934-.6a1.3 1.3 0 0 1 1.4 0l.933.6a1.3 1.3 0 0 0 1.4 0l.933-.6a1.3 1.3 0 0 1 1.4 0l.934.6a1.3 1.3 0 0 0 1.4 0l.933-.6A1.3 1.3 0 0 1 19 2a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1 1.3 1.3 0 0 1-.7-.2l-.933-.6a1.3 1.3 0 0 0-1.4 0l-.934.6a1.3 1.3 0 0 1-1.4 0l-.933-.6a1.3 1.3 0 0 0-1.4 0l-.933.6a1.3 1.3 0 0 1-1.4 0l-.934-.6a1.3 1.3 0 0 0-1.4 0l-.933.6a1.3 1.3 0 0 1-.7.2 1 1 0 0 1-1-1z\" />"
    ],
    heavy: [2]
  },
  'flask-conical': {  // the liquid-fill line -- there is something in the flask
    el: [
      "<path d=\"M14 2v6a2 2 0 0 0 .245.96l5.51 10.08A2 2 0 0 1 18 22H6a2 2 0 0 1-1.755-2.96l5.51-10.08A2 2 0 0 0 10 8V2\" />",
      "<path d=\"M6.453 15h11.094\" />",
      "<path d=\"M8.5 2h7\" />"
    ],
    heavy: [1]
  },
  'shopping-cart': {  // body+handle heavy, both wheels filled (one paired feature, not two)
    el: [
      "<circle cx=\"8\" cy=\"21\" r=\"1\" />",
      "<circle cx=\"19\" cy=\"21\" r=\"1\" />",
      "<path d=\"M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12\" />"
    ],
    heavy: [2],
    accent: [0, 1]
  },
  'shopping-bag': {  // the handle loop
    el: [
      "<path d=\"M16 10a4 4 0 0 1-8 0\" />",
      "<path d=\"M3.103 6.034h17.794\" />",
      "<path d=\"M3.4 5.467a2 2 0 0 0-.4 1.2V20a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6.667a2 2 0 0 0-.4-1.2l-2-2.667A2 2 0 0 0 17 2H7a2 2 0 0 0-1.6.8z\" />"
    ],
    heavy: [0]
  },
  'rocket': {  // the exhaust flame -- the launch, not the hull
    el: [
      "<path d=\"M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5\" />",
      "<path d=\"M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09\" />",
      "<path d=\"M9 12a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.4 22.4 0 0 1-4 2z\" />",
      "<path d=\"M9 12H4s.55-3.03 2-4c1.62-1.08 5 .05 5 .05\" />"
    ],
    heavy: [1]
  },
  'repeat': {  // both arrowheads -- one feature (the loop's direction), split across 2 elements by how Lucide draws it
    el: [
      "<path d=\"m17 2 4 4-4 4\" />",
      "<path d=\"M3 11v-1a4 4 0 0 1 4-4h14\" />",
      "<path d=\"m7 22-4-4 4-4\" />",
      "<path d=\"M21 13v1a4 4 0 0 1-4 4H3\" />"
    ],
    heavy: [0, 2]
  },
  'download': {  // the arrowhead
    el: [
      "<path d=\"M12 15V3\" />",
      "<path d=\"M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4\" />",
      "<path d=\"m7 10 5 5 5-5\" />"
    ],
    heavy: [2]
  },
  'upload': {  // the arrowhead
    el: [
      "<path d=\"M12 3v12\" />",
      "<path d=\"m17 8-5-5-5 5\" />",
      "<path d=\"M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4\" />"
    ],
    heavy: [1]
  },
  'megaphone': {  // the handle
    el: [
      "<path d=\"M11 6a13 13 0 0 0 8.4-2.8A1 1 0 0 1 21 4v12a1 1 0 0 1-1.6.8A13 13 0 0 0 11 14H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z\" />",
      "<path d=\"M6 14a12 12 0 0 0 2.4 7.2 2 2 0 0 0 3.2-2.4A8 8 0 0 1 10 14\" />",
      "<path d=\"M8 6v8\" />"
    ],
    heavy: [2]
  },
  'square-pen': {  // the pen -- the tool, not the square it is editing
    el: [
      "<path d=\"M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7\" />",
      "<path d=\"M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z\" />"
    ],
    heavy: [1]
  },
  'pin': {  // the point -- where it actually pins
    el: [
      "<path d=\"M12 17v5\" />",
      "<path d=\"M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z\" />"
    ],
    heavy: [0]
  },
  'contact': {  // the card outline heavy (it is a CARD, not a plain person), face filled
    el: [
      "<path d=\"M16 2v2\" />",
      "<path d=\"M7 22v-2a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v2\" />",
      "<path d=\"M8 2v2\" />",
      "<circle cx=\"12\" cy=\"11\" r=\"3\" />",
      "<rect x=\"3\" y=\"4\" width=\"18\" height=\"18\" rx=\"2\" />"
    ],
    heavy: [4],
    accent: [3]
  },
  'file-text': {  // the folded corner -- the "this is a file" tell
    el: [
      "<path d=\"M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z\" />",
      "<path d=\"M14 2v5a1 1 0 0 0 1 1h5\" />",
      "<path d=\"M10 9H8\" />",
      "<path d=\"M16 13H8\" />",
      "<path d=\"M16 17H8\" />"
    ],
    heavy: [1]
  },
  'folder': {  // single element, nothing to contrast against
    el: [
      "<path d=\"M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z\" />"
    ],
    heavy: [0]
  },
  'briefcase': {  // the handle -- literally what you carry it by
    el: [
      "<path d=\"M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16\" />",
      "<rect width=\"20\" height=\"14\" x=\"2\" y=\"6\" rx=\"2\" />"
    ],
    heavy: [0]
  },
  'banknote': {  // the bill edge heavy, the coin filled
    el: [
      "<rect width=\"20\" height=\"12\" x=\"2\" y=\"6\" rx=\"2\" />",
      "<circle cx=\"12\" cy=\"12\" r=\"2\" />",
      "<path d=\"M6 12h.01M18 12h.01\" />"
    ],
    heavy: [0],
    accent: [1]
  },
  'credit-card': {  // the magnetic stripe -- the card-specific tell, not the rounded rect
    el: [
      "<rect width=\"20\" height=\"14\" x=\"2\" y=\"5\" rx=\"2\" />",
      "<line x1=\"2\" x2=\"22\" y1=\"10\" y2=\"10\" />"
    ],
    heavy: [1]
  },
  'pill': {  // the seam dividing the two-tone capsule
    el: [
      "<path d=\"m10.5 20.5 10-10a4.95 4.95 0 1 0-7-7l-10 10a4.95 4.95 0 1 0 7 7Z\" />",
      "<path d=\"m8.5 8.5 7 7\" />"
    ],
    heavy: [1]
  },
  'user-round': {  // shoulders heavy, head filled
    el: [
      "<circle cx=\"12\" cy=\"8\" r=\"5\" />",
      "<path d=\"M20 21a8 8 0 0 0-16 0\" />"
    ],
    heavy: [1],
    accent: [0]
  },
  'tag': {  // outline heavy; element 1's fill=currentColor already shipped in the original Lucide data, left as-is
    el: [
      "<path d=\"M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z\" />",
      "<circle cx=\"7.5\" cy=\"7.5\" r=\".5\" fill=\"currentColor\" />"
    ],
    heavy: [0]
  },
  'landmark': {  // the pediment/roof -- the government-building tell, not the columns
    el: [
      "<path d=\"M10 18v-7\" />",
      "<path d=\"M11.119 2.205a2 2 0 0 1 1.762 0l7.84 3.846A.5.5 0 0 1 20.5 7h-17a.5.5 0 0 1-.22-.949z\" />",
      "<path d=\"M14 18v-7\" />",
      "<path d=\"M18 18v-7\" />",
      "<path d=\"M3 22h18\" />",
      "<path d=\"M6 18v-7\" />"
    ],
    heavy: [1]
  },
  'cross': {  // single element
    el: [
      "<path d=\"M4 9a2 2 0 0 0-2 2v2a2 2 0 0 0 2 2h4a1 1 0 0 1 1 1v4a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2v-4a1 1 0 0 1 1-1h4a2 2 0 0 0 2-2v-2a2 2 0 0 0-2-2h-4a1 1 0 0 1-1-1V4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4a1 1 0 0 1-1 1z\" />"
    ],
    heavy: [0]
  },
  'trophy': {  // the bowl -- silhouette-first, 5 thin handle/stem pieces stay light
    el: [
      "<path d=\"M10 14.66v1.626a2 2 0 0 1-.976 1.696A5 5 0 0 0 7 21.978\" />",
      "<path d=\"M14 14.66v1.626a2 2 0 0 0 .976 1.696A5 5 0 0 1 17 21.978\" />",
      "<path d=\"M18 9h1.5a1 1 0 0 0 0-5H18\" />",
      "<path d=\"M4 22h16\" />",
      "<path d=\"M6 9a6 6 0 0 0 12 0V3a1 1 0 0 0-1-1H7a1 1 0 0 0-1 1z\" />",
      "<path d=\"M6 9H4.5a1 1 0 0 1 0-5H6\" />"
    ],
    heavy: [4]
  },
  'palmtree': {  // the trunk (both pieces) -- the identifying part, not the frond blobs
    el: [
      "<path d=\"M13 8c0-2.76-2.46-5-5.5-5S2 5.24 2 8h2l1-1 1 1h4\" />",
      "<path d=\"M13 7.14A5.82 5.82 0 0 1 16.5 6c3.04 0 5.5 2.24 5.5 5h-3l-1-1-1 1h-3\" />",
      "<path d=\"M5.89 9.71c-2.15 2.15-2.3 5.47-.35 7.43l4.24-4.25.7-.7.71-.71 2.12-2.12c-1.95-1.96-5.27-1.8-7.42.35\" />",
      "<path d=\"M11 15.5c.5 2.5-.17 4.5-1 6.5h4c2-5.5-.5-12-1-14\" />"
    ],
    heavy: [2, 3]
  },
  'waves': {  // one wave brought forward; the other two recede
    el: [
      "<path d=\"M2 12q2.5 2 5 0t5 0 5 0 5 0\" />",
      "<path d=\"M2 19q2.5 2 5 0t5 0 5 0 5 0\" />",
      "<path d=\"M2 5q2.5 2 5 0t5 0 5 0 5 0\" />"
    ],
    heavy: [0]
  },
  'zap': {  // single element
    el: [
      "<path d=\"M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z\" />"
    ],
    heavy: [0]
  },
  'scale': {  // the crossbar -- the balance beam is what makes it read as a scale
    el: [
      "<path d=\"M12 3v18\" />",
      "<path d=\"m19 8 3 8a5 5 0 0 1-6 0zV7\" />",
      "<path d=\"M3 7h1a17 17 0 0 0 8-2 17 17 0 0 0 8 2h1\" />",
      "<path d=\"m5 8 3 8a5 5 0 0 1-6 0zV7\" />",
      "<path d=\"M7 21h10\" />"
    ],
    heavy: [2]
  },
  'timer': {  // the hand -- the moving, meaningful part; the knob and face stay light
    el: [
      "<line x1=\"10\" x2=\"14\" y1=\"2\" y2=\"2\" />",
      "<line x1=\"12\" x2=\"15\" y1=\"14\" y2=\"11\" />",
      "<circle cx=\"12\" cy=\"14\" r=\"8\" />"
    ],
    heavy: [1]
  },
  'undo-2': {  // arrowhead heavy; DIRECTIONAL -- return/back flips meaning under RTL
    el: [
      "<path d=\"M9 14 4 9l5-5\" />",
      "<path d=\"M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11\" />"
    ],
    heavy: [0],
    dir: "mirror"
  },
  'search': {  // the handle/drag stroke; the lens stays light (filling it would stop reading as a lens)
    el: [
      "<path d=\"m21 21-4.34-4.34\" />",
      "<circle cx=\"11\" cy=\"11\" r=\"8\" />"
    ],
    heavy: [0]
  },
  'printer': {  // the output tray -- the part with the receipt in it
    el: [
      "<path d=\"M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2\" />",
      "<path d=\"M6 9V3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v6\" />",
      "<rect x=\"6\" y=\"14\" width=\"12\" height=\"8\" rx=\"1\" />"
    ],
    heavy: [2]
  },
  'x': {  // one stroke brought forward for a touch of asymmetry; non-directional (must NOT carry the RTL mirror flag)
    el: [
      "<path d=\"M18 6 6 18\" />",
      "<path d=\"m6 6 12 12\" />"
    ],
    heavy: [0]
  },
  'check': {  // single element -- cannot be filled without redrawing an open polyline into new geometry
    el: [
      "<path d=\"M20 6 9 17l-5-5\" />"
    ],
    heavy: [0]
  },
  'lock': {  // the shackle -- the locked mechanism, not the body
    el: [
      "<rect width=\"18\" height=\"11\" x=\"3\" y=\"11\" rx=\"2\" ry=\"2\" />",
      "<path d=\"M7 11V7a5 5 0 0 1 10 0v4\" />"
    ],
    heavy: [1]
  },
  'mail': {  // the flap -- the envelope-specific tell
    el: [
      "<path d=\"m22 7-8.991 5.727a2 2 0 0 1-2.009 0L2 7\" />",
      "<rect x=\"2\" y=\"4\" width=\"20\" height=\"16\" rx=\"2\" />"
    ],
    heavy: [0]
  },
  'key-round': {  // shaft heavy; element 1's fill=currentColor already shipped in the original Lucide data, left as-is
    el: [
      "<path d=\"M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z\" />",
      "<circle cx=\"16.5\" cy=\"7.5\" r=\".5\" fill=\"currentColor\" />"
    ],
    heavy: [0]
  },
  'circle-check-big': {  // the checkmark heavy, the ring light -- filling the check is not possible without redrawing it
    el: [
      "<path d=\"M21.801 10A10 10 0 1 1 17 3.335\" />",
      "<path d=\"m9 11 3 3L22 4\" />"
    ],
    heavy: [1]
  },
  'lock-open': {  // same shackle-heavy choice as "lock" so the open/closed pair reads as one state flipping
    el: [
      "<rect width=\"18\" height=\"11\" x=\"3\" y=\"11\" rx=\"2\" ry=\"2\" />",
      "<path d=\"M7 11V7a5 5 0 0 1 9.9-1\" />"
    ],
    heavy: [1]
  },
  'triangle-alert': {  // stem + dot -- one feature (the exclamation mark); the triangle itself stays light
    el: [
      "<path d=\"m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3\" />",
      "<path d=\"M12 9v4\" />",
      "<path d=\"M12 17h.01\" />"
    ],
    heavy: [1, 2]
  },
  'cloud-off': {  // the slash -- the negation is the meaning
    el: [
      "<path d=\"m2 2 20 20\" />",
      "<path d=\"M5.782 5.782A7 7 0 0 0 9 19h8.5a4.5 4.5 0 0 0 1.307-.193\" />",
      "<path d=\"M21.532 16.5A4.5 4.5 0 0 0 17.5 10h-1.79A7.008 7.008 0 0 0 10 5.07\" />"
    ],
    heavy: [0]
  },
  'gift': {  // the bow -- a box is just a box without it
    el: [
      "<rect x=\"3\" y=\"8\" width=\"18\" height=\"4\" rx=\"1\" />",
      "<path d=\"M12 8v13\" />",
      "<path d=\"M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7\" />",
      "<path d=\"M7.5 8a2.5 2.5 0 0 1 0-5A4.8 8 0 0 1 12 8a4.8 8 0 0 1 4.5-5 2.5 2.5 0 0 1 0 5\" />"
    ],
    heavy: [3]
  },
  'flashlight': {  // the lens dot (boosted stroke, not fill -- it is a zero-length path, filling it renders nothing)
    el: [
      "<path d=\"M18 6c0 2-2 2-2 4v10a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2V10c0-2-2-2-2-4V2h12z\" />",
      "<line x1=\"6\" x2=\"18\" y1=\"6\" y2=\"6\" />",
      "<line x1=\"12\" x2=\"12\" y1=\"12\" y2=\"12\" />"
    ],
    heavy: [2]
  },
  'scroll-text': {  // the bottom curl -- the "rolled parchment" tell
    el: [
      "<path d=\"M15 12h-5\" />",
      "<path d=\"M15 8h-5\" />",
      "<path d=\"M19 17V5a2 2 0 0 0-2-2H4\" />",
      "<path d=\"M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3\" />"
    ],
    heavy: [3]
  },
  'sparkles': {  // the four-point burst -- the AI-assist tell; the two companion sparkles stay light
    el: [
      "<path d=\"M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z\" />",
      "<path d=\"M20 3v4\" />",
      "<path d=\"M22 5h-4\" />",
      "<path d=\"M4 17v2\" />",
      "<path d=\"M5 18H3\" />"
    ],
    heavy: [0]
  },
  'palette': {  // outline heavy; the four colour dots already ship fill=currentColor in the original Lucide data, left as-is -- same convention as tag/key-round above
    el: [
      "<circle cx=\"13.5\" cy=\"6.5\" r=\".5\" fill=\"currentColor\" />",
      "<circle cx=\"17.5\" cy=\"10.5\" r=\".5\" fill=\"currentColor\" />",
      "<circle cx=\"8.5\" cy=\"7.5\" r=\".5\" fill=\"currentColor\" />",
      "<circle cx=\"6.5\" cy=\"12.5\" r=\".5\" fill=\"currentColor\" />",
      "<path d=\"M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z\" />"
    ],
    heavy: [4]
  },
  'languages': {  // the Latin A's peak -- the second alphabet, the pairing that reads as translation rather than one logogram
    el: [
      "<path d=\"m5 8 6 6\" />",
      "<path d=\"m4 14 6-6 2-3\" />",
      "<path d=\"M2 5h12\" />",
      "<path d=\"M7 2h1\" />",
      "<path d=\"m22 22-5-10-5 10\" />",
      "<path d=\"M14 18h6\" />"
    ],
    heavy: [4]
  },
  'log-out': {  // the arrowhead -- same choice as download/upload above, the direction is the meaning
    el: [
      "<path d=\"M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4\" />",
      "<polyline points=\"16 17 21 12 16 7\" />",
      "<line x1=\"21\" x2=\"9\" y1=\"12\" y2=\"12\" />"
    ],
    heavy: [1]
  },
  'save': {  // the label rectangle -- the floppy-specific tell, not the folded-corner body
    el: [
      "<path d=\"M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z\" />",
      "<path d=\"M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7\" />",
      "<path d=\"M7 3v4a1 1 0 0 0 1 1h7\" />"
    ],
    heavy: [1]
  },
  'smartphone': {  // the home button dot (zero-length path, boosted stroke not fill -- same convention as flashlight's lens)
    el: [
      "<rect width=\"14\" height=\"20\" x=\"5\" y=\"2\" rx=\"2\" ry=\"2\" />",
      "<path d=\"M12 18h.01\" />"
    ],
    heavy: [1]
  },
  'ticket': {  // the perforation dashes -- the tear-line is what makes it a ticket, not a rounded card
    el: [
      "<path d=\"M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z\" />",
      "<path d=\"M13 5v2\" />",
      "<path d=\"M13 17v2\" />",
      "<path d=\"M13 11v2\" />"
    ],
    heavy: [1, 2, 3]
  },
  'circle-help': {  // the question mark's stem+dot, one feature (same bundling as triangle-alert); the ring stays light --
                     // this is render()'s LOUD FALLBACK placeholder, never a name the app maps an emoji to
    el: [
      "<circle cx=\"12\" cy=\"12\" r=\"10\" />",
      "<path d=\"M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3\" />",
      "<path d=\"M12 17h.01\" />"
    ],
    heavy: [1, 2]
  },
  'menu': {  // the top bar brought forward; the other two recede -- same asymmetry choice as 'waves' (3 identical bars, nothing else to contrast)
    el: [
      "<line x1=\"4\" x2=\"20\" y1=\"12\" y2=\"12\" />",
      "<line x1=\"4\" x2=\"20\" y1=\"6\" y2=\"6\" />",
      "<line x1=\"4\" x2=\"20\" y1=\"18\" y2=\"18\" />"
    ],
    heavy: [1]
  },
  // ── 2026-09-08, the last raw emoji left in a rendered surface ────────────
  // Twelve more real Lucide v0.469.0 icons, geometry fetched verbatim from
  // unpkg.com/lucide-static and never hand-drawn -- the same rule the six
  // state icons and the four chrome icons above were added under, and the
  // same reason (nobody in this pipeline can render an SVG to check it).
  //
  // They close four surfaces that were still drawing SYSTEM EMOJI, whose
  // appearance belongs to the OS font and which cannot take a theme token:
  //   inbox                     the Stock Accuracy "nothing to check" panel,
  //                             the one _stka* state the Exceptions screen's
  //                             equivalent (_exqEmpty) had no name for.
  //   bot / trash-2 / send      the AI panel's assistant avatar and its only
  //                             two controls.
  //   laptop / shirt / sandwich / cup-soda / gem / footprints / dumbbell /
  //   flower                    the ten POS category tiles -- the busiest
  //                             screen in the product, where only 🛒 and 📦
  //                             of the ten resolved.
  'inbox': {  // the tray's fold line -- an open box is just a box without it
    el: [
      "<polyline points=\"22 12 16 12 14 15 10 15 8 12 2 12\" />",
      "<path d=\"M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z\" />"
    ],
    heavy: [0]
  },
  'bot': {  // the two eyes -- what makes the rounded box a face rather than a crate
    el: [
      "<path d=\"M12 8V4H8\" />",
      "<rect width=\"16\" height=\"12\" x=\"4\" y=\"8\" rx=\"2\" />",
      "<path d=\"M2 14h2\" />",
      "<path d=\"M20 14h2\" />",
      "<path d=\"M15 13v2\" />",
      "<path d=\"M9 13v2\" />"
    ],
    heavy: [4, 5]
  },
  'trash-2': {  // the lid bar -- the tell that separates a bin from a cup
    el: [
      "<path d=\"M3 6h18\" />",
      "<path d=\"M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6\" />",
      "<path d=\"M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2\" />",
      "<line x1=\"10\" x2=\"10\" y1=\"11\" y2=\"17\" />",
      "<line x1=\"14\" x2=\"14\" y1=\"11\" y2=\"17\" />"
    ],
    heavy: [0]
  },
  'send': {  // the paper plane's body; the crease stays light
    el: [
      "<path d=\"M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z\" />",
      "<path d=\"m21.854 2.147-10.94 10.939\" />"
    ],
    heavy: [0]
  },
  'laptop': {  // single element -- nothing to contrast against, so it IS the heavy one
    el: [
      "<path d=\"M20 16V7a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v9m16 0H4m16 0 1.28 2.55a1 1 0 0 1-.9 1.45H3.62a1 1 0 0 1-.9-1.45L4 16\" />"
    ],
    heavy: [0]
  },
  'shirt': {  // single element, same as laptop
    el: [
      "<path d=\"M20.38 3.46 16 2a4 4 0 0 1-8 0L3.62 3.46a2 2 0 0 0-1.34 2.23l.58 3.47a1 1 0 0 0 .99.84H6v10c0 1.1.9 2 2 2h8a2 2 0 0 0 2-2V10h2.15a1 1 0 0 0 .99-.84l.58-3.47a2 2 0 0 0-1.34-2.23z\" />"
    ],
    heavy: [0]
  },
  'sandwich': {  // the filling bar -- the layer that makes it a sandwich, not a roof
    el: [
      "<path d=\"m2.37 11.223 8.372-6.777a2 2 0 0 1 2.516 0l8.371 6.777\" />",
      "<path d=\"M21 15a1 1 0 0 1 1 1v2a1 1 0 0 1-1 1h-5.25\" />",
      "<path d=\"M3 15a1 1 0 0 0-1 1v2a1 1 0 0 0 1 1h9\" />",
      "<path d=\"m6.67 15 6.13 4.6a2 2 0 0 0 2.8-.4l3.15-4.2\" />",
      "<rect width=\"20\" height=\"4\" x=\"2\" y=\"11\" rx=\"1\" />"
    ],
    heavy: [4]
  },
  'cup-soda': {  // the straw -- a tapered cup without it is a plant pot
    el: [
      "<path d=\"m6 8 1.75 12.28a2 2 0 0 0 2 1.72h4.54a2 2 0 0 0 2-1.72L18 8\" />",
      "<path d=\"M5 8h14\" />",
      "<path d=\"M7 15a6.47 6.47 0 0 1 5 0 6.47 6.47 0 0 0 5 0\" />",
      "<path d=\"m12 8 1-6h2\" />"
    ],
    heavy: [3]
  },
  'gem': {  // the crown facets -- the cut is the meaning; the outline stays light
    el: [
      "<path d=\"M6 3h12l4 6-10 13L2 9Z\" />",
      "<path d=\"M11 3 8 9l4 13 4-13-3-6\" />",
      "<path d=\"M2 9h20\" />"
    ],
    heavy: [1]
  },
  'footprints': {  // the toe bars -- what turns two blobs into footprints
    el: [
      "<path d=\"M4 16v-2.38C4 11.5 2.97 10.5 3 8c.03-2.72 1.49-6 4.5-6C9.37 2 10 3.8 10 5.5c0 3.11-2 5.66-2 8.68V16a2 2 0 1 1-4 0Z\" />",
      "<path d=\"M20 20v-2.38c0-2.12 1.03-3.12 1-5.62-.03-2.72-1.49-6-4.5-6C14.63 6 14 7.8 14 9.5c0 3.11 2 5.66 2 8.68V20a2 2 0 1 0 4 0Z\" />",
      "<path d=\"M16 17h4\" />",
      "<path d=\"M4 13h4\" />"
    ],
    heavy: [2, 3]
  },
  'dumbbell': {  // the bar -- the two plates read as anything without it
    el: [
      "<path d=\"M14.4 14.4 9.6 9.6\" />",
      "<path d=\"M18.657 21.485a2 2 0 1 1-2.829-2.828l-1.767 1.768a2 2 0 1 1-2.829-2.829l6.364-6.364a2 2 0 1 1 2.829 2.829l-1.768 1.767a2 2 0 1 1 2.828 2.829z\" />",
      "<path d=\"m21.5 21.5-1.4-1.4\" />",
      "<path d=\"M3.9 3.9 2.5 2.5\" />",
      "<path d=\"M6.404 12.768a2 2 0 1 1-2.829-2.829l1.768-1.767a2 2 0 1 1-2.828-2.829l2.828-2.828a2 2 0 1 1 2.829 2.828l1.767-1.768a2 2 0 1 1 2.829 2.829z\" />"
    ],
    heavy: [0]
  },
  'flower': {  // the centre -- the pistil is the one feature the eight petals frame
    el: [
      "<circle cx=\"12\" cy=\"12\" r=\"3\" />",
      "<path d=\"M12 16.5A4.5 4.5 0 1 1 7.5 12 4.5 4.5 0 1 1 12 7.5a4.5 4.5 0 1 1 4.5 4.5 4.5 4.5 0 1 1-4.5 4.5\" />",
      "<path d=\"M12 7.5V9\" />",
      "<path d=\"M7.5 12H9\" />",
      "<path d=\"M16.5 12H15\" />",
      "<path d=\"M12 16.5V15\" />",
      "<path d=\"m8 8 1.88 1.88\" />",
      "<path d=\"M14.12 9.88 16 8\" />",
      "<path d=\"m8 16 1.88-1.88\" />",
      "<path d=\"M14.12 14.12 16 16\" />"
    ],
    heavy: [0]
  }
  };

  var EMOJI = {
    "🏠": "home",
    "📦": "package",
    "📋": "clipboard-list",
    "📊": "bar-chart-3",
    "📅": "calendar",
    "👥": "users",
    "🎯": "target",
    "📈": "trending-up",
    "💰": "wallet",
    "👤": "user",
    "🏭": "factory",
    "⚙️": "settings",
    "🩺": "stethoscope",
    "🧾": "receipt",
    "🧪": "flask-conical",
    "🛒": "shopping-cart",
    "🛍️": "shopping-bag",
    "🚀": "rocket",
    "🔁": "repeat",
    "📥": "download",
    "📤": "upload",
    "📢": "megaphone",
    "📝": "square-pen",
    "📌": "pin",
    "📇": "contact",
    "📄": "file-text",
    "📂": "folder",
    "💼": "briefcase",
    "💸": "banknote",
    "💳": "credit-card",
    "💊": "pill",
    "👨‍⚕️": "user-round",
    "🏷️": "tag",
    "🏦": "landmark",
    "🏥": "cross",
    "🏆": "trophy",
    "🌴": "palmtree",
    "🌊": "waves",
    "⚡": "zap",
    "⚖️": "scale",
    "⏱️": "timer",
    "↩️": "undo-2",
    "🔍": "search",
    "🖨": "printer",
    "✕": "x",
    "✓": "check",
    "🎁": "gift",
    "🔦": "flashlight",
    "📜": "scroll-text",
    "⚠️": "triangle-alert",
    "💾": "save",
    "📧": "mail",
    "☰": "menu",
    // 2026-09-08 -- the glyphs still being drawn raw in a rendered surface.
    // 🔒 is the one that surprises: 'lock' has been in ICONS since the first
    // port, but no EMOJI entry pointed at it, so every capability-restricted
    // screen (the panel a cashier sees INSTEAD of a 403) drew a 40px system
    // padlock. The rest are new geometry added just above.
    "🔒": "lock",
    "📭": "inbox",
    "🤖": "bot",
    "🗑": "trash-2",
    "🗑️": "trash-2",
    "➤": "send",
    "🙂": "user-round",
    "💻": "laptop",
    "👕": "shirt",
    "🍔": "sandwich",
    "🥤": "cup-soda",
    "💍": "gem",
    "👟": "footprints",
    "⚽": "dumbbell",
    "💄": "flower"
  };

  // Every element is one self-closing tag (<path .../>, <circle .../>,
  // <rect .../>, <line .../>, <polyline .../>) -- inserting right before the
  // closing "/>" works for all of them without needing to know which.
  function injectAttr(tag, attr) {
    return tag.replace(/\s*\/>\s*$/, ' ' + attr + ' />');
  }

  function buildBody(icon) {
    var heavy = {}, accent = {};
    (icon.heavy || []).forEach(function (i) { heavy[i] = true; });
    (icon.accent || []).forEach(function (i) { accent[i] = true; });
    return icon.el.map(function (el, i) {
      // Accent wins if an index were ever (mis)marked as both -- a filled
      // shape's stroke-width is visually moot, so there's no reason to
      // fight over it.
      if (accent[i]) return injectAttr(el, 'fill="currentColor"');
      if (heavy[i]) return injectAttr(el, 'stroke-width="' + HEAVY_WEIGHT + '"');
      return el;
    }).join('\n  ');
  }

  // svg(name, size, opts) -- opts.animate: 'pop' | 'flip' | undefined (see
  // module comment above). Both classes are no-ops unless css/main.css
  // defines the keyframes, and both are killed under prefers-reduced-motion
  // by that same stylesheet's existing MOTION block.
  function svg(name, size, opts) {
    var icon = ICONS[name];
    if (!icon) return '';
    opts = opts || {};
    size = size || 18;
    var animClass = opts.animate === 'pop' ? ' aura-ic-pop'
      : opts.animate === 'flip' ? ' aura-ic-flip' : '';
    var dirAttr = icon.dir === 'mirror' ? ' data-aura-dir="mirror"' : '';
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + size + '" height="' + size +
      '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="' + LIGHT_WEIGHT +
      '" stroke-linecap="round" stroke-linejoin="round" class="aura-ic' + animClass +
      '" style="vertical-align:middle;flex-shrink:0"' + dirAttr + '>' + buildBody(icon) + '</svg>';
  }

  // Render an icon value: if it's a known emoji, return the signature SVG;
  // if it's a known Lucide-style name, same; otherwise the caller handed us
  // something nobody mapped. Returning that value AS-IS (the pre-2026-09-08
  // behaviour) is exactly how all eleven sections quietly shipped raw emoji
  // to the till screen and nobody noticed -- an unmapped icon looked just
  // as "fine" to the author as a mapped one, because it still rendered
  // something. Render the neutral placeholder instead and warn loudly, BY
  // NAME, so the gap is visible in the console the moment it ships rather
  // than staying invisible on screen.
  function render(val, size, opts) {
    if (val == null) return '';
    var name = EMOJI[val];
    if (name) return svg(name, size, opts);
    if (ICONS[val]) return svg(val, size, opts);
    console.warn('AuraIcons.render(): no icon mapped for ' + JSON.stringify(val) +
      ' -- rendering the placeholder glyph instead of the raw value.');
    return svg('circle-help', size, opts);
  }

  // Back-compat surface: the pre-redesign file exposed PATHS as name -> raw
  // markup. Rebuilt here from the SAME el arrays svg() renders from (never a
  // second copy of the geometry), unweighted -- i.e. PATHS reflects the
  // original geometry, not the heavy/accent treatment; callers that want the
  // signature must go through render()/svg() as before.
  var PATHS = {};
  Object.keys(ICONS).forEach(function (name) { PATHS[name] = ICONS[name].el.join('\n  '); });

  // mark(size, opts) -- the Aura BRAND MARK, not an icon from ICONS above.
  // Geometry copied verbatim from products/retail/frontend/brand/aura-mark.svg
  // (viewBox, ring, spark, A, bar -- every coordinate the same file the brand
  // asset itself defines); this is not a second source of truth for that
  // geometry, it is the same numbers inlined so the shell can render the
  // mark at an arbitrary size without an <img> request. Two deliberate
  // departures from the static file:
  //
  //   1. The A and its bar stroke `currentColor` instead of the asset's
  //      fixed #0f1319 ink. The static brand/ directory ships a SECOND file
  //      (aura-mark-on-dark.svg) purely to swap that one colour for dark
  //      surfaces; a mark that inherits the surrounding text colour makes
  //      that second file unnecessary here, and it can never go stale
  //      against whichever of the five sanctioned themes (THEME_NAMES in
  //      app-shell.js) is active -- there were only ever two files for what
  //      is now five palettes.
  //   2. The ring's gradient id carries a per-call counter (aura-ring-N),
  //      never the static file's bare "aura-ring". SVG gradient ids are ONE
  //      flat namespace across the whole document, not scoped to their own
  //      <svg>; this shell renders the mark more than once per page
  //      (sidebar brand slot + sign-in overlay, at minimum), and two
  //      fragments both defining #aura-ring would collide -- the SECOND
  //      one's stroke="url(#aura-ring)" would silently resolve to the
  //      FIRST fragment's gradient (duplicate ids resolve to the first
  //      match), so the mark would render with the wrong ring and no error
  //      at all. The beacon (below) needs no such id: it is a flat
  //      `currentColor` fill, not a gradient, so it cannot collide.
  //
  // 2026-09-08: the A's stroke weights and apex were raised/tightened, and
  // the old radial-gradient spark (a blurred glow plus a white dot) was
  // replaced by a flat `currentColor` diamond at the same point -- see the
  // MARK EVOLUTION comment above this IIFE for the full reasoning and the
  // measurement that proves the old gradient spark's softness had nothing
  // to do with print safety (a `currentColor` fill has none of the
  // gradient ring's banding problem; only the ring itself needs the
  // dedicated aura-mark-1bit.svg for that path).
  //
  // The ring gradient colour literals below are allowed by
  // retail_design_tokens_test.js's `.aura-logo` exemption -- fixed brand
  // colour, not a themed surface (see that file's EXEMPTIONS list).
  var markCounter = 0;
  function mark(size, opts) {
    opts = opts || {};
    size = size || 40;
    var n = ++markCounter;
    var ringId = 'aura-ring-' + n;
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="' + size + '" height="' + size +
      '" class="aura-ic aura-mark" data-aura-mark="1" aria-hidden="true" focusable="false">' +
        '<defs>' +
          '<linearGradient id="' + ringId + '" x1="0.15" y1="0.9" x2="0.85" y2="0.1">' +
            '<stop offset="0" stop-color="#1745a9" />' +
            '<stop offset="0.55" stop-color="#3f7be6" />' +
            '<stop offset="1" stop-color="#5fe3d0" />' +
          '</linearGradient>' +
        '</defs>' +
        '<circle cx="128" cy="128" r="94" fill="none" stroke="url(#' + ringId + ')" stroke-width="15" ' +
          'stroke-linecap="round" stroke-dasharray="492 99" stroke-dashoffset="-32" transform="rotate(-90 128 128)" />' +
        '<path d="M 196 45 L 211 60 L 196 75 L 181 60 Z" fill="currentColor" />' +
        '<path d="M 84 178 L 128 70 L 172 178" fill="none" stroke="currentColor" stroke-width="26" ' +
          'stroke-linecap="round" stroke-linejoin="round" />' +
        '<path d="M 108 142 L 148 142" fill="none" stroke="currentColor" stroke-width="20" stroke-linecap="round" />' +
      '</svg>';
  }

  return { svg: svg, render: render, mark: mark, PATHS: PATHS, EMOJI: EMOJI, ICONS: ICONS };
})();
