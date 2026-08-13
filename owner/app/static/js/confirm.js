/* Phase 9.5B-R Milestone 14 -- the one, tiny, CSP-compatible ('self',
 * external file, no inline script) piece of client-side JS in Owner.
 *
 * Reads an already-localized message from a data-confirm attribute
 * (server-rendered via Jinja's _(), safely HTML-attribute-escaped by
 * Jinja's own autoescape -- never interpolated into a JS string literal,
 * which is what an inline onsubmit="return confirm('...')" would have
 * required and which a translated string containing an apostrophe could
 * have broken or, worse, been used to inject into). This file carries zero
 * translation catalog of its own -- the server is the only source of
 * localized text (Non-Negotiable Principle 1).
 */
document.addEventListener("submit", function (event) {
  var form = event.target;
  if (form instanceof HTMLFormElement && form.dataset.confirm) {
    if (!window.confirm(form.dataset.confirm)) {
      event.preventDefault();
    }
  }
});
