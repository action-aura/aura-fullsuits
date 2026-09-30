"""Aura Retail -- F3b: voiding a customer payment across TWO real computers.

Two defects the verifier (vYM2) reproduced, both invisible to the single-
computer suite `retail_payment_void_gl_test.py`:

  HIGH   the same payment voided on two computers before they sync was
         reversed TWICE (each computer minted its own random reversal uid, so
         the second one arriving was not recognised as the same fact).
  MEDIUM a payment voided on computer A BEFORE the payment's journal reached A
         found nothing to reverse, the journal then arrived as an ordinary
         active fact, and the payment stayed voided while its money stayed in
         the books on every computer, forever.

Every test rings the payment through the REAL Flask route in a REAL second OS
process over the REAL `FileRelay` (`_money_void_device.py`, a slim copy of the
stock-sync device with generic `http`/`sql` ops), never a hand-built event.
"Reversed once" is asserted as ROW COUNTS on both computers AND as the full
per-account trial balance equalling the balance from BEFORE the payment --
a count alone could pass with the wrong journal reversed, a balance alone with
two half-reversals.

MUTATIONS THIS FILE OWNS (each performed and the RED quoted in F3b's report):
  * general_ledger.reverse_journal: drop `uid=reversal_uid(...)` (random uid)
    -> test_the_same_payment_voided_on_two_offline_computers_is_reversed_once
  * schema.py: drop the ux_gl_journals_one_reversal index / the reverse_journal
    already-reversed check are pinned in retail_payment_void_gl_test.py (N11).
  * retail_api.void_payment: drop the `_post_missing_void_reversals` call, and
    install_void_reversal_sweep: drop the post-pull sweep
    -> test_void_before_the_payments_journal_arrived_is_reversed_once_it_lands
  * app.py: drop either install_void_reversal_sweep(...) line
    -> test_app_py_installs_the_sweep_on_both_retail_sync_services

Run:
    <repo venv python> products/retail/tests/retail_payment_void_twodevice_test.py
    pytest products/retail/tests/retail_payment_void_twodevice_test.py -v
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PRODUCT_DIR = TESTS_DIR.parent
DEVICE = TESTS_DIR / "_money_void_device.py"
APP_PY = PRODUCT_DIR / "backend" / "app.py"
PY = sys.executable


def dev(app_dir, relay, actions, timeout=300):
    work = Path(app_dir).parent / f"_act_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    a, o = work / "a.json", work / "o.json"
    a.write_text(json.dumps(actions), encoding="utf-8")
    p = subprocess.run([PY, str(DEVICE), str(app_dir), str(relay), str(a), str(o)],
                       capture_output=True, text=True, timeout=timeout)
    assert p.returncode == 0, p.stderr[-3000:]
    return json.loads(o.read_text(encoding="utf-8"))


def sql(app, relay, q, params=()):
    return dev(app, relay, [{"op": "sql", "q": q, "params": list(params)}])[0]["rows"]


def books(app, relay, cred):
    """({account code: balance}, subledger) as this computer's trial balance."""
    r = dev(app, relay, [{"op": "login", **cred}, {"op": "trial_balance"}])[1]
    assert r["status_code"] == 200, r
    d = r["body"]["data"]
    return {a["code"]: a["balance"] for a in d["accounts"]}, d.get("subledger")


def reversal_rows(app, relay):
    return sql(app, relay, "SELECT id, uid, reversed_journal_id, business_date, posted_by "
                           "FROM gl_journals WHERE reversed_journal_id IS NOT NULL ORDER BY id")


def _setup(tmp_path):
    """B rings a credit sale (20.000 paid) then takes a 30.000 customer payment
    and pushes. Returns (A, B, relay, credB, payment reference, B's books from
    BEFORE the payment)."""
    A, B, relay = tmp_path / "A", tmp_path / "B", tmp_path / "relay.db"
    [shop] = dev(B, relay, [{"op": "create_shop", "price": 100.0, "initial_stock": 50}])
    credB = {"email": shop["email"], "password": shop["password"]}
    _, cust = dev(B, relay, [{"op": "login", **credB}, {"op": "create_customer", "name": "Void Cust"}])
    cid = cust["customer_id"]
    _, s = dev(B, relay, [{"op": "login", **credB},
                          {"op": "sell_credit", "product_id": shop["product_id"], "quantity": 1,
                           "customer_id": cid, "amount_paid": 20.0}])
    assert s["status_code"] == 200, s
    before = books(B, relay, credB)
    _, pay = dev(B, relay, [{"op": "login", **credB},
                            {"op": "customer_payment", "customer_id": cid, "amount": 30.0}])
    assert pay["status_code"] == 200, pay
    ref = pay["body"]["data"]["reference"]
    dev(B, relay, [{"op": "push"}])
    return A, B, relay, credB, ref, before


def _void(app, relay, cred, ref):
    """Void the payment TWICE on one computer; the second must be refused."""
    pid = sql(app, relay, "SELECT id FROM payments WHERE reference=?", (ref,))[0]["id"]
    out = dev(app, relay, [
        {"op": "login", **cred},
        {"op": "http", "method": "post", "path": f"/payments/{pid}/void", "json": {"reason": "f3b"}},
        {"op": "http", "method": "post", "path": f"/payments/{pid}/void", "json": {"reason": "again"}}])
    return out[1], out[2]


def _sync_round(A, B, relay, rounds=2):
    for _ in range(rounds):
        dev(A, relay, [{"op": "push"}, {"op": "pull"}])
        dev(B, relay, [{"op": "push"}, {"op": "pull"}])


