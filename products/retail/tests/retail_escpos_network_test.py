"""core/retail/escpos_transport.py -- LAN/network (TCP) ESC/POS transport
tests (retail-hardware-viewports).

WHY THIS FILE EXISTS

`escpos_transport.py` was WINDOWS-ONLY: `send_raw`/`list_printers` reach a
printer through the Windows print spooler, gated behind `_require_windows()`
-- fine for a printer with its own Windows driver, but it means a plain
LAN/network receipt printer (extremely common and cheap, and the kind of
hardware that has no Windows driver to install) could not be reached from
this backend AT ALL, on any platform. `send_to_network()` closes that gap
with a plain TCP "raw"/JetDirect socket -- the same approach the Android app
already uses for its own printer.

This is provable with no physical hardware: every test below runs a REAL
`socket` server on `127.0.0.1`, bound to port 0 (OS-assigned), in a
background thread, and asserts on exactly what that server received. No
mock stands in for the network -- only the PRINTER (a receipt printer's
firmware/hardware) is something this suite cannot exercise, and that gap is
called out explicitly in the task's own final report, not hidden here.

Self-booting, matching `retail_escpos_transport_test.py`'s own convention
(no shared conftest.py for products/retail/tests/) for the transport-level
tests (1-5). Test 6 is route-level and needs a real Flask app + sqlite
company, so it additionally boots one at import, matching
`retail_printer_kick_test.py`'s own bootstrap verbatim -- see that file's
docstring for why nothing here is shared via a conftest.py instead. One file
per process (AUDIT-010): this boots an app at import.

Run:
    <python> -m pytest products/retail/tests/retail_escpos_network_test.py -v
"""
import os
import shutil
import socket
import sys
import tempfile
import threading
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]         # products/retail
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent                     # aura-fullsuits
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail import escpos_transport as transport  # noqa: E402

# ── Route-level bootstrap (test 6 only) ─────────────────────────────────────
# Verbatim shape of retail_printer_kick_test.py's own bootstrap -- see that
# file's docstring for why this is duplicated per-file rather than shared.
DATA = Path(tempfile.mkdtemp(prefix="aura_retail_escpos_network_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from core.retail import escpos_receipt  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_user(role, *, company_id=None):
    """Same shape as retail_printer_kick_test.py's own `_make_user` (no
    `capabilities` override -- this file needs only one admin)."""
    email = f"net-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "EscPosNetworkPW1"  # pragma: allowlist secret -- throwaway test fixture, not a real credential
    company_id = company_id or str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role, "active"),
    )
    # mt_require_subsystem still demands the legacy un-namespaced grant, so
    # without this the request never reaches the capability check at all.
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client, company_id


def _seed_shop():
    """Same shape as retail_printer_kick_test.py's own `_seed_shop`."""
    admin, company_id = _make_user('admin')
    admin.get('/api/sub/retail/settings/tax')  # forces the lazy schema helpers, same as sibling files

    import random
    dconn = get_retail_conn()
    dconn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
        (company_id, random.randint(1, 5_000_000)),
    )
    dconn.commit()
    dconn.close()

    r = admin.post('/api/sub/retail/products', json={
        'name': 'ESC/POS Network Test Product', 'sku': f'NETKICK-{uuid.uuid4().hex[:8]}',
        'sell_price': 10.0, 'tax_rate': 0, 'initial_stock': 1000,
    })
    assert r.status_code == 200, r.get_json()
    product_id = r.get_json()['data']['id']
    return admin, company_id, product_id


