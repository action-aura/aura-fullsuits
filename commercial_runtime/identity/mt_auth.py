"""
Aura FullSuits -- Multi-Tenant Auth Middleware (Standalone Safe)

The SINGLE canonical implementation of:
  - registry-based (multi-tenant / standalone-product) authentication
    (`authenticate_registry_user`);
  - session creation (`create_session`);
  - login-attempt throttling / lockout;
  - the session-checking decorators used by every product route
    (`mt_login_required`, `mt_require_subsystem`, `require_clinic_role`).

Shared by Aura Retail and Aura Clinic. Extracted from Action Aura Enterprise's
api/mt_auth.py -- see docs/migration/dependency-map.md §1. Retargeted at
commercial_runtime.security / commercial_runtime.identity.registry_db instead
of the platform-wide core.security / database.registry_db modules.
"""
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import session, jsonify

from commercial_runtime.security.passwords import authenticate_and_maybe_upgrade
from commercial_runtime.security.audit import record as _audit, LOGIN_SUCCESS, LOGIN_FAILED, ACCOUNT_LOCKOUT, PASSWORD_HASH_UPGRADED

# ── Database path ─────────────────────────────────────────────────────────────
# Must match commercial_runtime/identity/registry_db.py so login and session
# checks read the SAME database.
_APP_DATA = os.environ.get('AURA_APP_DATA') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_DB = os.path.join(_APP_DATA, 'database', 'registry.db')

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60  # 15 minutes


def _get_registry_conn():
    conn = sqlite3.connect(REGISTRY_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_module_enabled(company_id, module_code):
    """Check if a company has a valid license for a module.

    Enforcement rule (fail-closed for provisioned tenants, soft fallback for
    un-provisioned standalone installs):

      1. If `company_modules` has ANY row at all for this company, the
         tenant has been explicitly provisioned. A missing row for the
         requested module then means "not licensed" -- FAILS CLOSED.
      2. If `company_modules` has NO rows for this company at all (the
         standalone single-tenant product's actual state), fall back to the
         local config.json license file. If config.json exists and lists the
         module, allow; if it exists and does not list the module, deny; if
         it does not exist at all (first run / dev), allow only so first-run
         setup isn't blocked before config.json is written.
    """
    try:
        conn = _get_registry_conn()
        try:
            any_row = conn.execute(
                "SELECT COUNT(*) FROM company_modules WHERE company_id=?", (company_id,)
            ).fetchone()[0]
            if any_row:
                row = conn.execute(
                    "SELECT status, enabled FROM company_modules WHERE company_id=? AND (module_code=? OR module_name=?)",
                    (company_id, module_code, module_code)
                ).fetchone()
                if not row:
                    return False  # provisioned tenant, module never enabled -> fail closed
                status = row['status'] if row['status'] else None
                if status:
                    return status in ('enabled', 'trial')
                return bool(row['enabled'])
        finally:
            conn.close()
    except Exception:
        pass  # registry DB unreadable -- fall through to the config.json check below

    return _is_module_enabled_via_local_config(module_code)


def _is_module_enabled_via_local_config(module_code) -> bool:
    """Standalone-product fallback: read this installation's own local license
    bundle (config.json, written by onboarding / seed_config on first run).
    Intentionally soft (no signature, no server call) -- see
    LICENSE-POLICY.md. It is not a security control, only a UX gate.
    """
    try:
        import json
        cfg_path = os.path.join(_APP_DATA, 'config.json')
        if not os.path.exists(cfg_path):
            return True  # no license bundle yet (first run) -- don't block setup
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        modules = cfg.get('modules')
        if not modules:
            return True  # bundle predates the modules field -- don't retroactively lock customers out
        return module_code in modules
    except Exception:
        return True  # unreadable config -- do not block first-run/offline setup


def create_session(user: dict) -> None:
    """The one place every registry-based login sets session state, so every
    caller ends up with the same minimum required fields."""
    session.clear()
    session['mt_user_id'] = user['id']
    session['company_id'] = user['company_id']
    session['employee_id'] = user['employee_id']
    session['mt_role'] = user['role']
    session['clinic_role'] = user.get('clinic_role') or ''
    session['mt_session_version'] = user.get('session_version', 1)
    session['require_password_change'] = bool(user.get('require_password_change'))
    session['email'] = user['email']
    session.permanent = True


def authenticate_registry_user(email: str, password: str) -> dict:
    """THE single canonical multi-tenant / standalone-product login check.

    Returns {'ok': bool, 'user': dict|None, 'error': str|None, 'locked': bool}.
    Handles: case-insensitive email lookup, legacy-SHA256-to-modern-hash
    migration (transparent, one time), login-attempt throttling/lockout, and
    disabled-account rejection.
    """
    email = (email or '').strip().lower()
    password = password or ''
    result = {'ok': False, 'user': None, 'error': 'Invalid email or password.', 'locked': False}
    if not email or not password:
        return result

    conn = _get_registry_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE LOWER(email)=?", (email,)).fetchone()
        if not row:
            _audit(None, None, LOGIN_FAILED, context={'email': email, 'reason': 'no_such_user'})
            return result

        user = dict(row)
        result['user'] = user
        locked_until = user.get('locked_until')
        if locked_until:
            try:
                still_locked = datetime.fromisoformat(locked_until) > datetime.now(timezone.utc)
            except Exception:
                still_locked = False
            if still_locked:
                _audit(user['company_id'], user['id'], LOGIN_FAILED,
                       context={'email': email, 'reason': 'locked_out'})
                result['error'] = 'Too many failed attempts. Please try again later.'
                result['locked'] = True
                return result

        ok, new_hash = authenticate_and_maybe_upgrade(password, user.get('password_hash') or '')

        if not ok:
            failed = int(user.get('failed_login_count') or 0) + 1
            if failed >= MAX_FAILED_ATTEMPTS:
                locked_until_ts = (datetime.now(timezone.utc) + timedelta(seconds=LOCKOUT_SECONDS)).isoformat()
                conn.execute(
                    "UPDATE users SET failed_login_count=?, locked_until=? WHERE id=?",
                    (failed, locked_until_ts, user['id'])
                )
                conn.commit()
                _audit(user['company_id'], user['id'], ACCOUNT_LOCKOUT,
                       context={'email': email, 'failed_attempts': failed})
            else:
                conn.execute("UPDATE users SET failed_login_count=? WHERE id=?", (failed, user['id']))
                conn.commit()
                _audit(user['company_id'], user['id'], LOGIN_FAILED,
                       context={'email': email, 'reason': 'bad_password', 'failed_attempts': failed})
            return result

        conn.execute(
            "UPDATE users SET failed_login_count=0, locked_until=NULL WHERE id=?", (user['id'],)
        )
        if new_hash:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (new_hash, user['id']))
            _audit(user['company_id'], user['id'], PASSWORD_HASH_UPGRADED,
                   context={'email': email})
        conn.commit()

        if user.get('status') == 'disabled':
            _audit(user['company_id'], user['id'], LOGIN_FAILED,
                   context={'email': email, 'reason': 'account_disabled'})
            result['error'] = 'Account is disabled by your Administrator.'
            return result

        _audit(user['company_id'], user['id'], LOGIN_SUCCESS, context={'email': email})
        result['ok'] = True
        result['error'] = None
        return result
    finally:
        conn.close()


