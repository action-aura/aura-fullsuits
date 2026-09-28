/* SubAI — the Retail sidebar "AI Assistant" panel.
 *
 * Wires up the previously-dead `SubAI.open(systemId)` call sites in
 * app-shell.js (`.sub-ai-btn`, the header shortcut, and the CSS scaffold
 * already shipped in css/main.css: `.sub-ai-panel`, `.ai-orb`,
 * `.ai-input-bar textarea`) to a real backend proxy at
 * POST /api/sub/<systemId>/ai/chat (products/retail/backend/api/retail_api.py
 * -- `ai_chat()`), which in turn calls a hosted small LLM (phi3.5:3.8b as of
 * 2026-08-13 -- see backend/config.py's AURA_AI_MODEL_NAME comment for the
 * on-host benchmark that picked it). See that route's docstring for the
 * server-side timeout/error-handling contract this module relies on: every
 * failure mode (timeout, connection error, non-200,
 * empty reply) comes back as `{success:false, error:'...'}` with a friendly
 * message, never a raw 500.
 *
 * The predecessor of this file (the source monolith's per-subsystem SubAI,
 * see docs/superpowers/plans/2026-08-06-retail-standalone-ui-shell.md Task 2)
 * was deliberately deleted during the standalone-shell port because its
 * backend route didn't exist yet in this product. It now does -- this is a
 * fresh implementation against this codebase's real route contract, not a
 * restoration of the deleted monolith code.
 *
 * Vanilla JS, no framework, no build step -- matches every other file in
 * this frontend. Depends on `window.t` (i18n.js, already loaded earlier in
 * index.html) and `SubsystemApp.apiPost`-style JSON conventions, though this
 * module uses `fetch` directly so it can attach its own AbortController
 * (SubsystemApp.apiPost has no client-side timeout, and a stuck upstream
 * model must never leave this panel spinning forever).
 */
