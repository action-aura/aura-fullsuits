/**
 * Aura Retail -- "Sign in with your Aura account" (new customer flow, plan S6,
 * 2026-10-31). Loaded by licensing.html BEFORE licensing.js, which mounts it as
 * the FIRST way to activate a computer (a code and a licence key stay below it).
 *
 * The steps, each a screen in the same card:
 *   1. e-mail + password        -> POST /api/licensing/account/sign-in
 *   2. forced password change   -> POST /api/licensing/account/password   (only if Owner says so)
 *   3. the contract (a modal that cannot be dismissed, Arabic first)
 *                               -> POST /api/licensing/account/contract/accept
 *   4. which branch (only if the account has several licences)
 *   5. activate                 -> POST /api/licensing/account/activate
 *
 * WHAT THIS FILE NEVER HOLDS. The Owner ticket stays in the Python process; this
 * page only ever holds `flow_id`, an opaque handle. The typed password lives in
 * the <input> until the request is sent and is then blanked -- it is never put in
 * a variable that outlives the click handler, storage, the URL or a log line.
 * (retail_account_signin_test.js greps for that.)
 *
 * No innerHTML anywhere in this file: every string reaches the DOM through
 * textContent, so there is no XSS sink to audit -- including the contract body,
 * which is text that came over the network.
 */
