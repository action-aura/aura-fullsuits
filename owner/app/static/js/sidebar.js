/* Aura Owner -- sidebar collapse/expand + mobile off-canvas toggle.
 *
 * CSP-compatible (external file, no inline script). The pre-paint
 * restore of the collapsed/expanded state (so there is no layout jump
 * on load) happens in a tiny inline <script> in layout/base.html's
 * <head>, which sets data-sidebar="collapsed" on <html> before first
 * paint; this file only wires up the interactive toggle controls.
 *
 * localStorage key "aura-owner-sidebar" holds "collapsed" | "expanded".
 * This is a presentation-only client preference, not a security control.
 */
(function () {
  var STORAGE_KEY = "aura-owner-sidebar";

  document.addEventListener("DOMContentLoaded", function () {
    var sidebar = document.getElementById("aura-sidebar");
    var desktopToggle = document.getElementById("aura-sidebar-toggle");
    var mobileToggle = document.getElementById("aura-sidebar-toggle-mobile");
    if (!sidebar) return;

    function isCollapsed() {
      return document.documentElement.getAttribute("data-sidebar") === "collapsed";
    }

    function setCollapsed(collapsed) {
      if (collapsed) {
        document.documentElement.setAttribute("data-sidebar", "collapsed");
      } else {
        document.documentElement.removeAttribute("data-sidebar");
      }
      try {
        window.localStorage.setItem(STORAGE_KEY, collapsed ? "collapsed" : "expanded");
      } catch (e) { /* localStorage unavailable -- state still applies for this load */ }
      if (desktopToggle) {
        desktopToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      }
    }

    if (desktopToggle) {
      desktopToggle.setAttribute("aria-expanded", isCollapsed() ? "false" : "true");
      desktopToggle.addEventListener("click", function () {
        setCollapsed(!isCollapsed());
      });
    }

    // Mobile: off-canvas open/close, independent of the collapsed/expanded
    // preference above (small viewports always start closed).
    if (mobileToggle) {
      mobileToggle.addEventListener("click", function () {
        var open = sidebar.classList.toggle("aura-sidebar--open");
        mobileToggle.setAttribute("aria-expanded", open ? "true" : "false");
      });

      sidebar.querySelectorAll("a").forEach(function (link) {
        link.addEventListener("click", function () {
          sidebar.classList.remove("aura-sidebar--open");
          mobileToggle.setAttribute("aria-expanded", "false");
        });
      });

      document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && sidebar.classList.contains("aura-sidebar--open")) {
          sidebar.classList.remove("aura-sidebar--open");
          mobileToggle.setAttribute("aria-expanded", "false");
        }
      });
    }
  });
})();
