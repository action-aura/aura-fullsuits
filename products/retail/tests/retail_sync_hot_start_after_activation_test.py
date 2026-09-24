"""Aura Retail -- retail-hardware-viewports: sync/LAN-hub hot-start after a
mid-session licence activation, with no restart required.

THE DEFECT, measured 2026-09-24 by running the product: on a fresh desktop
till with no AURA_SYNC_RELAY_URL env var, activating a licence WHILE THE APP
IS RUNNING never started cloud sync and never started the LAN hub election
until the process was restarted, and nothing told the operator. Root cause,
verified in code:

    1. `_sync_service`/`_registry_sync_service` (app.py) are built EXACTLY
       ONCE, at import time, gated on `_SYNC_RELAY_URL_IS_USABLE` -- which
       reads config.py's `SYNC_RELAY_BASE_URL` module constant, itself
       frozen the moment config.py was imported (the env var, or whatever
       Owner had ALREADY persisted to licensing.db before this process
       started). A fresh till has neither, so both constants are '' for the
       rest of that process's life no matter what happens afterwards --
       config.py used to say so explicitly, in a comment now rewritten:
       "An operator who activates while the app is already running must
       restart it for sync to come on."
    2. `_start_site_relay_if_enabled()` runs exactly once, from `init_app()`,
       and reads the licence state THEN (unactivated -> no hub). Its own
       guard against a second listener/coordinator makes a LATER call safe,
       but nothing was ever calling it again.

Consequence: the first till's admin 'create' user event sat in
`sync_outbox` forever, and a second device joining the same shop never
received the owner's account, because nothing ever pushed it.

THE FIX under test here:
    - config.py::resolve_sync_relay_base_url_now() -- the SAME precedence/
      validation the frozen module constants use, recomputed FRESH at call
      time (see its own docstring).
    - app.py::_build_windows_sync_services(relay_base_url) -- the exact
      construction logic that used to run only inline at import, now a
      function taking the relay URL as a parameter so it can run a SECOND
      time, later, with a value that did not exist yet at import.
    - app.py::_start_sync_after_activation() -- the hot-start seam itself,
      called from `_on_licence_activated()` (the real `on_activation_
      success` hook `make_licensing_blueprint` wires into `/activate`) only
      when the split-state check found nothing wrong.

Own subprocess/module -- config.py/app.py resolve environment variables
once at import time, so this file must be the only one in its pytest
process asserting on this particular combination (same reasoning as every
neighbouring sync test file's own docstring, and the reason
products/run_all_tests.py runs one file per pytest process).

AURA_SITE_RELAY_ENABLED=0 is set below (the operator kill switch) so
nothing in this file ever binds a real LAN-facing socket -- every test
spies on `_start_site_relay_if_enabled` as a call-count-only replacement
rather than letting the real listener/election run; that function's own
behaviour is proven separately by retail_site_relay_election_wiring_test.py
and friends. The one relay URL these tests DO use points at a CLOSED
loopback port (nothing listens there), so even `SyncService.start()` --
which this file lets run for REAL, as a counting spy, to prove `.start()`
itself does not raise -- can never reach a live network; its 10s timer is
always cancelled in teardown before it could ever fire.

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest products/retail/tests/retail_sync_hot_start_after_activation_test.py -v
"""
import logging
import os
import shutil
import sys
import tempfile
import threading
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_sync_hotstart_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
# THE EXACT DEFECT SCENARIO: a fresh till, no operator-set relay URL at
# boot at all. Every test below activates LATER, in this same running
# process, which is the one thing that used to never start anything.
os.environ.pop("AURA_SYNC_RELAY_URL", None)
# Kill switch -- see the module docstring's note on why this file never lets
# a real LAN listener come up.
os.environ["AURA_SITE_RELAY_ENABLED"] = "0"

