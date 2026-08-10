"""Phase 9R M5 -- scheduler ownership gate.

Any CLI command meant to be triggered by an external scheduler (a systemd
timer, cron) rather than a human should call require_scheduler_owner()
before doing real work. This is a cross-cutting concern, not specific to
any one domain (reports, commercial-ops expiry/reconcile/device-limit scans
all qualify) -- deliberately not folded into any single domain module.

Why this exists as an explicit check rather than relying only on "only
install the timer on one host": the unique-constraint/advisory-lock
idempotency already present in report_scheduler.generate_snapshot() (and
the dedup keys in expiry_scan/device_slot_ops) protects DATA correctness
even if two hosts fire at once -- but it doesn't stop a second,
accidentally-enabled host from doing real work (a wasted query pass, a
misleading log line, a duplicate external notification attempt before the
dedup check catches it). This check stops the attempt before any of that,
using the same OWNER_SCHEDULER_ROLE config contract validate() already
enforces is unambiguous in staging/production (configuration-contract.md).
"""
from __future__ import annotations


class SchedulerNotOwnerError(RuntimeError):
    """Raised when a scheduled job runs on a process that isn't the
    designated scheduler owner for this environment."""


def require_scheduler_owner(config: dict) -> None:
    role = config.get("SCHEDULER_ROLE", "worker")
    if role != "owner":
        raise SchedulerNotOwnerError(
            f"OWNER_SCHEDULER_ROLE={role!r} on this process -- refusing to run a scheduled job here. "
            "Only the process with OWNER_SCHEDULER_ROLE=owner may run scheduled jobs "
            "(see docs/owner/phase9r/scheduler-topology.md)."
        )
