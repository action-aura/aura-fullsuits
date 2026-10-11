"""MONEY-3 M1 (retail v51), two real processes: a sale paid in parts on computer A is refunded on
computer B, and both computers end with the same legs and the same books.

Two OS processes, each a REAL Flask app with its OWN database, joined only by a file relay (the
harness `_money_void_device.py` the supplier-drawer two-device test also uses). The legs ride INSIDE
the `return` sync event; the refund's journal rides as a `gl_journal`. Cash leaves only the drawer
that paid it out: B's drawer drops by the 30 of cash, A's drawer (which took the sale) is untouched.

MUTATIONS this file owns (each performed, red/green quoted in the crew report):
  M1-j  retail_api: drop `'legs': _leg_payload` from the `return` sync payload
        -> test_legs_and_books_are_identical_on_both_computers
  M1-k  sync_service `return` apply: skip `_apply_return_refund_legs`
        -> test_legs_and_books_are_identical_on_both_computers
  M1-l  sync_service `_apply_return_refund_legs`: no legacy leg when `legs` is absent
        -> test_a_return_from_a_till_that_sends_no_legs_becomes_one_legacy_leg

Run:
    pytest products/retail/tests/retail_money3_split_refund_twodevice_test.py -v
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
DEVICE = TESTS_DIR / "_money_void_device.py"
PY = sys.executable
BACKEND_DIR = TESTS_DIR.parent / "backend"
SUITE_ROOT = TESTS_DIR.parent.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def dev(app_dir, relay, actions, timeout=300):
    work = Path(app_dir).parent / f"_act_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    a, o = work / "a.json", work / "o.json"
    a.write_text(json.dumps(actions), encoding="utf-8")
    import os
    env = {k: v for k, v in os.environ.items() if k not in ("AURA_SYNC_RELAY_URL", "AURA_OWNER_LICENSING_URL")}
    p = subprocess.run([PY, str(DEVICE), str(app_dir), str(relay), str(a), str(o)],
                       capture_output=True, text=True, timeout=timeout, env=env)
    assert p.returncode == 0, p.stderr[-3000:]
    return json.loads(o.read_text(encoding="utf-8"))


def sql(app, relay, q, params=()):
    return dev(app, relay, [{"op": "sql", "q": q, "params": list(params)}])[0]["rows"]


def http(app, relay, cred, method, path, body=None):
    out = dev(app, relay, [{"op": "login", **cred},
                           {"op": "http", "method": method, "path": path, "json": body}])
    return out[1]


def sync_rounds(A, B, relay, rounds=2):
    for _ in range(rounds):
        dev(A, relay, [{"op": "push"}, {"op": "pull"}])
        dev(B, relay, [{"op": "push"}, {"op": "pull"}])


def _setup(tmp_path, card_only=False):
    """A sells 100 paid 30 cash + 70 card with its drawer open (float 100); B is a fresh
    computer that has pulled the sale and opens its own drawer (float 100)."""
    A, B, relay = tmp_path / "A", tmp_path / "B", tmp_path / "relay.db"
    [shop] = dev(A, relay, [{"op": "create_shop", "price": 100.0, "initial_stock": 50}])
    credA = {"email": shop["email"], "password": shop["password"]}
    pid = shop["product_id"]
    sa = http(A, relay, credA, "post", "/cash-sessions/open", {"opening_float": 100.0})
    assert sa["status_code"] == 200, sa
    sid_a = sa["body"]["data"]["id"]
    # card_only: a sale paid ONE way (100 by card). The legless-return test needs a sale whose
    # single refund_method is legitimately within what that method took; on the 30/70 split a
    # lone 'card' refund of 100 is (correctly, since the cap) refused.
    body = {"items": [{"product_id": pid, "quantity": 1}], "idempotency_key": str(uuid.uuid4())}
    if card_only:
        body.update(payment_method="card", amount_paid=100)
    else:
        body.update(payment_method="split",
                    tenders=[{"method": "cash", "amount": 30}, {"method": "card", "amount": 70}])
    sale = http(A, relay, credA, "post", "/sales", body)
    assert sale["status_code"] == 200, sale
    dev(A, relay, [{"op": "push"}])
    [boot] = dev(B, relay, [{"op": "bootstrap"}])
    dev(B, relay, [{"op": "pull"}])
    credB = {"email": boot["email"], "password": boot["password"]}
    sb = http(B, relay, credB, "post", "/cash-sessions/open", {"opening_float": 100.0})
    assert sb["status_code"] == 200, sb
    sale_b = sql(B, relay, "SELECT id FROM sales ORDER BY id DESC LIMIT 1")[0]["id"]
    return A, B, relay, credA, credB, pid, sid_a, sb["body"]["data"]["id"], sale_b


def _x(app, relay, cred, sid):
    r = http(app, relay, cred, "get", f"/cash-sessions/{sid}/x-report")
    assert r["status_code"] == 200, r
    return r["body"]["data"]


def _legs(app, relay):
    return sorted((r["method"], r["amount"], r["uid"]) for r in sql(
        app, relay, "SELECT method, amount, uid FROM return_refund_legs"))


def _books(app, relay, cred):
    tb = dev(app, relay, [{"op": "login", **cred}, {"op": "trial_balance"}])[1]
    assert tb["status_code"] == 200, tb
    data = tb["body"]["data"]
    return {a["code"]: (round(float(a.get("debit") or 0), 3), round(float(a.get("credit") or 0), 3))
            for a in data["accounts"]}, data


def test_legs_and_books_are_identical_on_both_computers(tmp_path):
    A, B, relay, credA, credB, pid, sid_a, sid_b, sale_b = _setup(tmp_path)
    assert _x(A, relay, credA, sid_a)["expected_cash"] == 130.0
    r = http(B, relay, credB, "post", "/returns", {
        "sale_id": sale_b, "items": [{"product_id": pid, "quantity": 1}],
        "refund_legs": "default", "idempotency_key": str(uuid.uuid4())})
    assert r["status_code"] == 200, r
    assert {lg["method"]: lg["amount"] for lg in r["body"]["data"]["legs"]} == {"cash": 30.0, "card": 70.0}
    # B's drawer: only the 30 of cash left it
    xb = _x(B, relay, credB, sid_b)
    assert xb["cash_refunds"] == 30.0 and xb["expected_cash"] == 70.0, xb

    sync_rounds(A, B, relay)
    sync_rounds(A, B, relay)            # echoes and replays add nothing
    la, lb = _legs(A, relay), _legs(B, relay)
    assert la == lb and [(m, a) for m, a, _ in la] == [("card", 70.0), ("cash", 30.0)], (la, lb)
    ra = sql(A, relay, "SELECT refund_method, tender_refund_amount, refund_amount FROM returns")
    assert ra == [{"refund_method": "split", "tender_refund_amount": 100.0, "refund_amount": 100.0}], ra

    # the books are identical and balanced on both computers
    books_a, da = _books(A, relay, credA)
    books_b, db = _books(B, relay, credB)
    assert books_a == books_b, (books_a, books_b)
    assert da["is_balanced"] is True and db["is_balanced"] is True
    # cash leaves only the drawer that paid it: A's drawer (which took the sale) is untouched,
    # and A's report does not grow a refund it never paid
    xa = _x(A, relay, credA, sid_a)
    assert xa["cash_refunds"] == 0.0 and xa["expected_cash"] == 130.0, xa
    assert _x(B, relay, credB, sid_b)["expected_cash"] == 70.0


def test_a_return_from_a_till_that_sends_no_legs_becomes_one_legacy_leg(tmp_path):
    """An older till's `return` event has no `legs` key. The receiver must read it as ONE leg of its
    refund_method for its tender part -- the identical row the v51 backfill writes -- never as 'no
    refund' and never twice."""
    A, B, relay, credA, credB, pid, sid_a, sid_b, sale_b = _setup(tmp_path, card_only=True)
    r = http(B, relay, credB, "post", "/returns", {
        "sale_id": sale_b, "items": [{"product_id": pid, "quantity": 1}],
        "refund_method": "card", "idempotency_key": str(uuid.uuid4())})
    assert r["status_code"] == 200, r
    dev(B, relay, [{"op": "push"}])
    # strip `legs` from the relayed return event: what an older till would have sent
    conn = sqlite3.connect(str(relay))
    stripped = 0
    for seq, payload in conn.execute("SELECT seq, payload FROM relay_events").fetchall():
        ev = json.loads(payload)
        if ev.get("entity_type") != "return":
            continue
        inner = ev["payload"]
        was_str = isinstance(inner, str)
        body = json.loads(inner) if was_str else inner
        if "legs" in body:
            del body["legs"]
            ev["payload"] = json.dumps(body) if was_str else body
            conn.execute("UPDATE relay_events SET payload=? WHERE seq=?", (json.dumps(ev), seq))
            stripped += 1
    conn.commit(); conn.close()
    assert stripped == 1
    dev(A, relay, [{"op": "pull"}])
    rows = sql(A, relay, "SELECT l.method AS method, l.amount AS amount, l.uid AS uid, r.uid AS ruid "
                         "FROM return_refund_legs l JOIN returns r ON r.id = l.return_id")
    assert len(rows) == 1, rows
    from database.schema import return_refund_leg_uid
    assert rows[0]["method"] == "card" and rows[0]["amount"] == 100.0
    assert rows[0]["uid"] == return_refund_leg_uid(rows[0]["ruid"], "legacy")
    dev(A, relay, [{"op": "pull"}])        # a replay is a no-op
    assert len(sql(A, relay, "SELECT uid FROM return_refund_legs")) == 1


