"""Step 1 (2026-10-25) -- a commission taken back AFTER the seller was already
paid is deducted automatically from the seller's next pay-out (app/commissions/
clawback.py, revision c7d41e90b2a5), and a Super Admin can settle one by hand.

Both directions are pinned: the deduction happens (full; partial with carry
forward over two pay-outs; several debts oldest first; hand settlement full and
partial) AND it does not happen when it must not (no debt; a debt fully settled
by hand; another seller's debt; another currency; a take-back whose parent was
never paid, or that was posted BEFORE the payment and so already netted by it).
Every test asserts rows and amounts, not status codes alone.

The ledger rows are written directly (tests/test_sauth_registry_pin_migration.py
::_seed fills their parents) so each case states exactly the state it is about:
a PAID parent, a REVERSED row after it, an APPROVED row waiting to be paid."""
from __future__ import annotations

import threading
from types import SimpleNamespace
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import get_csrf, login_and_verify_mfa, make_staff
from tests.test_issuance_sale import _profile
from tests.test_sauth_registry_pin_migration import _seed

UTC = timezone.utc
D = Decimal
REASON = "The owner took the money back from the seller in cash"
PAID_AT = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------ helpers --

def _world(app, seeded):
    w = {"cache": {}}
    w["admin"] = make_staff(app, f"admin-{uuid.uuid4().hex[:6]}@example.com", super_admin=True, mfa=True)
    w["admin_email"] = None
    w["manager"] = make_staff(app, f"manager-{uuid.uuid4().hex[:6]}@example.com", role_codes=["FINANCE"], mfa=True)
    for key in ("seller", "other"):
        staff = make_staff(app, f"{key}-{uuid.uuid4().hex[:6]}@example.com", role_codes=["SALES"], mfa=True)
        w[key] = _profile(app, staff, f"EMP-{key[:1].upper()}-{uuid.uuid4().hex[:4]}", key.title())
    return w


def _entry(app, w, who, amount, status, *, currency="JOD", reversal_of=None, paid_at=None, earned_at=None,
           created_at=None, kind="LADDER"):
    """One ledger row, written directly. `amount` is signed (a take-back is negative)."""
    overrides = {"employee_profile_id": w[who], "commission_amount": D(amount), "currency": currency,
                 "status": status, "entry_kind": kind, "ladder_month": date(2026, 4, 1),
                 "earned_at": earned_at or datetime(2026, 4, 1, tzinfo=UTC)}
    if reversal_of is not None:
        overrides["reversal_of_ledger_entry_id"] = str(reversal_of)
    if paid_at is not None:
        overrides["paid_at"] = paid_at
    if created_at is not None:
        overrides["created_at"] = created_at
    with app.app_context():
        from app.extensions import db_session

        conn = db_session.connection()
        # LADDER rows need no invoice/allocation (a PAYMENT_RULE row would), which keeps the
        # fixture small; the pay-out and take-back code is the same for every entry_kind.
        if kind == "LADDER":
            if "owner_commission_ladder_sales" not in w["cache"]:
                w["cache"]["owner_commission_ladder_sales"] = _seed(conn, "owner_commission_ladder_sales", w["cache"])
            overrides["ladder_sale_id"] = w["cache"]["owner_commission_ladder_sales"]
        else:  # PAYMENT_RULE: the real reversal code copies this kind and needs an invoice + a fresh allocation
            fresh = dict(w["cache"])
            alloc = _seed(conn, "owner_payment_allocations", fresh)
            for key, value in fresh.items():
                w["cache"].setdefault(key, value)
            overrides["source_payment_allocation_id"] = alloc
            overrides["source_commercial_invoice_id"] = conn.execute(
                text("SELECT commercial_invoice_id FROM owner_payment_allocations WHERE id = :i"), {"i": alloc}).scalar_one()
            overrides.pop("ladder_month")
        entry_id = _seed(conn, "owner_commission_ledger_entries", w["cache"], overrides)
        db_session.commit()
    return uuid.UUID(str(entry_id))