import app as _app_module  # noqa: E402
# The SAME `config` module object app.py imported its names from (backend dir
# is on sys.path above) -- T10 below patches one of its module globals.
import config as _config_module  # noqa: E402
from commercial_runtime.licensing_contracts.state_machine import LicenseState  # noqa: E402
from commercial_runtime.licensing_contracts.state_repository import (  # noqa: E402
    LicenseStateRecord, LicenseStateRepository)
from commercial_runtime.sync import sync_service as _sync_service_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

# The REAL site-relay starter, captured before the autouse fixture below
# replaces it with a counting stub -- T14 needs the real guard to race.
_REAL_START_SITE_RELAY_IF_ENABLED = _app_module._start_site_relay_if_enabled

_LICENSING_DB_PATH = Path(_app_module.DATABASE_DIR) / 'subsystems' / 'licensing.db'

# Nothing listens here -- a closed loopback port. If anything under test
# ever actually attempted a push/pull it would fail fast (connection
# refused) rather than hang or touch a real network. Never actually
# reached: SyncService's own 10s timer is cancelled in teardown before it
# could fire, and _build_sync_client() is never called by anything in this
# file.
_CLOSED_LOOPBACK_URL = "http://127.0.0.1:1"


def _stop_services():
    if _app_module._sync_service is not None:
        try:
            _app_module._sync_service.stop()
        except Exception:
            pass
    if _app_module._registry_sync_service is not None:
        try:
            _app_module._registry_sync_service.stop()
        except Exception:
            pass


def teardown_module(module):
    _stop_services()
    shutil.rmtree(DATA, ignore_errors=True)


def _persist_activation(sync_relay_base_url):
    """Writes a LicenseStateRecord at licensing.db's real, production path
    -- shaped exactly like activation.py's ingest_activation_response()
    would leave it after a genuine ACTIVE_ONLINE activation: an
    owner_installation_id (what _build_sync_client() actually reads back)
    and the one field this whole fix hot-starts on, sync_relay_base_url."""
    repo = LicenseStateRepository(_LICENSING_DB_PATH)
    repo.save(LicenseStateRecord(
        licensing_schema_version=2,
        product_code='AURA_RETAIL',
        platform='WINDOWS',
        current_state=LicenseState.ACTIVE_ONLINE.value,
        owner_installation_id=str(uuid.uuid4()),
        sync_relay_base_url=sync_relay_base_url,
    ))


@pytest.fixture(autouse=True)
def hot_start(monkeypatch):
    """Runs before AND after every test below.

    BEFORE: resets `_sync_service`/`_registry_sync_service`/the nudge()
    registration to "nothing built" -- these are process-wide module
    globals this one file's tests all share (see run_all_tests.py's own
    docstring on why that sharing is safe within one file and dangerous
    across files), and every scenario here needs to start from a known
    state regardless of execution order. Also defaults `_detect_split_
    state_reason` to a stub returning `None`: a fresh, never-multi-tenant
    install genuinely has no split state, and driving the real rebind
    machinery through registry.db/retail.db for every scenario in this file
    would re-test THAT machinery (already covered by retail_registry_v4_
    activation_split_state_test.py) rather than this file's own subject.
    T6 below overrides this default explicitly.

    SyncService.start is wrapped as a counting SPY -- it calls the REAL
    implementation, it is not replaced -- because the defect this file
    proves closed is specifically that `.start()` gets CALLED, not merely
    that app.py intends to call it. The real implementation schedules a
    genuine `threading.Timer` at its ~10s default interval; `_stop_
    services()` (module teardown, and this fixture's own teardown) cancels
    every such timer before it could ever fire during a test run.

    `_start_site_relay_if_enabled` is replaced outright with a bare
    counting stub -- there is nothing "real" to preserve for this file's
    purposes (its own behaviour has a dedicated, mutation-proved test file:
    retail_site_relay_election_wiring_test.py), and replacing it outright
    is what keeps this file from ever binding a real LAN socket regardless
    of AURA_SITE_RELAY_ENABLED.

    Returns (start_calls, site_relay_calls): two plain dicts with a
    'count' key each, so a test can read/reset them.
    """
    monkeypatch.setattr(_app_module, '_sync_service', None)
    monkeypatch.setattr(_app_module, '_registry_sync_service', None)
    _sync_service_module.unregister_active_service()
    monkeypatch.setattr(_app_module, '_detect_split_state_reason', lambda: None)

    start_calls = {'count': 0}
    real_start = _sync_service_module.SyncService.start

    def _counting_start(self, *args, **kwargs):
        start_calls['count'] += 1
        return real_start(self, *args, **kwargs)

    monkeypatch.setattr(_sync_service_module.SyncService, 'start', _counting_start)

    site_relay_calls = {'count': 0}

    def _counting_site_relay():
        site_relay_calls['count'] += 1

    monkeypatch.setattr(_app_module, '_start_site_relay_if_enabled', _counting_site_relay)

    yield start_calls, site_relay_calls

    _stop_services()
    _app_module._sync_service = None
    _app_module._registry_sync_service = None
    _sync_service_module.unregister_active_service()


