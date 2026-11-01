"""The 'Consents' card on the customer page (staff side): what the customer answered at
the acceptance step, and the "ID verification by hand needed" flag a refusal raises.
Pure reads; the one action (mark as verified) lives in customers/routes.py."""
from __future__ import annotations

from sqlalchemy import select

from app.extensions import db_session
from app.models.contracts import ContractConsent


def panel_context(customer, codes: set) -> dict | None:
    """None = nothing to show (no flag and no consent was ever recorded)."""
    consents = list(db_session.execute(
        select(ContractConsent).where(ContractConsent.customer_id == customer.id)
        .order_by(ContractConsent.given_at.desc(), ContractConsent.kind)
    ).scalars())
    flagged_at = customer.manual_id_verification_required_at
    if not consents and flagged_at is None:
        return None
    latest: dict = {}
    for consent in consents:  # newest first: the first row of a kind is the standing answer
        latest.setdefault(consent.kind, consent)
    return {
        "flagged_at": flagged_at,
        "latest": latest,
        "history": consents,
        "can_mark_verified": "customer_documents.view" in codes,
    }
