"""
Aura Retail -- failure throttle for manager-PIN approvals (UI-3a).

A manager PIN is four digits: 10,000 possibilities. The KDF behind it
(commercial_runtime.identity.user_accounts.verify_user_pin, PBKDF2 at 600k rounds)
slows each guess to a few hundred milliseconds, but a script can still sweep the
space in about an hour through the sale endpoint unless something stops it. This is
that something: after `max_failures` wrong PINs for one approver inside `window_s`
seconds the approver is LOCKED for the rest of the window, and while locked even the
CORRECT PIN is refused (otherwise a sweep simply continues until it hits it).

Deliberately in-process and in memory: the till is one Flask process per computer,
the lock only has to outlast a guessing session, and a restart needing physical
access to the machine is a boundary this threat does not cross. The clock is
injected so tests do not sleep.

Keyed by whatever the caller resolved (the approver's user id when the reference
matched somebody, the typed reference otherwise) so an unknown name cannot be
guessed at for free.
"""
from __future__ import annotations

import threading
import time

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60


class PinThrottle:
    def __init__(self, max_failures=MAX_FAILURES, window_s=WINDOW_SECONDS, clock=time.monotonic):
        self._max = max_failures
        self._window = window_s
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = {}   # key -> [timestamps within the window]

    def _live(self, key):
        now = self._clock()
        kept = [t for t in self._failures.get(key, ()) if now - t < self._window]
        if kept:
            self._failures[key] = kept
        else:
            self._failures.pop(key, None)
        return kept

    def is_locked(self, key) -> bool:
        with self._lock:
            return len(self._live(key)) >= self._max

    def retry_after(self, key) -> int:
        """Seconds until the oldest counted failure ages out (0 when not locked)."""
        with self._lock:
            kept = self._live(key)
            if len(kept) < self._max:
                return 0
            return max(1, int(self._window - (self._clock() - kept[0])) + 1)

    def record_failure(self, key) -> None:
        with self._lock:
            self._live(key)
            self._failures.setdefault(key, []).append(self._clock())

    def reset(self, key) -> None:
        with self._lock:
            self._failures.pop(key, None)