# ─────────────────────────────────────────────────────────────────────────
# T1 -- the headline scenario: a fresh till activates, mid-session, with no
# restart, and both services come up and are actually started.
# ─────────────────────────────────────────────────────────────────────────

def test_t1_fresh_activation_hot_starts_sync_and_site_relay(hot_start):
    start_calls, site_relay_calls = hot_start
    assert _app_module._sync_service is None  # precondition: fresh till, nothing built yet

    _persist_activation(_CLOSED_LOOPBACK_URL)  # what a real activation just wrote to licensing.db

    _app_module._on_licence_activated()  # the REAL on_activation_success hook

    assert _app_module._sync_service is not None, (
        'a fresh activation with a usable relay URL must build the retail SyncService '
        'in-process, with no restart')
    assert _app_module._registry_sync_service is not None, (
        'the registry-stream SyncService must be built alongside the retail one, exactly '
        'as the import-time construction always built both together')
    assert _app_module._registry_sync_service is not _app_module._sync_service, (
        'these must be two genuinely separate SyncService instances, mirroring the '
        'import-time construction')
    assert _sync_service_module._active_service is _app_module._sync_service, (
        'the RETAIL instance, and only it, must be registered for nudge() -- exactly '
        'like the import-time construction')
    assert start_calls['count'] == 2, (
        f'expected .start() called exactly once per service (2 total), got {start_calls["count"]}')
    assert site_relay_calls['count'] == 1, (
        f'expected _start_site_relay_if_enabled() called exactly once, got {site_relay_calls["count"]}')


# ─────────────────────────────────────────────────────────────────────────
# T2 -- Owner's activation response carried no relay URL at all (a deploy
# that never configured OWNER_SYNC_RELAY_PUBLIC_URL). Must not raise, must
# not build anything, and the site relay decision must still run.
# ─────────────────────────────────────────────────────────────────────────

def test_t2_activation_with_no_relay_url_leaves_sync_off_without_raising(hot_start):
    start_calls, site_relay_calls = hot_start
    _persist_activation(None)

    _app_module._on_licence_activated()  # must not raise

    assert _app_module._sync_service is None
    assert _app_module._registry_sync_service is None
    assert start_calls['count'] == 0
    assert site_relay_calls['count'] == 1, (
        'the site-relay decision must still run even when sync itself stays off -- a '
        'shop with no cloud relay configured can still be a LAN-only hub')


