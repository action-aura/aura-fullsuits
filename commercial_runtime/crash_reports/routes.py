"""HTTP surface of crash reports: /api/crash-reports/*.

  POST /client    any signed-in user -- the page's own JS reports a browser error
  GET  /summary   administrator -- how many reports are waiting / were sent
  POST /diagnostics  administrator -- queue a support snapshot, answer with its reference code

Why /client requires a signed-in user: the till's pages only ever run for a
signed-in cashier, so a request with no session is not our JS; refusing it (401)
keeps strangers on the same network from filling the local queue.

Why the answer to /client is an empty 204: the page learns nothing from
reporting except ONE thing, added by SUPPORT-1: a recorded report answers with
its 6-character reference code in the X-Aura-Report-Code header so the shop can
quote it to support. Over the rate limit, a malformed-but-small body, a store
that failed -- all still look the same to the page (a bare 204, no header). Only
the things the page can actually act on differ: not signed in (401) and body too
large (413).

This blueprint is NOT behind the retail licence guard, for the same reason the
update channel is not: an expired or restricted shop is exactly the shop whose
crashes we most want to hear about, and recording a crash locally mutates no
business data.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Callable, Optional

from flask import Blueprint, jsonify, request, session

from commercial_runtime.backup.routes import _require_admin

from .capture import REPORT_CODE_HEADER, build_diagnostics_report, report_from_client
from .reference_code import reference_code
from .store import CrashStore

log = logging.getLogger("aura.crash_reports")

MAX_BODY_BYTES = 16 * 1024
CLIENT_REPORT_LIMIT = 20
CLIENT_REPORT_WINDOW_SECONDS = 10 * 60


class ClientReportLimiter:
    """At most `limit` client reports per `window` seconds, per process, in
    memory. A render loop that throws on every frame would otherwise write the
    queue full in seconds; the first 20 are the useful ones anyway."""

    def __init__(self, limit: int = CLIENT_REPORT_LIMIT, window: float = CLIENT_REPORT_WINDOW_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self._limit = limit
        self._window = window
        self._clock = clock
        self._hits = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = self._clock()
        with self._lock:
            while self._hits and now - self._hits[0] >= self._window:
                self._hits.popleft()
            if len(self._hits) >= self._limit:
                return False
            self._hits.append(now)
            return True


def make_crash_reports_blueprint(
    store: Optional[CrashStore],
    *,
    app_version: str,
    platform: str,
    sending_enabled: bool,
    limiter: Optional[ClientReportLimiter] = None,
    on_diagnostics_recorded: Optional[Callable[[], None]] = None,
) -> Blueprint:
    """`sending_enabled` is whether a sender thread was started (an Owner URL
    is configured); it is reported by /summary so support can tell "nothing
    waiting" from "nothing will ever be sent"."""
    bp = Blueprint("crash_reports_api", __name__, url_prefix="/api/crash-reports")
    rate = limiter or ClientReportLimiter()

    @bp.route('/client', methods=['POST'])
    def client_error():
        if 'mt_user_id' not in session:
            return jsonify({'error': 'Authentication required', 'code': 401}), 401
        declared = request.content_length
        if declared is not None and declared > MAX_BODY_BYTES:
            return jsonify({'error': 'Report too large', 'code': 413}), 413
        # Read one byte past the cap: a chunked body has no Content-Length to
        # check up front, and get_data() would read it all.
        raw = request.stream.read(MAX_BODY_BYTES + 1)
        if len(raw) > MAX_BODY_BYTES:
            return jsonify({'error': 'Report too large', 'code': 413}), 413
        if not rate.allow():
            log.info("crash reports: client report dropped -- rate limit reached")
            return '', 204
        if store is None:
            return '', 204
        try:
            body = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return jsonify({'error': 'Malformed report', 'code': 400}), 400
        if not isinstance(body, dict):
            return jsonify({'error': 'Malformed report', 'code': 400}), 400
        try:
            report = report_from_client(
                message=body.get('message'), stack=body.get('stack'), screen=body.get('screen'),
                app_version=app_version, platform=platform)
            store.record(report)
        except Exception:  # noqa: BLE001 -- the page must never see a reporting failure
            log.warning("crash reports: could not record a client report", exc_info=True)
            return '', 204
        # SUPPORT-1 item 3: the body stays empty; the 6-character code of the
        # report that WAS saved rides a header so the page can tell the user
        # "a report was saved (code ...)". Every case where nothing was saved
        # (rate limit, no store, store failure) answers without it -- so the
        # page can show a code only when a report really exists.
        return '', 204, {REPORT_CODE_HEADER: reference_code(report["fingerprint"])}

    @bp.route('/diagnostics', methods=['POST'])
    def send_diagnostics():
        """"Send diagnostics to support": queue a snapshot for Owner and hand
        back its reference code. Administrator only -- the same tier as the
        diagnostics export it summarises (CAP_EMPLOYEES). The page sends the
        facts it just read from that export; only a fixed list of them is
        used (build_diagnostics_report), never free text."""
        denied = _require_admin()
        if denied:
            return denied
        if store is None:
            return jsonify({'error': 'Reports are unavailable on this computer', 'code': 503}), 503
        declared = request.content_length
        if declared is not None and declared > MAX_BODY_BYTES:
            return jsonify({'error': 'Report too large', 'code': 413}), 413
        raw = request.stream.read(MAX_BODY_BYTES + 1)
        if len(raw) > MAX_BODY_BYTES:
            return jsonify({'error': 'Report too large', 'code': 413}), 413
        try:
            facts = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return jsonify({'error': 'Malformed report', 'code': 400}), 400
        if not isinstance(facts, dict):
            return jsonify({'error': 'Malformed report', 'code': 400}), 400
        try:
            report = build_diagnostics_report(facts, app_version=app_version, platform=platform)
            store.record(report)
        except Exception:  # noqa: BLE001
            log.warning("crash reports: could not record a diagnostics snapshot", exc_info=True)
            return jsonify({'error': 'Could not save the report', 'code': 500}), 500
        if on_diagnostics_recorded is not None:
            try:
                on_diagnostics_recorded()
            except Exception:  # noqa: BLE001 -- an eager send is a courtesy; the 30-minute run still sends it
                log.warning("crash reports: eager send after a diagnostics snapshot failed", exc_info=True)
        return jsonify({'status': 'success', 'data': {
            'reference_code': reference_code(report["fingerprint"]),
            # Whether anything will ever leave this till (an Owner is configured).
            'sending_enabled': bool(sending_enabled),
        }}), 200

    @bp.route('/summary', methods=['GET'])
    def summary():
        denied = _require_admin()
        if denied:
            return denied
        counts = store.summary() if store is not None else {"pending": 0, "sent": 0, "last_sent_at": None}
        return jsonify({**counts, "enabled": bool(sending_enabled)}), 200

    return bp
