/* Expenses -- MONEY-4b (retail schema v52), review defect D2.
 *
 * window.RetailExpenses.render(container) paints the Expenses screen: money the shop paid out that
 * is NOT a sale, a purchase, a supplier payment or a till paid-out -- rent, utilities, salaries,
 * card fees. Each one is a book entry (Dr <expense account> / Cr Cash and Bank) the moment it is
 * saved, so the profit-and-loss and the balance sheet see it. Voiding one posts the opposite
 * entry; nothing is ever edited or deleted.
 *
 * DATA. GET  /api/sub/retail/expenses?from=&to=&account=   (retail.books.view)
 *       POST /api/sub/retail/expenses                      (retail.books.record)
 *       POST /api/sub/retail/expenses/<uid>/void           (retail.books.record, reason required)
 * The server owns the rules (allowed accounts, no future date, amount > 0); the dialog only
 * collects. The list is THIS computer's record: other computers' books carry the money (journals
 * sync) but not the rows (ROADMAP.md MONEY-4b).
 *
 * MOUNT. app-shell.js's _safeRender calls RetailExpenses.render(#sub-content) for the 'expenses'
 * nav row (RetailSystem.render()'s switch is deliberately not touched). Needs
 * <script src="/static/expenses.js"></script> in index.html. Tolerated ABSENT by the caller.
 *
 * STYLE. Vanilla, no build step. Logical CSS properties only (reads correctly under body.rtl),
 * colours are theme tokens, every string goes through t() so the Arabic lives in the catalogue.
 */
