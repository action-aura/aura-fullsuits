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
import threading
from datetime import timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

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
    OWNER_LICENSING_URL_SOURCE, OWNER_LICENSING_URL_PROBLEMS,
    LICENSING_TRUST_ANCHOR_PATH, LICENSING_PLATFORM, LICENSING_INTERNAL_SHARED_SECRET,
    SYNC_RELAY_BASE_URL, SYNC_RELAY_TIMEOUT_SECONDS, SYNC_RELAY_VERIFY_TLS,
    SYNC_RELAY_URL_PROBLEMS, resolve_sync_relay_base_url_now,
    SITE_RELAY_ENABLE_OVERRIDE, SITE_RELAY_PORT, SITE_RELAY_BIND_HOST,
    site_relay_should_start,
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

# retail-hardware-viewports (2026-09-24): same "log every problem loudly,
# never let it fail the boot" pattern as the sync-relay block immediately
# above, for a baked owner_endpoint.json that config.py's
# _resolve_owner_licensing_base_url() rejected (unreadable, malformed JSON,
# missing key, or a URL that failed _validate_baked_owner_licensing_url).
if OWNER_LICENSING_URL_PROBLEMS:
    import logging as _logging
    for _problem in OWNER_LICENSING_URL_PROBLEMS:
        _logging.getLogger(__name__).warning("Owner licensing URL DISABLED -- %s", _problem)
if OWNER_LICENSING_BASE_URL:
    import logging as _logging
    _logging.getLogger(__name__).info(
        "Owner licensing URL: %s (source: %s)", OWNER_LICENSING_BASE_URL, OWNER_LICENSING_URL_SOURCE
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
    docstring); this one is free to grow.

    `schema_version` IS NOT THE DATABASE SCHEMA VERSION, despite the name.
    It is `commercial_runtime.backup.service.SCHEMA_VERSION`, the version of
    the BACKUP ARCHIVE FORMAT -- the number stamped into an .aurabak.zip
    filename and checked on restore. It is 1, and has been since Wave 0.

    The database's own version is `PRAGMA user_version` on each subsystem db,
    maintained by `ensure_schema_version()`; retail's is at RETAIL_SCHEMA_VERSION
    (26 as of 2026-09-08). Measured on a freshly installed build that same day:
    this endpoint returned `"schema_version": 1` while `retail.db` reported
    `user_version = 26`.

    Written down rather than renamed. A release-manifest tool that reads this
    field expecting "which migration is this install on" gets 1 for every
    install ever shipped and concludes none of them need migrating -- and the
    name invites exactly that reading. Renaming it would be a wire-contract
    change for no consumer: both Android clients parse it into a model field
    (`net/Models.kt`) and neither ever reads the value, so there is nothing to
    fix on that side and no benefit in breaking the shape. If a caller ever
    genuinely needs the migration level, add a separate, correctly-named field
    beside this one; do not repurpose this one."""
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
    RETAIL_SCHEMA_VERSION,
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
from api.retail_api import retail_bp, _ensure_credit_schema, install_void_reversal_sweep, install_party_balance_refresh
from api.import_api import import_bp
from commercial_runtime.backup.routes import make_backup_blueprint
from commercial_runtime.licensing_contracts.routes import make_licensing_blueprint
from commercial_runtime.updates.routes import build_update_service, make_updates_blueprint
from commercial_runtime.updates.scheduler import UpdateScheduler
from commercial_runtime.crash_reports import capture as crash_capture
from commercial_runtime.crash_reports.routes import make_crash_reports_blueprint
from commercial_runtime.crash_reports.sender import build_crash_report_sender
from commercial_runtime.crash_reports.store import open_default_store as open_crash_store
from commercial_runtime.identity.device_routes import device_bp
from commercial_runtime.einvoicing.routes import make_einvoicing_blueprint
from commercial_runtime.einvoicing.worker import OutboxWorker
from commercial_runtime.einvoicing.providers.mock import MockProvider
from commercial_runtime.einvoicing.providers.unconfigured import UnconfiguredProvider
from commercial_runtime.einvoicing import settings as _einvoicing_settings
from core.retail import einvoice_adapter as _einvoice_adapter
from commercial_runtime.notifications.routes import make_notifications_blueprint
from commercial_runtime.notifications.worker import EmailOutboxWorker
from commercial_runtime.notifications import transport as _mail_transport
from commercial_runtime.notifications.identity_sink import IdentityMailSink
from commercial_runtime.identity import verification as _identity_verification
from commercial_runtime.notifications import settings as _notification_settings
from commercial_runtime.notifications.whatsapp_routes import make_whatsapp_notifications_blueprint
from commercial_runtime.notifications.whatsapp_worker import WhatsAppOutboxWorker
from commercial_runtime.notifications import whatsapp_settings as _whatsapp_settings

app.register_blueprint(auth_bp)
app.register_blueprint(onboarding_bp)
app.register_blueprint(retail_bp)
app.register_blueprint(import_bp)
app.register_blueprint(make_backup_blueprint(
    'retail', DATABASE_DIR, APP_VERSION,
    # SYNC-BOOT Phase 2: after a restore the relay's self-exclusion would lose whatever this
    # computer pushed after the backup was taken. `_after_backup_restore` arms include_own_once
    # (and then does exactly what the default hook did: ask the launcher for a restart).
    restart_after_restore=lambda: _after_backup_restore()))
app.register_blueprint(device_bp)

# R-LAN: the operator's side of hub mode ("Connect a device", who is
# connected, revoke one). Registered on THIS app -- the loopback one already
# serving the POS UI -- and deliberately NOT on the LAN-facing relay app that
# listener.py builds. These routes mint pairing codes, which are the
# credential that admits a new device to the shop, so they must be reachable
# only by an admin already logged in at the till, never by anything on the
# wifi. See admin_routes.py's module docstring.
#
# Registered UNCONDITIONALLY, even on the overwhelming majority of installs
# that will never be hubs: the routes answer {"enabled": false} when this
# install is not one, because the Settings screen asks on every load and a 404
# there is a console error on a perfectly healthy till.
#
# The provider is a LAMBDA, not the handle: hub mode starts during init_app(),
# after this line runs, so capturing the value here would pin it to None
# forever and every route would report "not a hub" on a machine that is one.
from commercial_runtime.sync.site_relay.admin_routes import make_site_relay_admin_blueprint
app.register_blueprint(make_site_relay_admin_blueprint(
    hub_provider=lambda: _site_relay_server))

# INSTALL-1: the one-time Windows Firewall rule for the shop network, asked at
# the owner's own "Connect a device" press (see install_support/firewall.py for
# why there, and why never on a dev run). Wiring only; the logic and its tests
# live in commercial_runtime/install_support.
from commercial_runtime.install_support.firewall import make_firewall_blueprint as _make_firewall_blueprint
app.register_blueprint(_make_firewall_blueprint(str(Path(DATABASE_DIR).parent)))

# docs/einvoicing/phase1/ -- Jordan JoFotara e-invoicing. Default ON for the
# Jordan tax regime since 2026-09-08 (Jordan mandated e-invoicing from
# 2024-05-31 -- see commercial_runtime/einvoicing/settings.py's DEFAULTS and
# its four-layer is_enabled() resolution); a non-Jordan shop turns the whole
# feature off by setting tax_regime='none', not by an 'enabled' toggle alone.
#
# This registry's DB is genuinely multi-tenant (commercial_runtime/identity/
# registry_db.py -- one install CAN host more than one company), so there is
# no single fixed company_id to build one OutboxWorker for at import time.
# Workers are created lazily, one per company, the first time that
# company's settings are touched or its background job is resumed at boot.
_EINVOICING_APP_DATA_DIR = str(Path(DATABASE_DIR).parent)
# E-invoicing now defaults ENABLED (settings.py DEFAULTS['enabled'] -- the
# Jordanian mandate means a shop cannot ship with the obligation off by
# default). That makes the LIVE provider choice a compliance decision, not
# a convenience one: MockProvider reports CLEARED -- with a QR and a
# provider_uuid -- without ever contacting JoFotara, so wiring it in as the
# default would put a false "e-invoice cleared" claim on a real receipt no
# tax authority ever saw. DirectISTDProvider is the real provider but
# deliberately raises NotImplementedError until Phase 2 (see
# providers/direct_istd.py), and worker.py's OutboxWorker will not submit
# through any provider whose is_configured is False anyway. So the shipped
# default here is UnconfiguredProvider: documents enqueue and wait, nothing
# submits, nothing claims clearance. AURA_EINVOICING_ALLOW_MOCK=1 opts back
# into MockProvider for development/tests that need a real round trip.
_einvoicing_provider = (
    MockProvider() if os.environ.get('AURA_EINVOICING_ALLOW_MOCK') == '1' else UnconfiguredProvider()
)
_einvoicing_workers = {}


class _IdempotentEinvoicingWorkerHandle:
    """Wraps one company's OutboxWorker so `.start()` is safe to call more
    than once. OutboxWorker.start()/_schedule_next() deliberately mirrors
    licensing_contracts/checkin_scheduler.py's LicenseCheckInScheduler, and
    neither guards against a second start(): calling start() again does
    NOT cancel the first Timer -- it only overwrites the handle to it, so
    the first Timer chain keeps firing forever, alongside the new one. That
    non-idempotency never mattered while the only caller was the boot-time
    resume sweep below (called at most once per company, ever). It matters
    now: commercial_runtime/einvoicing/routes.py's _post_settings() calls
    .start() on EVERY settings write that resolves to enabled=True (not
    only on a False->True transition -- see that function's comment for
    why the transition-only check breaks for a default-enabled company),
    so a company saving its e-invoicing settings twice while already
    enabled must not spawn a second parallel timer family. Guarding here,
    at the call site both this sweep and routes.py share (both ultimately
    go through _get_or_create_einvoicing_worker), keeps OutboxWorker's own
    scheduling code identical to the LicenseCheckInScheduler pattern it was
    deliberately written to mirror -- see worker.py's module docstring.
    """

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
            conn_factory=get_retail_conn,
            app_data_dir=_EINVOICING_APP_DATA_DIR,
            company_id=company_id,
            provider=_einvoicing_provider,
            document_builder=_einvoice_adapter.build_document,
            reconcile_fn=_einvoice_adapter.reconcile_missing_sales,
        ))
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

# Real-email wave. Mail leaves this till one of three ways, decided on every
# worker tick (commercial_runtime/notifications/transport.py): AURA_SMTP_HOST
# set -> direct SMTP (unchanged); else an Owner URL and an activated install ->
# relayed THROUGH Owner (this till never holds a mailbox password); else
# nothing, and rows wait in the outbox. The relay is built lazily and only when
# an Owner URL is configured; Android is excluded because its device key lives
# in Kotlin (a later wave).
_mail_relay = None
_mail_workers_started = set()
#: How often a worker started because ACCOUNT mail was queued ticks. Short on
#: purpose: someone is waiting for their verification link.
_ACCOUNT_MAIL_TICK_SECONDS = 15


def _mail_relay_configured():
    return bool(OWNER_LICENSING_BASE_URL) and LICENSING_PLATFORM != 'ANDROID'


def _mail_transport_possible():
    return _notification_settings.transport_possible()


def _get_mail_relay():
    global _mail_relay
    if _mail_relay is None and _mail_relay_configured():
        from commercial_runtime.licensing_contracts.client import LicensingClient, LicensingClientConfig
        from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository
        _mail_app_data = Path(DATABASE_DIR).parent
        _mail_state_db = _mail_app_data / 'database' / 'subsystems' / 'licensing.db'
        _mail_relay = _mail_transport.RelayTransport(
            # One retry: this runs unattended on the worker tick and the next
            # tick (with the outbox's own backoff) is the real retry.
            client_factory=lambda: LicensingClient(LicensingClientConfig(
                base_url=OWNER_LICENSING_BASE_URL, timeout_seconds=OWNER_LICENSING_TIMEOUT_SECONDS,
                verify_tls=OWNER_LICENSING_VERIFY_TLS, max_retries=1)),
            signer_factory=lambda: _licensing_device_identity_factory(_mail_app_data / 'licensing'),
            installation_id_provider=lambda: getattr(
                LicenseStateRepository(_mail_state_db).load(), 'owner_installation_id', None),
            app_version=APP_VERSION,
            platform=LICENSING_PLATFORM,
        )
    return _mail_relay


def _mail_transport_for_tick():
    return _mail_transport.choose_transport(_get_mail_relay())


def _mail_transport_info():
    return _mail_transport.describe(relay_configured=_mail_relay_configured(), relay=_get_mail_relay())


def _get_or_create_notifications_worker(company_id):
    if company_id not in _notifications_workers:
        _notifications_workers[company_id] = EmailOutboxWorker(
            conn_factory=get_retail_conn,
            company_id=company_id,
            transport_factory=_mail_transport_for_tick,
        )
    return _notifications_workers[company_id]


def _start_notifications_worker_once(company_id, interval_seconds):
    if company_id in _mail_workers_started:
        return
    _mail_workers_started.add(company_id)
    _get_or_create_notifications_worker(company_id).start(interval_seconds=interval_seconds)


# "Can mail leave this install at all" for the shop-alert gate
# (notifications.settings.is_enabled) and for account mail: own SMTP, or an
# Owner URL to relay through. Activation is NOT required here -- mail queued
# before activation waits and goes out after it.
_notification_settings.set_transport_probe(_mail_relay_configured)

# Verification / password-reset mail is queued, not sent inline (see
# commercial_runtime/identity/verification.py), and its worker runs whenever a
# transport exists, whatever the company's notifications toggle says.
_identity_verification.set_mail_sink(IdentityMailSink(
    conn_factory=get_retail_conn,
    possible=_mail_transport_possible,
    on_queued=lambda company_id: _start_notifications_worker_once(company_id, _ACCOUNT_MAIL_TICK_SECONDS),
))

app.register_blueprint(make_notifications_blueprint(
    conn_factory=get_retail_conn,
    get_worker=_get_or_create_notifications_worker,
    transport_info=_mail_transport_info,
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
        # The deferred THIRD HALF of `_on_licence_activated` (see its
        # docstring): an activation that landed in split state deliberately
        # did NOT hot-start sync or the hub election, because the till was
        # refusing to serve retail data. This is the moment that reason ends,
        # and the refusal can only ever have been entered BY an activation,
        # so this is still "right after activation" -- just later. Without
        # this call (found by the adversarial verifier, 2026-09-24,
        # retail_sync_hot_start_after_activation_test.py T13) the till went
        # back to normal service with sync and the LAN hub silently off
        # until the next restart -- exactly the restart-only gap
        # `_start_sync_after_activation` exists to close. Safe here: it never
        # raises, is a no-op once the pair is built (its own lock and
        # `_sync_service is None` check), and the site relay has its own
        # double-start guard; and this branch runs once per refusal, since
        # the next request short-circuits on `reason is None` above.
        _announce_device('changed')   # the deferred form of the same announce
        _start_sync_after_activation()
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

    THIRD HALF, retail-hardware-viewports: `_start_sync_after_activation()`
    (defined below, alongside the sync-service construction it hot-starts),
    called ONLY when the split-state check just above found nothing wrong.
    A till that is refusing every request because retail.db could not
    converge onto the Owner-issued company_id must not simultaneously start
    pushing/pulling that same not-yet-trustworthy database in the
    background -- the split-state refusal exists precisely so nothing reads
    or writes retail data until convergence is confirmed, and a sync loop
    is exactly such a reader/writer. DEFERRED, NOT DROPPED: the request that
    later finds convergence and clears the refusal (`_refuse_while_split_
    state`) makes this same call, so a split-state activation still ends with
    sync and the hub election running -- without a restart.
    """
    reason = _detect_split_state_reason()
    if reason is not None:
        _enter_split_state_refusal(reason)
    else:
        _clear_split_state_refusal()
        # Activation is one of the two moments a company comes into being on
        # this install (the other is signup -- create_admin). A JOINING
        # computer activates with the shop's key before any admin exists here,
        # so nothing else would announce it: the boot announce ran before
        # there was a company, and there is no SyncService heartbeat yet. Do
        # it BEFORE sync starts so the event rides the very first push.
        # `_announce_device` never raises.
        _announce_device('changed')
        _start_sync_after_activation()


from commercial_runtime.identity.device_context import peek_local_device_uuid as _peek_registry_device_id  # noqa: E402

from commercial_runtime.install_support.firewall import (
    with_firewall_ask_after_activation as _with_firewall_ask_after_activation)
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
    # INSTALL-1 (verifier M3): after the product's own bookkeeping, ask ONCE for
    # the Windows Firewall rule (a no-op off an installed Windows build) -- the
    # last deliberate moment before the hub first binds the LAN port on the
    # next boot and Windows shows its own prompt at the till.
    on_activation_success=_with_firewall_ask_after_activation(
        _on_licence_activated, str(Path(DATABASE_DIR).parent)),
    # AUDIT-033 fix round (verifier V finding #1): tell Owner which registry
    # device id this computer claims, at activation and every check-in, so
    # Owner can pin it and sign it into the lease (receivers trust a signer's
    # id only when its lease attests it). PEEK, never create: minting an
    # identity as a side effect of a licence call is the AUDIT-032D bug, and a
    # computer with no id yet simply sends none.
    registry_device_id_provider=_peek_registry_device_id,
))

# Update channel (Windows desktop only). The SAME licensing directory, licence
# database, trust store and device identity the blueprint above uses -- one
# identity, so Owner's device-signed discovery verifies as the installation it
# already knows. With no Owner URL (the default) `_update_service` is None:
# /api/updates/status answers enabled=false, the actions answer 409, no
# scheduler is built and no network call can ever be made. Android is excluded
# on purpose: its device key lives in Kotlin and its installer path differs,
# so it is a later wave, not a half-working one here.
# BACKUP-1 (scheduler wiring only; the logic lives in commercial_runtime/backup).
# Same folder the /api/backup blueprint above defaults to, so the automatic
# backups appear in the same list the Settings screen shows.
from commercial_runtime.backup import schedule as _backup_schedule

_BACKUP_APP_DATA_DIR = str(Path(DATABASE_DIR).parent)
_BACKUP_DIR = os.environ.get('AURA_BACKUP_DIR') or os.path.join(_BACKUP_APP_DATA_DIR, 'backups')


def _backup_day_key(now_utc):
    """The shop's BUSINESS day for `now_utc`, so 'once per day' means the
    trading day the owner reports on (a shop that closes at 01:30 is not backed
    up twice in one night). Uses retail's own business_now() on the first
    company that has declared a clock; with none declared it is the device's
    calendar date. Any failure falls back to that date -- a wrong day key can
    only cost a duplicate backup, never a missed one (the 24 h rule backs it)."""
    try:
        from database.schema import get_retail_conn
        from core.retail import metrics as _metrics
        conn = get_retail_conn()
        try:
            row = conn.execute("SELECT company_id FROM retail_settings LIMIT 1").fetchone()
            if row:
                return _metrics.business_now(conn, row[0], now_utc.astimezone()).date().isoformat()
        finally:
            conn.close()
    except Exception:
        pass
    return now_utc.astimezone().date().isoformat()


_backup_scheduler = (
    _backup_schedule.BackupScheduler(
        'retail', _BACKUP_APP_DATA_DIR, _BACKUP_DIR, APP_VERSION,
        settings=_backup_schedule.BackupSettings(_BACKUP_APP_DATA_DIR),
        status=_backup_schedule.BackupStatus(_BACKUP_APP_DATA_DIR),
        day_key_fn=_backup_day_key)
    # Desktop only: on Android the backup folder is the app sandbox, so an
    # "automatic" copy there would die with the phone (that half is the
    # Save/Share buttons in BackupRestoreScreen).
    if LICENSING_PLATFORM != 'ANDROID' else None
)

