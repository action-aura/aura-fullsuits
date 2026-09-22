"""Aura Retail -- the double-entry general ledger core (schema v38,
ROADMAP.md's 2026-09-20 "retail schema v38 CLAIMED: a real general ledger"
entry). Read that entry first -- it states the scope this module exists to
fill and, just as importantly, what it deliberately does NOT do (period
close, fiscal-year rollover, retained-earnings posting, multi-currency, a
balance sheet / P&L presentation layer -- each needs this core to exist
first and gets its own version).

WHY THIS EXISTS. CLAUDE.md had said for a while that there is no accounting
at all in this product: no chart of accounts, no general ledger, no trial
balance. The one artefact that existed, `journal_entries`
(database/schema.py), is a fossil from the monolith extraction that has
never had a single INSERT or SELECT in this codebase's history -- free-text
account names instead of rows, one debit/credit pair instead of a balanced
SET of lines, and `amount REAL` quantized nowhere. It is left in place
(untouched data, zero readers) and marked superseded in schema.py; nothing
here reads or writes it.

WHAT THIS MODULE IS, STRUCTURALLY: a "hook" module in the reorder_hook.py /
whatsapp_hook.py / einvoice_adapter.py sense -- it takes a live connection
and performs real writes -- NOT a pure, no-database module in the
pricing.py / cheques.py / returns_settlement.py sense. That distinction
matters here more than it does for those hooks: reorder_hook.py and
einvoice_adapter.py are deliberately called AFTER a sale's own transaction
has already committed, on their OWN connection, specifically so a failure
in the hook can never roll back or block the sale (see reorder_hook.py's
own module docstring). GL posting is the opposite: it is called BEFORE the
document's own `conn.commit()`, on the SAME connection and the SAME
transaction, because an unbalanced journal is exactly the kind of defect
that must abort the whole document rather than let it commit silently
wrong -- see `post_journal`'s own docstring for what "the balance invariant
enforced at write time" means in practice.

THE THREE TABLES (database/schema.py::_migrate_add_general_ledger has the
full column-by-column reasoning; this module only restates what it depends
on):

    gl_accounts       -- chart of accounts. company_id-scoped, seeded with
                         a small default chart by `ensure_default_chart`.
    gl_journals       -- one posted event: business date, reference, memo,
                         the source document type+id it came from,
                         company_id AND branch_id scoped.
    gl_journal_lines  -- the balanced set of debit/credit legs for one
                         journal. No company_id of its own -- scoped
                         entirely through journal_id, the cheque_events /
                         stock_transfer_items shape (a line has no meaning
                         outside the journal it belongs to).

Both gl_journals and gl_journal_lines carry a BEFORE UPDATE / BEFORE DELETE
trigger pair that RAISE(ABORT) unconditionally -- correcting a mistake
means posting a REVERSING journal (`reverse_journal` below), never
rewriting or removing a posted one. This is enforced at the database level,
not merely by "nothing in this file issues an UPDATE" discipline, because
retrofitting that guarantee after a shop has live data is far more
expensive than shipping it from the first commit (CLAUDE.md's engineering
standard, section 1: "reserve/enforce it now" beats "add a test that hopes
nobody does it later").

MONEY. Every debit/credit is stored the same way `payments.amount` is
stored after v37's requantization: a Decimal, quantized to the currency's
own minor unit via `commercial_runtime.currency.quantize_money`
(ROUND_HALF_UP), converted to `float` only at the SQLite boundary --
never a second money convention, never a bare `round(x, 2)`. The balance
check that decides whether a journal may post at all is `_gl_epsilon`,
computed from the SAME `currency_quantum` table `_money_epsilon` in
api/retail_api.py reads -- half the currency's own minor unit, e.g. half a
fil on JOD, never a hardcoded `0.005`. It is duplicated here rather than
imported because api/ imports core/, never the reverse (CLAUDE.md); the
formula is one line and reads the one shared table, so there is exactly
one place the ANSWER ("what is JOD's minor unit") can drift, even though
the trivial `/2` arithmetic is written twice.

`currency` IS REQUIRED on every public function here, with no
optional-defaults-to-2dp escape hatch. Every OTHER money-optional-currency
default in this codebase exists for exactly one reason: byte-compatibility
with callers that predate the currency-aware fix (see `_money`'s own
docstring). This module has no such callers to stay compatible with -- it
is new in v38 -- so carrying that same escape hatch here would only
reintroduce the defect class an entire correction round already existed to
close, for no compatibility benefit at all.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from commercial_runtime.currency import (
    DEFAULT_BASE_CURRENCY,
    currency_quantum,
    quantize_money,
)

# ── Chart of accounts -- codes, referenced by both retail_api.py call sites
#    and this module's own posting helpers, so a typo in either becomes an
#    import-time NameError instead of a silent "posted to the wrong bucket"
#    -----------------------------------------------------------------------
ACCOUNT_CASH = '1000'
ACCOUNT_AR = '1100'
ACCOUNT_INVENTORY = '1200'
ACCOUNT_AP = '2000'
ACCOUNT_TAX_PAYABLE = '2100'
ACCOUNT_EQUITY = '3000'
ACCOUNT_SALES_REVENUE = '4000'
ACCOUNT_SALES_RETURNS = '4900'
ACCOUNT_LOYALTY_EXPENSE = '5000'
ACCOUNT_COGS = '5900'

#: (code, name, account_type, normal_balance, parent_code). Parents are
#: listed before children -- `ensure_default_chart` inserts in this order
#: and resolves `parent_code` -> `parent_id` by looking the parent up, so a
#: forward reference here would silently seed a NULL parent_id instead of
#: failing loudly.
#:
#: DELIBERATELY SMALL: this is the "small sensible default chart" the
#: ROADMAP.md v38 entry asks for, not a full SME chart of accounts.
#:
#: 1200 (Inventory) and 5900 (Cost of Goods Sold) WERE seeded-but-unposted
#: when v38 shipped this chart -- at the time this product had no per-unit
#: inventory valuation (`products.cost_price` was a single "current cost"
#: field, not a weighted-average/FIFO ledger tied to actual stock
#: movements), so booking COGS at the moment of sale would have been
#: inventing a valuation this codebase did not actually track. `post_
#: purchase_order` already posted 1200 even then (a purchase is a real,
#: already-settled cash/AP event regardless of valuation method); 5900 was
#: the one account genuinely waiting.
#:
#: THAT WAIT IS OVER as of schema v39 (`products.avg_cost`, a moving
#: weighted average maintained by `receive_purchase_order`, and `sale_
#: items.unit_cost`, the per-line snapshot taken at the moment of sale --
#: see database/schema.py's v39 migration docstring for the full design and
#: WHY a moving average rather than FIFO). `post_cogs` below now posts 5900
#: on every sale that has a known cost basis (Dr COGS / Cr Inventory) and
#: reverses it on a return of those same lines (Cr COGS / Dr Inventory) --
#: see that function's own docstring for the epsilon-gated "no cost basis,
#: no journal" case, which is deliberately NOT an error: it is what an
#: unknown cost basis (a pre-v39 sale, or a product that has never been
#: received against) must mean here, exactly like `sale_items.unit_cost`
#: itself treats its own zero.
#:
#: 3000 (Owner's Equity) is seeded for a different, still-current reason:
#: `_migrate_add_general_ledger`'s own opening-balance backfill needs a
#: plug account for pre-v38 AR/AP history, and a real install eventually
#: needs an equity account regardless of whether this module ever posts to
#: it directly.
DEFAULT_CHART = (
    ('1000', 'Cash and Bank', 'asset', 'debit', None),
    ('1100', 'Accounts Receivable', 'asset', 'debit', None),
    ('1200', 'Inventory', 'asset', 'debit', None),
    ('2000', 'Accounts Payable', 'liability', 'credit', None),
    ('2100', 'Tax Payable', 'liability', 'credit', None),
    ('3000', "Owner's Equity", 'equity', 'credit', None),
    ('4000', 'Sales Revenue', 'revenue', 'credit', None),
    ('4900', 'Sales Returns and Allowances', 'revenue', 'debit', '4000'),
    ('5000', 'Loyalty Program Expense', 'expense', 'debit', None),
    ('5900', 'Cost of Goods Sold', 'expense', 'debit', None),
)


class GeneralLedgerImbalanceError(Exception):
    """Raised by `post_journal` when a journal's debits and credits do not
    agree to within half the currency's own minor unit, or when every line
    handed to it was zero (a journal that would post nothing). Deliberately
    NOT caught anywhere in this module -- see the module docstring's "WHAT
    THIS MODULE IS, STRUCTURALLY" section for why letting this propagate
    into the caller's existing `except Exception: conn.rollback()` handler
    is the correct behaviour, not a gap: it means the whole document (the
    sale, the return, the payment) is aborted atomically rather than
    committing with a broken ledger beside it."""


class GeneralLedgerAccountError(Exception):
    """Raised when a GL account code or journal id this module was asked to
    use does not exist for the given company -- always a programming error
    (a typo'd account code, a reversal of a journal id that was never
    posted), never a data-entry mistake a caller should recover from
    silently."""


def _today():
    return datetime.now().strftime('%Y-%m-%d')


def _resolve_currency(conn, cid):
    """The company's own base currency, read directly off `retail_settings`
    -- the identical query and the identical never-raise-default-to-JOD
    contract `_company_currency` in api/retail_api.py carries (see that
    function's own docstring for the fresh-Jordanian-install reasoning).
    Duplicated rather than imported for the same layering reason
    `_gl_epsilon` is duplicated below: core/ must not import api/.
    """
    try:
        row = conn.execute(
            "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='base_currency'",
            (cid,)
        ).fetchone()
        return (row[0] if row and row[0] else None) or DEFAULT_BASE_CURRENCY
    except Exception:
        return DEFAULT_BASE_CURRENCY


def _gl_epsilon(currency):
    """Half `currency`'s own minor unit -- see the module docstring's MONEY
    section for why this is computed here rather than imported from
    `api.retail_api._money_epsilon`."""
    try:
        return currency_quantum(currency) / 2
    except Exception:
        return Decimal('0.005')


def ensure_default_chart(conn, cid):
    """Seed `DEFAULT_CHART` for company `cid`, idempotently. Lazy, per-
    company seeding -- the same shape `_seed_methods` (api/retail_api.py)
    already uses for `payment_methods` -- rather than a migration-time
    sweep over every company, because a company is not a row this codebase
    creates explicitly: it simply appears the first time something writes
    with that `company_id`, so "seed it at migration time" cannot reach a
    company that does not exist yet. Called at the top of every public
    posting/reporting function below, so nothing else needs to remember to
    call it first.

    The `COUNT(*)` fast path makes the common case (chart already seeded)
    one query instead of ten INSERT-OR-IGNORE round trips; `INSERT OR
    IGNORE` on the actual seed makes a second, concurrent seed of the same
    company harmless rather than a UNIQUE-constraint 500 -- see
    `idx_gl_accounts_company_code` in the schema migration.
    """
    already = conn.execute(
        "SELECT COUNT(*) FROM gl_accounts WHERE company_id=?", (cid,)
    ).fetchone()[0]
    if already:
        return
    for code, name, account_type, normal_balance, parent_code in DEFAULT_CHART:
        parent_id = None
        if parent_code:
            row = conn.execute(
                "SELECT id FROM gl_accounts WHERE company_id=? AND code=?",
                (cid, parent_code)
            ).fetchone()
            parent_id = row[0] if row else None
        conn.execute(
            "INSERT OR IGNORE INTO gl_accounts "
            "(company_id, code, name, account_type, normal_balance, parent_id) "
            "VALUES (?,?,?,?,?,?)",
            (cid, code, name, account_type, normal_balance, parent_id)
        )


def _account_id(conn, cid, code):
    row = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (cid, code)
    ).fetchone()
    if not row:
        raise GeneralLedgerAccountError(
            f"unknown GL account code {code!r} for company {cid!r} -- "
            f"ensure_default_chart was not called, or DEFAULT_CHART is "
            f"missing this account"
        )
    return row[0]


def post_journal(conn, cid, branch_id, business_date, source_type, source_id,
                  currency, lines, reference=None, memo=None, posted_by=None,
                  reversed_journal_id=None):
    """THE balance invariant, enforced at write time. Inserts one
    `gl_journals` header plus its `gl_journal_lines`, in the caller's own
    transaction (no commit here -- see the module docstring), IF AND ONLY
    IF the lines balance; otherwise nothing is written and
    `GeneralLedgerImbalanceError` is raised.

    `lines` is an iterable of `{'account_code': str, 'debit': ..., 'credit':
    ..., 'memo': ...}` dicts (`memo` optional). Each entry may carry a
    NONZERO 'debit' OR a nonzero 'credit', never both -- a single ledger
    LINE is one side of one leg; a document that needs both a debit and a
    credit on the same account is two lines. A negative debit or credit is
    rejected outright (there is no such thing as a negative debit in double-
    entry bookkeeping; a caller that means "the other side" should swap
    which field it sets, not negate the value). Lines whose debit AND
    credit are both zero are silently dropped -- callers are expected to
    build their line list conditionally (e.g. a walk-in sale has no AR
    leg), but a defensive caller that includes a zero placeholder line
    should not have to special-case it away first.

    THE INVARIANT ITSELF: `sum(debit) == sum(credit)`, checked to within
    `_gl_epsilon(currency)` -- HALF the currency's own minor unit, never a
    hardcoded `0.005` (see the module docstring's MONEY section). This is
    not "close enough" laziness: every figure this module is handed already
    passed through `quantize_money` at its point of origin (create_sale's
    own subtotal/tax/total, create_return's own settlement split), so a
    genuine bug in a caller's arithmetic will be off by whole minor units,
    not fractions of one -- the epsilon exists only to absorb the same
    class of sub-quantum rounding noise `_money_epsilon` already absorbs
    everywhere else in this codebase (see that helper's own docstring for
    the worked JOD 0.006/0.001 example), never to paper over a real
    mismatch.

    ON VIOLATION: raises `GeneralLedgerImbalanceError` and writes NOTHING
    -- no `gl_journals` row, no `gl_journal_lines` rows. The caller's own
    `except Exception: conn.rollback()` (every route this is wired into
    already has one -- create_sale, create_return, customer_payment,
    supplier_payment, pay_purchase_order, create_purchase_order) then
    aborts the WHOLE document, exactly like an `sqlite3.IntegrityError`
    would. That is deliberate: a sale whose GL posting cannot balance is a
    sale this codebase must not silently keep anyway.

    `reversed_journal_id`, when supplied, is stamped straight into the
    INSERT -- never added by a later UPDATE, because `gl_journals` is
    append-only (see the module docstring). `reverse_journal` below is the
    only caller that passes it.
    """
    ensure_default_chart(conn, cid)

    total_debit = Decimal('0')
    total_credit = Decimal('0')
    clean_lines = []
    for raw in lines:
        code = raw['account_code']
        debit = raw.get('debit') or 0
        credit = raw.get('credit') or 0
        d = debit if isinstance(debit, Decimal) else Decimal(str(debit))
        c = credit if isinstance(credit, Decimal) else Decimal(str(credit))
        if d < 0 or c < 0:
            raise ValueError(
                f"GL line for account {code!r} carries a negative debit or "
                f"credit (debit={d} credit={c}) -- swap sides instead of "
                f"negating a value"
            )
        if d and c:
            raise ValueError(
                f"GL line for account {code!r} carries both a debit and a "
                f"credit (debit={d} credit={c}) -- split into two lines"
            )
        if not d and not c:
            continue
        d = quantize_money(d, currency)
        c = quantize_money(c, currency)
        total_debit += d
        total_credit += c
        clean_lines.append((code, d, c, raw.get('memo')))

    if not clean_lines:
        raise GeneralLedgerImbalanceError(
            f"refusing to post an empty journal for {source_type}:{source_id} "
            f"-- every line supplied was zero"
        )

    eps = _gl_epsilon(currency)
    diff = total_debit - total_credit
    if abs(diff) > eps:
        raise GeneralLedgerImbalanceError(
            f"journal for {source_type}:{source_id} does not balance: "
            f"debits={total_debit} credits={total_credit} diff={diff} "
            f"(tolerance is half of {currency}'s own minor unit, {eps})"
        )

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO gl_journals "
        "(company_id, branch_id, business_date, reference, memo, source_type, "
        " source_id, currency, reversed_journal_id, posted_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cid, branch_id, business_date, reference, memo, source_type, source_id,
         currency, reversed_journal_id, posted_by or 'System')
    )
    journal_id = cur.lastrowid
    for code, d, c, line_memo in clean_lines:
        account_id = _account_id(conn, cid, code)
        cur.execute(
            "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) "
            "VALUES (?,?,?,?,?)",
            (journal_id, account_id, float(d), float(c), line_memo)
        )
    return journal_id


def reverse_journal(conn, cid, journal_id, memo=None, posted_by=None, business_date=None):
    """Posts a NEW journal whose lines are the original's with debit and
    credit swapped -- the only sanctioned way to correct a posted journal
    (see the module docstring: `gl_journals`/`gl_journal_lines` are append-
    only, enforced by trigger, so there is no UPDATE path to correct one
    in place). `reversed_journal_id` on the new journal points at the
    original; the original itself carries no "I was reversed" flag (it
    CANNOT -- append-only), so "was this journal reversed" is always a
    query (`SELECT 1 FROM gl_journals WHERE reversed_journal_id=?`), never
    a stored bit that could itself go stale.

    Re-derives the original's branch/currency/reference rather than
    trusting a caller to supply them again, so a reversal can never
    accidentally land in the wrong branch or the wrong currency's
    minor-unit rounding.
    """
    original = conn.execute(
        "SELECT branch_id, business_date, reference, currency "
        "FROM gl_journals WHERE id=? AND company_id=?",
        (journal_id, cid)
    ).fetchone()
    if not original:
        raise GeneralLedgerAccountError(
            f"cannot reverse journal {journal_id}: no such journal for company {cid!r}"
        )
    rows = conn.execute(
        "SELECT a.code AS account_code, l.debit, l.credit, l.memo "
        "FROM gl_journal_lines l JOIN gl_accounts a ON a.id = l.account_id "
        "WHERE l.journal_id=?",
        (journal_id,)
    ).fetchall()
    if not rows:
        raise GeneralLedgerAccountError(
            f"cannot reverse journal {journal_id}: it has no lines"
        )
    swapped = [
        {'account_code': r['account_code'], 'debit': r['credit'], 'credit': r['debit'],
         'memo': r['memo']}
        for r in rows
    ]
    return post_journal(
        conn, cid, original['branch_id'], business_date or original['business_date'],
        'reversal', journal_id, original['currency'], swapped,
        reference=original['reference'], memo=memo or f"Reversal of journal #{journal_id}",
        posted_by=posted_by, reversed_journal_id=journal_id,
    )


def trial_balance(conn, cid):
    """Every seeded account for company `cid`, with its lifetime debit/
    credit totals and net `balance` (signed per the account's own
    `normal_balance` -- positive means "a normal balance for this account
    type", matching how a real trial balance reads).

    COMPANY SCOPING WITHOUT TOUCHING `gl_journals` AT ALL: `gl_journal_
    lines` carries no `company_id` of its own (see the module docstring),
    but every line's `account_id` was resolved by `_account_id`, which only
    ever returns an id scoped `WHERE company_id=?` -- so
    `gl_journal_lines.account_id` is transitively company-scoped through
    `gl_accounts`, and joining on `a.company_id = ?` alone is already
    correct; no second join to `gl_journals` is needed to avoid a cross-
    tenant leak here.

    `LEFT JOIN`, not `JOIN`: an account `ensure_default_chart` seeded but
    nothing has ever posted to (the common case for 1200/5900/3000 in a
    v38 install -- see DEFAULT_CHART's own comment) must still appear, at
    zero, not vanish from the report.

    Company-wide, all branches, all time -- v38 deliberately ships no
    branch or as-of-date filtering (see the ROADMAP.md entry's "Is NOT"
    list: a presentation/reporting layer is a later wave). Every account
    this function returns is still individually correct for any narrower
    report built on top of `gl_journal_lines`/`gl_journals` directly.
    """
    ensure_default_chart(conn, cid)
    currency = _resolve_currency(conn, cid)
    quant = currency_quantum(currency)

    rows = conn.execute(
        "SELECT a.code, a.name, a.account_type, a.normal_balance, "
        "       COALESCE(SUM(l.debit), 0) AS total_debit, "
        "       COALESCE(SUM(l.credit), 0) AS total_credit "
        "FROM gl_accounts a "
        "LEFT JOIN gl_journal_lines l ON l.account_id = a.id "
        "WHERE a.company_id = ? "
        "GROUP BY a.id ORDER BY a.code",
        (cid,)
    ).fetchall()

    accounts = []
    total_debit_all = Decimal('0')
    total_credit_all = Decimal('0')
    for r in rows:
        debit = Decimal(str(r['total_debit']))
        credit = Decimal(str(r['total_credit']))
        total_debit_all += debit
        total_credit_all += credit
        net = (debit - credit) if r['normal_balance'] == 'debit' else (credit - debit)
        accounts.append({
            'code': r['code'], 'name': r['name'], 'account_type': r['account_type'],
            'normal_balance': r['normal_balance'],
            'debit': float(debit.quantize(quant, rounding=ROUND_HALF_UP)),
            'credit': float(credit.quantize(quant, rounding=ROUND_HALF_UP)),
            'balance': float(net.quantize(quant, rounding=ROUND_HALF_UP)),
        })

    eps = _gl_epsilon(currency)
    return {
        'currency': currency,
        'accounts': accounts,
        'total_debit': float(total_debit_all.quantize(quant, rounding=ROUND_HALF_UP)),
        'total_credit': float(total_credit_all.quantize(quant, rounding=ROUND_HALF_UP)),
        'is_balanced': abs(total_debit_all - total_credit_all) <= eps,
    }


def account_balance(conn, cid, code):
    """One account's signed `balance` from `trial_balance`, or 0.0 if the
    account has never been posted to. Convenience for a caller (a test, a
    future statement route) that wants one figure rather than the whole
    report -- never used internally by this module, which always reads
    `trial_balance` directly when it needs more than one account so the two
    can never disagree about a shared SUM.
    """
    for row in trial_balance(conn, cid)['accounts']:
        if row['code'] == code:
            return row['balance']
    return 0.0


def _account_balance_in(tb, code):
    """Read one account's `balance` OUT OF an already-computed `trial_
    balance()` result, never by calling `trial_balance` (or `account_
    balance`, which does exactly that) again. `subledger_tie` below is
    always handed a trial balance its own caller already paid for -- a
    second call would re-run the identical `SUM(...) ... GROUP BY a.id`
    query and risk disagreeing with the first if anything posted in
    between, which is precisely the "two ledgers that could drift" defect
    class this whole module exists to close. `ensure_default_chart` (run at
    the top of every `trial_balance` call) guarantees ACCOUNT_AR/ACCOUNT_AP
    are always present in `tb['accounts']`, so the 0.0 fallback here is
    unreachable in practice -- kept anyway so a hand-built `tb` fixture (a
    test) degrades safely instead of raising IndexError.
    """
    for row in tb['accounts']:
        if row['code'] == code:
            return row['balance']
    return 0.0


def subledger_tie(conn, cid, tb):
    """THE POINT of exposing the ledger at all (ROADMAP.md's v38 entry:
    "a trial balance that ties back to the AR/AP subledger") -- not just
    the GL's own account balances, but whether they agree with the
    pre-existing `customers`/`suppliers.credit_balance` subledger the AR/AP
    screens already read. Returns
        {'ar_gl', 'ar_subledger', 'ar_ties', 'ap_gl', 'ap_subledger', 'ap_ties'}
    `ar_gl`/`ap_gl` are read OUT OF `tb` (see `_account_balance_in` above)
    rather than re-derived -- `tb` must already be this company's
    `trial_balance(conn, cid)` result, not recomputed here, so the GL side
    of the comparison can never disagree with what a caller already printed
    alongside it.

    GUARDED ON `customers`/`suppliers.credit_balance` EXISTING, the
    identical shape `_migrate_add_general_ledger`'s opening-balance
    backfill uses (database/schema.py -- see that migration's own
    docstring, "GUARDED ON credit_balance EXISTING"). That column is added
    LAZILY, at runtime, by api/retail_api.py's `_ensure_credit_schema` --
    it is NOT part of any versioned migration in schema.py -- so a
    brand-new install, or any install whose runtime has never opened the
    credit schema (never rung a credit sale, never taken a customer/
    supplier payment), genuinely does not have the column. That is a TRUE
    "nothing to tie back to" (0.0 subledger side, and the GL side is also
    0.0 because nothing has ever posted to AR/AP either) -- reported as
    `ar_ties=True`/`ap_ties=True`, not a 500 from querying a column that
    was never created.

    Checked PER SIDE, independently -- unlike the opening-balance backfill
    above, which needs BOTH totals together to balance one plug journal and
    so is all-or-nothing on the pair. A route reporting two independent
    facts has no such coupling: a database missing only one of the two
    columns (which `_ensure_credit_schema` never produces today, since it
    adds both in the same call, but nothing prevents a future minimal
    fixture or partial migration from doing so) should still answer
    honestly for the side it can.

    THE EPSILON IS `_gl_epsilon(tb['currency'])` -- HALF the company's own
    currency's minor unit -- never a hardcoded `0.005`. See this module's
    own MONEY section and CLAUDE.md's round-4 corrections ("~25 money gates
    compared against a hardcoded 0.005... never make one of these finer on
    its own") for why a fixed literal here would silently under-report a
    real divergence on any 3-decimal currency (JOD, KWD, BHD, OMR).
    """
    ar_gl = _account_balance_in(tb, ACCOUNT_AR)
    ap_gl = _account_balance_in(tb, ACCOUNT_AP)
    eps = _gl_epsilon(tb['currency'])

    def _credit_balance_sum(table):
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            return 0.0
        if 'credit_balance' not in cols:
            # _ensure_credit_schema has never run on this install for this
            # table -- see docstring's "GUARDED ON credit_balance EXISTING".
            # Nothing to tie back to.
            return 0.0
        row = conn.execute(
            f"SELECT COALESCE(SUM(credit_balance),0) FROM {table} WHERE company_id=?",
            (cid,)
        ).fetchone()
        return float(row[0] or 0)

    ar_sub = _credit_balance_sum('customers')
    ap_sub = _credit_balance_sum('suppliers')

    ar_ties = abs(Decimal(str(ar_gl)) - Decimal(str(ar_sub))) <= eps
    ap_ties = abs(Decimal(str(ap_gl)) - Decimal(str(ap_sub))) <= eps

    return {
        'ar_gl': ar_gl, 'ar_subledger': ar_sub, 'ar_ties': ar_ties,
        'ap_gl': ap_gl, 'ap_subledger': ap_sub, 'ap_ties': ap_ties,
    }


# ── Document posting helpers ─────────────────────────────────────────────
#
# Each of these takes figures the CALLING route (api/retail_api.py) has
# already computed and, in every case, already used to write the document
# itself (sales.total, returns.tender_refund_amount, the exact `amount`
# passed to `_record_payment`/`_adjust_credit`, ...). None of them re-derive
# business logic -- the one formula this module trusts is "the sum of what
# I was handed must balance", which `post_journal` itself checks.

def post_sale(conn, cid, branch_id, sale_id, sale_number, currency,
              cash_amount, ar_amount, loyalty_redeemed_amount,
              tax_amount, revenue_amount, business_date=None, posted_by=None):
    """Dr Cash (tender retained) + Dr Accounts Receivable (credit extended,
    ONLY when the caller's own `is_credit and balance_due > _eps and
    customer_id` gate already fired -- `ar_amount` must be exactly the
    figure already passed to `_adjust_credit`, not independently
    re-derived, or the GL and the subledger could disagree about whether a
    sale became a debt) + Dr Loyalty Program Expense (points redeemed,
    "the shop gave up this much revenue to honour points") against
    Cr Tax Payable + Cr Sales Revenue (subtotal net of discount).

    BALANCES BY CONSTRUCTION whenever the caller's own figures satisfy
    `net_received + balance_due + points_redeemed_amount == amount_due_
    after_points + points_redeemed_amount == total == subtotal -
    discount_amount + tax_amount` -- which create_sale's own arithmetic
    already guarantees to within `_money_epsilon` (see that route's
    precision-pass tests). `post_journal`'s own epsilon tolerance absorbs
    the one documented sub-quantum edge case where it does not hold
    exactly: a JOD sale whose `balance_due` lands strictly between 0 and
    half a fil is, by create_sale's OWN `> _eps` gate, never booked to AR
    at all -- see `_money_epsilon`'s own worked 0.006/0.001 example. In
    that case `cash_amount` (create_sale's real `net_received`) can differ
    from `total - ar_amount - loyalty_redeemed_amount` by up to that same
    half-fil, which is exactly what the tolerance exists to absorb rather
    than reject a real, already-shipped sale.
    """
    lines = []
    if cash_amount and cash_amount > 0:
        lines.append({'account_code': ACCOUNT_CASH, 'debit': cash_amount,
                       'memo': 'Tender retained'})
    if ar_amount and ar_amount > 0:
        lines.append({'account_code': ACCOUNT_AR, 'debit': ar_amount,
                       'memo': 'Credit extended to customer'})
    if loyalty_redeemed_amount and loyalty_redeemed_amount > 0:
        lines.append({'account_code': ACCOUNT_LOYALTY_EXPENSE, 'debit': loyalty_redeemed_amount,
                       'memo': 'Loyalty points redeemed'})
    if tax_amount and tax_amount > 0:
        lines.append({'account_code': ACCOUNT_TAX_PAYABLE, 'credit': tax_amount,
                       'memo': 'Sales tax collected'})
    if revenue_amount and revenue_amount > 0:
        lines.append({'account_code': ACCOUNT_SALES_REVENUE, 'credit': revenue_amount,
                       'memo': f'Sale {sale_number}'})
    if not lines:
        # A sale whose total is genuinely zero (every line fully
        # discounted away) has nothing for the GL to say. Not reachable
        # through create_sale today (a zero-total sale is not something
        # the till can ring), but a defensive no-op is cheaper than a
        # GeneralLedgerImbalanceError for a case that is a legitimate
        # "nothing happened" rather than a defect.
        return None
    return post_journal(conn, cid, branch_id, business_date or _today(),
                         'sale', sale_id, currency, lines,
                         reference=sale_number, posted_by=posted_by)


def post_return(conn, cid, branch_id, return_id, return_number, currency,
                 tender_refund_amount, ar_credit_amount, business_date=None,
                 posted_by=None):
    """Cr Cash (cash physically refunded) + Cr Accounts Receivable
    (`ar_forgiven_amount + store_credit_amount` combined -- both are, in
    `_adjust_credit` terms, the SAME `-amount` adjustment to
    `customers.credit_balance`, so this module tracks them as one GL
    account exactly like the subledger tracks them as one column) against
    Dr Sales Returns and Allowances for the SETTLED total.

    THE DEBIT IS DERIVED FROM THE CREDITS, NOT FROM `returns.refund_
    amount`, and that is a deliberate departure from post_sale's shape
    above -- see core/retail/returns_settlement.py's own module docstring,
    "THE INVARIANT THIS WEAKENS": `tender_refund + ar_forgiven +
    store_credit` sums to `refund` only when the original sale ever
    recorded enough consideration to cover it; otherwise it sums to
    `min(refund, tender_available + balance_due_remaining)`, strictly LESS
    than `refund` (the DEFECT-1B sub-quantum case). Booking `refund`
    itself as the contra-revenue debit would then leave a genuine,
    non-epsilon gap on the credit side with nowhere to put it. Booking the
    SETTLED amount instead -- what the credits actually sum to -- makes
    this journal balance BY CONSTRUCTION, for every return this codebase
    can produce, and it is also the more honest entry: it records exactly
    the value the shop's own books can account for, not a notional goods
    value the return never actually collected consideration for.
    """
    settled = Decimal('0')
    lines = []
    if tender_refund_amount and tender_refund_amount > 0:
        lines.append({'account_code': ACCOUNT_CASH, 'credit': tender_refund_amount,
                       'memo': 'Cash refunded'})
        settled += Decimal(str(tender_refund_amount))
    if ar_credit_amount and ar_credit_amount > 0:
        lines.append({'account_code': ACCOUNT_AR, 'credit': ar_credit_amount,
                       'memo': 'AR forgiven / store credit issued'})
        settled += Decimal(str(ar_credit_amount))
    if settled <= 0:
        # Nothing was actually settled against this return (the DEFECT-1B
        # edge case, or a return the caller should not have posted at
        # all). Correct to no-op: create_return itself calls neither
        # `_adjust_credit` nor writes a nonzero `tender_refund_amount` in
        # this case, so the GL agreeing by posting nothing is the two
        # ledgers staying in sync, not a gap.
        return None
    lines.append({'account_code': ACCOUNT_SALES_RETURNS, 'debit': settled,
                   'memo': f'Return {return_number}'})
    return post_journal(conn, cid, branch_id, business_date or _today(),
                         'return', return_id, currency, lines,
                         reference=return_number, posted_by=posted_by)


def post_customer_payment(conn, cid, branch_id, reference, currency, amount,
                           business_date=None, posted_by=None):
    """Dr Cash / Cr Accounts Receivable -- a customer paying down what they
    owe. `amount` must be the exact figure already passed to both
    `_record_payment` and `_adjust_credit` at the call site (customer_
    payment in api/retail_api.py), not re-derived.
    """
    if not amount or amount <= 0:
        return None
    lines = [
        {'account_code': ACCOUNT_CASH, 'debit': amount, 'memo': 'Customer payment received'},
        {'account_code': ACCOUNT_AR, 'credit': amount, 'memo': 'Accounts receivable settled'},
    ]
    return post_journal(conn, cid, branch_id, business_date or _today(),
                         'customer_payment', None, currency, lines,
                         reference=reference, posted_by=posted_by)


def post_supplier_payment(conn, cid, branch_id, reference, currency, amount,
                           source_type='supplier_payment', source_id=None,
                           business_date=None, posted_by=None):
    """Dr Accounts Payable / Cr Cash -- the shop paying down what it owes a
    supplier. ONE helper for BOTH `supplier_payment` (a direct account
    payment, `source_id=None`) and `pay_purchase_order` (an instalment
    against one PO, `source_type='po_payment', source_id=po_id`) -- the
    accounting entry is identical in both cases (see api/retail_api.py's
    own `supplier_payment`/`pay_purchase_order`, which differ only in
    which `purchase_orders` row they also touch), so this is one function
    with a doc-metadata parameter rather than two near-duplicates.
    """
    if not amount or amount <= 0:
        return None
    lines = [
        {'account_code': ACCOUNT_AP, 'debit': amount, 'memo': 'Accounts payable settled'},
        {'account_code': ACCOUNT_CASH, 'credit': amount, 'memo': 'Payment to supplier'},
    ]
    return post_journal(conn, cid, branch_id, business_date or _today(),
                         source_type, source_id, currency, lines,
                         reference=reference, posted_by=posted_by)


def post_purchase_order(conn, cid, branch_id, po_id, po_number, currency,
                         amount_paid, ap_balance, business_date=None, posted_by=None):
    """Dr Inventory against Cr Cash (any down payment) + Cr Accounts
    Payable (whatever remains).

    THE DEBIT IS `amount_paid + ap_balance` -- what the credits actually
    sum to -- NOT the PO's own `total`, and that is the identical
    departure `post_return` above makes from `returns.refund_amount` for
    the identical reason: `create_purchase_order` (api/retail_api.py)
    treats `supplier_id` as OPTIONAL ("supplier_id stays OPTIONAL: `if
    supplier_id:` guards this"), so a PO with no supplier and an unpaid
    `balance` never calls `_adjust_credit` at all -- that balance is not
    tracked ANYWHERE in the existing subledger, on a PO the app already
    allows to be created today. Booking the PO's raw `total` as Inventory
    in that case would debit more than the credits could ever cover,
    which `post_journal`'s balance check would then correctly (but
    unhelpfully) refuse -- BLOCKING a purchase-order flow this module must
    never block. Deriving the debit from what the caller actually passed
    as `amount_paid`/`ap_balance` instead means: (a) this journal balances
    by construction for every PO this codebase can produce, no exceptions,
    (b) the GL's Accounts Payable account always ties exactly to
    `SUM(suppliers.credit_balance)`, because both are driven by the same
    `ap_balance` figure, and (c) the rare supplier-less, partially-unpaid
    PO simply books the goods at what was actually accounted for, which is
    the honest figure rather than a total the shop's own books cannot
    currently explain either.
    """
    accounted = Decimal(str(amount_paid or 0)) + Decimal(str(ap_balance or 0))
    if accounted <= 0:
        return None
    lines = [
        {'account_code': ACCOUNT_INVENTORY, 'debit': accounted,
         'memo': f'Purchase order {po_number}'},
    ]
    if amount_paid and amount_paid > 0:
        lines.append({'account_code': ACCOUNT_CASH, 'credit': amount_paid,
                       'memo': 'Down payment'})
    if ap_balance and ap_balance > 0:
        lines.append({'account_code': ACCOUNT_AP, 'credit': ap_balance,
                       'memo': 'Balance owed to supplier'})
    return post_journal(conn, cid, branch_id, business_date or _today(),
                         'purchase_order', po_id, currency, lines,
                         reference=po_number, posted_by=posted_by)


def post_cogs(conn, cid, branch_id, source_type, source_id, reference, currency,
              cogs_amount, business_date=None, posted_by=None, reverse=False):
    """Dr Cost of Goods Sold / Cr Inventory for `cogs_amount` -- the cost
    basis (schema v39: `products.avg_cost` at the moment of sale, snapshotted
    onto `sale_items.unit_cost`) of goods that left the shelf on a sale --
    or, when `reverse=True`, the MIRROR (Cr Cost of Goods Sold / Dr
    Inventory) for goods a return actually put back on the shelf.

    ONE HELPER FOR BOTH DIRECTIONS, same shape `post_supplier_payment` above
    already uses for `supplier_payment`/`po_payment`: the accounting entry
    is identical either way (swap which side each account lands on), so
    this is one function with a `reverse` flag and a doc-metadata
    `source_type`/`source_id`/`reference` triple, rather than two
    near-duplicates that could drift on which account gets debited. The
    caller passes `source_type='sale'`/`source_id=sale_id` (create_sale) or
    `source_type='return'`/`source_id=return_id` (create_return) -- the
    SAME pair `post_sale`/`post_return` already stamp on their own sibling
    journal for the same document, so `gl_journals` ends up with TWO rows
    per sale (revenue+tender, and cost) sharing one `(source_type,
    source_id)` -- deliberate: `idx_gl_journals_source` carries no UNIQUE
    constraint (see database/schema.py), and "every journal this sale ever
    posted" is exactly what a lookup on that pair should answer.

    `cogs_amount` IS NOT RE-DERIVED HERE. It is `SUM(quantity * unit_cost)`
    over the document's own lines, computed by the caller from figures that
    already exist on the row being posted (create_sale: the SAME `sale_
    items.unit_cost` snapshot the write loop just persisted; create_return:
    the ORIGINAL sale line's snapshot, joined by `sale_item_id`, never the
    product's cost today) -- this module's usual contract, restated at the
    top of the "Document posting helpers" section above: it trusts the sum
    it is handed, it does not go compute one of its own.

    THE EPSILON-GATED EMPTY CASE, stated because it is the one a caller
    could get wrong by analogy with `post_sale`'s `if not lines: return
    None`: this function checks the TOTAL against `_gl_epsilon(currency)`,
    not against zero, and returns None -- posting nothing -- whenever it is
    at or below that half-minor-unit floor. That is not merely "no lines to
    post" (this function only ever has the same two lines, Inventory and
    COGS); it is "every line in this document had an unknown cost basis"
    (schema v39's `sale_items.unit_cost = 0` means NO SNAPSHOT WAS TAKEN,
    never "these goods cost nothing" -- see that migration's own docstring)
    or "this document's total cost basis rounds to functionally zero" for a
    tiny sale. Either way, inventing a balanced Dr/Cr pair around a total
    this module cannot actually vouch for would be the exact manufactured
    figure the v39 migration refused to backfill onto historical rows, for
    a live journal instead of a column -- so the correct behaviour is
    silence, not a zero-value or unbalanced journal, and NOT a blocked
    sale/return either (the caller never treats this return value as
    pass/fail -- see create_sale/create_return's own call sites).
    """
    amount = Decimal(str(cogs_amount or 0))
    if amount <= _gl_epsilon(currency):
        return None
    if reverse:
        lines = [
            {'account_code': ACCOUNT_INVENTORY, 'debit': amount,
             'memo': f'Cost of goods restocked, {reference}'},
            {'account_code': ACCOUNT_COGS, 'credit': amount,
             'memo': f'Return {reference}'},
        ]
    else:
        lines = [
            {'account_code': ACCOUNT_COGS, 'debit': amount,
             'memo': f'Cost of goods sold, {reference}'},
            {'account_code': ACCOUNT_INVENTORY, 'credit': amount,
             'memo': 'Inventory consumed'},
        ]
    return post_journal(conn, cid, branch_id, business_date or _today(),
                         source_type, source_id, currency, lines,
                         reference=reference, posted_by=posted_by)
