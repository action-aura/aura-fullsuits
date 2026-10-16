"""SYNC-BOOT Phase 2: building a bootstrap copy (producer) and installing it (installer), on REAL
retail databases migrated by the REAL schema chain, with REAL signatures (a throwaway Owner,
real Ed25519 leases) verified by the REAL `authority.verify_event`.

Each guard has its allow half next to its deny half: a producer that scrubbed EVERYTHING would pass
every "the secret is gone" test and hand a joining computer an empty shop, so the shop data
surviving is asserted in the same tests; an installer that refused EVERY copy would pass every
refusal test, so the matching copy being installed is asserted beside each refusal.

Run: pytest products/retail/tests/retail_bootstrap_producer_installer_test.py -q
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

for _k in ("AURA_SYNC_RELAY_URL", "AURA_OWNER_LICENSING_URL"):
    os.environ.pop(_k, None)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import api.retail_api as retail_api  # noqa: E402
import database.schema as schema  # noqa: E402
from commercial_runtime.identity import registry_db  # noqa: E402
from commercial_runtime.sync import authority  # noqa: E402
from commercial_runtime.sync.bootstrap import errors, installer, producer  # noqa: E402
from commercial_runtime.sync.bootstrap.errors import BootstrapError  # noqa: E402
from commercial_runtime.sync.tests._signing import SigningWorld  # noqa: E402
from database.bootstrap_classes import retail_spec  # noqa: E402

COMPANY = "co-shop-1"
HUB_URL = "aura-site-log://local"
DEVICE_URL = "https://192.168.1.50:5443"
SECRET = "SMTP-SECRET-pa55w0rd-9f3a"           # a credential that must never leave the producer
SITE_MARKER = "SITE-LOG-PAYLOAD-MARKER-77e1"   # the producer's relay log, ditto
AS_OF = 7


class Shop:
    """One computer's app-data directory with a migrated retail.db."""

    def __init__(self, root: Path, monkeypatch, name: str):
        self.app = root / name
        self.subsys = self.app / "database" / "subsystems"
        self.subsys.mkdir(parents=True, exist_ok=True)
        reg_dir = root / f"{name}_registry"
        with monkeypatch.context() as m:
            m.setattr(registry_db, "_db_dir", str(reg_dir))
            m.setattr(registry_db, "DB_PATH", str(reg_dir / "registry.db"))
            registry_db.init_registry_db()
            m.setattr(schema, "BASE_DIR", str(self.app / "database"))
            m.setattr(schema, "SUBSYS_DIR", str(self.subsys))
            schema.init_retail()
        self.db = self.subsys / "retail.db"
        conn = self.conn()
        previous = retail_api._CREDIT_SCHEMA_READY
        retail_api._CREDIT_SCHEMA_READY = False
        try:
            retail_api._ensure_credit_schema(conn)
            conn.commit()
        finally:
            retail_api._CREDIT_SCHEMA_READY = previous
            conn.close()

    def conn(self):
        c = sqlite3.connect(str(self.db), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def run(self, sql, params=()):
        c = self.conn()
        try:
            c.execute(sql, params)
            c.commit()
        finally:
            c.close()

    def one(self, sql, params=()):
        c = self.conn()
        try:
            r = c.execute(sql, params).fetchone()
            return None if r is None else tuple(r)
        finally:
            c.close()

    def count(self, table, where="1=1"):
        return self.one(f'SELECT COUNT(*) FROM "{table}" WHERE {where}')[0]

    def seed_producer(self):
        """A working hub: shop data, authority, and everything that is the hub's own."""
        c = self.conn()
        try:
            c.execute("INSERT INTO products (id, sku, name, sell_price) VALUES ('p1','SKU-1','Coffee',2.5)")
            c.execute("INSERT INTO products (id, sku, name, sell_price) VALUES ('p2','SKU-2','Tea',1.5)")
            c.execute("INSERT INTO customers (id, name) VALUES ('c1','Ayman')")
            c.execute("INSERT INTO inventory_balances (company_id, product_id, branch_id, quantity_on_hand) "
                      "VALUES (?, 'p1', 1, 40)", (COMPANY,)) if self._has("inventory_balances") else None
            # a PARKED event (an authority event nobody could verify yet): it must travel
            c.execute("INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason) "
                      "VALUES ('e-parked','user','update','{\"x\":1}','unsigned_authority_event')")
            # authority: dropped from the copy, returned signed by the main
            c.execute("INSERT INTO doc_series (id, company_id, doc_type, code, label) "
                      "VALUES ('ds1', 1, 'sale', 'ABD1', 'Sales')")
            c.execute("INSERT INTO till_allocations (company_id, branch_uid, till_number, terminal_uid, "
                      "allocated_by_terminal_uid, admin_epoch, created_at_utc, updated_at_utc) "
                      "VALUES ('co','b1',1,'t1','main',1,'x','x')")
            # retail_settings: one shop-wide, one device-local, one release-ack, one blob
            for k, v in (("currency", "JOD"), ("numbering_till:hub-terminal", '{"next": 900}'),
                         ("till_release_ack:abc", "ack"), ("blob_logo", "BASE64")):
                c.execute("INSERT INTO retail_settings (company_id, skey, svalue) VALUES (1, ?, ?)", (k, v))
            # the hub's own: counters, credentials, relay log, pin lockouts, an open drawer
            c.execute("INSERT INTO doc_series_counter (series_id, last_no) VALUES ('ds1', 900)")
            c.execute("INSERT INTO email_settings (company_id, skey, svalue) VALUES ('co','smtp_password',?)", (SECRET,))
            c.execute("INSERT INTO pin_approval_attempts (company_id, approver_uid, failures) VALUES ('co','mgr',3)")
            c.execute("INSERT INTO site_sync_events (id, entity_type, entity_id, event_type, payload, created_at, "
                      "origin_device_id) VALUES ('s1','product','p1','create',?, 'now', 'dev')",
                      (json.dumps({"marker": SITE_MARKER}),))
            c.execute("INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload) "
                      "VALUES ('hub-outbox','product','p1','update','{}')")
            c.execute("INSERT INTO cash_sessions (id, company_id, branch_id, status) VALUES ('hub-cs','co',1,'open')")
            c.execute("INSERT INTO audit_log (company_id, action) VALUES ('co','HUB_LOGIN')") if self._has("audit_log") else None
            c.execute("UPDATE sync_cursor SET last_seq = ?, relay_url = ? WHERE id = 1", (AS_OF, HUB_URL))
            c.commit()
        finally:
            c.close()

    def seed_requester(self, *, open_drawer=False):
        """A late joiner's own database: its own counters/credentials and one unpushed write."""
        c = self.conn()
        try:
            c.execute("INSERT INTO doc_series_counter (series_id, last_no) VALUES ('my-series', 41)")
            c.execute("INSERT INTO email_settings (company_id, skey, svalue) VALUES ('co','smtp_password','MINE-ok')")
            c.execute("INSERT INTO pin_approval_attempts (company_id, approver_uid, failures) VALUES ('co','mgr2',1)")
            c.execute("INSERT INTO retail_settings (company_id, skey, svalue) VALUES (1,'numbering_till:me','{\"next\": 42}')")
            c.execute("INSERT INTO retail_settings (company_id, skey, svalue) VALUES (1,'currency','USD-old')")
            c.execute("INSERT INTO doc_series (id, company_id, doc_type, code, label) VALUES ('old-ds',1,'sale','OLD','Old')")
            c.execute("INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload) "
                      "VALUES ('my-unpushed','product','my-p','create','{\"name\":\"Mine\"}')")
            c.execute("INSERT INTO products (id, sku, name) VALUES ('my-p','MY-SKU','Local only')")
            if open_drawer:
                c.execute("INSERT INTO cash_sessions (id, company_id, branch_id, status) VALUES ('my-cs','co',1,'open')")
            c.commit()
        finally:
            c.close()

    def _has(self, table):
        c = sqlite3.connect(str(self.db))
        try:
            return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone())
        finally:
            c.close()