_update_service = build_update_service(
    app_data_dir=str(Path(DATABASE_DIR).parent),
    owner_base_url=OWNER_LICENSING_BASE_URL if LICENSING_PLATFORM != 'ANDROID' else '',
    verify_tls=OWNER_LICENSING_VERIFY_TLS,
    timeout_seconds=OWNER_LICENSING_TIMEOUT_SECONDS,
    device_identity_factory=_licensing_device_identity_factory,
    release_channel='rc',
    app_version=APP_VERSION,
    # No safety copy, no install (see UpdateService.install).
    pre_install_backup=_backup_schedule.make_pre_update_backup(
        'retail', _BACKUP_APP_DATA_DIR, _BACKUP_DIR, APP_VERSION),
)
_update_scheduler = UpdateScheduler(_update_service) if _update_service is not None else None
app.register_blueprint(make_updates_blueprint(_update_service, current_version=APP_VERSION))

# Android half of the update channel: Kotlin (which holds the device key) makes
# the signed Owner call and hands the RAW answer here; this process verifies it
# against ITS trust store and ITS licence state's installation id and returns
# the only verdict Kotlin believes. Registered only on Android with the shared
# secret, exactly like the licensing /_internal routes -- on Windows the URL
# does not exist (404). Same licensing dir and licence database the licensing
# blueprint and build_update_service use: one trust set, one identity.
if LICENSING_PLATFORM == 'ANDROID' and LICENSING_INTERNAL_SHARED_SECRET:
    from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository as _UpdLicenseStateRepository
    from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore as _UpdOwnerTrustStore
    from commercial_runtime.updates.internal_routes import make_internal_updates_blueprint

    _upd_app_data_dir = Path(DATABASE_DIR).parent
    app.register_blueprint(make_internal_updates_blueprint(
        shared_secret=LICENSING_INTERNAL_SHARED_SECRET,
        trust_store_factory=lambda: _UpdOwnerTrustStore(_upd_app_data_dir / 'licensing' / 'trust_store.json'),
        state_repository_factory=lambda: _UpdLicenseStateRepository(
            _upd_app_data_dir / 'database' / 'subsystems' / 'licensing.db'),
        app_version=APP_VERSION,
    ))

# Crash reports. CAPTURE is always on and purely local: a crash on the server
# or in a background thread is written to a small bounded SQLite queue
# (<app_data>/database/subsystems/crash_reports.db, never retail.db), so a shop
# with no Owner still keeps a record of what broke. SENDING is opt-in: the
# sender exists only when an Owner URL is configured, and (like the update
# channel) not on Android, whose device key lives in Kotlin -- a later wave.
# With no Owner URL `_crash_sender` is None, no client is ever built and
# nothing leaves the till. Everything the sender transmits has first been
# through commercial_runtime/crash_reports/scrub.py (no names, amounts, keys
# or home folders). A store that cannot be opened turns capture off for this
# run rather than stopping the till booting.
_crash_store = open_crash_store(str(Path(DATABASE_DIR).parent))
_crash_sender = build_crash_report_sender(
    store=_crash_store,
    app_data_dir=str(Path(DATABASE_DIR).parent),
    owner_base_url=OWNER_LICENSING_BASE_URL if LICENSING_PLATFORM != 'ANDROID' else '',
    verify_tls=OWNER_LICENSING_VERIFY_TLS,
    timeout_seconds=OWNER_LICENSING_TIMEOUT_SECONDS,
    device_identity_factory=_licensing_device_identity_factory,
    product_code='AURA_RETAIL',
    app_version=APP_VERSION,
    platform=LICENSING_PLATFORM,
)
if _crash_store is not None:
    crash_capture.install(app, _crash_store, app_version=APP_VERSION, platform=LICENSING_PLATFORM)
def _send_crash_reports_now():
    """SUPPORT-1: right after a shopkeeper presses "Send diagnostics to support",
    try one send in the background instead of making them wait up to 30 minutes
    for the sender's next tick. A thread of its own so the button answers at
    once; run_once_now never raises past its own handling, leaves the schedule's
    back-off alone, and if the till is offline the row simply stays queued."""
    if _crash_sender is None:
        return
    threading.Thread(target=_crash_sender.run_once_now, name="crash-report-send-now", daemon=True).start()


app.register_blueprint(make_crash_reports_blueprint(
    _crash_store, app_version=APP_VERSION, platform=LICENSING_PLATFORM,
    sending_enabled=_crash_sender is not None,
    on_diagnostics_recorded=_send_crash_reports_now))


def _start_crash_report_sender():
    """Boot seam (init_app): start the background send. Never raises -- a
    broken reporter must not stop a till booting. A no-op with no Owner URL."""
    if _crash_sender is None:
        return
    try:
        _crash_sender.start()
    except Exception:
        logging.getLogger(__name__).warning(
            'Crash-report sender failed to start; this till keeps booting.', exc_info=True)


def _start_update_polling():
    """Boot seam (init_app): tidy the updates folder, then arm the daily
    check. Never raises -- a broken update channel must not stop a till
    booting. A no-op when updates are off (no Owner URL)."""
    if _update_service is None or _update_scheduler is None:
        return
    try:
        _update_service.startup()
        _update_scheduler.start()
    except Exception:
        logging.getLogger(__name__).warning(
            'Update channel failed to start; this till keeps booting.', exc_info=True)


def _start_backup_scheduler():
    """Boot seam (init_app): arm the automatic backup. Never raises -- a broken
    scheduler must not stop a till booting. No-op on Android."""
    if _backup_scheduler is None:
        return
    # AURA_BACKUP_SCHEDULER=0 is the operator/test kill switch, the same shape
    # as AURA_SITE_RELAY_ENABLED=0. Unlike updates and crash reports (no-ops
    # without an Owner URL), the backup timer arms on EVERY boot by design --
    # so every in-process test app armed a real timer thread, and a process-
    # level thread check (retail_einvoicing_regression_test) failed on it
    # (measured 2026-10-12). run_all_tests.py defaults it off for children;
    # retail_backup_scheduler_boot_test.py proves a normal boot still arms it.
    # Logged, so "never started" is never silent.
    if os.environ.get('AURA_BACKUP_SCHEDULER', '').strip() == '0':
        logging.getLogger(__name__).info(
            'Backup scheduler NOT started: AURA_BACKUP_SCHEDULER=0.')
        return
    try:
        _backup_scheduler.start()
    except Exception:
        logging.getLogger(__name__).warning(
            'Backup scheduler failed to start; this till keeps booting.', exc_info=True)


# Multi-device sync foundation (2026-08-06), Task 5: the background push/pull
# loop. Reuses the EXACT same licensing_dir/db_path/device-identity factory
# make_licensing_blueprint's own _build_context() constructs above -- never a
# second device identity, never a second on-disk key -- so the signature
# this client produces verifies against the SAME installation Owner already
# knows from activation/check-in.
_sync_service = None
_registry_sync_service = None

# Hub-self-sync wave (retail-hardware-viewports, 2026-09-24): the seam that
# routes a hub's OWN two SyncService instances through its OWN site log
# instead of Owner's cloud relay -- see `_enter_hub_self_sync`'s own
# docstring for the full defect this closes and the measurement that found
# it (hub site_sync_events=17, forwarded=0, cloud seq=0, a real phone's
# sales never reaching `/sales/recent`).
#
#   * `_sync_relay_selector` -- the `commercial_runtime.sync.site_relay.
#     local_client.RelaySelector` BOTH `_sync_service`/`_registry_sync_
#     service` share as their `client_factory`, built once by `_build_
#     windows_sync_services` alongside the pair itself. `None` on every
#     till that has never had a cloud-or-local sync pair built at all (the
#     pre-activation window) -- see `_enter_hub_self_sync`'s own "sync pair
#     exists but was not built by this function" guard for the one case
#     that would otherwise be a silent no-op.
#   * `_site_forwarder_loop` -- the single `commercial_runtime.sync.
#     site_relay.forwarder_loop.ForwarderLoop` this process ever
#     constructs. Started once, by `_enter_hub_self_sync`, and NEVER
#     stopped again for the lifetime of the process -- see that function's
#     own docstring and `election.py`'s "WHY STANDING DOWN LOSES NOTHING"
#     for why a stand-down must not stop it.
#   * `_site_housekeeping_stop` -- the stop `threading.Event` `listener.
#     start_housekeeping` returns, recorded here (rather than left to
#     `start_site_relay`'s own internal, unreturned timer -- see
#     `_bind_relay_now`'s own updated docstring) so re-election never starts
#     a second housekeeping loop: `_enter_hub_self_sync` is the ONE place
#     housekeeping is ever started now, with the hub's own consumer floors
#     wired in from the very first sweep.
_sync_relay_selector = None
_site_forwarder_loop = None
_site_housekeeping_stop = None

# Matches SyncService's own default tick and `forwarder_loop.
# FORWARD_INTERVAL_SECONDS` -- passed explicitly (never left to the
# module-level default) so a test can shrink it via
# `monkeypatch.setattr(_app_module, 'SITE_FORWARDER_INTERVAL_SECONDS', ...)`
# the same way `retail_sync_hot_start_after_activation_test.py` already
# does for other timers in this file, without reaching into forwarder_
# loop.py's own module constant.
SITE_FORWARDER_INTERVAL_SECONDS = 10.0


def _ensure_runtime_schema_for_sync():
    """Boot-time seam (retail-hardware-viewports): runs retail_api.py's own
    lazy `_ensure_credit_schema(conn)` migration BEFORE any SyncService or
    hub self-sync is started, so a freshly-joined device's very first pull
    batch never depends on a UI route having run first.

    THE DEFECT THIS CLOSES, measured 2026-09-25 on a real fresh till that
    joined an existing shop through Owner: every sync pull failed with
    `sqlite3.OperationalError: no such table: retail_settings` at
    commercial_runtime/sync/sync_service.py's `_apply_event`
    (`entity_type == "retail_setting"` branch, ~line 1838), reached via
    `apply_pull_result -> pull_once`. The shop had changed one setting (base
    currency JOD), so the pulled batch carried one `retail_setting` event
    next to 6 `product` events; the batch is applied inside a single
    transaction, so that one missing-table error rolled back the WHOLE
    batch, the cursor never advanced, and the new device received NO
    products either (`sync_cursor.last_seq` stayed 0, "consecutive
    failures=5"). `retail_settings` -- like `payment_methods` and
    `doc_sequences` beside it -- is created only lazily, by
    `api/retail_api.py::_ensure_credit_schema(conn)`, the first time an HTTP
    route that calls it happens to run (see that function's own
    docstring: "Idempotent migration. Extends the existing retail tables;
    safe on live data."). Opening a settings screen on that same till
    created the table, and the NEXT pull then applied the whole backlog
    correctly -- so the till self-healed, but only because some UI screen
    happened to hit that route before the sync timer's next tick. A
    headless till, or a phone that never opens a settings screen before its
    first background sync fires, stays wedged forever: the sync loop's
    timer runs on its own schedule, completely independent of any route
    ever being hit.

    This is the SAME shape of bug `SyncService`'s own `local_ensure_schema`
    constructor hook (commercial_runtime/sync/sync_service.py) was already
    built to close for `sales.due_date` and most of `payments`' AR/AP
    columns (Launch-readiness Phase 5) -- but that hook only fires from
    INSIDE `apply_pull_result`, and only when the batch carries a
    sale/return/payment create or update (see that method's own comment,
    "scoped narrower than the company_id fetch just above on purpose").
    `retail_setting` was never in that tuple, so a settings-plus-catalogue
    batch -- exactly what a freshly-joined device receives first -- never
    triggered it. Widening that tuple would only have closed THIS one gap;
    the next lazily-created table would reopen the identical failure with a
    different entity_type. This seam instead runs the same idempotent
    ensure unconditionally, at boot and at both places sync/hub-self-sync
    can start, so by the time a pull is even attempted the schema is simply
    already there, regardless of what that first batch contains -- see
    `_ensure_credit_schema`'s own docstring for why it is safe to call
    unconditionally against a live database.

    NEVER RAISES -- same contract as every neighbouring activation-adjacent
    seam in this module (`_start_sync_after_activation`,
    `_start_site_relay_if_enabled`, `_enter_hub_self_sync`): a schema hiccup
    here must never stop a till from booting or from starting sync for
    reasons that have nothing to do with sync itself.

    Idempotent and cheap to call from all three sites that use it below:
    `_ensure_credit_schema`'s own `_CREDIT_SCHEMA_READY` module flag makes
    every call after the first a single in-process boolean check."""
    try:
        conn = get_retail_conn()
        try:
            _ensure_credit_schema(conn)
        finally:
            conn.close()
    except Exception:
        logging.getLogger(__name__).exception(
            'Runtime schema ensure (retail_settings/payment_methods/'
            'doc_sequences/AR-AP columns) failed unexpectedly before sync '
            'start -- never fatal, but a device in this state can still '
            'hit "no such table" on its first pull until this succeeds.')


def _retail_bootstrap_hooks():
    """`BootstrapHooks` for the RETAIL stream's SyncService (SYNC-BOOT Phase 2). A function, and defined
    BEFORE `_build_windows_sync_services`, so the test harness builds a device's services with exactly
    the hooks production does. Every body is a lambda: this runs at IMPORT time, before the helpers
    further down this module exist; they resolve when called."""
    from commercial_runtime.sync.bootstrap.hooks import BootstrapHooks
    return BootstrapHooks(
        # `bootstrap_producer_v1` = "I act on bootstrap requests a relay piggybacks on my pull".
        # `bootstrap_v1` = "I can fetch a copy of the shop from Owner's mailbox" -- true since
        # SYNC-BOOT Phase 3 (transport_owner.OwnerRequester); the Phase 4 retention gate reads it.
        caps=('bootstrap_v1', 'bootstrap_producer_v1'),
        include_own_once=lambda: _bootstrap_include_own_pending('retail'),
        include_own_done=lambda: _bootstrap_include_own_done('retail'),
        reapply_outbox_pending=lambda: _bootstrap_reapply_pending(),
        reapply_outbox_done=lambda: _bootstrap_reapply_done(),
        on_resync_required=lambda stream, client, relay_url, details: _bootstrap_on_resync(
            stream, client, relay_url, details),
        on_bootstrap_requests=lambda requests: _bootstrap_on_requests(requests),
        on_mailbox_requests=lambda requests, client, relay_url: _bootstrap_on_mailbox_requests(
            requests, client, relay_url))


def _registry_bootstrap_hooks():
    """`BootstrapHooks` for the REGISTRY stream: only include_own_once (a restore replaces registry.db
    too). Its RESYNC_REQUIRED needs no hook: the service fast-forwards by itself."""
    from commercial_runtime.sync.bootstrap.hooks import BootstrapHooks
    return BootstrapHooks(
        include_own_once=lambda: _bootstrap_include_own_pending('registry'),
        include_own_done=lambda: _bootstrap_include_own_done('registry'))


def _build_windows_sync_services(relay_base_url):
    """Constructs the Windows sync-service pair -- everything the `if
    _SYNC_RELAY_URL_IS_USABLE and LICENSING_PLATFORM != 'ANDROID':` block
    below used to build inline, before retail-hardware-viewports extracted
    it into its own function.

    WHY A FUNCTION, TAKING THE URL AS A PARAMETER (not reading the module-
    level `SYNC_RELAY_BASE_URL` constant the way this code used to): so the
    exact same construction logic can run a SECOND time, later in the same
    process, with a FRESH url a just-completed activation just produced --
    see `_start_sync_after_activation()` further below, which is the only
    other caller. `SYNC_RELAY_BASE_URL` itself is frozen at import (config.py
    reads it once); a fresh till has no value there yet no matter how many
    times it is re-read, which is precisely the bug this parameter exists to
    avoid reintroducing.

    Returns (sync_service, registry_sync_service). The caller -- either the
    import-time call just below, or `_start_sync_after_activation()` -- is
    responsible for assigning them to the `_sync_service`/
    `_registry_sync_service` module globals and calling
    `register_active_service()`, exactly as the inline block this replaces
    used to do directly, so BOTH call sites do that step identically rather
    than this function guessing at it for either one.

    `_build_sync_client` is deliberately kept reachable as a genuine module
    attribute via `global` below (not merely a name closed over inside this
    function): retail_sync_starts_when_configured_test.py reads
    `_app_module._build_sync_client()` directly, and that must keep
    resolving at module level after this extraction exactly as it did when
    this code lived inline at import time. No other name this function
    defines (`_sync_licensing_dir`, `_sync_state_repository`, `_sync_signer`,
    `_sync_get_conn`, `_retail_sync_freshness_store`,
    `_registry_sync_get_conn`) was found referenced as an `app.<name>`
    attribute anywhere in products/, commercial_runtime/, scripts/ or any
    test file (grepped before this extraction), so those stay purely local
    to this function -- behaviourally identical, just no longer visible on
    the module for anything to reach in.
    """
    from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository
    from commercial_runtime.sync.relay_client import SyncRelayClient
    from commercial_runtime.sync.site_relay.local_client import LocalSiteRelayClient, RelaySelector
    from commercial_runtime.sync.sync_service import (
        REGISTRY_SYNC_ENTITY_TYPES,
        SyncFreshnessStore,
        SyncService,
        local_company_id_from_registry,
    )

    _sync_licensing_dir = Path(DATABASE_DIR).parent / 'licensing'
    _sync_state_repository = LicenseStateRepository(Path(DATABASE_DIR) / 'subsystems' / 'licensing.db')
    _sync_signer = _licensing_device_identity_factory(_sync_licensing_dir)

    global _build_sync_client, _sync_relay_selector

    def _build_sync_client():
        # Rebuilt on every call (not cached at startup) for the same reason
        # make_licensing_blueprint's _build_context() rebuilds its own
        # per-request object graph: the installation_id this client signs
        # with must reflect activation happening AFTER process start, not a
        # None/stale value captured once at import time.
        record = _sync_state_repository.load()
        installation_id = record.owner_installation_id if record else None
        return SyncRelayClient(
            relay_base_url,
            _sync_signer,
            installation_id,
            timeout_seconds=SYNC_RELAY_TIMEOUT_SECONDS,
            verify_tls=SYNC_RELAY_VERIFY_TLS,
        )

    def _sync_get_conn():
        from database.schema import get_retail_conn
        return get_retail_conn()

    def _build_local_site_client():
        # Hub-self-sync (retail-hardware-viewports): the LOCAL half of
        # `_sync_relay_selector` below, called fresh on every attempt
        # exactly like `_build_sync_client` above -- this hub's own
        # `owner_installation_id` can change across a licence re-issue
        # (`rehearsal_reissue.py --keep-device`; see MEMORY.md), and a
        # cached value here would keep stamping site-log rows under a
        # stale origin id forever.
        record = _sync_state_repository.load()
        installation_id = record.owner_installation_id if record else None
        return LocalSiteRelayClient(get_conn=_sync_get_conn, origin_device_id=installation_id)

    # `RelaySelector` is the client_factory BOTH SyncService instances
    # below share -- see local_client.py's own docstring for why this is
    # the seam that flips a hub's own sync from Owner's cloud to its own
    # site log. `cloud_factory=_build_sync_client if relay_base_url else
    # None`: a LAN-only shop (this till's own DECISION D2/A5 path --
    # `_enter_hub_self_sync` builds this pair with `relay_base_url=''`)
    # must never try to build a `SyncRelayClient` against an empty base
    # URL; the selector's own `NO_RELAY_CONFIGURED` error is what a caller
    # sees instead if CLOUD mode is ever reached before `use_local()` has
    # run, which `_enter_hub_self_sync` guarantees never happens (it flips
    # the selector to LOCAL before registering/starting either service --
    # see that function's own docstring).
    _sync_relay_selector = RelaySelector(
        cloud_factory=_build_sync_client if relay_base_url else None,
        local_factory=_build_local_site_client,
    )

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
    # `local_schema_version_provider` ARMS THE SCHEMA-SKEW GUARD, which is
    # inert without it. Until 2026-09-22 nothing anywhere checked a schema
    # version before applying a pulled row, so a v38 till and a v39 till
    # synced happily and the OLDER one silently dropped every column it had
    # never migrated -- no error, no warning, permanent disagreement. That
    # was found the hard way when three v39 columns travelled correctly and
    # still failed to converge.
    #
    # A callable, not the bare integer: it is read at APPLY time, so a
    # process that migrates mid-run reports the version it actually has
    # rather than the one it booted with.
    #
    # Retail's number belongs on the two services that read and write
    # retail.db. It is deliberately NOT passed to `_registry_sync_service`
    # below -- that one syncs `users` into registry.db, which carries its
    # OWN schema version on its own release cadence, and stamping retail's
    # integer onto it would quarantine perfectly good user rows every time
    # retail alone bumped.
    _sync_service = SyncService(_sync_relay_selector, _sync_get_conn, local_company_id_from_registry,
                                local_ensure_schema=_ensure_credit_schema,
                                local_freshness_store=_retail_sync_freshness_store,
                                local_schema_version_provider=lambda: RETAIL_SCHEMA_VERSION,
                                stock_exception_recorder=record_or_refresh_stock_exception,
                                stream_label='retail')  # SYNC-BOOT: Owner keeps one cursor per stream
    # F3b: a payment voided on this till BEFORE its journal arrived is only
    # completed by a sweep that runs after every pull. sync_service.py has no
    # post-apply hook, so the sweep is wrapped onto this instance. The
    # registry stream below never carries GL journals and is not wrapped.
    install_void_reversal_sweep(_sync_service)
    # v44: re-derive the customers'/suppliers' balance cache from the shop-wide
    # rows a pull just brought in (a receive-only computer otherwise keeps the
    # figure it had before the pull).
    install_party_balance_refresh(_sync_service)

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

    # `on_tick` is the device HEARTBEAT (registry v9): this stream's own 10s
    # timer re-runs the announcer, which decides for itself whether the last
    # announcement is old enough to repeat. Passed ONLY here -- the retail
    # stream has no business announcing devices.
    _registry_sync_service = SyncService(
        _sync_relay_selector, _registry_sync_get_conn, local_company_id_from_registry,
        handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES,
        # Late-bound: this runs at IMPORT time (the `if _SYNC_RELAY_URL_IS_USABLE`
        # block below), long before `_announce_heartbeat_device` is defined.
        on_tick=lambda: _announce_heartbeat_device(),
        stream_label='registry')  # SYNC-BOOT: Owner keeps one cursor per stream

    # SYNC-BOOT Phase 2: the bootstrap seams (see `_retail_bootstrap_hooks`).
    _sync_service.set_bootstrap_hooks(_retail_bootstrap_hooks())
    _registry_sync_service.set_bootstrap_hooks(_registry_bootstrap_hooks())

    return _sync_service, _registry_sync_service


