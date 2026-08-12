"""Outbound email -- product-side HTTP surface.

Mirrors commercial_runtime/einvoicing/routes.py's make_*_blueprint(...)
factory pattern -- see that file's docstring for the full rationale. Kept
deliberately smaller than einvoicing's blueprint: no credentials box (SMTP
credentials are env-var only, never stored in the DB -- see
smtp_client.py), no QR endpoints, no file-based killswitch endpoint (see
settings.py's own docstring for why AURA_SMTP_HOST unset already serves as
this feature's kill switch). What's left is exactly what an operator needs
to configure recipients, see queue health, and manually kick the worker for
support/testing -- the same subset einvoicing's own /status, /settings, and
/outbox/run-once routes cover.
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Optional

from flask import Blueprint, jsonify, request, session

from . import settings
from . import smtp_client
from .outbox import EmailOutboxRepository


def _cid():
    # Matches products/retail/backend/api/retail_api.py::_cid() /
    # commercial_runtime/einvoicing/routes.py's identical convention --
    # company_id always comes from the server-side session, never a
    # client-supplied request value.
    return session.get('company_id') or session.get('mt_company_id', 1)


def _require_session():
    if 'mt_user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
    return None


def _require_admin():
    denied = _require_session()
    if denied:
        return denied
    if session.get('mt_role') != 'admin':
        return jsonify({'status': 'error', 'message': 'Notification settings require an administrator account'}), 403
    return None


def make_notifications_blueprint(
    *,
    conn_factory: Callable[[], object],
    get_worker: Optional[Callable[[object], object]] = None,
):
    """`get_worker(company_id) -> EmailOutboxWorker` is a factory/registry,
    not a single instance -- identical reasoning to
    einvoicing/routes.py::make_einvoicing_blueprint's `get_worker` param;
    see products/retail/backend/app.py's `_get_or_create_notifications_worker`
    for the reference implementation."""
    bp = Blueprint('notifications_api', __name__, url_prefix='/api/notifications')

    @bp.route('/status', methods=['GET'])
    def _status():
        denied = _require_session()
        if denied:
            return denied
        conn = conn_factory()
        try:
            cid = _cid()
            enabled = settings.is_enabled(conn, cid)
            counts = EmailOutboxRepository(conn).counts_by_state(cid)
            return jsonify({
                'status': 'success',
                'data': {
                    'enabled': enabled,
                    'smtp_configured': smtp_client.is_configured(),
                    'counts_by_state': counts,
                },
            })
        finally:
            conn.close()

    @bp.route('/settings', methods=['GET'])
    def _get_settings():
        denied = _require_admin()
        if denied:
            return denied
        conn = conn_factory()
        try:
            cid = _cid()
            return jsonify({'status': 'success', 'data': {'settings': settings.get_all_settings(conn, cid)}})
        finally:
            conn.close()

    @bp.route('/settings', methods=['POST'])
    def _post_settings():
        denied = _require_admin()
        if denied:
            return denied
        payload = request.get_json(silent=True) or {}
        conn = conn_factory()
        try:
            cid = _cid()
            was_enabled = settings.is_enabled(conn, cid)
            for key, value in payload.items():
                try:
                    settings.set_setting(conn, cid, key, str(value))
                except settings.UnknownSettingError as exc:
                    conn.rollback()
                    return jsonify({'status': 'error', 'message': str(exc)}), 400
            conn.commit()
            now_enabled = settings.is_enabled(conn, cid)

            if get_worker is not None:
                if now_enabled and not was_enabled:
                    get_worker(cid).start(interval_seconds=int(settings.get_setting(conn, cid, 'submit_interval_seconds')))
                elif was_enabled and not now_enabled:
                    get_worker(cid).stop()

            return jsonify({'status': 'success', 'data': settings.get_all_settings(conn, cid)})
        finally:
            conn.close()

    @bp.route('/outbox', methods=['GET'])
    def _list_outbox():
        denied = _require_session()
        if denied:
            return denied
        conn = conn_factory()
        try:
            cid = _cid()
            status_filter = request.args.get('status')
            limit = min(int(request.args.get('limit', 50)), 200)
            offset = max(int(request.args.get('offset', 0)), 0)
            conn.row_factory = sqlite3.Row
            if status_filter:
                rows = conn.execute(
                    "SELECT id, company_id, email_type, recipient, subject, status, attempt_count, "
                    "created_at, updated_at, sent_at, last_error FROM email_outbox "
                    "WHERE company_id=? AND status=? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (cid, status_filter, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, company_id, email_type, recipient, subject, status, attempt_count, "
                    "created_at, updated_at, sent_at, last_error FROM email_outbox "
                    "WHERE company_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (cid, limit, offset),
                ).fetchall()
            # Deliberately never selects body_text/body_html in a list view --
            # this endpoint is for queue-health visibility, not for reading
            # mail contents over the API.
            return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})
        finally:
            conn.close()

    @bp.route('/outbox/run-once', methods=['POST'])
    def _run_once():
        denied = _require_admin()
        if denied:
            return denied
        if get_worker is None:
            return jsonify({'status': 'error', 'message': 'No worker configured for this installation'}), 503
        result = get_worker(_cid()).run_once()
        return jsonify({'status': 'success', 'data': result})

    return bp