def mt_login_required(f):
    """Require a valid multi-tenant session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('is_demo_mode'):
            return f(*args, **kwargs)

        if 'mt_user_id' not in session:
            return jsonify({'error': 'Authentication required', 'code': 401}), 401

        try:
            conn = _get_registry_conn()
            row = conn.execute(
                "SELECT status FROM users WHERE id=?",
                (session['mt_user_id'],)
            ).fetchone()
            conn.close()
            if not row:
                session.clear()
                return jsonify({'error': 'Session expired or revoked. Please log in again.', 'code': 401}), 401
            if row['status'] == 'disabled':
                session.clear()
                return jsonify({'error': 'Your account has been disabled. Contact your administrator.', 'code': 401}), 401
        except Exception:
            pass  # Fail-open only for a transient local-SQLite read error, not for a missing account.

        return f(*args, **kwargs)
    return decorated


def require_clinic_role(*allowed_roles):
    """Gate a clinic endpoint to specific clinic roles (e.g. 'doctor'). The
    global company admin (role='admin') and an active demo session always
    pass. Fail-closed for clinic staff, fail-open only when the DB is
    unreachable."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get('is_demo_mode'):
                return f(*args, **kwargs)
            if session.get('mt_role') == 'admin':
                return f(*args, **kwargs)

            clinic_role = session.get('clinic_role')
            if clinic_role is None:
                try:
                    conn = _get_registry_conn()
                    row = conn.execute(
                        "SELECT clinic_role FROM users WHERE id=?",
                        (session.get('mt_user_id'),)
                    ).fetchone()
                    conn.close()
                    clinic_role = (row['clinic_role'] if row else '') or ''
                except Exception:
                    return f(*args, **kwargs)  # fail-open only if DB unreachable

            if clinic_role in allowed_roles:
                return f(*args, **kwargs)
            return jsonify({
                'error': 'This area is restricted to: ' + ', '.join(allowed_roles) + '.',
                'code': 403
            }), 403
        return decorated
    return decorator


def mt_require_subsystem(subsystem):
    """Require a valid license AND employee permission for a specific subsystem."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get('is_demo_mode'):
                return f(*args, **kwargs)

            company_id = session.get('company_id')
            user_id    = session.get('mt_user_id')
            role       = session.get('mt_role')

            if not company_id:
                return jsonify({'error': 'Tenant context missing.'}), 403

            if not _is_module_enabled(company_id, subsystem):
                return jsonify({'error': f'License for {subsystem} is missing or expired.', 'code': 402}), 402

            if role == 'admin':
                return f(*args, **kwargs)

            try:
                conn = _get_registry_conn()
                row = conn.execute(
                    "SELECT access_level FROM user_permissions WHERE user_id=? AND subsystem=?",
                    (user_id, subsystem)
                ).fetchone()
                conn.close()
                if not row or row['access_level'] == 'none':
                    return jsonify({'error': f'Access denied to {subsystem}. Contact your Admin.', 'code': 403}), 403
            except Exception:
                pass  # Fail-open only for a transient local-SQLite read error.

            return f(*args, **kwargs)
        return decorated
    return decorator
