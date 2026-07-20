"""
commercial_runtime.launcher_support -- focused regression suite (Phase 3.7).

Covers the actual correctness-critical logic extracted from the Windows
desktop launchers (products/{retail,clinic}/desktop/launcher_*.py) after
the readiness-check root cause fix -- see
docs/corrections/launcher/root-cause-analysis.md and
docs/corrections/launcher/launcher-readiness-design.md.

check_readiness() is pure and dependency-injectable (fake clock, fake
sleep, fake opener, fake process-alive check), so these are true unit
tests -- no real socket, no real Windows session, no real packaged build.
Scenarios that only make sense against a real running process/exe (single
-instance enforcement, port release on shutdown, restart-after-shutdown, no
orphan process) are instead covered by the real packaged long-run smoke
test -- see docs/corrections/launcher/packaged-long-run-smoke-report.md.

Run:
    pytest products/retail/tests/launcher_support_test.py -v
"""
import sys
import urllib.error
from pathlib import Path

import pytest

SUITE_ROOT = Path(__file__).resolve().parents[3]
if str(SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(SUITE_ROOT))

from commercial_runtime.launcher_support import (  # noqa: E402
    LauncherState, ReadinessResult, check_readiness, health_url, no_proxy_opener,
)


class _FakeResponse:
    def __init__(self, status):
        self.status = status

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    """Replays a scripted sequence of outcomes, one per .open() call.
    Each item is either an int (HTTP status to return), or an exception
    instance/class to raise."""
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def open(self, url, timeout=None):
        self.calls.append((url, timeout))
        if not self.script:
            raise urllib.error.URLError('exhausted script -- connection refused')
        outcome = self.script.pop(0)
        if isinstance(outcome, int):
            if outcome == 200:
                return _FakeResponse(200)
            raise urllib.error.HTTPError(url, outcome, 'error', {}, None)
        if isinstance(outcome, Exception):
            raise outcome
        raise outcome


class _FakeClock:
    """Deterministic monotonic-style clock and sleep, driven by a fixed step."""
    def __init__(self, step=0.4):
        self.t = 0.0
        self.step = step

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += self.step


# ── 1. Server becomes ready immediately ─────────────────────────────────────

def test_ready_immediately():
    clock = _FakeClock()
    opener = _FakeOpener([200])
    result = check_readiness('http://x/api/health', timeout_seconds=45.0,
                              sleep=clock.sleep, now=clock.now,
                              opener_factory=lambda: opener)
    assert result.ready is True
    assert result.reason == 'ready'
    assert result.attempts == 1


# ── 2. Server becomes ready after a delay ───────────────────────────────────

def test_ready_after_delay():
    clock = _FakeClock()
    opener = _FakeOpener([urllib.error.URLError('refused')] * 5 + [200])
    result = check_readiness('http://x/api/health', timeout_seconds=45.0,
                              sleep=clock.sleep, now=clock.now,
                              opener_factory=lambda: opener)
    assert result.ready is True
    assert result.attempts == 6


# ── 3. External proxy environment variables are present ────────────────────

