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
    docstring for why its contract is frozen.

    `schema_version` IS NOT THE DATABASE SCHEMA VERSION, despite the name --
    same trap as Retail's identical endpoint, and the same reasoning applies
    verbatim. It is `commercial_runtime.backup.service.SCHEMA_VERSION`, the
    BACKUP ARCHIVE FORMAT version, which is 1 for every install ever shipped.
    The database's own level is `PRAGMA user_version`, maintained by
    `ensure_schema_version()`. See products/retail/backend/app.py's `_version`
    for the measurement and for why this is documented rather than renamed."""
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
from database.schema import get_clinic_conn, init_clinic
from api.clinic_api import clinic_bp
from commercial_runtime.backup.routes import make_backup_blueprint
from commercial_runtime.licensing_contracts.routes import make_licensing_blueprint
from commercial_runtime.identity.device_routes import device_bp
from commercial_runtime.einvoicing.routes import make_einvoicing_blueprint
from commercial_runtime.einvoicing.worker import OutboxWorker
from commercial_runtime.einvoicing.providers.mock import MockProvider
from commercial_runtime.einvoicing.providers.unconfigured import UnconfiguredProvider
from commercial_runtime.einvoicing import settings as _einvoicing_settings
from core.clinic import einvoice_adapter as _einvoice_adapter

app.register_blueprint(auth_bp)
app.register_blueprint(onboarding_bp)
app.register_blueprint(clinic_bp)
app.register_blueprint(make_backup_blueprint('clinic', DATABASE_DIR, APP_VERSION))
app.register_blueprint(device_bp)

# docs/einvoicing/phase1/ -- Jordan JoFotara e-invoicing. Mirrors
# products/retail/backend/app.py's identical block -- see that file's
# comments for the full rationale (default OFF, per-company worker
# registry, why one fixed company_id at import time would be wrong).
_EINVOICING_APP_DATA_DIR = str(Path(DATABASE_DIR).parent)
# E-invoicing now defaults ENABLED -- see products/retail/backend/app.py's
# identical block for the full rationale (Jordanian mandate, why MockProvider
# reporting CLEARED without contacting JoFotara would be a false compliance
# claim on a real receipt, why DirectISTDProvider still isn't real until
# Phase 2). Shipped default is UnconfiguredProvider: documents enqueue and
# wait, nothing submits, nothing claims clearance. AURA_EINVOICING_ALLOW_MOCK=1
# opts back into MockProvider for development/tests that need a real round trip.
_einvoicing_provider = (
    MockProvider() if os.environ.get('AURA_EINVOICING_ALLOW_MOCK') == '1' else UnconfiguredProvider()
)
_einvoicing_workers = {}


class _IdempotentEinvoicingWorkerHandle:
    """Mirrors products/retail/backend/app.py's identical class -- see that
    file's docstring for the full rationale (OutboxWorker.start() is not
    idempotent by itself, deliberately mirroring LicenseCheckInScheduler,
    so routes.py's _post_settings() calling .start() on every enabled
    write needs a call-site guard, not a change to OutboxWorker)."""

    def __init__(self, worker):
        self._worker = worker
        self._running = False

    def start(self, interval_seconds):
        if self._running:
            return
        self._worker.start(interval_seconds=interval_seconds)
        self._running = True

    def stop(self):
        self._worker.stop()
        self._running = False

    def run_once(self):
        return self._worker.run_once()


def _get_or_create_einvoicing_worker(company_id):
    if company_id not in _einvoicing_workers:
        _einvoicing_workers[company_id] = _IdempotentEinvoicingWorkerHandle(OutboxWorker(
            conn_factory=get_clinic_conn,
            app_data_dir=_EINVOICING_APP_DATA_DIR,
            company_id=company_id,
            provider=_einvoicing_provider,
            document_builder=_einvoice_adapter.build_document,
            reconcile_fn=_einvoice_adapter.reconcile_missing_invoices,
        ))
    return _einvoicing_workers[company_id]


app.register_blueprint(make_einvoicing_blueprint(
    product_code='AURA_CLINIC',
    platform=LICENSING_PLATFORM,
    app_data_dir=_EINVOICING_APP_DATA_DIR,
    conn_factory=get_clinic_conn,
    get_worker=_get_or_create_einvoicing_worker,
    provider=_einvoicing_provider,
))

# Part H: Android's main.py sets AURA_PLATFORM='ANDROID' before importing this
# module -- Windows (unset, defaults to 'WINDOWS') keeps making its own signed
# Owner calls directly via /activate|/check-in|/deactivate; Android instead
# relies on Kotlin (which holds the real device key) to call Owner and hand
# the raw response to the /_internal/sync-* routes below for independent
# verification (Part U). internal_shared_secret is only ever set on Android.
if LICENSING_PLATFORM == 'ANDROID':
    # Lazy import: WindowsDpapiDeviceIdentityProvider's module pulls in the
    # `cryptography` package, which is Windows-only in this build (Android
    # uses Tink instead, see Phase 7 Part F) -- an eager top-level import of
    # it on Android crashed the whole app.py import with
    # ModuleNotFoundError('cryptography'), taking the entire embedded server
    # down silently (found via physical-device testing, Phase 7V-F).
    from commercial_runtime.licensing_contracts.android_bridge_identity import AndroidBridgeDeviceIdentityProvider
    _licensing_device_identity_factory = AndroidBridgeDeviceIdentityProvider
else:
    from commercial_runtime.licensing_contracts.device_identity import WindowsDpapiDeviceIdentityProvider
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
    _resume_einvoicing_workers()
    return app


def _resume_einvoicing_workers():
    """Mirrors products/retail/backend/app.py's identical function -- see
    its docstring for the full rationale (e-invoicing now defaults ON, so a
    default-only-enabled company has no explicit einvoice_settings row at
    all; the sweep must also cover companies with einvoice_outbox rows,
    each filtered through settings.is_enabled() so the killswitch and an
    explicit '0' still win). A fresh/never-enabled install finds zero rows
    and starts zero threads."""
    conn = get_clinic_conn()
    try:
        rows = conn.execute(
            "SELECT company_id FROM einvoice_settings WHERE skey='enabled' AND svalue='1' "
            "UNION SELECT company_id FROM einvoice_outbox"
        ).fetchall()
        for row in rows:
            cid = row[0]
            if not _einvoicing_settings.is_enabled(conn, _EINVOICING_APP_DATA_DIR, cid):
                continue
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
