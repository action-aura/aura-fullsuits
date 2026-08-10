"""
Aura FullSuits -- per-device login / "one Admin Device": read-only HTTP
visibility routes (Phase 1, this week).

Two GET-only endpoints, no mutation of any kind -- Wednesday's slice of
docs/superpowers/specs/2026-08-10-per-device-login.md. Nothing here changes
login behavior: `mt_auth.py`, `auth_routes.py`, and `onboarding_routes.py`
are untouched, and `device_context.binding_enforced()` still has no caller.
This module exists purely so an admin (or a future settings screen) can SEE
what device_registry.py/device_context.py have already been recording all
along.

Plain module-level Blueprint, no factory function -- unlike
commercial_runtime/backup/routes.py's `make_backup_blueprint(...)` or
commercial_runtime/licensing_contracts/routes.py's `make_licensing_blueprint(...)`,
this module needs no per-product config (no database_dir, no product_code):
it resolves its own connection via registry_db.get_conn(), exactly like
commercial_runtime/identity/auth_routes.py and onboarding_routes.py already
do. Both of those are the structural precedent for this file, not the
factory-style blueprints -- they live in this same package and need the same
nothing-but-a-session amount of setup.

CRITICAL (same constraint as device_context.py): this module must stay safe
to import EAGERLY on Android (Chaquopy). It imports nothing from
licensing_contracts and nothing that touches `cryptography` -- only
device_registry, device_context, mt_auth (already imported eagerly by every
product's app.py via auth_routes.py/retail_api.py today), and registry_db.
See device_context.py's module docstring for the full history of the
Android ModuleNotFoundError('cryptography') regression this constraint
guards against.
"""
from flask import Blueprint, jsonify, session

from commercial_runtime.identity import device_context, device_registry
from commercial_runtime.identity.mt_auth import mt_login_required
from commercial_runtime.identity.registry_db import get_conn

device_bp = Blueprint('devices', __name__, url_prefix='/api/devices')


def _serialize_device(device: dict) -> dict:
    """Normalizes a device_registry row dict for JSON responses -- casts
    `is_admin_device` from SQLite's stored INTEGER (0/1, see
    device_registry.apply_identity_device_schema's schema) to an actual
    bool. This matters beyond cosmetics: a future notification worker needs
    to read `is_admin_device` off GET /api/devices/me to self-gate which
    device sends scheduled reports, without re-deriving anything from raw
    SQL -- see that field's mention in
    docs/superpowers/specs/2026-08-10-per-device-login.md. A bare 0/1 int
    would still round-trip through JSON as a number, not a boolean, leaving
    room for a careless `if device["is_admin_device"]:` in a language/
    consumer where that's not automatically truthy the way Python treats it.
    Explicit bool leaves no ambiguity for that future caller.
    """
    return {**device, 'is_admin_device': bool(device['is_admin_device'])}


@device_bp.route('', methods=['GET'])
@mt_login_required
def list_devices_route():
    """GET /api/devices -- every device row known for the current session's
    company_id, in the same order device_registry.list_devices() already
    returns (first_seen_at ascending). Read-only: no query params, no
    filtering, no pagination -- Phase 1 has no product surface consuming
    this yet, so there is nothing to over-build for."""
    company_id = session.get('company_id')
    if not company_id:
        # Mirrors mt_require_subsystem's exact wording (mt_auth.py) for the
        # same "authenticated but no tenant context" edge case -- should not
        # be reachable through a real login (create_session always sets
        # company_id), but mt_login_required alone doesn't guarantee it.
        return jsonify({'error': 'Tenant context missing.'}), 403

    conn = get_conn()
    try:
        devices = device_registry.list_devices(conn, company_id)
    finally:
        conn.close()

    return jsonify({'success': True, 'devices': [_serialize_device(d) for d in devices]})


@device_bp.route('/me', methods=['GET'])
@mt_login_required
def my_device_route():
    """GET /api/devices/me -- resolves and returns THIS device's own row via
    device_context.resolve_local_device(), the same idempotent entry point
    login enforcement will eventually call. Calling it here does perform its
    normal upsert-on-resolve side effect (first_seen_at/last_seen_at
    check-in, see device_registry.upsert_local_device()) -- that is
    resolve_local_device()'s documented, intended behavior, not a mutation
    this route bolts on; there is no POST/PATCH/DELETE anywhere in this
    file, and no admin-flag/grant state is ever written here.
    """
    company_id = session.get('company_id')
    if not company_id:
        return jsonify({'error': 'Tenant context missing.'}), 403

    conn = get_conn()
    try:
        try:
            device = device_context.resolve_local_device(conn, company_id)
        except device_context.DeviceCompanyMismatchError as exc:
            # Phase 0 decision 0-a's deliberate refusal (see device_context.py
            # and the spec doc's §b) -- surface it as a diagnosable HTTP
            # error instead of an unhandled 500, since this endpoint is
            # exactly the kind of place an admin would look to understand
            # why a device isn't behaving as expected.
            return jsonify({'error': str(exc), 'code': 'DEVICE_COMPANY_MISMATCH'}), 409
        except device_context.LocalDeviceStateCorruptError as exc:
            return jsonify({'error': str(exc), 'code': 'LOCAL_DEVICE_STATE_CORRUPT'}), 500
    finally:
        conn.close()

    return jsonify({'success': True, 'device': _serialize_device(device)})