if _SYNC_RELAY_URL_IS_USABLE and LICENSING_PLATFORM != 'ANDROID':
    # register_active_service imported here, on its own, rather than inside
    # _build_windows_sync_services above: that function's own imports cover
    # only what ITS body needs, and register_active_service is called here,
    # at the call site, not inside it -- see that function's docstring for
    # why the two calls to it (this one, and _start_sync_after_activation's)
    # both do this step themselves instead of it guessing at it for either.
    from commercial_runtime.sync.sync_service import register_active_service

    _sync_service, _registry_sync_service = _build_windows_sync_services(SYNC_RELAY_BASE_URL)
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
    from commercial_runtime.sync.sync_service import (
        REGISTRY_SYNC_ENTITY_TYPES,
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
    # this instance's `_sync_get_conn` is retail.db, so it carries the
    # `sync_freshness` table (schema v18) exactly like the Windows retail
    # instance does, and gets the same collaborator -- including
    # `record_offline_override` (schema v19, stage 7c-ii; see the Windows
    # branch above).
    # stock_exception_recorder: same reasoning as the Windows branch above
    # -- this instance's `_sync_get_conn` is retail.db too, so it carries
    # `stock_exceptions` (schema v20) exactly like the Windows instance
    # does, and gets the same collaborator.
    _android_sync_freshness_store = SyncFreshnessStore(
        load=load_sync_freshness, record=record_sync_freshness, record_override=record_offline_override)
    # Same schema-skew arming as the Windows branch above, and it matters
    # MORE here: a handset is the device most likely to be running a build
    # older than the till's, because a phone updates on its owner's whim
    # while a Windows till is updated deliberately. An un-armed handset is
    # exactly the "older peer silently drops columns it never migrated"
    # case the guard exists for.
    _android_sync_service = SyncService(None, _sync_get_conn, local_company_id_from_registry,
                                        local_ensure_schema=_ensure_credit_schema,
                                        local_freshness_store=_android_sync_freshness_store,
                                        local_schema_version_provider=lambda: RETAIL_SCHEMA_VERSION,
                                        stock_exception_recorder=record_or_refresh_stock_exception,
                                        stream_label='retail')  # client_factory never used -- see comment above
    # F3b: same post-pull void-reversal sweep as the Windows retail service.
    install_void_reversal_sweep(_android_sync_service)
    install_party_balance_refresh(_android_sync_service)  # v44, same as the Windows service
    app.register_blueprint(make_sync_internal_blueprint(
        sync_service=_android_sync_service,
        get_conn=_sync_get_conn,
        state_repository=_sync_state_repository,
        shared_secret=LICENSING_INTERNAL_SHARED_SECRET,
    ))

    # Sync backend follow-up (Android registry stream): the ANDROID mirror of
    # the Windows branch's `_registry_sync_service` above -- see that
    # instance's own comment block for the full "Two-stream design" reasoning
    # (sync_service.py's module docstring) and the exact bug this closes (a
    # `user`/`user_permission` event arriving from Kotlin's pull loop had no
    # instance gated to apply it at all: the ONE Android SyncService built
    # above is `RETAIL_SYNC_ENTITY_TYPES`-gated, which excludes both, so
    # `_apply_event` silently dropped them, cursor still advancing past --
    # see that gate's own comment in sync_service.py).
    #
    # `get_conn` is registry.db's own `get_conn`, never `_sync_get_conn`
    # above -- exactly the same registry.db-vs-retail.db split the Windows
    # branch's `_registry_sync_get_conn` makes, for the identical reason:
    # `users`/`user_permissions` live in registry.db, not retail.db.
    #
    # `client_factory=None`, exactly like `_android_sync_service` above --
    # this process never holds the device signing key (Kotlin does; see the
    # branch-opening comment), so nothing here ever calls push()/pull()
    # itself. `.start()` is deliberately never called (mirrors
    # `_android_sync_service`, and `init_app()`'s own docstring on why: a
    # started timer would call this `client_factory` and crash on the first
    # tick), and this instance is deliberately never passed to
    # `register_active_service()` either -- same reasoning as the Windows
    # `_registry_sync_service`: that global slot must stay pointed at
    # whatever drains the RETAIL outbox (on Android, nothing -- Kotlin's
    # SyncCoordinator drains both databases' outboxes itself via the two
    # `/_internal/sync/*` blueprints), never silently redirected.
    #
    # `local_ensure_schema`/`local_freshness_store`/`stock_exception_recorder`
    # are all deliberately omitted, mirroring the Windows `_registry_sync_
    # service` exactly and for the identical reason -- `_ensure_credit_
    # schema`, `sync_freshness` (schema v18) and `stock_exceptions` (schema
    # v20) are all retail.db-only concerns (AR/AP columns, retail's own
    # freshness/stock-exception tables). registry.db has none of them, and
    # this instance's apply loop never reaches a branch that would need them
    # anyway: `inventory_movement` (the only entity type that ever touches
    # `local_ensure_schema`/the freshness store/the stock-exception
    # recorder) is not in `REGISTRY_SYNC_ENTITY_TYPES`.
    #
    # Registered under its OWN blueprint name/url_prefix -- Flask refuses two
    # blueprints sharing a name, or two routes sharing a prefix, on the same
    # app, and `_android_sync_service`'s blueprint above already claimed the
    # defaults (`"sync_internal"` / `/api/sync`).
    def _android_registry_sync_get_conn():
        from commercial_runtime.identity.registry_db import get_conn as _registry_get_conn
        return _registry_get_conn()

    _android_registry_sync_service = SyncService(
        None, _android_registry_sync_get_conn, local_company_id_from_registry,
        handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES,
        stream_label='registry')  # client_factory never used -- see comment above
    app.register_blueprint(make_sync_internal_blueprint(
        sync_service=_android_registry_sync_service,
        get_conn=_android_registry_sync_get_conn,
        state_repository=_sync_state_repository,
        shared_secret=LICENSING_INTERNAL_SHARED_SECRET,
        blueprint_name="registry_sync_internal",
        url_prefix="/api/registry-sync",
    ))


# A GENUINE lock, not merely a defensive comment: `_start_sync_after_
# activation` below is reachable from a real HTTP request thread
# (`/api/licensing/activate`) and could in principle be re-entered by a
# near-simultaneous second activation attempt (a retried request, a second
# browser tab) racing the "if _sync_service is None" check against its own
# assignment. Cheap insurance -- activation is not a hot path -- against
# building two independent SyncService pairs on the same till, which would
# double every push/pull tick and, worse, leave only the LAST one registered
# via register_active_service() while the first kept ticking unseen.
#
# RLock, not Lock (hub-self-sync wave, retail-hardware-viewports): `_start_
# sync_after_activation` holds this lock for its ENTIRE body (see step 10 in
# its own docstring below), which now calls `_start_site_relay_if_enabled()`
# -> (on the explicit-pin or LAN-only path) `_bind_relay_now()` ->
# `_enter_hub_self_sync()`, which takes THIS SAME LOCK ITSELF (see that
# function's own docstring for why: it must never race a second `_start_
# sync_after_activation` call into building two selectors). A plain `Lock`
# would deadlock the very thread that already holds it the instant that
# call chain runs -- an `RLock` lets the same thread re-enter. Nothing here
# ever waits on a DIFFERENT thread while holding this lock (no I/O that
# blocks on another thread's own progress), so there is no cross-thread
# deadlock hazard from widening it to reentrant -- only the same-thread
# re-entry this call chain now requires.
_start_sync_after_activation_lock = threading.RLock()


def _hub_housekeeping_floors() -> list:
    """SYNC-JOIN (2026-10-13): the floors the hub's hourly housekeeping
    prunes by. A leading 0 holds the watermark at 0, so NO site_sync_events
    row is ever deleted (replay nonces still expire): a device paired with
    this hub later pulls the log from 0 and needs the shop's whole history,
    and nothing re-sends it once pruned (measured on the cloud relay by
    owner/tests/test_sync_late_join_after_prune_cross_stack.py; the hub
    prunes by the same rule). Drop the 0 only when a bootstrap/snapshot path
    exists. retail_sync_join_hub_floor_test.py pins it."""
    return [0, *_hub_own_consumer_floors()]


def _hub_own_consumer_floors() -> list:
    """This hub's OWN two sync streams' consumption positions into its own
    site log (`local_client.consumer_floor`, retail.db AND registry.db),
    for `listener.run_housekeeping_sweep`'s `consumer_floor_provider` --
    see `pruning.py`'s module docstring, reader #3, for why housekeeping
    must never prune a row this hub's own SyncService pair has not applied
    yet.

    May raise (a locked database, a missing table on a till that has never
    built the registry stream) -- the housekeeping sweep's own contract
    (`run_housekeeping_sweep`'s docstring) is to treat a raising provider as
    "prune nothing this hour, log once", so this function is deliberately
    NOT wrapped in its own try/except: doing so here would silently turn a
    real failure into an empty list (floor 0 everywhere, i.e. "nothing
    consumed yet"), which is the WRONG direction for a function whose
    caller already has the correct fail-safe behaviour for an exception."""
    from commercial_runtime.identity import registry_db
    from commercial_runtime.sync.site_relay.local_client import consumer_floor
    from database.schema import get_retail_conn

    retail_conn = get_retail_conn()
    try:
        retail_floor = consumer_floor(retail_conn)
    finally:
        retail_conn.close()

    registry_conn = registry_db.get_conn()
    try:
        registry_floor = consumer_floor(registry_conn)
    finally:
        registry_conn.close()

    return [retail_floor, registry_floor]


def _current_owner_installation_id() -> Optional[str]:
    """A fresh read of this till's own `owner_installation_id`, or `None` on
    any failure (unactivated, a locked/missing licensing.db, a corrupt
    row) -- never raises. Mirrors `_start_site_relay_if_enabled`'s own
    "reading the licence must never be able to stop this till from booting"
    contract, applied to the one seam that decides whether hub-self-sync
    can identify itself as this site log's origin at all."""
    from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository

    try:
        record = LicenseStateRepository(Path(DATABASE_DIR) / 'subsystems' / 'licensing.db').load()
    except Exception:
        return None
    return record.owner_installation_id if record else None


_hub_self_sync_deferred_logged = False

# Once-per-process latch for `_attempt_join_now`'s hub-self-sync refusal
# line (see that closure, inside `_start_site_relay_if_enabled`). The
# election coordinator calls `attempt_join` on EVERY CLIENT tick
# (coordinator.py `tick()`, TICK_INTERVAL_SECONDS == the 5s beacon
# interval), and a till that has ever been hub stays LOCAL for the rest of
# its life (decision S1, plus the `init_app` boot resume) -- so without this
# latch every stood-down till wrote the same INFO line every 5 seconds,
# forever: ~17,000 lines a day into backend.log, and from there into the
# diagnostics export's log tail, burying everything else. Found by the
# adversarial verifier, 2026-09-24 (retail_site_relay_hub_self_sync_
# election_test.py test (e): 3 CLIENT ticks wrote the line 3 times). The
# decision itself never changes for this process, so saying it once is the
# whole diagnostic value; repeating it adds none.
_hub_join_refusal_logged = False


def _enter_hub_self_sync(*, start_timers: bool = True) -> None:
    """THE FIX for the LAN hub island (this task's own plan, section 0 and
    decision A): routes THIS hub's own two SyncService instances (retail +
    registry) through its OWN site log instead of Owner's cloud relay, and
    starts the forwarder bridge and the (now floor-aware) housekeeping
    sweep -- all three exactly once per process.

    THE MEASUREMENT THIS CLOSES, 2026-09-24, real hardware: a Windows till
    that had auto-elected hub bound the LAN listener and a real Android
    phone auto-joined it over wifi (`site_paired_devices=1`) and synced
    WITH THE HUB (`site_sync_events=17`, `site_sync_nonces=37`) -- but
    nothing crossed between the phone and the DESKTOP TILL'S OWN BOOKS in
    either direction: the phone's 3 sales never reached the desktop's own
    `/sales/recent` (empty), and a product created on the desktop never
    reached the phone. `site_forward_cursor` stayed at (forwarded=0,
    cloud_seq=0) throughout. Root cause: the hub's own `_sync_service`/
    `_registry_sync_service` kept talking to Owner's cloud relay directly
    (`_build_sync_client`), a completely separate sequence space from the
    site log its paired devices actually write into and read from.

    Called from THREE places, all of them idempotent no-ops after the
    first genuine entry:
      (a) `_bind_relay_now`, both the explicit-pin (`AURA_SITE_RELAY_
          ENABLED=1`) and automatic-election `start_relay` paths -- the
          moment this till actually becomes the hub.
      (b) `init_app()`, for a till that has HOSTED a site log before (see
          `_hub_self_sync_was_active_before`) -- so a restart resumes LOCAL
          mode immediately, before the first tick, rather than reverting to
          the cloud and stranding unforwarded LAN rows (decision A4(b)).
          NOT under `AURA_SITE_RELAY_ENABLED=0` any more (REHEARSAL-1007 B1
          (c) overrules decision A7): the kill switch wins, and
          `_leave_hub_self_sync_under_kill_switch` drains what A7 protected.
      (c) `_start_sync_after_activation`, right after its own call to
          `_start_site_relay_if_enabled()` -- covers the EXPLICIT-PIN case
          where this till was already bound as hub BEFORE it activated
          (`_bind_relay_now` ran with no installation id yet, so this
          function's own guard below deferred), and activation is what
          finally gives it one.

    NEVER RAISES -- same contract as every neighbouring activation-adjacent
    seam in this module (`_start_sync_after_activation`, `_start_site_relay_
    if_enabled`): this must never be able to stop a till from booting or
    from finishing an activation that genuinely succeeded at Owner.

    `start_timers=False` (used by `init_app()`'s boot-resume call, item (b)
    above): builds the pair and flips the selector but does not call
    `.start()` on either SyncService -- `init_app()`'s own existing `if
    _sync_service is not None: _sync_service.start()` lines (already
    present, unconditional on how the pair was built) do that immediately
    afterward, exactly once, whether the pair was built at import time, by
    this function, or LAN-only just now."""
    if LICENSING_PLATFORM == 'ANDROID':
        return
    try:
        # `_ensure_runtime_schema_for_sync()` (retail-hardware-viewports):
        # before this function builds or starts either SyncService below --
        # see that function's own docstring for the "no such table:
        # retail_settings" defect this closes. Outside the lock: cheap and
        # idempotent (the ensure functions already guard with module
        # flags), and this hub may not even be the one building the pair
        # this call.
        _ensure_runtime_schema_for_sync()
        with _start_sync_after_activation_lock:
            global _sync_service, _registry_sync_service, _sync_relay_selector
            global _site_forwarder_loop, _site_housekeeping_stop
            global _hub_self_sync_deferred_logged

            installation_id = _current_owner_installation_id()
            if not installation_id:
                if not _hub_self_sync_deferred_logged:
                    logging.getLogger(__name__).info(
                        'Hub self-sync deferred: this till has no Owner installation id yet '
                        '(not activated). It will switch on at activation.')
                    _hub_self_sync_deferred_logged = True
                return

            # REHEARSAL-1007 B1 (b): a till that becomes hub must have its
            # CLOUD relay back, never a LAN hub address (its own, after an
            # election flip, being the rehearsal's exact case). This call
            # repairs a value the old autojoin damaged and writes it back;
            # the forwarder started below asks the same function on every
            # tick, so it can never dial a LAN hub as "the cloud" either.
            cloud, _cloud_problem = _cloud_relay_url_now(repair=True)

            if _sync_service is None:
                _sync_service, _registry_sync_service = _build_windows_sync_services(cloud)
                # BEFORE register/start: the very first push/pull attempt
                # (a timer tick, or a route's own nudge()) must already see
                # LOCAL mode -- flipping the selector after starting the
                # timers would leave a narrow window where a tick could
                # still reach the cloud with a fresh pair that was built
                # specifically because this till is a hub.
                _sync_relay_selector.use_local()
                from commercial_runtime.sync.sync_service import register_active_service
                register_active_service(_sync_service)
                if start_timers:
                    _sync_service.start()
                    _registry_sync_service.start()
            elif _sync_relay_selector is None:
                # Should be unreachable in production: every path that
                # builds `_sync_service` also builds `_sync_relay_selector`
                # in the same call to `_build_windows_sync_services` (see
                # that function's own `global _sync_relay_selector`). Loud
                # rather than silent if it ever IS reached -- routing a sync
                # pair through the site log requires the selector that pair
                # was built with; there is no safe way to attach one after
                # the fact.
                logging.getLogger(__name__).error(
                    'Hub self-sync: a sync pair exists but was not built by '
                    '_build_windows_sync_services, so it has no RelaySelector '
                    'to route through the site log. This till stays on whatever '
                    'relay that pair was already using.')
                return
            else:
                _sync_relay_selector.use_local()

            if _site_forwarder_loop is None:
                from commercial_runtime.sync.site_relay.forwarder_loop import ForwarderLoop

                _site_forwarder_loop = ForwarderLoop(
                    get_conn=get_retail_conn,
                    cloud_client_factory=_forwarder_cloud_client,
                    interval_seconds=SITE_FORWARDER_INTERVAL_SECONDS,
                )
                _site_forwarder_loop.start()
            # REHEARSAL-1007 B1 (d): from here on this till's own books talk
            # to its own site log, which never fails -- the forwarder is its
            # real link to the other tills, so /sync/health must report it.
            _install_upstream_health_overlay(_sync_service, _site_forwarder_loop)

            if _site_housekeeping_stop is None:
                from commercial_runtime.sync.site_relay import listener as _site_relay_listener
                from commercial_runtime.sync.site_relay import replay as _site_relay_replay

                _site_housekeeping_stop = _site_relay_listener.start_housekeeping(
                    get_conn=get_retail_conn,
                    nonce_ttl_seconds=_site_relay_replay.DEFAULT_NONCE_TTL_SECONDS,
                    # SYNC-JOIN (2026-10-13): a leading 0 floor means the
                    # hourly sweep deletes NO site_sync_events (it still
                    # expires replay nonces). The hub prunes by the same rule
                    # as Owner's relay, so a device paired after a sweep would
                    # miss the shop's history -- proven for the cloud relay,
                    # read from code for the hub. Remove once a bootstrap path
                    # exists (ROADMAP "SYNC-JOIN").
                    consumer_floor_provider=_hub_housekeeping_floors,
                )
                logging.getLogger(__name__).info(
                    'Hub self-sync ON: this till\'s own books now read and write its own '
                    'site log; the forwarder bridges that log to Owner\'s cloud relay (or '
                    'idles with no cloud). Measured 2026-09-24 before this fix: hub '
                    'site_sync_events=17, forwarded=0, cloud seq=0, a real phone\'s sales '
                    'never reaching this till\'s own /sales/recent.')
    except Exception:
        logging.getLogger(__name__).exception(
            'Hub self-sync failed to start unexpectedly -- never fatal: the site relay '
            'stays bound, and this till\'s own books stay on whatever relay they were '
            'already using until the next attempt.')


def _forwarder_cloud_client():
    """The `cloud_client_factory` for `_site_forwarder_loop` -- called fresh
    on EVERY tick (never cached), exactly like `_build_sync_client` above,
    because an operator's `AURA_SYNC_RELAY_URL` or Owner's own persisted
    `sync_relay_base_url` can both change across this process's lifetime
    and the forwarder must pick either up without a restart.

    Returns `None` (the forwarder's own "NO_CLOUD" state -- see
    `forwarder_loop.py`'s own docstring) when no cloud URL is configured at
    all -- the ordinary, permanent state for a LAN-only shop, never an
    error. Raises `LocalSiteRelayError('CLOUD_RELAY_URL_INVALID')` when an
    operator-set URL exists but fails validation -- `ForwarderLoop.tick()`
    catches this and reports state=FAILED for this tick; it does not stop
    the loop or crash the till."""
    from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository
    from commercial_runtime.sync.relay_client import SyncRelayClient
    from commercial_runtime.sync.site_relay.local_client import LocalSiteRelayError

    # REHEARSAL-1007 B1 (a): `_cloud_relay_url_now`, never the bare
    # `resolve_sync_relay_base_url_now()` this used to call -- that returns
    # whatever sits in `sync_relay_base_url`, and on the rehearsal's till B
    # that was another till's LAN hub address, so this "cloud" client dialled
    # a LAN till over ordinary TLS on every tick. A LAN hub address (or this
    # till's own) is now refused as a cloud target with its own reason code,
    # which `ForwarderLoop.tick()` reports as FAILED and `/sync/health` shows.
    relay_base_url, problem = _cloud_relay_url_now()
    if problem:
        raise LocalSiteRelayError(
            problem,
            'no usable CLOUD relay for the hub forwarder -- see '
            '_cloud_relay_url_now() for what each reason code means.',
        )
    if not relay_base_url:
        return None

    _forwarder_licensing_dir = Path(DATABASE_DIR).parent / 'licensing'
    _forwarder_state_repository = LicenseStateRepository(
        Path(DATABASE_DIR) / 'subsystems' / 'licensing.db')
    _forwarder_signer = _licensing_device_identity_factory(_forwarder_licensing_dir)
    record = _forwarder_state_repository.load()
    installation_id = record.owner_installation_id if record else None
    return SyncRelayClient(
        relay_base_url,
        _forwarder_signer,
        installation_id,
        timeout_seconds=SYNC_RELAY_TIMEOUT_SECONDS,
        verify_tls=SYNC_RELAY_VERIFY_TLS,
    )


# ── REHEARSAL-1007 B1: the CLOUD relay, kept apart from any LAN hub ─────────
# Measured 2026-10-07 on two real Windows tills (the full story is in
# commercial_runtime/sync/site_relay/upstream.py's module docstring): till B
# auto-joined A's LAN hub, autojoin saved A's LAN address into
# `sync_relay_base_url` -- the ONLY home of Owner's cloud relay address --
# then B won the election and its forwarder dialled that LAN address as "the
# cloud" forever (TLS_VERIFICATION_FAILED), while /sync/health said healthy
# with 0 pending. Autojoin no longer writes that field (it records the hub in
# a sidecar). The functions below are the other half: what this till treats
# as its cloud relay, refusing a LAN hub address, and repairing an install
# that the old autojoin had already damaged.

_cloud_relay_repair_logged = set()


def _joined_hub_store():
    """The sidecar autojoin records a joined LAN hub in -- next to this
    till's own hub TLS identity, never in licensing.db."""
    from commercial_runtime.sync.site_relay.upstream import JOINED_HUB_FILENAME, JoinedHubStore

    return JoinedHubStore(Path(DATABASE_DIR).parent / 'site-relay' / JOINED_HUB_FILENAME)


def _own_hub_urls() -> list:
    """Every address THIS till would answer as a hub on: its LAN addresses
    and loopback, on the configured hub port and on the port it is actually
    bound to right now. Never raises -- an empty list only weakens the
    exact-match signal; the shape signal in `is_lan_hub_url` still applies."""
    urls = []
    try:
        from commercial_runtime.sync.site_relay.listener import local_lan_addresses

        ports = {p for p in (SITE_RELAY_PORT, getattr(_site_relay_server, 'port', None)) if p}
        hosts = list(local_lan_addresses()) + ['127.0.0.1', 'localhost']
        urls = [f'https://{h}:{p}' for h in hosts for p in ports]
    except Exception:
        pass
    return urls


def _lan_hub_signals():
    """(hub_ports, known_hub_urls) for `upstream.is_lan_hub_url`."""
    from commercial_runtime.sync.site_relay.upstream import DEFAULT_HUB_PORT

    known = _own_hub_urls()
    ports = {DEFAULT_HUB_PORT}
    if SITE_RELAY_PORT:
        ports.add(SITE_RELAY_PORT)
    try:
        joined = _joined_hub_store().url()
    except Exception:
        joined = None
    if joined:
        known.append(joined)
    return ports, known


def _recover_cloud_relay_url(hub_ports, known_hub_urls) -> str:
    """Best-effort recovery of the cloud relay address an install lost to the
    old autojoin overwrite: the relay this till's own two sync streams last
    pulled from (`sync_cursor.relay_url`, retail.db then registry.db) -- the
    cloud, unless the till had already restarted onto the damaged value or
    become a hub. Accepted only if it passes the same validation a typed
    `AURA_SYNC_RELAY_URL` gets and is not itself a LAN hub address. '' when
    nothing qualifies -- never a guess."""
    from config import validate_sync_relay_url
    from commercial_runtime.identity import registry_db
    from commercial_runtime.sync.site_relay.upstream import is_lan_hub_url

    candidates = []
    for opener in (get_retail_conn, registry_db.get_conn):
        try:
            conn = opener()
            try:
                row = conn.execute("SELECT relay_url FROM sync_cursor WHERE id=1").fetchone()
            finally:
                conn.close()
        except Exception:
            continue
        if row is not None and row[0]:
            candidates.append(row[0])
    for url in candidates:
        if not url.lower().startswith(('http://', 'https://')):
            continue  # aura-site-log://local and anything else non-HTTP
        if validate_sync_relay_url(url):
            continue
        if is_lan_hub_url(url, hub_ports=hub_ports, known_hub_urls=known_hub_urls):
            continue
        return url
    return ''


def _cloud_relay_url_now(*, repair: bool = True):
    """(url, problem_code): the CLOUD relay this till's forwarder -- and a
    freshly built cloud sync pair -- may dial. `url` is '' when there is
    none; `problem_code` is None unless that absence is a fault to report.

      * an operator's AURA_SYNC_RELAY_URL wins, exactly as config.py's
        precedence rule says -- unless it names THIS till's own hub address
        (`CLOUD_RELAY_URL_IS_THIS_HUB`): a hub forwarding into itself is the
        self-loop this fix exists to end, whoever typed it.
      * an invalid operator value -> `CLOUD_RELAY_URL_INVALID` (unchanged).
      * nothing configured -> ('', None): a LAN-only shop, not a fault.
      * a persisted value that is a LAN hub address (old autojoin damage) is
        NEVER returned. It is repaired from `_recover_cloud_relay_url` when
        possible -- and with `repair=True` the recovered value is written
        back to licensing.db, so the next boot's config.py reads the cloud
        again -- otherwise ('', 'CLOUD_RELAY_URL_IS_LAN_HUB'), which stays a
        visible failure until Owner's relay address is supplied again
        (re-activation writes it; AURA_SYNC_RELAY_URL overrides it).

    Logged once per distinct outcome, never per call: the forwarder asks on
    every 10-second tick."""
    import config as _config
    from commercial_runtime.sync.site_relay.upstream import is_lan_hub_url, same_relay

    url, problems = resolve_sync_relay_base_url_now()
    if problems:
        return '', 'CLOUD_RELAY_URL_INVALID'
    if not url:
        return '', None
    if getattr(_config, '_AURA_SYNC_RELAY_URL_ENV', ''):
        if any(same_relay(url, own) for own in _own_hub_urls()):
            return '', 'CLOUD_RELAY_URL_IS_THIS_HUB'
        return url, None
    hub_ports, known = _lan_hub_signals()
    if not is_lan_hub_url(url, hub_ports=hub_ports, known_hub_urls=known):
        return url, None

    log = logging.getLogger(__name__)
    recovered = _recover_cloud_relay_url(hub_ports, known)
    if recovered:
        if repair:
            try:
                from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository

                repo = LicenseStateRepository(Path(DATABASE_DIR) / 'subsystems' / 'licensing.db')
                record = repo.load()
                if record is not None and record.sync_relay_base_url == url:
                    record.sync_relay_base_url = recovered
                    repo.save(record)
            except Exception:
                log.exception('Cloud relay repair: could not write the recovered address back; '
                              'this process still uses it.')
        if ('recovered', url, recovered) not in _cloud_relay_repair_logged:
            _cloud_relay_repair_logged.add(('recovered', url, recovered))
            log.warning(
                'Cloud relay repair (REHEARSAL-1007 B1): the saved cloud relay address was a '
                'LAN hub address (%s), written there by an older LAN auto-join. Restored the '
                'cloud relay this till last synced with (%s).', url, recovered)
        return recovered, None
    if ('lost', url) not in _cloud_relay_repair_logged:
        _cloud_relay_repair_logged.add(('lost', url))
        log.error(
            'Cloud relay LOST (REHEARSAL-1007 B1): the saved cloud relay address is a LAN '
            'hub address (%s), written there by an older LAN auto-join, and no earlier cloud '
            'address survives on this till. It will NOT be dialled as the cloud. This till '
            'reports sync as failing until Owner\'s relay address is supplied again '
            '(re-activate the licence, or set AURA_SYNC_RELAY_URL).', url)
    return '', 'CLOUD_RELAY_URL_IS_LAN_HUB'


def _install_upstream_health_overlay(service, loop) -> None:
    """REHEARSAL-1007 B1 (d): make `/sync/health` report the forwarder's
    failures as this till's sync failures -- see `forwarder_loop.
    overlay_upstream_health` for exactly what changes and why.

    Wraps the INSTANCE's `get_health` (once; idempotent) rather than adding a
    hook to `SyncService`, because sync_service.py is owned by another
    concurrent change in this wave -- the cleaner long-term home is a
    `health_overlay` constructor hook there, recorded in the B1 report. The
    registered service object is unchanged (`_active_service is
    _sync_service` still holds), so nothing that compares identity breaks."""
    if service is None or loop is None or getattr(service, '_aura_upstream_overlay', False):
        return
    from commercial_runtime.sync.site_relay.forwarder_loop import overlay_upstream_health

    base_get_health = service.get_health

    def get_health():
        return overlay_upstream_health(base_get_health(), loop.snapshot())

    service.get_health = get_health
    service._aura_upstream_overlay = True


def _hub_site_log_unconsumed() -> bool:
    """True if a site-log row this till did not write is still waiting for
    one of its OWN two sync streams to apply it -- only meaningful while
    that stream's cursor is on the site log. Fails SAFE (True) on any read
    error: the caller then keeps the old behaviour (resume LOCAL), which can
    never strand a row."""
    from commercial_runtime.identity import registry_db
    from commercial_runtime.sync.site_relay.local_client import LOCAL_RELAY_URL

    try:
        own_id = _current_owner_installation_id()
        if not own_id:
            return True
        cursors = []
        for opener in (get_retail_conn, registry_db.get_conn):
            conn = opener()
            try:
                row = conn.execute("SELECT last_seq, relay_url FROM sync_cursor WHERE id=1").fetchone()
            finally:
                conn.close()
            if row is not None and row[1] == LOCAL_RELAY_URL:
                cursors.append(int(row[0] or 0))
        if not cursors:
            return False
        conn = get_retail_conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM site_sync_events WHERE seq > ? AND origin_device_id != ? LIMIT 1",
                (min(cursors), own_id)).fetchone()
        finally:
            conn.close()
        return row is not None
    except Exception:
        logging.getLogger(__name__).exception(
            'Hub kill switch: could not tell whether this till has applied its whole site '
            'log; keeping it on the site log this boot (fail safe).')
        return True