# ─────────────────────────────────────────────────────────────────────────
# T3 -- a relay URL that FAILS validate_sync_relay_url() must never be
# built from, regardless of where it came from.
#
# WHY THIS IS DONE VIA A MONKEYPATCHED resolve_sync_relay_base_url_now()
# RATHER THAN A REAL PERSISTED INVALID VALUE: config.py's own precedence
# rule (_resolve_effective_sync_relay_base_url) deliberately DISCARDS an
# invalid PERSISTED value before validation ever sees it -- "it was never
# something an operator typed, so there is nothing for them to go fix" (see
# that function's own docstring). Persisting an invalid URL and calling the
# real resolver therefore always comes back as ('', []), which is
# indistinguishable from "nothing configured" (T2) and would never
# exercise _start_sync_after_activation's OWN "problems present" branch at
# all. The only way an invalid URL ever reaches that branch with a
# non-empty `problems` list is an operator's own AURA_SYNC_RELAY_URL --
# which cannot be changed mid-process in this shared-boot test file (env
# vars are read once, at import, by config.py) any more than a real
# operator's env var changes without a restart. Monkeypatching the
# resolver itself is therefore the direct, honest way to unit-test this
# one seam's own reaction to "usable=False because of validation problems",
# decoupled from the (intentionally silent) persisted-value case T2 already
# covers.
# ─────────────────────────────────────────────────────────────────────────

def test_t3_a_relay_url_that_fails_validation_is_never_built_from(hot_start, monkeypatch):
    start_calls, site_relay_calls = hot_start
    _persist_activation(_CLOSED_LOOPBACK_URL)  # present but must never be read, given the patch below

    invalid_url = 'http://192.168.1.50:5551'
    problems = [f"AURA_SYNC_RELAY_URL uses cleartext http:// against non-loopback host '192.168.1.50'."]
    monkeypatch.setattr(_app_module, 'resolve_sync_relay_base_url_now', lambda: (invalid_url, problems))

    _app_module._on_licence_activated()  # must not raise

    assert _app_module._sync_service is None
    assert _app_module._registry_sync_service is None
    assert start_calls['count'] == 0
    assert site_relay_calls['count'] == 1


# ─────────────────────────────────────────────────────────────────────────
# T4 -- calling the hook twice (a retried activation POST, a second tab)
# must not build a second pair or double-start anything.
# ─────────────────────────────────────────────────────────────────────────

def test_t4_hook_called_twice_builds_and_starts_only_once(hot_start):
    start_calls, site_relay_calls = hot_start
    _persist_activation(_CLOSED_LOOPBACK_URL)

    _app_module._on_licence_activated()
    first_sync_service = _app_module._sync_service
    first_registry_sync_service = _app_module._registry_sync_service
    assert first_sync_service is not None

    _app_module._on_licence_activated()  # second call -- must be a no-op for construction/start

    assert _app_module._sync_service is first_sync_service, (
        'a second activation-hook call must not replace the already-built retail SyncService')
    assert _app_module._registry_sync_service is first_registry_sync_service, (
        'a second activation-hook call must not replace the already-built registry SyncService')
    assert start_calls['count'] == 2, (
        f'.start() must still have been called exactly twice in total (once per service), '
        f'got {start_calls["count"]}')
    assert site_relay_calls['count'] == 2, (
        'the site-relay decision itself is safe to call again (it has its own guard) and '
        'is expected to run once per hook call')


# ─────────────────────────────────────────────────────────────────────────
# T5 -- Android never builds a Python-side SyncService; Kotlin's own
# SyncCoordinator drives Android's push/pull loop instead.
# ─────────────────────────────────────────────────────────────────────────

def test_t5_android_platform_never_builds_or_starts(hot_start, monkeypatch):
    start_calls, site_relay_calls = hot_start
    _persist_activation(_CLOSED_LOOPBACK_URL)  # a perfectly usable URL -- must still be ignored
    monkeypatch.setattr(_app_module, 'LICENSING_PLATFORM', 'ANDROID')

    _app_module._on_licence_activated()

    assert _app_module._sync_service is None
    assert _app_module._registry_sync_service is None
    assert start_calls['count'] == 0
    assert site_relay_calls['count'] == 0, (
        'the ANDROID check must return before the site-relay call too -- Android has no '
        'Windows-style hub election at all')


# ─────────────────────────────────────────────────────────────────────────
# T6 -- a till mid-tenant-identity-upgrade (split-state refusal) must not
# start background sync against a database it is not yet safely serving
# from.
# ─────────────────────────────────────────────────────────────────────────

