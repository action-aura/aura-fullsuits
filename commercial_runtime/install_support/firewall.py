"""Windows Firewall rule for the shop network (INSTALL-1, review #8 item 4).

THE PROBLEM. The till can act as the shop's hub: it listens on the LAN so phones
and other tills can sync with it. On Windows the first time an exe listens on a
network port the firewall shows its own prompt -- in the middle of a shift, in
front of whoever happens to be at the till, with a choice they cannot judge
("Private / Public networks"). Allowing nothing means the phones silently never
connect; allowing the wrong profile opens the till on a cafe wifi.

THE ANSWER. Ask once, at a moment the OWNER chose, and BEFORE the hub first binds
the LAN port (the bind is what makes Windows show its own prompt, in front of
whoever is at the till, with a choice they cannot judge):

  * when a licence ACTIVATION succeeds (verifier M3: the hub starts by itself on
    the next boot once the licence is ACTIVE, so this is the last deliberate
    moment before that bind), and
  * when an administrator presses "Connect a device" (POST
    /api/site-relay/pair-code) -- the only way to cover a till that was already
    activated before this version.

We add ONE inbound rule for THIS exe, private and domain networks only (never
public), with a single UAC prompt:

    netsh advfirewall firewall add rule name="Aura Retail (shop network) [<hash>]"
          dir=in action=allow program="<exe>" enable=yes profile=private,domain

The install is per-user (no admin), so the installer cannot do this; a UAC prompt
here, explained by what the owner just did, is the honest place for it.

LOOKUP IS BY PROGRAM PATH, NOT BY NAME (verifier M3). A second Windows user's
per-user install, a reinstall to another folder or a leftover test install all
share the name, so "a rule with that name exists" said "present" for an exe that
had no rule. We ask the firewall for the rules whose Program is THIS exe, and the
rule we add carries a short hash of the exe path in its name so two installs do
not collide.

CANCEL ON WINDOWS' OWN PROMPT. If the person pressed Cancel on Windows' prompt,
Windows wrote inbound BLOCK rules for the exe, and Block beats Allow. We never
delete a user's rule; we record status "blocked_by_windows_prompt" so a Settings
line can tell the owner, and we do not pretend the shop network works.

GUARANTEES, each pinned by a test:
  * never on a dev run (`python app.py`) or a non-Windows build: only a frozen
    Windows exe has an exe to allow;
  * idempotent: the rules are looked up first and nothing is added twice;
  * a declined UAC prompt is RECORDED (firewall_state.json -> status "declined")
    so Settings can say "the shop network is blocked, press Fix" later, and is
    not re-asked on every later trigger -- only an explicit retry asks again;
  * the response to the owner is never held up by the UAC dialog (it runs on a
    thread), and at most ONE prompt is ever in flight, across both triggers.

Android: this module is imported by the shared app.py. Nothing here touches
ctypes.windll or subprocess at import time; every Windows call is inside a
function that a non-frozen process never reaches.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("aura.firewall")

RULE_NAME = "Aura Retail (shop network)"      # prefix; rule_name_for() appends the exe hash
STATE_NAME = "firewall_state.json"
PAIR_CODE_PATH = "/api/site-relay/pair-code"
ELEVATED_WAIT_SECONDS = 60

# status values
PRESENT = "present"          # rule already existed; nothing was changed
ADDED = "added"              # we added it (UAC accepted)
DECLINED = "declined"        # the person refused the UAC prompt
FAILED = "failed"            # netsh/UAC error (not a refusal)
BLOCKED = "blocked_by_windows_prompt"   # inbound Block rule(s) for this exe exist (Block beats Allow)
SKIPPED_DEV = "skipped_not_installed_build"

_lock = threading.Lock()
_in_flight = threading.Event()     # one prompt at a time, across every trigger


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_is_installed_build() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def _norm_path(path) -> str:
    return str(path).replace("/", "\\").lower()


def rule_name_for(exe: str) -> str:
    """The name of the rule we add: stable for one exe path, different for two."""
    tag = hashlib.sha256(_norm_path(exe).encode("utf-8")).hexdigest()[:8]
    return "%s [%s]" % (RULE_NAME, tag)


def add_rule_params(exe: str) -> str:
    """The parameter string handed to the ELEVATED netsh. `exe` is our own
    sys.executable, but a quote in it would break out of program="..." so it is
    refused rather than escaped."""
    if '"' in exe or "\n" in exe or "\r" in exe:
        raise ValueError("unsafe executable path")
    return ('advfirewall firewall add rule name="%s" dir=in action=allow program="%s" '
            "enable=yes profile=private,domain" % (rule_name_for(exe), exe))


def _lookup_script(exe: str) -> str:
    quoted = exe.replace("'", "''")
    return (
        "Get-NetFirewallApplicationFilter -Program '%s' -ErrorAction SilentlyContinue | "
        "ForEach-Object { $r = $_ | Get-NetFirewallRule; "
        "[pscustomobject]@{ name=[string]$r.DisplayName; direction=[string]$r.Direction; "
        "action=[string]$r.Action; enabled=[string]$r.Enabled; program=[string]$_.Program } } | "
        "ConvertTo-Json -Compress" % quoted)


def default_list_rules(exe: str) -> list:
    """Every firewall rule whose Program is `exe`, as dicts with name /
    direction / action / enabled / program (strings: Get-NetFirewallRule's enums
    are locale-independent, unlike `netsh ... show rule` output). Raises on a
    lookup failure -- "could not look" must never read as "no rule"."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _lookup_script(exe)],
        capture_output=True, text=True, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # A lookup with no match makes PowerShell exit 1 with an EMPTY stderr (the
    # suppressed "no matching filter" error); a real failure (cmdlet missing,
    # access denied) always writes to stderr. Only the second is an error.
    if result.returncode != 0 and (result.stderr or "").strip():
        raise RuntimeError("firewall lookup failed: %s" % result.stderr.strip()[:200])
    text = result.stdout.strip()
    if not text:
        return []
    loaded = json.loads(text)
    return loaded if isinstance(loaded, list) else [loaded]


