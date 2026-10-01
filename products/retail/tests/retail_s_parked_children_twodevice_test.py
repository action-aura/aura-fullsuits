"""
Track S, P2S crew Y -- a sale set aside for a repeated receipt number must take
its whole family with it, and leave the books and the stock of BOTH computers
adding up. Two REAL computers, real relay, real sync engine, one process per
step (the harness).

The unit tests (commercial_runtime/sync/tests/test_parked_children_apply.py)
pin the rule inside one interpreter. This file pins what a shopkeeper would
actually be exposed to if the rule were wrong, measured on each computer after
a real clash:

  * stock: every product's balance equals its opening count minus the units in
    the sales that are PRESENT on that computer (a parked sale's units are not
    subtracted -- and not applied), and the stock check (`compute_drift`) finds
    nothing to complain about;
  * books: the revenue and the cash the ledger holds equal the totals of the
    sales that are present -- no journal for a sale that is not here;
  * the set-aside family is exactly the sale, its items, its payments, its stock
    movement and its journal -- nothing extra, nothing missing;
  * the sync-review screen's "money at risk" equals the total of the parked
    sales, measured against the OTHER computer's own sale table (derived, not
    typed).

HOW THE CLASH IS MADE (v42). Plain sale numbers are per-terminal unique now and
switching readable numbers on is a decision only the MAIN computer of a shop may
make (POST /numbering is owner + main only). So each computer is made main of
its OWN registry (`World.make_main`: planted ADMIN_CREATED row, announce,
claim_main) and told to number as book `AAA`, till 1. Each device process also
flips the shipped-build switch (`set_readable_available`), as
retail_p1b_parked_sale_children_test.py Part B does. Both computers then number
their first sale AAA1-000001 offline. Each computer therefore parks the OTHER
one's sale, which lets one scenario check both directions. Two mains in one
registry is impossible by design; the two computers here are deliberately two
separate registries meeting on one relay, which is the only way left to
manufacture the repeated number the parking rule exists for.

Run (repo venv, one file per process):
    aura-fullsuits/.venv/Scripts/python.exe -m pytest \
        products/retail/tests/retail_s_parked_children_twodevice_test.py -v
"""
import json
import sys
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(PRODUCT_DIR / "backend"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _accept_harness import run_accept_device  # noqa: E402

TA = "pk2-till-a-0000-0000-000000000001"
TB = "pk2-till-b-0000-0000-000000000002"
OPENING_A, OPENING_B = 100, 50
PRICE_A, PRICE_B = 5.0, 100.0
FLIP = {"op": "set_readable_available", "value": True}
# The whole family a parked sale leaves behind, and nothing else.
FAMILY = {"sale", "sale_item", "payment", "inventory_movement", "gl_journal"}


class World:
    """Two computers after a forced receipt-number clash and a full sync."""

    def __init__(self, tmp_path):
        self.py = sys.executable
        self.dirs = {"a": tmp_path / "a", "b": tmp_path / "b"}
        self.relay = tmp_path / "relay.db"
        self.device_id = {}

    def dev(self, who, actions):
        return run_accept_device(self.py, self.dirs[who], self.relay,
                                 [FLIP] + list(actions), timeout=240)[1:]

    def login(self, who):
        term, acct = (TA, self.shop) if who == "a" else (TB, self.boot)
        term = self.device_id.get(who, term)
        return [{"op": "set_terminal_id", "terminal_id": term},
                {"op": "login", "email": acct["email"], "password": acct["password"]}]

    def select(self, who, sql, params=()):
        [r] = self.dev(who, [{"op": "raw_select", "sql": sql, "params": list(params)}])
        return r["rows"]

    def cid(self, who):
        return (self.shop if who == "a" else self.boot)["company_id"]

    def make_main(self, who):
        """Make `who` the MAIN computer of its own registry.

        v42 lets only the main computer switch readable receipt numbers on
        (POST /numbering is owner + main only). The harness founds a shop by
        inserting the owner row directly, which skips the ADMIN_CREATED audit
        row the real founding route writes -- the row that tells the device
        registry this computer founded the shop -- so plant it, announce, and
        claim the main role, exactly as retail_s_devices_twodevice_test.py does.
        """
        acct = self.shop if who == "a" else self.boot
        out = self.dev(who, [
            {"op": "reg_exec",
             "sql": "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id) "
                    "VALUES (?, ?, NULL, 'ADMIN_CREATED', 'user', ?)",
             "params": [f"founder-{who}", acct["company_id"], f"founder-{who}"]},
            {"op": "login", "email": acct["email"], "password": acct["password"]},
            {"op": "announce", "reason": "boot"}, {"op": "claim_main"}])
        announce, claim = out[-2], out[-1]
        assert claim["status_code"] == 200, f"computer {who} could not become main: {claim}"
        # A till number can only be given to a computer the registry lists, so
        # from here on this computer's till identity IS its registry device id.
        self.device_id[who] = announce["device"]["id"]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    w = World(tmp_path_factory.mktemp("parkedkids2"))
    [w.shop] = w.dev("a", [{"op": "create_shop", "price": PRICE_A, "initial_stock": OPENING_A}])
    [w.boot] = w.dev("b", [{"op": "bootstrap"}])
    [_, _, prod] = w.dev("b", [{"op": "set_terminal_id", "terminal_id": TB},
                               {"op": "login", "email": w.boot["email"], "password": w.boot["password"]},
                               {"op": "create_product", "name": "B only widget",
                                "price": PRICE_B, "initial_stock": OPENING_B}])
    assert prod["status_code"] == 200, prod
    [br] = w.dev("b", [{"op": "list_branches", "company_id": w.boot["company_id"]}])
    bbid = br["branches"][0]["id"]
    w.make_main("a")
    w.make_main("b")
    on = {"op": "numbering_set", "branch_code": "AAA", "readable_enabled": True, "till_number": 1}
    ra_on = w.dev("a", w.login("a") + [on])[-1]
    rb_on = w.dev("b", w.login("b") + [on])[-1]
    assert ra_on["status_code"] == 200, ra_on
    assert rb_on["status_code"] == 200, rb_on
    ra = w.dev("a", w.login("a") + [{"op": "cash_session_open", "branch_id": w.shop["branch_id"]},
                                    {"op": "sell", "product_id": w.shop["product_id"], "quantity": 1}])
    rb = w.dev("b", w.login("b") + [{"op": "cash_session_open", "branch_id": bbid},
                                    {"op": "sell", "product_id": prod["product_id"], "quantity": 1}])
    assert ra[-1]["status_code"] == 200 and rb[-1]["status_code"] == 200, (ra[-1], rb[-1])
    w.number = ra[-1]["body"]["data"]["sale_number"]
    assert w.number == rb[-1]["body"]["data"]["sale_number"], \
        "the two computers did not clash on the sale number -- the scenario no longer holds"
    w.dev("b", [{"op": "push"}])
    w.dev("a", [{"op": "push"}])
    # Two full rounds: the second proves the parked family is stable on replay
    # and that nothing drifts when the same events are seen again.
    for _ in range(2):
        w.dev("a", w.login("a") + [{"op": "pull"}, {"op": "push"}])
        w.dev("b", w.login("b") + [{"op": "pull"}, {"op": "push"}])
    return w


@pytest.mark.parametrize("who", ["a", "b"])
def test_the_stock_check_finds_no_drift_on_either_computer(world, who):
    [r] = world.dev(who, [{"op": "compute_drift", "company_id": world.cid(who)}])
    assert r["drift"] == [], f"computer {who}: stock disagrees with its own ledger: {r['drift']}"


@pytest.mark.parametrize("who", ["a", "b"])
def test_each_products_stock_is_its_opening_count_minus_the_sales_that_are_present(world, who):
    rows = world.select(
        who,
        "SELECT p.name AS name, "
        "  (SELECT COALESCE(SUM(b.quantity_on_hand),0) FROM inventory_balances b WHERE b.product_id = p.id) AS on_hand, "
        "  (SELECT COALESCE(SUM(si.quantity),0) FROM sale_items si JOIN sales s ON s.id = si.sale_id "
        "    WHERE si.product_id = p.id) AS sold "
        "FROM products p ORDER BY p.name")
    by_name = {r["name"]: r for r in rows}
    opening = {"B only widget": OPENING_B, "Stock Sync Widget": OPENING_A}
    assert set(by_name) == set(opening), by_name
    for name, start in opening.items():
        r = by_name[name]
        assert r["on_hand"] == start - r["sold"], (
            f"computer {who}: {name} shows {r['on_hand']} on hand; opening {start} minus "
            f"{r['sold']} unit(s) in sales that are here should be {start - r['sold']}")
    # Exactly one computer sold each product: the OTHER computer's sale is
    # parked, so on every computer only that computer's own product moved.
    own = "Stock Sync Widget" if who == "a" else "B only widget"
    other = "B only widget" if who == "a" else "Stock Sync Widget"
    assert by_name[own]["sold"] == 1 and by_name[other]["sold"] == 0, by_name


@pytest.mark.parametrize("who", ["a", "b"])
def test_the_books_hold_exactly_the_sales_that_are_present(world, who):
    [sales] = world.select(who, "SELECT COUNT(*) AS n, COALESCE(SUM(total),0) AS total FROM sales")
    assert sales["n"] == 1, f"computer {who} should hold only its own sale: {sales}"
    [gl] = world.select(
        who,
        "SELECT "
        " COALESCE(SUM(CASE WHEN a.code = '4000' THEN l.credit - l.debit END),0) AS revenue, "
        " COALESCE(SUM(CASE WHEN a.code = '1000' THEN l.debit - l.credit END),0) AS cash "
        "FROM gl_journal_lines l JOIN gl_accounts a ON a.id = l.account_id "
        "JOIN gl_journals j ON j.id = l.journal_id WHERE j.source_type = 'sale'")
    assert gl["revenue"] == pytest.approx(sales["total"]), (
        f"computer {who}: revenue in the books {gl['revenue']} vs sales present {sales['total']}")
    assert gl["cash"] == pytest.approx(sales["total"]), (
        f"computer {who}: cash in the books {gl['cash']} vs sales present {sales['total']}")
    # And no journal or stock movement points at a sale this computer does not hold.
    # A sale's stock movement is written as 'sale_out' (not 'sale'). This query
    # used to say 'sale', which no code path writes, so its movements count was
    # always 0 and the check could never fail. The non-vacuity assertion right
    # below is what stops that recurring: it insists the detector SEES this
    # computer's own sale movement, so a wrong type name goes red instead of
    # silently counting nothing.
    [seen] = world.select(
        who, "SELECT COUNT(*) AS n FROM inventory_movements WHERE movement_type = 'sale_out'")
    assert seen["n"] >= 1, (
        f"computer {who}: no 'sale_out' movement found at all -- the orphan check below "
        "would be counting nothing")
    [orph] = world.select(
        who,
        "SELECT "
        " (SELECT COUNT(*) FROM gl_journals j WHERE j.source_type = 'sale' "
        "    AND (j.source_id IS NULL OR j.source_id NOT IN (SELECT id FROM sales))) AS journals, "
        " (SELECT COUNT(*) FROM inventory_movements m WHERE m.movement_type = 'sale_out' "
        "    AND (m.reference IS NULL OR m.reference NOT IN (SELECT sale_number FROM sales))) AS movements")
    assert orph == {"journals": 0, "movements": 0}, f"computer {who}: {orph}"


@pytest.mark.parametrize("who", ["a", "b"])
def test_the_set_aside_family_is_exactly_the_sale_and_what_belongs_to_it(world, who):
    q_all = world.select(who, "SELECT entity_type, reason, payload FROM sync_apply_quarantine")
    # The scenario itself makes both computers create a receipt book coded AAA
    # (that is how the two first sales get the same number), so the OTHER
    # computer's book is also set aside as a repeated code. That is the cause of
    # the clash, not part of the sale's family; it is pinned exactly here so
    # nothing else can hide behind the exclusion.
    side = {(r["entity_type"], r["reason"]) for r in q_all if r["entity_type"] == "doc_series"}
    # S-AUTH (AUDIT-033): each computer here believed it was main of its OWN registry
    # and neither knows the other is, so the other's book is refused at the authority
    # gate (`needs_main_computer`) instead of reaching the duplicate-code check. Either
    # way it is parked and applied nowhere. WHAT THIS CAN NO LONGER CATCH: which of the
    # two reasons parked it (the duplicate-code path is pinned in
    # retail_doc_series_device_isolation_test.py, where the receiver knows the sender is main).
    assert side <= {("doc_series", "duplicate_series_code"), ("doc_series", "needs_main_computer")}, side
    # Since 1e7194cb a till number is only accepted from the main computer, and
    # the two computers here are two separate first installs that each handed
    # THEMSELVES till 1 -- so each parks the other's allocation, because the
    # sender is not the main computer it knows about. Same shape as the
    # receipt book above: a side effect of the scenario's two independent
    # installs, not part of the sale's family. Pinned exactly: one row per
    # computer, and only that reason, so a different till_allocation refusal
    # (or a second row) cannot hide behind this exclusion.
    # WHAT THIS CAN NO LONGER CATCH: a till_allocation being parked WRONGLY
    # for this same reason. The till-number tests
    # (test_branch_alias_till_allocation_apply.py) own that behaviour; this
    # test owns only "no sale-family row is missing or foreign".
    tills = [(r["entity_type"], r["reason"]) for r in q_all if r["entity_type"] == "till_allocation"]
    # S-AUTH (AUDIT-033): the SIGNATURE now says who sent the allocation, and neither
    # computer knows the other is main, so the authority gate refuses it first
    # (`needs_main_computer`) -- before the older payload-based epoch/author check
    # (`till_allocation_not_from_main`) is ever reached. One row, one of those two
    # reasons. WHAT THIS CAN NO LONGER CATCH: which of the two refused it here; the
    # older check keeps its own tests in test_branch_alias_till_allocation_apply.py.
    assert len(tills) == 1 and tills[0][1] in ("till_allocation_not_from_main", "needs_main_computer"), (
        f"computer {who}: {tills}")
    q = [r for r in q_all if r["entity_type"] not in ("doc_series", "till_allocation")]
    kinds = {r["entity_type"] for r in q}
    assert kinds == FAMILY, f"computer {who}: parked kinds {sorted(kinds)}, expected {sorted(FAMILY)}"
    [sale] = [r for r in q if r["entity_type"] == "sale"]
    assert sale["reason"] == "duplicate_document_number", sale
    uid = json.loads(sale["payload"])["uid"]
    for r in q:
        if r["entity_type"] == "sale":
            continue
        assert r["reason"] == "missing_parent:sale", f"{r['entity_type']}: {r['reason']}"
        # items and payments name their parent as `sale_uid`; the stock
        # movement and the journal name their SOURCE document as `source_uid`.
        key = "sale_uid" if r["entity_type"] in ("sale_item", "payment") else "source_uid"
        assert json.loads(r["payload"]).get(key) == uid, (
            f"{r['entity_type']} is parked but does not belong to the parked sale")


def test_the_screens_money_at_risk_is_the_total_of_the_parked_sales(world):
    for who, other in (("a", "b"), ("b", "a")):
        # what the OTHER computer's own sale table says its sale was worth
        [theirs] = world.select(other, "SELECT COALESCE(SUM(total),0) AS total FROM sales")
        [r] = world.dev(who, world.login(who) + [{"op": "sync_review"}])[-1:]
        assert r["status_code"] == 200, r
        s = r["body"]["data"]["summary"]
        assert s["sales_count"] == 1, (who, s)
        assert s["sales_total"] == pytest.approx(theirs["total"]), (
            f"computer {who}: the screen says {s['sales_total']} is set aside; "
            f"the other computer's sale is worth {theirs['total']}")


# AUDIT-033 / S-AUTH: authority events (users, devices, the main-computer
# assignment, till allocations, numbering books, release acks) are refused unless
# a member of the shop signed them. Every simulated computer here is a real
# process with its own app-data directory, so each one is given what a real
# activated till has: a device key, an Owner-signed lease and a trust store (see
# `_provision_signed_identity` in _accept_device.py). Signing and verification are
# the production code path reading that app data -- nothing is stubbed.
#
# MODULE-scoped, unlike the sibling files: this file's `world` fixture builds both
# computers once for the whole module, so the environment must already be set then.
@pytest.fixture(scope="module", autouse=True)
def _real_signing_identities():
    mp = pytest.MonkeyPatch()
    mp.setenv("AURA_ACCEPT_SIGNED", "1")
    mp.setenv("AURA_ACCEPT_SIGNED_LICENCE", "LIC-ACCEPT-SIGNED")
    yield
    mp.undo()
