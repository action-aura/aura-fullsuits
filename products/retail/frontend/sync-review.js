/* Sync review -- Phase 1b (2026-09-29), crew Q.
 *
 * window.SyncReview.render(container[, opts]) paints ONE read-only card into
 * `container`: "Sales from other computers that could not be added here".
 *
 * WHY IT EXISTS. When two computers sync, a record the other computer sent can
 * be refused here -- most often a sale whose receipt number this computer
 * already used. The engine parks it in retail.db's `sync_apply_quarantine`.
 * Until this card, nothing read that table: the sale never reached this
 * computer's takings and nobody was told. The card names the count and the
 * money, then lists each parked record in plain words.
 *
 * READ-ONLY ON PURPOSE. No "fix" / "accept" / "delete" button. The sync engine
 * retries every parked record on each pull and removes the ones that resolve
 * (`missing_parent` rows usually clear on their own). The others need a person
 * and today the honest way forward is Action Aura support, which the card says.
 *
 * DATA. GET /api/sub/retail/sync-review (owner only). The server sends FIXED
 * English sentences for kind and reason (never raw payload); they go through
 * t() here, so the Arabic lives in the catalogue like every other string.
 *
 * MOUNT (crew H, the "Needs review" screen):
 *     window.SyncReview && window.SyncReview.render(containerEl);
 * The module is tolerated ABSENT by the caller. Needs
 * <script src="/static/sync-review.js"></script> after subsystem-retail.js
 * and before app-shell.js in index.html (crew P owns that file).
 *
 * WORKS STANDALONE: t() falls back to identity, money falls back to a plain
 * "JD 0.000" string, every colour is a theme token, and the CSS uses logical
 * properties only so it reads correctly under body.rtl.
 */
