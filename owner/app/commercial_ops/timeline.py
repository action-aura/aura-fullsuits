"""Combined subscription/license commercial timeline (Phase 8V, Part L).

Read-only aggregation over history tables that already exist -- never a
new source of truth, never a new mutation path. Every event is joined to
the subscription by an explicit foreign key (`Subscription.id` ==
`License.subscription_id` == `Installation.subscription_id` ==
`RenewalRequest.subscription_id` == `PilotRecord.subscription_id` ==
`InternalNotification.subscription_id`), never by timestamp-only
inference, per the governing brief's own instruction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from app.extensions import db_session
from app.models.commercial_ops import InternalNotification, PilotRecord, PilotStatusHistory, RenewalRequest, RenewalRequestStatusHistory
from app.models.installations import Installation, InstallationStatusHistory
from app.models.licensing import License, LicenseStatusHistory
from app.models.subscriptions import SubscriptionStatusHistory


@dataclass
class TimelineEvent:
    timestamp: datetime
    category: str
    description: str
    entity_type: str
    entity_id: str
    reason: str | None = None


def build_subscription_timeline(subscription_id) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []

    for h in db_session.execute(
        select(SubscriptionStatusHistory).where(SubscriptionStatusHistory.subscription_id == subscription_id)
    ).scalars().all():
        events.append(TimelineEvent(h.created_at, "SUBSCRIPTION", f"Subscription {h.from_status or 'created'} -> {h.to_status}", "subscription", str(h.subscription_id), h.reason))

    license_ids = [row[0] for row in db_session.execute(select(License.id).where(License.subscription_id == subscription_id)).all()]
    if license_ids:
        for h in db_session.execute(select(LicenseStatusHistory).where(LicenseStatusHistory.license_id.in_(license_ids))).scalars().all():
            events.append(TimelineEvent(h.created_at, "LICENSE", f"License {h.from_status or 'created'} -> {h.to_status}", "license", str(h.license_id), h.reason))

    installation_ids = [row[0] for row in db_session.execute(select(Installation.id).where(Installation.subscription_id == subscription_id)).all()]
    if installation_ids:
        for h in db_session.execute(select(InstallationStatusHistory).where(InstallationStatusHistory.installation_id.in_(installation_ids))).scalars().all():
            events.append(TimelineEvent(h.created_at, "INSTALLATION", f"Installation {h.from_status or 'registered'} -> {h.to_status}", "installation", str(h.installation_id), h.reason))

    renewal_ids = [row[0] for row in db_session.execute(select(RenewalRequest.id).where(RenewalRequest.subscription_id == subscription_id)).all()]
    if renewal_ids:
        for h in db_session.execute(select(RenewalRequestStatusHistory).where(RenewalRequestStatusHistory.renewal_request_id.in_(renewal_ids))).scalars().all():
            events.append(TimelineEvent(h.created_at, "RENEWAL", f"Renewal {h.from_status or 'created'} -> {h.to_status}", "renewal_request", str(h.renewal_request_id), h.reason))

    pilot_ids = [row[0] for row in db_session.execute(select(PilotRecord.id).where(PilotRecord.subscription_id == subscription_id)).all()]
    if pilot_ids:
        for h in db_session.execute(select(PilotStatusHistory).where(PilotStatusHistory.pilot_record_id.in_(pilot_ids))).scalars().all():
            events.append(TimelineEvent(h.created_at, "PILOT", f"Pilot {h.from_status or 'created'} -> {h.to_status}", "pilot_record", str(h.pilot_record_id), h.reason))

    for n in db_session.execute(select(InternalNotification).where(InternalNotification.subscription_id == subscription_id)).scalars().all():
        events.append(TimelineEvent(n.created_at, "NOTIFICATION", n.title, "internal_notification", str(n.id), None))

    events.sort(key=lambda e: e.timestamp)
    return events
