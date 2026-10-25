"""The push sender: outbox rows -> one delivery per device -> the push service.

One pass (run_once), used by three callers that all go through the same
claims, so any two of them running at once never send one sale to one device
twice:
  * the background thread (start_worker_if_configured -- the same shape as
    app/mail/worker.py: a daemon threading.Timer in each web process, only
    when push is configured and never in a test app) -- the fast path, a
    sale reaches the phones within about TICK_SECONDS;
  * `flask notifications send-pending` (app/push/cli.py), run every minute by
    the aura-owner-push systemd timer on the live server -- the backstop when
    no web process is up, and the command an operator runs by hand;
  * the tests.

A pass has two steps.

1. EXPAND each PENDING outbox row: resolve who may confirm the sale NOW
   (app/push/recipients.py) and insert one QUEUED delivery per device of each
   of them, ON CONFLICT DO NOTHING on (outbox row, device). The outbox row is
   locked (FOR UPDATE SKIP LOCKED) while that happens, then marked EXPANDED.
   A sale already confirmed/rejected, or a row older than MAX_AGE, is
   SKIPPED instead: nobody is woken up for a sale that is not waiting.

2. SEND, device by device. A device is taken with a lease on its
   subscription row (UPDATE ... WHERE the lease is free), so one device is
   handled by one worker at a time. Every due delivery of that device is
   re-checked -- the person may still confirm sales, the sale is still
   waiting -- and the rest go out as ONE message.

The burst rule (the owner's "don't spam"): at most one message per device per
MIN_SECONDS_BETWEEN_PUSHES. A sale recorded inside that window is not sent
on its own; it waits for the window to end and goes out with whatever else
arrived, as one message that says how many sales are waiting. Every message
uses the same notification tag, so the phone REPLACES the one it is showing
instead of piling up a list, and the same Topic, so a phone that is offline
receives only the latest one when it comes back. Ten sales typed in two
minutes are then at most three buzzes per phone, the last one saying "10".

Answers from the push service:
  2xx              SENT
  404 / 410        the device unsubscribed or expired: its row is deleted
                   (and its queued deliveries finished FAILED_PERMANENT)
  429 / 5xx / no answer   retried with backoff (30 s * 2^n, capped at 1 h,
                   +/-20% jitter -- app/mail/worker.py's numbers), at most
                   MAX_ATTEMPTS times, honouring Retry-After
  any other 4xx    the push service refused this message for good:
                   FAILED_PERMANENT, the device is kept (e.g. 403 after a
                   key change; the page re-subscribes it with the new key)

Nothing here logs an endpoint, a key or a payload.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import uuid
from datetime import datetime, timedelta, timezone

from flask import Flask, current_app
from flask_babel import force_locale
from flask_babel import gettext as _
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.commercial_sales.direct_sale import count_sales_awaiting_for, sale_is_waiting
from app.extensions import db_session
from app.i18n import current_direction, is_supported_locale
from app.models.commercial_sales import SalesOrder
from app.models.employees import EmployeeProfile
from app.models.push import PushDelivery, PushOutbox, PushSubscription
from app.models.staff import StaffUser
from app.models.subscriptions import PaymentRecord
from app.push import subscriptions as push_subscriptions
from app.push import vapid, webpush
from app.push.outbox import EVENT_SALE_WAITING
from app.push.recipients import recipients_for_sale, staff_may_confirm_sales

log = logging.getLogger("aura.owner.push")

TICK_SECONDS = 10
MIN_SECONDS_BETWEEN_PUSHES = 60
#: A notification about a sale older than this is no longer news (and the
#: push service would drop it after TTL_SECONDS anyway).
MAX_AGE = timedelta(hours=24)
TTL_SECONDS = 24 * 3600
BACKOFF_BASE_SECONDS = 30
BACKOFF_CAP_SECONDS = 3600
MAX_ATTEMPTS = 8
DEVICE_LEASE_SECONDS = 60
NOTIFICATION_TAG = "aura-owner-sales-waiting"
PUSH_TOPIC = "sales-waiting"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def compute_backoff_delay(attempt_count: int) -> float:
    delay = min(BACKOFF_BASE_SECONDS * (2 ** attempt_count), BACKOFF_CAP_SECONDS)
    return delay * random.uniform(0.8, 1.2)


# ----------------------------------------------------------------- expand --

def expand_pending(*, batch_size: int = 50, now: datetime | None = None) -> dict:
    now = now or _now()
    rows = db_session.execute(
        select(PushOutbox)
        .where(PushOutbox.status == "PENDING")
        .order_by(PushOutbox.created_at, PushOutbox.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    ).scalars().all()
    outcome = {"expanded": 0, "skipped": 0, "deliveries": 0}
    for row in rows:
        reason = None
        if row.event_type != EVENT_SALE_WAITING:
            reason = "UNKNOWN_EVENT"
        elif now - row.created_at > MAX_AGE:
            reason = "TOO_OLD"
        elif not sale_is_waiting(row.sales_order_id):
            reason = "NO_LONGER_WAITING"
        if reason is not None:
            row.status, row.skip_reason, row.processed_at = "SKIPPED", reason, now
            outcome["skipped"] += 1
            continue
        order = db_session.get(SalesOrder, row.sales_order_id)
        payment = db_session.get(PaymentRecord, row.payment_record_id)
        for staff in recipients_for_sale(order, payment):
            device_ids = db_session.execute(
                select(PushSubscription.id).where(PushSubscription.staff_user_id == staff.id)
            ).scalars().all()
            for device_id in device_ids:
                inserted = db_session.execute(
                    pg_insert(PushDelivery)
                    .values(id=uuid.uuid4(), outbox_id=row.id, subscription_id=device_id, staff_user_id=staff.id,
                            status="QUEUED", attempt_count=0)
                    .on_conflict_do_nothing(constraint="uq_owner_push_deliveries_outbox_subscription")
                    # RETURNING, not rowcount: psycopg reports -1 for an
                    # INSERT .. ON CONFLICT (seen in the real-browser proof).
                    .returning(PushDelivery.id)
                )
                outcome["deliveries"] += len(inserted.all())
        row.status, row.processed_at = "EXPANDED", now
        outcome["expanded"] += 1
    db_session.commit()
    return outcome


# ------------------------------------------------------------------- send --

def _due_device_ids(now: datetime, batch_size: int) -> list:
    return db_session.execute(
        select(PushDelivery.subscription_id)
        .where(
            PushDelivery.status == "QUEUED",
            PushDelivery.subscription_id.is_not(None),
            (PushDelivery.next_attempt_at.is_(None)) | (PushDelivery.next_attempt_at <= now),
        )
        .group_by(PushDelivery.subscription_id)
        .order_by(PushDelivery.subscription_id)
        .limit(batch_size)
    ).scalars().all()


def _take_device(device_id, now: datetime) -> bool:
    """The per-device lease. Committed at once: it must hold across the slow
    HTTP call, which runs outside any transaction."""
    result = db_session.execute(
        update(PushSubscription)
        .where(
            PushSubscription.id == device_id,
            (PushSubscription.lease_expires_at.is_(None)) | (PushSubscription.lease_expires_at <= now),
        )
        .values(lease_expires_at=now + timedelta(seconds=DEVICE_LEASE_SECONDS))
    )
    db_session.commit()
    return result.rowcount == 1


def _finish(rows, status: str, *, error: str | None, now: datetime) -> None:
    for row in rows:
        row.status, row.last_error, row.finished_at = status, error, now
        if status == "SENT":
            row.sent_at = now


def _seller_first_name(order: SalesOrder) -> str:
    seller = db_session.get(EmployeeProfile, order.created_by_employee_profile_id)
    name = (seller.full_name if seller is not None else "") or ""
    parts = name.split()
    return parts[0] if parts else name


def _locale_for(staff: StaffUser, device: PushSubscription) -> str:
    for candidate in (staff.locale, device.locale):
        if is_supported_locale(candidate):
            return candidate
    return current_app.config["BABEL_DEFAULT_LOCALE"]


def _sales_list_path() -> str:
    # Built from the URL map, not url_for: the background thread has no
    # request (and Owner sets no SERVER_NAME).
    return current_app.url_map.bind("localhost").build("license_issuance.sales_awaiting_confirmation")


def build_payload(staff: StaffUser, device: PushSubscription, rows: list[PushDelivery]) -> dict:
    """What the phone shows. The owner's rule: no money and no customer on the
    lock screen -- only that a sale is waiting, the SELLER's first name (an
    employee) and how many are waiting for this person. Never an amount, a
    currency, a customer name, a reference or a licence."""
    events = [db_session.get(PushOutbox, row.outbox_id) for row in rows]
    newest = max(events, key=lambda event: (event.created_at, str(event.id)))
    order = db_session.get(SalesOrder, newest.sales_order_id)
    seller = _seller_first_name(order)
    waiting = count_sales_awaiting_for(staff.id)
    locale = _locale_for(staff, device)
    with force_locale(locale):
        title = _("New sale waiting for confirmation")
        if waiting > 1:
            body = _("From %(name)s. Sales waiting for you now: %(count)s", name=seller, count=waiting)
        else:
            body = _("From %(name)s", name=seller)
    path = _sales_list_path()
    if len(events) == 1:
        # Tapping opens the list at THIS sale; if somebody confirmed it in the
        # meantime the page says so (issuance_routes.sales_awaiting_confirmation).
        path = f"{path}?sale={order.id}#sale-{order.id}"
    return {"title": title, "body": body, "url": path, "tag": NOTIFICATION_TAG,
            "lang": locale, "dir": current_direction(locale)}


def _send_device(device_id, keys: vapid.VapidKeys, now: datetime) -> str:
    device = db_session.get(PushSubscription, device_id)
    if device is None:
        return "gone"
    rows = db_session.execute(
        select(PushDelivery)
        .where(
            PushDelivery.subscription_id == device_id,
            PushDelivery.status == "QUEUED",
            (PushDelivery.next_attempt_at.is_(None)) | (PushDelivery.next_attempt_at <= now),
        )
        .order_by(PushDelivery.created_at, PushDelivery.id)
    ).scalars().all()

    def release(outcome: str) -> str:
        device.lease_expires_at = None
        db_session.commit()
        return outcome

    if not rows:
        return release("nothing_due")
    staff = db_session.get(StaffUser, device.staff_user_id)
    if not staff_may_confirm_sales(staff):
        _finish(rows, "CANCELLED", error="RECIPIENT_NOT_ELIGIBLE", now=now)
        return release("cancelled")
    live = []
    for row in rows:
        event = db_session.get(PushOutbox, row.outbox_id)
        if now - event.created_at > MAX_AGE:
            _finish([row], "CANCELLED", error="TOO_OLD", now=now)
        elif not sale_is_waiting(event.sales_order_id):
            _finish([row], "CANCELLED", error="NO_LONGER_WAITING", now=now)
        else:
            live.append(row)
    if not live:
        return release("cancelled")
    if device.last_sent_at is not None and now - device.last_sent_at < timedelta(seconds=MIN_SECONDS_BETWEEN_PUSHES):
        # The burst rule: wait for this device's window, then go out together.
        resume_at = device.last_sent_at + timedelta(seconds=MIN_SECONDS_BETWEEN_PUSHES)
        for row in live:
            row.next_attempt_at = resume_at
        return release("deferred")

    payload = json.dumps(build_payload(staff, device, live), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    endpoint, p256dh, auth = device.endpoint, device.p256dh, device.auth
    live_ids = [row.id for row in live]
    db_session.commit()  # no transaction stays open across the HTTP call

    try:
        response = webpush.send(endpoint=endpoint, p256dh=p256dh, auth=auth, payload=payload, keys=keys,
                                ttl_seconds=TTL_SECONDS, topic=PUSH_TOPIC)
        status, retry_after, error = response.status, response.retry_after_seconds, f"HTTP_{response.status}"
    except webpush.PushTransportError as exc:
        status, retry_after, error = None, None, str(exc)[:48] or "TRANSPORT_ERROR"

    now = _now()
    live = db_session.execute(select(PushDelivery).where(PushDelivery.id.in_(live_ids))).scalars().all()
    device = db_session.get(PushSubscription, device_id)
    if status is not None and 200 <= status < 300:
        _finish(live, "SENT", error=None, now=now)
        if device is not None:
            device.last_sent_at = now
            device.lease_expires_at = None
        db_session.commit()
        return "sent"
    if status in (404, 410):
        _finish(live, "FAILED_PERMANENT", error="SUBSCRIPTION_GONE", now=now)
        db_session.flush()
        if device is not None:
            push_subscriptions.remove_device(device_id, "SUBSCRIPTION_GONE")
        db_session.commit()
        return "gone"
    if status is None or status == 429 or status >= 500:
        attempts = max(row.attempt_count for row in live) + 1
        if attempts >= MAX_ATTEMPTS:
            _finish(live, "FAILED_PERMANENT", error=error, now=now)
            outcome = "failed_permanent"
        else:
            delay = max(compute_backoff_delay(attempts - 1), retry_after or 0.0)
            for row in live:
                row.attempt_count, row.last_error = attempts, error
                row.next_attempt_at = now + timedelta(seconds=delay)
            outcome = "retry"
        if device is not None:
            device.lease_expires_at = None
        db_session.commit()
        return outcome
    _finish(live, "FAILED_PERMANENT", error=error, now=now)
    if device is not None:
        device.lease_expires_at = None
    db_session.commit()
    return "failed_permanent"


def send_due(*, batch_size: int = 50, now: datetime | None = None) -> dict:
    keys = vapid.keys_from_config(current_app.config)
    outcomes: dict[str, int] = {}
    if keys is None:
        return outcomes
    for device_id in _due_device_ids(now or _now(), batch_size):
        if not _take_device(device_id, now or _now()):
            outcomes["busy"] = outcomes.get("busy", 0) + 1
            continue
        try:
            outcome = _send_device(device_id, keys, now or _now())
        except Exception as exc:  # noqa: BLE001 -- one broken device must not stop the others
            db_session.rollback()
            # Its lease simply expires and the next pass retries this device.
            # The class name only: a message could carry the endpoint.
            log.warning("owner push: a device pass failed (%s); it is retried after its lease", type(exc).__name__)
            outcome = "error"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return outcomes


def run_once(*, batch_size: int = 50) -> dict:
    """One pass inside an app context. Decided and reported, never silent:
    "push is off" and "nothing to do" are different answers."""
    if not vapid.push_enabled(current_app.config):
        return {"ran": False, "reason": "push_not_configured"}
    expanded = expand_pending(batch_size=batch_size)
    sent = send_due(batch_size=batch_size)
    return {"ran": True, "expand": expanded, "send": sent}


class PushWorker:
    def __init__(self, app: Flask, *, interval_seconds: float = TICK_SECONDS):
        self._app = app
        self._interval = interval_seconds
        self._timer: threading.Timer | None = None
        self._stopped = threading.Event()
        self._last_logged: str | None = None

    def start(self) -> None:
        self._stopped.clear()
        self._schedule_next()

    def stop(self) -> None:
        self._stopped.set()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _schedule_next(self) -> None:
        if self._stopped.is_set():
            return

        def _tick():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 -- a dead timer chain would stop every notification for good
                log.exception("owner push: worker tick crashed")
            finally:
                self._schedule_next()

        self._timer = threading.Timer(self._interval, _tick)
        self._timer.daemon = True
        self._timer.start()

    def tick(self) -> dict:
        with self._app.app_context():
            try:
                result = run_once()
            finally:
                db_session.remove()
        # Say what each tick decided, but only when it changes or did work --
        # a line every 10 seconds saying "nothing" would bury everything else.
        summary = json.dumps(result, sort_keys=True)
        did_work = result.get("ran") and (any(result["expand"].values()) or result["send"])
        if did_work or summary != self._last_logged:
            log.info("owner push: tick %s", summary)
            self._last_logged = summary
        return result


def start_worker_if_configured(app: Flask) -> PushWorker | None:
    """The ONLY place a push thread is ever started: VAPID key configured AND
    not a test app. With push off no thread exists."""
    vapid.warn_if_key_unusable(app)
    if app.config.get("TESTING"):
        return None
    if not vapid.push_enabled(app.config):
        return None
    worker = PushWorker(app)
    worker.start()
    app.extensions["owner_push_worker"] = worker
    log.info("owner push: worker started (tick every %ss)", TICK_SECONDS)
    return worker
