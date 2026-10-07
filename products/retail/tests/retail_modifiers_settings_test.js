/**
 * retail_modifiers_settings_test.js -- the "Extras" set-up screen (EXTRAS).
 *
 * WHY THIS EXISTS
 *
 * Modifier groups ("Size", "Toppings") shipped as a complete backend and a till
 * picker, with NO screen to create one: the only way to set a shop up was to
 * call the API by hand. This screen (frontend/modifiers-settings.js, shown as
 * the "Extras" section of Settings) is the doorway. This file proves the screen
 * is real: it is hidden from people who may not use it, its validation matches
 * the server's own (a bad group must be refused on the screen, not discovered
 * as a 400), every action hits the URL and verb the route expects, and the
 * source keeps the keyboard/label/dialog promises the other suites hold the
 * rest of the product to.
 *
 * The module is the REAL shipped file, read from disk and run against stubs --
 * no reimplementation. Each claim is also proved by an in-memory MUTANT: the
 * shipped text with one thing broken, which must turn the claim red. A claim
 * that stays green against its own mutant is measuring nothing.
 *
 *   node products/retail/tests/retail_modifiers_settings_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const MOD_FILE = path.join(FRONTEND, 'modifiers-settings.js');
const HUB_FILE = path.join(FRONTEND, 'settings-hub.js');
const INDEX_FILE = path.join(FRONTEND, 'index.html');
const API = '/api/sub/retail';

// ── stubs ───────────────────────────────────────────────────────────────────
function mkEl(id) {
  const el = {
    id: id || '', className: '', dataset: {}, attrs: {}, listeners: {}, _q: {}, children: [],
    _html: '', removed: false,
    addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
    appendChild(c) { this.children.push(c); return c; },
    remove() { this.removed = true; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
    focus() {},
    querySelectorAll() { return []; },
    querySelector(sel) {
      if (!this._q[sel]) this._q[sel] = { value: '', checked: false, textContent: '', innerHTML: '', hidden: false, focus() {}, setAttribute() {} };
      return this._q[sel];
    },
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return el._html; },
    set(v) { el._html = String(v); el._q = {}; },
  });
  return el;
}

function loadModule(src, opts) {
  opts = opts || {};
  const calls = [];
  const wired = [];
  const closes = [];
  const body = mkEl('body');
  const byId = {};
  const groups = opts.groups || [];
  const products = opts.products || [
    { id: 'p1', name: 'Burger', sku: 'B1', barcode: '111' },
    { id: 'p2', name: 'Fries', sku: 'F1', barcode: '222' },
  ];
  const RetailSystem = {
    _esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); },
    _currencyDp() { return opts.dp == null ? 3 : opts.dp; },
    _wireModalA11y(overlay, dialogId) { wired.push({ overlay, dialogId }); const c = () => closes.push(dialogId); return c; },
    _closeModalOverlay() {},
    async _get(url) {
      calls.push(plain(['GET', url]));
      if (url === API + '/modifier-groups') return { status: 'success', data: groups };
      if (url.indexOf(API + '/products') === 0) return { status: 'success', data: products };
      return { status: 'success', data: [] };
    },
    async _post(url, body) {
      calls.push(plain(['POST', url, body === undefined ? null : body]));
      if (opts.postFails) return { status: 'error', message: opts.postFails };
      if (url === API + '/modifier-groups') {
        const g = { id: 'gNew', name: body.name, min_select: body.min_select, max_select: body.max_select, status: 'active', options: [] };
        groups.push(g);
        return { status: 'success', data: { id: 'gNew' } };
      }
      return { status: 'success', data: { id: 'x' } };
    },
    async _patch(url, body) { calls.push(plain(['PATCH', url, body])); return { status: 'success' }; },
    async _del(url) { calls.push(['DELETE', url]); return { status: 'success' }; },
  };
  const document = {
    body, head: mkEl('head'),
    createElement(tag) { const e = mkEl(); e.tag = tag; return e; },
    getElementById(id) { return byId[id] || null; },
  };
  body.appendChild = function (c) { this.children.push(c); if (c.id) byId[c.id] = c; return c; };
  const toasts = [];
  const sandbox = {
    window: null, document, console,
    SubsystemApp: { showToast(m, k) { toasts.push([m, k]); } },
  };
  sandbox.window = sandbox;
  sandbox.RetailSystem = RetailSystem;
  sandbox.t = (s) => s;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'modifiers-settings.js' });
  return { win: sandbox, calls, wired, closes, body, toasts, groups, RetailSystem };
}

// Objects built inside the vm context have another realm's prototypes, which
// deepStrictEqual rightly refuses to equate with ours: compare as plain JSON.
const plain = (x) => JSON.parse(JSON.stringify(x));
const realSrc = () => fs.readFileSync(MOD_FILE, 'utf8');

function fire(el, type, ev) {
  const hs = el.listeners[type] || [];
  assert.ok(hs.length, 'no ' + type + ' listener was registered');
  return Promise.all(hs.map((h) => h(ev)));
}
function clickTarget(dataset) {
  const tgt = { dataset, closest(sel) { return sel.indexOf('[data-mx') === 0 ? tgt : null; } };
  return { target: tgt, preventDefault() {}, stopPropagation() {} };
}
function submitEvent(kind) {
  const tgt = { dataset: { mxForm: kind }, closest(sel) { return sel.indexOf('form[data-mx-form') === 0 ? tgt : null; } };
  return { target: tgt, preventDefault() {}, stopPropagation() {} };
}

const GROUPS = [
  { id: 'g1', name: 'Size', min_select: 1, max_select: 1, status: 'active', product_ids: ['p1'],
    options: [
      { id: 'o1', name: 'Small', price_delta: 0, is_default: 1, sort_order: 0, status: 'active' },
      { id: 'o2', name: 'Large', price_delta: 0.5, is_default: 0, sort_order: 1, status: 'active' },
      { id: 'o3', name: 'Huge', price_delta: 1, is_default: 0, sort_order: 2, status: 'inactive' },
    ] },
  { id: 'g2', name: 'Toppings <b>', min_select: 0, max_select: null, status: 'inactive', options: [] },
];

let passed = 0;
async function claim(name, fn) {
  try { await fn(); passed += 1; console.log('PASS: ' + name); }
  catch (e) { console.error('FAIL: ' + name + '\n  ' + (e && e.message)); process.exitCode = 1; throw e; }
}
// A claim that must FAIL against a mutant of the shipped text.
async function mutant(name, mutate, fn) {
  const src = realSrc();
  const mutated = mutate(src);
  assert.notStrictEqual(mutated, src, 'mutant "' + name + '" did not change the source (anchor drifted)');
  let failed = false;
  try { await fn(mutated); } catch (e) { failed = true; }
  assert.ok(failed, 'MUTANT SURVIVED: ' + name + ' -- the claim above does not guard what it says');
  passed += 1;
  console.log('PASS (mutant killed): ' + name);
}

async function main() {
  await claim('the module file exists and registers window.RetailModifiers', async () => {
    const m = loadModule(realSrc());
    assert.ok(m.win.RetailModifiers && typeof m.win.RetailModifiers.render === 'function');
  });

  // ── the settings hub door ────────────────────────────────────────────────
  function loadHub(hasCap, role, hubSrc) {
    const sandbox = {
      document: { getElementById() { return null; }, addEventListener() {} },
      console, setTimeout, clearTimeout,
      SubsystemApp: { role: role || 'admin', hasCapability: (c) => hasCap(c), showToast() {} },
      RetailSystem: { _esc: (s) => String(s) },
      t: (s) => s,
    };
    sandbox.window = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(hubSrc || fs.readFileSync(HUB_FILE, 'utf8'), sandbox, { filename: 'settings-hub.js' });
    return sandbox;
  }
  await claim('hub: the Extras section is hidden without retail.stock.adjust, shown with it', async () => {
    const without = loadHub((c) => c !== 'retail.stock.adjust').SettingsHub.sections();
    const withCap = loadHub(() => true).SettingsHub.sections();
    const a = without.find((s) => s.id === 'extras');
    const b = withCap.find((s) => s.id === 'extras');
    assert.ok(a && b, 'extras must be a known section id');
    assert.strictEqual(a.visible, false);
    assert.strictEqual(b.visible, true);
    assert.ok(/Extras/.test(b.label));
  });
  await (async () => {
    // Mutant: the door is shown to everyone. The claim above must notice.
    const hub = fs.readFileSync(HUB_FILE, 'utf8');
    const mut = hub.replace("visible: canStockAdjust() }", 'visible: true }');
    assert.notStrictEqual(mut, hub, 'hub mutant anchor drifted');
    const sec = loadHub((c) => c !== 'retail.stock.adjust', 'staff', mut).SettingsHub.sections().find((s) => s.id === 'extras');
    assert.strictEqual(sec.visible, true, 'the mutant really opens the door to a user without the capability');
    passed += 1; console.log('PASS (mutant killed): hub door shown to everyone is detected');
  })();
  await claim('hub: buildPane hands the extras pane to RetailModifiers.render', async () => {
    const hub = fs.readFileSync(HUB_FILE, 'utf8');
    assert.ok(/id === 'extras'[\s\S]{0,200}RetailModifiers\.render\(host\)/.test(hub));
  });
  await claim('index.html loads modifiers-settings.js', async () => {
    assert.ok(/<script src="\/static\/modifiers-settings\.js"><\/script>/.test(fs.readFileSync(INDEX_FILE, 'utf8')));
  });

  // ── pure helpers ─────────────────────────────────────────────────────────
  await claim('price change is signed and uses the shop currency decimals', async () => {
    const f3 = loadModule(realSrc(), { dp: 3 }).win.RetailModifiers._core.formatDelta;
    assert.strictEqual(f3(0), '+0.000');
    assert.strictEqual(f3(0.5), '+0.500');
    assert.strictEqual(f3(-0.25), '-0.250');
    const f2 = loadModule(realSrc(), { dp: 2 }).win.RetailModifiers._core.formatDelta;
    assert.strictEqual(f2(0.5), '+0.50');
  });
  await mutant('price change drops the sign', (s) => s.replace("(v < 0 ? '-' : '+')", "(v < 0 ? '-' : '')"), async (src) => {
    assert.strictEqual(loadModule(src).win.RetailModifiers._core.formatDelta(0.5), '+0.500');
  });

  await claim('group validation mirrors the server rules', async () => {
    const v = loadModule(realSrc()).win.RetailModifiers._core.validateGroup;
    assert.ok(v({ name: '  ', min: '', max: '' }).error, 'name required');
    assert.ok(v({ name: 'A', min: '-1', max: '' }).error, 'min >= 0');
    assert.ok(v({ name: 'A', min: '1.5', max: '' }).error, 'min must be whole');
    assert.ok(v({ name: 'A', min: '0', max: '0' }).error, 'max >= 1');
    assert.ok(v({ name: 'A', min: '3', max: '2' }).error, 'max >= min');
    assert.deepStrictEqual(plain(v({ name: ' Size ', min: '1', max: '1' }).value), { name: 'Size', min_select: 1, max_select: 1 });
    assert.deepStrictEqual(plain(v({ name: 'T', min: '', max: '' }).value), { name: 'T', min_select: 0, max_select: null }, 'blank max = unlimited, blank min = 0');
  });
  await mutant('group validation forgets max >= min', (s) => s.replace('max < min', 'false'), async (src) => {
    const v = loadModule(src).win.RetailModifiers._core.validateGroup;
    assert.ok(v({ name: 'A', min: '3', max: '2' }).error);
  });

  await claim('option validation: name required, price a number (blank = 0), order whole', async () => {
    const v = loadModule(realSrc()).win.RetailModifiers._core.validateOption;
    assert.ok(v({ name: '', price: '1', order: '' }).error);
    assert.ok(v({ name: 'X', price: 'abc', order: '' }).error);
    assert.ok(v({ name: 'X', price: '1', order: '1.5' }).error);
    assert.deepStrictEqual(plain(v({ name: 'Large', price: '0.5', order: '2', isDefault: true }).value),
      { name: 'Large', price_delta: 0.5, sort_order: 2, is_default: true });
    assert.strictEqual(v({ name: 'Small', price: '', order: '' }).value.price_delta, 0);
    assert.strictEqual(v({ name: 'Less', price: '-0.25', order: '' }).value.price_delta, -0.25);
  });

  // ── the list ─────────────────────────────────────────────────────────────
  async function renderList(src, groups) {
    const m = loadModule(src, { groups: JSON.parse(JSON.stringify(groups)) });
    const host = mkEl('host');
    await m.win.RetailModifiers.render(host);
    return { m, host };
  }
  await claim('list: required/optional, "up to N"/"any", live option count, status, escaped names', async () => {
    const { host } = await renderList(realSrc(), GROUPS);
    const h = host.innerHTML;
    assert.ok(/Required/.test(h) && /Optional/.test(h));
    assert.ok(/up to 1/.test(h), 'max_select 1 => "up to 1"');
    assert.ok(/any/.test(h), 'null max => any');
    assert.ok(/2 options/.test(h), 'only the 2 ACTIVE options are counted (Huge is deactivated)');
    assert.ok(/Toppings &lt;b&gt;/.test(h) && !/Toppings <b>/.test(h), 'group names are escaped');
    assert.ok(/data-mx="toggle"[^>]*data-id="g1"[^>]*data-active="1"/.test(h) || /data-mx="toggle"[^>]*data-active="1"[^>]*data-id="g1"/.test(h));
    assert.ok(/Reactivate/.test(h) && /Deactivate/.test(h));
    assert.ok(/data-mx="add"/.test(h) && /data-mx="edit"/.test(h));
    assert.ok(/Extras are set up on each computer separately for now\./.test(h));
  });
  await mutant('list counts deactivated options too', (s) => s.replace("o.status !== 'inactive'", 'true'), async (src) => {
    const { host } = await renderList(src, GROUPS);
    assert.ok(/2 options/.test(host.innerHTML));
  });

  await claim('list actions hit the right verb and URL', async () => {
    const { m, host } = await renderList(realSrc(), GROUPS);
    m.calls.length = 0;
    await fire(host, 'click', clickTarget({ mx: 'toggle', id: 'g1', active: '1' }));
    assert.deepStrictEqual(m.calls.filter((c) => c[0] !== 'GET'), [['DELETE', API + '/modifier-groups/g1']]);
    m.calls.length = 0;
    await fire(host, 'click', clickTarget({ mx: 'toggle', id: 'g2', active: '0' }));
    assert.deepStrictEqual(m.calls.filter((c) => c[0] !== 'GET'), [['PATCH', API + '/modifier-groups/g2', { status: 'active' }]]);
  });
  await mutant('deactivate sends PATCH instead of DELETE', (s) => s.replace("_del(API + '/modifier-groups/'", "_patch(API + '/modifier-groups/'"), async (src) => {
    const { m, host } = await renderList(src, GROUPS);
    m.calls.length = 0;
    await fire(host, 'click', clickTarget({ mx: 'toggle', id: 'g1', active: '1' }));
    assert.deepStrictEqual(m.calls.filter((c) => c[0] !== 'GET'), [['DELETE', API + '/modifier-groups/g1']]);
  });

  // ── the group dialog ─────────────────────────────────────────────────────
  async function openEdit(src, groups, opts) {
    const { m, host } = await renderList(src, groups);
    await fire(host, 'click', clickTarget({ mx: 'edit', id: 'g1' }));
    const overlay = m.body.children[m.body.children.length - 1];
    return { m, host, overlay };
  }
  await claim('dialog: opens as a wired, named dialog; add-group creates then switches to edit', async () => {
    const { m, host } = await renderList(realSrc(), []);
    await fire(host, 'click', clickTarget({ mx: 'add' }));
    const overlay = m.body.children[m.body.children.length - 1];
    assert.ok(/ret-modal-overlay/.test(overlay.className));
    assert.strictEqual(m.wired.length, 1);
    assert.strictEqual(m.wired[0].dialogId, 'mx-group-dialog');
    assert.ok(/role="dialog"/.test(overlay.innerHTML) && /aria-modal="true"/.test(overlay.innerHTML));
    // New group: options/attach are not offered until it exists.
    assert.ok(/Save the group first/.test(overlay.innerHTML));
    overlay.querySelector('#mx-g-name').value = 'Size';
    overlay.querySelector('#mx-g-min').value = '1';
    overlay.querySelector('#mx-g-max').value = '1';
    m.calls.length = 0;
    await fire(overlay, 'submit', submitEvent('group'));
    assert.deepStrictEqual(m.calls.filter((c) => c[0] === 'POST'),
      [['POST', API + '/modifier-groups', { name: 'Size', min_select: 1, max_select: 1 }]]);
    assert.ok(/id="mx-o-name"/.test(overlay.innerHTML), 'after saving, the same dialog shows the options form');
    assert.strictEqual(m.wired.length, 1, 'the dialog is re-rendered, not re-opened (one close path)');
  });
  await claim('dialog: an invalid group is refused on the screen, nothing is sent', async () => {
    const { m, host } = await renderList(realSrc(), []);
    await fire(host, 'click', clickTarget({ mx: 'add' }));
    const overlay = m.body.children[m.body.children.length - 1];
    overlay.querySelector('#mx-g-name').value = '';
    m.calls.length = 0;
    await fire(overlay, 'submit', submitEvent('group'));
    assert.deepStrictEqual(m.calls.filter((c) => c[0] !== 'GET'), []);
    assert.ok(/name/i.test(overlay.querySelector('#mx-g-err').textContent), 'the reason is shown');
  });
  await claim('dialog: a server refusal is shown, not swallowed', async () => {
    const m = loadModule(realSrc(), { groups: [], postFails: 'Modifier group name required.' });
    const host = mkEl('host');
    await m.win.RetailModifiers.render(host);
    await fire(host, 'click', clickTarget({ mx: 'add' }));
    const overlay = m.body.children[m.body.children.length - 1];
    overlay.querySelector('#mx-g-name').value = 'Size';
    await fire(overlay, 'submit', submitEvent('group'));
    assert.ok(/Modifier group name required\./.test(overlay.querySelector('#mx-g-err').textContent));
  });
  await mutant('invalid group is sent anyway', (s) => s.replace(/var v = validateGroup\(([^)]*)\);\s*if \(v\.error\) \{/, 'var v = validateGroup($1); if (false) {'), async (src) => {
    const { m, host } = await renderList(src, []);
    await fire(host, 'click', clickTarget({ mx: 'add' }));
    const overlay = m.body.children[m.body.children.length - 1];
    overlay.querySelector('#mx-g-name').value = '';
    m.calls.length = 0;
    await fire(overlay, 'submit', submitEvent('group'));
    assert.deepStrictEqual(m.calls.filter((c) => c[0] !== 'GET'), []);
  });

  await claim('dialog: edit saves name/min/max with PATCH', async () => {
    const { m, overlay } = await openEdit(realSrc(), GROUPS);
    assert.ok(/Small/.test(overlay.innerHTML) && /\+0\.500/.test(overlay.innerHTML), 'options listed with signed price');
    overlay.querySelector('#mx-g-name').value = 'Sizes';
    overlay.querySelector('#mx-g-min').value = '0';
    overlay.querySelector('#mx-g-max').value = '';
    m.calls.length = 0;
    await fire(overlay, 'submit', submitEvent('group'));
    assert.deepStrictEqual(m.calls.filter((c) => c[0] === 'PATCH'),
      [['PATCH', API + '/modifier-groups/g1', { name: 'Sizes', min_select: 0, max_select: null }]]);
  });

  await claim('options: add / edit / deactivate / reactivate hit the right URLs', async () => {
    const { m, overlay } = await openEdit(realSrc(), GROUPS);
    overlay.querySelector('#mx-o-name').value = 'Medium';
    overlay.querySelector('#mx-o-price').value = '0.25';
    overlay.querySelector('#mx-o-order').value = '3';
    overlay.querySelector('#mx-o-default').checked = true;
    m.calls.length = 0;
    await fire(overlay, 'submit', submitEvent('option'));
    assert.deepStrictEqual(m.calls.filter((c) => c[0] === 'POST'),
      [['POST', API + '/modifier-groups/g1/options', { name: 'Medium', price_delta: 0.25, is_default: true, sort_order: 3 }]]);

    // edit: click Edit on Large, the form is filled, submit PATCHes that option.
    await fire(overlay, 'click', clickTarget({ mx: 'opt-edit', oid: 'o2' }));
    assert.ok(/value="Large"/.test(overlay.innerHTML), 'the option form is pre-filled');
    overlay.querySelector('#mx-o-name').value = 'Large+';
    overlay.querySelector('#mx-o-price').value = '0.75';
    overlay.querySelector('#mx-o-order').value = '1';
    m.calls.length = 0;
    await fire(overlay, 'submit', submitEvent('option'));
    assert.deepStrictEqual(m.calls.filter((c) => c[0] === 'PATCH'),
      [['PATCH', API + '/modifier-options/o2', { name: 'Large+', price_delta: 0.75, is_default: false, sort_order: 1 }]]);

    m.calls.length = 0;
    await fire(overlay, 'click', clickTarget({ mx: 'opt-toggle', oid: 'o1', active: '1' }));
    assert.deepStrictEqual(m.calls.filter((c) => c[0] !== 'GET'), [['DELETE', API + '/modifier-options/o1']]);
    m.calls.length = 0;
    await fire(overlay, 'click', clickTarget({ mx: 'opt-toggle', oid: 'o3', active: '0' }));
    assert.deepStrictEqual(m.calls.filter((c) => c[0] !== 'GET'), [['PATCH', API + '/modifier-options/o3', { status: 'active' }]]);
  });
  await mutant('option edit PATCHes the GROUP url', (s) => s.replace("'/modifier-options/' + id", "'/modifier-groups/' + id"), async (src) => {
    const { m, overlay } = await openEdit(src, GROUPS);
    await fire(overlay, 'click', clickTarget({ mx: 'opt-edit', oid: 'o2' }));
    overlay.querySelector('#mx-o-name').value = 'Large+';
    m.calls.length = 0;
    await fire(overlay, 'submit', submitEvent('option'));
    assert.ok(m.calls.some((c) => c[0] === 'PATCH' && c[1] === API + '/modifier-options/o2'));
  });

  await claim('attach / detach: product search, POST and DELETE on the product routes', async () => {
    const { m, overlay } = await openEdit(realSrc(), GROUPS);
    assert.ok(/Burger/.test(overlay.innerHTML), '"Used on these items" names the live links from product_ids');
    // search
    const search = { id: 'mx-p-search', value: 'fri', closest() { return null; }, dataset: {} };
    await fire(overlay, 'input', { target: search });
    const results = overlay.querySelector('#mx-p-results').innerHTML;
    assert.ok(/Fries/.test(results) && !/Burger/.test(results), 'search filters the product list');
    m.calls.length = 0;
    await fire(overlay, 'click', clickTarget({ mx: 'attach', pid: 'p2' }));
    assert.deepStrictEqual(m.calls.filter((c) => c[0] === 'POST'), [['POST', API + '/products/p2/modifier-groups', { group_id: 'g1' }]]);
    m.calls.length = 0;
    await fire(overlay, 'click', clickTarget({ mx: 'detach', pid: 'p1' }));
    assert.deepStrictEqual(m.calls.filter((c) => c[0] === 'DELETE'), [['DELETE', API + '/products/p1/modifier-groups/g1']]);
  });
  await mutant('attach posts the product id as the group id', (s) => s.replace('{ group_id: gid }', '{ group_id: pid }'), async (src) => {
    const { m, overlay } = await openEdit(src, GROUPS);
    m.calls.length = 0;
    await fire(overlay, 'click', clickTarget({ mx: 'attach', pid: 'p2' }));
    assert.deepStrictEqual(m.calls.filter((c) => c[0] === 'POST'), [['POST', API + '/products/p2/modifier-groups', { group_id: 'g1' }]]);
  });
  await claim('attach list shows a dash (and still allows attach) when the server sends no product_ids', async () => {
    const noIds = JSON.parse(JSON.stringify(GROUPS));
    delete noIds[0].product_ids;
    const { m, overlay } = await openEdit(realSrc(), noIds);
    assert.ok(/data-mx-attached-unknown/.test(overlay.innerHTML) && /—/.test(overlay.innerHTML));
    m.calls.length = 0;
    await fire(overlay, 'click', clickTarget({ mx: 'attach', pid: 'p2' }));
    assert.strictEqual(m.calls.filter((c) => c[0] === 'POST').length, 1);
  });

  // ── source-level promises (keyboard, labels, dialog, i18n, tokens) ───────
  const src = realSrc();
  await claim('keyboard: each form is a real <form> with a submit button (Enter submits); Esc rides the shared helper', async () => {
    assert.ok(/<form[^>]*data-mx-form="group"/.test(src) && /<form[^>]*data-mx-form="option"/.test(src));
    assert.ok(/type="submit"/.test(src));
    assert.ok(/preventDefault\(\)/.test(src), 'the submit handler stops the browser navigating');
    assert.strictEqual((src.match(/\._wireModalA11y\(/g) || []).length, (src.match(/className\s*=\s*'[^']*\bret-modal-overlay\b[^']*'/g) || []).length);
    assert.ok(/_closeModalOverlay\(this\)/.test(src), 'the Close button goes through the helper so focus returns');
    assert.ok(!/keydown/.test(src), 'no private key handling: Escape and the Tab trap belong to the shared helper');
  });
  await mutant('a modal is built without wiring', (s) => s.replace(/\._wireModalA11y\(/, '._notWired('), async (mutated) => {
    assert.strictEqual((mutated.match(/\._wireModalA11y\(/g) || []).length, (mutated.match(/className\s*=\s*'[^']*\bret-modal-overlay\b[^']*'/g) || []).length);
  });
  await claim('labels: every for="x" has a matching id="x" in the file', async () => {
    const fors = (src.match(/\bfor="([^"]+)"/g) || []).map((m) => m.slice(5, -1));
    assert.ok(fors.length >= 7, 'the inputs are labelled (found ' + fors.length + ')');
    for (const f of fors) assert.ok(src.indexOf('id="' + f + '"') !== -1, 'label for="' + f + '" points at nothing');
  });
  await claim('the dialog is named: aria-labelledby resolves to an id in the file', async () => {
    const m = /role="dialog"[^>]*aria-labelledby="([^"]+)"/.exec(src);
    assert.ok(m && src.indexOf('id="' + m[1] + '"') !== -1);
  });
  await claim('shop-typed names never meet the Arabic dictionary sweep: every <bdi> is data-no-i18n', async () => {
    const all = (src.match(/<bdi[ >]/g) || []).length;
    const safe = (src.match(/<bdi data-no-i18n>/g) || []).length;
    assert.ok(all >= 4, 'found ' + all + ' name slots');
    assert.strictEqual(safe, all, 'a user-typed name is exposed to i18n.js (an option called "Large" turned into "كبير")');
  });
  await mutant('a name slot loses data-no-i18n', (s) => s.replace('<bdi data-no-i18n>', '<bdi>'), async (mutated) => {
    assert.strictEqual((mutated.match(/<bdi data-no-i18n>/g) || []).length, (mutated.match(/<bdi[ >]/g) || []).length);
  });
  await claim('no colour literals and no left/right physical offsets in the module', async () => {
    assert.ok(!/#[0-9a-fA-F]{3,8}\b/.test(src.replace(/&#\d+;/g, '')), 'colour literal found');
    assert.ok(!/rgba?\(/.test(src));
    assert.ok(!/margin-left|margin-right|padding-left|padding-right|text-align:\s*(left|right)/.test(src));
  });
  await claim('every t() literal exists in en.json and ar.json, and Arabic really differs', async () => {
    const en = JSON.parse(fs.readFileSync(path.join(FRONTEND, 'locales', 'en.json'), 'utf8'));
    const ar = JSON.parse(fs.readFileSync(path.join(FRONTEND, 'locales', 'ar.json'), 'utf8'));
    const keys = new Set();
    const re = /\bt\('((?:[^'\\]|\\.)*)'\)/g;
    let m;
    while ((m = re.exec(src))) keys.add(m[1].replace(/\\'/g, "'"));
    assert.ok(keys.size >= 40, 'found only ' + keys.size + ' strings');
    const missing = [...keys].filter((k) => !(k in en) || !(k in ar));
    assert.deepStrictEqual(missing, [], 'not in both catalogs');
    const same = [...keys].filter((k) => en[k] === ar[k] || !(ar[k] || '').trim());
    assert.deepStrictEqual(same, [], 'Arabic missing or identical to English');
  });
  await mutant('a user-facing string loses its translation key', (s) => s.replace("t('Add group')", "'Add group'"), async (mutated) => {
    const keys = new Set();
    const re = /\bt\('((?:[^'\\]|\\.)*)'\)/g;
    let m;
    while ((m = re.exec(mutated))) keys.add(m[1]);
    assert.ok(keys.has('Add group'));
  });

  console.log('\nretail_modifiers_settings_test: ' + passed + ' passed');
}

main().catch(() => { process.exit(1); });
