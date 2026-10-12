"""Is OUR Edge/Chrome app window still open? (INSTALL-1, verifier M1)

THE PROBLEM. On a PC without WebView2 the till falls back to an Edge/Chrome
`--app` window with its own `--user-data-dir`. A second `msedge --app ...
--user-data-dir=<same profile>` while the first window is open does not start a
new browser: it hands the URL to the running one and EXITS after ~0.35 s
(measured). The launcher used to read "the process I started ended" as "the
person closed the window", so after a restore (old child dies, its window
survives, the new child's launch hands off and exits) it quit -- leaving a dead
server behind 10 live Edge processes.

THE ANSWER. Ask the right question: "is there any msedge/chrome process whose
command line contains OUR --user-data-dir?" Renderer/GPU children carry the same
flag, so the answer stays True until the whole browser for that profile is gone.

No new dependency: psutil is not in requirements, so the process list comes from
PowerShell's Get-CimInstance (present on every Windows 10 1809+ box). Anything
that goes wrong while listing is logged and treated as "no window", which is the
old behaviour (quit), never a hang.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from typing import Callable, Iterable

log = logging.getLogger("aura.browser_profile")

_BROWSER_IMAGES = ("msedge.exe", "chrome.exe")
_LIST_COMMAND = (
    "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\" "
    "| ForEach-Object { $_.CommandLine }"
)


def default_list_command_lines() -> list:
    """Command lines of every Edge/Chrome process. Raises on failure (the caller
    decides what failure means)."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _LIST_COMMAND],
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        raise RuntimeError("process listing failed: %s" % (result.stderr or "").strip()[:200])
    return [line for line in result.stdout.splitlines() if line.strip()]


def _norm(text: str) -> str:
    return text.replace("/", "\\").lower()


def command_line_uses_profile(command_line: str, profile_dir: str) -> bool:
    """True when `--user-data-dir=<profile_dir>` (quoted or not, either slash,
    any case, optional trailing backslash) is in the command line. A different
    profile whose name merely starts the same (app_window2) does not match."""
    profile = _norm(str(profile_dir)).rstrip("\\")
    pattern = r"--user-data-dir=[\"']?" + re.escape(profile) + r"[\\]?[\"']?(?=\s|$)"
    return re.search(pattern, _norm(command_line)) is not None


def profile_in_use(profile_dir: str,
                   list_command_lines: Callable[[], Iterable[str]] = default_list_command_lines) -> bool:
    """Is any Edge/Chrome process running on this profile?"""
    if list_command_lines is default_list_command_lines and sys.platform != "win32":
        return False
    try:
        lines = list(list_command_lines())
    except Exception as exc:
        log.warning("could not list browser processes (%s); assuming our window is gone", exc)
        return False
    return any(command_line_uses_profile(line, profile_dir) for line in lines)
