/**
 * Aura Retail -- Shift / Cash-Drawer Management (feat/shift-cash-drawer).
 *
 * Self-contained, feature-scoped file -- same convention as
 * products/retail/frontend/einvoicing.js / import-wizard.js: this product's
 * frontend has no build step, so a new feature gets its own <script> file
 * rather than growing subsystem-retail.js (already ~2700 lines) further.
 * Unlike einvoicing.js (which is fully standalone), this file DOES hook into
 * RetailSystem's POS screen -- that's the whole point of a cash-drawer
 * gate/banner -- via a single explicit call RetailSystem._renderPOS makes
 * to `CashDrawer.mount(c)` at the end of its own render, not by
 * monkey-patching RetailSystem from here.
 *
 * ── Hard gate vs. soft warning (deliberate choice: SOFT WARNING) ─────────
 * A branch with no open cash session shows a dismissible-by-action banner
 * ("Open Shift") on the POS screen, but checkout is NEVER blocked
 * client-side. Two reasons:
 *   1. Server contract: create_sale/create_return NEVER require an open
 *      session -- sales.session_id/returns.session_id are nullable and
 *      stamped best-effort (products/retail/backend/api/retail_api.py's
 *      `_open_cash_session_id()`; see retail_cash_drawer_regression_test.py,
 *      which asserts a sale's response is byte-for-byte identical whether
 *      or not a session is open). A client-side hard gate would promise an
 *      invariant the server doesn't actually enforce -- and this product
 *      ships on Desktop + Android (Chaquopy) + a future KMP mobile app, so a
 *      gate living only in this one JS file could never be a REAL
 *      cross-surface guarantee anyway, just friction on one surface.
 *   2. This codebase's established "invisible unless opted in" philosophy --
 *      e-invoicing, licensing enforcement, reorder automation, and email
 *      outbox all default OFF/non-blocking (see CLAUDE.md). An install that
 *      doesn't care about cash-drawer tracking must see zero new friction
 *      at checkout. A future admin-configurable "require an open session to
 *      sell" setting could upgrade this to a hard gate later without any
 *      server-side change -- deliberately not built now (YAGNI).
 *
 * DOM is built via innerHTML template strings, matching subsystem-retail.js's
 * own established pattern throughout this product -- every value that could
 * contain free-text user input (movement reason) is escaped through _esc()
 * first, same policy as RetailSystem._esc().
 */
