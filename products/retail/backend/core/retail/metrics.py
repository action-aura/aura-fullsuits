"""
Aura Retail -- the single definition of every revenue/sales figure.

WHY THIS MODULE EXISTS
======================
Before this module, "revenue" was written out by hand roughly seven times
across api/retail_api.py, and the two corrections that mattered -- netting
refunds out, and scoping to a branch -- had each been retrofitted onto only
SOME of those copies. The result was screens that contradicted each other:

  * dashboard_stats() netted refunds out of the today_sales KPI card, but
    the hourly chart and the payment-method doughnut IN THE SAME RESPONSE
    were gross SUM(total). One refund today and the three widgets on one
    screen disagreed.
  * /reports/sales-trend and /reports/payment-methods never subtracted
    returns, while /reports/summary and /reports/by-branch did. The trend
    day-totals summed higher than the Revenue KPI printed beside them.
  * /reports/top-products had no date filter at all (all-time) while every
    sibling widget on the page was days-scoped, so one "top product" could
    report more revenue than the page's own 30-day total.
  * "average ticket" was AVG(total) (gross, per sale row) on the trend and
    by-branch routes but net-revenue/txns on the summary route.
  * The branch dropdown reached sales-trend and top-products only; summary,
    payment-methods and by-branch took no branch_id at all, so picking a
    branch moved the charts and left the KPI cards on all-branches.

Every one of those is the same defect: no single owner of the definition.
So this module owns them, once, and api/retail_api.py calls it instead of
re-deriving them. Raw sqlite3 on purpose -- this product has no ORM and is
not getting one; the goal here is FEWER lines of SQL in total, not a layer
stacked on top of the SQL that was already there.

THE CANONICAL ANSWERS
=====================
These are the decisions every caller now obeys. They are stated here so
there is exactly one place to argue with them.

1. REVENUE IS NET OF RETURNS -- ALWAYS.
   revenue = SUM(sales.total) - SUM(returns.refund_amount)
   A refund is real money leaving the till, and create_return() never edits
   the original sale (this codebase's "never edit/delete, only reverse"
   policy), so a raw SUM(sales.total) always overstates. `gross_sales` is
   still available separately for the rare screen that genuinely wants
   "money rung up before refunds", but nothing labelled "revenue" uses it.

   A refund is counted on the day IT was processed, not on the day of the
   sale it reverses. That matches how the money actually moves and how the
   cash drawer already reconciles (see _cash_expected in retail_api.py).
   A consequence to expect and not to "fix": a bucket (day, hour, payment
   method, product, branch) that saw only refunds reports NEGATIVE revenue.
   That is the truth, and it is what makes the buckets sum back to the KPI.

2. TRANSACTIONS ARE NOT NETTED.
   transactions = COUNT(sales rows). A refunded sale still happened; the
   count is "how many times did the till ring", not "how many sales
   survived". Returns are reported separately (`refunds`), never as a
   negative transaction.

3. AVERAGE TICKET = NET REVENUE / TRANSACTIONS.
   Never AVG(sales.total). AVG(total) is gross AND it silently ignores
   refunds, which is how the same page ended up printing two different
   "average ticket" numbers. One definition, derived from the two figures
   above, so it can never drift from them again.

4. COGS IS NET TOO, SO GROSS PROFIT IS COHERENT.
   cogs = cost of units sold - cost of units returned, and
   gross_profit = revenue - cogs.
   Netting only ONE side is the trap: with net revenue and gross COGS,
   gross profit is understated by the full cost of every returned unit, and
   the Reports summary card disagrees with the per-product profit column
   printed beside it. Returned goods are restocked by create_return(), so
   their cost genuinely was not consumed -- this is the accurate answer,
   not merely the self-consistent one.

5. THE PERIOD IS A CLOSED INTERVAL OF LOCAL CALENDAR DATES.
   Both ends are INCLUSIVE: start <= date(created_at) <= end.
   `period_days(n)` means "the last n days AND today" -- start = today - n,
   end = today -- which is n+1 distinct calendar dates. That is deliberately
   the same span every one of these routes already had (they all used
   `date(created_at) >= date('now','localtime','-n days')` with no upper
   bound), so the WINDOW itself moves no number that was already reviewed;
   the only difference is that an end bound now exists at all, excluding
   future-dated rows that were never legitimate anyway.

   (Numbers do move elsewhere in this consolidation, on purpose: the trend
   and payment-method charts now net refunds, top-products is windowed at
   all for the first time, COGS is net per #4, and summary's prior-period
   comparison window is now the same LENGTH as the current one -- it used
   to be one day shorter, which biased revenue_change upward.)

   Dates are LOCAL, not UTC. create_sale()/create_return() both write
   created_at as local wall-clock time explicitly for exactly this reason,
   so date(created_at) is already a local calendar date and no conversion
   belongs here.

6. THE BRANCH PREDICATE IS `branch_id = ?` ON BOTH SIDES.
   It applies to sales.branch_id AND returns.branch_id -- filtering the
   sales but not the refunds would let another branch's refund eat into
   this branch's revenue. `branch_id=None` (or '') means all branches and
   emits no predicate at all, so the unfiltered path stays byte-identical
   in shape to what it was.

   The one deliberate exception is revenue_by_branch(): a branch-comparison
   chart scoped to one branch is a contradiction, so that function takes no
   branch_id and always returns every active branch.

7. EVERY MONEY FIGURE IS TAX-INCLUSIVE -- THE AMOUNT THAT CHANGED HANDS.
   `sales.total` and `returns.refund_amount` are both written from
   pricing.calculate_line()'s `total`, i.e. taxable amount PLUS tax, and the
   cash drawer reconciles against those same two columns. So that is the one
   basis this module measures in, on every leg of every subtraction.

   Stating it is not pedantry -- it is the fix for a real defect this
   module introduced. `sale_items.line_total` and `return_items.line_total`
   are NOT the same quantity despite the identical column name:
   create_sale() writes sale_items.line_total from calc['taxable_amount']
   (tax EXCLUDED -- e-invoicing needs the taxable base there, see
   einvoice_adapter.build_document), while create_return() writes
   return_items.line_total from calc['total'] (tax INCLUDED). top_products()
   originally did SUM(sale_items.line_total) - SUM(return_items.line_total),
   which is a tax-exclusive base with a tax-inclusive refund subtracted from
   it: wrong by exactly the tax on every refund, and it made the Reports
   gross-profit card disagree with the per-product profit column beside it
   the moment tax_rate was anything but zero.

   So top_products() does NOT read sale_items.line_total. It re-derives each
   sale line's tax-inclusive total through pricing.calculate_line() -- the
   same function, over the same persisted inputs, that create_sale() used to
   produce sales.total in the first place, so the per-product revenues sum
   back to SUM(sales.total) exactly rather than approximately. (Re-deriving
   `line_total * (1 + tax_rate/100)` in SQL here would be both wrong under
   TAX_BEFORE_DISCOUNT and a direct violation of pricing.py's "this module is
   the only place these two formulas are allowed to be written out".)
   The refund leg needs no such treatment: return_items.line_total is
   already the tax-inclusive total.

   Reading the tax mode out of retail_settings is the same lookup, with the
   same defensive fallback, that einvoice_adapter.build_document() already
   does for the identical reason -- and it carries the same caveat: a company
   that switched tax_calculation_mode re-prices its OWN history, exactly as
   its e-invoices already do.

   CONSEQUENCE, stated out loud rather than buried: `gross_profit` and
   `margin_pct` are therefore computed from tax-INCLUSIVE revenue, so they
   include tax the business is only holding on the government's behalf.
   That is not new -- summary() has always been revenue(sales.total) minus
   cost -- and moving the whole product to a tax-exclusive revenue basis
   would move every KPI card, the cash drawer, and the e-invoice totals, so
   it is not something to slip into a consistency pass. What matters here is
   that ONE basis is now used on both sides of every subtraction; switching
   which basis that is, is a separate, deliberate change.

KNOWN GAP -- COST AT TIME OF SALE (follow-up, NOT fixed here)
=============================================================
`sale_items` captures quantity/unit_price/discount_pct/tax_rate/line_total
but NO cost snapshot (see database/schema.py's sale_items CREATE TABLE and
the v12 rebuild of it -- there is no cost column in either). So COGS and
gross profit below must join products.cost_price, i.e. the cost the product
has RIGHT NOW, not what it cost when it sold. Raising a cost price today
therefore retroactively shrinks last month's gross profit.

That is a real defect and it is flagged, not silently papered over. Fixing
it needs a `unit_cost` column on sale_items plus a PRAGMA user_version
migration and a write-side change in create_sale() -- deliberately out of
scope for this pass, which is about making the figures AGREE with each
other. Every cost/profit figure produced here is consistently wrong in the
same direction until that migration lands, which is strictly better than
today's mix of consistent-with-nothing.
"""
import logging
import sqlite3
from collections import namedtuple
from datetime import date, timedelta

