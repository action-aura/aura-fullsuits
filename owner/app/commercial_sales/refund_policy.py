"""Step 0 (2026-10-25) -- the owner's refund THRESHOLD and the Super Admin's
LATE-REFUND OVERRIDE. The one place both rules are decided.

1. THE THRESHOLD. Before this, ANY refund of package money stopped the
   licence 2 days later and took the sale off the seller's ladder (dc812348) --
   a 10 JOD goodwill refund on a 250 package cut the shop off. The owner's
   rule: a refund stops the licence only when the package money given back is
   LARGER than a percentage of the package money paid on that sale /
   subscription so far; at or below it the licence keeps working.
     - "above": strictly greater. 50% of 250 is 125.00; 125.00 back keeps the
       licence, 125.01 stops it.
     - Package money paid so far = the package part of what was collected on
       the sale's invoice + every later confirmed payment of its subscription
       in the same currency (the monthly payments of Package 2/3, a renewal
       of Package 1). The SAME money the ladder counts towards "about 100 JOD"
       (ladder.later_package_payments), so a shop that paid three months of
       Package 2 and gets one month back has had a third, not all, of its
       package money back.
     - The pair (extra devices) is refunded FIRST, on both sides of the
       fraction: package money back = refunds beyond the invoice's
       extra-device part; package money paid on the invoice = collected beyond
       it. So a refund of only the pair is never package money -- today's
       rule for it is unchanged (its devices go after 2 days, the licence
       keeps working; refunds.py).
     - Several refunds of one sale ADD UP: each refund is judged on all the
       package money given back so far, itself included.
     - Percentage: a system default (50%, owner_system_settings, the same
       table and shape as the website-lead retention setting) and a
       per-customer override (owner_customers, NULL = the default). Both set
       by a real Super Admin only (system/routes.py, customers/routes.py).
     - The decision is taken ONCE, when the refund is paid out, and stored on
       the refund (threshold_percent_applied, package_refund_over_threshold).
       A later change of the threshold, or a later payment, never re-judges a
       refund already paid -- the ladder is re-evaluated on every payment
       event and would otherwise count or uncount the sale again.
   Commission: refund_uncounts_sale() is the ONE decision point: above the
   threshold the sale does not count (as before), at or below it the sale
   still counts and the seller keeps the ladder commission. (A seller still on
   a PERCENTAGE plan keeps the old proportional take-back -- 10% of 250 is a
   share of money kept, and money given back was never kept. Only whether a
   sale COUNTS follows the threshold.)

2. THE LATE-REFUND OVERRIDE. "No refunds after 30 days" (b249eb70) stays the
   rule. A real Super Admin -- StaffUser.is_super_admin, checked on the
   account, like the no-charge licence (licensing/issuance.py): refunds.create
   and refunds.approve are grantable permissions and must not become a way
   round it -- may allow one refund after the 30 days with a written reason
   of at least 10 characters. The override is recorded on the refund (who,
   when, why) the first time it is needed -- when the refund is requested
   after the 30 days, or when it is paid out after them -- and audited in the
   same transaction (REFUND_WINDOW_OVERRIDDEN, exactly once per refund).
   Everyone else is refused (REFUND_OVERRIDE_SUPER_ADMIN_ONLY), and so is a
   Super Admin without a reason (REFUND_OVERRIDE_REASON_REQUIRED).
   Its commission effect (ladder.evaluate_sale): above the threshold the sale
   stops counting (REFUND_LATE_OVERRIDE) and its month is re-derived -- a row
   still only EARNED is cancelled, an APPROVED or PAID row gets a take-back
   (REVERSED) row, and an approved row that was taken back is never paid out
   (ledger.record_payout pays only what is left). A take-back of money
   already PAID is a REVERSED row on the seller's record (a debt) that is
   deducted automatically from the seller's NEXT pay-out, oldest first
   (Step 1, 2026-10-25, commissions/clawback.py); a Super Admin may instead
   settle it by hand in the app, with a written reason
   (clawback.settle_by_hand, audited COMMISSION_CLAWBACK_SETTLED_BY_HAND).
"""
from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select

