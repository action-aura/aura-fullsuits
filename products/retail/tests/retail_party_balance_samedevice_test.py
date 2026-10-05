"""Aura Retail -- v44: the balance guards that must hold INSIDE ONE RUNNING PROCESS.

WHY THIS FILE EXISTS (a blind spot in retail_party_balance_threedevice_test.py,
found by mutation, not by reading). The three-computer test drives each computer
through `_money_void_device.py`, which boots a FRESH OS process for every `dev()`
call, and every boot runs init_retail's v44 boot refresh -- which re-derives
every party's `credit_balance` from the shop-wide rows. So between two `dev()`
calls the display cache is always healed, and three things that test appears to
prove are in fact invisible to it:

  * the post-pull refresh (a pull is its own process; the next process's boot
    refresh repairs the cache anyway -- delete `install_party_balance_refresh`
    and the test stays green),
  * "the credit check ran on the derived figure, not the column": poisoning the
    cache to 0 in one call is undone by the next call's boot refresh before the
    sale is rung, so a check reading the stale column would pass the same way,
  * the return split's current_balance, for the same reason.

Here every poisoned-cache action and every pull-then-read happens inside ONE
`dev()` call (one process, one boot), so nothing can heal the cache between the
event and the thing being checked:

  1. post-pull refresh: a fresh computer pulls the shop's events and, in the
     SAME process, lists its customers -- the figure must already be 80.
  2. credit limit 100 (block), cache poisoned to 0 in-process: a 30 credit sale
     on top of 80 owed is still DENIED (so the check reads the records), and the
     allow-half, a 20 sale, still goes through.
  3. a return on a computer that never rang the sale, cache poisoned to 0
     in-process: the whole 80 debt is forgiven and no store credit is minted.

Mutation proofs (plan M4, M5, M6, M11): drop install_party_balance_refresh ->
(1) red; credit check reads the column -> (2) red; return split's current_balance
reads the column -> (3) red; restore the old void delta with the refresh off ->
(1) red.

Run:
    pytest products/retail/tests/retail_party_balance_samedevice_test.py -v
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


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    """B rings a credit sale of 100 (20 paid), takes a 30 payment and voids it
    itself, then pushes: the shop owes 80. C pulls everything (its own process).
    D is a fresh computer that has pulled NOTHING yet."""
    root = tmp_path_factory.mktemp("party_balance_samedev")
    B, C, D, relay = root / "B", root / "C", root / "D", root / "relay.db"
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
    pay_id = sql(B, relay, "SELECT id FROM payments WHERE reference=?", (ref,))[0]["id"]
    _, void = dev(B, relay, [{"op": "login", **credB},
                             {"op": "http", "method": "post", "path": f"/payments/{pay_id}/void",
                              "json": {"reason": "samedev"}}])
    assert void["status_code"] == 200, void
    dev(B, relay, [{"op": "push"}])

    creds = {}
    for name, app in (("C", C), ("D", D)):
        [boot] = dev(app, relay, [{"op": "bootstrap"}])
        creds[name] = {"email": boot["email"], "password": boot["password"]}
    dev(C, relay, [{"op": "pull"}])       # C has the shop's events; D deliberately has none yet
    return {"root": root, "C": C, "D": D, "relay": relay, "creds": creds,
            "product_id": made["product_id"], "customer_id": customer_id}


def test_a_pull_refreshes_the_balance_in_the_same_process(shop):
    """D pulls and, in the SAME process, lists customers + the debtor book. The
    post-pull refresh is the only thing that can make the list right here."""
    d, relay = shop["D"], shop["relay"]
    out = dev(d, relay, [{"op": "login", **shop["creds"]["D"]}, {"op": "pull"},
                         {"op": "http", "method": "get", "path": "/customers"}])
    assert out[2]["status_code"] == 200, out[2]
    listed = [c for c in out[2]["body"]["data"] if c["id"] == shop["customer_id"]]
    assert listed, out[2]["body"]
    assert listed[0]["credit_balance"] == SHOP_WIDE_OWED, (
        f"a pull left the display cache at {listed[0]['credit_balance']}, not the shop-wide {SHOP_WIDE_OWED}")


def _fork(shop, tmp_path):
    c, relay = tmp_path / "C", tmp_path / "relay.db"
    shutil.copytree(shop["C"], c)
    shutil.copy(shop["relay"], relay)
    return c, relay


def _ids(c, relay):
    cust = sql(c, relay, "SELECT id, company_id FROM customers")[0]
    prod = sql(c, relay, "SELECT id FROM products")[0]["id"]
    sale = sql(c, relay, "SELECT id FROM sales ORDER BY id LIMIT 1")[0]["id"]
    return cust["id"], cust["company_id"], prod, sale


def test_credit_limit_reads_the_records_not_a_poisoned_column(shop, tmp_path):
    c, relay = _fork(shop, tmp_path)
    cred = shop["creds"]["C"]
    cust_id, company_id, prod, _sale = _ids(c, relay)
    sql(c, relay, "UPDATE customers SET credit_mode='limited', credit_limit=100 WHERE id=?", (cust_id,), commit=True)
    sql(c, relay, "INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,'enforce_credit_limit','block')",
        (company_id,), commit=True)

    def credit_sale_after_poison(amount):
        # ONE process: poison the cache to 0, then ring the sale.
        out = dev(c, relay, [
            {"op": "login", **cred},
            {"op": "sql", "q": "UPDATE customers SET credit_balance=0 WHERE id=?", "params": [cust_id], "commit": True},
            {"op": "http", "method": "post", "path": "/sales", "json": {
                "items": [{"product_id": prod, "quantity": 1}], "payment_method": "credit",
                "customer_id": cust_id, "amount_paid": 100.0 - amount, "idempotency_key": str(uuid.uuid4())}}])
        return out[2]

    denied = credit_sale_after_poison(30.0)
    assert denied["status_code"] == 400, f"80 owed + 30 (limit 100) was not denied with the cache at 0: {denied}"
    assert "Credit limit exceeded" in json.dumps(denied["body"]), denied
    allowed = credit_sale_after_poison(20.0)    # allow-half: 80 + 20 = 100 is within the limit
    assert allowed["status_code"] == 200, f"a 20 credit sale exactly at the limit was refused: {allowed}"


def test_return_split_reads_the_records_not_a_poisoned_column(shop, tmp_path):
    c, relay = _fork(shop, tmp_path)
    cred = shop["creds"]["C"]
    cust_id, _company, prod, sale_id = _ids(c, relay)
    out = dev(c, relay, [
        {"op": "login", **cred},
        {"op": "sql", "q": "UPDATE customers SET credit_balance=0 WHERE id=?", "params": [cust_id], "commit": True},
        {"op": "http", "method": "post", "path": "/returns", "json": {
            "sale_id": sale_id, "items": [{"product_id": prod, "quantity": 1}],
            "reason": "samedev", "idempotency_key": str(uuid.uuid4())}}])
    assert out[2]["status_code"] == 200, out[2]
    row = sql(c, relay, "SELECT ar_forgiven_amount, store_credit_amount FROM returns")[0]
    assert row["ar_forgiven_amount"] == SHOP_WIDE_OWED, f"debt forgiven should be {SHOP_WIDE_OWED}: {row}"
    assert row["store_credit_amount"] == 0.0, f"store credit minted against a debt that existed: {row}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-x"]))
