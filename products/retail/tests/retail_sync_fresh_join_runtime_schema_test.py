"""Aura Retail -- retail-hardware-viewports: a freshly-joined device's very
first sync pull must never depend on a UI route having run first.

THE DEFECT, measured 2026-09-25 on a real fresh till that joined an existing
shop through Owner: every sync pull failed with
`sqlite3.OperationalError: no such table: retail_settings` at
commercial_runtime/sync/sync_service.py's `_apply_event` (the
`entity_type == "retail_setting"` branch, ~line 1838), reached via
`apply_pull_result -> pull_once`. The shop had changed one setting (base
currency JOD), so the pulled batch carried a `retail_setting` event next to
6 `product` events; the whole batch is applied inside ONE transaction, so
that single missing-table error rolled the WHOLE batch back, the cursor
never advanced, and the new device received NO products either
(`sync_cursor.last_seq` stayed 0, "consecutive failures=5"). `retail_
settings` (like `payment_methods`/`doc_sequences` beside it, and most of
`payments`' AR/AP columns) is created only LAZILY, by
`api/retail_api.py::_ensure_credit_schema(conn)`, the first time an HTTP
route that calls it happens to run. Opening a settings route on that same
till created the table, and the NEXT pull then applied everything
correctly -- so the till self-healed, but only because some UI screen
happened to hit that route before the sync timer's next tick. A headless
till, or a phone that never opens a settings screen before its first
background sync fires, stays wedged forever: the sync loop's timer runs on
its own schedule, completely independent of any route ever being hit.

THE FIX under test here: `app.py::_ensure_runtime_schema_for_sync()`, a
boot-time seam that runs the SAME `_ensure_credit_schema(conn)` migration
unconditionally, BEFORE any SyncService or hub self-sync can start, from
all three places sync can come up (`init_app()`, `_start_sync_after_
activation()`, `_enter_hub_self_sync()`) -- see that function's own
docstring in app.py for the full reasoning, including why the existing
per-batch `local_ensure_schema` hook (sync_service.py, Launch-readiness
Phase 5) did not already close this: it only fires for a batch touching
sale/return/payment, and `retail_setting` was never in that tuple.

STRUCTURE:
  GROUP 1 (test_g1_*) -- functional correctness, reproducing the EXACT
    measured defect against a brand-new device's database (schema.py
    migrations only, via `database.schema.init_retail()`, NO retail_api
    route ever called -- the same `install_b`-style bare-database technique
    retail_money_sync_test.py already uses for its own second device, minus
    that fixture's own `_ensure_credit_schema(_get_conn())` pre-call, which
    exists there specifically to paper over this exact gap). A fake
    in-memory relay feeds `pull_once()` a batch of one `retail_setting`
    update (skey=base_currency, svalue=JOD) + one `product` create + its
    `inventory_movement` create, in that order, exactly as Owner would
    deliver them:
      - test_g1_red_without_the_seam_reproduces_the_measured_defect: RED,
        quoting the exact exception the field defect report quotes.
      - test_g1_green_with_the_seam_the_batch_applies_cleanly: GREEN, once
        `_ensure_runtime_schema_for_sync()` has run against this same
        database (pointed at it the same way app.py's real seam points at
        whichever device it is running on) -- no exception, the cursor
        advanced, the product row exists, retail_settings holds
        base_currency=JOD.
  GROUP 2 (test_g2_*) -- THE CHECK RAN, not just the outcome: counts that
    `_ensure_runtime_schema_for_sync` is actually invoked, once, from each
    of `_start_sync_after_activation()` and `_enter_hub_self_sync()` (a
    counting-spy monkeypatch around the real function, same technique
    retail_sync_hot_start_after_activation_test.py already uses for
    `SyncService.start`/`_start_site_relay_if_enabled`), plus evidence that
    `init_app()`'s own call already ran, at module import, before this file
    ever touched a route.
  GROUP 3 (test_g3_*) -- the REAL boot, in a fresh interpreter each: boot
    app.py against a brand-new AURA_APP_DATA, call NO route, then (a) pull
    the same batch into THAT device's own retail.db, and (b) force the
    ensure to raise and require init_app() to finish anyway and write the
    error to backend.log. Added by the adversarial-verifier pass because
    a mutant that bypassed the never-raising seam in init_app() (M6, see
    that group's header) passed every GROUP 1-2 test.
  MUTATION PROOFS M1-M4 are NOT pytest functions in this file -- they
  mutate app.py's SOURCE (removing/no-opping/breaking the seam or one of
  its three call sites) and re-run the relevant tests above in a FRESH
  subprocess each time (this file's own module-level `app = _app_module.
  init_app()` makes `init_app()` single-shot per process -- see
  `create_app()`'s own docstring -- so a mutation of THAT call site cannot
  be observed by re-invoking anything in an already-booted process; it
  needs a fresh interpreter). See the sibling report for each mutation's
  RED output and the sha256 restore proof.

Own subprocess/module -- config.py/app.py resolve environment variables and
module-level globals once at import time, so this file must be the only one
in its pytest process asserting on this particular combination (same
reasoning as every neighbouring sync test file's own docstring, and the
reason products/run_all_tests.py runs one file per pytest process).

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest products/retail/tests/retail_sync_fresh_join_runtime_schema_test.py -v
"""
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_freshjoin_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SYNC_RELAY_URL", None)
# Kill switch -- this file never binds a real LAN-facing socket, matching
# retail_sync_hot_start_after_activation_test.py's own module docstring note.
os.environ["AURA_SITE_RELAY_ENABLED"] = "0"

