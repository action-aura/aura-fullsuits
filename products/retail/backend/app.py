"""
Aura Retail -- standalone Flask application entrypoint.

New file (not extracted) -- the source monolith's bootstrap (aura_core.py +
app.py) brings up EVERY subsystem's blueprints in one Flask app; a standalone
Retail product needs a much smaller equivalent that boots only Retail's own
routes plus the shared auth/identity layer. Patterned after the source
monolith's app.py for the pieces that must behave identically (secret key
handling, session cookie hardening, CORS-to-loopback-only) -- see
docs/migration/retail-extraction-report.md.
"""
import os
import re
import sys
from datetime import timedelta
from pathlib import Path

# Path resolution must not rely on Path(__file__) alone: in a PyInstaller
# build this module is compiled into the bundled archive (not collected as a
# loose file), so __file__ does not reliably resolve to a real directory on
# disk -- it broke static-file serving (frontend/locales/*, i18n.js all 404'd)
# until this was caught by an actual packaged-exe smoke test. sys._MEIPASS
# is PyInstaller's own answer to "where are my bundled files," and matches
# the same frozen-detection pattern already used by config.py (BASE_DIR).
if getattr(sys, 'frozen', False):
    SUITE_ROOT = Path(sys._MEIPASS)
    BACKEND_DIR = SUITE_ROOT / 'products' / 'retail' / 'backend'
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

# Per-installation random secret key (commercial_runtime/security/app_secret.py)
# -- never a hardcoded literal shared by every install. See SECURITY.md.
app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(hours=12)

# Session cookie hardening. `Secure` is intentionally left at its HTTP-compatible
# default -- this app is only ever reached over http://127.0.0.1:<port> (no TLS
# to anchor a Secure cookie to; see products/retail/desktop/launcher_retail.py).
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# CORS restricted to loopback origins only -- the web UI this app serves is
# always same-origin, and Android's Retrofit client isn't subject to CORS at all.
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
    corrective wave). Reveals no business data, no auth state, no
    filesystem paths -- deliberately the only thing the desktop launcher's
    startup readiness check is allowed to depend on. Do not gate this
    behind mt_login_required; the launcher polls it before any session
    could exist. See docs/corrections/launcher/root-cause-analysis.md for
    why the previous readiness check (bare GET on "/", which no route ever
    served) always 404'd and falsely triggered the startup watchdog."""
    return jsonify({'status': 'ok'}), 200


@app.route('/api/version', methods=['GET'])
def _version():
    """Release metadata for the About screen / release-manifest tooling
    (Wave 1B). Separate from /api/health on purpose -- that route's
    contract is frozen for the launcher's readiness probe (see its
    docstring); this one is free to grow."""
    from config import SCHEMA_VERSION, CALCULATION_VERSION, PRODUCT_CODE
    return jsonify({
        'product_code': PRODUCT_CODE,
        'product_name': 'Aura Retail',
        'app_version': APP_VERSION,
        'schema_version': SCHEMA_VERSION,
        'calculation_version': CALCULATION_VERSION,
    }), 200


from commercial_runtime.identity.auth_routes import auth_bp
from commercial_runtime.identity.onboarding_routes import onboarding_bp
from commercial_runtime.identity.registry_db import init_registry_db
from database.schema import get_retail_conn, init_retail
from api.retail_api import retail_bp
from api.import_api import import_bp
from commercial_runtime.backup.routes import make_backup_blueprint
from commercial_runtime.licensing_contracts.routes import make_licensing_blueprint
from commercial_runtime.einvoicing.routes import make_einvoicing_blueprint
from commercial_runtime.einvoicing.worker import OutboxWorker
from commercial_runtime.einvoicing.providers.mock import MockProvider
from commercial_runtime.einvoicing import settings as _einvoicing_settings
from core.retail import einvoice_adapter as _einvoice_adapter