@pytest.fixture
def world(tmp_path):
    return SigningWorld(tmp_path / "world")


@pytest.fixture
def spec():
    return retail_spec(schema.RETAIL_SCHEMA_VERSION)


@pytest.fixture
def hub(tmp_path, monkeypatch):
    s = Shop(tmp_path, monkeypatch, "hub")
    s.seed_producer()
    return s


@pytest.fixture
def late(tmp_path, monkeypatch):
    s = Shop(tmp_path, monkeypatch, "late")
    s.seed_requester()
    return s


def build(hub, spec, world, **kw):
    main = world.new_device("main-dev")
    args = dict(spec=spec, source_path=str(hub.db), company_id=COMPANY, signer=main.signer,
                expected_relay_url=HUB_URL)
    args.update(kw)
    return producer.build_bootstrap(**args)


def stage(late, spec, world, built, **kw):
    args = dict(spec=spec, app_data_dir=late.app, relay_url=DEVICE_URL, trust_store=world.trust_store,
                own_licence=world.licence, expected_company_id=COMPANY, registry_fast_forward=12)
    args.update(kw)
    return installer.stage(built.data, **args)


def unzip_db(data, tmp_path, name="copy.db"):
    out = tmp_path / name
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        out.write_bytes(zf.read("retail.db"))
    return out


