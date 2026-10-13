"""The seam between `SyncService` (shared, product-agnostic) and everything a
bootstrap needs from the product and the device.

`SyncService` calls these at four moments of a pull; each is optional and a hook
that raises is logged and ignored -- a bootstrap nicety must never stop a pull
(or, worse, the sale that follows it):

  include_own_once()   -> bool   ask the relay for this device's OWN events too
  include_own_done()             the relay honoured it; stop asking
  reapply_outbox_pending() -> bool / reapply_outbox_done()
                                 after an install: re-apply this device's unpushed
                                 outbox to the new database, once
  on_resync_required(stream, client, relay_url, details) -> bool
                                 the relay said RESYNC_REQUIRED for this stream;
                                 True = a copy was requested and staged (installed
                                 at the next start)
  on_bootstrap_requests(requests)
                                 a hub told this (producer-capable) device that
                                 other devices are waiting on the main computer
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class BootstrapHooks:
    #: Capabilities advertised in the pull body (`caps`). Empty = advertise none.
    caps: tuple = ()
    include_own_once: Optional[Callable[[], bool]] = None
    include_own_done: Optional[Callable[[], None]] = None
    reapply_outbox_pending: Optional[Callable[[], bool]] = None
    reapply_outbox_done: Optional[Callable[[], None]] = None
    on_resync_required: Optional[Callable[[str, object, str, dict], bool]] = None
    on_bootstrap_requests: Optional[Callable[[list], None]] = None
