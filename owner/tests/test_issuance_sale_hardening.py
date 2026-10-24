"""Step 0 fix round 3 (2026-10-24) -- the gaps an adversarial verifier found
in 580cd65a ("Sell a licence"), each pinned by a test that fails on it.

  F1  the seller's commission was lost on crash-then-retry: the allocation
      committed on its own, the commission entry in a second commit, and the
      resume read "an allocation exists" as "the commission is done";
  F2  opposite decisions raced: payment status was checked on the in-memory
      row with no lock, so a reject, a second confirm and the allocation
      could each act on a status that had already changed -- measured: a
      licence and a commission issued while the payment ended FAILED;
  F3  the no-charge reason minimum had no test (mutation V7 survived);
  F4  the confirm-time "payment still matches the order" recheck had no
      test (mutation V10 survived);
  F5  extra_devices=10**9 -> 500 (numeric overflow) with the inline customer
      already committed;
  F6  an order confirmed on the order page while its payment was PENDING
      could never be rejected from the sales screen;
  F7  the invoice creator counts as the seller -- pinned, with its allow half.

Interleavings are FORCED, never left to timing: the other manager's request
runs in its own thread and database session, loads its rows first (a screen
opened a moment earlier), and acts exactly when a hook inside the main flow
fires it. The hook returns once that act has finished OR is provably waiting
on a row lock (pg_locks, by its backend pid) -- so each test reaches the same
interleaving on every run, before and after the fix.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import force_login, get_csrf, make_staff
from tests.test_issuance_sale import (
    CONFIRMED,
    NOTHING,
    PEPPER,
    RECORDED,
    _assert_untouched,
    _confirm,
    _counts,
    _record,
    _world,
)


# ---------------------------------------------------------------- helpers --

def _waiting_on_a_lock(pid) -> bool:
    # A raw engine connection, NOT an app context: this runs inside the main
    # flow's hook, and leaving a nested app context would remove the main
    # thread's scoped session (teardown_appcontext) mid-confirmation.
    from app.extensions import get_engine

    with get_engine().connect() as conn:
        return bool(conn.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = :pid AND NOT granted)"), {"pid": pid}
        ).scalar())


class _OtherManager:
    """Another staff member's request: its own thread, its own session.
    load() runs at start() -- the row it returns goes stale exactly like a
    screen opened a moment earlier; act(row) runs when fire() is called."""

    def __init__(self, app, *, load, act):
        self.app, self._load, self._act = app, load, act
        self.loaded, self.go, self.done = threading.Event(), threading.Event(), threading.Event()
        self.pid = None
        self.result = None
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            with self.app.app_context():
                from app.extensions import db_session

                self.pid = db_session.execute(text("SELECT pg_backend_pid()")).scalar_one()
                row = self._load()
                self.loaded.set()
                if not self.go.wait(60):
                    raise RuntimeError("never fired")
                self.result = self._act(row)
        except Exception as exc:  # noqa: BLE001
            self.error = exc
        finally:
            from app.extensions import db_session as scoped

            scoped.remove()
            self.loaded.set()
            self.done.set()

    def start(self):
        self.thread.start()
        assert self.loaded.wait(60)
        assert self.error is None, self.error
        return self

    def fire(self, timeout: float = 30.0) -> str:
        """'done' (the act finished) or 'blocked' (it waits on a row lock)."""
        self.go.set()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.done.wait(0.05):
                return "done"
            if _waiting_on_a_lock(self.pid):
                return "blocked"
        raise AssertionError("the other request neither finished nor waited on a lock")

    def finish(self):
        self.thread.join(60)
        assert not self.thread.is_alive()


def _payment(payment_id):
    from app.extensions import db_session
    from app.models.subscriptions import PaymentRecord

    return db_session.get(PaymentRecord, payment_id)


def _state(app, result):
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_sales import SalesOrder
        from app.models.subscriptions import PaymentRecord

        return (db_session.get(PaymentRecord, result["payment_id"]).status,
                db_session.get(SalesOrder, result["order_id"]).status)


def _history(app, payment_id):
    """Every status write the payment ever got, oldest first."""
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.commercial_ops import PaymentCorrectionHistory

        rows = db_session.execute(
            select(PaymentCorrectionHistory)
            .where(PaymentCorrectionHistory.payment_record_id == payment_id)
            .order_by(PaymentCorrectionHistory.created_at)
        ).scalars().all()
        return [(r.previous_status, r.new_status, r.corrected_by_staff_user_id) for r in rows]


def _code(exc):
    return getattr(exc, "code", None)


def _hook_once(monkeypatch, module, name, before):
    """Replace module.name so its FIRST call runs before() first."""
    real = getattr(module, name)
    fired = {}

    def hooked(*args, **kwargs):
        if not fired:
            fired["outcome"] = before()
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, hooked)
    return fired


def _issued_pipeline_invoice(app, w, *, invoice_creator_profile=None):
    """The quote pipeline up to an ISSUED invoice (no direct_sale code):
    returns (customer_id, invoice_id)."""
    with app.app_context():
        from app.commercial_sales.invoices import create_invoice_from_order, issue_invoice
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
        from app.commercial_sales.sales_orders import confirm_order, create_order_from_quote
        from app.customers.services import create_customer

        customer = create_customer({"legal_name": "Pipeline Shop"}, w["seller"])
        quote = create_quote({"customer_id": customer.id, "currency": "JOD"},
                             actor_employee_profile_id=w["seller_profile"], actor_staff_user_id=w["seller"])
        add_quote_line(quote, plan_id=w["plan"], addon_id=None, quantity=1, actor_staff_user_id=w["seller"])
        submit_quote(quote, actor_staff_user_id=w["seller"])
        record_customer_decision(quote, accepted=True, actor_staff_user_id=w["seller"])
        order = create_order_from_quote(quote, actor_employee_profile_id=w["seller_profile"],
                                        actor_staff_user_id=w["seller"], idempotency_key=str(uuid.uuid4()))
        confirm_order(order, actor_staff_user_id=w["manager"])
        invoice = create_invoice_from_order(order, actor_employee_profile_id=invoice_creator_profile or w["seller_profile"],
                                            actor_staff_user_id=w["seller"], idempotency_key=str(uuid.uuid4()))
        issue_invoice(invoice, actor_staff_user_id=w["manager"])
        return customer.id, invoice.id


# ------------------------------------------- F1: commission exactly once --

def test_crash_at_the_commission_step_writes_neither_and_the_retry_pays_the_seller_once(app, seeded, monkeypatch):
    """RED on 580cd65a (verifier probe P1): the allocation committed, the
    crash lost the commission entry, and the retry -- seeing an allocation
    -- skipped the commission for good (commissions 0 with a licence out).
    Now the allocation and its commission entry are one write: a crash
    leaves neither, and the retry makes both, once."""
    w = _world(app)
    result, _ = _record(app, w)
    import app.commercial_sales.allocation as allocation_module

    real = allocation_module.post_earning_for_allocation
    calls = {"n": 0}

    def _crash_first(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash at the commission step")
        return real(*args, **kwargs)

    monkeypatch.setattr(allocation_module, "post_earning_for_allocation", _crash_first)
    with pytest.raises(RuntimeError):
        _confirm(app, result["order_id"], w["manager"])
    after_crash = _counts(app)
    assert (after_crash["allocations"], after_crash["commissions"], after_crash["licenses"]) == (0, 0, 0), after_crash
    done = _confirm(app, result["order_id"], w["manager"])
    assert done["full_key"] is not None
    assert _counts(app) == CONFIRMED
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.commercial_sales import PaymentAllocation
        from app.models.commissions import CommissionLedgerEntry

        entry = db_session.execute(select(CommissionLedgerEntry)).scalars().one()
        allocation = db_session.execute(select(PaymentAllocation)).scalars().one()
        assert entry.source_payment_allocation_id == allocation.id
        assert (entry.employee_profile_id, entry.commission_amount) == (w["seller_profile"], Decimal("10.00"))


def test_pipeline_allocation_and_its_commission_are_one_write(app, seeded, monkeypatch):
    """The same gap on the invoice page's Allocate button (allocate_payment
    itself, no sale screen): RED on 580cd65a -- (1, 0) after the crash, an
    allocation whose commission nothing would ever post."""
    w = _world(app)
    customer_id, invoice_id = _issued_pipeline_invoice(app, w)
    with app.app_context():
        from app.commercial_sales.payments import confirm_payment, submit_payment

        payment = submit_payment(customer_id=customer_id, amount=Decimal("100.00"), currency="JOD", method="BANK_TRANSFER",
                                 payment_date=date.today(), reference="T-1", actor_staff_user_id=w["seller"])
        confirm_payment(payment, actor_staff_user_id=w["manager"])
        payment_id = payment.id
    import app.commercial_sales.allocation as allocation_module

    real = allocation_module.post_earning_for_allocation
    calls = {"n": 0}

    def _crash_first(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash at the commission step")
        return real(*args, **kwargs)

    monkeypatch.setattr(allocation_module, "post_earning_for_allocation", _crash_first)

    def _allocate():
        with app.app_context():
            from app.extensions import db_session
            from app.models.commercial_sales import CommercialInvoice

            return allocation_module.allocate_payment(
                payment=_payment(payment_id), invoice=db_session.get(CommercialInvoice, invoice_id),
                amount=Decimal("100.00"), actor_staff_user_id=w["manager"])

    with pytest.raises(RuntimeError):
        _allocate()
    c = _counts(app)
    assert (c["allocations"], c["commissions"]) == (0, 0), c
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice

        assert db_session.get(CommercialInvoice, invoice_id).status == "ISSUED"  # the invoice did not move either
    _allocate()
    c = _counts(app)
    assert (c["allocations"], c["commissions"]) == (1, 1), c


# ------------------------------------ F2: one decision per payment, locked --

def test_reject_arriving_mid_confirmation_waits_and_is_refused(app, seeded, monkeypatch):
    """A second manager presses Reject on the payments screen while the sale
    is being confirmed (after its checks, before the payment is confirmed).
    RED on 580cd65a: the reject reported success (payment FAILED), then the
    confirmation -- holding a stale PENDING row -- overwrote it with
    CONFIRMED and issued the licence: one payment, two opposite decisions,
    both "succeeded". Now the confirmation holds the payment row: the reject
    waits for it, then sees CONFIRMED and is refused."""
    w = _world(app)
    other = make_staff(app, f"rej-{uuid.uuid4().hex[:6]}@example.com", role_codes=["FINANCE"], mfa=True)
    result, _ = _record(app, w)
    from app.commercial_sales.payments import reject_payment

    rejecter = _OtherManager(
        app, load=lambda: _payment(result["payment_id"]),
        act=lambda p: reject_payment(p, reason="Transfer bounced", actor_staff_user_id=other),
    ).start()
    import app.commercial_sales.direct_sale as direct_sale_module

    fired = _hook_once(monkeypatch, direct_sale_module, "describe_plan_for_sale", rejecter.fire)
    done = _confirm(app, result["order_id"], w["manager"])
    rejecter.finish()
    payment_status, _order_status = _state(app, result)
    assert rejecter.error is not None, f"the reject reported success, yet the payment ended {payment_status} with a licence"
    assert _code(rejecter.error) == "PAYMENT_NOT_CONFIRMED", repr(rejecter.error)
    assert fired["outcome"] == "blocked"  # it waited for the confirmation, it did not race it
    assert payment_status == "CONFIRMED" and done["full_key"] is not None
    assert _counts(app) == CONFIRMED
    assert _history(app, result["payment_id"]) == [("PENDING", "CONFIRMED", w["manager"])]


def test_stale_reject_after_the_payment_was_confirmed_is_refused(app, seeded, monkeypatch):
    """Verifier probe P2's interleaving: the payments screen was opened while
    the payment was PENDING; Reject lands after the sale confirmed the
    payment, before the allocation. RED on 580cd65a: "licence+commission
    issued while the payment is FAILED". Now the reject re-reads the row
    under a lock, sees CONFIRMED and is refused; the sale finishes."""
    w = _world(app)
    other = make_staff(app, f"rej-{uuid.uuid4().hex[:6]}@example.com", role_codes=["FINANCE"], mfa=True)
    result, _ = _record(app, w)
    from app.commercial_sales.payments import reject_payment

    rejecter = _OtherManager(
        app, load=lambda: _payment(result["payment_id"]),
        act=lambda p: reject_payment(p, reason="Transfer bounced", actor_staff_user_id=other),
    ).start()
    import app.commercial_sales.direct_sale as direct_sale_module

    _hook_once(monkeypatch, direct_sale_module, "allocate_payment", rejecter.fire)
    _confirm(app, result["order_id"], w["manager"])
    rejecter.finish()
    payment_status, _ = _state(app, result)
    c = _counts(app)
    assert rejecter.error is not None, f"the reject reported success; payment {payment_status}, counts {c}"
    assert _code(rejecter.error) == "PAYMENT_NOT_CONFIRMED", repr(rejecter.error)
    assert payment_status == "CONFIRMED" and c == CONFIRMED


def test_allocation_refuses_a_payment_that_stopped_being_confirmed(app, seeded, monkeypatch):
    """allocate_payment checked payment.status once, on the caller's
    in-memory row. Here the old correct route sets the payment FAILED after
    the sale confirmed it and before the allocation. RED on 580cd65a: the
    money was allocated, the commission posted and the licence issued
    against a FAILED payment. Now allocate_payment locks and re-reads the
    payment row: refused, nothing allocated, no commission, no licence."""
    w = _world(app)
    other = make_staff(app, f"cor-{uuid.uuid4().hex[:6]}@example.com", role_codes=["FINANCE"], mfa=True)
    result, _ = _record(app, w)
    from app.subscriptions.services import correct_payment

    corrector = _OtherManager(
        app, load=lambda: _payment(result["payment_id"]),
        act=lambda p: correct_payment(p, "FAILED", "the bank recalled the transfer", other),
    ).start()
    import app.commercial_sales.direct_sale as direct_sale_module
    from app.commercial_sales.errors import CommercialSalesError

    fired = _hook_once(monkeypatch, direct_sale_module, "allocate_payment", corrector.fire)
    with pytest.raises(CommercialSalesError) as exc:
        _confirm(app, result["order_id"], w["manager"])
    corrector.finish()
    assert corrector.error is None and fired["outcome"] == "done"
    assert exc.value.code == "PAYMENT_NOT_CONFIRMED"
    c = _counts(app)
    assert (c["allocations"], c["commissions"], c["licenses"], c["subscriptions"]) == (0, 0, 0, 0), c
    assert _state(app, result)[0] == "FAILED"


def test_a_stale_second_confirm_gets_already_confirmed(app, seeded):
    """Two managers open the same PENDING payment; the first confirms; the
    second presses Confirm on the page they opened before. RED on 580cd65a:
    the second confirmation also succeeded -- a second history row and the
    real checker's verified_by overwritten. Now: a clean
    PAYMENT_ALREADY_CONFIRMED and one confirmation on record."""
    w = _world(app)
    other = make_staff(app, f"two-{uuid.uuid4().hex[:6]}@example.com", role_codes=["FINANCE"], mfa=True)
    result, _ = _record(app, w)
    from app.commercial_sales.payments import confirm_payment

    second = _OtherManager(
        app, load=lambda: _payment(result["payment_id"]),
        act=lambda p: confirm_payment(p, actor_staff_user_id=other),
    ).start()
    with app.app_context():
        confirm_payment(_payment(result["payment_id"]), actor_staff_user_id=w["manager"])
    assert second.fire() == "done"
    second.finish()
    assert _code(second.error) == "PAYMENT_ALREADY_CONFIRMED", repr(second.error)
    assert _history(app, result["payment_id"]) == [("PENDING", "CONFIRMED", w["manager"])]
    with app.app_context():
        assert _payment(result["payment_id"]).verified_by_staff_user_id == w["manager"]


def test_the_one_writer_records_the_status_it_actually_replaced(app, seeded):
    """correct_payment (the old correct route's writer) decides on the
    locked current row too: a correction made from a page opened while the
    payment was PENDING, after someone confirmed it, must record that it
    replaced CONFIRMED -- the correction history is the audit trail of a
    money decision being reversed, and "PENDING -> FAILED" would hide it."""
    w = _world(app)
    other = make_staff(app, f"cor-{uuid.uuid4().hex[:6]}@example.com", role_codes=["FINANCE"], mfa=True)
    result, _ = _record(app, w)
    from app.commercial_sales.payments import confirm_payment
    from app.subscriptions.services import correct_payment

    corrector = _OtherManager(
        app, load=lambda: _payment(result["payment_id"]),
        act=lambda p: correct_payment(p, "FAILED", "the bank recalled the transfer", other),
    ).start()
    with app.app_context():
        confirm_payment(_payment(result["payment_id"]), actor_staff_user_id=w["manager"])
    assert corrector.fire() == "done"
    corrector.finish()
    assert corrector.error is None, repr(corrector.error)
    # Compared as a set: created_at is the writing TRANSACTION's start time
    # (now()), and the corrector's transaction began when its page was
    # loaded -- before the confirmation -- so ordering by it does not give
    # the order of the writes.
    history = _history(app, result["payment_id"])
    assert len(history) == 2 and set(history) == {
        ("PENDING", "CONFIRMED", w["manager"]), ("CONFIRMED", "FAILED", other)}, history


def test_payments_screen_confirm_racing_the_sale_confirm_is_one_confirmation(app, seeded, monkeypatch):
    """Manager B confirms the payment on the payments screen while manager A
    confirms the whole sale. RED on 580cd65a: both confirmations went
    through (two CONFIRMED history rows). Now B waits for A's row lock,
    then gets a clean PAYMENT_ALREADY_CONFIRMED -- and the sale is whole."""
    w = _world(app)
    other = make_staff(app, f"pay-{uuid.uuid4().hex[:6]}@example.com", role_codes=["FINANCE"], mfa=True)
    result, _ = _record(app, w)
    from app.commercial_sales.payments import confirm_payment

    b = _OtherManager(
        app, load=lambda: _payment(result["payment_id"]),
        act=lambda p: confirm_payment(p, actor_staff_user_id=other),
    ).start()
    import app.commercial_sales.direct_sale as direct_sale_module

    fired = _hook_once(monkeypatch, direct_sale_module, "describe_plan_for_sale", b.fire)
    done = _confirm(app, result["order_id"], w["manager"])
    b.finish()
    assert _code(b.error) == "PAYMENT_ALREADY_CONFIRMED", repr(b.error)
    assert fired["outcome"] == "blocked"
    assert _history(app, result["payment_id"]) == [("PENDING", "CONFIRMED", w["manager"])]
    assert done["full_key"] is not None and _counts(app) == CONFIRMED


def test_four_managers_confirming_one_payment_at_once_exactly_one_wins(app, seeded):
    """Real threads, released together after each loaded the PENDING row.
    RED on 580cd65a: all four confirmations succeeded. Now one wins, three
    get PAYMENT_ALREADY_CONFIRMED (not a 500), one history row."""
    w = _world(app)
    managers = [w["manager"]] + [
        make_staff(app, f"race{i}-{uuid.uuid4().hex[:6]}@example.com", role_codes=["FINANCE"], mfa=True) for i in range(3)
    ]
    result, _ = _record(app, w)
    from app.commercial_sales.payments import confirm_payment

    barrier = threading.Barrier(len(managers))
    outcomes, lock = [], threading.Lock()

    def worker(actor):
        try:
            with app.app_context():
                payment = _payment(result["payment_id"])
                assert payment.status == "PENDING"
                barrier.wait(30)
                confirm_payment(payment, actor_staff_user_id=actor)
                outcome = "ok"
        except Exception as exc:  # noqa: BLE001
            outcome = _code(exc) or repr(exc)
        finally:
            from app.extensions import db_session as scoped

            scoped.remove()
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker, args=(m,)) for m in managers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert sorted(outcomes) == ["PAYMENT_ALREADY_CONFIRMED"] * 3 + ["ok"], outcomes
    history = _history(app, result["payment_id"])
    assert [(h[0], h[1]) for h in history] == [("PENDING", "CONFIRMED")]
    with app.app_context():
        assert _payment(result["payment_id"]).verified_by_staff_user_id == history[0][2]


# ------------------------------------------------ F3: no-charge reason --

def test_no_charge_reason_must_be_at_least_ten_characters(app, seeded):
    """Mutation V7 ("1 character is enough") survived every test. Nine
    characters are refused before any write; ten are enough (the boundary,
    both sides)."""
    w = _world(app)
    from app.licensing.issuance import LicenseIssuanceError, issue_no_charge_license

    def issue(reason):
        with app.app_context():
            return issue_no_charge_license(reason=reason, new_customer_legal_name="Demo Shop", plan_id=w["plan"],
                                           idempotency_key=str(uuid.uuid4()), actor_staff_user_id=w["admin"],
                                           license_pepper=PEPPER)

    with pytest.raises(LicenseIssuanceError, match="at least 10 characters"):
        issue("123456789")
    with pytest.raises(LicenseIssuanceError, match="at least 10 characters"):
        issue("   12345678   ")  # padding does not count
    assert _counts(app) == NOTHING
    assert issue("1234567890")["full_key"] is not None
    assert _counts(app)["licenses"] == 1


# ------------------------------------- F4: confirm-time money recheck --

@pytest.mark.parametrize("tamper", [{"amount": Decimal("99.00")}, {"currency": "USD"}], ids=["amount", "currency"])
def test_confirm_refuses_a_payment_that_no_longer_matches_the_order(app, seeded, tamper):
    """Mutation V10 (the confirm-time amount/currency recheck off) survived.
    A payment edited after the sale was recorded must not be confirmed,
    allocated or turned into a licence."""
    w = _world(app)
    result, _ = _record(app, w)
    with app.app_context():
        from app.extensions import db_session

        payment = _payment(result["payment_id"])
        for field, value in tamper.items():
            setattr(payment, field, value)
        db_session.commit()
    from app.commercial_sales.direct_sale import DirectSaleError

    with pytest.raises(DirectSaleError, match="does not match its total"):
        _confirm(app, result["order_id"], w["manager"])
    assert _counts(app) == RECORDED
    _assert_untouched(app)


# ------------------------------------------------- F5: absurd sizes --

def test_absurd_extra_device_count_is_refused_before_any_write(app, seeded):
    """RED on 580cd65a (probe P3): extra_devices=10**9 reached the database
    -- "numeric field overflow" (a 500) -- with the inline customer already
    committed. Now refused before any write, with nothing left behind."""
    w = _world(app)
    # Step 0 packages: the screen sells pairs, so the cap is in pairs.
    from app.commercial_sales.direct_sale import MAX_EXTRA_DEVICE_PAIRS_PER_SALE as MAX_PAIRS
    from app.commercial_sales.direct_sale import DirectSaleError

    n = 10**9
    with pytest.raises(DirectSaleError, match="at most"):
        _record(app, w, extra_device_pairs=n, amount=Decimal("100.00") + Decimal("50.00") * n)
    with pytest.raises(DirectSaleError, match="at most"):
        _record(app, w, extra_device_pairs=MAX_PAIRS + 1,
                amount=Decimal("100.00") + Decimal("50.00") * (MAX_PAIRS + 1))
    assert _counts(app) == NOTHING
    # the allow half: the maximum itself is a sale
    result, _ = _record(app, w, extra_device_pairs=MAX_PAIRS,
                        amount=Decimal("100.00") + Decimal("50.00") * MAX_PAIRS)
    assert result["total"] == Decimal("100.00") + Decimal("50.00") * MAX_PAIRS
    assert _counts(app) == RECORDED


def test_a_total_too_large_to_store_is_refused_before_any_write(app, seeded):
    """A legal price and device count whose TOTAL no longer fits the money
    columns (Numeric(12, 2)): RED on 580cd65a -- the same overflow 500
    after the customer was written."""
    w = _world(app, plan_price=Decimal("9999999999.00"))
    from app.commercial_sales.direct_sale import DirectSaleError

    with pytest.raises(DirectSaleError, match="too large"):
        _record(app, w, extra_device_pairs=1, amount=Decimal("10000000049.00"))
    assert _counts(app) == NOTHING


# ------------------------------------- F6: reject an order-confirmed sale --

def _confirm_order_elsewhere(app, w, result):
    with app.app_context():
        from app.commercial_sales.sales_orders import confirm_order
        from app.extensions import db_session
        from app.models.commercial_sales import SalesOrder

        confirm_order(db_session.get(SalesOrder, result["order_id"]), actor_staff_user_id=w["manager"])


def test_sale_confirmed_on_the_order_page_can_still_be_rejected(app, seeded):
    """RED on 580cd65a (probe P4): "This sale is CONFIRMED; it cannot be
    rejected." -- the order was confirmed on /orders/<id>/confirm while the
    payment was PENDING, and the sale was stuck. With no invoice, no
    allocation and no licence there is nothing to undo: reject works."""
    w = _world(app)
    result, _ = _record(app, w)
    _confirm_order_elsewhere(app, w, result)
    with app.app_context():
        from app.commercial_sales.direct_sale import reject_direct_sale

        reject_direct_sale(result["order_id"], reason="No transfer reached the account", actor_staff_user_id=w["manager"])
    assert _state(app, result) == ("FAILED", "CANCELLED")
    assert _counts(app) == RECORDED


def test_sale_with_an_invoice_cannot_be_rejected_from_the_sales_screen(app, seeded):
    """The refuse half: once a document exists downstream (an invoice was
    made from the confirmed order), rejecting here would leave it pointing
    at a cancelled order -- refused, nothing changed."""
    w = _world(app)
    result, _ = _record(app, w)
    _confirm_order_elsewhere(app, w, result)
    with app.app_context():
        from app.commercial_sales.invoices import create_invoice_from_order
        from app.extensions import db_session
        from app.models.commercial_sales import SalesOrder

        create_invoice_from_order(db_session.get(SalesOrder, result["order_id"]),
                                  actor_employee_profile_id=w["seller_profile"], actor_staff_user_id=w["manager"],
                                  idempotency_key=str(uuid.uuid4()))
    from app.commercial_sales.direct_sale import DirectSaleError

    with app.app_context():
        from app.commercial_sales.direct_sale import reject_direct_sale

        with pytest.raises(DirectSaleError, match="invoice"):
            reject_direct_sale(result["order_id"], reason="No transfer reached the account", actor_staff_user_id=w["manager"])
    assert _state(app, result) == ("PENDING", "CONFIRMED")


def test_sale_whose_money_was_allocated_cannot_be_rejected(app, seeded, monkeypatch):
    """The other refuse half: a confirmation that crashed after the money
    was allocated (before the licence) is finished by pressing Confirm
    again, never rejected -- and still not once the old correct route has
    set that allocated payment FAILED (the allocation, not the payment's
    status, is what says money moved)."""
    w = _world(app)
    other = make_staff(app, f"cor-{uuid.uuid4().hex[:6]}@example.com", role_codes=["FINANCE"], mfa=True)
    result, _ = _record(app, w)
    import app.commercial_sales.direct_sale as direct_sale_module

    def _crash(*args, **kwargs):
        raise RuntimeError("simulated crash before fulfilment")

    monkeypatch.setattr(direct_sale_module, "fulfill_order", _crash)
    with pytest.raises(RuntimeError):
        _confirm(app, result["order_id"], w["manager"])
    from app.commercial_sales.direct_sale import DirectSaleError

    def reject():
        with app.app_context():
            direct_sale_module.reject_direct_sale(result["order_id"], reason="Changed my mind about it",
                                                  actor_staff_user_id=w["manager"])

    with pytest.raises(DirectSaleError, match="already confirmed"):
        reject()
    with app.app_context():
        from app.subscriptions.services import PaymentAllocatedError, correct_payment

        # Step 0 fix round 4: the old correct route can no longer set an
        # allocated payment FAILED (tests/test_payment_allocation_ownership.py)
        # -- this setup used that very hole. Pinned here: it is refused.
        with pytest.raises(PaymentAllocatedError):
            correct_payment(_payment(result["payment_id"]), "FAILED", "the bank recalled the transfer", other)
        # But rows written that way BEFORE the fix exist in live databases,
        # so the reject rule must still key on the allocation, not the
        # status. The same state, written directly, as such a row stands.
        from sqlalchemy import update

        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord

        db_session.execute(update(PaymentRecord).where(PaymentRecord.id == result["payment_id"]).values(status="FAILED"))
        db_session.commit()
    with pytest.raises(DirectSaleError, match="already allocated"):
        reject()
    assert _state(app, result) == ("FAILED", "CONFIRMED")
    assert _counts(app)["allocations"] == 1


# ---------------------------------------- F7: who counts as the seller --

def test_the_invoice_creator_counts_as_the_seller_and_cannot_confirm_its_payment(app, seeded):
    """Pins the rule: a pipeline payment naming an invoice belongs to that
    invoice's creator -- the employee the commission ledger credits. A
    FINANCE manager who created the invoice cannot confirm the payment by
    either door; an unrelated manager can (the allow half)."""
    w = _world(app)
    customer_id, invoice_id = _issued_pipeline_invoice(app, w, invoice_creator_profile=w["manager_profile"])
    third = make_staff(app, f"third-{uuid.uuid4().hex[:6]}@example.com", role_codes=["FINANCE"], mfa=True)
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.payments import confirm_payment, submit_payment
        from app.subscriptions.services import PaymentSelfVerificationError, correct_payment

        payment = submit_payment(customer_id=customer_id, amount=Decimal("100.00"), currency="JOD", method="BANK_TRANSFER",
                                 payment_date=date.today(), commercial_invoice_id=invoice_id, reference="T-9",
                                 actor_staff_user_id=w["seller"])
        payment_id = payment.id
        with pytest.raises(CommercialSalesError) as exc:
            confirm_payment(payment, actor_staff_user_id=w["manager"])
        assert exc.value.code == "SELLER_CONFIRMATION_FORBIDDEN"
        with pytest.raises(PaymentSelfVerificationError):
            correct_payment(_payment(payment_id), "CONFIRMED", "legacy door", w["manager"])
    with app.app_context():
        assert _payment(payment_id).status == "PENDING"
        confirm_payment(_payment(payment_id), actor_staff_user_id=third)
        assert (_payment(payment_id).status, _payment(payment_id).verified_by_staff_user_id) == ("CONFIRMED", third)


def test_a_payment_tied_to_no_sale_is_confirmed_by_an_unrelated_manager(app, seeded):
    """The allow half of the seller rule: a payment that names no invoice
    and belongs to no one-screen sale has no seller, so a manager who sells
    elsewhere (has an employee profile) is not blocked from confirming it."""
    w = _world(app)
    _issued_pipeline_invoice(app, w, invoice_creator_profile=w["manager_profile"])  # the manager sells elsewhere
    with app.app_context():
        from app.commercial_sales.payments import confirm_payment, submit_payment
        from app.customers.services import create_customer

        customer = create_customer({"legal_name": "Walk-in Ltd"}, w["seller"])
        payment = submit_payment(customer_id=customer.id, amount=Decimal("40.00"), currency="JOD", method="BANK_TRANSFER",
                                 payment_date=date.today(), reference="T-10", actor_staff_user_id=w["seller"])
        confirm_payment(payment, actor_staff_user_id=w["manager"])
        assert (_payment(payment.id).status, _payment(payment.id).verified_by_staff_user_id) == ("CONFIRMED", w["manager"])


# ------------------------------------------------ the screens, translated --

def test_every_direct_sale_message_is_translated(app):
    """Every DirectSaleError code has an Arabic message, with the same
    placeholders filled -- the service stays English and request-free; the
    screens localize by code (the CommercialSalesError pattern)."""
    from app.commercial_sales.direct_sale import DIRECT_SALE_MESSAGES
    from app.i18n_labels import localize_direct_sale_error

    assert len(DIRECT_SALE_MESSAGES) >= 40
    for code, english in DIRECT_SALE_MESSAGES.items():
        params = {name: "7" for name in re.findall(r"%\((\w+)\)s", english)}
        with app.test_request_context("/", headers={"Accept-Language": "ar"}):
            arabic = localize_direct_sale_error(code, **params)
        with app.test_request_context("/", headers={"Accept-Language": "en"}):
            again_english = localize_direct_sale_error(code, **params)
        assert arabic and arabic != again_english and "%(" not in arabic, code
        assert re.search(r"[؀-ۿ]", arabic), (code, arabic)
        if not any(p.endswith("_status") for p in params):
            assert again_english == english % params, code


def test_sale_form_error_is_shown_in_arabic(app, client, seeded):
    """RED on 580cd65a: the sale form showed str(DirectSaleError) -- English
    on the Arabic screen."""
    w = _world(app)
    force_login(client, app, w["seller"])
    client.get("/locale/ar?next=/")
    page = client.get("/licenses/issuance/new")
    resp = client.post("/licenses/issuance/sale", data={
        "csrf_token": get_csrf(page.get_data(as_text=True)), "idempotency_key": str(uuid.uuid4()),
        "new_customer_legal_name": "Corner Shop LLC", "plan_id": str(w["plan"]), "extra_device_pairs": "0",
        "amount": "99.99", "method": "BANK_TRANSFER", "reference": "CLIQ-1", "payment_date": date.today().isoformat(),
    })
    assert resp.status_code == 400
    body = resp.get_data(as_text=True)
    assert "The amount paid must be the full price" not in body
    assert "يجب أن يكون المبلغ المدفوع السعر الكامل لهذه العملية" in body
    assert _counts(app) == NOTHING


def test_waiting_list_shows_a_status_label_and_a_correct_device_count(app, client, seeded):
    """RED on 580cd65a: the list printed the raw status code (PENDING) and
    "1 extra devices". Step 0 packages: the sale screen sells pairs, so the
    count is now of pairs ("+ 1 extra pair", "+ 2 extra pairs")."""
    w = _world(app)
    _record(app, w, extra_device_pairs=1, amount=Decimal("150.00"))
    _record(app, w, extra_device_pairs=2, amount=Decimal("200.00"), new_customer_legal_name="Second Shop")
    force_login(client, app, w["manager"])
    body = client.get("/licenses/issuance/sales").get_data(as_text=True)
    assert "1 extra pairs" not in body
    assert "+ 1 extra pair" in body and "+ 2 extra pairs" in body
    assert "&middot; PENDING" not in body
    assert "&middot; Pending" in body