from core.retail import pricing

log = logging.getLogger(__name__)

# start/end are inclusive 'YYYY-MM-DD' local calendar dates (see #5 above).
# `days` is carried purely so report payloads can echo back the window size
# the caller asked for; it is None for a period built from explicit dates.
Period = namedtuple('Period', 'start end days')


# ── Period constructors ───────────────────────────────────────────────────────
#
# Every constructor that needs the clock takes `now` as a REQUIRED argument.
# Nothing in this module calls now()/today() -- the only datetime names it
# imports are `date` and `timedelta`, for pure calendar arithmetic on dates
# it was handed. Two reasons, both of which have already bitten:
#
#   * One response often builds several Periods (dashboard_stats builds
#     today/yesterday/month-to-date plus a current-hour label). Letting each
#     call re-read the clock means a request that straddles midnight puts the
#     KPI card on one day and the chart beside it on another.
#   * The clock has to stay on the CALLER's `datetime`. A test freezes
#     api.retail_api.datetime; a second `from datetime import datetime` in
#     here would be a different object that the freeze does not touch, so the
#     test would silently measure the wall clock and prove nothing. That is
#     exactly how the hourly-chart regression got through, so `now` is
#     mandatory rather than merely recommended -- forgetting it is now an
#     immediate TypeError instead of a test that passes for the wrong reason.

