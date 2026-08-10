# Phase 9.5E Milestone 0 — Browser-Test Dev-Server Port Isolation

## The real gap this closes

Phase 9.5D Milestone 25's real-browser validation hit a leftover `flask run` process from an earlier session silently squatting on the hand-typed, always-identical port `5551`, serving stale code — every new route 404'd until diagnosed via `netstat`. The recovery that day was careful (`TaskStop` on this session's own tracked PID only, never the foreign process), but nothing structurally prevented the collision from recurring, because every browser-validation session just typed `--port 5551` again from habit. A live example of the same class of hazard was still present on this machine at the start of this phase: a PID (`35088`) from a prior session's `flask run --port 5551` was still listed in `Get-CimInstance Win32_Process` when Milestone 0 began.

## What was built

`owner/tools/dev_server/port_isolation.py` — a small, tested module, no new dependencies (no `psutil`; PID liveness/command-line verification shells out to `Get-CimInstance`/`Get-Process` via PowerShell, matching this repo's existing Windows-only tooling pattern):

- **`pick_free_port()`**: binds to `("127.0.0.1", 0)`, lets the OS assign an ephemeral port, releases it. Every `start_server()` call gets a fresh port — no fixed port is ever hardcoded or reused.
- **`start_server(owner_dir, database_url, health_path="/health/live", timeout_seconds=20.0)`**: launches the real Owner Flask dev server on that port, polls the real `/health/live` endpoint (the existing Phase 9 liveness contract — process-responsive only, no DB dependency, per `app/health.py`) until it answers or the timeout elapses, then persists a runtime handle (`pid`, `port`, `started_at`, `health_path`) to `owner/tools/dev_server/.runtime/browser_test_server.json`. Any HTTP response (including 4xx) counts as "healthy" — the check proves the server is up and routing, not that any specific path returns 2xx.
- **`stop_server(handle=None)`**: terminates *only* the exact PID it started, and only after re-verifying, immediately before the kill, that the PID is still alive **and** its live command line still contains `--port <that port>` and `flask` (queried fresh via `Get-CimInstance Win32_Process`, never trusted from the stale handle file alone). If the PID is alive but no longer fingerprint-matches — i.e. it was reused by an unrelated process in the interim — `stop_server` raises `ForeignProcessError` and refuses to touch it. It never kills by port number, never scans for "whatever is listening on port X".
- **`reap_stale_handle()`**: called at the start of every `start_server()`; clears a leftover runtime-handle file only if its PID is confirmed dead — never assumes staleness from age alone.

## Proof (real, not mocked)

`owner/tests/test_phase9_5e_dev_server_port_isolation.py`, 5/5 passing:
1. `pick_free_port()` returns two distinct, usable ports on real calls.
2. A stale handle pointing at a dead PID is reaped.
3. `stop_server()` is handed a handle for a **real, live** subprocess whose command line does not fingerprint-match — proven to raise `ForeignProcessError` and leave that process running (asserted via `proc.poll() is None` after the call).
4. `stop_server()` on an already-dead PID is a clean no-op.
5. **Full real end-to-end cycle**: starts the actual Owner Flask app on a real freshly-chosen ephemeral port against the real dev database, confirms `/health/live` returns 200 over real HTTP, stops it, and confirms the PID is actually dead afterward — the same shape of proof this session has used for every other milestone (real server, real HTTP, real process table, not simulated).

## How future browser-validation sessions should use this

```
python owner/tools/dev_server/port_isolation.py start   # prints "started pid=... port=..."
# run Playwright/browser session against http://127.0.0.1:<port>
python owner/tools/dev_server/port_isolation.py stop
python owner/tools/dev_server/port_isolation.py status   # pid/port/alive/fingerprint_match, for diagnosis
```

No session needs to hand-type a port again, and no future collision with a leftover process is possible — a fresh ephemeral port is structurally distinct from whatever a prior orphaned process is still holding.
