"""Aura Retail -- the figures the AI analysis is allowed to see.

`build_snapshot()` condenses this shop's last 30 days (against the 30 before)
into one small JSON-able dict. It is the ONLY thing that leaves the till for
the AI path (till -> Owner -> AI provider), so what it contains is a privacy
decision, not a convenience one:

  R6 -- CUSTOMER NAMES NEVER LEAVE THE TILL. Customers appear only as
  aggregates (counts, shares) and as anonymous labels C1..C10 with spend and
  visit count. There is no name, phone, email, address, id or loyalty figure
  of any customer in here. This is enforced STRUCTURALLY: no query in this
  module reads `customers.name/phone/email/address` at all, and the top-
  customer query groups on `sales.customer_id` without joining `customers`.
  A reviewer should be able to confirm R6 by grepping this file. Product
  names DO leave -- the advice ("push X, reorder Y") needs them.

THE CLOCK. Every window is built from `metrics.business_now(conn, cid, now)`
and the `metrics.period_*` constructors, never from a bare datetime.now():
the shop's trading day, not the reporting device's wall clock, decides which
day a sale belongs to (see metrics.py #5 and retail_report_clock_agreement_
test.py for the shipped defect this avoids). `now` may be injected by a test;
when omitted the device clock is read ONCE here and converted immediately.

SCOPE. Every query is company-scoped, and branch-scoped when `branch_id` is
given (the caller is expected to have run it through the route layer's
`_coerce_branch_id_for_scope`, as every report route does).

SIZE. Owner refuses a body over 60 KB; this snapshot is kept under 40 KB by
construction (every list is capped) and, as a backstop, `_fit()` trims the
tail of the longest lists until the serialized JSON fits. A snapshot that is
too big must degrade to "fewer rows", never to an HTTP 400 the shopkeeper
cannot act on.

MONEY. Rounded with the shop currency's own quantum through
`metrics._money` (JOD has 3 dp), the same helper every report uses, so a
figure here equals the figure on the Reports screen.
"""
import json
from datetime import date, datetime

from core.retail import metrics
from core.retail import pricing

#: Hard ceiling on the serialized snapshot. Owner's body limit is 60 KB and the
#: signed envelope plus a chat question/history ride on top, so stay well under.
SNAPSHOT_MAX_BYTES = 40 * 1024

#: Window lengths, in days. `period_days(n)` is inclusive of both ends, so the
#: 30-day window is period_days(29).
WINDOW_DAYS = 30
PAIRS_WINDOW_DAYS = 90

TOP_PRODUCTS_LIMIT = 15
SLOW_MOVERS_LIMIT = 15
LOW_STOCK_LIMIT = 15
TOP_CUSTOMERS_LIMIT = 10
PAIRS_LIMIT = 10

#: Product names are shop-typed free text; bound them so one absurd name
#: cannot eat the size budget.
_NAME_MAX = 80

_WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')


def _name(value):
    return str(value or '')[:_NAME_MAX]


def _currency(conn, cid):
    """The shop's base currency. Same default-on-missing contract as
    api.retail_api._company_currency (which this module cannot import without
    a cycle): a raw SELECT, and the product default when no row exists."""
    try:
        row = conn.execute(
            "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='base_currency'", (cid,)
        ).fetchone()
    except Exception:  # noqa: BLE001 -- settings table not there yet: default
        row = None
    value = (row[0] if row else None) or ''
    return str(value).strip().upper() or pricing.DEFAULT_BASE_CURRENCY


def _branch_clause(column, branch_id):
    if branch_id in (None, ''):
        return '', []
    return f' AND {column}=?', [branch_id]


def _weekday_revenue(day_rows, currency):
    """Revenue by weekday, from the per-day rows already computed (so the two
    views cannot disagree). Keyed by the business DAY string's weekday."""
    totals = {name: 0.0 for name in _WEEKDAYS}
    for row in day_rows:
        try:
            wd = date.fromisoformat(row['day']).weekday()
        except (ValueError, TypeError):
            continue
        totals[_WEEKDAYS[wd]] += row['revenue']
    return {k: metrics._money(v, currency) for k, v in totals.items()}


