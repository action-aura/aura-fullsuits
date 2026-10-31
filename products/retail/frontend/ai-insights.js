/* Smart analysis -- AI-BRAIN (docs/superpowers/specs/2026-10-31-ai-brain-design.md).
 *
 * window.RetailAiInsights.render(container) paints the Smart analysis screen: an AI-written report on
 * the shop's last 30 days (headline, performance, trends, customers, suggestions, deep dive,
 * warnings). The till builds the figures (never a customer name), Owner holds the only AI key.
 *
 * DATA. GET  /api/sub/retail/ai/status            {configured, enabled, remote}   (retail.reports)
 *       GET  /api/sub/retail/ai/insights/latest   the cached report or {report:null}
 *       POST /api/sub/retail/ai/insights          generate + cache; 409 AI_NOT_ENABLED until the owner opts in
 * The opt-in itself lives in Settings (POST /settings/credit, owner-only); this screen only explains
 * it and points at it -- it never switches anything on.
 *
 * AUTOMATION. Opening the screen with the opt-in on and a cached report missing or older than 24 h
 * generates a fresh one, ONCE per open (`state.openId` guards a re-render racing the first).
 *
 * THE REPORT IS MODEL OUTPUT. Every piece of it goes in through textContent, never innerHTML: a
 * product name the model echoed back must not be able to become markup.
 *
 * MOUNT. app-shell.js's _safeRender calls RetailAiInsights.render(#sub-content) for the 'ai-insights'
 * nav row. Needs <script src="/static/ai-insights.js"></script> in index.html.
 *
 * STYLE. Vanilla, no build step. Logical CSS properties only (reads correctly under body.rtl), colours
 * are theme tokens, every string goes through t() so the Arabic lives in the catalogue.
 */
