"""Outbound WhatsApp -- product-side HTTP surface.

Mirrors commercial_runtime/notifications/routes.py's make_notifications_
blueprint(...) factory pattern exactly (session-derived company_id, admin-
gated settings/mutations, session-gated read-only status/outbox) -- see that
file's docstring for the full rationale. Adds a /recipients CRUD surface on
top, since WhatsApp needs the multi-recipient/role/branch routing email never
did (see whatsapp_recipients.py's own docstring).
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Optional

from flask import Blueprint, jsonify, request, session

from . import whatsapp_settings
from . import whatsapp_client
from . import whatsapp_recipients as recipients_repo
from .whatsapp_outbox import WhatsAppOutboxRepository


def _cid():
    # Matches commercial_runtime/notifications/routes.py::_cid()'s identical
    # convention -- company_id always comes from the server-side session,
    # never a client-supplied request value.
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
        return jsonify({'status': 'error', 'message': 'WhatsApp settings require an administrator account'}), 403
    return None


def make_whatsapp_notifications_blueprint(
    *,
    conn_factory: Callable[[], object],
    get_worker: Optional[Callable[[object], object]] = None,
):
    """`get_worker(company_id) -> WhatsAppOutboxWorker` is a factory/
    registry, not a single instance -- identical reasoning to
    notifications/routes.py::make_notifications_blueprint's `get_worker`
    param; see products/retail/backend/app.py's
    `_get_or_create_whatsapp_worker` for the reference implementation."""
    bp = Blueprint('whatsapp_notifications_api', __name__, url_prefix='/api/notifications/whatsapp')

    @bp.route('/status', methods=['GET'])
    def _status():
        denied = _require_session()
        if denied:
            return denied
        conn = conn_factory()
        try:
            cid = _cid()
            enabled = whatsapp_settings.is_enabled(conn, cid)
            counts = WhatsAppOutboxRepository(conn).counts_by_state(cid)
            recipient_count = len(recipients_repo.list_recipients(conn, cid))
            return jsonify({
                'status': 'success',
                'data': {
                    'enabled': enabled,
                    'whatsapp_configured': whatsapp_client.is_configured(),
                    'counts_by_state': counts,
                    'recipient_count': recipient_count,
                },
            })
        finally:
            conn.close()

    # ─── settings (enabled toggle, template names, template body previews) ──

    @bp.route('/settings', methods=['GET'])
    def _get_settings():
        denied = _require_admin()
        if denied:
            return denied
        conn = conn_factory()
        try:
            cid = _cid()
            return jsonify({'status': 'success', 'data': {'settings': whatsapp_settings.get_all_settings(conn, cid)}})
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
            was_enabled = whatsapp_settings.is_enabled(conn, cid)
            for key, value in payload.items():
                try:
                    whatsapp_settings.set_setting(conn, cid, key, str(value))
                except (whatsapp_settings.UnknownSettingError, whatsapp_settings.InvalidSettingValueError) as exc:
                    conn.rollback()
                    return jsonify({'status': 'error', 'message': str(exc)}), 400
            conn.commit()
            now_enabled = whatsapp_settings.is_enabled(conn, cid)

            if get_worker is not None:
                if now_enabled and not was_enabled:
                    get_worker(cid).start(
                        interval_seconds=int(whatsapp_settings.get_setting(conn, cid, 'submit_interval_seconds'))
                    )
                elif was_enabled and not now_enabled:
                    get_worker(cid).stop()

            return jsonify({'status': 'success', 'data': whatsapp_settings.get_all_settings(conn, cid)})
        finally:
            conn.close()

    # ─── recipients CRUD ──────────────────────────────────────────────────

    @bp.route('/recipients', methods=['GET'])
    def _list_recipients():
        denied = _require_admin()
        if denied:
            return denied
        conn = conn_factory()
        try:
            return jsonify({'status': 'success', 'data': recipients_repo.list_recipients(conn, _cid())})
        finally:
            conn.close()

    @bp.route('/recipients', methods=['POST'])
    def _create_recipient():
        denied = _require_admin()
        if denied:
            return denied
        payload = request.get_json(silent=True) or {}
        conn = conn_factory()
        try:
            cid = _cid()
            recipient_id = recipients_repo.create_recipient(
                conn, cid,
                display_name=payload.get('display_name', ''),
                phone_e164=payload.get('phone_e164', ''),
                role_label=payload.get('role_label'),
                branch_id=payload.get('branch_id'),
                report_types=payload.get('report_types', []),
                language_code=payload.get('language_code', 'en_US'),
            )
            conn.commit()
            return jsonify({'status': 'success', 'data': {'id': recipient_id}})
        except recipients_repo.InvalidRecipientError as exc:
            conn.rollback()
            return jsonify({'status': 'error', 'message': str(exc)}), 400
        finally:
            conn.close()

    @bp.route('/recipients/<recipient_id>', methods=['PUT'])
    def _update_recipient(recipient_id):
        denied = _require_admin()
        if denied:
            return denied
        payload = request.get_json(silent=True) or {}
        conn = conn_factory()
        try:
            cid = _cid()
            recipients_repo.update_recipient(conn, cid, recipient_id, **payload)
            conn.commit()
            return jsonify({'status': 'success'})
        except recipients_repo.RecipientNotFoundError as exc:
            conn.rollback()
            return jsonify({'status': 'error', 'message': str(exc)}), 404
        except recipients_repo.InvalidRecipientError as exc:
            conn.rollback()
            return jsonify({'status': 'error', 'message': str(exc)}), 400
        finally:
            conn.close()

    @bp.route('/recipients/<recipient_id>', methods=['DELETE'])
    def _delete_recipient(recipient_id):
        denied = _require_admin()
        if denied:
            return denied
        conn = conn_factory()
        try:
            cid = _cid()
            recipients_repo.delete_recipient(conn, cid, recipient_id)
            conn.commit()
            return jsonify({'status': 'success'})
        except recipients_repo.RecipientNotFoundError as exc:
            conn.rollback()
            return jsonify({'status': 'error', 'message': str(exc)}), 404
        finally:
            conn.close()

    # ─── outbox (queue health) ────────────────────────────────────────────

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
            base_cols = (
                "id, company_id, message_type, recipient_phone_e164, template_name, language_code, "
                "status, attempt_count, created_at, updated_at, sent_at, wamid, last_error"
            )
            if status_filter:
                rows = conn.execute(
                    f"SELECT {base_cols} FROM whatsapp_outbox "
                    "WHERE company_id=? AND status=? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (cid, status_filter, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {base_cols} FROM whatsapp_outbox "
                    "WHERE company_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (cid, limit, offset),
                ).fetchall()
            # Deliberately never selects component_params_json -- queue
            # health/visibility only, same "not for reading content over the
            # API" convention as notifications/routes.py::_list_outbox.
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
