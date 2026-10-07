"""W2b + W3a (schema v50) on the sync APPLY side: promotions and the extras
configuration shared between computers.

Before v50 a promotion and the extras configuration (modifier groups, options,
and which product carries which group) never left the computer they were typed
on. This file pins what the RECEIVING side must do with the four new entity
types `promotion`, `modifier_group`, `modifier_option`, `product_modifier_group`
and with the `modifiers` / `promotion` snapshots a `sale_item` now carries:

  * create/update under the RECEIVER's company, merged last-writer-wins per row
    on (row_version, updated_at_utc, content) -- two computers must pick the
    SAME winner whichever order the two edits arrive in,
  * a winning update writes only the fields it changed,
  * a stale edit never resurrects a tombstone / deactivated row,
  * a promotion's branch resolves by uid; an unknown branch PARKS (never widens
    to every branch, never narrows to the default branch),
  * a promotion aimed at both / neither of product and category parks,
  * two different attachment ids for one (product, group) converge on the
    lexicographically lower id on every computer, and never raise,
  * a sale line's extras / promotion snapshots land once; an older payload
    without the keys still applies,
  * applying NEVER queues anything (a receiver must not re-announce).

The merge rule is duplicated in sync_service (it cannot import products/ code);
the first tests pin the twin equal to core/retail/config_sync.py.

Same fixture technique as retail_po_sync_apply_test.py: a REAL `init_retail()`
database, `SyncService._apply_event` driven directly. One file per process.

Run:  <venv python> -m pytest -q -p no:cacheprovider products/retail/tests/retail_config_sync_apply_test.py
"""
from __future__ import annotations

import itertools
import json
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RETAIL_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_RETAIL_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import database.schema as schema  # noqa: E402
import api.retail_api as _retail_api_module  # noqa: E402
from api.retail_api import _ensure_credit_schema  # noqa: E402
from commercial_runtime.identity import registry_db  # noqa: E402
from commercial_runtime.sync import sync_service as ss  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402
from core.retail import config_sync as core  # noqa: E402

CID = "company-config-apply-test"
PRODUCT = str(uuid.uuid4())
PROMO_UID = str(uuid.uuid4())
GROUP = str(uuid.uuid4())
OPTION = str(uuid.uuid4())
LINK = str(uuid.uuid4())
T1, T2, T3 = ("2026-10-07T10:00:00+00:00", "2026-10-07T10:05:00+00:00",
              "2026-10-07T10:10:00+00:00")