# ── producer ────────────────────────────────────────────────────────────────

def test_as_of_is_the_copys_own_cursor_not_the_live_one(hub, spec, world, monkeypatch):
    """The live database moves between the snapshot and any later read. as_of must be what the COPY
    applied. Here the source's cursor jumps to 99 right after the snapshot is taken."""
    real = producer.take_snapshot

    def racing(source, dest):
        method = real(source, dest)
        hub.run("UPDATE sync_cursor SET last_seq = 99 WHERE id = 1")     # a pull lands right after
        return method

    monkeypatch.setattr(producer, "take_snapshot", racing)
    built = build(hub, spec, world)
    assert built.as_of == AS_OF and built.manifest["as_of"] == AS_OF
    assert hub.one("SELECT last_seq FROM sync_cursor")[0] == 99          # the race really happened


def test_parked_events_travel_with_the_copy(hub, spec, world, tmp_path):
    built = build(hub, spec, world)
    c = sqlite3.connect(str(unzip_db(built.data, tmp_path)))
    try:
        assert c.execute("SELECT entity_id, reason FROM sync_apply_quarantine").fetchall() == [
            ("e-parked", "unsigned_authority_event")]
    finally:
        c.close()


def test_the_copy_keeps_the_shops_business_and_drops_what_is_not_the_copys_to_give(hub, spec, world, tmp_path):
    built = build(hub, spec, world)
    c = sqlite3.connect(str(unzip_db(built.data, tmp_path)))
    try:
        n = lambda t, w="1=1": c.execute(f'SELECT COUNT(*) FROM "{t}" WHERE {w}').fetchone()[0]  # noqa: E731
        # allow half: the shop is there
        assert n("products", "id IN ('p1','p2')") == 2 and n("customers", "id = 'c1'") == 1
        assert c.execute("SELECT svalue FROM retail_settings WHERE skey='currency'").fetchone()[0] == "JOD"
        # deny half: authority, the hub's counters, credentials, relay log, lockouts, outbox, drawer, logs
        for table in ("doc_series", "till_allocations", "doc_series_counter", "email_settings",
                      "pin_approval_attempts", "site_sync_events", "sync_outbox", "cash_sessions",
                      "audit_log", "sync_cursor"):
            assert n(table) == 0, table
        left = {r[0] for r in c.execute("SELECT skey FROM retail_settings")}
        assert left == {"currency"}                       # numbering_till:, till_release_ack:, blob_ gone
    finally:
        c.close()
    assert built.manifest["scrubbed_rows"]["doc_series"] == 1


def test_scrubbed_secrets_cannot_be_read_back_out_of_the_shipped_file(hub, spec, world, tmp_path):
    """DELETE alone leaves the bytes in free pages. The shipped file must not contain the hub's
    credential or its relay log payload anywhere -- not just in a table."""
    built = build(hub, spec, world)
    raw = unzip_db(built.data, tmp_path).read_bytes()
    assert SECRET.encode() not in raw and SITE_MARKER.encode() not in raw
    assert b"Coffee" in raw                                # and the file is not simply empty
    # The control: the SOURCE does contain them, so the assertion above is not vacuous.
    src = hub.db.read_bytes() + Path(str(hub.db) + "-wal").read_bytes() if Path(str(hub.db) + "-wal").exists() else hub.db.read_bytes()
    assert SECRET.encode() in src


def test_the_producer_refuses_a_table_nobody_classified(hub, spec, world):
    hub.run("CREATE TABLE brand_new_cache (id INTEGER PRIMARY KEY, secret TEXT)")
    with pytest.raises(BootstrapError) as exc:
        build(hub, spec, world)
    assert exc.value.reason_code == errors.UNCLASSIFIED_TABLE


