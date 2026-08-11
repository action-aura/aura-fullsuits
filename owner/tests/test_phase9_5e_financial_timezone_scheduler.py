"""Phase 9.5E Milestone 21 -- financial/timezone/scheduler concurrency
matrix. Expense-payment overpayment/reversal and cash-closing race proofs
already live in test_phase9_5e_expense_payments_attachments_duplicates.py
and test_phase9_5e_security_and_idor.py respectively (retained, not
duplicated here). This file covers what wasn't proven yet: Expense-number
concurrency, Asia/Amman day/week/month boundaries, and the scheduler's
advisory-lock idempotency under real concurrent workers, retry, and
versioned regeneration."""
from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from decimal import Decimal

from tests.conftest import make_staff


def test_expense_number_concurrent_allocation_no_duplicates(app, seeded):
    """Real concurrency proof for the EXPENSE document_type, on top of
    Phase 9.5D's own 12-thread proof of the underlying DocumentNumberCounter
    mechanism (quote-numbering-contract.md) -- this proves the specific
    EXPENSE prefix/call-site under real concurrent load, not just trusting
    the shared mechanism by reference."""
    from app import create_app

    results = []
    errors = []

    def worker():
        thread_app = create_app("testing")
        with thread_app.app_context():
            from app.expenses.numbering import allocate_expense_number
            from app.extensions import db_session as thread_db_session
            try:
                number = allocate_expense_number(as_of=date(2026, 8, 1))
                thread_db_session.commit()
                results.append(number)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))
            finally:
                thread_db_session.remove()

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"allocate_expense_number() must never raise under concurrency: {errors}"
    assert len(results) == 12
    assert len(set(results)) == 12, f"duplicate expense numbers allocated under concurrency: {results}"


# ------------------------------------------------------------- Timezone --

def test_previous_completed_business_day_crosses_utc_midnight_correctly():
    """Asia/Amman is UTC+3 -- a UTC timestamp just after UTC midnight is
    already the next Amman day, so 'previous completed day' must still
    resolve to yesterday in Amman terms, not two days back or the same day."""
    from app.operational_reports.periods import previous_completed_business_day

    # 2026-08-15 00:30 UTC = 2026-08-15 03:30 Asia/Amman -- previous
    # completed Amman day is 2026-08-14.
    now_utc = datetime(2026, 8, 15, 0, 30, tzinfo=timezone.utc)
    assert previous_completed_business_day(now_utc) == date(2026, 8, 14)

    # 2026-08-14 23:30 UTC = 2026-08-15 02:30 Asia/Amman -- previous
    # completed Amman day is still 2026-08-14 (today's Amman date already
    # rolled to the 15th).
    now_utc_2 = datetime(2026, 8, 14, 23, 30, tzinfo=timezone.utc)
    assert previous_completed_business_day(now_utc_2) == date(2026, 8, 14)


def test_previous_completed_week_is_monday_to_sunday():
    from app.operational_reports.periods import previous_completed_week

    # 2026-08-17 is a Monday (Amman-local) -- the previous completed week
    # is 2026-08-10 (Mon) to 2026-08-16 (Sun).
    now_utc = datetime(2026, 8, 17, 6, 0, tzinfo=timezone.utc)  # 09:00 Amman
    start, end = previous_completed_week(now_utc)
    assert start == date(2026, 8, 10)
    assert end == date(2026, 8, 16)
    assert start.weekday() == 0  # Monday
    assert end.weekday() == 6  # Sunday


def test_previous_completed_month_is_full_calendar_month():
    from app.operational_reports.periods import previous_completed_month

    now_utc = datetime(2026, 9, 3, 6, 0, tzinfo=timezone.utc)
    start, end = previous_completed_month(now_utc)
    assert start == date(2026, 8, 1)
    assert end == date(2026, 8, 31)

    # Cross-year boundary.
    now_utc_jan = datetime(2027, 1, 5, 6, 0, tzinfo=timezone.utc)
    start_jan, end_jan = previous_completed_month(now_utc_jan)
    assert start_jan == date(2026, 12, 1)
    assert end_jan == date(2026, 12, 31)


# ------------------------------------------------------------- Scheduler --

def test_scheduler_concurrent_workers_never_duplicate_a_snapshot(app, seeded):
    """Real multi-worker proof of the advisory-lock + unique-constraint
    idempotency claimed in scheduled-report-snapshot-contract.md."""
    from app import create_app

    results = []
    errors = []

    def worker():
        thread_app = create_app("testing")
        with thread_app.app_context():
            from app.operational_reports.scheduler import generate_snapshot
            from app.extensions import db_session as thread_db_session
            try:
                snapshot = generate_snapshot(
                    report_type="DAILY_OPERATIONAL_SUMMARY", period_start=date(2026, 8, 5), period_end=date(2026, 8, 5),
                    currency="USD", generated_by="SCHEDULER",
                )
                results.append(str(snapshot.id))
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))
            finally:
                thread_db_session.remove()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"generate_snapshot() must never raise under concurrent workers: {errors}"
    assert len(results) == 8
    assert len(set(results)) == 1, f"expected exactly one snapshot id across all concurrent workers, got {set(results)}"

    from app.extensions import db_session
    from app.models.report_snapshots import ReportSnapshot
    from sqlalchemy import select
    with app.app_context():
        rows = db_session.execute(
            select(ReportSnapshot).where(
                ReportSnapshot.report_type == "DAILY_OPERATIONAL_SUMMARY", ReportSnapshot.period_start == date(2026, 8, 5),
                ReportSnapshot.period_end == date(2026, 8, 5), ReportSnapshot.currency == "USD",
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "PUBLISHED"


def test_scheduler_retry_after_success_returns_same_published_snapshot(app, seeded):
    from app.operational_reports.scheduler import generate_snapshot

    with app.app_context():
        first = generate_snapshot(report_type="WEEKLY_OPERATIONAL_SUMMARY", period_start=date(2026, 8, 10), period_end=date(2026, 8, 16), currency="USD", generated_by="SCHEDULER")
        second = generate_snapshot(report_type="WEEKLY_OPERATIONAL_SUMMARY", period_start=date(2026, 8, 10), period_end=date(2026, 8, 16), currency="USD", generated_by="SCHEDULER")
        assert first.id == second.id
        assert first.snapshot_version == 1


def test_scheduler_regeneration_creates_new_version_and_never_overwrites_published(app, seeded):
    from app.operational_reports.scheduler import generate_snapshot, regenerate_snapshot
    from app.extensions import db_session
    from app.models.report_snapshots import ReportSnapshot

    staff = make_staff(app, "sched1@example.com", role_codes=["FINANCE"])
    with app.app_context():
        original = generate_snapshot(report_type="MONTHLY_OPERATIONAL_SUMMARY", period_start=date(2026, 7, 1), period_end=date(2026, 7, 31), currency="USD", generated_by="SCHEDULER")
        original_payload_snapshot = dict(original.payload)
        original_id = original.id

        regenerated = regenerate_snapshot(report_type="MONTHLY_OPERATIONAL_SUMMARY", period_start=date(2026, 7, 1), period_end=date(2026, 7, 31), currency="USD", actor_staff_user_id=staff, reason="correction")
        assert regenerated.snapshot_version == 2
        assert regenerated.status == "PUBLISHED"
        assert regenerated.id != original_id

        original_row = db_session.get(ReportSnapshot, original_id)
        assert original_row.status == "SUPERSEDED"
        assert original_row.superseded_by_snapshot_id == regenerated.id
        # The original row's own payload content is never mutated.
        assert dict(original_row.payload) == original_payload_snapshot
