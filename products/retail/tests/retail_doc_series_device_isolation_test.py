"""Aura Retail -- Aseel-parity wave A-PAR, "per-document-type numbering
series" (schema v34): THE COLLISION PROPERTY, proved across TWO REAL
PROCESSES.

Uses `_accept_harness.run_accept_device` + `_accept_device.py` -- the same
two-instance shape `_accept_wedge_test.py` already uses for exactly this
class of proof (separate `AURA_APP_DATA`, separate terminal identity, a
real process boundary, `raw_select` against `sync_cursor` read directly
rather than inferred). `_accept_device.py` gained three ops for this pass
(`set_terminal_id`, `create_doc_series`, `claim_doc_series`, `list_doc_
series`, `quarantine_rows`) -- see that file's own module docstring for
why extending the existing script is correct here rather than forking a
fourth device-process script.

WHAT THIS FILE PROVES:

  1. `test_a_book_claimed_by_one_device_is_never_used_by_another_and_
      neither_wedges_the_other` -- device A claims a 'sale' book; device B
      (a genuinely different terminal identity, a genuinely different
      retail.db) rings a sale and gets the LEGACY number, never A's book.
      Both sales replicate both ways with no `IntegrityError`, and BOTH
      cursors are read DIRECTLY from `sync_cursor` and shown to have
      advanced -- this is the structural half of THE ONE DECISION
      (core/retail/doc_series.py's own module docstring): a device's own
      `resolve_series` query can never return another device's book, so
      the AUDIT-032B collision this whole feature exists to prevent is
      unreachable by construction, not merely policed after the fact.

  2. `test_two_offline_devices_coding_the_same_book_quarantine_without_
      wedging_the_batch` -- two devices, both never having pulled from
      each other, each create a 'sale' book coded 'A' for their own
      (locally-distinct) company. On convergence the SECOND one to apply
      lands in `sync_apply_quarantine` with `reason='duplicate_series_
      code'`, the cursor still advances PAST it, and a later, unrelated
      event in the SAME batch still lands -- proving `idx_doc_series_
      code`'s own reachable collision (B2's revised conclusion: this
      exact path, not the allocator-index path the original review
      guessed) is CONTAINED rather than a permanent wedge.

Run (one file per process, AUDIT-010):
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \
        products/retail/tests/retail_doc_series_device_isolation_test.py -v -s
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from _accept_harness import run_accept_device  # noqa: E402

PY = sys.executable

TERM_A = 'docseries-isolation-till-a'
TERM_B = 'docseries-isolation-till-b'


def _make_main_computer(device_dir, relay_db, email, password, term, tag):
    """Make this device the shop's main computer, by the real route.

    DELIBERATE UPDATE (P2S, schema v42): creating or claiming a numbering book
    is main-computer-only now (409 TILL_NEEDS_MAIN_COMPUTER). This file used to
    call those routes on a bare harness shop, which was never "the main
    computer" -- it just was not asked. The harness founds a shop by inserting
    the owner directly, so it plants the ADMIN_CREATED audit row the real
    founding route writes (the only thing that tells a founder from a joiner --
    same step as _v42_shop.Shop), then announces and claims through
    POST /api/devices/me/claim-admin.

    WHAT THIS FILE CAN NO LONGER CATCH: whether create_doc_series /
    claim_doc_series refuse a computer that is NOT main. That is pinned, both
    directions, in retail_v42_till_allocation_test.py."""
    run_accept_device(PY, device_dir, relay_db, [
        {"op": "reg_exec",
         "sql": ("INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id) "
                 "SELECT ?, company_id, NULL, 'ADMIN_CREATED', 'user', 'founder' FROM users LIMIT 1"),
         "params": [f"founder-audit-{tag}"]},
    ])
    out = run_accept_device(PY, device_dir, relay_db, [
        {"op": "set_terminal_id", "terminal_id": term},
        {"op": "login", "email": email, "password": password},
        {"op": "announce", "reason": "boot"},
        {"op": "claim_main"},
    ])
    assert out[-1]["status_code"] == 200, out[-1]["body"]


def _main_device_id(device_dir, relay_db):
    [res] = run_accept_device(PY, device_dir, relay_db, [
        {"op": "reg_select", "sql": "SELECT device_id FROM admin_device_assignment"}])
    return res["rows"][0]["device_id"]


def _record_main(device_dir, relay_db, main_device_id, epoch):
    """Tell `device_dir`'s computer who the shop's main computer is -- the fact its
    registry stream would deliver as an `admin_device` event. S-AUTH (AUDIT-033):
    numbering-book events are the main computer's to send and a receiver judges
    them against the main it KNOWS, so a computer that has not heard who the main
    is parks them (`main_unknown`). These two tests simulate only the RETAIL
    stream, so the registry fact is recorded directly instead of synced.

    WHAT THESE TESTS CAN NO LONGER CATCH: a book event applied on a computer that
    does not know who the main is (pinned in test_sync_authority_retail.py)."""
    run_accept_device(PY, device_dir, relay_db, [
        {"op": "reg_exec",
         "sql": ("INSERT OR REPLACE INTO admin_device_assignment (company_id, device_id, epoch, assigned_at_utc) "
                 "SELECT company_id, ?, ?, '2026-01-01T00:00:00+00:00' FROM users LIMIT 1"),
         "params": [main_device_id, epoch]}])


def test_a_book_claimed_by_one_device_is_never_used_by_another_and_neither_wedges_the_other(tmp_path):
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_accept_device(PY, device_a, relay_db, [
        {"op": "create_shop", "price": 8.0, "initial_stock": 100},
    ])
    a_pid = shop["product_id"]

    _make_main_computer(device_a, relay_db, shop["email"], shop["password"], TERM_A, "a")
    [_term_a, _login_a, created] = run_accept_device(PY, device_a, relay_db, [
        {"op": "set_terminal_id", "terminal_id": TERM_A},
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_doc_series", "doc_type": "sale", "code": "ISOA", "label": "A's Book"},
    ])
    assert created["status_code"] == 200, created["body"]
    series_id = created["series_id"]

    [_term_a2, _login_a2, claim, sale_a] = run_accept_device(PY, device_a, relay_db, [
        {"op": "set_terminal_id", "terminal_id": TERM_A},
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "claim_doc_series", "series_id": series_id},
        {"op": "sell", "product_id": a_pid, "quantity": 1},
    ])
    assert claim["status_code"] == 200, claim["body"]
    assert sale_a["status_code"] == 200, sale_a["body"]
    a_sale_number = sale_a["body"]["data"]["sale_number"]
    assert a_sale_number == "ISOA-000001", (
        f"device A must mint from its OWN claimed book; got {a_sale_number!r}")

    run_accept_device(PY, device_a, relay_db, [{"op": "push"}])

    # Device B: a genuinely different install, a genuinely different
    # terminal identity, its OWN product (no dependency on A's catalogue
    # ever arriving -- this test is about numbering isolation, not
    # catalogue sync).
    [bootstrap] = run_accept_device(PY, device_b, relay_db, [{"op": "bootstrap"}])
    _record_main(device_b, relay_db, _main_device_id(device_a, relay_db), 1)
    run_accept_device(PY, device_b, relay_db, [
        {"op": "set_terminal_id", "terminal_id": TERM_B},
        {"op": "login", "email": bootstrap["email"], "password": bootstrap["password"]},
        {"op": "pull"},  # receives A's doc_series book, claimed by TERM_A
    ])

    [_term_b1, _login_b1, b_series_list] = run_accept_device(PY, device_b, relay_db, [
        {"op": "set_terminal_id", "terminal_id": TERM_B},
        {"op": "login", "email": bootstrap["email"], "password": bootstrap["password"]},
        {"op": "list_doc_series", "doc_type": "sale"},
    ])
    assert b_series_list["status_code"] == 200, b_series_list["body"]
    b_rows = b_series_list["data"]
    assert any(r["code"] == "ISOA" for r in b_rows), "A's book never arrived on B"
    isoa_on_b = next(r for r in b_rows if r["code"] == "ISOA")
    assert isoa_on_b["is_mine"] is False, (
        "device B must never see A's claimed book as its own -- collision prevention is broken")
    assert isoa_on_b["next_no"] is None, (
        "a peer's next_no must be null, never a number -- B has no counter row for a book it does not own")

    [_term_b2, _login_b2, b_product] = run_accept_device(PY, device_b, relay_db, [
        {"op": "set_terminal_id", "terminal_id": TERM_B},
        {"op": "login", "email": bootstrap["email"], "password": bootstrap["password"]},
        {"op": "create_product", "name": "B Own Widget", "initial_stock": 50},
    ])
    assert b_product["status_code"] == 200, b_product["body"]
    # `sell`'s product_id must be resolved from the JUST-created product
    # (this harness's actions are static JSON, so a second batch is used
    # for the sale itself, once the product id is known).
    b_pid = b_product["product_id"]
    [_term_b3, _login_b3, b_sale] = run_accept_device(PY, device_b, relay_db, [
        {"op": "set_terminal_id", "terminal_id": TERM_B},
        {"op": "login", "email": bootstrap["email"], "password": bootstrap["password"]},
        {"op": "sell", "product_id": b_pid, "quantity": 1},
    ])
    assert b_sale["status_code"] == 200, b_sale["body"]
    b_sale_number = b_sale["body"]["data"]["sale_number"]
    assert not b_sale_number.startswith("ISOA-"), (
        f"device B minted from A's claimed book -- collision prevention is broken: {b_sale_number!r}")
    assert b_sale_number.startswith("SALE-"), (
        f"device B must fall back to the legacy format; got {b_sale_number!r}")

    # Push B's sale back through the relay; pull it on A. Neither cursor
    # may wedge, and no IntegrityError may escape -- the exact AUDIT-032B
    # shape this whole feature exists to prevent (both companies' sale_
    # number columns are independent per company_id here, but the SAME
    # apply-branch code path that would raise on a REAL cross-device
    # collision is exercised regardless).
    run_accept_device(PY, device_b, relay_db, [{"op": "push"}])
    pull_on_a = run_accept_device(PY, device_a, relay_db, [
        {"op": "set_terminal_id", "terminal_id": TERM_A},
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "pull"},  # must not raise
    ])
    assert pull_on_a[-1]["ok"] is True

    # Both cursors read DIRECTLY, not inferred from "no exception was
    # raised" -- retail_site_relay_schema_test.py's/_accept_wedge_test.py's
    # own standard for this exact class of claim.
    [cursor_a] = run_accept_device(PY, device_a, relay_db, [
        {"op": "raw_select", "sql": "SELECT last_seq FROM sync_cursor WHERE id=1"},
    ])
    [cursor_b] = run_accept_device(PY, device_b, relay_db, [
        {"op": "raw_select", "sql": "SELECT last_seq FROM sync_cursor WHERE id=1"},
    ])
    assert cursor_a["rows"][0]["last_seq"] > 0, "device A's cursor never advanced"
    assert cursor_b["rows"][0]["last_seq"] > 0, "device B's cursor never advanced"

    # Neither device's own quarantine table holds anything -- a REAL
    # cross-device collision on sales.sale_number never had the chance to
    # occur here (different company_id on each install), so this is the
    # "ordinary case has zero quarantine noise" sanity check, not the
    # collision-containment proof (that is test 2, below).
    [qa] = run_accept_device(PY, device_a, relay_db, [{"op": "quarantine_count"}])
    [qb] = run_accept_device(PY, device_b, relay_db, [{"op": "quarantine_count"}])
    assert qa["count"] == 0, "device A's quarantine table should be empty in the ordinary case"
    assert qb["count"] == 0, "device B's quarantine table should be empty in the ordinary case"


def test_two_offline_devices_coding_the_same_book_quarantine_without_wedging_the_batch(tmp_path):
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_accept_device(PY, device_a, relay_db, [
        {"op": "create_shop", "price": 5.0, "initial_stock": 10},
    ])
    _make_main_computer(device_a, relay_db, shop["email"], shop["password"], TERM_A, "a")
    run_accept_device(PY, device_a, relay_db, [
        {"op": "set_terminal_id", "terminal_id": TERM_A},
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_doc_series", "doc_type": "sale", "code": "DUP", "label": "A's Dup Book"},
    ])
    run_accept_device(PY, device_a, relay_db, [{"op": "push"}])

    # Device B: genuinely different install, NEVER having pulled A's book
    # yet, independently codes ITS OWN 'sale' book 'DUP' too -- the
    # reachable collision B2's revised conclusion names (not the
    # allocator-index path the original review guessed at).
    [bootstrap] = run_accept_device(PY, device_b, relay_db, [{"op": "bootstrap"}])
    _make_main_computer(device_b, relay_db, bootstrap["email"], bootstrap["password"], TERM_B, "b")
    run_accept_device(PY, device_b, relay_db, [
        {"op": "set_terminal_id", "terminal_id": TERM_B},
        {"op": "login", "email": bootstrap["email"], "password": bootstrap["password"]},
        {"op": "create_doc_series", "doc_type": "sale", "code": "DUP", "label": "B's Dup Book"},
        {"op": "create_product", "name": "Sentinel After Collision", "initial_stock": 5},
    ])
    run_accept_device(PY, device_b, relay_db, [{"op": "push"}])

    # Both computers believed they were main while apart. By the time A pulls, the
    # registry stream has settled it and B's claim is the one that stands (a higher
    # epoch): A now knows B is main, so B's book reaches the duplicate-code check
    # this test is about instead of being refused at the authority gate.
    _record_main(device_a, relay_db, _main_device_id(device_b, relay_db), 2)

    # A pulls the WHOLE batch (B's colliding doc_series create, then B's
    # sentinel product create) in ONE pull.
    pull_on_a = run_accept_device(PY, device_a, relay_db, [
        {"op": "set_terminal_id", "terminal_id": TERM_A},
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "pull"},  # must not raise -- this IS the assertion for the wedge itself
    ])
    assert pull_on_a[-1]["ok"] is True

    [qcount] = run_accept_device(PY, device_a, relay_db, [{"op": "quarantine_count"}])
    assert qcount["count"] == 1, "B's colliding 'DUP' book must be parked, not silently dropped or fatal"

    [qrows] = run_accept_device(PY, device_a, relay_db, [{"op": "quarantine_rows"}])
    row = qrows["rows"][0]
    assert row["entity_type"] == "doc_series"
    assert row["reason"] == "duplicate_series_code", row

    [cursor_a] = run_accept_device(PY, device_a, relay_db, [
        {"op": "raw_select", "sql": "SELECT last_seq FROM sync_cursor WHERE id=1"},
    ])
    assert cursor_a["rows"][0]["last_seq"] > 0, (
        "the cursor must still advance past the quarantined event -- a device stuck here "
        "re-pulls the SAME failing range forever, exactly wave A's worst defect")

    # The LATER, unrelated event in the SAME batch (B's sentinel product)
    # must still have landed -- it was not blocked by the quarantined
    # doc_series event ahead of it.
    [sentinel] = run_accept_device(PY, device_a, relay_db, [
        {"op": "raw_select", "sql": "SELECT COUNT(*) AS c FROM products WHERE name=?",
         "params": ["Sentinel After Collision"]},
    ])
    assert sentinel["rows"][0]["c"] == 1, (
        "a sibling event in the same batch was wedged by the quarantined doc_series collision")

    # A's OWN 'DUP' book is untouched -- the loser is B's, never A's.
    [a_series] = run_accept_device(PY, device_a, relay_db, [
        {"op": "raw_select", "sql": "SELECT label FROM doc_series WHERE doc_type='sale' AND code='DUP'"},
    ])
    assert len(a_series["rows"]) == 1, "A's own book must not have been duplicated or removed"
    assert a_series["rows"][0]["label"] == "A's Dup Book"


# AUDIT-033 / S-AUTH: authority events (users, devices, the main-computer
# assignment, till allocations, numbering books, release acks) are refused unless
# a member of the shop signed them. Every simulated computer here is a real
# process with its own app-data directory, so each one is given what a real
# activated till has: a device key, an Owner-signed lease and a trust store (see
# `_provision_signed_identity` in _accept_device.py). Signing and verification are
# the production code path reading that app data -- nothing is stubbed.
@pytest.fixture(autouse=True)
def _real_signing_identities(monkeypatch):
    monkeypatch.setenv("AURA_ACCEPT_SIGNED", "1")
    monkeypatch.setenv("AURA_ACCEPT_SIGNED_LICENCE", "LIC-ACCEPT-SIGNED")
