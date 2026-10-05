"""Aura Retail -- Wave 1 (BOOKS-WAVE1-PLAN.md, schema v41): "one set of books
across devices". Builder D's own test file (plan section 7: "D: the additive
ops in products/retail/tests/_stock_sync_device.py and the new two-device
test").

WHAT THIS PROVES, IN ONE SENTENCE: a journal posted on ONE device, through the
REAL emit (`general_ledger.post_journal`) -> relay (`_stock_sync_harness.
FileRelay`, a genuine cross-process transport double) -> apply
(`sync_service.SyncService._apply_event`'s `gl_journal` branch) path, lands on
every OTHER device such that `trial_balance`, `profit-and-loss` and the raw
`gl_journals.uid` set all read IDENTICAL -- never merely "the totals look
close".

WHY THIS IS A SEPARATE FILE FROM retail_gl_emit_test.py / retail_gl_journal_
apply_test.py / retail_v41_gl_sync_migration_test.py. Those three (Builders
A/C/B) each prove their OWN half in isolation -- a hand-built payload dict fed
straight to the apply function, or a single `post_journal` call inspected in
place. None of them proves the three halves actually FIT TOGETHER through a
real HTTP sale, a real file-backed relay and a real second OS process, which
is exactly the shape BOOKS-WAVE1-PLAN.md section 6's own numbered traps (1-8)
warn is how a wave ships broken while every one of its own unit suites stays
green (see CLAUDE.md's "Verification failure patterns" for the general
version of this lesson, and retail_stock_sync_test.py's own module docstring,
"THE HARNESS, AND WHY retail_money_sync_test.py's `install_b` IS NOT REUSED",
for the Wave-B precedent this file follows). Every test below rings its sale/
return/payment through the REAL Flask route, in a REAL second OS process, over
the REAL FileRelay -- never a hand-built event dict, never a bare-database
receiver that never writes anything of its own.

EVERY ONE OF SECTION 6'S NAMED TRAPS, AND HOW THIS FILE AVOIDS IT:
  1. `install_b` never rings anything -> every test here uses `run_device()`,
     the multi-process harness `retail_stock_sync_test.py` itself uses, never
     the bare-database `install_b` fixture.
  2. Seeding `gl_accounts`/calling `ensure_default_chart`/pre-posting on the
     receiver -> no test below ever does; every account a receiver needs
     arrives from the payload's own `accounts` metadata, exactly like
     production.
  3. Same `company_id` on both devices -> every test uses TWO independently
     minted company ids (`create_shop`'s own UUID and `bootstrap`'s own),
     never sharing one.
  4. A relay that filters a device's own events -> `FileRelay.pull()` returns
     EVERY event with `seq > since`, unconditionally; `test_origin_does_not_
     double_post_its_own_echo` below depends on exactly this, deliberately.
  5. Manufacturing "legacy" journals with the NEW code -> `insert_legacy_
     journal_raw` (a raw `INSERT INTO gl_journals` naming neither `uid` nor
     `source_uid`) is the only way this file ever creates a pre-v41-shaped
     row; `post_journal` always mints a uid and can never produce one.
  6. Only round figures, only today's date -> the headline test prices its
     product at a genuine 3dp JOD figure (`4.007`); `test_backdated_journal_
     lands_in_same_period_on_both` posts a journal dated years in the past.
  7. A single push/pull -> `test_cursor_reset_and_relay_switch_replay_posts_
     nothing_twice` performs a cursor reset AND a simulated relay switch, each
     followed by a full re-pull; `test_quarantine_retry_applies_each_journal_
     once` performs an explicit quarantine-then-heal-then-replay sequence.
  8. Comparing totals only -> `_assert_ledgers_converge` below compares the
     full per-account `trial_balance` arrays (every account, not just a sum),
     every named P&L line, AND the `gl_census` uid-set fingerprint -- three
     independent facts, any one of which a totals-only comparison could miss.

A CROSS-DEVICE FACT THIS SUITE'S OWN OP VOCABULARY HAS NO DEDICATED OP FOR IS
REACHED BY CONNECTING DIRECTLY TO A DEVICE'S OWN `retail.db` (or the shared
`relay.db`) BETWEEN `run_device()` CALLS, NEVER BY GROWING THAT VOCABULARY
PAST WHAT BOOKS-WAVE1-PLAN.md SECTION 6 ITSELF NAMES. This is not a new
technique: `retail_gl_integration_test.py`'s own `_set_customer_credit`
helper pokes `customers.credit_mode`/`credit_limit` directly over a bare
sqlite3 connection, and `retail_stock_sync_test.py`'s own orphan-resolution
tests hand-insert a `relay_events` row directly against `relay_db` from the
test file itself. `_read_device`/`_write_device` below do the identical thing
against a DEVICE's own db (never while that device's own subprocess is
running -- `run_device()` is synchronous, so the file is always closed by the
time the test's own connection opens it), and `_delay_relay_event`/
`_redeliver_relay_event` do the identical thing against the shared relay,
replaying the device's own REAL wire payload rather than a hand-built
substitute that could silently drift from the real contract.

MUTATION PROOFS THIS FILE OWNS (BOOKS-WAVE1-PLAN.md section 6: "D quotes the
RED of M-A1, M-C3 and M-A4 through the headline/replay/echo tests"). Each is
performed in an ISOLATED COPY of the tree (never the shared worktree -- other
builders' own uncommitted work lives there too), by temporarily editing that
copy's `general_ledger.py` or `sync_service.py`, running ONLY the one test
named, observing RED, then discarding the copy entirely. See this file's own
closing comment block (bottom of file) for the exact commands and the exact
RED text captured for each of the three.

Run:
    pytest products/retail/tests/retail_gl_two_device_sync_test.py -v
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _stock_sync_harness import run_device  # noqa: E402

PY = sys.executable


# ─────────────────────────────────────────────────────────────────────────────
# Direct-db helpers -- see the module docstring's own paragraph on why these
# exist instead of new device ops. Every one of these is a SHORT-LIVED
# connection opened strictly BETWEEN `run_device()` calls (that subprocess has
# already exited by the time any of these run), never concurrent with a live
# device subprocess.
# ─────────────────────────────────────────────────────────────────────────────

def _device_retail_db(app_data_dir: Path) -> Path:
    return Path(app_data_dir) / "database" / "subsystems" / "retail.db"


def _read_device(app_data_dir: Path, sql: str, params: tuple = ()) -> list:
    conn = sqlite3.connect(str(_device_retail_db(app_data_dir)), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _write_device(app_data_dir: Path, sql: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(str(_device_retail_db(app_data_dir)), timeout=30)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _stock_entry_split(app_data_dir: Path, company_id: str) -> tuple:
    """(journals that are stock entries, all other journals) -- STOCK-BOOKS posts
    `opening_entry` / `stock_adjustment` journals when stock is entered, which
    these census tests must count apart from the sale/COGS pair they pin."""
    rows = _read_device(
        app_data_dir,
        "SELECT SUM(CASE WHEN source_type IN ('opening_entry','stock_adjustment') THEN 1 ELSE 0 END) AS s, "
        "SUM(CASE WHEN source_type IN ('opening_entry','stock_adjustment') THEN 0 ELSE 1 END) AS r "
        "FROM gl_journals WHERE company_id=?", (company_id,))
    return int(rows[0]["s"] or 0), int(rows[0]["r"] or 0)


def _local_sale_id_by_uid(app_data_dir: Path, sale_uid: str) -> int:
    """The RECEIVING device's own local `sales.id` for a sale that arrived by
    sync -- `sales.uid` is the one cross-device identity a synced sale row
    carries (see general_ledger.py's `_SOURCE_UID_TABLES`), and no op in this
    suite's vocabulary looks this up (see module docstring)."""
    rows = _read_device(app_data_dir, "SELECT id FROM sales WHERE uid=?", (sale_uid,))
    assert rows, f"no local sale row found for uid={sale_uid!r} -- did it actually sync?"
    return rows[0]["id"]


def _set_local_avg_cost(app_data_dir: Path, product_id, avg_cost: float) -> None:
    """Diverges ONE device's own `products.avg_cost` for an already-synced
    product, independently of any other device's -- the exact scenario `test_
    cogs_is_the_origins_snapshot_not_receivers_avg_cost` needs to exist BEFORE
    the sale it is testing is rung, and no op in this suite's vocabulary
    writes this column directly (see module docstring)."""
    _write_device(app_data_dir, "UPDATE products SET avg_cost=? WHERE id=?", (avg_cost, product_id))


def _relay_conn(relay_db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(relay_db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _delay_relay_event(relay_db_path: Path, uid: str) -> str:
    """Removes the ONE `relay_events` row whose OUTER `entity_id` (the
    `gl_journal` wire identity, per BOOKS-WAVE1-PLAN.md section 2.3:
    "entity_id=uid") equals `uid`, and returns its exact payload TEXT so the
    caller can redeliver the SAME real wire content later via
    `_redeliver_relay_event` -- simulating a message delayed in transit,
    never a hand-built substitute (section 6 trap #5's reasoning applies
    here too: a fabricated payload could silently drift from the real
    contract; replaying the real one the device itself produced cannot)."""
    conn = _relay_conn(relay_db_path)
    try:
        row = conn.execute(
            "SELECT seq, payload FROM relay_events WHERE payload LIKE ?",
            (f'%"entity_id": "{uid}"%',),
        ).fetchone()
        assert row is not None, f"no relay event found carrying entity_id={uid!r}"
        conn.execute("DELETE FROM relay_events WHERE seq=?", (row["seq"],))
        conn.commit()
        return row["payload"]
    finally:
        conn.close()


def _redeliver_relay_event(relay_db_path: Path, payload_text: str) -> None:
    conn = _relay_conn(relay_db_path)
    try:
        conn.execute("INSERT INTO relay_events (payload) VALUES (?)", (payload_text,))
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Convergence assertions -- section 6 trap #8: "comparing totals only". Every
# one of these compares the FULL structure, not a derived sum.
# ─────────────────────────────────────────────────────────────────────────────

def _trial_balance(app_dir: Path, relay_db: Path, email: str, password: str) -> dict:
    # `trial_balance` is a real HTTP route (`@mt_login_required`) -- and every
    # `run_device()` call is a FRESH subprocess with its OWN, unauthenticated
    # `flask_app.test_client()` (see _stock_sync_device.py's own module
    # docstring: "HTTP-route ops ... need a logged-in client -- login must be
    # the first action"). `login` MUST be the first action of THIS SAME call,
    # never assumed to carry over from an earlier `run_device()` call.
    _, r = run_device(PY, app_dir, relay_db, [
        {"op": "login", "email": email, "password": password},
        {"op": "trial_balance"},
    ])
    assert r["status_code"] == 200, r["body"]
    return r["body"]["data"]


def _pnl(app_dir: Path, relay_db: Path, start_date: str, end_date: str, email: str, password: str) -> dict:
    _, r = run_device(PY, app_dir, relay_db, [
        {"op": "login", "email": email, "password": password},
        {"op": "pnl", "start_date": start_date, "end_date": end_date},
    ])
    assert r["status_code"] == 200, r["body"]
    return r["body"]["data"]


def _gl_census(app_dir: Path, relay_db: Path, company_id: str) -> dict:
    # Direct-SQL, non-HTTP op -- no login needed (see _stock_sync_device.py's
    # own "Non-HTTP ops" section).
    [r] = run_device(PY, app_dir, relay_db, [{"op": "gl_census", "company_id": company_id}])
    return r


def _assert_ledgers_converge(app_a, app_b, relay_db, cid_a, cid_b, start_date, end_date,
                              email_a, password_a, email_b, password_b):
    """THE decisive, three-way convergence check (section 6 trap #8). Every
    account in the trial balance (not the totals alone), every named P&L
    line, and the raw uid-set fingerprint must all agree -- any one of the
    three could stay silent about a defect the other two would catch (a
    duplicated but perfectly-balanced pair of lines moves no trial-balance
    total but DOES move the P&L's revenue account; a re-ordered but
    otherwise-identical uid set would pass a totals check but fail the
    census). Returns (tb_a, tb_b, pnl_a, pnl_b) for a caller that wants to
    assert something MORE specific afterwards.
    """
    tb_a = _trial_balance(app_a, relay_db, email_a, password_a)
    tb_b = _trial_balance(app_b, relay_db, email_b, password_b)
    # `subledger` is device-local by design (R6 -- `credit_balance` never
    # syncs, BOOKS-WAVE1-PLAN.md section 3's credit_ledger row) and is NOT
    # part of the ledger-parity claim this helper makes; every OTHER key
    # (`currency`, `accounts`, `total_debit`, `total_credit`, `is_balanced`)
    # must match exactly.
    tb_a_ledger = {k: v for k, v in tb_a.items() if k != 'subledger'}
    tb_b_ledger = {k: v for k, v in tb_b.items() if k != 'subledger'}
    assert tb_a_ledger == tb_b_ledger, (
        f"trial balances diverge:\nA={tb_a_ledger}\nB={tb_b_ledger}"
    )

    pnl_a = _pnl(app_a, relay_db, start_date, end_date, email_a, password_a)
    pnl_b = _pnl(app_b, relay_db, start_date, end_date, email_b, password_b)
    # `period` echoes the request back (identical on both by construction);
    # every SUBSTANTIVE P&L line must still match independently.
    for key in ('currency', 'revenue', 'gross_revenue', 'sales_returns',
                'cost_of_goods_sold', 'gross_profit', 'gross_margin',
                'operating_expenses', 'net_profit', 'has_activity'):
        assert pnl_a[key] == pnl_b[key], (
            f"P&L line {key!r} diverges: A={pnl_a[key]!r} B={pnl_b[key]!r}"
        )

    census_a = _gl_census(app_a, relay_db, cid_a)
    census_b = _gl_census(app_b, relay_db, cid_b)
    assert census_a['null_uids'] == 0, f"A has {census_a['null_uids']} un-backfilled journal(s)"
    assert census_b['null_uids'] == 0, f"B has {census_b['null_uids']} un-backfilled journal(s)"
    assert census_a['count'] == census_a['distinct_uids'], \
        f"A has a duplicate uid: {census_a}"
    assert census_b['count'] == census_b['distinct_uids'], \
        f"B has a duplicate uid: {census_b}"
    assert census_a['uid_hash'] == census_b['uid_hash'], (
        f"the two devices hold DIFFERENT sets of journal uids -- "
        f"A={census_a} B={census_b}"
    )
    return tb_a, tb_b, pnl_a, pnl_b


# ─────────────────────────────────────────────────────────────────────────────
# 1. THE HEADLINE TEST
# ─────────────────────────────────────────────────────────────────────────────

def test_sale_rung_on_b_shows_identical_pnl_and_trial_balance_on_a(tmp_path):
    """BOOKS-WAVE1-PLAN.md section 6's own headline test, RED today against
    pre-Wave-1 code (no `gl_journal` sync at all -- B's own AR/revenue/COGS
    posting would simply never reach A). Section 6 trap #6: a genuine 3dp JOD
    price (`4.007`, tax_rate 0 so the sale total is exactly `4.007`), not a
    round figure.

    Setup follows retail_stock_sync_test.py's own convention for a branch
    both devices can agree names the same place: A provisions an OPERATOR
    branch (`create_branch`) -- never `create_shop`'s own self-healed
    default, which deliberately queues no `branch` sync event at all (see
    that op's own docstring) -- stocks the shared product there, and pushes.
    B is a genuinely fresh install (`bootstrap`: an admin and a company, no
    product of its own) that pulls A's catalogue and THEN rings the real
    sale through the real `/sales` route in its own real subprocess.
    """
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_device(PY, device_a, relay_db, [
        {"op": "create_shop", "price": 4.007, "initial_stock": 0},
    ])
    cid_a, pid = shop["company_id"], shop["product_id"]

    _, branch = run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_branch", "name": "GL Sync Branch", "address": "1 Ledger Rd", "phone": "555-0141"},
    ])
    assert branch["status_code"] == 200, branch["body"]
    a_branch_id, a_branch_uid = branch["branch_id"], branch["branch_uid"]

    run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "adjust_stock", "product_id": pid, "quantity": 500,
         "reason": "Opening stock, shared branch", "branch_id": a_branch_id},
        {"op": "push"},
    ])

    [bootstrap] = run_device(PY, device_b, relay_db, [{"op": "bootstrap"}])
    cid_b = bootstrap["company_id"]
    run_device(PY, device_b, relay_db, [{"op": "pull"}])

    [lookup] = run_device(PY, device_b, relay_db, [{"op": "branch_by_uid", "uid": a_branch_uid}])
    b_branch_id = lookup["branch"]["id"]
    assert lookup["branch"]["company_id"] == cid_b  # B's own, never A's

    _, sell_result = run_device(PY, device_b, relay_db, [
        {"op": "login", "email": bootstrap["email"], "password": bootstrap["password"]},
        {"op": "sell", "product_id": pid, "quantity": 3, "branch_id": b_branch_id},
    ])
    assert sell_result["status_code"] == 200, sell_result["body"]
    assert sell_result["body"]["data"]["total"] == pytest.approx(12.021)  # 3 * 4.007
    run_device(PY, device_b, relay_db, [{"op": "push"}])
    run_device(PY, device_a, relay_db, [{"op": "pull"}])

    tb_a, tb_b, pnl_a, pnl_b = _assert_ledgers_converge(
        device_a, device_b, relay_db, cid_a, cid_b, "2000-01-01", "2099-12-31",
        shop["email"], shop["password"], bootstrap["email"], bootstrap["password"],
    )
    assert tb_a['is_balanced'] and tb_b['is_balanced']
    assert pnl_a['has_activity'] is True
    assert pnl_a['revenue'] == pytest.approx(12.021)


