"""2026-11-01 -- the late-payment policy row (owner, 2026-10-30): a shop that has
not paid when its paid time ends gets 7 days of grace, then the lock.

Measured before this change: NO commercial policy row existed anywhere, so the
nightly `commercial expiry-scan --apply` logged SKIPPED_NO_POLICY for every
subscription and nothing ever lapsed.

Every test here asserts what the scan DID to the stored rows and what the signed
lease says (status, past_due_since, commercial_grace_end) -- not just that a
function returned. The two halves of each rule are both pinned: the shop inside
its grace is NOT touched, the shop past it IS; a trial is NOT scanned, an active
subscription in the same run IS.

The scan reads its date from the argument and the lease reads real time, so a
"week later" is simulated by moving end_date AND backdating the PAST_DUE history
row together (_age) -- the nightly run keeps the two in step in production.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

from tests.conftest import make_staff

GRACE = 7


def _today() -> date:
    from app.operational_reports.periods import jordan_today

    return jordan_today()


def _subscription(app, staff_id, *, days_past_end, product_code="AURA_RETAIL", billing_model="ANNUAL", status="ACTIVE"):
    """A subscription whose paid-until date was `days_past_end` days ago."""
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    product = db_session.query(Product).filter_by(product_code=product_code).first()
    plan = Plan(plan_code=f"LP-{uuid.uuid4().hex[:8]}", product_id=product.id, name="Late payment test plan",
                billing_model=billing_model, currency="JOD")
    customer = Customer(legal_name=f"Late Shop {uuid.uuid4().hex[:6]}")
    db_session.add_all([plan, customer])
    db_session.commit()
    sub = create_subscription(
        {"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id,
         "start_date": _today() - timedelta(days=days_past_end + 365), "end_date": _today() - timedelta(days=days_past_end)},
        staff_id,
    )
    if status == "PILOT":
        transition_subscription(sub, "PILOT", staff_id)
    elif status != "DRAFT":
        transition_subscription(sub, "ACTIVE", staff_id)
    return sub


def _policy(app, staff_id):
    from app.commercial_ops.late_payment_policy import seed_late_payment_policy

    policy, created = seed_late_payment_policy(actor_staff_user_id=staff_id)
    assert created
    return policy


def _scan():
    from app.commercial_ops.expiry_scan import run_expiry_scan

    return run_expiry_scan(as_of=_today(), dry_run=False)


def _age(sub, days):
    """`days` days pass: paid-until and the PAST_DUE moment both move back."""
    from app.extensions import db_session
    from app.models.subscriptions import SubscriptionStatusHistory
    from sqlalchemy import select

    sub.end_date = sub.end_date - timedelta(days=days)
    for row in db_session.execute(
        select(SubscriptionStatusHistory).where(SubscriptionStatusHistory.subscription_id == sub.id,
                                                SubscriptionStatusHistory.to_status == "PAST_DUE")
    ).scalars().all():
        row.created_at = row.created_at - timedelta(days=days)
    db_session.commit()


def _lease(sub):
    from app.commercial_ops.assertion_fields import resolve_commercial_assertion_fields

    return resolve_commercial_assertion_fields(sub)


def _unpaid(sub):
    from app.commercial_ops.unpaid import subscription_is_unpaid

    return subscription_is_unpaid(sub)


def _pay(sub, creator_id, approver_id, *, months):
    """The real renewal pipeline a payment goes through: request, payment
    recorded, a DIFFERENT staff member approves, apply."""
    from app.commercial_ops.renewal_dates import add_interval
    from app.commercial_ops.renewal_requests import (
        apply_renewal_request, approve_renewal_request, create_renewal_request, transition_renewal_request,
    )

    start = _today()
    renewal = create_renewal_request(
        subscription=sub, date_rule="LATE_RENEWAL_FROM_PAYMENT_CONFIRMED_DATE", proposed_term_start=start,
        proposed_term_end=add_interval(start, months=months), currency="JOD", actor_staff_user_id=creator_id,
    )
    for to in ("QUOTED", "AWAITING_CONFIRMATION", "AWAITING_PAYMENT", "PAYMENT_RECORDED"):
        transition_renewal_request(renewal, to, creator_id)
    approve_renewal_request(renewal, approver_id)
    apply_renewal_request(renewal.id, approver_id)
    from app.extensions import db_session

    db_session.refresh(sub)


# ------------------------------------------------------------------ the row --

def test_the_row_is_grace_7_with_the_day_after_as_the_start(app, seeded):
    admin = make_staff(app, "lp-row@example.com", super_admin=True)
    with app.app_context():
        policy = _policy(app, admin)
        assert (policy.past_due_start_days, policy.payment_grace_days) == (1, GRACE)
        assert policy.auto_expire_after_grace is True and policy.product.product_code == "AURA_RETAIL"
        assert policy.effective_date == _today()


# ----------------------------------------------- the grace, both directions --

def test_one_day_past_paid_until_is_inside_the_grace_and_not_lapsed(app, seeded):
    admin = make_staff(app, "lp-d1@example.com", super_admin=True)
    with app.app_context():
        _policy(app, admin)
        sub = _subscription(app, admin, days_past_end=1)
        result = _scan()
        assert (result.transitioned_to_past_due, result.transitioned_to_expired) == (1, 0)
        assert sub.status == "PAST_DUE"                       # told it is late...
        lease = _lease(sub)
        assert lease["past_due_since"] is not None and lease["commercial_grace_end"] is not None
        since = datetime.fromisoformat(lease["past_due_since"])
        assert datetime.fromisoformat(lease["commercial_grace_end"]) - since == timedelta(days=GRACE)
        assert _unpaid(sub) is False                          # ...but the till still sells
        assert _scan().transitioned_to_expired == 0           # and a second night does not expire it


def test_seven_days_past_is_still_inside_the_grace(app, seeded):
    admin = make_staff(app, "lp-d7@example.com", super_admin=True)
    with app.app_context():
        _policy(app, admin)
        sub = _subscription(app, admin, days_past_end=1)
        _scan()
        _age(sub, 6)                                          # now 7 days past paid-until
        result = _scan()
        assert result.transitioned_to_expired == 0 and sub.status == "PAST_DUE"
        assert _unpaid(sub) is False


def test_eight_days_past_is_lapsed_and_the_lease_says_so(app, seeded):
    admin = make_staff(app, "lp-d8@example.com", super_admin=True)
    with app.app_context():
        _policy(app, admin)
        sub = _subscription(app, admin, days_past_end=1)
        _scan()
        _age(sub, 7)                                          # now 8 days past paid-until
        result = _scan()
        assert result.transitioned_to_expired == 1 and sub.status == "EXPIRED"
        assert _lease(sub)["term_end"] == sub.end_date.isoformat()
        assert _unpaid(sub) is True                           # the Owner-side gates agree with the till
        from app.commercial_ops.state_resolution import CommercialState, resolve_commercial_state

        decision = resolve_commercial_state(
            subscription_status=sub.status, license_status="ACTIVE", subscription_end_date=sub.end_date,
            license_valid_until=None, as_of=_today(),
        )
        assert decision.state == CommercialState.EXPIRED and decision.may_issue_assertion is False


def test_without_the_policy_row_nothing_ever_lapses(app, seeded):
    """The measured defect, pinned as the baseline the row fixes."""
    admin = make_staff(app, "lp-none@example.com", super_admin=True)
    with app.app_context():
        sub = _subscription(app, admin, days_past_end=400)
        result = _scan()
        assert [f.action for f in result.findings] == ["SKIPPED_NO_POLICY"]
        assert sub.status == "ACTIVE" and _unpaid(sub) is False


# ------------------------------------------------------------------ paying --

def test_paying_restores_a_lapsed_yearly_shop(app, seeded):
    admin = make_staff(app, "lp-pay1@example.com", super_admin=True)
    approver = make_staff(app, "lp-pay1b@example.com", super_admin=True)
    with app.app_context():
        _policy(app, admin)
        sub = _subscription(app, admin, days_past_end=1)
        _scan()
        _age(sub, 7)
        _scan()
        assert sub.status == "EXPIRED" and _unpaid(sub) is True
        _pay(sub, admin, approver, months=12)
        assert sub.status == "ACTIVE" and sub.end_date > _today()
        assert _unpaid(sub) is False
        lease = _lease(sub)
        assert lease["past_due_since"] is None and lease["commercial_grace_end"] is None
        assert _scan().transitioned_to_past_due == 0          # and the next night leaves it alone


def test_paying_inside_the_grace_also_restores(app, seeded):
    admin = make_staff(app, "lp-pay2@example.com", super_admin=True)
    approver = make_staff(app, "lp-pay2b@example.com", super_admin=True)
    with app.app_context():
        _policy(app, admin)
        sub = _subscription(app, admin, days_past_end=1)
        _scan()
        assert sub.status == "PAST_DUE"
        _pay(sub, admin, approver, months=12)
        assert sub.status == "ACTIVE" and _lease(sub)["past_due_since"] is None


# ------------------------------------------------- who the scan must not touch --

def test_a_trial_in_progress_is_never_touched_but_an_active_shop_in_the_same_run_is(app, seeded):
    admin = make_staff(app, "lp-trial@example.com", super_admin=True)
    with app.app_context():
        _policy(app, admin)
        trial = _subscription(app, admin, days_past_end=30, status="PILOT")   # ended on paper, locked by the till
        paid = _subscription(app, admin, days_past_end=30)
        result = _scan()
        assert trial.status == "PILOT" and _unpaid(trial) is False
        assert paid.status == "PAST_DUE"                      # the scan really ran
        assert result.scanned_count == 1


def test_a_shop_whose_paid_time_has_not_ended_is_untouched(app, seeded):
    admin = make_staff(app, "lp-future@example.com", super_admin=True)
    with app.app_context():
        _policy(app, admin)
        sub = _subscription(app, admin, days_past_end=-5)     # paid until 5 days from now
        late = _subscription(app, admin, days_past_end=2)
        _scan()
        assert sub.status == "ACTIVE" and late.status == "PAST_DUE"


def test_the_retail_policy_does_not_reach_clinic(app, seeded):
    """The policy is per product: a Clinic subscription keeps its own (absent) rule."""
    admin = make_staff(app, "lp-clinic@example.com", super_admin=True)
    with app.app_context():
        _policy(app, admin)
        clinic = _subscription(app, admin, days_past_end=40, product_code="AURA_CLINIC")
        retail = _subscription(app, admin, days_past_end=40)
        result = _scan()
        assert clinic.status == "ACTIVE" and retail.status == "PAST_DUE"
        assert result.scanned_count == 2 and [f.action for f in result.findings] == ["SKIPPED_NO_POLICY"]


# ------------------------------------------------------------ monthly plans --

def test_a_monthly_shop_lapses_and_is_restored_by_its_monthly_payment(app, seeded):
    """Monthly payments DO move the paid-until date (the same renewal pipeline,
    one month at a time), so the policy is safe for Packages 2 and 3."""
    admin = make_staff(app, "lp-mo@example.com", super_admin=True)
    approver = make_staff(app, "lp-mob@example.com", super_admin=True)
    with app.app_context():
        _policy(app, admin)
        sub = _subscription(app, admin, days_past_end=1, billing_model="MONTHLY")
        _scan()
        assert sub.status == "PAST_DUE" and _unpaid(sub) is False
        _age(sub, 7)
        _scan()
        assert sub.status == "EXPIRED" and _unpaid(sub) is True
        before = sub.end_date
        _pay(sub, admin, approver, months=1)                  # one month paid
        assert sub.status == "ACTIVE" and sub.end_date > before
        assert sub.end_date <= _today() + timedelta(days=31) and _unpaid(sub) is False


# ---------------------------------------------------------------- the CLI --

def _cli(app, *args):
    return app.test_cli_runner().invoke(args=["commercial", "seed-late-payment-policy", *args])


def _policy_rows():
    from app.extensions import db_session
    from app.models.commercial_ops import CommercialPolicy

    return db_session.query(CommercialPolicy).count()


def _audit_count():
    from app.extensions import db_session
    from app.models.audit import AuditLog

    return db_session.query(AuditLog).filter(AuditLog.action_code == "COMMERCIAL_POLICY_CREATED").count()


def test_cli_dry_run_creates_nothing_and_lists_who_would_be_moved(app, seeded):
    admin = make_staff(app, "lp-cli1@example.com", super_admin=True)
    with app.app_context():
        late = _subscription(app, admin, days_past_end=2)
        very_late = _subscription(app, admin, days_past_end=100)
        fine = _subscription(app, admin, days_past_end=-10)
        ids = (str(late.id), str(very_late.id), str(fine.id))
    result = _cli(app, "--dry-run")
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    with app.app_context():
        assert _policy_rows() == 0 and _audit_count() == 0   # CHECK: nothing was written
    assert out["dry_run"] is True and out["would_create"] is True and out["already_exists"] is False
    assert out["policy"]["payment_grace_days"] == GRACE and out["policy"]["past_due_start_days"] == 1
    moved = {r["subscription_id"] for r in out["would_become_past_due"]}
    assert moved == {ids[0], ids[1]}                          # the shop paid up to the future is not listed
    assert out["would_expire_on_second_nightly_run"] == [ids[1]]
    assert out["subscriptions_scanned"] == 3


def test_cli_creates_the_row_once_audited_and_a_second_run_creates_nothing(app, seeded):
    admin_id = make_staff(app, "lp-cli2@example.com", super_admin=True)
    result = _cli(app, "--actor-email", "lp-cli2@example.com")
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["created"] is True
    with app.app_context():
        assert _policy_rows() == 1 and _audit_count() == 1
    again = _cli(app, "--actor-email", "lp-cli2@example.com")
    assert again.exit_code == 0, again.output
    out2 = json.loads(again.output)
    assert out2["created"] is False and out2["already_exists"] is True and out2["policy_id"] == out["policy_id"]
    with app.app_context():
        assert _policy_rows() == 1 and _audit_count() == 1   # idempotent: still one row, one audit entry
    assert admin_id is not None


def test_cli_write_needs_a_super_admin_actor(app, seeded):
    make_staff(app, "lp-cli3@example.com", super_admin=False)
    for args in ((), ("--actor-email", "lp-cli3@example.com"), ("--actor-email", "nobody@example.com")):
        result = _cli(app, *args)
        assert result.exit_code != 0, args
    with app.app_context():
        assert _policy_rows() == 0


def test_cli_refuses_to_override_another_active_policy_for_retail(app, seeded):
    admin = make_staff(app, "lp-cli4@example.com", super_admin=True)
    with app.app_context():
        from app.commercial_ops.commercial_policy import create_commercial_policy
        from app.extensions import db_session
        from app.models.catalog import Product

        product = db_session.query(Product).filter_by(product_code="AURA_RETAIL").first()
        create_commercial_policy(
            policy_code="someone-elses", warning_offsets_days=[7, 0], notify_role_codes=["SALES"],
            effective_date=date(2026, 1, 1), actor_staff_user_id=admin, product_id=product.id,
        )
    result = _cli(app, "--actor-email", "lp-cli4@example.com")
    assert result.exit_code != 0 and "someone-elses" in result.output
    with app.app_context():
        assert _policy_rows() == 1
