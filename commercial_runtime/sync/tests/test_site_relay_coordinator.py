"""Tests for `commercial_runtime.sync.site_relay.coordinator` -- the driver
that reads `election.decide_role`'s answer every tick and actually starts,
stops, or joins a relay. See `coordinator.py`'s own module docstring for the
full design; this file mirrors `test_site_relay_election.py`'s own stated
conventions exactly (pure unit tests, no sockets, no real threads, no real
clock -- everything the coordinator would otherwise touch is a hand-rolled
fake/spy that records what it was called with).

Every test drives `tick()` directly, never `start()`/`stop()` -- that split
is the entire point of `coordinator.py`'s design (see its docstring's last
paragraph). `start()`/`stop()` are a thin, untested-here wrapper around
calling `tick()` on a loop; there is nothing about a real background thread
worth pinning down in a pure unit test, and doing so would just make these
tests flaky for no signal gained.

MUTATION PROOFS (see this task's own report for the verbatim before/after
output): three specific protections are proven load-bearing by temporarily
breaking the exact thing they guard and watching the corresponding test go
RED, then restoring it and watching it go GREEN again --

    1. `test_own_beacon_echoed_back_does_not_trigger_self_stand_down` --
       proven by removing the self-beacon filter in
       `SiteRelayCoordinator._merge_observed_peers` (i.e. letting a pointer
       whose `installation_id` equals this device's own reach `_peers`
       unfiltered).
    2. `test_established_hub_stands_down_and_stops_relay_when_lower_id_peer_appears`
       -- proven by inverting `election.should_stand_down`'s comparison
       from `<` to `>` (the same mutation `test_site_relay_election.py`
       itself uses for its own analogous proof, exercised here through the
       coordinator instead of the bare function).
    3. `test_idempotent_hub_ticks_start_the_relay_exactly_once` -- proven by
       making `SiteRelayCoordinator.tick`'s HUB branch call `start_relay()`
       unconditionally instead of only when `self._relay_handle is None`.

Run:
    /c/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \\
        commercial_runtime/sync/tests/test_site_relay_coordinator.py -q --color=no
"""
from __future__ import annotations

from typing import List, Optional

from commercial_runtime.sync.site_relay.coordinator import SiteRelayCoordinator
from commercial_runtime.sync.site_relay.election import (
    HUB_LISTEN_SECONDS,
    HUB_SILENCE_SECONDS,
    Role,
)

# Reused verbatim from test_site_relay_election.py so the two files agree on
# which id is "lower"/"higher" than which at a glance.
MY_ID = "installation-aaaa"
LOWER_ID = "installation-0001"  # sorts below MY_ID
HIGHER_ID = "installation-zzzz"  # sorts above MY_ID


def _pointer(installation_id: str, *, url: str = "https://10.0.0.5:8743", spki_pin: str = "pin==") -> dict:
    """The raw pointer shape `autojoin._extract_beacon_pointer` (and this
    module's own `_default_observe_beacons`) produces -- see coordinator.py's
    module docstring for why `observe_beacons` returns THIS shape rather
    than an `election.PeerBeacon` directly (a `PeerBeacon` also carries
    `seen_at`, which is the COORDINATOR's job to stamp with its own clock at
    observation time, never the listener's)."""
    return {"installation_id": installation_id, "url": url, "spki_pin": spki_pin}


