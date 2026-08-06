/**
 * Action Aura — Clinic Management Subsystem
 * API base: /api/sub/clinic
 */

const ClinicSystem = {
  _patients: [],
  _services: [],
  _doctors: [],

  // ── Auth-aware fetch wrapper ───────────────────────────────────────────────
  async _fetch(url, opts = {}) {
    opts.credentials = 'include';
    opts.cache = 'no-store';
    const res = await fetch(url, opts);
    if (res.status === 401) {
      const d = await res.json().catch(() => ({}));
      if (window.SubsystemApp && window.SubsystemApp.checkAuthAndSetup) {
        window.SubsystemApp.checkAuthAndSetup(d.error || 'Your session has expired. Please log in again.');
      }
      throw new Error(d.error || 'Session expired');
    }
    if (res.status === 402) {
      const d = await res.json().catch(() => ({}));
      SubsystemApp.showToast(d.error || 'Clinic module not licensed.', 'error');
      throw new Error(d.error || 'Module not licensed');
    }
    return res;
  },

  async _get(url) {
    const res = await this._fetch(url);
    return res.json();
  },

  async _post(url, body) {
    const res = await this._fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return res.json();
  },

  async _patch(url, body) {
    const res = await this._fetch(url, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return res.json();
  },

  render(sectionId) {
    const c = document.getElementById('sub-content');
    if (!c) return;
    switch (sectionId) {
      case 'dashboard':    this._renderDashboard(c); break;
      case 'patients':     this._renderPatients(c);  break;
      case 'appointments': this._renderAppointments(c); break;
      case 'visits':       this._renderVisits(c); break;
      case 'billing':      this._renderBilling(c);   break;
      case 'lab_expenses': this._renderLabExpenses(c); break;
      case 'prescriptions':this._renderPrescriptions(c); break;
      case 'doctors':      this._renderDoctors(c); break;
      case 'reports':      this._renderReports(c); break;
      default:
        c.innerHTML = `<div style="text-align:center;padding:100px;color:var(--text-muted)">
          <h2>${sectionId}</h2><p>Coming soon.</p></div>`;
    }
  },

  // ── Shared styles ────────────────────────────────────────────────────────
  _injectStyles() {
    if (document.getElementById('clinic-styles')) return;
    const s = document.createElement('style');
    s.id = 'clinic-styles';
    s.textContent = `
      .cl-kpi-grid { display:grid; grid-template-columns:repeat(5,1fr); gap:16px; margin-bottom:24px; }
      @media(max-width:1200px){ .cl-kpi-grid { grid-template-columns:repeat(3,1fr); } }
      @media(max-width:768px) { .cl-kpi-grid { grid-template-columns:repeat(2,1fr); } }
      .cl-kpi { background:rgba(0,0,0,0.25); border:1px solid rgba(255,255,255,0.07);
        border-radius:14px; padding:20px; position:relative; overflow:hidden; }
      .cl-kpi::before { content:''; position:absolute; inset:0;
        background:radial-gradient(circle at 80% 20%, var(--cl-accent,#14b8a6), transparent 65%);
        opacity:0.12; }
      .cl-kpi-label { color:var(--text-muted); font-size:12px; text-transform:uppercase;
        letter-spacing:.6px; margin-bottom:8px; }
      .cl-kpi-value { font-size:30px; font-weight:800; color:#fff; }
      .cl-kpi-sub   { font-size:12px; color:var(--text-muted); margin-top:4px; }

      .cl-table { width:100%; border-collapse:collapse; color:#fff; font-size:13px; }
      .cl-table th { color:var(--text-muted); font-weight:500; padding:10px 12px;
        border-bottom:1px solid rgba(255,255,255,0.08); text-align:left; }
      .cl-table td { padding:11px 12px; border-bottom:1px solid rgba(255,255,255,0.04); vertical-align:middle; }
      .cl-table tr:last-child td { border:none; }
      .cl-table tbody tr:hover { background:rgba(255,255,255,0.03); cursor:pointer; }

      .cl-badge { display:inline-block; padding:2px 10px; border-radius:10px; font-size:11px; font-weight:600; }
      .cl-badge-scheduled  { background:rgba(56,189,248,0.15);  color:#38bdf8; }
      .cl-badge-waiting    { background:rgba(251,191,36,0.15);   color:#fbbf24; }
      .cl-badge-in_progress{ background:rgba(168,85,247,0.15);  color:#a855f7; }
      .cl-badge-active     { background:rgba(16,185,129,0.15);   color:#10b981; }
      .cl-badge-completed  { background:rgba(16,185,129,0.15);   color:#10b981; }
      .cl-badge-cancelled  { background:rgba(239,68,68,0.15);    color:#ef4444; }
      .cl-badge-paid       { background:rgba(16,185,129,0.15);   color:#10b981; }
      .cl-badge-unpaid     { background:rgba(239,68,68,0.15);    color:#ef4444; }
      .cl-badge-partial    { background:rgba(251,191,36,0.15);   color:#fbbf24; }
      .cl-badge-no_show    { background:rgba(100,116,139,0.2);   color:#94a3b8; }

      .cl-modal-overlay { position:fixed; inset:0; background:rgba(0,0,0,0.7);
        display:flex; align-items:center; justify-content:center; z-index:9999; backdrop-filter:blur(4px); }
      .cl-modal { background:#0f172a; border:1px solid rgba(255,255,255,0.1);
        border-radius:18px; padding:32px; width:540px; max-height:88vh; overflow-y:auto;
        box-shadow:0 25px 60px rgba(0,0,0,0.6); }
      .cl-modal-wide { width:700px; }
      .cl-modal h3 { color:#fff; margin:0 0 24px; font-size:20px; font-weight:700; }
      .cl-field { margin-bottom:16px; }
      .cl-field label { display:block; color:var(--text-muted); font-size:12px;
        text-transform:uppercase; letter-spacing:.5px; margin-bottom:6px; }
      .cl-field input, .cl-field select, .cl-field textarea {
        width:100%; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1);
        border-radius:8px; color:#fff; padding:10px 14px; font-size:14px; outline:none;
        box-sizing:border-box; font-family:inherit; }
      .cl-field input:focus, .cl-field select:focus, .cl-field textarea:focus {
        border-color:#14b8a6; box-shadow:0 0 0 3px rgba(20,184,166,0.15); }
      .cl-field select option { background:#0f172a; }
      .cl-field-row { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
      .cl-field-row3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; }
      .cl-modal-footer { display:flex; gap:12px; justify-content:flex-end; margin-top:24px; }
      .cl-btn { padding:10px 20px; border-radius:8px; font-weight:600; font-size:14px;
        cursor:pointer; border:none; transition:all .2s; }
      .cl-btn-primary { background:#14b8a6; color:#fff; }
      .cl-btn-primary:hover { background:#0d9488; transform:translateY(-1px); }
      .cl-btn-primary:disabled { opacity:.5; cursor:not-allowed; transform:none; }
      .cl-btn-ghost { background:rgba(255,255,255,0.05); color:#cbd5e1;
        border:1px solid rgba(255,255,255,0.1); }
      .cl-btn-ghost:hover { background:rgba(255,255,255,0.1); }
      .cl-btn-danger { background:rgba(239,68,68,0.15); color:#ef4444;
        border:1px solid rgba(239,68,68,0.3); }
      .cl-btn-danger:hover { background:rgba(239,68,68,0.25); }
      .cl-btn-sm { padding:4px 12px; font-size:12px; }
      .cl-search { background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1);
        border-radius:8px; color:#fff; padding:9px 14px; font-size:14px; outline:none; width:260px; }
      .cl-search:focus { border-color:#14b8a6; }
      .cl-section-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:22px; }
      .cl-section-title { color:#fff; margin:0; font-size:24px; font-weight:700; }
      .cl-empty { text-align:center; color:var(--text-muted); padding:40px; }
      .cl-detail-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:20px; }
      .cl-detail-item label { display:block; color:var(--text-muted); font-size:11px; text-transform:uppercase; letter-spacing:.4px; margin-bottom:3px; }
      .cl-detail-item span { color:#fff; font-size:14px; }
      .cl-tab-bar { display:flex; gap:4px; margin-bottom:20px; border-bottom:1px solid rgba(255,255,255,0.08); padding-bottom:12px; }
      .cl-tab { padding:7px 18px; border-radius:8px; font-size:13px; font-weight:600; cursor:pointer;
        border:none; background:transparent; color:var(--text-muted); transition:.2s; }
      .cl-tab.active { background:rgba(20,184,166,0.15); color:#14b8a6; }
      .cl-tab:hover:not(.active) { background:rgba(255,255,255,0.05); color:#fff; }
    `;
    document.head.appendChild(s);
    document.documentElement.style.setProperty('--cl-accent', '#14b8a6');
  },

  _badge(status) {
    return `<span class="cl-badge cl-badge-${(status||'').replace(/ /g,'_')}">${(status||'').replace(/_/g,' ')}</span>`;
  },

  // ── Dashboard ────────────────────────────────────────────────────────────
  async _renderDashboard(c) {
    this._injectStyles();
    const today = new Date().toISOString().slice(0,10);
    c.innerHTML = `
      <div class="cl-section-header">
        <div>
          <h2 class="cl-section-title">${t('Clinic Overview')}</h2>
          <p style="color:var(--text-muted);margin:4px 0 0">${new Date().toLocaleDateString('en-US',{weekday:'long',year:'numeric',month:'long',day:'numeric'})}</p>
        </div>
        <div style="display:flex;gap:10px">
          <button class="sub-btn-primary" onclick="ClinicSystem._openBookModal()">+ Book Appointment</button>
          <button class="sub-btn-primary" onclick="ClinicSystem._openAddPatient()" style="background:rgba(20,184,166,0.25);border:1px solid rgba(20,184,166,0.5)">+ Add Patient</button>
        </div>
      </div>
      <div class="cl-kpi-grid">
        <div class="cl-kpi"><div class="cl-kpi-label">Today's Appointments</div><div class="cl-kpi-value" id="cl-k-appts">—</div><div class="cl-kpi-sub">Scheduled today</div></div>
        <div class="cl-kpi"><div class="cl-kpi-label">Waiting Room</div><div class="cl-kpi-value" id="cl-k-waiting" style="color:#fbbf24">—</div><div class="cl-kpi-sub">Checked in</div></div>
        <div class="cl-kpi"><div class="cl-kpi-label">Active Visits</div><div class="cl-kpi-value" id="cl-k-active" style="color:#a855f7">—</div><div class="cl-kpi-sub">In consultation</div></div>
        <div class="cl-kpi"><div class="cl-kpi-label">Today's Revenue</div><div class="cl-kpi-value" id="cl-k-rev" style="color:#14b8a6">—</div><div class="cl-kpi-sub">Payments collected</div></div>
        <div class="cl-kpi"><div class="cl-kpi-label">Total Patients</div><div class="cl-kpi-value" id="cl-k-pts">—</div><div class="cl-kpi-sub">All time</div></div>
      </div>
      <div class="sub-chart-card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
          <div class="sub-chart-title">Today's Appointments</div>
          <button class="cl-btn cl-btn-primary cl-btn-sm" onclick="SubsystemApp._navigate('appointments')">View All →</button>
        </div>
        <div style="overflow-x:auto">
          <table class="cl-table" id="cl-dash-appts">
            <thead><tr><th>Time</th><th>Patient</th><th>Reason</th><th>Status</th><th>Action</th></tr></thead>
            <tbody><tr><td colspan="5" class="cl-empty">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>
      <div id="clinic-recent-activity" style="margin-top:20px"></div>`;

    try {
      const [statsData, apptData] = await Promise.all([
        this._get('/api/sub/clinic/dashboard/stats'),
        this._get(`/api/sub/clinic/appointments?date=${today}`),
      ]);
      const stats = statsData.data || {};
      const appts = apptData.data || [];

      document.getElementById('cl-k-appts').textContent   = stats.today_appointments ?? '0';
      document.getElementById('cl-k-waiting').textContent = stats.waiting            ?? '0';
      document.getElementById('cl-k-active').textContent  = stats.active_visits      ?? '0';
      document.getElementById('cl-k-rev').textContent     = '$' + (+(stats.today_revenue || 0)).toFixed(2);
      document.getElementById('cl-k-pts').textContent     = stats.total_patients     ?? '0';

      const tbody = document.querySelector('#cl-dash-appts tbody');
      if (appts.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="cl-empty">No appointments today.</td></tr>`;
      } else {
        tbody.innerHTML = appts.map(a => `
          <tr>
            <td style="font-family:monospace;color:#38bdf8">${new Date(a.appointment_dt).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</td>
            <td>${a.patient_name || 'Unknown'}</td>
            <td style="color:var(--text-muted)">${a.reason || '—'}</td>
            <td>${this._badge(a.status)}</td>
            <td>
              ${a.status === 'scheduled' ? `<button class="cl-btn cl-btn-ghost cl-btn-sm" onclick="ClinicSystem._checkin(${a.id},this)">Check-in</button>` : '—'}
            </td>
          </tr>`).join('');
      }
    } catch (e) {
      console.warn('Clinic dashboard error:', e.message);
    }

    // Load recent audit activity
    this._loadRecentActivity();
  },

  async _loadRecentActivity() {
    try {
      const data = (await this._get('/api/sub/clinic/reports/audit')).data || [];
      const area = document.getElementById('clinic-recent-activity');
      if (!area || data.length === 0) return;
      area.innerHTML = `
        <div class="sub-chart-card">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
            <div class="sub-chart-title">Recent Activity</div>
            <span style="font-size:12px;color:var(--text-muted)">${data.length} events</span>
          </div>
          <table class="cl-table">
            <thead><tr><th>Time</th><th>Action</th><th>Entity</th><th>By</th></tr></thead>
            <tbody>
              ${data.slice(0,10).map(r => `<tr>
                <td style="font-family:monospace;color:var(--text-muted);font-size:12px">${r.timestamp ? r.timestamp.slice(0,16).replace('T',' ') : '—'}</td>
                <td style="color:#14b8a6;font-size:12px">${r.action||'—'}</td>
                <td style="color:var(--text-muted);font-size:12px">${r.entity||'—'} #${r.entity_id||''}</td>
                <td style="color:var(--text-muted);font-size:12px">${(r.user_id||'system').slice(0,8)}…</td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>`;
    } catch(e) { /* non-critical */ }
  },


  // ── Patients ─────────────────────────────────────────────────────────────
  async _renderPatients(c) {
    this._injectStyles();
    c.innerHTML = `
      <div class="cl-section-header">
        <h2 class="cl-section-title">Patient Registry</h2>
        <div style="display:flex;gap:12px">
          <input class="cl-search" id="cl-pt-search" placeholder="Search name / phone / code…" oninput="ClinicSystem._searchPatients()" />
          <button class="cl-btn cl-btn-ghost" onclick="ImportWizard.open('clinic','patients',()=>ClinicSystem._loadPatients())">⬆ Import</button>
          <button class="sub-btn-primary" onclick="ClinicSystem._openAddPatient()">+ Add Patient</button>
        </div>
      </div>
      <div class="sub-chart-card">
        <div style="overflow-x:auto">
          <table class="cl-table" id="cl-pt-table">
            <thead><tr><th>Code</th><th>Name</th><th>Gender</th><th>DOB</th><th>Phone</th><th>Blood Type</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody><tr><td colspan="8" class="cl-empty">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>`;
    await this._loadPatients();
  },

  async _loadPatients(q = '') {
    try {
      const url = q ? `/api/sub/clinic/patients?q=${encodeURIComponent(q)}` : '/api/sub/clinic/patients';
      const data = (await this._get(url)).data || [];
      this._patients = data;
      const tbody = document.querySelector('#cl-pt-table tbody');
      if (!tbody) return;
      if (data.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="cl-empty">No patients found.</td></tr>`;
        return;
      }
      tbody.innerHTML = data.map(p => `
        <tr onclick="ClinicSystem._openPatientDetail(${p.id})" title="View patient file">
          <td style="font-family:monospace;color:#14b8a6">${p.patient_code}</td>
          <td style="font-weight:600">${p.name}</td>
          <td style="color:var(--text-muted)">${p.gender || '—'}</td>
          <td style="color:var(--text-muted)">${p.dob || '—'}</td>
          <td>${p.phone || '—'}</td>
          <td><span class="cl-badge cl-badge-active">${p.blood_type || 'N/A'}</span></td>
          <td>${this._badge(p.status || 'active')}</td>
          <td onclick="event.stopPropagation()">
            <button class="cl-btn cl-btn-ghost cl-btn-sm" style="margin-right:6px"
              onclick="ClinicSystem._openBookModal(${p.id}, '${p.name.replace(/'/g,"\\'")}')">Book</button>
            <button class="cl-btn cl-btn-primary cl-btn-sm" style="margin-right:6px"
              onclick="ClinicSystem._openPatientDetail(${p.id})">File</button>
            <button class="cl-btn cl-btn-danger cl-btn-sm"
              onclick="ClinicSystem._deletePatient(${p.id}, '${p.name.replace(/'/g,"\\'")}')">Delete</button>
          </td>
        </tr>`).join('');
    } catch (e) { console.error(e); }
  },

  _searchPatients() {
    const q = document.getElementById('cl-pt-search')?.value || '';
    clearTimeout(this._searchTimer);
    this._searchTimer = setTimeout(() => this._loadPatients(q), 350);
  },

  async _deletePatient(pid, name) {
    if (!confirm(`Archive patient "${name}"?\n\nThe record is hidden but kept (medical & billing history is preserved). This can be reversed by an admin.`)) return;
    try {
      const d = await this._fetch(`/api/sub/clinic/patients/${pid}`, { method: 'DELETE' });
      const r = await d.json().catch(() => ({}));
      if (d.ok) {
        SubsystemApp.showToast(r.message || 'Patient archived', 'success');
        this._loadPatients(document.getElementById('cl-pt-search')?.value || '');
      } else {
        SubsystemApp.showToast(r.message || r.error || 'Could not delete patient', 'error');
      }
    } catch (e) { console.error(e); }
  },

  _openAddPatient() {
    const overlay = document.createElement('div');
    overlay.className = 'cl-modal-overlay';
    overlay.id = 'cl-add-pt-overlay';
    overlay.innerHTML = `
      <div class="cl-modal">
        <h3>➕ Add New Patient</h3>
        <div class="cl-field-row">
          <div class="cl-field"><label>Full Name *</label><input id="cl-pt-name" placeholder="John Doe" /></div>
          <div class="cl-field"><label>Gender</label>
            <select id="cl-pt-gender"><option value="">Select…</option><option>Male</option><option>Female</option><option>Other</option></select></div>
        </div>
        <div class="cl-field-row">
          <div class="cl-field"><label>Date of Birth</label><input type="date" id="cl-pt-dob" /></div>
          <div class="cl-field"><label>Blood Type</label>
            <select id="cl-pt-blood"><option value="">Unknown</option>
              <option>A+</option><option>A-</option><option>B+</option><option>B-</option>
              <option>AB+</option><option>AB-</option><option>O+</option><option>O-</option></select></div>
        </div>
        <div class="cl-field-row">
          <div class="cl-field"><label>Phone</label><input id="cl-pt-phone" placeholder="+1 555 000 0000" /></div>
          <div class="cl-field"><label>Email</label><input id="cl-pt-email" placeholder="patient@email.com" /></div>
        </div>
        <div class="cl-field"><label>Address</label><input id="cl-pt-addr" placeholder="Street, City, Country" /></div>
        <div class="cl-field-row">
          <div class="cl-field"><label>Emergency Contact</label><input id="cl-pt-ec" placeholder="Contact name" /></div>
          <div class="cl-field"><label>Emergency Phone</label><input id="cl-pt-ep" placeholder="+1 555 000 0001" /></div>
        </div>
        <div class="cl-field"><label>Notes (Allergies, conditions…)</label>
          <textarea id="cl-pt-notes" rows="2" placeholder="Allergies, chronic conditions…"></textarea></div>
        <div class="cl-modal-footer">
          <button class="cl-btn cl-btn-ghost" onclick="document.getElementById('cl-add-pt-overlay').remove()">Cancel</button>
          <button class="cl-btn cl-btn-primary" id="cl-pt-save-btn" onclick="ClinicSystem._savePatient()">Save Patient</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    document.getElementById('cl-pt-name')?.focus();
  },

  async _savePatient() {
    const name = document.getElementById('cl-pt-name')?.value.trim();
    if (!name) { SubsystemApp.showToast('Name is required', 'error'); return; }
    const btn = document.getElementById('cl-pt-save-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    const payload = {
      name,
      gender:            document.getElementById('cl-pt-gender')?.value,
      dob:               document.getElementById('cl-pt-dob')?.value,
      blood_type:        document.getElementById('cl-pt-blood')?.value,
      phone:             document.getElementById('cl-pt-phone')?.value,
      email:             document.getElementById('cl-pt-email')?.value,
      address:           document.getElementById('cl-pt-addr')?.value,
      emergency_contact: document.getElementById('cl-pt-ec')?.value,
      emergency_phone:   document.getElementById('cl-pt-ep')?.value,
      notes:             document.getElementById('cl-pt-notes')?.value,
    };
    try {
      const data = await this._post('/api/sub/clinic/patients', payload);
      if (data.status === 'success') {
        SubsystemApp.showToast(`Patient created — ${data.data.patient_code}`, 'success');
        document.getElementById('cl-add-pt-overlay')?.remove();
        this._loadPatients();
      } else {
        SubsystemApp.showToast(data.message || data.error || 'Error saving patient', 'error');
        if (btn) { btn.disabled = false; btn.textContent = 'Save Patient'; }
      }
    } catch (e) {
      if (btn) { btn.disabled = false; btn.textContent = 'Save Patient'; }
    }
  },

  // ── Patient Detail Modal ──────────────────────────────────────────────────
  async _openPatientDetail(pid) {
    const overlay = document.createElement('div');
    overlay.className = 'cl-modal-overlay';
    overlay.id = 'cl-pt-detail-overlay';
    overlay.innerHTML = `
      <div class="cl-modal cl-modal-wide">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
          <h3 style="margin:0">Patient File</h3>
          <button class="cl-btn cl-btn-ghost cl-btn-sm" onclick="document.getElementById('cl-pt-detail-overlay').remove()">✕ Close</button>
        </div>
        <div id="cl-pt-detail-content"><p class="cl-empty">Loading…</p></div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

    try {
      const d = (await this._get(`/api/sub/clinic/patients/${pid}`)).data || {};
      const p = d.patient || {};
      const visits = d.visits || [];
      const appts = d.appointments || [];

      document.getElementById('cl-pt-detail-content').innerHTML = `
        <div class="cl-detail-grid">
          <div class="cl-detail-item"><label>Code</label><span style="color:#14b8a6;font-family:monospace">${p.patient_code}</span></div>
          <div class="cl-detail-item"><label>Name</label><span style="font-weight:700">${p.name}</span></div>
          <div class="cl-detail-item"><label>Gender</label><span>${p.gender || '—'}</span></div>
          <div class="cl-detail-item"><label>Date of Birth</label><span>${p.dob || '—'}</span></div>
          <div class="cl-detail-item"><label>Phone</label><span>${p.phone || '—'}</span></div>
          <div class="cl-detail-item"><label>Email</label><span>${p.email || '—'}</span></div>
          <div class="cl-detail-item"><label>Blood Type</label><span>${this._badge(p.blood_type || 'N/A')}</span></div>
          <div class="cl-detail-item"><label>Status</label><span>${this._badge(p.status || 'active')}</span></div>
          <div class="cl-detail-item" style="grid-column:1/-1"><label>Address</label><span>${p.address || '—'}</span></div>
          <div class="cl-detail-item"><label>Emergency Contact</label><span>${p.emergency_contact || '—'}</span></div>
          <div class="cl-detail-item"><label>Emergency Phone</label><span>${p.emergency_phone || '—'}</span></div>
          ${p.notes ? `<div class="cl-detail-item" style="grid-column:1/-1"><label>Notes</label><span style="color:#fbbf24">${p.notes}</span></div>` : ''}
        </div>
        <div style="display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap">
          <button class="cl-btn cl-btn-primary cl-btn-sm" onclick="ClinicSystem._openBookModal(${p.id},'${(p.name||'').replace(/'/g,"\\'")}');document.getElementById('cl-pt-detail-overlay').remove()">📅 Book Appointment</button>
          <button class="cl-btn cl-btn-ghost cl-btn-sm" onclick="ClinicSystem._openInvoiceModal(${p.id});document.getElementById('cl-pt-detail-overlay').remove()">🧾 New Invoice</button>
          <button class="cl-btn cl-btn-ghost cl-btn-sm" onclick="ClinicSystem._openPrescModalForPatient(${p.id});document.getElementById('cl-pt-detail-overlay').remove()">💊 New Prescription</button>
        </div>
        <div class="cl-tab-bar" id="cl-pt-tabs">
          <button class="cl-tab active" onclick="ClinicSystem._ptTab(this,'visits-${pid}')">Visits (${visits.length})</button>
          <button class="cl-tab" onclick="ClinicSystem._ptTab(this,'appts-${pid}')">Appointments (${appts.length})</button>
          <button class="cl-tab" onclick="ClinicSystem._ptTab(this,'followups-${pid}');ClinicSystem._loadFollowups(${pid})">Follow-up Sheet</button>
        </div>
        <div id="cl-pt-tab-followups-${pid}" style="display:none">
          ${SubsystemApp.canClinic('doctor') ? `
          <div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:14px;margin-bottom:14px">
            <div class="cl-field-row">
              <div class="cl-field"><label>Date</label><input type="date" id="cl-fu-date-${pid}" value="${new Date().toISOString().slice(0,10)}" /></div>
              <div class="cl-field"><label>Weight</label><input id="cl-fu-weight-${pid}" placeholder="e.g. 72 kg" /></div>
            </div>
            <div class="cl-field-row">
              <div class="cl-field"><label>Blood Pressure</label><input id="cl-fu-bp-${pid}" placeholder="e.g. 120/80" /></div>
              <div class="cl-field"><label>Temperature</label><input id="cl-fu-temp-${pid}" placeholder="e.g. 37.0°C" /></div>
            </div>
            <div class="cl-field"><label>Progress</label><input id="cl-fu-progress-${pid}" placeholder="Clinical progress…" /></div>
            <div class="cl-field"><label>Notes</label><textarea id="cl-fu-notes-${pid}" rows="2" placeholder="Notes…"></textarea></div>
            <div style="text-align:right"><button class="cl-btn cl-btn-primary cl-btn-sm" onclick="ClinicSystem._addFollowup(${pid})">+ Add Entry</button></div>
          </div>` : '<p class="cl-empty" style="margin-bottom:12px">Follow-up entries are added by the doctor.</p>'}
          <div id="cl-fu-list-${pid}"><p class="cl-empty">Loading…</p></div>
        </div>
        <div id="cl-pt-tab-visits-${pid}">
          ${visits.length === 0 ? '<p class="cl-empty">No visits yet.</p>' : `
          <table class="cl-table"><thead><tr><th>#</th><th>Date</th><th>Doctor</th><th>Status</th><th>Diagnosis</th></tr></thead><tbody>
          ${visits.map(v=>`<tr>
            <td style="font-family:monospace;color:#a855f7">#${v.id}</td>
            <td>${v.created_at?v.created_at.slice(0,10):'—'}</td>
            <td>Dr. #${v.doctor_id||'—'}</td>
            <td>${this._badge(v.status)}</td>
            <td style="color:var(--text-muted)">${v.diagnosis||'—'}</td>
          </tr>`).join('')}
          </tbody></table>`}
        </div>
        <div id="cl-pt-tab-appts-${pid}" style="display:none">
          ${appts.length === 0 ? '<p class="cl-empty">No appointments yet.</p>' : `
          <table class="cl-table"><thead><tr><th>Date/Time</th><th>Doctor</th><th>Reason</th><th>Status</th></tr></thead><tbody>
          ${appts.map(a=>`<tr>
            <td style="font-family:monospace;color:#38bdf8">${new Date(a.appointment_dt).toLocaleString([],{dateStyle:'short',timeStyle:'short'})}</td>
            <td>Dr. #${a.doctor_id||'—'}</td>
            <td>${a.reason||'—'}</td>
            <td>${this._badge(a.status)}</td>
          </tr>`).join('')}
          </tbody></table>`}
        </div>`;
    } catch (e) {
      document.getElementById('cl-pt-detail-content').innerHTML = `<p class="cl-empty" style="color:#ef4444">Failed to load patient data.</p>`;
    }
  },

  _ptTab(btn, id) {
    btn.closest('.cl-tab-bar').querySelectorAll('.cl-tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    const pid = id.split('-').pop();
    ['visits','appts','followups'].forEach(t => {
      const el = document.getElementById(`cl-pt-tab-${t}-${pid}`);
      if (el) el.style.display = 'none';
    });
    const target = document.getElementById(`cl-pt-tab-${id}`);
    if (target) target.style.display = '';
  },

  async _loadFollowups(pid) {
    const wrap = document.getElementById(`cl-fu-list-${pid}`);
    if (!wrap) return;
    try {
      const data = (await this._get(`/api/sub/clinic/patients/${pid}/followups`)).data || [];
      if (!data.length) { wrap.innerHTML = '<p class="cl-empty">No follow-up entries yet.</p>'; return; }
      wrap.innerHTML = `<table class="cl-table"><thead><tr><th>Date</th><th>Weight</th><th>BP</th><th>Temp</th><th>Progress</th><th>Notes</th>${SubsystemApp.canClinic('doctor') ? '<th></th>' : ''}</tr></thead><tbody>
        ${data.map(f => `<tr>
          <td style="color:#38bdf8">${f.followup_date || (f.created_at||'').slice(0,10)}</td>
          <td>${f.weight || '—'}</td><td>${f.blood_pressure || '—'}</td><td>${f.temperature || '—'}</td>
          <td>${f.progress || '—'}</td><td style="color:var(--text-muted)">${f.notes || '—'}</td>
          ${SubsystemApp.canClinic('doctor') ? `<td><button class="cl-btn cl-btn-danger cl-btn-sm" onclick="ClinicSystem._deleteFollowup(${f.id},${pid})">✕</button></td>` : ''}
        </tr>`).join('')}</tbody></table>`;
    } catch (e) { wrap.innerHTML = '<p class="cl-empty" style="color:#ef4444">Failed to load follow-ups.</p>'; }
  },

  async _addFollowup(pid) {
    const g = id => document.getElementById(`cl-fu-${id}-${pid}`)?.value || '';
    const payload = {
      followup_date: g('date'), weight: g('weight'), blood_pressure: g('bp'),
      temperature: g('temp'), progress: g('progress'), notes: g('notes'),
    };
    try {
      const d = await this._post(`/api/sub/clinic/patients/${pid}/followups`, payload);
      if (d.status === 'success') {
        SubsystemApp.showToast('Follow-up entry added', 'success');
        ['weight','bp','temp','progress','notes'].forEach(k => { const el = document.getElementById(`cl-fu-${k}-${pid}`); if (el) el.value = ''; });
        this._loadFollowups(pid);
      } else {
        SubsystemApp.showToast(d.message || d.error || 'Could not add entry', 'error');
      }
    } catch (e) { console.error(e); }
  },

  async _deleteFollowup(fid, pid) {
    if (!confirm('Delete this follow-up entry?')) return;
    try {
      const d = await this._fetch(`/api/sub/clinic/followups/${fid}`, { method: 'DELETE' });
      if (d.ok) { SubsystemApp.showToast('Entry deleted', 'success'); this._loadFollowups(pid); }
    } catch (e) { console.error(e); }
  },

  _openPrescModalForPatient(patientId) {
    this._patients = this._patients.length ? this._patients : [];
    this._openPrescModal(patientId);
  },

  // ── Appointments ──────────────────────────────────────────────────────────
  async _renderAppointments(c) {
    this._injectStyles();
    const today = new Date().toISOString().slice(0,10);
    c.innerHTML = `
      <div class="cl-section-header">
        <h2 class="cl-section-title">Appointments</h2>
        <div style="display:flex;gap:12px;align-items:center">
          <input class="cl-search" id="cl-appt-search" placeholder="🔍 Search patient / phone / reason…"
            oninput="ClinicSystem._searchAppointments()" />
          <input type="date" id="cl-appt-date" value="${today}"
            style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:8px;color:#fff;padding:8px 12px;outline:none"
            onchange="ClinicSystem._loadAppointments(this.value)" />
          <button class="sub-btn-primary" onclick="ClinicSystem._openBookModal()">+ Book</button>
        </div>
      </div>
      <div class="sub-chart-card">
        <div style="overflow-x:auto">
          <table class="cl-table" id="cl-appt-table">
            <thead><tr><th>Time</th><th>Patient</th><th>Phone</th><th>Reason</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody><tr><td colspan="6" class="cl-empty">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>`;
    await this._loadAppointments(today);
  },

  _searchAppointments() {
    const q = document.getElementById('cl-appt-search')?.value || '';
    clearTimeout(this._apptSearchTimer);
    this._apptSearchTimer = setTimeout(() => {
      if (q.trim()) {
        this._loadAppointments(null, q.trim());
      } else {
        this._loadAppointments(document.getElementById('cl-appt-date')?.value || new Date().toISOString().slice(0,10));
      }
    }, 350);
  },

  async _loadAppointments(date, q = '') {
    try {
      const url = q
        ? `/api/sub/clinic/appointments?q=${encodeURIComponent(q)}`
        : `/api/sub/clinic/appointments?date=${date}`;
      const data = (await this._get(url)).data || [];
      const tbody = document.querySelector('#cl-appt-table tbody');
      if (!tbody) return;
      if (data.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="cl-empty">${q ? 'No matching appointments.' : 'No appointments for this date.'}</td></tr>`;
        return;
      }
      tbody.innerHTML = data.map(a => `
        <tr>
          <td style="font-family:monospace;color:#38bdf8">${q ? new Date(a.appointment_dt).toLocaleString([],{dateStyle:'short',timeStyle:'short'}) : new Date(a.appointment_dt).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</td>
          <td style="font-weight:600">${a.patient_name||'—'}</td>
          <td style="color:var(--text-muted)">${a.patient_phone||'—'}</td>
          <td style="color:var(--text-muted)">${a.reason||'—'}</td>
          <td>${this._badge(a.status)}</td>
          <td style="display:flex;gap:6px;flex-wrap:wrap">
            ${a.status==='scheduled'?`<button class="cl-btn cl-btn-ghost cl-btn-sm" onclick="ClinicSystem._checkin(${a.id},this)">✓ Check-in</button>`:''}
            ${(a.status==='waiting'||a.status==='in_progress')?`<button class="cl-btn cl-btn-primary cl-btn-sm" onclick="ClinicSystem._startVisit(${a.id},${a.patient_id},${a.doctor_id||1})">Start Visit</button>`:''}
            ${(a.status==='waiting'||a.status==='in_progress')?`<button class="cl-btn cl-btn-ghost cl-btn-sm" onclick="ClinicSystem._completeAppt(${a.id},this)">Complete</button>`:''}
            ${a.status!=='cancelled'&&a.status!=='completed'?`<button class="cl-btn cl-btn-danger cl-btn-sm" onclick="ClinicSystem._cancelAppt(${a.id},this)">Cancel</button>`:''}
          </td>
        </tr>`).join('');
    } catch(e) { console.error(e); }
  },

  async _checkin(aid, btn) {
    if (btn) { btn.disabled=true; btn.textContent='…'; }
    try {
      const d = await this._post(`/api/sub/clinic/appointments/${aid}/checkin`, {});
      if (d.status==='success') {
        SubsystemApp.showToast('Patient checked in — now waiting', 'success');
        const date = document.getElementById('cl-appt-date')?.value || new Date().toISOString().slice(0,10);
        this._loadAppointments(date);
      } else {
        SubsystemApp.showToast(d.message||'Check-in failed','error');
        if(btn){btn.disabled=false;btn.textContent='✓ Check-in';}
      }
    } catch(e) { if(btn){btn.disabled=false;btn.textContent='✓ Check-in';} }
  },

  async _cancelAppt(aid, btn) {
    if (!confirm('Cancel this appointment?')) return;
    if (btn) btn.disabled=true;
    try {
      await this._patch(`/api/sub/clinic/appointments/${aid}`, {status:'cancelled'});
      SubsystemApp.showToast('Appointment cancelled','success');
      const date = document.getElementById('cl-appt-date')?.value||new Date().toISOString().slice(0,10);
      this._loadAppointments(date);
    } catch(e) { if(btn) btn.disabled=false; }
  },

  async _completeAppt(aid, btn) {
    if (btn) btn.disabled=true;
    try {
      await this._patch(`/api/sub/clinic/appointments/${aid}`, {status:'completed'});
      SubsystemApp.showToast('Appointment completed','success');
      const date = document.getElementById('cl-appt-date')?.value||new Date().toISOString().slice(0,10);
      this._loadAppointments(date);
    } catch(e) { if(btn) btn.disabled=false; }
  },

  async _startVisit(aid, patientId, doctorId = 1) {
    try {
      const d = await this._post('/api/sub/clinic/visits', {
        patient_id: patientId, doctor_id: doctorId, appointment_id: aid
      });
      if (d.status==='success') {
        SubsystemApp.showToast('Visit started','success');
        SubsystemApp._navigate('visits');
      } else {
        SubsystemApp.showToast(d.message||'Could not start visit','error');
      }
    } catch(e) {}
  },

  _openBookModal(patientId = null, patientName = '') {
    if (!this._patients.length) {
      this._get('/api/sub/clinic/patients').then(d => {
        this._patients = d.data || [];
        this._openBookModal(patientId, patientName);
      }).catch(() => {});
      return;
    }
    const overlay = document.createElement('div');
    overlay.className = 'cl-modal-overlay';
    overlay.id = 'cl-book-overlay';
    const ptOptions = this._patients.map(p =>
      `<option value="${p.id}" ${p.id===patientId?'selected':''}>${p.name} (${p.patient_code})</option>`).join('');
    const now = new Date();
    now.setMinutes(Math.ceil(now.getMinutes()/15)*15, 0, 0);
    const dtLocal = new Date(now.getTime() - now.getTimezoneOffset()*60000).toISOString().slice(0,16);
    const docOptions = this._doctors.map(d =>
      `<option value="${d.id}">${d.name} — ${d.specialty||'General'}</option>`).join('');
    overlay.innerHTML = `
      <div class="cl-modal">
        <h3>📅 Book Appointment</h3>
        <div class="cl-field"><label>Patient *</label>
          <select id="cl-bk-patient"><option value="">Select patient…</option>${ptOptions}</select></div>
        <div class="cl-field-row">
          <div class="cl-field"><label>Date &amp; Time *</label><input type="datetime-local" id="cl-bk-dt" value="${dtLocal}" /></div>
          <div class="cl-field"><label>Doctor</label>
            <select id="cl-bk-doc">${docOptions||'<option value="1">Doctor #1</option>'}</select></div>
        </div>
        <div class="cl-field"><label>Reason for Visit</label>
          <input id="cl-bk-reason" placeholder="e.g. Annual checkup, fever, follow-up…" /></div>
        <div class="cl-modal-footer">
          <button class="cl-btn cl-btn-ghost" onclick="document.getElementById('cl-book-overlay').remove()">Cancel</button>
          <button class="cl-btn cl-btn-primary" id="cl-bk-save" onclick="ClinicSystem._saveAppointment()">Book Appointment</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target===overlay) overlay.remove(); });
    // Pre-load doctors list for the dropdown
    if (!this._doctors.length) {
      this._get('/api/sub/clinic/doctors').then(d => {
        this._doctors = d.data || [];
        const sel = document.getElementById('cl-bk-doc');
        if (sel && this._doctors.length) {
          sel.innerHTML = this._doctors.map(doc =>
            `<option value="${doc.id}">${doc.name} — ${doc.specialty||'General'}</option>`).join('');
        }
      }).catch(() => {});
    }
  },

  async _saveAppointment() {
    const patient_id     = document.getElementById('cl-bk-patient')?.value;
    const appointment_dt = document.getElementById('cl-bk-dt')?.value;
    const doctor_id      = document.getElementById('cl-bk-doc')?.value || 1;
    const reason         = document.getElementById('cl-bk-reason')?.value;
    if (!patient_id || !appointment_dt) { SubsystemApp.showToast('Patient and date/time required','error'); return; }
    const btn = document.getElementById('cl-bk-save');
    if (btn) { btn.disabled=true; btn.textContent='Booking…'; }
    try {
      const data = await this._post('/api/sub/clinic/appointments', {
        patient_id:+patient_id, appointment_dt, doctor_id:+doctor_id, reason
      });
      if (data.status==='success') {
        SubsystemApp.showToast('Appointment booked!','success');
        document.getElementById('cl-book-overlay')?.remove();
      } else {
        SubsystemApp.showToast(data.message||data.error||'Booking failed','error');
        if(btn){btn.disabled=false;btn.textContent='Book Appointment';}
      }
    } catch(e) { if(btn){btn.disabled=false;btn.textContent='Book Appointment';} }
  },

  // ── Visits / Consultations ────────────────────────────────────────────────
  async _renderVisits(c) {
    this._injectStyles();
    c.innerHTML = `
      <div class="cl-section-header">
        <h2 class="cl-section-title">Active Consultations</h2>
      </div>
      <div class="sub-chart-card">
        <div style="overflow-x:auto">
          <table class="cl-table" id="cl-visit-table">
            <thead><tr><th>#</th><th>Patient</th><th>Doctor</th><th>Started</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody><tr><td colspan="6" class="cl-empty">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>`;
    await this._loadVisits();
  },

  async _loadVisits() {
    try {
      // Load today's active visits via appointments that are in_progress or waiting
      const today = new Date().toISOString().slice(0,10);
      const appts = (await this._get(`/api/sub/clinic/appointments?date=${today}`)).data || [];
      const activeAppts = appts.filter(a => a.status==='waiting'||a.status==='in_progress'||a.status==='completed');
      const tbody = document.querySelector('#cl-visit-table tbody');
      if (!tbody) return;
      if (activeAppts.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="cl-empty">No active consultations today.</td></tr>`;
        return;
      }
      tbody.innerHTML = activeAppts.map(a => `
        <tr>
          <td style="font-family:monospace;color:#a855f7">#${a.id}</td>
          <td style="font-weight:600">${a.patient_name||'—'}</td>
          <td>Dr. #${a.doctor_id||'1'}</td>
          <td style="color:var(--text-muted)">${a.checked_in_at?new Date(a.checked_in_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'—'}</td>
          <td>${this._badge(a.status)}</td>
          <td style="display:flex;gap:6px">
            ${a.status==='waiting'?`<button class="cl-btn cl-btn-primary cl-btn-sm" onclick="ClinicSystem._startVisit(${a.id},${a.patient_id},${a.doctor_id||1})">Start</button>`:''}
            <button class="cl-btn cl-btn-ghost cl-btn-sm" onclick="ClinicSystem._openPatientDetail(${a.patient_id})">View Patient</button>
          </td>
        </tr>`).join('');
    } catch(e) { console.error(e); }
  },

  // ── Doctors ───────────────────────────────────────────────────────────────
  async _renderDoctors(c) {
    this._injectStyles();
    c.innerHTML = `
      <div class="cl-section-header">
        <h2 class="cl-section-title">Doctors & Staff</h2>
        <div style="display:flex;gap:10px">
          <button class="cl-btn cl-btn-ghost" onclick="ImportWizard.open('clinic','doctors',()=>ClinicSystem._loadDoctors())">⬆ Import</button>
          <button class="sub-btn-primary" onclick="ClinicSystem._openAddDoctor()">+ Add Doctor</button>
        </div>
      </div>
      <div class="sub-chart-card">
        <div style="overflow-x:auto">
          <table class="cl-table" id="cl-doc-table">
            <thead><tr><th>ID</th><th>Name</th><th>Specialty</th><th>Phone</th><th>Email</th><th>Status</th></tr></thead>
            <tbody><tr><td colspan="6" class="cl-empty">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>`;
    await this._loadDoctors();
  },

  async _loadDoctors() {
    try {
      const data = (await this._get('/api/sub/clinic/doctors')).data || [];
      this._doctors = data;
      const tbody = document.querySelector('#cl-doc-table tbody');
      if (!tbody) return;
      if (data.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="cl-empty">No doctors added yet.</td></tr>`;
        return;
      }
      tbody.innerHTML = data.map(d => `
        <tr>
          <td style="font-family:monospace;color:#a855f7">#${d.id}</td>
          <td style="font-weight:600">${d.name}</td>
          <td style="color:var(--text-muted)">${d.specialty||'—'}</td>
          <td>${d.phone||'—'}</td>
          <td>${d.email||'—'}</td>
          <td>${this._badge(d.status||'active')}</td>
        </tr>`).join('');
    } catch(e) { console.error(e); }
  },

  _openAddDoctor() {
    const overlay = document.createElement('div');
    overlay.className = 'cl-modal-overlay';
    overlay.id = 'cl-add-doc-overlay';
    overlay.innerHTML = `
      <div class="cl-modal">
        <h3>🩺 Add Doctor</h3>
        <div class="cl-field"><label>Full Name *</label><input id="cl-doc-name" placeholder="Dr. Jane Smith" /></div>
        <div class="cl-field-row">
          <div class="cl-field"><label>Specialty</label>
            <select id="cl-doc-spec">
              <option>General Practice</option><option>Pediatrics</option><option>Internal Medicine</option>
              <option>Cardiology</option><option>Dermatology</option><option>Orthopedics</option>
              <option>Gynecology</option><option>Neurology</option><option>Ophthalmology</option>
              <option>Dentistry</option><option>Other</option>
            </select>
          </div>
          <div class="cl-field"><label>Phone</label><input id="cl-doc-phone" placeholder="+1 555 000 0000" /></div>
        </div>
        <div class="cl-field"><label>Email</label><input id="cl-doc-email" placeholder="doctor@clinic.com" /></div>
        <div class="cl-modal-footer">
          <button class="cl-btn cl-btn-ghost" onclick="document.getElementById('cl-add-doc-overlay').remove()">Cancel</button>
          <button class="cl-btn cl-btn-primary" id="cl-doc-save-btn" onclick="ClinicSystem._saveDoctor()">Save Doctor</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target===overlay) overlay.remove(); });
    document.getElementById('cl-doc-name')?.focus();
  },

  async _saveDoctor() {
    const name = document.getElementById('cl-doc-name')?.value.trim();
    if (!name) { SubsystemApp.showToast('Doctor name is required','error'); return; }
    const btn = document.getElementById('cl-doc-save-btn');
    if (btn) { btn.disabled=true; btn.textContent='Saving…'; }
    try {
      const data = await this._post('/api/sub/clinic/doctors', {
        name,
        specialty: document.getElementById('cl-doc-spec')?.value,
        phone:     document.getElementById('cl-doc-phone')?.value,
        email:     document.getElementById('cl-doc-email')?.value,
      });
      if (data.status==='success') {
        SubsystemApp.showToast('Doctor added','success');
        document.getElementById('cl-add-doc-overlay')?.remove();
        this._loadDoctors();
      } else {
        SubsystemApp.showToast(data.message||data.error||'Error adding doctor','error');
        if(btn){btn.disabled=false;btn.textContent='Save Doctor';}
      }
    } catch(e) { if(btn){btn.disabled=false;btn.textContent='Save Doctor';} }
  },

  // ── Billing ───────────────────────────────────────────────────────────────
  async _renderBilling(c) {
    this._injectStyles();
    c.innerHTML = `
      <div class="cl-section-header">
        <h2 class="cl-section-title">Billing &amp; Invoices</h2>
        <button class="sub-btn-primary" onclick="ClinicSystem._openInvoiceModal()">+ New Invoice</button>
      </div>
      <div class="sub-chart-card">
        <div style="overflow-x:auto">
          <table class="cl-table" id="cl-inv-table">
            <thead><tr><th>Invoice #</th><th>Patient</th><th>Date</th><th>Total</th><th>Paid</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody><tr><td colspan="7" class="cl-empty">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>`;
    try {
      const data = (await this._get('/api/sub/clinic/invoices')).data || [];
      const tbody = document.querySelector('#cl-inv-table tbody');
      if (data.length===0) {
        tbody.innerHTML=`<tr><td colspan="7" class="cl-empty">No invoices yet.</td></tr>`;
        return;
      }
      tbody.innerHTML = data.map(inv => `
        <tr>
          <td style="font-family:monospace;color:#14b8a6">${inv.invoice_number}</td>
          <td style="font-weight:600">${inv.patient_name||'—'}</td>
          <td style="color:var(--text-muted)">${inv.created_at?inv.created_at.slice(0,10):'—'}</td>
          <td>$${(inv.total||0).toFixed(2)}</td>
          <td style="color:#10b981">$${(inv.amount_paid||0).toFixed(2)}</td>
          <td>${this._badge(inv.status)}</td>
          <td style="display:flex;gap:6px;flex-wrap:wrap">
            ${inv.status!=='paid'?`<button class="cl-btn cl-btn-primary cl-btn-sm"
              onclick="ClinicSystem._openPaymentModal(${inv.id},'${inv.invoice_number}',${inv.total||0},${inv.amount_paid||0})">Pay</button>`
              :'<span style="color:var(--text-muted);font-size:12px">Settled</span>'}
            <button class="cl-btn cl-btn-ghost cl-btn-sm" onclick="ClinicSystem._printInvoice(${inv.id})">🖨 Print</button>
          </td>
        </tr>`).join('');
    } catch(e) { console.error(e); }
  },

  _openInvoiceModal(defaultPatientId = null) {
    if (!this._patients.length) {
      this._get('/api/sub/clinic/patients').then(d=>{
        this._patients=d.data||[];
        this._openInvoiceModal(defaultPatientId);
      }).catch(()=>{});
      return;
    }
    const overlay = document.createElement('div');
    overlay.className = 'cl-modal-overlay';
    overlay.id = 'cl-inv-overlay';
    const ptOpts = this._patients.map(p =>
      `<option value="${p.id}" ${p.id===defaultPatientId?'selected':''}>${p.name} (${p.patient_code})</option>`).join('');
    overlay.innerHTML = `
      <div class="cl-modal cl-modal-wide">
        <h3>🧾 New Invoice</h3>
        <div class="cl-field"><label>Patient *</label>
          <select id="cl-inv-patient"><option value="">Select…</option>${ptOpts}</select></div>
        <label style="color:var(--text-muted);font-size:13px;display:block;margin:6px 0 4px">Line Items</label>
        <div id="cl-inv-lines"></div>
        <button class="cl-btn cl-btn-ghost cl-btn-sm" style="margin:6px 0 14px" onclick="ClinicSystem._addInvoiceLine()">+ Add line</button>
        <div class="cl-field-row">
          <div class="cl-field"><label>Discount ($)</label><input type="number" id="cl-inv-disc" value="0" step="0.01" min="0" oninput="ClinicSystem._recalcInvoice()" /></div>
          <div class="cl-field"><label>Tax Rate (%)</label><input type="number" id="cl-inv-tax" value="0" step="0.1" min="0" oninput="ClinicSystem._recalcInvoice()" /></div>
        </div>
        <div class="cl-field"><label>Notes</label>
          <textarea id="cl-inv-notes" rows="2" placeholder="Notes for this invoice…"></textarea></div>
        <div style="text-align:right;color:#fff;font-weight:700;margin:8px 0" id="cl-inv-totals">Total: $0.00</div>
        <div class="cl-modal-footer">
          <button class="cl-btn cl-btn-ghost" onclick="document.getElementById('cl-inv-overlay').remove()">Cancel</button>
          <button class="cl-btn cl-btn-primary" id="cl-inv-save-btn" onclick="ClinicSystem._saveInvoice()">Create Invoice</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target===overlay) overlay.remove(); });
    this._addInvoiceLine();  // start with one blank line
  },

  // A line-item row. Add as many as needed (entry-addition like the follow-up sheet, #11).
  _addInvoiceLine() {
    const wrap = document.getElementById('cl-inv-lines');
    if (!wrap) return;
    const row = document.createElement('div');
    row.className = 'cl-inv-line';
    row.style.cssText = 'display:flex;gap:8px;margin-bottom:8px;align-items:center';
    row.innerHTML = `
      <input class="cl-inv-desc" placeholder="Service / item…" style="flex:2" />
      <input class="cl-inv-qty" type="number" value="1" min="1" placeholder="Qty" style="flex:0 0 70px" oninput="ClinicSystem._recalcInvoice()" />
      <input class="cl-inv-price" type="number" step="0.01" placeholder="Price" style="flex:0 0 100px" oninput="ClinicSystem._recalcInvoice()" />
      <button class="cl-btn cl-btn-danger cl-btn-sm" onclick="this.closest('.cl-inv-line').remove();ClinicSystem._recalcInvoice()">✕</button>`;
    wrap.appendChild(row);
  },

  _readInvoiceLines() {
    return [...document.querySelectorAll('#cl-inv-lines .cl-inv-line')].map(r => ({
      description: r.querySelector('.cl-inv-desc')?.value.trim() || '',
      qty:        +(r.querySelector('.cl-inv-qty')?.value || 1),
      unit_price: +(r.querySelector('.cl-inv-price')?.value || 0),
    })).filter(i => i.description && i.unit_price);
  },

  _recalcInvoice() {
    const items = this._readInvoiceLines();
    const subtotal = items.reduce((s, i) => s + i.qty * i.unit_price, 0);
    let disc = +(document.getElementById('cl-inv-disc')?.value || 0);
    if (disc < 0) disc = 0; if (disc > subtotal) disc = subtotal;
    const taxRate = +(document.getElementById('cl-inv-tax')?.value || 0) / 100;
    const tax = (subtotal - disc) * taxRate;
    const total = subtotal - disc + tax;
    const el = document.getElementById('cl-inv-totals');
    if (el) el.innerHTML = `Subtotal: $${subtotal.toFixed(2)} &nbsp;·&nbsp; Disc: -$${disc.toFixed(2)} &nbsp;·&nbsp; Tax: $${tax.toFixed(2)} &nbsp;→&nbsp; <span style="color:#14b8a6">Total: $${total.toFixed(2)}</span>`;
    return { items, disc, taxRate };
  },

  async _saveInvoice() {
    const patient_id = document.getElementById('cl-inv-patient')?.value;
    const { items, disc, taxRate } = this._recalcInvoice();
    if (!patient_id || !items.length) { SubsystemApp.showToast('Patient and at least one line item required','error'); return; }
    const btn = document.getElementById('cl-inv-save-btn');
    if (btn) { btn.disabled=true; btn.textContent='Creating…'; }
    try {
      const data = await this._post('/api/sub/clinic/invoices', {
        patient_id:+patient_id, tax_rate:taxRate, discount:disc, items,
        notes: document.getElementById('cl-inv-notes')?.value || '',
      });
      if (data.status==='success') {
        SubsystemApp.showToast(`Invoice ${data.data.invoice_number} — $${data.data.total.toFixed(2)}`,'success');
        document.getElementById('cl-inv-overlay')?.remove();
        this._renderBilling(document.getElementById('sub-content'));
      } else {
        SubsystemApp.showToast(data.message||data.error||'Error creating invoice','error');
        if(btn){btn.disabled=false;btn.textContent='Create Invoice';}
      }
    } catch(e) { if(btn){btn.disabled=false;btn.textContent='Create Invoice';} }
  },

  _openPaymentModal(invId, invNo, total, paid) {
    const remaining = (total - paid).toFixed(2);
    const overlay   = document.createElement('div');
    overlay.className = 'cl-modal-overlay';
    overlay.id = 'cl-pay-overlay';
    overlay.innerHTML = `
      <div class="cl-modal" style="width:380px">
        <h3>💳 Record Payment</h3>
        <p style="color:var(--text-muted);margin-bottom:20px">${invNo} — Balance due: <strong style="color:#fff">$${remaining}</strong></p>
        <div class="cl-field"><label>Amount ($)</label>
          <input type="number" id="cl-pay-amount" value="${remaining}" step="0.01" /></div>
        <div class="cl-field"><label>Method</label>
          <select id="cl-pay-method">
            <option>cash</option><option>card</option><option>insurance</option><option>bank_transfer</option>
          </select></div>
        <div class="cl-field"><label>Reference / Notes</label><input id="cl-pay-ref" placeholder="Receipt #, card last 4…" /></div>
        <div class="cl-modal-footer">
          <button class="cl-btn cl-btn-ghost" onclick="document.getElementById('cl-pay-overlay').remove()">Cancel</button>
          <button class="cl-btn cl-btn-primary" id="cl-pay-save-btn" onclick="ClinicSystem._savePayment(${invId})">Record Payment</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target===overlay) overlay.remove(); });
  },

  async _savePayment(invId) {
    const amount    = +(document.getElementById('cl-pay-amount')?.value||0);
    const method    =   document.getElementById('cl-pay-method')?.value;
    const reference =   document.getElementById('cl-pay-ref')?.value;
    if (!amount) { SubsystemApp.showToast('Enter a valid amount','error'); return; }
    const btn = document.getElementById('cl-pay-save-btn');
    if (btn) { btn.disabled=true; btn.textContent='Processing…'; }
    try {
      const data = await this._post('/api/sub/clinic/payments', { invoice_id:invId, amount, method, reference });
      if (data.status==='success') {
        SubsystemApp.showToast(`Payment recorded — invoice is now ${data.data.invoice_status}`,'success');
        document.getElementById('cl-pay-overlay')?.remove();
        this._renderBilling(document.getElementById('sub-content'));
      } else {
        SubsystemApp.showToast(data.message||data.error||'Payment error','error');
        if(btn){btn.disabled=false;btn.textContent='Record Payment';}
      }
    } catch(e) { if(btn){btn.disabled=false;btn.textContent='Record Payment';} }
  },

  // Printable invoice (#4): builds a clean A4-style sheet in a new window and prints.
  // docs/einvoicing/phase1/ -- best-effort, never blocks printing on
  // error. Mirrors products/retail/frontend/subsystem-retail.js's
  // _einvoiceReceiptBlock -- see that comment for the full rationale
  // (submission is async, so a receipt printed immediately after
  // checkout usually predates clearance; the invoice_ref format is
  // deterministic, matching clinic_api.py::create_invoice's enqueue
  // call, so it can be constructed client-side without an extra field
  // on the GET /invoices/<id> response).
  async _einvoiceInvoiceBlock(invId) {
    const ref = 'AURA_CLINIC:clinic_invoice:' + invId;
    try {
      const resp = await this._get('/api/einvoicing/outbox/' + encodeURIComponent(ref));
      const entryStatus = resp?.data?.entry?.status;
      if (entryStatus === 'CLEARED') {
        const qrUrl = '/api/einvoicing/qr/' + encodeURIComponent(ref) + '.png';
        return `<div style="clear:both;padding-top:20px;text-align:center">
          <img src="${qrUrl}" style="width:100px;height:auto" />
          <div class="muted">Jordan e-invoice cleared</div></div>`;
      }
      if (entryStatus) {
        return `<div style="clear:both;padding-top:20px" class="muted">Jordan e-invoice: pending government clearance</div>`;
      }
    } catch (e) { /* not enqueued (feature off, or a 404) -- show nothing */ }
    return '';
  },

  async _printInvoice(invId) {
    try {
      const d = (await this._get(`/api/sub/clinic/invoices/${invId}`)).data || {};
      const inv = d.invoice || {};
      const items = d.items || [];
      const einvoiceBlock = await this._einvoiceInvoiceBlock(invId);
      const esc = s => String(s == null ? '' : s).replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));
      const rows = items.map(i => `<tr>
        <td>${esc(i.description)}</td>
        <td style="text-align:center">${i.qty}</td>
        <td style="text-align:right">$${(+i.unit_price||0).toFixed(2)}</td>
        <td style="text-align:right">$${(+i.line_total||0).toFixed(2)}</td></tr>`).join('');
      const html = `<!doctype html><html><head><meta charset="utf-8"><title>${esc(inv.invoice_number)}</title>
        <style>
          body{font-family:Arial,Helvetica,sans-serif;color:#111;margin:40px;}
          h1{font-size:22px;margin:0 0 4px} .muted{color:#666;font-size:13px}
          table{width:100%;border-collapse:collapse;margin-top:24px}
          th,td{padding:8px 10px;border-bottom:1px solid #ddd;font-size:14px}
          th{text-align:left;background:#f5f5f5}
          .totals{margin-top:16px;float:right;width:260px;font-size:14px}
          .totals div{display:flex;justify-content:space-between;padding:4px 0}
          .grand{font-weight:700;border-top:2px solid #111;margin-top:6px;padding-top:6px;font-size:16px}
          @media print{button{display:none}}
        </style></head><body>
        <button onclick="window.print()" style="float:right;padding:8px 16px">Print</button>
        <h1>INVOICE</h1>
        <div class="muted">${esc(inv.invoice_number)} &nbsp;·&nbsp; ${esc((inv.created_at||'').slice(0,10))}</div>
        <div class="muted" style="margin-top:8px">Status: ${esc(inv.status)}</div>
        <table><thead><tr><th>Description</th><th style="text-align:center">Qty</th><th style="text-align:right">Unit</th><th style="text-align:right">Total</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="4" style="text-align:center;color:#999">No items</td></tr>'}</tbody></table>
        <div class="totals">
          <div><span>Subtotal</span><span>$${(+inv.subtotal||0).toFixed(2)}</span></div>
          ${(+inv.discount)?`<div><span>Discount</span><span>-$${(+inv.discount).toFixed(2)}</span></div>`:''}
          <div><span>Tax</span><span>$${(+inv.tax||0).toFixed(2)}</span></div>
          <div class="grand"><span>Total</span><span>$${(+inv.total||0).toFixed(2)}</span></div>
          <div><span>Paid</span><span>$${(+inv.amount_paid||0).toFixed(2)}</span></div>
        </div>
        ${inv.notes?`<div style="clear:both;padding-top:30px" class="muted">Notes: ${esc(inv.notes)}</div>`:''}
        ${einvoiceBlock}
        </body></html>`;
      // On Android the WebView can't open a print popup; hand the HTML to the native
      // print bridge (MainActivity.AndroidBridge.printHtml). Inert on desktop.
      if (window.AndroidBridge && typeof window.AndroidBridge.isAndroid === 'function'
          && window.AndroidBridge.isAndroid()) {
        window.AndroidBridge.printHtml(html);
        return;
      }
      const w = window.open('', '_blank', 'width=800,height=900');
      if (!w) { SubsystemApp.showToast('Allow pop-ups to print invoices', 'error'); return; }
      w.document.write(html); w.document.close();
      setTimeout(() => { try { w.print(); } catch (e) {} }, 350);
    } catch (e) { SubsystemApp.showToast('Could not load invoice for printing', 'error'); }
  },

  // ── Lab Expenses ──────────────────────────────────────────────────────────
  async _renderLabExpenses(c) {
    this._injectStyles();
    c.innerHTML = `
      <div class="cl-section-header">
        <h2 class="cl-section-title">Lab Expenses</h2>
        <button class="sub-btn-primary" onclick="ClinicSystem._openLabExpenseModal()">+ Record Expense</button>
      </div>
      <div class="sub-chart-card">
        <div style="overflow-x:auto">
          <table class="cl-table" id="cl-lab-table">
            <thead><tr><th>Date</th><th>Lab</th><th>Test</th><th>Patient</th><th>Amount</th><th>Notes</th><th></th></tr></thead>
            <tbody><tr><td colspan="7" class="cl-empty">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>`;
    await this._loadLabExpenses();
  },

  async _loadLabExpenses() {
    try {
      const data = (await this._get('/api/sub/clinic/lab-expenses')).data || [];
      const tbody = document.querySelector('#cl-lab-table tbody');
      if (!tbody) return;
      if (data.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="cl-empty">No lab expenses recorded.</td></tr>`;
        return;
      }
      tbody.innerHTML = data.map(x => `
        <tr>
          <td style="color:var(--text-muted)">${x.expense_date || (x.created_at||'').slice(0,10) || '—'}</td>
          <td style="font-weight:600">${x.lab_name || '—'}</td>
          <td>${x.test_name || '—'}</td>
          <td style="color:var(--text-muted)">${x.patient_name || '—'}</td>
          <td style="color:#f59e0b">$${(+x.amount || 0).toFixed(2)}</td>
          <td style="color:var(--text-muted)">${x.notes || '—'}</td>
          <td onclick="event.stopPropagation()">
            <button class="cl-btn cl-btn-danger cl-btn-sm" onclick="ClinicSystem._deleteLabExpense(${x.id})">Delete</button>
          </td>
        </tr>`).join('');
    } catch (e) { console.error(e); }
  },

  _openLabExpenseModal() {
    if (!this._patients.length) {
      this._get('/api/sub/clinic/patients').then(d => { this._patients = d.data || []; this._openLabExpenseModal(); }).catch(() => {});
      return;
    }
    const today = new Date().toISOString().slice(0,10);
    const ptOpts = this._patients.map(p => `<option value="${p.id}">${p.name} (${p.patient_code})</option>`).join('');
    const overlay = document.createElement('div');
    overlay.className = 'cl-modal-overlay';
    overlay.id = 'cl-lab-overlay';
    overlay.innerHTML = `
      <div class="cl-modal">
        <h3>🧪 Record Lab Expense</h3>
        <div class="cl-field-row">
          <div class="cl-field"><label>Lab Name *</label><input id="cl-lab-name" placeholder="e.g. Acme Diagnostics" /></div>
          <div class="cl-field"><label>Test</label><input id="cl-lab-test" placeholder="e.g. CBC, MRI…" /></div>
        </div>
        <div class="cl-field-row">
          <div class="cl-field"><label>Amount ($) *</label><input type="number" id="cl-lab-amount" placeholder="0.00" step="0.01" /></div>
          <div class="cl-field"><label>Date</label><input type="date" id="cl-lab-date" value="${today}" /></div>
        </div>
        <div class="cl-field"><label>Patient (optional)</label>
          <select id="cl-lab-patient"><option value="">— None —</option>${ptOpts}</select></div>
        <div class="cl-field"><label>Notes</label>
          <textarea id="cl-lab-notes" rows="2" placeholder="Reference, remarks…"></textarea></div>
        <div class="cl-modal-footer">
          <button class="cl-btn cl-btn-ghost" onclick="document.getElementById('cl-lab-overlay').remove()">Cancel</button>
          <button class="cl-btn cl-btn-primary" id="cl-lab-save-btn" onclick="ClinicSystem._saveLabExpense()">Save Expense</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    document.getElementById('cl-lab-name')?.focus();
  },

  async _saveLabExpense() {
    const lab_name = document.getElementById('cl-lab-name')?.value.trim();
    const amount   = +(document.getElementById('cl-lab-amount')?.value || 0);
    if (!lab_name || !amount) { SubsystemApp.showToast('Lab name and amount are required', 'error'); return; }
    const btn = document.getElementById('cl-lab-save-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    const pid = document.getElementById('cl-lab-patient')?.value;
    try {
      const data = await this._post('/api/sub/clinic/lab-expenses', {
        lab_name,
        test_name:    document.getElementById('cl-lab-test')?.value,
        amount,
        expense_date: document.getElementById('cl-lab-date')?.value,
        patient_id:   pid ? +pid : null,
        notes:        document.getElementById('cl-lab-notes')?.value,
      });
      if (data.status === 'success') {
        SubsystemApp.showToast('Lab expense recorded (posted to Accounting)', 'success');
        document.getElementById('cl-lab-overlay')?.remove();
        this._loadLabExpenses();
      } else {
        SubsystemApp.showToast(data.message || data.error || 'Error saving expense', 'error');
        if (btn) { btn.disabled = false; btn.textContent = 'Save Expense'; }
      }
    } catch (e) { if (btn) { btn.disabled = false; btn.textContent = 'Save Expense'; } }
  },

  async _deleteLabExpense(id) {
    if (!confirm('Delete this lab expense?')) return;
    try {
      const d = await this._fetch(`/api/sub/clinic/lab-expenses/${id}`, { method: 'DELETE' });
      if (d.ok) { SubsystemApp.showToast('Lab expense deleted', 'success'); this._loadLabExpenses(); }
    } catch (e) { console.error(e); }
  },

  // ── Prescriptions ─────────────────────────────────────────────────────────
  async _renderPrescriptions(c) {
    this._injectStyles();
    c.innerHTML = `
      <div class="cl-section-header">
        <h2 class="cl-section-title">Prescriptions</h2>
        <button class="sub-btn-primary" onclick="ClinicSystem._openPrescModal()">+ New Prescription</button>
      </div>
      <div class="sub-chart-card">
        <div style="overflow-x:auto">
          <table class="cl-table" id="cl-rx-table">
            <thead><tr><th>ID</th><th>Patient</th><th>Medications</th><th>Notes</th><th>Date</th></tr></thead>
            <tbody><tr><td colspan="5" class="cl-empty">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>`;
    try {
      const data = (await this._get('/api/sub/clinic/prescriptions')).data || [];
      const tbody = document.querySelector('#cl-rx-table tbody');
      if (data.length===0) {
        tbody.innerHTML=`<tr><td colspan="5" class="cl-empty">No prescriptions yet.</td></tr>`;
        return;
      }
      tbody.innerHTML = data.map(rx => {
        let items = '—';
        try {
          const arr = JSON.parse(rx.items_json.replace(/'/g,'"'));
          items = arr.map(i=>i.drug||i.name||i).join(', ')||'—';
        } catch(e){}
        return `<tr>
          <td style="font-family:monospace;color:#14b8a6">#${rx.id}</td>
          <td style="font-weight:600">Patient #${rx.patient_id}</td>
          <td>${items}</td>
          <td style="color:var(--text-muted)">${rx.notes||'—'}</td>
          <td style="color:var(--text-muted)">${rx.created_at?rx.created_at.slice(0,10):'—'}</td>
        </tr>`;
      }).join('');
    } catch(e) { console.error(e); }
  },

  _openPrescModal(defaultPatientId = null) {
    if (!this._patients.length) {
      this._get('/api/sub/clinic/patients').then(d=>{
        this._patients=d.data||[];
        this._openPrescModal(defaultPatientId);
      }).catch(()=>{});
      return;
    }
    const overlay = document.createElement('div');
    overlay.className = 'cl-modal-overlay';
    overlay.id = 'cl-rx-overlay';
    const ptOpts = this._patients.map(p =>
      `<option value="${p.id}" ${p.id===defaultPatientId?'selected':''}>${p.name} (${p.patient_code})</option>`).join('');
    overlay.innerHTML = `
      <div class="cl-modal">
        <h3>💊 New Prescription</h3>
        <div class="cl-field"><label>Patient *</label>
          <select id="cl-rx-patient"><option value="">Select…</option>${ptOpts}</select></div>
        <div class="cl-field">
          <label>Medications (one per line — e.g. "Amoxicillin 500mg 3x/day")</label>
          <textarea id="cl-rx-drugs" rows="4" placeholder="Paracetamol 500mg — 3x daily&#10;Ibuprofen 400mg — as needed"></textarea>
        </div>
        <div class="cl-field"><label>Clinical Notes / Instructions</label>
          <textarea id="cl-rx-notes" rows="2" placeholder="Take with food. Follow up in 7 days."></textarea></div>
        <div class="cl-modal-footer">
          <button class="cl-btn cl-btn-ghost" onclick="document.getElementById('cl-rx-overlay').remove()">Cancel</button>
          <button class="cl-btn cl-btn-primary" id="cl-rx-save-btn" onclick="ClinicSystem._savePrescription()">Save Prescription</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target===overlay) overlay.remove(); });
  },

  async _savePrescription() {
    const patient_id = document.getElementById('cl-rx-patient')?.value;
    const drugs      = document.getElementById('cl-rx-drugs')?.value.trim();
    const notes      = document.getElementById('cl-rx-notes')?.value.trim();
    if (!patient_id||!drugs) { SubsystemApp.showToast('Patient and medications required','error'); return; }
    const items = drugs.split('\n').map(l=>l.trim()).filter(Boolean).map(l=>({ drug:l }));
    const btn = document.getElementById('cl-rx-save-btn');
    if (btn) { btn.disabled=true; btn.textContent='Saving…'; }
    try {
      const data = await this._post('/api/sub/clinic/prescriptions', { patient_id:+patient_id, items, notes });
      if (data.status==='success') {
        SubsystemApp.showToast('Prescription saved','success');
        document.getElementById('cl-rx-overlay')?.remove();
        this._renderPrescriptions(document.getElementById('sub-content'));
      } else {
        SubsystemApp.showToast(data.message||'Error','error');
        if(btn){btn.disabled=false;btn.textContent='Save Prescription';}
      }
    } catch(e) { if(btn){btn.disabled=false;btn.textContent='Save Prescription';} }
  },

  // ── Reports ───────────────────────────────────────────────────────────────
  async _renderReports(c) {
    this._injectStyles();
    const end   = new Date().toISOString().slice(0,10);
    const start = new Date(Date.now()-30*86400000).toISOString().slice(0,10);
    c.innerHTML = `
      <div class="cl-section-header">
        <h2 class="cl-section-title">Clinic Reports</h2>
        <div style="display:flex;gap:8px;align-items:center">
          <input type="date" id="cl-rpt-start" value="${start}" style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:8px;color:#fff;padding:7px 10px;outline:none" />
          <span style="color:var(--text-muted)">→</span>
          <input type="date" id="cl-rpt-end"   value="${end}"   style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:8px;color:#fff;padding:7px 10px;outline:none" />
          <button class="cl-btn cl-btn-primary cl-btn-sm" onclick="ClinicSystem._loadReports()">Load</button>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px" id="cl-rpt-charts">
        <div class="sub-chart-card"><div class="sub-chart-title">Daily Revenue</div><div style="height:260px"><canvas id="cl-rpt-rev-chart"></canvas></div></div>
        <div class="sub-chart-card"><div class="sub-chart-title">Appointments by Status</div><div style="height:260px"><canvas id="cl-rpt-appt-chart"></canvas></div></div>
      </div>
      <div class="sub-chart-card" style="margin-top:20px">
        <div class="sub-chart-title" style="margin-bottom:14px">Revenue Summary</div>
        <div id="cl-rpt-summary" style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px"></div>
      </div>`;
    await this._loadReports();
  },

  async _loadReports() {
    const start = document.getElementById('cl-rpt-start')?.value || new Date(Date.now()-30*86400000).toISOString().slice(0,10);
    const end   = document.getElementById('cl-rpt-end')?.value   || new Date().toISOString().slice(0,10);
    try {
      const [revData, apptData] = await Promise.all([
        this._get(`/api/sub/clinic/reports/revenue?start=${start}&end=${end}`),
        this._get(`/api/sub/clinic/reports/appointments?start=${start}&end=${end}`),
      ]);
      const revRows  = revData.data  || [];
      const apptRows = apptData.data || [];

      // Revenue chart
      if (window.Chart) {
        const revCtx = document.getElementById('cl-rpt-rev-chart');
        if (revCtx) {
          if (revCtx._chart) revCtx._chart.destroy();
          revCtx._chart = new Chart(revCtx.getContext('2d'), {
            type:'line',
            data:{
              labels: revRows.map(r=>r.day),
              datasets:[{ label:'Revenue ($)', data:revRows.map(r=>r.revenue||0),
                borderColor:'#14b8a6', backgroundColor:'rgba(20,184,166,0.1)',
                borderWidth:2, fill:true, tension:0.4 }]
            },
            options:{ responsive:true, maintainAspectRatio:false,
              plugins:{ legend:{display:false} },
              scales:{ y:{grid:{color:'rgba(255,255,255,0.05)'},ticks:{color:'#94a3b8',callback:v=>'$'+v}},
                       x:{grid:{display:false},ticks:{color:'#94a3b8'}} } }
          });
        }
        // Appointments status donut chart
        const statusMap = {};
        apptRows.forEach(r => { statusMap[r.status] = (statusMap[r.status]||0)+r.count; });
        const apptCtx = document.getElementById('cl-rpt-appt-chart');
        if (apptCtx) {
          if (apptCtx._chart) apptCtx._chart.destroy();
          const colors = { scheduled:'#38bdf8', waiting:'#fbbf24', in_progress:'#a855f7', completed:'#10b981', cancelled:'#ef4444', no_show:'#64748b' };
          apptCtx._chart = new Chart(apptCtx.getContext('2d'), {
            type:'doughnut',
            data:{
              labels: Object.keys(statusMap),
              datasets:[{ data:Object.values(statusMap),
                backgroundColor:Object.keys(statusMap).map(s=>colors[s]||'#64748b'),
                borderWidth:0 }]
            },
            options:{ responsive:true, maintainAspectRatio:false,
              plugins:{ legend:{position:'right',labels:{color:'#cbd5e1',font:{size:12}}} } }
          });
        }
      }

      // Summary cards
      const totalRev = revRows.reduce((s,r)=>s+(r.revenue||0), 0);
      const totalTxns = revRows.reduce((s,r)=>s+(r.transactions||0), 0);
      const totalAppts = Object.values(apptRows.reduce((m,r)=>{ m[r.status]=(m[r.status]||0)+r.count; return m; },{})).reduce((s,v)=>s+v,0);
      const summary = document.getElementById('cl-rpt-summary');
      if (summary) {
        summary.innerHTML = `
          <div class="cl-kpi"><div class="cl-kpi-label">Total Revenue</div><div class="cl-kpi-value" style="color:#14b8a6">$${totalRev.toFixed(2)}</div><div class="cl-kpi-sub">${start} → ${end}</div></div>
          <div class="cl-kpi"><div class="cl-kpi-label">Payments</div><div class="cl-kpi-value">${totalTxns}</div><div class="cl-kpi-sub">Transactions</div></div>
          <div class="cl-kpi"><div class="cl-kpi-label">Appointments</div><div class="cl-kpi-value">${totalAppts}</div><div class="cl-kpi-sub">Total in period</div></div>`;
      }
    } catch(e) { console.error('Reports error:', e); }
  },

  _openKPIPicker() {
    if (window.KPIPicker) {
      const _origAdd = KPIPicker._add.bind(KPIPicker);
      KPIPicker._add = function(id, title, type, color) {
        _origAdd(id, title, type, color);
        KPIPicker._add = _origAdd;
        if (window.EnterpriseDashboard) {
          const dept = EnterpriseDashboard._getDeptId ? EnterpriseDashboard._getDeptId() : 'clinic';
          EnterpriseDashboard._deptCharts[dept] = EnterpriseDashboard.charts;
          EnterpriseDashboard._saveCharts();
        }
        ClinicSystem.render('dashboard');
      };
      KPIPicker.open(['clinic']);
    }
  },
};

window.ClinicSystem = ClinicSystem;