# ─────────────────────────────────────────────────────────────────────────────
# 2. RETURN + COGS REVERSAL, FILED ON THE *OTHER* DEVICE FROM THE SALE
# ─────────────────────────────────────────────────────────────────────────────

def test_return_on_a_against_bs_sale_converges_including_cogs_reversal(tmp_path):
    """The sale rings on B; the return against it is filed on A -- a real
    multi-branch/multi-till scenario (a customer buys at one till, returns
    at another) and, per BOOKS-WAVE1-PLAN.md section 2.1's rejected-option
    (b) reasoning, exactly the case a receiver-recomputes design could never
    get right (the return's own COGS reversal needs the ORIGINAL sale line's
    cost snapshot). Under Wave 1's chosen design (a), A processes the return
    itself -- against its own local, synced copy of B's sale -- and POSTS
    its own return + COGS-reversal journals locally, which then sync back to
    B. Both devices must converge afterwards.
    """
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_device(PY, device_a, relay_db, [
        {"op": "create_shop", "price": 20.0, "initial_stock": 0},
    ])
    cid_a, pid = shop["company_id"], shop["product_id"]
    _, branch = run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_branch", "name": "Return Sync Branch", "address": "", "phone": ""},
    ])
    a_branch_id, a_branch_uid = branch["branch_id"], branch["branch_uid"]
    run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "adjust_stock", "product_id": pid, "quantity": 200,
         "reason": "Opening stock", "branch_id": a_branch_id},
        {"op": "push"},
    ])

    [bootstrap] = run_device(PY, device_b, relay_db, [{"op": "bootstrap"}])
    cid_b = bootstrap["company_id"]
    run_device(PY, device_b, relay_db, [{"op": "pull"}])
    [lookup] = run_device(PY, device_b, relay_db, [{"op": "branch_by_uid", "uid": a_branch_uid}])
    b_branch_id = lookup["branch"]["id"]

    _, sell_result = run_device(PY, device_b, relay_db, [
        {"op": "login", "email": bootstrap["email"], "password": bootstrap["password"]},
        {"op": "sell", "product_id": pid, "quantity": 4, "branch_id": b_branch_id},
    ])
    assert sell_result["status_code"] == 200, sell_result["body"]
    # The route's own response carries the sale's LOCAL integer `id`, never
    # its cross-device `uid` (see api/retail_api.py's `create_sale` response
    # body) -- read B's own persisted row directly for its `uid` instead of
    # inventing a response-shape change no other test needs.
    b_sale_id = sell_result["body"]["data"]["id"]
    sale_uid = _read_device(device_b, "SELECT uid FROM sales WHERE id=?", (b_sale_id,))[0]["uid"]
    run_device(PY, device_b, relay_db, [{"op": "push"}])
    run_device(PY, device_a, relay_db, [{"op": "pull"}])

    # A's own LOCAL id for B's synced sale -- see module docstring for why
    # this is a direct-db read rather than a new op.
    a_local_sale_id = _local_sale_id_by_uid(device_a, sale_uid)

    _, return_result = run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "return_sale", "sale_id": a_local_sale_id,
         "items": [{"product_id": pid, "quantity": 1}]},
    ])
    assert return_result["status_code"] == 200, return_result["body"]
    run_device(PY, device_a, relay_db, [{"op": "push"}])
    run_device(PY, device_b, relay_db, [{"op": "pull"}])

    _assert_ledgers_converge(
        device_a, device_b, relay_db, cid_a, cid_b, "2000-01-01", "2099-12-31",
        shop["email"], shop["password"], bootstrap["email"], bootstrap["password"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. COGS travels as the ORIGIN's cost snapshot, never recomputed
# ─────────────────────────────────────────────────────────────────────────────

def test_cogs_is_the_origins_snapshot_not_receivers_avg_cost(tmp_path):
    """BOOKS-WAVE1-PLAN.md section 2.2's "COGS cost basis" row: "amounts are
    copied verbatim from the originating journal ... nothing is recomputed
    on the receiver." A's product carries cost_price 3.000; B independently
    diverges its OWN local copy of `avg_cost` for that same product to 50.0
    (a plausible real scenario -- a receiver whose own stock-costing history
    disagrees with the origin's) BEFORE A ever rings a sale. If the receiver
    ever recomputed COGS from its own cost basis instead of trusting the
    synced journal's own lines verbatim, B's posted COGS would read
    `quantity * 50.0`; the origin's own figure is `quantity * 3.000`.
    """
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_device(PY, device_a, relay_db, [
        {"op": "create_shop", "price": 6.0, "initial_stock": 0},  # cost_price = price/2 = 3.000
    ])
    cid_a, pid = shop["company_id"], shop["product_id"]
    _, branch = run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_branch", "name": "COGS Sync Branch", "address": "", "phone": ""},
    ])
    a_branch_id, a_branch_uid = branch["branch_id"], branch["branch_uid"]
    run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "adjust_stock", "product_id": pid, "quantity": 100,
         "reason": "Opening stock", "branch_id": a_branch_id},
        {"op": "push"},
    ])

    [bootstrap] = run_device(PY, device_b, relay_db, [{"op": "bootstrap"}])
    cid_b = bootstrap["company_id"]
    run_device(PY, device_b, relay_db, [{"op": "pull"}])

    # B diverges its OWN local cost basis for the SAME (synced) product
    # BEFORE A's sale ever exists -- direct-db write, see module docstring.
    _set_local_avg_cost(device_b, pid, 50.0)

    _, sell_result, _ = run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "sell", "product_id": pid, "quantity": 5, "branch_id": a_branch_id},
        {"op": "push"},
    ])
    assert sell_result["status_code"] == 200, sell_result["body"]

    run_device(PY, device_b, relay_db, [{"op": "pull"}])

    expected_cogs = 5 * 3.000  # A's own cost_price snapshot, never B's 50.0
    tb_a = _trial_balance(device_a, relay_db, shop["email"], shop["password"])
    tb_b = _trial_balance(device_b, relay_db, bootstrap["email"], bootstrap["password"])
    cogs_a = next(a for a in tb_a['accounts'] if a['code'] == '5900')
    cogs_b = next(a for a in tb_b['accounts'] if a['code'] == '5900')
    assert cogs_a['debit'] == pytest.approx(expected_cogs)
    assert cogs_b['debit'] == pytest.approx(expected_cogs), (
        f"B posted COGS using its OWN avg_cost (would read {5 * 50.0}) instead "
        f"of trusting the origin's synced journal verbatim: {cogs_b}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. A device must never double-post its OWN echoed event
# ─────────────────────────────────────────────────────────────────────────────

def test_origin_does_not_double_post_its_own_echo(tmp_path):
    """Section 6 trap #4: `FileRelay` deliberately echoes a device's own
    pushed events straight back to it on the very next pull (no
    sender-filtering) -- exactly like Owner's real relay and a hub-forwarded
    copy both can. Only ONE device is needed here: A pushes its own sale +
    COGS journals, then pulls, receiving its own echo, and must not create a
    second copy of either.

    D quotes M-A4's RED through this test (BOOKS-WAVE1-PLAN.md section 6):
    see this file's closing comment block for the isolated-copy mutation
    and its exact RED output.
    """
    device_a = tmp_path / "device_a"
    relay_db = tmp_path / "relay.db"

    [shop] = run_device(PY, device_a, relay_db, [
        {"op": "create_shop", "price": 9.0, "initial_stock": 50},  # self-heal branch: fine, single-device
    ])
    cid_a, pid = shop["company_id"], shop["product_id"]

    _, sell_result = run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "sell", "product_id": pid, "quantity": 2},
    ])
    assert sell_result["status_code"] == 200, sell_result["body"]

    run_device(PY, device_a, relay_db, [{"op": "push"}])
    census_before_echo = _gl_census(device_a, relay_db, cid_a)
    # STOCK-BOOKS: entering the product's 50 units now books ONE opening-entry
    # journal (Dr 1200 / Cr 3900) of its own, so the census is 3 -- the sale + its
    # COGS (still pinned at exactly 2, counted apart from the stock entry) plus
    # that one. What this can no longer catch: nothing -- a duplicate of ANY of
    # the three still changes the total, and the echo check below compares the
    # whole census before/after.
    _stock, _rest = _stock_entry_split(device_a, cid_a)
    assert (_stock, _rest) == (1, 2), (
        "expected 1 stock-entry journal + exactly 2 others (the sale posting + its "
        f"COGS posting) before any echo: {census_before_echo} stock={_stock} rest={_rest}"
    )
    assert census_before_echo["count"] == 3, census_before_echo

    # A pulls -- FileRelay hands back every event with seq > cursor,
    # including the ones A itself JUST pushed a moment ago.
    run_device(PY, device_a, relay_db, [{"op": "pull"}])

    census_after_echo = _gl_census(device_a, relay_db, cid_a)
    assert census_after_echo == census_before_echo, (
        f"A double-posted its own echoed journal(s): before={census_before_echo} "
        f"after={census_after_echo}"
    )
    [quarantine] = run_device(PY, device_a, relay_db, [
        {"op": "quarantine_rows", "entity_type": "gl_journal"},
    ])
    assert quarantine["rows"] == [], (
        f"A's own echo was parked instead of cleanly deduped: {quarantine['rows']}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Cursor reset AND a simulated relay switch, each followed by a full
#    re-pull, must never double-post anything (section 6 trap #7)
# ─────────────────────────────────────────────────────────────────────────────

def test_cursor_reset_and_relay_switch_replay_posts_nothing_twice(tmp_path):
    """D quotes M-C3's RED through this test (BOOKS-WAVE1-PLAN.md section 6):
    see this file's closing comment block for the isolated-copy mutation and
    its exact RED output -- removing the `if cur.rowcount:` gate around the
    line-insert loop in `sync_service.py`'s `gl_journal` branch turns THIS
    test red (a stale `lastrowid` after `ON CONFLICT ... DO NOTHING` either
    raises a FOREIGN KEY error or, worse, silently double-books lines under
    the wrong journal id).
    """
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_device(PY, device_a, relay_db, [
        {"op": "create_shop", "price": 15.0, "initial_stock": 0},
    ])
    cid_a, pid = shop["company_id"], shop["product_id"]
    _, branch = run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_branch", "name": "Replay Branch", "address": "", "phone": ""},
    ])
    a_branch_id, a_branch_uid = branch["branch_id"], branch["branch_uid"]
    run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "adjust_stock", "product_id": pid, "quantity": 100,
         "reason": "Opening stock", "branch_id": a_branch_id},
        {"op": "sell", "product_id": pid, "quantity": 2, "branch_id": a_branch_id},
        {"op": "push"},
    ])

    [bootstrap] = run_device(PY, device_b, relay_db, [{"op": "bootstrap"}])
    cid_b = bootstrap["company_id"]
    run_device(PY, device_b, relay_db, [{"op": "pull"}])

    census_first = _gl_census(device_b, relay_db, cid_b)
    # STOCK-BOOKS: A's `adjust_stock` of 100 units now books one stock-adjustment
    # journal, which syncs too -- so B holds 3: that one + sale + COGS (still
    # pinned at exactly 2 apart from it). Nothing is lost: any duplicate still
    # moves the total, and the replays below compare the full census.
    _stock, _rest = _stock_entry_split(device_b, cid_b)
    assert (_stock, _rest) == (1, 2), (census_first, _stock, _rest)
    assert census_first["count"] == 3

    # A cursor reset: forces a re-pull of the ENTIRE history from seq 0.
    run_device(PY, device_b, relay_db, [{"op": "reset_cursor"}])
    run_device(PY, device_b, relay_db, [{"op": "pull"}])
    census_after_reset = _gl_census(device_b, relay_db, cid_b)
    assert census_after_reset == census_first, (
        f"a cursor reset replay double-posted: before={census_first} "
        f"after={census_after_reset}"
    )

    # A simulated hub<->cloud relay switch: the SAME two-column reset
    # `ensure_cursor_matches_relay` performs on a genuine relay change
    # (BOOKS-WAVE1-PLAN.md section 7 checkpoint C5) -- another full re-pull.
    run_device(PY, device_b, relay_db, [
        {"op": "switch_relay_url", "relay_url": "https://cloud.example.invalid/relay"},
    ])
    run_device(PY, device_b, relay_db, [{"op": "pull"}])
    census_after_switch = _gl_census(device_b, relay_db, cid_b)
    assert census_after_switch == census_first, (
        f"a relay-switch replay double-posted: before={census_first} "
        f"after={census_after_switch}"
    )

    [quarantine] = run_device(PY, device_b, relay_db, [
        {"op": "quarantine_rows", "entity_type": "gl_journal"},
    ])
    assert quarantine["rows"] == []