def _bootstrap_a(A, relay):
    [boot] = dev(A, relay, [{"op": "bootstrap"}])
    dev(A, relay, [{"op": "pull"}])
    return {"email": boot["email"], "password": boot["password"]}


def test_void_on_the_receiving_computer_returns_both_ledgers_to_before_the_payment(tmp_path):
    A, B, relay, credB, ref, (before, _) = _setup(tmp_path)
    credA = _bootstrap_a(A, relay)
    v1, v2 = _void(A, relay, credA, ref)
    assert v1["status_code"] == 200, v1
    assert v2["status_code"] == 409, v2   # the second void is refused, never doubled
    _sync_round(A, B, relay)
    ledA, _ = books(A, relay, credA)
    ledB, subB = books(B, relay, credB)
    assert ledA == ledB == before, (ledA, ledB, before)
    assert subB and subB["ar_ties"], subB
    assert len(reversal_rows(A, relay)) == 1 and len(reversal_rows(B, relay)) == 1


def test_the_same_payment_voided_on_two_offline_computers_is_reversed_once(tmp_path):
    A, B, relay, credB, ref, (before, _) = _setup(tmp_path)
    credA = _bootstrap_a(A, relay)
    va, _ = _void(A, relay, credA, ref)   # A has not pushed yet
    vb, _ = _void(B, relay, credB, ref)   # B has not pulled A's void
    assert va["status_code"] == 200 and vb["status_code"] == 200, (va, vb)
    # Both computers now hold their OWN reversal for the same original. The
    # deterministic uid is what makes them the same fact once they meet.
    assert len(reversal_rows(A, relay)) == 1 and len(reversal_rows(B, relay)) == 1
    _sync_round(A, B, relay)
    revA, revB = reversal_rows(A, relay), reversal_rows(B, relay)
    assert len(revA) == 1 and len(revB) == 1, (revA, revB)
    assert revA[0]["uid"] == revB[0]["uid"], "the two computers minted different reversal uids"
    ledA, subA = books(A, relay, credA)
    ledB, subB = books(B, relay, credB)
    assert ledA == before and ledB == before, (
        "the payment was reversed twice (or not at all)", ledA, ledB, before)
    # Only B (the computer that TOOK the payment) is asked to tie its customer
    # subledger to the books. A never carried the customer's credit_balance
    # for that sale (measured: 0 before A's void, 30 after, against 80 in the
    # GL) -- a receiving-computer subledger seeding gap that exists with a
    # single void too, is not a void defect, and lives in sync_service.py
    # (not this crew's file). Recorded as a residual, not asserted away.
    assert subB and subB["ar_ties"], subB
    # The losing duplicate must not have been quarantined either -- same uid
    # falls through the apply's ON CONFLICT, it is not parked as a duplicate.
    for app in (A, B):
        parked = sql(app, relay, "SELECT entity_type, reason FROM sync_apply_quarantine")
        assert not [r for r in parked if "reversal" in (r["reason"] or "")], parked


def test_void_before_the_payments_journal_arrived_is_reversed_once_it_lands(tmp_path):
    A, B, relay, credB, ref, (before, _) = _setup(tmp_path)
    juid = sql(B, relay, "SELECT uid FROM gl_journals WHERE reference=?", (ref,))[0]["uid"]
    c = sqlite3.connect(str(relay))
    row = c.execute("SELECT seq, payload FROM relay_events WHERE payload LIKE ?",
                    (f'%"entity_id": "{juid}"%',)).fetchone()
    assert row, "the payment journal's event was not found in the relay"
    c.execute("DELETE FROM relay_events WHERE seq=?", (row[0],))
    c.commit()
    credA = _bootstrap_a(A, relay)
    assert not sql(A, relay, "SELECT id FROM gl_journals WHERE uid=?", (juid,)), \
        "fixture broken: the journal is already on A"
    v1, _ = _void(A, relay, credA, ref)
    assert v1["status_code"] == 200, v1
    assert reversal_rows(A, relay) == [], "nothing to reverse yet, the journal is not here"
    # The journal now arrives, late, as an ordinary active fact.
    c.execute("INSERT INTO relay_events (payload) VALUES (?)", (row[1],))
    c.commit()
    c.close()
    _sync_round(A, B, relay)
    revA, revB = reversal_rows(A, relay), reversal_rows(B, relay)
    assert len(revA) == 1 and len(revB) == 1, (revA, revB)
    ledA, subA = books(A, relay, credA)
    ledB, subB = books(B, relay, credB)
    assert ledA == before and ledB == before, (ledA, ledB, before)
    assert subB and subB["ar_ties"], subB   # see the subledger note in the test above


def test_app_py_installs_the_sweep_on_both_retail_sync_services():
    """The harness installs the sweep itself, so nothing above proves app.py
    does. Pin the wiring: each retail SyncService that pulls into retail.db is
    followed by its install call. The registry-stream services are not wrapped
    (they never carry GL journals)."""
    src = APP_PY.read_text(encoding="utf-8")
    assert re.search(r"^from api\.retail_api import .*\binstall_void_reversal_sweep\b", src, re.M)
    for name in ("_sync_service", "_android_sync_service"):
        assert re.search(rf"^\s+install_void_reversal_sweep\({name}\)\s*$", src, re.M), (
            f"app.py never calls install_void_reversal_sweep({name})")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-x"]))
