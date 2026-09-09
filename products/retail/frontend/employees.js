/**
 * Aura Retail -- Employee management (multi-device Phase 1, design §3).
 *
 * The screen docs/launch-readiness/multi-device-design.md §3 says was the only
 * thing missing: "Employees use the already-written create_employee()... The
 * missing piece is purely UI: nothing in products/retail/frontend/ calls
 * /api/admin/employees."
 *
 * Self-contained feature file with its own <script> tag, the convention
 * cash-drawer.js / einvoicing.js / import-wizard.js already set -- this
 * frontend has no build step, and subsystem-retail.js is past 4,000 lines.
 * It hooks into the shell exactly the way cash-drawer.js does: one explicit
 * call from RetailSystem.render()'s switch, no monkey-patching from here.
 *
 * ── WHAT THIS SCREEN DELIBERATELY DOES NOT HAVE ──────────────────────────
 *
 * A NAME FIELD. `registry.db users` has no `name` column (registry_db.py's
 * CREATE TABLE) and `create_employee` accepts no name -- `create_admin` takes
 * one and writes it to config.json, which belongs to the install rather than
 * to any account. An account here IS its email address: that is what the
 * owner types to invite somebody, what that person signs in with, and what
 * `audit_logs` and every future `actor_user_uid` report resolves back to. A
 * "Full name" input would have looked like the obvious missing field and
 * would have been silently dropped in transit, which is worse than its
 * absence. The screen says so out loud instead of pretending.
 *
 * A ROLE OR STATUS CONTROL ON THE OWNER ROW. Two different reasons, both
 * server-side facts rather than styling choices:
 *   - `update_role` refuses the owner row with 409 and its own docstring says
 *     the message is not in the locale catalogs precisely because "the UI
 *     structurally never offers a role control on the owner row". This file
 *     is the half of that sentence that has to be true.
 *   - `update_status` has NO such guard -- it writes whatever status it is
 *     handed to whatever id it is handed. Disabling the single owner account
 *     is unrecoverable: a disabled admin still has a real `password_hash`, so
 *     `onboarding_status` keeps answering `needs_setup: false` and
 *     `create-admin` keeps answering 409, while `authenticate_registry_user`
 *     refuses the login. Every route that could undo it is behind the admin
 *     session nobody can obtain any more. Not rendering the button is a
 *     compensating control, not a fix, and the server-side bar is reported
 *     upstream as the real one.
 * PIN controls DO appear on the owner row: the owner rings sales too, and
 * `update_pin` has no owner bar because a PIN grants nothing (see below).
 *
 * A "PERMISSIONS" GRID. The eight capability codes exist and are seeded
 * (user_accounts.py), but no retail route reads them yet -- wiring them onto
 * the ~80 `@mt_require_subsystem('retail')` routes is a later slice of this
 * phase. A grid of switches that changed no behaviour would be a screen that
 * lies. Role IS the permission control today, and the role modal says that
 * changing it replaces the defaults.
 *
 * ── GATING: THE OWNER ROLE, NOT THE ADMIN DEVICE ─────────────────────────
 * Every route here gates on `session['mt_role'] == 'admin'` -- the USER axis.
 * app-shell.js's pre-existing `adminOnly` nav flag means `this.isAdminDevice`
 * -- the DEVICE axis, which design §3 is explicit is "not users.role='admin'".
 * Reusing it would have hidden this screen from the owner whenever they were
 * on their phone, and shown it to a cashier standing at the admin terminal
 * who would then be 403'd by every button on it. Hence the separate
 * `ownerOnly` flag in the nav, and the role check in render() below for the
 * case where somebody reaches the section without the nav.
 *
 * ── THE BRANCH-MANAGER REDUCED SCREEN (launch-readiness account-hierarchy
 *    design §10 item 3) ─────────────────────────────────────────────────
 * Commit 93cef8c shipped delegation server-side -- a branch manager
 * (role=manager AND a branch scope AND holding retail.employees) may
 * create a cashier at their own branch, disable one, and set/clear a PIN,
 * all via API -- and flagged in its own commit message that this screen
 * had no UI path onto any of it. render() below is that path: a non-owner
 * whose session role is 'manager' gets asked of GET /api/admin/employees
 * whether their OWN roster row currently carries `can_manage_staff`
 * (never recomputed from role/scope here -- see render()'s comment), and
 * if so gets `_reduced = true` for the rest of that render: create-cashier,
 * disable, and PIN only, in `_row()` and `_openInvite()` below. An
 * owner/admin session never sets `_reduced` and is byte-for-byte what this
 * screen has always rendered.
 */