import app as _app_module  # noqa: E402
import api.retail_api as _retail_api_module  # noqa: E402
import database.schema as _schema_module  # noqa: E402
from commercial_runtime.licensing_contracts.state_machine import LicenseState  # noqa: E402
from commercial_runtime.licensing_contracts.state_repository import (  # noqa: E402
    LicenseStateRecord, LicenseStateRepository)
from commercial_runtime.sync import sync_service as _sync_service_module  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402

# THIS is the process-wide proof that `init_app()`'s own call to the seam
# ran -- see this file's module docstring, GROUP 2. Nothing above this line
# has touched a route, a SyncService, or a hub; this is genuinely the FIRST
# thing that happens in this process, exactly the shape a headless till's
# own boot is.
app = _app_module.init_app()
app.config["TESTING"] = True

_LICENSING_DB_PATH = Path(_app_module.DATABASE_DIR) / 'subsystems' / 'licensing.db'

_CLOSED_LOOPBACK_URL = "http://127.0.0.1:1"  # nothing listens here -- see the hot-start test file's identical constant


def teardown_module(module):
    _stop_services()
    shutil.rmtree(DATA, ignore_errors=True)


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


def _persist_activation(sync_relay_base_url):
    """Identical technique to retail_sync_hot_start_after_activation_test.py's
    own helper -- see that file's docstring for the full reasoning."""
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
def _reset_module_globals(monkeypatch):
    """Runs before AND after every test below -- resets the process-wide
    sync-service globals to "nothing built", identical reasoning to
    retail_sync_hot_start_after_activation_test.py's own `hot_start`
    fixture (this file shares its process-global-state hazards, being the
    same shape of test against the same module)."""
    monkeypatch.setattr(_app_module, '_sync_service', None)
    monkeypatch.setattr(_app_module, '_registry_sync_service', None)
    _sync_service_module.unregister_active_service()
    monkeypatch.setattr(_app_module, '_detect_split_state_reason', lambda: None)
    monkeypatch.setattr(_app_module, '_start_site_relay_if_enabled', lambda: None)
    yield
    _stop_services()
    _app_module._sync_service = None
    _app_module._registry_sync_service = None
    _sync_service_module.unregister_active_service()


