"""JoFotara e-invoicing -- product-side HTTP surface.

Mirrors commercial_runtime/backup/routes.py's and
commercial_runtime/licensing_contracts/routes.py's make_*_blueprint(...)
factory pattern -- each product's app.py calls this once, exactly like it
already does for make_backup_blueprint()/make_licensing_blueprint(). Never
imports anything product-specific (no Retail/Clinic import here) -- the
product supplies its own DocumentAdapter (Step 13/14) and a conn_factory
for its own database.

Deliberate design decision, recorded here because it's easy to get backwards:
submission-related routes (/status, /outbox/*, /qr/*) are NEVER
license-capability-gated. Tax compliance is a legal obligation; a licensing
restriction must not be able to create a compliance failure by locking a
merchant out of seeing or retrying their own e-invoice queue. This mirrors
licensing_contracts/routes.py's own reasoning for why its own status/
activate/check-in routes are never capability-gated. Only settings/
credential WRITES optionally carry a capability_guard, and even that is
opt-in (callers may omit it entirely for Phase 1).
"""
from __future__ import annotations

import base64
import sqlite3
from typing import Callable, Optional

from flask import Blueprint, Response, jsonify, request, session

from . import audit, killswitch, settings
from .credentials import EInvoiceCredentials, get_secret_box
from .outbox import OutboxRepository
from .qr import qr_for_outbox_row, render_qr_png_data_uri, render_qr_svg_data_uri


def _cid():
    # Matches products/retail/backend/api/retail_api.py::_cid() /
    # products/clinic/backend/api/clinic_api.py's identical convention --
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
        return jsonify({'status': 'error', 'message': 'E-invoicing settings require an administrator account'}), 403
    return None


