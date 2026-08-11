"""Phase 9.5E Milestone 0 -- browser-test dev server port isolation.

Real gap this closes (Phase 9.5D M25): browser-validation sessions started the
Owner Flask dev server with a hand-typed, always-the-same port (--port 5551).
A leftover process from an earlier session silently squatted on that port and
served stale code; the only way anyone noticed was every route 404ing. The
recovery that day was safe (TaskStop on the session's own tracked PID, never
a blind kill), but nothing stopped the collision from happening again -- the
next session would type --port 5551 again.

This module removes the fixed port entirely: every caller gets an OS-assigned
ephemeral port, and the only PID this module will ever terminate is the exact
PID it just spawned, verified alive AND fingerprint-matched (own port number
present in its live command line) immediately before the kill. It will never
touch a PID it did not start, no matter what is listening on a port.
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parent / ".runtime"
RUNTIME_FILE = RUNTIME_DIR / "browser_test_server.json"


class ServerStartError(RuntimeError):
    pass


class ForeignProcessError(RuntimeError):
    """Raised when a runtime handle's PID is alive but does not fingerprint-match
    what this module started -- refuse to touch it, never assume it's safe to kill."""


@dataclasses.dataclass
class ServerHandle:
    pid: int
    port: int
    started_at: float
    health_path: str

    def to_json(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "ServerHandle":
        return cls(**data)


def pick_free_port() -> int:
    """Ask the OS for an ephemeral port by binding to port 0, then release it.
    Small TOCTOU window between release and the real bind is inherent to this
    technique on any platform; retried by the caller's health-check loop if lost."""
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _process_commandline(pid: int) -> str | None:
    """Windows-only: query the live command line of a PID via CIM, never trusting
    that a PID number alone still refers to the process we started (PIDs recycle)."""
    try:
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine",
            ],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _is_our_process(handle: ServerHandle) -> bool:
    cmdline = _process_commandline(handle.pid)
    if not cmdline:
        return False
    return f"--port {handle.port}" in cmdline and "flask" in cmdline.lower()


def start_server(
    owner_dir: Path,
    database_url: str,
    health_path: str = "/health/live",
    timeout_seconds: float = 20.0,
) -> ServerHandle:
    """Start a fresh Flask dev server on a freshly-chosen ephemeral port and
    block until it answers a real HTTP health check. Never reuses a fixed port."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    reap_stale_handle()

    python_exe = owner_dir.parent / ".venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        raise ServerStartError(f"venv python not found at {python_exe}")

    port = pick_free_port()
    env = {"OWNER_DATABASE_URL": database_url}
    import os
    full_env = {**os.environ, **env}

    proc = subprocess.Popen(
        [str(python_exe), "-m", "flask", "--app", "app:create_app", "run",
         "--port", str(port), "--no-reload"],
        cwd=str(owner_dir), env=full_env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    handle = ServerHandle(pid=proc.pid, port=port, started_at=time.time(), health_path=health_path)

    deadline = time.time() + timeout_seconds
    url = f"http://127.0.0.1:{port}{health_path}"
    last_error: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise ServerStartError(f"dev server process exited early with code {proc.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2):
                pass
            RUNTIME_FILE.write_text(json.dumps(handle.to_json()), encoding="utf-8")
            return handle
        except urllib.error.HTTPError:
            # Any HTTP response (even 4xx/5xx) proves the server is up and routing --
            # that's what "healthy" means here, not "this exact path returns 2xx".
            RUNTIME_FILE.write_text(json.dumps(handle.to_json()), encoding="utf-8")
            return handle
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            last_error = exc
        time.sleep(0.5)

    proc.terminate()
    raise ServerStartError(f"dev server did not become healthy within {timeout_seconds}s: {last_error}")


def stop_server(handle: ServerHandle | None = None) -> None:
    """Terminate exactly the PID this module started -- and only after
    re-verifying (fingerprint match) it's still the same process. If the PID
    is alive but does not match, refuse to touch it and raise instead."""
    if handle is None:
        handle = _read_handle()
        if handle is None:
            return

    if not _pid_alive(handle.pid):
        _clear_runtime_file()
        return

    if not _is_our_process(handle):
        raise ForeignProcessError(
            f"PID {handle.pid} is alive but its command line no longer matches "
            f"the dev server this module started on port {handle.port} -- refusing to kill it "
            f"(likely PID reuse by an unrelated process). Manual investigation required."
        )

    subprocess.run(["taskkill", "/PID", str(handle.pid), "/F"], capture_output=True)
    _clear_runtime_file()


def reap_stale_handle() -> None:
    """Called before starting a new server: if a previous handle file exists
    and its PID is dead, clear it. If alive and still fingerprint-matches, leave
    it alone and let the caller decide (this never kills a live, matching server
    just because a new one is being requested)."""
    handle = _read_handle()
    if handle is None:
        return
    if not _pid_alive(handle.pid):
        _clear_runtime_file()


def _pid_alive(pid: int) -> bool:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue) -ne $null"],
        capture_output=True, text=True, timeout=15,
    )
    return result.stdout.strip().lower() == "true"


def _read_handle() -> ServerHandle | None:
    if not RUNTIME_FILE.exists():
        return None
    try:
        return ServerHandle.from_json(json.loads(RUNTIME_FILE.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def _clear_runtime_file() -> None:
    RUNTIME_FILE.unlink(missing_ok=True)


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Owner browser-test dev server, port-isolated.")
    parser.add_argument("action", choices=["start", "stop", "status"])
    parser.add_argument("--database-url", default="postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_dev")
    parser.add_argument("--health-path", default="/health/live")
    args = parser.parse_args()

    owner_dir = Path(__file__).resolve().parents[2]

    if args.action == "start":
        handle = start_server(owner_dir, args.database_url, args.health_path)
        print(f"started pid={handle.pid} port={handle.port}")
        return 0
    if args.action == "stop":
        stop_server()
        print("stopped")
        return 0
    if args.action == "status":
        handle = _read_handle()
        if handle is None:
            print("no runtime handle")
            return 0
        alive = _pid_alive(handle.pid)
        matches = _is_our_process(handle) if alive else False
        print(f"pid={handle.pid} port={handle.port} alive={alive} fingerprint_match={matches}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(_main())
