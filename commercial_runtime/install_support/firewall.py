"""Windows Firewall rule for the shop network (INSTALL-1, review #8 item 4).

THE PROBLEM. The till can act as the shop's hub: it listens on the LAN so phones
and other tills can sync with it. On Windows the first time an exe listens on a
network port the firewall shows its own prompt -- in the middle of a shift, in
front of whoever happens to be at the till, with a choice they cannot judge
("Private / Public networks"). Allowing nothing means the phones silently never
connect; allowing the wrong profile opens the till on a cafe wifi.

THE ANSWER. Ask once, at a moment the OWNER chose: when an administrator presses
"Connect a device" (POST /api/site-relay/pair-code -- the only deliberate
"I am putting a second device on this shop network" act the product has; there
is no separate "Shop network" switch, the hub starts by itself once the licence
is active). At that moment we add ONE inbound rule for THIS exe, private and
domain networks only (never public), with a single UAC prompt:

    netsh advfirewall firewall add rule name="Aura Retail (shop network)"
          dir=in action=allow program="<exe>" enable=yes profile=private,domain

The install is per-user (no admin), so the installer cannot do this; a UAC prompt
here, explained by what the owner just pressed, is the honest place for it.

GUARANTEES, each pinned by a test:
  * never on a dev run (`python app.py`): only a frozen exe has an exe to allow;
  * idempotent: the rule is looked up first and never added twice;
  * a declined UAC prompt is RECORDED (firewall_state.json -> status "declined")
    so Settings can say "the shop network is blocked, press Fix" later, and is
    not re-asked on every later press -- only an explicit retry asks again;
  * the response to the owner is never held up by the UAC dialog (it runs on a
    thread), and two quick presses cannot open two prompts.

Android: this module is imported by the shared app.py. Nothing here touches
ctypes.windll or subprocess at import time; every Windows call is inside a
function that a non-frozen process never reaches.
"""
from __future__ import annotations

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

RULE_NAME = "Aura Retail (shop network)"
STATE_NAME = "firewall_state.json"
PAIR_CODE_PATH = "/api/site-relay/pair-code"
ELEVATED_WAIT_SECONDS = 60

# status values
PRESENT = "present"          # rule already existed; nothing was changed
ADDED = "added"              # we added it (UAC accepted)
DECLINED = "declined"        # the person refused the UAC prompt
FAILED = "failed"            # netsh/UAC error (not a refusal)
SKIPPED_DEV = "skipped_not_installed_build"

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_is_installed_build() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def show_rule_args() -> list:
    return ["netsh", "advfirewall", "firewall", "show", "rule", "name=" + RULE_NAME]


def add_rule_params(exe: str) -> str:
    """The parameter string handed to the ELEVATED netsh. `exe` is our own
    sys.executable, but a quote in it would break out of program="..." so it is
    refused rather than escaped."""
    if '"' in exe or "\n" in exe or "\r" in exe:
        raise ValueError("unsafe executable path")
    return ('advfirewall firewall add rule name="%s" dir=in action=allow program="%s" '
            "enable=yes profile=private,domain" % (RULE_NAME, exe))


def default_rule_exists() -> bool:
    """netsh exits 0 when a rule with that name exists and 1 ("No rules match
    the specified criteria") when none does."""
    result = subprocess.run(show_rule_args(), capture_output=True, text=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return result.returncode == 0


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
    state = {"status": status, "rule_name": RULE_NAME, "exe": exe, "at": _now()}
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
    rule_exists: Callable[[], bool] = default_rule_exists,
    run_elevated: Callable[[str], str] = default_run_elevated,
    retry_after_decline: bool = False,
) -> dict:
    """Make sure the shop-network rule exists. Returns the recorded state dict.
    Every branch logs one line and records a status, so "never tried" and
    "tried and declined" cannot look alike."""
    installed = (installed_build or default_is_installed_build)()
    if not installed:
        log.info("firewall: skipped -- not an installed build")
        return {"status": SKIPPED_DEV, "rule_name": RULE_NAME}
    exe = exe or sys.executable
    with _lock:
        previous = read_state(app_data_dir)
        if previous.get("status") == DECLINED and not retry_after_decline:
            log.info("firewall: not asking again -- the person declined earlier (retry is explicit)")
            return previous
        try:
            exists = rule_exists()
        except Exception:
            log.warning("firewall: could not look the rule up; treating as failed", exc_info=True)
            return _write_state(app_data_dir, FAILED, exe)
        if exists:
            log.info("firewall: rule already present; nothing to do")
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
            # Trust but verify: a netsh that exited 0 for the wrong rule would
            # leave the phones blocked with a green status.
            try:
                verified = rule_exists()
            except Exception:
                verified = False
            if verified:
                log.info("firewall: rule added and verified")
                return _write_state(app_data_dir, ADDED, exe)
            log.warning("firewall: netsh reported success but the rule is not there")
        return _write_state(app_data_dir, FAILED, exe)


def make_firewall_blueprint(app_data_dir, *, ensure: Callable[..., dict] = ensure_firewall_rule,
                            thread_factory=threading.Thread):
    """GET /api/site-relay/firewall -> the recorded status (Settings can show it
    later). POST .../firewall/retry (admin) asks again after a decline. And an
    after-request hook that fires the one-time ask when an admin's "Connect a
    device" press succeeded."""
    from flask import Blueprint, jsonify, request, session

    bp = Blueprint("install_firewall", __name__)
    in_flight = threading.Event()

    def _run(**kwargs):
        try:
            ensure(app_data_dir, **kwargs)
        except Exception:
            log.exception("firewall: ensure crashed")
        finally:
            in_flight.clear()

    def _start(**kwargs) -> bool:
        if in_flight.is_set():
            return False
        in_flight.set()
        thread_factory(target=_run, kwargs=kwargs, name="aura-firewall-rule", daemon=True).start()
        return True

    @bp.route("/api/site-relay/firewall", methods=["GET"])
    def firewall_status():
        if "mt_user_id" not in session:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        state = read_state(app_data_dir)
        return jsonify({"success": True, "status": state.get("status"), "rule_name": RULE_NAME,
                        "at": state.get("at")})

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
