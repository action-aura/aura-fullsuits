"""Aura Retail -- per-document-type numbering series ("books"), schema v34
(ROADMAP.md's 2026-09-15 "schema versions v32-v35 RESERVED" entry, v34 --
the Aseel تعدد الدفاتر / "multi-ledger" equivalent). Sibling of
promotions.py/po_split.py: pure functions plus two small reads/writes, no
Flask import.

THE ONE DECISION this module exists to enforce: a series names exactly one
allocating TERMINAL in its own row (`doc_series.allocator_terminal_uid`),
and `resolve_series` below can never return another device's series --
collision is prevented structurally, not merely detected after the fact.
See database/schema.py's `_migrate_add_doc_series` docstring for the full
"why" (AUDIT-032B, the bare-UNIQUE sync wedge `_device_doc_discriminator()`
already defends against, and why a naive per-book counter would delete
that protection).

THE CONSTRAINT THAT DOMINATES THIS FEATURE: this module must never import,
or in any way name, Aura's separate opt-in tax-authority invoicing
subsystem (see docs/einvoicing/phase1/invoice-numbering-audit.md for that
subsystem's own numbering rules), and must never let a series'
configuration reach that subsystem's own gapless counter. Deliberately not
even NAMED here by its usual short form, on purpose: this feature's own
sibling firewall test file (products/retail/tests/, alongside this
module's other tests) tripwires on that subsystem's name appearing
ANYWHERE in this file's text at all, comments included -- so this
docstring practices the same discipline it is describing rather than
merely asserting it. That sibling test proves the separation both
structurally (AST checks over this file and over that subsystem's own
package) and with one live sale that mints from both sequences
independently and compares them.

NO MANUAL NUMBER ENTRY, deliberately cut from v34. The submitted design's
first draft carried a `mode` column ('auto' | 'manual') so a shop could
type a specific number by hand at the till. Four reasons killed it before
this module was written:
  1. `create_sale`'s own idempotency replay path (retail_api.py) reads back
     the EXISTING `sale_number` on a retried request and runs BEFORE any
     write lock is taken -- what a retry carrying a DIFFERENT typed number
     should do is genuinely undefined, and it is the only path in this
     feature with no structural guarantee at all.
  2. It contradicts this feature's own capability argument (CAP_EMPLOYEES,
     "deciding what number a shop's invoices carry is the same authority
     as deciding its tax mode") by handing the number itself to whoever
     happens to be ringing the sale.
  3. Its only failure mode is a 409 at the counter, in front of a queue.
  4. Everything a real shop needs from it -- "my old paper book was at
     500" -- is `start_no`, which auto mode already delivers structurally
     and gaplessly (see `allocate`'s own docstring). Recorded as a
     deliberate cut in ROADMAP.md's "v34 CASHED IN" entry, with the
     conditions a future version would need (its own CAP_EMPLOYEES route,
     never `create_sale`'s body; a stated idempotency rule) -- not silence.
Consequence: there is no `mode` column on `doc_series` at all. An
unclaimed book is simply `allocator_terminal_uid IS NULL`, which already
says everything a `mode='manual'` column would have, for zero extra state.
"""
from __future__ import annotations

import sqlite3

#: MOVED here verbatim from retail_api.py's own module-level `_REF_PREFIX`
#: dict, not copied -- retail_api.py now does `_REF_PREFIX =
#: doc_series.REF_PREFIX` so `_next_ref`'s two references stay byte-
#: identical. One vocabulary, one home: a hardcoded second copy of this
#: dict anywhere would be exactly the "the two literals must match" shape
#: commercial_runtime/sync/sync_service.py's own module docstring already
#: names as a bug pattern this codebase has hit before, and this move
#: exists specifically to avoid reintroducing it here.
REF_PREFIX = {
    'sale': 'SALE', 'receipt': 'REC', 'po': 'PO', 'supplier_payment': 'PAY',
    'return': 'RET', 'hold': 'HOLD',
    # Aseel-parity wave A-PAR (schema v32): a bounce/cancel writes an
    # OPPOSITE payments row rather than voiding the original (see
    # bounce_cheque's own comment) -- this doc_type is what that row's
    # reference number is minted under. doc_sequences is keyed
    # (company_id, doc_type), so a new doc_type needs no DDL.
    'cheque_reversal': 'CHQR',
    # Aseel-parity wave A-PAR (schema v33): quotations and sales orders.
    # ONE prefix for both `doc_kind` values -- `doc_kind` records how the
    # document started (see database/schema.py's own column comment) and a
    # bare document NUMBER is not the place to also encode it; the printed
    # document itself already says "Quotation" or "Sales Order" from
    # `doc_kind`.
    'quotation': 'QUO',
}

#: The doc types THIS feature (schema v34) gives a configurable series to
#: -- a SUBSET of REF_PREFIX's keys, never a second, independently-typed
#: vocabulary (`retail_doc_series_migration_test.py`'s
#: `test_doc_series_types_is_a_subset_of_the_moved_ref_prefix_and_is_the_
#: same_object` proves the subset-and-identity relationship directly,
#: rather than trusting this comment).
#:
#: OUT of scope, with reasons (database/schema.py's own migration docstring
#: has the full write-up): 'hold' (no UNIQUE, not synced, a parked cart is
#: not an audited document); 'receipt'/'supplier_payment' (payments.
#: reference carries only a non-unique index); 'cheque_reversal' (cheques
#: are append-only with no version gate at all -- a different shape this
#: feature does not touch); 'quotation' (schema v33 -- a future wave may
#: add it here and call `resolve_series`/`allocate` unchanged, with NO
#: further migration, since both functions are already doc_type-generic).
DOC_SERIES_TYPES = ('sale', 'return', 'po')