# ─────────────────────────────────────────────────────────────────────────────
# 6. A reversal that arrives before its original is parked, then heals and
#    applies exactly once when the original arrives
# ─────────────────────────────────────────────────────────────────────────────

def test_quarantine_retry_applies_each_journal_once(tmp_path):
    """No branch/product needed at all -- both journals are posted directly
    via `gl_post_backdated`/`gl_reverse_last` (company-wide, `branch_id=None`)
    so the ordering this test needs to force is deterministic rather than a
    real-process race. J1's own relay message is DELAYED (removed from the
    shared relay, its exact wire payload text kept) so B receives ONLY J2 (the
    reversal) first, parking it on `missing_parent:gl_journal`; redelivering
    J1's real payload later heals it in the very next pull, per `apply_pull_
    result`'s own "retried AFTER this batch's own events" ordering.
    """
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_device(PY, device_a, relay_db, [{"op": "create_shop", "initial_stock": 0}])
    cid_a = shop["company_id"]

    [j1] = run_device(PY, device_a, relay_db, [{
        "op": "gl_post_backdated", "company_id": cid_a, "business_date": "2024-06-01",
        "source_type": "adjustment",
        "lines": [{"account_code": "1000", "debit": 40.0, "credit": 0.0},
                  {"account_code": "4000", "debit": 0.0, "credit": 40.0}],
        "reference": "MANUAL-J1", "memo": "Quarantine-retry fixture journal",
    }])
    j1_uid = j1["uid"]

    [j2] = run_device(PY, device_a, relay_db, [{"op": "gl_reverse_last", "company_id": cid_a}])
    assert j2["reversed_journal_id"] == j1["journal_id"]

    run_device(PY, device_a, relay_db, [{"op": "push"}])

    # Delay J1's own relay message -- B must receive ONLY J2 on its first pull.
    j1_payload_text = _delay_relay_event(relay_db, j1_uid)

    [bootstrap] = run_device(PY, device_b, relay_db, [{"op": "bootstrap"}])
    cid_b = bootstrap["company_id"]
    run_device(PY, device_b, relay_db, [{"op": "pull"}])

    [quarantine_before] = run_device(PY, device_b, relay_db, [
        {"op": "quarantine_rows", "entity_type": "gl_journal"},
    ])
    assert len(quarantine_before["rows"]) == 1, quarantine_before["rows"]
    assert quarantine_before["rows"][0]["reason"] == "missing_parent:gl_journal"

    census_parked = _gl_census(device_b, relay_db, cid_b)
    assert census_parked["count"] == 0, "the reversal must not apply while its parent is missing"

    # Redeliver J1's real payload -- appended after J2's own seq.
    _redeliver_relay_event(relay_db, j1_payload_text)
    run_device(PY, device_b, relay_db, [{"op": "pull"}])

    [quarantine_after] = run_device(PY, device_b, relay_db, [
        {"op": "quarantine_rows", "entity_type": "gl_journal"},
    ])
    assert quarantine_after["rows"] == [], quarantine_after["rows"]

    census_healed = _gl_census(device_b, relay_db, cid_b)
    assert census_healed["count"] == 2
    assert census_healed["distinct_uids"] == 2
    assert census_healed["null_uids"] == 0

    # One more replay tick -- must not apply either journal a second time.
    run_device(PY, device_b, relay_db, [{"op": "pull"}])
    census_replayed = _gl_census(device_b, relay_db, cid_b)
    assert census_replayed == census_healed, (
        f"a healed reversal was re-applied on a later, no-op pull tick: "
        f"before={census_healed} after={census_replayed}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 7. A pre-upgrade (uid-NULL) journal heals the very next boot, and its
#    healed identity reaches every other device
# ─────────────────────────────────────────────────────────────────────────────

def test_pre_upgrade_journal_heals_after_reboot_backfill(tmp_path):
    """`insert_legacy_journal_raw` reproduces exactly what a v37-era
    `gl_journals` row looks like: `uid`/`source_uid` both genuinely NULL,
    never faked by mutating a post_journal-minted row (section 6 trap #5).
    The NEXT `run_device()` call for the SAME device is a real "reboot" --
    `_stock_sync_device.py`'s own `main()` calls `app.init_app()` fresh, which
    calls `database.schema.init_retail()`, which runs the hasattr-guarded
    `general_ledger.backfill_legacy_journals(conn)` boot step on EVERY launch
    (see that call site's own comment) -- so no dedicated "run the backfill"
    op is needed at all; simply calling the device again is the reboot.

    THE ORDERING THIS TEST DEPENDS ON: the boot backfill runs once, at the
    TOP of `main()`, before ANY action in that call's own list executes.
    So `insert_legacy_journal_raw` and the "before" `gl_census`/`outbox_
    count_by_type` checks MUST be batched into the SAME `run_device()` call
    (the SAME boot) -- a raw-inserted row checked via a SEPARATE, later
    `run_device()` call would already have been healed by THAT call's own
    boot backfill before the check ever ran, silently proving nothing (the
    exact "fixture manufactures the state that hides the bug" shape
    CLAUDE.md's engineering standard names).
    """
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_device(PY, device_a, relay_db, [{"op": "create_shop", "initial_stock": 0}])
    cid_a = shop["company_id"]

    _insert_result, census_before_reboot, outbox_before = run_device(PY, device_a, relay_db, [
        {"op": "insert_legacy_journal_raw", "company_id": cid_a, "business_date": "2022-03-15",
         "source_type": "opening_balance",
         "lines": [{"account_code": "1100", "debit": 250.0, "credit": 0.0},
                   {"account_code": "3000", "debit": 0.0, "credit": 250.0}],
         "reference": "LEGACY-1"},
        {"op": "gl_census", "company_id": cid_a},
        {"op": "outbox_count_by_type", "entity_type": "gl_journal"},
    ])
    assert census_before_reboot["null_uids"] == 1, (
        f"the raw insert must land with uid IS NULL, exactly like a genuine "
        f"pre-v41 row: {census_before_reboot}"
    )
    assert outbox_before["count"] == 0, "a legacy row must not queue its own event before healing"

    # THE REBOOT -- just another run_device() call for the same device.
    # `main()` (via `app.init_app()` -> `init_retail()`) re-runs the boot
    # backfill fresh in THIS subprocess before this call's own `push` action
    # ever executes, so that same `push` flushes whatever the backfill just
    # queued in the SAME invocation -- no separate "run the backfill" step.
    run_device(PY, device_a, relay_db, [{"op": "push"}])

    census_after_reboot = _gl_census(device_a, relay_db, cid_a)
    assert census_after_reboot["null_uids"] == 0, (
        f"the legacy journal did not heal on reboot: {census_after_reboot}"
    )
    assert census_after_reboot["count"] == 1

    [bootstrap] = run_device(PY, device_b, relay_db, [{"op": "bootstrap"}])
    cid_b = bootstrap["company_id"]
    run_device(PY, device_b, relay_db, [{"op": "pull"}])

    census_b = _gl_census(device_b, relay_db, cid_b)
    assert census_b["count"] == 1
    assert census_b["null_uids"] == 0
    assert census_b["uid_hash"] == census_after_reboot["uid_hash"], (
        "the healed legacy journal's uid did not reach the other device intact"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 8. Customer payment on B converges Cash and AR on A -- ledger parity is
#    asserted; the AR SUBLEDGER tie (R6, device-local credit_balance) is only
#    recorded, never asserted true on the receiver
# ─────────────────────────────────────────────────────────────────────────────

def test_customer_payment_on_b_converges_cash_and_ar(tmp_path):
    """Everything (the credit sale AND the payment against it) happens on B
    alone -- no shared branch/product resolution is needed for this test's
    own point, which is purely about the GL's AR/Cash figures converging, not
    about stock. A is a fresh bootstrap that only ever pulls.

    Per BOOKS-WAVE1-PLAN.md section 6's own instruction for this test:
    "asserts ledger parity only and records the subledger tie separately" --
    `customers.credit_balance` is device-local (R6, BOOKS-WAVE1-PLAN.md
    section 8) and is NOT expected to read true on A; only B's OWN
    subledger tie (the device that actually wrote the credit_balance) is
    asserted.
    """
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_device(PY, device_b, relay_db, [
        {"op": "create_shop", "price": 100.0, "initial_stock": 50},
    ])
    cid_b, pid = shop["company_id"], shop["product_id"]

    _, customer = run_device(PY, device_b, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_customer", "name": "GL Sync Customer"},
    ])
    cust_id = customer["customer_id"]
    assert cust_id is not None, customer["body"]

    _, sell_result = run_device(PY, device_b, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "sell_credit", "product_id": pid, "quantity": 1,
         "customer_id": cust_id, "amount_paid": 20.0},
    ])
    assert sell_result["status_code"] == 200, sell_result["body"]
    assert sell_result["body"]["data"]["balance_due"] == pytest.approx(80.0)

    _, payment_result = run_device(PY, device_b, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "customer_payment", "customer_id": cust_id, "amount": 30.0},
    ])
    assert payment_result["status_code"] == 200, payment_result["body"]
    run_device(PY, device_b, relay_db, [{"op": "push"}])

    tb_b = _trial_balance(device_b, relay_db, shop["email"], shop["password"])
    assert tb_b["subledger"]["ar_ties"] is True, (
        f"B's OWN subledger must tie -- it is the device that wrote "
        f"credit_balance in the first place: {tb_b['subledger']}"
    )

    [bootstrap] = run_device(PY, device_a, relay_db, [{"op": "bootstrap"}])
    run_device(PY, device_a, relay_db, [{"op": "pull"}])
    tb_a = _trial_balance(device_a, relay_db, bootstrap["email"], bootstrap["password"])

    # LEDGER PARITY, asserted: every GL account balance (Cash, AR, Revenue)
    # must match -- the actual money-moved facts, which travelled as
    # `gl_journal` sync events regardless of `credit_balance` ever syncing.
    ledger_a = {a['code']: a['balance'] for a in tb_a['accounts']}
    ledger_b = {a['code']: a['balance'] for a in tb_b['accounts']}
    assert ledger_a == ledger_b, f"GL account balances diverge:\nA={ledger_a}\nB={ledger_b}"

    # SUBLEDGER TIE, recorded only (R6) -- `credit_balance` is device-local
    # and NEVER reaches A via sync, so A's own subledger side reads whatever
    # A's never-touched `customers.credit_balance` happens to be (typically
    # 0, since A never wrote it) while the GL side already reads the real
    # figure. This is the documented, expected gap the plan calls R6, not a
    # defect this test polices -- recorded here so a reader can see the
    # actual value rather than the assertion being silently absent.
    print(f"R6 (recorded, not asserted): A's own subledger_tie = {tb_a['subledger']}")