const RetailEmployees = {
  //: Populated by _load(). Rows come straight from GET /api/admin/employees.
  _rows: [],

  //: Populated by _load(). This company's real branches (GET
  //: /api/sub/retail/branches) -- launch-readiness account-hierarchy design
  //: §3.3/§4.2 D5/D9. The ONLY source the branch-scope picker below ever
  //: offers: identity code (commercial_runtime/identity/) never validates a
  //: branch_scope_uid against a `branches` row (that table lives in
  //: retail.db, a different product's database), so this screen offering
  //: only real branches is what keeps a legitimate request honest.
  _branches: [],

  // True while the CURRENT render() is the branch-manager reduced screen
  // (launch-readiness account-hierarchy design §10 item 3 -- the BM-facing
  // arm this file was missing per commit 93cef8c's own "KNOWN GAP" note).
  // Read by _row() to decide which action buttons a row offers; set only by
  // render(), once per render() call, from the server's own answer (see
  // render()'s branch-manager arm below) -- never flipped anywhere else, so
  // there is exactly one place that can put this screen into reduced mode.
  _reduced: false,

  // Delegated rather than reimplemented -- same shape cash-drawer.js uses, so
  // the 401 -> re-login handling in RetailSystem._fetch covers this screen too
  // instead of every feature file inventing its own session behaviour.
  async _get(url) { return RetailSystem._get(url); },
  async _post(url, body) { return RetailSystem._post(url, body); },
  async _put(url, body) { return RetailSystem._put(url, body); },
  async _del(url) { return RetailSystem._del(url); },
  _esc(v) { return RetailSystem._esc(v); },
  // One guarded AuraIcons call for the whole file rather than a
  // `window.AuraIcons ? ... : ...` ternary at each of the three sites. NOT
  // delegated to RetailSystem._icon the way _esc above is delegated: this
  // screen's own harness (retail_employees_screen_test.js) loads employees.js
  // against a small RetailSystem stub, and reaching for a method that stub
  // does not carry would make the screen unrenderable there. The guard is
  // window.AuraIcons, exactly as in app-shell.js and sub-ai.js.
  //
  // This screen is otherwise exemplary -- 132 t() calls, zero paint literals
  // -- which is what made three leftover raw emoji (🔒 at 40px on the
  // owner-only refusal, 👤 in the invite modal, ✅ on the invite-created
  // heading) easy to miss: nothing else on it looked wrong beside them.
  _icon(name, size, fallback) {
    return window.AuraIcons ? AuraIcons.render(name, size) : (fallback || '');
  },

  // ── Refusals ──────────────────────────────────────────────────────────────
  //
  // One place, so every failure path in this file behaves the same way and
  // every literal in it stays visible to the locale-parity test.
  //
  // A server-supplied `error` goes through t(): these routes answer with FIXED
  // English sentences ("Email already registered.", "PIN must be exactly 4
  // digits.") which are catalog keys, and i18n.js's t() matches the whole
  // sentence and returns its input unchanged for anything it does not know --
  // so a message we did not anticipate degrades to readable English instead of
  // to a blank toast.
  //
  // `fallback` is passed ALREADY TRANSLATED -- a single-quoted literal inside
  // a t() call at the CALL SITE, rather than a bare string translated in
  // here. That is not stylistic: retail_employee_management_test.py scans
  // this file for exactly that call shape to prove every user-visible string
  // exists in both catalogs, and a string that only ever appeared as a bare
  // argument would be invisible to the scan -- a parity test that silently
  // skips half the screen is worse than none. (The scan reads raw source, so
  // this comment deliberately describes the shape instead of spelling it:
  // a fake call written in prose would be collected as a real string.)
  _fail(res, fallback) {
    const serverMessage = res && res.error;
    SubsystemApp.showToast(serverMessage ? t(serverMessage) : fallback, 'error');
  },

  // ── Identity predicates ───────────────────────────────────────────────────

  // The logged-in user is the store owner. `SubsystemApp.role` is set once in
  // init() from GET /api/auth/session, i.e. from `session['mt_role']` -- the
  // exact value every route here compares against, so the screen and the
  // server cannot disagree about who is asking.
  _isOwner() {
    return (window.SubsystemApp && SubsystemApp.role) === 'admin';
  },

  // Is THIS row the owner account? Keyed on `effective_role`, which
  // get_employees computes with the same `normalize_role()` the server-side
  // owner bar in update_role uses -- so the row the UI treats as untouchable
  // is exactly the row the API refuses to touch. Comparing the raw `role`
  // string instead would drift the moment a legacy spelling appeared.
  _isOwnerRow(emp) {
    return (emp && emp.effective_role) === 'admin';
  },

  // ── Render ────────────────────────────────────────────────────────────────

  async render(c) {
    if (!c) return;
    RetailSystem._injectStyles();

    if (this._isOwner()) {
      this._reduced = false;
      c.innerHTML = `
        <div class="ret-hdr">
          <h2 class="ret-title">${t('Employees')}</h2>
          <button class="sub-btn-primary" onclick="RetailEmployees._openInvite()">+ ${t('Add Employee')}</button>
        </div>
        <div class="sub-chart-card">
          <p style="color:var(--text-muted);font-size:13px;margin:0 0 18px;line-height:1.7">
            ${t('Every account is identified by its email address. This product does not store a separate display name.')}
          </p>
          <div style="overflow-x:auto">
            <table class="ret-table" id="emp-table">
              <thead><tr>
                <th>${t('Employee')}</th>
                <th>${t('Role')}</th>
                <th>${t('Branch')}</th>
                <th>${t('Status')}</th>
                <th>${t('PIN')}</th>
                <th>${t('Actions')}</th>
              </tr></thead>
              <tbody><tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:30px">${t('Loading…')}</td></tr></tbody>
            </table>
          </div>
        </div>`;
      // Delegated click handler for the table's action buttons (C6, see
      // _onTableClick above). One listener per render() call: render()
      // rebuilds this whole innerHTML block -- and with it a brand-new
      // #emp-table element -- every time it runs, so there is nothing left
      // over from a previous render() for a second listener to pile onto.
      document.getElementById('emp-table')?.addEventListener('click', ev => this._onTableClick(ev));
      await this._load();
      return;
    }

    // Not the owner. Two real possibilities and one fake one:
    //   - an ordinary cashier/other non-owner, who has no path onto this
    //     screen at all;
    //   - a DELEGATED BRANCH MANAGER (launch-readiness account-hierarchy
    //     design §10 item 3) -- role='manager' AND a branch scope AND
    //     holding retail.employees, all read fresh server-side -- who gets
    //     a reduced screen: create cashier, disable, set/clear PIN, nothing
    //     else (commit 93cef8c shipped the delegation server-side and
    //     flagged this exact screen as the missing piece).
    //   - the fake one: a role that merely SAYS 'manager' this session but
    //     is not currently delegated (no scope, or the toggle was revoked
    //     since login). `SubsystemApp.role` decides nothing here -- it is
    //     rendering advice only, the same posture app-shell.js's
    //     hasCapability() already documents, and re-deriving the actual
    //     predicate from role+scope client-side is exactly what
    //     _isDelegationEligible below does for the OWNER's OWN grant/revoke
    //     button, which is allowed to be a rendering shortcut only because a
    //     wrong guess there just mis-renders one button the server still
    //     gates. Getting this screen's whole identity wrong is a bigger
    //     mistake, so the only source of truth is the same roster route the
    //     owner's screen already trusts: GET /api/admin/employees answers
    //     `can_manage_staff` on THIS session's own row, computed from the
    //     live grant, not from anything cached in this session's login.
    //
    // `role !== 'manager'` still short-circuits before any network call --
    // a cashier can never pass the server's role half of the delegation
    // predicate either way (the same "a stray hand-granted retail.employees
    // on a cashier row is inert" reasoning create_employee's own commit
    // message gives), so there is nothing to gain by asking, and every
    // existing non-owner refuses-with-zero-calls test keeps passing for
    // every role except the one that can actually be delegated.
    if (!(window.SubsystemApp && SubsystemApp.role === 'manager')) {
      c.innerHTML = this._refusalHtml();
      return;
    }

    c.innerHTML = `
      <div class="ret-hdr"><h2 class="ret-title">${t('Employees')}</h2></div>
      <div class="sub-chart-card" style="text-align:center;padding:48px 32px">
        <p style="color:var(--text-muted);font-size:13px;margin:0">${t('Loading…')}</p>
      </div>`;

    let probe = null;
    try {
      probe = await this._get('/api/admin/employees');
    } catch (err) {
      console.error('Branch-manager roster check failed', err);
    }
    const myId = window.SubsystemApp && SubsystemApp.currentUser && SubsystemApp.currentUser.id;
    // My own row is always present when the request was accepted: the
    // delegated arm of get_employees filters to
    // `branch_scope_uid = <creator's own scope> AND role != 'admin'`, and a
    // branch manager's own row matches that by construction (their own
    // scope, their own non-admin role). Not finding it is therefore treated
    // exactly like "not delegated" -- fail closed, never fail open.
    const myRow = (probe && probe.success)
      ? (probe.employees || []).find(e => String(e.id) === String(myId))
      : null;

    if (!myRow || !myRow.can_manage_staff) {
      c.innerHTML = this._refusalHtml();
      return;
    }

    this._reduced = true;
    c.innerHTML = `
      <div class="ret-hdr">
        <h2 class="ret-title">${t('Employees')}</h2>
        <button class="sub-btn-primary" onclick="RetailEmployees._openInvite(true)">+ ${t('Add Cashier')}</button>
      </div>
      <div class="sub-chart-card">
        <p style="color:var(--text-muted);font-size:13px;margin:0 0 18px;line-height:1.7">
          ${t('You can add, disable and reset the PIN for cashiers at your own branch. Role, branch and permission changes are made by the store owner.')}
        </p>
        <div style="overflow-x:auto">
          <table class="ret-table" id="emp-table">
            <thead><tr>
              <th>${t('Employee')}</th>
              <th>${t('Role')}</th>
              <th>${t('Branch')}</th>
              <th>${t('Status')}</th>
              <th>${t('PIN')}</th>
              <th>${t('Actions')}</th>
            </tr></thead>
            <tbody><tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:30px">${t('Loading…')}</td></tr></tbody>
          </table>
        </div>
      </div>`;
    document.getElementById('emp-table')?.addEventListener('click', ev => this._onTableClick(ev));
    // `probe` already IS the roster this session may see -- passed through
    // rather than fetched a second time, so a branch manager's render costs
    // exactly two requests (this probe + _load()'s own branch-list fetch),
    // not three.
    await this._load(probe);
  },

  // Degrade HONESTLY: refuse with a reason rather than painting a table and
  // then filling it with a 403. Shared by both non-owner exits above (an
  // ordinary employee, and a 'manager' role the server did not accept as
  // currently delegated) so there is exactly one refusal screen to keep
  // translated and exactly one place to update its wording.
  _refusalHtml() {
    return `
      <div class="ret-hdr"><h2 class="ret-title">${t('Employees')}</h2></div>
      <div class="sub-chart-card" style="text-align:center;padding:48px 32px">
        <div style="font-size:40px;margin-bottom:14px" aria-hidden="true">${this._icon('lock', 40, '🔒')}</div>
        <h3 style="color:var(--text);margin:0 0 10px;font-size:17px">${t('Employee management is available to the store owner only.')}</h3>
        <p style="color:var(--text-muted);font-size:13px;margin:0;line-height:1.7">
          ${t('You are signed in with an employee account. Ask the store owner to add or change staff accounts.')}
        </p>
      </div>`;
  },

  // `preloadedRes` -- optional, and only ever passed by render()'s
  // branch-manager arm above, which already had to call
  // GET /api/admin/employees once to decide whether to show the reduced
  // screen at all. Reusing that answer instead of fetching it again keeps a
  // branch manager's render at two requests total (the probe + the branch
  // list just below) rather than three; the owner path never passes this,
  // so its call sequence -- branches, then employees -- is unchanged.
  async _load(preloadedRes) {
    const tbody = document.querySelector('#emp-table tbody');
    // Fetched separately from the employee list, and deliberately allowed
    // to fail on its own: the branch picker degrades to just showing every
    // row's raw scope state (All branches / a name it cannot resolve) --
    // a company whose branches happen not to load must not lose the whole
    // employee screen over it.
    try {
      const br = await this._get('/api/sub/retail/branches');
      this._branches = (br && br.data) || [];
    } catch (err) {
      console.error('Branch list load failed', err);
      this._branches = [];
    }
    try {
      const res = preloadedRes || await this._get('/api/admin/employees');
      // These routes answer {'error': ...} with a 403 rather than the
      // {status:'success'} envelope the retail API uses -- surface the real
      // message instead of an empty list, which would read as "this shop has
      // no staff" when it actually means "you were refused".
      if (!res || !res.success) {
        const reason = (res && res.error) ? t(res.error) : t('Could not load the employee list.');
        if (tbody) tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--state-danger-text);padding:30px">${this._esc(reason)}</td></tr>`;
        return;
      }
      // Owner first, then by employee code, so the list has a stable order
      // instead of whatever order SQLite happened to return rows in.
      this._rows = (res.employees || []).slice().sort((a, b) => {
        if (this._isOwnerRow(a) !== this._isOwnerRow(b)) return this._isOwnerRow(a) ? -1 : 1;
        return String(a.employee_id || '').localeCompare(String(b.employee_id || ''));
      });
      if (!tbody) return;
      if (!this._rows.length) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:30px">${t('No staff accounts yet. Add one to get started.')}</td></tr>`;
        return;
      }
      tbody.innerHTML = this._rows.map(e => this._row(e)).join('');
    } catch (err) {
      console.error('Employee list load failed', err);
      if (tbody) tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--state-danger-text);padding:30px">${t('Could not load the employee list.')}</td></tr>`;
    }
  },

  // Every interpolated value is escaped. Emails are typed by the owner today,
  // but `users` is a SHARED table in design §4 -- once accounts sync, a row on
  // this screen can have been written on another device, which is the same
  // trust boundary the category/supplier rows already escape for.
  //
  // The action buttons carry `id` as a `data-id` attribute rather than
  // interpolating it into an inline `onclick="...('${id}')"` call (C6, see
  // `_onTableClick` below for the reasoning): `_esc()` turns a `'` into the
  // HTML entity `&#39;`, which is correct for TEXT and for an attribute
  // VALUE, but a browser HTML-entity-decodes a double-quoted attribute
  // BEFORE the inline handler's JS source is parsed -- so a decoded `'`
  // still terminates a single-quoted JS string literal early
  // (retail_supplier_name_apostrophe_onclick_test.js covers exactly this
  // class for supplier names). `id` is a server-minted uuid4 today, so this
  // is not reachable in practice, but the same escaped-for-HTML-not-for-
  // JS-in-HTML-attribute gap was present here too. Reading `id` back only
  // ever as attribute DATA (`btn.dataset.id`), never as a second parse of
  // JS source, removes the gap by construction instead of relying on a
  // uuid4 never containing a quote.
  // Launch-readiness account-hierarchy design §3.3/§4.2 D5/D9 -- the
  // opaque `branch_scope_uid` a row carries, resolved to a NAME purely for
  // display. `this._branches` is the SAME list the scope picker below
  // offers, so "resolves to a name" and "is a real, selectable branch" are
  // the same question asked twice rather than two answers that could drift.
  _branchName(uid) {
    const b = (this._branches || []).find(x => x.uid === uid);
    return b ? b.name : null;
  },

  // NULL/absent `branch_scope_uid` means "every branch" (registry v7's own
  // contract -- see branch_scope_schema.py) -- rendered as an explicit
  // badge rather than a blank cell, so an owner scanning the column can
  // tell "unscoped, on purpose" from "the branch failed to load".
  _branchBadge(e) {
    if (!e.branch_scope_uid) return RetailSystem._badge(t('All branches'), 'blue');
    const name = this._branchName(e.branch_scope_uid);
    // A scope that does not resolve locally is shown rather than hidden --
    // this device's own branch list has not synced it yet, or the row was
    // written on a device this one has not caught up with. The account IS
    // still scoped (D7 enforcement does not care whether THIS screen can
    // name it), so silently rendering "All branches" here would be a lie.
    return name
      ? RetailSystem._badge(this._esc(name), 'purple')
      : RetailSystem._badge(t('Unknown branch'), 'red');
  },

  _row(e) {
    const id = this._esc(e.id);
    const owner = this._isOwnerRow(e);
    // Branch-manager reduced screen (design §10 item 3): a delegated
    // manager gets create-cashier, disable, and set/clear-PIN ONLY. Role,
    // branch-scope, and the delegation toggle itself are owner-only levers
    // -- rendering them here would be a control that simply 403s the
    // moment it is clicked, which is worse than not offering it at all.
    // RE-ENABLE is asymmetric on purpose (commit 93cef8c): disable is the
    // safety action a manager needs at 9pm with nobody to call; re-enable
    // is the trust action the OWNER alone grants back, so a disabled row
    // offers nothing further here rather than a Reactivate button that
    // would also just 403.
    const reduced = this._reduced;
    return `<tr>
      <td>
        <div style="font-weight:600;color:var(--text)">${this._esc(e.email)}</div>
        <div style="font-size:11px;color:var(--text-muted);font-family:monospace">${this._esc(e.employee_id || '')}</div>
      </td>
      <td>${this._roleBadge(e)}</td>
      <td>${this._branchBadge(e)}</td>
      <td>${this._statusBadge(e)}</td>
      <td>${e.has_pin
            ? `<span style="color:var(--state-success-text);font-size:12px">● ${t('Set')}</span>`
            : `<span style="color:var(--text-muted);font-size:12px">○ ${t('Not set')}</span>`}</td>
      <td>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          ${reduced ? '' : (owner ? '' : `<button class="ret-btn ret-btn-ghost ret-btn-sm" data-action="role" data-id="${id}">${t('Change Role')}</button>`)}
          ${reduced ? '' : (owner ? '' : `<button class="ret-btn ret-btn-ghost ret-btn-sm" data-action="branch" data-id="${id}">${t('Change Branch')}</button>`)}
          ${reduced ? '' : this._delegationButton(e)}
          <button class="ret-btn ret-btn-ghost ret-btn-sm" data-action="pin" data-id="${id}">${e.has_pin ? t('Reset PIN') : t('Set PIN')}</button>
          ${owner ? '' : (e.status === 'disabled'
              ? (reduced ? '' : `<button class="ret-btn ret-btn-ghost ret-btn-sm" data-action="status" data-status="active" data-id="${id}">${t('Reactivate')}</button>`)
              : `<button class="ret-btn ret-btn-danger ret-btn-sm" data-action="status" data-status="disabled" data-id="${id}">${t('Deactivate')}</button>`)}
        </div>
      </td>
    </tr>`;
  },

  // ── Delegation toggle (launch-readiness account-hierarchy design §3.4/
  //    §4.2/§10 D9) ──────────────────────────────────────────────────────
  //
  // The ONE owner-facing lever this wave ships -- deliberately narrower
  // than the full 8-code permissions matrix screen (design §10 explains
  // why one toggle suffices: `retail.employees` is the only code any
  // delegated route -- create_employee/update_status/update_pin/
  // get_employees in onboarding_routes.py -- ever consults). Wired to the
  // existing, UNCHANGED `update_perms` route
  // (subsystem='retail.employees', access_level='full'|'none'); no new
  // server-side surface backs this control.
  //
  // Rendered only on a manager row that ALSO carries a branch scope: an
  // unscoped manager can never pass the delegated gate's own
  // `branch_scope_uid IS NOT NULL` half (G1/D1), so offering the toggle
  // there would be a control that only ever 403s.
  _isDelegationEligible(emp) {
    return emp && emp.effective_role === 'manager' && !!emp.branch_scope_uid;
  },

  _delegationButton(emp) {
    if (this._isOwnerRow(emp) || !this._isDelegationEligible(emp)) return '';
    const id = this._esc(emp.id);
    return emp.can_manage_staff
      ? `<button class="ret-btn ret-btn-ghost ret-btn-sm" data-action="delegation" data-delegate="revoke" data-id="${id}">${t('Revoke Staff Management')}</button>`
      : `<button class="ret-btn ret-btn-primary ret-btn-sm" data-action="delegation" data-delegate="grant" data-id="${id}">${t('Allow to Manage Branch Staff')}</button>`;
  },

  async _toggleDelegation(id, grant) {
    const emp = this._find(id);
    if (!emp) return;
    const question = grant
      ? t('Allow this branch manager to create and manage cashiers at their own branch?')
      : t('Revoke this manager ability to create and manage staff at their branch?');
    if (!await SubsystemApp.confirm({ title: question })) return;
    try {
      const res = await this._post(`/api/admin/employees/${id}/permissions`, {
        subsystem: 'retail.employees', access_level: grant ? 'full' : 'none',
      });
      if (res && res.success) {
        SubsystemApp.showToast(grant ? t('Staff management granted.') : t('Staff management revoked.'), 'success');
        this._load();
        return;
      }
      this._fail(res, t('Could not change staff-management delegation.'));
    } catch (err) {
      console.error('Delegation toggle failed', err);
      SubsystemApp.showToast(t('Could not change staff-management delegation.'), 'error');
    }
  },

  // Delegated handler for every action button _row() renders (C6). Reading
  // `dataset.id`/`dataset.action` is a plain string read, never a second
  // parse of the id as JS source, so there is nothing left for a quote in an
  // id to break out of -- see the comment on _row() above. Attached once per
  // render() call, on the freshly-created #emp-table element; render()
  // rebuilds the whole table from scratch every time, so there is nothing to
  // double-bind or leak across re-renders.
  _onTableClick(ev) {
    const btn = ev.target && ev.target.closest && ev.target.closest('button[data-action]');
    if (!btn) return;
    const id = btn.dataset.id;
    const action = btn.dataset.action;
    if (action === 'role') this._openRole(id);
    else if (action === 'branch') this._openBranchScope(id);
    else if (action === 'pin') this._openPin(id);
    else if (action === 'status') this._setStatus(id, btn.dataset.status);
    else if (action === 'delegation') this._toggleDelegation(id, btn.dataset.delegate === 'grant');
  },

  // `effective_role` rather than `role`: get_employees keeps `role` byte-for
  // -byte because Clinic renders the same response and its rows still say
  // 'employee', and adds `effective_role` as the widened-domain reading. The
  // effective one is what every capability decision is actually made against,
  // so showing it is showing the truth rather than the stored spelling.
  _roleBadge(e) {
    const role = (e && e.effective_role) || 'cashier';
    if (role === 'admin')   return RetailSystem._badge(t('Owner'), 'purple');
    if (role === 'manager') return RetailSystem._badge(t('Manager'), 'blue');
    return RetailSystem._badge(t('Cashier'), 'yellow');
  },

  // 'pending_setup' is shown as "Invited", not "Pending": it is the state of
  // an account whose invite link has been issued and not yet used, and
  // "invited" is the word that tells the owner the next action is chasing the
  // person rather than waiting on the software.
  _statusBadge(e) {
    const status = (e && e.status) || '';
    if (status === 'active')   return RetailSystem._badge(t('Active'), 'green');
    if (status === 'disabled') return RetailSystem._badge(t('Deactivated'), 'red');
    return RetailSystem._badge(t('Invited'), 'yellow');
  },

  _find(id) { return (this._rows || []).find(r => String(r.id) === String(id)); },

  _closeModal(elementId) { document.getElementById(elementId)?.remove(); },

  _modal(id, innerHtml, width) {
    const overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = id;
    overlay.innerHTML = `<div class="ret-modal" style="width:${width || 480}px">${innerHtml}</div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', ev => { if (ev.target === overlay) overlay.remove(); });
    return overlay;
  },

  // ── Invite a new employee ─────────────────────────────────────────────────

  // `reduced` -- true only when opened from the branch-manager screen
  // (design §10 item 3). A branch manager's create is ALWAYS a cashier at
  // their own branch (create_employee forces the role and stamps the
  // scope server-side, commit 93cef8c); this modal therefore offers no
  // role select at all in that mode rather than a dropdown whose "Manager"
  // option would be silently overridden by the server -- the exact
  // "screen does something other than what it showed" failure shape the
  // reduced view exists to avoid.
  _openInvite(reduced) {
    const roleField = reduced
      ? `<div class="ret-field">
          <label>${t('Role')}</label>
          <p style="color:var(--text-muted);font-size:12px;margin:6px 0 0;line-height:1.6">
            ${t('New staff you add here are always cashiers at your own branch.')}
          </p>
        </div>`
      : `<div class="ret-field">
          <label>${t('Role')}</label>
          <select id="emp-inv-role">
            <option value="cashier">${t('Cashier')}</option>
            <option value="manager">${t('Manager')}</option>
          </select>
          <p style="color:var(--text-muted);font-size:12px;margin:8px 0 0;line-height:1.6">
            ${t('A cashier can sell, refund against a sale, and close their own drawer. A manager can also discount, adjust stock and read reports.')}
          </p>
        </div>`;
    this._modal('emp-invite-modal', `
      <h3><span aria-hidden="true">${this._icon('user', 18, '👤')}</span> ${reduced ? t('Invite a cashier') : t('Invite an employee')}</h3>
      <p style="color:var(--text-muted);font-size:13px;margin:-14px 0 20px;line-height:1.7">
        ${t('They receive a one-time setup link and choose their own password. No password is set for them here.')}
      </p>
      <div class="ret-field">
        <label>${t('Email address')} *</label>
        <!-- The placeholder is NOT translated, and that is the correct
             answer rather than an omission: an example email address is not
             language, and the locale suite requires every catalog entry to
             differ between en and ar, which "name@example.com" cannot
             honestly do. i18n.js never sweeps input elements anyway. -->
        <input id="emp-inv-email" type="email" autocomplete="off" placeholder="name@example.com" />
      </div>
      ${roleField}
      <div class="ret-modal-footer">
        <button class="ret-btn ret-btn-ghost" onclick="RetailEmployees._closeModal('emp-invite-modal')">${t('Cancel')}</button>
        <button class="ret-btn ret-btn-primary" id="emp-inv-btn" onclick="RetailEmployees._submitInvite(${reduced ? 'true' : 'false'})">${reduced ? t('Add Cashier') : t('Create Invite')}</button>
      </div>`);
    document.getElementById('emp-inv-email')?.focus();
  },

  async _submitInvite(reduced) {
    const email = (document.getElementById('emp-inv-email')?.value || '').trim();
    // Reduced mode sends 'cashier' directly rather than reading a select
    // that does not exist in this mode's markup -- see _openInvite's
    // comment above for why that select is absent rather than merely
    // disabled.
    const role = reduced ? 'cashier' : (document.getElementById('emp-inv-role')?.value || 'cashier');
    if (!email) { SubsystemApp.showToast(t('Enter an email address.'), 'error'); return; }

    const btn = document.getElementById('emp-inv-btn');
    if (btn) { btn.disabled = true; btn.textContent = t('Working…'); }
    try {
      const res = await this._post('/api/admin/employees', { email, role });
      if (res && res.success) {
        this._closeModal('emp-invite-modal');
        this._showInviteLink(email, res.setup_link || '');
        this._load();
        return;
      }
      // `error` here is one of create_employee's own fixed sentences: "Email
      // required", "Email already registered.", "Role must be manager or
      // cashier." All three are catalog keys -- see _fail above.
      this._fail(res, t('Could not create this employee.'));
    } catch (err) {
      console.error('Employee invite failed', err);
      SubsystemApp.showToast(t('Could not create this employee.'), 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = reduced ? t('Add Cashier') : t('Create Invite'); }
    }
  },

  // The link is shown ONCE, here, and never again: create_employee returns the
  // raw token in this response only -- `secure_links` stores nothing but its
  // SHA-256, so no later screen can re-read it. An owner who closes this
  // dialog without copying the link has to issue a new invite, and the dialog
  // says so rather than leaving them to discover it.
  _showInviteLink(email, link) {
    this._modal('emp-link-modal', `
      <h3><span aria-hidden="true">${this._icon('circle-check-big', 18, '✅')}</span> ${t('Invite created')}</h3>
      <p style="color:var(--text-muted);font-size:13px;margin:-14px 0 16px;line-height:1.7">
        ${t('Send this link to')} <b style="color:var(--text)">${this._esc(email)}</b>.
      </p>
      <div class="ret-field">
        <input id="emp-link-input" readonly value="${this._esc(link)}"
               style="font-family:monospace;font-size:12px;direction:ltr;text-align:left" />
      </div>
      <div style="background:var(--state-warning-surface);border:1px solid var(--state-warning-border);border-radius:10px;padding:12px 14px;margin-bottom:6px">
        <div style="color:var(--state-warning-text);font-size:12px;font-weight:700;margin-bottom:4px">${t('This link works once and expires in 7 days.')}</div>
        <div style="color:var(--text-muted);font-size:12px;line-height:1.6">${t('It is shown only now. If you close this window without copying it, issue a new invite.')}</div>
      </div>
      <div class="ret-modal-footer">
        <button class="ret-btn ret-btn-ghost" id="emp-copy-btn" onclick="RetailEmployees._copyLink()">${t('Copy link')}</button>
        <button class="ret-btn ret-btn-primary" onclick="RetailEmployees._closeModal('emp-link-modal')">${t('Done')}</button>
      </div>`, 560);
    // direction:ltr on the input above is deliberate and survives RTL: a URL
    // is not natural-language text, and rtl.css's `body.rtl input {
    // text-align: right }` would otherwise render `/#setup/<token>` visually
    // reordered -- readable to nobody and impossible to transcribe by hand.
  },

  // navigator.clipboard is not guaranteed here. This app runs inside pywebview
  // and an Edge --app window, and the async Clipboard API is gated on a secure
  // context and a permission that neither reliably grants. So: try it, fall
  // back to select+execCommand, and if BOTH fail say so and leave the text
  // selected -- never a success toast for a copy that did not happen.
  async _copyLink() {
    const input = document.getElementById('emp-link-input');
    if (!input) return;
    const btn = document.getElementById('emp-copy-btn');
    const done = () => { if (btn) btn.textContent = t('Copied'); };
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(input.value);
        done();
        return;
      }
    } catch (err) { /* fall through to the selection path below */ }
    try {
      input.focus();
      input.select();
      input.setSelectionRange(0, input.value.length);
      if (document.execCommand && document.execCommand('copy')) { done(); return; }
    } catch (err) { /* fall through to the honest failure below */ }
    input.focus();
    input.select();
    SubsystemApp.showToast(t('Could not copy automatically. The link is selected — copy it manually.'), 'error');
  },

  // ── Change role ───────────────────────────────────────────────────────────

  _openRole(id) {
    const emp = this._find(id);
    if (!emp) return;
    const current = emp.effective_role === 'manager' ? 'manager' : 'cashier';
    this._modal('emp-role-modal', `
      <h3>${t('Change Role')}</h3>
      <p style="color:var(--text-muted);font-size:13px;margin:-14px 0 20px">${this._esc(emp.email)}</p>
      <div class="ret-field">
        <label>${t('Role')}</label>
        <select id="emp-role-select">
          <option value="cashier"${current === 'cashier' ? ' selected' : ''}>${t('Cashier')}</option>
          <option value="manager"${current === 'manager' ? ' selected' : ''}>${t('Manager')}</option>
        </select>
      </div>
      <div style="background:var(--state-warning-surface);border:1px solid var(--state-warning-border);border-radius:10px;padding:12px 14px">
        <div style="color:var(--text-muted);font-size:12px;line-height:1.6">
          ${t('Changing the role replaces this account permissions with the defaults for the new role, and signs the person out of any session they have open.')}
        </div>
        <!-- design §10's copy detail: update_role's reset semantics mean
             demote-then-repromote drops the branch-manager delegation
             toggle (_delegationButton below) -- it is not restored when the
             role comes back to manager, and this is the one place an owner
             is about to trigger exactly that. -->
        <div style="color:var(--text-muted);font-size:12px;line-height:1.6;margin-top:8px">
          ${t('If this account currently has permission to manage branch staff, changing its role away and back removes that permission. Grant it again afterward if needed.')}
        </div>
      </div>
      <div class="ret-modal-footer">
        <button class="ret-btn ret-btn-ghost" onclick="RetailEmployees._closeModal('emp-role-modal')">${t('Cancel')}</button>
        <button class="ret-btn ret-btn-primary" id="emp-role-btn">${t('Save')}</button>
      </div>`);
    // Wired as a closure over `id` (C6), the same reasoning as _row()'s
    // data-id switch above -- `id` never becomes HTML/onclick source text for
    // this button at all, so there is no attribute/JS-string boundary left
    // for it to break out of.
    document.getElementById('emp-role-btn')?.addEventListener('click', () => this._saveRole(id));
  },

  async _saveRole(id) {
    const role = document.getElementById('emp-role-select')?.value;
    const btn = document.getElementById('emp-role-btn');
    if (btn) { btn.disabled = true; btn.textContent = t('Working…'); }
    try {
      const res = await this._put(`/api/admin/employees/${id}/role`, { role });
      if (res && res.success) {
        this._closeModal('emp-role-modal');
        SubsystemApp.showToast(t('Role updated'), 'success');
        this._load();
        return;
      }
      this._fail(res, t('Could not change the role.'));
    } catch (err) {
      console.error('Role change failed', err);
      SubsystemApp.showToast(t('Could not change the role.'), 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = t('Save'); }
    }
  },

  // ── Branch scope (launch-readiness account-hierarchy design §3.3/§4.2 D5/D9) ─
  //
  // "All branches" in the picker is the NULL wire value -- registry v7's own
  // contract (branch_scope_schema.py): every account is unscoped by default,
  // exactly as it was before this control existed, and choosing it here is
  // what CLEARS a scope that was previously set rather than leaving it
  // untouched.

  _openBranchScope(id) {
    const emp = this._find(id);
    if (!emp) return;
    const current = emp.branch_scope_uid || '';
    const options = (this._branches || [])
      .map(b => `<option value="${this._esc(b.uid)}"${b.uid === current ? ' selected' : ''}>${this._esc(b.name)}</option>`)
      .join('');
    this._modal('emp-branch-modal', `
      <h3>${t('Change Branch')}</h3>
      <p style="color:var(--text-muted);font-size:13px;margin:-14px 0 20px">${this._esc(emp.email)}</p>
      <div class="ret-field">
        <label>${t('Branch')}</label>
        <select id="emp-branch-select">
          <option value=""${current ? '' : ' selected'}>${t('All branches')}</option>
          ${options}
        </select>
      </div>
      <div style="background:var(--state-info-surface);border:1px solid var(--state-info-border);border-radius:10px;padding:12px 14px">
        <div style="color:var(--text-muted);font-size:12px;line-height:1.6">
          ${t('A branch-scoped account only sees and can act on that branch data. Choose All branches to give this account access across every branch, the same as the owner.')}
        </div>
      </div>
      <div class="ret-modal-footer">
        <button class="ret-btn ret-btn-ghost" onclick="RetailEmployees._closeModal('emp-branch-modal')">${t('Cancel')}</button>
        <button class="ret-btn ret-btn-primary" id="emp-branch-btn">${t('Save')}</button>
      </div>`);
    // Closure over `id`, same reasoning as _openRole's Save button above.
    document.getElementById('emp-branch-btn')?.addEventListener('click', () => this._saveBranchScope(id));
  },

  async _saveBranchScope(id) {
    const value = document.getElementById('emp-branch-select')?.value || '';
    const btn = document.getElementById('emp-branch-btn');
    if (btn) { btn.disabled = true; btn.textContent = t('Working…'); }
    try {
      // An empty selection sends `branch_scope_uid: null`, not `''` -- the
      // server route's own "null clears" contract (D5), so an owner
      // choosing "All branches" actually clears a previously-set scope
      // rather than storing an empty string nobody's read path treats the
      // same way NULL is treated.
      const res = await this._put(`/api/admin/employees/${id}/branch-scope`, {
        branch_scope_uid: value || null,
      });
      if (res && res.success) {
        this._closeModal('emp-branch-modal');
        SubsystemApp.showToast(t('Branch updated'), 'success');
        this._load();
        return;
      }
      this._fail(res, t('Could not change the branch.'));
    } catch (err) {
      console.error('Branch scope change failed', err);
      SubsystemApp.showToast(t('Could not change the branch.'), 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = t('Save'); }
    }
  },

  // ── PIN ───────────────────────────────────────────────────────────────────

  _openPin(id) {
    const emp = this._find(id);
    if (!emp) return;
    this._modal('emp-pin-modal', `
      <h3>${emp.has_pin ? t('Reset PIN') : t('Set PIN')}</h3>
      <p style="color:var(--text-muted);font-size:13px;margin:-14px 0 18px">${this._esc(emp.email)}</p>
      <div style="background:var(--state-info-surface);border:1px solid var(--state-info-border);border-radius:10px;padding:12px 14px;margin-bottom:18px">
        <div style="color:var(--state-info-text);font-size:12px;font-weight:700;margin-bottom:4px">${t('A PIN identifies who is acting. It does not grant permission.')}</div>
        <div style="color:var(--text-muted);font-size:12px;line-height:1.6">
          ${t('Switching the acting user by PIN is not available yet. Setting a PIN now means this account will be ready to use it as soon as that feature ships.')}
        </div>
      </div>
      <div class="ret-field">
        <label>${t('New PIN')}</label>
        <input id="emp-pin-input" type="text" inputmode="numeric" autocomplete="off" maxlength="4"
               style="letter-spacing:8px;font-size:20px;text-align:center;direction:ltr" />
        <p style="color:var(--text-muted);font-size:12px;margin:8px 0 0">${t('Four digits. Stored hashed, never in plain text.')}</p>
      </div>
      <div class="ret-modal-footer">
        ${emp.has_pin ? `<button class="ret-btn ret-btn-danger" id="emp-pin-clear-btn">${t('Remove PIN')}</button>` : ''}
        <button class="ret-btn ret-btn-ghost" onclick="RetailEmployees._closeModal('emp-pin-modal')">${t('Cancel')}</button>
        <button class="ret-btn ret-btn-primary" id="emp-pin-btn">${t('Save')}</button>
      </div>`);
    document.getElementById('emp-pin-input')?.focus();
    // Closures over `id`, same reasoning as _openRole's Save button (C6).
    document.getElementById('emp-pin-btn')?.addEventListener('click', () => this._savePin(id));
    document.getElementById('emp-pin-clear-btn')?.addEventListener('click', () => this._clearPin(id));
    // type="text" + inputmode="numeric", NOT type="number". An Arabic soft
    // keyboard emits ARABIC-INDIC digits, and a number input refuses to hold
    // them -- which would defeat the digit folding user_accounts._normalize_pin
    // exists to do (it folds at set AND verify so the two spellings are one
    // PIN). direction:ltr keeps the four boxes in typing order under RTL.
  },

  async _savePin(id) {
    const pin = (document.getElementById('emp-pin-input')?.value || '').trim();
    const btn = document.getElementById('emp-pin-btn');
    if (btn) { btn.disabled = true; btn.textContent = t('Working…'); }
    try {
      // Length is checked server-side by set_user_pin (which also folds the
      // digits); this call is not pre-validated here on purpose -- a
      // client-side digit test would have to re-implement the Unicode-Nd
      // folding to avoid rejecting a legitimate Arabic PIN before it was ever
      // sent, and two copies of that rule is exactly how the two spellings
      // stop being one PIN.
      const res = await this._put(`/api/admin/employees/${id}/pin`, { pin });
      if (res && res.success) {
        this._closeModal('emp-pin-modal');
        SubsystemApp.showToast(t('PIN updated'), 'success');
        this._load();
        return;
      }
      this._fail(res, t('Could not update the PIN.'));
    } catch (err) {
      console.error('PIN save failed', err);
      SubsystemApp.showToast(t('Could not update the PIN.'), 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = t('Save'); }
    }
  },

  async _clearPin(id) {
    if (!await SubsystemApp.confirm({
      title: t('Remove the PIN from this account? They can still sign in with their password.'),
      danger: true,
    })) return;
    try {
      const res = await this._del(`/api/admin/employees/${id}/pin`);
      if (res && res.success) {
        this._closeModal('emp-pin-modal');
        SubsystemApp.showToast(t('PIN removed'), 'success');
        this._load();
        return;
      }
      this._fail(res, t('Could not update the PIN.'));
    } catch (err) {
      console.error('PIN clear failed', err);
      SubsystemApp.showToast(t('Could not update the PIN.'), 'error');
    }
  },

  // ── Deactivate / reactivate ───────────────────────────────────────────────
  //
  // REVERSIBLE, and the wording says so. `status` is a plain column and the
  // same route writes both values, so this is not a delete: the account keeps
  // its `uid`, its employee code, its PIN and every audit row that names it.
  // Nothing in this product hard-deletes an account -- `deleted_at_utc` exists
  // in registry v3 and no route writes it yet, which is why the screen offers
  // deactivation rather than a Delete button it could not honestly implement.
  async _setStatus(id, status) {
    const emp = this._find(id);
    if (!emp) return;
    const question = status === 'disabled'
      ? t('Deactivate this account? They will not be able to sign in until you reactivate it.')
      : t('Reactivate this account?');
    if (!await SubsystemApp.confirm({ title: question })) return;
    try {
      const res = await this._put(`/api/admin/employees/${id}/status`, { status });
      if (res && res.success) {
        SubsystemApp.showToast(status === 'disabled' ? t('Account deactivated') : t('Account reactivated'), 'success');
        this._load();
        return;
      }
      this._fail(res, t('Could not change the account status.'));
    } catch (err) {
      console.error('Status change failed', err);
      SubsystemApp.showToast(t('Could not change the account status.'), 'error');
    }
  },
};

window.RetailEmployees = RetailEmployees;