def test_the_producer_refuses_when_its_cursor_is_on_another_relay(hub, spec, world):
    """as_of is a position in ONE relay's sequence. A hub whose cursor is still bound to the cloud
    has no position in its own site log to offer."""
    hub.run("UPDATE sync_cursor SET relay_url = 'https://owner.example' WHERE id = 1")
    with pytest.raises(BootstrapError) as exc:
        build(hub, spec, world)
    assert exc.value.reason_code == errors.PRODUCER_NOT_ON_RELAY


def test_a_computer_that_cannot_sign_produces_nothing(hub, spec, world):
    with pytest.raises(BootstrapError) as exc:
        build(hub, spec, world, signer=None)
    assert exc.value.reason_code == errors.NO_SIGNER
    prepare_only = world.new_device(prepare_only=True)
    with pytest.raises(BootstrapError):
        build(hub, spec, world, signer=prepare_only.signer)


def test_the_backup_snapshot_is_the_fallback_when_vacuum_into_is_unavailable(hub, spec, world, monkeypatch, tmp_path):
    def broken(*a, **k):
        raise sqlite3.OperationalError("near INTO: syntax error")
    monkeypatch.setattr(producer, "_vacuum_into", broken)
    built = build(hub, spec, world)
    assert built.manifest["snapshot_method"] == "backup_snapshot"
    assert built.as_of == AS_OF
    c = sqlite3.connect(str(unzip_db(built.data, tmp_path)))
    try:
        assert c.execute("SELECT COUNT(*) FROM products WHERE id IN ('p1','p2')").fetchone()[0] == 2
        assert SECRET.encode() not in unzip_db(built.data, tmp_path, "again.db").read_bytes()
    finally:
        c.close()


# ── installer: verification ─────────────────────────────────────────────────

def _rezip(data, *, db_bytes=None, edit_manifest=None, signer=None):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        envelope = json.loads(zf.read("manifest.json"))
        db = zf.read("retail.db")
    if edit_manifest is not None:
        payload = {k: v for k, v in envelope["payload"].items() if k != "_auth"}
        edit_manifest(payload)
        envelope = authority.sign_event(
            {"entity_type": producer.MANIFEST_ENTITY_TYPE, "entity_id": payload["bootstrap_id"],
             "event_type": "create", "payload": payload}, signer)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("manifest.json", json.dumps(envelope))
        zf.writestr("retail.db", db if db_bytes is None else db_bytes)
    return out.getvalue()


def test_a_matching_signed_copy_is_staged_and_a_tampered_one_is_not(hub, late, spec, world):
    built = build(hub, spec, world)
    record = stage(late, spec, world, built)                       # allow half
    assert record["as_of"] == AS_OF and installer.pending(late.app)["bootstrap_id"] == record["bootstrap_id"]

    tampered = producer.BuiltBootstrap(_rezip(built.data, db_bytes=b"SQLite format 3\0" + b"x" * 5000), {}, AS_OF)
    with pytest.raises(BootstrapError) as exc:
        stage(late, spec, world, tampered)
    assert exc.value.reason_code == errors.CHECKSUM_MISMATCH
    assert installer.pending(late.app) is None or installer.pending(late.app)["bootstrap_id"] == record["bootstrap_id"]


def test_an_unsigned_or_foreign_licence_copy_is_refused(hub, late, spec, world, tmp_path):
    built = build(hub, spec, world)
    # unsigned: strip _auth
    with zipfile.ZipFile(io.BytesIO(built.data)) as zf:
        env = json.loads(zf.read("manifest.json"))
        db = zf.read("retail.db")
    env["payload"].pop("_auth")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("manifest.json", json.dumps(env))
        zf.writestr("retail.db", db)
    with pytest.raises(BootstrapError) as exc:
        stage(late, spec, world, producer.BuiltBootstrap(out.getvalue(), {}, AS_OF))
    assert exc.value.reason_code == errors.MANIFEST_NOT_TRUSTED
    # signed by a member of a DIFFERENT licence
    stranger_world = SigningWorld(tmp_path / "other", licence="lic-someone-else")
    stranger = stranger_world.new_device("stranger")
    foreign = producer.build_bootstrap(
        spec=spec, source_path=str(hub.db), company_id=COMPANY, signer=stranger.signer, expected_relay_url=HUB_URL)
    with pytest.raises(BootstrapError) as exc:
        stage(late, spec, world, foreign)
    assert exc.value.reason_code == errors.MANIFEST_NOT_TRUSTED