def _today(now):
    return now.strftime('%Y-%m-%d')


def period_days(days, now):
    """The last `days` days AND today, both ends inclusive -- see #5."""
    return Period(start=(now - timedelta(days=days)).strftime('%Y-%m-%d'),
                  end=now.strftime('%Y-%m-%d'), days=days)


def period_for_day(day):
    """A single calendar date, e.g. today or yesterday."""
    return Period(start=day, end=day, days=0)


def period_today(now):
    return period_for_day(_today(now))


def period_yesterday(now):
    return period_for_day((now - timedelta(days=1)).strftime('%Y-%m-%d'))


def period_all_time(now):
    """Everything ever recorded, up to and including today. For the few
    figures that are genuinely lifetime ("top-selling products overall") --
    a real Period rather than "just omit the date predicate", so those
    callers still go through the same code path as every other figure
    instead of hand-writing an unscoped query again."""
    return Period(start='0001-01-01', end=_today(now), days=None)


def period_month_to_date(now):
    return Period(start=now.replace(day=1).strftime('%Y-%m-%d'),
                  end=now.strftime('%Y-%m-%d'), days=None)


def preceding_period(period):
    """The window of equal length immediately BEFORE `period`, for
    period-over-period comparison. Ends the day before `period` starts, so
    the two windows are adjacent and never overlap (the old inline version
    expressed this as `>= prev_start AND < period_start`; same date set).

    A period that already reaches back to the start of the calendar (see
    period_all_time) has no "before" to compare against, so it degenerates
    to an empty window rather than raising on date underflow."""
    start = date.fromisoformat(period.start)
    end = date.fromisoformat(period.end)
    span = (end - start).days
    try:
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span)
    except OverflowError:
        # start > end: matches no row, which is the honest answer.
        return Period(start='9999-12-31', end='0001-01-01', days=period.days)
    return Period(start=prev_start.strftime('%Y-%m-%d'),
                  end=prev_end.strftime('%Y-%m-%d'), days=period.days)


