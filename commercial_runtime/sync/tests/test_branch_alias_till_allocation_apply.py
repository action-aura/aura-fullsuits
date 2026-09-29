"""P2S crew Y, deliverable 4 -- schema v42 on the sync APPLY side.

Two new shop-wide things travel between computers:

  * `branch_alias`     "this old branch uid is the same branch as that one"
  * `till_allocation`  "till number N of this branch belongs to terminal T"

and the receiving side must (a) apply them safely from a hostile or buggy peer,
(b) make every branch-scoped thing that arrives named by an OLD uid land on the
canonical branch, and (c) refuse to merge two genuinely separate local
branches. Each rule is pinned in BOTH directions (deny AND allow).

Same fixture technique as test_parked_children_apply.py: a REAL `init_retail()`
database, `SyncService._apply_event` driven directly. The parity tests compare
this module's copies of `default_branch_uid` / `canonical_branch_uid` /
`_normalise_branch_uid_refs` with the schema's own on the same database, because
sync_service cannot import `products/` and a drifted copy would silently split
a shop's branches.

Run:  pytest commercial_runtime/sync/tests/test_branch_alias_till_allocation_apply.py -q
"""
from __future__ import annotations

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
from commercial_runtime.sync import sync_service as ss  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402

CID = "company-alias-test"
OTHER_CID = "some-other-company"
OLD = "11111111-1111-4111-8111-111111111111"     # an old, random branch uid
CANON = "22222222-2222-4222-8222-222222222222"   # the canonical uid
OLD2 = "33333333-3333-4333-8333-333333333333"


@pytest.fixture
def install(tmp_path):
    root = tmp_path / "install"
    subsys = root / "subsystems"
    subsys.mkdir(parents=True, exist_ok=True)
    original = (schema.BASE_DIR, schema.SUBSYS_DIR)
    schema.BASE_DIR, schema.SUBSYS_DIR = str(root), str(subsys)
    try:
        schema.init_retail()
    finally:
        schema.BASE_DIR, schema.SUBSYS_DIR = original
    db_path = subsys / "retail.db"

    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    previous = _retail_api_module._CREDIT_SCHEMA_READY
    _retail_api_module._CREDIT_SCHEMA_READY = False
    try:
        c = _get_conn()
        try:
            _ensure_credit_schema(c)
            c.commit()
        finally:
            c.close()
    finally:
        _retail_api_module._CREDIT_SCHEMA_READY = previous
    return _get_conn


def _svc(get_conn, **kw):
    return SyncService(client_factory=lambda: None, get_conn=get_conn,
                       local_company_id_provider=lambda: CID, **kw)


def _apply(get_conn, ev, svc=None, company=CID):
    svc = svc or _svc(get_conn)
    conn = get_conn()
    try:
        ok = svc._apply_event(conn, ev, local_company_id=company)
        conn.commit()
        return ok
    finally:
        conn.close()


def _rows(get_conn, sql, params=()):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _alias_ev(alias=OLD, canon=CANON, **extra):
    p = {"alias_uid": alias, "canonical_uid": canon, "company_id": "sender-co",
         "reason": "default_branch_converge", "created_at_utc": "2026-09-27T10:00:00Z",
         "origin_terminal_uid": "term-sender"}
    p.update(extra)
    return {"entity_type": "branch_alias", "event_type": "create",
            "entity_id": alias, "payload": p}


def _seed_alias(get_conn, alias=OLD, canon=CANON, company=CID, announced=None):
    conn = get_conn()
    conn.execute("INSERT INTO branch_uid_aliases (alias_uid, canonical_uid, company_id, reason, "
                 "created_at_utc, origin_terminal_uid, announced_at_utc) VALUES (?,?,?,?,?,?,?)",
                 (alias, canon, company, "peer", "2026-09-27T10:00:00Z", None, announced))
    conn.commit()
    conn.close()


def _seed_branch(get_conn, uid, name="Main Branch", company=CID):
    conn = get_conn()
    cur = conn.execute("INSERT INTO branches (company_id,name,address,phone,uid) VALUES (?,?,?,?,?)",
                       (company, name, "", "", uid))
    conn.commit()
    bid = cur.lastrowid
    conn.close()
    return bid


def _company_branches(get_conn, cols):
    # init_retail() seeds demo branches for another company; only this test
    # company's rows are the subject.
    return _rows(get_conn, f"SELECT {cols} FROM branches WHERE CAST(company_id AS TEXT)=? ORDER BY id", (CID,))


