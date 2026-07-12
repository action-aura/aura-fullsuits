"""
Aura FullSuits -- shared login/logout/active-modules routes.

Trimmed extraction of Action Aura Enterprise's api/auth.py: keeps only the
registry-based (multi-tenant / standalone-product) login path used by Retail
and Clinic. Drops the legacy per-domain demo login (banking/healthcare/
education/manufacturing industries-demo verticals -- out of scope, see
docs/migration/source-inventory.md "Two different things both match
clinic/healthcare") and the SaaS company-registration/invite-link flows
(deferred -- not exercised by the ported test suite, revisit if a product
needs self-service tenant registration).

This is THE single login endpoint for a product build -- both the Windows
desktop entrypoint and the Android app hit this exact route.
"""
import os
from flask import Blueprint, request, jsonify, session

from commercial_runtime.identity.mt_auth import authenticate_registry_user, create_session

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    email = data.get('email', '').strip() or username
    password = data.get('password', '').strip()

    if not password or not email:
        return jsonify({'error': 'Email and password are required'}), 400

    mt_result = authenticate_registry_user(email, password)
    if not mt_result['user']:
        return jsonify({'error': mt_result['error'] or 'Invalid email or password.'}), 401

    user = mt_result['user']
    if not mt_result['ok']:
        status = 403 if 'disabled' in (mt_result['error'] or '') else (429 if mt_result['locked'] else 401)
        return jsonify({'error': mt_result['error']}), status

    create_session(user)
    return jsonify({
        'success': True,
        'is_mt': True,
        'require_password_change': session['require_password_change'],
        'user': {
            'id': user['id'],
            'employee_id': user['employee_id'],
            'email': user['email'],
            'role': user['role'],
            'clinic_role': session['clinic_role'],
        }
    })


@auth_bp.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})


@auth_bp.route('/api/auth/language', methods=['POST'])
def set_language():
    """Persist the logged-in user's UI language (en/ar) to their account.

    Extracted from api/standalone_auth.py's `set_language` (the file whose
    own docstring says it is "what the shipped Retail/Clinic standalone
    products actually run"). The rest of that 698-line file (onboarding
    wizard, employee management) was ported in Phase 3 -- see
    commercial_runtime/identity/onboarding_routes.py."""
    from commercial_runtime.identity.registry_db import get_conn
    data = request.get_json(silent=True) or {}
    lang = data.get('language', 'en')
    if lang not in ('en', 'ar'):
        lang = 'en'
    uid = session.get('mt_user_id')
    if not uid:
        # Pre-login: the client's localStorage already holds the choice.
        return jsonify({'success': True, 'language': lang})
    try:
        conn = get_conn()
        conn.execute("UPDATE users SET language=? WHERE id=?", (lang, uid))
        conn.commit()
        conn.close()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, 'language': lang})


@auth_bp.route('/api/auth/active-modules', methods=['GET'])
def get_active_modules():
    """Return the list of modules this installation is licensed for. In a
    standalone (customer) bundle, read directly from the bundle's config.json
    -- see commercial_runtime/identity/mt_auth.py's `_is_module_enabled_via_local_config`
    for the same soft-license source used to gate individual requests."""
    import json as _j
    import sys as _sys

    app_data = os.environ.get('AURA_APP_DATA') or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        cfg_path = os.path.join(app_data, 'config.json')
        if os.path.exists(cfg_path):
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = _j.load(f)
            if getattr(_sys, 'frozen', False) or cfg.get('setup_complete') or cfg.get('IS_STANDALONE'):
                mods = cfg.get('modules', [])
                if mods:
                    return jsonify({'modules': mods, 'labels': mods})
    except Exception:
        pass

    return jsonify({'modules': ['all']})
