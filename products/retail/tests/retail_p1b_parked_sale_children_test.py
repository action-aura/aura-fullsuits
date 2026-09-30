"""
Aura Retail P1b round 3, point 3 -- the sync-review screen must be honest about a
sale that was set aside for good, and about what the engine still did for it.

Two halves, deliberately separate:

  A. THE SCREEN (route tests on hand-written quarantine rows, exact fields).
     A sale parked as `duplicate_document_number` never resolves: nothing changes
     the number it collides with. Its items and payments wait as
     `missing_parent:sale`, a reason that used to read "will fix itself" -- which
     is false for them. The route now says so: `retries_itself=false`,
     `parent_parked=true`, a plain "needs a person" note. The parked sale itself
     carries `stock_and_books_applied`, DERIVED from the database (P2S): False
     ("set aside with it") on a computer that holds no movement/journal for it,
     True ("WERE added") only where one really is there -- both are pinned.

  B. THE ENGINE PIN (two REAL computers, real relay, real sync engine). This
     half pins what `commercial_runtime/sync/sync_service.py::_apply_event`
     really does with a sale that was parked as `duplicate_document_number`.

     HISTORY -- read this before touching the test. Until the P2S "parked
     children" rule, the engine applied that sale's stock movement and its
     accounting journal on the receiving computer while the sale row itself, its
     items and its payments were parked: stock and books for a sale that did not
     exist there, which nobody could ever reconcile. This half used to pin THAT
     (and the screen's `stock_and_books_applied=true` wording in Part A repeats
     it). The rule now parks the movement and the journal WITH the sale
     (`missing_parent:sale`), so a parked sale leaves nothing behind but its four
     quarantine rows. This half therefore pins the NEW behaviour: no movement, no
     journal, and their quarantine rows. If a future engine change applies them
     again, THIS TEST GOES RED on purpose.

Run (repo venv):
    aura-fullsuits/.venv/Scripts/python.exe -m pytest \
        products/retail/tests/retail_p1b_parked_sale_children_test.py -v
"""
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_parkedkids_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True
app.config["PROPAGATE_EXCEPTIONS"] = False

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.identity import device_context  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.sync import sync_service  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

URL = '/api/sub/retail/sync-review'
NOTE_CHILD = 'This will not fix itself. It needs a person to look at the sale it belongs to.'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean():
    device_context._cached_device = None
    conn = get_retail_conn()
    try:
        conn.execute("DELETE FROM sync_apply_quarantine")
        conn.commit()
    finally:
        conn.close()
    yield
    device_context._cached_device = None


def _owner(monkeypatch):
    email = f"pk-{uuid.uuid4().hex[:10]}@test.local"
    cid = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), cid, "EMP-0001", email, hash_password("ParkedKidsPW1"), "admin", "active"))
    conn.commit()
    conn.close()
    client = app.test_client()
    assert client.post("/api/auth/login", json={"email": email, "password": "ParkedKidsPW1"}).status_code == 200
    monkeypatch.setattr(sync_service, 'local_company_id_from_registry', lambda: cid)
    return client


def _park(entity_type, reason, payload):
    conn = get_retail_conn()
    try:
        conn.execute(
            "INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason, detail) "
            "VALUES (?,?,?,?,?,?)",
            (f"e-{uuid.uuid4().hex[:8]}", entity_type, 'create', json.dumps(payload), reason, 'detail'))
        conn.commit()
    finally:
        conn.close()


def _items(client):
    r = client.get(URL)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['items']


def _by_kind(items, kind_code):
    return [i for i in items if i['kind_code'] == kind_code]


# ── A. the screen ───────────────────────────────────────────────────────────

