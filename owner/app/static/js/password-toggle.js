/* Aura Owner -- password visibility toggle.
 *
 * Any <div class="aura-password-field"> containing exactly one
 * <input type="password"> gets a show/hide button injected next to it.
 * Vanilla JS, external file (CSP: script-src 'self', no inline).
 * Presentation only -- never changes what gets submitted.
 */
(function () {
  document.addEventListener("DOMContentLoaded", function () {
    var fields = document.querySelectorAll(".aura-password-field");
    for (var i = 0; i < fields.length; i++) {
      (function (field) {
        var input = field.querySelector('input[type="password"]');
        if (!input) return;
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "aura-password-toggle";
        btn.setAttribute("aria-label", input.dataset.showLabel || "Show password");
        btn.textContent = input.dataset.showText || "Show";
        btn.addEventListener("click", function () {
          var showing = input.type === "text";
          input.type = showing ? "password" : "text";
          btn.textContent = showing ? (input.dataset.showText || "Show") : (input.dataset.hideText || "Hide");
          btn.setAttribute("aria-label", showing ? (input.dataset.showLabel || "Show password") : (input.dataset.hideLabel || "Hide password"));
        });
        field.appendChild(btn);
      })(fields[i]);
    }
  });
})();