def _leave_hub_self_sync_under_kill_switch() -> None:
    """REHEARSAL-1007 B1 (c): a till that once hosted a site log, booted with
    the operator kill switch (`AURA_SITE_RELAY_ENABLED=0`), must NOT resume
    hub self-sync. The rehearsal's till B did, on every start, and with the
    damaged relay address that meant its own books never left its own disk.

    The one thing that used to justify resuming anyway (decision A4(b)/A7:
    "going back to the cloud would strand unforwarded LAN rows") is handled
    here instead of by staying a hub:
      * rows not yet sent UP are drained by an upload-only forwarder
        (`ForwarderLoop(pull_down=False)`) -- started only when there are
        any and a cloud exists to receive them;
      * rows other devices left that this till's own books have NOT applied
        yet cannot be read any other way -- in that one case this boot still
        resumes self-sync (old behaviour, logged as a WARNING) and the kill
        switch takes effect on the next boot after they are applied.
    Otherwise the sync pair built at import stays on the CLOUD."""
    global _site_forwarder_loop
    log = logging.getLogger(__name__)
    if _hub_site_log_unconsumed():
        log.warning(
            'Hub mode is switched OFF (AURA_SITE_RELAY_ENABLED=0), but this till still has '
            'rows from other devices on its own site log that its books have not applied. '
            'Keeping it on its site log for THIS boot so none are lost; the switch takes '
            'full effect on the next start.')
        _enter_hub_self_sync(start_timers=False)
        return
    log.info(
        'Hub mode is switched OFF (AURA_SITE_RELAY_ENABLED=0): this till does not resume '
        'hub self-sync; its own books sync with the cloud relay directly.')
    url, _problem = _cloud_relay_url_now()
    if not url or _site_forwarder_loop is not None:
        return
    try:
        from commercial_runtime.sync.site_relay import forwarder as _forwarder

        conn = get_retail_conn()
        try:
            backlog = _forwarder.count_unforwarded(conn)
        finally:
            conn.close()
    except Exception:
        log.exception('Hub kill switch: could not count unforwarded site-log rows.')
        return
    if backlog <= 0:
        return
    from commercial_runtime.sync.site_relay.forwarder_loop import ForwarderLoop

    _site_forwarder_loop = ForwarderLoop(
        get_conn=get_retail_conn,
        cloud_client_factory=_forwarder_cloud_client,
        interval_seconds=SITE_FORWARDER_INTERVAL_SECONDS,
        pull_down=False,
    )
    _site_forwarder_loop.start()
    _install_upstream_health_overlay(_sync_service, _site_forwarder_loop)
    log.info('Hub kill switch: forwarding %d site-log row(s) from this till\'s time as hub '
             'up to the cloud (upload only).', backlog)


def _repair_relay_url_at_boot() -> None:
    """REHEARSAL-1007 B1 (b), boot half: the sync pair was built at IMPORT
    from config.py's frozen `SYNC_RELAY_BASE_URL`. On a till the old autojoin
    damaged, that is a LAN hub address -- harmless while the pair reads its
    own site log, but it is what the pair dials whenever it is on the CLOUD
    (a till that never became hub, or one under the kill switch). Rebuild
    the not-yet-started pair on the repaired cloud address (or none, which
    then fails visibly with NO_RELAY_CONFIGURED rather than dialling a LAN
    till). An operator's AURA_SYNC_RELAY_URL is never second-guessed here."""
    global _sync_service, _registry_sync_service
    import config as _config
    from commercial_runtime.sync.site_relay.upstream import is_lan_hub_url

    if LICENSING_PLATFORM == 'ANDROID' or _sync_service is None:
        return
    if getattr(_config, '_AURA_SYNC_RELAY_URL_ENV', ''):
        return
    hub_ports, known = _lan_hub_signals()
    if not is_lan_hub_url(SYNC_RELAY_BASE_URL, hub_ports=hub_ports, known_hub_urls=known):
        return
    url, _problem = _cloud_relay_url_now(repair=True)
    from commercial_runtime.sync.sync_service import register_active_service

    _sync_service, _registry_sync_service = _build_windows_sync_services(url)
    register_active_service(_sync_service)


def _hub_self_sync_was_active_before() -> bool:
    """True if THIS till has hosted a site log before this boot -- the
    signal `init_app()` uses to resume LOCAL mode immediately (decision
    A4(b)) rather than waiting for the election/explicit-pin path to bind
    the relay again first. Never raises; any failure reading either signal
    is treated as "no", the same fail-closed direction `_start_site_relay_
    if_enabled` already uses for a licence read failure (worst case: one
    extra restart before self-sync resumes, never a false resume on a till
    that was never a hub)."""
    try:
        conn = get_retail_conn()
        try:
            from commercial_runtime.sync.site_relay.local_client import LOCAL_RELAY_URL

            row = conn.execute("SELECT relay_url FROM sync_cursor WHERE id=1").fetchone()
            if row is not None and row["relay_url"] == LOCAL_RELAY_URL:
                return True
            has_log = conn.execute("SELECT 1 FROM site_sync_events LIMIT 1").fetchone()
            return has_log is not None
        finally:
            conn.close()
    except Exception:
        logging.getLogger(__name__).exception(
            'Hub self-sync: could not determine whether this till has hosted a site '
            'log before; treating this boot as "no" (fail closed).')
        return False