def _is(value, *names) -> bool:
    return str(value).strip().lower() in names


def classify_rules(rules, exe: str) -> tuple:
    """(allowed, blocked): is there an enabled INBOUND allow / block rule for
    exactly this program? Rules for other programs, outbound rules and disabled
    rules do not count."""
    allowed = blocked = False
    target = _norm_path(exe)
    for rule in rules or []:
        if _norm_path(rule.get("program", "")) != target:
            continue
        if not (_is(rule.get("direction"), "inbound", "1") and _is(rule.get("enabled"), "true", "1")):
            continue
        if _is(rule.get("action"), "allow", "2"):
            allowed = True
        elif _is(rule.get("action"), "block", "4"):
            blocked = True
    return allowed, blocked


def default_run_elevated(exe: str) -> str:
    """ShellExecuteEx with the "runas" verb: shows ONE UAC prompt and runs netsh
    elevated. Returns "ok" (netsh ran and exited 0), "declined" (the person
    pressed No / closed the prompt: ERROR_CANCELLED 1223) or "failed"."""
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    ERROR_CANCELLED = 1223
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = "netsh.exe"
    info.lpParameters = add_rule_params(exe)
    info.nShow = 0  # SW_HIDE
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        return "declined" if ctypes.GetLastError() == ERROR_CANCELLED else "failed"
    try:
        kernel32.WaitForSingleObject(info.hProcess, ELEVATED_WAIT_SECONDS * 1000)
        code = wintypes.DWORD(1)
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return "ok" if code.value == 0 else "failed"
    finally:
        kernel32.CloseHandle(info.hProcess)


