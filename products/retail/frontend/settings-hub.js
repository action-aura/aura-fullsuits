/* Settings hub (Phase 1b, item 4 + the front half of item 6).
 *
 * One screen for everything the owner sets once: a left list of sections, one
 * section at a time, and a single sticky "Unsaved changes / Discard / Save" bar
 * that appears only when a field actually differs from what was last saved. It
 * replaces the row of per-card Save buttons the old Admin Center had.
 *
 * WHAT THIS FILE DOES NOT DUPLICATE. The old Admin Center cards (branding, tax
 * rate, device branch, business day, numbering books, e-invoicing regime) are
 * still built, loaded and saved by RetailSystem._renderAdminCenter and its
 * _load... and _save... helpers in subsystem-retail.js -- their field ids, validation
 * and server routes are unchanged and the older tests still anchor on them.
 * The hub renders that screen into a hidden stash once, then MOVES the cards
 * into the section that owns them (moving a node keeps its id, its typed
 * value and its loaded data). Only what has no owner yet is built here: the
 * tax mode / one-rate-per-category cards, the credit + loyalty card, the
 * live receipt preview, and the branch-code / readable-numbers card.
 *
 * THE PREVIEW IS THE REAL RECEIPT. It is drawn by RetailSystem._printReceipt
 * itself (given `saleData.__preview = {sink, branding, logo, widthMm}`), never
 * by a second template, so it cannot drift from what a customer is handed.
 *
 * Who sees what is decided per PERSON (role / capability), never per device:
 * see sections(). Server gates are unchanged and remain the authority; the
 * hub only stops offering doors that would answer 403.
 *
 * // P1b-waits: registry v9 -- the Books section (Phase 4) and the Accountant
 * // role are not built. No schema or capability-registry change was made.
 */
