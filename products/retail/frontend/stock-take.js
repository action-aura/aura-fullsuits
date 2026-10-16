/* Aura Retail -- the Stock Take screen (STOCK-TAKE, schema v57).
 *
 * window.RetailStockTake.render(host) paints the screen behind the Stock > "Stock Take" entry:
 * a list of counts, and (for the open one) a scan box, the counted lines with their variance,
 * and Post / Cancel.
 *
 * HOW A COUNT READS. The person scans (or types a SKU / name) and each scan ADDS one to that
 * product's counted quantity; typing a number into a line's Counted box REPLACES it. The system
 * quantity beside it is frozen the first time the product is counted, so the variance is "what is
 * on the shelf minus what the system said when I counted". Products nobody counted are not touched
 * when the count is posted -- counting one aisle is normal.
 *
 * Posting asks first and shows what it will do: how many lines move, how many units come and go,
 * and the net money value. The server is the authority on everything (it re-reads cost and stock);
 * this screen only displays what /stock-counts/<uid> returns and never computes a variance itself.
 *
 * Shape: the self-contained feature-file convention employees.js and modifiers-settings.js follow
 * (no build step). HTTP goes through RetailSystem so the 401 -> re-login handling covers this
 * screen too; modals use RetailSystem._wireModalA11y / _closeModalOverlay, which own Escape, the
 * Tab trap and focus return.
 *
 * Arabic strings are real but "native speaker to confirm". Android has no stock-take screen yet
 * (recorded in ROADMAP.md, STOCK-TAKE).
 */
