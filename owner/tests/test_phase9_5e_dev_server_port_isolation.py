"""Phase 9.5E Milestone 0 -- proves the browser-test dev server helper never
picks a fixed port and never terminates a process it did not start itself."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dev_server"))
import port_isolation  # noqa: E402

# port_isolation drives powershell.exe / taskkill and starts .venv\Scripts\python.exe: it
# exists for browser-validation sessions on the Windows dev machine and has no Linux code
# path. On Linux CI the four tests below fail on the missing binary before reaching the
# guarantee they exist for (measured 2026-09-07: FileNotFoundError: 'powershell.exe'), so
# they run only where the helper runs. What CI therefore cannot catch: a regression in the
# never-kill-a-foreign-PID guard -- that is proven on the Windows machine where the helper
# is actually used. pick_free_port() is platform-neutral and stays unconditional.
_windows_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="port_isolation drives powershell.exe/taskkill and .venv\\Scripts\\python.exe (Windows-only dev tool)",
)


OWNER_DIR = Path(__file__).resolve().parents[1]
DATABASE_URL = "postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_dev"


@pytest.fixture(autouse=True)
def clean_runtime_file():
    port_isolation._clear_runtime_file()
    yield
    port_isolation._clear_runtime_file()


def test_pick_free_port_returns_usable_distinct_ports():
    a = port_isolation.pick_free_port()
    b = port_isolation.pick_free_port()
    assert a != b
    assert 1024 < a < 65536
    assert 1024 < b < 65536


@_windows_only
def test_reap_stale_handle_clears_dead_pid():
    dead_handle = port_isolation.ServerHandle(pid=999999, port=59999, started_at=time.time(), health_path="/login")
    port_isolation.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    port_isolation.RUNTIME_FILE.write_text(
        __import__("json").dumps(dead_handle.to_json()), encoding="utf-8"
    )
    assert port_isolation._read_handle() is not None

    port_isolation.reap_stale_handle()

    assert port_isolation._read_handle() is None


@_windows_only
def test_stop_server_refuses_to_kill_a_live_non_matching_process():
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        fake_handle = port_isolation.ServerHandle(
            pid=proc.pid, port=54321, started_at=time.time(), health_path="/login"
        )

        with pytest.raises(port_isolation.ForeignProcessError):
            port_isolation.stop_server(fake_handle)

        assert proc.poll() is None, "stop_server must never touch a PID whose command line doesn't fingerprint-match"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@_windows_only
def test_stop_server_is_a_noop_when_pid_already_dead():
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    proc.wait(timeout=5)
    dead_pid = proc.pid

    handle = port_isolation.ServerHandle(pid=dead_pid, port=54322, started_at=time.time(), health_path="/login")
    port_isolation.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    port_isolation.RUNTIME_FILE.write_text(
        __import__("json").dumps(handle.to_json()), encoding="utf-8"
    )

    port_isolation.stop_server(handle)

    assert port_isolation._read_handle() is None


@pytest.mark.slow
@_windows_only
def test_real_server_start_health_check_and_stop_cycle():
    """Real end-to-end proof against the real Owner Flask app on a real,
    freshly-chosen ephemeral port -- not a fixed port, not a mock."""
    handle = port_isolation.start_server(OWNER_DIR, DATABASE_URL, health_path="/health/live", timeout_seconds=30.0)
    try:
        assert port_isolation._pid_alive(handle.pid)
        assert port_isolation._is_our_process(handle)

        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{handle.port}/health/live", timeout=5) as resp:
            assert resp.status == 200
    finally:
        port_isolation.stop_server(handle)

    time.sleep(1)
    assert not port_isolation._pid_alive(handle.pid)