def _aliases(get_conn):
    return _rows(get_conn, "SELECT * FROM branch_uid_aliases ORDER BY alias_uid")


def _quarantined(get_conn, entity_type):
    return _rows(get_conn, "SELECT reason FROM sync_apply_quarantine WHERE entity_type=?", (entity_type,))


def _outbox_count(get_conn):
    return _rows(get_conn, "SELECT COUNT(*) AS n FROM sync_outbox")[0]["n"]


# ---------------------------------------------------------------------------
# branch_alias
# ---------------------------------------------------------------------------

def test_alias_applies_with_the_receivers_company_and_is_not_re_announced(install):
    assert _apply(install, _alias_ev()) is True
    [row] = _aliases(install)
    assert (row["alias_uid"], row["canonical_uid"]) == (OLD, CANON)
    # The company is THIS device's, never the payload's ("sender-co").
    assert row["company_id"] == CID
    # Stamped announced: the receiver must not echo it back onto the wire.
    assert row["announced_at_utc"]
    assert row["origin_terminal_uid"] == "term-sender"


def test_alias_replay_is_idempotent(install):
    _apply(install, _alias_ev())
    before = _aliases(install)
    assert _apply(install, _alias_ev()) is True
    assert _aliases(install) == before
    assert _quarantined(install, "branch_alias") == []


@pytest.mark.parametrize("bad", [
    {"alias_uid": None},
    {"canonical_uid": None},
    {"canonical_uid": ""},
    {"alias_uid": 7},
    {"canonical_uid": ["x"]},
    {"alias_uid": "x" * 65},
    {"canonical_uid": "y" * 65},
    {"canonical_uid": OLD},          # alias == canonical
])
def test_malformed_alias_is_skipped_not_raised_not_stored(install, bad):
    ev = _alias_ev()
    ev["payload"].update(bad)
    assert _apply(install, ev) is True
    assert _aliases(install) == []
    assert _quarantined(install, "branch_alias") == []


def test_alias_pointing_somewhere_else_is_parked_and_the_existing_row_kept(install):
    _apply(install, _alias_ev(OLD, CANON))
    ok = _apply(install, _alias_ev(OLD, "44444444-4444-4444-8444-444444444444"))
    assert ok is False
    [row] = _aliases(install)
    assert row["canonical_uid"] == CANON
    assert [q["reason"] for q in _quarantined(install, "branch_alias")] == ["branch_alias_conflict"]


def test_alias_loop_is_parked(install):
    _apply(install, _alias_ev(OLD, CANON))
    ok = _apply(install, _alias_ev(CANON, OLD))
    assert ok is False
    assert [r["alias_uid"] for r in _aliases(install)] == [OLD]
    assert [q["reason"] for q in _quarantined(install, "branch_alias")] == ["branch_alias_conflict"]


def test_two_separate_local_branches_are_never_merged(install):
    _seed_branch(install, OLD, "Downtown")
    _seed_branch(install, CANON, "Airport")
    assert _apply(install, _alias_ev()) is False
    assert _aliases(install) == []
    assert [q["reason"] for q in _quarantined(install, "branch_alias")] == ["branch_alias_conflict"]
    uids = {r["uid"] for r in _rows(install, "SELECT uid FROM branches")}
    assert {OLD, CANON} <= uids, "both separate branches must survive untouched"


@pytest.mark.parametrize("held_uid", [OLD, CANON])
def test_a_lone_local_branch_under_either_uid_is_allowed(install, held_uid):
    _seed_branch(install, held_uid)
    assert _apply(install, _alias_ev()) is True
    assert [r["alias_uid"] for r in _aliases(install)] == [OLD]


