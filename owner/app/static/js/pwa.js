/* Aura Owner -- installable web app: service-worker registration + the
 * "add to home screen" hint.
 *
 * Vanilla JS, external file (CSP: script-src 'self', no inline script).
 * Loaded on every page by layout/base.html, which passes the worker URL in
 * data-sw-url (url_for('pwa.service_worker') -- /sw.js, so its scope is the
 * whole site without a Service-Worker-Allowed header; see app/pwa/routes.py).
 *
 * The hint (#aura-install-hint, server-rendered [hidden], text already
 * translated) is shown:
 *   - on Android/Chrome-family browsers only when the browser itself says the
 *     app is installable (beforeinstallprompt), with an Install button that
 *     opens the browser's own install dialog;
 *   - on iPhone/iPad, where no such event exists, as a one-line instruction
 *     (Share, then Add to Home Screen).
 * It is never shown inside the installed app (display-mode standalone /
 * navigator.standalone -- also enforced by CSS in shell.css), and once
 * dismissed it stays dismissed on that device (localStorage, wrapped in
 * try/catch: private mode or blocked storage just means it may show again).
 * Registration failure is silent: the site works exactly as before without
 * a worker.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  var swUrl = script ? script.getAttribute("data-sw-url") : null;
  var swScope = script ? script.getAttribute("data-sw-scope") : null;
  var DISMISS_KEY = "aura-owner-install-hint-dismissed";
  var deferredPrompt = null;

  function isStandalone() {
    try {
      if (window.matchMedia) {
        var modes = ["standalone", "fullscreen", "minimal-ui", "window-controls-overlay"];
        for (var i = 0; i < modes.length; i++) {
          if (window.matchMedia("(display-mode: " + modes[i] + ")").matches) return true;
        }
      }
    } catch (e) { /* very old browser: fall through to the iOS flag */ }
    return window.navigator.standalone === true;
  }

  function wasDismissed() {
    try { return window.localStorage.getItem(DISMISS_KEY) === "1"; } catch (e) { return false; }
  }

  function rememberDismissed() {
    try { window.localStorage.setItem(DISMISS_KEY, "1"); } catch (e) { /* storage blocked: may show again */ }
  }

  function isIOS() {
    var ua = window.navigator.userAgent || "";
    if (/iPad|iPhone|iPod/.test(ua)) return true;
    // iPadOS 13+ reports itself as a Mac; a touch-capable "Mac" is an iPad.
    return window.navigator.platform === "MacIntel" && window.navigator.maxTouchPoints > 1;
  }

  function hintElement() {
    return document.getElementById("aura-install-hint");
  }

  function hide() {
    var hint = hintElement();
    if (hint) hint.hidden = true;
  }

  function show(variant) {
    if (isStandalone() || wasDismissed()) return;
    var hint = hintElement();
    if (!hint) return;
    var texts = hint.querySelectorAll("[data-install-variant]");
    for (var i = 0; i < texts.length; i++) {
      texts[i].hidden = texts[i].getAttribute("data-install-variant") !== variant;
    }
    var installButton = hint.querySelector("[data-install-action='install']");
    if (installButton) installButton.hidden = variant !== "android";
    hint.hidden = false;
  }

  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  if (swUrl && "serviceWorker" in window.navigator) {
    window.addEventListener("load", function () {
      window.navigator.serviceWorker.register(swUrl, { scope: swScope || "/" }).catch(function () {
        /* the site works without a worker; nothing to tell the user */
      });
    });
  }

  window.addEventListener("beforeinstallprompt", function (event) {
    // Only take over the browser's own install banner when we are going to
    // show ours instead -- otherwise leave the browser's default alone.
    if (isStandalone() || wasDismissed()) return;
    event.preventDefault();
    deferredPrompt = event;
    onReady(function () { show("android"); });
  });

  window.addEventListener("appinstalled", function () {
    deferredPrompt = null;
    rememberDismissed();
    hide();
  });

  onReady(function () {
    var hint = hintElement();
    if (!hint) return;
    hint.addEventListener("click", function (event) {
      var target = event.target;
      var action = target && target.closest ? target.closest("[data-install-action]") : null;
      if (!action) return;
      if (action.getAttribute("data-install-action") === "dismiss") {
        rememberDismissed();
        hide();
        return;
      }
      if (action.getAttribute("data-install-action") === "install" && deferredPrompt) {
        var prompt = deferredPrompt;
        deferredPrompt = null;
        hide();
        prompt.prompt();
        // Either answer ends the nagging: installed, or a clear "no".
        if (prompt.userChoice && prompt.userChoice.then) {
          prompt.userChoice.then(function () { rememberDismissed(); }, function () { /* ignore */ });
        }
      }
    });
    if (isIOS()) show("ios");
  });
})();
