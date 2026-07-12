"""
Aura Clinic -- Windows desktop launcher (PyInstaller entry point).

Identical structure to products/retail/desktop/launcher_retail.py -- see
that file's docstring for the full rationale (native pywebview window,
single-instance guard, Edge/Chrome --app fallback, default-browser
fallback). Only the app name, mutex name, and imported server module differ.
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

os.environ.setdefault('AURA_STANDALONE', '1' if getattr(sys, 'frozen', False) else '0')

_app_data = os.environ.get('AURA_APP_DATA') or str(
    Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'AuraClinic'
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
log = logging.getLogger('aura-clinic-launcher')
log.info('Aura Clinic launcher starting.')
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
    import urllib.request
    import urllib.error
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    return False


def _run_server(port):
    import app as clinic_app
    clinic_app.init_app()
    from waitress import serve as _serve
    log.info(f'Starting server on http://{HOST}:{port}')
    _serve(clinic_app.app, host=HOST, port=port, threads=12,
           channel_timeout=120, connection_limit=200, _quiet=True)


def _acquire_single_instance():
    try:
        import ctypes
        from ctypes import wintypes
        ERROR_ALREADY_EXISTS = 183
        name = 'Global\\AuraClinic_' + Path(_app_data).name
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
                0, message + '\n\nSee logs\\startup.log for details.', 'Aura Clinic', 0x10)
        except Exception:
            pass
    sys.exit(1)


def _run_native_window(url: str) -> bool:
    try:
        import webview
        log.info('Opening native application window (pywebview / WebView2).')
        webview.create_window('Aura Clinic', url, width=1400, height=900,
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
                    0, 'Aura Clinic is already running.\nLook for its window (check the taskbar).',
                    'Aura Clinic', 0x40)
            except Exception:
                pass
        sys.exit(0)

    port = _find_free_port()
    if port is None:
        _fatal(f'No free network port was available (tried {DEFAULT_PORT}-{DEFAULT_PORT + 20}). '
               'Another copy of Aura Clinic may still be running -- close it and try again.')

    server_thread = threading.Thread(target=_run_server, args=(port,), daemon=True)
    server_thread.start()

    url = f'http://{HOST}:{port}'
    log.info(f'Waiting for server at {url} (up to 45 s)...')
    if not _wait_for_server(url):
        _fatal('The Aura Clinic server did not start in time.')

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
