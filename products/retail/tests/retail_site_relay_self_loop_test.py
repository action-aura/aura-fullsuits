"""Aura Retail -- REHEARSAL-1007 B1 (CRITICAL): a second Windows till cut
itself off from the shop while showing "Synced".

THE MEASUREMENT (2026-10-07, two real tills, logs in the job's rehearsal
folder, till-5082-before-override.err):
  1. till B auto-joined till A's LAN hub -- and autojoin saved A's LAN
     address into B's `sync_relay_base_url`, the only home of Owner's cloud
     relay address;
  2. B then won the hub election and A stood down;
  3. B's forwarder (the hub's only path to Owner) read that field as "the
     cloud": `Site relay forwarder: state=RAN cloud=192.168.1.219:5443 ...
     push_error=TLS_VERIFICATION_FAILED pull_error=TLS_VERIFICATION_FAILED`,
     every tick, forever;
  4. `/sync/health` said `healthy: true, pending_count: 0`;
  5. staff created on A never reached B; restarts -- even with
     `-SiteRelay 0` -- resumed the same self-sync.

This file replays that with TWO REAL TILLS IN TWO OS PROCESSES
(`_hub_selfloop_device.py`), a real pinned-TLS LAN hub, the real autojoin
`/identity` + `/join`, the real election seams, and a fake Owner cloud relay
over real HTTP in this process. Lab safety (the owner's own till may be a
hub on 5443 on this laptop): hub ports are picked from 5300-5399, listeners
bind 127.0.0.1, no beacon is ever broadcast, no election runs on its own --
see the device script's docstring. A "LAN hub address" that must not be
dialled is always THIS till's own loopback hub, never a real LAN host.

Own process. Run:
    pytest products/retail/tests/retail_site_relay_self_loop_test.py -v
"""
import base64
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

TESTS_DIR = Path(__file__).resolve().parent
DEVICE_SCRIPT = TESTS_DIR / "_hub_selfloop_device.py"
LICENSE_PUBLIC_ID = "lic-rehearsal-b1-" + uuid.uuid4().hex[:8]
OWNER_KEY = Ed25519PrivateKey.generate()
OWNER_PRIV_B64 = base64.b64encode(OWNER_KEY.private_bytes(
    serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
    serialization.NoEncryption())).decode("ascii")
ROOT = Path(tempfile.mkdtemp(prefix="aura_b1_self_loop_"))
_used_ports = set()


def _free_hub_port() -> int:
    """A free TCP port in 5300-5399, never 5443 (the owner's live hub)."""
    for port in range(5300, 5400):
        if port in _used_ports:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        _used_ports.add(port)
        return port
    raise RuntimeError("no free port in 5300-5399")


# ── a fake Owner cloud relay: Owner's push/pull wire shape over real HTTP ──

class FakeCloud:
    """Stores pushed events with the pusher's installation_id as origin and
    hands each puller everything it did not push itself -- the two
    properties of Owner's relay this scenario depends on. `reject=True`
    answers every call with a rejection (no retry/backoff in the client),
    the fast way to make a forwarder fail."""

    def __init__(self):
        self.events = []
        self.reject = False
        self.calls = []
        self._lock = threading.Lock()
        cloud = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                origin = body.get("installation_id")
                with cloud._lock:
                    cloud.calls.append((self.path, origin))
                    if cloud.reject:
                        out = {"reason_code": "INSTALLATION_REVOKED"}
                    elif self.path.endswith("/push"):
                        for ev in body.get("events", []):
                            if not any(e["ev"]["id"] == ev["id"] for e in cloud.events):
                                cloud.events.append({"origin": origin, "ev": ev})
                        out = {"stored": len(body.get("events", [])), "received": len(body.get("events", []))}
                    else:
                        since = int(body.get("since") or 0)
                        rows = [dict(e["ev"], seq=i + 1) for i, e in enumerate(cloud.events)
                                if i + 1 > since and e["origin"] != origin]
                        cursor = rows[-1]["seq"] if rows else min(since, len(cloud.events))
                        out = {"events": rows, "cursor": cursor}
                data = json.dumps(out).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.host = f"127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def origins(self):
        with self._lock:
            return [e["origin"] for e in self.events]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# ── one real till in its own process ───────────────────────────────────────