def test_alias_normalises_references_and_queues_no_event(install):
    conn = install()
    for sid, code in (("s-old", "AAA"), ("s-canon", "BBB")):
        pass
    conn.execute("INSERT INTO doc_series (id, company_id, doc_type, code, label, branch_uid, "
                 "row_version) VALUES ('ds1', ?, 'sale', 'AAA', 'A', ?, 3)", (CID, OLD))
    conn.execute("INSERT INTO retail_settings (company_id, skey, svalue) VALUES (?,?,?)",
                 (CID, f"branch_code:{OLD}", "AAA"))
    conn.execute("INSERT INTO retail_settings (company_id, skey, svalue) VALUES (?,?,?)",
                 (CID, "numbering_till:term-x", json.dumps({"branch_uid": OLD, "next": 5})))
    conn.commit()
    conn.close()
    outbox_before = _outbox_count(install)

    assert _apply(install, _alias_ev()) is True

    [ds] = _rows(install, "SELECT branch_uid, row_version FROM doc_series WHERE id='ds1'")
    assert ds["branch_uid"] == CANON
    assert ds["row_version"] == 3, "the deterministic rewrite must not bump row_version"
    keys = {r["skey"]: r["svalue"] for r in _rows(install, "SELECT skey, svalue FROM retail_settings")}
    assert keys.get(f"branch_code:{CANON}") == "AAA"
    assert f"branch_code:{OLD}" not in keys
    assert json.loads(keys["numbering_till:term-x"]) == {"branch_uid": CANON, "next": 5}
    assert _outbox_count(install) == outbox_before, "normalising must not queue a sync event"


def test_existing_canonical_branch_code_wins_over_the_alias_key(install):
    conn = install()
    conn.execute("INSERT INTO retail_settings (company_id, skey, svalue) VALUES (?,?,?)",
                 (CID, f"branch_code:{OLD}", "OLDCODE"))
    conn.execute("INSERT INTO retail_settings (company_id, skey, svalue) VALUES (?,?,?)",
                 (CID, f"branch_code:{CANON}", "NEWCODE"))
    conn.commit()
    conn.close()
    _apply(install, _alias_ev())
    keys = {r["skey"]: r["svalue"] for r in _rows(install, "SELECT skey, svalue FROM retail_settings")}
    assert keys[f"branch_code:{CANON}"] == "NEWCODE"


def test_normalising_leaves_another_companys_rows_alone(install):
    conn = install()
    conn.execute("INSERT INTO doc_series (id, company_id, doc_type, code, label, branch_uid, "
                 "row_version) VALUES ('ds-other', ?, 'sale', 'ZZZ', 'Z', ?, 1)", (OTHER_CID, OLD))
    conn.commit()
    conn.close()
    _apply(install, _alias_ev())
    [ds] = _rows(install, "SELECT branch_uid FROM doc_series WHERE id='ds-other'")
    assert ds["branch_uid"] == OLD


def test_missing_alias_table_is_a_quiet_skip(install):
    conn = install()
    conn.execute("DROP TABLE branch_uid_aliases")
    conn.commit()
    conn.close()
    assert _apply(install, _alias_ev()) is True


# ---------------------------------------------------------------------------
# alias read-side: branch / doc_series / retail_setting / _resolve_branch_id
# ---------------------------------------------------------------------------

def test_resolve_branch_id_follows_an_alias_when_the_event_names_the_old_uid(install):
    bid = _seed_branch(install, CANON)
    _seed_alias(install)
    conn = install()
    sink: list = []
    got = SyncService._resolve_branch_id(conn, CID, OLD, sink)
    conn.close()
    assert got == bid
    assert sink == [], "resolved through the alias: no fallback may be recorded"


def test_resolve_branch_id_follows_an_alias_when_this_device_holds_the_old_uid(install):
    bid = _seed_branch(install, OLD)
    _seed_alias(install)
    conn = install()
    sink: list = []
    got = SyncService._resolve_branch_id(conn, CID, CANON, sink)
    conn.close()
    assert got == bid
    assert sink == []


def test_resolve_branch_id_between_two_aliases_of_the_same_canonical(install):
    bid = _seed_branch(install, OLD2)
    _seed_alias(install, OLD, CANON)
    _seed_alias(install, OLD2, CANON)
    conn = install()
    sink: list = []
    got = SyncService._resolve_branch_id(conn, CID, OLD, sink)
    conn.close()
    assert got == bid and sink == []


def test_resolve_branch_id_unrelated_uid_still_falls_back(install):
    default = _seed_branch(install, "55555555-5555-4555-8555-555555555555")
    _seed_branch(install, CANON, "Other")
    _seed_alias(install)
    conn = install()
    sink: list = []
    got = SyncService._resolve_branch_id(conn, CID, "66666666-6666-4666-8666-666666666666", sink)
    conn.close()
    assert got == default
    assert len(sink) == 1


