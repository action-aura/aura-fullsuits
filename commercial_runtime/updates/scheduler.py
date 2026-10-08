"""Background "is there an update?" poll.

CHECK ONLY. This never downloads and never installs: replacing the running
application is something a person decides, at a moment that suits the shop
(not mid-sale), so the poll's whole job is to make the "Update ready" pill
appear. It is started by the retail app only when an Owner URL is configured
(see products/retail/backend/app.py) -- an install with no Owner opens no
timer and makes no network call.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger("aura.updates")

FIRST_CHECK_DELAY_SECONDS = 120.0   # let the till finish booting and the licence check in first
CHECK_INTERVAL_SECONDS = 24 * 60 * 60


class UpdateScheduler:
    def __init__(
        self,
        service,
        *,
        first_delay: float = FIRST_CHECK_DELAY_SECONDS,
        interval: float = CHECK_INTERVAL_SECONDS,
        timer_factory: Callable[..., threading.Timer] = threading.Timer,
    ):
        self._service = service
        self._first_delay = first_delay
        self._interval = interval
        self._timer_factory = timer_factory
        self._timer = None
        self._stopped = False
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._timer is not None or self._stopped:
                log.info("updates: scheduler start ignored (already started or stopped)")
                return
            self._arm(self._first_delay)
        log.info("updates: scheduler started (first check in %ss, then every %ss)", self._first_delay, self._interval)

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self._timer is not None:
                self._timer.cancel()
        log.info("updates: scheduler stopped")

    def _arm(self, delay: float) -> None:
        timer = self._timer_factory(delay, self._tick)
        timer.daemon = True  # never keep the app alive just to poll
        self._timer = timer
        timer.start()

    def _tick(self) -> None:
        try:
            self._service.check_quietly()
        except Exception:
            # check_quietly already swallows; this is the belt for a service
            # double that does not. A dead timer chain would stop all polling.
            log.exception("updates: scheduler tick crashed")
        finally:
            with self._lock:
                if not self._stopped:
                    self._arm(self._interval)
