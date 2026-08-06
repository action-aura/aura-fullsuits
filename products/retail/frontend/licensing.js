/**
 * Aura Retail -- Licensing UI (Phase 7 Part G).
 *
 * Self-contained: does not depend on the shared SubsystemApp shell that
 * subsystem-retail.js expects. At the time this file was written (Part G),
 * no such shell existed anywhere in this product -- no index.html, no
 * SubsystemApp object -- so this page stood alone to give the required
 * activation flow (Part G) somewhere real to run. That shell (index.html
 * + app-shell.js) was subsequently built by a later branch; this file was
 * kept self-contained regardless since it never needed the shell.
 *
 * Talks only to /api/licensing/* (Part E/G/L routes, already tested in
 * commercial_runtime/licensing_contracts/tests/test_routes.py) -- never
 * touches /api/sub/retail/* itself.
 *
 * DOM is built with createElement/textContent throughout -- deliberately no
 * innerHTML/outerHTML assignment anywhere in this file, even for values
 * that are hardcoded or already escaped, so there is no XSS-sink pattern to
 * audit in the first place.
 */
(function () {
  'use strict';

  const messageArea = document.getElementById('message-area');
  const content = document.getElementById('content');

  const STATE_LABELS = {
    NOT_CONFIGURED: { text: 'Not configured', cls: 'state-neutral' },
    ACTIVATION_REQUIRED: { text: 'Activation required', cls: 'state-neutral' },
    ACTIVATING: { text: 'Activating…', cls: 'state-neutral' },
    ACTIVE_ONLINE: { text: 'Active', cls: 'state-active' },
    ACTIVE_OFFLINE: { text: 'Active (offline)', cls: 'state-active' },
    WARNING: { text: 'Check-in needed soon', cls: 'state-warning' },
    GRACE_PERIOD: { text: 'Offline grace period', cls: 'state-warning' },
    RESTRICTED: { text: 'Restricted', cls: 'state-restricted' },
    SUSPENDED: { text: 'Suspended', cls: 'state-restricted' },
    REVOKED: { text: 'Revoked', cls: 'state-restricted' },
    EXPIRED: { text: 'Expired', cls: 'state-restricted' },
    DEVICE_DEACTIVATED: { text: 'Device deactivated', cls: 'state-neutral' },
    CLOCK_REVIEW_REQUIRED: { text: 'Clock review required', cls: 'state-warning' },
    LOCAL_STATE_CORRUPT: { text: 'Local state needs reset', cls: 'state-restricted' },
  };

  const REASON_MESSAGES = {
    INVALID_REQUEST: 'Please check the information entered.',
    ACTIVATION_REJECTED: 'This license key could not be activated. Double-check the key and try again, or contact support.',
    PRODUCT_MISMATCH: 'This license key is not valid for Aura Retail.',
    PLATFORM_NOT_ALLOWED: 'This license key is not valid for a Windows installation.',
    DEVICE_LIMIT_REACHED: 'This license has reached its device limit. Deactivate another device or contact support to add capacity.',
    RATE_LIMITED: 'Too many attempts. Please wait a moment and try again.',
    NETWORK_UNAVAILABLE: 'Could not reach the licensing service. Check your internet connection and try again.',
    REQUEST_TIMED_OUT: 'The request timed out. Please try again.',
    TLS_VERIFICATION_FAILED: 'A secure connection to the licensing service could not be established.',
    SERVICE_TEMPORARILY_UNAVAILABLE: 'The licensing service is temporarily unavailable. Please try again shortly.',
    SIGNING_KEY_UNAVAILABLE: 'The licensing service is temporarily unavailable. Please try again shortly.',
    MALFORMED_RESPONSE: 'Received an unexpected response from the licensing service. Please try again.',
    DEVICE_KEY_UNAVAILABLE: 'This device is not yet set up for activation. Please try again.',
  };

  function clearChildren(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function el(tag, opts, children) {
    const node = document.createElement(tag);
    if (opts) {
      if (opts.className) node.className = opts.className;
      if (opts.id) node.id = opts.id;
      if (opts.text !== undefined) node.textContent = opts.text;
      if (opts.type) node.type = opts.type;
      if (opts.placeholder) node.placeholder = opts.placeholder;
      if (opts.autocomplete) node.autocomplete = opts.autocomplete;
      if (opts.spellcheck !== undefined) node.spellcheck = opts.spellcheck;
      if (opts.htmlFor) node.htmlFor = opts.htmlFor;
      if (opts.disabled) node.disabled = true;
    }
    (children || []).forEach((c) => node.appendChild(c));
    return node;
  }

  function showMessage(text, kind) {
    clearChildren(messageArea);
    messageArea.appendChild(el('div', { className: 'message message-' + kind, text: text }));
  }

  function clearMessage() {
    clearChildren(messageArea);
  }

  async function apiGet(url) {
    const res = await fetch(url, { credentials: 'include', cache: 'no-store' });
    return { status: res.status, body: await res.json().catch(() => ({})) };
  }

  async function apiPost(url, body) {
    const res = await fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    return { status: res.status, body: await res.json().catch(() => ({})) };
  }

  function stateBadge(state) {
    const info = STATE_LABELS[state] || { text: state, cls: 'state-neutral' };
    return el('span', { className: 'state-badge ' + info.cls, text: info.text });
  }

  async function refresh() {
    const { status, body } = await apiGet('/api/licensing/status');
    if (status !== 200) {
      renderNetworkProblem();
      return;
    }
    render(body);
  }

  function renderNetworkProblem() {
    clearChildren(content);
    const retryBtn = el('button', { text: 'Retry' });
    retryBtn.addEventListener('click', refresh);
    content.appendChild(el('div', {}, [stateBadge('NOT_CONFIGURED')]));
    content.appendChild(el('p', { text: 'Owner Service Temporarily Unavailable — could not check licensing status.' }));
    content.appendChild(retryBtn);
  }

  function render(status) {
    const state = status.current_state;
    if (state === 'NOT_CONFIGURED' || state === 'ACTIVATION_REQUIRED') {
      renderActivationForm(state);
      return;
    }
    renderLicenseStatus(status);
  }

  function renderActivationForm(state) {
    clearChildren(content);
    content.appendChild(el('div', {}, [stateBadge(state)]));

    const detailText = state === 'NOT_CONFIGURED'
      ? 'This installation is not yet connected to a licensing server. Owner licensing is not configured for this build.'
      : 'Enter your Aura Retail license key to activate this installation.';
    content.appendChild(el('p', { text: detailText }));

    content.appendChild(el('label', { htmlFor: 'license-key-input', text: 'License key' }));
    const input = el('input', {
      id: 'license-key-input', type: 'text', autocomplete: 'off', spellcheck: false,
      placeholder: 'AURA-RETAIL-XXXX-YYYY-ZZZZ',
    });
    content.appendChild(input);

    const activateBtn = el('button', { id: 'activate-btn', text: 'Activate' });
    activateBtn.addEventListener('click', () => onActivateClicked(input, activateBtn));
    content.appendChild(activateBtn);
  }

  async function onActivateClicked(input, btn) {
    const key = input.value.trim();
    if (!key) {
      showMessage('Please enter a license key.', 'error');
      return;
    }

    clearMessage();
    btn.disabled = true;
    clearChildren(btn);
    btn.appendChild(el('span', { className: 'spinner' }));
    btn.appendChild(document.createTextNode(' Activating…'));

    const badge = content.querySelector('.state-badge');
    if (badge) badge.replaceWith(stateBadge('ACTIVATING'));

    try {
      const { status, body } = await apiPost('/api/licensing/activate', { license_key: key });
      // The key never lingers in this scope longer than needed to send it.
      input.value = '';
      if (status === 200 && body.result === 'SUCCESS') {
        showMessage('Activation successful.', 'info');
      } else if (status === 202 && body.result === 'PENDING') {
        // Phase 8 Part O: Owner is holding this activation for manual
        // approval, not rejecting it -- a distinct, non-error state.
        showMessage(
          "This activation is awaiting manual approval. We'll keep checking automatically -- no action needed right now.",
          'info',
        );
      } else {
        const reason = body.reason_code || 'ACTIVATION_REJECTED';
        showMessage(REASON_MESSAGES[reason] || REASON_MESSAGES.ACTIVATION_REJECTED, 'error');
      }
    } catch (e) {
      showMessage(REASON_MESSAGES.NETWORK_UNAVAILABLE, 'error');
    }
    await refresh();
  }

  function addStatusRow(dl, label, value) {
    dl.appendChild(el('dt', { text: label }));
    dl.appendChild(el('dd', { text: value }));
  }

  function renderLicenseStatus(status) {
    const state = status.current_state;
    const canDeactivate = state !== 'DEVICE_DEACTIVATED';

    clearChildren(content);
    content.appendChild(el('div', {}, [stateBadge(state)]));

    const dl = el('dl');
    addStatusRow(dl, 'Product', status.product_code || '—');
    if (status.installation_id) addStatusRow(dl, 'Installation', status.installation_id);
    if (status.license_status) addStatusRow(dl, 'License status', status.license_status);
    if (status.last_successful_checkin_at) addStatusRow(dl, 'Last check-in', status.last_successful_checkin_at);
    content.appendChild(dl);

    if (state === 'RESTRICTED' || state === 'GRACE_PERIOD' || state === 'WARNING') {
      content.appendChild(el('p', {
        text: 'Some features are limited in this state. Existing records remain fully viewable, and backup/restore/export remain available.',
      }));
    }
    if (state === 'SUSPENDED' || state === 'REVOKED' || state === 'EXPIRED') {
      content.appendChild(el('p', {
        text: 'Commercial features are unavailable. Your existing data is safe and remains viewable; backup, restore, and export remain available.',
      }));
    }

    const row = el('div', { className: 'row' });
    const checkinBtn = el('button', { id: 'checkin-btn', className: 'secondary', text: 'Check Now' });
    checkinBtn.addEventListener('click', () => onCheckInClicked(checkinBtn));
    row.appendChild(checkinBtn);

    if (canDeactivate) {
      const deactivateBtn = el('button', { id: 'deactivate-btn', className: 'danger', text: 'Deactivate This Device' });
      deactivateBtn.addEventListener('click', onDeactivateClicked);
      row.appendChild(deactivateBtn);
    }
    content.appendChild(row);
  }

  async function onCheckInClicked(btn) {
    clearMessage();
    btn.disabled = true;
    try {
      const { status, body } = await apiPost('/api/licensing/check-in', {});
      if (status === 200 && body.last_attempt_reached_owner) {
        showMessage('Check-in complete.', 'info');
      } else if (status === 200) {
        // The route reports current status either way (Part AD: a network
        // blip must not look like an error state) -- but this specific
        // attempt did not actually reach Owner, and the UI should say so
        // honestly rather than claim success.
        showMessage('Could not reach the licensing service. Your current status is unchanged.', 'error');
      } else {
        showMessage(REASON_MESSAGES[body.reason_code] || REASON_MESSAGES.SERVICE_TEMPORARILY_UNAVAILABLE, 'error');
      }
    } catch (e) {
      showMessage(REASON_MESSAGES.NETWORK_UNAVAILABLE, 'error');
    }
    await refresh();
  }

  async function onDeactivateClicked() {
    if (!window.confirm('Deactivate this device? You will need to reactivate with a license key to use commercial features again.')) {
      return;
    }
    clearMessage();
    try {
      const { status, body } = await apiPost('/api/licensing/deactivate', {});
      if (status === 200) {
        showMessage('This device has been deactivated.', 'info');
      } else {
        showMessage(REASON_MESSAGES[body.reason_code] || REASON_MESSAGES.ACTIVATION_REJECTED, 'error');
      }
    } catch (e) {
      showMessage(REASON_MESSAGES.NETWORK_UNAVAILABLE, 'error');
    }
    await refresh();
  }

  refresh();
})();