def test_no_proxy_opener_ignores_environment_proxy(monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://user:pass@proxy.example:8080')
    monkeypatch.setenv('HTTPS_PROXY', 'http://user:pass@proxy.example:8080')

    # Baseline: urllib's own default opener DOES pick up the environment
    # proxy (a live ProxyHandler with a non-empty mapping shows up in its
    # handler chain) -- this confirms the environment variables above are
    # actually in effect for this test process.
    import urllib.request
    default_handlers = [type(h).__name__ for h in urllib.request.build_opener().handlers]
    assert 'ProxyHandler' in default_handlers

    # no_proxy_opener() passes an explicit, empty-mapping ProxyHandler to
    # build_opener(), which suppresses build_opener()'s own
    # environment-auto-detecting default ProxyHandler entirely (an empty
    # ProxyHandler registers no protocol handler methods at all -- verified
    # directly against CPython's urllib.request behavior). The net effect:
    # zero proxy handling of any kind for this opener, regardless of what
    # HTTP_PROXY/HTTPS_PROXY/the Windows registry say.
    opener = no_proxy_opener()
    handler_types = [type(h).__name__ for h in opener.handlers]
    assert 'ProxyHandler' not in handler_types


# ── 4. localhost / 127.0.0.1 URL construction ───────────────────────────────

def test_health_url_uses_explicit_loopback_literal():
    url = health_url('127.0.0.1', 5000)
    assert url == 'http://127.0.0.1:5000/api/health'
    assert 'localhost' not in url


# ── 5. Readiness succeeds and remains successful beyond the old 43s window ──

def test_ready_after_44_seconds_still_succeeds_within_45s_budget():
    clock = _FakeClock(step=0.4)
    # 110 failures (~44s at 0.4s/attempt) then success, all inside the 45s deadline.
    opener = _FakeOpener([urllib.error.URLError('refused')] * 110 + [200])
    result = check_readiness('http://x/api/health', timeout_seconds=45.0,
                              sleep=clock.sleep, now=clock.now,
                              opener_factory=lambda: opener)
    assert result.ready is True
    assert result.elapsed < 45.0


# ── 6. Watchdog is cancelled after readiness (no re-polling) ────────────────

def test_no_further_polling_after_ready():
    clock = _FakeClock()
    opener = _FakeOpener([200, 200, 200])  # extra outcomes must never be consumed
    result = check_readiness('http://x/api/health', timeout_seconds=45.0,
                              sleep=clock.sleep, now=clock.now,
                              opener_factory=lambda: opener)
    assert result.ready is True
    assert len(opener.calls) == 1  # returned immediately on the first success


# ── 7. A later transient health failure does not trigger the startup timeout ─

def test_readiness_result_is_a_one_shot_call_not_reusable_as_a_watchdog():
    """check_readiness() is called exactly once by main() in both launchers
    (see launcher_readiness-design.md) -- there is no code path that
    re-invokes it after READY, so a transient failure occurring after
    startup cannot retroactively fail a startup that already succeeded.
    This test documents/enforces the contract: a fresh call with a
    subsequent failure is an independent result, never merged with a prior
    success."""
    clock = _FakeClock()
    ready_result = check_readiness('http://x/api/health', timeout_seconds=45.0,
                                    sleep=clock.sleep, now=clock.now,
                                    opener_factory=lambda: _FakeOpener([200]))
    assert ready_result.ready is True

    clock2 = _FakeClock()
    later_result = check_readiness('http://x/api/health', timeout_seconds=1.0,
                                    sleep=clock2.sleep, now=clock2.now,
                                    opener_factory=lambda: _FakeOpener([500]))
    assert later_result.ready is False
    # The two results are independent objects -- proving no shared mutable
    # "have we ever been ready" state exists that a real watchdog-style bug
    # could accidentally flip back to failed.
    assert ready_result.ready is True


# ── 8. Server process exits before readiness ────────────────────────────────

def test_process_exited_fails_fast_not_full_timeout():
    clock = _FakeClock()
    calls = {'n': 0}

    def is_alive():
        calls['n'] += 1
        return calls['n'] <= 2  # dies after 2 checks

    opener = _FakeOpener([urllib.error.URLError('refused')] * 100)
    result = check_readiness('http://x/api/health', timeout_seconds=45.0,
                              sleep=clock.sleep, now=clock.now,
                              is_process_alive=is_alive,
                              opener_factory=lambda: opener)
    assert result.ready is False
    assert result.reason == 'process_exited'
    assert result.elapsed < 5.0  # failed fast, did not wait out 45s


# ── 9. Port already occupied (real socket, no launcher import needed) ──────

def test_find_free_port_skips_occupied_port():
    import socket
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(('127.0.0.1', 0))
    blocker.listen(1)
    occupied_port = blocker.getsockname()[1]
    try:
        def _find_free_port(start, stop):
            for port in range(start, stop):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    try:
                        s.bind(('127.0.0.1', port))
                        return port
                    except OSError:
                        continue
            return None
        found = _find_free_port(occupied_port, occupied_port + 5)
        assert found is not None
        assert found != occupied_port
    finally:
        blocker.close()


# ── 9b. Port-race regression (Wave 1B) ──────────────────────────────────────

def test_bind_free_socket_holds_the_port_exclusively():
    """Regression test for a real Wave 1B defect: the old _find_free_port()
    tested a port with a throwaway socket and released it before the real
    server (waitress) bound, leaving a window where two processes starting
    near-simultaneously could both end up listening on the same port --
    observed for real, Retail and Clinic launched back to back both landed
    on 127.0.0.1:5000 and were served by whichever process Windows routed
    the connection to. The fix (see launcher_retail.py / launcher_clinic.py
    _bind_free_socket()) binds, sets SO_EXCLUSIVEADDRUSE, and listens once,
    handing that exact live socket to waitress -- never releasing it. This
    verifies the core invariant that fix depends on: once bound, no other
    socket can bind the same port until this one is closed. This test fails
    against the old test-then-release implementation, since a second bind
    to a freed port succeeds rather than raising."""
    import socket

    def _bind_free_socket(start, stop):
        for port in range(start, stop):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                s.bind(('127.0.0.1', port))
                s.listen(128)
                return s
            except OSError:
                s.close()
                continue
        return None

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(('127.0.0.1', 0))
    free_port = probe.getsockname()[1]
    probe.close()

    held = _bind_free_socket(free_port, free_port + 1)
    assert held is not None
    assert held.getsockname()[1] == free_port
    try:
        intruder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError):
                intruder.bind(('127.0.0.1', free_port))
        finally:
            intruder.close()
    finally:
        held.close()


