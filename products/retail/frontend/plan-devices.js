/* Plan & devices -- Phase 1b (2026-09-29), crew B.
 *
 * window.PlanDevices.render(host[, opts]) paints the owner's "Plan & devices"
 * screen into `host`:
 *   - plan card         which plan, until when, licence state
 *   - seat meter        "2 of 2 devices", or an honest "—" when unknown
 *   - devices           this computer + the computers paired on the shop
 *                       network, with the admin-only Remove
 *   - included features only what the signed licence really carries
 *   - add-on tiles      priced from website/js/content.js (mirrored in PRICES
 *                       below and drift-guarded by
 *                       tests/retail_p1b_plan_devices_prices_test.py)
 *
 * Owner only. The check here is presentation; the server enforces the same
 * rule on the one mutating call (site-relay revoke is admin-only there).
 *
 * WORKS STANDALONE: t() falls back to identity and every colour is a theme
 * token, so it also renders on a page that loaded only the theme CSS.
 *
 * "Unknown" is never drawn as zero. A seat count this computer has not
 * received shows "—" and says why; a joined till cannot count the other
 * computers and says so instead of claiming "1 of 2".
 *
 * P1b-waits: registry v9 -- the DEVICE-SYNC items (identity device list,
 * "Make this the main computer") are deliberately not built here; they wait
 * on registry v9 and will slot into devicesCard() below.
 */
