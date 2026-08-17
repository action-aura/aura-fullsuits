/* Aura Owner -- keyboard shortcuts (Dashboard Interactive Kit).
 *
 * Vanilla JS, external file (CSP: script-src 'self', same convention as
 * command-palette.js/sidebar.js -- no inline script).
 *
 * Two shortcut classes:
 *  1. `g` then a second key -- navigate to a destination, read from the
 *     server-rendered [data-shortcut-goto] list inside the cheatsheet
 *     partial (never a hardcoded route: a destination this employee lacks
 *     permission for simply has no matching element, so the chord is a
 *     silent no-op for it).
 *  2. `?` -- toggle the cheatsheet panel.
 *
 * Guard: ignored while focus is inside a text-entry control, or while the
 * command palette overlay (.aura-cp-overlay, added by command-palette.js's
 * own open()) is present in the DOM -- a hotkey must never fire while the
 * user is typing a search query into the palette.
 */
(function () {
  var CHORD_TIMEOUT_MS = 900;

  document.addEventListener("DOMContentLoaded", function () {
    var cheatsheet = document.getElementById("aura-shortcuts-cheatsheet");
    if (!cheatsheet) return;

    var gotoTargets = {};
    cheatsheet.querySelectorAll("[data-shortcut-goto]").forEach(function (el) {
      var key = el.getAttribute("data-shortcut-goto");
      var link = el.querySelector("a");
      if (key && link) gotoTargets[key] = link.href;
    });

    var pendingG = false;
    var pendingTimer = null;

    function isTextEntryFocused() {
      var el = document.activeElement;
      if (!el) return false;
      var tag = el.tagName ? el.tagName.toLowerCase() : "";
      return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
    }

    function isCommandPaletteOpen() {
      return !!document.querySelector(".aura-cp-overlay");
    }

    function openCheatsheet() {
      cheatsheet.hidden = false;
      var closeBtn = cheatsheet.querySelector("[data-shortcuts-action='close']");
      if (closeBtn) closeBtn.focus();
    }

    function closeCheatsheet() {
      cheatsheet.hidden = true;
    }

    cheatsheet.addEventListener("click", function (event) {
      if (event.target === cheatsheet || event.target.closest("[data-shortcuts-action='close']")) {
        closeCheatsheet();
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (isTextEntryFocused() || isCommandPaletteOpen()) return;

      if (!cheatsheet.hidden && event.key === "Escape") {
        event.preventDefault();
        closeCheatsheet();
        return;
      }

      if (event.key === "?") {
        event.preventDefault();
        openCheatsheet();
        return;
      }

      if (pendingG) {
        pendingG = false;
        if (pendingTimer) { window.clearTimeout(pendingTimer); pendingTimer = null; }
        var target = gotoTargets[event.key.toLowerCase()];
        if (target) {
          event.preventDefault();
          window.location.href = target;
        }
        return;
      }

      if (event.key.toLowerCase() === "g") {
        pendingG = true;
        pendingTimer = window.setTimeout(function () { pendingG = false; }, CHORD_TIMEOUT_MS);
      }
    });
  });
})();
