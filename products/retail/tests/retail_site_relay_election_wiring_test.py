"""Aura Retail -- R-LAN: the automatic hub election is actually CONSTRUCTED and
actually RUNS on a real boot.

WHY THIS FILE EXISTS, and it is not "coverage". `SiteRelayCoordinator`
(commercial_runtime/sync/site_relay/coordinator.py) shipped with nine green
unit tests in commercial_runtime/sync/tests/test_site_relay_coordinator.py --
and NOTHING IN THE PRODUCT CONSTRUCTED IT. Its own commit said so. A pure
decision rule nobody drives is dead code with an excellent test suite, and no
amount of further testing of the rule itself could ever have noticed, because
the defect was in the one line that was never written.

That is the exact failure Android already had, and the exact shape of guard
that caught it: `HubAutoJoinWiringContractTest` ("447 tests, 0 skipped... and
NOTHING ANYWHERE CALLED `HubAutoJoinService.start()`... found on hardware, not
in the suite"). This file is the desktop twin of that guard, and it is
deliberately STRONGER than its Kotlin sibling, which could only read source
text because it has no runtime: here the real `app.py` boot path really runs,
so this file asserts on a live object, a live thread, real tick calls that
really happened, and seams that really bind and really unbind a socket --
never on the presence of a substring in a file.

WHAT EACH PIECE OF EVIDENCE IS FOR
  * `COORDINATOR`            -- the coordinator `init_app()` built. Proves
                                CONSTRUCTION.
  * `TICKS_OBSERVED`         -- real `SiteRelayCoordinator.tick` entries,
                                counted by a spy installed on the class BEFORE
                                `app` is imported. Proves the loop is DRIVEN,
                                which "an object exists" does not.
  * `RELAY_AT_BOOT`          -- snapshotted the instant `init_app()` returned.
                                Proves automatic mode binds NOTHING until the
                                election says so (the whole point: two tills
                                that both bind at boot is the two-hub bug).
  * the seam tests           -- drive `start_relay`/`stop_relay`/the beacon
                                observer directly and watch a real TCP port
                                open and close, and a real UDP datagram arrive.
                                Proves the coordinator was wired to the
                                PRODUCT's relay, not to stubs that would make
                                every assertion above pass while the shop still
                                had no hub.

THE COORDINATOR IS STOPPED AT IMPORT TIME, immediately after the evidence
above is captured. Everything after that point drives the seams by hand, one
call at a time, so no test races the background thread for the module globals.
Capturing first and quiescing second is why these assertions are deterministic
rather than timing-dependent.

AUTOMATIC MODE, DELIBERATELY: `AURA_SITE_RELAY_ENABLED` is POPPED here, not set
-- an explicit '1' takes the operator-pin branch in app.py, which binds
immediately and holds no election at all, and is already covered by
retail_site_relay_boots_when_enabled_test.py. Automatic mode additionally needs
an ACTIVE_FAMILY licence and LICENSING_PLATFORM == 'WINDOWS', so this fixture
writes a real ACTIVE_OFFLINE row into licensing.db before `app` is imported and
pins AURA_PLATFORM explicitly rather than relying on the host it happens to run
on.

BINDS 127.0.0.1 ON PORT 0 for the same reason the sibling file does: a test
must not open a real LAN port on a developer's or CI machine, and port 0 lets
the OS pick a free one so parallel runs cannot collide. The BEACON port
(45455) is the one exception -- the production observer binds it by definition,
and that is precisely what the observer test is proving.

Run (ONE FILE PER PROCESS, AUDIT-010):
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \
        products/retail/tests/retail_site_relay_election_wiring_test.py -q
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_site_relay_election_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SYNC_RELAY_URL", None)
# THE POINT OF THIS FILE: automatic mode. An explicit '1' would take app.py's
# operator-pin branch instead and never reach the election at all.
os.environ.pop("AURA_SITE_RELAY_ENABLED", None)
os.environ.update(
    AURA_SITE_RELAY_PORT="0",           # OS picks a free port
    AURA_SITE_RELAY_BIND_HOST="127.0.0.1",
    # Pinned rather than inherited: automatic election is Windows-only by
    # design (config.py's "A HANDSET NEVER SELF-ELECTS" block), so a test that
    # silently ran on a host reporting anything else would assert on the
    # refusal path while claiming to cover the election path.
    AURA_PLATFORM="WINDOWS",
)

# ── A real ACTIVE licence, written before `app` is imported ────────────────
# app.py reads licensing.db inside `_start_site_relay_if_enabled`, which runs
# during `init_app()` -- so the row has to exist first or automatic mode
# correctly refuses and there is no election to test.
from commercial_runtime.licensing_contracts.state_repository import (  # noqa: E402
    LicenseStateRecord, LicenseStateRepository)
from commercial_runtime.licensing_contracts.state_machine import LicenseState  # noqa: E402

INSTALLATION_ID = "installation-election-wiring-0001"
LICENSING_DB = DATA / "database" / "subsystems" / "licensing.db"
_repo = LicenseStateRepository(LICENSING_DB)
ACTIVE_RECORD = LicenseStateRecord(
    licensing_schema_version=2,
    product_code="RETAIL",
    platform="WINDOWS",
    current_state=LicenseState.ACTIVE_OFFLINE.value,
    owner_installation_id=INSTALLATION_ID,
    # Deliberately left None: `sync_relay_base_url` is what config.py would
    # discover and turn into a CLOUD sync service. This file is about the LAN
    # election, and a background cloud-sync timer would be noise in it.
    sync_relay_base_url=None,
)
_repo.save(ACTIVE_RECORD)

# ── The tick spy, installed on the real class BEFORE app imports it ────────
# "A coordinator object exists" and "the election is running" are different
# claims, and only the second one is the feature. `start()` spawning a thread
# that calls `tick()` is what makes the difference, so the evidence has to be
# actual tick entries. Recorded on ENTRY (not on return) because the first
# thing a real tick does is block for a full beacon interval inside
# `observe_beacons` -- waiting for a tick to COMPLETE would mean waiting five
# seconds to learn something that is already true.
from commercial_runtime.sync.site_relay import coordinator as _coordinator_module  # noqa: E402

TICKS_OBSERVED = []
_REAL_TICK = _coordinator_module.SiteRelayCoordinator.tick


def _spy_tick(self):
    TICKS_OBSERVED.append(self)
    return _REAL_TICK(self)


_coordinator_module.SiteRelayCoordinator.tick = _spy_tick

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

# ── Evidence captured, in this order, before anything is quiesced ──────────
COORDINATOR = _app_module._site_relay_coordinator
RELAY_AT_BOOT = _app_module._site_relay_server
_deadline = time.monotonic() + 5.0
while not TICKS_OBSERVED and time.monotonic() < _deadline:
    time.sleep(0.05)
TICK_COUNT_AT_BOOT = len(TICKS_OBSERVED)
COORDINATOR_THREADS_AT_BOOT = [
    t.name for t in threading.enumerate() if t.name == 'aura-site-relay-coordinator'
]

# Quiesce. From here on every test drives the seams by hand; nothing races the
# background thread for `_site_relay_server`.
if COORDINATOR is not None:
    COORDINATOR.stop()
_coordinator_module.SiteRelayCoordinator.tick = _REAL_TICK


def teardown_module(module):
    if _app_module._site_relay_coordinator is not None:
        _app_module._site_relay_coordinator.stop()
    handle = _app_module._site_relay_server
    if handle is not None:
        if getattr(handle, 'beacon', None) is not None:
            handle.beacon.stop()
        handle.server.shutdown()
        handle.server.server_close()
    if _app_module._sync_service is not None:
        _app_module._sync_service.stop()
    if _app_module._registry_sync_service is not None:
        _app_module._registry_sync_service.stop()
    shutil.rmtree(DATA, ignore_errors=True)


def _port_is_accepting(port):
    """True if something is listening on 127.0.0.1:port. A plain TCP connect,
    not a TLS handshake: this is asking "is the socket bound", which is the
    thing `stop_relay` has to actually undo. (The full TLS/pinned-client story
    is already proven against the same listener in
    retail_site_relay_boots_when_enabled_test.py; repeating it here would test
    the relay, not the wiring.)"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(3.0)
    try:
        probe.connect(('127.0.0.1', port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


# ── 1. Constructed ─────────────────────────────────────────────────────────

def test_init_app_actually_constructed_the_hub_election_coordinator():
    """THE MISSING LINE, guarded. Not "the module imports", not "the class has
    tests" -- a real `SiteRelayCoordinator` instance, built by the real boot
    path, on a real licensed Windows install."""
    assert COORDINATOR is not None, (
        "init_app() on an activated Windows till built no hub-election "
        "coordinator -- automatic hub mode is back to every till binding a "
        "relay with nothing deciding which one is the hub"
    )
    assert isinstance(COORDINATOR, _coordinator_module.SiteRelayCoordinator)


# ── 2. Driven ──────────────────────────────────────────────────────────────

def test_the_election_is_actually_driven_and_not_merely_constructed():
    """Construction without `start()` is the SAME defect in a different
    costume: an object that decides nothing, sitting in a module global,
    looking wired.

    Two independent pieces of evidence, because each fails differently: a
    named live thread (proves `start()` ran) and real `tick()` entries on the
    real class (proves that thread is actually executing the election loop,
    not merely existing)."""
    assert COORDINATOR_THREADS_AT_BOOT == ['aura-site-relay-coordinator'], (
        f"expected exactly one election tick thread, saw "
        f"{COORDINATOR_THREADS_AT_BOOT!r}"
    )
    assert TICK_COUNT_AT_BOOT >= 1, (
        "the coordinator exists but never ticked -- the election is not "
        "running, it is just sitting there"
    )


# ── 3. Wired to THIS till's identity, not a placeholder ────────────────────

def test_the_election_runs_under_this_tills_own_installation_id():
    """`election.should_stand_down` is a total order over installation_ids and
    nothing else. Wired with the wrong value -- a blank, a hostname, a fresh
    uuid per boot -- the comparison still "works" and the shop still ends up
    with the wrong number of hubs, silently. So the id is asserted to be the
    one Owner issued THIS install, read back out of licensing.db."""
    assert COORDINATOR._my_installation_id == INSTALLATION_ID


# ── 4. Automatic mode binds NOTHING until the election decides ─────────────

def test_automatic_mode_binds_no_lan_socket_at_boot():
    """The two-hub bug was every till binding at boot. If `init_app()` still
    binds, the coordinator above is decoration.

    Snapshotted the instant `init_app()` returned, deliberately, rather than
    read live here: the till legitimately DOES become a hub once
    `election.HUB_LISTEN_SECONDS` has elapsed with no peer heard, so a live
    read would be a race against the clock rather than an assertion."""
    assert RELAY_AT_BOOT is None, (
        "automatic hub mode bound a LAN socket at boot, before the election "
        "had decided anything -- which is exactly the state where two tills "
        "in one shop each become a hub"
    )


# ── 5. The start/stop seams drive the PRODUCT's real relay ─────────────────

def test_the_start_and_stop_seams_bind_and_unbind_the_real_relay():
    """The seams could be `lambda: None` and every assertion above would still
    pass while the shop had no hub. So: call the coordinator's own
    `start_relay`, and watch the product's module global fill in and a real
    TCP port start accepting. Then call its `stop_relay`, and watch both
    reverse.

    Both halves matter and the second is the one that gets skipped by default
    (ENGINEERING.md: "prove both directions of anything that both denies and
    allows"). A stand-down that stops the beacon but leaves the listener bound
    -- or clears the coordinator's handle but leaves `_site_relay_server`
    populated -- looks fine from the outside and leaves two answering hubs on
    the LAN, or an operator being told this till is the hub when it is not."""
    handle = COORDINATOR._start_relay()

    assert _app_module._site_relay_server is handle, (
        "start_relay did not publish the handle to the module global that "
        "admin_routes.py's hub provider reads"
    )
    assert isinstance(handle.port, int) and handle.port > 0
    assert _port_is_accepting(handle.port), "start_relay bound no real socket"
    # A hub that does not broadcast cannot be found by any device that has not
    # already pinned its address, so the beacon is part of "is the hub".
    assert handle.beacon_started is True

    COORDINATOR._stop_relay(handle)

    assert _app_module._site_relay_server is None, (
        "stop_relay left the module global populated -- /api/site-relay/status "
        "would keep telling an operator this till is the shop's hub"
    )
    assert handle.beacon_started is False, (
        "stop_relay left the addressing beacon broadcasting -- a till that "
        "stopped serving but keeps shouting pulls devices toward a hub that "
        "can no longer answer them"
    )
    assert not _port_is_accepting(handle.port), (
        "stop_relay left the LAN listener bound; standing down did not stand "
        "anything down"
    )


# ── 6. The beacon observer seam is a real LAN listener ─────────────────────

def test_the_beacon_observer_seam_really_listens_on_the_lan():
    """An `observe_beacons` that returns `[]` makes this till believe it is
    alone in the shop, forever -- it would win every election and the two-hub
    bug would be exactly as bad as before, with an election running on top of
    it. So the seam the coordinator actually holds is driven here against a
    real datagram on the real beacon port.

    The datagram is hand-built rather than signed, and that is correct rather
    than lazy: the production observer decodes with
    `autojoin._extract_beacon_pointer`, which deliberately does NOT verify a
    signature (coordinator.py's docstring explains why -- this device has no
    pre-shared key for an unrelated hub). Requiring a signature here would be
    testing a check the product does not make."""
    peer = {
        "installation_id": "installation-0000-a-lower-peer",
        "url": "https://10.0.0.9:8743",
        "spki_pin": "sha256/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    }
    datagram = json.dumps(peer).encode('utf-8')
    heard = []
    failure = []

    def _listen():
        try:
            heard.extend(COORDINATOR._observe_beacons(3.0))
        except OSError as exc:  # pragma: no cover - environment, not logic
            failure.append(exc)

    listener = threading.Thread(target=_listen, name='election-wiring-listener')
    listener.start()
    time.sleep(0.4)  # let the listener get its socket bound before sending

    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Sent more than once: UDP is allowed to drop, and one dropped packet
        # must not be the reason this guard reports the wiring is broken.
        for _ in range(3):
            sender.sendto(datagram, ('127.0.0.1', _coordinator_module.beacon.BEACON_PORT))
            time.sleep(0.15)
    finally:
        sender.close()

    listener.join(timeout=10.0)

    assert not failure, (
        f"the production beacon observer could not bind UDP "
        f"{_coordinator_module.beacon.BEACON_PORT}: {failure[0]!r}"
    )
    assert any(p.get('installation_id') == peer['installation_id'] for p in heard), (
        "the coordinator's observe_beacons seam heard nothing on the real "
        "beacon port -- this till would believe it is the only device in the "
        "shop and would win every election it ever held"
    )


# ── 7. An unlicensed / pre-activation install elects nothing, binds nothing ─

def test_an_unlicensed_install_neither_elects_nor_binds():
    """Runs the REAL boot seam a second time against a wiped licensing.db.

    This is the constraint that outranks the feature: an install with no
    licence must bind nothing at all -- not a listener, and now also not an
    election driver that would go on to bind one 15 seconds later. Driving
    `_start_site_relay_if_enabled()` itself (rather than re-asserting
    config.py's pure gate, which retail_site_relay_config_test.py already
    pins) is what makes this a guard on the ORDER of the branches added here:
    a coordinator built above the licence gate would pass every pure-function
    test in the suite."""
    saved_server = _app_module._site_relay_server
    saved_coordinator = _app_module._site_relay_coordinator
    _repo.reset()
    _app_module._site_relay_server = None
    _app_module._site_relay_coordinator = None
    try:
        _app_module._start_site_relay_if_enabled()

        assert _app_module._site_relay_server is None, (
            "an unlicensed install bound a LAN socket"
        )
        assert _app_module._site_relay_coordinator is None, (
            "an unlicensed install started a hub election -- it would bind a "
            "LAN socket the moment the listen window elapsed"
        )
    finally:
        if _app_module._site_relay_coordinator is not None:
            _app_module._site_relay_coordinator.stop()
        _repo.save(ACTIVE_RECORD)
        _app_module._site_relay_server = saved_server
        _app_module._site_relay_coordinator = saved_coordinator


# ── 8. A second init_app() does not start a second election ────────────────

def test_a_second_boot_does_not_build_a_second_election_driver():
    """`init_app()` is reachable more than once (its own docstring says so).
    Two coordinators on one till is not merely wasteful: each holds its own
    `_relay_handle`, so one can stop the relay the other started, and the till
    flaps between hub and not-hub with nothing in the logs naming a cause.

    The pre-existing double-start guard only tested `_site_relay_server`,
    which is None for the entire listen window on this path -- so on the
    automatic path it guarded nothing. This pins the widened guard."""
    before = _app_module._site_relay_coordinator
    assert before is not None, "precondition: this till has an election driver"

    _app_module._start_site_relay_if_enabled()

    try:
        assert _app_module._site_relay_coordinator is before, (
            "a second boot built a SECOND hub-election driver on one till"
        )
        assert _app_module._site_relay_server is None
    finally:
        if _app_module._site_relay_coordinator is not before:
            _app_module._site_relay_coordinator.stop()
