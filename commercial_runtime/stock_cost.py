"""The ONE rule for blending incoming stock into a branch's average cost.

STOCK-BOOKS verify F1. Before this module the rule was written out four times
(receive_purchase_order, receive_stock_transfer, recall, and the sync apply) and
the copies had drifted: the sync apply blended only purchase_in/transfer_in, so
a peer that learned stock through opening_stock + a PO receipt ended at a
different average than the computer that made them (A: 10 @ 2.000, adjust -3,
receive 7 @ 4.000 -> A 3.000, peer 4.000; peer stock value 49 vs books 42).

Lives in `commercial_runtime` (not core/retail) because the sync service is
shared across products and must not import a retail module, while the retail
routes need the identical arithmetic. Both import this file.

THE RULE
  Incoming movements that carry a cost (unit_cost > 0) of these types blend
  into the branch average: opening_stock, purchase_in, transfer_in, return_in.
  new_avg = (prior_qty * prior_avg + in_qty * in_cost) / (prior_qty + in_qty)
  EXCEPT when prior_qty <= 0 or prior_avg <= 0: then new_avg = in_cost. There
  is no cost worth carrying in free stock (avg 0 means "cost unknown", never
  "costs nothing") or in negative stock (an oversell recorded behind sync), so
  the observed incoming cost wins rather than a figure dragged along by a
  corrupt running total.
  Movements that remove stock, and manual stock_in (no cost information), never
  change the average.
"""
from __future__ import annotations

from decimal import Decimal

#: movement types that blend into a branch's average cost when they carry a cost.
BLENDING_MOVEMENT_TYPES = ('opening_stock', 'purchase_in', 'transfer_in', 'return_in')


def blended_unit_cost(prior_qty, prior_avg, incoming_qty, incoming_unit_cost) -> Decimal:
    """New average cost (a Decimal, NOT quantized -- callers quantize to their
    own precision) after `incoming_qty` units at `incoming_unit_cost` join
    `prior_qty` units averaging `prior_avg`. See the module docstring."""
    pq = Decimal(str(prior_qty or 0))
    pa = Decimal(str(prior_avg or 0))
    iq = Decimal(str(incoming_qty or 0))
    ic = Decimal(str(incoming_unit_cost or 0))
    if iq <= 0 or ic <= 0:
        # no quantity, or NO cost information (0 is the "unknown" sentinel, F2):
        # never drag a real average toward zero.
        return pa
    if pq <= 0 or pa <= 0:
        return ic
    return (pq * pa + iq * ic) / (pq + iq)
