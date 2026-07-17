"""
Aura FullSuits -- shared Windows desktop launcher support (Phase 3.7).

Extracted from products/retail/desktop/launcher_retail.py and
products/clinic/desktop/launcher_clinic.py, which were near-identical
copies (the source files' own docstrings said so) that had independently
drifted into an identical, identically-broken readiness check: both polled
the bare application root path ("/"), which neither app.py has ever
served, so the check could never succeed -- see
docs/corrections/launcher/root-cause-analysis.md. Implementing and testing
the correctness-critical logic once here, instead of twice in two frozen
launcher scripts, is the actual fix for that class of bug recurring.

Pure, dependency-injectable, and importable without pywebview/ctypes/Flask
so it can be unit-tested without a real socket, a real Windows session, or
a real packaged build.
"""
import time
import urllib.request
import urllib.error
from enum import Enum


class LauncherState(Enum):
    NOT_STARTED = 'not_started'
    STARTING = 'starting'
    READY = 'ready'
    UI_RUNNING = 'ui_running'
    STOPPING = 'stopping'
    STOPPED = 'stopped'
    FAILED = 'failed'


class ReadinessResult:
    """
    `reason` is one of:
      'ready'                 -- success
      'process_exited'        -- the server process/thread died before
                                  becoming ready
      'not_ready_timeout'     -- deadline reached with no HTTP response of
                                  any kind (server never became reachable)
      'health_endpoint_error' -- deadline reached, but the endpoint DID
                                  respond at least once with a non-200
                                  status (server is up, health check itself
                                  is wrong/misconfigured -- this is exactly
                                  the Phase 3.7 root cause's signature)
    """
    def __init__(self, ready, reason, detail, attempts, elapsed):
        self.ready = ready
        self.reason = reason
        self.detail = detail
        self.attempts = attempts
        self.elapsed = elapsed

    def __repr__(self):
        return (f'ReadinessResult(ready={self.ready}, reason={self.reason!r}, '
                f'attempts={self.attempts}, elapsed={self.elapsed:.2f}s)')


def health_url(host, port):
    return f'http://{host}:{port}/api/health'


def no_proxy_opener():
    """
    A urllib opener that never consults environment or Windows-registry
    proxy settings. Local loopback readiness checks must not be routed
    through a system-configured HTTP proxy -- not confirmed as the cause of
    the Phase 3.7 defect (proxy env vars were unset in every reproduction),
    but a real, independently-justified hardening for a customer machine
    that does have a global proxy configured. Scoped to this one opener
    only -- does not touch os.environ or any other networking in the
    process.
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def check_readiness(url, timeout_seconds=45.0, poll_interval=0.4,
                     is_process_alive=None, sleep=time.sleep,
                     now=time.monotonic, opener_factory=no_proxy_opener):
    """
    Poll `url` (an unauthenticated local health endpoint expected to return
    HTTP 200) until it succeeds, the process dies, or `timeout_seconds`
    elapses. Uses a monotonic clock so a wall-clock adjustment mid-startup
    cannot corrupt the deadline (time.monotonic() is immune to NTP/DST
    changes, unlike time.time()).

    `is_process_alive`, if given, is checked every iteration; if it
    returns False before readiness is achieved, this fails fast with
    reason='process_exited' instead of waiting out the full timeout --
    distinguishing "the process crashed" from "the process just hasn't
    finished starting yet."

    `sleep`/`now`/`opener_factory`/`is_process_alive` are dependency
    -injected so this function is unit-testable without real sockets or
    real elapsed time (see products/retail/tests/launcher_support_test.py).
    """
    opener = opener_factory()
    deadline = now() + timeout_seconds
    start = now()
    attempts = 0
    last_detail = 'No readiness attempt completed.'
    saw_http_response = False

    while now() < deadline:
        if is_process_alive is not None and not is_process_alive():
            return ReadinessResult(
                False, 'process_exited',
                'Server process exited before becoming ready.',
                attempts, now() - start)

        attempts += 1
        try:
            with opener.open(url, timeout=2) as resp:
                saw_http_response = True
                status = getattr(resp, 'status', None) or resp.getcode()
                if status == 200:
                    return ReadinessResult(
                        True, 'ready',
                        f'Health endpoint returned 200 on attempt {attempts}.',
                        attempts, now() - start)
                last_detail = f'Health endpoint returned unexpected status {status}.'
        except urllib.error.HTTPError as e:
            saw_http_response = True
            last_detail = f'Health endpoint returned HTTP {e.code}.'
        except (urllib.error.URLError, OSError) as e:
            last_detail = f'{type(e).__name__}: server not reachable yet.'
        except Exception as e:  # noqa: BLE001 -- readiness must never crash the launcher
            last_detail = f'Unexpected client error: {type(e).__name__}.'

        sleep(poll_interval)

    reason = 'health_endpoint_error' if saw_http_response else 'not_ready_timeout'
    return ReadinessResult(False, reason, last_detail, attempts, now() - start)


def show_fatal_dialog(title, message, message_box_fn):
    """
    Runs `message_box_fn(title, message)` (expected to be a blocking modal
    call like ctypes' MessageBoxW) on a background thread and joins it with
    a bounded timeout, so a launch context with no interactive desktop
    session (a real, reproduced Phase 3.7 finding -- MessageBoxW never
    returned in a non-interactive launch, so the sys.exit(1) after it was
    never reached, leaving an orphaned server process) can never block
    process shutdown forever. A real interactive user still sees and can
    dismiss the dialog normally; a headless/non-interactive launch instead
    times out and proceeds to clean shutdown.
    """
    import threading
    t = threading.Thread(target=message_box_fn, args=(title, message), daemon=True)
    t.start()
    t.join(timeout=120)