def _paid_parent(app, w, who="seller", amount="10.00", **kw):
    return _entry(app, w, who, amount, "PAID", paid_at=PAID_AT, **kw)


def _debt(app, w, parent, amount, who="seller", *, currency="JOD", created_at=None):
    """A take-back posted AFTER the parent was paid (earned_at after PAID_AT)."""
    return _entry(app, w, who, f"-{amount}", "REVERSED", currency=currency, reversal_of=parent,
                  earned_at=PAID_AT + timedelta(days=30), created_at=created_at)


def _batch(app, w):
    with app.app_context():
        from datetime import date

        from app.commissions.ledger import approve_payout_batch, create_payout_batch

        batch = create_payout_batch({"batch_reference": f"PAY-{uuid.uuid4().hex[:6]}", "period_start": date(2026, 1, 1),
                                     "period_end": date(2026, 12, 31)}, w["admin"])
        approve_payout_batch(batch, actor_staff_user_id=w["manager"])
        return batch.id


def _pay(app, w, entry_id, batch_id):
    """Pay one approved entry; returns the line's amount."""
    with app.app_context():
        from app.commissions.ledger import record_payout
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry, CommissionPayoutBatch

        line = record_payout(db_session.get(CommissionLedgerEntry, entry_id), db_session.get(CommissionPayoutBatch, batch_id),
                             actor_staff_user_id=w["admin"])
        return Decimal(line.amount)


def _remaining(app, debt_id):
    with app.app_context():
        from app.commissions import clawback
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry

        return clawback.remaining(db_session.get(CommissionLedgerEntry, debt_id))


def _deductions(app):
    with app.app_context():
        from app.extensions import db_session
        from app.models.commissions import CommissionClawbackDeduction

        rows = db_session.execute(select(CommissionClawbackDeduction).order_by(
            CommissionClawbackDeduction.created_at, CommissionClawbackDeduction.id)).scalars().all()
        return [(r.debt_ledger_entry_id, Decimal(r.amount), r.kind, r.payout_line_id) for r in rows]


def _audits(app, action_code):
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog

        return db_session.execute(select(AuditLog).where(AuditLog.action_code == action_code)).scalars().all()


def _line_amounts(app):
    with app.app_context():
        from app.extensions import db_session
        from app.models.commissions import CommissionPayoutLine

        return {r.commission_ledger_entry_id: Decimal(r.amount)
                for r in db_session.execute(select(CommissionPayoutLine)).scalars().all()}


