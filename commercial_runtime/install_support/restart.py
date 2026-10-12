"""Restart-after-restore (INSTALL-1, review F16).

WHY THIS EXISTS. A restore replaces the live database files under a running
process. The old message told the shopkeeper to "restart the application", which
a person at a till does not know how to do safely (closing the window can leave
the server half alive; see the Edge-fallback note in launcher_retail.py). So the
backend now restarts itself: it answers the restore request, then exits with a
distinct code, and the desktop launcher's supervisor treats that code as
"relaunch me" rather than "the app is closing".

WHO OPTS IN. Only a process started by the launcher's supervisor sets
AURA_RESTART_ON_RESTORE=1. A plain `python app.py` run (and every in-process
test that drives the restore route through a Flask test client) does NOT, so a
restore there keeps the old behaviour -- the response asks for a manual restart
-- and, crucially, a test suite that restores a backup can never `os._exit`
itself. A developer who wants the exit on a dev run sets the variable; the
process then simply exits with code 75 (nothing relaunches it).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Mapping, Optional

log = logging.getLogger("aura.restart")

# 75 is EX_TEMPFAIL in sysexits.h ("try again"): not a code Python, Flask,
# waitress, PyInstaller or Windows produce for a crash or a normal quit.
RESTART_EXIT_CODE = 75
RESTART_ENV = "AURA_RESTART_ON_RESTORE"

# The restore response is a few hundred bytes, but it still has to leave the
# socket before the process dies. Waitress writes it from a worker thread
# right after the view returns; this is generous for that without making the
# shopkeeper stare at a frozen screen.
RESTART_DELAY_SECONDS = 1.5


def restart_supported(env: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if env is None else env
    return env.get(RESTART_ENV, "").strip() == "1"


def schedule_restart(
    delay: float = RESTART_DELAY_SECONDS,
    *,
    env: Optional[Mapping[str, str]] = None,
    exit_fn: Optional[Callable[[int], None]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> bool:
    """Arms the delayed exit. Returns True if an exit was scheduled, False when
    this process did not opt in (the caller then keeps the manual-restart
    message). Logged either way, because "never armed" and "armed and fired"
    must not look identical in a log."""
    if not restart_supported(env):
        log.info("restart: not scheduled -- %s is not set (dev run or test)", RESTART_ENV)
        return False
    do_exit = exit_fn or os._exit

    def _later() -> None:
        sleep_fn(delay)
        log.info("restart: exiting with code %d so the launcher relaunches", RESTART_EXIT_CODE)
        logging.shutdown()           # os._exit skips atexit; flush the log first
        do_exit(RESTART_EXIT_CODE)

    threading.Thread(target=_later, name="aura-restart-after-restore", daemon=True).start()
    log.info("restart: scheduled in %.1fs", delay)
    return True
