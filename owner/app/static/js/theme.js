/* Aura Owner -- theme toggle (light / dark / system).
 *
 * CSP-compatible (external file, no inline script -- same pattern as
 * confirm.js / geolocation-capture.js). The actual pre-paint restore
 * (reading localStorage and setting data-theme before first paint, so
 * there is no flash of the wrong theme) happens in a tiny inline
 * <script> in layout/base.html's <head>; this file only wires up the
 * three-way toggle control once the page is interactive.
 *
 * localStorage key "aura-owner-theme" holds "light" | "dark" | "system".
 * This is a presentation-only client preference, not a security control.
 */
(function () {
  var STORAGE_KEY = "aura-owner-theme";

  function currentPreference() {
    try {
      var saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved === "light" || saved === "dark") return saved;
    } catch (e) { /* localStorage unavailable */ }
    return "system";
  }

  function applyPreference(pref) {
    if (pref === "light" || pref === "dark") {
      document.documentElement.setAttribute("data-theme", pref);
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
    try {
      window.localStorage.setItem(STORAGE_KEY, pref);
    } catch (e) { /* localStorage unavailable -- theme still applies for this load */ }
  }

  function syncButtons(pref) {
    var buttons = document.querySelectorAll("[data-theme-option]");
    for (var i = 0; i < buttons.length; i++) {
      var isActive = buttons[i].getAttribute("data-theme-option") === pref;
      buttons[i].setAttribute("aria-pressed", isActive ? "true" : "false");
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var pref = currentPreference();
    syncButtons(pref);

    var buttons = document.querySelectorAll("[data-theme-option]");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", function () {
        var value = this.getAttribute("data-theme-option");
        applyPreference(value);
        syncButtons(value);
      });
    }
  });
})();