def _status(app, entry_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry

        return db_session.get(CommissionLedgerEntry, entry_id).status


def _settle(app, debt_id, actor, reason=REASON, amount=None):
    with app.app_context():
        from app.commissions import clawback

        row = clawback.settle_by_hand(debt_id, actor_staff_user_id=actor, reason=reason, amount=amount)
        return SimpleNamespace(kind=row.kind, amount=Decimal(row.amount), reason=row.reason, payout_line_id=row.payout_line_id)


# ------------------------------------------- the deduction happens (allow) --

def test_a_debt_is_deducted_in_full_from_the_next_payout(app, seeded):
    w = _world(app, seeded)
    debt = _debt(app, w, _paid_parent(app, w), "4.00")
    approved = _entry(app, w, "seller", "25.00", "APPROVED")
    batch = _batch(app, w)

    net = _pay(app, w, approved, batch)

    assert net == D("21.00")
    assert _line_amounts(app)[approved] == D("21.00") and _status(app, approved) == "PAID"
    [(debt_id, amount, kind, line_id)] = _deductions(app)
    assert (debt_id, amount, kind) == (debt, D("4.00"), "PAYOUT") and line_id is not None
    assert _remaining(app, debt) == D("0.00")
    [audit] = _audits(app, "COMMISSION_CLAWBACK_DEDUCTED")
    assert audit.after_state_redacted["amount"] == "4.00" and audit.after_state_redacted["debt_ledger_entry_id"] == str(debt)
    [paid] = _audits(app, "COMMISSION_PAID")
    assert paid.after_state_redacted["amount"] == "21.00" and paid.after_state_redacted["clawback_deducted"] == "4.00"


def test_a_debt_bigger_than_the_payout_pays_nothing_and_carries_the_rest_to_the_next_payout(app, seeded):
    w = _world(app, seeded)
    debt = _debt(app, w, _paid_parent(app, w, amount="40.00"), "30.00")
    first = _entry(app, w, "seller", "20.00", "APPROVED")
    second = _entry(app, w, "seller", "25.00", "APPROVED")
    batch = _batch(app, w)

    assert _pay(app, w, first, batch) == D("0.00")  # never negative
    assert _status(app, first) == "PAID"  # the commission IS settled -- it went to the debt
    assert _line_amounts(app)[first] == D("0.00")
    assert _remaining(app, debt) == D("10.00")

    assert _pay(app, w, second, batch) == D("15.00")
    assert _remaining(app, debt) == D("0.00")
    assert [(d, a) for d, a, _k, _l in _deductions(app)] == [(debt, D("20.00")), (debt, D("10.00"))]
    assert len(_audits(app, "COMMISSION_CLAWBACK_DEDUCTED")) == 2


def test_several_debts_are_taken_oldest_first(app, seeded):
    w = _world(app, seeded)
    parent = _paid_parent(app, w, amount="50.00")
    newer = _debt(app, w, parent, "5.00", created_at=datetime(2026, 7, 2, tzinfo=UTC))
    older = _debt(app, w, parent, "3.00", created_at=datetime(2026, 7, 1, tzinfo=UTC))
    approved = _entry(app, w, "seller", "6.00", "APPROVED")

    assert _pay(app, w, approved, _batch(app, w)) == D("0.00")

    assert [(d, a) for d, a, _k, _l in _deductions(app)] == [(older, D("3.00")), (newer, D("3.00"))]
    assert _remaining(app, older) == D("0.00") and _remaining(app, newer) == D("2.00")


def test_a_hand_settlement_in_full_is_recorded_with_its_reason_and_audited(app, seeded):
    w = _world(app, seeded)
    debt = _debt(app, w, _paid_parent(app, w), "4.00")

    row = _settle(app, debt, w["admin"])

    assert (row.kind, Decimal(row.amount), row.reason, row.payout_line_id) == ("HAND", D("4.00"), REASON, None)
    assert _remaining(app, debt) == D("0.00")
    [audit] = _audits(app, "COMMISSION_CLAWBACK_SETTLED_BY_HAND")
    assert audit.reason == REASON and audit.after_state_redacted["remaining_after"] == "0.00"


def test_a_partial_hand_settlement_leaves_the_rest_for_the_next_payout(app, seeded):
    w = _world(app, seeded)
    debt = _debt(app, w, _paid_parent(app, w), "10.00")
    _settle(app, debt, w["admin"], amount=D("6.00"))
    assert _remaining(app, debt) == D("4.00")
    approved = _entry(app, w, "seller", "25.00", "APPROVED")

    assert _pay(app, w, approved, _batch(app, w)) == D("21.00")

    assert [(k, a) for _d, a, k, _l in _deductions(app)] == [("HAND", D("6.00")), ("PAYOUT", D("4.00"))]
    assert _remaining(app, debt) == D("0.00")


# ------------------------------------------ the deduction does not happen (deny) --

def test_no_debt_means_the_full_amount_is_paid_and_nothing_is_recorded(app, seeded):
    w = _world(app, seeded)
    approved = _entry(app, w, "seller", "25.00", "APPROVED")
    assert _pay(app, w, approved, _batch(app, w)) == D("25.00")
    assert _deductions(app) == [] and _audits(app, "COMMISSION_CLAWBACK_DEDUCTED") == []


def test_a_debt_already_settled_by_hand_is_not_deducted_again(app, seeded):
    w = _world(app, seeded)
    debt = _debt(app, w, _paid_parent(app, w), "4.00")
    _settle(app, debt, w["admin"])
    approved = _entry(app, w, "seller", "25.00", "APPROVED")

    assert _pay(app, w, approved, _batch(app, w)) == D("25.00")
    assert [k for _d, _a, k, _l in _deductions(app)] == ["HAND"]


def test_another_sellers_debt_is_never_taken_from_this_sellers_payout(app, seeded):
    w = _world(app, seeded)
    debt = _debt(app, w, _paid_parent(app, w, who="other"), "4.00", who="other")
    approved = _entry(app, w, "seller", "25.00", "APPROVED")

    assert _pay(app, w, approved, _batch(app, w)) == D("25.00")
    assert _deductions(app) == [] and _remaining(app, debt) == D("4.00")


def test_a_debt_in_another_currency_is_never_taken_from_a_payout_in_this_one(app, seeded):
    w = _world(app, seeded)
    debt = _debt(app, w, _paid_parent(app, w, currency="USD"), "4.00", currency="USD")
    approved = _entry(app, w, "seller", "25.00", "APPROVED", currency="JOD")

    assert _pay(app, w, approved, _batch(app, w)) == D("25.00")
    assert _deductions(app) == [] and _remaining(app, debt) == D("4.00")


def test_a_take_back_whose_parent_was_never_paid_is_not_a_debt(app, seeded):
    w = _world(app, seeded)
    parent = _entry(app, w, "seller", "10.00", "APPROVED")
    _entry(app, w, "seller", "-4.00", "REVERSED", reversal_of=parent)
    other = _entry(app, w, "seller", "25.00", "APPROVED")

    # The parent itself is paid 6.00: the take-back is netted by record_payout's own rule.
    assert _pay(app, w, parent, _batch(app, w)) == D("6.00")
    assert _pay(app, w, other, _batch(app, w)) == D("25.00")
    assert _deductions(app) == []


def test_a_take_back_posted_before_the_payment_is_not_a_debt(app, seeded):
    w = _world(app, seeded)
    parent = _entry(app, w, "seller", "10.00", "PAID", paid_at=PAID_AT)
    _entry(app, w, "seller", "-4.00", "REVERSED", reversal_of=parent, earned_at=PAID_AT - timedelta(days=1))
    approved = _entry(app, w, "seller", "25.00", "APPROVED")

    assert _pay(app, w, approved, _batch(app, w)) == D("25.00")
    assert _deductions(app) == []


# ------------------------- a real take-back of paid money becomes a debt --

def test_reversing_a_paid_entry_makes_a_debt_even_when_the_callers_clock_is_older_than_the_payment(app, seeded, monkeypatch):
    """The writer picks `now` before it wins the seller lock; a payout that
    committed in between has a LATER paid_at. take_back_stamp must still put
    the take-back after the payment, or the debt silently disappears."""
    w = _world(app, seeded)
    parent = _paid_parent(app, w, amount="10.00", kind="PAYMENT_RULE")

    monkeypatch.setattr("app.commissions.ledger.utcnow", lambda: PAID_AT - timedelta(seconds=5))
    with app.app_context():
        from app.commissions.ledger import reverse_commission_entry
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry

        reversal = reverse_commission_entry(db_session.get(CommissionLedgerEntry, parent), reversal_amount=D("4.00"),
                                            reason="refund", actor_staff_user_id=w["admin"])
        reversal_id = reversal.id
    monkeypatch.undo()

    assert _remaining(app, reversal_id) == D("4.00")
    approved = _entry(app, w, "seller", "25.00", "APPROVED")
    assert _pay(app, w, approved, _batch(app, w)) == D("21.00")


def test_reversing_an_approved_entry_is_netted_by_its_own_payout_and_leaves_no_debt(app, seeded):
    w = _world(app, seeded)
    parent = _entry(app, w, "seller", "10.00", "APPROVED", kind="PAYMENT_RULE")
    with app.app_context():
        from app.commissions.ledger import reverse_commission_entry
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry

        reverse_commission_entry(db_session.get(CommissionLedgerEntry, parent), reversal_amount=D("4.00"),
                                 reason="refund", actor_staff_user_id=w["admin"])
    assert _pay(app, w, parent, _batch(app, w)) == D("6.00")
    other = _entry(app, w, "seller", "25.00", "APPROVED")
    assert _pay(app, w, other, _batch(app, w)) == D("25.00")
    assert _deductions(app) == []


# ----------------------------------------------- hand settlement is guarded --

def test_only_a_super_admin_may_settle_by_hand(app, seeded):
    from app.commissions.errors import CommissionError

    w = _world(app, seeded)
    debt = _debt(app, w, _paid_parent(app, w), "4.00")
    with pytest.raises(CommissionError) as exc:
        _settle(app, debt, w["manager"])  # FINANCE holds commissions.pay -- not enough
    assert exc.value.code == "COMMISSION_CLAWBACK_SUPER_ADMIN_ONLY"
    assert _deductions(app) == [] and _remaining(app, debt) == D("4.00")


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_a_hand_settlement_needs_a_written_reason(app, seeded, reason):
    from app.commissions.errors import CommissionError

    w = _world(app, seeded)
    debt = _debt(app, w, _paid_parent(app, w), "4.00")
    with pytest.raises(CommissionError) as exc:
        _settle(app, debt, w["admin"], reason=reason)
    assert exc.value.code == "COMMISSION_CLAWBACK_REASON_REQUIRED"
    assert _deductions(app) == []


def test_a_hand_settlement_can_never_exceed_what_remains_or_settle_twice(app, seeded):
    from app.commissions.errors import CommissionError

    w = _world(app, seeded)
    debt = _debt(app, w, _paid_parent(app, w), "4.00")
    for bad, code in ((D("4.01"), "COMMISSION_CLAWBACK_AMOUNT_TOO_LARGE"), (D("0"), "COMMISSION_CLAWBACK_AMOUNT_INVALID"),
                      (D("-1"), "COMMISSION_CLAWBACK_AMOUNT_INVALID")):
        with pytest.raises(CommissionError) as exc:
            _settle(app, debt, w["admin"], amount=bad)
        assert exc.value.code == code
    assert _deductions(app) == []
    _settle(app, debt, w["admin"], amount=D("1.50"))
    with pytest.raises(CommissionError) as exc:  # 2.50 remain; 2.51 is too much
        _settle(app, debt, w["admin"], amount=D("2.51"))
    assert exc.value.code == "COMMISSION_CLAWBACK_AMOUNT_TOO_LARGE"
    _settle(app, debt, w["admin"])  # the rest
    with pytest.raises(CommissionError) as exc:
        _settle(app, debt, w["admin"])
    assert exc.value.code == "COMMISSION_CLAWBACK_NOTHING_REMAINING"
    assert sum(a for _d, a, _k, _l in _deductions(app)) == D("4.00")


def test_only_a_real_debt_can_be_settled_by_hand(app, seeded):
    from app.commissions.errors import CommissionError

    w = _world(app, seeded)
    approved = _entry(app, w, "seller", "10.00", "APPROVED")
    unpaid_parent_reversal = _entry(app, w, "seller", "-2.00", "REVERSED", reversal_of=approved)
    for target in (approved, unpaid_parent_reversal, uuid.uuid4()):
        with pytest.raises(CommissionError) as exc:
            _settle(app, target, w["admin"])
        assert exc.value.code == "COMMISSION_CLAWBACK_NOT_A_DEBT"
    assert _deductions(app) == []


def test_the_database_itself_refuses_a_hand_row_without_a_reason_and_a_payout_row_without_a_line(app, seeded):
    w = _world(app, seeded)
    debt = _debt(app, w, _paid_parent(app, w), "4.00")

    def insert(**over):
        values = {"id": str(uuid.uuid4()), "debt_ledger_entry_id": str(debt), "employee_profile_id": str(w["seller"]),
                  "amount": "1.00", "currency": "JOD", "kind": "HAND", "reason": "paid in cash",
                  "created_by_staff_user_id": str(w["admin"]), **over}
        cols = ", ".join(values)
        params = ", ".join(f":{k}" for k in values)
        with app.app_context():
            from app.extensions import db_session

            try:
                db_session.execute(text(f"INSERT INTO owner_commission_clawback_deductions ({cols}) VALUES ({params})"), values)
                db_session.commit()
            finally:
                db_session.rollback()

    insert()  # allow: a well-formed HAND row
    for bad in ({"reason": None}, {"reason": "   "}, {"amount": "0"}, {"amount": "-1"}, {"kind": "OTHER"},
                {"kind": "PAYOUT", "reason": None}):
        with pytest.raises(IntegrityError):
            insert(**bad)


# --------------------------------------------------------------- concurrency --

def _run_together(app, jobs):
    """Run each job on its own thread and its own session, released by one barrier."""
    barrier = threading.Barrier(len(jobs))
    outcomes = [None] * len(jobs)

    def worker(i, job):
        with app.app_context():
            from app.extensions import db_session

            try:
                db_session.execute(text("SELECT 1"))  # connection checked out BEFORE the barrier
                barrier.wait(timeout=30)
                outcomes[i] = ("ok", job())
            except Exception as exc:  # noqa: BLE001 -- reported in the assertion
                outcomes[i] = ("error", getattr(exc, "code", None) or f"{type(exc).__name__}: {exc}")
            finally:
                db_session.remove()

    threads = [threading.Thread(target=worker, args=(i, job)) for i, job in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)
    assert all(o is not None for o in outcomes), f"a worker hung: {outcomes}"
    return outcomes


def test_two_payouts_for_one_seller_prepared_at_once_settle_a_debt_exactly_once(app, seeded):
    w = _world(app, seeded)
    debt = _debt(app, w, _paid_parent(app, w, amount="40.00"), "30.00")
    entries = [_entry(app, w, "seller", "20.00", "APPROVED") for _ in range(2)]
    batch = _batch(app, w)

    outcomes = _run_together(app, [lambda e=e: _pay(app, w, e, batch) for e in entries])

    assert all(kind == "ok" for kind, _ in outcomes), outcomes
    nets = sorted(value for _, value in outcomes)
    assert nets == [D("0.00"), D("10.00")]  # 40 payable in total, 30 owed: 10 left, never more
    assert sum(a for _d, a, _k, _l in _deductions(app)) == D("30.00")
    assert _remaining(app, debt) == D("0.00")


def test_a_hand_settlement_racing_a_payout_never_settles_more_than_the_debt(app, seeded):
    w = _world(app, seeded)
    debt = _debt(app, w, _paid_parent(app, w, amount="40.00"), "10.00")
    approved = _entry(app, w, "seller", "6.00", "APPROVED")
    batch = _batch(app, w)

    outcomes = _run_together(app, [lambda: _pay(app, w, approved, batch),
                                   lambda: _settle(app, debt, w["admin"]).amount])

    assert outcomes[0][0] == "ok", outcomes
    taken = sum(a for _d, a, _k, _l in _deductions(app))
    assert taken <= D("10.00")
    assert all(a > 0 for _d, a, _k, _l in _deductions(app))
    # Whichever came second saw what the first took: the debt is settled exactly, once.
    if outcomes[1][0] == "ok":
        assert taken == D("10.00") and _remaining(app, debt) == D("0.00")
    else:
        # the settlement ran second and found the pay-out had already taken it all
        assert outcomes[1][1] == "COMMISSION_CLAWBACK_NOTHING_REMAINING" and taken == D("10.00")
    assert _remaining(app, debt) >= D("0.00")
    assert _line_amounts(app)[approved] == D("6.00") - sum(a for _d, a, k, _l in _deductions(app) if k == "PAYOUT")


# ------------------------------------------------------------------- screens --

def _login_admin(app, client, w):
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        email = db_session.get(StaffUser, w["admin"]).email
    login_and_verify_mfa(client, email)


def _urls(app):
    with app.test_request_context():
        from flask import url_for

        return url_for("commercial_sales_web.list_payout_batches")


def test_the_payout_page_lists_the_debt_and_a_super_admin_can_settle_it_by_hand(app, client, seeded):
    w = _world(app, seeded)
    debt = _debt(app, w, _paid_parent(app, w), "4.00")
    _login_admin(app, client, w)
    page = _urls(app)
    html = client.get(page).get_data(as_text=True)
    assert "Earlier refund take-backs still owed by sellers" in html and f"settle-by-hand" in html

    csrf = get_csrf(html)
    action = f"{page.rsplit('/', 1)[0]}/commission-clawbacks/{debt}/settle-by-hand"
    resp = client.post(action, data={"csrf_token": csrf, "reason": "  "})
    assert resp.status_code == 302 and "COMMISSION_CLAWBACK_REASON_REQUIRED" in resp.headers["Location"]
    assert _deductions(app) == []
    resp = client.post(action, data={"csrf_token": csrf, "reason": REASON, "amount": "1.50"})
    assert resp.status_code == 302 and "error" not in resp.headers["Location"]
    assert _remaining(app, debt) == D("2.50")
    resp = client.post(action, data={"csrf_token": csrf, "reason": REASON, "amount": "9"})
    assert "COMMISSION_CLAWBACK_AMOUNT_TOO_LARGE" in resp.headers["Location"]
    assert _remaining(app, debt) == D("2.50")


def test_a_finance_manager_sees_the_debt_but_cannot_settle_it(app, client, seeded):
    w = _world(app, seeded)
    debt = _debt(app, w, _paid_parent(app, w), "4.00")
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        email = db_session.get(StaffUser, w["manager"]).email
    login_and_verify_mfa(client, email)
    page = _urls(app)
    html = client.get(page).get_data(as_text=True)
    assert "Earlier refund take-backs still owed by sellers" in html and "settle-by-hand" not in html
    action = f"{page.rsplit('/', 1)[0]}/commission-clawbacks/{debt}/settle-by-hand"
    resp = client.post(action, data={"csrf_token": get_csrf(html), "reason": REASON})
    assert resp.status_code == 302 and "COMMISSION_CLAWBACK_SUPER_ADMIN_ONLY" in resp.headers["Location"]
    assert _deductions(app) == [] and _remaining(app, debt) == D("4.00")


def test_the_seller_dashboard_shows_what_was_deducted_and_what_is_still_owed_in_both_languages(app, seeded):
    w = _world(app, seeded)
    parent = _paid_parent(app, w, amount="50.00")
    _debt(app, w, parent, "4.00")
    _debt(app, w, parent, "9.00")
    approved = _entry(app, w, "seller", "6.00", "APPROVED")
    _pay(app, w, approved, _batch(app, w))  # takes 4.00 + 2.00 of the second

    with app.app_context():
        from app.commercial_sales.dashboards import employee_commercial_dashboard

        data = employee_commercial_dashboard(w["seller"], w["admin"], "JOD")
        other = employee_commercial_dashboard(w["other"], w["admin"], "JOD")
    assert (data["clawback_deducted_from_payouts"], data["clawback_outstanding"], data["clawback_settled_by_hand"]) == (
        D("6.00"), D("7.00"), D("0.00"))
    assert other["clawback_outstanding"] == D("0.00") and other["clawback_deducted_from_payouts"] == D("0.00")


def test_the_dashboard_screen_names_the_deduction_in_english_and_arabic(app, client, seeded):
    w = _world(app, seeded)
    staff_email = f"dash-{uuid.uuid4().hex[:6]}@example.com"
    staff = make_staff(app, staff_email, role_codes=["SALES"], mfa=True)
    profile = _profile(app, staff, f"EMP-D-{uuid.uuid4().hex[:4]}", "Dash")
    w["dash"] = profile
    debt_parent = _paid_parent(app, w, who="dash", amount="50.00")
    _debt(app, w, debt_parent, "4.00", who="dash")
    approved = _entry(app, w, "dash", "6.00", "APPROVED")
    _pay(app, w, approved, _batch(app, w))
    login_and_verify_mfa(client, staff_email)
    with app.test_request_context():
        from flask import url_for

        url = url_for("commercial_sales_web.employee_dashboard_view")
    html = client.get(url + "?currency=JOD").get_data(as_text=True)
    assert "Deducted: earlier refund take-back" in html
    client.get("/locale/ar?next=/")
    html_ar = client.get(url + "?currency=JOD").get_data(as_text=True)
    assert "Deducted: earlier refund take-back" not in html_ar and "مخصوم" in html_ar
