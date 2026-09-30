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
 * P2S (registry v9, crew U): the device-sync items now live here.
 *   - "This computer" row  main badge from the registry, app version, last seen,
 *                          and (on a non-main computer) the way to become main.
 *   - "Computers in this shop" card  every computer the registry knows, with the
 *                          rename / remove / reinstate / make-main actions ON THE
 *                          MAIN COMPUTER ONLY (the server enforces the same rule
 *                          with NOT_ADMIN_DEVICE; hiding the buttons is manners).
 *   - take-over dialog     for a non-main computer whose main is gone: owner
 *                          password + typing TAKE OVER, server codes said as
 *                          plain sentences (errorText, also used by app-shell).
 * The site-relay list above is a different list (licence seats on the shop
 * network) and is untouched.
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
    '.pd-err{margin:0;color:var(--state-danger-text);font-size:var(--text-size-meta)}',
    '.pd-acts{display:flex;flex-wrap:wrap;gap:var(--space-snug);align-items:center}',
    '.pd-badges{display:flex;flex-wrap:wrap;gap:var(--space-snug);align-items:center}',
    '.pd-dev>div:first-child{min-inline-size:0;flex:1 1 12rem;overflow-wrap:anywhere}',
    '.pd-input{font:inherit;font-size:var(--text-size-body);min-block-size:2.5rem;inline-size:100%;box-sizing:border-box;padding-inline:var(--space-snug);border-radius:var(--radius-control);border:1px solid var(--border-default);background:var(--surface-sunken);color:var(--text-primary)}',
    '.pd-input:focus-visible{outline:2px solid var(--accent-action);outline-offset:2px}',
    '.pd-edit{display:flex;flex-wrap:wrap;gap:var(--space-snug);align-items:center;inline-size:100%}',
    '.pd-edit .pd-input{flex:1 1 12rem;inline-size:auto}',
    // inside the take-over dialog the explanation sits right above the first label: give it air
    '#pd-takeover-dialog>.pd-muted{margin-block-end:var(--space-base)}'
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
        fetchJson('/api/sub/retail/settings/branding'),
        fetchJson('/api/devices')
      ]).then(function (r) {
        var branding = r[3] && r[3].data ? r[3].data : {};
        // The registry list. null = could not be read (an old server, a
        // network blip): the screen then shows exactly what it showed before
        // registry v9 rather than an empty "no computers" it does not know.
        var regBody = r[4] && Array.isArray(r[4].devices) ? r[4]
          : (r[4] && r[4].data && Array.isArray(r[4].data.devices) ? r[4].data : null);
        return {
          owner: true,
          lic: r[0] || {},
          relay: r[1],
          me: r[2],
          shopName: branding.branding_business_name || '',
          registry: regBody && Array.isArray(regBody.devices) ? regBody.devices : null
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

  // ── registry v9: the shop's computers ───────────────────────────────────

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // A server refusal, said the way a shopkeeper would say it. Keyed on the
  // stable `code`, never on the English `error` string: that one is for logs
  // and is not translated. An unknown code (a newer server) gets the generic
  // sentence instead of raw text. Also used by app-shell's claim button.
  // "N minutes / hours / days" in the shopkeeper's language. English has two
  // forms (1, other). Arabic has four, and one sentence with a glued unit reads
  // wrong for most counts: 1 (one word, no number), 2 (dual), 3-10 (plural),
  // 11+ (singular after a big number). Each form is its own whole catalog key
  // (see also share.js daysPhrase); the "(two)"/"(few)" ones are only ever
  // picked in Arabic, so English never shows those suffixes.
  function unitPhrase(n, unit) {
    var one = '{n} ' + unit;
    var many = '{n} ' + unit + 's';
    var ar = window.AuraI18n && window.AuraI18n.current === 'ar';
    var key = n === 1 ? one : many;
    if (ar && n === 2) key = many + ' (two)';
    else if (ar && n >= 3 && n <= 10) key = many + ' (few)';
    return fill(t(key), { n: n });
  }

  function errorText(body) {
    var code = body && typeof body.code === 'string' ? body.code : '';
    if (code === 'TAKEOVER_LOCKED') {
      var secs = Number(body.retry_after_seconds);
      if (isFinite(secs) && secs > 0) {
        var mins = Math.max(1, Math.ceil(secs / 60));
        var wait;
        if (mins < 120) wait = unitPhrase(mins, 'minute');
        else if (Math.ceil(mins / 60) < 48) wait = unitPhrase(Math.ceil(mins / 60), 'hour');
        else wait = unitPhrase(Math.ceil(Math.ceil(mins / 60) / 24), 'day');
        return fill(t('Too many wrong passwords. Try again in {wait}.'), { wait: wait });
      }
      return t('Too many wrong passwords. Try again later.');
    }
    var known = {
      NOT_ADMIN_DEVICE: t('Only the main computer can do this. Open this screen on the main computer.'),
      ADMIN_ROLE_REQUIRED: t('Only the shop owner can do this.'),
      DEVICE_NOT_FOUND: t('That computer is not in this shop any more.'),
      DEVICE_REVOKED: t('That computer was removed from the shop. Bring it back first.'),
      DEVICE_NOT_REVOKED: t('That computer is already part of the shop.'),
      DEVICE_NOT_ELIGIBLE: t('That computer cannot be the main computer.'),
      DEVICE_IDENTITY_CLASH: t('This computer looks the same as another computer in the shop, so it cannot be made the main computer. Contact support.'),
      MAIN_MUST_BE_WINDOWS: t('Only a Windows computer can be the main computer.'),
      JOINED_DEVICE_CANNOT_CLAIM: t('This computer joined an existing shop, so it cannot become the main computer by itself. Ask the main computer to hand the role over.'),
      ADMIN_DEVICE_ALREADY_CLAIMED: t('Another computer is already the main computer.'),
      CANNOT_REVOKE_MAIN: t('The main computer cannot be removed. Make another computer the main one first.'),
      DEVICE_NAME_REQUIRED: t('Type a name first.'),
      DEVICE_NAME_TOO_LONG: t('The name can be at most 60 characters.'),
      DEVICE_NAME_INVALID: t('That name has characters that are not allowed.'),
      TAKEOVER_CONFIRM_REQUIRED: t('Type TAKE OVER to confirm.'),
      TAKEOVER_PASSWORD_INCORRECT: t('Your password is not correct.'),
      TAKEOVER_NO_MAIN: t('This shop has no main computer to replace. Set this computer as the main computer instead.'),
      TAKEOVER_ALREADY_MAIN: t('This computer is already the main computer.'),
      TAKEOVER_MAIN_SEEN_RECENTLY: t('The main computer was online in the last 7 days. You can only take over when it has been silent for at least 7 days.'),
      TAKEOVER_MAIN_UNKNOWN: t('This computer cannot tell when the main computer was last online, so it cannot take over.'),
      TAKEOVER_DEVICE_NOT_ACTIVE: t('This computer was removed from the shop and cannot take over.'),
      TAKEOVER_CLOCK_BEHIND: t('The clock on this computer is behind the shop records. Correct the date and time, then try again.')
    };
    return Object.prototype.hasOwnProperty.call(known, code) ? known[code] : t('That did not work. Please try again.');
  }

  function postJson(url, payload) {
    var init = { method: 'POST', credentials: 'include', cache: 'no-store' };
    if (payload !== undefined) {
      init.headers = { 'Content-Type': 'application/json' };
      init.body = JSON.stringify(payload);
    }
    return fetch(url, init).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (b) {
        return { ok: r.ok, status: r.status, body: b };
      });
    }).catch(function () { return { ok: false, status: 0, body: null }; });
  }

  function thisRegistryDevice(data) {
    var list = data.registry;
    if (!Array.isArray(list)) return null;
    for (var i = 0; i < list.length; i++) if (list[i] && list[i].is_this_device === true) return list[i];
    return null;
  }

  // Only the main computer may manage the others. Read from the registry
  // entry of THIS computer; no entry means the list could not be read, and
  // then nothing is offered (a button that cannot work is worse than none).
  function mainHere(data) {
    var d = thisRegistryDevice(data);
    return !!(d && d.is_admin_device === true);
  }

  function canBeMain(d) {
    var p = d && typeof d.platform === 'string' ? d.platform.toUpperCase() : '';
    return p === '' || p === 'WINDOWS';
  }

  var PLATFORM_NAMES = { WINDOWS: 'Windows', ANDROID: 'Android', IOS: 'iOS' };

  function metaLines(d) {
    var bits = [];
    var p = typeof d.platform === 'string' ? d.platform.toUpperCase() : '';
    if (p) bits.push(PLATFORM_NAMES[p] || (p.charAt(0) + p.slice(1).toLowerCase()));
    if (typeof d.app_version === 'string' && d.app_version) {
      bits.push(fill(t('Version {version}'), { version: '⁦' + d.app_version + '⁩' }));
    }
    var seen = dateOnly(d.last_seen_at);
    return {
      line1: bits.join(' · '),
      line2: seen ? fill(t('Last seen {date}'), { date: seen }) : t('Never seen')
    };
  }

  // This computer just became the main one, or just stopped being it: the
  // shell's own badge, gear menu and audit entry all hang off these two flags,
  // and launch() is what re-reads them (the same thing the claim button does).
  function refreshShell(nowMain, host, opts) {
    var app = window.SubsystemApp;
    if (app && typeof app.launch === 'function') {
      app.isAdminDevice = nowMain;
      app.canClaimAdminDevice = false;
      app.launch(app.active || 'retail', app.currentSection || 'dashboard');
      return Promise.resolve();
    }
    return render(host, opts);
  }

  function askLeave(reason) {
    var app = window.SubsystemApp;
    if (app && typeof app._askLeaveGuards === 'function') return app._askLeaveGuards(reason, 'navigate');
    return Promise.resolve(true);
  }

  function toast(msg, kind) {
    var app = window.SubsystemApp;
    if (app && typeof app.showToast === 'function') app.showToast(msg, kind);
  }

  function makeMain(host, opts, d, errNode) {
    var name = d.device_label || t('Unnamed computer');
    var title = fill(t('Make {name} the main computer?'), { name: name });
    var message = t('This computer will stop being the main computer.');
    var rs = window.RetailSystem;
    var asked = rs && typeof rs._confirm === 'function'
      ? rs._confirm({ title: title, message: message, confirmLabel: t('Make main computer'), danger: false })
      : Promise.resolve(window.confirm(title + '\n\n' + message));
    return asked.then(function (ok) {
      if (!ok) return null;
      return askLeave('transfer-main-computer').then(function (go) {
        if (!go) return null;
        return postJson('/api/devices/' + encodeURIComponent(d.id) + '/admin').then(function (r) {
          if (!r.ok) { errNode.textContent = errorText(r.body); return null; }
          return refreshShell(false, host, opts);
        });
      });
    });
  }

  function removeComputer(host, opts, d, errNode) {
    return confirmRemove(d.device_label || t('Unnamed computer')).then(function (ok) {
      if (!ok) return null;
      return postJson('/api/devices/' + encodeURIComponent(d.id) + '/revoke').then(function (r) {
        if (!r.ok) { errNode.textContent = errorText(r.body); return null; }
        return render(host, opts);
      });
    });
  }

  function reinstateComputer(host, opts, d, errNode) {
    return postJson('/api/devices/' + encodeURIComponent(d.id) + '/reinstate').then(function (r) {
      if (!r.ok) { errNode.textContent = errorText(r.body); return null; }
      return render(host, opts);
    });
  }

  function openRename(host, opts, d, row, renameBtn, errNode) {
    if (row.querySelector('.pd-edit')) return;
    renameBtn.disabled = true;
    var wrap = el('div', 'pd-edit');
    wrap.setAttribute('data-pd', 'rename');
    var input = el('input', 'pd-input');
    input.type = 'text';
    input.maxLength = 60;
    input.value = d.device_label || '';
    input.setAttribute('aria-label', t('Computer name'));
    input.setAttribute('data-pd', 'rename-input');
    var save = el('button', 'pd-btn', t('Save'));
    save.type = 'button';
    save.setAttribute('data-pd', 'rename-save');
    var cancel = el('button', 'pd-btn', t('Cancel'));
    cancel.type = 'button';
    function stop() {
      if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
      renameBtn.disabled = false;
      renameBtn.focus();
    }
    function submit() {
      save.disabled = true;
      errNode.textContent = '';
      postJson('/api/devices/' + encodeURIComponent(d.id) + '/label', { label: input.value }).then(function (r) {
        if (!r.ok) { errNode.textContent = errorText(r.body); save.disabled = false; return null; }
        return render(host, opts);
      });
    }
    save.addEventListener('click', submit);
    cancel.addEventListener('click', stop);
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); submit(); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); stop(); }
    });
    wrap.appendChild(input);
    wrap.appendChild(save);
    wrap.appendChild(cancel);
    row.appendChild(wrap);
    input.focus();
    input.select();
  }

  function computerRow(data, host, opts, d, manage, errNode) {
    var revoked = d.status === 'revoked';
    var row = el('div', 'pd-dev');
    row.setAttribute('data-pd-shop-device', String(d.id));
    row.setAttribute('data-status', revoked ? 'revoked' : 'active');
    var left = el('div');
    left.appendChild(el('p', 'pd-body', d.device_label || t('Unnamed computer')));
    var meta = metaLines(d);
    if (meta.line1) left.appendChild(el('p', 'pd-muted', meta.line1));
    var seen = el('p', 'pd-muted', meta.line2);
    seen.setAttribute('data-pd', 'last-seen');
    left.appendChild(seen);
    row.appendChild(left);

    var right = el('div', 'pd-badges');
    if (d.is_admin_device === true) {
      var mb = el('span', 'pd-badge info', t('Main computer'));
      mb.setAttribute('data-pd', 'main-badge');
      right.appendChild(mb);
    }
    if (revoked) {
      var rb = el('span', 'pd-badge bad', t('Removed from the shop'));
      rb.setAttribute('data-pd', 'removed-badge');
      right.appendChild(rb);
    }
    if (manage) {
      var acts = el('div', 'pd-acts');
      function button(label, cls, key, fn) {
        var b = el('button', 'pd-btn' + (cls ? ' ' + cls : ''), label);
        b.type = 'button';
        b.setAttribute('data-pd', key);
        b.addEventListener('click', function () { fn(b); });
        acts.appendChild(b);
        return b;
      }
      button(t('Rename'), '', 'rename', function (b) { openRename(host, opts, d, row, b, errNode); });
      if (revoked) {
        button(t('Reinstate'), '', 'reinstate', function () { reinstateComputer(host, opts, d, errNode); });
      } else if (d.is_admin_device !== true) {
        if (canBeMain(d)) button(t('Make main computer'), '', 'make-main', function () { makeMain(host, opts, d, errNode); });
        button(t('Remove'), 'danger', 'remove', function () { removeComputer(host, opts, d, errNode); });
      }
      right.appendChild(acts);
    }
    row.appendChild(right);
    return row;
  }

  function shopComputersCard(data, host, opts) {
    if (!Array.isArray(data.registry)) return null;
    var card = el('section', 'pd-card');
    card.setAttribute('data-pd', 'shop-computers');
    card.appendChild(el('h2', 'pd-h', t('Computers in this shop')));
    var others = data.registry.filter(function (d) { return d && d.is_this_device !== true; });
    var manage = mainHere(data);
    var errNode = el('p', 'pd-err');
    errNode.setAttribute('role', 'alert');
    if (!others.length) {
      card.appendChild(el('p', 'pd-muted', t('No other computer has joined this shop yet.')));
      return card;
    }
    if (!manage) {
      card.appendChild(el('p', 'pd-muted', t('Only the main computer can do this. Open this screen on the main computer.')));
    }
    var ordered = others.filter(function (d) { return d.status !== 'revoked'; })
      .concat(others.filter(function (d) { return d.status === 'revoked'; }));
    ordered.forEach(function (d) { card.appendChild(computerRow(data, host, opts, d, manage, errNode)); });
    card.appendChild(errNode);
    return card;
  }

  // The owner's escape hatch when the main computer is gone. Two deliberate
  // frictions -- the password and the typed phrase -- because this replaces
  // the computer everything else follows. The SERVER decides whether it may
  // happen (7 days of silence, lockout after wrong passwords); this dialog
  // only carries the answer back in words.
  function openTakeover(host, opts) {
    var rs = window.RetailSystem;
    if (!rs || typeof rs._wireModalA11y !== 'function' || document.getElementById('pd-takeover-modal')) return;
    var overlay = document.createElement('div');
    overlay.className = 'ret-modal-overlay';
    overlay.id = 'pd-takeover-modal';
    overlay.innerHTML =
      '<div class="ret-modal" style="max-width:26rem" id="pd-takeover-dialog" role="dialog" aria-modal="true" aria-labelledby="pd-takeover-dialog-title">' +
      '<h3 id="pd-takeover-dialog-title">' + esc(t('Take over as main computer')) + '</h3>' +
      '<p class="pd-muted">' + esc(t('Use this only if the old main computer is broken or lost. It only works after the main computer has been silent for 7 days or more.')) + '</p>' +
      '<div class="ret-field"><label for="pd-takeover-pw">' + esc(t('Password')) + '</label>' +
      '<input class="pd-input" type="password" id="pd-takeover-pw" autocomplete="current-password" /></div>' +
      '<div class="ret-field"><label for="pd-takeover-phrase">' + esc(t('Type TAKE OVER to confirm.')) + '</label>' +
      '<input class="pd-input" type="text" id="pd-takeover-phrase" dir="ltr" autocomplete="off" autocapitalize="characters" spellcheck="false" /></div>' +
      '<p class="pd-err" id="pd-takeover-err" role="alert"></p>' +
      '<div class="ret-modal-footer">' +
      '<button type="button" class="ret-btn ret-btn-ghost" onclick="RetailSystem._closeModalOverlay(this)">' + esc(t('Cancel')) + '</button>' +
      '<button type="button" class="ret-btn ret-btn-primary" id="pd-takeover-btn" disabled>' + esc(t('Take over')) + '</button>' +
      '</div></div>';
    document.body.appendChild(overlay);
    var close = rs._wireModalA11y(overlay, 'pd-takeover-dialog');
    var pw = document.getElementById('pd-takeover-pw');
    var phrase = document.getElementById('pd-takeover-phrase');
    var err = document.getElementById('pd-takeover-err');
    var btn = document.getElementById('pd-takeover-btn');
    function ready() { return pw.value !== '' && phrase.value.trim() === 'TAKE OVER'; }
    function sync() { btn.disabled = !ready(); }
    pw.addEventListener('input', sync);
    phrase.addEventListener('input', sync);
    function submit() {
      if (!ready() || btn.disabled) return;
      btn.disabled = true;
      err.textContent = '';
      askLeave('take-over-main-computer').then(function (go) {
        if (!go) { sync(); return null; }
        return postJson('/api/devices/me/take-over', { password: pw.value, confirm: phrase.value.trim() }).then(function (r) {
          if (!r.ok) {
            err.textContent = errorText(r.body);
            pw.value = '';
            sync();
            pw.focus();
            return null;
          }
          if (typeof close === 'function') close();
          toast(t('This computer is now your shop’s main computer.'), 'success');
          return refreshShell(true, host, opts);
        });
      });
    }
    btn.addEventListener('click', submit);
    [pw, phrase].forEach(function (i) {
      i.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); submit(); } });
    });
    pw.focus();
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
    var reg = thisRegistryDevice(data);
    // The registry knows who is main; the older me/joined guess is only the
    // fallback when the registry list could not be read.
    var isMain = reg ? reg.is_admin_device === true : !joined;
    me.setAttribute('data-pd', 'this-computer');
    me.setAttribute('data-main', isMain ? 'true' : 'false');
    if (reg) {
      var rm = metaLines(reg);
      if (rm.line1) meLeft.appendChild(el('p', 'pd-muted', rm.line1));
      var rSeen = el('p', 'pd-muted', rm.line2);
      rSeen.setAttribute('data-pd', 'last-seen');
      meLeft.appendChild(rSeen);
    }
    me.appendChild(meLeft);
    var meRight = el('div', 'pd-badges');
    if (isMain) {
      var mainBadge = el('span', 'pd-badge info', t('Main computer'));
      mainBadge.setAttribute('data-pd', 'main-badge');
      meRight.appendChild(mainBadge);
    } else {
      var meActs = el('div', 'pd-acts');
      var appNow = window.SubsystemApp;
      if (data.me && data.me.can_claim_admin === true && appNow && typeof appNow._claimAdminDeviceFromMenu === 'function') {
        var claimBtn = el('button', 'pd-btn', t('Make this the main computer'));
        claimBtn.type = 'button';
        claimBtn.setAttribute('data-pd', 'claim-main');
        claimBtn.addEventListener('click', function () {
          claimBtn.disabled = true;
          Promise.resolve(appNow._claimAdminDeviceFromMenu()).then(function () { return render(host, opts); });
        });
        meActs.appendChild(claimBtn);
      } else if (joined && canBeMain(reg || {}) && window.RetailSystem && typeof window.RetailSystem._wireModalA11y === 'function') {
        var takeBtn = el('button', 'pd-btn', t('Take over as main computer'));
        takeBtn.type = 'button';
        takeBtn.setAttribute('data-pd', 'take-over');
        takeBtn.addEventListener('click', function () { openTakeover(host, opts); });
        meActs.appendChild(takeBtn);
      }
      if (meActs.childNodes.length) meRight.appendChild(meActs);
    }
    me.appendChild(meRight);
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
      var shopCard = shopComputersCard(data, host, opts);
      if (shopCard) host.appendChild(shopCard);
      host.appendChild(featuresCard(data));
      host.appendChild(addonsCard(data, opts));
    });
  }

  window.PlanDevices = { render: render, prices: PRICES, errorText: errorText };
})();
