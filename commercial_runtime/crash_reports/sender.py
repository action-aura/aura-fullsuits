"""Crash reports -- the sender thread (the other half of the outbox).

Reads pending rows from the local store and ships them to Owner, device-signed.
Started by the retail app ONLY when an Owner URL is configured (see
products/retail/backend/app.py) -- an install with no Owner builds no sender,
opens no thread and can never make a network call for this feature. Capture
still happens locally in that case; it is just never sent anywhere.

Timing: first run 5 minutes after start (let the till finish booting and the
licence check in), then every 30 minutes. A failed run doubles the wait, up to
6 hours; a good run resets it. A bad network must not turn into a hammer.

Every decision logs one line. "Never started", "ran and had nothing to send"
and "ran and was refused" all look identical in a log if they log nothing, and
a silent early return here would be undiagnosable from a shop's log file.

Privacy: rows leave through `_payload_for`, which re-scrubs the message and
re-validates the frames AGAIN even though the store only ever holds scrubbed
data. The sender is the last place that can stop a bad row (an older build's, a
hand-edited file) from leaving the till, so it does not trust the store.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from commercial_runtime.licensing_contracts.client import LicensingClient, LicensingClientConfig
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository

from . import scrub
from .store import CrashStore

log = logging.getLogger("aura.crash_reports")

FIRST_RUN_DELAY_SECONDS = 5 * 60
INTERVAL_SECONDS = 30 * 60
MAX_INTERVAL_SECONDS = 6 * 60 * 60
BATCH_ROWS = 20
# Owner refuses a request body over 64 KB. 40 KB leaves room for the envelope
# and the signature, so a batch of long-framed reports is split across runs
# instead of being rejected whole and retried forever.
MAX_BATCH_BYTES = 40 * 1024


class CrashReportSender:
    def __init__(
        self,
        store: CrashStore,
        client_factory: Callable[[], object],
        signer_factory: Callable[[], object],
        state_repository_factory: Callable[[], object],
        product_code: str,
        app_version: str,
        platform: str,
        *,
        first_delay: float = FIRST_RUN_DELAY_SECONDS,
        interval: float = INTERVAL_SECONDS,
        max_interval: float = MAX_INTERVAL_SECONDS,
        batch_rows: int = BATCH_ROWS,
    ):
        self._store = store
        self._client_factory = client_factory
        self._signer_factory = signer_factory
        self._state_repository_factory = state_repository_factory
        self._product_code = product_code
        self._app_version = app_version
        self._platform = platform
        self._first_delay = first_delay
        self._base_interval = interval
        self._max_interval = max_interval
        self._batch_rows = batch_rows
        self.interval = interval              # current wait; doubles on failure, resets on success
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            log.info("crash reports: sender start ignored (already started)")
            return
        self._thread = threading.Thread(target=self._loop, name="crash-report-sender", daemon=True)
        self._thread.start()
        log.info("crash reports: sender started (first run in %ss, then every %ss)", self._first_delay, self._base_interval)

    def stop(self) -> None:
        self._stop.set()
        log.info("crash reports: sender stopped")

    def _loop(self) -> None:
        delay = self._first_delay
        while not self._stop.wait(delay):
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 -- a dead loop would stop all reporting for good
                log.exception("crash reports: sender run crashed")
                self._back_off()
            delay = self.interval

    # -- one run ---------------------------------------------------------
    def _installation_id(self) -> Optional[str]:
        try:
            record = self._state_repository_factory().load()
        except Exception:  # noqa: BLE001
            log.warning("crash reports: could not read licence state", exc_info=True)
            return None
        return getattr(record, "owner_installation_id", None) or None

    def _back_off(self) -> None:
        self.interval = min(self.interval * 2, self._max_interval)

    def _payload_for(self, rows: list) -> list:
        """Rows -> the report dicts that go on the wire, within MAX_BATCH_BYTES
        (always at least one). Only the eleven allowed fields; the message and
        frames are scrubbed again here."""
        reports = []
        used = 0
        for row in rows:
            report = {
                "fingerprint": str(row["fingerprint"]),
                "kind": str(row["kind"]),
                "exception_type": str(row["exception_type"])[:scrub.MAX_FIELD_CHARS],
                "message": scrub.scrub_message(row["message"]),
                "frames": scrub.safe_frames(row["frames"]),
                "route": (str(row["route"])[:scrub.MAX_FIELD_CHARS] if row.get("route") else None),
                "app_version": str(row["app_version"]),
                "platform": str(row["platform"]),
                "first_seen_at": str(row["first_seen_at"]),
                "last_seen_at": str(row["last_seen_at"]),
                "count": int(row["count"]),
            }
            size = len(json.dumps(report, ensure_ascii=False).encode("utf-8"))
            if reports and used + size > MAX_BATCH_BYTES:
                break
            reports.append(report)
            used += size
        return reports

    def run_once(self) -> str:
        """One send attempt. Returns a short outcome code (also logged)."""
        installation_id = self._installation_id()
        if not installation_id:
            log.info("crash reports: run skipped -- this install is not activated")
            return "NOT_ACTIVATED"
        rows = self._store.pending(self._batch_rows)
        if not rows:
            log.info("crash reports: run skipped -- nothing pending")
            return "NOTHING_PENDING"
        # SUPPORT-1: diagnostics snapshots travel in a request of their own,
        # AFTER the crash reports. An Owner built before the "diagnostics" kind
        # existed refuses a whole request that contains one (all-or-nothing
        # validation); mixed into one request that refusal would also hold back
        # every real crash report behind it, forever, oldest-first.
        crashes = [r for r in rows if r["kind"] != "diagnostics"]
        snapshots = [r for r in rows if r["kind"] == "diagnostics"]
        outcome = "NOTHING_PENDING"
        for group in (crashes, snapshots):
            if not group:
                continue
            outcome = self._send_group(installation_id, group)
            if outcome != "SENT":
                return outcome
        return outcome

    def _send_group(self, installation_id: str, rows: list) -> str:
        reports = self._payload_for(rows)
        sent_rows = rows[:len(reports)]
        try:
            client = self._client_factory()
            response = client.send_crash_reports(
                installation_id=installation_id,
                reports=reports,
                signer=self._signer_factory(),
                app_version=self._app_version,
                platform=self._platform,
            )
        except Exception as exc:  # noqa: BLE001 -- transport failure, any kind
            self._back_off()
            log.warning("crash reports: send failed (%s); %s report(s) stay pending, next wait %ss",
                        getattr(exc, "reason_code", type(exc).__name__), len(reports), self.interval)
            return "SEND_FAILED"
        if not isinstance(response, dict) or response.get("result") != "ACCEPTED":
            self._back_off()
            reason = response.get("reason_code") if isinstance(response, dict) else None
            log.warning("crash reports: Owner did not accept the batch (reason=%s); %s report(s) stay pending, next wait %ss",
                        reason, len(reports), self.interval)
            return "REFUSED"
        marked = self._store.mark_sent([(r["id"], r["count"]) for r in sent_rows])
        self.interval = self._base_interval
        log.info("crash reports: sent %s report(s), %s marked sent", len(reports), marked)
        return "SENT"


def build_crash_report_sender(
    *,
    store: Optional[CrashStore],
    app_data_dir: str,
    owner_base_url: str,
    verify_tls: bool,
    timeout_seconds: float,
    device_identity_factory: Callable[[Path], object],
    product_code: str,
    app_version: str,
    platform: str,
    **sender_overrides,
) -> Optional[CrashReportSender]:
    """Returns None when there is no Owner URL (or no store): nothing is built,
    so no client can ever be made and nothing can ever be sent.

    Uses the SAME licensing directory, licence database and device identity the
    licensing blueprint and the update channel use -- never a second key -- so
    Owner verifies the reports as coming from the installation it activated.
    """
    if not owner_base_url or store is None:
        return None
    licensing_dir = Path(app_data_dir) / "licensing"
    db_path = Path(app_data_dir) / "database" / "subsystems" / "licensing.db"

    def client_factory():
        # One retry, like the update poll: this runs unattended every half hour
        # and the next run is the retry.
        return LicensingClient(
            LicensingClientConfig(
                base_url=owner_base_url, timeout_seconds=timeout_seconds, verify_tls=verify_tls, max_retries=1
            )
        )

    return CrashReportSender(
        store,
        client_factory,
        lambda: device_identity_factory(licensing_dir),
        lambda: LicenseStateRepository(db_path),
        product_code,
        app_version,
        platform,
        **sender_overrides,
    )