(function () {
  'use strict';

  const t = (typeof window !== 'undefined' && typeof window.t === 'function')
    ? window.t
    : function (s) { return s; };

  // Must match SubsystemApp.ROLE_PENDING_KEY in app-shell.js EXACTLY (separate
  // document, separate script scope, nothing to import -- same reason as
  // licensing.js's PENDING_ACTIVATION_KEY). Written when an account activation
  // succeeds; app-shell.js reads it to show the "which computer is this?" step.
  const ROLE_PENDING_KEY = 'aura.firstrun.rolePending';

  // Best effort: the backend refuses once a shop has an admin (409), and a
  // network hiccup must never block an activation that already succeeded.
  function postRolePending(email) {
    return fetch('/api/onboarding/role-step', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ state: 'pending', email: email || '' }),
    }).catch(function () { /* the localStorage copy still stands */ });
  }

  // rc.19 (dress rehearsal 2026-10-31): the activation screen printed the raw plan
  // code "AURA_RETAIL_P1_YEARLY". Owner now sends `plan_name` (and `plan_name_ar`)
  // next to `plan_code`; these are the fallback for an Owner that does not yet,
  // and the same three names the Android app maps (AccountFlow.kt planDisplayName).
  const PLAN_NAMES = {
    AURA_RETAIL_P1_YEARLY: { en: 'Package 1 (yearly)', ar: 'الباقة 1 (سنوية)' },
    AURA_RETAIL_P2_MONTHLY_12: { en: 'Package 2 (monthly, 12-month contract)', ar: 'الباقة 2 (شهرية، عقد 12 شهراً)' },
    AURA_RETAIL_P3_MONTHLY_24: { en: 'Package 3 (monthly, 24-month contract)', ar: 'الباقة 3 (شهرية، عقد 24 شهراً)' },
  };

  function planDisplayName(lic) {
    const ar = !!(window.AuraI18n && window.AuraI18n.current === 'ar');
    const sent = ar ? (lic.plan_name_ar || lic.plan_name) : (lic.plan_name || lic.plan_name_ar);
    if (sent) return sent;
    const local = PLAN_NAMES[lic.plan_code];
    // An unknown code is never shown raw: a code is not a name.
    return local ? (ar ? local.ar : local.en) : '';
  }

  // "<shop or branch label> · <package name>"; either half may be missing. The
  // placeholder ("your shop" / "Branch 2") is used only when there is neither a
  // label nor a package name -- or always, in the branch picker, where several
  // licences of one package must stay tellable apart.
  function licenceLabel(lic, fallbackLabel, alwaysFallback) {
    const parts = [lic.branch_label || (alwaysFallback ? fallbackLabel : ''), planDisplayName(lic)].filter(Boolean);
    return parts.length ? parts.join(' · ') : fallbackLabel;
  }

  // rc.19 (dress rehearsal 2026-10-31): reloading on the forced-password step silently
  // dropped the person back on the sign-in form. The ticket cannot be kept (memory only,
  // by design), so we keep only a NON-SECRET note -- which step, and when -- and explain
  // after the reload. Nothing here is a credential, a ticket or a flow id.
  const MIDFLOW_KEY = 'aura.licensing.accountMidFlow';
  const MIDFLOW_MAX_AGE_MS = 30 * 60 * 1000;
  const MIDFLOW_MESSAGES = {
    password: 'The page was reloaded before your new password was saved, so please sign in again. Use the same email and the temporary password your seller gave you.',
    contract: 'The page was reloaded before the agreement was accepted, so please sign in again. Use the email and the new password you chose.',
    activate: 'The page was reloaded before this computer was activated, so please sign in again. Use the email and the password you chose.',
  };

  function markMidFlow(step) {
    try { localStorage.setItem(MIDFLOW_KEY, JSON.stringify({ v: 1, at: Date.now(), step: step })); } catch (e) { /* storage blocked: no note, nothing lost */ }
  }

  function clearMidFlow() {
    try { localStorage.removeItem(MIDFLOW_KEY); } catch (e) { /* storage blocked */ }
  }

  // The step the person was on when the page went away, or '' -- and the note is consumed.
  function readMidFlow() {
    let step = '';
    try {
      const rec = JSON.parse(localStorage.getItem(MIDFLOW_KEY) || 'null');
      if (rec && rec.v === 1 && typeof rec.at === 'number' && Date.now() - rec.at < MIDFLOW_MAX_AGE_MS
          && Object.prototype.hasOwnProperty.call(MIDFLOW_MESSAGES, rec.step)) step = rec.step;
    } catch (e) { /* unreadable note: treat as none */ }
    clearMidFlow();
    return step;
  }

  const MIN_PASSWORD_LENGTH = 10;   // Owner's customer policy; Owner stays the authority.

  const MESSAGES = {
    ACCOUNT_SIGN_IN_FAILED: 'The email or password is not right. Check them and try again.',
    RATE_LIMITED: 'Too many attempts. Please wait a few minutes and try again.',
    TRIAL_REQUIRES_UPDATE: 'This version of Aura Retail is too old to start a trial. Please download the newest version from actionaura.me and install it.',
    CONTRACT_HASH_MISMATCH: 'The agreement text could not be checked, so it was not shown. Please try again, or contact support.',
    ACCOUNT_FLOW_EXPIRED: 'Your sign-in timed out. Please sign in again.',
    PASSWORD_CHANGE_REQUIRED: 'You must choose a new password first.',
    CONTRACT_ACCEPTANCE_REQUIRED: 'The agreement must be accepted before this computer can be activated.',
    CONSENT_DATA_OUTSIDE_JORDAN_REQUIRED: 'Please tick the box about storing data outside Jordan to continue.',
    ACTIVATION_GRANT_INVALID: 'This computer could not be activated with that account. Sign in again, or contact your seller.',
    DEVICE_LIMIT_REACHED: 'This account has reached its computer limit. Contact your seller to add a device.',
    PASSWORD_POLICY_FAILED: 'That password is not allowed. Choose a longer one you have not used before.',
    INVALID_REQUEST: 'Please check the information entered.',
    NETWORK_UNAVAILABLE: 'Could not reach the licensing service. Check your internet connection and try again.',
    REQUEST_TIMED_OUT: 'The request timed out. Please try again.',
    SERVICE_TEMPORARILY_UNAVAILABLE: 'The licensing service is temporarily unavailable. Please try again shortly.',
    TLS_VERIFICATION_FAILED: 'A secure connection to the licensing service could not be established.',
    MALFORMED_RESPONSE: 'Received an unexpected response from the licensing service. Please try again.',
  };
  const POLICY_MESSAGES = {
    too_short: 'The new password must be at least 10 characters.',
    same_as_old: 'The new password must be different from your temporary password.',
  };

  function reasonText(body) {
    const code = (body && body.reason_code) || 'INVALID_REQUEST';
    if (code === 'PASSWORD_POLICY_FAILED' && body.policy_code && POLICY_MESSAGES[body.policy_code]) {
      return t(POLICY_MESSAGES[body.policy_code]);
    }
    if (code === 'RATE_LIMITED' && typeof body.retry_after_seconds === 'number') {
      const minutes = Math.max(1, Math.ceil(body.retry_after_seconds / 60));
      return t(MESSAGES.RATE_LIMITED) + ' (' + minutes + ' ' + t('min') + ')';
    }
    return t(MESSAGES[code] || MESSAGES.INVALID_REQUEST);
  }

  function el(tag, opts, children) {
    const node = document.createElement(tag);
    const o = opts || {};
    if (o.className) node.className = o.className;
    if (o.id) node.id = o.id;
    if (o.text !== undefined) node.textContent = o.text;
    if (o.type) node.type = o.type;
    if (o.autocomplete) node.autocomplete = o.autocomplete;
    if (o.htmlFor) node.htmlFor = o.htmlFor;
    if (o.lang) node.setAttribute('lang', o.lang);
    if (o.dir) node.setAttribute('dir', o.dir);
    if (o.disabled) node.disabled = true;
    (children || []).forEach((c) => node.appendChild(c));
    return node;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function field(id, label, type, autocomplete) {
    const input = el('input', { id: id, type: type, autocomplete: autocomplete });
    input.setAttribute('spellcheck', 'false');
    return { wrap: el('div', {}, [el('label', { htmlFor: id, text: label }), input]), input: input };
  }

  /**
   * mount(host, api): paints the sign-in wizard into `host`.
   *   api.post(url, body) -> Promise<{status, body}>   (licensing.js's apiPost)
   *   api.say(text, kind)  /  api.clearSay()           (the page's message area)
   *   api.onActivated({email, response})               (called once, on success)
   */
  function mount(host, api) {
    let flowId = null;
    let view = null;
    let email = '';
    let chosenLicence = null;

    function reset() { flowId = null; view = null; chosenLicence = null; clearMidFlow(); showSignIn(); }

    function busy(button, on, label) {
      button.disabled = on;
      if (label) button.textContent = on ? t('Please wait…') : label;
    }

    // Any answer that means "this sign-in is dead, start over".
    function expired(body) {
      return body && (body.reason_code === 'ACCOUNT_FLOW_EXPIRED');
    }

    // ── 1. e-mail + password ────────────────────────────────────────────────
    function showSignIn() {
      clear(host);
      host.appendChild(el('p', { text: t('Sign in with your Aura account to activate this computer. Use the email and password your seller gave you.') }));
      const e = field('account-email', t('Email'), 'email', 'username');
      const p = field('account-password', t('Password'), 'password', 'current-password');
      if (email) e.input.value = email;
      const btn = el('button', { id: 'account-signin-btn', text: t('Sign in') });
      const submit = async function () {
        const typedEmail = e.input.value.trim();
        if (!typedEmail || !p.input.value) {
          api.say(t(MESSAGES.ACCOUNT_SIGN_IN_FAILED), 'error');
          return;
        }
        api.clearSay();
        busy(btn, true, t('Sign in'));
        let res;
        try {
          // The password goes into this one request body and is blanked from the
          // field the moment the request has been handed to fetch().
          const pending = api.post('/api/licensing/account/sign-in', { email: typedEmail, password: p.input.value });
          p.input.value = '';
          res = await pending;
        } catch (err) {
          p.input.value = '';
          busy(btn, false, t('Sign in'));
          api.say(t(MESSAGES.NETWORK_UNAVAILABLE), 'error');
          return;
        }
        busy(btn, false, t('Sign in'));
        if (res.status !== 200 || !res.body || !res.body.flow_id) {
          api.say(reasonText(res.body), 'error');
          return;
        }
        email = typedEmail;
        flowId = res.body.flow_id;
        view = res.body;
        next();
      };
      btn.addEventListener('click', submit);
      p.input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') submit(); });
      e.input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') p.input.focus(); });
      host.appendChild(e.wrap);
      host.appendChild(p.wrap);
      host.appendChild(btn);
    }

    // The order is fixed here AND enforced by the local routes: password, then
    // contract, then branch, then activate.
    function next() {
      if (view.password_change_required) { markMidFlow('password'); return showPasswordChange(); }
      if (view.contract && view.contract.required) { markMidFlow('contract'); return showContract(); }
      markMidFlow('activate');
      return showBranchOrActivate();
    }

    // ── 2. forced password change ───────────────────────────────────────────
    function showPasswordChange() {
      clear(host);
      host.appendChild(el('h2', { text: t('Choose your own password'), className: 'step-title' }));
      host.appendChild(el('p', { text: t('The password your seller gave you was temporary. Choose a new one that only you know. At least 10 characters.') }));
      const a = field('account-new-password', t('New password'), 'password', 'new-password');
      const b = field('account-new-password2', t('Repeat the new password'), 'password', 'new-password');
      const btn = el('button', { id: 'account-password-btn', text: t('Save password') });
      const submit = async function () {
        if (a.input.value.length < MIN_PASSWORD_LENGTH) {
          api.say(t(POLICY_MESSAGES.too_short), 'error');
          return;
        }
        if (a.input.value !== b.input.value) {
          api.say(t('The two passwords are not the same.'), 'error');
          return;
        }
        api.clearSay();
        busy(btn, true, t('Save password'));
        let res;
        try {
          const pending = api.post('/api/licensing/account/password', { flow_id: flowId, new_password: a.input.value });
          a.input.value = '';
          b.input.value = '';
          res = await pending;
        } catch (err) {
          a.input.value = ''; b.input.value = '';
          busy(btn, false, t('Save password'));
          api.say(t(MESSAGES.NETWORK_UNAVAILABLE), 'error');
          return;
        }
        busy(btn, false, t('Save password'));
        if (res.status === 200) {
          view.password_change_required = false;
          api.clearSay();
          next();
          return;
        }
        api.say(reasonText(res.body), 'error');
        if (expired(res.body) || (res.body && res.body.reason_code === 'ACCOUNT_SIGN_IN_FAILED')) reset();
      };
      btn.addEventListener('click', submit);
      b.input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') submit(); });
      host.appendChild(a.wrap);
      host.appendChild(b.wrap);
      host.appendChild(btn);
      a.input.focus();
    }

    // ── 3. the contract: a modal with no way out except Accept or going back ─
    function showContract() {
      clear(host);
      host.appendChild(el('p', { text: t('One last step: please read and accept the service agreement.') }));
      const c = view.contract;
      // Arabic first: the contract is Arabic by default, and its frame is shown
      // Arabic-above-English whatever language the screen is in.
      const rtl = (c.language || 'ar').toLowerCase().indexOf('ar') === 0;

      const overlay = el('div', { className: 'contract-overlay', id: 'contract-modal' });
      overlay.setAttribute('role', 'dialog');
      overlay.setAttribute('aria-modal', 'true');
      overlay.setAttribute('aria-labelledby', 'contract-title');
      const card = el('div', { className: 'contract-card' });

      card.appendChild(el('h2', { id: 'contract-title', className: 'contract-title', lang: 'ar', dir: 'rtl', text: c.title || 'عقد الاستخدام' }));
      card.appendChild(el('p', { className: 'contract-hint', lang: 'ar', dir: 'rtl', text: 'يرجى قراءة العقد حتى نهايته، ثم تأكيد الموافقة للمتابعة.' }));
      card.appendChild(el('p', { className: 'contract-hint', text: t('Read the agreement to the end, then tick the box to continue.') }));

      const bodyWrap = el('div', { className: 'contract-body-wrap' });
      const body = el('div', { id: 'contract-body', className: 'contract-body', lang: rtl ? 'ar' : (c.language || 'en'), dir: rtl ? 'rtl' : 'ltr', text: c.body || '' });
      body.setAttribute('tabindex', '0');
      bodyWrap.appendChild(body);
      if (c.draft) {
        // Owner says no lawyer has approved this text yet: it must never be
        // mistaken for the final agreement on a screenshot or in a demo.
        const mark = el('div', { id: 'contract-draft', className: 'contract-draft', text: 'DRAFT — مسودة' });
        mark.setAttribute('aria-hidden', 'true');
        bodyWrap.appendChild(mark);
      }
      card.appendChild(bodyWrap);

      const agree = el('input', { id: 'contract-agree', type: 'checkbox', disabled: true });
      const agreeLabel = el('label', { className: 'contract-agree', htmlFor: 'contract-agree' }, [
        agree,
        el('span', { lang: 'ar', dir: 'rtl', text: ' قرأت العقد وأوافق على جميع بنوده' }),
        el('span', { text: ' / ' + t('I have read the agreement and I accept it') }),
      ]);
      card.appendChild(agreeLabel);
      // Two SEPARATE consents (Owner 2026-11-01), unticked, each recorded on its own.
      // (b) data outside Jordan is mandatory; (a) ID-document retention is optional --
      // without it the team verifies the identity by phone. Neither waits for the scroll.
      const idDocs = el('input', { id: 'contract-consent-id-docs', type: 'checkbox' });
      card.appendChild(el('label', { className: 'contract-agree', htmlFor: 'contract-consent-id-docs' }, [
        idDocs,
        el('span', { lang: 'ar', dir: 'rtl', text: ' أوافق على احتفاظ Action Aura بصورتي هويتي وسجلي التجاري للتحقق من حسابي (تُحذف بعد سنة من انتهاء اشتراكي).' }),
        el('span', { text: ' / ' + t('I agree that Action Aura keeps the photos of my ID and commercial registration to verify my account (deleted one year after my subscription ends).') }),
      ]));
      const idDocsNote = el('p', { id: 'contract-consent-id-docs-note', className: 'contract-hint' }, [
        el('span', { lang: 'ar', dir: 'rtl', text: 'بدون هذه الموافقة سيتحقق فريقنا من هويتك هاتفيًا.' }),
        el('span', { text: ' / ' + t('Without this, our team will verify your identity by phone.') }),
      ]);
      card.appendChild(idDocsNote);
      const outside = el('input', { id: 'contract-consent-outside-jordan', type: 'checkbox' });
      card.appendChild(el('label', { className: 'contract-agree', htmlFor: 'contract-consent-outside-jordan' }, [
        outside,
        el('span', { lang: 'ar', dir: 'rtl', text: ' أوافق على حفظ بيانات حسابي ومتجري على خوادم Action Aura خارج الأردن (ألمانيا وCloudflare).' }),
        el('span', { text: ' / ' + t("I agree that my account and shop data are stored on Action Aura's servers outside Jordan (Germany and Cloudflare).") }),
      ]));
      const note = el('p', { id: 'contract-scroll-note', className: 'contract-hint', text: t('Scroll to the end of the agreement to enable the box.') });
      card.appendChild(note);

      // The shop owner (or their authorised person) accepts in person -- never the seller.
      card.appendChild(el('p', { id: 'contract-personal-note', className: 'contract-hint' }, [
        el('span', { lang: 'ar', dir: 'rtl', text: 'يجب أن يوافق صاحب المتجر أو المفوَّض بنفسه، ولا يجوز للمندوب الموافقة نيابة عنه.' }),
        el('span', { text: ' / ' + t('The shop owner (or their authorised person) must accept personally; the seller may not accept on their behalf.') }),
      ]));
      const accept = el('button', { id: 'contract-accept-btn', text: 'أوافق / ' + t('Accept'), disabled: true });
      const back = el('button', { id: 'contract-decline-btn', className: 'secondary', text: t('Decline and go back') });
      card.appendChild(el('div', { className: 'row' }, [accept, back]));
      overlay.appendChild(card);

      let reachedEnd = false;
      const atEnd = function () { return body.scrollTop + body.clientHeight >= body.scrollHeight - 4; };
      const onScroll = function () {
        if (!reachedEnd && atEnd()) {
          reachedEnd = true;
          agree.disabled = false;
          note.textContent = '';
        }
      };
      body.addEventListener('scroll', onScroll);
      // Accept needs: the end reached, the contract box, AND the mandatory data-outside-Jordan box.
      // The ID-document box is optional, but its note shows while it is unticked.
      const canAccept = function () { return reachedEnd && agree.checked && outside.checked; };
      const refresh = function () {
        accept.disabled = !canAccept();
        idDocsNote.hidden = idDocs.checked;
      };
      agree.addEventListener('change', refresh);
      outside.addEventListener('change', refresh);
      idDocs.addEventListener('change', refresh);
      refresh();

      // Cannot be dismissed: Escape and a click on the dark area do nothing.
      const swallowEscape = function (ev) {
        if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); }
      };
      document.addEventListener('keydown', swallowEscape, true);
      const close = function () {
        document.removeEventListener('keydown', swallowEscape, true);
        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      };

      accept.addEventListener('click', async function () {
        // Belt and braces: the buttons are disabled until both conditions hold,
        // but a script clicking a disabled-then-enabled node must not slip past.
        if (!canAccept()) return;
        busy(accept, true);
        let res;
        try {
          res = await api.post('/api/licensing/account/contract/accept', {
            flow_id: flowId, agreed: true,
            consents: { id_documents: idDocs.checked, data_outside_jordan: outside.checked },
          });
        } catch (err) {
          busy(accept, false);
          api.say(t(MESSAGES.NETWORK_UNAVAILABLE), 'error');
          return;
        }
        if (res.status === 200) {
          close();
          view.contract.required = false;
          api.clearSay();
          next();
          return;
        }
        busy(accept, false);
        api.say(reasonText(res.body), 'error');
        if (expired(res.body) || (res.body && res.body.reason_code === 'ACCOUNT_SIGN_IN_FAILED')) { close(); reset(); }
      });
      back.addEventListener('click', function () { close(); reset(); });

      document.body.appendChild(overlay);
      // A short agreement that fits without scrolling is "already at the end".
      setTimeout(onScroll, 0);
      body.focus();
    }

    // ── 4/5. branch, then activate ──────────────────────────────────────────
    function showBranchOrActivate() {
      const licences = (view && view.licences) || [];
      if (licences.length === 1) {
        chosenLicence = licences[0].license_public_id;
        return showReadyToActivate(licences[0]);
      }
      clear(host);
      host.appendChild(el('h2', { text: t('Which branch is this computer for?'), className: 'step-title' }));
      const list = el('div', { id: 'branch-list' });
      licences.forEach(function (lic, i) {
        const radio = el('input', { type: 'radio', id: 'branch-' + i });
        radio.name = 'branch';
        radio.value = lic.license_public_id;
        radio.addEventListener('change', function () { chosenLicence = lic.license_public_id; go.disabled = false; });
        const label = licenceLabel(lic, t('Branch') + ' ' + (i + 1), true)
          + ((lic.trial && lic.trial.ends_on) ? ' · ' + t('trial ends') + ' ' + lic.trial.ends_on : '');
        list.appendChild(el('label', { htmlFor: 'branch-' + i, className: 'branch-row' }, [radio, el('span', { text: ' ' + label })]));
      });
      host.appendChild(list);
      const go = el('button', { id: 'branch-continue-btn', text: t('Continue'), disabled: true });
      go.addEventListener('click', function () { activate(go); });
      host.appendChild(go);
    }

    function showReadyToActivate(lic) {
      clear(host);
      host.appendChild(el('p', { text: t('Signed in. This computer is ready to be activated for:') }));
      const what = licenceLabel(lic, t('your shop'));
      host.appendChild(el('p', { id: 'ready-licence', className: 'step-title', text: what }));
      if (lic.trial && lic.trial.ends_on) {
        host.appendChild(el('p', { text: t('Your free trial runs until') + ' ' + lic.trial.ends_on + '.' }));
      }
      const go = el('button', { id: 'account-activate-btn', text: t('Activate this computer') });
      go.addEventListener('click', function () { activate(go); });
      host.appendChild(go);
    }

    async function activate(button) {
      if (!chosenLicence) return;
      api.clearSay();
      busy(button, true);
      let res;
      try {
        res = await api.post('/api/licensing/account/activate', { flow_id: flowId, license_public_id: chosenLicence });
      } catch (err) {
        busy(button, false);
        api.say(t(MESSAGES.NETWORK_UNAVAILABLE), 'error');
        return;
      }
      if (res.status === 200 && res.body && res.body.result === 'SUCCESS') {
        try {
          localStorage.setItem(ROLE_PENDING_KEY, JSON.stringify({ v: 1, at: Date.now(), email: email }));
        } catch (err) { /* storage blocked: the server copy below still holds the step */ }
        // rc.19: ALSO kept on the device's own backend (config.json), so a reload,
        // a wiped webview profile or another browser still lands on the role
        // step. Awaited: the page navigates away right after.
        await postRolePending(email);
        clearMidFlow();
        api.onActivated({ email: email, response: res.body });
        return;
      }
      busy(button, false);
      if (res.status === 202) {
        api.say(t('Your request was received and is waiting for approval from Action Aura.'), 'info');
        return;
      }
      const code = (res.body && res.body.reason_code) || '';
      api.say(reasonText(res.body), 'error');
      if (code === 'ACTIVATION_GRANT_INVALID' || expired(res.body)) reset();
    }

    showSignIn();
    // A reload (or an app restart) in the middle of the steps cannot resume them: the
    // sign-in ticket lives only in the backend's memory and is never written to disk (that
    // is the rule A9 of the account e2e pins). Say so plainly instead of silently showing
    // a fresh sign-in form as if nothing had happened.
    const lost = readMidFlow();
    if (lost) api.say(t(MIDFLOW_MESSAGES[lost]), 'info');
  }

  window.AuraAccountSignIn = { mount: mount, ROLE_PENDING_KEY: ROLE_PENDING_KEY, planDisplayName: planDisplayName };
})();