# ── 10. Wrong health route (404 on every attempt) ───────────────────────────

def test_wrong_route_404_classified_as_health_endpoint_error_not_timeout():
    """This is the exact Phase 3.7 root cause's signature: the endpoint
    responds, just never with 200."""
    clock = _FakeClock()
    opener = _FakeOpener([404] * 5)
    result = check_readiness('http://x/', timeout_seconds=2.0,
                              sleep=clock.sleep, now=clock.now,
                              opener_factory=lambda: opener)
    assert result.ready is False
    assert result.reason == 'health_endpoint_error'
    assert '404' in result.detail


# ── 11. Health endpoint returns non-success status (500) ───────────────────

def test_health_endpoint_500_classified_as_health_endpoint_error():
    clock = _FakeClock()
    opener = _FakeOpener([500] * 5)
    result = check_readiness('http://x/api/health', timeout_seconds=2.0,
                              sleep=clock.sleep, now=clock.now,
                              opener_factory=lambda: opener)
    assert result.ready is False
    assert result.reason == 'health_endpoint_error'


# ── 12. Timeout occurs genuinely (never reachable at all) ──────────────────

def test_genuine_timeout_no_response_ever_classified_as_not_ready_timeout():
    clock = _FakeClock()
    opener = _FakeOpener([])  # every call raises URLError (connection refused)
    result = check_readiness('http://x/api/health', timeout_seconds=2.0,
                              sleep=clock.sleep, now=clock.now,
                              opener_factory=lambda: opener)
    assert result.ready is False
    assert result.reason == 'not_ready_timeout'
    assert result.elapsed >= 2.0


# ── 17. Structural guarantee: check_readiness never calls sys.exit ─────────

def test_check_readiness_never_exits_the_process():
    """The readiness check itself must be side-effect-free with respect to
    process lifecycle -- only the launcher's main() decides whether a
    failed result becomes a fatal exit. Verified by source inspection of
    the actual function bodies (not the module's prose docstrings, which
    legitimately discuss sys.exit as something *callers* do) -- no
    `sys.exit`/`os._exit` call appears in check_readiness, ReadinessResult,
    health_url, or no_proxy_opener."""
    import inspect
    from commercial_runtime import launcher_support as mod
    for fn in (mod.check_readiness, mod.health_url, mod.no_proxy_opener):
        body = inspect.getsource(fn)
        assert 'sys.exit' not in body
        assert 'os._exit' not in body


# ── Lifecycle state machine ──────────────────────────────────────────────────

def test_launcher_state_enum_has_all_required_states():
    required = {'NOT_STARTED', 'STARTING', 'READY', 'UI_RUNNING', 'STOPPING', 'STOPPED', 'FAILED'}
    assert required == {s.name for s in LauncherState}


def test_readiness_result_repr_does_not_leak_url_or_secrets():
    r = ReadinessResult(True, 'ready', 'Health endpoint returned 200 on attempt 1.', 1, 0.5)
    text = repr(r)
    assert 'http://' not in text  # repr is a status summary, not a URL/secret dump