def test_a_copy_of_another_shop_is_refused_company_mismatch(hub, late, spec, world):
    other = build(hub, spec, world, company_id="co-someone-elses-shop")
    with pytest.raises(BootstrapError) as exc:
        stage(late, spec, world, other)
    assert exc.value.reason_code == errors.COMPANY_MISMATCH
    stage(late, spec, world, build(hub, spec, world))              # allow half: the right shop installs
    with pytest.raises(BootstrapError) as exc:
        stage(late, spec, world, build(hub, spec, world), expected_company_id=None)
    assert exc.value.reason_code == errors.COMPANY_MISMATCH        # a device that knows no company accepts none


def test_a_newer_schema_is_refused_and_an_older_one_is_migrated_at_install(hub, late, spec, world, tmp_path):
    built = build(hub, spec, world)
    main = world.devices["main-dev"]
    newer = producer.BuiltBootstrap(
        _rezip(built.data, edit_manifest=lambda p: p.update(schema_version=spec.schema_version + 1), signer=main.signer),
        {}, AS_OF)
    with pytest.raises(BootstrapError) as exc:
        stage(late, spec, world, newer)
    assert exc.value.reason_code == errors.SCHEMA_NEWER

    # OLDER: turn the hub into a v54 database, build from it, stage, install with the real migration.
    hub.run("DROP TABLE site_device_cursors")
    hub.run("CREATE TABLE site_device_cursors (installation_id TEXT PRIMARY KEY, last_seq INTEGER NOT NULL DEFAULT 0, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    hub.run("DROP TABLE site_prune_state")
    c = hub.conn()
    c.execute("PRAGMA user_version = 54")
    c.commit()
    c.close()
    older = build(hub, spec, world)
    assert older.manifest["schema_version"] == 54
    stage(late, spec, world, older)
    report = installer.apply_staged(late.app, spec, migrate_fn=schema.migrate_retail_file)
    assert report["status"] == "installed", report
    assert late.one("PRAGMA user_version")[0] == schema.RETAIL_SCHEMA_VERSION
    assert late.count("site_prune_state") == 1                      # created by the migration
    assert late.count("products", "id IN ('p1','p2')") == 2          # and the shop's data came through it


def test_an_older_copy_without_a_migration_is_not_installed(hub, late, spec, world):
    hub.run("DROP TABLE site_prune_state")
    c = hub.conn()
    c.execute("PRAGMA user_version = 54")
    c.commit()
    c.close()
    stage(late, spec, world, build(hub, spec, world))
    before = late.db.read_bytes()
    report = installer.apply_staged(late.app, spec)               # no migrate_fn
    assert report["status"] == "failed" and "no migration" in report["why"]
    assert late.db.read_bytes() == before


# ── installer: the open drawer ──────────────────────────────────────────────

def test_an_open_cash_drawer_blocks_staging_and_a_drawer_opened_later_blocks_the_swap(hub, late, spec, world):
    late.run("INSERT INTO cash_sessions (id, company_id, branch_id, status) VALUES ('my-cs','co',1,'open')")
    built = build(hub, spec, world)
    with pytest.raises(BootstrapError) as exc:
        stage(late, spec, world, built)
    assert exc.value.reason_code == errors.OPEN_CASH_SESSION
    assert installer.pending(late.app) is None and not installer.staging_dir(late.app).exists()

    late.run("UPDATE cash_sessions SET status='closed' WHERE id='my-cs'")
    stage(late, spec, world, built)                                 # allow half: a closed drawer is fine
    late.run("UPDATE cash_sessions SET status='open' WHERE id='my-cs'")   # opened AFTER staging
    before = late.db.read_bytes()
    report = installer.apply_staged(late.app, spec)
    assert report["status"] == "blocked"
    assert late.db.read_bytes() == before and installer.pending(late.app) is not None   # kept for next start
    late.run("UPDATE cash_sessions SET status='closed' WHERE id='my-cs'")
    assert installer.apply_staged(late.app, spec)["status"] == "installed"


# ── installer: the swap ─────────────────────────────────────────────────────

def test_install_swaps_in_the_shop_keeps_the_devices_own_and_drops_authority(hub, late, spec, world):
    stage(late, spec, world, build(hub, spec, world))
    report = installer.apply_staged(late.app, spec)
    assert report["status"] == "installed", report

    # the shop arrived
    assert late.count("products", "id IN ('p1','p2')") == 2 and late.count("customers", "id='c1'") == 1
    assert late.one("SELECT svalue FROM retail_settings WHERE skey='currency'")[0] == "JOD"   # the shop's, not 'USD-old'
    # parked events survived into the installed database
    assert late.count("sync_apply_quarantine", "entity_id='e-parked'") == 1
    # LOCAL_CARRY: the device's OWN numbering, credentials, lockouts -- and none of the hub's
    assert late.one("SELECT last_no FROM doc_series_counter WHERE series_id='my-series'")[0] == 41
    assert late.count("doc_series_counter", "series_id='ds1'") == 0
    assert late.one("SELECT svalue FROM email_settings WHERE skey='smtp_password'")[0] == "MINE-ok"
    assert late.count("pin_approval_attempts", "approver_uid='mgr2'") == 1
    assert late.count("pin_approval_attempts", "approver_uid='mgr'") == 0
    assert late.one("SELECT svalue FROM retail_settings WHERE skey='numbering_till:me'")[0] == '{"next": 42}'
    assert late.count("retail_settings", "skey='numbering_till:hub-terminal'") == 0
    # AUTHORITY: neither the hub's nor the device's old rows -- they come back signed from the main
    assert late.count("doc_series") == 0 and late.count("till_allocations") == 0
    # the cursor: complete up to as_of ON THE DEVICE'S relay
    assert late.one("SELECT last_seq, relay_url FROM sync_cursor WHERE id=1") == (AS_OF, DEVICE_URL)
    # the hub's own log/outbox did not come along
    assert late.count("site_sync_events") == 0 and late.count("sync_outbox", "id='hub-outbox'") == 0


def test_the_devices_unpushed_outbox_survives_and_is_flagged_for_reapply(hub, late, spec, world):
    stage(late, spec, world, build(hub, spec, world))
    assert installer.apply_staged(late.app, spec)["carried_outbox"] == 1
    assert late.one("SELECT entity_id FROM sync_outbox WHERE id='my-unpushed'") == ("my-p",)
    assert installer.reapply_outbox_pending(late.app) is True
    # the copy was taken before the device's own product existed: the install did NOT contain it
    assert late.count("products", "id='my-p'") == 0


def test_the_install_arms_include_own_once_for_retail_only_and_clears_per_stream(hub, late, spec, world):
    stage(late, spec, world, build(hub, spec, world))
    installer.apply_staged(late.app, spec)
    assert installer.include_own_once_pending(late.app, "retail") is True
    assert installer.include_own_once_pending(late.app, "registry") is False   # registry.db is never swapped
    installer.clear_include_own_once(late.app, "retail")
    assert installer.include_own_once_pending(late.app, "retail") is False
    installer.mark_include_own_once(late.app, "backup-restore")                # both streams
    assert installer.include_own_once_pending(late.app, "registry") and installer.include_own_once_pending(late.app, "retail")
    installer.clear_include_own_once(late.app, "retail")
    assert installer.include_own_once_pending(late.app, "registry") and not installer.include_own_once_pending(late.app, "retail")


def test_the_rollback_copy_holds_the_old_databases_latest_writes_even_when_they_sit_only_in_its_wal(hub, late, spec, world):
    """A crashed run leaves a non-empty -wal beside the database: the newest writes live ONLY there.
    The rollback copy must contain them (the install checkpoints first), because the sidecar is then
    deleted and the copy would otherwise silently lack the device's last writes."""
    holder = late.conn()
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("PRAGMA wal_autocheckpoint=0")
    holder.execute("INSERT INTO customers (id, name) VALUES ('old-c','Old Customer')")
    holder.commit()
    crashed = late.app.parent / "crashed_files"
    crashed.mkdir()
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(late.db) + suffix)
        assert src.exists(), suffix
        shutil.copyfile(src, crashed / ("retail.db" + suffix))
    holder.close()                                     # a clean close would have merged the wal: put the crash back
    for suffix in ("", "-wal", "-shm"):
        target = Path(str(late.db) + suffix)
        if target.exists():
            target.unlink()
        shutil.copyfile(crashed / ("retail.db" + suffix), target)
    assert Path(str(late.db) + "-wal").stat().st_size > 0

    stage(late, spec, world, build(hub, spec, world))
    report = installer.apply_staged(late.app, spec)
    assert report["status"] == "installed", report
    saved = sqlite3.connect(str(Path(report["rollback_dir"]) / "retail.db"))
    try:
        assert saved.execute("SELECT COUNT(*) FROM customers WHERE id='old-c'").fetchone()[0] == 1
    finally:
        saved.close()
    assert late.count("customers", "id='old-c'") == 0 and late.count("customers", "id='c1'") == 1
    assert not Path(str(late.db) + "-wal").exists() or Path(str(late.db) + "-wal").stat().st_size == 0


