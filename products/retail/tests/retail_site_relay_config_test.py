"""Aura Retail -- R-LAN (2026-09-14): the LAN site relay's AUTOMATIC,
licence-gated boot decision.

See config.py's "LAN site relay / hub mode" block for the full reasoning.
This file used to pin a much simpler contract, and per this repo's
ENGINEERING.md ("Never weaken a test to get green" / "when a test genuinely
must change, state exactly what it can no longer catch"), that history is
worth keeping rather than quietly overwriting:

    THE OLD TEST pinned `config.SITE_RELAY_ENABLED` to a bare boolean,
    default False, on the theory that nothing else in the suite would
    notice a drift to an on-by-default boolean. That was the right test for
    that contract: hub mode required an operator to type
    AURA_SITE_RELAY_ENABLED=1, so "off unless told" was the whole feature's
    safety story.

    That contract was DELIBERATELY changed, not regressed: the product
    owner's actual requirement ("i want the offline sync to be automatic...
    i dont want to put an ip or ports or so... i dont want the advanced
    networking stuff to be visible") cannot be satisfied by an env-var gate
    at all -- no shop owner will ever set one on a till, so the old default
    made the feature unreachable in the field, not merely off. Joining a
    hub is now licence-proof end to end (commercial_runtime/sync/
    site_relay/join.py), which is what makes flipping the default safe.

    THIS FILE now pins the REPLACEMENT contract just as tightly: '1' and
    '0' are still absolute overrides in either direction, and everything
    else (including unset) is decided ONLY by this install's own licence
    state (ACTIVE_FAMILY, commercial_runtime/licensing_contracts/
    state_machine.py) -- driven through every LicenseState member by
    iterating the enum, so a state added in the future is exercised here
    automatically rather than silently slipping past a hand-picked subset.

    WHAT THE OLD TEST COULD CATCH THAT THIS ONE CANNOT: a drift back to a
    bare on-by-default boolean is no longer a meaningful thing to guard,
    because there is no bare boolean left to drift -- config.py has no
    attribute that is simply "is hub mode on" any more. What THIS file
    guards instead, and the old one could not: an explicit '0' being
    silently overridden by an active licence, and a licence state being
    mis-classified against ACTIVE_FAMILY (which the exhaustive per-state
    loop below would catch even for a state that does not exist yet at the
    time this file was written).

`site_relay_should_start()` is pure (config.py's docstring: "No I/O and no
environment reads in here") -- it is a pure config test, no database, no
Flask, no network, no importlib.reload of anything for the enable-decision
tests below. The port/bind-host tests still use `_reload_with` because
SITE_RELAY_PORT/SITE_RELAY_BIND_HOST genuinely are resolved from the
environment at import time.

Run:
    pytest products/retail/tests/retail_site_relay_config_test.py -v
"""
import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_site_relay_config_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
for _var in ("AURA_SITE_RELAY_ENABLED", "AURA_SITE_RELAY_PORT", "AURA_SITE_RELAY_BIND_HOST"):
    os.environ.pop(_var, None)

import config  # noqa: E402
from commercial_runtime.licensing_contracts.state_machine import (  # noqa: E402
    ACTIVE_FAMILY, LicenseState)


def teardown_module(module):
    for _var in ("AURA_SITE_RELAY_ENABLED", "AURA_SITE_RELAY_PORT", "AURA_SITE_RELAY_BIND_HOST"):
        os.environ.pop(_var, None)
    importlib.reload(config)
    shutil.rmtree(DATA, ignore_errors=True)


def _reload_with(**env):
    """Re-import config.py with these AURA_SITE_RELAY_* values, exactly the
    way a real process boot would read them. Values of None unset the
    variable entirely, which is the genuine out-of-the-box state -- not the
    same thing as setting it to an empty string."""
    for key, value in env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    return importlib.reload(config)


# ── the tri-state enable decision (pure, no reload needed) ─────────────────

def test_unset_and_inactive_licence_is_off():
    """The out-of-the-box, unlicensed state: a fresh till with nothing
    configured and no licence yet must not listen on the LAN."""
    assert config.site_relay_should_start(None, None) is False
    assert config.site_relay_should_start(None, LicenseState.NOT_CONFIGURED) is False
    assert config.site_relay_should_start(None, LicenseState.ACTIVATION_REQUIRED) is False


def test_unset_and_active_licence_is_on():
    """The whole point of this change: a licensed shop needs NOTHING typed,
    no environment variable, no IP, no port -- it just becomes a hub."""
    assert config.site_relay_should_start(None, LicenseState.ACTIVE_ONLINE) is True
    assert config.site_relay_should_start(None, LicenseState.ACTIVE_OFFLINE) is True


def test_explicit_zero_wins_even_when_licence_is_active():
    """The operator kill switch beats an active licence. This is the
    override an operator reaches for specifically to guarantee a till never
    becomes a hub, so an active licence must never second-guess it."""
    for state in (LicenseState.ACTIVE_ONLINE, LicenseState.ACTIVE_OFFLINE,
                  LicenseState.WARNING, LicenseState.GRACE_PERIOD):
        assert config.site_relay_should_start('0', state) is False, (
            f"'0' must win over {state}")


def test_explicit_one_wins_even_when_licence_is_inactive():
    """Explicit on, for a dev/test box with no licence configured at all --
    kept working on purpose (config.py's comment) so hub mode stays
    exercisable without going through activation first."""
    for state in (None, LicenseState.NOT_CONFIGURED, LicenseState.REVOKED,
                  LicenseState.EXPIRED, LicenseState.SUSPENDED):
        assert config.site_relay_should_start('1', state) is True, (
            f"'1' must win over {state}")


