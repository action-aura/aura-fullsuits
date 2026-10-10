"""MONEY-3 M2, two real computers: a supplier cash payment taken out of ONE till's drawer,
cancelled on another computer, comes back exactly once.

Same shape as retail_payment_void_twodevice_test.py (real Flask app in a real OS process per
device, real FileRelay, the generic `http`/`sql` ops of _money_void_device.py).

WHAT EACH TEST PINS
  * paid_from travels with the payment (it is a fact about the payment), but the paid-out it
    caused does NOT: the receiving computer has no cash_movements row for it, so its own
    drawer is never touched by a payment it did not take.
  * cancel on the OTHER computer (no drawer there): the till that paid gets the cash back in
    its drawer, once, after the void reaches it -- and not twice when both computers cancel
    the same payment while offline.
  * a cancel on a computer that HAS an open drawer must not put the money into that drawer
    (it never lost it): the holder of the original paid-out decides.

MUTATIONS (each performed; red quoted in the crew report):
  M2-h  retail_api._record_payment: drop `'paid_from': paid_from` from the sync payload
        -> test_paid_from_travels_but_the_paid_out_does_not
  M2-i  sync_service payment apply: drop the paid_from UPDATE
        -> test_paid_from_travels_but_the_paid_out_does_not
  M2-j  retail_api.post_missing_void_reversals_all_companies: drop the drawer sweep
        -> test_cancel_on_the_other_computer_brings_the_cash_back_to_the_drawer_once

Run:
    pytest products/retail/tests/retail_money3_supplier_drawer_twodevice_test.py -v
"""
from __future__ import annotations

import json
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
    env = None
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


def _setup(tmp_path):
    """B takes a 30.000 cash supplier payment out of its open drawer (float 100) and pushes.
    Returns (A, B, relay, credA, credB, supplier id, session id, payment reference)."""
    A, B, relay = tmp_path / "A", tmp_path / "B", tmp_path / "relay.db"
    [shop] = dev(B, relay, [{"op": "create_shop", "price": 100.0, "initial_stock": 50}])
    credB = {"email": shop["email"], "password": shop["password"]}
    sup = http(B, relay, credB, "post", "/suppliers", {"name": "Two Device Supplies"})
    assert sup["status_code"] == 200, sup
    sup_id = sup["body"]["data"]["id"]
    sess = http(B, relay, credB, "post", "/cash-sessions/open", {"opening_float": 100.0})
    assert sess["status_code"] == 200, sess
    sid = sess["body"]["data"]["id"]
    pay = http(B, relay, credB, "post", f"/suppliers/{sup_id}/payments", {"amount": 30.0})
    assert pay["status_code"] == 200 and pay["body"]["data"]["paid_from"] == "drawer", pay
    ref = pay["body"]["data"]["reference"]
    dev(B, relay, [{"op": "push"}])
    [boot] = dev(A, relay, [{"op": "bootstrap"}])
    dev(A, relay, [{"op": "pull"}])
    credA = {"email": boot["email"], "password": boot["password"]}
    return A, B, relay, credA, credB, sup_id, sid, ref


def _expected(app, relay, cred, sid):
    r = http(app, relay, cred, "get", f"/cash-sessions/{sid}/x-report")
    assert r["status_code"] == 200, r
    return r["body"]["data"]["expected_cash"]


def _payment_id(app, relay, ref):
    return sql(app, relay, "SELECT id FROM payments WHERE reference=?", (ref,))[0]["id"]


def _drawer_rows(app, relay):
    return sql(app, relay, "SELECT type FROM cash_movements WHERE payment_uid IS NOT NULL")


def test_paid_from_travels_but_the_paid_out_does_not(tmp_path):
    A, B, relay, credA, credB, sup_id, sid, ref = _setup(tmp_path)
    assert _expected(B, relay, credB, sid) == 70.0
    got = sql(A, relay, "SELECT paid_from, method, status FROM payments WHERE reference=?", (ref,))
    assert got == [{"paid_from": "drawer", "method": "cash", "status": "active"}], got
    assert _drawer_rows(A, relay) == [], "the receiving computer must not grow a drawer row"


def test_cancel_on_the_other_computer_brings_the_cash_back_to_the_drawer_once(tmp_path):
    A, B, relay, credA, credB, sup_id, sid, ref = _setup(tmp_path)
    pid_a = _payment_id(A, relay, ref)
    v = http(A, relay, credA, "post", f"/payments/{pid_a}/void", {"reason": "wrong supplier"})
    assert v["status_code"] == 200, v
    assert _drawer_rows(A, relay) == [], "A has no drawer: nothing may be written there"
    assert _expected(B, relay, credB, sid) == 70.0, "B has not heard about the cancel yet"
    sync_rounds(A, B, relay)
    assert _expected(B, relay, credB, sid) == 100.0, "the cash did not come back to the till that paid"
    sync_rounds(A, B, relay)    # replays and echoes add nothing
    assert _expected(B, relay, credB, sid) == 100.0
    types = sorted(r["type"] for r in _drawer_rows(B, relay))
    assert types == ["paid_in", "paid_out"], types
    assert _drawer_rows(A, relay) == []


def test_both_computers_cancelling_the_same_payment_offline_come_back_once(tmp_path):
    A, B, relay, credA, credB, sup_id, sid, ref = _setup(tmp_path)
    for app, cred in ((A, credA), (B, credB)):
        pid = _payment_id(app, relay, ref)
        v = http(app, relay, cred, "post", f"/payments/{pid}/void", {"reason": "both"})
        assert v["status_code"] == 200, v
    sync_rounds(A, B, relay, rounds=3)
    assert _expected(B, relay, credB, sid) == 100.0
    types = sorted(r["type"] for r in _drawer_rows(B, relay))
    assert types == ["paid_in", "paid_out"], types


def test_a_cancel_on_a_computer_with_its_own_open_drawer_does_not_credit_that_drawer(tmp_path):
    A, B, relay, credA, credB, sup_id, sid, ref = _setup(tmp_path)
    sa = http(A, relay, credA, "post", "/cash-sessions/open", {"opening_float": 40.0})
    assert sa["status_code"] == 200, sa
    sid_a = sa["body"]["data"]["id"]
    pid_a = _payment_id(A, relay, ref)
    assert http(A, relay, credA, "post", f"/payments/{pid_a}/void", {"reason": "x"})["status_code"] == 200
    assert _expected(A, relay, credA, sid_a) == 40.0, "A's drawer never lost this cash"
    sync_rounds(A, B, relay)
    assert _expected(A, relay, credA, sid_a) == 40.0
    assert _expected(B, relay, credB, sid) == 100.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-x"]))
