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
    SYNC_RELAY_BASE_URL, SYNC_RELAY_TIMEOUT_SECONDS, SYNC_RELAY_VERIFY_TLS,
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
from database.schema import init_retail
from api.retail_api import retail_bp
from api.import_api import import_bp
from commercial_runtime.backup.routes import make_backup_blueprint
from commercial_runtime.licensing_contracts.routes import make_licensing_blueprint

app.register_blueprint(auth_bp)
app.register_blueprint(onboarding_bp)
app.register_blueprint(retail_bp)
app.register_blueprint(import_bp)
app.register_blueprint(make_backup_blueprint('retail', DATABASE_DIR, APP_VERSION))

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

# Multi-device sync foundation (2026-08-06), Task 5: the background push/pull
# loop. Reuses the EXACT same licensing_dir/db_path/device-identity factory
# make_licensing_blueprint's own _build_context() constructs above -- never a
# second device identity, never a second on-disk key -- so the signature
# this client produces verifies against the SAME installation Owner already
# knows from activation/check-in.
_sync_service = None
if SYNC_RELAY_BASE_URL and LICENSING_PLATFORM != 'ANDROID':
    from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository
    from commercial_runtime.sync.relay_client import SyncRelayClient
    from commercial_runtime.sync.sync_service import (
        SyncService,
        local_company_id_from_registry,
        register_active_service,
    )

    _sync_licensing_dir = Path(DATABASE_DIR).parent / 'licensing'
    _sync_state_repository = LicenseStateRepository(Path(DATABASE_DIR) / 'subsystems' / 'licensing.db')
    _sync_signer = _licensing_device_identity_factory(_sync_licensing_dir)

    def _build_sync_client():
        # Rebuilt on every call (not cached at startup) for the same reason
        # make_licensing_blueprint's _build_context() rebuilds its own
        # per-request object graph: the installation_id this client signs
        # with must reflect activation happening AFTER process start, not a
        # None/stale value captured once at import time.
        record = _sync_state_repository.load()
        installation_id = record.owner_installation_id if record else None
        return SyncRelayClient(
            SYNC_RELAY_BASE_URL,
            _sync_signer,
            installation_id,
            timeout_seconds=SYNC_RELAY_TIMEOUT_SECONDS,
            verify_tls=SYNC_RELAY_VERIFY_TLS,
        )

    def _sync_get_conn():
        from database.schema import get_retail_conn
        return get_retail_conn()

    # local_company_id_from_registry (commercial_runtime/sync/sync_service.py)
    # reads THIS device's own company_id from the registry DB on every pull
    # batch that needs it -- never the pulled payload's own company_id (a
    # different device's value). See that function's docstring and the
    # module-level "Cross-device company_id bug fix" note.
    _sync_service = SyncService(_build_sync_client, _sync_get_conn, local_company_id_from_registry)
    register_active_service(_sync_service)
elif LICENSING_PLATFORM == 'ANDROID' and LICENSING_INTERNAL_SHARED_SECRET:
    # Android's own wiring (multi-device-sync-foundation, Task 9): this
    # process never holds the Android device's private key
    # (AndroidBridgeDeviceIdentityProvider.sign() always raises -- see that
    # class's docstring), so it can never call SyncRelayClient.push()/pull()
    # itself the way the Windows branch above does. Kotlin's own
    # SyncRelayClient.kt (AndroidKeystore-backed, mirrors OwnerClient.kt --
    # "the ONLY place on Android that ever makes a signed HTTP call to
    # Owner") makes the actual signed push/pull calls directly to Owner, and
    # uses the /_internal/sync/* routes below (registered by
    # make_sync_internal_blueprint) to read the outbox it's about to sign,
    # acknowledge what it pushed, read the cursor, and apply what it pulled.
    # register_active_service()/nudge() are deliberately NOT wired here:
    # nudge() calls push_once(), which would call this SyncService's
    # client_factory (None, never usable on Android) -- Kotlin's
    # SyncCoordinator has its own short-interval timer instead of relying on
    # the shared retail_api.py nudge() call sites.
    from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository
    from commercial_runtime.sync.sync_service import SyncService, local_company_id_from_registry
    from commercial_runtime.sync.internal_routes import make_sync_internal_blueprint

    _sync_state_repository = LicenseStateRepository(Path(DATABASE_DIR) / 'subsystems' / 'licensing.db')

    def _sync_get_conn():
        from database.schema import get_retail_conn
        return get_retail_conn()

    # local_company_id_from_registry: see the Windows branch above -- the
    # /_internal/sync/pull-apply route below calls apply_pull_result() on
    # THIS service, which needs the same fix.
    _android_sync_service = SyncService(None, _sync_get_conn, local_company_id_from_registry)  # client_factory never used -- see comment above
    app.register_blueprint(make_sync_internal_blueprint(
        sync_service=_android_sync_service,
        get_conn=_sync_get_conn,
        state_repository=_sync_state_repository,
        shared_secret=LICENSING_INTERNAL_SHARED_SECRET,
    ))


def init_app():
    """Initialize the registry + retail schema, and start the background
    sync loop (if configured). Call once before serving.

    Deliberately never starts anything for `_android_sync_service` -- it is
    never assigned to `_sync_service` (see the ANDROID branch above), so
    this function's own `_sync_service is not None` check correctly stays
    False on Android. Calling `.start()` on it would run Python's own
    push/pull timer, which would call its `client_factory` (`None`) and
    crash on the first tick -- Kotlin's SyncCoordinator is what drives
    Android's push/pull loop instead."""
    init_registry_db()
    init_retail()
    if _sync_service is not None:
        _sync_service.start()
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
