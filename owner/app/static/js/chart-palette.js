/* Aura Owner -- chart palette switcher (Brand / Vivid / Colorblind-safe).
 *
 * CSP-compatible (external file, no inline script -- same pattern as
 * theme.js, which this file mirrors closely). The actual pre-paint
 * restore (reading localStorage and setting data-chart-palette before
 * first paint, so charts never flash from one ramp to another) happens
 * in static/js/theme-preload.js; this file only wires up the switcher
 * control once the page is interactive, and only does anything on pages
 * that actually render it (dashboard/index.html) -- querySelectorAll
 * returns an empty list everywhere else, so this file is a safe no-op
 * on every other page despite being loaded globally (same tradeoff
 * theme.js/sidebar.js already make).
 *
 * localStorage key "aura-owner-chart-palette" holds
 * "brand" | "vivid" | "cb-safe". Presentation-only client preference,
 * not a security control.
 */
(function () {
  var STORAGE_KEY = "aura-owner-chart-palette";

  function currentPreference() {
    try {
      var saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved === "vivid" || saved === "cb-safe") return saved;
    } catch (e) { /* localStorage unavailable */ }
    return "brand";
  }

  function applyPreference(pref) {
    if (pref === "vivid" || pref === "cb-safe") {
      document.documentElement.setAttribute("data-chart-palette", pref);
    } else {
      document.documentElement.removeAttribute("data-chart-palette");
    }
    try {
      window.localStorage.setItem(STORAGE_KEY, pref);
    } catch (e) { /* localStorage unavailable -- palette still applies for this load */ }
  }

  function syncButtons(pref) {
    var buttons = document.querySelectorAll("[data-chart-palette-option]");
    for (var i = 0; i < buttons.length; i++) {
      var isActive = buttons[i].getAttribute("data-chart-palette-option") === pref;
      buttons[i].setAttribute("aria-pressed", isActive ? "true" : "false");
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var buttons = document.querySelectorAll("[data-chart-palette-option]");
    if (!buttons.length) return;

    syncButtons(currentPreference());

    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", function () {
        var value = this.getAttribute("data-chart-palette-option");
        applyPreference(value);
        syncButtons(value);
      });
    }
  });
})();
