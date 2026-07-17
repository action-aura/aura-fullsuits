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
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

HOST = '127.0.0.1'
DEFAULT_PORT = 5000

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
log.info('Aura Retail launcher starting.')
log.info(f'App data: {_app_data}')


def _find_free_port(start=DEFAULT_PORT, stop=DEFAULT_PORT + 20):
    for port in range(start, stop):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    return None


def _wait_for_server(url, timeout=45.0):
    # TEMPORARY DIAGNOSTIC INSTRUMENTATION -- Phase 3.7 Step 1 reproduction.
    # Logs proxy env + per-attempt exception type/message/status, no secrets
    # or business data. Removed once root cause is confirmed (see
    # docs/corrections/launcher/pre-fix-reproduction.md).
    import urllib.request
    import urllib.error
    log.info(f'[DIAG] readiness URL={url} timeout={timeout}')
    log.info(f'[DIAG] proxy env: HTTP_PROXY={os.environ.get("HTTP_PROXY")!r} '
             f'HTTPS_PROXY={os.environ.get("HTTPS_PROXY")!r} NO_PROXY={os.environ.get("NO_PROXY")!r}')
    try:
        import socket as _s
        log.info(f'[DIAG] getaddrinfo(127.0.0.1): {_s.getaddrinfo("127.0.0.1", None)}')
    except Exception as _e:
        log.info(f'[DIAG] getaddrinfo failed: {_e!r}')
    deadline = time.time() + timeout
    attempt = 0
    start = time.time()
    while time.time() < deadline:
        attempt += 1
        t0 = time.time()
        try:
            resp = urllib.request.urlopen(url, timeout=2)
            log.info(f'[DIAG] attempt={attempt} elapsed={t0-start:.2f}s SUCCESS status={resp.status}')
            return True
        except urllib.error.HTTPError as e:
            log.info(f'[DIAG] attempt={attempt} elapsed={t0-start:.2f}s HTTPError code={e.code} reason={e.reason!r}')
            time.sleep(0.4)
        except (urllib.error.URLError, OSError) as e:
            log.info(f'[DIAG] attempt={attempt} elapsed={t0-start:.2f}s {type(e).__name__}: {e!r}')
            time.sleep(0.4)
    log.info(f'[DIAG] TIMED OUT after {attempt} attempts, {time.time()-start:.2f}s elapsed')
    return False


def _run_server(port):
    import app as retail_app
    retail_app.init_app()
    from waitress import serve as _serve
    log.info(f'Starting server on http://{HOST}:{port}')
    _serve(retail_app.app, host=HOST, port=port, threads=12,
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


def _fatal(message: str):
    log.error(message)
    if getattr(sys, 'frozen', False):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, message + '\n\nSee logs\\startup.log for details.', 'Aura Retail', 0x10)
        except Exception:
            pass
    sys.exit(1)


def _run_native_window(url: str) -> bool:
    try:
        import webview
        log.info('Opening native application window (pywebview / WebView2).')
        webview.create_window('Aura Retail', url, width=1400, height=900,
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
                    0, 'Aura Retail is already running.\nLook for its window (check the taskbar).',
                    'Aura Retail', 0x40)
            except Exception:
                pass
        sys.exit(0)

    port = _find_free_port()
    if port is None:
        _fatal(f'No free network port was available (tried {DEFAULT_PORT}-{DEFAULT_PORT + 20}). '
               'Another copy of Aura Retail may still be running -- close it and try again.')

    server_thread = threading.Thread(target=_run_server, args=(port,), daemon=True)
    server_thread.start()

    url = f'http://{HOST}:{port}'
    log.info(f'Waiting for server at {url} (up to 45 s)...')
    if not _wait_for_server(url):
        _fatal('The Aura Retail server did not start in time.')

    log.info('Server ready -- launching application window.')

    if _run_native_window(url):
        log.info('Application closed.')
        return

    proc = _open_app_window(url)
    if proc is not None:
        log.info('Opened dedicated app window (Edge/Chrome --app mode).')
    else:
        log.warning('No Chromium browser found -- falling back to default browser.')
        webbrowser.open(url)

    try:
        while server_thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    log.info('Application closed.')


if __name__ == '__main__':
    main()
