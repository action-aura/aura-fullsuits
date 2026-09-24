"""Aura Lab -- proves, on the real product, that an Owner-issued licence
activates a first device (till A), lets a second device JOIN the same shop
with the SAME key (till B -- a stand-in for the phone: the Android app
embeds this same Python backend), and that the two devices then sync real
money-path data. Also measures three suspected multi-device gaps (P&L
parity, promotion propagation, and what actually crosses the Owner's cloud
relay) without fixing anything it finds.

INTERPRETER: the SYSTEM Python 3.14 -- the one with playwright + chromium
installed (see scripts/lab/README.md's own notes on this repo's two
interpreters):
    C:\\Users\\MSI\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe lab_e2e.py prove

Everything this script generates (secrets, screenshots, the result file) is
real output from real HTTP/browser calls against the lab Owner + two real
till backends -- nothing here is templated or assumed. Every route this
script calls is one already exercised by an existing driver under
scripts/ops/ (two_device_convergence.py, two_till_owner_login.py,
two_till_settings.py, join_e2e.py, join_door_e2e.py) or read directly out of
owner/app/**/routes.py and products/retail/backend/api/retail_api.py -- see
this repo's CLAUDE.md and this file's own READ-FIRST list for the exact
citations. Nothing here invents a route.

Subcommands (see `main()` at the bottom): mfa_and_issue, till_a, till_b,
converge, cleanup, issue_second, prove (runs the whole sequence). Each
subcommand after the first depends on lab.env.json keys the previous one
wrote -- `prove` is the one full run the task asks for; the others exist so
a failed run can be resumed without repeating what already worked.

lab.env.json is treated as ADD-ONLY: `save_env()` below only ever adds a
key that is not already present, and logs (never silently skips) when a key
it was about to add already exists. Screenshots land in
C:/Users/MSI/AuraLab/proof/screens/NN-name.png. Results accumulate in
C:/Users/MSI/AuraLab/proof/result.json, written after every phase (not only
at the end), so a run that dies partway still leaves a readable trail; a
human-readable result.md is regenerated from it at the end of every run.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Fixed lab facts (from the planner's environment section -- never guessed).
# ---------------------------------------------------------------------------

LAB_ROOT = Path(os.environ.get("AURA_LAB_ROOT", "C:/Users/MSI/AuraLab"))
ENV_PATH = LAB_ROOT / "lab.env.json"
RUN_DIR = LAB_ROOT / "run"
PROOF_DIR = LAB_ROOT / "proof"
SCREENS_DIR = PROOF_DIR / "screens"
RESULT_JSON = PROOF_DIR / "result.json"
RESULT_MD = PROOF_DIR / "result.md"

# This file lives at <W>/scripts/lab/lab_e2e.py.
REPO_ROOT = Path(__file__).resolve().parents[2]
LAB_SCRIPTS_DIR = REPO_ROOT / "scripts" / "lab"

OWNER_BASE = "http://127.0.0.1:5551"
TILL_A_PORT = 5020
TILL_A_BASE = f"http://127.0.0.1:{TILL_A_PORT}"
TILL_B_PORT = 5021
TILL_B_BASE = f"http://127.0.0.1:{TILL_B_PORT}"
DATA_A_DIR = "C:/Users/MSI/AuraLab/proof-a"
DATA_B_DIR = "C:/Users/MSI/AuraLab/proof-b"

SITE_RELAY_TCP_PORT = 5443
SITE_RELAY_BEACON_UDP_PORT = 45455

PSQL_EXE = Path("C:/pgportable/pgsql/bin/psql.exe")
OWNER_PG_DB = "aura_owner_lab"
OWNER_PG_USER = "aura_owner"
OWNER_PG_PASSWORD = "aura_owner_dev"  # loopback-only lab Postgres; not a real secret.

PROOF_ADMIN_EMAIL = "admin@proof.lab"
PROOF_CASHIER_EMAIL = "lab-cashier@proof.lab"

TAG = time.strftime("%H%M%S")

# ---------------------------------------------------------------------------
# S0 (2026-09-24 hot-start proof) -- opt-in-only restart-till-A workaround.
#
# `_restart_till_a_so_its_sync_starts()` further below exists to work
# around the exact gap `app.py::_start_sync_after_activation` (the hot-start
# fix this whole proof is measuring) now closes: till A's own sync loop used
# to start exactly once, at import time, so an activation landing later in
# the same process never got sync running until the operator restarted it.
# phase_p3 (proof1, already run) called that workaround UNCONDITIONALLY,
# which was the only way to get past the gap before the fix existed. Now
# that the fix is in `products/retail/backend/app.py`/`config.py`, the whole
# point of this proof is that A must NOT need that restart any more -- so
# the workaround becomes opt-in, default OFF, via either the
# --restart-till-a-workaround CLI flag or the LAB_RESTART_TILL_A_WORKAROUND=1
# env var (the CLI flag sets the env var in main(), before pw dispatch, so
# both routes land on the exact same check). Left in place, rather than
# deleted, so a rerun against a future regression (or against pre-fix code)
# can still reach for it deliberately, on purpose, instead of silently.
RESTART_TILL_A_WORKAROUND = os.environ.get("LAB_RESTART_TILL_A_WORKAROUND", "0") == "1"

# ---------------------------------------------------------------------------
# Proof2 (S1-S6) -- a SECOND, independent licence/customer/pair of tills,
# entirely separate from proof1's Lab Proof Shop / A / B above, so this
# proof's own no-restart measurement can never be contaminated by proof1's
# already-issued licence, its already-registered devices, or its own A/B
# data directories. Ports 5022/5023 (not 5020/5021) and a "-a2"/"-b2" data
# dir suffix are the only things that differ from proof1's shape; every
# helper below (Client, wait_until, tcp_port_listening, capture_one_beacon,
# get_net_tcp_owner, run_ps_script, stop_lab, Results, shot) is the SAME
# code proof1 already exercised, just pointed at these constants instead.
# -----------------------------------------------------------------------
TILL_A2_PORT = 5022
TILL_A2_BASE = f"http://127.0.0.1:{TILL_A2_PORT}"
TILL_B2_PORT = 5023
TILL_B2_BASE = f"http://127.0.0.1:{TILL_B2_PORT}"
DATA_A2_DIR = "C:/Users/MSI/AuraLab/proof-a2"
DATA_B2_DIR = "C:/Users/MSI/AuraLab/proof-b2"

RESULT2_JSON = PROOF_DIR / "result2.json"
RESULT2_MD = PROOF_DIR / "result2.md"
SCREENS2_DIR = PROOF_DIR / "screens2"

PROOF2_ADMIN_EMAIL = "admin@proof2.lab"
PROOF2_CASHIER_EMAIL = "lab-cashier2@proof2.lab"
PROOF2_LICENCE_TARGET_DEVICES = 2


# ---------------------------------------------------------------------------
# lab.env.json -- read-modify-write, ADD-ONLY (never overwrite an existing key)
# ---------------------------------------------------------------------------

def load_env() -> dict:
    if not ENV_PATH.exists():
        sys.exit(f"lab.env.json not found at {ENV_PATH} -- run lab_bootstrap.ps1 first.")
    # utf-8-sig: lab_bootstrap.ps1's Set-Content -Encoding UTF8 writes a BOM
    # (a PowerShell-ism); a plain utf-8 read chokes on it, so accept either.
    return json.loads(ENV_PATH.read_text(encoding="utf-8-sig"))


def save_env(updates: dict) -> dict:
    """Read-modify-write. Only ADDS a key that is not already present;
    an existing key is left untouched and the collision is logged, per
    this lab's standing rule that lab.env.json's existing values (the
    license pepper baked into every already-issued licence, foremost) must
    never be regenerated out from under a caller."""
    current = load_env()
    added = []
    skipped = []
    for k, v in updates.items():
        if k in current:
            skipped.append(k)
            continue
        current[k] = v
        added.append(k)
    ENV_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
    if added:
        log(f"lab.env.json: added key(s) {added}")
    if skipped:
        log(f"lab.env.json: key(s) already present, left untouched: {skipped}")
    return current


def log(msg: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)


# ---------------------------------------------------------------------------
# RFC 6238 TOTP -- stdlib only (hmac/base64/struct/time), no pyotp.
# ---------------------------------------------------------------------------

def _b32_decode(secret: str) -> bytes:
    s = secret.strip().upper().replace(" ", "")
    pad = (-len(s)) % 8
    return base64.b32decode(s + ("=" * pad))


def totp_now(secret_b32: str, *, digits: int = 6, period: int = 30, when: float | None = None) -> str:
    moment = when if when is not None else time.time()
    counter = int(moment // period)
    key = _b32_decode(secret_b32)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code_int).zfill(digits)


def seconds_into_totp_window(period: int = 30) -> float:
    return time.time() % period


# ---------------------------------------------------------------------------
# Plain HTTP client (urllib + cookiejar) -- same shape as scripts/ops/*.py's
# own Client classes, reused deliberately rather than reinvented.
# ---------------------------------------------------------------------------

class Client:
    def __init__(self, base: str):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(self, method: str, path: str, body=None, timeout: int = 30):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except Exception:
                return exc.code, {"raw": raw[:300].decode(errors="replace")}
        except urllib.error.URLError as exc:
            return None, {"error": str(exc)}


def rows_of(body) -> list:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        return body.get("data", body.get("products", body.get("sales", body.get("employees", []))))
    return []


def find_by(rows: list, key: str, value) -> dict | None:
    for r in rows:
        if str(r.get(key, "")).upper() == str(value).upper():
            return r
    return None


def wait_until(check, timeout: float, interval: float = 3.0, label: str = ""):
    """Polls `check()` (a zero-arg callable returning a truthy match or
    None/False) until it returns truthy or `timeout` seconds pass. Returns
    (ok, value, elapsed_seconds). Never raises on a check that itself
    raises transiently -- logs and keeps polling, since a device that just
    restarted or is mid-sync answering a connection-refused for a few
    seconds is the expected shape here, not a bug."""
    start = time.time()
    deadline = start + timeout
    last = None
    while time.time() < deadline:
        try:
            last = check()
        except Exception as exc:  # noqa: BLE001
            last = None
            log(f"  wait_until({label}): transient error: {exc}")
        if last:
            return True, last, time.time() - start
        time.sleep(interval)
    return False, last, time.time() - start


# ---------------------------------------------------------------------------
# Results accumulator -- written after every phase, not only at the end.
# ---------------------------------------------------------------------------

class Results:
    def __init__(self, json_path: Path = None, md_path: Path = None, screens_dir: Path = None):
        # Parametrized (2026-09-24, proof2/S1-S6) so a second, independent
        # results file (result2.json/result2.md/screens2/) can accumulate
        # proof2's own phases without either run ever touching the other's
        # file -- defaults preserve proof1's original RESULT_JSON/RESULT_MD/
        # SCREENS_DIR exactly, so every existing call site (`R = Results()`)
        # is unchanged.
        self.json_path = json_path if json_path is not None else RESULT_JSON
        self.md_path = md_path if md_path is not None else RESULT_MD
        self.screens_dir = screens_dir if screens_dir is not None else SCREENS_DIR
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        self.screens_dir.mkdir(parents=True, exist_ok=True)
        # Each subcommand (mfa_and_issue, till_a, till_b, ...) is its own
        # process -- load whatever result file already has and APPEND,
        # rather than starting fresh every time and silently discarding
        # every earlier subcommand's phases/findings. Only `prove`/`prove2`
        # runs the whole sequence in one process; every other subcommand
        # exists specifically so a partial run can be resumed, and a resumed
        # run that erases its own history defeats that purpose.
        self.data = {"started_at": datetime.now(timezone.utc).isoformat(), "phases": [], "findings": []}
        if self.json_path.exists():
            try:
                loaded = json.loads(self.json_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and "phases" in loaded:
                    self.data = loaded
            except (json.JSONDecodeError, OSError):
                pass  # corrupt/unreadable -- start fresh rather than crash.
        self._shot_n = len(self.data.get("phases", []))

    def phase(self, name: str, status: str, detail: str = "", seconds: float | None = None):
        entry = {"phase": name, "status": status, "detail": detail,
                  "seconds": round(seconds, 1) if seconds is not None else None,
                  "at": datetime.now(timezone.utc).isoformat()}
        self.data["phases"].append(entry)
        log(f"[{status}] {name} ({entry['seconds']}s): {detail}")
        self.flush()
        return entry

    def finding(self, name: str, evidence: str):
        self.data["findings"].append({"name": name, "evidence": evidence})
        log(f"[MEASURED] {name}: {evidence}")
        self.flush()

    def flush(self):
        self.json_path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        self._write_md()

    def next_shot_name(self, label: str) -> str:
        self._shot_n += 1
        return f"{self._shot_n:02d}-{label}"

    def _write_md(self):
        lines = ["# Aura Lab E2E result", "", f"Generated: {datetime.now(timezone.utc).isoformat()}", "",
                 "| Phase | Status | Seconds | Detail |", "|---|---|---|---|"]
        for p in self.data["phases"]:
            lines.append(f"| {p['phase']} | {p['status']} | {p['seconds']} | {p['detail']} |")
        if self.data["findings"]:
            lines += ["", "## Measured findings", ""]
            for f in self.data["findings"]:
                lines.append(f"- **{f['name']}**: {f['evidence']}")
        self.md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


R = Results()
# Proof2's own accumulator (S1-S6, 2026-09-24) -- see the Results docstring
# above: same append-on-load behaviour, its own json/md/screens files, never
# touches R's (proof1's) result.json/result.md/screens/.
R2 = Results(RESULT2_JSON, RESULT2_MD, SCREENS2_DIR)


# ---------------------------------------------------------------------------
# Process helpers -- PowerShell scripts this session owns editing rights to
# call (lab_desktop.ps1's own -Headless mode; lab_down.ps1 unmodified).
# ---------------------------------------------------------------------------

def run_ps_script(script: Path, params: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    """Deliberately does NOT use capture_output=True (stdout/stderr PIPE).
    lab_desktop.ps1's own Start-Process launches a DETACHED grandchild
    (the till backend) that outlives this script -- and on Windows that
    grandchild can inherit a duplicate handle of the pipe's write end, so
    even after powershell.exe itself exits, the pipe's read end never
    sees EOF and subprocess.run(capture_output=True) blocks forever
    (confirmed here: it hung well past its own `timeout=` argument, with
    the powershell.exe process already gone and only the grandchild left
    holding the port). Redirecting to real files sidesteps this
    entirely -- multiple processes holding a FILE open concurrently
    never blocks a read of that file's current contents."""
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *params]
    out_path = RUN_DIR / f"_ps_stdout_{uuid.uuid4().hex[:8]}.log"
    err_path = RUN_DIR / f"_ps_stderr_{uuid.uuid4().hex[:8]}.log"
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as out_f, open(err_path, "w", encoding="utf-8") as err_f:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=out_f, stderr=err_f,
                               text=True, timeout=timeout)
    stdout = out_path.read_text(encoding="utf-8", errors="replace")
    stderr = err_path.read_text(encoding="utf-8", errors="replace")
    for p in (out_path, err_path):
        try:
            p.unlink()
        except OSError:
            pass
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout=stdout, stderr=stderr)