(function () {
  'use strict';

  var API = '/api/sub/retail';

  function t(s) {
    try {
      if (typeof window.t === 'function') return window.t(s);
    } catch (e) { /* identity */ }
    return s;
  }
  function RS() { return window.RetailSystem; }
  function esc(s) { return RS()._esc(s); }
  function toast(msg, kind) {
    if (window.SubsystemApp && typeof window.SubsystemApp.showToast === 'function') window.SubsystemApp.showToast(msg, kind);
  }
  // User data (product names) goes in <bdi data-no-i18n>: i18n.js's Arabic sweep rewrites any text
  // node that equals a catalogue key, so a product the shop named "Total" must not become a label.
  function bdi(s) { return '<bdi data-no-i18n>' + esc(s) + '</bdi>'; }

  var state = { host: null, products: null, openUid: null, detail: null };

  // ── pure display helpers (exposed for the test) ────────────────────────────
  function qty(n) { return String(+(+n || 0).toFixed(3)); }
  function signed(n) { var v = +n || 0; return (v > 0 ? '+' : '') + qty(v); }
  function dp() {
    try { var n = RS()._currencyDp(); return typeof n === 'number' ? n : 3; } catch (e) { return 3; }
  }
  function money(n) { var v = +n || 0; return (v > 0 ? '+' : '') + v.toFixed(dp()); }
  function varianceClass(v) { return v > 0 ? 'ret-badge-green' : (v < 0 ? 'ret-badge-red' : 'ret-badge-blue'); }
  var STATUS_LABEL = { open: 'Open', posted: 'Posted', cancelled: 'Cancelled' };
  var STATUS_COLOR = { open: 'blue', posted: 'green', cancelled: 'red' };

  // Match what was typed/scanned to a product: exact barcode or SKU first (a scan), then a single
  // name match (a typed name). More than one name match is refused rather than guessed.
  function matchProduct(products, text) {
    var q = String(text || '').trim();
    if (!q) return { error: 'empty' };
    var lq = q.toLowerCase();
    var exact = products.filter(function (p) {
      return String(p.barcode || '').toLowerCase() === lq || String(p.sku || '').toLowerCase() === lq;
    });
    if (exact.length === 1) return { product: exact[0] };
    var byName = products.filter(function (p) { return String(p.name || '').toLowerCase().indexOf(lq) >= 0; });
    if (byName.length === 1) return { product: byName[0] };
    return { error: byName.length > 1 || exact.length > 1 ? 'ambiguous' : 'none' };
  }

  async function loadProducts() {
    if (state.products) return state.products;
    var d = await RS()._get(API + '/products');
    state.products = d.data || [];
    return state.products;
  }

  // ── list of counts ─────────────────────────────────────────────────────────
  async function render(host) {
    state.host = host;
    state.openUid = null;
    state.detail = null;
    host.innerHTML = '<div class="sub-chart-card"><p style="padding:20px;color:var(--text-muted)">' + esc(t('Loading…')) + '</p></div>';
    var rows = [];
    try { rows = (await RS()._get(API + '/stock-counts')).data || []; } catch (e) { rows = []; }
    var open = rows.filter(function (r) { return r.status === 'open'; })[0];
    host.innerHTML =
      '<div class="ret-hdr">' +
        '<button class="sub-btn-primary" id="st-start" type="button">+ ' + esc(t('Start a count')) + '</button>' +
      '</div>' +
      '<div class="sub-chart-card"><div style="overflow-x:auto"><table class="ret-table" id="st-table">' +
        '<thead><tr><th>' + esc(t('Count')) + '</th><th>' + esc(t('Branch')) + '</th><th>' + esc(t('Status')) +
        '</th><th>' + esc(t('Lines')) + '</th><th>' + esc(t('Started')) + '</th><th></th></tr></thead><tbody>' +
        (rows.length ? rows.map(function (r) {
          return '<tr><td style="font-family:monospace">' + esc((r.uid || '').slice(0, 8)) + '</td>' +
            '<td>' + bdi(r.branch_name || '—') + '</td>' +
            '<td>' + RS()._badge(esc(t(STATUS_LABEL[r.status] || r.status)), STATUS_COLOR[r.status] || 'blue') + '</td>' +
            '<td>' + esc(r.line_count) + '</td>' +
            '<td style="color:var(--text-muted)">' + esc((r.started_at || '').replace('T', ' ').slice(0, 16)) + '</td>' +
            '<td><button class="ret-btn ret-btn-ghost ret-btn-sm" type="button" data-st-open="' + esc(r.uid) + '">' +
            esc(r.status === 'open' ? t('Continue') : t('View')) + '</button></td></tr>';
        }).join('') :
          '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:30px">' +
          esc(t('No counts yet. Start one, scan what is on the shelf, then post the difference.')) + '</td></tr>') +
      '</tbody></table></div></div>';
    host.querySelector('#st-start').onclick = function () { startCount(open); };
    Array.prototype.forEach.call(host.querySelectorAll('[data-st-open]'), function (b) {
      b.onclick = function () { openCount(b.getAttribute('data-st-open')); };
    });
  }

  async function startCount(existingOpen) {
    if (existingOpen) { openCount(existingOpen.uid); return; }   // one open count per branch: join it
    var d = await RS()._post(API + '/stock-counts', {});
    if (d.status === 'success') { openCount(d.data.uid); return; }
    if (d.code === 'STOCK_COUNT_ALREADY_OPEN' && d.count_uid) { openCount(d.count_uid); return; }
    toast(d.message || t('Could not start the count.'), 'error');
  }

  // ── one count ──────────────────────────────────────────────────────────────
  async function openCount(uid) {
    state.openUid = uid;
    var d = await RS()._get(API + '/stock-counts/' + encodeURIComponent(uid));
    if (d.status !== 'success') { toast(d.message || t('Count not found'), 'error'); return render(state.host); }
    state.detail = d.data;
    draw();
  }

  function draw() {
    var host = state.host, c = state.detail.count, lines = state.detail.lines, tot = state.detail.totals;
    var isOpen = c.status === 'open';
    host.innerHTML =
      '<div class="ret-hdr">' +
        '<button class="ret-btn ret-btn-ghost" id="st-back" type="button">← ' + esc(t('All counts')) + '</button>' +
        '<span style="margin-inline-start:12px">' + RS()._badge(esc(t(STATUS_LABEL[c.status] || c.status)), STATUS_COLOR[c.status] || 'blue') + '</span>' +
        '<button class="ret-btn ret-btn-ghost ret-btn-sm" id="st-refresh" type="button" style="margin-inline-start:auto">' + esc(t('Refresh')) + '</button>' +
      '</div>' +
      (isOpen ?
        '<div class="sub-chart-card" style="padding:14px"><form id="st-scan" class="ret-field-row" style="grid-template-columns:3fr 1fr auto;gap:8px;align-items:end">' +
          '<div class="ret-field" style="margin:0"><label for="st-code">' + esc(t('Scan or type a barcode, SKU or name')) + '</label>' +
          '<input id="st-code" type="text" autocomplete="off" autofocus /></div>' +
          '<div class="ret-field" style="margin:0"><label for="st-qty">' + esc(t('Qty')) + '</label>' +
          '<input id="st-qty" type="number" value="1" min="0" step="any" /></div>' +
          '<button class="ret-btn ret-btn-primary" type="submit">' + esc(t('Add')) + '</button></form></div>' : '') +
      '<div class="sub-chart-card"><div style="overflow-x:auto"><table class="ret-table" id="st-lines"><thead><tr>' +
        '<th>' + esc(t('Product')) + '</th><th>' + esc(t('System')) + '</th><th>' + esc(t('Counted')) + '</th><th>' +
        esc(t('Difference')) + '</th><th>' + esc(t('Value')) + '</th></tr></thead><tbody>' +
        (lines.length ? lines.map(function (l) {
          return '<tr><td>' + bdi(l.product_name || l.product_id) +
            '<div style="color:var(--text-muted);font-size:12px">' + bdi(l.barcode || l.sku || '') + '</div></td>' +
            '<td>' + esc(qty(l.system_qty_at_count)) + '</td>' +
            '<td>' + (isOpen ? '<input type="number" min="0" step="any" style="width:90px" data-st-set="' + esc(l.product_id) +
              '" value="' + esc(qty(l.counted_qty)) + '" aria-label="' + esc(t('Counted')) + '" />' : esc(qty(l.counted_qty))) + '</td>' +
            '<td><span class="ret-badge ' + varianceClass(l.variance) + '">' + esc(signed(l.variance)) + '</span></td>' +
            '<td>' + esc(money(l.variance_value)) + '</td></tr>';
        }).join('') :
          '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:24px">' + esc(t('Nothing counted yet.')) + '</td></tr>') +
      '</tbody></table></div></div>' +
      '<div class="sub-chart-card" style="padding:14px;display:flex;gap:16px;align-items:center;flex-wrap:wrap">' +
        '<div><strong>' + esc(tot.lines) + '</strong> ' + esc(t('products counted')) + '</div>' +
        '<div>' + esc(t('Over')) + ': <strong>' + esc(qty(tot.gain_qty)) + '</strong></div>' +
        '<div>' + esc(t('Short')) + ': <strong>' + esc(qty(tot.short_qty)) + '</strong></div>' +
        '<div>' + esc(t('Net value')) + ': <strong>' + esc(money(tot.variance_value)) + '</strong></div>' +
        (isOpen ? '<div style="margin-inline-start:auto"><button class="ret-btn ret-btn-danger" id="st-cancel" type="button">' + esc(t('Cancel count')) +
          '</button> <button class="ret-btn ret-btn-primary" id="st-post" type="button"' + (lines.length ? '' : ' disabled') + '>' + esc(t('Post count')) + '</button></div>' : '') +
      '</div>';
    host.querySelector('#st-back').onclick = function () { render(host); };
    host.querySelector('#st-refresh').onclick = function () { openCount(state.openUid); };
    if (!isOpen) return;
    host.querySelector('#st-scan').onsubmit = function (e) { e.preventDefault(); onScan(); };
    Array.prototype.forEach.call(host.querySelectorAll('[data-st-set]'), function (inp) {
      inp.onchange = function () { setLine(inp.getAttribute('data-st-set'), inp.value); };
    });
    host.querySelector('#st-cancel').onclick = cancelCount;
    host.querySelector('#st-post').onclick = confirmPost;
    var code = host.querySelector('#st-code');
    if (code) code.focus();
  }

  async function onScan() {
    var host = state.host, input = host.querySelector('#st-code');
    var q = Number(host.querySelector('#st-qty').value);
    var text = input.value;
    var m;
    try { m = matchProduct(await loadProducts(), text); } catch (e) { m = { error: 'none' }; }
    if (m.error) {
      toast(m.error === 'empty' ? t('Scan or type something first.') :
        (m.error === 'ambiguous' ? t('More than one product matches. Scan its barcode or type its SKU.') : t('Product not found.')), 'error');
      input.select();
      return;
    }
    var d = await RS()._post(API + '/stock-counts/' + encodeURIComponent(state.openUid) + '/lines',
      { product_id: m.product.id, quantity: q > 0 ? q : 1, mode: 'add' });
    if (d.status !== 'success') { toast(d.message || t('Could not record this count.'), 'error'); return; }
    await openCount(state.openUid);
  }

  async function setLine(productId, value) {
    var v = Number(value);
    if (!(v >= 0)) { toast(t('Enter a number, zero or more.'), 'error'); return openCount(state.openUid); }
    var d = await RS()._post(API + '/stock-counts/' + encodeURIComponent(state.openUid) + '/lines',
      { product_id: productId, quantity: v, mode: 'set' });
    if (d.status !== 'success') toast(d.message || t('Could not record this count.'), 'error');
    await openCount(state.openUid);
  }

  async function cancelCount() {
    if (!window.confirm(t('Cancel this count? Nothing will change in your stock.'))) return;
    var d = await RS()._post(API + '/stock-counts/' + encodeURIComponent(state.openUid) + '/cancel', {});
    if (d.status !== 'success') { toast(d.message || t('Could not cancel the count.'), 'error'); return; }
    render(state.host);
  }

  // The post confirmation shows what it is about to do, from the numbers the server just returned.
  function confirmPost() {
    var tot = state.detail.totals;
    var overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.innerHTML =
      '<div class="ret-modal" id="st-post-dialog" role="dialog" aria-modal="true" aria-labelledby="st-post-title">' +
        '<h3 id="st-post-title">' + esc(t('Post this count?')) + '</h3>' +
        '<p>' + esc(t('Your stock will be changed to match what you counted. Products you did not count stay as they are.')) + '</p>' +
        '<table class="ret-table"><tbody>' +
          '<tr><td>' + esc(t('Products counted')) + '</td><td><strong>' + esc(tot.lines) + '</strong></td></tr>' +
          '<tr><td>' + esc(t('Units over')) + '</td><td><strong>' + esc(qty(tot.gain_qty)) + '</strong></td></tr>' +
          '<tr><td>' + esc(t('Units short')) + '</td><td><strong>' + esc(qty(tot.short_qty)) + '</strong></td></tr>' +
          '<tr><td>' + esc(t('Net value of the difference')) + '</td><td><strong id="st-post-value">' + esc(money(tot.variance_value)) + '</strong></td></tr>' +
        '</tbody></table>' +
        '<div class="ret-modal-footer">' +
          '<button class="ret-btn ret-btn-ghost" type="button" id="st-post-no">' + esc(t('Not yet')) + '</button>' +
          '<button class="ret-btn ret-btn-primary" type="button" id="st-post-yes">' + esc(t('Post count')) + '</button>' +
        '</div></div>';
    document.body.appendChild(overlay);
    var close = RS()._wireModalA11y(overlay, 'st-post-dialog');
    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });
    overlay.querySelector('#st-post-no').onclick = close;
    overlay.querySelector('#st-post-yes').onclick = async function () {
      this.disabled = true;
      var d = await RS()._post(API + '/stock-counts/' + encodeURIComponent(state.openUid) + '/post', {});
      close();
      if (d.status !== 'success') { toast(d.message || t('Could not post the count.'), 'error'); return; }
      toast(t('Count posted. Your stock now matches the shelf.'), 'success');
      openCount(state.openUid);
    };
  }

  window.RetailStockTake = {
    render: render, matchProduct: matchProduct, qty: qty, signed: signed, money: money,
    _state: state
  };
})();