# ── The predicates (#5 and #6) ────────────────────────────────────────────────

def _scope(cid, period, branch_id=None, alias=None):
    """The one company+period+branch WHERE fragment. `alias` is the table
    alias to qualify columns with ('s' for sales in a join, None for an
    unaliased single-table query). Returns (sql, params) -- callers splice
    the sql after their own WHERE/AND and extend their param list."""
    p = f'{alias}.' if alias else ''
    sql = f'{p}company_id=? AND date({p}created_at) >= ? AND date({p}created_at) <= ?'
    params = [cid, period.start, period.end]
    if branch_id not in (None, ''):
        sql += f' AND {p}branch_id=?'
        params.append(branch_id)
    return sql, params


def _money(value):
    return round(float(value or 0), 2)


def _missing_table(exc):
    """True only for "no such table" -- the one failure mode the refund
    tolerance below is actually for."""
    return isinstance(exc, sqlite3.OperationalError) and 'no such table' in str(exc).lower()


def _returns_query(conn, sql, params):
    """Every read of `returns`/`return_items` goes through here. Those
    tables are created unconditionally by init_retail(), but the routes this
    module replaced each wrapped their refund lookup in a try/except so a
    hand-built or partially-migrated database (a test fixture, an old backup
    restored mid-upgrade) degrades to "no refunds known" instead of 500-ing
    the whole dashboard. That tolerance is preserved -- once, here, instead
    of copy-pasted at every call site.

    NARROWLY, though, and loudly. The tolerance is ONLY for a missing table,
    and it is logged at WARNING with the failing SQL every single time.
    Catching sqlite3.Error wholesale (which is what the copy-pasted versions
    did, and what this helper first did) means a locked database, a disk I/O
    error or a corrupt index also returns [] -- and because this helper now
    sits on the refund leg of cogs(), all four bucketed breakdowns and
    top_products, "[]" there is not a degraded chart, it is REVENUE REPORTED
    AS IF NO REFUND HAD EVER HAPPENED, silently, on a financial screen.
    Those paths previously issued no refund query at all, so broadening the
    swallow along with the query would have been a real regression. Anything
    that is not a missing table now propagates and 500s, which is the honest
    outcome: a report that cannot be computed must not be printed."""
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.Error as exc:
        if not _missing_table(exc):
            raise
        log.warning("retail metrics: refund query skipped, table missing (%s): %s",
                    exc, ' '.join(sql.split()))
        return []


def _tax_mode(conn, cid):
    """The company's configured tax_calculation_mode, read exactly the way
    core/retail/einvoice_adapter.py::build_document() reads it (and for the
    same reason: core/retail must not import api.retail_api's _settings(),
    since api/ already imports core/retail). A fresh install whose
    retail_settings table does not exist yet falls back to the default mode
    -- narrowed and logged for the same reason _returns_query is."""
    try:
        row = conn.execute(
            "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='tax_calculation_mode'",
            (cid,)).fetchone()
    except sqlite3.Error as exc:
        if not _missing_table(exc):
            raise
        log.warning("retail metrics: retail_settings missing (%s), assuming %s",
                    exc, pricing.DEFAULT_MODE)
        return pricing.DEFAULT_MODE
    return pricing.normalize_mode(row[0] if row else None)


# ── Scalars ───────────────────────────────────────────────────────────────────

def gross_sales(conn, cid, period, branch_id=None):
    """Money rung up, BEFORE refunds. Almost nothing should want this --
    see #1. Exposed only so a screen that genuinely means "gross" can say so
    out loud rather than quietly re-writing SUM(total) for the eighth time."""
    where, params = _scope(cid, period, branch_id)
    row = conn.execute(f'SELECT COALESCE(SUM(total),0) FROM sales WHERE {where}', params).fetchone()
    return _money(row[0])