class InMemoryRelay:
    """Identical shape to retail_money_sync_test.py's own double -- see that
    file's module docstring for the full reasoning. A fake relay client,
    never the network."""

    def __init__(self):
        self.events = []
        self._next_seq = 1

    def push(self, events):
        for ev in events:
            self.events.append(dict(ev, seq=self._next_seq))
            self._next_seq += 1
        return {"stored": len(events), "received": len(events)}

    def pull(self, since):
        pending = [ev for ev in self.events if ev["seq"] > since]
        cursor = pending[-1]["seq"] if pending else since
        return {"events": pending, "cursor": cursor}


@pytest.fixture
def relay():
    return InMemoryRelay()


@pytest.fixture
def fresh_device(tmp_path):
    """A brand-new device's own retail.db, built EXACTLY the way a freshly
    joined device has it: `database.schema.init_retail()` (schema.py's
    versioned migrations) and NOTHING ELSE -- no retail_api route is ever
    called against it, so its lazily-created tables (`retail_settings`,
    `payment_methods`, `doc_sequences`) and lazily-added columns do not
    exist yet. This is the SAME technique retail_money_sync_test.py's own
    `install_b` fixture uses (temporarily repointing `schema.BASE_DIR`/
    `SUBSYS_DIR`, calling `schema.init_retail()`, then restoring them) --
    deliberately WITHOUT that fixture's own extra `_ensure_credit_schema(
    _get_conn())` call, because reproducing the exact gap that call exists
    to paper over is this test file's whole purpose.

    Returns a `_get_conn` callable (matching `SyncService`'s own `get_conn`
    contract), bound to this device's own temp database file."""
    root = tmp_path / "fresh_device"
    subsys = root / "subsystems"
    subsys.mkdir(parents=True, exist_ok=True)

    original_base_dir, original_subsys_dir = _schema_module.BASE_DIR, _schema_module.SUBSYS_DIR
    _schema_module.BASE_DIR = str(root)
    _schema_module.SUBSYS_DIR = str(subsys)
    try:
        _schema_module.init_retail()
    finally:
        _schema_module.BASE_DIR, _schema_module.SUBSYS_DIR = original_base_dir, original_subsys_dir

    db_path = subsys / "retail.db"

    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    return _get_conn


_FRESH_JOIN_PRODUCT_ID = 501
_BASE_CURRENCY_SKEY_UUID_NS = uuid.uuid5(uuid.NAMESPACE_URL, "aura-retail-settings-sync")


def _fresh_join_batch():
    """The exact batch shape the field defect describes: one `retail_setting`
    update (the shop's base-currency change) next to catalogue events --
    here one `product` create and its `inventory_movement` create, in that
    order, exactly as Owner would deliver them (a settings change alongside
    the shop's existing catalogue, which is what a device joining an
    EXISTING shop receives on its very first pull)."""
    now = datetime.now(timezone.utc).isoformat()
    movement_uid = str(uuid.uuid4())
    return [
        {
            "entity_type": "retail_setting",
            "entity_id": str(uuid.uuid5(_BASE_CURRENCY_SKEY_UUID_NS, "retail_setting:base_currency")),
            "event_type": "update",
            "payload": {"skey": "base_currency", "svalue": "JOD"},
        },
        {
            "entity_type": "product",
            "entity_id": str(_FRESH_JOIN_PRODUCT_ID),
            "event_type": "create",
            "payload": {
                "id": _FRESH_JOIN_PRODUCT_ID, "sku": "FRESH-JOIN-001", "name": "Fresh Join Widget",
                "cost_price": 5.0, "sell_price": 10.0, "tax_rate": 0, "unit": "pcs",
            },
        },
        {
            "entity_type": "inventory_movement",
            "entity_id": str(uuid.uuid4()),
            "event_type": "create",
            "payload": {
                "product_id": _FRESH_JOIN_PRODUCT_ID, "branch_uid": None, "movement_type": "purchase_in",
                "quantity": 10, "unit_cost": 5.0, "reference": "opening stock", "notes": None,
                "created_by": "System", "uid": movement_uid, "actor_user_uid": None,
                "terminal_id": None, "created_at_utc": now,
            },
        },
    ]