def _start_sync_after_activation():
    """Hot-start seam (retail-hardware-viewports): start cloud sync (both
    SyncService instances) and the LAN hub election IN THIS PROCESS, right
    after a licence activates, instead of requiring a restart.

    THE GAP THIS CLOSES, measured 2026-09-24 by running the product: on a
    fresh desktop till with no AURA_SYNC_RELAY_URL env var, `_sync_service`/
    `_registry_sync_service` above are built EXACTLY ONCE, at import time,
    gated on `_SYNC_RELAY_URL_IS_USABLE` -- which reads config.py's
    `SYNC_RELAY_BASE_URL` module constant, itself frozen the moment config.py
    was imported. A fresh till has neither an operator-set env var nor a
    value Owner has ever persisted yet, so that constant is '' for the rest
    of THIS process's life no matter what happens afterwards -- activating a
    licence writes a fresh `sync_relay_base_url` into licensing.db, but
    nothing before this fix ever went back and looked. The till's admin
    'create' user event then sat in the outbox forever, and a second device
    joining the same shop never received the owner's account, because the
    LAN hub election (`_start_site_relay_if_enabled` below) never ran either
    -- it too runs exactly once, from `init_app()`, before activation.
    config.py's `resolve_sync_relay_base_url_now()` is the other half of
    this fix: it recomputes the identical precedence/validation FRESH, at
    whatever moment it is called, instead of trusting the frozen constant.

    HUB-SELF-SYNC SEAM (retail-hardware-viewports, added by a later wave):
    right after this function's own call to `_start_site_relay_if_enabled()`,
    if that call left `_site_relay_server` bound, this function also calls
    `_enter_hub_self_sync()` -- the EXPLICIT-PIN case where this till was
    bound as hub before it ever activated, so hub-self-sync's own guard
    deferred for lack of an installation id. See `_enter_hub_self_sync`'s
    own docstring for the full three-call-site picture.

    CALLED FROM `_on_licence_activated()` -- the existing `on_activation_
    success` seam `make_licensing_blueprint` already invokes on every
    successful activation -- but ONLY when that function's own split-state
    check found nothing wrong (see its docstring's "THIRD HALF" paragraph).
    ALSO CALLED FROM `_refuse_while_split_state()`, on the one request that
    finds a split-state refusal has converged and clears it: that is the
    deferred form of the same "right after activation" moment.

    NEVER RAISES. Same contract as every neighbouring activation-seam
    function in this module (`_on_licence_activated`, `_detect_split_state_
    reason`, both rebind halves they call): a licence activation that
    genuinely succeeded at Owner must never be reported back to the customer
    as a failure because starting a background sync timer hit some local
    problem. Wrapped in a bare `except Exception` with `logging.exception`,
    exactly like `_configure_file_logging` and `_start_site_relay_if_enabled`
    already do for the same reason.

    ANDROID never reaches the interesting branch below: Kotlin's own
    SyncCoordinator drives Android's push/pull loop end to end (see the
    ANDROID branch of the import-time construction above), and this Python
    process never holds the Android device's signing key -- building a
    Python-side SyncService here would be actively wrong on that platform,
    not merely redundant, so this returns immediately rather than falling
    through into logic that assumes Windows.

    A NO-OP before `init_app()` has run. `init_app()` runs schema migrations
    BEFORE starting sync (see its own call order and docstring), and this
    seam exists only to cover an activation that lands LATER, mid-session,
    in an already-initialised process -- an activation cannot land before
    `init_app()` has even registered the licensing blueprint's routes, but a
    defensive check costs nothing and keeps this function honest about the
    one ordering invariant it depends on rather than assuming a caller
    always gets it right.
    """
    try:
        if LICENSING_PLATFORM == 'ANDROID':
            return
        if not _INIT_APP_HAS_RUN:
            return

        global _sync_service, _registry_sync_service

        # `_ensure_runtime_schema_for_sync()` (retail-hardware-viewports):
        # before this function builds or starts anything below -- see that
        # function's own docstring for the "no such table: retail_settings"
        # defect this closes. Cheap/idempotent even on the common path where
        # `_sync_service` already exists and this call changes nothing.
        _ensure_runtime_schema_for_sync()

        with _start_sync_after_activation_lock:
            if _sync_service is None:
                relay_base_url, problems = resolve_sync_relay_base_url_now()
                if relay_base_url and not problems:
                    from commercial_runtime.sync.sync_service import register_active_service

                    _sync_service, _registry_sync_service = _build_windows_sync_services(relay_base_url)
                    register_active_service(_sync_service)
                    _sync_service.start()
                    _registry_sync_service.start()
                    logging.getLogger(__name__).info(
                        'Multi-device sync started after activation, with no restart -- relay %s',
                        relay_base_url,
                    )
                elif problems:
                    # An operator-set AURA_SYNC_RELAY_URL that fails
                    # validate_sync_relay_url() -- see resolve_sync_relay_
                    # base_url_now()'s own docstring for why this is the ONLY
                    # case that reaches here with `problems` non-empty (an
                    # invalid PERSISTED value is discarded silently by the
                    # precedence rule itself, long before this point, and is
                    # therefore indistinguishable from "nothing configured"
                    # below -- it was never something an operator typed, so
                    # it is not reported as a problem either).
                    for problem in problems:
                        logging.getLogger(__name__).error(
                            'Multi-device sync stays OFF after activation -- %s', problem)
                else:
                    # The ordinary case for the overwhelming majority of
                    # shops: no operator env var, and Owner's activation
                    # response either carried no sync_relay_base_url at all
                    # (a deploy that never configured OWNER_SYNC_RELAY_
                    # PUBLIC_URL) or carried one that already failed
                    # validation and was discarded. Logged at INFO, not
                    # silently: CLAUDE.md's engineering standard is explicit
                    # that a silent early return is undiagnosable.
                    logging.getLogger(__name__).info(
                        'Multi-device sync stays OFF after activation -- no usable relay URL '
                        '(neither AURA_SYNC_RELAY_URL nor a valid one from Owner).')

            # Regardless of whether sync was just built above, was already
            # built at import time (an operator had AURA_SYNC_RELAY_URL set
            # from boot), or stays off entirely: the site relay re-reads the
            # licence state itself and is already guarded against a second
            # start (`_site_relay_server is not None or _site_relay_
            # coordinator is not None`, at the top of `_start_site_relay_if_
            # enabled`), so calling it again here is safe -- and is what
            # actually runs the hub election for the FIRST time on a fresh
            # till that has just activated, since `init_app()`'s own call to
            # it ran before any licence existed.
            #
            # INSIDE THE LOCK, deliberately (adversarial verifier,
            # 2026-09-24, T14). That guard is check-then-set with no lock of
            # its own, which was fine while its only caller was `init_app()`,
            # single-threaded before any request is served. This seam calls
            # it from REQUEST threads -- two near-simultaneous activations, or
            # the burst of concurrent fetches a page load fires the moment a
            # split-state refusal clears -- and two callers that both pass
            # the guard build TWO election coordinators on one till: the
            # two-hub bug `_site_relay_coordinator`'s own comment says this
            # feature exists to remove. Measured before the move: T14 built
            # 2 coordinators. Holding this lock across it costs nothing that
            # matters: this path runs only when an activation lands (or a
            # split-state refusal clears), and after the first call the
            # guard returns at once.
            _start_site_relay_if_enabled()

            # Hub-self-sync (retail-hardware-viewports): covers the
            # EXPLICIT-PIN case where this till was bound as hub BEFORE it
            # ever activated (`_bind_relay_now` ran with no installation id
            # yet, so `_enter_hub_self_sync`'s own guard deferred). The
            # AUTOMATIC path is already covered by `_bind_relay_now` itself,
            # which now calls `_enter_hub_self_sync()` on every bind
            # (including the idempotent early-return one) -- see that
            # function's own updated docstring. Re-entrant-safe: this
            # function already holds `_start_sync_after_activation_lock`
            # (now an RLock -- see that lock's own comment for why), and
            # `_enter_hub_self_sync` takes the SAME lock.
            if _site_relay_server is not None:
                _enter_hub_self_sync()
    except Exception:
        logging.getLogger(__name__).exception(
            'Starting sync after activation failed unexpectedly; sync stays '
            'off until the next restart. Never fatal to the activation '
            'itself.')


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


#: Set True at the end of `init_app()`. Read by the refusal guard below.
#:
#: WHY THIS EXISTS. `init_app()` runs every schema migration and is called
#: only from this module's `__main__` block, so `python app.py` is the one
#: correct way to start this server. Started any other way -- most plausibly
#: `flask --app app run`, which imports the module-level `app` object and
#: never calls this function -- the process comes up looking perfectly
#: healthy: it binds its port, serves static files, and answers
#: `GET /api/health` with 200. It then fails the first request that touches
#: a table with `sqlite3.OperationalError: no such table: users`.
#:
#: That was observed for real while standing up a clean install, and it cost
#: real time precisely because every signal available said the server was
#: fine. A missing migration must announce itself as a missing migration, at
#: the door, not as a confusing error from whichever query happens to run
#: first.
_INIT_APP_HAS_RUN = False


@app.before_request
def _refuse_to_serve_before_init_app():
    """Answer every request with a clear 503 until `init_app()` has run.

    Deliberately covers `/api/health` as well. Reporting healthy while the
    databases have never been migrated is the exact lie this guard exists to
    stop -- `launcher_support`'s readiness poll asks this endpoint whether the
    server is ready to be shown to a user, and until the schema exists the
    honest answer is no. The real launch path (`python app.py`) calls
    `init_app()` before serving a single request, so nothing legitimate ever
    sees this.
    """
    if _INIT_APP_HAS_RUN:
        return None
    return jsonify({
        'status': 'error',
        'reason_code': 'APP_NOT_INITIALISED',
        'message': (
            'Aura Retail was started without running its database migrations, '
            'so no request can be served. Start it with "python app.py" (the '
            'PORT environment variable selects the port). Starting it via '
            '"flask --app app run" or any other WSGI entry point imports this '
            'module without calling init_app(), which is what leaves the '
            'schema uncreated.'
        ),
    }), 503


# ── File logging (retail-hardware-viewports) ────────────────────────────────
# THE GAP this closes: nothing above configures file logging anywhere in this
# module -- every `logging.getLogger(...)` call in this file and every module
# it imports has only ever reached stderr (Python's own "no handlers found"
# lastResort fallback). This ships as a PyInstaller/pywebview WINDOWED desktop
# app (see products/retail/desktop/launcher_retail.py); a windowed process has
# no console, so stderr goes nowhere at all. When a shop says "it stopped
# working yesterday", there is currently no record -- nothing to read, nothing
# to ask them to send.
#
# Bounded on purpose: a till runs for months unattended, and an unbounded log
# is a disk-filling bug, not a diagnostic. 2 MB x 3 backups is a firm ceiling
# (~8 MB) that still keeps weeks of INFO-level activity on a normal shop.
LOG_MAX_BYTES = 2 * 1024 * 1024  # 2 MB per file
LOG_BACKUP_COUNT = 3             # plus the active file -- 4 files, ~8 MB, forever
LOG_LEVEL_ENV_VAR = 'AURA_LOG_LEVEL'  # overrides the INFO default, e.g. DEBUG/WARNING

# Same directory launcher_retail.py's own `logging.basicConfig` already
# writes 'startup.log' into (`Path(_app_data, 'logs')`) -- this is the
# backend's OWN log, not a duplicate of the launcher's, so it gets a
# different filename in the same, already-established, app-data-relative
# 'logs' directory. `Path(DATABASE_DIR).parent` is this module's own
# existing idiom for "the app-data root" (see `_sync_licensing_dir` above),
# not a new path convention.
LOG_DIR = Path(DATABASE_DIR).parent / 'logs'
LOG_FILE_PATH = LOG_DIR / 'backend.log'

# retail_diagnostics_export_test.py's diagnostics_export route (retail_api.py)
# reads this same file's tail -- see that route's own module-level path
# constant, which must be derived to the identical location.

_file_logging_configured = False


def _configure_file_logging():
    """Attach a bounded RotatingFileHandler to the root logger, alongside
    (never instead of) the existing stderr output, so every module's
    `logging.getLogger(__name__)` call in this process lands in a real file
    on disk. Called from `init_app()` -- the one seam BOTH the real entry
    point (`if __name__ == '__main__':` at the bottom of this file) and
    every test bootstrap (retail_printer_kick_test.py,
    retail_route_capability_matrix_test.py, etc. -- each calls
    `_app_module.init_app()` directly) actually run through. Starting this
    module via `flask run`, or importing it without calling `init_app()`,
    skips this exactly the same way it skips the schema migrations --
    see `_refuse_to_serve_before_init_app` immediately above.

    BEST-EFFORT ONLY: a read-only app-data volume, a permissions problem, or
    any other failure creating the directory/file is caught here and logged
    to stderr instead. A logging failure must NEVER be the reason a till
    fails to boot -- this function cannot raise.

    Idempotent: safe if `init_app()` ever runs more than once in a process
    (some test bootstraps re-import/re-init within the same interpreter).
    """
    global _file_logging_configured
    if _file_logging_configured:
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        level_name = os.environ.get(LOG_LEVEL_ENV_VAR, 'INFO').upper()
        level = getattr(logging, level_name, logging.INFO)
        if not isinstance(level, int):
            level = logging.INFO

        file_handler = RotatingFileHandler(
            str(LOG_FILE_PATH), maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
            encoding='utf-8',
        )
        formatter = logging.Formatter(
            '%(asctime)s %(levelname)-8s %(name)s: %(message)s'
        )
        file_handler.setFormatter(formatter)

        root_logger = logging.getLogger()
        # Explicit stderr handler alongside the new file handler -- NOT a
        # replacement for it. Root previously had zero handlers, which meant
        # only WARNING+ ever reached stderr at all (Python's lastResort);
        # adding a handler here without also keeping an explicit stderr
        # sink would be a silent regression for anyone still watching a
        # console during development. See launcher_retail.py's own
        # `logging.basicConfig(handlers=[FileHandler(...), StreamHandler(...)])`
        # for the identical two-sinks-at-once shape this mirrors.
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)

        root_logger.addHandler(file_handler)
        root_logger.addHandler(stream_handler)
        root_logger.setLevel(level)
        _file_logging_configured = True
        # A totally quiet, error-free boot (no sync configured, nothing
        # mis-set) otherwise emits NOTHING at INFO level anywhere in this
        # process -- verified while proving this feature works end to end.
        # An empty log file is a weaker proof than a real one, and this line
        # is also genuinely useful on its own: it is the first thing a
        # vendor reading `backend.log` sees, timestamping exactly when this
        # process started logging and to which file.
        logging.getLogger(__name__).info(
            'Aura Retail backend starting -- file logging active at %s (level=%s)',
            LOG_FILE_PATH, level_name,
        )
    except Exception:
        logging.getLogger(__name__).exception(
            'File logging could not be configured (app-data volume unwritable '
            'or similar); continuing with stderr-only logging. This is never '
            'fatal to boot.'
        )


_site_relay_server = None

# The automatic hub election's driver (commercial_runtime/sync/site_relay/
# coordinator.py's `SiteRelayCoordinator`), or None on any install not running
# one -- which is every install that is not an activated Windows till, plus
# every till whose operator pinned hub mode explicitly with
# AURA_SITE_RELAY_ENABLED=1 (see `_start_site_relay_if_enabled` for why an
# explicit pin deliberately skips the election instead of running it).
#
# A MODULE GLOBAL RATHER THAN A LOCAL, for one concrete reason: `init_app()` is
# reachable more than once (see its own docstring's note about timers that are
# not safe to start twice), and the guard at the top of
# `_start_site_relay_if_enabled` can only refuse a second election driver if
# there is somewhere for the first one to be recorded. Two drivers ticking on
# one till can each start and stop the other's relay -- the two-hub bug this
# whole feature exists to remove, reproduced inside a single process.
_site_relay_coordinator = None


def _observe_lan_beacons_paced(timeout_seconds):
    """Production `observe_beacons` seam for `SiteRelayCoordinator`: the real
    UDP listen coordinator.py already ships, plus ONE guarantee it does not
    make on its own -- a FAILED listen still costs a full tick interval of
    wall-clock time.

    WHY A WRAPPER, when the underlying function is already the production
    default. `SiteRelayCoordinator._run` is literally `while not stopped:
    tick()`, with no sleep of its own, and that is correct by design:
    coordinator.py states that "an unconditional `_default_observe_beacons(
    timeout)` IS the tick cadence". That holds perfectly while the listen
    SUCCEEDS. If the UDP bind raises instead -- port 45455 held by another
    process that did not set SO_REUSEADDR, a local firewall refusing the bind
    -- `_default_observe_beacons` returns in microseconds, `tick()` correctly
    swallows the exception and logs it WITH A TRACEBACK, and the loop tries
    again immediately, forever, as fast as the CPU allows. The symptom would
    not read as "no hub": it reads as a till pegging a core and writing
    tracebacks into backend.log as fast as the disk accepts them, while a
    cashier is trying to ring a sale on that same machine.

    Deliberately NOT fixed in coordinator.py: that module is finished, and its
    never-throws contract ("tick() swallows, logs, and the next tick retries
    from scratch") is right as written. The missing piece is a PACE for the
    retry, and pacing a real-world seam is the wiring layer's job -- this file.
    The exception is re-raised unchanged after the sleep, so the coordinator's
    own logging and role bookkeeping are completely unaltered; all this adds is
    the delay in front of the retry.
    """
    import time as _time

    from commercial_runtime.sync.site_relay.coordinator import (
        _default_observe_beacons)

    try:
        return _default_observe_beacons(timeout_seconds)
    except Exception:
        _time.sleep(max(0.0, timeout_seconds))
        raise


