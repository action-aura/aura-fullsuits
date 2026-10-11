"""MONEY-3 M4, two real computers: conflicting offline cheque transitions leave IDENTICAL, BALANCED books.

D1: A bounces a deposited cheque while B clears it, both offline. Before M4 each computer posted
journals from its own fold, so both ended with 1150 = -80 / 1000 = +80. D2 (found while planning):
when A's bounce sorts first, the fold admits B's `cleared` as an APPLY crossing at index 2 that no
computer ever wrote a payments row for, so the status said "cleared" while the customer still owed.

Same harness as retail_money3_cheque_books_twodevice_test.py (two OS processes over a FileRelay,
driven through the real routes). Each scenario also holds a second, untouched pending cheque so
"1150 equals the live uncleared cheques" is a non-trivial equation.

MUTATIONS (each performed; red quoted in the crew report):
  R-m  sync_service `cheque_event` apply: do not enqueue the cheque for reconciliation
         -> every race test (the books stay as each writer posted them)
  R-n  retail_api.reconcile_cheque: skip the missing-payment writer
         -> test_bounce_sorts_first_clear_is_admitted_as_a_crossing (party balance D2 half)
  R-o  cheques.desired_postings: always 'reverse'
         -> test_clear_sorts_first_the_bounce_reverses_the_bank_leg

Run:
    pytest products/retail/tests/retail_money3_cheque_reconcile_twodevice_test.py -v
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retail_money3_cheque_books_twodevice_test import (  # noqa: E402
    books, dev, http, sql, sync_rounds, _cheque_on, _setup)


def _converged(A, B, relay, credA, credB, chq, cust):
    """The checks every scenario shares; returns (ledger, folded status, customer balance)."""
    ledA, tbA = books(A, relay, credA)
    ledB, tbB = books(B, relay, credB)
    assert ledA == ledB, (ledA, ledB)
    for tb in (tbA, tbB):
        assert tb["is_balanced"] is True, tb
        assert tb["subledger"]["ar_ties"] is True, tb["subledger"]
    sa = http(A, relay, credA, "get", f"/cheques/{chq}")["body"]["data"]["cheque"]["status"]
    sb = http(B, relay, credB, "get", f"/cheques/{chq}")["body"]["data"]["cheque"]["status"]
    assert sa == sb, (sa, sb)
    bal = lambda app: sql(app, relay, "SELECT credit_balance FROM customers WHERE id=?", (cust,))[0]["credit_balance"]
    assert bal(A) == bal(B), (bal(A), bal(B))
    pays = lambda app: sorted(r["uid"] for r in sql(app, relay, "SELECT uid FROM payments WHERE related_type='cheque'"))
    assert pays(A) == pays(B), (pays(A), pays(B))
    jr = lambda app: sorted(r["uid"] for r in sql(app, relay, "SELECT uid FROM gl_journals WHERE source_type='cheque'"))
    assert jr(A) == jr(B), (jr(A), jr(B))
    return ledA, sa, bal(A), pays(A)


def _race(tmp_path, first, second, *, endorse=False):
    """B receives two cheques (the raced one, and a pending bystander), A gets them, and they both
    act offline: `first` on one computer, then `second` on the other. `first` sorts first."""
    A, B, relay, credA, credB, cust = _setup(tmp_path)
    chq = _cheque_on(B, relay, credB, cust, 80.0)
    bystander = _cheque_on(B, relay, credB, cust, 30.0)
    if not endorse:
        assert http(B, relay, credB, "post", f"/cheques/{chq}/deposit", {})["status_code"] == 200
    dev(B, relay, [{"op": "push"}])
    dev(A, relay, [{"op": "pull"}])
    sup = http(B, relay, credB, "post", "/suppliers", {"name": "Endorsee"})["body"]["data"]["id"] if endorse else None
    if endorse:
        dev(B, relay, [{"op": "push"}])
        dev(A, relay, [{"op": "pull"}])
    who = {"A": (A, credA), "B": (B, credB)}

    def act(side, action):
        app, cred = who[side]
        body = {"reason": "returned"} if action == "bounce" else {}
        if action == "endorse":
            body = {"to_party_type": "supplier", "to_party_id": sup}
        r = http(app, relay, cred, "post", f"/cheques/{chq}/{action}", body)
        assert r["status_code"] == 200, r
    act(*first)
    act(*second)
    sync_rounds(A, B, relay, rounds=3)
    return A, B, relay, credA, credB, cust, chq, bystander


def test_bounce_sorts_first_clear_is_admitted_as_a_crossing(tmp_path):
    A, B, relay, credA, credB, cust, chq, by = _race(tmp_path, ("A", "bounce"), ("B", "clear"))
    ledger, status, balance, pays = _converged(A, B, relay, credA, credB, chq, cust)
    assert status == "cleared"
    # bounced then admitted-cleared: the money is IN. 200 owed - 80 (cleared) - 30 (bystander) = 90
    assert balance == 90, balance
    assert len(pays) == 3 + 1, pays            # cheque crossings 0,1,2 + the bystander's apply
    assert ledger["1150"] == 30, ledger         # only the pending bystander is under collection
    assert ledger["1000"] == 80, ledger
    assert ledger["1100"] == 90, ledger


def test_clear_sorts_first_the_bounce_reverses_the_bank_leg(tmp_path):
    A, B, relay, credA, credB, cust, chq, by = _race(tmp_path, ("B", "clear"), ("A", "bounce"))
    ledger, status, balance, pays = _converged(A, B, relay, credA, credB, chq, cust)
    assert status == "bounced"
    assert balance == 170, balance              # 200 owed - 30 bystander; the 80 is owed again
    assert len(pays) == 2 + 1, pays
    assert ledger["1150"] == 30 and ledger["1000"] == 0 and ledger["1100"] == 170, ledger


def test_bounce_and_endorse_bounce_first(tmp_path):
    A, B, relay, credA, credB, cust, chq, by = _race(tmp_path, ("A", "bounce"), ("B", "endorse"), endorse=True)
    ledger, status, balance, pays = _converged(A, B, relay, credA, credB, chq, cust)
    assert status == "endorsed"
    assert balance == 90, balance
    assert ledger["1150"] == 30 and ledger["1000"] == 80 and ledger["1100"] == 90, ledger


def test_bounce_and_endorse_endorse_first(tmp_path):
    A, B, relay, credA, credB, cust, chq, by = _race(tmp_path, ("B", "endorse"), ("A", "bounce"), endorse=True)
    ledger, status, balance, pays = _converged(A, B, relay, credA, credB, chq, cust)
    assert status == "bounced"
    assert balance == 170, balance
    assert ledger["1150"] == 30 and ledger["1000"] == 0 and ledger["1100"] == 170, ledger


def _snapshot(A, B, relay):
    q = lambda app: (sql(app, relay, "SELECT COUNT(*) n FROM gl_journals")[0]["n"],
                     sql(app, relay, "SELECT COUNT(*) n FROM payments")[0]["n"],
                     sql(app, relay, "SELECT COUNT(*) n FROM gl_journals WHERE reversed_journal_id IS NOT NULL")[0]["n"])
    return q(A), q(B)


def test_replaying_the_whole_batch_twice_changes_nothing(tmp_path):
    A, B, relay, credA, credB, cust, chq, by = _race(tmp_path, ("A", "bounce"), ("B", "clear"))
    before = _snapshot(A, B, relay)
    ledger_before = books(A, relay, credA)[0]
    assert ledger_before["1150"] == 30 and ledger_before["1000"] == 80 and ledger_before["1100"] == 90, ledger_before
    # duplicate every relayed event under a fresh sequence number, then pull both computers again
    conn = sqlite3.connect(str(relay))
    conn.execute("INSERT INTO relay_events (payload) SELECT payload FROM relay_events")
    conn.commit(); conn.close()
    sync_rounds(A, B, relay, rounds=2)
    assert _snapshot(A, B, relay) == before
    assert books(A, relay, credA)[0] == ledger_before
    _converged(A, B, relay, credA, credB, chq, cust)


def test_a_journal_that_arrives_before_its_event_converges(tmp_path):
    """The relay delivers A's bounce JOURNAL to B while the bounce EVENT is held back; the event
    follows in a later pull. B must end with the same books as A, balanced."""
    A, B, relay, credA, credB, cust = _setup(tmp_path)
    chq = _cheque_on(B, relay, credB, cust, 80.0)
    dev(B, relay, [{"op": "push"}])
    dev(A, relay, [{"op": "pull"}])
    assert http(A, relay, credA, "post", f"/cheques/{chq}/bounce", {"reason": "nsf"})["status_code"] == 200
    dev(A, relay, [{"op": "push"}])
    conn = sqlite3.connect(str(relay))
    held = []
    for seq, payload in conn.execute("SELECT seq, payload FROM relay_events").fetchall():
        ev = json.loads(payload)
        inner = ev.get("payload")
        inner = json.loads(inner) if isinstance(inner, str) else (inner or {})
        if ev.get("entity_type") == "cheque_event" and inner.get("event_type") == "bounced":
            held.append((seq, payload))
    assert len(held) == 1, held
    conn.execute("DELETE FROM relay_events WHERE seq=?", (held[0][0],))
    conn.commit()
    dev(B, relay, [{"op": "pull"}])                      # journal + payment arrive, the event does not
    jr = sql(B, relay, "SELECT uid FROM gl_journals WHERE source_type='cheque'")
    assert len(jr) == 2, jr                              # apply0 (own) + A's reverse1
    conn.execute("INSERT INTO relay_events (payload) VALUES (?)", (held[0][1],))
    conn.commit(); conn.close()
    sync_rounds(A, B, relay, rounds=3)
    ledger, status, balance, pays = _converged(A, B, relay, credA, credB, chq, cust)
    assert status == "bounced" and balance == 200 and ledger["1150"] == 0 and ledger["1100"] == 200, ledger


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-x"]))