from app.extensions import db_session
from app.models.audit import SystemSetting
from app.models.customers import Customer

logger = logging.getLogger(__name__)

THRESHOLD_SETTING_KEY = "refund_licence_stop_threshold_percent"
DEFAULT_THRESHOLD_PERCENT = 50
MIN_THRESHOLD_PERCENT = 0
MAX_THRESHOLD_PERCENT = 100
OVERRIDE_MIN_REASON_LENGTH = 10

_ZERO = Decimal("0.00")


# ------------------------------------------------------------ the percent --

def valid_percent(value) -> bool:
    # bool is an int subclass; True must not be read as "1%".
    return (isinstance(value, int) and not isinstance(value, bool)
            and MIN_THRESHOLD_PERCENT <= value <= MAX_THRESHOLD_PERCENT)


def _setting_row():
    return db_session.execute(select(SystemSetting).where(SystemSetting.key == THRESHOLD_SETTING_KEY)).scalars().first()


def get_default_threshold_percent() -> int:
    """The stored default, or 50 with no row. A malformed stored value falls
    back to 50 and logs: refunds keep being judged by the owner's default
    rather than crashing or guessing."""
    row = _setting_row()
    if row is None:
        return DEFAULT_THRESHOLD_PERCENT
    percent = row.value.get("percent") if isinstance(row.value, dict) else None
    if not valid_percent(percent):
        logger.warning("owner_system_settings[%s] holds an invalid value (%r); using the default of %d%%",
                       THRESHOLD_SETTING_KEY, row.value, DEFAULT_THRESHOLD_PERCENT)
        return DEFAULT_THRESHOLD_PERCENT
    return percent


def set_default_threshold_percent(percent, staff_user) -> int:
    """Validate and store the default; returns the previous one. Flush only:
    the caller writes the audit row (which commits) on the same session, so
    the change and its audit land together."""
    if not valid_percent(percent):
        raise ValueError(f"percent must be a whole number from {MIN_THRESHOLD_PERCENT} to {MAX_THRESHOLD_PERCENT}")
    previous = get_default_threshold_percent()
    row = _setting_row()
    if row is None:
        db_session.add(SystemSetting(key=THRESHOLD_SETTING_KEY, value={"percent": percent},
                                     updated_by_staff_user_id=staff_user.id))
    else:
        row.value = {"percent": percent}
        row.updated_by_staff_user_id = staff_user.id
    db_session.flush()
    return previous


def set_customer_threshold_percent(customer: Customer, percent, staff_user) -> int | None:
    """None clears the override (the customer follows the default again).
    Returns the previous override. Flush only, like the default."""
    if percent is not None and not valid_percent(percent):
        raise ValueError(f"percent must be a whole number from {MIN_THRESHOLD_PERCENT} to {MAX_THRESHOLD_PERCENT}")
    previous = customer.refund_licence_stop_threshold_percent
    customer.refund_licence_stop_threshold_percent = percent
    db_session.flush()
    return previous


def threshold_percent_for_customer(customer_id) -> int:
    customer = db_session.get(Customer, customer_id) if customer_id is not None else None
    if customer is not None and customer.refund_licence_stop_threshold_percent is not None:
        return customer.refund_licence_stop_threshold_percent
    return get_default_threshold_percent()


# ------------------------------------------------------------- the money --

def package_money_paid(invoice) -> Decimal | None:
    """Package money paid on this package sale so far, or None when the
    invoice is not a package sale's (no plan line: the old rule applies)."""
    from app.commercial_sales.invoices import confirmed_allocated_amount
    from app.commissions import ladder

    sale = ladder.load_package_sale(invoice)
    if sale is None:
        return None
    collected = confirmed_allocated_amount(invoice)
    # The pair first: only what was collected beyond the extra-device part is
    # package money, never more than the package itself.
    on_invoice = min(sale.package_amount, max(_ZERO, Decimal(collected) - sale.other_amount))
    later = sum((Decimal(p.amount) for _at, p in ladder.later_package_payments(sale)), _ZERO)
    return on_invoice + later