def test_t6_split_state_reason_present_never_builds_or_starts(hot_start, monkeypatch):
    start_calls, site_relay_calls = hot_start
    _persist_activation(_CLOSED_LOOPBACK_URL)
    monkeypatch.setattr(_app_module, '_detect_split_state_reason', lambda: 'stuck: fabricated for this test')

    _app_module._on_licence_activated()

    assert _app_module._split_state_refusal['reason'] is not None, (
        'sanity check: the split-state refusal itself must actually have engaged, or this '
        'test would prove nothing')
    assert _app_module._sync_service is None
    assert _app_module._registry_sync_service is None
    assert start_calls['count'] == 0
    assert site_relay_calls['count'] == 0

    # Clean up the module-level refusal dict so it cannot bleed into a later test in this
    # same shared-process file.
    _app_module._clear_split_state_refusal()


# ─────────────────────────────────────────────────────────────────────────
# T7 -- a relay URL already usable BEFORE this hook ever runs (in
# production: AURA_SYNC_RELAY_URL set at boot, so the pair was already
# built at import time) must not be rebuilt -- but the site relay must
# still be re-decided, since `init_app()`'s own call to it ran before any
# licence existed.
#
# Simulated here by building the pair directly through the SAME production
# function the import-time call site uses (_build_windows_sync_services),
# then marking it "already started" -- rather than a real env var, which
# cannot be injected mid-process in this shared-boot file (see T3's own
# docstring for the identical constraint). This exercises the exact same
# "already built" precondition a real env-var-at-boot install would present
# to this hook, without needing a second process.
# ─────────────────────────────────────────────────────────────────────────

def test_t7_already_built_pair_is_not_rebuilt_but_site_relay_still_runs(hot_start):
    start_calls, site_relay_calls = hot_start

    sync_service, registry_sync_service = _app_module._build_windows_sync_services(_CLOSED_LOOPBACK_URL)
    _app_module._sync_service = sync_service
    _app_module._registry_sync_service = registry_sync_service
    _sync_service_module.register_active_service(sync_service)
    sync_service.start()
    registry_sync_service.start()
    assert start_calls['count'] == 2  # the "already started at import" baseline
    start_calls['count'] = 0  # reset -- the hook itself must add nothing more

    _persist_activation(_CLOSED_LOOPBACK_URL)  # activation still happens; e.g. a re-activation
    _app_module._on_licence_activated()

    assert _app_module._sync_service is sync_service, 'must not rebuild an already-usable pair'
    assert _app_module._registry_sync_service is registry_sync_service
    assert start_calls['count'] == 0, '.start() must not be called again for an already-running pair'
    assert site_relay_calls['count'] == 1, (
        'the site-relay decision must still run -- it is what actually starts the hub '
        'election the first time a till with an already-configured relay URL activates')


# ─────────────────────────────────────────────────────────────────────────
# T8 -- before init_app() has run, nothing must start (init_app() starts
# everything itself, in the right order, once it does run).
# ─────────────────────────────────────────────────────────────────────────

def test_t8_before_init_app_has_run_nothing_starts(hot_start, monkeypatch):
    start_calls, site_relay_calls = hot_start
    _persist_activation(_CLOSED_LOOPBACK_URL)
    monkeypatch.setattr(_app_module, '_INIT_APP_HAS_RUN', False)

    _app_module._on_licence_activated()

    assert _app_module._sync_service is None
    assert _app_module._registry_sync_service is None
    assert start_calls['count'] == 0
    assert site_relay_calls['count'] == 0


# -------------------------------------------------------------------------
# T9-T13 were added by the ADVERSARIAL VERIFIER pass (2026-09-24), each one
# because a mutation of the fix went GREEN against T1-T8 above. The
# mutation each one exists to catch is named in its own comment; none of
# T1-T8 was changed.
# -------------------------------------------------------------------------

