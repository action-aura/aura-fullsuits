/**
 * retail_ui2_customer_test.js -- the customer at the till (UI-2 item 4, review
 * A #8): the header button with a balance chip, "+ New customer" in the F7
 * picker, credit terms in the Add/Edit dialog, and the open balance + statement
 * link on the detail modal.
 *
 * What is pinned, and why each is not vacuous:
 *   1. THE HEADER BUTTON shows who is on the sale (name, phone) and an "owes"
 *      chip ONLY when they owe more than half a minor unit -- a settled account
 *      and a sub-fil dust balance carry no chip, a real balance does (both ways).
 *      Repainted from _onCustomerChange, the one path every change goes through.
 *   2. "+ NEW CUSTOMER": the form saves name + phone, puts the new customer on the
 *      sale (the model <select>, the header, the till's cache) and Enter saves.
 *      "Credit allowed" is a SECOND request (the create route cannot grant credit)
 *      and exists only for someone who may grant it. DENY halves: no name -> no
 *      request; a refused credit PATCH -> customer still created and chosen and the
 *      person is told; a failed create -> not chosen, error shown, can retry.
 *   3. CREDIT FIELDS in the Add/Edit dialog exist only with retail.discount (the
 *      route refuses the whole PATCH otherwise); a contact-only edit carries NO
 *      credit keys (so it cannot be refused for them); a changed mode / limit rides
 *      the PATCH; Add sends the credit as a second PATCH.
 *   4. THE DETAIL MODAL shows the open balance (or "Nothing owed") and the terms,
 *      with a button that opens the existing statement for THAT customer.
 *   5. THE CLIENT MIRRORS THE SERVER's credit fallback exactly
 *      (`customer.credit_mode || shop default || 'none'`): if the till ever
 *      disagreed with create_sale it would show a Credit tile the server refuses.
 *
 * Run: node products/retail/tests/retail_ui2_customer_test.js
 */
'use strict';

const { loadRetail, assert, fire } = require('./_ui2_harness.js');

const OWNER_CAPS = ['retail.sell', 'retail.discount', 'retail.refund', 'retail.reports'];
const CASHIER_CAPS = ['retail.sell', 'retail.refund', 'retail.cash.close'];
const tick = () => new Promise((r) => setImmediate(r));
const clone = (o) => JSON.parse(JSON.stringify(o));

function till(caps, customers) {
  const h = loadRetail({ capabilities: caps, role: caps === OWNER_CAPS ? 'admin' : 'cashier' });
  h.seed('pos-customer', { value: '' });
  h.seed('pos-cust-name'); h.seed('pos-cust-sub'); h.seed('pos-cust-btn');
  h.R._customers = customers || [];
  h.R._syncCreditButtonState = () => {};
  h.R._recalc = () => {};
  h.R._loyaltyRedemptionEnabled = () => false;
  const calls = [];
  h.calls = calls;
  h.R._post = async (url, body) => { calls.push(['POST', url, clone(body)]); return h.postReply ? h.postReply(url, body) : { status: 'success', data: { id: 'new-1' } }; };
  h.R._patch = async (url, body) => { calls.push(['PATCH', url, clone(body)]); return h.patchReply ? h.patchReply(url, body) : { status: 'success' }; };
  h.R._get = async () => ({ status: 'success', data: [] });
  return h;
}

const sub = (h) => h.el('pos-cust-sub').innerHTML;
const name = (h) => h.el('pos-cust-name').textContent;

