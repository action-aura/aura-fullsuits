/**
 * Action Aura — Subsystem Core
 * Shared shell: router, sidebar, header, AI chat panel, and logo system.
 */

// ── Theme Engine ──────────────────────────────────────────────────────────────
// The light/dark switch. This object used to be something else twice over, and
// both priors are why it now looks over-careful:
//
//   * It was the ACCENT-PALETTE picker: six dark-HUD backgrounds (midnight /
//     deep-space / carbon / ...) applied by writing --bg-dark / --bg-panel as
//     INLINE styles on <html> -- near-black literals no stylesheet could
//     override, applied on EVERY boot because init() defaulted to 'midnight'.
//     The [data-app-theme] rules in main.css and a set of !important
//     counter-rules existed purely to fight that. All of that is gone: this
//     engine moves ONE attribute between two VALIDATED values and never writes
//     an inline style, so the stylesheet owns every colour in both themes.
//
//   * Separately, the first light/dark toggle shipped a 1.00:1 white-on-white
//     dark mode and was killed (see index.html's boot comment for the full
//     postmortem). Dark v2 is token-value-only in main.css, which is what makes
//     a toggle safe to have again -- but the PERSISTENCE discipline from that
//     postmortem is kept here verbatim: a NEW key that the broken era never
//     wrote, exact-match validation on read AND write, and the legacy keys
//     ('aura_theme' in index.html, 'aura_app_theme' here) actively removed so
//     nothing can quietly wire them back up.
//
// retail_design_theme_safety_test.js pins the validation and the key hygiene;
// retail_design_contrast_test.js proves the palette both of these values land on.
const ThemeEngine = {
  KEY: 'aura_theme_v2',   // NEVER 'aura_theme' -- that key belongs to the broken era
  current: 'light',

  // The dot literals are the swatch's CONTENT (each previews its theme's
  // --surface-app / --surface-panel), not styling -- the same reason the
  // .theme-swatch exemption exists in retail_design_tokens_test.js.
  themes: {
    light: { label: 'Light', dot: '#eaeef3', edge: '#b3bfcd' },
    dark:  { label: 'Dark',  dot: '#0f1319', edge: '#47566a' },
  },

  // THE one sanitizer. Every path that turns a stored/argument value into a
  // data-theme write goes through here, so "anything but the exact string
  // 'dark' is light" is a property of the engine, not of each caller's care.
  _sanitize(name) { return name === 'dark' ? 'dark' : 'light'; },

  apply(name) {
    const theme = this._sanitize(name);
    const changed = theme !== this.current ||
      document.documentElement.getAttribute('data-theme') !== theme;
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem(this.KEY, theme); } catch (e) { /* private mode */ }
    this.current = theme;
    // Refresh picker active state if open
    document.querySelectorAll('.theme-swatch').forEach(el => {
      el.classList.toggle('active', el.dataset.theme === theme);
    });
    // Canvas charts (Chart.js) read token values at build time via
    // RetailSystem._cssToken -- CSS cannot restyle pixels already drawn -- so
    // a theme change re-renders the active section to rebuild them. Guarded:
    // during boot the shell may not exist yet, and re-rendering an unchanged
    // theme would just cost a flicker.
    if (changed && window.SubsystemApp && SubsystemApp.active &&
        document.getElementById('sub-content')) {
      try { SubsystemApp._navigate(SubsystemApp.active); } catch (e) { /* mid-boot */ }
    }
  },

  init() {
    let saved = null;
    try { saved = localStorage.getItem(this.KEY); } catch (e) {}
    this.current = this._sanitize(saved);
    // Re-assert rather than trust: index.html's boot script already stamped
    // the attribute pre-paint, but the shell can also be booted by hosts with
    // their own HTML (the Android WebView wrapper), where nothing has.
    document.documentElement.setAttribute('data-theme', this.current);
    // The accent-palette era's key. Nothing reads it any more; leaving a key
    // whose stored value is 'midnight' in every shop's browser storage is an
    // invitation to wire it back up without reading this file first.
    try { localStorage.removeItem('aura_app_theme'); } catch (e) {}
  },

  toggle() { this.apply(this.current === 'dark' ? 'light' : 'dark'); },

  openPicker() {
    if (document.getElementById('theme-picker-panel')) {
      this.closePicker(); return;
    }
    const backdrop = document.createElement('div');
    backdrop.className = 'theme-picker-backdrop';
    backdrop.id = 'theme-picker-backdrop';
    backdrop.onclick = () => this.closePicker();
    document.body.appendChild(backdrop);

    const panel = document.createElement('div');
    panel.className = 'theme-picker-panel';
    panel.id = 'theme-picker-panel';

    const label = document.createElement('div');
    label.className = 'theme-picker-label';
    label.textContent = '🎨 ' + t('Theme');
    panel.appendChild(label);

    const grid = document.createElement('div');
    grid.className = 'theme-grid';
    Object.entries(this.themes).forEach(([key, def]) => {
      // A <button>, not the old click-wired <div>: Enter/Space and focus come
      // free, and .theme-swatch:focus-visible in main.css already draws the
      // ring for it.
      const swatch = document.createElement('button');
      swatch.type = 'button';
      swatch.className = 'theme-swatch' + (this.current === key ? ' active' : '');
      swatch.dataset.theme = key;
      swatch.addEventListener('click', () => this.apply(key));

      const dot = document.createElement('div');
      dot.className = 'theme-swatch-dot';
      dot.style.background = def.dot;
      dot.style.borderColor = def.edge;

      const name = document.createElement('div');
      name.className = 'theme-swatch-name';
      name.textContent = t(def.label);

      swatch.appendChild(dot);
      swatch.appendChild(name);
      grid.appendChild(swatch);
    });
    panel.appendChild(grid);

    document.body.appendChild(panel);
  },

  closePicker() {
    document.getElementById('theme-picker-panel')?.remove();
    document.getElementById('theme-picker-backdrop')?.remove();
  }
};


// ── KPI Drag Manager ──────────────────────────────────────────────────────────
const KPIDragManager = {
  _dragEl: null,

  init() {
    // Watch for .sub-kpi-grid containers being populated
    const observer = new MutationObserver(() => this._scan());
    observer.observe(document.body, { childList: true, subtree: true });
  },

  _scan() {
    document.querySelectorAll('.sub-kpi-grid:not([data-kpi-drag])').forEach(grid => {
      grid.setAttribute('data-kpi-drag', '1');
      // Re-run when the grid's own children change (re-render)
      new MutationObserver(() => this._wire(grid)).observe(grid, { childList: true });
      this._wire(grid);
    });
  },

  _wire(grid) {
    grid.querySelectorAll('.sub-kpi-card').forEach((card, i) => {
      card.setAttribute('draggable', 'true');
      if (card.dataset.kpiWired) return;
      card.dataset.kpiWired = '1';
      card.addEventListener('dragstart', e => {
        this._dragEl = card;
        card.classList.add('kpi-dragging');
        e.dataTransfer.effectAllowed = 'move';
      });
      card.addEventListener('dragend', () => {
        this._dragEl = null;
        grid.querySelectorAll('.sub-kpi-card').forEach(c => {
          c.classList.remove('kpi-dragging', 'kpi-drag-over');
        });
      });
      card.addEventListener('dragover', e => { e.preventDefault(); card.classList.add('kpi-drag-over'); });
      card.addEventListener('dragleave', () => card.classList.remove('kpi-drag-over'));
      card.addEventListener('drop', e => {
        e.preventDefault();
        card.classList.remove('kpi-drag-over');
        if (!this._dragEl || this._dragEl === card) return;
        // Swap positions in DOM
        const parent = card.parentNode;
        const allCards = [...parent.querySelectorAll('.sub-kpi-card')];
        const fromIdx = allCards.indexOf(this._dragEl);
        const toIdx   = allCards.indexOf(card);
        if (fromIdx < toIdx) parent.insertBefore(this._dragEl, card.nextSibling);
        else                  parent.insertBefore(this._dragEl, card);
        this._dragEl.classList.remove('kpi-dragging');
      });
    });
  }
};


// ── Logo System ───────────────────────────────────────────────────────────────
const LogoSystem = {
  _retypeTimers: [],

  init() {
    document.querySelectorAll('.aura-logo').forEach(logo => {
      logo.addEventListener('mouseenter', () => this._startRetype(logo));
      logo.addEventListener('mouseleave', () => this._stopRetype(logo));
      logo.addEventListener('click', () => {
        // In standalone app mode, return to the subsystem grid instead of landing page
        if (window.IS_STANDALONE) {
            if (window.SubsystemApp) window.SubsystemApp.exit();
            return;
        }
        // Always go straight back to the website landing page
        if (window.SubsystemApp && window.SubsystemApp.active) {
          window.SubsystemApp.active = null; // clear active flag cleanly
        }
        if (window.AuraRouter) {
          window.AuraRouter.goLanding();
        }
      });
    });
  },

  _stopRetype(logo) {
    this._retypeTimers.forEach(t => clearTimeout(t));
    this._retypeTimers = [];
    const nameEl = logo.querySelector('.logo-name');
    if (nameEl) nameEl.innerHTML = 'Action<strong>Aura</strong>';
  },

  _startRetype(logo) {
    const nameEl = logo.querySelector('.logo-name');
    if (!nameEl) return;
    const text = 'ActionAura';
    let i = 0;
    nameEl.innerHTML = '';
    const type = () => {
      if (i <= text.length) {
        const display = text.slice(0, i);
        const action = display.slice(0, 6);
        const aura = display.slice(6);
        nameEl.innerHTML = `${action}<strong>${aura}</strong><span class="logo-cursor">|</span>`;
        i++;
        this._retypeTimers.push(setTimeout(type, 55));
      } else {
        nameEl.innerHTML = 'Action<strong>Aura</strong>';
      }
    };
    type();
  }
};

