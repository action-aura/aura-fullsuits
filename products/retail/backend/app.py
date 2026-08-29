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
import logging
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

from flask import Flask, jsonify, request
from flask_cors import CORS

from config import (
    SECRET_KEY, DATABASE_DIR, APP_VERSION,
    OWNER_LICENSING_BASE_URL, OWNER_LICENSING_VERIFY_TLS, OWNER_LICENSING_TIMEOUT_SECONDS,
    LICENSING_TRUST_ANCHOR_PATH, LICENSING_PLATFORM, LICENSING_INTERNAL_SHARED_SECRET,
    SYNC_RELAY_BASE_URL, SYNC_RELAY_TIMEOUT_SECONDS, SYNC_RELAY_VERIFY_TLS,
    SYNC_RELAY_URL_PROBLEMS,
)

# Final-review Fix 2 (2026-08-07): a sync relay URL that fails config.py's
# `validate_sync_relay_url` (cleartext http:// to a non-loopback host,
# embedded credentials, unsupported scheme, ...) disables sync entirely
# rather than silently shipping device-signed business data in the clear.
# Mirrors mobile/aura-retail-unified's AuraAppContainer.kt, which likewise
# never starts sync against a `SyncRelayConfiguration` that fails
# `validate()`. Logged loudly (not silently swallowed) so a misconfigured
# deployment is diagnosable, and NOT fatal to the app: Retail must still boot
# and work fully with no working Owner/sync at all.
_SYNC_RELAY_URL_IS_USABLE = bool(SYNC_RELAY_BASE_URL) and not SYNC_RELAY_URL_PROBLEMS
if SYNC_RELAY_BASE_URL and SYNC_RELAY_URL_PROBLEMS:
    import logging as _logging
    for _problem in SYNC_RELAY_URL_PROBLEMS:
        _logging.getLogger(__name__).error("Multi-device sync DISABLED -- %s", _problem)

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


@app.route('/', methods=['GET'])
def _index():
    """Serves the standalone shell (frontend/index.html), which loads
    app-shell.js + subsystem-retail.js and drives onboarding/login/nav.
    Was previously unrouted entirely -- the packaged launcher opened this
    exact URL and 404'd (see docs/superpowers/specs/2026-08-06-retail-standalone-ui-shell-design.md)."""
    return app.send_static_file('index.html')


from commercial_runtime.identity.auth_routes import auth_bp
from commercial_runtime.identity.onboarding_routes import onboarding_bp
from commercial_runtime.identity.registry_db import init_registry_db
from database.schema import (
    get_retail_conn,
    init_retail,
    load_sync_freshness,
    record_sync_freshness,
    record_offline_override,
    record_or_refresh_stock_exception,
    rebind_company_id_after_activation,
)
# Launch-readiness Phase 5 prerequisite #1 (registry v4) -- the identity-side
# half of the company_id rebind. Aliased because the retail-side function
# just above shares the exact same name by design (mirrored dialect, see
# commercial_runtime/identity/company_rebind.py's module docstring) and both
# are needed here, composed in the right order. See _on_licence_activated
# and init_app() below for where each is actually called.
from commercial_runtime.identity.company_rebind import (
    rebind_company_id_after_activation as rebind_registry_company_id_after_activation,
    owner_issued_company_id as registry_owner_issued_company_id,
    registry_tenant_ids as registry_company_tenant_ids,
)
from commercial_runtime.identity.registry_db import get_conn as get_registry_conn
from api.retail_api import retail_bp, _ensure_credit_schema
from api.import_api import import_bp
from commercial_runtime.backup.routes import make_backup_blueprint
from commercial_runtime.licensing_contracts.routes import make_licensing_blueprint
from commercial_runtime.identity.device_routes import device_bp
from commercial_runtime.einvoicing.routes import make_einvoicing_blueprint
from commercial_runtime.einvoicing.worker import OutboxWorker
from commercial_runtime.einvoicing.providers.mock import MockProvider
from commercial_runtime.einvoicing import settings as _einvoicing_settings
from core.retail import einvoice_adapter as _einvoice_adapter
from commercial_runtime.notifications.routes import make_notifications_blueprint
from commercial_runtime.notifications.worker import EmailOutboxWorker
from commercial_runtime.notifications import settings as _notification_settings
from commercial_runtime.notifications.whatsapp_routes import make_whatsapp_notifications_blueprint
from commercial_runtime.notifications.whatsapp_worker import WhatsAppOutboxWorker
from commercial_runtime.notifications import whatsapp_settings as _whatsapp_settings

