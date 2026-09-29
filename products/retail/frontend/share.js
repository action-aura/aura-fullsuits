/**
 * Action Aura — Retail "Send on WhatsApp" (P1a item #6/interface "wa.me
 * helper (T)").
 *
 * There is no wa.me code anywhere else in this repo (checked with grep
 * before this file was written, matching p1a-plan.json's own note). This is
 * the one place a phone number becomes a wa.me link and the one place a
 * receipt/statement/reminder becomes the text sent through it.
 *
 * `window.AuraShare`, guarded for `typeof window` throughout so every pure
 * function here (normalizeJoPhone, the text builders) is node-testable with
 * no DOM at all — `node products/retail/tests/retail_share_whatsapp_test.js`
 * requires this file directly via CommonJS and never touches a browser.
 *
 * OWNER DECISION (contract.md, 2026-09-28): the till's own "Send on
 * WhatsApp" opens whatever WhatsApp the TILL COMPUTER is signed into — this
 * is NOT Action Aura's own service number, and it is NOT a per-shop
 * WhatsApp Business API integration. A cashier presses Send, WhatsApp Web or
 * Desktop opens with the message pre-filled, and the cashier still presses
 * WhatsApp's own send button. The UI text this file's callers show always
 * says "Send", never "automatic" — see subsystem-retail.js's own receipt/
 * statement/reminder buttons.
 */