// ── Subsystem App Shell ────────────────────────────────────────────────────────
const SubsystemApp = {
  active: null,
  currentSection: 'dashboard',
  _syncHealthTimer: null,
  _syncBannerEl: null,
  _syncPollDisabled: false,

  // Identity of the logged-in user (populated from /api/auth/session in init()).
  currentUser: {},
  role: '',         // global role: 'admin' | 'employee'
  clinicRole: '',   // clinic overlay: '' | 'doctor' | 'secretary'
  // `null` until /api/auth/session resolves, meaning "unknown, assume full
  // access" -- see hasCapability() below for why that default is safe. Once
  // resolved it is either `null` still (the field hasn't landed on the
  // session response yet) or an array of the user's own granted
  // `retail.*` codes (see hasCapability()).
  capabilities: null,

  // True if the current user may access a clinic area restricted to the given
  // clinic roles. The clinic owner (global admin) always passes. Mirrors the
  // backend require_clinic_role() gate so the UI hides what the API would reject.
  canClinic(...roles) {
    if (this.role === 'admin') return true;          // owner sees everything
    if (!roles || roles.length === 0) return true;   // unrestricted area
    return roles.includes(this.clinicRole);
  },

  // ── Accent: let the stylesheet win ────────────────────────────────────────
  //
  // `systems.retail.accent` is a hex literal sitting in JavaScript, and five
  // separate call sites used to push it straight onto the root element with
  // setProperty(). Two problems with that, one cosmetic and one structural:
  //
  //   * STRUCTURAL: an inline style on documentElement beats every stylesheet
  //     rule short of !important, so the token layer in css/ physically cannot
  //     restyle the accent while these lines exist. The accent was the one
  //     colour in this product that no stylesheet could own.
  //
  //   * COSMETIC, but it matters at a till: the value is #f43f5e, a rose that
  //     sits close enough to the danger red that "accent" and "refusal" stop
  //     being distinguishable at a glance. On a screen where colour is meant
  //     to carry meaning -- money in, money out, a warning, a refusal -- an
  //     accent occupying the refusal hue quietly spends the one signal you
  //     most need to keep unambiguous.
  //
  // This does NOT pick a colour here; choosing it is the token layer's job,
  // not JavaScript's. It asks one question instead: DOES A TOKEN LAYER EXIST?
  // If css/main.css defines --accent-action, that stylesheet already sets
  // --sub-accent / --sub-accent-rgb from it at :root, and the correct action
  // is to write nothing at all -- because an inline property on
  // documentElement outranks every :root rule and would pin the accent to the
  // JS hex forever, on every install, invisibly.
  //
  // --accent-action is used as the sentinel precisely because JavaScript never
  // writes it. Probing --sub-accent instead would be useless: after the first
  // call this method's own inline value is what getComputedStyle returns, so
  // the check would pass on boot and fail on every subsequent navigation.
  //
  // If no token layer is present (an older cached stylesheet), the previous
  // behaviour is preserved exactly.
  _tokenLayerOwnsAccent() {
    try {
      return !!(getComputedStyle(document.documentElement)
        .getPropertyValue('--accent-action') || '').trim();
    } catch (e) {
      return false;   // no computed style (tests / very early boot)
    }
  },

  _applyAccent(sys) {
    if (this._tokenLayerOwnsAccent()) return;
    const accent = sys && sys.accent;
    const rgb    = sys && sys.accentRgb;
    // Both or neither: a colour with no rgb triplet would break every
    // rgba(var(--sub-accent-rgb), a) in the product.
    if (!accent || !rgb) return;
    try {
      document.documentElement.style.setProperty('--sub-accent', accent);
      document.documentElement.style.setProperty('--sub-accent-rgb', rgb);
    } catch (e) { /* non-DOM host */ }
  },

  // True if the current user's per-user capability grants include `code`.
  // RENDERING ADVICE ONLY -- every route keeps its own server-side gate
  // (mt_auth.mt_require_capability); this exists purely so the shell can
  // choose not to fetch/render a tile the server would 403 anyway (see
  // subsystem-retail.js's _renderDashboard, the cashier-dashboard-on-login
  // fix this was built for). Nothing downstream of this method may itself
  // become an authorization decision.
  //
  // `this.capabilities === null` covers two cases this method deliberately
  // treats the same way -- "/api/auth/session hasn't resolved yet" and "the
  // backend hasn't shipped the `capabilities` field yet" -- and answers
  // `true` for both, i.e. fails OPEN. That is the opposite of every real
  // authorization check in this codebase, and is only safe here because
  // rendering advice failing open just means a tile renders and its own
  // fetch 403s -- the status quo before this method existed, not a new hole.
  // It is what keeps a server build that predates the field from blanking
  // every gated tile for every role.
  //
  // 2026-08-21: that fail-open default has a sharp edge, and it drew blood.
  // See _adoptSessionCapabilities() below -- this method answered `true` for
  // every code, for every role, on every install, for as long as the list was
  // being read out of the wrong key. Failing open means a wiring mistake in
  // the line that FILLS `this.capabilities` cannot announce itself here: it
  // looks exactly like "no capability information available", which is a
  // legitimate state. Anything that resolves this list needs its own test;
  // this method cannot be the place a mistake surfaces.
  hasCapability(code) {
    if (!Array.isArray(this.capabilities)) return true;
    return this.capabilities.includes(code);
  },

  // The exact per-item visibility rule _renderShell's nav used to inline in
  // its filter(). Pulled out to its own method so the sidebar-grouping pass
  // (launch-readiness 2026-08-29) can apply it once per item and reuse the
  // result for both the ungrouped Dashboard entry and every sys.navGroups
  // bucket, without re-deriving it or changing what it decides. Every axis
  // stays exactly as it was: `roles` (canClinic), `desktopOnly` (this
  // platform), `adminOnly` (this DEVICE, this.isAdminDevice), `ownerOnly`
  // (this USER's role) and `capability` (this user's per-grant list via
  // hasCapability). retail_employee_management_test.py greps app-shell.js's
  // SOURCE for the literal fragment `!item.ownerOnly || this.role === 'admin'`
  // -- keep that exact text if this method is ever touched.
  _isNavItemVisible(item) {
    return (!item.roles || this.canClinic(...item.roles)) &&
      (!item.desktopOnly || !/Android/i.test(navigator.userAgent || '')) &&
      (!item.adminOnly || this.isAdminDevice) &&
      (!item.ownerOnly || this.role === 'admin') &&
      (!item.capability || this.hasCapability(item.capability));
  },

  // Resolve `this.capabilities` from a GET /api/auth/session body.
  //
  // Extracted from init() into its own named method for one reason: it is the
  // single line in this file whose correctness cannot be established by
  // reading it. Everything else here is "does this code do what it says";
  // this is "does the server put the value where this code looks", which is a
  // question about a different file in a different language, and the answer
  // was NO for the entire life of the capability feature.
  //
  // The bug: this used to read `sess.user.capabilities`. get_session() in
  // commercial_runtime/identity/onboarding_routes.py returns `capabilities`
  // as a TOP-LEVEL key, a sibling of `user`, never a member of it. Confirmed
  // by booting the app and logging in as each role, not by re-reading the
  // handler -- a cashier's real response body is:
  //
  //   {"authenticated": true,
  //    "capabilities": ["retail.cash.close", "retail.refund", "retail.sell"],
  //    "is_mt": true, "language": "en",
  //    "user": {"id": "...", "role": "cashier", "email": "...", ...}}
  //
  // So the old read was permanently `undefined`, `this.capabilities` was
  // permanently `null`, and hasCapability() therefore answered `true` for
  // everything (it fails open on null, correctly and by design). The visible
  // consequence: subsystem-retail.js's `_renderDashboard` cashier-landing
  // panel -- built specifically so a cashier's first screen after login is
  // not a 403 -- never fired once. The fix shipped, the bug it fixed kept
  // happening, and nothing was red, because a gate that never engages is
  // indistinguishable from a gate on a permissive account.
  //
  // Both locations are accepted, top-level first. Not defensive padding: the
  // clinic product shares this identity stack and its own session route is a
  // separate code path, and `user` is the more obvious place for a future
  // contributor to add it. Reading both costs one `Array.isArray` and removes
  // the entire failure mode, whose whole character is that it is silent.
  // retail_attribution_i18n_test.py asserts the server keeps sending the
  // top-level key AND that this file still reads it -- the seam itself, which
  // is the only place this class of bug is visible.
  _adoptSessionCapabilities(session) {
    const sess = session || {};
    // `Array.isArray`, not truthiness. An intentionally EMPTY grant list (a
    // user denied every capability) must NOT be folded into the same `null`
    // bucket as "the server told us nothing": one means "deny everything this
    // list doesn't name", the other means "nothing has been checked, render
    // as before". Truthiness cannot tell them apart -- `[]` is truthy in JS,
    // but `[] || fallback` is not the trap; `if (!caps)` is, and this is the
    // shape that avoids ever writing it.
    if (Array.isArray(sess.capabilities)) {
      this.capabilities = sess.capabilities;
    } else if (sess.user && Array.isArray(sess.user.capabilities)) {
      this.capabilities = sess.user.capabilities;
    } else {
      this.capabilities = null;
    }
    return this.capabilities;
  },

  // ── HTML escaping ─────────────────────────────────────────────────────────
  // _setupKeyRetry() below renders a backend-supplied `reason` string into
  // .innerHTML, and used to do it behind a `this._esc ? this._esc(reason) :
  // reason` guard -- except SubsystemApp never defined _esc anywhere, so the
  // ternary was decorative: it always took the else branch and the raw string
  // always reached the sink. The sink is real (that string is the activation
  // `detail` the Owner licensing service returned, not a literal from this
  // build), so the helper is real now and the guard is gone -- a guard that
  // silently no-ops is worse than no guard, because it reads as handled.
  _esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  },

  // ── Device-activation predicate (single source of truth) ──────────────────
  // /api/licensing/status's NOT_CONFIGURED shape is ambiguous on its own: the
  // backend returns it both when licensing is genuinely unconfigured for this
  // build AND when it IS configured but this device has simply never
  // activated (no local state record yet) -- see routes.py::_not_configured_
  // response() vs status_presenter.py::present_status(None). Only the first
  // case attaches a `detail` field, so the ABSENCE of `detail` is what means
  // "configured, just not activated yet". No backend change needed.
  //
  // This test used to be copy-pasted in THREE places: the boot gate in
  // init(), showSetupModal() below, and licensing.js -- and the licensing.js
  // copy had drifted, omitting 'ACTIVATING' entirely. That divergence was a
  // real dead end, not a cosmetic one: a device left in ACTIVATING was
  // bounced to licensing.html by the boot gate here (which does count
  // ACTIVATING), and licensing.js then decided no activation was needed and
  // rendered the read-only status card instead of the key form -- no way
  // forward from either screen. One predicate now. licensing.js is a
  // separate script scope on a separate page and cannot import from this
  // file, so it mirrors this exact three-way test with a comment pointing
  // back here; keep the two in step.
  _needsActivation(lic) {
    if (!lic) return false;
    return lic.current_state === 'ACTIVATION_REQUIRED'
      || lic.current_state === 'ACTIVATING'
      || (lic.current_state === 'NOT_CONFIGURED' && !lic.detail);
  },

  // "This device has already joined a licensed shop": the honest signal is a
  // DATA fact, not a state list. status_presenter.py exposes `installation_id`
  // only once Owner has issued one, so a build with no Owner wired up
  // (NOT_CONFIGURED + detail), a fresh install (ACTIVATION_REQUIRED), or a
  // null payload can never satisfy it. The `_needsActivation()` half then
  // removes the one installation-bearing state that is still pre-activation:
  // ACTIVATING, where Owner has the device pending approval and the owner's
  // account cannot arrive yet.
  //
  // The first cut of the join door used `!_needsActivation(lic)` alone --
  // which is also true for a never-licensed install, so a plain dev/demo
  // build with needs_setup would have sat on "Connecting to your shop…" for
  // the full 120 s before being offered setup at all. Pinned by
  // retail_join_shop_modal_test.js (no-Owner and pending-approval checks).
  _isJoinedDevice(lic) {
    if (!lic || !lic.installation_id) return false;
    return !this._needsActivation(lic);
  },

  // ── "A key was already submitted, Owner hasn't ruled on it yet" marker ────
  // POST /api/licensing/activate can answer 202 PENDING -- Owner is holding
  // this activation for a human to approve, which is neither success nor
  // rejection. On that path the backend deliberately persists NO local state
  // record (commercial_runtime/licensing_contracts/activation.py), so GET
  // /api/licensing/status keeps answering a bare NOT_CONFIGURED, which
  // _needsActivation() above correctly reads as "still needs activating".
  //
  // With no client-side memory of the submission that produced an endless
  // loop: register -> 202 PENDING -> account created -> init() -> boot gate
  // sees NOT_CONFIGURED -> redirect to licensing.html -> key form -> user
  // re-enters the SAME key -> 202 PENDING -> key form -> forever. This
  // marker is the memory that lets the UI say "we already have your key,
  // we're waiting on approval" instead of asking a second time.
  //
  // localStorage rather than sessionStorage on purpose: the approval is a
  // human on Owner's side and can easily outlive this browser/pywebview
  // window. Every access is wrapped -- localStorage throws outright (not
  // returns null) in some restricted webview and private-browsing contexts,
  // and a licensing nicety must never be able to break boot.
  PENDING_ACTIVATION_KEY: 'aura.licensing.pendingActivation',

  // Mirrors licensing.js's PENDING_MAX_AGE_MS -- see there for why a pending
  // marker has to be able to age out at all. Keep the two in step.
  PENDING_ACTIVATION_MAX_AGE_MS: 7 * 24 * 60 * 60 * 1000,

  // In-document fallback for the case the comment above says is real: when
  // Storage throws, every write here silently no-ops, and a marker written
  // one line earlier would be unreadable by the check a few lines later. It
  // cannot survive a navigation (nothing client-side can, once Storage is
  // gone), but it keeps same-document decisions -- notably showSetupModal()'s
  // "has this user already given us a key?" -- correct instead of quietly
  // wrong. A server-side record would be the real fix and does not exist yet:
  // activation.py records an ACTIVATION_PENDING event that no route reads back.
  _pendingActivationMemory: null,

  // The submitted key, in memory and NOWHERE else -- not localStorage, not
  // sessionStorage, not the URL. Same rule licensing.js states at its own
  // `pendingKey`: this is credential material, and the backend goes out of
  // its way to stop holding it (routes.py activate() nulls it in a `finally`),
  // so a frontend that wrote it to disk would quietly undo that.
  //
  // It exists because re-POSTing /api/licensing/activate is the ONLY route
  // that can resolve a held activation -- POST /api/licensing/check-in
  // short-circuits to a canned ACTIVATION_REQUIRED for any device with no
  // persisted state record, and a PENDING device is exactly that. Keeping the
  // key here is what lets _setupAwaitingApproval() below finish the job in
  // this same document instead of bouncing the user to licensing.html, which
  // is a separate document that cannot see this value and would therefore
  // have to ask for the key a second time.
  _pendingActivationKey: '',

  // `at` is ISO-8601, not epoch milliseconds. This record is written here and
  // read by a DIFFERENT document (licensing.js), and it is exactly the kind of
  // thing someone ends up eyeballing in devtools when an install is stuck on
  // "waiting for approval": "2026-08-18T08:14:09.784Z" says what it is and
  // when it happened, 1755504849784 does not. Both sides parse it back with
  // Date.parse() and treat NaN as absent, so a hand-edited or truncated value
  // degrades to "no marker" rather than to an un-ageable one.
  //
  // installation_id comes off the 202 body (routes.py attaches it) so the
  // awaiting screens can still name the installation, which GET
  // /api/licensing/status cannot supply for a pending device --
  // present_status(None) omits it entirely, and a pending device is always
  // that case. Never the license key: that lives in memory only.
  _markActivationPending(installationId) {
    const rec = { v: 1, at: new Date().toISOString(), installation_id: installationId || null };
    this._pendingActivationMemory = rec;
    try { localStorage.setItem(this.PENDING_ACTIVATION_KEY, JSON.stringify(rec)); } catch (e) {}
  },

  _clearActivationPending() {
    this._pendingActivationMemory = null;
    this._pendingActivationKey = '';
    try { localStorage.removeItem(this.PENDING_ACTIVATION_KEY); } catch (e) {}
  },

  _isActivationPending() {
    // Same record shape licensing.js reads (separate document, separate
    // script scope, nothing to import -- keep the two in step). A value that
    // is not that shape carries no timestamp, so it could never be aged out;
    // an un-ageable marker is exactly the stale-marker failure the timestamp
    // exists to bound, so it is discarded rather than trusted.
    let rec = this._pendingActivationMemory;
    if (!rec) {
      let raw = null;
      try { raw = localStorage.getItem(this.PENDING_ACTIVATION_KEY); } catch (e) { return false; }
      if (!raw) return false;
      // JSON.parse does NOT throw on a bare '1' left by an earlier build -- it
      // returns the number 1 -- so the shape check below, not the catch, is
      // what rejects it.
      try { rec = JSON.parse(raw); } catch (e) { return false; }
      if (!rec || typeof rec !== 'object') return false;
    }
    const at = Date.parse(rec.at);
    if (isNaN(at)) return false;   // no usable timestamp => cannot age out => distrust
    return (Date.now() - at) <= this.PENDING_ACTIVATION_MAX_AGE_MS;
  },

  // A real toggle again -- but only because the work its previous comment
  // demanded actually happened. For one era this was deliberately a no-op that
  // forced light: the first dark theme rendered white-on-white at 1.00:1
  // because the compatibility layer was scoped to [data-theme="light"] while
  // dark was injected literals, and "restoring a dark theme is real work, not
  // a flag flip" was written right here. Dark v2 IS that work: a full palette
  // solved to the same AA/AAA bar (html[data-theme="dark"] in main.css), the
  // compatibility layer made theme-agnostic, and the injected chrome fully
  // tokenised. Delegating to ThemeEngine keeps every data-theme write behind
  // its one sanitizer -- this method cannot produce a value the engine would
  // not.
  //
  // Kept as a method (rather than pointing callers at ThemeEngine) because it
  // was a documented public entry point (#15) and stale callers may remain.
  toggleTheme() {
    ThemeEngine.toggle();
  },

  systems: {
    retail: {
      name: 'Retail & POS',
      icon: '🛍️',
      accent: '#f43f5e',
      accentRgb: '244,63,94',
      nav: [
        { id: 'dashboard',  label: 'Dashboard',        icon: '🏠' },
        { id: 'pos',        label: 'Point of Sale',     icon: '🛒' },
        { id: 'products',   label: 'Products',          icon: '📦' },
        { id: 'categories', label: 'Categories',        icon: '🏷️' },
        { id: 'customers',  label: 'Customers',         icon: '👥' },
        // Launch-readiness 2026-08-30 (ROADMAP.md "retail schema v23"):
        // promotions wave 1 -- per-product/per-category discount rules the
        // POS resolves automatically at checkout. `capability: 'retail.discount'`
        // reuses the EXISTING CAP_DISCOUNT code (commercial_runtime/identity/
        // user_accounts.py) rather than inventing a new one: configuring a
        // promotion is the same authority as typing a manual discount at the
        // till, and ROLE_CAPABILITIES already grants it to manager/admin and
        // withholds it from a cashier by default. Same mechanism as the
        // Reports entry above -- hiding the nav entry is not the enforcement,
        // just the invitation not to walk into a screen every button on which
        // will 403.
        { id: 'promotions', label: 'Promotions',        icon: '🎁', capability: 'retail.discount' },
        { id: 'suppliers',  label: 'Suppliers',         icon: '🏭' },
        { id: 'purchases',  label: 'Purchase Orders',   icon: '📋' },
        { id: 'returns',    label: 'Returns',           icon: '↩️' },
        // `capability` is a THIRD axis, alongside `adminOnly` (this device)
        // and `ownerOnly` (this user's role) -- see the note on `employees`
        // below for why conflating those two breaks a feature in both
        // directions at once. This one is per-USER GRANT: the actual
        // user_permissions rows, which an owner can turn off for one person
        // without changing their role, so neither of the other two axes can
        // express it.
        //
        // Every panel on the Reports screen reads a route decorated
        // @mt_require_capability(CAP_REPORTS) -- report_summary,
        // report_sales_trend, report_top_products, report_payment_methods,
        // report_by_branch -- and ROLE_CAPABILITIES grants a cashier only
        // {sell, refund, cash.close}. Unconditional, this entry invited a
        // cashier to click through to five 403s in one page load: the same
        // bug class as the cashier-dashboard-on-login fix, and NOT covered by
        // it (that one guards `_renderDashboard`; nothing guarded this).
        //
        // Hiding the entry is half the fix. The other half is in
        // subsystem-retail.js's `_renderReports`, because `_navigate`
        // ('reports') is reachable without this link at all -- AuraRouter
        // persists the last section to the URL hash and replays it on the
        // next launch, so a shared till where a manager last opened Reports
        // drops the next cashier straight onto that screen.
        { id: 'reports',    label: 'Reports',           icon: '📊', capability: 'retail.reports' },
        { id: 'scanner',    label: 'Barcode Scanner',   icon: '🔦', desktopOnly: true },
        // feat/reorder-automation-foundation: gated on this.isAdminDevice
        // (resolved once at init() via GET /api/devices/me -- see that
        // method), same adminOnly mechanism the _renderShell nav filter
        // below applies to every entry with this flag. Hidden entirely
        // (not just disabled) on any device that isn't this company's
        // single admin device, fail-closed if the check couldn't run.
        // Multi-device Phase 1 (design §3). NOTE THE FLAG: `ownerOnly`, not
        // `adminOnly`. They are different axes and picking the wrong one
        // breaks the feature in both directions at once. `adminOnly` means
        // `this.isAdminDevice` -- the DEVICE axis, which design §3 says in as
        // many words is "not users.role='admin'". Every /api/admin/employees
        // route gates on `session['mt_role'] == 'admin'` -- the USER axis. Had
        // this entry reused `adminOnly`, the owner would lose the screen the
        // moment they picked up their phone (a second terminal is by
        // definition not the admin device), while a cashier standing at the
        // admin terminal would be shown a screen every button on which
        // answers 403.
        { id: 'employees',    label: 'Employees',       icon: '👤', ownerOnly: true },
        // ci-hardening-w0.3 continuation: THE DOORWAY. POST /api/sub/retail/
        // branches (retail_api.py:7894, create_branch) has been complete and
        // gated since Phase 5 wave A -- nothing in the frontend ever called
        // it. Every install therefore self-heals exactly one branch
        // (_default_branch) and a shop has had no way to add a second, ever,
        // which left an entire multi-store programme already shipped on top
        // of it (device-branch pinning dc22b04, branch-scoped accounts
        // ab6b3c1, branch managers 93cef8c, the head-office comparison
        // chart) real, tested and unreachable.
        //
        // `capability: 'retail.employees'` matches create_branch's own
        // @mt_require_capability(CAP_EMPLOYEES) decorator exactly, the same
        // reasoning as every other capability-gated entry in this list: a
        // cashier must not be shown a screen every button of which would
        // 403. list_branches (GET) carries no capability of its own -- read
        // access is not the gate here, creating a new one is.
        { id: 'branches',     label: 'Branches',        icon: '🏦', capability: 'retail.employees' },
        { id: 'admin-center', label: 'Settings',        icon: '⚙️', adminOnly: true },
        // ci-hardening-w0.3 continuation: THE DOORWAY, a second one on this
        // branch. GET/POST /api/notifications/{status,settings,outbox,
        // outbox/run-once} (commercial_runtime/notifications/routes.py) have
        // been complete since the outbox/worker/SMTP client shipped, plus a
        // real trigger already enqueuing into it (low-stock alerts,
        // core/retail/whatsapp_hook.py's sibling). WhatsApp got a settings
        // page (whatsapp.html, linked from the Settings card below); email
        // never did. So SMTP recipients could not be configured by any
        // user, ever, and the channel was inert in practice.
        //
        // `ownerOnly`, NOT `adminOnly` -- the exact distinction the
        // Employees and Stock Accuracy entries above both spell out. Every
        // mutating route here (`_require_admin` in routes.py) reads
        // `session['mt_role'] == 'admin'`, the USER axis; `adminOnly` means
        // `this.isAdminDevice`, the DEVICE axis Admin Center actually uses.
        // Picking `adminOnly` here would show this screen to a manager
        // standing at the admin terminal (whose every mutating click would
        // 403) while hiding it from the owner working from a second device.
        { id: 'email-notifications', label: 'Email Notifications', icon: '📧', ownerOnly: true },
        // ci-hardening-w0.3 continuation: THE DOORWAY, a third one on this
        // branch, and the one that matters most: licensing.js promises a
        // customer whose licence is RESTRICTED/SUSPENDED/EXPIRED/REVOKED --
        // the exact moment they are wondering whether they can get their
        // data out -- "Your existing data is safe and remains viewable;
        // backup, restore, and export remain available." Four backup routes
        // (commercial_runtime/backup/routes.py: POST /create, GET /list,
        // GET /download/<filename>, POST /restore) and three CSV export
        // routes (retail_api.py: /reports/export/{sales,payments,
        // cash-sessions}) have all been complete and correctly gated since
        // before this branch started. Nothing in the frontend ever called
        // any of them, so the promise above was false at the UI level.
        //
        // Both axes, matching _renderStockAccuracy's precedent exactly for
        // the identical reason: `capability: 'retail.reports'` matches
        // export_{sales,payments,cash_sessions}_csv's own
        // @mt_require_capability(CAP_REPORTS) decorator, and `ownerOnly`
        // matches backup/routes.py's `_require_admin()`, which
        // reads `session['mt_role'] == 'admin'` (the USER axis) for EVERY
        // one of its four routes, none of which carry a capability code at
        // all. ROLE_ADMIN holds every capability including retail.reports
        // (user_accounts.py's ROLE_CAPABILITIES), so gating the whole
        // screen on the stricter (admin) axis never costs a manager the
        // export half either -- it would already 403 on the backup half.
        //
        // Deliberately NOT keyed on licence state anywhere: neither
        // backup/routes.py nor the three export routes carry a
        // @require_license_capability decorator at all (unlike most of
        // this file's mutating routes), so this screen is reachable and
        // fully functional under EVERY licence state, RESTRICTED included
        // -- the exact state the promise above is made in. See
        // retail_backup_export_test.js's testWorksUnderRestrictedLicence.
        { id: 'backup-export', label: 'Backup & Export', icon: '💾', ownerOnly: true, capability: 'retail.reports' },
        // feat/audit-log-viewer: same adminOnly mechanism as Admin Center
        // above -- refund/void/product-change audit trail carries every
        // user's attribution, not just this device's, so it's gated the
        // same way rather than shown to every logged-in user. The backend
        // route (GET /api/sub/retail/audit-log) also enforces this itself
        // (see retail_api.py's _is_admin_device) -- unlike Admin Center's
        // reorder-requests route, this one does NOT rely on nav-hiding alone.
        //
        // Both axes, because list_audit_log gates on both and its docstring
        // says why in as many words: the device check answers "is this the
        // shop's admin terminal", the capability answers "is this person
        // allowed to read the shop's records". `adminOnly` alone leaves a
        // cashier standing AT the admin terminal looking at an entry that
        // 403s -- the identical bug the Reports entry above just had.
        { id: 'audit-log',   label: 'Audit Log',        icon: '📜', adminOnly: true, capability: 'retail.reports' },
        // Phase 3 (docs/launch-readiness/phase3-ledger-truth.md): the stock
        // accuracy report, over GET /api/sub/retail/inventory/reconciliation.
        //
        // `ownerOnly`, NOT `adminOnly` -- and the distinction is the one the
        // Employees entry above spells out, applied to a different route.
        // That route enforces `_require_company_admin()`, which reads
        // `session['mt_role'] == 'admin'`: the USER axis. `adminOnly` means
        // `this.isAdminDevice`, the DEVICE axis, and picking it here would
        // hide the shop's own stock report from the owner the moment they
        // opened it on a second terminal, while showing it to a manager
        // standing at the admin till whose one request answers 403.
        //
        // The capability is the third axis and is also real: the route
        // carries @mt_require_capability(CAP_REPORTS) like every other read
        // that dumps the shop's position. Both are needed, exactly as on the
        // Audit Log entry above -- and neither is the enforcement. The render
        // guard in subsystem-retail.js's _renderStockAccuracy repeats both,
        // because AuraRouter replays the last section out of the URL hash and
        // reaches this screen with no nav click in between.
        { id: 'stock-accuracy', label: 'Stock Accuracy', icon: '⚖️', ownerOnly: true, capability: 'retail.reports' },
        // Launch-readiness 2026-08-29 ("the two exception queues both need
        // ONE screen, not two"): the oversell queue (stock_exceptions,
        // Phase 7 stage 7d-i/ii) and the discarded-catalogue-edit queue
        // (sync_conflicts, Phase 6 stage 6a-ii) as one surface.
        //
        // `capability` only, matching BOTH read routes
        // (list_stock_exceptions / list_sync_conflicts, both gated
        // CAP_REPORTS with no company-admin requirement) -- deliberately
        // NOT `ownerOnly`, unlike Stock Accuracy immediately above. Neither
        // route discloses an unpaginated whole-catalogue dump; both are the
        // same operational disclosure tier as Reports/Audit Log. The
        // per-row RESOLVE action inside the screen needs a stricter
        // capability of its own (retail.stock.adjust) -- gated inside
        // _renderExceptions, not here, because that authority varies by
        // ROW section, not by whether the screen is reachable at all.
        { id: 'exceptions', label: 'Exceptions', icon: '⚠️', capability: 'retail.reports' },
      ],

      // Sidebar section grouping (launch-readiness 2026-08-29: "the left
      // panel needs reorganizing -- it's so much stuff on the left you get
      // distracted"). This is a RENDER-TIME arrangement ONLY -- it does not
      // gate anything and must never change WHO sees WHICH entry, only how
      // the entries an owner already sees are laid out. `nav` above is left
      // completely untouched: it stays the flat, per-item source of truth
      // every capability/adminOnly/ownerOnly/desktopOnly check already reads
      // (via _isNavItemVisible), and the exact shape
      // retail_employee_management_test.py and
      // retail_reports_capability_gate_test.js both assert against with a
      // regex/render probe -- it cannot become a nested structure.
      //
      // Dashboard is deliberately absent from every group here: it is the
      // home screen, not a member of a category, and _renderShell renders it
      // before walking this list.
      //
      // Grouped by what a shopkeeper is DOING, not by what the data model
      // calls things: Customers sits under Sell because you reach for a
      // customer mid-sale, not while managing inventory; Purchase Orders
      // sits under Stock because it is how stock arrives.
      //
      // Every id below must name exactly one entry in `nav` above (other
      // than 'dashboard'), and every non-dashboard `nav` entry must appear in
      // exactly one group -- retail_nav_groups_test.js's
      // testAllFifteenDestinationsReachableForOwner and
      // testGroupsRenderInSpecifiedOrderWithMembers pin both directions, so a
      // future nav entry that forgets a group fails loudly instead of
      // silently vanishing from the sidebar.
      //
      // A group whose every item is filtered out by _isNavItemVisible must
      // render NO header at all -- see _renderShell's groupsHTML below. An
      // empty section header is worse than the flat list this replaces.
      navGroups: [
        { label: 'Sell',    items: ['pos', 'returns', 'scanner', 'customers', 'promotions'] },
        { label: 'Stock',   items: ['products', 'categories', 'suppliers', 'purchases'] },
        { label: 'Insight', items: ['reports', 'stock-accuracy', 'exceptions', 'audit-log'] },
        { label: 'Admin',   items: ['employees', 'branches', 'admin-center', 'email-notifications', 'backup-export'] },
      ],
    },
  },

  async init() {
    // ── Email verification / password reset links ──────────────────────────
    // Checked before the licensing/auth gates below -- these are one-time
    // links a user opens directly (from the email verification.py sends),
    // not part of the normal boot sequence, and must render regardless of
    // activation/session state. Same "#fragment/token" shape as the
    // (currently frontend-unwired) employee-invite link this was modeled
    // after -- see commercial_runtime/identity/onboarding_routes.py.
    const hash = location.hash || '';
    if (hash.startsWith('#verify-email/')) {
      this._showVerifyEmailScreen(hash.slice('#verify-email/'.length));
      return;
    }
    if (hash.startsWith('#reset-password/')) {
      this._showResetPasswordScreen(hash.slice('#reset-password/'.length));
      return;
    }
    if (hash.startsWith('#setup/')) {
      this._showEmployeeSetupScreen(hash.slice('#setup/'.length));
      return;
    }

    // ── Onboarding status ───────────────────────────────────────────────────
    // AUDIT-fix 2026-08-17: must run BEFORE the device-activation gate that
    // used to sit here unconditionally. A brand-new install (no admin
    // account yet) used to hit a pre-login "enter your license key" screen
    // FIRST, then a completely separate "create your account" screen after
    // -- two disconnected steps, key-before-account, with no way to land on
    // one coherent "get started" action. Registration now collects the
    // license key itself (see showSetupModal()), so a fresh install skips
    // this old pre-login gate entirely and goes straight there instead.
    let needsSetup = false;
    if (!window.isDemoMode && sessionStorage.getItem('demo_mode') !== 'true') {
      try {
        const status = await fetch('/api/onboarding/status', { cache: 'no-store' }).then(r => r.json());
        needsSetup = !!status.needs_setup;
      } catch (e) {
        // Network hiccup: fail open (needsSetup stays false), same as every
        // other best-effort check in this init() sequence.
      }
    }
    if (needsSetup) {
      // Not showSetupModal() directly: a device that has just JOINED a shop
      // is needs_setup too, and must wait for its owner's account instead of
      // being asked to create one. _openFirstRun() is the one place that
      // tells the two apart -- see its comment for the launch this got wrong.
      await this._openFirstRun();
      return;
    }

    this._authPrompted = false;     // re-arm the 401 guard on every (re)init
    this._installAuthGuard();
    ThemeEngine.init();
    KPIDragManager.init();
    // feat/reorder-automation-foundation: fail-closed default -- the
    // Admin Center nav entry (adminOnly: true, see `systems.retail.nav`
    // below) stays hidden unless GET /api/devices/me is reached AND
    // explicitly reports is_admin_device=true. Set BEFORE the auth gate
    // below so any early return (setup/relogin modal) still leaves this
    // defined and hidden, never undefined.
    this.isAdminDevice = false;
    // Same fail-closed default, same reason: an unreachable/failed
    // /api/devices/me must never leave this undefined, or the claim prompt
    // below would render off a `undefined === true` that happened to be
    // falsy today and something else tomorrow.
    this.canClaimAdminDevice = false;

    // Which optional modules (e.g. the AI Assistant) this installation is
    // licensed for. The standalone-shell trim (2026-08-06) removed the old
    // multi-subsystem chooser's active-modules fetch/filter entirely since
    // there was no chooser left to filter -- but `hasAI` in _renderShell()
    // was deliberately left reading `this.activeModules`, so it silently
    // evaluated to false with nothing ever populating it. Restored here as
    // its own standalone fetch (no chooser dependency): backend route is
    // commercial_runtime/identity/auth_routes.py's /api/auth/active-modules,
    // which defaults to ['all'] whenever AURA_APP_DATA/config.json doesn't
    // explicitly restrict `modules` -- i.e. every install shows the AI
    // button unless it was deliberately license-restricted. Never blocks
    // the rest of init(): a failed/slow fetch just leaves the button hidden,
    // same as any other network hiccup.
    try {
      const mods = await fetch('/api/auth/active-modules', { cache: 'no-store' }).then(r => r.json());
      this.activeModules = (mods && mods.modules) || [];
    } catch (e) {
      this.activeModules = [];
    }

  // ── Auth gate ─────────────────────────────────────────────────────────────
  // Before rendering anything, check if the user is authenticated.
  // On first launch (no admin exists) → show account setup.
  // On returning launch (admin exists, no session) → show login form.
  // Skip entirely in demo mode — the demo session is managed by /api/demo/start.
    if (!window.isDemoMode && sessionStorage.getItem('demo_mode') !== 'true') {
      try {
        const sess = await fetch('/api/auth/session', { credentials: 'include', cache: 'no-store' })
          .then(r => r.json()).catch(() => ({}));
        if (!sess.authenticated) {
          // needsSetup was already checked above (and would have returned
          // this init() call early) -- an admin account is guaranteed to
          // exist by this point, so this is always a returning-user login,
          // never account creation.
          this.showReloginModal('Sign in to your store');
          return; // Modal's success handler will call SubsystemApp.init() again
        }
        // Authenticated: apply the account's saved UI language before rendering,
        // so the whole app renders in the user's language from the first paint.
        if (sess.language && window.AuraI18n && AuraI18n.current !== sess.language) {
          AuraI18n.current = sess.language;
          localStorage.setItem('aura_lang', sess.language);
          AuraI18n.apply();
        }
        // Remember who is logged in (role + clinic_role) so the UI can gate
        // role-specific sections (e.g. clinic doctor vs secretary).
        this.currentUser = sess.user || {};
        this.role        = (sess.user && sess.user.role) || '';
        this.clinicRole  = (sess.user && sess.user.clinic_role) || '';
        // Rendering advice for hasCapability() above. The resolution rule
        // lives in _adoptSessionCapabilities() rather than inline here --
        // read that method's comment before touching this line; the version
        // that WAS inline here looked correct and silently disabled every
        // capability gate in the product.
        this._adoptSessionCapabilities(sess);
        // feat/reorder-automation-foundation: resolved once, here, BEFORE
        // _renderShell ever builds the nav list -- mirrors the desktopOnly
        // gate's own mechanism (a plain boolean flag on `this`, read by the
        // nav filter in _renderShell), except desktopOnly is derived
        // synchronously from navigator.userAgent while this needs one
        // network round-trip first. Any failure (network error, non-200,
        // missing field) leaves isAdminDevice at its fail-closed default
        // (false) set above -- never assumed true.
        try {
          const dev = await fetch('/api/devices/me', { credentials: 'include', cache: 'no-store' })
            .then(r => r.ok ? r.json() : { success: false });
          this.isAdminDevice = !!(dev && dev.success && dev.device && dev.device.is_admin_device === true);
          // 2026-08-20: the other half of the admin-device fix. Until now
          // NOTHING could set is_admin_device outside of an auto-promotion
          // hidden inside the backend's authorization check, so removing
          // that (it was granting admin to whichever device asked first --
          // see retail_api.py's _is_admin_device) would have left this flag
          // permanently false and the Settings/Audit Log nav entries
          // permanently hidden on every install. The server now tells us
          // whether a claim is available (admin role + nobody has claimed
          // yet); _maybeOfferAdminDeviceClaim() below turns that into the
          // one visible action that gets a fresh install its admin device.
          this.canClaimAdminDevice = !!(dev && dev.success && dev.can_claim_admin === true);
        } catch (e) {
          this.isAdminDevice = false;
          this.canClaimAdminDevice = false;
        }

        // ── Device activation gate ───────────────────────────────────────
        // Moved here (2026-08-17) from before the auth gate: "log in, THEN
        // enter your key the first time" for an existing account with no
        // key yet, rather than a pre-login key screen every returning user
        // used to see too. A brand-new install never reaches this at all --
        // needsSetup above sends it to showSetupModal(), which collects the
        // key as part of registration itself.
        // /api/licensing/status's NOT_CONFIGURED shape is ambiguous by
        // itself (returned both when genuinely unconfigured AND when
        // configured but never activated -- see commercial_runtime/
        // licensing_contracts's own test_status_before_activation_is_not_
        // configured_shape, which pins the configured-but-fresh case on
        // purpose), but the "detail" field is only ever attached by the
        // genuinely-unconfigured branch (routes.py::_not_configured_
        // response) -- present_status(None) never sets it, so its presence
        // is the real disambiguating signal, no backend change needed.
        // The three-way test itself now lives in _needsActivation() -- it was
        // copy-pasted here, in showSetupModal(), and (divergently) in
        // licensing.js. See that method for what the divergence actually cost.
        if (await this._enforceActivationGate()) return;
      } catch(e) {
        // Can't reach server — proceed and let individual API calls handle 401s
      }
    }

    // Whatever institute/co-op/foundation is running this install, read
    // once here (best-effort, same fail-open shape as activeModules above)
    // so document.title and the sidebar wordmark below can use it from the
    // very first render. `this.branding` defaults to {} on any failure --
    // every read of it below falls back to the current product name, never
    // to a blank string.
    await this._loadBranding();

    // Single-product build: there is only ever one system, so skip the
    // multi-subsystem chooser entirely and launch straight into it.
    this.launch('retail', 'dashboard');
    this._maybeOfferAdminDeviceClaim();
  },

  // See the comment on the call site above. Reads the same
  // GET /settings/branding route subsystem-retail.js's receipt printer
  // reads -- one company-scoped source for "what does this shop call
  // itself", never a second copy of the business name kept only in this file.
  async _loadBranding() {
    try {
      const resp = await fetch('/api/sub/retail/settings/branding', { credentials: 'include', cache: 'no-store' })
        .then(r => r.json());
      this.branding = (resp && resp.status === 'success' && resp.data) ? resp.data : {};
    } catch (e) {
      this.branding = {};
    }
    document.title = (this.branding && this.branding.branding_business_name) || 'Aura Retail';
  },

  // ── Admin-device claim prompt ───────────────────────────────────────────────
  // Shown only when the server says a claim is genuinely available: an admin
  // is logged in AND no device has claimed the role yet for this company.
  // Deliberately NOT shown to a non-admin, and never shown once some device
  // holds the flag -- a button that can only ever return 409 is worse than
  // no button. The server re-derives every one of those conditions on the
  // POST (and the `idx_devices_one_admin` partial unique index has the final
  // say), so this prompt is convenience, never the enforcement.
  //
  // Persistent element on document.body, not inside #subsystem-shell, for
  // the same reason as the sync banner below: _renderShell() replaces that
  // element's entire innerHTML on every launch().
  _maybeOfferAdminDeviceClaim() {
    document.getElementById('aura-admin-device-claim')?.remove();
    if (this.isAdminDevice || !this.canClaimAdminDevice) return;
    if (sessionStorage.getItem('admin_device_claim_dismissed') === 'true') return;

    const bar = document.createElement('div');
    bar.id = 'aura-admin-device-claim';
    bar.style.cssText = 'position:fixed;left:50%;transform:translateX(-50%);bottom:var(--overlay-inset-block-end,24px);z-index:99998;display:flex;align-items:center;gap:14px;flex-wrap:wrap;max-width:min(680px,92vw);padding:14px 18px;border-radius:14px;background:#1e1e2e;border:1px solid rgba(244,63,94,.45);box-shadow:0 10px 30px rgba(0,0,0,.45);color:#e8e8f0;font-size:13px;line-height:1.5;';
    // textContent (not innerHTML) for the message, and every button built as
    // a real element: nothing here interpolates a server-supplied string
    // into markup.
    const msg = document.createElement('span');
    // flex BASIS, not a bare flex:1. At 390px the two buttons take their
    // intrinsic width first and left the message about 60px, which wrapped
    // it to ONE WORD PER LINE and grew this banner to roughly half the
    // phone screen, burying the till underneath it. With a 220px basis and
    // flex-wrap on the container, the buttons drop to their own row instead
    // and the sentence reads normally.
    msg.style.cssText = 'flex:1 1 220px;min-inline-size:0;';
    msg.textContent = 'This device is not yet your store\'s admin device. Settings and the Audit Log stay hidden until one device is chosen.';
    const claim = document.createElement('button');
    claim.className = 'btn btn-primary';
    claim.style.cssText = 'white-space:nowrap;padding:8px 16px;border-radius:9px;border:none;background:#f43f5e;color:#fff;font-size:13px;font-weight:600;cursor:pointer;';
    claim.textContent = 'Make this the admin device';
    const later = document.createElement('button');
    later.style.cssText = 'background:none;border:none;color:#9aa0b4;font-size:13px;cursor:pointer;padding:8px;';
    later.textContent = 'Not now';

    claim.addEventListener('click', () => this._claimAdminDevice(claim));
    later.addEventListener('click', () => {
      // Session-scoped, not localStorage: "not now" should mean this
      // sitting, not "never ask again on this machine" -- an install left
      // permanently without an admin device is the failure state this whole
      // prompt exists to get out of.
      sessionStorage.setItem('admin_device_claim_dismissed', 'true');
      bar.remove();
    });

    bar.append(msg, claim, later);
    document.body.appendChild(bar);
    if (window.AuraI18n) AuraI18n.apply();   // translate before first paint settles
  },

  async _claimAdminDevice(button) {
    button.disabled = true;
    const original = button.textContent;
    button.textContent = 'Working…';
    let body = null, ok = false;
    try {
      const res = await fetch('/api/devices/me/claim-admin', {
        method: 'POST', credentials: 'include', cache: 'no-store',
      });
      body = await res.json().catch(() => null);
      ok = res.ok;
    } catch (e) {
      // Network failure -- leave the prompt in place so it can be retried.
    }
    if (!ok) {
      button.disabled = false;
      button.textContent = original;
      // ADMIN_DEVICE_ALREADY_CLAIMED means another device won in the
      // meantime; say which one, since the whole point of the server
      // naming the holder is that the user knows where to go.
      const holder = body && body.admin_device && (body.admin_device.device_label || body.admin_device.platform);
      // AuraI18n.t() explicitly here, not the DOM sweep: the sweep only
      // translates a text node whose FULL trimmed text matches a dictionary
      // key (see i18n.js), so a sentence with a device name concatenated
      // onto it would silently stay English in Arabic. Translating the fixed
      // half and appending the (untranslatable) device name is the only
      // shape that actually localizes.
      const t = (s) => (window.AuraI18n ? AuraI18n.t(s) : s);
      this.showToast(
        holder ? t('Another device is already the admin device:') + ' ' + holder
               : t('Could not make this the admin device.'),
        'error'
      );
      if (body && body.code === 'ADMIN_DEVICE_ALREADY_CLAIMED') {
        this.canClaimAdminDevice = false;
        document.getElementById('aura-admin-device-claim')?.remove();
      }
      return;
    }
    this.isAdminDevice = true;
    this.canClaimAdminDevice = false;
    document.getElementById('aura-admin-device-claim')?.remove();
    this.showToast('This device is now your store\'s admin device.', 'success');
    // Re-render so the adminOnly nav entries (Settings, Audit Log) appear
    // immediately -- _renderShell()'s nav filter reads this.isAdminDevice,
    // so without this the user would have to restart the app to see the
    // thing they just enabled.
    this.launch(this.active || 'retail', this.currentSection || 'dashboard');
  },

  // ── Device-activation gate ──────────────────────────────────────────────────
  // Returns true when the caller must stop immediately: the browser is
  // navigating away to the licensing page.
  //
  // Extracted from init() so it is not init()'s alone. The gate used to live
  // ONLY inside init(), and _reloginSubmit() re-enters through _navigate()
  // instead of init() whenever `this.active` is already set -- so a session
  // that reached the shell without passing the gate (the fetch below fails
  // open on a transient error, deliberately) and then expired could be signed
  // back into for the rest of its life without the gate ever running again.
  // That is the mirror image of the re-ask bug this whole area is about: the
  // key asked ZERO times rather than once.
  async _enforceActivationGate() {
    if (window.isDemoMode || sessionStorage.getItem('demo_mode') === 'true') return false;
    try {
      const lic = await fetch('/api/licensing/status', { cache: 'no-store' }).then(r => r.json());
      if (this._needsActivation(lic)) {
        location.href = '/static/licensing.html?gate=1';
        return true;
      }
      // Reached only when the server says this device does NOT need activating,
      // which makes any surviving marker provably spent -- the submission it
      // stood for has been decided. Retiring it here (the same thing
      // licensing.js's render() does on its own side) is what stops a
      // long-dead marker from suppressing showSetupModal()'s key field later.
      this._clearActivationPending();
    } catch (e) {
      // Network hiccup: fail open, same as every other best-effort check in
      // the init() sequence this was lifted out of.
    }
    return false;
  },

  launch(systemId, sectionId) {
    if (!this.systems[systemId]) {
      // Dynamic fallback for newly registered EIP modules
      this.systems[systemId] = {
        name: systemId.charAt(0).toUpperCase() + systemId.slice(1) + ' System',
        icon: '⚙️', accent: '#38bdf8', accentRgb: '56,189,248',
        nav: [{ id: 'dashboard', label: 'Dashboard', icon: '🏠' }]
      };
    }
    const sys = this.systems[systemId];
    if (!sys) return;
    this.active = systemId;
    this.currentSection = sectionId || 'dashboard';

    // Persist to URL hash
    if (window.AuraRouter) {
      const mode = window.isDemoMode ? 'demo' : 'real';
      window.AuraRouter.save(mode, systemId, this.currentSection);
    }

    // Set accent color CSS variable
    this._applyAccent(sys);

    // Show subsystem page, hide others
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const page = document.getElementById('page-subsystem');
    if (page) page.classList.add('active');

    this._renderShell(sys, systemId);
    this._navigate(this.currentSection);
    LogoSystem.init();
    this._startSyncHealthPoll();
    this._startLicenseCheckInPoll();
  },

  exit() {
    // Single-product build: "exit" (clicking the logo) just returns to the
    // retail dashboard — there is no other subsystem to choose between.
    this.launch('retail', 'dashboard');
  },

  // Device license activation/status page (licensing.html + licensing.js,
  // wired to /api/licensing/* since Phase 7 Part G) was never linked from
  // this shell -- no nav entry, no settings section, no restricted-license
  // banner anywhere reached it, so once OWNER_LICENSING_BASE_URL is
  // configured there was no discoverable way for an installer/admin to
  // activate or reactivate a device short of being told the raw URL
  // out-of-band. Plain same-window navigation (not window.open/new tab):
  // this shell also runs inside the pywebview desktop window and the
  // Edge --app fallback (products/retail/desktop/launcher_retail.py),
  // neither of which reliably opens a second window/tab the way a normal
  // browser does, so in-place navigation is the one approach that behaves
  // the same across native window, Edge --app, and default-browser launch.
  // licensing.html carries its own "Back to Aura Retail" link (see that
  // file) to return here, mirroring the Android app's onOpenLicensing /
  // onBack round trip (android/aura-retail/.../ui/AppRoot.kt).
  openLicensing() {
    location.href = '/static/licensing.html';
  },

  async logout() {
    // Stopped BEFORE the logout fetch, not after: the global auth guard
    // intercepts any /api/ 401 and pops the relogin modal, so a sync-health
    // poll racing this logout would trigger a spurious relogin modal.
    this._stopSyncHealthPoll();
    try {
      await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
    } catch(e) {}
    this.active = null;
    document.getElementById('aura-relogin-modal')?.remove();
    this.showReloginModal('You have been logged out. Please sign in again.');
  },

  // ── Called on 401 from any API: shows setup on first launch, login otherwise ─
  async checkAuthAndSetup(errorMsg) {
    if (window.isDemoMode || sessionStorage.getItem('demo_mode') === 'true') return;
    if (this._authModalOpen) return;
    this._authModalOpen = true;
    try {
      const status = await fetch('/api/onboarding/status', { cache: 'no-store' })
        .then(r => r.json()).catch(() => ({ needs_setup: false }));
      if (status.needs_setup) {
        // Same decision as init()'s boot path -- setup modal, or the
        // "Connecting to your shop…" wait for a device that has already
        // joined -- made in ONE place so the two callers cannot disagree.
        await this._openFirstRun();
      } else {
        this.showReloginModal(errorMsg || 'Your session has expired. Please log in again.');
      }
    } catch(e) {
      this.showReloginModal(errorMsg || 'Your session has expired. Please log in again.');
    }
  },

  // ── FIRST-TIME SETUP MODAL ────────────────────────────────────────────────
  // Visual layer lives in main.css (.auth-*) — this used to be a wall of
  // inline styles with a hardcoded teal that matched nothing in the product.
  // The accent vars are applied here the same way launch() applies them, so
  // the very first screen a customer sees already carries the product brand.
  async showSetupModal() {
    document.getElementById('aura-relogin-modal')?.remove();
    this._authModalOpen = true;
    // Always render into the ordinary (non-join) layout. _toggleJoinMode()
    // below flips this to true in place without a re-render; the one path
    // back to the setup form re-renders from here, which is what makes
    // resetting it here sufficient to undo everything the toggle changed.
    this._joinMode = false;
    this._applyAccent(this.systems.retail);

    // AUDIT-fix 2026-08-17: registration now collects the license key
    // itself instead of the old separate pre-login "enter your key" screen
    // -- see init()'s own comment for the full before/after. Only shown
    // when this install actually has licensing configured (OWNER_LICENSING_
    // BASE_URL set) -- an install with no Owner wired up stays fully
    // unlocked per CLAUDE.md's "invisible unless opted in" rule, so forcing
    // a key field there would be actively wrong, not just unnecessary.
    let needsKey = false;
    try {
      const lic = await fetch('/api/licensing/status', { cache: 'no-store' }).then(r => r.json());
      // Same shared predicate the boot gate in init() uses -- this was the
      // second of the three copies. Asking for a key here when the gate
      // wouldn't have asked (or vice versa) is exactly how the two screens
      // used to disagree about whether a device still needed activating.
      needsKey = this._needsActivation(lic);
    } catch (e) { /* fail open: no key field, same as the old pre-login gate's own fail-open */ }
    // A key already submitted and held by Owner for approval persists NO local
    // state record, so _needsActivation() above stays true for as long as the
    // approval is outstanding -- and this modal would render the key field a
    // SECOND time for a user who has already given us the key. That is the
    // exact re-ask the pending marker exists to prevent, and here it is worse
    // than elsewhere: the field is mandatory (see _setupSubmit's
    // `this._setupNeedsKey && !key` guard) and this overlay has no cancel and
    // no close, so a user with a genuinely pending key and no key to hand
    // would be stuck against a form they cannot satisfy or leave.
    if (needsKey && this._isActivationPending()) needsKey = false;
    this._setupNeedsKey = needsKey;

    const overlay = document.createElement('div');
    overlay.id = 'aura-relogin-modal';
    overlay.className = 'auth-overlay';
    overlay.innerHTML = `
      <div class="auth-card">
        <button onclick="AuraI18n.toggle()" title="Language / اللغة" class="auth-lang-btn">EN | ع</button>
        <div class="auth-head">
          <div class="auth-icon">${AuraIcons.render('zap', 32)}</div>
          <h2 class="auth-title" id="su-title">${t('Welcome to Action Aura')}</h2>
          <p class="auth-sub" id="su-sub">${t('Create your administrator account to get started.')}</p>
          <p class="auth-note" data-role="setup-only">This setup runs <strong>only once</strong>. Your credentials will be saved permanently.</p>
          ${needsKey ? `<p class="auth-foot" style="margin:4px 0 0"><a href="#" id="su-join-link" onclick="SubsystemApp._toggleJoinMode(event)">${t('Already have a shop? Join it with your licence key')}</a></p>` : ''}
        </div>
        <div class="auth-grid-2" data-role="setup-only">
          <div class="auth-field">
            <label for="su-name">Full Name *</label>
            <input id="su-name" type="text" placeholder="Your full name" autocomplete="name"
              onkeydown="if(event.key==='Enter')document.getElementById('su-company').focus()" />
          </div>
          <div class="auth-field">
            <label for="su-company">Company Name</label>
            <input id="su-company" type="text" placeholder="Your company" autocomplete="organization"
              onkeydown="if(event.key==='Enter')document.getElementById('su-email').focus()" />
          </div>
        </div>
        <div class="auth-field" data-role="setup-only">
          <label for="su-email">Email Address *</label>
          <input id="su-email" type="email" placeholder="admin@yourcompany.com" autocomplete="email"
            onkeydown="if(event.key==='Enter')document.getElementById('su-pass').focus()" />
        </div>
        ${needsKey ? `
        <div class="auth-field">
          <label for="su-key">License Key *</label>
          <input id="su-key" type="text" placeholder="AURA-RETAIL-XXXX-YYYY-ZZZZ" autocomplete="off"
            style="text-transform:uppercase" onkeydown="if(event.key==='Enter')document.getElementById('su-pass').focus()" />
          <p class="hint" id="su-key-hint" style="margin:4px 0 0;font-size:12px;color:var(--text-muted)">${t('From your Aura order confirmation. Activated together with your account below.')}</p>
        </div>` : ''}
        <div class="auth-grid-2" data-role="setup-only">
          <div class="auth-field">
            <label for="su-pass">Password *</label>
            <input id="su-pass" type="password" placeholder="Min. 6 characters" autocomplete="new-password"
              onkeydown="if(event.key==='Enter')document.getElementById('su-pass2').focus()" />
          </div>
          <div class="auth-field">
            <label for="su-pass2">Confirm Password *</label>
            <input id="su-pass2" type="password" placeholder="Repeat password" autocomplete="new-password"
              onkeydown="if(event.key==='Enter')SubsystemApp._setupSubmit()" />
          </div>
        </div>
        <div id="su-error" class="auth-error"></div>
        <button id="su-btn" class="auth-submit" onclick="SubsystemApp._setupSubmit()">Create Account &amp; Launch</button>
        <p class="auth-foot">Your data is stored locally on this device. No cloud required.</p>
      </div>`;
    document.body.appendChild(overlay);
    setTimeout(() => document.getElementById('su-name')?.focus(), 150);
  },

  async _setupSubmit() {
    const name    = document.getElementById('su-name')?.value.trim();
    const company = document.getElementById('su-company')?.value.trim() || 'My Company';
    const email   = document.getElementById('su-email')?.value.trim().toLowerCase();
    const key     = document.getElementById('su-key')?.value.trim().toUpperCase();
    const pass    = document.getElementById('su-pass')?.value;
    const pass2   = document.getElementById('su-pass2')?.value;
    const errEl   = document.getElementById('su-error');
    const btn     = document.getElementById('su-btn');

    const showErr = (msg) => { if(errEl){errEl.textContent=msg;errEl.style.display='block';} if(btn){btn.textContent='Create Account & Launch';btn.disabled=false;} };

    if (!name)                   return showErr('Full name is required.');
    if (!email || !email.includes('@')) return showErr('A valid email address is required.');
    if (this._setupNeedsKey && !key) return showErr('A license key is required.');
    if (!pass)                   return showErr('Password is required.');
    if (pass.length < 6)         return showErr('Password must be at least 6 characters.');
    if (pass !== pass2)          return showErr('Passwords do not match.');

    if (btn) { btn.textContent = 'Creating account…'; btn.disabled = true; }
    if (errEl) errEl.style.display = 'none';

    try {
      const res  = await fetch('/api/onboarding/create-admin', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, email, password: pass, company_name: company }),
      });
      const data = await res.json();

      if (!data.success) {
        // 409 = "An admin account already exists. Please login." This modal
        // has no cancel, no close and no login link, so showing that error in
        // place is a dead end the user can only escape by reloading the page
        // by hand -- and it is reachable without anything exotic: any DB-level
        // exception inside /api/onboarding/status hits its bare `except` and
        // fail-safes to needs_setup:true, which sends a fully-registered user
        // straight back here via checkAuthAndSetup(). The server is telling us
        // exactly which screen this user actually needs; hand them to it.
        if (res.status === 409) {
          this._authModalOpen = false;
          return this.showReloginModal(data.error || 'An account already exists on this installation. Please sign in.');
        }
        return showErr(data.error || 'Could not create account. Please try again.');
      }

      // Account exists and this session is already authenticated (create-
      // admin calls create_session() itself) from this point on -- a key
      // failure below must NEVER send the user back through registration.
      if (this._setupNeedsKey && key) {
        if (btn) btn.textContent = 'Activating license…';
        let activation = null;
        try {
          const actRes = await fetch('/api/licensing/activate', {
            method: 'POST', credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ license_key: key }),
          });
          activation = await actRes.json();
          activation._status = actRes.status;
        } catch (e) {
          activation = { result: 'NETWORK_ERROR' };
        }
        if (activation.result === 'SUCCESS') {
          // Fully activated -- retire any marker left over from an earlier
          // PENDING submission on this device.
          this._clearActivationPending();
        } else if (activation.result === 'PENDING') {
          // 202: Owner is holding this key for manual approval and persists
          // NO local state record, so /api/licensing/status will keep
          // answering NOT_CONFIGURED and the boot gate would keep sending the
          // user to licensing.html. Remember the submission so nothing asks
          // for the very same key again -- that re-ask loop was the whole bug
          // -- and finish the wait HERE, in this document, which is the only
          // scope that still holds the key needed to resolve it.
          this._markActivationPending(activation.installation_id);
          this._pendingActivationKey = key;
          await fetch('/api/onboarding/complete', { method: 'POST', credentials: 'include' }).catch(() => {});
          return this._setupAwaitingApproval(name, activation.installation_id);
        } else {
          // Account created, key not activated -- ask for a valid one in
          // place, never re-show the registration form (the account already
          // exists). _activationFailureMessage() is what keeps this honest:
          // a thrown fetch or a 503 is NOT a verdict on the key.
          return this._setupKeyRetry(name, this._activationFailureMessage(activation));
        }
      }

      // Mark onboarding complete
      await fetch('/api/onboarding/complete', { method: 'POST', credentials: 'include' }).catch(() => {});

      // Close modal and relaunch the full UI
      document.getElementById('aura-relogin-modal')?.remove();
      this._authModalOpen = false;

      // Show a brief welcome message overlay (on body, not inside #app so we preserve the #page-subsystem/#subsystem-shell containers)
      const overlay = document.createElement('div');
      overlay.id = 'aura-setup-complete-overlay';
      overlay.style.cssText = 'position:fixed;inset:0;background:#020617;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#fff;font-family:Inter,sans-serif;z-index:99999;';
      overlay.innerHTML = `
        <div style="font-size:56px;margin-bottom:16px;color:var(--sub-accent,#14b8a6)">${AuraIcons.render('circle-check-big', 56, { animate: 'pop' })}</div>
        <h2 style="font-size:28px;font-weight:800;margin:0 0 8px;">Account Created!</h2>
        <p style="color:#64748b;margin:0;font-size:16px;">Welcome, <strong id="aura-setup-complete-name" style="color:var(--sub-accent,#14b8a6)"></strong>. Loading your platform…</p>`;
      const nameEl = overlay.querySelector('#aura-setup-complete-name');
      if (nameEl) nameEl.textContent = name;
      document.body.appendChild(overlay);

      // Init the platform and remove the overlay once rendering is complete
      setTimeout(async () => {
        try {
          await SubsystemApp.init();
        } finally {
          document.getElementById('aura-setup-complete-overlay')?.remove();
        }
      }, 1400);

    } catch(e) {
      showErr('Network error. Make sure the server is running.');
    }
  },

  // ── First run: set up a new shop, or wait for a joined shop's account ──────
  // The ONE place that decides what a needs_setup device sees. Both callers
  // -- init() at boot and checkAuthAndSetup() on a 401 -- go through it.
  //
  // The first cut of the join door wired this branch into checkAuthAndSetup()
  // only, and its test called that function directly, so it passed. On a real
  // launch init() runs FIRST, read needs_setup itself and opened the setup
  // modal straight away -- so a till that had just joined a shop was offered
  // "Create your administrator account" a second time, the exact leftover
  // the door exists to remove. Measured in Chromium on a fresh till
  // (scripts/ops/join_door_e2e.py, after-restart phase, 2026-09-06); pinned
  // by retail_join_shop_modal_test.js driving init() itself.
  //
  // A device that already activated via /api/licensing/activate with no
  // admin session yet is ACTIVE and needs_setup at the same time: its
  // owner's account simply has not synced down yet, which is the
  // join-existing-shop-design.md waiting state, not a fresh install that
  // still needs a key. _isJoinedDevice() tells the two apart from the status
  // payload's Owner-issued installation_id, minus the pending-approval state.
  async _openFirstRun() {
    try {
      const lic = await fetch('/api/licensing/status', { cache: 'no-store' }).then(r => r.json());
      if (this._isJoinedDevice(lic)) {
        return this._waitForShopAccount();
      }
    } catch (e) {
      // Fail open, same as every other best-effort licensing check in this
      // file: fall through to the ordinary setup modal below.
    }
    this.showSetupModal();
  },

  // ── JOIN AN EXISTING SHOP (second device / phone) ──────────────────────────
  // docs/launch-readiness/join-existing-shop-design.md. A device joining a
  // shop that already exists on another till needs nothing this modal
  // otherwise collects -- no name, no email, no password -- because there is
  // no new admin to create: POST /api/licensing/activate takes no session,
  // the identity rebind it triggers seeds company_settings for a device that
  // activates with no admin, and the owner's real account then arrives by
  // sync. The only input this flow needs is the key already in the form.
  //
  // Reached from the "Already have a shop?" link showSetupModal() renders
  // above (only when this build even carries a key field -- an install with
  // no Owner wired up has no shop to join), and toggled back the same way.
  _joinMode: false,

  // Turning join mode ON hides the setup-only fields IN PLACE -- same
  // overlay, same DOM nodes, so anything already typed survives a mind
  // change -- via the `data-role="setup-only"` wrappers showSetupModal()'s
  // template carries on both `.auth-grid-2` blocks and the email field.
  // Turning it back OFF re-renders the whole modal from scratch through
  // showSetupModal() instead of hand-restoring every title/button/link string
  // this mutates below -- simpler, and showSetupModal() resets `_joinMode`
  // itself, re-fetches /api/licensing/status (idempotent) and is the exact
  // function this overlay was built by in the first place.
  _toggleJoinMode(ev) {
    if (ev && ev.preventDefault) ev.preventDefault();
    const overlay = document.getElementById('aura-relogin-modal');
    if (!overlay) return;
    if (this._joinMode) {
      return this.showSetupModal();
    }
    this._joinMode = true;
    overlay.querySelectorAll('[data-role="setup-only"]').forEach(el => { el.style.display = 'none'; });
    const titleEl = document.getElementById('su-title');
    const subEl   = document.getElementById('su-sub');
    const btnEl   = document.getElementById('su-btn');
    const linkEl  = document.getElementById('su-join-link');
    const hintEl  = document.getElementById('su-key-hint');
    if (titleEl) titleEl.textContent = t('Join your shop');
    if (subEl)   subEl.textContent = t("Enter the licence key your shop already uses. Your account will arrive from your shop's other device.");
    // The setup layout's key hint ends "activated together with your account
    // below" -- there is no account form below in join mode (seen in the
    // Chromium run's screenshot, 2026-09-06), so say what the key IS instead.
    if (hintEl)  hintEl.textContent = t("The same key your shop's other device was activated with. It is in your Aura order confirmation.");
    if (btnEl) {
      btnEl.textContent = t('Join shop');
      btnEl.setAttribute('onclick', 'SubsystemApp._joinSubmit()');
    }
    if (linkEl) linkEl.textContent = t('Set up a new shop instead');
  },

  async _joinSubmit() {
    const key   = document.getElementById('su-key')?.value.trim().toUpperCase();
    const errEl = document.getElementById('su-error');
    const btn   = document.getElementById('su-btn');
    const showErr = (msg) => { if(errEl){errEl.textContent=msg;errEl.style.display='block';} if(btn){btn.textContent=t('Join shop');btn.disabled=false;} };

    if (!key) return showErr('A license key is required.');

    if (btn) { btn.textContent = t('Joining…'); btn.disabled = true; }
    if (errEl) errEl.style.display = 'none';

    // Activation only -- deliberately no /api/onboarding/create-admin call
    // anywhere in this function. There is no admin to create on this device;
    // the one this join is FOR already exists on the shop's other till and
    // arrives by sync once this device is trusted.
    let activation = null;
    try {
      const res = await fetch('/api/licensing/activate', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ license_key: key }),
      });
      activation = await res.json();
      activation._status = res.status;
    } catch (e) {
      activation = { result: 'NETWORK_ERROR' };
    }

    if (activation.result !== 'SUCCESS') {
      // Same reason-code mapping _setupSubmit's own activation branch uses --
      // one table, not a second copy that can drift from it.
      return showErr(this._activationFailureMessage(activation));
    }

    // Activated -- but the relay address this device just learned only takes
    // effect at the NEXT launch (read at import, see the design doc), so the
    // honest instruction is to restart, not to carry on in this process
    // pretending the new wiring is already live.
    const overlay = document.getElementById('aura-relogin-modal');
    if (overlay) {
      overlay.querySelector('.auth-card').innerHTML = `
        <div class="auth-head">
          <div class="auth-icon">${AuraIcons.render('circle-check-big', 32, { animate: 'pop' })}</div>
          <h2 class="auth-title">${t('Connected to your shop')}</h2>
          <p class="auth-sub">${t("Restart Aura to finish joining. Your account is on its way from your shop's other device.")}</p>
        </div>
        <button id="su-join-continue-btn" class="auth-submit" onclick="SubsystemApp._waitForShopAccount()">${t('I restarted — continue')}</button>`;
    }
  },

  // ── Waiting for the owner's account to arrive by sync ───────────────────────
  // Reached two ways: right after _joinSubmit() above ("I restarted --
  // continue"), and by checkAuthAndSetup() below on the VERY NEXT launch, for
  // a device that is already ACTIVE but still has no admin session -- the
  // owner's account just hasn't synced down yet, which is not the same thing
  // as a fresh install that still needs a key.
  //
  // `intervalMs`/`ceilingMs` default to the design's real 3s/120s and are
  // parameters ONLY so a test can drive this with a short real ceiling --
  // this frontend has no test framework and no fake-timer shim (see this
  // file's other standalone Node tests), so a short REAL wait is the only
  // way to exercise the timeout branch without a 120-second test. Production
  // call sites never pass either argument.
  _waitApprovalTimer: null,
  _waitApprovalElapsedMs: 0,

  _waitForShopAccount(intervalMs, ceilingMs) {
    const interval = intervalMs || 3000;
    const ceiling  = ceilingMs  || 120000;
    // Two very different entry states share this function: _joinSubmit()
    // above calls it into an overlay that already exists (its own "Connected"
    // screen), while checkAuthAndSetup() below calls it at boot, before ANY
    // modal has been created -- there is nothing on screen yet for an ACTIVE,
    // no-admin-yet device's very first render. Build the overlay ourselves
    // when it is missing, the same way showSetupModal()/showReloginModal() do.
    let overlay = document.getElementById('aura-relogin-modal');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'aura-relogin-modal';
      overlay.className = 'auth-overlay';
      overlay.innerHTML = '<div class="auth-card"></div>';
      document.body.appendChild(overlay);
    }
    this._authModalOpen = true;
    this._applyAccent(this.systems.retail);
    this._waitApprovalElapsedMs = 0;
    overlay.querySelector('.auth-card').innerHTML = `
      <div class="auth-head">
        <div class="auth-icon">${AuraIcons.render('key-round', 32)}</div>
        <h2 class="auth-title">${t('Connecting to your shop…')}</h2>
        <p class="auth-sub">${t('This takes a few seconds once the app has restarted.')}</p>
      </div>`;
    this._stopWaitForShopAccountPoll();
    this._waitApprovalTimer = setInterval(() => this._pollShopAccount(interval, ceiling), interval);
  },

  _stopWaitForShopAccountPoll() {
    if (this._waitApprovalTimer) {
      clearInterval(this._waitApprovalTimer);
      this._waitApprovalTimer = null;
    }
  },

  async _pollShopAccount(interval, ceiling) {
    this._waitApprovalElapsedMs += interval;
    let status = null;
    try {
      status = await fetch('/api/onboarding/status', { cache: 'no-store' }).then(r => r.json());
    } catch (e) {
      // Transient -- keep waiting silently, same fail-open convention every
      // other best-effort poll in this file follows.
    }
    if (status && status.needs_setup === false) {
      this._stopWaitForShopAccountPoll();
      document.getElementById('aura-relogin-modal')?.remove();
      this._authModalOpen = false;
      this.showReloginModal(t('Your shop is connected. Sign in with your account.'));
      return;
    }
    if (this._waitApprovalElapsedMs >= ceiling) {
      this._stopWaitForShopAccountPoll();
      const overlay = document.getElementById('aura-relogin-modal');
      const card = overlay && overlay.querySelector('.auth-card');
      if (card) {
        card.innerHTML = `
          <div class="auth-head">
            <div class="auth-icon">${AuraIcons.render('key-round', 32)}</div>
            <h2 class="auth-title">${t('Still waiting for your account. Check the connection, or set up a new shop instead.')}</h2>
          </div>
          <button id="su-wait-retry-btn" class="auth-submit" onclick="SubsystemApp._waitForShopAccount(${interval}, ${ceiling})">${t('Keep waiting')}</button>
          <button id="su-wait-setup-btn" class="auth-submit" style="margin-top:8px" onclick="SubsystemApp.showSetupModal()">${t('Set up a new shop instead')}</button>`;
      }
    }
  },

  // ── Activation failure copy ───────────────────────────────────────────────
  // "That license key was not accepted." used to be said for EVERY non-success
  // outcome, including `{ result: 'NETWORK_ERROR' }` -- the object the catch
  // block above fabricates when fetch itself threw and the request never
  // completed. So a customer with a perfectly good key, registering while the
  // local backend was still binding its port (or after a laptop sleep, or
  // through an antivirus that blocked the loopback call), was told their key
  // was bad and sent hunting for another one, inside an overlay with no back
  // and no cancel. A key is only "not accepted" when Owner actually said so.
  ACTIVATION_TRANSIENT_REASONS: {
    NETWORK_UNAVAILABLE: 1,
    REQUEST_TIMED_OUT: 1,
    TLS_VERIFICATION_FAILED: 1,
    SERVICE_TEMPORARILY_UNAVAILABLE: 1,
    SIGNING_KEY_UNAVAILABLE: 1,
    RATE_LIMITED: 1,
    MALFORMED_RESPONSE: 1,
    DEVICE_KEY_UNAVAILABLE: 1,
  },

  // Second bucket, same principle one step further along: these are produced
  // entirely on THIS device, after Owner already answered SUCCESS -- our own
  // verify_assertion() refusing the signed licence Owner sent. Mirrors
  // LOCAL_VERIFICATION_REASON_CODES in licensing.js (separate document,
  // separate script scope, nothing to import -- keep the two in step).
  //
  // The transport gives the frontend no other way to tell them from an Owner
  // verdict: routes.py::activate() flattens every ActivationFailed into the
  // same 400 {reason_code, detail}. Real, already-seen case: Owner rotates
  // its signing key while this install still ships a stale trust_anchor.json
  // (a condition this project has hit on the live droplet) -> Owner APPROVES,
  // the installation goes ACTIVE and burns a paid device slot, and this
  // device answers UNKNOWN_SIGNING_KEY. Without this bucket that reached the
  // `a.detail` fallback below and told the customer their key was rejected,
  // in Owner's raw internal wording ("Assertion signed by an untrusted
  // key: ..."), with a fresh key the only offered escape.
  ACTIVATION_LOCAL_VERIFICATION_REASONS: {
    UNSIGNED_RESPONSE_REJECTED: 1,
    UNKNOWN_SIGNING_KEY: 1,
    ASSERTION_VERIFICATION_FAILED: 1,
    ASSERTION_EXPIRED: 1,
    ASSERTION_NOT_YET_VALID: 1,
    ASSERTION_PRODUCT_MISMATCH: 1,
    ASSERTION_PLATFORM_MISMATCH: 1,
    ASSERTION_INSTALLATION_MISMATCH: 1,
    ASSERTION_DEVICE_MISMATCH: 1,
    ASSERTION_FORBIDDEN_FIELD: 1,
    CLOCK_ROLLBACK_SUSPECTED: 1,
    LOCAL_STATE_CORRUPT: 1,
  },

  // The only members of the set above a customer can resolve without support,
  // and so the only ones "check the date and time" is true advice for. Mirrors
  // CLOCK_FIXABLE_REASON_CODES in licensing.js and LicensingMessages.kt.
  ACTIVATION_CLOCK_FIXABLE_REASONS: {
    ASSERTION_EXPIRED: 1,
    ASSERTION_NOT_YET_VALID: 1,
    CLOCK_ROLLBACK_SUSPECTED: 1,
  },

  _activationFailureMessage(activation) {
    const a = activation || {};
    if (a.result === 'NETWORK_ERROR') {
      return 'Could not reach the licensing service, so this key has not been checked yet. '
        + 'Your account is saved — check the connection and try again.';
    }
    if (this.ACTIVATION_TRANSIENT_REASONS[a.reason_code]) {
      return 'The licensing service is temporarily unavailable, so this key has not been '
        + 'checked yet. Your account is saved — please try again shortly.';
    }
    // Deliberately ahead of the `a.detail` fallback, and deliberately says
    // nothing about the key: Owner said yes, so a new key cannot help and
    // asking for one is actively harmful advice.
    //
    // Clock advice is given ONLY for the three codes a clock can actually
    // cause. It used to be given for all twelve, and on a real handset
    // stranded at UNKNOWN_SIGNING_KEY that sent an investigation off to
    // compare clocks which already matched to the identical second, while the
    // true cause was a trust store seeded once and never refreshed. Everything
    // else gets the reason code to quote to support -- the fastest route to
    // the cause. Keep in step with licensing.js and LicensingMessages.kt.
    if (this.ACTIVATION_LOCAL_VERIFICATION_REASONS[a.reason_code]) {
      const opening = 'Action Aura approved this activation, but this computer could not verify the signed '
        + 'licence it received, so it has not been applied yet. Your license key is not the problem — ';
      if (this.ACTIVATION_CLOCK_FIXABLE_REASONS[a.reason_code]) {
        return opening
          + 'do not replace it. Check that this computer’s date and time are correct; if they are, contact support.';
      }
      return opening + 'do not replace it, and re-entering it cannot help. Please contact support and quote '
        + 'this code: ' + (a.reason_code || 'UNKNOWN') + '.';
    }
    // A real verdict from Owner. `detail` is Owner-supplied text; every caller
    // renders it through _esc().
    return a.detail || 'That license key was not accepted.';
  },

  // ── Awaiting-approval screen (registration flow) ──────────────────────────
  // Owner answered 202 PENDING: the key is good enough to be queued for a
  // human to approve, and holding it is neither success nor rejection.
  //
  // This screen lives HERE, in the registration document, rather than letting
  // init()'s boot gate bounce the user to licensing.html, because this scope
  // is the only one that still holds the key -- and the key is the only thing
  // that can resolve a held activation. POST /api/licensing/check-in cannot:
  // it short-circuits to a canned ACTIVATION_REQUIRED for any device with no
  // persisted state record (routes.py), and a PENDING device is exactly that,
  // because activation.py raises ActivationPending before state_repository.
  // save() is ever reached. Only re-POSTing /api/licensing/activate resolves
  // it. Bouncing to licensing.html would drop the key on the floor and that
  // page would have to ask for it a second time -- the very loop being fixed.
  _setupApprovalTimer: null,
  _setupApprovalName: '',

  _setupAwaitingApproval(name, installationId) {
    const overlay = document.getElementById('aura-relogin-modal');
    if (!overlay) return;
    // Remembered for the rejection path below, which falls back to
    // _setupKeyRetry() and needs the same name that screen was built with.
    this._setupApprovalName = name || '';
    overlay.querySelector('.auth-card').innerHTML = `
      <div class="auth-head">
        <div class="auth-icon">${AuraIcons.render('key-round', 32)}</div>
        <h2 class="auth-title">${t('Waiting for approval')}</h2>
        <p class="auth-sub">${t('Your account is ready and your license key was received. Action Aura needs to approve this activation before the app opens — you do not need to enter the key again.')}</p>
      </div>
      <p id="su-await-status" class="auth-foot">${t('Checking automatically every 30 seconds…')}</p>
      ${installationId ? `<p class="auth-foot">${t('Installation')}: ${this._esc(installationId)}</p>` : ''}
      <div id="su-await-error" class="auth-error"></div>
      <button id="su-await-btn" class="auth-submit" onclick="SubsystemApp._pollSetupApproval(true)">${t('Check now')}</button>`;
    // One timer only. Cleared by _stopSetupApprovalPoll() on every exit path,
    // so a re-entry (Check now -> resolved -> another screen) can never leave
    // an orphan interval hammering /activate in the background.
    this._stopSetupApprovalPoll();
    this._setupApprovalTimer = setInterval(() => this._pollSetupApproval(false), 30000);
  },

  _stopSetupApprovalPoll() {
    if (this._setupApprovalTimer) {
      clearInterval(this._setupApprovalTimer);
      this._setupApprovalTimer = null;
    }
  },

  async _pollSetupApproval(fromButton) {
    const key = this._pendingActivationKey;
    if (!key) {
      // Should be unreachable -- _setupAwaitingApproval is only ever reached
      // one line after the key is stored. If it happens anyway, the one
      // unacceptable outcome is carrying on claiming to check with nothing to
      // check with, so hand over to the licensing page's own honest screen.
      this._stopSetupApprovalPoll();
      location.href = '/static/licensing.html?gate=1';
      return;
    }
    const statusEl = document.getElementById('su-await-status');
    const errEl    = document.getElementById('su-await-error');
    const showErr  = (msg) => { if (errEl) { errEl.textContent = msg; errEl.style.display = 'block'; } };

    let data = null;
    try {
      const res = await fetch('/api/licensing/activate', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ license_key: key }),
      });
      data = await res.json();
      data._status = res.status;
    } catch (e) {
      // Automatic ticks stay silent about transient failures: a network blip
      // every 30s must not paint this screen red, because nothing is wrong
      // with the pending activation itself.
      if (fromButton) showErr('Could not reach the licensing service. Will keep trying.');
      return;
    }
    if (errEl) errEl.style.display = 'none';

    if (data.result === 'PENDING') {
      if (statusEl) {
        statusEl.textContent = t('Still waiting for approval. Last checked at ')
          + new Date().toLocaleTimeString() + '.';
      }
      return;
    }

    if (data.result === 'SUCCESS') {
      this._stopSetupApprovalPoll();
      this._clearActivationPending();
      document.getElementById('aura-relogin-modal')?.remove();
      this._authModalOpen = false;
      await SubsystemApp.init();
      return;
    }

    if (this.ACTIVATION_TRANSIENT_REASONS[data.reason_code]) {
      if (fromButton) showErr(this._activationFailureMessage(data));
      return;
    }

    // NOT a verdict: Owner answered SUCCESS and this device failed to verify
    // that answer (see ACTIVATION_LOCAL_VERIFICATION_REASONS). The held
    // activation really is resolved -- APPROVED -- on Owner's side, so the one
    // thing that must not happen is what the rejection branch below does:
    // wiping the pending marker and dropping the customer onto a "enter a
    // valid license key" screen for a key Owner has already accepted. Stay
    // put, keep the marker and the key, keep the timer running -- unlike a
    // rejection this self-heals with no user action at all once the trust
    // anchor or the clock is fixed, and the next tick is what notices. Shown
    // on every tick, not just `fromButton`: it needs someone to act, and
    // saying nothing would leave the screen promising a wait that is over.
    if (this.ACTIVATION_LOCAL_VERIFICATION_REASONS[data.reason_code]) {
      showErr(this._activationFailureMessage(data));
      return;
    }

    // A verdict: Owner reviewed the held activation and declined it. Surfacing
    // a rejection at all is the point -- without this the screen would sit on
    // "waiting for approval" forever for an approval that is never coming.
    // The marker goes with it, or the next screen would still believe a live
    // submission exists. Also genuinely reachable for a REJECTION only as of
    // owner activation.py's DEACTIVATED/REPLACED guard -- before that, Owner
    // re-activated the installation its own staff had just rejected, so this
    // 30s poll would have flipped the screen to "approved" instead.
    this._stopSetupApprovalPoll();
    this._clearActivationPending();
    this._setupKeyRetry(this._setupApprovalName, this._activationFailureMessage(data));
  },

  // The account from _setupSubmit() above is already created and this
  // session is already authenticated -- this is key-entry only, reusing
  // the same overlay/.auth-card in place (same pattern as _showForgotPasswordScreen).
  _setupKeyRetry(name, reason) {
    const overlay = document.getElementById('aura-relogin-modal');
    if (!overlay) return;
    overlay.querySelector('.auth-card').innerHTML = `
      <div class="auth-head">
        <div class="auth-icon">${AuraIcons.render('key-round', 32)}</div>
        <h2 class="auth-title">${t('Almost there')}</h2>
        <p class="auth-sub">${t('Your account was created. Enter a valid license key to finish.')}</p>
      </div>
      <div class="auth-field">
        <label for="su-key-2">${t('License Key')}</label>
        <input id="su-key-2" type="text" placeholder="AURA-RETAIL-XXXX-YYYY-ZZZZ" autocomplete="off"
          style="text-transform:uppercase" onkeydown="if(event.key==='Enter')SubsystemApp._setupKeyRetrySubmit()" />
      </div>
      <div id="su-key-2-error" class="auth-error" style="display:block">${this._esc(reason)}</div>
      <button id="su-key-2-btn" class="auth-submit" onclick="SubsystemApp._setupKeyRetrySubmit()">${t('Activate')}</button>`;
    setTimeout(() => document.getElementById('su-key-2')?.focus(), 100);
  },

  async _setupKeyRetrySubmit() {
    const key   = document.getElementById('su-key-2')?.value.trim().toUpperCase();
    const errEl = document.getElementById('su-key-2-error');
    const btn   = document.getElementById('su-key-2-btn');
    const showErr = (msg) => { if(errEl){errEl.textContent=msg;errEl.style.display='block';} if(btn){btn.textContent=t('Activate');btn.disabled=false;} };
    if (!key) return showErr('A license key is required.');
    if (btn) { btn.textContent = 'Activating…'; btn.disabled = true; }
    try {
      const res = await fetch('/api/licensing/activate', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ license_key: key }),
      });
      const data = await res.json();
      // Same 202-PENDING contract as _setupSubmit() above: PENDING is neither
      // success nor rejection, and it leaves no local state record behind, so
      // the marker is the only thing that stops anything else asking for this
      // exact key a second time. SUCCESS retires it.
      if (data.result === 'SUCCESS') {
        this._clearActivationPending();
      } else if (data.result === 'PENDING') {
        this._markActivationPending(data.installation_id);
        this._pendingActivationKey = key;
        await fetch('/api/onboarding/complete', { method: 'POST', credentials: 'include' }).catch(() => {});
        return this._setupAwaitingApproval('', data.installation_id);
      } else {
        return showErr(this._activationFailureMessage(data));
      }
      await fetch('/api/onboarding/complete', { method: 'POST', credentials: 'include' }).catch(() => {});
      document.getElementById('aura-relogin-modal')?.remove();
      this._authModalOpen = false;
      await SubsystemApp.init();
    } catch (e) {
      showErr('Network error. Make sure the server is running.');
    }
  },

  // ── RETURNING USER LOGIN MODAL ────────────────────────────────────────────
  // Same auth-* component family as the setup modal (main.css) — one visual
  // language for both halves of the auth flow, branded with the product
  // accent instead of the old off-brand teal.
  showReloginModal(msg = 'Your session has expired. Please log in again.') {
    document.getElementById('aura-relogin-modal')?.remove();
    this._authModalOpen = true;
    this._applyAccent(this.systems.retail);
    const overlay = document.createElement('div');
    overlay.id = 'aura-relogin-modal';
    overlay.className = 'auth-overlay';
    overlay.innerHTML = `
      <div class="auth-card auth-card-compact">
        <button onclick="AuraI18n.toggle()" title="Language / اللغة" class="auth-lang-btn">EN | ع</button>
        <div class="auth-head">
          <div class="auth-icon">${AuraIcons.render('lock', 32)}</div>
          <h2 class="auth-title">${t('Welcome to Aura Retail')}</h2>
          <p class="auth-sub">${t(msg)}</p>
        </div>
        <div class="auth-field">
          <label for="rl-email">${t('Email')}</label>
          <input id="rl-email" type="email" placeholder="admin@yourcompany.com" autocomplete="email"
            onkeydown="if(event.key==='Enter')document.getElementById('rl-pass').focus()" />
        </div>
        <div class="auth-field" style="margin-bottom:20px;">
          <label for="rl-pass">${t('Password')}</label>
          <input id="rl-pass" type="password" placeholder="••••••••" autocomplete="current-password"
            onkeydown="if(event.key==='Enter')SubsystemApp._reloginSubmit()" />
        </div>
        <div id="rl-error" class="auth-error"></div>
        <button id="rl-btn" class="auth-submit" onclick="SubsystemApp._reloginSubmit()">${t('Log In')}</button>
        <p class="auth-foot"><a href="#" onclick="SubsystemApp._showForgotPasswordScreen();return false;">${t('Forgot password?')}</a></p>
      </div>`;
    document.body.appendChild(overlay);
    setTimeout(() => document.getElementById('rl-email')?.focus(), 100);
  },

  async _reloginSubmit() {
    const email = document.getElementById('rl-email')?.value.trim();
    const pass  = document.getElementById('rl-pass')?.value;
    const errEl = document.getElementById('rl-error');
    const btn   = document.getElementById('rl-btn');
    if (!email || !pass) {
      if(errEl){errEl.textContent='Email and password are required.';errEl.style.display='block';}
      return;
    }
    if (btn) { btn.textContent='Signing in…'; btn.disabled=true; }
    if (errEl) errEl.style.display='none';
    try {
      const res  = await fetch('/api/auth/login', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, username: email, password: pass }),
      });
      const data = await res.json();
      if (data.success || data.user) {
        document.getElementById('aura-relogin-modal')?.remove();
        this._authModalOpen = false;
        this._authPrompted = false;   // re-arm the 401 guard for future expiries
        if (this.active) {
          // Already inside a subsystem — reload current section. The gate has
          // to be re-run explicitly first: this branch deliberately skips
          // init(), which is where the gate used to live and ONLY live, so a
          // session that reached the shell without ever passing it (the gate
          // fails open on a transient error, by design) could be signed back
          // into for the rest of its life without ever being asked for a key.
          // That is the same bug from the opposite side -- asked zero times
          // rather than once.
          if (await this._enforceActivationGate()) return;
          this._navigate(this.currentSection || 'dashboard');
        } else {
          // At the menu level — re-init the full platform
          SubsystemApp.init();
        }
      } else {
        if (errEl) { errEl.textContent = data.error || 'Invalid email or password.'; errEl.style.display='block'; }
        if (btn) { btn.textContent='Log In'; btn.disabled=false; }
      }
    } catch(e) {
      if (errEl) { errEl.textContent='Network error. Check the server is running.'; errEl.style.display='block'; }
      if (btn) { btn.textContent='Log In'; btn.disabled=false; }
    }
  },

  // ── FORGOT / RESET PASSWORD ────────────────────────────────────────────────
  // Reuses the relogin modal's own overlay element (swaps its innerHTML)
  // rather than creating a second overlay -- there is only ever one of
  // these on screen at a time, same assumption showReloginModal/
  // showSetupModal already make with their shared #aura-relogin-modal id.
  _showForgotPasswordScreen() {
    const overlay = document.getElementById('aura-relogin-modal');
    if (!overlay) return;
    overlay.querySelector('.auth-card').innerHTML = `
      <div class="auth-head">
        <div class="auth-icon">${AuraIcons.render('mail', 32)}</div>
        <h2 class="auth-title">${t('Reset your password')}</h2>
        <p class="auth-sub">${t("Enter your account email and we'll send a reset link.")}</p>
      </div>
      <div class="auth-field">
        <label for="fp-email">${t('Email')}</label>
        <input id="fp-email" type="email" placeholder="admin@yourcompany.com" autocomplete="email"
          onkeydown="if(event.key==='Enter')SubsystemApp._forgotPasswordSubmit()" />
      </div>
      <div id="fp-error" class="auth-error"></div>
      <button id="fp-btn" class="auth-submit" onclick="SubsystemApp._forgotPasswordSubmit()">${t('Send reset link')}</button>
      <p class="auth-foot"><a href="#" onclick="SubsystemApp.showReloginModal();return false;">${t('Back to sign in')}</a></p>`;
    setTimeout(() => document.getElementById('fp-email')?.focus(), 100);
  },

  async _forgotPasswordSubmit() {
    const email = document.getElementById('fp-email')?.value.trim();
    const errEl = document.getElementById('fp-error');
    const btn   = document.getElementById('fp-btn');
    if (!email || !email.includes('@')) {
      if (errEl) { errEl.textContent = 'A valid email address is required.'; errEl.style.display = 'block'; }
      return;
    }
    if (btn) { btn.textContent = 'Sending…'; btn.disabled = true; }
    if (errEl) errEl.style.display = 'none';
    try {
      await fetch('/api/auth/forgot-password', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
    } catch (e) { /* deliberately silent -- see the confirmation message below */ }

    // Identical message regardless of what actually happened (unknown
    // email, real email, network hiccup) -- the backend's own
    // enumeration-safety guarantee (verification.request_password_reset)
    // is worthless if this screen leaks the answer some other way.
    const overlay = document.getElementById('aura-relogin-modal');
    if (overlay) {
      overlay.querySelector('.auth-card').innerHTML = `
        <div class="auth-head">
          <div class="auth-icon">${AuraIcons.render('circle-check-big', 32, { animate: 'pop' })}</div>
          <h2 class="auth-title">${t('Check your email')}</h2>
          <p class="auth-sub">${t('If an account exists for that email, a reset link is on its way.')}</p>
        </div>
        <p class="auth-foot"><a href="#" onclick="SubsystemApp.showReloginModal();return false;">${t('Back to sign in')}</a></p>`;
    }
  },

  // ── VERIFY EMAIL LANDING SCREEN (from #verify-email/<token>) ──────────────
  _showVerifyEmailScreen(token) {
    this._applyAccent(this.systems.retail);
    const overlay = document.createElement('div');
    overlay.id = 'aura-verify-email-screen';
    overlay.className = 'auth-overlay';
    overlay.innerHTML = `
      <div class="auth-card auth-card-compact">
        <div class="auth-head">
          <div class="auth-icon">${AuraIcons.render('mail', 32)}</div>
          <h2 class="auth-title" id="ve-title">${t('Verifying your email…')}</h2>
          <p class="auth-sub" id="ve-sub"></p>
        </div>
        <button id="ve-continue" class="auth-submit" style="display:none"
          onclick="location.hash='';SubsystemApp.init();">${t('Continue')}</button>
      </div>`;
    document.body.appendChild(overlay);

    fetch('/api/auth/verify-email', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    }).then(r => r.json()).then(data => {
      const titleEl = document.getElementById('ve-title');
      const subEl = document.getElementById('ve-sub');
      const btn = document.getElementById('ve-continue');
      if (data.success) {
        if (titleEl) titleEl.textContent = t('Email verified');
        if (subEl) subEl.textContent = t('Your email address has been confirmed.');
      } else {
        if (titleEl) titleEl.textContent = t('Link invalid or expired');
        if (subEl) subEl.textContent = data.error || t('Please request a new verification email and try again.');
      }
      if (btn) btn.style.display = 'block';
    }).catch(() => {
      const titleEl = document.getElementById('ve-title');
      const subEl = document.getElementById('ve-sub');
      const btn = document.getElementById('ve-continue');
      if (titleEl) titleEl.textContent = t('Network error');
      if (subEl) subEl.textContent = t('Make sure the server is running and try again.');
      if (btn) btn.style.display = 'block';
    });
  },

  // ── RESET PASSWORD LANDING SCREEN (from #reset-password/<token>) ──────────
  _showResetPasswordScreen(token) {
    this._applyAccent(this.systems.retail);
    const overlay = document.createElement('div');
    overlay.id = 'aura-reset-password-screen';
    overlay.className = 'auth-overlay';
    overlay.innerHTML = `
      <div class="auth-card auth-card-compact">
        <div class="auth-head">
          <div class="auth-icon">${AuraIcons.render('key-round', 32)}</div>
          <h2 class="auth-title">${t('Set a new password')}</h2>
        </div>
        <div class="auth-field">
          <label for="rp-pass">${t('New password')}</label>
          <input id="rp-pass" type="password" placeholder="Min. 6 characters" autocomplete="new-password"
            onkeydown="if(event.key==='Enter')document.getElementById('rp-pass2').focus()" />
        </div>
        <div class="auth-field" style="margin-bottom:20px;">
          <label for="rp-pass2">${t('Confirm new password')}</label>
          <input id="rp-pass2" type="password" placeholder="Repeat password" autocomplete="new-password"
            onkeydown="if(event.key==='Enter')SubsystemApp._resetPasswordSubmit('${token}')" />
        </div>
        <div id="rp-error" class="auth-error"></div>
        <button id="rp-btn" class="auth-submit" onclick="SubsystemApp._resetPasswordSubmit('${token}')">${t('Set password')}</button>
      </div>`;
    document.body.appendChild(overlay);
    setTimeout(() => document.getElementById('rp-pass')?.focus(), 100);
  },

  async _resetPasswordSubmit(token) {
    const pass  = document.getElementById('rp-pass')?.value;
    const pass2 = document.getElementById('rp-pass2')?.value;
    const errEl = document.getElementById('rp-error');
    const btn   = document.getElementById('rp-btn');
    const showErr = (msg) => { if (errEl) { errEl.textContent = msg; errEl.style.display = 'block'; } if (btn) { btn.textContent = 'Set password'; btn.disabled = false; } };
    if (!pass || pass.length < 6) return showErr('Password must be at least 6 characters.');
    if (pass !== pass2) return showErr('Passwords do not match.');

    if (btn) { btn.textContent = 'Saving…'; btn.disabled = true; }
    if (errEl) errEl.style.display = 'none';
    try {
      const res = await fetch('/api/auth/reset-password', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, password: pass }),
      });
      const data = await res.json();
      if (!data.success) return showErr(data.error || 'This reset link is invalid or has expired.');

      const overlay = document.getElementById('aura-reset-password-screen');
      if (overlay) {
        overlay.querySelector('.auth-card').innerHTML = `
          <div class="auth-head">
            <div class="auth-icon">${AuraIcons.render('circle-check-big', 32, { animate: 'pop' })}</div>
            <h2 class="auth-title">${t('Password updated')}</h2>
            <p class="auth-sub">${t('You can now sign in with your new password.')}</p>
          </div>
          <button class="auth-submit" onclick="location.hash='';SubsystemApp.init();">${t('Continue to sign in')}</button>`;
      }
    } catch (e) {
      showErr('Network error. Make sure the server is running.');
    }
  },

  // ── EMPLOYEE SETUP LANDING SCREEN (from #setup/<token>) ────────────────────
  // Measured 2026-09-06: the Employees screen's "+ Add Employee" dialog issues
  // an invite link of the form http://<host>/#setup/<token> ("This link works
  // once and expires in 7 days"), but init() only ever handled
  // #verify-email/<token> and #reset-password/<token> -- opening a setup link
  // in a fresh browser fell through to the ordinary sign-in modal, and a
  // brand-new employee with no password had nowhere to set one. The backend
  // route (POST /api/auth/employee/setup, commercial_runtime/identity/
  // onboarding_routes.py) already existed -- its own comment called the
  // invite link "currently frontend-unwired". This pair mirrors
  // _showResetPasswordScreen/_resetPasswordSubmit exactly, on purpose: same
  // shape, so the two one-time-link screens cannot silently drift apart.
  _showEmployeeSetupScreen(token) {
    this._applyAccent(this.systems.retail);
    const overlay = document.createElement('div');
    overlay.id = 'aura-employee-setup-screen';
    overlay.className = 'auth-overlay';
    overlay.innerHTML = `
      <div class="auth-card auth-card-compact">
        <div class="auth-head">
          <div class="auth-icon">${AuraIcons.render('key-round', 32)}</div>
          <h2 class="auth-title">${t('Set your password')}</h2>
          <p class="auth-sub">${t('Choose the password you will sign in with. Your email is already on file.')}</p>
        </div>
        <div class="auth-field">
          <label for="es-pass">${t('Password')}</label>
          <input id="es-pass" type="password" placeholder="Min. 6 characters" autocomplete="new-password"
            onkeydown="if(event.key==='Enter')document.getElementById('es-pass2').focus()" />
        </div>
        <div class="auth-field" style="margin-bottom:20px;">
          <label for="es-pass2">${t('Confirm password')}</label>
          <input id="es-pass2" type="password" placeholder="Repeat password" autocomplete="new-password"
            onkeydown="if(event.key==='Enter')SubsystemApp._employeeSetupSubmit('${token}')" />
        </div>
        <div id="es-error" class="auth-error"></div>
        <button id="es-btn" class="auth-submit" onclick="SubsystemApp._employeeSetupSubmit('${token}')">${t('Set password')}</button>
      </div>`;
    document.body.appendChild(overlay);
    setTimeout(() => document.getElementById('es-pass')?.focus(), 100);
  },

  async _employeeSetupSubmit(token) {
    const pass  = document.getElementById('es-pass')?.value;
    const pass2 = document.getElementById('es-pass2')?.value;
    const errEl = document.getElementById('es-error');
    const btn   = document.getElementById('es-btn');
    const showErr = (msg) => { if (errEl) { errEl.textContent = msg; errEl.style.display = 'block'; } if (btn) { btn.textContent = 'Set password'; btn.disabled = false; } };
    if (!pass || pass.length < 6) return showErr('Password must be at least 6 characters.');
    if (pass !== pass2) return showErr('Passwords do not match.');

    if (btn) { btn.textContent = t('Saving…'); btn.disabled = true; }
    if (errEl) errEl.style.display = 'none';
    try {
      const res = await fetch('/api/auth/employee/setup', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, password: pass }),
      });
      const data = await res.json();
      if (!data.success) return showErr(data.error || t('This setup link is invalid or has expired. Ask your admin for a new one.'));

      const overlay = document.getElementById('aura-employee-setup-screen');
      if (overlay) {
        overlay.querySelector('.auth-card').innerHTML = `
          <div class="auth-head">
            <div class="auth-icon">${AuraIcons.render('circle-check-big', 32, { animate: 'pop' })}</div>
            <h2 class="auth-title">${t('Your password is set')}</h2>
            <p class="auth-sub">${t('Sign in with your email and this password, on this device or the phone.')}</p>
          </div>
          <button class="auth-submit" onclick="location.hash='';SubsystemApp.init();">${t('Continue to sign in')}</button>`;
      }
    } catch (e) {
      showErr('Network error. Make sure the server is running.');
    }
  },

  // The 5 bottom-tab-bar destinations (docs/design/phone-ui-redesign.md §2):
  // Home/Stock/Till/Customers map 1:1 to these ids; the 5th slot is the More
  // sheet, active whenever the current section is none of the four. Shared
  // between _renderShell (builds the bar) and _navigate (keeps its active
  // state in sync) so the two lists cannot silently drift apart -- the same
  // reason the ungated-nav-entry list in the spec's §2 rationale is quoted
  // once rather than copied.
  _TAB_BAR_SECTIONS: ['dashboard', 'products', 'pos', 'customers'],

  _renderShell(sys, systemId) {
    const shell = document.getElementById('subsystem-shell');
    if (!shell) return;

    const hasAI = this.activeModules && (this.activeModules.includes('all') || this.activeModules.includes('ai_agent'));

    // ── Phone tab bar (≤640px, css/main.css) — docs/design/phone-ui-redesign.md
    // §2/§7 Step 1. Renders unconditionally (like the sidebar it replaces at
    // that width); the media query is what actually hides it ≥641px, not a
    // JS branch, so there is nothing here for a resize to get out of sync
    // with. Stable across every role on purpose -- see the spec's "why these
    // five" -- so no _isNavItemVisible() gating applies to the four mapped
    // slots the way it does for the sidebar/More-sheet items.
    const activeSection = this.currentSection || 'dashboard';
    const tabIcon = (val) => window.AuraIcons ? AuraIcons.render(val, 22) : val;
    const tabHTML = (id, emoji, label, extraClass) => `
            <a class="sub-tab${extraClass ? ' ' + extraClass : ''}${id === activeSection ? ' active' : ''}"
               data-tab="${id}"
               onclick="SubsystemApp._navigate('${id}')">
              <span class="sub-tab-icon">${tabIcon(emoji)}</span>
              <span class="sub-tab-label">${t(label)}</span>
            </a>
          `;
    const tabBarHTML = `
      <nav class="sub-tabbar" id="sub-tabbar">
        ${tabHTML('dashboard', '🏠', 'Home')}
        ${tabHTML('products', '📦', 'Stock')}
        ${tabHTML('pos', '🛒', 'Till', 'sub-tab-till')}
        ${tabHTML('customers', '👥', 'Customers')}
        <a class="sub-tab${!this._TAB_BAR_SECTIONS.includes(activeSection) ? ' active' : ''}"
           id="sub-tab-more"
           onclick="SubsystemApp._openMoreSheet()">
          <span class="sub-tab-icon">${tabIcon('☰')}</span>
          <span class="sub-tab-label">${t('More')}</span>
        </a>
      </nav>
    `;

    // ── Sidebar nav: Dashboard ungrouped, then sys.navGroups ────────────────
    // See the long comment on systems.retail.navGroups for why this stays a
    // separate render-time arrangement over the untouched flat `nav` array
    // rather than a nested nav structure.
    const navItemHTML = (item) => `
            <a class="sub-nav-item ${item.id === 'dashboard' ? 'active' : ''}"
               data-section="${item.id}"
               onclick="SubsystemApp._navigate('${item.id}')">
              <span class="sub-nav-icon">${window.AuraIcons ? AuraIcons.render(item.icon, 17) : item.icon}</span>
              <span class="sub-nav-label">${t(item.label)}</span>
            </a>
          `;
    const byId = new Map(sys.nav.map((item) => [item.id, item]));
    const dashboardItem = byId.get('dashboard');
    const dashboardHTML = (dashboardItem && this._isNavItemVisible(dashboardItem)) ? navItemHTML(dashboardItem) : '';
    const groupsHTML = (sys.navGroups || []).map((group) => {
      const visibleItems = group.items
        .map((id) => byId.get(id))
        .filter((item) => item && this._isNavItemVisible(item));
      // THE EMPTY-GROUP RULE: no visible members means no header and no box
      // at all, not an empty section. See the mutation proof on this line in
      // retail_nav_groups_test.js -- rendering the header unconditionally
      // here is exactly the bug that test exists to catch.
      if (!visibleItems.length) return '';
      return `
          <div class="sub-nav-group-label">${t(group.label)}</div>
          ${visibleItems.map(navItemHTML).join('')}`;
    }).join('');

    shell.innerHTML = `
      <!-- Subsystem Sidebar -->
      <aside class="sub-sidebar" id="sub-sidebar">
        <div class="sub-sidebar-brand aura-logo" title="Return to Home">
          <div class="sub-brand-icon">${window.AuraIcons ? AuraIcons.render(sys.icon, 22) : sys.icon}</div>
          <div class="sub-brand-text">
            <!-- Configured business name (whatever institute/co-op/foundation
                 bought this install) when one is set, falling back to the
                 current product name (sys.name) exactly as it always has --
                 this.branding is loaded once in init() (_loadBranding) and
                 is operator-entered text, so it is escaped like every other
                 shop-typed string this file interpolates (see _esc's own
                 comment). -->
            <span class="sub-system-name">${(this.branding && this.branding.branding_business_name) ? this._esc(this.branding.branding_business_name) : t(sys.name)}</span>
            <span class="logo-name" style="font-size:11px;color:var(--text-muted)">Action<strong>Aura</strong></span>
          </div>
        </div>

        <nav class="sub-nav" id="sub-nav">
          ${dashboardHTML}${groupsHTML}
        </nav>

        <div class="sub-sidebar-bottom">
          ${hasAI ? `
          <button class="sub-ai-btn" onclick="SubAI.open('${systemId}')">
            <span>🤖</span> <span>${t('AI Assistant')}</span>
            <span class="ai-pulse"></span>
          </button>
          ` : ''}
          <button class="sub-exit-btn" onclick="SubsystemApp.openLicensing()" title="Device license activation and status">
            <span>🔑</span> <span>${t('License')}</span>
          </button>
          <button class="sub-exit-btn" onclick="SubsystemApp.logout()" style="background:var(--state-danger-surface);border-color:var(--state-danger-border);color:var(--state-danger-text);margin-top:4px;">
            <span>⏻</span> <span>${t('Log Out')}</span>
          </button>
        </div>
      </aside>

      <!-- Subsystem Main -->
      <div class="sub-main">
        <header class="sub-header">
          <div class="sub-header-left">
            <h2 class="sub-header-title" id="sub-header-title">${t(sys.name)}</h2>
            <span class="sub-header-section" id="sub-header-section">${t('Dashboard')}</span>
          </div>
          <div class="sub-header-right">
            <button class="sub-header-btn" id="aura-lang-toggle" onclick="AuraI18n.toggle()" title="Language / اللغة" style="font-size:13px;font-weight:700;">${window.AuraI18n && AuraI18n.current === 'ar' ? 'EN' : 'ع'}</button>
            <!-- Theme control. ThemeEngine is the light/dark switch now (the
                 old accent-palette picker it replaced is documented on the
                 engine itself); dark v2 is token-value-only, so this can no
                 longer produce the historical white-on-white state -- see
                 index.html's boot comment for that postmortem. -->
            <button class="sub-header-btn" onclick="ThemeEngine.openPicker()" title="Change UI theme" style="font-size:15px;">🎨</button>
            <!-- Rides the semantic accent tokens, not sys.accent (#f43f5e, a
                 rose the token layer retired): the literal was ~3.3:1 on the
                 light header and would not flip with the theme at all. -->
            <div class="sub-header-badge" style="background:rgba(var(--sub-accent-rgb),0.15);border-color:var(--sub-accent);color:var(--accent-action)">
              ${window.AuraIcons ? AuraIcons.render(sys.icon, 14) : sys.icon} ${t(sys.name)}
            </div>
            ${hasAI ? `<button class="sub-header-btn" onclick="SubAI.open('${systemId}')" title="AI Assistant">🤖</button>` : ''}
          </div>
        </header>

        <main class="sub-content" id="sub-content">
          <div style="text-align:center;padding:80px;color:var(--text-muted)">Loading...</div>
        </main>
      </div>
      ${tabBarHTML}
    `;
  },

  _navigate(sectionId) {
    this.currentSection = sectionId;

    // ── Stop any active auto-refresh from the previous section ────────────────
    if (this._autoRefreshTimer) {
      clearInterval(this._autoRefreshTimer);
      this._autoRefreshTimer = null;
    }

    // Persist section to URL hash
    if (window.AuraRouter) {
      const mode = window.isDemoMode ? 'demo' : 'real';
      window.AuraRouter.save(mode, this.active, sectionId);
    }

    // Update nav active state
    document.querySelectorAll('.sub-nav-item').forEach(el => {
      el.classList.toggle('active', el.dataset.section === sectionId);
    });

    // Phone tab bar mirrors the same active-state toggle (§2 of
    // docs/design/phone-ui-redesign.md): the four mapped slots match by
    // data-tab, and More is active whenever the section is one of the 15 it
    // owns rather than one of the four -- so the bar always shows where you
    // are, even for a destination only the sheet lists.
    document.querySelectorAll('.sub-tab[data-tab]').forEach(el => {
      el.classList.toggle('active', el.dataset.tab === sectionId);
    });
    document.getElementById('sub-tab-more')?.classList.toggle('active', !this._TAB_BAR_SECTIONS.includes(sectionId));

    // The More sheet never persists across a navigation (§2: "Tapping any
    // row ... closes the sheet") -- every _navigate() call, whichever tab or
    // sheet row triggered it, closes it.
    this._closeMoreSheet();

    // Update header
    const sys = this.systems[this.active];
    const navItem = sys?.nav.find(n => n.id === sectionId);
    document.getElementById('sub-header-section')?.innerText &&
      (document.getElementById('sub-header-section').innerText = t(navItem?.label || sectionId));

    // ── Destroy any existing Chart.js instances before replacing the DOM ──────
    const content = document.getElementById('sub-content');
    if (content) {
      if (window.Chart) {
        content.querySelectorAll('canvas').forEach(canvas => {
          try {
            const existing = Chart.getChart ? Chart.getChart(canvas) : null;
            if (existing) existing.destroy();
          } catch(e) {}
        });
      }
      content.innerHTML = `<div class="sub-loading"><div class="sub-spinner"></div></div>`;
    }

    setTimeout(async () => {
      const _safeRender = async () => {
        const _RENDERERS = {
          retail: 'RetailSystem',
        };

        if (this.active === 'dashboard') {
          throw new Error(
            `The Dashboard module is not available in this installation. ` +
            `Please contact your administrator or re-download the system package.`
          );
        }

        const rendererName = _RENDERERS[this.active];
        if (rendererName) {
          const renderer = window[rendererName];
          if (typeof renderer === 'undefined' || !renderer) {
            throw new Error(
              `The ${this.systems[this.active]?.name || this.active} module is not available in this installation. ` +
              `Please contact your administrator or re-download the system package.`
            );
          }
          await renderer.render(sectionId);
        } else {
          throw new Error(
            `The ${this.systems[this.active]?.name || this.active} module is not available in this installation. ` +
            `Please contact your administrator or re-download the system package.`
          );
        }
      };

      try {
        await _safeRender();

        // ── Auto-refresh: re-render the dashboard every 60 s while it's open ──
        if (sectionId === 'dashboard') {
          this._autoRefreshTimer = setInterval(() => {
            if (this.currentSection === 'dashboard' && this.active) {
              this._navigate('dashboard');
            }
          }, 60000);
        }

        // ── Show the live-data badge to confirm real-time mode is active ───────

      } catch (err) {
        const c = document.getElementById('sub-content');
        if (c) c.innerHTML = `
          <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;padding:60px;text-align:center;">
            <div style="font-size:48px;margin-bottom:20px;">⚠️</div>
            <h3 style="color:var(--state-danger-text);margin-bottom:12px;font-size:20px;">Failed to load ${sectionId}</h3>
            <p style="color:var(--text-secondary);max-width:500px;line-height:1.6;font-size:14px;">${err.message || 'An unexpected error occurred.'}</p>
            <button onclick="SubsystemApp._navigate('${sectionId}')" style="margin-top:24px;padding:10px 24px;background:var(--accent-action);border:none;border-radius:8px;color:var(--text-on-accent);font-weight:600;cursor:pointer;font-size:14px;">↻ Retry</button>
          </div>`;
        console.error('[SubsystemApp] Error in ' + this.active + '/' + sectionId + ':', err);
      }
    }, 50);
  },

  // ── The More bottom sheet (docs/design/phone-ui-redesign.md §2, §7 Step 2) ──
  // Reuses sys.navGroups + _isNavItemVisible exactly as the sidebar does (see
  // _renderShell's own comment), so WHO sees WHAT stays governed by the one
  // source of truth -- this is a render-time arrangement over the untouched
  // flat `nav` array, not a second nav data model.
  //
  // Built on document.body, not inside #subsystem-shell, for the same
  // persistence reason _maybeOfferAdminDeviceClaim's bar is: _renderShell
  // replaces the shell's innerHTML wholesale on every launch()/language
  // toggle, which would otherwise destroy an open sheet out from under a tap.
  _openMoreSheet() {
    this._closeMoreSheet();
    const sys = this.systems[this.active];
    if (!sys) return;

    const hasAI = this.activeModules && (this.activeModules.includes('all') || this.activeModules.includes('ai_agent'));
    const icon = (val) => window.AuraIcons ? AuraIcons.render(val, 20) : val;
    const byId = new Map(sys.nav.map((item) => [item.id, item]));

    const rowHTML = (item) => `
            <a class="sub-more-row${item.id === this.currentSection ? ' active' : ''}"
               onclick="SubsystemApp._navigate('${item.id}')">
              <span class="sub-more-row-icon">${icon(item.icon)}</span>
              <span>${t(item.label)}</span>
            </a>
          `;
    const groupsHTML = (sys.navGroups || []).map((group) => {
      const visibleItems = group.items
        .map((id) => byId.get(id))
        .filter((item) => item && this._isNavItemVisible(item))
        // A destination that is already a BOTTOM TAB must not also appear in
        // this sheet. The sheet exists for "the other fifteen"; a row for Till
        // or Customers teaches two routes to the same place and makes the
        // sheet longer for nothing.
        //
        // Same rule, same reason, as the Android client: its drawer duplicated
        // Settings, and its bottom bar nearly re-introduced the duplication
        // when Customers was promoted to a tab while still sitting in More.
        .filter((item) => !this._TAB_BAR_SECTIONS.includes(item.id));
      // THE EMPTY-GROUP RULE, same as _renderShell's sidebar (and the same
      // mutation proof in retail_nav_groups_test.js): a group with nothing
      // visible in it renders no header, not a header over an empty box.
      if (!visibleItems.length) return '';
      return `
          <div class="sub-nav-group-label">${t(group.label)}</div>
          ${visibleItems.map(rowHTML).join('')}`;
    }).join('');

    // Footer: the AI/License/Log-Out trio that used to be three unlabeled
    // rail-bottom squares (§2's "why this answers the owner's complaints"),
    // plus Language and Theme, as full-width labeled rows. Every action here
    // closes the sheet itself first, rather than relying on _navigate()'s
    // close (none of these five call _navigate) -- AI/Theme open their own
    // overlay, License/Log Out navigate away, and Language rebuilds the
    // whole shell (AuraI18n.setLang -> SubsystemApp.launch), so closing
    // first keeps the sheet from being left open behind any of them.
    const footerRows = [
      hasAI ? `
            <button class="sub-more-row" onclick="SubsystemApp._closeMoreSheet();SubAI.open('${this.active}')">
              <span class="sub-more-row-icon">🤖</span>
              <span>${t('AI Assistant')}</span>
            </button>` : '',
      `
            <button class="sub-more-row" onclick="SubsystemApp._closeMoreSheet();AuraI18n.toggle()">
              <span class="sub-more-row-icon">🌐</span>
              <span>${t('Language')}</span>
              <span class="sub-more-row-value">${window.AuraI18n && AuraI18n.current === 'ar' ? 'EN' : 'ع'}</span>
            </button>`,
      `
            <button class="sub-more-row" onclick="SubsystemApp._closeMoreSheet();ThemeEngine.openPicker()">
              <span class="sub-more-row-icon">🎨</span>
              <span>${t('Theme')}</span>
            </button>`,
      `
            <button class="sub-more-row" onclick="SubsystemApp._closeMoreSheet();SubsystemApp.openLicensing()">
              <span class="sub-more-row-icon">🔑</span>
              <span>${t('License')}</span>
            </button>`,
      `
            <button class="sub-more-row danger" onclick="SubsystemApp._closeMoreSheet();SubsystemApp.logout()">
              <span class="sub-more-row-icon">⏻</span>
              <span>${t('Log Out')}</span>
            </button>`,
    ].join('');

    // Lets CSS stand the admin-device claim banner down while this sheet is
    // up. A sheet is a modal surface; a banner floating over it is purely in
    // the way, which the 390px screenshots showed plainly.
    document.body.classList.add('sub-more-sheet-open');

    const scrim = document.createElement('div');
    scrim.className = 'sub-more-scrim';
    scrim.id = 'sub-more-scrim';
    scrim.onclick = () => this._closeMoreSheet();

    const sheet = document.createElement('div');
    sheet.className = 'sub-more-sheet';
    sheet.id = 'sub-more-sheet';
    sheet.setAttribute('role', 'dialog');
    sheet.setAttribute('aria-modal', 'true');
    sheet.setAttribute('aria-label', t('More'));
    sheet.innerHTML = `
          <div class="sub-more-handle"></div>
          <div class="sub-more-body">
            ${groupsHTML}
            <div class="sub-more-footer">${footerRows}</div>
          </div>
        `;

    document.body.appendChild(scrim);
    document.body.appendChild(sheet);
  },

  _closeMoreSheet() {
    document.body.classList.remove('sub-more-sheet-open');
    document.getElementById('sub-more-scrim')?.remove();
    document.getElementById('sub-more-sheet')?.remove();
  },

  // The "● LIVE" badge that used to live here is DELETED.
  //
  // It was set true after a section rendered and false in the catch -- and the
  // catch immediately replaces the screen with a "Failed to load / Retry"
  // panel. So it was visible in every state a user could actually observe,
  // and therefore said nothing.
  //
  // It had one honest job once: retail_dashboard_error_propagation_test.js
  // records that _renderDashboard used to swallow its fetch error, so the
  // badge lit up over a dashboard frozen on placeholders. Fixing that (by
  // rethrowing, which that test pins) removed the only condition under which
  // the badge could disagree with the screen. The guarantee is tested
  // directly; the badge was residue.
  //
  // It also carried costs a dark theme and an Arabic layout would both have
  // had to pay: `margin-left` (physical), five hardcoded colour literals, an
  // infinite animation, and header room on a 390px phone.

  // ── Multi-device sync health indicator ──────────────────────────────────────
  // Persistent (NOT showToast -- a 3s auto-dismissing toast is the wrong
  // shape for a condition that can last hours, and the calm state below is
  // meant to stay visible indefinitely, not flash once). Appended to
  // document.body, never into #subsystem-shell, because _renderShell()
  // replaces that element's entire innerHTML on every launch().
  //
  // feat/sync-freshness-indicator: before this change, the banner was
  // failure-only -- nothing rendered at all while sync was healthy, so
  // there was no way to tell "sync is fine" from "sync was never checked".
  // sync_service.py's get_health() always included last_success_at on both
  // push and pull, but it was thrown away here. Two states now share the
  // SAME persistent element (#aura-sync-banner), swapped on every poll tick
  // by _renderSyncBanner():
  //   - calm  (default/healthy): small bottom-right pill, "Synced Ns ago ·
  //     N pending" -- ambient, ignorable.
  //   - alarm (SYNC_DEGRADED_THRESHOLD+ consecutive failures on either
  //     half): the original full-width top banner, unchanged.
  SYNC_POLL_MS: 30000,
  SYNC_DEGRADED_THRESHOLD: 3,
  // AUDIT-fix 2026-08-17: GET /api/licensing/status (checked once, at boot --
  // see init() above) only ever reads the LOCAL persisted license state --
  // it never contacts Owner. The only route that actually does (POST /api/
  // licensing/check-in) was previously wired to a manual "Check In" button
  // only (licensing.js). That meant a license Owner suspends/revokes mid-
  // session stayed persisted as ACTIVE locally -- and every mutation route's
  // require_license_capability() check reads exactly that stale persisted
  // state -- until the user happened to click Check In or restart the app.
  // Data sync already re-syncs every 10s (sync_service.py) regardless of
  // whether anything changed; licensing deserves the same "don't wait for
  // a restart" treatment, just on a much longer interval -- this is a
  // real network call that does crypto verification and DB writes on
  // Owner's side too, not a cheap local read like sync's own poll.
  LICENSE_CHECKIN_POLL_MS: 5 * 60 * 1000,

  _startSyncHealthPoll() {
    if (this._syncHealthTimer || this._syncPollDisabled) return;
    this._pollSyncHealth();                       // immediate first check
    this._syncHealthTimer = setInterval(() => this._pollSyncHealth(), this.SYNC_POLL_MS);
  },

  _stopSyncHealthPoll() {
    if (this._syncHealthTimer) { clearInterval(this._syncHealthTimer); this._syncHealthTimer = null; }
    this._renderSyncBanner(null);
  },

  _startLicenseCheckInPoll() {
    if (this._licenseCheckInTimer || this._licenseCheckInDisabled) return;
    // No immediate first call here (unlike sync health) -- init() already
    // did a fresh GET /api/licensing/status at boot; this timer is only for
    // catching a change Owner makes WHILE the app is already running.
    this._licenseCheckInTimer = setInterval(() => this._pollLicenseCheckIn(), this.LICENSE_CHECKIN_POLL_MS);
  },

  _stopLicenseCheckInPoll() {
    if (this._licenseCheckInTimer) { clearInterval(this._licenseCheckInTimer); this._licenseCheckInTimer = null; }
  },

  async _pollLicenseCheckIn() {
    let body = null;
    try {
      const res = await fetch('/api/licensing/check-in', { method: 'POST', credentials: 'include', cache: 'no-store' });
      if (!res.ok) return;                          // transient (network/Owner unreachable) -- retry next tick
      body = await res.json();
    } catch (e) { return; }                          // never let a background poll break the app
    if (!body || body.current_state === 'NOT_CONFIGURED') {
      // Licensing was never turned on for this install (no OWNER_LICENSING_
      // BASE_URL) -- this can never flip true without a restart, same
      // reasoning _pollSyncHealth uses for sync being off. Zero cost for
      // installs that never opted in.
      this._licenseCheckInDisabled = true;
      this._stopLicenseCheckInPoll();
    }
    // Deliberately no UI update here beyond that -- every mutation route
    // already independently re-reads this same persisted state via
    // require_license_capability() on its own next request; this poll's
    // whole job is making sure that persisted state doesn't go stale for
    // a full session, not rendering a banner itself.
  },

  async _pollSyncHealth() {
    let data = null;
    try {
      const res = await fetch('/api/sub/retail/sync/health', { credentials: 'include', cache: 'no-store' });
      if (!res.ok) return;                        // transient -- leave the banner as-is
      data = (await res.json()).data;
    } catch (e) { return; }                       // never let a poll break the app
    // Phase 7 stage 7b (docs/launch-readiness/phase7-offline-ux.md): the POS
    // tile's stale-stock treatment (subsystem-retail.js's _isStockStale())
    // reads this SAME snapshot rather than running a second poller -- this
    // fetch already runs every SYNC_POLL_MS. Stashed even on the
    // {configured:false} branch below (and on a null/malformed response),
    // so the tile learns "sync isn't on for this install" too and applies
    // its own silence rule instead of defaulting to "unknown -> stale".
    if (window.RetailSystem) window.RetailSystem._syncHealth = data;
    if (!data || data.configured !== true) {
      // Sync was never turned on for this install. Stop polling entirely --
      // app.py builds _sync_service at import time, so this can never flip
      // without a restart. Zero cost for installs that never opted in.
      this._syncPollDisabled = true;
      this._stopSyncHealthPoll();
      return;
    }
    this._renderSyncBanner(data);
  },

  // Relative-time formatter for last_success_at -- no library. Only the
  // fixed unit word goes through t(): t() is a whole-string exact-match
  // dictionary lookup (see i18n.js), and a dictionary key per possible
  // number isn't something that API can express, so the number itself is
  // plain-concatenated (same compromise subsystem-retail.js's scanner
  // status "Xs ago"/"Xm ago" already makes -- this version at least routes
  // its static words through t(), which that one never did).
  _formatRelativeTime(isoString) {
    if (!isoString) return null;
    const then = new Date(isoString).getTime();
    if (Number.isNaN(then)) return null;
    const diffSeconds = Math.max(0, Math.round((Date.now() - then) / 1000));
    if (diffSeconds < 5) return t('just now');
    if (diffSeconds < 60) return diffSeconds + t('s ago');
    const minutes = Math.round(diffSeconds / 60);
    if (minutes < 60) return minutes + t('m ago');
    const hours = Math.round(minutes / 60);
    if (hours < 24) return hours + t('h ago');
    const days = Math.round(hours / 24);
    return days + t('d ago');
  },

  // Whichever of push/pull most recently succeeded -- either half talking
  // to the relay counts as "this device is in contact with sync".
  _mostRecentSyncIso(pushIso, pullIso) {
    if (!pushIso) return pullIso || null;
    if (!pullIso) return pushIso;
    return new Date(pushIso).getTime() >= new Date(pullIso).getTime() ? pushIso : pullIso;
  },

  // "Synced 12s ago · 0 pending" -- the calm state's label. pending_count
  // comes straight from sync_service.py's get_health() (a live COUNT(*)
  // over sync_outbox -- that table holds ONLY rows not yet acked by a
  // successful push, see ack_outbox()'s docstring, so no status filter is
  // needed on the backend and none is needed here either).
  _syncIndicatorText(data) {
    const lastSuccess = this._mostRecentSyncIso(data.push.last_success_at, data.pull.last_success_at);
    const pending = Number.isFinite(data.pending_count) ? data.pending_count : 0;
    const head = lastSuccess
      ? (t('Synced') + ' ' + this._formatRelativeTime(lastSuccess))
      : t('Waiting for first sync');
    return head + ' · ' + pending + ' ' + t('pending');
  },

  _renderSyncBanner(data) {
    if (!data) {
      if (this._syncBannerEl) { this._syncBannerEl.remove(); this._syncBannerEl = null; }
      return;
    }

    const T = this.SYNC_DEGRADED_THRESHOLD;
    const pushBad = data.push.consecutive_failures >= T;
    const pullBad = data.pull.consecutive_failures >= T;

    if (!this._syncBannerEl) {
      const el = document.createElement('div');
      el.id = 'aura-sync-banner';
      document.body.appendChild(el);
      this._syncBannerEl = el;
    }

    // Phase 7 stage 7b adds a THIRD tier between "actively failing" (above,
    // keyed off LIVE consecutive_failures) and the calm pill (below): synced
    // before, but the PERSISTED last-success clock (stage 7a) is older than
    // RetailSystem.SYNC_STALE_THRESHOLD_SECONDS. Deliberately a SEPARATE
    // signal from consecutive_failures -- that counter resets to 0 on a
    // single lucky retry (or on an app restart, since it is in-memory only),
    // so a connection that is silently bad for hours but occasionally
    // reconnects could sit in the calm pill forever under the old two-way
    // split alone. never_synced is excluded here on purpose: a fresh install
    // that has never completed its first sync gets its OWN wording via the
    // calm pill's existing "Waiting for first sync" fallback (below), not
    // "behind by N" -- there is no "last synced" instant to report yet.
    //
    // Phase 7 stage 7c-i adds a FOURTH tier, checked BEFORE the plain
    // "behind" tier since it is the more specific (and more severe) of the
    // two: still behind, but by more than RetailSystem.
    // SYNC_STALE_WARNING_THRESHOLD_SECONDS (24h). Purely an escalation of
    // the SAME "behind" state's presentation -- it carries the identical
    // facts (last-sync clock time, unsynced count) the plain "behind" tier
    // does, just rendered more strongly, and still informs rather than
    // blocks (Decision 2).
    // Which of the 4 tiers this paint lands in -- computed once here so both
    // branches below (which tier to draw) and the icon's own motion (whether
    // to flip) read the SAME classification, rather than each re-deriving it
    // and risking drift. `changed` is true only when the tier actually
    // differs from the LAST paint, so the icon's aura-ic-flip animation
    // fires once on a real transition (e.g. calm -> behind) and never on a
    // same-tier repaint from the next poll tick -- see icons.js's MOTION
    // comment: state icons animate on change, not on an idle interval.
    let tier;
    if (pushBad || pullBad) tier = 'alarm';
    else if (!data.never_synced && this._isSyncBehindWarningThreshold(data)) tier = 'behind-warning';
    else if (!data.never_synced && this._isSyncBehindThreshold(data)) tier = 'behind';
    else tier = 'calm';
    const changed = tier !== this._syncBannerTier;
    this._syncBannerTier = tier;

    if (tier === 'alarm') this._renderSyncAlarmState(data, pushBad, pullBad, changed);
    else if (tier === 'behind-warning') this._renderSyncBehindWarningState(data, changed);
    else if (tier === 'behind') this._renderSyncBehindState(data, changed);
    else this._renderSyncCalmState(data, changed);
  },

  // See _renderSyncBanner's comment just above for why this is a distinct
  // signal from consecutive_failures. RetailSystem.SYNC_STALE_THRESHOLD_
  // SECONDS is read rather than declared a second time here, so the banner
  // and the POS tile's own staleness check (subsystem-retail.js) can never
  // disagree about what "stale" means (docs/launch-readiness/
  // phase7-offline-ux.md). Guarded against RetailSystem not being loaded
  // (defaults to "not behind" -- the calm pill -- rather than throwing).
  _isSyncBehindThreshold(data) {
    const threshold = window.RetailSystem && window.RetailSystem.SYNC_STALE_THRESHOLD_SECONDS;
    const secs = data.seconds_since_last_success;
    return typeof threshold === 'number' && typeof secs === 'number' && secs > threshold;
  },

  // Phase 7 stage 7c-i: the 24-hour escalation of _isSyncBehindThreshold
  // just above -- identical shape, reading RetailSystem.
  // SYNC_STALE_WARNING_THRESHOLD_SECONDS instead of RetailSystem.
  // SYNC_STALE_THRESHOLD_SECONDS, and the same "not behind" default when
  // RetailSystem isn't loaded.
  _isSyncBehindWarningThreshold(data) {
    const threshold = window.RetailSystem && window.RetailSystem.SYNC_STALE_WARNING_THRESHOLD_SECONDS;
    const secs = data.seconds_since_last_success;
    return typeof threshold === 'number' && typeof secs === 'number' && secs > threshold;
  },

  // Renders one of the banner's state icons through icons.js, falling back
  // to '' when AuraIcons isn't loaded -- several standalone JS test
  // harnesses (retail_offline_banner_*_test.js) load app-shell.js on its
  // own without icons.js, matching how the sidebar nav icons at the top of
  // this file already guard the same call. 'flip' plays only when
  // _renderSyncBanner already determined the tier changed since the last
  // paint, never on a same-tier repaint from the next poll tick.
  _syncIcon(name, changed) {
    return window.AuraIcons ? AuraIcons.render(name, 15, changed ? { animate: 'flip' } : undefined) : '';
  },

  // State 4 (docs/launch-readiness/phase7-offline-ux.md, stage decomposition:
  // "Offline since 14:20 -- 412 unsynced"). Full-width and hard to miss, like
  // the alarm state above -- this is meant to be SEEN, not ambient -- but
  // visually distinct (no red/amber) since nothing is actively erroring
  // right now; the device just hasn't reached the relay in a while.
  _renderSyncBehindState(data, changed) {
    const el = this._syncBannerEl;
    const lastSuccess = this._mostRecentSyncIso(data.push.last_success_at, data.pull.last_success_at);
    const pending = Number.isFinite(data.pending_count) ? data.pending_count : 0;
    // Clock time ("14:20"), not "45m ago" -- matches the POS tile's own
    // dated figure (subsystem-retail.js's _formatClockTime, reused rather
    // than re-implemented here) and, unlike a relative label, does not go
    // stale on screen itself the next time someone glances at it.
    const clock = (window.RetailSystem && window.RetailSystem._formatClockTime)
      ? window.RetailSystem._formatClockTime(lastSuccess)
      : null;
    // lastSuccess is guaranteed non-null here (_renderSyncBanner only routes
    // here when !data.never_synced), but fall back rather than ever render a
    // blank "Offline since" if the clock formatter can't be reached.
    const when = clock || this._formatRelativeTime(lastSuccess) || '';
    const headline = t('Offline since') + ' ' + this._esc(when) + ' — ' +
      this._esc(String(pending)) + ' ' + t('unsynced');

    el.title = '';
    el.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#1e1e2e;'
      + 'border-bottom:2px solid #60a5fa;color:white;padding:9px 18px;font-size:13px;'
      + 'line-height:1.45;text-align:center;z-index:99998;'
      + 'box-shadow:0 4px 18px rgba(0,0,0,.35);';
    el.innerHTML = '<span style="color:#60a5fa;font-weight:700;display:inline-flex;align-items:center;gap:6px;">'
      + this._syncIcon('cloud-off', changed) + headline + '</span>';
  },

  // State 5, Phase 7 stage 7c-i (docs/launch-readiness/phase7-offline-ux.md,
  // "PART 2 -- the 24-hour soft warning"): the SAME two facts
  // _renderSyncBehindState carries (last-sync clock time, unsynced count),
  // escalated once the device has been behind for more than RetailSystem.
  // SYNC_STALE_WARNING_THRESHOLD_SECONDS (24h). Visibly stronger than the
  // plain "behind" tier -- red instead of blue, a heavier border and
  // font-weight, a distinct ⚠ icon, and an explanatory detail sentence the
  // plain tier does not carry -- but still informational only: no
  // capability is checked, and nothing here refuses any write (Decision 2:
  // "The 24-hour soft warning needs no capability: it informs, it does not
  // block").
  _renderSyncBehindWarningState(data, changed) {
    const el = this._syncBannerEl;
    const lastSuccess = this._mostRecentSyncIso(data.push.last_success_at, data.pull.last_success_at);
    const pending = Number.isFinite(data.pending_count) ? data.pending_count : 0;
    const clock = (window.RetailSystem && window.RetailSystem._formatClockTime)
      ? window.RetailSystem._formatClockTime(lastSuccess)
      : null;
    // lastSuccess is guaranteed non-null here (_renderSyncBanner only routes
    // here when !data.never_synced), same fallback discipline as the plain
    // "behind" tier just above.
    const when = clock || this._formatRelativeTime(lastSuccess) || '';
    const headline = t('Still offline since') + ' ' + this._esc(when) + ' — ' +
      this._esc(String(pending)) + ' ' + t('unsynced');
    const detail = t("This device has not synced with your other devices in over 24 hours. Reconnect it as soon as you can.");

    el.title = '';
    el.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#1e1e2e;'
      + 'border-bottom:3px solid #ef4444;color:white;padding:9px 18px;font-size:13px;'
      + 'line-height:1.45;text-align:center;z-index:99998;'
      + 'box-shadow:0 4px 18px rgba(0,0,0,.35);';
    el.innerHTML = '<span style="color:#ef4444;font-weight:800;display:inline-flex;align-items:center;gap:6px;">'
      + this._syncIcon('triangle-alert', changed) + headline + '</span>'
      + '<span style="opacity:.85;margin-left:10px;">' + detail + '</span>';
  },

  // The original failure-only banner, unchanged in look and behavior: full-
  // width, top of page, impossible to miss. Reached only once either half
  // has failed SYNC_DEGRADED_THRESHOLD times in a row -- a single blip
  // never triggers it (see sync_service.py's run_once()/per-tick retry).
  _renderSyncAlarmState(data, pushBad, pullBad, changed) {
    const el = this._syncBannerEl;
    let headline;
    if (pushBad && pullBad) headline = t("Not syncing with your other devices right now");
    else if (pushBad)       headline = t("This device's recent changes haven't reached your other devices yet");
    else                    headline = t("This device isn't receiving updates from your other devices right now");
    const detail = t("This device is still working normally. Everything will catch up automatically once the connection comes back.");

    el.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#1e1e2e;'
      + 'border-bottom:2px solid #fbbf24;color:white;padding:9px 18px;font-size:13px;'
      + 'line-height:1.45;text-align:center;z-index:99998;'
      + 'box-shadow:0 4px 18px rgba(0,0,0,.35);';
    // last_failure_reason goes ONLY in title= -- support can hover for the
    // real code, the shop owner never sees "INVALID_SIGNATURE".
    const reason = (pushBad ? data.push.last_failure_reason : data.pull.last_failure_reason) || '';
    el.title = reason ? ('Sync detail: ' + reason) : '';
    el.innerHTML =
      '<span style="color:#fbbf24;font-weight:700;display:inline-flex;align-items:center;gap:6px;">'
      + this._syncIcon('triangle-alert', changed) + headline + '</span>'
      + '<span style="opacity:.8;margin-left:10px;">' + detail + '</span>';
  },

  // New calm state: a small, unobtrusive bottom-right pill -- ambient
  // confirmation that sync is alive, not an alert. This is the whole point
  // of the freshness indicator: previously NOTHING rendered here while sync
  // was working normally. pointer-events:none so it never sits in the way
  // of whatever's underneath it in that corner. The dot (not an icon) is
  // deliberate here -- see icons.js's module comment: "calm" is the one tier
  // that stays untouched by the redesign, since a plain status dot already
  // reads as calm/ambient and swapping in an icon would just be motion for
  // its own sake on the one tier that should feel like nothing is happening.
  _renderSyncCalmState(data, changed) {
    const el = this._syncBannerEl;
    el.style.cssText = 'position:fixed;bottom:14px;right:14px;display:inline-flex;'
      + 'align-items:center;gap:7px;padding:6px 12px;border-radius:20px;'
      // State tokens, not the old HUD mint (#a7f3d0 on a 10% green tint was
      // ~1.5:1 over the light shell). An opaque state pair is self-contained,
      // so the pill reads the same over whatever corner it floats on, in
      // either theme.
      + 'background:var(--state-success-surface);border:1px solid var(--state-success-border);'
      + 'color:var(--state-success-text);font-size:12px;font-weight:500;letter-spacing:.2px;'
      + 'z-index:99997;box-shadow:0 4px 14px rgba(0,0,0,.25);pointer-events:none;';
    el.title = '';
    el.innerHTML =
      '<span style="width:6px;height:6px;border-radius:50%;background:var(--state-success-text);display:inline-block;flex-shrink:0;"></span>'
      + '<span>' + this._syncIndicatorText(data) + '</span>';
  },

  // ── Wire up real-time WebSocket refresh (called once after init) ─────────────
  _initRealtimeRefresh() {
    // Use app.js socket if available, else connect independently
    const socket = window.App?.socket;
    if (!socket) return;
    socket.on('refresh', (data) => {
      if (!this.active || !this.currentSection) return;
      const dept = (data.department || data.source_system || '').toLowerCase();
      if (dept === this.active || dept === 'all' || !dept) {
        this._navigate(this.currentSection);
      }
    });
  },
  // ── Shared utility ──────────────────────────────────────────────
  // Global auth guard: any API call that returns 401 (an expired/revoked
  // session) surfaces the relogin modal instead of letting the raw JSON
  // error leak into a dashboard. Installed once; covers every fetch on the
  // page, not just the apiGet/apiPost helpers below.
  _installAuthGuard() {
    if (window.__auraAuthGuardInstalled) return;
    window.__auraAuthGuardInstalled = true;
    const origFetch = window.fetch.bind(window);
    // Endpoints that legitimately return 401 during auth flow — never bounce on these.
    const SKIP = ['/api/auth/', '/api/onboarding/', '/api/system/', '/api/demo/'];
    window.fetch = async (...args) => {
      const res = await origFetch(...args);
      try {
        const url = (typeof args[0] === 'string') ? args[0] : (args[0] && args[0].url) || '';
        if (res.status === 401 && url.indexOf('/api/') !== -1 && !SKIP.some(s => url.indexOf(s) !== -1)) {
          if (!SubsystemApp._authPrompted) {
            SubsystemApp._authPrompted = true;
            // Defer so the in-flight call stack unwinds before we swap the UI.
            setTimeout(() => {
              try { SubsystemApp.showReloginModal('Your session has expired. Please log in again.'); }
              catch (e) { location.reload(); }
            }, 0);
          }
        }
      } catch (e) { /* the guard must never break a request */ }
      return res;
    };
  },

  async apiGet(path) {
    const res = await fetch(path);
    if (!res.ok) {
      if (res.status === 401) throw new Error('Session expired — please log in again.');
      throw new Error(await res.text());
    }
    return res.json();
  },

  async apiPost(path, body, method = 'POST') {
    const res = await fetch(path, {
      method: method,
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : null
    });
    if (!res.ok) {
      if (res.status === 401) throw new Error('Session expired — please log in again.');
      throw new Error(await res.text());
    }
    
    // Broadcast real-time update
    if (window.App && App.socket && ['POST', 'PUT', 'DELETE'].includes(method.toUpperCase())) {
      const domain = window.Auth?.domain?.id || 'demo';
      App.socket.emit('data_update', { department: this.active, domain: domain });
    }
    
    return res.json().catch(() => ({}));
  },
  
  async apiDelete(path) {
    return this.apiPost(path, null, 'DELETE');
  },

  async wipeDemoData() {
    if (!confirm('Are you sure you want to permanently wipe all data in this subsystem?')) return;
    try {
      const res = await fetch(`/api/sub/${this.active}/demo-wipe`, { method: 'DELETE' });
      if (!res.ok) throw new Error(await res.text());
      this.showToast('Database wiped successfully!', 'success');
      
      // Emit data_update to instantly refresh dashboards on all clients
      const domain = window.Auth?.domain?.id || 'demo';
      if (window.App && App.socket) {
          App.socket.emit('data_update', { department: this.active, domain: domain });
      }
      
      // Navigate back to dashboard to refresh view locally
      this._navigate('dashboard');
    } catch(e) {
      this.showToast('Error wiping database', 'error');
      console.error(e);
    }
  },

  formatCurrency(v) {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(v || 0);
  },

  formatDate(d) {
    if (!d) return '—';
    return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  },

  badge(text, color) {
    const colors = {
      green: '#34d399', red: '#f87171', yellow: '#fbbf24',
      blue: '#60a5fa', purple: '#a78bfa', orange: '#fb923c',
      gray: '#6b7280'
    };
    const c = colors[color] || colors.gray;
    return `<span style="background:${c}22;color:${c};padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600">${text}</span>`;
  },

  kpiCard(label, value, icon = '📊', color = null, trend = null) {
    const accent = color || 'var(--sub-accent)';
    return `
      <div class="sub-kpi-card" style="border-left-color:${accent}">
        <div class="sub-kpi-icon">${icon}</div>
        <div class="sub-kpi-body">
          <div class="sub-kpi-label">${t(label)}</div>
          <div class="sub-kpi-value" style="color:${accent}">${value}</div>
          ${trend ? `<div class="sub-kpi-trend">${trend}</div>` : ''}
        </div>
      </div>
    `;
  },

  renderChart(canvasId, config) {
    const el = document.getElementById(canvasId);
    if (!el) return;
    // Destroy prior instance if any
    if (el._chartInstance) el._chartInstance.destroy();
    el._chartInstance = new Chart(el.getContext('2d'), config);
  },

  showToast(msg, type = 'info') {
    const colors = { success: '#34d399', error: '#f87171', info: 'var(--sub-accent)' };
    const toast = document.createElement('div');
    toast.style.cssText = `position:fixed;bottom:var(--overlay-inset-block-end,24px);right:24px;background:#1e1e2e;border:1px solid ${colors[type]};color:white;padding:12px 20px;border-radius:10px;font-size:13px;z-index:99999;animation:slideUp .3s ease;box-shadow:0 8px 25px rgba(0,0,0,.4)`;
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
  }
};