def package_money_refunded(invoice, other_amount: Decimal) -> Decimal:
    """Package money given back so far: every PAID refund of the invoice
    beyond its extra-device part (the pair is refunded first)."""
    from app.commercial_sales.refunds import total_confirmed_refunds

    return max(_ZERO, total_confirmed_refunds(invoice) - other_amount)


def decide_refund(refund, invoice) -> None:
    """Judge a refund being paid out (flush only, inside confirm_refund's
    transaction, after the refund is flushed PAID so it counts itself) and
    store the decision on it. Not a package sale: left undecided (NULL)."""
    from app.commissions import ladder

    paid = package_money_paid(invoice)
    if paid is None:
        refund.threshold_percent_applied = None
        refund.package_refund_over_threshold = None
        db_session.flush()
        return
    sale = ladder.load_package_sale(invoice)
    percent = threshold_percent_for_customer(invoice.customer_id)
    refunded = package_money_refunded(invoice, sale.other_amount)
    refund.threshold_percent_applied = percent
    refund.package_refund_over_threshold = is_above_threshold(refunded, paid, percent)
    db_session.flush()


def is_above_threshold(refunded: Decimal, paid: Decimal, percent: int) -> bool:
    """Strictly ABOVE (the owner said "above"). Compared without division, so
    no rounding: refunded / paid > percent / 100."""
    if refunded <= 0:
        return False
    return refunded * 100 > Decimal(percent) * paid


# ------------------------------------------------------ the decision points --

def refund_stops_licence(refund) -> bool:
    """Does this paid refund stop the licence (2 days later)? A refund paid
    out before this rule (undecided) keeps the rule it was paid under."""
    return bool(refund.package_refund_over_threshold)


def refund_uncounts_sale(refund, *, legacy_package_money_back: bool) -> bool:
    """THE commission decision point: does this paid refund take the sale off
    the seller's ladder? Above the threshold yes, at or below it no -- the
    seller keeps the commission. A refund paid out before this rule
    (undecided, NULL) keeps the old rule: any package money given back
    (legacy_package_money_back, computed by the caller as before)."""
    if refund.package_refund_over_threshold is None:
        return legacy_package_money_back
    return bool(refund.package_refund_over_threshold)


# ------------------------------------------------------- the late override --

def check_window_override(actor_staff_user_id, reason) -> str:
    """The refund window is closed: may this actor allow it anyway? Returns
    the cleaned reason, or raises. reason None = no override was asked."""
    from app.commercial_sales.errors import CommercialSalesError
    from app.models.staff import StaffUser

    if reason is None:
        raise CommercialSalesError("REFUND_WINDOW_CLOSED")
    staff = db_session.get(StaffUser, actor_staff_user_id)
    if staff is None or not staff.is_super_admin:
        raise CommercialSalesError("REFUND_OVERRIDE_SUPER_ADMIN_ONLY")
    cleaned = reason.strip()
    if len(cleaned) < OVERRIDE_MIN_REASON_LENGTH:
        raise CommercialSalesError("REFUND_OVERRIDE_REASON_REQUIRED")
    return cleaned


def record_window_override(refund, *, actor_staff_user_id, reason: str, now) -> None:
    refund.window_override_reason = reason
    refund.window_override_by_staff_user_id = actor_staff_user_id
    refund.window_override_at = now


def audit_window_override(refund, invoice, *, actor_staff_user_id) -> None:
    """Writes the REFUND_WINDOW_OVERRIDDEN row -- and, because audit_record
    commits the session, COMMITS the caller's transaction with it: the
    override and its audit row land together or not at all. Called as the
    caller's commit."""
    from app.audit.services import record as audit_record
    from app.commissions import ladder

    ends_at = ladder.refund_window_ends_at(invoice)
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot="SUPER_ADMIN",
        action_code="REFUND_WINDOW_OVERRIDDEN", entity_type="commercial_refund", entity_public_id=str(refund.id),
        reason=refund.window_override_reason,
        after_state={"commercial_invoice_id": str(invoice.id), "amount": str(refund.amount),
                     "window_ended_at": ends_at.isoformat() if ends_at is not None else None,
                     "overridden_at": refund.window_override_at.isoformat()},
    )
