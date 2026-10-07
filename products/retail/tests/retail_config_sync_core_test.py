"""Aura Retail -- W2b/W3a core rules for configuration sync (schema v50).

Pins `core.retail.config_sync`: the merge rule (`lww_incoming_wins`), the
"do they actually disagree" test (`config_values_differ`) and the legacy
promotion uid (`promotion_legacy_uid`).

WHAT EACH TEST GUARDS (and the mutation that must turn it RED):
  * F1 (fix round 1): the STAMP is compared FIRST, row_version second. The old
    order (rv first) let a computer that renamed a promotion 3 times (rv 4,
    OLDER stamps) beat a later deactivation (rv 2): the promotion came back ON
    on both computers and kept discounting. A later stamp wins whatever the
    counters say; equal stamps -> higher row_version; then content.
  * a missing/unparseable stamp is the OLDEST possible (never raises, never
    beats a real stamp); two such rows fall through to row_version / content.
  * `next_stamp(prev)` is strictly after `prev` even when this computer's
    clock is behind, so an edit made on top of a version always beats it.
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


def test_later_stamp_beats_higher_row_version():
    # H1: B renamed 3x (rv 4, older stamps), A deactivated later (rv 2).
    # PIN CHANGED from "higher row_version wins even against a later timestamp":
    # that was the defect. It can no longer catch the old order coming back as
    # a pure regression -- this test now asserts the opposite on purpose.
    b_side = _row(row_version=4, updated_at_utc='2026-10-07T09:00:00Z', status='active')
    a_side = _row(row_version=2, updated_at_utc='2026-10-07T10:00:00Z', status='inactive')
    assert cs.lww_incoming_wins(b_side, a_side) is True
    assert cs.lww_incoming_wins(a_side, b_side) is False


def test_equal_stamp_higher_row_version_wins():
    local = _row(row_version=2)
    incoming = _row(row_version=3, name='X')
    assert cs.lww_incoming_wins(local, incoming) is True
    assert cs.lww_incoming_wins(incoming, local) is False


def test_stamp_spellings_compare_by_instant_not_by_text():
    # '+00:00', 'Z', a space separator and fractional seconds are the SAME clock.
    z = _row(updated_at_utc='2026-10-07T10:00:00Z', row_version=1)
    plus = _row(updated_at_utc='2026-10-07T10:00:00+00:00', row_version=2, name='X')
    space = _row(updated_at_utc='2026-10-07 10:00:00', row_version=3, name='Y')
    frac = _row(updated_at_utc='2026-10-07T10:00:00.000000Z', row_version=4, name='W')
    # equal instants -> row_version decides, so the highest rv wins each time
    assert cs.lww_incoming_wins(z, plus) is True
    assert cs.lww_incoming_wins(plus, space) is True
    assert cs.lww_incoming_wins(space, frac) is True
    assert cs.lww_incoming_wins(frac, z) is False
    # a microsecond later beats a higher counter
    later = _row(updated_at_utc='2026-10-07T10:00:00.000001Z', row_version=1, name='L')
    assert cs.lww_incoming_wins(frac, later) is True
    assert cs.lww_incoming_wins(later, frac) is False


def test_missing_or_unparseable_stamp_is_oldest():
    real = _row(updated_at_utc='2026-10-07T10:00:00Z', row_version=1)
    for bad in (None, '', 'garbage', 12):
        none = _row(updated_at_utc=bad, row_version=9, name='N')
        assert cs.lww_incoming_wins(none, real) is True, bad
        assert cs.lww_incoming_wins(real, none) is False, bad
    a = _row(updated_at_utc=None, row_version=1, name='A')
    b = _row(updated_at_utc='garbage', row_version=2, name='B')
    assert cs.lww_incoming_wins(a, b) is True       # both oldest -> rv decides


def test_parse_stamp_accepts_the_shapes_the_suite_writes():
    import datetime as dt
    want = dt.datetime(2026, 10, 7, 10, 0, 0, 250000, tzinfo=dt.timezone.utc)
    for s in ('2026-10-07T10:00:00.25Z', '2026-10-07T10:00:00.250000+00:00',
              '2026-10-07 10:00:00.25'):
        assert cs.parse_stamp(s) == want, s
    assert cs.parse_stamp('2026-10-07 10:00:00') == dt.datetime(
        2026, 10, 7, 10, 0, 0, tzinfo=dt.timezone.utc)
    for bad in (None, '', 'x', 5):
        assert cs.parse_stamp(bad) is None


def test_next_stamp_is_strictly_after_a_future_previous_stamp():
    import datetime as dt
    now = dt.datetime(2026, 10, 7, 10, 0, 0, tzinfo=dt.timezone.utc)
    ahead = '2026-10-07T10:00:05.000000Z'          # this clock runs 5s behind the writer
    out = cs.next_stamp(ahead, now=now)
    assert out == '2026-10-07T10:00:05.000001Z'
    assert cs.parse_stamp(out) > cs.parse_stamp(ahead)
    # equal to now is also not enough
    same = '2026-10-07T10:00:00.000000Z'
    assert cs.parse_stamp(cs.next_stamp(same, now=now)) > cs.parse_stamp(same)


def test_next_stamp_uses_the_clock_when_it_is_ahead_and_when_no_previous():
    import datetime as dt
    now = dt.datetime(2026, 10, 7, 10, 0, 0, 123456, tzinfo=dt.timezone.utc)
    assert cs.next_stamp(None, now=now) == '2026-10-07T10:00:00.123456Z'
    assert cs.next_stamp('2026-10-07T09:00:00Z', now=now) == '2026-10-07T10:00:00.123456Z'
    assert cs.next_stamp('garbage', now=now) == '2026-10-07T10:00:00.123456Z'
    real = cs.next_stamp(None)
    assert real.endswith('Z') and cs.parse_stamp(real) is not None


def test_an_edit_on_top_of_a_version_beats_it_even_with_a_slow_clock():
    import datetime as dt
    slow_now = dt.datetime(2026, 10, 7, 9, 0, 0, tzinfo=dt.timezone.utc)
    v1 = _row(row_version=1, updated_at_utc='2026-10-07T10:00:00.000000Z')
    v2 = _row(row_version=2, name='X',
              updated_at_utc=cs.next_stamp(v1['updated_at_utc'], now=slow_now))
    assert cs.lww_incoming_wins(v1, v2) is True
    assert cs.lww_incoming_wins(v2, v1) is False


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
    # F1: an unparseable stamp is the oldest possible, i.e. EQUAL to None, so with
    # equal versions and nothing else to compare this is no win (it used to be a
    # win because the text 'x' sorted after ''). Cannot catch: a garbage stamp
    # being preferred over a missing one -- which is not a distinction worth keeping.
    assert cs.lww_incoming_wins({'row_version': 1, 'updated_at_utc': None},
                                {'row_version': 1, 'updated_at_utc': 'x'}) is False


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
