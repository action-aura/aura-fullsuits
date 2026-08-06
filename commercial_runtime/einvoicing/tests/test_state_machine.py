import itertools

from commercial_runtime.einvoicing.outbox import (
    STATES,
    TERMINAL_STATES,
    is_legal_transition,
)

EXPECTED_LEGAL = {
    ('QUEUED', 'SUBMITTING'),
    ('SUBMITTING', 'CLEARED'),
    ('SUBMITTING', 'FAILED_PERMANENT'),
    ('SUBMITTING', 'QUEUED'),
    ('SUBMITTING', 'AWAITING_CLEARANCE'),
    ('SUBMITTING', 'SUBMITTING_UNKNOWN'),
    ('AWAITING_CLEARANCE', 'CLEARED'),
    ('AWAITING_CLEARANCE', 'FAILED_PERMANENT'),
    ('SUBMITTING_UNKNOWN', 'CLEARED'),
    ('SUBMITTING_UNKNOWN', 'QUEUED'),
} | {(state, 'CANCELLED') for state in STATES - TERMINAL_STATES}


def test_full_transition_matrix_matches_exactly():
    """Exhaustive: every (from, to) pair over the full state set must match
    the expected legal set exactly -- nothing legal is missing, nothing
    illegal sneaks through."""
    for from_state, to_state in itertools.product(STATES, STATES):
        expected = (from_state, to_state) in EXPECTED_LEGAL
        actual = is_legal_transition(from_state, to_state)
        assert actual == expected, f"{from_state} -> {to_state}: expected {expected}, got {actual}"


def test_no_self_transitions_are_legal():
    for state in STATES:
        assert not is_legal_transition(state, state), f"{state} -> {state} must not be legal"


def test_terminal_states_have_no_outgoing_transitions():
    for terminal in TERMINAL_STATES:
        for other in STATES:
            assert not is_legal_transition(terminal, other), f"{terminal} is terminal, must have no outgoing transitions"


def test_every_non_terminal_state_can_reach_cancelled():
    for state in STATES - TERMINAL_STATES:
        assert is_legal_transition(state, 'CANCELLED')


def test_cleared_and_failed_permanent_and_cancelled_are_terminal():
    assert TERMINAL_STATES == {'CLEARED', 'FAILED_PERMANENT', 'CANCELLED'}


def test_unknown_never_transitions_directly_to_failed_permanent():
    """The anti-double-submission rule's other face: UNKNOWN must be
    resolved via CLEARED or QUEUED (a real check_status answer), never
    silently written off as failed without confirmation."""
    assert not is_legal_transition('SUBMITTING_UNKNOWN', 'FAILED_PERMANENT')


def test_awaiting_clearance_never_goes_back_to_queued():
    """Once accepted-but-pending, only a poll resolving cleared/rejected
    moves it -- it must not silently return to the claimable pool."""
    assert not is_legal_transition('AWAITING_CLEARANCE', 'QUEUED')
