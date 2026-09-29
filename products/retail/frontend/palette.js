// palette.js -- the Ctrl+K command palette (Phase 1b, item 7).
//
// WHY IT EXISTS. Retail has ~40 screens and a settings area that is about to
// become a hub of eight sections. The sidebar is the map; this is the search
// box. A shopkeeper who knows the word for the thing ("whatsapp", "tax",
// "backup", a customer's name) should reach it in two keystrokes instead of
// hunting through groups -- in Arabic or English, whichever they type.
//
// THE ONE RULE (read before changing _buildIndex). The palette NEVER offers
// what the sidebar hides. It is not a second permission system: every page
// entry is asked of SubsystemApp._navItemState() -- the single source of truth
// for "may this person see this screen" (role, platform, device, owner-only
// and per-grant capability axes) -- and only 'visible' is offered. 'main-only'
// and 'hidden' both mean "not offered": a 'main-only' row in the sidebar is a
// greyed explanation, and a command palette that jumped to it would just fire
// the same refusal. Settings-hub sections are filtered on the hub's own
// `visible` flag for the same reason. Actions and live search results are
// gated on the nav item that would show the same data. A cashier therefore
// gets a strictly smaller palette than an owner, by construction, and
// retail_p1b_palette_e2e.py compares the two sets rather than trusting this
// comment.
//
// It also never floats over the till on its own: it opens only on an explicit
// Ctrl/Cmd+K or a click on the header trigger, and it steps aside when another
// modal (payment, receipt, a confirm) already owns the keyboard.
//
// No dependencies beyond what the shell already loads: vanilla JS, t(),
// AuraIcons, SubsystemApp, RetailSystem. Everything it talks to is checked
// for existence at use time, so a missing optional module (SettingsHub,
// AuraHelp, AuraSetupGuide) just means fewer rows, never an error.

