"""The daemon-thread timer that actually calls `forwarder.run_once` --
`forwarder.py` stays a pure, no-timer, no-HTTP module (see that file's own
module docstring); this is the wiring layer, mirroring the tick-versus-
thread split `commercial_runtime/sync/site_relay/coordinator.py` already
uses for the LAN hub election, and `sync_service.py`'s own `_schedule_next`/
`threading.Timer` pattern for the ordinary cloud sync loop.

THE DEFECT THIS MODULE CLOSES (this task's own plan, finding 0.1):
`forwarder.py` is complete and unit-tested (`test_site_relay_forwarder.py`)
but is imported by NOTHING in production -- no timer ever called it, so a
hub's site log never actually reached Owner's cloud relay and the cloud's
own stream never reached the hub's paired LAN devices. Measured on real
hardware 2026-09-24: `site_forward_cursor` stayed at (forwarded=0,
cloud_seq=0) after 17 site-log events and a real phone sync.

WHY THIS RUNS ONLY AS `_no_apply` (decision A / B4 in this task's own
plan): a cloud event this loop pulls down lands in the site log as
`store.UPSTREAM_ORIGIN` (see `forwarder.pull_down`'s own docstring). This
hub's OWN two `SyncService` instances (retail + registry, both routed
through `local_client.RelaySelector` once `_enter_hub_self_sync` has
flipped it to LOCAL) then pull that same site log through the ordinary
apply path -- quarantine, schema-skew guard, `local_ensure_schema`,
freshness, conflicts -- exactly once each. If THIS loop also applied the
same events itself, every one of them would be applied TWICE: once here,
once by the hub's own SyncService a moment later, which writes one
`sync_conflicts` row per event at equal `row_version` (see `store.py`'s own
"replaying catalogue events" note and this task's plan item 0.6) for no
reason. It also could not correctly cover BOTH of the hub's databases:
`forwarder.pull_down`'s single connection is retail.db only, and `users`/
`user_permissions` live in registry.db -- a second, different SyncService
instance this loop has no way to reach.

THIS INVARIANT ONLY HOLDS WHEN THE HUB'S OWN SYNCSERVICE PAIR IS ACTUALLY
CONSUMING THE SAME SITE LOG THIS LOOP WRITES INTO. See `app.py::
_enter_hub_self_sync`'s own docstring: this loop is started from THERE,
AFTER the selector has already been flipped to LOCAL, and is NEVER stopped
on stand-down (decision S1) for exactly this reason -- see that function's
own comment for the full ordering argument.
"""
from __future__ import annotations

import logging
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Callable, Optional

from commercial_runtime.sync.site_relay import forwarder

_log = logging.getLogger(__name__)

# Matches SyncService's own default tick (`SyncService.start`'s
# `interval_seconds=10.0`) and Android's SyncCoordinator -- the forwarder is
# one more leg of the same store-and-forward relay, and there is no reason
# for it to run on a different cadence than the two streams it bridges.
FORWARD_INTERVAL_SECONDS = 10.0

# Bounded per tick so a huge backlog (a hub's first LOCAL switch, or a
# LAN-only shop's internet coming back after days offline) cannot hold this
# thread -- and, more importantly, the SQLite write lock each page briefly
# takes -- for an unbounded stretch of wall-clock time. 10 pages at up to
# 200 up / 500 down each is still a meaningful 2,000/5,000 events per tick;
# whatever remains is simply picked up on the next tick, 10 seconds later.
MAX_PAGES_PER_TICK = 10

# How often a BUSY (moving) steady state still leaves a trail even when
# nothing has changed status -- see `tick()`'s own de-duplicated logging
# below. An hour matches `listener.py`'s own `HOUSEKEEPING_INTERVAL_SECONDS`
# for the identical reason: frequent enough that a genuinely stuck forwarder
# is still discoverable within a shift, infrequent enough that a healthy
# hub's log is not spammed once every 10 seconds forever.
SUMMARY_INTERVAL_SECONDS = 3600.0


