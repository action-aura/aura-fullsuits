"""Aura Retail -- W3b: stock transfers shared between computers (schema v47).

WHY THIS MODULE EXISTS (W3b). A transfer's stock movements and their 1250/5910
journals already synced, but the `stock_transfers` header and its lines never
did. So a transfer sent from computer A could not be received on computer B
(404), B's books showed stock in transit that B's screen could not see, the
movement uids were random (the SAME transfer received on two computers while
offline added the stock twice), and a recall on one computer racing a receive on
another was not handled at all.

The small rules below live here because the API (api/retail_api.py), the sync
apply (commercial_runtime/sync/sync_service.py) and the ledger all need the SAME
answer, and none of them may import another's file (retail_api imports this
module, so importing back would be a cycle; sync_service cannot import products
code at all -- it carries a duplicate of `transfer_rank` and a test pins the two
equal, the way W4a did for the purchase-order namespace):

  * the movement uids -- pure functions of the transfer and its line, so the
    same send (or the same receipt) recorded on two computers collides on the
    unique `uid` index and moves the stock ONCE;
  * `transfer_rank` -- the order a transfer's state may only move forward in
    when two computers' views are merged;
  * `send_key` -- what the SEND journal's uid is derived from (W3b verify F1), so
    two sends at different costs post two journals that both reach every computer
    and the retail reconcile reverses the loser;
  * `smaller` -- the deterministic tie-break for a line fact two computers wrote
    differently (the smaller value wins on every computer).
  (The receive journal is posted PER LINE by the ledger itself -- verify F2 --
  so there is no per-receipt key any more.)

NO IMPORT OF api/ OR general_ledger: nothing here touches the database.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

#: NEVER CHANGE. Two computers must derive the SAME movement uid from the same
#: (transfer, line); a different namespace on one of them would make each mint
#: its own uid, the unique index would not collide, and the stock would move
#: twice -- silently, because nothing errors.
_TRANSFER_MOVEMENT_NAMESPACE = uuid.UUID('6c1f0d52-8e7b-4a39-b5d4-2f9a73e8c1b6')

#: Rank of a transfer's state, higher wins when two computers' views merge. It
#: is a function of the (status, recalled_at) PAIR, not of status alone, because
#: a 'cancelled' transfer means two different things: cancelled before it was
#: ever sent (goods never left) and recalled after it was sent (goods came
#: back). They must not be confused: a recall outranks the send it undoes, a
#: plain cancel does not.
RANK_PENDING = 0
RANK_CANCELLED_BEFORE_SEND = 1
RANK_IN_TRANSIT = 2
RANK_RECALLED = 3
RANK_RECEIVED = 4

#: The conflict reasons recorded when a merge meets a state it could not simply
#: order. Plain-language sentences for them live in retail_api's
#: `_SYNC_REVIEW_REASONS`; the strings are duplicated in sync_service (see above).
CONFLICT_SENT_AFTER_CANCEL = 'transfer_sent_after_cancel'
CONFLICT_RECEIVED_AND_RECALLED = 'transfer_received_and_recalled'
CONFLICT_SENT_TWICE = 'transfer_sent_twice'
CONFLICT_RECEIVED_TWICE = 'transfer_received_twice'


def qty_canon(quantity) -> str:
    """Canonical text of a quantity: `5`, `5.0` and `Decimal('5.00')` all give
    '5'; `50` gives '50' (never '5E+1'); `0.0` and `-0.0` give '0'. The movement
    uid of a receipt includes it, so a quantity written two ways by two
    computers must still be the SAME receipt."""
    d = Decimal(str(quantity))
    if d == 0:
        return '0'
    return format(d.normalize(), 'f')


def out_movement_uid(transfer_id: str, item_id: str) -> str:
    """The uid of the `transfer_out` movement SENDING line `item_id` of
    `transfer_id` -- deterministic, so it is the same on every computer."""
    return str(uuid.uuid5(_TRANSFER_MOVEMENT_NAMESPACE, f"{transfer_id}:{item_id}:out"))


def recall_movement_uid(transfer_id: str, item_id: str) -> str:
    """The uid of the movement that puts a recalled line back at the source."""
    return str(uuid.uuid5(_TRANSFER_MOVEMENT_NAMESPACE, f"{transfer_id}:{item_id}:recall"))


def in_movement_uid(transfer_id: str, item_id: str, quantity) -> str:
    """The uid of the `transfer_in` movement RECEIVING `quantity` of line
    `item_id`. The QUANTITY is part of the identity on purpose: the same
    receipt on two computers is the same movement (stock arrives once), but two
    DIFFERENT received quantities are two movements -- both move stock, and the
    merge flags the pair, rather than one silently winning and the other's
    stock vanishing."""
    return str(uuid.uuid5(_TRANSFER_MOVEMENT_NAMESPACE,
                          f"{transfer_id}:{item_id}:in:{qty_canon(quantity)}"))


def receive_line_key(transfer_id: str, line_key, quantity) -> str:
    """The key `general_ledger.post_transfer_receive` derives ONE LINE's receive
    journal uid from (W3b verify F2; it replaces the per-receipt `receipt_key`).
    The same line counted the same on two computers is the same journal, exactly
    like its stock movement (`in_movement_uid`); the same line counted
    differently is a different journal, on every computer alike."""
    return f"{transfer_id}:{line_key}:{qty_canon(quantity)}"


def costs_canon(item_costs) -> str:
    """W3b verify F1. `item_costs` is an iterable of (item_id, unit_cost); the
    canonical text is sorted by item id, like `lines_canon`. It is part of the
    SEND journal's identity: two computers that each sent the same transfer
    while offline, at DIFFERENT costs, must post two DIFFERENT journals (and
    both must reach every computer) instead of colliding on one uid and leaving
    each computer with only its own figure -- see `send_key`."""
    return ";".join(f"{item_id}={qty_canon(cost)}"
                    for item_id, cost in sorted(item_costs, key=lambda p: str(p[0])))


def send_key(transfer_id: str, item_costs) -> str:
    """The key `general_ledger.post_transfer_send` derives the SEND journal's
    uid from (W3b verify F1). Same costs -> same key -> one journal on every
    computer; different costs -> a different key -> both journals post, and the
    retail reconcile reverses the loser (the one whose costs are not the
    converged line costs)."""
    return f"{transfer_id}:{costs_canon(item_costs)}"


def smaller(local, incoming):
    """W3b verify F1. The deterministic tie-break for a line fact two computers
    wrote differently (`unit_cost`, `quantity_received`): the SMALLER value
    wins, on every computer, in any arrival order -- so all converge. An absent
    value never beats a real one. Twin of `SyncService._transfer_smaller`; a
    test pins the two equal."""
    if local is None:
        return incoming
    if incoming is None:
        return local
    return incoming if Decimal(str(incoming)) < Decimal(str(local)) else local


def transfer_rank(status, recalled_at=None) -> int:
    """Merge rank of a transfer's state; see the RANK_* constants. Raises
    ValueError for a status this build does not know, so a garbage event is
    refused loudly rather than quietly out-ranking real state."""
    if status == 'pending':
        return RANK_PENDING
    if status == 'in_transit':
        return RANK_IN_TRANSIT
    if status == 'received':
        return RANK_RECEIVED
    if status == 'cancelled':
        return RANK_RECALLED if recalled_at else RANK_CANCELLED_BEFORE_SEND
    raise ValueError(f"unknown transfer status {status!r}")


def earliest(local, incoming):
    """Set-once merge of a timestamp: the earliest non-empty value wins, and an
    empty one never replaces a real one. (Timestamps here are 'YYYY-MM-DD
    HH:MM:SS' text, which orders correctly as a string.)"""
    a = local or None
    b = incoming or None
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)
