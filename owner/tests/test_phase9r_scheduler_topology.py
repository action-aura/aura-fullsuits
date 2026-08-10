"""Phase 9R M5 -- scheduler topology: exactly one designated process may run
scheduled report-snapshot generation. Real gap found and fixed this
milestone: app/operational_reports/scheduler.py's own module docstring
claimed "CLI-triggerable," but no such CLI command existed -- the
"SCHEDULER" value in REPORT_GENERATED_BY was structurally unreachable."""
from __future__ import annotations

from datetime import date

import pytest

from app.models.report_snapshots import ReportSnapshot
from app.scheduling import SchedulerNotOwnerError, require_scheduler_owner


class TestSchedulerOwnershipGate:
    def test_worker_role_refuses(self):
        with pytest.raises(SchedulerNotOwnerError, match="OWNER_SCHEDULER_ROLE"):
            require_scheduler_owner({"SCHEDULER_ROLE": "worker"})

    def test_missing_role_defaults_to_worker_and_refuses(self):
        with pytest.raises(SchedulerNotOwnerError):
            require_scheduler_owner({})

    def test_owner_role_allows(self):
        require_scheduler_owner({"SCHEDULER_ROLE": "owner"})  # must not raise


class TestGenerateScheduledReportsCLI:
    def test_refuses_on_a_worker_process(self, app):
        app.config["SCHEDULER_ROLE"] = "worker"
        runner = app.test_cli_runner()
        result = runner.invoke(args=["reports", "generate-scheduled", "--period-type", "daily", "--currency", "USD"])
        assert result.exit_code != 0
        assert "OWNER_SCHEDULER_ROLE" in result.output

    def test_generates_daily_snapshots_on_the_owner_process(self, app, seeded):
        app.config["SCHEDULER_ROLE"] = "owner"
        runner = app.test_cli_runner()
        result = runner.invoke(
            args=["reports", "generate-scheduled", "--period-type", "daily", "--currency", "USD", "--as-of", "2026-08-05"]
        )
        assert result.exit_code == 0, result.output
        rows = ReportSnapshot.query.filter_by(report_type="DAILY_OPERATIONAL_SUMMARY", period_start=date(2026, 8, 4)).all()
        assert len(rows) == 1
        assert rows[0].generated_by == "SCHEDULER"

    def test_daily_run_is_idempotent(self, app, seeded):
        app.config["SCHEDULER_ROLE"] = "owner"
        runner = app.test_cli_runner()
        first = runner.invoke(
            args=["reports", "generate-scheduled", "--period-type", "daily", "--currency", "USD", "--as-of", "2026-08-05"]
        )
        second = runner.invoke(
            args=["reports", "generate-scheduled", "--period-type", "daily", "--currency", "USD", "--as-of", "2026-08-05"]
        )
        assert first.exit_code == 0 and second.exit_code == 0
        rows = ReportSnapshot.query.filter_by(report_type="DAILY_OPERATIONAL_SUMMARY", period_start=date(2026, 8, 4)).all()
        assert len(rows) == 1  # not duplicated by the second run

    def test_daily_requires_currency(self, app):
        app.config["SCHEDULER_ROLE"] = "owner"
        runner = app.test_cli_runner()
        result = runner.invoke(args=["reports", "generate-scheduled", "--period-type", "daily"])
        assert result.exit_code != 0
        assert "--currency is required" in result.output

    def test_weekly_period_is_the_completed_monday_to_sunday_week(self, app, seeded):
        app.config["SCHEDULER_ROLE"] = "owner"
        runner = app.test_cli_runner()
        # 2026-08-10 is a Monday -- the week that just completed (ending
        # yesterday, Sunday 2026-08-09) is 2026-08-03 (Mon) .. 2026-08-09 (Sun).
        result = runner.invoke(
            args=["reports", "generate-scheduled", "--period-type", "weekly", "--currency", "USD", "--as-of", "2026-08-10"]
        )
        assert result.exit_code == 0, result.output
        rows = ReportSnapshot.query.filter_by(report_type="WEEKLY_OPERATIONAL_SUMMARY").all()
        assert len(rows) == 1
        assert rows[0].period_start == date(2026, 8, 3)
        assert rows[0].period_end == date(2026, 8, 9)

    def test_monthly_period_is_the_completed_calendar_month(self, app, seeded):
        app.config["SCHEDULER_ROLE"] = "owner"
        runner = app.test_cli_runner()
        result = runner.invoke(
            args=["reports", "generate-scheduled", "--period-type", "monthly", "--currency", "USD", "--as-of", "2026-08-01"]
        )
        assert result.exit_code == 0, result.output
        rows = ReportSnapshot.query.filter_by(report_type="MONTHLY_OPERATIONAL_SUMMARY").all()
        assert len(rows) == 1
        assert rows[0].period_start == date(2026, 7, 1)
        assert rows[0].period_end == date(2026, 7, 31)
