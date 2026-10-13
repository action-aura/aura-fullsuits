"""The hub's list of devices that asked the MAIN computer to re-announce.

A device that is told RESYNC_REQUIRED on its REGISTRY stream does not get a file
(users and permissions are never copied -- see the package docstring); it
fast-forwards its registry cursor past the pruned history and needs the main
computer to say, signed and fresh, who the shop's staff are NOW. The hub learns
of that request from the device's pull. When the hub's own computer is the main,
it can answer at once; otherwise the main is some other device on the LAN, and
this board is how the request reaches it: producer-capable pullers (`caps`
contains `bootstrap_producer_v1`) get the open requests in their pull responses.

IN MEMORY, DELIBERATELY. A request is only a nudge: losing it (a hub restart)
costs nothing permanent, because the requesting device either already
fast-forwarded past the pruned range -- and the main's next heartbeat
re-announces anyway (reannounce.run_if_due, once per epoch) -- or is still
behind, gets RESYNC_REQUIRED again on its next pull, and the request is simply
made again. A table for this would need a schema version for a hint that is
worthless after an hour.

Entries are de-duplicated per (requester, stream) so a device retrying every
tick is one entry, and expire after `ttl_seconds` so a device that was
decommissioned does not make the main re-announce forever.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

DEFAULT_TTL_SECONDS = 3600.0
MAX_OPEN_REQUESTS = 64


class BootstrapBoard:
    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS,
                 clock: Callable[[], float] = time.time):
        self._ttl = float(ttl_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._open: dict = {}   # (requester, stream) -> requested_at (float)

    def add(self, requester: str, stream: str) -> None:
        now = self._clock()
        with self._lock:
            self._expire_locked(now)
            if (requester, stream) not in self._open and len(self._open) >= MAX_OPEN_REQUESTS:
                # A flood of distinct requesters must not grow this without
                # bound; the oldest request is the least likely to still matter.
                oldest = min(self._open, key=self._open.get)
                self._open.pop(oldest, None)
            self._open.setdefault((requester, stream), now)

    def open_requests(self) -> list:
        """Open requests, oldest first, as wire-ready dicts."""
        now = self._clock()
        with self._lock:
            self._expire_locked(now)
            items = sorted(self._open.items(), key=lambda kv: kv[1])
        return [{"requester": r, "stream": s, "requested_at": at} for (r, s), at in items]

    def resolve(self, requester: str, stream: Optional[str] = None) -> int:
        with self._lock:
            keys = [k for k in self._open if k[0] == requester and (stream is None or k[1] == stream)]
            for k in keys:
                self._open.pop(k, None)
            return len(keys)

    def _expire_locked(self, now: float) -> None:
        stale = [k for k, at in self._open.items() if now - at > self._ttl]
        for k in stale:
            self._open.pop(k, None)
