/**
 * Aura Retail -- Jordan JoFotara e-invoicing UI (docs/einvoicing/phase1/).
 *
 * Self-contained, mirroring products/retail/frontend/licensing.js exactly
 * (same rationale: no shared SubsystemApp shell exists to plug into --
 * see that file's docstring). Talks only to /api/einvoicing/* -- never
 * touches /api/sub/retail/* itself.
 *
 * DOM is built with createElement/textContent throughout -- no
 * innerHTML/outerHTML assignment anywhere in this file, same policy as
 * licensing.js, so there is no XSS-sink pattern to audit.
 */
(function () {
  'use strict';

  // i18n.js (loaded before this script in einvoicing.html) defines the
  // global t()/AuraI18n. Falling back to identity when it's absent keeps
  // this file loadable standalone -- e.g. retail's own test harnesses run
  // product frontend files through a bare vm sandbox with no i18n.js in it,
  // and this file worked standalone before i18n was wired in, so it still
  // should (see retail_einvoicing_status_test.js's sandbox loader).
  const t = (typeof window !== 'undefined' && typeof window.t === 'function')
    ? window.t
    : function (s) { return s; };

  const messageArea = document.getElementById('message-area');
  const statusContent = document.getElementById('status-content');
  const settingsContent = document.getElementById('settings-content');
  const credentialsContent = document.getElementById('credentials-content');
  const outboxContent = document.getElementById('outbox-content');

  const OUTBOX_STATE_LABELS = {
    QUEUED: { text: 'Queued', cls: 'state-neutral' },
    SUBMITTING: { text: 'Submitting', cls: 'state-neutral' },
    SUBMITTING_UNKNOWN: { text: 'Confirming…', cls: 'state-warning' },
    AWAITING_CLEARANCE: { text: 'Awaiting clearance', cls: 'state-warning' },
    CLEARED: { text: 'Cleared', cls: 'state-active' },
    FAILED_PERMANENT: { text: 'Failed', cls: 'state-restricted' },
    CANCELLED: { text: 'Cancelled', cls: 'state-neutral' },
  };

  const PROVIDER_LABELS = {
    mock: 'Test mode — no live submissions',
    unconfigured: 'Not connected',
  };

  function clearChildren(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function el(tag, opts, children) {
    const node = document.createElement(tag);
    if (opts) {
      if (opts.className) node.className = opts.className;
      if (opts.id) node.id = opts.id;
      if (opts.text !== undefined) node.textContent = opts.text;
      if (opts.type) node.type = opts.type;
      if (opts.value !== undefined) node.value = opts.value;
      if (opts.placeholder) node.placeholder = opts.placeholder;
      if (opts.checked !== undefined) node.checked = opts.checked;
      if (opts.htmlFor) node.htmlFor = opts.htmlFor;
      if (opts.disabled) node.disabled = true;
    }
    (children || []).forEach((c) => node.appendChild(c));
    return node;
  }

  // ── Confirm dialog (replaces native confirm()) ──────────────────────────
  // Mirrors licensing.js's confirmDialog exactly (same rationale: no shared
  // SubsystemApp shell exists to plug into -- see the file header, and
  // RetailSystem._confirm in subsystem-retail.js for the full rationale
  // behind replacing native confirm() at all). Built with el()/textContent
  // only, matching this file's no-innerHTML policy. This page loads i18n.js
  // directly (see the file header), so opts route through t() like the rest
  // of this file.
  function confirmDialog(opts) {
    const o = opts || {};
    const danger = !!o.danger;
    const trigger = document.activeElement;

    const cancelBtn = el('button', { className: 'secondary', text: o.cancelLabel || t('Cancel') });
    const okBtn = el('button', { className: danger ? 'danger' : '', text: o.confirmLabel || t('Confirm') });
    const cardChildren = [el('h3', { text: o.title || '' })];
    if (o.message) cardChildren.push(el('p', { className: 'confirm-message', text: o.message }));
    cardChildren.push(el('div', { className: 'row' }, [cancelBtn, okBtn]));
    const card = el('div', { className: 'confirm-card' }, cardChildren);
    card.setAttribute('role', 'alertdialog');
    card.setAttribute('aria-modal', 'true');
    const overlay = el('div', { className: 'confirm-overlay' }, [card]);

    return new Promise((resolve) => {
      document.body.appendChild(overlay);

      let settled = false;
      const finish = (result) => {
        if (settled) return;   // Enter/click/overlay-click can race; resolve once only
        settled = true;
        document.removeEventListener('keydown', onKeydown, true);
        overlay.remove();
        if (trigger && typeof trigger.focus === 'function') trigger.focus();
        resolve(result);
      };

      const onKeydown = (e) => {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); return; }
        if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); finish(true); }
      };
      document.addEventListener('keydown', onKeydown, true);

      overlay.addEventListener('click', (e) => { if (e.target === overlay) finish(false); });
      cancelBtn.addEventListener('click', () => finish(false));
      okBtn.addEventListener('click', () => finish(true));
      okBtn.focus();
    });
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

  async function apiDelete(url) {
    const res = await fetch(url, { method: 'DELETE', credentials: 'include' });
    return { status: res.status, body: await res.json().catch(() => ({})) };
  }

  function stateBadge(label) {
    return el('span', { className: 'state-badge ' + label.cls, text: t(label.text) });
  }

  // ─── status card ────────────────────────────────────────────────────

  async function refreshStatus() {
    const { status, body } = await apiGet('/api/einvoicing/status');
    if (status !== 200) {
      clearChildren(statusContent);
      statusContent.appendChild(el('p', { text: t('Could not load status.') }));
      return;
    }
    renderStatus(body.data);
  }

  function renderStatus(data) {
    clearChildren(statusContent);
    const badge = data.enabled
      ? stateBadge({ text: 'Enabled', cls: 'state-active' })
      : stateBadge({ text: 'Disabled', cls: 'state-neutral' });
    statusContent.appendChild(badge);
    if (data.killswitch && data.killswitch.disabled) {
      statusContent.appendChild(stateBadge({ text: t('Paused:') + ' ' + data.killswitch.reason, cls: 'state-warning' }));
    }

    // data.provider_configured describes the live provider OBJECT
    // (routes.py), not the 'provider' setting below -- a shop can have
    // e-invoicing switched on (the new default in Jordan) while never
    // having registered on the JoFotara portal, which is exactly the case
    // this notice exists for: without it, the card would show "Enabled" +
    // a growing Queued count and nothing telling the shop owner why
    // nothing is clearing. Missing on an older backend (undefined) reads
    // as "configured" -- only an explicit false trips this.
    const notConnected = data.provider_configured === false;
    if (notConnected) {
      statusContent.appendChild(el('div', {
        className: 'state-warning',
        text: t('E-invoicing is on and every sale is being recorded, but nothing has been sent to the tax authority yet ' +
          'because this installation is not connected to JoFotara. To connect, register the business on the JoFotara ' +
          'portal and enter the Client-ID, Secret-Key and activity number it issues.'),
      }));
    }

    const dl = el('dl');
    dl.appendChild(el('dt', { text: t('Provider') }));
    // provider_in_use is the object actually running; fall back to the
    // older 'provider' setting key when a backend hasn't shipped the new
    // field yet (undefined/null), so this card renders correctly either way.
    const providerKey = data.provider_in_use != null ? data.provider_in_use : data.provider;
    dl.appendChild(el('dd', { text: t(PROVIDER_LABELS[providerKey] || providerKey) }));
    const counts = data.counts_by_state || {};
    dl.appendChild(el('dt', { text: t('Queued') }));
    dl.appendChild(el('dd', { text: String(counts.QUEUED || 0) }));
    dl.appendChild(el('dt', { text: t('Cleared') }));
    dl.appendChild(el('dd', { text: String(counts.CLEARED || 0) }));
    dl.appendChild(el('dt', { text: t('Needs attention') }));
    dl.appendChild(el('dd', {
      text: String((counts.FAILED_PERMANENT || 0) + (counts.SUBMITTING_UNKNOWN || 0) + (counts.AWAITING_CLEARANCE || 0)),
    }));
    statusContent.appendChild(dl);

    const row = el('div', { className: 'row' });
    const runBtn = el('button', { className: 'secondary', text: t('Submit queue now') });
    if (notConnected) {
      // A button that cannot work is how a shopkeeper concludes the
      // software itself is broken -- disable it and say why, right here,
      // rather than letting them press it and get a cryptic failure.
      runBtn.disabled = true;
      runBtn.title = t('Connect this installation to JoFotara (see the credentials below) before submitting the queue.');
    }
    runBtn.addEventListener('click', () => onRunOnceClicked(runBtn));
    row.appendChild(runBtn);

    if (data.killswitch && data.killswitch.disabled) {
      const resumeBtn = el('button', { text: t('Resume') });
      resumeBtn.addEventListener('click', () => onKillswitchClicked(false));
      row.appendChild(resumeBtn);
    } else {
      const pauseBtn = el('button', { className: 'danger', text: t('Pause immediately') });
      pauseBtn.addEventListener('click', () => onKillswitchClicked(true));
      row.appendChild(pauseBtn);
    }
    statusContent.appendChild(row);
  }

  async function onRunOnceClicked(btn) {
    clearMessage();
    btn.disabled = true;
    try {
      const { status, body } = await apiPost('/api/einvoicing/outbox/run-once', {});
      if (status === 200) {
        showMessage(t('Submission queue processed.'), 'info');
      } else {
        showMessage(body.message || t('Could not process the queue.'), 'error');
      }
    } catch (e) {
      showMessage(t('Network error while contacting this installation.'), 'error');
    }
    btn.disabled = false;
    await Promise.all([refreshStatus(), refreshOutbox()]);
  }

  async function onKillswitchClicked(pause) {
    clearMessage();
    try {
      if (pause) {
        const ok = await confirmDialog({
          title: t('Pause e-invoicing submission immediately?'),
          message: t('Existing queued invoices stay queued until resumed.'),
          confirmLabel: t('Pause'),
        });
        if (!ok) return;
        await apiPost('/api/einvoicing/killswitch', { reason: 'manual' });
        showMessage(t('E-invoicing paused.'), 'info');
      } else {
        await apiDelete('/api/einvoicing/killswitch');
        showMessage(t('E-invoicing resumed.'), 'info');
      }
    } catch (e) {
      showMessage(t('Network error while contacting this installation.'), 'error');
    }
    await refreshStatus();
  }

  // ─── settings card ──────────────────────────────────────────────────

  async function refreshSettings() {
    const { status, body } = await apiGet('/api/einvoicing/settings');
    if (status !== 200) {
      clearChildren(settingsContent);
      settingsContent.appendChild(el('p', { text: t('Could not load settings.') }));
      return;
    }
    renderSettings(body.data.settings);
  }

  function renderSettings(settings) {
    clearChildren(settingsContent);

    const toggleRow = el('div', { className: 'toggle-row' });
    const toggleLabel = el('label', { text: t('Enable Jordan e-invoicing for this business'), htmlFor: 'einv-enabled' });
    toggleLabel.style.margin = '0';
    const toggle = el('input', { id: 'einv-enabled', type: 'checkbox' });
    toggle.checked = settings.enabled === '1';
    toggleRow.appendChild(toggleLabel);
    toggleRow.appendChild(toggle);
    settingsContent.appendChild(toggleRow);
    settingsContent.appendChild(el('div', {
      className: 'hint', text: t('When off, nothing about your existing records or receipts changes.'),
    }));

    settingsContent.appendChild(el('label', { text: t('Invoice type'), htmlFor: 'einv-family' }));
    const familySelect = el('select', { id: 'einv-family' });
    [['income', 'Income invoice (not VAT-registered)'], ['general_sales', 'General sales invoice (VAT-registered)']]
      .forEach(([value, label]) => {
        const opt = el('option', { value: value, text: t(label) });
        if (settings.invoice_family === value) opt.selected = true;
        familySelect.appendChild(opt);
      });
    settingsContent.appendChild(familySelect);

    settingsContent.appendChild(el('label', { text: t('Business name (as registered with ISTD)'), htmlFor: 'einv-seller-name' }));
    settingsContent.appendChild(el('input', { id: 'einv-seller-name', type: 'text', value: settings.seller_name || '' }));

    settingsContent.appendChild(el('label', { text: t('Tax identification number (TIN)'), htmlFor: 'einv-seller-tin' }));
    settingsContent.appendChild(el('input', { id: 'einv-seller-tin', type: 'text', value: settings.seller_tin || '' }));

    settingsContent.appendChild(el('label', { text: t('Currency'), htmlFor: 'einv-currency' }));
    settingsContent.appendChild(el('input', { id: 'einv-currency', type: 'text', value: settings.currency || 'JOD' }));

    const row = el('div', { className: 'row' });
    const saveBtn = el('button', { text: t('Save settings') });
    saveBtn.addEventListener('click', () => onSaveSettingsClicked(saveBtn));
    row.appendChild(saveBtn);
    settingsContent.appendChild(row);
  }

  async function onSaveSettingsClicked(btn) {
    clearMessage();
    btn.disabled = true;
    const payload = {
      enabled: document.getElementById('einv-enabled').checked ? '1' : '0',
      invoice_family: document.getElementById('einv-family').value,
      seller_name: document.getElementById('einv-seller-name').value,
      seller_tin: document.getElementById('einv-seller-tin').value,
      currency: document.getElementById('einv-currency').value,
    };
    try {
      const { status, body } = await apiPost('/api/einvoicing/settings', payload);
      if (status === 200) {
        showMessage(t('Settings saved.'), 'info');
      } else {
        showMessage(body.message || t('Could not save settings.'), 'error');
      }
    } catch (e) {
      showMessage(t('Network error while contacting this installation.'), 'error');
    }
    btn.disabled = false;
    await Promise.all([refreshStatus(), refreshSettings()]);
  }

  // ─── credentials card ───────────────────────────────────────────────

  async function refreshCredentials() {
    const { status, body } = await apiGet('/api/einvoicing/settings');
    if (status !== 200) {
      clearChildren(credentialsContent);
      credentialsContent.appendChild(el('p', { text: t('Could not load credential status.') }));
      return;
    }
    renderCredentials(body.data.credentials);
  }

  function renderCredentials(creds) {
    clearChildren(credentialsContent);

    if (creds.configured) {
      const suffix = creds.client_id_last4 ? ' (' + t('ending') + ' ' + creds.client_id_last4 + ')' : '';
      credentialsContent.appendChild(el('p', { text: t('JoFotara credentials are configured') + suffix + '.' }));
      if (creds.readable === false) {
        credentialsContent.appendChild(el('div', {
          className: 'message message-error', text: t('Stored credentials could not be read. Please re-enter them below.'),
        }));
      }
      const wipeBtn = el('button', { className: 'danger', text: t('Remove credentials') });
      wipeBtn.addEventListener('click', onWipeCredentialsClicked);
      credentialsContent.appendChild(wipeBtn);
      credentialsContent.appendChild(el('div', { className: 'hint', text: t('Enter new credentials below to replace them.') }));
    } else {
      credentialsContent.appendChild(el('p', { text: t('No JoFotara credentials configured yet.') }));
    }

    credentialsContent.appendChild(el('label', { text: t('Client ID'), htmlFor: 'einv-client-id' }));
    credentialsContent.appendChild(el('input', { id: 'einv-client-id', type: 'text', placeholder: t('From the JoFotara portal') }));

    credentialsContent.appendChild(el('label', { text: t('Client secret'), htmlFor: 'einv-client-secret' }));
    credentialsContent.appendChild(el('input', { id: 'einv-client-secret', type: 'password', placeholder: t('From the JoFotara portal') }));

    const row = el('div', { className: 'row' });
    const saveBtn = el('button', { text: t('Save credentials') });
    saveBtn.addEventListener('click', () => onSaveCredentialsClicked(saveBtn));
    row.appendChild(saveBtn);
    credentialsContent.appendChild(row);
  }

  async function onSaveCredentialsClicked(btn) {
    clearMessage();
    const clientId = document.getElementById('einv-client-id').value.trim();
    const clientSecret = document.getElementById('einv-client-secret').value.trim();
    if (!clientId || !clientSecret) {
      showMessage(t('Enter both the client ID and client secret.'), 'error');
      return;
    }
    btn.disabled = true;
    try {
      const { status, body } = await apiPost('/api/einvoicing/credentials', { client_id: clientId, client_secret: clientSecret });
      document.getElementById('einv-client-secret').value = '';
      if (status === 200) {
        showMessage(t('Credentials saved.'), 'info');
      } else {
        showMessage(body.message || t('Could not save credentials.'), 'error');
      }
    } catch (e) {
      showMessage(t('Network error while contacting this installation.'), 'error');
    }
    btn.disabled = false;
    await refreshCredentials();
  }

  async function onWipeCredentialsClicked() {
    const ok = await confirmDialog({
      title: t('Remove the stored JoFotara credentials from this installation?'),
      confirmLabel: t('Remove'),
      danger: true,
    });
    if (!ok) return;
    clearMessage();
    try {
      const { status, body } = await apiDelete('/api/einvoicing/credentials');
      if (status === 200) {
        showMessage(t('Credentials removed.'), 'info');
      } else {
        showMessage(body.message || t('Could not remove credentials.'), 'error');
      }
    } catch (e) {
      showMessage(t('Network error while contacting this installation.'), 'error');
    }
    await refreshCredentials();
  }

  // ─── outbox card ────────────────────────────────────────────────────

  async function refreshOutbox() {
    const { status, body } = await apiGet('/api/einvoicing/outbox?limit=25');
    if (status !== 200) {
      clearChildren(outboxContent);
      outboxContent.appendChild(el('p', { text: t('Could not load the submission queue.') }));
      return;
    }
    renderOutbox(body.data);
  }

  function renderOutbox(rows) {
    clearChildren(outboxContent);
    if (!rows.length) {
      outboxContent.appendChild(el('p', { text: t('No invoices submitted yet.') }));
      return;
    }

    const table = el('table');
    const thead = el('thead');
    const headRow = el('tr');
    [t('Invoice #'), t('E-invoice #'), t('Status'), ''].forEach((h) => headRow.appendChild(el('th', { text: h })));
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = el('tbody');
    rows.forEach((row) => {
      const tr = el('tr');
      tr.appendChild(el('td', { text: row.local_document_no || '—' }));
      tr.appendChild(el('td', { text: row.einvoice_no }));
      const label = OUTBOX_STATE_LABELS[row.status] || { text: row.status, cls: 'state-neutral' };
      tr.appendChild(el('td', {}, [stateBadge(label)]));

      const actionsCell = el('td');
      if (row.status === 'FAILED_PERMANENT') {
        const retryBtn = el('button', { className: 'small secondary', text: t('Retry') });
        retryBtn.addEventListener('click', () => onRetryClicked(row.invoice_ref, retryBtn));
        actionsCell.appendChild(retryBtn);
      }
      tr.appendChild(actionsCell);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    outboxContent.appendChild(table);
  }

  async function onRetryClicked(invoiceRef, btn) {
    clearMessage();
    btn.disabled = true;
    try {
      const { status, body } = await apiPost('/api/einvoicing/outbox/' + encodeURIComponent(invoiceRef) + '/retry', {});
      if (status === 200) {
        showMessage(t('Queued for retry.'), 'info');
      } else {
        showMessage(body.message || t('Could not retry this entry.'), 'error');
      }
    } catch (e) {
      showMessage(t('Network error while contacting this installation.'), 'error');
    }
    await Promise.all([refreshStatus(), refreshOutbox()]);
  }

  // ─── boot ───────────────────────────────────────────────────────────

  // Gate the first render on the translation dictionaries the same way
  // index.html gates SubsystemApp.init() -- see i18n.js's docstring/apply().
  // Falls back to an immediate render when AuraI18n isn't present (see the
  // t() fallback above for why that has to be possible).
  function bootRefresh() {
    Promise.all([refreshStatus(), refreshSettings(), refreshCredentials(), refreshOutbox()]);
  }
  if (typeof window !== 'undefined' && window.AuraI18n) {
    window.AuraI18n.load().then(bootRefresh);
  } else {
    bootRefresh();
  }
})();
