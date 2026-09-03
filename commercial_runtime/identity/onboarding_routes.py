"""
Aura FullSuits -- first-run onboarding + admin employee management.

Extracted from Action Aura Enterprise's api/standalone_auth.py (698 lines),
whose own docstring identifies it as "what the shipped Retail/Clinic
standalone products actually run." Only the onboarding-wizard and
employee-management surface is kept -- the legacy per-subsystem
`access_level` permission model plus `clinic_role` (doctor/secretary/none)
overlay. The newer "named permission grants" RBAC system
(`core/rbac/engine.py`) is NOT ported: it is wrapped in try/except in the
source itself (graceful degradation when unavailable) and Phase 0/3
discovery confirmed it is not wired into any actual Clinic enforcement path
(only the binary `require_clinic_role('doctor')` gate is real) -- see
docs/migration/clinic-source-inventory.md.

Shared by Retail and Clinic. There must be no hardcoded admin account, no
demo backdoor, and no seeded user anywhere in this file -- the ONLY way an
account is created is `create_admin` (gated on "no valid admin exists yet")
or `employee_setup` (gated on a time-limited, single-use invite token).
"""
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, request, jsonify, session

from commercial_runtime.identity.registry_db import get_conn
from commercial_runtime.identity.mt_auth import create_session, mt_login_required
from commercial_runtime.identity import user_accounts as _accounts
from commercial_runtime.identity import verification as _verification
# Launch-readiness account-hierarchy design §2.4/§4.2 D1 -- the install-
# stable device UUID `EMP-<dev4>-NNNN` derives its fragment from. Imported
# at module scope (unlike mt_auth.py's deferred `user_accounts` import):
# device_context has no import chain of its own worth deferring, and this
# file already imports several other leaf modules the same way.
from commercial_runtime.identity.device_context import peek_local_device_uuid
from commercial_runtime.security.passwords import hash_password
from commercial_runtime.security.audit import record as _security_audit, ADMIN_CREATED
from commercial_runtime.licensing_contracts.flask_guard import make_capability_guard

onboarding_bp = Blueprint('onboarding', __name__)

#: Deliberately the SAME logger mt_auth.py uses (mt_auth.py:31), not a new
#: per-module one: a failure in here that silently downgrades what a client is
#: allowed to render is an identity-layer security event, and an operator
#: grepping their logs for one should find both halves under one name.
log = logging.getLogger('aura.security.identity')