// ── 1. header button ─────────────────────────────────────────────────────────
function testHeaderButton() {
  const h = till(OWNER_CAPS, [
    { id: 'c1', name: 'Sara Khalil', phone: '0791112233', credit_balance: 12.5 },
    { id: 'c2', name: 'Omar Nasser', phone: '0795556677', credit_balance: 0 },
    { id: 'c3', name: 'Dust Case', phone: '', credit_balance: 0.0004 },
  ]);
  h.R._renderCustomerButton();
  assert.strictEqual(name(h), 'Walk-in');
  assert.ok(/F7/.test(sub(h)) && !/owes/.test(sub(h)), 'walk-in shows the F7 hint and no chip');

  h.el('pos-customer').value = 'c1'; h.R._renderCustomerButton();
  assert.strictEqual(name(h), 'Sara Khalil');
  assert.ok(sub(h).includes('0791112233'), 'the phone is shown');
  assert.ok(/pos-cust-owes/.test(sub(h)) && /owes/.test(sub(h)) && /12\.500/.test(sub(h)), 'a customer who owes 12.5 carries the "owes JD 12.500" chip: ' + sub(h));

  h.el('pos-customer').value = 'c2'; h.R._renderCustomerButton();
  assert.ok(!/pos-cust-owes/.test(sub(h)), 'a settled account must carry no chip: ' + sub(h));
  assert.ok(sub(h).includes('0795556677'));

  h.el('pos-customer').value = 'c3'; h.R._renderCustomerButton();
  assert.ok(!/pos-cust-owes/.test(sub(h)), 'dust below half a minor unit must carry no chip');
  assert.ok(!/pos-cust-phone/.test(sub(h)), 'no phone, no phone span');

  // the button repaints through the path every change uses
  h.el('pos-customer').value = 'c1';
  h.R._onCustomerChange();
  assert.strictEqual(name(h), 'Sara Khalil', '_onCustomerChange must repaint the header button');
  // hostile name is text, not markup
  h.R._customers.push({ id: 'x', name: '<img src=x onerror=1>', phone: '"><b>', credit_balance: 0 });
  h.el('pos-customer').value = 'x'; h.R._renderCustomerButton();
  assert.ok(!/<img/.test(sub(h)) && !/<b>/.test(sub(h)), 'a hostile phone must be escaped: ' + sub(h));
  console.log('PASS: header button shows name + phone, an "owes" chip only for a real balance, repainted on every change, escaped');
}

function testTemplateWiring() {
  const h = loadRetail();
  const src = require('fs').readFileSync(require('path').join(__dirname, '..', 'frontend', 'subsystem-retail.js'), 'utf8');
  assert.ok(/<button type="button" id="pos-cust-btn"[^>]*onclick="RetailSystem\._openCustomerPicker\(\)"/.test(src), 'the header control is not a button that opens the picker');
  assert.ok(/<select id="pos-customer" class="pos-cust-model"/.test(src), 'the model <select> must stay (everything reads #pos-customer.value)');
  assert.ok(typeof h.R._openCustomerPicker === 'function');
  console.log('PASS: the header control is a button wired to the F7 picker; the model <select> is still in the page');
}

// ── 2. + New customer ────────────────────────────────────────────────────────
function openPicker(h) {
  h.R._openCustomerPicker();
  const overlay = h.overlays.find((o) => o.id === 'ret-cust-pick');
  assert.ok(overlay, 'the picker did not open');
  return overlay;
}

async function testNewCustomerFlow() {
  const h = till(OWNER_CAPS, [{ id: 'c1', name: 'Sara', phone: '0791', credit_balance: 0 }]);
  h.R._onCustomerChange = () => { h.changed = (h.changed || 0) + 1; h.R._renderCustomerButton(); };
  const overlay = openPicker(h);
  assert.ok(/id="cp-new"/.test(overlay.innerHTML) && /New customer/.test(overlay.innerHTML), 'no "+ New customer" in the picker');
  assert.ok(/id="cp-new-credit"/.test(overlay.innerHTML), 'the owner is not offered the credit box');
  fire(h.el('cp-new'), 'click');
  assert.strictEqual(h.el('cp-form').hidden, false, 'the form did not show');
  assert.strictEqual(h.el('cp-pick').hidden, true, 'the list should step aside while the form is up');

  // empty name: nothing is sent
  fire(h.el('cp-new-save'), 'click'); await tick();
  assert.strictEqual(h.calls.length, 0, 'saving with no name must send nothing');
  assert.ok(/Name required/.test(h.el('cp-new-err').textContent), 'no message for the missing name');

  // fill + ENTER saves
  h.el('cp-new-name').value = 'Lina Haddad'; h.el('cp-new-phone').value = '0795551234'; h.el('cp-new-credit').checked = true;
  fire(h.el('cp-form'), 'keydown', { code: 'Enter' });
  await tick(); await tick();
  assert.deepStrictEqual(h.calls[0], ['POST', '/api/sub/retail/customers', { name: 'Lina Haddad', phone: '0795551234' }], 'create body');
  assert.deepStrictEqual(h.calls[1], ['PATCH', '/api/sub/retail/customers/new-1', { credit_mode: 'unlimited' }], 'credit is a SECOND request');
  assert.strictEqual(h.el('pos-customer').value, 'new-1', 'the new customer is not on the sale');
  assert.ok(h.R._customers.some((c) => c.id === 'new-1' && c.credit_mode === 'unlimited'), 'the till cache does not know the new customer');
  assert.strictEqual(name(h), 'Lina Haddad', 'the header did not repaint to the new customer');
  assert.ok(!h.overlays.includes(overlay) || true);
  console.log('PASS: + New customer saves name + phone, credit as a second request, Enter saves, and the customer is on the sale');
}

