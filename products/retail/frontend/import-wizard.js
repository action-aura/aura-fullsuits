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

  // AuraI18n.t() explicitly, not the DOM sweep: i18n.js only translates a
  // text node whose FULL trimmed text matches a dictionary key, so any
  // sentence with a SKU or a quantity concatenated onto it would silently
  // stay English in Arabic. Every string below that mixes prose with data
  // translates the fixed half through this and appends the rest.
  _t(s) { return window.AuraI18n ? AuraI18n.t(s) : s; },

  // Entry point from the subsystem header — goes straight to file upload.
  // The mapping step auto-detects which entity the file belongs to via
  // entity_suggestions, so no upfront category picker is needed.
  async openForSystem(system) {
    if (!window.IS_STANDALONE || window.isDemoMode) {
      (window.SubsystemApp?.showToast || window.alert)(
        this._t('Data import is available in your downloaded system, not in the demo.'), 'info');
      return;
    }
    try {
      if (!this._schemasCache) {
        const res = await fetch('/api/import/schemas', { credentials: 'include' });
        const data = await res.json();
        this._schemasCache = data.schemas || {};
      }
    } catch (e) {
      (window.SubsystemApp?.showToast || window.alert)(this._t('Could not load import config'), 'error');
      return;
    }
    const entities = this._schemasCache[system] || {};
    const keys = Object.keys(entities);
    if (keys.length === 0) {
      (window.SubsystemApp?.showToast || window.alert)(this._t('No importable data types for this system.'), 'info');
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
        this._t('Data import is available in your downloaded system, not in the demo.'), 'info');
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
          <div><h3>${this._t('Smart Import')}</h3><p>${this._t('Upload one file — we route it to the right functions')}</p></div>
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
        <div id="iw-sdrop-text" style="color:var(--text-primary);font-size:16px;font-weight:600;margin-bottom:6px">${this._t('Drop a file here or click to browse')}</div>
        <div style="color:var(--text-tertiary);font-size:13px">${this._t("CSV, Excel or JSON — combined or single-type, we'll figure it out")}</div>
      </div>
    `, `
      <span></span>
      <button class="iw-btn iw-btn-primary" id="iw-sdetect" disabled onclick="ImportWizard._smartDetect()">${this._t('Analyze File ›')}</button>
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
    if (btn) { btn.disabled = true; btn.textContent = this._t('Analyzing…'); }
    const fd = new FormData();
    fd.append('file', this._smartFile);
    if (this._smartSystems) fd.append('systems', this._smartSystems.join(','));
    try {
      const res = await fetch('/api/import/detect', { method:'POST', credentials:'include', body: fd });
      const data = await res.json();
      if (!data.success) {
        (window.SubsystemApp?.showToast || alert)(data.error || this._t('Could not analyze file'), 'error');
        if (btn) { btn.disabled = false; btn.textContent = this._t('Analyze File ›'); }
        return;
      }
      this._smartParsed = data;
      this._smartTargets = (data.detected || []).map(d => ({
        system: d.system, entity: d.entity, label: d.label,
        include: true, mapping: { ...d.suggested }, fields: d.fields,
      }));
      this._smartRenderReview();
    } catch (e) {
      (window.SubsystemApp?.showToast || alert)(this._t('Error analyzing file'), 'error');
      if (btn) { btn.disabled = false; btn.textContent = this._t('Analyze File ›'); }
    }
  },

  _smartRenderReview() {
    const d = this._smartParsed;
    if (!this._smartTargets.length) {
      this._smartShell(
        `<div style="padding:24px;text-align:center;color:var(--state-danger-text)">${this._t("We couldn't recognize any importable data types in this file.")}<br>
          <span style="color:var(--text-tertiary);font-size:13px">${this._t("Use a function's own Import button and map the columns manually.")}</span></div>`,
        `<button class="iw-btn iw-btn-ghost" onclick="ImportWizard._smartStep1()">${this._t('‹ Back')}</button><span></span>`);
      return;
    }
    const cols = d.columns;
    const cards = this._smartTargets.map((t, i) => {
      const mappedCount = t.fields.filter(f => t.mapping[f.key]).length;
      const rows = t.fields.map(f => {
        const sel = t.mapping[f.key] || '';
        return `<div style="display:grid;grid-template-columns:1fr 20px 1fr;gap:8px;align-items:center;padding:4px 0">
          <span style="color:var(--text-secondary);font-size:13px">${this._esc(f.label)}${f.required ? '<span style="color:var(--state-danger-text)">*</span>' : ''}</span>
          <span style="color:var(--text-tertiary);text-align:center">→</span>
          <select class="iw-select" onchange="ImportWizard._smartMap(${i},'${f.key}',this.value)" style="padding:6px 10px;font-size:12px">
            <option value="">${this._t('— skip —')}</option>
            ${cols.map(c => `<option value="${this._esc(c)}" ${c === sel ? 'selected' : ''}>${this._esc(c)}</option>`).join('')}
          </select></div>`;
      }).join('');
      return `<div style="border:1px solid var(--border-default);border-radius:12px;padding:14px 16px;margin-bottom:12px;background:var(--surface-raised)">
        <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
          <input type="checkbox" ${t.include ? 'checked' : ''} onchange="ImportWizard._smartToggle(${i},this.checked)" style="width:16px;height:16px">
          <span style="color:var(--text-primary);font-weight:700;font-size:15px">${this._esc(t.label)}</span>
          <span style="color:var(--accent-action);font-size:12px">${mappedCount} ${this._t('field(s) detected')}</span>
        </label>
        <details style="margin-inline-start:26px;margin-top:6px"><summary style="color:var(--text-tertiary);font-size:12px;cursor:pointer">${this._t('Review / adjust mapping')}</summary>
          <div style="margin-top:8px">${rows}</div></details>
      </div>`;
    }).join('');
    const order = this._smartTargets.filter(t => t.include).map(t => t.label).join(' → ');
    this._smartShell(`
      <div style="background:var(--surface-accent-soft);border:1px solid var(--border-default);border-radius:10px;padding:12px 16px;margin-bottom:16px;color:var(--accent-action);font-size:13px">
        ✓ ${this._t('Found')} <strong>${d.total}</strong> ${this._t('rows. This file maps to')} <strong>${this._smartTargets.length}</strong> ${this._t('function(s) — review and import them all at once.')}
      </div>
      ${cards}
      <div style="color:var(--text-tertiary);font-size:12px">${this._t('Import order:')} ${this._esc(order || '—')}</div>
      <div id="iw-sresult" style="margin-top:14px"></div>
    `, `
      <button class="iw-btn iw-btn-ghost" onclick="ImportWizard._smartStep1()">${this._t('‹ Back')}</button>
      <button class="iw-btn iw-btn-primary" id="iw-sgo" onclick="ImportWizard._smartExecute()">${this._t('Import All Selected')}</button>
    `);
  },

  _smartToggle(i, on) { if (this._smartTargets[i]) this._smartTargets[i].include = on; },
  _smartMap(i, fkey, col) { if (this._smartTargets[i]) this._smartTargets[i].mapping[fkey] = col || null; },

  async _smartExecute() {
    const targets = this._smartTargets.filter(t => t.include)
      .map(t => ({ system: t.system, entity: t.entity, mapping: t.mapping }));
    if (!targets.length) { (window.SubsystemApp?.showToast || alert)('Select at least one type to import', 'info'); return; }
    const btn = document.getElementById('iw-sgo'); if (btn) { btn.disabled = true; btn.textContent = this._t('Importing…'); }
    const fd = new FormData(); fd.append('file', this._smartFile); fd.append('targets', JSON.stringify(targets));
    try {
      const res = await fetch('/api/import/smart-execute', { method:'POST', credentials:'include', body: fd });
      const data = await res.json();
      const el = document.getElementById('iw-sresult');
      if (data.success) {
        // Smart-import runs the SAME handlers as the single-entity path, so a
        // refused opening-stock declaration has to be reported here too --
        // otherwise this route becomes the silent-discard the other one just
        // stopped being.
        const rowsHtml = data.results.map(r => `<div style="padding:6px 0;border-bottom:1px solid var(--border-hairline);font-size:13px">
          <div style="display:flex;justify-content:space-between">
            <span style="color:var(--text-primary)">${this._esc(r.label || r.entity)}</span>
            <span style="color:${r.error ? 'var(--state-danger-text)' : 'var(--accent-action)'}">${r.error ? this._esc(r.error) : ((r.imported || 0) + ' imported' + (r.updated ? ', ' + r.updated + ' updated' : '') + (r.skipped ? ', ' + r.skipped + ' skipped' : ''))}</span>
          </div>
          ${(r.stock_errors || []).length ? `<div style="margin-top:4px">
            <div style="color:var(--state-danger-text);font-size:12px;font-weight:600">${this._esc(this._t('Stock was left unchanged for these products:'))}</div>
            ${r.stock_errors.map(e => `<div style="color:var(--state-danger-text);font-size:12px;margin:2px 0">
              <b>${this._esc(e.sku)}</b> — ${this._esc(this._t(e.reason))} (${this._esc(e.declared)}${e.would_be == null ? '' : ' → ' + this._esc(e.would_be)}, ${this._esc(this._t('currently on hand'))} ${this._esc(e.on_hand)})
            </div>`).join('')}
          </div>` : ''}
          ${(r.parent_errors || []).length ? `<div style="margin-top:4px">
            <div style="color:var(--state-danger-text);font-size:12px;font-weight:600">${this._esc(this._t('Parent SKU link could not be made for these products:'))}</div>
            ${r.parent_errors.map(e => `<div style="color:var(--state-danger-text);font-size:12px;margin:2px 0">
              <b>${this._esc(e.sku)}</b> → <b>${this._esc(e.parent_sku)}</b>: ${this._esc(this._t(e.reason))}
            </div>`).join('')}
          </div>` : ''}
        </div>`).join('');
        el.innerHTML = `<div style="background:var(--state-success-surface);border:1px solid var(--state-success-border);border-radius:10px;padding:14px">
          <div style="color:var(--text-primary);font-weight:700;margin-bottom:8px">✅ Imported ${data.total_imported} records across ${data.results.length} function(s)</div>${rowsHtml}</div>`;
        if (btn) { btn.textContent = this._t('Done'); btn.disabled = false; btn.onclick = () => { ImportWizard.close(); try { if (window.SubsystemApp && SubsystemApp.active) SubsystemApp._navigate(SubsystemApp.currentSection); } catch (e) {} }; }
      } else {
        el.innerHTML = `<div style="background:var(--state-danger-surface);border:1px solid var(--state-danger-border);border-radius:10px;padding:14px;color:var(--state-danger-text)">${this._esc(data.error || 'Import failed')}</div>`;
        if (btn) { btn.disabled = false; btn.textContent = this._t('Retry'); }
      }
    } catch (e) {
      (window.SubsystemApp?.showToast || alert)(this._t('Network error during import'), 'error');
      if (btn) { btn.disabled = false; btn.textContent = this._t('Retry'); }
    }
  },

  async open(system, entity, onDone) {
    // ── Standalone-only gate ──────────────────────────────────────────────────
    // Live Excel/CSV import is a feature of the downloaded customer system only.
    // It must NEVER be available on the demo portal.
    if (!window.IS_STANDALONE || window.isDemoMode) {
      (window.SubsystemApp?.showToast || window.alert)(
        this._t('Data import is available in your downloaded system, not in the demo.'), 'info');
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
        (window.SubsystemApp?.showToast || window.alert)(this._t('Import not available for this section'), 'error');
        return;
      }
    } catch (e) {
      (window.SubsystemApp?.showToast || window.alert)(this._t('Could not load import config'), 'error');
      return;
    }

    this._injectStyles();
    this._renderStep1();
  },

  // AUDIT -- this injected a COMPLETE SECOND DESIGN SYSTEM over a page
  // index.html has already styled: a near-black slate modal on a near-opaque
  // slate scrim, pure-white headings, 5%-white inputs, and the aurora teal
  // pair as the accent -- the teal DESIGN.md §4.3 retired on 2026-09-08 for
  // reading "as a developer tool, not a Levantine retail product". Roughly
  // 160 paint literals in this one file. Opening Import from Products,
  // Customers or Suppliers dropped that dark island over a light-theme till,
  // in all five themes. (The old hex values are deliberately NOT quoted here:
  // the guard added with this fix refuses ANY hex in the wizard's own output,
  // and a comment naming one would be indistinguishable from a relapse.)
  //
  // This is EXACTLY the .ret-modal defect subsystem-retail.js already records
  // and fixed ("a surface whose palette disagrees with the app's means every
  // rule must know which of the two it is on, and eventually one of them
  // forgets"), so the cure is the same one: every value below is now a token,
  // so a rule written anywhere in the product is correct in here too, and the
  // wizard follows whichever of the five themes the shop actually chose.
  //
  // Nothing could see it: retail_design_tokens_test.js scans css/main.css only
  // and reported PASS. retail_toast_tokens_test.js's sibling check now runs
  // this real function and reads the stylesheet it actually builds.
  _injectStyles() {
    if (document.getElementById('iw-styles')) return;
    const s = document.createElement('style');
    s.id = 'iw-styles';
    s.textContent = `
      .iw-overlay { position:fixed;inset:0;background:var(--surface-scrim);display:flex;align-items:center;justify-content:center;z-index:100000;backdrop-filter:blur(6px); }
      .iw-modal { background:var(--surface-panel);border:1px solid var(--border-soft);border-radius:18px;width:720px;max-width:94vw;max-height:90vh;display:flex;flex-direction:column;box-shadow:var(--elevation-modal); }
      .iw-head { padding:24px 28px;border-bottom:1px solid var(--border-soft);display:flex;justify-content:space-between;align-items:center; }
      .iw-head h3 { margin:0;color:var(--text-primary);font-size:20px;font-weight:700; }
      .iw-head p { margin:4px 0 0;color:var(--text-muted);font-size:13px; }
      .iw-close { background:none;border:none;color:var(--text-tertiary);font-size:22px;cursor:pointer;line-height:1; }
      .iw-steps { display:flex;gap:8px;padding:16px 28px;border-bottom:1px solid var(--border-hairline); }
      .iw-step { flex:1;display:flex;align-items:center;gap:8px;color:var(--text-tertiary);font-size:13px;font-weight:600; }
      .iw-step.active { color:var(--accent-action); }
      .iw-step.done { color:var(--state-success-text); }
      .iw-step-num { width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;border:1.5px solid currentColor; }
      .iw-body { padding:28px;overflow-y:auto;flex:1; }
      .iw-spinner { width:38px;height:38px;border:3px solid var(--border-default);border-top-color:var(--accent-action);border-radius:50%;animation:iw-spin 0.8s linear infinite; }
      @keyframes iw-spin { to { transform:rotate(360deg); } }
      .iw-foot { padding:18px 28px;border-top:1px solid var(--border-soft);display:flex;justify-content:space-between;gap:10px; }
      .iw-btn { padding:11px 22px;border-radius:9px;font-weight:600;font-size:14px;cursor:pointer;border:none;transition:.2s; }
      /* Flat accent, not the old teal gradient: DESIGN.md §2.1 -- one accent,
         used semantically, never decoration. --text-on-accent is the only text
         token contrast-solved against an accent fill in all five themes. */
      .iw-btn-primary { background:var(--accent-action);color:var(--text-on-accent); }
      .iw-btn-primary:hover { background:var(--accent-action-hover); }
      .iw-btn-primary:disabled { opacity:.4;cursor:not-allowed; }
      .iw-btn-ghost { background:var(--surface-hover);color:var(--text-secondary);border:1px solid var(--border-default); }
      .iw-drop { border:2px dashed var(--border-default);border-radius:14px;padding:48px 20px;text-align:center;cursor:pointer;transition:.2s; }
      .iw-drop:hover,.iw-drop.drag { border-color:var(--accent-action);background:var(--surface-accent-soft); }
      .iw-map-row { display:grid;grid-template-columns:1fr 28px 1fr;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid var(--border-hairline); }
      .iw-map-field { color:var(--text-primary);font-size:14px;font-weight:600; }
      .iw-map-field .req { color:var(--state-danger-text);margin-inline-start:3px; }
      .iw-map-field .hint { display:block;color:var(--text-tertiary);font-size:11px;font-weight:400;margin-top:2px; }
      .iw-map-arrow { color:var(--text-tertiary);text-align:center; }
      .iw-select { width:100%;background:var(--surface-sunken);border:1px solid var(--border-soft);border-radius:8px;color:var(--text-primary);padding:9px 12px;font-size:13px;outline:none; }
      .iw-select:focus { border-color:var(--accent-action); }
      .iw-select.unmapped-req { border-color:var(--state-danger-border); }
      .iw-sample { display:block;color:var(--state-success-text);font-size:11px;margin-top:3px;font-family:monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }
      .iw-summary-card { background:var(--surface-raised);border-radius:12px;padding:20px;margin-bottom:16px; }
      .iw-stat { display:inline-block;text-align:center;padding:0 24px; }
      .iw-stat-num { font-size:32px;font-weight:800;color:var(--accent-action); }
      .iw-stat-lbl { font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px; }
      .iw-err-list { max-height:180px;overflow-y:auto;background:var(--state-danger-surface);border:1px solid var(--state-danger-border);border-radius:10px;padding:12px;margin-top:14px; }
      .iw-err-item { color:var(--state-danger-text);font-size:12px;padding:4px 0;border-bottom:1px solid var(--state-danger-border); }
      .iw-tmpl-link { color:var(--accent-action);font-size:13px;cursor:pointer;text-decoration:underline;background:none;border:none; }
      .iw-conf { display:inline-block;font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px;margin-inline-start:8px;vertical-align:middle;text-transform:uppercase;letter-spacing:.3px; }
      .iw-conf.high { background:var(--state-success-surface);color:var(--state-success-text); }
      .iw-conf.medium { background:var(--state-warning-surface);color:var(--state-warning-text); }
      .iw-conf.low { background:var(--state-danger-surface);color:var(--state-danger-text); }
      .iw-conf.manual { background:var(--state-info-surface);color:var(--state-info-text); }
      .iw-conf-reason { color:var(--text-tertiary);font-size:11px;margin-top:3px;display:block; }
      .iw-entity-banner { background:var(--state-warning-surface);border:1px solid var(--state-warning-border);border-radius:10px;padding:13px 16px;margin-bottom:16px;color:var(--state-warning-text);font-size:13px; }
      .iw-entity-banner b { color:var(--text-primary); }
      /* margin-block-start + margin-inline-end, not the old four-value margin
         shorthand: its fourth value is a physical LEFT margin, so in Arabic the
         gap landed on the wrong side of the button row. */
      .iw-entity-banner button { background:var(--state-warning-surface);border:1px solid var(--state-warning-border);color:var(--state-warning-text);border-radius:7px;padding:6px 13px;font-size:12px;font-weight:700;cursor:pointer;margin-block-start:8px;margin-inline-end:8px; }
      .iw-entity-banner button:hover { background:var(--surface-hover); }
      .iw-lowmatch-banner { background:var(--state-danger-surface);border:1px solid var(--state-danger-border);border-radius:10px;padding:11px 16px;margin-bottom:16px;color:var(--state-danger-text);font-size:13px; }
    `;
    document.head.appendChild(s);
  },

  _shell(stepNum, bodyHtml, footHtml) {
    document.getElementById('iw-overlay')?.remove();
    const steps = [this._t('Upload File'), this._t('Map Columns'), this._t('Clean & Review')];
    const overlay = document.createElement('div');
    overlay.className = 'iw-overlay';
    overlay.id = 'iw-overlay';
    overlay.innerHTML = `
      <div class="iw-modal">
        <div class="iw-head">
          <div>
            <h3>${this._t('Import')} ${this._esc(this._t(this._schema.label))}</h3>
            <p>${this._esc(this._t(this._schema.description))}</p>
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
      `<span title="${this._esc(this._t(f.help || ''))}" style="display:inline-block;background:var(--surface-sunken);border:1px solid var(--border-soft);border-radius:6px;padding:3px 9px;margin:3px;font-size:12px;color:${f.required?'var(--text-primary)':'var(--text-muted)'}">${this._esc(this._t(f.label))}${f.required?' *':''}</span>`
    ).join('');

    this._shell(1, `
      <div class="iw-drop" id="iw-drop" onclick="document.getElementById('iw-file').click()">
        <input type="file" id="iw-file" accept=".csv,.xlsx,.xls,.json,.db,.sqlite,.sqlite3" style="display:none" onchange="ImportWizard._onFilePicked(this.files[0])" />
        <div style="font-size:46px;margin-bottom:12px">📁</div>
        <div id="iw-drop-text" style="color:var(--text-primary);font-size:16px;font-weight:600;margin-bottom:6px">${this._t('Drop your file here or click to browse')}</div>
        <div style="color:var(--text-tertiary);font-size:13px">${this._t('Supports CSV, Excel (.xlsx), JSON, and SQLite (.db)')}</div>
      </div>
      <div style="margin-top:22px">
        <div style="color:var(--text-primary);font-size:14px;font-weight:600;margin-bottom:8px">
          ${this._t('Fields you can import')} <span style="color:var(--text-tertiary);font-weight:400">${this._t('(★ = required)')}</span>
        </div>
        <div>${fieldList}</div>
        <div style="margin-top:14px">
          <button class="iw-tmpl-link" onclick="ImportWizard.downloadTemplate()">⬇ ${this._t('Download a blank CSV template')}</button>
        </div>
      </div>
    `, `
      <span></span>
      <button class="iw-btn iw-btn-primary" id="iw-next1" disabled onclick="ImportWizard._parseAndMap()">${this._t('Next: Map Columns ›')}</button>
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
        if (btn) { btn.disabled = false; btn.textContent = this._t('Next: Map Columns ›'); }
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
      (window.SubsystemApp?.showToast || alert)(this._t('Error reading file'), 'error');
      if (btn) { btn.disabled = false; btn.textContent = this._t('Next: Map Columns ›'); }
    }
  },

  _renderStep2() {
    const cols = this._parsed.columns;
    const samples = this._parsed.samples || {};
    const colOptions = (selected) => `<option value="">${this._t('— Skip / Not mapped —')}</option>` +
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
            ${this._esc(this._t(f.label))}${f.required ? '<span class="req">*</span>' : ''}
            <span class="hint">${f.type}${f.example ? ' · e.g. '+f.example : ''}</span>
            ${f.help ? `<span class="hint" style="color:var(--text-muted);white-space:normal;line-height:1.45;margin-top:4px;display:block">${this._esc(this._t(f.help))}</span>` : ''}
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
                ? `<span class="iw-sample" id="iw-sample-${f.key}" style="color:var(--text-tertiary);">${this._t('Not found in your file — will be skipped')}</span>`
                : `<span class="iw-sample" id="iw-sample-${f.key}"></span>`}
          </div>
        </div>`;
    }).join('');

    // Wrong-entity banner — suggest a better-matching data type if one exists.
    const sugg = this._parsed.entity_suggestions || [];
    const entityBanner = sugg.length ? `
      <div class="iw-entity-banner">
        ⚠ ${this._t("This file doesn't look like")} <b>${this._esc(this._t(this._schema.label))}</b>. ${this._t('It matches')}
        ${sugg.map(s => `<b>${this._esc(this._t(s.label))}</b>`).join(' ' + this._t('or') + ' ')} ${this._t('better.')}
        <div>${sugg.map(s => `<button onclick="ImportWizard._switchEntity('${s.system}','${s.entity}')">${this._t('Switch to')} ${this._esc(this._t(s.label))}</button>`).join('')}</div>
      </div>` : '';

    // Low-match banner — warn when required fields couldn't be auto-mapped.
    const unmappedReq = this._schema.fields.filter(f => f.required && !this._mapping[f.key]);
    const lowBanner = (!sugg.length && unmappedReq.length) ? `
      <div class="iw-lowmatch-banner">
        ⚠ ${unmappedReq.length} ${this._t(unmappedReq.length>1 ? 'required fields' : 'required field')}
        (${unmappedReq.map(f=>this._esc(this._t(f.label))).join(', ')}) ${this._t("couldn't be matched automatically — please pick the right column below.")}
      </div>` : '';

    // "Importing as" selector — lets the user retarget their uploaded file to a
    // different data type in this system WITHOUT re-uploading. Pre-selected to the
    // auto-detected type. This is what removes the "import the same file per
    // function" friction: upload once, switch the target here if needed.
    const sysEntities = this._schemasCache[this._system] || {};
    const entityKeys = Object.keys(sysEntities);
    const entitySelector = entityKeys.length > 1 ? `
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap">
        <span style="color:var(--text-muted);font-size:13px">${this._t('Importing as:')}</span>
        <select onchange="ImportWizard._switchEntity('${this._system}', this.value)"
          style="background:var(--surface-sunken);border:1px solid var(--border-default);border-radius:8px;color:var(--text-primary);padding:7px 12px;font-size:13px;outline:none;cursor:pointer">
          ${entityKeys.map(k => `<option value="${this._esc(k)}" ${k===this._entity?'selected':''}>${this._esc(sysEntities[k].label)}</option>`).join('')}
        </select>
        ${this._autoDetectedLabel ? `<span style="color:var(--accent-action);font-size:12px">✓ ${this._t('auto-detected from your file')}</span>` : `<span style="color:var(--text-tertiary);font-size:12px">${this._t("change if this isn't the right type")}</span>`}
      </div>` : '';

    this._shell(2, `
      <div style="background:var(--surface-accent-soft);border:1px solid var(--border-default);border-radius:10px;padding:12px 16px;margin-bottom:18px;color:var(--accent-action);font-size:13px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
        <span>✓ ${this._t('Found')} <strong>${this._parsed.total}</strong> ${this._t('rows and')} <strong>${cols.length}</strong> ${this._t('columns.')}
        ${this._t('We auto-matched your columns below — review and adjust if needed.')}</span>
        <button class="iw-tmpl-link" style="white-space:nowrap" onclick="ImportWizard.downloadTemplate()">⬇ ${this._t('Download matching template')}</button>
      </div>
      ${entitySelector}
      ${entityBanner}
      ${lowBanner}
      <div style="display:grid;grid-template-columns:1fr 28px 1fr;gap:12px;padding-bottom:8px;border-bottom:1px solid var(--border-default);margin-bottom:6px">
        <div style="color:var(--text-tertiary);font-size:11px;text-transform:uppercase;letter-spacing:.5px">${this._t('System Field')}</div>
        <div></div>
        <div style="color:var(--text-tertiary);font-size:11px;text-transform:uppercase;letter-spacing:.5px">${this._t('Your File Column')}</div>
      </div>
      ${rows}
    `, `
      <button class="iw-btn iw-btn-ghost" onclick="ImportWizard._renderStep1()">${this._t('‹ Back')}</button>
      <button class="iw-btn iw-btn-primary" id="iw-next2" onclick="ImportWizard._validateAndReview()">${this._t('Next: Review ›')}</button>
    `);
  },

  // Render a confidence chip from a mapping-meta entry (or 'manual' if null).
  _confBadge(m) {
    if (!m) return '';
    const label = this._t(m.confidence === 'high' ? 'auto' : (m.confidence === 'medium' ? 'check' : 'low match'));
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
        sampleEl.style.color = 'var(--text-tertiary)';
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
        this._t('Please map these required fields:') + ' ' + missing.map(f => this._t(f.label)).join(', '), 'error');
      return;
    }
    this._renderStep3();
  },

  // ── STEP 3: Clean & Review ──────────────────────────────────────────────
  async _renderStep3() {
    // Show loading while the cleaning pipeline runs on the server.
    this._shell(3, `
      <div style="text-align:center;padding:48px 20px;color:var(--text-muted)">
        <div class="iw-spinner" style="margin:0 auto 16px"></div>
        <div style="font-size:15px;font-weight:600;color:var(--text-primary)">${this._t('Cleaning your data…')}</div>
        <div style="font-size:13px;margin-top:6px">${this._t('Trimming, validating, de-duplicating and standardizing records.')}</div>
      </div>`, `
      <button class="iw-btn iw-btn-ghost" onclick="ImportWizard._renderStep2()">${this._t('‹ Back')}</button>
      <button class="iw-btn iw-btn-primary" disabled style="opacity:.5">${this._t('Cleaning…')}</button>
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
      this._shell(3, `<div style="color:var(--state-danger-text);padding:24px">${this._t('Could not run cleaning:')} ${this._esc(e.message)}</div>`,
        `<button class="iw-btn iw-btn-ghost" onclick="ImportWizard._renderStep2()">${this._t('‹ Back')}</button>`);
      return;
    }
    this._cleanReport = report;

    const cat = report.categories || {};
    const badge = (label, n, color) => `
      <div style="flex:1;text-align:center;background:var(--surface-raised);border:1px solid var(--border-soft);border-radius:10px;padding:12px 8px">
        <div style="font-size:22px;font-weight:800;color:${n>0?color:'var(--text-tertiary)'}">${n||0}</div>
        <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.4px;margin-top:2px">${label}</div>
      </div>`;

    const catColors = { duplicate:'var(--state-warning-text)', unreadable:'var(--state-danger-text)', missing:'var(--state-info-text)', empty:'var(--text-tertiary)' };
    const catLabels = { duplicate:this._t('Duplicate'), unreadable:this._t('Error / Unreadable'), missing:this._t('Missing Field'), empty:this._t('Empty Row') };
    const issuesRows = (report.issues || []).map(it => `
      <div style="display:grid;grid-template-columns:54px 130px 1fr;gap:10px;padding:7px 10px;border-bottom:1px solid var(--border-hairline);font-size:12.5px;align-items:center">
        <span style="color:var(--text-tertiary)">${this._t('Row')} ${it.row}</span>
        <span style="color:${catColors[it.category]||'var(--text-muted)'};font-weight:700">${catLabels[it.category]||it.category}</span>
        <span style="color:var(--text-secondary)">${this._esc(it.reason)}</span>
      </div>`).join('');

    const logRows = (report.log || []).map(l => `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-bottom:1px solid var(--border-hairline)">
        <div>
          <div style="color:var(--text-primary);font-size:13px;font-weight:600">${this._esc(l.step)}</div>
          <div style="color:var(--text-tertiary);font-size:11.5px;margin-top:1px">${this._esc(l.detail)}</div>
        </div>
        <div style="color:${l.affected>0?'var(--accent-action)':'var(--text-tertiary)'};font-weight:800;font-size:15px;min-width:40px;text-align:end">${l.affected}</div>
      </div>`).join('');

    const sampleKeys = (report.sample && report.sample[0]) ? Object.keys(report.sample[0]).slice(0,5) : [];
    const sampleTable = sampleKeys.length ? `
      <div style="overflow-x:auto;border:1px solid var(--border-soft);border-radius:10px;margin-top:8px">
        <table style="width:100%;border-collapse:collapse;font-size:12px">
          <thead><tr>${sampleKeys.map(k=>`<th style="text-align:start;padding:7px 10px;color:var(--text-muted);border-bottom:1px solid var(--border-hairline);text-transform:capitalize">${this._esc(k.replace(/_/g,' '))}</th>`).join('')}</tr></thead>
          <tbody>${report.sample.slice(0,5).map(r=>`<tr>${sampleKeys.map(k=>`<td style="padding:6px 10px;color:var(--text-secondary);border-bottom:1px solid var(--border-hairline)">${this._esc(r[k]==null?'':r[k])}</td>`).join('')}</tr>`).join('')}</tbody>
        </table>
      </div>` : '';

    this._shell(3, `
      <div style="display:flex;gap:10px;margin-bottom:16px">
        <div style="flex:1;text-align:center;background:var(--surface-accent-soft);border:1px solid var(--border-default);border-radius:12px;padding:14px">
          <div style="font-size:26px;font-weight:800;color:var(--accent-action)">${report.clean_rows}</div>
          <div style="font-size:11px;color:var(--accent-action);text-transform:uppercase;letter-spacing:.5px">${this._t('Clean Records')}</div>
        </div>
        <div style="flex:1;text-align:center;background:var(--surface-raised);border:1px solid var(--border-soft);border-radius:12px;padding:14px">
          <div style="font-size:26px;font-weight:800;color:var(--text-primary)">${report.total_rows}</div>
          <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px">${this._t('Rows Read')}</div>
        </div>
        <div style="flex:1;text-align:center;background:var(--state-danger-surface);border:1px solid var(--state-danger-border);border-radius:12px;padding:14px">
          <div style="font-size:26px;font-weight:800;color:var(--state-danger-text)">${report.removed_rows}</div>
          <div style="font-size:11px;color:var(--state-danger-text);text-transform:uppercase;letter-spacing:.5px">${this._t('Removed')}</div>
        </div>
      </div>

      <div style="color:var(--text-primary);font-size:13px;font-weight:700;margin:0 0 8px">${this._t('Issues found (by category)')}</div>
      <div style="display:flex;gap:8px;margin-bottom:18px">
        ${badge(this._t('Duplicates'), cat.duplicate, catColors.duplicate)}
        ${badge(this._t('Errors'), cat.unreadable, catColors.unreadable)}
        ${badge(this._t('Missing'), cat.missing, catColors.missing)}
        ${badge(this._t('Empty'), cat.empty, catColors.empty)}
      </div>

      <div style="color:var(--text-primary);font-size:13px;font-weight:700;margin:0 0 6px">🧹 ${this._t('Cleaning Audit Log')}</div>
      <div style="background:var(--surface-raised);border:1px solid var(--border-soft);border-radius:10px;margin-bottom:18px">${logRows}</div>

      ${(report.issues && report.issues.length) ? `
      <div style="color:var(--text-primary);font-size:13px;font-weight:700;margin:0 0 6px">⚠ ${this._t('Removed / Skipped rows')} (${report.issues.length})</div>
      <div style="max-height:180px;overflow-y:auto;background:var(--surface-raised);border:1px solid var(--border-soft);border-radius:10px;margin-bottom:18px">${issuesRows}</div>` : ''}

      ${sampleTable ? `<div style="color:var(--text-primary);font-size:13px;font-weight:700;margin:0 0 4px">✓ ${this._t('Cleaned data preview')}</div>${sampleTable}` : ''}

      <div id="iw-result" style="margin-top:16px"></div>
    `, `
      <button class="iw-btn iw-btn-ghost" onclick="ImportWizard._renderStep2()">${this._t('‹ Back')}</button>
      <button class="iw-btn iw-btn-primary" id="iw-import-btn" onclick="ImportWizard._execute()" ${report.clean_rows>0?'':'disabled style=opacity:.5'}>${this._t('Import')} ${report.clean_rows} ${this._t('Clean Records')}</button>
    `);
  },

  async _execute() {
    const btn = document.getElementById('iw-import-btn');
    if (btn) { btn.disabled = true; btn.textContent = this._t('Importing…'); }
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
            <div style="background:var(--state-danger-surface);border:1px solid var(--state-danger-border);border-radius:10px;padding:16px;text-align:center">
              <div style="font-size:40px;margin-bottom:8px">⚠️</div>
              <div style="color:var(--text-primary);font-size:16px;font-weight:700;margin-bottom:4px">${this._t('No records imported')}</div>
              <div style="color:var(--state-danger-text);font-size:14px">${this._esc(data.message || '')}</div>
            </div>`;
        } else {
          const partial = status === 'partial';
          resultEl.innerHTML = `
            <div style="background:var(--state-success-surface);border:1px solid var(--state-success-border);border-radius:10px;padding:16px;text-align:center">
              <div style="font-size:40px;margin-bottom:8px">${partial ? '⚠️' : '✅'}</div>
              <div style="color:var(--text-primary);font-size:16px;font-weight:700;margin-bottom:4px">${this._t(partial ? 'Imported with warnings' : 'Import Complete')}</div>
              <div style="color:${partial ? 'var(--state-warning-text)' : 'var(--accent-action)'};font-size:14px">${this._esc(data.message || '')}</div>
              ${(data.updated ? `<div style="color:var(--text-muted);font-size:13px;margin-top:4px">${data.updated} ${this._t('existing records updated')}</div>` : '')}
            </div>`;
        }

        // Actionable warnings (e.g. "import Employees first") — always prominent.
        if (warnings.length) {
          resultEl.innerHTML += `<div style="background:var(--state-warning-surface);border:1px solid var(--state-warning-border);border-radius:10px;padding:12px;margin-top:10px">
            ${warnings.map(w => `<div style="color:var(--state-warning-text);font-size:13px;margin:2px 0">⚠ ${this._esc(w)}</div>`).join('')}</div>`;
        }
        // Refused opening-stock declarations. The catalogue half of these
        // rows DID land, so they never appear in row_errors or the skipped
        // count -- without this block the operator would see a green tick
        // and quietly not get the stock figure they typed.
        const stockErrors = data.stock_errors || [];
        if (stockErrors.length) {
          resultEl.innerHTML += `<div style="background:var(--state-danger-surface);border:1px solid var(--state-danger-border);border-radius:10px;padding:12px;margin-top:10px">
            <div style="color:var(--state-danger-text);font-size:13px;font-weight:600;margin-bottom:6px">${this._esc(this._t('Stock was left unchanged for these products:'))}</div>
            ${stockErrors.map(e => `<div style="color:var(--state-danger-text);font-size:12px;margin:3px 0">
              <b>${this._esc(e.sku)}</b> — ${this._esc(this._t(e.reason))} (${this._esc(e.declared)}${e.would_be == null ? '' : ' → ' + this._esc(e.would_be)}, ${this._esc(this._t('currently on hand'))} ${this._esc(e.on_hand)})
            </div>`).join('')}
          </div>`;
        }
        // launch-readiness "product variants" follow-up: a `parent_sku` that
        // could not be linked (unresolvable anywhere, or itself a variant)
        // -- same "the catalogue row still landed, only the relationship
        // didn't" shape as stockErrors just above, never a silent drop.
        const parentErrors = data.parent_errors || [];
        if (parentErrors.length) {
          resultEl.innerHTML += `<div style="background:var(--state-danger-surface);border:1px solid var(--state-danger-border);border-radius:10px;padding:12px;margin-top:10px">
            <div style="color:var(--state-danger-text);font-size:13px;font-weight:600;margin-bottom:6px">${this._esc(this._t('Parent SKU link could not be made for these products:'))}</div>
            ${parentErrors.map(e => `<div style="color:var(--state-danger-text);font-size:12px;margin:3px 0">
              <b>${this._esc(e.sku)}</b> → <b>${this._esc(e.parent_sku)}</b>: ${this._esc(this._t(e.reason))}
            </div>`).join('')}
          </div>`;
        }
        if (skipped) {
          resultEl.innerHTML += `<div style="color:var(--text-muted);font-size:12px;margin-top:8px">${skipped} ${this._t('row(s) skipped in total.')}</div>`;
        }
        if (errCount) {
          resultEl.innerHTML += `<div class="iw-err-list">${data.row_errors.map(e =>
            `<div class="iw-err-item">${this._t('Row')} ${e.row}: ${e.errors.join('; ')}</div>`).join('')}</div>`;
        }

        if (status === 'none') {
          // Don't pretend it's done — send them back to fix the mapping/order.
          if (btn) { btn.disabled = false; btn.textContent = this._t('‹ Back to mapping'); btn.onclick = () => ImportWizard._renderStep2(); }
        } else {
          if (btn) { btn.textContent = this._t('Done'); btn.onclick = () => { ImportWizard.close(); ImportWizard._refreshAfterImport(); }; btn.disabled = false; }
          // Auto-refresh the underlying view + charts after a moment
          setTimeout(() => { try { ImportWizard._refreshAfterImport(); } catch(e){} }, 1500);
        }
      } else {
        resultEl.innerHTML = `
          <div style="background:var(--state-danger-surface);border:1px solid var(--state-danger-border);border-radius:10px;padding:16px">
            <div style="color:var(--state-danger-text);font-size:14px;font-weight:600">${this._t('Import failed')}</div>
            <div style="color:var(--text-muted);font-size:13px;margin-top:4px">${this._esc(data.error || this._t('Unknown error'))}</div>
          </div>`;
        if (data.row_errors && data.row_errors.length) {
          resultEl.innerHTML += `<div class="iw-err-list">${data.row_errors.map(e =>
            `<div class="iw-err-item">${this._t('Row')} ${e.row}: ${e.errors.join('; ')}</div>`).join('')}</div>`;
        }
        if (btn) { btn.disabled = false; btn.textContent = this._t('Retry'); }
      }
    } catch (e) {
      (window.SubsystemApp?.showToast || alert)(this._t('Network error during import'), 'error');
      if (btn) { btn.disabled = false; btn.textContent = this._t('Retry'); }
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
