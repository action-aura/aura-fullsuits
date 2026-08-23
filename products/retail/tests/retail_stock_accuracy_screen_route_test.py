"""
Aura Retail -- the Stock accuracy screen's DATA CONTRACT (Phase 3,
docs/launch-readiness/phase3-ledger-truth.md).

`GET /api/sub/retail/inventory/reconciliation` grew two fields for the screen
that sits on it, and this file is why they are safe to rely on. Both exist to
close a specific hole, and neither is decoration:

  pairs_examined       HOW MUCH WAS LOOKED AT. Without it, `drift_count: 0` is
                       the identical response from a healthy 500-product shop
                       and from a company with no stock records at all, so the
                       screen's empty state would be asserting an OUTCOME
                       ("your stock is accurate") on evidence that only says a
                       query returned nothing. That is the same defect this
                       programme keeps shipping, wearing a UI costume.

  repair_confirmation  WHAT THE REPAIR TWIN WILL DEMAND BACK. The token embeds
                       the company id and NO surface in
                       products/retail/frontend knows the company id (nor does
                       /api/auth/session return it), so before this the repair
                       route was unreachable from any frontend at all. The
                       token is not an authorization secret -- see
                       test_the_token_is_scoped_to_the_caller_s_own_company,
                       which is the assertion that keeps that true.

THE THING THIS FILE IS REALLY GUARDING is the seam between the route and
core/retail/stock_reconciliation.py. The route derives BOTH numbers from one
compute_drift() call, so `pairs_examined >= drift_count` holds by construction
rather than by two queries agreeing. The alternative -- a second, hand-written
COUNT over the same UNION -- would put "which (product, branch) pairs exist"
in two files, and the copy in retail_api.py would go quietly wrong the first
time the reconciliation module widened its key set. The tests below pin the
seam from both sides: differentially against the real module on a real
database, and structurally by watching what the route actually calls.

Self-contained bootstrap, matching every other file in this directory (no
shared conftest.py exists here). Run:

    pytest products/retail/tests/retail_stock_accuracy_screen_route_test.py -v
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
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_stkascreen_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from core.retail import stock_reconciliation  # noqa: E402
from api import retail_api as _retail_api  # noqa: E402

REPORT = "/api/sub/retail/inventory/reconciliation"
REPAIR = "/api/sub/retail/inventory/reconciliation/repair"


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers ────────────────────────────────────────────────────

def _new_company(role="admin"):
    email = f"stkascreen-{uuid.uuid4().hex[:10]}@test.local"
    password = "StkaScreenPW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0001", email, hash_password(password), role, "active"),
    )
    conn.commit()
    conn.close()
    return company_id, email, password


def _login(email, password):
    c = app.test_client()
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    c.get("/api/sub/retail/settings/tax")   # forces the lazy schema helpers
    return c


@pytest.fixture
def company():
    """One company per test. The report is company-scoped and unpaginated, so
    a shared company would make every count in this file depend on which other
    tests happened to have run first."""
    company_id, email, password = _new_company()
    return company_id, _login(email, password)


def _cashier_client(company_id):
    """A real non-admin user WITH retail access, so a refusal below is the
    route's own authorization and not the subsystem gate bouncing them one
    layer earlier."""
    email = f"stka-cashier-{uuid.uuid4().hex[:10]}@test.local"
    password = "StkaCashierPW1"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), "cashier", "active"),
    )
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    conn.commit()
    conn.close()
    return _login(email, password)


def _create_product(client, initial_stock=0):
    sku = f"STKA-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/sub/retail/products", json={
        "name": f"Stock accuracy item {sku}", "sku": sku, "sell_price": 10.0,
        "cost_price": 5.0, "tax_rate": 0, "initial_stock": initial_stock,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]["id"], sku


def _sql(fn):
    conn = get_retail_conn()
    try:
        out = fn(conn)
        conn.commit()
        return out
    finally:
        conn.close()


def _nudge_balance(company_id, product_id, delta):
    """Move the cached balance behind the API's back -- exactly what the two
    historically-broken writers did, reproduced without depending on either of
    them still being broken."""
    _sql(lambda c: c.execute(
        "UPDATE inventory_balances SET quantity_on_hand = quantity_on_hand + ? "
        "WHERE company_id=? AND product_id=?", (delta, company_id, product_id)))


def _add_branch(company_id, name):
    def go(c):
        cur = c.cursor()
        cur.execute("INSERT INTO branches (company_id,name) VALUES (?,?)", (company_id, name))
        return cur.lastrowid
    return _sql(go)


def _add_movement(company_id, product_id, branch_id, qty):
    _sql(lambda c: c.execute(
        "INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,reference) "
        "VALUES (?,?,?,?,?,?)",
        (company_id, product_id, branch_id, 'adjustment', qty, 'stock-accuracy-screen-test')))


def _key_set(company_id):
    """Every (product_id, branch_id) the reconciliation is defined over, read
    straight from the tables. This is the INDEPENDENT number `pairs_examined`
    is checked against -- deriving it from compute_drift as well would make
    the comparison circular."""
    def go(c):
        rows = c.execute(
            "SELECT product_id, branch_id FROM inventory_balances WHERE company_id=? "
            "UNION "
            "SELECT product_id, branch_id FROM inventory_movements WHERE company_id=?",
            (company_id, company_id)).fetchall()
        return {(r['product_id'], r['branch_id']) for r in rows}
    return _sql(go)


def _drift_now(company_id, tolerance=None):
    conn = get_retail_conn()
    try:
        if tolerance is None:
            return stock_reconciliation.compute_drift(conn, company_id)
        return stock_reconciliation.compute_drift(conn, company_id, tolerance=tolerance)
    finally:
        conn.close()


# ── THE FIXTURE SHOP ──────────────────────────────────────────────────────
#
# Five pairs, five different jobs. Anything less and one of the assertions
# below would be true of a shop that could not have failed it.

def _build_mixed_shop(company_id, client):
    """Returns a dict describing the shop this file measures.

      healthy   a product whose balance and ledger agree exactly
      drifted   a balance nudged +7 behind the API's back
      residue   a balance off by 0.0002 -- BELOW compute_drift's tolerance, so
                it is a PAIR but not a DRIFT. This is the row that catches a
                route that stopped applying the module's tolerance.
      orphan    movements with NO balance row at all (the deleted/never-created
                balance) -- the union direction a JOIN from the balances side
                would drop silently
      stranded  a movement with branch_id NULL, which inventory_balances can
                never hold (its branch_id is NOT NULL), so compute_drift
                reports it repairable=False
    """
    healthy, _ = _create_product(client, initial_stock=20)
    drifted, _ = _create_product(client, initial_stock=20)
    residue, _ = _create_product(client, initial_stock=8)
    orphan, _ = _create_product(client, initial_stock=0)
    stranded, _ = _create_product(client, initial_stock=0)

    _nudge_balance(company_id, drifted, 7)
    _nudge_balance(company_id, residue, 0.0002)

    other = _add_branch(company_id, "Second Branch")
    _add_movement(company_id, orphan, other, 4)          # movements, no balance row
    _add_movement(company_id, stranded, None, 3)         # NULL branch: nowhere to put it

    return {
        'healthy': healthy, 'drifted': drifted, 'residue': residue,
        'orphan': orphan, 'stranded': stranded, 'other_branch': other,
    }


# ═════════════════════════════════════════════════════════════════════════
# 1. pairs_examined -- the number that makes an empty result mean something
# ═════════════════════════════════════════════════════════════════════════

def test_pairs_examined_counts_every_pair_the_reconciliation_is_defined_over(company):
    company_id, client = company
    shop = _build_mixed_shop(company_id, client)

    # ── ANTI-VACUITY, BEFORE ANY CLAIM ABOUT THE RESPONSE ────────────────
    # Each of these says the fixture really is the awkward shop it claims to
    # be. A shop with no drift, no sub-tolerance residue and no orphan proves
    # nothing about a report that has to tell those apart.
    keys = _key_set(company_id)
    assert len(keys) >= 6, f"the fixture built only {len(keys)} (product, branch) pairs: {keys}"
    assert any(b is None for _p, b in keys), "no NULL-branch movement in the fixture"
    drifted_now = _drift_now(company_id)
    assert len(drifted_now) >= 3, f"the fixture carries no drift to report: {drifted_now}"
    assert any(not r['repairable'] for r in drifted_now), "no unrepairable row in the fixture"
    residue_rows = [r for r in _drift_now(company_id, tolerance=-1.0)
                    if r['product_id'] == shop['residue']]
    assert residue_rows, "the residue product produced no pair at all"
    assert all(0 < abs(r['drift']) <= stock_reconciliation.DEFAULT_TOLERANCE for r in residue_rows), (
        "the residue product's drift is not inside the tolerance band, so it cannot show that "
        f"the route applies the module's tolerance: {residue_rows}"
    )

    r = client.get(REPORT)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]

    # THE COUNT IS THE WHOLE KEY SET, not just the drifted part of it. Read
    # against a set built independently from the tables, so a route that
    # re-implemented the union with a subtly different one is caught.
    assert data["pairs_examined"] == len(keys), (
        f"pairs_examined={data['pairs_examined']} but the tables hold {len(keys)} distinct "
        f"(product, branch) pairs: {sorted(map(str, keys))}"
    )
    # ...and it is the module's OWN key set, not a lookalike.
    assert data["pairs_examined"] == len(_drift_now(company_id, tolerance=-1.0))

    # `rows` is exactly what the module reports at the real tolerance -- the
    # residue is a pair and is NOT a drift.
    assert data["drift_count"] == len(drifted_now)
    reported = {(row["product_id"], row["branch_id"]) for row in data["rows"]}
    expected = {(row["product_id"], row["branch_id"]) for row in drifted_now}
    assert reported == expected, f"route rows {reported} != compute_drift rows {expected}"
    assert shop['residue'] not in {row["product_id"] for row in data["rows"]}, (
        "a sub-tolerance float residue was reported as drift. compute_drift's DEFAULT_TOLERANCE "
        "exists because a balance is accumulated by repeated UPDATEs and the ledger by one SUM; "
        "reporting 1e-13 as drift buries the real rows."
    )

    # The invariant the screen's headline depends on.
    assert data["pairs_examined"] >= data["drift_count"] > 0


def test_a_company_with_no_stock_is_a_different_answer_from_a_company_with_no_drift(company):
    """The two zero shapes, distinguishable at the API.

    This is the whole reason pairs_examined exists. Before it, these two
    responses were byte-identical, and the screen could not honestly say
    "everything agrees" about one and "there was nothing to compare" about the
    other -- so it would have had to say the same thing about both, and one of
    them would have been a lie.
    """
    empty_id, client = company
    nothing = client.get(REPORT).get_json()["data"]
    assert nothing["drift_count"] == 0
    assert nothing["pairs_examined"] == 0, (
        "a company with no stock records reported pairs it never had; the 'nothing to check' "
        "state would then be unreachable."
    )

    # ...and now the same company with real, agreeing stock.
    _create_product(client, initial_stock=12)
    clean = client.get(REPORT).get_json()["data"]
    assert clean["drift_count"] == 0
    assert clean["pairs_examined"] > 0, (
        "a healthy shop reports the same pairs_examined as an empty one, so 'every figure "
        "agrees' cannot be told from 'nothing was compared'."
    )
    assert clean["pairs_examined"] != nothing["pairs_examined"]


def test_the_count_and_the_rows_come_from_ONE_reconciliation_call(company, monkeypatch):
    """Structural, and it is the guard against the duplication this design
    deliberately avoided.

    If somebody adds a second, hand-written `SELECT COUNT(*) ... UNION ...` to
    the route to get pairs_examined, the differential test above would still
    pass on the day it was written -- and would start lying the first time the
    reconciliation module widened its key set. So this watches the CALL: one
    compute_drift, made with a tolerance that returns every pair, with the
    drifted rows filtered out of its result.
    """
    _company_id, client = company
    _create_product(client, initial_stock=5)

    real = stock_reconciliation.compute_drift
    seen = []

    def spy(conn, company_id, tolerance=stock_reconciliation.DEFAULT_TOLERANCE):
        seen.append(tolerance)
        return real(conn, company_id, tolerance)

    monkeypatch.setattr(_retail_api.stock_reconciliation, 'compute_drift', spy)
    assert client.get(REPORT).status_code == 200

    assert len(seen) == 1, (
        f"the report called compute_drift {len(seen)} time(s). Two calls are two snapshots of a "
        "live till, and the route's `pairs_examined >= drift_count` stops being true by "
        f"construction. Tolerances seen: {seen}"
    )
    assert seen[0] < 0, (
        f"the report asked compute_drift for tolerance={seen[0]}, so it did not ask for every "
        "pair. Whatever produced pairs_examined is not the module's key set."
    )


def test_both_figures_are_derived_from_the_modules_return_value(company, monkeypatch):
    """The other half of the same seam: not just ONE call, but that both
    numbers come OUT of it.

    compute_drift is replaced with a source that has no relationship to the
    database at all. A route that re-queried for either figure would report
    the real (empty) database instead of this, and the assertions below would
    fail -- which is exactly what should happen.
    """
    _company_id, client = company

    synthetic = [
        {'product_id': 1, 'product_name': 'A', 'sku': 'A', 'branch_id': 1, 'branch_name': 'B',
         'stored_balance': 9.0, 'ledger_balance': 0.0, 'drift': 9.0, 'repairable': True},
        {'product_id': 2, 'product_name': 'B', 'sku': 'B', 'branch_id': 1, 'branch_name': 'B',
         'stored_balance': 4.0, 'ledger_balance': 4.0, 'drift': 0.0, 'repairable': True},
        {'product_id': 3, 'product_name': 'C', 'sku': 'C', 'branch_id': 1, 'branch_name': 'B',
         'stored_balance': 4.0002, 'ledger_balance': 4.0, 'drift': 0.0002, 'repairable': True},
    ]
    monkeypatch.setattr(_retail_api.stock_reconciliation, 'compute_drift',
                        lambda conn, cid, tolerance=None: [dict(r) for r in synthetic])

    data = client.get(REPORT).get_json()["data"]
    assert data["pairs_examined"] == 3, (
        f"pairs_examined={data['pairs_examined']} for a reconciliation that returned 3 pairs. "
        "It is being computed somewhere other than from compute_drift's result."
    )
    assert data["drift_count"] == 1, (
        f"drift_count={data['drift_count']}. Exactly one of the three synthetic pairs is outside "
        f"the module's DEFAULT_TOLERANCE ({stock_reconciliation.DEFAULT_TOLERANCE}); the 0.0 and "
        "the 0.0002 are agreement."
    )
    assert [row["product_id"] for row in data["rows"]] == [1]
    assert data["net_drift"] == 9.0


# ═════════════════════════════════════════════════════════════════════════
# 2. repair_confirmation -- what makes the repair reachable, and what keeps
#    it from being reachable by the wrong person
# ═════════════════════════════════════════════════════════════════════════

def test_the_token_the_report_hands_out_is_the_token_the_repair_accepts(company):
    """The round trip, verbatim -- because the whole point of returning it is
    that no client has to know its format.

    A client that assembled "RECONCILE-<company_id>" itself would be a second
    copy of a format defined in retail_api.py, and the failure mode of that
    copy drifting is a repair button that answers 400 on every click while
    quoting the very string the client just sent.
    """
    company_id, client = company
    pid, _sku = _create_product(client, initial_stock=20)
    _nudge_balance(company_id, pid, -6)

    report = client.get(REPORT).get_json()["data"]
    assert report["drift_count"] == 1, report      # anti-vacuity: there IS something to repair
    token = report["repair_confirmation"]
    assert token, "the report did not hand out a confirmation token, so no frontend can repair"

    r = client.post(REPAIR, json={"confirm": token})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["repaired_count"] == 1

    after = client.get(REPORT).get_json()["data"]
    assert after["drift_count"] == 0
    assert after["pairs_examined"] == report["pairs_examined"], (
        "the repair changed how many pairs exist. It is supposed to rewrite a cached balance, "
        "never to create or destroy one."
    )


def test_the_token_is_scoped_to_the_callers_own_company(company):
    """The assertion that keeps 'the token is not a secret' true.

    Returning it is safe only because _require_confirmation compares it
    against the CALLER'S OWN session company. If that ever stopped being so,
    handing the token out in a GET would be a real weakening rather than a
    convenience -- so it is asserted here, next to the field that discloses
    it, rather than inferred from a helper three files away.
    """
    company_id, client = company
    pid, _sku = _create_product(client, initial_stock=20)
    _nudge_balance(company_id, pid, 5)

    other_id, other_email, other_password = _new_company()
    other_client = _login(other_email, other_password)
    other_token = other_client.get(REPORT).get_json()["data"]["repair_confirmation"]
    assert other_token != client.get(REPORT).get_json()["data"]["repair_confirmation"]

    refused = client.post(REPAIR, json={"confirm": other_token})
    assert refused.status_code == 400, refused.get_json()
    # And nothing moved.
    assert client.get(REPORT).get_json()["data"]["drift_count"] == 1

    # A missing token is refused too -- the interlock is not merely
    # "any string will do".
    assert client.post(REPAIR, json={}).status_code == 400
    assert client.post(REPAIR, json={"confirm": "RECONCILE"}).status_code == 400
    assert client.get(REPORT).get_json()["data"]["drift_count"] == 1


def test_a_cashier_is_refused_and_is_told_nothing_at_all(company):
    """The report is an unpaginated dump of the company's catalogue and stock
    position, and it now also carries the repair token. A refusal must
    disclose neither.
    """
    company_id, client = company
    pid, _sku = _create_product(client, initial_stock=20)
    _nudge_balance(company_id, pid, 3)

    cashier = _cashier_client(company_id)
    # The cashier really does have retail access -- so what follows is the
    # route's own authorization, not the subsystem gate one layer earlier.
    assert cashier.get("/api/sub/retail/products").status_code == 200

    denied = cashier.get(REPORT)
    assert denied.status_code == 403, denied.get_json()
    body = json.dumps(denied.get_json() or {})
    assert "repair_confirmation" not in body and "RECONCILE-" not in body, (
        "a refused reconciliation report leaked the repair confirmation token: " + body
    )
    assert "rows" not in body and "pairs_examined" not in body, (
        "a refused reconciliation report leaked the stock position: " + body
    )

    # ...and the same person cannot repair either.
    assert cashier.post(REPAIR, json={"confirm": f"RECONCILE-{company_id}"}).status_code in (400, 403)
    assert client.get(REPORT).get_json()["data"]["drift_count"] == 1, (
        "a cashier's repair attempt changed a cached balance"
    )

    # The owner still gets the whole answer -- otherwise the assertions above
    # would be satisfied by a route that refuses everybody.
    allowed = client.get(REPORT)
    assert allowed.status_code == 200
    allowed_data = allowed.get_json()["data"]
    assert allowed_data["drift_count"] == 1
    assert allowed_data["repair_confirmation"].startswith("RECONCILE-")


# ═════════════════════════════════════════════════════════════════════════
# 3. The awkward rows the screen has to show honestly
# ═════════════════════════════════════════════════════════════════════════

def test_the_unrepairable_rows_are_reported_and_are_left_alone_by_a_repair(company):
    """A NULL-branch movement has no balance row that could ever hold it
    (inventory_balances.branch_id is NOT NULL), so it is real drift that no
    repair can fix. It must be REPORTED -- the screen queues it for a branch
    assignment -- and it must be SKIPPED rather than quietly counted as
    repaired, or an owner reading "all fixed" would be reading a false
    statement.
    """
    company_id, client = company
    shop = _build_mixed_shop(company_id, client)

    data = client.get(REPORT).get_json()["data"]
    unrepairable = [row for row in data["rows"] if not row["repairable"]]
    assert unrepairable, "the NULL-branch movement is not reported at all"
    assert all(row["branch_id"] is None for row in unrepairable)
    assert {row["product_id"] for row in unrepairable} == {shop['stranded']}

    token = data["repair_confirmation"]
    result = client.post(REPAIR, json={"confirm": token}).get_json()["data"]
    assert result["skipped_count"] == len(unrepairable), (
        f"the repair skipped {result['skipped_count']} rows but {len(unrepairable)} were "
        "unrepairable. The screen reports this number to the owner verbatim."
    )
    assert result["repaired_count"] > 0, "the repair fixed nothing, so 'it skipped only the " \
        "unrepairable ones' is true of a repair that did nothing at all"

    after = client.get(REPORT).get_json()["data"]
    assert after["drift_count"] == len(unrepairable), (
        "after a repair the only remaining drift must be the rows nothing could fix"
    )
    assert all(not row["repairable"] for row in after["rows"])


def test_a_repair_made_through_the_route_names_who_made_it(company):
    """The Stock accuracy screen made this route reachable from a UI for the
    first time, so "who rewrote the shop's stock" now has a real answer.

    repair_drift writes its own summary audit row and leaves the actor NULL
    when the caller supplied none -- correct for a maintenance prompt, and
    wrong for a button somebody pressed. A stock rewrite nobody can be named
    for is exactly the trade the repair's own docstring calls a bad one.
    """
    company_id, client = company
    pid, _sku = _create_product(client, initial_stock=20)
    _nudge_balance(company_id, pid, 4)
    token = client.get(REPORT).get_json()["data"]["repair_confirmation"]
    assert client.post(REPAIR, json={"confirm": token}).status_code == 200

    def go(c):
        return c.execute(
            "SELECT action, user_id FROM audit_log WHERE company_id=?", (company_id,)).fetchall()
    rows = _sql(go)
    assert rows, "the repair wrote no audit trail at all"
    unattributed = [r['action'] for r in rows if not r['user_id']]
    assert not unattributed, (
        f"these audit rows from an HTTP repair name nobody: {unattributed}. The session has a "
        "real user; leaving the actor NULL is the answer reserved for a repair run from a "
        "maintenance prompt."
    )


def test_the_screen_has_a_label_for_every_reason_the_repair_can_refuse_a_row():
    """The cross-language seam: repair_drift's refusal vocabulary lives in
    Python, and the sentences an owner reads live in JavaScript.

    An owner ACTS on the reason. `no_branch` means "go and assign a branch";
    `no_ledger_history` means "this balance has no ledger behind it at all",
    which is a different problem with a different fix -- and that second one
    is invisible to the report, so the screen cannot re-derive it. A new
    constant added on the Python side with nothing rendering it would show up
    as a row refused for a reason the frontend prints as "the server did not
    say why", which is honest but useless, and nothing would have said so.

    So the check is here, at the seam, rather than in either language's own
    suite: every SKIP_* value must be a `case` in the frontend's
    _stkaSkipReasonLabel().
    """
    reasons = {name: value for name, value in vars(stock_reconciliation).items()
               if name.startswith('SKIP_') and isinstance(value, str)}
    # ANTI-VACUITY: a scan that found no constants would make the loop below
    # pass by iterating over nothing.
    assert len(reasons) >= 2, (
        f"found {len(reasons)} SKIP_* constants in stock_reconciliation; the scan is broken, so "
        '"the screen names every refusal" would be a claim about an empty set.'
    )

    frontend = (PRODUCT_DIR / 'frontend' / 'subsystem-retail.js').read_text(encoding='utf-8')
    start = frontend.find('_stkaSkipReasonLabel(reason) {')
    assert start != -1, (
        "the Stock accuracy screen has no _stkaSkipReasonLabel(); every refused row would render "
        "under whatever label happens to be nearest."
    )
    body = frontend[start:frontend.index('\n  },', start)]

    unnamed = sorted(f"{name} = {value!r}" for name, value in reasons.items()
                     if f"case '{value}'" not in body)
    assert not unnamed, (
        "these repair refusal reasons have no sentence on the Stock accuracy screen:\n  "
        + "\n  ".join(unnamed) +
        "\n\nAdd a `case` to _stkaSkipReasonLabel() in products/retail/frontend/"
        "subsystem-retail.js and the sentence to BOTH locale catalogs. Do not leave it to the "
        "default branch: 'the server did not say why' is the honest answer for a reason nobody "
        "has written down, and a wrong-but-confident label is worse than either."
    )

    # ...and the default branch still exists, because a build of the frontend
    # older than a reason the server sends is a real deployment state.
    assert 'default:' in body, (
        "_stkaSkipReasonLabel() has no default branch. A frontend that predates a new refusal "
        "reason would then render nothing for it at all."
    )


def test_a_balance_row_with_no_movements_behind_it_is_reported_as_positive_drift(company):
    """The sign carries the diagnosis, so it is asserted rather than assumed.

    POSITIVE means the balance claims MORE than the ledger can account for --
    the double-received-PO and resurrected-by-import signature, and every
    pre-existing install's balance-without-movements. The screen renders that
    sign with an explicit "+" because the direction is the only part of the
    number that names the bug; if the route's arithmetic were the other way
    round, every one of those screens would name the wrong one.
    """
    company_id, client = company
    pid, _sku = _create_product(client, initial_stock=10)
    # Delete the opening movement: a balance row with nothing behind it, which
    # is exactly what _seed_retail and the importer produce.
    _sql(lambda c: c.execute(
        "DELETE FROM inventory_movements WHERE company_id=? AND product_id=?", (company_id, pid)))

    data = client.get(REPORT).get_json()["data"]
    assert data["drift_count"] == 1, data
    row = data["rows"][0]
    assert row["stored_balance"] == 10.0 and row["ledger_balance"] == 0.0
    assert row["drift"] == 10.0, (
        f"drift={row['drift']} for a balance of 10 with an empty ledger. Drift is stored MINUS "
        "ledger; the other order would tell every screen the opposite story about the same shop."
    )
    assert data["net_drift"] == 10.0

    # ...and the mirror case nets against it, which is why net alone is not
    # the whole headline. NOTE THE SIGN OF THE MOVEMENT, not of the drift:
    # this is +10 of goods recorded in the ledger with NO balance row to hold
    # them (drift = stored 0 MINUS ledger 10 = -10), which is the second of
    # the two directions compute_drift's UNION exists to surface.
    other, _ = _create_product(client, initial_stock=0)
    _add_movement(company_id, other, None, 10)
    both = client.get(REPORT).get_json()["data"]
    assert both["net_drift"] == 0.0, both
    assert both["drift_count"] == 2, (
        "a +10 and a -10 net to zero. If drift_count did not move with them, the screen's "
        "headline would read as a clean shop while two figures are wrong -- which is the exact "
        "reason the count is shown beside the net."
    )
