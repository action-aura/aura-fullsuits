"""Outbound WhatsApp -- WhatsAppOutboxWorker: the background job that
actually sends via the WhatsApp Business Cloud API.

Structurally a clone of commercial_runtime/notifications/worker.py's
EmailOutboxWorker -- same daemon threading.Timer start()/stop()/
_schedule_next() shape, same "one worker instance per (installation,
company_id), the CALLER decides whether to ever call start()" contract, same
disabled-gate-before-any-claim placement. See that module's docstring for
the full lineage and reasoning; only the send call and its params/JSON
decoding differ here.
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import whatsapp_settings
from .whatsapp_outbox import WhatsAppOutboxRepository
from .whatsapp_client import WhatsAppSendError, load_config_from_env, send_whatsapp_template_message

_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 3600

# whatsapp_outbox.last_error is user-facing -- it renders inside a one-line
# cell in the WhatsApp queue table (products/retail/frontend/whatsapp.js
# ::renderQueue) -- so the stored reason is bounded here rather than letting a
# multi-kilobyte upstream error body land in the column.
_MAX_STORED_ERROR_CHARS = 300

# Same 'aura.<area>' logger convention as commercial_runtime/backup/routes.py
# and security/app_secret.py.
log = logging.getLogger('aura.notifications.whatsapp')

# What replaces credential material. Deliberately visible rather than a silent
# deletion: an operator staring at a short reason in the queue table needs to
# be able to tell "redaction ran here" apart from "the API said nothing".
_REDACTED = '<redacted>'

# Pass 2 of _redact_credentials -- by SHAPE. Everything from the word "Bearer"
# to the end of the string is dropped, not just the next whitespace-delimited
# word. WHY the greedy form: requests >= 2.32's header validator interpolates
# the REJECTED HEADER VALUE into InvalidHeader's message ("... in header value:
# 'Bearer <token>'"), and the only way that validator ever fires on our
# Authorization header is an access token carrying an embedded CR/LF -- which
# is exactly the shape a token pasted into a .env or a wrapped `setx` line has,
# and which load_config_from_env's .strip() cannot remove because it only
# touches the LEADING/TRAILING ends. A non-greedy `\S+` would stop AT that
# embedded \r and leave the second half of the live token in the string. The
# tail after "Bearer" in these messages is credential material, so it all goes.
_CREDENTIAL_RE = re.compile(r'(?is)bearer\b.*')

# Floor for pass 1 (by VALUE). A Meta System User token is ~200 chars, so this
# never excludes a real one. It exists because str.replace('') splices the
# placeholder between every character of the message, and a one- or two-char
# configured value would shred an otherwise readable reason into noise --
# destroying exactly the diagnosability _describe_send_failure() exists to add.
# Nothing that short is a live credential, and pass 2 still sweeps the only
# leak vector that has actually been observed.
_MIN_REDACTABLE_TOKEN_CHARS = 8


def _redact_credentials(text: str) -> str:
    """Last line of defense before a failure reason is persisted to
    whatsapp_outbox.last_error / written to the app log.

    That column is served by GET /api/notifications/whatsapp/outbox, which is
    gated by _require_session() and NOT _require_admin() -- so anything that
    lands in it is readable by any signed-in user, cashier included. A string
    that can carry the WhatsApp access token must therefore never reach it,
    regardless of how unlikely the path that produced it looks.

    Two passes, because neither alone is sufficient:

    1. By VALUE -- the currently-configured access token, substring-matched
       anywhere in the text. Catches an occurrence with no 'Bearer ' in front
       of it (a proxy or urllib3 message that echoes only the value), which
       pass 2 would walk straight past.
    2. By SHAPE -- any 'Bearer ...' run, whatever follows it. Catches a token
       that is no longer the configured one: rotating
       AURA_WHATSAPP_ACCESS_TOKEN is not atomic with an in-flight retry, so a
       row that failed just before the rotation still carries the OLD secret
       in its message, and pass 1 can no longer recognise it.

    Ordered value-then-shape on purpose: pass 1 leaves the surrounding message
    intact, so a bare-token leak still ends up diagnosable, while pass 2 is a
    truncating sweep of last resort.
    """
    # load_config_from_env() only reads os.environ, but this runs INSIDE the
    # send-failure path: if it ever raised, _describe_send_failure would raise
    # with it, _process_row's except block would never finish, and the row
    # would be left leased with no retry recorded -- a redaction helper must
    # not be able to break delivery bookkeeping. Degrade to pass 2 alone.
    try:
        cfg = load_config_from_env()
    except Exception:
        cfg = None
    # cfg is None only when AURA_WHATSAPP_PHONE_NUMBER_ID is blank, and on that
    # install send_whatsapp_template_message raises WhatsAppNotConfiguredError
    # before it ever builds an Authorization header -- there is no token in
    # flight to leak. Pass 2 runs unconditionally either way.
    token = (cfg.access_token if cfg is not None else '') or ''
    if len(token) >= _MIN_REDACTABLE_TOKEN_CHARS:
        text = text.replace(token, _REDACTED)

    return _CREDENTIAL_RE.sub('Bearer ' + _REDACTED, text)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _describe_send_failure(exc: BaseException) -> str:
    """Turns a send exception into the short, specific reason that gets
    persisted to whatsapp_outbox.last_error and shown in the queue UI.

    WHY this function exists (real shipped bug): this used to be plain
    `type(exc).__name__`, and there was no logging call anywhere in this
    module. So a recipient that is not on Meta's allowed list (131030), a
    template that does not exist (132001), an expired/revoked access token
    (190) and a plain DNS outage were all persisted as the identical string
    "WhatsAppSendError" -- a live send failure was undiagnosable from both the
    UI and the app log. whatsapp_client.WhatsAppSendError had been carrying
    .status_code/.api_error the whole time precisely "so a retry/
    failed-permanent decision can be made on the actual reason, not just a
    generic HTTP status" (its own docstring); the worker was simply throwing
    that away. Always keep Meta's numeric error code in the output -- it is
    the field Meta's own error documentation is indexed by.

    The access token must never appear in the output. The original version of
    this docstring asserted that it could not -- "whatsapp_client only ever
    sends it as an Authorization header, never in the URL or the JSON body" --
    which is true of whatsapp_client and still false as a safety claim: on the
    `else` branch below, `str(exc)` is requests' own message, and requests >=
    2.32 puts the rejected header VALUE (i.e. 'Bearer <token>') into
    InvalidHeader's text, which a reviewer reproduced with a real token. So
    nothing here is assumed any more: what guarantees the output is safe is
    that EVERY branch funnels through _redact_credentials() before the length
    cap, and that helper scrubs both by value (the configured token) and by
    shape (any 'Bearer ...' run). The 300-char cap is not part of that
    guarantee -- the leaked token sits at the FRONT of the InvalidHeader
    message, so truncation alone never removed it.
    """
    if isinstance(exc, WhatsAppSendError):
        # api_error is whatever was under the response's "error" key -- Meta
        # always sends an object there, but tolerate a non-dict rather than
        # blowing up inside the failure path itself.
        api_error = exc.api_error if isinstance(exc.api_error, dict) else {}
        code = api_error.get('code')
        parts = [
            'HTTP %s' % exc.status_code,
            'Meta error %s' % code if code is not None else 'Meta error (no code)',
            str(api_error.get('message') or 'unknown WhatsApp API error'),
        ]
        # error_data.details is Meta's human-readable "why", and is often far
        # more actionable than the generic top-level message.
        error_data = api_error.get('error_data')
        if isinstance(error_data, dict) and error_data.get('details'):
            parts.append(str(error_data['details']))
        reason = ' | '.join(parts)
    else:
        # No Meta code exists for a transport/config failure (requests
        # ConnectionError/Timeout, WhatsAppNotConfiguredError, a malformed
        # JSON body). Fall back to the class name, plus the exception's own
        # text when it has any, so a DNS failure stays distinguishable from a
        # not-configured installation at a glance.
        detail = str(exc).strip()
        reason = '%s: %s' % (type(exc).__name__, detail) if detail else type(exc).__name__

    # Redact BEFORE collapsing whitespace: an embedded CR/LF inside a leaked
    # token is what splits it into two words, and collapsing first would turn
    # the tail half into an innocuous-looking separate word that a
    # whitespace-bounded pattern could no longer recognise.
    reason = _redact_credentials(reason)
    reason = ' '.join(reason.split())  # collapse newlines/runs -- one-line cell
    if len(reason) > _MAX_STORED_ERROR_CHARS:
        reason = reason[:_MAX_STORED_ERROR_CHARS - 3] + '...'
    return reason


def compute_backoff_delay(attempt_count: int) -> float:
    """Identical shape to notifications/worker.py's compute_backoff_delay --
    same deliberate small duplication rather than a shared helper; see that
    function's own docstring."""
    delay = min(_BACKOFF_BASE_SECONDS * (2 ** attempt_count), _BACKOFF_CAP_SECONDS)
    return delay * random.uniform(0.8, 1.2)


