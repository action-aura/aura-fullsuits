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

THE BALANCE SHEET, AND WHY IT IS A SNAPSHOT, NOT A PERIOD.
`balance_sheet` below is this module's second statement, added
retail-hardware-viewports. Where `income_statement` answers "what happened
between two dates", a balance sheet answers "what does the company own, owe
and keep AS OF one date" -- there is no `start_date`, and `as_of_date` scopes
every query with `gl_journals.business_date <= ?`, never a range. Getting
this backwards (treating it as a same-shaped period report) would silently
drop every account balance carried forward from before whatever window was
chosen, which is not what a balance sheet means.

THE ONE HARD PART: THIS PRODUCT HAS NO PERIOD CLOSE. In real double-entry
bookkeeping, `Assets = Liabilities + Equity` holds automatically, at every
instant, as a consequence of `post_journal`'s own balance invariant (every
journal's debits equal its credits, so the GLOBAL sum of debits equals the
global sum of credits, always) -- see `balance_sheet`'s own docstring for the
worked algebra. But that identity only closes if EQUITY includes every
period's net profit. A conventional accounting system runs a period-close
step that posts net income into retained earnings; this codebase has never
had one (general_ledger.py's own "NOT BUILT" list used to name "period
close / retained-earnings rollover" here, and still does -- the ROLLOVER is
still absent). So `gl_accounts` code 3000 (Owner's Equity) alone will NOT
balance the sheet: the gap is exactly the cumulative, never-closed net
profit. `balance_sheet` closes that gap at READ time instead of at POST
time -- it computes "retained earnings" as a live, derived figure (calling
`income_statement` itself, from the dawn of the ledger through `as_of_date`)
rather than a stored balance, and presents it as its own labelled line
alongside the Owner's Equity ledger account, never merged into it silently.
This is a presentation-layer fix, not a bookkeeping one: no journal is
posted, `gl_accounts` code 3000 is completely unchanged, and the identity
above is reconstructed fresh on every read. A future real period-close
feature would post retained earnings INTO 3000 and this derived line would
then read 0 going forward -- that is still unbuilt, and this function does
not simulate it, only compensates for its absence in the one report that
would otherwise be visibly wrong without it.

THE CASH FLOW STATEMENT, AND WHY IT IS THE DIRECT METHOD.
`cash_flow` below is this module's THIRD statement, added retail-hardware-
viewports. There are two textbook ways to build one: the INDIRECT method
starts from net profit and backs into a cash figure by adjusting for
non-cash items and working-capital changes -- the tool you reach for when
all you HAVE is a P&L and two balance sheets and cash itself is not
separately recorded anywhere. The DIRECT method instead reads actual cash
movements straight off the ledger. This codebase has a real general ledger
with every debit/credit to `gl.ACCOUNT_CASH` individually posted and
traceable to a source document (`post_sale`/`post_return`/`post_customer_
payment`/`post_supplier_payment`/`post_purchase_order`, general_ledger.py),
so the indirect method's whole reason to exist -- not having the real
movements -- does not apply here, and reaching for it anyway would be
reconstructing an approximation of a number this module can simply read.
The direct method is also the more HONEST one for the reader this statement
is for: "money in from customers, money out to suppliers" is a sentence a
shopkeeper recognises, where "net profit, adjusted for a 1,240 increase in
accounts receivable" is not.

GROUPED BY `gl_journals.source_type` -- the document that caused the cash
movement -- never by an enumerated code list, for the same "derive
generically" reason point 1 above gives `income_statement`'s own account
selection. A cash-touching journal's `source_type` already carries a single,
well-defined business meaning (each of `post_sale`/`post_return`/`post_
customer_payment`/`post_supplier_payment`/`post_purchase_order`'s own
docstrings states exactly what the OTHER side of that journal is), so
grouping at the document level is a faithful reading of "what the other side
was" without having to arbitrate which of several non-cash lines in one
journal a single cash leg is "really" against -- a purchase order journal
can carry a Cash leg, an Accounts Payable leg and an Inventory debit all at
once, and there is no single correct way to allocate the cash portion
between the other two. `_CASH_FLOW_LABELS` below gives the known source
types a shopkeeper-readable label (and merges `supplier_payment`/
`po_payment` into ONE "Payments to Suppliers" line, because post_supplier_
payment's own docstring is explicit that the two are "ONE helper... the
accounting entry is identical in both cases"); an UNKNOWN future source
type still shows up, generically titled, rather than being silently
dropped -- the label is cosmetic, the SQL that finds it is not.

OPERATING ONLY -- NO INVESTING/FINANCING SECTION IS INVENTED. The current
chart of accounts (`general_ledger.DEFAULT_CHART`) has no fixed-asset, loan
or owner-contribution/draw account, so no journal this codebase can produce
touches cash for an investing or a financing reason; `investing_activities`/
`financing_activities` below are `None`, not empty `{total: 0, lines: []}`
placeholders, and `investing_and_financing_note` says why in words a
presentation layer can show. An honest two-section statement beats a padded
three-section one that would otherwise report a bank loan or an owner draw
as "0.00" -- a claim about something that was measured, not about something
that cannot happen with today's chart.

THE RECONCILIATION REUSES `balance_sheet`, NEVER A SECOND CASH DERIVATION.
Opening and closing cash are each read by calling `balance_sheet` (this
module's own second statement, immediately above) at the day before
`start_date` and at `end_date` respectively, and pulling `gl.ACCOUNT_CASH`'s
own balance out of its `assets.accounts` list -- the SAME lifetime-balance
machinery `balance_sheet` already uses for every other asset account, not a
parallel SUM over `gl_journal_lines` written a second time in this
function. `total_cash_in`/`total_cash_out` (this function's OWN direct-
method summation over just `[start_date, end_date]`) then independently
predicts a closing figure (`opening_cash + net_change_in_cash`), and
`reconciliation.is_reconciled` compares that prediction against `balance_
sheet`'s own answer. For a healthy ledger the two are equal by construction
-- a balance's change over a window is definitionally the sum of what moved
it during that window -- so this is the same "provably true, but MEASURED
anyway" posture `balance_sheet`'s own `balance_check` documents for itself,
compared to within `_epsilon(currency)` and never inferred from the
architecture holding. `opening_cash` needs the calendar day immediately
before `start_date`; the one edge case (`start_date` itself is the
'0001-01-01' all-time sentinel `income_statement`'s own retained-earnings
call uses) has no earlier day to ask about and reports `opening_cash` as a
literal 0 rather than underflowing a date.

NOT BUILT HERE, deliberately, matching general_ledger.py's own "NOT BUILT"
posture: period close / retained-earnings ROLLOVER (the actual posting of
net income into 3000 -- see the balance sheet's own docstring for why
`balance_sheet` works around its absence instead of assuming it),
multi-currency consolidation, or a drill-through from a statement line back
to its individual journals (that last one is the same kind of follow-up
report_trial_balance's own comment names for a future journal-browse route
-- a presentation feature, not a ledger-correctness one).
"""
from __future__ import annotations

from datetime import date, datetime
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
        revenue                -- SALES revenue, NET of returns (gross_revenue - sales_returns).
                                   Does NOT include other_income (POLISH-1).
        gross_revenue          -- sales revenue before returns
        revenue_accounts       -- the sales-revenue lines behind gross_revenue
        cash_rounding          -- signed 4950 Cash Rounding net, INSIDE gross_revenue (not other_income)
        other_income           -- {total, accounts: [{code, name, amount}]}: revenue that is not
                                   a sale (4800). Outside gross_profit/gross_margin, inside
                                   net_profit
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
        net_profit             -- gross_profit - operating_expenses['total'] + other_income['total']
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
    # P0 round 2 (item B2): NORMALIZE, not just VALIDATE, before either
    # string reaches the SQL parameter below. `_parse_period_date` is
    # lenient about a non-zero-padded month/day -- `datetime.strptime`
    # parses '2026-9-1' to the same date as '2026-09-01' -- but this
    # function's own `period_params` (a few lines down) binds the ORIGINAL
    # `start_date`/`end_date` strings, not `start`/`end`'s parsed value,
    # against `gl_journals.business_date`, which is always stored
    # zero-padded ISO. SQLite compares TEXT lexicographically, and that
    # disagrees with calendar order the instant one side is not
    # zero-padded (`'2026-09-15' < '2026-9-1'` as TEXT, since the 6th
    # character '0' sorts before '9') -- a caller passing '2026-9-1' would
    # silently get the WRONG revenue/expense rows with no error anywhere,
    # exactly the trap `_parse_period_date`'s own validation looks like it
    # already closed. `general_ledger.trial_balance`'s `as_of` parameter had
    # the identical bug and got the identical fix. Reassigning here also
    # normalizes the `period` block this function echoes back below, so a
    # caller that sent '2026-9-1' sees '2026-09-01' in its own response
    # rather than a value that would fail to round-trip if resent verbatim.
    start_date, end_date = start.isoformat(), end.isoformat()

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
    other_income_total = Decimal('0')
    other_income_accounts = []
    cash_rounding_net = Decimal('0')
    cogs = Decimal('0')
    opex_total = Decimal('0')
    opex_accounts = []
    revenue_accounts = []

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
            if (r['normal_balance'] == canonical and r['code'] != gl.ACCOUNT_SALES_REVENUE
                    and r['code'] != gl.ACCOUNT_CASH_ROUNDING):
                # POLISH-1: income that is NOT a sale (4800 Other Income -- a supplier's refund,
                # a found note) is reported on its own and kept OUT of revenue, gross profit and
                # the margin. Folded into revenue it made the margin look better than the
                # shop's trading really was. It still counts in net profit (added below).
                other_income_total += net
                # A line that nets to 0.000 (income posted, then reversed) is not shown.
                if net.quantize(quant, rounding=ROUND_HALF_UP) != 0:
                    other_income_accounts.append({
                        'code': r['code'],
                        'name': r['name'],
                        'amount': float(net.quantize(quant, rounding=ROUND_HALF_UP)),
                    })
                continue
            revenue_net += net
            if r['code'] == gl.ACCOUNT_CASH_ROUNDING:
                # MONEY-5 verifier LOW: 4950 Cash Rounding is a SALES adjustment (the coin the shop
                # gave up or took on the sale itself, and on its refunds), not "Other income": it
                # belongs inside net sales so gross profit and the margin see it. Signed: a shop
                # that rounds down shows a NEGATIVE line. It also stays visible on its own below.
                cash_rounding_net += net
                gross_revenue += net
                if net.quantize(quant, rounding=ROUND_HALF_UP) != 0:
                    revenue_accounts.append({
                        'code': r['code'],
                        'name': r['name'],
                        'amount': float(net.quantize(quant, rounding=ROUND_HALF_UP)),
                    })
                continue
            if r['normal_balance'] == canonical:
                # A "gross" revenue line (normal_balance agrees with the
                # type's own canonical direction) -- its type-canonical net
                # IS its own face value.
                gross_revenue += net
                revenue_accounts.append({
                    'code': r['code'],
                    'name': r['name'],
                    'amount': float(net.quantize(quant, rounding=ROUND_HALF_UP)),
                })
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
                # POLISH-1: a VOIDED expense posts a reversing journal, so its account has
                # activity (debit == credit > 0) yet nets to 0.000. Listing "Rent  0.000" tells the
                # owner nothing; the total is unaffected either way.
                if net.quantize(quant, rounding=ROUND_HALF_UP) != 0:
                    opex_accounts.append({
                        'code': r['code'],
                        'name': r['name'],
                        'amount': float(net.quantize(quant, rounding=ROUND_HALF_UP)),
                    })

    gross_profit = revenue_net - cogs
    net_profit = gross_profit - opex_total + other_income_total
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
        'revenue_accounts': revenue_accounts,
        # The signed 4950 Cash Rounding net, already INSIDE gross_revenue / revenue (0.0 when the shop
        # does not round).
        'cash_rounding': float(cash_rounding_net.quantize(quant, rounding=ROUND_HALF_UP)),
        'sales_returns': float(sales_returns.quantize(quant, rounding=ROUND_HALF_UP)),
        'cost_of_goods_sold': float(cogs.quantize(quant, rounding=ROUND_HALF_UP)),
        'gross_profit': float(gross_profit.quantize(quant, rounding=ROUND_HALF_UP)),
        'gross_margin': gross_margin,
        'other_income': {
            'total': float(other_income_total.quantize(quant, rounding=ROUND_HALF_UP)),
            'accounts': other_income_accounts,
        },
        'operating_expenses': {
            'total': float(opex_total.quantize(quant, rounding=ROUND_HALF_UP)),
            'accounts': opex_accounts,
        },
        'net_profit': float(net_profit.quantize(quant, rounding=ROUND_HALF_UP)),
        'has_activity': has_activity,
        'cost_basis_incomplete': incomplete_count > 0,
        'cost_basis_incomplete_sale_count': incomplete_count,
    }


#: Which side of a debit/credit pair counts as an INCREASE for a BALANCE-
#: SHEET account TYPE -- the identical "measure in the TYPE's own canonical
#: direction, never each account's own normal_balance" convention
#: `_TYPE_CANONICAL_NORMAL` above uses for revenue/expense (see that
#: constant's own comment for the worked contra-account arithmetic),
#: extended to the three balance-sheet types. No contra-asset/liability/
#: equity account exists in `general_ledger.DEFAULT_CHART` today, so this
#: nets identically to `trial_balance`'s own each-account's-own-
#: normal_balance convention for every account that exists right now -- the
#: two conventions only diverge for a CONTRA account, and choosing the
#: type-canonical one here means a future one (e.g. Accumulated
#: Depreciation, account_type='asset', normal_balance='credit') nets out
#: correctly with zero changes to this module, for the same reason 4900
#: already does inside `income_statement`.
_BALANCE_SHEET_TYPE_CANONICAL_NORMAL = {'asset': 'debit', 'liability': 'credit', 'equity': 'credit'}


def balance_sheet(conn, cid, as_of_date, branch_id=None):
    """Assets, liabilities and equity for company `cid` AS OF `as_of_date`
    (a `'YYYY-MM-DD'` string) -- a SNAPSHOT, not a period: every query below
    filters `gl_journals.business_date <= as_of_date`, never a range. This
    is the one structural difference from `income_statement` above that
    matters most to get right -- a balance sheet answers "what does the
    company own/owe/keep right now", not "what moved in a window" -- and it
    is why this function takes one date, not two. `branch_id`, when given,
    is an integer `branches.id` (or `None` for the whole company); this
    function trusts its caller to have already resolved/validated it,
    exactly the same "callers pass clean types" contract `income_statement`
    documents for itself (the route does the query-string parsing and
    multi-branch-scope coercion; see api/retail_api.py's
    `report_balance_sheet`).

    Raises `StatementPeriodError` if `as_of_date` is not a valid date --
    reusing `income_statement`'s own exception type rather than minting a
    second one, so a caller (the route) can catch exactly one class for
    either statement's period validation.

    THE NO-PERIOD-CLOSE PROBLEM, AND WHY EQUITY IS TWO LINES, NOT ONE.
    This codebase has never posted a period-close journal -- net profit
    never moves into `gl_accounts` code 3000 (Owner's Equity), so that
    account's own ledger balance is NOT the company's real equity, and
    presenting it alone would make this statement fail to balance for every
    company that has ever posted a single sale. The fix is arithmetic, not
    a posting: double-entry bookkeeping guarantees (`post_journal`'s own
    balance invariant, enforced on every journal) that

        SUM(debit) == SUM(credit)                              -- globally

    over every journal line ever posted for this company (scoped to
    `branch_id` exactly like `income_statement` scopes it -- see point 4
    below). Splitting that global sum by account TYPE, each measured in its
    OWN canonical direction (assets/expenses net debit-credit, liabilities/
    equity/revenue net credit-debit -- `_BALANCE_SHEET_TYPE_CANONICAL_
    NORMAL` above and `_TYPE_CANONICAL_NORMAL` in `income_statement`'s own
    section), the identity rearranges to:

        assets_total == liabilities_total + equity_ledger_total
                         + (revenue_total - expense_total)

    and `revenue_total - expense_total` over ALL TIME up to `as_of_date` is
    EXACTLY `income_statement(conn, cid, '0001-01-01', as_of_date,
    branch_id=branch_id)['net_profit']` -- the same all-time sentinel start
    date `core/retail/metrics.py::period_all_time` already uses for "every
    figure ever recorded, up to and including today". So `equity` below is
    reported as `owners_equity` (the literal ledger balance of every
    account_type='equity' account, generically derived -- 3000 today) PLUS
    `retained_earnings` (that all-time `net_profit`, computed by CALLING
    `income_statement` rather than re-deriving revenue/expense with a
    second query) -- never merged into one opaque figure, so a shopkeeper
    reading "Retained earnings" understands it is an accumulated, running
    total this product has no period-close step to ever move into the
    ledger proper, not a manual bookkeeping entry someone made. Reusing
    `income_statement`'s own function (never a parallel SUM over
    `gl_journal_lines`) is deliberate: if the two statements ever
    disagreed about net profit, the balance sheet and the P&L would
    contradict each other on the same screen, which is precisely the
    failure `income_statement`'s own module docstring says the GL-derived
    design exists to prevent -- and it is why `test_equity_equals_owners_
    equity_plus_earnings_and_matches_income_statement` in this module's own
    test file calls both functions and diffs the figure directly, rather
    than re-computing an expected value by hand.

    THE BALANCE CHECK. Because the identity above is architectural (it
    follows from `post_journal`'s invariant, not from anything this
    function assumes about its own inputs), `assets_total` and
    `liabilities_total + equity_total` are PROVABLY equal for a healthy
    ledger. "Provably" is exactly the class of claim CLAUDE.md's engineering
    standard distrusts when it is not also MEASURED, so `balance_check`
    below reports both totals, an `is_balanced` boolean (compared to within
    `_epsilon(currency)` -- half the currency's own minor unit, the SAME
    tolerance `general_ledger.post_journal`/`trial_balance` use, never a
    hardcoded `0.005`), and the signed `difference` unconditionally --
    present and 0.0 even when balanced, not merely omitted, so a caller
    never has to infer "no difference" from a missing key. A ledger that
    ever fails this check has corruption somewhere upstream of this
    function (a hand-written `UPDATE`, a bypassed `post_journal` call), and
    this statement is the report built to make that visible rather than
    quietly compute around it.

    DERIVE BY ACCOUNT TYPE, NEVER AN ENUMERATED CODE LIST -- the identical
    design decision `income_statement`'s own module docstring makes (point
    1): this function asks the ledger "which accounts are account_type IN
    ('asset','liability','equity') and what is their balance as of this
    date", never "SELECT ... WHERE code IN ('1000','1100', ...)". A shop
    that adds a new asset/liability/equity account to its chart appears in
    its own balance sheet automatically, with zero edits here.

    AS-OF-DATE SCOPING IS `gl_journals.business_date`, NEVER `posted_at` --
    the identical reasoning `income_statement`'s module docstring point 3
    gives in full; not repeated here.

    BRANCH SCOPING MATCHES `income_statement` EXACTLY (module docstring
    point 4): branch-less journals ride along with EVERY branch's report
    (`gj.branch_id IS NULL OR gj.branch_id = ?`), so summing every
    individual branch's balance sheet double-counts a branch-less figure
    once per branch, while the whole-company report (no branch filter) is
    always exactly correct. See that point's own comment for why including
    rather than excluding them was the deliberate choice.

    Returns a dict:
        currency               -- this company's base currency code
        as_of_date              -- echoed back
        branch_id               -- echoed back
        assets                  -- {total, accounts: [{code, name, amount}]}
        liabilities             -- {total, accounts: [{code, name, amount}]}
        equity                  -- {owners_equity, retained_earnings, total,
                                     accounts: [{code, name, amount}]} --
                                     `accounts` is the ledger's own equity-
                                     type accounts (3000 today); `retained_
                                     earnings` is the derived, never-closed
                                     figure described above, NOT a ledger
                                     account and never listed in `accounts`
        total_liabilities_and_equity -- liabilities.total + equity.total
        balance_check           -- {assets_total, liabilities_and_equity_total,
                                     is_balanced, difference} -- see above
        has_activity            -- False when NOT ONE asset/liability/equity
                                     account has ever moved as of this date
                                     AND `income_statement`'s own all-time
                                     has_activity is also False -- lets a
                                     presentation layer say "nothing has
                                     been posted" instead of a tidy set of
                                     zeros for what is really an empty
                                     ledger
        cost_basis_incomplete   -- carried straight from the all-time
                                     `income_statement` call this function
                                     already makes for retained_earnings
                                     (see module docstring point 5) -- when
                                     True, `cost_of_goods_sold` being
                                     understated means BOTH retained_
                                     earnings (overstated net profit) AND
                                     the Inventory account (post_cogs's
                                     matching Cr Inventory leg was skipped
                                     for the same zero-cost lines, so
                                     Inventory is left overstated by the
                                     identical unknown amount) are affected
                                     -- this statement carries the SAME
                                     flag `income_statement` does rather
                                     than inventing an Inventory-specific
                                     one, because it is the identical root
                                     cause surfacing on two different line
                                     items of two different statements
        cost_basis_incomplete_sale_count -- how many distinct sales that is
    """
    gl.ensure_default_chart(conn, cid)
    # Validated the same way `income_statement` validates its own dates --
    # `_parse_period_date` raises `StatementPeriodError` on anything that is
    # not a real 'YYYY-MM-DD' calendar date.
    #
    # P0 round 2 (item B2): the RETURNED `date` object now IS needed here --
    # the paragraph this comment used to carry ("not needed further here,
    # the SQL below compares the original STRING") described the exact bug,
    # not a design choice: `_parse_period_date` is lenient about a
    # non-zero-padded month/day ('2026-9-1' parses to the same date as
    # '2026-09-01'), but `gl_journals.business_date` is always stored
    # zero-padded ISO and SQLite compares TEXT lexicographically, which
    # disagrees with calendar order the instant one side is not padded
    # ('2026-09-15' < '2026-9-1' as TEXT). A caller passing '2026-9-1' got
    # silently wrong balances with no error anywhere. Reassigning
    # `as_of_date` to the parsed date's own `.isoformat()` (always padded)
    # closes that for every use of the variable below, including the
    # `income_statement` call this function makes for retained_earnings.
    as_of_date = _parse_period_date('as_of_date', as_of_date).isoformat()

    currency = _resolve_currency(conn, cid)
    quant = currency_quantum(currency)
    eps = _epsilon(currency)

    # Branch-less journals ride along with EVERY branch filter -- see this
    # function's own docstring and income_statement's module docstring
    # point 4. Short aliases (`gj`/`fl`) inside the subquery only, matching
    # income_statement's identical query shape immediately above in this
    # file.
    branch_sql = ''
    params = [cid, as_of_date]
    if branch_id is not None:
        branch_sql = ' AND (gj.branch_id IS NULL OR gj.branch_id = ?)'
        params.append(branch_id)

    # LEFT JOIN, not JOIN: a seeded-but-never-posted account (1200/3000 on a
    # fresh install -- see DEFAULT_CHART's own comment) must still be
    # queryable, not vanish from the join entirely -- matching trial_
    # balance's identical reasoning. The date filter lives INSIDE the
    # subquery's WHERE, not appended outside, for the identical reason
    # income_statement's own comment gives: an account with real lifetime
    # activity but nothing ON OR BEFORE this specific date still comes back
    # as a LEFT-JOIN NULL (COALESCE'd to 0), rather than vanishing.
    rows = conn.execute(
        "SELECT a.code, a.name, a.account_type, a.normal_balance, "
        "       COALESCE(SUM(fl.debit), 0) AS total_debit, "
        "       COALESCE(SUM(fl.credit), 0) AS total_credit "
        "FROM gl_accounts a "
        "LEFT JOIN ( "
        "    SELECT gjl.account_id, gjl.debit, gjl.credit "
        "    FROM gl_journal_lines gjl "
        "    JOIN gl_journals gj ON gj.id = gjl.journal_id "
        "    WHERE gj.company_id = ? AND gj.business_date <= ?"
        + branch_sql +
        ") fl ON fl.account_id = a.id "
        "WHERE a.company_id = ? AND a.account_type IN ('asset', 'liability', 'equity') "
        "GROUP BY a.id ORDER BY a.code",
        (*params, cid)
    ).fetchall()

    bs_has_activity = False
    totals = {'asset': Decimal('0'), 'liability': Decimal('0'), 'equity': Decimal('0')}
    accounts = {'asset': [], 'liability': [], 'equity': []}

    for r in rows:
        debit = Decimal(str(r['total_debit']))
        credit = Decimal(str(r['total_credit']))
        if debit == 0 and credit == 0:
            continue  # never posted to as of this date -- omit, matching income_statement
        bs_has_activity = True
        acct_type = r['account_type']
        canonical = _BALANCE_SHEET_TYPE_CANONICAL_NORMAL[acct_type]
        net = (debit - credit) if canonical == 'debit' else (credit - debit)
        totals[acct_type] += net
        accounts[acct_type].append({
            'code': r['code'],
            'name': r['name'],
            'amount': float(net.quantize(quant, rounding=ROUND_HALF_UP)),
        })

    # ── Retained earnings -- see this function's own docstring's "THE
    # NO-PERIOD-CLOSE PROBLEM" section for the full algebra. '0001-01-01' is
    # the SAME "all recorded history" sentinel core/retail/metrics.py's own
    # `period_all_time` uses -- not invented here.
    earnings = income_statement(conn, cid, '0001-01-01', as_of_date, branch_id=branch_id)
    retained_earnings = Decimal(str(earnings['net_profit']))
    owners_equity = totals['equity']
    equity_total = owners_equity + retained_earnings

    # An empty ledger has no BS-account activity AND no revenue/expense
    # activity either (a sale always touches an asset account, so the two
    # can never disagree in practice -- see the docstring's Returns section
    # -- but both are checked so this claim does not silently rely on that
    # coincidence holding forever).
    has_activity = bs_has_activity or earnings['has_activity']

    assets_total = totals['asset']
    liabilities_total = totals['liability']
    liabilities_and_equity_total = liabilities_total + equity_total
    difference = assets_total - liabilities_and_equity_total

    return {
        'currency': currency,
        'as_of_date': as_of_date,
        'branch_id': branch_id,
        'assets': {
            'total': float(assets_total.quantize(quant, rounding=ROUND_HALF_UP)),
            'accounts': accounts['asset'],
        },
        'liabilities': {
            'total': float(liabilities_total.quantize(quant, rounding=ROUND_HALF_UP)),
            'accounts': accounts['liability'],
        },
        'equity': {
            'owners_equity': float(owners_equity.quantize(quant, rounding=ROUND_HALF_UP)),
            'retained_earnings': float(retained_earnings.quantize(quant, rounding=ROUND_HALF_UP)),
            'total': float(equity_total.quantize(quant, rounding=ROUND_HALF_UP)),
            'accounts': accounts['equity'],
        },
        'total_liabilities_and_equity': float(liabilities_and_equity_total.quantize(quant, rounding=ROUND_HALF_UP)),
        'balance_check': {
            'assets_total': float(assets_total.quantize(quant, rounding=ROUND_HALF_UP)),
            'liabilities_and_equity_total': float(liabilities_and_equity_total.quantize(quant, rounding=ROUND_HALF_UP)),
            # Compared BEFORE quantizing for display -- the same "raw Decimal
            # vs. eps" convention general_ledger.post_journal/trial_balance
            # use for their own balance invariant, never the rounded display
            # figure.
            'is_balanced': abs(difference) <= eps,
            'difference': float(difference.quantize(quant, rounding=ROUND_HALF_UP)),
        },
        'has_activity': has_activity,
        'cost_basis_incomplete': earnings['cost_basis_incomplete'],
        'cost_basis_incomplete_sale_count': earnings['cost_basis_incomplete_sale_count'],
    }


#: source_type (gl_journals.source_type) -> a shopkeeper-readable label for
#: the cash movement it represents. See `cash_flow`'s own module-docstring
#: section ("GROUPED BY gl_journals.source_type") for why grouping at the
#: DOCUMENT level, not the individual account level, is the honest reading
#: of "what the other side of this cash movement was".
#:
#: 'supplier_payment' and 'po_payment' are DELIBERATELY merged onto the same
#: label -- `general_ledger.post_supplier_payment`'s own docstring: "ONE
#: helper for BOTH `supplier_payment`... and `pay_purchase_order`... the
#: accounting entry is identical in both cases". Two source_types, one line
#: a shopkeeper reads as "money I paid suppliers".
#:
#: 'purchase_order' stays a SEPARATE line from those two on purpose: its cash
#: leg (`post_purchase_order`'s optional `amount_paid` down payment) sits
#: alongside a Dr Inventory line, not an Accounts Payable settlement -- "cash
#: paid to receive goods" is a different fact from "cash paid to clear a debt
#: already on the books", even though both eventually reach the same
#: supplier's pocket.
#:
#: A source_type NOT in this map still appears in the statement (the SQL
#: below is not filtered by this dict's keys) with a generic, `source_type`-
#: derived label -- see `cash_flow`'s own fallback -- so a future cash-
#: touching document type is reported, merely unprettified, never dropped.
_CASH_FLOW_LABELS = {
    'sale': 'Cash Sales Receipts',
    'return': 'Refunds Paid to Customers',
    'customer_payment': 'Customer Account Payments Received',
    'supplier_payment': 'Payments to Suppliers',
    'po_payment': 'Payments to Suppliers',
    'purchase_order': 'Cash Paid for Inventory Purchases',
    # MONEY-3 M3 (v51): a cheque reaching the bank (customer cheque cleared = cash in, supplier
    # cheque cleared = cash out). One label for both: the sign of the line says which.
    'cheque': 'Cheques Cleared at the Bank',
    # MONEY-4b (v52): money out that is not a purchase -- drawer paid-outs and expenses.
    'cash_movement': 'Paid Out of the Till',
    'expense': 'Expenses Paid',
}


def _cash_balance(balance_sheet_result):
    """`gl.ACCOUNT_CASH`'s own balance out of a `balance_sheet()` result's
    `assets.accounts` list -- 0.0 if the cash account has never been posted
    to as of that snapshot (an account with no activity is OMITTED from
    `balance_sheet`'s `accounts` list, matching that function's own "nothing
    to report" convention, never listed at a phantom 0). Reading it out of an
    already-computed `balance_sheet()` result, rather than summing
    `gl_journal_lines` a second time, is the whole point of `cash_flow`'s own
    "reuse balance_sheet, never a second cash derivation" design -- see this
    module's docstring section on the reconciliation.
    """
    # MONEY-5 (v55): "cash" is no longer one account. Tenders land in 1010 Cash in Drawer / 1020
    # Bank / 1030 Card Clearing / 1040 CliQ-Wallet, and the pre-v55 history stays in 1000, so the
    # statement's cash is the SUM over `gl.CASH_LIKE_CODES`. Reading only 1000 would show a shop's
    # closing cash as zero the day it upgraded.
    total = Decimal('0')
    for account in balance_sheet_result['assets']['accounts']:
        if account['code'] in gl.CASH_LIKE_CODES:
            total += Decimal(str(account['amount']))
    return total


def cash_flow(conn, cid, start_date, end_date, branch_id=None):
    """The cash flow statement for company `cid` over `[start_date,
    end_date]`, BOTH ENDS INCLUSIVE -- the identical inclusive-period
    convention `income_statement` documents for itself. `start_date`/
    `end_date` are `'YYYY-MM-DD'` strings; `branch_id`, when given, is an
    integer `branches.id` (or `None` for the whole company) -- this function
    trusts its caller to have already resolved/validated that value, the
    same "callers pass clean types" contract `income_statement`/`balance_
    sheet` both document for themselves (the route does the query-string
    parsing and multi-branch-scope coercion; see api/retail_api.py's
    `report_cash_flow`).

    DIRECT METHOD. See this module's own docstring, "THE CASH FLOW
    STATEMENT, AND WHY IT IS THE DIRECT METHOD", for the full reasoning: this
    codebase has a real ledger with every cash movement individually posted
    and traceable to a source document, so reconstructing cash from net
    profit and working-capital deltas (the indirect method) would be
    building an approximation of a number this function can simply read.

    Raises `StatementPeriodError` (reusing `income_statement`'s own
    exception type, exactly like `balance_sheet` does) if `start_date`/
    `end_date` are not valid dates or `start_date` is after `end_date`.
    Never silently swaps them, never silently defaults to "today".

    BRANCH SCOPING MATCHES `income_statement`/`balance_sheet` EXACTLY
    (that function's module-docstring point 4): branch-less journals ride
    along with EVERY branch's report, so summing every individual branch's
    cash flow statement double-counts a branch-less figure once per branch,
    while the whole-company report (no branch filter) is always exactly
    correct.

    Returns a dict:
        currency                 -- this company's base currency code
        method                   -- always 'direct' -- see this module's own
                                     docstring for why
        period                   -- {start_date, end_date, branch_id} echoed
                                     back
        opening_cash             -- `gl.ACCOUNT_CASH`'s balance the day
                                     before `start_date`, read via
                                     `balance_sheet` (0 when `start_date` is
                                     the '0001-01-01' all-time sentinel --
                                     there is no earlier day to ask about)
        closing_cash             -- `gl.ACCOUNT_CASH`'s balance as of
                                     `end_date`, read via `balance_sheet` --
                                     the AUTHORITATIVE figure this statement
                                     reports as "closing cash", independent
                                     of this function's own `total_cash_in`/
                                     `total_cash_out` summation
        total_cash_in             -- sum of every debit this period posted to
                                     `gl.ACCOUNT_CASH` (an asset account:
                                     debit increases it)
        total_cash_out            -- sum of every credit this period posted
                                     to `gl.ACCOUNT_CASH`
        net_change_in_cash        -- total_cash_in - total_cash_out
        operating_activities      -- {cash_in: {total, lines: [{label,
                                     amount, source_types}]}, cash_out:
                                     {total, lines: [...]}, net} -- see
                                     `_CASH_FLOW_LABELS` above for how a
                                     journal's `source_type` becomes a line's
                                     `label`; `source_types` lists which raw
                                     source_type(s) were merged onto that
                                     label (traceability, not display)
        investing_activities      -- always `None` -- see this module's own
                                     docstring, "OPERATING ONLY"
        financing_activities      -- always `None`, same reason
        investing_and_financing_note -- a sentence explaining the `None`s
                                     above, for a presentation layer to show
                                     in place of an (empty) section
        reconciliation            -- {opening_cash, net_change_in_cash,
                                     computed_closing_cash (opening_cash +
                                     net_change_in_cash, this function's OWN
                                     prediction), closing_cash (balance_
                                     sheet's answer -- duplicated here so the
                                     whole check reads from one dict),
                                     is_balanced, difference} -- the
                                     identical is_balanced/difference SHAPE
                                     `balance_sheet`'s own `balance_check`
                                     uses, compared to within
                                     `_epsilon(currency)`, never inferred
                                     from "the architecture guarantees it"
        has_activity               -- False when NOT ONE journal posted a
                                     cash movement in this period -- lets a
                                     presentation layer say "nothing moved"
                                     instead of a tidy set of zeros for what
                                     is really an empty period

    NO `cost_basis_incomplete` HERE, DELIBERATELY -- unlike income_statement/
    balance_sheet, which both carry it (module docstring point 5). That flag
    exists because an unknown `sale_items.unit_cost` UNDERSTATES `gl.
    ACCOUNT_COGS` (5900), and COGS never touches Cash (1000) at all --
    `post_cogs` is Dr Cost of Goods Sold / Cr Inventory, a memo entry with no
    cash leg. A cash flow statement built off account 1000 alone is
    therefore unaffected by that honesty caveat by construction, not by
    oversight; inventing the flag here would imply a real cash figure is
    ever in doubt for the reason it is in doubt on the other two statements.
    """
    gl.ensure_default_chart(conn, cid)
    start = _parse_period_date('start_date', start_date)
    end = _parse_period_date('end_date', end_date)
    if start > end:
        raise StatementPeriodError(
            f"start_date {start_date!r} is after end_date {end_date!r} -- "
            f"a reporting period cannot run backwards."
        )
    # P0 round 2 (item B2): same normalize-not-just-validate fix as
    # income_statement's identical two lines above in this file (see that
    # comment for the full 'gl_journals.business_date is TEXT-compared'
    # reasoning) -- this function's own `period_params` a few lines down
    # binds these two strings directly, so a non-zero-padded caller must be
    # normalized here too, not just have its calendar-date validity checked.
    start_date, end_date = start.isoformat(), end.isoformat()

    currency = _resolve_currency(conn, cid)
    quant = currency_quantum(currency)
    eps = _epsilon(currency)

    def q(value):
        return float(value.quantize(quant, rounding=ROUND_HALF_UP))

    # ── Direct-method cash movements, grouped by the document that caused
    # them (gl_journals.source_type) -- see this module's own docstring
    # section on why the document level, not an individual counter-account,
    # is the honest grain for "what the other side was". Branch-less
    # journals ride along with EVERY branch filter, the identical convention
    # income_statement/balance_sheet both use (see either's own docstring,
    # point 4) -- short aliases (`gj`/`gjl`/`a`) inside this query only.
    branch_sql = ''
    # MONEY-5 (v55): every cash-like account, not just 1000 (see `_cash_balance`).
    _cash_marks = ','.join('?' for _ in gl.CASH_LIKE_CODES)
    period_params = [cid, *gl.CASH_LIKE_CODES, cid, start_date, end_date]
    if branch_id is not None:
        branch_sql = ' AND (gj.branch_id IS NULL OR gj.branch_id = ?)'
        period_params.append(branch_id)

    # INNER JOINs throughout, deliberately unlike income_statement's/balance_
    # sheet's LEFT JOIN over the whole chart: this query starts from "which
    # journal LINES actually touched the cash account", not "every account,
    # padded with zeros where nothing happened" -- there is exactly one cash
    # account, so a row here always means real activity, and an empty result
    # set (rather than one zero-value row) is the correct "nothing moved"
    # signal `has_activity` below reads.
    rows = conn.execute(
        "SELECT gj.source_type, "
        "       COALESCE(SUM(gjl.debit), 0) AS total_debit, "
        "       COALESCE(SUM(gjl.credit), 0) AS total_credit "
        "FROM gl_journal_lines gjl "
        "JOIN gl_journals gj ON gj.id = gjl.journal_id "
        "JOIN gl_accounts a ON a.id = gjl.account_id "
        "WHERE gj.company_id = ? AND a.code IN (" + _cash_marks + ") AND a.company_id = ? "
        "AND gj.business_date >= ? AND gj.business_date <= ?"
        + branch_sql +
        " GROUP BY gj.source_type ORDER BY gj.source_type",
        period_params
    ).fetchall()

    cash_in_lines = {}   # label -> {'amount': Decimal, 'source_types': [str, ...]}
    cash_out_lines = {}  # label -> {'amount': Decimal, 'source_types': [str, ...]}
    total_cash_in = Decimal('0')
    total_cash_out = Decimal('0')

    for r in rows:
        source_type = r['source_type'] or 'other'
        debit = Decimal(str(r['total_debit']))
        credit = Decimal(str(r['total_credit']))
        label = _CASH_FLOW_LABELS.get(source_type, source_type.replace('_', ' ').strip().title())
        if debit > 0:
            bucket = cash_in_lines.setdefault(label, {'amount': Decimal('0'), 'source_types': []})
            bucket['amount'] += debit
            bucket['source_types'].append(source_type)
            total_cash_in += debit
        if credit > 0:
            bucket = cash_out_lines.setdefault(label, {'amount': Decimal('0'), 'source_types': []})
            bucket['amount'] += credit
            bucket['source_types'].append(source_type)
            total_cash_out += credit

    def _lines(bucket_map):
        return [
            {'label': label, 'amount': q(b['amount']), 'source_types': sorted(b['source_types'])}
            for label, b in sorted(bucket_map.items())
        ]

    net_change_in_cash = total_cash_in - total_cash_out

    # ── Opening/closing cash -- BOTH read via balance_sheet(), never a
    # second SUM over gl_journal_lines. See this module's own docstring
    # section on the reconciliation for why.
    if start <= date.min:
        # No earlier day exists to ask balance_sheet about -- start_date is
        # income_statement's own '0001-01-01' all-time sentinel. Nothing
        # could have happened before the dawn of this ledger's calendar.
        opening_cash = Decimal('0')
    else:
        day_before_start = date.fromordinal(start.toordinal() - 1).isoformat()
        opening_bs = balance_sheet(conn, cid, day_before_start, branch_id=branch_id)
        opening_cash = _cash_balance(opening_bs)

    closing_bs = balance_sheet(conn, cid, end_date, branch_id=branch_id)
    closing_cash = _cash_balance(closing_bs)

    computed_closing_cash = opening_cash + net_change_in_cash
    difference = computed_closing_cash - closing_cash

    return {
        'currency': currency,
        'method': 'direct',
        'period': {'start_date': start_date, 'end_date': end_date, 'branch_id': branch_id},
        'opening_cash': q(opening_cash),
        'closing_cash': q(closing_cash),
        'total_cash_in': q(total_cash_in),
        'total_cash_out': q(total_cash_out),
        'net_change_in_cash': q(net_change_in_cash),
        'operating_activities': {
            'cash_in': {'total': q(total_cash_in), 'lines': _lines(cash_in_lines)},
            'cash_out': {'total': q(total_cash_out), 'lines': _lines(cash_out_lines)},
            'net': q(net_change_in_cash),
        },
        'investing_activities': None,
        'financing_activities': None,
        'investing_and_financing_note': (
            "No investing or financing accounts exist in this company's chart of "
            "accounts (no fixed-asset, loan, or owner-contribution/draw account -- "
            "see general_ledger.DEFAULT_CHART), so no journal today can produce an "
            "investing or a financing cash movement. Every cash movement this "
            "period is operating. Adding such an account to the chart would make "
            "this statement report it automatically, with no change to this module."
        ),
        'reconciliation': {
            'opening_cash': q(opening_cash),
            'net_change_in_cash': q(net_change_in_cash),
            'computed_closing_cash': q(computed_closing_cash),
            'closing_cash': q(closing_cash),
            'is_balanced': abs(difference) <= eps,
            'difference': q(difference),
        },
        'has_activity': bool(rows),
    }