app.register_blueprint(auth_bp)
app.register_blueprint(onboarding_bp)
app.register_blueprint(retail_bp)
app.register_blueprint(import_bp)
app.register_blueprint(make_backup_blueprint('retail', DATABASE_DIR, APP_VERSION))
app.register_blueprint(device_bp)

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

# feat/email-outbox-foundation -- outbound email (low-stock alerts, on-
# demand report emails, verification codes). Default OFF: AURA_SMTP_HOST is
# unset by default (commercial_runtime/notifications/smtp_client.py), which
# is this feature's own hard-off gate, independent of the per-company
# `email_settings.enabled` toggle below -- see
# commercial_runtime/notifications/settings.py::is_enabled. Same lazy,
# per-company worker registry pattern as einvoicing just above, for the
# identical reason (this registry is genuinely multi-tenant).
_notifications_workers = {}


def _get_or_create_notifications_worker(company_id):
    if company_id not in _notifications_workers:
        _notifications_workers[company_id] = EmailOutboxWorker(
            conn_factory=get_retail_conn,
            company_id=company_id,
        )
    return _notifications_workers[company_id]


app.register_blueprint(make_notifications_blueprint(
    conn_factory=get_retail_conn,
    get_worker=_get_or_create_notifications_worker,
))

# whatsapp-recipients -- outbound WhatsApp reports (daily sales, shift
# close, low stock, AR overdue), routed to multiple role/branch-scoped
# recipients (commercial_runtime/notifications/whatsapp_recipients.py).
# Default OFF: AURA_WHATSAPP_PHONE_NUMBER_ID is unset by default
# (commercial_runtime/notifications/whatsapp_client.py), this feature's own
# hard-off gate, independent of the per-company `whatsapp_settings.enabled`
# toggle below -- see whatsapp_settings.py::is_enabled. Same lazy,
# per-company worker registry pattern as email notifications just above.
_whatsapp_workers = {}


def _get_or_create_whatsapp_worker(company_id):
    if company_id not in _whatsapp_workers:
        _whatsapp_workers[company_id] = WhatsAppOutboxWorker(
            conn_factory=get_retail_conn,
            company_id=company_id,
        )
    return _whatsapp_workers[company_id]