def test_a_legless_return_on_a_credit_sale_becomes_a_leg_of_the_money_not_the_goods(tmp_path):
    """N2. A credit sale: 100 of goods, 70 paid in cash, 30 on account. A full return gives 70 back as
    money and FORGIVES 30 of debt, so refund_amount (100) != tender_refund_amount (70). A till that
    sends no `legs` must be read as ONE leg of the 70 -- the identical row the v51 backfill writes.
    Reading refund_amount instead would book the forgiven debt as 30 of cash that never left the
    drawer on the receiving computer."""
    A, B, relay = tmp_path / "A", tmp_path / "B", tmp_path / "relay.db"
    [shop] = dev(A, relay, [{"op": "create_shop", "price": 100.0, "initial_stock": 50}])
    credA = {"email": shop["email"], "password": shop["password"]}
    pid = shop["product_id"]
    [_, cust] = dev(A, relay, [{"op": "login", **credA}, {"op": "create_customer", "name": "N2 Customer"}])
    assert cust["status_code"] == 200, cust
    sa = http(A, relay, credA, "post", "/cash-sessions/open", {"opening_float": 100.0})
    assert sa["status_code"] == 200, sa
    [_, sold] = dev(A, relay, [{"op": "login", **credA}, {"op": "sell_credit", "product_id": pid, "quantity": 1,
                                                          "customer_id": cust["customer_id"], "amount_paid": 70}])
    assert sold["status_code"] == 200, sold
    dev(A, relay, [{"op": "push"}])
    [boot] = dev(B, relay, [{"op": "bootstrap"}])
    dev(B, relay, [{"op": "pull"}])
    credB = {"email": boot["email"], "password": boot["password"]}
    sb = http(B, relay, credB, "post", "/cash-sessions/open", {"opening_float": 100.0})
    assert sb["status_code"] == 200, sb
    sale_b = sql(B, relay, "SELECT id FROM sales ORDER BY id DESC LIMIT 1")[0]["id"]
    r = http(B, relay, credB, "post", "/returns", {
        "sale_id": sale_b, "items": [{"product_id": pid, "quantity": 1}],
        "refund_method": "cash", "idempotency_key": str(uuid.uuid4())})
    assert r["status_code"] == 200, r
    row = sql(B, relay, "SELECT refund_amount, tender_refund_amount, ar_forgiven_amount FROM returns")[0]
    assert row == {"refund_amount": 100.0, "tender_refund_amount": 70.0, "ar_forgiven_amount": 30.0}, row
    dev(B, relay, [{"op": "push"}])
    conn = sqlite3.connect(str(relay))
    stripped = 0
    for seq, payload in conn.execute("SELECT seq, payload FROM relay_events").fetchall():
        ev = json.loads(payload)
        if ev.get("entity_type") != "return":
            continue
        inner = ev["payload"]
        was_str = isinstance(inner, str)
        body = json.loads(inner) if was_str else inner
        if "legs" in body:
            del body["legs"]
            ev["payload"] = json.dumps(body) if was_str else body
            conn.execute("UPDATE relay_events SET payload=? WHERE seq=?", (json.dumps(ev), seq))
            stripped += 1
    conn.commit(); conn.close()
    assert stripped == 1
    dev(A, relay, [{"op": "pull"}])
    legs = sql(A, relay, "SELECT method, amount FROM return_refund_legs")
    assert legs == [{"method": "cash", "amount": 70.0}], legs


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-x"]))
