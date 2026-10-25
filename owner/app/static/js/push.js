/* Aura Owner -- "phone notifications" card on the Sales to confirm page.
 *
 * Vanilla JS, external file (CSP: script-src 'self'). The card
 * (#aura-push-optin, licensing/_push_optin.html) is rendered only while push
 * is configured on the server and only for someone who may confirm sales; it
 * ships [hidden] and every sentence in it is already translated -- this file
 * only chooses which one to show (data-push-state) and which button.
 *
 *   unsupported   no service worker / Push API in this browser
 *   ios-install   iPhone/iPad Safari outside the home-screen app: Apple only
 *                 allows push in the installed app (iOS 16.4+)
 *   denied        the browser blocks notifications for this site
 *   off / on      this device's state; a button switches it
 *   error         the last switch failed (nothing changed on the server)
 *
 * Asking for permission happens ONLY when the person presses "Turn on" --
 * never on page load (browsers also penalise sites that ask unprompted).
 * When this device is already on, every visit re-sends its subscription:
 * that heals a server row removed after the push service expired it, and a
 * subscription made with an old server key (after a key change) is replaced.
 * Nothing is logged.
 */
(function () {
  "use strict";

  function onReady(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  function b64urlToBytes(value) {
    var padded = (value + "===".slice((value.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
    var raw = window.atob(padded);
    var bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return bytes;
  }

  function sameKey(subscription, keyBytes) {
    var current = subscription.options && subscription.options.applicationServerKey;
    if (!current) return true; // older browsers do not expose it: assume it matches
    var a = new Uint8Array(current);
    if (a.length !== keyBytes.length) return false;
    for (var i = 0; i < a.length; i++) if (a[i] !== keyBytes[i]) return false;
    return true;
  }

  function isIOS() {
    var ua = window.navigator.userAgent || "";
    if (/iPad|iPhone|iPod/.test(ua)) return true;
    return window.navigator.platform === "MacIntel" && window.navigator.maxTouchPoints > 1;
  }

  function isStandalone() {
    try {
      if (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) return true;
    } catch (e) { /* fall through */ }
    return window.navigator.standalone === true;
  }

  onReady(function () {
    var card = document.getElementById("aura-push-optin");
    if (!card) return;
    var keyBytes = null;
    try { keyBytes = b64urlToBytes(card.getAttribute("data-public-key") || ""); } catch (e) { keyBytes = null; }
    var subscribeUrl = card.getAttribute("data-subscribe-url");
    var unsubscribeUrl = card.getAttribute("data-unsubscribe-url");
    var csrf = card.getAttribute("data-csrf") || "";
    var enableBtn = card.querySelector("[data-push-action='enable']");
    var disableBtn = card.querySelector("[data-push-action='disable']");

    function show(state) {
      var texts = card.querySelectorAll("[data-push-state]");
      for (var i = 0; i < texts.length; i++) texts[i].hidden = texts[i].getAttribute("data-push-state") !== state;
      if (enableBtn) enableBtn.hidden = !(state === "off" || state === "error");
      if (disableBtn) disableBtn.hidden = state !== "on";
      card.setAttribute("data-push-current", state);
      card.hidden = false;
    }

    function post(url, body) {
      return window.fetch(url, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Content-Type": "application/json", "Accept": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify(body),
      }).then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      });
    }

    function registration() {
      return window.navigator.serviceWorker.ready;
    }

    function subscribeAndSend(reg) {
      return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes })
        .then(function (subscription) { return post(subscribeUrl, subscription.toJSON()); });
    }

    var supported = keyBytes && keyBytes.length === 65 && "serviceWorker" in window.navigator &&
      "PushManager" in window && "Notification" in window && window.fetch;
    if (!supported) {
      show(isIOS() && !isStandalone() ? "ios-install" : "unsupported");
      return;
    }
    if (window.Notification.permission === "denied") {
      show("denied");
      return;
    }

    registration().then(function (reg) {
      return reg.pushManager.getSubscription().then(function (subscription) {
        if (!subscription) { show("off"); return null; }
        if (!sameKey(subscription, keyBytes)) {
          // Made with an old server key: it can never receive again. Replace it.
          return subscription.unsubscribe().then(function () {
            if (window.Notification.permission !== "granted") { show("off"); return null; }
            return subscribeAndSend(reg).then(function () { show("on"); });
          });
        }
        return post(subscribeUrl, subscription.toJSON()).then(function () { show("on"); });
      });
    }).catch(function () { show("off"); });

    if (enableBtn) {
      enableBtn.addEventListener("click", function () {
        enableBtn.disabled = true;
        window.Notification.requestPermission().then(function (permission) {
          if (permission !== "granted") { show(permission === "denied" ? "denied" : "off"); return null; }
          return registration().then(subscribeAndSend).then(function () { show("on"); });
        }).catch(function () { show("error"); }).then(function () { enableBtn.disabled = false; });
      });
    }

    if (disableBtn) {
      disableBtn.addEventListener("click", function () {
        disableBtn.disabled = true;
        registration().then(function (reg) {
          return reg.pushManager.getSubscription();
        }).then(function (subscription) {
          if (!subscription) return null;
          var endpoint = subscription.endpoint;
          return subscription.unsubscribe().then(function () { return post(unsubscribeUrl, { endpoint: endpoint }); });
        }).then(function () { show("off"); }, function () { show("error"); })
          .then(function () { disableBtn.disabled = false; });
      });
    }
  });
})();
