/* Aura Owner -- dashboard department cards: cinematic open/close.
 *
 * Each card is a native <details class="aura-dept-card"><summary>...
 * </summary><div class="aura-dept-card__panel">...</div></details>
 * (layout/_dept_cards.html). That native element is the whole reason
 * this stays progressive: a browser with JavaScript disabled can still
 * click/Enter the <summary> and the browser's own disclosure behavior
 * opens the panel and makes its links reachable -- no listener in this
 * file is required for that baseline to work. Everything below is a
 * pure enhancement layered on top of it:
 *
 *   1. document.startViewTransition() around the toggle, when the
 *      browser supports it AND the employee has not asked for reduced
 *      motion -- produces the "cinematic" cross-fade the redesign spec
 *      asks for. When either condition fails, this file does not call
 *      preventDefault() at all, so the native toggle proceeds untouched
 *      and dashboard-redesign.css's own [open] .aura-dept-card__panel
 *      keyframe animation (transform/opacity only, and itself guarded by
 *      the same prefers-reduced-motion media query) is the fallback --
 *      CSS-only, so it plays even if this script fails to load.
 *   2. Keeping an explicit aria-expanded/aria-controls pair in sync via
 *      the native `toggle` event, which fires no matter which of the two
 *      paths above changed .open. <details>/<summary> already implies
 *      expanded state to most assistive tech, but the redesign spec asks
 *      for it stated explicitly too, and doing so costs nothing.
 *   3. Focus management: moving focus to the panel's first link when a
 *      card opens, and Escape closing the panel and returning focus to
 *      the card's <summary> (a plain click on <summary> already leaves
 *      focus there on close, so only the Escape path needs an explicit
 *      refocus).
 *
 * CSP-compatible: external file, no inline script/handlers.
 */
(function () {
  function prefersReducedMotion() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  function supportsViewTransition() {
    return typeof document.startViewTransition === "function";
  }

  document.addEventListener("DOMContentLoaded", function () {
    var cards = document.querySelectorAll(".aura-dept-card");

    cards.forEach(function (card) {
      var summary = card.querySelector(".aura-dept-card__summary");
      var panel = card.querySelector(".aura-dept-card__panel");
      if (!summary || !panel) return;

      // Only intercept the click when a smoother transition is actually
      // possible; otherwise let <details> do exactly what it already
      // does with no JavaScript at all.
      summary.addEventListener("click", function (event) {
        if (!supportsViewTransition() || prefersReducedMotion()) return;
        event.preventDefault();
        document.startViewTransition(function () {
          card.open = !card.open;
        });
      });

      // Fires for BOTH paths above (native toggle and the VT-wrapped
      // manual .open assignment), so this is the one place expanded
      // state and focus placement are kept correct.
      card.addEventListener("toggle", function () {
        var isOpen = card.open;
        summary.setAttribute("aria-expanded", isOpen ? "true" : "false");
        if (isOpen) {
          var firstLink = panel.querySelector("a, button");
          if (firstLink) firstLink.focus();
        }
      });

      panel.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && card.open) {
          card.open = false;
          summary.focus();
        }
      });
    });
  });
})();