def _start_site_relay_if_enabled():
    """Hub mode: bind the LAN-facing site relay, if this install is a hub.

    See docs/launch-readiness/lan-restaurant-design.md §3 and
    commercial_runtime/sync/site_relay/listener.py. A hub hosts a LAN
    implementation of Owner's own push/pull contract so tablets and second
    tills on the shop's wifi converge with no internet at all.

    THIS IS THE ONLY PLACE IN THE PRODUCT THAT BINDS A NON-LOOPBACK SOCKET.
    2026-09-14: the gate used to be a bare "did an operator set
    AURA_SITE_RELAY_ENABLED=1" -- safe in theory, unreachable in practice,
    because no shop owner was ever going to set an environment variable on
    a till. The gate is now `site_relay_should_start()` (config.py), a
    tri-state decision: '1' always starts the relay, '0' never does, and
    anything else -- unset included -- is AUTOMATIC, gated on this
    install's own licence state via ACTIVE_FAMILY. See config.py's "LAN
    site relay / hub mode" block for the full reasoning, including the one
    real cost this trade has (an unauthenticated `/identity` route becomes
    reachable by default on a licensed till) and why it was judged
    acceptable.

    Reading the licence to make that decision must NEVER be able to stop
    this till from booting -- same NEVER FATAL contract as the rest of this
    function, applied one step earlier. Any failure reading licensing.db
    here (locked file, corrupt row, a first-run race before the table
    exists) is treated as "no active licence", which is the fail-closed
    direction for a feature that binds a LAN-facing socket: worst case a
    licensed shop's till takes one more restart to notice hub mode, never
    an unlicensed shop's till becoming an unexpected LAN listener.

    The UI server is untouched by all of this: it keeps its own 127.0.0.1
    binding in `_run_server`, and the relay gets a SEPARATE port and a
    SEPARATE, minimal Flask app carrying only the relay blueprint. The LAN is
    offered the sync contract and nothing else.

    NEVER FATAL, and deliberately so -- same rule as the sync relay's own
    "misconfigured URL disables SYNC, not the app" handling above. A till
    whose LAN port is already taken, or whose firewall refuses the bind, must
    still boot and sell; it simply is not a hub this run. Logged at ERROR so
    the condition is diagnosable rather than silent, because the symptom on
    every OTHER device is just "sync stopped working" with nothing to point
    at.

    2026-09-15: ON THE AUTOMATIC PATH THIS FUNCTION NO LONGER BINDS ANYTHING
    ITSELF. It builds `SiteRelayCoordinator` (commercial_runtime/sync/
    site_relay/coordinator.py) and starts its tick loop; the ELECTION decides
    whether this till becomes the hub, and calls back into the `start_relay`
    seam below when it does. Automatic mode without an election was the
    two-hub bug: every activated Windows till in a shop started a relay and
    broadcast a beacon at boot, with nothing choosing between them, so a
    two-till shop ran two hubs and its devices split across them. An explicit
    AURA_SITE_RELAY_ENABLED=1 still binds immediately and holds no election --
    see that branch's own comment for why an operator's explicit pin must not
    be overrideable by a peer's lower installation id.

    Guarded against a second start: `init_app()` is reachable more than once
    (see its own docstring's note about timers that are not safe to start
    twice), and starting a second listener would either raise on the bind or,
    worse, leave an orphaned thread serving a stale connection factory. The
    guard covers the COORDINATOR as well as the listener, because on the
    automatic path there is no bound listener yet to notice -- a second
    `init_app()` would otherwise build a second election driver on the same
    till, and two drivers can each stop the other's relay."""
    global _site_relay_server, _site_relay_coordinator
    if _site_relay_server is not None or _site_relay_coordinator is not None:
        return

    # Built here, before the enable decision, rather than down in the try
    # block below where the pre-2026-09-14 version of this function built
    # it: the automatic-mode decision needs a licence read too, so the SAME
    # repository object (and the SAME loaded record) now serves both the
    # enable decision below and `_hub_installation_id` further down --
    # one open of licensing.db per boot, not two.
    from commercial_runtime.licensing_contracts.state_repository import (
        LicenseStateRepository as _HubStateRepository)
    from commercial_runtime.licensing_contracts.state_machine import LicenseState
    _hub_state_repository = _HubStateRepository(
        Path(DATABASE_DIR) / 'subsystems' / 'licensing.db')
    try:
        _hub_license_record = _hub_state_repository.load()
    except Exception:
        # NEVER FATAL: a till that cannot read its own licence right now
        # must still boot and sell. It just does not get to decide "active"
        # for automatic hub mode this run -- see the docstring above for why
        # that is the correct fail-closed direction here.
        _hub_license_record = None
        logging.getLogger(__name__).exception(
            'Site relay: could not read licence state to decide automatic '
            'hub mode; treating this install as unlicensed for this '
            'decision. Hub mode stays off unless AURA_SITE_RELAY_ENABLED=1 '
            'is set explicitly. Never fatal to boot.')

    try:
        _hub_license_state = (
            LicenseState(_hub_license_record.current_state)
            if _hub_license_record is not None else None
        )
    except ValueError:
        # An unrecognized current_state (a downgrade, a restored backup, a
        # partial write) is not a member of ACTIVE_FAMILY under any reading
        # of it, so it fails closed exactly like a missing record -- this
        # decision only needs "active or not", so it does not need its own
        # LOCAL_STATE_CORRUPT bookkeeping the way flask_guard.py's
        # _resolve_current_state does for the routes that are actually
        # licence-gated.
        _hub_license_state = None

    # LICENSING_PLATFORM, not a guess: automatic election is Windows-only,
    # because a handset must never self-elect as the shop's hub (see
    # site_relay_should_start's own block for the hardware finding behind
    # that). An explicit AURA_SITE_RELAY_ENABLED=1 still wins on any
    # platform.
    if not site_relay_should_start(
            SITE_RELAY_ENABLE_OVERRIDE, _hub_license_state, LICENSING_PLATFORM):
        return

    try:
        from commercial_runtime.sync.site_relay.listener import (
            local_lan_addresses, start_site_relay)

        # Identity for the pairing QR and the beacon, built HERE rather than
        # reused from the sync block above, and that is deliberate: those names
        # (`_sync_state_repository`, `_sync_signer`) only exist when a CLOUD
        # relay URL is configured. A LAN-only shop -- no cloud relay at all,
        # which is precisely the offline case this whole feature exists for --
        # would hit a NameError and silently fail to become a hub. Hub mode
        # must not depend on cloud sync being configured.
        _hub_signer = _licensing_device_identity_factory(
            Path(DATABASE_DIR).parent / 'licensing')

        # Tolerated as None: hub mode can bind before this till has activated,
        # which is a normal transient state on a first-run machine rather than
        # an error (see SiteRelayHandle's docstring). Pairing and the beacon
        # wait for an identity instead of the relay refusing to start.
        #
        # Reuses `_hub_license_record`, already loaded above to decide
        # whether to start at all -- not a second load() of licensing.db.
        _hub_installation_id = (
            _hub_license_record.owner_installation_id if _hub_license_record else None
        )

        # The hub's own Ed25519 DEVICE public key, which goes on the pairing QR
        # so a paired device can later verify this hub's signed addressing
        # beacon. NOT the same thing as the SPKI pin sitting beside it: the pin
        # authenticates the TLS connection, this key authenticates a UDP
        # datagram. Two keys, two jobs.
        #
        # This was MISSING on the first cut of this wiring, and the payload went
        # out with `"hub_device_public_key": null`. Android's
        # `parsePairingPayload` requires the field, so the phone would have
        # refused every QR this hub produced -- caught by reading a real payload
        # rather than by any test, because nothing asserted the field was
        # populated end to end.
        #
        # Tolerated as None for the same reason installation_id is: a hub can
        # bind before this till has activated, and no device key exists until
        # then. `get_public_key_b64` raises rather than returning empty in that
        # state, so it is guarded rather than allowed to stop the relay.
        try:
            _hub_device_public_key = _hub_signer.get_public_key_b64()
        except Exception:
            _hub_device_public_key = None
            logging.getLogger(__name__).info(
                'Site relay: no device key yet (this till has not activated), so '
                'pairing QRs will omit the beacon-verification key until it has.')

        # ── Licence-proof automatic joining ─────────────────────────────────
        # These three are what let a device join this hub with NOTHING typed by
        # anyone: no QR, no code, no IP. Every activated device holds an
        # Owner-signed assertion naming its `license_public_id`, so two devices
        # are in the same shop exactly when those match -- checkable offline by
        # both sides against the trust anchor bundled in every install.
        #
        # All three are CALLABLES, deliberately, not values. A hub can bind
        # before its till has activated, and activation may happen minutes
        # later in the same process; capturing values here would pin them to
        # "not activated" for the lifetime of the run and the hub would refuse
        # every join forever, with nothing on screen explaining why.
        from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore

        _hub_licensing_dir = Path(DATABASE_DIR).parent / 'licensing'

        def _hub_assertion_payload():
            """This device's own verified-at-activation assertion payload, or
            None before activation."""
            import json as _json

            record = _hub_state_repository.load()
            if record is None or not record.assertion_envelope_json:
                return None
            try:
                envelope = _json.loads(record.assertion_envelope_json)
            except (TypeError, ValueError):
                return None
            payload = envelope.get('payload')
            return envelope if isinstance(payload, dict) else None

        def _hub_identity_provider():
            envelope = _hub_assertion_payload()
            if envelope is None or not _hub_installation_id or not _hub_device_public_key:
                return None
            return {
                'installation_id': _hub_installation_id,
                'device_public_key': _hub_device_public_key,
                'assertion': envelope,
            }

        def _hub_license_public_id_provider():
            envelope = _hub_assertion_payload()
            if envelope is None:
                return None
            return envelope['payload'].get('license_public_id')

        def _hub_trust_store_provider():
            # Read per call rather than cached: trust_store.json is seeded once
            # at first run and can be rotated by an admitted key manifest, and a
            # hub holding a stale in-memory copy would keep trusting a key the
            # operator has since revoked.
            return OwnerTrustStore(_hub_licensing_dir / 'trust_store.json')

        # -- The three seams SiteRelayCoordinator cannot build for itself --
        # coordinator.py's docstring is explicit that `start_relay`,
        # `stop_relay` and `attempt_join` have NO production default inside
        # that module, on purpose: each needs plumbing (`get_conn`, the
        # licensing state repository, the device signer, a bind host/port, an
        # identity directory) the coordinator has no way to obtain and must
        # not guess at. Supplying them is THIS function's job. They are
        # closures over the identity work immediately above rather than a
        # second way to reach any of it: there is still exactly ONE
        # `start_site_relay(...)` call in this product, and it is the one
        # inside `_bind_relay_now`.

        def _bind_relay_now():
            """Bind the LAN listener. Serves as the coordinator's
            `start_relay` seam AND as the whole body of the explicit-pin path
            below -- one construction path, not two.

            RE-CHECKS THE MODULE GLOBAL rather than trusting its caller.
            `_site_relay_server` is what `admin_routes.py`'s hub provider
            reads and what this function's own double-start guard tests, so it
            must never hold anything but the ONE listener this process bound.
            The coordinator has its own idempotence guard (`_relay_handle is
            None`), but that protects the COORDINATOR's bookkeeping; this
            global is what decides whether a second `init_app()` binds a
            second socket. Returning the existing handle rather than raising
            keeps the coordinator's HUB branch idempotent in the one case
            where the two could ever disagree.
            """
            global _site_relay_server
            if _site_relay_server is not None:
                # Hub-self-sync (retail-hardware-viewports): idempotent on
                # every call, including this early-return one -- a caller
                # (`_start_sync_after_activation`, step 10) may reach here
                # AFTER the relay was already bound but BEFORE this till had
                # an installation id, and self-sync's own guard (see
                # `_enter_hub_self_sync`) is what actually notices the id
                # has since arrived.
                _enter_hub_self_sync()
                return _site_relay_server

            handle = start_site_relay(
                get_conn=get_retail_conn,
                hub_identity_provider=_hub_identity_provider,
                license_public_id_provider=_hub_license_public_id_provider,
                trust_store_provider=_hub_trust_store_provider,
                # SYNC-BOOT Phase 2: a device told RESYNC_REQUIRED asks THIS hub for a fresh copy
                # of the shop, and the main computer is asked to re-announce the authority state.
                bootstrap_provider=_hub_bootstrap_provider,
                bootstrap_board=_hub_bootstrap_board,
                on_bootstrap_requested=_hub_on_bootstrap_requested,
                host=SITE_RELAY_BIND_HOST,
                port=SITE_RELAY_PORT,
                identity_dir=Path(DATABASE_DIR).parent / 'site-relay',
                installation_id=_hub_installation_id,
                device_public_key=_hub_device_public_key,
                signer=_hub_signer,
                # Hub-self-sync (retail-hardware-viewports): housekeeping is
                # now started ONCE by `_enter_hub_self_sync`, with the hub's
                # own consumer floors wired in from its very first sweep --
                # letting `start_site_relay` start its OWN, floor-blind
                # sweep here too would risk pruning a row this hub's own
                # SyncService pair has not applied yet (see `pruning.py`'s
                # module docstring, reader #3).
                housekeeping=False,
            )
            _site_relay_server = handle
            # The pin is what a paired device must trust, so an operator has to
            # be able to read it back off the hub -- it is the payload of the
            # pairing QR the design describes, and until that screen exists this
            # log line is the only way to get it. It is not a secret (design §4:
            # "The beacon carries no secrets"); it is a public key fingerprint.
            logging.getLogger(__name__).info(
                'Site relay (hub mode) is ON. Devices pair to: %s -- SPKI pin %s',
                ', '.join(f'https://{a}:{SITE_RELAY_PORT}' for a in local_lan_addresses())
                or f'https://<this machine>:{SITE_RELAY_PORT}',
                handle.pin,
            )
            _enter_hub_self_sync()
            return handle

        def _unbind_relay_now(handle):
            """Stop being the hub. The coordinator's `stop_relay` seam, called
            when this till hears a live peer holding a LOWER installation_id
            and must stand down (election.py's rule 2).

            CLEARS THE MODULE GLOBAL TOO, and that is the point of doing this
            here rather than letting the coordinator merely forget its own
            handle: `admin_routes.py`'s /api/site-relay/status answers off
            `_site_relay_server`, so a till that stood down but left the global
            populated would keep telling an operator it is the shop's hub while
            no longer being one.

            Stops the addressing BEACON first, then the listener. A till that
            stopped serving but kept broadcasting would actively pull other
            devices toward a hub that can no longer answer them -- strictly
            worse than never having been a hub at all. The listener shutdown
            sits in a `finally` so a beacon that refuses to stop can never
            leave the socket bound: the socket is the part other devices
            connect TO, and leaving it up is exactly how two answering hubs
            happen.

            Nothing already accepted is lost by standing down -- see
            election.py's "WHY STANDING DOWN LOSES NOTHING": every event this
            till took while it was hub is already in its own site log and is
            forwarded upstream regardless of the role it holds now.

            DELIBERATELY DOES NOT TOUCH `_sync_relay_selector` OR `_site_
            forwarder_loop` (hub-self-sync wave, decision "S1" in this
            task's own plan). This till's own SyncService pair STAYS routed
            through its own site log, and the forwarder KEEPS running,
            forever, even after standing down -- see `election.py`'s own
            "WHY STANDING DOWN LOSES NOTHING" paragraph (updated by that
            same wave) for exactly why reverting to the cloud here would be
            the loss path: this till may hold LAN rows from OTHER paired
            devices that its forwarder has not pushed upstream yet, and
            Owner's cloud pull excludes a device's own events, so those
            rows would never come back down once forwarded. A stood-down
            till converges with the new hub through its own forwarder and
            Owner's cloud relay, not by re-pointing at the new hub directly.

            WHAT STANDING DOWN DOES *NOT* UNDO, written here rather than left
            to be discovered, because both only bite on a till that flaps
            between roles (a hub going in and out of wifi range) and neither is
            visible on the first cycle:
              * UNREDEEMED PAIRING CODES. `start_site_relay` builds a fresh
                in-memory `PairingCodeStore` per call, so a code an operator
                minted before the stand-down is gone if this till is later
                re-elected. It shows up as "the QR I just made says invalid" --
                minting another one is the whole fix, and codes are
                single-use and short-lived by design anyway.
              * THE HOUSEKEEPING TIMER DOES NOT RESTART ON RE-ELECTION.
                `_enter_hub_self_sync` is now the ONE place housekeeping is
                ever started (`_site_housekeeping_stop` is a module global,
                never cleared here) -- a re-election after a stand-down-then-
                reclaim cycle does NOT start a second sweep the way it used
                to before this wave (this bullet used to warn about exactly
                that duplication; it no longer applies).
            The TLS identity is NOT in this list: it is created once and
            reloaded on every subsequent call, so a re-elected hub keeps the
            exact SPKI pin every already-paired device holds.
            """
            global _site_relay_server
            try:
                if getattr(handle, 'beacon', None) is not None:
                    handle.beacon.stop()
                    handle.beacon = None
            finally:
                handle.server.shutdown()
                handle.server.server_close()
                _site_relay_server = None
            logging.getLogger(__name__).info(
                'Site relay (hub mode) is OFF: another till holds the hub role. This '
                'till keeps selling and keeps syncing its own books through its own '
                'site log; the forwarder bridges that log to Owner, so it converges '
                'with the new hub through the cloud.')

        def _attempt_join_now():
            """The coordinator's `attempt_join` seam: one licence-proof
            auto-join attempt against whatever hub this device can currently
            hear. Safe to call on every CLIENT tick by `discover_and_join`'s
            own design -- its ALREADY_JOINED short-circuit makes a repeat call
            a pure no-op with no network traffic at all.

            `trusted_now` is computed PER CALL and never captured at wiring
            time: every real `verify_assertion` caller in this codebase threads
            a fresh clock through for this reason, and a coordinator that has
            been ticking for a week on a timestamp taken at boot would be
            judging today's assertions against last Monday. Same for the trust
            store, fetched through `_hub_trust_store_provider` on each call
            (see its own comment -- trust_store.json can be rotated under a
            running process).

            HUB-SELF-SYNC GUARD (retail-hardware-viewports): a till already
            routed through its OWN site log (`_sync_relay_selector.
            is_local()`) must never also join ANOTHER hub as a LAN client.
            This guard was written against the overwrite of
            `licensing_state.sync_relay_base_url` that a join used to do --
            and it only covered a till that was ALREADY local. REHEARSAL-1007
            B1 is the order it missed: a till joined FIRST (not local yet, so
            this guard let it through), the overwrite landed, and THEN it won
            the election. The overwrite itself is gone now (autojoin records
            the hub in `_joined_hub_store()` and never writes licensing.db),
            so this guard is defence in depth: a till reading and writing its
            own site log has no use for another hub's, and joining would only
            add a pairing row on that hub. `is_local()` is the correct test
            here, not `_site_relay_server is not None`: a STOOD-DOWN till
            (decision S1) is no longer bound but must still refuse to join.
            """
            global _hub_join_refusal_logged
            if _sync_relay_selector is not None and _sync_relay_selector.is_local():
                # Logged ONCE per process, not per call -- see
                # `_hub_join_refusal_logged`'s own comment: this runs on
                # every 5s CLIENT tick for the rest of a stood-down till's
                # life.
                if not _hub_join_refusal_logged:
                    logging.getLogger(__name__).info(
                        'Not joining another hub as a LAN client: this till syncs through '
                        'its own site log and converges via its forwarder, so another '
                        'hub has nothing to give it.')
                    _hub_join_refusal_logged = True
                return

            from datetime import datetime as _datetime, timezone as _timezone

            from commercial_runtime.sync.site_relay.autojoin import (
                discover_and_join)

            # REHEARSAL-1007 B1: the joined hub is recorded in its own
            # sidecar; `_hub_state_repository` (licensing.db, home of the
            # cloud relay address) is only ever READ by autojoin now.
            discover_and_join(
                state_repository=_hub_state_repository,
                signer=_hub_signer,
                trust_store=_hub_trust_store_provider(),
                trusted_now=_datetime.now(_timezone.utc),
                joined_hub_store=_joined_hub_store(),
            )

        # -- EXPLICIT OPERATOR PIN ('1'): bind now, hold NO election ---------
        # AURA_SITE_RELAY_ENABLED=1 is a human saying "THIS machine is the
        # hub" -- config.py documents it as exactly that, and it is also the
        # only way an unlicensed dev/test box can exercise hub mode at all.
        # Running an election on top of it would let a peer holding a
        # lexicographically lower installation_id take the role away from the
        # machine an operator deliberately pinned, which is the opposite of
        # what they asked for, and it would break the synchronous contract
        # retail_site_relay_boots_when_enabled_test.py pins: with '1' set, a
        # relay is bound by the time `init_app()` returns.
        #
        # THIS BRANCH IS ALSO WHAT KEEPS AUTOMATIC ELECTION WINDOWS-ONLY
        # WITHOUT A SECOND PLATFORM CHECK ANYWHERE IN THIS FILE. The only path
        # that reaches the coordinator is the non-'1' path, and
        # `site_relay_should_start` has already refused that path on every
        # platform but Windows (its "A HANDSET NEVER SELF-ELECTS" block, added
        # after a real phone elected itself on real hardware). So a handset
        # cannot reach the election code at all, and an Android dev box that
        # sets '1' explicitly still gets the same direct bind it always got.
        if SITE_RELAY_ENABLE_OVERRIDE == '1':
            _bind_relay_now()
            return

        # -- AUTOMATIC MODE: the ELECTION decides which till is the hub ------
        # THE BUG THIS REPLACES, stated plainly because the code below reads
        # like an optimisation otherwise: when hub mode became automatic
        # (2026-09-14) every activated Windows till started a relay and
        # broadcast a beacon at boot with NOTHING deciding between them, so a
        # two-till shop ran two hubs and its devices split across them, each
        # half seeing a different local ordering of the same day's sales.
        # `SiteRelayCoordinator` is the thing that decides -- and until this
        # wiring, NOTHING CONSTRUCTED IT. It shipped inert behind nine green
        # unit tests, with not one tick ever running on a real till. That is
        # the same failure, and the same shape of fix, as Android's
        # `HubAutoJoinWiringContractTest` docstring records for
        # `HubAutoJoinService` (447 green tests, zero log lines on hardware).
        #
        # NOTE WHAT IS NO LONGER STARTED HERE: on this path `init_app()` binds
        # NOTHING. Nothing binds until the coordinator's first tick decides
        # HUB, which cannot happen before election.py's HUB_LISTEN_SECONDS
        # window has elapsed with no other hub heard. That delay IS the
        # feature -- "listen before claiming" is the main defence against
        # every till in a shop claiming the role in the same instant after one
        # shared power cut.
        if not _hub_installation_id:
            # The election is a TOTAL ORDER over installation_ids; a device
            # with no id cannot take part in one. Handing None to
            # `should_stand_down` raises on the first peer comparison
            # (TypeError: '<' not supported between 'str' and 'NoneType'),
            # which `tick()` would dutifully swallow and log every five
            # seconds forever -- an election that decides nothing, wearing the
            # face of one that is running.
            #
            # FAILS CLOSED (no id -> no hub this run) rather than binding
            # anyway, because an unidentifiable hub is precisely the two-hub
            # state this path exists to prevent. This should be unreachable on
            # a real till: automatic mode already required an ACTIVE_FAMILY
            # licence above, and activation is what writes
            # owner_installation_id. Logged at ERROR because if it IS reached,
            # the shop silently has no hub and this line is the only evidence.
            logging.getLogger(__name__).error(
                'Site relay: this install reports an active licence but no '
                'owner installation id, so it cannot take part in the hub '
                'election and will NOT become a hub this run. Devices on this '
                'LAN will not converge through this till. The till itself is '
                'unaffected and keeps selling.')
            return

        from commercial_runtime.sync.site_relay.coordinator import (
            SiteRelayCoordinator)
        from commercial_runtime.sync.site_relay.election import HUB_LISTEN_SECONDS

        _site_relay_coordinator = SiteRelayCoordinator(
            my_installation_id=_hub_installation_id,
            start_relay=_bind_relay_now,
            stop_relay=_unbind_relay_now,
            attempt_join=_attempt_join_now,
            # The REAL LAN listener -- coordinator.py's own
            # `_default_observe_beacons`, paced by `_observe_lan_beacons_paced`
            # at the top of this module so a failing UDP bind cannot turn the
            # tick loop into a hot spin. `monotonic` is deliberately left at
            # its default of `time.monotonic`: every window this election
            # measures is monotonic seconds, never wall-clock, so an NTP step
            # or a DST change can never make a live hub look silent or an
            # unelapsed listen window look elapsed.
            observe_beacons=_observe_lan_beacons_paced,
        )
        _site_relay_coordinator.start()
        # AUDIT (retail-hardware-viewports, 2026-09-16): this line used to
        # interpolate `_hub_installation_id` into the message text, which
        # leaked it into backend.log -- and from there straight into the
        # diagnostics-export bundle's `log_tail`
        # (retail_api.py::_diagnostics_log_tail), because that route's
        # redaction passes cannot catch it: `_redact_diagnostics_log_line`'s
        # regexes only recognise shapes with a fixed format (email/phone/
        # licence-key), and an installation id has none -- it is an opaque
        # value from the licensing system, same problem a customer NAME has.
        # The name problem is solved by `_redact_known_pii_from_line`
        # exact-matching against a live, per-company "known values" table
        # (`customers`); an installation id has no equivalent authoritative
        # list to match against at export time -- it is a single global
        # value, and a stale one from before a licence re-issue
        # (`rehearsal_reissue.py --keep-device` proved ids DO change) would
        # still leak even if today's current id were added to such a set.
        # Fixing it downstream would mean inventing a second, parallel
        # "known sensitive values" source in the diagnostics module just for
        # this one identifier, AND doing that for every other place that
        # ever logs an installation id (e.g. admin_routes.py's "device %s
        # revoked" line) -- fragile, and easy to miss for the next one.
        # ROOT CAUSE FIX: never put the raw id into a free-text log line in
        # the first place. Nothing downstream needs to guess a shape or
        # maintain a value list for a leak that never happens.
        logging.getLogger(__name__).info(
            'Site relay: automatic hub election is RUNNING. '
            'This till listens for %.0fs before claiming the hub role, and '
            'stands down immediately if it hears a till that already holds it. '
            'No LAN socket is bound until it wins.',
            HUB_LISTEN_SECONDS)
    except Exception:
        _site_relay_server = None
        _site_relay_coordinator = None
        logging.getLogger(__name__).exception(
            'Site relay (hub mode) FAILED to start on %s:%s -- this till is NOT '
            'acting as a hub, so any device paired to it will stop converging '
            'until this is fixed. The till itself is unaffected and keeps '
            'selling. Usual causes: the port is already in use, or a firewall '
            'rule is refusing the bind.',
            SITE_RELAY_BIND_HOST, SITE_RELAY_PORT,
        )