class FakeClock:
    """Injected `monotonic` seam. Starts at `start` and only ever moves when
    a test calls `.advance()` -- exactly like `election.py`'s own tests
    choosing plain numbers for `now`/`started_at`, except here the numbers
    are read by the coordinator's OWN clock calls (`_started_at` at
    construction, `now` on every `tick()`) rather than passed as function
    arguments directly, since `SiteRelayCoordinator` owns its own clock
    reads instead of taking `now` as a per-call parameter the way
    `decide_role` does."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class BeaconFeed:
    """Injected `observe_beacons` seam: a scripted queue of results, one
    list per `tick()` call. Records every `timeout_seconds` it was called
    with (unused by most tests, but there for anything that wants to assert
    the coordinator actually asked for a listen window). Once the queue is
    exhausted, further calls return `[]` -- "no beacon heard this tick" is
    the overwhelmingly common steady-state case and a test that scripted
    fewer ticks than it ends up calling should not blow up over it."""

    def __init__(self, *results: List[dict]) -> None:
        self._queue = list(results)
        self.calls: List[float] = []

    def __call__(self, timeout_seconds: float) -> List[dict]:
        self.calls.append(timeout_seconds)
        if self._queue:
            return self._queue.pop(0)
        return []


class RelaySpy:
    """Injected `start_relay`/`stop_relay` seam pair. `start` hands back a
    distinct handle per call (`"handle-1"`, `"handle-2"`, ...) so a test can
    assert exactly WHICH handle `stop_relay` was later called with, proving
    the coordinator passed back the same object it was given rather than
    some other value. `fail_times` lets a test make the first N calls to
    `start()` raise, then succeed -- the seam used by the never-throws
    mutation proof below."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.start_calls = 0
        self.stop_calls: List[object] = []
        self._fail_times = fail_times

    def start(self) -> object:
        self.start_calls += 1
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("simulated relay start failure")
        return f"handle-{self.start_calls}"

    def stop(self, handle: object) -> None:
        self.stop_calls.append(handle)


