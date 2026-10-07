"""Aura Retail -- W2b/W3a core rules for configuration sync (schema v50).

Pins `core.retail.config_sync`: the merge rule (`lww_incoming_wins`), the
"do they actually disagree" test (`config_values_differ`) and the legacy
promotion uid (`promotion_legacy_uid`).

WHAT EACH TEST GUARDS (and the mutation that must turn it RED):
  * higher row_version wins, even against a LATER timestamp: comparing the
    timestamp first lets a clock-skewed older edit beat a newer one.
  * equal version -> later updated_at_utc wins.
  * full tie -> the winner is decided by content and is OPPOSITE in the two
    directions (A's verdict on (a, b) is the inverse of B's on (b, a)); a rule
    that returns True (or False) on a tie leaves each computer keeping its own
    row forever -- they never converge.
  * identical content is no win: otherwise every echo rewrites the row.
  * the tiebreak ignores bookkeeping keys (company_id, id, sync_backfill_pending)
    that legitimately differ between computers; including them makes two
    computers disagree about which payload is "bigger".
  * the legacy promotion uid is the same for the same promotion typed on two
    computers, spelled differently (10 vs 10.0), differs for any real
    difference, and a second identical copy on ONE computer gets its own uid.
"""
import itertools
import sys
import uuid
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail import config_sync as cs  # noqa: E402


def _row(**kw):
    base = {'name': 'Eid', 'discount_pct': 10, 'status': 'active',
            'row_version': 1, 'updated_at_utc': '2026-10-07T10:00:00Z'}
    base.update(kw)
    return base


def test_higher_row_version_wins_even_against_a_later_timestamp():
    local = _row(row_version=2, updated_at_utc='2026-10-07T09:00:00Z')
    incoming = _row(row_version=3, updated_at_utc='2026-10-07T08:00:00Z', name='X')
    assert cs.lww_incoming_wins(local, incoming) is True
    assert cs.lww_incoming_wins(incoming, local) is False


def test_equal_version_later_timestamp_wins():
    local = _row(updated_at_utc='2026-10-07T10:00:00Z')
    incoming = _row(updated_at_utc='2026-10-07T10:00:01Z', name='X')
    assert cs.lww_incoming_wins(local, incoming) is True
    assert cs.lww_incoming_wins(incoming, local) is False


def test_full_tie_converges_both_directions():
    a = _row(name='Alpha')
    b = _row(name='Beta')
    verdict_on_a = cs.lww_incoming_wins(a, b)   # A holds a, receives b
    verdict_on_b = cs.lww_incoming_wins(b, a)   # B holds b, receives a
    assert verdict_on_a != verdict_on_b, 'exactly one computer keeps its row'
    # and the survivor is the same row on both: the one the rule prefers
    survivor_on_a = b if verdict_on_a else a
    survivor_on_b = a if verdict_on_b else b
    assert survivor_on_a == survivor_on_b


def test_identical_content_is_no_win_either_way():
    a = _row()
    b = _row()
    assert cs.lww_incoming_wins(a, b) is False
    assert cs.lww_incoming_wins(b, a) is False
    assert cs.config_values_differ(a, b) is False


def test_tiebreak_ignores_bookkeeping_keys_that_differ_between_computers():
    local = _row(id=7, company_id=1, sync_backfill_pending=0)
    incoming = _row(id=99, company_id=5, sync_backfill_pending=1,
                    _changed_fields=['name'])
    assert cs.lww_incoming_wins(local, incoming) is False
    assert cs.lww_incoming_wins(incoming, local) is False
    assert cs.config_values_differ(local, incoming) is False


def test_tiebreak_is_spelling_independent_for_numbers():
    assert cs.lww_incoming_wins(_row(discount_pct=10), _row(discount_pct=10.0)) is False
    assert cs.lww_incoming_wins(_row(discount_pct=10.0), _row(discount_pct=10)) is False


