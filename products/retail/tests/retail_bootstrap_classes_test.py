"""SYNC-BOOT Phase 2: the class ratchet. Every table of a retail database has exactly one
bootstrap class, and the classes that keep the shop's authority out of a copy are the right ones.

WHY A RATCHET. A bootstrap copy is built table by table by class. A table nobody classified is
a table nobody decided about: copied, it can leak the producer's credentials; dropped, it can lose
shop data. The producer refuses to ship an unclassified table at run time
(`UNCLASSIFIED_TABLE`); this test is the same rule applied at merge time, so a new table cannot
land without somebody saying what a copy does with it.

The source-scan test is the second net: it finds `CREATE TABLE` statements in the product's code
that no fresh database in this file would create (tables made lazily by a route, or by a module
this test does not import), so the first net's blind spot is covered by name.

Run: pytest products/retail/tests/retail_bootstrap_classes_test.py -q
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
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
from commercial_runtime.sync.bootstrap import classes  # noqa: E402
from database import bootstrap_classes  # noqa: E402
from database.bootstrap_classes import RETAIL_ROW_RULES, RETAIL_TABLE_CLASSES, retail_spec  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    reg_dir = tmp_path / "registry_db"
    monkeypatch.setattr(registry_db, "_db_dir", str(reg_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(reg_dir / "registry.db"))
    registry_db.init_registry_db()
    root = tmp_path / "install"
    subsys = root / "subsystems"
    subsys.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(schema, "BASE_DIR", str(root))
    monkeypatch.setattr(schema, "SUBSYS_DIR", str(subsys))
    schema.init_retail()
    conn = sqlite3.connect(str(subsys / "retail.db"))
    conn.row_factory = sqlite3.Row
    # The tables the route layer creates lazily (retail_settings, payment_methods, doc_sequences...)
    previous = retail_api._CREDIT_SCHEMA_READY
    retail_api._CREDIT_SCHEMA_READY = False
    try:
        retail_api._ensure_credit_schema(conn)
        conn.commit()
    finally:
        retail_api._CREDIT_SCHEMA_READY = previous
    yield conn
    conn.close()


def test_every_table_in_a_freshly_migrated_database_has_exactly_one_class(fresh_db):
    spec = retail_spec(schema.RETAIL_SCHEMA_VERSION)
    assert spec.problems(fresh_db) == []
    tables = classes.user_tables(fresh_db)
    assert len(tables) > 70                                   # the fixture really built the database
    # "Exactly one": a dict key cannot repeat, and every value must be a known class.
    assert all(spec.class_of(t) in classes.TABLE_CLASSES for t in tables)


def test_a_table_added_without_a_class_is_reported(fresh_db):
    """The ratchet's own deny half: it must go red when someone adds a table and says nothing."""
    fresh_db.execute("CREATE TABLE brand_new_loyalty_cache (id INTEGER PRIMARY KEY)")
    problems = retail_spec(schema.RETAIL_SCHEMA_VERSION).problems(fresh_db)
    assert problems == ["brand_new_loyalty_cache: no class"]


def test_the_spec_is_internally_consistent():
    spec = retail_spec(schema.RETAIL_SCHEMA_VERSION)
    # problems() against an empty database still checks every structural rule of the MAP itself.
    empty = sqlite3.connect(":memory:")
    assert spec.problems(empty) == []
    empty.close()
    assert spec.schema_version == schema.RETAIL_SCHEMA_VERSION


def test_a_row_level_table_without_a_rule_or_a_rule_on_a_plain_table_is_a_problem():
    empty = sqlite3.connect(":memory:")
    bad = classes.BootstrapSpec(
        product="retail", db_name="retail.db", schema_version=1,
        table_classes={"a": classes.ROW_LEVEL, "b": classes.SYNCED, "c": classes.BLOCK_IF_ROWS},
        row_rules={"b": RETAIL_ROW_RULES["retail_settings"]})
    problems = bad.problems(empty)
    assert "a: ROW_LEVEL but no RowRule" in problems
    assert "b: has a RowRule but class is SYNCED" in problems
    assert "c: BLOCK_IF_ROWS but no block_where predicate" in problems