def _announce_device(reason):
    """Registry v9 (Track S): tell the shop's other computers that THIS device
    is up, what build it runs and which schema versions it speaks.

    reason='boot' forces the announcement even when nothing changed: a peer
    that is still on an older build skipped every earlier `device` event, and
    a fresh start is the cheapest moment to heal that. Runs BEFORE the
    registry SyncService starts, so the event it queues rides the very first
    push. reason='heartbeat' (from `_announce_heartbeat_device`, driven by the
    registry stream's own timer) announces only when the last announcement is
    at least `ANNOUNCE_HEARTBEAT_SECONDS` old -- `announce_local_device`
    decides, this function only opens the door. Without it a till that stays
    up for a week looks dead to every peer.

    NEVER RAISES -- same contract as every other boot seam in `init_app()`:
    a till must boot (and keep syncing) even when it cannot announce. Two
    normal reasons it does nothing: no company exists yet (not onboarded --
    the row is written by the first announce after onboarding/activation), or
    the device row belongs to another company (`DeviceCompanyMismatchError`,
    logged loudly, never overwritten)."""
    try:
        from commercial_runtime.sync.sync_service import local_company_id_from_registry
        company_id = local_company_id_from_registry()
        if not company_id:
            # A heartbeat fires every tick; only the one-off boot line is worth
            # writing, or an un-onboarded till logs this every 10 seconds.
            if reason == 'boot':
                logging.getLogger(__name__).info(
                    'Device announce skipped: no company on this install yet.')
            return
        from commercial_runtime.identity.device_context import announce_local_device
        conn = get_registry_conn()
        try:
            announce_local_device(
                conn, company_id, reason=reason,
                app_version=APP_VERSION,
                retail_schema_version=RETAIL_SCHEMA_VERSION,
            )
            # AUDIT-033 / S-AUTH: pre-bind THIS computer's own device id to its
            # own installation and key in `sync_signers`, so no peer can get in
            # first and claim it. The sync engine also does this lazily before it
            # judges its first event; doing it at boot (and again after
            # activation, via the 'changed' announce) just closes the window
            # earlier. Best effort: a till with no key or lease yet has nothing
            # to bind.
            from commercial_runtime.sync import authority as _authority
            _signer = _authority.local_signer_or_none(Path(DATABASE_DIR).parent)
            if _signer is not None:
                _authority.ensure_self_bound(conn, company_id, _signer)
            conn.commit()
            # AUDIT-033 B2b: once this computer can sign (and again when it becomes
            # the main computer), re-state the shop's authority events SIGNED, so a
            # computer that joins later does not receive only pre-upgrade unsigned
            # history that every peer parks. Cheap when nothing is due; never raises.
            _reannounce_authority_state(conn, company_id)
        finally:
            conn.close()
    except Exception:
        logging.getLogger(__name__).warning(
            'Device announce (%s) failed; this till keeps running.', reason,
            exc_info=True)


def _make_retail_emit(company_id):
    """The retail half of a re-announce: queues this computer's till allocations, numbering books
    and branch aliases into retail.db's outbox (the payload builders live in `retail_api`; the
    shared package must not import the product)."""
    def _retail_emit(is_main, terminal_uid):
        from api.retail_api import reannounce_authority_events
        from database.schema import get_retail_conn
        retail_conn = get_retail_conn()
        try:
            return reannounce_authority_events(
                retail_conn, company_id, is_main=is_main, terminal_uid=terminal_uid)
        except Exception:
            retail_conn.rollback()
            raise
        finally:
            retail_conn.close()
    return _retail_emit


def _reannounce_authority_state(reg_conn, company_id):
    """The retail half of `commercial_runtime.sync.reannounce.run_if_due`: hands it
    a callback that queues this computer's till allocations / numbering books into
    retail.db's outbox (the payload builders live in `retail_api`; the shared
    package must not import the product). NEVER RAISES."""
    try:
        from commercial_runtime.sync import reannounce as _reannounce

        _reannounce.run_if_due(reg_conn, company_id, app_data_dir=Path(DATABASE_DIR).parent,
                               retail_emit=_make_retail_emit(company_id))
    except Exception:
        logging.getLogger(__name__).warning(
            'Authority re-announce failed; this till keeps running.', exc_info=True)


# ── SYNC-BOOT Phase 2: bootstrap seams ──────────────────────────────────────────────────────────
# A computer far enough behind its relay that the relay has pruned what it needs takes a COPY of
# the shop instead of a replay (commercial_runtime/sync/bootstrap/). Everything below is glue: the
# logic lives in the shared package, the product-specific parts (which tables are what) in
# database/bootstrap_classes.py. Every function here NEVER RAISES: a bootstrap nicety must not be
# able to stop a till from booting, pulling, or ringing up a sale.

from commercial_runtime.sync.bootstrap.board import BootstrapBoard as _BootstrapBoard  # noqa: E402

_hub_bootstrap_board = _BootstrapBoard()


def _bootstrap_app_dir():
    return Path(DATABASE_DIR).parent


def _bootstrap_include_own_pending(stream):
    from commercial_runtime.sync.bootstrap import installer
    return installer.include_own_once_pending(_bootstrap_app_dir(), stream)


def _bootstrap_include_own_done(stream):
    from commercial_runtime.sync.bootstrap import installer
    installer.clear_include_own_once(_bootstrap_app_dir(), stream)


def _bootstrap_reapply_pending():
    from commercial_runtime.sync.bootstrap import installer
    return installer.reapply_outbox_pending(_bootstrap_app_dir())


def _bootstrap_reapply_done():
    from commercial_runtime.sync.bootstrap import installer
    installer.clear_reapply_outbox(_bootstrap_app_dir())


def _apply_staged_bootstrap():
    """Install a bootstrap copy staged by a previous run. Called first thing in `init_app()`, BEFORE
    any database of this product is opened: the swap replaces retail.db, which cannot be done under
    a running app. A copy from an older build is migrated forward here (`migrate_retail_file`).
    Returns the installer's report (or None). NEVER RAISES."""
    try:
        from commercial_runtime.sync.bootstrap import installer
        from database.bootstrap_classes import retail_spec
        from database.schema import migrate_retail_file
        report = installer.apply_staged(
            _bootstrap_app_dir(), retail_spec(RETAIL_SCHEMA_VERSION), migrate_fn=migrate_retail_file)
        if report is not None:
            logging.getLogger(__name__).warning('Bootstrap install: %s', report)
        return report
    except Exception:
        logging.getLogger(__name__).exception('Bootstrap install failed unexpectedly; booting on the old data.')
        return None


def _after_backup_restore():
    """The backup blueprint's `restart_after_restore`: arm include_own_once for BOTH streams (a
    restore replaces registry.db and retail.db), then ask for the usual restart."""
    try:
        from commercial_runtime.sync.bootstrap import installer
        installer.mark_include_own_once(_bootstrap_app_dir(), 'backup-restore')
    except Exception:
        logging.getLogger(__name__).warning('Could not arm include_own_once after a restore.', exc_info=True)
    from commercial_runtime.install_support.restart import schedule_restart
    return schedule_restart()


def _bootstrap_on_resync(stream, client, relay_url, details):
    """This device's relay said RESYNC_REQUIRED. For the RETAIL stream against a LAN hub: request a
    copy of the shop from the hub, verify and stage it, and ask the launcher to restart so the next
    start installs it. Returns True when a copy is staged. Owner's own mailbox is Phase 3, so a cloud
    relay returns False (the stream stays stopped and visible in /sync/health)."""
    log = logging.getLogger(__name__)
    try:
        if stream != 'retail' or not hasattr(client, 'post_signed_raw'):
            return False
        from commercial_runtime.sync import authority as _authority
        from commercial_runtime.sync.bootstrap import transport_hub
        from commercial_runtime.sync.bootstrap.errors import BootstrapError
        from commercial_runtime.sync.relay_client import SyncRelayClientError
        from commercial_runtime.sync.site_relay.upstream import is_lan_hub_url
        from commercial_runtime.sync.sync_service import local_company_id_from_registry
        from database.bootstrap_classes import retail_spec
        ports, known = _lan_hub_signals()
        if not is_lan_hub_url(relay_url, hub_ports=ports, known_hub_urls=known):
            # Owner's cloud relay: ask its mailbox. A peer builds the copy, so this WAITS (a
            # background poller, resumed from a state file after a restart) instead of staging now.
            _start_owner_requester(client, relay_url)
            return False
        env = _authority.default_authority_env(_bootstrap_app_dir())
        try:
            record = transport_hub.request_and_stage(
                client, spec=retail_spec(RETAIL_SCHEMA_VERSION), app_data_dir=_bootstrap_app_dir(),
                relay_url=relay_url, trust_store=env.trust_store, own_licence=env.own_licence,
                expected_company_id=local_company_id_from_registry())
        except (BootstrapError, SyncRelayClientError) as exc:
            log.warning('Bootstrap: could not get a copy of the shop from the hub (%s). This computer '
                        'stays stopped on that stream until it is restarted.',
                        getattr(exc, 'reason_code', type(exc).__name__))
            return False
        log.warning('Bootstrap: a copy of the shop (complete up to relay position %s) is staged; it is '
                    'installed at the next start.', record.get('as_of'))
        from commercial_runtime.install_support.restart import schedule_restart
        schedule_restart()
        return True
    except Exception:
        log.exception('Bootstrap request failed unexpectedly.')
        return False


_OWNER_REQUESTER_LOCK = threading.Lock()
_OWNER_REQUESTER_THREAD = None
#: Test seam (the acceptance harness sets it): run the Owner-mailbox work INLINE and do not
#: auto-start the requester's poller, so a short-lived device process can drive each step
#: itself and a test can interleave several computers deterministically. Never set in production.
_BOOTSTRAP_INLINE = False


def _make_owner_requester(client, relay_url):
    """The `OwnerRequester` for this computer against `client`'s relay -- one construction, used by
    the background poller and by the harness's stepwise driver."""
    from commercial_runtime.sync import authority as _authority
    from commercial_runtime.sync.bootstrap import transport_owner
    from commercial_runtime.sync.sync_service import local_company_id_from_registry
    from database.bootstrap_classes import retail_spec
    env = _authority.default_authority_env(_bootstrap_app_dir())

    def _staged(record):
        logging.getLogger(__name__).warning(
            'Bootstrap: a copy of the shop from Owner (complete up to relay position %s) is staged; it is '
            'installed at the next start.', record.get('as_of'))
        if not _BOOTSTRAP_INLINE:
            from commercial_runtime.install_support.restart import schedule_restart
            schedule_restart()

    return transport_owner.OwnerRequester(
        transport_owner.MailboxApi(client), spec=retail_spec(RETAIL_SCHEMA_VERSION),
        app_data_dir=_bootstrap_app_dir(), relay_url=relay_url, trust_store=env.trust_store,
        own_licence=env.own_licence, expected_company_id=local_company_id_from_registry(),
        app_version=str(APP_VERSION), after_staged=_staged)


def _start_owner_requester(client, relay_url):
    """SYNC-BOOT Phase 3: RESYNC_REQUIRED from Owner's cloud relay -> poll its bootstrap mailbox
    until a peer has produced a copy, then stage it and ask for the restart that installs it. One
    poller per process; NEVER RAISES (a till keeps selling while it waits)."""
    global _OWNER_REQUESTER_THREAD
    log = logging.getLogger(__name__)
    try:
        if _BOOTSTRAP_INLINE or not hasattr(client, 'post_signed_json'):
            return False
        with _OWNER_REQUESTER_LOCK:
            if _OWNER_REQUESTER_THREAD is not None and _OWNER_REQUESTER_THREAD.is_alive():
                return False
            requester = _make_owner_requester(client, relay_url)
            thread = threading.Thread(
                target=lambda: log.warning('Bootstrap requester finished: %s', requester.run()),
                name='aura-bootstrap-requester', daemon=True)
            _OWNER_REQUESTER_THREAD = thread
            thread.start()
        return True
    except Exception:
        log.exception('Could not start the bootstrap requester.')
        return False


_MAILBOX_WORKER_LOCK = threading.Lock()
_MAILBOX_SIGHTINGS = {}
#: A non-main computer lets the main claim first: it only steps in once it has seen the same
#: request this many times (pulls), i.e. the main has had two ticks and did not.
_MAILBOX_PEER_PATIENCE = 3


def _is_main_computer():
    try:
        from commercial_runtime.identity.device_registry import get_assignment
        from commercial_runtime.sync import authority as _authority
        from commercial_runtime.sync.sync_service import local_company_id_from_registry
        company_id = local_company_id_from_registry()
        device_id = _authority._local_device_id(_bootstrap_app_dir())
        if not company_id or not device_id:
            return False
        reg = get_registry_conn()
        try:
            assignment = get_assignment(reg, company_id)
        finally:
            reg.close()
        return bool(assignment and str(assignment['device_id']) == device_id)
    except Exception:
        return False


def _bootstrap_on_mailbox_requests(requests, client, relay_url):
    """Owner told this producer-capable computer that others wait. Two jobs, both done on a worker
    thread so the pull that delivered the news is never held up by a 50 MB copy:
      * the MAIN computer re-announces authority (staff, till numbers) for a registry request or a
        retail copy that is READY, then tells Owner (`fulfil`);
      * a computer that holds the shop builds, encrypts and uploads the copy for an OPEN retail
        request -- the main first, anyone else after `_MAILBOX_PEER_PATIENCE` sightings.
    NEVER RAISES."""
    log = logging.getLogger(__name__)
    try:
        if not requests or not hasattr(client, 'post_signed_json'):
            return
        if not _MAILBOX_WORKER_LOCK.acquire(blocking=False):
            return                                     # a previous batch is still being worked on
    except Exception:
        return

    def _work():
        try:
            from commercial_runtime.sync import authority as _authority
            from commercial_runtime.sync.bootstrap import transport_owner
            from commercial_runtime.sync.bootstrap.producer import build_bootstrap
            from commercial_runtime.sync.sync_service import local_company_id_from_registry, resync_streams_snapshot
            from database.bootstrap_classes import retail_spec
            from database.schema import _get_path
            api = transport_owner.MailboxApi(client)
            live = {r['request_id'] for r in requests}
            for stale in [k for k in _MAILBOX_SIGHTINGS if k not in live]:
                _MAILBOX_SIGHTINGS.pop(stale, None)
            is_main = None
            for req in requests:
                rid = req['request_id']
                wants_authority = req.get('stream') == 'registry' or req.get('status') in ('READY', 'DELIVERED')
                if wants_authority:
                    if is_main is None:
                        is_main = _is_main_computer()
                    if is_main:
                        report = _force_main_reannounce('Owner request %s' % rid)
                        if report and report.get('ran'):
                            api.fulfil(rid)
                    continue
                if req.get('stream') != 'retail' or req.get('status') != 'OPEN':
                    continue
                seen = _MAILBOX_SIGHTINGS[rid] = _MAILBOX_SIGHTINGS.get(rid, 0) + 1
                if is_main is None:
                    is_main = _is_main_computer()
                if not is_main and seen < _MAILBOX_PEER_PATIENCE:
                    continue
                if 'retail' in resync_streams_snapshot():
                    continue                           # we are behind the prune ourselves: our copy would be too
                theirs = req.get('requester_schema_version')
                if isinstance(theirs, int) and theirs < RETAIL_SCHEMA_VERSION:
                    continue                           # they cannot read a copy newer than themselves
                company_id = local_company_id_from_registry()
                if not company_id:
                    continue
                signer = _authority.local_signer_or_none(_bootstrap_app_dir())

                def _build(requester, _signer=signer, _company=company_id):
                    return build_bootstrap(
                        spec=retail_spec(RETAIL_SCHEMA_VERSION), source_path=_get_path('retail'),
                        company_id=_company, signer=_signer, expected_relay_url=relay_url,
                        requester=requester)

                outcome = transport_owner.produce_for(api, req, build=_build, schema_version=RETAIL_SCHEMA_VERSION)
                log.warning('Bootstrap: request %s -> %s', rid, outcome)
        except Exception:
            log.warning('Bootstrap: answering Owner mailbox requests failed (they stay open).', exc_info=True)
        finally:
            _MAILBOX_WORKER_LOCK.release()

    try:
        if _BOOTSTRAP_INLINE:
            _work()
        else:
            threading.Thread(target=_work, name='aura-bootstrap-producer', daemon=True).start()
    except Exception:
        _MAILBOX_WORKER_LOCK.release()
        log.exception('Could not start the bootstrap producer thread.')