def test_missing_version_and_stamp_never_raise():
    assert cs.lww_incoming_wins({'name': 'a'}, {'name': 'a', 'row_version': 1}) is True
    assert cs.lww_incoming_wins({'name': 'a', 'row_version': 1}, {'name': 'a'}) is False
    assert cs.lww_incoming_wins({'row_version': 1, 'updated_at_utc': None},
                                {'row_version': 1, 'updated_at_utc': 'x'}) is True


def test_a_tombstone_edit_with_a_higher_version_beats_a_stale_live_edit():
    live_stale = _row(row_version=2)
    tombstone = _row(row_version=3, deleted_at_utc='2026-10-07T11:00:00Z')
    assert cs.lww_incoming_wins(tombstone, live_stale) is False, 'a stale edit never resurrects a tombstone'
    assert cs.lww_incoming_wins(live_stale, tombstone) is True


def test_config_values_differ_only_on_shared_fields():
    assert cs.config_values_differ(_row(), _row(name='Other')) is True
    assert cs.config_values_differ(_row(), dict(_row(), extra_local_only=1)) is False


def test_every_pair_of_a_small_universe_picks_one_winner_on_full_ties():
    rows = [_row(name=n, discount_pct=p) for n, p in itertools.product('ab', (5, 10, 15))]
    for x, y in itertools.combinations(rows, 2):
        assert cs.lww_incoming_wins(x, y) != cs.lww_incoming_wins(y, x)


# ---- legacy promotion uid ------------------------------------------------

_ARGS = ('Eid sale', 10, 5, None, '2026-10-01T00:00', '2026-10-31T23:59', 'branch-uid-1')


def test_legacy_uid_is_deterministic_and_a_uuid():
    u = cs.promotion_legacy_uid(*_ARGS)
    assert u == cs.promotion_legacy_uid(*_ARGS)
    assert str(uuid.UUID(u)) == u


def test_legacy_uid_ignores_number_spelling():
    other = ('Eid sale', 10.0) + _ARGS[2:]
    assert cs.promotion_legacy_uid(*_ARGS) == cs.promotion_legacy_uid(*other)
    other = ('Eid sale', '10.000') + _ARGS[2:]
    assert cs.promotion_legacy_uid(*_ARGS) == cs.promotion_legacy_uid(*other)


def test_legacy_uid_differs_for_each_real_difference():
    base = cs.promotion_legacy_uid(*_ARGS)
    variants = [
        ('Other',) + _ARGS[1:],
        (_ARGS[0], 11) + _ARGS[2:],
        _ARGS[:2] + (6,) + _ARGS[3:],
        _ARGS[:2] + (None, 5) + _ARGS[4:],
        _ARGS[:4] + ('2026-10-02T00:00',) + _ARGS[5:],
        _ARGS[:5] + ('2026-11-01T00:00',) + _ARGS[6:],
        _ARGS[:6] + ('branch-uid-2',),
        _ARGS[:6] + ('',),
    ]
    uids = {cs.promotion_legacy_uid(*v) for v in variants}
    assert base not in uids and len(uids) == len(variants)


def test_legacy_uid_ordinal_separates_identical_copies_but_zero_is_the_plain_key():
    assert cs.promotion_legacy_uid(*_ARGS, ordinal=0) == cs.promotion_legacy_uid(*_ARGS)
    assert cs.promotion_legacy_uid(*_ARGS, ordinal=1) != cs.promotion_legacy_uid(*_ARGS)
    assert cs.promotion_legacy_uid(*_ARGS, ordinal=1) != cs.promotion_legacy_uid(*_ARGS, ordinal=2)


def test_no_branch_and_empty_branch_are_the_same():
    a = cs.promotion_legacy_uid('n', 1, None, 2, None, None, None)
    b = cs.promotion_legacy_uid('n', 1, None, 2, None, None, '')
    assert a == b