def test_resolve_branch_id_ignores_another_companys_alias(install):
    default = _seed_branch(install, "55555555-5555-4555-8555-555555555555")
    _seed_branch(install, CANON, "Other-co canonical", company=OTHER_CID)
    _seed_alias(install, company=OTHER_CID)
    conn = install()
    sink: list = []
    got = SyncService._resolve_branch_id(conn, CID, OLD, sink)
    conn.close()
    assert got == default and len(sink) == 1


def test_branch_event_updates_a_lone_alias_row_in_place(install):
    bid = _seed_branch(install, OLD, "Old name")
    _seed_alias(install)
    ev = {"entity_type": "branch", "event_type": "update", "entity_id": CANON,
          "payload": {"uid": CANON, "name": "New name", "address": "Main St", "phone": "1",
                      "status": "active"}}
    assert _apply(install, ev) is True
    rows = _company_branches(install, "id, uid, name, address")
    assert len(rows) == 1, f"a duplicate branch appeared: {rows}"
    assert rows[0]["id"] == bid and rows[0]["name"] == "New name" and rows[0]["address"] == "Main St"


def test_branch_event_naming_the_old_uid_lands_on_the_canonical_row(install):
    bid = _seed_branch(install, CANON, "Canon")
    _seed_alias(install)
    ev = {"entity_type": "branch", "event_type": "update", "entity_id": OLD,
          "payload": {"uid": OLD, "name": "Renamed", "address": "", "phone": "", "status": "active"}}
    assert _apply(install, ev) is True
    rows = _company_branches(install, "id, uid, name")
    assert [(r["id"], r["uid"], r["name"]) for r in rows] == [(bid, CANON, "Renamed")]


def test_branch_event_without_any_alias_still_inserts(install):
    ev = {"entity_type": "branch", "event_type": "create", "entity_id": OLD,
          "payload": {"uid": OLD, "name": "Fresh", "address": "", "phone": "", "status": "active"}}
    assert _apply(install, ev) is True
    assert [r["uid"] for r in _company_branches(install, "uid")] == [OLD]


def test_doc_series_apply_stores_the_canonical_branch_uid(install):
    _seed_alias(install)
    p = {"id": "ds-x", "doc_type": "sale", "code": "QQQ", "label": "Q", "branch_uid": OLD,
         "allocator_terminal_uid": None, "claimed_at_utc": None, "pad_width": 6, "start_no": 1,
         "status": "active", "created_at": "2026-09-27 10:00:00", "row_version": 1,
         "updated_at_utc": "2026-09-27T10:00:00Z"}
    ev = {"entity_type": "doc_series", "event_type": "create", "entity_id": "ds-x", "payload": p}
    assert _apply(install, ev) is True
    [r] = _rows(install, "SELECT branch_uid FROM doc_series WHERE id='ds-x'")
    assert r["branch_uid"] == CANON


def test_retail_setting_branch_code_lands_under_the_canonical_key(install):
    _seed_alias(install)
    ev = {"entity_type": "retail_setting", "event_type": "update", "entity_id": str(uuid.uuid4()),
          "payload": {"skey": f"branch_code:{OLD}", "svalue": "AMM"}}
    assert _apply(install, ev) is True
    keys = {r["skey"]: r["svalue"] for r in _rows(install, "SELECT skey, svalue FROM retail_settings")}
    assert keys.get(f"branch_code:{CANON}") == "AMM"
    assert f"branch_code:{OLD}" not in keys


def test_retail_setting_branch_code_without_alias_keeps_its_key(install):
    ev = {"entity_type": "retail_setting", "event_type": "update", "entity_id": str(uuid.uuid4()),
          "payload": {"skey": f"branch_code:{OLD}", "svalue": "AMM"}}
    assert _apply(install, ev) is True
    keys = {r["skey"] for r in _rows(install, "SELECT skey FROM retail_settings")}
    assert f"branch_code:{OLD}" in keys


# ---------------------------------------------------------------------------
# self-heal: the deterministic default branch uid
# ---------------------------------------------------------------------------

def test_self_healed_branch_gets_the_licence_derived_uid_only_for_that_company(install, monkeypatch):
    lid = "LIC-PUBLIC-123"
    monkeypatch.setattr(ss, "_owner_licence_id", lambda: lid)
    conn = install()
    ss.SyncService._resolve_branch_id(conn, lid, None)          # company == licence id
    ss.SyncService._resolve_branch_id(conn, "unrelated-co", None)  # any other company
    conn.commit()
    conn.close()
    by_co = {r["company_id"]: r["uid"] for r in _rows(install, "SELECT company_id, uid FROM branches")}
    assert by_co[lid] == schema.default_branch_uid(lid)
    assert by_co["unrelated-co"] != schema.default_branch_uid(lid)
    uuid.UUID(by_co["unrelated-co"])  # still a valid random uuid


