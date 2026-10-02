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
  const SECTION_ORDER = ['shop-receipt', 'tax', 'credit', 'tills', 'hardware', 'display', 'team', 'data-backup', 'plan-devices'];

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
    tills: null,         // {data: last GET /tills, raw: {terminal_uid: typed text}}
    ready: false,        // true once the baseline is taken; refreshBar records only then
    carry: null,         // unsaved values to bring back across a theme / language re-render
    holding: {},         // group name -> true while its fields are still loading (never "dirty")
  };

  // `section` says which left-hand section a group's fields live in, so the bar
  // can name (and link to) the section that still has unsaved changes (F6).
  //
  // Tax mode and the default tax rate are SEPARATE groups on purpose (F3): they
  // are two different server calls, so one can be saved while the other is
  // refused, and each needs its own baseline. As one group, "mode saved, rate
  // refused, Discard" put the OLD mode back on screen while the server held the
  // new one.
  const GROUPS = {
    brand: {
      section: 'shop-receipt',
      fields: ['brand-name', 'brand-tax', 'brand-address', 'brand-phone', 'brand-header', 'brand-footer',
        'brand-receipt-lang', 'brand-show-cashier', 'brand-show-customer', 'brand-whatsapp', 'brand-logo-file'],
      save: () => R()._saveBranding().then(async (ok) => {
        if (ok) {
          // The chosen file has been uploaded by the saver: drop it from the
          // picker so the next Save does not upload it a second time.
          const f = $('brand-logo-file');
          if (f) f.value = '';
          await R()._loadBrandingForm();
          return true;
        }
        if (R()._brandingSaveState === 'logo-refused') {
          // The details went through and the LOGO did not. Re-base every text
          // field (the server has them now) but keep the old picker baseline, so
          // the group stays dirty on the logo alone: the bar stays up, the
          // refused file stays chosen, and the preview shows the server's logo
          // (the saver put it back). Discard then clears the picker.
          const keepFile = (S.baseline.brand || {})['brand-logo-file'];
          const snap = snapGroup('brand');
          snap['brand-logo-file'] = keepFile === undefined ? '' : keepFile;
          S.baseline.brand = snap;
          schedulePreview(0);
        }
        return false;
      }),
    },
    taxMode: {
      section: 'tax',
      fields: ['tax-mode'],
      save: async () => {
        try {
          const resp = await R()._post('/api/sub/retail/settings/tax', { tax_calculation_mode: $('tax-mode').value });
          if (!resp || resp.status !== 'success') {
            toast((resp && resp.message) || t('Could not save the tax setting.'), 'error');
            return false;
          }
          toast(t('Tax setting saved.'), 'success');
          return true;
        } catch (e) {
          console.error(e);
          toast(t('Could not save the tax setting.'), 'error');
          return false;
        }
      },
    },
    taxRate: { section: 'tax', fields: ['tax-default-rate'], save: () => R()._saveDefaultTaxRate() },
    credit: {
      section: 'credit',
      fields: ['credit-mode', 'credit-limit', 'credit-enforce', 'credit-loyalty'],
      save: () => saveCredit(),
    },
    deviceBranch: { section: 'tills', fields: ['device-branch-select'], save: () => R()._saveDeviceBranch() },
    businessDay: { section: 'tills', fields: ['business-day-tz', 'business-day-hour'], save: () => R()._saveBusinessDay() },
    numbering: { section: 'tills', fields: ['num-branch-code', 'num-readable', 'num-till'], save: () => saveNumbering() },
    // The plan for the shop's till numbers (a hidden field that mirrors what is
    // typed in the Tills card; see "tills" below). Absent on any computer that
    // is not the main one, so there it is never dirty.
    tillPlan: { section: 'tills', fields: ['till-plan'], save: () => saveTills() },
    einvoice: { section: 'tax', fields: ['einvoicing-regime'], save: () => R()._saveEinvoicingRegime() },
    // Device-local (F7): saved to this computer's localStorage, not the server,
    // so it cannot be refused. It joins the one bar instead of keeping a Save
    // button of its own.
    printer: {
      section: 'hardware',
      fields: ['pr-width', 'pr-device', 'pr-host', 'pr-port', 'pr-auto-kick'],
      save: async () => !!R()._savePrinterSettings(),
    },
  };
  const GROUP_ORDER = ['brand', 'taxMode', 'taxRate', 'credit', 'deviceBranch', 'businessDay', 'numbering', 'tillPlan', 'einvoice', 'printer'];

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
    // ONE comparison for the Save bar, the leave guard and the browser prompt.
    // Until the hub has finished filling (S.ready), S.baseline is still empty, so
    // every field "differs" from it -- and the leave guard, which is not gated on
    // S.ready like the bar is, used to call a hub nobody touched "unsaved" (a
    // verifier saw 'Discard unsaved changes?' 15 times in 15 rounds on a slow
    // load). Nothing typed cannot be unsaved: report clean until the baseline is real.
    if (!S.ready) return [];
    return GROUP_ORDER.filter((g) => {
      if (S.holding[g]) return false; // still loading: its fields are not real values yet
      const now = snapGroup(g);
      if (!Object.keys(now).length) return false;
      return !sameSnap(now, S.baseline[g] || {});
    });
  }

  // The sections that still hold unsaved changes, in left-hand order, so the bar
  // can name them (F6). After a multi-section Save where one section was refused,
  // the person is otherwise looking at a bar with no hint of WHERE.
  function dirtySections(groups) {
    const seen = [];
    (groups || dirtyGroups()).forEach((g) => { const s = GROUPS[g].section; if (seen.indexOf(s) === -1) seen.push(s); });
    return SECTION_ORDER.filter((s) => seen.indexOf(s) !== -1);
  }
  function drawWhere(groups) {
    const box = $('sh-savebar-where');
    if (!box) return;
    const labels = {};
    sections().forEach((s) => { labels[s.id] = s.label; });
    const ids = dirtySections(groups);
    box.innerHTML = ids.length
      ? esc(t('Unsaved in:')) + ' ' + ids.map((id) =>
        `<button type="button" class="sh-savebar-link" data-sh-goto="${esc(id)}">${esc(labels[id] || id)}</button>`).join(', ')
      : '';
  }
  function refreshBar() {
    const bar = $('sh-savebar');
    if (!bar) return;
    const groups = dirtyGroups();
    bar.hidden = groups.length === 0;
    drawWhere(groups);
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
  // this is NOT the leave guard (hubLeaveGuard below asks before leaving; this
  // only survives the re-render a theme / language switch causes).
  //
  // P1b verifier F2: the first version brought stale values back too eagerly --
  // type, navigate AWAY, switch the theme elsewhere, come back within 15 s, and
  // the abandoned name was there (and a Save would have stored it); and an
  // owner's unsaved choice appeared in the NEXT signed-in user's hub. So a
  // carry now also needs ALL of:
  //   * the same signed-in person that typed it (whoAmI);
  //   * the hub still on screen when the theme / language switch happened --
  //     watchHubLeft() drops the carry the moment the hub is taken off the page
  //     for any reason other than that switch;
  //   * it is used once (takeCarry empties it) and expires after 15 s.
  const CARRY_MAX_AGE_MS = 15000;
  const CARRY_SKIP = ['brand-logo-file']; // a file input cannot be filled from script
  function envStamp() {
    return (document.documentElement.getAttribute('data-theme') || '') + '|' +
      ((window.AuraI18n && AuraI18n.current) || '');
  }
  function whoAmI() {
    const u = (window.SubsystemApp && SubsystemApp.currentUser) || {};
    return String(u.id != null ? u.id : (u.email || u.username || u.name || ''));
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
      ? { values, section: S.current, env: envStamp(), at: Date.now(), who: whoAmI() }
      : null;
  }
  function takeCarry() {
    const c = S.carry;
    S.carry = null;
    if (!c || c.env === envStamp() || c.who !== whoAmI() || Date.now() - c.at > CARRY_MAX_AGE_MS) return null;
    return c;
  }
  // The hub leaving the page for a reason that is NOT a theme / language switch
  // (navigating to another section, signing out, a relogin) forgets the carry.
  // The check runs in a microtask after the DOM change, by which time the
  // theme/language attribute has already changed if a switch caused it -- in
  // that case the carry is exactly what the next hub render is about to take.
  let watchedRoot = null;
  let leaveObserver = null;

  // ── the "Discard unsaved changes?" leave warning ───────────────────────────
  // Every way out of the hub asks the guards registered here first: section
  // changes and sign out (app-shell _navigate / logout), page loads to the
  // Licence / E-invoicing / WhatsApp / LAN-hub pages and the brand logo and
  // "make this the main computer" (app-shell _leaveToPage / exit / claim), and
  // reload / back / close-window (the browser's own prompt, armed through the
  // isDirty probe below). See the exits list in app-shell.js "Leave guards". The hub registers ONE guard while it is on screen
  // and drops it the moment it is not (a guard left behind would question the
  // person on every other screen). Answering Yes puts the saved values back and
  // lets them go; No keeps them on the screen with every typed value and the
  // Save bar exactly as they were. Sections inside the hub are not "leaving".
  let dropLeaveGuard = null;
  function unregisterHubGuard() {
    if (dropLeaveGuard) { try { dropLeaveGuard(); } catch (e) { /* nothing to undo */ } }
    dropLeaveGuard = null;
  }
  function hubIsDirty() {
    return !!(S.ready && S.root && S.root.isConnected && dirtyGroups().length);
  }
  async function hubLeaveGuard() {
    // A guard that outlived its screen must not ask about a screen that is gone.
    if (!S.root || !S.root.isConnected) { unregisterHubGuard(); return true; }
    if (!dirtyGroups().length) return true;
    const leave = await R()._confirm({
      title: t('Discard unsaved changes?'),
      message: t('You changed some settings and did not save them. If you leave now, they are lost.'),
      confirmLabel: t('Discard'),
      cancelLabel: t('Keep editing'),
      danger: true,
    });
    if (!leave) return false;
    await discard(); // also empties the carry, so a quick return does not bring the edits back
    S.carry = null;
    return true;
  }
  function registerHubGuard() {
    unregisterHubGuard(); // a re-render must not stack a second copy
    const shell = window.SubsystemApp;
    if (!shell || typeof shell.registerLeaveGuard !== 'function') return;
    // isDirty lets app-shell's one beforeunload handler (reload / back / close
    // window) ask the browser to confirm ONLY when there is real unsaved work.
    dropLeaveGuard = shell.registerLeaveGuard(hubLeaveGuard, { isDirty: hubIsDirty });
  }

  function watchHubLeft(root) {
    watchedRoot = root;
    if (leaveObserver || typeof MutationObserver === 'undefined') return;
    leaveObserver = new MutationObserver(() => {
      if (!watchedRoot || watchedRoot.isConnected) return;
      watchedRoot = null;
      unregisterHubGuard();
      if (S.carry && S.carry.env === envStamp()) S.carry = null;
    });
    leaveObserver.observe(document.body || document.documentElement, { childList: true, subtree: true });
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
    S.lastSaveAt = Date.now(); // lets a sign-back-in tell "the Save that was refused" from any other refused call
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
    const failedNames = []; // P1b F13: the status names WHICH products were left alone
    for (let i = 0; i < products.length; i += 1) {
      say(t('Updating {done} of {n}…').replace('{done}', String(done)).replace('{n}', String(products.length)));
      try {
        const r = await R()._patch('/api/sub/retail/products/' + encodeURIComponent(products[i].id), { tax_rate: rate });
        if (r && r.status === 'success') done += 1; else { failed += 1; failedNames.push(products[i].name || String(products[i].id)); }
      } catch (e) { failed += 1; failedNames.push(products[i].name || String(products[i].id)); }
    }
    if (btn) btn.disabled = false;
    if (failed) {
      const SHOWN = 5;
      let names = failedNames.slice(0, SHOWN).join(', ');
      if (failedNames.length > SHOWN) names += ' ' + t('and {more} more').replace('{more}', String(failedNames.length - SHOWN));
      say(t('Changed {done} of {n} products. {failed} could not be changed: {names}. Press the button again to retry.')
        .replace('{done}', String(done)).replace('{n}', String(products.length)).replace('{failed}', String(failed))
        .replace('{names}', names));
      toast(t('Some products could not be changed.'), 'error');
    } else {
      say(t('Changed the tax rate of {n} products.').replace('{n}', String(done)));
      toast(t('Tax rate changed.'), 'success');
    }
  }

  // ── numbering: readable receipt numbers for THIS computer + the branch code ──
  // Crew B's route (retail_api.py, /numbering, owner-only):
  //   GET  ?branch_uid&till_number -> {branch_uid, branch_name,
  //          branch_code (null when the branch name has no Latin letters and
  //            nothing is saved -- the field is then EMPTY and asks; a code is
  //            never guessed here), branch_code_is_saved, branch_code_taken_by,
  //          branch_code_note, readable_enabled, till_number, books_ready,
  //          max_till_number, till_numbers_in_use, preview:{sale,return}|null,
  //          preview_is_example, preview_note}
  //   POST {branch_uid, branch_code?, readable_enabled?, till_number?} -> same
  //          body. `readable_enabled:true` needs a till_number; the server never
  //          picks one.
  // The two notes come from the server in English; they are shown through t()
  // with that exact English as the key, so an Arabic shop reads Arabic.
  //
  // MIRROR of the server's reserved list (retail_api._reserved_book_prefixes()).
  // The server stays the authority and answers 400 for a code on it; the client
  // copy only saves a round trip and lets the message be plain. If the server's
  // list grows, a code missing here is still refused there (and mapped to a
  // translated sentence by numberingErrorText) -- it just costs one round trip.
  const RESERVED_CODES = ['CHQR', 'GS', 'HOLD', 'INC', 'PAY', 'PO', 'QUO', 'REC', 'RET', 'SALE'];
  const CODE_RE = /^[A-Z]{2,4}$/;
  const NOTE_KEEPS = 'This computer keeps numbering with its current books ({code}). The new branch code applies only to tills switched on afterwards.';
  const NOTE_EXAMPLE = 'An example for till {n}. This computer is not switched on, so it still uses the long numbers.';

  const NOTE_LONG = 'This computer uses the long numbers. The two short codes at the end tell tills apart.';
  const LONG_EXAMPLE = { sale: 'SALE-000001-a1b2c3d4-e5f6a7b8', ret: 'RET-000001-a1b2c3d4-e5f6a7b8' };

  // `{slot}` fill without String.replace: a value such as "$&" must stay literal.
  function fill(s, vars) {
    let out = String(s);
    Object.keys(vars).forEach((k) => { out = out.split('{' + k + '}').join(String(vars[k])); });
    return out;
  }
  function tillMax() { return (S.numbering && Number(S.numbering.max_till_number)) || 99; }

  function serverNote(note, shownTill) {
    if (!note) return '';
    let m = /^This computer keeps numbering with its current books \((.*)\)\. The new branch code applies only to tills switched on afterwards\.$/.exec(note);
    if (m) return fill(t(NOTE_KEEPS), { code: m[1] });
    m = /^An example for till (\d+)\. This computer is not switched on, so it still uses the long numbers\.$/.exec(note);
    if (m) return fill(t(NOTE_EXAMPLE), { n: shownTill != null ? shownTill : m[1] });
    return t(note);
  }

  // Why readable numbers are not on for this computer. Since v42 the honest
  // reason is "the main computer has not given this computer a till number yet"
  // -- the old "arrive with multi-computer support" promise is gone. This is the
  // server's own sentence word for word (retail_api._READABLE_UNAVAILABLE_REASON),
  // so a server that sends it and a client that falls back to it read the same.
  const READABLE_UNAVAILABLE_TEXT = 'Readable numbers are given out by the main computer, and this computer has no till number from the main computer yet. Until it has one it uses the long numbers, so two computers can never print the same receipt number.';
  const READABLE_WITHDRAWN_TEXT = 'This computer was using readable numbers, but it has no till number from the main computer now, so it uses the long numbers again.';
  function readableUnavailableText(d) {
    // Prefer the server's reason when it sends one; t() gives it in Arabic when the
    // locale has that exact sentence, and shows the English otherwise.
    if (d && d.readable_switched_off_by_update) return t(READABLE_WITHDRAWN_TEXT);
    const fromServer = d && typeof d.unavailable_reason === 'string' ? d.unavailable_reason.trim() : '';
    return t(fromServer || READABLE_UNAVAILABLE_TEXT);
  }

  // Every 400 / 409 the route can answer, as a sentence in the person's own
  // language. The 400s carry no `code`, only English `message`, so they are
  // recognised by their wording; anything unrecognised gets the generic
  // sentence -- never the raw English.
  function numberingErrorText(resp) {
    const msg = String((resp && resp.message) || '');
    const kind = resp && resp.code;
    let m;
    if (kind === 'BRANCH_CODE_TAKEN') {
      m = /^([A-Za-z]+) is already the code of (.+)\. Each branch needs its own code\.$/.exec(msg) ||
        /^([A-Za-z]+) is also the code of (.+)\. Give this branch its own code first\.$/.exec(msg);
      return m
        ? fill(t('The code {code} already belongs to {name}. Each branch needs its own code.'), { code: m[1].toUpperCase(), name: m[2] })
        : t('That branch code already belongs to another branch. Each branch needs its own code.');
    }
    if (kind === 'READABLE_NEEDS_DEVICE_REGISTRY') {
      return t(READABLE_UNAVAILABLE_TEXT);
    }
    if (kind === 'TILL_NEEDS_MAIN_COMPUTER') {
      return t("Only the shop's main computer can give out till numbers. Do this on the main computer.");
    }
    if (kind === 'TILL_ALREADY_ALLOCATED') return tillRefusalText(resp);
    if (kind === 'DEVICE_IDENTITY_CLASH') return tillRefusalText(resp);
    if (kind === 'TILL_NUMBER_TAKEN') {
      m = /^Till number (\d+) is already used/.exec(msg);
      if (m) return fill(t('Till number {n} is already used by another computer. Choose a different number.'), { n: m[1] });
      if (/^This computer already numbers its documents with/.test(msg)) {
        return t('This computer already has readable numbers. Switch them off first, then choose a number.');
      }
      return t('That till number cannot be used. Choose a different number.');
    }
    if (/^The branch code must be 2 to 4 letters/.test(msg)) return t('The branch code must be 2 to 4 letters (A to Z).');
    m = /^'?([A-Za-z]+)'? is already used at the start of other document numbers/.exec(msg);
    if (m) return fill(t('"{code}" is already used at the start of other document numbers. Choose another code.'), { code: m[1].toUpperCase() });
    if (/^Choose this computer's till number/.test(msg)) return fill(t("Choose this till's number, from 1 to {max}."), { max: tillMax() });
    if (/no code yet/.test(msg)) return t('Choose a branch code first: 2 to 4 letters.');
    if (/^This computer has no identity yet/.test(msg)) return t('This computer has no identity yet, so it cannot have its own numbers.');
    if (/^This branch has no identity yet/.test(msg)) return t('This branch has no identity yet, so a code cannot be saved for it.');
    if (/^Unknown branch/.test(msg)) return t('This branch was not found.');
    return t('Could not save the receipt numbering.');
  }

  function showNumberingError(msg) {
    const box = $('num-error');
    if (!box) return;
    box.textContent = msg || '';
    box.hidden = !msg;
  }

  function parseTill(raw) {
    const s = String(raw == null ? '' : raw).trim();
    if (!/^\d{1,2}$/.test(s)) return null;
    const n = parseInt(s, 10);
    return n >= 1 && n <= tillMax() ? n : null;
  }

  // What is wrong with the numbers as typed right now, in the person's words, or
  // ''. `atSave` adds the "you switched it on but left something empty" checks
  // that would only nag while the person is still typing.
  function numberingProblem(atSave) {
    const input = $('num-branch-code');
    const cb = $('num-readable');
    const till = $('num-till');
    if (!input || !cb || !till) return '';
    const n = S.numbering || {};
    const raw = input.value.trim().toUpperCase();
    const baseCode = (S.baseline.numbering || {})['num-branch-code'];
    if (raw && !CODE_RE.test(raw)) return t('The branch code must be 2 to 4 letters (A to Z).');
    if (atSave && !raw && baseCode) return t('The branch code must be 2 to 4 letters (A to Z).');
    if (RESERVED_CODES.indexOf(raw) !== -1) {
      return fill(t('"{code}" is already used at the start of other document numbers. Choose another code.'), { code: raw });
    }
    const turningOn = cb.checked && !n.readable_enabled;
    if (atSave && turningOn) {
      if (!raw) return t('Choose a branch code first: 2 to 4 letters.');
      if (parseTill(till.value) == null) return fill(t("Choose this till's number, from 1 to {max}."), { max: tillMax() });
    }
    return '';
  }

  // The example, next to the fields. While this computer is switched on it is the
  // server's REAL next number (the books decide, not today's code). Otherwise it
  // is worked out from what is typed: <code><till>-000001, the same shape the
  // server's own example has (doc_series.preview_next), so typing a code shows the
  // answer at once instead of after a Save.
  function drawNumberingExample() {
    const box = $('num-preview');
    const input = $('num-branch-code');
    if (!box || !input) return;
    const n = S.numbering || {};
    const noteBox = $('num-preview-note');
    const bdi = (s) => `<bdi dir="ltr">${esc(s)}</bdi>`;
    const line = (a, b) => `${esc(t('Sale'))}: ${bdi(a)}<br>${esc(t('Return'))}: ${bdi(b)}`;
    let shownTill = null;
    if (n.available === false) {
      // Readable numbers are unavailable, so THIS computer prints the long
      // numbers whatever code is typed. Showing "ABD1-000001" here would promise
      // a receipt this computer cannot print. The shape is the real legacy one
      // (SALE-000016-<company8>-<device8>, see RetailSystem._docNo); the two
      // hex tails are obvious placeholders.
      box.innerHTML = line(LONG_EXAMPLE.sale, LONG_EXAMPLE.ret);
      if (noteBox) noteBox.textContent = t(NOTE_LONG);
      return;
    }
    if (n.preview && n.preview_is_example === false) {
      box.innerHTML = line(String(n.preview.sale || ''), String(n.preview.return || ''));
    } else {
      const raw = input.value.trim().toUpperCase();
      const ok = CODE_RE.test(raw) && RESERVED_CODES.indexOf(raw) === -1;
      const code = ok ? raw : (raw ? '' : (n.branch_code || ''));
      const cb = $('num-readable');
      const typed = parseTill($('num-till') && $('num-till').value);
      shownTill = (cb && cb.checked && typed != null) ? typed : (typed != null ? typed : (n.till_number || 1));
      if (!code) {
        box.innerHTML = `<span class="sh-num-empty">${esc(t('Type a branch code to see an example.'))}</span>`;
      } else {
        const stem = `${code}${shownTill}`;
        box.innerHTML = line(`${stem}-000001`, `${stem}-R-000001`);
      }
    }
    if (noteBox) noteBox.textContent = serverNote(n.preview_note, shownTill);
  }

  function drawTillNote() {
    const note = $('num-till-note');
    const n = S.numbering;
    if (!note || !n) return;
    const on = !!n.readable_enabled;
    const parts = [on
      ? t('This computer keeps this number while readable numbers are on. To change it, switch them off first.')
      : fill(t('A number from 1 to {max}. It is never picked for you.'), { max: tillMax() })];
    const used = (n.till_numbers_in_use || []).filter((x) => !(on && x === n.till_number));
    if (used.length) parts.push(fill(t('Already used: {list}'), { list: used.join(', ') }));
    const typed = parseTill($('num-till') && $('num-till').value);
    // A hint only: the server is the authority on a taken number (409), so the
    // person is told early but Save still asks the server.
    if (!on && typed != null && used.indexOf(typed) !== -1) {
      parts.push(fill(t('Till number {n} is already used.'), { n: typed }));
    }
    note.textContent = parts.join(' ');
  }

  // Fields that only make sense together: the till number shows once the switch
  // is on, is fixed while the server has it on, and a number typed and then
  // abandoned (switch turned off again) is cleared so it cannot leave the bar up.
  function syncNumberingFields() {
    const cb = $('num-readable');
    const till = $('num-till');
    const wrap = $('num-till-wrap');
    const n = S.numbering;
    if (!cb || !till || !wrap || !n) return;
    if (!cb.checked && !n.readable_enabled) till.value = '';
    wrap.hidden = !cb.checked;
    till.readOnly = !!n.readable_enabled;
    drawTillNote();
  }

  // The Save bar is sticky at the bottom of the window. Editing raises it, and it
  // must never sit on top of the example the person is looking at while typing.
  function keepExampleClear() {
    const ex = $('num-example-card');
    const bar = $('sh-savebar');
    if (!ex || !bar || bar.hidden) return;
    const eb = ex.getBoundingClientRect();
    const bb = bar.getBoundingClientRect();
    if (eb.bottom <= bb.top || eb.top >= bb.bottom) return;
    ex.style.scrollMarginBlockEnd = Math.ceil(bb.height + 16) + 'px';
    ex.scrollIntoView({ block: 'nearest' });
  }

  function renderNumberingBody(d) {
    const host = $('numbering-codes-body');
    if (!host) return;
    const hasCode = !!d.branch_code;
    const codeNote = !hasCode
      ? t('This branch has no code yet. Choose 2 to 4 letters (A to Z) for it.')
      : (d.branch_code_is_saved
        ? t('2 to 4 letters (A to Z).')
        : t('2 to 4 letters (A to Z). Filled in from the branch name until you change it.'));
    // Readable numbers are UNAVAILABLE (the server says so with `available:false`)
    // until the main computer has given this computer a till number. Everything
    // that turns them ON stays in this file, behind `readableAvailable`, so
    // enabling them later is the server flipping one answer, not a rewrite.
    const readableAvailable = d.available !== false;
    const reason = readableAvailable ? '' : readableUnavailableText(d);
    host.innerHTML = `
      <div class="sh-num-split">
        <div class="sh-num-fields">
          <div class="ret-field sh-num-switch">
            <input type="checkbox" id="num-readable" ${readableAvailable ? '' : 'disabled aria-describedby="num-unavailable"'} />
            <label for="num-readable">${esc(t('Readable receipt numbers on this computer'))}</label>
          </div>
          ${readableAvailable
    ? `<p class="sh-note">${esc(t('Off keeps the numbering you have now. Turning it on only changes tills that open a new shift afterwards. Books already in use keep going.'))}</p>`
    : `<p class="sh-num-unavailable" id="num-unavailable">${esc(reason)}</p>`}
          <div class="ret-field" id="num-till-wrap" hidden>
            <label for="num-till">${esc(t("This till's number"))}</label>
            <input type="number" id="num-till" min="1" max="${esc(tillMax())}" step="1" inputmode="numeric" dir="ltr" />
            <p class="sh-note" id="num-till-note"></p>
          </div>
          <div class="ret-field">
            <label for="num-branch-code">${esc(t('Branch code'))}</label>
            <input type="text" id="num-branch-code" dir="ltr" maxlength="4" autocapitalize="characters" autocomplete="off"
              placeholder="${esc(t('For example ABD'))}" />
            <p class="sh-note" id="num-code-note">${esc(codeNote)}</p>
            <p class="sh-note" id="num-code-server-note">${esc(serverNote(d.branch_code_note))}</p>
          </div>
          <p class="sh-num-error" id="num-error" role="alert" hidden></p>
        </div>
        <aside class="sh-num-example" id="num-example-card" aria-label="${esc(t('Example'))}">
          <h4 class="sh-num-example-title">${esc(t('Example'))}</h4>
          <p class="sh-num-preview" id="num-preview"></p>
          <p class="sh-note" id="num-preview-note"></p>
        </aside>
      </div>`;
    $('num-branch-code').value = d.branch_code || '';
    // While unavailable the switch always shows OFF, whatever the row still says.
    $('num-readable').checked = readableAvailable && !!d.readable_enabled;
    $('num-till').value = readableAvailable && d.readable_enabled && d.till_number != null ? String(d.till_number) : '';
    if (!readableAvailable) $('num-till').disabled = true;
    syncNumberingFields();
    drawNumberingExample();
    // Every numbering write is main-computer-only on the server (a 409
    // TILL_NEEDS_MAIN_COMPUTER otherwise), so on any other computer the fields
    // are read-only and say why, instead of letting a change be typed and then
    // refused. Strictly `=== false`: an answer that does not say stays editable.
    if (d.is_main === false) {
      ['num-readable', 'num-till', 'num-branch-code'].forEach((id) => { const el = $(id); if (el) el.disabled = true; });
      const box = $('num-error');
      if (box && !$('num-main-only')) {
        const p = document.createElement('p');
        p.className = 'sh-num-unavailable';
        p.id = 'num-main-only';
        p.textContent = t('Receipt numbers are set on the main computer. Here they can only be read.');
        box.parentNode.insertBefore(p, box);
      }
    }
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
    renderNumberingBody(d);
    rebaseline('numbering');
  }

  async function saveNumbering() {
    const input = $('num-branch-code');
    const cb = $('num-readable');
    const till = $('num-till');
    if (!input || !cb || !till) return true;
    const n = S.numbering || {};
    const base = S.baseline.numbering || {};
    // Refused HERE, before anything is sent: a reserved code or a non-letter
    // never costs a round trip, and the person reads it in plain words.
    const problem = numberingProblem(true);
    if (problem) {
      showNumberingError(problem);
      toast(problem, 'error');
      return false;
    }
    // The switch is disabled while unavailable, so this only fires if the page was
    // tampered with; it still must not send a request the server will refuse.
    if (cb.checked && n.available === false && !n.readable_enabled) {
      const msg = readableUnavailableText(n);
      showNumberingError(msg);
      toast(msg, 'error');
      return false;
    }
    const body = { branch_uid: n.branch_uid };
    if (input.value !== base['num-branch-code']) body.branch_code = input.value.trim().toUpperCase();
    if (cb.checked !== !!base['num-readable']) {
      body.readable_enabled = cb.checked;
      if (cb.checked) body.till_number = parseTill(till.value);
    }
    if (Object.keys(body).length === 1) return true;
    try {
      const resp = await R()._post('/api/sub/retail/numbering', body);
      if (!resp || resp.status !== 'success') {
        const msg = numberingErrorText(resp);
        showNumberingError(msg);
        toast(msg, 'error');
        return false;
      }
      showNumberingError('');
      if (resp.data) {
        S.numbering = resp.data;
        renderNumberingBody(resp.data);
      }
      toast(t('Receipt numbering saved.'), 'success');
      return true;
    } catch (e) {
      console.error(e);
      const msg = t('Could not save the receipt numbering.');
      showNumberingError(msg);
      toast(msg, 'error');
      return false;
    }
  }

  // ── tills: which computer is which till number (P2S, schema v42) ───────────
  // The MAIN computer gives out till numbers; every other computer only reads
  // them. Routes (retail_api.py, owner only):
  //   GET    /tills                     -> {available, is_main, branch_uid, branch_code,
  //            max_till_number, devices:[{terminal_uid, label, is_main, is_this_computer,
  //            till_number|null, suggested_till_number|null}]}
  //   POST   /tills/allocations         {terminal_uid, till_number?} or {all:true} -> same body
  //   DELETE /tills/allocations?terminal_uid=  -> same body
  // Main computer only: anything else is a 409 TILL_NEEDS_MAIN_COMPUTER.
  //
  // What is typed is a PLAN, not a change: nothing is sent until Save. The plan
  // is kept in `S.tills.raw` (the text in each computer's box) and DERIVED into
  // operations (tillOp), so there is one source of truth. It is mirrored into a
  // hidden field, #till-plan, as sorted JSON ('' when there is nothing to do),
  // which is what lets the ONE bar's dirty test, Discard and the theme /
  // language carry treat it like any other field: the baseline is '', so
  // "typed and then put back" is not dirty either.
  //
  // "Number all tills" is the one thing that is NOT a plan: it asks first and
  // acts at once, and it is never done for the person.
  const TILL_PLAN_ID = 'till-plan';
  const TILL_STYLE_ID = 'sh-tills-style';

  // Tokens only, no colour literals. Kept here (not in settings-hub.css) because
  // that file is another crew's; it can move over unchanged.
  function injectTillStyles() {
    if (document.getElementById(TILL_STYLE_ID)) return;
    const st = document.createElement('style');
    st.id = TILL_STYLE_ID;
    st.textContent = `
      .sh-till-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--space-base); }
      .sh-till-row { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
        gap: var(--space-base) var(--space-comfy); padding: var(--space-base); background: var(--surface-sunken);
        border: 1px solid var(--border-default); border-radius: var(--radius-control); }
      .sh-till-row[data-problem="1"] { border-color: var(--state-danger-border); }
      .sh-till-who { flex: 1 1 220px; min-inline-size: 0; display: flex; flex-direction: column; gap: var(--space-tight); }
      .sh-till-name { font-size: var(--text-size-body); font-weight: var(--text-weight-semibold); color: var(--text-primary); overflow-wrap: anywhere; }
      .sh-till-badge { display: inline-block; margin-inline-start: var(--space-tight); padding: 0 var(--space-tight);
        font-size: var(--text-size-meta); font-weight: normal; color: var(--text-secondary);
        border: 1px solid var(--border-default); border-radius: var(--radius-control); }
      .sh-till-hint { margin: 0; font-size: var(--text-size-meta); color: var(--text-secondary); }
      .sh-till-hint bdi { font-weight: var(--text-weight-semibold); color: var(--text-primary); }
      .sh-till-hint[data-tone="change"] { color: var(--state-warning-text); }
      .sh-till-hint[data-tone="problem"] { color: var(--state-danger-text); }
      .sh-till-ctl { display: flex; align-items: center; gap: var(--space-base); flex: 0 0 auto; }
      .sh-till-input { inline-size: 5rem; min-block-size: var(--touch-target-min); padding-inline: var(--space-base);
        font: inherit; text-align: center; color: var(--text-primary); background: var(--surface-raised);
        border: 1px solid var(--border-strong); border-radius: var(--radius-control); }
      .sh-till-input:focus-visible { outline: var(--focus-ring-width) solid var(--focus-ring-color); outline-offset: var(--focus-ring-offset); }
      .sh-till-input[aria-invalid="true"] { border-color: var(--state-danger-border); }
      .sh-till-act { min-inline-size: 7.5rem; }
      .sh-till-foot { margin-block-start: var(--space-comfy); }
      .sh-till-sub { margin: var(--space-comfy) 0 var(--space-base); font-size: var(--text-size-body); color: var(--text-secondary); }
    `;
    (document.head || document.documentElement).appendChild(st);
  }

  // Every 400 / 409 the till routes can answer, as a sentence in the person's
  // language. Only some carry a `code`; the rest are recognised by their
  // English wording. Anything unrecognised gets a generic sentence -- never
  // the raw English.
  function tillMaxN() { const d = S.tills && S.tills.data; return (d && Number(d.max_till_number)) || 99; }
  // Numbers taken from a computer that has not yet confirmed it stopped using them
  // (the server keeps them reserved). Never offered, never accepted.
  function tillReserved() {
    const d = S.tills && S.tills.data;
    return d && Array.isArray(d.pending_releases) ? d.pending_releases : [];
  }
  function tillReservedFor(n, exceptUid) {
    return tillReserved().find((r) => Number(r.till_number) === Number(n) && r.terminal_uid !== exceptUid) || null;
  }
  function tillWaitLabel(uid, n) {
    const devs = tillDevices();
    let who = uid ? devs.find((x) => x.terminal_uid === uid) : null;
    if (!who && n != null) {
      const r = tillReserved().find((x) => Number(x.till_number) === Number(n));
      who = r ? devs.find((x) => x.terminal_uid === r.terminal_uid) : null;
    }
    return (who && who.label) || t('another computer');
  }
  function tillPendingText(n, uid) {
    return fill(t('Till number {n} is still reserved for {label} until that computer is switched on and confirms it gave it up. Pick another number.'),
      { n: n, label: tillWaitLabel(uid, n) });
  }
  function tillRefusalText(resp) {
    const msg = String((resp && resp.message) || '');
    const kind = resp && resp.code;
    let m;
    if (kind === 'TILL_NEEDS_MAIN_COMPUTER') {
      return t("Only the shop's main computer can give out till numbers. Do this on the main computer.");
    }
    if (kind === 'TILL_ALREADY_ALLOCATED') {
      m = /^That computer already is till (\d+)/.exec(msg);
      return m
        ? fill(t('That computer already is till {n}. Release its number first, then give it a new one.'), { n: m[1] })
        : t('That computer already has a till number. Release its number first, then give it a new one.');
    }
    if (kind === 'TILL_NUMBER_TAKEN') {
      // The server says WHY in `reason` (+ which computer it waits for). Read that
      // first: the English sentence is only the fallback for an older server.
      if ((resp && resp.reason === 'release_pending') || /^Till number \d+ was taken away/.test(msg)) {
        const mm = /^Till number (\d+)/.exec(msg);
        const num = (resp && resp.till_number) || (mm ? mm[1] : '');
        return tillPendingText(num, resp && resp.held_by_terminal_uid);
      }
      if (/^All \d+ till numbers/.test(msg)) return t('Every till number of this branch is in use.');
      m = /^Till number (\d+) was just given/.exec(msg);
      if (m) return fill(t('Till number {n} was just given to another computer. Pick a different number.'), { n: m[1] });
      m = /^Till number (\d+) is already used/.exec(msg);
      if (m) return fill(t('Till number {n} is already used by another computer. Choose a different number.'), { n: m[1] });
      return t('That till number cannot be used. Choose a different number.');
    }
    if (kind === 'DEVICE_IDENTITY_CLASH') {
      return t("That computer is not one of this shop's active computers, so it cannot be given a till number.");
    }
    if (kind === 'READABLE_NEEDS_DEVICE_REGISTRY') return t(READABLE_UNAVAILABLE_TEXT);
    if (/no code yet/.test(msg)) return t('This branch has no code yet. Choose 2 to 4 letters for it under Receipt numbers first.');
    if (/^A till number is from/.test(msg)) return fill(t('A till number is a whole number from 1 to {max}.'), { max: tillMaxN() });
    if (/^That computer has no identity yet/.test(msg)) return t('That computer has no identity yet, so it cannot be given a till number.');
    if (/^This computer has no identity yet/.test(msg)) return t('This computer has no identity yet, so it cannot give out till numbers.');
    if (/^This branch has no identity yet/.test(msg)) return t('This branch has no identity yet, so its tills cannot be numbered.');
    if (/^Unknown branch/.test(msg)) return t('This branch was not found.');
    if (/no till number to release/.test(msg)) return t('That computer has no till number to release.');
    if (/^Say which computer/.test(msg)) return t('Choose which computer first.');
    return t('Could not change the till numbers.');
  }

  function tillDevices() {
    const d = S.tills && S.tills.data;
    return d && Array.isArray(d.devices) ? d.devices : [];
  }
  // What the box of this computer means, given the number it holds now:
  //   null  nothing to do        'r'  release its number
  //   n     give it number n     'x'  what is typed cannot be a till number
  function tillOp(dev) {
    const raw = S.tills.raw[dev.terminal_uid];
    if (raw === undefined) return null;
    const s = String(raw).trim();
    if (s === '') return dev.till_number != null ? 'r' : null;
    if (!/^\d{1,2}$/.test(s)) return 'x';
    const n = parseInt(s, 10);
    if (n < 1 || n > tillMaxN()) return 'x';
    return n === dev.till_number ? null : n;
  }
  function tillPlan() {
    const plan = {};
    tillDevices().forEach((dev) => { const op = tillOp(dev); if (op !== null) plan[dev.terminal_uid] = op; });
    return plan;
  }
  function tillPlanJson() {
    const plan = tillPlan();
    const keys = Object.keys(plan).sort();
    if (!keys.length) return '';
    const sorted = {};
    keys.forEach((k) => { sorted[k] = plan[k]; });
    return JSON.stringify(sorted);
  }
  function syncTillPlanField() {
    const el = $(TILL_PLAN_ID);
    if (el) el.value = tillPlanJson(); // no event on purpose: callers call refreshBar()
  }
  // Discard / a theme or language carry put a plan back through the hidden field.
  function tillsFromField() {
    const el = $(TILL_PLAN_ID);
    if (!el || !S.tills) return;
    let plan = {};
    try { plan = JSON.parse(el.value || '{}') || {}; } catch (e) { plan = {}; }
    const raw = {};
    tillDevices().forEach((dev) => {
      const op = Object.prototype.hasOwnProperty.call(plan, dev.terminal_uid) ? plan[dev.terminal_uid] : undefined;
      if (op === undefined) return;
      raw[dev.terminal_uid] = op === 'r' ? '' : (typeof op === 'number' ? String(op) : '0');
    });
    S.tills.raw = raw;
    renderTillsBody();
    refreshBar();
  }
  function ensureTillField(on) {
    const card = $('tills-card');
    if (!card) return;
    const el = $(TILL_PLAN_ID);
    if (on && !el) {
      const f = document.createElement('input');
      f.type = 'hidden';
      f.id = TILL_PLAN_ID;
      f.value = '';
      card.appendChild(f);
    } else if (!on && el) {
      el.remove();
    }
  }

  // What is wrong with the numbers as typed, per computer, in plain words.
  function tillProblems() {
    const out = {};
    const finals = {};
    tillDevices().forEach((dev) => {
      const op = tillOp(dev);
      if (op === 'x') { out[dev.terminal_uid] = fill(t('A till number is a whole number from 1 to {max}.'), { max: tillMaxN() }); return; }
      const n = op === 'r' ? null : (op === null ? dev.till_number : op);
      if (typeof op === 'number') {
        const held = tillReservedFor(op, dev.terminal_uid);
        if (held) { out[dev.terminal_uid] = tillPendingText(op, held.terminal_uid); return; }
      }
      if (n != null) (finals[n] = finals[n] || []).push(dev.terminal_uid);
    });
    Object.keys(finals).forEach((k) => {
      if (finals[k].length > 1) {
        finals[k].forEach((uid) => {
          out[uid] = fill(t('Till number {n} is used twice in this list. Every computer needs its own number.'), { n: k });
        });
      }
    });
    return out;
  }

  function showTillsError(msg) {
    const box = $('tills-error');
    if (!box) return;
    box.textContent = msg || '';
    box.hidden = !msg;
  }

  function tillSuggestion(dev) {
    const n = dev.suggested_till_number;
    return n != null && !tillReservedFor(n, dev.terminal_uid) ? n : null;
  }
  function tillHint(dev, op, problem) {
    const d = S.tills.data;
    const code = d.branch_code || '';
    const bdi = (s) => `<bdi dir="ltr">${esc(s)}</bdi>`;
    if (problem) return { tone: 'problem', html: esc(problem) };
    if (op === 'r') return { tone: 'change', html: esc(fill(t('Will lose its number {m} when you save.'), { m: dev.till_number })) };
    if (typeof op === 'number') {
      return { tone: 'change', html: esc(dev.till_number != null
        ? fill(t('Will change from {m} to {n} when you save.'), { m: dev.till_number, n: op })
        : fill(t('Will get till number {n} when you save.'), { n: op })) };
    }
    if (dev.till_number != null) {
      return { tone: '', html: code
        ? `${esc(t('Receipts start with'))} ${bdi(code + dev.till_number + '-')}`
        : esc(fill(t('Till number {n}.'), { n: dev.till_number })) };
    }
    return { tone: '', html: esc(tillSuggestion(dev) != null
      ? fill(t('No number yet. Next free number: {n}.'), { n: tillSuggestion(dev) })
      : t('No number yet.')) };
  }

  // The one button of a row. "Undo" whenever the box differs from what the
  // computer holds now; otherwise Release (it has a number) or Use N (it has
  // none and the server knows a free one).
  function tillActionHTML(dev, op) {
    const uid = esc(dev.terminal_uid);
    const btn = (act, label) => `<button type="button" class="ret-btn ret-btn-ghost sh-till-act" data-till-act="${act}" data-till-uid="${uid}">${esc(label)}</button>`;
    if (op !== null) return btn('undo', dev.till_number != null ? fill(t('Keep number {n}'), { n: dev.till_number }) : t('Undo'));
    if (dev.till_number != null) return btn('release', t('Release number'));
    if (tillSuggestion(dev) != null) return btn('suggest', fill(t('Use {n}'), { n: tillSuggestion(dev) }));
    return '';
  }

  function paintTillRows() {
    const st = S.tills;
    if (!st || !st.data) return;
    const problems = tillProblems();
    const byUid = {};
    tillDevices().forEach((dev) => { byUid[dev.terminal_uid] = dev; });
    document.querySelectorAll('#tills-body [data-till-row]').forEach((li) => {
      const dev = byUid[li.dataset.tillRow];
      if (!dev) return;
      const op = tillOp(dev);
      const problem = problems[dev.terminal_uid] || '';
      const hint = tillHint(dev, op, problem);
      const h = li.querySelector('[data-till-hint]');
      if (h) { h.innerHTML = hint.html; h.setAttribute('data-tone', hint.tone); }
      li.setAttribute('data-problem', problem ? '1' : '0');
      const input = li.querySelector('[data-till-input]');
      if (input) input.setAttribute('aria-invalid', problem ? 'true' : 'false');
      const slot = li.querySelector('[data-till-act-slot]');
      if (slot) {
        const want = tillActionHTML(dev, op);
        // Replaced only when it changed, so a button being pressed keeps focus.
        if (slot.dataset.now !== want) { slot.innerHTML = want; slot.dataset.now = want; }
      }
    });
    const all = $('till-number-all');
    if (all) {
      const dirty = !!tillPlanJson();
      const anyFree = tillDevices().some((dev) => dev.till_number == null);
      all.disabled = dirty || !anyFree;
      const note = $('till-all-note');
      if (note) {
        note.textContent = dirty
          ? t('Save or discard your changes first.')
          : (anyFree
            ? t('Gives every computer that has no number the next free one. You are asked first.')
            : t('Every computer already has a number.'));
      }
    }
  }

  function badgesHTML(dev) {
    return (dev.is_this_computer ? `<span class="sh-till-badge">${esc(t('This computer'))}</span>` : '') +
      (dev.is_main ? `<span class="sh-till-badge">${esc(t('Main computer'))}</span>` : '');
  }

  // Not the main computer: only text and a list. Nothing here can change a number.
  function renderTillsReadOnly(host, d) {
    const main = tillDevices().find((x) => x.is_main);
    const lead = main && main.label
      ? fill(t('Till numbers are given out on the main computer ({label}).'), { label: main.label })
      : t('Till numbers are given out on the main computer.');
    host.innerHTML = `
      <p class="sh-num-unavailable" id="tills-main-pointer">${esc(lead)}</p>
      <p class="sh-till-sub">${esc(t('Computers in this shop'))}</p>
      <ul class="sh-till-list" id="tills-readonly-list">
        ${tillDevices().map((dev) => `
          <li class="sh-till-row" data-till-row-ro="${esc(dev.terminal_uid)}">
            <div class="sh-till-who">
              <span class="sh-till-name">${esc(dev.label || t('Unnamed computer'))}${badgesHTML(dev)}</span>
            </div>
            <p class="sh-till-hint">${dev.till_number != null
    ? esc(fill(t('Till number {n}'), { n: dev.till_number }))
    : esc(t('No number yet.'))}</p>
          </li>`).join('')}
      </ul>`;
  }

  function renderTillsBody() {
    const host = $('tills-body');
    if (!host) return;
    const st = S.tills;
    const d = st && st.data;
    if (!d) {
      host.innerHTML = `<p class="sh-note">${esc(t('Till numbers are not available on this computer yet.'))}</p>`;
      return;
    }
    if (d.available === false) {
      host.innerHTML = `<p class="sh-num-unavailable">${esc(readableUnavailableText(d))}</p>`;
      return;
    }
    if (d.is_main === false) { renderTillsReadOnly(host, d); return; }
    const devs = tillDevices();
    host.innerHTML = `
      ${d.branch_code ? '' : `<p class="sh-num-unavailable" id="tills-no-code">${esc(t('This branch has no code yet. Choose 2 to 4 letters for it under Receipt numbers first.'))}</p>`}
      <ul class="sh-till-list" id="tills-list">
        ${devs.map((dev, i) => `
          <li class="sh-till-row" data-till-row="${esc(dev.terminal_uid)}" data-problem="0">
            <div class="sh-till-who">
              <span class="sh-till-name">${esc(dev.label || t('Unnamed computer'))}${badgesHTML(dev)}</span>
              <p class="sh-till-hint" id="till-hint-${i}" data-till-hint data-tone=""></p>
            </div>
            <div class="sh-till-ctl">
              <input type="text" class="sh-till-input" id="till-n-${i}" data-till-input="${esc(dev.terminal_uid)}"
                inputmode="numeric" maxlength="2" dir="ltr" autocomplete="off" aria-invalid="false"
                aria-label="${esc(fill(t('Till number of {name}'), { name: dev.label || t('Unnamed computer') }))}"
                aria-describedby="till-hint-${i}" />
              <span data-till-act-slot></span>
            </div>
          </li>`).join('')}
      </ul>
      ${tillReserved().length ? `
      <p class="sh-till-sub">${esc(t('Reserved numbers'))}</p>
      <ul class="sh-till-list" id="tills-reserved">
        ${tillReserved().map((r) => `
          <li class="sh-till-row" data-till-reserved="${esc(r.terminal_uid)}" data-till-reserved-n="${esc(String(r.till_number))}">
            <div class="sh-till-who">
              <span class="sh-till-name">${esc(fill(t('Till number {n}'), { n: r.till_number }))}</span>
              <p class="sh-till-hint">${esc(fill(t('Reserved for {label} until that computer is switched on and confirms it gave the number up.'), { label: tillWaitLabel(r.terminal_uid, r.till_number) }))}</p>
            </div>
          </li>`).join('')}
      </ul>` : ''}
      <p class="sh-num-error" id="tills-error" role="alert" hidden></p>
      <div class="sh-inline-actions sh-till-foot">
        <button type="button" class="ret-btn ret-btn-ghost" id="till-number-all">${esc(t('Number all tills'))}</button>
        <p class="sh-note" id="till-all-note"></p>
      </div>`;
    host.querySelectorAll('[data-till-input]').forEach((input) => {
      const dev = devs.find((x) => x.terminal_uid === input.dataset.tillInput);
      if (!dev) return;
      const raw = st.raw[dev.terminal_uid];
      input.value = raw !== undefined ? raw : (dev.till_number != null ? String(dev.till_number) : '');
    });
    syncTillPlanField();
    paintTillRows();
  }

  async function reloadTillData() {
    try {
      const resp = await R()._get('/api/sub/retail/tills');
      if (resp && resp.status === 'success' && resp.data) { S.tills.data = resp.data; return true; }
    } catch (e) { console.error(e); }
    return false;
  }
  async function loadTills() {
    const host = $('tills-body');
    if (!host) return;
    injectTillStyles();
    let d = null;
    try {
      const resp = await R()._get('/api/sub/retail/tills');
      if (resp && resp.status === 'success' && resp.data) d = resp.data;
    } catch (e) { d = null; }
    S.tills = { data: d, raw: {} };
    ensureTillField(!!d && d.is_main !== false && d.available !== false);
    renderTillsBody();
    rebaseline('tillPlan');
  }
  // The main computer's own number is also on the Receipt numbers card.
  async function refreshNumberingCard() {
    if (dirtyGroups().indexOf('numbering') !== -1) return; // never overwrite typing
    await loadNumbering();
  }

  function tillTyped(input) {
    if (!S.tills) return;
    S.tills.raw[input.dataset.tillInput] = input.value;
    showTillsError('');
    syncTillPlanField();
    paintTillRows();
    refreshBar();
  }
  function tillAction(act, uid) {
    const st = S.tills;
    const dev = tillDevices().find((x) => x.terminal_uid === uid);
    if (!st || !dev) return;
    if (act === 'release') st.raw[uid] = '';
    else if (act === 'undo') delete st.raw[uid];
    else if (act === 'suggest' && tillSuggestion(dev) != null) st.raw[uid] = String(tillSuggestion(dev));
    else return;
    showTillsError('');
    renderTillsBody();
    refreshBar();
    // The button that was pressed is gone; keyboard focus stays in the same row.
    const li = Array.prototype.find.call(document.querySelectorAll('#tills-body [data-till-row]'), (x) => x.dataset.tillRow === uid);
    const next = li && (li.querySelector('[data-till-act]') || li.querySelector('[data-till-input]'));
    if (next) next.focus();
  }

  async function saveTills() {
    const st = S.tills;
    if (!st || !st.data || st.data.is_main === false || !$(TILL_PLAN_ID)) return true;
    const problems = tillProblems();
    const first = Object.keys(problems)[0];
    if (first) {
      showTillsError(problems[first]);
      toast(problems[first], 'error');
      return false;
    }
    const devs = tillDevices();
    // A pure release is a DELETE. A change of number is ONE request with
    // replace:true: the server drops the old number and takes the new one in a
    // single transaction, so a refused new number leaves the computer holding the
    // old one (release-then-assign left it with none). Releases go first so a
    // number handed from one computer to another is free by then.
    const releases = [];
    const assigns = [];
    devs.forEach((dev) => {
      const op = tillOp(dev);
      if (op === null) return;
      if (typeof op === 'number') assigns.push({ dev, n: op });
      else if (dev.till_number != null) releases.push(dev);
    });
    if (!releases.length && !assigns.length) return true;
    const fail = async (resp, thrown) => {
      const msg = thrown ? t('Could not change the till numbers.') : tillRefusalText(resp);
      await reloadTillData(); // show what the server really holds now
      renderTillsBody();
      showTillsError(msg);
      toast(msg, 'error');
      return false;
    };
    let latest = null;
    try {
      for (let i = 0; i < releases.length; i += 1) {
        const resp = await R()._del('/api/sub/retail/tills/allocations?terminal_uid=' + encodeURIComponent(releases[i].terminal_uid));
        if (!resp || resp.status !== 'success') return await fail(resp);
        latest = resp.data || latest;
        // Keep the screen honest between calls: this computer no longer holds it.
        releases[i].till_number = null;
      }
      // Order the changes so a number is taken only after the computer holding it
      // has moved on (B 6->7 must go before A 4->6).
      while (assigns.length) {
        const heldBy = {};
        devs.forEach((x) => { if (x.till_number != null) heldBy[x.till_number] = x.terminal_uid; });
        let at = assigns.findIndex((a) => !heldBy[a.n] || heldBy[a.n] === a.dev.terminal_uid);
        if (at < 0) at = 0; // none can go: let the server say why
        const a = assigns.splice(at, 1)[0];
        const resp = await R()._post('/api/sub/retail/tills/allocations',
          { terminal_uid: a.dev.terminal_uid, till_number: a.n, replace: true, branch_uid: st.data.branch_uid });
        if (!resp || resp.status !== 'success') return await fail(resp);
        latest = resp.data || latest;
        a.dev.till_number = a.n;
      }
    } catch (e) {
      console.error(e);
      return await fail(null, true);
    }
    if (latest) st.data = latest; else await reloadTillData();
    st.raw = {};
    showTillsError('');
    renderTillsBody();
    await refreshNumberingCard();
    toast(t('Till numbers saved.'), 'success');
    return true;
  }

  async function numberAllTills() {
    const st = S.tills;
    const btn = $('till-number-all');
    if (!st || !st.data || st.data.is_main === false || tillPlanJson()) return;
    const yes = await R()._confirm({
      title: t('Number all tills?'),
      message: t('Every computer that has no number gets the next free one. Computers that already have a number keep it. You can release a number later.'),
      confirmLabel: t('Number all tills'),
      cancelLabel: t('Cancel'),
    });
    if (!yes) return;
    if (btn) btn.disabled = true;
    try {
      const resp = await R()._post('/api/sub/retail/tills/allocations', { all: true, branch_uid: st.data.branch_uid });
      if (!resp || resp.status !== 'success') {
        const msg = tillRefusalText(resp);
        showTillsError(msg);
        toast(msg, 'error');
        return;
      }
      st.data = resp.data || st.data;
      st.raw = {};
      showTillsError('');
      renderTillsBody();
      refreshBar();
      await refreshNumberingCard();
      toast(t('All tills are numbered.'), 'success');
    } catch (e) {
      console.error(e);
      const msg = t('Could not change the till numbers.');
      showTillsError(msg);
      toast(msg, 'error');
    } finally {
      paintTillRows();
    }
  }

  // ── the live receipt preview ───────────────────────────────────────────────
  // A fixed, invented sale (so the preview never prints a real customer). Built
  // per language because item names are DATA and are not translated by the
  // receipt's own label translator. `sampleSale()` carries no `__preview`: the
  // very same object can be handed to a real print, which is how the tests
  // prove the preview and the printed receipt say the same thing.
  // The sample's tax follows the SELECTED tax mode, worked out the way the server
  // works it out. MIRROR of core/retail/pricing.py::calculate_line (the only
  // place the two formulas are written; create_sale() calls it per line):
  //   after_discount : taxable = gross - discount ; tax = round(taxable * rate)
  //   before_discount: taxable = gross            ; tax = round(gross   * rate)
  //   total = gross - discount + tax   (both modes; tax is rounded once per line)
  // Rounding is R()._roundMoney, the till's own half-up mirror of the server's
  // ROUND_HALF_UP at the currency's minor unit. The sample used to hard-code
  // tax 1.12 / total 8.12, which is right for after_discount only (P1b F14).
  const SAMPLE_TAX_RATE = 16; // percent: Jordan's general sales tax, an invented shop
  function sampleLineFigures(gross, discount, mode) {
    const rnd = (v) => R()._roundMoney(v);
    const taxable = mode === 'before_discount' ? gross : gross - discount;
    const tax = rnd(taxable * SAMPLE_TAX_RATE / 100);
    return { tax, total: rnd(gross - discount + tax) };
  }
  function sampleSale(lang, taxMode) {
    const L = lang === 'ar' ? 'ar' : 'en';
    const mode = taxMode === 'before_discount' ? 'before_discount' : 'after_discount';
    const tr = (s) => R()._translateForLang(s, L);
    const lines = [
      { name: tr('Sample item'), quantity: 2, unit_price: 1.5, discount_amount: 0, applied_promotion: null },
      { name: tr('Sample item with offer'), quantity: 1, unit_price: 4.25, discount_amount: 0.25, applied_promotion: 'PREVIEW' },
    ];
    let subtotal = 0;
    let tax = 0;
    let total = 0;
    lines.forEach((l) => {
      const gross = R()._roundMoney(l.unit_price * l.quantity);
      const f = sampleLineFigures(gross, l.discount_amount, mode);
      subtotal += gross;
      tax += f.tax;
      total += f.total;
    });
    const amountPaid = 10;
    return {
      sale_number: 'A-000124',
      created_at: '2026-01-15 14:32:00',
      employee_name: tr('Sample cashier'),
      customer_id: 1,
      customer_name: tr('Sample customer'),
      lines,
      subtotal: R()._roundMoney(subtotal),
      tax_amount: R()._roundMoney(tax),
      total: R()._roundMoney(total),
      amount_paid: amountPaid,
      change: R()._roundMoney(amountPaid - total),
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
    const modeEl = $('tax-mode');
    const sale = sampleSale(S.pvLang, modeEl ? modeEl.value : 'after_discount');
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
          <div id="numbering-codes-body"><p class="sh-pane-loading">${esc(t('Loading…'))}</p></div>
          <details class="sh-advanced" id="numbering-advanced">
            <summary>${esc(t('Advanced'))}</summary>
            <div id="numbering-advanced-body"></div>
          </details>`;
        tills.appendChild(num);
        // The numbering books list lives under Advanced: most shops never open it.
        moveCard('doc-series-card', num.querySelector('#numbering-advanced-body'));
        const ds = $('doc-series-card');
        if (ds) ds.classList.add('sh-embedded');
        // Which computer is which till: right under the receipt numbers it feeds.
        const tc = document.createElement('div');
        tc.className = 'sub-chart-card';
        tc.id = 'tills-card';
        tc.innerHTML = `<h3 class="sh-card-title">${esc(t('Tills'))}</h3>
          <p class="sh-card-lead">${esc(t('Give each computer in the shop its own till number, so every receipt says which till printed it.'))}</p>
          <div id="tills-body"><p class="sh-pane-loading">${esc(t('Loading…'))}</p></div>`;
        tills.appendChild(tc);
      } else {
        moveCard('doc-series-card', tills);
      }
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

  // Screen size (P1c). Applies the moment a button is pressed -- it is a
  // per-device preference, not a shop setting, so it deliberately does NOT go
  // through the unsaved-changes bar. Stored by window.AuraUiScale (index.html).
  function displayCard() {
    const cur = (window.AuraUiScale && AuraUiScale.get()) || 'large';
    const opt = (v, label) => `<button type="button" data-ui-scale="${v}" aria-pressed="${cur === v}">${esc(label)}</button>`;
    return `
      <div class="sub-chart-card" id="display-card">
        <h3 class="sh-card-title">${esc(t('Screen size'))}</h3>
        <p class="sh-card-lead">${esc(t('Makes the text and buttons bigger or smaller on this computer only. It changes at once.'))}</p>
        <div class="sh-seg" role="group" aria-label="${esc(t('Screen size'))}">
          ${opt('normal', t('Normal'))}${opt('large', t('Large'))}${opt('xlarge', t('Extra large'))}
        </div>
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
      // The printer fields join the one bar as the device-local `printer` group.
      // The card's own Save button goes (one bar, one Save), and the group is
      // "holding" until the device list has filled the select -- else the bar
      // would flash "unsaved" while the saved printer name is still loading.
      S.holding.printer = true;
      try { await R()._mountPrinterCard($('sh-printer-slot')); } catch (e) { console.error(e); }
      if (!alive()) return;
      const own = $('pr-save-btn');
      if (own) own.remove();
      rebaseline('printer');
      delete S.holding.printer;
    } else if (id === 'display') {
      host.innerHTML = displayCard();
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
      // Screen size is per DEVICE and needs no authority (it changes nothing in
      // the shop's data), so it is offered to everyone who can open Settings.
      { id: 'display', label: t('Display'), visible: dev },
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
    S.tills = null;
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
            <span class="sh-savebar-where" id="sh-savebar-where"></span>
            <span class="sh-savebar-actions">
              <button type="button" class="ret-btn ret-btn-ghost" id="sh-discard">${esc(t('Discard'))}</button>
              <button type="button" class="ret-btn ret-btn-primary" id="sh-save">${esc(t('Save'))}</button>
            </span>
          </div>
        </div>
      </div>`;
    S.root = $('sh-root');
    S.holding = {};
    watchHubLeft(S.root);
    registerHubGuard();

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
      await Promise.all([loadTaxMode(), loadCategories(), loadNumbering(), loadTills()]);
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
    // The till plan: a typed box, or Discard / a theme switch putting a plan back.
    if (t0.id === TILL_PLAN_ID) { tillsFromField(); return; }
    if (t0.dataset && t0.dataset.tillInput !== undefined) { tillTyped(t0); return; }
    if (t0.id === 'credit-mode') syncCreditLimitVisibility();
    // The sample receipt's tax follows the selected mode (F14).
    if (t0.id === 'tax-mode') schedulePreview();
    if (t0.id && t0.id.indexOf('num-') === 0) {
      if (t0.id === 'num-branch-code') {
        // Letters only (A-Z): the server refuses digits, so they never get typed in.
        const up = t0.value.toUpperCase().replace(/[^A-Z]/g, '');
        if (up !== t0.value) t0.value = up;
      }
      syncNumberingFields();
      drawNumberingExample();
      // Live, plain, before Save: reserved code / wrong length. Nothing is sent.
      showNumberingError(numberingProblem(false));
      // refreshBar() below raises the Save bar; keep the example clear of it.
      refreshBar();
      keepExampleClear();
      return;
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
    if (el.dataset.uiScale) {
      if (window.AuraUiScale) AuraUiScale.set(el.dataset.uiScale);
      S.root.querySelectorAll('[data-ui-scale]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.uiScale === el.dataset.uiScale)));
      return;
    }
    if (el.dataset.sh) { showSection(el.dataset.sh); return; }
    if (el.dataset.shGoto) { showSection(el.dataset.shGoto); return; }
    if (el.id === 'sh-save') { save(); return; }
    if (el.id === 'sh-discard') { discard(); return; }
    if (el.id === 'tax-cat-apply') { applyCategoryRate(); return; }
    if (el.dataset.tillAct) { tillAction(el.dataset.tillAct, el.dataset.tillUid); return; }
    if (el.id === 'till-number-all') { numberAllTills(); return; }
    if (el.dataset.shW) { S.pvWidth = Number(el.dataset.shW) === 58 ? 58 : 80; syncPreviewToggles(); schedulePreview(0); return; }
    if (el.dataset.shL) { S.pvLang = el.dataset.shL === 'ar' ? 'ar' : 'en'; syncPreviewToggles(); schedulePreview(0); }
  }

  // The same person signed back in through the "session expired" box. The
  // screen was NOT redrawn, so what they typed is still here; the only thing
  // lost was the call that got the 401. If that call was this hub's Save
  // (a write, made within seconds of the 401), do it again instead of leaving
  // the person to notice the bar and press Save a second time. Any other refused
  // call is not ours to repeat.
  const RETRY_WINDOW_MS = 10000;
  function retryAfterRelogin(trigger) {
    if (!trigger || String(trigger.method || 'GET').toUpperCase() === 'GET') return false;
    if (!S.lastSaveAt || Math.abs(trigger.at - S.lastSaveAt) > RETRY_WINDOW_MS) return false;
    if (!hubIsDirty()) return false;
    save();
    return true;
  }
  window.SettingsHub = { render, sections, open, sampleSale, retryAfterRelogin, hasUnsaved: hubIsDirty };
})();
