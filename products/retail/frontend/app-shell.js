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
      ]
    },
  },

  async init() {
    this._authPrompted = false;     // re-arm the 401 guard on every (re)init
    this._installAuthGuard();
    ThemeEngine.init();
    KPIDragManager.init();
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
            this.showReloginModal('Please log in to access the Enterprise Platform.');
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
  },

  exit() {
    // Single-product build: "exit" (clicking the logo) just returns to the
    // retail dashboard — there is no other subsystem to choose between.
    this.launch('retail', 'dashboard');
  },
  async logout() {
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
  showSetupModal() {
    document.getElementById('aura-relogin-modal')?.remove();
    this._authModalOpen = true;
    const overlay = document.createElement('div');
    overlay.id = 'aura-relogin-modal';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(2,6,23,0.95);display:flex;align-items:center;justify-content:center;z-index:99999;backdrop-filter:blur(12px);';
    overlay.innerHTML = `
      <div style="position:relative;background:linear-gradient(135deg,#0f172a,#1e293b);border:1px solid rgba(20,184,166,0.3);border-radius:24px;padding:44px;width:480px;box-shadow:0 40px 100px rgba(0,0,0,0.8),0 0 60px rgba(20,184,166,0.08);">
        <button onclick="AuraI18n.toggle()" title="Language / اللغة" style="position:absolute;top:14px;inset-inline-end:14px;background:none;border:1px solid rgba(255,255,255,0.2);color:#94a3b8;border-radius:8px;padding:4px 10px;font-size:12px;cursor:pointer;">EN | ع</button>
        <div style="text-align:center;margin-bottom:32px;">
          <div style="width:72px;height:72px;border-radius:20px;background:linear-gradient(135deg,#14b8a6,#0d9488);display:flex;align-items:center;justify-content:center;font-size:36px;margin:0 auto 16px;box-shadow:0 8px 28px rgba(20,184,166,0.35);">⚡</div>
          <h2 style="color:#fff;margin:0 0 8px;font-size:26px;font-weight:800;letter-spacing:-0.5px;">${t('Welcome to Action Aura')}</h2>
          <p style="color:#64748b;margin:0;font-size:15px;">${t('Create your administrator account to get started.')}</p>
          <p style="color:#94a3b8;margin:8px 0 0;font-size:12px;background:rgba(20,184,166,0.08);border:1px solid rgba(20,184,166,0.2);border-radius:8px;padding:8px;">This setup runs <strong style="color:#14b8a6">only once</strong>. Your credentials will be saved permanently.</p>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px;">
          <div>
            <label style="display:block;color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px;">Full Name *</label>
            <input id="su-name" type="text" placeholder="Your full name" autocomplete="name"
              style="width:100%;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:10px;color:#fff;padding:11px 14px;font-size:14px;outline:none;box-sizing:border-box;transition:.2s;"
              onfocus="this.style.borderColor='#14b8a6'" onblur="this.style.borderColor='rgba(255,255,255,0.1)'"
              onkeydown="if(event.key==='Enter')document.getElementById('su-company').focus()" />
          </div>
          <div>
            <label style="display:block;color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px;">Company Name</label>
            <input id="su-company" type="text" placeholder="Your company" autocomplete="organization"
              style="width:100%;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:10px;color:#fff;padding:11px 14px;font-size:14px;outline:none;box-sizing:border-box;transition:.2s;"
              onfocus="this.style.borderColor='#14b8a6'" onblur="this.style.borderColor='rgba(255,255,255,0.1)'"
              onkeydown="if(event.key==='Enter')document.getElementById('su-email').focus()" />
          </div>
        </div>
        <div style="margin-bottom:14px;">
          <label style="display:block;color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px;">Email Address *</label>
          <input id="su-email" type="email" placeholder="admin@yourcompany.com" autocomplete="email"
            style="width:100%;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:10px;color:#fff;padding:11px 14px;font-size:14px;outline:none;box-sizing:border-box;transition:.2s;"
            onfocus="this.style.borderColor='#14b8a6'" onblur="this.style.borderColor='rgba(255,255,255,0.1)'"
            onkeydown="if(event.key==='Enter')document.getElementById('su-pass').focus()" />
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px;">
          <div>
            <label style="display:block;color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px;">Password *</label>
            <input id="su-pass" type="password" placeholder="Min. 6 characters" autocomplete="new-password"
              style="width:100%;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:10px;color:#fff;padding:11px 14px;font-size:14px;outline:none;box-sizing:border-box;transition:.2s;"
              onfocus="this.style.borderColor='#14b8a6'" onblur="this.style.borderColor='rgba(255,255,255,0.1)'"
              onkeydown="if(event.key==='Enter')document.getElementById('su-pass2').focus()" />
          </div>
          <div>
            <label style="display:block;color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px;">Confirm Password *</label>
            <input id="su-pass2" type="password" placeholder="Repeat password" autocomplete="new-password"
              style="width:100%;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:10px;color:#fff;padding:11px 14px;font-size:14px;outline:none;box-sizing:border-box;transition:.2s;"
              onfocus="this.style.borderColor='#14b8a6'" onblur="this.style.borderColor='rgba(255,255,255,0.1)'"
              onkeydown="if(event.key==='Enter')SubsystemApp._setupSubmit()" />
          </div>
        </div>
        <div id="su-error" style="color:#f87171;font-size:13px;margin-bottom:14px;display:none;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.2);border-radius:8px;padding:10px 14px;"></div>
        <button id="su-btn" onclick="SubsystemApp._setupSubmit()"
          style="width:100%;background:linear-gradient(135deg,#14b8a6,#0d9488);color:#fff;border:none;border-radius:12px;padding:15px;font-size:16px;font-weight:700;cursor:pointer;letter-spacing:.3px;transition:opacity .2s;box-shadow:0 8px 24px rgba(20,184,166,0.3);">
          Create Account &amp; Launch
        </button>
        <p style="text-align:center;color:#475569;font-size:12px;margin:16px 0 0;">Your data is stored locally on this device. No cloud required.</p>
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
        <p style="color:#64748b;margin:0;font-size:16px;">Welcome, <strong id="aura-setup-complete-name" style="color:#14b8a6"></strong>. Loading your platform…</p>`;
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
  showReloginModal(msg = 'Your session has expired. Please log in again.') {
    document.getElementById('aura-relogin-modal')?.remove();
    this._authModalOpen = true;
    const overlay = document.createElement('div');
    overlay.id = 'aura-relogin-modal';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(2,6,23,0.92);display:flex;align-items:center;justify-content:center;z-index:99999;backdrop-filter:blur(10px);';
    overlay.innerHTML = `
      <div style="position:relative;background:linear-gradient(135deg,#0f172a,#1e293b);border:1px solid rgba(255,255,255,0.1);border-radius:24px;padding:44px;width:420px;box-shadow:0 40px 100px rgba(0,0,0,0.8);">
        <button onclick="AuraI18n.toggle()" title="Language / اللغة" style="position:absolute;top:14px;inset-inline-end:14px;background:none;border:1px solid rgba(255,255,255,0.2);color:#94a3b8;border-radius:8px;padding:4px 10px;font-size:12px;cursor:pointer;">EN | ع</button>
        <div style="text-align:center;margin-bottom:32px;">
          <div style="width:64px;height:64px;border-radius:18px;background:linear-gradient(135deg,#1e293b,#0f172a);border:1px solid rgba(20,184,166,0.3);display:flex;align-items:center;justify-content:center;font-size:32px;margin:0 auto 16px;">🔐</div>
          <h2 style="color:#fff;margin:0 0 8px;font-size:24px;font-weight:800;">${t('Sign In Required')}</h2>
          <p style="color:#64748b;margin:0;font-size:14px;">${t(msg)}</p>
        </div>
        <div style="margin-bottom:14px;">
          <label style="display:block;color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px;">${t('Email')}</label>
          <input id="rl-email" type="email" placeholder="admin@yourcompany.com" autocomplete="email"
            style="width:100%;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:10px;color:#fff;padding:11px 14px;font-size:14px;outline:none;box-sizing:border-box;transition:.2s;"
            onfocus="this.style.borderColor='#14b8a6'" onblur="this.style.borderColor='rgba(255,255,255,0.1)'"
            onkeydown="if(event.key==='Enter')document.getElementById('rl-pass').focus()" />
        </div>
        <div style="margin-bottom:22px;">
          <label style="display:block;color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px;">${t('Password')}</label>
          <input id="rl-pass" type="password" placeholder="••••••••" autocomplete="current-password"
            style="width:100%;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:10px;color:#fff;padding:11px 14px;font-size:14px;outline:none;box-sizing:border-box;transition:.2s;"
            onfocus="this.style.borderColor='#14b8a6'" onblur="this.style.borderColor='rgba(255,255,255,0.1)'"
            onkeydown="if(event.key==='Enter')SubsystemApp._reloginSubmit()" />
        </div>
        <div id="rl-error" style="color:#f87171;font-size:13px;margin-bottom:14px;display:none;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.2);border-radius:8px;padding:10px 14px;"></div>
        <button id="rl-btn" onclick="SubsystemApp._reloginSubmit()"
          style="width:100%;background:linear-gradient(135deg,#14b8a6,#0d9488);color:#fff;border:none;border-radius:12px;padding:14px;font-size:16px;font-weight:700;cursor:pointer;box-shadow:0 8px 24px rgba(20,184,166,0.25);transition:opacity .2s;">
          ${t('Log In')}
        </button>
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
          ${sys.nav.filter(item => (!item.roles || this.canClinic(...item.roles)) && (!item.desktopOnly || !/Android/i.test(navigator.userAgent || ''))).map(item => `
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