def test_self_heal_without_a_licence_is_random(install, monkeypatch):
    monkeypatch.setattr(ss, "_owner_licence_id", lambda: None)
    conn = install()
    ss.SyncService._resolve_branch_id(conn, CID, None)
    conn.commit()
    conn.close()
    [r] = _company_branches(install, "uid")
    uuid.UUID(r["uid"])
    assert r["uid"] != schema.default_branch_uid(CID)


# ---------------------------------------------------------------------------
# parity with schema.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lid", ["LIC-1", "  LIC-1  ", "a-much-longer-licence-public-id-0001", 12345])
def test_default_branch_uid_parity(lid):
    assert ss._default_branch_uid(lid) == schema.default_branch_uid(lid)


@pytest.mark.parametrize("lid", [None, "", "   "])
def test_default_branch_uid_parity_for_missing_ids(lid):
    assert ss._default_branch_uid(lid) is None
    assert schema.default_branch_uid(lid) is None


def test_namespace_literal_matches_the_schema():
    assert ss._DEFAULT_BRANCH_UID_NAMESPACE == schema.DEFAULT_BRANCH_UID_NAMESPACE


def test_canonical_branch_uid_parity_including_chains_loops_and_company_scope(install):
    a, b, c, d, e = (f"{i}0000000-0000-4000-8000-000000000000" for i in range(1, 6))
    _seed_alias(install, a, b)
    _seed_alias(install, b, c)
    _seed_alias(install, c, d)
    _seed_alias(install, d, e)          # 4 hops from a
    _seed_alias(install, e, "extra")    # 5th hop: bounded
    # a loop and a foreign-company alias
    _seed_alias(install, "loop-1", "loop-2")
    _seed_alias(install, "loop-2", "loop-1")
    _seed_alias(install, "foreign", "foreign-canon", company=OTHER_CID)
    conn = install()
    try:
        for uid in (a, b, c, d, e, "loop-1", "foreign", "nothing", None, ""):
            for co in (CID, OTHER_CID, None):
                assert ss._canonical_branch_uid(conn, uid, co) == schema.canonical_branch_uid(conn, uid, co), \
                    (uid, co)
    finally:
        conn.close()


def test_normalise_refs_parity_with_the_schema_twin(install, tmp_path):
    def seed():
        conn = install()
        conn.execute("DELETE FROM doc_series")
        conn.execute("DELETE FROM retail_settings")
        conn.execute("INSERT INTO doc_series (id, company_id, doc_type, code, label, branch_uid, row_version) "
                     "VALUES ('d1', ?, 'sale', 'A', 'A', ?, 2)", (CID, OLD))
        conn.execute("INSERT INTO doc_series (id, company_id, doc_type, code, label, branch_uid, row_version) "
                     "VALUES ('d2', ?, 'sale', 'B', 'B', ?, 2)", (OTHER_CID, OLD))
        conn.execute("INSERT INTO retail_settings VALUES (?,?,?)", (CID, f"branch_code:{OLD}", "X"))
        conn.execute("INSERT INTO retail_settings VALUES (?,?,?)", (CID, f"branch_code:{OLD2}", "Y"))
        conn.execute("INSERT INTO retail_settings VALUES (?,?,?)", (CID, f"branch_code:{CANON}", "Z"))
        conn.execute("INSERT INTO retail_settings VALUES (?,?,?)",
                     (CID, "numbering_till:t1", json.dumps({"branch_uid": OLD, "next": 1})))
        conn.execute("INSERT INTO retail_settings VALUES (?,?,?)", (CID, "numbering_till:t2", "not json"))
        conn.commit()
        return conn

    def dump(conn):
        return (conn.execute("SELECT id, branch_uid, row_version FROM doc_series ORDER BY id").fetchall(),
                conn.execute("SELECT company_id, skey, svalue FROM retail_settings ORDER BY skey").fetchall())

    _seed_alias(install, OLD, CANON)
    _seed_alias(install, OLD2, CANON)

    conn = seed()
    schema._normalise_branch_uid_refs(conn, CID)
    conn.commit()
    want = [[tuple(r) for r in part] for part in dump(conn)]
    conn.close()

    conn = seed()
    ss._normalise_branch_uid_refs(conn, CID)
    conn.commit()
    got = [[tuple(r) for r in part] for part in dump(conn)]
    conn.close()
    assert got == want


