"""Unit-level tests for the schema-version skew guard
(commercial_runtime/sync/sync_service.py's `local_schema_version_provider`
constructor parameter, `SyncService._parse_schema_version`, `read_outbox`'s
`_sender_schema_version` stamping, and the version gate at the top of
`_apply_event`). See sync_service.py's own module docstring, "Schema-version
skew guard" section, for the full design and the fail-closed reasoning this
file pins.

The hole this guard closes: NOTHING anywhere in this file, before this
guard existed, ever compared a sender's and a receiver's schema version.
A device on retail schema v38 applying a v39 device's row silently dropped
every column v38's code had never been taught to write (`p.get(...)` simply
never asked for them) -- no error, no log, no failing test. That specific
instance (products.avg_cost / sale_items.unit_cost / sales.points_redeemed_
amount) is fixed elsewhere in this same change; this guard is the
STRUCTURAL fix so the next version bump cannot repeat it silently.

Fixture shape copied from test_retail_setting_sync_apply.py (itself copied
from test_sync_service.py's category-branch tests): a real sqlite file
under tmp_path, a minimal schema matching only the columns the `category`
branch of `_apply_event` actually touches plus the sync bookkeeping tables
(`sync_outbox`/`sync_cursor`/`sync_apply_quarantine`), a real `SyncService`
applied through the real `apply_pull_result`/`read_outbox`, never a
hand-rolled substitute for either.

Full two-device, real-Flask-app integration scenarios (same version syncs
as today, older-applies-newer is refused, newer-pulls-older applies,
garbage/absent fails closed, a refusal does not corrupt the database) live
in products/retail/tests/retail_sync_version_skew_test.py -- this file is
the fast, narrow unit layer underneath that: the parser's exact trust
boundary, the wire-stamping mechanics, and the gate's four branches in
isolation, each provable in milliseconds without booting a Flask app.

Run:
    pytest commercial_runtime/sync/tests/test_schema_version_skew_guard.py -v
"""
import json
import sqlite3
import uuid

import pytest

from commercial_runtime.sync.sync_service import RETAIL_SYNC_ENTITY_TYPES, SyncService

_SCHEMA = """
CREATE TABLE categories (
    id TEXT PRIMARY KEY,
    company_id INTEGER DEFAULT 1,
    name TEXT NOT NULL,
    description TEXT,
    row_version INTEGER NOT NULL DEFAULT 1,
    updated_at_utc TEXT,
    deleted_at_utc TEXT
);
CREATE TABLE sync_outbox (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE sync_cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_seq INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO sync_cursor (id, last_seq) VALUES (1, 0);
CREATE TABLE sync_apply_quarantine (
    entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    reason TEXT NOT NULL,
    detail TEXT,
    quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_id, event_type)
);
"""

RECEIVER_COMPANY_ID = "receiver-co"

# Sentinel distinguishing "local_schema_version_provider was never passed to
# SyncService at all" (the guard is entirely inert -- see __init__'s own
# docstring) from "it was passed and returns None" (opted in, but this
# device's own version could not be resolved -- fails closed). Both are
# real, distinct, separately-tested states; collapsing them to one Python
# `None` in this file's own helper would make that distinction untestable.
_NOT_WIRED = object()


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "retail.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def get_conn(db_path):
    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        return c
    return _get_conn


def _make_service(get_conn, *, schema_version=_NOT_WIRED, local_company_id=RECEIVER_COMPANY_ID):
    kwargs = dict(client_factory=lambda: None, get_conn=get_conn,
                  local_company_id_provider=lambda: local_company_id)
    if schema_version is not _NOT_WIRED:
        kwargs["local_schema_version_provider"] = lambda: schema_version
    return SyncService(**kwargs)


def _category_event(sender_schema_version=_NOT_WIRED, entity_id=None, **payload_overrides):
    entity_id = entity_id or str(uuid.uuid4())
    payload = {"id": entity_id, "name": "Skew Category", "row_version": 1}
    if sender_schema_version is not _NOT_WIRED:
        payload["_sender_schema_version"] = sender_schema_version
    payload.update(payload_overrides)
    return {
        "id": str(uuid.uuid4()), "entity_type": "category", "entity_id": entity_id,
        "event_type": "create", "payload": payload,
    }


def _apply(get_conn, events, *, cursor=1, schema_version=_NOT_WIRED):
    """One-shot apply of a pre-built event batch through the REAL pull path
    (`apply_pull_result`) -- never a direct `_apply_event` call bypassing it,
    so this exercises the real cursor-advance / quarantine / retry wiring,
    matching test_retail_setting_sync_apply.py's own `_apply` helper."""
    conn = get_conn()
    service = _make_service(get_conn, schema_version=schema_version)
    try:
        service.apply_pull_result(conn, {"events": events, "cursor": cursor})
        conn.commit()
    finally:
        conn.close()
    return service