def _cloud_host_port(cloud_client) -> Optional[str]:
    """`host:port` of the cloud this tick is talking to, for the log line
    ONLY -- never the full URL (its path can carry a tenant slug) and never
    userinfo (it can carry credentials). Plan B9, and the whole of G10's
    mitigation: a till whose `sync_relay_base_url` autojoin overwrote with
    ANOTHER hub's LAN address fails every tick forever, and without the
    host in the WARNING a stale LAN address and a dead Owner read exactly
    the same ("pull_error=NETWORK_UNAVAILABLE"). Added by the adversarial
    verifier, 2026-09-24 -- the first cut logged no host at all. Duck-typed
    on `base_url` (the real `SyncRelayClient` has it); anything without one
    logs as None rather than failing the tick over a log line."""
    base_url = getattr(cloud_client, "base_url", None)
    if not isinstance(base_url, str) or not base_url:
        return None
    try:
        netloc = urllib.parse.urlsplit(base_url).netloc
    except ValueError:
        return None
    return netloc.rsplit("@", 1)[-1] or None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_failure(result: dict) -> bool:
    """A tick FAILED when it could not build a cloud client at all
    (`error`), or when either half of the bridge raised. `NO_CLOUD` (a
    LAN-only shop, the ordinary permanent state) is NOT a failure."""
    return bool(result.get("error") or result.get("push_error") or result.get("pull_error"))


def _degraded(half: dict, failures: int, reason: Optional[str], failed_at: Optional[str]) -> dict:
    out = dict(half or {})
    out["healthy"] = False
    out["consecutive_failures"] = max(int(out.get("consecutive_failures") or 0), failures)
    out["last_failure_reason"] = f"UPSTREAM:{reason}" if reason else "UPSTREAM"
    out["last_failure_at"] = failed_at or out.get("last_failure_at")
    return out


def overlay_upstream_health(health: dict, upstream: dict) -> dict:
    """REHEARSAL-1007 B1 (d): fold the forwarder's state into a hub's own
    `SyncService.get_health()` -- returns a NEW dict, never mutates either
    argument.

    WHY: on a hub, the retail `SyncService` pushes and pulls its OWN site
    log (`local_client.LocalSiteRelayClient`), which cannot fail for network
    reasons, so its own health is "healthy, 0 pending" by construction --
    the rehearsal's till B showed exactly that for half an hour while its
    forwarder, the only thing that actually talks to Owner, failed
    `TLS_VERIFICATION_FAILED` on every tick and staff created on the other
    till never arrived. The forwarder is this till's real link to the shop's
    other devices, so its failures are this till's sync failures.

    What changes, and only when it should:
      * `upstream` -- the forwarder snapshot, always attached, so a support
        screen can see which leg is broken and against which host.
      * `pending_count` -- plus the site-log rows not yet forwarded, unless
        this shop has no cloud at all (`NO_CLOUD`: those rows are never
        meant to leave, and counting them would nag forever).
      * on a failing forwarder (`consecutive_failures > 0`): top-level
        `healthy` false, and the failing half (`push` for an upload error,
        `pull` for a download error, both when no client could be built)
        carries the forwarder's failure count and an `UPSTREAM:<code>`
        reason -- the fields the till's sync banner already reads, so it
        degrades with no front-end change.

    `seconds_since_last_success`/`never_synced` are deliberately left alone:
    they drive the 72-hour offline sales stop, and whether a LAN hub that
    has lost the internet should stop selling is a policy decision this
    fix does not get to make in passing."""
    if not isinstance(health, dict) or not health.get("configured"):
        return health
    out = dict(health)
    out["upstream"] = dict(upstream)
    state = upstream.get("state")
    unforwarded = upstream.get("unforwarded_count")
    if state not in (None, "NO_CLOUD") and isinstance(unforwarded, int) and unforwarded > 0:
        out["pending_count"] = int(out.get("pending_count") or 0) + unforwarded
    failures = int(upstream.get("consecutive_failures") or 0)
    if failures > 0:
        out["healthy"] = False
        reason = upstream.get("last_failure_reason")
        failed_at = upstream.get("last_failure_at")
        if upstream.get("error") or upstream.get("push_error"):
            out["push"] = _degraded(out.get("push"), failures, reason, failed_at)
        if upstream.get("error") or upstream.get("pull_error"):
            out["pull"] = _degraded(out.get("pull"), failures, reason, failed_at)
    return out


def _forward_up_only(conn, cloud_client) -> dict:
    """`forwarder.run_once`'s result shape for the upload half alone -- the
    drain mode (`ForwarderLoop(pull_down=False)`). Never raises, exactly
    like `run_once`."""
    page = {"forwarded": 0, "pulled": 0, "push_error": None, "pull_error": None}
    try:
        page["forwarded"] = forwarder.forward_up(conn, cloud_client)
    except Exception as exc:  # noqa: BLE001 -- mirrors run_once
        page["push_error"] = forwarder._error_label(exc)
    return page


def _no_apply(events: list) -> None:
    """The `apply_pulled` this loop always passes to `forwarder.pull_down`
    -- deliberately a no-op. See this module's own docstring, "WHY THIS RUNS
    ONLY AS `_no_apply`", for the full reasoning: applying here would
    double-apply every cloud-pulled event, once here and once through the
    hub's own two `SyncService` streams re-reading the same site log a
    moment later."""
    return None


