/* Aura Owner -- website demo-request badge (topbar).
 *
 * Vanilla JS, external file (CSP: script-src 'self', same convention as
 * command-palette.js). Polls data-count-url (attention.website_leads_count)
 * every data-poll-ms (30 s) and shows the badge ONLY when the count is above
 * zero; at zero it stays/returns [hidden]. No SSE, no websocket: one small
 * GET per open tab per 30 s. The endpoint does not refresh the session idle
 * timer, so an unattended tab still times out.
 *
 * Stops for good on 401/403 (session ended, or no lead permission). Pauses
 * while the tab is hidden and polls once immediately when it is shown again.
 * Network errors keep the last state and retry next tick. Nothing is logged.
 */
(function () {
  document.addEventListener("DOMContentLoaded", function () {
    var badge = document.getElementById("aura-website-leads-badge");
    if (!badge || !badge.dataset.countUrl || !window.fetch) return;
    var countEl = badge.querySelector(".aura-topbar__website-leads-count");
    var url = badge.dataset.countUrl;
    var pollMs = parseInt(badge.dataset.pollMs, 10) || 30000;
    var labelTemplate = badge.dataset.labelTemplate || "__COUNT__";
    var formatter = null;
    try { formatter = new Intl.NumberFormat(document.documentElement.lang || undefined); } catch (e) { formatter = null; }
    var timer = null;
    var stopped = false;

    function render(count) {
      if (!(count > 0)) {
        badge.hidden = true;
        return;
      }
      var text = formatter ? formatter.format(count) : String(count);
      if (countEl) countEl.textContent = text;
      badge.setAttribute("aria-label", labelTemplate.replace("__COUNT__", text));
      badge.hidden = false;
    }

    function poll() {
      if (stopped) return;
      fetch(url, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } })
        .then(function (res) {
          if (res.status === 401 || res.status === 403) {
            stopped = true;
            stop();
            render(0);
            return null;
          }
          return res.ok ? res.json() : null;
        })
        .then(function (data) {
          if (data && typeof data.count === "number") render(data.count);
        })
        .catch(function () { /* keep the last state; try again next tick */ });
    }

    function start() {
      if (timer || stopped) return;
      poll();
      timer = window.setInterval(poll, pollMs);
    }
    function stop() {
      if (timer) { window.clearInterval(timer); timer = null; }
    }

    document.addEventListener("visibilitychange", function () {
      if (document.hidden) stop(); else start();
    });
    if (!document.hidden) start();
  });
})();
