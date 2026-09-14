"""Automatic hub election for the LAN site relay -- the thing
`docs/launch-readiness/lan-restaurant-design.md` sec3 explicitly declined to
build, made necessary anyway by a later owner decision.

Read sec3's "Recovery: manual hub promotion, v1" paragraph before touching
this file. It rejects automatic election in these exact words: "Automatic
election is deliberately rejected for v1: two hubs that each accepted writes
during a partition is the split-brain problem... A restaurant with a dead
till performs one deliberate, guided action; it does not need Raft." That
reasoning was correct the day it was written and is NOT being overturned
here -- the owner has since required LAN sync to be fully automatic ("no QR,
no pasted payload, no IP, no port, nothing anyone types"), which forecloses
the manual-promotion answer entirely: there is no admin present to press
"make this the hub" on a till that nobody is standing in front of. So this
module builds the thing sec3 refused, and sec3's hazards are not overridden
by that -- they are this module's design constraints, addressed one by one
below rather than argued away.

WHAT THIS MODULE IS: a pure decision function, `decide_role`, plus two small
helpers it is built from (`prune`, `should_stand_down`). No sockets, no
clock reads, no I/O of any kind -- `now` and `started_at` are both passed in
by the caller (the future beacon listener). That is deliberate and is the
whole reason this lives in its own file rather than inside `beacon.py` or
`listener.py`: a function with no side effects can be driven through every
edge case, including the concurrent-boot and split-brain cases that are hard
to reproduce with real sockets and real clocks, by simply choosing its
inputs. The listener that eventually calls this (a later task; not built by
this file) owns reading the real clock and the real peer set and acting on
the returned `Role`.

THE RULE, IN ONE SENTENCE: listen before claiming, never fight an existing
hub, and if two devices both end up claiming hub during a partition, the one
with the lower `installation_id` wins and the other stands down the moment
it hears about the conflict.

RESIDUAL RISK -- STATED PLAINLY, NOT SOLD AWAY: this rule does not eliminate
split-brain. A wifi partition can still produce two hubs, each accepting
writes from the devices stranded on its side, for as long as the partition
lasts. That is a real, accepted cost, not an oversight. It is SURVIVABLE
rather than corrupting for exactly the reason sec3 gives: the cloud relay
dedups every pushed event on its client-generated UUID, so when the
partition heals and both sides' forwarders catch up to Owner, nothing is
lost or double-counted -- each event reached the cloud exactly once no
matter which half of the split shop originated it. But UNTIL it heals, the
two halves of one shop see two different local orderings of the same
period: a waiter tablet on one side of the partition will not see a sale
rung on the other side, and vice versa, until connectivity is restored and
the standing-down side's beacon reaches the other. Do not describe this rule
to anyone as "solving" split-brain. It bounds the blast radius and makes
recovery automatic; it does not make the partition window consistent.

WHY STANDING DOWN LOSES NOTHING: a hub that stands down (transitions
HUB -> CLIENT) simply stops ACCEPTING NEW writes as a relay -- it does not
erase or roll back anything. Every event it already accepted while it was
hub is already committed to its own `site_sync_events` log, and that log is
independently forwarded upstream to Owner's cloud relay by the forwarder
regardless of this device's current `Role` (the forwarder reads the site
log, not the election state). So demotion is purely forward-looking: the
demoted device just starts pointing its own `SyncService` at the new hub
like any other client would, and the writes it already took are already
safe and already in flight to the cloud. Nothing already accepted is lost,
retried, or duplicated by a stand-down.

WHY THE COMPARISON MUST BE A TOTAL ORDER, NOT A COIN FLIP: both sides of a
split-brain pair run this exact same function, independently, with no
negotiation channel between them (that is what "partition" means). For
exactly one of them to end up HUB, both must compute the same answer to
"who wins" from only the inputs each can see -- its own id and the id(s) it
hears. Comparing `installation_id` strings with `<` gives that: it is a
total order (every pair of distinct ids is comparable, and the comparison
is antisymmetric and transitive), so the device holding the globally lowest
id among the live set is the unique fixed point that never sees a "lower
peer" and therefore never stands down, while every other device that can
hear it does. Which id wins (lowest, not highest, not longest, not
first-seen) is arbitrary -- nothing about the id's VALUE matters -- but the
rule must be a deterministic total order BOTH sides evaluate identically,
or two devices could each conclude the other should stand down (or that
they both should), which is a second, dumber way to manufacture the exact
split-brain this module exists to bound.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import List, Sequence

# How long a freshly (re)started device stays in LISTENING before it will
# claim HUB for itself, having heard no other hub. See the comment on the
# LISTENING branch of `decide_role` for why this window is the single most
# important number in this module.
HUB_LISTEN_SECONDS = 15.0

# How long a hub can go unheard before its beacon is treated as gone rather
# than merely quiet. Chosen well above any single missed broadcast interval
# so ordinary jitter (a dropped UDP packet, a slow tick) never mistakes a
# live hub for a dead one; see `prune`.
HUB_SILENCE_SECONDS = 45.0


class Role(enum.Enum):
    """What a device on the shop LAN currently believes it is."""

    LISTENING = "listening"
    HUB = "hub"
    CLIENT = "client"


@dataclass(frozen=True)
class PeerBeacon:
    """One hub beacon this device has heard, as of `seen_at`.

    Mirrors the signed fields `beacon.py` already puts on the wire
    (`installation_id`, the hub's current URL, its SPKI pin) plus a
    monotonic timestamp of when THIS device last heard it -- `seen_at` is
    local observation time, never the beacon's own claimed wall-clock time
    (`beacon.py`'s payload carries a wall-clock `timestamp` for the
    clock-skew warning UI; that is a different field for a different
    purpose and must never be substituted here). Using monotonic seconds
    for `seen_at`/`now`/`started_at` throughout this module is deliberate:
    wall-clock time can jump (NTP sync, DST, a user changing the clock) and
    a silence/listen window measured against it could misfire in either
    direction. A beacon is only ever heard from a device that currently
    believes it is HUB -- a device in LISTENING or CLIENT does not
    broadcast -- so `peers` throughout this module means "other hubs I can
    currently hear", not "every device on the LAN".
    """

    installation_id: str
    base_url: str
    spki_pin: str
    seen_at: float


def prune(peers: Sequence[PeerBeacon], *, now: float) -> List[PeerBeacon]:
    """Drop peers not heard from in over `HUB_SILENCE_SECONDS`.

    A hub that has stopped broadcasting -- powered off, unplugged, crashed
    -- is gone, and treating its last-heard beacon as still live would
    freeze the shop with no hub forever (rule 1 in the plan this module
    implements). `now - seen_at` is compared with strict `>` against the
    silence threshold, so a peer heard exactly at the boundary is still
    considered live -- ordinary jitter around the threshold should not flip
    a live hub to "gone" on a single unlucky tick.
    """
    return [peer for peer in peers if (now - peer.seen_at) <= HUB_SILENCE_SECONDS]


def should_stand_down(
    my_installation_id: str,
    peers: Sequence[PeerBeacon],
    *,
    now: float,
) -> bool:
    """True if a live peer with a LOWER installation_id is currently heard.

    This is the entire split-brain-recovery rule, isolated to one
    comparison so it can be mutation-tested on its own: the device holding
    the lowest id among the live set never finds a lower peer here and so
    never stands down; every other live device does, the moment it can
    hear the winner. `peers` is pruned first -- a stale, silent peer's id
    must never force a live, correctly-operating hub to stand down.
    """
    live = prune(peers, now=now)
    return any(peer.installation_id < my_installation_id for peer in live)


def decide_role(
    *,
    my_installation_id: str,
    peers: Sequence[PeerBeacon],
    started_at: float,
    now: float,
    current_role: Role,
) -> Role:
    """Decide this device's `Role` for the current tick.

    Pure function: no sockets, no clock reads -- `started_at` and `now` are
    both supplied by the caller, in the same monotonic clock. Rules, in the
    order they are evaluated:

      1. (via `prune`, applied throughout) a peer unheard for more than
         `HUB_SILENCE_SECONDS` is treated as gone, never as a live hub.
      2. Already HUB: stand down to CLIENT the instant a live peer with a
         LOWER installation_id is heard (`should_stand_down`); otherwise
         stay HUB. This is evaluated FIRST and independently of the rules
         below -- an established hub's own path to giving up the role is
         only ever this one comparison, never "no peers heard this tick"
         (which would make a hub flap back to LISTENING on a single missed
         beacon from itself, which cannot happen, or from meaningless
         jitter).
      3. Not already HUB, and a live peer exists: CLIENT. Never fight an
         established hub -- an established hub is only ever displaced by
         rule 2's deterministic comparison, never by a device that has not
         yet claimed the role itself.
      4. Not already HUB, no live peer, and the listen window has not yet
         elapsed since `started_at`: stay/become LISTENING. THIS WINDOW IS
         THE MAIN DEFENSE AGAINST SPLIT-BRAIN: claiming HUB the instant no
         peer is heard would mean every till in the shop that powers back
         on after one shared power cut claims HUB in the same instant,
         because none of them has had time to hear anyone else yet. Making
         every device sit quietly and listen first turns "N tills boot
         simultaneously" from "N simultaneous claims" into "N devices
         listening, then whichever finishes its window first claims, and
         the rest hear that claim and become CLIENT before their own
         window would have elapsed" -- assuming beacons propagate faster
         than the listen window, which `HUB_LISTEN_SECONDS` is sized for.
         It does not make simultaneous claims impossible (two devices can
         still both finish the window before hearing each other on a slow
         or lossy LAN), which is exactly why rule 2's stand-down exists as
         the backstop rather than the primary defense.
      5. Not already HUB, no live peer, listen window elapsed: HUB.
    """
    if current_role is Role.HUB:
        if should_stand_down(my_installation_id, peers, now=now):
            return Role.CLIENT
        return Role.HUB

    live_peers = prune(peers, now=now)
    if live_peers:
        return Role.CLIENT

    if (now - started_at) < HUB_LISTEN_SECONDS:
        return Role.LISTENING

    return Role.HUB