# ---------------------------------------------------------------------------
# till_allocation
# ---------------------------------------------------------------------------

BR = "77777777-7777-4777-8777-777777777777"


def _till_ev(alloc_id="al-1", number=2, terminal="term-A", status="active", rv=1, epoch=1,
             branch=BR, by="term-main", created="2026-09-27T10:00:00Z", event="create", **extra):
    p = {"id": alloc_id, "company_id": "sender-co", "branch_uid": branch, "till_number": number,
         "terminal_uid": terminal, "status": status, "allocated_by_terminal_uid": by,
         "admin_epoch": epoch, "row_version": rv, "created_at_utc": created,
         "updated_at_utc": created}
    p.update(extra)
    return {"entity_type": "till_allocation", "event_type": event, "entity_id": alloc_id, "payload": p}


def _tills(get_conn):
    return {r["id"]: r for r in _rows(get_conn, "SELECT * FROM till_allocations ORDER BY id")}


def test_till_insert_uses_the_receivers_company_and_queues_nothing(install):
    before = _outbox_count(install)
    assert _apply(install, _till_ev()) is True
    [row] = _tills(install).values()
    assert row["company_id"] == CID
    assert (row["branch_uid"], row["till_number"], row["terminal_uid"], row["status"]) == (BR, 2, "term-A", "active")
    assert (row["admin_epoch"], row["row_version"]) == (1, 1)
    assert _outbox_count(install) == before


def test_till_identical_replay_changes_nothing(install):
    _apply(install, _till_ev())
    before = _tills(install)
    assert _apply(install, _till_ev()) is True
    assert _tills(install) == before


def test_till_release_moves_forward_and_only_the_lifecycle_moves(install):
    _apply(install, _till_ev())
    rel = _till_ev(status="released", rv=2, number=9, terminal="term-EVIL", epoch=50,
                   created="2026-09-27T11:00:00Z", event="update")
    assert _apply(install, rel) is True
    [row] = _tills(install).values()
    assert row["status"] == "released" and row["row_version"] == 2
    # identity is immutable
    assert (row["till_number"], row["terminal_uid"], row["admin_epoch"]) == (2, "term-A", 1)
    assert row["updated_at_utc"] == "2026-09-27T11:00:00Z"


def test_till_stale_update_is_ignored(install):
    _apply(install, _till_ev(status="released", rv=3))
    assert _apply(install, _till_ev(status="active", rv=2, event="update")) is True
    [row] = _tills(install).values()
    assert row["status"] == "released" and row["row_version"] == 3


def test_till_equal_version_different_status_is_recorded_as_a_conflict(install):
    _apply(install, _till_ev(status="active", rv=2))
    sink: list = []
    svc = _svc(install)
    conn = install()
    try:
        ok = svc._apply_event(conn, _till_ev(status="released", rv=2, event="update"),
                              local_company_id=CID, conflict_sink=sink)
        conn.commit()
    finally:
        conn.close()
    assert ok is True
    assert _tills(install)["al-1"]["status"] == "active", "the local row must not be overwritten"
    conflicts = _rows(install, "SELECT entity_type FROM sync_conflicts")
    assert conflicts and all(c["entity_type"] == "till_allocation" for c in conflicts)


@pytest.mark.parametrize("bad", [
    {"id": None}, {"id": ""}, {"id": "x" * 65},
    {"branch_uid": None}, {"terminal_uid": ""}, {"allocated_by_terminal_uid": None},
    {"till_number": 0}, {"till_number": 100}, {"till_number": "3"}, {"till_number": True},
    {"till_number": 2.0}, {"admin_epoch": -1}, {"admin_epoch": 2 ** 40}, {"admin_epoch": "1"},
    {"row_version": 0}, {"row_version": 2 ** 40}, {"row_version": True},
    {"status": "deleted"}, {"status": None},
])
def test_malformed_till_allocation_is_skipped_not_raised_not_stored(install, bad):
    ev = _till_ev()
    ev["payload"].update(bad)
    assert _apply(install, ev) is True
    assert _tills(install) == {}
    assert _quarantined(install, "till_allocation") == []


