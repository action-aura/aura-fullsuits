"""AI spending caps (app/ai/budget.py): refuse AT the cap, allow BELOW it,
count per licence / per staff / per purpose, never eat budget on a refusal."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.ai import budget
from app.ai.providers import AIError
from app.extensions import db_session
from app.models.base import utcnow


def _count(prefix_like: str) -> int:
    rows = db_session.execute(
        text("SELECT COALESCE(SUM(count), 0) FROM owner_rate_limit_counters WHERE bucket_key LIKE :p"), {"p": prefix_like}
    ).scalar_one()
    return int(rows)


def test_shop_cap_allows_below_and_refuses_at_the_cap(app):
    cfg = {"AI_SHOP_MONTHLY_CAP": "2", "AI_DAILY_CAP": "50"}
    lic = uuid.uuid4()
    with app.app_context():
        assert budget.reserve_shop_analysis(cfg, lic) == {"used": 1, "cap": 2}
        assert budget.reserve_shop_analysis(cfg, lic) == {"used": 2, "cap": 2}
        with pytest.raises(AIError) as exc:
            budget.reserve_shop_analysis(cfg, lic)
        assert exc.value.code == "AI_BUDGET_EXHAUSTED"
        assert _count(f"ai:shop:{lic}:%") == 2, "the refused request must not have counted"


def test_caps_are_per_licence(app):
    cfg = {"AI_SHOP_MONTHLY_CAP": "1", "AI_DAILY_CAP": "50"}
    a, b = uuid.uuid4(), uuid.uuid4()
    with app.app_context():
        budget.reserve_shop_analysis(cfg, a)
        budget.reserve_shop_analysis(cfg, b)  # b has its own allowance
        with pytest.raises(AIError):
            budget.reserve_shop_analysis(cfg, a)


def test_refusal_on_the_monthly_cap_does_not_eat_the_daily_budget(app):
    cfg = {"AI_SHOP_MONTHLY_CAP": "1", "AI_DAILY_CAP": "50"}
    lic = uuid.uuid4()
    with app.app_context():
        budget.reserve_shop_analysis(cfg, lic)
        for _ in range(3):
            with pytest.raises(AIError):
                budget.reserve_shop_analysis(cfg, lic)
        assert _count("ai:day:shop_analysis:%") == 1, "refused requests consumed the server-wide daily allowance"


def test_daily_server_cap_refuses_across_licences(app):
    cfg = {"AI_SHOP_MONTHLY_CAP": "10", "AI_DAILY_CAP": "2"}
    with app.app_context():
        budget.reserve_shop_analysis(cfg, uuid.uuid4())
        budget.reserve_shop_analysis(cfg, uuid.uuid4())
        third = uuid.uuid4()
        with pytest.raises(AIError) as exc:
            budget.reserve_shop_analysis(cfg, third)
        assert exc.value.code == "AI_BUDGET_EXHAUSTED"
        assert _count(f"ai:shop:{third}:%") == 0, "the licence's monthly slot must be rolled back when the daily cap refuses"


def test_staff_help_cap_is_per_staff_member(app):
    cfg = {"AI_HELP_DAILY_CAP_PER_STAFF": "2", "AI_DAILY_CAP": "50"}
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    with app.app_context():
        budget.reserve_staff_help(cfg, s1)
        budget.reserve_staff_help(cfg, s1)
        with pytest.raises(AIError) as exc:
            budget.reserve_staff_help(cfg, s1)
        assert exc.value.code == "AI_BUDGET_EXHAUSTED"
        budget.reserve_staff_help(cfg, s2)  # someone else is unaffected


def test_purpose_daily_cap_for_leads(app):
    cfg = {"AI_DAILY_CAP": "1"}
    with app.app_context():
        budget.reserve_purpose_day(cfg, "leads")
        with pytest.raises(AIError) as exc:
            budget.reserve_purpose_day(cfg, "leads")
        assert exc.value.code == "AI_BUDGET_EXHAUSTED"
        budget.reserve_staff_help({"AI_DAILY_CAP": "1", "AI_HELP_DAILY_CAP_PER_STAFF": "5"}, uuid.uuid4())  # other purposes keep their own bucket


@pytest.mark.parametrize("bad", ["0", "-3", "abc", "2.5"])
def test_invalid_or_zero_cap_switches_the_feature_off_never_unlimited(app, bad):
    with app.app_context():
        with pytest.raises(AIError) as exc:
            budget.reserve_shop_analysis({"AI_SHOP_MONTHLY_CAP": bad}, uuid.uuid4())
        assert exc.value.code == "AI_NOT_CONFIGURED"


def test_unset_cap_means_the_default_not_unlimited(app):
    with app.app_context():
        assert budget.reserve_shop_analysis({}, uuid.uuid4())["cap"] == budget.DEFAULT_SHOP_MONTHLY_CAP == 120


def test_window_start_is_the_period_end_so_housekeeping_cannot_reset_the_month(app):
    cfg = {}
    lic = uuid.uuid4()
    with app.app_context():
        budget.reserve_shop_analysis(cfg, lic)
        month_row = db_session.execute(
            text("SELECT window_start FROM owner_rate_limit_counters WHERE bucket_key LIKE :p"), {"p": f"ai:shop:{lic}:%"}
        ).scalar_one()
        # The nightly purge deletes rows whose window_start is >1 day old: a
        # month counter stamped with the month's START would be gone by the 2nd.
        assert month_row > utcnow()
        assert month_row <= utcnow() + timedelta(days=32)
        # Simulate the purge; the live month's counter must survive it.
        db_session.execute(text("DELETE FROM owner_rate_limit_counters WHERE window_start < :cut"), {"cut": utcnow() - timedelta(days=1)})
        db_session.commit()
        assert _count(f"ai:shop:{lic}:%") == 1


def test_a_new_month_starts_a_new_counter(app):
    cfg = {"AI_SHOP_MONTHLY_CAP": "1", "AI_DAILY_CAP": "50"}
    lic = uuid.uuid4()
    now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    with app.app_context():
        budget.reserve_shop_analysis(cfg, lic, now=now)
        with pytest.raises(AIError):
            budget.reserve_shop_analysis(cfg, lic, now=now + timedelta(hours=1))
        assert budget.reserve_shop_analysis(cfg, lic, now=now + timedelta(days=5))["used"] == 1  # November
