/* Setup guide (Phase 1b, item 5): the "Get your shop ready" checklist.
 *
 * WHAT IT IS. A short list of the things a new shop has to do once, each with a
 * button that takes the owner to the place where it is done. It shows as a card
 * on the owner's home screen (mount) and as a dialog the owner asks for from the
 * account menu, the help dialog or the command palette (open).
 *
 * WHERE THE ANSWERS COME FROM. GET /api/sub/retail/setup-checklist tells us, per
 * step, done true / false / null ("the server cannot tell"). Two steps are known
 * only to this browser (a test print was sent, WhatsApp Desktop is set up), so
 * they are ticked here and kept in localStorage. The server's own `detail`
 * sentences are English only, so this file carries its own translated label per
 * step id and never prints the server's text.
 *
 * WHAT IT MUST NEVER DO.
 *   - Show to anyone but the owner. The route answers 403 OWNER_ONLY for others,
 *     and mount()/open() also refuse on the client so a cashier never even asks.
 *   - Float over the till. It is a card in the owner's home page flow, or a
 *     dialog the owner opened on purpose. Nothing here positions itself over a
 *     screen unasked.
 *   - Stay at 100%. A finished checklist renders nothing.
 *   - Count a step it cannot verify as done. A `null` step stays open until the
 *     owner ticks it by hand ("I did this"); a `false` step is a fact and can
 *     only become true by doing the thing.
 *
 * No dependencies beyond what the shell loads: t(), RetailSystem, SubsystemApp,
 * and (for the step buttons) SettingsHub. Each is checked at use time.
 */
