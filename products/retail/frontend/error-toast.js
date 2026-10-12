// error-toast.js -- SUPPORT-1 item 3: when something breaks, tell the shopkeeper
// quietly that a report was saved, and give them the code to quote.
//
// WHY A SEPARATE FILE. The page already reports its own JS errors
// (app-shell.js, "Crash reports: the browser's half") and the server already
// records its own 500s, but the person at the till saw nothing and had nothing
// to say to support. The server now puts the 6-character reference code of a
// report it REALLY saved in the X-Aura-Report-Code response header (on the 204
// that answers a client report, and on a captured 500). This file watches
// responses for that header and shows one quiet toast. It wraps window.fetch
// instead of editing app-shell.js so the shell stays untouched.
//
// THE RULES (each one has a test in retail_error_toast_test.js):
//   * no header, no toast: a report that was not saved (rate-limited, store
//     failed, anonymous) must never produce a toast with an invented code;
//   * the header is only trusted if it is exactly 6 characters of the code
//     alphabet -- anything else is ignored, never rendered;
//   * at most one toast per 60 s, so a render loop cannot paper the screen;
//   * non-blocking: it goes through the shell's own toast (which already moves
//     to the top corner while the till is on screen so it never covers the
//     tender tiles or the Charge button); no modal, no focus change;
//   * the wrapper hands back the SAME promise fetch returned and swallows every
//     error of its own: it can never change a request's result or throw into
//     the code that made it.
(function (root) {
  'use strict';

  var HEADER = 'X-Aura-Report-Code';
  var CODE_RE = /^[2-9A-HJ-NP-Z]{6}$/;
  var MIN_GAP_MS = 60 * 1000;

  function create(win, now) {
    var clock = now || function () { return Date.now(); };
    var lastShown = null;

    function message(code) {
      var tr = typeof win.t === 'function' ? win.t : function (s) { return s; };
      return tr('Something went wrong. A report was saved (code {code}).').replace('{code}', code);
    }

    function show(code) {
      if (lastShown !== null && clock() - lastShown < MIN_GAP_MS) return false;
      var app = win.SubsystemApp;
      if (!app || typeof app.showToast !== 'function') return false;
      lastShown = clock();
      app.showToast(message(code), 'info');
      return true;
    }

    function observe(response) {
      try {
        if (!response || !response.headers || typeof response.headers.get !== 'function') return false;
        var code = response.headers.get(HEADER);
        if (typeof code !== 'string' || !CODE_RE.test(code)) return false;
        return show(code);
      } catch (e) { return false; }
    }

    function install() {
      if (typeof win.fetch !== 'function' || win.fetch.__auraErrorToast) return false;
      var original = win.fetch;
      var wrapped = function () {
        var promise = original.apply(this, arguments);
        try { promise.then(observe, function () { /* the caller handles its own failure */ }); } catch (e) { /* see above */ }
        return promise;
      };
      wrapped.__auraErrorToast = true;
      win.fetch = wrapped;
      return true;
    }

    return { observe: observe, install: install };
  }

  var api = { create: create, HEADER: HEADER };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root && typeof root.document !== 'undefined') {
    root.AuraErrorToast = api;
    try { create(root).install(); } catch (e) { /* a courtesy must never break the page */ }
  }
})(typeof window !== 'undefined' ? window : this);