(function () {
  const PANEL_ID = 'sub-ai-panel';
  const MESSAGES_ID = 'sub-ai-messages';
  const INPUT_ID = 'sub-ai-input';
  const ORB_ID = 'sub-ai-orb';
  const STATUS_ID = 'sub-ai-status';
  const SEND_ID = 'sub-ai-send';
  const TYPING_ID = 'sub-ai-typing';
  // P0 review-fix (2026-09-27) -- the sidebar liveness dot. Coupled to
  // app-shell.js by these bare id strings, not by any shared module (there
  // isn't one): SIDEBAR_BTN_ID must match the literal `id="..."` app-shell.js
  // writes on `.sub-ai-btn`, and PULSE_ID is the id this file itself gives
  // the dot when it creates one. Same "two files share a bare name on
  // purpose" coupling index.html's own header comment documents for
  // app-shell.js/i18n.js.
  const SIDEBAR_BTN_ID = 'sub-ai-sidebar-btn';
  const PULSE_ID = 'sub-ai-pulse-dot';

  // Guarded AuraIcons.render(), the shape every other file in this frontend
  // uses. This panel kept four raw emoji longer than anywhere else -- 🙂/🤖
  // as the chat avatars and 🗑/➤ as its ONLY TWO CONTROLS -- so the one
  // surface in the product that is meant to feel like part of the app was
  // drawing its buttons in the OS emoji font. 'bot', 'trash-2' and 'send'
  // were added to icons.js for these (real Lucide geometry, fetched not
  // drawn); 🙂 maps to the 'user-round' icon that already existed.
  const _icon = (name, size, fallback) =>
    (window.AuraIcons ? AuraIcons.render(name, size) : (fallback || ''));

  // 2026-08-13 (speed pass): with the response now streamed (see send()
  // below), a healthy reply can legitimately run past 50s of WALL time
  // while visibly producing text -- a one-shot response made 50s a TOTAL
  // budget, but that would now abort perfectly healthy long sessions. What
  // actually matters is "never silent forever", so this is a STALL timer:
  // armed before the fetch and re-armed on every received token (resetStall()
  // in send() below). Server-side, config.py's AURA_AI_TIMEOUT_SECONDS (45s)
  // is likewise now a per-read inactivity timeout once streaming, not a
  // total-request timeout (see ai_chat()'s streaming-branch comment) --
  // this client stall timer is the matching client-side concept.
  const CLIENT_STALL_TIMEOUT_MS = 50000;
  const HISTORY_TURNS_SENT = 10;
  const UNAVAILABLE_MSG = () => t('AI assistant is temporarily unavailable. Please try again in a moment.');
  const GREETING_MSG = () => t("Hi! I'm your Aura Retail assistant. Ask me anything about using the system.");

  const SubAI = {
    _systemId: 'retail',
    _history: [],   // [{role:'user'|'assistant', content:string}] -- sent back to the server for multi-turn context
    _sending: false,
    // P0 #18 -- AI. This panel used to hardcode 'Online' in its markup and
    // reset the status pill to 'Online' after EVERY reply, including a
    // failed one -- see the old _setBusy(false) line this replaced. There
    // was no state here at all for "is an endpoint even configured"; the
    // panel simply asserted it always was.
    //   _configured: null until GET /ai/status resolves once (this session),
    //     then true/false from the server's own answer.
    //   _lastCallFailed: true only across the idle moment right after a
    //     failed send() -- cleared optimistically the next time the user
    //     tries again, so one failure does not permanently brick the pill.
    _configured: null,
    _lastCallFailed: false,

    open(systemId) {
      this._systemId = systemId || 'retail';
      const panel = document.getElementById(PANEL_ID);
      if (!panel) return;
      panel.classList.remove('hidden');
      panel.setAttribute('aria-hidden', 'false');
      if (!this._history.length) {
        this._appendMessage('assistant', GREETING_MSG());
      }
      const input = document.getElementById(INPUT_ID);
      if (input) setTimeout(() => input.focus(), 50);
      this._checkStatus();
    },

    // GET /api/sub/<systemId>/ai/status -- same decorators as POST .../ai/chat
    // (retail_api.py), so an unauthenticated/uncapable request fails the same
    // way a chat message already would. Shows 'Checking…' the instant the
    // panel opens (never a stale 'Online' left over from a previous system),
    // then the real answer: 'Online' only when the server reports a
    // configured endpoint, 'Not set up' otherwise. Skipped while a message is
    // in flight so it can never stomp 'Thinking…'/'Typing…'.
    async _checkStatus() {
      if (this._sending) return;
      this._lastCallFailed = false;
      this._configured = null;
      this._setStatusText(t('Checking…'));
      try {
        const res = await fetch(`/api/sub/${this._systemId}/ai/status`, { credentials: 'include', cache: 'no-store' });
        const data = await res.json().catch(() => null);
        this._configured = !!(res.ok && data && data.configured === true);
      } catch (e) {
        // Network failure answering the STATUS check is not the same claim
        // as a failed CHAT reply, so this resolves to 'Not set up' rather
        // than 'Unavailable' -- there is no reply to have failed yet.
        this._configured = false;
      }
      if (!this._sending) this._setStatusText(this._idleStatusText());
      this._syncPulseDot();
    },

    // P0 review-fix (2026-09-27) -- the sidebar '.ai-pulse' dot. app-shell.js
    // only ever gates it at RENDER time (`window.SubAI._configured === true`
    // when the sidebar HTML string is built), but that render happens before
    // _checkStatus() has ever run, so left alone the dot would freeze on
    // whatever _configured happened to be at that one instant -- null,
    // forever, on an install where the sidebar is painted once and never
    // rebuilt. This is the live half of that fix: the one place that
    // actually LEARNS the answer folds it back into the already-rendered
    // button. Both directions matter, not just "turn it on": an endpoint
    // that answered before and now does not (_configured flips back to
    // false) must lose its dot too, or the dot becomes a stale claim in the
    // other direction instead.
    _syncPulseDot() {
      const btn = document.getElementById(SIDEBAR_BTN_ID);
      if (!btn) return;   // AI button not in this render (no retail.reports, or module not licensed)
      const existing = document.getElementById(PULSE_ID);
      if (this._configured === true) {
        if (!existing) {
          const dot = document.createElement('span');
          dot.id = PULSE_ID;
          dot.className = 'ai-pulse';
          btn.appendChild(dot);
        }
      } else if (existing) {
        existing.remove();
      }
    },

    // The single source _setBusy(false) and _checkStatus() both read, so the
    // pill can never show two different ideas of "idle" depending on which
    // one last wrote to it.
    _idleStatusText() {
      if (this._lastCallFailed) return t('Unavailable');
      if (this._configured === null) return t('Checking…');
      return this._configured ? t('Online') : t('Not set up');
    },

    _setStatusText(text) {
      const status = document.getElementById(STATUS_ID);
      if (status) status.textContent = text;
    },

    close() {
      const panel = document.getElementById(PANEL_ID);
      if (!panel) return;
      panel.classList.add('hidden');
      panel.setAttribute('aria-hidden', 'true');
    },

    toggle(systemId) {
      const panel = document.getElementById(PANEL_ID);
      if (panel && !panel.classList.contains('hidden')) this.close();
      else this.open(systemId);
    },

    clear() {
      this._history = [];
      const msgs = document.getElementById(MESSAGES_ID);
      if (msgs) msgs.innerHTML = '';
      this._appendMessage('assistant', GREETING_MSG());
    },

    async send() {
      if (this._sending) return;
      const input = document.getElementById(INPUT_ID);
      if (!input) return;
      const message = input.value.trim();
      if (!message) return;

      input.value = '';
      input.style.height = 'auto';   // matches app-shell.js's own auto-resize handler on this element
      this._appendMessage('user', message);
      this._history.push({ role: 'user', content: message });

      this._sending = true;
      this._lastCallFailed = false;   // optimistic -- this attempt may still succeed
      this._setBusy(true);

      const controller = new AbortController();
      let killTimer = setTimeout(() => controller.abort(), CLIENT_STALL_TIMEOUT_MS);
      const resetStall = () => {
        clearTimeout(killTimer);
        killTimer = setTimeout(() => controller.abort(), CLIENT_STALL_TIMEOUT_MS);
      };

      let bubble = null;   // created lazily on the first streamed token -- see _finishStream()'s comment for why
      let acc = '';
      let streamError = false;

      try {
        const res = await fetch(`/api/sub/${this._systemId}/ai/chat`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: message,
            history: this._history.slice(-HISTORY_TURNS_SENT),
            stream: true,
            // AuraI18n.current (i18n.js) is the live, explicit source of truth
            // for the active UI locale -- it reflects the language toggle
            // instantly, unlike the `users.language` column the server
            // persists (POST /api/auth/language) but never reads back for
            // this route. The server whitelists this value; it is never
            // trusted outright (see retail_api.py's _resolve_ai_language()).
            lang: (window.AuraI18n && AuraI18n.current) || 'en',
          }),
          signal: controller.signal,
        });

        if (!res.ok) {
          const errBody = await res.json().catch(() => ({}));
          throw new Error((errBody && errBody.error) || 'unavailable');
        }

        const contentType = res.headers.get('Content-Type') || '';
        if (contentType.indexOf('application/json') !== -1) {
          // Legacy/non-streaming backend fallback -- lets this file keep
          // working unmodified against a server build that ignores `stream`.
          const data = await res.json().catch(() => ({}));
          const reply = (data && data.success && data.data && data.data.reply) || '';
          if (!reply) throw new Error((data && data.error) || 'unavailable');
          this._appendMessage('assistant', reply);
          this._history.push({ role: 'assistant', content: reply });
          return;
        }

        const reader = res.body && res.body.getReader ? res.body.getReader() : null;
        if (!reader) {
          // No ReadableStream support -- degrade to reading the whole NDJSON
          // body at once and replaying it through the same line handler
          // below (still correct, just not incremental). No second request.
          const text = await res.text();
          for (const line of text.split('\n')) {
            const r = this._handleStreamLine(line, resetStall, bubble, acc);
            if (r.bubble !== undefined) bubble = r.bubble;
            if (r.acc !== undefined) acc = r.acc;
            if (r.error) { streamError = true; break; }
            if (r.done) break;
          }
        } else {
          const decoder = new TextDecoder('utf-8');
          let buf = '';
          let streamDone = false;
          while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const lines = buf.split('\n');
            buf = lines.pop();   // keep the trailing partial line for the next chunk
            for (const line of lines) {
              const r = this._handleStreamLine(line, resetStall, bubble, acc);
              if (r.bubble !== undefined) bubble = r.bubble;
              if (r.acc !== undefined) acc = r.acc;
              if (r.error) { streamError = true; }
              if (r.done) { streamDone = true; }
              if (r.done || r.error) break;
            }
            if (streamError || streamDone) break;
          }
        }

        this._finishStream(bubble, acc, streamError);
      } catch (e) {
        // Covers: network failure, AbortError (client stall timeout), and
        // non-200 -- all funnel through _finishStream() so a stall/abort
        // that happens AFTER some tokens already rendered keeps that partial
        // text instead of wiping it (see _finishStream()'s own comment).
        if (e && e.name !== 'AbortError') console.warn('SubAI.send failed:', e);
        this._finishStream(bubble, acc, true);
      } finally {
        clearTimeout(killTimer);
        this._sending = false;
        this._setBusy(false);
      }
    },

    // Parses one NDJSON line from the stream ({"delta":...} / {"done":true} /
    // {"error":...}) and applies it to the DOM. Returns {bubble, acc, done,
    // error} -- the caller folds these back into its own local state because
    // JS has no output parameters; kept as a small pure-ish helper so both
    // the ReadableStream loop and the no-reader fallback in send() share
    // exactly one line-parsing implementation.
    _handleStreamLine(line, resetStall, bubble, acc) {
      const trimmed = (line || '').trim();
      if (!trimmed) return {};
      let obj;
      try {
        obj = JSON.parse(trimmed);
      } catch (e) {
        return {};   // a malformed line must never abort an otherwise-good stream
      }
      if (obj.delta) {
        resetStall();
        if (!bubble) {
          this._removeTyping();
          bubble = this._startAssistantBubble();
          const status = document.getElementById(STATUS_ID);
          if (status) status.textContent = t('Typing…');
        }
        acc = (acc || '') + obj.delta;
        bubble.textContent = acc;
        const msgs = document.getElementById(MESSAGES_ID);
        if (msgs) msgs.scrollTop = msgs.scrollHeight;
        return { bubble, acc };
      }
      if (obj.error) {
        return { bubble, acc, error: true };
      }
      if (obj.done) {
        return { bubble, acc, done: true };
      }
      return { bubble, acc };
    },

    // Single resolution point for both the normal end-of-stream path and the
    // catch block in send() -- a stalled/aborted/mid-stream-failed request
    // takes the exact same branch as a clean {"error":...} line.
    //
    // Decision: on failure, KEEP whatever partial text the user already
    // read and append an inline unavailable-marker on a blank line, rather
    // than wiping the bubble. With the old one-shot response there was no
    // partial state to preserve, so "replace with the error" was the only
    // option; with streaming, discarding text the user has already seen
    // would look like a bug and would throw away the one real benefit
    // streaming bought. Never push the marker itself into `_history` --
    // only the real accumulated text, so a later multi-turn prompt doesn't
    // include the UI-only error string.
    _finishStream(bubble, acc, failed) {
      if (failed) {
        // P0 #18 -- AI. The status pill must say 'Unavailable' for THIS
        // failure, not silently fall back to whatever _configured already
        // was -- _setBusy(false), called right after this in send()'s
        // `finally`, reads this flag ahead of _configured (see
        // _idleStatusText()).
        this._lastCallFailed = true;
        if (acc) {
          bubble.textContent = acc + '\n\n' + UNAVAILABLE_MSG();
          this._history.push({ role: 'assistant', content: acc });
        } else {
          // No bubble was ever created (see send()'s lazy creation above --
          // this is exactly why it's lazy: a failure before the first token
          // arrives can never leave an empty bubble behind).
          this._appendMessage('assistant', UNAVAILABLE_MSG());
        }
        return;
      }
      if (acc) {
        this._history.push({ role: 'assistant', content: acc });
      } else if (!bubble) {
        // Stream reported success but produced zero fragments -- treat the
        // same as the non-streaming route's "empty reply" 503 case. P0
        // review-fix (2026-09-27): this branch used to leave _lastCallFailed
        // untouched, so _setBusy(false) right after this (send()'s `finally`)
        // fell through to _idleStatusText()'s _configured branch and claimed
        // "Online" beside a bubble telling the user the assistant is
        // unavailable -- the exact lie the `failed` branch above already
        // removes for a genuinely FAILED reply, reached here instead through
        // the "succeeded but said nothing" gap.
        this._lastCallFailed = true;
        this._appendMessage('assistant', UNAVAILABLE_MSG());
      }
    },

    // Builds one message row (`.ai-msg` / `.ai-msg-avatar` / `.ai-msg-bubble`),
    // appends it, and returns the bubble element -- the one shared DOM shape
    // both a complete message (_appendMessage) and a streamed-in-progress
    // message (_startAssistantBubble) use.
    //
    // dir="auto": the panel's overall direction follows the UI locale
    // (body.rtl / html[dir], set by AuraI18n.apply()), but a REPLY's
    // language follows the server's _resolve_ai_language() -- which, via
    // its heuristic fallback, can legitimately be Arabic while the UI is
    // English (or English while the UI is Arabic). dir="auto" makes the
    // browser resolve each bubble's own direction independently from its
    // first strong directional character, which is exactly the desired
    // per-message behavior without any CSS.
    //
    // data-no-i18n: i18n.js's AuraI18n._translateTree()/_startObserver()
    // sweep newly-added DOM nodes and rewrite any text node whose full
    // trimmed value exactly matches an EN->AR dictionary key -- correct for
    // UI chrome, wrong for chat content (user/model data, not UI strings;
    // with streaming, the observer would otherwise re-scan this bubble on
    // every single token). i18n.js's `_translateTree` explicitly honors
    // `[data-no-i18n]` as a skip hook. Accepted consequence: switching
    // language mid-session leaves already-rendered transcript in its
    // original language -- correct, a transcript shouldn't retroactively
    // change language underneath the user.
    _buildMessageRow(role) {
      const msgs = document.getElementById(MESSAGES_ID);
      if (!msgs) return null;
      const row = document.createElement('div');
      row.className = 'ai-msg' + (role === 'user' ? ' user' : '');
      const avatar = document.createElement('div');
      avatar.className = 'ai-msg-avatar';
      // Avatars through AuraIcons, not raw emoji: a system emoji's
      // appearance belongs to the OS font and cannot take a theme token,
      // and these two are the only faces in the product. innerHTML (not
      // textContent) because render() returns an <svg>; the values are
      // constants from this file, never user input.
      avatar.innerHTML = _icon(role === 'user' ? 'user-round' : 'bot', 18, role === 'user' ? '🙂' : '🤖');
      const bubble = document.createElement('div');
      bubble.className = 'ai-msg-bubble';
      bubble.setAttribute('dir', 'auto');
      bubble.setAttribute('data-no-i18n', '');
      row.appendChild(avatar);
      row.appendChild(bubble);
      msgs.appendChild(row);
      msgs.scrollTop = msgs.scrollHeight;
      return bubble;
    },

    _appendMessage(role, text) {
      const bubble = this._buildMessageRow(role);
      if (!bubble) return;
      bubble.textContent = text;   // textContent only -- never render model output as HTML
    },

    // Streaming variant of _appendMessage: creates an empty assistant bubble
    // up front and returns it so send() can append tokens into it as they
    // arrive via bubble.textContent = acc (still textContent-only -- see
    // _buildMessageRow's comment, this never changes for streamed content
    // either).
    _startAssistantBubble() {
      return this._buildMessageRow('assistant');
    },

    _removeTyping() {
      const typing = document.getElementById(TYPING_ID);
      if (typing) typing.remove();
    },

    _setBusy(busy) {
      const orb = document.getElementById(ORB_ID);
      const status = document.getElementById(STATUS_ID);
      const sendBtn = document.getElementById(SEND_ID);
      if (orb) orb.style.opacity = busy ? '0.4' : '1';
      // P0 #18 -- AI. Was the unconditional t('Online') that made the pill
      // lie after a failed reply, and before the endpoint was ever checked
      // at all -- see _idleStatusText()'s own comment for the three-way
      // answer this replaces it with.
      if (status) status.textContent = busy ? t('Thinking…') : this._idleStatusText();
      if (sendBtn) sendBtn.disabled = !!busy;

      const msgs = document.getElementById(MESSAGES_ID);
      if (!msgs) return;
      let typing = document.getElementById(TYPING_ID);
      if (busy) {
        if (!typing) {
          typing = document.createElement('div');
          typing.id = TYPING_ID;
          typing.className = 'ai-msg';
          typing.innerHTML = '<div class="ai-msg-avatar">' + _icon('bot', 18, '🤖') + '</div>'
            + '<div class="ai-msg-bubble typing-dots"><span>.</span><span>.</span><span>.</span></div>';
          msgs.appendChild(typing);
          msgs.scrollTop = msgs.scrollHeight;
        }
      } else {
        this._removeTyping();
      }
    },

    _panelHTML() {
      return `
        <!-- P0 review-fix (2026-09-27) -- this carried role="dialog" with no
             aria-modal="true", found by retail_modal_a11y_coverage_test.js's
             widened check 2 (item #4) and correctly left failing there and
             handed to this crew rather than "fixed" in a file C5 does not own
             (see that test's own header comment on this exact gap).
             role="dialog" was the wrong call either way: this panel is a
             PERSISTENT side panel toggled .hidden/un-hidden (.ai-panel is
             'position:absolute', no backdrop/scrim -- css/main.css), the
             same shape CLAUDE.md already calls out for the mobile POS cart
             bottom-sheet as needing "its own answer" rather than the modal
             convention. Unlike a real dialog it never traps focus, never
             blocks the rest of the shell, and has no Escape-to-close --
             adding aria-modal="true" without also adding that behaviour
             would tell assistive tech the page behind it is unavailable
             when it plainly still is. role="complementary" (a persistent,
             supporting region, same category the ARIA spec gives a sidebar)
             is the honest read of what this actually is; aria-label is kept
             unchanged so it is still announced as "AI Assistant". -->
        <div class="ai-panel sub-ai-panel hidden" id="${PANEL_ID}" role="complementary" aria-label="${t('AI Assistant')}" aria-hidden="true">
          <div class="ai-panel-header">
            <div class="ai-panel-title">
              <div class="ai-orb" id="${ORB_ID}"></div>
              <div>
                <span class="ai-name">${t('AI Assistant')}</span><br>
                <!-- P0 #18 -- 'Checking…' at injection time (this markup is
                     built once, well before open()/its GET /ai/status ever
                     runs): the pill must never assert 'Online' before the
                     server has said so. -->
                <span class="ai-status" id="${STATUS_ID}">${t('Checking…')}</span>
              </div>
            </div>
            <div class="ai-header-actions">
              <button class="ai-hdr-btn" onclick="SubAI.clear()" title="${t('Clear chat')}" aria-label="${t('Clear chat')}">${_icon('trash-2', 16, '🗑')}</button>
              <button class="ai-hdr-btn" onclick="SubAI.close()" title="${t('Close')}" aria-label="${t('Close')}">✕</button>
            </div>
          </div>
          <div class="ai-messages" id="${MESSAGES_ID}"></div>
          <div class="ai-input-bar">
            <textarea id="${INPUT_ID}" rows="1" dir="auto" placeholder="${t('Ask me anything...')}"></textarea>
            <button class="ai-send-btn" id="${SEND_ID}" onclick="SubAI.send()" title="${t('Send')}" aria-label="${t('Send')}">${_icon('send', 16, '➤')}</button>
          </div>
        </div>
      `;
    },

    // Injects the panel markup into the DOM once, synchronously (not inside
    // a DOMContentLoaded handler). This file loads (in index.html) before
    // app-shell.js, whose own DOMContentLoaded listener wires up
    // `#sub-ai-input`'s auto-resize behavior by element id -- that listener
    // only finds the element if it already exists in the DOM by the time
    // DOMContentLoaded fires, which requires this panel to be injected
    // during initial script execution, not deferred to this file's own
    // DOMContentLoaded (script tags all run, in order, before
    // DOMContentLoaded fires -- so "synchronous now" is early enough).
    _inject() {
      if (document.getElementById(PANEL_ID)) return;
      if (!document.body) return;   // defensive; index.html places this script at the end of <body>
      document.body.insertAdjacentHTML('beforeend', this._panelHTML());

      const input = document.getElementById(INPUT_ID);
      if (input) {
        input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            SubAI.send();
          }
        });
      }
    },
  };

  window.SubAI = SubAI;
  SubAI._inject();
})();
