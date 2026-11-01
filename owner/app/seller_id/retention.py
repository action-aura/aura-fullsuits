"""Retention of a seller's own ID photo: kept while they work for us, deleted 365 days after
they are disabled or terminated (legal review v2, (d)-4; same year the customer's photos get).

Same shape as app/customer_onboarding/retention.py: a pure function of the staff member's
current state, evaluated every night (`flask documents purge-due`), so re-enabling a seller
clears the date again and nothing depends on a hook having fired. Every photo of that seller
counts -- the superseded ones too (a replaced photo is still an identity document).

A photo held by a not-yet-accepted invitation (staff_user_id still NULL) is not covered: it
belongs to nobody yet and is bound to the staff user at acceptance.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.customer_onboarding import documents as kyc
from app.customer_onboarding.documents import POST_CUSTOMER_RETENTION_DAYS
from app.extensions import db_session
from app.models.employees import EmployeeProfile
from app.models.staff import StaffUser
from app.models.staff_identity import StaffIdentityDocument

log = logging.getLogger(__name__)
_ENDED_EMPLOYMENT = ("TERMINATED", "ARCHIVED")


def _ended_at(staff: StaffUser) -> datetime | None:
    """When this person stopped working for us, or None while they still do."""
    moments = []
    if not staff.is_active and staff.disabled_at is not None:
        moments.append(staff.disabled_at)
    profile = db_session.execute(select(EmployeeProfile).where(EmployeeProfile.staff_user_id == staff.id)).scalars().first()
    if profile is not None and profile.employment_status in _ENDED_EMPLOYMENT and profile.employment_end_date is not None:
        moments.append(datetime.combine(profile.employment_end_date, datetime.min.time(), tzinfo=timezone.utc))
    return min(moments) if moments else None


def apply_seller_photo_retention(*, now: datetime | None = None) -> dict:
    """Sets purge_after = ended + 365 days on every unpurged photo of a seller who has left,
    clears it for one who is back. Returns {"set": n, "cleared": n} in sellers."""
    rows = db_session.execute(
        select(StaffIdentityDocument).where(StaffIdentityDocument.staff_user_id.is_not(None), StaffIdentityDocument.purged_at.is_(None))
    ).scalars().all()
    by_staff: dict = {}
    for row in rows:
        by_staff.setdefault(row.staff_user_id, []).append(row)
    changed = {"set": 0, "cleared": 0}
    audits = []
    for staff_id, photos in by_staff.items():
        staff = db_session.get(StaffUser, staff_id)
        ended = _ended_at(staff) if staff is not None else None
        target = ended + timedelta(days=POST_CUSTOMER_RETENTION_DAYS) if ended is not None else None
        moved = [p for p in photos if p.purge_after != target]
        for photo in moved:
            photo.purge_after = target
        if moved:
            changed["cleared" if target is None else "set"] += 1
            audits.append((staff_id, target, len(moved)))
    db_session.commit()
    for staff_id, target, n in audits:
        audit_record(
            actor_staff_user_id=None, actor_role_snapshot=None,
            action_code="STAFF_ID_PHOTO_RETENTION_CLEARED" if target is None else "STAFF_ID_PHOTO_RETENTION_SET",
            entity_type="staff_user", entity_public_id=str(staff_id),
            after_state={"photos": n, "purge_after": target.isoformat() if target else None},
        )
    return changed


def purge_expired_seller_photos(*, now: datetime | None = None) -> int:
    """Deletes the encrypted file and stamps purged_at on every photo whose date has passed.
    Idempotent (a purged row is skipped); the row stays as the record that a photo existed."""
    now = now or datetime.now(timezone.utc)
    due = db_session.execute(
        select(StaffIdentityDocument).where(
            StaffIdentityDocument.purge_after.is_not(None), StaffIdentityDocument.purge_after <= now,
            StaffIdentityDocument.purged_at.is_(None),
        )
    ).scalars().all()
    gone = []
    for photo in due:
        try:
            import os

            os.remove(kyc.path_for_key(photo.storage_key))
        except FileNotFoundError:
            pass
        except (OSError, kyc.DocumentError):
            log.error("seller ID photo %s is due for deletion but its file could not be removed", photo.id)
            continue  # leave the row unpurged: the next run tries again, nothing claims it is gone
        photo.purged_at = now
        gone.append(photo)
    db_session.commit()
    for photo in gone:
        audit_record(
            actor_staff_user_id=None, actor_role_snapshot=None, action_code="STAFF_ID_PHOTO_PURGED",
            entity_type="staff_identity_document", entity_public_id=str(photo.id),
            after_state={"staff_user_id": str(photo.staff_user_id)},
        )
    return len(gone)
