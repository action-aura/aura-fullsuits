/* AuraI18n — product UI bilingual overlay (EN/AR).
 *
 * English is the source of truth and default. t(text) returns the Arabic
 * translation when Arabic is active, else the original English (graceful
 * fallback). Designed to coexist with the marketing-page LandingI18n engine:
 *  - shares the same localStorage key ('aura_lang') so the choice is consistent,
 *  - reuses the [data-i18n] convention,
 *  - NEVER mutates the DOM in English mode, and only replaces a [data-i18n]
 *    element when a translation actually exists (so LandingI18n's semantic keys
 *    are never clobbered = zero regression to the existing landing page).
 */
(function () {
  const AuraI18n = {
    current: 'en',
    dicts: { en: {}, ar: {} },
    _ready: false,

    async load() {
      try {
        const [en, ar] = await Promise.all([
          fetch('/static/locales/en.json').then(r => r.ok ? r.json() : {}).catch(() => ({})),
          fetch('/static/locales/ar.json').then(r => r.ok ? r.json() : {}).catch(() => ({})),
        ]);
        this.dicts.en = en || {};
        this.dicts.ar = ar || {};
      } catch (e) { /* offline-safe: empty dicts -> everything falls back to English */ }
      // Priority: server (set on session load) -> localStorage -> 'en'
      this.current = window.__AURA_LANG || localStorage.getItem('aura_lang') || 'en';
      this._ready = true;
      this.apply();
      this._startObserver();   // keep newly-rendered content translated in Arabic
    },

    t(text) {
      if (text == null) return text;
      if (this.current === 'en') return text;             // default path: untouched English
      const d = this.dicts[this.current] || {};
      return (text in d) ? d[text] : text;                // fallback to the English key
    },

    isRTL() { return this.current === 'ar'; },

    apply() {
      const html = document.documentElement;
      html.setAttribute('lang', this.current);
      html.setAttribute('dir', this.isRTL() ? 'rtl' : 'ltr');
      if (document.body) document.body.classList.toggle('rtl', this.isRTL());
      if (this.current === 'en') return;                  // English: leave the DOM exactly as-is
      const d = this.dicts[this.current] || {};
      // Tagged static elements
      document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        if (key in d) el.textContent = d[key];
      });
      document.querySelectorAll('[data-i18n-ph]').forEach(el => {
        const key = el.getAttribute('data-i18n-ph');
        if (key in d) el.setAttribute('placeholder', d[key]);
      });
      // Broad coverage: sweep the rendered DOM and translate any text node whose
      // FULL trimmed text exactly matches a dictionary key. This makes Arabic
      // apply across deep sub-views without hand-wrapping every string, while
      // leaving user data (names, values — never dict keys) untouched.
      if (document.body) this._translateTree(document.body);
    },

    // Skip containers where translating text would be wrong/harmful.
    _SKIP: { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, INPUT: 1, SELECT: 1, OPTION: 1, CANVAS: 1, CODE: 1, PRE: 1 },

    _translateNode(node, d) {
      const raw = node.nodeValue;
      if (!raw) return;
      const key = raw.trim();
      if (key && (key in d) && d[key] !== key) {
        node.nodeValue = raw.replace(key, d[key]);   // preserve surrounding whitespace
      }
    },

    _translateTree(root) {
      if (this.current !== 'ar' || !root) return;
      const d = this.dicts.ar || {};
      const self = this;
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode(n) {
          if (!n.nodeValue || !n.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
          const p = n.parentNode;
          if (p && self._SKIP[p.nodeName]) return NodeFilter.FILTER_REJECT;
          if (p && p.closest && p.closest('[data-no-i18n]')) return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        }
      });
      const nodes = [];
      let n; while ((n = walker.nextNode())) nodes.push(n);
      nodes.forEach(node => self._translateNode(node, d));
    },

    // Observe the app: re-translate newly-rendered content while Arabic is active.
    // childList-only (not characterData) so our own text edits don't re-trigger.
    _startObserver() {
      if (this._observer || !document.body) return;
      const self = this;
      this._observer = new MutationObserver(muts => {
        if (self.current !== 'ar') return;
        const d = self.dicts.ar || {};
        for (const m of muts) {
          m.addedNodes.forEach(node => {
            if (node.nodeType === 1) self._translateTree(node);
            else if (node.nodeType === 3) self._translateNode(node, d);
          });
        }
      });
      this._observer.observe(document.body, { childList: true, subtree: true });
    },

    async setLang(lang) {
      if (lang !== 'en' && lang !== 'ar') lang = 'en';
      this.current = lang;
      localStorage.setItem('aura_lang', lang);
      this.apply();
      // The header isn't re-rendered on language change, so update the toggle label in place.
      const _tb = document.getElementById('aura-lang-toggle');
      if (_tb) _tb.textContent = (lang === 'ar') ? 'EN' : 'ع';
      // Persist to the user account (best-effort; ignored pre-login / offline)
      try {
        await fetch('/api/auth/language', {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ language: lang }),
        });
      } catch (e) { /* localStorage already holds it */ }
      // Re-render the active product view so t() re-runs (no full page reload)
      try {
        if (window.SubsystemApp && SubsystemApp.active) {
          SubsystemApp._navigate(SubsystemApp.currentSection || 'dashboard');
        } else if (window.SubsystemApp && typeof SubsystemApp.init === 'function') {
          SubsystemApp.init();
        }
      } catch (e) { /* if re-render fails, the apply() text swap already happened */ }
    },

    toggle() { this.setLang(this.current === 'en' ? 'ar' : 'en'); },
  };

  window.AuraI18n = AuraI18n;
  window.t = function (s) { return AuraI18n.t(s); };   // global shorthand used by render code

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => AuraI18n.load());
  } else {
    AuraI18n.load();
  }
})();
