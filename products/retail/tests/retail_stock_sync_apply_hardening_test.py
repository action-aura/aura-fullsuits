"""Aura Retail -- Phase 5 wave B (stock-moving sync): apply-side hardening
for `SyncService._apply_event`'s `inventory_movement` and `branch` branches.

PROMOTED FROM SCRATCH. Every test below started life as an adversarial
verifier's disposable mutation-proof script (`_scratch_m3_duplicate_uid_
proof_test.py` and `_scratch_m5_tenancy_test.py`, both formerly at the repo
root, deleted once their content landed here) rather than as part of the
originally delivered `retail_stock_sync_test.py` suite -- each one proves a
claim that suite's own tests either only caught transitively or never
covered at all:

  1. `test_duplicate_uid_create_event_must_not_rewrite_movement_quantity` --
     a `create` event redelivered for a movement `uid` that already landed,
     with a DIFFERENT quantity in the payload (a corrupted or malicious
     duplicate delivery, not a mere exact replay). The delivered suite's
     `test_replaying_the_full_pull_result_twice_does_not_double_stock`
     only ever replays the IDENTICAL payload; this isolates the narrower,
     sharper claim by reading `inventory_movements.quantity` back directly.
  2. `test_a_bare_inventory_movement_update_event_with_no_prior_create_is_
     refused_not_fabricated` -- THE decisive proof that
     `if event_type != "create": return True` (sync_service.py's
     `_apply_event`, `inventory_movement` branch) actually does something.
     Verified directly: removing ONLY that guard while leaving
     `ON CONFLICT(uid) ... DO NOTHING` intact left the delivered suite's own
     `test_an_inventory_movement_update_event_on_an_existing_uid_never_
     rewrites_it` (formerly `..._update_or_delete_event_is_refused`) green,
     because that test only ever sends an "update" for a uid that was
     ALREADY "create"d -- DO NOTHING alone protects an existing row,
     independent of the guard. The guard's real job only shows up on a
     movement uid that was NEVER "create"d: no existing row means DO
     NOTHING has nothing to conflict with, so without the guard a bare
     "update" is free to fabricate a brand-new ledger row AND bump
     `inventory_balances` for it -- forging stock history and moving real,
     counted inventory out of nothing. This test is that decisive case.
  3./4. `test_inventory_movement_ignores_hostile_payload_company_id` /
     `test_branch_ignores_hostile_payload_company_id` -- a malicious/corrupt
     relay payload embeds a `company_id` different from the receiving
     device's own. Neither wave-B1 entity type had ANY tenancy test before
     this: the applied row must be stamped with the RECEIVER's own
     `local_company_id` (the constructor-supplied value standing in for
     `local_company_id_from_registry()` in production), never the
     payload's -- identical reasoning to every pre-existing entity type in
     this module (see sync_service.py's own "Cross-device company_id bug
     fix" docstring note).

No Flask app is booted in this file -- unlike retail_stock_sync_test.py,
every test here drives `SyncService._apply_event` directly against a bare
`install_b`-style sqlite database (the exact technique
retail_two_install_roundtrip_test.py's own `install_b` fixture uses), the
right tool for "does this one INSERT statement do what it claims" rather
than an end-to-end route-driven proof.

Run:
    pytest products/retail/tests/retail_stock_sync_apply_hardening_test.py -v
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import database.schema as schema  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402

RECEIVER_COMPANY_ID = "company-x"
HOSTILE_COMPANY_ID = "hostile-attacker-company"


@pytest.fixture
def install_b(tmp_path):
    """Same technique as retail_two_install_roundtrip_test.py's own
    `install_b` -- see that file's module docstring for the full "AURA_
    APP_DATA trap" reasoning (repeated here rather than imported for the
    same survive-independently reason every other duplicate helper in this
    suite is)."""
    b_root = tmp_path / "install_b"
    b_subsys = b_root / "subsystems"
    b_subsys.mkdir(parents=True, exist_ok=True)

    original_base_dir, original_subsys_dir = schema.BASE_DIR, schema.SUBSYS_DIR
    schema.BASE_DIR = str(b_root)
    schema.SUBSYS_DIR = str(b_subsys)
    try:
        schema.init_retail()
    finally:
        schema.BASE_DIR, schema.SUBSYS_DIR = original_base_dir, original_subsys_dir

    db_path = b_subsys / "retail.db"

    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    return _get_conn


def _seed_branch_and_product(b_conn, company_id=RECEIVER_COMPANY_ID):
    """Minimal parent rows an `inventory_movement` apply needs to resolve:
    one branch (movements carry no FK to it, but it exercises the same
    `_resolve_branch_id` path a real payload would) and one product (the
    ONE real FK `inventory_movements` declares). Returns the product id."""
    b_conn.execute(
        "INSERT INTO branches (company_id,name,address,phone,uid) VALUES (?,?,?,?,?)",
        (company_id, 'Main', '', '', str(uuid.uuid4())))
    b_conn.commit()
    # products.id is a client-generated UUID TEXT primary key (the wave-A/
    # wave-B "UUID migration" -- see CLAUDE.md's Sync section and
    # retail_api.py's create_product route, which does
    # `pid = str(_uuid.uuid4())` and inserts it explicitly), NOT an
    # autoincrement INTEGER -- omitting it here silently inserts a row with
    # id=NULL, which makes every downstream assertion quietly find nothing
    # to check rather than fail loudly (verified directly against an
    # earlier revision of this fixture: `product_row == {'id': None}`, and
    # `_apply_event`'s own `_row_exists` check then correctly, if
    # confusingly, quarantined the movement instead of applying it).
    product_id = str(uuid.uuid4())
    b_conn.execute(
        "INSERT INTO products (id, company_id, sku, barcode, name, cost_price, sell_price, tax_rate, unit, "
        "reorder_level, status) VALUES (?, ?, ?, '', ?, 5, 10, 0, 'pcs', 5, 'active')",
        (product_id, company_id, f'HARDEN-{product_id[:8]}', 'Hardening Test Product'))
    b_conn.commit()
    return product_id


def test_duplicate_uid_create_event_must_not_rewrite_movement_quantity(install_b):
    """A device (or a corrupted/malicious relay) redelivers a `create` event
    for a movement uid that already landed, but with a DIFFERENT quantity in
    the payload. Immutable-ledger posture (`ON CONFLICT(uid) ... DO
    NOTHING`) must leave the ALREADY-STORED row's quantity untouched --
    proved by reading the column back directly, not inferring it from the
    balance."""
    b_conn = install_b()
    try:
        product_id = _seed_branch_and_product(b_conn)
        service_b = SyncService(client_factory=lambda: None, get_conn=install_b,
                                 local_company_id_provider=lambda: RECEIVER_COMPANY_ID)
        movement_uid = str(uuid.uuid4())
        ev1 = {
            "entity_type": "inventory_movement", "event_type": "create",
            "payload": {
                "uid": movement_uid, "product_id": product_id, "branch_uid": None,
                "movement_type": "opening_stock", "quantity": 200, "unit_cost": 0,
                "reference": "OPEN", "notes": None, "created_by": "System",
                "actor_user_uid": None, "terminal_id": None, "created_at_utc": None,
            },
        }
        service_b._apply_event(b_conn, ev1, local_company_id=RECEIVER_COMPANY_ID)
        b_conn.commit()

        stored_after_first = b_conn.execute(
            "SELECT quantity FROM inventory_movements WHERE uid=?", (movement_uid,)
        ).fetchone()["quantity"]
        assert stored_after_first == 200

        # A DIFFERENT payload, SAME uid, sent as "create" again -- e.g. a
        # replay from a relay that re-sent the batch, or a bug/attack that
        # re-uses an existing wire uid with a rewritten quantity.
        ev2 = dict(ev1)
        ev2["payload"] = dict(ev1["payload"], quantity=200000)
        service_b._apply_event(b_conn, ev2, local_company_id=RECEIVER_COMPANY_ID)
        b_conn.commit()

        stored_after_second = b_conn.execute(
            "SELECT quantity FROM inventory_movements WHERE uid=?", (movement_uid,)
        ).fetchone()["quantity"]
        assert stored_after_second == 200, (
            f"a duplicate-uid create event REWROTE stock history: "
            f"{stored_after_first} -> {stored_after_second}"
        )
    finally:
        b_conn.close()


def test_a_bare_inventory_movement_update_event_with_no_prior_create_is_refused_not_fabricated(install_b):
    """THE decisive proof for `if event_type != "create": return True`
    (sync_service.py's `_apply_event`, `inventory_movement` branch) -- see
    this file's own module docstring for the full "what the delivered
    suite's differently-named test actually proves" story.

    A movement uid that was NEVER "create"d on this device (a malicious/
    corrupt relay payload, or Owner's push-side quarantine skipping the
    create but relaying a later, spurious update) arrives as a bare
    "update". With no existing row to conflict with, `ON CONFLICT(uid) ...
    DO NOTHING` cannot help -- only the event_type guard stands between this
    and a forged ledger row that also bumps `inventory_balances`.

    MUTATION-PROVEN (see this task's own report for the verbatim before/
    after pytest output): temporarily removing the `if event_type !=
    "create": return True` line from sync_service.py's `_apply_event` turns
    this test RED -- the forged row gets inserted and the balance moves.
    Restoring the guard turns it back GREEN."""
    b_conn = install_b()
    try:
        product_id = _seed_branch_and_product(b_conn)
        service_b = SyncService(client_factory=lambda: None, get_conn=install_b,
                                 local_company_id_provider=lambda: RECEIVER_COMPANY_ID)
        movement_uid = str(uuid.uuid4())  # NEVER "create"d on this device
        forged_ev = {
            "entity_type": "inventory_movement", "event_type": "update",
            "payload": {
                "uid": movement_uid, "product_id": product_id, "branch_uid": None,
                "movement_type": "opening_stock", "quantity": 999999, "unit_cost": 0,
                "reference": "FORGED", "notes": None, "created_by": "System",
                "actor_user_uid": None, "terminal_id": None, "created_at_utc": None,
            },
        }
        service_b._apply_event(b_conn, forged_ev, local_company_id=RECEIVER_COMPANY_ID)
        b_conn.commit()

        row = b_conn.execute(
            "SELECT quantity FROM inventory_movements WHERE uid=?", (movement_uid,)
        ).fetchone()
        assert row is None, (
            f"a bare 'update' event (no prior 'create') FABRICATED a new movement "
            f"with quantity={row['quantity'] if row else None} out of nothing"
        )

        bal = b_conn.execute(
            "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=?",
            (RECEIVER_COMPANY_ID, product_id),
        ).fetchone()
        assert bal is None or bal['quantity_on_hand'] == 0, (
            f"the forged movement was not applied as a ledger row, but the balance moved anyway: "
            f"{dict(bal) if bal else None}"
        )
    finally:
        b_conn.close()


def test_inventory_movement_ignores_hostile_payload_company_id(install_b):
    """A malicious/corrupt relay payload claims a DIFFERENT company_id than
    the receiving device's own. The applied row must be stamped with the
    RECEIVER's own company_id (the `local_company_id` argument), never the
    payload's -- both `inventory_movements.company_id` AND
    `inventory_balances.company_id` (the balance side-effect)."""
    b_conn = install_b()
    try:
        product_id = _seed_branch_and_product(b_conn)
        service_b = SyncService(client_factory=lambda: None, get_conn=install_b,
                                 local_company_id_provider=lambda: RECEIVER_COMPANY_ID)
        movement_uid = str(uuid.uuid4())
        hostile_ev = {
            "entity_type": "inventory_movement", "event_type": "create",
            "payload": {
                "uid": movement_uid, "product_id": product_id, "branch_uid": None,
                "movement_type": "opening_stock", "quantity": 50, "unit_cost": 0,
                "reference": "OPEN", "notes": None, "created_by": "System",
                "actor_user_uid": None, "terminal_id": None, "created_at_utc": None,
                # The hostile part -- an attacker's/another device's own company_id
                # riding along in the payload, exactly the shape the module
                # docstring's "Cross-device company_id bug fix" note warns about.
                "company_id": HOSTILE_COMPANY_ID,
            },
        }
        # Call is made with local_company_id=RECEIVER_COMPANY_ID (as
        # apply_pull_result always does -- it is derived from the RECEIVER's
        # own registry, never read out of the event); the payload's
        # "company_id" key must never override it.
        service_b._apply_event(b_conn, hostile_ev, local_company_id=RECEIVER_COMPANY_ID)
        b_conn.commit()

        mv_row = b_conn.execute(
            "SELECT company_id FROM inventory_movements WHERE uid=?", (movement_uid,)
        ).fetchone()
        assert mv_row is not None, "movement was not applied at all"
        assert mv_row["company_id"] == RECEIVER_COMPANY_ID, (
            f"movement was stamped with the payload's hostile company_id, not the receiver's own: "
            f"{mv_row['company_id']!r}"
        )

        bal_row = b_conn.execute(
            "SELECT company_id, quantity_on_hand FROM inventory_balances WHERE product_id=?", (product_id,)
        ).fetchone()
        assert bal_row is not None, "balance side-effect row was never created"
        assert bal_row["company_id"] == RECEIVER_COMPANY_ID, (
            f"balance row was stamped with the payload's hostile company_id: {bal_row['company_id']!r}"
        )
        # And it must be reachable under the RECEIVER's own company scope --
        # not invisible to it (the exact symptom the docstring's bug fix note
        # describes: a row stamped with the wrong company_id becomes
        # permanently invisible to that device's own WHERE company_id=? reads).
        assert bal_row["quantity_on_hand"] == 50.0
        under_hostile = b_conn.execute(
            "SELECT 1 FROM inventory_balances WHERE company_id=?", (HOSTILE_COMPANY_ID,)
        ).fetchone()
        assert under_hostile is None, "a row was filed under the hostile company_id at all"
    finally:
        b_conn.close()


def test_branch_ignores_hostile_payload_company_id(install_b):
    """Same claim, for the `branch` entity type -- a rename/create carrying
    a hostile `company_id` in its payload must land under the RECEIVER's own
    company_id, never the payload's, on both `create` and `update`."""
    b_conn = install_b()
    try:
        service_b = SyncService(client_factory=lambda: None, get_conn=install_b,
                                 local_company_id_provider=lambda: RECEIVER_COMPANY_ID)
        branch_uid = str(uuid.uuid4())
        hostile_ev = {
            "entity_type": "branch", "event_type": "create",
            "payload": {
                "uid": branch_uid, "name": "Attacker Branch", "address": "", "phone": "",
                "status": "active",
                "company_id": HOSTILE_COMPANY_ID,
            },
        }
        service_b._apply_event(b_conn, hostile_ev, local_company_id=RECEIVER_COMPANY_ID)
        b_conn.commit()

        row = b_conn.execute("SELECT company_id FROM branches WHERE uid=?", (branch_uid,)).fetchone()
        assert row is not None, "branch was not applied at all"
        assert row["company_id"] == RECEIVER_COMPANY_ID, (
            f"branch was stamped with the payload's hostile company_id, not the receiver's own: "
            f"{row['company_id']!r}"
        )

        under_hostile = b_conn.execute(
            "SELECT 1 FROM branches WHERE company_id=?", (HOSTILE_COMPANY_ID,)
        ).fetchone()
        assert under_hostile is None, "a branch was filed under the hostile company_id at all"
    finally:
        b_conn.close()


def test_a_negative_first_movement_seeds_the_balance_row_with_the_signed_value(install_b):
    """The upsert's INSERT half must seed `quantity_on_hand` with the
    movement's SIGNED quantity, and its DO UPDATE half must ADD the signed
    quantity -- proved with a NEGATIVE movement arriving FIRST, when no
    balance row exists yet for that `(product_id, branch_id)`.

    Why this case and not the positive one: the delivered suite's
    `test_a_movement_applied_on_b_updates_its_balance_and_creates_the_row_
    if_absent` only ever seeds a row with a POSITIVE opening quantity
    (+200), so an INSERT half that dropped the sign, stored an absolute
    value, or seeded 0 and relied on a later UPDATE would pass it
    unchanged. A receiver reaches THIS path whenever the first movement it
    ever hears about for a product/branch is stock going OUT -- a sale rung
    on the originating device before the opening-stock event happens to be
    pulled, or any batch that arrives out of order (`read_outbox` orders by
    rowid, which is emission order, not business order).

    Getting it wrong is silent: the ledger row lands correctly either way,
    so nothing raises -- but `compute_drift` (the Phase 3 GATE that refuses
    to advance `user_version`) would then read non-zero on that device
    forever, and a future migration would refuse to run on that install for
    a reason the shop cannot explain.

    This test exists because the disposable script that originally proved
    the claim (`_verify_signed_seed.py`, repo root) was deleted during
    cleanup before its claim had ever entered the permanent suite.
    """
    b_conn = install_b()
    try:
        product_id = _seed_branch_and_product(b_conn)
        service_b = SyncService(client_factory=lambda: None, get_conn=install_b,
                                local_company_id_provider=lambda: RECEIVER_COMPANY_ID)

        def _movement(movement_type, quantity):
            return {
                "entity_type": "inventory_movement", "event_type": "create",
                "payload": {
                    "uid": str(uuid.uuid4()), "product_id": product_id, "branch_uid": None,
                    "movement_type": movement_type, "quantity": quantity, "unit_cost": 0,
                    "reference": "SIGNED", "notes": None, "created_by": "System",
                    "actor_user_uid": None, "terminal_id": None, "created_at_utc": None,
                },
            }

        # No balance row exists yet -- this is the INSERT half of the upsert.
        service_b._apply_event(b_conn, _movement("sale_out", -7), local_company_id=RECEIVER_COMPANY_ID)
        b_conn.commit()

        seeded = b_conn.execute(
            "SELECT quantity_on_hand FROM inventory_balances "
            "WHERE company_id=? AND product_id=?", (RECEIVER_COMPANY_ID, product_id)
        ).fetchone()
        assert seeded is not None, "balance row was never created by a negative first movement"
        assert seeded["quantity_on_hand"] == -7.0, (
            "the INSERT half did not seed the SIGNED quantity: expected -7.0, got "
            f"{seeded['quantity_on_hand']} -- an absolute value or a dropped sign here "
            "leaves the receiver permanently drifted against its own ledger"
        )

        # The row now exists -- this exercises the DO UPDATE half, which must
        # ADD the signed quantity rather than replace it.
        service_b._apply_event(b_conn, _movement("purchase_in", 20), local_company_id=RECEIVER_COMPANY_ID)
        b_conn.commit()

        after = b_conn.execute(
            "SELECT quantity_on_hand FROM inventory_balances "
            "WHERE company_id=? AND product_id=?", (RECEIVER_COMPANY_ID, product_id)
        ).fetchone()["quantity_on_hand"]
        assert after == 13.0, (
            f"the DO UPDATE half did not ADD the signed quantity: expected 13.0, got {after}"
        )
    finally:
        b_conn.close()