def _category_row(get_conn, entity_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM categories WHERE id=?", (entity_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _quarantine_rows(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sync_apply_quarantine").fetchall()]
    conn.close()
    return rows


def _cursor(get_conn):
    conn = get_conn()
    row = conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()
    conn.close()
    return row["last_seq"]


# ── 1. `_parse_schema_version` -- the guard's one trust boundary ───────────

def test_parse_schema_version_trusts_only_a_nonnegative_int():
    parse = SyncService._parse_schema_version
    assert parse(39) == 39
    assert parse(0) == 0


@pytest.mark.parametrize("garbage", [None, -1, True, False, "39", 39.0, [], {}, object()])
def test_parse_schema_version_rejects_everything_that_is_not_a_nonnegative_int(garbage):
    # `bool` is a subclass of `int` in Python -- True/False are in this list
    # deliberately, not as an oversight, because `isinstance(True, int)` is
    # True and a naive `isinstance(value, int)` check alone would silently
    # accept them as 1/0.
    assert SyncService._parse_schema_version(garbage) is None


# ── 2. `read_outbox` stamping ───────────────────────────────────────────────

def _seed_outbox_row(get_conn):
    entity_id = str(uuid.uuid4())
    conn = get_conn()
    conn.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), "category", entity_id, "create",
         json.dumps({"id": entity_id, "name": "Outbox Category"}), "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return entity_id


def test_read_outbox_stamps_sender_schema_version_when_provider_configured(get_conn):
    _seed_outbox_row(get_conn)
    service = _make_service(get_conn, schema_version=39)
    events = service.read_outbox(get_conn())
    assert len(events) == 1
    assert events[0]["payload"]["_sender_schema_version"] == 39


def test_read_outbox_stamps_nothing_when_no_provider_is_wired(get_conn):
    """The guard's opt-in default: a SyncService built exactly like every
    pre-guard call site (no local_schema_version_provider at all) must
    produce byte-for-byte the pre-guard outbox shape."""
    _seed_outbox_row(get_conn)
    service = _make_service(get_conn)  # schema_version left at _NOT_WIRED
    events = service.read_outbox(get_conn())
    assert "_sender_schema_version" not in events[0]["payload"]


def test_read_outbox_stamps_nothing_when_the_providers_own_value_is_garbage(get_conn):
    """A device that cannot state its own version honestly has no business
    asserting one -- see read_outbox's own docstring. A garbage return value
    from the provider must not be stamped at all (never coerced, never
    defaulted to 0)."""
    _seed_outbox_row(get_conn)
    service = _make_service(get_conn, schema_version="not-an-int")
    events = service.read_outbox(get_conn())
    assert "_sender_schema_version" not in events[0]["payload"]


# ── 3. the gate: four outcomes ──────────────────────────────────────────────

def test_neither_side_configured_the_guard_is_completely_inert(get_conn):
    """THE REGRESSION-RISK CASE, called out explicitly in this guard's own
    design brief: every pre-existing call site and every pre-existing test
    constructs a SyncService with no local_schema_version_provider at all.
    An event with no `_sender_schema_version` key (exactly what `read_
    outbox` produces for such a sender) must apply completely normally when
    the RECEIVER also has not opted in -- the gate must never even evaluate,
    let alone refuse."""
    ev = _category_event()  # no _sender_schema_version key at all
    entity_id = ev["entity_id"]
    _apply(get_conn, [ev])  # receiver: schema_version=_NOT_WIRED (not opted in)

    row = _category_row(get_conn, entity_id)
    assert row is not None and row["name"] == "Skew Category"
    assert _quarantine_rows(get_conn) == []


def test_equal_versions_apply_normally_the_critical_allow_half(get_conn):
    ev = _category_event(sender_schema_version=39)
    entity_id = ev["entity_id"]
    _apply(get_conn, [ev], schema_version=39)

    row = _category_row(get_conn, entity_id)
    assert row is not None and row["name"] == "Skew Category"
    assert _quarantine_rows(get_conn) == []


