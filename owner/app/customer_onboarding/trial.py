"""The 7-day trial: when it starts, when it ends, who may stretch it.

THE RULES (owner, 2026-10-30/31)
  - A new customer gets a trial: a Subscription in status PILOT plus a PilotRecord
    of kind TRIAL (created by customer_onboarding.service; no dates yet).
  - The clock starts at the FIRST DEVICE ACTIVATION, not when the seller made the
    customer: a shop that signs up on Thursday and installs the app on Saturday
    still gets its full week. start_trial_on_activation() is called from
    licensing_service/activation.py, inside the activation transaction, under the
    licence row lock, so two devices activating at the same moment start the clock
    once.
  - If nobody ever signs in, the clock starts anyway 14 days after the customer was
    created (start_overdue_trials, run by `flask commercial start-overdue-trials`),
    so an abandoned account cannot sit "in trial" for ever.
  - The trial ends at the END of Amman day (start + 7): signed up on the 1st, the
    shop sells through the whole of the 8th (the product's trial_cutoff(term_end)
    is the first instant AFTER term_end's Amman day). Subscription.end_date is the
    term_end the lease signs; the product does the locking, Owner only has to put
    the right date in the lease and never move a trial to EXPIRED by itself.
  - One trial per physical device, ever: the device-key fingerprint is recorded in
    owner_trial_device_marks (UNIQUE); a second CUSTOMER's trial cannot start on the
    same hardware (TRIAL_DEVICE_ALREADY_USED). The same customer's other devices,
    or the same device reinstalled, are fine.
  - Old builds cannot lock a trial, so they are refused: an app older than
    MIN_TRIAL_APP_VERSION (the first build with the lock) gets TRIAL_REQUIRES_UPDATE
    instead of a free, never-ending trial.
  - A Sales Manager or Super Admin may extend a started trial ONCE, by at most 7 days,
    with a written reason (extend_trial). A seller may not.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.commercial_ops import PilotExtension, PilotRecord
from app.models.customer_onboarding import CustomerDocument, TrialDeviceMark
from app.models.licensing import License
from app.models.subscriptions import Subscription
from app.operational_reports.periods import jordan_today
from app.releases import versioning

TRIAL_LENGTH_DAYS = 7
FALLBACK_START_AFTER_DAYS = 14
MAX_TRIAL_EXTENSION_DAYS = 7
MAX_TRIAL_EXTENSIONS = 1
# The first Retail build that locks an ended trial (feat/trial-lock). Anything
# older would treat a PILOT lease as "keep selling" for ever.
MIN_TRIAL_APP_VERSION = "1.0.0-rc.17"
MIN_EXTEND_REASON_LENGTH = 10

_DEAD_PILOT_STATUSES = ("CONVERTED", "COMPLETED", "CANCELLED")


class TrialError(ValueError):
    """Stable code in .code (the activation endpoint puts it in the response as
    the reason code; staff screens word it)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def trial_end_for(start: date) -> date:
    return start + timedelta(days=TRIAL_LENGTH_DAYS)


def trial_pilot_for_subscription(subscription_id) -> PilotRecord | None:
    return db_session.execute(
        select(PilotRecord).where(PilotRecord.subscription_id == subscription_id, PilotRecord.pilot_kind == "TRIAL")
    ).scalars().first()


def trial_pilot_for_customer(customer_id) -> PilotRecord | None:
    return db_session.execute(
        select(PilotRecord).where(PilotRecord.customer_id == customer_id, PilotRecord.pilot_kind == "TRIAL")
    ).scalars().first()


def app_version_can_run_trial(app_version: str | None) -> bool:
    try:
        return versioning.compare_semver(app_version or "", MIN_TRIAL_APP_VERSION) >= 0
    except ValueError:
        return False  # cannot prove it is new enough -> not allowed (fail closed)