def test_children_of_a_permanently_parked_sale_say_a_person_is_needed(monkeypatch):
    client = _owner(monkeypatch)
    _park('sale', 'duplicate_document_number', {'uid': 'sale-uid-1', 'sale_number': 'AAA1-000001', 'total': 5})
    _park('sale_item', 'missing_parent:sale', {'sale_uid': 'sale-uid-1', 'quantity': 1})
    _park('payment', 'missing_parent:sale', {'sale_uid': 'sale-uid-1', 'related_type': 'sale', 'amount': 5})
    items = _items(client)
    for kind in ('sale_item', 'payment'):
        [it] = _by_kind(items, kind)
        assert it['retries_itself'] is False, it
        assert it['parent_parked'] is True, it
        assert it['note'] == NOTE_CHILD, it
        assert 'set aside' in it['reason'] and 'same receipt number' in it['reason'], it


def _plant_movement(number, terminal, movement_type='sale_out'):
    """A stock movement really present on THIS computer, as the OLD engine left it
    for a set-aside sale. Product 1 is enough: the flag only looks at the
    (movement_type, reference, terminal_id) key. This fixture database has no
    products, and `inventory_movements.product_id` is a real foreign key, so a
    throwaway product 9001 is planted first (removed by `_planted_rows_cleaned`)."""
    conn = get_retail_conn()
    try:
        conn.execute("INSERT OR IGNORE INTO products (id, sku, name) VALUES (9001, 'PLANT-SKU', 'PLANT-product')")
        conn.execute("INSERT INTO inventory_movements (product_id, movement_type, quantity, reference, terminal_id) "
                     "VALUES (9001, ?, 1, ?, ?)", (movement_type, number, terminal))
        conn.commit()
    finally:
        conn.close()


def _plant_journal(kind, uid):
    """An accounting journal really present on THIS computer for the document."""
    conn = get_retail_conn()
    try:
        conn.execute("INSERT INTO gl_journals (company_id, business_date, reference, source_type, source_uid, currency) "
                     "VALUES (1, '2026-09-30', 'planted', ?, ?, 'JOD')", (kind, uid))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def _planted_rows_cleaned():
    yield
    conn = get_retail_conn()
    try:
        conn.execute("DELETE FROM inventory_movements WHERE reference LIKE 'PLANT-%'")
        # gl_journals is APPEND-ONLY (a database trigger aborts DELETE), so the
        # planted journal cannot be removed. It is harmless: its source_uid
        # ('sale-uid-p2') is used by no other test and the fixture DB is a throwaway.
        conn.execute("DELETE FROM products WHERE id=9001 AND sku='PLANT-SKU'")
        conn.commit()
    finally:
        conn.close()


def test_a_parked_sale_says_its_stock_and_books_were_set_aside_with_it(monkeypatch):
    """DELIBERATE UPDATE (P2S, crew Y's 52aa477c + `_sync_review_applied_uids`).
    This test used to assert `stock_and_books_applied is True` and the words
    'WERE added' for ANY sale parked as a repeated number. The server now DERIVES
    the flag from the database, and a computer that holds no movement and no
    journal for the sale (which is what the current engine leaves, since it parks
    them with the sale) must say the opposite: False, and 'set aside with it'.

    WHAT THE OLD ASSERTION CAN NO LONGER CATCH: it could not tell a computer that
    HAD applied the stock and books from one that had not, so it would have passed
    a screen that told the owner 'stock and accounts are ahead of the takings' on a
    computer where nothing was added. That direction is now guarded by the
    planted-row tests below (the flag must be True when the rows are really there)."""
    client = _owner(monkeypatch)
    _park('sale', 'duplicate_document_number', {'uid': 'sale-uid-2', 'sale_number': 'X-1', 'total': 5,
                                               'terminal_id': 'other-till'})
    [sale] = _by_kind(_items(client), 'sale')
    assert sale['stock_and_books_applied'] is False, sale
    assert sale['parent_parked'] is False, sale
    assert 'set aside with it' in sale['note'] and 'WERE added' not in sale['note'], sale
    assert 'stock' in sale['note'] and 'accounting' in sale['note'], sale