def _make_db(tmp_path, monkeypatch, tag="a"):
    reg_dir = tmp_path / f"registry_{tag}"
    monkeypatch.setattr(registry_db, "_db_dir", str(reg_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(reg_dir / "registry.db"))
    registry_db.init_registry_db()
    root = tmp_path / f"install_{tag}"
    subsys = root / "subsystems"
    subsys.mkdir(parents=True, exist_ok=True)
    original = (schema.BASE_DIR, schema.SUBSYS_DIR)
    schema.BASE_DIR, schema.SUBSYS_DIR = str(root), str(subsys)
    try:
        schema.init_retail()
    finally:
        schema.BASE_DIR, schema.SUBSYS_DIR = original
    db_path = subsys / "retail.db"

    def _get():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    previous = _retail_api_module._CREDIT_SCHEMA_READY
    _retail_api_module._CREDIT_SCHEMA_READY = False
    try:
        c = _get()
        try:
            _ensure_credit_schema(c)
            c.execute("INSERT INTO products (id, company_id, sku, name) VALUES (?,?,?,?)",
                      (PRODUCT, CID, "SKU-1", "Widget"))
            c.commit()
        finally:
            c.close()
    finally:
        _retail_api_module._CREDIT_SCHEMA_READY = previous
    return _get


@pytest.fixture
def get_conn(tmp_path, monkeypatch):
    return _make_db(tmp_path, monkeypatch)


def _svc(get_conn):
    return SyncService(client_factory=lambda: None, get_conn=get_conn,
                       local_company_id_provider=lambda: CID)


def _apply(get_conn, ev):
    conn = get_conn()
    try:
        ok = _svc(get_conn)._apply_event(conn, ev, local_company_id=CID)
        conn.commit()
        return ok
    finally:
        conn.close()


def _retry(get_conn):
    conn = get_conn()
    try:
        _svc(get_conn)._retry_quarantined_events(conn, local_company_id=CID)
        conn.commit()
    finally:
        conn.close()


def _rows(get_conn, sql, params=()):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _exec(get_conn, sql, params=()):
    conn = get_conn()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _parked(get_conn):
    return _rows(get_conn, "SELECT entity_type, entity_id, reason FROM sync_apply_quarantine")


def _conflicts(get_conn, entity_type=None):
    sql = "SELECT entity_type, entity_id, incoming_payload FROM sync_conflicts"
    args = ()
    if entity_type:
        sql += " WHERE entity_type=?"
        args = (entity_type,)
    out = []
    for r in _rows(get_conn, sql, args):
        r["payload"] = json.loads(r["incoming_payload"])
        out.append(r)
    return out


# ── event builders ───────────────────────────────────────────────────────────

def _promo(event_type="create", uid=PROMO_UID, changed=None, **over):
    p = {"uid": uid, "name": "10% off Widget", "discount_pct": 10.0,
         "product_id": PRODUCT, "category_id": None, "branch_uid": None,
         "starts_at": None, "ends_at": None, "status": "active",
         "row_version": 1, "updated_at_utc": T1,
         # A hostile/buggy sender's tenant key must never be trusted.
         "company_id": "the-senders-company"}
    p.update(over)
    if changed is not None:
        p["_changed_fields"] = changed
    return {"entity_type": "promotion", "event_type": event_type, "entity_id": uid, "payload": p}


def _group(event_type="create", gid=GROUP, changed=None, **over):
    p = {"id": gid, "name": "Extras", "min_select": 0, "max_select": None,
         "status": "active", "deleted_at_utc": None, "row_version": 1,
         "updated_at_utc": T1, "company_id": "the-senders-company"}
    p.update(over)
    if changed is not None:
        p["_changed_fields"] = changed
    return {"entity_type": "modifier_group", "event_type": event_type, "entity_id": gid, "payload": p}


def _option(event_type="create", oid=OPTION, changed=None, **over):
    p = {"id": oid, "group_id": GROUP, "name": "Cheese", "price_delta": 0.5,
         "cost_delta": 0.2, "is_default": 0, "sort_order": 0, "status": "active",
         "deleted_at_utc": None, "row_version": 1, "updated_at_utc": T1,
         "company_id": "the-senders-company"}
    p.update(over)
    if changed is not None:
        p["_changed_fields"] = changed
    return {"entity_type": "modifier_option", "event_type": event_type, "entity_id": oid, "payload": p}


def _link(event_type="create", lid=LINK, changed=None, **over):
    p = {"id": lid, "product_id": PRODUCT, "group_id": GROUP, "sort_order": 0,
         "deleted_at_utc": None, "row_version": 1, "updated_at_utc": T1,
         "company_id": "the-senders-company"}
    p.update(over)
    if changed is not None:
        p["_changed_fields"] = changed
    return {"entity_type": "product_modifier_group", "event_type": event_type,
            "entity_id": lid, "payload": p}


def _promo_row(get_conn, uid=PROMO_UID):
    rows = _rows(get_conn, "SELECT * FROM promotions WHERE uid=?", (uid,))
    return rows[0] if rows else None


# ── 1. the twin is pinned equal to the core module ───────────────────────────

_ROWS = [
    {"row_version": 1, "updated_at_utc": T1, "name": "a", "discount_pct": 10},
    {"row_version": 1, "updated_at_utc": T1, "name": "a", "discount_pct": 10.0},
    {"row_version": 1, "updated_at_utc": T1, "name": "b", "discount_pct": 10},
    {"row_version": 2, "updated_at_utc": T1, "name": "a", "discount_pct": 10},
    {"row_version": 2, "updated_at_utc": T2, "name": "a", "discount_pct": 10},
    {"row_version": 2, "updated_at_utc": T2, "name": "z", "discount_pct": 12.5},
    {"row_version": 2, "updated_at_utc": None, "name": "a", "discount_pct": 10},
    {"row_version": None, "updated_at_utc": T1, "name": "a", "discount_pct": 10},
    {"row_version": "3", "updated_at_utc": T3, "name": "a", "discount_pct": 10,
     "_changed_fields": ["name"], "company_id": "x", "id": 5},
    {"updated_at_utc": T3, "name": "a"},
]


def test_the_merge_rule_twin_equals_the_core_module_on_every_pair():
    """sync_service cannot import products/ code, so it carries a twin of the
    merge rule. Two computers on DIFFERENT rules would pick different winners
    for the same pair of edits and never converge, so every ordered pair of a
    battery that exercises each leg (version, stamp, content, missing keys,
    float spelling, meta keys) must give the SAME verdict from both."""
    seen = set()
    for local, incoming in itertools.product(_ROWS, _ROWS):
        want = core.lww_incoming_wins(local, incoming)
        assert ss._config_lww_incoming_wins(local, incoming) is want, (local, incoming)
        seen.add(want)
        assert ss._config_values_differ(local, incoming) is core.config_values_differ(local, incoming)
        assert ss._config_canonical_json(incoming) == core.canonical_json(incoming)
    assert seen == {True, False}, "the battery must reach both verdicts"


def test_both_computers_pick_the_same_winner_for_every_pair():
    """The point of the rule: for any two DIFFERENT versions, exactly one of
    (A keeps its row against B's) and (B keeps its row against A's) holds."""
    for x, y in itertools.permutations(_ROWS, 2):
        in_a = ss._config_lww_incoming_wins(x, y)   # A holds x, receives y
        in_b = ss._config_lww_incoming_wins(y, x)   # B holds y, receives x
        if in_a == in_b:
            # Both replace (they would swap and never converge) is forbidden
            # outright. Neither replacing is only allowed when the rule cannot
            # tell the two rows apart at all (same version, stamp and content).
            assert in_a is False, (x, y)
            keys = ss._config_shared_keys(x, y)
            assert (ss._config_version(x), ss._config_stamp(x), ss._config_content(x, keys)) == \
                   (ss._config_version(y), ss._config_stamp(y), ss._config_content(y, keys)), (x, y)


def test_the_four_types_are_handled_and_need_a_company_id():
    for t in ("promotion", "modifier_group", "modifier_option", "product_modifier_group"):
        assert t in ss.RETAIL_SYNC_ENTITY_TYPES
        assert t in ss.CONFIG_SYNC_ENTITY_TYPES
        assert t not in ss._ENTITY_TYPES_WITHOUT_COMPANY_ID


# ── 2. promotion create / update ─────────────────────────────────────────────

def test_promotion_create_lands_under_the_receivers_company_and_queues_nothing(get_conn):
    outbox_before = _rows(get_conn, "SELECT COUNT(*) AS n FROM sync_outbox")[0]["n"]
    assert _apply(get_conn, _promo()) is True
    row = _promo_row(get_conn)
    assert row["company_id"] == CID, "the sender's company key must never be trusted"
    assert (row["name"], row["discount_pct"], row["product_id"], row["category_id"]) == \
        ("10% off Widget", 10.0, PRODUCT, None)
    assert row["branch_id"] is None
    assert (row["row_version"], row["updated_at_utc"], row["status"]) == (1, T1, "active")
    assert row["sync_backfill_pending"] in (0, None), "a receiver must never re-announce"
    assert _rows(get_conn, "SELECT COUNT(*) AS n FROM sync_outbox")[0]["n"] == outbox_before
    # A replay is the same fact, not a second row.
    assert _apply(get_conn, _promo()) is True
    assert _rows(get_conn, "SELECT COUNT(*) AS n FROM promotions")[0]["n"] == 1


def test_promotion_window_travels_verbatim_never_converted(get_conn):
    _apply(get_conn, _promo(starts_at="2026-10-01 09:00", ends_at="2026-10-31 23:59"))
    row = _promo_row(get_conn)
    assert (row["starts_at"], row["ends_at"]) == ("2026-10-01 09:00", "2026-10-31 23:59")


def test_winning_update_writes_the_full_snapshot_not_only_the_changed_fields(get_conn):
    """WHOLE-ROW last-writer-wins (planner decision). This test used to pin the
    DELTA write (a winner touching only `_changed_fields`). It can no longer
    catch independent field edits merging: they do not merge on a tie any more,
    because delta writes left two computers different (see the convergence
    tests below). A higher-version edit is a normal update, not a conflict."""
    _apply(get_conn, _promo())
    _exec(get_conn, "UPDATE promotions SET name='Renamed here' WHERE uid=?", (PROMO_UID,))
    _apply(get_conn, _promo("update", discount_pct=25.0, row_version=2, updated_at_utc=T2,
                            changed=["discount_pct"]))
    row = _promo_row(get_conn)
    assert row["discount_pct"] == 25.0
    assert row["name"] == "10% off Widget"      # the winner's snapshot, whole row
    assert (row["row_version"], row["updated_at_utc"]) == (2, T2)
    assert _conflicts(get_conn, "promotion") == []


def test_stale_promotion_edit_loses_and_is_recorded_once_with_the_values(get_conn):
    _apply(get_conn, _promo(discount_pct=30.0, row_version=3, updated_at_utc=T3))
    stale = _promo("update", discount_pct=5.0, row_version=2, updated_at_utc=T2,
                   changed=["discount_pct"])
    assert _apply(get_conn, stale) is True
    assert _promo_row(get_conn)["discount_pct"] == 30.0
    assert _promo_row(get_conn)["row_version"] == 3
    # Redelivery (a replayed pull / a re-announce) must not pile up copies.
    _apply(get_conn, stale)
    assert len(_conflicts(get_conn, "promotion")) == 1


def test_a_stale_edit_that_changes_nothing_is_not_a_conflict(get_conn):
    _apply(get_conn, _promo(row_version=3, updated_at_utc=T3))
    _apply(get_conn, _promo("update", row_version=2, updated_at_utc=T2, changed=["name"]))
    assert _conflicts(get_conn, "promotion") == []


def test_a_stale_edit_never_resurrects_a_deactivated_promotion(get_conn):
    _apply(get_conn, _promo())
    _apply(get_conn, _promo("update", status="inactive", row_version=3, updated_at_utc=T3,
                            changed=["status"]))
    assert _promo_row(get_conn)["status"] == "inactive"
    # An older "reactivate" (rv2) and an older full snapshot (the create, rv1)
    # both arrive late.
    _apply(get_conn, _promo("update", status="active", row_version=2, updated_at_utc=T2,
                            changed=["status"]))
    _apply(get_conn, _promo())
    assert _promo_row(get_conn)["status"] == "inactive"
    # A genuinely newer reactivation still works.
    _apply(get_conn, _promo("update", status="active", row_version=4, updated_at_utc=T3,
                            changed=["status"]))
    assert _promo_row(get_conn)["status"] == "active"


def test_update_for_a_promotion_never_seen_here_creates_it_from_the_snapshot(get_conn):
    _apply(get_conn, _promo("update", row_version=4, updated_at_utc=T3, changed=["name"]))
    row = _promo_row(get_conn)
    assert row is not None and row["row_version"] == 4


# ── 3. equal-version ties converge in either arrival order ──────────────────

def _state(get_conn):
    p = _promo_row(get_conn)
    return (p["name"], p["discount_pct"], p["row_version"], p["updated_at_utc"])


@pytest.mark.parametrize("same_stamp", [True, False])
def test_an_equal_version_tie_converges_in_either_order(tmp_path, monkeypatch, same_stamp):
    """Two computers edit the SAME promotion at the SAME version. Each applies
    the other's edit; the pair must end identical whichever order they arrive
    in (the plain `incoming > local` gate that every older entity uses is false
    in BOTH directions here and would leave the two computers divergent)."""
    db1 = _make_db(tmp_path, monkeypatch, "one")
    db2 = _make_db(tmp_path, monkeypatch, "two")
    t_y = T2 if same_stamp else T3
    x = _promo("update", name="X name", discount_pct=11.0, row_version=2, updated_at_utc=T2,
               changed=["name", "discount_pct"])
    y = _promo("update", name="Y name", discount_pct=12.0, row_version=2, updated_at_utc=t_y,
               changed=["name", "discount_pct"])
    for db in (db1, db2):
        _apply(db, _promo())
    _apply(db1, x)
    _apply(db1, y)
    _apply(db2, y)
    _apply(db2, x)
    assert _state(db1) == _state(db2), (_state(db1), _state(db2))


def test_each_computer_applying_the_others_edit_to_its_own_converges(tmp_path, monkeypatch):
    """The real shape: A holds its OWN edit and receives B's; B holds its own
    and receives A's. With a full-field tie both must keep the same one."""
    db_a = _make_db(tmp_path, monkeypatch, "a2")
    db_b = _make_db(tmp_path, monkeypatch, "b2")
    for db in (db_a, db_b):
        _apply(db, _promo())
    ea = _promo("update", name="From A", row_version=2, updated_at_utc=T2, changed=["name"])
    eb = _promo("update", name="From B", row_version=2, updated_at_utc=T2, changed=["name"])
    _apply(db_a, ea)   # A's own edit, applied locally the same way
    _apply(db_b, eb)
    _apply(db_a, eb)   # now each receives the other's
    _apply(db_b, ea)
    assert _state(db_a) == _state(db_b)


# ── 3b. concurrent edits to DIFFERENT fields at one version converge ────────
# Whole-row LWW: both computers must end byte-identical, whichever edit is
# later and in whichever order each computer sees them. With delta writes the
# winner's computer kept both edits and the other kept only the winner's.

_CONVERGE_CASES = {
    "promotion": (lambda **o: _promo("update", **o), "name", "Name from A", "discount_pct", 33.0,
                  "SELECT name, discount_pct, product_id, category_id, branch_id, starts_at, ends_at, "
                  "status, row_version, updated_at_utc FROM promotions"),
    "modifier_group": (lambda **o: _group("update", **o), "name", "Name from A", "min_select", 2,
                       "SELECT name, min_select, max_select, status, deleted_at_utc, row_version, "
                       "updated_at_utc FROM modifier_groups"),
    "modifier_option": (lambda **o: _option("update", **o), "name", "Name from A", "price_delta", 9.5,
                        "SELECT group_id, name, price_delta, cost_delta, is_default, sort_order, status, "
                        "deleted_at_utc, row_version, updated_at_utc FROM modifier_options"),
    "product_modifier_group": (lambda **o: _link("update", **o), "sort_order", 7, "deleted_at_utc", T2,
                               "SELECT product_id, group_id, sort_order, deleted_at_utc, row_version, "
                               "updated_at_utc FROM product_modifier_groups"),
}
_CREATE = {"promotion": _promo, "modifier_group": _group, "modifier_option": _option,
           "product_modifier_group": _link}


@pytest.mark.parametrize("kind", sorted(_CONVERGE_CASES))
@pytest.mark.parametrize("a_stamp,b_stamp", [(T2, T3), (T3, T2), (T2, T2)])
def test_edits_to_different_fields_at_one_version_leave_both_computers_identical(
        tmp_path, monkeypatch, kind, a_stamp, b_stamp):
    mk, fa, va, fb, vb, select = _CONVERGE_CASES[kind]
    dbs = [_make_db(tmp_path, monkeypatch, f"{kind}{i}") for i in (0, 1)]
    for db in dbs:
        _apply(db, _CREATE[kind]())
    # Each computer edits a DIFFERENT field of the same row, both reaching rv2.
    ev_a = mk(**{fa: va, "row_version": 2, "updated_at_utc": a_stamp, "changed": [fa]})
    ev_b = mk(**{fb: vb, "row_version": 2, "updated_at_utc": b_stamp, "changed": [fb]})
    _apply(dbs[0], ev_a)          # computer A: its own edit, then B's
    _apply(dbs[0], ev_b)
    _apply(dbs[1], ev_b)          # computer B: its own edit, then A's
    _apply(dbs[1], ev_a)
    assert _rows(dbs[0], select) == _rows(dbs[1], select), kind


def test_a_dropped_concurrent_edit_is_recorded_once_on_the_computer_that_lost_it(tmp_path, monkeypatch):
    db = _make_db(tmp_path, monkeypatch, "rec")
    _apply(db, _promo())
    _apply(db, _promo("update", name="Local edit", row_version=2, updated_at_utc=T2, changed=["name"]))
    later = _promo("update", discount_pct=40.0, row_version=2, updated_at_utc=T3,
                   changed=["discount_pct"])
    _apply(db, later)             # wins on the later stamp, replaces the local edit
    _apply(db, later)             # redelivery adds nothing
    flagged = [c for c in _conflicts(db, "promotion") if c["payload"].get("reason") == "config_edit_superseded"]
    assert len(flagged) == 1
    assert flagged[0]["payload"]["name"] == "Local edit"   # which edit was dropped


# ── 4. promotion branch (D4) ─────────────────────────────────────────────────

def test_promotion_for_an_unknown_branch_parks_and_never_widens(get_conn):
    branch_uid = str(uuid.uuid4())
    ok = _apply(get_conn, _promo(branch_uid=branch_uid))
    assert ok is False
    assert _promo_row(get_conn) is None, "must NOT apply to every branch while the branch is unknown"
    assert [(r["entity_type"], r["reason"]) for r in _parked(get_conn)] == \
        [("promotion", "missing_parent:branch")]


def test_a_parked_promotion_replays_onto_the_branch_when_it_arrives(get_conn):
    branch_uid = str(uuid.uuid4())
    _apply(get_conn, _promo(branch_uid=branch_uid))
    _exec(get_conn, "INSERT INTO branches (company_id, name, uid) VALUES (?,?,?)",
          (CID, "Far branch", branch_uid))
    bid = _rows(get_conn, "SELECT id FROM branches WHERE uid=?", (branch_uid,))[0]["id"]
    _retry(get_conn)
    assert _promo_row(get_conn)["branch_id"] == bid
    assert _parked(get_conn) == []


def test_promotion_with_a_known_branch_is_filed_under_that_branch_not_the_default(get_conn):
    default_id = _rows(get_conn, "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1",
                       (CID,))
    branch_uid = str(uuid.uuid4())
    _exec(get_conn, "INSERT INTO branches (company_id, name, uid) VALUES (?,?,?)",
          (CID, "Second", branch_uid))
    bid = _rows(get_conn, "SELECT id FROM branches WHERE uid=?", (branch_uid,))[0]["id"]
    assert not default_id or default_id[0]["id"] != bid
    _apply(get_conn, _promo(branch_uid=branch_uid))
    assert _promo_row(get_conn)["branch_id"] == bid


def test_promotion_for_all_branches_stays_null_even_when_a_default_branch_exists(get_conn):
    _exec(get_conn, "INSERT INTO branches (company_id, name, uid) VALUES (?,?,?)",
          (CID, "Main", str(uuid.uuid4())))
    _apply(get_conn, _promo(branch_uid=None))
    assert _promo_row(get_conn)["branch_id"] is None


# ── 5. malformed promotion (D4) ──────────────────────────────────────────────

@pytest.mark.parametrize("over", [
    {"product_id": PRODUCT, "category_id": "cat-1"},   # both
    {"product_id": None, "category_id": None},         # neither
    {"name": ""},
    {"discount_pct": "ten"},
])
def test_a_promotion_that_breaks_its_own_rules_parks_and_does_not_apply(get_conn, over):
    assert _apply(get_conn, _promo(**over)) is False
    assert _promo_row(get_conn) is None
    assert [r["reason"] for r in _parked(get_conn)] == ["malformed_promotion"]


def test_a_category_promotion_applies_without_a_local_category_row(get_conn):
    """product_id / category_id carry no FK: a promotion for a category this
    computer has not received yet must still apply (never park on them)."""
    _apply(get_conn, _promo(product_id=None, category_id=str(uuid.uuid4())))
    assert _promo_row(get_conn) is not None
    assert _parked(get_conn) == []


# ── 6. modifier groups / options ────────────────────────────────────────────

def test_group_and_option_create_then_delta_update(get_conn):
    assert _apply(get_conn, _group()) is True
    assert _apply(get_conn, _option()) is True
    g = _rows(get_conn, "SELECT * FROM modifier_groups WHERE id=?", (GROUP,))[0]
    o = _rows(get_conn, "SELECT * FROM modifier_options WHERE id=?", (OPTION,))[0]
    assert g["company_id"] == CID and o["company_id"] == CID
    assert (o["group_id"], o["name"], o["price_delta"]) == (GROUP, "Cheese", 0.5)
    _exec(get_conn, "UPDATE modifier_options SET name='Local name' WHERE id=?", (OPTION,))
    _apply(get_conn, _option("update", price_delta=0.75, row_version=2, updated_at_utc=T2,
                             changed=["price_delta"]))
    o = _rows(get_conn, "SELECT * FROM modifier_options WHERE id=?", (OPTION,))[0]
    # Whole-row LWW: the winner's snapshot, so the local-only rename is replaced.
    # (Can no longer catch a delta write; see the convergence tests.)
    assert (o["price_delta"], o["name"], o["row_version"]) == (0.75, "Cheese", 2)


def test_an_option_whose_group_has_not_arrived_still_applies(get_conn):
    """`modifier_options.group_id` carries no literal FK (schema v26): arrival
    order between a group and its options must not matter."""
    assert _apply(get_conn, _option()) is True
    assert _parked(get_conn) == []


def test_a_stale_edit_never_resurrects_a_deactivated_option(get_conn):
    _apply(get_conn, _option())
    _apply(get_conn, _option("update", status="inactive", row_version=3, updated_at_utc=T3,
                             changed=["status"]))
    _apply(get_conn, _option("update", status="active", row_version=2, updated_at_utc=T2,
                             changed=["status"]))
    o = _rows(get_conn, "SELECT status FROM modifier_options WHERE id=?", (OPTION,))[0]
    assert o["status"] == "inactive"


def test_two_groups_with_the_same_name_both_apply_and_are_flagged_once(get_conn):
    other = str(uuid.uuid4())
    _apply(get_conn, _group())
    _apply(get_conn, _group(gid=other))
    assert _rows(get_conn, "SELECT COUNT(*) AS n FROM modifier_groups")[0]["n"] == 2
    _apply(get_conn, _group(gid=other))   # replay
    flagged = [c for c in _conflicts(get_conn, "modifier_group")
               if c["payload"].get("reason") == "modifier_group_same_name"]
    assert len(flagged) == 1


# ── 7. attachment natural-key clash (D5) ────────────────────────────────────

def _links(get_conn):
    return [r["id"] for r in _rows(
        get_conn, "SELECT id FROM product_modifier_groups WHERE product_id=? AND group_id=?",
        (PRODUCT, GROUP))]


def test_link_create_and_detach(get_conn):
    assert _apply(get_conn, _link()) is True
    assert _links(get_conn) == [LINK]
    _apply(get_conn, _link("update", row_version=2, updated_at_utc=T2,
                           deleted_at_utc=T2, changed=["deleted_at_utc"]))
    row = _rows(get_conn, "SELECT * FROM product_modifier_groups WHERE id=?", (LINK,))[0]
    assert row["deleted_at_utc"] == T2 and row["row_version"] == 2


@pytest.mark.parametrize("order", ["low_first", "high_first"])
def test_two_attachment_ids_for_one_product_and_group_converge_on_the_lower(get_conn, order):
    low, high = sorted([str(uuid.uuid4()), str(uuid.uuid4())])
    evs = [_link(lid=low), _link(lid=high)]
    if order == "high_first":
        evs.reverse()
    for ev in evs:
        assert _apply(get_conn, ev) is True      # never raises, never parks
    assert _links(get_conn) == [low]
    assert _parked(get_conn) == []
    clash = [c for c in _conflicts(get_conn, "product_modifier_group")
             if c["payload"].get("reason") == "modifier_link_duplicate"]
    assert len(clash) == 1, "recorded once per (product, group)"


def test_a_later_edit_to_the_losing_attachment_id_is_ignored(get_conn):
    low, high = sorted([str(uuid.uuid4()), str(uuid.uuid4())])
    _apply(get_conn, _link(lid=high))
    _apply(get_conn, _link(lid=low))
    _apply(get_conn, _link("update", lid=high, row_version=5, updated_at_utc=T3,
                           deleted_at_utc=T3, changed=["deleted_at_utc"]))
    rows = _rows(get_conn, "SELECT id, deleted_at_utc FROM product_modifier_groups")
    assert rows == [{"id": low, "deleted_at_utc": None}]


# ── 8. sale line snapshots (D8) ─────────────────────────────────────────────

SALE_UID = str(uuid.uuid4())
ITEM_UID = str(uuid.uuid4())


def _sale_item(**over):
    p = {"uid": ITEM_UID, "sale_uid": SALE_UID, "product_id": PRODUCT, "quantity": 2,
         "unit_price": 5.0, "discount_pct": 0, "tax_rate": 0, "line_total": 10.0,
         "unit_cost": 1.0}
    p.update(over)
    return {"entity_type": "sale_item", "event_type": "create", "entity_id": ITEM_UID, "payload": p}


def _mod_snap(uid=None, **over):
    m = {"uid": uid or str(uuid.uuid4()), "modifier_option_id": OPTION,
         "group_name": "Extras", "name": "Cheese", "price_delta": 0.5, "cost_delta": 0.2}
    m.update(over)
    return m


def _make_sale(get_conn):
    _exec(get_conn, "INSERT INTO sales (company_id, uid, sale_number, total) VALUES (?,?,?,?)",
          (CID, SALE_UID, "S-" + SALE_UID[:6], 10))


def test_sale_item_with_extras_and_a_promotion_lands_both_snapshots_once(get_conn):
    _make_sale(get_conn)
    _apply(get_conn, _promo())
    m1, m2 = _mod_snap(), _mod_snap(name="Bacon", price_delta=1.0)
    promo_snap = {"uid": str(uuid.uuid4()), "promotion_uid": PROMO_UID,
                  "name": "10% off Widget", "discount_pct": 10.0, "discount_amount": 1.0}
    ev = _sale_item(modifiers=[m1, m2], promotion=promo_snap)
    assert _apply(get_conn, ev) is True
    assert _apply(get_conn, ev) is True      # replay: nothing doubles
    item_id = _rows(get_conn, "SELECT id FROM sale_items WHERE uid=?", (ITEM_UID,))[0]["id"]
    mods = _rows(get_conn, "SELECT * FROM sale_item_modifiers WHERE sale_item_id=? ORDER BY name_snapshot",
                 (item_id,))
    assert [(m["name_snapshot"], m["group_name_snapshot"], m["price_delta_snapshot"],
             m["cost_delta_snapshot"], m["modifier_option_id"], m["company_id"]) for m in mods] == [
        ("Bacon", "Extras", 1.0, 0.2, OPTION, CID),
        ("Cheese", "Extras", 0.5, 0.2, OPTION, CID)]
    promos = _rows(get_conn, "SELECT * FROM sale_item_promotions WHERE sale_item_id=?", (item_id,))
    assert len(promos) == 1
    p = promos[0]
    local_promo = _promo_row(get_conn)["id"]
    assert (p["uid"], p["promotion_id"], p["name_snapshot"], p["discount_pct_snapshot"],
            p["discount_amount_snapshot"], p["company_id"]) == (
        promo_snap["uid"], local_promo, "10% off Widget", 10.0, 1.0, CID)


def test_sale_item_promotion_snapshot_applies_even_when_the_promotion_has_not_arrived(get_conn):
    """The snapshot is what matters (money is baked into the line); the local
    promotion id is provenance only, 0 when the promotion is not here yet."""
    _make_sale(get_conn)
    promo_snap = {"uid": str(uuid.uuid4()), "promotion_uid": str(uuid.uuid4()),
                  "name": "Gone", "discount_pct": 5.0, "discount_amount": 0.5}
    assert _apply(get_conn, _sale_item(promotion=promo_snap)) is True
    p = _rows(get_conn, "SELECT * FROM sale_item_promotions")[0]
    assert (p["promotion_id"], p["name_snapshot"]) == (0, "Gone")
    assert _parked(get_conn) == []


def test_legacy_sale_item_payload_without_the_snapshot_keys_still_applies(get_conn):
    _make_sale(get_conn)
    assert _apply(get_conn, _sale_item()) is True
    assert len(_rows(get_conn, "SELECT id FROM sale_items WHERE uid=?", (ITEM_UID,))) == 1
    assert _rows(get_conn, "SELECT COUNT(*) AS n FROM sale_item_modifiers")[0]["n"] == 0
    assert _rows(get_conn, "SELECT COUNT(*) AS n FROM sale_item_promotions")[0]["n"] == 0


def test_malformed_snapshot_entries_are_skipped_not_fatal(get_conn):
    _make_sale(get_conn)
    ev = _sale_item(modifiers=["junk", {"name": "no uid"}, None, _mod_snap()], promotion="junk")
    assert _apply(get_conn, ev) is True
    assert _rows(get_conn, "SELECT COUNT(*) AS n FROM sale_item_modifiers")[0]["n"] == 1
    assert _rows(get_conn, "SELECT COUNT(*) AS n FROM sale_item_promotions")[0]["n"] == 0
    assert len(_rows(get_conn, "SELECT id FROM sale_items WHERE uid=?", (ITEM_UID,))) == 1


def test_snapshots_apply_when_the_batch_has_no_company_id(get_conn):
    """`sale_item` is in `_ENTITY_TYPES_WITHOUT_COMPANY_ID`, so a batch of only
    sale lines never resolves `local_company_id`. The snapshot rows' NOT NULL
    company_id must then come from the parent sale, not from the caller."""
    _make_sale(get_conn)
    conn = get_conn()
    try:
        _svc(get_conn)._apply_event(conn, _sale_item(modifiers=[_mod_snap()]), local_company_id=None)
        conn.commit()
    finally:
        conn.close()
    assert _rows(get_conn, "SELECT company_id FROM sale_item_modifiers")[0]["company_id"] == CID