def test_a_database_still_held_open_by_a_running_app_fails_the_install_without_harm(hub, late, spec, world):
    """The seam runs before anything opens the database. If something DID (a second instance), the
    install must fail loudly and leave the old database alone, to be retried at the next start."""
    stage(late, spec, world, build(hub, spec, world))
    holder = late.conn()
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("INSERT INTO customers (id, name) VALUES ('live-c','Live')")
    holder.commit()
    try:
        before = late.one("SELECT COUNT(*) FROM customers WHERE id='live-c'")
        report = installer.apply_staged(late.app, spec)
        assert report["status"] == "failed"
        assert late.one("SELECT COUNT(*) FROM customers WHERE id='live-c'") == before
        assert installer.pending(late.app) is not None
    finally:
        holder.close()


def test_a_failed_swap_leaves_the_old_database_exactly_where_it_was(hub, late, spec, world, monkeypatch):
    stage(late, spec, world, build(hub, spec, world))
    before = late.db.read_bytes()
    real = os.replace

    def failing(src, dst, *a, **k):
        if str(dst).endswith("retail.db"):
            raise OSError("disk says no")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(installer.os, "replace", failing)
    report = installer.apply_staged(late.app, spec)
    assert report["status"] == "failed"
    assert late.db.read_bytes() == before
    assert late.count("customers", "id='c1'") == 0                 # nothing of the copy leaked in