(function () {
  'use strict';

  function t(s) {
    try {
      if (typeof window.t === 'function') return window.t(s);
    } catch (e) { /* fall through to identity */ }
    return s;
  }

  // Mirror of website/js/content.js pricing. Kept as constants (the website
  // file is not served by the app) and guarded against drift by a test.
  var PRICES = { currency: 'JD', extraDevice: '50', extraBranch: '250' };

  function addons() {
    return [
      {
        id: 'extra-device',
        english: 'Extra device',
        name: t('Extra device'),
        desc: t('Another till or phone, beyond the ones your license includes.'),
        price: PRICES.extraDevice,
        unit: t('per device, paid once')
      },
      {
        id: 'extra-branch',
        english: 'Extra branch',
        name: t('Extra branch'),
        desc: t('A second shop location with its own stock, staff and till.'),
        price: PRICES.extraBranch,
        unit: t('per branch, paid once')
      }
    ];
  }

  // Entitlement keys the licence can carry that a shopkeeper understands.
  var FEATURES = [
    { key: 'digital_receipts_enabled', label: 'Digital receipts on WhatsApp' }
  ];

  // Keys are the REAL LicenseState names (commercial_runtime/licensing_contracts/
  // state_machine.py) -- every member of that enum has a row, and
  // tests/retail_p1b_plan_devices_e2e.py walks each one in a real browser. An
  // earlier draft invented names (CHECKIN_DUE_SOON, OFFLINE_GRACE, DEACTIVATED)
  // that the licence never emits, so WARNING / GRACE_PERIOD / DEVICE_DEACTIVATED
  // all fell through to "Not configured" on a licensed shop.
  var STATE_LABELS = {
    ACTIVE_ONLINE: ['Active', 'ok'],
    ACTIVE_OFFLINE: ['Active (offline)', 'ok'],
    WARNING: ['Check-in needed soon', 'warn'],
    GRACE_PERIOD: ['Offline grace period', 'warn'],
    CLOCK_REVIEW_REQUIRED: ['Clock review required', 'warn'],
    RESTRICTED: ['Restricted', 'bad'],
    SUSPENDED: ['Suspended', 'bad'],
    REVOKED: ['Revoked', 'bad'],
    EXPIRED: ['Expired', 'bad'],
    LOCAL_STATE_CORRUPT: ['Local state needs reset', 'bad'],
    DEVICE_DEACTIVATED: ['Computer deactivated', 'bad'],
    DEVICE_REPLACED: ['Computer replaced', 'bad'],
    NOT_CONFIGURED: ['Not configured', 'neutral'],
    ACTIVATION_REQUIRED: ['Activation required', 'neutral'],
    ACTIVATING: ['Activating…', 'neutral']
  };

  // A state this screen does not know (a newer licence engine, or a status
  // that could not be read at all) is said to be unknown. It must never be
  // dressed up as a real state -- least of all as "Not configured".
  var UNKNOWN_STATE = ['Status unavailable', 'neutral'];

  // States in which this computer has no usable licence and the way forward is
  // to ACTIVATE it, not to "connect to the internet and wait".
  var NEEDS_ACTIVATION = {
    NOT_CONFIGURED: true, ACTIVATION_REQUIRED: true, ACTIVATING: true,
    DEVICE_DEACTIVATED: true, DEVICE_REPLACED: true
  };

  function stateLabel(state) {
    return typeof state === 'string' && Object.prototype.hasOwnProperty.call(STATE_LABELS, state)
      ? STATE_LABELS[state] : UNKNOWN_STATE;
  }

  var CSS = [
    '.pd-root{font-family:var(--font-base);color:var(--text-primary);display:flex;flex-direction:column;gap:var(--space-base);max-inline-size:44rem}',
    '.pd-card{background:var(--surface-raised);border:1px solid var(--border-default);border-radius:var(--radius-card);padding:var(--space-base);display:flex;flex-direction:column;gap:var(--space-snug)}',
    '.pd-h{margin:0;font-size:var(--text-size-subhead);font-weight:var(--text-weight-semibold);color:var(--text-primary)}',
    '.pd-row{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:var(--space-snug)}',
    '.pd-muted{margin:0;color:var(--text-secondary);font-size:var(--text-size-meta)}',
    '.pd-body{margin:0;font-size:var(--text-size-body)}',
    '.pd-plan{font-size:var(--text-size-title);font-weight:var(--text-weight-semibold)}',
    '.pd-badge{display:inline-block;padding-block:0;padding-inline:var(--space-snug);border-radius:var(--radius-pill);font-size:var(--text-size-micro);font-weight:var(--text-weight-semibold);border:1px solid var(--border-default);color:var(--text-secondary)}',
    '.pd-badge.ok{color:var(--state-success-text);background:var(--state-success-surface);border-color:var(--state-success-border)}',
    '.pd-badge.warn{color:var(--state-warning-text);background:var(--state-warning-surface);border-color:var(--state-warning-border)}',
    '.pd-badge.bad{color:var(--state-danger-text);background:var(--state-danger-surface);border-color:var(--state-danger-border)}',
    '.pd-badge.info{color:var(--state-info-text);background:var(--state-info-surface);border-color:var(--state-info-border)}',
    '.pd-seat{font-size:var(--text-size-title);font-weight:var(--text-weight-semibold)}',
    '.pd-meter{block-size:0.5rem;inline-size:100%;background:var(--border-default);border-radius:var(--radius-pill);overflow:hidden}',
    '.pd-meter>span{display:block;block-size:100%;inline-size:0;background:var(--state-success-text);border-radius:var(--radius-pill)}',
    '.pd-meter.full>span{background:var(--state-warning-text)}',
    '.pd-meter.over>span{background:var(--state-danger-text)}',
    '.pd-dev{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:var(--space-snug);padding-block:var(--space-snug);border-block-start:1px solid var(--border-hairline)}',
    '.pd-btn{font:inherit;font-size:var(--text-size-meta);min-block-size:2.5rem;padding-inline:var(--space-base);border-radius:var(--radius-control);border:1px solid var(--border-default);background:var(--surface-raised);color:var(--text-primary);cursor:pointer}',
    '.pd-btn:focus-visible,.pd-link:focus-visible{outline:2px solid var(--accent-action);outline-offset:2px}',
    '.pd-btn.danger{color:var(--state-danger-text);border-color:var(--state-danger-border)}',
    '.pd-link{display:inline-flex;align-items:center;align-self:flex-start;min-block-size:2.5rem;padding-inline:var(--space-base);border-radius:var(--radius-control);border:1px solid var(--accent-action);color:var(--accent-action);text-decoration:none;font-size:var(--text-size-meta);font-weight:var(--text-weight-semibold)}',
    '.pd-link.quiet{border-color:transparent;padding-inline:0}',
    '.pd-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(14rem,1fr));gap:var(--space-base)}',
    '.pd-tile{border:1px solid var(--border-default);border-radius:var(--radius-card);padding:var(--space-base);display:flex;flex-direction:column;gap:var(--space-snug)}',
    '.pd-price{font-size:var(--text-size-title);font-weight:var(--text-weight-semibold)}',
    '.pd-list{margin:0;padding-inline-start:var(--space-base);display:flex;flex-direction:column;gap:var(--space-tight)}',
    '.pd-err{margin:0;color:var(--state-danger-text);font-size:var(--text-size-meta)}'
  ].join('');

  function ensureStyle() {
    if (document.getElementById('pd-style')) return;
    var s = document.createElement('style');
    s.id = 'pd-style';
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

  function fetchJson(url) {
    return fetch(url, { credentials: 'include' }).then(function (r) {
      if (!r.ok) return null;
      return r.json();
    }).catch(function () { return null; });
  }

  function dateOnly(s) {
    if (typeof s !== 'string') return null;
    var m = /^(\d{4}-\d{2}-\d{2})/.exec(s);
    // Wrapped in a left-to-right ISOLATE: in an Arabic (RTL) sentence the
    // bidi algorithm otherwise reorders 2027-09-01 into 01-09-2027 -- a
    // different-looking date on a screen about a licence deadline.
    return m ? '\u2066' + m[1] + '\u2069' : null;
  }

  // "RETAIL_STANDARD" -> "Standard". The product word is noise on a screen
  // that is already inside Aura Retail; a raw code is never shown.
  function planName(code) {
    if (typeof code !== 'string' || !code) return null;
    var words = code.split(/[^A-Za-z0-9]+/).filter(function (w) {
      var u = w.toUpperCase();
      return w && u !== 'AURA' && u !== 'RETAIL';
    });
    if (!words.length) return t('Aura Shop');
    return words.map(function (w) {
      return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
    }).join(' ');
  }

  function fill(str, vars) {
    return str.replace(/\{(\w+)\}/g, function (m, k) {
      return Object.prototype.hasOwnProperty.call(vars, k) ? String(vars[k]) : m;
    });
  }

  function isOwner(session) {
    return !!(session && session.authenticated && session.user && session.user.role === 'admin');
  }

  function isJoinedClient(me) {
    return !!(me && me.device && me.device.is_admin_device === false && me.can_claim_admin === false);
  }

  function load() {
    return fetchJson('/api/auth/session').then(function (session) {
      if (!isOwner(session)) return { owner: false };
      return Promise.all([
        fetchJson('/api/licensing/status'),
        fetchJson('/api/site-relay/status'),
        fetchJson('/api/devices/me'),
        fetchJson('/api/sub/retail/settings/branding')
      ]).then(function (r) {
        var branding = r[3] && r[3].data ? r[3].data : {};
        return {
          owner: true,
          lic: r[0] || {},
          relay: r[1],
          me: r[2],
          shopName: branding.branding_business_name || ''
        };
      });
    });
  }

  function activeRelayDevices(relay) {
    if (!relay || !relay.enabled || !Array.isArray(relay.devices)) return [];
    return relay.devices.filter(function (d) { return d && !d.revoked; });
  }

  // used: a number, or null when this computer cannot know. The count comes
  // only from what this screen can see itself: the site-relay's device list.
  // (The licence status carries no seat count; an earlier `devices_used` field
  // on it was never filled in by any caller and has been removed.)
  function seatsUsed(data) {
    if (data.relay && data.relay.enabled) return 1 + activeRelayDevices(data.relay).length;
    if (isJoinedClient(data.me)) return null;
    if (data.relay && data.relay.enabled === false) return 1;
    return null;
  }

  function seatsAllowed(data) {
    var n = data.lic.allowed_device_count;
    return typeof n === 'number' ? n : null;
  }

  function planCard(data) {
    var lic = data.lic;
    var card = el('section', 'pd-card');
    card.setAttribute('data-pd', 'plan');
    card.appendChild(el('h2', 'pd-h', t('Your plan')));

    var row = el('div', 'pd-row');
    var name = planName(lic.plan_code);
    row.appendChild(el('span', 'pd-plan', name || t('Aura Shop')));
    var pair = stateLabel(lic.current_state);
    var badge = el('span', 'pd-badge ' + pair[1], t(pair[0]));
    badge.setAttribute('data-pd', 'state');
    badge.setAttribute('data-pd-state', typeof lic.current_state === 'string' ? lic.current_state : '');
    row.appendChild(badge);
    card.appendChild(row);

    if (!name) card.appendChild(el('p', 'pd-muted', t('The plan name is not available on this computer yet.')));

    var end = dateOnly(lic.term && lic.term.end);
    var until = el('p', 'pd-body', end ? fill(t('Valid until {date}'), { date: end }) : t('Valid until —'));
    until.setAttribute('data-pd', 'until');
    card.appendChild(until);

    var link = el('a', 'pd-link quiet', t('License details'));
    link.setAttribute('href', '/static/licensing.html');
    card.appendChild(link);
    return card;
  }

  function confirmRemove(name) {
    // One whole sentence with a slot, never three glued fragments: Arabic
    // word order differs, so a translator must see the sentence as a unit.
    var title = fill(t('Remove {name} from the shop?'), { name: name });
    var message = t('It will stop syncing with this hub immediately. It keeps the data it already has and can carry on selling on its own.');
    var rs = window.RetailSystem;
    if (rs && typeof rs._confirm === 'function') {
      return rs._confirm({ title: title, message: message, confirmLabel: t('Remove'), danger: true });
    }
    return Promise.resolve(window.confirm(title + '\n\n' + message));
  }

  function removeDevice(host, opts, dev, errNode) {
    var label = dev.label || t('Unnamed device');
    return confirmRemove(label).then(function (ok) {
      if (!ok) return null;
      return fetch('/api/site-relay/devices/' + encodeURIComponent(dev.installation_id) + '/revoke', {
        method: 'POST', credentials: 'include'
      }).then(function (r) {
        if (!r.ok) throw new Error('revoke');
        return render(host, opts);
      }).catch(function () {
        errNode.textContent = t('That did not work. Please try again.');
      });
    });
  }

  function devicesCard(data, host, opts) {
    var card = el('section', 'pd-card');
    card.setAttribute('data-pd', 'devices');
    card.appendChild(el('h2', 'pd-h', t('Devices')));

    var used = seatsUsed(data);
    var allowed = seatsAllowed(data);
    var figure = el('div', 'pd-seat',
      allowed === null ? '—' : fill(t('{used} of {allowed} devices'), { used: used === null ? '—' : used, allowed: allowed }));
    figure.id = 'pd-seat-figure';
    card.appendChild(figure);

    if (allowed !== null && used !== null) {
      var meter = el('div', 'pd-meter' + (used > allowed ? ' over' : (used === allowed ? ' full' : '')));
      meter.setAttribute('role', 'progressbar');
      meter.setAttribute('aria-valuemin', '0');
      meter.setAttribute('aria-valuemax', String(allowed));
      meter.setAttribute('aria-valuenow', String(Math.min(used, allowed)));
      var bar = el('span');
      bar.style.inlineSize = (allowed > 0 ? Math.min(100, Math.round(used * 100 / allowed)) : 100) + '%';
      meter.appendChild(bar);
      card.appendChild(meter);
    }

    if (allowed === null) {
      // An unactivated computer is not waiting for a network: it is waiting
      // for the owner to activate it. Telling it to "connect to the internet"
      // sends the person to the wrong fix.
      var stateNow = data.lic.current_state;
      var needsActivation = typeof stateNow === 'string' && NEEDS_ACTIVATION[stateNow] === true;
      var why = needsActivation
        ? t('This computer is not activated yet. Activate it with your license key and the number of devices it includes will appear here.')
        : t('This computer has not received the number of devices your license includes yet. Connect to the internet and it will appear here.');
      var whyNode = el('p', 'pd-muted', why);
      whyNode.setAttribute('data-pd', needsActivation ? 'needs-activation' : 'needs-internet');
      card.appendChild(whyNode);
    } else if (used === null) {
      card.appendChild(el('p', 'pd-muted', t('This computer is connected to the shop’s main computer, so it cannot count the other computers. Open this screen on the main computer.')));
    } else if (used > allowed) {
      card.appendChild(el('p', 'pd-err', t('More computers are connected than your license includes. Remove one below, or request an extra device.')));
    } else if (used === allowed) {
      card.appendChild(el('p', 'pd-muted', t('Every device your license includes is in use. To add another, request an extra device below.')));
    }

    // The "manager's phone and one till" wording is the shape of a 2-device
    // licence and is only true for one. Any other count is stated plainly from
    // the licence itself; an unknown count says nothing rather than guess.
    if (allowed !== null) {
      var inclText;
      if (allowed === 2) inclText = t('Your license includes a manager’s phone and one till.');
      else if (allowed === 1) inclText = t('Your license includes 1 device.');
      else inclText = fill(t('Your license includes {n} devices.'), { n: allowed });
      var incl = el('p', 'pd-body', inclText);
      incl.setAttribute('data-pd', 'included');
      card.appendChild(incl);
    }

    var joined = isJoinedClient(data.me);
    var errNode = el('p', 'pd-err');
    errNode.setAttribute('role', 'alert');

    var me = el('div', 'pd-dev');
    var ownName = data.me && data.me.device && data.me.device.device_name;
    var meLeft = el('div');
    // Name line only when the computer has a name; otherwise "This computer"
    // would be printed twice, one line under the other.
    meLeft.appendChild(el('p', 'pd-body', ownName || t('This computer')));
    if (ownName) meLeft.appendChild(el('p', 'pd-muted', t('This computer')));
    me.appendChild(meLeft);
    if (!joined) me.appendChild(el('span', 'pd-badge info', t('Main computer')));
    card.appendChild(me);

    if (joined) {
      card.appendChild(el('p', 'pd-muted', t('This computer is connected to the shop’s main computer. Manage the other computers there.')));
    } else if (data.relay && data.relay.enabled) {
      activeRelayDevices(data.relay).forEach(function (d) {
        var row = el('div', 'pd-dev');
        row.setAttribute('data-pd-device', String(d.installation_id));
        var left = el('div');
        left.appendChild(el('p', 'pd-body', d.label || t('Unnamed device')));
        left.appendChild(el('p', 'pd-muted', t('Connected')));
        row.appendChild(left);
        var btn = el('button', 'pd-btn danger', t('Remove'));
        btn.type = 'button';
        btn.addEventListener('click', function () { removeDevice(host, opts, d, errNode); });
        row.appendChild(btn);
        card.appendChild(row);
      });
    } else {
      card.appendChild(el('p', 'pd-muted', t('Shop network is off. This computer works on its own.')));
    }
    card.appendChild(errNode);
    return card;
  }

  function featuresCard(data) {
    var card = el('section', 'pd-card');
    card.setAttribute('data-pd', 'features');
    card.appendChild(el('h2', 'pd-h', t('Included in your license')));
    var ent = data.lic.entitlements || {};
    var shown = FEATURES.filter(function (f) { return typeof ent[f.key] === 'boolean'; });
    if (!shown.length) {
      card.appendChild(el('p', 'pd-muted', t('Nothing extra is switched on in your license.')));
      return card;
    }
    var ul = el('ul', 'pd-list');
    shown.forEach(function (f) {
      var on = ent[f.key] === true;
      ul.appendChild(el('li', 'pd-body', t(f.label) + (on ? '' : ' (' + t('not included') + ')')));
    });
    card.appendChild(ul);
    return card;
  }

  // Crew P's AuraHelp.supportUrl(context) -> wa.me URL or null. Absent,
  // throwing, or null all mean "no support channel": the button is hidden.
  function supportUrl(context) {
    try {
      var h = window.AuraHelp;
      if (h && typeof h.supportUrl === 'function') {
        var u = h.supportUrl(context);
        return typeof u === 'string' && u ? u : null;
      }
    } catch (e) { /* no support channel */ }
    return null;
  }

  function addonsCard(data, opts) {
    var card = el('section', 'pd-card');
    card.setAttribute('data-pd', 'addons');
    card.appendChild(el('h2', 'pd-h', t('Add-ons')));
    var tiles = el('div', 'pd-tiles');
    var list = opts && Array.isArray(opts.addons) ? opts.addons : addons();
    list.forEach(function (a) {
      var tile = el('div', 'pd-tile');
      tile.setAttribute('data-pd-addon', a.id);
      tile.appendChild(el('div', 'pd-h', a.name));
      tile.appendChild(el('div', 'pd-muted', a.desc));
      var priced = a.price !== null && a.price !== undefined && a.price !== '';
      var price = el('div', 'pd-price', priced ? t('JD') + ' ' + a.price : t('Price on request'));
      price.setAttribute('data-pd', 'price');
      tile.appendChild(price);
      if (priced && a.unit) tile.appendChild(el('div', 'pd-muted', a.unit));
      // A shop that never filled in its branding has no name to send, so the
      // installation id rides along too: it is what support can actually look
      // up, and it is never shown on screen.
      var instId = data.lic && typeof data.lic.installation_id === 'string' ? data.lic.installation_id : '';
      var url = supportUrl('Add-on request: ' + (a.english || a.id) +
        (data.shopName ? ' | Shop: ' + data.shopName : '') +
        (instId ? ' | Installation: ' + instId : ''));
      if (url) {
        var req = el('a', 'pd-link', t('Request'));
        req.setAttribute('href', url);
        req.setAttribute('target', '_blank');
        req.setAttribute('rel', 'noopener noreferrer');
        req.setAttribute('data-pd', 'request');
        tile.appendChild(req);
      }
      tiles.appendChild(tile);
    });
    card.appendChild(tiles);
    return card;
  }

  function render(host, opts) {
    ensureStyle();
    clear(host);
    host.classList.add('pd-root');
    host.appendChild(el('p', 'pd-muted', t('Loading…')));
    return load().then(function (data) {
      clear(host);
      if (!data.owner) {
        host.appendChild(el('p', 'pd-muted', t('Only the shop owner can see the plan and devices.')));
        return;
      }
      host.appendChild(planCard(data));
      host.appendChild(devicesCard(data, host, opts));
      host.appendChild(featuresCard(data));
      host.appendChild(addonsCard(data, opts));
    });
  }

  window.PlanDevices = { render: render, prices: PRICES };
})();
