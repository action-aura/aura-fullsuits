"""The driver `election.py` explicitly declines to be: the thing that reads
the real clock, listens to the real LAN, and ACTS on the `Role` that
`election.decide_role` returns.

READ `election.py`'S MODULE DOCSTRING FIRST -- it is not restated here.
In its own words: "`decide_role`... No sockets, no clock reads... The
listener that eventually calls this (a later task; not built by this file)
owns reading the real clock and the real peer set and acting on the returned
`Role`." This module is that later task.

WHY THIS HAS TO EXIST NOW, NOT LATER
-------------------------------------
LAN sync just became automatic and opt-out rather than opt-in
(`products/retail/backend/config.py`'s `site_relay_should_start`): an
activated till starts a hub with NO configuration at all -- no env var, no
operator action. That means EVERY licensed till in a two-till (or five-till)
shop would start a relay and broadcast a beacon at boot, with nothing
deciding which one should actually be the hub. `decide_role` is the pure,
fully-tested rule for resolving that ("listen before claiming, never fight
an existing hub, lowest `installation_id` wins a tie") -- but as of this
module's addition, NOTHING CALLS IT. A pure function nobody drives is dead
code with a very good test suite. `SiteRelayCoordinator` is the driver.

THE BUG THIS MODULE IS DESIGNED AGAINST, EXPLICITLY
----------------------------------------------------
`beacon.BeaconBroadcaster` sends to `255.255.255.255` -- the LAN broadcast
address -- which a machine's own network stack can and does deliver back to
sockets on that same machine that are listening on the destination port.
Concretely: a hub broadcasting its own beacon can receive its OWN datagram
back through this module's `observe_beacons` seam.

If that self-beacon were allowed to land in the peer set, `election.
should_stand_down` would compare this device's `installation_id` against
itself via `peer.installation_id < my_installation_id`, which is always
`False` for equal strings -- so in THIS specific direction the device would
never mistakenly stand down. But that is a fact about Python string
comparison, not a property this module gets to lean on: `should_stand_down`
is a total-order comparison whose entire job is to be evaluated against
OTHER devices' ids, and feeding it a self-comparison is feeding it an input
it was never designed to see. A future change to that comparison (or to
`decide_role`'s handling of a peer set that happens to contain "myself") has
no reason to know it must preserve "and also never destabilize a device
against a copy of its own id" -- that invariant would be sitting there
silently relied upon, undocumented and untested, exactly the shape of bug
that survives a refactor and reappears at 2am in one specific shop. So the
filter is not "defense against a symptom we currently avoid by luck"; it is
"this input is nonsensical and must never reach the pure function at all,"
enforced at the one seam that can produce it (`_merge_observed_peers`,
below) and proven directly by feeding a coordinator its own beacon and
asserting it stays HUB (see the test file's
`test_own_beacon_echoed_back_does_not_trigger_self_stand_down`).

THE RESIDUAL SPLIT-BRAIN RISK -- STATED PLAINLY, NOT SOLVED HERE EITHER
------------------------------------------------------------------------
This module adds nothing to `election.py`'s own risk analysis; it only
supplies the missing clock and socket. `election.py`'s docstring already
states, and this module does not change: a wifi partition can still produce
two hubs, each accepting writes from whichever devices are stranded on its
side, for as long as the partition lasts. The forwarder's per-event UUID
dedup against Owner's cloud relay is what makes that survivable rather than
corrupting -- nothing in THIS file improves or worsens that story. Do not
describe this module, in a commit message or anywhere else, as "fixing"
split-brain. It is the wiring that lets the existing, honest, partial
mitigation actually run.

THE NEVER-THROWS CONTRACT
--------------------------
`tick()` is called from an unattended background thread with nobody
watching it. An exception escaping the thread's run loop ends that thread
silently -- Python does not restart a dead daemon thread, and there is
nothing else in this process watching for it to have died. If that happened
here, this device would simply never become a hub, or never notice a rival
and stand down, for the rest of the process's lifetime, with no error
anywhere to point at: the shop would just quietly stay split, or quietly
have no hub at all.

This is the desktop twin of
`android/aura-retail/app/src/main/java/com/actionaura/retail/sync/
HubAutoJoinService.kt`'s `runOnce` -- read that docstring; it states the
identical hazard for the identical reason ("an exception escaping a
`launch` coroutine's body completes it exceptionally, which would silently
end the `while (isActive)` loop... this device would never join a hub again
for the rest of the process's life, with nothing to point at"). `tick()`
below wraps its entire body -- beacon observation, the pure decision, and
every action taken on it -- in one `try/except Exception`, logs at WARNING
(discoverable if a tick fails EVERY time; not a paging-level event, the same
posture `listener.py`'s housekeeping sweep already takes for an identical
reason), and returns whatever `Role` this device held BEFORE the failed
tick. The next tick, on its own timer, tries again from scratch. See the
test file's `test_a_raising_seam_does_not_kill_the_tick_loop` for the
mutation proof that this actually holds.

SEAMS -- WHY EVERY ONE IS A CONSTRUCTOR PARAMETER, NOT AN IMPORT
-------------------------------------------------------------------
`election.decide_role` is testable by choosing plain numbers and strings.
This class wraps it with real sockets, real background threads and a real
clock, none of which are testable that way -- so every side-effecting
collaborator is injected, exactly the pattern `autojoin.discover_and_join`
already established for the identical reason (see that module's own
docstring, "Every collaborator is injectable"):

  * `observe_beacons(timeout_seconds) -> list[dict]` -- production default
    is `_default_observe_beacons`, below: a real UDP listen on
    `beacon.BEACON_PORT`, decoding pointers with `autojoin.
    _extract_beacon_pointer` (deliberately NOT a second wire format or a
    second size cap -- see that function's own docstring for why the
    signature-verifying `beacon.parse_beacon` cannot be used here either:
    this device has no pre-shared hub key to check an unrelated beacon
    against, only ever its own). Unlike `autojoin._default_beacon_listener`
    (which returns on the FIRST valid beacon, because it only ever needs
    one hub to join), this drains and returns EVERY valid beacon heard
    within the window, because this module's job is to maintain the whole
    live peer set, not to find one hub.
  * `start_relay() -> handle` / `stop_relay(handle)` -- deliberately have NO
    production default defined in this module. Starting a real hub needs
    `get_conn`, a bind host/port, an identity directory and a signer
    (`listener.start_site_relay`'s own parameter list) that this class has
    no way to obtain on its own and must not guess at or hardcode; the
    caller (whoever owns wiring this into `app.py` -- not this file's job)
    supplies a zero-argument closure over those. Tests supply a spy.
  * `attempt_join()` -- in production, a zero-argument closure over
    `autojoin.discover_and_join` with this device's own `state_repository`,
    `signer`, `trust_store` and clock already bound in -- for the identical
    reason `start_relay`/`stop_relay` take no production default here: this
    class has no way to construct those dependencies itself. Tests supply a
    spy.
  * `monotonic() -> float` -- defaults to the real `time.monotonic`.
    Matches `election.py`'s own non-negotiable: every timestamp this module
    computes or compares (`_started_at`, each `PeerBeacon.seen_at`, `now`)
    is monotonic seconds, NEVER wall-clock time, for the identical reason
    `election.py`'s `PeerBeacon` docstring gives -- wall-clock time can jump
    (NTP sync, DST, a user changing the clock) and a silence/listen window
    measured against it could misfire in either direction. Injectable so
    tests drive the listen window and the silence window by choosing
    numbers, exactly like `election.py`'s own tests do.

`tick()` performs exactly one observe-decide-act step and returns the `Role`
it ended up in; `start()`/`stop()` are a thin, always-daemon background
thread wrapped around calling it repeatedly. Tests drive `tick()` directly
and never touch `start()`/`stop()` at all -- that is the entire point of
splitting them.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable, Dict, List, Optional

from commercial_runtime.sync.site_relay import autojoin, beacon
from commercial_runtime.sync.site_relay.election import (
    PeerBeacon,
    Role,
    decide_role,
    prune,
)

_log = logging.getLogger(__name__)

# Matches `beacon.BEACON_INTERVAL_SECONDS` -- the hub broadcasts on that
# cadence, so a tick loop that listens for anything shorter than one full
# beacon interval risks missing a still-live hub's beacon on a given tick
# for no benefit (it would just be re-checked, and re-missed with the same
# probability, on the very next tick). Tying this to the SAME constant
# rather than picking an independent number means the two can never quietly
# drift apart -- a slower beacon cadence widens this listen window with it,
# automatically.
TICK_INTERVAL_SECONDS = beacon.BEACON_INTERVAL_SECONDS


def _default_observe_beacons(timeout_seconds: float) -> List[dict]:
    """Production default for `SiteRelayCoordinator`'s `observe_beacons`
    seam: listens on `beacon.BEACON_PORT` for up to `timeout_seconds` and
    returns EVERY beacon pointer heard in that window (not just the first --
    see this module's own docstring for why that differs from `autojoin.
    _default_beacon_listener`'s one-shot behaviour: that function is looking
    for A hub to join; this one is maintaining the LIVE PEER SET, which
    needs every hub currently shouting on the LAN, not merely the first one
    heard).

    Deliberately reuses `beacon.BEACON_PORT` and `autojoin.
    _extract_beacon_pointer` rather than inventing a second wire format or a
    second size cap (this task's own instruction, and the same discipline
    `autojoin.py`'s own module docstring insists on for its listener): two
    independent decoders for "the same conceptual beacon datagram" drift
    apart from each other by construction, and the failure mode is a beacon
    that quietly stops being recognised by one of the two listeners.

    Mirrors `beacon.listen_for_beacon`'s / `autojoin._default_beacon_
    listener`'s own socket shape exactly (`SO_REUSEADDR`, bind to `('',
    port)` so the datagram is received regardless of which local NIC it
    arrives on, deadline tracked in monotonic wall-clock terms so a burst of
    junk packets can consume the window but can never make this function
    return early with an incomplete answer) and their requirement 7: an
    invalid or unparsable datagram does NOT end the wait -- it is simply not
    added to the returned list, and listening continues for whatever time
    remains.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", beacon.BEACON_PORT))
    pointers: List[dict] = []
    try:
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return pointers
            sock.settimeout(remaining)
            try:
                datagram, _source = sock.recvfrom(65536)
            except socket.timeout:
                return pointers
            pointer = autojoin._extract_beacon_pointer(datagram)
            if pointer is not None:
                pointers.append(pointer)
            # Keep listening past a junk/invalid datagram -- see this
            # function's own docstring and beacon.py's requirement 7.
    finally:
        sock.close()


class SiteRelayCoordinator:
    """Owns the real clock, the real peer set, and acts on `election.
    decide_role`'s answer every tick. See module docstring for the full
    design; this docstring covers only the object's own state and API.

    State kept across ticks (all private, all mutated only from `tick()`,
    which is never called concurrently with itself by this class's own
    `start()` -- a single background thread, or a test calling `tick()`
    directly one call at a time):

      * `_role` -- this device's current `Role`, `LISTENING` at
        construction (mirrors `election.decide_role`'s own initial-state
        assumption: a freshly started device has claimed nothing yet).
      * `_started_at` -- captured ONCE, at construction, from the injected
        `monotonic` clock. `election.decide_role`'s listen-window rule
        (`HUB_LISTEN_SECONDS`) is measured from this moment, never
        recomputed later.
      * `_peers` -- `installation_id -> PeerBeacon`, the live-peer set
        `decide_role` is evaluated against every tick. Updated only through
        `_merge_observed_peers` (which is also where the self-beacon filter
        lives -- see module docstring). Opportunistically pruned at the end
        of every successful tick with `election.prune` (the same pure
        helper `decide_role` already applies internally) purely to bound
        this dict's memory on a long-running process; `decide_role` prunes
        its own input independently regardless, so skipping this cleanup
        would be a memory-hygiene issue, never a correctness one.
      * `_relay_handle` -- `None` when this device is not currently running
        a relay, otherwise whatever `start_relay()` returned. Its
        nullness, not `_role`, is the single source of truth for whether
        the relay is actually running -- see `tick()`'s HUB/CLIENT branches
        for why that distinction is what makes both idempotent.
    """

    def __init__(
        self,
        *,
        my_installation_id: str,
        start_relay: Callable[[], object],
        stop_relay: Callable[[object], None],
        attempt_join: Callable[[], None],
        observe_beacons: Optional[Callable[[float], List[dict]]] = None,
        monotonic: Callable[[], float] = time.monotonic,
        tick_interval_seconds: float = TICK_INTERVAL_SECONDS,
    ) -> None:
        """`start_relay`/`stop_relay`/`attempt_join` have no default: see the
        module docstring's "SEAMS" section for why a meaningful production
        default cannot be built inside this module at all (each needs
        plumbing -- `get_conn`, licensing state, a signer -- that only the
        caller wiring this into `app.py` has). `observe_beacons` and
        `monotonic` DO have real, fully self-contained production defaults,
        because both need only what this module already imports.
        """
        self._my_installation_id = my_installation_id
        self._start_relay = start_relay
        self._stop_relay = stop_relay
        self._attempt_join = attempt_join
        self._observe_beacons = (
            observe_beacons if observe_beacons is not None else _default_observe_beacons
        )
        self._monotonic = monotonic
        self._tick_interval_seconds = tick_interval_seconds

        self._role: Role = Role.LISTENING
        self._started_at: float = self._monotonic()
        self._peers: Dict[str, PeerBeacon] = {}
        self._relay_handle: Optional[object] = None

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def role(self) -> Role:
        """This device's `Role` as of the last completed tick. Read-only --
        the only way to change it is through `tick()`."""
        return self._role

    def start(self) -> None:
        """Starts the background tick loop on a daemon thread. Idempotent:
        calling `start()` while already running is a no-op rather than a
        second thread -- matches this package's general posture on
        background loops that must not run twice
        (`beacon.BeaconBroadcaster.start()`, `listener.py`'s own guard on
        `_site_relay_server`)."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="aura-site-relay-coordinator", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signals the loop to stop and waits (briefly) for it to actually
        exit. A `tick()` already blocked inside `observe_beacons` will not
        notice the stop signal until that call returns -- bounded by
        `_tick_interval_seconds`, the same bounded-shutdown-latency posture
        `beacon.BeaconBroadcaster.stop()` already accepts for the identical
        reason (a blocking socket call cannot be interrupted from outside
        without a second signalling mechanism this module has no need for)."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._tick_interval_seconds + 1.0)
            self._thread = None

    def _run(self) -> None:
        """Thin wrapper: the ENTIRE loop is just "call `tick()`, forever,
        until told to stop." `tick()` already blocks for roughly
        `_tick_interval_seconds` inside its own `observe_beacons` call (see
        `_default_observe_beacons`, which always listens for the full
        timeout in order to hear every beacon, not just the first), so this
        loop needs no separate sleep/wait of its own between iterations --
        an unconditional `_default_observe_beacons(timeout)` IS the tick
        cadence."""
        while not self._stop_event.is_set():
            self.tick()

    def _merge_observed_peers(self, raw_pointers: List[dict], *, now: float) -> None:
        """Folds this tick's freshly observed beacon pointers into the live
        peer set, stamping each with `now` -- LOCAL monotonic observation
        time, never any wall-clock field a pointer might carry (there isn't
        one on the pointer shape `autojoin._extract_beacon_pointer` returns
        anyway, but the principle is `election.PeerBeacon`'s own docstring's
        and it is restated here at the one call site that actually
        constructs a `PeerBeacon`: `seen_at` answers "when did *I* last hear
        this", not "what did the beacon claim about itself").

        THE SELF-BEACON FILTER -- see module docstring's full reasoning.
        `beacon.BeaconBroadcaster` sends to the LAN broadcast address, which
        this same machine's own socket can receive back. A beacon whose
        `installation_id` equals this device's own is therefore not a peer
        at all -- it is an echo of this device's own voice -- and must never
        reach `_peers`, from which it would otherwise be handed straight
        into `election.decide_role`'s `should_stand_down` comparison against
        itself. Filtered HERE, before a single such pointer is ever turned
        into a `PeerBeacon`, so no downstream code (this class's own
        `tick()`, or `election.py`, which was never written to expect or
        guard against seeing its own id in `peers`) has to know this
        filtering happened at all.
        """
        for pointer in raw_pointers:
            installation_id = pointer.get("installation_id")
            url = pointer.get("url")
            spki_pin = pointer.get("spki_pin")
            if not installation_id or not url or not spki_pin:
                # Defensive only: `_default_observe_beacons` never returns a
                # pointer missing these (`_extract_beacon_pointer` already
                # validates them), but a caller-supplied test/production
                # `observe_beacons` is not obligated to be that careful, and
                # a malformed entry must never crash a tick over it.
                continue
            if installation_id == self._my_installation_id:
                # THE SELF-BEACON FILTER. See this method's own docstring
                # and the module docstring's "THE BUG THIS MODULE IS
                # DESIGNED AGAINST" section. Dropped silently -- hearing
                # your own beacon on every tick you are HUB is the expected,
                # routine case, not something worth logging about.
                continue
            self._peers[installation_id] = PeerBeacon(
                installation_id=installation_id,
                base_url=url,
                spki_pin=spki_pin,
                seen_at=now,
            )

    def tick(self) -> Role:
        """One observe-decide-act step. Returns the `Role` this device is in
        after the tick -- the new role on success, or the role it held
        BEFORE this tick if the tick failed partway through (see below).

        NEVER THROWS -- see module docstring's "THE NEVER-THROWS CONTRACT"
        section for the full reasoning and the Kotlin analogue this mirrors.
        The entire body is one `try/except Exception`: a failure in ANY seam
        (`observe_beacons`, `start_relay`, `stop_relay`, `attempt_join`, or
        even `monotonic` itself) is caught, logged at WARNING with the
        traceback (discoverable if it fails on every tick; not a paging
        event -- a single missed tick is routine, exactly the posture
        `listener.py`'s housekeeping sweep already takes), and this method
        returns without committing `_role`, `_relay_handle` or the pruned
        `_peers` to their new values -- so the NEXT tick starts from
        whatever state was last successfully committed and retries the
        whole decision fresh, rather than getting stuck half-applied.

        ACTIONS, one per `Role` `decide_role` can return -- see the module
        docstring and `election.decide_role`'s own docstring for why these
        are the correct actions for each role:

          HUB       -- ensure the relay is running. Guarded by
                       `self._relay_handle is None`, which is what makes
                       repeated HUB ticks idempotent: `start_relay()` is
                       called on the FIRST tick that decides HUB and never
                       again while a handle is already held, however many
                       consecutive ticks stay HUB.
          CLIENT    -- ensure the relay is stopped (guarded by
                       `self._relay_handle is not None`, the same
                       idempotence in the other direction: `stop_relay()` is
                       never called against a relay this coordinator did
                       not itself start, or already stopped), then run
                       exactly one auto-join attempt. Both happen on EVERY
                       CLIENT tick, including a tick that was already
                       CLIENT the moment before -- a repeated join attempt
                       is cheap and safe by `autojoin.discover_and_join`'s
                       own design (its `ALREADY_JOINED` short-circuit is a
                       pure no-op with no network call at all).
          LISTENING -- nothing. No relay action, no join attempt. A device
                       still inside its own listen window has not yet
                       decided anything and must not act as though it had.
        """
        try:
            raw_pointers = self._observe_beacons(self._tick_interval_seconds)
            now = self._monotonic()
            self._merge_observed_peers(raw_pointers, now=now)

            new_role = decide_role(
                my_installation_id=self._my_installation_id,
                peers=list(self._peers.values()),
                started_at=self._started_at,
                now=now,
                current_role=self._role,
            )

            if new_role is Role.HUB:
                if self._relay_handle is None:
                    self._relay_handle = self._start_relay()
            elif new_role is Role.CLIENT:
                if self._relay_handle is not None:
                    self._stop_relay(self._relay_handle)
                    self._relay_handle = None
                self._attempt_join()
            # Role.LISTENING: deliberately no action of any kind -- see this
            # method's own docstring.

            self._role = new_role
            # Bound _peers' memory on a long-running process. Safe to do
            # AFTER decide_role has already consumed the un-pruned list:
            # decide_role prunes its own copy internally (`election.prune`),
            # so this is purely housekeeping, never a second, divergent
            # notion of "live" from the one decide_role itself used a few
            # lines above.
            self._peers = {
                peer.installation_id: peer
                for peer in prune(list(self._peers.values()), now=now)
            }
            return self._role
        except Exception:
            _log.warning(
                "site relay coordinator: tick failed; role and any relay "
                "start/stop this tick intended are left uncommitted, and "
                "the next tick will retry from scratch.",
                exc_info=True,
            )
            return self._role