(function () {
  'use strict';

  var URL = '/api/sub/retail/sync-review';
  var PAGE = 50;

  function t(s) {
    try {
      if (typeof window.t === 'function') return window.t(s);
    } catch (e) { /* fall through to identity */ }
    return s;
  }

  function fill(str, vars) {
    return str.replace(/\{(\w+)\}/g, function (m, k) {
      return Object.prototype.hasOwnProperty.call(vars, k) ? String(vars[k]) : m;
    });
  }

  // The app's own money formatter (currency mark + the currency's decimals,
  // fils for JD). RetailSystem is a top-level `const` in another classic
  // script: reachable by bare name, NOT as window.RetailSystem.
  function money(n) {
    var v = +n || 0;
    try {
      if (typeof RetailSystem !== 'undefined' && RetailSystem && typeof RetailSystem._fmt === 'function') {
        return RetailSystem._fmt(v);
      }
    } catch (e) { /* fall through */ }
    return 'JD ' + v.toFixed(3);
  }

  // "2026-09-29 14:05:33" -> "2026-09-29 14:05". Wrapped in a left-to-right
  // ISOLATE so an Arabic sentence cannot reorder the digits into another date.
  function when(s) {
    if (typeof s !== 'string') return null;
    var m = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})/.exec(s);
    return '⁦' + (m ? m[1] + ' ' + m[2] : s) + '⁩';
  }

  // The sending computer is an opaque id; the last few characters are enough
  // for the owner to tell two computers apart without reading a UUID.
  function shortId(id) {
    var s = String(id);
    return '⁦' + (s.length > 8 ? s.slice(-8) : s) + '⁩';
  }

  var CSS = [
    '.sr-root{font-family:var(--font-base);color:var(--text-primary);max-inline-size:44rem}',
    '.sr-card{background:var(--surface-raised);border:1px solid var(--border-default);border-radius:var(--radius-card);padding:var(--space-base);display:flex;flex-direction:column;gap:var(--space-snug)}',
    '.sr-h{margin:0;font-size:var(--text-size-subhead);font-weight:var(--text-weight-semibold);color:var(--text-primary)}',
    '.sr-muted{margin:0;color:var(--text-secondary);font-size:var(--text-size-meta)}',
    '.sr-body{margin:0;font-size:var(--text-size-body)}',
    '.sr-stats{display:flex;flex-wrap:wrap;gap:var(--space-base)}',
    '.sr-stat{display:flex;flex-direction:column;gap:var(--space-tight);padding:var(--space-snug) var(--space-base);border:1px solid var(--state-warning-border);background:var(--state-warning-surface);border-radius:var(--radius-control);min-inline-size:9rem}',
    '.sr-stat-n{font-size:var(--text-size-title);font-weight:var(--text-weight-semibold);color:var(--state-warning-text)}',
    '.sr-stat-l{font-size:var(--text-size-meta);color:var(--text-secondary)}',
    '.sr-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column}',
    '.sr-item{display:flex;flex-direction:column;gap:var(--space-tight);padding-block:var(--space-snug);border-block-start:1px solid var(--border-hairline)}',
    '.sr-line{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:var(--space-snug)}',
    '.sr-kind{font-size:var(--text-size-body);font-weight:var(--text-weight-semibold)}',
    '.sr-num{unicode-bidi:isolate;direction:ltr;color:var(--text-secondary);font-size:var(--text-size-meta)}',
    '.sr-money{unicode-bidi:isolate;direction:ltr;font-weight:var(--text-weight-semibold)}',
    '.sr-badge{display:inline-block;padding-block:0;padding-inline:var(--space-snug);border-radius:var(--radius-pill);font-size:var(--text-size-micro);font-weight:var(--text-weight-semibold);border:1px solid var(--border-default);color:var(--text-secondary)}',
    '.sr-badge.info{color:var(--state-info-text);background:var(--state-info-surface);border-color:var(--state-info-border)}',
    '.sr-badge.warn{color:var(--state-warning-text);background:var(--state-warning-surface);border-color:var(--state-warning-border)}',
    '.sr-help{border-block-start:1px solid var(--border-hairline);padding-block-start:var(--space-snug);display:flex;flex-direction:column;gap:var(--space-tight)}',
    '.sr-ok{margin:0;color:var(--state-success-text);background:var(--state-success-surface);border:1px solid var(--state-success-border);border-radius:var(--radius-control);padding:var(--space-snug) var(--space-base);font-size:var(--text-size-body)}',
    '.sr-err{margin:0;color:var(--state-danger-text);background:var(--state-danger-surface);border:1px solid var(--state-danger-border);border-radius:var(--radius-control);padding:var(--space-snug) var(--space-base);font-size:var(--text-size-body)}',
    '.sr-btn{font:inherit;font-size:var(--text-size-meta);min-block-size:2.5rem;padding-inline:var(--space-base);border-radius:var(--radius-control);border:1px solid var(--border-default);background:var(--surface-raised);color:var(--text-primary);cursor:pointer;align-self:flex-start}',
    '.sr-btn:focus-visible{outline:2px solid var(--accent-action);outline-offset:2px}'
  ].join('');

  function ensureStyle() {
    if (document.getElementById('sr-style')) return;
    var s = document.createElement('style');
    s.id = 'sr-style';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  // -> {ok, status, code, data}. Never throws: a dead network is an answer too.
  function fetchReview(cursor) {
    var url = URL + '?limit=' + PAGE + (cursor ? '&cursor=' + encodeURIComponent(cursor) : '');
    return fetch(url, { credentials: 'include' }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        return {
          ok: r.ok && body && body.status === 'success',
          status: r.status,
          code: body && body.code,
          data: body && body.data
        };
      });
    }).catch(function () {
      return { ok: false, status: 0, code: null, data: null };
    });
  }

  function itemRow(it) {
    var li = el('li', 'sr-item');
    var top = el('div', 'sr-line');
    var left = el('span');
    left.appendChild(el('span', 'sr-kind', t(it.kind)));
    if (it.number) {
      left.appendChild(document.createTextNode(' '));
      left.appendChild(el('span', 'sr-num', it.number));
    }
    top.appendChild(left);
    if (typeof it.amount === 'number') top.appendChild(el('span', 'sr-money', money(it.amount)));
    li.appendChild(top);

    li.appendChild(el('p', 'sr-body', fill(t('Not added because {reason}.'), { reason: t(it.reason) })));

    var meta = el('div', 'sr-line');
    var facts = [];
    if (it.source) facts.push(fill(t('From computer {id}'), { id: shortId(it.source) }));
    var w = when(it.arrived_at);
    if (w) facts.push(fill(t('Set aside {time}'), { time: w }));
    meta.appendChild(el('span', 'sr-muted', facts.join(' · ')));
    meta.appendChild(it.retries_itself
      ? el('span', 'sr-badge info', t('May fix itself'))
      : el('span', 'sr-badge warn', t('Needs a person')));
    li.appendChild(meta);
    return li;
  }

  function stat(number, label) {
    var d = el('div', 'sr-stat');
    d.appendChild(el('span', 'sr-stat-n', number));
    d.appendChild(el('span', 'sr-stat-l', label));
    return d;
  }

  function guidance() {
    var g = el('div', 'sr-help');
    g.appendChild(el('p', 'sr-body', t('What you can do today')));
    g.appendChild(el('p', 'sr-muted',
      t('Keep both computers switched on and online so they can sync. Records marked "May fix itself" are tried again on every sync.')));
    g.appendChild(el('p', 'sr-muted',
      t('If a sale stays here, call Action Aura support. Do not enter it again by hand: it may already be counted on the other computer.')));
    return g;
  }

  function paint(container, state) {
    clear(container);
    var root = el('div', 'sr-root');
    var card = el('section', 'sr-card');
    card.setAttribute('aria-label', t('Sales from other computers that could not be added here'));
    card.appendChild(el('h3', 'sr-h', t('Sales from other computers that could not be added here')));
    root.appendChild(card);
    container.appendChild(root);

    if (state.phase === 'loading') {
      card.appendChild(el('p', 'sr-muted', t('Loading…')));
      return;
    }
    if (state.phase === 'error') {
      var msg = state.status === 403
        ? t('Only the shop owner can open this.')
        : t('This list could not be loaded. Check the connection and try again.');
      var e = el('p', 'sr-err', msg);
      e.setAttribute('role', 'alert');
      card.appendChild(e);
      if (state.status !== 403) {
        var retry = el('button', 'sr-btn', t('Try again'));
        retry.type = 'button';
        retry.addEventListener('click', function () { load(container); });
        card.appendChild(retry);
      }
      return;
    }

    var sum = state.summary;
    if (!sum.waiting_count) {
      var ok = el('p', 'sr-ok', t('Nothing is waiting. Every sale from your other computers was added.'));
      ok.setAttribute('role', 'status');
      card.appendChild(ok);
      return;
    }

    var stats = el('div', 'sr-stats');
    stats.appendChild(stat(String(sum.waiting_count), t('Records waiting')));
    stats.appendChild(stat(String(sum.sales_count), t('Sales set aside')));
    stats.appendChild(stat(money(sum.sales_total), t('Total at risk')));
    card.appendChild(stats);
    card.appendChild(el('p', 'sr-muted',
      t('This money is not in this computer\'s takings until the records below are added.')));

    var list = el('ul', 'sr-list');
    state.items.forEach(function (it) { list.appendChild(itemRow(it)); });
    card.appendChild(list);

    if (state.next) {
      var more = el('button', 'sr-btn', t('Show more'));
      more.type = 'button';
      more.addEventListener('click', function () { loadMore(container, state, more); });
      card.appendChild(more);
    }
    card.appendChild(guidance());
  }

  function loadMore(container, state, btn) {
    btn.disabled = true;
    var token = container.__srToken;
    fetchReview(state.next).then(function (r) {
      if (container.__srToken !== token) return;
      if (!r.ok || !r.data) {
        btn.disabled = false;
        btn.textContent = t('Try again');
        return;
      }
      state.items = state.items.concat(r.data.items || []);
      state.next = r.data.next_cursor || null;
      state.summary = r.data.summary || state.summary;
      paint(container, state);
    });
  }

  function load(container) {
    // A second render() into the same container (the screen re-opened, or
    // Retry) must win over a slower first one still in flight.
    var token = (container.__srToken || 0) + 1;
    container.__srToken = token;
    paint(container, { phase: 'loading' });
    return fetchReview(null).then(function (r) {
      if (container.__srToken !== token) return;
      if (!r.ok || !r.data || !r.data.summary) {
        paint(container, { phase: 'error', status: r.status });
        return;
      }
      paint(container, {
        phase: 'ready',
        summary: r.data.summary,
        items: r.data.items || [],
        next: r.data.next_cursor || null
      });
    });
  }

  function render(container) {
    if (!container || typeof container.appendChild !== 'function') return Promise.resolve();
    ensureStyle();
    return load(container);
  }

  window.SyncReview = { render: render };
})();
