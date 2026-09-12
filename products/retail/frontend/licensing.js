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

  // i18n.js (loaded before this script in licensing.html) defines the global
  // t()/AuraI18n. Falling back to identity when it's absent keeps this file
  // loadable standalone -- e.g. retail's own test harnesses run product
  // frontend files through a bare vm sandbox with no i18n.js in it, and this
  // file worked standalone before i18n was wired in, so it still should.
  const t = (typeof window !== 'undefined' && typeof window.t === 'function')
    ? window.t
    : function (s) { return s; };

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
    // Reachable for the first time now that Owner refuses to re-activate a
    // terminal installation (owner activation.py's DEACTIVATED/REPLACED
    // guard). This is what a DECLINED activation looks like from here, and
    // "double-check the key and try again" -- what the generic
    // ACTIVATION_REJECTED copy below would have said -- is precisely the
    // wrong advice for it: the key is fine, the decision was not about the
    // key, and retrying can only ever produce the same answer.
    INSTALLATION_DEACTIVATED: 'This installation was not approved, or has since been deactivated by Action Aura. '
      + 'Re-entering the same key will not change that — please contact support to have this device re-enabled.',
    INSTALLATION_REPLACED: 'This installation has been replaced by another device. '
      + 'Please contact support if this device still needs access.',
  };

  // Client-LOCAL failures produced entirely on this device, AFTER Owner
  // already answered. These are LOCAL_REASON_CODES from
  // commercial_runtime/licensing_contracts/reason_codes.py minus the ones
  // already listed in TRANSIENT_REASON_CODES below (and minus
  // CAPABILITY_DENIED, which the capability decorator raises and /activate
  // never returns) -- kept as its own bucket because the transport gives the
  // frontend NO other way to tell them apart: routes.py::activate() turns
  // every ActivationFailed into the same HTTP 400 {reason_code}, whether the
  // code came from Owner or from our own verify_assertion().
  //
  // Treating one of these as a verdict is a real, already-seen failure: Owner
  // rotates its signing key while this install still ships a stale
  // trust_anchor.json (a condition this project has actually hit on the live
  // droplet), so Owner APPROVES, marks the installation ACTIVE and consumes a
  // paid device slot, and the assertion then fails verify_assertion() here
  // with UNKNOWN_SIGNING_KEY. Before this bucket existed that landed in the
  // rejection branch: the poll stopped, the pending marker was destroyed, and
  // the customer was told to double-check a license key that was never the
  // problem -- with re-typing it reproducing the identical message forever.
  const LOCAL_VERIFICATION_REASON_CODES = {
    UNSIGNED_RESPONSE_REJECTED: 1,
    UNKNOWN_SIGNING_KEY: 1,
    ASSERTION_VERIFICATION_FAILED: 1,
    ASSERTION_EXPIRED: 1,
    ASSERTION_NOT_YET_VALID: 1,
    ASSERTION_PRODUCT_MISMATCH: 1,
    ASSERTION_PLATFORM_MISMATCH: 1,
    ASSERTION_INSTALLATION_MISMATCH: 1,
    ASSERTION_DEVICE_MISMATCH: 1,
    ASSERTION_FORBIDDEN_FIELD: 1,
    CLOCK_ROLLBACK_SUSPECTED: 1,
    LOCAL_STATE_CORRUPT: 1,
  };

  // The only members of the set above a customer can resolve without support,
  // and so the only ones for which "check the date and time" is true advice.
  const CLOCK_FIXABLE_REASON_CODES = {
    ASSERTION_EXPIRED: 1,
    ASSERTION_NOT_YET_VALID: 1,
    CLOCK_ROLLBACK_SUSPECTED: 1,
  };

  // Deliberately says nothing about the key. It is not the key -- Owner said
  // yes.
  //
  // WHY THIS IS SPLIT (2026-09-04). One message served all twelve codes and
  // told every one of them to check the date and time. For nine that is not
  // just unhelpful, it is false and it misdirects: a real handset stranded on
  // UNKNOWN_SIGNING_KEY sent an investigation to compare clocks (they matched
  // to the identical epoch second) while the actual cause was a trust store
  // seeded once and never refreshed. Keep in step with LicensingMessages.kt
  // and app-shell.js::_activationFailureMessage.
  const LOCAL_VERIFICATION_MESSAGE = 'Action Aura approved this activation, but this computer could not verify '
    + 'the signed licence it received, so it has not been applied yet. Your license key is not the problem — do not '
    + 'replace it. Check that this computer’s date and time are correct; if they are, contact support.';

  // Everything else needs support, and gets the reason code to quote -- the
  // fastest route to the cause, and the thing whose absence forced reading it
  // out of a database by hand.
  function localVerificationMessage(reason) {
    if (CLOCK_FIXABLE_REASON_CODES[reason]) return t(LOCAL_VERIFICATION_MESSAGE);
    return t('Action Aura approved this activation, but this computer could not verify the signed licence it '
      + 'received, so it has not been applied yet. Your license key is not the problem — do not replace it, and '
      + 're-entering it cannot help. Please contact support and quote this code:') + ' ' + (reason || 'UNKNOWN') + '.';
  }

  // One place that decides what a reason_code is allowed to SAY, so the
  // "local failure is not an Owner verdict" rule cannot be honoured on one
  // screen and quietly forgotten on the next.
  function reasonMessage(reason) {
    if (LOCAL_VERIFICATION_REASON_CODES[reason]) return localVerificationMessage(reason);
    return t(REASON_MESSAGES[reason] || REASON_MESSAGES.ACTIVATION_REJECTED);
  }

  // Must match SubsystemApp.PENDING_ACTIVATION_KEY in app-shell.js EXACTLY.
  // This page is a separate document with its own script scope and no module
  // system (vanilla <script>, no bundler), so the literal is duplicated on
  // purpose -- there is nothing to import from. Changing one without the
  // other silently re-opens the PENDING trap described on renderAwaiting-
  // Approval() below, and does it quietly: nothing throws, the user just
  // gets asked for the same key forever again.
  const PENDING_ACTIVATION_KEY = 'aura.licensing.pendingActivation';

  // The submitted license key, held in a module-scoped variable and NOWHERE
  // else -- not localStorage, not sessionStorage, not the URL. It is
  // credential material: it is the thing that proves entitlement to this
  // product, and the backend goes out of its way to stop holding it (routes.py
  // activate() nulls it in a `finally`; perform_activation() drops its own
  // reference too -- see the "Part G" comments there). A frontend that wrote
  // the same value to disk, where it outlives the process and is readable by
  // anything else running as this user, would quietly undo all of that.
  //
  // The direct, accepted consequence: this is EMPTY after any reload or
  // restart, and after the navigation from app-shell.js's registration modal
  // (a different document, a different script scope -- nothing is shared but
  // the marker below). That is not a case to paper over. Only the key can
  // resolve a held activation (see pollActivationApproval), so a screen that
  // promises automatic progress without one is a lie; render() picks the
  // honest screen instead. See renderActivationForm's `pendingWithoutKey`.
  let pendingKey = '';

  // A pending approval nobody ever ruled on is not evidence forever. Without
  // an expiry, a marker left behind by an install whose licensing.db was later
  // wiped or re-provisioned would keep this page claiming "your key was
  // received, waiting for approval" for a submission that no longer exists on
  // either side -- the browser profile survives that wipe (same origin,
  // http://127.0.0.1:<port>), the local licensing state does not. Seven days
  // is far past any real manual-review turnaround, so ageing out here can only
  // ever affect a marker that is genuinely dead.
  const PENDING_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

  // How often the awaiting-approval screen re-submits the held activation to
  // find out whether Owner has ruled on it yet. Each tick is a real Owner
  // round trip that does signature verification and DB writes on their side,
  // so it is deliberately slow -- it exists to make the "we keep checking for
  // you" promise TRUE (see renderAwaitingApproval), not to be instant.
  const AWAITING_POLL_MS = 30000;
  let awaitingPollTimer = null;
  // Bumped by stopAwaitingPoll(); a poll captures it before its await and
  // bails if it changed. clearInterval() cannot cancel a request that is
  // already in flight, so without this a response for the OLD key can land
  // after the user has already pressed "Use a different key" and is halfway
  // through typing a new one, and repaint `content` out from under them.
  let awaitingPollGeneration = 0;

  // Every screen-level render must call this. The awaiting-approval screen is
  // the only thing on this page that owns a timer, and it can be re-entered
  // (poll -> render -> awaiting again, or a "use a different key" round trip);
  // without an explicit stop each render stacks another interval on top of
  // the previous one and the page quietly turns into an activation flood.
  function stopAwaitingPoll() {
    awaitingPollGeneration += 1;
    if (awaitingPollTimer !== null) {
      clearInterval(awaitingPollTimer);
      awaitingPollTimer = null;
    }
  }

  // localStorage/sessionStorage throw outright (rather than returning null) in
  // some restricted webview and private-browsing contexts -- this page is
  // served inside the pywebview desktop window too, so that is not
  // hypothetical. A licensing nicety must never be able to take the page down
  // with it, so every access below is wrapped.
  //
  // memoryPending is the in-document fallback for exactly that case. When
  // Storage is unavailable every write silently no-ops, and the marker
  // onActivateClicked() wrote a few hundred milliseconds ago would be
  // unreadable by the very next render() in the same document -- i.e. the page
  // would ask again for the key it just accepted, with nothing surfaced
  // anywhere to explain why. It cannot survive a navigation (nothing
  // client-side can once Storage is gone), but it makes the single-document
  // flow correct instead of silently broken.
  let memoryPending = null;

  function readPendingRecord() {
    let rec = memoryPending;
    if (!rec) {
      let raw = null;
      try { raw = localStorage.getItem(PENDING_ACTIVATION_KEY); } catch (e) {}
      if (!raw) return null;
      // Anything that is not the record shape this build writes -- a bare '1'
      // from an earlier build, a hand-edited value -- is discarded rather than
      // trusted: it carries no timestamp, so it could never be aged out, and
      // an un-ageable marker is precisely the stale-marker failure this record
      // shape exists to bound. Falling back to the key form costs one extra
      // ask; trusting it costs a screen the user has no way to leave.
      // JSON.parse does NOT throw on a bare '1' left by an earlier build -- it
      // returns the number 1 -- so the shape check below, not the catch, is
      // what rejects it.
      try { rec = JSON.parse(raw); } catch (e) { rec = null; }
      if (!rec || typeof rec !== 'object') return null;
    }
    // ISO-8601 on the wire (app-shell.js writes it; see _markActivationPending
    // there for why). An unparseable `at` carries no age, and a marker that can
    // never age out is precisely the stale-marker failure the timestamp exists
    // to bound -- so it is discarded rather than trusted.
    const at = Date.parse(rec.at);
    if (isNaN(at) || Date.now() - at > PENDING_MAX_AGE_MS) {
      clearActivationPending();
      return null;
    }
    return rec;
  }

  // installationId comes off the 202 body (routes.py attaches it) -- kept so
  // the awaiting screen can still name the installation after a reload, when
  // GET /api/licensing/status has none to give (present_status(None) omits it
  // entirely for a device with no local state record, which is every PENDING
  // device).
  // `at` is ISO-8601 -- byte-for-byte the record shape app-shell.js writes (see
  // its _markActivationPending), because either document may write a marker the
  // other one reads. Never the license key: that stays in `pendingKey`, memory
  // only.
  function markActivationPending(installationId) {
    const rec = { v: 1, at: new Date().toISOString(), installation_id: installationId || null };
    memoryPending = rec;
    try { localStorage.setItem(PENDING_ACTIVATION_KEY, JSON.stringify(rec)); } catch (e) {}
  }

  function clearActivationPending() {
    memoryPending = null;
    pendingKey = '';
    try { localStorage.removeItem(PENDING_ACTIVATION_KEY); } catch (e) {}
  }

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

  // ── Confirm dialog (replaces native confirm()) ──────────────────────────
  // Native confirm() cannot be styled, cannot be mirrored for Arabic, and
  // looks like a browser warning rather than part of the product -- see
  // RetailSystem._confirm (subsystem-retail.js) for the full rationale. This
  // page cannot call that (or SubsystemApp.confirm in app-shell.js): it is a
  // standalone document with no shared shell (see the file header), so this
  // mirrors the SAME Promise<boolean> contract locally instead -- true on
  // Confirm (click or Enter), false on Cancel, Escape, or a click on the
  // overlay itself. Built with el()/textContent only, matching this file's
  // no-innerHTML policy (see header): there is no XSS-sink here either.
  //
  // This page has no shared SubsystemApp shell (see the file header), but it
  // loads i18n.js directly (same t()/[data-i18n] mechanism the shell uses),
  // so Arabic installs still get a translated, mirrored dialog here.
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

  function stateBadge(state) {
    const info = STATE_LABELS[state] || { text: state, cls: 'state-neutral' };
    return el('span', { className: 'state-badge ' + info.cls, text: t(info.text) });
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
    stopAwaitingPoll();
    clearChildren(content);
    const retryBtn = el('button', { text: t('Retry') });
    retryBtn.addEventListener('click', refresh);
    content.appendChild(el('div', {}, [stateBadge('NOT_CONFIGURED')]));
    content.appendChild(el('p', { text: t('Owner Service Temporarily Unavailable — could not check licensing status.') }));
    content.appendChild(retryBtn);
  }

  // Mirrors SubsystemApp._needsActivation() in app-shell.js EXACTLY -- keep
  // the two in step (separate documents, separate script scopes, nothing to
  // import).
  //
  // This copy used to read `state === 'NOT_CONFIGURED' || state ===
  // 'ACTIVATION_REQUIRED'`: it omitted 'ACTIVATING', while app-shell's boot
  // gate counted it. A device sitting in ACTIVATING was therefore redirected
  // HERE by that gate and then shown the read-only status card instead of
  // the key form -- a genuine dead end with no way forward from either
  // screen. The `!status.detail` term matters just as much: NOT_CONFIGURED
  // *with* a detail field means licensing is genuinely unconfigured for this
  // build (see the longer explanation in renderActivationForm below), which
  // is a permanent condition, not a pre-activation one, and must not be
  // treated as something a license key can resolve.
  function needsActivation(status) {
    if (!status) return false;
    const state = status.current_state;
    return state === 'ACTIVATION_REQUIRED'
      || state === 'ACTIVATING'
      || (state === 'NOT_CONFIGURED' && !status.detail);
  }

  function render(status) {
    stopAwaitingPoll();
    const state = status.current_state;
    if (needsActivation(status)) {
      // A key was already submitted and Owner answered 202 PENDING. Asking
      // for it again is the trap -- show what's actually happening instead.
      //
      // BOTH halves are required, and that is the whole design. The marker
      // says a submission exists; the key is the only thing that can actually
      // resolve it (renderAwaitingApproval explains why). Marker but no key --
      // any reload, any restart, or arrival here from app-shell.js's
      // registration modal, which is a separate document -- means this window
      // cannot make progress on its own, and the honest screen is the key form
      // WITH that explained, never an awaiting screen promising an automatic
      // check it has no way to perform.
      const pending = readPendingRecord();
      if (pending && pendingKey) {
        renderAwaitingApproval(status, pending);
        return;
      }
      renderActivationForm(state, status, !!pending);
      return;
    }

    // Past this point the server says this device does not need activating --
    // it either has a real license state or licensing is not configured for
    // this build at all. Either way any marker is now provably stale (the
    // submission it stood for has been decided, or never applied here), and a
    // stale marker is exactly what makes the next legitimate key entry render
    // the wrong screen. Retire it here, at the one place that has just been
    // told authoritatively that it is spent.
    clearActivationPending();

    // NOT_CONFIGURED that survived needsActivation() above = the genuinely-
    // unconfigured build (it carried a `detail`). There is no license status
    // to show for it, and renderActivationForm's own genuinelyUnconfigured
    // branch is the screen that explains the situation, so it still owns
    // this case -- it just isn't reached as "needs activating" any more.
    if (state === 'NOT_CONFIGURED') {
      renderActivationForm(state, status);
      return;
    }
    renderLicenseStatus(status);
  }

  // ── Awaiting-approval screen ──────────────────────────────────────────────
  // Reached when this device still needs activation AND a key was already
  // submitted and came back 202 PENDING (Owner is holding it for a human to
  // approve). Before this screen existed the user was hard-trapped: on
  // PENDING the backend deliberately persists NO local state record (see
  // commercial_runtime/licensing_contracts/activation.py), so GET
  // /api/licensing/status keeps answering a bare NOT_CONFIGURED, app-shell's
  // boot gate keeps redirecting here, and this page kept re-rendering the key
  // form. The only move available was to submit the SAME key again, collect
  // another 202, and be shown the form again. Forever.
  //
  // The old PENDING message also told the user "We'll keep checking
  // automatically -- no action needed right now." Nothing in the codebase
  // did any such thing; it was simply untrue. This screen is what makes that
  // sentence true.
  //
  // WHY it polls /activate and NOT /check-in -- this screen's first cut got
  // that wrong, and the bug was completely invisible from the frontend:
  // POST /api/licensing/check-in short-circuits at routes.py:157 with
  //     if not signer.has_key() or state_repository.load() is None:
  //         return jsonify({"current_state": "ACTIVATION_REQUIRED"}), 200
  // and a PENDING device is EXACTLY the `load() is None` case, because
  // activation.py::ingest_activation_response raises ActivationPending before
  // it ever reaches state_repository.save(). So check-in hands back a canned
  // ACTIVATION_REQUIRED without making any Owner call whatsoever (pinned on
  // purpose by test_routes.py::test_checkin_before_activation_reports_
  // activation_required). Polling it could never observe an approval, and
  // never a rejection either -- the screen would have repainted "still
  // waiting" forever and shipped a fresh false promise of exactly the class
  // it was written to remove. Re-POSTing /activate is the ONLY thing that
  // resolves a held activation; activation.py's own comment says so ("the
  // next activation retry, same idempotency_key, Owner-side self-healing, is
  // what eventually resolves this"). That is why this screen runs on the
  // submitted KEY and not merely on the pending marker, and why render()
  // above refuses to show it without one.
  function renderAwaitingApproval(status, pending) {
    stopAwaitingPoll();            // re-entrant: never stack intervals
    clearChildren(content);

    content.appendChild(el('div', {}, [stateBadge('ACTIVATING')]));
    content.appendChild(el('p', {
      text: t('Your license key was received. This activation is waiting for approval '
        + 'from Action Aura before this installation can be used. You do not need to '
        + 'enter the key again in this window.'),
    }));
    content.appendChild(el('p', {
      id: 'awaiting-status',
      text: t('Checking with the licensing service automatically every 30 seconds…'),
    }));

    const installationId = (status && status.installation_id)
      || (pending && pending.installation_id);
    if (installationId) {
      const dl = el('dl');
      addStatusRow(dl, t('Installation'), installationId);
      content.appendChild(dl);
    }

    const row = el('div', { className: 'row' });

    // Not 'awaiting-checkin-btn' any more: this button re-submits the
    // activation, it does not check in. The old id named the route that could
    // never resolve this screen -- see the block comment above.
    const checkNowBtn = el('button', { id: 'awaiting-recheck-btn', className: 'secondary', text: t('Check Now') });
    checkNowBtn.addEventListener('click', () => {
      clearMessage();
      pollActivationApproval(true);
    });
    row.appendChild(checkNowBtn);

    // The escape hatch, and the reason this screen isn't just a
    // better-looking trap. A customer who mistyped the key, or was issued one
    // Owner is never going to approve, has to be able to get back to the form
    // under their own power -- clearing the marker is all that takes.
    const differentKeyBtn = el('button', { id: 'use-different-key-btn', className: 'secondary', text: t('Use a different key') });
    differentKeyBtn.addEventListener('click', () => {
      stopAwaitingPoll();
      clearActivationPending();
      clearMessage();
      renderActivationForm((status && status.current_state) || 'ACTIVATION_REQUIRED', status || {});
    });
    row.appendChild(differentKeyBtn);

    content.appendChild(row);

    // Explicit `false`, not a bare function reference: `fromButton` decides
    // whether a transient failure is allowed to paint the message area, and an
    // automatic tick must never do that (see pollActivationApproval).
    awaitingPollTimer = setInterval(() => pollActivationApproval(false), AWAITING_POLL_MS);
  }

  // Reason codes that mean "we could not get an answer", never "the answer is
  // no". A held activation has to survive every one of these untouched:
  // dropping the marker on a DNS blip would strand the user back on a key form
  // for a submission Owner is still perfectly willing to approve, which is the
  // same re-ask loop from the other direction.
  const TRANSIENT_REASON_CODES = {
    NETWORK_UNAVAILABLE: 1,
    REQUEST_TIMED_OUT: 1,
    TLS_VERIFICATION_FAILED: 1,
    SERVICE_TEMPORARILY_UNAVAILABLE: 1,
    SIGNING_KEY_UNAVAILABLE: 1,
    RATE_LIMITED: 1,
    MALFORMED_RESPONSE: 1,
    DEVICE_KEY_UNAVAILABLE: 1,
  };

  // `fromButton` is true only for the manual "Check Now" press -- the
  // interval passes no argument. Automatic ticks stay silent about transient
  // failures on purpose: a network blip every 30s must not paint the screen
  // red, because nothing is actually wrong with the pending activation.
  async function pollActivationApproval(fromButton) {
    const generation = awaitingPollGeneration;
    const key = pendingKey;
    if (!key) {
      // Belt and braces: render() will not open this screen without a key in
      // memory, so this should be unreachable. If it ever is reached, the one
      // unacceptable outcome is carrying on pretending to check -- there is
      // nothing to check WITH. Stop, and let refresh() re-derive the honest
      // screen (which will be the key form with the already-submitted framing).
      stopAwaitingPoll();
      await refresh();
      return;
    }

    let result;
    try {
      result = await apiPost('/api/licensing/activate', { license_key: key });
    } catch (e) {
      if (generation === awaitingPollGeneration && fromButton) {
        showMessage(t(REASON_MESSAGES.NETWORK_UNAVAILABLE), 'error');
      }
      return;
    }
    // The user left this screen (or a new render replaced it) while the
    // request was in flight -- clearInterval cannot recall an outstanding
    // fetch, so the answer to a question nobody is asking any more is dropped
    // here rather than repainting whatever is on screen now.
    if (generation !== awaitingPollGeneration) return;

    if (result.status === 202 && result.body.result === 'PENDING') {
      // Owner still hasn't ruled on it. Stay put, say so honestly, tick again.
      const line = document.getElementById('awaiting-status');
      if (line) {
        line.textContent = t('Still waiting for approval. Last checked at')
          + ' ' + new Date().toLocaleTimeString() + '.';
      }
      return;
    }

    if (result.status === 200 && result.body.result === 'SUCCESS') {
      // Approved: this device now has a real, verified license state, so the
      // marker and the stored key have both done their job and must go --
      // leaving them set would show this screen again the next time the device
      // legitimately needs a key.
      stopAwaitingPoll();
      clearActivationPending();
      showMessage(t('This installation has been approved and activated.'), 'info');
      if (new URLSearchParams(location.search).get('gate') === '1') {
        // Same bounce the SUCCESS path in onActivateClicked() does: the user
        // was sent here by app-shell's boot gate and belongs back in the app.
        setTimeout(() => { location.href = '/'; }, 900);
        return;
      }
      await refresh();
      return;
    }

    const reason = result.body.reason_code || 'ACTIVATION_REJECTED';
    if (TRANSIENT_REASON_CODES[reason]) {
      if (fromButton) {
        showMessage(t(REASON_MESSAGES[reason] || REASON_MESSAGES.SERVICE_TEMPORARILY_UNAVAILABLE), 'error');
      }
      return;
    }

    // NOT a verdict: Owner answered SUCCESS and this device failed to verify
    // or persist that answer (see LOCAL_VERIFICATION_REASON_CODES). The held
    // activation is genuinely resolved and APPROVED on Owner's side, so the
    // one thing that must not happen here is what the rejection branch below
    // does -- destroying the marker and telling the customer their key was no
    // good. Keep the marker, keep the key, and keep the timer running: unlike
    // a rejection this can and does self-heal without the user touching
    // anything (a refreshed trust anchor, a corrected clock), and the next
    // tick is what picks that up. Speak up on EVERY tick, not just `fromButton`
    // -- this needs someone to act on it, and staying silent after Owner has
    // already approved would leave the screen claiming "still waiting" for a
    // wait that is over.
    if (LOCAL_VERIFICATION_REASON_CODES[reason]) {
      showMessage(localVerificationMessage(reason), 'error');
      return;
    }

    // A verdict: Owner reviewed the held activation and declined it. The
    // marker's entire job was to stop this page asking for a key that had
    // already been accepted for review; once the review says no, that job is
    // over, and leaving it set would park the user on "waiting for approval"
    // for an approval that is never coming. Surfacing a rejection at ALL is
    // new -- the check-in poll this screen originally used could not observe
    // one, so a key Owner would never approve looked identical to one still
    // under review, forever. It is also genuinely reachable for a REJECTION
    // for the first time: Owner used to re-activate the installation its own
    // staff had just rejected, so this branch could only ever fire for a key
    // that failed some other check (see the DEACTIVATED/REPLACED guard in
    // owner activation.py, which is what actually turns a staff rejection
    // into the INSTALLATION_DEACTIVATED answer this branch renders).
    stopAwaitingPoll();
    clearActivationPending();
    showMessage(reasonMessage(reason), 'error');
    await refresh();
  }

  // `pendingWithoutKey`: a marker says a key from this installation is already
  // awaiting Owner's approval, but this window has no copy of that key and so
  // cannot check on it (see render()). The form is the right screen; it just
  // must not look like the first submission silently vanished.
  function renderActivationForm(state, status, pendingWithoutKey) {
    stopAwaitingPoll();   // screen entry point: never leave a timer behind
    clearChildren(content);
    content.appendChild(el('div', {}, [stateBadge(state)]));

    // NOT_CONFIGURED is ambiguous by itself: the backend returns it both
    // when licensing is genuinely unconfigured for this build AND when
    // it's configured but this device has simply never activated (no
    // state record yet) -- see routes.py::_not_configured_response() vs
    // status_presenter.py::present_status(None). Only the first case
    // attaches a "detail" field, so its presence is the real signal --
    // showing "not connected to a licensing server" on a device that's
    // actually just pre-activation is confusing and wrong (caught via a
    // real screenshot of the gate feature this message shares).
    const genuinelyUnconfigured = state === 'NOT_CONFIGURED' && !!status.detail;
    const detailText = genuinelyUnconfigured
      ? t('This installation is not yet connected to a licensing server. Owner licensing is not configured for this build.')
      : t('Enter your Aura Retail license key to activate this installation.');
    content.appendChild(el('p', { text: detailText }));

    if (pendingWithoutKey && !genuinelyUnconfigured) {
      // Asked once more, but never blind: without this the user sees a bare
      // key form and reasonably concludes their first submission was lost,
      // when in fact it is sitting in Owner's approval queue. Re-submitting is
      // genuinely safe -- Owner self-heals a repeat activation for a held
      // installation rather than opening a second request (see activation.py's
      // ActivationPending comment) -- and it is also the only action that can
      // move this device forward from here.
      content.appendChild(el('p', {
        text: t('A license key from this installation is already waiting for approval from '
          + 'Action Aura. This window no longer has a copy of it, so enter the same key '
          + 'again to check whether it has been approved — re-submitting it is safe and '
          + 'does not create a second request.'),
      }));
    }

    content.appendChild(el('label', { htmlFor: 'license-key-input', text: t('License key') }));
    const input = el('input', {
      id: 'license-key-input', type: 'text', autocomplete: 'off', spellcheck: false,
      placeholder: 'AURA-RETAIL-XXXX-YYYY-ZZZZ',
    });
    content.appendChild(input);

    const activateBtn = el('button', { id: 'activate-btn', text: t('Activate') });
    activateBtn.addEventListener('click', () => onActivateClicked(input, activateBtn));
    content.appendChild(activateBtn);
  }

  async function onActivateClicked(input, btn) {
    const key = input.value.trim();
    if (!key) {
      showMessage(t('Please enter a license key.'), 'error');
      return;
    }

    clearMessage();
    btn.disabled = true;
    clearChildren(btn);
    btn.appendChild(el('span', { className: 'spinner' }));
    btn.appendChild(document.createTextNode(' ' + t('Activating…')));

    const badge = content.querySelector('.state-badge');
    if (badge) badge.replaceWith(stateBadge('ACTIVATING'));

    try {
      const { status, body } = await apiPost('/api/licensing/activate', { license_key: key });
      // The key never lingers in this scope longer than needed to send it.
      input.value = '';
      if (status === 200 && body.result === 'SUCCESS') {
        // Fully activated -- retire any marker left behind by an earlier
        // PENDING submission on this device.
        clearActivationPending();
        showMessage(t('Activation successful.'), 'info');
        // Reached via app-shell.js's pre-login activation gate (?gate=1) --
        // bounce back to '/' so init() re-runs and, now that this device is
        // activated, proceeds straight to setup/login. The settings-accessed
        // path (Settings -> Licensing, already logged in) has no ?gate=1 and
        // deliberately stays here showing the now-active status card, same
        // as before this gate existed.
        if (new URLSearchParams(location.search).get('gate') === '1') {
          setTimeout(() => { location.href = '/'; }, 900);
        }
      } else if (status === 202 && body.result === 'PENDING') {
        // Phase 8 Part O: Owner is holding this activation for manual
        // approval, not rejecting it -- a distinct, non-error state.
        //
        // This branch used to showMessage(...) and then fall through to the
        // refresh() at the bottom of this function, which re-rendered the key
        // form (status still reads a bare NOT_CONFIGURED -- PENDING persists
        // no local state record). The user's only remaining move was to
        // re-enter the same key, get the same 202, and be handed the same
        // form again, forever. Remember the submission and hand over to the
        // awaiting-approval screen, which is the one that actually polls
        // Owner. The early return is load-bearing: it stops refresh() below
        // from immediately undoing this.
        //
        // The key is kept IN MEMORY ONLY (see `pendingKey`'s declaration)
        // because re-POSTing /activate with it is the ONLY thing that can
        // resolve a held activation -- renderAwaitingApproval's comment has the
        // full reasoning. It is dropped the moment Owner rules either way, and
        // it is never written anywhere that outlives this document; the marker
        // beside it deliberately carries no key material at all.
        pendingKey = key;
        markActivationPending(body.installation_id);
        clearMessage();
        renderAwaitingApproval(body, readPendingRecord());
        return;
      } else {
        // reasonMessage(), not a bare REASON_MESSAGES lookup: this branch is
        // reached by a LOCAL verification failure too (Owner returned SUCCESS,
        // verify_assertion() then rejected the assertion on this device --
        // routes.py flattens both into the same 400 {reason_code}), and the
        // generic "double-check the key" fallback is the wrong thing to say
        // about a key Owner just accepted. Note the pending marker is
        // deliberately NOT cleared here -- only the SUCCESS branch above
        // retires it -- so a device that submitted a key, got 202 PENDING and
        // later trips this keeps its "already waiting for approval" framing.
        const reason = body.reason_code || 'ACTIVATION_REJECTED';
        showMessage(reasonMessage(reason), 'error');
      }
    } catch (e) {
      showMessage(t(REASON_MESSAGES.NETWORK_UNAVAILABLE), 'error');
    }
    await refresh();
  }

  function addStatusRow(dl, label, value) {
    dl.appendChild(el('dt', { text: label }));
    dl.appendChild(el('dd', { text: value }));
  }

  function renderLicenseStatus(status) {
    stopAwaitingPoll();   // screen entry point: never leave a timer behind
    const state = status.current_state;
    const canDeactivate = state !== 'DEVICE_DEACTIVATED';

    clearChildren(content);
    content.appendChild(el('div', {}, [stateBadge(state)]));

    const dl = el('dl');
    addStatusRow(dl, t('Product'), status.product_code || '—');
    if (status.installation_id) addStatusRow(dl, t('Installation'), status.installation_id);
    if (status.license_status) addStatusRow(dl, t('License status'), status.license_status);
    if (status.last_successful_checkin_at) addStatusRow(dl, t('Last check-in'), status.last_successful_checkin_at);
    content.appendChild(dl);

    if (state === 'RESTRICTED' || state === 'GRACE_PERIOD' || state === 'WARNING') {
      content.appendChild(el('p', {
        text: t('Some features are limited in this state. Existing records remain fully viewable, and backup/restore/export remain available.'),
      }));
    }
    if (state === 'SUSPENDED' || state === 'REVOKED' || state === 'EXPIRED') {
      content.appendChild(el('p', {
        text: t('Commercial features are unavailable. Your existing data is safe and remains viewable; backup, restore, and export remain available.'),
      }));
    }

    const row = el('div', { className: 'row' });
    const checkinBtn = el('button', { id: 'checkin-btn', className: 'secondary', text: t('Check Now') });
    checkinBtn.addEventListener('click', () => onCheckInClicked(checkinBtn));
    row.appendChild(checkinBtn);

    if (canDeactivate) {
      const deactivateBtn = el('button', { id: 'deactivate-btn', className: 'danger', text: t('Deactivate This Device') });
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
        showMessage(t('Check-in complete.'), 'info');
      } else if (status === 200) {
        // The route reports current status either way (Part AD: a network
        // blip must not look like an error state) -- but this specific
        // attempt did not actually reach Owner, and the UI should say so
        // honestly rather than claim success.
        showMessage(t('Could not reach the licensing service. Your current status is unchanged.'), 'error');
      } else {
        showMessage(t(REASON_MESSAGES[body.reason_code] || REASON_MESSAGES.SERVICE_TEMPORARILY_UNAVAILABLE), 'error');
      }
    } catch (e) {
      showMessage(t(REASON_MESSAGES.NETWORK_UNAVAILABLE), 'error');
    }
    await refresh();
  }

  async function onDeactivateClicked() {
    const ok = await confirmDialog({
      title: t('Deactivate this device?'),
      message: t('You will need to reactivate with a license key to use commercial features again.'),
      confirmLabel: t('Deactivate'),
      danger: true,
    });
    if (!ok) {
      return;
    }
    clearMessage();
    try {
      const { status, body } = await apiPost('/api/licensing/deactivate', {});
      if (status === 200) {
        // Deactivating is an explicit "I want to enter a key again" -- drop
        // any stale pending marker so the next activation lands on the key
        // form rather than an awaiting-approval screen for a submission that
        // is no longer relevant to this device.
        clearActivationPending();
        showMessage(t('This device has been deactivated.'), 'info');
      } else {
        showMessage(t(REASON_MESSAGES[body.reason_code] || REASON_MESSAGES.ACTIVATION_REJECTED), 'error');
      }
    } catch (e) {
      showMessage(t(REASON_MESSAGES.NETWORK_UNAVAILABLE), 'error');
    }
    await refresh();
  }

  // Gate the first render on the translation dictionaries the same way
  // index.html gates SubsystemApp.init() -- see i18n.js's docstring/apply():
  // this is what lets the very first paint (STATE_LABELS/REASON_MESSAGES
  // etc., all resolved through t() above) come out already in the right
  // language instead of flashing English before the DOM-sweep catches up.
  // Falls back to an immediate refresh() when AuraI18n isn't present (see
  // the t() fallback above for why that has to be possible).
  if (typeof window !== 'undefined' && window.AuraI18n) {
    window.AuraI18n.load().then(refresh);
  } else {
    refresh();
  }
})();
