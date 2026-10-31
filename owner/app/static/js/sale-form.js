/* The "Sell a licence" form (templates/licensing/sale.html).
 *
 * Owner ships script-src 'self' (no inline script), so this is a file.
 *  - Trial-payment page (form[data-prefill]): the amount box is pre-filled with what the
 *    customer pays first for the chosen package (+ any extra phones and desktops) and the line under it
 *    says what that is made of. The seller can still type another amount; once they have,
 *    changing the package no longer overwrites it.
 *  - Choosing Cash shows the cash rule (hand over ALL of it within 24 hours, no deduction).
 * Nothing here is a control: the server re-checks every value.
 */
(function () {
  'use strict';
  var form = document.getElementById('sale-form');
  if (!form) { return; }
  var plan = document.getElementById('sale-plan-id');
  var amount = document.getElementById('sale-amount');
  var phones = document.getElementById('sale-extra-phones');
  var desktops = document.getElementById('sale-extra-desktops');
  var method = document.getElementById('sale-method');
  var note = document.getElementById('sale-amount-breakdown');
  var cashRule = document.getElementById('sale-cash-rule');
  var prefill = form.getAttribute('data-prefill') === '1';
  var typedByHand = false;

  if (amount) { amount.addEventListener('input', function () { typedByHand = true; }); }

  function chosenPackage() {
    return plan && plan.selectedIndex >= 0 ? plan.options[plan.selectedIndex] : null;
  }

  function syncAmount() {
    var option = chosenPackage();
    if (!option) { return; }
    if (note) { note.textContent = option.getAttribute('data-breakdown') || ''; }
    if (!prefill || typedByHand || !amount) { return; }
    var first = parseFloat(option.getAttribute('data-first-payment'));
    var phonePrice = parseFloat(option.getAttribute('data-phone-price')) || 0;
    var desktopPrice = parseFloat(option.getAttribute('data-desktop-price')) || 0;
    var phoneCount = parseInt(phones ? phones.value : '0', 10) || 0;
    var desktopCount = parseInt(desktops ? desktops.value : '0', 10) || 0;
    if (!isNaN(first)) { amount.value = (first + phoneCount * phonePrice + desktopCount * desktopPrice).toFixed(2); }
  }

  function syncCashRule() {
    if (cashRule && method) { cashRule.hidden = method.value !== 'CASH'; }
  }

  if (plan) { plan.addEventListener('change', syncAmount); }
  if (phones) { phones.addEventListener('input', syncAmount); }
  if (desktops) { desktops.addEventListener('input', syncAmount); }
  if (method) { method.addEventListener('change', syncCashRule); }
  syncCashRule();
}());