def _force_main_reannounce(reason):
    """Re-announce the shop's authority state NOW if THIS computer is the main one. Returns the
    report (or None). NEVER RAISES."""
    try:
        from commercial_runtime.sync import reannounce as _reannounce
        from commercial_runtime.sync.sync_service import local_company_id_from_registry
        company_id = local_company_id_from_registry()
        if not company_id:
            return None
        reg = get_registry_conn()
        try:
            return _reannounce.force_main_reannounce(
                reg, company_id, app_data_dir=_bootstrap_app_dir(), retail_emit=_make_retail_emit(company_id))
        finally:
            reg.close()
    except Exception:
        logging.getLogger(__name__).warning('Forced authority re-announce (%s) failed.', reason, exc_info=True)
        return None


def _bootstrap_on_requests(requests):
    """A hub told this producer-capable computer that others are waiting. Only the main computer
    acts (force_main_reannounce checks); everyone else does nothing and costs nothing."""
    if requests:
        _force_main_reannounce('hub request: %s' % ', '.join(sorted({r.get('requester', '?') for r in requests})))


def _hub_on_bootstrap_requested(requester, stream):
    """Called by this hub's own routes when a device asks. If this computer IS the main it answers at
    once; if not, the request sits on the board for the main's next pull."""
    _force_main_reannounce('device %s (%s)' % (requester, stream))


def _hub_bootstrap_provider(requester_installation_id, requester_schema_version):
    """Build the copy for `POST /bootstrap` from THIS hub's own retail.db. Raises BootstrapError for
    a refusal the route reports (no signer, hub not on its site log, requester too old)."""
    from commercial_runtime.sync import authority as _authority
    from commercial_runtime.sync.bootstrap import errors as _bootstrap_errors
    from commercial_runtime.sync.bootstrap.errors import BootstrapError
    from commercial_runtime.sync.bootstrap.producer import BootstrapOffer, build_bootstrap
    from commercial_runtime.sync.site_relay import store as _site_store
    from commercial_runtime.sync.site_relay.local_client import LOCAL_RELAY_URL
    from commercial_runtime.sync.sync_service import local_company_id_from_registry
    from database.bootstrap_classes import retail_spec
    from database.schema import _get_path

    if requester_schema_version is not None and requester_schema_version < RETAIL_SCHEMA_VERSION:
        # The copy is this hub's schema; a requester on an older build cannot read it (it can only
        # migrate forward from a copy that is not newer than itself). Say so before building 50 MB.
        raise BootstrapError(
            _bootstrap_errors.SCHEMA_NEWER,
            'this hub is on a newer version than the device asking; update the device first')
    company_id = local_company_id_from_registry()
    if not company_id:
        raise BootstrapError(_bootstrap_errors.PRODUCER_FAILED, 'this computer has no company yet')
    signer = _authority.local_signer_or_none(_bootstrap_app_dir())
    built = build_bootstrap(
        spec=retail_spec(RETAIL_SCHEMA_VERSION), source_path=_get_path('retail'), company_id=company_id,
        signer=signer, expected_relay_url=LOCAL_RELAY_URL, requester=requester_installation_id)
    conn = get_retail_conn()
    try:
        end_of_log = _site_store.max_event_seq(conn)
    finally:
        conn.close()
    return BootstrapOffer(data=built.data, as_of=built.as_of, registry_fast_forward=end_of_log)


def _announce_when_company_established(company_id=None):
    """The identity layer's `company_established` hook (signup): announce THIS
    computer the moment a company exists, not at the next restart. See
    device_context.notify_company_established."""
    _announce_device('changed')


# Registered at import time, like the other identity seams: the hook must exist
# before the first signup request, and init_app() may run more than once in a
# test process (re-registering the same function is harmless).
from commercial_runtime.identity import device_context as _identity_device_context  # noqa: E402
_identity_device_context.set_company_established_hook(_announce_when_company_established)


def _announce_boot_device():
    """`_announce_device('boot')`, called once from `init_app()`."""
    _announce_device('boot')


def _announce_heartbeat_device():
    """`_announce_device('heartbeat')`, called on every tick of the registry
    SyncService (its `on_tick` hook)."""
    _announce_device('heartbeat')


def _sweep_cash_pair_at_boot():
    """AUDIT-032 boot sweep: a non-admin who ALREADY holds both cash.close and
    cash.approve (written by a build before the guard, or by hand) is never
    looked at by the per-event rule, so narrow them once per boot. Runs right
    after the registry migration. NEVER RAISES: a till must boot. Only ever
    narrows access and emits no sync (every device reaches the same answer)."""
    try:
        from commercial_runtime.sync.sync_service import local_company_id_from_registry
        from commercial_runtime.identity.user_accounts import enforce_cash_pair_all
        company_id = local_company_id_from_registry()
        if not company_id:
            return
        conn = get_registry_conn()
        try:
            fixed = enforce_cash_pair_all(conn, company_id)
            conn.commit()
        finally:
            conn.close()
        if fixed:
            logging.getLogger(__name__).warning(
                'Boot sweep removed cash.approve from %d account(s) that held '
                'both cash.close and cash.approve.', len(fixed))
    except Exception:
        logging.getLogger(__name__).warning(
            'Cash-pair boot sweep failed; this till keeps booting.', exc_info=True)


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
    both non-`None` together on Windows (both are only ever built together,
    by `_build_windows_sync_services()`: at import from the `if
    _SYNC_RELAY_URL_IS_USABLE and LICENSING_PLATFORM != 'ANDROID':` block
    above, or later by `_start_sync_after_activation()`, which starts them
    itself because this function has already run by then), but each is a
    genuinely separate
    `SyncService` with its OWN timer/thread/lock, so each needs its own
    `.start()` call -- `.start()` on one has no effect on the other's own
    scheduling.

    HUB-SELF-SYNC BOOT RESUME (retail-hardware-viewports): between the
    schema migrations and the `.start()` calls above, this function also
    calls `_enter_hub_self_sync(start_timers=False)` when `_hub_self_sync_
    was_active_before()` says this till has hosted a site log before --
    see that function's own docstring for why a restart must resume LOCAL
    mode immediately rather than reverting to the cloud first."""
    # First, before anything else touches a database or starts a thread --
    # see `_configure_file_logging`'s own docstring for why this is the one
    # seam guaranteed to run for both the real entry point and every test
    # bootstrap. Never fatal: a logging failure must not stop a till booting.
    _configure_file_logging()
    # SYNC-BOOT Phase 2: install a staged bootstrap copy BEFORE any database is opened (the swap
    # replaces retail.db). A copy from an older build is migrated forward inside this call.
    _apply_staged_bootstrap()
    init_registry_db()
    # AUDIT-032: narrow any pre-existing cash.close + cash.approve pair.
    _sweep_cash_pair_at_boot()
    init_retail()
    # Launch-readiness Phase 5 prerequisite #1 -- must run AFTER both
    # migrations (it is what catches up when a migration's own version gate
    # can no longer fire, see the docstring above) and BEFORE anything below
    # this line reads company-scoped retail data, so a stuck install is
    # caught before the sync loop or any outbox worker touches it.
    _converge_and_refuse_to_serve_if_stuck()
    # Hub-self-sync boot resume (retail-hardware-viewports, decision A4(b)):
    # a till that has HOSTED a site log before (this restart, or any prior
    # one) must resume LOCAL mode immediately, before the first tick --
    # never fall back to the cloud first and only later flip, which would
    # briefly (or, on a LAN-only shop, permanently) strand unforwarded LAN
    # rows. `start_timers=False` here: the existing `.start()` calls just
    # below run exactly once regardless of whether the pair was built at
    # import time, LAN-only by this resume call, or later by `_start_sync_
    # after_activation` -- see `_enter_hub_self_sync`'s own docstring.
    # Runs BEFORE any request can be served (`_INIT_APP_HAS_RUN` is only set
    # True at the end of this function), so `nudge()` can never race this
    # flip -- see that function's own docstring.
    #
    # `_ensure_runtime_schema_for_sync()` (retail-hardware-viewports): runs
    # BEFORE the hub-self-sync resume and BEFORE either SyncService's own
    # `.start()` call just below -- see that function's own docstring for
    # the "no such table: retail_settings" defect this closes. This also
    # covers Android: Chaquopy's entry point (android/aura-retail/app/src/
    # main/python/main.py `_run_server`) calls this SAME `init_app()` before
    # waitress ever binds the port Kotlin's SyncCoordinator talks to, so no
    # separate Android seam is needed here.
    _ensure_runtime_schema_for_sync()
    # REHEARSAL-1007 B1 (b): before either SyncService starts, rebuild the
    # import-time pair if its cloud address is a LAN hub address an older
    # autojoin wrote over Owner's relay -- see the function's docstring.
    _repair_relay_url_at_boot()
    if LICENSING_PLATFORM != 'ANDROID' and _hub_self_sync_was_active_before():
        # REHEARSAL-1007 B1 (c): the operator kill switch now WINS over the
        # boot resume. Decision A4(b)/A7 used to resume self-sync even under
        # AURA_SITE_RELAY_ENABLED=0 (to avoid stranding unforwarded LAN rows);
        # on the rehearsal's till B that meant `-SiteRelay 0` could not get it
        # off its own site log. The stranding concern is now handled by
        # `_leave_hub_self_sync_under_kill_switch` without staying a hub.
        if SITE_RELAY_ENABLE_OVERRIDE == '0':
            _leave_hub_self_sync_under_kill_switch()
        else:
            _enter_hub_self_sync(start_timers=False)
    # Registry v9: announce this device BEFORE the registry stream starts, so
    # the queued `device` event rides the first push. Never fatal.
    _announce_boot_device()
    if _sync_service is not None:
        _sync_service.start()
    if _registry_sync_service is not None:
        _registry_sync_service.start()
    # After init_retail() above, necessarily: the relay serves the six
    # `site_*` tables that migration creates, and a hub that bound its port
    # before they existed would answer its first push with a 500.
    _start_site_relay_if_enabled()
    _resume_einvoicing_workers()
    _resume_notifications_workers()
    _resume_whatsapp_workers()
    _start_update_polling()
    _start_crash_report_sender()
    _start_backup_scheduler()
    global _INIT_APP_HAS_RUN
    _INIT_APP_HAS_RUN = True
    return app


def create_app():
    """Application factory -- the single correct construction path this
    module was missing (retail-hardware-viewports Fix 1).

    WHAT "BUILD" ALREADY MEANS HERE: `app = Flask(...)` near the top of
    this file, and every `app.register_blueprint(...)` call below it, run
    unconditionally at IMPORT time -- not inside this function, and that is
    deliberate and UNCHANGED by this fix (see the backward-compatibility
    note below). The module-level `app` object already exists, fully
    routed, the moment this module is imported; ~140 existing test files'
    bootstraps (`import app as _app_module; app = _app_module.init_app()`)
    and the Android/Chaquopy embedding all depend on exactly that. Moving
    the `Flask(...)` construction and blueprint registration INTO this
    function would break every one of those call sites -- a separate,
    larger migration, not attempted here.

    WHAT THIS FUNCTION ACTUALLY ADDS: the missing call to `init_app()` --
    schema migrations, the split-state boot guard, background worker/sync
    startup -- which, before this, only ran from the `if __name__ ==
    '__main__':` block at the bottom of this file. `_INIT_APP_HAS_RUN`'s
    own docstring above spells out the exact failure this closes: starting
    this module any other way (most plausibly `flask --app app run`, which
    imports the module and stops there) produced a process that bound its
    port and answered `GET /api/health` with 200 -- "looking perfectly
    healthy" -- then failed the first real request with
    `sqlite3.OperationalError: no such table: users`. `create_app()` is now
    the one path that both builds (already done at import) AND initialises.

    SINGLE-SHOT, NOT "MAKE init_app() ITSELF IDEMPOTENT" -- a deliberate
    choice, made by reusing the SAME `_INIT_APP_HAS_RUN` flag
    `_refuse_to_serve_before_init_app` already reads, rather than teaching
    `init_app()` to tolerate being re-run. `init_app()` starts background
    timers that are NOT provably safe to start twice: `_sync_service.start()`
    / `_registry_sync_service.start()` mirror `licensing_contracts/
    checkin_scheduler.py`'s LicenseCheckInScheduler, which -- per the
    `_IdempotentEinvoicingWorkerHandle` comment above -- does NOT cancel a
    running Timer on a second `.start()`, it only overwrites the handle to
    it, so the first Timer chain keeps firing forever alongside a new one.
    `_resume_notifications_workers()` / `_resume_whatsapp_workers()` call
    `.start()` directly on `EmailOutboxWorker`/`WhatsAppOutboxWorker` with
    no idempotent wrapper at all (unlike the einvoicing sweep, which is
    wrapped precisely because it needed one -- see that class's docstring).
    Re-running `init_app()` on a second `create_app()` call would therefore
    risk duplicate background timers, not a harmless no-op re-init. So: the
    FIRST call in a process runs `init_app()` and returns the
    fully-initialised `app`; every later call is a no-op that returns the
    SAME already-initialised `app` without touching `init_app()` again --
    calling this factory more than once is safe, but only because the
    second call is skipped outright, not because `init_app()` was made
    re-entrant.

    WHAT THIS DOES NOT FIX (being honest about it): this does not let
    separate test files share one process. The module-level `app` -- its
    blueprints, its DB connections -- is still built at IMPORT time, which
    is exactly what the ~140 existing test bootstraps and the Android
    embedding require. Sharing one process across test files needs THAT to
    change (lazy construction of `app` itself), which is a separate, much
    larger change than this one. Do not read this factory as a test-speed
    improvement -- it removes the `flask run` trap and gives a single
    construction path, nothing more.
    """
    global _INIT_APP_HAS_RUN
    if not _INIT_APP_HAS_RUN:
        init_app()
    return app


def _resume_einvoicing_workers():
    """A company that had e-invoicing enabled before a restart (PC reboot,
    app update) must keep submitting without waiting for a settings write
    to notice -- sweep for companies with the feature already on and
    resume their background workers. A fresh/never-enabled install finds
    zero rows here and starts zero threads.

    AUDIT: e-invoicing now defaults ON (settings.py DEFAULTS['enabled']=
    '1'), so a company enabled purely by that default has NO row at all in
    einvoice_settings -- the old query above (`WHERE skey='enabled' AND
    svalue='1'`) only ever matched an EXPLICIT row, so it silently missed
    every default-only-enabled company on every restart, and their workers
    never resumed: documents would queue in einvoice_outbox forever, even
    after real JoFotara credentials were configured, because nothing ever
    ticked the worker again after the process that first enqueued them
    exited. The set of companies that need a worker is exactly the set
    with something WAITING for one -- companies with an explicit enabled='1'
    row, UNIONed with companies that already have einvoice_outbox rows
    (the default-only-enabled ones, since enqueue_sale/enqueue_invoice run
    regardless of whether enabled_at was ever stamped) -- each candidate is
    then filtered through settings.is_enabled() so the killswitch and an
    explicit '0' still win over an outbox that happens to have old rows in
    it. One pass, one start() call per company (the idempotent handle
    above also makes a second call harmless, but this dedupes via UNION
    and the loop so there's normally never a second call to make)."""
    conn = get_retail_conn()
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


def _resume_notifications_workers():
    """Mirrors _resume_einvoicing_workers() immediately above -- same
    reasoning: a company that had email notifications enabled before a
    restart must keep draining its outbox without waiting for a settings
    write to notice. A fresh/never-enabled install finds zero rows here and
    starts zero threads, same as einvoicing's own sweep.

    Real-email wave: (1) with no way for mail to leave this install at all (no
    SMTP host, no Owner URL) nothing is started -- this sweep used to start
    workers that could only ever no-op; (2) a company with account mail still
    waiting (a verification link queued before a restart or before activation)
    gets its worker even though it never touched the notifications toggle."""
    if not _mail_transport_possible():
        return
    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT company_id FROM email_settings WHERE skey='enabled' AND svalue='1' "
            "UNION SELECT company_id FROM email_outbox WHERE status IN ('QUEUED','SENDING')"
        ).fetchall()
        for row in rows:
            cid = row[0]
            interval = int(_notification_settings.get_setting(conn, cid, 'submit_interval_seconds'))
            _start_notifications_worker_once(cid, interval)
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


def _run_server(port):
    """Serve `app` via waitress (this codebase's production WSGI server),
    with a governed fallback -- see the Fix 2 comments in the `except`
    branch below for why a packaged build must never silently fall back to
    Flask's development server (retail-hardware-viewports Fix 2).

    Factored out of the `if __name__ == '__main__':` block below so the
    fallback/refusal branch is directly callable -- e.g. with `waitress`'s
    import forced to fail and `commercial_runtime.security.modes.IS_FROZEN`
    monkeypatched to True -- without having to run this whole module as a
    script to exercise it.
    """
    try:
        from waitress import serve as _serve
        _serve(app, host='127.0.0.1', port=port, threads=12,
               channel_timeout=120, connection_limit=200, _quiet=True)
    except ImportError:
        # Fix 2: a packaged build silently falling back to Flask's
        # development server used to be entirely silent -- nothing logged,
        # nothing refused. That is a production-grade problem: the dev
        # server is not built for a real shop's sustained, concurrent till
        # traffic, and a shop quietly running on it is a support incident
        # nobody can diagnose -- every symptom (occasional dropped
        # requests, no real concurrency under load) looks like a random bug
        # rather than "the wrong server started."
        #
        # `IS_FROZEN` is this codebase's OWN existing idiom for "is this a
        # packaged build" (commercial_runtime/security/modes.py -- "Single
        # source of truth for which environment is this process running
        # in"), imported lazily inside the function exactly the way
        # retail_api.py's demo-mode gate already does. Reused here rather
        # than re-deriving `getattr(sys, 'frozen', False)` a second time:
        # the raw check at the very top of this file only exists because
        # THAT path-resolution code has to run before sys.path even
        # includes commercial_runtime -- not the case here, deep into the
        # module after every import above has already succeeded.
        from commercial_runtime.security.modes import IS_FROZEN
        if IS_FROZEN:
            logging.getLogger(__name__).error(
                'FATAL: waitress is missing in a PACKAGED build -- refusing '
                'to start. Aura Retail must never run a real shop on the '
                'Flask development server: it is not built for sustained '
                'concurrent till traffic, and a shop silently running on it '
                'is a support incident nobody can diagnose (every symptom '
                'looks like a random bug rather than "the wrong server is '
                'running"). This means the packaged build is missing a '
                'dependency -- waitress must be bundled (check the '
                'PyInstaller .spec file\'s hidden imports and '
                'requirements/retail.txt). Exiting without serving.'
            )
            sys.exit(1)
        logging.getLogger(__name__).warning(
            'waitress is not installed -- falling back to the Flask '
            'development server. This is acceptable for local development '
            'ONLY. It must never serve a real shop: install waitress (see '
            'requirements/retail.txt) before packaging, or before pointing '
            'a real till at this process.'
        )
        app.run(host='127.0.0.1', port=port, debug=False)


if __name__ == '__main__':
    create_app()
    port = int(os.environ.get('PORT', 5000))
    _run_server(port)
