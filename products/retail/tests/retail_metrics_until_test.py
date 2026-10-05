"""Aura Today (TA step 2) -- metrics.revenue_until / revenue_until_by_branch.

"Yesterday by now" needs the net revenue of a day counted only up to a time of
day. The cutoff is on the SHOP'S BUSINESS CLOCK (the same shifted clock the
period predicate uses), and sales and returns take the identical predicate.

Each case builds its own company, so a day-start setting in one cannot leak
into another. Every expected figure is hand-computed in the case.

Run:
    pytest products/retail/tests/retail_metrics_until_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_until_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
                  AURA_SITE_RELAY_ENABLED="0")
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()

from core.retail import metrics  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

AMMAN_OFFSET_H = 3          # Asia/Amman is a fixed +03:00 since 2022


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _company(day_start=None):
    """A fresh company with two branches (A, B), zone Asia/Amman and,
    optionally, a trading-day start hour. Returns (conn, cid, a, b)."""
    cid = str(uuid.uuid4())
    conn = get_retail_conn()
    conn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'A')", (cid,))
    conn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'B')", (cid,))
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY name", (cid,)).fetchall()]
    conn.execute("INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                 (cid, 'business_timezone', 'Asia/Amman'))
    if day_start is not None:
        conn.execute("INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                     (cid, 'business_day_start_hour', str(day_start)))
    conn.commit()
    return conn, cid, ids[0], ids[1]


def _stamps(local):
    """(created_at wall clock, created_at_utc) for a shop-local 'YYYY-MM-DD HH:MM:SS'."""
    t = datetime.strptime(local, '%Y-%m-%d %H:%M:%S')
    return local, (t - timedelta(hours=AMMAN_OFFSET_H)).strftime('%Y-%m-%d %H:%M:%S')


def _sale(conn, cid, branch, local, total):
    ca, cu = _stamps(local)
    conn.execute(
        "INSERT INTO sales (company_id, sale_number, branch_id, subtotal, total, amount_paid, payment_method, "
        "status, created_at, created_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cid, f'S-{uuid.uuid4().hex[:8]}', branch, total, total, total, 'cash', 'completed', ca, cu))


def _return(conn, cid, branch, local, amount):
    ca, cu = _stamps(local)
    sid = conn.execute("SELECT id FROM sales WHERE company_id=? ORDER BY id LIMIT 1", (cid,)).fetchone()[0]
    conn.execute(
        "INSERT INTO returns (company_id, return_number, sale_id, branch_id, refund_amount, status, created_at, "
        "created_at_utc) VALUES (?,?,?,?,?,?,?,?)",
        (cid, f'R-{uuid.uuid4().hex[:8]}', sid, branch, amount, 'completed', ca, cu))


DAY = metrics.period_for_day('2026-10-05')


def test_cutoff_before_at_and_after_each_sale():
    conn, cid, a, _b = _company()
    _sale(conn, cid, a, '2026-10-05 09:00:00', 10.0)
    _sale(conn, cid, a, '2026-10-05 11:00:00', 20.0)
    _sale(conn, cid, a, '2026-10-05 15:00:00', 30.5)
    conn.commit()
    ru = lambda cut: metrics.revenue_until(conn, cid, DAY, cut, None, 'JOD')   # noqa: E731
    assert ru('08:59:59') == 0.0
    assert ru('09:00:00') == 10.0            # AT the sale's own second: inclusive
    assert ru('10:59:59') == 10.0
    assert ru('11:00:00') == 30.0
    assert ru('14:59:59') == 30.0
    assert ru('15:00:00') == 60.5
    conn.close()


def test_returns_are_netted_only_when_they_are_inside_the_cutoff():
    conn, cid, a, _b = _company()
    _sale(conn, cid, a, '2026-10-05 09:00:00', 10.0)
    _sale(conn, cid, a, '2026-10-05 11:00:00', 20.0)
    _return(conn, cid, a, '2026-10-05 12:00:00', 5.0)
    conn.commit()
    ru = lambda cut: metrics.revenue_until(conn, cid, DAY, cut, None, 'JOD')   # noqa: E731
    assert ru('11:59:59') == 30.0            # refund not yet rung: not netted
    assert ru('12:00:00') == 25.0            # refund at its own second: netted
    conn.close()


def test_end_of_day_cutoff_equals_the_whole_day_revenue():
    conn, cid, a, b = _company()
    _sale(conn, cid, a, '2026-10-05 00:00:00', 1.25)
    _sale(conn, cid, b, '2026-10-05 13:13:13', 7.5)
    _sale(conn, cid, a, '2026-10-05 23:59:59', 3.0)
    _return(conn, cid, b, '2026-10-05 20:00:00', 2.0)
    _sale(conn, cid, a, '2026-10-04 12:00:00', 99.0)          # another day: excluded
    conn.commit()
    whole = metrics.revenue(conn, cid, DAY, None, 'JOD')
    assert whole == 9.75
    assert metrics.revenue_until(conn, cid, DAY, '23:59:59', None, 'JOD') == whole
    conn.close()


def test_day_start_hour_cuts_on_the_business_clock_not_the_wall_clock():
    # Trading day starts 04:00: a sale at 01:30 on the 6th is the tail of the
    # 5th's trading day and sits at business time 21:30 on the 5th.
    conn, cid, a, _b = _company(day_start=4)
    _sale(conn, cid, a, '2026-10-05 10:00:00', 10.0)
    _sale(conn, cid, a, '2026-10-06 01:30:00', 40.0)
    conn.commit()
    ru = lambda cut: metrics.revenue_until(conn, cid, DAY, cut, None, 'JOD')   # noqa: E731
    assert ru('21:29:59') == 10.0
    assert ru('21:30:00') == 50.0
    # Had the cutoff been read on the wall clock, 21:29 would have admitted
    # the 01:30 row (01:30 < 21:29) and the line above would be 50.0.
    assert ru('23:59:59') == 50.0
    conn.close()


def test_by_branch_sums_to_the_shop_figure_and_respects_the_cutoff():
    conn, cid, a, b = _company()
    _sale(conn, cid, a, '2026-10-05 09:00:00', 10.0)
    _sale(conn, cid, b, '2026-10-05 10:00:00', 40.0)
    _sale(conn, cid, b, '2026-10-05 18:00:00', 5.0)
    _return(conn, cid, a, '2026-10-05 12:00:00', 4.0)
    conn.commit()
    by = metrics.revenue_until_by_branch(conn, cid, DAY, '13:00:00', 'JOD')
    assert by == {a: 6.0, b: 40.0}
    total = metrics.revenue_until(conn, cid, DAY, '13:00:00', None, 'JOD')
    assert total == 46.0 == sum(by.values())
    # branch filter on the scalar agrees with the grouped figure
    assert metrics.revenue_until(conn, cid, DAY, '13:00:00', b, 'JOD') == by[b]
    conn.close()
