/* Aura Owner -- copy-to-clipboard for one-time-reveal secrets.
 *
 * A licence key (licensing/detail.html, licensing/issuance.html) and its
 * pre-composed WhatsApp message are shown exactly once and never again --
 * there is no re-reveal, only replace_license. Before this file, the only
 * way to copy one was hand-selecting text out of a <code>/<textarea>, which
 * at ~11 issuances/day made a stray keypress or accidental navigation a
 * routine way to lose a key for good. See docs/owner -- this is a targeted
 * fix, not new UI ceremony.
 *
 * CSP-compatible: external file, script-src 'self', no inline handlers.
 * Same external-file + data-*-attribute discipline as confirm.js,
 * auto-submit.js and password-toggle.js -- this is the same, third, kind of
 * helper, not a new mechanism. All button labels come from data-copy-*
 * attributes rendered server-side via Jinja's _() (Non-Negotiable
 * Principle 1, see confirm.js) -- this file carries no text of its own.
 *
 * Usage: put `data-copy-target="<id>"` on a <button type="button">. The
 * target element (found by id) supplies the text to copy -- its `.value`
 * for an <input>/<textarea>, its `.textContent` otherwise (e.g. <code>).
 * Optional data-copy-label / data-copy-success-label / data-copy-fallback-
 * label swap the button's own text temporarily to confirm what happened;
 * omitted attributes fall back to the button's current text.
 *
 * Clipboard API requires a secure context (HTTPS, or localhost in dev) and
 * can also be refused by the user or the browser. Neither case is allowed
 * to leave the button silently doing nothing -- an operator who believes a
 * key was copied when it was not, and then navigates away, has lost it
 * exactly as if this file did not exist. On any unavailability or failure,
 * the fallback selects the target's text in place so the operator can
 * still press Ctrl+C themselves, and the button label says so.
 */
(function () {
  function selectTargetText(target) {
    if (typeof target.select === "function") {
      target.focus();
      target.select();
      return;
    }
    var selection = window.getSelection ? window.getSelection() : null;
    if (!selection || typeof document.createRange !== "function") {
      return;
    }
    var range = document.createRange();
    range.selectNodeContents(target);
    selection.removeAllRanges();
    selection.addRange(range);
  }

  function getTargetText(target) {
    return "value" in target ? target.value : target.textContent;
  }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest ? event.target.closest("[data-copy-target]") : null;
    if (!btn) {
      return;
    }
    var target = document.getElementById(btn.dataset.copyTarget);
    if (!target) {
      return;
    }

    var originalLabel = btn.dataset.copyLabel || btn.textContent;
    var successLabel = btn.dataset.copySuccessLabel || originalLabel;
    var fallbackLabel = btn.dataset.copyFallbackLabel || successLabel;

    function showLabelThenRevert(label) {
      btn.textContent = label;
      window.setTimeout(function () {
        btn.textContent = originalLabel;
      }, 2500);
    }

    function fallbackToSelection() {
      selectTargetText(target);
      showLabelThenRevert(fallbackLabel);
    }

    var canUseClipboardApi =
      window.isSecureContext &&
      window.navigator &&
      window.navigator.clipboard &&
      typeof window.navigator.clipboard.writeText === "function";

    if (canUseClipboardApi) {
      window.navigator.clipboard.writeText(getTargetText(target)).then(
        function () {
          showLabelThenRevert(successLabel);
        },
        function () {
          // Denied by the user or blocked by the browser -- never fail silently.
          fallbackToSelection();
        }
      );
    } else {
      fallbackToSelection();
    }
  });
})();
