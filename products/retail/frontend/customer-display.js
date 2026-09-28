/* customer-display.js — the customer-facing second screen (retail-hardware-viewports).
 *
 * A shopper-readable window: line items as they are scanned, subtotal /
 * promotions / discount / tax, a large TOTAL, and a brief paid/change screen
 * once a sale completes. Standalone on purpose -- this document never loads
 * subsystem-retail.js (574KB, and it wires up the scanner/keyboard/session
 * behaviour that has no meaning on a screen a customer only reads and never
 * types into).
 *
 * TWO delivery paths feed the SAME state machine (#11/I-1), because neither
 * one alone reaches every place this screen can run:
 *   - A same-origin BroadcastChannel (see RetailSystem._openCustomerDisplay/
 *     _broadcastDisplayState in subsystem-retail.js) -- the FAST path, but
 *     same-process/same-script-realm only, so it cannot cross into a
 *     genuinely separate window (a second native pywebview window, C7).
 *   - A short poll of GET /api/sub/retail/customer-display/state?since=seq
 *     (C2), every ~600ms, deduped by seq -- the path that DOES cross that
 *     boundary, and the one path this file may actually depend on; the
 *     channel above is a latency optimisation on top of it, never a
 *     requirement. Branding (shop name/logo) is still a one-time GET on
 *     load, separate from both -- it changes once a quarter, not once a
 *     scan.
 *
 * MONEY: every figure rendered here is a STRING the till already formatted
 * with its own _fmt()/_moneyDigits() (see subsystem-retail.js's money-helpers
 * comment block). This file never parses, sums, rounds or re-formats a
 * currency figure -- it only ever inserts the exact text the till sent, so a
 * shop running 3-decimal JOD (or any other currency) can never show the
 * customer a number that disagrees with what is about to be charged.
 *
 * FALLBACK: if BroadcastChannel does not exist, or throws on construction,
 * this file MUST NOT throw. It stays on the idle/welcome screen, which is the
 * documented degraded behaviour, not an error.
 */
