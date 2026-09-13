"""Aura Retail -- R-LAN: an ordinary till binds NOTHING on the network.

The negative-path mirror of retail_site_relay_boots_when_enabled_test.py, in
its own process for the same reason that file is (config.py and app.py both
resolve their environment once, at import).

THIS IS THE MORE IMPORTANT OF THE TWO FILES, and it is worth saying why,
because the enabled-path test is the one that looks like the real work.

Almost every Aura install will never be a hub. A corner shop with one till
has no LAN to converge and no second device; it just sells. For all of those
installs the only correct behaviour is that this entire feature does not
exist -- no socket, no thread, no certificate on disk. The product binds
127.0.0.1 and nothing else, exactly as it did before R-LAN.

The regression this guards is not subtle in effect but is very easy to
introduce: someone moves `_start_site_relay_if_enabled()` above its own flag
check, or wires the listener straight into `init_app`, or "simplifies" the
enable flag. The feature would work perfectly in every functional test --
better, even, because hub mode would now need no configuration -- and every
till in every shop would quietly begin answering sync requests on the
customer wifi. Nothing else in this suite would fail.

retail_site_relay_config_test.py pins the FLAG's default. This file pins what
the flag actually prevents, which is the part that matters: no bound socket
and no generated TLS identity on an install that never opted in.

Run:
    pytest products/retail/tests/retail_site_relay_inert_when_disabled_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_site_relay_inert_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SYNC_RELAY_URL", None)
# The whole point: UNSET, exactly as a shipped install arrives. Not '0' --
# unset, which is the state that actually reaches a customer machine.
for _var in ("AURA_SITE_RELAY_ENABLED", "AURA_SITE_RELAY_PORT", "AURA_SITE_RELAY_BIND_HOST"):
    os.environ.pop(_var, None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True


def teardown_module(module):
    if _app_module._sync_service is not None:
        _app_module._sync_service.stop()
    if _app_module._registry_sync_service is not None:
        _app_module._registry_sync_service.stop()
    shutil.rmtree(DATA, ignore_errors=True)


def test_no_site_relay_server_was_created():
    assert _app_module._site_relay_server is None


def test_no_tls_identity_was_generated_on_disk():
    """A till that never became a hub must not have minted a keypair. Beyond
    tidiness: a private key existing on disk is a thing to protect and a thing
    an auditor will ask about, and an install that cannot possibly use it
    should not have one."""
    site_relay_dir = Path(DATA) / 'site-relay'
    assert not site_relay_dir.exists(), (
        f"a non-hub install generated a TLS identity at {site_relay_dir}")


def test_calling_the_starter_directly_is_still_a_no_op_while_disabled():
    """Belt and braces on the guard itself rather than on init_app's call
    site: even invoked directly, the starter must refuse while the flag is
    off. This is what keeps the flag meaningful if the call ever moves."""
    _app_module._start_site_relay_if_enabled()
    assert _app_module._site_relay_server is None


def test_no_relay_thread_is_running_in_this_process():
    """Checked against the live thread table rather than inferred from our own
    variable.

    `_site_relay_server is None` only proves OUR handle is empty -- a listener
    started by some other route would leave that handle None and still be
    serving. `start_site_relay` names its thread 'aura-site-relay'
    (listener.py), so the thread table answers the real question: is anything
    in this process actually serving the LAN right now."""
    import threading

    relay_threads = [t.name for t in threading.enumerate() if t.name == 'aura-site-relay']
    assert relay_threads == [], f"a non-hub install is running a relay thread: {relay_threads}"