# ─────────────────────────────────────────────────────────────────────────────
# 9. business_date travels verbatim -- both devices bucket a backdated
#    journal into the SAME reporting period
# ─────────────────────────────────────────────────────────────────────────────

def test_backdated_journal_lands_in_same_period_on_both(tmp_path):
    """Section 6 trap #6's other half: "one backdated journal". Posted years
    in the past via `gl_post_backdated` (company-wide, no branch/product
    needed), it must bucket into the SAME `[start_date, end_date]` window on
    BOTH devices -- proving `business_date` travelled as the wire value
    itself and was never re-stamped to "today" on the receiving device
    (BOOKS-WAVE1-PLAN.md section 2.2's "Reports" row).
    """
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_device(PY, device_a, relay_db, [{"op": "create_shop", "initial_stock": 0}])
    cid_a = shop["company_id"]

    run_device(PY, device_a, relay_db, [{
        "op": "gl_post_backdated", "company_id": cid_a, "business_date": "2019-07-04",
        "source_type": "adjustment",
        "lines": [{"account_code": "1000", "debit": 77.777, "credit": 0.0},
                  {"account_code": "4000", "debit": 0.0, "credit": 77.777}],
        "reference": "BACKDATED-1",
    }])
    run_device(PY, device_a, relay_db, [{"op": "push"}])

    [bootstrap] = run_device(PY, device_b, relay_db, [{"op": "bootstrap"}])
    run_device(PY, device_b, relay_db, [{"op": "pull"}])

    # INSIDE the journal's own period on both.
    pnl_a_in = _pnl(device_a, relay_db, "2019-07-01", "2019-07-31", shop["email"], shop["password"])
    pnl_b_in = _pnl(device_b, relay_db, "2019-07-01", "2019-07-31",
                     bootstrap["email"], bootstrap["password"])
    assert pnl_a_in['has_activity'] is True
    assert pnl_b_in['has_activity'] is True
    assert pnl_a_in['revenue'] == pytest.approx(77.777)
    assert pnl_b_in['revenue'] == pytest.approx(77.777)

    # OUTSIDE the journal's own period on both -- proves it did NOT land on
    # today's date (the receiving device's own "now") on either side.
    pnl_a_out = _pnl(device_a, relay_db, "2019-08-01", "2019-08-31", shop["email"], shop["password"])
    pnl_b_out = _pnl(device_b, relay_db, "2019-08-01", "2019-08-31",
                      bootstrap["email"], bootstrap["password"])
    assert pnl_a_out['has_activity'] is False
    assert pnl_b_out['has_activity'] is False


