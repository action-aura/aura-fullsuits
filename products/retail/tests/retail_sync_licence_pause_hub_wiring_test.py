"""Aura Retail -- keep-selling rule, hub half: app.py hands the licence-pause callable to each of the
THREE places that talk to other devices while this till is a hub:

  1. the hub self-sync ForwarderLoop      (`_enter_hub_self_sync`)
  2. the kill-switch upload-only forwarder (`_leave_hub_self_sync_under_kill_switch`)
  3. the LAN listener `start_site_relay`   (`_bind_relay_now`, inside `_start_site_relay_if_enabled`)

retail_sync_licence_pause_wiring_test.py proves the two SyncService streams; before this file nothing
failed if any of these three received `sync_paused=None` -- a lapsed hub would have kept forwarding to
the cloud and serving push/pull/bootstrap to the LAN. Each site is captured by wrapping the real
constructor (the real object is still built; only kwargs are recorded) and asserted to receive the
SAME callable app.py exposes, so a test that merely checks "something non-None" cannot pass.

Own module (explicit hub pin, boots the real app) -- run one file per process:
    pytest products/retail/tests/retail_sync_licence_pause_hub_wiring_test.py -v
"""
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_sync_pause_hub_wiring_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ["AURA_SYNC_RELAY_URL"] = "http://127.0.0.1:1"   # nothing listens; never reached
os.environ["AURA_SYNC_RELAY_INSECURE"] = "1"
os.environ.update(
    AURA_SITE_RELAY_ENABLED="1",         # explicit hub pin: binds synchronously inside init_app()
    AURA_SITE_RELAY_PORT="0",
    AURA_SITE_RELAY_BIND_HOST="127.0.0.1",
)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

from commercial_runtime.sync.site_relay import forwarder as _forwarder  # noqa: E402
from commercial_runtime.sync.site_relay import forwarder_loop as _forwarder_loop_mod  # noqa: E402
from commercial_runtime.sync.site_relay import listener as _listener  # noqa: E402

FORWARDER_KWARGS: list = []      # one dict per ForwarderLoop(...) construction, in order
RELAY_KWARGS: list = []          # one dict per start_site_relay(...) call

_RealForwarderLoop = _forwarder_loop_mod.ForwarderLoop
_real_start_site_relay = _listener.start_site_relay


class _RecordingForwarderLoop(_RealForwarderLoop):
    def __init__(self, *args, **kwargs):
        FORWARDER_KWARGS.append(dict(kwargs))
        super().__init__(*args, **kwargs)

    def start(self):  # never let a real timer reach the network from a test
        return None


def _recording_start_site_relay(*args, **kwargs):
    RELAY_KWARGS.append(dict(kwargs))
    return _real_start_site_relay(*args, **kwargs)


_forwarder_loop_mod.ForwarderLoop = _RecordingForwarderLoop
_listener.start_site_relay = _recording_start_site_relay

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True


def teardown_module(module):
    _forwarder_loop_mod.ForwarderLoop = _RealForwarderLoop
    _listener.start_site_relay = _real_start_site_relay
    handle = _app_module._site_relay_server
    if handle is not None:
        handle.server.shutdown()
        handle.server.server_close()
    for svc in (_app_module._sync_service, _app_module._registry_sync_service):
        if svc is not None:
            svc.stop()
    shutil.rmtree(DATA, ignore_errors=True)


def test_the_lan_listener_is_started_with_the_licence_pause_callable():
    assert len(RELAY_KWARGS) == 1, "the explicit hub pin must have called start_site_relay exactly once"
    assert RELAY_KWARGS[0].get('sync_paused') is _app_module._licence_pauses_sync_now


def test_the_hub_self_sync_forwarder_is_built_with_the_licence_pause_callable():
    # init_app() (explicit pin -> _bind_relay_now -> _enter_hub_self_sync) built the first forwarder.
    assert len(FORWARDER_KWARGS) == 1, "hub self-sync must have constructed exactly one ForwarderLoop"
    assert 'pull_down' not in FORWARDER_KWARGS[0], "this is the two-way hub forwarder, not the kill-switch one"
    assert FORWARDER_KWARGS[0].get('sync_paused') is _app_module._licence_pauses_sync_now


def test_the_kill_switch_upload_only_forwarder_is_built_with_the_licence_pause_callable(monkeypatch):
    before = len(FORWARDER_KWARGS)
    monkeypatch.setattr(_app_module, '_site_forwarder_loop', None)
    monkeypatch.setattr(_app_module, '_hub_site_log_unconsumed', lambda: False)
    monkeypatch.setattr(_app_module, '_cloud_relay_url_now', lambda *a, **k: ('http://127.0.0.1:1', None))
    monkeypatch.setattr(_forwarder, 'count_unforwarded', lambda conn: 3)

    _app_module._leave_hub_self_sync_under_kill_switch()

    assert len(FORWARDER_KWARGS) == before + 1, "the kill-switch drain forwarder must have been constructed"
    built = FORWARDER_KWARGS[-1]
    assert built.get('pull_down') is False, "this must be the upload-only kill-switch forwarder"
    assert built.get('sync_paused') is _app_module._licence_pauses_sync_now
