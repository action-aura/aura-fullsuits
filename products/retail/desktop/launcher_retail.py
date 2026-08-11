"""
Aura Retail -- Windows desktop launcher (PyInstaller entry point).

New file (not extracted) -- Action Aura Enterprise's launcher.py is a thin
platform layer over `aura_core`, the shared bootstrap for the ENTIRE
multi-subsystem monolith (path resolution across every subsystem, bundle
seeding for every subsystem's databases, etc.). A standalone Retail product
does not need that machinery -- products/retail/backend/app.py already does
its own path resolution via AURA_APP_DATA/AURA_STANDALONE.

This launcher preserves the desktop UX pieces that DO matter for a shipped
product and are platform-specific, following the same pattern as the source
launcher.py: a native application window (pywebview), a single-instance
guard, and graceful fallbacks (Edge/Chrome --app, then default browser) if
pywebview isn't available. See docs/migration/retail-extraction-report.md.

Phase 3.7 correction: the startup readiness check used to poll the bare
"/" path, which no route has ever served -- every check failed for the
full timeout regardless of server health, and the failure path's blocking
MessageBoxW call could prevent shutdown entirely in a non-interactive
launch context. See docs/corrections/launcher/root-cause-analysis.md and
docs/corrections/launcher/launcher-readiness-design.md. The lifecycle is
now an explicit state machine (LauncherState), the readiness check targets
the unauthenticated /api/health endpoint via
commercial_runtime.launcher_support.check_readiness(), and the fatal-error
dialog is time-bounded so it can never block process exit indefinitely.
"""
import os
import sys
import time
import socket
import threading
import webbrowser
import logging
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / 'backend'
SUITE_ROOT = BACKEND_DIR.parent.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from commercial_runtime.launcher_support import (  # noqa: E402
    LauncherState, check_readiness, health_url, show_fatal_dialog,
)

APP_NAME = 'Aura Retail'
HOST = '127.0.0.1'  # what THIS machine's own UI/readiness-checks talk to --
# always loopback, regardless of bind mode below (binding 0.0.0.0 still
# accepts loopback connections, so this never needs to change).
# AURA_LAN_DEMO_MODE opts the socket into binding all interfaces instead of
# loopback-only, so another device on the same LAN (e.g. a phone browser)
# can reach this same backend -- one shared database, no sync engine
# involved. Off by default: a normal customer build is unaffected, this is
# opt-in for a live demo where the operator controls the network.
BIND_HOST = '0.0.0.0' if os.environ.get('AURA_LAN_DEMO_MODE') == '1' else HOST
DEFAULT_PORT = 5000
READY_TIMEOUT_SECONDS = 45.0

# A frozen .exe is always a standalone customer build (no sample-data seeding).
os.environ.setdefault('AURA_STANDALONE', '1' if getattr(sys, 'frozen', False) else '0')

