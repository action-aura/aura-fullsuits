/**
 * Aura Retail -- WhatsApp reports UI (whatsapp-recipients feature).
 *
 * Self-contained, mirroring products/retail/frontend/einvoicing.js exactly
 * (same rationale: no shared SubsystemApp shell exists to plug into -- see
 * that file's docstring). Talks only to /api/notifications/whatsapp/* and,
 * for on-demand sends, /api/sub/retail/reports/whatsapp and
 * /api/sub/retail/branches (recipient branch picker).
 *
 * DOM is built with createElement/textContent throughout -- no
 * innerHTML/outerHTML assignment anywhere in this file, same policy as
 * einvoicing.js/licensing.js, so there is no XSS-sink pattern to audit.
 */
(function () {
  'use strict';

  const messageArea = document.getElementById('message-area');
  const statusContent = document.getElementById('status-content');
  const templatesContent = document.getElementById('templates-content');
  const recipientsContent = document.getElementById('recipients-content');
  const queueContent = document.getElementById('queue-content');

  const REPORT_TYPES = [
    { key: 'daily_sales_summary', label: 'Daily sales summary', nameKey: 'daily_sales_template_name', bodyKey: 'daily_sales_template_body', namePlaceholder: 'aura_daily_sales' },
    { key: 'shift_close_report', label: 'Shift close report', nameKey: 'shift_close_template_name', bodyKey: 'shift_close_template_body', namePlaceholder: 'aura_shift_close' },
    { key: 'low_stock_alert', label: 'Low stock alert', nameKey: 'low_stock_template_name', bodyKey: 'low_stock_template_body', namePlaceholder: 'aura_low_stock' },
    { key: 'ar_overdue_alert', label: 'Receivables overdue alert', nameKey: 'ar_overdue_template_name', bodyKey: 'ar_overdue_template_body', namePlaceholder: 'aura_ar_overdue' },
  ];

  const OUTBOX_STATE_LABELS = {
    QUEUED: { text: 'Queued', cls: 'state-neutral' },
    SENDING: { text: 'Sending', cls: 'state-neutral' },
    SENT: { text: 'Sent', cls: 'state-active' },
    FAILED_PERMANENT: { text: 'Failed', cls: 'state-restricted' },
    CANCELLED: { text: 'Cancelled', cls: 'state-neutral' },
  };

  let editingRecipientId = null;
  let branchOptions = []; // [{id, name}]

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
      if (opts.rows) node.rows = opts.rows;
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

  async function apiPut(url, body) {
    const res = await fetch(url, {
      method: 'PUT',
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
    return el('span', { className: 'state-badge ' + label.cls, text: label.text });
  }

  // ─── status card ────────────────────────────────────────────────────

  async function refreshStatus() {
    const { status, body } = await apiGet('/api/notifications/whatsapp/status');
    if (status !== 200) {
      clearChildren(statusContent);
      statusContent.appendChild(el('p', { text: 'Could not load status.' }));
      return;
    }
    renderStatus(body.data);
  }

  function renderStatus(data) {
    clearChildren(statusContent);

    if (!data.whatsapp_configured) {
      statusContent.appendChild(stateBadge({ text: 'Transport not configured', cls: 'state-warning' }));
      statusContent.appendChild(el('p', {
        className: 'hint',
        text: 'AURA_WHATSAPP_PHONE_NUMBER_ID / AURA_WHATSAPP_ACCESS_TOKEN are not set on this installation. Nothing can send until the installer sets them.',
      }));
    } else {
      statusContent.appendChild(data.enabled
        ? stateBadge({ text: 'Enabled', cls: 'state-active' })
        : stateBadge({ text: 'Disabled', cls: 'state-neutral' }));
    }

    const dl = el('dl');
    dl.appendChild(el('dt', { text: 'Recipients configured' }));
    dl.appendChild(el('dd', { text: String(data.recipient_count || 0) }));
    const counts = data.counts_by_state || {};
    dl.appendChild(el('dt', { text: 'Queued' }));
    dl.appendChild(el('dd', { text: String(counts.QUEUED || 0) }));
    dl.appendChild(el('dt', { text: 'Sent' }));
    dl.appendChild(el('dd', { text: String(counts.SENT || 0) }));
    dl.appendChild(el('dt', { text: 'Needs attention' }));
    dl.appendChild(el('dd', { text: String(counts.FAILED_PERMANENT || 0) }));
    statusContent.appendChild(dl);

    const toggleRow = el('div', { className: 'toggle-row' });
    toggleRow.style.marginTop = '14px';
    const toggleLabel = el('label', { text: 'Enable WhatsApp reports for this business', htmlFor: 'wa-enabled' });
    toggleLabel.style.margin = '0';
    const toggle = el('input', { id: 'wa-enabled', type: 'checkbox' });
    toggle.checked = data.enabled === true;
    toggle.disabled = !data.whatsapp_configured;
    toggle.addEventListener('change', () => onToggleEnabledChanged(toggle));
    toggleRow.appendChild(toggleLabel);
    toggleRow.appendChild(toggle);
    statusContent.appendChild(toggleRow);
  }

  async function onToggleEnabledChanged(toggle) {
    clearMessage();
    toggle.disabled = true;
    try {
      const { status, body } = await apiPost('/api/notifications/whatsapp/settings', { enabled: toggle.checked ? '1' : '0' });
      if (status === 200) {
        showMessage(toggle.checked ? 'WhatsApp reports enabled.' : 'WhatsApp reports disabled.', 'info');
      } else {
        showMessage(body.message || 'Could not save this setting.', 'error');
      }
    } catch (e) {
      showMessage('Network error while contacting this installation.', 'error');
    }
    await refreshStatus();
  }

  // ─── templates card ─────────────────────────────────────────────────

  async function refreshTemplates() {
    const { status, body } = await apiGet('/api/notifications/whatsapp/settings');
    if (status !== 200) {
      clearChildren(templatesContent);
      templatesContent.appendChild(el('p', { text: 'Could not load templates.' }));
      return;
    }
    renderTemplates(body.data.settings);
  }

  function renderTemplates(settings) {
    clearChildren(templatesContent);

    templatesContent.appendChild(el('label', { text: 'Default language code', htmlFor: 'wa-default-lang' }));
    const langSelect = el('select', { id: 'wa-default-lang' });
    [['en_US', 'English (en_US)'], ['ar', 'Arabic (ar)']].forEach(([value, label]) => {
      const opt = el('option', { value: value, text: label });
      if (settings.default_language_code === value) opt.selected = true;
      langSelect.appendChild(opt);
    });
    templatesContent.appendChild(langSelect);

    REPORT_TYPES.forEach((rt) => {
      const block = el('div', { className: 'template-block' });
      block.appendChild(el('label', { text: rt.label + ' -- template name', htmlFor: 'wa-name-' + rt.key }));
      block.appendChild(el('input', { id: 'wa-name-' + rt.key, type: 'text', value: settings[rt.nameKey] || '', placeholder: 'e.g. ' + rt.namePlaceholder }));
      block.appendChild(el('label', { text: 'Reference text (not sent -- see note above)', htmlFor: 'wa-body-' + rt.key }));
      block.appendChild(el('textarea', { id: 'wa-body-' + rt.key, rows: 2, value: settings[rt.bodyKey] || '' }));
      templatesContent.appendChild(block);
    });

    const row = el('div', { className: 'row' });
    const saveBtn = el('button', { text: 'Save templates' });
    saveBtn.addEventListener('click', () => onSaveTemplatesClicked(saveBtn));
    row.appendChild(saveBtn);
    templatesContent.appendChild(row);
  }

  async function onSaveTemplatesClicked(btn) {
    clearMessage();
    btn.disabled = true;
    const payload = { default_language_code: document.getElementById('wa-default-lang').value };
    REPORT_TYPES.forEach((rt) => {
      payload[rt.nameKey] = document.getElementById('wa-name-' + rt.key).value.trim();
      payload[rt.bodyKey] = document.getElementById('wa-body-' + rt.key).value;
    });
    try {
      const { status, body } = await apiPost('/api/notifications/whatsapp/settings', payload);
      if (status === 200) {
        showMessage('Templates saved.', 'info');
      } else {
        showMessage(body.message || 'Could not save templates.', 'error');
      }
    } catch (e) {
      showMessage('Network error while contacting this installation.', 'error');
    }
    btn.disabled = false;
    await refreshTemplates();
  }

  // ─── recipients card ────────────────────────────────────────────────

  async function refreshBranches() {
    try {
      const { status, body } = await apiGet('/api/sub/retail/branches');
      branchOptions = (status === 200 && body.data) ? body.data : [];
    } catch (e) {
      branchOptions = [];
    }
  }

  async function refreshRecipients() {
    const { status, body } = await apiGet('/api/notifications/whatsapp/recipients');
    if (status !== 200) {
      clearChildren(recipientsContent);
      recipientsContent.appendChild(el('p', { text: 'Could not load recipients.' }));
      return;
    }
    renderRecipients(body.data);
  }

  function branchName(branchId) {
    if (branchId === null || branchId === undefined) return 'All branches';
    const b = branchOptions.find((x) => String(x.id) === String(branchId));
    return b ? b.name : ('Branch ' + branchId);
  }

  function renderRecipients(rows) {
    clearChildren(recipientsContent);

    if (!rows.length) {
      recipientsContent.appendChild(el('p', { text: 'No recipients configured yet.' }));
    } else {
      const table = el('table');
      const thead = el('thead');
      const headRow = el('tr');
      ['Name', 'Phone', 'Role', 'Branch', 'Reports', 'Active', ''].forEach((h) => headRow.appendChild(el('th', { text: h })));
      thead.appendChild(headRow);
      table.appendChild(thead);

      const tbody = el('tbody');
      rows.forEach((r) => {
        const tr = el('tr');
        tr.appendChild(el('td', { text: r.display_name }));
        tr.appendChild(el('td', { text: r.phone_e164 }));
        tr.appendChild(el('td', { text: r.role_label || '—' }));
        tr.appendChild(el('td', { text: branchName(r.branch_id) }));
        tr.appendChild(el('td', { text: r.report_types.join(', ') || '—' }));
        tr.appendChild(el('td', { text: r.status === 'active' ? 'Yes' : 'No' }));

        const actionsCell = el('td');
        const editBtn = el('button', { className: 'small secondary', text: 'Edit' });
        editBtn.addEventListener('click', () => onEditRecipientClicked(r));
        actionsCell.appendChild(editBtn);
        const removeBtn = el('button', { className: 'small danger', text: 'Remove' });
        removeBtn.style.marginLeft = '6px';
        removeBtn.addEventListener('click', () => onRemoveRecipientClicked(r.id));
        actionsCell.appendChild(removeBtn);
        tr.appendChild(actionsCell);

        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      recipientsContent.appendChild(table);
    }

    recipientsContent.appendChild(buildRecipientForm());
  }

  function buildRecipientForm(prefill) {
    const form = el('div', { className: 'template-block' });
    form.appendChild(el('label', { text: editingRecipientId ? 'Edit recipient' : 'Add a recipient' }));

    form.appendChild(el('label', { text: 'Name', htmlFor: 'wa-r-name' }));
    form.appendChild(el('input', { id: 'wa-r-name', type: 'text', value: (prefill && prefill.display_name) || '', placeholder: 'e.g. Owner, Downtown Manager' }));

    form.appendChild(el('label', { text: 'Phone (with country code, e.g. +15551234567)', htmlFor: 'wa-r-phone' }));
    form.appendChild(el('input', { id: 'wa-r-phone', type: 'tel', value: (prefill && prefill.phone_e164) || '' }));

    form.appendChild(el('label', { text: 'Role (label only)', htmlFor: 'wa-r-role' }));
    form.appendChild(el('input', { id: 'wa-r-role', type: 'text', value: (prefill && prefill.role_label) || '', placeholder: 'e.g. Owner, Manager, Accountant' }));

    form.appendChild(el('label', { text: 'Branch', htmlFor: 'wa-r-branch' }));
    const branchSelect = el('select', { id: 'wa-r-branch' });
    const allOpt = el('option', { value: '', text: 'All branches' });
    branchSelect.appendChild(allOpt);
    branchOptions.forEach((b) => {
      const opt = el('option', { value: String(b.id), text: b.name });
      if (prefill && String(prefill.branch_id) === String(b.id)) opt.selected = true;
      branchSelect.appendChild(opt);
    });
    form.appendChild(branchSelect);

    form.appendChild(el('label', { text: 'Language', htmlFor: 'wa-r-lang' }));
    const langSelect = el('select', { id: 'wa-r-lang' });
    [['en_US', 'English'], ['ar', 'Arabic']].forEach(([value, label]) => {
      const opt = el('option', { value: value, text: label });
      if (prefill && prefill.language_code === value) opt.selected = true;
      langSelect.appendChild(opt);
    });
    form.appendChild(langSelect);

    form.appendChild(el('label', { text: 'Reports this recipient gets' }));
    const prefillTypes = (prefill && prefill.report_types) || [];
    REPORT_TYPES.forEach((rt) => {
      const rowEl = el('div', { className: 'checkbox-row' });
      const cb = el('input', { id: 'wa-r-type-' + rt.key, type: 'checkbox' });
      cb.checked = prefillTypes.indexOf(rt.key) !== -1;
      const cbLabel = el('label', { text: rt.label, htmlFor: 'wa-r-type-' + rt.key });
      rowEl.appendChild(cb);
      rowEl.appendChild(cbLabel);
      form.appendChild(rowEl);
    });

    const row = el('div', { className: 'row' });
    const saveBtn = el('button', { text: editingRecipientId ? 'Save changes' : 'Add recipient' });
    saveBtn.addEventListener('click', () => onSaveRecipientClicked(saveBtn));
    row.appendChild(saveBtn);
    if (editingRecipientId) {
      const cancelBtn = el('button', { className: 'secondary', text: 'Cancel' });
      cancelBtn.addEventListener('click', () => { editingRecipientId = null; refreshRecipients(); });
      row.appendChild(cancelBtn);
    }
    form.appendChild(row);
    return form;
  }

  function onEditRecipientClicked(recipient) {
    editingRecipientId = recipient.id;
    clearChildren(recipientsContent);
    recipientsContent.appendChild(buildRecipientForm(recipient));
    recipientsContent.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  async function onSaveRecipientClicked(btn) {
    clearMessage();
    const displayName = document.getElementById('wa-r-name').value.trim();
    const phone = document.getElementById('wa-r-phone').value.trim();
    if (!displayName || !phone) {
      showMessage('Enter both a name and a phone number.', 'error');
      return;
    }
    const branchVal = document.getElementById('wa-r-branch').value;
    const reportTypes = REPORT_TYPES
      .filter((rt) => document.getElementById('wa-r-type-' + rt.key).checked)
      .map((rt) => rt.key);
    const payload = {
      display_name: displayName,
      phone_e164: phone,
      role_label: document.getElementById('wa-r-role').value.trim() || null,
      branch_id: branchVal ? Number(branchVal) : null,
      language_code: document.getElementById('wa-r-lang').value,
      report_types: reportTypes,
    };

    btn.disabled = true;
    try {
      const { status, body } = editingRecipientId
        ? await apiPut('/api/notifications/whatsapp/recipients/' + encodeURIComponent(editingRecipientId), payload)
        : await apiPost('/api/notifications/whatsapp/recipients', payload);
      if (status === 200) {
        showMessage(editingRecipientId ? 'Recipient updated.' : 'Recipient added.', 'info');
        editingRecipientId = null;
      } else {
        showMessage(body.message || 'Could not save this recipient.', 'error');
      }
    } catch (e) {
      showMessage('Network error while contacting this installation.', 'error');
    }
    btn.disabled = false;
    await Promise.all([refreshRecipients(), refreshStatus()]);
  }

  async function onRemoveRecipientClicked(recipientId) {
    if (!window.confirm('Remove this recipient? They will stop receiving any WhatsApp reports.')) return;
    clearMessage();
    try {
      const { status, body } = await apiDelete('/api/notifications/whatsapp/recipients/' + encodeURIComponent(recipientId));
      if (status === 200) {
        showMessage('Recipient removed.', 'info');
      } else {
        showMessage(body.message || 'Could not remove this recipient.', 'error');
      }
    } catch (e) {
      showMessage('Network error while contacting this installation.', 'error');
    }
    await Promise.all([refreshRecipients(), refreshStatus()]);
  }

  // ─── queue card ─────────────────────────────────────────────────────

  async function refreshQueue() {
    const { status, body } = await apiGet('/api/notifications/whatsapp/outbox?limit=20');
    if (status !== 200) {
      clearChildren(queueContent);
      queueContent.appendChild(el('p', { text: 'Could not load the queue.' }));
      return;
    }
    renderQueue(body.data);
  }

  function renderQueue(rows) {
    clearChildren(queueContent);

    if (rows.length) {
      const table = el('table');
      const thead = el('thead');
      const headRow = el('tr');
      ['Type', 'Phone', 'Template', 'Status', 'Attempts', ''].forEach((h) => headRow.appendChild(el('th', { text: h })));
      thead.appendChild(headRow);
      table.appendChild(thead);

      const tbody = el('tbody');
      rows.forEach((row) => {
        const tr = el('tr');
        tr.appendChild(el('td', { text: row.message_type }));
        tr.appendChild(el('td', { text: row.recipient_phone_e164 }));
        tr.appendChild(el('td', { text: row.template_name }));
        const label = OUTBOX_STATE_LABELS[row.status] || { text: row.status, cls: 'state-neutral' };
        const statusCell = el('td');
        statusCell.appendChild(stateBadge(label));
        if (row.last_error) statusCell.appendChild(el('div', { className: 'hint', text: row.last_error }));
        tr.appendChild(statusCell);
        tr.appendChild(el('td', { text: String(row.attempt_count) }));
        tr.appendChild(el('td'));
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      queueContent.appendChild(table);
    } else {
      queueContent.appendChild(el('p', { text: 'Nothing queued yet.' }));
    }

    const row = el('div', { className: 'row' });
    const runBtn = el('button', { className: 'secondary', text: 'Process queue now' });
    runBtn.addEventListener('click', () => onRunOnceClicked(runBtn));
    row.appendChild(runBtn);

    const dailyBtn = el('button', { className: 'secondary', text: 'Send daily summary now' });
    dailyBtn.addEventListener('click', () => onSendNowClicked(dailyBtn, 'daily_sales_summary'));
    row.appendChild(dailyBtn);

    const arBtn = el('button', { className: 'secondary', text: 'Send AR overdue alert now' });
    arBtn.addEventListener('click', () => onSendNowClicked(arBtn, 'ar_overdue_alert'));
    row.appendChild(arBtn);

    queueContent.appendChild(row);
  }

  async function onRunOnceClicked(btn) {
    clearMessage();
    btn.disabled = true;
    try {
      const { status, body } = await apiPost('/api/notifications/whatsapp/outbox/run-once', {});
      if (status === 200) {
        showMessage('Queue processed.', 'info');
      } else {
        showMessage(body.message || 'Could not process the queue.', 'error');
      }
    } catch (e) {
      showMessage('Network error while contacting this installation.', 'error');
    }
    btn.disabled = false;
    await Promise.all([refreshStatus(), refreshQueue()]);
  }

  async function onSendNowClicked(btn, reportType) {
    clearMessage();
    btn.disabled = true;
    try {
      const { status, body } = await apiPost('/api/sub/retail/reports/whatsapp', { report_type: reportType });
      if (status === 200) {
        showMessage('Queued for ' + body.data.queued + ' recipient(s).', 'info');
      } else {
        showMessage(body.message || 'Could not queue this report.', 'error');
      }
    } catch (e) {
      showMessage('Network error while contacting this installation.', 'error');
    }
    btn.disabled = false;
    await Promise.all([refreshStatus(), refreshQueue()]);
  }

  // ─── boot ───────────────────────────────────────────────────────────

  (async function boot() {
    await refreshBranches();
    await Promise.all([refreshStatus(), refreshTemplates(), refreshRecipients(), refreshQueue()]);
  })();
})();