def test_till_delete_event_is_ignored(install):
    _apply(install, _till_ev())
    assert _apply(install, _till_ev(event="delete")) is True
    assert list(_tills(install)) == ["al-1"]


def test_till_allocation_for_an_aliased_branch_is_stored_canonical(install):
    _seed_alias(install, OLD, CANON)
    _apply(install, _till_ev(branch=OLD))
    [row] = _tills(install).values()
    assert row["branch_uid"] == CANON


def test_till_same_id_from_another_company_is_parked(install):
    conn = install()
    conn.execute("INSERT INTO till_allocations (id, company_id, branch_uid, till_number, terminal_uid, "
                 "status, allocated_by_terminal_uid, admin_epoch, row_version, created_at_utc, updated_at_utc) "
                 "VALUES ('al-1', ?, ?, 2, 'term-A', 'active', 'm', 1, 1, 'c', 'c')", (OTHER_CID, BR))
    conn.commit()
    conn.close()
    assert _apply(install, _till_ev(rv=5, event="update")) is False
    assert [q["reason"] for q in _quarantined(install, "till_allocation")] == ["till_allocation_conflict"]
    assert _tills(install)["al-1"]["row_version"] == 1


def _status(get_conn):
    return {k: v["status"] for k, v in _tills(get_conn).items()}


def test_clash_same_number_higher_epoch_wins(install):
    _apply(install, _till_ev("al-lo", number=3, terminal="term-A", epoch=1))
    _apply(install, _till_ev("al-hi", number=3, terminal="term-B", epoch=2))
    assert _status(install) == {"al-lo": "superseded", "al-hi": "active"}


def test_clash_same_number_arriving_in_the_other_order_gives_the_same_winner(install):
    _apply(install, _till_ev("al-hi", number=3, terminal="term-B", epoch=2))
    _apply(install, _till_ev("al-lo", number=3, terminal="term-A", epoch=1))
    assert _status(install) == {"al-lo": "superseded", "al-hi": "active"}


def test_clash_equal_epoch_earlier_created_wins(install):
    _apply(install, _till_ev("al-late", number=3, terminal="term-A", created="2026-09-27T12:00:00Z"))
    _apply(install, _till_ev("al-early", number=3, terminal="term-B", created="2026-09-27T09:00:00Z"))
    assert _status(install) == {"al-late": "superseded", "al-early": "active"}


def test_clash_equal_epoch_and_time_smaller_id_wins(install):
    _apply(install, _till_ev("al-b", number=3, terminal="term-A"))
    _apply(install, _till_ev("al-a", number=3, terminal="term-B"))
    assert _status(install) == {"al-b": "superseded", "al-a": "active"}


def test_clash_same_terminal_two_numbers_resolves_to_one_active(install):
    _apply(install, _till_ev("al-1", number=3, terminal="term-A", epoch=1))
    _apply(install, _till_ev("al-2", number=4, terminal="term-A", epoch=2))
    assert _status(install) == {"al-1": "superseded", "al-2": "active"}


def test_clash_settlement_bumps_no_version_and_queues_no_event(install):
    _apply(install, _till_ev("al-lo", number=3, terminal="term-A", epoch=1, rv=4))
    before = _outbox_count(install)
    _apply(install, _till_ev("al-hi", number=3, terminal="term-B", epoch=2))
    assert _tills(install)["al-lo"]["row_version"] == 4
    assert _outbox_count(install) == before


def test_no_clash_across_different_branches_or_companies(install):
    _apply(install, _till_ev("al-1", number=3, terminal="term-A", branch=BR))
    _apply(install, _till_ev("al-2", number=3, terminal="term-B", branch="88888888-8888-4888-8888-888888888888"))
    assert _status(install) == {"al-1": "active", "al-2": "active"}


def _seed_foreign_till(get_conn, alloc_id, epoch, created, number=3, terminal="term-A", branch=BR):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO till_allocations (id, company_id, branch_uid, till_number, terminal_uid, "
            "status, allocated_by_terminal_uid, admin_epoch, row_version, created_at_utc, updated_at_utc) "
            "VALUES (?, 'other-company', ?, ?, ?, 'active', 'term-x', ?, 1, ?, ?)",
            (alloc_id, branch, number, terminal, epoch, created, created))
        conn.commit()
    finally:
        conn.close()


