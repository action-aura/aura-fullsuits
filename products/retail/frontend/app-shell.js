/**
 * Action Aura — Subsystem Core
 * Shared shell: router, sidebar, header, AI chat panel, and logo system.
 */

// ── Theme Engine ──────────────────────────────────────────────────────────────
const ThemeEngine = {
  current: 'midnight',

  themes: {
    midnight:    { name:'Midnight',   icon:'🌑', dot:'#080810', bg:'#050508',  panel:'#0a0a10', sidebar:'#080810'  },
    'deep-space':{ name:'Deep Space', icon:'🌌', dot:'#0b1225', bg:'#020617',  panel:'#0f172a', sidebar:'#0b1225'  },
    carbon:      { name:'Carbon',     icon:'🪨', dot:'#161618', bg:'#111113',  panel:'#1a1a1f', sidebar:'#161618'  },
    emerald:     { name:'Emerald',    icon:'💚', dot:'#081a14', bg:'#061410',  panel:'#0a2018', sidebar:'#081a14'  },
    crimson:     { name:'Crimson',    icon:'🔴', dot:'#140808', bg:'#100606',  panel:'#1a0a0a', sidebar:'#140808'  },
    violet:      { name:'Violet',     icon:'💜', dot:'#0e0b1a', bg:'#08060e',  panel:'#110e1f', sidebar:'#0e0b1a'  },
  },

  apply(name) {
    const t = this.themes[name];
    if (!t) return;
    document.documentElement.setAttribute('data-app-theme', name);
    document.documentElement.style.setProperty('--bg-dark',  t.bg);
    document.documentElement.style.setProperty('--bg-panel', t.panel);
    localStorage.setItem('aura_app_theme', name);
    this.current = name;
    // Refresh picker active state if open
    document.querySelectorAll('.theme-swatch').forEach(el => {
      el.classList.toggle('active', el.dataset.theme === name);
    });
  },

  init() {
    const saved = localStorage.getItem('aura_app_theme') || 'midnight';
    this.apply(saved);
  },

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
    label.textContent = '🎨 App Theme';
    panel.appendChild(label);

    const grid = document.createElement('div');
    grid.className = 'theme-grid';
    Object.entries(this.themes).forEach(([key, t]) => {
      const swatch = document.createElement('div');
      swatch.className = 'theme-swatch' + (this.current === key ? ' active' : '');
      swatch.dataset.theme = key;
      swatch.addEventListener('click', () => this.apply(key));

      const dot = document.createElement('div');
      dot.className = 'theme-swatch-dot';
      dot.style.background = t.sidebar;
      dot.style.boxShadow = 'inset 0 0 0 2px rgba(255,255,255,0.1), 0 0 12px ' + t.sidebar + '88';

      const name = document.createElement('div');
      name.className = 'theme-swatch-name';
      name.textContent = t.name;

      swatch.appendChild(dot);
      swatch.appendChild(name);
      grid.appendChild(swatch);
    });
    panel.appendChild(grid);

    const hint = document.createElement('div');
    hint.style.cssText = 'font-size:10px;color:rgba(255,255,255,0.25);text-align:center;margin-top:4px;';
    hint.textContent = 'Persists across sessions';
    panel.appendChild(hint);

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

  // True if the current user may access a clinic area restricted to the given
  // clinic roles. The clinic owner (global admin) always passes. Mirrors the
  // backend require_clinic_role() gate so the UI hides what the API would reject.
  canClinic(...roles) {
    if (this.role === 'admin') return true;          // owner sees everything
    if (!roles || roles.length === 0) return true;   // unrestricted area
    return roles.includes(this.clinicRole);
  },

  // Light/Dark theme toggle (#15). Persists to localStorage; the pre-paint script
  // in index.html applies the saved choice on load (default: light).
  toggleTheme() {
    const cur = document.documentElement.getAttribute('data-theme') || 'light';
    const next = cur === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('aura_theme', next); } catch (e) {}
    const btn = document.getElementById('aura-theme-toggle');
    if (btn) btn.textContent = next === 'dark' ? '☀️' : '🌙';
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
        { id: 'suppliers',  label: 'Suppliers',         icon: '🏭' },
        { id: 'purchases',  label: 'Purchase Orders',   icon: '📋' },
        { id: 'returns',    label: 'Returns',           icon: '↩️' },
        { id: 'reports',    label: 'Reports',           icon: '📊' },
        { id: 'scanner',    label: 'Barcode Scanner',   icon: '🔦', desktopOnly: true },
        // feat/reorder-automation-foundation: gated on this.isAdminDevice
        // (resolved once at init() via GET /api/devices/me -- see that
        // method), same adminOnly mechanism the _renderShell nav filter
        // below applies to every entry with this flag. Hidden entirely
        // (not just disabled) on any device that isn't this company's
        // single admin device, fail-closed if the check couldn't run.
        { id: 'admin-center', label: 'Settings',        icon: '⚙️', adminOnly: true },
        // feat/audit-log-viewer: same adminOnly mechanism as Admin Center
        // above -- refund/void/product-change audit trail carries every
        // user's attribution, not just this device's, so it's gated the
        // same way rather than shown to every logged-in user. The backend
        // route (GET /api/sub/retail/audit-log) also enforces this itself
        // (see retail_api.py's _is_admin_device) -- unlike Admin Center's
        // reorder-requests route, this one does NOT rely on nav-hiding alone.
        { id: 'audit-log',   label: 'Audit Log',        icon: '📜', adminOnly: true },
      ]
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

    // ── Device activation gate ─────────────────────────────────────────────
    // Checked before anything else, including the auth gate below, so an
    // unactivated device on a real licensed install never reaches setup or
    // login. Skipped in demo mode (matches the auth gate's own skip) and
    // skipped when licensing isn't wired up on this install at all --
    // /api/licensing/status's NOT_CONFIGURED shape is ambiguous by itself
    // (returned both when genuinely unconfigured AND when configured but
    // never activated -- see commercial_runtime/licensing_contracts's own
    // test_status_before_activation_is_not_configured_shape, which pins the
    // configured-but-fresh case on purpose), but the "detail" field is only
    // ever attached by the genuinely-unconfigured branch
    // (routes.py::_not_configured_response) -- present_status(None) never
    // sets it, so its presence is the real disambiguating signal, no
    // backend change needed.
    if (!window.isDemoMode && sessionStorage.getItem('demo_mode') !== 'true') {
      try {
        const lic = await fetch('/api/licensing/status', { cache: 'no-store' }).then(r => r.json());
        const needsActivation = lic.current_state === 'ACTIVATION_REQUIRED'
          || lic.current_state === 'ACTIVATING'
          || (lic.current_state === 'NOT_CONFIGURED' && !lic.detail);
        if (needsActivation) {
          location.href = '/static/licensing.html?gate=1';
          return;
        }
      } catch (e) {
        // Network hiccup: fail open, same as every other best-effort check
        // in this init() sequence (active-modules fetch above does the same).
      }
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
          const status = await fetch('/api/onboarding/status', { cache: 'no-store' })
            .then(r => r.json()).catch(() => ({ needs_setup: false }));
          if (status.needs_setup) {
            this.showSetupModal();
          } else {
            this.showReloginModal('Sign in to your store');
          }
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
        } catch (e) {
          this.isAdminDevice = false;
        }
      } catch(e) {
        // Can't reach server — proceed and let individual API calls handle 401s
      }
    }

    // Single-product build: there is only ever one system, so skip the
    // multi-subsystem chooser entirely and launch straight into it.
    this.launch('retail', 'dashboard');
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
    document.documentElement.style.setProperty('--sub-accent', sys.accent);
    document.documentElement.style.setProperty('--sub-accent-rgb', sys.accentRgb);

    // Show subsystem page, hide others
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const page = document.getElementById('page-subsystem');
    if (page) page.classList.add('active');

    this._renderShell(sys, systemId);
    this._navigate(this.currentSection);
    LogoSystem.init();
    this._startSyncHealthPoll();
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
        this.showSetupModal();
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
  showSetupModal() {
    document.getElementById('aura-relogin-modal')?.remove();
    this._authModalOpen = true;
    document.documentElement.style.setProperty('--sub-accent', this.systems.retail.accent);
    document.documentElement.style.setProperty('--sub-accent-rgb', this.systems.retail.accentRgb);
    const overlay = document.createElement('div');
    overlay.id = 'aura-relogin-modal';
    overlay.className = 'auth-overlay';
    overlay.innerHTML = `
      <div class="auth-card">
        <button onclick="AuraI18n.toggle()" title="Language / اللغة" class="auth-lang-btn">EN | ع</button>
        <div class="auth-head">
          <div class="auth-icon">⚡</div>
          <h2 class="auth-title">${t('Welcome to Action Aura')}</h2>
          <p class="auth-sub">${t('Create your administrator account to get started.')}</p>
          <p class="auth-note">This setup runs <strong>only once</strong>. Your credentials will be saved permanently.</p>
        </div>
        <div class="auth-grid-2">
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
        <div class="auth-field">
          <label for="su-email">Email Address *</label>
          <input id="su-email" type="email" placeholder="admin@yourcompany.com" autocomplete="email"
            onkeydown="if(event.key==='Enter')document.getElementById('su-pass').focus()" />
        </div>
        <div class="auth-grid-2">
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
    const pass    = document.getElementById('su-pass')?.value;
    const pass2   = document.getElementById('su-pass2')?.value;
    const errEl   = document.getElementById('su-error');
    const btn     = document.getElementById('su-btn');

    const showErr = (msg) => { if(errEl){errEl.textContent=msg;errEl.style.display='block';} if(btn){btn.textContent='Create Account & Launch';btn.disabled=false;} };

    if (!name)                   return showErr('Full name is required.');
    if (!email || !email.includes('@')) return showErr('A valid email address is required.');
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

      if (!data.success) return showErr(data.error || 'Could not create account. Please try again.');

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
        <div style="font-size:56px;margin-bottom:16px;">✅</div>
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

  // ── RETURNING USER LOGIN MODAL ────────────────────────────────────────────
  // Same auth-* component family as the setup modal (main.css) — one visual
  // language for both halves of the auth flow, branded with the product
  // accent instead of the old off-brand teal.
  showReloginModal(msg = 'Your session has expired. Please log in again.') {
    document.getElementById('aura-relogin-modal')?.remove();
    this._authModalOpen = true;
    document.documentElement.style.setProperty('--sub-accent', this.systems.retail.accent);
    document.documentElement.style.setProperty('--sub-accent-rgb', this.systems.retail.accentRgb);
    const overlay = document.createElement('div');
    overlay.id = 'aura-relogin-modal';
    overlay.className = 'auth-overlay';
    overlay.innerHTML = `
      <div class="auth-card auth-card-compact">
        <button onclick="AuraI18n.toggle()" title="Language / اللغة" class="auth-lang-btn">EN | ع</button>
        <div class="auth-head">
          <div class="auth-icon">🔐</div>
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
          // Already inside a subsystem — reload current section
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
        <div class="auth-icon">✉️</div>
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
          <div class="auth-icon">✅</div>
          <h2 class="auth-title">${t('Check your email')}</h2>
          <p class="auth-sub">${t('If an account exists for that email, a reset link is on its way.')}</p>
        </div>
        <p class="auth-foot"><a href="#" onclick="SubsystemApp.showReloginModal();return false;">${t('Back to sign in')}</a></p>`;
    }
  },

  // ── VERIFY EMAIL LANDING SCREEN (from #verify-email/<token>) ──────────────
  _showVerifyEmailScreen(token) {
    document.documentElement.style.setProperty('--sub-accent', this.systems.retail.accent);
    document.documentElement.style.setProperty('--sub-accent-rgb', this.systems.retail.accentRgb);
    const overlay = document.createElement('div');
    overlay.id = 'aura-verify-email-screen';
    overlay.className = 'auth-overlay';
    overlay.innerHTML = `
      <div class="auth-card auth-card-compact">
        <div class="auth-head">
          <div class="auth-icon">✉️</div>
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
    document.documentElement.style.setProperty('--sub-accent', this.systems.retail.accent);
    document.documentElement.style.setProperty('--sub-accent-rgb', this.systems.retail.accentRgb);
    const overlay = document.createElement('div');
    overlay.id = 'aura-reset-password-screen';
    overlay.className = 'auth-overlay';
    overlay.innerHTML = `
      <div class="auth-card auth-card-compact">
        <div class="auth-head">
          <div class="auth-icon">🔑</div>
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
            <div class="auth-icon">✅</div>
            <h2 class="auth-title">${t('Password updated')}</h2>
            <p class="auth-sub">${t('You can now sign in with your new password.')}</p>
          </div>
          <button class="auth-submit" onclick="location.hash='';SubsystemApp.init();">${t('Continue to sign in')}</button>`;
      }
    } catch (e) {
      showErr('Network error. Make sure the server is running.');
    }
  },

  _renderShell(sys, systemId) {
    const shell = document.getElementById('subsystem-shell');
    if (!shell) return;
    
    const hasAI = this.activeModules && (this.activeModules.includes('all') || this.activeModules.includes('ai_agent'));

    shell.innerHTML = `
      <!-- Subsystem Sidebar -->
      <aside class="sub-sidebar" id="sub-sidebar">
        <div class="sub-sidebar-brand aura-logo" title="Return to Home">
          <div class="sub-brand-icon">${window.AuraIcons ? AuraIcons.render(sys.icon, 22) : sys.icon}</div>
          <div class="sub-brand-text">
            <span class="sub-system-name">${t(sys.name)}</span>
            <span class="logo-name" style="font-size:11px;color:var(--text-muted)">Action<strong>Aura</strong></span>
          </div>
        </div>

        <nav class="sub-nav" id="sub-nav">
          ${sys.nav.filter(item => (!item.roles || this.canClinic(...item.roles)) && (!item.desktopOnly || !/Android/i.test(navigator.userAgent || '')) && (!item.adminOnly || this.isAdminDevice)).map(item => `
            <a class="sub-nav-item ${item.id === 'dashboard' ? 'active' : ''}"
               data-section="${item.id}"
               onclick="SubsystemApp._navigate('${item.id}')">
              <span class="sub-nav-icon">${window.AuraIcons ? AuraIcons.render(item.icon, 17) : item.icon}</span>
              <span class="sub-nav-label">${t(item.label)}</span>
            </a>
          `).join('')}
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
          <button class="sub-exit-btn" onclick="SubsystemApp.logout()" style="background:rgba(239,68,68,0.1);border-color:rgba(239,68,68,0.25);color:#f87171;margin-top:4px;">
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
            <button class="sub-header-btn" id="aura-theme-toggle" onclick="SubsystemApp.toggleTheme()" title="Light / Dark mode" style="font-size:15px;">${(document.documentElement.getAttribute('data-theme')==='dark')?'☀️':'🌙'}</button>
            <button class="sub-header-btn" onclick="ThemeEngine.openPicker()" title="Change UI theme" style="font-size:15px;">🎨</button>
            <div class="sub-header-badge" style="background:rgba(${sys.accentRgb},0.15);border-color:${sys.accent};color:${sys.accent}">
              ${window.AuraIcons ? AuraIcons.render(sys.icon, 14) : sys.icon} ${t(sys.name)}
            </div>
            ${hasAI ? `<button class="sub-header-btn" onclick="SubAI.open('${systemId}')" title="AI Assistant">🤖</button>` : ''}
          </div>
        </header>

        <main class="sub-content" id="sub-content">
          <div style="text-align:center;padding:80px;color:var(--text-muted)">Loading...</div>
        </main>
      </div>
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
        this._updateLiveBadge(true);

      } catch (err) {
        this._updateLiveBadge(false);
        const c = document.getElementById('sub-content');
        if (c) c.innerHTML = `
          <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;padding:60px;text-align:center;">
            <div style="font-size:48px;margin-bottom:20px;">⚠️</div>
            <h3 style="color:#f87171;margin-bottom:12px;font-size:20px;">Failed to load ${sectionId}</h3>
            <p style="color:#64748b;max-width:500px;line-height:1.6;font-size:14px;">${err.message || 'An unexpected error occurred.'}</p>
            <button onclick="SubsystemApp._navigate('${sectionId}')" style="margin-top:24px;padding:10px 24px;background:#3b82f6;border:none;border-radius:8px;color:white;font-weight:600;cursor:pointer;font-size:14px;">↻ Retry</button>
          </div>`;
        console.error('[SubsystemApp] Error in ' + this.active + '/' + sectionId + ':', err);
      }
    }, 50);
  },

  // ── Show/hide a "● LIVE" badge in the header ────────────────────────────────
  _updateLiveBadge(live) {
    let badge = document.getElementById('sub-live-badge');
    if (!live) { badge?.remove(); return; }
    if (badge) return; // Already shown
    const hdr = document.getElementById('sub-header-section');
    if (!hdr) return;
    badge = document.createElement('span');
    badge.id = 'sub-live-badge';
    badge.style.cssText = 'display:inline-flex;align-items:center;gap:5px;margin-left:12px;padding:3px 10px;border-radius:20px;background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.3);color:#10b981;font-size:11px;font-weight:600;letter-spacing:.5px;vertical-align:middle;';
    badge.innerHTML = '<span style="width:6px;height:6px;border-radius:50%;background:#10b981;animation:live-pulse 2s infinite;display:inline-block;"></span> LIVE';
    if (!document.getElementById('live-pulse-style')) {
      const s = document.createElement('style');
      s.id = 'live-pulse-style';
      s.textContent = '@keyframes live-pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.5;transform:scale(1.3)}}';
      document.head.appendChild(s);
    }
    hdr.parentNode?.insertBefore(badge, hdr.nextSibling);
  },

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

  _startSyncHealthPoll() {
    if (this._syncHealthTimer || this._syncPollDisabled) return;
    this._pollSyncHealth();                       // immediate first check
    this._syncHealthTimer = setInterval(() => this._pollSyncHealth(), this.SYNC_POLL_MS);
  },

  _stopSyncHealthPoll() {
    if (this._syncHealthTimer) { clearInterval(this._syncHealthTimer); this._syncHealthTimer = null; }
    this._renderSyncBanner(null);
  },

  async _pollSyncHealth() {
    let data = null;
    try {
      const res = await fetch('/api/sub/retail/sync/health', { credentials: 'include', cache: 'no-store' });
      if (!res.ok) return;                        // transient -- leave the banner as-is
      data = (await res.json()).data;
    } catch (e) { return; }                       // never let a poll break the app
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

    if (pushBad || pullBad) this._renderSyncAlarmState(data, pushBad, pullBad);
    else this._renderSyncCalmState(data);
  },

  // The original failure-only banner, unchanged in look and behavior: full-
  // width, top of page, impossible to miss. Reached only once either half
  // has failed SYNC_DEGRADED_THRESHOLD times in a row -- a single blip
  // never triggers it (see sync_service.py's run_once()/per-tick retry).
  _renderSyncAlarmState(data, pushBad, pullBad) {
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
      '<span style="color:#fbbf24;font-weight:700;">⚠ ' + headline + '</span>'
      + '<span style="opacity:.8;margin-left:10px;">' + detail + '</span>';
  },

  // New calm state: a small, unobtrusive bottom-right pill -- ambient
  // confirmation that sync is alive, not an alert. This is the whole point
  // of the freshness indicator: previously NOTHING rendered here while sync
  // was working normally. pointer-events:none so it never sits in the way
  // of whatever's underneath it in that corner.
  _renderSyncCalmState(data) {
    const el = this._syncBannerEl;
    el.style.cssText = 'position:fixed;bottom:14px;right:14px;display:inline-flex;'
      + 'align-items:center;gap:7px;padding:6px 12px;border-radius:20px;'
      + 'background:rgba(16,185,129,0.10);border:1px solid rgba(16,185,129,0.28);'
      + 'color:#a7f3d0;font-size:12px;font-weight:500;letter-spacing:.2px;'
      + 'z-index:99997;box-shadow:0 4px 14px rgba(0,0,0,.25);pointer-events:none;';
    el.title = '';
    el.innerHTML =
      '<span style="width:6px;height:6px;border-radius:50%;background:#10b981;display:inline-block;flex-shrink:0;"></span>'
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
    toast.style.cssText = `position:fixed;bottom:24px;right:24px;background:#1e1e2e;border:1px solid ${colors[type]};color:white;padding:12px 20px;border-radius:10px;font-size:13px;z-index:99999;animation:slideUp .3s ease;box-shadow:0 8px 25px rgba(0,0,0,.4)`;
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