def refunds(conn, cid, period, branch_id=None):
    where, params = _scope(cid, period, branch_id)
    rows = _returns_query(conn, f'SELECT COALESCE(SUM(refund_amount),0) FROM returns WHERE {where}', params)
    return _money(rows[0][0]) if rows else 0.0


def transactions(conn, cid, period, branch_id=None):
    """Sale count, never netted -- see #2."""
    where, params = _scope(cid, period, branch_id)
    return int(conn.execute(f'SELECT COUNT(*) FROM sales WHERE {where}', params).fetchone()[0] or 0)


def revenue(conn, cid, period, branch_id=None):
    """THE revenue figure -- net of returns, always (#1)."""
    return _money(gross_sales(conn, cid, period, branch_id) - refunds(conn, cid, period, branch_id))


def avg_ticket(net_revenue, txn_count):
    """Net revenue / transactions (#3). Pure arithmetic on figures already
    computed above, so it is structurally incapable of disagreeing with the
    revenue and transaction numbers printed next to it."""
    return _money(net_revenue / txn_count) if txn_count else 0.0


def cogs(conn, cid, period, branch_id=None):
    """Cost of goods sold, NET of returned units.

    Netting the returns side matters and is easy to get wrong: revenue is
    net (#1), so if COGS stayed gross then gross_profit = revenue - cogs
    would be understated by the full cost of every returned unit. That is
    exactly the disagreement this module exists to kill -- the Reports
    summary card would report a different profit from the per-product
    profit column beside it, which computes over net units.

    Returned goods go back on the shelf (create_return() restocks them), so
    their cost genuinely was not consumed. Subtracting them is the accurate
    answer, not merely the self-consistent one.

    Both legs are `quantity * products.cost_price`, i.e. the same expression
    over the same column, so the #7 basis question does not arise here at all
    -- a purchase cost carries no sales tax and neither leg has one. (Stated
    because "check both sides of every subtraction" is the rule that
    top_products failed; this is what checking it looks like when it passes.)

    Uses products.cost_price as it stands RIGHT NOW on both sides -- see the
    KNOWN GAP section in this module's docstring."""
    sale_where, sale_params = _scope(cid, period, branch_id, alias='s')
    sold = conn.execute(f"""
        SELECT COALESCE(SUM(si.quantity * p.cost_price),0)
        FROM sale_items si
        JOIN products p ON si.product_id=p.id
        JOIN sales s ON si.sale_id=s.id
        WHERE {sale_where}
    """, sale_params).fetchone()[0]

    ret_where, ret_params = _scope(cid, period, branch_id, alias='r')
    rows = _returns_query(conn, f"""
        SELECT COALESCE(SUM(ri.quantity * p.cost_price),0)
        FROM return_items ri
        JOIN products p ON ri.product_id=p.id
        JOIN returns r ON ri.return_id=r.id
        WHERE {ret_where}
    """, ret_params)
    returned = rows[0][0] if rows else 0
    return _money(float(sold or 0) - float(returned or 0))


def inventory_value(conn, cid):
    """Stock on hand at current cost. Not period- or branch-scoped: it is a
    snapshot of right now, which is why it takes no Period."""
    row = conn.execute("""
        SELECT COALESCE(SUM(b.quantity_on_hand * p.cost_price),0)
        FROM inventory_balances b JOIN products p ON b.product_id=p.id
        WHERE b.company_id=? AND p.status='active'
    """, (cid,)).fetchone()
    return _money(row[0])


