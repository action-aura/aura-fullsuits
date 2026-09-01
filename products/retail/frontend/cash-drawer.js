/**
 * Aura Retail -- Shift / Cash-Drawer Management (feat/shift-cash-drawer,
 * made terminal-aware by Phase 4).
 *
 * Self-contained, feature-scoped file -- same convention as
 * products/retail/frontend/einvoicing.js / import-wizard.js: this product's
 * frontend has no build step, so a new feature gets its own <script> file
 * rather than growing subsystem-retail.js (already ~5000 lines) further.
 * Unlike einvoicing.js (which is fully standalone), this file DOES hook into
 * RetailSystem's POS screen -- that's the whole point of a cash-drawer
 * gate/banner -- via a single explicit call RetailSystem._renderPOS makes
 * to `CashDrawer.mount(c)` at the end of its own render, not by
 * monkey-patching RetailSystem from here.
 *
 * ── WHAT PHASE 4 CHANGED HERE, AND WHY EACH CHANGE IS NOT COSMETIC ────────
 *
 * 1. THE DRAWER NOW SAYS WHICH TILL IT IS. It could not before, because there
 *    was only ever one drawer per BRANCH and the question had no answer. A
 *    shop with a desktop and a phone saw one bar on both devices, describing
 *    one drawer, with a "Close Shift (Z)" button on each of them wired to the
 *    same session -- and whichever cashier pressed it first closed the other
 *    one's shift. Every state below now names its terminal.
 *
 * 2. A FORCE-CLOSED DRAWER LOOKS DIFFERENT FROM AN ENDED ONE. A session that
 *    went out of service without anybody counting it has an UNKNOWN variance,
 *    not a variance of zero, and 0.00 is precisely the number a shop reads as
 *    "that drawer was fine". It is rendered as "not counted", in the warning
 *    palette, with no number at all -- see _varianceCell().
 *
 * 3. THE EMPTY STATE IS HONEST. "No drawer is open on this terminal",
 *    "you do not operate a drawer on this terminal" and "the drawer could not
 *    be loaded" are three different facts with three different next actions,
 *    and this file used to render all three as the second one by catching
 *    every failure into `this._session = null`. A cashier whose network had
 *    dropped was told, confidently, that their till had no shift open.
 *
 * 4. TOKENS, NOT LITERALS. The injected stylesheet here was written for the
 *    near-black HUD this product used to be: `#a7f3d0` on `rgba(16,185,129,
 *    .08)`, hairlines drawn as `rgba(255,255,255,0.06)`. On the Operational
 *    Calm light palette those are a pale mint on near-white and a hairline
 *    that is invisible -- the same class of defect, in the same shape, as the
 *    1.48:1 "Held" button retail_design_render_test.js exists because of.
 *    Every colour, space, radius and weight below is a token.
 *
 * 5. IT SPEAKS ARABIC. Every string here was a bare literal in neither
 *    catalog; an Arabic install saw an English drawer bar sitting inside an
 *    Arabic till. Money goes through RetailSystem._money(), which emits
 *    `<span class="money">` -- tabular figures, slashed zero, and the
 *    `direction:ltr` isolation rtl.css gives that class, which is what stops
 *    a signed variance reading backwards next to Arabic text. The
 *    over/short/balanced qualifier beside it is a real translated WORD, so
 *    the sign survives a greyscale screenshot and a colourblind reader.
 *
 * ── Hard gate vs. soft warning (deliberate choice: SOFT WARNING) ─────────
 * A terminal with no open cash session shows a dismissible-by-action banner
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
 * contain free-text user input (movement reason, a terminal id from another
 * device) is escaped through RetailSystem._esc() first.
 *
 * ── Why the helpers below DELEGATE rather than reimplement ────────────────
 * `_esc`, `_money`, `_fmt` and `_bdi` are RetailSystem's. This file used to
 * carry its own `_esc` and its own `_fmt`, and the `_fmt` had already drifted:
 * it emitted an ASCII hyphen with no `.money` wrapper, so a negative variance
 * on the till got none of the three non-colour cues main.css supplies (the
 * U+2212 glyph, the bold weight, the accounting parentheses) and none of the
 * tabular alignment. Two spellings of "how this product prints money" is how
 * that happens, so there is now one. RetailSystem is definitionally loaded --
 * it is what calls mount() -- so there is nothing to fall back to.
 */
