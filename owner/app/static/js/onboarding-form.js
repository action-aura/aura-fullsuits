/* New customer flow -- the seller's "Create a new customer" form.
 *
 * CSP-compatible (external file, script-src 'self', no inline handlers) and, like
 * geolocation-capture.js, it carries no text of its own: every message is a data-*
 * attribute rendered server-side through Jinja's _().
 *
 *  - "Use my current location": ONE getCurrentPosition() call per button press
 *    (never watchPosition, never on page load -- no tracking). It only fills the
 *    hidden latitude / longitude / accuracy fields; the form is not submitted.
 *  - "Make one": a random temporary password from the browser's crypto source,
 *    without look-alike characters (0/O, 1/l/I), so a seller can read it out.
 */
document.addEventListener("DOMContentLoaded", function () {
  var locate = document.getElementById("nc-locate");
  var status = document.getElementById("nc-locate-status");
  if (locate) {
    locate.addEventListener("click", function () {
      if (!navigator.geolocation) { status.textContent = locate.dataset.unsupported || ""; return; }
      status.textContent = locate.dataset.busy || "";
      navigator.geolocation.getCurrentPosition(
        function (pos) {
          document.getElementById("nc-lat").value = pos.coords.latitude;
          document.getElementById("nc-lng").value = pos.coords.longitude;
          document.getElementById("nc-acc").value = pos.coords.accuracy;
          status.textContent = locate.dataset.ok || "";
        },
        function () { status.textContent = locate.dataset.denied || ""; }
      );
    });
  }

  var gen = document.getElementById("nc-genpw");
  var pw = document.getElementById("nc-pw");
  if (gen && pw) {
    var ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    gen.addEventListener("click", function () {
      var bytes = new Uint8Array(12);
      window.crypto.getRandomValues(bytes);
      var out = "";
      for (var i = 0; i < bytes.length; i++) { out += ALPHABET.charAt(bytes[i] % ALPHABET.length); }
      pw.value = out;
      pw.focus();
    });
  }
});