(function () {
  'use strict';

  const ROUTE = '/api/sub/retail/setup-checklist';
  const CLIENT_KEY = 'aura_setup_ticks';
  const CSS_ID = 'aura-setup-guide-css';
  const RING_R = 26;
  const RING_C = 2 * Math.PI * RING_R;

  // Steps known only to this browser. Never returned by the server.
  const CLIENT_STEPS = ['printer_tested', 'whatsapp_desktop'];

  // window.t takes ONE argument (i18n.js), so {name} slots are filled here,
  // after translation, exactly as the other modules do.
  const tr = (s, vars) => {
    let out = typeof window.t === 'function' ? window.t(s) : s;
    if (vars) Object.keys(vars).forEach((k) => { out = out.split('{' + k + '}').join(String(vars[k])); });
    return out;
  };
  const R = () => window.RetailSystem;
  const esc = (s) => (R() && R()._esc ? R()._esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])));

  function isOwner() { return !!(window.SubsystemApp && SubsystemApp.role === 'admin'); }

  // Step catalogue, in the order the owner should do them. `go` is what the
  // button does. Labels are literal t() calls so the i18n key extractor sees them.
  function catalogue() {
    const hub = (section) => () => { if (window.SettingsHub && SettingsHub.open(section)) return; if (window.SubsystemApp && SubsystemApp._navigate) SubsystemApp._navigate('admin-center'); };
    const nav = (screen) => () => { if (window.SubsystemApp && SubsystemApp._navigate) SubsystemApp._navigate(screen); };
    // The real WhatsApp setup is its own page (the sidebar's WhatsApp entry), not a
    // Settings hub card: go through the shell's leave guard, like the sidebar does.
    const whatsappPage = () => {
      if (window.SubsystemApp && SubsystemApp._leaveToPage) SubsystemApp._leaveToPage('/static/whatsapp.html');
      else window.location.href = '/static/whatsapp.html';
    };
    // neverManual: the owner can NOT tick it by hand; only the server's fact closes it.
    // optional:    not counted in the total, the percentage or "Next:".
    return [
      { id: 'licence', label: tr('Activate your licence'), hint: tr('Your licence lets this computer ring up sales.'), go: hub('plan-devices'), cta: tr('Open Plan & devices'), neverManual: true },
      { id: 'main_computer', label: tr('Choose the main computer'), hint: tr('One computer in the shop keeps the other tills in step.'), go: hub('plan-devices'), cta: tr('Open Plan & devices') },
      { id: 'shop_name_logo', label: tr('Add your shop name'), hint: tr('It prints at the top of every receipt. A logo is a nice extra, but you can skip it.'), go: hub('shop-receipt'), cta: tr('Open Shop & receipt') },
      { id: 'default_gst', label: tr('Set your sales tax'), hint: tr('Choose how tax is worked out on a sale.'), go: hub('tax'), cta: tr('Open Tax') },
      { id: 'products', label: tr('Add your products'), hint: tr('Add the items you sell, one by one or from a file.'), go: nav('products'), cta: tr('Open Products') },
      { id: 'cashier_invited', label: tr('Add a cashier'), hint: tr('Give each person their own sign-in.'), go: hub('team'), cta: tr('Open Team') },
      { id: 'readable_numbers', label: tr('Choose readable receipt numbers'), hint: tr('Short numbers such as AMM-1-00001 are easier to read out loud.'), go: hub('tills'), cta: tr('Open Tills') },
      { id: 'printer_tested', label: tr('Send a test receipt'), hint: tr('Check that your printer works before the first customer.'), go: hub('hardware'), cta: tr('Open Hardware'), client: true },
      { id: 'whatsapp_desktop', label: tr('Set up WhatsApp on this computer'), hint: tr('Send receipts to customers on WhatsApp.'), go: whatsappPage, cta: tr('Open WhatsApp'), client: true, manual: true },
      { id: 'first_backup', label: tr('Make your first backup'), hint: tr('A copy of your shop data, kept safe outside this computer.'), go: hub('data-backup'), cta: tr('Open Data & backup') },
      { id: 'jofotara', label: tr('Connect e-invoicing'), hint: tr('Jordan asks shops to send invoices to the tax office.'), note: tr('Optional for now. Waiting on registration with the tax authority; there is nothing to finish here until that is approved.'), go: hub('tax'), cta: tr('Open Tax'), optional: true },
    ];
  }

  // ── what this browser knows ───────────────────────────────────────────────
  // Ticks are kept per COMPANY and per USER. They used to live under one global
  // key, so on a shared computer a cashier-turned-owner (or a second shop's
  // owner) inherited the previous person's "I did this". The session carries the
  // user id (and a company field when the server sends one); whichever is
  // missing falls back to a fixed word so the key is still stable.
  function ticksKey() {
    const u = (window.SubsystemApp && SubsystemApp.currentUser) || {};
    const company = u.company_id || (u.company && (u.company.id || u.company)) || 'co';
    return CLIENT_KEY + ':' + String(company) + ':' + String(u.id || 'anon');
  }
  function parseTicks(raw) {
    try {
      const obj = raw ? JSON.parse(raw) : {};
      return obj && typeof obj === 'object' ? obj : {};
    } catch (e) { return {}; }
  }
  function readTicks() {
    try {
      const scoped = localStorage.getItem(ticksKey());
      if (scoped !== null) return parseTicks(scoped);
      // Nothing under the new key yet: read the OLD global key once, so ticks made
      // before this change are not lost. The first write below creates the new key
      // and from then on the old one is never read for this person again.
      return parseTicks(localStorage.getItem(CLIENT_KEY));
    } catch (e) { return {}; }
  }
  function writeTicks(obj) {
    try { localStorage.setItem(ticksKey(), JSON.stringify(obj)); } catch (e) { /* private window: the tick just does not stick */ }
  }
  function tick(id, on) {
    const cur = readTicks();
    if (on === false) delete cur[id]; else cur[id] = true;
    writeTicks(cur);
    refresh();
  }

  // ── the server's answer, folded together with the browser's ────────────────
  const S = { steps: null, loading: null, seq: 0 };

  async function load() {
    const mine = ++S.seq;
    const p = (async () => {
      let resp = null;
      // Readable numbers cannot be switched on until multi-computer support
      // lands (the numbering route says `available:false`). A step nobody can
      // finish would hold the card below 100% for ever, so it is left out while
      // unavailable. Only an explicit `false` hides it: a failed or older answer
      // keeps the step, because a missing step is the worse mistake.
      let readableUnavailable = false;
      const numbering = (async () => {
        try {
          const n = await R()._get('/api/sub/retail/numbering');
          readableUnavailable = !!(n && n.status === 'success' && n.data && n.data.available === false);
        } catch (e) { readableUnavailable = false; }
      })();
      // The server's shop step wants a name AND a logo. A logo is optional, so a
      // saved shop name alone must close the step: ask the branding settings
      // directly (the same saved value the server reads) and let a non-empty name win.
      let hasShopName = false;
      const branding = (async () => {
        try {
          const b = await R()._get('/api/sub/retail/settings/branding');
          hasShopName = !!(b && b.status === 'success' && b.data && String(b.data.branding_business_name || '').trim());
        } catch (e) { hasShopName = false; }
      })();
      try { resp = await R()._get(ROUTE); } catch (e) { resp = null; }
      await numbering;
      await branding;
      const rows = resp && resp.status === 'success' && resp.data && Array.isArray(resp.data.steps) ? resp.data.steps : null;
      if (mine !== S.seq) return S.steps;
      if (!rows) { S.steps = null; return null; }
      const byId = {};
      rows.forEach((r) => { byId[r.id] = r.done; });
      const ticks = readTicks();
      S.steps = catalogue().map((c) => {
        let done;
        if (c.id === 'readable_numbers' && readableUnavailable) return null;
        if (c.client) done = !!ticks[c.id];
        else if (!(c.id in byId)) return null;                // a step this server does not know: leave it out
        else if (byId[c.id] === true) done = true;
        else if (c.id === 'shop_name_logo' && hasShopName) done = true;   // name alone is enough; the logo is optional
        else if (c.neverManual) done = false;                 // a licence is a fact, never a tick (a stale tick is ignored too)
        else if (byId[c.id] === null) done = !!ticks[c.id];   // "cannot tell": only the owner's own tick closes it
        else done = false;
        return Object.assign({}, c, { done, unknown: byId[c.id] === null && !c.neverManual, server: byId[c.id] });
      }).filter(Boolean);
      return S.steps;
    })();
    S.loading = p;
    return p;
  }

  function summary(steps) {
    // Optional steps (e-invoicing) are listed but never counted: they must not
    // hold the card below 100%, and must not be offered as "Next".
    const req = steps.filter((s) => !s.optional);
    const total = req.length;
    const done = req.filter((s) => s.done).length;
    return { total, done, pct: total ? done / total : 0, next: req.find((s) => !s.done) || null };
  }

  // ── drawing ────────────────────────────────────────────────────────────────
  function injectCss() {
    if (document.getElementById(CSS_ID)) return;
    const st = document.createElement('style');
    st.id = CSS_ID;
    // Tokens and logical properties only, like the rest of the hub: the same
    // rules serve sand, night and RTL. The accent colour is left to the buttons.
    st.textContent = `
      #setup-guide-mount:not(:empty) { margin-block-end: var(--space-comfy); }
      .sg-card { display: flex; align-items: center; gap: var(--space-comfy); flex-wrap: wrap; }
      .sg-ring { flex: 0 0 auto; inline-size: 64px; block-size: 64px; position: relative; }
      .sg-ring svg { inline-size: 100%; block-size: 100%; transform: rotate(-90deg); }
      html[dir="rtl"] .sg-ring svg { transform: rotate(-90deg) scaleY(-1); }
      .sg-ring-track { fill: none; stroke: var(--border-default); stroke-width: 6; }
      .sg-ring-fill { fill: none; stroke: var(--text-primary); stroke-width: 6; stroke-linecap: round; transition: stroke-dashoffset var(--duration-normal, 300ms); }
      .sg-ring-num { position: absolute; inset: 0; display: grid; place-items: center; font-size: var(--text-size-meta); font-weight: var(--text-weight-semibold); color: var(--text-primary); }
      .sg-body { flex: 1 1 220px; min-inline-size: 0; }
      .sg-title { margin: 0; font-size: var(--text-size-body-lg); color: var(--text-primary); }
      .sg-sub { margin: var(--space-snug) 0 0; font-size: var(--text-size-body); color: var(--text-tertiary); }
      .sg-actions { display: flex; gap: var(--space-base); flex-wrap: wrap; }
      .sg-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--space-tight); }
      .sg-step { display: flex; align-items: center; gap: var(--space-base); padding: var(--space-base); border: 1px solid var(--border-default); border-radius: var(--radius-control); }
      .sg-step.is-done { background: var(--surface-sunken); }
      .sg-mark { flex: 0 0 auto; inline-size: 24px; block-size: 24px; display: grid; place-items: center; border-radius: 50%; border: 2px solid var(--border-strong); color: var(--text-primary); font-size: var(--text-size-meta); }
      .sg-step.is-done .sg-mark { background: var(--text-primary); color: var(--surface-raised); border-color: var(--text-primary); }
      .sg-text { flex: 1 1 auto; min-inline-size: 0; }
      .sg-label { display: block; font-size: var(--text-size-body); color: var(--text-primary); }
      .sg-step.is-done .sg-label { color: var(--text-secondary); }
      .sg-hint { display: block; font-size: var(--text-size-meta); color: var(--text-tertiary); }
      .sg-step-actions { flex: 0 0 auto; display: flex; gap: var(--space-tight); flex-wrap: wrap; justify-content: flex-end; }
      .sg-overlay { position: fixed; inset: 0; z-index: 100000; display: grid; place-items: center; padding: var(--space-comfy); background: rgba(0, 0, 0, 0.45); }
      .sg-dialog { inline-size: min(560px, 100%); max-block-size: min(90vh, 720px); overflow: auto; padding: var(--space-roomy); background: var(--surface-raised); border: 1px solid var(--border-strong); border-radius: var(--radius-card); display: flex; flex-direction: column; gap: var(--space-comfy); }
      .sg-dialog-head { display: flex; align-items: center; gap: var(--space-comfy); }
    `;
    document.head.appendChild(st);
  }

  function ringHtml(sum) {
    const off = RING_C * (1 - sum.pct);
    return `<span class="sg-ring" aria-hidden="true">
      <svg viewBox="0 0 64 64"><circle class="sg-ring-track" cx="32" cy="32" r="${RING_R}"></circle>
      <circle class="sg-ring-fill" cx="32" cy="32" r="${RING_R}" stroke-dasharray="${RING_C.toFixed(2)}" stroke-dashoffset="${off.toFixed(2)}"></circle></svg>
      <span class="sg-ring-num"><bdi dir="ltr">${sum.done}/${sum.total}</bdi></span></span>`;
  }

  // Returns HTML (already escaped). The two numbers sit in <bdi dir="ltr"> so an
  // Arabic sentence does not reorder "3 of 10" into "10 of 3".
  function progressHtml(sum) {
    const num = (n) => '<bdi dir="ltr">' + esc(String(n)) + '</bdi>';
    return esc(tr('{done} of {total} steps done')).split('{done}').join(num(sum.done)).split('{total}').join(num(sum.total));
  }

  function cardHtml(steps) {
    const sum = summary(steps);
    const next = sum.next;
    return `<div class="sub-chart-card sg-card" id="setup-guide-card" role="region" aria-label="${esc(tr('Get your shop ready'))}">
      ${ringHtml(sum)}
      <div class="sg-body">
        <h3 class="sg-title">${esc(tr('Get your shop ready'))}</h3>
        <p class="sg-sub">${progressHtml(sum)}${next ? ' · ' + esc(tr('Next: {step}', { step: next.label })) : ''}</p>
      </div>
      <div class="sg-actions">
        <button type="button" class="ret-btn ret-btn-primary" data-sg="open">${esc(tr('See the steps'))}</button>
      </div>
    </div>`;
  }

  function stepsHtml(steps) {
    return `<ul class="sg-list">${steps.map((s) => `
      <li class="sg-step${s.done ? ' is-done' : ''}" data-step="${esc(s.id)}">
        <span class="sg-mark" aria-hidden="true">${s.done ? '&#10003;' : ''}</span>
        <span class="sg-text"><span class="sg-label">${esc(s.label)}${s.optional ? ` <span class="sh-note sg-optional">(${esc(tr('Optional'))})</span>` : ''}</span><span class="sg-hint">${esc(s.hint)}</span>${(s.optional && s.note && !s.done) ? `<span class="sg-hint sg-waiting">${esc(s.note)}</span>` : ''}</span>
        <span class="sg-step-actions">
          ${s.done ? `<span class="sh-note">${esc(tr('Done'))}</span>` : `<button type="button" class="ret-btn" data-sg-go="${esc(s.id)}">${esc(s.cta)}</button>`}
          ${(!s.done && !s.neverManual && (s.unknown || s.manual)) ?`<button type="button" class="ret-btn" data-sg-tick="${esc(s.id)}">${esc(tr('I did this'))}</button>` : ''}
          ${(s.done && (s.client || s.unknown)) ? `<button type="button" class="ret-btn" data-sg-untick="${esc(s.id)}">${esc(tr('Undo'))}</button>` : ''}
        </span>
      </li>`).join('')}</ul>`;
  }

  // ── the card on the owner's home screen ────────────────────────────────────
  let mounted = null;   // the element the card lives in, or null

  async function mount(el) {
    if (!el) return null;
    mounted = el;
    if (!isOwner()) { el.innerHTML = ''; return null; }   // a cashier never even asks
    injectCss();
    const steps = await load();
    if (mounted !== el || !el.isConnected) return null;   // the screen changed while we waited
    // Route failed: show nothing rather than a wrong number. 100%: the card goes away.
    // The 100% test uses the REQUIRED count: an optional step still open must not keep the card up.
    if (!steps || !steps.length || summary(steps).done >= summary(steps).total) { el.innerHTML = ''; return null; }
    el.innerHTML = cardHtml(steps);
    const btn = el.querySelector('[data-sg="open"]');
    if (btn) btn.addEventListener('click', () => open());
    return el.firstElementChild;
  }

  // ── the dialog the owner asks for ──────────────────────────────────────────
  let dlg = null;

  function closeDialog() {
    if (dlg) { dlg.remove(); dlg = null; }
    document.removeEventListener('keydown', onKey, true);
  }
  function onKey(ev) { if (ev.key === 'Escape') { ev.stopPropagation(); closeDialog(); } }

  function paintDialog() {
    if (!dlg || !S.steps) return;
    const sum = summary(S.steps);
    dlg.querySelector('.sg-dialog').innerHTML = `
      <div class="sg-dialog-head">${ringHtml(sum)}
        <div class="sg-body"><h2 class="sg-title" id="sg-dlg-title">${esc(tr('Get your shop ready'))}</h2>
        <p class="sg-sub">${sum.done >= sum.total ? esc(tr('Everything is set up.')) : progressHtml(sum)}</p></div>
        <button type="button" class="ret-btn" data-sg="close" id="sg-close">${esc(tr('Close'))}</button>
      </div>
      ${stepsHtml(S.steps)}`;
  }

  async function open() {
    if (!isOwner()) return false;
    injectCss();
    closeDialog();
    const overlay = document.createElement('div');
    overlay.className = 'sg-overlay';
    overlay.innerHTML = `<div class="sg-dialog" role="dialog" aria-modal="true" aria-labelledby="sg-dlg-title"><p class="sh-note">${esc(tr('Loading...'))}</p></div>`;
    document.body.appendChild(overlay);
    dlg = overlay;
    document.addEventListener('keydown', onKey, true);
    overlay.addEventListener('click', (ev) => {
      if (ev.target === overlay) { closeDialog(); return; }
      const b = ev.target.closest && ev.target.closest('button');
      if (!b) return;
      if (b.dataset.sg === 'close') { closeDialog(); return; }
      if (b.dataset.sgGo) {
        const step = (S.steps || []).find((x) => x.id === b.dataset.sgGo);
        closeDialog();
        if (step) step.go();
        return;
      }
      if (b.dataset.sgTick) { tick(b.dataset.sgTick, true); return; }
      if (b.dataset.sgUntick) { tick(b.dataset.sgUntick, false); }
    });
    const steps = await load();
    if (dlg !== overlay) return true;
    if (!steps) {
      overlay.querySelector('.sg-dialog').innerHTML = `<p class="sh-note">${esc(tr('Could not load the setup steps.'))}</p><button type="button" class="ret-btn" data-sg="close">${esc(tr('Close'))}</button>`;
      return true;
    }
    paintDialog();
    const c = overlay.querySelector('#sg-close');
    if (c) c.focus();
    return true;
  }

  // Re-read and redraw whatever is on screen. Called after a tick, and by the
  // hub/printer code after the thing a step asks for has been done.
  async function refresh() {
    if (!isOwner()) return;
    await load();
    if (dlg) paintDialog();
    if (mounted && mounted.isConnected) mount(mounted);
  }

  window.AuraSetupGuide = { open, mount, tick, refresh, isDone: (id) => !!readTicks()[id], ticksKey };
})();