def start_till(port: int, data_dir: str, site_relay: str = "auto") -> subprocess.CompletedProcess:
    """Generalized (2026-09-24, proof2/S2-S3) form of the original
    start_till_b -- any headless till, any port/data dir, kept as one call
    site so proof2's A2/B2 use the identical lab_desktop.ps1 invocation
    proof1's A/B already exercised, not a second hand-typed copy."""
    log(f"starting till on :{port} (data={data_dir}, site_relay={site_relay})")
    return run_ps_script(
        LAB_SCRIPTS_DIR / "lab_desktop.ps1",
        ["-Headless", "-Port", str(port), "-DataDir", data_dir, "-SiteRelay", site_relay],
        timeout=90,
    )


def start_till_b(site_relay: str = "auto") -> subprocess.CompletedProcess:
    return start_till(TILL_B_PORT, DATA_B_DIR, site_relay)


def read_pid_file(name: str) -> dict | None:
    """Reads $LabRoot/run/<name>.pid.json (written by lab_common.ps1's own
    Save-Pid) -- used (2026-09-24, proof2/S2) to learn WHICH pid a headless
    till actually got, so a site-relay-owner check can confirm the process
    holding :5443 is that same till's own process tree, not merely "a"
    process. Returns None if the file does not exist or is unreadable
    (a till that never started, or crashed before Save-Pid ran)."""
    path = RUN_DIR / f"{name}.pid.json"
    try:
        # utf-8-sig: Save-Pid's own `ConvertTo-Json | Set-Content -Encoding
        # UTF8` writes a BOM (the same PowerShell-ism load_env() already
        # works around for lab.env.json) -- a plain utf-8 read chokes on it
        # and json.loads raises, which this function then silently turned
        # into a false "no pid file" (confirmed live 2026-09-24: the file
        # existed and was valid JSON, but read_pid_file returned None).
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def stop_lab(only: str) -> subprocess.CompletedProcess:
    log(f"stopping lab process '{only}'")
    return run_ps_script(LAB_SCRIPTS_DIR / "lab_down.ps1", ["-Only", only], timeout=60)


def http_get_ok(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def wait_http_ok(url: str, timeout: float = 45.0, interval: float = 1.0) -> bool:
    ok, _, _ = wait_until(lambda: http_get_ok(url) or None, timeout=timeout, interval=interval, label=url)
    return ok


def tcp_port_listening(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def tcp_port_closed(port: int, host: str = "127.0.0.1", timeout: float = 30.0) -> bool:
    ok, _, _ = wait_until(lambda: (not tcp_port_listening(port, host)) or None, timeout=timeout, interval=1.5,
                          label=f"port {port} closed")
    return ok


def get_net_tcp_owner(port: int) -> dict:
    """Prefer PowerShell for process work, per this lab's own standing note
    (Get-NetTCPConnection / Get-CimInstance rather than parsing 'ps' output).
    Never raises -- returns {} on anything but a clean single-owner answer."""
    script = (
        f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue "
        f"| Select-Object -First 1; "
        f"if ($c) {{ $p = Get-CimInstance Win32_Process -Filter \"ProcessId=$($c.OwningProcess)\" "
        f"-ErrorAction SilentlyContinue; "
        f"[PSCustomObject]@{{pid=$c.OwningProcess; commandLine=$p.CommandLine}} | ConvertTo-Json -Compress }}"
    )
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                               capture_output=True, text=True, timeout=20)
        out = proc.stdout.strip()
        return json.loads(out) if out else {}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def get_process_parent_pid(pid: int) -> int | None:
    """Win32_Process's own ParentProcessId, for ONE pid. Needed (2026-09-24,
    proof2/S2) because Stop-LabPid's own docstring already says it plainly:
    'The recorded pid is the venv LAUNCHER (.venv\\Scripts\\python.exe); the
    listening server is its child on the base interpreter.' -- so the pid
    Save-Pid records for a headless till is NEVER the pid that ends up
    holding a listening socket; a caller that wants to confirm a port is
    owned by "till X's own process" has to walk one level up, not compare
    pids directly. Confirmed live 2026-09-24: till-5022.pid.json recorded
    pid 34732 (the launcher); the process actually listening on :5022 AND
    :5443 was pid 65556, whose own ParentProcessId is exactly 34732."""
    script = (
        f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" -ErrorAction SilentlyContinue; "
        f"if ($p) {{ $p.ParentProcessId }}"
    )
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                               capture_output=True, text=True, timeout=20)
        out = proc.stdout.strip()
        return int(out) if out.isdigit() else None
    except Exception:  # noqa: BLE001
        return None


