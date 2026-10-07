/* Aura Retail -- the "Extras" set-up screen (EXTRAS).
 *
 * window.RetailModifiers.render(host) paints the Settings > Extras pane: the
 * shop's modifier groups ("Size", "Toppings"), their options with a price
 * change each, and which items each group is attached to.
 *
 * WHY THIS FILE EXISTS. Modifier groups shipped (schema v26) as a complete
 * backend and a till picker, but nothing let a shop CREATE one: the admin
 * routes had no caller, and 'modifier-options' sat in
 * retail_route_reachability_test.py's INTENTIONALLY_UNREACHABLE with "no
 * Settings screen exists yet". This is that screen.
 *
 * Shape: the same self-contained feature-file convention employees.js and
 * plan-devices.js follow (no build step; the settings hub calls render()).
 * HTTP goes through RetailSystem so the 401 -> re-login handling covers this
 * screen too. Modals use RetailSystem._wireModalA11y / _closeModalOverlay: the
 * shared helper owns Escape, the Tab trap and focus return, so this file has
 * no key handling of its own. Enter submits because each form is a real <form>
 * with a submit button.
 *
 * MONEY. A price change is shown with the shop currency's own decimals and a
 * sign ("+0.500", "-0.250"). It is a display of what the server stores; this
 * screen never computes a sale total (the server re-reads every price at sale
 * time, see core/retail/modifiers.py).
 *
 * VALIDATION mirrors the server (create_modifier_group / update_modifier_group)
 * so a bad group is refused on the screen with a reason, not discovered as a
 * 400: name required, min whole and >= 0, max blank (= any) or whole and >= 1
 * and not below min.
 *
 * KNOWN LIMIT, said on the screen: modifier set-up does not sync between
 * computers yet (wave W3a); each computer is set up separately.
 *
 * Arabic strings are real but "native speaker to confirm".
 */
