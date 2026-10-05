"""Aura Retail -- v44 T2: ONE customer balance across THREE real computers.

THE BUG THIS PINS (plan job f8252b48, ROADMAP "retail schema v44 CLAIMED").
`customers.credit_balance` is moved only by the computer that performs the
event. A sale, a payment or a return pulled from a peer never moves it, so a
computer that did not ring the sale shows 0 (or something else) for a
customer the books say owes 80, and every decision made from that column --
the credit-limit check, the return's debt-forgiveness split -- is wrong on
exactly the computers that did not ring the sale.
`retail_payment_void_twodevice_test.py` recorded a slice of it as a residual
("0 before A's void, 30 after, against 80 in the GL").

THE SCENARIO. B rings a credit sale of 100 with 20 paid, takes a 30 payment
on account, and pushes. A and C are fresh computers that pull. A voids the
payment and pushes; everyone pulls. The shop-wide truth is 80 owed
(100 - 20 tendered), and every computer must say so on every surface:
GET /customers, receivables, the statement, aging, and the trial balance's
subledger tie.

THEN THE DECISIONS, on C -- the computer that never touched the sale:
  * credit limit 100, enforce=block: a 30 credit sale must be DENIED (80 + 30
    > 100) and a 20 sale ALLOWED (80 + 20 = 100). Then the cache is poisoned
    to 0 and a 30 sale must STILL be denied -- the decision reads the records,
    never the column. (This is the "assert the check RAN, not the outcome"
    shape: a stale cache that happens to be high would deny for the wrong
    reason, which is why the poisoned-to-zero step exists.)
  * a return of B's sale on C must forgive the whole 80 debt (refund 100 =
    20 tender + 80 forgiven), never mint store credit.

This file is written to FAIL on the pre-v44 tree and pass once balances are
derived (crew L2). Every action goes through the REAL Flask routes in REAL
OS processes over the REAL FileRelay (`_money_void_device.py`).

Run:
    pytest products/retail/tests/retail_party_balance_threedevice_test.py -v
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
DEVICE = TESTS_DIR / "_money_void_device.py"
PY = sys.executable
SHOP_WIDE_OWED = 80.0


def dev(app_dir, relay, actions, timeout=300):
    work = Path(app_dir).parent / f"_act_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    a, o = work / "a.json", work / "o.json"
    a.write_text(json.dumps(actions), encoding="utf-8")
    p = subprocess.run([PY, str(DEVICE), str(app_dir), str(relay), str(a), str(o)],
                       capture_output=True, text=True, timeout=timeout)
    assert p.returncode == 0, p.stderr[-3000:]
    return json.loads(o.read_text(encoding="utf-8"))


def sql(app, relay, q, params=(), commit=False):
    return dev(app, relay, [{"op": "sql", "q": q, "params": list(params), "commit": commit}])[0]["rows"]


def http(app, relay, cred, method, path, body=None):
    out = dev(app, relay, [{"op": "login", **cred},
                           {"op": "http", "method": method, "path": path, "json": body}])
    return out[1]


def _sync(*computers, rounds=2):
    for _ in range(rounds):
        for app, relay in computers:
            dev(app, relay, [{"op": "push"}, {"op": "pull"}])


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    root = tmp_path_factory.mktemp("party_balance_3dev")
    A, B, C, relay = root / "A", root / "B", root / "C", root / "relay.db"
    [made] = dev(B, relay, [{"op": "create_shop", "price": 100.0, "initial_stock": 50}])
    credB = {"email": made["email"], "password": made["password"]}
    _, cust = dev(B, relay, [{"op": "login", **credB}, {"op": "create_customer", "name": "Shared Debtor"}])
    customer_id = cust["customer_id"]
    _, sale = dev(B, relay, [{"op": "login", **credB},
                             {"op": "sell_credit", "product_id": made["product_id"], "quantity": 1,
                              "customer_id": customer_id, "amount_paid": 20.0}])
    assert sale["status_code"] == 200, sale
    _, pay = dev(B, relay, [{"op": "login", **credB},
                            {"op": "customer_payment", "customer_id": customer_id, "amount": 30.0}])
    assert pay["status_code"] == 200, pay
    ref = pay["body"]["data"]["reference"]
    dev(B, relay, [{"op": "push"}])

    creds = {"B": credB}
    for name, app in (("A", A), ("C", C)):
        [boot] = dev(app, relay, [{"op": "bootstrap"}])
        dev(app, relay, [{"op": "pull"}])
        creds[name] = {"email": boot["email"], "password": boot["password"]}
        creds[name + "_company"] = boot["company_id"]

    # A voids the 30 payment, then everyone meets.
    pay_id = sql(A, relay, "SELECT id FROM payments WHERE reference=?", (ref,))[0]["id"]
    v = http(A, relay, creds["A"], "post", f"/payments/{pay_id}/void", {"reason": "t2"})
    assert v["status_code"] == 200, v
    _sync((A, relay), (B, relay), (C, relay), rounds=3)
    return {"root": root, "A": A, "B": B, "C": C, "relay": relay, "creds": creds,
            "customer_id": customer_id, "product_id": made["product_id"]}


def _fork_c(shop, tmp_path):
    """A private copy of computer C + the relay, so one test's sales never
    change the figures another test starts from."""
    c, relay = tmp_path / "C", tmp_path / "relay.db"
    shutil.copytree(shop["C"], c)
    shutil.copy(shop["relay"], relay)
    return c, relay


def _c_ids(c, relay):
    cust = sql(c, relay, "SELECT id, company_id FROM customers")[0]
    prod = sql(c, relay, "SELECT id FROM products")[0]["id"]
    sale = sql(c, relay, "SELECT id FROM sales ORDER BY id LIMIT 1")[0]["id"]
    return cust["id"], cust["company_id"], prod, sale


def _what_a_computer_shows(app, relay, cred):
    cust_id = sql(app, relay, "SELECT id FROM customers")[0]["id"]
    out = dev(app, relay, [
        {"op": "login", **cred},
        {"op": "http", "method": "get", "path": "/customers"},
        {"op": "http", "method": "get", "path": "/customers/receivables"},
        {"op": "http", "method": "get", "path": f"/customers/{cust_id}/statement"},
        {"op": "http", "method": "get", "path": "/reports/aging"},
        {"op": "trial_balance"}])
    lst, rec, stmt, aging, tb = out[1:]
    for r in (lst, rec, stmt, aging, tb):
        assert r["status_code"] == 200, r
    listed = [c for c in lst["body"]["data"] if c["id"] == cust_id][0]
    receivable = [c for c in rec["body"]["data"] if c["id"] == cust_id]
    aged = [c for c in aging["body"]["customers"] if c["id"] == cust_id]
    events = stmt["body"]["data"]["events"]
    return {
        "customers_list": listed["credit_balance"],
        "receivables": receivable[0]["credit_balance"] if receivable else 0.0,
        "receivables_total": rec["body"]["total_receivable"],
        "statement_balance": stmt["body"]["data"]["balance"],
        "statement_last_running": events[-1]["running_balance"] if events else 0.0,
        "aging": aged[0]["balance"] if aged else 0.0,
        "ar_ties": (tb["body"]["data"].get("subledger") or {}).get("ar_ties"),
    }


@pytest.mark.parametrize("name", ["B", "A", "C"])
def test_every_computer_shows_the_shop_wide_balance_on_every_surface(shop, name):
    shows = _what_a_computer_shows(shop[name], shop["relay"], shop["creds"][name])
    wrong = {k: v for k, v in shows.items() if k != "ar_ties" and v != SHOP_WIDE_OWED}
    assert not wrong, f"computer {name} disagrees with the shop-wide {SHOP_WIDE_OWED}: {shows}"
    assert shows["ar_ties"] is True, f"computer {name}: AR subledger does not tie to the books: {shows}"


def test_credit_limit_is_decided_from_the_shop_wide_balance_on_the_computer_that_never_rang_the_sale(shop, tmp_path):
    c, relay = _fork_c(shop, tmp_path)
    cred = shop["creds"]["C"]
    cust_id, company_id, prod, _sale = _c_ids(c, relay)
    sql(c, relay, "UPDATE customers SET credit_mode='limited', credit_limit=100 WHERE id=?", (cust_id,), commit=True)
    sql(c, relay, "INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,'enforce_credit_limit','block')",
        (company_id,), commit=True)

    def credit_sale(amount):
        out = dev(c, relay, [{"op": "login", **cred},
                             {"op": "http", "method": "post", "path": "/sales", "json": {
                                 "items": [{"product_id": prod, "quantity": 1}], "payment_method": "credit",
                                 "customer_id": cust_id, "amount_paid": 100.0 - amount,
                                 "idempotency_key": str(uuid.uuid4())}}])
        return out[1]

    # (1) 80 owed + 30 more = 110 > 100: denied. A cache that read 0 or 30 would allow it.
    denied = credit_sale(30.0)
    assert denied["status_code"] == 400, f"a 30 credit sale on top of 80 owed (limit 100) was not denied: {denied}"
    assert "Credit limit exceeded" in json.dumps(denied["body"]), denied

    # (2) the same sale with the cache poisoned to 0 must STILL be denied: the
    # decision reads the records, not the column. Proves the check ran on the
    # derived figure rather than being denied by a lucky stale value.
    sql(c, relay, "UPDATE customers SET credit_balance=0 WHERE id=?", (cust_id,), commit=True)
    denied_again = credit_sale(30.0)
    assert denied_again["status_code"] == 400, f"credit check trusted the stale column: {denied_again}"

    # (3) allow-half: 80 + 20 = 100 is within the limit, so it must go through.
    allowed = credit_sale(20.0)
    assert allowed["status_code"] == 200, f"a 20 credit sale (exactly at the limit) was refused: {allowed}"


def test_a_return_on_the_computer_that_never_rang_the_sale_forgives_the_whole_debt(shop, tmp_path):
    c, relay = _fork_c(shop, tmp_path)
    cred = shop["creds"]["C"]
    cust_id, _company, prod, sale_id = _c_ids(c, relay)
    out = dev(c, relay, [{"op": "login", **cred},
                         {"op": "http", "method": "post", "path": "/returns", "json": {
                             "sale_id": sale_id, "items": [{"product_id": prod, "quantity": 1}],
                             "reason": "t2", "idempotency_key": str(uuid.uuid4())}}])
    assert out[1]["status_code"] == 200, out[1]
    row = sql(c, relay, "SELECT ar_forgiven_amount, store_credit_amount, refund_amount FROM returns")[0]
    assert row["ar_forgiven_amount"] == SHOP_WIDE_OWED, (
        f"debt forgiven should be the shop-wide {SHOP_WIDE_OWED}, got {row}")
    assert row["store_credit_amount"] == 0.0, f"store credit minted against a debt that existed: {row}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-x"]))