# T9 -- the hot-started services must actually TALK TO the relay URL that
# was resolved fresh at activation, signed as the installation activation
# just wrote. T1 proves the pair is built, registered and started; it never
# asked where the pair would push. Mutation that went GREEN on T1-T8 (and on
# retail_sync_starts_when_configured_test.py, whose boot-time URL equals the
# frozen constant by construction): `_build_sync_client` passing the frozen
# module constant `SYNC_RELAY_BASE_URL` instead of its `relay_base_url`
# parameter -- which on a fresh till is '', so every push and pull of a
# "started" sync would fail forever. That is the exact bug
# `_build_windows_sync_services`'s own docstring says its parameter exists
# to prevent, and nothing pinned it. Read through each SERVICE's own
# `_client_factory` (what every real tick calls), not the module attribute.

def test_t9_hot_started_clients_use_the_fresh_relay_url_and_installation_id(hot_start):
    _persist_activation(_CLOSED_LOOPBACK_URL)
    installation_id = LicenseStateRepository(_LICENSING_DB_PATH).load().owner_installation_id
    assert installation_id  # fixture sanity: activation wrote one

    _app_module._on_licence_activated()

    for label, service in (('retail', _app_module._sync_service),
                           ('registry', _app_module._registry_sync_service)):
        assert service is not None, f'{label} SyncService was not built at all'
        client = service._client_factory()
        assert client.base_url == _CLOSED_LOOPBACK_URL, (
            f'{label} service would push/pull to {client.base_url!r}, not the relay URL this '
            f'activation just persisted ({_CLOSED_LOOPBACK_URL!r})')
        assert client._installation_id == installation_id, (
            f'{label} service would sign as {client._installation_id!r}, not the installation '
            f'this activation just wrote ({installation_id!r})')


# T10 -- T3's branch, driven through the REAL resolver and validator rather
# than a monkeypatched resolver. T3's docstring says an invalid operator env
# var "cannot be changed mid-process in this shared-boot test file"; that is
# true of os.environ, but config.py copies it into the module global
# `_AURA_SYNC_RELAY_URL_ENV`, which `resolve_sync_relay_base_url_now()` reads
# at CALL time -- so patching that global is exactly what a boot with that
# env var would have produced. Mutation that went GREEN on T1-T8:
# `resolve_sync_relay_base_url_now()` returning `problems = []` (skipping
# validation) -- T3 never runs the real resolver, so a cleartext
# http://<LAN host> operator URL would have been built from and would ship
# device-signed business data in the clear. Also asserts the ERROR line the
# problems branch logs, so this proves THAT branch ran (not merely "nothing
# was built", which the no-URL branch would also produce).

def test_t10_invalid_operator_env_url_is_refused_through_the_real_resolver(hot_start, monkeypatch, caplog):
    start_calls, site_relay_calls = hot_start
    _persist_activation(_CLOSED_LOOPBACK_URL)  # a VALID persisted URL -- the env var must still win
    monkeypatch.setattr(_config_module, '_AURA_SYNC_RELAY_URL_ENV', 'http://192.168.1.50:5551')
    caplog.set_level(logging.INFO, logger='app')

    _app_module._on_licence_activated()

    assert _app_module._sync_service is None
    assert _app_module._registry_sync_service is None
    assert start_calls['count'] == 0
    assert site_relay_calls['count'] == 1
    refused = [r for r in caplog.records
               if r.levelno == logging.ERROR and 'Multi-device sync stays OFF after activation' in r.getMessage()]
    assert refused, 'the validation-problems branch did not run (no ERROR line logged)'
    assert any('192.168.1.50' in r.getMessage() for r in refused)


# T11 -- the other half of the precedence rule, also through the real
# resolver: an invalid PERSISTED (Owner-supplied) value is discarded
# silently -- never built from, never reported as an operator problem (see
# `_resolve_effective_sync_relay_base_url`'s docstring). Pins that the
# INFO "no usable relay URL" branch is the one that ran, so a resolver that
# stopped discarding it (and pushed it into `problems` instead) goes RED.