(function () {
  'use strict';

  const R = () => window.RetailSystem;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => R()._esc(s);
  const toast = (msg, type) => { if (window.SubsystemApp && SubsystemApp.showToast) SubsystemApp.showToast(msg, type); };

  // Section ids are a contract with the palette (palette.js) and the tests.
  const SECTION_ORDER = ['shop-receipt', 'tax', 'credit', 'tills', 'hardware', 'team', 'data-backup', 'plan-devices'];

  // ── who is asking ──────────────────────────────────────────────────────────
  // Same two axes _renderAdminCenter uses. `!window.SubsystemApp` fails OPEN,
  // and `hasCapability` is checked with typeof because test fixtures stub a
  // partial shell.
  function isOwner() { return !window.SubsystemApp || SubsystemApp.role === 'admin'; }
  function canDevice() {
    return isOwner() ||
      !!(window.SubsystemApp && typeof SubsystemApp.hasCapability === 'function' &&
         SubsystemApp.hasCapability('retail.employees'));
  }

  // ── dirty tracking ─────────────────────────────────────────────────────────
  // A group is a set of field ids that are saved together by one saver. The bar
  // is up iff some group's current values differ from its baseline (what was
  // loaded or last saved) -- so typing a value and typing it back leaves the
  // bar down, and "Discard" is just "put the baseline back".
  const S = {
    root: null,          // #sh-root of the live render, null when not mounted
    token: 0,            // bumps on every render; stale async work checks it
    current: null,       // id of the visible section
    built: {},           // section id -> true once its pane has content
    baseline: {},        // group name -> {fieldId: value}
    pending: null,       // section requested by open() before the hub was mounted
    pvLang: 'en',
    pvWidth: 80,
    pvTimer: null,
    pvSeq: 0,
    catalog: null,       // categories loaded for the tax card
    numbering: null,     // last GET /numbering data
    ready: false,        // true once the baseline is taken; refreshBar records only then
    carry: null,         // unsaved values to bring back across a theme / language re-render
  };

  const GROUPS = {
    brand: {
      fields: ['brand-name', 'brand-tax', 'brand-address', 'brand-phone', 'brand-header', 'brand-footer',
        'brand-receipt-lang', 'brand-show-cashier', 'brand-show-customer', 'brand-whatsapp', 'brand-logo-file'],
      save: () => R()._saveBranding().then(async (ok) => {
        if (ok) {
          // The chosen file has been uploaded by the saver: drop it from the
          // picker so the next Save does not upload it a second time.
          const f = $('brand-logo-file');
          if (f) f.value = '';
          await R()._loadBrandingForm();
        }
        return ok;
      }),
    },
    tax: {
      fields: ['tax-mode', 'tax-default-rate'],
      save: async () => {
        const now = snapGroup('tax');
        const was = S.baseline.tax || {};
        let ok = true;
        if ('tax-mode' in now && now['tax-mode'] !== was['tax-mode']) {
          try {
            const resp = await R()._post('/api/sub/retail/settings/tax', { tax_calculation_mode: now['tax-mode'] });
            if (!resp || resp.status !== 'success') {
              toast((resp && resp.message) || t('Could not save the tax setting.'), 'error');
              ok = false;
            } else {
              toast(t('Tax setting saved.'), 'success');
            }
          } catch (e) {
            console.error(e);
            toast(t('Could not save the tax setting.'), 'error');
            ok = false;
          }
        }
        if ('tax-default-rate' in now && now['tax-default-rate'] !== was['tax-default-rate']) {
          if (!(await R()._saveDefaultTaxRate())) ok = false;
        }
        return ok;
      },
    },
    credit: {
      fields: ['credit-mode', 'credit-limit', 'credit-enforce', 'credit-loyalty'],
      save: () => saveCredit(),
    },
    deviceBranch: { fields: ['device-branch-select'], save: () => R()._saveDeviceBranch() },
    businessDay: { fields: ['business-day-tz', 'business-day-hour'], save: () => R()._saveBusinessDay() },
    numbering: { fields: ['num-branch-code', 'num-readable'], save: () => saveNumbering() },
    einvoice: { fields: ['einvoicing-regime'], save: () => R()._saveEinvoicingRegime() },
  };
  const GROUP_ORDER = ['brand', 'tax', 'credit', 'deviceBranch', 'businessDay', 'numbering', 'einvoice'];

  function fieldValue(el) {
    return el.type === 'checkbox' ? !!el.checked : String(el.value == null ? '' : el.value);
  }
  // Only fields present in the document take part: a manager's hub has no
  // branding fields, and a hub whose numbering endpoint is missing has no
  // numbering fields -- neither is "dirty".
  function snapGroup(name) {
    const out = {};
    GROUPS[name].fields.forEach((id) => { const el = $(id); if (el) out[id] = fieldValue(el); });
    return out;
  }
  function sameSnap(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
  function rebaseline(name) { S.baseline[name] = snapGroup(name); }
  function rebaselineAll() { GROUP_ORDER.forEach(rebaseline); }
  function dirtyGroups() {
    return GROUP_ORDER.filter((g) => {
      const now = snapGroup(g);
      if (!Object.keys(now).length) return false;
      return !sameSnap(now, S.baseline[g] || {});
    });
  }
  function refreshBar() {
    const bar = $('sh-savebar');
    if (!bar) return;
    bar.hidden = dirtyGroups().length === 0;
    liftToasts(!bar.hidden);
    recordCarry();
  }

  // ── unsaved values survive a theme / language switch ───────────────────────
  // A theme or language switch re-renders the section (app-shell.js calls
  // _navigate / launch), which used to throw away whatever the person had typed
  // and not yet saved. So the dirty values are remembered in module state on
  // every edit, together with the theme + language they were typed under.
  //
  // They come back ONLY when the re-render was caused by such a switch: the
  // stamp differs from the one recorded, and it happens within moments. A plain
  // navigation away and back leaves the stamp unchanged, so it starts clean --
  // this is NOT a leave guard (navigating away still loses unsaved changes; that
  // guard waits for the shell to offer a hook).
  const CARRY_MAX_AGE_MS = 15000;
  const CARRY_SKIP = ['brand-logo-file']; // a file input cannot be filled from script
  function envStamp() {
    return (document.documentElement.getAttribute('data-theme') || '') + '|' +
      ((window.AuraI18n && AuraI18n.current) || '');
  }
  function recordCarry() {
    if (!S.ready) return; // before the baseline exists every field looks dirty
    const values = {};
    dirtyGroups().forEach((g) => {
      const now = snapGroup(g);
      const was = S.baseline[g] || {};
      Object.keys(now).forEach((id) => {
        if (CARRY_SKIP.indexOf(id) === -1 && now[id] !== was[id]) values[id] = now[id];
      });
    });
    S.carry = Object.keys(values).length
      ? { values, section: S.current, env: envStamp(), at: Date.now() }
      : null;
  }
  function takeCarry() {
    const c = S.carry;
    S.carry = null;
    if (!c || c.env === envStamp() || Date.now() - c.at > CARRY_MAX_AGE_MS) return null;
    return c;
  }
  function applyCarry(c) {
    Object.keys(c.values).forEach((id) => {
      const el = $(id);
      if (!el) return;
      const v = c.values[id];
      if (el.type === 'checkbox') el.checked = !!v;
      else if (el.tagName === 'SELECT' && !Array.prototype.some.call(el.options, (o) => o.value === v)) return;
      else el.value = v;
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  // Toasts sit bottom-inline-end, exactly where the bar's Save button is: a
  // "Branding saved." toast from the last save would cover the Save button of the
  // next edit (found with a real mouse click, elementFromPoint hit the toast).
  // app-shell.js positions toasts from --overlay-inset-block-end, so while the
  // bar is up that variable is raised by the bar's height. A timer puts it back
  // when the bar hides or the hub is left (nothing tells us about the latter).
  const OVERLAY_VAR = '--overlay-inset-block-end';
  let liftTimer = null;
  function liftToasts(on) {
    const root = document.documentElement;
    if (!on) {
      root.style.removeProperty(OVERLAY_VAR);
      if (liftTimer) { clearInterval(liftTimer); liftTimer = null; }
      return;
    }
    const bar = $('sh-savebar');
    const h = bar ? Math.ceil(bar.getBoundingClientRect().height) : 0;
    const phone = window.matchMedia && window.matchMedia('(max-width: 640px)').matches;
    const base = phone
      ? 'var(--tabbar-block-size) + var(--safe-area-bottom) + var(--space-snug, 12px)'
      : '24px';
    root.style.setProperty(OVERLAY_VAR, 'calc(' + h + 'px + var(--space-comfy) + ' + base + ')');
    if (!liftTimer) {
      liftTimer = setInterval(() => {
        const b = $('sh-savebar');
        if (!b || b.hidden || !b.isConnected) liftToasts(false);
      }, 400);
    }
  }

  function restoreGroup(name) {
    const base = S.baseline[name] || {};
    Object.keys(base).forEach((id) => {
      const el = $(id);
      if (!el) return;
      if (el.type === 'checkbox') el.checked = !!base[id];
      else if (el.type === 'file') el.value = '';
      else el.value = base[id];
      // Anything that reacts to typing (credit limit visibility, the receipt
      // preview, the numbering example) hears the restore like a keystroke.
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  async function discard() {
    dirtyGroups().forEach(restoreGroup);
    if (dirtyGroups().length === 0 && $('brand-logo-file')) {
      // A logo picked but not saved is shown in the small preview; put the
      // saved one back.
      await R()._loadBrandingForm();
      const b = R()._brandingLoaded || {};
      if (!b.has_logo) {
        const p = $('brand-logo-preview');
        if (p) { p.style.display = 'none'; p.removeAttribute('src'); }
      }
    }
    refreshBar();
    schedulePreview(0);
  }

  async function save() {
    const btn = $('sh-save');
    const groups = dirtyGroups();
    if (!groups.length) { refreshBar(); return; }
    if (btn) { btn.disabled = true; btn.textContent = t('Saving…'); }
    for (let i = 0; i < groups.length; i += 1) {
      let ok = false;
      try { ok = !!(await GROUPS[groups[i]].save()); } catch (e) { console.error(e); ok = false; }
      // Only a saved group is re-based: a refused one stays dirty, so the bar
      // stays up and nothing the person typed is thrown away.
      if (ok) rebaseline(groups[i]);
    }
    if (btn) { btn.disabled = false; btn.textContent = t('Save'); }
    refreshBar();
  }

  // ── credit + loyalty ───────────────────────────────────────────────────────
  // POST /settings/credit stores whatever it is sent (str(v), no value checks),
  // so the numbers are checked HERE before anything leaves the browser.
  async function saveCredit() {
    const mode = $('credit-mode').value;
    const enforce = $('credit-enforce').value;
    const limitRaw = $('credit-limit').value.trim();
    const loyaltyRaw = $('credit-loyalty').value.trim();
    const limit = limitRaw === '' ? 0 : Number(limitRaw);
    const loyalty = loyaltyRaw === '' ? 0 : Number(loyaltyRaw);
    if (!Number.isFinite(limit) || limit < 0) {
      toast(t('The credit limit must be a number, zero or more.'), 'error');
      return false;
    }
    if (mode === 'limited' && limit <= 0) {
      toast(t('Enter a credit limit above zero, or choose another credit option.'), 'error');
      return false;
    }
    if (!Number.isFinite(loyalty) || loyalty < 0) {
      toast(t('The loyalty point value must be a number, zero or more.'), 'error');
      return false;
    }
    try {
      const resp = await R()._post('/api/sub/retail/settings/credit', {
        default_credit_mode: mode,
        default_credit_limit: String(limit),
        enforce_credit_limit: enforce,
        loyalty_point_value: String(loyalty),
      });
      if (!resp || resp.status !== 'success') {
        toast((resp && resp.message) || t('Could not save the credit settings.'), 'error');
        return false;
      }
      toast(t('Credit settings saved.'), 'success');
      return true;
    } catch (e) {
      console.error(e);
      toast(t('Could not save the credit settings.'), 'error');
      return false;
    }
  }

  function syncCreditLimitVisibility() {
    const mode = $('credit-mode');
    const wrap = $('credit-limit-wrap');
    if (mode && wrap) wrap.hidden = mode.value !== 'limited';
  }

  async function loadCredit() {
    try {
      const resp = await R()._get('/api/sub/retail/settings/credit');
      const d = (resp && resp.data) || {};
      const mode = ['none', 'limited', 'unlimited'].includes(d.default_credit_mode) ? d.default_credit_mode : 'none';
      $('credit-mode').value = mode;
      $('credit-limit').value = d.default_credit_limit != null && Number(d.default_credit_limit) > 0 ? d.default_credit_limit : '';
      $('credit-enforce').value = d.enforce_credit_limit === 'block' ? 'block' : 'warn';
      $('credit-loyalty').value = d.loyalty_point_value != null && Number(d.loyalty_point_value) > 0 ? d.loyalty_point_value : '';
    } catch (e) { console.error(e); }
    syncCreditLimitVisibility();
    rebaseline('credit');
  }

  // ── tax ────────────────────────────────────────────────────────────────────
  async function loadTaxMode() {
    try {
      const resp = await R()._get('/api/sub/retail/settings/tax');
      const d = (resp && resp.data) || {};
      $('tax-mode').value = d.tax_calculation_mode === 'before_discount' ? 'before_discount' : 'after_discount';
    } catch (e) { console.error(e); }
  }

  async function loadCategories() {
    const sel = $('tax-cat-select');
    if (!sel) return;
    try {
      const resp = await R()._get('/api/sub/retail/categories');
      S.catalog = (resp && resp.data) || [];
      sel.innerHTML = S.catalog.length
        ? S.catalog.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')
        : `<option value="">${esc(t('No categories yet'))}</option>`;
      $('tax-cat-apply').disabled = !S.catalog.length;
    } catch (e) { console.error(e); }
  }

  // There is no bulk "set the tax rate of a category" route, so this walks the
  // category's products and PATCHes each one (the same route Edit Product uses,
  // same CAP_STOCK_ADJUST gate). It is therefore NOT atomic: if it stops half
  // way the status line says exactly how many were changed, and running it
  // again is safe (each product just gets the same rate).
  async function applyCategoryRate() {
    const status = $('tax-cat-status');
    const say = (m) => { if (status) status.textContent = m; };
    const sel = $('tax-cat-select');
    const raw = $('tax-cat-rate').value.trim();
    const rate = Number(raw);
    if (!sel || !sel.value) { say(t('Choose a category first.')); return; }
    if (raw === '' || !Number.isFinite(rate) || rate < 0 || rate > 100) {
      say(t('Enter a tax rate between 0 and 100.'));
      return;
    }
    const cat = (S.catalog || []).find((c) => String(c.id) === String(sel.value));
    const catName = cat ? cat.name : '';
    let products = [];
    try {
      const resp = await R()._get('/api/sub/retail/products');
      products = ((resp && resp.data) || []).filter((p) => String(p.category_id) === String(sel.value));
    } catch (e) {
      console.error(e);
      say(t('Could not load the products.'));
      return;
    }
    if (!products.length) { say(t('No products in this category.')); return; }
    const okGo = await R()._confirm({
      title: t('Change tax rate'),
      message: t('Set the tax rate of {n} products in "{category}" to {rate}%? Their current tax rates are replaced.')
        .replace('{n}', String(products.length)).replace('{category}', catName).replace('{rate}', String(rate)),
      confirmLabel: t('Change tax rate'),
    });
    if (!okGo) return;
    const btn = $('tax-cat-apply');
    if (btn) btn.disabled = true;
    let done = 0;
    let failed = 0;
    for (let i = 0; i < products.length; i += 1) {
      say(t('Updating {done} of {n}…').replace('{done}', String(done)).replace('{n}', String(products.length)));
      try {
        const r = await R()._patch('/api/sub/retail/products/' + encodeURIComponent(products[i].id), { tax_rate: rate });
        if (r && r.status === 'success') done += 1; else failed += 1;
      } catch (e) { failed += 1; }
    }
    if (btn) btn.disabled = false;
    if (failed) {
      say(t('Changed {done} of {n} products. {failed} could not be changed; press the button again to retry.')
        .replace('{done}', String(done)).replace('{n}', String(products.length)).replace('{failed}', String(failed)));
      toast(t('Some products could not be changed.'), 'error');
    } else {
      say(t('Changed the tax rate of {n} products.').replace('{n}', String(done)));
      toast(t('Tax rate changed.'), 'success');
    }
  }

  // ── numbering: branch code + readable receipt numbers ─────────────────────
  // Built against crew B's documented shape (numbering-shapes.md):
  //   GET  /numbering -> {branch_uid, branch_code, default_branch_code,
  //                       branch_code_is_saved, readable_enabled,
  //                       preview:{sale, return}}
  //   POST /numbering {branch_code?, readable_enabled?} -> same body
  // Owner-only on the server. If the route is missing or refuses, the card
  // says so plainly instead of showing fields that cannot save.
  function numberingExample(code) {
    const n = S.numbering;
    if (!n || !n.preview) return null;
    const cut = (s) => (s.indexOf(n.branch_code) === 0 ? code + s.slice(n.branch_code.length) : s);
    return { sale: cut(String(n.preview.sale || '')), ret: cut(String(n.preview.return || '')) };
  }
  function drawNumberingExample() {
    const box = $('num-preview');
    const input = $('num-branch-code');
    if (!box || !input) return;
    const code = input.value.trim().toUpperCase();
    const valid = /^[A-Z0-9]{2,4}$/.test(code);
    const ex = numberingExample(valid ? code : (S.numbering && S.numbering.branch_code) || '');
    if (!ex) { box.textContent = ''; return; }
    box.innerHTML = `${esc(t('Sale'))}: <bdi dir="ltr">${esc(ex.sale)}</bdi> &nbsp; ${esc(t('Return'))}: <bdi dir="ltr">${esc(ex.ret)}</bdi>` +
      (valid ? '' : `<br>${esc(t('Use 2 to 4 letters or digits.'))}`);
  }
  async function loadNumbering() {
    const host = $('numbering-codes-body');
    if (!host) return;
    let d = null;
    try {
      const resp = await R()._get('/api/sub/retail/numbering');
      if (resp && resp.status === 'success' && resp.data) d = resp.data;
    } catch (e) { d = null; }
    if (!d) {
      S.numbering = null;
      host.innerHTML = `<p class="sh-note">${esc(t('Readable receipt numbers are not available on this computer yet.'))}</p>`;
      return;
    }
    S.numbering = d;
    host.innerHTML = `
      <div class="sh-row">
        <div class="ret-field" style="margin:0">
          <label for="num-branch-code">${esc(t('Branch code'))}</label>
          <input type="text" id="num-branch-code" dir="ltr" maxlength="4" autocapitalize="characters" autocomplete="off" />
          <p class="sh-note">${esc(t('2 to 4 letters or digits. Filled in from the branch name until you change it.'))}</p>
        </div>
      </div>
      <div class="ret-field" style="margin-top:12px;display:flex;align-items:center;gap:10px">
        <input type="checkbox" id="num-readable" style="width:auto" />
        <label for="num-readable" style="margin:0;text-transform:none;font-size:14px;color:var(--text-primary)">${esc(t('Readable receipt numbers'))}</label>
      </div>
      <p class="sh-note">${esc(t('Off keeps the numbering you have now. Turning it on only changes tills that open a new shift afterwards. Books already in use keep going.'))}</p>
      <p class="sh-num-preview" id="num-preview"></p>`;
    $('num-branch-code').value = d.branch_code || '';
    $('num-readable').checked = !!d.readable_enabled;
    rebaseline('numbering');
    drawNumberingExample();
  }
  async function saveNumbering() {
    const input = $('num-branch-code');
    const cb = $('num-readable');
    if (!input || !cb) return true;
    const base = S.baseline.numbering || {};
    const code = input.value.trim().toUpperCase();
    const body = {};
    if (input.value !== base['num-branch-code']) {
      if (!/^[A-Z0-9]{2,4}$/.test(code)) {
        toast(t('The branch code must be 2 to 4 letters or digits.'), 'error');
        return false;
      }
      body.branch_code = code;
    }
    if (cb.checked !== base['num-readable']) body.readable_enabled = cb.checked;
    if (!Object.keys(body).length) return true;
    try {
      const resp = await R()._post('/api/sub/retail/numbering', body);
      if (!resp || resp.status !== 'success') {
        toast((resp && resp.message) || t('Could not save the receipt numbering.'), 'error');
        return false;
      }
      if (resp.data) {
        S.numbering = resp.data;
        input.value = resp.data.branch_code || code;
        cb.checked = !!resp.data.readable_enabled;
        drawNumberingExample();
      }
      toast(t('Receipt numbering saved.'), 'success');
      return true;
    } catch (e) {
      console.error(e);
      toast(t('Could not save the receipt numbering.'), 'error');
      return false;
    }
  }

  // ── the live receipt preview ───────────────────────────────────────────────
  // A fixed, invented sale (so the preview never prints a real customer). Built
  // per language because item names are DATA and are not translated by the
  // receipt's own label translator. `sampleSale()` carries no `__preview`: the
  // very same object can be handed to a real print, which is how the tests
  // prove the preview and the printed receipt say the same thing.
  function sampleSale(lang) {
    const L = lang === 'ar' ? 'ar' : 'en';
    const tr = (s) => R()._translateForLang(s, L);
    return {
      sale_number: 'A-000124',
      created_at: '2026-01-15 14:32:00',
      employee_name: tr('Sample cashier'),
      customer_id: 1,
      customer_name: tr('Sample customer'),
      lines: [
        { name: tr('Sample item'), quantity: 2, unit_price: 1.5, discount_amount: 0, applied_promotion: null },
        { name: tr('Sample item with offer'), quantity: 1, unit_price: 4.25, discount_amount: 0.25, applied_promotion: 'PREVIEW' },
      ],
      subtotal: 7.25,
      tax_amount: 1.12,
      total: 8.12,
      amount_paid: 10,
      change: 1.88,
    };
  }

  // Branding as it is typed RIGHT NOW, in the shape _printReceipt reads.
  function typedBranding() {
    const g = (id) => { const el = $(id); return el ? el.value.trim() : ''; };
    const loaded = R()._brandingLoaded || {};
    const lang = g('brand-receipt-lang');
    return Object.assign({}, loaded, {
      branding_business_name: g('brand-name'),
      branding_tax_number: g('brand-tax'),
      branding_address: g('brand-address'),
      branding_phone: g('brand-phone'),
      branding_receipt_header: g('brand-header'),
      branding_receipt_footer: g('brand-footer'),
      branding_receipt_language: lang === 'en' || lang === 'ar' ? lang : 'auto',
      branding_receipt_show_cashier: String(!!($('brand-show-cashier') && $('brand-show-cashier').checked)),
      branding_receipt_show_customer: String(!!($('brand-show-customer') && $('brand-show-customer').checked)),
    });
  }

  function currentLogo() {
    const p = $('brand-logo-preview');
    if (!p || p.style.display === 'none') return '';
    return p.getAttribute('src') || '';
  }

  async function drawPreview() {
    const frame = $('sh-preview-frame');
    if (!frame) return;
    const seq = (S.pvSeq += 1);
    const branding = typedBranding();
    // The preview language is chosen by the toggle, and the receipt itself is
    // told to use it, so what is drawn is exactly what a shop set to that
    // language prints.
    branding.branding_receipt_language = S.pvLang;
    const sale = sampleSale(S.pvLang);
    sale.__preview = {
      branding,
      logo: currentLogo(),
      widthMm: S.pvWidth,
      sink: (html) => {
        if (seq !== S.pvSeq) return; // a newer draw superseded this one
        frame.style.setProperty('--paper-mm', String(S.pvWidth));
        frame.onload = () => {
          try { frame.style.height = (frame.contentDocument.documentElement.scrollHeight + 8) + 'px'; } catch (e) { /* keep min height */ }
        };
        frame.srcdoc = html;
      },
    };
    try { await R()._printReceipt(sale); } catch (e) { console.error(e); }
  }
  function schedulePreview(delay) {
    clearTimeout(S.pvTimer);
    S.pvTimer = setTimeout(drawPreview, delay == null ? 150 : delay);
  }
  function syncPreviewToggles() {
    const root = S.root;
    if (!root) return;
    root.querySelectorAll('[data-sh-w]').forEach((b) => b.setAttribute('aria-pressed', String(Number(b.dataset.shW) === S.pvWidth)));
    root.querySelectorAll('[data-sh-l]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.shL === S.pvLang)));
  }

  // ── panes ──────────────────────────────────────────────────────────────────
  function moveCard(id, into, opts) {
    const card = $(id);
    if (!card || !into) return null;
    // The hub has ONE Save bar; the per-card Save buttons the old screen put
    // on each card are dropped when the card moves in.
    if (!opts || opts.keepSave !== true) {
      card.querySelectorAll('button[onclick*="_saveBranding"], button[onclick*="_saveDefaultTaxRate"], ' +
        'button[onclick*="_saveDeviceBranch"], button[onclick*="_saveBusinessDay"], ' +
        'button[onclick*="_saveEinvoicingRegime"]').forEach((b) => b.remove());
    }
    into.appendChild(card);
    return card;
  }

  function pane(id) { return S.root ? S.root.querySelector('[data-sh-pane="' + id + '"]') : null; }

  function paneShell(label) {
    return `<p class="sh-pane-loading">${esc(t('Loading…'))}</p>`;
  }

  // Panes that reuse the moved Admin Center cards. Filled in once, after the
  // stash has been rendered.
  function fillAdminPanes() {
    const owner = isOwner();
    const shop = pane('shop-receipt');
    if (shop) {
      shop.innerHTML = `
        <div class="sh-split">
          <div class="sh-pane" id="sh-shop-forms"></div>
          <aside class="sh-preview" aria-label="${esc(t('Receipt preview'))}">
            <div class="sh-preview-bar">
              <div class="sh-seg" role="group" aria-label="${esc(t('Paper width'))}">
                <button type="button" dir="ltr" data-sh-w="80" aria-pressed="true">80 mm</button>
                <button type="button" dir="ltr" data-sh-w="58" aria-pressed="false">58 mm</button>
              </div>
              <div class="sh-seg" role="group" aria-label="${esc(t('Receipt language'))}">
                <button type="button" data-sh-l="ar" aria-pressed="false">العربية</button>
                <button type="button" data-sh-l="en" aria-pressed="false">English</button>
              </div>
            </div>
            <div class="sh-preview-stage">
              <iframe id="sh-preview-frame" class="sh-preview-frame" title="${esc(t('Receipt preview'))}" sandbox="allow-same-origin"></iframe>
            </div>
            <p class="sh-note">${esc(t('This is drawn by the same code that prints the real receipt, with the sample sale and the details you are typing.'))}</p>
          </aside>
        </div>`;
      const forms = $('sh-shop-forms');
      moveCard('branding-card', forms);
      moveCard('wa-reports-card', forms);
    }

    const tax = pane('tax');
    if (tax) {
      tax.innerHTML = `
        <div class="sub-chart-card" id="tax-mode-card">
          <h3 class="sh-card-title">${esc(t('How tax is worked out'))}</h3>
          <p class="sh-card-lead">${esc(t('Whether the discount comes off before tax is worked out, or tax is worked out on the full price. Applies to every sale from now on.'))}</p>
          <div class="ret-field" style="margin:0"><label for="tax-mode">${esc(t('Tax is worked out'))}</label>
            <select id="tax-mode">
              <option value="after_discount">${esc(t('After the discount'))}</option>
              <option value="before_discount">${esc(t('Before the discount'))}</option>
            </select>
          </div>
        </div>
        <div id="sh-tax-default-slot"></div>
        <div class="sub-chart-card" id="tax-category-card">
          <h3 class="sh-card-title">${esc(t('One tax rate for a whole category'))}</h3>
          <p class="sh-card-lead">${esc(t('Sets the tax rate of every product in the category in one go. This changes the products now; it is not saved with the bar below.'))}</p>
          <div class="sh-inline-actions">
            <div class="ret-field" style="margin:0"><label for="tax-cat-select">${esc(t('Category'))}</label><select id="tax-cat-select"></select></div>
            <div class="ret-field" style="margin:0"><label for="tax-cat-rate">${esc(t('Tax rate %'))}</label>
              <input type="number" id="tax-cat-rate" min="0" max="100" step="0.1" dir="ltr" /></div>
            <button type="button" class="ret-btn ret-btn-ghost" id="tax-cat-apply">${esc(t('Change tax rate'))}</button>
          </div>
          <p class="sh-cat-status" id="tax-cat-status" role="status" aria-live="polite"></p>
        </div>
        <div id="sh-einvoice-slot"></div>`;
      moveCard('default-tax-card', $('sh-tax-default-slot'));
      moveCard('einvoicing-card', $('sh-einvoice-slot'));
    }

    const tills = pane('tills');
    if (tills) {
      tills.innerHTML = '';
      moveCard('admin-center-manager-note', tills);
      moveCard('device-branch-card', tills);
      if (owner) {
        const num = document.createElement('div');
        num.className = 'sub-chart-card';
        num.id = 'numbering-codes-card';
        num.innerHTML = `<h3 class="sh-card-title">${esc(t('Receipt numbers'))}</h3>
          <p class="sh-card-lead">${esc(t('Start every receipt number with a short code for the branch, so a paper receipt tells you where it was printed.'))}</p>
          <div id="numbering-codes-body"><p class="sh-pane-loading">${esc(t('Loading…'))}</p></div>`;
        tills.appendChild(num);
      }
      moveCard('doc-series-card', tills);
      moveCard('business-day-card', tills);
      moveCard('shop-network-card', tills);
    }
  }

  function credCard() {
    return `
      <div class="sub-chart-card" id="credit-card">
        <h3 class="sh-card-title">${esc(t('Customer credit'))}</h3>
        <p class="sh-card-lead">${esc(t('What a new customer is allowed to owe you, and what the till does when they go over.'))}</p>
        <div class="sh-row">
          <div class="ret-field" style="margin:0"><label for="credit-mode">${esc(t('Credit for new customers'))}</label>
            <select id="credit-mode">
              <option value="none">${esc(t('No credit'))}</option>
              <option value="limited">${esc(t('Up to a limit'))}</option>
              <option value="unlimited">${esc(t('No limit'))}</option>
            </select></div>
          <div class="ret-field" style="margin:0" id="credit-limit-wrap" hidden><label for="credit-limit">${esc(t('Credit limit'))}</label>
            <input type="number" id="credit-limit" min="0" step="0.001" dir="ltr" /></div>
          <div class="ret-field" style="margin:0"><label for="credit-enforce">${esc(t('When a customer goes over their limit'))}</label>
            <select id="credit-enforce">
              <option value="warn">${esc(t('Warn the cashier'))}</option>
              <option value="block">${esc(t('Block the sale'))}</option>
            </select></div>
        </div>
      </div>
      <div class="sub-chart-card" id="loyalty-card">
        <h3 class="sh-card-title">${esc(t('Loyalty points'))}</h3>
        <p class="sh-card-lead">${esc(t('What one point is worth when a customer spends points. Leave empty or 0 to turn spending points off.'))}</p>
        <div class="ret-field" style="margin:0"><label for="credit-loyalty">${esc(t('Loyalty point value'))}</label>
          <input type="number" id="credit-loyalty" min="0" step="0.001" dir="ltr" /></div>
      </div>`;
  }

  // Builds one pane's content the first time it is shown. Returns a promise.
  async function buildPane(id, token) {
    if (S.built[id]) return;
    S.built[id] = true;
    const host = pane(id);
    if (!host) return;
    const alive = () => S.token === token && S.root && S.root.isConnected;
    if (id === 'credit') {
      host.innerHTML = credCard();
      await loadCredit();
    } else if (id === 'hardware') {
      host.innerHTML = `<p class="sh-note">${esc(t('These settings belong to this computer only.'))}</p><div id="sh-printer-slot"></div>`;
      R()._mountPrinterCard($('sh-printer-slot'));
    } else if (id === 'team') {
      if (window.RetailEmployees && typeof RetailEmployees.render === 'function') await RetailEmployees.render(host);
    } else if (id === 'data-backup') {
      await R()._renderBackupExport(host);
    } else if (id === 'plan-devices') {
      if (window.PlanDevices && typeof PlanDevices.render === 'function') await PlanDevices.render(host);
      else host.innerHTML = '';
    }
    if (!alive()) return;
    refreshBar();
  }

  function showSection(id) {
    if (!S.root) return;
    S.current = id;
    S.root.querySelectorAll('[data-sh]').forEach((b) => {
      if (b.dataset.sh === id) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    });
    S.root.querySelectorAll('[data-sh-pane]').forEach((p) => { p.hidden = p.dataset.shPane !== id; });
    buildPane(id, S.token);
    if (id === 'shop-receipt') schedulePreview(0);
  }

  // ── the public surface ─────────────────────────────────────────────────────
  function sections() {
    const owner = isOwner();
    const dev = canDevice();
    const list = [
      { id: 'shop-receipt', label: t('Shop & receipt'), visible: owner },
      { id: 'tax', label: t('Tax'), visible: owner },
      { id: 'credit', label: t('Credit & loyalty'), visible: owner },
      { id: 'tills', label: t('Tills & numbering'), visible: dev },
      // Hardware stays owner-only (decided): a delegated manager keeps the
      // printer card on the scanner screen, which is where they already use it.
      { id: 'hardware', label: t('Hardware'), visible: owner },
      { id: 'team', label: t('Team'), visible: dev },
      { id: 'data-backup', label: t('Data & backup'), visible: owner },
      { id: 'plan-devices', label: t('Plan & devices'), visible: owner && !!window.PlanDevices },
    ];
    return SECTION_ORDER.map((id) => list.find((s) => s.id === id));
  }

  function open(id) {
    const s = sections().find((x) => x.id === id);
    if (!s || !s.visible) return false;
    if (S.root && S.root.isConnected) { showSection(id); return true; }
    S.pending = id;
    if (window.SubsystemApp && typeof SubsystemApp._navigate === 'function') SubsystemApp._navigate('admin-center');
    return true;
  }

  async function render(c) {
    const r = R();
    r._injectStyles();
    if (!canDevice()) {
      return r._renderCapabilityRestricted(c, {
        icon: 'lock',
        title: t('Settings'),
        message: t('Settings is limited to the store owner and delegated managers. Open the till to start ringing sales.'),
      });
    }
    const token = (S.token += 1);
    const carry = takeCarry();
    S.ready = false;
    const visible = sections().filter((s) => s.visible);
    S.built = {};
    S.baseline = {};
    S.catalog = null;
    S.numbering = null;
    S.current = null;
    S.pvSeq += 1;
    S.pvWidth = r._printerCfg().paperWidth === '58mm' ? 58 : 80;
    S.pvLang = (window.AuraI18n && AuraI18n.current === 'ar') ? 'ar' : 'en';

    c.innerHTML = `
      <div class="settings-hub" id="sh-root">
        <nav class="sh-nav" aria-label="${esc(t('Settings sections'))}">
          ${visible.map((s) => `<button type="button" class="sh-nav-item" data-sh="${esc(s.id)}">${esc(s.label)}</button>`).join('')}
        </nav>
        <div class="sh-main">
          <div id="sh-stash" hidden></div>
          ${visible.map((s) => `<section class="sh-pane" data-sh-pane="${esc(s.id)}" aria-label="${esc(s.label)}" hidden>${paneShell(s.label)}</section>`).join('')}
          <div class="sh-savebar" id="sh-savebar" role="region" aria-label="${esc(t('Unsaved changes'))}" hidden>
            <span class="sh-savebar-msg">${esc(t('Unsaved changes'))}</span>
            <span class="sh-savebar-actions">
              <button type="button" class="ret-btn ret-btn-ghost" id="sh-discard">${esc(t('Discard'))}</button>
              <button type="button" class="ret-btn ret-btn-primary" id="sh-save">${esc(t('Save'))}</button>
            </span>
          </div>
        </div>
      </div>`;
    S.root = $('sh-root');

    // One delegated listener set: the moved Admin Center cards keep their own
    // inline markup, so the hub listens at the root instead of at each field.
    S.root.addEventListener('input', onEdit);
    S.root.addEventListener('change', onEdit);
    S.root.addEventListener('click', onClick);

    // Show the first section immediately (so the person sees the screen), then
    // fill the Admin Center panes once the stash has loaded.
    if (carry && !S.pending) S.pending = carry.section;
    const first = (S.pending && visible.some((s) => s.id === S.pending)) ? S.pending : visible[0].id;
    S.pending = null;
    S.root.querySelectorAll('[data-sh]').forEach((b) => b.setAttribute('aria-current', b.dataset.sh === first ? 'page' : 'false'));
    S.root.querySelector('[data-sh-pane="' + first + '"]').hidden = false;
    S.current = first;

    // _renderAdminCenter reads its cards back with getElementById, so the
    // stash must be in the document -- it is, hidden.
    await r._renderAdminCenter($('sh-stash'));
    if (S.token !== token || !S.root || !S.root.isConnected) return;
    fillAdminPanes();
    if (isOwner()) {
      await Promise.all([loadTaxMode(), loadCategories(), loadNumbering()]);
    } else {
      await loadNumbering(); // a no-op without the owner-only card
    }
    if (S.token !== token || !S.root || !S.root.isConnected) return;
    S.built['shop-receipt'] = true;
    S.built.tax = true;
    S.built.tills = true;
    rebaselineAll();
    if (carry) applyCarry(carry);
    S.ready = true;
    // Which receipt language the preview starts in: the shop's own setting when
    // it names one, else the till's language.
    const lang = $('brand-receipt-lang');
    if (lang && (lang.value === 'en' || lang.value === 'ar')) S.pvLang = lang.value;
    syncPreviewToggles();
    if (first !== 'shop-receipt' && first !== 'tax' && first !== 'tills') {
      S.built[first] = false;
      buildPane(first, token);
    }
    if (first === 'shop-receipt') schedulePreview(0);
    // The stash keeps only what no section claimed.
    refreshBar();
  }

  function onEdit(ev) {
    const t0 = ev.target;
    if (!t0) return;
    if (t0.id === 'credit-mode') syncCreditLimitVisibility();
    if (t0.id === 'num-branch-code') {
      const up = t0.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
      if (up !== t0.value) t0.value = up;
      drawNumberingExample();
    }
    if (t0.id === 'brand-logo-file') {
      const f = t0.files && t0.files[0];
      if (f) {
        R()._readFileAsDataURL(f).then((uri) => {
          const p = $('brand-logo-preview');
          if (p) { p.src = uri; p.style.display = ''; }
          schedulePreview(0);
        }).catch(() => {});
      }
    }
    if (t0.closest && t0.closest('#branding-card')) schedulePreview();
    refreshBar();
  }

  function onClick(ev) {
    const el = ev.target && ev.target.closest ? ev.target.closest('button') : null;
    if (!el) return;
    if (el.dataset.sh) { showSection(el.dataset.sh); return; }
    if (el.id === 'sh-save') { save(); return; }
    if (el.id === 'sh-discard') { discard(); return; }
    if (el.id === 'tax-cat-apply') { applyCategoryRate(); return; }
    if (el.dataset.shW) { S.pvWidth = Number(el.dataset.shW) === 58 ? 58 : 80; syncPreviewToggles(); schedulePreview(0); return; }
    if (el.dataset.shL) { S.pvLang = el.dataset.shL === 'ar' ? 'ar' : 'en'; syncPreviewToggles(); schedulePreview(0); }
  }

  window.SettingsHub = { render, sections, open, sampleSale };
})();
