/* SubAI — the Retail sidebar "AI Assistant" panel.
 *
 * Wires up the previously-dead `SubAI.open(systemId)` call sites in
 * app-shell.js (`.sub-ai-btn`, the header shortcut, and the CSS scaffold
 * already shipped in css/main.css: `.sub-ai-panel`, `.ai-orb`,
 * `.ai-input-bar textarea`) to a real backend proxy at
 * POST /api/sub/<systemId>/ai/chat (products/retail/backend/api/retail_api.py
 * -- `ai_chat()`), which in turn calls a hosted small LLM (phi3.5:3.8b as of
 * 2026-08-13 -- see backend/config.py's AURA_AI_MODEL_NAME comment for the
 * on-droplet benchmark that picked it). See that route's docstring for the
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
        // same as the non-streaming route's "empty reply" 503 case.
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
      avatar.textContent = role === 'user' ? '🙂' : '🤖';
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
      if (status) status.textContent = busy ? t('Thinking…') : t('Online');
      if (sendBtn) sendBtn.disabled = !!busy;

      const msgs = document.getElementById(MESSAGES_ID);
      if (!msgs) return;
      let typing = document.getElementById(TYPING_ID);
      if (busy) {
        if (!typing) {
          typing = document.createElement('div');
          typing.id = TYPING_ID;
          typing.className = 'ai-msg';
          typing.innerHTML = '<div class="ai-msg-avatar">🤖</div>'
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
        <div class="ai-panel sub-ai-panel hidden" id="${PANEL_ID}" role="dialog" aria-label="AI Assistant" aria-hidden="true">
          <div class="ai-panel-header">
            <div class="ai-panel-title">
              <div class="ai-orb" id="${ORB_ID}"></div>
              <div>
                <span class="ai-name">${t('AI Assistant')}</span><br>
                <span class="ai-status" id="${STATUS_ID}">${t('Online')}</span>
              </div>
            </div>
            <div class="ai-header-actions">
              <button class="ai-hdr-btn" onclick="SubAI.clear()" title="${t('Clear chat')}" aria-label="${t('Clear chat')}">🗑</button>
              <button class="ai-hdr-btn" onclick="SubAI.close()" title="${t('Close')}" aria-label="${t('Close')}">✕</button>
            </div>
          </div>
          <div class="ai-messages" id="${MESSAGES_ID}"></div>
          <div class="ai-input-bar">
            <textarea id="${INPUT_ID}" rows="1" dir="auto" placeholder="${t('Ask me anything...')}"></textarea>
            <button class="ai-send-btn" id="${SEND_ID}" onclick="SubAI.send()" title="${t('Send')}" aria-label="${t('Send')}">➤</button>
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