def test_t11_invalid_persisted_url_is_discarded_not_reported(hot_start, caplog):
    start_calls, site_relay_calls = hot_start
    _persist_activation('http://192.168.1.50:5551')
    caplog.set_level(logging.INFO, logger='app')

    _app_module._on_licence_activated()

    assert _app_module._sync_service is None
    assert start_calls['count'] == 0
    assert site_relay_calls['count'] == 1
    messages = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == 'app']
    assert any(level == logging.INFO and 'no usable relay URL' in msg for level, msg in messages), messages
    assert not any(level >= logging.ERROR and 'Multi-device sync' in msg for level, msg in messages), messages


# T12 -- the lock. `_start_sync_after_activation_lock`'s own comment claims
# it stops two near-simultaneous activation hooks (a retried POST, a second
# tab) from both passing the `_sync_service is None` check and building two
# pairs. Mutation that went GREEN on T1-T8: replacing
# `with _start_sync_after_activation_lock:` by `if True:`. Deterministic, not
# a timing race: the FIRST builder holds its window open until a SECOND
# builder arrives (or 2s pass). Without the lock the second arrives at once
# and there are two builds; with it the second is blocked on the lock, the
# first times out and assigns, and the second then sees a built pair.

def test_t12_concurrent_activation_hooks_build_exactly_one_pair(hot_start, monkeypatch):
    start_calls, site_relay_calls = hot_start
    _persist_activation(_CLOSED_LOOPBACK_URL)

    real_build = _app_module._build_windows_sync_services
    build_calls = {'count': 0}
    counter_lock = threading.Lock()
    second_builder_arrived = threading.Event()

    def _window_holding_build(relay_base_url):
        with counter_lock:
            build_calls['count'] += 1
            nth = build_calls['count']
        if nth == 1:
            second_builder_arrived.wait(timeout=2.0)
        else:
            second_builder_arrived.set()
        return real_build(relay_base_url)

    monkeypatch.setattr(_app_module, '_build_windows_sync_services', _window_holding_build)
    barrier = threading.Barrier(2)

    def _hook():
        barrier.wait()
        _app_module._on_licence_activated()

    threads = [threading.Thread(target=_hook) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), 'a hook thread hung'

    assert build_calls['count'] == 1, (
        f'two concurrent activation hooks built {build_calls["count"]} sync pairs; exactly one allowed')
    assert start_calls['count'] == 2, f'expected one .start() per service (2), got {start_calls["count"]}'
    assert site_relay_calls['count'] == 2


# T13 -- the split-state DEFERRAL must not become a permanent "restart
# required". T6 proves sync does not start while the activation-time
# split-state refusal is engaged. But that refusal clears itself on a LATER
# request (`_refuse_while_split_state`, once `_detect_split_state_reason()`
# comes back None -- app.py names a locked retail.db under a concurrent
# sale as the ordinary cause), and before the verifier's fix nothing started
# sync or the hub election at that moment: the till served normally again,
# with sync and the LAN hub off until the next restart, and nothing logged.
# Both directions: a request while STILL stuck serves the 503 and starts
# nothing (deny half); the first request that finds it converged starts
# both services and the hub decision exactly once (allow half).