# ── Breakdowns ────────────────────────────────────────────────────────────────
#
# Every breakdown below nets refunds into its own buckets, and buckets are
# the UNION of the sale keys and the refund keys, never just the sale keys --
# dropping a refund-only bucket would put money in no bucket at all.
#
# For the three breakdowns that partition the period exhaustively --
# revenue_by_day, revenue_by_hour, revenue_by_payment_method -- that gives
#     sum(bucket revenues) == revenue(...) for the same period and branch,
# which is what stops one screen's chart from disagreeing with the KPI card
# printed above it, and is asserted in
# products/retail/tests/retail_metrics_consistency_test.py.
#
# TWO breakdowns here do NOT satisfy that identity, by design. Naming them
# rather than letting the paragraph above quietly cover them:
#
#   * top_products() returns at most `limit` rows. A top-10 list of a
#     catalogue with eleven products sums to less than revenue() by whatever
#     the eleventh product earned; that is what "top 10" means. (Its per-row
#     figures ARE on the canonical basis -- see #7 -- so an UNTRUNCATED call
#     does sum back to revenue(), which is the form the gross-profit
#     agreement test exercises.) It also drops rows whose product has been
#     hard-deleted out from under its sale lines.
#   * revenue_by_branch() enumerates ACTIVE branches. Revenue booked against
#     a branch that has since been deactivated, or against no branch at all
#     (sales.branch_id IS NULL -- legal in the schema, and what a
#     single-till install that never created a branch produces), has no bar
#     to land in and is therefore not in the sum. The chart is a comparison
#     of the branches you currently operate, not a partition of revenue.
#
# Anything ADDED to this section is expected to satisfy the identity unless
# it says here why it cannot.

def _merge_buckets(sale_rows, refund_rows):
    """sale_rows: (key, gross, count). refund_rows: (key, refunds).
    Returns {key: {'gross','refunds','revenue','count'}} over the union."""
    out = {}
    for key, gross, count in sale_rows:
        out[key] = {'gross': float(gross or 0), 'refunds': 0.0, 'count': int(count or 0)}
    for key, refund in refund_rows:
        out.setdefault(key, {'gross': 0.0, 'refunds': 0.0, 'count': 0})
        out[key]['refunds'] += float(refund or 0)
    for bucket in out.values():
        bucket['revenue'] = _money(bucket['gross'] - bucket['refunds'])
        bucket['gross'] = _money(bucket['gross'])
        bucket['refunds'] = _money(bucket['refunds'])
    return out


def _bucketed(conn, cid, period, branch_id, sales_key, returns_key):
    """Shared shape for the day/hour/payment-method/branch breakdowns: one
    key expression grouped over `sales`, its counterpart grouped over
    `returns`, merged into net buckets.

    `sales` and `returns` both carry company_id/created_at/branch_id, so a
    SINGLE _scope() fragment applies verbatim to both -- which is precisely
    the guarantee that a bucket's sales side and refund side can never end
    up scoped to different periods or different branches."""
    where, params = _scope(cid, period, branch_id)
    sales = conn.execute(
        f'SELECT {sales_key} AS k, COALESCE(SUM(total),0), COUNT(*) '
        f'FROM sales WHERE {where} GROUP BY k', params).fetchall()
    rets = _returns_query(
        conn,
        f'SELECT {returns_key} AS k, COALESCE(SUM(refund_amount),0) '
        f'FROM returns WHERE {where} GROUP BY k', params)
    return _merge_buckets([(r[0], r[1], r[2]) for r in sales], [(r[0], r[1]) for r in rets])


def revenue_by_day(conn, cid, period, branch_id=None):
    """Net revenue per calendar day, ascending. Powers the sales-trend
    chart, whose day totals now sum to exactly the Revenue KPI beside it."""
    buckets = _bucketed(conn, cid, period, branch_id, 'date(created_at)', 'date(created_at)')
    return [{'day': day,
             'revenue': buckets[day]['revenue'],
             'transactions': buckets[day]['count'],
             'avg_ticket': avg_ticket(buckets[day]['revenue'], buckets[day]['count'])}
            for day in sorted(buckets)]


def revenue_by_hour(conn, cid, period, branch_id=None):
    """Net revenue keyed by two-digit hour ('00'..'23'). Zero-filling the
    quiet hours is the caller's job (the dashboard has to know how far into
    the day "now" is); this only reports the hours that saw activity."""
    hour_expr = "strftime('%H',created_at)"
    buckets = _bucketed(conn, cid, period, branch_id, hour_expr, hour_expr)
    return {hour: buckets[hour]['revenue'] for hour in buckets}