class ForwarderLoop:
    """Owns exactly one daemon thread that calls `forwarder.run_once` on a
    fixed interval. `forwarder.py` remains the single writer of
    `site_forward_cursor` (see that module's own "OWNERSHIP" paragraph) --
    this class is the ONLY caller of `forwarder.run_once` in production,
    which keeps that invariant true: there is exactly one place `run_once`
    is ever invoked outside a test.
    """

    def __init__(self, *, get_conn, cloud_client_factory: Callable[[], Optional[object]],
                 interval_seconds: float = FORWARD_INTERVAL_SECONDS,
                 max_pages_per_tick: int = MAX_PAGES_PER_TICK,
                 monotonic=time.monotonic,
                 pull_down: bool = True):
        self._get_conn = get_conn
        self._cloud_client_factory = cloud_client_factory
        self._interval_seconds = interval_seconds
        self._max_pages_per_tick = max_pages_per_tick
        self._monotonic = monotonic
        # REHEARSAL-1007 B1 (c): `pull_down=False` is the DRAIN mode a
        # former hub runs under the operator kill switch
        # (`AURA_SITE_RELAY_ENABLED=0`): its own books are back on the cloud,
        # so nothing would ever read what a pull-down writes into the site
        # log -- but rows it accepted while it WAS hub may not have reached
        # Owner yet, and those must still go up. Upload only.
        self._pull_down = pull_down
        # Failure bookkeeping for `snapshot()` / `overlay_upstream_health`.
        self._consecutive_failures = 0
        self._last_failure_at: Optional[str] = None
        self._last_failure_reason: Optional[str] = None
        self._last_success_at: Optional[str] = None

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()
        self._last_result: dict = {
            "state": None, "forwarded": 0, "pulled": 0, "pages": 0,
            "push_error": None, "pull_error": None, "error": None,
            "cloud_host": None,
        }
        # De-duplicated logging state -- see `tick()`'s own "decision key"
        # comment below.
        self._last_log_key = None
        self._last_summary_at: Optional[float] = None
        self._cumulative_forwarded = 0
        self._cumulative_pulled = 0

    @property
    def last_result(self) -> dict:
        return dict(self._last_result)

    @property
    def pull_down_enabled(self) -> bool:
        return self._pull_down

    def _record_outcome(self, result: dict) -> None:
        if _is_failure(result):
            self._consecutive_failures += 1
            self._last_failure_at = _now_iso()
            self._last_failure_reason = result.get("error") or result.get("push_error") or result.get("pull_error")
        else:
            self._consecutive_failures = 0
            if result.get("state") == "RAN":
                self._last_success_at = _now_iso()

    def snapshot(self) -> dict:
        """The forwarder's state for `overlay_upstream_health` -- the last
        tick's result plus failure bookkeeping plus how many site-log rows
        have not reached Owner yet. NEVER RAISES: the count is `None` when
        the database cannot be read right now (a health read must never be
        the thing that fails)."""
        snap = dict(self._last_result)
        snap.update({
            "consecutive_failures": self._consecutive_failures,
            "last_failure_at": self._last_failure_at,
            "last_failure_reason": self._last_failure_reason,
            "last_success_at": self._last_success_at,
            "pull_down": self._pull_down,
            "unforwarded_count": None,
        })
        conn = None
        try:
            conn = self._get_conn()
            snap["unforwarded_count"] = forwarder.count_unforwarded(conn)
        except Exception:  # noqa: BLE001 -- see docstring
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        return snap

    def tick(self) -> dict:
        """One forwarder attempt. NEVER RAISES -- the whole body sits in
        `try/except`, mirroring `SyncService.run_once`'s own "an
        unreachable relay is ordinary, not a reason to crash the host app"
        contract. Returns and stores `self.last_result`; `start()`'s own
        `_run` loop wraps this AGAIN (belt and braces -- see that method's
        own comment) so this thread can never die from an unexpected
        exception in this method's own bookkeeping."""
        result = {
            "state": "RAN", "forwarded": 0, "pulled": 0, "pages": 0,
            "push_error": None, "pull_error": None, "error": None,
            "cloud_host": None,
        }
        try:
            cloud_client = self._cloud_client_factory()
        except Exception as exc:  # noqa: BLE001 -- never raises past this method
            result["state"] = "FAILED"
            result["error"] = getattr(exc, "reason_code", None) or type(exc).__name__
            self._last_result = result
            self._record_outcome(result)
            self._log_result(result)
            return result

        if cloud_client is None:
            result["state"] = "NO_CLOUD"
            self._last_result = result
            self._record_outcome(result)
            self._log_result(result)
            return result

        result["cloud_host"] = _cloud_host_port(cloud_client)

        conn = None
        try:
            conn = self._get_conn()
            for _page in range(self._max_pages_per_tick):
                if self._pull_down:
                    page = forwarder.run_once(conn, cloud_client, apply_pulled=_no_apply)
                else:
                    page = _forward_up_only(conn, cloud_client)
                result["forwarded"] += page["forwarded"]
                result["pulled"] += page["pulled"]
                result["pages"] += 1
                # A page's own push/pull error is the LATEST one recorded --
                # a later page's success must not silently erase an earlier
                # page's real failure from this tick's own result, so only
                # ever overwrite with something (never blank out a set
                # error with `None` from a later, unrelated page).
                if page["push_error"] is not None:
                    result["push_error"] = page["push_error"]
                if page["pull_error"] is not None:
                    result["pull_error"] = page["pull_error"]
                moved = (page["forwarded"] + page["pulled"]) > 0
                errored = page["push_error"] is not None or page["pull_error"] is not None
                # Stop paging once a page moves nothing and reports nothing
                # new -- there is nothing left to drain this tick. Stop on
                # ANY error too (push or pull): `forwarder.run_once` already
                # isolates the two halves from each other per call, but
                # spinning through 10 pages against a relay that is
                # currently rejecting/unreachable buys nothing and only
                # holds the connection longer.
                if not moved or errored:
                    break
        except Exception as exc:  # noqa: BLE001 -- never raises past this method
            result["state"] = "FAILED"
            result["error"] = getattr(exc, "reason_code", None) or type(exc).__name__
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        self._cumulative_forwarded += result["forwarded"]
        self._cumulative_pulled += result["pulled"]
        self._last_result = result
        self._record_outcome(result)
        self._log_result(result)
        return result

    def _log_result(self, result: dict) -> None:
        """Says what THIS tick decided, deduped -- never one line per tick
        forever (a healthy hub ticking every 10 seconds would otherwise
        write over 8,000 lines/day for "nothing happened"), but never fully
        silent either (MEMORY.md: "silent early returns are undiagnosable
        -- say what each tick decided, deduped"."""
        moved = (result["forwarded"] + result["pulled"]) > 0
        key = (result["state"], result["push_error"], result["pull_error"], result["error"], moved)
        now = self._monotonic()

        if key != self._last_log_key:
            level = logging.WARNING if (result["push_error"] or result["pull_error"] or result["error"]) else logging.INFO
            was_erroring = self._last_log_key is not None and (
                self._last_log_key[1] or self._last_log_key[2] or self._last_log_key[3]
            )
            is_erroring = bool(result["push_error"] or result["pull_error"] or result["error"])
            if was_erroring and not is_erroring:
                _log.info(
                    "Site relay forwarder: recovered -- state=%s forwarded=%d pulled=%d",
                    result["state"], result["forwarded"], result["pulled"],
                )
            else:
                _log.log(
                    level,
                    "Site relay forwarder: state=%s cloud=%s forwarded=%d pulled=%d pages=%d "
                    "push_error=%s pull_error=%s error=%s",
                    result["state"], result.get("cloud_host"), result["forwarded"],
                    result["pulled"], result["pages"],
                    result["push_error"], result["pull_error"], result["error"],
                )
            self._last_log_key = key
            self._last_summary_at = now
            return

        if self._last_summary_at is None or (now - self._last_summary_at) >= SUMMARY_INTERVAL_SECONDS:
            _log.info(
                "Site relay forwarder summary: cumulative forwarded=%d pulled=%d (state=%s)",
                self._cumulative_forwarded, self._cumulative_pulled, result["state"],
            )
            self._last_summary_at = now

    def start(self) -> None:
        """Idempotent -- one daemon thread per instance, ever. Safe to call
        more than once (mirrors `_bind_relay_now`'s own idempotence
        guard)."""
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, name="aura-site-relay-forwarder", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        # The first tick runs one interval AFTER start, not at boot -- see
        # this task's own plan, B1: nothing here is urgent, and a hub that
        # has just started has nothing forwardable yet in the overwhelming
        # common case.
        while not self._stop_event.wait(self._interval_seconds):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 -- belt and braces; tick() itself never raises
                _log.exception("Site relay forwarder: tick() raised unexpectedly; the loop continues.")

    def stop(self, timeout: Optional[float] = None) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
