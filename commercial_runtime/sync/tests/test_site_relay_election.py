"""Tests for `commercial_runtime.sync.site_relay.election` -- the pure
decision function behind automatic LAN hub election. Task spec: this task's
own instructions, and `docs/launch-readiness/lan-restaurant-design.md` sec3
("A. Topology: the main till is the hub"), specifically the "Recovery:
manual hub promotion, v1" paragraph that this module's docstring explains it
is deliberately superseding (owner now requires zero configuration, so
manual promotion -- which needs a human standing in front of a till -- is no
longer an option; sec3's split-brain hazards are this module's design
constraints, not a reason not to build it).

Everything here is a pure unit test: no sockets, no real clock reads.
`now`/`started_at`/`seen_at` are always caller-supplied monotonic seconds,
exactly as `decide_role`'s own docstring requires -- that is what makes
concurrent-boot and split-brain scenarios (which are painful to reproduce
against a real clock and real UDP) trivial to construct here: just choose
the numbers.

MUTATION PROOFS (see this task's own report for the verbatim before/after
output): three specific protections are proven load-bearing by temporarily
breaking the exact thing they guard and watching the corresponding test go
RED, then restoring it and watching it go GREEN again --

    1. `test_alone_before_the_listen_window_stays_listening` -- proven by
       temporarily removing the listen-window check in `decide_role` (i.e.
       claiming HUB immediately with no live peer, instead of only after
       `HUB_LISTEN_SECONDS`).
    2. `test_two_devices_with_symmetric_inputs_converge_on_exactly_one_hub`
       -- proven by flipping `should_stand_down`'s comparison from `<` to
       `>`.
    3. `test_a_peer_unheard_past_silence_window_is_treated_as_gone` --
       proven by temporarily making `prune` a no-op (returning `peers`
       unchanged instead of filtering out stale ones).

Run:
    /c/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \\
        commercial_runtime/sync/tests/test_site_relay_election.py -q --color=no
"""
from __future__ import annotations

from commercial_runtime.sync.site_relay.election import (
    HUB_LISTEN_SECONDS,
    HUB_SILENCE_SECONDS,
    PeerBeacon,
    Role,
    decide_role,
    prune,
    should_stand_down,
)

MY_ID = "installation-aaaa"
LOWER_ID = "installation-0001"  # sorts below MY_ID
HIGHER_ID = "installation-zzzz"  # sorts above MY_ID


def _beacon(installation_id: str, *, seen_at: float, base_url: str = "https://10.0.0.5:8743", spki_pin: str = "pin==") -> PeerBeacon:
    return PeerBeacon(installation_id=installation_id, base_url=base_url, spki_pin=spki_pin, seen_at=seen_at)


# ── 1. Alone, before the listen window ──────────────────────────────────────

def test_alone_before_the_listen_window_stays_listening():
    started_at = 1000.0
    now = started_at + (HUB_LISTEN_SECONDS - 1.0)

    role = decide_role(
        my_installation_id=MY_ID,
        peers=[],
        started_at=started_at,
        now=now,
        current_role=Role.LISTENING,
    )

    assert role is Role.LISTENING


# ── 2. Alone, after the listen window ───────────────────────────────────────

def test_alone_after_the_listen_window_becomes_hub():
    started_at = 1000.0
    now = started_at + HUB_LISTEN_SECONDS + 1.0

    role = decide_role(
        my_installation_id=MY_ID,
        peers=[],
        started_at=started_at,
        now=now,
        current_role=Role.LISTENING,
    )

    assert role is Role.HUB


# ── 3. A live peer present -> CLIENT, even after the window ────────────────

def test_a_live_peer_present_stays_client_even_after_the_window():
    started_at = 1000.0
    now = started_at + HUB_LISTEN_SECONDS + 1.0
    peers = [_beacon(HIGHER_ID, seen_at=now - 1.0)]

    role = decide_role(
        my_installation_id=MY_ID,
        peers=peers,
        started_at=started_at,
        now=now,
        current_role=Role.LISTENING,
    )

    assert role is Role.CLIENT


# ── 4. A peer unheard past the silence window is ignored -> HUB ────────────