def _sale(client, product_id, *, payment_method='cash'):
    """Rings ONE real sale through the real route, matching
    retail_printer_kick_test.py's own `_sale` -- so `payment_method` on the
    row is exactly what create_sale itself writes."""
    payload = {
        'items': [{'product_id': product_id, 'quantity': 1}],
        'amount_paid': 1000,
        'payment_method': payment_method,
        'idempotency_key': str(uuid.uuid4()),
    }
    r = client.post('/api/sub/retail/sales', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


@pytest.fixture(scope='module')
def shop():
    return _seed_shop()


# ═════════════════════════════════════════════════════════════════════════
# A real TCP listener -- stands in for a physical printer everywhere below.
# ═════════════════════════════════════════════════════════════════════════

class _RecvServer:
    """A real `socket` listener on `127.0.0.1`, OS-assigned port, accepting
    exactly ONE connection in a background thread and recording every byte
    it receives until that connection closes. This is what "provable with no
    hardware" means in this file: `send_to_network` talks to a real TCP
    endpoint, not a mock of one."""

    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(('127.0.0.1', 0))
        self._sock.listen(1)
        self.host, self.port = self._sock.getsockname()
        self.received = bytearray()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        try:
            self._sock.settimeout(10.0)
            conn, _addr = self._sock.accept()
        except OSError:
            return
        try:
            conn.settimeout(10.0)
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                self.received.extend(chunk)
        except OSError:
            pass
        finally:
            conn.close()

    def join(self, timeout=10.0):
        """Waits for the accept+recv loop to finish (i.e. the client closed
        its side) so `self.received` is safe to assert on."""
        self._thread.join(timeout)

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture
def recv_server():
    server = _RecvServer()
    yield server
    server.close()


def _unused_tcp_port():
    """A port number that is guaranteed to have NOTHING listening on it at
    the moment this returns -- bind to port 0 for an OS-assigned free port,
    then close immediately without ever calling `listen()`."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ═════════════════════════════════════════════════════════════════════════
# 1. send_to_network delivers the EXACT bytes
# ═════════════════════════════════════════════════════════════════════════
# MUTATION-PROVEN (see the task's own verification step): breaking
# `send_to_network` (e.g. sending `payload[:-1]` or a `send()` swapped in
# for `sendall()`... see test 4 for the short-write case specifically) makes
# this go RED with a real byte-mismatch, restoring makes it GREEN -- both
# directions quoted in the final report.

def test_send_to_network_delivers_exact_bytes(recv_server):
    payload = bytes(range(256)) + b"\x1b\x40\x1d\x56\x42\x03"  # every byte value, plus real ESC/POS bytes
    transport.send_to_network(recv_server.host, recv_server.port, payload)
    recv_server.join()
    # A non-zero length AND an exact match -- so a silently-broken adapter
    # (e.g. one that "succeeds" without ever calling sendall) and a
    # silently-broken test (e.g. an assertion that passes on empty bytes)
    # cannot look identical.
    assert len(recv_server.received) > 0
    assert bytes(recv_server.received) == payload


# ═════════════════════════════════════════════════════════════════════════
# 2. Connection refused -- named error, never a bare OSError
# ═════════════════════════════════════════════════════════════════════════

def test_send_to_network_connection_refused_names_host_and_port():
    port = _unused_tcp_port()  # nothing is listening here
    with pytest.raises(transport.EscPosTransportError) as exc_info:
        transport.send_to_network('127.0.0.1', port, b'\x1b@')
    message = str(exc_info.value)
    assert '127.0.0.1' in message, message
    assert str(port) in message, message


def test_send_to_network_connection_refused_is_not_a_bare_oserror():
    # The regression this guards: forgetting to wrap the `socket.create_
    # connection`/`sendall` call in `except OSError` would surface as a bare
    # `ConnectionRefusedError` (an OSError subclass) instead of this
    # module's own named error -- exactly the same discipline
    # retail_escpos_transport_test.py's `test_non_windows_error_is_not_
    # attributeerror` applies to the Windows-spooler half of this module.
    port = _unused_tcp_port()
    try:
        transport.send_to_network('127.0.0.1', port, b'\x1b@')
    except transport.EscPosTransportError:
        pass
    except OSError:
        pytest.fail('send_to_network raised a bare OSError instead of EscPosTransportError')


# ═════════════════════════════════════════════════════════════════════════
# 3. Invalid input -- a typo'd setting fails with a clear message
# ═════════════════════════════════════════════════════════════════════════

def test_send_to_network_rejects_empty_host():
    with pytest.raises(transport.EscPosTransportError) as exc_info:
        transport.send_to_network('', 9100, b'\x1b@')
    assert 'host' in str(exc_info.value).lower()


def test_send_to_network_rejects_port_zero():
    with pytest.raises(transport.EscPosTransportError) as exc_info:
        transport.send_to_network('127.0.0.1', 0, b'\x1b@')
    assert 'port' in str(exc_info.value).lower()


def test_send_to_network_rejects_port_above_65535():
    with pytest.raises(transport.EscPosTransportError) as exc_info:
        transport.send_to_network('127.0.0.1', 70000, b'\x1b@')
    assert 'port' in str(exc_info.value).lower()


def test_send_to_network_rejects_non_integer_port():
    with pytest.raises(transport.EscPosTransportError) as exc_info:
        transport.send_to_network('127.0.0.1', '9100', b'\x1b@')
    assert 'port' in str(exc_info.value).lower()


# ═════════════════════════════════════════════════════════════════════════
# 4. A large payload arrives complete -- proves sendall(), not a short send()
# ═════════════════════════════════════════════════════════════════════════

def test_send_to_network_large_payload_arrives_complete(recv_server):
    # Deterministic content (not os.urandom) so a mismatch is reproducible
    # and diffable -- 200,000 bytes, well past any single send()'s usual
    # short-write ceiling on a loopback socket.
    payload = (bytes(range(256)) * 800)[:200_000]
    assert len(payload) == 200_000
    transport.send_to_network(recv_server.host, recv_server.port, payload)
    recv_server.join()
    assert len(recv_server.received) == len(payload)
    assert bytes(recv_server.received) == payload


# ═════════════════════════════════════════════════════════════════════════
# 5. Does NOT require Windows -- no _require_windows() guard at all
# ═════════════════════════════════════════════════════════════════════════

def test_send_to_network_works_regardless_of_platform(monkeypatch, recv_server):
    # Meaningful on every host this suite runs on, Windows included: this
    # does not merely assert "it worked" (which would be vacuous on
    # Windows), it patches `sys.platform` to something that is not `win32`
    # and confirms the call still succeeds -- proving the function has no
    # platform branch to take in the first place.
    monkeypatch.setattr(sys, 'platform', 'not-a-real-platform')
    payload = b'\x1b@platform-agnostic-network-print\n'
    transport.send_to_network(recv_server.host, recv_server.port, payload)
    recv_server.join()
    assert bytes(recv_server.received) == payload


def test_send_to_network_never_calls_require_windows(monkeypatch, recv_server):
    # The stronger, direct proof (meaningful even ON Windows, where the test
    # above would otherwise pass "for free"): if `send_to_network` were ever
    # changed to call `_require_windows()`, this makes that change fail
    # loudly here instead of silently reintroducing the Windows-only gap
    # this function exists to close.
    def _boom():
        raise AssertionError('_require_windows() must never be called by send_to_network')
    monkeypatch.setattr(transport, '_require_windows', _boom)
    payload = b'\x1b@no-windows-guard\n'
    transport.send_to_network(recv_server.host, recv_server.port, payload)
    recv_server.join()
    assert bytes(recv_server.received) == payload


# ═════════════════════════════════════════════════════════════════════════
# 6. Route-level: printer_kick with a `host` actually kicks
# ═════════════════════════════════════════════════════════════════════════
# THE test that proves the wiring, not just the transport -- matching
# retail_printer_kick_test.py's own test 6 docstring almost verbatim.
# MUTATION-PROVEN (see the task's own verification step): breaking
# printer_kick's SELECTION RULE (e.g. making it ignore `host` and always
# fall through to the Windows-spooler branch) makes this go RED, restoring
# makes it GREEN -- both directions quoted in the final report.

def test_printer_kick_with_host_kicks_a_real_listener(shop, recv_server):
    admin, _, product_id = shop
    sale_id = _sale(admin, product_id, payment_method='cash')

    r = admin.post('/api/sub/retail/printer/kick', json={
        'sale_id': sale_id,
        'host': recv_server.host,
        'port': recv_server.port,
    })

    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['kicked'] is True, d
    recv_server.join()
    # Exactly the kick bytes -- never a rendered receipt -- matching
    # retail_printer_kick_test.py's identical assertion for the Windows-
    # spooler path.
    assert bytes(recv_server.received) == escpos_receipt.drawer_kick()