(function (root) {
  'use strict';

  // A FUNCTION, not a value cached at module-load time: this module is
  // require()'d directly by retail_share_whatsapp_test.js, which sets
  // `global.window` itself, per test, AFTER this file has already loaded
  // once. A `var hasWindow = typeof window !== 'undefined'` computed only
  // at load time would freeze at whatever was true the FIRST time this
  // module was required (false, in a bare `node` process with no window
  // yet) and never see a later test's own stubbed window at all --
  // window.open would silently never be called and every such test would
  // fail on "expected window.open to have been called" for a reason that
  // has nothing to do with the code under test. Checked fresh on every
  // call instead, the same way share.js's own callers (subsystem-retail.js,
  // a real browser) always have a real, unchanging `window`.
  function hasWindow() { return typeof window !== 'undefined'; }

  // ── Phone normalisation ───────────────────────────────────────────────────

  // Arabic-Indic digits (٠-٩, U+0660-U+0669) -> Western 0-9. A shop owner or
  // customer typing a Jordanian number on an Arabic keyboard types these, not
  // the Western glyphs the rest of this function expects.
  function toWesternDigits(s) {
    return String(s == null ? '' : s).replace(/[٠-٩]/g, function (ch) {
      return String(ch.charCodeAt(0) - 0x0660);
    });
  }

  /**
   * Normalises a Jordanian mobile number to E.164 (+9627XXXXXXXX), or
   * returns `false` for anything that cannot be resolved to one — NEVER a
   * best-effort guess, because a wrong-but-plausible-looking number would
   * open a wa.me link to a stranger, or (more likely) to nobody at all,
   * silently.
   *
   * Accepts, after stripping everything but digits (spaces, dashes,
   * parentheses, a leading '+' — all noise once Arabic-Indic digits are
   * converted):
   *   07XXXXXXXX      (10 digits, local dialling form)
   *   7XXXXXXXX       (9 digits, no leading 0)
   *   009627XXXXXXXX  (00 international prefix)
   *   9627XXXXXXXX    (no + and no 00, 12 digits total)
   *   +9627XXXXXXXX   (already E.164 — '+' is stripped by the digit filter,
   *                    so this collapses into the 9627XXXXXXXX case above)
   *
   * The final 9-digit LOCAL part must start with '7' (Jordan's own mobile
   * prefixes are 77/78/79 — Zain/Orange/Umniah) and be exactly 9 digits;
   * anything else (a landline 0X number, a wrong digit count, empty input)
   * is refused.
   */
  function normalizeJoPhone(raw) {
    if (raw == null) return false;
    var digits = toWesternDigits(raw).replace(/\D/g, '');
    if (!digits) return false;

    var local9 = null;
    if (digits.indexOf('00962') === 0) {
      local9 = digits.slice(5);
    } else if (digits.indexOf('962') === 0 && digits.length === 12) {
      local9 = digits.slice(3);
    } else if (digits.charAt(0) === '0' && digits.length === 10) {
      local9 = digits.slice(1);
    } else if (digits.length === 9) {
      local9 = digits;
    } else {
      return false;
    }

    if (!/^7\d{8}$/.test(local9)) return false;
    return '+962' + local9;
  }

  // ── wa.me ──────────────────────────────────────────────────────────────

  /**
   * Opens https://wa.me/<e164-digits>?text=<encoded text>, which hands off
   * to whatever WhatsApp (Desktop app, if registered as the wa.me handler,
   * else WhatsApp Web) this COMPUTER is signed into — never Action Aura's
   * own number, never a background send (see this file's own header
   * comment). Returns `false` and opens nothing for a number
   * normalizeJoPhone cannot resolve — the caller's job is to tell the
   * cashier why, this function's job is to never open a broken link.
   */
  function whatsapp(phone, text) {
    var normalized = normalizeJoPhone(phone);
    if (!normalized) return false;
    var digits = normalized.replace(/\D/g, ''); // wa.me wants no leading '+'
    var url = 'https://wa.me/' + digits + '?text=' + encodeURIComponent(text == null ? '' : text);
    if (hasWindow() && typeof window.open === 'function') {
      window.open(url, '_blank', 'noopener');
    }
    return true;
  }

  // ── Share audit (crew D's endpoint; contract.md "Share audit") ───────────

  /**
   * Fire-and-forget POST /api/sub/retail/share-log after a wa.me link opens
   * -- the retail blueprint's own url_prefix, corrected 2026-09-28 (crew D
   * built the route under /api/sub/retail/, not the bare /api/retail/ this
   * file originally guessed at before that route existed to check against).
   * MUST NEVER block or throw — a failed audit write is not a reason to
   * have refused (or to appear to have refused) a share that already
   * happened; the cashier already saw WhatsApp open. Every rejection path
   * (network down, 401, 400) is swallowed here, not surfaced.
   */
  function logShare(kind, ref) {
    if (!hasWindow() || typeof window.fetch !== 'function') return;
    try {
      var body = JSON.stringify({
        channel: 'whatsapp',
        kind: kind,
        ref: String(ref == null ? '' : ref).slice(0, 64),
      });
      window.fetch('/api/sub/retail/share-log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: body,
      }).catch(function () { /* best-effort only, see this function's own comment */ });
    } catch (e) { /* never block or throw the share itself */ }
  }

  // ── Message text builders ─────────────────────────────────────────────
  //
  // Pure functions — no DOM, no fetch — so they are testable exactly like
  // normalizeJoPhone. FIX ROUND F13: the message LANGUAGE now follows the
  // shop's own `branding_receipt_language` setting (the same field the
  // printed receipt already obeys), defaulting to Arabic when unset or
  // 'auto' — never the CASHIER's own UI language, which is frequently
  // English even in an Arabic-speaking shop, and never hardcoded Arabic-only
  // either (an English-configured shop gets an English message). Each
  // builder below carries its own small EN/AR label pair rather than going
  // through the app's t() catalogue on purpose — t() resolves against
  // whatever language THIS BROWSER TAB is currently showing (the cashier's
  // UI), which is exactly the wrong axis for a message addressed to a
  // CUSTOMER's phone.
  //
  // `opts.lang` is 'ar' (default) or 'en'; anything else falls back to 'ar'.
  // `opts.formatMoney` is an OPTIONAL injected formatter (amount) => string
  // -- the app itself always passes its own currency-aware `_fmt`, never
  // letting this file's own bare `toFixed(3)` fallback (below) be the thing
  // that actually ships. The fallback exists only so this file stays usable
  // stand-alone (this node test, or a page that has not wired the app's
  // formatter yet) without silently producing a blank message.

  // FIX ROUND 3 (LOW-2): "N days" in the customer's language, in ONE place
  // so the polite reminder, the firm reminder and any future message can
  // never disagree. English: "1 day" / "N days". Arabic has four number
  // forms and the message used to hard-code the last one ("30 يوماً") for
  // every count, which reads wrong for 1 and 2 and for 3-10:
  //   1        يوم واحد   (singular, the number is inside the word)
  //   2        يومين      (dual, the number is inside the word)
  //   3 to 10  N أيام     (plural after a small count)
  //   11+      N يوماً    (singular accusative after a large count)
  // Anything below 1 or not a number is treated as 0 days -> "0 يوماً" /
  // "0 days", never NaN or blank.
  function daysPhrase(days, lang) {
    var n = Math.floor(Number(days));
    if (!isFinite(n) || n < 0) n = 0;
    if (lang === 'en') return n + (n === 1 ? ' day' : ' days');
    if (n === 1) return 'يوم واحد';
    if (n === 2) return 'يومين';
    if (n >= 3 && n <= 10) return n + ' أيام';
    return n + ' يوماً';
  }

  var LABELS = {
    ar: {
      receiptTitle: 'فاتورتك', total: 'الإجمالي', saved: 'وفّرت', thanks: 'شكراً لتسوقكم معنا 🙏',
      statementTitle: 'كشف حساب', currentBalance: 'الرصيد الحالي', oldestUnpaid: 'أقدم مبلغ غير مسدد', recentLines: 'آخر الحركات',
      greetingDefault: 'تحية طيبة،',
      politeBody: function (amount, days) {
        return 'نود تذكيركم بوجود رصيد مستحق قدره ' + amount + ' منذ ' + daysPhrase(days, 'ar') + '. يسعدنا استلام السداد في الوقت المناسب لكم. شكراً لتعاونكم 🙏';
      },
      firmBody: function (amount, days) {
        return 'يوجد لدينا رصيد متأخر عليكم قدره ' + amount + ' منذ ' + daysPhrase(days, 'ar') + '. نرجو التكرم بالتسديد في أقرب وقت ممكن لتفادي أي إجراء إضافي.';
      },
    },
    en: {
      receiptTitle: 'Your receipt', total: 'Total', saved: 'You saved', thanks: 'Thank you for shopping with us 🙏',
      statementTitle: 'Statement', currentBalance: 'Current balance', oldestUnpaid: 'Oldest unpaid', recentLines: 'Recent activity',
      greetingDefault: 'Hello,',
      politeBody: function (amount, days) {
        return 'A reminder that you have an outstanding balance of ' + amount + ' from ' + daysPhrase(days, 'en') + ' ago. We would appreciate payment at your convenience. Thank you 🙏';
      },
      firmBody: function (amount, days) {
        return 'You have an overdue balance of ' + amount + ' from ' + daysPhrase(days, 'en') + ' ago. Please settle it as soon as possible to avoid further action.';
      },
    },
  };
  function labelsFor(lang) { return LABELS[lang === 'en' ? 'en' : 'ar']; }

  function moneyText(amount, currency, formatMoney) {
    if (typeof formatMoney === 'function') {
      var out = formatMoney(amount);
      if (typeof out === 'string' && out) return out;
    }
    // Fallback only -- see this section's own header comment. Western
    // digits, JD to 3 decimals, same as the app's own money rule.
    var n = Number(amount);
    if (!isFinite(n)) n = 0;
    return (currency || 'JD') + ' ' + n.toFixed(3);
  }

  /**
   * The sale-complete receipt: shop name, every product name, the total,
   * 'you saved' when a discount/promotion actually applied, and the SHORT
   * sale number (the caller's job to pass the short form -- e.g.
   * 'SALE-000124', never the long device/company-suffixed internal id; this
   * function does not parse or shorten it). `opts.items` is
   * `[{name, qty, total}]`; every field is optional except `items` and
   * `total`, so a caller with a partial sale record still gets a usable
   * message rather than a thrown error.
   */
  function buildReceiptText(opts) {
    var o = opts || {};
    var L = labelsFor(o.lang);
    var currency = o.currency || 'JD';
    var fm = function (n) { return moneyText(n, currency, o.formatMoney); };
    var lines = [];
    if (o.shopName) lines.push(o.shopName);
    lines.push(L.receiptTitle + (o.saleNumber ? ' #' + o.saleNumber : ''));
    (Array.isArray(o.items) ? o.items : []).forEach(function (it) {
      var name = (it && it.name) || '';
      var qty = (it && it.qty != null) ? it.qty : 1;
      var lineTotal = (it && it.total != null) ? fm(it.total) : '';
      lines.push(name + ' × ' + qty + (lineTotal ? ' — ' + lineTotal : ''));
    });
    lines.push(L.total + ': ' + fm(o.total));
    if (o.saved != null && Number(o.saved) > 0) {
      lines.push(L.saved + ': ' + fm(o.saved));
    }
    lines.push(L.thanks);
    return lines.join('\n');
  }

  /**
   * Customer statement: current balance, the oldest unpaid item, and the
   * last up-to-5 ledger lines. `opts.recentLines` is
   * `[{label, amount, date}]` -- `label` is ALREADY a translated string (the
   * caller resolves 'Credit sale'/'Payment'/etc. through its own catalogue
   * before calling this, in the SAME `lang`, since this file carries no
   * catalogue of its own) and `date` is ALREADY a formatted string (the
   * caller formats it through the app's own locale-aware date formatter --
   * this file does no date parsing at all, on purpose, to stay a pure
   * string-assembly function). `recentLines` must already be the NEWEST-
   * first 5 -- this function takes the first 5 it is given, unchanged, and
   * does not re-sort (the "never re-derive what the ledger already decided"
   * posture the ledger-paper design_system entry states).
   */
  function buildStatementText(opts) {
    var o = opts || {};
    var L = labelsFor(o.lang);
    var currency = o.currency || 'JD';
    var fm = function (n) { return moneyText(n, currency, o.formatMoney); };
    var lines = [];
    if (o.shopName) lines.push(o.shopName);
    lines.push(L.statementTitle + (o.customerName ? ' — ' + o.customerName : ''));
    lines.push(L.currentBalance + ': ' + fm(o.balance));
    if (o.oldestUnpaid) {
      var ou = o.oldestUnpaid;
      lines.push(L.oldestUnpaid + ': ' + fm(ou.amount) + (ou.date ? ' (' + ou.date + ')' : ''));
    }
    var recent = (Array.isArray(o.recentLines) ? o.recentLines : []).slice(0, 5);
    if (recent.length) {
      lines.push(L.recentLines + ':');
      recent.forEach(function (r) {
        lines.push('- ' + (r.label || '') + ': ' + fm(r.amount) + (r.date ? ' (' + r.date + ')' : ''));
      });
    }
    return lines.join('\n');
  }

  /**
   * One overdue row's reminder, in either register:
   *   'polite' — first/routine reminder (default)
   *   'firm'   — the sender's own explicit choice, never auto-picked from
   *              the day count (FIX ROUND F3(d): a cashier decides the
   *              register, this file only supplies the two templates).
   * Both carry the amount and the days overdue. Falls back to 'polite' for
   * any unrecognised tone rather than escalating a caller's typo into the
   * firm wording.
   */
  function buildReminderText(opts, tone) {
    var o = opts || {};
    var L = labelsFor(o.lang);
    var currency = o.currency || 'JD';
    var amount = moneyText(o.amount, currency, o.formatMoney);
    var days = Number(o.daysOverdue) || 0;
    var greeting = o.customerName ? (o.customerName + (o.lang === 'en' ? ',' : '،')) : L.greetingDefault;
    var body = tone === 'firm' ? L.firmBody(amount, days) : L.politeBody(amount, days);
    return greeting + '\n' + body + (o.shopName ? '\n— ' + o.shopName : '');
  }

  var AuraShare = {
    normalizeJoPhone: normalizeJoPhone,
    whatsapp: whatsapp,
    logShare: logShare,
    buildReceiptText: buildReceiptText,
    buildStatementText: buildStatementText,
    buildReminderText: buildReminderText,
    daysPhrase: daysPhrase,
  };

  if (hasWindow()) {
    window.AuraShare = AuraShare;
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = AuraShare;
  }
})(typeof window !== 'undefined' ? window : this);
