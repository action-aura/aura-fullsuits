// help.js -- the Help dialog, the support link and the printable till key card
// (Phase 1b, item 11). Exposes window.AuraHelp.
//
// WHY IT EXISTS. A shop owner who is stuck at the counter needs three things
// within one click: (1) a way to reach a human at Action Aura who already knows
// WHICH shop, WHICH version and WHICH screen they are looking at, (2) a card
// with the till's F-keys they can stick next to the screen, and (3) the
// installation id and version, which support asks for first. Before this, none
// of the three existed in the product.
//
// THE TWO NUMBERS. A wrong number in a shipped build is worse than none (a
// shopkeeper would send a customer's licence id to a stranger), so each lives
// in this ONE place and every surface built on it hides itself while it is
// null: supportUrl() returns null, the dialog omits the chat link, and the
// palette omits its row.
//   * SUPPORT: the company number, +962 78 006 9485 (owner, 2026-10-04: the
//     phone AND WhatsApp line). Set 2026-10-12 (UI-1) -- Help used to show no
//     way to reach anyone at all because this was null.
//   * SALES: still null. The owner has not given a separate sales number and
//     the support line is not assumed to be one.
// Filling in a number here is the only change needed to switch a surface on.
//
// THE F-KEY CARD is generated from RetailSystem.POS_PAYMENT_METHODS at print
// time, never typed by hand: that list is what actually drives the F1-F6
// accelerators in the till (_onPOSShortcut indexes it), so a card built from it
// cannot describe a key that does nothing. It is printed through a hidden
// iframe (same recipe as the receipt printer) so the app underneath is never
// disturbed and no popup blocker can eat it.

