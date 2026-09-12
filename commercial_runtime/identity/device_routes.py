"""
Aura FullSuits -- per-device login / "one Admin Device": read-only HTTP
visibility routes (Phase 1, this week).

Originally two GET-only endpoints -- Wednesday's slice of
docs/superpowers/specs/2026-08-10-per-device-login.md. Nothing here changes
login behavior: `mt_auth.py`, `auth_routes.py`, and `onboarding_routes.py`
are untouched, and `device_context.binding_enforced()` still has no caller.

2026-08-20 adds the two POSTs that make `devices.is_admin_device` a flag
something can actually SET. Until then the only code that ever wrote it was
an auto-promotion buried inside `device_context.resolve_local_device()` --
i.e. inside the read path that retail_api.py's audit-log authorization
check calls -- so asking "am I the admin device?" is what made you the
admin device (see `local_device_is_admin()`'s docstring for the whole
account). Removing that auto-promotion without giving the flag a real
setter would have swung the bug the other way: permanently 0, Settings and
the Audit Log unreachable forever on every install. Both endpoints below
are therefore deliberate, authenticated, admin-role decisions:

  POST /me/claim-admin      -- bootstrap: "no admin device exists for this
                               company yet; make it this one." Fails 409 if
                               one already exists.
  POST /<device_id>/admin   -- transfer: "move the flag to that device",
                               and only the CURRENT admin device may ask.

What this deliberately does NOT cover: recovery when the admin device is
lost/destroyed/wiped. Transfer requires the current holder to be the one
asking, which is exactly what a lost device cannot do. Recovering from that
needs an out-of-band authority (an Owner-CC-side "reset this company's
admin device") -- a real gap, called out here rather than papered over with
a self-service escape hatch that would hand any device the flag on demand
and re-open the hole this file just closed.

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
    # Should not be reachable through a real login (create_session always
    # sets company_id), but mt_login_required alone doesn't guarantee it.
    company_id, err = _tenant_context()
    if err:
        return err

    conn = get_conn()
    try:
        devices = device_registry.list_devices(conn, company_id)
    finally:
        conn.close()

    return jsonify({'success': True, 'devices': [_serialize_device(d) for d in devices]})


def _tenant_context():
    """(company_id, error_response) -- every route below needs the session's
    company_id, and all of them refuse identically without one. Mirrors
    mt_require_subsystem's exact wording (mt_auth.py) for the same
    "authenticated but no tenant context" edge case."""
    company_id = session.get('company_id')
    if not company_id:
        return None, (jsonify({'error': 'Tenant context missing.'}), 403)
    return company_id, None


def _require_admin_role():
    """The admin-device flag is a company-level authority decision, so only
    a company admin may make it -- `session['mt_role'] == 'admin'` is the
    only privilege level this codebase's session model actually
    distinguishes (the same check retail_api.py's `_require_company_admin()`
    uses to gate demo wipe/seed, and the same reasoning recorded in
    RETAIL_SECURITY_PHASE_1.md for why finer-grained RBAC is deferred).

    Deliberately NOT satisfied by a demo session: `mt_login_required` lets
    `session['is_demo_mode']` through without any user at all, which is
    right for read-only browsing of seeded data but must not be able to
    hand a device a permanent, persisted privilege in registry.db that
    outlives the demo.
    """
    if session.get('is_demo_mode') or session.get('mt_role') != 'admin':
        return jsonify({
            'error': 'Only a company administrator can change the admin device.',
            'code': 'ADMIN_ROLE_REQUIRED',
        }), 403
    return None


def _resolve_or_error(conn, company_id):
    """(device, error_response) -- resolve_local_device() with the same two
    deliberate refusals my_device_route() already surfaces as HTTP, shared
    so the claim route reports them identically instead of 500-ing."""
    try:
        return device_context.resolve_local_device(conn, company_id), None
    except device_context.DeviceCompanyMismatchError as exc:
        return None, (jsonify({'error': str(exc), 'code': 'DEVICE_COMPANY_MISMATCH'}), 409)
    except device_context.LocalDeviceStateCorruptError as exc:
        return None, (jsonify({'error': str(exc), 'code': 'LOCAL_DEVICE_STATE_CORRUPT'}), 500)


@device_bp.route('/me', methods=['GET'])
@mt_login_required
def my_device_route():
    """GET /api/devices/me -- resolves and returns THIS device's own row via
    device_context.resolve_local_device(), the same idempotent entry point
    login enforcement will eventually call. Calling it here does perform its
    normal upsert-on-resolve side effect (first_seen_at/last_seen_at
    check-in, see device_registry.upsert_local_device()) -- that is
    resolve_local_device()'s documented, intended behavior, not a mutation
    this route bolts on. No admin-flag or grant state is ever written here:
    that is what the two POSTs below are for, and keeping this GET incapable
    of it is the invariant the whole 2026-08-20 fix rests on (see the module
    docstring -- an authorization check that could promote a device is what
    opened the audit log to any device that asked).
    """
    company_id, err = _tenant_context()
    if err:
        return err

    conn = get_conn()
    try:
        # Phase 0 decision 0-a's deliberate refusal (see device_context.py
        # and the spec doc's §b) is surfaced by _resolve_or_error as a
        # diagnosable 409 instead of an unhandled 500, since this endpoint
        # is exactly the kind of place an admin would look to understand
        # why a device isn't behaving as expected.
        device, err = _resolve_or_error(conn, company_id)
        if err:
            return err
        # `can_claim_admin` is what turns "this device is not the admin
        # device" into something the UI can act on rather than just a
        # dead end. It is advisory only -- POST /me/claim-admin re-derives
        # every part of it server-side and the database has the final say
        # (see claim_admin_device's partial-unique-index argument), so a
        # client that ignores a false here gains nothing. Sitting on the
        # envelope rather than inside `device` on purpose: it is a fact
        # about this SESSION and this COMPANY (is the caller an admin, has
        # anyone claimed yet), not a column of the device row.
        can_claim = (
            not device['is_admin_device']
            and session.get('mt_role') == 'admin'
            and not session.get('is_demo_mode')
            and not device_registry.has_admin_device(conn, company_id)
        )
    finally:
        conn.close()

    return jsonify({'success': True, 'device': _serialize_device(device),
                     'can_claim_admin': bool(can_claim)})


@device_bp.route('/me/claim-admin', methods=['POST'])
@mt_login_required
def claim_admin_device_route():
    """POST /api/devices/me/claim-admin -- the fresh-install bootstrap: a
    company admin, sitting at this device, declares it the company's admin
    device. Only possible while NO device holds the flag.

    Trust-on-first-claim, bounded by role and by the database. It is the
    weakest rule that still produces a working install: on a fresh
    install/company there is no existing authority that could authorize the
    first admin device (that is what "first" means), so the strongest
    available evidence is "an authenticated company administrator is
    physically at this machine and said so". Everything after the first
    claim is strictly stronger -- a second device gets 409 and must be
    granted the flag BY the current admin device via the transfer route
    below, never by asking on its own behalf.

    409 rather than silently transferring: a claim from a device that isn't
    the holder is either a mistake or an attempt, and both deserve to be
    told no in a way that names the actual holder.
    """
    company_id, err = _tenant_context()
    if err:
        return err
    err = _require_admin_role()
    if err:
        return err

    conn = get_conn()
    try:
        # Resolving (and so upserting) here is correct where it was wrong
        # in the authorization check: this request IS the deliberate
        # write -- the user asked for a state change -- rather than a read
        # that quietly mutated on the way past.
        device, err = _resolve_or_error(conn, company_id)
        if err:
            return err
        claimed = device_registry.claim_admin_device(conn, company_id, device['id'])
        if claimed is None:
            holder = device_registry.admin_device(conn, company_id)
            return jsonify({
                'error': 'Another device is already this company\'s admin device.',
                'code': 'ADMIN_DEVICE_ALREADY_CLAIMED',
                'admin_device': _serialize_device(holder) if holder else None,
            }), 409
    except ValueError as exc:
        # claim_admin_device's "not an active device for this company"
        # refusal -- reachable if this install's device row was revoked.
        return jsonify({'error': str(exc), 'code': 'DEVICE_NOT_ELIGIBLE'}), 409
    finally:
        conn.close()

    # The row this process has cached predates the claim (is_admin_device
    # = 0); without this the very client that just succeeded would be told
    # by the next GET /me that nothing changed. See invalidate_cache().
    device_context.invalidate_cache()
    return jsonify({'success': True, 'device': _serialize_device(claimed)})


@device_bp.route('/<device_id>/admin', methods=['POST'])
@mt_login_required
def transfer_admin_device_route(device_id):
    """POST /api/devices/<device_id>/admin -- move the admin flag to another
    of this company's devices. Requires BOTH a company admin role AND that
    the request is coming FROM the device that currently holds the flag.

    The from-the-current-holder requirement is the whole security value:
    without it this route is just "any admin can take the flag from any
    device", which makes the claim route's 409 above decorative. With it,
    the flag can only ever move along a chain that started at a deliberate
    first claim -- retiring a till is a thing you do AT the old till, which
    is also the moment you actually have both machines in front of you.

    Uses set_admin_device() (clear-then-set, one transaction) rather than
    claim_admin_device(): this is precisely the transfer case where an
    existing holder is expected, and the caller has just proven it IS that
    holder.
    """
    company_id, err = _tenant_context()
    if err:
        return err
    err = _require_admin_role()
    if err:
        return err

    conn = get_conn()
    try:
        try:
            if not device_context.local_device_is_admin(conn, company_id):
                return jsonify({
                    'error': 'Only the current admin device can hand that role to another device.',
                    'code': 'NOT_ADMIN_DEVICE',
                }), 403
        except device_context.LocalDeviceStateCorruptError as exc:
            return jsonify({'error': str(exc), 'code': 'LOCAL_DEVICE_STATE_CORRUPT'}), 500

        target = device_registry.get_device(conn, device_id)
        if not target or target['company_id'] != company_id:
            # Same 404 for "no such device" and "device of another company":
            # a company must not be able to probe another tenant's device
            # ids by watching this route's status code change.
            return jsonify({'error': 'Device not found.', 'code': 'DEVICE_NOT_FOUND'}), 404
        if (target['status'] or '').lower() != 'active':
            return jsonify({'error': 'That device has been revoked.',
                             'code': 'DEVICE_REVOKED'}), 409
        updated = device_registry.set_admin_device(conn, company_id, device_id)
    finally:
        conn.close()

    device_context.invalidate_cache()
    return jsonify({'success': True, 'device': _serialize_device(updated)})
