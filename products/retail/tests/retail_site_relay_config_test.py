"""Aura Retail -- R-LAN (2026-09-14): the LAN site relay's config surface,
and specifically that HUB MODE IS OFF UNLESS SOMEONE TURNED IT ON.

See config.py's "LAN site relay / hub mode" block and
docs/launch-readiness/lan-restaurant-design.md §3 for what a hub is. This
file exists for one reason, and it is a security reason rather than a
feature one:

    Every Aura process today binds 127.0.0.1 only (app.py's `_run_server`).
    SITE_RELAY_ENABLED is the single switch in this whole product that makes
    it listen on an interface a stranger on the same wifi can reach.

A future edit that makes that default truthy -- an `os.environ.get(...,
'1')`, a `!= '0'`, a bool() of a non-empty string -- would silently turn
every till in every shop into a network listener, and nothing else in the
suite would notice, because every OTHER test would keep passing: the
feature would simply be on. That is exactly the shape of defect this repo's
ENGINEERING.md calls "the pass condition IS the bug signature", so the
default is pinned here explicitly rather than left to be inferred.

`SITE_RELAY_ENABLED` is deliberately compared against the exact string '1'
and nothing else, so the common near-misses a human actually types --
'true', 'yes', 'TRUE', '0', '' -- all mean OFF. Failing closed on an
ambiguous value is correct here: the cost of wrongly-off is "the LAN
feature does not work and someone reads the runbook", and the cost of
wrongly-on is "a POS till is serving a sync endpoint on café wifi without
its owner knowing."

Pure config tests -- no database, no Flask, no network. config.py resolves
environment variables once at import time, so the on/off cases are exercised
via importlib.reload rather than by mutating an already-imported module's
constants (which would prove nothing about what a real boot does).

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


def test_hub_mode_is_off_when_nothing_is_set():
    """The out-of-the-box state. This is the assertion the whole file exists
    for: an install nobody configured must not listen on the LAN."""
    cfg = _reload_with(AURA_SITE_RELAY_ENABLED=None)
    assert cfg.SITE_RELAY_ENABLED is False


def test_hub_mode_is_on_only_for_the_exact_string_one():
    cfg = _reload_with(AURA_SITE_RELAY_ENABLED="1")
    assert cfg.SITE_RELAY_ENABLED is True


def test_near_miss_values_all_mean_off():
    """Fails closed on anything ambiguous. Each of these is a value a human
    plausibly types meaning 'on'; none of them switches on a network
    listener, because guessing wrong in that direction is the expensive
    one."""
    for value in ("true", "TRUE", "yes", "on", "0", "", "  1  ", "1 "):
        cfg = _reload_with(AURA_SITE_RELAY_ENABLED=value)
        assert cfg.SITE_RELAY_ENABLED is False, f"{value!r} must not enable hub mode"


def test_port_defaults_and_is_overridable():
    cfg = _reload_with(AURA_SITE_RELAY_ENABLED=None, AURA_SITE_RELAY_PORT=None)
    assert cfg.SITE_RELAY_PORT == 5443
    cfg = _reload_with(AURA_SITE_RELAY_PORT="7443")
    assert cfg.SITE_RELAY_PORT == 7443


def test_bind_host_defaults_to_all_interfaces_and_is_overridable():
    """0.0.0.0 is the honest default FOR A HUB -- a hub bound to loopback can
    serve nobody, so it would be inert in exactly the configuration someone
    switched it on to get. It is only ever reached because the enable flag
    above had to be set deliberately first; the two settings are a pair and
    testing the default in isolation would misread it as permissive."""
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