# ─────────────────────────────────────────────────────────────────────────
# GROUP 1 -- functional correctness against a genuinely bare device database
# ─────────────────────────────────────────────────────────────────────────

def test_g1_anti_vacuity_fresh_device_really_has_no_retail_settings_table(fresh_device):
    """Sanity check for every test below: if `fresh_device` accidentally
    built a database that already has `retail_settings` (e.g. because some
    earlier test in this SAME process already flipped `_CREDIT_SCHEMA_READY`
    True and a later call silently no-opped against a database that already
    had it from a previous run), the RED test below would prove nothing."""
    conn = fresh_device()
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        conn.close()
    assert 'retail_settings' not in tables, (
        'fresh_device already has retail_settings -- this fixture is no longer building a '
        'genuinely un-ensured database, so neither RED nor GREEN below proves anything')
    assert 'products' in tables and 'inventory_movements' in tables, (
        'sanity: schema.py\'s own versioned migrations must have run')


def test_g1_red_without_the_seam_reproduces_the_measured_defect(fresh_device, relay):
    """RED. No boot seam has touched `fresh_device` -- exactly a headless
    till, or a phone that never opened a settings screen, on its very first
    sync tick. Reproduces the field defect's own exception verbatim."""
    relay.push(_fresh_join_batch())
    service = SyncService(client_factory=lambda: relay, get_conn=fresh_device,
                          local_company_id_provider=lambda: 'fresh-join-co')

    with pytest.raises(sqlite3.OperationalError, match='no such table: retail_settings'):
        service.pull_once()

    # The decisive half of the measured defect: the WHOLE batch rolled back,
    # not just the retail_setting row -- so the cursor never advanced and
    # the product/inventory_movement in the SAME batch never landed either.
    conn = fresh_device()
    try:
        assert service.read_cursor(conn) == 0, (
            'cursor must not advance past a batch that raised mid-apply')
        products = conn.execute("SELECT * FROM products").fetchall()
        assert len(products) == 0, (
            'the product in the SAME batch as the failing retail_setting event must also be '
            'absent -- this is the "new device received NO products" half of the defect, not '
            'merely "the setting failed to apply"')
    finally:
        conn.close()


def test_g1_green_with_the_seam_the_batch_applies_cleanly(fresh_device, relay):
    """GREEN. Runs the REAL `_ensure_runtime_schema_for_sync()` against this
    SAME fresh device -- not a hand-rolled reimplementation of what it does
    -- pointed at it exactly the way the real boot seam points at whichever
    device it is running on (see the monkeypatch below)."""
    # `_ensure_runtime_schema_for_sync()` calls the module-level
    # `get_retail_conn()` app.py imported from database.schema -- pointing
    # it at THIS test's fresh_device is what makes calling the real,
    # unmodified production function equivalent to "this device's own boot
    # just ran". `_CREDIT_SCHEMA_READY` is a PROCESS-global flag (not a
    # per-database one) and this process's own module-level `init_app()`
    # already flipped it True against install A's database above -- forced
    # False around this one call and restored immediately after, identical
    # to retail_money_sync_test.py's `install_b` fixture's own documented
    # workaround for the exact same trap.
    previous_conn_fn = _app_module.get_retail_conn
    previous_flag = _retail_api_module._CREDIT_SCHEMA_READY
    _app_module.get_retail_conn = fresh_device
    _retail_api_module._CREDIT_SCHEMA_READY = False
    try:
        _app_module._ensure_runtime_schema_for_sync()
    finally:
        _app_module.get_retail_conn = previous_conn_fn
        _retail_api_module._CREDIT_SCHEMA_READY = previous_flag

    relay.push(_fresh_join_batch())
    service = SyncService(client_factory=lambda: relay, get_conn=fresh_device,
                          local_company_id_provider=lambda: 'fresh-join-co')

    service.pull_once()  # must not raise

    conn = fresh_device()
    try:
        assert service.read_cursor(conn) == 3, (
            f'cursor must advance past all 3 events, got {service.read_cursor(conn)}')
        products = conn.execute("SELECT * FROM products WHERE id=?", (_FRESH_JOIN_PRODUCT_ID,)).fetchall()
        assert len(products) == 1, 'the product row must exist'
        assert products[0]['name'] == 'Fresh Join Widget'
        movements = conn.execute(
            "SELECT * FROM inventory_movements WHERE product_id=?", (_FRESH_JOIN_PRODUCT_ID,)).fetchall()
        assert len(movements) == 1, 'the inventory_movement row must exist'
        assert movements[0]['quantity'] == 10
        setting = conn.execute(
            "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='base_currency'",
            ('fresh-join-co',)).fetchone()
        assert setting is not None and setting['svalue'] == 'JOD', (
            f'retail_settings must hold base_currency=JOD, got {setting!r}')
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────
# GROUP 2 -- the check RAN: the seam is actually wired into each boot path
# ─────────────────────────────────────────────────────────────────────────

