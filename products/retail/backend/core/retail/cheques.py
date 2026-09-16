"""
Aura Retail -- Aseel-parity wave A-PAR, "cheque lifecycle as a tracked
instrument" (schema v32). Pure functions only: no database access, no
Flask -- matching promotions.py/po_split.py/pricing.py in this package.
Imported by api/retail_api.py only; commercial_runtime/sync/sync_service.py
imports NOTHING from this module and contains no cheque business logic at
all (see that module's own `_resolve_branch_id` docstring for why it must
never carry one product's semantics).

THE ONE INVARIANT THIS MODULE EXISTS TO PROTECT: a cheque in a LIVE state
(pending/deposited/cleared/endorsed) has had its face value applied to the
party's balance; a cheque in a DEAD state (bounced/cancelled/written_off)
has not. Money moves at RECEIPT, not at clearing -- a shop handed a
post-dated cheque considers itself paid, and a bounce puts the money back,
loudly, dated the day it bounced (a NEW opposite ledger row, never a
retroactive void -- see api/retail_api.py's cheque routes for why).

WHY STATUS IS NOT A STORED COLUMN, AND WHY THAT IS WHAT MAKES THIS
CONVERGE. See database/schema.py's RETAIL_SCHEMA_VERSION v32 comment for
the full story of the design this replaced (a mutable `status` column,
row_version-gated the `reorder_requests` way) and exactly how it failed:
two devices that both observe the same bounce both write row_version=2 from
the same base, satisfy neither device's strictly-greater gate, and BOTH
keep believing their own status forever while BOTH have written a money
leg keyed on a fresh uuid4 -- AR silently over-restored by the face value
of the cheque. The fix here is structural, not a better gate: `cheques`
(the header) and `cheque_events` (this module's input) are BOTH append-only
sets. Two append-only sets converge by plain set union, with no version to
compare and nothing to reject as stale. `status` becomes a PURE FOLD over
the event set, computed identically by every device that holds the same
set -- which is exactly what `fold()` below is.

THE CONVERGENCE PROOF, why a crossing is never discarded even when it
disagrees with the locally-observed `from_status`:

Crossings strictly ALTERNATE. From a LIVE state the only transition that
changes side is ->DEAD; from a DEAD state the only one is ->LIVE. So
crossing-index PARITY determines the leg (even index = APPLY, odd =
REVERSE) and the amount is always the cheque's face value -- two devices
computing the same index therefore always mean the same money row, which
is what makes `money_uid()` below safe to key on the index alone.

Effective crossings NEVER CHANGE INDEX once a payment has been written for
them. Inserting a non-crossing event into the ordered set cannot move any
crossing (a non-crossing does not change the running side, and side is
what decides whether a LATER crossing crosses). Inserting a CROSSING
between crossings i and i+1 flips the running side, so crossing i+1 now
targets the side it is already ON and becomes INERT -- the newly inserted
event takes index i+1, and crossing i+2 (which targets the opposite side
again) still applies at index i+2. Exactly one following crossing is
consumed; the count and every later index are unchanged. If there is no
following crossing at all, the count simply grows by one and no index
moves. Therefore a `payments` row already written for crossing k is ALWAYS
the row the converged fold wants at index k, on every device, forever --
no reconciler, no compensating void, ever required.

This is why an event whose `from_status` does not match the fold's current
status is not simply discarded: it may still be a REAL crossing (a fact
about what the bank did), and admitting it is what keeps a `payments` row
some device already wrote consistent with the converged status. Discarding
it instead would leave a re-applied money leg sitting beside a status that
says the cheque is DEAD -- the exact invariant this module exists to hold.
An event that is neither locally-consistent NOR a crossing is INERT: it is
kept in the record (so a two-device race is visible on the timeline as
"superseded", never silently dropped) but changes nothing.

ORDER: `(created_at_utc, id)`, both carried VERBATIM on the wire by every
writer and by every sync apply branch -- never re-derived at apply time.
`id` is a client-generated UUID, so ties on `created_at_utc` still sort
identically everywhere. Stated honestly: clock skew between two devices can
order two near-simultaneous events in a business-WRONG sequence, but never
in a DIFFERENT sequence on two different devices -- which is all
convergence requires, and is a named residual risk, not a defect (see
ROADMAP.md's A-PAR wave entry: a Lamport/HLC stamp is the eventual, fleet-
wide answer, and is deliberately out of scope for this module).
"""
import uuid

#: A cheque in any of these states has had its face value APPLIED to the
#: party's balance (money already moved in the shop's favour, or already
#: paid out to a supplier).
LIVE_STATES = ('pending', 'deposited', 'cleared', 'endorsed')

#: A cheque in any of these states has NOT -- either it never crossed, or a
#: prior crossing was reversed.
DEAD_STATES = ('bounced', 'cancelled', 'written_off')