# ─────────────────────────────────────────────────────────────────────────────
# MUTATION PROOFS (BOOKS-WAVE1-PLAN.md section 6: "D quotes the RED of M-A1,
# M-C3 and M-A4 through the headline/replay/echo tests"), performed in an
# ISOLATED COPY of the tree, never the shared worktree. Method (identical to
# every other Wave 1 builder's own report):
#
#   robocopy <worktree> %TEMP%\builderD-mut /E /XD .git android node_modules
#            dist build .venv .venv-*
#   (edit ONLY the copy's general_ledger.py / sync_service.py)
#   <copy>\.venv...\python.exe -m pytest <copy>\products\retail\tests\
#       retail_gl_two_device_sync_test.py::<one test> -v
#   (observe RED, restore by re-copying the ORIGINAL file back into place
#    inside the copy, or simply delete the whole copy)
#
# M-A1 (headline test) -- general_ledger.py's `post_journal`: comment out
# `_queue_gl_journal_sync_event(conn, journal_id)`.
#   ACTUAL RED, quoted verbatim from the isolated-copy run:
#
#     AssertionError: trial balances diverge:
#     A={'accounts': [... every account 'debit': 0.0, 'credit': 0.0, 'balance': 0.0 ...],
#        'total_debit': 0.0, 'total_credit': 0.0, ...}
#     B={'accounts': [... '1000' debit=12.021 balance=12.021, '1200' credit=6.012
#        balance=-6.012, '4000' credit=12.021 balance=12.021, '5900' debit=6.012
#        balance=6.012, ... ], 'total_debit': 18.033, 'total_credit': 18.033, ...}
#
#   B's own trial_balance/P&L show the real sale (posted locally on B
#   regardless of sync -- B still calls `post_journal` itself), but A's stay
#   at zero activity across every account, because the journal that would
#   have carried it to A was never queued. Restoring the call returns the
#   test to GREEN (confirmed: reverting in the same isolated copy and
#   re-running turned it green again without touching anything else).
#
# M-A4 (echo test) -- general_ledger.py's `post_journal`: change
# `uid = str(uuid.uuid4())` to `uid = None`.
#   ACTUAL RED, quoted verbatim from the isolated-copy run (this is NOT what
#   the mechanism looks like in `retail_gl_emit_test.py`'s own unit-shaped
#   proof, which asserts the raw `sqlite3.IntegrityError` directly -- through
#   THIS file's own real HTTP route, `create_sale`'s own `except Exception:
#   conn.rollback()` catches it first and returns a normal 500 response, so
#   the test never even reaches the echo assertion it was named for):
#
#     AssertionError: {'message': 'NOT NULL constraint failed: sync_outbox.
#     entity_id', 'status': 'error'}
#     assert 500 == 200
#
#   (at `assert sell_result["status_code"] == 200, sell_result["body"]`,
#   this file's own setup line for the sale that never got to post at all).
#   `_queue_gl_journal_sync_event`'s `INSERT INTO sync_outbox` names
#   `entity_id=payload['uid']`, and `sync_outbox.entity_id` is `NOT NULL` --
#   so with the origin's uid nulled, the sale that would have posted the
#   journal fails outright, not merely the sync half. Restoring `uid =
#   str(uuid.uuid4())` returns the test to GREEN.
#
# M-C3 (replay test) -- sync_service.py's `gl_journal` branch: remove the
# `if cur.rowcount:` guard so the line-insert loop always runs.
#   ACTUAL RED, quoted verbatim from the isolated-copy run:
#
#     RuntimeError: device subprocess failed (exit=1) for app_data_dir=...
#     --- stderr ---
#     ...
#       File ".../commercial_runtime/sync/sync_service.py", line ..., in _apply_event
#         conn.execute(
#           "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) "
#           "VALUES (?,?,?,?,?)",
#           (journal_id, line_account_ids[code], float(debit), float(credit), line_memo),
#         )
#     sqlite3.IntegrityError: FOREIGN KEY constraint failed
#
#   -- raised on the RESET-REPLAY pull specifically (the first, un-reset
#   pull is still the header's own first-ever insert and stays green): the
#   header INSERT's own `ON CONFLICT(uid) ... DO NOTHING` still fires on the
#   replayed (already-seen) journal, so `cur.lastrowid` is STALE (SQLite
#   leaves it at whatever this cursor's last REAL insert was), and the
#   line-insert loop then tries to insert `gl_journal_lines` under that
#   wrong/nonexistent `journal_id`. Restoring the guard returns the test to
#   GREEN.
# ─────────────────────────────────────────────────────────────────────────────