def _apply_dates(pilot: PilotRecord, start: date, *, started_at: datetime) -> date:
    end = trial_end_for(start)
    subscription = db_session.get(Subscription, pilot.subscription_id)
    subscription.start_date = start
    subscription.end_date = end
    for license_row in db_session.execute(select(License).where(License.subscription_id == subscription.id)).scalars():
        license_row.valid_from = start
        license_row.valid_until = end
    pilot.pilot_start = start
    pilot.pilot_end = end
    pilot.trial_started_at = started_at
    pilot.trial_length_days = TRIAL_LENGTH_DAYS
    # An unconverted trial's ID / registration files go 90 days after it ends
    # (documents.py); now that the end is known the provisional date is replaced.
    purge_after = datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=90)
    for document in db_session.execute(
        select(CustomerDocument).where(
            CustomerDocument.customer_id == pilot.customer_id,
            CustomerDocument.kind.in_(("NATIONAL_ID", "COMMERCIAL_REGISTRATION")),
            CustomerDocument.purge_after.is_not(None), CustomerDocument.purged_at.is_(None),
        )
    ).scalars():
        document.purge_after = purge_after
    return end


def _mark_device(*, fingerprint: str, customer_id, registry_device_id, installation_id) -> None:
    """Records the device and refuses a second customer's trial on it. Two
    signals, either one is enough: the device-KEY fingerprint (UNIQUE: two
    customers' activations racing on one key cannot both insert, and the loser is
    told TRIAL_DEVICE_ALREADY_USED rather than getting a 500) and the registry
    device id the app claims for this computer (it survives a reinstall, which
    makes a new key; the app is ours, so it is a deterrent, not a proof)."""
    registry_device_id = (registry_device_id or "").strip()[:64] or None
    clash = select(TrialDeviceMark).where(TrialDeviceMark.customer_id != customer_id)
    if registry_device_id is not None:
        clash = clash.where(or_(TrialDeviceMark.device_key_fingerprint == fingerprint,
                                TrialDeviceMark.registry_device_id == registry_device_id))
    else:
        clash = clash.where(TrialDeviceMark.device_key_fingerprint == fingerprint)
    if db_session.execute(clash).scalars().first() is not None:
        raise TrialError("TRIAL_DEVICE_ALREADY_USED")
    existing = db_session.execute(
        select(TrialDeviceMark).where(TrialDeviceMark.device_key_fingerprint == fingerprint)
    ).scalars().first()
    if existing is not None:
        return
    try:
        with db_session.begin_nested():
            db_session.add(TrialDeviceMark(
                device_key_fingerprint=fingerprint, customer_id=customer_id,
                registry_device_id=registry_device_id, installation_id=installation_id,
            ))
            db_session.flush()
    except IntegrityError:
        winner = db_session.execute(
            select(TrialDeviceMark).where(TrialDeviceMark.device_key_fingerprint == fingerprint)
        ).scalars().first()
        if winner is None or winner.customer_id != customer_id:
            raise TrialError("TRIAL_DEVICE_ALREADY_USED")


def start_trial_on_activation(
    license_row: License, *, device_fingerprint: str, registry_device_id: str | None, installation_id,
    app_version: str | None, today: date | None = None,
) -> bool:
    """Called by activation under the locked licence row, after the device is
    accepted and before the lease is signed. Does nothing for a licence that is
    not a live trial. Raises TrialError (TRIAL_REQUIRES_UPDATE /
    TRIAL_DEVICE_ALREADY_USED); the caller turns it into an activation refusal
    and the whole transaction is rolled back. Returns True when this call
    started the clock."""
    subscription = license_row.subscription
    if subscription.status != "PILOT":
        return False
    pilot = trial_pilot_for_subscription(subscription.id)
    if pilot is None or pilot.status in _DEAD_PILOT_STATUSES:
        return False
    if not app_version_can_run_trial(app_version):
        raise TrialError("TRIAL_REQUIRES_UPDATE")
    _mark_device(fingerprint=device_fingerprint, customer_id=license_row.customer_id,
                 registry_device_id=registry_device_id, installation_id=installation_id)
    if pilot.trial_started_at is not None:
        return False
    today = today or jordan_today()
    _apply_dates(pilot, today, started_at=datetime.now(timezone.utc))
    return True