def resolve_series(conn, company_id, doc_type, terminal_uid):
    """The ONE lookup this feature performs to decide "does THIS device own
    a configured book for this doc_type". Returns the matching
    `doc_series` row, or None when this device should take the legacy
    `_next_ref()` + fragments path (retail_api.py) instead.

    `terminal_uid` a None SHORT-CIRCUITS to None before the query runs, on
    purpose -- never a NULL-matching WHERE clause. `allocator_terminal_uid`
    is only ever NULL on an unclaimed book, and a query that matched NULL
    against a None terminal_uid would hand a till with no established
    identity yet (see database/schema.py's own AUDIT-032D paragraph) every
    unclaimed book in the company, which is precisely the two-allocators-
    one-book state this whole feature exists to prevent structurally. A
    till with no identity is never able to own a series; it always mints
    through the legacy path.

    `ORDER BY created_at, id LIMIT 1` -- deterministic, not merely stable.
    If a claim race or a hand-edited database ever left two rows matching
    this exact `(company_id, doc_type, allocator_terminal_uid, status)`
    tuple on ONE device (which the claim route's own transaction is
    designed to make impossible in the ordinary case), this keeps every
    call on that device resolving to the SAME row rather than alternating
    between two numbering series call to call -- a settings-card-visible
    condition, not a silent flip in what a receipt says.

    No query in this module (or anywhere else in this feature) can ever
    return a DIFFERENT device's series: `allocator_terminal_uid = ?` is
    always compared against THIS device's own terminal_uid, never a
    payload value or another device's identity. That is what makes
    document-number collision across devices structural rather than
    merely policed after the fact -- see database/schema.py's own
    `_migrate_add_doc_series` docstring, "THE ONE DECISION".
    """
    if not terminal_uid:
        return None
    return conn.execute(
        "SELECT * FROM doc_series WHERE company_id=? AND doc_type=? AND status='active' "
        "AND allocator_terminal_uid=? ORDER BY created_at, id LIMIT 1",
        (company_id, doc_type, terminal_uid),
    ).fetchone()


def allocate(conn, series_row) -> str:
    """Mints the next number in `series_row`'s own book. Structurally
    identical to retail_api.py's pre-existing `_next_ref()` and to Aura's
    separate tax-authority invoicing subsystem's own per-company allocator
    (`commercial_runtime/einvoicing/sequence.py` -- not named more
    precisely here on purpose, see this module's own opening docstring)
    -- all three docstrings give, and this one restates, the SAME reason:
    this function does NOT open its own transaction. The caller is already
    inside the transaction that commits the number together with the
    document it belongs to, or rolls both back together -- see
    `retail_doc_series_test.py`'s gapless-under-rollback proofs for what
    breaks the moment that stops being true.

    THE SEED IS THE CONTRACT. `doc_series.start_no` is the number the
    FIRST document in this book carries -- not an offset, not a "last used"
    value. `start_no=500` mints `A-000500` first, `A-000501` second. The
    `doc_series_counter` row is seeded to `max(start_no - 1, 0)` so that
    the FIRST `last_no + 1` lands on `start_no` itself, matched exactly by
    `retail_doc_series_test.py`'s `test_start_no_is_the_first_documents_
    number` (pinned by VALUE, not merely "does not raise") -- a shop that
    typed 500 meaning "my old paper book's next number is 500" and got 501
    has a gap it cannot explain to an auditor, which is the exact value
    this whole feature claims to deliver. `INSERT OR IGNORE` makes the seed
    a one-time event: the second and every later call for the same
    `series_id` finds the row already present and only ever increments it.
    """
    seed = max(series_row['start_no'] - 1, 0)
    conn.execute(
        "INSERT OR IGNORE INTO doc_series_counter (series_id, last_no) VALUES (?,?)",
        (series_row['id'], seed),
    )
    conn.execute(
        "UPDATE doc_series_counter SET last_no = last_no + 1 WHERE series_id=?",
        (series_row['id'],),
    )
    n = conn.execute(
        "SELECT last_no FROM doc_series_counter WHERE series_id=?",
        (series_row['id'],),
    ).fetchone()[0]
    return f"{series_row['code']}-{int(n):0{series_row['pad_width']}d}"


def peek_next_no(conn, series_row) -> str:
    """The READ-ONLY twin of `allocate` -- the exact string the NEXT
    document from this book will carry, without consuming it. Used by
    `GET /doc-series` (retail_api.py) to render "this till numbers its
    sales A-000043 next" on the settings card.

    Deliberately does NOT call `allocate` and roll back: a rolled-back
    `INSERT OR IGNORE ... UPDATE last_no=last_no+1` is not equivalent to
    never having run it under SQLite's own autoincrement-adjacent
    behaviour, and there is a much simpler answer available -- read
    `doc_series_counter` as it stands and add one, falling back to the
    UNSEEDED case (this series has never allocated yet) exactly the way
    `allocate`'s own seed arithmetic does.
    """
    row = conn.execute(
        "SELECT last_no FROM doc_series_counter WHERE series_id=?",
        (series_row['id'],),
    ).fetchone()
    last_no = row['last_no'] if row is not None else max(series_row['start_no'] - 1, 0)
    return f"{series_row['code']}-{int(last_no) + 1:0{series_row['pad_width']}d}"
