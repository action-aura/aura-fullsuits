/* Aura Owner -- live count badges (top bar, menu).
 *
 * Vanilla JS, external file (CSP: script-src 'self', same convention as
 * command-palette.js). Drives every element marked [data-count-badge]:
 *   - the website demo-request badge (attention.website_leads_count);
 *   - the managers' "sales waiting" badges, in the top bar and on the
 *     Sales to confirm menu link (license_issuance.sales_waiting_count,
 *     Step 1 -- before 2026-10-25 this file was website-leads-badge.js and
 *     drove only the first one).
 * Badges that share a data-count-url share ONE poll. Each polls every
 * data-poll-ms (30 s) and shows the badge ONLY when the count is above zero;
 * at zero it stays/returns [hidden]. A badge the server already rendered with
 * its count simply keeps it until the first poll answers. No SSE, no
 * websocket: one small GET per open tab per URL per 30 s. The endpoints do not
 * refresh the session idle timer, so an unattended tab still times out.
 *
 * Stops for good on 401/403 (session ended, or no permission). Pauses while
 * the tab is hidden and polls once immediately when it is shown again.
 * Network errors keep the last state and retry next tick. Nothing is logged.
 */
(function () {
  function watch(url, badges) {
    var pollMs = parseInt(badges[0].dataset.pollMs, 10) || 30000;
    var formatter = null;
    try { formatter = new Intl.NumberFormat(document.documentElement.lang || undefined); } catch (e) { formatter = null; }
    var timer = null;
    var stopped = false;

    function renderOne(badge, count) {
      if (!(count > 0)) {
        badge.hidden = true;
        return;
      }
      var text = formatter ? formatter.format(count) : String(count);
      var countEl = badge.querySelector("[data-count-badge-value]");
      if (countEl) countEl.textContent = text;
      if (badge.dataset.labelTemplate) badge.setAttribute("aria-label", badge.dataset.labelTemplate.replace("__COUNT__", text));
      badge.hidden = false;
    }

    function render(count) {
      for (var i = 0; i < badges.length; i++) renderOne(badges[i], count);
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
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!window.fetch) return;
    var byUrl = {};
    var order = [];
    var all = document.querySelectorAll("[data-count-badge][data-count-url]");
    for (var i = 0; i < all.length; i++) {
      var url = all[i].dataset.countUrl;
      if (!byUrl[url]) { byUrl[url] = []; order.push(url); }
      byUrl[url].push(all[i]);
    }
    for (var j = 0; j < order.length; j++) watch(order[j], byUrl[order[j]]);
  });
})();
