"""Crash reports -- catching the crash.

Two global hooks, nothing else:

  * Flask's `got_request_exception` signal -- a request handler raised and
    nothing handled it (the user got a 500).
  * `threading.excepthook` -- a background thread (sync, e-invoicing worker,
    the update poll...) died. The previous hook is always chained, so the
    thread's traceback still reaches the log exactly as before.

Deliberately NOT installed: `sys.excepthook` / signal handlers / anything that
wraps the WSGI app. Each extra global hook is another way for the reporter to
change how the till behaves.

THE RULE FOR THIS FILE: a crash reporter that crashes the till is worse than
none. Every path here is wrapped so a failure INSIDE reporting is swallowed and
logged once (not on every later crash -- a broken reporter would otherwise
double the log noise of the very failure it is describing). The signal handler
returns nothing and re-raises nothing, so it cannot change the HTTP response
the app was already going to send; the app's existing error behaviour is the
contract, and the retail suite is what proves it did not move.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import uuid
from typing import Optional

from flask import g, got_request_exception, request

from . import scrub
from .reference_code import reference_code
from .store import CrashStore

log = logging.getLogger("aura.crash_reports")

KIND_SERVER = "server"
KIND_THREAD = "thread"
KIND_CLIENT = "client"
# SUPPORT-1: not a crash. A support snapshot the shopkeeper chose to send
# ("Send diagnostics to support"). It rides the crash-report outbox because that
# is already the device-signed, queued, bounded channel to Owner -- no second
# channel, no schema change (the kind column is a free 16-char string).
KIND_DIAGNOSTICS = "diagnostics"

# The response header that carries a report's 6-character reference code to the
# page (SUPPORT-1 item 3). A header, not a body: the existing answers (204 for
# a recorded client report, the app's own 500 page) keep their shape.
REPORT_CODE_HEADER = "X-Aura-Report-Code"

_SCREEN_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_MAX_CLIENT_MESSAGE_INPUT = 2000      # bound the text the regexes ever see
_MAX_CLIENT_STACK_INPUT = 8000


def build_report(*, kind: str, exception_type: str, message: str, frames: list, route: Optional[str],
                 app_version: str, platform: str) -> dict:
    """The one place a report dict is assembled, so every kind has the same
    shape. `message` must already be scrubbed by the caller."""
    return {
        "fingerprint": scrub.fingerprint(kind, exception_type, frames),
        "kind": kind,
        "exception_type": exception_type,
        "message": message,
        "frames": frames,
        "route": route,
        "app_version": app_version,
        "platform": platform,
    }


def report_from_exception(exc: BaseException, *, kind: str, route: Optional[str], app_version: str,
                          platform: str) -> dict:
    return build_report(
        kind=kind,
        exception_type=scrub.exception_type_name(exc),
        message=scrub.scrub_message(exc),
        frames=scrub.relativize_frames(exc.__traceback__),
        route=route,
        app_version=app_version,
        platform=platform,
    )


def report_from_client(*, message: object, stack: object, screen: object, app_version: str,
                       platform: str) -> dict:
    """A browser-side error. `screen` is the section id the page's own JS
    knows ("dashboard", "sales"...); anything that is not a plain short
    identifier is dropped, so a full location.href with its query string can
    never become the route even if a client sends one."""
    message_text = message[:_MAX_CLIENT_MESSAGE_INPUT] if isinstance(message, str) else ""
    stack_text = stack[:_MAX_CLIENT_STACK_INPUT] if isinstance(stack, str) else ""
    screen_id = screen if isinstance(screen, str) and _SCREEN_RE.match(screen) else None
    return build_report(
        kind=KIND_CLIENT,
        exception_type=scrub.js_exception_type(stack_text),
        message=scrub.scrub_message(message_text),
        frames=scrub.frames_from_js_stack(stack_text),
        route=screen_id,
        app_version=app_version,
        platform=platform,
    )


def _fmt_count(value: object) -> str:
    """A row count for the snapshot message. Four or more digits would be
    blanked to "<n>" by the scrubber (it cannot tell a count from an amount),
    which would make a busy shop's snapshot useless, so big numbers are
    shortened to e.g. "12k" -- a magnitude, not an exact figure."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return "?"
    if value < 1000:
        return str(value)
    return "%dk" % (value // 1000)


_STATE_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")


def _state_text(value: object) -> str:
    return value if isinstance(value, str) and _STATE_RE.match(value) else "?"


def build_diagnostics_report(facts: object, *, app_version: str, platform: str,
                             nonce: Optional[str] = None) -> dict:
    """A support snapshot as a report dict (same shape as every other kind).

    `facts` is the page's copy of the existing diagnostics export
    (GET /api/sub/retail/diagnostics/export). Only a fixed list of its fields is
    read, each re-typed here -- an int stays an int, a state stays a short token --
    so nothing free-text from the page (and in particular NOT the export's
    log_tail) can reach Owner. The whole export does not fit the channel's
    300-character message anyway; this is the part support asks for first.

    The fingerprint is unique per press of the button (a random nonce), not
    per bug: two snapshots from one shop are two things support may be asked
    about, each with its own code, and the store's (fingerprint, version)
    upsert must not fold them into one row.
    """
    facts = facts if isinstance(facts, dict) else {}
    counts = facts.get("row_counts") if isinstance(facts.get("row_counts"), dict) else {}
    licence = facts.get("licence") if isinstance(facts.get("licence"), dict) else {}
    sync = facts.get("sync_health") if isinstance(facts.get("sync_health"), dict) else {}
    schema = facts.get("retail_schema_version")
    # sync_health (SyncService.get_health): {"configured": False} when sync is
    # off, else booleans "healthy" and a pending_count. Only those three facts.
    if sync.get("configured") is False:
        sync_text = "off"
    elif isinstance(sync.get("healthy"), bool):
        sync_text = ("ok" if sync["healthy"] else "unhealthy") + " pending " + _fmt_count(sync.get("pending_count"))
    else:
        sync_text = "?"
    text = "snapshot v%s schema %s licence %s sync %s sales %s products %s customers %s users %s" % (
        app_version, schema if isinstance(schema, int) and not isinstance(schema, bool) else "?",
        _state_text(licence.get("state")), sync_text,
        _fmt_count(counts.get("sales")), _fmt_count(counts.get("products")),
        _fmt_count(counts.get("customers")), _fmt_count(counts.get("users")))
    fingerprint = hashlib.sha256(("diagnostics|" + (nonce or uuid.uuid4().hex)).encode("utf-8")).hexdigest()[:scrub.FINGERPRINT_LENGTH]
    report = build_report(
        kind=KIND_DIAGNOSTICS, exception_type="DiagnosticsSnapshot", message=scrub.scrub_message(text),
        frames=[], route=None, app_version=app_version, platform=platform)
    report["fingerprint"] = fingerprint
    return report


def _remember_code_for_response(report: dict) -> None:
    """Stash the code on the request so the after_request hook can put it on
    the response. Outside a request (a background thread) there is nothing to
    answer, so this quietly does nothing."""
    try:
        code = reference_code(report.get("fingerprint"))
        if code:
            g.aura_report_code = code
    except Exception:  # noqa: BLE001 -- no app/request context
        pass


class CaptureHandle:
    """Returned by install(). `remove()` undoes every hook this install made
    (used by tests, and harmless in production)."""

    def __init__(self, app, store: CrashStore, app_version: str, platform: str):
        self._app = app
        self._store = store
        self._app_version = app_version
        self._platform = platform
        self._failure_logged = False
        self._active = True
        self._previous_thread_hook = None
        self._thread_hook = None
        self._signal_receiver = None
        self._after_request = None

    # -- the one guard every capture path goes through -------------------
    def _swallow(self, what: str, exc: BaseException) -> None:
        # Logged once per install: the first failure tells us the reporter is
        # broken; repeating it for every later crash would just bury the log.
        if not self._failure_logged:
            self._failure_logged = True
            log.warning("crash reports: capture failed while handling %s; further failures are not logged",
                        what, exc_info=(type(exc), exc, exc.__traceback__))

    def record(self, report: dict) -> None:
        self._store.record(report)

    def capture_exception(self, exc: BaseException, *, kind: str, route: Optional[str]) -> None:
        if not self._active:
            return
        try:
            report = report_from_exception(
                exc, kind=kind, route=route, app_version=self._app_version, platform=self._platform)
            self.record(report)
            # Only after the report is really in the store: a code for a
            # report that was not saved would send support looking for
            # something that does not exist.
            _remember_code_for_response(report)
        except Exception as inner:  # noqa: BLE001 -- see module docstring
            self._swallow(kind, inner)

    # -- hooks -----------------------------------------------------------
    def _on_request_exception(self, sender, exception=None, **_extra) -> None:
        try:
            route = request.url_rule.rule if request.url_rule is not None else None
        except Exception:  # noqa: BLE001 -- no request context, nothing to read
            route = None
        if exception is not None:
            self.capture_exception(exception, kind=KIND_SERVER, route=route)

    def _install_thread_hook(self) -> None:
        previous = threading.excepthook

        def hook(args):
            try:
                # SystemExit in a thread is a normal way to stop; the default
                # hook ignores it and so do we.
                if self._active and args.exc_type is not SystemExit and args.exc_value is not None:
                    self.capture_exception(args.exc_value, kind=KIND_THREAD, route=None)
            except Exception as inner:  # noqa: BLE001
                self._swallow("thread", inner)
            finally:
                previous(args)         # the traceback still reaches stderr/log as before

        self._previous_thread_hook = previous
        self._thread_hook = hook
        threading.excepthook = hook

    def remove(self) -> None:
        self._active = False
        if self._signal_receiver is not None:
            got_request_exception.disconnect(self._signal_receiver, self._app)
            self._signal_receiver = None
        # Only unwind the hook if it is still ours: someone may have chained
        # on top of it since, and ripping the chain apart would drop theirs.
        # If it is not ours, `_active = False` already turns ours into a
        # pass-through to the previous hook.
        if self._thread_hook is not None and threading.excepthook is self._thread_hook:
            threading.excepthook = self._previous_thread_hook
        self._thread_hook = None


def install(app, store: CrashStore, *, app_version: str, platform: str) -> CaptureHandle:
    """Wire capture into `app`. Cheap, synchronous, one SQLite upsert per
    crash. Safe to call with a store that later fails: see CaptureHandle."""
    handle = CaptureHandle(app, store, app_version, platform)
    handle._signal_receiver = handle._on_request_exception
    got_request_exception.connect(handle._signal_receiver, app, weak=False)

    def add_code_header(response):
        # A 500 the user is about to see carries the code of the report just
        # saved for it, so the page can say "a report was saved (code ...)".
        # Header only: the body and status are exactly what the app already sent.
        try:
            code = getattr(g, "aura_report_code", None)
            if handle._active and code:
                response.headers[REPORT_CODE_HEADER] = code
        except Exception:  # noqa: BLE001
            pass
        return response

    handle._after_request = add_code_header
    app.after_request(add_code_header)
    handle._install_thread_hook()
    log.info("crash reports: capture installed (server + thread); store=%s", store.path)
    return handle
