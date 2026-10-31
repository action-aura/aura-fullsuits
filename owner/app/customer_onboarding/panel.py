"""The 'Trial' card on the customer page (staff side): state of the trial, the
manager's extend button, and the list of the customer's ID / registration photos
for those who may open them. Pure reads; the actions live in routes.py."""
from __future__ import annotations

from datetime import timedelta

from flask import current_app

from app.customer_onboarding import documents, trial
from app.extensions import db_session
from app.models.subscriptions import Subscription
from app.operational_reports.periods import jordan_today


def trial_days_left(pilot, today=None) -> int:
    """Whole days left, counting the last day itself (the product's display rule:
    ends on the 8th -> on the 8th it says 1 day left); 0 once over; None before it starts."""
    if pilot.trial_started_at is None:
        return None
    today = today or jordan_today()
    return max((pilot.pilot_end - today).days + 1, 0)


def panel_context(customer, codes: set) -> dict | None:
    """None = feature off, or this customer has no trial."""
    if not current_app.config.get("CUSTOMER_ONBOARDING_ENABLED"):
        return None
    pilot = trial.trial_pilot_for_customer(customer.id)
    if pilot is None:
        return None
    started = pilot.trial_started_at is not None
    converted = pilot.status == "CONVERTED"
    paid_until = None
    if converted:
        # The paid term, not the trial's end: after payment the subscription carries the real date.
        paid_until = db_session.get(Subscription, pilot.subscription_id).end_date
    return {
        "paid_until": paid_until,
        "pilot": pilot,
        "started": started,
        "converted": converted,
        "days_left": trial_days_left(pilot),
        "fallback_start": None if started else pilot.created_at.date() + timedelta(days=trial.FALLBACK_START_AFTER_DAYS),
        "can_convert": "orders.create" in codes and "payments.create" in codes and pilot.status in ("ACTIVE", "EXTENDED"),
        "can_extend": "trials.extend" in codes and started and pilot.status in ("ACTIVE", "EXTENDED")
        and pilot.extension_count < trial.MAX_TRIAL_EXTENSIONS,
        "documents": documents.documents_of(customer.id) if "customer_documents.view" in codes else [],
    }
