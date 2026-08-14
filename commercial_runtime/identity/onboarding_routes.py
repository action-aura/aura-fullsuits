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
from commercial_runtime.identity.mt_auth import create_session
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
            conn.execute("DELETE FROM users WHERE role='admin'")

        company_id = cfg.get('company_id') or hashlib.md5(email.encode()).hexdigest()
        user_id    = str(uuid.uuid4())
        pwd_hash   = hash_password(password)

        conn.execute("""
            INSERT INTO users
              (id, company_id, employee_id, email, password_hash, role, status, require_password_change)
            VALUES (?, ?, 'ADMIN-0001', ?, ?, 'admin', 'active', 0)
        """, (user_id, company_id, email, pwd_hash))

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

        return jsonify({
            'success': True,
            'user': {'id': user_id, 'email': email, 'role': 'admin', 'company': company}
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


@onboarding_bp.route('/api/onboarding/complete', methods=['POST'])
def complete_onboarding():
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin session required.'}), 403
    _write_config({'setup_complete': True})
    return jsonify({'success': True})


@onboarding_bp.route('/api/auth/session', methods=['GET'])
def get_session():
    if 'mt_user_id' in session:
        lang = 'en'
        try:
            conn = get_conn()
            row = conn.execute("SELECT language FROM users WHERE id=?", (session['mt_user_id'],)).fetchone()
            conn.close()
            if row and row['language']:
                lang = row['language']
        except Exception:
            pass
        return jsonify({
            'authenticated': True,
            'is_mt': True,
            'language': lang,
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
def get_employees():
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    conn = get_conn()
    try:
        emps = conn.execute(
            "SELECT id, employee_id, email, role, clinic_role, status, created_at FROM users WHERE company_id=?",
            (session['company_id'],)
        ).fetchall()
        return jsonify({'success': True, 'employees': [dict(e) for e in emps]})
    finally:
        conn.close()


@onboarding_bp.route('/api/admin/employees', methods=['POST'])
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
            INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change, clinic_role)
            VALUES (?, ?, ?, ?, ?, 'employee', 'pending_setup', 0, ?)
        """, (user_id, company_id, emp_id, email, 'PENDING', clinic_role))

        for sub, lvl in perms.items():
            if lvl != 'none':
                cur.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?, ?, ?, ?)",
                            (str(uuid.uuid4()), user_id, sub, lvl))

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
def update_status(user_id):
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    conn = get_conn()
    try:
        status = request.json.get('status')
        conn.execute("UPDATE users SET status=?, session_version=session_version+1 WHERE id=? AND company_id=?",
                     (status, user_id, session['company_id']))
        conn.execute(
            "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id, new_value_json) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), session['company_id'], session['mt_user_id'], 'UPDATE_STATUS', 'USER', str(user_id), json.dumps({'status': status})))
        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()


@onboarding_bp.route('/api/admin/employees/<string:user_id>/clinic-role', methods=['PUT'])
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
            "UPDATE users SET clinic_role=?, session_version=session_version+1 WHERE id=? AND company_id=?",
            (clinic_role, user_id, session['company_id'])
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


@onboarding_bp.route('/api/admin/employees/<string:user_id>/permissions', methods=['POST'])
def update_perms(user_id):
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    conn = get_conn()
    try:
        sub = request.json.get('subsystem')
        lvl = request.json.get('access_level')
        conn.execute("DELETE FROM user_permissions WHERE user_id=? AND subsystem=?", (user_id, sub))
        conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?, ?, ?, ?)",
                     (str(uuid.uuid4()), user_id, sub, lvl))
        conn.execute("UPDATE users SET session_version=session_version+1 WHERE id=?", (user_id,))
        conn.execute(
            "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id, new_value_json) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), session['company_id'], session['mt_user_id'], 'UPDATE_PERM', 'USER_PERMISSION', str(user_id), json.dumps({sub: lvl})))
        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()


@onboarding_bp.route('/api/admin/audit', methods=['GET'])
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

        conn.execute("UPDATE users SET password_hash=?, status='active', require_password_change=0 WHERE id=?",
                     (hash_password(password), user['id']))
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
