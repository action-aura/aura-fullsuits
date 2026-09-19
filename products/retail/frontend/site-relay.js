/* Aura Retail -- the operator's screen for hub mode (R-LAN).
 *
 * Talks only to /api/site-relay/* on this device's OWN loopback backend
 * (admin_routes.py). It never talks to the LAN-facing relay: the relay serves
 * the sync wire contract to paired devices, and the controls that ADMIT a
 * device belong on the till, behind an admin login.
 *
 * Vanilla JS, no framework, no build step -- matching every other page in this
 * folder (see whatsapp.js / einvoicing.js). Do not introduce one here.
 *
 * A NOTE ON THE WORDING, since it is doing real work. Nothing on this screen
 * says "relay", "SPKI", "installation_id" or "Ed25519". The person using it
 * owns a shop; they are deciding whether to let a tablet join, and they need
 * to understand the consequence, not the mechanism. So: "security key" rather
 * than "SPKI pin", "Connect a device" rather than "issue pairing code", and
 * the one genuinely important warning -- that whoever holds the code can add a
 * device to their shop -- is written in those words rather than left implied.
 */
(function () {
  'use strict';

  var els = {};
  var countdownTimer = null;

  function $(id) { return document.getElementById(id); }

  function show(el, visible) {
    if (el) { el.hidden = !visible; }
  }

  function message(text, kind) {
    var el = els.message;
    if (!text) { show(el, false); return; }
    el.textContent = text;
    el.className = 'message message-' + (kind || 'info');
    show(el, true);
  }

  function api(path, options) {
    // Same-origin, credentials included: these routes are behind the ordinary
    // admin session cookie the rest of the app already uses.
    var opts = Object.assign({ credentials: 'same-origin' }, options || {});
    return fetch('/api/site-relay' + path, opts).then(function (resp) {
      return resp.json().catch(function () {
        // A non-JSON body here almost always means the session expired and
        // something returned a login page. Say that, rather than surfacing a
        // JSON parse error the shop owner can do nothing with.
        throw new Error('Your session has expired. Please sign in again.');
      }).then(function (body) {
        if (!resp.ok || body.success === false) {
          throw new Error(body.error || 'That did not work. Please try again.');
        }
        return body;
      });
    });
  }

  // Fixed YYYY-MM-DD HH:MM:SS in local time, never a bare toLocaleString().
  // A bare call formats against the OPERATING SYSTEM's locale rather than the
  // language chosen in the app, so the same relay timestamp read
  // "9/19/2026, 3:45:00 PM" on one till and rendered in Eastern Arabic-Indic
  // digits on another -- for a value whose whole job is letting two devices
  // agree on when they last talked. Same reasoning, and the same format, as
  // subsystem-retail.js's _auditTimestamp/_fixedDateTime; duplicated rather
  // than shared because this frontend has no module system (see that file's
  // _esc() for the established per-file-duplication convention).
  function formatWhen(value) {
    if (!value) { return '—'; }
    var d = new Date(value.indexOf('Z') === -1 ? value + 'Z' : value);
    if (isNaN(d.getTime())) { return value; }
    var p = function (n) { return String(n).padStart(2, '0'); };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
           ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  function renderStatus(body) {
    if (!body.enabled) {
      show(els.cardDisabled, true);
      show(els.cardStatus, false);
      show(els.cardConnect, false);
      show(els.cardDevices, false);
      return;
    }

    show(els.cardDisabled, false);
    show(els.cardStatus, true);
    show(els.cardConnect, true);
    show(els.cardDevices, true);

    var addresses = body.addresses || [];
    // An empty list is not an error: local_lan_addresses() is best-effort and
    // returns nothing on some multi-interface Windows setups. The address is a
    // convenience anyway -- devices learn it from the signed beacon and trust
    // the KEY, not the address -- so say something useful instead of blank.
    els.factAddresses.textContent = addresses.length
      ? addresses.join(', ')
      : 'Could not detect — devices will still find this hub automatically.';
    els.factPort.textContent = body.port;
    els.factPin.textContent = body.spki_pin || '—';

    renderDevices(body.devices || []);
  }

  function renderDevices(devices) {
    var body = els.devicesBody;
    body.textContent = '';

    if (!devices.length) {
      show(els.devicesEmpty, true);
      show(els.devicesTable, false);
      return;
    }
    show(els.devicesEmpty, false);
    show(els.devicesTable, true);

    devices.forEach(function (device) {
      var tr = document.createElement('tr');

      var name = document.createElement('td');
      // textContent, never innerHTML: the label is operator-supplied text that
      // arrived from another device, so it is untrusted input on this screen.
      name.textContent = device.label || 'Unnamed device';
      var id = document.createElement('div');
      id.className = 'mono';
      id.style.color = '#888';
      id.textContent = device.installation_id;
      name.appendChild(id);
      tr.appendChild(name);

      var when = document.createElement('td');
      when.textContent = formatWhen(device.paired_at);
      tr.appendChild(when);

      var status = document.createElement('td');
      var badge = document.createElement('span');
      badge.className = 'state-badge ' + (device.revoked ? 'state-restricted' : 'state-active');
      badge.textContent = device.revoked ? 'Removed' : 'Connected';
      status.appendChild(badge);
      tr.appendChild(status);

      var action = document.createElement('td');
      if (!device.revoked) {
        var btn = document.createElement('button');
        btn.className = 'danger small';
        btn.textContent = 'Remove';
        btn.addEventListener('click', function () { revoke(device, btn); });
        action.appendChild(btn);
      }
      tr.appendChild(action);

      body.appendChild(tr);
    });
  }

  function revoke(device, btn) {
    var name = device.label || 'this device';
    if (!window.confirm(
        'Remove ' + name + ' from the shop?\n\n' +
        'It will stop syncing with this hub immediately. It keeps the data it '
        + 'already has and can carry on selling on its own.')) {
      return;
    }
    btn.disabled = true;
    api('/devices/' + encodeURIComponent(device.installation_id) + '/revoke',
        { method: 'POST' })
      .then(function () {
        message(name + ' was removed.', 'info');
        return refresh();
      })
      .catch(function (err) {
        btn.disabled = false;
        message(err.message, 'error');
      });
  }

  function startCountdown(seconds) {
    stopCountdown();
    var remaining = seconds;
    function tick() {
      if (remaining <= 0) {
        stopCountdown();
        // The code is genuinely dead at this point -- the backend expires it
        // regardless of what this screen shows -- so hide it rather than leave
        // a code on screen that will silently fail when someone scans it.
        show(els.pairingOutput, false);
        show(els.btnPairDone, false);
        els.btnPair.disabled = false;
        message('That code expired. Press "Connect a device" for a new one.', 'info');
        return;
      }
      var mins = Math.floor(remaining / 60);
      var secs = remaining % 60;
      els.pairingCountdown.textContent = mins > 0
        ? mins + 'm ' + (secs < 10 ? '0' : '') + secs + 's'
        : secs + 's';
      remaining -= 1;
    }
    tick();
    countdownTimer = window.setInterval(tick, 1000);
  }

  function stopCountdown() {
    if (countdownTimer !== null) {
      window.clearInterval(countdownTimer);
      countdownTimer = null;
    }
  }

  function requestPairingCode() {
    els.btnPair.disabled = true;
    message('');
    api('/pair-code', { method: 'POST' })
      .then(function (body) {
        els.pairingQr.src = body.qr_svg;
        els.pairingText.value = body.qr_text;
        show(els.pairingOutput, true);
        show(els.btnPairDone, true);
        els.btnPair.textContent = 'Get another code';
        startCountdown(body.expires_in_seconds || 300);
      })
      .catch(function (err) {
        els.btnPair.disabled = false;
        message(err.message, 'error');
      });
  }

  function finishPairing() {
    stopCountdown();
    show(els.pairingOutput, false);
    show(els.btnPairDone, false);
    els.btnPair.disabled = false;
    els.btnPair.textContent = 'Connect a device';
    // The new device only appears once it has actually paired, so refresh
    // rather than optimistically adding a row for a device that may never
    // have completed.
    refresh();
  }

  function refresh() {
    return api('/status')
      .then(renderStatus)
      .catch(function (err) { message(err.message, 'error'); });
  }

  document.addEventListener('DOMContentLoaded', function () {
    els = {
      message: $('message'),
      cardDisabled: $('card-disabled'),
      cardStatus: $('card-status'),
      cardConnect: $('card-connect'),
      cardDevices: $('card-devices'),
      factAddresses: $('fact-addresses'),
      factPort: $('fact-port'),
      factPin: $('fact-pin'),
      btnPair: $('btn-pair'),
      btnPairDone: $('btn-pair-done'),
      pairingOutput: $('pairing-output'),
      pairingQr: $('pairing-qr'),
      pairingText: $('pairing-text'),
      pairingCountdown: $('pairing-countdown'),
      devicesTable: $('devices-table'),
      devicesBody: $('devices-body'),
      devicesEmpty: $('devices-empty'),
    };

    els.btnPair.addEventListener('click', requestPairingCode);
    els.btnPairDone.addEventListener('click', finishPairing);
    els.pairingText.addEventListener('focus', function () { this.select(); });

    refresh();
  });
})();