class Till:
    def __init__(self, name, *, persisted_relay_url, site_relay_enabled=None, preseed=None,
                 hub_port=None, installation_id=None):
        self.name = name
        self.hub_port = hub_port or _free_hub_port()
        # Lexicographically high ids -- a habit every hub test here keeps,
        # so nothing a test does could ever out-rank a real till's uuid.
        self.installation_id = installation_id or f"zzzz-b1-{name}-{uuid.uuid4().hex[:8]}"
        data = ROOT / name
        data.mkdir(parents=True, exist_ok=True)
        cfg = {
            "data_dir": str(data), "hub_port": self.hub_port,
            "site_relay_enabled": site_relay_enabled,
            "persisted_relay_url": persisted_relay_url, "owner_priv_b64": OWNER_PRIV_B64,
            "license_public_id": LICENSE_PUBLIC_ID, "installation_id": self.installation_id,
            "preseed": preseed,
        }
        cfg_path = data / "_cfg.json"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        env = dict(os.environ)
        for var in ("AURA_SYNC_RELAY_URL", "AURA_OWNER_LICENSING_URL", "AURA_SITE_RELAY_ENABLED"):
            env.pop(var, None)
        self._stderr = open(data / "_stderr.log", "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, str(DEVICE_SCRIPT), str(cfg_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr,
            text=True, encoding="utf-8", env=env, cwd=str(TESTS_DIR))
        self._lines = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        ready = self._next(timeout=90)
        assert ready.get("ready"), ready

    def _pump(self):
        for line in self.proc.stdout:
            if line.startswith("@@"):
                self._lines.put(json.loads(line[2:]))
        self._lines.put({"ok": False, "error": "device process ended"})

    def _next(self, timeout=60):
        try:
            return self._lines.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError(f"{self.name}: no reply in {timeout}s; see {ROOT / self.name / '_stderr.log'}")

    def call(self, op, **kwargs):
        self.proc.stdin.write(json.dumps({"op": op, **kwargs}) + "\n")
        self.proc.stdin.flush()
        reply = self._next()
        assert reply.get("ok"), f"{self.name}.{op} failed: {reply.get('error')}\n{reply.get('trace')}"
        return reply["result"]

    def close(self):
        try:
            self.proc.stdin.write(json.dumps({"op": "quit"}) + "\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=20)
        except Exception:
            self.proc.kill()
        self._stderr.close()


@pytest.fixture(scope="module")
def cloud():
    c = FakeCloud()
    yield c
    c.close()


@pytest.fixture(scope="module")
def shop(cloud):
    a = Till("a", persisted_relay_url=cloud.url)
    b = Till("b", persisted_relay_url=cloud.url)
    yield {"a": a, "b": b}
    a.close()
    b.close()


def teardown_module(module):
    shutil.rmtree(ROOT, ignore_errors=True)


# ── 1-5: the rehearsal, step by step ───────────────────────────────────────

def test_1_b_joins_a_without_losing_its_cloud_relay(shop, cloud):
    """Step 1 of the measurement. MUTATION-PROOF (a): re-adding the
    `sync_relay_base_url = hub_url` write to autojoin's JOINED path turns
    this RED -- B's licence then holds A's hub address."""
    a, b = shop["a"], shop["b"]
    hub_a = a.call("become_hub")
    b.call("set_beacon", pointer={"installation_id": hub_a["installation_id"],
                                  "url": hub_a["url"], "spki_pin": hub_a["spki_pin"]})
    joined = b.call("attempt_join")

    assert joined["joined_hub"] is not None, "B never joined A -- the scenario did not happen"
    assert joined["joined_hub"]["url"] == hub_a["url"]
    assert joined["licence_relay_url"] == cloud.url, (
        f"joining A overwrote B's cloud relay address with {joined['licence_relay_url']!r}")


def test_2_after_the_election_flip_bs_forwarder_dials_the_cloud(shop, cloud):
    """Steps 2-3: B becomes hub, A stands down. B's forwarder must reach
    Owner's cloud -- never A's address, never its own. Pre-fix this read
    `cloud_host=127.0.0.1:<A's hub port>` with TLS_VERIFICATION_FAILED."""
    a, b = shop["a"], shop["b"]
    b.call("become_hub")
    a.call("stand_down")

    state_b = b.call("state")
    assert state_b["selector_local"] is True and state_b["hub_bound"] is True
    tick = b.call("forwarder_tick")
    assert tick["cloud_host"] == cloud.host, tick
    assert tick["push_error"] is None and tick["pull_error"] is None and tick["error"] is None, tick


def test_3_a_record_written_on_a_reaches_bs_books(shop, cloud):
    """Step 5's real question: does what A writes reach B? A's books ->
    A's site log -> A's forwarder -> cloud -> B's forwarder -> B's site log
    -> B's own sync stream -> B's database. (A category rides the exact
    path a staff account does -- the registry stream reads the same site
    log; staff themselves need Owner-signed leases this test cannot mint.)"""
    a, b = shop["a"], shop["b"]
    name = "B1-category-" + uuid.uuid4().hex[:6]
    a.call("emit_category", name=name)
    a.call("sync_run_once")           # A's books -> A's site log
    up = a.call("forwarder_tick")     # A's site log -> cloud
    assert up["forwarded"] >= 1 and up["push_error"] is None, up
    assert a.installation_id in cloud.origins()

    b.call("seed_company", company_id=LICENSE_PUBLIC_ID)
    down = b.call("forwarder_tick")   # cloud -> B's site log
    assert down["pulled"] >= 1 and down["pull_error"] is None, down
    b.call("sync_run_once")           # B's site log -> B's books
    assert name in b.call("category_names"), "A's record never reached B's books"


def test_4_health_reports_a_failing_forwarder(shop, cloud):
    """Step 4. MUTATION-PROOF (d): with `_install_upstream_health_overlay`
    made a no-op, B's health stays `healthy: true` while every forwarder
    tick fails -- the exact lie the rehearsal saw."""
    b = shop["b"]
    healthy = b.call("health")
    assert healthy["configured"] is True and healthy["healthy"] is True, healthy

    b.call("emit_category", name="queued-while-cut-off")
    b.call("sync_run_once")           # into B's own site log -- "0 pending" by itself
    cloud.reject = True
    try:
        b.call("forwarder_tick")
        b.call("forwarder_tick")
        sick = b.call("health")
    finally:
        cloud.reject = False
    assert sick["healthy"] is False, sick
    assert sick["pending_count"] >= 1, sick
    assert sick["push"]["consecutive_failures"] >= 2, sick["push"]
    assert sick["push"]["last_failure_reason"].startswith("UPSTREAM:"), sick["push"]

    b.call("forwarder_tick")          # the cloud answers again
    assert b.call("health")["healthy"] is True


# ── (b): an install the OLD autojoin already damaged ───────────────────────

def test_5_damaged_relay_address_is_repaired_from_the_last_cloud_cursor(cloud):
    """A till whose saved "cloud" address is a LAN hub address (here its
    own loopback hub -- the rehearsal's self-loop, without touching any
    real LAN host), but whose own books last synced with the cloud. Boot
    must restore the cloud address, and once hub, forward there.
    MUTATION-PROOF (b): with the LAN-hub check in `_cloud_relay_url_now`
    removed, the licence keeps the hub address and the forwarder dials it."""
    port = _free_hub_port()
    till = Till("damaged-recoverable", hub_port=port,
                persisted_relay_url=f"https://127.0.0.1:{port}",
                preseed={"cursor_relay_url": cloud.url, "cursor_last_seq": 0})
    try:
        state = till.call("state")
        assert state["licence_relay_url"] == cloud.url, state
        assert state["own_sync_client_base_url"] == cloud.url, state
        till.call("become_hub")
        tick = till.call("forwarder_tick")
        assert tick["cloud_host"] == cloud.host and tick["pull_error"] is None, tick
    finally:
        till.close()


def test_6_unrecoverable_damage_fails_loudly_instead_of_dialling_itself(cloud):
    """Same damage, but the till had already become a hub, so no cloud
    address survives anywhere. It must refuse to dial its own hub as the
    cloud and say so in /sync/health -- not `healthy: true, 0 pending`."""
    port = _free_hub_port()
    till = Till("damaged-lost", hub_port=port,
                persisted_relay_url=f"https://127.0.0.1:{port}",
                preseed={"cursor_relay_url": "aura-site-log://local", "cursor_last_seq": 0,
                         "site_rows": [{"origin": "phone-x"}]})
    try:
        till.call("become_hub")
        till.call("sync_run_once")
        tick = till.call("forwarder_tick")
        assert tick["state"] == "FAILED" and tick["error"] == "CLOUD_RELAY_URL_IS_LAN_HUB", tick
        health = till.call("health")
        assert health["healthy"] is False, health
        assert health["pull"]["last_failure_reason"] == "UPSTREAM:CLOUD_RELAY_URL_IS_LAN_HUB", health
    finally:
        till.close()


# ── (c): the operator kill switch wins over the boot resume ────────────────

def test_7_kill_switch_keeps_a_former_hub_off_its_site_log_and_drains_it(cloud):
    """A former hub (cursor on its own site log, every foreign row applied,
    one of its own rows never forwarded) boots with
    AURA_SITE_RELAY_ENABLED=0. Pre-fix it resumed hub self-sync. Now its
    books stay on the cloud and an upload-only forwarder sends the
    leftover row up."""
    till = Till("kill-switch", site_relay_enabled="0", persisted_relay_url=cloud.url,
                installation_id="zzzz-b1-kill-" + uuid.uuid4().hex[:8],
                preseed={"cursor_relay_url": "aura-site-log://local", "cursor_last_seq": 1,
                         "site_rows": [{"origin": "phone-x"}, {"origin": "__self__"}],
                         "forwarded_to_seq": 1})
    try:
        state = till.call("state")
        assert state["selector_local"] is False, state
        assert state["own_sync_client_base_url"] == cloud.url, state
        assert state["forwarder_running"] is True and state["forwarder_pull_down"] is False, state
        before = len(cloud.events)
        tick = till.call("forwarder_tick")
        assert tick["forwarded"] == 1 and tick["pulled"] == 0, tick
        assert len(cloud.events) == before + 1
    finally:
        till.close()


def test_8_kill_switch_still_applies_rows_other_devices_left_first(cloud):
    """The allow half of (c): a row another device left on the site log that
    this till's books never applied cannot be read any other way, so THIS
    boot keeps the site log (logged), rather than strand it."""
    till = Till("kill-switch-unconsumed", site_relay_enabled="0", persisted_relay_url=cloud.url,
                preseed={"cursor_relay_url": "aura-site-log://local", "cursor_last_seq": 0,
                         "site_rows": [{"origin": "phone-x"}], "forwarded_to_seq": 1})
    try:
        assert till.call("state")["selector_local"] is True
    finally:
        till.close()