app.register_blueprint(auth_bp)
app.register_blueprint(onboarding_bp)
app.register_blueprint(retail_bp)
app.register_blueprint(import_bp)
app.register_blueprint(make_backup_blueprint('retail', DATABASE_DIR, APP_VERSION))

# docs/einvoicing/phase1/ -- Jordan JoFotara e-invoicing. Default OFF (see
# commercial_runtime/einvoicing/settings.py's DEFAULTS); nothing here
# changes behavior for an install that never turns it on.
#
# This registry's DB is genuinely multi-tenant (commercial_runtime/identity/
# registry_db.py -- one install CAN host more than one company), so there is
# no single fixed company_id to build one OutboxWorker for at import time.
# Workers are created lazily, one per company, the first time that
# company's settings are touched or its background job is resumed at boot.
_EINVOICING_APP_DATA_DIR = str(Path(DATABASE_DIR).parent)
_einvoicing_provider = MockProvider()  # Phase 1 default -- see providers/direct_istd.py
_einvoicing_workers = {}


def _get_or_create_einvoicing_worker(company_id):
    if company_id not in _einvoicing_workers:
        _einvoicing_workers[company_id] = OutboxWorker(
            conn_factory=get_retail_conn,
            app_data_dir=_EINVOICING_APP_DATA_DIR,
            company_id=company_id,
            provider=_einvoicing_provider,
            document_builder=_einvoice_adapter.build_document,
            reconcile_fn=_einvoice_adapter.reconcile_missing_sales,
        )
    return _einvoicing_workers[company_id]


app.register_blueprint(make_einvoicing_blueprint(
    product_code='AURA_RETAIL',
    platform=LICENSING_PLATFORM,
    app_data_dir=_EINVOICING_APP_DATA_DIR,
    conn_factory=get_retail_conn,
    get_worker=_get_or_create_einvoicing_worker,
    provider=_einvoicing_provider,
))

# Part H: see products/clinic/backend/app.py's identical block for the full
# rationale. Lazy-imported (Phase 7V-F): WindowsDpapiDeviceIdentityProvider
# pulls in the Windows-only `cryptography` package; an eager top-level
# import crashed the whole app.py import on Android with
# ModuleNotFoundError('cryptography'), found via physical-device testing.
if LICENSING_PLATFORM == 'ANDROID':
    from commercial_runtime.licensing_contracts.android_bridge_identity import AndroidBridgeDeviceIdentityProvider
    _licensing_device_identity_factory = AndroidBridgeDeviceIdentityProvider
else:
    from commercial_runtime.licensing_contracts.device_identity import WindowsDpapiDeviceIdentityProvider
    _licensing_device_identity_factory = WindowsDpapiDeviceIdentityProvider

app.register_blueprint(make_licensing_blueprint(
    product_code='AURA_RETAIL',
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
    """Initialize the registry + retail schema. Call once before serving."""
    init_registry_db()
    init_retail()
    _resume_einvoicing_workers()
    return app


def _resume_einvoicing_workers():
    """A company that had e-invoicing enabled before a restart (PC reboot,
    app update) must keep submitting without waiting for a settings write
    to notice -- sweep for companies with the feature already on and
    resume their background workers. A fresh/never-enabled install finds
    zero rows here and starts zero threads."""
    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT company_id FROM einvoice_settings WHERE skey='enabled' AND svalue='1'"
        ).fetchall()
        for row in rows:
            cid = row[0]
            interval = int(_einvoicing_settings.get_setting(conn, cid, 'submit_interval_seconds'))
            _get_or_create_einvoicing_worker(cid).start(interval_seconds=interval)
    finally:
        conn.close()


if __name__ == '__main__':
    init_app()
    port = int(os.environ.get('PORT', 5000))
    try:
        from waitress import serve as _serve
        _serve(app, host='127.0.0.1', port=port, threads=12,
               channel_timeout=120, connection_limit=200, _quiet=True)
    except ImportError:
        app.run(host='127.0.0.1', port=port, debug=False)
