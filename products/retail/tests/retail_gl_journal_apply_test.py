"""Aura Retail -- BOOKS-WAVE1 W1 (schema v41), plan section 2.5: apply-side
tests for `SyncService._apply_event`'s NEW `gl_journal` entity-type branch
(`commercial_runtime/sync/sync_service.py`) -- Builder C's own scope.

WHAT THIS FILE PROVES. A `gl_journal` event carries one ALREADY-POSTED,
ALREADY-BALANCED `gl_journals` header plus its `gl_journal_lines` set from
the device that rang the sale (BOOKS-WAVE1-PLAN.md section 2.1, decision
"(a) Sync the journals" -- re-deriving from pulled sales/returns/purchase-
orders was rejected, see that section's own reasoning). This device never
posts a journal of its own for a pulled document; it only ever inserts the
SAME fact the originating device already validated and persisted. Every test
below drives `SyncService._apply_event` directly against a bare, real
`init_retail()` database -- the same technique `retail_stock_sync_apply_
hardening_test.py` uses for the `inventory_movement`/`branch` branches this
one is modelled on -- rather than the two-device harness (`_stock_sync_
device.py`), which is Builder D's own file and scope.

NO IMPORT OF ANYTHING BUILDER A HAS NOT FINISHED. Test payloads below are
hand-built strictly from BOOKS-WAVE1-PLAN.md section 2.3's fixed contract,
never from calling `general_ledger.post_journal`/`journal_sync_payload`
(Builder A's own emit-side file, developed in parallel in the same shared
worktree) -- so this file's own RED/GREEN status never depends on A's
landing order. The ONE deliberate exception is
`test_balance_check_agrees_with_post_journal_on_the_same_lines`, whose
entire point is to compare THIS file's `_validate_gl_journal_lines` against
`general_ledger.validated_lines` -- the pure function A's own docstring
names THIS test, by name, as the parity proof for. `ensure_default_chart`
is also imported (for seeding a realistic chart on a "decoy" company so
account ids differ from the receiver's own, per this task's own orders) --
it is a pre-existing, stable helper Wave 1 does not change, not one of the
functions this file is forbidden to depend on.

Traps this suite deliberately avoids (BOOKS-WAVE1-PLAN.md section 6):
  - never pre-seeds the RECEIVER's own chart identically to a decoy
    company's -- a decoy company's chart is seeded FIRST so account ids
    differ, catching a query that resolves an account by code alone
    without a company_id filter (`test_lines_map_by_code_to_receivers_own_
    account_ids`);
  - uses a JOD 3-decimal value (4.007) and a backdated `business_date`, not
    only round 2-decimal, today-dated figures;
  - replays and retries, not only a single apply.

Run:
    pytest products/retail/tests/retail_gl_journal_apply_test.py -v
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import database.schema as schema  # noqa: E402
from core.retail.general_ledger import ensure_default_chart  # noqa: E402
from commercial_runtime.sync.sync_service import (  # noqa: E402
    RETAIL_SYNC_ENTITY_TYPES,
    SyncService,
)
import api.retail_api as _retail_api_module  # noqa: E402
from api.retail_api import _ensure_credit_schema  # noqa: E402

RECEIVER_COMPANY_ID = "company-receiver"
DECOY_COMPANY_ID = "company-decoy-seeded-first"


@pytest.fixture
def install_recv(tmp_path):
    """Same technique as `retail_stock_sync_apply_hardening_test.py`'s own
    `install_b` fixture -- see that file's module docstring for the full
    "AURA_APP_DATA trap" reasoning (repeated here rather than imported for
    the same survive-independently reason every other duplicate helper in
    this suite is). A REAL `init_retail()` database, not a hand-built
    minimal schema -- this task's own orders require it, and it is also
    the only way to get the real v41 migration chain (`gl_journals.uid`/
    `source_uid`, the write-once trigger, `sync_apply_quarantine`) rather
    than a stale hand-copied fixture schema."""
    recv_root = tmp_path / "install_recv"
    recv_subsys = recv_root / "subsystems"
    recv_subsys.mkdir(parents=True, exist_ok=True)

    original_base_dir, original_subsys_dir = schema.BASE_DIR, schema.SUBSYS_DIR
    schema.BASE_DIR = str(recv_root)
    schema.SUBSYS_DIR = str(recv_subsys)
    try:
        schema.init_retail()
    finally:
        schema.BASE_DIR, schema.SUBSYS_DIR = original_base_dir, original_subsys_dir

    db_path = recv_subsys / "retail.db"

    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    # Stands in for the `local_ensure_schema` hook a REAL install wires into
    # its SyncService (app.py passes `_ensure_credit_schema` on both the
    # Windows and Android branch; SyncService then calls it lazily, from
    # inside `apply_pull_result`, on any batch touching sale/return/
    # payment) -- identical technique to `retail_money_sync_test.py`'s own
    # `install_b` fixture, repeated here rather than imported for the same
    # survive-independently reason every other duplicate helper in this
    # suite is. This harness never boots `app.py`'s Flask app (unlike that
    # file, which does at import time), so `_CREDIT_SCHEMA_READY` should
    # already be False here -- forced False anyway, defensively, in case a
    # future test file sharing this process already flipped it, so a
    # `sqlite3.OperationalError: table sales has no column named due_date`
    # (this suite's own `test_applying_a_pulled_sale_posts_no_journal_
    # locally`, which applies a pulled `sale` create event -- a device that
    # never served one HTTP route before its first pulled sale, exactly
    # this harness's own shape) never resurfaces silently.
    _previous_flag = _retail_api_module._CREDIT_SCHEMA_READY
    _retail_api_module._CREDIT_SCHEMA_READY = False
    try:
        _credit_conn = _get_conn()
        try:
            _ensure_credit_schema(_credit_conn)
            _credit_conn.commit()
        finally:
            _credit_conn.close()
    finally:
        _retail_api_module._CREDIT_SCHEMA_READY = _previous_flag

    return _get_conn


def _seed_decoy_company_chart(conn):
    """Seeds `DECOY_COMPANY_ID`'s own default chart FIRST, consuming the
    low `gl_accounts.id` autoincrement values, so that when the RECEIVER's
    own accounts are later seeded (by this file's own tests, or by the
    `gl_journal` branch itself from payload metadata) they land under
    DIFFERENT ids for the SAME codes. A query that resolved an account by
    `code` alone -- forgetting the `company_id=?` filter -- would silently
    pick up one of THESE rows instead (`test_lines_map_by_code_to_
    receivers_own_account_ids`'s own mutation target)."""
    ensure_default_chart(conn, DECOY_COMPANY_ID)
    conn.commit()


def _new_service(get_conn, company_id=RECEIVER_COMPANY_ID):
    return SyncService(
        client_factory=lambda: None, get_conn=get_conn,
        local_company_id_provider=lambda: company_id,
    )


def _base_payload(**overrides):
    """One well-formed `gl_journal` create-event payload, built strictly
    from BOOKS-WAVE1-PLAN.md section 2.3's fixed contract -- a JOD sale
    with a fils tender, not a round 2-decimal figure, and a backdated
    `business_date` (plan section 6, trap 6: "only round figures and only
    today's date")."""
    payload = {
        "uid": str(uuid.uuid4()),
        "business_date": "2026-09-24",
        "reference": "S-1001",
        "memo": None,
        "source_type": "sale",
        "source_uid": None,
        "branch_uid": None,
        "currency": "JOD",
        "reversed_journal_uid": None,
        "posted_by": "System",
        "posted_at": "2026-09-24 10:11:12",
        "lines": [
            {"account_code": "1000", "debit": 4.007, "credit": 0.0, "memo": "Tender retained"},
            {"account_code": "4000", "debit": 0.0, "credit": 4.007, "memo": None},
        ],
        "accounts": [],
    }
    payload.update(overrides)
    return {
        "entity_type": "gl_journal", "event_type": "create",
        "entity_id": payload["uid"] or str(uuid.uuid4()),
        "payload": payload,
    }


def _gl_journal_row(conn, uid):
    return conn.execute("SELECT * FROM gl_journals WHERE uid=?", (uid,)).fetchone()


def _gl_journal_lines_rows(conn, journal_id):
    return conn.execute(
        "SELECT * FROM gl_journal_lines WHERE journal_id=? ORDER BY id", (journal_id,)
    ).fetchall()


def _quarantine_rows(conn):
    return conn.execute(
        "SELECT entity_id, entity_type, event_type, reason, detail FROM sync_apply_quarantine"
    ).fetchall()


# ---------------------------------------------------------------------------
# test_gl_journal_is_a_retail_sync_entity_type
# ---------------------------------------------------------------------------

def test_gl_journal_is_a_retail_sync_entity_type(install_recv):
    """`gl_journal` is in the module-level constant AND in the default set
    a plain `SyncService(...)` construction (no `handled_entity_types`
    override -- the shape both of app.py's own two real call sites use)
    actually applies -- the two can drift apart if a future edit only
    updates one of them."""
    assert "gl_journal" in RETAIL_SYNC_ENTITY_TYPES
    conn = install_recv()
    try:
        svc = _new_service(install_recv)
        assert "gl_journal" in svc._handled_entity_types
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# test_pulled_journal_lands_under_receivers_company_id  (M-C1)
# ---------------------------------------------------------------------------

def test_pulled_journal_lands_under_receivers_company_id(install_recv):
    """The applied row's `company_id` is the constructor-supplied
    `local_company_id`, never anything read out of the payload -- proved
    with a HOSTILE decoy `company_id` key riding along in the payload
    (the contract itself carries no such key at all; this proves the
    branch does not even look for one under a different name).

    MUTATION M-C1: swap `local_company_id` for `p.get("company_id",
    local_company_id)` in the INSERT's bound values -- turns this RED."""
    conn = install_recv()
    try:
        ensure_default_chart(conn, RECEIVER_COMPANY_ID)
        conn.commit()
        svc = _new_service(install_recv)
        ev = _base_payload()
        ev["payload"]["company_id"] = "hostile-attacker-company"
        assert svc._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()

        row = _gl_journal_row(conn, ev["payload"]["uid"])
        assert row is not None, "journal was not applied at all"
        assert row["company_id"] == RECEIVER_COMPANY_ID, (
            f"journal was stamped with the payload's hostile company_id, not the "
            f"receiver's own: {row['company_id']!r}"
        )
        under_hostile = conn.execute(
            "SELECT 1 FROM gl_journals WHERE company_id=?", ("hostile-attacker-company",)
        ).fetchone()
        assert under_hostile is None, "a gl_journals row was filed under the hostile company_id at all"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# test_lines_map_by_code_to_receivers_own_account_ids  (M-C2)
# ---------------------------------------------------------------------------

def test_lines_map_by_code_to_receivers_own_account_ids(install_recv):
    """Another company's chart is seeded FIRST (consuming the low
    `gl_accounts.id` values), then the receiver's own chart is seeded
    SEPARATELY -- so account "1000"/"4000" exist TWICE, under two
    different ids, one per company. The applied lines must point at the
    RECEIVER's own rows, never the decoy's.

    MUTATION M-C2: drop `AND company_id=?` from the per-line account
    lookup (`SELECT id FROM gl_accounts WHERE code=?`) -- turns this RED,
    because SQLite returns the FIRST matching row, which is the decoy's
    (seeded first, therefore the lower id)."""
    conn = install_recv()
    try:
        _seed_decoy_company_chart(conn)
        ensure_default_chart(conn, RECEIVER_COMPANY_ID)
        conn.commit()

        decoy_cash_id = conn.execute(
            "SELECT id FROM gl_accounts WHERE company_id=? AND code='1000'", (DECOY_COMPANY_ID,)
        ).fetchone()["id"]
        receiver_cash_id = conn.execute(
            "SELECT id FROM gl_accounts WHERE company_id=? AND code='1000'", (RECEIVER_COMPANY_ID,)
        ).fetchone()["id"]
        assert decoy_cash_id != receiver_cash_id, (
            "test fixture bug: decoy and receiver share the same account id for code 1000 "
            "-- this test cannot distinguish a correct lookup from a company-blind one"
        )

        svc = _new_service(install_recv)
        ev = _base_payload()
        assert svc._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()

        row = _gl_journal_row(conn, ev["payload"]["uid"])
        lines = _gl_journal_lines_rows(conn, row["id"])
        assert len(lines) == 2
        cash_line = next(l for l in lines if l["debit"] == pytest.approx(4.007))
        assert cash_line["account_id"] == receiver_cash_id, (
            f"the Cash line points at account_id={cash_line['account_id']!r}, which is "
            f"the DECOY company's row ({decoy_cash_id!r}), not the receiver's own "
            f"({receiver_cash_id!r})"
        )
        assert cash_line["account_id"] != decoy_cash_id
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# test_replaying_the_same_event_posts_once  (M-C3)
# ---------------------------------------------------------------------------

def test_replaying_the_same_event_posts_once(install_recv):
    """A `create` event for a `uid` that already landed -- a retried
    cursor page, a FileRelay echo, or a hub-forwarded duplicate -- must
    leave EXACTLY one `gl_journals` row and its ORIGINAL lines untouched,
    never a second header and never doubled lines.

    MUTATION M-C3: remove the `if cur.rowcount:` gate around the lines
    INSERT (always insert lines) -- turns this RED: a replay would insert
    a SECOND set of `gl_journal_lines` under the SAME `journal_id`
    `ON CONFLICT` skipped re-creating, doubling every debit and credit
    `trial_balance` would then read back."""
    conn = install_recv()
    try:
        ensure_default_chart(conn, RECEIVER_COMPANY_ID)
        conn.commit()
        svc = _new_service(install_recv)
        ev = _base_payload()

        assert svc._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
        # A DIFFERENT event dict, but the SAME uid -- exactly how a
        # retried/re-pulled page or a relay echo would arrive, never the
        # literal same Python object.
        ev_replay = _base_payload(uid=ev["payload"]["uid"])
        ev_replay["entity_id"] = ev["entity_id"]
        assert svc._apply_event(conn, ev_replay, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()

        journals = conn.execute(
            "SELECT id FROM gl_journals WHERE uid=?", (ev["payload"]["uid"],)
        ).fetchall()
        assert len(journals) == 1, f"replay created {len(journals)} gl_journals rows, expected 1"
        lines = _gl_journal_lines_rows(conn, journals[0]["id"])
        assert len(lines) == 2, f"replay left {len(lines)} gl_journal_lines rows, expected 2 (not doubled)"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# test_null_branch_uid_stays_null  (M-C4)
# ---------------------------------------------------------------------------

def test_null_branch_uid_stays_null(install_recv):
    """A company-wide journal (`branch_uid=None`) must land with
    `branch_id IS NULL` -- NEVER pinned to this device's own default/
    self-healed branch. A branch exists on this device (self-healed by an
    earlier operation, or created for an unrelated reason) precisely so
    the fallback has somewhere wrong to land if this guard is broken.

    MUTATION M-C4: call `_resolve_branch_id` unconditionally (the way
    `sale`/`inventory_movement` do) instead of only when `branch_uid` is
    truthy -- turns this RED: `_resolve_branch_id`'s own tier-2 fallback
    self-heals/returns a real (non-NULL) branch id even when passed
    `branch_uid=None`."""
    conn = install_recv()
    try:
        ensure_default_chart(conn, RECEIVER_COMPANY_ID)
        conn.execute(
            "INSERT INTO branches (company_id,name,address,phone,uid) VALUES (?,?,?,?,?)",
            (RECEIVER_COMPANY_ID, 'Main', '', '', str(uuid.uuid4())))
        conn.commit()

        svc = _new_service(install_recv)
        ev = _base_payload(branch_uid=None)
        assert svc._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()

        row = _gl_journal_row(conn, ev["payload"]["uid"])
        assert row["branch_id"] is None, (
            f"a null branch_uid was resolved to a local branch id anyway: {row['branch_id']!r}"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# test_business_date_and_posted_at_travel_verbatim  (M-C5)
# ---------------------------------------------------------------------------

def test_business_date_and_posted_at_travel_verbatim(install_recv):
    """A BACKDATED journal's `business_date` and `posted_at` must land
    EXACTLY as the payload carries them -- never today's date, never
    SQLite's own `CURRENT_TIMESTAMP` default -- so `trial_balance`/
    `income_statement` periods agree between the originating device and
    this one (plan section 2.2, "Reports" row).

    MUTATION M-C5: omit `business_date`/`posted_at` from the INSERT's
    column list (falling through to the column defaults) -- turns this
    RED, because `posted_at` would then read close to "now" instead of
    the historical value the payload carried."""
    conn = install_recv()
    try:
        ensure_default_chart(conn, RECEIVER_COMPANY_ID)
        conn.commit()
        svc = _new_service(install_recv)
        ev = _base_payload(
            business_date="2020-01-15", reference="S-BACKDATED",
            posted_at="2020-01-15 08:30:00",
        )
        assert svc._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()

        row = _gl_journal_row(conn, ev["payload"]["uid"])
        assert row["business_date"] == "2020-01-15"
        assert row["posted_at"] == "2020-01-15 08:30:00", (
            f"posted_at was not carried verbatim: {row['posted_at']!r}"
        )
        assert row["reference"] == "S-BACKDATED"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# test_unbalanced_payload_is_quarantined_and_writes_nothing  (M-C6)
# ---------------------------------------------------------------------------

def test_unbalanced_payload_is_quarantined_and_writes_nothing(install_recv):
    """Lines off by a whole fil (one JOD minor unit, well past half a fil)
    must be PARKED, and must write NEITHER a `gl_journals` header NOR any
    `gl_journal_lines` -- "quarantined and writes nothing" is a single
    claim, proved by checking BOTH tables, not inferring the second from
    the first.

    MUTATION M-C6: drop the `abs(total_debit - total_credit) > eps` check
    from `_validate_gl_journal_lines` -- turns this RED: the unbalanced
    journal would be accepted and WRITTEN."""
    conn = install_recv()
    try:
        ensure_default_chart(conn, RECEIVER_COMPANY_ID)
        conn.commit()
        svc = _new_service(install_recv)
        ev = _base_payload(lines=[
            {"account_code": "1000", "debit": 4.008, "credit": 0.0, "memo": None},
            {"account_code": "4000", "debit": 0.0, "credit": 4.007, "memo": None},
        ])
        result = svc._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()

        assert result is False, "an unbalanced gl_journal was reported as fully applied"
        assert _gl_journal_row(conn, ev["payload"]["uid"]) is None, (
            "an unbalanced journal wrote a gl_journals header row anyway"
        )
        assert conn.execute("SELECT COUNT(*) AS n FROM gl_journal_lines").fetchone()["n"] == 0, (
            "an unbalanced journal wrote gl_journal_lines rows anyway"
        )
        q = _quarantine_rows(conn)
        assert len(q) == 1
        assert q[0]["reason"] == "gl_unbalanced"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# test_balance_check_agrees_with_post_journal_on_the_same_lines
# ---------------------------------------------------------------------------

def test_balance_check_agrees_with_post_journal_on_the_same_lines():
    """Parity proof: `SyncService._validate_gl_journal_lines` (this file's
    own scope) and `general_ledger.validated_lines` (Builder A's own pure
    function, extracted from `post_journal` FOR this exact proof -- see
    its own docstring, which names this test by name) must agree on
    every input, bucketed the same way onto the same two failure MODES --
    a `ValueError`-shaped input maps to `gl_invalid`, a
    `GeneralLedgerImbalanceError`-shaped input maps to `gl_unbalanced`.

    JOD half-fil edges: JOD's quantum is 0.001 (a fil) and the epsilon is
    HALF of it (0.0005). Because both functions quantize each line's own
    debit/credit to the quantum BEFORE summing, no combination of
    per-line amounts can ever leave a total disagreement strictly between
    0 and one full quantum -- so the smallest possible real disagreement
    (a single fil, 0.001) is already a FULL quantum past the half-quantum
    tolerance. This proves the epsilon can never accidentally forgive a
    genuine one-fil posting error, on EITHER side, not merely that the
    two agree on already-trivial cases."""
    from commercial_runtime.sync.sync_service import SyncService as SS
    from core.retail import general_ledger as gl

    cases = [
        ("balanced_3dp", [
            {"account_code": "1000", "debit": 4.007, "credit": 0.0},
            {"account_code": "4000", "debit": 0.0, "credit": 4.007},
        ]),
        ("off_by_one_fil", [
            {"account_code": "1000", "debit": 4.008, "credit": 0.0},
            {"account_code": "4000", "debit": 0.0, "credit": 4.007},
        ]),
        ("sub_quantum_only", [  # F4 (BOOKS-WAVE1-PLAN.md; v2 finding 5): round-then-drop
            {"account_code": "1000", "debit": 0.0004, "credit": 0.0},
            {"account_code": "4000", "debit": 0.0, "credit": 0.0004},
        ]),
        ("negative_debit", [
            {"account_code": "1000", "debit": -1.0, "credit": 0.0},
            {"account_code": "4000", "debit": 0.0, "credit": 1.0},
        ]),
        ("both_sides_nonzero", [
            {"account_code": "1000", "debit": 1.0, "credit": 1.0},
        ]),
        ("all_zero", [
            {"account_code": "1000", "debit": 0.0, "credit": 0.0},
        ]),
        ("many_lines_balanced", [
            {"account_code": "1000", "debit": 1.001, "credit": 0.0},
            {"account_code": "1000", "debit": 2.002, "credit": 0.0},
            {"account_code": "4000", "debit": 0.0, "credit": 3.003},
        ]),
    ]
    currency = "JOD"
    for label, lines in cases:
        their_verdict = None  # None == accepted, else the exception type name
        try:
            gl.validated_lines([dict(l) for l in lines], currency)
        except ValueError:
            their_verdict = "ValueError"
        except gl.GeneralLedgerImbalanceError:
            their_verdict = "GeneralLedgerImbalanceError"

        _, our_reason = SS._validate_gl_journal_lines([dict(l) for l in lines], currency)

        if their_verdict is None:
            assert our_reason is None, f"{label}: validated_lines accepted, we rejected ({our_reason!r})"
        elif their_verdict == "ValueError":
            assert our_reason == "gl_invalid", (
                f"{label}: validated_lines raised ValueError, we bucketed it {our_reason!r}, "
                f"expected 'gl_invalid'"
            )
        else:
            assert our_reason == "gl_unbalanced", (
                f"{label}: validated_lines raised GeneralLedgerImbalanceError, we bucketed it "
                f"{our_reason!r}, expected 'gl_unbalanced'"
            )


# ---------------------------------------------------------------------------
# test_unknown_account_without_metadata_is_quarantined
# ---------------------------------------------------------------------------

def test_unknown_account_without_metadata_is_quarantined(install_recv):
    """A line names an account code this device has never seen, and the
    payload's own `accounts` metadata says nothing about it either
    (missing/malformed relay payload) -- there is no safe account to file
    it under, so it must be parked, never guessed."""
    conn = install_recv()
    try:
        svc = _new_service(install_recv)
        ev = _base_payload(lines=[
            {"account_code": "9999", "debit": 1.0, "credit": 0.0},
            {"account_code": "4000", "debit": 0.0, "credit": 1.0},
        ], accounts=[])
        result = svc._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()

        assert result is False
        assert _gl_journal_row(conn, ev["payload"]["uid"]) is None
        q = _quarantine_rows(conn)
        assert len(q) == 1
        assert q[0]["reason"] == "gl_unknown_account"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# test_missing_account_seeded_from_metadata_parents_first
# ---------------------------------------------------------------------------

def test_missing_account_seeded_from_metadata_parents_first(install_recv):
    """A journal references a brand-new PARENT and CHILD account, neither
    of which exists on this device yet, described in the payload's
    `accounts` metadata PARENT FIRST (the real order `general_ledger.
    _account_metadata` builds it in). Both must be seeded, and the
    child's `parent_id` must point at the newly-seeded parent's id, not
    NULL.

    Mutation-proved: seeding with `parent_id=None` unconditionally
    (ignoring `parent_code`) turns the parent_id assertion below RED."""
    conn = install_recv()
    try:
        svc = _new_service(install_recv)
        ev = _base_payload(
            lines=[
                {"account_code": "4900", "debit": 1.0, "credit": 0.0},
                {"account_code": "1000", "debit": 0.0, "credit": 1.0},
            ],
            accounts=[
                {"code": "4000", "name": "Sales Revenue", "account_type": "revenue",
                 "normal_balance": "credit", "parent_code": None},
                {"code": "4900", "name": "Sales Returns and Allowances", "account_type": "revenue",
                 "normal_balance": "debit", "parent_code": "4000"},
                {"code": "1000", "name": "Cash and Bank", "account_type": "asset",
                 "normal_balance": "debit", "parent_code": None},
            ],
        )
        assert conn.execute(
            "SELECT 1 FROM gl_accounts WHERE company_id=? AND code IN ('4000','4900')",
            (RECEIVER_COMPANY_ID,)
        ).fetchone() is None, "test fixture bug: accounts already existed before the apply"

        result = svc._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
        assert result is True

        parent = conn.execute(
            "SELECT id FROM gl_accounts WHERE company_id=? AND code='4000'", (RECEIVER_COMPANY_ID,)
        ).fetchone()
        child = conn.execute(
            "SELECT parent_id FROM gl_accounts WHERE company_id=? AND code='4900'", (RECEIVER_COMPANY_ID,)
        ).fetchone()
        assert parent is not None, "the parent account (4000) was never seeded"
        assert child is not None, "the child account (4900) was never seeded"
        assert child["parent_id"] == parent["id"], (
            f"4900's parent_id={child['parent_id']!r} does not point at the newly-seeded "
            f"4000 row (id={parent['id']!r}) -- parents-first seeding did not wire the FK"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# test_reversal_before_original_parks_then_applies_once
# ---------------------------------------------------------------------------

def test_reversal_before_original_parks_then_applies_once(install_recv):
    """A reversal arrives BEFORE the journal it reverses (plan section
    2.2, "Reversals" row / R3 ordering) -- it must park under
    `missing_parent:gl_journal`, apply exactly once the retry AFTER the
    original arrives, resolve `reversed_journal_id` to the ORIGINAL's
    LOCAL id, and never double-apply on a further replay."""
    conn = install_recv()
    try:
        ensure_default_chart(conn, RECEIVER_COMPANY_ID)
        conn.commit()
        svc = _new_service(install_recv)

        original_uid = str(uuid.uuid4())
        original_ev = _base_payload(uid=original_uid, source_type="sale")

        reversal_uid = str(uuid.uuid4())
        reversal_ev = _base_payload(
            uid=reversal_uid, source_type="reversal", source_uid=None,
            reversed_journal_uid=original_uid,
            lines=[
                {"account_code": "1000", "debit": 0.0, "credit": 4.007, "memo": "Reversal"},
                {"account_code": "4000", "debit": 4.007, "credit": 0.0, "memo": "Reversal"},
            ],
        )

        # Reversal arrives FIRST -- its parent has not landed yet.
        result = svc._apply_event(conn, reversal_ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
        assert result is False, "a reversal with an unresolved parent was reported as applied"
        assert _gl_journal_row(conn, reversal_uid) is None
        q = _quarantine_rows(conn)
        assert len(q) == 1
        assert q[0]["reason"] == "missing_parent:gl_journal"

        # The original arrives.
        assert svc._apply_event(conn, original_ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
        original_row = _gl_journal_row(conn, original_uid)
        assert original_row is not None

        # Retry the quarantine -- the reversal should now resolve.
        svc._retry_quarantined_events(conn, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
        assert _quarantine_rows(conn) == [], "the reversal is still parked after its parent arrived"
        reversal_row = _gl_journal_row(conn, reversal_uid)
        assert reversal_row is not None, "the reversal never applied after retry"
        assert reversal_row["reversed_journal_id"] == original_row["id"], (
            f"reversed_journal_id={reversal_row['reversed_journal_id']!r} does not point at the "
            f"original's local id ({original_row['id']!r})"
        )
        assert conn.execute("SELECT COUNT(*) AS n FROM gl_journals").fetchone()["n"] == 2

        # A further replay of the ALREADY-APPLIED reversal must not double it.
        replay_ev = dict(reversal_ev)
        replay_ev["payload"] = dict(reversal_ev["payload"])
        assert svc._apply_event(conn, replay_ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) AS n FROM gl_journals").fetchone()["n"] == 2, (
            "replaying the already-applied reversal created a duplicate"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# test_source_id_is_receivers_local_sale_id_or_null  (M-C7)
# ---------------------------------------------------------------------------

def test_source_id_is_receivers_local_sale_id_or_null(install_recv):
    """`source_id` must be the RECEIVER's own local `sales.id` for the
    matching `source_uid`, resolved fresh -- never a value lifted straight
    off the payload (which carries no such field at all per the contract,
    but a hostile/legacy payload might smuggle one in anyway), and never
    parked when the sale has not arrived yet -- NULL instead (plan
    section 2.2's "source_id" row: only the cost-basis flag, W1b, cares
    about that case).

    MUTATION M-C7: read `p.get("source_id")` when present instead of
    always resolving via `_local_id_by_uid` -- turns this RED against the
    hostile-integer case below."""
    conn = install_recv()
    try:
        ensure_default_chart(conn, RECEIVER_COMPANY_ID)
        sale_uid = str(uuid.uuid4())
        # A sale that arrived on this device via its OWN sync apply --
        # local id is whatever THIS device's autoincrement assigns, which
        # must never be confused with any integer the sender might send.
        conn.execute(
            "INSERT INTO sales (company_id, sale_number, uid) VALUES (?,?,?)",
            (RECEIVER_COMPANY_ID, f"S-{sale_uid[:8]}", sale_uid),
        )
        conn.commit()
        real_local_sale_id = conn.execute(
            "SELECT id FROM sales WHERE uid=?", (sale_uid,)
        ).fetchone()["id"]

        svc = _new_service(install_recv)

        # Case 1: the sale HAS arrived -- source_id resolves to the
        # receiver's own local id, ignoring a hostile "source_id" the
        # payload has no business carrying at all (the contract excludes
        # it entirely).
        ev_resolved = _base_payload(source_type="sale", source_uid=sale_uid)
        ev_resolved["payload"]["source_id"] = 99999999
        assert svc._apply_event(conn, ev_resolved, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
        row = _gl_journal_row(conn, ev_resolved["payload"]["uid"])
        assert row["source_id"] == real_local_sale_id, (
            f"source_id={row['source_id']!r} does not match the receiver's own local sale id "
            f"({real_local_sale_id!r}) -- a hostile/foreign integer leaked through instead"
        )
        assert row["source_id"] != 99999999

        # Case 2: the sale has NOT arrived yet -- source_id is NULL, and
        # the journal is NOT parked for it (only reversed_journal_uid
        # parks; a missing sale source never does, per plan section 2.2).
        never_arrived_uid = str(uuid.uuid4())
        ev_unresolved = _base_payload(source_type="sale", source_uid=never_arrived_uid)
        assert svc._apply_event(conn, ev_unresolved, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
        row2 = _gl_journal_row(conn, ev_unresolved["payload"]["uid"])
        assert row2 is not None, "a journal whose sale has not arrived yet was parked instead of applied"
        assert row2["source_id"] is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# test_applying_a_pulled_sale_posts_no_journal_locally
# ---------------------------------------------------------------------------

def test_applying_a_pulled_sale_posts_no_journal_locally(install_recv):
    """Architectural invariant this whole wave depends on (plan section
    2.1: "receiving a sale, return or payment never posts a journal"):
    applying a pulled `sale` create event through the PRE-EXISTING `sale`
    branch must not touch `gl_journals` at all. Only the ORIGINATING
    device posts; every peer only ever inserts the journal it receives
    through the `gl_journal` branch itself."""
    conn = install_recv()
    try:
        before = conn.execute("SELECT COUNT(*) AS n FROM gl_journals").fetchone()["n"]
        svc = _new_service(install_recv)
        sale_ev = {
            "entity_type": "sale", "event_type": "create",
            "payload": {
                "uid": str(uuid.uuid4()), "sale_number": "S-NOJOURNAL", "branch_uid": None,
                "customer_id": None, "cashier": "POS", "subtotal": 10, "discount_amount": 0,
                "tax_amount": 0, "total": 10, "amount_paid": 10, "change_amount": 0,
                "points_redeemed_amount": 0, "payment_method": "cash", "status": "completed",
                "notes": "", "created_at": "2026-09-24 09:00:00", "due_date": None,
                "actor_user_uid": None, "terminal_id": None, "created_at_utc": None,
            },
        }
        assert svc._apply_event(conn, sale_ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()

        after = conn.execute("SELECT COUNT(*) AS n FROM gl_journals").fetchone()["n"]
        assert after == before, (
            f"applying a pulled sale changed gl_journals row count from {before} to {after} -- "
            f"the receiving device must never post a journal of its own"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# F8 (BOOKS-WAVE1-PLAN.md; v1 gap 1, gap 3): a malformed/hostile gl_journal
# payload is PARKED as gl_invalid, never raised out of _apply_event.
# ---------------------------------------------------------------------------

def test_missing_uid_is_quarantined_as_gl_invalid(install_recv):
    """v1 gap 3: the missing-uid -> gl_invalid branch had no test at all."""
    conn = install_recv()
    try:
        ensure_default_chart(conn, RECEIVER_COMPANY_ID)
        conn.commit()
        svc = _new_service(install_recv)
        ev = _base_payload(uid=None)
        result = svc._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()

        assert result is False
        assert conn.execute("SELECT COUNT(*) AS n FROM gl_journals").fetchone()["n"] == 0
        q = _quarantine_rows(conn)
        assert len(q) == 1
        assert q[0]["reason"] == "gl_invalid"
    finally:
        conn.close()


@pytest.mark.parametrize("missing_field", ["business_date", "source_type"])
def test_missing_required_column_is_quarantined_not_raised(install_recv, missing_field):
    """F8 (v1 gap 1): `business_date` and `source_type` are NOT NULL columns
    on `gl_journals` (database/schema.py) -- a payload missing either used
    to raise `sqlite3.IntegrityError` straight out of `_apply_event`,
    escaping it entirely and wedging the whole pull batch, rather than
    parking one bad payload the same way a missing `uid` already does.

    MUTATION: removing this check turns this RED with an unhandled
    `sqlite3.IntegrityError` instead of a clean `False`/`gl_invalid`."""
    conn = install_recv()
    try:
        ensure_default_chart(conn, RECEIVER_COMPANY_ID)
        conn.commit()
        svc = _new_service(install_recv)
        ev = _base_payload(**{missing_field: None})
        result = svc._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()

        assert result is False, f"a payload missing {missing_field} must be parked, not applied"
        assert conn.execute("SELECT COUNT(*) AS n FROM gl_journals").fetchone()["n"] == 0
        q = _quarantine_rows(conn)
        assert len(q) == 1
        assert q[0]["reason"] == "gl_invalid"
    finally:
        conn.close()


@pytest.mark.parametrize("hostile_value", [float("nan"), float("inf"), float("-inf"), "1E+9999"])
def test_non_finite_or_absurdly_large_amount_is_quarantined_not_raised(install_recv, hostile_value):
    """F8 (v1 gap 1): a NaN or +/-Infinity `Decimal` parses fine but raises
    `decimal.InvalidOperation` the moment it is COMPARED (`d < 0`) -- OUTSIDE
    the `try` that only wrapped the parse -- and an "absurdly large" but
    finite amount (e.g. 1E+9999) can still overflow `Decimal.quantize`'s own
    precision. Either used to escape `_apply_event` entirely and wedge the
    whole pull batch; both must now be parked as gl_invalid.

    MUTATION: removing the `is_finite()` check, or un-wrapping the
    `quantize` call's own try/except, turns this RED with an unhandled
    `decimal.InvalidOperation` instead of a clean `False`/`gl_invalid`."""
    conn = install_recv()
    try:
        ensure_default_chart(conn, RECEIVER_COMPANY_ID)
        conn.commit()
        svc = _new_service(install_recv)
        ev = _base_payload(lines=[
            {"account_code": "1000", "debit": hostile_value, "credit": 0.0, "memo": None},
            {"account_code": "4000", "debit": 0.0, "credit": 4.007, "memo": None},
        ])
        result = svc._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()

        assert result is False
        assert conn.execute("SELECT COUNT(*) AS n FROM gl_journals").fetchone()["n"] == 0
        q = _quarantine_rows(conn)
        assert len(q) == 1
        assert q[0]["reason"] == "gl_invalid"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# F8, the rest of it (third wave verifier, 2026-09-26): a payload VALUE of
# the wrong TYPE -- not only a missing or non-finite one -- must be parked.
# ---------------------------------------------------------------------------

def _malformed_cases():
    """Each case: (label, payload overrides, line-0 overrides). Every value
    here is a shape the emitter can never produce (`journal_sync_payload`
    reads TEXT columns and always sends str/None, lists and dicts only where
    the contract has them). Before the fix, the dict/list cases raised
    `sqlite3.ProgrammingError` ("Error binding parameter ... type 'dict' is
    not supported") or `TypeError` straight out of `_apply_event` -- the
    whole pull page rolled back and the cursor never moved, on every tick
    (the AUDIT-032B wedge F8 exists to close) -- and the bool/float cases
    were written into the books as '1' / '3.5'."""
    cases = []
    for field in ("uid", "business_date", "reference", "memo", "source_type", "source_uid",
                  "branch_uid", "currency", "reversed_journal_uid", "posted_by", "posted_at"):
        cases.append((f"{field}=dict", {field: {"x": 1}}, {}))
    cases += [
        ("uid=list", {"uid": ["x"]}, {}),
        ("business_date=True", {"business_date": True}, {}),
        ("posted_at=3.5", {"posted_at": 3.5}, {}),
        ("line.account_code=dict", {}, {"account_code": {"x": 1}}),
        ("line.account_code=list", {}, {"account_code": ["1000"]}),
        ("line.account_code=int", {}, {"account_code": 1000}),
        ("line.memo=dict", {}, {"memo": {"x": 1}}),
        ("line.debit=non-numeric", {}, {"debit": "abc"}),
        ("accounts=int", {"accounts": 5}, {}),
        ("accounts=str", {"accounts": "1000"}, {}),
        ("accounts[0].code=dict", {"accounts": [{"code": {"x": 1}, "name": "n",
                                                 "account_type": "asset", "normal_balance": "debit",
                                                 "parent_code": None}]}, {}),
        ("accounts[0].name=dict", {"accounts": [{"code": "9999", "name": {"x": 1},
                                                 "account_type": "asset", "normal_balance": "debit",
                                                 "parent_code": None}]}, {}),
        ("accounts[0].parent_code=list", {"accounts": [{"code": "9999", "name": "n",
                                                        "account_type": "asset", "normal_balance": "debit",
                                                        "parent_code": ["1000"]}]}, {}),
    ]
    return cases


@pytest.mark.parametrize("label,overrides,line_overrides", _malformed_cases(),
                         ids=[c[0] for c in _malformed_cases()])
def test_wrongly_typed_payload_values_are_parked_not_raised(install_recv, label, overrides,
                                                              line_overrides):
    """Through the real `apply_pull_result`, the path a pull tick takes: no
    exception escapes, the cursor advances, the event is parked `gl_invalid`,
    and NOTHING is written -- no journal, no line, no seeded account. RED
    before the fix for every dict/list case (ProgrammingError / TypeError)
    and for the bool/float/int cases (applied, not parked)."""
    conn = install_recv()
    try:
        ensure_default_chart(conn, RECEIVER_COMPANY_ID)
        conn.commit()
        accounts_before = conn.execute("SELECT COUNT(*) AS n FROM gl_accounts").fetchone()["n"]
        ev = _base_payload(**overrides)
        ev["entity_id"] = str(uuid.uuid4())  # the relay envelope's own id is always text
        ev["payload"]["lines"] = [dict(ev["payload"]["lines"][0], **line_overrides),
                                  ev["payload"]["lines"][1]]
        svc = _new_service(install_recv)

        svc.apply_pull_result(conn, {"events": [ev], "cursor": 7})  # must not raise
        conn.commit()

        assert conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()[0] == 7
        assert conn.execute("SELECT COUNT(*) AS n FROM gl_journals").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM gl_journal_lines").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM gl_accounts").fetchone()["n"] == accounts_before
        q = _quarantine_rows(conn)
        assert [r["reason"] for r in q] == ["gl_invalid"], (
            f"{label}: expected one gl_invalid park, got {[dict(r) for r in q]}"
        )
    finally:
        conn.close()
