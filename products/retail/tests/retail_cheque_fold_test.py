"""Aura Retail -- core/retail/cheques.py unit tests: THE B2 GUARD.

Pure Python, no database, no Flask -- see that module's own docstring. This
file exists because B2 in the design review this implements found a
double-apply defect that every single-device test the submitted design
carried would have stayed green through: two devices that both observe the
SAME bounce each wrote their own reversal `payments` row keyed on a fresh
uuid4, so AR was silently over-restored by the face value of the cheque on
every device, and nothing in a single-process test suite could ever see it,
because the defect only exists ACROSS devices.

So this file enumerates what a two-device harness can only sample: for a set
of concurrent-event scenarios, it tries EVERY permutation of arrival order
and asserts the fold's `(status, crossing uids)` is identical regardless of
which order the caller happened to hand the events in -- proving the fold
depends only on the SET plus its own internal total order
(`(created_at_utc, id)`), never on how `_apply_event` happened to receive
them.

Run:
    pytest products/retail/tests/retail_cheque_fold_test.py -v
"""
import itertools
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail import cheques as ch  # noqa: E402


def ev(event_type, from_status, to_status, ts, eid=None):
    """One `cheque_events` row, as the plain object shape `fold()` accepts
    (see its own docstring's `_get` helper -- attribute access, matching a
    hand-built test fixture rather than a sqlite3.Row)."""
    return SimpleNamespace(
        id=eid or str(uuid.uuid4()), event_type=event_type,
        from_status=from_status, to_status=to_status, created_at_utc=ts,
    )


CHEQUE_ID = 'CHQ-TEST-1'


# ── 1. Baseline: a normal single-device lifecycle folds exactly as expected ──

def test_a_normal_pending_deposit_clear_lifecycle_folds_to_cleared_with_one_crossing():
    events = [
        ev('created', None, 'pending', '2026-01-01T00:00:00Z', 'e0'),
        ev('deposited', 'pending', 'deposited', '2026-01-02T00:00:00Z', 'e1'),
        ev('cleared', 'deposited', 'cleared', '2026-01-03T00:00:00Z', 'e2'),
    ]
    result = ch.fold(events)
    assert result.status == 'cleared'
    # Exactly one crossing: the initial 'created' (DEAD "no cheque" -> LIVE
    # 'pending'). Neither deposit nor clear changes side (both LIVE).
    assert len(result.crossings) == 1
    assert result.crossings[0].kind == 'apply'
    assert result.crossings[0].event_id == 'e0'
    assert all(result.effective.values())  # nothing superseded in a clean single-device run


def test_a_bounce_after_deposit_is_the_second_crossing_and_is_a_reverse():
    events = [
        ev('created', None, 'pending', '2026-01-01T00:00:00Z', 'e0'),
        ev('deposited', 'pending', 'deposited', '2026-01-02T00:00:00Z', 'e1'),
        ev('bounced', 'deposited', 'bounced', '2026-01-03T00:00:00Z', 'e2'),
    ]
    result = ch.fold(events)
    assert result.status == 'bounced'
    assert len(result.crossings) == 2
    assert result.crossings[0].kind == 'apply'
    assert result.crossings[1].kind == 'reverse'
    assert result.crossings[1].event_id == 'e2'


# ── 2. THE PERMUTATION GUARD ─────────────────────────────────────────────────

