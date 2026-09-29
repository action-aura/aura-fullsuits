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

import logging
import re
import sqlite3

_log = logging.getLogger(__name__)

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


#: Where each doc type's number lives. All three columns carry a BARE UNIQUE
#: index (not company-scoped), so "taken" is checked across the whole table.
NUMBER_COLUMNS = {
    'sale': ('sales', 'sale_number'),
    'return': ('returns', 'return_number'),
    'po': ('purchase_orders', 'po_number'),
}


def number_is_taken(conn, doc_type, number) -> bool:
    """True when `number` already exists in the document table for
    `doc_type` on THIS database (ours, or pulled from another computer)."""
    spot = NUMBER_COLUMNS.get(doc_type)
    if spot is None:
        return False
    table, column = spot
    try:
        return conn.execute(
            f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", (number,)
        ).fetchone() is not None
    except sqlite3.OperationalError as exc:
        # A database that has no such document table (a bare test fixture, a
        # book created before the sales schema) cannot hold a taken number.
        # ONLY that case is answered "free"; any other failure must surface.
        if 'no such table' in str(exc):
            return False
        raise


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
    skipped = 0
    while True:
        conn.execute(
            "UPDATE doc_series_counter SET last_no = last_no + 1 WHERE series_id=?",
            (series_row['id'],),
        )
        n = conn.execute(
            "SELECT last_no FROM doc_series_counter WHERE series_id=?",
            (series_row['id'],),
        ).fetchone()[0]
        number = f"{series_row['code']}-{int(n):0{series_row['pad_width']}d}"
        # NEVER HAND OUT A NUMBER THAT ALREADY EXISTS HERE. The counter is
        # device-local, but the document tables it feeds are not: a sale
        # pulled from another computer can already carry the very number this
        # counter is about to mint (two computers that held the same book
        # code, or a book re-created after a restore). Before this loop that
        # was a permanent wedge -- every later sale on this till died with
        # `UNIQUE constraint failed: sales.sale_number` (re-verifier B2, X4),
        # because the counter rolled back with the failed sale and offered the
        # same taken number again forever. Advancing past it is inside the
        # caller's own transaction, so the skipped counter values are
        # committed together with the document that finally takes a free
        # number, or rolled back together with it.
        if not number_is_taken(conn, series_row['doc_type'], number):
            break
        skipped += 1
    if skipped:
        _log.warning(
            "doc_series %s (%s): skipped %d number(s) already present locally, "
            "next free is %s", series_row['id'], series_row['code'], skipped, number)
    return number


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
    # Mirror `allocate`'s skip-past-taken rule so the card never promises a
    # number the next document will not actually carry.
    nxt = int(last_no) + 1
    while True:
        number = f"{series_row['code']}-{nxt:0{series_row['pad_width']}d}"
        if not number_is_taken(conn, series_row['doc_type'], number):
            return number
        nxt += 1


# ── Readable per-till books (Retail UX P1b item 6) ───────────────────────────
# A till the OWNER has switched on (per computer, with an explicit till number)
# gets its own sale / return / purchase-order series at its next shift, coded
# from the branch and the till number: sale `ABD1`, return `ABD1-R`, purchase
# order `ABD1-P`, so the receipt reads `ABD1-000001` instead of the legacy
# `SALE-<device>-000001`. NOTHING switches this on by itself -- see
# retail_api.py's numbering block for why (a number picked automatically on
# two computers that have not heard from each other collides silently).
# Everything below is PURE (no Flask, no settings, no sync): retail_api.py
# decides WHEN (the per-till switch, the hook, the routes); this module decides
# WHAT the codes are and WHETHER a till number is free, so the naming rules
# live in one testable place.
#
# `reserved` is always passed IN by the caller (the route derives it from the
# tax-authority sequence module's own prefix table plus REF_PREFIX). It is
# deliberately a parameter and not an import here -- see this module's opening
# docstring for why nothing in this file may reach for that package.

#: The suffix each doc type's book code carries after `<branch><till>`. A
#: sale book has none, so the everyday receipt is the shortest one.
BOOK_SUFFIX = {'sale': '', 'return': '-R', 'po': '-P'}

#: Shop-facing label prefix for the settings card ("Sales ABD1").
BOOK_LABEL = {'sale': 'Sales', 'return': 'Returns', 'po': 'Purchases'}

#: Branch codes are 2-4 upper-case LETTERS, nothing else. Why letters only, and
#: why NO separator between the letters and the till number (`ABD1`, not
#: `ABD-1`): a code made of letters followed by digits reads back in exactly one
#: way -- `ABD12` is branch ABD, till 12, never branch AB, till D12 -- so two
#: different (branch, till) pairs can never spell the same book code, and the
#: receipt stays as short as the plan's own example `ABD1-000124`. A branch code
#: that could itself end in a digit ("AB1" + till 2 = "AB12" = "AB" + till 12)
#: would break that, which is the only reason digits are not allowed. The
#: ceiling is `_DOC_SERIES_CODE_RE`'s 12 characters: 4 letters + 2 digits + '-R'.
_BRANCH_CODE_RE = re.compile(r'^[A-Z]{2,4}$')
_ASCII_LETTER_RE = re.compile(r'[A-Za-z]')

#: The highest till number an owner can choose; keeps `<code><till>-R` short.
MAX_TILL_NUMBER = 99

