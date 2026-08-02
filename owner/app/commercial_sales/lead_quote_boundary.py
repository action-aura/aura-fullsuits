"""Phase 9.5D Milestone 7 -- the Lead/Quote/Customer boundary.

A Quote may originate from a qualified Lead or a confirmed Customer
(Non-Negotiable Rule 13). For a Lead-based Quote, once ACCEPTED, the
Lead must be converted or linked to a Customer through the canonical
Phase 9.5C conversion service (app/leads/conversion.py::convert()) --
never reimplemented -- before an Order can be created. This module is
the single call site for that boundary; Milestone 8's Sales Order
creation calls it, never app/leads/conversion.py directly.

The boundary transaction must not create Payment/Subscription/License/
Installation/Commission (Non-Negotiable Rule 1/13) -- convert() itself
already guarantees this (Phase 9.5C, unchanged, re-verified here by
import-list grep in the test suite).
"""
from __future__ import annotations

import uuid

from app.commercial_sales.errors import CommercialSalesError
from app.extensions import db_session
from app.leads.conversion import DuplicateCustomerError, InvalidLeadStateError, convert
from app.models.commercial_sales import Quote
from app.models.customers import Customer
from app.models.leads import Lead


def resolve_customer_for_accepted_quote(
    quote: Quote, *, actor_staff_user_id: uuid.UUID, idempotency_key: str
) -> Customer:
    """Returns the Quote's Customer, converting its source Lead first if
    the Quote was Lead-based and hasn't been converted yet. Idempotent:
    a Quote whose customer_id is already set (either created directly
    against a Customer, or already converted by an earlier call) returns
    that Customer without re-running conversion."""
    if quote.status != "ACCEPTED":
        raise CommercialSalesError("QUOTE_NOT_ACCEPTED")

    if quote.customer_id is not None:
        customer = db_session.get(Customer, quote.customer_id)
        if customer is None:
            raise CommercialSalesError("CUSTOMER_REQUIRED")
        return customer

    lead = db_session.get(Lead, quote.lead_id)
    if lead is None:
        raise CommercialSalesError("CUSTOMER_REQUIRED")

    try:
        customer = convert(lead, actor_staff_user_id=actor_staff_user_id, idempotency_key=idempotency_key)
    except InvalidLeadStateError as exc:
        raise CommercialSalesError("QUOTE_NOT_ACCEPTED") from exc
    except DuplicateCustomerError as exc:
        # Real duplicate-Customer candidates exist for this Lead --
        # surfaced as-is (not swallowed into a generic code) so the route
        # layer (Milestone 19) can present the same duplicate-review flow
        # Phase 9.5C's own Lead conversion UI already uses -- never
        # silently picks one, never leaks another employee's Customer
        # details through this path (Phase 9.5C's existing privacy rule,
        # unchanged, reused here).
        raise

    quote.customer_id = customer.id
    quote.version += 1
    db_session.commit()
    return customer