(function () {
  'use strict';

  // User data (group, option and item names) goes in <bdi data-no-i18n>: i18n.js's
  // Arabic sweep rewrites any text node that equals a catalogue key, so an option
  // the shop named "Large" showed as "كبير" (measured in a real browser).
  var API = '/api/sub/retail';

  function t(s) {
    try {
      if (typeof window.t === 'function') return window.t(s);
    } catch (e) { /* identity */ }
    return s;
  }
  function RS() { return window.RetailSystem; }
  function esc(s) { return RS()._esc(s); }
  function fill(s, vars) {
    return String(s).replace(/\{(\w+)\}/g, function (m, k) { return vars[k] == null ? m : vars[k]; });
  }
  function toast(msg, kind) {
    if (window.SubsystemApp && typeof SubsystemApp.showToast === 'function') SubsystemApp.showToast(msg, kind);
  }

  var state = {
    host: null,
    groups: [],
    products: null,       // loaded once, the same list the till searches
    gid: null,            // group open in the dialog (null = a new one)
    overlay: null,
    editOpt: null,        // option id being edited in the dialog
    focusId: null,        // input to refocus after the dialog repaints
    localAttached: {}     // gid -> {pid: true}, only used when the server sends no product_ids
  };

  // ── pure helpers (also exposed for the test) ───────────────────────────────
  function dp() {
    try { var n = RS()._currencyDp(); return typeof n === 'number' ? n : 3; } catch (e) { return 3; }
  }
  function formatDelta(n) {
    var v = +n || 0;
    return (v < 0 ? '-' : '+') + Math.abs(v).toFixed(dp());
  }
  // Eastern Arabic-Indic digits typed on an Arabic keyboard -> ASCII.
  function digits(s) {
    return String(s == null ? '' : s).replace(/[٠-٩]/g, function (d) { return String(d.charCodeAt(0) - 1632); }).trim();
  }
  function validateGroup(raw) {
    var name = String(raw.name == null ? '' : raw.name).trim();
    if (!name) return { error: t('Give the group a name.') };
    var minS = digits(raw.min);
    var min = 0;
    if (minS !== '') {
      if (!/^\d+$/.test(minS)) return { error: t('"At least" must be a whole number, 0 or more.') };
      min = parseInt(minS, 10);
    }
    var maxS = digits(raw.max);
    var max = null;
    if (maxS !== '') {
      if (!/^\d+$/.test(maxS) || parseInt(maxS, 10) < 1) return { error: t('"Up to" must be a whole number, 1 or more (or empty for any).') };
      max = parseInt(maxS, 10);
      if (max < min) return { error: t('"Up to" cannot be smaller than "At least".') };
    }
    return { value: { name: name, min_select: min, max_select: max } };
  }
  function validateOption(raw) {
    var name = String(raw.name == null ? '' : raw.name).trim();
    if (!name) return { error: t('Give the option a name.') };
    var priceS = digits(raw.price).replace(',', '.');
    var price = 0;
    if (priceS !== '') {
      price = Number(priceS);
      if (!isFinite(price)) return { error: t('The price change must be a number, like 0.500 or -0.250.') };
    }
    var orderS = digits(raw.order);
    var order = 0;
    if (orderS !== '') {
      if (!/^-?\d+$/.test(orderS)) return { error: t('The order must be a whole number.') };
      order = parseInt(orderS, 10);
    }
    return { value: { name: name, price_delta: price, sort_order: order, is_default: !!raw.isDefault } };
  }
  function liveOptionCount(g) {
    return (g.options || []).filter(function (o) { return o.status !== 'inactive'; }).length;
  }
  function findGroup(id) {
    for (var i = 0; i < state.groups.length; i++) if (state.groups[i].id === id) return state.groups[i];
    return null;
  }
  function productName(pid) {
    var list = state.products || [];
    for (var i = 0; i < list.length; i++) if (list[i].id === pid) return list[i].name;
    return pid;
  }
  function attachedIds(g) {
    if (Array.isArray(g.product_ids)) return g.product_ids.slice();
    return null; // unknown: the server did not say
  }

  // ── styles (tokens only) ───────────────────────────────────────────────────
  var CSS = [
    '.mx-root{display:flex;flex-direction:column;gap:var(--space-base);max-inline-size:44rem;font-family:var(--font-base);color:var(--text-primary)}',
    '.mx-note{margin:0;padding:var(--space-snug) var(--space-base);border-radius:var(--radius-control);background:var(--state-info-surface);color:var(--state-info-text);border:1px solid var(--state-info-border);font-size:var(--text-size-meta)}',
    '.mx-head{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:var(--space-snug)}',
    '.mx-h{margin:0;font-size:var(--text-size-subhead);font-weight:var(--text-weight-semibold)}',
    '.mx-muted{margin:0;color:var(--text-secondary);font-size:var(--text-size-meta)}',
    '.mx-card{background:var(--surface-raised);border:1px solid var(--border-default);border-radius:var(--radius-card);padding:var(--space-base);display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:var(--space-snug)}',
    '.mx-card.off{opacity:.7}',
    '.mx-name{font-weight:var(--text-weight-semibold);font-size:var(--text-size-body)}',
    '.mx-badges{display:flex;flex-wrap:wrap;gap:var(--space-snug);align-items:center}',
    '.mx-badge{display:inline-block;padding-block:0;padding-inline:var(--space-snug);border-radius:var(--radius-pill);font-size:var(--text-size-micro);font-weight:var(--text-weight-semibold);border:1px solid var(--border-default);color:var(--text-secondary)}',
    '.mx-badge.req{color:var(--state-warning-text);background:var(--state-warning-surface);border-color:var(--state-warning-border)}',
    '.mx-badge.on{color:var(--state-success-text);background:var(--state-success-surface);border-color:var(--state-success-border)}',
    '.mx-acts{display:flex;flex-wrap:wrap;gap:var(--space-snug);align-items:center}',
    '.mx-btn{font:inherit;font-size:var(--text-size-meta);min-block-size:2.5rem;padding-inline:var(--space-base);border-radius:var(--radius-control);border:1px solid var(--border-default);background:var(--surface-raised);color:var(--text-primary);cursor:pointer}',
    '.mx-btn:hover{background:var(--surface-hover)}',
    '.mx-btn:focus-visible,.mx-input:focus-visible{outline:2px solid var(--accent-action);outline-offset:2px}',
    '.mx-btn.danger{color:var(--state-danger-text);border-color:var(--state-danger-border)}',
    '.mx-dialog{max-inline-size:36rem;inline-size:100%;max-block-size:90vh;overflow:auto;display:flex;flex-direction:column;gap:var(--space-base)}',
    '.mx-form{display:flex;flex-direction:column;gap:var(--space-snug)}',
    '.mx-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));gap:var(--space-snug)}',
    '.mx-field{display:flex;flex-direction:column;gap:var(--space-tight)}',
    '.mx-field label{font-size:var(--text-size-meta);color:var(--text-secondary)}',
    '.mx-input{font:inherit;font-size:var(--text-size-body);min-block-size:2.5rem;inline-size:100%;box-sizing:border-box;padding-inline:var(--space-snug);border-radius:var(--radius-control);border:1px solid var(--border-default);background:var(--surface-sunken);color:var(--text-primary)}',
    '.mx-check{display:flex;align-items:center;gap:var(--space-snug);font-size:var(--text-size-meta)}',
    '.mx-err{margin:0;color:var(--state-danger-text);font-size:var(--text-size-meta);min-block-size:1em}',
    '.mx-sec{display:flex;flex-direction:column;gap:var(--space-snug);border-block-start:1px solid var(--border-hairline);padding-block-start:var(--space-base)}',
    '.mx-line{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:var(--space-snug);padding-block:var(--space-tight)}',
    '.mx-line.off{opacity:.6}',
    '.mx-price{font-variant-numeric:tabular-nums;color:var(--text-secondary)}',
    '.mx-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column}'
  ].join('');
  function injectStyle() {
    if (typeof document === 'undefined' || !document.createElement) return;
    if (document.getElementById && document.getElementById('mx-style')) return;
    var s = document.createElement('style');
    s.id = 'mx-style';
    s.textContent = CSS;
    (document.head || document.body).appendChild(s);
  }

  // ── data ───────────────────────────────────────────────────────────────────
  async function loadGroups() {
    var r = await RS()._get(API + '/modifier-groups');
    state.groups = (r && r.status === 'success' && Array.isArray(r.data)) ? r.data : [];
    return r;
  }
  async function ensureProducts() {
    if (state.products) return;
    try {
      var r = await RS()._get(API + '/products');
      var d = r && r.data;
      state.products = Array.isArray(d) ? d : ((d && d.products) || []);
    } catch (e) { state.products = []; }
  }
  // One place for "did the server accept it": returns null on success, else a
  // sentence the person can read.
  async function send(call) {
    try {
      var r = await call();
      if (r && r.status === 'success') return { ok: true, data: r.data };
      return { ok: false, message: (r && r.message) ? t(r.message) : t('The server did not accept that.') };
    } catch (e) {
      return { ok: false, message: t('Could not reach the server. Try again.') };
    }
  }

  // The operations, one per route. Each returns {ok, message, data}.
  function createGroup(v) { return send(function () { return RS()._post(API + '/modifier-groups', v); }); }
  function updateGroup(id, v) { return send(function () { return RS()._patch(API + '/modifier-groups/' + id, v); }); }
  function setGroupActive(id, active) {
    if (active) return send(function () { return RS()._patch(API + '/modifier-groups/' + id, { status: 'active' }); });
    return send(function () { return RS()._del(API + '/modifier-groups/' + id); });
  }
  function createOption(gid, v) { return send(function () { return RS()._post(API + '/modifier-groups/' + gid + '/options', v); }); }
  function updateOption(id, v) { return send(function () { return RS()._patch(API + '/modifier-options/' + id, v); }); }
  function setOptionActive(id, active) {
    if (active) return send(function () { return RS()._patch(API + '/modifier-options/' + id, { status: 'active' }); });
    return send(function () { return RS()._del(API + '/modifier-options/' + id); });
  }
  function attachGroup(pid, gid) {
    return send(function () { return RS()._post(API + '/products/' + encodeURIComponent(pid) + '/modifier-groups', { group_id: gid }); });
  }
  function detachGroup(pid, gid) {
    return send(function () { return RS()._del(API + '/products/' + encodeURIComponent(pid) + '/modifier-groups/' + encodeURIComponent(gid)); });
  }

  // ── the list ───────────────────────────────────────────────────────────────
  function groupCard(g) {
    var active = g.status !== 'inactive';
    var required = (+g.min_select || 0) >= 1;
    var n = liveOptionCount(g);
    var upTo = (g.max_select == null) ? t('Choose any number') : fill(t('Choose up to {n}'), { n: g.max_select });
    return '<div class="mx-card' + (active ? '' : ' off') + '" data-mx-group="' + esc(g.id) + '">' +
      '<div><div class="mx-name"><bdi data-no-i18n>' + esc(g.name) + '</bdi></div>' +
      '<div class="mx-badges">' +
      '<span class="mx-badge' + (required ? ' req' : '') + '">' + esc(required ? t('Required') : t('Optional')) + '</span>' +
      '<span class="mx-muted">' + esc(upTo) + '</span>' +
      '<span class="mx-muted">' + esc(fill(n === 1 ? t('{n} option') : t('{n} options'), { n: n })) + '</span>' +
      '<span class="mx-badge' + (active ? ' on' : '') + '">' + esc(active ? t('Active') : t('Inactive')) + '</span>' +
      '</div></div>' +
      '<div class="mx-acts">' +
      '<button type="button" class="mx-btn" data-mx="edit" data-id="' + esc(g.id) + '">' + esc(t('Edit')) + '</button>' +
      '<button type="button" class="mx-btn' + (active ? ' danger' : '') + '" data-mx="toggle" data-id="' + esc(g.id) + '" data-active="' + (active ? '1' : '0') + '">' +
      esc(active ? t('Deactivate') : t('Reactivate')) + '</button>' +
      '</div></div>';
  }
  function listHtml() {
    return '<div class="mx-root">' +
      '<p class="mx-note">' + esc(t('Extras are set up on each computer separately for now.')) + '</p>' +
      '<div class="mx-head"><div><h3 class="mx-h">' + esc(t('Extras')) + '</h3>' +
      '<p class="mx-muted">' + esc(t('Choices a customer can add to an item, like a size or a topping.')) + '</p></div>' +
      '<button type="button" class="mx-btn" data-mx="add">' + esc(t('Add group')) + '</button></div>' +
      (state.groups.length
        ? state.groups.map(groupCard).join('')
        : '<p class="mx-muted">' + esc(t('No extras yet. Add a group, like "Size", then add its options.')) + '</p>') +
      '</div>';
  }
  function paint() {
    if (state.host) state.host.innerHTML = listHtml();
  }
  async function refresh() {
    await loadGroups();
    paint();
    paintDialog();
  }

  async function onHostClick(ev) {
    var el = ev.target && ev.target.closest ? ev.target.closest('[data-mx]') : null;
    if (!el) return;
    var kind = el.dataset.mx;
    if (kind === 'add') return openGroupDialog(null);
    if (kind === 'edit') return openGroupDialog(el.dataset.id);
    if (kind === 'toggle') {
      var r = await setGroupActive(el.dataset.id, el.dataset.active !== '1');
      if (!r.ok) toast(r.message, 'error');
      await refresh();
    }
  }

  async function render(host) {
    injectStyle();
    state.host = host;
    if (!host._mxWired) {
      host._mxWired = true;
      host.addEventListener('click', onHostClick);
    }
    host.innerHTML = '<p class="mx-muted">' + esc(t('Loading…')) + '</p>';
    try { await loadGroups(); } catch (e) { state.groups = []; }
    paint();
  }

  // ── the dialog ─────────────────────────────────────────────────────────────
  function optionRow(o) {
    var active = o.status !== 'inactive';
    return '<li class="mx-line' + (active ? '' : ' off') + '">' +
      '<span><bdi data-no-i18n>' + esc(o.name) + '</bdi> <span class="mx-price" dir="ltr">' + esc(formatDelta(o.price_delta)) + '</span>' +
      (o.is_default ? ' <span class="mx-badge on">' + esc(t('Default')) + '</span>' : '') +
      (active ? '' : ' <span class="mx-badge">' + esc(t('Inactive')) + '</span>') + '</span>' +
      '<span class="mx-acts">' +
      '<button type="button" class="mx-btn" data-mx="opt-edit" data-oid="' + esc(o.id) + '">' + esc(t('Edit')) + '</button>' +
      '<button type="button" class="mx-btn' + (active ? ' danger' : '') + '" data-mx="opt-toggle" data-oid="' + esc(o.id) + '" data-active="' + (active ? '1' : '0') + '">' +
      esc(active ? t('Deactivate') : t('Reactivate')) + '</button></span></li>';
  }
  function optionsSection(g) {
    var editing = null;
    var opts = (g.options || []).slice().sort(function (a, b) { return (a.sort_order || 0) - (b.sort_order || 0); });
    opts.forEach(function (o) { if (o.id === state.editOpt) editing = o; });
    var v = editing || { name: '', price_delta: '', sort_order: '', is_default: 0 };
    return '<section class="mx-sec"><h4 class="mx-h">' + esc(t('Options')) + '</h4>' +
      (opts.length ? '<ul class="mx-list">' + opts.map(optionRow).join('') + '</ul>' : '<p class="mx-muted">' + esc(t('No options yet.')) + '</p>') +
      '<form class="mx-form" data-mx-form="option" novalidate>' +
      '<div class="mx-fields">' +
      '<div class="mx-field"><label for="mx-o-name">' + esc(t('Option name')) + '</label><input class="mx-input" type="text" id="mx-o-name" autocomplete="off" value="' + esc(editing ? v.name : '') + '"></div>' +
      '<div class="mx-field"><label for="mx-o-price">' + esc(t('Price change')) + '</label><input class="mx-input" type="text" inputmode="decimal" dir="ltr" id="mx-o-price" autocomplete="off" value="' + esc(editing ? v.price_delta : '') + '"></div>' +
      '<div class="mx-field"><label for="mx-o-order">' + esc(t('Order')) + '</label><input class="mx-input" type="text" inputmode="numeric" dir="ltr" id="mx-o-order" autocomplete="off" value="' + esc(editing ? v.sort_order : '') + '"></div>' +
      '</div>' +
      '<div class="mx-check"><input type="checkbox" id="mx-o-default"' + (editing && v.is_default ? ' checked' : '') + '><label for="mx-o-default">' + esc(t('Pre-selected at the till')) + '</label></div>' +
      '<p class="mx-err" id="mx-o-err" role="alert"></p>' +
      '<div class="mx-acts"><button type="submit" class="mx-btn">' + esc(editing ? t('Save option') : t('Add option')) + '</button>' +
      (editing ? '<button type="button" class="mx-btn" data-mx="opt-cancel">' + esc(t('Cancel')) + '</button>' : '') + '</div>' +
      '</form></section>';
  }
  function itemsSection(g) {
    var ids = attachedIds(g);
    var local = Object.keys(state.localAttached[g.id] || {});
    var shown = ids || local;
    var rows = shown.map(function (pid) {
      return '<li class="mx-line"><span><bdi data-no-i18n>' + esc(productName(pid)) + '</bdi></span>' +
        '<button type="button" class="mx-btn danger" data-mx="detach" data-pid="' + esc(pid) + '">' + esc(t('Remove')) + '</button></li>';
    }).join('');
    var unknown = (ids === null && !local.length);
    return '<section class="mx-sec"><h4 class="mx-h">' + esc(t('Used on these items')) + '</h4>' +
      (unknown ? '<p class="mx-muted" data-mx-attached-unknown>—</p>' : '') +
      (rows ? '<ul class="mx-list">' + rows + '</ul>' : (unknown ? '' : '<p class="mx-muted">' + esc(t('Not used on any item yet.')) + '</p>')) +
      '<div class="mx-field"><label for="mx-p-search">' + esc(t('Find an item to add')) + '</label>' +
      '<input class="mx-input" type="search" id="mx-p-search" autocomplete="off"></div>' +
      '<ul class="mx-list" id="mx-p-results"></ul></section>';
  }
  function dialogHtml() {
    var g = state.gid ? findGroup(state.gid) : null;
    var isNew = !g;
    return '<div class="ret-modal mx-dialog" id="mx-group-dialog" role="dialog" aria-modal="true" aria-labelledby="mx-group-title" tabindex="-1">' +
      '<h3 class="mx-h" id="mx-group-title">' + esc(isNew ? t('Add an extras group') : t('Edit extras group')) + '</h3>' +
      '<form class="mx-form" data-mx-form="group" novalidate>' +
      '<div class="mx-field"><label for="mx-g-name">' + esc(t('Group name')) + '</label><input class="mx-input" type="text" id="mx-g-name" autocomplete="off" value="' + esc(g ? g.name : '') + '"></div>' +
      '<div class="mx-fields">' +
      '<div class="mx-field"><label for="mx-g-min">' + esc(t('At least (0 = optional)')) + '</label><input class="mx-input" type="text" inputmode="numeric" dir="ltr" id="mx-g-min" autocomplete="off" value="' + esc(g ? (g.min_select || 0) : '0') + '"></div>' +
      '<div class="mx-field"><label for="mx-g-max">' + esc(t('Up to (empty = any)')) + '</label><input class="mx-input" type="text" inputmode="numeric" dir="ltr" id="mx-g-max" autocomplete="off" value="' + esc(g && g.max_select != null ? g.max_select : '') + '"></div>' +
      '</div>' +
      '<p class="mx-err" id="mx-g-err" role="alert"></p>' +
      '<div class="mx-acts"><button type="submit" class="mx-btn">' + esc(isNew ? t('Create group') : t('Save group')) + '</button></div>' +
      '</form>' +
      (isNew ? '<p class="mx-muted">' + esc(t('Save the group first, then add its options and the items it is used on.')) + '</p>'
             : optionsSection(g) + itemsSection(g)) +
      '<div class="ret-modal-footer"><button type="button" class="ret-btn ret-btn-ghost" onclick="RetailSystem._closeModalOverlay(this)">' + esc(t('Close')) + '</button></div>' +
      '</div>';
  }
  // Re-render the dialog's CONTENTS but keep the dialog element itself.
  // _wireModalA11y captured that element for its Tab trap; replacing it (an
  // overlay.innerHTML rewrite) left the trap pointing at a detached node, so
  // Tab walked out of the dialog after the first save. Found by pressing Tab
  // 40 times in a real browser, not by any stub.
  function paintDialog() {
    var ov = state.overlay;
    if (!ov || ov.removed) return;
    var html = dialogHtml();
    var dlg = ov.querySelector ? ov.querySelector('#mx-group-dialog') : null;
    if (dlg && typeof dlg.replaceChildren === 'function' && typeof document !== 'undefined') {
      var tmp = document.createElement('div');
      tmp.innerHTML = html;
      var fresh = tmp.firstElementChild;
      if (fresh) { dlg.replaceChildren.apply(dlg, Array.prototype.slice.call(fresh.childNodes)); }
      else { ov.innerHTML = html; }
    } else {
      ov.innerHTML = html;
    }
    // The element that had focus was just replaced; put focus where the
    // person's next keystroke belongs, or Enter-then-type would go nowhere.
    var f = state.focusId && ov.querySelector('#' + state.focusId);
    if (f && typeof f.focus === 'function') f.focus();
    state.focusId = null;
  }
  function setErr(id, msg) {
    var e = state.overlay && state.overlay.querySelector('#' + id);
    if (e) e.textContent = msg || '';
  }
  function val(id) {
    var e = state.overlay.querySelector('#' + id);
    return e ? e.value : '';
  }

  async function submitGroup() {
    var raw = { name: val('mx-g-name'), min: val('mx-g-min'), max: val('mx-g-max') };
    var v = validateGroup(raw);
    if (v.error) { setErr('mx-g-err', v.error); return; }
    setErr('mx-g-err', '');
    var r = state.gid ? await updateGroup(state.gid, v.value) : await createGroup(v.value);
    if (!r.ok) { setErr('mx-g-err', r.message); return; }
    state.focusId = state.gid ? 'mx-g-name' : 'mx-o-name';
    if (!state.gid && r.data && r.data.id) state.gid = r.data.id;
    toast(t('Saved.'), 'success');
    await refresh();
  }
  async function submitOption() {
    var g = state.gid ? findGroup(state.gid) : null;
    if (!g) return;
    var raw = { name: val('mx-o-name'), price: val('mx-o-price'), order: val('mx-o-order'),
                isDefault: !!state.overlay.querySelector('#mx-o-default').checked };
    var v = validateOption(raw);
    if (v.error) { setErr('mx-o-err', v.error); return; }
    setErr('mx-o-err', '');
    var r = state.editOpt ? await updateOption(state.editOpt, v.value) : await createOption(g.id, v.value);
    if (!r.ok) { setErr('mx-o-err', r.message); return; }
    state.editOpt = null;
    state.focusId = 'mx-o-name';
    await refresh();
  }
  async function onDialogClick(ev) {
    var el = ev.target && ev.target.closest ? ev.target.closest('[data-mx]') : null;
    if (!el) return;
    var kind = el.dataset.mx;
    var gid = state.gid;
    if (kind === 'opt-edit') { state.editOpt = el.dataset.oid; state.focusId = 'mx-o-name'; paintDialog(); return; }
    if (kind === 'opt-cancel') { state.editOpt = null; state.focusId = 'mx-o-name'; paintDialog(); return; }
    if (kind === 'opt-toggle') {
      var r = await setOptionActive(el.dataset.oid, el.dataset.active !== '1');
      if (!r.ok) setErr('mx-o-err', r.message);
      await refresh();
      return;
    }
    if (kind === 'attach') {
      var pid = el.dataset.pid;
      var a = await attachGroup(pid, gid);
      if (!a.ok) { setErr('mx-g-err', a.message); return; }
      state.focusId = 'mx-p-search';
      (state.localAttached[gid] = state.localAttached[gid] || {})[pid] = true;
      await refresh();
      return;
    }
    if (kind === 'detach') {
      var pd = el.dataset.pid;
      var d = await detachGroup(pd, gid);
      if (!d.ok) { setErr('mx-g-err', d.message); return; }
      state.focusId = 'mx-p-search';
      if (state.localAttached[gid]) delete state.localAttached[gid][pd];
      await refresh();
    }
  }
  function onDialogSubmit(ev) {
    var f = ev.target && ev.target.closest ? ev.target.closest('form[data-mx-form]') : null;
    if (!f) return;
    ev.preventDefault();
    return f.dataset.mxForm === 'option' ? submitOption() : submitGroup();
  }
  function onDialogInput(ev) {
    if (!ev.target || ev.target.id !== 'mx-p-search') return;
    var q = String(ev.target.value || '').trim().toLowerCase();
    var out = state.overlay.querySelector('#mx-p-results');
    if (!out) return;
    var g = state.gid ? findGroup(state.gid) : null;
    if (!q || !g) { out.innerHTML = ''; return; }
    var have = attachedIds(g) || Object.keys(state.localAttached[g.id] || {});
    var hits = (state.products || []).filter(function (p) {
      if (have.indexOf(p.id) !== -1) return false;
      return [p.name, p.sku, p.barcode].some(function (f) { return f && String(f).toLowerCase().indexOf(q) !== -1; });
    }).slice(0, 8);
    out.innerHTML = hits.length ? hits.map(function (p) {
      return '<li class="mx-line"><span><bdi data-no-i18n>' + esc(p.name) + '</bdi></span>' +
        '<button type="button" class="mx-btn" data-mx="attach" data-pid="' + esc(p.id) + '">' + esc(t('Add')) + '</button></li>';
    }).join('') : '<li class="mx-muted">' + esc(t('No item matches.')) + '</li>';
  }

  async function openGroupDialog(gid) {
    if (typeof document === 'undefined' || !RS() || typeof RS()._wireModalA11y !== 'function') return;
    if (document.getElementById('mx-group-modal')) return;
    state.gid = gid || null;
    state.editOpt = null;
    await ensureProducts();
    var overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'mx-group-modal';
    state.overlay = overlay;
    overlay.innerHTML = dialogHtml();
    document.body.appendChild(overlay);
    RS()._wireModalA11y(overlay, 'mx-group-dialog');
    overlay.addEventListener('click', onDialogClick);
    overlay.addEventListener('submit', onDialogSubmit);
    overlay.addEventListener('input', onDialogInput);
  }

  window.RetailModifiers = {
    render: render,
    openGroupDialog: openGroupDialog,
    _core: {
      formatDelta: formatDelta,
      validateGroup: validateGroup,
      validateOption: validateOption
    }
  };
})();
