/* Aura Owner -- the staff Help chat panel (layout/base.html, #aura-ai-help).
 *
 * Vanilla JS, external file (CSP: script-src 'self'). The template renders the
 * button, the panel and every sentence in it, already translated; this file
 * opens/closes the panel, posts the question and appends the reply. CSRF rides
 * in the X-CSRFToken header, read from a data attribute, same pattern as
 * push.js. Answers are written with textContent -- never innerHTML -- so a
 * model reply can never inject markup.
 *
 * It sends: the question (<= 1000 chars), the last 6 turns of THIS panel's
 * conversation, and location.pathname (which screen the person is on). Nothing
 * else on the page is read. The panel tells people not to paste customer
 * personal data, because the staff helper may run on a free AI tier.
 */
(function () {
  "use strict";

  var MAX_HISTORY = 6;

  function onReady(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  onReady(function () {
    var root = document.getElementById("aura-ai-help");
    if (!root) return;
    var toggle = root.querySelector(".aura-ai-help__toggle");
    var panel = root.querySelector(".aura-ai-help__panel");
    var log = root.querySelector(".aura-ai-help__log");
    var form = root.querySelector(".aura-ai-help__form");
    var input = form.querySelector("textarea");
    var send = form.querySelector("button");
    var history = [];

    function add(kind, text) {
      var p = document.createElement("p");
      p.className = "aura-ai-help__msg aura-ai-help__msg--" + kind;
      p.textContent = text;
      log.appendChild(p);
      log.scrollTop = log.scrollHeight;
      return p;
    }

    toggle.addEventListener("click", function () {
      var open = panel.hidden;
      panel.hidden = !open;
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) input.focus();
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var question = input.value.trim();
      if (!question) return;
      add("user", question);
      input.value = "";
      send.disabled = true;
      var waiting = add("assistant", root.getAttribute("data-msg-busy"));
      fetch(root.getAttribute("data-ask-url"), {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "X-CSRFToken": root.getAttribute("data-csrf") || ""
        },
        body: JSON.stringify({
          question: question,
          history: history.slice(-MAX_HISTORY),
          page: window.location.pathname
        })
      }).then(function (resp) {
        return resp.json().then(function (data) { return { ok: resp.ok, data: data }; }, function () { return { ok: false, data: {} }; });
      }).then(function (out) {
        if (out.ok && out.data && typeof out.data.reply === "string") {
          waiting.textContent = out.data.reply;
          history.push({ role: "user", content: question });
          history.push({ role: "assistant", content: out.data.reply });
        } else {
          waiting.className = "aura-ai-help__msg aura-ai-help__msg--error";
          waiting.textContent = (out.data && out.data.message) || root.getAttribute("data-msg-error");
        }
      }).catch(function () {
        waiting.className = "aura-ai-help__msg aura-ai-help__msg--error";
        waiting.textContent = root.getAttribute("data-msg-error");
      }).then(function () {
        send.disabled = false;
        input.focus();
      });
    });
  });
})();
