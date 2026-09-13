"""Aura Retail -- R-LAN: hub mode actually binds and answers when switched on.

The positive-path mirror of retail_site_relay_inert_when_disabled_test.py,
built on the same pattern as retail_sync_starts_when_configured_test.py (own
process, environment fixed before `import app`, because config.py and app.py
both resolve their environment once at import time).

WHAT THIS PROVES THAT THE UNIT TESTS CANNOT. commercial_runtime's own
site-relay suite proves the relay works when something constructs and starts
it. This file proves the PRODUCT does that: that `init_app()` -- the single
path every real boot and every test bootstrap goes through -- actually
reaches `_start_site_relay_if_enabled`, that the listener really binds a
socket, and that a pinned client really gets a sync-protocol answer out of
the shipped app. Wiring is exactly the layer that passes review by inspection
and is dead in practice, which is the failure this repo's ENGINEERING.md
names first: "Run the real artefact before claiming done."

It also asserts the thing that would be a genuine security regression rather
than a broken feature: the UI listener must stay on loopback. Hub mode adds a
SECOND server; it must never move or widen the first.

BINDS 127.0.0.1 DELIBERATELY. The production default for a hub is 0.0.0.0
(a hub bound to loopback can serve nobody), but a test must not open a port
on the developer's or CI machine's real network. `AURA_SITE_RELAY_BIND_HOST`
exists partly for this, and port 0 lets the OS pick a free one so parallel
runs cannot collide.

Run:
    pytest products/retail/tests/retail_site_relay_boots_when_enabled_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_site_relay_boot_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SYNC_RELAY_URL", None)
os.environ.update(
    AURA_SITE_RELAY_ENABLED="1",
    AURA_SITE_RELAY_PORT="0",          # OS picks a free port; read it back off the server
    AURA_SITE_RELAY_BIND_HOST="127.0.0.1",
)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True


def teardown_module(module):
    server = _app_module._site_relay_server
    if server is not None:
        server.shutdown()
        server.server_close()
    if _app_module._sync_service is not None:
        _app_module._sync_service.stop()
    if _app_module._registry_sync_service is not None:
        _app_module._registry_sync_service.stop()
    shutil.rmtree(DATA, ignore_errors=True)


def _relay_base_url():
    return f"https://127.0.0.1:{_app_module._site_relay_server.server_address[1]}"


def test_init_app_actually_started_the_site_relay():
    """Not "the module imports" and not "the flag is true" -- a real bound
    server object, created by the real boot path."""
    assert _app_module._site_relay_server is not None


def test_the_listener_is_really_bound_to_a_real_port():
    port = _app_module._site_relay_server.server_address[1]
    assert isinstance(port, int) and port > 0


def test_a_pinned_client_gets_a_sync_protocol_answer_from_the_shipped_app():
    """End of the wire, through the product's own boot: TLS handshake against
    the hub's generated identity, SPKI pin enforced, and a real reason_code
    back from the real blueprint.

    INSTALLATION_NOT_FOUND is the CORRECT answer here and is what makes this
    assertion meaningful: nothing is paired to this fresh hub, so a
    well-formed request from an unknown device must be refused by name. A
    200, or a connection error, or an HTML error page would all mean the
    wiring is wrong in different ways -- only this specific reason code means
    the request reached `auth.py` and was judged on its merits."""
    import base64
    import uuid
    from datetime import datetime, timezone

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
    from commercial_runtime.sync.site_relay.pinned_transport import pinned_session
    from commercial_runtime.sync.site_relay.tls_identity import (
        load_or_create_site_tls_identity)

    # The pin the hub generated at boot, read from the same directory app.py
    # told it to use. Idempotent, so this returns the existing identity rather
    # than minting a second one.
    _, _, pin = load_or_create_site_tls_identity(Path(DATA) / 'database' / '..' / 'site-relay')

    key = Ed25519PrivateKey.generate()
    body = {
        "installation_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "nonce": uuid.uuid4().hex,
        "events": [],
    }
    body["signature"] = base64.b64encode(key.sign(canonicalize_bytes(body))).decode("ascii")

    session = pinned_session(pin)
    resp = session.post(f"{_relay_base_url()}/api/sync/v1/push",
                        json=body, verify=False, timeout=10)

    assert resp.status_code == 400
    assert resp.json()["reason_code"] == "INSTALLATION_NOT_FOUND"


def test_the_ui_server_was_not_moved_off_loopback_by_hub_mode():
    """Hub mode adds a SECOND listener; it must never widen the first.

    Read out of app.py's source rather than asserted about a running socket,
    because the UI server is started by `_run_server`, which a test process
    does not call -- so the binding that ships is the one in the source, and
    that is the thing worth guarding. A '0.0.0.0' appearing in either bind
    call is the regression this catches."""
    import re

    source = (BACKEND_DIR / 'app.py').read_text(encoding='utf-8')
    ui_binds = re.findall(r"(?:_serve|app\.run)\(\s*(?:app,\s*)?host=(['\"])([^'\"]+)\1", source)
    assert ui_binds, "could not find the UI server's bind -- this test can no longer guard anything"
    for _quote, host in ui_binds:
        assert host == '127.0.0.1', f"UI server bind widened to {host!r}"


def test_the_app_still_boots_and_serves_health_with_hub_mode_on():
    """A hub is still a till. Whatever the relay does, the POS must work."""
    with app.test_client() as client:
        resp = client.get('/api/health')
        assert resp.status_code == 200
