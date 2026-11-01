"""Switching on the late-payment policy: after the paid time ends, a shop that
has not paid gets a 7-day grace, then the till locks (owner, 2026-10-30).

WHY this module exists: the expiry scan (expiry_scan.py) and the signed lease
(assertion_fields.py) both read a CommercialPolicy row, and NO such row was ever
created -- every nightly scan logged SKIPPED_NO_POLICY, nothing ever lapsed, and
no commercial_grace_end was ever signed, so a shop that never paid again sold on
for ever. This module holds the one row and the report of what it would do.

What the row does (past_due_start_days=1, payment_grace_days=7):
  * the nightly `commercial expiry-scan --apply` moves an ACTIVE subscription
    1 day past its paid-until date (end_date) to PAST_DUE;
  * a PAST_DUE one 8 days past end_date goes to EXPIRED;
  * every check-in signs subscription_status and commercial_grace_end
    (= PAST_DUE moment + 7 days); the till (policy_evaluator) is RESTRICTED once
    now is past that moment, or at once for EXPIRED -- the lock state the owner
    described;
  * paying = the renewal pipeline (renewal_requests.apply_renewal_request) sets
    a new end_date and flips PAST_DUE/EXPIRED back to ACTIVE; the next check-in
    unlocks the till.
past_due_start_days=0 would skip PAST_DUE and expire on day 1 with no grace at
all, which is why the start is 1 and not 0.

The policy is per PRODUCT (a CommercialPolicy has no plan column), so it covers
all three packages of Aura Retail together. That is safe because every payment,
yearly or monthly, moves end_date through the same renewal pipeline.
Trials (status PILOT) and perpetual plans (end_date NULL) are never scanned.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select

from app.commercial_ops.commercial_policy import create_commercial_policy, resolve_policy_for_subscription
from app.commercial_ops.expiry_scan import run_expiry_scan
from app.extensions import db_session
from app.models.catalog import Product
from app.models.commercial_ops import CommercialPolicy
from app.models.customers import Customer
from app.models.subscriptions import Subscription
from app.operational_reports.periods import jordan_today

PRODUCT_CODE = "AURA_RETAIL"
POLICY_CODE = "aura-retail-late-payment-v1"
PAST_DUE_START_DAYS = 1
PAYMENT_GRACE_DAYS = 7
# Internal reminders before the paid time ends, then on the day.
WARNING_OFFSETS_DAYS = [14, 7, 3, 1, 0]
NOTIFY_ROLE_CODES = ["FINANCE"]


class LatePaymentPolicyError(ValueError):
    """Operator/CLI only -- never shown in a browser, so plain English."""


@dataclass
class LatePaymentPolicyReport:
    as_of: date
    product_code: str
    policy_code: str
    already_exists: bool
    would_create: bool
    conflicting_policy_codes: list[str] = field(default_factory=list)
    policy: dict = field(default_factory=dict)
    scanned_subscriptions: int = 0
    would_become_past_due: list[dict] = field(default_factory=list)
    would_expire: list[dict] = field(default_factory=list)
    # Already so late that the SECOND nightly run expires them (8+ days past
    # end_date): the 7 days are counted from end_date, not from switch-on.
    expire_on_second_run: list[str] = field(default_factory=list)


def _retail_product() -> Product:
    product = db_session.execute(select(Product).where(Product.product_code == PRODUCT_CODE)).scalars().first()
    if product is None:
        raise LatePaymentPolicyError(f"No product {PRODUCT_CODE}; run the catalog seed first.")
    return product


def _policy_values() -> dict:
    return {
        "policy_code": POLICY_CODE, "warning_offsets_days": list(WARNING_OFFSETS_DAYS),
        "notify_role_codes": list(NOTIFY_ROLE_CODES), "past_due_start_days": PAST_DUE_START_DAYS,
        "payment_grace_days": PAYMENT_GRACE_DAYS, "auto_expire_after_grace": True,
    }


def _active_policies_for(product: Product, as_of: date) -> list[CommercialPolicy]:
    return list(db_session.execute(
        select(CommercialPolicy).where(
            CommercialPolicy.product_id == product.id, CommercialPolicy.effective_date <= as_of,
            (CommercialPolicy.retired_date.is_(None)) | (CommercialPolicy.retired_date > as_of),
        )
    ).scalars().all())


def _subscription_summary(subscription_id: str, detail: dict) -> dict:
    subscription = db_session.get(Subscription, subscription_id)
    customer = db_session.get(Customer, subscription.customer_id)
    return {
        "subscription_id": str(subscription.id),
        "customer": customer.legal_name if customer else None,
        "plan_code": subscription.plan.plan_code if subscription.plan_id else None,
        "status": subscription.status,
        "paid_until": subscription.end_date.isoformat() if subscription.end_date else None,
        **detail,
    }


def report_late_payment_policy(*, as_of: date | None = None) -> LatePaymentPolicyReport:
    """Read-only. What would be created, and what the nightly scan would do to
    each subscription TODAY once the row exists (a hypothetical, never-saved
    CommercialPolicy stands in for it when it does not exist yet)."""
    as_of = as_of or jordan_today()
    product = _retail_product()
    existing = db_session.execute(
        select(CommercialPolicy).where(CommercialPolicy.policy_code == POLICY_CODE)
    ).scalars().first()
    others = [p.policy_code for p in _active_policies_for(product, as_of) if p.policy_code != POLICY_CODE]
    report = LatePaymentPolicyReport(
        as_of=as_of, product_code=PRODUCT_CODE, policy_code=POLICY_CODE, already_exists=existing is not None,
        would_create=existing is None and not others, conflicting_policy_codes=others,
        policy={**_policy_values(), "effective_date": as_of.isoformat()},
    )

    if existing is not None:
        resolver = None  # the stored row decides, exactly as the real scan will
    else:
        hypothetical = CommercialPolicy(
            product_id=product.id, policy_version=1, requires_customer_contact=False, effective_date=as_of,
            **{k: v for k, v in _policy_values().items()},
        )

        def resolver(subscription, *, as_of):  # noqa: ANN001
            if subscription.product_id == product.id:
                return hypothetical
            return resolve_policy_for_subscription(subscription, as_of=as_of)

    scan = run_expiry_scan(as_of=as_of, dry_run=True, policy_resolver=resolver)
    report.scanned_subscriptions = scan.scanned_count
    for finding in scan.findings:
        if finding.action == "TRANSITION_TO_PAST_DUE":
            row = _subscription_summary(finding.subscription_id, finding.detail)
            report.would_become_past_due.append(row)
            if finding.detail["days_past_end"] - PAST_DUE_START_DAYS >= PAYMENT_GRACE_DAYS:
                report.expire_on_second_run.append(row["subscription_id"])
        elif finding.action == "TRANSITION_TO_EXPIRED":
            report.would_expire.append(_subscription_summary(finding.subscription_id, finding.detail))
    return report


def seed_late_payment_policy(*, actor_staff_user_id, as_of: date | None = None) -> tuple[CommercialPolicy | None, bool]:
    """(policy, created). Idempotent on policy_code: a second call creates
    nothing and returns the stored row untouched (never edits an existing
    policy). Refuses when ANOTHER active policy already governs Aura Retail --
    ours would silently take over from it (the newest effective_date wins)."""
    as_of = as_of or jordan_today()
    product = _retail_product()
    existing = db_session.execute(
        select(CommercialPolicy).where(CommercialPolicy.policy_code == POLICY_CODE)
    ).scalars().first()
    if existing is not None:
        return existing, False
    others = [p.policy_code for p in _active_policies_for(product, as_of)]
    if others:
        raise LatePaymentPolicyError(
            f"Aura Retail already has an active commercial policy ({', '.join(others)}); "
            "retire it first, then run this again."
        )
    policy = create_commercial_policy(
        effective_date=as_of, actor_staff_user_id=actor_staff_user_id, product_id=product.id,
        **_policy_values(),
    )
    return policy, True
