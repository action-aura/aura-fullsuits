/* Aura Owner -- "Rank with AI" on the Leads Finder results (leads/discover.html).
 *
 * Vanilla JS in an external file (CSP: script-src 'self', no inline script).
 * The template renders the whole UI and every word of it, already translated
 * (data-msg-* on #leads-rank, the column headers, the Copy button) -- this
 * file only moves values around and adds no text of its own.
 *
 * What is sent: for each result row, the data-* values the template put on the
 * <tr> -- name, address, categories, rating, review count, whether a phone and
 * a website are listed, business status. NEVER the phone number itself: it is
 * not in any data-* attribute, and this file never reads the hidden phone
 * input. The server rebuilds what the AI sees from an allowlist anyway
 * (app/ai/leads_rank.py), so this is defence in depth, not the only guard.
 *
 * The answer fills Score / Why / Opener, sorts the rows by score, and copies
 * the score + reason + opener into hidden inputs (ai_score-N, ai_reason-N,
 * ai_opener-N) so the import form saves them on the new lead as a note. Row
 * inputs are named by their original index, so re-ordering rows is safe.
 * All values go in with textContent / .value -- never innerHTML.
 */
(function () {
  "use strict";

  function onReady(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  function numberOrNull(raw) {
    if (raw === null || raw === "") return null;
    var n = Number(raw);
    return isFinite(n) ? n : null;
  }

  function rowPayload(row) {
    var types = (row.getAttribute("data-types") || "").split("|").filter(function (t) { return t; });
    return {
      idx: parseInt(row.getAttribute("data-idx"), 10),
      name: row.getAttribute("data-name") || "",
      address: row.getAttribute("data-address") || "",
      types: types,
      rating: numberOrNull(row.getAttribute("data-rating")),
      reviews: numberOrNull(row.getAttribute("data-reviews")),
      has_phone: row.getAttribute("data-has-phone") === "1",
      has_website: row.getAttribute("data-has-website") === "1",
      status: row.getAttribute("data-status") || ""
    };
  }

  function setField(row, name, value) {
    var input = row.querySelector("[data-rank-field='" + name + "']");
    if (input) input.value = value;
  }

  function copyText(text, done) {
    function fallback() {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try { document.execCommand("copy"); } catch (e) { /* the text is selected; the person can press Ctrl+C */ }
      document.body.removeChild(area);
      done();
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, fallback);
    } else {
      fallback();
    }
  }

  onReady(function () {
    var box = document.getElementById("leads-rank");
    if (!box) return;
    var rankBtn = box.querySelector("[data-rank-action='rank']");
    var statusEl = box.querySelector("[data-rank-status]");
    var rows = Array.prototype.slice.call(document.querySelectorAll("tr[data-rank-row]"));
    if (!rankBtn || !rows.length) return;

    function say(text) { if (statusEl) statusEl.textContent = text || ""; }

    function applyResults(results) {
      var byIdx = {};
      results.forEach(function (r) { byIdx[r.idx] = r; });
      var scored = [];
      rows.forEach(function (row) {
        var r = byIdx[parseInt(row.getAttribute("data-idx"), 10)];
        if (!r) return;
        row.querySelector("[data-rank-score]").textContent = String(r.score);
        row.querySelector("[data-rank-reason]").textContent = r.reason;
        row.querySelector("[data-rank-opener]").textContent = r.opener;
        var copyBtn = row.querySelector("[data-rank-action='copy']");
        if (copyBtn) copyBtn.hidden = !r.opener;
        setField(row, "score", String(r.score));
        setField(row, "reason", r.reason);
        setField(row, "opener", r.opener);
        scored.push({ row: row, score: r.score });
      });
      // Highest score first; unscored rows keep their order below the scored ones.
      scored.sort(function (a, b) { return b.score - a.score; });
      var tbody = rows[0].parentNode;
      // Each insert goes to the top, so walk from the lowest score up: the
      // highest-scored row is inserted last and ends first.
      for (var i = scored.length - 1; i >= 0; i--) tbody.insertBefore(scored[i].row, tbody.firstChild);
    }

    rankBtn.addEventListener("click", function () {
      rankBtn.disabled = true;
      say(box.getAttribute("data-msg-busy"));
      fetch(box.getAttribute("data-rank-url"), {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "X-CSRFToken": box.getAttribute("data-csrf") || ""
        },
        body: JSON.stringify({
          segment: box.getAttribute("data-segment") || "",
          area: box.getAttribute("data-area") || "",
          lang: box.getAttribute("data-lang") === "ar" ? "ar" : "en",
          places: rows.map(rowPayload)
        })
      }).then(function (resp) {
        return resp.json().then(function (data) { return { ok: resp.ok, data: data }; }, function () { return { ok: false, data: {} }; });
      }).then(function (out) {
        if (!out.ok || !out.data || !out.data.results) {
          // The server's message is already localized and key-free.
          say((out.data && out.data.message) || box.getAttribute("data-msg-error"));
          return;
        }
        applyResults(out.data.results);
        say("");
      }).catch(function () {
        say(box.getAttribute("data-msg-error"));
      }).then(function () {
        rankBtn.disabled = false;
      });
    });

    document.addEventListener("click", function (event) {
      var btn = event.target.closest ? event.target.closest("[data-rank-action='copy']") : null;
      if (!btn) return;
      var row = btn.closest("tr");
      var opener = row ? row.querySelector("[data-rank-opener]") : null;
      if (!opener) return;
      copyText(opener.textContent, function () { say(box.getAttribute("data-msg-copied")); });
    });
  });
})();
