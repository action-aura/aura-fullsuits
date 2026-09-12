/* CSP-compatible replacement for `onchange="this.form.submit()"`.
 *
 * Owner's CSP is script-src 'self' with no 'unsafe-inline'
 * (app/security/headers.py), so every inline onchange= in the app was
 * silently refused by the browser -- the control rendered, looked live, and
 * did nothing. Same external-file, data-*-attribute discipline as
 * confirm.js; this is the second and (deliberately) last such helper.
 *
 * Usage: put `data-auto-submit` on a form control that is the ONLY
 * meaningful field in its form. Do not use it on a multi-field filter form
 * -- changing one field there would submit before the user has set the
 * others, and it silently discards a search box's uncommitted keystrokes.
 * Those forms get a visible Filter/Search button instead (see
 * components/table.html's filter_bar macro).
 *
 * requestSubmit(), not submit(): form.submit() bypasses the submit event
 * entirely, which would skip both native constraint validation and
 * confirm.js's data-confirm guard. requestSubmit() dispatches a real,
 * cancellable submit event, so an auto-submitting control composes with a
 * data-confirm on the same form instead of quietly defeating it. The
 * fallback matters only for browsers without requestSubmit (pre-2021); the
 * behaviour there is the old, unguarded one rather than nothing at all.
 */
document.addEventListener("change", function (event) {
  var control = event.target;
  if (!control || !control.dataset || control.dataset.autoSubmit === undefined) {
    return;
  }
  var form = control.form;
  if (!form) {
    return;
  }
  if (typeof form.requestSubmit === "function") {
    form.requestSubmit();
  } else {
    form.submit();
  }
});