def test_every_license_state_is_classified_by_active_family_membership():
    """Driven by iterating the LicenseState enum, not a hand-picked subset
    -- CLAUDE.md calls this exact mistake out by name ("never a hand-typed
    list of active states"). A state added to the enum in the future is
    exercised here automatically instead of silently slipping past."""
    for state in LicenseState:
        expected = state in ACTIVE_FAMILY
        assert config.site_relay_should_start(None, state) is expected, (
            f"{state} should {'start' if expected else 'not start'} the "
            f"relay under automatic (unset) mode"
        )


def test_near_miss_values_fall_back_to_automatic_not_to_a_guess():
    """'true', 'yes', '  1  ', an empty string -- none of these is the
    exact string '1' or '0', so none of them is an explicit choice in
    either direction. They must fall through to automatic (licence-gated),
    exactly like unset -- never silently treated as though they were '1' or
    '0'. This is what makes a typo safe: it degrades to "decided by
    licence" instead of picking a state nobody actually asked for."""
    near_misses = ("true", "TRUE", "yes", "on", "  1  ", "1 ", "0 ", "2", "")
    for value in near_misses:
        assert config.site_relay_should_start(value, None) is False, (
            f"{value!r} with no active licence must stay off")
        assert config.site_relay_should_start(value, LicenseState.ACTIVE_ONLINE) is True, (
            f"{value!r} with an active licence must turn on automatically")


def test_site_relay_enable_override_reflects_the_raw_environment_value():
    """SITE_RELAY_ENABLE_OVERRIDE is the raw AURA_SITE_RELAY_ENABLED string
    (or None), resolved once at import time like every other env-derived
    constant in this file -- config.py's site_relay_should_start() takes it
    as a parameter rather than reading the environment itself, so this is
    the one place that actually exercises the environment-to-config wiring
    rather than the pure decision alone."""
    cfg = _reload_with(AURA_SITE_RELAY_ENABLED=None)
    assert cfg.SITE_RELAY_ENABLE_OVERRIDE is None
    cfg = _reload_with(AURA_SITE_RELAY_ENABLED="1")
    assert cfg.SITE_RELAY_ENABLE_OVERRIDE == "1"
    cfg = _reload_with(AURA_SITE_RELAY_ENABLED="0")
    assert cfg.SITE_RELAY_ENABLE_OVERRIDE == "0"
    cfg = _reload_with(AURA_SITE_RELAY_ENABLED="banana")
    assert cfg.SITE_RELAY_ENABLE_OVERRIDE == "banana"


# ── port / bind-host (unaffected by the enable-decision change) ────────────

def test_port_defaults_and_is_overridable():
    cfg = _reload_with(AURA_SITE_RELAY_ENABLED=None, AURA_SITE_RELAY_PORT=None)
    assert cfg.SITE_RELAY_PORT == 5443
    cfg = _reload_with(AURA_SITE_RELAY_PORT="7443")
    assert cfg.SITE_RELAY_PORT == 7443


def test_bind_host_defaults_to_all_interfaces_and_is_overridable():
    """0.0.0.0 is the honest default FOR A HUB -- a hub bound to loopback can
    serve nobody, so it would be inert in exactly the configuration it is
    reached in. It is only ever reached because site_relay_should_start()
    above already said yes -- explicitly or automatically -- so testing the
    bind-host default in isolation would misread it as permissive on its
    own."""
    cfg = _reload_with(AURA_SITE_RELAY_BIND_HOST=None)
    assert cfg.SITE_RELAY_BIND_HOST == "0.0.0.0"
    cfg = _reload_with(AURA_SITE_RELAY_BIND_HOST="192.168.1.50")
    assert cfg.SITE_RELAY_BIND_HOST == "192.168.1.50"


def test_the_site_relay_port_does_not_collide_with_the_ui_listener():
    """The design doc refuses to rebind the existing loopback UI server, so
    the two ports must never collide -- a collision means whichever binds
    first wins, and the failure mode of the UI server winning is the entire
    Flask session and UI surface answering on the café wifi.

    The UI port default is NOT in config.py -- app.py resolves it itself, at
    the bottom of the file, as `int(os.environ.get('PORT', 5000))`. So it is
    read back out of app.py's source here rather than copied as a literal.
    That is deliberate: a hardcoded 5000 in this test would keep passing if
    someone moved app.py's default onto the site relay's port, which is the
    one change this test exists to catch.

    (An earlier draft of this test compared against `getattr(cfg, 'PORT',
    None)`. config.py has no `PORT` attribute at all, so it compared 5443
    against None and could never fail -- vacuous, and left in it would have
    read as coverage of a collision that was never actually being checked.)
    """
    import re
    app_source = (BACKEND_DIR / 'app.py').read_text(encoding='utf-8')
    match = re.search(r"os\.environ\.get\(\s*['\"]PORT['\"]\s*,\s*(\d+)\s*\)", app_source)
    assert match, "could not find app.py's PORT default -- this test can no longer guard anything"
    ui_port = int(match.group(1))

    cfg = _reload_with(AURA_SITE_RELAY_ENABLED=None, AURA_SITE_RELAY_PORT=None)
    assert cfg.SITE_RELAY_PORT != ui_port, (
        f"site relay default port {cfg.SITE_RELAY_PORT} collides with the UI "
        f"listener's default {ui_port}"
    )