#: The pad width a readable book uses; `ABD1-000001`.
READABLE_PAD_WIDTH = 6


def normalise_branch_code(raw):
    """`raw` stripped and upper-cased if it is a valid branch code (2-4 ASCII
    letters), else None. Never raises on a non-string."""
    if not isinstance(raw, str):
        return None
    code = raw.strip().upper()
    return code if _BRANCH_CODE_RE.match(code) else None


def default_branch_code(name, reserved=()):
    """The code offered for a branch before its owner chooses one: its first up
    to three ASCII LETTERS, upper-cased ("Abdoun" -> "ABD"). None -- never a
    made-up fallback -- when the name gives fewer than two letters (an
    Arabic-only name, empty) or the result is a reserved prefix: a silent
    "BR" on every such branch would give two branches the same code and hide
    that the owner has never been asked. The screen asks instead."""
    letters = _ASCII_LETTER_RE.findall(name if isinstance(name, str) else '')
    code = ''.join(letters[:3]).upper()
    if len(code) < 2 or code in reserved:
        return None
    return code


def book_code(branch_code, till_number, doc_type):
    """`ABD` + 1 + 'sale' -> `ABD1`; 'return' -> `ABD1-R`; 'po' -> `ABD1-P`."""
    return f"{branch_code}{int(till_number)}{BOOK_SUFFIX[doc_type]}"


def till_codes(branch_code, till_number):
    """{doc_type: code} for one till: the three codes that must ALL be free (or
    already this terminal's own) before that till number is usable."""
    return {dt: book_code(branch_code, till_number, dt) for dt in DOC_SERIES_TYPES}


def inspect_till_books(conn, company_id, terminal_uid, branch_code, till_number):
    """Where THIS terminal stands against one (branch code, till number). Pure
    read; inserting / reactivating is the caller's job, in its own
    transaction. Returns a dict:

      codes        {doc_type: code} -- the three codes this till would use
      have         [row] this terminal's own ACTIVE book with exactly that code
      reactivate   [row] this terminal's own RETIRED book with exactly that code
                   (switched off earlier; the counter is untouched, so turning
                   it back on continues the numbers instead of repeating them)
      create       [doc_type] no book anywhere has that code
      conflicts    [{doc_type, code}] a book with that code exists but is NOT
                   this terminal's (another computer's, another company's, or
                   an unclaimed one). `idx_doc_series_code` is unique on
                   (doc_type, code) GLOBALLY, so this check is global too.
      other_active [{doc_type, code}] this terminal already numbers a document
                   type with some OTHER active book -- a hand-made one, or a
                   readable one under a different till number. Two active
                   books for one (terminal, doc_type) would make
                   `resolve_series` alternate, so the caller refuses.

    A terminal with no identity can never own a book (see `resolve_series`):
    every code is then reported as a conflict-free `create` but the callers
    refuse earlier, on the missing identity."""
    codes = till_codes(branch_code, till_number)
    out = {'codes': codes, 'have': [], 'reactivate': [], 'create': [],
           'conflicts': [], 'other_active': []}
    for dt, code in codes.items():
        row = conn.execute(
            "SELECT * FROM doc_series WHERE doc_type=? AND code=?", (dt, code)).fetchone()
        if row is None:
            out['create'].append(dt)
        elif row['company_id'] == company_id and terminal_uid and row['allocator_terminal_uid'] == terminal_uid:
            (out['have'] if row['status'] == 'active' else out['reactivate']).append(row)
        else:
            out['conflicts'].append({'doc_type': dt, 'code': code})
    if terminal_uid:
        for row in conn.execute(
                "SELECT doc_type, code FROM doc_series WHERE company_id=? AND "
                "allocator_terminal_uid=? AND status='active'", (company_id, terminal_uid)):
            if row['doc_type'] in codes and row['code'] != codes[row['doc_type']]:
                out['other_active'].append({'doc_type': row['doc_type'], 'code': row['code']})
    return out


def new_book_spec(doc_type, code):
    """(label, pad_width) for a readable book the hook is about to create."""
    return f"{BOOK_LABEL[doc_type]} {code}", READABLE_PAD_WIDTH


def preview_next(conn, inspected):
    """The receipts this till prints NEXT, {'sale': ..., 'return': ...}: the
    real counter of a book that exists (`peek_next_no`, so a till that has sold
    124 times says `ABD1-000125`), else the first number of a book still to be
    created. Never consumes a number."""
    existing = {r['doc_type']: r for r in inspected['have'] + inspected['reactivate']}
    out = {}
    for dt in ('sale', 'return'):
        row = existing.get(dt)
        out[dt] = (peek_next_no(conn, row) if row is not None
                   else f"{inspected['codes'][dt]}-{1:0{READABLE_PAD_WIDTH}d}")
    return out


def till_numbers_in_use(conn, branch_code):
    """Sorted till numbers for which a SALE book coded `<branch_code><N>`
    already exists anywhere on this computer (any company, any status, any
    owner): the numbers the owner cannot pick. The letters-then-digits code
    shape is what makes this a plain prefix parse."""
    pat = re.compile(r'^' + re.escape(branch_code) + r'(\d{1,2})$')
    used = set()
    for row in conn.execute("SELECT code FROM doc_series WHERE doc_type='sale' AND code LIKE ?",
                            (branch_code + '%',)):
        m = pat.match(row['code'])
        if m:
            used.add(int(m.group(1)))
    return sorted(used)