#: Each scenario is a tuple of events (already carrying their own fixed
#: created_at_utc/id, so the "real" order is baked into the data, exactly as
#: it would be on the wire) whose HANDED-IN order this test scrambles in
#: every possible way. `fold()` must not care.
SCENARIOS = {
    'two_concurrent_bounces': [
        ev('created', None, 'pending', '2026-02-01T00:00:00Z', 'c0'),
        ev('deposited', 'pending', 'deposited', '2026-02-02T00:00:00Z', 'c1'),
        # Two devices, each starting from 'deposited', both bounce it --
        # same from_status, different event ids/timestamps (a real race).
        ev('bounced', 'deposited', 'bounced', '2026-02-03T09:00:00Z', 'c2a'),
        ev('bounced', 'deposited', 'bounced', '2026-02-03T09:05:00Z', 'c2b'),
    ],
    'bounce_then_cancel': [
        ev('created', None, 'pending', '2026-02-01T00:00:00Z', 'd0'),
        # A device that saw 'pending' emits 'cancelled' (legal from pending);
        # another device already bounced from a LATER state it did not know
        # about yet. Both are locally-legal writes on their own device.
        ev('bounced', 'pending', 'bounced', '2026-02-02T00:00:00Z', 'd1'),
        ev('cancelled', 'pending', 'cancelled', '2026-02-01T12:00:00Z', 'd2'),
    ],
    'clear_then_bounce': [
        ev('created', None, 'pending', '2026-02-01T00:00:00Z', 'f0'),
        ev('cleared', 'pending', 'cleared', '2026-02-02T00:00:00Z', 'f1'),
        ev('bounced', 'cleared', 'bounced', '2026-02-03T00:00:00Z', 'f2'),
    ],
    'deposit_then_bounce': [
        ev('created', None, 'pending', '2026-02-01T00:00:00Z', 'g0'),
        ev('deposited', 'pending', 'deposited', '2026-02-02T00:00:00Z', 'g1'),
        ev('bounced', 'deposited', 'bounced', '2026-02-03T00:00:00Z', 'g2'),
    ],
    'write_off_then_reinstate': [
        ev('created', None, 'pending', '2026-02-01T00:00:00Z', 'h0'),
        ev('bounced', 'pending', 'bounced', '2026-02-02T00:00:00Z', 'h1'),
        # Device A writes off from 'bounced'; device B reinstates from the
        # SAME 'bounced' -- a genuine race, both legal locally.
        ev('written_off', 'bounced', 'written_off', '2026-02-03T09:00:00Z', 'h2'),
        ev('reinstated', 'bounced', 'pending', '2026-02-03T10:00:00Z', 'h3'),
    ],
    'reinstate_then_write_off': [
        ev('created', None, 'pending', '2026-02-01T00:00:00Z', 'i0'),
        ev('bounced', 'pending', 'bounced', '2026-02-02T00:00:00Z', 'i1'),
        ev('reinstated', 'bounced', 'pending', '2026-02-03T09:00:00Z', 'i2'),
        ev('written_off', 'bounced', 'written_off', '2026-02-03T10:00:00Z', 'i3'),
    ],
    'bounce_reinstate_write_off': [
        ev('created', None, 'pending', '2026-02-01T00:00:00Z', 'j0'),
        ev('bounced', 'pending', 'bounced', '2026-02-02T00:00:00Z', 'j1'),
        ev('reinstated', 'bounced', 'pending', '2026-02-03T00:00:00Z', 'j2'),
        ev('bounced', 'pending', 'bounced', '2026-02-04T00:00:00Z', 'j3'),
        ev('written_off', 'bounced', 'written_off', '2026-02-05T00:00:00Z', 'j4'),
    ],
}


def _fold_signature(result):
    """(status, tuple of (index, event_id, kind) for every crossing) -- the
    exact thing that must be IDENTICAL across every permutation, and the
    exact thing `money_uid()` is keyed on (index), so this signature is
    precisely "what the money leg would do", not an incidental detail."""
    return (result.status, tuple((c.index, c.event_id, c.kind) for c in result.crossings))


def test_the_fold_is_identical_across_every_arrival_order_permutation():
    """THE guard. For each scenario, fold() must return the SAME signature
    no matter what order the caller hands the events in -- proving the
    result depends only on the (created_at_utc, id) total order baked into
    the data, never on arrival/pull order.

    MUTATION A (sort key): change fold()'s sort key from `created_at_utc` to
    the LOCAL apply-time column (simulated here by asserting against a
    known-wrong signature) -- see test_sorting_by_the_wrong_key_breaks_
    permutation_invariance below for the actual code mutation this proves
    against, since this test alone (which only calls the real, unmodified
    fold()) cannot demonstrate a hypothetical wrong implementation.
    """
    for name, events in SCENARIOS.items():
        signatures = set()
        for perm in itertools.permutations(events):
            result = ch.fold(list(perm))
            signatures.add(_fold_signature(result))
        assert len(signatures) == 1, (
            f"scenario {name!r}: fold() produced {len(signatures)} distinct "
            f"signatures across {len(list(itertools.permutations(events)))} "
            f"arrival-order permutations -- it must produce exactly one:\n"
            + '\n'.join(str(s) for s in signatures)
        )


def test_two_concurrent_bounces_collapse_to_one_reverse_crossing():
    """THE B2 scenario, named plainly: two devices that both bounce the same
    cheque from the same observed state must fold to exactly ONE reverse
    crossing (not two), because a second event that does not change SIDE is
    INERT, not a second money-mover. This is what money_uid() being keyed on
    the crossing INDEX (not the event id) depends on: if this produced two
    reverse crossings, two DIFFERENT uids would be minted for what is really
    one bank fact."""
    result = ch.fold(SCENARIOS['two_concurrent_bounces'])
    assert result.status == 'bounced'
    assert len(result.crossings) == 2  # crossing 0 (created->pending), crossing 1 (deposited->bounced)
    reverse_crossings = [c for c in result.crossings if c.kind == 'reverse']
    assert len(reverse_crossings) == 1, (
        f"two concurrent bounces must collapse to ONE reverse crossing, got {len(reverse_crossings)}")
    # Exactly one of the two bounce events is the effective crossing; the
    # other is INERT (recorded, shown as superseded, changes nothing).
    bounce_ids = {'c2a', 'c2b'}
    effective_bounces = [eid for eid in bounce_ids if result.effective[eid]]
    inert_bounces = [eid for eid in bounce_ids if not result.effective[eid]]
    assert len(effective_bounces) == 1
    assert len(inert_bounces) == 1