# ── Licence gate (AUDIT: account administration had none) ──────────────────
#
# `/api/admin/employees` and its siblings below were gated on
# `@mt_login_required` ONLY -- no licence check anywhere in this file. Every
# OTHER mutation surface in both products refuses a non-ACTIVE licence
# (retail_api.py's `require_license_capability(..., restricted_mode_
# allowlist=RETAIL_RESTRICTED_ALLOWLIST)`, clinic_api.py's identical
# pattern with CLINIC_RESTRICTED_ALLOWLIST) -- an expired/suspended/revoked/
# never-activated install could still mint staff accounts, change roles,
# re-scope branches and grant permissions. This block closes that gap for
# every mutating route in the "Admin: employee management" section below,
# plus the company-settings mutation branch further down.
#
# `make_capability_guard(app_data_dir)` is normally called once per PRODUCT,
# at that product's OWN route-module import time, closed over a path built
# from that product's OWN `config.py::DATABASE_DIR` (see retail_api.py /
# clinic_api.py). This module has no such product-owned constant to read --
# it is imported by BOTH apps' app.py (confirmed by grep: Clinic's
# `products/clinic/backend/app.py` genuinely registers `onboarding_bp`,
# exactly like Retail's does) -- so it resolves `app_data_dir` the same way
# `registry_db.py` and `mt_auth.py`, its two siblings in this exact package,
# already do for their own database paths: `AURA_APP_DATA`, falling back to
# this package's own parent directory. `licensing.db` and `registry.db`
# already share one `AURA_APP_DATA/database/` tree (see
# company_rebind.py's `_licensing_db_path` docstring), so this resolves to
# the SAME per-install licensing database every other guarded route in
# whichever product imported this module reads -- never a retail- or
# clinic-specific path, and no import from either product.
_APP_DATA_FOR_LICENSING = (
    os.environ.get('AURA_APP_DATA') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
require_license_capability = make_capability_guard(_APP_DATA_FOR_LICENSING)

# Empty on purpose. RETAIL_RESTRICTED_ALLOWLIST/CLINIC_RESTRICTED_ALLOWLIST
# each carve out a handful of capabilities that stay reachable in a
# restricted-but-data-preserved licence state (returns, debt payments, an
# already-open clinical encounter) because refusing them is its own harm.
# Nothing administered by this file is that kind of action: minting a staff
# account, moving one between roles, disabling/enabling one, resetting a
# PIN, granting or revoking a capability, and editing company settings are
# all administrative, not continuity-of-care/business-continuity -- so every
# route this guards requires a fully ACTIVE licence, with nothing exempted.
_ACCOUNT_ADMIN_RESTRICTED_ALLOWLIST = frozenset()

# A NEW, product-neutral "identity.*" namespace, not "retail.*"/"clinic.*".
# This module cannot import either product's own capability vocabulary
# (retail-restriction-capability-matrix.md / clinic-restriction-capability-
# matrix.md) without importing something product-specific into
# commercial_runtime, which the "product-agnostic" rule this package already
# follows elsewhere (see get_employees'/update_branch_scope's own docstrings
# on why `branches` is never looked up here) forbids. These two codes are
# evaluated ONLY against `_ACCOUNT_ADMIN_RESTRICTED_ALLOWLIST` above, in
# THIS module -- they are not, and do not need to be, registered in either
# product's own restriction-matrix doc, since neither product owns these
# routes. Two codes (not one, and not one per route): granular enough that a
# CAPABILITY_DENIED audit event tells an operator "employee administration"
# vs. "company settings" apart, without a separate code per HTTP verb the
# way retail_api.py's finer product-specific vocabulary does -- every route
# under each code makes the identical allow/deny decision, so a per-route
# split would add names without adding a distinction.
_CAP_IDENTITY_EMPLOYEE_MANAGE = "identity.employee.manage"
_CAP_IDENTITY_COMPANY_SETTINGS_MANAGE = "identity.company.settings.manage"


# ── Config helpers ─────────────────────────────────────────────────────────────

def _config_path():
    base = os.environ.get('AURA_APP_DATA') or os.getcwd()
    return os.path.join(base, 'config.json')


def _read_config():
    p = _config_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _write_config(data: dict):
    p = _config_path()
    existing = _read_config()
    existing.update(data)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(existing, f, indent=4)


def get_device_branch_uid():
    """Which branch THIS device is pinned to -- launch-readiness chain wave
    C1 (ROADMAP.md's 2026-08-30 "the multi-branch capture defect" entry;
    docs/launch-readiness/seats-and-chain-design.md §5.2 gap 1). Returns the
    `branches.uid` string, or `None` when unset.

    Deliberately a UID, never `branches.id`. `id` is a plain per-device
    autoincrement -- the SAME physical branch carries a DIFFERENT integer id
    on every device that has ever self-healed or re-seeded one (see
    `_default_branch`'s own docstring in retail_api.py), so pinning the
    integer would break on exactly the device this fix exists for, the
    moment that device is re-seeded or the row arrives in a different
    order. `uid` is the one identity that means the same thing everywhere.

    Deliberately `config.json` (this module's own `_read_config`), never a
    database row. `config.json` is device-local by construction -- it lives
    outside every table `sync_outbox` ever touches, so it is never synced --
    which is EXACTLY the property "which branch is this till standing in"
    needs: a DB row would converge across every device on the licence the
    moment sync ran, reproducing the exact defect this fix closes.

    Shared runtime: Clinic imports this module too but calls neither this
    function nor `set_device_branch_uid` anywhere, so a Clinic install's
    `config.json` is untouched by this pair existing -- see
    retail_device_branch_pin_test.py's
    test_config_without_branch_uid_key_round_trips_unchanged for the proof
    that a config with no `branch_uid` key, and every OTHER key in one that
    has some, is unaffected.
    """
    return _read_config().get('branch_uid') or None


def set_device_branch_uid(uid):
    """Pins (`uid` a non-empty string) or clears (`uid` `None`/`''`) this
    device's working branch -- see `get_device_branch_uid`'s docstring for
    why this is a uid, not an id, and why it lives in `config.json` rather
    than the database.

    A thin wrapper over `_write_config`, which already does a full
    read-modify-write: this call touches ONLY the `branch_uid` key, and
    every other key already in `config.json` is left exactly as
    `_write_config`'s own `existing.update(data)` already guarantees --
    nothing about this function is special-cased beyond the one key name.
    """
    _write_config({'branch_uid': (uid or None)})


# ── Onboarding ─────────────────────────────────────────────────────────────────

@onboarding_bp.route('/api/onboarding/status', methods=['GET'])
def onboarding_status():
    """Check if first-run setup is needed. The DATABASE is the single source
    of truth: if no admin user exists (or the admin has no real password),
    setup is needed -- config.json flags never override an empty DB."""
    try:
        conn = get_conn()
        admin = conn.execute(
            "SELECT id, password_hash FROM users WHERE role='admin' LIMIT 1"
        ).fetchone()
        conn.close()

        if not admin:
            return jsonify({'needs_setup': True})
        if not admin['password_hash'] or admin['password_hash'] in ('PENDING', '', 'null', 'NULL'):
            return jsonify({'needs_setup': True})
        return jsonify({'needs_setup': False})
    except Exception as e:
        print(f"[onboarding_status] DB error: {e}")
        return jsonify({'needs_setup': True})  # fail-safe: show setup


@onboarding_bp.route('/api/onboarding/create-admin', methods=['POST'])
def create_admin():
    """Create the first administrator account. Allowed whenever no valid
    admin exists in the DB. This is the ONLY account-creation path that
    doesn't require an existing admin session -- deliberately gated on
    "no valid admin exists yet" rather than any hardcoded credential."""
    cfg = _read_config()
    data = request.get_json() or {}
    name     = data.get('name', '').strip()
    email    = data.get('email', '').strip().lower()
    password = data.get('password', '').strip()
    company  = data.get('company_name', '').strip() or cfg.get('company_id', 'Enterprise')
    country       = data.get('country', 'US').strip()
    # `timezone_name`, NOT `timezone`: this is the customer's IANA zone string
    # ('Asia/Amman'), and the bare name is taken at module scope by
    # `datetime.timezone` -- which this file's only timestamp idiom,
    # `datetime.now(timezone.utc)`, depends on (see create_employee). Binding
    # the string to `timezone` shadowed that class for this whole function, so
    # adding the house idiom here -- copied verbatim from the two existing call
    # sites, and looking entirely correct -- raised
    # `AttributeError: 'str' object has no attribute 'utc'`, which the broad
    # `except Exception as e` below turns into a 500. This is the one route
    # that runs with no admin session, so that lands on first-run onboarding.
    # Pinned by tests/test_onboarding_timezone_symbol_is_not_shadowed.py.
    timezone_name = data.get('timezone', 'UTC').strip()
    currency      = data.get('currency', 'USD').strip()
    business_type = data.get('business_type', '').strip()
    language      = data.get('language', 'en').strip()

    if not email or not password:
        return jsonify({'error': 'Email and password are required.'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters.'}), 400

    conn = get_conn()
    try:
        # BEGIN IMMEDIATE takes the write lock up front (rather than on the
        # first write, as a bare BEGIN would) so two concurrent onboarding
        # requests can't both observe "no admin exists yet" and both proceed
        # -- the second one blocks here until the first commits or rolls
        # back, then re-reads a world where an admin now exists.
        conn.execute("BEGIN IMMEDIATE")

        existing_admin = conn.execute(
            "SELECT id, password_hash FROM users WHERE role='admin' LIMIT 1"
        ).fetchone()
        if existing_admin and existing_admin['password_hash'] not in ('', 'PENDING', None, 'null', 'NULL'):
            conn.rollback()
            return jsonify({'error': 'An admin account already exists. Please login.'}), 409

        # Reject a genuine email collision with an unrelated account instead
        # of silently deleting it -- only the pending-admin row being
        # replaced (if any) may share this email.
        email_owner = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if email_owner and (not existing_admin or email_owner['id'] != existing_admin['id']):
            conn.rollback()
            return jsonify({'error': 'This email is already registered to another account.'}), 400

        if existing_admin:
            # The pending admin's capability/permission grants go with it.
            # There is no FK from user_permissions to users (see
            # device_registry.py's schema comment for why), so without this
            # the rows would survive as orphans keyed to a user id that no
            # longer exists -- harmless to read, but they accumulate on every
            # re-onboard and they make the permission table lie about who
            # exists.
            #
            # Phase 5 wave B2 stage 3 (Decision 4): if this pending admin's
            # grants ever reached another device (its own `create` user
            # event queued the moment it was first onboarded, immediately
            # below in this same function), that peer must also learn they
            # are gone -- a delete that does not travel leaves a permission
            # GRANTED there forever. Read BEFORE deleting (there is no fixed
            # code list to emit against here, unlike update_role's own
            # CAPABILITY_CODES -- this DELETE has no subsystem filter at
            # all), and queued BEFORE `DELETE FROM users` below:
            # `_queue_user_permission_sync_event` reads the owning user's
            # `uid` from the `users` table itself, which must still exist
            # at the moment each call runs.
            _old_admin_perm_subsystems = [
                r['subsystem'] for r in conn.execute(
                    "SELECT subsystem FROM user_permissions WHERE user_id=?",
                    (existing_admin['id'],),
                ).fetchall()
            ]
            conn.execute("DELETE FROM user_permissions WHERE user_id=?", (existing_admin['id'],))
            for _sub in _old_admin_perm_subsystems:
                _accounts._queue_user_permission_sync_event(conn, existing_admin['id'], _sub, 'delete')
            conn.execute("DELETE FROM users WHERE role='admin'")

        company_id = cfg.get('company_id') or hashlib.md5(email.encode()).hexdigest()
        user_id    = str(uuid.uuid4())
        pwd_hash   = hash_password(password)

        # `uid` is the WIRE identity (registry v3, design §6) -- the id above
        # stays a private local detail. Set at creation rather than left for
        # the migration to backfill, because the migration only ever runs
        # once and every account minted afterwards would otherwise have no
        # identity a second device could name it by.
        conn.execute("""
            INSERT INTO users
              (id, company_id, employee_id, email, password_hash, role, status, require_password_change,
               uid, row_version, updated_at_utc)
            VALUES (?, ?, 'ADMIN-0001', ?, ?, 'admin', 'active', 0, ?, 1, ?)
        """, (user_id, company_id, email, pwd_hash, str(uuid.uuid4()), _accounts.now_utc_iso()))

        # Phase 5 wave B2 stage 2b: the owner account itself is a `user`
        # like any other -- a second device onboarded later (or a restore)
        # needs to learn it exists. `row_version` is already 1 from the
        # INSERT above, so this is a `create` event, not an `update`.
        _accounts._queue_user_sync_event(conn, user_id, 'create')

        # Admin still bypasses the permission lookup at mt_auth.py:287, so
        # these rows change no decision today; they exist so the owner's own
        # account shows up in the capability grid the employee screens read,
        # instead of appearing as an account with no permissions at all.
        # `emit_sync=True` (Phase 5 wave B2 stage 3): a second device
        # onboarded later, or a restore, needs the owner's own grants too --
        # not just the `users` row `_queue_user_sync_event` above already
        # sends.
        _accounts.seed_capabilities_for_user(conn, user_id, _accounts.ROLE_ADMIN, emit_sync=True)

        # Kept inside the same transaction as the user insert -- either both
        # land or neither does, so a mid-onboarding failure can never leave
        # an admin account with no matching company_settings row.
        conn.execute("""
            INSERT OR REPLACE INTO company_settings
              (id, company_id, country, timezone, currency, business_type, language)
            VALUES (?,?,?,?,?,?,?)
        """, (str(uuid.uuid4()), company_id, country, timezone_name, currency, business_type, language))

        conn.commit()

        # config.json is not part of the DB transaction (it's a file, not a
        # SQL statement) -- written only after the DB commit succeeds, so a
        # DB-side failure never leaves a stray config.json update behind.
        # The DB remains the single source of truth either way (see
        # onboarding_status()'s own docstring).
        _write_config({
            'company_id':          company,
            'admin_email':         email,
            'admin_name':          name,
            'country':             country,
            'timezone':            timezone_name,
            'currency':            currency,
            'business_type':       business_type,
            'language':            language,
        })

        _security_audit(company_id, user_id, ADMIN_CREATED, context={'email': email, 'company': company})

        create_session({
            'id': user_id, 'company_id': company_id, 'employee_id': 'ADMIN-0001',
            'role': 'admin', 'clinic_role': '', 'session_version': 1,
            'require_password_change': False, 'email': email,
        })

        # Best-effort: a verification-email send failure (SMTP not
        # configured on this install, or a transient transport error) must
        # never fail account creation -- the account and session above are
        # already committed. `email_sent` tells the frontend whether to show
        # "check your inbox" or a quieter "verify later from Settings"
        # state; it is not a security signal (email_verified_at is).
        email_sent = _verification.send_verification_email(
            conn, company_id=company_id, user_email=email, base_url=request.host_url,
        )
        conn.commit()

        return jsonify({
            'success': True,
            'user': {'id': user_id, 'email': email, 'role': 'admin', 'company': company},
            'email_verification_sent': email_sent,
        })
    except Exception as e:
        # Safe even if the transaction already committed above (nothing left
        # to roll back in that case) -- guards the pre-commit failure path,
        # which is the one that must never leave a partial user/company row.
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


@onboarding_bp.route('/api/auth/verify-email', methods=['POST'])
def verify_email():
    data = request.json or {}
    token = (data.get('token') or '').strip()
    if not token:
        return jsonify({'error': 'Missing token.'}), 400
    conn = get_conn()
    try:
        user_id = _verification.verify_email(conn, token)
        if user_id is None:
            conn.rollback()
            return jsonify({'error': 'This verification link is invalid or has expired.'}), 400
        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()


@onboarding_bp.route('/api/auth/verify-email/resend', methods=['POST'])
def resend_verification_email():
    """Only for the currently-signed-in user, re-sending to their own
    address -- unauthenticated resend would be an open email-bombing vector
    identical to the one request_password_reset's cooldown guards against,
    but that guard only applies to password-reset links, so this path needs
    its own session gate instead."""
    if 'mt_user_id' not in session:
        return jsonify({'error': 'Sign in required.'}), 401
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT email, email_verified_at FROM users WHERE id=?", (session['mt_user_id'],)
        ).fetchone()
        if row is None:
            return jsonify({'error': 'User not found.'}), 404
        if row['email_verified_at']:
            return jsonify({'success': True, 'already_verified': True})
        sent = _verification.send_verification_email(
            conn, company_id=session['company_id'], user_email=row['email'], base_url=request.host_url,
        )
        conn.commit()
        return jsonify({'success': True, 'email_verification_sent': sent})
    finally:
        conn.close()


@onboarding_bp.route('/api/auth/forgot-password', methods=['POST'])
def forgot_password():
    """Always responds 200/success regardless of whether the email belongs
    to a real account -- see verification.request_password_reset's own
    docstring for why that guard lives there, not just here."""
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    if not email:
        return jsonify({'error': 'Email is required.'}), 400
    conn = get_conn()
    try:
        _verification.request_password_reset(conn, email=email, base_url=request.host_url)
        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()


@onboarding_bp.route('/api/auth/reset-password', methods=['POST'])
def reset_password():
    data = request.json or {}
    token = (data.get('token') or '').strip()
    password = (data.get('password') or '').strip()
    if not token or not password:
        return jsonify({'error': 'Missing data.'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters.'}), 400

    conn = get_conn()
    try:
        link = _verification.consume_link(conn, token, purpose='password_reset')
        if link is None:
            conn.rollback()
            return jsonify({'error': 'This reset link is invalid or has expired.'}), 400
        user = conn.execute("SELECT id FROM users WHERE email=?", (link['email_target'],)).fetchone()
        if user is None:
            conn.rollback()
            return jsonify({'error': 'This reset link is invalid or has expired.'}), 400
        # Bumping session_version invalidates every session issued before
        # this reset -- same pattern update_clinic_role/update_perms use for
        # any other security-relevant change to a user row.
        # row_version/updated_at_utc move with every write to a user row
        # (registry v3, design §4): `users` is a shared, admin-device
        # single-writer table, and a row that changed without moving its
        # version looks unchanged to a peer that already holds an older copy.
        conn.execute(
            "UPDATE users SET password_hash=?, session_version=session_version+1, "
            "failed_login_count=0, locked_until=NULL, "
            "row_version=COALESCE(row_version, 1)+1, updated_at_utc=? WHERE id=?",
            (hash_password(password), _accounts.now_utc_iso(), user['id']),
        )
        # Phase 5 wave B2 stage 2b: a genuinely new password must reach every
        # other device, or a till that stayed offline through this reset
        # would still accept the OLD password after reconnecting (design
        # Decision 2 -- exactly the resurrection scenario `row_version`
        # exists to prevent). `row_version` was just bumped in the SAME
        # UPDATE above, so the payload this reads back carries the NEW
        # version, not the one that was true before this statement ran.
        _accounts._queue_user_sync_event(conn, user['id'], 'update')
        try:
            conn.execute(
                "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id, new_value_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), link['company_id'], user['id'], 'PASSWORD_RESET', 'USER', user['id'], '{}'),
            )
        except Exception:
            pass
        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()


@onboarding_bp.route('/api/onboarding/complete', methods=['POST'])
@mt_login_required
def complete_onboarding():
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin session required.'}), 403
    _write_config({'setup_complete': True})
    return jsonify({'success': True})


def _capabilities_for_session(conn, user_id, role):
    """The `capabilities` list `/api/auth/session` exposes -- RENDERING
    ADVICE ONLY. The retail frontend fetches a subsystem-gated dashboard
    route unconditionally on load; a cashier's first screen after login was
    a 403 because nothing told the client what the signed-in user can
    actually reach. This list lets the frontend hide what the server would
    refuse anyway.

    Every route keeps its own server-side gate regardless of what this
    returns -- nothing may start trusting this list for authorization.

    Derived from the user's ACTUAL `user_permissions` rows with
    access_level='full', NOT from `capabilities_for_role()`: per-user
    overrides exist and are the whole point of that table, so sending the
    role default would make the UI disagree with the server the moment an
    owner turns one code off for one person.

    An admin is the one exception, and deliberately reads the ROLE rather
    than the table: `mt_require_capability`/`mt_require_subsystem` both let
    role='admin' bypass the permission table entirely everywhere they gate a
    route, so a table row edited (or missing) for the admin's own account
    changes nothing about what they can actually do -- reporting anything
    other than all eight here would make this list lie about the one role
    whose access this table was never authoritative for.

    Restricted to the eight namespaced `CAPABILITY_CODES` -- the legacy
    `subsystem='retail'`/`subsystem='clinic'` rows `mt_require_subsystem`
    still reads on ~85 routes are a different vocabulary the frontend was
    never meant to branch UI on, and must never leak into this list.
    """
    if _accounts.normalize_role(role) == _accounts.ROLE_ADMIN:
        return sorted(_accounts.CAPABILITY_CODES)
    placeholders = ','.join('?' * len(_accounts.CAPABILITY_CODES))
    rows = conn.execute(
        f"SELECT subsystem FROM user_permissions "
        f"WHERE user_id=? AND subsystem IN ({placeholders}) AND access_level=?",
        (user_id, *_accounts.CAPABILITY_CODES, _accounts.ACCESS_FULL),
    ).fetchall()
    return sorted(r['subsystem'] for r in rows)


@onboarding_bp.route('/api/auth/session', methods=['GET'])
@mt_login_required
def get_session():
    """Also the mechanism a fresh client uses to LEARN whether it is
    authenticated at all -- both the web frontend (app-shell.js init()) and
    the Android client (AppRoot.kt's phaseAfterActivationGate) call this
    unconditionally before knowing whether a session exists, and both
    already treat any non-2xx / thrown response identically to
    `authenticated: false`. Gating it with `@mt_login_required` means an
    anonymous call now gets a plain 401 instead of a 200 body carrying
    `authenticated: false` -- a real but harmless contract change for both
    callers -- and, in exchange, a DISABLED or REVOKED account's browser
    stops being told it is still authenticated: previously this route read
    every field straight from the cookie with no database re-check at all,
    so a disabled admin's own tab would keep rendering as logged in even
    though every real admin route now refuses it (see
    test_admin_routes_require_login.py). Computing `capabilities` below also
    needs a validated, live user row to read `user_permissions` against, which
    this decorator now guarantees before the handler body ever runs.

    `capabilities` is TRI-STATE and every consumer depends on that: a list of
    codes, `[]` for "computed, holds nothing", and JSON `null` for "could not
    compute". See the block comment on its initialisation below -- collapsing
    the last two is what shipped an owner-locked-out-of-their-own-shop bug.
    Covered by
    commercial_runtime/identity/tests/test_session_capabilities_unknown_vs_denied.py.
    """
    if 'mt_user_id' in session:
        lang = 'en'
        # `None`, NOT `[]`. These are two different answers and conflating them
        # locked an owner out of their own shop.
        #
        # `[]` is a REAL, legitimate result: "computed successfully, and this
        # user holds nothing." `None` means "could not compute." Initialising
        # to `[]` and swallowing every failure below in a bare `except` made a
        # locked database, a half-migrated registry missing `user_permissions`,
        # or a connection that could not be opened at all return HTTP 200
        # carrying the single most restrictive answer this route can give.
        #
        # That was inert for exactly as long as nothing read the value. It
        # stopped being inert once app-shell.js's `_adoptSessionCapabilities`
        # was fixed to read this key: `hasCapability()` fails OPEN on a
        # non-array and CLOSED on a list, so a transient registry error handed
        # an admin the cashier UI -- no Reports nav entry, no Audit Log, the
        # cashier refusal copy, the cashier landing panel -- while
        # `mt_require_capability` kept serving those very routes 200 to that
        # same admin, because it bypasses on the session role and never reads
        # this table. Silent, transient, and indistinguishable in review from
        # a correctly denied account.
        #
        # Emitted as JSON `null` rather than omitted so the response SHAPE is
        # stable for every caller; both clients already read it that way and
        # treat it as "unknown -> render as before":
        #   - app-shell.js `_adoptSessionCapabilities` tests `Array.isArray`,
        #     not truthiness, precisely so `[]` and "nothing told us" cannot
        #     collapse into each other.
        #   - android/.../net/Models.kt declares `capabilities: List<String>?`
        #     and its comment names `"capabilities": null` as the value that
        #     type exists for.
        capabilities = None
        # ACCEPTED TRADE, recorded deliberately -- do not "fix" this without
        # reading what it reopens. `null` here makes both clients fail OPEN:
        # a non-admin whose capability read failed sees the Reports and
        # Employees nav and does not get the cashier landing panel, i.e. a
        # cashier can transiently be shown UI they are not entitled to.
        #
        # That is the lesser of the two harms and it was chosen knowingly:
        #   - Nothing is actually granted. Every one of those routes still
        #     runs its own server-side gate and still refuses the request --
        #     this value is rendering advice, never authorisation (see
        #     _capabilities_for_session's docstring). The worst outcome is a
        #     nav entry that 403s.
        #   - Failing CLOSED on unknown means a transient registry hiccup
        #     locks an owner out of their own shop. This programme has already
        #     shipped that exact bug once; see this route's docstring and
        #     tests/test_session_capabilities_unknown_vs_denied.py.
        # Fail-open on UNKNOWN costs a wrong-looking menu; fail-closed on
        # UNKNOWN costs the customer their business for the duration.
        #
        # NOTE: `[]` still fails CLOSED, and must -- that is a real, computed
        # answer. Only "we could not compute" fails open.
        conn = None
        try:
            conn = get_conn()
            row = conn.execute(
                "SELECT language FROM users WHERE id=?", (session['mt_user_id'],)
            ).fetchone()
            if row and row['language']:
                lang = row['language']
            capabilities = _capabilities_for_session(conn, session['mt_user_id'], session.get('mt_role'))
        except Exception:
            # Logged, never swallowed. The failure downgrades what the client
            # renders, so it has to leave a trace -- `except Exception: pass`
            # is why the version of this bug that shipped was invisible. Still
            # non-fatal: a session route that 500s logs everyone out of a
            # working shop, which is strictly worse than one that answers
            # "authenticated, capabilities unknown".
            #
            # `exc_info=True` is not decoration and is pinned by
            # test_the_failure_log_carries_the_traceback: without it the
            # operator gets this sentence and nothing else -- no exception
            # type, no file, no line, no cause. For the one failure whose
            # entire purpose is to be debuggable after the fact, the traceback
            # is most of the value; the sentence alone only restates what they
            # already knew from the missing Reports tab.
            log.warning(
                "session: could not compute capabilities for user %s (role=%r); "
                "reporting them as UNKNOWN (null) rather than as an empty grant "
                "list, so the client falls back to rendering unrestricted "
                "instead of showing this user the most locked-down UI there is",
                session.get('mt_user_id'), session.get('mt_role'), exc_info=True,
            )
        finally:
            # OUTSIDE the guarded block, in its own handler. When this close
            # lived inside that `try`, a close failure -- which by definition
            # happens AFTER the answer is already computed and assigned -- fell
            # into the `except` arm above and logged "could not compute
            # capabilities ... reporting them as UNKNOWN (null)" while the
            # response body went out carrying all eight codes. The log was
            # simply false, and false in the expensive direction: it sends the
            # next operator hunting a capability-read outage that never
            # happened, straight past the real fault, which is a connection
            # that would not close. Covered by
            # test_a_close_failure_after_a_successful_computation_is_not_logged_as_unknown.
            #
            # Still logged rather than passed: relocating the close must not
            # turn it into the silent swallow this whole route was fixed for.
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    log.warning(
                        "session: failed to close the registry connection for user %s; "
                        "the capability computation itself was unaffected and the "
                        "reported value stands",
                        session.get('mt_user_id'), exc_info=True,
                    )
        return jsonify({
            'authenticated': True,
            'is_mt': True,
            'language': lang,
            # Rendering ADVICE ONLY -- see _capabilities_for_session's
            # docstring. Every route still enforces its own server-side gate.
            # TOP-LEVEL and a sibling of `user`: that is the contract both
            # clients read (see app-shell.js's _adoptSessionCapabilities
            # comment for what moving it costs). `null` here means "could not
            # compute", never "denied everything".
            'capabilities': capabilities,
            'user': {
                'id': session['mt_user_id'],
                'employee_id': session.get('employee_id'),
                'email': session.get('email'),
                'role': session.get('mt_role'),
                'clinic_role': session.get('clinic_role') or ''
            }
        })
    return jsonify({'authenticated': False})


# ── Admin: employee management ──────────────────────────────────────────────

@onboarding_bp.route('/api/admin/employees', methods=['GET'])
@mt_login_required
def get_employees():
    """Launch-readiness account-hierarchy design §4.1/§4.2 D4 -- two arms.

    ADMIN: unchanged -- every row, this company, full stop.

    DELEGATED (a branch manager, same predicate `create_employee` uses --
    role='manager' AND holding retail.employees AND a non-NULL
    branch_scope_uid, read fresh from registry.db per G6): sees ONLY
    `WHERE branch_scope_uid = <creator's own scope> AND role != 'admin'`.
    A NULL-scope cashier (head office) is outside every branch manager's
    reach here for the identical reason it is outside their reach in
    `update_status`/`update_pin` -- NULL matches nothing in a
    `branch_scope_uid = ?` comparison.

    Everyone else falls through to the SAME 'Admin only' 403 a non-admin
    gets today -- unreachable from Clinic for the identical reason
    `create_employee`'s docstring gives (§9 point 2/4).
    """
    is_admin = session.get('mt_role') == 'admin'
    conn = get_conn()
    try:
        scope_uid = None
        if not is_admin:
            creator_id = session.get('mt_user_id')
            creator = conn.execute(
                "SELECT role, branch_scope_uid FROM users WHERE id=? AND company_id=?",
                (creator_id, session.get('company_id')),
            ).fetchone()
            is_branch_manager = (
                creator is not None
                and _accounts.normalize_role(creator['role']) == _accounts.ROLE_MANAGER
                and creator['branch_scope_uid'] is not None
                and _accounts.user_has_capability(conn, creator_id, _accounts.CAP_EMPLOYEES)
            )
            if not is_branch_manager:
                return jsonify({'error': 'Admin only'}), 403
            scope_uid = creator['branch_scope_uid']

        # `pin_hash IS NOT NULL` -- the PRESENCE of a PIN, never the hash. The
        # column is a PBKDF2 digest of a four-digit secret, so a keyspace of
        # 10,000 is small enough that handing the hash to any client at all is
        # handing them the PIN; only the boolean is anyone's business.
        # `branch_scope_uid` (registry v7) added to the SELECT so the
        # employees screen can render each row's current scope without a
        # second round trip -- an opaque `branches.uid` string or NULL
        # ("every branch"), read back verbatim; this route does not resolve
        # it to a branch NAME, because `branches` lives in retail.db and
        # this identity-layer route must stay product-agnostic (see
        # branch_scope_schema.py's module docstring). The frontend already
        # fetches /api/sub/retail/branches for the scope picker and does
        # the uid-to-name lookup client-side.
        #
        # `can_manage_staff` (design §10 D9) -- ADDITIVE, same posture as
        # `branch_scope_uid`/`has_pin` above: the one piece of state the
        # owner-facing delegation toggle (employees.js) needs to render
        # "grant" vs. "revoke" correctly, without a second route to read
        # one user's permission grants (none exists). LEFT JOIN so a user
        # with no retail.employees row at all reads as NOT delegated
        # rather than vanishing from the result.
        if is_admin:
            emps = conn.execute(
                "SELECT u.id, u.employee_id, u.email, u.role, u.clinic_role, u.status, u.created_at, "
                "       u.branch_scope_uid, (u.pin_hash IS NOT NULL) AS has_pin, "
                "       (p.access_level = ?) AS can_manage_staff "
                "FROM users u LEFT JOIN user_permissions p "
                "  ON p.user_id = u.id AND p.subsystem = ? "
                "WHERE u.company_id=?",
                (_accounts.ACCESS_FULL, _accounts.CAP_EMPLOYEES, session['company_id'])
            ).fetchall()
        else:
            emps = conn.execute(
                "SELECT u.id, u.employee_id, u.email, u.role, u.clinic_role, u.status, u.created_at, "
                "       u.branch_scope_uid, (u.pin_hash IS NOT NULL) AS has_pin, "
                "       (p.access_level = ?) AS can_manage_staff "
                "FROM users u LEFT JOIN user_permissions p "
                "  ON p.user_id = u.id AND p.subsystem = ? "
                "WHERE u.company_id=? AND u.branch_scope_uid = ? AND u.role != 'admin'",
                (_accounts.ACCESS_FULL, _accounts.CAP_EMPLOYEES, session['company_id'], scope_uid)
            ).fetchall()
        rows = []
        for e in emps:
            row = dict(e)
            # ADDITIVE only. `role` keeps its stored value byte-for-byte
            # because Clinic renders this same response and its rows still say
            # 'employee'; rewriting it here would change a shipped product's
            # UI from a route this phase is not allowed to alter for it.
            # `effective_role` is the widened-domain reading (design §3) --
            # the value every capability decision is actually made against, so
            # a client that shows it is showing the truth rather than the
            # legacy spelling.
            row['effective_role'] = _accounts.normalize_role(row.get('role'))
            row['has_pin'] = bool(row.get('has_pin'))
            row['can_manage_staff'] = bool(row.get('can_manage_staff'))
            rows.append(row)
        return jsonify({'success': True, 'employees': rows})
    finally:
        conn.close()


def _delegated_employee_id_fragment():
    """`dev4` for `EMP-<dev4>-NNNN` -- design §2.4, AUDIT-032B's pattern,
    third application after `sale_number`/`return_number`/`po_number`'s
    own `_device_doc_discriminator()` (retail_api.py). NOT reused directly:
    that helper lives in retail_api.py and reads a SEPARATE, retail-only
    persisted discriminator file (`peek_doc_discriminator`); this file is
    shared with Clinic and identity-layer code, so design §2.4 names the
    source explicitly as `device_context.py::peek_local_device_uuid()` --
    the install-stable device UUID that already exists inside THIS
    package, which `local_terminal_id()` (retail's schema.py) itself
    wraps.

    Read-only: `peek_local_device_uuid()` never CREATES device identity as
    a side effect of minting an employee id (unlike its writing twin,
    `local_device_uuid()`) -- an authorization-adjacent id mint has no
    business manufacturing identity state as a side effect of being asked
    for a fragment.

    Never raises. A device whose UUID was never generated (a fresh
    install, pre-activation) AND a corrupt local_device.json (raises
    `LocalDeviceStateCorruptError`, per that function's own docstring)
    both fall back to a random 4-hex fragment -- never a shared constant,
    which would silently collide across every such install the exact way
    the bug this function exists to fix does.
    """
    try:
        value = peek_local_device_uuid()
    except Exception:
        value = None
    if not value:
        return uuid.uuid4().hex[:4]
    return str(value)[:4]


@onboarding_bp.route('/api/admin/employees', methods=['POST'])
@mt_login_required
@require_license_capability(_CAP_IDENTITY_EMPLOYEE_MANAGE,
                             restricted_mode_allowlist=_ACCOUNT_ADMIN_RESTRICTED_ALLOWLIST)
def create_employee():
    """Launch-readiness account-hierarchy design §4.1/§4.2 D1 -- two arms.

    ADMIN: today's path, byte-for-byte (§9 point 2/3) -- same gate
    (`session['mt_role'] == 'admin'`), same `EMP-{count+1:04d}` allocator,
    same INSERT, same response shape. Every id Clinic (or any existing
    Retail install) ever mints through this route is unchanged.

    DELEGATED (a branch manager -- `role='manager'` AND holding
    `retail.employees` AND a non-NULL `branch_scope_uid`, ALL read fresh
    from registry.db inside this transaction, never from the session
    cookie -- G6): may create exactly one thing, a cashier at their own
    branch, and nothing else.
      G1 role ceiling  -- normalize_role(requested role) must resolve to
                          'cashier'; anything else is refused, never
                          silently downgraded.
      G2 grant channel -- a non-empty `permissions` dict is REFUSED (403),
                          never silently dropped (design §0.2/§4.1 G2): an
                          unrefused dict is the widest escalation path on
                          the board here -- a branch manager could
                          otherwise mint a cashier holding
                          retail.cash.approve, or even retail.employees
                          itself (recursive delegation).
      G3 scope stamp   -- the new row's `branch_scope_uid` is the
                          CREATOR's own, read fresh below -- never from
                          the request body, which carries no scope field
                          on this path at all.
    Everything else (a cashier, a plain manager, a demoted/unscoped
    manager, anyone with no session) falls through to the SAME 'Admin
    only' 403 a non-admin gets today -- the identical, byte-for-byte
    refusal that keeps Clinic (whose only account-creating actor is its
    own admin) provably unreachable through this arm (§9 point 2).

    D0 (design §2.4): the whole body runs under BEGIN IMMEDIATE -- closes
    the same-device double-submit race on the email-uniqueness check and
    the employee_id counter, admin or delegated, regardless of
    delegation. The delegated arm ALSO mints `EMP-<dev4>-NNNN` rather than
    `EMP-NNNN` (`_delegated_employee_id_fragment()` above) -- two DEVICES
    creating from the same local COUNT would otherwise mint the identical
    id and collide on sync (design §2.2's silent-fork failure), which
    delegation turns from a rare owner-on-two-tills accident into a
    routine event. The admin arm's id format is UNTOUCHED: that residual
    race is today's status quo, accepted rather than fixed here (design
    §2.4) -- fixing it too would mean Clinic's ids stop being
    byte-identical (§9 point 3).
    """
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    perms = data.get('permissions', {})
    clinic_role = (data.get('clinic_role') or '').strip().lower()
    if clinic_role not in ('', 'doctor', 'secretary'):
        clinic_role = ''

    if not email:
        return jsonify({'error': 'Email required'}), 400

    # `role` is new (registry v3) and OPTIONAL, and its default is the
    # pre-v3 value rather than one of the widened ones. That is deliberate:
    # Clinic shares this route, keys its own RBAC off `clinic_role` rather
    # than `role`, and selects its staff rows with `WHERE role='employee'`.
    # Changing the default would break a product this phase is not allowed
    # to touch, for no security gain -- `user_accounts.normalize_role()`
    # reads 'employee' as 'cashier' everywhere a capability decision is
    # made, so a row created without a role is a cashier in effect either
    # way. An unrecognised value is refused rather than quietly downgraded:
    # an admin who meant to create a manager must not silently get a cashier.
    role = str(data.get('role') or _accounts.LEGACY_ROLE_EMPLOYEE).strip().lower()
    if role not in _accounts.ASSIGNABLE_ROLES:
        # A FIXED literal, not ', '.join(ASSIGNABLE_ROLES): this string is
        # translated by matching the whole English sentence against the
        # locale catalogs (products/retail/frontend/locales/{en,ar}.json --
        # see i18n.js's t()), so a sentence assembled at runtime would have
        # no key and would silently stay English on an Arabic till. It also
        # deliberately does not name the legacy 'employee' value, which is
        # accepted for API compatibility but is not a choice any UI offers.
        return jsonify({'error': 'Role must be manager or cashier.'}), 400

    is_admin = session.get('mt_role') == 'admin'

    conn = get_conn()
    try:
        # D0: takes the write lock before either the email check or the
        # employee_id counter below is read -- see this route's own
        # docstring.
        conn.execute("BEGIN IMMEDIATE")

        scope_uid = None
        if not is_admin:
            # ── D1 delegated gate (design §4.1 G1/G2/G3/G6) ────────────────
            # G6: read fresh, from registry.db, inside this transaction --
            # never session['mt_role']/anything else cached at login. A
            # manager just demoted or descoped by the owner must not keep
            # minting on a session that has not yet re-read its own row --
            # the owner's own edit already bumped session_version, so the
            # NEXT request from that session dies in mt_login_required; this
            # is the belt for the same-request window.
            creator_id = session.get('mt_user_id')
            creator = conn.execute(
                "SELECT role, branch_scope_uid FROM users WHERE id=? AND company_id=?",
                (creator_id, session.get('company_id')),
            ).fetchone()
            is_branch_manager = (
                creator is not None
                and _accounts.normalize_role(creator['role']) == _accounts.ROLE_MANAGER
                and creator['branch_scope_uid'] is not None
                # Deliberately redundant with the role check above (design
                # §4.2 D1): the pair keeps a stray hand-granted
                # retail.employees row on a CASHIER inert, since a cashier
                # can never pass the role half either way.
                and _accounts.user_has_capability(conn, creator_id, _accounts.CAP_EMPLOYEES)
            )
            if not is_branch_manager:
                conn.rollback()
                # The SAME 'Admin only' 403 a plain non-admin gets today --
                # not a different message -- so Clinic (whose non-admin
                # actors can never satisfy is_branch_manager, see this
                # route's own docstring, §9 point 2) falls through to a
                # byte-identical refusal.
                return jsonify({'error': 'Admin only'}), 403

            # G1 -- role ceiling. normalize_role(), never a raw string
            # equality: the legacy 'employee' alias must not slip a naive
            # == 'cashier' check.
            if _accounts.normalize_role(role) != _accounts.ROLE_CASHIER:
                conn.rollback()
                return jsonify({'error': 'A branch manager may only create cashiers.'}), 403
            role = _accounts.ROLE_CASHIER

            # G2 -- the escalation channel. REFUSED, never silently dropped
            # -- see this route's own docstring and design §0.2/§4.1 G2.
            if perms:
                conn.rollback()
                return jsonify({'error': 'A branch manager may not grant permissions directly.'}), 403

            # G3 -- scope stamping, from the creator's OWN row read above --
            # never from the request body, which carries no scope field on
            # this path at all.
            scope_uid = creator['branch_scope_uid']

        existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            conn.rollback()
            return jsonify({'error': 'Email already registered.'}), 400

        company_id = session.get('company_id', 'local')
        user_id = str(uuid.uuid4())
        count = conn.execute("SELECT COUNT(*) FROM users WHERE company_id=?", (company_id,)).fetchone()[0]
        # D0 (design §2.4): the admin arm's format is UNTOUCHED --
        # `EMP-{count+1:04d}`, byte-for-byte, so Clinic and every existing
        # Retail install keep minting the identical id (§9 point 3). Only
        # the delegated arm gains the device fragment.
        emp_id = (f"EMP-{count + 1:04d}" if is_admin
                  else f"EMP-{_delegated_employee_id_fragment()}-{count + 1:04d}")

        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status,
                               require_password_change, clinic_role, uid, row_version, updated_at_utc,
                               branch_scope_uid)
            VALUES (?, ?, ?, ?, ?, ?, 'pending_setup', 0, ?, ?, 1, ?, ?)
        """, (user_id, company_id, emp_id, email, 'PENDING', role, clinic_role,
              str(uuid.uuid4()), _accounts.now_utc_iso(), scope_uid))

        # Phase 5 wave B2 stage 2b. `conn`, never `cur` -- `cur` is reused
        # below for the permission rows, the secure_links insert and the
        # audit row, and `_queue_user_sync_event` issues its own statement
        # internally; running it on `cur` would leave `cur.lastrowid`
        # pointing at the outbox row instead of whatever the caller reads it
        # for next (see that function's own docstring for the named bug this
        # avoids). `row_version` is 1 from the INSERT above -> `create`.
        _accounts._queue_user_sync_event(conn, user_id, 'create')

        for sub, lvl in perms.items():
            if lvl != 'none':
                cur.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?, ?, ?, ?)",
                            (str(uuid.uuid4()), user_id, sub, lvl))
                # Phase 5 wave B2 stage 3: an explicit grant made AT CREATION
                # TIME is still a grant a peer device needs -- `conn`, not
                # `cur`, matching every other emit call in this file (see
                # _queue_user_sync_event's own docstring for the lastrowid
                # trap this avoids).
                _accounts._queue_user_permission_sync_event(conn, user_id, sub, 'create')

        # AFTER the caller's explicit grants, never before: seeding is
        # INSERT OR IGNORE against UNIQUE(user_id, subsystem), so running it
        # second means an explicit grant for the same code wins and the seed
        # only fills the gaps. Running it first would make the caller's own
        # request collide with the defaults. `emit_sync=True`: this is the
        # headline stage-3 site -- a new hire arrives on another till with a
        # WORKING permission set, not an account that logs in and can do
        # nothing there.
        _accounts.seed_capabilities_for_user(conn, user_id, role, emit_sync=True)

        raw_token = uuid.uuid4().hex
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        # Timezone-AWARE UTC ('...+00:00'), matching `_accounts.now_utc_iso()`
        # and `verification.py`'s `_now()` -- which already writes this exact
        # column, in this exact format, for password-reset and verification
        # links. `datetime.utcnow()` is deprecated and scheduled for removal,
        # and its naive output was the odd one out in a table two other
        # writers already stamped as aware.
        #
        # Read back by `employee_setup()` as TEXT, against
        # `_accounts.now_utc_iso()` -- the same format, so the pair is
        # symmetric. Rows written by the previous naive code are still read
        # correctly: both forms start with the full 'YYYY-MM-DDTHH:MM:SS'
        # prefix, so the lexicographic compare is decided by the timestamp
        # itself and only ever reaches the '+00:00' suffix on a sub-second
        # tie, which for a seven-day link is not a distinction that exists.
        # Pinned by tests/test_employee_setup_link_expiry.py, which asserts
        # the expiry gate against BOTH stored formats.
        expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        cur.execute(
            "INSERT INTO secure_links (id, company_id, token_hash, email_target, expires_at) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), company_id, token_hash, email, expires_at)
        )

        try:
            cur.execute(
                "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id, new_value_json) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), company_id, session['mt_user_id'], 'CREATE_EMPLOYEE', 'USER', str(user_id), json.dumps({'email': email}))
            )
        except Exception:
            pass

        conn.commit()

        base_url = request.host_url.rstrip('/')
        setup_url = f'{base_url}/#setup/{raw_token}'
        return jsonify({'success': True, 'setup_link': setup_url})
    finally:
        conn.close()


@onboarding_bp.route('/api/admin/employees/<string:user_id>/status', methods=['PUT'])
@mt_login_required
@require_license_capability(_CAP_IDENTITY_EMPLOYEE_MANAGE,
                             restricted_mode_allowlist=_ACCOUNT_ADMIN_RESTRICTED_ALLOWLIST)
def update_status(user_id):
    """Enable/disable toggle -- had none of `update_role`'s guards: no 404 for
    an unknown id, no validation of the status string (accepted and stored
    `status='banana'` verbatim), and no owner bar.

    Disabling the sole admin is UNRECOVERABLE: the disabled admin still has a
    real `password_hash`, so `onboarding_status` keeps answering
    `needs_setup=false` and `create-admin` keeps answering 409, while
    `authenticate_registry_user` refuses the login -- and every route that
    could undo it sits behind the admin session nobody can obtain any more.

    Launch-readiness account-hierarchy design §4.1/§4.2 D2 -- two arms.

    ADMIN: untouched, including the 409 owner-disable bar above.

    DELEGATED (a branch manager, same predicate `create_employee` uses,
    read fresh from registry.db per G6): G4 target rule -- the target's
    `normalize_role` must be 'cashier', its `branch_scope_uid` must equal
    the creator's OWN scope (a NULL-scope/head-office cashier matches
    nothing here), and the target must not be the caller. ONLY
    `status='disabled'` is accepted -- the ENABLE direction is refused
    with 403 for a delegated caller (design §3.4: re-enable is
    owner-only, asymmetrically to disable. Disable is the SAFETY action a
    branch manager needs at 9pm without phoning the owner; re-enable is
    the TRUST action that must never be quietly available to a
    sympathetic or complicit manager -- a worker the OWNER disabled for
    suspected theft must not be reactivated by anyone else).

    The branch-manager check runs BEFORE the target row is even looked
    up, matching the admin arm's own shape (its gate runs before any
    connection is opened at all) -- a caller who is not privileged at all
    must not learn whether a given id exists in this company via the
    404-vs-403 distinction.
    """
    is_admin = session.get('mt_role') == 'admin'

    status = (request.json or {}).get('status')
    # The real domain this specific control writes: the frontend's own
    # `_setStatus` (employees.js) only ever sends 'active' or 'disabled'.
    # 'pending_setup' is a state an invited account starts in and leaves only
    # through the invite/setup flow (`employee_setup` below) -- never a value
    # this admin toggle is meant to assign.
    if status not in ('active', 'disabled'):
        return jsonify({'error': 'Status must be active or disabled.'}), 400

    conn = get_conn()
    try:
        # D0: BEGIN IMMEDIATE closes the same-device double-submit race on
        # the row read below, admin or delegated.
        conn.execute("BEGIN IMMEDIATE")

        creator = None
        if not is_admin:
            # ── D2 delegated gate, part 1 (design §4.1 G6) ──────────────────
            creator_id = session.get('mt_user_id')
            creator = conn.execute(
                "SELECT role, branch_scope_uid FROM users WHERE id=? AND company_id=?",
                (creator_id, session.get('company_id')),
            ).fetchone()
            is_branch_manager = (
                creator is not None
                and _accounts.normalize_role(creator['role']) == _accounts.ROLE_MANAGER
                and creator['branch_scope_uid'] is not None
                and _accounts.user_has_capability(conn, creator_id, _accounts.CAP_EMPLOYEES)
            )
            if not is_branch_manager:
                conn.rollback()
                return jsonify({'error': 'Admin only'}), 403
            if status != 'disabled':
                conn.rollback()
                return jsonify({'error': 'A branch manager may not reactivate an account.'}), 403

        row = conn.execute(
            "SELECT id, role, branch_scope_uid FROM users WHERE id=? AND company_id=?",
            (user_id, session['company_id']),
        ).fetchone()
        if not row:
            conn.rollback()
            return jsonify({'error': 'User not found.'}), 404

        if is_admin:
            # One admin per install (`create_admin` is gated on "no valid admin
            # exists yet") and no way to mint a second -- refuse only the
            # DISABLE direction; re-affirming an already-active owner as
            # 'active' is a harmless no-op and stays allowed.
            if status == 'disabled' and _accounts.normalize_role(row['role']) == _accounts.ROLE_ADMIN:
                conn.rollback()
                return jsonify({'error': 'You cannot disable the owner account.'}), 409
        else:
            # ── D2 delegated gate, part 2 (design §4.1 G4) ──────────────────
            target_is_own_branch_cashier = (
                str(user_id) != str(session.get('mt_user_id'))
                and _accounts.normalize_role(row['role']) == _accounts.ROLE_CASHIER
                and row['branch_scope_uid'] is not None
                and row['branch_scope_uid'] == creator['branch_scope_uid']
            )
            if not target_is_own_branch_cashier:
                conn.rollback()
                return jsonify({'error': 'You may only manage cashiers at your own branch.'}), 403

        conn.execute("UPDATE users SET status=?, session_version=session_version+1, "
                     "row_version=COALESCE(row_version, 1)+1, updated_at_utc=? "
                     "WHERE id=? AND company_id=?",
                     (status, _accounts.now_utc_iso(), user_id, session['company_id']))
        # Phase 5 wave B2 stage 2b: a suspend must reach every other till, or
        # a disabled cashier could keep transacting on a device that never
        # heard about it.
        _accounts._queue_user_sync_event(conn, user_id, 'update')
        conn.execute(
            "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id, new_value_json) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), session['company_id'], session['mt_user_id'], 'UPDATE_STATUS', 'USER', str(user_id), json.dumps({'status': status})))
        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()


@onboarding_bp.route('/api/admin/employees/<string:user_id>/branch-scope', methods=['PUT'])
@mt_login_required
@require_license_capability(_CAP_IDENTITY_EMPLOYEE_MANAGE,
                             restricted_mode_allowlist=_ACCOUNT_ADMIN_RESTRICTED_ALLOWLIST)
def update_branch_scope(user_id):
    """D5 (launch-readiness account-hierarchy design §3.3/§4.2) -- sets or
    clears an employee's `branch_scope_uid` (registry v7). NULL (a missing
    key, JSON `null`, or a blank string in the request body) means "every
    branch"; anything else is a `branches.uid` string, stored VERBATIM and
    OPAQUELY.

    THIS ROUTE NEVER VALIDATES THE UID AGAINST A `branches` ROW, on
    purpose. `branches` lives in retail.db, a different product's database
    from this one (registry.db) -- `commercial_runtime.identity` is shared
    by Retail AND Clinic and must stay product-agnostic, so it cannot import
    a retail-only table without breaking that layering. See
    branch_scope_schema.py's own module docstring for the full reasoning.
    What keeps a LEGITIMATE request honest is the retail-facing caller: the
    employees screen (products/retail/frontend/employees.js) only ever
    offers this company's real branches in its picker, so nothing an owner
    can actually click can name a uid this route would have refused anyway.
    A hand-made request naming a nonexistent uid is stored as given and
    simply matches no branch anywhere it is later compared -- inert, not
    dangerous, since D7's data-plane enforcement (retail_api.py) resolves a
    scope back to a LOCAL branch id and treats "resolves to nothing" as "no
    branch", never as "every branch".

    ADMIN ONLY, and refuses the admin's OWN row -- the owner is never
    scoped, structurally (design §3.3). Guarded by
    `normalize_role(row['role']) == ROLE_ADMIN`, the identical check
    `update_status`/`update_role` already use for their own owner bars,
    rather than an id comparison against the caller's session -- there is
    exactly one admin per install, so the two are equivalent in practice,
    but the role check is what every sibling route in this file reads for
    the same purpose, and it also refuses a request naming the owner's row
    by a stale/guessed id rather than the caller's own session id.

    Bumps `session_version` (so a session already open on this account
    re-reads its scope on its very next request -- design §4.1 G6: "the
    delegated gate resolves the creator's role, scope... at request time...
    never from the session cookie") and `row_version` +
    `_queue_user_sync_event`, inline, in the SAME transaction, exactly like
    every sibling write in this file (`update_status`, `update_role`,
    `update_perms`, `update_clinic_role`).
    """
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin only'}), 403

    data = request.json or {}
    raw = data.get('branch_scope_uid')
    # Any non-empty string is stored as-is (trimmed); a missing key, JSON
    # `null`, an empty string, or whitespace-only all clear the scope -- the
    # route's own "null clears" contract, generalised to every shape a
    # hand-made request could plausibly send instead of a strict `is None`
    # check that a bare `''` would silently slip past.
    scope_uid = raw.strip() if isinstance(raw, str) and raw.strip() else None

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, role FROM users WHERE id=? AND company_id=?",
            (user_id, session['company_id']),
        ).fetchone()
        if not row:
            return jsonify({'error': 'User not found.'}), 404

        # The owner is never scoped -- structurally, not merely by
        # convention. 409, matching update_role's own "conflict with a
        # standing invariant" status for the identical owner-row refusal,
        # rather than 400 (this is not malformed input).
        if _accounts.normalize_role(row['role']) == _accounts.ROLE_ADMIN:
            return jsonify({'error': 'The owner account is never scoped to a branch.'}), 409

        conn.execute(
            "UPDATE users SET branch_scope_uid=?, session_version=session_version+1, "
            "row_version=COALESCE(row_version, 1)+1, updated_at_utc=? "
            "WHERE id=? AND company_id=?",
            (scope_uid, _accounts.now_utc_iso(), user_id, session['company_id']),
        )
        # Phase 5 wave B2 stage 2b's pattern, extended to branch_scope_uid
        # (registry v7): a scope change must reach every other till, or a
        # peer holding this user's OLD (or no) scope keeps enforcing it --
        # `session_version` above already forces re-login there; without
        # this queue call the account's stale branch_scope_uid would still
        # be what a second device's own copy of the row says.
        _accounts._queue_user_sync_event(conn, user_id, 'update')
        try:
            conn.execute(
                "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id, new_value_json) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), session['company_id'], session['mt_user_id'],
                 'UPDATE_BRANCH_SCOPE', 'USER', user_id, json.dumps({'branch_scope_uid': scope_uid})),
            )
        except Exception:
            pass
        conn.commit()
        return jsonify({'success': True, 'branch_scope_uid': scope_uid})
    finally:
        conn.close()


@onboarding_bp.route('/api/admin/employees/<string:user_id>/role', methods=['PUT'])
@mt_login_required
@require_license_capability(_CAP_IDENTITY_EMPLOYEE_MANAGE,
                             restricted_mode_allowlist=_ACCOUNT_ADMIN_RESTRICTED_ALLOWLIST)
def update_role(user_id):
    """Move an existing employee between the widened roles (design §3).

    WHY THIS EXISTS AT ALL: registry v3 widened `users.role` to
    {admin, manager, cashier} and `create_employee` learned to accept a role,
    but nothing could ever CHANGE one afterwards -- the only role-shaped route
    in this file was `update_clinic_role`, which writes the unrelated
    `clinic_role` column. So "promote this cashier to manager" was an
    operation the data model supported and the API did not, and the only
    workaround was delete-and-reinvite, which throws away the account's
    identity (`uid`), its history, and its PIN.

    Three things move together here, and all three are load-bearing:

    1. `role` itself -- what `normalize_role()` reads for every capability
       default, and what `sync_service.py` reads to find the admin's company.
    2. `session_version` -- design §4 makes a bump the revocation channel for
       `users`. A demotion that left the person's live session alone would
       leave a manager holding manager access until they happened to log out;
       `mt_auth._session_version_is_stale()` turns the bump into "their very
       next request re-authenticates against the new role".
    3. The capability rows -- see the reset below, which is the subtle part.
    """
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin only'}), 403

    role = str((request.json or {}).get('role') or '').strip().lower()
    # Same FIXED literal `create_employee` returns, for the same reason (it is
    # translated by whole-sentence lookup against the locale catalogs, so a
    # runtime-assembled sentence would have no key). Deliberately does NOT
    # accept the legacy 'employee' value that `create_employee` still tolerates
    # for API compatibility: this route is only ever reached from a UI that
    # offers exactly two choices, and accepting a third would let an admin
    # silently move somebody onto a value the widened domain does not contain.
    if role not in (_accounts.ROLE_MANAGER, _accounts.ROLE_CASHIER):
        return jsonify({'error': 'Role must be manager or cashier.'}), 400

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, role FROM users WHERE id=? AND company_id=?",
            (user_id, session['company_id']),
        ).fetchone()
        # Scoped by company_id, so a row belonging to another tenant is
        # indistinguishable from one that does not exist -- the same shape the
        # sibling routes' UPDATE ... WHERE company_id=? already enforces.
        if not row:
            return jsonify({'error': 'User not found.'}), 404

        # The owner account is not demotable, and nothing is promotable INTO
        # it. `create_admin` is gated on "no valid admin exists yet", so this
        # install has exactly one admin: demoting them would leave a shop with
        # no account that can manage employees, approve cash variances, or
        # reach the admin-device surface -- an irreversible self-lockout
        # through a two-tap UI control. `ASSIGNABLE_ROLES` already excludes
        # 'admin' for the mirror-image reason (no side door to a second owner).
        #
        # Not in the locale catalogs, matching `update_clinic_role`'s own
        # backstop message directly below: the UI structurally never offers a
        # role control on the owner row, so this sentence is an API-level
        # refusal for a hand-made request rather than a screen a user reaches.
        if _accounts.normalize_role(row['role']) == _accounts.ROLE_ADMIN:
            return jsonify({'error': "The owner account's role cannot be changed."}), 409

        conn.execute(
            "UPDATE users SET role=?, session_version=session_version+1, "
            "row_version=COALESCE(row_version, 1)+1, updated_at_utc=? "
            "WHERE id=? AND company_id=?",
            (role, _accounts.now_utc_iso(), user_id, session['company_id']),
        )
        # Phase 5 wave B2 stage 2b: a promotion/demotion must reach every
        # other till -- `session_version` above already forces re-login
        # there, but without this the account's stale `role` would still be
        # what a second device's OWN capability table was seeded against.
        _accounts._queue_user_sync_event(conn, user_id, 'update')

        # RESET the capability rows to the new role's defaults.
        #
        # A plain re-seed would be a NO-OP and therefore a bug:
        # `seed_capabilities_for_user` is INSERT OR IGNORE against
        # UNIQUE(user_id, subsystem), and every account already has all eight
        # rows from creation -- so "promote to manager" would move the role
        # string and grant nothing. Deleting first is what makes the promotion
        # real.
        #
        # The cost is that a per-user grant the owner tuned earlier (say, a
        # cashier they trusted with refunds) is discarded. That is the right
        # trade and the UI says so before the tap: a role IS the permission
        # preset, and silently carrying old exceptions across a role change
        # would produce accounts whose access nobody can predict from their
        # role -- exactly the state capability codes exist to eliminate.
        #
        # CRITICAL: bounded to the eight namespaced codes. The legacy
        # `subsystem='retail'` row is what `mt_require_subsystem` reads on ~80
        # routes TODAY; an unbounded `DELETE ... WHERE user_id=?` would delete
        # it and lock the employee out of the entire retail app as a side
        # effect of changing their job title.
        placeholders = ','.join('?' * len(_accounts.CAPABILITY_CODES))
        conn.execute(
            f"DELETE FROM user_permissions WHERE user_id=? AND subsystem IN ({placeholders})",
            (user_id, *_accounts.CAPABILITY_CODES),
        )
        # Phase 5 wave B2 stage 3 (Decision 4): each of the eight rows just
        # removed must reach every other device, or a peer that already had
        # this user's OLD grants keeps them after this device revoked them
        # -- a silent privilege escalation elsewhere. The reseed immediately
        # below re-queues a `create` for every one of these same eight
        # `(user, subsystem)` pairs with the NEW role's defaults, so a
        # receiver ends up correct either way the two arrive relative to
        # each other -- but the explicit delete is what keeps a receiver
        # correct even if only PART of this transaction's events have
        # arrived so far (see the design doc's own D4 reasoning).
        for _code in _accounts.CAPABILITY_CODES:
            _accounts._queue_user_permission_sync_event(conn, user_id, _code, 'delete')
        _accounts.seed_capabilities_for_user(conn, user_id, role, emit_sync=True)

        try:
            conn.execute(
                "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id, new_value_json) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), session['company_id'], session['mt_user_id'],
                 'UPDATE_ROLE', 'USER', user_id,
                 json.dumps({'from': row['role'], 'to': role})),
            )
        except Exception:
            pass
        conn.commit()
        return jsonify({'success': True, 'role': role})
    finally:
        conn.close()


@onboarding_bp.route('/api/admin/employees/<string:user_id>/pin', methods=['PUT', 'DELETE'])
@mt_login_required
@require_license_capability(_CAP_IDENTITY_EMPLOYEE_MANAGE,
                             restricted_mode_allowlist=_ACCOUNT_ADMIN_RESTRICTED_ALLOWLIST)
def update_pin(user_id):
    """Set (PUT) or clear (DELETE) an employee's till PIN.

    The HTTP surface for `user_accounts.set_user_pin` / `clear_user_pin`,
    which registry v3 added and which had no caller anywhere -- the hashing,
    the Arabic-Indic digit folding and the "attribution, never authorization"
    rule were all written and all unreachable. A PIN nobody can set is not a
    feature, and the two locale catalogs already carry this route's only
    user-facing refusal ("PIN must be exactly 4 digits.") in both languages.

    DELIBERATELY DOES NOT BUMP `session_version`. Every other write in this
    file does, because every other write changes what the person is ALLOWED to
    do, and a live session holding the old answer is a security hole. A PIN
    changes only which user id gets stamped on the rows a terminal writes
    (design §3), so bumping here would log a cashier out of a till mid-sale to
    propagate a change that alters none of their permissions. `set_user_pin`
    still moves `row_version`/`updated_at_utc` via `_touch_user`, so the row
    is correctly marked dirty for sync without revoking anything.

    The PIN is never echoed back, never logged, and never written to the audit
    row -- only the fact that it changed.

    Launch-readiness account-hierarchy design §4.1/§4.2 D3 -- the SAME
    non-admin gate as D2 (`update_status`): G4 target rule (own-branch
    cashiers only, never self) plus G6 (fresh read, from registry.db,
    inside this request). Applies to BOTH directions (PUT sets, DELETE
    clears) -- a branch manager may reset or clear a cashier's PIN at
    their own branch either way; `SET_PIN`/`CLEAR_PIN` audit rows already
    name the actor, which is the frame-up deterrent design §12 relies on
    for this exact lever.
    """
    is_admin = session.get('mt_role') == 'admin'

    conn = get_conn()
    try:
        creator = None
        if not is_admin:
            # ── D3 delegated gate, part 1 (design §4.1 G6) ──────────────────
            creator_id = session.get('mt_user_id')
            creator = conn.execute(
                "SELECT role, branch_scope_uid FROM users WHERE id=? AND company_id=?",
                (creator_id, session.get('company_id')),
            ).fetchone()
            is_branch_manager = (
                creator is not None
                and _accounts.normalize_role(creator['role']) == _accounts.ROLE_MANAGER
                and creator['branch_scope_uid'] is not None
                and _accounts.user_has_capability(conn, creator_id, _accounts.CAP_EMPLOYEES)
            )
            if not is_branch_manager:
                return jsonify({'error': 'Admin only'}), 403

        row = conn.execute(
            "SELECT id, role, branch_scope_uid FROM users WHERE id=? AND company_id=?",
            (user_id, session['company_id']),
        ).fetchone()
        if not row:
            return jsonify({'error': 'User not found.'}), 404

        if not is_admin:
            # ── D3 delegated gate, part 2 (design §4.1 G4) ──────────────────
            target_is_own_branch_cashier = (
                str(user_id) != str(session.get('mt_user_id'))
                and _accounts.normalize_role(row['role']) == _accounts.ROLE_CASHIER
                and row['branch_scope_uid'] is not None
                and row['branch_scope_uid'] == creator['branch_scope_uid']
            )
            if not target_is_own_branch_cashier:
                return jsonify({'error': 'You may only manage cashiers at your own branch.'}), 403

        if request.method == 'DELETE':
            _accounts.clear_user_pin(conn, user_id)
            action, has_pin = 'CLEAR_PIN', False
        else:
            try:
                _accounts.set_user_pin(conn, user_id, (request.json or {}).get('pin'))
            except _accounts.PinPolicyError as exc:
                # str(exc) is set_user_pin's own f-string, which renders
                # exactly the catalog key "PIN must be exactly 4 digits." --
                # kept as the raised text rather than re-spelled here so the
                # policy and its message can never drift apart.
                return jsonify({'error': str(exc)}), 400
            action, has_pin = 'SET_PIN', True

        try:
            conn.execute(
                "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id, new_value_json) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), session['company_id'], session['mt_user_id'],
                 action, 'USER', user_id, json.dumps({'has_pin': has_pin})),
            )
        except Exception:
            pass
        conn.commit()
        return jsonify({'success': True, 'has_pin': has_pin})
    finally:
        conn.close()


@onboarding_bp.route('/api/admin/employees/<string:user_id>/clinic-role', methods=['PUT'])
@mt_login_required
@require_license_capability(_CAP_IDENTITY_EMPLOYEE_MANAGE,
                             restricted_mode_allowlist=_ACCOUNT_ADMIN_RESTRICTED_ALLOWLIST)
def update_clinic_role(user_id):
    """Set or clear a staff member's clinic role (doctor / secretary / none).
    Bumps session_version so the change takes effect on their next request
    -- this is what makes RBAC role changes take effect safely rather than
    leaving a stale, already-issued session with the old permission set."""
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    clinic_role = (request.json.get('clinic_role') or '').strip().lower()
    if clinic_role not in ('', 'doctor', 'secretary'):
        return jsonify({'error': 'clinic_role must be doctor, secretary, or empty.'}), 400
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE users SET clinic_role=?, session_version=session_version+1, "
            "row_version=COALESCE(row_version, 1)+1, updated_at_utc=? WHERE id=? AND company_id=?",
            (clinic_role, _accounts.now_utc_iso(), user_id, session['company_id'])
        )
        # Phase 5 wave B2 stage 2b -- SUBTLE: `clinic_role` itself is NOT in
        # the sync allowlist (a Clinic-owned column; see sync_service.py's
        # `user` branch), so this event's payload carries every OTHER
        # allowlisted field unchanged. It still has to be queued, because the
        # `session_version` bump just above is the revocation this route
        # exists for -- design Decision 3 applies session_version as
        # MAX(local, incoming) precisely so a bump made here still forces
        # re-login on every OTHER device, even though clinic_role itself
        # never travels. Skipping the emit here would silently keep that
        # revocation local to this one device.
        _accounts._queue_user_sync_event(conn, user_id, 'update')
        try:
            conn.execute(
                "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id, new_value_json) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), session['company_id'], session['mt_user_id'],
                 'UPDATE_CLINIC_ROLE', 'USER', user_id, json.dumps({'clinic_role': clinic_role}))
            )
        except Exception:
            pass
        conn.commit()
        return jsonify({'success': True, 'clinic_role': clinic_role})
    finally:
        conn.close()


#: `mt_require_subsystem` still reads these two literal, un-namespaced values
#: on ~85 routes total across both products (Retail's 'retail', Clinic's
#: 'clinic') -- the ONLY legacy subsystem strings actually written or read
#: anywhere in this codebase (confirmed by grepping every
#: `mt_require_subsystem(...)` call site). `update_perms`'s new validation
#: below must keep accepting exactly these two alongside the eight namespaced
#: capability codes, or it would lock an admin out of ever granting the
#: legacy gate those routes still check.
_LEGACY_SUBSYSTEMS = ('retail', 'clinic')


@onboarding_bp.route('/api/admin/employees/<string:user_id>/permissions', methods=['POST'])
@mt_login_required
@require_license_capability(_CAP_IDENTITY_EMPLOYEE_MANAGE,
                             restricted_mode_allowlist=_ACCOUNT_ADMIN_RESTRICTED_ALLOWLIST)
def update_perms(user_id):
    """Grant or revoke one capability/subsystem row for an employee.

    Was NOT tenant-scoped: unlike `update_role`, `update_status` and
    `update_clinic_role`, it deleted and re-inserted `user_permissions` with
    a bare `WHERE user_id=?` and bumped the user with a bare `WHERE id=?` --
    no `company_id` predicate anywhere. In the shared-registry design an
    admin of company A could rewrite permissions for a user of company B
    just by knowing (or guessing) their id.

    It also wrote an arbitrary caller-supplied `subsystem` and `access_level`
    with no validation at all.
    """
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin only'}), 403

    data = request.json or {}
    sub = data.get('subsystem')
    lvl = data.get('access_level')

    # `user_permissions.access_level`'s real domain -- the table's own
    # DEFAULT is 'none', `mt_require_subsystem` already treats 'none' as a
    # refusal, and 'full'/'none' are the only two values anything in this
    # codebase ever writes or reads (user_accounts.ACCESS_FULL/ACCESS_NONE).
    # This route has no screen yet (see user_accounts.py's ROLE_CAPABILITIES
    # docstring), so this refusal is plain, un-catalogued English, the same
    # way update_clinic_role's own format check just above is.
    if lvl not in (_accounts.ACCESS_FULL, _accounts.ACCESS_NONE):
        return jsonify({'error': 'access_level must be full or none.'}), 400

    # `subsystem`'s real domain: the eight namespaced capability codes plus
    # the two legacy values above. Rejecting anything else stops this route
    # from writing a `user_permissions` row no reader will ever consult.
    if sub not in _accounts.CAPABILITY_CODES and sub not in _LEGACY_SUBSYSTEMS:
        return jsonify({'error': 'subsystem is not recognized.'}), 400

    conn = get_conn()
    try:
        # Establishes tenant ownership BEFORE either write below touches
        # anything -- `user_permissions` has no `company_id` column of its
        # own (nothing in this schema does; see account_schema.py), so this
        # lookup is the only thing making the DELETE/INSERT that follow
        # tenant-safe, exactly the way update_role's own unscoped
        # `user_permissions` DELETE is protected by its prior row lookup.
        row = conn.execute(
            "SELECT id FROM users WHERE id=? AND company_id=?",
            (user_id, session['company_id']),
        ).fetchone()
        if not row:
            return jsonify({'error': 'User not found.'}), 404

        conn.execute("DELETE FROM user_permissions WHERE user_id=? AND subsystem=?", (user_id, sub))
        # Phase 5 wave B2 stage 3 (Decision 4): this DELETE really can leave
        # the pair with no row at all until the INSERT just below runs --
        # queued here, before the INSERT, so a receiver applying the two in
        # this same order sees the same momentary state this device did.
        _accounts._queue_user_permission_sync_event(conn, user_id, sub, 'delete')
        conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?, ?, ?, ?)",
                     (str(uuid.uuid4()), user_id, sub, lvl))
        # The corrected grant -- 'update' (not 'create'): this route's whole
        # job is changing an EXISTING (user, subsystem) pair's access_level,
        # even though the row underneath it was just replaced. Either
        # event_type upserts identically on the receiver (design Decision
        # 2), so this is a naming choice for a future reader, not a
        # behavioural one.
        _accounts._queue_user_permission_sync_event(conn, user_id, sub, 'update')
        conn.execute("UPDATE users SET session_version=session_version+1, "
                     "row_version=COALESCE(row_version, 1)+1, updated_at_utc=? WHERE id=? AND company_id=?",
                     (_accounts.now_utc_iso(), user_id, session['company_id']))
        # Phase 5 wave B2 stage 2b -- SAME reasoning as update_clinic_role's
        # own comment: `user_permission` rows are not synced yet (stage 3),
        # so this event's payload does not carry the permission that just
        # changed. It still has to be queued because the `session_version`
        # bump above is a deliberate revocation (a device that already had
        # this user's OLD permission set logged in must re-authenticate to
        # pick up the new one) -- MAX(local, incoming) on the receiving side
        # only propagates that revocation if an event actually arrives.
        _accounts._queue_user_sync_event(conn, user_id, 'update')
        conn.execute(
            "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id, new_value_json) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), session['company_id'], session['mt_user_id'], 'UPDATE_PERM', 'USER_PERMISSION', str(user_id), json.dumps({sub: lvl})))
        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()


@onboarding_bp.route('/api/admin/audit', methods=['GET'])
@mt_login_required
def get_audit():
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    company_id = session['company_id']

    dept      = request.args.get('dept', '').strip()
    action    = request.args.get('action', '').strip()
    from_date = request.args.get('from_date', '').strip()
    to_date   = request.args.get('to_date', '').strip()
    user      = request.args.get('user', '').strip()
    page      = max(1, int(request.args.get('page', 1)))
    limit     = min(200, int(request.args.get('limit', 50)))
    offset    = (page - 1) * limit

    conditions = ['company_id=?']
    params     = [company_id]
    if dept:
        conditions.append('LOWER(entity_type) LIKE ?')
        params.append(f'%{dept.lower()}%')
    if action:
        conditions.append('LOWER(action) LIKE ?')
        params.append(f'%{action.lower()}%')
    if from_date:
        conditions.append('created_at >= ?')
        params.append(from_date)
    if to_date:
        conditions.append('created_at <= ?')
        params.append(to_date + ' 23:59:59')
    if user:
        conditions.append('(LOWER(user_id) LIKE ? OR LOWER(new_value_json) LIKE ?)')
        params.extend([f'%{user.lower()}%', f'%{user.lower()}%'])

    where = ' AND '.join(conditions)
    conn = get_conn()
    try:
        total = conn.execute(f'SELECT COUNT(*) FROM audit_logs WHERE {where}', params).fetchone()[0]
        logs = conn.execute(
            f'SELECT * FROM audit_logs WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?',
            params + [limit, offset]
        ).fetchall()
        return jsonify({
            'success': True, 'logs': [dict(l) for l in logs], 'total': total,
            'page': page, 'pages': max(1, -(-total // limit)), 'limit': limit,
        })
    except Exception:
        return jsonify({'success': True, 'logs': [], 'total': 0, 'page': 1, 'pages': 1})
    finally:
        conn.close()


@onboarding_bp.route('/api/admin/stats', methods=['GET'])
@mt_login_required
def get_admin_stats():
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    company_id = session['company_id']
    conn = get_conn()
    try:
        # Aware UTC purely to retire the deprecated `datetime.utcnow()`; the
        # rendered value is byte-identical because '%Y-%m-%d' never formats an
        # offset, so this cannot move the audit_today boundary.
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        rows = conn.execute(
            'SELECT status, COUNT(*) as cnt FROM users WHERE company_id=? GROUP BY status', (company_id,)
        ).fetchall()
        emp_counts = {r['status']: r['cnt'] for r in rows}
        total_emp = sum(emp_counts.values())

        audit_today = conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE company_id=? AND created_at >= ?", (company_id, today)
        ).fetchone()[0]

        last_login_row = conn.execute(
            "SELECT created_at FROM audit_logs WHERE company_id=? ORDER BY created_at DESC LIMIT 1", (company_id,)
        ).fetchone()
        last_login = last_login_row['created_at'] if last_login_row else None

        dept_rows = conn.execute(
            "SELECT entity_type, COUNT(*) as cnt FROM audit_logs WHERE company_id=? GROUP BY entity_type", (company_id,)
        ).fetchall()
        by_dept = {r['entity_type']: r['cnt'] for r in dept_rows}

        role_rows = conn.execute(
            "SELECT role, COUNT(*) as cnt FROM users WHERE company_id=? GROUP BY role", (company_id,)
        ).fetchall()
        by_role = {r['role']: r['cnt'] for r in role_rows}

        return jsonify({
            'success': True,
            'employees': {
                'total': total_emp, 'active': emp_counts.get('active', 0),
                'inactive': emp_counts.get('disabled', 0), 'pending': emp_counts.get('pending_setup', 0),
            },
            'audit_today': audit_today, 'last_login': last_login,
            'by_department': by_dept, 'by_role': by_role,
        })
    finally:
        conn.close()


@onboarding_bp.route('/api/admin/company/settings', methods=['GET', 'POST'])
@mt_login_required
def company_settings():
    """GET/POST split into two inner functions below rather than one gated
    view function, on purpose: `@require_license_capability` refuses the
    whole request it wraps, and the GET branch (reading the shop's own
    settings) is a READ -- it must stay reachable in every licensing state,
    exactly like `get_employees`/`get_audit`/`get_admin_stats` above, none
    of which are gated. Only the POST branch (editing them) is an admin
    mutation, gated the same way its siblings in this file now are."""
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    if request.method == 'POST':
        return _company_settings_update()
    return _company_settings_view()


def _company_settings_view():
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM company_settings WHERE company_id=?", (session['company_id'],)
        ).fetchone()
        return jsonify({'success': True, 'settings': dict(row) if row else {}})
    finally:
        conn.close()


@require_license_capability(_CAP_IDENTITY_COMPANY_SETTINGS_MANAGE,
                             restricted_mode_allowlist=_ACCOUNT_ADMIN_RESTRICTED_ALLOWLIST)
def _company_settings_update():
    conn = get_conn()
    try:
        data = request.get_json() or {}
        allowed = ['country', 'timezone', 'currency', 'currency_symbol',
                   'business_type', 'language', 'date_format', 'fiscal_year_start']
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return jsonify({'error': 'No valid fields provided'}), 400
        existing = conn.execute(
            "SELECT id FROM company_settings WHERE company_id=?", (session['company_id'],)
        ).fetchone()
        if not existing:
            conn.execute("INSERT INTO company_settings (id, company_id) VALUES (?,?)",
                         (str(uuid.uuid4()), session['company_id']))
        sets = ', '.join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE company_settings SET {sets} WHERE company_id=?",
                     list(updates.values()) + [session['company_id']])
        _write_config({k: v for k, v in updates.items() if k in ('country', 'timezone', 'currency', 'language')})
        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()


# ── Employee self-setup (accepts an admin-issued invite link) ─────────────────

@onboarding_bp.route('/api/auth/employee/setup', methods=['POST'])
def employee_setup():
    data = request.json or {}
    token = data.get('token')
    # .strip() matches create_admin() (line ~97) and login()
    # (auth_routes.py:29) -- without it, a stray leading/trailing space from
    # a clipboard-copied invite password gets baked into the stored hash
    # here, but login() always strips before verifying, so the account can
    # never authenticate again with any input (AUDIT: employee_setup
    # password-strip mismatch).
    password = (data.get('password') or '').strip()
    if not token or not password:
        return jsonify({'error': 'Missing data'}), 400
    # Same minimum-length policy create_admin() enforces (line ~107) -- this
    # is the OTHER account-creation path (invite-link self-service), and
    # without this check an employee could set a 1-character password:
    # hash_password() itself only rejects an empty string, it is not a
    # policy gate.
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters.'}), 400

    conn = get_conn()
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        link = conn.execute(
            "SELECT id, email_target, expires_at, is_used FROM secure_links WHERE token_hash=?", (token_hash,)
        ).fetchone()

        if not link or link['is_used']:
            return jsonify({'error': 'Invalid or expired setup token.'}), 400
        # The read half of the pair written by `create_employee()` above, in
        # the same aware-UTC format and through the same canonical helper the
        # rest of this file already stamps `users.updated_at_utc` with. See
        # that write site for why legacy naive rows still compare correctly.
        if _accounts.now_utc_iso() > link['expires_at']:
            return jsonify({'error': 'This setup link has expired. Request a new one from your admin.'}), 400

        user = conn.execute("SELECT id FROM users WHERE email=?", (link['email_target'],)).fetchone()
        if not user:
            return jsonify({'error': 'User not found.'}), 404

        conn.execute("UPDATE users SET password_hash=?, status='active', require_password_change=0, "
                     "row_version=COALESCE(row_version, 1)+1, updated_at_utc=? WHERE id=?",
                     (hash_password(password), _accounts.now_utc_iso(), user['id']))
        # Phase 5 wave B2 stage 2b: the employee's first real password (and
        # their move out of 'pending_setup') must reach every other device --
        # identical reasoning to reset_password's own emit above.
        _accounts._queue_user_sync_event(conn, user['id'], 'update')
        conn.execute("UPDATE secure_links SET is_used=1 WHERE id=?", (link['id'],))

        try:
            conn.execute(
                "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id, new_value_json) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), 'SYSTEM', user['id'], 'EMPLOYEE_SETUP_COMPLETE', 'USER', user['id'], '{}'))
        except Exception:
            pass

        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()
