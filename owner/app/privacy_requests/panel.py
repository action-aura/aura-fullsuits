"""The 'Personal data requests' card on the customer page (staff side). Pure reads; the actions
live in routes.py. None = the actor does not hold privacy.requests (the card is not drawn)."""
from __future__ import annotations

from app.models.data_requests import REQUEST_CHANNELS, REQUEST_KINDS
from app.operational_reports.periods import jordan_today
from app.privacy_requests import erase, service


def panel_context(customer, codes: set) -> dict | None:
    if "privacy.requests" not in codes:
        return None
    rows = service.requests_for_customer(customer.id)
    return {
        "requests": rows,
        "kinds": REQUEST_KINDS,
        "channels": REQUEST_CHANNELS,
        "today": jordan_today(),
        "overdue": {r.id for r in rows if service.is_overdue(r)},
        "can_open_documents": "customer_documents.view" in codes,
        # shown beside an ERASE request so the manager sees why the button will be refused
        "blocking": sorted({s.status for s in erase.blocking_subscriptions(customer.id)}),
    }
