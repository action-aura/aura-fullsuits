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
from flask import Blueprint, jsonify, request, session

from commercial_runtime.identity import device_context, device_registry
from commercial_runtime.identity.mt_auth import mt_login_required
from commercial_runtime.identity.registry_db import get_conn

device_bp = Blueprint('devices', __name__, url_prefix='/api/devices')

# P1a FINAL polish F6: GET /me also names the BRANCH this computer stands in,
# so the app header can read "Branch . This computer". That name lives in
# retail.db's `branches` table -- a different product's database. This module
# is shared with Clinic and imported eagerly on Android, and
# branch_scope_schema.py records the same rule for the same reason: identity
# stays product-agnostic and never reaches into retail.db. So the lookup is
# INJECTED: the product that owns branches registers a resolver
# `fn(company_id) -> str | None`; a product that has none (Clinic) leaves it
# unset and `branch_name` is simply null. A resolver that raises is treated as
# "unknown" (null) -- a header label must never turn GET /me into a 500, the
# route the whole admin-device flow hangs off.
_branch_name_resolver = None


def register_branch_name_resolver(fn):
    """Install (or, with None, clear) the product's `fn(company_id)` that
    returns this device's branch display name, or None."""
    global _branch_name_resolver
    _branch_name_resolver = fn


def _resolve_branch_name(company_id):
    fn = _branch_name_resolver
    if fn is None:
        return None
    try:
        name = fn(company_id)
    except Exception:
        return None
    name = (name or '').strip() if isinstance(name, str) else None
    return name or None


# Registry v9 refusal strings. FIXED LITERALS on purpose: the frontend's t()
# looks a whole sentence up in the locale files, so a sentence assembled from
# parts would never be translated. New ones are listed in tmp/p2s/i18n-R.json.
JOINED_DEVICE_MESSAGE = (
    'This computer joined an existing shop, so it cannot become the main '
    'computer by itself. Ask the main computer to hand the role over.'
)
MAIN_MUST_BE_WINDOWS_MESSAGE = (
    'Only a Windows computer can be the main computer, because this shop has one. '
    'A phone can be the main computer only in a shop with no Windows computer.'
)
NOT_MAIN_FOR_DEVICES_MESSAGE = 'Only the main computer can rename or remove other computers.'
CANNOT_REVOKE_MAIN_MESSAGE = (
    'The main computer cannot be removed. Move the main role to another computer first.'
)
DEVICE_CLASH_MESSAGE = (
    'That computer shares an identity with another one, so it cannot be made the main computer.'
)
DEVICE_NAME_TOO_LONG_MESSAGE = 'Name must be 60 characters or fewer.'
DEVICE_NAME_REQUIRED_MESSAGE = 'Enter a name for this computer.'
DEVICE_NOT_REMOVED_MESSAGE = 'That computer is not removed, so there is nothing to bring back.'
# The registry helpers refuse with a machine-readable ValueError text; the
# person sees this sentence and the raw text rides along as `reason`.
DEVICE_NOT_ELIGIBLE_MESSAGE = (
    'That computer cannot be the main computer. It was removed from the shop, '
    'or it is not a Windows computer.'
)
# Fields a non-admin never sees in GET /api/devices (L2).
NON_ADMIN_HIDDEN_DEVICE_FIELDS = ('device_fingerprint', 'owner_installation_id')


def _platform_can_be_main(platform) -> bool:
    """Only a Windows computer may be main. A device that has not reported its
    platform yet (a row from before v9, never announced) is NOT refused on
    that ground: refusing an unknown platform would lock out every upgrade
    until the boot announcement runs, and an unknown platform here is a
    Windows desktop in practice (Android has always announced 'ANDROID')."""
    return not platform or str(platform).upper() == device_registry.WINDOWS_PLATFORM


def _can_be_main(conn, company_id, platform) -> bool:
    """`_platform_can_be_main` plus the phone-only allowance (owner decision
    2026-10-31): a phone MAY be the main computer in a shop that has no
    Windows computer registered. The registry helpers enforce the same rule
    (device_registry._refuse_ineligible_main), so this is only the friendlier
    first answer in front of them."""
    if _platform_can_be_main(platform):
        return True
    return not device_registry.windows_device_registered(conn, company_id)