def test_a_peer_unheard_past_silence_window_is_treated_as_gone():
    started_at = 1000.0
    now = started_at + HUB_LISTEN_SECONDS + 1.0
    stale_peer = _beacon(HIGHER_ID, seen_at=now - (HUB_SILENCE_SECONDS + 1.0))

    role = decide_role(
        my_installation_id=MY_ID,
        peers=[stale_peer],
        started_at=started_at,
        now=now,
        current_role=Role.LISTENING,
    )

    assert role is Role.HUB


# ── 5. Already HUB, a lower-id peer appears -> stand down to CLIENT ────────

def test_already_hub_stands_down_when_a_lower_id_peer_appears():
    now = 5000.0
    peers = [_beacon(LOWER_ID, seen_at=now - 1.0)]

    role = decide_role(
        my_installation_id=MY_ID,
        peers=peers,
        started_at=0.0,
        now=now,
        current_role=Role.HUB,
    )

    assert role is Role.CLIENT


# ── 6. Already HUB, only higher-id peers -> stays HUB ───────────────────────

def test_already_hub_stays_hub_when_only_higher_id_peers_are_heard():
    now = 5000.0
    peers = [_beacon(HIGHER_ID, seen_at=now - 1.0)]

    role = decide_role(
        my_installation_id=MY_ID,
        peers=peers,
        started_at=0.0,
        now=now,
        current_role=Role.HUB,
    )

    assert role is Role.HUB


# ── 7. Two devices, symmetric inputs, converge on exactly one hub ──────────

def test_two_devices_with_symmetric_inputs_converge_on_exactly_one_hub():
    """Simulates the exact split-brain scenario sec3 warns about: both
    devices boot after the same power cut, neither hears the other during
    its own listen window (a slow/lossy LAN, or simply unlucky timing), so
    BOTH independently claim HUB. Once their beacons reach each other, this
    is the tick that must converge them back to exactly one hub -- run from
    each device's own point of view, with no shared state and no
    negotiation between the two calls, exactly as it would run on two real,
    partitioned tills."""
    device_a = "installation-1111"  # lower id
    device_b = "installation-9999"  # higher id
    now = 9000.0

    # Both independently believe they are HUB already (the split-brain the
    # window could not fully prevent), and each has just heard the other's
    # beacon for the first time.
    role_a = decide_role(
        my_installation_id=device_a,
        peers=[_beacon(device_b, seen_at=now - 1.0)],
        started_at=0.0,
        now=now,
        current_role=Role.HUB,
    )
    role_b = decide_role(
        my_installation_id=device_b,
        peers=[_beacon(device_a, seen_at=now - 1.0)],
        started_at=0.0,
        now=now,
        current_role=Role.HUB,
    )

    # Exactly one hub survives -- and it is the deterministic, total-order
    # winner (lowest id), computed identically and independently by both
    # sides.
    roles = {device_a: role_a, device_b: role_b}
    hubs = [device_id for device_id, role in roles.items() if role is Role.HUB]
    assert hubs == [device_a]
    assert role_a is Role.HUB
    assert role_b is Role.CLIENT


# ── 8. prune drops only the silent peers ────────────────────────────────────

def test_prune_drops_only_the_silent_peers():
    now = 10000.0
    live_peer = _beacon(LOWER_ID, seen_at=now - 1.0)
    boundary_peer = _beacon(HIGHER_ID, seen_at=now - HUB_SILENCE_SECONDS)
    stale_peer = _beacon(MY_ID, seen_at=now - (HUB_SILENCE_SECONDS + 0.001))

    survivors = prune([live_peer, boundary_peer, stale_peer], now=now)

    assert live_peer in survivors
    assert boundary_peer in survivors
    assert stale_peer not in survivors
    assert len(survivors) == 2


# ── should_stand_down: direct coverage of the isolated comparison ──────────

def test_should_stand_down_true_only_for_a_live_lower_id_peer():
    now = 20000.0

    assert should_stand_down(MY_ID, [_beacon(LOWER_ID, seen_at=now - 1.0)], now=now) is True
    assert should_stand_down(MY_ID, [_beacon(HIGHER_ID, seen_at=now - 1.0)], now=now) is False
    # A lower-id peer that has gone silent must not force a stand-down.
    assert should_stand_down(
        MY_ID, [_beacon(LOWER_ID, seen_at=now - (HUB_SILENCE_SECONDS + 1.0))], now=now
    ) is False