def start_overdue_trials(*, today: date | None = None) -> int:
    """The fallback: a trial nobody ever signed in to starts FALLBACK_START_AFTER_DAYS
    after the customer was created (so it also ends, instead of waiting for
    ever). Idempotent. Returns how many were started."""
    today = today or jordan_today()
    started = 0
    candidates = db_session.execute(
        select(PilotRecord).where(
            PilotRecord.pilot_kind == "TRIAL", PilotRecord.trial_started_at.is_(None),
            PilotRecord.status.notin_(_DEAD_PILOT_STATUSES),
        )
    ).scalars().all()
    for pilot in candidates:
        created_day = pilot.created_at.astimezone(timezone.utc).date()
        start = created_day + timedelta(days=FALLBACK_START_AFTER_DAYS)
        if start > today:
            continue
        end = _apply_dates(pilot, start, started_at=datetime.now(timezone.utc))
        db_session.commit()
        audit_record(
            actor_staff_user_id=None, actor_role_snapshot=None, action_code="TRIAL_STARTED_BY_FALLBACK",
            entity_type="pilot_record", entity_public_id=str(pilot.id),
            after_state={"start": start.isoformat(), "end": end.isoformat()},
        )
        started += 1
    return started


def extend_trial(pilot: PilotRecord, *, days: int, reason: str, actor_staff_user_id) -> PilotExtension:
    """One extension of at most 7 days, with a written reason. The caller has
    checked the trials.extend permission. Moves the lease's term_end (the
    subscription's end date) and the licence's valid-until with it."""
    from app.commercial_ops.pilot_lifecycle import extend_pilot

    reason = (reason or "").strip()
    if len(reason) < MIN_EXTEND_REASON_LENGTH:
        raise TrialError("TRIAL_EXTEND_REASON_REQUIRED")
    if not isinstance(days, int) or days < 1 or days > MAX_TRIAL_EXTENSION_DAYS:
        raise TrialError("TRIAL_EXTEND_DAYS_INVALID")
    if pilot.pilot_kind != "TRIAL":
        raise TrialError("NOT_A_TRIAL")
    if pilot.trial_started_at is None:
        raise TrialError("TRIAL_NOT_STARTED")
    if pilot.status in _DEAD_PILOT_STATUSES:
        raise TrialError("TRIAL_NOT_ACTIVE")
    if pilot.extension_count >= MAX_TRIAL_EXTENSIONS:
        raise TrialError("TRIAL_ALREADY_EXTENDED")
    new_end = pilot.pilot_end + timedelta(days=days)
    extension = extend_pilot(pilot, new_end_date=new_end, reason=reason, actor_staff_user_id=actor_staff_user_id)
    subscription = db_session.get(Subscription, pilot.subscription_id)
    subscription.end_date = new_end
    for license_row in db_session.execute(select(License).where(License.subscription_id == subscription.id)).scalars():
        license_row.valid_until = new_end
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="TRIAL_EXTENDED",
        entity_type="pilot_record", entity_public_id=str(pilot.id), reason=reason,
        after_state={"days": days, "new_end": new_end.isoformat()},
    )
    return extension


def convert_trial_pilot(pilot: PilotRecord, *, order_id, actor_staff_user_id) -> None:
    """The trial was paid for: PilotRecord ACTIVE/EXTENDED -> CONVERTED, through the
    pilot lifecycle's own transition (history row + audit). Called by fulfillment
    only (commercial_sales/fulfillment.py::_convert_trial_in_place)."""
    from app.commercial_ops.pilot_lifecycle import transition_pilot

    transition_pilot(pilot, "CONVERTED", actor_staff_user_id, reason=f"Paid by sales order {order_id}.")
    pilot.conversion_decision = "CONVERT"
    db_session.commit()


def is_unconverted_trial(subscription_id) -> bool:
    """True while this subscription is a trial that has not been paid for. Used
    to forbid moving it to EXPIRED (subscriptions.services.transition_subscription)."""
    pilot = trial_pilot_for_subscription(subscription_id)
    return pilot is not None and pilot.status != "CONVERTED"
