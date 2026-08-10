/* Phase 9.5C -- explicit, one-shot browser location capture.
 *
 * CSP-compatible (external file, no inline script -- app/static/js/confirm.js
 * already established this is the only pattern that works here; inline
 * <script> blocks are blocked by the site's script-src 'self' policy, a
 * real defect found via Milestone 23 real-browser validation).
 *
 * Reads already-localized messages from data attributes (server-rendered
 * via Jinja's _(), safely HTML-attribute-escaped), same discipline as
 * confirm.js: this file carries zero translation catalog of its own.
 *
 * A single getCurrentPosition() call per button click -- never
 * watchPosition, never a timer, never triggered on page load
 * (Non-Negotiable Domain Rule 8: no continuous/background tracking).
 */
document.addEventListener("DOMContentLoaded", function () {
  var btn = document.getElementById("capture-location-btn");
  if (!btn) return;

  btn.addEventListener("click", function () {
    if (!navigator.geolocation) {
      alert(btn.dataset.errorUnsupported || "Geolocation is not supported by this browser.");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        document.getElementById("loc-lat").value = pos.coords.latitude;
        document.getElementById("loc-lng").value = pos.coords.longitude;
        document.getElementById("loc-acc").value = pos.coords.accuracy;
        document.getElementById("location-form").submit();
      },
      function () {
        alert(btn.dataset.errorDenied || "Location permission was denied or unavailable.");
      }
    );
  });
});