app.register_blueprint(make_whatsapp_notifications_blueprint(
    conn_factory=get_retail_conn,
    get_worker=_get_or_create_whatsapp_worker,
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

def _detect_split_state_reason():
    """Run BOTH halves of the launch-readiness Phase 5 prerequisite #1
    rebind (identity, then retail -- same order and same never-raises
    contract as `_on_licence_activated` below) and report whether the
    process is now stuck in Defect 2's split state: identity has
    UNAMBIGUOUSLY adopted the Owner-issued `company_id` but retail.db could
    not follow.

    THE ONE PLACE this decision gets made. `_converge_and_refuse_to_serve_
    if_stuck()` (the boot-time guard, already correct and mutation-proven --
    see its own docstring) and the activation-time refusal below
    (`_on_licence_activated` / `_refuse_while_split_state`) both call this
    SAME function rather than each holding its own opinion about what
    "stuck" means. Two independently-written predicates for the identical
    question are exactly how the two seams quietly drift apart and disagree
    about a shop's data -- reusing one function is what keeps that
    impossible.

    Returns the refusal message (a non-empty string, ready to raise or to
    serve as a 503 body) if genuinely stuck, or `None` for every other
    outcome: nothing to do, converged just now, deferred (identity has not
    adopted the Owner id yet), or the CLAUDE.md-legitimate multi-tenant
    case. See `_converge_and_refuse_to_serve_if_stuck`'s docstring for the
    full reasoning behind each branch below -- this function only extracts
    the shared decision, it does not re-derive it.
    """
    rebind_registry_company_id_after_activation()
    result = rebind_company_id_after_activation()
    if result['status'] != 'failed':
        return None  # skipped / deferred / already_bound / rebound -- all fine

    new_company_id = registry_owner_issued_company_id()
    if not new_company_id:
        return None  # the licence disappeared between the call above and this check

    registry_conn = get_registry_conn()
    try:
        tenants = registry_company_tenant_ids(registry_conn)
    finally:
        registry_conn.close()

    if tenants != {new_company_id}:
        # Either genuinely multi-tenant, or identity itself has not adopted
        # the Owner-issued id yet -- either way retail's failure above is
        # not the empty-shop catastrophe this guard exists to prevent.
        return None

    return (
        "REFUSING TO SERVE: registry.db's identity layer has adopted the "
        f"Owner-issued company_id {new_company_id!r}, but retail.db could not "
        f"converge onto it ({result.get('reason')!r}). Starting the server now "
        "would make every WHERE company_id=? query in retail_api.py match zero "
        "rows -- this shop's entire history would silently disappear from the "
        "screen. See docs/launch-readiness/phase5-prerequisites.md §1."
    )


# Defect 2 (launch-readiness Phase 5 verification, HIGH): the in-process
# equivalent of the boot-time refusal above, for the window `_converge_and_
# refuse_to_serve_if_stuck()` cannot see -- an ACTIVATION that lands while
# this process is already up and serving. `_on_licence_activated` (below)
# used to call both rebind halves and discard BOTH result dicts; if
# identity's half committed and retail's half then failed (a locked
# retail.db under a concurrent sale is the ordinary cause), nothing noticed,
# and this process kept serving that split state -- an empty-looking shop,
# `200 OK`, for the rest of its running lifetime, healed only by the NEXT
# restart's boot-time guard. `docs/launch-readiness/phase5-prerequisites.md`
# §1 says plainly: "If retail's half fails, identity's half must be rolled
# back or the app must refuse to serve." The boot path already refuses; this
# is what makes the activation seam refuse too, instead of quietly serving.
#
# A tiny mutable module-level dict (not a bare global string) so the
# `before_request` hook below can read AND clear it without a `global`
# statement, and so a value of `None` is unambiguous: "not currently
# refusing" is not a special string, it is the literal absence of a reason.
_split_state_refusal = {'reason': None}


_split_state_log = logging.getLogger(__name__)


def _enter_split_state_refusal(reason):
    _split_state_log.error("Entering split-state refusal after activation: %s", reason)
    _split_state_refusal['reason'] = reason


def _clear_split_state_refusal():
    if _split_state_refusal['reason'] is not None:
        _split_state_log.info(
            "Split-state refusal cleared -- retail.db has converged; resuming normal service."
        )
    _split_state_refusal['reason'] = None


@app.before_request
def _refuse_while_split_state():
    """Serves the 503 half of Defect 2's fix -- see the module-level
    `_split_state_refusal` comment above for why this exists at all.

    THE COMMON CASE (no activation has ever landed the process into this
    state) is a single dict-key read and nothing else: no database touched,
    no measurable cost added to every other request this app serves.

    WHILE REFUSING, every request pays the cost of retrying convergence --
    deliberately, not merely tolerated: "clear it as soon as convergence
    succeeds (retry on a later request or at next boot)" is the whole point
    of an in-process refusal instead of a hard crash. Re-running `_detect_
    split_state_reason()` (the SAME predicate the boot guard and the
    activation hook both use) means the very next request after whatever
    was blocking retail's convergence clears (the lock let go, the disk
    issue resolved, an operator fixed a fabricated multi-tenant retail.db)
    is the request that ends the refusal -- no restart required, though a
    restart still heals it too via the boot guard.

    `/api/health` is exempted: its own docstring already freezes its
    contract for the desktop launcher's startup readiness probe, it reveals
    no business data, and refusing it would make the launcher unable to
    tell "the process is up but refusing" apart from "the process never
    started" -- turning an honest, diagnosable 503 into a bare timeout.
    """
    if _split_state_refusal['reason'] is None:
        return None
    if request.path == '/api/health':
        return None

    reason = _detect_split_state_reason()
    if reason is None:
        _clear_split_state_refusal()
        return None

    _split_state_refusal['reason'] = reason
    return jsonify({
        'error': (
            'This till is finishing a one-time tenant-identity upgrade and cannot '
            'serve requests yet. It retries automatically on the next request; '
            'restarting the app also retries immediately. Contact support if this '
            'persists.'
        ),
        'code': 503,
        'reason': reason,
    }), 503


def _on_licence_activated():
    """Composed `on_activation_success` hook: BOTH halves of the
    launch-readiness Phase 5 prerequisite #1 rebind, in the order that keeps
    the identity layer the leader and retail the follower, PLUS Defect 2's
    activation-seam refusal check.

    IDENTITY FIRST, ALWAYS -- inherited from `_detect_split_state_reason`,
    which runs `rebind_registry_company_id_after_activation()` before
    `rebind_company_id_after_activation()` for the same reason this
    function always has: only once identity has landed does retail's own
    convergence find `local_authoritative_company_id() == new_company_id`
    and follow (see database/schema.py::_rebind_to_owner_issued's
    docstring). Calling them in the opposite order would mean retail's
    check never passes on THIS activation, deferring the real work to
    whatever boot happens to come next -- correct eventually, but a
    needless extra window.

    NEVER RAISES (unchanged contract -- both rebind calls inside `_detect_
    split_state_reason` are explicitly documented NEVER TO RAISE, for the
    same reason: a licence activation that genuinely succeeded at Owner
    must not be reported back to the customer as a failure because a local
    bookkeeping rewrite hit a locked database). `_run_activation_hook` in
    commercial_runtime/licensing_contracts/routes.py also swallows whatever
    a hook raises regardless, so this composed function inherits that
    safety net too, belt and suspenders -- but the split-state check itself
    only ever reads status dicts and a set of strings, so there is nothing
    left here that should raise in the first place.
    """
    reason = _detect_split_state_reason()
    if reason is not None:
        _enter_split_state_refusal(reason)
    else:
        _clear_split_state_refusal()


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
    # Launch-readiness Phase 2 (retail schema v14) + Phase 5 prerequisite #1
    # (registry v4): activation is the exact moment this install first
    # learns its Owner-issued tenant key (`license_public_id`, the same
    # value owner/app/sync/routes.py scopes every relayed event by).
    # registry.db's identity layer has to adopt it before retail.db's
    # `company_id` -- derived locally at onboarding as md5(admin_email) and
    # therefore meaningless to Owner -- can safely converge onto it, and
    # neither migration alone can do that: licensing is OFF by default here,
    # so the common install migrates long before it ever activates, and a
    # rebind that only ran at migration time would silently never happen.
    # See _on_licence_activated above.
    on_activation_success=_on_licence_activated,
))