def test_a_parked_sale_whose_movement_really_was_applied_says_so(monkeypatch, _planted_rows_cleaned):
    """The other half of the derived flag: a computer that DID apply the sale's
    stock movement (the old engine) must still be told 'WERE added'."""
    client = _owner(monkeypatch)
    _plant_movement('PLANT-1', 'other-till')
    _park('sale', 'duplicate_document_number', {'uid': 'sale-uid-p1', 'sale_number': 'PLANT-1', 'total': 5,
                                               'terminal_id': 'other-till'})
    [sale] = _by_kind(_items(client), 'sale')
    assert sale['stock_and_books_applied'] is True, sale
    assert 'WERE added' in sale['note'] and 'set aside with it' not in sale['note'], sale


def test_a_parked_sale_whose_journal_really_was_applied_says_so(monkeypatch, _planted_rows_cleaned):
    client = _owner(monkeypatch)
    _plant_journal('sale', 'sale-uid-p2')
    _park('sale', 'duplicate_document_number', {'uid': 'sale-uid-p2', 'sale_number': 'PLANT-2', 'total': 5,
                                               'terminal_id': 'other-till'})
    [sale] = _by_kind(_items(client), 'sale')
    assert sale['stock_and_books_applied'] is True, sale
    assert 'WERE added' in sale['note'], sale


def test_the_winning_computers_movement_with_the_same_number_is_not_the_losers(monkeypatch, _planted_rows_cleaned):
    """Deny-half of the movement key: the computer that WON the number holds a
    movement with the SAME reference but its OWN terminal. That must not make the
    loser's parked sale read as applied, or every clash would claim it."""
    client = _owner(monkeypatch)
    _plant_movement('PLANT-3', 'the-winning-till')
    _park('sale', 'duplicate_document_number', {'uid': 'sale-uid-p3', 'sale_number': 'PLANT-3', 'total': 5,
                                               'terminal_id': 'the-losing-till'})
    [sale] = _by_kind(_items(client), 'sale')
    assert sale['stock_and_books_applied'] is False, sale
    assert 'set aside with it' in sale['note'], sale


def test_children_of_a_sale_that_is_not_parked_keep_saying_they_retry(monkeypatch):
    """The other direction: a child waiting for a sale that simply has not
    arrived yet really does fix itself. It must NOT be told it needs a person."""
    client = _owner(monkeypatch)
    _park('sale_item', 'missing_parent:sale', {'sale_uid': 'not-parked', 'quantity': 1})
    _park('payment', 'missing_parent:sale', {'sale_uid': 'not-parked', 'related_type': 'sale', 'amount': 1})
    _park('sale', 'duplicate_document_number', {'uid': 'a-different-sale', 'sale_number': 'X-2', 'total': 1})
    items = _items(client)
    for kind in ('sale_item', 'payment'):
        [it] = _by_kind(items, kind)
        assert it['retries_itself'] is True, it
        assert it['parent_parked'] is False and it['note'] is None, it


def test_a_sale_parked_for_another_reason_is_not_marked_applied(monkeypatch):
    """`stock_and_books_applied` is only for the duplicate-number case the write-up
    covers; any other parked sale must not inherit the claim, and its children
    are not treated as permanently parked."""
    client = _owner(monkeypatch)
    _park('sale', 'some_other_reason', {'uid': 'sale-uid-3', 'sale_number': 'X-3', 'total': 2})
    _park('sale_item', 'missing_parent:sale', {'sale_uid': 'sale-uid-3', 'quantity': 1})
    items = _items(client)
    [sale] = _by_kind(items, 'sale')
    assert sale['stock_and_books_applied'] is None and sale['note'] is None, sale
    [kid] = _by_kind(items, 'sale_item')
    assert kid['parent_parked'] is False and kid['retries_itself'] is True, kid


def test_every_other_row_carries_null_defaults(monkeypatch):
    client = _owner(monkeypatch)
    _park('doc_series', 'duplicate_code', {'code': 'AAA1-P'})
    [book] = _items(client)
    assert book['parent_parked'] is False and book['stock_and_books_applied'] is None and book['note'] is None, book