def _slow_movers(conn, cid, period, boundary, branch_id, currency):
    """In stock but selling little or nothing: ascending units sold over the
    window, then most stock first. `units` is gross units rung (returns are
    not netted: a slow mover is about demand at the till)."""
    scope_sql, scope_params = metrics._scope(cid, period, boundary, branch_id, alias='s')
    bal_sql, bal_params = _branch_clause('branch_id', branch_id)
    rows = conn.execute(f"""
        SELECT p.name AS name, b.qty AS on_hand, COALESCE(sold.units, 0) AS units
        FROM products p
        JOIN (SELECT product_id, SUM(quantity_on_hand) AS qty
              FROM inventory_balances WHERE company_id=?{bal_sql} GROUP BY product_id) b
             ON b.product_id = p.id
        LEFT JOIN (SELECT si.product_id AS pid, SUM(si.quantity) AS units
                   FROM sale_items si JOIN sales s ON s.id = si.sale_id
                   WHERE {scope_sql} GROUP BY si.product_id) sold ON sold.pid = p.id
        WHERE p.company_id=? AND p.status='active' AND p.deleted_at_utc IS NULL AND b.qty > 0
        ORDER BY units ASC, b.qty DESC, p.id ASC
        LIMIT ?
    """, [cid] + bal_params + scope_params + [cid, SLOW_MOVERS_LIMIT]).fetchall()
    return [{'name': _name(r['name']), 'on_hand': round(float(r['on_hand']), 2),
             'units_sold_30d': round(float(r['units']), 2)} for r in rows]


def _low_stock(conn, cid, branch_id):
    bal_sql, bal_params = _branch_clause('branch_id', branch_id)
    rows = conn.execute(f"""
        SELECT p.name AS name, COALESCE(b.qty, 0) AS on_hand, p.reorder_level AS reorder
        FROM products p
        LEFT JOIN (SELECT product_id, SUM(quantity_on_hand) AS qty
                   FROM inventory_balances WHERE company_id=?{bal_sql} GROUP BY product_id) b
             ON b.product_id = p.id
        WHERE p.company_id=? AND p.status='active' AND p.deleted_at_utc IS NULL
              AND COALESCE(b.qty, 0) <= p.reorder_level
        ORDER BY COALESCE(b.qty, 0) ASC, p.id ASC
        LIMIT ?
    """, [cid] + bal_params + [cid, LOW_STOCK_LIMIT]).fetchall()
    return [{'name': _name(r['name']), 'on_hand': round(float(r['on_hand']), 2),
             'reorder_level': round(float(r['reorder'] or 0), 2)} for r in rows]


def _customers(conn, cid, period, boundary, branch_id, currency):
    """Aggregates + anonymous top-10. NOTHING here reads a customers column
    other than counting rows and `created_at`; the top list groups on the
    sale's own customer_id and is relabelled C1..C10 in rank order."""
    scope_sql, scope_params = metrics._scope(cid, period, boundary, branch_id, alias='s')
    total = conn.execute("SELECT COUNT(*) FROM customers WHERE company_id=?", (cid,)).fetchone()[0] or 0
    # Customers created inside the window. created_at is the stored local
    # timestamp; comparing its date part to the business-date window is within
    # a trading-day edge of exact, which is fine for a "how many new" figure.
    new = conn.execute(
        "SELECT COUNT(*) FROM customers WHERE company_id=? AND date(created_at) >= ? AND date(created_at) <= ?",
        (cid, period.start, period.end)).fetchone()[0] or 0
    per_customer = conn.execute(f"""
        SELECT s.customer_id AS cid, COUNT(*) AS visits, COALESCE(SUM(s.total), 0) AS spend
        FROM sales s
        WHERE {scope_sql} AND s.customer_id IS NOT NULL AND s.customer_id != 0
        GROUP BY s.customer_id
        ORDER BY spend DESC, visits DESC, s.customer_id ASC
    """, scope_params).fetchall()
    active = len(per_customer)
    repeaters = sum(1 for r in per_customer if r['visits'] >= 2)
    top = [{'label': f'C{i}', 'spend': metrics._money(r['spend'], currency), 'visits': int(r['visits'])}
           for i, r in enumerate(per_customer[:TOP_CUSTOMERS_LIMIT], start=1)]
    return {
        'total': int(total),
        'active_30d': active,
        'new_30d': int(new),
        'repeat_buyer_share_pct': round(repeaters / active * 100, 1) if active else 0,
        'top': top,
    }


def _frequent_pairs(conn, cid, period, boundary, branch_id):
    """Product pairs bought in the same sale. a.product_id < b.product_id
    counts each unordered pair once; bounded by LIMIT and by the window."""
    scope_sql, scope_params = metrics._scope(cid, period, boundary, branch_id, alias='s')
    rows = conn.execute(f"""
        SELECT p1.name AS a, p2.name AS b, COUNT(DISTINCT s.id) AS n
        FROM sale_items x
        JOIN sale_items y ON y.sale_id = x.sale_id AND x.product_id < y.product_id
        JOIN sales s ON s.id = x.sale_id
        JOIN products p1 ON p1.id = x.product_id
        JOIN products p2 ON p2.id = y.product_id
        WHERE {scope_sql}
        GROUP BY x.product_id, y.product_id
        ORDER BY n DESC, x.product_id ASC, y.product_id ASC
        LIMIT ?
    """, scope_params + [PAIRS_LIMIT]).fetchall()
    return [{'a': _name(r['a']), 'b': _name(r['b']), 'sales_together': int(r['n'])} for r in rows]


