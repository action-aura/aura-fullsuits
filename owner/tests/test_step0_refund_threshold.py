"""Owner decisions (2026-10-25): the refund THRESHOLD and the Super Admin's
LATE-REFUND OVERRIDE.

1. A refund of package money stops the licence (2 days later, as before) only
   when the package money given back is LARGER than a percentage of the
   package money paid on that sale/subscription so far -- "above", so exactly
   the threshold keeps the licence. System default 50%; the owner (a real
   Super Admin) changes the default and, per customer, an override (empty =
   use the default). Several refunds of one sale add up. The commission
   follows the same line: above it the sale does not count; at or below it the
   sale still counts. A refund of only the pair keeps today's rule.
2. After the 30 days a refund is refused -- except that a real Super Admin
   (the account itself, not a permission) may allow it with a written reason
   of at least 10 characters, audited once. Its commission effect: the sale
   stops counting; what was still held or merely earned is cancelled, what was
   approved or paid gets a take-back (REVERSED) row, and a take-back on
   approved money is never paid out.

Every test asserts rows and amounts, not just status codes.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_staff
from tests.test_step0_ladder import (
    _entries,
    _ladder_sales,
    _net_by_order,
    _pay_month,
    _refund,
    _rows,
    _sell,
    _world,
)

UTC = timezone.utc
REASON = "The bank charged the shop twice for this sale"


# ------------------------------------------------------------------ helpers --

def _stops(app):
    """[(licence, to_status, devices_removed, source_refund_id)] -- every scheduled row."""
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import LicenseScheduledStop

        return [(s.license_id, s.to_status, s.devices_removed, s.source_refund_id)
                for s in db_session.execute(select(LicenseScheduledStop).order_by(LicenseScheduledStop.created_at))
                .scalars().all()]


def _licence_stops(app):
    return [s for s in _stops(app) if s[1] is not None]


def _refund_row(app, refund_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialRefund

        r = db_session.get(CommercialRefund, refund_id)
        return {"status": r.status, "percent": r.threshold_percent_applied, "over": r.package_refund_over_threshold,
                "override_reason": r.window_override_reason, "override_by": r.window_override_by_staff_user_id,
                "override_at": r.window_override_at, "paid_at": r.paid_at}


def _staff(app, staff_id):
    from app.extensions import db_session
    from app.models.staff import StaffUser

    return db_session.get(StaffUser, staff_id)


def _set_default(app, w, percent):
    with app.app_context():
        from app.commercial_sales import refund_policy
        from app.extensions import db_session

        refund_policy.set_default_threshold_percent(percent, _staff(app, w["admin"]))
        db_session.commit()


def _set_customer(app, w, customer_id, percent):
    with app.app_context():
        from app.commercial_sales import refund_policy
        from app.extensions import db_session
        from app.models.customers import Customer

        refund_policy.set_customer_threshold_percent(db_session.get(Customer, customer_id), percent,
                                                     _staff(app, w["admin"]))
        db_session.commit()


def _audits(app, action_code):
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog

        return db_session.execute(select(AuditLog).where(AuditLog.action_code == action_code)).scalars().all()


def _all_refunds(app):
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialRefund

        return db_session.execute(select(CommercialRefund)).scalars().all()


def _create(app, w, order_id, amount, *, actor="manager", override_reason=None):
    rows = _rows(app, order_id)
    with app.app_context():
        from app.commercial_sales.refunds import create_refund
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice

        kwargs = {} if override_reason is None else {"window_override_reason": override_reason}
        return create_refund(db_session.get(CommercialInvoice, rows["invoice_id"]), amount=Decimal(amount),
                             reason="Customer asked for the money back", payment_record_id=None,
                             actor_employee_profile_id=w[f"{actor}_profile"], actor_staff_user_id=w[actor],
                             **kwargs).id


def _approve(app, w, refund_id, actor):
    with app.app_context():
        from app.commercial_sales.refunds import approve_refund
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialRefund

        approve_refund(db_session.get(CommercialRefund, refund_id), actor_staff_user_id=w[actor])


def _confirm(app, w, refund_id, actor, override_reason=None):
    with app.app_context():
        from app.commercial_sales.refunds import confirm_refund
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialRefund

        kwargs = {} if override_reason is None else {"window_override_reason": override_reason}
        confirm_refund(db_session.get(CommercialRefund, refund_id), actor_staff_user_id=w[actor], **kwargs)


def _long_ago():
    return datetime.now(UTC) - timedelta(days=31)


# ======================================================= 1. the threshold --

def test_a_refund_of_49_percent_keeps_the_licence_and_the_commission(app, seeded):
    w = _world(app)
    sale = _sell(app, w, "p1")["order_id"]
    refund_id = _refund(app, w, sale, "122.50")  # 49% of the 250 package
    assert _licence_stops(app) == []
    assert [s["status"] for s in _ladder_sales(app, w["seller_profile"])] == ["COUNTED"]
    assert _net_by_order(app, w["seller_profile"])[sale] == Decimal("20.00")
    row = _refund_row(app, refund_id)
    assert (row["percent"], row["over"]) == (50, False)


def test_a_refund_of_51_percent_stops_the_licence_after_two_days_and_the_sale_does_not_count(app, seeded):
    w = _world(app)
    sale = _sell(app, w, "p1")["order_id"]
    refund_id = _refund(app, w, sale, "127.50")  # 51%
    rows = _rows(app, sale)
    assert _licence_stops(app) == [(rows["license_id"], "SUSPENDED", None, refund_id)]
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import LicenseScheduledStop

        stop = db_session.execute(select(LicenseScheduledStop)).scalars().one()
        paid_at = _refund_row(app, refund_id)["paid_at"]
        assert stop.effective_at.astimezone(UTC) == paid_at.astimezone(UTC) + timedelta(days=2)
    [counted] = _ladder_sales(app, w["seller_profile"])
    assert (counted["status"], counted["cause"]) == ("UNCOUNTED", "REFUND_IN_HOLD")
    assert _net_by_order(app, w["seller_profile"]).get(sale, Decimal("0.00")) == Decimal("0.00")
    row = _refund_row(app, refund_id)
    assert (row["percent"], row["over"]) == (50, True)


def test_exactly_the_threshold_is_not_above_it(app, seeded):
    """The owner said "above": 125.00 of 250 is 50%, not above 50%."""
    w = _world(app)
    sale = _sell(app, w, "p1")["order_id"]
    _refund(app, w, sale, "125.00")
    assert _licence_stops(app) == []
    assert [s["status"] for s in _ladder_sales(app, w["seller_profile"])] == ["COUNTED"]


def test_a_customer_set_to_20_percent_is_stopped_by_a_25_percent_refund_and_others_are_not(app, seeded):
    w = _world(app)
    strict = _sell(app, w, "p1")["order_id"]
    ordinary = _sell(app, w, "p1")["order_id"]
    _set_customer(app, w, _rows(app, strict)["customer_id"], 20)
    strict_refund = _refund(app, w, strict, "62.50")  # 25%
    _refund(app, w, ordinary, "62.50")
    assert _licence_stops(app) == [(_rows(app, strict)["license_id"], "SUSPENDED", None, strict_refund)]
    statuses = {s["order_id"]: s["status"] for s in _ladder_sales(app, w["seller_profile"])}
    assert statuses == {strict: "UNCOUNTED", ordinary: "COUNTED"}
    assert _refund_row(app, strict_refund)["percent"] == 20


def test_changing_the_default_applies_to_customers_without_their_own_threshold(app, seeded):
    w = _world(app)
    plain = _sell(app, w, "p1")["order_id"]
    own = _sell(app, w, "p1")["order_id"]
    _set_customer(app, w, _rows(app, own)["customer_id"], 60)
    _set_default(app, w, 30)
    plain_refund = _refund(app, w, plain, "93.75")  # 37.5% > 30
    own_refund = _refund(app, w, own, "125.00")     # 50% <= 60
    assert [s[0] for s in _licence_stops(app)] == [_rows(app, plain)["license_id"]]
    assert (_refund_row(app, plain_refund)["percent"], _refund_row(app, own_refund)["percent"]) == (30, 60)
    # and the customer's own threshold can be cleared back to the default
    _set_customer(app, w, _rows(app, own)["customer_id"], None)
    with app.app_context():
        from app.commercial_sales.refund_policy import threshold_percent_for_customer

        assert threshold_percent_for_customer(_rows(app, own)["customer_id"]) == 30


def test_two_partial_refunds_add_up_and_the_second_crosses_the_threshold(app, seeded):
    w = _world(app)
    sale = _sell(app, w, "p1")["order_id"]
    first = _refund(app, w, sale, "75.00")   # 30%
    assert _licence_stops(app) == []
    assert [s["status"] for s in _ladder_sales(app, w["seller_profile"])] == ["COUNTED"]
    second = _refund(app, w, sale, "75.00")  # 60% together
    assert _licence_stops(app) == [(_rows(app, sale)["license_id"], "SUSPENDED", None, second)]
    with app.app_context():
        from app.extensions import db_session
        from app.models.commissions import CommissionLadderSale

        ladder_sale = db_session.execute(select(CommissionLadderSale)).scalars().one()
        assert (ladder_sale.status, ladder_sale.source_refund_id) == ("UNCOUNTED", second)
    assert (_refund_row(app, first)["over"], _refund_row(app, second)["over"]) == (False, True)


def test_the_pair_is_refunded_first_and_a_small_package_refund_after_it_keeps_the_licence(app, seeded):
    """Pair-only refunds keep today's rule (its two devices go after 2 days,
    the licence keeps working); the next 100 is package money -- 40% of the
    250 -- so the licence keeps working and the sale keeps counting."""
    w = _world(app)
    sale = _sell(app, w, "p1", pairs=1)["order_id"]  # 300
    licence = _rows(app, sale)["license_id"]
    pair_refund = _refund(app, w, sale, "50.00")
    assert _stops(app) == [(licence, None, 2, pair_refund)]
    _refund(app, w, sale, "100.00")
    assert _stops(app) == [(licence, None, 2, pair_refund)]
    assert [s["status"] for s in _ladder_sales(app, w["seller_profile"])] == ["COUNTED"]


def test_a_monthly_package_counts_every_payment_of_the_subscription_as_package_money_paid(app, seeded):
    """Package 2, three months paid (105): giving back the first month's 35 is
    33% -- the licence keeps working and the sale keeps counting. A shop that
    paid one month and gets 20 of its 35 back (57%) is stopped."""
    w = _world(app)
    sale = _sell(app, w, "p2")["order_id"]
    sub = _rows(app, sale)["subscription_id"]
    _pay_month(app, w, sub, "35.00")
    _pay_month(app, w, sub, "35.00")
    assert [s["status"] for s in _ladder_sales(app, w["seller_profile"])] == ["COUNTED"]
    _refund(app, w, sale, "35.00")
    assert _licence_stops(app) == []
    assert [s["status"] for s in _ladder_sales(app, w["seller_profile"])] == ["COUNTED"]

    short = _sell(app, w, "p2")["order_id"]
    short_refund = _refund(app, w, short, "20.00")
    assert _licence_stops(app) == [(_rows(app, short)["license_id"], "SUSPENDED", None, short_refund)]


def test_the_default_is_50_and_a_bad_value_is_refused(app, seeded):
    w = _world(app)
    with app.app_context():
        from app.commercial_sales import refund_policy
        from app.extensions import db_session

        assert refund_policy.get_default_threshold_percent() == 50
        for bad in (-1, 101, "30", 1.5, True, None):
            with pytest.raises(ValueError):
                refund_policy.set_default_threshold_percent(bad, _staff(app, w["admin"]))
        db_session.rollback()
        assert refund_policy.get_default_threshold_percent() == 50


# ====================================== 2. the Super Admin's late override --

def test_after_30_days_a_non_super_admin_is_refused_even_with_a_reason(app, seeded):
    from app.commercial_sales.errors import CommercialSalesError

    w = _world(app)
    sale = _sell(app, w, "p1", confirmed_at=_long_ago())["order_id"]
    with pytest.raises(CommercialSalesError) as plain:
        _create(app, w, sale, "250.00")
    assert plain.value.code == "REFUND_WINDOW_CLOSED"
    with pytest.raises(CommercialSalesError) as overridden:
        _create(app, w, sale, "250.00", override_reason=REASON)
    assert overridden.value.code == "REFUND_OVERRIDE_SUPER_ADMIN_ONLY"
    assert _all_refunds(app) == [] and _audits(app, "REFUND_WINDOW_OVERRIDDEN") == []


def test_after_30_days_a_super_admin_without_a_written_reason_is_refused(app, seeded):
    from app.commercial_sales.errors import CommercialSalesError

    w = _world(app)
    sale = _sell(app, w, "p1", confirmed_at=_long_ago())["order_id"]
    for reason in ("", "   ", "too short"):  # "too short" is 9 characters
        with pytest.raises(CommercialSalesError) as exc:
            _create(app, w, sale, "250.00", actor="admin", override_reason=reason)
        assert exc.value.code == "REFUND_OVERRIDE_REASON_REQUIRED"
    with pytest.raises(CommercialSalesError) as none_given:
        _create(app, w, sale, "250.00", actor="admin")
    assert none_given.value.code == "REFUND_WINDOW_CLOSED"
    assert _all_refunds(app) == [] and _audits(app, "REFUND_WINDOW_OVERRIDDEN") == []


def test_after_30_days_a_super_admin_with_a_reason_may_refund_audited_once_and_the_sale_stops_counting(app, seeded):
    w = _world(app)
    sale = _sell(app, w, "p1", confirmed_at=_long_ago())["order_id"]
    assert _net_by_order(app, w["seller_profile"])[sale] == Decimal("20.00")  # earned, past its hold
    refund_id = _create(app, w, sale, "250.00", actor="admin", override_reason="  " + REASON + "  ")
    row = _refund_row(app, refund_id)
    assert (row["status"], row["override_reason"], row["override_by"]) == ("DRAFT", REASON, w["admin"])
    assert row["override_at"] is not None
    _approve(app, w, refund_id, "manager")  # two people: the admin asked, the manager approves
    _confirm(app, w, refund_id, "manager")  # the refund carries the override: no second one needed
    assert _refund_row(app, refund_id)["status"] == "PAID"
    [audit] = _audits(app, "REFUND_WINDOW_OVERRIDDEN")
    assert (audit.actor_staff_user_id, audit.entity_public_id, audit.reason) == (w["admin"], str(refund_id), REASON)
    assert _licence_stops(app) == [(_rows(app, sale)["license_id"], "SUSPENDED", None, refund_id)]
    [counted] = _ladder_sales(app, w["seller_profile"])
    assert (counted["status"], counted["cause"]) == ("UNCOUNTED", "REFUND_LATE_OVERRIDE")
    # the merely-earned row is cancelled, never paid
    assert [(a, s) for o, a, s, _p, _m in _entries(app, w["seller_profile"]) if o == sale] == [
        (Decimal("20.00"), "CANCELLED")]


def test_a_late_override_while_the_window_is_still_open_records_nothing(app, seeded):
    w = _world(app)
    sale = _sell(app, w, "p1")["order_id"]
    refund_id = _create(app, w, sale, "10.00", actor="admin", override_reason=REASON)
    row = _refund_row(app, refund_id)
    assert (row["override_reason"], row["override_by"], row["override_at"]) == (None, None, None)
    assert _audits(app, "REFUND_WINDOW_OVERRIDDEN") == []


def test_a_refund_paid_out_after_the_30_days_needs_the_super_admin_at_pay_out(app, seeded):
    """Requested on day 29 by the manager; the 30 days pass before it is paid
    out. The manager is refused (with or without a reason), the Super Admin
    without a reason too; with a reason it is paid, audited once."""
    from app.commercial_sales.errors import CommercialSalesError

    w = _world(app)
    sale = _sell(app, w, "p1", confirmed_at=datetime.now(UTC) - timedelta(days=29))["order_id"]
    refund_id = _create(app, w, sale, "250.00")
    _approve(app, w, refund_id, "admin")
    with app.app_context():
        from app.extensions import db_session

        moved = _long_ago()
        db_session.execute(text("UPDATE owner_payment_correction_history SET created_at = :t WHERE new_status = 'CONFIRMED'"),
                           {"t": moved})
        db_session.execute(text("UPDATE owner_payment_allocations SET allocated_at = :t"), {"t": moved})
        db_session.commit()
    for actor, reason, code in (("manager", None, "REFUND_WINDOW_CLOSED"),
                                ("manager", REASON, "REFUND_OVERRIDE_SUPER_ADMIN_ONLY"),
                                ("admin", "short", "REFUND_OVERRIDE_REASON_REQUIRED")):
        with pytest.raises(CommercialSalesError) as exc:
            _confirm(app, w, refund_id, actor, override_reason=reason)
        assert exc.value.code == code
        assert _refund_row(app, refund_id)["status"] == "APPROVED"
    assert _stops(app) == []
    _confirm(app, w, refund_id, "admin", override_reason=REASON)
    row = _refund_row(app, refund_id)
    assert (row["status"], row["override_by"], row["override_reason"]) == ("PAID", w["admin"], REASON)
    assert len(_audits(app, "REFUND_WINDOW_OVERRIDDEN")) == 1
    from app.commercial_sales.errors import CommercialSalesError as _E

    with pytest.raises(_E):  # paying it out again is refused and audits nothing more
        _confirm(app, w, refund_id, "admin", override_reason=REASON)
    assert len(_audits(app, "REFUND_WINDOW_OVERRIDDEN")) == 1


def _approve_entry(app, w, order_id):
    with app.app_context():
        from app.commissions.ledger import approve_commission_entry
        from app.extensions import db_session
        from app.models.commissions import CommissionLadderSale, CommissionLedgerEntry

        ladder_sale = db_session.execute(select(CommissionLadderSale).where(
            CommissionLadderSale.sales_order_id == order_id)).scalars().one()
        entry = db_session.execute(select(CommissionLedgerEntry).where(
            CommissionLedgerEntry.ladder_sale_id == ladder_sale.id)).scalars().one()
        approve_commission_entry(entry, actor_staff_user_id=w["manager"])
        return entry.id


def _batch(app, w):
    with app.app_context():
        from datetime import date

        from app.commissions.ledger import approve_payout_batch, create_payout_batch

        batch = create_payout_batch({"batch_reference": f"PAY-{uuid.uuid4().hex[:6]}", "period_start": date(2026, 1, 1),
                                     "period_end": date(2026, 12, 31)}, w["admin"])
        approve_payout_batch(batch, actor_staff_user_id=w["manager"])
        return batch.id


def _pay(app, w, entry_id, batch_id):
    with app.app_context():
        from app.commissions.ledger import record_payout
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry, CommissionPayoutBatch

        line = record_payout(db_session.get(CommissionLedgerEntry, entry_id), db_session.get(CommissionPayoutBatch, batch_id),
                             actor_staff_user_id=w["admin"])
        return Decimal(line.amount)


def _late_override_refund(app, w, sale):
    refund_id = _create(app, w, sale, "250.00", actor="admin", override_reason=REASON)
    _approve(app, w, refund_id, "manager")
    _confirm(app, w, refund_id, "manager")
    return refund_id


def test_a_late_refund_of_an_approved_commission_takes_it_back_and_it_is_never_paid_out(app, seeded):
    from app.commissions.errors import CommissionError

    w = _world(app)
    sale = _sell(app, w, "p1", confirmed_at=_long_ago())["order_id"]
    entry_id = _approve_entry(app, w, sale)
    _late_override_refund(app, w, sale)
    assert sorted((a, s) for o, a, s, _p, _m in _entries(app, w["seller_profile"]) if o == sale) == [
        (Decimal("-20.00"), "REVERSED"), (Decimal("20.00"), "APPROVED")]
    batch_id = _batch(app, w)
    with pytest.raises(CommissionError) as exc:
        _pay(app, w, entry_id, batch_id)
    assert exc.value.code == "COMMISSION_TAKEN_BACK"
    with app.app_context():
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry, CommissionPayoutLine

        assert db_session.execute(select(CommissionPayoutLine)).scalars().all() == []
        assert db_session.get(CommissionLedgerEntry, entry_id).status == "APPROVED"


def test_a_late_refund_of_a_paid_commission_records_a_take_back_against_the_seller(app, seeded):
    w = _world(app)
    sale = _sell(app, w, "p1", confirmed_at=_long_ago())["order_id"]
    entry_id = _approve_entry(app, w, sale)
    assert _pay(app, w, entry_id, _batch(app, w)) == Decimal("20.00")
    _late_override_refund(app, w, sale)
    assert sorted((a, s) for o, a, s, _p, _m in _entries(app, w["seller_profile"]) if o == sale) == [
        (Decimal("-20.00"), "REVERSED"), (Decimal("20.00"), "PAID")]
    assert _net_by_order(app, w["seller_profile"])[sale] == Decimal("0.00")


def test_a_partly_taken_back_approved_commission_pays_only_what_is_left(app, seeded):
    """Percentage seller: 10% of 250 = 25.00, approved; a 100 refund (40%,
    below the threshold) takes back 10.00 in proportion; the payout pays 15.00."""
    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1")["order_id"]
    with app.app_context():
        from app.commissions.ledger import approve_commission_entry
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry

        entry = db_session.execute(select(CommissionLedgerEntry)).scalars().one()
        db_session.execute(text("UPDATE owner_commission_ledger_entries SET payable_after = NULL"))
        db_session.commit()
        approve_commission_entry(entry, actor_staff_user_id=w["manager"])
        entry_id = entry.id
    _refund(app, w, sale, "100.00")
    assert _pay(app, w, entry_id, _batch(app, w)) == Decimal("15.00")


# ================================================================ screens --

def _super_admin_client(app, client):
    email = f"owner-{uuid.uuid4().hex[:6]}@example.com"
    staff_id = make_staff(app, email, super_admin=True, mfa=True)
    login_and_verify_mfa(client, email)
    return staff_id


def _stored_default(app):
    with app.app_context():
        from app.commercial_sales.refund_policy import THRESHOLD_SETTING_KEY
        from app.extensions import db_session
        from app.models.audit import SystemSetting

        row = db_session.execute(select(SystemSetting).where(SystemSetting.key == THRESHOLD_SETTING_KEY)).scalars().first()
        return None if row is None else row.value


def test_the_super_admin_sets_the_default_on_the_system_page(app, client, seeded):
    _super_admin_client(app, client)
    html = client.get("/system/backups").get_data(as_text=True)
    assert re.search(r'name="threshold_percent"[^>]*value="50"', html)
    csrf = get_csrf(html)
    for bad in ("-1", "101", "abc", "1.5", ""):
        client.post("/system/refund-licence-threshold", data={"csrf_token": csrf, "threshold_percent": bad})
    assert _stored_default(app) is None
    resp = client.post("/system/refund-licence-threshold", data={"csrf_token": csrf, "threshold_percent": "30"})
    assert resp.status_code == 302 and _stored_default(app) == {"percent": 30}
    [audit] = _audits(app, "REFUND_THRESHOLD_DEFAULT_CHANGED")
    assert (audit.before_state_redacted, audit.after_state_redacted) == ({"percent": 50}, {"percent": 30})
    client.get("/locale/ar?next=/")
    assert "حد إيقاف الترخيص عند الاسترداد" in client.get("/system/backups").get_data(as_text=True)


def test_a_role_holding_the_settings_permission_is_not_enough_for_the_default(app, client, seeded):
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import Permission, Role, RolePermission

        role = Role(code="SETTINGS_CLERK", name="Settings clerk", description="custom", is_system_role=False)
        db_session.add(role)
        db_session.flush()
        for code in ("system.view", "system.manage_settings"):
            perm = db_session.execute(select(Permission).where(Permission.code == code)).scalars().one()
            db_session.add(RolePermission(role_id=role.id, permission_id=perm.id))
        db_session.commit()
    make_staff(app, "clerk@example.com", role_codes=["SETTINGS_CLERK"], mfa=True)
    login_and_verify_mfa(client, "clerk@example.com")
    html = client.get("/system/backups").get_data(as_text=True)
    assert 'name="threshold_percent"' not in html
    resp = client.post("/system/refund-licence-threshold", data={"csrf_token": get_csrf(html), "threshold_percent": "5"})
    assert resp.status_code == 403
    assert _stored_default(app) is None


def _customer_threshold(app, customer_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer

        return db_session.get(Customer, customer_id).refund_licence_stop_threshold_percent


def test_the_super_admin_sets_and_clears_a_customers_threshold_on_its_page(app, client, seeded):
    w = _world(app)
    customer_id = _rows(app, _sell(app, w, "p1")["order_id"])["customer_id"]
    _super_admin_client(app, client)
    html = client.get(f"/customers/{customer_id}").get_data(as_text=True)
    assert 'data-refund-threshold-effective="50"' in html
    csrf = get_csrf(html)
    url = f"/customers/{customer_id}/refund-licence-threshold"
    for bad in ("-5", "120", "x"):
        client.post(url, data={"csrf_token": csrf, "threshold_percent": bad})
    assert _customer_threshold(app, customer_id) is None
    assert client.post(url, data={"csrf_token": csrf, "threshold_percent": "20"}).status_code == 302
    assert _customer_threshold(app, customer_id) == 20
    assert 'data-refund-threshold-effective="20"' in client.get(f"/customers/{customer_id}").get_data(as_text=True)
    assert client.post(url, data={"csrf_token": csrf, "threshold_percent": ""}).status_code == 302
    assert _customer_threshold(app, customer_id) is None
    changes = _audits(app, "CUSTOMER_REFUND_THRESHOLD_CHANGED")
    assert [(a.before_state_redacted, a.after_state_redacted) for a in changes] == [
        ({"percent": None}, {"percent": 20}), ({"percent": 20}, {"percent": None})]
    client.get("/locale/ar?next=/")
    assert "حد إيقاف الترخيص عند الاسترداد" in client.get(f"/customers/{customer_id}").get_data(as_text=True)


def test_a_seller_who_may_edit_customers_cannot_set_a_threshold(app, client, seeded):
    w = _world(app)
    customer_id = _rows(app, _sell(app, w, "p1")["order_id"])["customer_id"]
    make_staff(app, "editor@example.com", role_codes=["SALES"], mfa=True)
    login_and_verify_mfa(client, "editor@example.com")
    page = client.get(f"/customers/{customer_id}")
    html = page.get_data(as_text=True)
    assert "data-refund-threshold-effective" not in html
    csrf = get_csrf(html) or get_csrf(client.get("/").get_data(as_text=True))
    resp = client.post(f"/customers/{customer_id}/refund-licence-threshold",
                       data={"csrf_token": csrf, "threshold_percent": "0"})
    assert resp.status_code in (403, 404)
    assert _customer_threshold(app, customer_id) is None


def test_the_override_box_is_on_the_refund_form_only_for_the_super_admin_after_the_30_days(app, client, seeded):
    w = _world(app)
    late = _sell(app, w, "p1", confirmed_at=_long_ago())["order_id"]
    fresh = _sell(app, w, "p1")["order_id"]
    late_invoice, fresh_invoice = _rows(app, late)["invoice_id"], _rows(app, fresh)["invoice_id"]
    force_login(client, app, w["manager"])
    assert 'name="override_window"' not in client.get(f"/invoices/{late_invoice}").get_data(as_text=True)
    force_login(client, app, w["admin"])
    assert 'name="override_window"' not in client.get(f"/invoices/{fresh_invoice}").get_data(as_text=True)
    html = client.get(f"/invoices/{late_invoice}").get_data(as_text=True)
    assert 'name="override_window"' in html and 'name="override_reason"' in html
    resp = client.post(f"/invoices/{late_invoice}/refunds/new", data={
        "csrf_token": get_csrf(html), "amount": "250.00", "reason": "Customer asked",
        "override_window": "1", "override_reason": REASON})
    assert resp.status_code == 302, resp.get_data(as_text=True)
    [refund] = _all_refunds(app)
    assert (refund.window_override_by_staff_user_id, refund.window_override_reason) == (w["admin"], REASON)
    client.get("/locale/ar?next=/")
    ar = client.get(f"/invoices/{late_invoice}").get_data(as_text=True)
    assert "السماح بالاسترداد بعد مرور 30 يومًا" in ar


def test_the_new_refusals_are_translated(app, seeded):
    expected = {
        "REFUND_OVERRIDE_SUPER_ADMIN_ONLY": (
            "Only a Super Admin can allow a refund after the 30 days.",
            "فقط المشرف العام يمكنه السماح بالاسترداد بعد مرور 30 يومًا."),
        "REFUND_OVERRIDE_REASON_REQUIRED": (
            "Write why this refund is allowed after the 30 days -- at least 10 characters.",
            "اكتب سبب السماح بهذا الاسترداد بعد مرور 30 يومًا -- 10 أحرف على الأقل."),
    }
    with app.test_request_context():
        from flask_babel import force_locale

        from app.i18n_labels import localize_commercial_sales_error, localize_commission_error

        for code, (english, arabic) in expected.items():
            with force_locale("en"):
                assert localize_commercial_sales_error(code) == english
            with force_locale("ar"):
                assert localize_commercial_sales_error(code) == arabic
        with force_locale("en"):
            assert localize_commission_error("COMMISSION_TAKEN_BACK") == (
                "This commission was taken back (a refund or a correction) and nothing is left to pay.")
        with force_locale("ar"):
            assert localize_commission_error("COMMISSION_TAKEN_BACK") == (
                "تم استرجاع هذه العمولة (استرداد أو تصحيح) ولم يتبقَّ منها شيء للدفع.")
