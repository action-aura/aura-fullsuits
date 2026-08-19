/* Aura Owner -- sidebar collapse/expand + mobile off-canvas toggle,
 * plus the per-department disclosure groups.
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

  /* Per-department disclosure state, one key per group slug (e.g.
   * "aura-owner-nav-group:sales" -> "open" | "closed"). Keyed by the
   * stable slug rather than a position, because which groups render at
   * all depends on the employee's permissions -- an index would mean the
   * same department resolved to a different key for different roles. */
  var GROUP_STORAGE_PREFIX = "aura-owner-nav-group:";

  /* Every localStorage touch is wrapped: access throws outright (not
   * returns null) in restricted contexts -- Safari private mode, blocked
   * third-party storage, enterprise policy. A nav convenience must never
   * be the thing that takes the page down, so both helpers degrade to
   * "no saved preference" and the server-rendered state simply stands. */
  function readGroupState(slug) {
    try {
      return window.localStorage.getItem(GROUP_STORAGE_PREFIX + slug);
    } catch (e) {
      return null;
    }
  }

  function writeGroupState(slug, expanded) {
    try {
      window.localStorage.setItem(GROUP_STORAGE_PREFIX + slug, expanded ? "open" : "closed");
    } catch (e) { /* localStorage unavailable -- state still applies for this load */ }
  }

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

    // Department disclosures. The button is a real <button>, so Enter and
    // Space already activate it and it is already in the tab order --
    // nothing here re-implements keyboard handling. All this does is keep
    // aria-expanded and the list's `hidden` attribute in lockstep, which
    // is what makes the collapsed content genuinely unreachable rather
    // than merely invisible.
    function setGroupExpanded(button, list, expanded) {
      button.setAttribute("aria-expanded", expanded ? "true" : "false");
      if (expanded) {
        list.removeAttribute("hidden");
      } else {
        list.setAttribute("hidden", "hidden");
      }
    }

    sidebar.querySelectorAll("[data-nav-group-toggle]").forEach(function (button) {
      var slug = button.getAttribute("data-nav-group-toggle");
      var list = document.getElementById(button.getAttribute("aria-controls"));
      if (!list) return;

      /* The server already expanded the group holding the current page
       * (layout/_sidebar.html), which is why there is no flash of an
       * all-collapsed nav on load. That decision outranks anything
       * saved: an employee who once closed "Sales" and then navigates
       * to a Sales screen must still see where they are. Note we do NOT
       * overwrite the saved value here -- the force-open is for this
       * page only, and their real preference survives to the next one. */
      var isActiveGroup = button.getAttribute("aria-expanded") === "true";
      if (!isActiveGroup && readGroupState(slug) === "open") {
        setGroupExpanded(button, list, true);
      }

      button.addEventListener("click", function () {
        var next = button.getAttribute("aria-expanded") !== "true";
        setGroupExpanded(button, list, next);
        writeGroupState(slug, next);
      });
    });

    // Mobile: off-canvas open/close, independent of the collapsed/expanded
    // preference above (small viewports always start closed).
    if (mobileToggle) {
      mobileToggle.addEventListener("click", function () {
        var open = sidebar.classList.toggle("aura-sidebar--open");
        mobileToggle.setAttribute("aria-expanded", open ? "true" : "false");
      });

      // Only real destinations dismiss the off-canvas panel. The group
      // disclosure buttons deliberately do not match this selector --
      // opening a department is not navigating away from the page.
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
