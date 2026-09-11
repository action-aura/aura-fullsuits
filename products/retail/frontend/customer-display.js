/* customer-display.js — the customer-facing second screen (retail-hardware-viewports).
 *
 * A shopper-readable window: line items as they are scanned, subtotal /
 * discount / tax, a large TOTAL, and a brief paid/change screen once a sale
 * completes. Standalone on purpose -- this document never loads
 * subsystem-retail.js (574KB, and it wires up the scanner/keyboard/session
 * behaviour that has no meaning on a screen a customer only reads and never
 * types into). It talks to the till ONLY over a same-origin BroadcastChannel
 * (see RetailSystem._openCustomerDisplay/_broadcastDisplayState in
 * subsystem-retail.js) and needs no backend route of its own: branding is
 * read from the existing GET /api/sub/retail/settings/branding(/logo) routes
 * app-shell.js and the receipt printer already call.
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

    init: function () {
      this._clearCompleteTimer();
      this._renderIdle();
      this._loadBranding();
      this._connectChannel();
    },

    // ── Branding (idle screen only) ───────────────────────────────────────────
    // Best-effort: any failure here (offline, a pre-activation licence state,
    // a build that predates this route) leaves the generic 'Aura Retail' +
    // no-logo fallback that _renderIdle() already painted -- never a broken
    // screen. This is the ONE thing on this page that is fetched rather than
    // pushed over the channel, because it changes once a quarter, not once a
    // scan, and the till's own settings screen is the place that edits it.
    _loadBranding: function () {
      if (typeof global.fetch !== 'function') return;
      var self = this;
      global.fetch('/api/sub/retail/settings/branding', { credentials: 'include', cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (resp) {
          var b = (resp && resp.status === 'success' && resp.data) ? resp.data : {};
          var name = b.branding_business_name;
          if (name) {
            setText('cd-shop-name', name);
            if (global.document) global.document.title = name + ' — ' + t('Customer Display');
          }
          if (b.has_logo) return self._loadLogo();
        })
        .catch(function () { /* generic fallback already on screen */ });
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

    _renderIdle: function () {
      setText('cd-welcome', t('Welcome!'));
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
          return '<div class="cd-item-row">' +
            '<div class="cd-item-main">' +
              '<div class="cd-item-name">' + esc(it.name) + '</div>' +
              '<div class="cd-item-meta">' + esc(it.unitPrice || '') + ' × ' + esc(qty) + '</div>' +
            '</div>' +
            '<div class="cd-item-total money">' + esc(it.lineTotal || '') + '</div>' +
          '</div>';
        }).join('');
      }
      setText('cd-subtotal-label', t('Subtotal'));
      setText('cd-subtotal-value', msg.subtotal);
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