def test_a_payment_that_is_not_for_a_sale_is_not_swept_up(monkeypatch):
    """A payment whose payload carries the parked sale's uid but is not a sale
    payment child must still be judged by the same rule as any child -- here the
    uid matches, so it IS a child of the parked sale. A payment carrying NO
    sale_uid (a supplier payment) must be left alone."""
    client = _owner(monkeypatch)
    _park('sale', 'duplicate_document_number', {'uid': 'sale-uid-4', 'sale_number': 'X-4', 'total': 2})
    _park('payment', 'missing_parent:purchase_order', {'related_type': 'purchase_order', 'amount': 3})
    [pay] = _by_kind(_items(client), 'payment')
    assert pay['parent_parked'] is False, pay


# ── B. the engine pin ───────────────────────────────────────────────────────

def test_engine_pin_a_parked_sale_takes_its_stock_and_books_with_it(tmp_path):
    """Two real computers both number their first sale AAA1-000001 (the clash
    that retail_p1b_numbering_twodevice_test.py T5 pins; reachable here only with
    READABLE_NUMBERS_AVAILABLE flipped inside each device process). After they
    sync, on the computer that lost the clash:

      * the other computer's SALE is parked `duplicate_document_number`;
      * its items/payments are parked `missing_parent:sale`;
      * its stock movement is parked `missing_parent:sale` and NOT applied (no
        movement exists for the product only the parked sale sold);
      * its accounting journal is parked `missing_parent:sale` and NOT applied.

    WHAT THIS CHANGE CAN NO LONGER CATCH (stated on purpose, per the rule that a
    changed pin says what it lost): the old pin failed if the engine STOPPED
    applying a parked sale's stock and journal. That behaviour was the defect --
    stock and books for a document that does not exist on this computer -- so
    losing that guard is the point, and a future engine that regresses to it is
    caught by the opposite assertions below. What the old pin protected that the
    new one does not: the receiving computer's stock count now leaves out the
    losing sale's units until a person resolves the clash. That is the correct
    trade (a count that is short by one visible, parked sale beats a count that is
    off by a sale nobody can see), but it means the stock is not 'as sold' on this
    computer in the meantime. `sale_number` clash resolution is unchanged."""
    from _accept_harness import run_accept_device
    py = sys.executable
    TA, TB = 'pk-till-a-0000-0000-000000000001', 'pk-till-b-0000-0000-000000000002'
    flip = {"op": "set_readable_available", "value": True}

    def dev(d, relay, acts):
        return run_accept_device(py, d, relay, [flip] + list(acts), timeout=240)[1:]

    term_of = {}

    def login(term, who):
        term = term_of.get(a if who is shop else b, term)
        return [{"op": "set_terminal_id", "terminal_id": term},
                {"op": "login", "email": who["email"], "password": who["password"]}]

    a, b, relay = tmp_path / 'a', tmp_path / 'b', tmp_path / 'relay.db'
    [shop] = dev(a, relay, [{"op": "create_shop", "price": 5.0, "initial_stock": 100}])
    [boot] = dev(b, relay, [{"op": "bootstrap"}])
    [_, _, prod] = dev(b, relay, [{"op": "set_terminal_id", "terminal_id": TB},
                                  {"op": "login", "email": boot["email"], "password": boot["password"]},
                                  {"op": "create_product", "name": "B only widget", "initial_stock": 50}])
    [br] = dev(b, relay, [{"op": "list_branches", "company_id": boot["company_id"]}])
    bbid = br["branches"][0]["id"]
    # v42: switching readable numbers on is main-computer-only. Make each
    # computer main of its OWN registry (planted ADMIN_CREATED row -- the
    # harness inserts the owner directly, skipping the founding route that
    # writes it -- then announce, then claim_main).
    for d, who in ((a, shop), (b, boot)):
        cl = dev(d, relay, [
            {"op": "reg_exec",
             "sql": "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id) "
                    "VALUES (?, ?, NULL, 'ADMIN_CREATED', 'user', ?)",
             "params": ["founder-" + who["company_id"], who["company_id"], "founder"]},
            {"op": "login", "email": who["email"], "password": who["password"]},
            {"op": "announce", "reason": "boot"},
            {"op": "claim_main"},
        ])
        assert cl[-1]["status_code"] == 200, f"could not become main: {cl[-1]}"
        # a till number can only go to a computer the registry lists, so this
        # computer's till identity from here on IS its registry device id
        term_of[d] = cl[-2]["device"]["id"]
    on = {"op": "numbering_set", "branch_code": "AAA", "readable_enabled": True, "till_number": 1}
    ron_a = dev(a, relay, login(TA, shop) + [on])[-1]
    ron_b = dev(b, relay, login(TB, boot) + [on])[-1]
    assert ron_a["status_code"] == 200, ron_a
    assert ron_b["status_code"] == 200, ron_b
    ra = dev(a, relay, login(TA, shop) + [{"op": "cash_session_open", "branch_id": shop["branch_id"]},
                                          {"op": "sell", "product_id": shop["product_id"], "quantity": 1}])
    rb = dev(b, relay, login(TB, boot) + [{"op": "cash_session_open", "branch_id": bbid},
                                          {"op": "sell", "product_id": prod["product_id"], "quantity": 1}])
    assert ra[-1]["body"]["data"]["sale_number"] == rb[-1]["body"]["data"]["sale_number"], \
        "the two computers did not clash on the sale number -- the scenario no longer holds"
    number = ra[-1]["body"]["data"]["sale_number"]
    dev(b, relay, [{"op": "push"}])
    dev(a, relay, [{"op": "push"}])
    dev(a, relay, login(TA, shop) + [{"op": "pull"}])
    dev(b, relay, login(TB, boot) + [{"op": "pull"}])

    # Look at A (which sold the shop's own product). B's sale arrived and was parked.
    def select(sql, params=()):
        [r] = dev(a, relay, [{"op": "raw_select", "sql": sql, "params": list(params)}])
        return r["rows"]

    q = select("SELECT entity_type, reason, payload FROM sync_apply_quarantine")
    parked_sales = [r for r in q if r["entity_type"] == "sale"]
    assert len(parked_sales) == 1 and parked_sales[0]["reason"] == "duplicate_document_number", q
    parked_uid = json.loads(parked_sales[0]["payload"])["uid"]
    kids = [r for r in q if r["entity_type"] in ("sale_item", "payment")
            and json.loads(r["payload"]).get("sale_uid") == parked_uid]
    assert {k["entity_type"] for k in kids} == {"sale_item", "payment"}, q
    assert all(k["reason"] == "missing_parent:sale" for k in kids), kids

    # The parked sale's stock and books were parked WITH it, not applied.
    # (1) its movement is quarantined as a child of the sale...
    parked_kinds = {(r["entity_type"], r["reason"]) for r in q}
    assert ("inventory_movement", "missing_parent:sale") in parked_kinds, (
        f"the parked sale's stock movement is not quarantined: {sorted(parked_kinds)}")
    assert ("gl_journal", "missing_parent:sale") in parked_kinds, (
        f"the parked sale's journal is not quarantined: {sorted(parked_kinds)}")
    # (2) ...and NOT applied: no movement for the product only that sale sold
    # (products carry no uid column here; the name is unique in this fixture).
    moved = select(
        "SELECT m.movement_type, m.quantity FROM inventory_movements m "
        "JOIN products p ON p.id = m.product_id WHERE p.name = 'B only widget' "
        "AND m.reference = ?", (number,))
    assert not moved, (
        "the parked sale's stock movement WAS applied on the receiving computer -- "
        f"stock for a sale that does not exist here: {moved}")
    # (3) no journal for it either. `source_id` is NULL when the sale row is
    # missing and the journal's reference is the sale number; A's own journal for
    # ITS sale (same number) has a real source_id and is expected to be there.
    journals = select("SELECT source_type, source_id FROM gl_journals WHERE reference = ?", (number,))
    b_journals = [j for j in journals if j["source_id"] is None]
    assert not b_journals, (
        "the parked sale's accounting journal WAS applied on the receiving "
        f"computer -- books for a sale that does not exist here: {journals}")
    assert journals, "A's own journal for its own sale vanished -- the negative checks above prove nothing"