def test_apply_staged_with_nothing_staged_does_nothing(late, spec):
    before = late.db.read_bytes()
    assert installer.apply_staged(late.app, spec) is None
    assert late.db.read_bytes() == before


def test_the_registry_cursor_is_fast_forwarded_never_rewound(hub, late, spec, world):
    reg = late.app / "database" / "registry.db"
    c = sqlite3.connect(str(reg))
    c.execute("CREATE TABLE sync_cursor (id INTEGER PRIMARY KEY CHECK (id=1), last_seq INTEGER NOT NULL DEFAULT 0, relay_url TEXT)")
    c.execute("INSERT INTO sync_cursor (id, last_seq, relay_url) VALUES (1, 3, ?)", (DEVICE_URL,))
    c.commit()
    c.close()
    stage(late, spec, world, build(hub, spec, world), registry_fast_forward=12)
    installer.apply_staged(late.app, spec)
    c = sqlite3.connect(str(reg))
    try:
        assert c.execute("SELECT last_seq, relay_url FROM sync_cursor").fetchone() == (12, DEVICE_URL)
    finally:
        c.close()


# ── SYNC-BOOT Phase 3, Android: file staging, status, install-failure cap ───

def test_staging_from_a_file_matches_staging_from_bytes_and_never_loads_the_database(hub, late, spec, world, tmp_path, monkeypatch):
    """A phone must not hold a whole shop in memory: the path form hashes the database member in steps and
    copies it out, never calling ZipFile.read on it. MUTATION: make verify_package read the member
    (db_bytes = zf.read(...)) in the path form -> the patched read raises -> red."""
    built = build(hub, spec, world)
    zip_path = tmp_path / "copy.zip"
    zip_path.write_bytes(built.data)
    real_read = zipfile.ZipFile.read

    def guarded(self, name, *a, **k):
        if name == "retail.db":
            raise AssertionError("the database member was loaded into memory")
        return real_read(self, name, *a, **k)

    monkeypatch.setattr(zipfile.ZipFile, "read", guarded)
    verified = installer.verify_package(
        str(zip_path), spec=spec, trust_store=world.trust_store, own_licence=world.licence, expected_company_id=COMPANY)
    assert verified.db_bytes is None and verified.zip_path == str(zip_path)
    record = installer.stage(
        str(zip_path), spec=spec, app_data_dir=late.app, relay_url=DEVICE_URL, trust_store=world.trust_store,
        own_licence=world.licence, expected_company_id=COMPANY)
    monkeypatch.undo()
    staged = (installer.staging_dir(late.app) / "retail.db").read_bytes()
    assert staged == unzip_db(built.data, tmp_path).read_bytes() and record["as_of"] == AS_OF

    tampered = tmp_path / "bad.zip"                                  # the other direction: a bad file is refused
    tampered.write_bytes(_rezip(built.data, db_bytes=b"SQLite format 3\0" + b"x" * 5000))
    with pytest.raises(BootstrapError) as exc:
        installer.verify_package(
            str(tampered), spec=spec, trust_store=world.trust_store, own_licence=world.licence, expected_company_id=COMPANY)
    assert exc.value.reason_code == errors.CHECKSUM_MISMATCH