_app_data = os.environ.get('AURA_APP_DATA') or str(
    Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'AuraRetail'
)
os.environ['AURA_APP_DATA'] = _app_data
Path(_app_data, 'logs').mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    handlers=[
        logging.FileHandler(Path(_app_data, 'logs', 'startup.log'), encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger('aura-retail-launcher')
log.info(f'{APP_NAME} launcher starting.')
log.info(f'App data: {_app_data}')

_state = LauncherState.NOT_STARTED


def _set_state(new_state: LauncherState):
    global _state
    log.info(f'[state] {_state.value} -> {new_state.value}')
    _state = new_state


def _bind_free_socket(start=DEFAULT_PORT, stop=DEFAULT_PORT + 20):
    """Bind and start listening on the first free loopback port, returning
    the live socket (not just the port number).

    On Windows, bind() alone does not guarantee exclusive ownership of a
    port the way POSIX's default TCP listen semantics do -- SO_EXCLUSIVEADDRUSE
    must be set explicitly, or two processes racing to start within
    milliseconds of each other (e.g. Retail and Clinic launched back to
    back) can both end up "listening" on the same port, with Windows
    delivering connections to either one unpredictably. A prior version of
    this function tested a port with a throwaway socket and released it
    before the real server bound -- that release-then-rebind gap was itself
    a second, independent race window. Binding once here and handing this
    exact socket to waitress (see _run_server) closes both: nothing else
    can ever bind this port from the moment this function returns.
    """
    for port in range(start, stop):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind((BIND_HOST, port))
            s.listen(128)
            return s
        except OSError:
            s.close()
            continue
    return None


def _run_server(sock):
    import app as retail_app
    retail_app.init_app()
    from waitress import serve as _serve
    log.info(f'Starting server on http://{BIND_HOST}:{sock.getsockname()[1]}')
    _serve(retail_app.app, sockets=[sock], threads=12,
           channel_timeout=120, connection_limit=200, _quiet=True)


def _acquire_single_instance():
    """Best-effort single-instance guard via a named Windows mutex. Returns the
    handle if we are first, None if another instance already holds it."""
    try:
        import ctypes
        from ctypes import wintypes
        ERROR_ALREADY_EXISTS = 183
        name = 'Global\\AuraRetail_' + Path(_app_data).name
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, wintypes.BOOL(True), name)
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return None
        return handle
    except Exception:
        return 'unsupported'


def _open_app_window(url):
    import subprocess
    import shutil
    candidates = [
        os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe'),
        os.path.expandvars(r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe'),
        shutil.which('msedge'),
        os.path.expandvars(r'%ProgramFiles%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe'),
        shutil.which('chrome'),
    ]
    exe = next((c for c in candidates if c and os.path.isfile(c)), None)
    if not exe:
        return None
    profile = str(Path(_app_data) / 'app_window')
    try:
        return subprocess.Popen([
            exe, f'--app={url}', f'--user-data-dir={profile}',
            '--window-size=1400,900', '--no-first-run', '--no-default-browser-check',
        ])
    except Exception as exc:
        log.warning(f'Could not open app window via {exe}: {exc}')
        return None


def _message_box(title, message):
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)


def _fatal(message: str, reason_code: str):
    """Enters FAILED state and exits with a bounded, non-blocking failure
    dialog -- a real interactive user still sees and can dismiss it
    normally, but a non-interactive launch context (no desktop session to
    show a modal on) cannot hang here forever (Phase 3.7 finding: the
    previous unbounded MessageBoxW call could prevent sys.exit from ever
    running, leaving an orphaned server process with no visible UI)."""
    _set_state(LauncherState.FAILED)
    log.error(f'[{reason_code}] {message}')
    if getattr(sys, 'frozen', False):
        full_message = f'{message}\n\nReference: {reason_code}\nSee logs\\startup.log for details.'
        show_fatal_dialog(APP_NAME, full_message, _message_box)
    _set_state(LauncherState.STOPPED)
    sys.exit(1)


def _run_native_window(url: str) -> bool:
    try:
        import webview
        log.info('Opening native application window (pywebview / WebView2).')
        webview.create_window(APP_NAME, url, width=1400, height=900,
                               text_select=True, zoomable=True)
        webview.start()
        log.info('Native application window closed.')
        return True
    except Exception as exc:
        log.warning(f'Native window unavailable ({exc}); will try Edge --app.')
        return False


def main():
    instance = _acquire_single_instance()
    if instance is None:
        log.info('Another instance is already running -- exiting this launch.')
        if getattr(sys, 'frozen', False):
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0, f'{APP_NAME} is already running.\nLook for its window (check the taskbar).',
                    APP_NAME, 0x40)
            except Exception:
                pass
        sys.exit(0)

    _set_state(LauncherState.STARTING)

    sock = _bind_free_socket()
    if sock is None:
        _fatal(f'No free network port was available (tried {DEFAULT_PORT}-{DEFAULT_PORT + 20}). '
               'Another copy of Aura Retail may still be running -- close it and try again.',
               reason_code='PORT_UNAVAILABLE')
        return  # unreachable (sys.exit above); explicit for readability
    port = sock.getsockname()[1]

    server_thread = threading.Thread(target=_run_server, args=(sock,), daemon=True)
    server_thread.start()

    url = health_url(HOST, port)
    log.info(f'Waiting for readiness at {url} (up to {READY_TIMEOUT_SECONDS:.0f}s)...')
    result = check_readiness(
        url, timeout_seconds=READY_TIMEOUT_SECONDS,
        is_process_alive=server_thread.is_alive,
    )
    log.info(f'Readiness check result: {result}')

    if not result.ready:
        reason_map = {
            'process_exited': 'SERVER_PROCESS_EXITED',
            'health_endpoint_error': 'HEALTH_ENDPOINT_ERROR',
            'not_ready_timeout': 'STARTUP_TIMEOUT',
        }
        _fatal(f'The Aura Retail server did not become ready in time ({result.detail})',
               reason_code=reason_map.get(result.reason, 'STARTUP_TIMEOUT'))
        return

    # Defense in depth against the port-race scenario _bind_free_socket()
    # closes above: even a socket-level bug we haven't foreseen must not
    # result in this launcher silently opening a window onto a DIFFERENT
    # Aura product's backend (both share the same generic /api/health
    # {"status":"ok"} shape, so a plain health check alone cannot tell them
    # apart). /api/version is product-specific and checked explicitly here.
    try:
        import json
        import urllib.request
        with urllib.request.urlopen(f'http://{HOST}:{port}/api/version', timeout=5) as resp:
            product_code = json.loads(resp.read()).get('product_code')
    except Exception as exc:
        product_code = None
        log.warning(f'Could not verify product identity at http://{HOST}:{port}/api/version: {exc}')
    if product_code != 'AURA_RETAIL':
        _fatal(f'Port {port} answered as "{product_code}", not Aura Retail -- another Aura '
               'product is using this port. Close all Aura applications and try again.',
               reason_code='WRONG_PRODUCT_ON_PORT')
        return

    # READY reached -- this is the one and only place readiness is declared.
    # No timer, thread, or callback anywhere in this module can still fire a
    # startup-timeout failure after this point; check_readiness() has
    # already returned, and nothing below re-invokes it.
    _set_state(LauncherState.READY)
    log.info(f'Server ready after {result.attempts} attempt(s), {result.elapsed:.2f}s -- launching application window.')

    _set_state(LauncherState.UI_RUNNING)
    if _run_native_window(f'http://{HOST}:{port}'):
        log.info('Application closed.')
    else:
        proc = _open_app_window(f'http://{HOST}:{port}')
        if proc is not None:
            log.info('Opened dedicated app window (Edge/Chrome --app mode).')
        else:
            log.warning('No Chromium browser found -- falling back to default browser.')
            webbrowser.open(f'http://{HOST}:{port}')

        try:
            while server_thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    _set_state(LauncherState.STOPPING)
    log.info('Application closed.')
    _set_state(LauncherState.STOPPED)


if __name__ == '__main__':
    main()
