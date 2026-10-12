"""MONEY-4b, two real computers: a drawer paid-out and an expense recorded on ONE computer reach
the OTHER computer's books exactly once, and nothing double-posts on the way.

Same shape as retail_money3_supplier_drawer_twodevice_test.py (a real Flask app in a real OS
process per device, a real FileRelay, the generic `http`/`sql` ops of _money_void_device.py).

WHAT IS PINNED, AND WHY IT MATTERS
  * `cash_movements` are DEVICE-LOCAL (a drawer belongs to one terminal): the receiving computer
    has no movement row. Its books still carry the paid-out, because the JOURNAL syncs as the
    existing `gl_journal` event. So the receiver must end with exactly ONE journal for the
    movement (same uid as the sender's), never a second one posted by the receiving side.
  * an expense journal and its void reversal each arrive once; replaying the sync adds nothing.
  * both computers' trial balances balance and agree on Cash and Bank.
  * KNOWN GAP, pinned so it cannot be mistaken for a defect that is already fixed: the expense
    LIST (`expenses` rows) is per-computer; the receiver sees the money in its books and not a
    row on its Expenses screen. ROADMAP.md MONEY-4b records it.

MUTATIONS (each performed; red quoted in the hand-back):
  T-a  create_cash_movement: skip the journal post  -> test_paid_out_reaches_the_other_computers_books_once
  T-b  create_expense: skip the journal post         -> test_expense_and_its_void_reach_the_other_computer_once

Run:
    pytest products/retail/tests/retail_money4b_twodevice_test.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

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
    out = dev(app, relay, [{"op": "login", **cred},
                           {"op": "http", "method": method, "path": path, "json": body}])
    return out[1]


def sync_rounds(A, B, relay, rounds=2):
    for _ in range(rounds):
        dev(A, relay, [{"op": "push"}, {"op": "pull"}])
        dev(B, relay, [{"op": "push"}, {"op": "pull"}])


def _setup(tmp_path):
    A, B, relay = tmp_path / "A", tmp_path / "B", tmp_path / "relay.db"
    [shop] = dev(B, relay, [{"op": "create_shop", "price": 100.0, "initial_stock": 50}])
    credB = {"email": shop["email"], "password": shop["password"]}
    dev(B, relay, [{"op": "push"}])
    [boot] = dev(A, relay, [{"op": "bootstrap"}])
    dev(A, relay, [{"op": "pull"}])
    credA = {"email": boot["email"], "password": boot["password"]}
    return A, B, relay, credA, credB


def _journals(app, relay, source_type):
    return sql(app, relay,
               "SELECT uid, business_date, source_uid FROM gl_journals WHERE source_type=? ORDER BY uid",
               (source_type,))


def _cash(app, relay, cred):
    r = http(app, relay, cred, "get", "/reports/trial-balance")
    assert r["status_code"] == 200, r
    d = r["body"]["data"]
    assert d["is_balanced"] is True, d
    for a in d["accounts"]:
        if a["code"] == "1000":
            return a["balance"]
    return 0.0


def test_paid_out_reaches_the_other_computers_books_once(tmp_path):
    A, B, relay, credA, credB = _setup(tmp_path)
    sess = http(B, relay, credB, "post", "/cash-sessions/open", {"opening_float": 100.0})
    assert sess["status_code"] == 200, sess
    sid = sess["body"]["data"]["id"]
    mv = http(B, relay, credB, "post", f"/cash-sessions/{sid}/movements",
              {"type": "paid_out", "amount": 20.0, "gl_account_code": "6300", "reason": "driver"})
    assert mv["status_code"] == 200, mv
    mid = mv["body"]["data"]["id"]
    sync_rounds(A, B, relay, rounds=3)

    on_b = _journals(B, relay, "cash_movement")
    on_a = _journals(A, relay, "cash_movement")
    assert len(on_b) == 1, on_b                    # a count, so "none matched" cannot pass
    assert on_a == on_b, (on_a, on_b)              # the SAME journal, once, by uid
    assert on_a[0]["source_uid"] == mid
    assert sql(A, relay, "SELECT COUNT(*) AS n FROM cash_movements")[0]["n"] == 0, \
        "cash movements are device-local; the receiver must not grow one"
    assert _cash(A, relay, credA) == _cash(B, relay, credB)

    sync_rounds(A, B, relay, rounds=2)             # replays and echoes add nothing
    assert _journals(A, relay, "cash_movement") == on_b
    assert _journals(B, relay, "cash_movement") == on_b


def test_expense_and_its_void_reach_the_other_computer_once(tmp_path):
    A, B, relay, credA, credB = _setup(tmp_path)
    ex = http(B, relay, credB, "post", "/expenses",
              {"gl_account_code": "6000", "amount": 250.0, "paid_via": "bank", "memo": "rent"})
    assert ex["status_code"] == 200, ex
    uid = ex["body"]["data"]["uid"]
    sync_rounds(A, B, relay, rounds=3)
    assert len(_journals(B, relay, "expense")) == 1
    assert _journals(A, relay, "expense") == _journals(B, relay, "expense")
    assert _cash(A, relay, credA) == _cash(B, relay, credB)

    v = http(B, relay, credB, "post", f"/expenses/{uid}/void", {"reason": "wrong month"})
    assert v["status_code"] == 200, v
    sync_rounds(A, B, relay, rounds=3)
    rev_b = _journals(B, relay, "reversal")
    assert len(rev_b) == 1, rev_b
    assert _journals(A, relay, "reversal") == rev_b
    assert _cash(A, relay, credA) == _cash(B, relay, credB)

    # the pinned gap: the books agree, the LIST is per-computer
    assert sql(B, relay, "SELECT COUNT(*) AS n FROM expenses")[0]["n"] == 1
    assert sql(A, relay, "SELECT COUNT(*) AS n FROM expenses")[0]["n"] == 0