def test_g2_init_app_already_ensured_the_schema_before_any_test_touched_a_route():
    """Evidence for `init_app()`'s own call site: `app = _app_module.
    init_app()` (this file's module level, above) is the FIRST thing that
    runs in this process -- nothing before it has called a retail_api
    route, built a SyncService, or touched `_ensure_credit_schema` any
    other way. If `retail_settings` already exists on install A's own
    database and `_CREDIT_SCHEMA_READY` is already True by the time this
    (or any other) test function runs, the only thing that could have put
    it there is `init_app()`'s own call to `_ensure_runtime_schema_for_
    sync()`."""
    assert _retail_api_module._CREDIT_SCHEMA_READY is True, (
        'init_app() must have already run the runtime-schema ensure by the time any test '
        'in this process executes')
    conn = _app_module.get_retail_conn()
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        conn.close()
    assert {'retail_settings', 'payment_methods', 'doc_sequences'} <= tables, (
        f'init_app() should have created all three lazy tables before any route ran: {sorted(tables)}')


def test_g2_start_sync_after_activation_calls_the_seam_exactly_once(monkeypatch):
    """Counting-spy proof, same technique as retail_sync_hot_start_after_
    activation_test.py's own `_counting_start`/`_counting_site_relay`: the
    REAL `_ensure_runtime_schema_for_sync` still runs (this is a spy, not a
    replacement), wrapped to count calls."""
    calls = {'count': 0}
    real_ensure = _app_module._ensure_runtime_schema_for_sync

    def _counting_ensure():
        calls['count'] += 1
        return real_ensure()

    monkeypatch.setattr(_app_module, '_ensure_runtime_schema_for_sync', _counting_ensure)
    _persist_activation(_CLOSED_LOOPBACK_URL)

    _app_module._start_sync_after_activation()

    assert calls['count'] == 1, (
        f'_start_sync_after_activation() must call the runtime-schema seam exactly once per '
        f'invocation, got {calls["count"]}')
    assert _app_module._sync_service is not None, (
        'sanity: this call must actually have reached the service-building branch, or the '
        'count above would be trivially satisfied by an early return')