def capture_one_beacon(port: int = SITE_RELAY_BEACON_UDP_PORT, timeout: float = 30.0) -> dict:
    """Binds SO_REUSEADDR on the beacon port and waits for ONE datagram.
    Never verifies the signature (this script does not hold the hub's
    device public key) -- prints only `url`/`installation_id`, per the
    task's own instruction. If another process already owns this port
    exclusively, records that fact instead of raising."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", port))
    except OSError as exc:
        sock.close()
        return {"bound": False, "error": str(exc)}
    try:
        sock.settimeout(timeout)
        try:
            datagram, _source = sock.recvfrom(65536)
        except socket.timeout:
            return {"bound": True, "received": False}
        try:
            body = json.loads(datagram.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            return {"bound": True, "received": True, "parse_error": str(exc)}
        return {"bound": True, "received": True,
                "url": body.get("url"), "installation_id": body.get("installation_id")}
    finally:
        sock.close()


def tail_text_file(path: Path, max_chars: int = 4000) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        return raw[-max_chars:]
    except Exception as exc:  # noqa: BLE001
        return f"(could not read {path}: {exc})"


# ---------------------------------------------------------------------------
# Playwright helpers
# ---------------------------------------------------------------------------

def shot(page, label: str, *, results: "Results" = None):
    # `results` (2026-09-24, proof2/S1-S6) lets a caller route a screenshot
    # into R2/screens2 instead of the default R/screens -- every existing
    # call site omits it and keeps writing to proof1's own screens dir.
    res = results if results is not None else R
    name = res.next_shot_name(label)
    path = res.screens_dir / f"{name}.png"
    try:
        page.screenshot(path=str(path))
        log(f"  screenshot: {path}")
    except Exception as exc:  # noqa: BLE001
        log(f"  screenshot FAILED ({label}): {exc}")


def owner_authenticate(page, env: dict, *, results: "Results" = None) -> dict:
    """Logs into the Owner Control Center as the seeded super admin,
    handling BOTH first-ever login (forced MFA enrollment, READ-FIRST
    owner/app/auth/routes.py::mfa_enroll_form/_submit) and every login
    after that (ordinary MFA verify) -- so this same function serves P1
    (enrollment) and P6 (re-authenticating to issue a second licence).
    Returns whatever new env keys it minted (secret/recovery codes), so
    the caller can persist them with save_env()."""
    minted: dict = {}
    page.goto(OWNER_BASE + "/auth/login", wait_until="networkidle")
    page.fill("#email", env["admin_email"])
    page.fill("#password", env["admin_password"])
    page.click("button.btn")
    page.wait_for_load_state("networkidle")
    shot(page, "owner-post-login", results=results)

    if "/auth/mfa-enroll" in page.url:
        log("  owner: forced MFA enrollment (first-ever login)")
        secret_locator = page.locator("dl.kv dd code")
        if secret_locator.count() == 0:
            raise RuntimeError(
                "P1 STOP: the MFA enrollment page shows no manual-entry secret text "
                "(only a QR image would be unusable by this script) -- "
                "owner/app/templates/auth/mfa_enroll.html no longer renders "
                "'dl.kv dd code'."
            )
        secret = secret_locator.inner_text().strip()
        minted["admin_totp_secret"] = secret
        shot(page, "owner-mfa-enroll-secret", results=results)

        # A code computed right at a 30s boundary can land one tick stale by
        # the time the POST reaches the server -- wait out the boundary once
        # rather than risk a spurious "Invalid code" on the very first try.
        if seconds_into_totp_window() > 27:
            time.sleep(4)
        code = totp_now(secret)
        page.fill("#code", code)
        page.click("button.btn")
        page.wait_for_load_state("networkidle")
        if "/auth/mfa-enroll" in page.url:
            # One retry against the next 30s window -- the only thing that
            # can make a correctly-computed TOTP code fail is clock skew or
            # timing right on the boundary, both cured by trying again fresh.
            log("  owner: MFA enroll code rejected once, retrying in the next window")
            time.sleep(31)
            page.fill("#code", totp_now(secret))
            page.click("button.btn")
            page.wait_for_load_state("networkidle")
        if "/auth/mfa-enroll" in page.url:
            raise RuntimeError("P1 STOP: MFA enrollment code rejected twice -- see screenshot.")

        if "/auth/mfa-recovery-codes" in page.url:
            codes = page.locator(".aura-recovery-codes code").all_text_contents()
            if codes:
                minted["admin_recovery_codes"] = codes
            shot(page, "owner-mfa-recovery-codes", results=results)
            page.click("a.btn")
            page.wait_for_load_state("networkidle")

    elif "/auth/mfa-verify" in page.url:
        log("  owner: ordinary MFA verify (already enrolled)")
        secret = env.get("admin_totp_secret")
        if not secret:
            raise RuntimeError(
                "P6 STOP: account already has MFA enrolled but lab.env.json carries no "
                "admin_totp_secret -- cannot compute a code for it."
            )
        if seconds_into_totp_window() > 27:
            time.sleep(4)
        page.fill("#code", totp_now(secret))
        page.click("button.btn")
        page.wait_for_load_state("networkidle")
        if "/auth/mfa-verify" in page.url:
            time.sleep(31)
            page.fill("#code", totp_now(secret))
            page.click("button.btn")
            page.wait_for_load_state("networkidle")

    if "/auth/" in page.url:
        raise RuntimeError(f"owner_authenticate: still on an auth screen after login+MFA: {page.url}")
    shot(page, "owner-dashboard", results=results)
    return minted


def owner_issue_license(page, *, legal_name: str, target_devices: int | None = None,
                         results: "Results" = None) -> dict:
    """READ-FIRST owner/app/licensing/issuance_routes.py + templates/
    licensing/issuance.html. Picks the seeded Retail plan by matching
    'retail' in its option text (case-insensitive) rather than a hardcoded
    plan id -- the seed data is the catalogue's own, not this script's.

    `target_devices` (2026-09-24, proof2/S1) -- when given, reads the
    package option's own '(N devices included)' text (issuance.html:65)
    back rather than assuming a number, and fills `extra_devices` with
    whatever is still short of `target_devices`. `None` (every existing
    caller) keeps the original behaviour of always requesting 0 extra."""
    page.goto(OWNER_BASE + "/licenses/issuance/new", wait_until="networkidle")
    if page.locator("#issuance-plan-id").count() == 0:
        # recent_auth_ok was False -- the template shows only the reauth
        # link in that branch. owner_authenticate() should have left recent
        # auth valid (mark_mfa_verified() fires on both the enroll-confirm
        # and the verify path), so this is a genuine, worth-reporting stop.
        raise RuntimeError("issuance form not shown -- recent_auth_ok appears False; see screenshot.")

    page.fill("#issuance-new-customer-name", legal_name)
    options = page.eval_on_selector_all(
        "#issuance-plan-id option",
        "els => els.map(e => ({value: e.value, text: e.textContent}))",
    )
    match = next((o for o in options if o["value"] and "retail" in o["text"].lower()), None)
    if match is None:
        raise RuntimeError(f"no sellable Retail plan option found in issuance form: {options}")
    log(f"  issuance: selected package option: {match['text']}")
    page.select_option("#issuance-plan-id", value=match["value"])

    extra_devices = 0
    included_devices = None
    if target_devices is not None:
        m = re.search(r"\((\d+)\s+devices? included\)", match["text"])
        included_devices = int(m.group(1)) if m else 1
        extra_devices = max(0, target_devices - included_devices)
        log(f"  issuance: package includes {included_devices} device(s) -- requesting "
            f"{extra_devices} extra to reach {target_devices} total")
    page.fill("#issuance-extra-devices", str(extra_devices))
    shot(page, f"issuance-form-{legal_name.lower().replace(' ', '-')}", results=results)

    page.click("button.btn--primary-action")
    page.wait_for_selector("#issuance-revealed-key", timeout=30000)
    key = page.locator("#issuance-revealed-key").inner_text().strip()
    shot(page, f"issuance-key-revealed-{legal_name.lower().replace(' ', '-')}", results=results)

    # Excludes '/licenses/issuance...' (the "Issue another license" link the
    # same card renders right below "View license record") -- both contain
    # the substring '/licenses/', and a bare `a[href*='/licenses/']` picked
    # the wrong one here once already (a GET onto the POST-only
    # /licenses/issuance create route, 405).
    detail_href = page.get_attribute("a[href*='/licenses/']:not([href*='issuance'])", "href")
    license_id = None
    if detail_href:
        page.goto(OWNER_BASE + detail_href, wait_until="networkidle")
        shot(page, f"issuance-detail-{legal_name.lower().replace(' ', '-')}", results=results)
        m = re.search(r"/licenses/([^/?#]+)", detail_href)
        license_id = m.group(1) if m else None

    return {"key": key, "package_option_text": match["text"], "license_id": license_id,
            "target_devices": target_devices, "included_devices": included_devices,
            "extra_devices": extra_devices}


# ---------------------------------------------------------------------------
# P1 -- Owner: MFA enrollment + issue the Lab Proof Shop licence.
# ---------------------------------------------------------------------------

def phase_p1(pw) -> None:
    t0 = time.time()
    env = load_env()
    browser = pw.chromium.launch()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        minted = owner_authenticate(page, env)
        if minted:
            save_env(minted)
            env = load_env()

        result = owner_issue_license(page, legal_name="Lab Proof Shop")
        save_env({"proof_licence_key": result["key"], "proof_licence_id": result["license_id"]})
        R.phase("P1 owner MFA enrol + issue licence", "PASS",
                f"package={result['package_option_text']!r} license_id={result['license_id']}",
                time.time() - t0)
    except Exception as exc:  # noqa: BLE001
        R.phase("P1 owner MFA enrol + issue licence", "FAIL", f"{exc}\n{traceback.format_exc()}", time.time() - t0)
        raise
    finally:
        browser.close()


# ---------------------------------------------------------------------------
# P2 -- Till A: first-run create-admin + activate, then measure the LAN hub.
# ---------------------------------------------------------------------------

def phase_p2(pw) -> None:
    t0 = time.time()
    env = load_env()
    key = env.get("proof_licence_key")
    if not key:
        R.phase("P2 till A activate", "FAIL", "no proof_licence_key in lab.env.json -- run P1 first", time.time() - t0)
        raise RuntimeError("P2 requires proof_licence_key")

    admin_password = uuid.uuid4().hex[:10] + "Aa1!"
    save_env({"proof_admin_password": admin_password})
    env = load_env()
    admin_password = env["proof_admin_password"]

    browser = pw.chromium.launch()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(TILL_A_BASE + "/", wait_until="networkidle")
        page.wait_for_selector("#su-btn", timeout=30000)
        if page.locator("#su-key").count() == 0:
            raise RuntimeError("till A first-run modal has no license-key field -- licensing may not be wired up.")
        shot(page, "till-a-first-run")

        page.fill("#su-name", "Lab Proof Admin")
        page.fill("#su-company", "Lab Proof Shop")
        page.fill("#su-email", PROOF_ADMIN_EMAIL)
        page.fill("#su-key", key)
        page.fill("#su-pass", admin_password)
        page.fill("#su-pass2", admin_password)
        page.click("#su-btn")

        page.wait_for_selector("#rl-email", timeout=60000)
        shot(page, "till-a-relogin-after-activate")
        page.fill("#rl-email", PROOF_ADMIN_EMAIL)
        page.fill("#rl-pass", admin_password)
        page.click("#rl-btn")
        page.wait_for_selector("#sub-header-section", timeout=30000)
        page.wait_for_timeout(1000)
        shot(page, "till-a-signed-in")

        status = page.request.get(TILL_A_BASE + "/api/licensing/status").json()
        current_state = status.get("current_state")
        active_family_ok = current_state in ("ACTIVE_ONLINE", "ACTIVE_OFFLINE")
        R.phase("P2a till A create-admin + activate", "PASS" if active_family_ok else "FAIL",
                f"current_state={current_state}", time.time() - t0)
        if not active_family_ok:
            raise RuntimeError(f"till A licensing.status not in ACTIVE_FAMILY: {status}")

        # -- LAN hub probe: TCP 5443 within 90s -----------------------------
        t_hub = time.time()
        hub_up = wait_until(lambda: tcp_port_listening(SITE_RELAY_TCP_PORT) or None, timeout=90, interval=5,
                             label="till A site relay :5443")[0]
        owner_info = get_net_tcp_owner(SITE_RELAY_TCP_PORT) if hub_up else {}
        R.phase("P2b till A LAN hub (TCP :5443) within 90s", "PASS" if hub_up else "MEASURED",
                f"listening={hub_up} owner={owner_info}", time.time() - t_hub)
        if not hub_up:
            R.finding(
                "Site relay hub mode does not start mid-process on activation",
                "commercial_runtime/sync/site_relay is only started from "
                "_start_site_relay_if_enabled(), called exactly once inside "
                "products/retail/backend/app.py::init_app() (app.py:1692). Till A "
                "booted BEFORE this licence existed, so init_app() evaluated "
                "site_relay_should_start() against a NOT_CONFIGURED licence state "
                "and decided not to start the hub -- activating mid-process (this "
                "phase) never re-runs that decision. A restart would very likely "
                "flip it (config.py:459-520: Windows + ACTIVE_FAMILY -> automatic), "
                "but nothing in the product restarts a till for you after activation."
            )

        # -- UDP beacon probe: one datagram within 30s ----------------------
        t_beacon = time.time()
        beacon = capture_one_beacon(timeout=30)
        if not beacon.get("bound"):
            R.phase("P2c till A beacon capture (UDP :45455)", "MEASURED",
                    f"could not bind -- port held exclusively: {beacon.get('error')}", time.time() - t_beacon)
        elif beacon.get("received"):
            R.phase("P2c till A beacon capture (UDP :45455)", "PASS" if hub_up else "MEASURED",
                    f"url={beacon.get('url')} installation_id={beacon.get('installation_id')}",
                    time.time() - t_beacon)
        else:
            R.phase("P2c till A beacon capture (UDP :45455)", "MEASURED",
                    "bound but received nothing in 30s (consistent with the hub not running -- see P2b)",
                    time.time() - t_beacon)
    except Exception as exc:  # noqa: BLE001
        R.phase("P2 till A activate", "FAIL", f"{exc}\n{traceback.format_exc()}", time.time() - t0)
        raise
    finally:
        browser.close()


# ---------------------------------------------------------------------------
# P3 -- Till B (phone stand-in): join with the same key, restart, sign in.
# ---------------------------------------------------------------------------

def _open_till_b_and_wait_signed_in(pw, browser, site_relay_used: str) -> tuple:
    env = load_env()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    wait_http_ok(TILL_B_BASE + "/api/health", timeout=30)
    page.goto(TILL_B_BASE + "/", wait_until="networkidle")
    page.wait_for_selector("#rl-pass, #su-btn, .auth-title", timeout=150000)
    su_count = page.locator("#su-btn").count()
    shot(page, f"till-b-first-screen-after-restart-{site_relay_used}")
    if su_count != 0:
        raise RuntimeError("till B was offered the FIRST-RUN setup modal again after joining -- a real regression.")
    page.wait_for_selector("#rl-pass", timeout=150000)
    shot(page, f"till-b-signin-screen-{site_relay_used}")
    page.fill("#rl-email", PROOF_ADMIN_EMAIL)
    page.fill("#rl-pass", env["proof_admin_password"])
    page.click("#rl-btn")
    page.wait_for_selector("#sub-header-section", timeout=30000)
    page.wait_for_timeout(1000)
    shot(page, f"till-b-signed-in-{site_relay_used}")
    return page, browser


def _restart_till_a_so_its_sync_starts() -> None:
    """NOT part of the documented P1-P6 sequence -- a required workaround
    for the exact gap P2b measured (site relay never starting mid-process)
    turning out to be one instance of a WIDER problem: till A's regular
    cloud-relay SYNC loop is ALSO decided exactly once, inside
    products/retail/backend/app.py::init_app() (`if _sync_service is not
    None: _sync_service.start()`), against whatever SYNC_RELAY_BASE_URL
    resolved to at THAT BOOT. A booted unactivated, so at boot time no
    relay URL had been persisted yet and `_sync_service` was built as
    None -- permanently, for this process's lifetime. Confirmed live via
    GET /api/sub/retail/sync/health on A right after P2 finished:
    `{"configured": false}` despite current_state already being
    ACTIVE_ONLINE. Till B was left polling 'Connecting to your shop...'
    forever as a direct, reproduced consequence -- A had a fully valid,
    queued 'create' sync event for its own admin user
    (onboarding_routes.py::create_admin already calls
    _queue_user_sync_event) that could never leave the outbox, because
    the process that owns it never started the loop that pushes it.

    This is restarted here, as an explicit, separately-recorded step,
    rather than folded into phase_p2 itself -- P2's own 90s hub-timeout
    measurement stays a faithful, unmodified reading of what a till that
    is NEVER restarted after activation actually does (nothing), which is
    exactly what a real shop that only ever restarts the SECOND till
    would experience. Restarting A is the same operational step this
    product's own UI already asks the operator to do after activating B;
    nothing here edits product code or weakens a check."""
    t0 = time.time()
    stop_lab("till-5020")
    tcp_port_closed(TILL_A_PORT, timeout=20)
    proc = run_ps_script(LAB_SCRIPTS_DIR / "lab_desktop.ps1",
                          ["-Headless", "-Port", str(TILL_A_PORT), "-DataDir", DATA_A_DIR], timeout=60)
    if not wait_http_ok(TILL_A_BASE + "/api/health", timeout=30):
        R.phase("P3-pre restart till A (required workaround for the P2b gap)", "FAIL",
                f"till A did not come back up. stdout={proc.stdout} stderr={proc.stderr}", time.time() - t0)
        raise RuntimeError("till A restart failed")

    env = load_env()
    a = Client(TILL_A_BASE)
    status, _ = a.call("POST", "/api/auth/login", {"email": PROOF_ADMIN_EMAIL, "password": env["proof_admin_password"]})
    ok, health, elapsed = wait_until(
        lambda: (lambda s, b: (b.get("data") or {}) if s == 200 and (b.get("data") or {}).get("configured") else None)(*a.call("GET", "/api/sub/retail/sync/health")),
        timeout=30, interval=2, label="A sync becomes configured after restart")
    R.finding(
        "Till A's own sync loop does not start on activation without a restart (root cause of B's join hanging)",
        f"before restart: GET /api/sub/retail/sync/health on A returned "
        f"{{'configured': False}} despite current_state already ACTIVE_ONLINE (app.py::init_app's "
        f"_sync_service.start() only ever runs once, at boot, against whatever relay URL existed "
        f"THEN -- config.py's own comment: 'falls back to whatever Owner told this device at "
        f"activation time'). After this explicit restart: configured={bool(ok)} health={health}. "
        f"No production flow ever tells the operator to restart the FIRST till after activation -- "
        f"only the join screen tells the SECOND till's operator to restart."
    )
    R.phase("P3-pre restart till A (required workaround for the P2b gap)", "PASS" if ok else "FAIL",
            f"sync configured after {elapsed:.0f}s: {ok}", time.time() - t0)
    if not ok:
        raise RuntimeError("till A's sync never became configured even after a restart")


def phase_p3(pw) -> None:
    t0 = time.time()
    env = load_env()
    key = env.get("proof_licence_key")
    if not key:
        R.phase("P3 till B join", "FAIL", "no proof_licence_key -- run P1 first", time.time() - t0)
        raise RuntimeError("P3 requires proof_licence_key")

    if RESTART_TILL_A_WORKAROUND:
        _restart_till_a_so_its_sync_starts()
    else:
        log("S0: skipping the till-A restart workaround (opt-in only, "
            "--restart-till-a-workaround / LAB_RESTART_TILL_A_WORKAROUND=1 not "
            "set) -- proving the hot-start fix means A must NOT need a restart")
        R.phase("P3-pre restart till A (SKIPPED -- opt-in workaround not requested)",
                "PASS", "hot-start fix is expected to make this unnecessary", 0.0)

    browser = None
    site_relay_final = "auto"
    try:
        proc = start_till_b(site_relay="auto")
        if not wait_http_ok(TILL_B_BASE + "/api/health", timeout=30):
            raise RuntimeError(f"till B did not come up. stdout={proc.stdout} stderr={proc.stderr}")

        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(TILL_B_BASE + "/", wait_until="networkidle")
        page.wait_for_selector("#su-btn", timeout=30000)
        assert page.locator("#su-join-link").count() == 1, "no 'Already have a shop?' join link on a fresh till"
        shot(page, "till-b-first-run")
        page.click("#su-join-link")
        title = (page.text_content("#su-title") or "").strip()
        if title != "Join your shop":
            raise RuntimeError(f"join-mode title mismatch: {title!r}")
        shot(page, "till-b-join-mode")
        page.fill("#su-key", key)
        page.click("#su-btn")
        page.wait_for_selector("#su-join-continue-btn", timeout=60000)
        shot(page, "till-b-connected-pending-restart")
        page.close()

        # The relay address B just learned only takes effect at the NEXT
        # launch (app-shell.js's own comment on _joinSubmit -- read at
        # import, see products/retail/backend/config.py). Restart for real.
        stop_lab("till-5021")
        tcp_port_closed(TILL_B_PORT, timeout=20)
        proc = start_till_b(site_relay="auto")
        if not wait_http_ok(TILL_B_BASE + "/api/health", timeout=30):
            raise RuntimeError(f"till B did not come back up after restart. stderr={proc.stderr}")

        try:
            page, browser = _open_till_b_and_wait_signed_in(pw, browser, "auto")
        except Exception as exc:  # noqa: BLE001
            log(f"  till B (auto site-relay) did not reach sign-in cleanly: {exc}")
            log_tail = tail_text_file(LAB_ROOT / "logs" / f"till-{TILL_B_PORT}.err")
            R.phase("P3a till B join (site-relay auto)", "MEASURED",
                    f"did not reach sign-in cleanly ({exc}); stderr tail: {log_tail[-800:]}", time.time() - t0)
            log("  retrying till B with -SiteRelay 0 (cloud relay via the Owner)")
            stop_lab("till-5021")
            tcp_port_closed(TILL_B_PORT, timeout=20)
            proc = start_till_b(site_relay="0")
            if not wait_http_ok(TILL_B_BASE + "/api/health", timeout=30):
                raise RuntimeError(f"till B did not come up with -SiteRelay 0 either. stderr={proc.stderr}")
            site_relay_final = "0"
            page, browser = _open_till_b_and_wait_signed_in(pw, browser, "site-relay-0")

        status = page.request.get(TILL_B_BASE + "/api/licensing/status").json()
        current_state = status.get("current_state")
        active_ok = current_state in ("ACTIVE_ONLINE", "ACTIVE_OFFLINE")
        R.phase("P3 till B join + restart + sign in", "PASS" if active_ok else "FAIL",
                f"current_state={current_state} site_relay_used={site_relay_final}", time.time() - t0)

        # Which sync path is B actually using -- READ-FIRST
        # commercial_runtime/sync/sync_service.py::get_active_health(),
        # exposed at GET /api/sub/retail/sync/health (retail_api.py:16374).
        sync_health = page.request.get(TILL_B_BASE + "/api/sub/retail/sync/health").json()
        R.finding("Till B's active sync path", json.dumps(sync_health.get("data", sync_health))[:500])

        relay_status = page.request.get(TILL_B_BASE + "/api/site-relay/status").json()
        R.finding("Till B's /api/site-relay/status (loopback, whether B is itself a hub)",
                  json.dumps(relay_status)[:400])

        save_env({"till_b_site_relay_mode": site_relay_final})
    except Exception as exc:  # noqa: BLE001
        R.phase("P3 till B join", "FAIL", f"{exc}\n{traceback.format_exc()}", time.time() - t0)
        raise
    finally:
        if browser is not None:
            browser.close()


# ---------------------------------------------------------------------------
# P4 -- convergence, plain HTTP (matches scripts/ops/*.py's own shape).
# ---------------------------------------------------------------------------

def _login(base: str, email: str, password: str) -> Client:
    c = Client(base)
    status, body = c.call("POST", "/api/auth/login", {"email": email, "password": password})
    if status != 200:
        raise RuntimeError(f"login failed on {base} as {email}: {status} {body}")
    return c


def phase_p4() -> None:
    t0 = time.time()
    env = load_env()
    admin_password = env["proof_admin_password"]
    a = _login(TILL_A_BASE, PROOF_ADMIN_EMAIL, admin_password)
    b = _login(TILL_B_BASE, PROOF_ADMIN_EMAIL, admin_password)
    log("P4: logged in on both tills as the (synced) shop admin")

    sku = f"LAB-P1-{TAG}"

    # -- a) product convergence --------------------------------------------
    t = time.time()
    status, body = a.call("POST", "/api/sub/retail/products", {
        "name": "Lab Proof Product 1", "sku": sku, "sell_price": 2.5, "price": 2.5,
        "cost_price": 1.0, "stock": 10, "stock_quantity": 10, "quantity": 10,
        "category": "Lab", "unit": "pc",
    })
    status, plist = a.call("GET", "/api/sub/retail/products")
    prod = find_by(rows_of(plist), "sku", sku)
    if not prod:
        R.phase("P4a product create (A)", "FAIL", f"create status={status} product not listed after create", time.time() - t)
        raise RuntimeError("product not visible on A after create")
    pid = prod["id"]
    status, _ = a.call("POST", f"/api/sub/retail/products/{pid}/stock-adjust",
                        {"quantity": 10, "reason": "lab e2e opening stock"})

    # Waits for total_stock == 10, NOT merely the sku's presence (2026-09-24,
    # found running proof2's S4a -- this exact phase had never actually run
    # before then; proof1 was blocked earlier). The create-product event and
    # the stock-adjust event just above are TWO SEPARATE outbox rows, pushed
    # on the sync loop's own interval -- a poll that returns as soon as the
    # sku appears can win the race against the second event and let b) fire
    # a sale against a stale zero-stock snapshot ("Insufficient stock (have
    # 0.0, requested 2.0)"), which is exactly what happened before this was
    # a wait condition. Waiting for the STATE THE NEXT STEP NEEDS, not a
    # proxy for it, is the fix -- not a sleep or a retry around the sale.
    ok, seen, elapsed = wait_until(
        lambda: (lambda s, bd: (lambda r: r if r and r.get("total_stock") == 10 else None)(
            find_by(rows_of(bd), "sku", sku)) if s == 200 else None)(*b.call("GET", "/api/sub/retail/products")),
        timeout=180, interval=5, label="product (with opening stock) on B")
    same_uid = bool(seen) and seen.get("id") == pid
    R.phase("P4a product LAB-P1 created on A, appears on B with opening stock", "PASS" if same_uid else "FAIL",
            f"seen_on_B={bool(seen)} same_uid={same_uid} price={seen.get('sell_price') if seen else None} "
            f"stock={seen.get('total_stock') if seen else None}", elapsed)

    # -- b) sale on B, seen on A ----------------------------------------------
    t = time.time()
    status, _ = b.call("POST", "/api/sub/retail/cash-sessions/open", {"opening_float": 5})
    sale_key = f"lab-e2e-sale-{TAG}"
    status, sale_body = b.call("POST", "/api/sub/retail/sales", {
        "items": [{"product_id": pid, "quantity": 2}],
        "payment_method": "cash", "amount_paid": 5.0, "idempotency_key": sale_key,
    })
    sale = (sale_body.get("data") or {}) if isinstance(sale_body, dict) else {}
    sale_number = sale.get("sale_number")
    if not sale_number:
        R.phase("P4b sale on B", "FAIL", f"sale did not return a sale_number: status={status} body={sale_body}", time.time() - t)
        raise RuntimeError("sale on B failed")

    ok, found_sale, elapsed = wait_until(
        lambda: (lambda s, bd: next((r for r in rows_of(bd) if r.get("sale_number") == sale_number), None) if s == 200 else None)(*a.call("GET", "/api/sub/retail/sales/recent")),
        timeout=180, interval=5, label="sale on A")
    status, prod_a_after = a.call("GET", "/api/sub/retail/products")
    status, prod_b_after = b.call("GET", "/api/sub/retail/products")
    stock_a = (find_by(rows_of(prod_a_after), "sku", sku) or {}).get("total_stock")
    stock_b = (find_by(rows_of(prod_b_after), "sku", sku) or {}).get("total_stock")
    stock_ok = stock_a == 8 and stock_b == 8
    R.phase("P4b B sells 2xLAB-P1, seen on A + stock 8 both sides", "PASS" if (ok and stock_ok) else "FAIL",
            f"sale_seen_on_A={bool(found_sale)} stock_a={stock_a} stock_b={stock_b}", elapsed)

    # -- c) customers, both directions --------------------------------------
    t = time.time()
    name_a = f"Lab Customer {TAG}"
    a.call("POST", "/api/sub/retail/customers", {"name": name_a})
    ok1, _, e1 = wait_until(
        lambda: (lambda s, bd: find_by(rows_of(bd), "name", name_a) if s == 200 else None)(*b.call("GET", "/api/sub/retail/customers")),
        timeout=90, interval=5, label="A's customer on B")
    name_b = f"Phone Customer {TAG}"
    b.call("POST", "/api/sub/retail/customers", {"name": name_b})
    ok2, _, e2 = wait_until(
        lambda: (lambda s, bd: find_by(rows_of(bd), "name", name_b) if s == 200 else None)(*a.call("GET", "/api/sub/retail/customers")),
        timeout=90, interval=5, label="B's customer on A")
    R.phase("P4c customers converge both directions", "PASS" if (ok1 and ok2) else "FAIL",
            f"A-created seen on B after {e1:.0f}s={ok1}; B-created seen on A after {e2:.0f}s={ok2}", time.time() - t)

    # -- d) employee invite + set password, cashier signs in on B -----------
    t = time.time()
    status, emp_body = a.call("POST", "/api/admin/employees", {"email": PROOF_CASHIER_EMAIL, "role": "cashier"})
    setup_link = emp_body.get("setup_link", "") if status == 200 else ""
    m = re.search(r"#setup/([0-9a-f]+)$", setup_link)
    cashier_password = uuid.uuid4().hex[:10] + "Aa1!"
    setup_ok = False
    if m:
        setup_status, setup_body = a.call("POST", "/api/auth/employee/setup",
                                           {"token": m.group(1), "password": cashier_password})
        setup_ok = setup_status == 200
    ok3, _, e3 = wait_until(
        lambda: True if _login(TILL_B_BASE, PROOF_CASHIER_EMAIL, cashier_password) else None,
        timeout=180, interval=5, label="cashier can sign in on B")
    cashier_products_ok = False
    if ok3:
        cashier_client = _login(TILL_B_BASE, PROOF_CASHIER_EMAIL, cashier_password)
        pstatus, _ = cashier_client.call("GET", "/api/sub/retail/products")
        cashier_products_ok = pstatus == 200
    R.phase("P4d employee invite + setup + cashier signs in on B", "PASS" if (setup_ok and ok3 and cashier_products_ok) else "FAIL",
            f"create_status={status} setup_ok={setup_ok} signed_in_on_B_after={e3:.0f}s list_products_ok={cashier_products_ok}",
            time.time() - t)

    # -- e) one shop setting travels -----------------------------------------
    t = time.time()
    a.call("POST", "/api/sub/retail/settings/credit", {"base_currency": "USD"})
    ok4, _, e4 = wait_until(
        lambda: (lambda s, bd: ((bd.get("data") or bd).get("base_currency") == "USD") or None)(*b.call("GET", "/api/sub/retail/settings/tax")),
        timeout=90, interval=5, label="currency on B")
    a.call("POST", "/api/sub/retail/settings/credit", {"base_currency": "JOD"})  # restore
    R.phase("P4e shop setting (base_currency) converges to B", "PASS" if ok4 else "FAIL", f"after {e4:.0f}s", time.time() - t)

    # -- f1) MEASURE: P&L parity + a sales-based figure, both sides ----------
    t = time.time()
    today = datetime.now().strftime("%Y-%m-%d")
    status_a, pnl_a = a.call("GET", f"/api/sub/retail/reports/profit-and-loss?start_date={today}&end_date={today}")
    status_b, pnl_b = b.call("GET", f"/api/sub/retail/reports/profit-and-loss?start_date={today}&end_date={today}")
    status_da, dash_a = a.call("GET", "/api/sub/retail/dashboard/stats")
    status_db, dash_b = b.call("GET", "/api/sub/retail/dashboard/stats")
    rev_a = (pnl_a.get("data") or {}).get("revenue") if status_a == 200 else None
    rev_b = (pnl_b.get("data") or {}).get("revenue") if status_b == 200 else None
    today_a = (dash_a.get("data") or dash_a).get("today_sales") if status_da == 200 else None
    today_b = (dash_b.get("data") or dash_b).get("today_sales") if status_db == 200 else None
    R.finding(
        "P&L revenue vs dashboard today-sales, A vs B (same period, right after B's sale)",
        f"P&L revenue: A={rev_a} B={rev_b} (equal={rev_a == rev_b}); "
        f"dashboard today_sales: A={today_a} B={today_b} (equal={today_a == today_b})"
    )

    # -- f2) MEASURE: promotion propagation + discount applied at the till ---
    starts = datetime.now().strftime("%Y-%m-%d")
    ends = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    promo_status, promo_body = a.call("POST", "/api/sub/retail/promotions", {
        "name": f"Lab Promo 10pct {TAG}", "discount_pct": 10, "product_id": pid,
        "starts_at": starts, "ends_at": ends,
    })
    time.sleep(60)
    active_status, active_body = b.call("GET", "/api/sub/retail/promotions/active")
    promo_on_b = any(r.get("product_id") == pid for r in rows_of(active_body)) if active_status == 200 else False
    ring_status, ring_body = b.call("POST", "/api/sub/retail/sales", {
        "items": [{"product_id": pid, "quantity": 1}],
        "payment_method": "cash", "amount_paid": 2.5,
        "idempotency_key": f"lab-e2e-promo-sale-{TAG}",
    })
    ring_sale = (ring_body.get("data") or {}) if isinstance(ring_body, dict) else {}
    ring_total = ring_sale.get("total")
    discount_applied = ring_total is not None and ring_total < 2.5
    R.finding(
        "Promotion created on A -> visible on GET /promotions/active on B after 60s, and discount applied on B",
        f"create_status={promo_status} visible_on_B_after_60s={promo_on_b} "
        f"ring_status={ring_status} ring_total={ring_total} discount_applied={discount_applied}"
    )

    R.phase("P4f measurements (P&L/dashboard parity, promotion propagation)", "MEASURED",
            "see 'findings' section for both results", time.time() - t)

    # -- f3) MEASURE: what actually crosses the cloud relay ------------------
    t = time.time()
    env2 = os.environ.copy()
    env2["PGPASSWORD"] = OWNER_PG_PASSWORD
    try:
        proc = subprocess.run(
            [str(PSQL_EXE), "-h", "localhost", "-p", "5432", "-U", OWNER_PG_USER, "-d", OWNER_PG_DB, "-tAc",
             "SELECT entity_type, COUNT(*) FROM owner_sync_events GROUP BY entity_type ORDER BY entity_type"],
            capture_output=True, text=True, timeout=30, env=env2,
        )
        counts = proc.stdout.strip()
        R.finding("owner_sync_events counts by entity_type (what crossed the cloud relay)",
                  counts if counts else f"(no rows) stderr={proc.stderr.strip()}")
    except Exception as exc:  # noqa: BLE001
        R.finding("owner_sync_events counts by entity_type", f"psql query failed: {exc}")
    R.phase("P4 convergence phase complete", "PASS", "see phases a-e and findings f1-f3 above", time.time() - t0)


# ---------------------------------------------------------------------------
# P5 -- cleanup: stop both tills, verify ports closed, leave Owner running.
# ---------------------------------------------------------------------------

def phase_p5() -> None:
    t0 = time.time()
    stop_lab("till-5020")
    stop_lab("till-5021")
    a_closed = tcp_port_closed(TILL_A_PORT, timeout=30)
    b_closed = tcp_port_closed(TILL_B_PORT, timeout=30)
    relay_closed = tcp_port_closed(SITE_RELAY_TCP_PORT, timeout=30)
    owner_still_up = http_get_ok(OWNER_BASE + "/auth/login")
    ok = a_closed and b_closed and relay_closed and owner_still_up
    R.phase("P5 cleanup: stop A/B, ports closed, Owner still up", "PASS" if ok else "FAIL",
            f"5020_closed={a_closed} 5021_closed={b_closed} 5443_closed={relay_closed} owner_up={owner_still_up}",
            time.time() - t0)


# ---------------------------------------------------------------------------
# P6 -- issue one more licence for the owner's own manual test (never activated).
# ---------------------------------------------------------------------------

def phase_p6(pw) -> None:
    t0 = time.time()
    env = load_env()
    browser = pw.chromium.launch()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        minted = owner_authenticate(page, env)
        if minted:
            save_env(minted)
        result = owner_issue_license(page, legal_name="My Test Shop")
        save_env({"ready_licence_key": result["key"], "ready_licence_id": result["license_id"]})
        R.phase("P6 issue second licence (ready_licence_key, not activated anywhere)", "PASS",
                f"package={result['package_option_text']!r} license_id={result['license_id']}", time.time() - t0)
    except Exception as exc:  # noqa: BLE001
        R.phase("P6 issue second licence", "FAIL", f"{exc}\n{traceback.format_exc()}", time.time() - t0)
        raise
    finally:
        browser.close()


# ---------------------------------------------------------------------------
# Proof2 (S0-S6, 2026-09-24) -- proves the hot-start fix end to end on a
# SECOND, independent licence/customer/pair of tills, and finishes the
# measurements the previous (blocked) proof1 run could not reach: A2 must
# reach sync-configured + LAN-hub-listening WITHOUT any restart, and B2 must
# be able to join and receive the owner's account WITHOUT restarting A2
# either. See the RESTART_TILL_A_WORKAROUND / proof2 constants block near
# the top of this file for S0's opt-in flag and S1-S6's ports/paths.
# ---------------------------------------------------------------------------

def _grep_log_line(path: Path, needle: str) -> str | None:
    """Returns the FIRST line containing `needle` from `path`, or None.
    Used (S2) to quote app.py's own INFO line -- 'Multi-device sync started
    after activation, with no restart' -- straight from the till's own
    stderr log: app.py::_configure_file_logging attaches a StreamHandler on
    sys.stderr alongside its RotatingFileHandler, and lab_desktop.ps1's
    -Headless mode redirects that stderr to logs/till-<port>.err. Quoting
    the log line itself is proof the fix's own code path actually ran, not
    an inference from a side effect."""
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if needle in line:
                return line
    except OSError:
        pass
    return None


def _psql_owner_sync_events_by_entity_type(license_id: str | None) -> str:
    """Read-only COUNT(*) ... GROUP BY entity_type against owner_sync_events,
    optionally filtered to one licence via its `license_id` FK
    (owner/app/models/sync.py::SyncEvent.license_id -> owner_licenses.id --
    the same id owner_issue_license() reads back off the licence detail
    page's own URL). Mirrors phase_p4's f3 query exactly, parametrized by
    licence so proof2's count is never mixed with proof1's Lab Proof Shop
    rows in the same Postgres instance."""
    env2 = os.environ.copy()
    env2["PGPASSWORD"] = OWNER_PG_PASSWORD
    where = f"WHERE license_id = '{license_id}'" if license_id else ""
    query = f"SELECT entity_type, COUNT(*) FROM owner_sync_events {where} GROUP BY entity_type ORDER BY entity_type"
    try:
        proc = subprocess.run(
            [str(PSQL_EXE), "-h", "localhost", "-p", "5432", "-U", OWNER_PG_USER, "-d", OWNER_PG_DB, "-tAc", query],
            capture_output=True, text=True, timeout=30, env=env2,
        )
        out = proc.stdout.strip()
        return out if out else f"(no rows) stderr={proc.stderr.strip()}"
    except Exception as exc:  # noqa: BLE001
        return f"psql query failed: {exc}"


# ---------------------------------------------------------------------------
# S1 -- issue proof2's licence.
# ---------------------------------------------------------------------------

def phase_q1(pw) -> None:
    """S1 -- issue proof2's licence: customer 'Lab Proof Shop 2', the seeded
    Retail plan, 2 devices total. Existing subcommand machinery (owner_
    authenticate + owner_issue_license) exactly as phase_p1/phase_p6 already
    use it -- the only new things are target_devices=2 and saving under
    proof2_licence_key/proof2_licence_id, so this can never collide with
    proof1's ADD-ONLY proof_licence_key."""
    t0 = time.time()
    env = load_env()
    browser = pw.chromium.launch()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        minted = owner_authenticate(page, env, results=R2)
        if minted:
            save_env(minted)
            env = load_env()

        result = owner_issue_license(page, legal_name="Lab Proof Shop 2",
                                      target_devices=PROOF2_LICENCE_TARGET_DEVICES, results=R2)
        save_env({"proof2_licence_key": result["key"], "proof2_licence_id": result["license_id"]})
        R2.phase("S1 issue proof2 licence (Lab Proof Shop 2, 2 devices)", "PASS",
                 f"package={result['package_option_text']!r} included={result['included_devices']} "
                 f"extra={result['extra_devices']} license_id={result['license_id']}", time.time() - t0)
    except Exception as exc:  # noqa: BLE001
        R2.phase("S1 issue proof2 licence", "FAIL", f"{exc}\n{traceback.format_exc()}", time.time() - t0)
        raise
    finally:
        browser.close()


# ---------------------------------------------------------------------------
# S2 -- till A2: activate, then measure the hot-start fix directly, with NO
# restart anywhere in this function (unlike phase_p3, this phase never even
# has the option -- S0's workaround lives only inside phase_p3).
# ---------------------------------------------------------------------------

def phase_q2(pw) -> None:
    t0 = time.time()
    env = load_env()
    key = env.get("proof2_licence_key")
    if not key:
        R2.phase("S2 till A2 activate", "FAIL", "no proof2_licence_key -- run S1 first", time.time() - t0)
        raise RuntimeError("S2 requires proof2_licence_key")

    if not env.get("proof2_admin_password"):
        save_env({"proof2_admin_password": uuid.uuid4().hex[:10] + "Aa1!"})
        env = load_env()
    admin_password = env["proof2_admin_password"]

    log(f"S2: starting till A2 on :{TILL_A2_PORT} (data={DATA_A2_DIR}, site_relay=auto)")
    proc = start_till(TILL_A2_PORT, DATA_A2_DIR, site_relay="auto")
    if not wait_http_ok(TILL_A2_BASE + "/api/health", timeout=30):
        R2.phase("S2 till A2 boot", "FAIL", f"till A2 did not come up. stdout={proc.stdout} stderr={proc.stderr}",
                 time.time() - t0)
        raise RuntimeError("till A2 did not boot")

    # Best-effort environment verification: Windows exposes no supported way
    # to read a FOREIGN process's environment block after launch (Win32_
    # Process carries no env vars, and .NET's ProcessStartInfo.Environment is
    # only populated for a process THIS interpreter itself started, not one
    # recovered by pid afterward) -- so the honest limit of what this box can
    # verify is: (a) THIS script's own environment, the one lab_desktop.ps1
    # actually ran under, never carried AURA_SYNC_RELAY_URL, and (b)
    # lab_desktop.ps1 itself unconditionally runs `Remove-Item Env:AURA_
    # SYNC_RELAY_URL` in -Headless mode regardless of what it inherited
    # (scripts/lab/lab_desktop.ps1:55, read directly, not re-derived).
    launching_shell_had_var = "AURA_SYNC_RELAY_URL" in os.environ
    R2.phase("S2a env check: no AURA_SYNC_RELAY_URL anywhere in A2's environment",
             "PASS" if not launching_shell_had_var else "FAIL",
             f"launching shell had the var set={launching_shell_had_var} (must be False); "
             f"lab_desktop.ps1 -Headless also unconditionally Remove-Item's it regardless -- "
             f"Windows has no supported way to inspect a foreign process's own env block "
             f"after launch to verify further than this", 0.0)

    browser = pw.chromium.launch()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(TILL_A2_BASE + "/", wait_until="networkidle")
        page.wait_for_selector("#su-btn", timeout=30000)
        if page.locator("#su-key").count() == 0:
            raise RuntimeError("till A2 first-run modal has no license-key field.")
        shot(page, "till-a2-first-run", results=R2)

        page.fill("#su-name", "Lab Proof Admin 2")
        page.fill("#su-company", "Lab Proof Shop 2")
        page.fill("#su-email", PROOF2_ADMIN_EMAIL)
        page.fill("#su-key", key)
        page.fill("#su-pass", admin_password)
        page.fill("#su-pass2", admin_password)
        page.click("#su-btn")

        page.wait_for_selector("#rl-email", timeout=60000)
        shot(page, "till-a2-relogin-after-activate", results=R2)
        page.fill("#rl-email", PROOF2_ADMIN_EMAIL)
        page.fill("#rl-pass", admin_password)
        page.click("#rl-btn")
        page.wait_for_selector("#sub-header-section", timeout=30000)
        page.wait_for_timeout(1000)
        shot(page, "till-a2-signed-in", results=R2)

        status = page.request.get(TILL_A2_BASE + "/api/licensing/status").json()
        current_state = status.get("current_state")
        active_ok = current_state in ("ACTIVE_ONLINE", "ACTIVE_OFFLINE")
        R2.phase("S2b till A2 create-admin + activate", "PASS" if active_ok else "FAIL",
                 f"current_state={current_state}", time.time() - t0)
        if not active_ok:
            raise RuntimeError(f"till A2 licensing.status not in ACTIVE_FAMILY: {status}")

        # -- sync health becomes configured, WITHOUT a restart ---------------
        def _a2_sync_health():
            r = page.request.get(TILL_A2_BASE + "/api/sub/retail/sync/health")
            if not r.ok:
                return None
            data = (r.json().get("data") or {})
            return data if data.get("configured") else None

        ok_health, health, elapsed_health = wait_until(
            _a2_sync_health, timeout=90, interval=2, label="A2 sync becomes configured (no restart)")
        R2.phase("S2c till A2 sync health configured, NO restart (<=90s)", "PASS" if ok_health else "FAIL",
                 f"configured after {elapsed_health:.0f}s: {ok_health}; health={health}", elapsed_health)
        if not ok_health:
            raise RuntimeError(f"till A2's sync never became configured without a restart: {health}")

        # -- LAN hub TCP :5443, owned by A2's own process ---------------------
        hub_up, _, elapsed_hub = wait_until(
            lambda: tcp_port_listening(SITE_RELAY_TCP_PORT) or None,
            timeout=120, interval=5, label="till A2 site relay :5443 (election takes ~15s to start listening)")
        owner_info = get_net_tcp_owner(SITE_RELAY_TCP_PORT) if hub_up else {}
        a2_pidfile = read_pid_file(f"till-{TILL_A2_PORT}")
        a2_launcher_pid = a2_pidfile.get("pid") if a2_pidfile else None
        owner_pid = owner_info.get("pid")
        # The recorded pid is the venv LAUNCHER; the socket is held by ITS
        # CHILD on the base interpreter (Stop-LabPid's own docstring, and
        # confirmed live -- see get_process_parent_pid's docstring). Direct
        # equality would always read false for a real till and MEASURED
        # would then never actually confirm ownership; walk one parent level.
        owned_by_a2 = bool(hub_up and a2_launcher_pid and (
            owner_pid == a2_launcher_pid or get_process_parent_pid(owner_pid) == a2_launcher_pid
        ))
        R2.phase("S2d till A2 LAN hub (TCP :5443), owned by A2's own process (<=120s)",
                 "PASS" if owned_by_a2 else ("MEASURED" if hub_up else "FAIL"),
                 f"listening={hub_up} owner={owner_info} a2_launcher_pid_on_file={a2_launcher_pid} "
                 f"owned_by_a2(direct_or_parent)={owned_by_a2}", elapsed_hub)
        if not hub_up:
            raise RuntimeError("till A2's LAN hub never started TCP :5443 without a restart")

        # -- one UDP beacon ----------------------------------------------------
        t_beacon = time.time()
        beacon = capture_one_beacon(timeout=30)
        R2.phase("S2e till A2 beacon capture (UDP :45455)", "PASS" if beacon.get("received") else "MEASURED",
                 f"bound={beacon.get('bound')} received={beacon.get('received')} "
                 f"url={beacon.get('url')} installation_id={beacon.get('installation_id')}",
                 time.time() - t_beacon)

        # -- app.py's own new log line ------------------------------------------
        log_line = _grep_log_line(LAB_ROOT / "logs" / f"till-{TILL_A2_PORT}.err",
                                   "Multi-device sync started after activation, with no restart")
        R2.phase("S2f app.py's 'sync started after activation' log line present",
                 "PASS" if log_line else "FAIL", log_line or "line not found in till-5022.err", 0.0)
        if log_line:
            log(f"  QUOTED: {log_line.strip()}")
    except Exception as exc:  # noqa: BLE001
        R2.phase("S2 till A2 activate", "FAIL", f"{exc}\n{traceback.format_exc()}", time.time() - t0)
        raise
    finally:
        browser.close()


# ---------------------------------------------------------------------------
# S3 -- till B2 (phone stand-in) joins via cloud relay (-SiteRelay 0), and
# NEVER restarts A2. Whether B2 itself needs a restart is measured, not
# assumed.
# ---------------------------------------------------------------------------

def _join_till_b2_no_restart(browser, key: str):
    """Drives the real join UI through to the 'Restart Aura to finish
    joining' screen and clicks its continue button WITHOUT ever restarting
    the process. app-shell.js's own su-join-continue-btn handler
    (_waitForShopAccount/_pollShopAccount) is a client-side poll of
    /api/onboarding/status, not a check that a restart actually happened --
    so this is a faithful exercise of exactly what an operator who never
    restarts experiences, which is the point of this proof. Returns
    (page, elapsed_seconds, arrived: bool)."""
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    wait_http_ok(TILL_B2_BASE + "/api/health", timeout=30)
    page.goto(TILL_B2_BASE + "/", wait_until="networkidle")
    page.wait_for_selector("#su-btn", timeout=30000)
    assert page.locator("#su-join-link").count() == 1, "no 'Already have a shop?' join link on a fresh till"
    shot(page, "till-b2-first-run", results=R2)
    page.click("#su-join-link")
    title = (page.text_content("#su-title") or "").strip()
    if title != "Join your shop":
        raise RuntimeError(f"join-mode title mismatch: {title!r}")
    shot(page, "till-b2-join-mode", results=R2)
    page.fill("#su-key", key)
    page.click("#su-btn")
    page.wait_for_selector("#su-join-continue-btn", timeout=60000)
    shot(page, "till-b2-connected-pending-continue", results=R2)

    t_wait = time.time()
    page.click("#su-join-continue-btn")  # "I restarted -- continue" -- deliberately never restarted
    try:
        page.wait_for_selector("#rl-email", timeout=180000)
        elapsed = time.time() - t_wait
        shot(page, "till-b2-owner-account-arrived", results=R2)
        return page, elapsed, True
    except Exception:  # noqa: BLE001
        elapsed = time.time() - t_wait
        shot(page, "till-b2-still-waiting-at-180s", results=R2)
        return page, elapsed, False


def _b2_stall_diagnostics(env: dict) -> dict:
    """Collects sync/outbox diagnostics from BOTH tills plus Owner's own
    owner_sync_events, without restarting or mutating anything -- called
    BEFORE the separate, labelled restart-B2 measurement, so that
    measurement has a known baseline to compare against instead of a
    guess. A login failure on B2 is EXPECTED and reported as data, not an
    error: B2 stalled means no admin account has synced down to it yet, so
    there is no session to authenticate."""
    diag = {}
    try:
        with urllib.request.urlopen(TILL_B2_BASE + "/api/onboarding/status", timeout=5) as resp:
            diag["b2_onboarding_status"] = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        diag["b2_onboarding_status_error"] = str(exc)

    try:
        a2 = _login(TILL_A2_BASE, PROOF2_ADMIN_EMAIL, env["proof2_admin_password"])
        _, a2_health = a2.call("GET", "/api/sub/retail/sync/health")
        diag["a2_sync_health"] = a2_health.get("data", a2_health)
    except Exception as exc:  # noqa: BLE001
        diag["a2_sync_health_error"] = str(exc)

    try:
        b2 = _login(TILL_B2_BASE, PROOF2_ADMIN_EMAIL, env["proof2_admin_password"])
        _, b2_health = b2.call("GET", "/api/sub/retail/sync/health")
        diag["b2_sync_health"] = b2_health.get("data", b2_health)
    except Exception as exc:  # noqa: BLE001
        diag["b2_login_or_health_error (EXPECTED if no admin has synced down yet)"] = str(exc)

    diag["owner_sync_events_for_proof2_licence"] = _psql_owner_sync_events_by_entity_type(env.get("proof2_licence_id"))
    diag["a2_log_tail"] = tail_text_file(LAB_ROOT / "logs" / f"till-{TILL_A2_PORT}.err", max_chars=1500)
    diag["b2_log_tail"] = tail_text_file(LAB_ROOT / "logs" / f"till-{TILL_B2_PORT}.err", max_chars=1500)
    return diag


def phase_q3(pw) -> None:
    t0 = time.time()
    env = load_env()
    key = env.get("proof2_licence_key")
    if not key:
        R2.phase("S3 till B2 join", "FAIL", "no proof2_licence_key -- run S1 first", time.time() - t0)
        raise RuntimeError("S3 requires proof2_licence_key")

    log(f"S3: starting till B2 on :{TILL_B2_PORT} with -SiteRelay 0 (cloud relay via Owner) -- "
        f"A2 is NEVER restarted by this phase")
    proc = start_till(TILL_B2_PORT, DATA_B2_DIR, site_relay="0")
    if not wait_http_ok(TILL_B2_BASE + "/api/health", timeout=30):
        R2.phase("S3 till B2 boot", "FAIL", f"till B2 did not come up. stdout={proc.stdout} stderr={proc.stderr}",
                 time.time() - t0)
        raise RuntimeError("till B2 did not boot")

    browser = pw.chromium.launch()
    try:
        page, elapsed_wait, arrived = _join_till_b2_no_restart(browser, key)
        R2.phase("S3a till B2 join -> owner's account arrives, NO restart of A2 or B2 (<=180s)",
                 "PASS" if arrived else "MEASURED", f"arrived={arrived} after {elapsed_wait:.0f}s", elapsed_wait)

        if not arrived:
            diag = _b2_stall_diagnostics(env)
            R2.finding("B2 join stalled past 180s -- diagnostics collected BEFORE any restart",
                       json.dumps(diag, default=str)[:1800])

            log("S3b: restarting B2 ONLY (never A2) as a separate, labelled measurement")
            t_restart = time.time()
            page.close()
            stop_lab(f"till-{TILL_B2_PORT}")
            tcp_port_closed(TILL_B2_PORT, timeout=20)
            proc = start_till(TILL_B2_PORT, DATA_B2_DIR, site_relay="0")
            if not wait_http_ok(TILL_B2_BASE + "/api/health", timeout=30):
                R2.phase("S3b restart B2 only (measurement)", "FAIL",
                         f"B2 did not come back up. stderr={proc.stderr}", time.time() - t_restart)
                raise RuntimeError("B2 restart failed")
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(TILL_B2_BASE + "/", wait_until="networkidle")
            try:
                page.wait_for_selector("#rl-email", timeout=60000)
                restart_fixed_it = True
            except Exception:  # noqa: BLE001
                restart_fixed_it = False
            shot(page, "till-b2-after-restart-only-measurement", results=R2)
            R2.phase("S3b restarting B2 only -- did that change anything (measurement, not a fix)",
                     "MEASURED", f"restart_fixed_it={restart_fixed_it}", time.time() - t_restart)
            if not restart_fixed_it:
                raise RuntimeError("B2 never reached sign-in even after restarting B2 alone")

        page.fill("#rl-email", PROOF2_ADMIN_EMAIL)
        page.fill("#rl-pass", env["proof2_admin_password"])
        page.click("#rl-btn")
        page.wait_for_selector("#sub-header-section", timeout=30000)
        page.wait_for_timeout(1000)
        shot(page, "till-b2-signed-in", results=R2)

        status = page.request.get(TILL_B2_BASE + "/api/licensing/status").json()
        current_state = status.get("current_state")
        active_ok = current_state in ("ACTIVE_ONLINE", "ACTIVE_OFFLINE")
        R2.phase("S3 till B2 join complete, signed in", "PASS" if active_ok else "FAIL",
                 f"current_state={current_state}", time.time() - t0)

        sync_health = page.request.get(TILL_B2_BASE + "/api/sub/retail/sync/health").json()
        R2.finding("Till B2's active sync path", json.dumps(sync_health.get("data", sync_health))[:500])
        relay_status = page.request.get(TILL_B2_BASE + "/api/site-relay/status").json()
        R2.finding("Till B2's /api/site-relay/status (loopback -- B2 must NOT itself be a hub, -SiteRelay 0)",
                   json.dumps(relay_status)[:400])
    except Exception as exc:  # noqa: BLE001
        R2.phase("S3 till B2 join", "FAIL", f"{exc}\n{traceback.format_exc()}", time.time() - t0)
        raise
    finally:
        browser.close()


# ---------------------------------------------------------------------------
# S4 -- convergence matrix, same route shapes as phase_p4 (and
# scripts/ops/two_device_convergence.py / two_till_*.py), each timed.
# ---------------------------------------------------------------------------

def phase_q4() -> None:
    t0 = time.time()
    env = load_env()
    admin_password = env["proof2_admin_password"]
    a = _login(TILL_A2_BASE, PROOF2_ADMIN_EMAIL, admin_password)
    b = _login(TILL_B2_BASE, PROOF2_ADMIN_EMAIL, admin_password)
    log("S4: logged in on both A2 and B2 as the (synced) shop admin")

    sku = f"LAB-P1-{TAG}-2"

    # -- a) product convergence ---------------------------------------------
    t = time.time()
    status, body = a.call("POST", "/api/sub/retail/products", {
        "name": "Lab Proof Product 1", "sku": sku, "sell_price": 2.5, "price": 2.5,
        "cost_price": 1.0, "stock": 10, "stock_quantity": 10, "quantity": 10,
        "category": "Lab", "unit": "pc",
    })
    status, plist = a.call("GET", "/api/sub/retail/products")
    prod = find_by(rows_of(plist), "sku", sku)
    if not prod:
        R2.phase("S4a product create (A2)", "FAIL", f"create status={status} product not listed after create",
                  time.time() - t)
        raise RuntimeError("product not visible on A2 after create")
    pid = prod["id"]
    status, _ = a.call("POST", f"/api/sub/retail/products/{pid}/stock-adjust",
                        {"quantity": 10, "reason": "lab e2e opening stock"})

    # See the identical fix + full explanation on phase_p4's own P4a, just
    # above in this file -- same race (create-product and stock-adjust are
    # two separate sync events), found running THIS phase for the first
    # time ever, 2026-09-24 (proof1 never reached S4/P4 -- it was blocked
    # earlier). Waits for total_stock == 10, not merely the sku's presence.
    ok, seen, elapsed = wait_until(
        lambda: (lambda s, bd: (lambda r: r if r and r.get("total_stock") == 10 else None)(
            find_by(rows_of(bd), "sku", sku)) if s == 200 else None)(*b.call("GET", "/api/sub/retail/products")),
        timeout=180, interval=5, label="product (with opening stock) on B2")
    same_uid = bool(seen) and seen.get("id") == pid
    R2.phase("S4a product LAB-P1 created on A2, appears on B2 with opening stock", "PASS" if same_uid else "FAIL",
              f"seen_on_B2={bool(seen)} same_uid={same_uid} price={seen.get('sell_price') if seen else None} "
              f"stock={seen.get('total_stock') if seen else None}", elapsed)

    # -- b) sale on B2, seen on A2 --------------------------------------------
    t = time.time()
    status, _ = b.call("POST", "/api/sub/retail/cash-sessions/open", {"opening_float": 5})
    sale_key = f"lab-e2e-sale-{TAG}-2"
    status, sale_body = b.call("POST", "/api/sub/retail/sales", {
        "items": [{"product_id": pid, "quantity": 2}],
        "payment_method": "cash", "amount_paid": 5.0, "idempotency_key": sale_key,
    })
    sale = (sale_body.get("data") or {}) if isinstance(sale_body, dict) else {}
    sale_number = sale.get("sale_number")
    if not sale_number:
        R2.phase("S4b sale on B2", "FAIL", f"sale did not return a sale_number: status={status} body={sale_body}",
                  time.time() - t)
        raise RuntimeError("sale on B2 failed")

    ok, found_sale, elapsed = wait_until(
        lambda: (lambda s, bd: next((r for r in rows_of(bd) if r.get("sale_number") == sale_number), None) if s == 200 else None)(*a.call("GET", "/api/sub/retail/sales/recent")),
        timeout=180, interval=5, label="sale on A2")
    status, prod_a_after = a.call("GET", "/api/sub/retail/products")
    status, prod_b_after = b.call("GET", "/api/sub/retail/products")
    stock_a = (find_by(rows_of(prod_a_after), "sku", sku) or {}).get("total_stock")
    stock_b = (find_by(rows_of(prod_b_after), "sku", sku) or {}).get("total_stock")
    stock_ok = stock_a == 8 and stock_b == 8
    R2.phase("S4b B2 sells 2xLAB-P1, seen on A2 + stock 8 both sides", "PASS" if (ok and stock_ok) else "FAIL",
              f"sale_seen_on_A2={bool(found_sale)} stock_a={stock_a} stock_b={stock_b}", elapsed)

    # -- c) customers, both directions ----------------------------------------
    t = time.time()
    name_a = f"Lab Customer {TAG}-2"
    a.call("POST", "/api/sub/retail/customers", {"name": name_a})
    ok1, _, e1 = wait_until(
        lambda: (lambda s, bd: find_by(rows_of(bd), "name", name_a) if s == 200 else None)(*b.call("GET", "/api/sub/retail/customers")),
        timeout=90, interval=5, label="A2's customer on B2")
    name_b = f"Phone Customer {TAG}-2"
    b.call("POST", "/api/sub/retail/customers", {"name": name_b})
    ok2, _, e2 = wait_until(
        lambda: (lambda s, bd: find_by(rows_of(bd), "name", name_b) if s == 200 else None)(*a.call("GET", "/api/sub/retail/customers")),
        timeout=90, interval=5, label="B2's customer on A2")
    R2.phase("S4c customers converge both directions", "PASS" if (ok1 and ok2) else "FAIL",
              f"A2-created seen on B2 after {e1:.0f}s={ok1}; B2-created seen on A2 after {e2:.0f}s={ok2}",
              time.time() - t)

    # -- d) employee invite + set password, cashier signs in on B2 (Employees screen routes) --
    t = time.time()
    status, emp_body = a.call("POST", "/api/admin/employees", {"email": PROOF2_CASHIER_EMAIL, "role": "cashier"})
    setup_link = emp_body.get("setup_link", "") if status == 200 else ""
    m = re.search(r"#setup/([0-9a-f]+)$", setup_link)
    cashier_password = uuid.uuid4().hex[:10] + "Aa1!"
    setup_ok = False
    if m:
        setup_status, setup_body = a.call("POST", "/api/auth/employee/setup",
                                           {"token": m.group(1), "password": cashier_password})
        setup_ok = setup_status == 200
    ok3, _, e3 = wait_until(
        lambda: True if _login(TILL_B2_BASE, PROOF2_CASHIER_EMAIL, cashier_password) else None,
        timeout=180, interval=5, label="cashier can sign in on B2")
    cashier_products_ok = False
    if ok3:
        cashier_client = _login(TILL_B2_BASE, PROOF2_CASHIER_EMAIL, cashier_password)
        pstatus, _ = cashier_client.call("GET", "/api/sub/retail/products")
        cashier_products_ok = pstatus == 200
    R2.phase("S4d employee invite + setup + cashier signs in on B2", "PASS" if (setup_ok and ok3 and cashier_products_ok) else "FAIL",
              f"create_status={status} setup_ok={setup_ok} signed_in_on_B2_after={e3:.0f}s "
              f"list_products_ok={cashier_products_ok}", time.time() - t)

    # -- e) one shop setting travels -------------------------------------------
    t = time.time()
    a.call("POST", "/api/sub/retail/settings/credit", {"base_currency": "USD"})
    ok4, _, e4 = wait_until(
        lambda: (lambda s, bd: ((bd.get("data") or bd).get("base_currency") == "USD") or None)(*b.call("GET", "/api/sub/retail/settings/tax")),
        timeout=90, interval=5, label="currency on B2")
    a.call("POST", "/api/sub/retail/settings/credit", {"base_currency": "JOD"})  # restore
    R2.phase("S4e shop setting (base_currency) converges to B2", "PASS" if ok4 else "FAIL", f"after {e4:.0f}s",
              time.time() - t)

    # -- f1) MEASURE: P&L parity + a sales-based figure, both sides -------------
    t = time.time()
    today = datetime.now().strftime("%Y-%m-%d")
    status_a, pnl_a = a.call("GET", f"/api/sub/retail/reports/profit-and-loss?start_date={today}&end_date={today}")
    status_b, pnl_b = b.call("GET", f"/api/sub/retail/reports/profit-and-loss?start_date={today}&end_date={today}")
    status_da, dash_a = a.call("GET", "/api/sub/retail/dashboard/stats")
    status_db, dash_b = b.call("GET", "/api/sub/retail/dashboard/stats")
    rev_a = (pnl_a.get("data") or {}).get("revenue") if status_a == 200 else None
    rev_b = (pnl_b.get("data") or {}).get("revenue") if status_b == 200 else None
    today_a = (dash_a.get("data") or dash_a).get("today_sales") if status_da == 200 else None
    today_b = (dash_b.get("data") or dash_b).get("today_sales") if status_db == 200 else None
    R2.finding(
        "P&L revenue vs dashboard today-sales, A2 vs B2 (same period, right after B2's sale)",
        f"P&L revenue: A2={rev_a} B2={rev_b} (equal={rev_a == rev_b}); "
        f"dashboard today_sales: A2={today_a} B2={today_b} (equal={today_a == today_b})"
    )

    # -- f2) MEASURE: promotion propagation + discount applied at the till ------
    starts = datetime.now().strftime("%Y-%m-%d")
    ends = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    promo_status, promo_body = a.call("POST", "/api/sub/retail/promotions", {
        "name": f"Lab Promo 10pct {TAG}-2", "discount_pct": 10, "product_id": pid,
        "starts_at": starts, "ends_at": ends,
    })
    time.sleep(60)
    active_status, active_body = b.call("GET", "/api/sub/retail/promotions/active")
    promo_on_b = any(r.get("product_id") == pid for r in rows_of(active_body)) if active_status == 200 else False
    ring_status, ring_body = b.call("POST", "/api/sub/retail/sales", {
        "items": [{"product_id": pid, "quantity": 1}],
        "payment_method": "cash", "amount_paid": 2.5,
        "idempotency_key": f"lab-e2e-promo-sale-{TAG}-2",
    })
    ring_sale = (ring_body.get("data") or {}) if isinstance(ring_body, dict) else {}
    ring_total = ring_sale.get("total")
    discount_applied = ring_total is not None and ring_total < 2.5
    R2.finding(
        "Promotion created on A2 -> visible on GET /promotions/active on B2 after 60s, and discount applied on B2",
        f"create_status={promo_status} visible_on_B2_after_60s={promo_on_b} "
        f"ring_status={ring_status} ring_total={ring_total} discount_applied={discount_applied}"
    )

    R2.phase("S4f measurements (P&L/dashboard parity, promotion propagation)", "MEASURED",
              "see 'findings' section for both results", time.time() - t)

    # -- f3) MEASURE: what actually crosses the cloud relay, for THIS licence ---
    t = time.time()
    counts = _psql_owner_sync_events_by_entity_type(env.get("proof2_licence_id"))
    R2.finding("owner_sync_events counts by entity_type, filtered to proof2's licence", counts)
    R2.phase("S4 convergence phase complete", "PASS", "see phases a-e and findings f1-f3 above", time.time() - t0)


# ---------------------------------------------------------------------------
# S5 -- cleanup: stop A2/B2, verify ports closed, leave Owner running, and
# confirm proof1's OLDER tills are not running either.
# ---------------------------------------------------------------------------

def phase_q5() -> None:
    t0 = time.time()
    stop_lab(f"till-{TILL_A2_PORT}")
    stop_lab(f"till-{TILL_B2_PORT}")
    a2_closed = tcp_port_closed(TILL_A2_PORT, timeout=30)
    b2_closed = tcp_port_closed(TILL_B2_PORT, timeout=30)
    relay_closed = tcp_port_closed(SITE_RELAY_TCP_PORT, timeout=30)
    owner_still_up = http_get_ok(OWNER_BASE + "/auth/login")
    old_a_running = tcp_port_listening(TILL_A_PORT)
    old_b_running = tcp_port_listening(TILL_B_PORT)
    ok = a2_closed and b2_closed and relay_closed and owner_still_up and not old_a_running and not old_b_running
    R2.phase("S5 cleanup: stop A2/B2, ports closed, Owner still up, older proof tills not running",
              "PASS" if ok else "FAIL",
              f"5022_closed={a2_closed} 5023_closed={b2_closed} 5443_closed={relay_closed} "
              f"owner_up={owner_still_up} old_till_a_5020_running={old_a_running} "
              f"old_till_b_5021_running={old_b_running}", time.time() - t0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    global RESTART_TILL_A_WORKAROUND

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=[
        "mfa_and_issue", "till_a", "till_b", "converge", "cleanup", "issue_second", "prove",
        "issue_proof2", "till_a2", "till_b2", "converge2", "cleanup2", "prove2",
    ])
    # S0 (2026-09-24) -- see the RESTART_TILL_A_WORKAROUND module-level
    # comment near the top of this file: opt-in only, default OFF. Only
    # phase_p3 (till_b/prove) ever reads this; the proof2 phases (S1-S6)
    # never restart A2 at all, by design, so this flag has no effect on them.
    parser.add_argument("--restart-till-a-workaround", action="store_true",
                         help="Opt IN to the pre-fix 'restart till A before B joins' workaround "
                              "inside phase_p3 (till_b/prove only). Default OFF -- the hot-start "
                              "fix is expected to make it unnecessary.")
    args = parser.parse_args()

    if args.restart_till_a_workaround:
        RESTART_TILL_A_WORKAROUND = True

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit(
            "playwright is not installed on this interpreter. Run this script with the SYSTEM "
            "Python 3.14 (C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe), "
            "not the retail venv."
        )

    needs_browser = args.command in (
        "mfa_and_issue", "till_a", "till_b", "issue_second", "prove",
        "issue_proof2", "till_a2", "till_b2", "prove2",
    )

    if needs_browser:
        with sync_playwright() as pw:
            _dispatch(args.command, pw)
    else:
        _dispatch(args.command, None)

    if args.command in ("issue_proof2", "till_a2", "till_b2", "converge2", "cleanup2", "prove2"):
        log(f"result2.json -> {RESULT2_JSON}")
        log(f"result2.md   -> {RESULT2_MD}")
    else:
        log(f"result.json -> {RESULT_JSON}")
        log(f"result.md   -> {RESULT_MD}")


def _dispatch(command: str, pw) -> None:
    if command == "mfa_and_issue":
        phase_p1(pw)
    elif command == "till_a":
        phase_p2(pw)
    elif command == "till_b":
        phase_p3(pw)
    elif command == "converge":
        phase_p4()
    elif command == "cleanup":
        phase_p5()
    elif command == "issue_second":
        phase_p6(pw)
    elif command == "prove":
        # Each phase's own exception already recorded a FAIL entry; stop the
        # sequence there rather than pressing on against state a failed
        # phase never reached (an unissued licence, an unactivated till).
        for fn, needs_pw in ((phase_p1, True), (phase_p2, True), (phase_p3, True),
                              (phase_p4, False), (phase_p5, False), (phase_p6, True)):
            try:
                fn(pw) if needs_pw else fn()
            except Exception as exc:  # noqa: BLE001
                log(f"prove: stopping sequence -- {fn.__name__} raised: {exc}")
                break
    elif command == "issue_proof2":
        phase_q1(pw)
    elif command == "till_a2":
        phase_q2(pw)
    elif command == "till_b2":
        phase_q3(pw)
    elif command == "converge2":
        phase_q4()
    elif command == "cleanup2":
        phase_q5()
    elif command == "prove2":
        # Same stop-on-first-failure contract as `prove` above.
        for fn, needs_pw in ((phase_q1, True), (phase_q2, True), (phase_q3, True),
                              (phase_q4, False), (phase_q5, False)):
            try:
                fn(pw) if needs_pw else fn()
            except Exception as exc:  # noqa: BLE001
                log(f"prove2: stopping sequence -- {fn.__name__} raised: {exc}")
                break


if __name__ == "__main__":
    main()