def _session_user_uid(conn):
    """The signed-in user's synced `uid` (what other devices know them by),
    or None. `mt_user_id` is the local row id and means nothing to a peer."""
    user_id = session.get('mt_user_id')
    if not user_id:
        return None
    try:
        row = conn.execute("SELECT uid FROM users WHERE id=?", (user_id,)).fetchone()
    except Exception:
        return None
    return row['uid'] if row and row['uid'] else None


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
        blockers = device_registry.registry_rollout_blockers(conn, company_id)
    finally:
        conn.close()

    # v9: peers arrive by sync, so the list is now "every computer in this
    # shop", and the page needs to say which row is THIS one. peek (never
    # creates identity) -- a corrupt identity file just means "unknown".
    try:
        this_id = device_context.peek_local_device_uuid()
    except device_context.LocalDeviceStateCorruptError:
        this_id = None
    # L2 (2026-09-29 review): the hardware fingerprint and the install id are
    # what makes one computer "the same" computer to the sync engine. They are
    # an admin's business; a cashier's device list needs a name, a platform and
    # a status. `mt_role == 'admin'` (and not a demo session) is the same test
    # _require_admin_role applies.
    is_admin = session.get('mt_role') == 'admin' and not session.get('is_demo_mode')
    out = []
    for d in devices:
        s = _serialize_device(d)
        if not is_admin:
            for hidden in NON_ADMIN_HIDDEN_DEVICE_FIELDS:
                s.pop(hidden, None)
        s['is_this_device'] = bool(this_id) and d['id'] == this_id
        out.append(s)

    # Rollout advisory, NOT enforcement: preset cards and role edits should be
    # disabled while any active computer reports registry version < 9 (an old
    # build stores but never reads the books grants). The server does not
    # refuse the edit itself yet -- until the boot announcement is wired into
    # app.py, every existing device would read as "old" and the owner could
    # not edit roles at all. The UI reads this block.
    return jsonify({
        'success': True, 'devices': out,
        'role_editing': {
            'allowed': not blockers,
            'min_registry_schema_version': device_registry.REGISTRY_ROLLOUT_MIN_VERSION,
            'blocking_devices': [
                {'id': b['id'], 'device_label': b['device_label'], 'platform': b['platform'],
                 'registry_schema_version': b['registry_schema_version']}
                for b in blockers
            ],
        },
    })


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

    # F6, additive only: two display fields beside the existing row. Nothing
    # existing is renamed or removed, and neither is written anywhere.
    #   device_name -- the operator-given name (`devices.device_label`);
    #                  null when nobody has named this till.
    #   branch_name -- this device's branch for THIS company, via the
    #                  product-registered resolver (see top of file).
    serialized = _serialize_device(device)
    # L2 (fix round 2): /me used to hand the fingerprint and install id to ANY
    # signed-in user; the list route already hid them from non-admins.
    if not (session.get('mt_role') == 'admin' and not session.get('is_demo_mode')):
        for hidden in NON_ADMIN_HIDDEN_DEVICE_FIELDS:
            serialized.pop(hidden, None)
    label = serialized.get('device_label')
    serialized['device_name'] = (label.strip() or None) if isinstance(label, str) else None
    serialized['branch_name'] = _resolve_branch_name(company_id)
    return jsonify({'success': True, 'device': serialized,
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
        # v9: a joined device (one that entered an existing shop) never
        # bootstraps itself into the main computer, and only a Windows
        # computer can be main. Both are refusals of a decision the user
        # asked for, so they name the reason in plain words.
        # A shop that already has a main computer that is not this one answers
        # first with the holder's name (actionable), not with the generic
        # "you joined" message: both are true, only one tells the owner where
        # to go.
        holder = device_registry.admin_device(conn, company_id)
        if holder and holder.get('id') != device['id']:
            return jsonify({
                'error': 'Another device is already this company\'s admin device.',
                'code': 'ADMIN_DEVICE_ALREADY_CLAIMED',
                'admin_device': _serialize_device(holder),
            }), 409
        if device_registry.is_joined_device(conn, company_id, device['id']):
            return jsonify({'error': JOINED_DEVICE_MESSAGE,
                            'code': 'JOINED_DEVICE_CANNOT_CLAIM'}), 409
        if not _can_be_main(conn, company_id, device.get('platform')):
            return jsonify({'error': MAIN_MUST_BE_WINDOWS_MESSAGE,
                            'code': 'MAIN_MUST_BE_WINDOWS'}), 409
        claimed = device_registry.claim_admin_device_with_assignment(
            conn, company_id, device['id'], by_user_uid=_session_user_uid(conn))
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
        return jsonify({'error': DEVICE_NOT_ELIGIBLE_MESSAGE, 'code': 'DEVICE_NOT_ELIGIBLE',
                        'reason': str(exc)}), 409
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
        # v9: the new main computer must be a Windows computer and must not be
        # one the sync engine has flagged as sharing an identity with another.
        if not _can_be_main(conn, company_id, target.get('platform')):
            return jsonify({'error': MAIN_MUST_BE_WINDOWS_MESSAGE,
                            'code': 'MAIN_MUST_BE_WINDOWS'}), 409
        if device_registry.device_has_open_clash(conn, device_id):
            return jsonify({'error': DEVICE_CLASH_MESSAGE,
                            'code': 'DEVICE_IDENTITY_CLASH'}), 409
        # Clear-then-set + epoch+1 + `admin_device` event, one transaction.
        # The helper now enforces "not revoked / Windows if known / epoch in
        # range" itself (item 18), so the checks above are a friendlier first
        # answer and this catch is the second line: a ValueError is a refusal
        # (409), never a 500.
        try:
            updated = device_registry.transfer_admin_device_with_assignment(
                conn, company_id, device_id,
                by_user_uid=_session_user_uid(conn),
                by_device_id=device_context.peek_local_device_uuid())
        except ValueError as exc:
            conn.rollback()
            return jsonify({'error': DEVICE_NOT_ELIGIBLE_MESSAGE, 'code': 'DEVICE_NOT_ELIGIBLE',
                            'reason': str(exc)}), 409
    finally:
        conn.close()

    device_context.invalidate_cache()
    return jsonify({'success': True, 'device': _serialize_device(updated)})


def _require_main_device(conn, company_id):
    """(None) when this request comes from the main computer, else an error
    response. Rename/remove are decisions about OTHER computers; like the
    transfer route they are only allowed to be made at the main one."""
    try:
        if not device_context.local_device_is_admin(conn, company_id):
            return jsonify({'error': NOT_MAIN_FOR_DEVICES_MESSAGE,
                            'code': 'NOT_ADMIN_DEVICE'}), 403
    except device_context.LocalDeviceStateCorruptError as exc:
        return jsonify({'error': str(exc), 'code': 'LOCAL_DEVICE_STATE_CORRUPT'}), 500
    return None


@device_bp.route('/<device_id>/label', methods=['POST'])
@mt_login_required
def label_device_route(device_id):
    """POST /api/devices/<device_id>/label {"label": "..."} -- rename a
    computer. Admin role, from the main computer. 1-60 characters after
    trimming. row_version+1 and a `device` event, so the new name reaches
    every peer."""
    company_id, err = _tenant_context()
    if err:
        return err
    err = _require_admin_role()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    raw_label = body.get('label')
    if isinstance(raw_label, str) and len(raw_label.strip()) > device_registry.DEVICE_LABEL_MAX * 4:
        # Refuse absurd input before spending any work on it.
        return jsonify({'error': DEVICE_NAME_TOO_LONG_MESSAGE, 'code': 'DEVICE_NAME_TOO_LONG'}), 400
    try:
        # L3: the same rule the apply side uses (Cc/Cf stripped, line breaks
        # refused), so a name typed here and a name received over sync can
        # never disagree about what is allowed.
        label = device_registry.clean_device_label(raw_label) if isinstance(raw_label, str) else None
    except ValueError as exc:
        if 'characters or fewer' in str(exc):
            return jsonify({'error': DEVICE_NAME_TOO_LONG_MESSAGE, 'code': 'DEVICE_NAME_TOO_LONG'}), 400
        return jsonify({'error': str(exc), 'code': 'DEVICE_NAME_INVALID'}), 400
    if not label:
        return jsonify({'error': DEVICE_NAME_REQUIRED_MESSAGE, 'code': 'DEVICE_NAME_REQUIRED'}), 400

    conn = get_conn()
    try:
        err = _require_main_device(conn, company_id)
        if err:
            return err
        updated = device_registry.set_device_label_synced(conn, company_id, device_id, label)
        if updated is None:
            return jsonify({'error': 'Device not found.', 'code': 'DEVICE_NOT_FOUND'}), 404
    finally:
        conn.close()
    device_context.invalidate_cache()
    return jsonify({'success': True, 'device': _serialize_device(updated)})


@device_bp.route('/<device_id>/revoke', methods=['POST'])
@mt_login_required
def revoke_device_route(device_id):
    """POST /api/devices/<device_id>/revoke -- remove a computer from the
    shop (status 'revoked'; the row is never deleted, so its history and
    sales keep their meaning). Admin role, from the main computer. The main
    computer itself cannot be revoked: hand the role over first."""
    company_id, err = _tenant_context()
    if err:
        return err
    err = _require_admin_role()
    if err:
        return err

    conn = get_conn()
    try:
        err = _require_main_device(conn, company_id)
        if err:
            return err
        try:
            updated = device_registry.revoke_device_synced(conn, company_id, device_id)
        except ValueError:
            return jsonify({'error': CANNOT_REVOKE_MAIN_MESSAGE,
                            'code': 'CANNOT_REVOKE_MAIN'}), 409
        if updated is None:
            return jsonify({'error': 'Device not found.', 'code': 'DEVICE_NOT_FOUND'}), 404
    finally:
        conn.close()
    device_context.invalidate_cache()
    return jsonify({'success': True, 'device': _serialize_device(updated)})


@device_bp.route('/<device_id>/reinstate', methods=['POST'])
@mt_login_required
def reinstate_device_route(device_id):
    """POST /api/devices/<device_id>/reinstate -- bring a removed computer
    back (status 'revoked' -> 'active'). Admin role, from the main computer
    only, and only for a computer that IS revoked.

    2026-09-29 review (H2d): this is the ONE way out of 'revoked'. The sync
    apply side refuses revoked -> active from any ordinary `device` event and
    accepts it only when the event carries `reinstated_by_device_id` equal to
    the main computer that receiver already knows -- so a forged "active"
    event cannot quietly resurrect a computer the owner removed."""
    company_id, err = _tenant_context()
    if err:
        return err
    err = _require_admin_role()
    if err:
        return err

    conn = get_conn()
    try:
        err = _require_main_device(conn, company_id)
        if err:
            return err
        try:
            updated = device_registry.reinstate_device_synced(
                conn, company_id, device_id,
                by_device_id=device_context.peek_local_device_uuid())
        except ValueError:
            return jsonify({'error': DEVICE_NOT_REMOVED_MESSAGE,
                            'code': 'DEVICE_NOT_REVOKED'}), 409
        if updated is None:
            return jsonify({'error': 'Device not found.', 'code': 'DEVICE_NOT_FOUND'}), 404
    finally:
        conn.close()
    device_context.invalidate_cache()
    return jsonify({'success': True, 'device': _serialize_device(updated)})


# ==== LOCAL TAKEOVER (option b) -- BEGIN ====================================
# One removable unit; see device_takeover.py's docstring for the why, the
# guards, and what to delete if the owner picks Owner-CC reset (option a)
# instead: this block, that file, its test, its i18n strings. Nothing above
# refers to anything below.
import json as _json
import logging as _logging
import uuid as _uuid

from commercial_runtime.identity import device_takeover

_takeover_log = _logging.getLogger('aura.security.identity')


@device_bp.route('/me/take-over', methods=['POST'])
@mt_login_required
def take_over_main_route():
    """POST /api/devices/me/take-over {"password": "...", "confirm": "TAKE OVER"}
    -- make THIS computer the main computer when the real one is gone.

    Order of the guards, each a hard stop and none skippable by the next:
      1. company admin (not a demo session)             403 ADMIN_ROLE_REQUIRED
      2. the typed phrase is exact                      400 TAKEOVER_CONFIRM_REQUIRED
         (before the password, so a typo never burns a login attempt)
      3. the admin's own password, counted on the
         takeover's OWN lockout (never the login's)     403 TAKEOVER_PASSWORD_INCORRECT
                                                        429 TAKEOVER_LOCKED after 3 misses
      4. this computer may be main at all (Windows, no
         identity clash)                                409
      5. device_takeover: current main silent >= 7 days,
         this computer is not already main, etc.        409 TAKEOVER_*
    Only ever THIS computer -- there is no target id to pass."""
    company_id, err = _tenant_context()
    if err:
        return err
    err = _require_admin_role()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    if not device_takeover.confirm_phrase_ok(body.get('confirm')):
        return jsonify({'error': device_takeover.TAKEOVER_CONFIRM_MESSAGE,
                        'code': 'TAKEOVER_CONFIRM_REQUIRED'}), 400

    conn = get_conn()
    try:
        device, err = _resolve_or_error(conn, company_id)
        if err:
            return err
        # M2 (2026-09-29 review): the password is checked against the SIGNED-IN
        # user's own hash (so another admin's password typed here is not
        # consent from this session) with the takeover's OWN counter -- 3 free
        # misses, then a lock that doubles from 15 minutes. It deliberately no
        # longer goes through authenticate_registry_user: that bumped the
        # login screen's failed_login_count, so anyone at an unlocked till
        # could lock the owner out of signing in by guessing here.
        user_id = session.get('mt_user_id')
        locked, retry_after = device_takeover.takeover_lock_state(conn, user_id, device['id'])
        if locked:
            return jsonify({'error': device_takeover.TAKEOVER_LOCKED_MESSAGE,
                            'code': 'TAKEOVER_LOCKED',
                            'retry_after_seconds': retry_after}), 429
        if not device_takeover.password_matches(conn, user_id, body.get('password')):
            lock_seconds = device_takeover.record_takeover_failure(conn, user_id, device['id'])
            if lock_seconds:
                return jsonify({'error': device_takeover.TAKEOVER_LOCKED_MESSAGE,
                                'code': 'TAKEOVER_LOCKED',
                                'retry_after_seconds': lock_seconds}), 429
            return jsonify({'error': device_takeover.TAKEOVER_PASSWORD_MESSAGE,
                            'code': 'TAKEOVER_PASSWORD_INCORRECT'}), 403
        device_takeover.clear_takeover_failures(conn, user_id, device['id'])

        if not _can_be_main(conn, company_id, device.get('platform')):
            return jsonify({'error': MAIN_MUST_BE_WINDOWS_MESSAGE,
                            'code': 'MAIN_MUST_BE_WINDOWS'}), 409
        if device_registry.device_has_open_clash(conn, device['id']):
            return jsonify({'error': DEVICE_CLASH_MESSAGE,
                            'code': 'DEVICE_IDENTITY_CLASH'}), 409
        before = device_registry.get_assignment(conn, company_id)
        try:
            updated = device_takeover.take_over_main(
                conn, company_id, device['id'], by_user_uid=_session_user_uid(conn))
        except device_takeover.TakeoverRefused as exc:
            return jsonify({'error': exc.message, 'code': exc.code}), 409
        # Best effort, like every other privilege-change record in this
        # package (a full audit table must not undo a completed handover),
        # but never silent: a lost row is logged with its traceback.
        try:
            conn.execute(
                "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, "
                "entity_id, new_value_json) VALUES (?,?,?,?,?,?,?)",
                (str(_uuid.uuid4()), company_id, session.get('mt_user_id'),
                 'TAKEOVER_MAIN_DEVICE', 'DEVICE', device['id'],
                 _json.dumps({'from_device_id': before['device_id'] if before else None,
                              'to_device_id': device['id']})),
            )
            conn.commit()
        except Exception:
            _takeover_log.exception('TAKEOVER_MAIN_DEVICE audit row was not written')
    finally:
        conn.close()

    device_context.invalidate_cache()
    return jsonify({'success': True, 'device': _serialize_device(updated)})
# ==== LOCAL TAKEOVER (option b) -- END ======================================