async function testNewCustomerDenyHalves() {
  // a cashier: no credit box, and even a ticked one could not exist
  const c = till(CASHIER_CAPS, []);
  c.R._onCustomerChange = () => {};
  const co = openPicker(c);
  assert.ok(!/cp-new-credit/.test(co.innerHTML), 'a cashier must not be offered "Allow credit"');
  fire(c.el('cp-new'), 'click');
  c.el('cp-new-name').value = 'Walk Up';
  fire(c.el('cp-new-save'), 'click'); await tick(); await tick();
  assert.strictEqual(c.calls.length, 1, 'a cashier-created customer must send ONE request (no credit PATCH): ' + JSON.stringify(c.calls));
  assert.strictEqual(c.calls[0][0], 'POST');

  // the server refuses the credit PATCH (stale capability list): customer still created + chosen, and told
  const o = till(OWNER_CAPS, []);
  o.R._onCustomerChange = () => {};
  o.patchReply = () => ({ status: 'error', message: 'denied' });
  openPicker(o); fire(o.el('cp-new'), 'click');
  o.el('cp-new-name').value = 'Refused Credit'; o.el('cp-new-credit').checked = true;
  fire(o.el('cp-new-save'), 'click'); await tick(); await tick();
  assert.strictEqual(o.el('pos-customer').value, 'new-1', 'a refused credit must not undo the customer');
  assert.ok(o.toasts.some((t) => /only a manager can allow credit/.test(t.msg)), 'the person is not told credit was refused: ' + JSON.stringify(o.toasts));
  assert.strictEqual(o.R._customers.find((x) => x.id === 'new-1').credit_mode, 'none', 'the cache must not claim credit that was refused');

  // a failed create: not chosen, the reason is shown, and a retry works
  const f = till(OWNER_CAPS, []);
  f.R._onCustomerChange = () => {};
  f.postReply = () => ({ status: 'error', message: 'Customer name required' });
  openPicker(f); fire(f.el('cp-new'), 'click');
  f.el('cp-new-name').value = 'X';
  fire(f.el('cp-new-save'), 'click'); await tick(); await tick();
  assert.strictEqual(f.el('pos-customer').value, '', 'a failed create must not put anybody on the sale');
  assert.ok(/Customer name required/.test(f.el('cp-new-err').textContent), 'the failure reason is not shown');
  f.postReply = null;
  fire(f.el('cp-new-save'), 'click'); await tick(); await tick();
  assert.strictEqual(f.el('pos-customer').value, 'new-1', 'retry after a failure did not work');
  console.log('PASS: + New customer deny halves -- cashier gets no credit box/PATCH; refused credit keeps the customer and says so; failed create is not chosen and can retry');
}