class WhatsAppOutboxWorker:
    def __init__(
        self,
        *,
        conn_factory: Callable[[], object],
        company_id,
        batch_size: int = 25,
        lease_seconds: int = 120,
        rate_limit_seconds: float = 0.5,
    ):
        self._conn_factory = conn_factory
        self._company_id = company_id
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._rate_limit_seconds = rate_limit_seconds
        self._timer: Optional[threading.Timer] = None
        self._stopped = threading.Event()

    # ─── scheduling (mirrors EmailOutboxWorker) ─────────────────────────

    def start(self, interval_seconds: int) -> None:
        self._stopped.clear()
        self._schedule_next(interval_seconds)

    def stop(self) -> None:
        self._stopped.set()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _schedule_next(self, interval_seconds: int) -> None:
        if self._stopped.is_set():
            return

        def _tick():
            try:
                self.run_once()
            finally:
                self._schedule_next(interval_seconds)

        self._timer = threading.Timer(interval_seconds, _tick)
        self._timer.daemon = True
        self._timer.start()

    # ─── one pass ───────────────────────────────────────────────────────

    def run_once(self) -> dict:
        conn = self._conn_factory()
        try:
            if not whatsapp_settings.is_enabled(conn, self._company_id):
                # Re-checked every tick, before any claim -- identical
                # placement/reasoning to EmailOutboxWorker.run_once.
                return {'ran': False, 'reason': 'disabled'}

            repo = WhatsAppOutboxRepository(conn)

            reclaimed = repo.reclaim_expired_leases()
            conn.commit()

            claimed = repo.claim_due(
                company_id=self._company_id, batch_size=self._batch_size, lease_seconds=self._lease_seconds,
            )
            conn.commit()

            outcomes = {'sent': 0, 'retry': 0, 'failed_permanent': 0}
            for i, row in enumerate(claimed):
                outcome = self._process_row(conn, repo, row)
                conn.commit()
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
                if self._rate_limit_seconds and i < len(claimed) - 1:
                    time.sleep(self._rate_limit_seconds)

            return {'ran': True, 'claimed': len(claimed), 'reclaimed': reclaimed, 'outcomes': outcomes}
        finally:
            conn.close()

    # ─── per-row send ────────────────────────────────────────────────────

    def _process_row(self, conn, repo: WhatsAppOutboxRepository, row) -> str:
        params = json.loads(row['component_params_json']) if row['component_params_json'] else None
        try:
            wamid = send_whatsapp_template_message(
                recipient_phone_e164=row['recipient_phone_e164'],
                template_name=row['template_name'],
                language_code=row['language_code'],
                component_params=params,
            )
        except Exception as exc:
            reason = _describe_send_failure(exc)
            outcome = self._apply_retry(conn, repo, row, error=reason)
            # Log as well as persist: last_error is only ever seen by somebody
            # who opens the WhatsApp queue card, while the scheduled daemon-
            # timer path (_schedule_next -> run_once) has no UI watching it at
            # all -- before this, a queue failing every tick left no trace
            # anywhere. WARNING while the row is still retryable, ERROR once
            # it is dead, so a real delivery failure is greppable by level.
            log.log(
                logging.ERROR if outcome == 'failed_permanent' else logging.WARNING,
                'WhatsApp send failed: outbox id=%s company=%s type=%s template=%s attempt=%s outcome=%s reason=%s',
                row['id'], self._company_id, row['message_type'], row['template_name'],
                row['attempt_count'] + 1, outcome, reason,
            )
            return outcome

        repo.mark_sent(row['id'], wamid=wamid)
        return 'sent'

    def _apply_retry(self, conn, repo: WhatsAppOutboxRepository, row, *, error: str) -> str:
        max_attempts = int(whatsapp_settings.get_setting(conn, self._company_id, 'max_attempts'))
        attempt_after = row['attempt_count'] + 1
        if attempt_after >= max_attempts:
            repo.mark_failed_permanent(row['id'], error=error)
            return 'failed_permanent'

        next_attempt_at = (datetime.now(timezone.utc)
                            + timedelta(seconds=compute_backoff_delay(row['attempt_count']))).isoformat()
        repo.mark_retry(row['id'], error=error, next_attempt_at=next_attempt_at)
        return 'retry'