window.SubsystemApp = SubsystemApp;
window.LogoSystem = LogoSystem;

document.addEventListener('DOMContentLoaded', () => {
  LogoSystem.init();

  // Hash routing for subsystems is now handled exclusively by app.js

  // Sub AI input auto-resize
  const subInput = document.getElementById('sub-ai-input');
  if (subInput) {
    subInput.addEventListener('input', () => {
      subInput.style.height = 'auto';
      subInput.style.height = Math.min(subInput.scrollHeight, 120) + 'px';
    });
  }
});
// ── Mobile: auto-label table cells (data-label = column header) so tables can
//    render as stacked, labelled cards on phones via the @media(max-width:600px)
//    rules in main.css. Generic across every subsystem. No effect on desktop
//    (the data-label attrs are only surfaced by the mobile CSS). ──────────────
(function () {
  function labelize(table) {
    const heads = [...table.querySelectorAll('thead th')].map(th => th.textContent.trim());
    if (!heads.length) return;
    table.querySelectorAll('tbody tr').forEach(tr => {
      [...tr.children].forEach((td, i) => {
        if (heads[i] !== undefined && !td.hasAttribute('data-label')) {
          td.setAttribute('data-label', heads[i]);
        }
      });
    });
  }
  let scheduled = false;
  function scan() {
    scheduled = false;
    document.querySelectorAll('#sub-content table, .cl-modal table').forEach(labelize);
  }
  function schedule() { if (!scheduled) { scheduled = true; requestAnimationFrame(scan); } }
  function start() {
    try {
      new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
      scan();
    } catch (e) {}
  }
  if (document.readyState !== 'loading') start();
  else document.addEventListener('DOMContentLoaded', start);
})();
