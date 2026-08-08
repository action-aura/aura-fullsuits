/* Aura Owner -- generic ARIA tabs progressive enhancement (Customer 360,
 * Stage D UI modernization -- see
 * docs/owner/ui-modernization/customer-360-contract.md).
 *
 * CSP-compatible (external file, no inline script), same vanilla-JS/
 * DOMContentLoaded/localStorage-free pattern as table-density.js and
 * product-tour.js.
 *
 * No-JS behavior (deliberate, disclosed): the server always renders every
 * tab as a real <a href="#panel-id"> jump link inside a real
 * role="tablist", and every panel is visible (stacked, no [hidden]) by
 * default. With JS disabled, this is a fully working degraded experience
 * -- clicking a "tab" link jump-scrolls to its section like any in-page
 * anchor, and every section is already on the page to scroll to. This
 * script's only job is to layer real single-panel-visible tab behavior
 * on top: it hides every panel except the active one, intercepts clicks
 * to swap the active panel instead of only scrolling, and adds real
 * keyboard arrow-key/Home/End roving-tabindex navigation between tabs
 * (WAI-ARIA APG tabs pattern, automatic activation).
 */
(function () {
  function activate(container, tabs, panels, targetId, opts) {
    tabs.forEach(function (tab) {
      var selected = tab.getAttribute("data-tabs-panel") === targetId;
      tab.setAttribute("aria-selected", selected ? "true" : "false");
      tab.setAttribute("tabindex", selected ? "0" : "-1");
      if (selected && opts && opts.focus) tab.focus();
    });
    panels.forEach(function (panel) {
      if (panel.id === targetId) {
        panel.removeAttribute("hidden");
      } else {
        panel.setAttribute("hidden", "");
      }
    });
  }

  function setUpTabs(container) {
    var list = container.querySelector("[data-tabs-list]");
    if (!list) return;
    var tabs = Array.prototype.slice.call(container.querySelectorAll("[data-tabs-tab]"));
    var panels = Array.prototype.slice.call(container.querySelectorAll("[data-tabs-panel-el]"));
    if (!tabs.length || !panels.length) return;

    // Deep-link support: if the URL's hash names a real panel, open it;
    // otherwise default to the first (real, permission-visible) tab --
    // matches the plain-anchor no-JS behavior exactly (a hash link lands
    // on that section) rather than always resetting to Overview.
    var initial = tabs[0].getAttribute("data-tabs-panel");
    var hash = window.location.hash ? window.location.hash.slice(1) : "";
    if (hash && tabs.some(function (t) { return t.getAttribute("data-tabs-panel") === hash; })) {
      initial = hash;
    }
    activate(container, tabs, panels, initial, {});

    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function (event) {
        event.preventDefault();
        var targetId = tab.getAttribute("data-tabs-panel");
        activate(container, tabs, panels, targetId, {});
        if (window.history && window.history.replaceState) {
          window.history.replaceState(null, "", "#" + targetId);
        }
      });
      tab.addEventListener("keydown", function (event) {
        var newIndex = null;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") newIndex = (index + 1) % tabs.length;
        else if (event.key === "ArrowLeft" || event.key === "ArrowUp") newIndex = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === "Home") newIndex = 0;
        else if (event.key === "End") newIndex = tabs.length - 1;
        if (newIndex === null) return;
        event.preventDefault();
        var targetTab = tabs[newIndex];
        var targetId = targetTab.getAttribute("data-tabs-panel");
        activate(container, tabs, panels, targetId, { focus: true });
        if (window.history && window.history.replaceState) {
          window.history.replaceState(null, "", "#" + targetId);
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-tabs]").forEach(setUpTabs);
  });
})();
