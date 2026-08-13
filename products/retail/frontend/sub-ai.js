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

  // Server-side timeout (config.py's AURA_AI_TIMEOUT_SECONDS) is 45s as of
  // 2026-08-12 (bumped from 15s -- a real prompt took 36.5s against the
  // actual small CPU droplet, verified by timed curl). This client-side
  // backstop sits above it so a hung network connection (not just a slow
  // model) still resolves into the friendly error state instead of leaving
  // the send button disabled and the orb "thinking" forever.
  const CLIENT_TIMEOUT_MS = 50000;
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
      const killTimer = setTimeout(() => controller.abort(), CLIENT_TIMEOUT_MS);

      try {
        const res = await fetch(`/api/sub/${this._systemId}/ai/chat`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: message,
            history: this._history.slice(-HISTORY_TURNS_SENT),
          }),
          signal: controller.signal,
        });
        const data = await res.json().catch(() => ({}));
        const reply = (res.ok && data && data.success && data.data && data.data.reply) || '';
        if (!reply) throw new Error((data && data.error) || 'unavailable');
        this._appendMessage('assistant', reply);
        this._history.push({ role: 'assistant', content: reply });
      } catch (e) {
        // Covers: network failure, AbortError (client timeout), non-200,
        // and a 200-with-no-reply body -- all collapse to the same
        // friendly message. The real cause stays in the console for
        // whoever's debugging, never in the chat bubble.
        if (e && e.name !== 'AbortError') console.warn('SubAI.send failed:', e);
        this._appendMessage('assistant', UNAVAILABLE_MSG());
      } finally {
        clearTimeout(killTimer);
        this._sending = false;
        this._setBusy(false);
      }
    },

    _appendMessage(role, text) {
      const msgs = document.getElementById(MESSAGES_ID);
      if (!msgs) return;
      const row = document.createElement('div');
      row.className = 'ai-msg' + (role === 'user' ? ' user' : '');
      const avatar = document.createElement('div');
      avatar.className = 'ai-msg-avatar';
      avatar.textContent = role === 'user' ? '🙂' : '🤖';
      const bubble = document.createElement('div');
      bubble.className = 'ai-msg-bubble';
      bubble.textContent = text;   // textContent only -- never render model output as HTML
      row.appendChild(avatar);
      row.appendChild(bubble);
      msgs.appendChild(row);
      msgs.scrollTop = msgs.scrollHeight;
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
      } else if (typing) {
        typing.remove();
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
            <textarea id="${INPUT_ID}" rows="1" placeholder="${t('Ask me anything...')}"></textarea>
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
