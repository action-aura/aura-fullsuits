/*
  Aura Retail marketing site -- language engine.

  No framework, no build step: every translatable element in the HTML
  carries a `data-i18n="dotted.path"` attribute that resolves against
  js/content.js's AURA_CONTENT[lang] object (array items are addressed by
  numeric index, e.g. "proof.0.title"). `data-i18n-attr="attr:path;attr2:path2"`
  does the same for element attributes (meta descriptions, aria-labels).

  Arabic is the default language (this product is sold Arabic-first --
  see website/README.md and CLAUDE.md's "Arabic-first interface" note).
  The blocking snippet at the top of each page's <head> already applied a
  stored English preference, if any, before first paint -- this file is
  what actually fills in the text and wires the toggle.
*/
(function (global) {
  "use strict";

  var STORAGE_KEY = "aura_lang";
  var DEFAULT_LANG = "ar";

  function readStoredLang() {
    try {
      return global.localStorage.getItem(STORAGE_KEY);
    } catch (err) {
      return null; // private browsing / storage disabled -- fall back silently
    }
  }

  function writeStoredLang(lang) {
    try {
      global.localStorage.setItem(STORAGE_KEY, lang);
    } catch (err) {
      /* degrade silently -- the toggle still works for this page view */
    }
  }

  function resolveInitialLang() {
    var stored = readStoredLang();
    return stored === "en" || stored === "ar" ? stored : DEFAULT_LANG;
  }

  function getByPath(obj, path) {
    var segments = path.split(".");
    var current = obj;
    for (var i = 0; i < segments.length; i++) {
      if (current == null) return undefined;
      current = current[segments[i]];
    }
    return current;
  }

  function applyLang(lang) {
    var dict = global.AURA_CONTENT && global.AURA_CONTENT[lang];
    if (!dict) return;

    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
    document.body.classList.toggle("lang-ar", lang === "ar");
    document.body.classList.toggle("lang-en", lang === "en");

    var textNodes = document.querySelectorAll("[data-i18n]");
    for (var i = 0; i < textNodes.length; i++) {
      var el = textNodes[i];
      var key = el.getAttribute("data-i18n");
      var value = getByPath(dict, key);
      if (value === undefined) {
        console.warn('[aura-i18n] missing "' + key + '" for lang "' + lang + '"');
        continue;
      }
      el.textContent = value;
    }

    var attrNodes = document.querySelectorAll("[data-i18n-attr]");
    for (var j = 0; j < attrNodes.length; j++) {
      var attrEl = attrNodes[j];
      var pairs = attrEl.getAttribute("data-i18n-attr").split(";");
      for (var p = 0; p < pairs.length; p++) {
        var pair = pairs[p].trim();
        if (!pair) continue;
        var split = pair.indexOf(":");
        if (split === -1) continue;
        var attrName = pair.slice(0, split).trim();
        var attrKey = pair.slice(split + 1).trim();
        var attrValue = getByPath(dict, attrKey);
        if (attrValue !== undefined) attrEl.setAttribute(attrName, attrValue);
      }
    }

    var toggles = document.querySelectorAll("[data-lang-btn]");
    for (var k = 0; k < toggles.length; k++) {
      var pressed = toggles[k].getAttribute("data-lang-btn") === lang;
      toggles[k].setAttribute("aria-pressed", pressed ? "true" : "false");
    }

    // Let page-specific scripts (pricing.html's currency re-render) react
    // to the language actually having changed, instead of guessing at
    // click-listener registration order. Two independent `click` listeners
    // on the same toggle button run in attachment order, not DOM order,
    // and a listener attached before this file's own DOMContentLoaded
    // handler would read document.documentElement.lang BEFORE this
    // function updates it -- exactly the one-click-behind currency bug
    // this event was added to fix.
    document.dispatchEvent(new CustomEvent("aura:langchange", { detail: { lang: lang } }));
  }

  function setLang(lang) {
    if (lang !== "en" && lang !== "ar") return;
    writeStoredLang(lang);
    applyLang(lang);
  }

  function init() {
    applyLang(resolveInitialLang());
    var toggles = document.querySelectorAll("[data-lang-btn]");
    for (var i = 0; i < toggles.length; i++) {
      toggles[i].addEventListener("click", function (evt) {
        setLang(evt.currentTarget.getAttribute("data-lang-btn"));
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  global.AuraI18n = { setLang: setLang, applyLang: applyLang, resolveInitialLang: resolveInitialLang };
})(window);
