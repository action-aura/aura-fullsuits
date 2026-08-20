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
import os
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify, session

from commercial_runtime.identity.registry_db import get_conn
from commercial_runtime.identity.mt_auth import create_session, mt_login_required
from commercial_runtime.identity import user_accounts as _accounts
from commercial_runtime.identity import verification as _verification
from commercial_runtime.security.passwords import hash_password
from commercial_runtime.security.audit import record as _security_audit, ADMIN_CREATED

onboarding_bp = Blueprint('onboarding', __name__)


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
    timezone      = data.get('timezone', 'UTC').strip()
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
            conn.execute("DELETE FROM user_permissions WHERE user_id=?", (existing_admin['id'],))
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

        # Admin still bypasses the permission lookup at mt_auth.py:287, so
        # these rows change no decision today; they exist so the owner's own
        # account shows up in the capability grid the employee screens read,
        # instead of appearing as an account with no permissions at all.
        _accounts.seed_capabilities_for_user(conn, user_id, _accounts.ROLE_ADMIN)

        # Kept inside the same transaction as the user insert -- either both
        # land or neither does, so a mid-onboarding failure can never leave
        # an admin account with no matching company_settings row.
        conn.execute("""
            INSERT OR REPLACE INTO company_settings
              (id, company_id, country, timezone, currency, business_type, language)
            VALUES (?,?,?,?,?,?,?)
        """, (str(uuid.uuid4()), company_id, country, timezone, currency, business_type, language))

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
            'timezone':            timezone,
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
    """
    if 'mt_user_id' in session:
        lang = 'en'
        capabilities = []
        try:
            conn = get_conn()
            try:
                row = conn.execute(
                    "SELECT language FROM users WHERE id=?", (session['mt_user_id'],)
                ).fetchone()
                if row and row['language']:
                    lang = row['language']
                capabilities = _capabilities_for_session(conn, session['mt_user_id'], session.get('mt_role'))
            finally:
                conn.close()
        except Exception:
            pass
        return jsonify({
            'authenticated': True,
            'is_mt': True,
            'language': lang,
            # Rendering ADVICE ONLY -- see _capabilities_for_session's
            # docstring. Every route still enforces its own server-side gate.
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
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    conn = get_conn()
    try:
        # `pin_hash IS NOT NULL` -- the PRESENCE of a PIN, never the hash. The
        # column is a PBKDF2 digest of a four-digit secret, so a keyspace of
        # 10,000 is small enough that handing the hash to any client at all is
        # handing them the PIN; only the boolean is anyone's business.
        emps = conn.execute(
            "SELECT id, employee_id, email, role, clinic_role, status, created_at, "
            "       (pin_hash IS NOT NULL) AS has_pin "
            "FROM users WHERE company_id=?",
            (session['company_id'],)
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
            rows.append(row)
        return jsonify({'success': True, 'employees': rows})
    finally:
        conn.close()


@onboarding_bp.route('/api/admin/employees', methods=['POST'])
@mt_login_required
def create_employee():
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin only'}), 403
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

    conn = get_conn()
    try:
        existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            return jsonify({'error': 'Email already registered.'}), 400

        company_id = session.get('company_id', 'local')
        user_id = str(uuid.uuid4())
        count = conn.execute("SELECT COUNT(*) FROM users WHERE company_id=?", (company_id,)).fetchone()[0]
        emp_id = f"EMP-{count + 1:04d}"

        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status,
                               require_password_change, clinic_role, uid, row_version, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, 'pending_setup', 0, ?, ?, 1, ?)
        """, (user_id, company_id, emp_id, email, 'PENDING', role, clinic_role,
              str(uuid.uuid4()), _accounts.now_utc_iso()))

        for sub, lvl in perms.items():
            if lvl != 'none':
                cur.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?, ?, ?, ?)",
                            (str(uuid.uuid4()), user_id, sub, lvl))

        # AFTER the caller's explicit grants, never before: seeding is
        # INSERT OR IGNORE against UNIQUE(user_id, subsystem), so running it
        # second means an explicit grant for the same code wins and the seed
        # only fills the gaps. Running it first would make the caller's own
        # request collide with the defaults.
        _accounts.seed_capabilities_for_user(conn, user_id, role)

        raw_token = uuid.uuid4().hex
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = (datetime.utcnow() + timedelta(days=7)).isoformat()
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
def update_status(user_id):
    """Enable/disable toggle -- had none of `update_role`'s guards: no 404 for
    an unknown id, no validation of the status string (accepted and stored
    `status='banana'` verbatim), and no owner bar.

    Disabling the sole admin is UNRECOVERABLE: the disabled admin still has a
    real `password_hash`, so `onboarding_status` keeps answering
    `needs_setup=false` and `create-admin` keeps answering 409, while
    `authenticate_registry_user` refuses the login -- and every route that
    could undo it sits behind the admin session nobody can obtain any more.
    """
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin only'}), 403

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
        row = conn.execute(
            "SELECT id, role FROM users WHERE id=? AND company_id=?",
            (user_id, session['company_id']),
        ).fetchone()
        if not row:
            return jsonify({'error': 'User not found.'}), 404

        # One admin per install (`create_admin` is gated on "no valid admin
        # exists yet") and no way to mint a second -- refuse only the
        # DISABLE direction; re-affirming an already-active owner as
        # 'active' is a harmless no-op and stays allowed.
        if status == 'disabled' and _accounts.normalize_role(row['role']) == _accounts.ROLE_ADMIN:
            return jsonify({'error': 'You cannot disable the owner account.'}), 409

        conn.execute("UPDATE users SET status=?, session_version=session_version+1, "
                     "row_version=COALESCE(row_version, 1)+1, updated_at_utc=? "
                     "WHERE id=? AND company_id=?",
                     (status, _accounts.now_utc_iso(), user_id, session['company_id']))
        conn.execute(
            "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id, new_value_json) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), session['company_id'], session['mt_user_id'], 'UPDATE_STATUS', 'USER', str(user_id), json.dumps({'status': status})))
        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()


@onboarding_bp.route('/api/admin/employees/<string:user_id>/role', methods=['PUT'])
@mt_login_required
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
        _accounts.seed_capabilities_for_user(conn, user_id, role)

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
    """
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin only'}), 403

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE id=? AND company_id=?",
            (user_id, session['company_id']),
        ).fetchone()
        if not row:
            return jsonify({'error': 'User not found.'}), 404

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
        conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?, ?, ?, ?)",
                     (str(uuid.uuid4()), user_id, sub, lvl))
        conn.execute("UPDATE users SET session_version=session_version+1, "
                     "row_version=COALESCE(row_version, 1)+1, updated_at_utc=? WHERE id=? AND company_id=?",
                     (_accounts.now_utc_iso(), user_id, session['company_id']))
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
        today = datetime.utcnow().strftime('%Y-%m-%d')
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
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    conn = get_conn()
    try:
        if request.method == 'GET':
            row = conn.execute(
                "SELECT * FROM company_settings WHERE company_id=?", (session['company_id'],)
            ).fetchone()
            return jsonify({'success': True, 'settings': dict(row) if row else {}})
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
        if datetime.utcnow().isoformat() > link['expires_at']:
            return jsonify({'error': 'This setup link has expired. Request a new one from your admin.'}), 400

        user = conn.execute("SELECT id FROM users WHERE email=?", (link['email_target'],)).fetchone()
        if not user:
            return jsonify({'error': 'User not found.'}), 404

        conn.execute("UPDATE users SET password_hash=?, status='active', require_password_change=0, "
                     "row_version=COALESCE(row_version, 1)+1, updated_at_utc=? WHERE id=?",
                     (hash_password(password), _accounts.now_utc_iso(), user['id']))
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