def test_t13_split_state_that_clears_on_a_later_request_then_hot_starts(hot_start, monkeypatch):
    start_calls, site_relay_calls = hot_start
    _persist_activation(_CLOSED_LOOPBACK_URL)
    stuck = {'reason': 'stuck: fabricated for this test'}
    monkeypatch.setattr(_app_module, '_detect_split_state_reason', lambda: stuck['reason'])
    try:
        _app_module._on_licence_activated()
        assert _app_module._split_state_refusal['reason'] is not None  # sanity: refusal engaged

        client = app.test_client()
        still_stuck = client.get('/api/licensing/status')
        assert still_stuck.status_code == 503, 'sanity: the refusal must still be serving 503'
        assert _app_module._sync_service is None, 'sync started while the till was still refusing'
        assert start_calls['count'] == 0
        assert site_relay_calls['count'] == 0

        stuck['reason'] = None  # retail.db converged; the NEXT request clears the refusal
        converged = client.get('/api/licensing/status')
        assert converged.status_code == 200
        assert _app_module._split_state_refusal['reason'] is None

        assert _app_module._sync_service is not None, (
            'the split-state refusal cleared but sync was never started -- restart required again')
        assert _app_module._registry_sync_service is not None
        assert start_calls['count'] == 2
        assert site_relay_calls['count'] == 1

        again = client.get('/api/licensing/status')  # normal service: nothing further starts
        assert again.status_code == 200
        assert start_calls['count'] == 2
        assert site_relay_calls['count'] == 1
    finally:
        _app_module._split_state_refusal['reason'] = None


# T14 -- the HUB half of the same race T12 covers for sync. Before the
# hot-start fix `_start_site_relay_if_enabled()` ran only from `init_app()`,
# single-threaded, before any request was served, so its check-then-set guard
# (`_site_relay_coordinator is not None` -> return ... later assign) never had
# a second caller to race. The hot-start now calls it from REQUEST threads:
# two near-simultaneous activation hooks (T12's retried POST / second tab),
# or -- via T13's resume path -- the burst of concurrent fetches a page load
# fires the moment a split-state refusal clears. Two callers that both pass
# that guard build TWO election coordinators on one till, which app.py's own
# `_site_relay_coordinator` comment names as "the two-hub bug this whole
# feature exists to remove, reproduced inside a single process".
#
# Drives the REAL `_start_site_relay_if_enabled` in AUTOMATIC mode (the
# kill switch this file sets is overridden for this one test) with ONLY the
# coordinator class faked, so no UDP socket is ever opened. Same
# window-holding technique as T12: the first coordinator constructor waits
# for a second one (or 2s). Mutation this catches: the site-relay call placed
# OUTSIDE `_start_sync_after_activation_lock` (where the builder had it).

def test_t14_concurrent_activation_hooks_construct_exactly_one_hub_coordinator(hot_start, monkeypatch):
    from commercial_runtime.sync.site_relay import coordinator as _coordinator_module

    _persist_activation(_CLOSED_LOOPBACK_URL)
    installation_id = LicenseStateRepository(_LICENSING_DB_PATH).load().owner_installation_id

    constructed = []
    counter_lock = threading.Lock()
    second_constructor_arrived = threading.Event()

    class _WindowHoldingCoordinator:
        def __init__(self, **kwargs):
            with counter_lock:
                constructed.append(kwargs)
                nth = len(constructed)
            if nth == 1:
                second_constructor_arrived.wait(timeout=2.0)
            else:
                second_constructor_arrived.set()

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(_coordinator_module, 'SiteRelayCoordinator', _WindowHoldingCoordinator)
    monkeypatch.setattr(_app_module, '_start_site_relay_if_enabled', _REAL_START_SITE_RELAY_IF_ENABLED)
    monkeypatch.setattr(_app_module, 'SITE_RELAY_ENABLE_OVERRIDE', '')  # automatic mode
    monkeypatch.setattr(_app_module, 'LICENSING_PLATFORM', 'WINDOWS')
    monkeypatch.setattr(_app_module, '_site_relay_server', None)
    monkeypatch.setattr(_app_module, '_site_relay_coordinator', None)

    barrier = threading.Barrier(2)

    def _hook():
        barrier.wait()
        _app_module._on_licence_activated()

    threads = [threading.Thread(target=_hook) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), 'a hook thread hung'

    assert len(constructed) == 1, (
        f'two concurrent activation hooks constructed {len(constructed)} hub-election coordinators '
        f'on one till; exactly one allowed')
    assert constructed[0].get('my_installation_id') == installation_id, (
        'the coordinator must run the election as the installation this activation just wrote')
    assert _app_module._site_relay_coordinator is not None
