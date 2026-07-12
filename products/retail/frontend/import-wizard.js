/**
 * Action Aura — Universal Data Import Wizard
 * 3-step flow: Upload file → Map columns → Review & Import
 * Usage: ImportWizard.open('retail', 'products', onDoneCallback)
 */
const ImportWizard = {
  _system: null,
  _entity: null,
  _file: null,
  _schema: null,
  _parsed: null,      // { columns, samples, total, suggested }
  _mapping: {},       // { field_key: column_name | null }
  _onDone: null,
  _schemasCache: null,

  // Entry point from the subsystem header — goes straight to file upload.
  // The mapping step auto-detects which entity the file belongs to via
  // entity_suggestions, so no upfront category picker is needed.
  async openForSystem(system) {
    if (!window.IS_STANDALONE || window.isDemoMode) {
      (window.SubsystemApp?.showToast || window.alert)(
        'Data import is available in your downloaded system, not in the demo.', 'info');
      return;
    }
    try {
      if (!this._schemasCache) {
        const res = await fetch('/api/import/schemas', { credentials: 'include' });
        const data = await res.json();
        this._schemasCache = data.schemas || {};
      }
    } catch (e) {
      (window.SubsystemApp?.showToast || window.alert)('Could not load import config', 'error');
      return;
    }
    const entities = this._schemasCache[system] || {};
    const keys = Object.keys(entities);
    if (keys.length === 0) {
      (window.SubsystemApp?.showToast || window.alert)('No importable data types for this system.', 'info');
      return;
    }
    // Prefer the primary/people entity for each system; fall back to first key.
    const PRIMARY = { hr: 'employees', payroll: 'payroll', retail: 'sales', crm: 'contacts' };
    const startEntity = (PRIMARY[system] && entities[PRIMARY[system]]) ? PRIMARY[system] : keys[0];
    this.open(system, startEntity);
  },

  // ── UNIVERSAL / SMART IMPORT (home dashboard) ──────────────────────────────
  // Upload one file (combined or single-type); detect every function it covers
  // and import them all in one ordered pass. Reuses the wizard's styles.
  async openSmart(systems) {
    if (!window.IS_STANDALONE || window.isDemoMode) {
      (window.SubsystemApp?.showToast || window.alert)(
        'Data import is available in your downloaded system, not in the demo.', 'info');
      return;
    }
    this._smartSystems = (systems && systems[0] !== 'all') ? systems : null;
    this._smartFile = null;
    this._smartParsed = null;
    this._smartTargets = [];
    this._injectStyles();
    this._smartStep1();
  },

  _smartShell(body, foot) {
    document.getElementById('iw-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'iw-overlay'; overlay.id = 'iw-overlay';
    overlay.innerHTML = `
      <div class="iw-modal">
        <div class="iw-head">
          <div><h3>Smart Import</h3><p>Upload one file — we route it to the right functions</p></div>
          <button class="iw-close" onclick="ImportWizard.close()">✕</button>
        </div>
        <div class="iw-body">${body}</div>
        <div class="iw-foot">${foot}</div>
      </div>`;
    document.body.appendChild(overlay);
  },

  _smartStep1() {
    this._smartShell(`
      <div class="iw-drop" id="iw-sdrop" onclick="document.getElementById('iw-sfile').click()">
        <input type="file" id="iw-sfile" accept=".csv,.xlsx,.xls,.json,.db,.sqlite,.sqlite3" style="display:none" onchange="ImportWizard._smartOnFile(this.files[0])" />
        <div style="font-size:46px;margin-bottom:12px">🗂️</div>
        <div id="iw-sdrop-text" style="color:#fff;font-size:16px;font-weight:600;margin-bottom:6px">Drop a file here or click to browse</div>
        <div style="color:#64748b;font-size:13px">CSV, Excel or JSON — combined or single-type, we'll figure it out</div>
      </div>
    `, `
      <span></span>
      <button class="iw-btn iw-btn-primary" id="iw-sdetect" disabled onclick="ImportWizard._smartDetect()">Analyze File →</button>
    `);
    const drop = document.getElementById('iw-sdrop');
    ['dragover','dragenter'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('drag'); }));
    ['dragleave','drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('drag'); }));
    drop.addEventListener('drop', e => { const f = e.dataTransfer.files[0]; if (f) this._smartOnFile(f); });
  },

  _smartOnFile(file) {
    if (!file) return;
    this._smartFile = file;
    const t = document.getElementById('iw-sdrop-text'); if (t) t.textContent = '📄 ' + file.name;
    const b = document.getElementById('iw-sdetect'); if (b) b.disabled = false;
  },

  async _smartDetect() {
    const btn = document.getElementById('iw-sdetect');
    if (btn) { btn.disabled = true; btn.textContent = 'Analyzing…'; }
    const fd = new FormData();
    fd.append('file', this._smartFile);
    if (this._smartSystems) fd.append('systems', this._smartSystems.join(','));
    try {
      const res = await fetch('/api/import/detect', { method:'POST', credentials:'include', body: fd });
      const data = await res.json();
      if (!data.success) {
        (window.SubsystemApp?.showToast || alert)(data.error || 'Could not analyze file', 'error');
        if (btn) { btn.disabled = false; btn.textContent = 'Analyze File →'; }
        return;
      }
      this._smartParsed = data;
      this._smartTargets = (data.detected || []).map(d => ({
        system: d.system, entity: d.entity, label: d.label,
        include: true, mapping: { ...d.suggested }, fields: d.fields,
      }));
      this._smartRenderReview();
    } catch (e) {
      (window.SubsystemApp?.showToast || alert)('Error analyzing file', 'error');
      if (btn) { btn.disabled = false; btn.textContent = 'Analyze File →'; }
    }
  },

  _smartRenderReview() {
    const d = this._smartParsed;
    if (!this._smartTargets.length) {
      this._smartShell(
        `<div style="padding:24px;text-align:center;color:#fca5a5">We couldn't recognize any importable data types in this file.<br>
          <span style="color:#64748b;font-size:13px">Use a function's own Import button and map the columns manually.</span></div>`,
        `<button class="iw-btn iw-btn-ghost" onclick="ImportWizard._smartStep1()">← Back</button><span></span>`);
      return;
    }
    const cols = d.columns;
    const cards = this._smartTargets.map((t, i) => {
      const mappedCount = t.fields.filter(f => t.mapping[f.key]).length;
      const rows = t.fields.map(f => {
        const sel = t.mapping[f.key] || '';
        return `<div style="display:grid;grid-template-columns:1fr 20px 1fr;gap:8px;align-items:center;padding:4px 0">
          <span style="color:#cbd5e1;font-size:13px">${this._esc(f.label)}${f.required ? '<span style="color:#f43f5e">*</span>' : ''}</span>
          <span style="color:#475569;text-align:center">→</span>
          <select class="iw-select" onchange="ImportWizard._smartMap(${i},'${f.key}',this.value)" style="padding:6px 10px;font-size:12px">
            <option value="">— skip —</option>
            ${cols.map(c => `<option value="${this._esc(c)}" ${c === sel ? 'selected' : ''}>${this._esc(c)}</option>`).join('')}
          </select></div>`;
      }).join('');
      return `<div style="border:1px solid rgba(255,255,255,0.1);border-radius:12px;padding:14px 16px;margin-bottom:12px;background:rgba(255,255,255,0.02)">
        <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
          <input type="checkbox" ${t.include ? 'checked' : ''} onchange="ImportWizard._smartToggle(${i},this.checked)" style="width:16px;height:16px">
          <span style="color:#fff;font-weight:700;font-size:15px">${this._esc(t.label)}</span>
          <span style="color:#5eead4;font-size:12px">${mappedCount} field(s) detected</span>
        </label>
        <details style="margin-left:26px;margin-top:6px"><summary style="color:#64748b;font-size:12px;cursor:pointer">Review / adjust mapping</summary>
          <div style="margin-top:8px">${rows}</div></details>
      </div>`;
    }).join('');
    const order = this._smartTargets.filter(t => t.include).map(t => t.label).join(' → ');
    this._smartShell(`
      <div style="background:rgba(20,184,166,0.08);border:1px solid rgba(20,184,166,0.2);border-radius:10px;padding:12px 16px;margin-bottom:16px;color:#5eead4;font-size:13px">
        ✓ Found <strong>${d.total}</strong> rows. This file maps to <strong>${this._smartTargets.length}</strong> function(s) — review and import them all at once.
      </div>
      ${cards}
      <div style="color:#64748b;font-size:12px">Import order: ${this._esc(order || '—')}</div>
      <div id="iw-sresult" style="margin-top:14px"></div>
    `, `
      <button class="iw-btn iw-btn-ghost" onclick="ImportWizard._smartStep1()">← Back</button>
      <button class="iw-btn iw-btn-primary" id="iw-sgo" onclick="ImportWizard._smartExecute()">Import All Selected</button>
    `);
  },

  _smartToggle(i, on) { if (this._smartTargets[i]) this._smartTargets[i].include = on; },
  _smartMap(i, fkey, col) { if (this._smartTargets[i]) this._smartTargets[i].mapping[fkey] = col || null; },

  async _smartExecute() {
    const targets = this._smartTargets.filter(t => t.include)
      .map(t => ({ system: t.system, entity: t.entity, mapping: t.mapping }));
    if (!targets.length) { (window.SubsystemApp?.showToast || alert)('Select at least one type to import', 'info'); return; }
    const btn = document.getElementById('iw-sgo'); if (btn) { btn.disabled = true; btn.textContent = 'Importing…'; }
    const fd = new FormData(); fd.append('file', this._smartFile); fd.append('targets', JSON.stringify(targets));
    try {
      const res = await fetch('/api/import/smart-execute', { method:'POST', credentials:'include', body: fd });
      const data = await res.json();
      const el = document.getElementById('iw-sresult');
      if (data.success) {
        const rowsHtml = data.results.map(r => `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.06);font-size:13px">
          <span style="color:#e2e8f0">${this._esc(r.label || r.entity)}</span>
          <span style="color:${r.error ? '#fca5a5' : '#5eead4'}">${r.error ? this._esc(r.error) : ((r.imported || 0) + ' imported' + (r.updated ? ', ' + r.updated + ' updated' : '') + (r.skipped ? ', ' + r.skipped + ' skipped' : ''))}</span>
        </div>`).join('');
        el.innerHTML = `<div style="background:rgba(16,185,129,0.1);border:1px solid rgba(16,185,129,0.3);border-radius:10px;padding:14px">
          <div style="color:#fff;font-weight:700;margin-bottom:8px">✅ Imported ${data.total_imported} records across ${data.results.length} function(s)</div>${rowsHtml}</div>`;
        if (btn) { btn.textContent = 'Done'; btn.disabled = false; btn.onclick = () => { ImportWizard.close(); try { if (window.SubsystemApp && SubsystemApp.active) SubsystemApp._navigate(SubsystemApp.currentSection); } catch (e) {} }; }
      } else {
        el.innerHTML = `<div style="background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:10px;padding:14px;color:#fca5a5">${this._esc(data.error || 'Import failed')}</div>`;
        if (btn) { btn.disabled = false; btn.textContent = 'Retry'; }
      }
    } catch (e) {
      (window.SubsystemApp?.showToast || alert)('Network error during import', 'error');
      if (btn) { btn.disabled = false; btn.textContent = 'Retry'; }
    }
  },

  async open(system, entity, onDone) {
    // ── Standalone-only gate ──────────────────────────────────────────────────
    // Live Excel/CSV import is a feature of the downloaded customer system only.
    // It must NEVER be available on the demo portal.
    if (!window.IS_STANDALONE || window.isDemoMode) {
      (window.SubsystemApp?.showToast || window.alert)(
        'Data import is available in your downloaded system, not in the demo.', 'info');
      return;
    }

    this._system = system;
    this._entity = entity;
    this._onDone = onDone || function(){};
    this._file = null;
    this._parsed = null;
    this._mapping = {};
    this._autoRouted = false;        // auto-detect the data type once per import
    this._autoDetectedLabel = null;

    // Load schema definition
    try {
      if (!this._schemasCache) {
        const res = await fetch('/api/import/schemas', { credentials: 'include' });
        const data = await res.json();
        this._schemasCache = data.schemas || {};
      }
      this._schema = (this._schemasCache[system] || {})[entity];
      if (!this._schema) {
        (window.SubsystemApp?.showToast || window.alert)('Import not available for this section', 'error');
        return;
      }
    } catch (e) {
      (window.SubsystemApp?.showToast || window.alert)('Could not load import config', 'error');
      return;
    }

    this._injectStyles();
    this._renderStep1();
  },

  _injectStyles() {
    if (document.getElementById('iw-styles')) return;
    const s = document.createElement('style');
    s.id = 'iw-styles';
    s.textContent = `
      .iw-overlay { position:fixed;inset:0;background:rgba(2,6,23,0.85);display:flex;align-items:center;justify-content:center;z-index:100000;backdrop-filter:blur(6px); }
      .iw-modal { background:#0f172a;border:1px solid rgba(255,255,255,0.1);border-radius:18px;width:720px;max-width:94vw;max-height:90vh;display:flex;flex-direction:column;box-shadow:0 30px 90px rgba(0,0,0,0.7); }
      .iw-head { padding:24px 28px;border-bottom:1px solid rgba(255,255,255,0.07);display:flex;justify-content:space-between;align-items:center; }
      .iw-head h3 { margin:0;color:#fff;font-size:20px;font-weight:700; }
      .iw-head p { margin:4px 0 0;color:var(--text-muted,#94a3b8);font-size:13px; }
      .iw-close { background:none;border:none;color:#64748b;font-size:22px;cursor:pointer;line-height:1; }
      .iw-steps { display:flex;gap:8px;padding:16px 28px;border-bottom:1px solid rgba(255,255,255,0.05); }
      .iw-step { flex:1;display:flex;align-items:center;gap:8px;color:#475569;font-size:13px;font-weight:600; }
      .iw-step.active { color:#14b8a6; }
      .iw-step.done { color:#10b981; }
      .iw-step-num { width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;border:1.5px solid currentColor; }
      .iw-body { padding:28px;overflow-y:auto;flex:1; }
      .iw-spinner { width:38px;height:38px;border:3px solid rgba(20,184,166,0.2);border-top-color:#14b8a6;border-radius:50%;animation:iw-spin 0.8s linear infinite; }
      @keyframes iw-spin { to { transform:rotate(360deg); } }
      .iw-foot { padding:18px 28px;border-top:1px solid rgba(255,255,255,0.07);display:flex;justify-content:space-between;gap:10px; }
      .iw-btn { padding:11px 22px;border-radius:9px;font-weight:600;font-size:14px;cursor:pointer;border:none;transition:.2s; }
      .iw-btn-primary { background:linear-gradient(135deg,#14b8a6,#0d9488);color:#fff; }
      .iw-btn-primary:hover { opacity:.9; }
      .iw-btn-primary:disabled { opacity:.4;cursor:not-allowed; }
      .iw-btn-ghost { background:rgba(255,255,255,0.05);color:#cbd5e1;border:1px solid rgba(255,255,255,0.1); }
      .iw-drop { border:2px dashed rgba(255,255,255,0.15);border-radius:14px;padding:48px 20px;text-align:center;cursor:pointer;transition:.2s; }
      .iw-drop:hover,.iw-drop.drag { border-color:#14b8a6;background:rgba(20,184,166,0.05); }
      .iw-map-row { display:grid;grid-template-columns:1fr 28px 1fr;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.04); }
      .iw-map-field { color:#fff;font-size:14px;font-weight:600; }
      .iw-map-field .req { color:#f43f5e;margin-left:3px; }
      .iw-map-field .hint { display:block;color:#64748b;font-size:11px;font-weight:400;margin-top:2px; }
      .iw-map-arrow { color:#475569;text-align:center; }
      .iw-select { width:100%;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:8px;color:#fff;padding:9px 12px;font-size:13px;outline:none; }
      .iw-select:focus { border-color:#14b8a6; }
      .iw-select.unmapped-req { border-color:rgba(244,63,94,0.5); }
      .iw-sample { display:block;color:#10b981;font-size:11px;margin-top:3px;font-family:monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }
      .iw-summary-card { background:rgba(255,255,255,0.04);border-radius:12px;padding:20px;margin-bottom:16px; }
      .iw-stat { display:inline-block;text-align:center;padding:0 24px; }
      .iw-stat-num { font-size:32px;font-weight:800;color:#14b8a6; }
      .iw-stat-lbl { font-size:12px;color:var(--text-muted,#94a3b8);text-transform:uppercase;letter-spacing:.5px; }
      .iw-err-list { max-height:180px;overflow-y:auto;background:rgba(239,68,68,0.06);border:1px solid rgba(239,68,68,0.2);border-radius:10px;padding:12px;margin-top:14px; }
      .iw-err-item { color:#fca5a5;font-size:12px;padding:4px 0;border-bottom:1px solid rgba(239,68,68,0.1); }
      .iw-tmpl-link { color:#38bdf8;font-size:13px;cursor:pointer;text-decoration:underline;background:none;border:none; }
      .iw-conf { display:inline-block;font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px;margin-left:8px;vertical-align:middle;text-transform:uppercase;letter-spacing:.3px; }
      .iw-conf.high { background:rgba(16,185,129,0.15);color:#34d399; }
      .iw-conf.medium { background:rgba(234,179,8,0.15);color:#fbbf24; }
      .iw-conf.low { background:rgba(244,63,94,0.18);color:#fb7185; }
      .iw-conf.manual { background:rgba(56,189,248,0.15);color:#7dd3fc; }
      .iw-conf-reason { color:#64748b;font-size:11px;margin-top:3px;display:block; }
      .iw-entity-banner { background:rgba(234,179,8,0.08);border:1px solid rgba(234,179,8,0.35);border-radius:10px;padding:13px 16px;margin-bottom:16px;color:#fde68a;font-size:13px; }
      .iw-entity-banner b { color:#fff; }
      .iw-entity-banner button { background:rgba(234,179,8,0.2);border:1px solid rgba(234,179,8,0.45);color:#fde68a;border-radius:7px;padding:6px 13px;font-size:12px;font-weight:700;cursor:pointer;margin:8px 8px 0 0; }
      .iw-entity-banner button:hover { background:rgba(234,179,8,0.32); }
      .iw-lowmatch-banner { background:rgba(244,63,94,0.07);border:1px solid rgba(244,63,94,0.25);border-radius:10px;padding:11px 16px;margin-bottom:16px;color:#fda4af;font-size:13px; }
    `;
    document.head.appendChild(s);
  },

  _shell(stepNum, bodyHtml, footHtml) {
    document.getElementById('iw-overlay')?.remove();
    const steps = ['Upload File', 'Map Columns', 'Clean & Review'];
    const overlay = document.createElement('div');
    overlay.className = 'iw-overlay';
    overlay.id = 'iw-overlay';
    overlay.innerHTML = `
      <div class="iw-modal">
        <div class="iw-head">
          <div>
            <h3>Import ${this._schema.label}</h3>
            <p>${this._schema.description}</p>
          </div>
          <button class="iw-close" onclick="ImportWizard.close()">✕</button>
        </div>
        <div class="iw-steps">
          ${steps.map((s,i) => `
            <div class="iw-step ${i+1===stepNum?'active':''} ${i+1<stepNum?'done':''}">
              <span class="iw-step-num">${i+1<stepNum?'✓':i+1}</span> ${s}
            </div>`).join('')}
        </div>
        <div class="iw-body">${bodyHtml}</div>
        <div class="iw-foot">${footHtml}</div>
      </div>`;
    document.body.appendChild(overlay);
  },

  close() {
    document.getElementById('iw-overlay')?.remove();
  },

  // ── STEP 1: Upload ──────────────────────────────────────────────────────
  _renderStep1() {
    const fieldList = this._schema.fields.map(f =>
      `<span style="display:inline-block;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.08);border-radius:6px;padding:3px 9px;margin:3px;font-size:12px;color:${f.required?'#fff':'#94a3b8'}">${f.label}${f.required?' *':''}</span>`
    ).join('');

    this._shell(1, `
      <div class="iw-drop" id="iw-drop" onclick="document.getElementById('iw-file').click()">
        <input type="file" id="iw-file" accept=".csv,.xlsx,.xls,.json,.db,.sqlite,.sqlite3" style="display:none" onchange="ImportWizard._onFilePicked(this.files[0])" />
        <div style="font-size:46px;margin-bottom:12px">📁</div>
        <div id="iw-drop-text" style="color:#fff;font-size:16px;font-weight:600;margin-bottom:6px">Drop your file here or click to browse</div>
        <div style="color:#64748b;font-size:13px">Supports CSV, Excel (.xlsx), JSON, and SQLite (.db)</div>
      </div>
      <div style="margin-top:22px">
        <div style="color:#fff;font-size:14px;font-weight:600;margin-bottom:8px">
          Fields you can import <span style="color:#64748b;font-weight:400">(★ = required)</span>
        </div>
        <div>${fieldList}</div>
        <div style="margin-top:14px">
          <button class="iw-tmpl-link" onclick="ImportWizard.downloadTemplate()">⬇ Download a blank CSV template</button>
        </div>
      </div>
    `, `
      <span></span>
      <button class="iw-btn iw-btn-primary" id="iw-next1" disabled onclick="ImportWizard._parseAndMap()">Next: Map Columns →</button>
    `);

    // Drag & drop wiring
    const drop = document.getElementById('iw-drop');
    ['dragover','dragenter'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('drag'); }));
    ['dragleave','drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('drag'); }));
    drop.addEventListener('drop', e => {
      const f = e.dataTransfer.files[0];
      if (f) this._onFilePicked(f);
    });
  },

  _onFilePicked(file) {
    if (!file) return;
    this._file = file;
    document.getElementById('iw-drop-text').textContent = `📄 ${file.name}`;
    document.getElementById('iw-next1').disabled = false;
  },

  downloadTemplate() {
    const headers = this._schema.fields.map(f => f.label);
    const example = this._schema.fields.map(f => f.example || '');
    const csv = headers.join(',') + '\n' + example.map(v => `"${v}"`).join(',') + '\n';
    const blob = new Blob([csv], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${this._system}_${this._entity}_template.csv`;
    a.click();
  },

  // ── STEP 2: Map columns ─────────────────────────────────────────────────
  async _parseAndMap() {
    const btn = document.getElementById('iw-next1');
    if (btn) { btn.disabled = true; btn.textContent = 'Reading file…'; }
    const fd = new FormData();
    fd.append('file', this._file);
    fd.append('system', this._system);
    fd.append('entity', this._entity);
    try {
      const res = await fetch('/api/import/parse', { method:'POST', credentials:'include', body: fd });
      const data = await res.json();
      if (!data.success) {
        (window.SubsystemApp?.showToast || alert)(data.error || 'Could not read file', 'error');
        if (btn) { btn.disabled = false; btn.textContent = 'Next: Map Columns →'; }
        return;
      }
      this._parsed = data;
      // Auto-route: if the uploaded file clearly matches a DIFFERENT data type in
      // this same system better than the one we started on, switch to it
      // automatically (once) — so the user uploads their dataset once and the
      // system figures out which function it belongs to, instead of making them
      // pick. Cross-system matches stay as a manual suggestion banner.
      const sugg = data.entity_suggestions || [];
      const sameSys = sugg.find(s => s.system === this._system);
      if (sameSys && !this._autoRouted) {
        this._autoRouted = true;
        this._autoDetectedLabel = sameSys.label;
        await this._switchEntity(sameSys.system, sameSys.entity);
        return;
      }
      this._mapping = { ...(data.suggested || {}) };
      this._renderStep2();
    } catch (e) {
      (window.SubsystemApp?.showToast || alert)('Error reading file', 'error');
      if (btn) { btn.disabled = false; btn.textContent = 'Next: Map Columns →'; }
    }
  },

  _renderStep2() {
    const cols = this._parsed.columns;
    const samples = this._parsed.samples || {};
    const colOptions = (selected) => '<option value="">— Skip / Not mapped —</option>' +
      cols.map(c => `<option value="${this._esc(c)}" ${c===selected?'selected':''}>${this._esc(c)}</option>`).join('');

    const meta = this._parsed.mapping_meta || {};

    const rows = this._schema.fields.map(f => {
      const sel = this._mapping[f.key] || '';
      const sampleVals = sel && samples[sel] ? samples[sel].slice(0,2).join(', ') : '';
      const notInFile = !sel && !f.required;
      const m = sel && meta[f.key] && meta[f.key].column === sel ? meta[f.key] : null;
      return `
        <div class="iw-map-row">
          <div class="iw-map-field">
            ${f.label}${f.required ? '<span class="req">*</span>' : ''}
            <span class="hint">${f.type}${f.example ? ' · e.g. '+f.example : ''}</span>
          </div>
          <div class="iw-map-arrow">→</div>
          <div>
            <select class="iw-select ${f.required && !sel ? 'unmapped-req' : ''}" data-field="${f.key}" data-req="${f.required}"
              onchange="ImportWizard._onMapChange(this)">
              ${colOptions(sel)}
            </select>
            <span class="iw-conf-wrap" id="iw-conf-${f.key}">${this._confBadge(m)}</span>
            ${sampleVals
              ? `<span class="iw-sample" id="iw-sample-${f.key}">↳ ${this._esc(sampleVals)}</span>`
              : notInFile
                ? `<span class="iw-sample" id="iw-sample-${f.key}" style="color:#475569;">Not found in your file — will be skipped</span>`
                : `<span class="iw-sample" id="iw-sample-${f.key}"></span>`}
          </div>
        </div>`;
    }).join('');

    // Wrong-entity banner — suggest a better-matching data type if one exists.
    const sugg = this._parsed.entity_suggestions || [];
    const entityBanner = sugg.length ? `
      <div class="iw-entity-banner">
        ⚠ This file doesn't look like <b>${this._esc(this._schema.label)}</b>. It matches
        ${sugg.map(s => `<b>${this._esc(s.label)}</b>`).join(' or ')} better.
        <div>${sugg.map(s => `<button onclick="ImportWizard._switchEntity('${s.system}','${s.entity}')">Switch to ${this._esc(s.label)}</button>`).join('')}</div>
      </div>` : '';

    // Low-match banner — warn when required fields couldn't be auto-mapped.
    const unmappedReq = this._schema.fields.filter(f => f.required && !this._mapping[f.key]);
    const lowBanner = (!sugg.length && unmappedReq.length) ? `
      <div class="iw-lowmatch-banner">
        ⚠ ${unmappedReq.length} required field${unmappedReq.length>1?'s':''}
        (${unmappedReq.map(f=>this._esc(f.label)).join(', ')}) couldn't be matched automatically — please pick the right column below.
      </div>` : '';

    // "Importing as" selector — lets the user retarget their uploaded file to a
    // different data type in this system WITHOUT re-uploading. Pre-selected to the
    // auto-detected type. This is what removes the "import the same file per
    // function" friction: upload once, switch the target here if needed.
    const sysEntities = this._schemasCache[this._system] || {};
    const entityKeys = Object.keys(sysEntities);
    const entitySelector = entityKeys.length > 1 ? `
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap">
        <span style="color:#94a3b8;font-size:13px">Importing as:</span>
        <select onchange="ImportWizard._switchEntity('${this._system}', this.value)"
          style="background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.15);border-radius:8px;color:#fff;padding:7px 12px;font-size:13px;outline:none;cursor:pointer">
          ${entityKeys.map(k => `<option value="${this._esc(k)}" ${k===this._entity?'selected':''}>${this._esc(sysEntities[k].label)}</option>`).join('')}
        </select>
        ${this._autoDetectedLabel ? `<span style="color:#5eead4;font-size:12px">✓ auto-detected from your file</span>` : `<span style="color:#64748b;font-size:12px">change if this isn't the right type</span>`}
      </div>` : '';

    this._shell(2, `
      <div style="background:rgba(20,184,166,0.08);border:1px solid rgba(20,184,166,0.2);border-radius:10px;padding:12px 16px;margin-bottom:18px;color:#5eead4;font-size:13px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
        <span>✓ Found <strong>${this._parsed.total}</strong> rows and <strong>${cols.length}</strong> columns.
        We auto-matched your columns below — review and adjust if needed.</span>
        <button class="iw-tmpl-link" style="white-space:nowrap" onclick="ImportWizard.downloadTemplate()">⬇ Download matching template</button>
      </div>
      ${entitySelector}
      ${entityBanner}
      ${lowBanner}
      <div style="display:grid;grid-template-columns:1fr 28px 1fr;gap:12px;padding-bottom:8px;border-bottom:1px solid rgba(255,255,255,0.1);margin-bottom:6px">
        <div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.5px">System Field</div>
        <div></div>
        <div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.5px">Your File Column</div>
      </div>
      ${rows}
    `, `
      <button class="iw-btn iw-btn-ghost" onclick="ImportWizard._renderStep1()">← Back</button>
      <button class="iw-btn iw-btn-primary" id="iw-next2" onclick="ImportWizard._validateAndReview()">Next: Review →</button>
    `);
  },

  // Render a confidence chip from a mapping-meta entry (or 'manual' if null).
  _confBadge(m) {
    if (!m) return '';
    const label = m.confidence === 'high' ? 'auto' : (m.confidence === 'medium' ? 'check' : 'low match');
    const reason = m.reason ? `<span class="iw-conf-reason">${this._esc(m.reason)}</span>` : '';
    return `<span class="iw-conf ${m.confidence}">${label}</span>${reason}`;
  },

  // Re-run the wizard against a different entity/system, keeping the same file.
  async _switchEntity(system, entity) {
    const schema = (this._schemasCache[system] || {})[entity];
    if (!schema) return;
    this._system = system;
    this._entity = entity;
    this._schema = schema;
    await this._parseAndMap();
  },

  _onMapChange(sel) {
    const field = sel.dataset.field;
    const col   = sel.value;
    this._mapping[field] = col || null;
    const sampleEl = document.getElementById('iw-sample-' + field);
    if (sampleEl) {
      if (col) {
        const vals = this._parsed.samples[col] ? this._parsed.samples[col].slice(0,2).join(', ') : '';
        sampleEl.style.color = '';
        sampleEl.textContent = vals ? '↳ ' + vals : '';
      } else if (sel.dataset.req !== 'true') {
        sampleEl.style.color = '#475569';
        sampleEl.textContent = 'Not found in your file — will be skipped';
      } else {
        sampleEl.style.color = '';
        sampleEl.textContent = '';
      }
    }
    if (sel.dataset.req === 'true') {
      sel.classList.toggle('unmapped-req', !col);
    }
    // Reflect the manual choice in the confidence chip.
    const confEl = document.getElementById('iw-conf-' + field);
    if (confEl) {
      const auto = this._parsed.mapping_meta && this._parsed.mapping_meta[field];
      if (!col) {
        confEl.innerHTML = '';
      } else if (auto && auto.column === col) {
        confEl.innerHTML = this._confBadge(auto);
      } else {
        confEl.innerHTML = this._confBadge({ confidence: 'manual', reason: 'Set manually' });
      }
    }
  },

  _validateAndReview() {
    // Ensure all required fields are mapped
    const missing = this._schema.fields.filter(f => f.required && !this._mapping[f.key]);
    if (missing.length) {
      (window.SubsystemApp?.showToast || alert)(
        'Please map these required fields: ' + missing.map(f => f.label).join(', '), 'error');
      return;
    }
    this._renderStep3();
  },

  // ── STEP 3: Clean & Review ──────────────────────────────────────────────
  async _renderStep3() {
    // Show loading while the cleaning pipeline runs on the server.
    this._shell(3, `
      <div style="text-align:center;padding:48px 20px;color:#94a3b8">
        <div class="iw-spinner" style="margin:0 auto 16px"></div>
        <div style="font-size:15px;font-weight:600;color:#fff">Cleaning your data…</div>
        <div style="font-size:13px;margin-top:6px">Trimming, validating, de-duplicating and standardizing records.</div>
      </div>`, `
      <button class="iw-btn iw-btn-ghost" onclick="ImportWizard._renderStep2()">← Back</button>
      <button class="iw-btn iw-btn-primary" disabled style="opacity:.5">Cleaning…</button>
    `);

    let report;
    try {
      const fd = new FormData();
      fd.append('file', this._file);
      fd.append('system', this._system);
      fd.append('entity', this._entity);
      fd.append('mapping', JSON.stringify(this._mapping));
      const res = await fetch('/api/import/clean', { method:'POST', credentials:'include', body: fd });
      report = await res.json();
      if (!report.success) throw new Error(report.error || 'Cleaning failed');
    } catch (e) {
      this._shell(3, `<div style="color:#fca5a5;padding:24px">Could not run cleaning: ${this._esc(e.message)}</div>`,
        `<button class="iw-btn iw-btn-ghost" onclick="ImportWizard._renderStep2()">← Back</button>`);
      return;
    }
    this._cleanReport = report;

    const cat = report.categories || {};
    const badge = (label, n, color) => `
      <div style="flex:1;text-align:center;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:12px 8px">
        <div style="font-size:22px;font-weight:800;color:${n>0?color:'#475569'}">${n||0}</div>
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.4px;margin-top:2px">${label}</div>
      </div>`;

    const catColors = { duplicate:'#f59e0b', unreadable:'#ef4444', missing:'#a855f7', empty:'#64748b' };
    const catLabels = { duplicate:'Duplicate', unreadable:'Error / Unreadable', missing:'Missing Field', empty:'Empty Row' };
    const issuesRows = (report.issues || []).map(it => `
      <div style="display:grid;grid-template-columns:54px 130px 1fr;gap:10px;padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.05);font-size:12.5px;align-items:center">
        <span style="color:#64748b">Row ${it.row}</span>
        <span style="color:${catColors[it.category]||'#94a3b8'};font-weight:700">${catLabels[it.category]||it.category}</span>
        <span style="color:#cbd5e1">${this._esc(it.reason)}</span>
      </div>`).join('');

    const logRows = (report.log || []).map(l => `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-bottom:1px solid rgba(255,255,255,0.04)">
        <div>
          <div style="color:#e2e8f0;font-size:13px;font-weight:600">${this._esc(l.step)}</div>
          <div style="color:#64748b;font-size:11.5px;margin-top:1px">${this._esc(l.detail)}</div>
        </div>
        <div style="color:${l.affected>0?'#14b8a6':'#475569'};font-weight:800;font-size:15px;min-width:40px;text-align:right">${l.affected}</div>
      </div>`).join('');

    const sampleKeys = (report.sample && report.sample[0]) ? Object.keys(report.sample[0]).slice(0,5) : [];
    const sampleTable = sampleKeys.length ? `
      <div style="overflow-x:auto;border:1px solid rgba(255,255,255,0.08);border-radius:10px;margin-top:8px">
        <table style="width:100%;border-collapse:collapse;font-size:12px">
          <thead><tr>${sampleKeys.map(k=>`<th style="text-align:left;padding:7px 10px;color:#94a3b8;border-bottom:1px solid rgba(255,255,255,0.08);text-transform:capitalize">${this._esc(k.replace(/_/g,' '))}</th>`).join('')}</tr></thead>
          <tbody>${report.sample.slice(0,5).map(r=>`<tr>${sampleKeys.map(k=>`<td style="padding:6px 10px;color:#cbd5e1;border-bottom:1px solid rgba(255,255,255,0.04)">${this._esc(r[k]==null?'':r[k])}</td>`).join('')}</tr>`).join('')}</tbody>
        </table>
      </div>` : '';

    this._shell(3, `
      <div style="display:flex;gap:10px;margin-bottom:16px">
        <div style="flex:1;text-align:center;background:rgba(20,184,166,0.08);border:1px solid rgba(20,184,166,0.3);border-radius:12px;padding:14px">
          <div style="font-size:26px;font-weight:800;color:#14b8a6">${report.clean_rows}</div>
          <div style="font-size:11px;color:#5eead4;text-transform:uppercase;letter-spacing:.5px">Clean Records</div>
        </div>
        <div style="flex:1;text-align:center;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:14px">
          <div style="font-size:26px;font-weight:800;color:#fff">${report.total_rows}</div>
          <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px">Rows Read</div>
        </div>
        <div style="flex:1;text-align:center;background:rgba(239,68,68,0.06);border:1px solid rgba(239,68,68,0.2);border-radius:12px;padding:14px">
          <div style="font-size:26px;font-weight:800;color:#f87171">${report.removed_rows}</div>
          <div style="font-size:11px;color:#fca5a5;text-transform:uppercase;letter-spacing:.5px">Removed</div>
        </div>
      </div>

      <div style="color:#fff;font-size:13px;font-weight:700;margin:0 0 8px">Issues found (by category)</div>
      <div style="display:flex;gap:8px;margin-bottom:18px">
        ${badge('Duplicates', cat.duplicate, catColors.duplicate)}
        ${badge('Errors', cat.unreadable, catColors.unreadable)}
        ${badge('Missing', cat.missing, catColors.missing)}
        ${badge('Empty', cat.empty, catColors.empty)}
      </div>

      <div style="color:#fff;font-size:13px;font-weight:700;margin:0 0 6px">🧹 Cleaning Audit Log</div>
      <div style="background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-radius:10px;margin-bottom:18px">${logRows}</div>

      ${(report.issues && report.issues.length) ? `
      <div style="color:#fff;font-size:13px;font-weight:700;margin:0 0 6px">⚠ Removed / Skipped rows (${report.issues.length})</div>
      <div style="max-height:180px;overflow-y:auto;background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-radius:10px;margin-bottom:18px">${issuesRows}</div>` : ''}

      ${sampleTable ? `<div style="color:#fff;font-size:13px;font-weight:700;margin:0 0 4px">✓ Cleaned data preview</div>${sampleTable}` : ''}

      <div id="iw-result" style="margin-top:16px"></div>
    `, `
      <button class="iw-btn iw-btn-ghost" onclick="ImportWizard._renderStep2()">← Back</button>
      <button class="iw-btn iw-btn-primary" id="iw-import-btn" onclick="ImportWizard._execute()" ${report.clean_rows>0?'':'disabled style=opacity:.5'}>Import ${report.clean_rows} Clean Records</button>
    `);
  },

  async _execute() {
    const btn = document.getElementById('iw-import-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Importing…'; }
    const fd = new FormData();
    fd.append('file', this._file);
    fd.append('system', this._system);
    fd.append('entity', this._entity);
    fd.append('mapping', JSON.stringify(this._mapping));
    try {
      const res = await fetch('/api/import/execute', { method:'POST', credentials:'include', body: fd });
      const data = await res.json();
      const resultEl = document.getElementById('iw-result');
      if (data.success) {
        const status   = data.status || 'ok';
        const skipped  = data.skipped || 0;
        const warnings = data.warnings || [];
        const errCount = (data.row_errors || []).length;

        if (status === 'none') {
          // Nothing actually landed — never show a green success here.
          resultEl.innerHTML = `
            <div style="background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:10px;padding:16px;text-align:center">
              <div style="font-size:40px;margin-bottom:8px">⚠️</div>
              <div style="color:#fff;font-size:16px;font-weight:700;margin-bottom:4px">No records imported</div>
              <div style="color:#fca5a5;font-size:14px">${this._esc(data.message || '')}</div>
            </div>`;
        } else {
          const partial = status === 'partial';
          resultEl.innerHTML = `
            <div style="background:rgba(16,185,129,0.1);border:1px solid rgba(16,185,129,0.3);border-radius:10px;padding:16px;text-align:center">
              <div style="font-size:40px;margin-bottom:8px">${partial ? '⚠️' : '✅'}</div>
              <div style="color:#fff;font-size:16px;font-weight:700;margin-bottom:4px">${partial ? 'Imported with warnings' : 'Import Complete'}</div>
              <div style="color:${partial ? '#fbbf24' : '#5eead4'};font-size:14px">${this._esc(data.message || '')}</div>
              ${(data.updated ? `<div style="color:#94a3b8;font-size:13px;margin-top:4px">${data.updated} existing records updated</div>` : '')}
            </div>`;
        }

        // Actionable warnings (e.g. "import Employees first") — always prominent.
        if (warnings.length) {
          resultEl.innerHTML += `<div style="background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.3);border-radius:10px;padding:12px;margin-top:10px">
            ${warnings.map(w => `<div style="color:#fcd34d;font-size:13px;margin:2px 0">⚠ ${this._esc(w)}</div>`).join('')}</div>`;
        }
        if (skipped) {
          resultEl.innerHTML += `<div style="color:#94a3b8;font-size:12px;margin-top:8px">${skipped} row(s) skipped in total.</div>`;
        }
        if (errCount) {
          resultEl.innerHTML += `<div class="iw-err-list">${data.row_errors.map(e =>
            `<div class="iw-err-item">Row ${e.row}: ${e.errors.join('; ')}</div>`).join('')}</div>`;
        }

        if (status === 'none') {
          // Don't pretend it's done — send them back to fix the mapping/order.
          if (btn) { btn.disabled = false; btn.textContent = '← Back to mapping'; btn.onclick = () => ImportWizard._renderStep2(); }
        } else {
          if (btn) { btn.textContent = 'Done'; btn.onclick = () => { ImportWizard.close(); ImportWizard._refreshAfterImport(); }; btn.disabled = false; }
          // Auto-refresh the underlying view + charts after a moment
          setTimeout(() => { try { ImportWizard._refreshAfterImport(); } catch(e){} }, 1500);
        }
      } else {
        resultEl.innerHTML = `
          <div style="background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:10px;padding:16px">
            <div style="color:#fca5a5;font-size:14px;font-weight:600">Import failed</div>
            <div style="color:#94a3b8;font-size:13px;margin-top:4px">${data.error || 'Unknown error'}</div>
          </div>`;
        if (data.row_errors && data.row_errors.length) {
          resultEl.innerHTML += `<div class="iw-err-list">${data.row_errors.map(e =>
            `<div class="iw-err-item">Row ${e.row}: ${e.errors.join('; ')}</div>`).join('')}</div>`;
        }
        if (btn) { btn.disabled = false; btn.textContent = 'Retry'; }
      }
    } catch (e) {
      (window.SubsystemApp?.showToast || alert)('Network error during import', 'error');
      if (btn) { btn.disabled = false; btn.textContent = 'Retry'; }
    }
  },

  // Refresh the data view AND live charts after an import so the dashboard
  // reacts immediately to the newly uploaded records (all data is read live
  // from the subsystem DB, so re-rendering re-fetches and redraws charts).
  _refreshAfterImport() {
    // Re-entrancy guard: never allow this to run nested (defends against an
    // onDone callback that loops back here, which would recurse and storm the UI).
    if (this._refreshing) return;
    this._refreshing = true;
    try {
      // 1. Reload the specific list/view the import was opened from.
      try { this._onDone(); } catch (e) {}

      // 2. Re-render the active subsystem section so its charts re-query the DB.
      const app = window.SubsystemApp;
      if (app && app.active && app.currentSection && typeof app._navigate === 'function') {
        try { app._navigate(app.currentSection); } catch (e) {}
      }
    } finally {
      // Release on the next tick so a synchronous loop can't slip through.
      setTimeout(() => { this._refreshing = false; }, 0);
    }
  },

  _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  },
};

window.ImportWizard = ImportWizard;
