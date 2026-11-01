"""Shop reminder emails (feat/shop-reminders).

WHY: the shop agreement promises a shop that its selling stops at a known moment, but nothing
told the shop beforehand. A trial ended or a paid year ran out and the till simply locked. The
five mails here say it early and plainly, with how to pay: the trial is about to end, the trial
has ended, the paid time is about to end, the grace week has started, and selling is paused.
(A payment RECEIPT is not here: app/mail/notices.py already mails every payment status.)

THE RULES
  * Once per event. The dedupe key names subscription + kind + the date the clock runs to (+ the
    reminder step), so the nightly job can run any number of times and each reminder is queued
    once, while the NEXT cycle (a renewed end date, an extended trial) is a new event and mails again.
  * A catch-up window, not a single day. A missed night (server down, job failed) must not lose a
    reminder for good, so each kind is due on its day AND the next few days, never before it and
    never after the next stage has taken over. Windows are small on purpose: a window as wide as
    "any time after" would mail a long-lapsed shop a stale 'your trial ends soon' the first night
    this job is switched on.
  * Only a subscription that can still be mailed about: ACTIVE / PAST_DUE / EXPIRED / PILOT
    (never CANCELLED, SUSPENDED, DRAFT), of a customer who is not archived, with a customer
    ACCOUNT email whose account is not DISABLED (an erased or disabled account is never written
    to). No account email means nobody to write to: skipped.
  * Dry run by default (lists what would be sent, writes nothing). `apply` queues the mail through
    app.mail.notices.queue_notice, the one place that owns the once-per-event rule and the
    'never raises' rule, and reports mail that could not be queued (email off here) as NOT_SENT,
    never as sent.
  * "Today" is Jordan's business date (periods.jordan_today); tests pass `as_of`.

THE DAYS (end = Subscription.end_date, the last selling day of the trial or the paid period)
  TRIAL_ENDING   a started trial (PILOT), from end-2 through end (2 days left on its day).
  TRIAL_ENDED    PILOT, end+1 .. end+3: the first night the till is locked. Not on `end` itself:
                 the shop can still sell all of that day, so 'selling is paused' would be untrue.
  RENEWAL_DUE    ACTIVE, yearly (billing_model ANNUAL): end-30 .. end-28 and end-7 .. end-5;
                 monthly: end-5 .. end-3. The step (30 / 7 / 5) is part of the key.
  GRACE_STARTED  PAST_DUE and 1..3 days past end (the nightly expiry scan moves it to PAST_DUE
                 the day after end; grace is late_payment_policy.PAYMENT_GRACE_DAYS).
  LOCKED         EXPIRED and 8..10 days past end.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select

from app.commercial_ops.late_payment_policy import PAST_DUE_START_DAYS, PAYMENT_GRACE_DAYS
from app.extensions import db_session
from app.mail import notices
from app.mail.invitations import email_is_set_up
from app.models.catalog import Plan
from app.models.customer_accounts import CustomerAccount
from app.models.customers import Customer
from app.models.email_outbox import EmailOutbox
from app.models.subscriptions import Subscription
from app.operational_reports.periods import jordan_today

logger = logging.getLogger(__name__)

TRIAL_ENDING, TRIAL_ENDED = "customer_trial_ending", "customer_trial_ended"
RENEWAL_DUE, GRACE_STARTED, LOCKED = "customer_renewal_due", "customer_grace_started", "customer_locked"
KINDS = (TRIAL_ENDING, TRIAL_ENDED, RENEWAL_DUE, GRACE_STARTED, LOCKED)

# Catch-up: how many extra days after its day a reminder is still due (see the module docstring).
CATCH_UP_DAYS = 2
TRIAL_ENDING_DAYS_BEFORE = 2
YEARLY_STEPS = (30, 7)
MONTHLY_STEP = 5

_MAILABLE_STATUSES = ("PILOT", "ACTIVE", "PAST_DUE", "EXPIRED")
_GONE_CUSTOMER_STATUSES = ("ARCHIVED", "CHURNED", "CANCELLED")

# Where the money goes: the company's bank account for transfers / CliQ, and the support line.
BANK_NAME = ("Arab Bank", "البنك العربي")
BANK_IBAN = "JO93ARAB1620000000162291164500"
BANK_ACCOUNT_NAME = "شركة الذئب الاسود لتصميم وتشغيل التطبيقات وبرامج الاعمال"
SUPPORT_PHONE = "+962 78 006 9485"


@dataclass(frozen=True)
class Reminder:
    kind: str
    subscription_id: str
    customer_id: str
    to_address: str
    lang: str
    dedupe_key: str
    lines: dict
    already_queued: bool = False


def _payment_lines() -> tuple[list[str], list[str]]:
    en = [f"Bank: {BANK_NAME[0]}", f"IBAN: {BANK_IBAN}", f"Account name: {BANK_ACCOUNT_NAME}",
          f"Questions or help paying: call or WhatsApp {SUPPORT_PHONE}"]
    ar = [f"البنك: {BANK_NAME[1]}", f"رقم الحساب (IBAN): {BANK_IBAN}", f"اسم الحساب: {BANK_ACCOUNT_NAME}",
          f"للاستفسار أو المساعدة في الدفع: اتصل أو راسلنا على واتساب {SUPPORT_PHONE}"]
    return en, ar


def _shop_label(customer: Customer, subscription: Subscription) -> str:
    name = customer.trade_name or customer.legal_name
    return f"{name} ({subscription.branch_label})" if subscription.branch_label else name


def _lines(kind: str, *, shop: str, end: date, today: date) -> dict:
    pay_en, pay_ar = _payment_lines()
    day = end.isoformat()
    if kind == TRIAL_ENDING:
        head_en = [f"Shop: {shop}", f"Your free trial ends on {day}"]
        head_ar = [f"المتجر: {shop}", f"تنتهي تجربتك المجانية في {day}"]
    elif kind == TRIAL_ENDED:
        head_en = [f"Shop: {shop}", f"Your free trial ended on {day}"]
        head_ar = [f"المتجر: {shop}", f"انتهت تجربتك المجانية في {day}"]
    elif kind == RENEWAL_DUE:
        head_en = [f"Shop: {shop}", f"Paid until: {day}", f"Days left: {(end - today).days}"]
        head_ar = [f"المتجر: {shop}", f"مدفوع حتى: {day}", f"الأيام المتبقية: {(end - today).days}"]
    elif kind == GRACE_STARTED:
        last = (end + timedelta(days=PAST_DUE_START_DAYS + PAYMENT_GRACE_DAYS - 1)).isoformat()
        head_en = [f"Shop: {shop}", f"Paid time ended on: {day}",
                   f"Please pay by {last} to avoid a pause in selling"]
        head_ar = [f"المتجر: {shop}", f"انتهت المدة المدفوعة في: {day}",
                   f"يرجى الدفع قبل نهاية {last} لتجنّب توقف البيع"]
    else:  # LOCKED
        head_en = [f"Shop: {shop}", f"Paid time ended on: {day}"]
        head_ar = [f"المتجر: {shop}", f"انتهت المدة المدفوعة في: {day}"]
    return {"en": head_en + pay_en, "ar": head_ar + pay_ar}


def _due(subscription: Subscription, plan: Plan | None, today: date) -> list[tuple[str, str]]:
    """[(kind, step)] this subscription is due for on `today`. `step` is part of the dedupe key."""
    end = subscription.end_date
    if end is None:  # perpetual, or a trial whose clock has not started: no date to remind about
        return []
    status = subscription.status
    until_end = (end - today).days   # >0 before the end day
    past_end = -until_end            # >0 after the end day
    due: list[tuple[str, str]] = []
    if status == "PILOT":
        if 0 <= until_end <= TRIAL_ENDING_DAYS_BEFORE:
            due.append((TRIAL_ENDING, "-"))
        elif 1 <= past_end <= 1 + CATCH_UP_DAYS:
            due.append((TRIAL_ENDED, "-"))
    elif status == "ACTIVE":
        billing = plan.billing_model if plan is not None else None
        steps = YEARLY_STEPS if billing == "ANNUAL" else ((MONTHLY_STEP,) if billing == "MONTHLY" else ())
        for step in steps:
            if step - CATCH_UP_DAYS <= until_end <= step:
                due.append((RENEWAL_DUE, str(step)))
    elif status == "PAST_DUE":
        if PAST_DUE_START_DAYS <= past_end <= PAST_DUE_START_DAYS + CATCH_UP_DAYS:
            due.append((GRACE_STARTED, "-"))
    elif status == "EXPIRED":
        locked_from = PAST_DUE_START_DAYS + PAYMENT_GRACE_DAYS
        if locked_from <= past_end <= locked_from + CATCH_UP_DAYS:
            due.append((LOCKED, "-"))
    return due


def due_reminders(*, as_of: date | None = None) -> list[Reminder]:
    """Every reminder due on `as_of` (default: Jordan's today), already-queued ones flagged.
    Read-only."""
    today = as_of or jordan_today()
    rows = db_session.execute(
        select(Subscription, Plan, Customer, CustomerAccount)
        .join(Customer, Customer.id == Subscription.customer_id)
        .join(CustomerAccount, CustomerAccount.customer_id == Customer.id)
        .outerjoin(Plan, Plan.id == Subscription.plan_id)
        .where(Subscription.status.in_(_MAILABLE_STATUSES), Subscription.end_date.is_not(None),
               Customer.archived_at.is_(None), Customer.lifecycle_status.not_in(_GONE_CUSTOMER_STATUSES),
               CustomerAccount.status != "DISABLED")
        .order_by(Subscription.end_date, Subscription.id)
    ).all()
    found: list[Reminder] = []
    for subscription, plan, customer, account in rows:
        if not (account.email or "").strip():
            continue
        for kind, step in _due(subscription, plan, today):
            key = f"shop_reminder:{kind}:{subscription.id}:{subscription.end_date.isoformat()}:{step}"
            queued = db_session.execute(select(EmailOutbox.id).where(EmailOutbox.dedupe_key == key)).first() is not None
            found.append(Reminder(
                kind=kind, subscription_id=str(subscription.id), customer_id=str(customer.id),
                to_address=account.email, lang=account.language, dedupe_key=key, already_queued=queued,
                lines=_lines(kind, shop=_shop_label(customer, subscription), end=subscription.end_date, today=today)))
    return found


def run(*, apply: bool = False, as_of: date | None = None) -> dict:
    """Dry run unless `apply`. Returns counts and one entry per reminder (kind, subscription,
    outcome; never an address or a body)."""
    reminders = due_reminders(as_of=as_of)
    result = {"applied": apply, "email_set_up": email_is_set_up(), "due": len(reminders), "queued": 0,
              "would_queue": 0, "already_queued": 0, "not_sent": 0, "items": []}
    for reminder in reminders:
        if reminder.already_queued:
            outcome = "ALREADY_QUEUED"
            result["already_queued"] += 1
        elif not apply:
            outcome = "WOULD_QUEUE"
            result["would_queue"] += 1
        elif notices.queue_notice(reminder.kind, reminder.to_address, lang=reminder.lang,
                                  dedupe_key=reminder.dedupe_key, lines=reminder.lines):
            outcome = "QUEUED"
            result["queued"] += 1
        else:
            outcome = "NOT_SENT"  # email off here, or queueing failed (logged by queue_notice)
            result["not_sent"] += 1
        result["items"].append({"kind": reminder.kind, "subscription_id": reminder.subscription_id, "outcome": outcome})
    return result
