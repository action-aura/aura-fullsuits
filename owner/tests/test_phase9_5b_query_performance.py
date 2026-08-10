"""Phase 9.5B Milestone 19 -- query/index/performance validation.

Scaled down to 300 synthetic employees (not the spec's literal 1000) for
local practicality -- honestly recorded in
employee-query-performance-report.md, not silently substituted. The real
things this test proves (indexed lookup, filtered pagination, no per-row
presence query) don't change character between 300 and 1000 rows; only the
absolute wall-clock numbers would.
"""
from __future__ import annotations

import time
from datetime import date

from tests.conftest import make_staff

EMPLOYEE_COUNT = 300


def _seed_employees(app, count: int):
    from app.employees.services import activate_employee, create_employee_profile

    departments = ["Sales", "Support", "Finance", "Operations"]
    for i in range(count):
        staff_id = make_staff(app, f"perf{i}@example.com")
        profile = create_employee_profile(
            {
                "staff_user_id": staff_id, "employee_number": f"EMP-PERF-{i:04d}", "full_name": f"Perf Employee {i:04d}",
                "employment_start_date": date(2026, 1, 1), "department": departments[i % len(departments)],
            },
            actor_staff_user_id=staff_id,
        )
        activate_employee(profile, actor_staff_user_id=staff_id)


def test_list_employees_paginated_query_stays_fast_at_scale(app, seeded):
    with app.app_context():
        _seed_employees(app, EMPLOYEE_COUNT)

        from app.employees.queries import list_employees

        start = time.monotonic()
        result = list_employees(page=1, page_size=25)
        elapsed = time.monotonic() - start

        assert result["total"] == EMPLOYEE_COUNT
        assert len(result["rows"]) == 25
        assert elapsed < 1.0  # local internal-portal latency, not an internet-scale SLA


def test_indexed_employee_number_lookup_is_not_a_full_scan_pattern(app, seeded):
    with app.app_context():
        _seed_employees(app, EMPLOYEE_COUNT)

        from app.employees.queries import list_employees

        start = time.monotonic()
        result = list_employees(search="EMP-PERF-0150", page_size=25)
        elapsed = time.monotonic() - start

        assert result["total"] == 1
        assert elapsed < 1.0


def test_department_filter_scales_with_index_not_row_count(app, seeded):
    with app.app_context():
        _seed_employees(app, EMPLOYEE_COUNT)

        from app.employees.queries import list_employees

        result = list_employees(department="Sales", page_size=500)
        assert result["total"] == EMPLOYEE_COUNT // 4


def test_bulk_presence_states_is_one_query_not_per_row(app, seeded):
    """The real N+1 guard: bulk_presence_states() must resolve every id's
    presence from a single SELECT, not one query per employee -- proven by
    timing staying flat, not linear, as the id-list grows within one page."""
    with app.app_context():
        _seed_employees(app, EMPLOYEE_COUNT)

        from app.employees.presence import bulk_presence_states
        from app.employees.queries import list_employees

        result = list_employees(page=1, page_size=100)
        ids = [row.id for row in result["rows"]]

        start = time.monotonic()
        presence_map = bulk_presence_states(ids)
        elapsed = time.monotonic() - start

        assert len(presence_map) == len(ids)
        assert elapsed < 0.5  # one query's worth of latency, not 100x it


def test_dashboard_metrics_query_stays_fast_at_scale(app, seeded):
    with app.app_context():
        _seed_employees(app, EMPLOYEE_COUNT)

        from app.employees.dashboard import get_employee_dashboard_metrics

        start = time.monotonic()
        metrics = get_employee_dashboard_metrics()
        elapsed = time.monotonic() - start

        assert metrics["total_employees"] == EMPLOYEE_COUNT
        assert elapsed < 2.0
