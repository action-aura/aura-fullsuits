/* Aura Owner -- enterprise table density toggle (compact/comfortable).
 *
 * CSP-compatible (external file, no inline script), same pattern as
 * sidebar.js's collapsed/expanded preference. localStorage key
 * "aura-owner-table-density" holds "compact" | "comfortable". This is a
 * presentation-only client preference (row padding via the
 * --aura-table-cell-pad-* tokens), not a security control and not
 * persisted server-side -- same status as the sidebar's own preference
 * (application-shell-contract.md).
 *
 * Not pre-paint-blocking (unlike theme.js/sidebar.js's collapsed state):
 * a table row-height/padding change on load is a much smaller visual
 * jump than the sidebar's width change, so this runs on DOMContentLoaded
 * like every other non-layout-critical script in the app.
 */
(function () {
  var STORAGE_KEY = "aura-owner-table-density";

  function currentDensity() {
    try {
      return window.localStorage.getItem(STORAGE_KEY) || "comfortable";
    } catch (e) {
      return "comfortable";
    }
  }

  function applyDensity(density) {
    if (density === "compact") {
      document.documentElement.setAttribute("data-table-density", "compact");
    } else {
      document.documentElement.removeAttribute("data-table-density");
    }
    document.querySelectorAll("[data-density-toggle]").forEach(function (btn) {
      var pressed = density === "compact";
      btn.setAttribute("aria-pressed", pressed ? "true" : "false");
      var label = btn.querySelector("[data-density-toggle-label]");
      if (label) {
        label.textContent = pressed ? btn.getAttribute("data-label-comfortable") || "Comfortable view" : btn.getAttribute("data-label-compact") || "Compact view";
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var toggles = document.querySelectorAll("[data-density-toggle]");
    if (!toggles.length) return;

    // Each toggle button carries its own two localized labels (server
    // rendered, translated, via data-label-compact/data-label-comfortable
    // in components/table.html's density_toggle() macro) so this script
    // never hardcodes/duplicates English text -- same discipline as
    // confirm.js's own reasoning for keeping translated strings
    // server-side only.
    applyDensity(currentDensity());

    toggles.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var next = currentDensity() === "compact" ? "comfortable" : "compact";
        try {
          window.localStorage.setItem(STORAGE_KEY, next);
        } catch (e) { /* localStorage unavailable -- state still applies for this load */ }
        applyDensity(next);
      });
    });
  });
})();