def revenue_by_payment_method(conn, cid, period, branch_id=None):
    """Net revenue per tender type, highest first.

    A refund is netted against the method it was PAID BACK IN
    (returns.refund_method), not the method the original sale used -- cash
    handed back over the counter reduces the cash taken that day even if the
    sale it reverses was on card. That is also what the cash drawer already
    assumes (_cash_expected subtracts cash refunds from the cash float), so
    this keeps the chart and the drawer telling the same story."""
    buckets = _bucketed(conn, cid, period, branch_id, 'payment_method', 'refund_method')
    rows = [{'payment_method': method,
             'count': buckets[method]['count'],
             'revenue': buckets[method]['revenue']}
            for method in buckets]
    rows.sort(key=lambda r: r['revenue'], reverse=True)
    return rows


def top_products(conn, cid, period, branch_id=None, limit=10):
    """Best sellers WITHIN the period (#5) -- this is the fix for the route
    that had no date filter at all and could out-earn the page it sat on.

    Units and revenue are both net: a returned unit is subtracted from the
    product's units_sold and its refund from the product's revenue, via
    return_items. Cost/profit carry the current-cost caveat (KNOWN GAP).

    THE SALES LEG DOES NOT READ sale_items.line_total. It cannot: that
    column holds the TAXABLE amount (tax excluded) while return_items
    .line_total holds the TOTAL (tax included), so subtracting one from the
    other is wrong by the tax on every refund -- see #7, which is the whole
    reason this function is shaped the way it is. Instead each distinct sale
    line is re-priced through pricing.calculate_line(), the same call with
    the same persisted inputs that create_sale() made when it wrote
    sales.total, so these per-product revenues sum back to SUM(sales.total)
    exactly rather than approximately.

    The GROUP BY is over the full line shape, not just product_id, so
    identical lines (the overwhelmingly common case: same product, qty 1, no
    discount, one tax rate) collapse to a single row with a count. Rounding
    is per line and must stay that way -- calculate_line() rounds each line
    to the cent before create_sale() sums them -- which is why this cannot
    be one SUM() in SQL."""
    mode = _tax_mode(conn, cid)
    sale_where, sale_params = _scope(cid, period, branch_id, alias='s')
    sales = conn.execute(f"""
        SELECT si.product_id AS pid,
               si.quantity AS quantity,
               si.unit_price AS unit_price,
               COALESCE(si.discount_pct,0) AS discount_pct,
               COALESCE(si.tax_rate,0) AS tax_rate,
               COUNT(*) AS line_count
        FROM sale_items si
        JOIN sales s ON si.sale_id=s.id
        WHERE {sale_where}
        GROUP BY si.product_id, si.quantity, si.unit_price, si.discount_pct, si.tax_rate
    """, sale_params).fetchall()

    ret_where, ret_params = _scope(cid, period, branch_id, alias='r')
    rets = _returns_query(conn, f"""
        SELECT ri.product_id AS pid,
               COALESCE(SUM(ri.quantity),0) AS units,
               COALESCE(SUM(ri.line_total),0) AS gross
        FROM return_items ri
        JOIN returns r ON ri.return_id=r.id
        WHERE {ret_where}
        GROUP BY ri.product_id
    """, ret_params)

    totals = {}
    for row in sales:
        count = int(row['line_count'] or 0)
        units = float(row['quantity'] or 0)
        line_total = pricing.calculate_line(
            float(row['unit_price'] or 0), units,
            float(row['discount_pct'] or 0), float(row['tax_rate'] or 0), mode=mode)['total']
        entry = totals.setdefault(row['pid'], {'units': 0.0, 'revenue': 0.0})
        entry['units'] += units * count
        entry['revenue'] += line_total * count
    # The refund leg needs no re-pricing: create_return() writes
    # return_items.line_total straight from calculate_line()'s `total`, so it
    # is ALREADY the tax-inclusive figure the sales leg above was rebuilt to
    # match, and SUM(return_items.line_total) per return equals that return's
    # returns.refund_amount exactly.
    for row in rets:
        entry = totals.setdefault(row['pid'], {'units': 0.0, 'revenue': 0.0})
        entry['units'] -= float(row['units'] or 0)
        entry['revenue'] -= float(row['gross'] or 0)
    if not totals:
        return []

    # Rank and cut BEFORE looking names up, so the metadata query below binds
    # at most `limit` ids. Doing it the other way round would build an
    # `IN (...)` over every product sold in the window, which on a real
    # catalogue is thousands of bound variables and eventually trips
    # SQLITE_MAX_VARIABLE_NUMBER. `limit` is caller-supplied (a query string
    # on /reports/top-products), hence the hard ceiling too -- no chart needs
    # more than this and it keeps the bind count bounded by construction.
    limit = max(1, min(int(limit), 500))
    ranked = sorted(totals.items(), key=lambda kv: kv[1]['units'], reverse=True)[:limit]

    # Names/SKUs/costs by id rather than by JOIN, because a product that
    # appears ONLY in return_items for this window still has to be nameable.
    placeholders = ','.join('?' * len(ranked))
    meta = {r['id']: r for r in conn.execute(
        f'SELECT id, name, sku, COALESCE(cost_price,0) AS cost_price '
        f'FROM products WHERE company_id=? AND id IN ({placeholders})',
        [cid] + [pid for pid, _ in ranked]).fetchall()}

    rows = []
    for pid, entry in ranked:
        info = meta.get(pid)
        if info is None:
            continue  # product hard-deleted out from under its sale lines
        cost = _money(entry['units'] * float(info['cost_price'] or 0))
        rows.append({'product_id': pid, 'name': info['name'], 'sku': info['sku'],
                     'units_sold': round(entry['units'], 2),
                     'revenue': _money(entry['revenue']),
                     'cost': cost,
                     'profit': _money(entry['revenue'] - cost)})
    return rows


