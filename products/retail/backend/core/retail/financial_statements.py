"""Aura Retail -- the income statement (profit and loss), read straight off
schema v38/v39's double-entry general ledger (core/retail/general_ledger.py).

WHY THIS EXISTS, AND WHY IT READS THE LEDGER RATHER THAN `sales`/`returns`.
core/retail/metrics.py already answers "how much revenue" by summing
`sales.total` and `returns.refund_amount` directly -- that is the right tool
for a fast KPI tile, and this module does not replace it. A PROFIT AND LOSS
STATEMENT is a different kind of claim: it has to be provable, line by line,
against a set of entries someone could audit, not merely a number a formula
produced. The general ledger is that record (`post_journal`'s balance
invariant means every figure here is backed by a balanced Dr/Cr pair, traced
to a real sale/return/purchase through `gl_journals.source_type`/
`source_id`), so this module is deliberately GL-derived even though it will
usually agree with metrics.py's own numbers to the last minor unit. When it
does not agree, THAT disagreement is itself useful information -- it means
something posted outside the money-path routes metrics.py assumes.

THE FIVE DESIGN DECISIONS, held together here because they interact:

1. DERIVE BY ACCOUNT TYPE, NEVER BY AN ENUMERATED CODE LIST. `income_
   statement` below asks the ledger "which accounts are account_type=
   'revenue'/'expense' and what did they move in this period", never
   "SELECT ... WHERE code IN ('4000','4900','5000','5900')". A shop that
   adds a new expense account to its chart (there is no create-account route
   in v38/v39 -- see general_ledger.py's DEFAULT_CHART comment -- but a
   future one, or a hand-seeded row, will exist one day) appears in its own
   P&L automatically, with zero edits here. The ONE exception, and it is
   deliberate: `gl.ACCOUNT_COGS` (an imported, named constant from the
   module that itself defines and is the ONLY writer of that one account via
   `post_cogs` -- see that function's own docstring) is pulled out of the
   generic expense bucket so "gross profit" can exist at all. That is not
   the hardcoded LIST this design avoids -- a list would silently drop
   whichever codes were left off it; singling out the one account the
   ledger's own posting logic already treats specially cannot drop anything,
   because every OTHER expense-type account, whatever its code, still lands
   in `operating_expenses` generically.

2. TYPE-CANONICAL NETTING, using each account's own `normal_balance` to
   decide GROSS vs. CONTRA, not to decide the sign of the type total. See
   `_TYPE_CANONICAL_NORMAL`'s own comment for the full arithmetic; in short,
   revenue is netted `credit - debit` for every revenue-type account
   regardless of that account's own normal_balance, which is what makes
   4900 (Sales Returns and Allowances, normal_balance='debit', a CONTRA-
   revenue account) SUBTRACT itself from revenue automatically rather than
   add to it. `a.normal_balance` is still read and used -- to classify a
   revenue account as a "gross revenue" line or a "returns" line for the
   `gross_revenue`/`sales_returns` breakdown -- so both halves of "by
   account TYPE and the account's own normal_balance" are real, doing two
   different jobs.

3. PERIOD SCOPING IS `gl_journals.business_date`, NEVER `posted_at`.
   `business_date` is the ACCOUNTING date (schema.py's own column comment:
   "the ACCOUNTING date... kept distinct from posted_at... for the same
   reason stock_transfers keeps created_at/sent_at/received_at distinct: a
   real-world event and the moment a device happened to record it are
   different facts"). `posted_at` is a local wall-clock display stamp with
   no accounting meaning -- a sale rung at 11:58pm and synced/backfilled the
   next morning must still land in the day it actually happened, or a
   month-end P&L silently drifts by however long a device was offline. Every
   query below filters and joins on `business_date` alone.

4. BRANCH-LESS JOURNALS ARE INCLUDED IN EVERY BRANCH'S REPORT, not excluded.
   `gl_journals.branch_id` is nullable (schema.py: "an opening-balance or a
   direct customer/supplier account payment has no single branch to
   charge"). A branch-scoped P&L query here uses
   `(gj.branch_id IS NULL OR gj.branch_id = ?)` -- the SAME convention
   core/retail/promotions.py already uses for its own nullable-branch
   filter ("branch_id IS NULL (every branch) OR branch_id equals the sale's
   own"). The alternative -- silently dropping branch-less entries from a
   branch-filtered report -- was explicitly rejected: it would make the sum
   of every individual branch's report disagree with the whole-company
   report by exactly the branch-less amount, which is precisely the kind of
   quiet discrepancy that destroys trust in a financial report. The
   documented consequence of INCLUDING them instead: summing every
   individual branch's P&L will double-count a branch-less figure once per
   branch. The whole-company report (no `branch_id` filter) is the one that
   is always exactly correct, because it includes every journal exactly
   once regardless of this question. In practice this is close to
   theoretical for revenue/expense accounts specifically -- `post_sale`/
   `post_return`/`post_cogs` (the only writers of revenue/COGS/loyalty-
   expense accounts) are always called with the branch the selling route
   resolved -- but the design has to be correct for the case it does not
   see today, not merely for the cases it happens to see now.

5. THE HONESTY FLAG. Schema v39 left `sale_items.unit_cost` at its column
   default, 0, for every sale that predates the migration, and its own
   docstring is explicit that 0 there means "no snapshot was taken", never
   "this sale cost nothing" (database/schema.py::_migrate_add_cost_basis).
   The identical sentinel also arises going FORWARD for a perfectly ordinary
   post-v39 sale of a product that has never been received against (avg_cost
   0, cost_price unset) -- `create_sale`'s own cost_basis fallback
   (`product['avg_cost'] if product['avg_cost'] else product['cost_price']`)
   resolves to 0 in that case too, for the same underlying reason: no cost
   basis exists anywhere for this line. Either way, `post_cogs`'s epsilon
   gate means a sale whose known-cost lines sum to (near) zero posts NO COGS
   journal at all, and a sale with SOME zero-cost lines and some real ones
   posts a COGS journal that is silently short by exactly the zero-cost
   lines' true (unknown) cost. A period containing either shape has an
   UNDERSTATED `cost_of_goods_sold` and therefore an OVERSTATED
   `gross_profit`/`net_profit`. `income_statement` detects this directly
   against `sale_items.unit_cost = 0` (never by inferring it from a missing
   COGS journal, which the partial case would miss) and returns both a
   boolean (`cost_basis_incomplete`) and a count of the affected sale
   documents (`cost_basis_incomplete_sale_count`), so a presentation layer
   can show a real number qualified as uncertain rather than a confident
   figure that is quietly wrong. A confidently wrong profit is worse than a
   disclosed-uncertain one -- this module's whole reason for existing is to
   never be the first kind.

MONEY: every accumulator here is a `Decimal`, quantized through
`commercial_runtime.currency.currency_quantum`/`quantize_money` -- the SAME
helpers general_ledger.py itself uses, never a bare `round(x, 2)`. The
"is this figure meaningfully nonzero" gate (used only for `gross_margin`'s
divide-by-zero guard) is half the currency's own minor unit, matching
`general_ledger._gl_epsilon`/`api.retail_api._money_epsilon` -- never a
hardcoded `0.005`, for the exact reason CLAUDE.md's round-4 corrections
record: a fixed literal silently under-reports on JOD/KWD/BHD/OMR's
3-decimal minor unit.

NOT BUILT HERE, deliberately, matching general_ledger.py's own "NOT BUILT"
posture: a balance sheet, a cash-flow statement, period close / retained-
earnings rollover, multi-currency consolidation, or a drill-through from a
statement line back to its individual journals (that last one is the same
kind of follow-up report_trial_balance's own comment names for a future
journal-browse route -- a presentation feature, not a ledger-correctness
one).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from commercial_runtime.currency import DEFAULT_BASE_CURRENCY, currency_quantum
from core.retail import general_ledger as gl


class StatementPeriodError(ValueError):
    """Raised by `income_statement` when `start_date`/`end_date` are not
    valid `'YYYY-MM-DD'` strings, or when `start_date` is after `end_date`.
    A `ValueError` subclass (not a bare one) so a caller that wants to catch
    "this specific function's period validation failed" can, without also
    swallowing an unrelated `ValueError` from somewhere else in the call
    stack. The route this module was built for catches this and returns a
    400 with the message verbatim -- see api/retail_api.py's
    `report_profit_and_loss`."""


#: Which side of a debit/credit pair counts as an INCREASE for a P&L account
#: TYPE. This is not a per-shop or per-account setting -- it is what
#: "revenue" and "expense" MEAN in double-entry bookkeeping, the identical
#: fact `general_ledger.DEFAULT_CHART`'s own `normal_balance` column already
#: encodes for the "usual" account of each type (4000 revenue/credit, 5000
#: and 5900 expense/debit).
#:
#: USING THE TYPE'S canonical direction -- rather than summing each
#: account's OWN `normal_balance`-signed net, the way `general_ledger.
#: trial_balance` does for a bare balance report -- is what makes a CONTRA
#: account net out correctly with no special case. Worked arithmetic for
#: 4900 (Sales Returns and Allowances: account_type='revenue',
#: normal_balance='debit', only ever debited):
#:
#:   trial_balance's OWN-normal-balance convention: net(4900) = debit-credit
#:   = +returns_amount (a POSITIVE number, because that is its own normal
#:   balance). Summing that WITH 4000's own net (+revenue_amount) would give
#:   revenue_amount + returns_amount -- returns would be ADDED to revenue,
#:   exactly backwards.
#:
#:   TYPE-canonical convention (this module): net(4900) = credit-debit = 0 -
#:   returns_amount = -returns_amount. Summed with 4000's net (credit-debit
#:   = +revenue_amount), the total is revenue_amount - returns_amount --
#:   correct, and with no code that ever had to know "4900 is a contra
#:   account" -- it falls out of always measuring in the TYPE's own
#:   direction rather than each account's.
#:
#: A future shop-added contra-expense account (normal_balance='credit',
#: none exist in DEFAULT_CHART today) nets out the identical way inside
#: `operating_expenses`, with zero changes to this module.
_TYPE_CANONICAL_NORMAL = {'revenue': 'credit', 'expense': 'debit'}


def _resolve_currency(conn, cid):
    """This company's base currency. Byte-identical query to `general_
    ledger._resolve_currency` / `api.retail_api._company_currency` --
    duplicated rather than imported, matching the layering reason `general_
    ledger.py`'s own module docstring gives for duplicating `_money_
    epsilon` from api/retail_api.py rather than importing it: this keeps
    financial_statements.py free of any reach into another module's
    underscore-prefixed internals, at the cost of one small query existing
    in three places instead of a cross-file private coupling. The formula
    is one line reading one shared table (`retail_settings`), so there is
    exactly one place the ANSWER can drift even though the trivial SQL is
    written three times.
    """
    try:
        row = conn.execute(
            "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='base_currency'",
            (cid,)
        ).fetchone()
        return (row[0] if row and row[0] else None) or DEFAULT_BASE_CURRENCY
    except Exception:
        return DEFAULT_BASE_CURRENCY


def _epsilon(currency):
    """Half `currency`'s own minor unit -- see `general_ledger._gl_epsilon`
    for the full reasoning (duplicated for the identical layering reason
    `_resolve_currency` above is). The only use here is `gross_margin`'s
    divide-by-zero guard: "is revenue meaningfully nonzero", never a
    hardcoded `0.005`."""
    try:
        return currency_quantum(currency) / 2
    except Exception:
        return Decimal('0.005')


def _parse_period_date(label, value):
    """`value` must be a real `'YYYY-MM-DD'` calendar date -- raises
    `StatementPeriodError` (never returns `None`/a silent default) on
    anything else, per this task's own instruction to "fail clearly on
    garbage rather than silently defaulting". Returns a `datetime.date` so
    `income_statement` can compare `start`/`end` chronologically; the SQL
    queries themselves compare the original STRINGS (ISO 'YYYY-MM-DD' sorts
    identically to chronological order), matching `_export_date_range`'s
    own inclusive-range convention elsewhere in api/retail_api.py.
    """
    if not isinstance(value, str) or not value.strip():
        raise StatementPeriodError(f"{label} is required (format 'YYYY-MM-DD').")
    try:
        return datetime.strptime(value.strip(), '%Y-%m-%d').date()
    except ValueError:
        raise StatementPeriodError(
            f"{label} {value!r} is not a valid date in 'YYYY-MM-DD' format."
        )


def income_statement(conn, cid, start_date, end_date, branch_id=None):
    """The profit and loss statement for company `cid` over
    `[start_date, end_date]`, BOTH ENDS INCLUSIVE -- a document whose
    `business_date` equals `start_date` or `end_date` exactly is IN the
    period, matching `_export_date_range`'s own inclusive convention
    elsewhere in this codebase. `start_date`/`end_date` are `'YYYY-MM-DD'`
    strings; `branch_id`, when given, is an integer `branches.id` (or
    `None` for the whole company) -- this function trusts its caller to
    have already resolved/validated that value (the route does the query-
    string parsing and multi-branch-scope coercion; see api/retail_api.py's
    `report_profit_and_loss`), the same "callers pass clean types" contract
    every `general_ledger.py` posting helper already carries.

    Raises `StatementPeriodError` if `start_date`/`end_date` are not valid
    dates or `start_date` is after `end_date`. Never silently swaps them,
    never silently defaults to "today" -- a caller that wants a default
    period decides that itself, explicitly, before calling this.

    Returns a dict:
        currency               -- this company's base currency code
        period                 -- {start_date, end_date, branch_id} echoed back
        revenue                -- NET of returns (gross_revenue - sales_returns)
        gross_revenue          -- revenue before returns
        sales_returns          -- the contra-revenue total (a positive number)
        cost_of_goods_sold     -- the ledger's own gl.ACCOUNT_COGS balance
        gross_profit           -- revenue - cost_of_goods_sold
        gross_margin           -- gross_profit / revenue, a RATIO (0.4, not
                                   40.0) -- None when revenue is not
                                   meaningfully nonzero (see `_epsilon`),
                                   never a misleading 0.0
        operating_expenses     -- {total, accounts: [{code, name, amount}]},
                                   every expense-type account EXCEPT COGS,
                                   derived generically (see module docstring
                                   point 1) -- zero-activity accounts in this
                                   period are omitted, not listed at 0
        net_profit             -- gross_profit - operating_expenses['total']
        has_activity           -- False when NOT ONE revenue/expense-type
                                   account moved in this period -- lets a
                                   presentation layer say "nothing was
                                   posted in this period" instead of "the
                                   shop broke even" for what is really an
                                   empty period, not a real zero result
        cost_basis_incomplete  -- True when any sale dated inside this
                                   period has at least one line with
                                   sale_items.unit_cost = 0 (see module
                                   docstring point 5) -- cost_of_goods_sold
                                   and therefore gross_profit/net_profit are
                                   UNDERSTATED/OVERSTATED respectively when
                                   this is True
        cost_basis_incomplete_sale_count -- how many distinct sales that is,
                                   for a presentation layer to disclose a
                                   real number rather than a bare warning
    """
    gl.ensure_default_chart(conn, cid)
    start = _parse_period_date('start_date', start_date)
    end = _parse_period_date('end_date', end_date)
    if start > end:
        raise StatementPeriodError(
            f"start_date {start_date!r} is after end_date {end_date!r} -- "
            f"a reporting period cannot run backwards."
        )

    currency = _resolve_currency(conn, cid)
    quant = currency_quantum(currency)
    eps = _epsilon(currency)

    # Branch-less journals ride along with EVERY branch filter -- see module
    # docstring point 4. `gj` = gl_journals, `gjl` = gl_journal_lines (short
    # aliases inside the subquery only, to keep the two JOINs on one line
    # each readable).
    branch_sql = ''
    period_params = [cid, start_date, end_date]
    if branch_id is not None:
        branch_sql = ' AND (gj.branch_id IS NULL OR gj.branch_id = ?)'
        period_params.append(branch_id)

    # The date filter lives INSIDE the subquery's WHERE, not appended to the
    # outer query's WHERE/ON -- so an account with real lifetime activity
    # but NOTHING in this specific period still comes back as a LEFT-JOIN
    # NULL (COALESCE'd to 0 below) rather than vanishing from the result
    # entirely, and an account that has NEVER been posted to at all (a
    # freshly seeded chart) behaves identically. Both cases are then
    # filtered out by the `debit == 0 and credit == 0: continue` guard in
    # the loop below, for the SAME reason (nothing to report), not two
    # different code paths.
    rows = conn.execute(
        "SELECT a.code, a.name, a.account_type, a.normal_balance, "
        "       COALESCE(SUM(fl.debit), 0) AS total_debit, "
        "       COALESCE(SUM(fl.credit), 0) AS total_credit "
        "FROM gl_accounts a "
        "LEFT JOIN ( "
        "    SELECT gjl.account_id, gjl.debit, gjl.credit "
        "    FROM gl_journal_lines gjl "
        "    JOIN gl_journals gj ON gj.id = gjl.journal_id "
        "    WHERE gj.company_id = ? AND gj.business_date >= ? AND gj.business_date <= ?"
        + branch_sql +
        ") fl ON fl.account_id = a.id "
        "WHERE a.company_id = ? AND a.account_type IN ('revenue', 'expense') "
        "GROUP BY a.id ORDER BY a.code",
        (*period_params, cid)
    ).fetchall()

    has_activity = False
    gross_revenue = Decimal('0')
    sales_returns = Decimal('0')
    revenue_net = Decimal('0')
    cogs = Decimal('0')
    opex_total = Decimal('0')
    opex_accounts = []

    for r in rows:
        debit = Decimal(str(r['total_debit']))
        credit = Decimal(str(r['total_credit']))
        if debit == 0 and credit == 0:
            continue  # no activity in this period for this account -- omit
        has_activity = True
        acct_type = r['account_type']
        canonical = _TYPE_CANONICAL_NORMAL[acct_type]
        # TYPE-canonical net -- see `_TYPE_CANONICAL_NORMAL`'s own comment
        # for why this is measured in the TYPE's direction, not the
        # account's OWN normal_balance.
        net = (credit - debit) if canonical == 'credit' else (debit - credit)

        if acct_type == 'revenue':
            revenue_net += net
            if r['normal_balance'] == canonical:
                # A "gross" revenue line (normal_balance agrees with the
                # type's own canonical direction) -- its type-canonical net
                # IS its own face value.
                gross_revenue += net
            else:
                # A CONTRA-revenue line (normal_balance disagrees) -- net is
                # already negative here (see the worked arithmetic above);
                # report its POSITIVE face value as `sales_returns`.
                sales_returns += -net
        else:  # acct_type == 'expense'
            if r['code'] == gl.ACCOUNT_COGS:
                cogs += net
            else:
                opex_total += net
                opex_accounts.append({
                    'code': r['code'],
                    'name': r['name'],
                    'amount': float(net.quantize(quant, rounding=ROUND_HALF_UP)),
                })

    gross_profit = revenue_net - cogs
    net_profit = gross_profit - opex_total
    # None, never 0.0, when revenue is not meaningfully nonzero -- a 0.0
    # margin reads as "the shop broke even on every unit sold", which is a
    # completely different claim from "there was no revenue to measure a
    # margin against at all".
    gross_margin = float(gross_profit / revenue_net) if revenue_net > eps else None

    # ── The honesty flag -- see module docstring point 5 ────────────────────
    # Detected directly against `sale_items.unit_cost = 0`, never inferred
    # from a missing COGS journal (`post_cogs`'s epsilon gate silently skips
    # posting only when a sale's KNOWN-cost lines sum to zero; a sale with
    # SOME zero-cost lines and some real ones still posts a real, but
    # understated, COGS journal -- inferring from journal absence would miss
    # exactly that partial case).
    #
    # Scoped the SAME way the revenue/expense query above is: `gj.company_id`
    # (never relying on `sales.company_id` alone, though it is also checked
    # for defense in depth), `gj.business_date` for the period, and the
    # identical branch-less-journals-ride-along rule.
    cost_flag_params = [cid, start_date, end_date]
    cost_flag_branch_sql = ''
    if branch_id is not None:
        cost_flag_branch_sql = ' AND (gj.branch_id IS NULL OR gj.branch_id = ?)'
        cost_flag_params.append(branch_id)
    incomplete_count = conn.execute(
        "SELECT COUNT(DISTINCT s.id) "
        "FROM gl_journals gj "
        "JOIN sales s ON s.id = gj.source_id AND s.company_id = gj.company_id "
        "JOIN sale_items si ON si.sale_id = s.id "
        "WHERE gj.company_id = ? AND gj.source_type = 'sale' "
        "AND gj.business_date >= ? AND gj.business_date <= ?"
        + cost_flag_branch_sql +
        " AND si.unit_cost = 0",
        cost_flag_params
    ).fetchone()[0]

    return {
        'currency': currency,
        'period': {'start_date': start_date, 'end_date': end_date, 'branch_id': branch_id},
        'revenue': float(revenue_net.quantize(quant, rounding=ROUND_HALF_UP)),
        'gross_revenue': float(gross_revenue.quantize(quant, rounding=ROUND_HALF_UP)),
        'sales_returns': float(sales_returns.quantize(quant, rounding=ROUND_HALF_UP)),
        'cost_of_goods_sold': float(cogs.quantize(quant, rounding=ROUND_HALF_UP)),
        'gross_profit': float(gross_profit.quantize(quant, rounding=ROUND_HALF_UP)),
        'gross_margin': gross_margin,
        'operating_expenses': {
            'total': float(opex_total.quantize(quant, rounding=ROUND_HALF_UP)),
            'accounts': opex_accounts,
        },
        'net_profit': float(net_profit.quantize(quant, rounding=ROUND_HALF_UP)),
        'has_activity': has_activity,
        'cost_basis_incomplete': incomplete_count > 0,
        'cost_basis_incomplete_sale_count': incomplete_count,
    }