(function () {
  'use strict';

  var URL = '/api/sub/retail/expenses';
  // The server's EXPENSE_ACCOUNT_CODES. The server refuses any other code, so a drift here is a
  // visible 400, never a wrong booking. [code, English label]
  var ACCOUNTS = [
    ['6000', 'Rent'], ['6100', 'Utilities'], ['6200', 'Salaries and wages'],
    ['6300', 'Transport and delivery'], ['6400', 'Marketing'], ['6500', 'Bank and card fees'],
    ['6900', 'Other expenses'],
  ];
  var PAID_VIA = [
    ['cash_office', 'Cash from the office'], ['bank', 'Bank transfer'], ['card', 'Card'],
  ];
  var state = { today: '', from: '', to: '', account: '' };

  function t(s) {
    try { if (typeof window.t === 'function') return window.t(s); } catch (e) { /* identity */ }
    return s;
  }
  function esc(v) {
    return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  // The app's own money formatter (currency mark + the currency's decimals). RetailSystem is a
  // top-level const in another classic script: reachable by bare name, not as window.RetailSystem.
  function money(n) {
    var v = +n || 0;
    try {
      if (typeof RetailSystem !== 'undefined' && RetailSystem && typeof RetailSystem._fmt === 'function') {
        return RetailSystem._fmt(v);
      }
    } catch (e) { /* fall through */ }
    return v.toFixed(3);
  }
  function can(cap) {
    try {
      if (window.SubsystemApp && typeof SubsystemApp.hasCapability === 'function') {
        return SubsystemApp.hasCapability(cap);
      }
    } catch (e) { /* fall through */ }
    return true;
  }
  function toast(msg, kind) {
    try { if (window.SubsystemApp && SubsystemApp.showToast) { SubsystemApp.showToast(msg, kind); return; } }
    catch (e) { /* fall through */ }
  }
  function accountLabel(code) {
    for (var i = 0; i < ACCOUNTS.length; i++) if (ACCOUNTS[i][0] === code) return t(ACCOUNTS[i][1]);
    return code;
  }
  function viaLabel(code) {
    for (var i = 0; i < PAID_VIA.length; i++) if (PAID_VIA[i][0] === code) return t(PAID_VIA[i][1]);
    return code || '';
  }
  function localISO(d) {
    var m = d.getMonth() + 1, day = d.getDate();
    return d.getFullYear() + '-' + (m < 10 ? '0' : '') + m + '-' + (day < 10 ? '0' : '') + day;
  }
  async function call(method, url, body) {
    var opts = { method: method, credentials: 'include', cache: 'no-store' };
    if (body !== undefined) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    var res = await fetch(url, opts);
    return res.json();
  }

  function injectStyles() {
    if (document.getElementById('exp-style')) return;
    var st = document.createElement('style');
    st.id = 'exp-style';
    st.textContent = [
      '.exp-bar { display:flex; flex-wrap:wrap; gap:12px; align-items:flex-end; margin-block-end:16px; }',
      '.exp-bar label { display:block; font-size:12px; color:var(--text-muted); margin-block-end:4px; }',
      '.exp-total { margin-inline-start:auto; text-align:end; }',
      '.exp-total b { display:block; font-size:22px; color:var(--text-money); }',
      '.exp-total span { font-size:12px; color:var(--text-muted); text-transform:uppercase; }',
      '.exp-row-void td { color:var(--text-muted); text-decoration:line-through; }',
      '.exp-row-void td.exp-keep { text-decoration:none; }',
      '.exp-void-why { display:block; font-size:12px; font-style:italic; }',
      '.exp-empty { text-align:center; color:var(--text-muted); padding:36px 12px; }',
      '.exp-err { color:var(--danger, #b3261e); margin-block-start:8px; min-block-size:1.2em; font-size:13px; }',
    ].join('\n');
    document.head.appendChild(st);
  }

  function closeModal() {
    var el = document.getElementById('exp-modal');
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }
  function openModal(innerHtml, dialogId) {
    closeModal();
    var overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'exp-modal';
    overlay.innerHTML = innerHtml;
    document.body.appendChild(overlay);
    try {
      if (typeof RetailSystem !== 'undefined' && RetailSystem._wireModalA11y) {
        RetailSystem._wireModalA11y(overlay, dialogId);
      }
    } catch (e) { /* Escape/focus-trap is an extra; the buttons still work */ }
    return overlay;
  }

  function optionList(pairs, selected) {
    return pairs.map(function (p) {
      // Template literal on purpose: retail_option_i18n_test.js scans the source for
      // `${...}` inside every <option> to prove the label goes through t().
      return `<option value="${esc(p[0])}"${p[0] === selected ? ' selected' : ''}>${esc(t(p[1]))}</option>`;
    }).join('');
  }

  async function load() {
    var c = document.getElementById('exp-list');
    if (!c) return;
    var qs = [];
    if (state.from) qs.push('from=' + encodeURIComponent(state.from));
    if (state.to) qs.push('to=' + encodeURIComponent(state.to));
    if (state.account) qs.push('account=' + encodeURIComponent(state.account));
    var res;
    try {
      res = await call('GET', URL + (qs.length ? '?' + qs.join('&') : ''));
    } catch (e) {
      c.innerHTML = '<div class="exp-empty">' + esc(t('Could not load expenses.')) + '</div>';
      return;
    }
    if (!res || res.status !== 'success') {
      c.innerHTML = '<div class="exp-empty">' + esc((res && res.message) || t('Could not load expenses.')) + '</div>';
      return;
    }
    var data = res.data || {};
    state.today = data.today || state.today;
    var rows = data.expenses || [];
    var totalEl = document.getElementById('exp-total-amount');
    if (totalEl) totalEl.textContent = money(data.total);
    if (!rows.length) {
      c.innerHTML = '<div class="exp-empty">' + esc(t('No expenses in this period.')) + '</div>';
      return;
    }
    var canRecord = can('retail.books.record');
    c.innerHTML = '<div style="overflow-x:auto"><table class="ret-table"><thead><tr>' +
      '<th>' + esc(t('Date')) + '</th><th>' + esc(t('Account')) + '</th><th>' + esc(t('Note')) + '</th>' +
      '<th>' + esc(t('Paid by')) + '</th><th>' + esc(t('Recorded by')) + '</th>' +
      '<th class="num">' + esc(t('Amount')) + '</th><th></th></tr></thead><tbody>' +
      rows.map(function (r) {
        var voided = !!r.voided;
        var action = '';
        if (voided) {
          action = '<span class="exp-void-why">' + esc(t('Voided')) + (r.void_reason ? ': ' + esc(r.void_reason) : '') + '</span>';
        } else if (canRecord) {
          action = '<button class="ret-btn ret-btn-ghost ret-btn-sm" data-uid="' + esc(r.uid) +
            '" onclick="RetailExpenses.openVoid(this.dataset.uid)">' + esc(t('Void')) + '</button>';
        }
        return '<tr class="' + (voided ? 'exp-row-void' : '') + '">' +
          '<td>' + esc(r.business_date) + '</td>' +
          '<td>' + esc(accountLabel(r.gl_account_code) || r.account_name || '') + '</td>' +
          '<td>' + esc(r.memo || '') + '</td>' +
          '<td>' + esc(viaLabel(r.paid_via)) + '</td>' +
          '<td>' + esc(r.created_by_name || '') + '</td>' +
          '<td class="num">' + esc(money(r.amount)) + '</td>' +
          '<td class="exp-keep">' + action + '</td></tr>';
      }).join('') + '</tbody></table></div>';
  }

  async function render(c) {
    if (!c) return;
    injectStyles();
    if (!can('retail.books.view')) {
      c.innerHTML = '<div class="exp-empty"><h2>' + esc(t('Expenses')) + '</h2><p>' +
        esc(t('The books are limited to the store owner and the accountant.')) + '</p></div>';
      return;
    }
    var now = new Date();
    state.today = state.today || localISO(now);
    state.from = localISO(new Date(now.getFullYear(), now.getMonth(), 1));
    state.to = localISO(now);
    state.account = '';
    var addBtn = can('retail.books.record')
      ? '<button class="ret-btn ret-btn-primary" onclick="RetailExpenses.openAdd()">' + esc(t('Add expense')) + '</button>'
      : '';
    c.innerHTML =
      '<div class="ret-hdr"><h2 class="ret-title"><span>' + esc(t('Expenses')) + '</span></h2>' + addBtn + '</div>' +
      '<p style="color:var(--text-muted);font-size:13px;margin-block:0 14px;max-inline-size:62ch">' +
      esc(t('Rent, bills and salaries paid from the office or the bank. Money paid out of a till drawer is recorded on the till.')) + '</p>' +
      '<div class="exp-bar">' +
        '<div><label for="exp-from">' + esc(t('From')) + '</label>' +
          '<input type="date" id="exp-from" class="ret-input" value="' + esc(state.from) + '" onchange="RetailExpenses.filter()" /></div>' +
        '<div><label for="exp-to">' + esc(t('To')) + '</label>' +
          '<input type="date" id="exp-to" class="ret-input" value="' + esc(state.to) + '" onchange="RetailExpenses.filter()" /></div>' +
        '<div><label for="exp-account">' + esc(t('Account')) + '</label>' +
          '<select id="exp-account" class="ret-input" onchange="RetailExpenses.filter()">' +
          `<option value="">${esc(t('All accounts'))}</option>` + optionList(ACCOUNTS, '') + '</select></div>' +
        '<div class="exp-total"><span>' + esc(t('Total')) + '</span><b id="exp-total-amount" class="num">' + esc(money(0)) + '</b></div>' +
      '</div>' +
      '<div class="sub-chart-card"><div id="exp-list"><div class="exp-empty">' + esc(t('Loading…')) + '</div></div></div>';
    await load();
  }

  function filter() {
    state.from = (document.getElementById('exp-from') || {}).value || '';
    state.to = (document.getElementById('exp-to') || {}).value || '';
    state.account = (document.getElementById('exp-account') || {}).value || '';
    return load();
  }

  function openAdd() {
    var day = state.today || localISO(new Date());
    var overlay = openModal(
      '<div class="ret-modal" style="width:420px" id="exp-dialog" role="dialog" aria-modal="true" aria-labelledby="exp-dialog-title">' +
        '<h3 id="exp-dialog-title">' + esc(t('Add expense')) + '</h3>' +
        '<div class="ret-field"><label for="exp-f-date">' + esc(t('Date')) + '</label>' +
          '<input type="date" id="exp-f-date" value="' + esc(day) + '" max="' + esc(day) + '" /></div>' +
        '<div class="ret-field"><label for="exp-f-account">' + esc(t('What for?')) + '</label>' +
          '<select id="exp-f-account">' +
          `<option value="">${esc(t('Choose what the money was for'))}</option>` +
          optionList(ACCOUNTS, '') + '</select></div>' +
        '<div class="ret-field"><label for="exp-f-amount">' + esc(t('Amount')) + '</label>' +
          '<input type="number" id="exp-f-amount" min="0.001" step="0.001" inputmode="decimal" /></div>' +
        '<div class="ret-field"><label for="exp-f-via">' + esc(t('Paid by')) + '</label>' +
          '<select id="exp-f-via">' + optionList(PAID_VIA, 'cash_office') + '</select></div>' +
        '<div class="ret-field"><label for="exp-f-memo">' + esc(t('Note')) + '</label>' +
          '<input type="text" id="exp-f-memo" maxlength="200" placeholder="' + esc(t('Optional note')) + '" /></div>' +
        '<div class="exp-err" id="exp-f-err" role="alert"></div>' +
        '<div class="ret-modal-footer">' +
          '<button class="ret-btn ret-btn-ghost" onclick="RetailExpenses.closeModal()">' + esc(t('Cancel')) + '</button>' +
          '<button class="ret-btn ret-btn-primary" id="exp-f-save" onclick="RetailExpenses.save()">' + esc(t('Save')) + '</button>' +
        '</div></div>', 'exp-dialog');
    overlay.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' && ev.target && ev.target.tagName !== 'SELECT' && ev.target.tagName !== 'BUTTON') {
        ev.preventDefault();
        save();
      }
    });
    var first = document.getElementById('exp-f-account');
    if (first) first.focus();
  }

  async function save() {
    var err = document.getElementById('exp-f-err');
    var btn = document.getElementById('exp-f-save');
    var account = (document.getElementById('exp-f-account') || {}).value || '';
    var amount = parseFloat((document.getElementById('exp-f-amount') || {}).value || '0');
    if (!account) { if (err) err.textContent = t('Choose what the money was for'); return; }
    if (!(amount > 0)) { if (err) err.textContent = t('Enter a valid amount'); return; }
    if (err) err.textContent = '';
    if (btn) { btn.disabled = true; btn.textContent = t('Saving…'); }
    var res;
    try {
      res = await call('POST', URL, {
        business_date: (document.getElementById('exp-f-date') || {}).value || undefined,
        gl_account_code: account,
        amount: amount,
        paid_via: (document.getElementById('exp-f-via') || {}).value || 'cash_office',
        memo: (document.getElementById('exp-f-memo') || {}).value || '',
      });
    } catch (e) {
      res = { status: 'error', message: t('Could not save. Nothing was recorded.') };
    }
    if (res && res.status === 'success') {
      closeModal();
      toast(t('Expense recorded'), 'success');
      await load();
      return;
    }
    if (err) err.textContent = (res && res.message) || t('Could not save. Nothing was recorded.');
    if (btn) { btn.disabled = false; btn.textContent = t('Save'); }
  }

  function openVoid(uid) {
    openModal(
      '<div class="ret-modal" style="width:400px" id="exp-dialog" role="dialog" aria-modal="true" aria-labelledby="exp-dialog-title">' +
        '<h3 id="exp-dialog-title">' + esc(t('Void expense')) + '</h3>' +
        '<p style="color:var(--text-muted);font-size:13px">' +
          esc(t('The expense stays on the list, crossed out, and the books get the opposite entry.')) + '</p>' +
        '<div class="ret-field"><label for="exp-v-reason">' + esc(t('Reason')) + '</label>' +
          '<input type="text" id="exp-v-reason" maxlength="200" data-uid="' + esc(uid) + '" /></div>' +
        '<div class="exp-err" id="exp-v-err" role="alert"></div>' +
        '<div class="ret-modal-footer">' +
          '<button class="ret-btn ret-btn-ghost" onclick="RetailExpenses.closeModal()">' + esc(t('Cancel')) + '</button>' +
          '<button class="ret-btn ret-btn-primary" id="exp-v-go" onclick="RetailExpenses.confirmVoid()">' + esc(t('Void expense')) + '</button>' +
        '</div></div>', 'exp-dialog');
    var input = document.getElementById('exp-v-reason');
    if (input) {
      input.focus();
      input.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { ev.preventDefault(); confirmVoid(); } });
    }
  }

  async function confirmVoid() {
    var input = document.getElementById('exp-v-reason');
    var err = document.getElementById('exp-v-err');
    var btn = document.getElementById('exp-v-go');
    var reason = ((input || {}).value || '').trim();
    if (!reason) { if (err) err.textContent = t('A reason is required to void an expense.'); return; }
    if (btn) btn.disabled = true;
    var res;
    try {
      res = await call('POST', URL + '/' + encodeURIComponent(input.dataset.uid) + '/void', { reason: reason });
    } catch (e) {
      res = { status: 'error', message: t('Could not save. Nothing was recorded.') };
    }
    if (res && res.status === 'success') {
      closeModal();
      toast(t('Expense voided'), 'success');
      await load();
      return;
    }
    if (err) err.textContent = (res && res.message) || t('Could not save. Nothing was recorded.');
    if (btn) btn.disabled = false;
  }

  window.RetailExpenses = {
    render: render, filter: filter, openAdd: openAdd, save: save,
    openVoid: openVoid, confirmVoid: confirmVoid, closeModal: closeModal,
    ACCOUNTS: ACCOUNTS,
  };
})();