def test_g2_enter_hub_self_sync_calls_the_seam_exactly_once(monkeypatch):
    """Same technique as the previous test, against `_enter_hub_self_sync()`
    -- the THIRD of the three call sites the task names. Installation id
    present (required past this function's own early deferral guard) and
    `LICENSING_PLATFORM` forced to WINDOWS (this function is a no-op on
    ANDROID, by design -- see its own docstring)."""
    calls = {'count': 0}
    real_ensure = _app_module._ensure_runtime_schema_for_sync

    def _counting_ensure():
        calls['count'] += 1
        return real_ensure()

    monkeypatch.setattr(_app_module, '_ensure_runtime_schema_for_sync', _counting_ensure)
    monkeypatch.setattr(_app_module, 'LICENSING_PLATFORM', 'WINDOWS')
    _persist_activation(_CLOSED_LOOPBACK_URL)

    _app_module._enter_hub_self_sync(start_timers=False)

    assert calls['count'] == 1, (
        f'_enter_hub_self_sync() must call the runtime-schema seam exactly once per '
        f'invocation, got {calls["count"]}')
    assert _app_module._sync_service is not None, (
        'sanity: this call must actually have reached the service-building branch')


def test_g2_seam_never_raises_even_when_ensure_credit_schema_itself_does(monkeypatch, caplog):
    """MUTATION M4's proof, driven the cheap in-process way (equivalent to
    physically making the seam raise): whatever `_ensure_credit_schema`
    itself throws, `_ensure_runtime_schema_for_sync()` must swallow it,
    never propagate, and log it -- same NEVER RAISES contract as every
    neighbouring activation-adjacent seam in app.py (see that function's
    own docstring). A till whose SQLite file is briefly locked by another
    process must still finish booting."""
    def _boom(conn):
        raise sqlite3.OperationalError('database is locked (fabricated for this test)')

    monkeypatch.setattr(_app_module, '_ensure_credit_schema', _boom)
    caplog.set_level(logging.ERROR, logger='app')

    _app_module._ensure_runtime_schema_for_sync()  # must not raise

    logged = [r for r in caplog.records
               if r.levelno >= logging.ERROR and 'Runtime schema ensure' in r.getMessage()]
    assert logged, (
        f'the exception must be logged, not merely swallowed silently: {caplog.records!r}')


def test_g2_seam_is_a_genuine_no_op_once_ensure_credit_schema_has_already_run(fresh_device):
    """MUTATION M3's allow-half: the seam must not do anything HARMFUL on a
    device that has ALREADY been ensured (the ordinary case on every boot
    after the first) -- calling it twice must be a clean no-op, never a
    second DDL pass, never an error. Proves the ALLOW half of the
    idempotency claim `_ensure_credit_schema`'s own docstring makes,
    mirroring CLAUDE.md's standing rule to prove both directions of
    anything that both denies (M4: a broken ensure must not wedge boot) and
    allows (this: an already-ensured device must boot exactly as before)."""
    previous_conn_fn = _app_module.get_retail_conn
    previous_flag = _retail_api_module._CREDIT_SCHEMA_READY
    _app_module.get_retail_conn = fresh_device
    _retail_api_module._CREDIT_SCHEMA_READY = False
    try:
        _app_module._ensure_runtime_schema_for_sync()  # first call -- real DDL
        _app_module._ensure_runtime_schema_for_sync()  # second call -- must be a no-op
    finally:
        _app_module.get_retail_conn = previous_conn_fn
        _retail_api_module._CREDIT_SCHEMA_READY = previous_flag

    conn = fresh_device()
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        conn.close()
    assert {'retail_settings', 'payment_methods', 'doc_sequences'} <= tables