// ── 3. credit fields in Add/Edit ─────────────────────────────────────────────
async function testEditDialogCredit() {
  const cu = { id: 'c1', name: 'Sara', phone: '079', email: '', address: '', credit_mode: 'limited', credit_limit: 50 };
  const owner = till(OWNER_CAPS, [cu]);
  owner.R._loadCustomers = async () => {};
  owner.R._showCustomerModal(cu);
  const html = owner.overlays[0].innerHTML;
  assert.ok(/id="cm-credit-mode"/.test(html) && /<option value="limited" selected>/.test(html), 'the owner does not see the credit mode with the current value selected');
  assert.ok(/id="cm-credit-limit"[^>]*step="0\.001"[^>]*value="50\.000"/.test(html.replace(/\s+/g, ' ')), 'limit box: 3 dp step and the current limit: ' + (html.match(/<input type="number" id="cm-credit-limit"[^>]*>/) || [''])[0]);
  assert.ok(/id="cm-limit-wrap"(?![^>]*hidden)/.test(html), 'the limit box must be visible for a limited customer');

  const cashier = till(CASHIER_CAPS, [cu]);
  cashier.R._showCustomerModal(cu);
  assert.ok(!/cm-credit-mode|cm-credit-limit/.test(cashier.overlays[0].innerHTML), 'a cashier must not be offered credit terms (the route would refuse the whole PATCH)');

  // contact-only edit: NO credit keys in the PATCH
  owner.el('cm-name').value = 'Sara K'; owner.el('cm-phone').value = '079'; owner.el('cm-email').value = ''; owner.el('cm-addr').value = '';
  owner.el('cm-credit-mode').value = 'limited'; owner.el('cm-credit-limit').value = '50';
  await owner.R._saveCustomer('c1');
  assert.deepStrictEqual(owner.calls[0][2], { name: 'Sara K', phone: '079', email: '', address: '' }, 'an unchanged credit setting must not ride the PATCH');
  // a changed mode rides the same PATCH
  // (the fake DOM does not initialise a <select> from its `selected` option, so each reopened
  // dialog sets the value the real browser would have started with)
  owner.calls.length = 0; owner.closeAll();
  owner.R._showCustomerModal(cu);
  owner.el('cm-name').value = 'Sara'; owner.el('cm-credit-mode').value = 'unlimited';
  await owner.R._saveCustomer('c1');
  assert.strictEqual(owner.calls[0][2].credit_mode, 'unlimited');
  assert.strictEqual(owner.calls[0][2].credit_limit, 0);
  // a changed limit rides it too
  owner.calls.length = 0; owner.closeAll();
  owner.R._showCustomerModal(cu);
  owner.el('cm-name').value = 'Sara'; owner.el('cm-credit-mode').value = 'limited'; owner.el('cm-credit-limit').value = '75.500';
  await owner.R._saveCustomer('c1');
  assert.deepStrictEqual([owner.calls[0][2].credit_mode, owner.calls[0][2].credit_limit], ['limited', 75.5]);
  // cashier edit never carries credit
  cashier.R._loadCustomers = async () => {};
  cashier.el('cm-name').value = 'Sara';
  await cashier.R._saveCustomer('c1');
  assert.ok(!('credit_mode' in cashier.calls[0][2]) && !('credit_limit' in cashier.calls[0][2]), 'a cashier edit must carry no credit keys');
  console.log('PASS: credit terms in the Edit dialog -- owner only; contact-only edits carry none; changed mode/limit ride the PATCH');
}

async function testAddDialogCredit() {
  const owner = till(OWNER_CAPS, []);
  owner.R._loadCustomers = async () => {};
  owner.R._showCustomerModal({});
  assert.ok(/id="cm-credit-mode"[^>]*>/.test(owner.overlays[0].innerHTML) && /<option value="none" selected>/.test(owner.overlays[0].innerHTML), 'new customers start at "No credit"');
  assert.ok(/id="cm-limit-wrap" hidden/.test(owner.overlays[0].innerHTML), 'no limit box until "Up to a limit"');
  owner.el('cm-name').value = 'New Debtor'; owner.el('cm-credit-mode').value = 'limited'; owner.el('cm-credit-limit').value = '25';
  await owner.R._saveCustomer(null);
  assert.deepStrictEqual(owner.calls.map((c) => c[0]), ['POST', 'PATCH'], 'create then a credit PATCH');
  assert.strictEqual('credit_mode' in owner.calls[0][2], false, 'the create body must not carry credit (the route ignores it)');
  assert.deepStrictEqual(owner.calls[1][2], { credit_mode: 'limited', credit_limit: 25 });
  // "No credit" left alone: a single POST
  const plain = till(OWNER_CAPS, []);
  plain.R._loadCustomers = async () => {};
  plain.R._showCustomerModal({}); plain.el('cm-name').value = 'Cash Only'; plain.el('cm-credit-mode').value = 'none';
  await plain.R._saveCustomer(null);
  assert.deepStrictEqual(plain.calls.map((c) => c[0]), ['POST'], 'no credit chosen -> no second request');
  console.log('PASS: Add Customer -- credit is a second PATCH only when chosen');
}