# Multi-device sync foundation (2026-08-06), Task 5: the background push/pull
# loop. Reuses the EXACT same licensing_dir/db_path/device-identity factory
# make_licensing_blueprint's own _build_context() constructs above -- never a
# second device identity, never a second on-disk key -- so the signature
# this client produces verifies against the SAME installation Owner already
# knows from activation/check-in.
_sync_service = None
_registry_sync_service = None
if _SYNC_RELAY_URL_IS_USABLE and LICENSING_PLATFORM != 'ANDROID':
    from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository
    from commercial_runtime.sync.relay_client import SyncRelayClient
    from commercial_runtime.sync.sync_service import (
        REGISTRY_SYNC_ENTITY_TYPES,
        SyncFreshnessStore,
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
    #
    # `_ensure_credit_schema` (Launch-readiness Phase 5, money-moving sync):
    # SyncService's own `local_ensure_schema` hook -- see that constructor
    # parameter's docstring for the exact bug this closes (a lazy, route-
    # triggered migration `sales.due_date`/`payments`'s AR-AP columns depend
    # on, which the sync background loop's own timer can reach before any
    # HTTP route ever has on a brand-new device).
    #
    # `_retail_sync_freshness_store` (Launch-readiness Phase 7 stage 7a,
    # docs/launch-readiness/phase7-offline-ux.md "FINDING 1"): SyncService's
    # `local_freshness_store` hook -- see `SyncFreshnessStore`'s own
    # docstring for the full reasoning. `load_sync_freshness`/
    # `record_sync_freshness` (database/schema.py) read and write the
    # `sync_freshness` table (schema v18), which exists ONLY in retail.db --
    # this instance's own database via `_sync_get_conn` above -- so it is
    # passed here, and deliberately NOT to `_registry_sync_service` below,
    # whose database has no such table.
    #
    # `record_offline_override` (schema v19, stage 7c-ii): the SAME table's
    # third column, wired the same way for the same reason -- see
    # SyncFreshnessStore's own docstring on why this rides the existing
    # collaborator instead of a second one.
    #
    # `record_or_refresh_stock_exception` (schema v20, stage 7d-i; extended
    # to a SECOND caller in stage 7d-iii): SyncService's own
    # `stock_exception_recorder` hook -- see that constructor parameter's
    # docstring for the full reasoning. Same shape of wiring as `local_
    # ensure_schema` above (a bare optional callable, not a NamedTuple):
    # `stock_exceptions` (schema v20) is a retail.db table, exactly like
    # `sync_freshness` above, so it is passed here and deliberately NOT to
    # `_registry_sync_service` below, whose database has no such table --
    # and whose apply loop never reaches the branch that would call it
    # anyway, since `inventory_movement` is not in
    # `REGISTRY_SYNC_ENTITY_TYPES`.
    _retail_sync_freshness_store = SyncFreshnessStore(
        load=load_sync_freshness, record=record_sync_freshness, record_override=record_offline_override)
    _sync_service = SyncService(_build_sync_client, _sync_get_conn, local_company_id_from_registry,
                                local_ensure_schema=_ensure_credit_schema,
                                local_freshness_store=_retail_sync_freshness_store,
                                stock_exception_recorder=record_or_refresh_stock_exception)
    register_active_service(_sync_service)

    # Phase 5 wave B2, Decision 6 (docs/launch-readiness/
    # phase5-waveb2-user-sync.md): a SECOND SyncService instance, pulling
    # the SAME relay stream (`_build_sync_client` -- the exact same signer,
    # so this stream authenticates as the same installation) into a
    # DIFFERENT database. `users` lives in registry.db, not retail.db, and
    # the two run in separate WAL-mode files with no cross-database atomic
    # commit (§Decision 1) -- so `_sync_get_conn` below is registry.db's own
    # `get_conn`, never `_sync_get_conn` above. `handled_entity_types=
    # REGISTRY_SYNC_ENTITY_TYPES` is what keeps this instance from ever
    # trying to write a `sale`/`category`/... event into registry.db, which
    # has no such tables -- see sync_service.py's module docstring
    # "Two-stream design" paragraph for the exact wedge this design avoids.
    # `local_ensure_schema` is intentionally omitted: that hook only ever
    # exists for retail's lazy, route-triggered AR/AP migration
    # (`_ensure_credit_schema`), which has nothing to do with `users`.
    # `local_freshness_store` is intentionally omitted too, for the same
    # shape of reason: `sync_freshness` (schema v18) is a retail.db table,
    # and registry.db is at its own, independent schema version -- see
    # `SyncFreshnessStore`'s own docstring. This instance therefore persists
    # no freshness at all; its `get_health()` behaves exactly as it always
    # has, pre-Phase-7.
    #
    # Deliberately NEVER passed to `register_active_service` -- that global
    # slot is what `nudge()` (called from retail_api.py after every
    # catalogue/money/stock write) pushes through, and there is exactly one
    # slot. Registering this second instance there would silently redirect
    # every existing nudge() call away from the retail outbox it is meant to
    # drain. It runs its OWN push/pull timer via `.start()` below instead --
    # a plain 10-second tick, not a nudge()-triggered one -- which is what
    # actually drains this instance's outbox: every identity write site
    # (Phase 5 wave B2 stage 2b, commercial_runtime/identity/user_accounts.py
    # `_queue_user_sync_event` + its onboarding_routes.py/auth_routes.py call
    # sites) now queues a `user` event here on account creation, password
    # change, role/status change, language change and PIN set/clear. Stage
    # 2a shipped this timer BEFORE any write site existed specifically so the
    # APPLY side (a `user` event arriving from Owner, from another device's
    # own stage 2b) was already live the moment stage 2b landed, with no
    # second deploy required.
    def _registry_sync_get_conn():
        from commercial_runtime.identity.registry_db import get_conn as _registry_get_conn
        return _registry_get_conn()

    _registry_sync_service = SyncService(
        _build_sync_client, _registry_sync_get_conn, local_company_id_from_registry,
        handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES)
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
    from commercial_runtime.sync.sync_service import (
        SyncFreshnessStore,
        SyncService,
        local_company_id_from_registry,
    )
    from commercial_runtime.sync.internal_routes import make_sync_internal_blueprint

    _sync_state_repository = LicenseStateRepository(Path(DATABASE_DIR) / 'subsystems' / 'licensing.db')

    def _sync_get_conn():
        from database.schema import get_retail_conn
        return get_retail_conn()

    # local_company_id_from_registry / local_ensure_schema: see the Windows
    # branch above for both -- the /_internal/sync/pull-apply route below
    # calls apply_pull_result() on THIS service too, and needs the identical
    # fix (Kotlin's own pull loop can reach this route before any other
    # request has, on a brand-new Android install, exactly like the Windows
    # timer can).
    #
    # local_freshness_store: same reasoning as the Windows branch above --
    # this instance's `_sync_get_conn` is retail.db too (Android has no
    # separate registry-stream SyncService of its own), so it carries the
    # `sync_freshness` table (schema v18) exactly like the Windows instance
    # does, and gets the same collaborator -- including `record_offline_
    # override` (schema v19, stage 7c-ii; see the Windows branch above).
    # stock_exception_recorder: same reasoning as the Windows branch above
    # -- this instance's `_sync_get_conn` is retail.db too, so it carries
    # `stock_exceptions` (schema v20) exactly like the Windows instance
    # does, and gets the same collaborator.
    _android_sync_freshness_store = SyncFreshnessStore(
        load=load_sync_freshness, record=record_sync_freshness, record_override=record_offline_override)
    _android_sync_service = SyncService(None, _sync_get_conn, local_company_id_from_registry,
                                        local_ensure_schema=_ensure_credit_schema,
                                        local_freshness_store=_android_sync_freshness_store,
                                        stock_exception_recorder=record_or_refresh_stock_exception)  # client_factory never used -- see comment above
    app.register_blueprint(make_sync_internal_blueprint(
        sync_service=_android_sync_service,
        get_conn=_sync_get_conn,
        state_repository=_sync_state_repository,
        shared_secret=LICENSING_INTERNAL_SHARED_SECRET,
    ))


def _converge_and_refuse_to_serve_if_stuck():
    """Launch-readiness Phase 5 prerequisite #1 -- closes the boot-time half
    of "the window" described in
    docs/launch-readiness/phase5-prerequisites.md §1 and required by the
    task that added this function (see git history / PR description for
    "registry v4, the identity-side company_id rebind").

    WHY THIS HAS TO RUN ON EVERY SINGLE BOOT, not just when a migration is
    due: `ensure_schema_version`'s whole point is a fast no-op once a
    database is already at its target version. That is exactly wrong for
    this specific piece of state, because a crash landing between identity
    adopting the Owner-issued company_id (registry v4's migration step, or
    `_on_licence_activated`'s identity half) and retail's own convergence
    following it would otherwise NEVER be revisited -- neither
    registry_db.py's v4 step nor schema.py's v14 step re-enters a database
    that is already at its own target version. `rebind_company_id_after_
    activation()` has no such gate: it is a plain idempotent
    read-then-maybe-write, safe to call on every boot at negligible cost (a
    handful of SELECT DISTINCT queries) once an install has converged, and
    it is what actually CLOSES this window on a converged single-tenant
    install -- the far more common outcome than the refusal below.

    THE REFUSAL, for the install that genuinely cannot converge: if identity
    has UNAMBIGUOUSLY adopted the Owner-issued id (registry.db holds exactly
    that one company_id and nothing else -- the ordinary single-tenant case,
    never the legitimate "one install hosts more than one company" case
    CLAUDE.md documents) and retail's attempt to follow still reports
    'failed', starting the server anyway would mean serving a shop whose
    entire history is invisible: `session['company_id']` would come from
    registry.db's now-Owner-issued value, `retail_api._cid()` filters every
    query on it, and not one row in retail.db would match. That is strictly
    worse than refusing to boot, so this raises instead -- and because
    `init_app()` is called unconditionally, synchronously, before this
    process ever starts accepting a request (see `if __name__ == '__main__'`
    at the bottom of this file), raising here means the process never
    listens at all rather than listening and serving an empty-looking shop.

    A genuinely multi-tenant registry.db (`registry_company_tenant_ids`
    returns more than one value, or a value other than the Owner-issued one)
    is deliberately NOT covered by this refusal -- that ambiguity is
    `rebind_company_id`'s own, correct, permanent refusal (CLAUDE.md: "one
    install *can* host more than one company"), and this function must never
    mistake that legitimate, unchanged state for the specific catastrophe it
    exists to prevent.

    IDENTITY FIRST, on every boot, for the SAME reason the retail half runs
    on every boot -- and it was missing before this function existed.
    Identity's own rebind is reachable from exactly two seams: registry v4's
    migration step (version-gated, so it never re-enters once user_version
    is already 4) and `_on_licence_activated` (only /activate and
    /_internal/sync-activation fire it -- NOT /check-in). An activation
    whose identity half returned 'failed' (a locked registry.db is the
    ordinary cause) therefore had no third chance at all: retail's half
    correctly deferred, both databases stayed consistently on the legacy
    key, the shop kept working -- and the install stayed permanently
    un-rebound despite holding a licence, which is precisely the state
    Phase 5 must not open on top of (rows pushed under md5(admin_email)
    arrive at Owner scoped to a tenant it does not recognise).

    THE ACTUAL DECISION is delegated to `_detect_split_state_reason()` --
    see that function's docstring for why: `_on_licence_activated`'s own
    in-process refusal (Defect 2) has to ask the identical question this
    function asks, and two independently-written answers to "are we stuck?"
    is how the two seams end up disagreeing about a shop's data. This
    function's only remaining job is turning a non-`None` answer into the
    boot-time consequence: refuse to start at all, rather than the
    activation-time consequence (serve 503s until it clears).
    """
    reason = _detect_split_state_reason()
    if reason is not None:
        raise RuntimeError(reason)


def init_app():
    """Initialize the registry + retail schema, and start the background
    sync loop (if configured). Call once before serving.

    Deliberately never starts anything for `_android_sync_service` -- it is
    never assigned to `_sync_service` (see the ANDROID branch above), so
    this function's own `_sync_service is not None` check correctly stays
    False on Android. Calling `.start()` on it would run Python's own
    push/pull timer, which would call its `client_factory` (`None`) and
    crash on the first tick -- Kotlin's SyncCoordinator is what drives
    Android's push/pull loop instead.

    `_registry_sync_service` (Phase 5 wave B2, Decision 6) is started here
    too, independently of `_sync_service` -- both are `None` together or
    both non-`None` together on Windows (both are only ever built inside
    the SAME `if _SYNC_RELAY_URL_IS_USABLE and LICENSING_PLATFORM !=
    'ANDROID':` block above), but each is a genuinely separate
    `SyncService` with its OWN timer/thread/lock, so each needs its own
    `.start()` call -- `.start()` on one has no effect on the other's own
    scheduling."""
    init_registry_db()
    init_retail()
    # Launch-readiness Phase 5 prerequisite #1 -- must run AFTER both
    # migrations (it is what catches up when a migration's own version gate
    # can no longer fire, see the docstring above) and BEFORE anything below
    # this line reads company-scoped retail data, so a stuck install is
    # caught before the sync loop or any outbox worker touches it.
    _converge_and_refuse_to_serve_if_stuck()
    if _sync_service is not None:
        _sync_service.start()
    if _registry_sync_service is not None:
        _registry_sync_service.start()
    _resume_einvoicing_workers()
    _resume_notifications_workers()
    _resume_whatsapp_workers()
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


def _resume_notifications_workers():
    """Mirrors _resume_einvoicing_workers() immediately above -- same
    reasoning: a company that had email notifications enabled before a
    restart must keep draining its outbox without waiting for a settings
    write to notice. A fresh/never-enabled install finds zero rows here and
    starts zero threads, same as einvoicing's own sweep."""
    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT company_id FROM email_settings WHERE skey='enabled' AND svalue='1'"
        ).fetchall()
        for row in rows:
            cid = row[0]
            interval = int(_notification_settings.get_setting(conn, cid, 'submit_interval_seconds'))
            _get_or_create_notifications_worker(cid).start(interval_seconds=interval)
    finally:
        conn.close()


def _resume_whatsapp_workers():
    """Mirrors _resume_notifications_workers() immediately above -- same
    reasoning: a company that had WhatsApp reports enabled before a restart
    must keep draining its outbox without waiting for a settings write to
    notice. A fresh/never-enabled install finds zero rows here and starts
    zero threads, same as email's own sweep."""
    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT company_id FROM whatsapp_settings WHERE skey='enabled' AND svalue='1'"
        ).fetchall()
        for row in rows:
            cid = row[0]
            interval = int(_whatsapp_settings.get_setting(conn, cid, 'submit_interval_seconds'))
            _get_or_create_whatsapp_worker(cid).start(interval_seconds=interval)
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
