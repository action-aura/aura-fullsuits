"""AUDIT-033 / S-AUTH, D4 + D3 -- the RETAIL-stream authority events, the signing
side (`read_outbox`, both streams) and the forged-attack scenarios against
retail.db.

Fixture technique as test_branch_alias_till_allocation_apply.py: a REAL
`init_retail()` database and a REAL registry.db, `SyncService._apply_event` driven
directly, REAL Ed25519 signatures and Owner-signed leases (_signing.py).

Every deny asserts the business table did not move; every rule has its allow half.

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/sync/tests/test_sync_authority_retail.py -q
"""
from __future__ import annotations

import json
import logging
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
from commercial_runtime.sync import authority  # noqa: E402
from commercial_runtime.sync import sync_service as ss  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402
from commercial_runtime.sync.tests._signing import SigningWorld, install_main  # noqa: E402

CID = "company-retail-auth"
LID = "LIC-RETAIL-AUTH-0001"
CANON = schema.default_branch_uid(LID)
OLD = "11111111-1111-4111-8111-111111111111"
BR = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class Shop:
    __test__ = False

    def __init__(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ss, "_owner_licence_id", lambda: LID)
        reg_dir = tmp_path / "registry_db"
        monkeypatch.setattr(registry_db, "_db_dir", str(reg_dir))
        monkeypatch.setattr(registry_db, "DB_PATH", str(reg_dir / "registry.db"))
        registry_db.init_registry_db()
        root = tmp_path / "install"
        subsys = root / "subsystems"
        subsys.mkdir(parents=True, exist_ok=True)
        original = (schema.BASE_DIR, schema.SUBSYS_DIR)
        schema.BASE_DIR, schema.SUBSYS_DIR = str(root), str(subsys)
        try:
            schema.init_retail()
        finally:
            schema.BASE_DIR, schema.SUBSYS_DIR = original
        self.db_path = subsys / "retail.db"

        previous = _retail_api_module._CREDIT_SCHEMA_READY
        _retail_api_module._CREDIT_SCHEMA_READY = False
        try:
            c = self.get_conn()
            try:
                _ensure_credit_schema(c)
                c.commit()
            finally:
                c.close()
        finally:
            _retail_api_module._CREDIT_SCHEMA_READY = previous

        self.world = SigningWorld(tmp_path, licence=LID)
        self.R = self.world.new_device("dev-receiver", licence=LID)
        self.M = self.world.new_device("dev-main", licence=LID)
        self.A = self.world.new_device("dev-a", licence=LID)
        self.B = self.world.new_device("dev-b", licence=LID)
        reg = registry_db.get_conn()
        try:
            install_main(reg, CID, self.M.dev, epoch=1)
        finally:
            reg.close()
        self.svc = SyncService(client_factory=lambda: None, get_conn=self.get_conn,
                               local_company_id_provider=lambda: CID,
                               authority_env_provider=self.world.provider(self.R))

    def get_conn(self):
        c = sqlite3.connect(str(self.db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def apply(self, ev, signer=None):
        if signer is not None:
            ev = self.world.sign(ev, signer)
        conn = self.get_conn()
        try:
            handled = self.svc._apply_event(conn, ev, local_company_id=CID)
            conn.commit()
            return handled
        finally:
            conn.close()

    def pull(self, events=()):
        conn = self.get_conn()
        try:
            self.svc.apply_pull_result(conn, {"events": list(events), "cursor": 1})
            conn.commit()
        finally:
            conn.close()

    def rows(self, sql, params=()):
        conn = self.get_conn()
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def parked(self, entity_type=None):
        return [r["reason"] for r in self.rows(
            "SELECT entity_type, reason FROM sync_apply_quarantine ORDER BY rowid")
            if entity_type in (None, r["entity_type"])]

    def set_main(self, dev, epoch):
        from commercial_runtime.identity import device_registry
        reg = registry_db.get_conn()
        try:
            device_registry._write_assignment(reg, CID, dev, epoch, "2026-09-27T09:00:00Z", None, None)
            reg.commit()
        finally:
            reg.close()

    def counts(self):
        return {t: self.rows(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
                for t in ("till_allocations", "doc_series", "branch_uid_aliases", "retail_settings")}


@pytest.fixture
def shop(tmp_path, monkeypatch):
    return Shop(tmp_path, monkeypatch)


def _till_ev(alloc_id="al-1", number=2, terminal="term-A", epoch=1, by="dev-main", rv=1, status="active"):
    p = {"id": alloc_id, "company_id": "sender-co", "branch_uid": BR, "till_number": number,
         "terminal_uid": terminal, "status": status, "allocated_by_terminal_uid": by,
         "admin_epoch": epoch, "row_version": rv, "created_at_utc": "2026-09-27T10:00:00Z",
         "updated_at_utc": "2026-09-27T10:00:00Z"}
    return {"entity_type": "till_allocation", "event_type": "create", "entity_id": alloc_id, "payload": p}


def _series_ev(series_id="ds-1", code="QQQ"):
    p = {"id": series_id, "doc_type": "sale", "code": code, "label": "Q", "branch_uid": BR,
         "allocator_terminal_uid": None, "claimed_at_utc": None, "pad_width": 6, "start_no": 1,
         "status": "active", "created_at": "2026-09-27 10:00:00", "row_version": 1,
         "updated_at_utc": "2026-09-27T10:00:00Z"}
    return {"entity_type": "doc_series", "event_type": "create", "entity_id": series_id, "payload": p}


def _alias_ev(origin, alias=OLD):
    p = {"alias_uid": alias, "canonical_uid": CANON, "company_id": "sender-co",
         "reason": "default_branch_converge", "created_at_utc": "2026-09-27T10:00:00Z",
         "origin_terminal_uid": origin}
    return {"entity_type": "branch_alias", "event_type": "create", "entity_id": alias, "payload": p}


def _ack_ev(alloc_id="al-1"):
    return {"entity_type": "retail_setting", "event_type": "update", "entity_id": str(uuid.uuid4()),
            "payload": {"skey": f"till_release_ack:{alloc_id}", "svalue": "1"}}


# ═══ till_allocation ═══════════════════════════════════════════════════════

def test_the_main_may_allocate_a_till(shop):
    assert shop.apply(_till_ev(), shop.M) is True
    assert [r["id"] for r in shop.rows("SELECT id FROM till_allocations")] == ["al-1"]
    assert shop.parked() == []


def test_a_non_main_cannot_allocate_a_till_even_naming_the_main_as_author(shop):
    """What the old epoch/author check could not stop: it compared a payload field
    the forger typed. The signature says who really sent it."""
    before = shop.counts()
    assert shop.apply(_till_ev(by="dev-main"), shop.A) is False
    assert shop.parked("till_allocation") == ["needs_main_computer"]
    assert shop.counts() == before


def test_an_unsigned_till_allocation_is_parked(shop):
    before = shop.counts()
    assert shop.apply(_till_ev()) is False
    assert shop.parked("till_allocation") == ["unsigned_authority_event"]
    assert shop.counts() == before


def test_a_hand_over_allocation_parks_as_epoch_unknown_then_applies_once_the_registry_catches_up(shop):
    """The new main authors an allocation before this receiver has heard of the
    hand-over: parked for the REAL reason, applied (still signed by the new main)
    the moment the assignment arrives."""
    ev = shop.world.sign(_till_ev("new", epoch=2, by="dev-a"), shop.A)
    assert shop.apply(ev) is False
    assert shop.parked("till_allocation") == ["allocation_epoch_unknown"]
    assert shop.rows("SELECT id FROM till_allocations") == []
    shop.set_main("dev-a", 2)
    shop.pull()
    assert shop.parked() == []
    assert [r["id"] for r in shop.rows("SELECT id FROM till_allocations")] == ["new"]


def test_the_epoch_pass_through_never_applies_an_update_to_an_existing_allocation(shop):
    """An UPDATE has no author check in the applier, so a non-main member naming a
    sky-high epoch must not slip through the pass-through: the allocation row
    must stay as the main wrote it."""
    assert shop.apply(_till_ev("al-1", status="active", rv=1), shop.M) is True
    forged = _till_ev("al-1", status="released", rv=9, epoch=99, by="dev-a")
    forged["event_type"] = "update"
    assert shop.apply(forged, shop.A) is False
    assert shop.parked("till_allocation") == ["needs_main_computer"]
    assert shop.rows("SELECT status FROM till_allocations")[0]["status"] == "active"


def test_till_allocation_is_parked_main_unknown_when_no_main_is_recorded(shop):
    reg = registry_db.get_conn()
    reg.execute("DELETE FROM admin_device_assignment")
    reg.commit()
    reg.close()
    assert shop.apply(_till_ev(), shop.M) is False
    assert shop.parked("till_allocation") == ["main_unknown"]
    assert shop.rows("SELECT id FROM till_allocations") == []


def test_an_unreadable_registry_parks_instead_of_accepting(shop, monkeypatch):
    def _boom():
        raise sqlite3.OperationalError("unable to open database file")
    ev = shop.world.sign(_till_ev(), shop.M)
    monkeypatch.setattr(registry_db, "get_conn", _boom)
    assert shop.apply(ev) is False
    assert shop.parked("till_allocation") == ["main_unknown"]
    assert shop.rows("SELECT id FROM till_allocations") == []


# ═══ doc_series ════════════════════════════════════════════════════════════

def test_the_main_may_create_a_numbering_book(shop):
    assert shop.apply(_series_ev(), shop.M) is True
    assert [r["id"] for r in shop.rows("SELECT id FROM doc_series")] == ["ds-1"]


def _own_book(book_id, owner, code, **over):
    ev = _series_ev(book_id, code)
    ev["payload"].update(allocator_terminal_uid=owner, claimed_at_utc="2026-09-27T10:00:00Z")
    ev["payload"].update(over)
    return ev


def test_a_till_may_create_update_and_retire_its_own_book(shop):
    """Once the main hands a till its number, THAT TILL creates, reactivates and
    retires its own books and the events travel from it (`_apply_till_books`)."""
    assert shop.apply(_own_book("own-1", "dev-a", "AAA2"), shop.A) is True
    retire = _own_book("own-1", "dev-a", "AAA2", status="retired", row_version=2)
    retire["event_type"] = "update"
    assert shop.apply(retire, shop.A) is True
    assert shop.rows("SELECT status FROM doc_series WHERE id='own-1'")[0]["status"] == "retired"
    assert shop.parked() == []


def test_a_non_main_cannot_write_a_book_unowned_or_for_another_till(shop):
    before = shop.counts()
    assert shop.apply(_series_ev("ds-2", "ZZZ"), shop.A) is False                  # allocator None
    assert shop.apply(_own_book("ds-3", "dev-b", "YYY"), shop.A) is False           # allocator someone else
    assert shop.parked("doc_series") == ["needs_main_computer", "needs_main_computer"]
    assert shop.counts() == before


def test_a_non_main_cannot_claim_a_book_the_main_made(shop):
    assert shop.apply(_series_ev("ds-1", "QQQ"), shop.M) is True                    # unclaimed, made by the main
    steal = _own_book("ds-1", "dev-a", "QQQ", row_version=2)
    steal["event_type"] = "update"
    assert shop.apply(steal, shop.A) is False
    assert shop.parked("doc_series") == ["needs_main_computer"]
    assert shop.rows("SELECT allocator_terminal_uid FROM doc_series WHERE id='ds-1'")[0]["allocator_terminal_uid"] is None


def test_a_till_cannot_take_over_another_tills_book(shop):
    assert shop.apply(_own_book("b-book", "dev-b", "BBB1"), shop.B) is True
    steal = _own_book("b-book", "dev-a", "BBB1", row_version=9)
    steal["event_type"] = "update"
    assert shop.apply(steal, shop.A) is False
    assert shop.parked("doc_series") == ["needs_main_computer"]
    assert shop.rows("SELECT allocator_terminal_uid FROM doc_series WHERE id='b-book'")[0]["allocator_terminal_uid"] == "dev-b"


# ═══ retail_setting: till_release_ack ══════════════════════════════════════

def _with_allocation(shop, terminal):
    assert shop.apply(_till_ev("al-1", terminal=terminal), shop.M) is True


def test_the_till_being_released_may_confirm_it(shop):
    _with_allocation(shop, "dev-a")
    assert shop.apply(_ack_ev(), shop.A) is True
    assert shop.rows("SELECT svalue FROM retail_settings WHERE skey='till_release_ack:al-1'")[0]["svalue"] == "1"


def test_the_main_may_confirm_a_release(shop):
    _with_allocation(shop, "dev-a")
    assert shop.apply(_ack_ev(), shop.M) is True


def test_another_member_cannot_confirm_someone_elses_release(shop):
    _with_allocation(shop, "dev-a")
    before = shop.counts()
    assert shop.apply(_ack_ev(), shop.B) is False
    assert shop.parked("retail_setting") == ["release_ack_wrong_sender"]
    assert shop.counts() == before


def test_an_ack_for_an_unknown_allocation_is_only_the_mains_to_send(shop):
    assert shop.apply(_ack_ev("never-heard-of"), shop.B) is False
    assert shop.parked("retail_setting") == ["release_ack_wrong_sender"]


def test_other_retail_settings_stay_unsigned_and_apply(shop):
    ev = {"entity_type": "retail_setting", "event_type": "update", "entity_id": str(uuid.uuid4()),
          "payload": {"skey": "currency", "svalue": "JOD"}}
    assert shop.apply(ev) is True
    assert shop.rows("SELECT svalue FROM retail_settings WHERE skey='currency'")[0]["svalue"] == "JOD"


# ═══ branch_alias ══════════════════════════════════════════════════════════

def test_a_branch_alias_from_its_own_origin_is_allowed(shop):
    assert shop.apply(_alias_ev("dev-a"), shop.A) is True
    assert [r["alias_uid"] for r in shop.rows("SELECT alias_uid FROM branch_uid_aliases")] == [OLD]


def test_a_branch_alias_naming_someone_elses_origin_is_refused(shop):
    before = shop.counts()
    assert shop.apply(_alias_ev("dev-b"), shop.A) is False
    assert shop.parked("branch_alias") == ["alias_origin_mismatch"]
    assert shop.counts() == before


# ═══ the rest of the engine is untouched ═══════════════════════════════════

def test_unsigned_non_authority_events_still_apply_unchanged(shop):
    branch = {"entity_type": "branch", "event_type": "create", "entity_id": OLD,
              "payload": {"uid": OLD, "name": "Fresh", "address": "", "phone": "", "status": "active"}}
    assert shop.apply(branch) is True
    assert [r["uid"] for r in shop.rows("SELECT uid FROM branches WHERE uid=?", (OLD,))] == [OLD]
    assert shop.parked() == []


def test_forged_retail_authority_rows_change_nothing(shop):
    """A computer holding the shop licence inserts raw outbox rows to allocate a
    till to itself, take over numbering, alias a branch and fake a release
    acknowledgement. Signed by the attacker, and again unsigned: nothing lands."""
    _with_allocation(shop, "dev-b")
    before = shop.counts()
    forged = [_till_ev("evil", number=7, terminal="dev-a", by="dev-main"), _series_ev("evil-ds", "EVIL"),
              _alias_ev("dev-b", alias="22222222-2222-4222-8222-222222222222"), _ack_ev("al-1")]
    for ev in forged:
        shop.apply(ev, shop.A)
    assert shop.parked() == ["needs_main_computer", "needs_main_computer", "alias_origin_mismatch",
                             "release_ack_wrong_sender"]
    for ev in forged:
        shop.apply(ev)
    assert shop.counts() == before
    assert [r["id"] for r in shop.rows("SELECT id FROM till_allocations")] == ["al-1"]


def test_a_member_of_another_licence_cannot_allocate(shop):
    outsider = shop.world.new_device("dev-main", licence="lic-someone-else")   # even naming the main's id
    before = shop.counts()
    assert shop.apply(_till_ev(), outsider) is False
    assert shop.parked("till_allocation") == ["foreign_licence"]
    assert shop.counts() == before


# ═══ D3: signing in read_outbox, both streams ══════════════════════════════

def _queue(conn, entity_type, payload, entity_id=None, event_type="create"):
    conn.execute("INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) "
                 "VALUES (?,?,?,?,?,?)",
                 (str(uuid.uuid4()), entity_type, entity_id or str(uuid.uuid4()), event_type,
                  json.dumps(payload), "2026-10-01T00:00:00+00:00"))


def test_read_outbox_signs_authority_events_and_leaves_the_rest_alone(shop):
    sender = SyncService(client_factory=lambda: None, get_conn=shop.get_conn,
                         local_company_id_provider=lambda: CID,
                         local_schema_version_provider=lambda: 43,
                         authority_env_provider=shop.world.provider(shop.M))
    conn = shop.get_conn()
    _queue(conn, "till_allocation", _till_ev()["payload"], "al-1")
    _queue(conn, "branch", {"uid": OLD, "name": "Fresh"}, OLD)
    _queue(conn, "retail_setting", {"skey": "currency", "svalue": "JOD"})
    _queue(conn, "retail_setting", {"skey": "till_release_ack:al-1", "svalue": "1"})
    conn.commit()
    events = sender.read_outbox(conn)
    conn.close()
    by_type = {}
    for e in events:
        by_type.setdefault(e["entity_type"], []).append(e)
    signed = by_type["till_allocation"][0]
    assert signed["payload"]["_sender_schema_version"] == 43               # stamped AFTER signing...
    assert isinstance(authority.verify_event(signed, trust_store=shop.world.trust_store, own_licence=LID),
                      authority.VerifiedSigner)                               # ...and does not break it
    assert "_auth" in by_type["retail_setting"][1]["payload"]              # the release ack is signed
    assert "_auth" not in by_type["retail_setting"][0]["payload"]          # an ordinary setting is not
    assert "_auth" not in by_type["branch"][0]["payload"]


def test_a_signed_outbox_event_is_applied_by_a_receiver_end_to_end(shop):
    sender = SyncService(client_factory=lambda: None, get_conn=shop.get_conn,
                         local_company_id_provider=lambda: CID,
                         authority_env_provider=shop.world.provider(shop.M))
    conn = shop.get_conn()
    _queue(conn, "till_allocation", _till_ev("pushed")["payload"], "pushed")
    conn.commit()
    [event] = sender.read_outbox(conn)
    conn.execute("DELETE FROM sync_outbox")
    conn.commit()
    conn.close()
    wire = json.loads(json.dumps({k: event[k] for k in ("id", "entity_type", "entity_id", "event_type",
                                                          "payload", "created_at")}))   # a real JSON hop
    assert shop.apply(wire) is True
    assert [r["id"] for r in shop.rows("SELECT id FROM till_allocations")] == ["pushed"]


def test_a_sender_without_a_signer_sends_unsigned_and_says_so_once(shop, caplog):
    sender = SyncService(client_factory=lambda: None, get_conn=shop.get_conn,
                         local_company_id_provider=lambda: CID,
                         authority_env_provider=shop.world.provider(None))
    conn = shop.get_conn()
    _queue(conn, "till_allocation", _till_ev("a")["payload"], "a")
    _queue(conn, "till_allocation", _till_ev("b", number=3, terminal="t2")["payload"], "b")
    conn.commit()
    with caplog.at_level(logging.WARNING):
        events = sender.read_outbox(conn)
        sender.read_outbox(conn)
    conn.close()
    assert all("_auth" not in e["payload"] for e in events)
    assert sum("cannot sign" in r.getMessage() for r in caplog.records) == 1


def test_read_outbox_does_not_mutate_the_stored_row(shop):
    sender = SyncService(client_factory=lambda: None, get_conn=shop.get_conn,
                         local_company_id_provider=lambda: CID,
                         authority_env_provider=shop.world.provider(shop.M))
    conn = shop.get_conn()
    _queue(conn, "till_allocation", _till_ev()["payload"], "al-1")
    conn.commit()
    sender.read_outbox(conn)
    stored = json.loads(conn.execute("SELECT payload FROM sync_outbox").fetchone()["payload"])
    conn.close()
    assert "_auth" not in stored


def test_registry_stream_outbox_is_signed_too(shop):
    """The registry stream's own SyncService reads registry.db's outbox through the
    same method: its `user` / `admin_device` events are signed on the way out."""
    reg_svc = SyncService(client_factory=lambda: None, get_conn=registry_db.get_conn,
                          local_company_id_provider=lambda: CID,
                          handled_entity_types=ss.REGISTRY_SYNC_ENTITY_TYPES,
                          authority_env_provider=shop.world.provider(shop.M))
    reg = registry_db.get_conn()
    _queue(reg, "admin_device", {"device_id": "dev-main", "epoch": 1,
                                 "assigned_at_utc": "2026-01-01T00:00:00+00:00"})
    _queue(reg, "user", {"uid": "u1", "role": "cashier"})
    reg.commit()
    events = reg_svc.read_outbox(reg)
    reg.close()
    assert all("_auth" in e["payload"] for e in events)
    assert all(isinstance(authority.verify_event(e, trust_store=shop.world.trust_store, own_licence=LID),
                          authority.VerifiedSigner) for e in events)