def test_no_authority_table_is_classified_as_something_a_copy_would_carry():
    """A copy must never carry what the main computer signs (AUDIT-033). Each authority entity type
    that lives in the retail database maps to a table that must be AUTHORITY."""
    authority_table = {"till_allocation": "till_allocations", "doc_series": "doc_series",
                       "branch_alias": "branch_uid_aliases"}
    assert set(authority_table) <= set(authority.AUTHORITY_ENTITY_TYPES)
    for entity, table in authority_table.items():
        assert RETAIL_TABLE_CLASSES[table] == classes.AUTHORITY, (entity, table)
    # `retail_setting` is authority only for release acks; the row rule must say exactly that, using
    # the prefix the authority module itself uses (two values that CAN drift apart, unlike a rule
    # compared to its own constant).
    rule = RETAIL_ROW_RULES["retail_settings"]
    assert rule.class_of_key(authority.RELEASE_ACK_PREFIX + "abc") == classes.AUTHORITY


def test_a_tills_own_numbering_counter_is_carried_never_copied():
    rule = RETAIL_ROW_RULES["retail_settings"]
    assert rule.class_of_key(retail_api._TILL_STATE_KEY_PREFIX + "terminal-1") == classes.LOCAL_CARRY
    # Allow half: an ordinary shop-wide setting still travels, and a branch code is SHOP-WIDE
    # (test_retail_setting_device_local_skip pins that numbering breaks across tills if it is not).
    assert rule.class_of_key("currency") == classes.SYNCED
    assert rule.class_of_key("branch_code:abc") == classes.SYNCED
    assert rule.class_of_key("blob_logo") == classes.LOCAL_CARRY


def test_credentials_and_hub_bookkeeping_are_never_copied_from_a_peer():
    for table in ("email_settings", "whatsapp_settings", "einvoice_settings", "einvoice_sequence",
                  "site_paired_devices", "site_sync_events", "site_sync_nonces", "site_device_cursors",
                  "site_prune_state", "pin_approval_attempts", "doc_series_counter"):
        assert RETAIL_TABLE_CLASSES[table] == classes.LOCAL_CARRY, table


def test_an_open_cash_drawer_is_the_blocker():
    assert RETAIL_TABLE_CLASSES["cash_sessions"] == classes.BLOCK_IF_ROWS
    assert bootstrap_classes.RETAIL_BLOCK_WHERE == {"cash_sessions": "status = 'open'"}


_CREATE = re.compile(r'CREATE TABLE(?: IF NOT EXISTS)?\s+"?([a-z][a-z_0-9]*)"?\s*\(', re.IGNORECASE)


def test_every_table_the_products_code_can_create_is_classified():
    """The source scan: a CREATE TABLE anywhere under the retail backend (or the shared notification,
    e-invoicing and sync modules that build tables inside retail.db) whose name has no class."""
    roots = [_BACKEND, _REPO_ROOT / "commercial_runtime" / "notifications",
             _REPO_ROOT / "commercial_runtime" / "einvoicing",
             _REPO_ROOT / "commercial_runtime" / "sync", _REPO_ROOT / "commercial_runtime" / "security"]
    found = {}
    for root in roots:
        for path in root.rglob("*.py"):
            if "tests" in path.parts or path.name.startswith("test_") or "_test" in path.stem:
                continue
            for name in _CREATE.findall(path.read_text(encoding="utf-8", errors="replace")):
                found.setdefault(name, path.name)
    # Names that are temporaries of a rebuild, or live in another database (registry.db, licensing.db).
    ignore = {n for n in found if n.endswith("_new")}
    ignore |= {"sync_signers"}               # registry.db (authority.py), never retail.db
    ignore |= {"exactly"}                   # prose in a docstring ("CREATE TABLE exactly (...)"), not a statement
    missing = sorted((n, f) for n, f in found.items() if n not in RETAIL_TABLE_CLASSES and n not in ignore)
    assert missing == [], f"tables with no bootstrap class: {missing}"
