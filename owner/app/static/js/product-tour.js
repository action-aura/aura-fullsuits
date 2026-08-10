/* Aura Owner -- reusable first-login welcome tour engine.
 *
 * Vanilla JS, external file (CSP: script-src 'self'). Reads its content
 * from a <template> already rendered server-side with real, permission-
 * checked data (layout/_tour.html) -- this file only handles step
 * navigation, focus management, and the safe local "seen" marker. No
 * business data, no permission logic, lives here.
 *
 * Completion tracking: localStorage[data-tour-storage-key] (per staff
 * id, set by _tour.html). Presentation-only preference, not a security
 * or business record -- if cleared, the tour simply reappears once.
 *
 * Respects prefers-reduced-motion: step transitions are instant
 * (display toggle only, no animation) either way in this implementation,
 * so there's nothing extra to disable -- documented here so a future
 * animated version doesn't forget the check.
 */
(function () {
  document.addEventListener("DOMContentLoaded", function () {
    var root = document.getElementById("aura-tour-root");
    if (!root) return;
    var template = document.getElementById("aura-tour-template");
    if (!template) return;

    var storageKey = root.dataset.tourStorageKey;

    function alreadySeen() {
      try {
        return window.localStorage.getItem(storageKey) === "1";
      } catch (e) {
        return false;
      }
    }

    function markSeen() {
      try {
        window.localStorage.setItem(storageKey, "1");
      } catch (e) { /* localStorage unavailable -- tour will simply reappear next load */ }
    }

    var overlay = null;
    var steps = [];
    var current = 0;
    var lastFocused = null;

    function fill(el) {
      el.querySelectorAll("[data-tour-fill]").forEach(function (node) {
        var key = node.dataset.tourFill;
        if (key === "name") node.textContent = root.dataset.tourName;
        if (key === "role") node.textContent = root.dataset.tourRole;
      });
    }

    function renderDots(container, count) {
      container.textContent = "";
      for (var i = 0; i < count; i++) {
        var dot = document.createElement("span");
        dot.className = "aura-tour-dot";
        container.appendChild(dot);
      }
    }

    function updateStep() {
      steps.forEach(function (step, i) {
        step.hidden = i !== current;
      });
      var dots = overlay.querySelectorAll(".aura-tour-dot");
      dots.forEach(function (dot, i) {
        dot.classList.toggle("is-active", i === current);
      });
      var backBtn = overlay.querySelector('[data-tour-action="back"]');
      var nextBtn = overlay.querySelector('[data-tour-action="next"]');
      var finishBtn = overlay.querySelector('[data-tour-action="finish"]');
      backBtn.hidden = current === 0;
      var isLast = current === steps.length - 1;
      nextBtn.hidden = isLast;
      finishBtn.hidden = !isLast;
      var focusTarget = overlay.querySelector(".aura-tour-dialog");
      if (focusTarget) focusTarget.focus();
    }

    function close() {
      markSeen();
      if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
      overlay = null;
      if (lastFocused && lastFocused.focus) lastFocused.focus();
    }

    function open() {
      lastFocused = document.activeElement;
      var fragment = template.content.cloneNode(true);
      document.body.appendChild(fragment);
      overlay = document.body.querySelector(".aura-tour-overlay:last-of-type");
      fill(overlay);
      steps = Array.prototype.slice.call(overlay.querySelectorAll(".aura-tour-step"));
      current = 0;
      renderDots(overlay.querySelector("[data-tour-dots]"), steps.length);
      updateStep();

      overlay.addEventListener("click", function (event) {
        if (event.target === overlay) close();
      });
      overlay.querySelectorAll("[data-tour-action]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var action = btn.dataset.tourAction;
          if (action === "next" && current < steps.length - 1) current += 1;
          else if (action === "back" && current > 0) current -= 1;
          else if (action === "close" || action === "finish") { close(); return; }
          updateStep();
        });
      });
      document.addEventListener("keydown", function onKey(event) {
        if (!overlay) { document.removeEventListener("keydown", onKey); return; }
        if (event.key === "Escape") close();
      });
    }

    var forceOpen = root.dataset.tourForce === "true";
    if (forceOpen) {
      // Reopened via the profile menu ("Show welcome tour" -> ?tour=reopen)
      // -- strip the query param from the URL bar so a refresh doesn't
      // reopen it again, without a full navigation/reload.
      open();
      if (window.history && window.history.replaceState) {
        window.history.replaceState(null, "", window.location.pathname);
      }
    } else if (!alreadySeen()) {
      open();
    }
  });
})();
