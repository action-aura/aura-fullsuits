"""Phase 9.5D Milestone 5 -- Quote domain service.

Built directly on the existing Phase 9.5A Quote/QuoteLine schema
(app/models/commercial_sales.py) -- no new Quote model. Every line
mutation recomputes the parent Quote's subtotal/discount_total/total via
app/commercial_sales/calculator.py -- never a client-supplied total.
Every catalog reference is resolved through
app/commercial_sales/catalog_for_sales.py at the moment of line creation
(price-snapshot immutability). See
docs/owner/phase9_5d/quote-domain-contract.md,
quote-versioning-contract.md, quote-numbering-contract.md.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.commercial_sales.calculator import (
    DocumentResult,
    LineInput,
    calculate_document,
    calculate_line,
    validate_currency,
)
from app.commercial_sales.approvals import (
    compute_line_commercial_fingerprint,
    create_approval_request,
    unresolved_approvals_for_targets,
)
from app.commercial_sales.catalog_for_sales import describe_addon_for_sale, describe_plan_for_sale, requires_line_approval
from app.commercial_sales.errors import QUOTE_TRANSITIONS, CommercialSalesError
from app.commercial_sales.numbering import allocate_document_number
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_sales import Quote, QuoteLine

DEFAULT_QUOTE_VALIDITY_DAYS = 30


def _resolve_catalog_line(*, plan_id: uuid.UUID | None, addon_id: uuid.UUID | None) -> dict:
    if (plan_id is None) == (addon_id is None):
        raise CommercialSalesError("CATALOG_ITEM_INACTIVE")
    catalog_item = describe_plan_for_sale(plan_id) if plan_id else describe_addon_for_sale(addon_id)
    if catalog_item is None:
        raise CommercialSalesError("CATALOG_ITEM_INACTIVE")
    return catalog_item


def _recompute_quote_totals(quote: Quote) -> DocumentResult:
    lines = db_session.execute(
        select(QuoteLine).where(QuoteLine.quote_id == quote.id).order_by(QuoteLine.sort_order)
    ).scalars().all()
    if not lines:
        quote.subtotal = Decimal("0.00")
        quote.discount_total = Decimal("0.00")
        quote.total = Decimal("0.00")
        return None

    line_inputs = [
        LineInput(
            quantity=l.quantity,
            unit_price=l.unit_price,
            override_unit_price=l.overridden_unit_price,
            discount_amount=l.discount_amount or Decimal("0"),
            sort_order=l.sort_order,
        )
        for l in lines
    ]
    result = calculate_document(line_inputs)
    quote.subtotal = result.subtotal
    quote.discount_total = result.discount_total
    quote.total = result.total
    return result


def create_quote(fields: dict, *, actor_employee_profile_id: uuid.UUID, actor_staff_user_id: uuid.UUID) -> Quote:
    """A Quote may originate from a confirmed Customer (fields["customer_id"])
    or a qualified Lead (fields["lead_id"]) -- exactly one, per
    Non-Negotiable Rule 13 and the Quote model's own CHECK constraint. See
    docs/owner/phase9_5d/lead-quote-customer-boundary.md."""
    customer_id = fields.get("customer_id")
    lead_id = fields.get("lead_id")
    if (customer_id is None) == (lead_id is None):
        raise CommercialSalesError("CUSTOMER_REQUIRED")

    currency = validate_currency(fields.get("currency"))
    valid_until = fields.get("valid_until") or (utcnow().date() + timedelta(days=DEFAULT_QUOTE_VALIDITY_DAYS))

    quote = Quote(
        customer_id=customer_id,
        lead_id=lead_id,
        created_by_employee_profile_id=actor_employee_profile_id,
        status="DRAFT",
        quote_number=allocate_document_number("QUOTE"),
        currency=currency,
        subtotal=Decimal("0.00"),
        discount_total=Decimal("0.00"),
        total=Decimal("0.00"),
        valid_until=valid_until,
        notes=fields.get("notes"),
        version=1,
    )
    db_session.add(quote)
    db_session.flush()
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="QUOTE_CREATED",
        entity_type="quote",
        entity_public_id=str(quote.id),
        after_state={
            "quote_number": quote.quote_number,
            "customer_id": str(customer_id) if customer_id else None,
            "lead_id": str(lead_id) if lead_id else None,
            "currency": currency,
        },
    )
    return quote


def add_quote_line(
    quote: Quote,
    *,
    plan_id: uuid.UUID | None,
    addon_id: uuid.UUID | None,
    quantity: int,
    override_unit_price: Decimal | None = None,
    override_reason: str | None = None,
    discount_amount: Decimal = Decimal("0"),
    sort_order: int = 0,
    actor_staff_user_id: uuid.UUID,
) -> QuoteLine:
    if quote.status != "DRAFT":
        raise CommercialSalesError("INVALID_QUOTE_TRANSITION", from_status=quote.status, to_status=quote.status)

    catalog_item = _resolve_catalog_line(plan_id=plan_id, addon_id=addon_id)
    if catalog_item["currency"] != quote.currency:
        raise CommercialSalesError("CURRENCY_MISMATCH", given=catalog_item["currency"], expected=quote.currency)

    # This Quote flow never applies a document-level discount (only
    # per-line discount_amount is exposed) -- so a line's own line_net is
    # fully determined by its own inputs, independent of sibling lines.
    # Computed directly (not by matching sort_order against a full-document
    # recompute result, which would be ambiguous whenever two lines share
    # the same sort_order -- the default is 0 for every line unless the
    # caller sets distinct values).
    own_result = calculate_line(
        LineInput(
            quantity=quantity,
            unit_price=catalog_item["unit_price"],
            override_unit_price=override_unit_price,
            discount_amount=discount_amount,
            sort_order=sort_order,
        )
    )

    line = QuoteLine(
        quote_id=quote.id,
        plan_id=plan_id,
        addon_id=addon_id,
        price_version_id=uuid.UUID(catalog_item["price_version_id"]),
        description=catalog_item["name"],
        quantity=quantity,
        unit_price=catalog_item["unit_price"],
        overridden_unit_price=override_unit_price,
        override_reason=override_reason,
        discount_amount=discount_amount,
        line_total=own_result.line_net,
        sort_order=sort_order,
    )
    db_session.add(line)
    db_session.flush()

    _recompute_quote_totals(quote)
    quote.version += 1

    needs_approval = requires_line_approval(
        unit_price=catalog_item["unit_price"],
        override_unit_price=override_unit_price,
        discount_amount=discount_amount,
        line_gross=own_result.line_gross,
    )
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="QUOTE_LINE_ADDED",
        entity_type="quote",
        entity_public_id=str(quote.id),
        after_state={"line_id": str(line.id), "quantity": quantity},
    )

    if needs_approval:
        if override_unit_price is not None:
            reason_code = "PRICE_OVERRIDE" if override_unit_price != 0 else "ZERO_PRICE_LINE"
        else:
            reason_code = "DISCOUNT_ABOVE_LIMIT"
        fingerprint = compute_line_commercial_fingerprint(
            plan_id=plan_id,
            addon_id=addon_id,
            quantity=quantity,
            unit_price=catalog_item["unit_price"],
            overridden_unit_price=override_unit_price,
            discount_amount=discount_amount,
            currency=quote.currency,
        )
        create_approval_request(
            target_type="QUOTE_LINE",
            target_id=line.id,
            target_version_at_request=line.version,
            commercial_fingerprint=fingerprint,
            reason_code=reason_code,
            requested_values={
                "unit_price": str(catalog_item["unit_price"]),
                "override_unit_price": str(override_unit_price) if override_unit_price is not None else None,
                "discount_amount": str(discount_amount),
            },
            original_values={"unit_price": str(catalog_item["unit_price"])},
            requested_by_staff_user_id=actor_staff_user_id,
        )

    return line


def remove_quote_line(quote: Quote, line: QuoteLine, *, actor_staff_user_id: uuid.UUID) -> None:
    if quote.status != "DRAFT":
        raise CommercialSalesError("INVALID_QUOTE_TRANSITION", from_status=quote.status, to_status=quote.status)
    db_session.delete(line)
    db_session.flush()
    _recompute_quote_totals(quote)
    quote.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="QUOTE_LINE_REMOVED",
        entity_type="quote",
        entity_public_id=str(quote.id),
        after_state={"line_id": str(line.id)},
    )


def _check_transition(status: str, target: str) -> None:
    if target not in QUOTE_TRANSITIONS.get(status, set()):
        raise CommercialSalesError("INVALID_QUOTE_TRANSITION", from_status=status, to_status=target)


def _check_version(quote: Quote, expected_version: int | None) -> None:
    if expected_version is not None and quote.version != expected_version:
        raise CommercialSalesError("STALE_VERSION")


def submit_quote(quote: Quote, *, actor_staff_user_id: uuid.UUID, expected_version: int | None = None) -> Quote:
    _check_version(quote, expected_version)
    _check_transition(quote.status, "SENT")
    if not db_session.execute(select(QuoteLine).where(QuoteLine.quote_id == quote.id)).scalars().first():
        raise CommercialSalesError("EMPTY_DOCUMENT")

    quote.status = "SENT"
    quote.sent_at = utcnow()
    quote.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="QUOTE_SUBMITTED",
        entity_type="quote",
        entity_public_id=str(quote.id),
        before_state={"status": "DRAFT"},
        after_state={"status": "SENT"},
    )
    return quote


def cancel_quote(quote: Quote, *, reason: str, actor_staff_user_id: uuid.UUID, expected_version: int | None = None) -> Quote:
    _check_version(quote, expected_version)
    _check_transition(quote.status, "CANCELLED")
    if not reason or not reason.strip():
        raise CommercialSalesError("REASON_REQUIRED")

    before_status = quote.status
    quote.status = "CANCELLED"
    quote.cancelled_at = utcnow()
    quote.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="QUOTE_CANCELLED",
        entity_type="quote",
        entity_public_id=str(quote.id),
        reason=reason,
        before_state={"status": before_status},
        after_state={"status": "CANCELLED"},
    )
    return quote


def record_customer_decision(
    quote: Quote, *, accepted: bool, reason: str | None = None, actor_staff_user_id: uuid.UUID, expected_version: int | None = None
) -> Quote:
    _check_version(quote, expected_version)
    target = "ACCEPTED" if accepted else "REJECTED"
    _check_transition(quote.status, target)
    if not accepted and (not reason or not reason.strip()):
        raise CommercialSalesError("REASON_REQUIRED")

    if quote.valid_until and quote.valid_until < utcnow().date():
        raise CommercialSalesError("PRICE_VERSION_EXPIRED")

    if accepted:
        line_ids = [
            l.id for l in db_session.execute(select(QuoteLine).where(QuoteLine.quote_id == quote.id)).scalars().all()
        ]
        if unresolved_approvals_for_targets("QUOTE_LINE", line_ids):
            raise CommercialSalesError("APPROVAL_REQUIRED")

    quote.status = target
    quote.version += 1
    action_code = "QUOTE_ACCEPTED"
    if accepted:
        quote.accepted_at = utcnow()
    else:
        quote.rejected_at = utcnow()
        action_code = "QUOTE_REJECTED_BY_CUSTOMER"
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code=action_code,
        entity_type="quote",
        entity_public_id=str(quote.id),
        reason=reason,
        after_state={"status": target},
    )
    return quote


def expire_stale_quotes(*, as_of: date | None = None) -> int:
    """Server-evaluated expiry -- only still-SENT quotes past valid_until;
    an ACCEPTED quote can never silently expire (Non-Negotiable: 'an
    ACCEPTED Quote cannot silently expire')."""
    as_of = as_of or utcnow().date()
    stale = db_session.execute(
        select(Quote).where(Quote.status == "SENT", Quote.valid_until.is_not(None), Quote.valid_until < as_of)
    ).scalars().all()
    for quote in stale:
        quote.status = "EXPIRED"
        quote.version += 1
        audit_record(
            actor_staff_user_id=None,
            actor_role_snapshot=None,
            action_code="QUOTE_EXPIRED",
            entity_type="quote",
            entity_public_id=str(quote.id),
            before_state={"status": "SENT"},
            after_state={"status": "EXPIRED"},
        )
    if stale:
        db_session.commit()
    return len(stale)