def make_einvoicing_blueprint(
    *,
    product_code: str,
    platform: str,
    app_data_dir: str,
    conn_factory: Callable[[], object],
    get_worker: Optional[Callable[[int], object]] = None,
    provider=None,
    settings_capability: Optional[str] = None,
    capability_guard: Optional[Callable[[str], Callable]] = None,
):
    """`get_worker(company_id) -> OutboxWorker` is a factory/registry, not a
    single instance -- this codebase's registry is genuinely multi-tenant
    (one install CAN host more than one company; see
    commercial_runtime/identity/registry_db.py), so there is no single
    "the" company to build one fixed worker for at blueprint-creation time.
    The product's app.py owns a per-company worker cache and passes a
    lookup function here; see products/retail/backend/app.py's
    `_get_or_create_einvoicing_worker` for the reference implementation."""
    bp = Blueprint('einvoicing_api', __name__, url_prefix='/api/einvoicing')
    secret_box_factory = lambda: get_secret_box(app_data_dir, platform)

    def _maybe_capability_gated(fn):
        if settings_capability and capability_guard:
            return capability_guard(settings_capability)(fn)
        return fn

    @bp.route('/status', methods=['GET'])
    def _status():
        denied = _require_session()
        if denied:
            return denied
        conn = conn_factory()
        try:
            cid = _cid()
            enabled = settings.is_enabled(conn, app_data_dir, cid)
            provider_name = settings.get_setting(conn, cid, 'provider')
            ks = killswitch.is_disabled(app_data_dir)
            counts = OutboxRepository(conn).counts_by_state(cid)
            oldest_row = conn.execute(
                "SELECT created_at FROM einvoice_outbox WHERE company_id=? AND status IN "
                "('QUEUED','SUBMITTING','SUBMITTING_UNKNOWN','AWAITING_CLEARANCE') "
                "ORDER BY created_at ASC LIMIT 1", (cid,)
            ).fetchone()
            return jsonify({
                'status': 'success',
                'data': {
                    'enabled': enabled,
                    'provider': provider_name,
                    'killswitch': {'disabled': ks.disabled, 'reason': ks.reason},
                    'counts_by_state': counts,
                    'oldest_pending_created_at': oldest_row[0] if oldest_row else None,
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
            all_settings = settings.get_all_settings(conn, cid)
            description = secret_box_factory().describe()
            return jsonify({'status': 'success', 'data': {'settings': all_settings, 'credentials': description}})
        finally:
            conn.close()

    @_maybe_capability_gated
    @bp.route('/settings', methods=['POST'])
    def _post_settings():
        denied = _require_admin()
        if denied:
            return denied
        payload = request.get_json(silent=True) or {}
        conn = conn_factory()
        try:
            cid = _cid()
            was_enabled = settings.is_enabled(conn, app_data_dir, cid)
            for key, value in payload.items():
                try:
                    settings.set_setting(conn, cid, key, str(value))
                except settings.UnknownSettingError as exc:
                    conn.rollback()
                    return jsonify({'status': 'error', 'message': str(exc)}), 400
            conn.commit()
            now_enabled = settings.is_enabled(conn, app_data_dir, cid)

            if now_enabled != was_enabled:
                audit.record(conn, app_data_dir, company_id=cid,
                             event='FEATURE_ENABLED' if now_enabled else 'FEATURE_DISABLED',
                             details={'keys_changed': sorted(payload.keys())})
                conn.commit()

            if get_worker is not None:
                if now_enabled and not was_enabled:
                    get_worker(cid).start(interval_seconds=int(settings.get_setting(conn, cid, 'submit_interval_seconds')))
                elif was_enabled and not now_enabled:
                    get_worker(cid).stop()

            return jsonify({'status': 'success', 'data': settings.get_all_settings(conn, cid)})
        finally:
            conn.close()

    @_maybe_capability_gated
    @bp.route('/credentials', methods=['POST'])
    def _post_credentials():
        denied = _require_admin()
        if denied:
            return denied
        payload = request.get_json(silent=True) or {}
        client_id = payload.get('client_id')
        client_secret = payload.get('client_secret')
        if not client_id or not client_secret:
            return jsonify({'status': 'error', 'message': 'client_id and client_secret are required'}), 400

        box = secret_box_factory()
        box.store(EInvoiceCredentials(client_id=client_id, client_secret=client_secret))

        conn = conn_factory()
        try:
            audit.record(conn, app_data_dir, company_id=_cid(), event='CREDENTIALS_STORED',
                         details={'backend': box.backend_name})
            conn.commit()
        finally:
            conn.close()
        # Never echo the credentials back -- describe() only.
        return jsonify({'status': 'success', 'data': box.describe()})

    @_maybe_capability_gated
    @bp.route('/credentials', methods=['DELETE'])
    def _delete_credentials():
        denied = _require_admin()
        if denied:
            return denied
        box = secret_box_factory()
        box.wipe()
        conn = conn_factory()
        try:
            audit.record(conn, app_data_dir, company_id=_cid(), event='CREDENTIALS_WIPED')
            conn.commit()
        finally:
            conn.close()
        return jsonify({'status': 'success'})

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
                    "SELECT * FROM einvoice_outbox WHERE company_id=? AND status=? "
                    "ORDER BY id DESC LIMIT ? OFFSET ?",
                    (cid, status_filter, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM einvoice_outbox WHERE company_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (cid, limit, offset),
                ).fetchall()
            return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})
        finally:
            conn.close()

    @bp.route('/outbox/<path:invoice_ref>', methods=['GET'])
    def _get_outbox_entry(invoice_ref):
        denied = _require_session()
        if denied:
            return denied
        conn = conn_factory()
        try:
            cid = _cid()
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM einvoice_outbox WHERE company_id=? AND invoice_ref=?", (cid, invoice_ref)
            ).fetchone()
            if not row:
                return jsonify({'status': 'error', 'message': 'Not found'}), 404
            attempts = audit.recent(conn, cid, invoice_ref=invoice_ref)
            return jsonify({'status': 'success', 'data': {'entry': dict(row), 'attempts': [dict(a) for a in attempts]}})
        finally:
            conn.close()

    @bp.route('/outbox/<path:invoice_ref>/retry', methods=['POST'])
    def _retry_outbox_entry(invoice_ref):
        denied = _require_admin()
        if denied:
            return denied
        conn = conn_factory()
        try:
            cid = _cid()
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, status FROM einvoice_outbox WHERE company_id=? AND invoice_ref=?", (cid, invoice_ref)
            ).fetchone()
            if not row:
                return jsonify({'status': 'error', 'message': 'Not found'}), 404
            if row['status'] != 'FAILED_PERMANENT':
                return jsonify({'status': 'error', 'message': 'Only a FAILED_PERMANENT entry can be manually retried'}), 400
            conn.execute(
                "UPDATE einvoice_outbox SET status='QUEUED', attempt_count=0, next_attempt_at=NULL, "
                "lease_expires_at=NULL, last_reason_code='MANUAL_RETRY' WHERE id=?",
                (row['id'],),
            )
            audit.record(conn, app_data_dir, company_id=cid, event='MANUAL_RETRY', invoice_ref=invoice_ref)
            conn.commit()
            return jsonify({'status': 'success'})
        finally:
            conn.close()

    @bp.route('/outbox/<path:invoice_ref>/cancel', methods=['POST'])
    def _cancel_outbox_entry(invoice_ref):
        denied = _require_admin()
        if denied:
            return denied
        conn = conn_factory()
        try:
            cid = _cid()
            row = conn.execute(
                "SELECT id FROM einvoice_outbox WHERE company_id=? AND invoice_ref=?", (cid, invoice_ref)
            ).fetchone()
            if not row:
                return jsonify({'status': 'error', 'message': 'Not found'}), 404
            ok = OutboxRepository(conn).cancel(row[0])
            if ok:
                audit.record(conn, app_data_dir, company_id=cid, event='ENTRY_CANCELLED', invoice_ref=invoice_ref)
            conn.commit()
            if not ok:
                return jsonify({'status': 'error', 'message': 'Entry is already in a terminal state and cannot be cancelled'}), 400
            return jsonify({'status': 'success'})
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

    @bp.route('/qr/<path:invoice_ref>.svg', methods=['GET'])
    def _qr_svg(invoice_ref):
        return _qr_response(invoice_ref, render_svg=True)

    @bp.route('/qr/<path:invoice_ref>.png', methods=['GET'])
    def _qr_png(invoice_ref):
        return _qr_response(invoice_ref, render_svg=False)

    def _qr_response(invoice_ref, *, render_svg):
        denied = _require_session()
        if denied:
            return denied
        conn = conn_factory()
        try:
            cid = _cid()
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT status, qr_payload, qr_image_base64 FROM einvoice_outbox WHERE company_id=? AND invoice_ref=?",
                (cid, invoice_ref),
            ).fetchone()
            if not row or row['status'] != 'CLEARED':
                return jsonify({'status': 'error', 'message': 'No cleared QR code for this invoice'}), 404

            if row['qr_image_base64']:
                # The tax authority's own rendering -- format is whatever
                # ISTD gave us (typically PNG), served as-is regardless of
                # which extension was requested; we cannot losslessly
                # convert a raster image the authority returned into SVG.
                data_uri = qr_for_outbox_row(dict(row))
            elif row['qr_payload']:
                data_uri = render_qr_svg_data_uri(row['qr_payload']) if render_svg else render_qr_png_data_uri(row['qr_payload'])
            else:
                data_uri = None

            if data_uri is None:
                return jsonify({'status': 'error', 'message': 'No QR data available'}), 404

            encoded = data_uri.split(',', 1)[1]
            raw = base64.b64decode(encoded)
            mimetype = 'image/svg+xml' if data_uri.startswith('data:image/svg+xml') else 'image/png'
            return Response(raw, mimetype=mimetype)
        finally:
            conn.close()

    @bp.route('/killswitch', methods=['POST'])
    def _post_killswitch():
        denied = _require_admin()
        if denied:
            return denied
        payload = request.get_json(silent=True) or {}
        reason = payload.get('reason', 'manual')
        minutes = payload.get('minutes')
        killswitch.set_disabled(app_data_dir, reason=reason, minutes=minutes)
        conn = conn_factory()
        try:
            audit.record(conn, app_data_dir, company_id=_cid(), event='KILLSWITCH_SET', details={'reason': reason})
            conn.commit()
        finally:
            conn.close()
        return jsonify({'status': 'success'})

    @bp.route('/killswitch', methods=['DELETE'])
    def _delete_killswitch():
        denied = _require_admin()
        if denied:
            return denied
        killswitch.clear_disabled(app_data_dir)
        conn = conn_factory()
        try:
            audit.record(conn, app_data_dir, company_id=_cid(), event='KILLSWITCH_CLEARED')
            conn.commit()
        finally:
            conn.close()
        return jsonify({'status': 'success'})

    @bp.route('/selftest', methods=['GET'])
    def _selftest():
        denied = _require_admin()
        if denied:
            return denied
        if provider is None:
            return jsonify({'status': 'error', 'message': 'No provider configured for this installation'}), 503
        result = provider.selftest()
        return jsonify({'status': 'success', 'data': {
            'outcome': result.outcome, 'provider_uuid': result.provider_uuid,
            'reason_code': result.reason_code, 'detail': result.detail,
        }})

    return bp