# ─────────────────────────────────────────────────────────────────────────
# GROUP 3 -- the REAL boot, in a FRESH interpreter (adversarial-verifier pass)
# ─────────────────────────────────────────────────────────────────────────
#
# Why this group exists -- two gaps GROUPS 1-2 left open, each found by a
# mutation that stayed GREEN against all 8 tests above:
#
#   M6: replace init_app()'s `_ensure_runtime_schema_for_sync()` with a bare
#       `_ensure_credit_schema(get_retail_conn())`. Every test above still
#       passed -- the schema IS created, so GROUP 2's init_app evidence
#       (flag True, tables present) is satisfied, and the never-raises test
#       only exercises the seam function in isolation, never init_app()
#       calling it. But that mutant crashes init_app() the moment the ensure
#       raises (a retail.db briefly locked by another process at boot), i.e.
#       exactly the "till cannot boot for a reason unrelated to selling"
#       outcome the seam's NEVER RAISES contract exists to prevent.
#   GROUP 1's GREEN half calls the seam BY NAME against a side database;
#       nothing above boots a device and then pulls into THAT device's own
#       retail.db the way the field till did. Against the pre-fix app.py it
#       failed with AttributeError (no such function), not with the field
#       defect's own symptom.
#
# Both need a fresh interpreter: this file's module-level `init_app()` has
# already run in this process, and `create_app()`'s docstring explains why
# init_app() is single-shot per process. The child below boots app.py
# against its own brand-new AURA_APP_DATA (a pytest tmp_path, never a lab
# dir), calls NO route, counts the seam (a spy that still runs the real
# function), and reports what it saw as one JSON line.
_BOOT_CHILD = r'''
import json, os, sqlite3, sys, uuid
suite_root, backend_dir, data_dir, mode = sys.argv[1:5]
sys.path[:0] = [suite_root, backend_dir]
os.makedirs(os.path.join(data_dir, "database", "subsystems"), exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=backend_dir, AURA_APP_DATA=data_dir,
                  AURA_SITE_RELAY_ENABLED="0")
for k in ("AURA_DEV", "AURA_SYNC_RELAY_URL"):
    os.environ.pop(k, None)
import app as A
out = {"seam_calls": 0}
# getattr, not attribute access: against an app.py that has no seam at all
# (the pre-fix code) this child must still boot and pull, so the parent
# sees the field symptom itself rather than an AttributeError here.
_real_seam = getattr(A, "_ensure_runtime_schema_for_sync", None)
if _real_seam is not None:
    def _spy():
        out["seam_calls"] += 1
        return _real_seam()
    A._ensure_runtime_schema_for_sync = _spy
if mode == "raise":
    def _boom(conn):
        raise sqlite3.OperationalError("database is locked (fabricated at boot by the G3 child)")
    A._ensure_credit_schema = _boom
try:
    A.init_app()
    out["booted"] = True
except BaseException as exc:
    out["booted"] = False
    out["boot_error"] = repr(exc)
out["init_app_has_run"] = bool(getattr(A, "_INIT_APP_HAS_RUN", False))
conn = A.get_retail_conn()
try:
    out["tables_before_any_route"] = sorted(
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"))
finally:
    conn.close()
if mode == "pull" and out["booted"]:
    from commercial_runtime.sync.sync_service import SyncService
    batch = json.loads(sys.stdin.read())
    for ev in batch:
        ev["payload"]["_sender_schema_version"] = A.RETAIL_SCHEMA_VERSION
    class _Relay:
        def pull(self, since):
            pending = [dict(e, seq=i + 1) for i, e in enumerate(batch) if i + 1 > since]
            return {"events": pending, "cursor": pending[-1]["seq"] if pending else since}
        def push(self, events):
            return {"stored": len(events), "received": len(events)}
    relay = _Relay()
    # Wired the way app.py wires the retail SyncService: the SAME booted
    # get_retail_conn, the same local_ensure_schema hook, the schema-skew
    # guard armed. Only the relay is fake (never the network).
    svc = SyncService(client_factory=lambda: relay, get_conn=A.get_retail_conn,
                      local_company_id_provider=lambda: "fresh-join-co",
                      local_ensure_schema=A._ensure_credit_schema,
                      local_schema_version_provider=lambda: A.RETAIL_SCHEMA_VERSION)
    try:
        svc.pull_once()
        out["pull_error"] = None
    except Exception as exc:
        out["pull_error"] = "%s: %s" % (type(exc).__name__, exc)
    conn = A.get_retail_conn()
    try:
        out["cursor"] = svc.read_cursor(conn)
        out["product_names"] = [r[0] for r in conn.execute("SELECT name FROM products")]
        try:
            out["base_currency"] = [list(r) for r in conn.execute(
                "SELECT company_id, svalue FROM retail_settings WHERE skey='base_currency'")]
        except sqlite3.OperationalError as exc:
            out["base_currency"] = "ERR " + str(exc)
    finally:
        conn.close()
print("G3_RESULT " + json.dumps(out), flush=True)
os._exit(0)
'''


