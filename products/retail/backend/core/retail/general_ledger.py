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

WAVE 1 (2026-09-26, schema v41, ROADMAP.md's "retail schema v41 CLAIMED: one
set of books across devices" entry, BOOKS-WAVE1-PLAN.md section 2.4): this
module is now also the EMIT side of GL journal sync -- Builder A's own file
under that wave's disjoint-file assignment (section 7). `post_journal` mints
a stable `uid` and derives `source_uid` on every journal it posts, then
queues one `gl_journal` sync-outbox event in the SAME transaction, so a
rollback anywhere in the caller's route removes the journal and its outbox
row together. `journal_sync_payload` is the ONE place that payload is ever
built, and it always re-reads the just-persisted rows rather than trusting
the caller's own inputs. `backfill_legacy_journals` gives every PRE-v41
journal (which has no `uid` at all) a deterministic identity and queues it
too -- see that function's own docstring for why "deterministic" matters
here specifically (a restored backup must not mint a second, different
identity for history it already sent once). None of this changes the
balance invariant itself, or any existing caller's signature -- `post_sale`/
`post_return`/`post_customer_payment`/`post_supplier_payment`/
`post_purchase_order`/`post_cogs`/`reverse_journal` are all unchanged.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from commercial_runtime.currency import (
    DEFAULT_BASE_CURRENCY,
    currency_quantum,
    quantize_money,
)

#: Deterministic uuid5 NAMESPACE for `legacy_journal_uid` below -- a fixed,
#: hardcoded namespace (never regenerated) so the SAME (namespace, name)
#: pair always yields the SAME uuid5 on every machine, every run, forever.
#: `uuid.uuid5(uuid.NAMESPACE_DNS, ...)` is itself deterministic (a pure
#: hash of its two arguments), so computing it here at import time is
#: exactly as fixed as a hardcoded literal would be, and is self-documenting
#: about where it came from -- the same idea `cheques.py`'s own
#: deterministic-uid backfill uses (see that module's docstring).
_GL_LEGACY_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, 'gl-legacy.aura-fullsuits.internal')

#: Deterministic uuid5 NAMESPACE for REVERSAL journals' own `uid` (F3b). A
#: reversal's uid is `uuid5(this, <original journal's uid> + ':reversal')`,
#: so the SAME payment voided independently on two computers before they
#: sync mints the SAME uid on both -- and the sync apply's `ON CONFLICT(uid)
#: DO NOTHING` collapses the two reversals into one row, instead of
#: reversing the money twice. The ORIGINAL's uid (not its integer id) is the
#: name, because integer ids differ per device. Fixed literal, never
#: regenerated: changing it would make two builds disagree about the uid.
_GL_REVERSAL_NAMESPACE = uuid.UUID('6f1c2b8e-4d3a-5e7f-9a10-2c5b7d8e9f31')


def reversal_uid(original_journal_uid):
    """The deterministic uid of the reversal of the journal whose uid is
    `original_journal_uid`. Pure; same input gives the same output on every
    device, every run."""
    return str(uuid.uuid5(_GL_REVERSAL_NAMESPACE, f"{original_journal_uid}:reversal"))


#: Deterministic uuid5 NAMESPACE for the STOCK-BOOKS journals' own `uid`
#: (opening entries, adjustments, transfer send/receive, PO receipt). Same
#: idea as `_GL_REVERSAL_NAMESPACE`: the uid is a pure function of WHAT the
#: journal books, so a re-post (retry, double click, two computers) mints
#: the SAME uid and the sync apply's `ON CONFLICT(uid) DO NOTHING` collapses
#: it. Fixed literal, never regenerated.
_GL_STOCK_NAMESPACE = uuid.UUID('9a7e4c1d-2b6f-5e38-8d40-6c1f3a5b7e92')


def stock_journal_uid(key, kind):
    """The deterministic uid of the stock journal of `kind` for `key`.

    `key` MUST be globally unique across computers: an inventory movement's
    uid, a stock transfer's id (a UUID), or the PO ORDER JOURNAL's uid --
    NEVER a `purchase_orders.id` (a per-computer integer) or a PO number,
    which two computers can hand out to different documents."""
    return str(uuid.uuid5(_GL_STOCK_NAMESPACE, f"{key}:{kind}"))


def _post_once(conn, cid, uid, branch_id, business_date, source_type, source_id,
               currency, lines, reference=None, memo=None, posted_by=None,
               source_uid=None):
    """`post_journal` with a deterministic `uid`, idempotent: if a journal
    with this uid already exists for the company, return ITS id and post
    nothing. This is what makes every STOCK-BOOKS helper safe to call twice
    (a retried request, a re-run after a crash) without booking the stock
    event twice."""
    existing = conn.execute(
        "SELECT id FROM gl_journals WHERE company_id=? AND uid=?", (cid, uid)
    ).fetchone()
    if existing:
        return existing['id']
    return post_journal(conn, cid, branch_id, business_date, source_type,
                        source_id, currency, lines, reference=reference,
                        memo=memo, posted_by=posted_by, uid=uid,
                        source_uid=source_uid)