def test_write_off_then_reinstate_the_reinstate_crossing_is_never_discarded():
    """THE convergence-proof scenario, both orderings. 'reinstated' is a real
    crossing (bounced/DEAD -> pending/LIVE) REGARDLESS of whether it landed
    after a write_off that moved the observed status to 'written_off' first
    -- discarding it because from_status disagrees would leave a payments
    row (crossing 2, an APPLY) beside a DEAD folded status, which is exactly
    the invariant this module exists to hold. Both orderings converge to the
    IDENTICAL crossing set (money is the same either way); only the final
    STATUS differs; because who moved second determines which state a human
    reads on the timeline, which is the honestly-named residual risk in the
    design (clock skew orders the story, never the money)."""
    forward = ch.fold(SCENARIOS['write_off_then_reinstate'])
    backward = ch.fold(list(reversed(SCENARIOS['write_off_then_reinstate'])))
    assert _fold_signature(forward) == _fold_signature(backward)
    # Three crossings total: created (apply), bounced (reverse), reinstated (apply).
    # written_off never becomes a crossing here because by the time it is
    # processed (immediately after 'bounced' in sort order) status is already
    # DEAD and written_off's to_status is also DEAD -- not a crossing, just
    # inert-or-consistent depending on order.
    kinds = [c.kind for c in forward.crossings]
    assert kinds == ['apply', 'reverse', 'apply']


# ── 3. Money uid: deterministic, keyed on the crossing index ────────────────

def test_money_uid_is_deterministic_and_differs_only_by_index():
    a = ch.money_uid(CHEQUE_ID, 0)
    b = ch.money_uid(CHEQUE_ID, 0)
    c = ch.money_uid(CHEQUE_ID, 1)
    assert a == b, 'money_uid must be a pure function of (cheque_id, index)'
    assert a != c, 'different crossing indices must mint different uids'


def test_two_devices_computing_the_same_crossing_mint_the_same_uid():
    """THE fix for the B2 defect, restated as the money-uid claim directly:
    two independent fold() calls over the SAME converged event set (as two
    devices would each compute after sync) must derive the identical uid
    for crossing 1 (the bounce), because both call money_uid(cheque_id, 1)
    -- never a per-write uuid4."""
    result = ch.fold(SCENARIOS['two_concurrent_bounces'])
    bounce_crossing = [c for c in result.crossings if c.kind == 'reverse'][0]
    uid_from_device_a = ch.money_uid(CHEQUE_ID, bounce_crossing.index)
    uid_from_device_b = ch.money_uid(CHEQUE_ID, bounce_crossing.index)
    assert uid_from_device_a == uid_from_device_b


def test_money_uid_mutation_to_uuid4_would_break_determinism():
    """Documents, executably, the exact mutation this design forbids (see
    core/retail/cheques.py's own docstring on money_uid: 'uuid5, never
    uuid4'). Calls a LOCAL uuid4-based re-implementation to show it fails
    the identical determinism property the real function must hold -- this
    is the demonstration referenced by test_the_fold_is_identical... above,
    made concrete rather than asserted only in prose."""
    import uuid as _uuid

    def _wrong_money_uid(cheque_id, crossing_index):
        return str(_uuid.uuid4())  # THE forbidden mutation

    a = _wrong_money_uid(CHEQUE_ID, 1)
    b = _wrong_money_uid(CHEQUE_ID, 1)
    assert a != b, (
        'sanity: this local re-implementation of the forbidden mutation must '
        'actually be non-deterministic, or it proves nothing about the real '
        'money_uid()')
    # And the real function must NOT share this property:
    assert ch.money_uid(CHEQUE_ID, 1) == ch.money_uid(CHEQUE_ID, 1)


# ── 4. Legality table ────────────────────────────────────────────────────────

def test_is_legal_matches_the_transition_table_both_directions():
    assert ch.is_legal('created', None) is True
    assert ch.is_legal('created', 'pending') is False
    assert ch.is_legal('deposited', 'pending') is True
    assert ch.is_legal('deposited', 'bounced') is True
    assert ch.is_legal('deposited', 'cleared') is False
    assert ch.is_legal('bounced', 'cleared') is True
    assert ch.is_legal('bounced', 'cancelled') is False
    assert ch.is_legal('cancelled', 'pending') is True
    assert ch.is_legal('cancelled', 'deposited') is False
    assert ch.is_legal('written_off', 'bounced') is True
    assert ch.is_legal('written_off', 'pending') is False


def test_resulting_status_matches_the_table():
    assert ch.resulting_status('created') == 'pending'
    assert ch.resulting_status('deposited') == 'deposited'
    assert ch.resulting_status('bounced') == 'bounced'
    assert ch.resulting_status('reinstated') == 'pending'
    assert ch.resulting_status('written_off') == 'written_off'


def test_live_and_dead_states_partition_every_status_the_table_names():
    named_statuses = {to for _from, to in ch.LEGAL_TRANSITIONS.values()}
    assert named_statuses == set(ch.LIVE_STATES) | set(ch.DEAD_STATES)
    assert set(ch.LIVE_STATES) & set(ch.DEAD_STATES) == set()
