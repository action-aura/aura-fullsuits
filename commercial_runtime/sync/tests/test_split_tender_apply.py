"""Split tender over sync: a sale paid by several methods arrives as ONE sale
event (payment_method='split') plus one payment event per tender, and the
receiving device must end up with exactly those rows -- once, even when the
batch is delivered again. Reuses test_sync_service.py's harness (fixtures and
event builders) rather than a second copy of it.
"""
import uuid

from commercial_runtime.sync.tests.test_sync_service import (  # noqa: F401
    _apply, _payment_event, _payments, _sale_event, _sales, db_path, get_conn,
)


def _split_batch(sale_uid):
    return [
        _sale_event(uid=sale_uid, payment_method="split", total=11.832, amount_paid=11.832),
        _payment_event(sale_uid=sale_uid, method="card", amount=5.0, reference="RCPT-1"),
        _payment_event(sale_uid=sale_uid, method="cash", amount=6.832, reference="RCPT-2"),
    ]


def test_split_sale_arrives_as_one_sale_and_two_linked_payments(get_conn):
    sale_uid = str(uuid.uuid4())
    _apply(get_conn, _split_batch(sale_uid), cursor=1)
    sales = _sales(get_conn)
    assert len(sales) == 1 and sales[0]["payment_method"] == "split"
    pays = sorted(_payments(get_conn), key=lambda p: p["method"])
    assert [(p["method"], p["amount"]) for p in pays] == [("card", 5.0), ("cash", 6.832)]
    assert {p["sale_id"] for p in pays} == {sales[0]["id"]}, "both tenders must be linked to the sale"


def test_redelivering_the_split_batch_changes_nothing(get_conn):
    sale_uid = str(uuid.uuid4())
    batch = _split_batch(sale_uid)
    for n in range(3):
        _apply(get_conn, batch, cursor=n + 1)
    assert len(_sales(get_conn)) == 1
    assert sum(p["amount"] for p in _payments(get_conn)) == 11.832
    assert len(_payments(get_conn)) == 2