class JoinSpy:
    """Injected `attempt_join` seam: a zero-argument callable that just
    counts how many times it ran."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


def _coordinator(
    *,
    clock: Optional[FakeClock] = None,
    beacons: Optional[BeaconFeed] = None,
    relay: Optional[RelaySpy] = None,
    join: Optional[JoinSpy] = None,
) -> SiteRelayCoordinator:
    """Builds one coordinator with sane defaults for whichever seams a test
    does not care about, so each test only names the fakes it actually
    inspects."""
    clock = clock if clock is not None else FakeClock()
    beacons = beacons if beacons is not None else BeaconFeed()
    relay = relay if relay is not None else RelaySpy()
    join = join if join is not None else JoinSpy()
    return SiteRelayCoordinator(
        my_installation_id=MY_ID,
        start_relay=relay.start,
        stop_relay=relay.stop,
        attempt_join=join,
        observe_beacons=beacons,
        monotonic=clock,
    )


# ── 1. A lone till: LISTENING during the window, HUB after it elapses ──────

def test_lone_till_is_listening_then_becomes_hub_after_the_window():
    clock = FakeClock()
    relay = RelaySpy()
    coordinator = _coordinator(clock=clock, beacons=BeaconFeed([], []), relay=relay)

    role = coordinator.tick()
    assert role is Role.LISTENING
    assert relay.start_calls == 0

    clock.advance(HUB_LISTEN_SECONDS + 1.0)
    role = coordinator.tick()
    assert role is Role.HUB
    assert relay.start_calls == 1


# ── 2. A live peer heard while LISTENING -> CLIENT, relay never started ────

def test_live_peer_while_listening_becomes_client_without_starting_relay():
    relay = RelaySpy()
    join = JoinSpy()
    coordinator = _coordinator(beacons=BeaconFeed([_pointer(HIGHER_ID)]), relay=relay, join=join)

    role = coordinator.tick()

    assert role is Role.CLIENT
    assert relay.start_calls == 0
    assert join.calls == 1


# ── 3. Established HUB + a LOWER-id peer -> CLIENT, relay actually stopped ─

def test_established_hub_stands_down_and_stops_relay_when_lower_id_peer_appears():
    clock = FakeClock()
    relay = RelaySpy()
    join = JoinSpy()
    coordinator = _coordinator(
        clock=clock, beacons=BeaconFeed([], [_pointer(LOWER_ID)]), relay=relay, join=join
    )

    clock.advance(HUB_LISTEN_SECONDS + 1.0)
    role = coordinator.tick()
    assert role is Role.HUB
    assert relay.start_calls == 1
    assert relay.stop_calls == []

    clock.advance(1.0)
    role = coordinator.tick()

    assert role is Role.CLIENT
    assert relay.stop_calls == ["handle-1"]
    assert join.calls == 1


# ── 4. Established HUB + a HIGHER-id peer -> stays HUB, relay NOT stopped ──

def test_established_hub_stays_hub_when_only_higher_id_peers_are_heard():
    clock = FakeClock()
    relay = RelaySpy()
    join = JoinSpy()
    coordinator = _coordinator(
        clock=clock, beacons=BeaconFeed([], [_pointer(HIGHER_ID)]), relay=relay, join=join
    )

    clock.advance(HUB_LISTEN_SECONDS + 1.0)
    assert coordinator.tick() is Role.HUB

    clock.advance(1.0)
    role = coordinator.tick()

    assert role is Role.HUB
    assert relay.stop_calls == []
    assert join.calls == 0


# ── 5. Own beacon echoed back -> still HUB (THE self-beacon bug) ───────────

def test_own_beacon_echoed_back_does_not_trigger_self_stand_down():
    """THE BUG THIS GUARDS AGAINST: `beacon.BeaconBroadcaster` sends to the
    LAN broadcast address, which this same machine can receive back.

    Note WHY this must be tested with the device still LISTENING, not only
    once it is already HUB: `election.should_stand_down`'s `<` comparison
    happens to be immune to a self-match on its own (a string is never `<`
    itself, so an unfiltered self-echo heard while already HUB would not by
    itself trigger a stand-down through THAT specific comparison). The
    branch the self-beacon filter actually protects is a DIFFERENT one --
    `decide_role`'s "not already HUB, and a live peer exists at all -> CLIENT"
    rule, which does not compare ids, it only checks whether `peers` is
    non-empty. An unfiltered self-echo heard while this device is still
    LISTENING (tick 1, below) would satisfy that non-emptiness check purely
    because it heard SOMETHING calling itself a beacon -- even though the
    "peer" is itself -- and would wrongly flip this device to CLIENT before
    it ever gets the chance to become HUB at all. THIS is "the shop breaks
    and the symptom is silent" the task instructions describe: a till that
    can hear its own echo would never become a hub, silently, forever.

    So: tick 1 feeds the coordinator its own beacon while still inside the
    listen window and asserts it STAYS LISTENING (not CLIENT); tick 2 feeds
    it again after the window elapses and asserts it becomes HUB; tick 3
    feeds it a third time, now that it legitimately IS broadcasting as HUB
    (the realistic case), and asserts it stays HUB. All three assertions
    exercise the filter; the mutation proof in this task's report removes
    the filter and shows tick 1 alone is enough to go red."""
    clock = FakeClock()
    relay = RelaySpy()
    coordinator = _coordinator(
        clock=clock,
        beacons=BeaconFeed([_pointer(MY_ID)], [_pointer(MY_ID)], [_pointer(MY_ID)]),
        relay=relay,
    )

    # Tick 1: still inside the listen window. An unfiltered self-echo would
    # wrongly satisfy decide_role's "any live peer -> CLIENT" rule here.
    role = coordinator.tick()
    assert role is Role.LISTENING
    assert relay.start_calls == 0

    # Tick 2: window elapsed, own beacon heard again -> becomes HUB.
    clock.advance(HUB_LISTEN_SECONDS + 1.0)
    role = coordinator.tick()
    assert role is Role.HUB
    assert relay.start_calls == 1

    # Tick 3: the realistic case -- now actually HUB and hearing its own
    # broadcast reflected back -- stays HUB, never stands down, never
    # re-starts the relay.
    clock.advance(1.0)
    role = coordinator.tick()
    assert role is Role.HUB
    assert relay.stop_calls == []
    assert relay.start_calls == 1  # never re-started either


# ── 6. A peer gone silent past HUB_SILENCE_SECONDS stops counting ──────────

def test_peer_gone_silent_past_hub_silence_window_stops_counting():
    clock = FakeClock()
    relay = RelaySpy()
    coordinator = _coordinator(
        clock=clock, beacons=BeaconFeed([_pointer(HIGHER_ID)], []), relay=relay
    )

    # Heard once -- a live peer -> CLIENT, regardless of the listen window.
    role = coordinator.tick()
    assert role is Role.CLIENT
    assert relay.start_calls == 0

    # The peer never broadcasts again. Once HUB_SILENCE_SECONDS has passed
    # since it was last heard, `election.prune` (applied inside `decide_role`
    # on every tick) drops it -- it "stops counting" as a live peer, and
    # with no live peer and the listen window long since elapsed, this
    # device claims HUB for itself.
    clock.advance(HUB_SILENCE_SECONDS + 1.0)
    role = coordinator.tick()

    assert role is Role.HUB
    assert relay.start_calls == 1


# ── 7. Idempotence: several HUB ticks in a row start the relay exactly ONCE ─

def test_idempotent_hub_ticks_start_the_relay_exactly_once():
    clock = FakeClock()
    relay = RelaySpy()
    coordinator = _coordinator(
        clock=clock, beacons=BeaconFeed([], [], [], []), relay=relay
    )

    clock.advance(HUB_LISTEN_SECONDS + 1.0)
    for _ in range(4):
        role = coordinator.tick()
        assert role is Role.HUB
        clock.advance(1.0)

    assert relay.start_calls == 1


# ── 8. CLIENT ticks call the join seam; HUB and LISTENING ticks never do ───

def test_join_seam_is_called_only_on_client_ticks():
    # LISTENING: no peer, still inside the listen window.
    listening_join = JoinSpy()
    listening_coordinator = _coordinator(
        beacons=BeaconFeed([]), join=listening_join,
    )
    assert listening_coordinator.tick() is Role.LISTENING
    assert listening_join.calls == 0

    # HUB: no peer, and the listen window has already elapsed.
    hub_clock = FakeClock()
    hub_join = JoinSpy()
    hub_coordinator = _coordinator(clock=hub_clock, beacons=BeaconFeed([]), join=hub_join)
    hub_clock.advance(HUB_LISTEN_SECONDS + 1.0)
    assert hub_coordinator.tick() is Role.HUB
    assert hub_join.calls == 0

    # CLIENT: a live peer is heard.
    client_join = JoinSpy()
    client_coordinator = _coordinator(beacons=BeaconFeed([_pointer(HIGHER_ID)]), join=client_join)
    assert client_coordinator.tick() is Role.CLIENT
    assert client_join.calls == 1


# ── 9. A seam that raises does not kill the loop ────────────────────────────

def test_a_raising_seam_does_not_kill_the_tick_loop():
    """`RelaySpy(fail_times=1)` makes the FIRST `start_relay()` call raise
    and every call after it succeed. `tick()` must swallow that exception
    (never propagate it -- see coordinator.py's "NEVER THROWS" contract) and
    leave `_role` uncommitted so the very next tick retries the whole
    decision fresh, exactly as the module docstring describes."""
    clock = FakeClock()
    relay = RelaySpy(fail_times=1)
    coordinator = _coordinator(clock=clock, beacons=BeaconFeed([], []), relay=relay)

    clock.advance(HUB_LISTEN_SECONDS + 1.0)
    role = coordinator.tick()  # start_relay() raises inside this call

    assert role is Role.LISTENING  # the failed tick's role was never committed
    assert relay.start_calls == 1  # the attempt was made, and it failed

    clock.advance(1.0)
    role = coordinator.tick()  # start_relay() succeeds this time

    assert role is Role.HUB
    assert relay.start_calls == 2