const CashDrawer = {
  /** The last successful GET /cash-sessions/current, or null. */
  _session: null,

  /**
   * What the drawer bar is currently able to say. Four values, because there
   * are four genuinely different facts and collapsing any two of them is a
   * lie to whoever is standing at the till:
   *
   *   'open'      this terminal has a drawer open      -> work it
   *   'none'      this terminal has no drawer open     -> open one
   *   'denied'    you do not hold retail.cash.close    -> nothing to do here
   *   'error'     the request did not answer           -> retry
   *
   * Initial value is 'error' rather than 'none' on purpose: before the first
   * fetch resolves, "no drawer is open" is not something this file knows.
   */
  _state: 'error',
  /** Server-reported terminal identity for THIS device. */
  _terminal: { id: null, short: null },
  /** Open drawers on this branch belonging to OTHER terminals. */
  _otherTerminalsOpen: 0,

  _esc(v) { return RetailSystem._esc(v); },
  /** Plain string, ASCII hyphen -- for toasts and button labels only. */
  _fmt(n) { return RetailSystem._fmt(n); },
  /** `<span class="money">` -- for an amount composed into larger markup. */
  _money(n, cls) { return RetailSystem._money(n, cls); },
  _bdi(text, full, dir) { return RetailSystem._bdi(text, full, dir); },

  async _get(url) { return RetailSystem._get(url); },
  async _post(url, body) { return RetailSystem._post(url, body); },

  // ── Terminal identity, rendered ──────────────────────────────────────────
  //
  // Three cases, and the third is the one that has to stay visible rather
  // than being quietly folded into the second. A device that has never
  // established a local device identity has NO terminal id, so its drawer is
  // scoped to "the unidentified till" -- which two such devices would share.
  // That is strictly better than the branch-wide fold this phase replaces and
  // it is still not right, so the screen says so instead of printing a
  // confident label over it.
  _terminalLabel(session) {
    if (!session) return '';
    if (session.is_this_terminal) return t('This terminal');
    if (!session.terminal_short) return t('Unidentified terminal');
    // `full` carries the whole uuid into the title attribute: a truncated id
    // cannot be matched against device_registry.devices, and "which till"
    // questions are asked precisely when something has gone wrong.
    return t('Till') + ' ' + this._bdi(session.terminal_short, session.terminal_id);
  },

  /** This device's own identity, for the states where there is no session. */
  _thisTerminalLabel() {
    if (!this._terminal.short) return t('Unidentified terminal');
    return t('Till') + ' ' + this._bdi(this._terminal.short, this._terminal.id);
  },

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

  /**
   * Ask the server which drawer is open ON THIS TERMINAL.
   *
   * THE CATCH-ALL THAT USED TO BE HERE WAS THE BUG. `catch (e) { this._session
   * = null; }` turned a dropped connection, a 500 and a permission refusal
   * into the same screen: "no cash session open for this branch", with an
   * "Open Shift" button that would fail for the same reason the read just
   * did. Each failure now keeps its own name, and only a genuine `data: null`
   * from a successful response means "no drawer".
   */
  async refresh() {
    try {
      const res = await this._get('/api/sub/retail/cash-sessions/current');
      if (res && res.status === 'success') {
        this._session = res.data || null;
        this._state = this._session ? 'open' : 'none';
        this._terminal = { id: res.terminal_id || null, short: res.terminal_short || null };
        this._otherTerminalsOpen = res.other_terminals_open || 0;
      } else if (res && res.code === 403) {
        // The capability refusal carries `code: 403` (mt_auth's
        // _capability_denied_response). This is not an error to retry; the
        // account simply does not work a drawer.
        this._session = null;
        this._state = 'denied';
      } else {
        this._session = null;
        this._state = 'error';
      }
    } catch (e) {
      this._session = null;
      this._state = 'error';
    }
    this._renderBar();
  },

  // ── Styles ───────────────────────────────────────────────────────────────
  //
  // Every value is a token. The previous version of this block was written
  // for a near-black HUD and shipped unchanged onto the light Operational
  // Calm palette, where `color:#a7f3d0` on `background:rgba(16,185,129,.08)`
  // over white is roughly 1.5:1 -- unreadable, and invisible to
  // retail_design_contrast_test.js because that suite resolves the RENDERED
  // corpus and this file has never been in it. It is now, via
  // retail_drawer_screen_test.js.
  _injectStyles() {
    if (document.getElementById('cd-styles')) return;
    const s = document.createElement('style');
    s.id = 'cd-styles';
    s.textContent = `
      .cd-bar { display:flex;align-items:center;justify-content:space-between;
        gap:var(--space-base);padding:var(--space-snug) var(--space-roomy);
        font-size:var(--text-size-meta);line-height:var(--text-leading-body);
        border-bottom:1px solid var(--border-hairline);
        color:var(--text-secondary);background:var(--surface-raised); }
      .cd-bar-open { background:var(--state-success-surface);
        border-bottom-color:var(--state-success-border);color:var(--text-secondary); }
      .cd-bar-warn { background:var(--state-warning-surface);
        border-bottom-color:var(--state-warning-border);color:var(--text-secondary); }
      .cd-bar-error { background:var(--state-danger-surface);
        border-bottom-color:var(--state-danger-border);color:var(--text-secondary); }
      .cd-bar-info { background:var(--state-info-surface);
        border-bottom-color:var(--state-info-border);color:var(--text-secondary); }
      .cd-bar b, .cd-bar strong { color:var(--text-primary);
        font-weight:var(--text-weight-semibold); }
      .cd-bar-actions { display:flex;gap:var(--space-snug);flex-shrink:0; }

      /* WHICH TILL. A pill rather than a run of body text because it is the
         one fact on this bar that answers "is this mine?", and it is read at
         a glance from arm's length. */
      .cd-terminal { display:inline-block;padding:var(--space-hairline) var(--space-snug);
        border-radius:var(--radius-pill);border:1px solid var(--border-default);
        background:var(--surface-till);color:var(--text-primary);
        font-weight:var(--text-weight-semibold);font-size:var(--text-size-micro); }
      .cd-terminal-mine { border-color:var(--accent-action);
        background:var(--surface-accent-soft);color:var(--accent-action); }

      .cd-report-row { display:flex;justify-content:space-between;
        gap:var(--space-base);padding:var(--space-snug) 0;
        border-bottom:1px solid var(--border-hairline);
        font-size:var(--text-size-body);color:var(--text-secondary); }
      .cd-report-row b { color:var(--text-primary);font-weight:var(--text-weight-semibold); }
      .cd-report-total { border-top:1px solid var(--border-default);
        margin-top:var(--space-snug);padding-top:var(--space-base);
        font-size:var(--text-size-body-lg); }

      /* Status of a drawer's variance. COLOUR IS NEVER THE ONLY CUE: each of
         these carries a translated WORD, and the border weight differs, so a
         greyscale screenshot and a colourblind reader both still separate
         "accepted" from "nobody counted this". */
      .cd-badge { display:inline-block;padding:var(--space-hairline) var(--space-snug);
        border-radius:var(--radius-pill);font-size:var(--text-size-micro);
        font-weight:var(--text-weight-semibold);border:1px solid transparent;
        white-space:nowrap; }
      .cd-badge-approved { background:var(--state-success-surface);
        color:var(--state-success-text);border-color:var(--state-success-border); }
      .cd-badge-unverified { background:var(--state-info-surface);
        color:var(--state-info-text);border-color:var(--state-info-border); }
      .cd-badge-notcounted { background:var(--state-warning-surface);
        color:var(--state-warning-text);border-color:var(--state-warning-border); }
      .cd-badge-open { background:var(--surface-sunken);
        color:var(--text-tertiary);border-color:var(--border-default); }

      /* The "nobody counted this" cell. Deliberately NOT a number: a
         force-closed drawer's variance is UNKNOWN, and printing 0.00 there is
         the single most misleading thing this screen could do. */
      .cd-uncounted { color:var(--state-warning-text);
        font-weight:var(--text-weight-semibold); }

      .cd-note { color:var(--text-tertiary);font-size:var(--text-size-meta);
        margin:var(--space-snug) 0 0; }
      .cd-warn-note { color:var(--state-warning-text);font-size:var(--text-size-meta);
        margin:var(--space-snug) 0 0;font-weight:var(--text-weight-medium); }

      .cd-table { width:100%;border-collapse:collapse;font-size:var(--text-size-meta); }
      .cd-table th { text-align:start;padding:var(--space-snug);
        color:var(--text-tertiary);font-weight:var(--text-weight-semibold);
        border-bottom:1px solid var(--border-default);
        font-size:var(--text-size-micro);text-transform:uppercase; }
      .cd-table td { padding:var(--space-snug);color:var(--text-secondary);
        border-bottom:1px solid var(--border-hairline);vertical-align:middle; }
      .cd-table tr.cd-row-mine td { background:var(--surface-accent-soft); }
      .cd-empty { padding:var(--space-roomy);text-align:center;
        color:var(--text-tertiary);font-size:var(--text-size-body); }
    `;
    document.head.appendChild(s);
  },

  // ── The bar ──────────────────────────────────────────────────────────────
  _renderBar() {
    const bar = document.getElementById('cash-drawer-bar');
    if (!bar) return;
    // Same "flip only on a real transition" discipline as app-shell.js's
    // sync banner (_syncBannerTier) and subsystem-retail.js's Exceptions
    // panels (_exqLastState): open/closed is a live state a cashier watches
    // all shift, so its icon should mark the MOMENT it flips, not pulse on
    // every poll that finds it unchanged.
    const changed = this._state !== this._lastBarState;
    this._lastBarState = this._state;
    const renderers = {
      open: () => this._barOpen(changed),
      none: () => this._barNone(changed),
      denied: () => this._barDenied(),
      error: () => this._barError(),
    };
    const { cls, html } = (renderers[this._state] || renderers.error)();
    bar.className = 'cd-bar ' + cls;
    bar.innerHTML = html;
  },

  // Renders the drawer-state icon through icons.js, falling back to '' when
  // AuraIcons isn't loaded (retail_drawer_screen_test.js's vm sandbox loads
  // this file standalone, without icons.js).
  _stateIcon(name, changed) {
    return window.AuraIcons ? AuraIcons.render(name, 15, changed ? { animate: 'flip' } : undefined) : '';
  },

  _barOpen(changed) {
    const s = this._session;
    const opened = String(s.opened_at || '').slice(0, 16).replace('T', ' ');
    // Two facts, in this order: WHICH till, then since when. The till comes
    // first because on a two-device shop it is the one that decides whether
    // the rest of the bar is about you at all.
    return {
      cls: 'cd-bar-open',
      html: `
        <span>
          ${this._stateIcon('lock-open', changed)}
          <span class="cd-terminal cd-terminal-mine">${this._terminalLabel(s)}</span>
          ${t('Drawer open since')} <b>${this._bdi(opened)}</b>
          &middot; ${t('Opening float')} ${this._money(s.opening_float)}
        </span>
        <span class="cd-bar-actions">
          <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="CashDrawer.openMovementModal()">${t('Cash In/Out')}</button>
          <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="CashDrawer.openXReportModal()">${t('X Report')}</button>
          <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="CashDrawer.openHistoryModal()">${t('Drawer History')}</button>
          <button class="ret-btn ret-btn-danger ret-btn-sm" onclick="CashDrawer.openCloseModal()">${t('End Shift (Z)')}</button>
        </span>`,
    };
  },

  _barNone(changed) {
    // "...and two other tills ARE trading" is materially different from "the
    // shop is shut". It is what tells a cashier that they, specifically, are
    // about to sell into nothing while everyone else is counted.
    const others = this._otherTerminalsOpen > 0
      ? ` <span class="cd-note">${t('Other terminals have a drawer open on this branch.')}</span>`
      : '';
    return {
      cls: 'cd-bar-warn',
      html: `
        <span>
          ${this._stateIcon('lock', changed)}
          <span class="cd-terminal">${this._thisTerminalLabel()}</span>
          ${t('No cash drawer is open on this terminal. Sales still work normally, but will not be attributed to a drawer count.')}
          ${others}
        </span>
        <span class="cd-bar-actions">
          <button class="ret-btn ret-btn-primary ret-btn-sm" onclick="CashDrawer.openOpenShiftModal()">${t('Open Shift')}</button>
        </span>`,
    };
  },

  _barDenied() {
    // No action button. Offering "Open Shift" to somebody the server will
    // refuse is the interface promising something it cannot deliver, and the
    // refusal would arrive as a toast with no explanation of what to do next.
    return {
      cls: 'cd-bar-info',
      html: `
        <span>
          <span class="cd-terminal">${this._thisTerminalLabel()}</span>
          ${t('You do not operate a cash drawer on this terminal.')}
        </span>`,
    };
  },

  _barError() {
    // NOT "no drawer is open". This file does not know whether a drawer is
    // open; it knows that it asked and did not get an answer.
    return {
      cls: 'cd-bar-error',
      html: `
        <span>${t('Could not load the cash drawer. Its state is unknown.')}</span>
        <span class="cd-bar-actions">
          <button class="ret-btn ret-btn-ghost ret-btn-sm" onclick="CashDrawer.refresh()">${t('Retry')}</button>
        </span>`,
    };
  },

  // ── Variance, rendered ───────────────────────────────────────────────────
  //
  // THE ONE CELL THIS WHOLE SCREEN EXISTS FOR, and the one place a number
  // must be allowed to be absent.
  //
  //   not_counted  no number at all. Nobody counted this drawer, so its
  //                variance is UNKNOWN. `sess.variance` is already NULL for
  //                these rows and the backend refuses to coerce it; this is
  //                the matching refusal on the screen.
  //   otherwise    the amount, through RetailSystem._money() so it gets
  //                tabular figures, the U+2212 glyph, the bold weight and the
  //                accounting parentheses -- three non-colour cues -- plus a
  //                translated WORD saying which way it went, which is what
  //                carries the sign for a reader who cannot see the colour
  //                and what keeps the sign attached to the number when the
  //                surrounding paragraph is Arabic.
  _varianceCell(sess) {
    if (sess.variance_status === 'not_counted') {
      return `<span class="cd-uncounted">${t('Not counted')}</span>`;
    }
    if (sess.variance == null) return `<span class="cd-note">${t('Not counted')}</span>`;
    const v = Number(sess.variance) || 0;
    const word = v > 0 ? t('over') : (v < 0 ? t('short') : t('balanced'));
    return `${this._money(v)} <span class="cd-note">${word}</span>`;
  },

  _statusBadge(sess) {
    const map = {
      approved: ['cd-badge-approved', 'Variance accepted'],
      unverified: ['cd-badge-unverified', 'Awaiting approval'],
      not_counted: ['cd-badge-notcounted', 'Force-closed, never counted'],
      pending: ['cd-badge-open', 'Open'],
    };
    const [cls, label] = map[sess.variance_status] || map.pending;
    return `<span class="cd-badge ${cls}">${t(label)}</span>`;
  },

  // ── Open shift ───────────────────────────────────────────────────────────
  openOpenShiftModal() {
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'cd-open-modal';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:380px">
        <h3>${t('Open Cash Shift')}</h3>
        <p class="cd-note">${t('This drawer belongs to')} <span class="cd-terminal">${this._thisTerminalLabel()}</span></p>
        <div class="ret-field">
          <label>${t('Opening Float')}</label>
          <input type="number" id="cd-opening-float" min="0" step="0.01" value="0" />
        </div>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('cd-open-modal').remove()">${t('Cancel')}</button>
          <button class="ret-btn ret-btn-primary" id="cd-open-btn" onclick="CashDrawer._submitOpenShift()">${t('Open Shift')}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
  },

  async _submitOpenShift() {
    const btn = document.getElementById('cd-open-btn');
    const val = parseFloat(document.getElementById('cd-opening-float')?.value || 0);
    if (isNaN(val) || val < 0) { SubsystemApp.showToast(t('Enter a valid opening float'), 'error'); return; }
    if (btn) { btn.disabled = true; btn.textContent = t('Opening…'); }
    try {
      const res = await this._post('/api/sub/retail/cash-sessions/open', { opening_float: val });
      if (res.status === 'success') {
        document.getElementById('cd-open-modal')?.remove();
        SubsystemApp.showToast(t('Cash shift opened'), 'success');
        this.refresh();
      } else {
        SubsystemApp.showToast(res.message || t('Failed to open shift'), 'error');
        if (btn) { btn.disabled = false; btn.textContent = t('Open Shift'); }
      }
    } catch (e) {
      SubsystemApp.showToast(t('Could not load the cash drawer. Its state is unknown.'), 'error');
      if (btn) { btn.disabled = false; btn.textContent = t('Open Shift'); }
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
        <h3>${t('Cash Movement')}</h3>
        <p class="cd-note">${t('This drawer belongs to')} <span class="cd-terminal cd-terminal-mine">${this._terminalLabel(this._session)}</span></p>
        <div class="ret-field">
          <label>${t('Type')}</label>
          <select id="cd-mv-type">
            <option value="float_in">${t('Float In (add cash to drawer)')}</option>
            <option value="float_out">${t('Float Out (remove cash from drawer)')}</option>
            <option value="paid_in">${t('Paid In (misc. cash received)')}</option>
            <option value="paid_out">${t('Paid Out (misc. cash paid)')}</option>
          </select>
        </div>
        <div class="ret-field">
          <label>${t('Amount')}</label>
          <input type="number" id="cd-mv-amount" min="0.01" step="0.01" />
        </div>
        <div class="ret-field">
          <label>${t('Reason')}</label>
          <input type="text" id="cd-mv-reason" data-i18n-ph="Optional note" placeholder="${t('Optional note')}" maxlength="200" />
        </div>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('cd-mv-modal').remove()">${t('Cancel')}</button>
          <button class="ret-btn ret-btn-primary" id="cd-mv-btn" onclick="CashDrawer._submitMovement()">${t('Record')}</button>
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
    if (!amount || amount <= 0) { SubsystemApp.showToast(t('Enter a valid amount'), 'error'); return; }
    if (btn) { btn.disabled = true; btn.textContent = t('Saving…'); }
    try {
      const res = await this._post(`/api/sub/retail/cash-sessions/${encodeURIComponent(this._session.id)}/movements`, {
        type, amount, reason,
      });
      if (res.status === 'success') {
        document.getElementById('cd-mv-modal')?.remove();
        SubsystemApp.showToast(t('Cash movement recorded'), 'success');
      } else {
        SubsystemApp.showToast(res.message || t('Failed to record movement'), 'error');
        if (btn) { btn.disabled = false; btn.textContent = t('Record'); }
      }
    } catch (e) {
      SubsystemApp.showToast(t('Could not load the cash drawer. Its state is unknown.'), 'error');
      if (btn) { btn.disabled = false; btn.textContent = t('Record'); }
    }
  },

  // ── X Report (live, non-destructive, callable any number of times) ──
  //
  // Shared by the X modal and the close-out modal, because they show the same
  // arithmetic and having two renderers is how they come to disagree.
  _reportRows(r) {
    return `
      <div class="cd-report-row"><span>${t('Opening float')}</span><b>${this._money(r.opening_float)}</b></div>
      <div class="cd-report-row"><span>${t('Cash sales')}</span><b>${this._money(r.cash_sales)}</b></div>
      <div class="cd-report-row"><span>${t('Cash refunds')}</span><b>${this._money(-Math.abs(Number(r.cash_refunds) || 0))}</b></div>
      <div class="cd-report-row"><span>${t('Float in')}</span><b>${this._money(r.movements.float_in)}</b></div>
      <div class="cd-report-row"><span>${t('Float out')}</span><b>${this._money(-Math.abs(Number(r.movements.float_out) || 0))}</b></div>
      <div class="cd-report-row"><span>${t('Paid in')}</span><b>${this._money(r.movements.paid_in)}</b></div>
      <div class="cd-report-row"><span>${t('Paid out')}</span><b>${this._money(-Math.abs(Number(r.movements.paid_out) || 0))}</b></div>
      <div class="cd-report-row cd-report-total">
        <span>${t('Expected cash in drawer')}</span><b>${this._money(r.expected_cash)}</b>
      </div>
      ${this._contaminationNote(r)}`;
  },

  /**
   * The disclosure that makes a legacy Z report readable rather than merely
   * wrong.
   *
   * `_open_cash_session_id` now stamps a sale with the session THIS terminal
   * opened, so nothing written from here on can land in another till's drawer.
   * What that cannot do is un-write history: on a shop that has been running
   * two devices, every session opened before this phase has other terminals'
   * sales inside it, permanently, and the arithmetic above will total them
   * because they are exactly the rows the FK says belong here. The server
   * counts them (`foreign_terminal_sales`) and this says so, because a number
   * that is wrong for a knowable reason has to carry the reason.
   *
   * -1 means the server could not determine it, which is NOT the same as 0.
   */
  _contaminationNote(r) {
    const n = Number(r.foreign_terminal_sales);
    if (n === -1) {
      return `<p class="cd-warn-note">${t('Could not check whether other terminals sold into this drawer.')}</p>`;
    }
    if (!n || n <= 0) return '';
    return `<p class="cd-warn-note">${t('Sales rung on another terminal are included in this drawer:')}
      <b>${this._bdi(String(n))}</b>. ${t('They were recorded before drawers were bound to a terminal and cannot be separated now.')}</p>`;
  },

  async openXReportModal() {
    if (!this._session) return;
    let res;
    try {
      res = await this._get(`/api/sub/retail/cash-sessions/${encodeURIComponent(this._session.id)}/x-report`);
    } catch (e) {
      SubsystemApp.showToast(t('Could not load the cash drawer. Its state is unknown.'), 'error');
      return;
    }
    if (!res || res.status !== 'success') {
      SubsystemApp.showToast((res && res.message) || t('Failed to load X report'), 'error');
      return;
    }
    const r = res.data;
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'cd-x-modal';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:440px">
        <h3>${t('X Report — mid-shift snapshot')}</h3>
        <p class="cd-note">
          <span class="cd-terminal cd-terminal-mine">${this._terminalLabel(r)}</span>
          ${t('Non-destructive -- does not end the shift. Safe to check any time.')}
        </p>
        ${this._reportRows(r)}
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('cd-x-modal').remove()">${t('Close')}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
  },

  // ── End shift / Z report ────────────────────────────────────────────
  //
  // "End", not "Close", and the word is load-bearing rather than cosmetic.
  // Ending is what the cashier does: they count the drawer and the count is
  // locked. Whether the resulting variance is ACCEPTED is a different
  // authority (retail.cash.approve), withheld from every role that can end a
  // shift so that nobody signs off their own shortfall. A button labelled
  // "Close" promised the second thing while doing only the first.
  async openCloseModal() {
    if (!this._session) return;
    let expected = 0;
    let report = null;
    try {
      const res = await this._get(`/api/sub/retail/cash-sessions/${encodeURIComponent(this._session.id)}/x-report`);
      if (res && res.status === 'success') { report = res.data; expected = res.data.expected_cash; }
    } catch (e) { report = null; }
    if (!report) {
      // Refusing to open the modal is the right failure. A close-out screen
      // whose "expected" silently reads 0.00 because the fetch failed would
      // report the entire drawer as a shortfall.
      SubsystemApp.showToast(t('Could not load the cash drawer. Its state is unknown.'), 'error');
      return;
    }
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'cd-close-modal';
    overlay.innerHTML = `
      <div class="ret-modal" style="width:440px">
        <h3>${t('End Shift — Z Report')}</h3>
        <p class="cd-note">
          <span class="cd-terminal cd-terminal-mine">${this._terminalLabel(report)}</span>
        </p>
        <div class="cd-report-row"><span>${t('Expected cash')}</span><b id="cd-close-expected">${this._money(expected)}</b></div>
        <div class="ret-field">
          <label>${t('Counted Cash (physical count)')}</label>
          <input type="number" id="cd-close-counted" min="0" step="0.01" oninput="CashDrawer._previewVariance(${expected})" />
        </div>
        <div id="cd-close-variance" class="cd-report-row" style="display:none">
          <span>${t('Variance (counted − expected)')}</span><b id="cd-close-variance-val"></b>
        </div>
        ${this._contaminationNote(report)}
        <p class="cd-note">${t('Ending locks this count. A short drawer never blocks you from ending your shift -- the variance is recorded and someone with approval authority reviews it.')}</p>
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('cd-close-modal').remove()">${t('Cancel')}</button>
          <button class="ret-btn ret-btn-danger" id="cd-close-btn" onclick="CashDrawer._submitClose(${expected})">${t('End Shift')}</button>
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
    // innerHTML, not textContent: the value has to arrive wrapped in
    // `.money` or it loses tabular figures, the typographic minus, the
    // accounting parentheses AND rtl.css's direction isolation -- the exact
    // set of cues that make a signed amount readable next to Arabic text.
    // Both halves are locally generated (a number and a catalog string), so
    // there is no untrusted input on this path.
    val.innerHTML = this._varianceCell({ variance, variance_status: 'unverified' });
  },

  async _submitClose(expected) {
    if (!this._session) return;
    const btn = document.getElementById('cd-close-btn');
    const counted = parseFloat(document.getElementById('cd-close-counted')?.value);
    if (isNaN(counted) || counted < 0) { SubsystemApp.showToast(t('Enter the counted cash amount'), 'error'); return; }
    if (btn) { btn.disabled = true; btn.textContent = t('Ending…'); }
    try {
      const res = await this._post(`/api/sub/retail/cash-sessions/${encodeURIComponent(this._session.id)}/close`, {
        closing_float_counted: counted,
      });
      if (res.status === 'success') {
        document.getElementById('cd-close-modal')?.remove();
        const report = (res.data && res.data.report) || {};
        const variance = (typeof report.variance === 'number') ? report.variance : (counted - expected);
        // Two different outcomes, said differently. A cashier whose shift is
        // waiting on approval must not be told it is "closed" -- they will
        // report it as done and nobody will look at the shortfall.
        const msg = report.variance_status === 'approved'
          ? t('Shift ended and variance accepted.')
          : t('Shift ended. The variance is recorded and awaiting approval.');
        SubsystemApp.showToast(`${msg} ${this._fmt(variance)}`, 'success');
        this.refresh();
      } else {
        SubsystemApp.showToast(res.message || t('Failed to end shift'), 'error');
        if (btn) { btn.disabled = false; btn.textContent = t('End Shift'); }
      }
    } catch (e) {
      SubsystemApp.showToast(t('Could not load the cash drawer. Its state is unknown.'), 'error');
      if (btn) { btn.disabled = false; btn.textContent = t('End Shift'); }
    }
  },

  // ── Drawer history, and the approval surface ─────────────────────────────
  //
  // WHY THIS IS A MODAL AND NOT A SCREEN. RetailSystem.render()'s `case`
  // labels are the authority retail_design_render_test.js closes its corpus
  // against: a new router section that is not also built into that corpus
  // fails the suite, deliberately, because that is how the Reports page
  // shipped white-on-white. A drawer review surface does not need to be a
  // route -- it is reached from the drawer it is about -- so it is a modal,
  // and it is covered by retail_drawer_screen_test.js instead.
  //
  // WHAT THE LIST CONTAINS depends on the caller's authority, decided by the
  // SERVER: this terminal's own drawers for a cashier, every terminal's for
  // somebody holding retail.reports or retail.cash.approve. The client does
  // not ask for a scope and cannot widen it.
  async openHistoryModal() {
    let res;
    try {
      res = await this._get('/api/sub/retail/cash-sessions?limit=50');
    } catch (e) {
      SubsystemApp.showToast(t('Could not load the cash drawer. Its state is unknown.'), 'error');
      return;
    }
    if (!res || res.status !== 'success') {
      SubsystemApp.showToast((res && res.message) || t('Failed to load drawer history'), 'error');
      return;
    }
    const rows = res.data || [];
    const mayApprove = !!res.may_approve;
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'cd-hist-modal';
    overlay.innerHTML = `
      <div class="ret-modal ret-modal-wide" style="width:760px">
        <h3>${t('Drawer History')}</h3>
        <p class="cd-note">${res.scope === 'all_terminals'
          ? t('Every terminal in this shop.')
          : t('Drawers worked on this terminal.')}</p>
        ${rows.length === 0
          ? `<div class="cd-empty">${t('No drawers have been opened yet.')}</div>`
          : `<table class="cd-table">
              <thead><tr>
                <th>${t('Terminal')}</th><th>${t('Opened')}</th>
                <th>${t('Expected')}</th><th>${t('Counted')}</th>
                <th>${t('Variance')}</th><th>${t('Status')}</th><th></th>
              </tr></thead>
              <tbody>${rows.map((s) => this._historyRow(s, mayApprove)).join('')}</tbody>
            </table>`}
        <div class="ret-modal-footer">
          <button class="ret-btn ret-btn-ghost" onclick="document.getElementById('cd-hist-modal').remove()">${t('Close')}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
  },

  _historyRow(s, mayApprove) {
    const opened = String(s.opened_at || '').slice(0, 16).replace('T', ' ');
    const canApprove = mayApprove && s.variance_status === 'unverified';
    const canAckUncounted = mayApprove && s.variance_status === 'not_counted';
    let action = '';
    if (canApprove) {
      action = `<button class="ret-btn ret-btn-primary ret-btn-sm"
        onclick="CashDrawer.approve('${this._esc(s.id)}', false)">${t('Accept variance')}</button>`;
    } else if (canAckUncounted) {
      // A DIFFERENT BUTTON, saying a different thing, calling the same route
      // with the explicit acknowledgement flag it requires. Accepting a
      // shortfall somebody counted and accepting that a drawer was never
      // counted at all are not the same decision, and one button for both
      // would make the second one reachable by muscle memory.
      action = `<button class="ret-btn ret-btn-ghost ret-btn-sm"
        onclick="CashDrawer.approve('${this._esc(s.id)}', true)">${t('Acknowledge uncounted')}</button>`;
    }
    return `<tr class="${s.is_this_terminal ? 'cd-row-mine' : ''}">
      <td><span class="cd-terminal ${s.is_this_terminal ? 'cd-terminal-mine' : ''}">${this._terminalLabel(s)}</span></td>
      <td>${this._bdi(opened)}</td>
      <td>${s.closing_float_expected == null ? '<span class="cd-note">&mdash;</span>' : this._money(s.closing_float_expected)}</td>
      <td>${s.closing_float_counted == null ? `<span class="cd-uncounted">${t('Not counted')}</span>` : this._money(s.closing_float_counted)}</td>
      <td>${this._varianceCell(s)}</td>
      <td>${this._statusBadge(s)}</td>
      <td>${action}</td>
    </tr>`;
  },

  async approve(sessionId, acknowledgeUncounted) {
    try {
      const res = await this._post(
        `/api/sub/retail/cash-sessions/${encodeURIComponent(sessionId)}/approve`,
        acknowledgeUncounted ? { acknowledge_uncounted: true } : {});
      if (res.status === 'success') {
        SubsystemApp.showToast(
          res.data && res.data.self_approved
            ? t('Variance accepted. You approved a drawer you ended yourself.')
            : t('Variance accepted.'),
          'success');
        document.getElementById('cd-hist-modal')?.remove();
        this.openHistoryModal();
      } else {
        SubsystemApp.showToast(res.message || t('Failed to accept the variance'), 'error');
      }
    } catch (e) {
      SubsystemApp.showToast(t('Could not load the cash drawer. Its state is unknown.'), 'error');
    }
  },
};

window.CashDrawer = CashDrawer;