(function () {
  'use strict';

  var BASE = '/api/sub/retail/ai';
  var STALE_MS = 24 * 60 * 60 * 1000;
  var state = { openId: 0, busy: false };

  function t(s) {
    try { if (typeof window.t === 'function') return window.t(s); } catch (e) { /* identity */ }
    return s;
  }
  function lang() {
    return (window.AuraI18n && window.AuraI18n.current) || 'en';
  }
  async function call(method, url, body) {
    var opts = { method: method, credentials: 'include', cache: 'no-store' };
    if (body !== undefined) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    var res = await fetch(url, opts);
    var json = await res.json().catch(function () { return {}; });
    return { ok: res.ok, status: res.status, body: json || {} };
  }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = String(text);
    return n;
  }
  function isAdminDevice() {
    try { return !!(window.SubsystemApp && window.SubsystemApp.isAdminDevice); } catch (e) { return false; }
  }

  function injectStyles() {
    if (document.getElementById('aii-style')) return;
    var st = document.createElement('style');
    st.id = 'aii-style';
    st.textContent = [
      '.aii-lead { color:var(--text-secondary); font-size:var(--text-size-meta); max-inline-size:66ch; margin-block:0 var(--space-base); }',
      '.aii-bar { display:flex; flex-wrap:wrap; gap:var(--space-snug); align-items:center; margin-block-end:var(--space-base); }',
      '.aii-when { color:var(--text-tertiary); font-size:var(--text-size-meta); margin-inline-start:auto; }',
      '.aii-note { background:var(--surface-sunken); border:1px solid var(--border-hairline); border-radius:var(--radius-card);',
      '  padding:var(--space-comfy); max-inline-size:66ch; color:var(--text-primary); }',
      '.aii-note p { margin-block:0 var(--space-snug); }',
      '.aii-headline { font-size:var(--text-size-subhead); font-weight:var(--text-weight-semibold); color:var(--text-primary);',
      '  background:var(--surface-raised); border:1px solid var(--border-hairline); border-radius:var(--radius-card);',
      '  padding:var(--space-comfy); margin-block-end:var(--space-base); }',
      '.aii-section { margin-block-end:var(--space-base); }',
      '.aii-section h3 { font-size:var(--text-size-body-lg); color:var(--text-primary); margin-block:0 var(--space-tight); }',
      '.aii-section ul { margin:0; padding-inline-start:1.2em; color:var(--text-primary); line-height:var(--text-leading-body); }',
      '.aii-cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:var(--space-snug); }',
      '.aii-card { background:var(--surface-raised); border:1px solid var(--border-hairline); border-radius:var(--radius-card);',
      '  box-shadow:var(--elevation-card); padding:var(--space-comfy); }',
      '.aii-card h4 { margin-block:0 var(--space-tight); font-size:var(--text-size-body); color:var(--text-primary); }',
      '.aii-card p { margin-block:0 var(--space-tight); font-size:var(--text-size-meta); color:var(--text-secondary); }',
      '.aii-card b { color:var(--text-primary); }',
      '.aii-warn { background:var(--state-warning-surface); color:var(--state-warning-text); border-radius:var(--radius-card);',
      '  padding:var(--space-comfy); }',
      '.aii-warn ul { color:var(--state-warning-text); }',
      '.aii-err { background:var(--state-danger-surface); color:var(--state-danger-text); border:1px solid var(--state-danger-border);',
      '  border-radius:var(--radius-card); padding:var(--space-snug) var(--space-comfy); margin-block-end:var(--space-base); }',
      '.aii-empty { text-align:center; color:var(--text-tertiary); padding:var(--space-roomy) var(--space-base); }',
    ].join('\n');
    document.head.appendChild(st);
  }

  // A list section (heading + bullets). An empty list renders nothing at all.
  function listSection(title, items, cls) {
    if (!Array.isArray(items) || !items.length) return null;
    var wrap = el('section', 'aii-section' + (cls ? ' ' + cls : ''));
    wrap.appendChild(el('h3', null, t(title)));
    var ul = el('ul');
    items.forEach(function (it) { ul.appendChild(el('li', null, it)); });
    wrap.appendChild(ul);
    return wrap;
  }

  function suggestionSection(items) {
    if (!Array.isArray(items) || !items.length) return null;
    var wrap = el('section', 'aii-section');
    wrap.appendChild(el('h3', null, t('Suggestions')));
    var grid = el('div', 'aii-cards');
    items.forEach(function (s) {
      if (!s || typeof s !== 'object') return;
      var card = el('div', 'aii-card');
      card.appendChild(el('h4', null, s.title));
      var why = el('p');
      why.appendChild(el('b', null, t('Why') + ': '));
      why.appendChild(document.createTextNode(String(s.why == null ? '' : s.why)));
      card.appendChild(why);
      var act = el('p');
      act.appendChild(el('b', null, t('Do this') + ': '));
      act.appendChild(document.createTextNode(String(s.action == null ? '' : s.action)));
      card.appendChild(act);
      grid.appendChild(card);
    });
    wrap.appendChild(grid);
    return wrap;
  }

  function paintReport(host, payload) {
    host.textContent = '';
    var report = payload && payload.report;
    if (!report || typeof report !== 'object') {
      host.appendChild(el('div', 'aii-empty', t('No analysis yet. Press "Analyse now".')));
      return;
    }
    if (report.headline) host.appendChild(el('div', 'aii-headline', report.headline));
    [
      listSection('Performance', report.performance),
      listSection('Trends', report.trends),
      listSection('Customers', report.customers),
      suggestionSection(report.suggestions),
      listSection('Deep dive', report.deep_dive),
      listSection('Warnings', report.warnings, 'aii-warn'),
    ].forEach(function (s) { if (s) host.appendChild(s); });
  }

  function paintWhen(payload) {
    var w = document.getElementById('aii-when');
    if (!w) return;
    w.textContent = '';
    if (!payload || !payload.generated_at) return;
    var d = new Date(payload.generated_at);
    if (isNaN(d.getTime())) return;
    w.textContent = t('Last analysed') + ' ' + d.toLocaleString(lang() === 'ar' ? 'ar' : 'en-GB');
  }

  function showError(msg) {
    var box = document.getElementById('aii-error');
    if (!box) return;
    box.textContent = msg || '';
    box.hidden = !msg;
  }

  function isStale(payload) {
    if (!payload || !payload.report || !payload.generated_at) return true;
    var ts = new Date(payload.generated_at).getTime();
    if (isNaN(ts)) return true;
    return (Date.now() - ts) > STALE_MS;
  }

  // Generate (POST), then repaint. `openId` ties the result to the screen instance that asked, so a
  // report arriving after the user navigated away never paints into someone else's container.
  async function analyse(openId) {
    if (state.busy) return;
    state.busy = true;
    var btn = document.getElementById('aii-go');
    if (btn) { btn.disabled = true; btn.textContent = t('Analysing…'); }
    showError('');
    try {
      var r = await call('POST', BASE + '/insights', { lang: lang() });
      if (openId !== state.openId) return;
      if (r.ok && r.body && r.body.success && r.body.data) {
        var host = document.getElementById('aii-report');
        if (host) paintReport(host, r.body.data);
        paintWhen(r.body.data);
      } else {
        showError((r.body && r.body.error) || t('Could not write the analysis.'));
      }
    } catch (e) {
      if (openId === state.openId) showError(t('Could not write the analysis.'));
    } finally {
      state.busy = false;
      var b2 = document.getElementById('aii-go');
      if (b2) { b2.disabled = false; b2.textContent = t('Analyse now'); }
    }
  }

  function paintNote(c, lines, withSettingsButton) {
    var note = el('div', 'aii-note');
    lines.forEach(function (l) { note.appendChild(el('p', null, l)); });
    if (withSettingsButton) {
      var b = el('button', 'ret-btn ret-btn-primary', t('Turn on in Settings'));
      b.type = 'button';
      b.addEventListener('click', function () {
        try { if (window.SubsystemApp && window.SubsystemApp._navigate) window.SubsystemApp._navigate('admin-center'); }
        catch (e) { /* the gear is also in the sidebar footer */ }
      });
      note.appendChild(b);
    }
    c.appendChild(note);
  }

  async function render(c) {
    if (!c) return;
    injectStyles();
    var openId = ++state.openId;
    c.textContent = '';

    var hdr = el('div', 'ret-hdr');
    var h2 = el('h2', 'ret-title');
    h2.appendChild(el('span', null, t('Smart analysis')));
    hdr.appendChild(h2);
    c.appendChild(hdr);
    c.appendChild(el('p', 'aii-lead', t('Smart analysis reads your last 30 days of sales and writes a short report: how you are doing, what is changing, and what to do this week.')));

    var status = null;
    try {
      var s = await call('GET', BASE + '/status');
      if (s.ok) status = s.body;
    } catch (e) { /* treated as unavailable below */ }
    if (openId !== state.openId) return;

    if (!status || status.remote !== true) {
      paintNote(c, [t('Smart analysis is not available on this device.')], false);
      return;
    }
    if (status.enabled !== true) {
      var lines = [
        t("Your shop's sales figures (never customer names) are sent to our server and an AI service to write your analysis."),
        t('It is off. The shop owner can turn it on in Settings.'),
      ];
      paintNote(c, lines, isAdminDevice());
      return;
    }

    var bar = el('div', 'aii-bar');
    var go = el('button', 'ret-btn ret-btn-primary', t('Analyse now'));
    go.id = 'aii-go';
    go.type = 'button';
    go.addEventListener('click', function () { analyse(state.openId); });
    var ask = el('button', 'ret-btn ret-btn-ghost', t('Ask a question'));
    ask.type = 'button';
    ask.addEventListener('click', function () {
      try { if (window.SubAI && typeof window.SubAI.open === 'function') window.SubAI.open('retail'); }
      catch (e) { /* the sidebar assistant button still works */ }
    });
    var when = el('span', 'aii-when');
    when.id = 'aii-when';
    bar.appendChild(go);
    bar.appendChild(ask);
    bar.appendChild(when);
    c.appendChild(bar);

    var err = el('div', 'aii-err');
    err.id = 'aii-error';
    err.setAttribute('role', 'alert');
    err.hidden = true;
    c.appendChild(err);

    var host = el('div');
    host.id = 'aii-report';
    c.appendChild(host);

    var cached = null;
    try {
      var l = await call('GET', BASE + '/insights/latest');
      if (l.ok && l.body && l.body.data) cached = l.body.data;
    } catch (e) { /* no cache => generate below */ }
    if (openId !== state.openId) return;
    paintReport(host, cached);
    paintWhen(cached);

    // Once per open: missing or older than 24 h => refresh now.
    if (isStale(cached)) await analyse(openId);
  }

  window.RetailAiInsights = { render: render, analyse: function () { return analyse(state.openId); } };
})();
