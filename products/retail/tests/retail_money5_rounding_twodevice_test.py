"""MONEY-5 verifier M2 (real, two OS processes): a rounded cash sale and its part-returns made on
computer A reach computer B with the SAME rounding figures and the SAME books.

Each process is a REAL Flask app with its OWN database, joined only by a file relay (harness
`_money_void_device.py`, as the MONEY-3 two-device tests). The rounding rides inside the `sale` and
`return` sync events (`rounding_amount`); the journals ride as `gl_journal` events. If either bind
broke, B would show a phantom debt / a trial balance that disagrees with A.

MUTATIONS (performed): bind None for the sale's rounding_amount in sync_service's peer INSERT
-> test_the_rounded_sale_reaches_the_peer_with_the_same_rounding_and_the_same_books RED (B's
sales.rounding_amount is NULL).

Run (one file per process):
    pytest products/retail/tests/retail_money5_rounding_twodevice_test.py -v
"""
from __future__ import annotations

import uuid

from retail_money3_split_refund_twodevice_test import _books, dev, http, sql, sync_rounds  # noqa: F401


def _shop(tmp_path):
    A, B, relay = tmp_path / "A", tmp_path / "B", tmp_path / "relay.db"
    [shop] = dev(A, relay, [{"op": "create_shop", "price": 3.457, "initial_stock": 50}])
    cred_a = {"email": shop["email"], "password": shop["password"]}
    r = http(A, relay, cred_a, "post", "/settings/credit",
             {"cash_rounding_interval": "0.050", "cash_rounding_rule": "nearest"})
    assert r["status_code"] == 200, r
    return A, B, relay, cred_a, shop["product_id"]


def test_the_rounded_sale_reaches_the_peer_with_the_same_rounding_and_the_same_books(tmp_path):
    A, B, relay, cred_a, pid = _shop(tmp_path)
    sale = http(A, relay, cred_a, "post", "/sales", {
        "items": [{"product_id": pid, "quantity": 1}], "payment_method": "cash", "amount_paid": 3.45,
        "cash_rounding_ack": True, "idempotency_key": str(uuid.uuid4())})
    assert sale["status_code"] == 200, sale
    assert sale["body"]["data"]["rounding_amount"] == -0.007
    dev(A, relay, [{"op": "push"}])
    [boot] = dev(B, relay, [{"op": "bootstrap"}])
    sync_rounds(A, B, relay)
    cred_b = {"email": boot["email"], "password": boot["password"]}

    ra = sql(A, relay, "SELECT total, amount_paid, rounding_amount FROM sales")
    rb = sql(B, relay, "SELECT total, amount_paid, rounding_amount FROM sales")
    assert ra == rb == [{"total": 3.457, "amount_paid": 3.45, "rounding_amount": -0.007}], (ra, rb)
    books_a, da = _books(A, relay, cred_a)
    books_b, db = _books(B, relay, cred_b)
    assert books_a == books_b, (books_a, books_b)
    assert da["is_balanced"] is True and db["is_balanced"] is True
    assert books_b["4950"] == (0.007, 0.0) and books_b["1010"] == (3.45, 0.0), books_b


def test_part_returns_of_the_rounded_sale_leave_both_computers_with_identical_books(tmp_path):
    A, B, relay, cred_a, pid = _shop(tmp_path)
    s = http(A, relay, cred_a, "post", "/sales", {
        "items": [{"product_id": pid, "quantity": 4}], "payment_method": "cash", "amount_paid": 1000,
        "cash_rounding_ack": True, "idempotency_key": str(uuid.uuid4())})
    assert s["status_code"] == 200, s
    sale_id = s["body"]["data"]["id"]
    got = 0.0
    for _ in range(4):
        r = http(A, relay, cred_a, "post", "/returns", {
            "sale_id": sale_id, "items": [{"product_id": pid, "quantity": 1}], "refund_method": "cash",
            "cash_rounding_ack": True, "idempotency_key": str(uuid.uuid4())})
        assert r["status_code"] == 200, r
        got += r["body"]["data"]["tender_refund_amount"]
    taken = sql(A, relay, "SELECT SUM(amount) AS a FROM payments WHERE direction='in' AND method='cash'")[0]["a"]
    assert taken == 13.85, taken                              # 4 x 3.457 = 13.828 rounded to 0.050
    assert round(got, 3) == taken, (got, taken)               # everything the sale took came back
    dev(A, relay, [{"op": "push"}])
    [boot] = dev(B, relay, [{"op": "bootstrap"}])
    sync_rounds(A, B, relay)
    cred_b = {"email": boot["email"], "password": boot["password"]}
    qa = sql(A, relay, "SELECT return_number, tender_refund_amount, rounding_amount FROM returns ORDER BY id")
    qb = sql(B, relay, "SELECT return_number, tender_refund_amount, rounding_amount FROM returns ORDER BY id")
    assert len(qa) == 4 and qa == qb, (qa, qb)
    books_a, da = _books(A, relay, cred_a)
    books_b, db = _books(B, relay, cred_b)
    assert books_a == books_b, (books_a, books_b)
    assert da["is_balanced"] is True and db["is_balanced"] is True
    # a full return in parts nets the sale's rounding to zero, on BOTH computers
    for books in (books_a, books_b):
        d, c = books.get("4950", (0.0, 0.0))
        assert round(d - c, 3) == 0.0, books.get("4950")