def test_status_says_whether_a_copy_can_be_staged_is_already_staged_and_how_installs_went(hub, late, spec, world):
    clean = installer.status(late.app, spec)
    assert clean == {"can_stage": True, "block_reason": None, "pending_staged": False, "install_failures": 0,
                     "needs_support": False, "last_install_failure": None}
    late.run("INSERT INTO cash_sessions (id, company_id, branch_id, status) VALUES ('my-cs','co',1,'open')")
    drawer = installer.status(late.app, spec)
    assert drawer["can_stage"] is False and errors.OPEN_CASH_SESSION in drawer["block_reason"]
    late.run("UPDATE cash_sessions SET status='closed' WHERE id='my-cs'")
    stage(late, spec, world, build(hub, spec, world))
    assert installer.status(late.app, spec)["pending_staged"] is True


def test_three_failed_installs_discard_the_copy_and_ask_for_support_and_a_success_resets_the_count(hub, late, spec, world, monkeypatch):
    """The re-request/restart loop is capped: a copy that cannot be installed must not be re-requested and
    re-installed forever. MUTATION: raise MAX_INSTALL_FAILURES to 99 -> the third assertion block goes red;
    drop the success reset -> the last block goes red."""
    built = build(hub, spec, world)
    stage(late, spec, world, built)
    real_check = installer.check_staged_database

    def broken(path, spec_):
        raise BootstrapError(errors.MIGRATION_FAILED, "boom")

    monkeypatch.setattr(installer, "check_staged_database", broken)
    for n in (1, 2):
        assert installer.apply_staged(late.app, spec)["status"] == "failed"
        st = installer.status(late.app, spec)
        assert st["install_failures"] == n and st["needs_support"] is False and st["pending_staged"] is True
    assert installer.apply_staged(late.app, spec)["status"] == "failed"      # the third
    st = installer.status(late.app, spec)
    assert st["needs_support"] is True and st["pending_staged"] is False and "boom" in st["last_install_failure"]
    assert not installer.staging_dir(late.app).exists()
    assert installer.apply_staged(late.app, spec) is None                    # nothing left to retry

    # the other direction: one failure, then a good install -> the counter is back to zero
    marker = installer.read_marker(late.app)
    marker.pop("install_failures"); marker.pop("last_install_failure")
    installer.write_marker(late.app, marker)
    monkeypatch.setattr(installer, "check_staged_database", real_check)
    stage(late, spec, world, built)
    monkeypatch.setattr(installer, "check_staged_database", broken)
    assert installer.apply_staged(late.app, spec)["status"] == "failed"
    assert installer.status(late.app, spec)["install_failures"] == 1
    monkeypatch.setattr(installer, "check_staged_database", real_check)
    assert installer.apply_staged(late.app, spec)["status"] == "installed"
    st = installer.status(late.app, spec)
    assert st["install_failures"] == 0 and st["last_install_failure"] is None


def test_the_stage_file_path_must_be_inside_the_download_directory(tmp_path):
    """MUTATION: return Path(path) without the check -> the outside and `..` cases stop raising -> red."""
    inside = tmp_path / "dl"
    inside.mkdir()
    ok = inside / "copy.zip"
    ok.write_bytes(b"x")
    assert installer.require_inside(inside, ok) == ok.resolve()
    outside = tmp_path / "other.zip"
    outside.write_bytes(b"x")
    for bad in (outside, inside / ".." / "other.zip", inside, tmp_path / "dl-evil" / "copy.zip"):
        with pytest.raises(BootstrapError) as exc:
            installer.require_inside(inside, bad)
        assert exc.value.reason_code == errors.BAD_PACKAGE


def test_a_blocked_install_is_not_counted_as_a_failure(hub, late, spec, world):
    stage(late, spec, world, build(hub, spec, world))
    late.run("INSERT INTO cash_sessions (id, company_id, branch_id, status) VALUES ('my-cs','co',1,'open')")
    for _ in range(5):
        assert installer.apply_staged(late.app, spec)["status"] == "blocked"
    st = installer.status(late.app, spec)
    assert st["install_failures"] == 0 and st["pending_staged"] is True      # a drawer resolves itself