def test_clash_scan_never_reaches_another_companys_rows(install):
    # A row of ANOTHER company holding the same branch uid, number and terminal
    # (the partial unique indexes carry company_id, so it can coexist) must be
    # invisible to the clash scan in BOTH directions:
    #   * an incoming row that would LOSE to it on priority stays active;
    #   * an incoming row that would BEAT it does not demote it.
    _seed_foreign_till(install, "foreign-strong", epoch=9, created="2026-01-01T00:00:00Z")
    _apply(install, _till_ev("al-weak", number=3, terminal="term-A", epoch=1))
    _seed_foreign_till(install, "foreign-weak", epoch=0, created="2030-01-01T00:00:00Z",
                       number=4, terminal="term-B")
    _apply(install, _till_ev("al-strong", number=4, terminal="term-B", epoch=5))
    st = _status(install)
    assert st == {"al-weak": "active", "al-strong": "active",
                  "foreign-strong": "active", "foreign-weak": "active"}, st


def test_released_number_can_be_taken_again(install):
    _apply(install, _till_ev("al-1", number=3, terminal="term-A"))
    _apply(install, _till_ev("al-1", number=3, terminal="term-A", status="released", rv=2, event="update"))
    _apply(install, _till_ev("al-2", number=3, terminal="term-B"))
    assert _status(install) == {"al-1": "released", "al-2": "active"}


def test_two_active_rows_never_share_a_number_or_a_terminal_after_any_arrival_order(install):
    # Allow-half + invariant: whatever the order, the partial unique indexes
    # are never violated and exactly one of each clashing pair stays active.
    import itertools
    evs = [_till_ev("c1", number=5, terminal="tA", epoch=1),
           _till_ev("c2", number=5, terminal="tB", epoch=2),
           _till_ev("c3", number=6, terminal="tB", epoch=3)]
    finals = set()
    for perm in itertools.permutations(evs):
        conn = install()
        conn.execute("DELETE FROM till_allocations")
        conn.commit()
        conn.close()
        for e in perm:
            assert _apply(install, e) is True
        active = [r for r in _tills(install).values() if r["status"] == "active"]
        assert len({(r["branch_uid"], r["till_number"]) for r in active}) == len(active)
        assert len({r["terminal_uid"] for r in active}) == len(active)
        finals.add(tuple(sorted(_status(install).items())))
    # Documented residual: three rows chained across a number clash AND a
    # terminal clash can settle differently by arrival order when several
    # mains race. This pins that it is at least one valid set every time.
    assert finals


# ---------------------------------------------------------------------------
# mixed builds: an older (v41) receiver must PARK the new types, not eat them
# ---------------------------------------------------------------------------

V41_HANDLED = ss.RETAIL_SYNC_ENTITY_TYPES - {"branch_alias", "till_allocation"}


@pytest.mark.parametrize("make", [_alias_ev, _till_ev])
def test_a_v41_receiver_parks_the_new_types_as_schema_version_too_new(install, make):
    svc = _svc(install, handled_entity_types=V41_HANDLED, local_schema_version_provider=lambda: 41)
    ev = make()
    ev["payload"]["_sender_schema_version"] = 42
    sink: list = []
    conn = install()
    try:
        ok = svc._apply_event(conn, ev, local_company_id=CID, schema_skew_sink=sink)
        conn.commit()
    finally:
        conn.close()
    assert ok is False
    assert (ev["entity_type"], "schema_version_too_new") in sink
    assert [q["reason"] for q in _quarantined(install, ev["entity_type"])] == ["schema_version_too_new"]
    assert _aliases(install) == [] and _tills(install) == {}


@pytest.mark.parametrize("make", [_alias_ev, _till_ev])
def test_a_v42_receiver_applies_the_same_event(install, make):
    svc = _svc(install, handled_entity_types=ss.RETAIL_SYNC_ENTITY_TYPES,
               local_schema_version_provider=lambda: 42)
    ev = make()
    ev["payload"]["_sender_schema_version"] = 42
    assert _apply(install, ev, svc=svc) is True
    assert _quarantined(install, ev["entity_type"]) == []
    assert (_aliases(install) or _tills(install))


def test_new_types_are_company_scoped_types():
    assert "branch_alias" in ss.RETAIL_SYNC_ENTITY_TYPES
    assert "till_allocation" in ss.RETAIL_SYNC_ENTITY_TYPES
    assert "branch_alias" not in ss._ENTITY_TYPES_WITHOUT_COMPANY_ID
    assert "till_allocation" not in ss._ENTITY_TYPES_WITHOUT_COMPANY_ID