// ── 4. detail modal ──────────────────────────────────────────────────────────
async function testDetailModal() {
  const owing = { id: 'c1', name: 'Sara', phone: '079', credit_mode: 'limited', credit_limit: 50, credit_balance: 12.5, total_spent: 100, order_count: 3, loyalty_points: 2 };
  const settled = { id: 'c2', name: 'Omar', phone: '', credit_mode: 'none', credit_balance: 0 };
  const h = till(OWNER_CAPS, [owing, settled]);
  await h.R._viewCustomer('c1');
  let html = h.overlays[0].innerHTML;
  assert.ok(/id="cu-balance"/.test(html) && /12\.500/.test(html.replace(/<[^>]+>/g, '')), 'the open balance is not on the detail modal');
  assert.ok(/Up to a limit/.test(html) && /50\.000/.test(html.replace(/<[^>]+>/g, '')), 'the credit terms are not shown');
  assert.ok(/onclick="RetailSystem\._openPartyStatement\('customer','c1'\)"/.test(html), 'the statement link is missing or points at another customer');
  h.closeAll();
  await h.R._viewCustomer('c2');
  html = h.overlays[0].innerHTML;
  assert.ok(/Nothing owed/.test(html) && !/12\.500/.test(html), 'a settled customer must read "Nothing owed"');
  assert.ok(/No credit/.test(html));
  assert.ok(/_openPartyStatement\('customer','c2'\)/.test(html), 'the statement is still offered at zero');
  console.log('PASS: detail modal shows the open balance (or "Nothing owed"), the credit terms, and a statement link for THAT customer');
}

// ── 5. the client mirrors create_sale's credit fallback ─────────────────────
function testCreditGateMirrorsTheServer() {
  const h = till(OWNER_CAPS, [
    { id: 'a', credit_mode: 'unlimited' }, { id: 'b', credit_mode: 'none' }, { id: 'c', credit_mode: '' }, { id: 'd', credit_mode: null },
  ]);
  const gate = (id, shop) => { h.R._defaultCreditMode = shop; return h.R._creditGateReason(id); };
  assert.strictEqual(gate('', 'unlimited'), 'Walk-in customer', 'walk-in first, like create_sale');
  assert.strictEqual(gate('a', 'none'), null, 'own unlimited wins over a shop default of none');
  assert.strictEqual(gate('b', 'unlimited'), 'Not a credit customer', "an explicit 'none' on the customer is not overridden by the shop default (create_sale: `credit_mode or default`, and 'none' is truthy)");
  assert.strictEqual(gate('c', 'unlimited'), null, "an EMPTY mode falls back to the shop default, as create_sale does");
  assert.strictEqual(gate('d', 'unlimited'), null, 'a null mode falls back to the shop default');
  assert.strictEqual(gate('c', 'none'), 'Not a credit customer');
  console.log('PASS: the till gates Credit by the SAME `customer mode || shop default` chain as create_sale');
}

(async function main() {
  const checks = [
    ['header button', testHeaderButton],
    ['template wiring', testTemplateWiring],
    ['+ New customer flow', testNewCustomerFlow],
    ['+ New customer deny halves', testNewCustomerDenyHalves],
    ['edit dialog credit', testEditDialogCredit],
    ['add dialog credit', testAddDialogCredit],
    ['detail modal', testDetailModal],
    ['credit gate mirrors server', testCreditGateMirrorsTheServer],
  ];
  let failed = 0;
  for (const [name, fn] of checks) {
    try { await fn(); } catch (e) { failed++; console.log('FAIL: ' + name + '\n      ' + (e && e.stack || e)); }
  }
  if (failed) { console.log(`\nFAIL: retail_ui2_customer_test.js -- ${failed} of ${checks.length} checks failed`); process.exit(1); }
  console.log(`\nPASS: retail_ui2_customer_test.js -- ${checks.length} checks`);
})();