def read_state(app_data_dir) -> dict:
    try:
        loaded = json.loads((Path(app_data_dir) / STATE_NAME).read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(app_data_dir, status: str, exe: Optional[str]) -> dict:
    state = {"status": status, "rule_name": rule_name_for(exe) if exe else RULE_NAME, "exe": exe, "at": _now()}
    try:
        path = Path(app_data_dir) / STATE_NAME
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        log.warning("firewall: could not record status %s", status, exc_info=True)
    return state


def ensure_firewall_rule(
    app_data_dir,
    *,
    exe: Optional[str] = None,
    installed_build: Optional[Callable[[], bool]] = None,
    list_rules: Optional[Callable[[str], list]] = None,
    run_elevated: Optional[Callable[[str], str]] = None,
    retry_after_decline: bool = False,
) -> dict:
    """Make sure the shop-network rule exists for THIS exe. Returns the recorded
    state dict. Every branch logs one line and records a status, so "never
    tried" and "tried and declined" cannot look alike."""
    installed = (installed_build or default_is_installed_build)()
    if not installed:
        log.info("firewall: skipped -- not an installed Windows build")
        return {"status": SKIPPED_DEV, "rule_name": RULE_NAME}
    list_rules = list_rules or default_list_rules
    run_elevated = run_elevated or default_run_elevated
    exe = exe or sys.executable
    with _lock:
        previous = read_state(app_data_dir)
        # A decline belongs to the exe that was asked about: a reinstall to
        # another folder is a different program and is asked afresh.
        if (previous.get("status") == DECLINED and not retry_after_decline
                and _norm_path(previous.get("exe") or "") == _norm_path(exe)):
            log.info("firewall: not asking again -- the person declined earlier (retry is explicit)")
            return previous
        try:
            allowed, blocked = classify_rules(list_rules(exe), exe)
        except Exception:
            log.warning("firewall: could not look the rules up; treating as failed", exc_info=True)
            return _write_state(app_data_dir, FAILED, exe)
        if blocked:
            # Windows' own prompt was answered Cancel (or someone blocked us).
            # Block beats Allow, so adding ours would change nothing; and a
            # user's rule is not ours to delete. Record it so Settings can say so.
            log.warning("firewall: inbound BLOCK rule(s) exist for this program; recording, not touching")
            return _write_state(app_data_dir, BLOCKED, exe)
        if allowed:
            log.info("firewall: an inbound allow rule for this program already exists; nothing to do")
            return _write_state(app_data_dir, PRESENT, exe)
        log.info("firewall: adding the shop-network rule (one UAC prompt)")
        try:
            outcome = run_elevated(exe)
        except Exception:
            log.warning("firewall: the elevated step crashed", exc_info=True)
            outcome = "failed"
        if outcome == "declined":
            log.info("firewall: the person declined the UAC prompt")
            return _write_state(app_data_dir, DECLINED, exe)
        if outcome == "ok":
            # Trust but verify, by program path: a netsh that exited 0 for the
            # wrong rule would leave the phones blocked with a green status.
            try:
                allowed, blocked = classify_rules(list_rules(exe), exe)
            except Exception:
                allowed = blocked = False
            if blocked:
                return _write_state(app_data_dir, BLOCKED, exe)
            if allowed:
                log.info("firewall: rule added and verified")
                return _write_state(app_data_dir, ADDED, exe)
            log.warning("firewall: netsh reported success but no allow rule for this program is there")
        return _write_state(app_data_dir, FAILED, exe)


def request_rule_async(app_data_dir, *, ensure: Optional[Callable[..., dict]] = None,
                       thread_factory=threading.Thread, **kwargs) -> bool:
    """Start the ask on a thread so the caller's response is never held up by a
    UAC dialog. At most one is ever in flight: a second trigger while one runs
    returns False and does nothing. Used by BOTH triggers (licence activation,
    "Connect a device")."""
    if _in_flight.is_set():
        return False
    _in_flight.set()
    ensure = ensure or ensure_firewall_rule

    def _run():
        try:
            ensure(app_data_dir, **kwargs)
        except Exception:
            log.exception("firewall: ensure crashed")
        finally:
            _in_flight.clear()

    try:
        thread_factory(target=_run, name="aura-firewall-rule", daemon=True).start()
    except Exception:
        _in_flight.clear()
        raise
    return True


def with_firewall_ask_after_activation(existing_hook: Optional[Callable[[], object]], app_data_dir, *,
                                       ask: Optional[Callable[..., bool]] = None) -> Callable[[], None]:
    """Wrap the licensing blueprint's on_activation_success hook: run the
    product's own hook, then ask for the firewall rule. The ask never raises
    (an activation that succeeded must not be reported as failed), and
    ensure_firewall_rule itself refuses on a dev run / non-Windows build."""

    def hook() -> None:
        try:
            if existing_hook is not None:
                existing_hook()
        finally:
            try:
                (ask or request_rule_async)(app_data_dir)
            except Exception:
                log.exception("firewall: could not start the ask after activation")

    return hook


def make_firewall_blueprint(app_data_dir, *, ensure: Callable[..., dict] = ensure_firewall_rule,
                            thread_factory=threading.Thread):
    """GET /api/site-relay/firewall -> the recorded status (Settings can show it
    later). POST .../firewall/retry (admin) asks again after a decline. And an
    after-request hook that fires the one-time ask when an admin's "Connect a
    device" press succeeded."""
    from flask import Blueprint, jsonify, request, session

    bp = Blueprint("install_firewall", __name__)

    def _start(**kwargs) -> bool:
        return request_rule_async(app_data_dir, ensure=ensure, thread_factory=thread_factory, **kwargs)

    @bp.route("/api/site-relay/firewall", methods=["GET"])
    def firewall_status():
        if "mt_user_id" not in session:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        state = read_state(app_data_dir)
        return jsonify({"success": True, "status": state.get("status"),
                        "rule_name": state.get("rule_name") or RULE_NAME, "at": state.get("at")})

    @bp.route("/api/site-relay/firewall/retry", methods=["POST"])
    def firewall_retry():
        if session.get("mt_role") != "admin":
            return jsonify({"success": False, "error": "Admin only."}), 403
        started = _start(retry_after_decline=True)
        return jsonify({"success": True, "started": started})

    @bp.after_app_request
    def _after(response):
        try:
            if (request.method == "POST" and request.path == PAIR_CODE_PATH
                    and response.status_code == 200 and session.get("mt_role") == "admin"):
                _start()
        except Exception:
            log.exception("firewall: after-request hook failed")
        return response

    return bp
