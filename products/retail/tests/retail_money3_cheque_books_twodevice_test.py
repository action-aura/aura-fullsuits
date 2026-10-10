"""MONEY-3 M3, two real computers: a cheque's journals are the same facts on both books.

A cheque RECEIVED on computer A and CLEARED on computer B must leave A and B with identical
trial balances, each tying AR/AP to its party balances. Two computers that both clear the same
cheque while offline must book the bank leg ONCE: the journal's uid names the fact (cheque +
live stint), not the event id, so the second copy collapses when they meet.

Same harness as retail_payment_void_twodevice_test.py: a real Flask app in a real OS process per
device over the real FileRelay, driven through the real routes by the generic `http` / `sql`
ops of _money_void_device.py.

MUTATIONS (each performed; red quoted in the crew report):
  M3-i  general_ledger.cheque_journal_uid: key the BANK leg on a per-computer random value
        -> test_two_computers_clearing_the_same_cheque_offline_book_the_bank_leg_once
  M3-j  retail_api._post_cheque_books: post nothing for 'cleared'
        -> test_received_on_one_computer_cleared_on_the_other_books_are_identical

Run:
    pytest products/retail/tests/retail_money3_cheque_books_twodevice_test.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
DEVICE = TESTS_DIR / "_money_void_device.py"
PY = sys.executable


def dev(app_dir, relay, actions, timeout=300):
    work = Path(app_dir).parent / f"_act_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    a, o = work / "a.json", work / "o.json"
    a.write_text(json.dumps(actions), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("AURA_SYNC_RELAY_URL", "AURA_OWNER_LICENSING_URL")}
    p = subprocess.run([PY, str(DEVICE), str(app_dir), str(relay), str(a), str(o)],
                       capture_output=True, text=True, timeout=timeout, env=env)
    assert p.returncode == 0, p.stderr[-3000:]
    return json.loads(o.read_text(encoding="utf-8"))


def sql(app, relay, q, params=()):
    return dev(app, relay, [{"op": "sql", "q": q, "params": list(params)}])[0]["rows"]


def http(app, relay, cred, method, path, body=None):
    return dev(app, relay, [{"op": "login", **cred},
                            {"op": "http", "method": method, "path": path, "json": body}])[1]


def books(app, relay, cred):
    r = dev(app, relay, [{"op": "login", **cred}, {"op": "trial_balance"}])[1]
    assert r["status_code"] == 200, r
    d = r["body"]["data"]
    return {a["code"]: a["balance"] for a in d["accounts"]}, d


def sync_rounds(A, B, relay, rounds=2):
    for _ in range(rounds):
        dev(A, relay, [{"op": "push"}, {"op": "pull"}])
        dev(B, relay, [{"op": "push"}, {"op": "pull"}])


def _setup(tmp_path):
    """B has a shop, a customer owing 200 (a credit sale), and has pushed. A is a fresh second
    computer that has pulled. Returns (A, B, relay, credA, credB, customer id)."""
    A, B, relay = tmp_path / "A", tmp_path / "B", tmp_path / "relay.db"
    [shop] = dev(B, relay, [{"op": "create_shop", "price": 200.0, "initial_stock": 50}])
    credB = {"email": shop["email"], "password": shop["password"]}
    _, cust = dev(B, relay, [{"op": "login", **credB}, {"op": "create_customer", "name": "Cheque Cust"}])
    _, s = dev(B, relay, [{"op": "login", **credB},
                          {"op": "sell_credit", "product_id": shop["product_id"], "quantity": 1,
                           "customer_id": cust["customer_id"], "amount_paid": 0}])
    assert s["status_code"] == 200, s
    dev(B, relay, [{"op": "push"}])
    [boot] = dev(A, relay, [{"op": "bootstrap"}])
    dev(A, relay, [{"op": "pull"}])
    return A, B, relay, {"email": boot["email"], "password": boot["password"]}, credB, cust["customer_id"]


def _cheque_on(app, relay, cred, customer, amount=80.0):
    r = http(app, relay, cred, "post", "/cheques", {
        "direction": "in", "party_id": customer, "amount": amount, "due_date": "2026-12-01",
        "cheque_number": f"TD-{uuid.uuid4().hex[:5]}"})
    assert r["status_code"] == 201, r
    return r["body"]["data"]["id"]


def _bank_journals(app, relay, cheque_id):
    return sql(app, relay,
               "SELECT j.uid FROM gl_journals j JOIN gl_journal_lines l ON l.journal_id=j.id "
               "JOIN gl_accounts a ON a.id=l.account_id WHERE j.source_uid=? AND a.code='1000' "
               "AND j.reversed_journal_id IS NULL", (cheque_id,))


def test_received_on_one_computer_cleared_on_the_other_books_are_identical(tmp_path):
    A, B, relay, credA, credB, cust = _setup(tmp_path)
    chq = _cheque_on(A, relay, credA, cust, 80.0)             # received on A
    dev(A, relay, [{"op": "push"}])
    dev(B, relay, [{"op": "pull"}])
    assert http(B, relay, credB, "post", f"/cheques/{chq}/clear", {})["status_code"] == 200   # cleared on B
    sync_rounds(A, B, relay, rounds=3)
    ledA, tbA = books(A, relay, credA)
    ledB, tbB = books(B, relay, credB)
    assert ledA == ledB, (ledA, ledB)
    assert ledA["1150"] == 0 and ledA["1000"] == 80 and ledA["1100"] == 120, ledA
    for tb in (tbA, tbB):
        assert tb["is_balanced"] is True
        assert tb["subledger"]["ar_ties"] is True, tb["subledger"]
    # the same journals, by uid, on both computers -- and exactly the two this cheque owns
    uids = lambda app: sorted(r["uid"] for r in sql(app, relay, "SELECT uid FROM gl_journals WHERE source_uid=?", (chq,)))
    assert uids(A) == uids(B) and len(uids(A)) == 2, (uids(A), uids(B))


def test_two_computers_clearing_the_same_cheque_offline_book_the_bank_leg_once(tmp_path):
    A, B, relay, credA, credB, cust = _setup(tmp_path)
    chq = _cheque_on(B, relay, credB, cust, 50.0)
    dev(B, relay, [{"op": "push"}])
    dev(A, relay, [{"op": "pull"}])
    for app, cred in ((A, credA), (B, credB)):                # neither has seen the other's clear
        assert http(app, relay, cred, "post", f"/cheques/{chq}/clear", {})["status_code"] == 200
    sync_rounds(A, B, relay, rounds=3)
    ledA, tbA = books(A, relay, credA)
    ledB, tbB = books(B, relay, credB)
    assert ledA == ledB, (ledA, ledB)
    assert ledA["1000"] == 50 and ledA["1150"] == 0, ledA     # 50 once, not 100
    assert len(_bank_journals(A, relay, chq)) == 1 and len(_bank_journals(B, relay, chq)) == 1
    assert tbA["subledger"]["ar_ties"] is True and tbB["subledger"]["ar_ties"] is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-x"]))