def revenue_by_branch(conn, cid, period):
    """Every ACTIVE branch, including ones that sold nothing in the window
    (a zero bar is a real, visible fact in a comparison chart; a missing bar
    is a lie). Deliberately takes no branch_id -- see #6's exception."""
    branches = conn.execute(
        "SELECT id, name FROM branches WHERE company_id=? AND status='active' ORDER BY name",
        (cid,)).fetchall()
    buckets = _bucketed(conn, cid, period, None, 'branch_id', 'branch_id')
    rows = []
    for branch in branches:
        bucket = buckets.get(branch['id'], {'revenue': 0.0, 'count': 0})
        rows.append({'branch_id': branch['id'], 'branch_name': branch['name'],
                     'revenue': bucket['revenue'],
                     'transactions': bucket['count'],
                     'avg_ticket': avg_ticket(bucket['revenue'], bucket['count'])})
    return rows


def summary(conn, cid, period, branch_id=None):
    """The Reports page KPI cards, and the body of the emailed/WhatsApped
    report. Every figure here is one of the definitions above -- nothing is
    recomputed locally, which is what guarantees the cards agree with the
    charts drawn from the breakdowns."""
    prev = preceding_period(period)
    cur_rev = revenue(conn, cid, period, branch_id)
    prev_rev = revenue(conn, cid, prev, branch_id)
    cur_txns = transactions(conn, cid, period, branch_id)
    cur_cost = cogs(conn, cid, period, branch_id)
    gross_profit = _money(cur_rev - cur_cost)

    return {
        'period_days': period.days,
        'revenue': cur_rev,
        'prev_revenue': prev_rev,
        'revenue_change': round((cur_rev - prev_rev) / prev_rev * 100, 1) if prev_rev > 0 else 0,
        'transactions': cur_txns,
        'avg_ticket': avg_ticket(cur_rev, cur_txns),
        'cogs': cur_cost,
        'gross_profit': gross_profit,
        'margin_pct': round(gross_profit / cur_rev * 100, 1) if cur_rev > 0 else 0,
        'inventory_value': inventory_value(conn, cid),
    }