def _discounts(conn, cid, period, boundary, branch_id, currency):
    scope_sql, scope_params = metrics._scope(cid, period, boundary, branch_id)
    row = conn.execute(
        f"SELECT COALESCE(SUM(discount_amount), 0) FROM sales WHERE {scope_sql}", scope_params).fetchone()
    return metrics._money(row[0], currency)


def _returns_count(conn, cid, period, boundary, branch_id):
    scope_sql, scope_params = metrics._scope(cid, period, boundary, branch_id)
    rows = metrics._returns_query(
        conn, f"SELECT COUNT(*) FROM returns WHERE {scope_sql}", scope_params)
    return int(rows[0][0]) if rows else 0


#: Lists trimmed (tail first) when the snapshot is over budget, in the order
#: they are given up: least decision-critical first. Paths are (key, subkey).
_TRIM_ORDER = (
    ('frequent_pairs', None),
    ('slow_movers', None),
    ('low_stock', None),
    ('customers', 'top'),
    ('top_products', None),
    ('revenue_by_day', None),
)


def _size(snapshot):
    return len(json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def _fit(snapshot, max_bytes):
    """Trim the tail of the lists, one row at a time, until the serialized
    snapshot fits. `revenue_by_day` is trimmed from the OLDEST end (head) since
    the recent days matter most; every other list is already ranked best-first
    so its tail is the least valuable."""
    for key, sub in _TRIM_ORDER:
        target = snapshot[key] if sub is None else snapshot[key][sub]
        while target and _size(snapshot) > max_bytes:
            if key == 'revenue_by_day':
                target.pop(0)
            else:
                target.pop()
        if _size(snapshot) <= max_bytes:
            break
    return snapshot


def build_snapshot(conn, cid, branch_id=None, now=None, max_bytes=SNAPSHOT_MAX_BYTES):
    """The shop's analysis input. See the module docstring for the privacy,
    clock, scope and size rules. Returns a plain dict of JSON-able values."""
    if now is None:
        # The one device-clock read; converted onto the shop's business clock
        # on the very next line, exactly as dashboard_stats does.
        now = datetime.now()
    bnow = metrics.business_now(conn, cid, now)
    currency = _currency(conn, cid)
    boundary = metrics.business_day(conn, cid)

    cur = metrics.period_days(WINDOW_DAYS - 1, bnow)
    prev = metrics.preceding_period(cur)
    pairs_period = metrics.period_days(PAIRS_WINDOW_DAYS - 1, bnow)

    summary = metrics.summary(conn, cid, cur, branch_id, currency)
    summary.pop('period_days', None)
    day_rows = metrics.revenue_by_day(conn, cid, cur, branch_id, currency)
    hour_rows = metrics.revenue_by_hour(conn, cid, cur, branch_id, currency)
    refunds_total = metrics.refunds(conn, cid, cur, branch_id, currency)
    gross = metrics.gross_sales(conn, cid, cur, branch_id, currency)

    snapshot = {
        'currency': currency,
        'as_of': bnow.strftime('%Y-%m-%d'),
        'window': {'start': cur.start, 'end': cur.end, 'days': WINDOW_DAYS,
                   'previous_start': prev.start, 'previous_end': prev.end},
        'scope': 'branch' if branch_id not in (None, '') else 'all_branches',
        'summary': summary,
        'revenue_by_day': [{'day': r['day'], 'revenue': r['revenue'], 'transactions': r['transactions']}
                           for r in day_rows],
        'revenue_by_weekday': _weekday_revenue(day_rows, currency),
        'revenue_by_hour': dict(sorted(hour_rows.items())),
        'payment_mix': [{'method': _name(r['payment_method']), 'count': r['count'], 'revenue': r['revenue']}
                        for r in metrics.revenue_by_payment_method(conn, cid, cur, branch_id, currency)],
        'top_products': [{'name': _name(r['name']), 'units': r['units_sold'],
                          'revenue': r['revenue'], 'profit': r['profit']}
                         for r in metrics.top_products(conn, cid, cur, branch_id, TOP_PRODUCTS_LIMIT, currency)],
        'slow_movers': _slow_movers(conn, cid, cur, boundary, branch_id, currency),
        'low_stock': _low_stock(conn, cid, branch_id),
        'refunds': {
            'amount': refunds_total,
            'count': _returns_count(conn, cid, cur, boundary, branch_id),
            'returns_rate_pct': round(refunds_total / gross * 100, 1) if gross > 0 else 0,
        },
        'discounts_total': _discounts(conn, cid, cur, boundary, branch_id, currency),
        'customers': _customers(conn, cid, cur, boundary, branch_id, currency),
        'frequent_pairs': _frequent_pairs(conn, cid, pairs_period, boundary, branch_id),
    }
    return _fit(snapshot, max_bytes)