const AuraHelp = {
  // Digits with country code, no plus sign or spaces, e.g. '9627XXXXXXXX'.
  ACTION_AURA_SUPPORT_WHATSAPP: '962780069485',
  ACTION_AURA_SALES_WHATSAPP: null,
  // How the same number reads on screen. Shown (left-to-right) under the chat
  // link so a shop without WhatsApp on this computer can simply phone it.
  ACTION_AURA_SUPPORT_PHONE_DISPLAY: '+962 78 006 9485',

  _ctx: { installationId: null, appVersion: null },
  _loading: null,
  _lastCardFrame: null,

  // Fetches the two facts the support message carries. Both routes are cheap;
  // failures leave the fields null (the message then simply omits them) rather
  // than blocking Help on a network hiccup.
  load() {
    if (this._loading) return this._loading;
    const getJson = async (url) => {
      try {
        const r = await fetch(url, { credentials: 'include' });
        return await r.json();
      } catch (e) { return null; }
    };
    this._loading = Promise.all([getJson('/api/version'), getJson('/api/licensing/status')]).then(([v, lic]) => {
      if (v && v.app_version) this._ctx.appVersion = String(v.app_version);
      const status = lic && (lic.data || lic);
      if (status && status.installation_id) this._ctx.installationId = String(status.installation_id);
      this._loading = null;
      return this.context();
    });
    return this._loading;
  },

  // What support needs to know, from what is already loaded. `screen` is read
  // live (it changes as the person navigates); the other two are cached.
  context() {
    const app = window.SubsystemApp;
    return {
      installationId: this._ctx.installationId,
      appVersion: this._ctx.appVersion,
      screen: (app && app.currentSection) || null,
    };
  },

  _digits(n) { return typeof n === 'string' ? n.replace(/\D/g, '') : ''; },

  _url(number, lines) {
    const digits = this._digits(number);
    if (!digits) return null;
    return 'https://wa.me/' + digits + '?text=' + encodeURIComponent(lines.join('\n'));
  },

  // null while the number is null. The message is English on purpose: it is
  // read by the support team, not the shopkeeper.
  supportUrl(context) {
    const c = context || this.context();
    const lines = ['Hello Action Aura support,'];
    if (c.installationId) lines.push('Installation: ' + c.installationId);
    if (c.appVersion) lines.push('Version: ' + c.appVersion);
    if (c.screen) lines.push('Screen: ' + c.screen);
    return this._url(this.ACTION_AURA_SUPPORT_WHATSAPP, lines);
  },

  salesUrl(context) {
    const c = context || this.context();
    const lines = ['Hello Action Aura sales,'];
    if (c.installationId) lines.push('Installation: ' + c.installationId);
    return this._url(this.ACTION_AURA_SALES_WHATSAPP, lines);
  },

  _esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  },

  // Arabic for the strings ONLY the printed card uses, so the card is Arabic
  // even before locales/ar.json carries them (the card ignores the UI language,
  // see fkeyCardHtml). dicts.ar wins when it has the string.
  CARD_AR: {
    'Till keys': 'مفاتيح الصندوق',
    'Press a key on the till screen to choose how the customer pays.': 'اضغط مفتاحًا على شاشة الصندوق لاختيار طريقة دفع العميل.',
    'Charge the sale': 'إتمام البيع',
    'Exact cash: charge at once': 'دفع نقدي بالمبلغ الكامل فورًا',
    'Remove the last line': 'حذف آخر سطر',
    'Go to the search box': 'الانتقال إلى مربع البحث',
    'Quantity of the next item (type 3, then *)': 'كمية الصنف التالي (اكتب 3 ثم *)',
    'Add the item you typed. With an empty search: go to payment': 'أضف الصنف الذي كتبته. والبحث فارغ: انتقل إلى الدفع',
    'Pick a search result, or a cart line when the search is empty': 'اختر نتيجة بحث، أو سطرًا من السلة عندما يكون البحث فارغًا',
    'One more or one fewer of the selected line': 'زيادة أو إنقاص واحد في السطر المحدد',
    'Quantity of the next item (type 3, then *), or of the selected line': 'كمية الصنف التالي (اكتب 3 ثم *)، أو كمية السطر المحدد',
    'Remove the selected line (or the last one)': 'حذف السطر المحدد (أو الأخير)',
    'Void the whole sale': 'إلغاء البيع كاملًا',
    'Choose a customer': 'اختيار عميل',
    'Discount': 'الخصم',
    'Hold the sale': 'تعليق البيع',
    'Held sales': 'المبيعات المعلّقة',
    'Price check': 'فحص السعر',
    'Show these keys': 'عرض هذه المفاتيح',
    'Close, clear the search, cancel': 'إغلاق، مسح البحث، إلغاء',
    'On the receipt: print': 'في الإيصال: طباعة',
    'Sign out': 'تسجيل الخروج',
    'Full screen': 'ملء الشاشة',
    'Cash drawer: open shift, cash in/out, X report, end shift': 'درج النقد: فتح وردية، إيداع/سحب، تقرير X، إنهاء الوردية',
  },

  _ar(s) {
    const d = (window.AuraI18n && AuraI18n.dicts && AuraI18n.dicts.ar) || {};
    return (s in d) ? d[s] : ((s in this.CARD_AR) ? this.CARD_AR[s] : s);
  },

  // The card as a complete standalone HTML document (for the print iframe and
  // for tests to read).
  //
  // WHICH KEY DOES WHAT comes from the SAME place the till's key handler reads:
  // F1..F6 are POSITIONS in RetailSystem.POS_PAYMENT_METHODS
  // (`POS_PAYMENT_METHODS[n - 1]` in _onPOSShortcut), NOT the `fkey` text on
  // each entry. A card built from `m.fkey` agrees with the handler only while
  // the two happen to match; reorder the list and it prints keys that do
  // something else (verifier F6). The other rows are the till's fixed keys.
  //
  // It is printed in ARABIC whatever the screen language: the card is stuck
  // next to the till for the cashier, and the shop's language is not the
  // owner's screen language. An English line under each Arabic one is for the
  // owner setting it up. Key names stay left-to-right.
  fkeyCardHtml() {
    const methods = (window.RetailSystem && RetailSystem.POS_PAYMENT_METHODS) || [];
    const esc = (s) => this._esc(s);
    const row = (key, en) => {
      const ar = this._ar(en);
      const enLine = ar !== en ? `<small class="en" lang="en" dir="ltr">${esc(en)}</small>` : '';
      return `<tr><td class="k" dir="ltr">${esc(key)}</td><td class="v"><span class="ar" lang="ar">${esc(ar)}</span>${enLine}</td></tr>`;
    };
    // The handler only answers F1..F6 for tenders, so a 7th method has NO key
    // and must not be printed as one (F7 is the customer picker now -- it is
    // printed from POS_KEY_HELP below, under its own name, never as a tender).
    // The other rows come from RetailSystem.POS_KEY_HELP, the same list the
    // F12 overlay shows, so the printed card and the on-screen card cannot
    // disagree. Its own "F1 – F6" row is skipped: the six tender rows above
    // are generated from the live list and are more specific.
    const help = (window.RetailSystem && RetailSystem.POS_KEY_HELP) || [];
    const rows = methods.slice(0, 6).map((m, i) => row('F' + (i + 1), m.label)).join('')
      + help.filter(([k]) => k !== 'F1 – F6').map(([k, en]) => row(k, en)).join('');
    return `<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8">
<title>${esc(this._ar('Till keys'))}</title>
<style>
  body { font-family: system-ui, 'Segoe UI', Tahoma, sans-serif; margin: 0; padding: 24px; color: #000; background: #fff; }
  .card { border: 2px solid #000; border-radius: 10px; padding: 18px 22px; inline-size: 360px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  p { margin: 0 0 12px; font-size: 13px; }
  table { border-collapse: collapse; inline-size: 100%; }
  td { padding: 8px 6px; border-block-start: 1px solid #000; font-size: 16px; }
  td.k { font-weight: 700; inline-size: 64px; font-family: ui-monospace, Consolas, monospace; }
  small.en { display: block; font-size: 11px; color: #333; }
</style></head><body>
<div class="card" id="fkey-card">
  <h1>${esc(this._ar('Till keys'))}</h1>
  <p>${esc(this._ar('Press a key on the till screen to choose how the customer pays.'))}</p>
  <table><tbody>${rows}</tbody></table>
</div>
</body></html>`;
  },

  printFkeyCard() {
    if (this._lastCardFrame && this._lastCardFrame.remove) this._lastCardFrame.remove();
    const frame = document.createElement('iframe');
    frame.setAttribute('aria-hidden', 'true');
    frame.tabIndex = -1;
    frame.style.cssText = 'position:fixed;inset-inline-end:0;inset-block-end:0;inline-size:0;block-size:0;border:0;';
    frame.srcdoc = this.fkeyCardHtml();
    frame.addEventListener('load', () => {
      try {
        frame.contentWindow.focus();
        frame.contentWindow.print();
      } catch (e) {
        console.error('till key card: print failed', e);
        if (window.SubsystemApp && typeof SubsystemApp.showToast === 'function') {
          SubsystemApp.showToast(t('Could not print. Check the printer and try again.'), 'error');
        }
      }
      // contentWindow.focus() above moved keyboard focus INTO the hidden frame
      // (print() prints the focused frame). Once print() returns, hand focus
      // back to the dialog's own button, otherwise Esc and Tab go to a
      // zero-size frame nobody can see and the dialog seems frozen.
      try {
        window.focus();
        const btn = document.querySelector('#aura-help-dialog [data-help="fkeys"]');
        if (btn) btn.focus();
      } catch (_) { /* focus is a courtesy; printing already happened */ }
      // Give the spooler a moment, then let go of the frame.
      setTimeout(() => { if (frame.isConnected && this._lastCardFrame !== frame) frame.remove(); }, 60000);
    });
    document.body.appendChild(frame);
    this._lastCardFrame = frame;
    return frame;
  },

  // ── dialog ────────────────────────────────────────────────────────────────
  open() {
    if (document.getElementById('aura-help-overlay')) return;
    const rs = window.RetailSystem;
    if (rs && typeof rs._injectStyles === 'function') rs._injectStyles();
    const app = window.SubsystemApp;
    const support = this.supportUrl();
    const showSetup = !!(app && app.role === 'admin' && window.AuraSetupGuide && typeof window.AuraSetupGuide.open === 'function');
    const c = this.context();

    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'aura-help-overlay';
    overlay.innerHTML = `
      <div class="ret-modal aura-help" id="aura-help-dialog" role="dialog" aria-modal="true" aria-labelledby="aura-help-title">
        <div class="aura-help-head">
          <h3 id="aura-help-title">${this._esc(t('Help'))}</h3>
          <button type="button" class="ret-btn ret-btn-ghost ret-btn-sm" id="aura-help-close" aria-label="${this._esc(t('Close'))}" title="${this._esc(t('Close'))}">✕</button>
        </div>
        <div class="aura-help-actions">
          ${support ? `<a class="aura-help-action" data-help="support" href="${this._esc(support)}" target="_blank" rel="noopener">
            <span class="aura-help-action-title">${this._esc(t('Chat with Action Aura support'))}</span>
            <span class="aura-help-action-sub">${this._esc(t('Opens WhatsApp with your shop details filled in'))}</span></a>` : ''}
          ${support ? `<p class="aura-help-contact" id="aura-help-phone" style="margin:0;padding-inline:4px;font-size:14px;color:var(--text-muted)">
            ${this._esc(t('Phone and WhatsApp'))}: <a href="tel:+${this._esc(this._digits(this.ACTION_AURA_SUPPORT_WHATSAPP))}" dir="ltr" style="font-weight:700;color:var(--text)">${this._esc(this.ACTION_AURA_SUPPORT_PHONE_DISPLAY)}</a></p>` : ''}
          <button type="button" class="aura-help-action" data-help="fkeys">
            <span class="aura-help-action-title">${this._esc(t('Print the till key card'))}</span>
            <span class="aura-help-action-sub">${this._esc(t('A card with the till keys to stick next to the screen'))}</span></button>
          ${showSetup ? `<button type="button" class="aura-help-action" data-help="setup-guide">
            <span class="aura-help-action-title">${this._esc(t('Setup guide'))}</span>
            <span class="aura-help-action-sub">${this._esc(t('The steps to get your shop ready'))}</span></button>` : ''}
        </div>
        <dl class="aura-help-about">
          <dt>${this._esc(t('Version'))}</dt><dd id="aura-help-version" dir="ltr">${this._esc(c.appVersion || '—')}</dd>
          <dt>${this._esc(t('Installation ID'))}</dt><dd id="aura-help-install" dir="ltr">${this._esc(c.installationId || '—')}</dd>
        </dl>
      </div>`;
    document.body.appendChild(overlay);
    const close = rs && typeof rs._wireModalA11y === 'function'
      ? rs._wireModalA11y(overlay, 'aura-help-dialog')
      : () => overlay.remove();
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    overlay.querySelector('#aura-help-close').addEventListener('click', close);
    overlay.querySelector('[data-help="fkeys"]').addEventListener('click', () => this.printFkeyCard());
    const setupBtn = overlay.querySelector('[data-help="setup-guide"]');
    if (setupBtn) setupBtn.addEventListener('click', () => { close(); window.AuraSetupGuide.open(); });

    // Fill in the two facts in place once they have loaded; the dialog is
    // usable (and printable) before they arrive.
    this.load().then((ctx) => {
      const v = document.getElementById('aura-help-version');
      const i = document.getElementById('aura-help-install');
      if (v) v.textContent = ctx.appVersion || '—';
      if (i) i.textContent = ctx.installationId || '—';
      // The link was built before the installation id was known; rebuild it
      // now so the prefill is complete.
      const a = document.querySelector('#aura-help-dialog [data-help="support"]');
      const fresh = this.supportUrl();
      if (a && fresh) a.setAttribute('href', fresh);
    });
  },
};

window.AuraHelp = AuraHelp;

// Warm the cache so the support link the palette offers already carries the
// installation id and version. Harmless before login (both routes answer or
// fail quietly) and cheap.
AuraHelp.load();
