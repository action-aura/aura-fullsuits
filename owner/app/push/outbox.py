"""The push outbox: what has to be announced, written with the sale itself.

enqueue_sale_waiting() is called INSIDE the transaction that records the
one-screen sale's PENDING payment (commercial_sales/direct_sale.py ->
subscriptions/services.py::record_payment(in_same_transaction=...)). It adds
one owner_push_outbox row and never commits: the payment's own commit carries
it. That is the whole crash story --
  * the payment committed  -> the row committed: a crash before the worker
    runs only delays the notification, the next tick sends it;
  * the payment rolled back -> the row rolled back: no notification for a
    sale that does not exist.
This is the e-invoicing outbox discipline (commercial_runtime/einvoicing/
outbox.py: the row is part of the business write, delivery is a worker's
job) in the shape Owner's own email queue already uses (app/mail/outbox.py).

Kill switch: with push off (no VAPID key, app/push/vapid.py) nothing is
written at all -- an install that never turns push on stores nothing extra,
and turning it on later does not suddenly announce every old sale.

Who is told is NOT decided here. The row names the sale; the worker resolves
the recipients when it sends (app/push/recipients.py), so somebody deactivated
or stripped of the right to confirm between the sale and the send is not told.
"""
from __future__ import annotations

import uuid

from app.extensions import db_session
from app.models.push import PushOutbox
from app.push.vapid import push_enabled

EVENT_SALE_WAITING = "SALE_AWAITING_CONFIRMATION"


def enqueue_sale_waiting(*, order_id: uuid.UUID, payment_id: uuid.UUID) -> PushOutbox | None:
    """Adds (does not commit) the outbox row for a newly recorded sale."""
    if not push_enabled():
        return None
    row = PushOutbox(event_type=EVENT_SALE_WAITING, sales_order_id=order_id, payment_record_id=payment_id, status="PENDING")
    db_session.add(row)
    return row