const CashDrawer = {
  _session: null,

  _esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  },
  _fmt(n) {
    const v = Number(n) || 0;
    return (v < 0 ? '-$' : '$') + Math.abs(v).toFixed(2);
  },

  async _get(url) { return RetailSystem._get(url); },
  async _post(url, body) { return RetailSystem._post(url, body); },

  // ── Mount point -- called once from RetailSystem._renderPOS(c), after the
  // POS DOM (including .pos-pane-hdr) already exists. Idempotent: safe to
  // call on every POS render (section switch, checkout refresh, etc.).
  async mount(container) {
    this._injectStyles();
    let bar = document.getElementById('cash-drawer-bar');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'cash-drawer-bar';
      const hdr = container.querySelector('.pos-pane-hdr');
      if (hdr && hdr.parentNode) hdr.parentNode.insertBefore(bar, hdr.nextSibling);
      else container.insertBefore(bar, container.firstChild);
    }
    await this.refresh();
  },

  async refresh() {
    try {
      const res = await this._get('/api/sub/retail/cash-sessions/current');
      this._session = (res && res.status === 'success' && res.data) ? res.data : null;
    } catch (e) {
      this._session = null;
    }
    this._renderBar();
  },

  _injectStyles() {
    if (document.getElementById('cd-styles')) return;
    const s = document.createElement('style');
    s.id = 'cd-styles';
    s.textContent = `
      .cd-bar { display:flex;align-items:center;justify-content:space-between;gap:12px;
        padding:8px 20px;font-size:12px;border-bottom:1px solid rgba(255,255,255,0.06); }
      .cd-bar-open { background:rgba(16,185,129,0.08);color:#a7f3d0; }
      .cd-bar-warn { background:rgba(251,191,36,0.10);color:#fde68a; }
      .cd-bar-actions { display:flex;gap:8px;flex-shrink:0; }
      .cd-variance-pos { color:#10b981;font-weight:700; }
      .cd-variance-neg { color:#ef4444;font-weight:700; }
      .cd-variance-zero { color:#cbd5e1;font-weight:700; }
      .cd-report-row { display:flex;justify-content:space-between;padding:7px 0;
        border-bottom:1px solid rgba(255,255,255,0.05);font-size:13px;color:#cbd5e1; }
      .cd-report-row b { color:#fff; }
    `;
    document.head.appendChild(s);
  },

  _renderBar() {
    const bar = document.getElementById('cash-drawer-bar');
    if (!bar) return;
    if (this._session) {
      const opened = String(this._session.opened_at || '').slice(0, 16).replace('T', ' ');
      bar.className = 'cd-bar cd-bar-open';
      bar.innerHTML = `
        <span>🟢 Cash session open since <b>${this._esc(opened)}</b> &middot; opening float ${this._fmt(this._session.opening_float)}</span>
        <span class="cd-bar-actions">
          <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="CashDrawer.openMovementModal()">+ Cash In/Out</button>
          <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="CashDrawer.openXReportModal()">X Report</button>
          <button class="ret-btn ret-btn-danger ret-btn-sm" onclick="CashDrawer.openCloseModal()">Close Shift (Z)</button>
        </span>`;
    } else {
      bar.className = 'cd-bar cd-bar-warn';
      bar.innerHTML = `
        <span>⚠️ No cash session open for this branch. Sales still work normally, but won't be attributed to a drawer count.</span>
        <span class="cd-bar-actions">
          <button class="ret-btn ret-btn-primary ret-btn-sm" onclick="CashDrawer.openOpenShiftModal()">Open Shift</button>
        </span>`;
    }
  },

  // ── Open shift ───────────────────────────────────────────────────────
  openOpenShiftModal() {
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'cd-open-modal';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:380px">
        <h3>Open Cash Shift</h3>
        <div class="ret-field">
          <label>Opening Float</label>
          <input type="number" id="cd-opening-float" min="0" step="0.01" value="0" />
        </div>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('cd-open-modal').remove()">Cancel</button>
          <button class="ret-btn ret-btn-primary" id="cd-open-btn" onclick="CashDrawer._submitOpenShift()">Open Shift</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
  },

  async _submitOpenShift() {
    const btn = document.getElementById('cd-open-btn');
    const val = parseFloat(document.getElementById('cd-opening-float')?.value || 0);
    if (isNaN(val) || val < 0) { SubsystemApp.showToast('Enter a valid opening float', 'error'); return; }
    if (btn) { btn.disabled = true; btn.textContent = 'Opening…'; }
    try {
      const res = await this._post('/api/sub/retail/cash-sessions/open', { opening_float: val });
      if (res.status === 'success') {
        document.getElementById('cd-open-modal')?.remove();
        SubsystemApp.showToast('Cash shift opened', 'success');
        this.refresh();
      } else {
        SubsystemApp.showToast(res.message || 'Failed to open shift', 'error');
        if (btn) { btn.disabled = false; btn.textContent = 'Open Shift'; }
      }
    } catch (e) {
      if (btn) { btn.disabled = false; btn.textContent = 'Open Shift'; }
    }
  },

  // ── Movement (float in/out, paid in/out) ────────────────────────────
  openMovementModal() {
    if (!this._session) return;
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'cd-mv-modal';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:400px">
        <h3>Cash Movement</h3>
        <div class="ret-field">
          <label>Type</label>
          <select id="cd-mv-type">
            <option value="float_in">Float In (add cash to drawer)</option>
            <option value="float_out">Float Out (remove cash from drawer)</option>
            <option value="paid_in">Paid In (misc. cash received)</option>
            <option value="paid_out">Paid Out (misc. cash paid)</option>
          </select>
        </div>
        <div class="ret-field">
          <label>Amount</label>
          <input type="number" id="cd-mv-amount" min="0.01" step="0.01" />
        </div>
        <div class="ret-field">
          <label>Reason</label>
          <input type="text" id="cd-mv-reason" placeholder="Optional note" maxlength="200" />
        </div>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('cd-mv-modal').remove()">Cancel</button>
          <button class="ret-btn ret-btn-primary" id="cd-mv-btn" onclick="CashDrawer._submitMovement()">Record</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
  },

  async _submitMovement() {
    if (!this._session) return;
    const btn = document.getElementById('cd-mv-btn');
    const type = document.getElementById('cd-mv-type')?.value;
    const amount = parseFloat(document.getElementById('cd-mv-amount')?.value || 0);
    const reason = document.getElementById('cd-mv-reason')?.value || '';
    if (!amount || amount <= 0) { SubsystemApp.showToast('Enter a valid amount', 'error'); return; }
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    try {
      const res = await this._post(`/api/sub/retail/cash-sessions/${encodeURIComponent(this._session.id)}/movements`, {
        type, amount, reason,
      });
      if (res.status === 'success') {
        document.getElementById('cd-mv-modal')?.remove();
        SubsystemApp.showToast('Cash movement recorded', 'success');
      } else {
        SubsystemApp.showToast(res.message || 'Failed to record movement', 'error');
        if (btn) { btn.disabled = false; btn.textContent = 'Record'; }
      }
    } catch (e) {
      if (btn) { btn.disabled = false; btn.textContent = 'Record'; }
    }
  },

  // ── X Report (live, non-destructive, callable any number of times) ──
  async openXReportModal() {
    if (!this._session) return;
    const res = await this._get(`/api/sub/retail/cash-sessions/${encodeURIComponent(this._session.id)}/x-report`);
    if (res.status !== 'success') { SubsystemApp.showToast(res.message || 'Failed to load X report', 'error'); return; }
    const r = res.data;
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'cd-x-modal';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:420px">
        <h3>X Report — mid-shift snapshot</h3>
        <p style="color:var(--text-muted);font-size:12px;margin-top:-14px">Non-destructive -- does not close the shift. Safe to check any time.</p>
        <div class="cd-report-row"><span>Opening float</span><b>${this._fmt(r.opening_float)}</b></div>
        <div class="cd-report-row"><span>Cash sales</span><b>+${this._fmt(r.cash_sales)}</b></div>
        <div class="cd-report-row"><span>Cash refunds</span><b>-${this._fmt(r.cash_refunds)}</b></div>
        <div class="cd-report-row"><span>Float in</span><b>+${this._fmt(r.movements.float_in)}</b></div>
        <div class="cd-report-row"><span>Float out</span><b>-${this._fmt(r.movements.float_out)}</b></div>
        <div class="cd-report-row"><span>Paid in</span><b>+${this._fmt(r.movements.paid_in)}</b></div>
        <div class="cd-report-row"><span>Paid out</span><b>-${this._fmt(r.movements.paid_out)}</b></div>
        <div class="cd-report-row" style="border-top:1px dashed rgba(255,255,255,0.15);margin-top:8px;padding-top:12px;font-size:15px">
          <span>Expected cash in drawer</span><b>${this._fmt(r.expected_cash)}</b>
        </div>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('cd-x-modal').remove()">Close</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
  },

  // ── Close shift / Z report (locks the session) ──────────────────────
  async openCloseModal() {
    if (!this._session) return;
    const res = await this._get(`/api/sub/retail/cash-sessions/${encodeURIComponent(this._session.id)}/x-report`);
    const expected = res.status === 'success' ? res.data.expected_cash : 0;
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'cd-close-modal';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:420px">
        <h3>Close Shift — Z Report</h3>
        <div class="cd-report-row"><span>Expected cash</span><b id="cd-close-expected">${this._fmt(expected)}</b></div>
        <div class="ret-field">
          <label>Counted Cash (physical count)</label>
          <input type="number" id="cd-close-counted" min="0" step="0.01" oninput="CashDrawer._previewVariance(${expected})" />
        </div>
        <div id="cd-close-variance" class="cd-report-row" style="display:none">
          <span>Variance (counted − expected)</span><b id="cd-close-variance-val"></b>
        </div>
        <p style="color:var(--text-muted);font-size:12px">Closing locks this session -- it cannot be reopened or edited afterward.</p>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('cd-close-modal').remove()">Cancel</button>
          <button class="ret-btn ret-btn-danger" id="cd-close-btn" onclick="CashDrawer._submitClose(${expected})">Confirm Close</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
  },

  _previewVariance(expected) {
    const counted = parseFloat(document.getElementById('cd-close-counted')?.value);
    const row = document.getElementById('cd-close-variance');
    const val = document.getElementById('cd-close-variance-val');
    if (!row || !val) return;
    if (isNaN(counted)) { row.style.display = 'none'; return; }
    const variance = Math.round((counted - expected) * 100) / 100;
    row.style.display = 'flex';
    val.textContent = (variance > 0 ? '+' : '') + this._fmt(variance);
    val.className = variance === 0 ? 'cd-variance-zero' : (variance > 0 ? 'cd-variance-pos' : 'cd-variance-neg');
  },

  async _submitClose(expected) {
    if (!this._session) return;
    const btn = document.getElementById('cd-close-btn');
    const counted = parseFloat(document.getElementById('cd-close-counted')?.value);
    if (isNaN(counted) || counted < 0) { SubsystemApp.showToast('Enter the counted cash amount', 'error'); return; }
    if (btn) { btn.disabled = true; btn.textContent = 'Closing…'; }
    try {
      const res = await this._post(`/api/sub/retail/cash-sessions/${encodeURIComponent(this._session.id)}/close`, {
        closing_float_counted: counted,
      });
      if (res.status === 'success') {
        document.getElementById('cd-close-modal')?.remove();
        const variance = (res.data && res.data.report && typeof res.data.report.variance === 'number')
          ? res.data.report.variance : (counted - expected);
        SubsystemApp.showToast(
          `Shift closed. Variance: ${variance > 0 ? '+' : ''}${variance.toFixed(2)}`, 'success'
        );
        this.refresh();
      } else {
        SubsystemApp.showToast(res.message || 'Failed to close shift', 'error');
        if (btn) { btn.disabled = false; btn.textContent = 'Confirm Close'; }
      }
    } catch (e) {
      if (btn) { btn.disabled = false; btn.textContent = 'Confirm Close'; }
    }
  },
};

window.CashDrawer = CashDrawer;