def _boot_fresh_interpreter(data_dir, mode, stdin_text=""):
    import json
    import subprocess
    proc = subprocess.run(
        [sys.executable, "-c", _BOOT_CHILD, str(SUITE_ROOT), str(BACKEND_DIR), str(data_dir), mode],
        input=stdin_text, capture_output=True, text=True, timeout=600)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("G3_RESULT ")]
    assert lines, (
        f"the fresh-interpreter boot never reported back (rc={proc.returncode}) -- stdout tail:\n"
        f"{proc.stdout[-2000:]}\nstderr tail:\n{proc.stderr[-4000:]}")
    return json.loads(lines[-1][len("G3_RESULT "):])


def test_g3_a_really_booted_device_applies_the_fresh_join_batch_with_no_route_called(tmp_path):
    """The field defect's own shape, end to end: boot app.py, call NO route,
    pull the fresh-join batch into THAT device's own retail.db. Against the
    pre-fix app.py this reproduced the field symptom verbatim (`no such
    table: retail_settings`, cursor 0, no products). Also the direct count
    for init_app()'s boot path that GROUP 2 could only infer: exactly one
    seam call during a fresh device's boot."""
    import json
    out = _boot_fresh_interpreter(tmp_path / "booted_device", "pull",
                                  stdin_text=json.dumps(_fresh_join_batch()))
    assert out["booted"] is True, out
    # Behaviour first, so a regression reports the field symptom itself.
    assert out["pull_error"] is None, (
        f"{out['pull_error']} -- cursor={out['cursor']}, products={out['product_names']}")
    assert out["cursor"] == 3, f"cursor must advance past all 3 events, got {out['cursor']}"
    assert out["product_names"] == ["Fresh Join Widget"], out["product_names"]
    assert out["base_currency"] == [["fresh-join-co", "JOD"]], out["base_currency"]
    assert {"retail_settings", "payment_methods", "doc_sequences"} <= set(out["tables_before_any_route"]), (
        "the lazy tables must exist straight after boot, before any route has run")
    assert out["seam_calls"] == 1, (
        f"init_app() must run the runtime-schema seam exactly once on a fresh boot, got {out['seam_calls']}")


def test_g3_boot_survives_a_failing_ensure_and_logs_it_to_the_real_backend_log(tmp_path):
    """The NEVER RAISES contract at the level it actually protects -- a
    till's boot -- not just the seam function in isolation (GROUP 2's own
    test, which the M6 mutant above passes). `_ensure_credit_schema` is
    forced to raise inside a fresh interpreter BEFORE init_app() runs:
    init_app() must still complete, the seam must still have been reached
    (so the boot survived BECAUSE the seam swallowed it, not because the
    ensure was skipped), and the failure must land in the product's own
    backend.log -- the file an operator actually reads -- with its
    traceback, never swallowed silently."""
    data_dir = tmp_path / "locked_at_boot"
    out = _boot_fresh_interpreter(data_dir, "raise")
    assert out["booted"] is True, (
        f"init_app() must survive a failing runtime-schema ensure: {out.get('boot_error')}")
    assert out["init_app_has_run"] is True, out
    assert out["seam_calls"] == 1, (
        f"the boot must have survived because the seam swallowed the error, not because init_app() "
        f"never reached it: seam_calls={out['seam_calls']}")
    log_path = data_dir / "logs" / "backend.log"
    assert log_path.exists(), f"no backend.log at {log_path}"
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    assert "Runtime schema ensure" in log_text and "fabricated at boot by the G3 child" in log_text, (
        "the swallowed error (message AND its exception text) must be in backend.log:\n" + log_text[-3000:])
