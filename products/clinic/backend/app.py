"""
Aura Clinic -- standalone Flask application entrypoint.

New file (not extracted) -- same rationale as products/retail/backend/app.py.
Registers the shared auth/onboarding blueprints plus clinic_bp. Includes the
sys.frozen / sys._MEIPASS path-resolution fix from the start (Retail's
Phase 2B packaged-smoke-test found this bug and fixed it after the fact --
Clinic's app.py is written correctly from day one).
"""
import os
import re
import sys
from datetime import timedelta
from pathlib import Path

if getattr(sys, 'frozen', False):
    SUITE_ROOT = Path(sys._MEIPASS)
    BACKEND_DIR = SUITE_ROOT / 'products' / 'clinic' / 'backend'
else:
    BACKEND_DIR = Path(__file__).resolve().parent
    SUITE_ROOT = BACKEND_DIR.parent.parent.parent
PRODUCT_DIR = BACKEND_DIR.parent

for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from flask import Flask, jsonify
from flask_cors import CORS

from config import (
    SECRET_KEY, DATABASE_DIR, APP_VERSION,
    OWNER_LICENSING_BASE_URL, OWNER_LICENSING_VERIFY_TLS, OWNER_LICENSING_TIMEOUT_SECONDS,
    LICENSING_TRUST_ANCHOR_PATH, LICENSING_PLATFORM, LICENSING_INTERNAL_SHARED_SECRET,
)

app = Flask(__name__, static_folder=str(PRODUCT_DIR / 'frontend'), static_url_path='/static')

app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(hours=12)

app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

CORS(app, supports_credentials=True,
     origins=re.compile(r'^https?://(127\.0\.0\.1|localhost)(:\d+)?$'))


@app.after_request
def _no_cache(response):
    if 'Cache-Control' not in response.headers:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '-1'
    return response


@app.route('/api/health', methods=['GET'])
def _health():
    """Unauthenticated local liveness/readiness probe (Phase 3.7, launcher
    corrective wave). Mirrors products/retail/backend/app.py's _health() --
    see that function's docstring and
    docs/corrections/launcher/root-cause-analysis.md for why this exists."""
    return jsonify({'status': 'ok'}), 200


@app.route('/api/version', methods=['GET'])
def _version():
    """Release metadata for the About screen / release-manifest tooling
    (Wave 1B). Separate from /api/health on purpose -- see that route's
    docstring for why its contract is frozen."""
    from config import SCHEMA_VERSION, PRODUCT_CODE
    return jsonify({
        'product_code': PRODUCT_CODE,
        'product_name': 'Aura Clinic',
        'app_version': APP_VERSION,
        'schema_version': SCHEMA_VERSION,
    }), 200


from commercial_runtime.identity.auth_routes import auth_bp
from commercial_runtime.identity.onboarding_routes import onboarding_bp
from commercial_runtime.identity.registry_db import init_registry_db
from database.schema import init_clinic
from api.clinic_api import clinic_bp
from commercial_runtime.backup.routes import make_backup_blueprint
from commercial_runtime.licensing_contracts.device_identity import WindowsDpapiDeviceIdentityProvider
from commercial_runtime.licensing_contracts.android_bridge_identity import AndroidBridgeDeviceIdentityProvider
from commercial_runtime.licensing_contracts.routes import make_licensing_blueprint

app.register_blueprint(auth_bp)
app.register_blueprint(onboarding_bp)
app.register_blueprint(clinic_bp)
app.register_blueprint(make_backup_blueprint('clinic', DATABASE_DIR, APP_VERSION))

# Part H: Android's main.py sets AURA_PLATFORM='ANDROID' before importing this
# module -- Windows (unset, defaults to 'WINDOWS') keeps making its own signed
# Owner calls directly via /activate|/check-in|/deactivate; Android instead
# relies on Kotlin (which holds the real device key) to call Owner and hand
# the raw response to the /_internal/sync-* routes below for independent
# verification (Part U). internal_shared_secret is only ever set on Android.
if LICENSING_PLATFORM == 'ANDROID':
    _licensing_device_identity_factory = AndroidBridgeDeviceIdentityProvider
else:
    _licensing_device_identity_factory = WindowsDpapiDeviceIdentityProvider

app.register_blueprint(make_licensing_blueprint(
    product_code='AURA_CLINIC',
    platform=LICENSING_PLATFORM,
    app_version=APP_VERSION,
    app_data_dir=str(Path(DATABASE_DIR).parent),
    owner_base_url=OWNER_LICENSING_BASE_URL,
    verify_tls=OWNER_LICENSING_VERIFY_TLS,
    timeout_seconds=OWNER_LICENSING_TIMEOUT_SECONDS,
    trust_anchor_path=Path(LICENSING_TRUST_ANCHOR_PATH),
    device_identity_factory=_licensing_device_identity_factory,
    internal_shared_secret=LICENSING_INTERNAL_SHARED_SECRET,
))


def init_app():
    """Initialize the registry + clinic schema. Call once before serving."""
    init_registry_db()
    init_clinic()
    return app


if __name__ == '__main__':
    init_app()
    port = int(os.environ.get('PORT', 5000))
    try:
        from waitress import serve as _serve
        _serve(app, host='127.0.0.1', port=port, threads=12,
               channel_timeout=120, connection_limit=200, _quiet=True)
    except ImportError:
        app.run(host='127.0.0.1', port=port, debug=False)