#: THE one legality table -- consumed by both the write routes (to refuse an
#: illegal transition before anything is written) and by `fold()` (to decide
#: whether an out-of-order event is a real crossing). One place, not two --
#: see this module's own docstring for why a second copy inside
#: sync_service.py would be the exact mistake `_resolve_branch_id`'s own
#: docstring warns against.
#:
#: event_type -> (legal FROM statuses (None means "no cheque exists yet"),
#:                 the resulting TO status)
LEGAL_TRANSITIONS = {
    'created':     (None,                                            'pending'),
    'deposited':   (('pending', 'bounced'),                          'deposited'),
    'cleared':     (('pending', 'deposited'),                        'cleared'),
    'bounced':     (('pending', 'deposited', 'cleared', 'endorsed'), 'bounced'),
    'reinstated':  (('bounced',),                                    'pending'),
    'endorsed':    (('pending',),                                    'endorsed'),
    'cancelled':   (('pending',),                                    'cancelled'),
    'written_off': (('bounced',),                                    'written_off'),
}

#: Fixed namespace UUID for `money_uid()` below. NEVER CHANGE THIS VALUE --
#: doing so re-mints a different uid for every crossing that has ever
#: happened on every device in the fleet, which defeats the
#: `ON CONFLICT(uid) DO NOTHING` dedupe this whole design leans on and would
#: let a bounce that already reversed once reverse again on every device
#: that re-derives its uid under the new namespace. Generated once via
#: `uuid.uuid4()` and frozen here as a literal, the same posture this
#: product already takes for `_RETAIL_SETTING_SYNC_NAMESPACE` in
#: api/retail_api.py.
_CHEQUE_MONEY_NAMESPACE = uuid.UUID('5468d3cc-24c6-485a-8084-7062aa011815')


def money_uid(cheque_id, crossing_index):
    """The `payments.uid` for the money leg of crossing `crossing_index` of
    `cheque_id` -- a DETERMINISTIC function of which crossing this is, never
    a fresh random value per write. Two devices that each observe the SAME
    bank fact (the same cheque crossing the live/dead line for the Nth time)
    compute the IDENTICAL uid, and the pre-existing
    `ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING` on the `payment`
    sync-apply branch (commercial_runtime/sync/sync_service.py) collapses
    the duplicate row to one, everywhere -- including on the two
    originating devices themselves. This is the fix for the double-apply an
    earlier revision of this design shipped (see this module's own
    docstring): that revision minted a fresh `uuid4()` per write, so two
    devices bouncing the same cheque wrote two DIFFERENT, both-genuine
    `payments` rows and over-restored AR by the face value.

    `uuid5`, never `uuid4` -- this is the single most important line in this
    module. Swapping it back to a random uid silently reopens the exact
    defect this design exists to close, and would still pass every
    single-device test, because the duplication only appears the moment two
    DIFFERENT devices compute the SAME crossing independently."""
    return str(uuid.uuid5(_CHEQUE_MONEY_NAMESPACE, f"{cheque_id}:{crossing_index}"))


class Crossing:
    """One EFFECTIVE, side-changing event in a folded cheque's history.

    `index` is what `money_uid()` is keyed on; `kind` is 'apply' (DEAD->LIVE,
    face value moves onto the party's balance) or 'reverse' (LIVE->DEAD, the
    face value moves back). Crossing 0 is always the cheque's own `created`
    event (the "no cheque yet" state is DEAD by convention -> LIVE
    'pending'), so parity alone tells a caller which leg of THE MONEY LEG
    table (api/retail_api.py's write routes) applies, with no extra state to
    carry."""
    __slots__ = ('index', 'event_id', 'kind')

    def __init__(self, index, event_id, kind):
        self.index = index
        self.event_id = event_id
        self.kind = kind

    def __repr__(self):  # pragma: no cover - diagnostics only
        return f"Crossing(index={self.index}, event_id={self.event_id!r}, kind={self.kind!r})"

    def __eq__(self, other):
        return (isinstance(other, Crossing) and self.index == other.index
                and self.event_id == other.event_id and self.kind == other.kind)

    def __hash__(self):
        return hash((self.index, self.event_id, self.kind))


class FoldResult:
    """The output of `fold()`. `effective` and `crossing_index` are keyed by
    event id so a caller (GET /cheques/<id>'s timeline) can annotate every
    event in the ORIGINAL list it folded, including the INERT ones -- an
    inert event is never dropped from the record, only marked as not having
    changed anything (rendered "superseded -- recorded on another device")."""
    __slots__ = ('status', 'crossings', 'effective', 'crossing_index')

    def __init__(self, status, crossings, effective, crossing_index):
        self.status = status
        self.crossings = crossings
        self.effective = effective
        self.crossing_index = crossing_index

    @property
    def is_live(self):
        return self.status in LIVE_STATES