def test_sender_ahead_of_receiver_is_refused_the_lossy_direction(get_conn):
    ev = _category_event(sender_schema_version=39)
    entity_id = ev["entity_id"]
    _apply(get_conn, [ev], schema_version=38)  # receiver behind

    assert _category_row(get_conn, entity_id) is None, (
        "an older receiver applied a newer sender's row -- exactly the "
        "v38/v39 hole this guard exists to close"
    )
    quarantined = _quarantine_rows(get_conn)
    assert len(quarantined) == 1
    assert quarantined[0]["reason"] == "schema_version_too_new"
    assert quarantined[0]["entity_type"] == "category"
    assert quarantined[0]["entity_id"] == entity_id
    assert "39" in quarantined[0]["detail"] and "38" in quarantined[0]["detail"]
    # The cursor still advances past a quarantined row -- identical posture
    # to the pre-existing missing-parent quarantine (see apply_pull_
    # result's own module-docstring-referenced comment): a refusal parks
    # the ROW, never wedges the PULL.
    assert _cursor(get_conn) == 1


def test_sender_behind_receiver_applies_normally_the_usually_fine_direction(get_conn):
    ev = _category_event(sender_schema_version=38)
    entity_id = ev["entity_id"]
    _apply(get_conn, [ev], schema_version=39)  # receiver ahead

    row = _category_row(get_conn, entity_id)
    assert row is not None and row["name"] == "Skew Category"
    assert _quarantine_rows(get_conn) == []


def test_absent_sender_version_fails_closed_once_the_receiver_has_opted_in(get_conn):
    """No `_sender_schema_version` key at all -- a legacy/not-yet-upgraded
    sender -- on a receiver that HAS opted in. Deliberately refused, not
    treated as 'must be old, therefore safe': see sync_service.py's module
    docstring, 'FAIL-CLOSED ON UNPARSEABLE/ABSENT/GARBAGE' section, for why
    this is the opposite of this same file's row_version legacy
    convention on purpose."""
    ev = _category_event()  # no _sender_schema_version key
    entity_id = ev["entity_id"]
    _apply(get_conn, [ev], schema_version=38)

    assert _category_row(get_conn, entity_id) is None
    quarantined = _quarantine_rows(get_conn)
    assert len(quarantined) == 1
    assert quarantined[0]["reason"] == "schema_version_unverifiable"


@pytest.mark.parametrize("garbage_sender_version", [-1, "39", 39.0, True])
def test_garbage_sender_version_fails_closed(get_conn, garbage_sender_version):
    ev = _category_event(sender_schema_version=garbage_sender_version)
    entity_id = ev["entity_id"]
    _apply(get_conn, [ev], schema_version=38)

    assert _category_row(get_conn, entity_id) is None
    quarantined = _quarantine_rows(get_conn)
    assert len(quarantined) == 1
    assert quarantined[0]["reason"] == "schema_version_unverifiable"


def test_garbage_receiver_version_fails_closed_even_with_a_valid_sender(get_conn):
    """The receiver opted in (a provider IS configured) but its own return
    value does not parse -- this device cannot state its own version, so it
    must refuse rather than assume it is compatible with anything."""
    ev = _category_event(sender_schema_version=38)
    entity_id = ev["entity_id"]
    _apply(get_conn, [ev], schema_version="not-an-int")

    assert _category_row(get_conn, entity_id) is None
    quarantined = _quarantine_rows(get_conn)
    assert len(quarantined) == 1
    assert quarantined[0]["reason"] == "schema_version_unverifiable"


# ── 4. a refusal is retried and resolves once the receiver upgrades ────────

def test_a_refused_event_is_retried_and_applies_once_the_receiver_upgrades(get_conn):
    """The 'these two don't sync until both are updated' degradation this
    guard's design brief calls for must actually heal itself -- an operator
    upgrading the behind device must not also have to manually clear
    sync_apply_quarantine."""
    ev = _category_event(sender_schema_version=39)
    entity_id = ev["entity_id"]
    _apply(get_conn, [ev], schema_version=38)
    assert _category_row(get_conn, entity_id) is None
    assert len(_quarantine_rows(get_conn)) == 1

    # This device is upgraded (schema_version now 39, matching the sender)
    # and the next ordinary pull tick retries the quarantine automatically
    # -- an EMPTY new batch is enough, exactly like the pre-existing
    # missing-parent retry path.
    _apply(get_conn, [], schema_version=39)

    row = _category_row(get_conn, entity_id)
    assert row is not None and row["name"] == "Skew Category", (
        "the quarantined row must apply on the very next pull tick after "
        "this device is upgraded, with no operator action beyond the "
        "upgrade itself"
    )
    assert _quarantine_rows(get_conn) == []


# ── 5. sanity: this guard's entity type is a real retail entity type ───────

def test_category_is_a_real_retail_sync_entity_type():
    assert "category" in RETAIL_SYNC_ENTITY_TYPES
