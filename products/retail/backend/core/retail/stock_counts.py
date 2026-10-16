"""Aura Retail -- STOCK-TAKE: counting the shelf and posting the difference (schema v57).

WHY THIS MODULE EXISTS. A shop counts its shelves, sometimes on two computers at
once (one person scans aisle 1 on the till, another aisle 2 on the back-office
PC), and then somebody presses "post". The small rules below live here because the
API (api/retail_api.py), the sync apply (commercial_runtime/sync/sync_service.py,
which cannot import products code and carries duplicates that a test pins equal)
and the tests all need the SAME answer:

  * `line_uid` / `count_movement_uid` -- pure functions of the count and the
    product. The SAME product counted on two computers is ONE line, and the SAME
    count posted on two computers writes ONE stock movement per product: the
    second computer's insert collides on the unique `uid` index and moves
    nothing. Random uids here would double the correction silently -- nothing
    errors, the shelf just ends up wrong by the variance a second time.
  * `status_rank` -- the order a count's state may only move forward in when two
    computers' views are merged (open < cancelled < posted: posted wins, because
    its stock movements are real and travel on their own).
  * `variance` -- counted minus what the system said when the line was FIRST
    counted. Not minus "the balance at post time": sales that happen between the
    count and the post are real and must survive the correction.

NO IMPORT OF api/ OR general_ledger: nothing here touches the database.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

#: NEVER CHANGE. Two computers must derive the SAME uid from the same (count,
#: product); a different namespace on one of them would make each mint its own
#: uid, the unique index would not collide, and the stock would move twice.
_STOCK_COUNT_NAMESPACE = uuid.UUID('b3d1c9a4-5e27-4f08-9a6c-7e41d0f2a8c5')

STATUS_OPEN = 'open'
STATUS_CANCELLED = 'cancelled'
STATUS_POSTED = 'posted'
#: Derived locally on every computer (never on the wire): a second OPEN count of one branch whose uid
#: lost to a smaller one. Its contributions live under the winner.
STATUS_MERGED = 'merged'

#: Higher wins when two computers' views of one count are merged. 'merged' ranks with 'cancelled':
#: an incoming 'posted' still outranks it (stock really moved), anything lower is ignored.
_STATUS_RANK = {STATUS_OPEN: 0, STATUS_CANCELLED: 1, STATUS_MERGED: 1, STATUS_POSTED: 2}


def status_rank(status):
    """Rank of a count status, or None for a value this build does not know
    (a newer peer's state is never guessed at)."""
    return _STATUS_RANK.get(status)


def line_uid(count_uid: str, product_id: str, device_id: str) -> str:
    """The row id of ONE device's contribution for `product_id` in `count_uid`. Deterministic at
    creation (same device + count + product = same id) and then just an opaque id: a merge moves
    the row under another count without renaming it."""
    return str(uuid.uuid5(_STOCK_COUNT_NAMESPACE, f"{count_uid}:{product_id}:{device_id}:line"))


def count_movement_uid(count_uid: str, product_id: str) -> str:
    """The uid of the inventory movement that posts `product_id`'s variance for
    `count_uid` -- the same on every computer, so posting twice (or on two
    computers) moves the stock once."""
    return str(uuid.uuid5(_STOCK_COUNT_NAMESPACE, f"{count_uid}:{product_id}:post"))


def late_movement_uid(count_uid: str, product_id: str, device_id: str, counted_at: str) -> str:
    """The uid of the correction a LATE contribution (one that reached the main computer after the
    count was posted) books: a function of the count, the product and the contribution's writer and
    timestamp, so applying the same late event twice moves the stock once."""
    return str(uuid.uuid5(_STOCK_COUNT_NAMESPACE, f"{count_uid}:{product_id}:late:{device_id}:{counted_at}"))


def merge_winner(uid_a: str, uid_b: str) -> str:
    """Of two OPEN counts of one branch, the one that survives: the smaller uid. Pure and
    symmetric, so every computer picks the same one whatever order the counts reached it."""
    return min(uid_a, uid_b)


def variance(counted_qty, system_qty_at_count) -> float:
    """counted - system, as a float rounded the way stock quantities are stored
    (Decimal subtraction first, so 0.3 - 0.1 is 0.2 and not 0.19999999999999998)."""
    return float(Decimal(str(counted_qty)) - Decimal(str(system_qty_at_count)))