def _side(status):
    """LIVE if `status` names a LIVE state, DEAD otherwise -- including for
    `None` (the "no cheque exists yet" state before the `created` event),
    which is deliberately DEAD: the `created` event's own to_status
    ('pending') is LIVE, so treating the pre-cheque state as DEAD is what
    makes `created` fold as crossing 0, exactly like every other transition
    that first brings a cheque's money onto the books."""
    return 'LIVE' if status in LIVE_STATES else 'DEAD'


def _get(obj, key):
    """Reads `key` off a mapping (sqlite3.Row, dict) or an attribute-bearing
    object -- `fold()`'s only concession to not knowing which shape its
    caller hands it. sqlite3.Row does not support `.get()`, so this tries
    subscript access first (covers dict and sqlite3.Row) and falls back to
    getattr (covers a plain object/namedtuple, used by the unit tests)."""
    try:
        return obj[key]
    except (TypeError, IndexError, KeyError):
        return getattr(obj, key, None)


def fold(events):
    """Computes the converged status of one cheque from its FULL, APPEND-
    ONLY event set. Never touches a database; `events` is an iterable of
    objects (or mappings) each exposing `id`/`from_status`/`to_status`/
    `created_at_utc` -- rows already read from `cheque_events`, in ANY
    order. See this module's own docstring for the convergence proof this
    function implements; only the mechanics are documented here.

    Sorts by `(created_at_utc, id)` FIRST -- the fold never trusts the order
    `events` arrived in, which is what makes its result independent of pull
    order / arrival order across devices (see the docstring's ORDER
    paragraph). `events` is not mutated; a new sorted list is built.

    Returns a `FoldResult`. `effective`/`crossing_index` are dicts keyed by
    event id so every input event -- including an INERT one -- gets an
    answer; an id absent from `effective` never happens, because every event
    in the input is classified one way or the other.
    """
    ordered = sorted(events, key=lambda e: (_get(e, 'created_at_utc') or '', _get(e, 'id')))

    status = None  # "no cheque exists yet" -- see _side()'s own docstring
    crossings = []
    effective = {}
    crossing_index = {}

    for ev in ordered:
        eid = _get(ev, 'id')
        ev_from = _get(ev, 'from_status')
        ev_to = _get(ev, 'to_status')
        side_now = _side(status)
        side_to = _side(ev_to)
        is_crossing = side_to != side_now
        # EFFECTIVE when either (a) this event's writer observed exactly the
        # status the fold has ALSO arrived at (the ordinary, no-race case),
        # or (b) it is a real crossing regardless of (a) -- a crossing is a
        # claim about what the BANK did, and a bank fact is never discarded
        # just because it disagrees with which local status wrote it (see
        # the module docstring's convergence proof for why this is
        # required, not merely permissive).
        is_effective = (ev_from == status) or is_crossing
        effective[eid] = is_effective
        if is_effective:
            if is_crossing:
                kind = 'apply' if side_to == 'LIVE' else 'reverse'
                idx = len(crossings)
                crossings.append(Crossing(idx, eid, kind))
                crossing_index[eid] = idx
            else:
                crossing_index[eid] = None
            status = ev_to
        else:
            crossing_index[eid] = None

    return FoldResult(status=status, crossings=tuple(crossings),
                       effective=effective, crossing_index=crossing_index)


def next_crossing_index(events):
    """Convenience for the write routes: `len(fold(events).crossings)` --
    the crossing index a NEW event would receive if it turns out to be a
    crossing, computed from the events that exist BEFORE this write. See
    api/retail_api.py's write-route body for why this is safe to compute
    once, up front, rather than re-folding after the write: a freshly
    written event's `from_status` is always exactly the fold's own current
    `status` (the route reads it from the very same fold before deciding
    legality), so it is always EFFECTIVE, and the convergence proof in this
    module's docstring guarantees its index never moves later even if
    another device's event is later found to sort before it."""
    return len(fold(events).crossings)


def is_legal(event_type, current_status):
    """True when `event_type` may be applied to a cheque currently at
    `current_status` (as `fold()` computes it) -- the ONE check both the
    write routes and the tests in
    products/retail/tests/retail_cheque_lifecycle_test.py exercise.
    `current_status` is `None` only for `event_type='created'` on a cheque
    that does not exist yet; every other event_type is checked against an
    existing cheque's folded status."""
    if event_type not in LEGAL_TRANSITIONS:
        return False
    legal_from, _to = LEGAL_TRANSITIONS[event_type]
    if legal_from is None:
        return current_status is None
    return current_status in legal_from


def resulting_status(event_type):
    """The `to_status` LEGAL_TRANSITIONS records for `event_type` -- a tiny
    accessor so callers never index the tuple by position."""
    return LEGAL_TRANSITIONS[event_type][1]
