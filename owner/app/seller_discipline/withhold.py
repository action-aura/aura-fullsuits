"""Clause 16, sanction 3: "withhold the commission of the deal the violation is about".

THE FLAG IS THE CASE. A COMMISSION_WITHHOLD sanction case that is DECIDED_APPLIED withholds every
commission ledger row of its deal (sales order). Nothing is added to the ledger, so a row can never
disagree with the case; dropping or never applying the case means the row was never held.

WHAT "WITHHELD" DOES. The row stays on the ledger and stays EARNED, but it can neither be approved
nor paid out (commissions/ledger.py asks `case_withholding_entry`), and the seller's monthly
statement shows it as "withheld (case N)" instead of payable (commissions/statements.py). Take-back
rows (negative amounts) are never held: a refund must still reduce what the seller is owed.

Models only, no Flask, so the ledger can import it without a cycle.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.extensions import db_session
from app.models.commercial_sales import CommercialInvoice
from app.models.commissions import CommissionLadderSale, CommissionLedgerEntry
from app.models.seller_discipline import SanctionCase
from app.models.subscriptions import PaymentRecord, Subscription


def case_for_order(order_id) -> SanctionCase | None:
    """The applied withhold case on this deal, or None."""
    if order_id is None:
        return None
    return db_session.execute(
        select(SanctionCase).where(
            SanctionCase.kind == "COMMISSION_WITHHOLD", SanctionCase.status == "DECIDED_APPLIED",
            SanctionCase.withheld_sales_order_id == order_id,
        ).order_by(SanctionCase.case_number)
    ).scalars().first()


def order_id_for_entry(entry: CommissionLedgerEntry) -> uuid.UUID | None:
    """The deal (sales order) a ledger row belongs to: its ladder sale's order, else its invoice's
    order, else -- for a renewal row -- the order its payment's subscription was sold on."""
    if entry.ladder_sale_id is not None:
        sale = db_session.get(CommissionLadderSale, entry.ladder_sale_id)
        if sale is not None:
            return sale.sales_order_id
    if entry.source_commercial_invoice_id is not None:
        invoice = db_session.get(CommercialInvoice, entry.source_commercial_invoice_id)
        if invoice is not None and invoice.sales_order_id is not None:
            return invoice.sales_order_id
    if entry.source_payment_record_id is not None:
        payment = db_session.get(PaymentRecord, entry.source_payment_record_id)
        if payment is not None and payment.subscription_id is not None:
            subscription = db_session.get(Subscription, payment.subscription_id)
            if subscription is not None:
                return subscription.sales_order_id
    return None


def withheld_orders() -> dict:
    """{sales order id: case number} for every deal under an applied withhold case -- ONE query, for the
    statement, which asks about every row of a month and must not ask the database once per row."""
    rows = db_session.execute(
        select(SanctionCase.withheld_sales_order_id, SanctionCase.case_number).where(
            SanctionCase.kind == "COMMISSION_WITHHOLD", SanctionCase.status == "DECIDED_APPLIED")
    ).all()
    return {order_id: number for order_id, number in rows}


def case_number_for_entry(entry: CommissionLedgerEntry, held: dict) -> int | None:
    """The same answer as case_withholding_entry, from the dict withheld_orders() returned."""
    if not held or entry.commission_amount is None or entry.commission_amount <= 0:
        return None
    return held.get(order_id_for_entry(entry))


def case_withholding_entry(entry: CommissionLedgerEntry) -> SanctionCase | None:
    """The case that holds this ledger row back, or None. Only money owed (a positive row) is held."""
    if entry.commission_amount is None or entry.commission_amount <= 0:
        return None
    return case_for_order(order_id_for_entry(entry))