#: `source_type` -> the LOCAL table whose own `uid` column is this journal's
#: `source_uid`, for every source type that names a real document row.
#: `'reversal'` is handled separately (its `source_uid` is the REVERSED
#: JOURNAL's own `uid`, not a row in either table below) -- see
#: `_derive_source_uid`. Every other source type (`customer_payment`,
#: `supplier_payment`, `po_payment`, `purchase_order`, `opening_balance`)
#: has no cross-device row identity to point at yet (purchase orders are
#: still INTEGER-id-only as of Wave 1 -- see BOOKS-WAVE1-PLAN.md section 3,
#: W4a) and gets `source_uid = NULL`, which is exactly what BOOKS-WAVE1-
#: PLAN.md section 2.2's "source_id" row states ("Otherwise NULL").
_SOURCE_UID_TABLES = {'sale': 'sales', 'return': 'returns'}

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
# STOCK-BOOKS wave: the four accounts the stock events post to. Flat (no
# parent) -- see docs/launch-readiness/target-chart-of-accounts.md for the
# naming/normal-balance rules and the rest of the planned chart.
ACCOUNT_STOCK_IN_TRANSIT = '1250'
ACCOUNT_GOODS_ORDERED = '1260'
ACCOUNT_OPENING_EQUITY = '3900'
ACCOUNT_SHRINKAGE = '5910'

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
    # STOCK-BOOKS wave -- ONLY the codes that wave posts to. Names/types are
    # frozen once released: INSERT OR IGNORE never overwrites a seeded row.
    ('1250', 'Stock in Transit', 'asset', 'debit', None),
    ('1260', 'Goods Ordered Not Received', 'asset', 'debit', None),
    ('3900', 'Opening Balance Equity', 'equity', 'credit', None),
    ('5910', 'Shrinkage and Adjustments', 'expense', 'debit', None),
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

    THE FAST PATH CHECKS EVERY `DEFAULT_CHART` CODE IS PRESENT, NOT MERELY
    THAT THE TABLE IS NON-EMPTY (Wave 1, BOOKS-WAVE1-PLAN.md section 2.4).
    A bare `COUNT(*) FROM gl_accounts WHERE company_id=?` used to short-
    circuit the instant a company had ANY seeded account at all -- which a
    receiving device can now have without having the FULL chart: the sync
    apply side (`commercial_runtime/sync/sync_service.py`) seeds only the
    specific accounts a pulled journal's lines actually reference
    (`INSERT OR IGNORE`, parent first -- BOOKS-WAVE1-PLAN.md section 2.5
    point 4), never the whole `DEFAULT_CHART`. A company that received a
    sale journal referencing only `1000`/`4000` before ever ringing a
    LOCAL return would have 2 accounts seeded, the old `already=2` fast
    path would be truthy, and it would return WITHOUT ever seeding `4900
    Sales Returns and Allowances` -- so the first local return would raise
    `GeneralLedgerAccountError` and abort the whole return, not because
    anything about the return was wrong, but because sync had partially
    seeded this company's chart first. Comparing the actual CODE SET
    against `DEFAULT_CHART` instead makes "partially seeded by sync, then
    completed locally" the same idempotent no-op every other partial-seed
    case already is -- `INSERT OR IGNORE` below still makes re-inserting
    the already-present codes harmless, so this costs one extra query in
    the common case (chart already complete) for a real correctness gap
    closed in the uncommon one. `test_partially_seeded_chart_is_completed_
    before_posting` (retail_gl_emit_test.py) mutation-proves this: M-A5
    restores the old `COUNT(*)` fast path and turns it RED.
    """
    existing_codes = {
        row[0] for row in conn.execute(
            "SELECT code FROM gl_accounts WHERE company_id=?", (cid,)
        ).fetchall()
    }
    if all(code in existing_codes for code, *_ in DEFAULT_CHART):
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


def validated_lines(lines, currency):
    """Pure validation + normalization of a candidate journal's `lines` --
    pulled OUT of `post_journal` (Wave 1, BOOKS-WAVE1-PLAN.md section 2.4)
    as its own function so a caller with NO source document to post at all
    can still ask "would `post_journal` accept these lines" -- the sync
    APPLY side (`commercial_runtime/sync/sync_service.py`, a separate
    builder's own file) is exactly such a caller: BOOKS-WAVE1-PLAN.md
    section 2.5 point 2 requires it to validate a PULLED journal's lines
    BEFORE writing anything, using the identical rules `post_journal`
    itself enforces, so the two can never quietly disagree about what
    "balanced" means. `test_balance_check_agrees_with_post_journal_on_
    the_same_lines` (the sync apply side's own test file) is the parity
    proof this split exists to make possible.

    Takes no `conn`, `cid`, `source_type` or `source_id` -- deliberately:
    this function knows nothing about accounts existing, companies, or
    which document a journal came from, only whether a candidate line list
    is internally well-formed and balanced. Resolving each line's
    `account_code` to a real `gl_accounts.id` stays `post_journal`'s own
    job (via `_account_id`), same as before this split.

    THE RULES (identical to `post_journal`'s pre-Wave-1 inline checks, same
    semantics, same exceptions, same messages where a message did not need
    a document to name):
      - each line's 'debit'/'credit' must be non-negative;
      - a line may carry a NONZERO debit OR a nonzero credit, never both;
      - every amount is quantized to `currency`'s own minor unit via
        `quantize_money` FIRST, and a line whose debit AND credit are BOTH
        zero AFTER quantizing is then silently dropped (F4, BOOKS-WAVE1-
        PLAN.md decision F4; v2 finding 5) -- NEVER the other way around.
        A line carrying a genuinely sub-quantum amount on both sides (e.g.
        JOD 0.0004, below the fil) is nonzero BEFORE rounding, so checking
        "is this line zero" before `quantize_money` let it survive the
        drop, get rounded to 0.000/0.000, and be APPENDED to `clean_lines`
        as a zero-value line -- which made `post_journal` emit a wire
        payload consisting ENTIRELY of such lines for a sub-fil-only
        journal, and the sync APPLY side's own mirror of this same rule
        (`commercial_runtime/sync/sync_service.py::_validate_gl_journal_
        lines`) then parks it as `gl_unbalanced` forever, because ITS
        pre-fix zero-check ran on the ALREADY-rounded wire values (0.0/0.0
        exactly) and drops them, leaving nothing -- see that function's
        own matching fix. Rounding first makes both sides agree: a
        sub-quantum line is zero, whether it is checked before or after
        being written to the wire;
      - a `lines` iterable that has nothing left after dropping (now
        post-rounding) zero lines raises `GeneralLedgerImbalanceError` -- a
        journal that would post nothing at all;
      - `sum(debit)` and `sum(credit)` must agree to within
        `_gl_epsilon(currency)` -- HALF the currency's own minor unit,
        never a hardcoded `0.005` (see the module docstring's MONEY
        section) -- or `GeneralLedgerImbalanceError` is raised.

    Returns `(clean_lines, total_debit, total_credit)`, where `clean_lines`
    is a list of `(account_code, debit: Decimal, credit: Decimal, memo)`
    tuples, quantized and with every (post-rounding) zero line already
    dropped -- exactly the shape `post_journal`'s own INSERT loop iterates
    over.
    """
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
        # F4: quantize BEFORE checking for zero -- a sub-quantum amount
        # (nonzero here) must round down to zero and be dropped, never be
        # kept as a zero-value line. See this function's own docstring.
        d = quantize_money(d, currency)
        c = quantize_money(c, currency)
        if not d and not c:
            continue
        total_debit += d
        total_credit += c
        clean_lines.append((code, d, c, raw.get('memo')))

    if not clean_lines:
        raise GeneralLedgerImbalanceError(
            "refusing to validate an empty journal -- every line supplied was zero"
        )

    eps = _gl_epsilon(currency)
    diff = total_debit - total_credit
    if abs(diff) > eps:
        raise GeneralLedgerImbalanceError(
            f"lines do not balance: debits={total_debit} credits={total_credit} "
            f"diff={diff} (tolerance is half of {currency}'s own minor unit, {eps})"
        )

    return clean_lines, total_debit, total_credit


def _derive_source_uid(conn, cid, source_type, source_id, reversed_journal_id):
    """This journal's `source_uid` -- BOOKS-WAVE1-PLAN.md section 2.2's
    `source_id` row: for `sale`/`return`, the originating row's own `uid`
    (looked up in `_SOURCE_UID_TABLES`, scoped `company_id=cid` like every
    other lookup in this module); for `reversal`, the REVERSED journal's
    own `uid` (looked up on `gl_journals` itself -- the reversed journal is
    always posted BEFORE its reversal, so by the time `reverse_journal`
    calls `post_journal`, it already has a real `uid`, whether it was
    posted under Wave 1 code or assigned one by `backfill_legacy_journals`
    below); otherwise `NULL` (`customer_payment`, `supplier_payment`,
    `po_payment`, `purchase_order`, `opening_balance` -- none of these name
    a row with a cross-device `uid` today).

    Returns `None`, never raises, when the named row cannot be found (a
    caller passing a `source_id` that does not exist is a pre-existing
    possibility this function does not newly create -- `post_journal`
    itself never required the source row to exist before Wave 1 either).
    """
    if source_type == 'reversal':
        if reversed_journal_id is None:
            return None
        row = conn.execute(
            "SELECT uid FROM gl_journals WHERE id=? AND company_id=?",
            (reversed_journal_id, cid)
        ).fetchone()
        return row['uid'] if row else None

    table = _SOURCE_UID_TABLES.get(source_type)
    if not table or source_id is None:
        return None
    row = conn.execute(
        f"SELECT uid FROM {table} WHERE id=? AND company_id=?",
        (source_id, cid)
    ).fetchone()
    return row['uid'] if row else None


def _account_metadata(conn, cid, codes):
    """Account metadata for the sync payload's `accounts` list (BOOKS-
    WAVE1-PLAN.md section 2.3) -- one dict per account CODE referenced by
    `codes`, PLUS every ancestor account any of them depends on, ordered
    PARENTS FIRST, so a receiving device with a missing account can
    `INSERT OR IGNORE` them in the order given and never insert a child
    before the parent row it points at (BOOKS-WAVE1-PLAN.md section 2.5
    point 4: "parent first").

    Parent-first falls out of the recursion itself, not a separate sort:
    `_add` recurses into a code's own `parent_code` BEFORE appending that
    code's own dict, so nothing is ever appended until everything it
    depends on already has been. `seen` also doubles as de-duplication --
    two lines referencing the same account code (or two lines whose
    accounts share a parent) must not double that account's metadata in
    the payload.
    """
    seen = set()
    ordered = []

    def _add(code):
        if code in seen:
            return
        row = conn.execute(
            "SELECT a.code, a.name, a.account_type, a.normal_balance, p.code AS parent_code "
            "FROM gl_accounts a LEFT JOIN gl_accounts p "
            "  ON p.id = a.parent_id AND p.company_id = a.company_id "
            "WHERE a.company_id=? AND a.code=?",
            (cid, code)
        ).fetchone()
        if not row:
            return
        if row['parent_code']:
            _add(row['parent_code'])
        seen.add(code)
        ordered.append({
            'code': row['code'], 'name': row['name'],
            'account_type': row['account_type'],
            'normal_balance': row['normal_balance'],
            'parent_code': row['parent_code'],
        })

    for code in codes:
        _add(code)
    return ordered


def journal_sync_payload(conn, journal_id):
    """THE ONLY payload builder for a `gl_journal` sync-outbox event
    (BOOKS-WAVE1-PLAN.md section 2.3's fixed contract). Re-reads the rows
    `post_journal` (or `backfill_legacy_journals`) just persisted, from the
    SAME connection, NEVER the caller's own in-memory inputs -- so the
    payload can never diverge from what was actually committed to
    `gl_journals`/`gl_journal_lines`, even if a caller's local variables
    were somehow stale or re-derived differently.

    Every key on the wire is either a column copied verbatim (`business_
    date`, `reference`, `memo`, `source_type`, `currency`, `posted_by`,
    `posted_at`, this journal's own `uid`, its `source_uid`) or a LOCAL
    integer id translated to a `uid`/`code` (`branch_id` -> `branch_uid`,
    `reversed_journal_id` -> `reversed_journal_uid`, each line's
    `account_id` -> `account_code`) -- `company_id`, `branch_id`,
    `source_id`, `reversed_journal_id` and every `account_id` are NEVER on
    the wire (BOOKS-WAVE1-PLAN.md section 2.3, "These are never on the
    wire"), because they are local-integer-id facts about THIS device that
    mean nothing, or mean something DIFFERENT, on a peer.
    """
    header = conn.execute(
        "SELECT j.id, j.company_id, j.branch_id, j.business_date, j.reference, "
        "       j.memo, j.source_type, j.source_id, j.currency, "
        "       j.reversed_journal_id, j.posted_by, j.posted_at, "
        "       j.uid, j.source_uid, "
        "       b.uid AS branch_uid, rj.uid AS reversed_journal_uid "
        "FROM gl_journals j "
        "LEFT JOIN branches b ON b.id = j.branch_id "
        "LEFT JOIN gl_journals rj ON rj.id = j.reversed_journal_id "
        "WHERE j.id=?",
        (journal_id,)
    ).fetchone()
    if not header:
        raise GeneralLedgerAccountError(
            f"cannot build a sync payload for journal {journal_id}: no such "
            f"gl_journals row"
        )

    lines = conn.execute(
        "SELECT a.code AS account_code, l.debit, l.credit, l.memo "
        "FROM gl_journal_lines l JOIN gl_accounts a ON a.id = l.account_id "
        "WHERE l.journal_id=? ORDER BY l.id",
        (journal_id,)
    ).fetchall()

    accounts = _account_metadata(conn, header['company_id'], [r['account_code'] for r in lines])

    return {
        'uid': header['uid'],
        'business_date': header['business_date'],
        'reference': header['reference'],
        'memo': header['memo'],
        'source_type': header['source_type'],
        'source_uid': header['source_uid'],
        'branch_uid': header['branch_uid'],
        'currency': header['currency'],
        'reversed_journal_uid': header['reversed_journal_uid'],
        'posted_by': header['posted_by'],
        'posted_at': header['posted_at'],
        'lines': [
            {
                'account_code': r['account_code'],
                'debit': float(r['debit']),
                'credit': float(r['credit']),
                'memo': r['memo'],
            }
            for r in lines
        ],
        'accounts': accounts,
    }


def _queue_gl_journal_sync_event(conn, journal_id):
    """Queues one `gl_journal` / `create` sync-outbox event for a
    just-persisted journal -- a raw `INSERT INTO sync_outbox`, the same
    shape this codebase's other sync-queuing call sites all use (api/
    retail_api.py, core/retail/reorder_hook.py), never imported from any
    of them: core/ must not import api/ (this module already states that
    layering rule for `_resolve_currency`/`_gl_epsilon` above), and this
    module runs on the SAME connection a route handed it, with no `cur`
    of its own to borrow -- so this is a deliberate, minimal duplicate of
    that one INSERT statement, not a divergent implementation.

    `entity_id` is the journal's own `uid` (BOOKS-WAVE1-PLAN.md section
    2.3: "entity_id=uid"), never its local integer id -- a peer device,
    or Owner's own relay, must be able to name this row without knowing
    anything about THIS device's autoincrement sequence. Called on the
    SAME connection/transaction as the journal's own INSERT -- see
    `post_journal`'s and `backfill_legacy_journals`'s own docstrings for
    what that buys (a rollback removes both together; a boot-time backfill
    commits both per row).
    """
    payload = journal_sync_payload(conn, journal_id)
    conn.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            str(uuid.uuid4()), 'gl_journal', payload['uid'], 'create',
            json.dumps(payload), datetime.now(timezone.utc).isoformat(),
        )
    )


def _opening_balance_canonical_lines(currency, lines):
    """An opening balance's lines as F2 identifies them: sorted (account
    code, debit, credit), each amount a string quantized to `currency`'s own
    minor unit -- NO memo (see `legacy_journal_uid`'s F2 notes). The ONE
    definition both `legacy_journal_uid` and `_opening_balance_occurrence`
    use, so "the same opening-balance fact" can never mean two different
    things in the two places that ask."""
    try:
        quant = currency_quantum(currency or DEFAULT_BASE_CURRENCY)
    except Exception:
        quant = currency_quantum(DEFAULT_BASE_CURRENCY)

    def _amt(value):
        d = value if isinstance(value, Decimal) else Decimal(str(value or 0))
        return str(d.quantize(quant, rounding=ROUND_HALF_UP))

    return sorted(
        (row['account_code'], _amt(row['debit']), _amt(row['credit']))
        for row in lines
    )


def _opening_balance_occurrence(conn, row, lines):
    """How many opening balances of `row`'s company, with a LOWER local id,
    carry the identical (currency, lines) fact -- 0 for the first one.

    Deterministic across copies of one database (same ids, same rows) and
    across boots (ids never change; a row skipped this boot is counted the
    same way next boot). A PULLED opening balance never shifts it: every
    candidate here is local pre-v41 history, written before this device
    could pull any `gl_journal`, and `gl_journals.id` is AUTOINCREMENT, so a
    pulled row always has a higher id than every legacy row."""
    target = (row['currency'], _opening_balance_canonical_lines(row['currency'], lines))
    earlier = conn.execute(
        "SELECT id, currency FROM gl_journals "
        "WHERE company_id=? AND source_type='opening_balance' AND id<? ORDER BY id",
        (row['company_id'], row['id'])
    ).fetchall()
    occurrence = 0
    for prior in earlier:
        prior_lines = conn.execute(
            "SELECT a.code AS account_code, l.debit, l.credit "
            "FROM gl_journal_lines l JOIN gl_accounts a ON a.id = l.account_id "
            "WHERE l.journal_id=?",
            (prior['id'],)
        ).fetchall()
        if (prior['currency'],
                _opening_balance_canonical_lines(prior['currency'], prior_lines)) == target:
            occurrence += 1
    return occurrence


def legacy_journal_uid(header, lines):
    """Deterministic `uuid5` identity for a PRE-v41 `gl_journals` row that
    has no `uid` yet (BOOKS-WAVE1-PLAN.md section 2.4). Deterministic, not
    `uuid4`, for the same reason `cheques.py`'s own historical-uid backfill
    already is (see that module's docstring): a pre-v41 BACKUP restored
    onto a REPLACEMENT device must derive the exact same `uid` for the
    exact same historical journal that the original machine already
    assigned and sent -- a fresh random uid here would make the restored
    device's own history look like a brand-new, never-before-seen batch of
    journals to every peer that already received the original backfill,
    duplicating every one of them. `test_backfill_uids_are_deterministic_
    across_two_copies_of_one_db` (M-A6 uses `uuid4`) is this function's own
    mutation proof.

    `header` is a mapping exposing the LOCAL columns that make one
    historical row unique enough to hash: `id` (this device's own local
    row id -- INCLUDED deliberately: two genuinely different historical
    journals on the SAME device, with identical business_date/reference/
    lines, must still hash to different uids, and the local id is the one
    fact already guaranteed unique per-device), `business_date`,
    `reference`, `memo`, `source_type`, `source_uid` (already resolved by
    the caller -- may be `None`), `currency`, `posted_by`, `posted_at`.
    `company_id` and every local branch/source/reversed-journal INTEGER id
    are DELIBERATELY EXCLUDED (BOOKS-WAVE1-PLAN.md section 2.4: "it
    excludes company_id and local branch/source ids") -- those are facts
    about THIS install, not about the journal, and including them would
    make the SAME journal restored onto a device that rebinds its
    `company_id` (a real, supported operation elsewhere in this codebase)
    hash to a different uid than the original did.

    `lines` is an iterable of the journal's persisted lines, each exposing
    `account_code`, `debit`, `credit`, `memo` (a `sqlite3.Row` or a plain
    mapping, either works). Sorted by `(account_code, debit, credit, memo)`
    before hashing -- NEVER by insertion/`id` order, which is an SQLite
    implementation detail, not a fact about the journal -- and each amount
    is rendered as a STRING quantized to `currency`'s own minor unit, never
    a bare float repr (not stable across Python/SQLite versions for the
    same value).

    F2 (BOOKS-WAVE1-PLAN.md decision F2; v2 finding 2) -- `source_type ==
    'opening_balance'` HASHES DIFFERENTLY FROM EVERY OTHER JOURNAL: the
    canonical form is ONLY `currency` plus the sorted `lines` above -- NO
    local `id`, `business_date`, `reference`, `memo` or `posted_at`.
    Rationale: a pre-v38 backup restored onto a REPLACEMENT device
    re-generates its opening balance at RESTORE time -- a new `posted_at`,
    often a new `business_date` too -- so the general-case formula above
    (which deliberately INCLUDES those) mints a brand-new uid for the
    IDENTICAL imported debt every time the same shop's pre-v38 backup is
    restored anywhere. An opening balance is not "an event that happened
    on this device on this date"; it is a statement of a pre-existing,
    imported DEBT, and the same imported debt restored onto two different
    devices -- or backed up and restored twice on the same one -- is ONE
    fact, not two. Hashing only `currency` + `lines` makes two opening
    balances with identical amounts collapse to the SAME uid regardless of
    which device or which restore produced them, which is exactly what
    `test_backfill_hashes_opening_balance_restores_to_one_fact`
    (retail_gl_emit_test.py) proves against verifier 2's own repro4
    scenario (a third device receiving both the original's and a restored
    replacement's opening-balance events must hold the true AR ONCE, not
    twice).

    WHAT THIS CANNOT DISTINGUISH -- stated plainly, per BOOKS-WAVE1-PLAN.md
    F2, so it is a decision, not a surprise found later: TWO GENUINELY
    DIFFERENT DEVICES (or two genuinely different companies) whose opening
    balances happen to be identical TO THE FIL -- same `currency`, same
    account codes, same amounts -- collapse to the SAME uid and therefore
    the SAME fact, even though they were two real, separate debts that
    happened to coincide in value. This is deliberately accepted: it is
    judged far less likely, and far less costly when it does happen, than
    the routine "restore a backup onto a replacement device" case the
    general-case formula would otherwise silently double every time.

    CORRECTED 2026-09-26 (third wave verifier), two points, both inside F2's
    own decision rather than against it:

      * NO LINE MEMO. F2 names the canonical form as currency plus each
        line's (account code, debit, credit). The first cut also hashed each
        line's memo. Those memos are migration-code prose ('Opening balance:
        pre-v38 customer AR history'), so any later rewording or translation
        of that prose would give a replacement device's regenerated opening
        balance a NEW uid, and every peer would book the same imported debt
        twice. `test_opening_balance_uid_ignores_line_memos` is RED against
        the memo-hashing cut.

      * `opening_occurrence` -- how many EARLIER opening balances of the same
        company on the same device carry this identical (currency, lines)
        fact (`_opening_balance_occurrence`, computed by the caller). Without
        it, W1-0 residue (the v38 opening balance posted AGAIN, to the fil,
        on a later upgrade whose `credit_balance` had not moved) hashed to
        the SAME uid as the original; F3 then skipped the second copy as a
        "uid collision" -- left uid NULL, never sent, but still counted in
        this device's own books. So the posting device booked the debt
        twice and every peer once, forever, which is exactly the divergence
        plan section 2.6 forbids ("send them anyway, so both devices hold
        identical (equally wrong) books and a single reverse_journal corrects
        every device"). The first copy is occurrence 0 -- the same value a
        replacement device's single regenerated opening balance gets -- so
        the restore case above still collapses to one fact.
        `test_identical_w1_0_duplicate_opening_balances_each_reach_every_
        device` is RED without it.
    """
    currency = header.get('currency') or DEFAULT_BASE_CURRENCY
    try:
        quant = currency_quantum(currency)
    except Exception:
        quant = currency_quantum(DEFAULT_BASE_CURRENCY)

    def _amt(value):
        d = value if isinstance(value, Decimal) else Decimal(str(value or 0))
        return str(d.quantize(quant, rounding=ROUND_HALF_UP))

    if header.get('source_type') == 'opening_balance':
        canonical = json.dumps({
            'source_type': 'opening_balance',
            'currency': header.get('currency'),
            'lines': _opening_balance_canonical_lines(header.get('currency'), lines),
            'occurrence': int(header.get('opening_occurrence') or 0),
        }, sort_keys=True, default=str)
    else:
        canonical_lines = sorted(
            (
                row['account_code'],
                _amt(row['debit']),
                _amt(row['credit']),
                row['memo'] or '',
            )
            for row in lines
        )
        canonical = json.dumps({
            'id': header['id'],
            'business_date': header.get('business_date'),
            'reference': header.get('reference'),
            'memo': header.get('memo'),
            'source_type': header.get('source_type'),
            'source_uid': header.get('source_uid'),
            'currency': header.get('currency'),
            'posted_by': header.get('posted_by'),
            'posted_at': header.get('posted_at'),
            'lines': canonical_lines,
        }, sort_keys=True, default=str)
    return str(uuid.uuid5(_GL_LEGACY_NAMESPACE, canonical))


def _suspected_duplicate_opening_balances(candidates):
    """W1-0 residue detector (BOOKS-WAVE1-PLAN.md section 2.6), run over
    `candidates` -- the legacy rows `backfill_legacy_journals` is assigning
    uids to in THIS call, in ascending id order -- never over every journal
    on the device.

    CORRECTED 2026-09-26 (wave verifier): this used to scan EVERY
    `opening_balance` row on the device, on every boot. That flagged a
    peer's LEGITIMATE opening balance: section 2.6 syncs every device's own
    v38 opening balance on purpose ("adding the history together is the
    true shop-wide ledger"), so a two-till shop that carried credit on both
    tills holds two opening balances per till once sync runs -- its own, and
    its peer's at a higher local id. The WARNING then told the owner to
    consider `reverse_journal()`-ing a real balance, on every boot.
    W1-0 residue can only be LOCAL, PRE-v41 history (the W1-0 guard in
    `_migrate_add_general_ledger` stops new ones), and that is exactly the
    candidate set: a pulled
    journal always arrives with a uid and is never a candidate. So residue
    is reported once, on the boot that backfills it, from the device that
    posted it. `retail_gl_emit_test.py::test_duplicate_detector_ignores_a_
    peers_opening_balance_that_arrived_by_sync` is RED against the old scan.

    Flags an `opening_balance` candidate, PER COMPANY, when EITHER:
      - it is not that company's EARLIEST `opening_balance` journal (a
        second, third or fourth one -- exactly what the pre-fix
        `_migrate_add_general_ledger` bug in `database/schema.py`
        produced on every later chain replay, before `gl_already_existed`
        landed); OR
      - some candidate that is NOT an opening balance has a LOWER id for
        the same company (a real transaction was posted before this
        "opening" balance ever was, which an honest first-ever seed could
        never do).

    Never raises. Returns a list of `{'id', 'company_id'}` dicts, logged
    as a single WARNING by `backfill_legacy_journals` (never per-row --
    a shop with a real W1-0 residue would otherwise flood its own log).
    Existing duplicates are NOT reversed here or anywhere in this module
    -- `credit_balance` can be set without ever posting to the ledger, so
    a later opening balance can be partly legitimate, and only the shop's
    owner can decide whether to `reverse_journal` the extras (BOOKS-WAVE1-
    PLAN.md section 2.6, "No automatic reversal").
    """
    seen_opening = set()       # company_ids with an earlier opening candidate
    seen_non_opening = set()   # company_ids with an earlier non-opening candidate
    suspects = []
    for row in sorted(candidates, key=lambda r: r['id']):
        cid = row['company_id']
        if row['source_type'] == 'opening_balance':
            if cid in seen_opening or cid in seen_non_opening:
                suspects.append({'id': row['id'], 'company_id': cid})
            seen_opening.add(cid)
        else:
            seen_non_opening.add(cid)
    return suspects


def backfill_legacy_journals(conn, chunk=1000):
    """Boot-time, NOT version-gated -- `database/schema.py::_init_retail`'s
    own call site (hasattr-guarded, wired by Builder B -- see that call
    site's own comment) runs this on EVERY boot, same as `_v16_rebind_
    orphaned_open_drawers` immediately above it, because it must be safe
    to retry indefinitely rather than tied to a version marker that only
    ever gives it one chance to succeed (BOOKS-WAVE1-PLAN.md section 5,
    "Boot step").

    Every `gl_journals` row this codebase ever posted BEFORE Wave 1 (v41)
    has no `uid` -- this is what finally gives each one a stable,
    cross-device identity and sends it to every other device on this
    shop's licence, which is what heals the divergence Wave 1 exists to
    fix (BOOKS-WAVE1-PLAN.md section 2.6, "History (backfill)").

    For each row `WHERE uid IS NULL`, in ascending `id` order (so an
    earlier journal always gets its `uid` BEFORE a later journal that
    reverses it needs to look that `uid` up -- see `_derive_source_uid`'s
    own docstring on why a reversal's original is always processed
    first):
      1. `_derive_source_uid` resolves its `source_uid` FIRST -- the
         IDENTICAL derivation `post_journal` itself uses, so a legacy
         `sale`/`return` journal's `source_uid` points at that row's own
         `uid` exactly like a Wave-1-native journal's would, and a legacy
         `reversal` resolves against whatever `uid` its original was JUST
         assigned, earlier in this same pass;
      2. `legacy_journal_uid` mints its deterministic `uid` from a `header`
         that carries THIS resolved `source_uid` (F1, BOOKS-WAVE1-PLAN.md
         decision F1; v2 finding 4) -- NEVER the other way around. The
         `header` this function builds from the candidate SELECT below has
         no `source_uid` column of its own (a legacy row's `source_uid` is
         NULL until this exact pass resolves it), so hashing before this
         step would silently hash `None` for every row, forever -- and per
         F1, the uid FORMULA itself can never change after ships, so this
         ordering is fixed now, permanently;
      3. both are written with one `UPDATE`, guarded by a SAVEPOINT (F3,
         BOOKS-WAVE1-PLAN.md decision F3; v2 finding 3) -- see below;
      4. `_queue_gl_journal_sync_event` queues its one `create` event, but
         ONLY when the UPDATE actually landed (see F3 below: a uid
         collision skips both the UPDATE and this queue call for that one
         row) -- and inside the SAME savepoint, within an explicitly opened
         transaction, so a row's uid and its event always commit together
         (see the BEGIN comment in the loop: a bare SAVEPOINT with no
         transaction around it is itself a transaction, and releasing it
         commits).

    F3 -- A UID COLLISION MUST SKIP ONE ROW, NEVER ABORT THE WHOLE
    BACKFILL. `idx_gl_journals_uid` (database/schema.py) is a UNIQUE
    partial index on `uid`, and `legacy_journal_uid` is DETERMINISTIC by
    design (see that function's own docstring) -- which means the SAME uid
    can legitimately already exist on ANOTHER local row: a "pulled twin" of
    this exact historical journal, received from a peer that already ran
    ITS OWN backfill and sent the (identical) legacy event over before this
    device's own backfill got to it (verifier 2's own `repro6_poison_row.py`
    -- a cloned install that synced before this device's backfill
    succeeded). Before this fix, that UPDATE's `sqlite3.IntegrityError`
    propagated straight out of this function's un-guarded loop, aborting
    the ENTIRE backfill -- on EVERY later boot, forever, because the
    candidate SELECT re-selects the exact same uid-NULL rows every time
    and hits the exact same collision first. Every OTHER legacy journal on
    the device, however unrelated, was silently never backfilled either.
    Each row's UPDATE now runs inside its OWN `SAVEPOINT`: a collision
    rolls back only that row's attempted write, is logged ONCE with BOTH
    ids (the local row that could not be assigned, and the existing row
    already holding that uid), and the loop CONTINUES -- the colliding
    local row is left exactly as it was (`uid` still NULL, so a future
    boot may retry it e.g. once the twin itself is reconciled) and is
    NEVER queued to `_queue_gl_journal_sync_event` (there is nothing new to
    tell peers; they already have this fact, under the existing row's own
    uid). `test_backfill_skips_a_uid_collision_and_continues_past_it`
    (retail_gl_emit_test.py, modelled on verifier 2's own repro6) is this
    fix's own proof: after ONE boot, every OTHER legacy row still gets its
    uid and its outbox event.

    A COLLIDED ROW'S REVERSAL (third wave verifier, retry, 2026-09-26). F1
    feeds a legacy reversal's `source_uid` -- the REVERSED journal's uid --
    into its hash. A collided row keeps `uid` NULL, so on the device that
    skipped it that lookup returns None, while every peer's copy of the same
    reversal hashed with the twin's real uid. The two copies of ONE reversal
    then hashed apart: this device's copy was no collision, got a fresh uid
    and was SENT, with no `reversed_journal_uid` to tie it to anything, and
    every peer booked the reversal a second time (a clone holding X and its
    reversal R: a peer's cash moved by -X, where the truth is 0). So the uid
    a collided row hashed to is remembered for the rest of this pass, and a
    reversal of that row derives its `source_uid` from it -- the identical
    value the peer derived -- which makes it recognisably the same fact:
    it collides with its own pulled twin and is skipped too. Candidates run
    in ascending id order and a reversal always has a higher id than what it
    reverses, so the original is always met first; a collided row is
    re-selected every boot, so the map is rebuilt identically each time.
    `test_a_collided_journals_legacy_reversal_is_skipped_with_it_not_resent`
    is RED without it. This does NOT touch the formula: it only corrects the
    `source_uid` value fed into it for this one case.

    WHAT F3 STILL LEAVES, stated so it is a known state rather than a
    surprise: a skipped row stays in THIS device's books next to its pulled
    twin, so this device books that fact twice until the owner reverses one
    (verifier 2's repro6 said so). And a reversal of the skipped row posted
    LATER, live, through `reverse_journal`, carries no `reversed_journal_uid`
    (the row has none), so peers book it standalone -- correcting this
    device while moving every peer. Both need an owner decision, not a sync
    rule, and are logged here per row via the WARNING below.

    Commits after every `chunk` rows (default 1000, BOOKS-WAVE1-PLAN.md
    section 5's own R4 estimate: roughly 2 journals per historical sale,
    so a 10k-sale shop backfills on the order of 20k rows) -- a crash
    partway through leaves already-assigned rows assigned (picked up
    correctly on the very next boot, never re-run) instead of holding one
    enormous uncommitted transaction for the whole shop's history.

    THE CANDIDATE ROW LIST IS FETCHED EXACTLY ONCE, UP FRONT, NEVER
    RE-QUERIED INSIDE THE COMMIT LOOP -- a deliberate choice, not just a
    style preference. An earlier shape of this function re-ran `WHERE uid
    IS NULL ... LIMIT chunk` inside a `while True:` after every commit,
    relying on the result set SHRINKING (each chunk's rows no longer match
    once assigned) to terminate. That makes termination itself depend on
    the WHERE clause being correct -- if it were ever weakened to select
    rows regardless of `uid`, the loop would find the SAME rows again
    after every commit and never terminate at all, turning a coverage bug
    into a hang instead of a fast, observable failure. Snapshotting the
    full candidate id list ONE time (`ORDER BY id`, so a reversal is still
    always snapshotted after the original it points at) and then chunking
    over that FIXED Python list instead bounds this function to at most
    `len(candidates)` iterations no matter what any future edit does to
    the SELECT that built it -- a broken filter now shows up as wrong
    OUTPUT (rows re-emitted that already had a uid) rather than a hang.
    `test_backfill_emits_each_legacy_journal_once_across_two_runs`
    (M-A7 widens the candidate SELECT to ignore the NULL filter) is this
    function's own mutation proof, and is fast specifically because of
    this shape.

    IDEMPOTENT BY CONSTRUCTION: the candidate SELECT is always `WHERE uid
    IS NULL`, so a second CALL (the normal case -- every boot, not just
    the first) snapshots an EMPTY list once every legacy row has been
    assigned, and is a clean no-op. A PULLED journal (one this device
    received from a peer) always arrives WITH a `uid` already (BOOKS-
    WAVE1-PLAN.md section 2.3/2.5) and is therefore never a candidate
    either, so this backfill only ever touches genuinely local, genuinely
    pre-v41 history -- never a journal that already has its cross-device
    identity, whichever device minted it.

    RETURNS `{'assigned': int, 'emitted': int, 'uid_collisions': [...],
    'suspected_duplicate_opening': [...]}`. `assigned` and `emitted` are
    always equal today (one outbox event per newly-assigned uid, no skips
    other than a collision, which counts toward NEITHER) -- kept as two
    separate keys because a future reason to assign a uid WITHOUT emitting
    it (a row this device already knows it will never need to sync) should
    not have to invent a new return key later. `uid_collisions` is F3's own
    result: one `{'id', 'existing_id', 'uid'}` dict per row this call
    skipped because its deterministic uid already belonged to another
    local row (see F3 above) -- empty on the overwhelmingly common boot
    where nothing collides. `suspected_duplicate_opening` is
    `_suspected_duplicate_opening_balances`'s own result, logged here as a
    single WARNING (never raised: a shop with this residue must still boot
    and sync normally) and returned so a caller can surface it without
    re-deriving the same query.
    """
    logger = logging.getLogger(__name__)
    assigned = 0
    emitted = 0
    uid_collisions = []
    # Local id -> the uid a COLLIDED row hashed to (its pulled twin's uid).
    # See "A COLLIDED ROW'S REVERSAL" in this function's docstring.
    collided_uid_by_id = {}

    candidates = conn.execute(
        "SELECT id, company_id, branch_id, business_date, reference, memo, "
        "       source_type, source_id, currency, reversed_journal_id, "
        "       posted_by, posted_at "
        "FROM gl_journals WHERE uid IS NULL ORDER BY id"
    ).fetchall()

    for start in range(0, len(candidates), chunk):
        for row in candidates[start:start + chunk]:
            lines = conn.execute(
                "SELECT a.code AS account_code, l.debit, l.credit, l.memo "
                "FROM gl_journal_lines l JOIN gl_accounts a ON a.id = l.account_id "
                "WHERE l.journal_id=? ORDER BY l.id",
                (row['id'],)
            ).fetchall()
            # F1 -- resolve `source_uid` FIRST and fold it into `header`
            # BEFORE hashing: `legacy_journal_uid`'s canonical form includes
            # `source_uid` (see that function's own docstring), and the raw
            # candidate row above carries no such column -- see this
            # function's own docstring, point 2, for why the order is
            # fixed permanently, not just today's convenience.
            source_uid = _derive_source_uid(
                conn, row['company_id'], row['source_type'], row['source_id'],
                row['reversed_journal_id'],
            )
            if (source_uid is None and row['source_type'] == 'reversal'
                    and row['reversed_journal_id'] in collided_uid_by_id):
                # The reversed journal collided with its pulled twin earlier
                # in this pass, so its own uid is still NULL here -- but the
                # fact it IS carries the twin's uid, and so does every peer's
                # copy of this reversal. See "A COLLIDED ROW'S REVERSAL".
                source_uid = collided_uid_by_id[row['reversed_journal_id']]
            header = dict(row)
            header['source_uid'] = source_uid
            if row['source_type'] == 'opening_balance':
                # See legacy_journal_uid's F2 notes: the Nth identical copy
                # of one opening balance on this device (W1-0 residue) is
                # its own fact, never a "collision" with the first.
                header['opening_occurrence'] = _opening_balance_occurrence(conn, row, lines)
            uid = legacy_journal_uid(header, lines)

            # ONE ROW = ONE ATOMIC UNIT, ONE CHUNK = ONE TRANSACTION (third
            # wave verifier, 2026-09-26). SQLite treats a SAVEPOINT with no
            # transaction around it as the transaction itself, and RELEASE
            # of that outermost savepoint is a COMMIT. The first cut of F3
            # relied on that without meaning to, and queued the outbox event
            # AFTER the RELEASE: the first row of every boot, and of every
            # chunk after a commit, had its uid committed BEFORE its outbox
            # row was inserted, so a failure or a killed process before the
            # chunk commit kept the uid and lost the event -- and that
            # journal, never `uid IS NULL` again, never reached any other
            # device. Two parts close it: this explicit BEGIN keeps a whole
            # chunk in one transaction (commit per chunk, plan R4, never a
            # commit per row), and the event is queued INSIDE the row's
            # savepoint, before RELEASE, so a row's uid and event can only
            # ever be kept or undone together. `test_a_boot_backfill_that_
            # fails_mid_chunk_never_strands_a_uid_without_its_event` and
            # `test_a_caller_that_commits_after_a_backfill_failure_still_
            # keeps_only_whole_pairs` (retail_gl_emit_test.py) pin both.
            if not conn.in_transaction:
                conn.execute("BEGIN")
            # F3 -- one SAVEPOINT per row: a uid collision (a pulled twin
            # of this exact journal already sitting on this device under
            # the SAME deterministic uid) rolls back ONLY this row's
            # attempted UPDATE, is logged once, and the loop continues --
            # never lets one poisoned row abort every other legacy
            # journal's backfill, this boot or any future one.
            conn.execute("SAVEPOINT backfill_legacy_row")
            try:
                conn.execute(
                    "UPDATE gl_journals SET uid=?, source_uid=? WHERE id=?",
                    (uid, source_uid, row['id'])
                )
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK TO SAVEPOINT backfill_legacy_row")
                conn.execute("RELEASE SAVEPOINT backfill_legacy_row")
                existing = conn.execute(
                    "SELECT id FROM gl_journals WHERE uid=?", (uid,)
                ).fetchone()
                existing_id = existing['id'] if existing else None
                logger.warning(
                    "backfill_legacy_journals: uid collision -- local "
                    "gl_journals row %s's deterministic uid (%s) already "
                    "belongs to row %s (a pulled twin of this same "
                    "journal, BOOKS-WAVE1-PLAN.md F3) -- row %s is "
                    "skipped this boot (uid left NULL) and never emitted; "
                    "every other legacy row still backfills normally.",
                    row['id'], uid, existing_id, row['id'],
                )
                uid_collisions.append(
                    {'id': row['id'], 'existing_id': existing_id, 'uid': uid}
                )
                collided_uid_by_id[row['id']] = uid
                continue
            try:
                _queue_gl_journal_sync_event(conn, row['id'])
            except BaseException:
                # Undo THIS row's uid too, so what is left in the open
                # transaction is only whole (uid, event) pairs -- whatever
                # the caller then does with it (init_retail's `finally`
                # rolls it back; a commit would persist only whole pairs).
                # Guarded: some SQLite failures (disk full, I/O) end the
                # transaction themselves, and a "no such savepoint" here must
                # never replace the error the operator needs to read.
                try:
                    conn.execute("ROLLBACK TO SAVEPOINT backfill_legacy_row")
                    conn.execute("RELEASE SAVEPOINT backfill_legacy_row")
                except sqlite3.Error:
                    pass
                raise
            conn.execute("RELEASE SAVEPOINT backfill_legacy_row")
            assigned += 1
            emitted += 1
        conn.commit()

    suspected = _suspected_duplicate_opening_balances(candidates)
    if suspected:
        logger.warning(
            "backfill_legacy_journals: %d suspected duplicate opening-balance "
            "journal(s) detected (W1-0 residue, BOOKS-WAVE1-PLAN.md section "
            "2.6) -- company/id pairs: %s. These were sent to every device on "
            "this shop's licence as-is; only the owner can decide whether to "
            "reverse_journal() the extras.",
            len(suspected),
            [(s['company_id'], s['id']) for s in suspected],
        )

    return {
        'assigned': assigned,
        'emitted': emitted,
        'uid_collisions': uid_collisions,
        'suspected_duplicate_opening': suspected,
    }


def post_journal(conn, cid, branch_id, business_date, source_type, source_id,
                  currency, lines, reference=None, memo=None, posted_by=None,
                  reversed_journal_id=None, uid=None, source_uid=None):
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

    WAVE 1 ADDITIONS (schema v41, BOOKS-WAVE1-PLAN.md section 2.4), all
    inside this SAME transaction, all before this function returns: mints
    this journal's own `uid` (`uuid.uuid4()`), derives its `source_uid`
    (`_derive_source_uid` below -- the originating sale/return's own `uid`
    for a `sale`/`return` posting, the REVERSED journal's own `uid` for a
    `reversal`, `NULL` otherwise), stores both on the new row, and queues
    exactly one `gl_journal` sync-outbox event via `_queue_gl_journal_
    sync_event`. Because the outbox INSERT runs on the SAME connection and
    transaction as the journal itself, a rollback anywhere in the caller's
    route (the identical `except Exception: conn.rollback()` this
    docstring already describes above) removes the journal, its lines, AND
    its outbox row together -- `test_rolled_back_document_leaves_no_gl_
    outbox_row` (retail_gl_emit_test.py) proves this. `test_post_sale_
    queues_exactly_one_gl_journal_event_per_journal` (M-A1 removes the
    queue call) and `test_journal_uid_is_persisted_on_the_origin_row`
    (M-A4 stores NULL) are this addition's own mutation proofs.
    """
    ensure_default_chart(conn, cid)

    try:
        clean_lines, total_debit, total_credit = validated_lines(lines, currency)
    except GeneralLedgerImbalanceError as exc:
        # Re-raised with THIS call's own document context prefixed on --
        # `validated_lines` itself is a context-free pure function (Wave 1,
        # BOOKS-WAVE1-PLAN.md section 2.4 -- it exists so a caller with NO
        # source document at all, like the sync apply side's own balance
        # check, can ask "would this balance" without inventing one), so it
        # cannot name `source_type`/`source_id` in its own message the way
        # this function's callers have always been able to rely on.
        raise GeneralLedgerImbalanceError(
            f"journal for {source_type}:{source_id} -- {exc}"
        ) from exc

    # F3b: `uid` is overridable so `reverse_journal` can mint the
    # DETERMINISTIC reversal uid (see `_GL_REVERSAL_NAMESPACE`), and
    # `source_uid` so a payment posting can carry the payment row's own uid
    # -- the source link the void path finds its journal by. Both default
    # to the old behaviour.
    uid = uid or str(uuid.uuid4())
    if source_uid is None:
        source_uid = _derive_source_uid(conn, cid, source_type, source_id, reversed_journal_id)

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO gl_journals "
        "(company_id, branch_id, business_date, reference, memo, source_type, "
        " source_id, currency, reversed_journal_id, posted_by, uid, source_uid) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, branch_id, business_date, reference, memo, source_type, source_id,
         currency, reversed_journal_id, posted_by or 'System', uid, source_uid)
    )
    journal_id = cur.lastrowid
    for code, d, c, line_memo in clean_lines:
        account_id = _account_id(conn, cid, code)
        cur.execute(
            "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) "
            "VALUES (?,?,?,?,?)",
            (journal_id, account_id, float(d), float(c), line_memo)
        )
    _queue_gl_journal_sync_event(conn, journal_id)
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

    F3b -- IDEMPOTENT AND DETERMINISTIC. A journal is reversed AT MOST ONCE:
    if a reversal of it already exists (posted here, or arrived by sync),
    this returns THAT reversal's id and posts nothing. The reversal's `uid`
    is `reversal_uid(<original's uid>)`, so two computers that each reverse
    the same journal while offline mint the SAME uid and the sync apply
    collapses them into one row (a partial UNIQUE index on `(company_id,
    reversed_journal_id)` backs this at the database level). A legacy
    original with no uid falls back to a random uid: nothing else can name it
    across devices anyway.
    """
    original = conn.execute(
        "SELECT branch_id, business_date, reference, currency, uid "
        "FROM gl_journals WHERE id=? AND company_id=?",
        (journal_id, cid)
    ).fetchone()
    if not original:
        raise GeneralLedgerAccountError(
            f"cannot reverse journal {journal_id}: no such journal for company {cid!r}"
        )
    existing = conn.execute(
        "SELECT id FROM gl_journals WHERE company_id=? AND reversed_journal_id=? "
        "ORDER BY id LIMIT 1",
        (cid, journal_id)
    ).fetchone()
    if existing:
        return existing['id']
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
        uid=reversal_uid(original['uid']) if original['uid'] else None,
    )


def trial_balance(conn, cid, as_of=None):
    """Every seeded account for company `cid`, with its debit/credit
    totals -- lifetime, unless `as_of` narrows them -- and net `balance`
    (signed per the account's own `normal_balance` -- positive means "a
    normal balance for this account type", matching how a real trial
    balance reads).

    COMPANY SCOPING WITHOUT TOUCHING `gl_journals` AT ALL (the `as_of=None`
    path only): `gl_journal_lines` carries no `company_id` of its own (see
    the module docstring), but every line's `account_id` was resolved by
    `_account_id`, which only ever returns an id scoped `WHERE
    company_id=?` -- so `gl_journal_lines.account_id` is transitively
    company-scoped through `gl_accounts`, and joining on `a.company_id = ?`
    alone is already correct; no second join to `gl_journals` is needed to
    avoid a cross-tenant leak here.

    `LEFT JOIN`, not `JOIN`: an account `ensure_default_chart` seeded but
    nothing has ever posted to (the common case for 1200/5900/3000 in a
    v38 install -- see DEFAULT_CHART's own comment) must still appear, at
    zero, not vanish from the report.

    `as_of` (P0 item #15d, interface I-5) -- an optional `'YYYY-MM-DD'`
    string. `None` (the default, and the ONLY behaviour that existed before
    this parameter) is the original all-time query above: no join to
    `gl_journals` at all, so a v38/v39 install that has never posted a
    single journal outside today still sees the exact same numbers it
    always has, byte for byte -- adding an optional filter must never
    change the no-filter answer. When given, `business_date <= as_of` is
    applied INSIDE a subquery's WHERE (never appended outside the join),
    the identical shape `financial_statements.balance_sheet` already uses
    for its own `as_of_date` and for the identical reason that function's
    own comment gives: an account with real lifetime activity but nothing
    ON OR BEFORE this specific date must still come back a LEFT-JOIN NULL
    (COALESCE'd to 0), never vanish from the report. Filtered on
    `gl_journals.business_date`, the accounting date, NEVER `posted_at` --
    matching every other period-scoped query in this codebase (see
    `financial_statements.py`'s module docstring point 3, not repeated
    here). Raises `ValueError` on a string that is not a real calendar
    date -- this module has no `StatementPeriodError` of its own
    (`financial_statements.py` imports `general_ledger`, so the reverse
    import would be circular), and the route this function serves
    (`api/retail_api.py::report_trial_balance`) catches plain `ValueError`
    for the same 400-with-message contract `report_balance_sheet` gives
    `StatementPeriodError`.

    Company-wide, all branches -- v38's branch-scoping decision is
    unchanged and out of scope for this date-only addition (see the
    ROADMAP.md entry's "Is NOT" list).
    """
    ensure_default_chart(conn, cid)
    currency = _resolve_currency(conn, cid)
    quant = currency_quantum(currency)

    if as_of is not None:
        try:
            _as_of_date = datetime.strptime(as_of, '%Y-%m-%d')
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"as_of must be a 'YYYY-MM-DD' date, got {as_of!r}"
            ) from exc
        # P0 round 2 (item B2): NORMALIZE, do not just VALIDATE.
        # `datetime.strptime(..., '%Y-%m-%d')` is lenient about a
        # non-zero-padded month/day -- '2026-9-1' parses successfully to the
        # same date as '2026-09-01' -- but the query below compares
        # `gl_journals.business_date` (always stored zero-padded ISO,
        # 'YYYY-MM-DD') against `as_of` as a plain SQLite TEXT string, not a
        # calendar date. Lexicographic string comparison disagrees with
        # calendar order the instant the string is not zero-padded:
        # '2026-09-15' < '2026-9-1' as TEXT (comparing the 6th character,
        # '0' < '9'), so an as_of of '2026-9-1' would have INCLUDED every
        # September date up to the 9-<=-9 boundary and EXCLUDED October
        # dates that string-sort before it -- silently wrong totals with no
        # error anywhere, exactly the trap this line existed to guard
        # against by validating the date at all. Reassigning `as_of` to the
        # parsed date's own `.isoformat()` (always zero-padded) before it
        # ever reaches the SQL parameter below closes that gap for every
        # caller, not just a hypothetical one: `financial_statements.py`'s
        # `income_statement`/`balance_sheet` had the identical bug (see
        # their own `_parse_period_date` call sites) and got the same fix.
        as_of = _as_of_date.date().isoformat()

    if as_of is None:
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
    else:
        rows = conn.execute(
            "SELECT a.code, a.name, a.account_type, a.normal_balance, "
            "       COALESCE(SUM(fl.debit), 0) AS total_debit, "
            "       COALESCE(SUM(fl.credit), 0) AS total_credit "
            "FROM gl_accounts a "
            "LEFT JOIN ( "
            "    SELECT gjl.account_id, gjl.debit, gjl.credit "
            "    FROM gl_journal_lines gjl "
            "    JOIN gl_journals gj ON gj.id = gjl.journal_id "
            "    WHERE gj.company_id = ? AND gj.business_date <= ? "
            ") fl ON fl.account_id = a.id "
            "WHERE a.company_id = ? "
            "GROUP BY a.id ORDER BY a.code",
            (cid, as_of, cid)
        ).fetchall()

    accounts = []
    total_debit_all = Decimal('0')
    total_credit_all = Decimal('0')
    total_closing_debit_all = Decimal('0')
    total_closing_credit_all = Decimal('0')
    for r in rows:
        debit = Decimal(str(r['total_debit']))
        credit = Decimal(str(r['total_credit']))
        total_debit_all += debit
        total_credit_all += credit
        net = (debit - credit) if r['normal_balance'] == 'debit' else (credit - debit)
        # Closing Dr/Cr (P0 item #15d): sided by the BALANCE SIGN, not just
        # the account's normal side -- a `net` of 0 or more sits on the
        # account's own normal side (the common case: an asset with a debit
        # balance shows in the Dr column); a NEGATIVE `net` is an abnormal
        # balance (e.g. Inventory oversold into a credit position -- the
        # exact -0.792-with-no-side-shown case this item's own bug report
        # names) and must flip to the OPPOSITE column instead of printing a
        # signed negative number in a column that is supposed to only ever
        # hold non-negative amounts. Exactly one of the two is nonzero for
        # every row, so `total_closing_debit`/`total_closing_credit` below
        # are just a re-sorted split of the same lifetime debit/credit
        # totals and are therefore ALWAYS equal to each other -- this is
        # arithmetic (both column pairs sum to the same total_debit_all -
        # total_credit_all), not a claim that needs its own reconciliation.
        if net >= 0:
            closing_debit = net if r['normal_balance'] == 'debit' else Decimal('0')
            closing_credit = net if r['normal_balance'] == 'credit' else Decimal('0')
        else:
            closing_debit = -net if r['normal_balance'] == 'credit' else Decimal('0')
            closing_credit = -net if r['normal_balance'] == 'debit' else Decimal('0')
        total_closing_debit_all += closing_debit
        total_closing_credit_all += closing_credit
        accounts.append({
            'code': r['code'], 'name': r['name'], 'account_type': r['account_type'],
            'normal_balance': r['normal_balance'],
            'debit': float(debit.quantize(quant, rounding=ROUND_HALF_UP)),
            'credit': float(credit.quantize(quant, rounding=ROUND_HALF_UP)),
            'balance': float(net.quantize(quant, rounding=ROUND_HALF_UP)),
            'closing_debit': float(closing_debit.quantize(quant, rounding=ROUND_HALF_UP)),
            'closing_credit': float(closing_credit.quantize(quant, rounding=ROUND_HALF_UP)),
        })

    eps = _gl_epsilon(currency)
    return {
        'currency': currency,
        'as_of': as_of,
        'accounts': accounts,
        'total_debit': float(total_debit_all.quantize(quant, rounding=ROUND_HALF_UP)),
        'total_credit': float(total_credit_all.quantize(quant, rounding=ROUND_HALF_UP)),
        'closing_debit': float(total_closing_debit_all.quantize(quant, rounding=ROUND_HALF_UP)),
        'closing_credit': float(total_closing_credit_all.quantize(quant, rounding=ROUND_HALF_UP)),
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

    `tb['as_of']` NOT NONE (P0 item #15d, reviewer finding): `customers`/
    `suppliers.credit_balance` are LIVE, TODAY'S figures -- there is no
    history table behind them (rule 2 in this module's own header:
    `AR`/`AP` themselves are the closest thing to a subledger this codebase
    keeps). `tb`'s own GL side, by contrast, is correctly as-of the date
    asked for. Comparing today's live balance against a PAST GL balance
    reports a false "Does Not Tie" the instant AR/AP has moved even once
    since that date -- measured: one paid PO today, `as_of=yesterday`, gave
    `ap_gl=0.0` (correct: nothing had posted by yesterday) against
    `ap_subledger=10.0` (today's live balance), `ap_ties=False`, on a ledger
    that in fact ties perfectly for both "all time" and "as of today". The
    comparison is therefore SKIPPED entirely for a genuine past date: `ar_
    ties`/`ap_ties` come back `True` (there being nothing false to report --
    a past-dated GL slice was never claimed to tie to today's live balance
    in the first place) and `applicable` is `False`, so a caller that reads
    THAT key can render "not applicable for a past date" instead of either
    badge. The subledger figures themselves are set to mirror the GL side
    (`ar_subledger = ar_gl`, `ap_subledger = ap_gl`) rather than left at
    today's live, unrelated number or at `None` -- both of the routes this
    function serves render `ar_subledger`/`ap_subledger` unconditionally, so
    a caller that has not yet been updated to branch on `applicable` still
    shows one internally-consistent pair of figures and a "Ties" badge
    instead of a stale, contradicting live balance next to a red "Does Not
    Tie" that was never really about this date at all.

    P0 round 3 (item V5, reviewer finding) UPDATED the MECHANISM, not the
    goal just described: `applicable` used to be decided by `tb['as_of'] <
    _today()`, the server's own clock. That is wrong the moment a journal
    can be business-dated after this server's "today" (the v41 GL journal
    sync makes cross-device clock/timezone skew real, not hypothetical) --
    `as_of=today` then fell through to a REAL comparison and reported a
    false "Does Not Tie" against a journal the live subledger already
    counted but `tb` correctly excluded. `applicable` is now decided from
    the DATA: false whenever any `gl_journals` row for this company is
    dated after `as_of`, regardless of what day it is. See the code just
    below for the query and the exact measured case.
    """
    ar_gl = _account_balance_in(tb, ACCOUNT_AR)
    ap_gl = _account_balance_in(tb, ACCOUNT_AP)
    eps = _gl_epsilon(tb['currency'])

    # P0 round 3 (item V5, reviewer finding): this used to read `tb['as_of']
    # < _today()` -- THE SERVER'S OWN CLOCK. That is the wrong question. A
    # peer's clock/timezone skew, or a shop `business_timezone` ahead of
    # this server, can post a journal business-dated AFTER this server's
    # own "today" -- the v41 GL journal sync makes this a real cross-device
    # case, not a hypothetical one. When that happens with `as_of=today`,
    # the old check's `today < today` was False, so it fell through to the
    # REAL comparison below -- and reported a false "Does Not Tie", because
    # `tb` (filtered `business_date <= as_of`) correctly excludes that
    # future-dated journal while the live `credit_balance` subledger side
    # already reflects it (that journal's underlying sale/payment already
    # happened; only its BUSINESS DATE is ahead of as_of). Measured
    # (probe3.log): a credit sale posted with `business_date` tomorrow made
    # `as_of=today` report `ar_gl=10.0` / `ar_subledger=20.0` / `ar_ties=
    # False`, while all-time and `as_of=tomorrow` both tied at 20/20 --
    # the SAME data, disagreeing only because of which clock decided
    # "applicable".
    #
    # Decided from the DATA instead: the comparison is applicable exactly
    # when no `gl_journals` row for this company is dated after `as_of` --
    # i.e. nothing has been excluded from `tb` that the live subledger
    # could already be counting. This subsumes the old past-date case (a
    # genuine past `as_of` almost always has at least one journal dated
    # after it, namely everything posted since) without ever asking what
    # day the SERVER thinks it is, and it also catches the future-dated-
    # journal case the old clock check could not, because the question is
    # now "does excluded data exist" rather than "is as_of before today".
    _future_journal = conn.execute(
        "SELECT 1 FROM gl_journals WHERE company_id=? AND business_date > ? LIMIT 1",
        (cid, tb['as_of']),
    ).fetchone() if tb.get('as_of') is not None else None
    if _future_journal is not None:
        return {
            'ar_gl': ar_gl, 'ar_subledger': ar_gl, 'ar_ties': True,
            'ap_gl': ap_gl, 'ap_subledger': ap_gl, 'ap_ties': True,
            'applicable': False,
            'reason': (
                'a journal is business-dated after as_of; subledger balances are '
                'current-only and not comparable while activity is excluded by the date filter'
            ),
        }

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
        # v44: the subledger side is the DERIVED per-party balances summed
        # (every party, negatives included -- exactly what SUM(credit_balance)
        # covered), not the per-computer stored cache. On a computer that only
        # received a debtor's sales by sync the cache said 0 while the synced
        # books held the AR, so the tie broke on every non-originating computer.
        from core.retail import party_balance as _pb
        derive = _pb.customer_balances if table == 'customers' else _pb.supplier_balances
        return float(sum(derive(conn, cid, tb['currency']).values(), Decimal('0')))

    ar_sub = _credit_balance_sum('customers')
    ap_sub = _credit_balance_sum('suppliers')

    ar_ties = abs(Decimal(str(ar_gl)) - Decimal(str(ar_sub))) <= eps
    ap_ties = abs(Decimal(str(ap_gl)) - Decimal(str(ap_sub))) <= eps

    # No `applicable` key on this, the pre-existing (`as_of=None`) path --
    # deliberately BYTE-COMPATIBLE with every caller from before P0 item
    # #15d, including retail_gl_route_test.py's own exact-dict-equality
    # guard (`test_subledger_tie_does_not_explode_when_credit_balance_was_
    # never_created`), matching this codebase's "invisible unless opted in"
    # rule (CLAUDE.md): the new key only appears on the NEW, explicitly
    # opted-into `as_of` path above, never on the response every existing
    # caller already reads.
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
                 posted_by=None, tax_refunded=0, points_restored_value=0):
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

    DAY-1d -- THE RETURN GIVES BACK ITS SHARE OF THE TAX. The sale credited
    Tax Payable for the tax it collected; a refund is tax-inclusive, so
    debiting ALL of it to Sales Returns left Tax Payable overstated by the tax
    inside every refund and revenue understated by the same amount (measured
    in the day-in-shop run).

    DAY-1d review F1 -- ONE STORED FIGURE. `tax_refunded` is `returns.
    tax_refunded`, decided once by create_return from the sale lines'
    snapshotted rates and capped at what this return settles; this function
    does NOT derive a ratio of its own (the first cut did, and disagreed with
    the tax report for mixed-rate baskets and points-paid sales). The tax
    report reads the same stored column, so the books and the report cannot
    drift. Dr Tax Payable `tax_refunded`, Dr Sales Returns `settled -
    tax_refunded`: both debits sum to `settled`, so the journal balances by
    construction. A return with no tax splits nothing. Journals already posted
    are NOT rewritten.

    DAY-1d round 3 R3 -- POINTS GIVEN BACK. `points_restored_value` is the money
    value of the loyalty points THIS return restores to the customer. post_sale
    debited Loyalty Program Expense for the points a sale redeemed; giving them
    back must credit the same account, or the expense stays booked for points
    the customer got back (and `settled` excluded that value, so the tax of a
    points-paid taxed line was stranded and the result depended on return
    order). It is part of `settled`: Cr Loyalty Program Expense
    `points_restored_value`, and the debits (Tax Payable + Sales Returns) cover
    cash + AR + points, so the journal still balances by construction.
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
    if points_restored_value and points_restored_value > 0:
        lines.append({'account_code': ACCOUNT_LOYALTY_EXPENSE, 'credit': points_restored_value,
                       'memo': 'Loyalty points given back'})
        settled += Decimal(str(points_restored_value))
    if settled <= 0:
        # Nothing was actually settled against this return (the DEFECT-1B
        # edge case, or a return the caller should not have posted at
        # all). Correct to no-op: create_return itself calls neither
        # `_adjust_credit` nor writes a nonzero `tender_refund_amount` in
        # this case, so the GL agreeing by posting nothing is the two
        # ledgers staying in sync, not a gap.
        return None
    quant = currency_quantum(currency or DEFAULT_BASE_CURRENCY)
    # Defensive only: the caller already capped it at `settled`.
    tax_part = max(Decimal('0'), min(
        Decimal(str(tax_refunded or 0)).quantize(quant, rounding=ROUND_HALF_UP), settled))
    if tax_part > 0:
        lines.append({'account_code': ACCOUNT_TAX_PAYABLE, 'debit': tax_part,
                       'memo': f'Tax on return {return_number}'})
    lines.append({'account_code': ACCOUNT_SALES_RETURNS, 'debit': settled - tax_part,
                   'memo': f'Return {return_number}'})
    return post_journal(conn, cid, branch_id, business_date or _today(),
                         'return', return_id, currency, lines,
                         reference=return_number, posted_by=posted_by)


def post_customer_payment(conn, cid, branch_id, reference, currency, amount,
                           business_date=None, posted_by=None, source_uid=None):
    """Dr Cash / Cr Accounts Receivable -- a customer paying down what they
    owe. `amount` must be the exact figure already passed to both
    `_record_payment` and `_adjust_credit` at the call site (customer_
    payment in api/retail_api.py), not re-derived.

    `source_uid` (F3b) is the PAYMENT ROW'S OWN `uid`. It is the source link
    a later void finds this journal by -- never by reference plus amount,
    because reference counters are per-device and collide across tills.
    """
    if not amount or amount <= 0:
        return None
    lines = [
        {'account_code': ACCOUNT_CASH, 'debit': amount, 'memo': 'Customer payment received'},
        {'account_code': ACCOUNT_AR, 'credit': amount, 'memo': 'Accounts receivable settled'},
    ]
    return post_journal(conn, cid, branch_id, business_date or _today(),
                         'customer_payment', None, currency, lines,
                         reference=reference, posted_by=posted_by,
                         source_uid=source_uid)


def post_supplier_payment(conn, cid, branch_id, reference, currency, amount,
                           source_type='supplier_payment', source_id=None,
                           business_date=None, posted_by=None, source_uid=None):
    """Dr Accounts Payable / Cr Cash -- the shop paying down what it owes a
    supplier. ONE helper for BOTH `supplier_payment` (a direct account
    payment, `source_id=None`) and `pay_purchase_order` (an instalment
    against one PO, `source_type='po_payment', source_id=po_id`) -- the
    accounting entry is identical in both cases (see api/retail_api.py's
    own `supplier_payment`/`pay_purchase_order`, which differ only in
    which `purchase_orders` row they also touch), so this is one function
    with a doc-metadata parameter rather than two near-duplicates.

    `source_uid` (F3b): the payment row's own `uid`, the source link a later
    void finds this journal by (`source_id` cannot be it: every instalment
    against one PO shares the PO's `source_id`).
    """
    if not amount or amount <= 0:
        return None
    lines = [
        {'account_code': ACCOUNT_AP, 'debit': amount, 'memo': 'Accounts payable settled'},
        {'account_code': ACCOUNT_CASH, 'credit': amount, 'memo': 'Payment to supplier'},
    ]
    return post_journal(conn, cid, branch_id, business_date or _today(),
                         source_type, source_id, currency, lines,
                         reference=reference, posted_by=posted_by,
                         source_uid=source_uid)


def post_purchase_order(conn, cid, branch_id, po_id, po_number, currency,
                         amount_paid, ap_balance, business_date=None, posted_by=None):
    """Dr Goods Ordered Not Received (1260) against Cr Cash (any down
    payment) + Cr Accounts Payable (whatever remains).

    STOCK-BOOKS: an ORDER is not stock. The debit used to land on Inventory
    (1200) the moment the PO was written, so the books claimed shelves the
    shop did not yet have (and a cancelled order left them overstated). It
    now lands on 1260; `post_po_receipt` moves it to 1200 when the goods
    are actually received. INTERIM, stated plainly: Accounts Payable is
    still recognised at ORDER time (the v44 supplier subledger counts the PO
    total at order) -- moving AP to receipt time belongs with W4a. A PO
    posted before this wave keeps its old Dr 1200 journal untouched
    (`po_order_journal` reports which account it debited).

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
        {'account_code': ACCOUNT_GOODS_ORDERED, 'debit': accounted,
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


# ── STOCK-BOOKS: stock events in the books ───────────────────────────────
#
# Every helper below is ONCE-ONLY (`_post_once`: a deterministic uid from
# `stock_journal_uid`, so a repeat call returns the existing journal) and
# posts NOTHING at an amount at or below half the currency's minor unit --
# exactly like `post_cogs`: an unknown/zero cost is silence, never an invented
# figure and never a blocked document. The caller (api/retail_api.py) passes
# figures it already wrote on the document; these helpers do not re-derive
# stock or cost, EXCEPT where noted (the transfer receipt reads the send
# journal's own amount back, and the PO receipt reads the order journal's).

def _stock_amount(qty, unit_cost, currency):
    return quantize_money(abs(Decimal(str(qty or 0))) * Decimal(str(unit_cost or 0)), currency)


def po_order_journal(conn, cid, po_id):
    """The ORDER journal of purchase order `po_id` on THIS computer, or None:
    {'id', 'uid', 'debit_code', 'debit_amount'} where `debit_code` is the
    account its (single) debit line hit -- '1260' for an order posted by this
    wave, '1200' for a legacy PO posted before it -- and `debit_amount` is
    that line's amount as a Decimal.

    Looked up by `source_type='purchase_order' AND source_id=po_id`: safe
    because POs are local to the computer that wrote them, and a synced
    journal arrives with `source_id` NULL so it can never match a local PO
    id. Reversal journals have their own `source_type`, so the first row is
    always the original; the earliest id wins if a PO somehow has several."""
    row = conn.execute(
        "SELECT id, uid FROM gl_journals WHERE company_id=? AND source_type='purchase_order' "
        "AND source_id=? AND reversed_journal_id IS NULL ORDER BY id LIMIT 1",
        (cid, po_id)
    ).fetchone()
    if not row:
        return None
    debit = conn.execute(
        "SELECT a.code AS code, l.debit AS debit FROM gl_journal_lines l "
        "JOIN gl_accounts a ON a.id = l.account_id "
        "WHERE l.journal_id=? AND l.debit > 0 ORDER BY l.id LIMIT 1",
        (row['id'],)
    ).fetchone()
    if not debit:
        return None
    return {'id': row['id'], 'uid': row['uid'], 'debit_code': debit['code'],
            'debit_amount': Decimal(str(debit['debit']))}


def post_po_receipt(conn, cid, branch_id, po_id, po_number, currency, posted_by=None):
    """Goods ordered have arrived: Dr Inventory (1200) / Cr Goods Ordered Not
    Received (1260), for the ORDER journal's own 1260 debit (NOT qty x cost:
    a supplier-less, partly paid PO booked only the accounted part, and the
    receipt must move exactly what the order put into 1260, no more).

    Posts NOTHING (returns None) for a legacy PO (its order journal already
    debited Inventory directly -- booking again would count the stock twice)
    and for a PO with no order journal at all. Once-only on a uid derived from
    the ORDER journal's uid (never the PO's per-computer integer id)."""
    order = po_order_journal(conn, cid, po_id)
    if not order or order['debit_code'] != ACCOUNT_GOODS_ORDERED or not order['uid']:
        return None
    amount = order['debit_amount']
    if amount <= _gl_epsilon(currency):
        return None
    lines = [
        {'account_code': ACCOUNT_INVENTORY, 'debit': amount,
         'memo': f'Goods received, purchase order {po_number}'},
        {'account_code': ACCOUNT_GOODS_ORDERED, 'credit': amount,
         'memo': f'Order {po_number} received'},
    ]
    return _post_once(conn, cid, stock_journal_uid(order['uid'], 'po_receipt'),
                      branch_id, _today(), 'po_receipt', po_id, currency, lines,
                      reference=po_number, posted_by=posted_by,
                      source_uid=order['uid'])


def post_transfer_send(conn, cid, branch_id, transfer_id, lines, currency):
    """Stock leaves the source branch: Dr Stock in Transit (1250) / Cr
    Inventory (1200) for the sum of quantize(qty x unit_cost) over `lines`
    (a list of (product_id, qty, unit_cost)) -- per-line rounding, because the
    receipt reads THIS journal's total back and must credit exactly it.
    `branch_id` is the SOURCE branch. None when the total is at or below the
    currency's half-unit floor (a cost-less transfer is not booked)."""
    amount = sum((_stock_amount(q, c, currency) for _pid, q, c in lines), Decimal('0'))
    if amount <= _gl_epsilon(currency):
        return None
    jl = [
        {'account_code': ACCOUNT_STOCK_IN_TRANSIT, 'debit': amount,
         'memo': f'Transfer {transfer_id} sent'},
        {'account_code': ACCOUNT_INVENTORY, 'credit': amount,
         'memo': 'Stock left the branch'},
    ]
    return _post_once(conn, cid, stock_journal_uid(transfer_id, 'transfer_send'),
                      branch_id, _today(), 'stock_transfer', None, currency, jl,
                      reference=str(transfer_id), source_uid=str(transfer_id))


def send_journal(conn, cid, transfer_id):
    """The send journal of `transfer_id`, or None (a transfer sent before this
    wave, or one sent at zero cost): {'id', 'uid', 'amount'} where `amount` is
    its 1250 debit as a Decimal. Found by its deterministic uid, so it is the
    same journal on every computer."""
    row = conn.execute(
        "SELECT id, uid FROM gl_journals WHERE company_id=? AND uid=?",
        (cid, stock_journal_uid(transfer_id, 'transfer_send'))
    ).fetchone()
    if not row:
        return None
    debit = conn.execute(
        "SELECT l.debit FROM gl_journal_lines l JOIN gl_accounts a ON a.id=l.account_id "
        "WHERE l.journal_id=? AND a.code=? AND l.debit > 0 ORDER BY l.id LIMIT 1",
        (row['id'], ACCOUNT_STOCK_IN_TRANSIT)
    ).fetchone()
    if not debit:
        return None
    return {'id': row['id'], 'uid': row['uid'], 'amount': Decimal(str(debit[0]))}


def post_transfer_receive(conn, cid, branch_id, transfer_id, lines, currency):
    """Stock arrives at the destination: Dr Inventory (1200) for what was
    received, Cr Stock in Transit (1250) for EXACTLY what the send journal
    debited (READ BACK, never recomputed -- per-line rounding makes a fresh
    sum drift from it by a minor unit), and the difference to Shrinkage and
    Adjustments (5910): a shortage debits it, an over-receipt credits it.
    `lines` is [(product_id, qty_sent, qty_received, unit_cost)];
    `branch_id` is the DESTINATION branch.

    Returns (journal_id, {product_id: written_off}) -- `written_off` per
    product is quantize(sent x cost) - quantize(received x cost): positive
    for a shortage, negative for an over-receipt. A transfer with NO send
    journal (sent before this wave, or at zero cost) posts nothing and
    returns (None, {}): its stock never entered 1250, so there is nothing to
    clear and 1250 can never go negative."""
    send = send_journal(conn, cid, transfer_id)
    if not send:
        return None, {}
    received_total = Decimal('0')
    written_off = {}
    for product_id, qty_sent, qty_received, unit_cost in lines:
        recv = _stock_amount(qty_received, unit_cost, currency)
        written_off[product_id] = _stock_amount(qty_sent, unit_cost, currency) - recv
        received_total += recv
    difference = send['amount'] - received_total
    jl = [
        {'account_code': ACCOUNT_INVENTORY, 'debit': received_total,
         'memo': f'Transfer {transfer_id} received'},
        {'account_code': ACCOUNT_STOCK_IN_TRANSIT, 'credit': send['amount'],
         'memo': 'Stock arrived'},
    ]
    if difference > 0:
        jl.append({'account_code': ACCOUNT_SHRINKAGE, 'debit': difference,
                   'memo': 'Transfer shortage written off'})
    elif difference < 0:
        jl.append({'account_code': ACCOUNT_SHRINKAGE, 'credit': -difference,
                   'memo': 'Transfer over-receipt'})
    journal_id = _post_once(
        conn, cid, stock_journal_uid(transfer_id, 'transfer_receive'), branch_id,
        _today(), 'stock_transfer', None, currency, jl,
        reference=str(transfer_id), source_uid=str(transfer_id))
    return journal_id, written_off


def _post_movement_journal(conn, cid, branch_id, movement_uid, qty, unit_cost,
                           reference, currency, kind, source_type, offset_code):
    amount = _stock_amount(qty, unit_cost, currency)
    if amount <= _gl_epsilon(currency):
        return None
    if Decimal(str(qty)) > 0:
        jl = [{'account_code': ACCOUNT_INVENTORY, 'debit': amount, 'memo': kind},
              {'account_code': offset_code, 'credit': amount, 'memo': kind}]
    else:
        jl = [{'account_code': offset_code, 'debit': amount, 'memo': kind},
              {'account_code': ACCOUNT_INVENTORY, 'credit': amount, 'memo': kind}]
    return _post_once(conn, cid, stock_journal_uid(movement_uid, kind), branch_id,
                      _today(), source_type, None, currency, jl,
                      reference=reference, source_uid=movement_uid)


def post_stock_adjustment(conn, cid, branch_id, movement_uid, qty, unit_cost,
                          reference, currency):
    """A manual stock in/out or a counted exception: +qty is Dr Inventory /
    Cr Shrinkage and Adjustments (5910), -qty is Dr 5910 / Cr Inventory, at
    abs(qty) x `unit_cost` (callers pass `stock_books.effective_unit_cost`).
    Keyed on the MOVEMENT's uid, so only the computer that wrote the movement
    posts it and a synced peer never does."""
    return _post_movement_journal(conn, cid, branch_id, movement_uid, qty, unit_cost,
                                  reference, currency, 'stock_adjustment',
                                  'stock_adjustment', ACCOUNT_SHRINKAGE)


def post_opening_entry(conn, cid, branch_id, movement_uid, qty, unit_cost,
                       reference, currency):
    """Stock entered at product create/import: +qty is Dr Inventory / Cr
    Opening Balance Equity (3900), -qty the mirror. NEVER source_type
    'opening_balance' -- that name belongs to the v38 AR/AP backfill and the
    sheets treat it specially."""
    return _post_movement_journal(conn, cid, branch_id, movement_uid, qty, unit_cost,
                                  reference, currency, 'opening_entry',
                                  'opening_entry', ACCOUNT_OPENING_EQUITY)


def inventory_tie(conn, cid, tb):
    """Does Inventory (1200) agree with the stock on the shelves? Returns
        {'inventory_gl', 'stock_value', 'in_transit_gl', 'in_transit_stock',
         'difference', 'unbooked_movements', 'status', 'applicable'}
    (plus 'reason' when not applicable). `tb` must be this company's
    `trial_balance(...)`, like `subledger_tie`; a SIBLING of `subledger_tie`
    rather than new keys on it, because that dict is pinned exactly by
    retail_gl_route_test.py.

    `status`, first match wins:
      1. 'not_applicable' -- any journal is dated after `tb['as_of']`: stock
         value is a live figure, the GL slice is a past one (same reasoning
         and same data-driven test as `subledger_tie`);
      2. 'not_opened' -- unbooked stock movements exist: the books have never
         been told about some stock, so a difference is missing history, not
         an error. NEUTRAL on screen, never red;
      3. 'ties' -- |difference| within half a minor unit;
      4. 'rounding' -- within that plus half a quantum per unit on hand (the
         moving average's own rounding); amber;
      5. 'differs' -- red.
    `difference` = inventory_gl - stock_value. The in-transit pair is
    reported for display; it does not feed `status` (the in-transit stock
    figure is this computer's only -- R5)."""
    from core.retail import stock_books
    currency = tb['currency']
    inv_gl = Decimal(str(_account_balance_in(tb, ACCOUNT_INVENTORY)))
    transit_gl = Decimal(str(_account_balance_in(tb, ACCOUNT_STOCK_IN_TRANSIT)))
    stock = stock_books.stock_value(conn, cid, currency)
    transit_stock = stock_books.in_transit_stock_value(conn, cid, currency)
    unbooked = stock_books.unbooked_movement_count(conn, cid)
    difference = inv_gl - stock
    result = {
        'inventory_gl': float(inv_gl), 'stock_value': float(stock),
        'in_transit_gl': float(transit_gl), 'in_transit_stock': float(transit_stock),
        'difference': float(difference), 'unbooked_movements': unbooked,
        'applicable': True,
    }
    future = conn.execute(
        "SELECT 1 FROM gl_journals WHERE company_id=? AND business_date > ? LIMIT 1",
        (cid, tb['as_of']),
    ).fetchone() if tb.get('as_of') is not None else None
    if future is not None:
        result.update(applicable=False, status='not_applicable', reason=(
            'a journal is business-dated after as_of; stock value is a current '
            'figure and not comparable with a past slice of the books'))
        return result
    eps = _gl_epsilon(currency)
    if unbooked > 0:
        result['status'] = 'not_opened'
    elif abs(difference) <= eps:
        result['status'] = 'ties'
    elif abs(difference) <= eps + stock_books.rounding_allowance(conn, cid, currency):
        result['status'] = 'rounding'
    else:
        result['status'] = 'differs'
    return result