(function (global) {
  'use strict';

  var CHANNEL_NAME = 'aura_retail_customer_display';
  // Matches _showReceipt()'s own auto-dismiss timing in subsystem-retail.js,
  // so the cashier's own receipt modal and the customer's "paid/change"
  // screen clear at the same moment rather than one outliving the other.
  var SALE_COMPLETE_DISPLAY_MS = 8000;
  // How often this window re-announces itself while waiting for the till to
  // answer. Covers both orderings: the till may already be running (and its
  // BroadcastChannel already live) before this window ever opens, or this
  // window may reload independently (e.g. an accidental refresh) while the
  // till keeps its own channel open across POS re-renders.
  var READY_RETRY_MS = 2000;
  // #11/I-1 -- the short-poll interval for the backend relay. NEVER a
  // long-poll: the desktop backend runs on waitress with only 12 worker
  // threads, and a request held open for seconds at a time would starve the
  // rest of the till's own traffic on the same process. 600ms sits in the
  // middle of I-1's specified 500-700ms band. BroadcastChannel above stays
  // the FAST path (same-process, sub-poll-interval delivery on the same
  // computer), but this poll is the path that also reaches a genuinely
  // separate window/profile (a second native pywebview window, C7) that
  // BroadcastChannel cannot cross -- so nothing here may depend on the
  // channel ever having fired.
  var POLL_INTERVAL_MS = 600;
  var THEME_NAMES = ['light', 'sand', 'dark', 'night', 'dusk']; // same allowlist as the boot-theme script above and main.css's design tokens

  // t() is the SAME global i18n.js exposes on every other screen (see
  // window.t in i18n.js) -- reused here, not reimplemented, so a translation
  // added once covers this screen too. Falls back to identity if i18n.js
  // failed to load (offline-safe, matches i18n.js's own AuraI18n.t() default
  // for an unknown key), so this file never throws for want of a dictionary.
  var t = (typeof global.t === 'function') ? global.t : function (s) { return s; };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function byId(id) {
    return global.document ? global.document.getElementById(id) : null;
  }

  function setText(id, value) {
    var el = byId(id);
    if (el) el.textContent = value == null ? '' : value;
  }

  var CustomerDisplay = {
    _channel: null,
    _completeTimer: null,
    _readyInterval: null,
    _connectedOnce: false,
    _pollTimer: null,
    _lastSeq: 0,
    _shopNameKnown: false,

    init: function () {
      this._clearCompleteTimer();
      // #12 -- paint the neutral fallback BEFORE either async source (the
      // branding fetch, the relay poll) can answer, so the very first frame
      // this screen ever shows is never the empty <h1> the markup now
      // ships with, and never the old hardcoded vendor name either.
      this._applyShopName(null);
      this._renderIdle();   // _renderIdle() itself calls _loadPromotion() -- see F7's comment there
      this._loadBranding();
      this._connectChannel();
      this._startPolling();
    },

    // ── Branding (idle screen only) ───────────────────────────────────────────
    // Best-effort: any failure here (offline, a pre-activation licence state,
    // a build that predates this route) leaves whatever _applyShopName(null)
    // already painted -- never a broken screen. This is the ONE thing on
    // this page that is fetched rather than pushed over the channel/relay,
    // because it changes once a quarter, not once a scan, and the till's own
    // settings screen is the place that edits it. branding_settings_get
    // itself carries the #12/I-3 read-time signup-name fallback server-side,
    // so this call needs no fallback logic of its own beyond "no name yet".
    _loadBranding: function () {
      if (typeof global.fetch !== 'function') return;
      var self = this;
      global.fetch('/api/sub/retail/settings/branding', { credentials: 'include', cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (resp) {
          var b = (resp && resp.status === 'success' && resp.data) ? resp.data : {};
          // Review fix (2026-09-27, #12-display, high): this used to read
          // ONLY b.branding_business_name, the RAW stored value, which stays
          // '' on every fresh signup-only install -- so this screen showed
          // the neutral 'Welcome / أهلاً وسهلاً' fallback (never the vendor
          // name, per #12's own fix, but also never the shop's OWN name)
          // until a till posted a cart over the #11 relay, because ONLY the
          // relay's shop.name (SubsystemApp._shopDisplayName(), app-shell.js)
          // ever read the I-3 fallback. Worse: on a restricted/expired
          // licence the relay POST is refused (require_license_capability,
          // not on RETAIL_RESTRICTED_ALLOWLIST), so this screen never showed
          // the shop's name AT ALL in that state. branding_settings_get
          // ALREADY computes the same fallback server-side and returns it as
          // `business_name` (empty branding -> the signup Company Name, read
          // straight from config.json -- see that route's own comment); this
          // is the exact `b.business_name || b.branding_business_name` chain
          // app-shell.js's OWN _shopDisplayName() already uses for the
          // sidebar/title, so the two can no longer disagree about what this
          // shop is called. Mutation: revert to `b.branding_business_name`
          // alone and testRealShopNameFromBrandingLatchesOverLaterNull (the
          // real server response shape) goes red.
          self._applyShopName(b.business_name || b.branding_business_name);
          // P1a -- the WhatsApp QR rides this SAME one-time branding fetch
          // (server-side rendered by _shop_whatsapp_qr_data_uri in
          // retail_api.py -- see that function's own comment for why it is
          // exposed here rather than a new endpoint or poll). Applied
          // unconditionally: null/'' -> _applyWhatsappQr hides the row, so a
          // shop with no number configured shows no QR and no empty box.
          self._applyWhatsappQr(b.whatsapp_qr_svg_data_uri);
          if (b.has_logo) return self._loadLogo();
        })
        .catch(function () { /* generic fallback already on screen */ });
    },

    // ── Today's promotion (idle screen only) ─────────────────────────────────
    // Fix round F7 (verifier finding: "the idle-screen promotion can go
    // stale"): re-fetched every time the screen TRANSITIONS back to idle
    // (see _renderIdle below), not on a fresh page load alone and NOT on a
    // repeating timer (contract still holds: "no new polling loop" -- a
    // sale completing or a cart clearing is a real event this file already
    // reacts to, not a new poll). GET /promotions/active is the SAME
    // till-facing read the POS itself calls before every sale (see
    // subsystem-retail.js's _loadPOSData), reused rather than duplicated.
    // "This till's promotions until W2b" (p1a-plan.json) -- a branch-aware,
    // continuously-refreshed promotion feed is explicitly out of scope here.
    _loadPromotion: function () {
      if (typeof global.fetch !== 'function') return;
      var self = this;
      global.fetch('/api/sub/retail/promotions/active', { credentials: 'include', cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (resp) {
          var rows = (resp && resp.status === 'success' && Array.isArray(resp.data)) ? resp.data : [];
          // F7 follow-up (coordinator instruction, P1a fix round): showing
          // NOTHING because every real promotion is scoped is honest but
          // defeats the feature -- so this now shows the current promotion
          // WITH its scope (the category or product it applies to) instead
          // of hiding it. Only rows whose scope NAME actually resolved
          // (retail_api.py's list_active_promotions now enriches each row
          // with category_name/product_name) and whose ends_at has not
          // passed are eligible -- see _promotionIsCurrent and
          // _promotionScopeName below; a row with an unresolved scope name
          // is never shown and never implied shop-wide.
          //
          // DETERMINISTIC PICK when several are active: highest
          // discount_pct wins (the offer most worth a shopper's attention),
          // ties broken by the LOWEST id (the promotion created first) --
          // stated here because the coordinator asked which rule this uses.
          var eligible = rows.filter(function (p) {
            return self._promotionIsCurrent(p) && !!self._promotionScopeName(p);
          });
          eligible.sort(function (a, b) {
            if (b.discount_pct !== a.discount_pct) return b.discount_pct - a.discount_pct;
            return a.id - b.id;
          });
          self._applyPromotion(eligible.length ? eligible[0] : null);
        })
        .catch(function () { /* offline, or no promotions configured -- the row stays hidden */ });
    },

    // Fix round F7 -- re-checks `ends_at` client-side: /promotions/active
    // already filters server-side by `now_local` (core/retail/
    // promotions.py's own SQL, `ends_at>=?`), but this screen only
    // re-fetches on an idle TRANSITION, not a repeating timer, so a
    // promotion already on screen could in principle outlive its own
    // ends_at while nothing re-triggers a check -- this re-validates the
    // SAME row the moment it is (re-)applied. Scope (product/category) is
    // resolved separately, in _promotionScopeName -- kept apart from this
    // function because "is the row still live" and "can we name what it
    // applies to" are independent questions the caller must both answer
    // yes to before showing anything (see _loadPromotion's `eligible` filter).
    _promotionIsCurrent: function (promo) {
      if (!promo) return false;
      if (promo.ends_at) {
        // Same sortable 'YYYY-MM-DD HH:MM:SS' shape and the same plain
        // string comparison core/retail/promotions.py's own SQL uses --
        // never parsed into a Date, matching this file's "never invent a
        // clock/format the server did not give it" discipline (see the
        // module comment's MONEY section for the same rule applied to
        // figures instead of timestamps).
        if (String(promo.ends_at) < this._nowLocalSortable()) return false;
      }
      return true;
    },

    // Fix round F7 follow-up -- "show the current promotion WITH its
    // scope... a promotion whose name can't be resolved is not shown.
    // Never claim it applies shop-wide." `category_name`/`product_name` are
    // added server-side by retail_api.py's list_active_promotions
    // (company-scoped lookups keyed off this SAME row's product_id/
    // category_id -- see that route's own comment for why it is not a
    // second network call). core/retail/promotions.py's create_promotion
    // enforces "exactly one of product_id/category_id", so exactly one of
    // these two branches applies to any real row; returns null (never an
    // empty string, which `!!` would still treat as falsy but is worth
    // being explicit about) when neither name resolved -- a category or
    // product that was deleted after the promotion was created, for
    // instance -- and the caller's contract is to treat null as "do not
    // show this promotion", never to fall back to a bare, unscoped claim.
    _promotionScopeName: function (promo) {
      if (!promo) return null;
      if (promo.category_id != null) return promo.category_name || null;
      if (promo.product_id != null) return promo.product_name || null;
      return null;
    },

    _nowLocalSortable: function () {
      var d = new Date();
      var p = function (n) { return String(n).padStart(2, '0'); };
      return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
        ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
    },

    _applyPromotion: function (promo) {
      var el = byId('cd-promo');
      if (!el) return;
      var scopeName = this._promotionScopeName(promo);
      if (!promo || !this._promotionIsCurrent(promo) || !scopeName) { el.hidden = true; return; }
      // discount_pct is a plain DB value (core/retail/promotions.py), never
      // a pre-formatted string the way till-broadcast money figures are --
      // there is nothing to parse here, this route was never asked to
      // compute a price. `scopeName` is shop-authored data (a category or
      // product name the owner typed) exactly like `promo.name` used to be
      // here, so it is never routed through t() either -- only the fixed
      // English phrase around the two values is.
      var pct = (promo.discount_pct == null) ? '' : String(promo.discount_pct);
      // Coordinator's own example shape: t('{pct}% off {category}').
      // Placeholder substitution, not a templating ENGINE (no loops, no
      // conditionals, exactly two named slots) -- matches this file's "no
      // templating engine dependency" convention while still letting the
      // ARABIC translation reorder the two values, which naively
      // translating the word "off" and concatenating could not do (Arabic
      // puts the preposition before the object: "خصم 20% على Snacks", not
      // "20% خصم Snacks").
      el.textContent = t('{pct}% off {category}')
        .split('{pct}').join(pct)
        .split('{category}').join(scopeName);
      el.hidden = false;
    },

    // ── Shop WhatsApp QR (idle screen only) ───────────────────────────────────
    _applyWhatsappQr: function (dataUri) {
      var wrap = byId('cd-whatsapp');
      var img = byId('cd-whatsapp-qr');
      if (!wrap || !img) return;
      if (!dataUri) { wrap.hidden = true; img.src = ''; return; }
      img.src = dataUri;
      img.alt = t('Chat with us on WhatsApp');
      setText('cd-whatsapp-label', t('Chat with us on WhatsApp'));
      wrap.hidden = false;
    },

    // ── Shop identity (#12) ───────────────────────────────────────────────────
    // Single choke point for the idle-screen shop name, fed by TWO
    // independent sources that can arrive in either order: the one-time
    // branding fetch above (I-3) and the relay's per-message `shop` object
    // (I-1/#11, _applyShopMeta below). Once EITHER has supplied a real name,
    // a later "no name" answer from the other source must not blank it back
    // out -- _shopNameKnown latches the first real name this page has seen.
    _applyShopName: function (name) {
      if (name) {
        setText('cd-shop-name', name);
        this._shopNameKnown = true;
        if (global.document) global.document.title = name + ' — ' + t('Customer Display');
      } else if (!this._shopNameKnown) {
        // AUDIT #12 -- the OLD default here was the hardcoded HTML text
        // 'Aura Retail', the VENDOR's own name, shown on a screen a
        // shopper reads as THIS shop's own signage until the owner found
        // Settings and typed a name. Never acceptable as a fallback.
        //
        // Deliberately NOT run through t(): this line has to read correctly
        // regardless of which language the shop's own display preference
        // turns out to be, precisely because -- with no shop name known
        // yet -- that preference (relay `shop.lang`) may not have arrived
        // either. Per-language translation would collapse it to one
        // language and lose that property; registering it as a catalogue
        // key would also fail testEveryNewLocaleKeyExistsInBothCatalogs's
        // "ar must differ from en" check, since both languages are present
        // here on purpose, not by an untranslated omission.
        setText('cd-shop-name', 'Welcome / أهلاً وسهلاً');
      }
    },

    _loadLogo: function () {
      return global.fetch('/api/sub/retail/settings/branding/logo', { credentials: 'include', cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (resp) {
          var uri = resp && resp.data && resp.data.logo;
          var img = byId('cd-logo');
          if (uri && img) { img.src = uri; img.hidden = false; }
        })
        .catch(function () { /* no logo configured, or the read failed -- name-only idle screen stands */ });
    },

    // ── Channel ────────────────────────────────────────────────────────────────
    _connectChannel: function () {
      var BC = global.BroadcastChannel;
      var ch = null;
      if (typeof BC === 'function') {
        try { ch = new BC(CHANNEL_NAME); } catch (e) { ch = null; }
      }
      if (!ch) return;   // documented fallback: BroadcastChannel unavailable, stay on the idle screen
      this._channel = ch;
      var self = this;
      ch.onmessage = function (ev) { self._onMessage(ev && ev.data); };
      var announce = function () {
        try { ch.postMessage({ type: 'display_ready' }); } catch (e) { /* channel closed mid-flight -- next retry covers it */ }
      };
      announce();
      this._readyInterval = global.setInterval(function () {
        if (!self._connectedOnce) announce();
      }, READY_RETRY_MS);
    },

    // ── Relay poll (#11 / I-1) ───────────────────────────────────────────────
    // The one path guaranteed to reach this screen regardless of which window
    // or process it runs in -- see POLL_INTERVAL_MS's own comment. Feeds the
    // SAME _onMessage() the BroadcastChannel path already uses, so every
    // rendering rule (cart, sale_complete, the #9 CSS fix, all of it) is
    // exercised exactly once, not duplicated for a second delivery mechanism.
    _startPolling: function () {
      if (typeof global.fetch !== 'function') return;   // same documented fallback as branding -- no fetch, whatever BroadcastChannel already delivered stands
      var self = this;
      // An immediate first tick, not just the interval below -- a display
      // opened mid-sale (or reloaded) must not sit on the idle screen for up
      // to POLL_INTERVAL_MS before it learns the till already has a cart in
      // progress. Same "sync immediately, then keep current" shape
      // _connectChannel()'s own `announce()` uses for BroadcastChannel.
      this._pollOnce();
      this._pollTimer = global.setInterval(function () { self._pollOnce(); }, POLL_INTERVAL_MS);
    },

    // A single poll cycle, exposed as its own method (rather than reachable
    // only through the interval) so a test can drive it directly against a
    // fake fetch without needing to fast-forward a fake timer repeatedly --
    // see retail_customer_display_test.js.
    _pollOnce: function () {
      var self = this;
      return global.fetch(
        '/api/sub/retail/customer-display/state?since=' + this._lastSeq,
        { credentials: 'include', cache: 'no-store' }
      )
        .then(function (r) {
          if (r.status === 204) return null;   // no newer message than `since` -- nothing to apply this tick
          if (!r.ok) return null;              // 401 (a genuinely separate, cookie-less window/profile -- the deny half), 404 (the relay route is not deployed on this build yet), 5xx -- none of these may throw
          return r.json();
        })
        .then(function (data) {
          if (!data || typeof data.seq !== 'number') return;
          // Dedupe by seq (#11): a short poll re-asks the SAME question every
          // ~600ms until something newer exists, so without this guard every
          // idle tick would re-render whatever is already on screen -- and,
          // worse, a stale response arriving after a newer one (out-of-order
          // completion of two overlapping fetches) would paint backwards.
          if (data.seq <= self._lastSeq) return;
          self._lastSeq = data.seq;
          if (data.shop) self._applyShopMeta(data.shop);
          if (data.message) self._onMessage(data.message);
        })
        .catch(function () { /* network hiccup, or C2's route not landed yet on this build -- the next tick tries again */ });
    },

    // shop.name/lang/theme ride the relay payload itself (I-1) -- this file
    // must NOT re-fetch branding on every poll tick just to learn the name
    // again; that is exactly the "fetched once a quarter, not once a scan"
    // rule _loadBranding's own comment already states, and doing it here too
    // would turn a 600ms poll into a 600ms branding re-fetch loop.
    _applyShopMeta: function (shop) {
      if (!shop) return;
      this._applyShopName(shop.name);
      var doc = global.document;
      var root = doc && doc.documentElement;
      if (!root || typeof root.setAttribute !== 'function') return;
      if (shop.theme && THEME_NAMES.indexOf(shop.theme) !== -1) root.setAttribute('data-theme', shop.theme);
      if (shop.lang === 'ar' || shop.lang === 'en') {
        root.setAttribute('lang', shop.lang);
        root.setAttribute('dir', shop.lang === 'ar' ? 'rtl' : 'ltr');
      }
    },

    _onMessage: function (msg) {
      if (!msg || !msg.type) return;
      this._connectedOnce = true;
      if (msg.type === 'cart') this._onCart(msg);
      else if (msg.type === 'sale_complete') this._onSaleComplete(msg);
      // Any other type (e.g. a future message this build predates) is
      // ignored rather than treated as an error -- forward compatibility for
      // a screen that is far more awkward to redeploy mid-shift than the
      // till it is paired with.
    },

    _onCart: function (msg) {
      var items = Array.isArray(msg.items) ? msg.items : [];
      // A non-empty cart always wins immediately -- the cashier already rang
      // the next customer's sale. An EMPTY cart arriving while the "thank
      // you" screen is still up is _clearCart()'s own broadcast from the
      // sale that JUST completed (subsystem-retail.js resets the cart before
      // showing the receipt) -- that is not a second event, so it must not
      // cut the thank-you screen short; the pending timer finishes it.
      if (this._completeTimer && items.length === 0) return;
      this._clearCompleteTimer();
      if (items.length === 0) { this._renderIdle(); return; }
      this._renderCart(msg, items);
    },

    _onSaleComplete: function (msg) {
      this._clearCompleteTimer();
      this._renderComplete(msg);
      var self = this;
      this._completeTimer = global.setTimeout(function () {
        self._completeTimer = null;
        self._renderIdle();
      }, SALE_COMPLETE_DISPLAY_MS);
    },

    _clearCompleteTimer: function () {
      if (this._completeTimer) { global.clearTimeout(this._completeTimer); this._completeTimer = null; }
    },

    // ── Rendering ──────────────────────────────────────────────────────────────
    _show: function (name) {
      var sections = { idle: 'cd-idle', active: 'cd-active', complete: 'cd-complete' };
      Object.keys(sections).forEach(function (key) {
        var el = byId(sections[key]);
        if (el) el.hidden = key !== name;
      });
    },

    // Fix round F7: re-fetches the promotion on EVERY transition back to
    // idle (init, an emptied cart, and the end of the sale-complete timer --
    // every call site of _renderIdle() in this file), not just once at page
    // load, and never on a new timer -- see _loadPromotion's own comment.
    _renderIdle: function () {
      setText('cd-welcome', t('Welcome!'));
      this._loadPromotion();
      this._show('idle');
    },

    // `msg` fields (subtotal/tax/discount/total/items[].unitPrice/lineTotal)
    // are ALREADY-FORMATTED STRINGS from RetailSystem._fmt()/_moneyDigits() --
    // see the module comment. Never parsed, never re-formatted here: they are
    // inserted as text exactly as received.
    _renderCart: function (msg, items) {
      var list = byId('cd-items');
      if (list) {
        list.innerHTML = items.map(function (it) {
          var qty = (it.quantity == null) ? '' : String(it.quantity);
          // #8/I-2 -- items[].originalLineTotal is the formatted PRE-promotion
          // line total, or null when no promotion applied to that line (till
          // side, C3b). Struck through, ahead of the post-promotion figure,
          // so the line that actually earned a saving shows it in place
          // rather than only as a lump sum in the summary below.
          var originalHtml = it.originalLineTotal
            ? '<span class="cd-item-original">' + esc(it.originalLineTotal) + '</span>'
            : '';
          return '<div class="cd-item-row">' +
            '<div class="cd-item-main">' +
              '<div class="cd-item-name">' + esc(it.name) + '</div>' +
              '<div class="cd-item-meta">' + esc(it.unitPrice || '') + ' × ' + esc(qty) + '</div>' +
            '</div>' +
            '<div class="cd-item-total money">' + originalHtml + esc(it.lineTotal || '') + '</div>' +
          '</div>';
        }).join('');
      }
      setText('cd-subtotal-label', t('Subtotal'));
      setText('cd-subtotal-value', msg.subtotal);
      // #8/I-2 -- total PROMOTION saving (rules-based, e.g. buy-X-get-Y),
      // distinct from the MANUAL discount row below it: the two used to be
      // folded into one figure, which is exactly what made 0.450 of a
      // 12.300 subtotal go unexplained on screen (see the item text).
      // Hidden whenever the till sends no promotions field (null/below half
      // a minor unit -- the till's own job, not this file's, per the
      // "never parse money here" rule).
      var promoRow = byId('cd-promotions-row');
      if (promoRow) {
        if (msg.promotions) {
          promoRow.hidden = false;
          setText('cd-promotions-label', t('Promotions'));
          setText('cd-promotions-value', '-' + msg.promotions);
        } else {
          promoRow.hidden = true;
        }
      }
      setText('cd-tax-label', t('Tax'));
      setText('cd-tax-value', msg.tax);
      var discRow = byId('cd-discount-row');
      if (discRow) {
        if (msg.discount) {
          discRow.hidden = false;
          setText('cd-discount-label', t('Discount'));
          setText('cd-discount-value', '-' + msg.discount);
        } else {
          discRow.hidden = true;
        }
      }
      setText('cd-total-label', t('Total'));
      setText('cd-total-value', msg.total);
      // Points are a TENDER against the total, not a reduction of it (see the
      // markup comment and create_sale's own "THE CASH TRAP" heading), so the
      // row sits below the total band and carries a minus sign the way the
      // discount row does. 'Points Redeemed' is the SAME catalogue key the
      // till's own summary row uses -- one wording for one concept, rather
      // than a second phrase for the customer to reconcile against the
      // receipt.
      var loyaltyRow = byId('cd-loyalty-row');
      if (loyaltyRow) {
        if (msg.loyalty) {
          loyaltyRow.hidden = false;
          setText('cd-loyalty-label', t('Points Redeemed'));
          setText('cd-loyalty-value', '-' + msg.loyalty);
        } else {
          loyaltyRow.hidden = true;
        }
      }
      // Present only when the till says it differs from the total. This file
      // deliberately does NOT work that out for itself: `msg.total` and
      // `msg.amountDue` are formatted STRINGS, and comparing or subtracting
      // them would mean parsing money here -- the one thing this display is
      // built never to do.
      var dueBand = byId('cd-due-band');
      if (dueBand) {
        if (msg.amountDue) {
          dueBand.hidden = false;
          setText('cd-due-label', t('Amount Due'));
          setText('cd-due-value', msg.amountDue);
        } else {
          dueBand.hidden = true;
        }
      }
      this._show('active');
    },

    _renderComplete: function (msg) {
      setText('cd-complete-heading', t('Sale Complete!'));
      setText('cd-paid-label', t('Paid'));
      setText('cd-paid-value', msg.amountPaid);
      var changeRow = byId('cd-change-row');
      if (changeRow) {
        if (msg.change) {
          changeRow.hidden = false;
          setText('cd-change-label', t('Change Due'));
          setText('cd-change-value', msg.change);
        } else {
          changeRow.hidden = true;
        }
      }
      setText('cd-complete-thanks', t('Thank you'));
      this._show('complete');
    },
  };

  global.CustomerDisplay = CustomerDisplay;

  function boot() {
    var run = function () { CustomerDisplay.init(); };
    if (global.AuraI18n && typeof global.AuraI18n.load === 'function') {
      // AuraI18n.load() never rejects (i18n.js catches its own fetch
      // failures and falls back to empty dictionaries), but a fallback
      // path is still passed here rather than assumed, matching how this
      // whole file treats every other browser API as fallible.
      global.AuraI18n.load().then(run, run);
    } else {
      run();
    }
  }

  if (global.document) {
    if (global.document.readyState === 'loading') {
      global.document.addEventListener('DOMContentLoaded', boot);
    } else {
      boot();
    }
  }
})(typeof window !== 'undefined' ? window : this);
