"""Seller agreement clause 5 -- the monthly commission statement, the 7-day right to object,
and the company's reply within 2 Jordan working days.

What is proven, each with its allow twin:
  window   the Object button exists from 00:00 Jordan on the 1st until (excluding) 00:00 on
           the 8th: one second inside is accepted, the exact edge and one second after are
           refused, one second before issue is refused -- and the DATABASE refuses an
           out-of-window row written directly
  due date 2 working days, Friday + Saturday skipped, counted from the Jordan calendar day
           (22:30 UTC Wednesday is already Thursday in Amman)
  privacy  a seller sees only their own statement; a seller cannot open the manager queue
  content  held / payable / paid / taken-back split, ladder positions, uncounted sales
  reply    a manager answers (accepted / rejected + text); the seller reads it; nobody
           answers their own objection; an answered objection cannot be answered twice
  overdue  an open objection past its deadline is flagged in the queue and on the dashboard

The ledger rows are written directly (tests/test_sauth_registry_pin_migration.py::_seed fills
their parents) so each case states exactly the state it is about. The clock is pinned by
patching the two modules that read it, never by sleeping.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import force_login, get_csrf, make_staff
from tests.test_issuance_sale import _profile
from tests.test_sauth_registry_pin_migration import _seed

AMMAN = ZoneInfo("Asia/Amman")
UTC = timezone.utc
D = Decimal
JUNE = date(2026, 6, 1)
REASON = "The second sale should be on the 50 JOD step, not 30."


def amman(*args) -> datetime:
    return datetime(*args, tzinfo=AMMAN)


# June closes at the end of 30 June; the statement is ISSUED at 00:00 Amman on 1 July.
ISSUED = amman(2026, 7, 1, 0, 0, 0)
CLOSES = amman(2026, 7, 8, 0, 0, 0)


@pytest.fixture()
def clock(monkeypatch):
    """clock.set(dt) pins 'now' for the statements module and its routes."""
    state = {"now": amman(2026, 7, 3, 10, 0, 0)}
    from app.commissions import statement_routes, statements

    monkeypatch.setattr(statements, "utcnow", lambda: state["now"])
    monkeypatch.setattr(statement_routes, "utcnow", lambda: state["now"])

    class _Clock:
        def set(self, moment):
            state["now"] = moment

    return _Clock()


@pytest.fixture()
def w(app, seeded):
    world = {"cache": {}}
    world["admin"] = make_staff(app, f"st-admin-{uuid.uuid4().hex[:5]}@example.com", super_admin=True, mfa=True)
    world["manager"] = make_staff(app, f"st-mgr-{uuid.uuid4().hex[:5]}@example.com", role_codes=["SALES_MANAGER"], mfa=True)
    world["manager2"] = make_staff(app, f"st-mgr2-{uuid.uuid4().hex[:5]}@example.com", role_codes=["SALES_MANAGER"], mfa=True)
    world["finance"] = make_staff(app, f"st-fin-{uuid.uuid4().hex[:5]}@example.com", role_codes=["FINANCE"], mfa=True)
    world["both"] = make_staff(app, f"st-both-{uuid.uuid4().hex[:5]}@example.com", role_codes=["SALES", "SALES_MANAGER"], mfa=True)
    for key in ("seller", "other", "both"):
        staff = world[key] if key == "both" else make_staff(app, f"st-{key}-{uuid.uuid4().hex[:5]}@example.com",
                                                             role_codes=["SALES"], mfa=True)
        world[key] = staff
        world[f"{key}_profile"] = _profile(app, staff, f"EMP-{key[:3].upper()}-{uuid.uuid4().hex[:4]}", key.title())
    return world


def _client(app, staff_id):
    client = app.test_client()
    force_login(client, app, staff_id)
    return client


def _sale(app, w, who, *, position, amount, status="EARNED", payable_after=None, month=JUNE, counted=True, kind="LADDER",
          reversal_of=None, qualified_at=None):
    """One counted (or uncounted) ladder sale and its ledger row. Returns (sale_id, entry_id)."""
    with app.app_context():
        from app.extensions import db_session

        conn = db_session.connection()
        sale_over = {"employee_profile_id": w[f"{who}_profile"], "ladder_month": month,
                     "qualified_at": qualified_at or datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
                     "status": "COUNTED" if counted else "UNCOUNTED"}
        if not counted:
            sale_over.update(uncounted_at=datetime(2026, 6, 20, tzinfo=UTC), uncount_cause="REFUND_IN_HOLD")
        fresh = dict(w["cache"])
        for table in ("owner_sales_orders", "owner_commercial_invoices", "owner_payment_records"):
            fresh.pop(table, None)  # a new order/invoice/payment per sale: the unique index allows one COUNTED sale per order
        sale_id = _seed(conn, "owner_commission_ladder_sales", fresh, sale_over)
        for key, value in fresh.items():
            w["cache"].setdefault(key, value)
        over = {"employee_profile_id": w[f"{who}_profile"], "commission_amount": D(amount), "currency": "JOD",
                "status": status, "entry_kind": kind, "ladder_month": month, "ladder_position": position,
                "earned_at": datetime(2026, 6, 10, 9, 0, tzinfo=UTC), "payable_after": payable_after,
                "ladder_sale_id": sale_id if kind == "LADDER" else None}
        if status == "PAID":
            over["paid_at"] = datetime(2026, 7, 2, tzinfo=UTC)
        if reversal_of is not None:
            over["reversal_of_ledger_entry_id"] = str(reversal_of)
        entry_id = _seed(conn, "owner_commission_ledger_entries", fresh, over)
        db_session.commit()
    return uuid.UUID(str(sale_id)), uuid.UUID(str(entry_id))


def _objections(app, who_staff=None):
    with app.app_context():
        from app.extensions import db_session
        from app.models.commissions import CommissionStatementObjection

        stmt = select(CommissionStatementObjection)
        if who_staff is not None:
            stmt = stmt.where(CommissionStatementObjection.staff_user_id == who_staff)
        return db_session.execute(stmt).scalars().all()


def _object(app, w, who, month, now, reason=REASON):
    from app.commissions import statements

    with app.app_context():
        from app.extensions import db_session
        from app.models.employees import EmployeeProfile
        from app.models.staff import StaffUser

        row = statements.object_to_statement(
            staff=db_session.get(StaffUser, w[who]), profile=db_session.get(EmployeeProfile, w[f"{who}_profile"]),
            month=month, reason=reason, now=now)
        # the service commits (which expires the row): read what the test needs while the session is alive
        return SimpleNamespace(**{c.key: getattr(row, c.key) for c in row.__table__.columns})


def _code(call):
    from app.commissions.errors import CommissionError

    with pytest.raises(CommissionError) as raised:
        call()
    return raised.value.code


# ---------------------------------------------------------------- the 7-day window

def test_the_issue_instant_and_the_window_end_are_exact_jordan_midnights():
    from app.commissions import statements

    assert statements.statement_issued_at(JUNE) == ISSUED.astimezone(UTC)
    assert statements.objection_window_closes_at(JUNE) == CLOSES.astimezone(UTC)
    # December rolls over the year: the statement for December is issued on 1 January
    assert statements.statement_issued_at(date(2026, 12, 1)) == amman(2027, 1, 1).astimezone(UTC)


def test_the_window_edges_one_second_inside_is_accepted_the_exact_end_and_after_are_refused(app, w):
    """Five probes, one month each (one objection per statement). Rows written are counted:
    the refused ones wrote NOTHING."""
    plan = [
        # (month,                 now,                                        expected)
        (date(2026, 1, 1), amman(2026, 2, 1, 0, 0, 0), None),                         # the very first instant: allowed
        (date(2026, 2, 1), amman(2026, 3, 7, 23, 59, 59), None),                      # last second of day 7: allowed
        (date(2026, 3, 1), amman(2026, 4, 8, 0, 0, 0), "STATEMENT_OBJECTION_WINDOW_CLOSED"),   # the exact end: refused
        (date(2026, 4, 1), amman(2026, 5, 8, 0, 0, 1), "STATEMENT_OBJECTION_WINDOW_CLOSED"),   # one second after
        (date(2026, 5, 1), amman(2026, 5, 31, 23, 59, 59), "STATEMENT_NOT_ISSUED"),   # month not closed yet: no statement
    ]
    allowed = 0
    for month, now, expected in plan:
        if expected is None:
            row = _object(app, w, "seller", month, now.astimezone(UTC))
            assert row.status == "OPEN" and row.window_closes_at == amman(month.year, month.month + 1, 8).astimezone(UTC)
            allowed += 1
        else:
            assert _code(lambda m=month, n=now: _object(app, w, "seller", m, n.astimezone(UTC))) == expected
    assert len(_objections(app)) == allowed == 2


def test_the_database_refuses_an_objection_written_outside_the_window_or_answered_by_its_own_seller(app, w):
    """Whatever code writes the row, the rules hold: window, due date, answer shape, no self-answer."""
    from app.commissions import statements

    base = {"staff_user_id": w["seller"], "employee_profile_id": w["seller_profile"], "statement_month": JUNE, "reason": "r",
            "statement_snapshot": "{}", "statement_sha256": "0" * 64, "objected_at": amman(2026, 7, 2).astimezone(UTC),
            "window_closes_at": CLOSES.astimezone(UTC), "due_at": amman(2026, 7, 6).astimezone(UTC)}
    insert = text(
        "INSERT INTO owner_commission_statement_objections (id, staff_user_id, employee_profile_id, statement_month, reason, "
        "statement_snapshot, statement_sha256, objected_at, window_closes_at, due_at, status, reply_text, answered_by_staff_user_id, "
        "answered_at) VALUES (gen_random_uuid(), :staff_user_id, :employee_profile_id, :statement_month, :reason, "
        "CAST(:statement_snapshot AS jsonb), :statement_sha256, :objected_at, :window_closes_at, :due_at, :status, :reply_text, "
        ":answered_by, :answered_at)")
    open_defaults = {"status": "OPEN", "reply_text": None, "answered_by": None, "answered_at": None}
    answered = {"status": "REJECTED", "reply_text": "No.", "answered_by": w["manager"], "answered_at": amman(2026, 7, 3).astimezone(UTC)}
    bad = {
        # due_at is pushed past the objection so ONLY the window rule can refuse this row
        "objected at the window end": {**base, **open_defaults, "objected_at": CLOSES.astimezone(UTC),
                                        "due_at": amman(2026, 7, 10).astimezone(UTC)},
        "due before it was objected": {**base, **open_defaults, "due_at": amman(2026, 7, 1).astimezone(UTC)},
        "open but already has a reply": {**base, **open_defaults, "reply_text": "x"},
        "answered with a blank reply": {**base, **answered, "reply_text": "  "},
        "answered by the seller themselves": {**base, **answered, "answered_by": w["seller"]},
        "answered with no answerer": {**base, **answered, "answered_by": None},
        "a month that is not the 1st": {**base, **open_defaults, "statement_month": date(2026, 6, 15)},
    }
    with app.app_context():
        from app.extensions import db_session

        for label, params in bad.items():
            with pytest.raises(IntegrityError):
                db_session.execute(insert, params)
            db_session.rollback()
            assert db_session.execute(text("SELECT count(*) FROM owner_commission_statement_objections")).scalar_one() == 0, label
        # allow twins: the same shapes done right are accepted
        db_session.execute(insert, {**base, **open_defaults})
        db_session.execute(insert, {**base, **answered, "statement_month": date(2026, 5, 1)})
        db_session.commit()
        assert db_session.execute(text("SELECT count(*) FROM owner_commission_statement_objections")).scalar_one() == 2
        # one objection per seller per statement, whatever code writes the second
        with pytest.raises(IntegrityError):
            db_session.execute(insert, {**base, **open_defaults})
        db_session.rollback()
        assert db_session.execute(text("SELECT count(*) FROM owner_commission_statement_objections")).scalar_one() == 2
    assert statements  # imported to make the module's import a part of this test


# ---------------------------------------------------------------- the 2-working-day deadline

def _weekday(moment):
    return moment.astimezone(AMMAN).strftime("%a")


@pytest.mark.parametrize("objected, due", [
    (amman(2026, 10, 28, 12, 0), amman(2026, 11, 2)),   # Wed: Thu + Sun  -> the weekend is skipped, due end of Sunday
    (amman(2026, 10, 29, 12, 0), amman(2026, 11, 3)),   # Thu: Sun + Mon
    (amman(2026, 10, 30, 12, 0), amman(2026, 11, 3)),   # Fri: Sun + Mon  (Friday itself never counts)
    (amman(2026, 10, 31, 12, 0), amman(2026, 11, 3)),   # Sat: Sun + Mon
    (amman(2026, 11, 1, 12, 0), amman(2026, 11, 4)),    # Sun: Mon + Tue
    (amman(2026, 11, 2, 12, 0), amman(2026, 11, 5)),    # Mon: Tue + Wed
    (datetime(2026, 10, 28, 22, 30, tzinfo=UTC), amman(2026, 11, 3)),   # Wed 22:30 UTC is THURSDAY 01:30 in Amman: Sun + Mon
])
def test_the_reply_is_due_at_the_end_of_the_second_jordan_working_day(objected, due):
    from app.commissions import statements

    assert statements.reply_due_at(objected) == due.astimezone(UTC)


def test_the_fixture_days_are_the_weekdays_the_comments_claim():
    assert [_weekday(amman(2026, 10, d)) for d in (28, 29, 30, 31)] == ["Wed", "Thu", "Fri", "Sat"]
    assert _weekday(amman(2026, 11, 1)) == "Sun"


def test_a_recorded_objection_carries_the_due_date_and_the_exact_window(app, w):
    row = _object(app, w, "seller", JUNE, amman(2026, 7, 2, 11, 0).astimezone(UTC))   # Thursday 2 July 2026
    assert _weekday(row.objected_at) == "Thu"
    assert row.due_at == amman(2026, 7, 7).astimezone(UTC)                              # Sun 5 + Mon 6 -> end of Monday = 00:00 on the 7th
    assert row.window_closes_at == CLOSES.astimezone(UTC) and row.status == "OPEN"


# ---------------------------------------------------------------- the statement itself

def test_the_statement_splits_held_payable_paid_and_taken_back_and_lists_every_sale(app, w, clock):
    from app.commissions import statements

    clock.set(amman(2026, 7, 3, 10))
    past, future = datetime(2026, 6, 20, tzinfo=UTC), datetime(2026, 7, 30, tzinfo=UTC)
    _, first = _sale(app, w, "seller", position=1, amount="20.00", payable_after=past)               # payable (hold over)
    _sale(app, w, "seller", position=2, amount="30.00", payable_after=future)                        # held
    _sale(app, w, "seller", position=3, amount="50.00", status="PAID")                               # paid
    _sale(app, w, "seller", position=0, amount="0.00", counted=False)                                # stopped counting
    # a take-back of 5.00 against the first sale's row
    with app.app_context():
        from app.extensions import db_session

        sale_id = db_session.execute(text("SELECT ladder_sale_id FROM owner_commission_ledger_entries WHERE id = :i"), {"i": first}).scalar_one()
        conn = db_session.connection()
        _seed(conn, "owner_commission_ledger_entries", dict(w["cache"]), {
            "employee_profile_id": w["seller_profile"], "commission_amount": D("-5.00"), "currency": "JOD", "status": "REVERSED",
            "entry_kind": "LADDER", "ladder_month": JUNE, "ladder_sale_id": sale_id, "reversal_of_ledger_entry_id": str(first),
            "earned_at": datetime(2026, 6, 25, tzinfo=UTC)})
        db_session.commit()
        statement = statements.build_statement(w["seller_profile"], JUNE, now=amman(2026, 7, 3, 10).astimezone(UTC))
    totals = statement["totals"]
    assert (totals["held"], totals["payable"], totals["paid"], totals["clawed_back"], totals["net"]) == (
        "30.00", "20.00", "50.00", "5.00", "95.00")                                             # 30 + 20 + 50 - 5
    assert statement["sales_confirmed"] == 3                                                     # the uncounted one is not "confirmed"
    sale_lines = [line for line in statement["lines"] if line["kind"] == "SALE"]
    assert len(sale_lines) == 4 and [line["counted"] for line in sale_lines].count(False) == 1
    uncounted = next(line for line in sale_lines if not line["counted"])
    assert uncounted["uncount_cause"] == "REFUND_IN_HOLD"
    by_net = {line["net"]: line for line in sale_lines}
    assert by_net["15.00"]["clawed_back"] == "5.00" and by_net["15.00"]["position"] == 1     # 20 - 5 on the first sale
    # the same statement hashes the same twice (the objection stores the hash)
    assert statements.statement_sha256(statement) == statements.statement_sha256(dict(statement))


def test_an_open_month_has_no_statement_and_a_closed_one_lists_only_months_with_activity(app, w, clock):
    clock.set(amman(2026, 7, 3, 10))
    _sale(app, w, "seller", position=1, amount="20.00", payable_after=datetime(2026, 6, 20, tzinfo=UTC))
    _sale(app, w, "seller", position=1, amount="20.00", month=date(2026, 7, 1), qualified_at=datetime(2026, 7, 2, tzinfo=UTC))
    client = _client(app, w["seller"])
    index = client.get("/commissions/statements").get_data(as_text=True)
    assert 'data-statement-month="2026-06"' in index and 'data-statement-month="2026-07"' not in index
    assert client.get("/commissions/statements/2026-06").status_code == 200
    assert client.get("/commissions/statements/2026-07").status_code == 404      # July is still open
    assert client.get("/commissions/statements/2026-13").status_code == 404 and client.get("/commissions/statements/nope").status_code == 404


# ---------------------------------------------------------------- only your own

def test_a_seller_sees_only_their_own_statement_and_never_the_managers_queue(app, w, clock):
    clock.set(amman(2026, 7, 3, 10))
    past = datetime(2026, 6, 20, tzinfo=UTC)
    _sale(app, w, "seller", position=1, amount="20.00", payable_after=past)
    _sale(app, w, "other", position=1, amount="777.00", payable_after=past)
    mine = _client(app, w["seller"]).get("/commissions/statements/2026-06").get_data(as_text=True)
    theirs = _client(app, w["other"]).get("/commissions/statements/2026-06").get_data(as_text=True)
    assert 'data-line-net="20.00"' in mine and "777.00" not in mine
    assert 'data-line-net="777.00"' in theirs and 'data-line-net="20.00"' not in theirs
    # the TOTALS are theirs only too (a statement that summed everyone's ledger would show 797.00)
    assert 'data-total-payable="20.00"' in mine and 'data-total-net="20.00"' in mine and "797.00" not in mine
    assert 'data-total-payable="777.00"' in theirs and "797.00" not in theirs
    # the other seller's objection (and its reason) never reaches this seller's page, list or any route they can open
    _object(app, w, "other", JUNE, amman(2026, 7, 2).astimezone(UTC), reason="CONFIDENTIAL-REASON-OF-OTHER")
    client = _client(app, w["seller"])
    for path in ("/commissions/statements", "/commissions/statements/2026-06"):
        page = client.get(path).get_data(as_text=True)
        assert "CONFIDENTIAL-REASON-OF-OTHER" not in page and 'data-objection-status' not in page, path
    assert 'data-objection-status="OPEN"' in _client(app, w["other"]).get("/commissions/statements/2026-06").get_data(as_text=True)
    (their_row,) = _objections(app, w["other"])
    assert client.get(f"/commissions/objections/{their_row.id}").status_code == 403
    assert client.get("/commissions/objections").status_code == 403
    assert client.post(f"/commissions/objections/{their_row.id}/answer", data={"outcome": "ACCEPTED", "reply_text": "x"}).status_code in (400, 403)
    assert _objections(app, w["other"])[0].status == "OPEN"
    # a role without commission access sees neither the statement nor the queue
    assert _client(app, w["finance"]).get("/commissions/statements/2026-06").status_code == 403


# ---------------------------------------------------------------- the Object button

def test_the_object_button_is_there_for_seven_days_then_gone_and_the_post_is_refused(app, w, clock):
    _sale(app, w, "seller", position=1, amount="20.00", payable_after=datetime(2026, 6, 20, tzinfo=UTC))
    client = _client(app, w["seller"])
    clock.set(CLOSES.astimezone(UTC) - timedelta(seconds=1))
    inside = client.get("/commissions/statements/2026-06")
    assert 'data-object-button="1"' in inside.get_data(as_text=True)
    clock.set(CLOSES.astimezone(UTC))
    after = client.get("/commissions/statements/2026-06").get_data(as_text=True)
    assert 'data-object-button="1"' not in after and 'data-objection-closed="1"' in after
    token = get_csrf(inside.get_data(as_text=True))
    refused = client.post("/commissions/statements/2026-06/object", data={"csrf_token": token, "reason": REASON})
    assert refused.status_code == 302 and _objections(app) == []                  # nothing written
    # the allow twin: back inside the window the same POST records the objection and the snapshot
    clock.set(amman(2026, 7, 4, 9))
    assert client.post("/commissions/statements/2026-06/object", data={"csrf_token": token, "reason": REASON}).status_code == 302
    (row,) = _objections(app)
    assert row.reason == REASON and row.status == "OPEN" and row.statement_snapshot["totals"]["payable"] == "20.00"
    assert len(row.statement_sha256) == 64 and row.staff_user_id == w["seller"]
    page = client.get("/commissions/statements/2026-06").get_data(as_text=True)
    assert 'data-object-button="1"' not in page and 'data-objection-status="OPEN"' in page
    # a second objection to the same statement is refused and writes nothing
    again = client.post("/commissions/statements/2026-06/object", data={"csrf_token": token, "reason": "again"})
    assert again.status_code == 302 and len(_objections(app)) == 1


def test_an_objection_needs_a_written_reason(app, w, clock):
    clock.set(amman(2026, 7, 4, 9))
    assert _code(lambda: _object(app, w, "seller", JUNE, amman(2026, 7, 4, 9).astimezone(UTC), reason="   ")) == \
        "STATEMENT_OBJECTION_REASON_REQUIRED"
    assert _objections(app) == []


# ---------------------------------------------------------------- the reply

def _answer_via_route(app, client, row_id, **form):
    page = client.get(f"/commissions/objections/{row_id}")
    token = get_csrf(page.get_data(as_text=True))
    return client.post(f"/commissions/objections/{row_id}/answer", data={"csrf_token": token, **form})


def test_a_manager_answers_and_the_seller_reads_the_reply(app, w, clock):
    clock.set(amman(2026, 7, 2, 10))
    row = _object(app, w, "seller", JUNE, amman(2026, 7, 2, 10).astimezone(UTC))
    manager = _client(app, w["manager"])
    queue = manager.get("/commissions/objections")
    assert queue.status_code == 200 and f'data-objection-id="{row.id}"' in queue.get_data(as_text=True)
    # refused: blank reply, unknown outcome -- the objection stays OPEN
    assert _answer_via_route(app, manager, row.id, outcome="ACCEPTED", reply_text="  ").status_code == 302
    assert _answer_via_route(app, manager, row.id, outcome="MAYBE", reply_text="Looked at it").status_code == 302
    assert _objections(app)[0].status == "OPEN"
    # allowed: a written reply with an outcome
    clock.set(amman(2026, 7, 5, 10))
    assert _answer_via_route(app, manager, row.id, outcome="REJECTED", reply_text="The ladder step is 30 for the 2nd sale.").status_code == 302
    (done,) = _objections(app)
    assert done.status == "REJECTED" and done.answered_by_staff_user_id == w["manager"] and done.reply_text.startswith("The ladder")
    assert done.answered_at == amman(2026, 7, 5, 10).astimezone(UTC)
    seller_page = _client(app, w["seller"]).get("/commissions/statements/2026-06").get_data(as_text=True)
    assert 'data-objection-status="REJECTED"' in seller_page and "The ladder step is 30 for the 2nd sale." in seller_page
    # an answered objection cannot be answered again, by the same or another manager
    assert _answer_via_route(app, _client(app, w["manager2"]), row.id, outcome="ACCEPTED", reply_text="changed my mind").status_code == 302
    assert _objections(app)[0].status == "REJECTED" and _objections(app)[0].reply_text.startswith("The ladder")


def test_accepting_is_recorded_and_the_super_admin_can_answer_too(app, w, clock):
    clock.set(amman(2026, 7, 2, 10))
    row = _object(app, w, "seller", JUNE, amman(2026, 7, 2, 10).astimezone(UTC))
    assert _answer_via_route(app, _client(app, w["admin"]), row.id, outcome="ACCEPTED", reply_text="You are right; we will correct it.").status_code == 302
    (done,) = _objections(app)
    assert done.status == "ACCEPTED" and done.answered_by_staff_user_id == w["admin"]


def test_nobody_answers_their_own_objection(app, w, clock):
    """A manager who also sells (SALES + SALES_MANAGER) objects to their own statement: they
    cannot answer it -- the service refuses, the page offers no form, and the database agrees."""
    from app.commissions import statements

    clock.set(amman(2026, 7, 2, 10))
    row = _object(app, w, "both", JUNE, amman(2026, 7, 2, 10).astimezone(UTC))
    own_client = _client(app, w["both"])
    page = own_client.get(f"/commissions/objections/{row.id}").get_data(as_text=True)
    assert 'data-self-answer="1"' in page and 'name="reply_text"' not in page
    assert _answer_via_route(app, own_client, row.id, outcome="ACCEPTED", reply_text="I agree with myself").status_code in (302, 400)
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        assert _code(lambda: statements.answer_objection(row.id, answerer=db_session.get(StaffUser, w["both"]),
                                                         outcome="ACCEPTED", reply_text="me")) == \
            "STATEMENT_OBJECTION_SELF_ANSWER_FORBIDDEN"
    assert _objections(app)[0].status == "OPEN"
    # the allow twin: another manager answers it
    assert _answer_via_route(app, _client(app, w["manager"]), row.id, outcome="ACCEPTED", reply_text="Agreed.").status_code == 302
    assert _objections(app)[0].status == "ACCEPTED"


# ---------------------------------------------------------------- overdue

def _card(dashboard_html: str) -> str:
    """Just the objections card (it holds the two tiles; they close before the first </div>)."""
    return dashboard_html.split('data-objections-card="1"')[1].split("</div>")[0]


def test_an_open_objection_past_its_deadline_is_flagged_in_the_queue_and_on_the_dashboard(app, w, clock):
    clock.set(amman(2026, 7, 2, 10))                                  # Thursday
    row = _object(app, w, "seller", JUNE, amman(2026, 7, 2, 10).astimezone(UTC))   # due end of Monday 6 July = 00:00 on the 7th
    manager = _client(app, w["manager"])

    def flags():
        queue = manager.get("/commissions/objections").get_data(as_text=True)
        dash = manager.get("/").get_data(as_text=True)
        return 'data-objection-overdue="1"' in queue, dash

    clock.set(amman(2026, 7, 6, 23, 59, 59))                          # Monday night: still inside
    queued_overdue, dash = flags()
    assert not queued_overdue and 'data-objections-card="1"' in dash and "aura-stat-tile--danger" not in _card(dash)
    clock.set(amman(2026, 7, 7, 0, 0, 1))                             # one second past the deadline
    queued_overdue, dash = flags()
    assert queued_overdue and "aura-stat-tile--danger" in _card(dash)
    from app.commissions import statements

    with app.app_context():
        assert statements.open_and_overdue_counts(now=amman(2026, 7, 7, 0, 0, 1).astimezone(UTC)) == (1, 1)
        assert statements.open_and_overdue_counts(now=amman(2026, 7, 6, 23, 59, 59).astimezone(UTC)) == (1, 0)
    # answering clears the flag
    assert _answer_via_route(app, manager, row.id, outcome="ACCEPTED", reply_text="Done.").status_code == 302
    queued_overdue, dash = flags()
    assert not queued_overdue
    with app.app_context():
        assert statements.open_and_overdue_counts(now=amman(2026, 7, 7).astimezone(UTC)) == (0, 0)
    assert "aura-stat-tile--danger" not in _card(dash)


def test_the_dashboard_card_is_only_for_those_who_may_answer(app, w, clock):
    clock.set(amman(2026, 7, 2, 10))
    for who, shown in (("manager", True), ("admin", True), ("seller", False), ("finance", False)):
        page = _client(app, w[who]).get("/").get_data(as_text=True)
        assert ('data-objections-card="1"' in page) is shown, who


def test_the_permission_is_on_the_sales_manager_and_not_on_the_seller_role(app, w):
    from app.security.rbac import get_staff_permission_codes

    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        codes = {who: get_staff_permission_codes(db_session.get(StaffUser, w[who])) for who in ("manager", "seller", "finance", "admin")}
    assert "commission_objections.answer" in codes["manager"] and "commission_objections.answer" in codes["admin"]
    assert "commission_objections.answer" not in codes["seller"] and "commission_objections.answer" not in codes["finance"]


def test_statement_pages_are_behind_the_seller_agreement_gate(app, w, clock):
    from tests.test_seller_agreement_gate import _gated, _publish_seller_version

    clock.set(amman(2026, 7, 3, 10))
    _publish_seller_version(app, w["admin"])
    client = _client(app, w["seller"])
    for path in ("/commissions/statements", "/commissions/statements/2026-06", "/commissions/objections"):
        assert _gated(client.get(path)), path
    app.config.update(WTF_CSRF_ENABLED=False)  # the question is the gate, not the CSRF token
    assert _gated(client.post("/commissions/statements/2026-06/object", data={"reason": REASON}))
    assert _objections(app) == []