const AuraPalette = {
  RECENT_MAX: 6,
  LIVE_MIN_CHARS: 2,
  LIVE_DEBOUNCE_MS: 200,
  LIVE_PRODUCTS: 8,
  LIVE_CUSTOMERS: 5,
  PAINT_WAIT_MS: 4000,

  // Nav ids whose screens the settings hub now covers with a section of its
  // own. Offering both "Employees" (page) and "Team" (hub section) would list
  // one destination twice, so the page entry steps aside -- but only while the
  // hub section is really offered; if the hub is absent the page entry stays
  // and the palette loses nothing.
  SUPERSEDED_BY_HUB: { employees: 'team', branches: 'team', 'backup-export': 'data-backup' },

  _overlay: null,
  _input: null,
  _list: null,
  _opener: null,
  _layer: null,
  _rows: [],
  _active: 0,
  _staticEntries: [],
  _live: { products: [], customers: [], pending: false },
  _timer: null,
  _abort: null,
  _seq: 0,
  _arToEn: null,
  _arToEnSource: null,

  // ── matching ──────────────────────────────────────────────────────────────
  // Arabic is typed many ways for the same word: with or without diacritics,
  // alef with hamza or without, teh-marbuta vs heh, alef-maqsura vs yeh, with
  // or without tatweel. Fold all of it so "واتساب" and "وَاتْسَاب" and
  // "whatsapp" (case-folded) hit the same row.
  _norm(s) {
    return String(s == null ? '' : s)
      .toLowerCase()
      .replace(/[ؐ-ًؚ-ٰٟۖ-ۭـ]/g, '')
      .replace(/[أإآٱ]/g, 'ا')
      .replace(/ة/g, 'ه')
      .replace(/ى/g, 'ي')
      .replace(/\s+/g, ' ')
      .trim();
  },

  _dict(lang) {
    return (window.AuraI18n && AuraI18n.dicts && AuraI18n.dicts[lang]) || {};
  },

  // Arabic -> English reverse lookup, built lazily and cached against the
  // dictionary OBJECT (a reload of the catalogue replaces the object, which
  // invalidates the cache without any bookkeeping here).
  _reverseAr() {
    const ar = this._dict('ar');
    if (this._arToEn && this._arToEnSource === ar) return this._arToEn;
    const rev = {};
    Object.keys(ar).forEach((en) => {
      const v = ar[en];
      if (typeof v === 'string' && v && !(v in rev)) rev[v] = en;
    });
    this._arToEn = rev;
    this._arToEnSource = ar;
    return rev;
  },

  // Every spelling a row can be found under: what the screen shows now, its
  // Arabic, its English (whichever language the UI is currently in), plus
  // explicit keywords (and their Arabic).
  _variants(label, keywords, sub) {
    const ar = this._dict('ar');
    const out = [label, ar[label], this._reverseAr()[label]];
    (keywords || []).forEach((k) => { out.push(k, ar[k]); });
    // A live row's second line (customer phone; product "sku · barcode") is
    // what the person is often typing -- the server matched on it, so the
    // client filter must too (verifier F3: a phone number showed "Nothing
    // found" while the server had returned the customer). Static rows have
    // no `sub`, so nothing changes for them.
    // Digits are compared per part ("sku · barcode"), so the SKU's digits and
    // the barcode's never run together into a number neither of them holds.
    if (sub) {
      out.push(sub);
      String(sub).split(' · ').forEach((part) => {
        const d = part.replace(/\D+/g, '');
        if (d) out.push(this._stripZeros(d));
      });
    }
    return out.filter(Boolean).map((v) => this._norm(v));
  },

  // Phone numbers are typed with and without the trunk zero (0791112233 /
  // 791112233 / +962 79 111 2233). Compare digits only, leading zeros dropped;
  // a value with no other digits ("0") is left alone so it cannot become an
  // empty string that matches every row.
  _stripZeros(digits) {
    const s = String(digits || '').replace(/^0+/, '');
    return s || String(digits || '');
  },

  // null = does not match. Lower = better: prefix 0, word start 1, substring
  // 2, matched only by a keyword/other-language variant 3. EVERY whitespace-
  // separated token must be found, in the same variant string.
  _score(entry, q) {
    const tokens = this._norm(q).split(' ').filter(Boolean)
      .map((tok) => (/^\d+$/.test(tok) ? this._stripZeros(tok) : tok));
    if (!tokens.length) return 0;
    let best = null;
    for (const v of entry._variants) {
      let total = 0;
      let ok = true;
      for (const tok of tokens) {
        const at = v.indexOf(tok);
        if (at === -1) { ok = false; break; }
        total = Math.max(total, at === 0 ? 0 : (v.charAt(at - 1) === ' ' ? 1 : 2));
      }
      if (ok && (best === null || total < best)) best = total;
    }
    if (best === null) return null;
    // A hit on the row's own visible label beats a hit on a keyword alone.
    const own = this._norm(entry.label);
    return own.indexOf(this._norm(q)) === -1 && best < 3 && !tokens.every((k) => own.indexOf(k) !== -1)
      ? Math.max(best, 3)
      : best;
  },

  // ── index ─────────────────────────────────────────────────────────────────
  _app() { return window.SubsystemApp || null; },

  _hubSections() {
    try {
      const hub = window.SettingsHub;
      const list = hub && typeof hub.sections === 'function' ? hub.sections() : [];
      return Array.isArray(list) ? list.filter((s) => s && s.visible === true && s.id) : [];
    } catch (e) {
      return [];
    }
  },

  _icon(name, size) {
    return window.AuraIcons ? AuraIcons.render(name, size || 16) : '';
  },

  _navItem(app, id) {
    const sys = app && app.systems && app.systems[app.active];
    return sys ? (sys.nav || []).find((i) => i.id === id) || null : null;
  },

  _navVisible(app, id) {
    const item = this._navItem(app, id);
    return !!item && app._isPageOffered(item);
  },

  _finish(entry) {
    entry._variants = this._variants(entry.label, entry.keywords, entry.sub);
    return entry;
  },

  _buildIndex() {
    const app = this._app();
    const out = [];
    if (!app || !app.systems || !app.systems[app.active]) { this._staticEntries = out; return out; }
    const sys = app.systems[app.active];
    const hubIds = new Set(this._hubSections().map((s) => s.id));

    (sys.nav || []).forEach((item) => {
      if (!app._isPageOffered(item)) return;                       // THE ONE RULE (= visible + the sell-only Dashboard rule)
      const hubTwin = this.SUPERSEDED_BY_HUB[item.id];
      if (hubTwin && hubIds.has(hubTwin)) return;
      out.push(this._finish({
        key: 'page:' + item.id,
        kind: 'page',
        label: t(item.label),
        icon: item.icon,
        keywords: [item.label],
        run: () => {
          if (item.externalPage) { window.location.href = '/static/' + item.externalPage; return; }
          app._navigate(item.id);
        },
      }));
    });

    this._hubSections().forEach((s) => {
      out.push(this._finish({
        key: 'setting:' + s.id,
        kind: 'setting',
        label: s.label,
        iconName: 'settings',
        keywords: [],
        run: () => { window.SettingsHub.open(s.id); },
      }));
    });

    this._actionEntries(app).forEach((e) => out.push(this._finish(e)));
    this._helpEntries(app).forEach((e) => out.push(this._finish(e)));
    this._staticEntries = out;
    return out;
  },

  _actionEntries(app) {
    const out = [];
    const cd = window.CashDrawer;
    if (this._navVisible(app, 'pos') && app.hasCapability('retail.cash.close') && cd
        && typeof cd.openOpenShiftModal === 'function'
        && cd._state !== 'open' && cd._state !== 'denied') {
      out.push({
        key: 'action:open-till', kind: 'action', label: t('Open till'), iconName: 'banknote',
        keywords: ['shift', 'cash drawer', 'float'],
        run: () => { app._navigate('pos'); cd.openOpenShiftModal(); },
      });
    }
    if (this._navVisible(app, 'products') && app.hasCapability('retail.stock.adjust')) {
      out.push({
        key: 'action:new-product', kind: 'action', label: t('New product'), iconName: 'plus',
        keywords: ['add product', 'item', 'stock'],
        run: () => this._openProductsScreen().then(() => {
          if (window.RetailSystem && typeof RetailSystem._openAddProduct === 'function') RetailSystem._openAddProduct();
        }),
      });
    }
    if (this._navVisible(app, 'receivables')) {
      out.push({
        key: 'action:customer-payment', kind: 'action', label: t('Record customer payment'), iconName: 'wallet',
        keywords: ['receive payment', 'debt', 'credit', 'collect'],
        run: () => { app._navigate('receivables'); },
      });
    }
    return out;
  },

  _helpEntries(app) {
    const out = [];
    const help = window.AuraHelp;
    if (help && typeof help.open === 'function') {
      out.push({
        key: 'help:open', kind: 'help', label: t('Help'), iconName: 'circle-help',
        keywords: ['support', 'guide', 'manual'],
        run: () => help.open(),
      });
      if (typeof help.printFkeyCard === 'function') {
        out.push({
          key: 'help:fkeys', kind: 'help', label: t('Print the till key card'), iconName: 'printer',
          keywords: ['shortcuts', 'keys', 'f1', 'cheat sheet'],
          run: () => help.printFkeyCard(),
        });
      }
      let url = null;
      try { url = typeof help.supportUrl === 'function' ? help.supportUrl() : null; } catch (e) { url = null; }
      // The support number is null until Action Aura fills it in (help.js);
      // an entry that opens nothing would be a dead row, so it is not offered.
      if (url) {
        out.push({
          key: 'help:support', kind: 'help', label: t('Chat with Action Aura support'), iconName: 'message-circle',
          keywords: ['whatsapp', 'contact', 'problem'],
          run: () => { window.open(help.supportUrl(), '_blank', 'noopener'); },
        });
      }
    }
    if (app.role === 'admin' && window.AuraSetupGuide && typeof window.AuraSetupGuide.open === 'function') {
      out.push({
        key: 'help:setup-guide', kind: 'help', label: t('Setup guide'), iconName: 'sparkles',
        keywords: ['getting started', 'checklist'],
        run: () => window.AuraSetupGuide.open(),
      });
    }
    return out;
  },

  // For tests and diagnostics: what would be offered right now, no DOM.
  entries() {
    return this._buildIndex().map((e) => ({ key: e.key, kind: e.kind, label: e.label }));
  },

  // ── navigation helpers ────────────────────────────────────────────────────
  _sleep(ms) { return new Promise((r) => setTimeout(r, ms)); },

  // _navigate paints a loading placeholder first and replaces it when the
  // screen's data has arrived. "The screen is ready" is therefore "the
  // placeholder is gone" -- observed, not slept for.
  async _whenPainted() {
    const deadline = Date.now() + this.PAINT_WAIT_MS;
    while (Date.now() < deadline) {
      if (!document.querySelector('#sub-content .sub-loading')) return true;
      await this._sleep(40);
    }
    return false;
  },

  // Open the Products screen and resolve once its lists (categories feed the
  // add/edit form) have really loaded. RetailSystem._categories is replaced by
  // a NEW array when the screen's fetch lands, so "it changed" is the signal.
  async _openProductsScreen() {
    const app = this._app();
    if (!app) return;
    const before = window.RetailSystem ? RetailSystem._categories : null;
    app._navigate('products');
    await this._whenPainted();
    const deadline = Date.now() + this.PAINT_WAIT_MS;
    while (window.RetailSystem && RetailSystem._categories === before && Date.now() < deadline) {
      await this._sleep(40);
    }
  },

  _productEntry(p) {
    const name = p.name || p.name_en || p.sku || '';
    return {
      key: 'product:' + p.id, kind: 'product', label: name, iconName: 'package',
      sub: [p.sku, p.barcode].filter(Boolean).join(' · '),
      live: { id: p.id, name, sub: [p.sku, p.barcode].filter(Boolean).join(' · ') },
      run: async () => {
        await this._openProductsScreen();
        const rs = window.RetailSystem;
        if (!rs) return;
        const app = this._app();
        const canEdit = app && app.hasCapability('retail.stock.adjust');
        if (canEdit && (rs._products || []).some((x) => String(x.id) === String(p.id))
            && typeof rs._openEditProduct === 'function') {
          rs._openEditProduct(p.id);
          return;
        }
        // Read-only (or not in the loaded page): land on the row instead.
        const box = document.getElementById('prod-search');
        if (box) { box.value = p.sku || name; if (typeof rs._filterProducts === 'function') rs._filterProducts(); }
      },
    };
  },

  _customerEntry(c) {
    const name = c.name || c.phone || '';
    return {
      key: 'customer:' + c.id, kind: 'customer', label: name, iconName: 'user',
      sub: c.phone || '',
      live: { id: c.id, name, sub: c.phone || '' },
      run: () => {
        if (window.RetailSystem && typeof RetailSystem._openPartyStatement === 'function') {
          RetailSystem._openPartyStatement('customer', c.id);
        }
      },
    };
  },

  // ── recents ───────────────────────────────────────────────────────────────
  _recentKey() {
    const app = this._app();
    return 'aura_palette_recent_' + ((app && app.currentUser && app.currentUser.id) || 'anon');
  },

  _loadRecent() {
    try {
      const raw = localStorage.getItem(this._recentKey());
      const list = raw ? JSON.parse(raw) : [];
      return Array.isArray(list) ? list.filter((x) => x && typeof x.key === 'string') : [];
    } catch (e) {
      return [];
    }
  },

  // Static rows are stored by KEY ONLY and re-resolved against a fresh index
  // when shown, so a remembered row that the person has since lost access to
  // (role changed, licence lapsed) silently disappears instead of being
  // offered from a stale copy. Live rows (a product, a customer) have no
  // static index to resolve against, so they carry their display fields and
  // are re-gated on the nav item that shows the same data.
  _remember(entry) {
    try {
      const rec = entry.live
        ? { key: entry.key, kind: entry.kind, id: entry.live.id, name: entry.live.name, sub: entry.live.sub }
        : { key: entry.key };
      const next = [rec].concat(this._loadRecent().filter((x) => x.key !== entry.key)).slice(0, this.RECENT_MAX);
      localStorage.setItem(this._recentKey(), JSON.stringify(next));
    } catch (e) { /* storage blocked: recents are a convenience, never a requirement */ }
  },

  _recentEntries() {
    const app = this._app();
    const byKey = new Map(this._staticEntries.map((e) => [e.key, e]));
    const out = [];
    this._loadRecent().forEach((r) => {
      if (byKey.has(r.key)) { out.push(byKey.get(r.key)); return; }
      if (r.kind === 'product' && this._navVisible(app, 'products')) {
        out.push(this._productEntry({ id: r.id, name: r.name, sku: r.sub }));
      } else if (r.kind === 'customer' && this._navVisible(app, 'customers')) {
        out.push(this._customerEntry({ id: r.id, name: r.name, phone: r.sub }));
      }
    });
    return out.map((e) => this._finish(Object.assign({ keywords: [] }, e)));
  },

  // ── live results ──────────────────────────────────────────────────────────
  _scheduleLive(query) {
    clearTimeout(this._timer);
    if (this._abort) { try { this._abort.abort(); } catch (e) { /* already done */ } this._abort = null; }
    this._live = { products: [], customers: [], pending: false };
    const q = String(query || '').trim();
    if (q.length < this.LIVE_MIN_CHARS) return;
    const app = this._app();
    const wantProducts = this._navVisible(app, 'products');
    const wantCustomers = this._navVisible(app, 'customers');
    if (!wantProducts && !wantCustomers) return;
    this._live.pending = true;
    const seq = ++this._seq;
    this._timer = setTimeout(async () => {
      const ctl = typeof AbortController === 'function' ? new AbortController() : null;
      this._abort = ctl;
      const get = async (url) => {
        try {
          const r = await fetch(url, { credentials: 'include', signal: ctl ? ctl.signal : undefined });
          const j = await r.json();
          return j && j.status === 'success' && Array.isArray(j.data) ? j.data : [];
        } catch (e) { return []; }
      };
      const enc = encodeURIComponent(q);
      const [prods, custs] = await Promise.all([
        wantProducts ? get('/api/sub/retail/products?q=' + enc + '&limit=' + this.LIVE_PRODUCTS) : [],
        wantCustomers ? get('/api/sub/retail/customers?q=' + enc) : [],
      ]);
      if (seq !== this._seq || !this._overlay) return;              // a newer keystroke owns the list
      this._live = {
        products: prods.slice(0, this.LIVE_PRODUCTS).map((p) => this._finish(Object.assign({ keywords: [] }, this._productEntry(p)))),
        customers: custs.slice(0, this.LIVE_CUSTOMERS).map((c) => this._finish(Object.assign({ keywords: [] }, this._customerEntry(c)))),
        pending: false,
      };
      this._render();
    }, this.LIVE_DEBOUNCE_MS);
  },

  // ── open / close ──────────────────────────────────────────────────────────
  isOpen() { return !!this._overlay; },

  open() {
    const app = this._app();
    if (this._overlay) { this._input && this._input.focus(); return; }
    if (!app || !app.currentUser || !document.getElementById('aura-palette-trigger')) return;
    // The Theme picker is a modal panel with a click-away backdrop that is NOT
    // on the RetailSystem modal-key stack, so nothing else stops Ctrl+K from
    // stacking a search box on top of it (verifier F7): two overlays, one
    // Escape, and closing the palette would leave the picker half-open behind
    // it. Refuse instead -- the picker closes with its own backdrop/Esc.
    if (document.getElementById('theme-picker-panel')) return;
    // One overlay at a time: an open header dropdown or the phone More sheet
    // would sit half-hidden behind the palette and keep answering Escape.
    try {
      if (app._dropdownState) app._closeDropdown(app._dropdownState.btnId, app._dropdownState.menuId);
      if (typeof app._closeMoreSheet === 'function') app._closeMoreSheet();
    } catch (e) { /* cosmetic only */ }

    this._opener = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;
    this._buildIndex();
    this._live = { products: [], customers: [], pending: false };

    const overlay = document.createElement('div');
    overlay.className = 'aura-palette-overlay';
    overlay.innerHTML = `
      <div class="aura-palette" id="aura-palette-dialog" role="dialog" aria-modal="true" aria-label="${this._attr(t('Search or jump to…'))}">
        <div class="aura-palette-field">
          <span class="aura-palette-field-icon" aria-hidden="true">${this._icon('search', 18)}</span>
          <input type="text" id="aura-palette-input" class="aura-palette-input" role="combobox"
                 aria-expanded="true" aria-controls="aura-palette-list" aria-autocomplete="list"
                 autocomplete="off" autocapitalize="off" spellcheck="false"
                 placeholder="${this._attr(t('Search pages, settings, products, customers…'))}"
                 aria-label="${this._attr(t('Search or jump to…'))}">
          <button type="button" id="aura-palette-close" class="aura-palette-esc" data-no-i18n translate="no" aria-label="${this._attr(t('Close'))}">Esc</button>
        </div>
        <div class="aura-palette-list" id="aura-palette-list" role="listbox" aria-label="${this._attr(t('Results'))}"></div>
        <div class="aura-palette-foot" aria-hidden="true">
          <!-- Key names are the LABELS PRINTED ON THE KEYBOARD: never translated
               (the AR text-node sweep in i18n.js would otherwise rewrite
               "Enter"/"Esc"; data-no-i18n stops it, translate="no" stops the
               browser's own page translator). -->
          <span><kbd dir="ltr" data-no-i18n translate="no">↑</kbd><kbd dir="ltr" data-no-i18n translate="no">↓</kbd> ${t('to move')}</span>
          <span><kbd dir="ltr" data-no-i18n translate="no">Enter</kbd> ${t('to open')}</span>
          <span><kbd dir="ltr" data-no-i18n translate="no">Esc</kbd> ${t('to close')}</span>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    this._overlay = overlay;
    this._input = overlay.querySelector('#aura-palette-input');
    this._list = overlay.querySelector('#aura-palette-list');

    // Join the RetailSystem modal-key stack: while this layer is on top the
    // till's F-key handler bails (a keystroke meant for the search box must
    // never charge a sale) and other modals' Escape handlers stand down.
    if (window.RetailSystem && Array.isArray(RetailSystem._modalKeyStack)) {
      this._layer = { overlay };
      RetailSystem._modalKeyStack.push(this._layer);
    }

    overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) this.close({ restoreFocus: true }); });
    overlay.querySelector('#aura-palette-close').addEventListener('click', () => this.close({ restoreFocus: true }));
    this._input.addEventListener('input', () => {
      this._active = 0;
      this._scheduleLive(this._input.value);
      this._render();
    });
    overlay.addEventListener('keydown', (e) => this._onKeydown(e));
    this._list.addEventListener('mousemove', (e) => {
      const row = e.target.closest && e.target.closest('.aura-palette-row');
      if (row && Number(row.dataset.index) !== this._active) this._setActive(Number(row.dataset.index), false);
    });
    this._list.addEventListener('click', (e) => {
      const row = e.target.closest && e.target.closest('.aura-palette-row');
      if (row) this._choose(Number(row.dataset.index));
    });

    this._active = 0;
    this._render();
    this._input.focus();
  },

  close(opts) {
    const restore = !opts || opts.restoreFocus !== false;
    clearTimeout(this._timer);
    if (this._abort) { try { this._abort.abort(); } catch (e) { /* already done */ } this._abort = null; }
    this._seq++;
    if (this._layer && window.RetailSystem && typeof RetailSystem._dropModalKeyLayer === 'function') {
      RetailSystem._dropModalKeyLayer(this._layer);
    }
    this._layer = null;
    if (this._overlay) this._overlay.remove();
    this._overlay = null;
    this._input = null;
    this._list = null;
    this._rows = [];
    const opener = this._opener;
    this._opener = null;
    if (restore) {
      if (opener && opener.isConnected && typeof opener.focus === 'function') opener.focus();
      else this._focusContent();
    }
  },

  _focusContent() {
    const c = document.getElementById('sub-content');
    if (!c) return;
    if (!c.hasAttribute('tabindex')) c.setAttribute('tabindex', '-1');
    c.focus({ preventScroll: true });
  },

  // ── rendering ─────────────────────────────────────────────────────────────
  _attr(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  },
  _esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  },

  KIND_ORDER: ['action', 'page', 'setting', 'help', 'product', 'customer'],
  GROUP_LABEL: { action: 'Actions', page: 'Go to', setting: 'Settings', help: 'Help', product: 'Products', customer: 'Customers' },
  KIND_HINT: { action: 'Action', page: 'Page', setting: 'Setting', help: 'Help', product: 'Product', customer: 'Customer' },

  // The rows for the current query, already grouped: [{heading, entries}].
  _groups() {
    const q = this._input ? this._input.value : '';
    if (!this._norm(q)) {
      const groups = [];
      const recent = this._recentEntries();
      if (recent.length) groups.push({ heading: 'Recent', entries: recent });
      const recentKeys = new Set(recent.map((e) => e.key));
      this.KIND_ORDER.forEach((kind) => {
        if (kind === 'product' || kind === 'customer' || kind === 'page') return; // "Go to" is listed below, in nav order
        const list = this._staticEntries.filter((e) => e.kind === kind && !recentKeys.has(e.key));
        if (list.length) groups.push({ heading: this.GROUP_LABEL[kind], entries: list });
      });
      const pages = this._staticEntries.filter((e) => e.kind === 'page' && !recentKeys.has(e.key));
      if (pages.length) groups.push({ heading: this.GROUP_LABEL.page, entries: pages });
      return groups;
    }
    const scored = [];
    this._staticEntries.concat(this._live.products, this._live.customers).forEach((e) => {
      const s = this._score(e, q);
      if (s !== null) scored.push({ e, s });
    });
    const groups = [];
    this.KIND_ORDER.forEach((kind) => {
      const list = scored.filter((x) => x.e.kind === kind).sort((a, b) => a.s - b.s).map((x) => x.e);
      if (list.length) groups.push({ heading: this.GROUP_LABEL[kind], entries: list });
    });
    // The best match overall goes first so Enter does what the eye expects:
    // reorder the groups so the group holding the lowest score leads.
    const best = (g) => Math.min(...g.entries.map((e) => this._score(e, q)));
    groups.sort((a, b) => best(a) - best(b));
    return groups;
  },

  _render() {
    if (!this._list) return;
    const groups = this._groups();
    const flat = [];
    let html = '';
    groups.forEach((g) => {
      html += `<div class="aura-palette-group" role="presentation">${this._esc(t(g.heading))}</div>`;
      g.entries.forEach((e) => {
        const i = flat.length;
        flat.push(e);
        const icon = e.iconName ? this._icon(e.iconName, 16) : (window.AuraIcons ? AuraIcons.render(e.icon || '•', 16) : this._esc(e.icon || ''));
        html += `<div class="aura-palette-row" role="option" id="aura-palette-opt-${i}" data-index="${i}" data-kind="${e.kind}" data-key="${this._attr(e.key)}" aria-selected="${i === this._active ? 'true' : 'false'}">
          <span class="aura-palette-row-icon" aria-hidden="true">${icon}</span>
          <span class="aura-palette-row-label">${this._esc(e.label)}${e.sub ? ` <span class="aura-palette-row-sub" dir="auto">${this._esc(e.sub)}</span>` : ''}</span>
          <span class="aura-palette-row-hint">${this._esc(t(this.KIND_HINT[e.kind] || ''))}</span>
        </div>`;
      });
    });
    if (!flat.length) {
      const q = this._input ? this._input.value.trim() : '';
      const msg = this._live.pending ? t('Searching…') : (q ? t('Nothing found') : '');
      html = msg ? `<div class="aura-palette-empty" role="status">${this._esc(msg)}</div>` : '';
    } else if (this._live.pending) {
      html += `<div class="aura-palette-empty" role="status">${this._esc(t('Searching…'))}</div>`;
    }
    this._rows = flat;
    if (this._active >= flat.length) this._active = Math.max(0, flat.length - 1);
    this._list.innerHTML = html;
    this._syncActive(false);
  },

  _setActive(i, scroll) {
    if (!this._rows.length) return;
    this._active = (i + this._rows.length) % this._rows.length;
    this._syncActive(scroll !== false);
  },

  _syncActive(scroll) {
    if (!this._list || !this._input) return;
    this._list.querySelectorAll('.aura-palette-row').forEach((row) => {
      row.setAttribute('aria-selected', Number(row.dataset.index) === this._active ? 'true' : 'false');
    });
    const cur = this._list.querySelector(`#aura-palette-opt-${this._active}`);
    if (cur) {
      this._input.setAttribute('aria-activedescendant', cur.id);
      if (scroll && typeof cur.scrollIntoView === 'function') cur.scrollIntoView({ block: 'nearest' });
    } else {
      this._input.removeAttribute('aria-activedescendant');
    }
  },

  // ── keys ──────────────────────────────────────────────────────────────────
  _onKeydown(e) {
    // Mid-composition keystrokes belong to the IME (Arabic/CJK keyboards
    // confirm a candidate with Enter); acting on them would fire a row while
    // the person is still typing the word.
    if (e.isComposing || e.keyCode === 229) return;
    // MONEY BUG (verifier F1): every key the palette handles must STOP HERE.
    // _choose() closes the palette (dropping its modal layer) and moves focus
    // to a plain <div> while this very keydown is still travelling up. The
    // till's document-level Enter handler (RetailSystem._onPOSShortcut) then
    // sees "no modal open, no text field focused, cart not empty" and CHARGES
    // THE SALE that was sitting in the cart -- measured: sales 0 -> 1 -> 2 -> 3
    // from three "go to Products" Enters. The till's own guard cannot tell
    // "closed a moment ago by this same keypress" from "closed long ago", so
    // the only safe fix is to never let the event leave the palette. Same for
    // arrows / Tab / Esc: they must not scroll the page or move till state
    // either. stopImmediatePropagation so a second listener on the same node
    // cannot see it either.
    const handled = e.key === 'Escape' || e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === 'Tab';
    if (handled) { e.stopPropagation(); if (typeof e.stopImmediatePropagation === 'function') e.stopImmediatePropagation(); }
    if (e.key === 'Escape') { e.preventDefault(); this.close({ restoreFocus: true }); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); this._setActive(this._active + 1); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); this._setActive(this._active - 1); return; }
    if (e.key === 'Enter') { e.preventDefault(); this._choose(this._active); return; }
    if (e.key === 'Tab') {
      // Focus trap: the dialog has exactly two tab stops, the field and Close.
      const btn = this._overlay.querySelector('#aura-palette-close');
      const onInput = document.activeElement === this._input;
      e.preventDefault();
      (onInput ? btn : this._input).focus();
    }
  },

  _choose(i) {
    const entry = this._rows[i];
    if (!entry) return;
    this._remember(entry);
    // Close WITHOUT restoring focus to the opener: the destination screen is
    // about to repaint, and yanking focus back to a header button would leave
    // the person on a control that has nothing to do with where they went.
    this.close({ restoreFocus: false });
    this._focusContent();
    Promise.resolve()
      .then(() => entry.run())
      .catch((err) => {
        console.error('palette action failed', err);
        const app = this._app();
        if (app && typeof app.showToast === 'function') app.showToast(t('Could not reach the server. Please try again.'), 'error');
      });
  },
};

window.AuraPalette = AuraPalette;

// Ctrl+K / Cmd+K, anywhere in the app. Capture phase, so it wins over a
// focused input's own handling (browsers bind Ctrl+K to their search bar).
document.addEventListener('keydown', (e) => {
  if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
  if (String(e.key).toLowerCase() !== 'k') return;
  if (AuraPalette.isOpen()) { e.preventDefault(); e.stopPropagation(); AuraPalette.close({ restoreFocus: true }); return; }
  const app = window.SubsystemApp;
  if (!app || !app.currentUser || !document.getElementById('aura-palette-trigger')) return;
  // Another modal (payment, receipt, a confirm) owns the keyboard: do not
  // stack a search box on top of a half-finished sale.
  if (window.RetailSystem && typeof RetailSystem._topModalKeyLayer === 'function' && RetailSystem._topModalKeyLayer()) return;
  e.preventDefault();
  e.stopPropagation();
  AuraPalette.open();
}, true);
