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
     says `stock_and_books_applied=true`.

  B. THE ENGINE PIN (two REAL computers, real relay, real sync engine). The
     screen's `stock_and_books_applied` claim is a claim about
     `commercial_runtime/sync/sync_service.py::_apply_event`, which we do NOT
     change here (Track S owns its apply order). This half PROVES the claim on
     rows the engine really wrote: a parked sale's stock movement and its
     accounting journal ARE applied on the receiving computer, while its items and
     payments park as `missing_parent:sale`. If a future engine change groups a
     sale with its stock/journal (so they park together), THIS TEST GOES RED on
     purpose -- and the screen's wording (`stock_and_books_applied`, the note)
     must be revisited in the same change. That is its whole job.

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


def test_a_parked_sale_says_its_stock_and_books_were_applied(monkeypatch):
    client = _owner(monkeypatch)
    _park('sale', 'duplicate_document_number', {'uid': 'sale-uid-2', 'sale_number': 'X-1', 'total': 5})
    [sale] = _by_kind(_items(client), 'sale')
    assert sale['stock_and_books_applied'] is True, sale
    assert sale['parent_parked'] is False, sale
    assert 'stock' in sale['note'] and 'WERE added' in sale['note'], sale
    assert 'accounting' in sale['note'], sale


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

def test_engine_pin_a_parked_sales_stock_and_books_are_applied_but_its_children_park(tmp_path):
    """Two real computers both number their first sale AAA1-000001 (the clash
    that retail_p1b_numbering_twodevice_test.py T5 pins; reachable here only with
    READABLE_NUMBERS_AVAILABLE flipped inside each device process). After they
    sync, on the computer that lost the clash:

      * the other computer's SALE is parked `duplicate_document_number`;
      * its items/payments are parked `missing_parent:sale`;
      * BUT its stock movement is applied (the receiving computer holds a movement
        for a product that only the parked sale sold), and
      * BUT its accounting journal is applied (a gl_journals row whose source is
        the parked sale exists).

    This is the engine behaviour the screen's `stock_and_books_applied` and the
    "needs a person" note describe. Go RED = the engine changed = revisit both."""
    from _accept_harness import run_accept_device
    py = sys.executable
    TA, TB = 'pk-till-a-0000-0000-000000000001', 'pk-till-b-0000-0000-000000000002'
    flip = {"op": "set_readable_available", "value": True}

    def dev(d, relay, acts):
        return run_accept_device(py, d, relay, [flip] + list(acts), timeout=240)[1:]

    def login(term, who):
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
    on = {"op": "numbering_set", "branch_code": "AAA", "readable_enabled": True, "till_number": 1}
    assert dev(a, relay, login(TA, shop) + [on])[-1]["status_code"] == 200
    assert dev(b, relay, login(TB, boot) + [on])[-1]["status_code"] == 200
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

    # ...and yet its stock and books went in. (1) a movement for B's product,
    # a product only the parked sale sold.
    # (products carry no uid column here; the name is unique in this fixture)
    moved = select(
        "SELECT m.movement_type, m.quantity FROM inventory_movements m "
        "JOIN products p ON p.id = m.product_id WHERE p.name = 'B only widget' "
        "AND m.reference = ?", (number,))
    assert moved and moved[0]["quantity"] != 0, (
        "the parked sale's stock movement was NOT applied -- the engine now keeps a "
        f"sale and its stock together; revisit stock_and_books_applied: {moved}")
    # (2) an accounting journal for the parked sale (source_id is left NULL when
    # the sale row is missing; the journal's reference is the sale number).
    journals = select("SELECT source_type, source_id FROM gl_journals WHERE reference = ?", (number,))
    b_journals = [j for j in journals if j["source_id"] is None]
    assert b_journals, (
        "no journal arrived for the parked sale (or it was tied to a local sale) -- "
        f"the engine changed; revisit stock_and_books_applied: {journals}")
