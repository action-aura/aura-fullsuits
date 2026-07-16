"""
Android Python entry point (run by Chaquopy, called from ServerBootstrap.kt).

Rewritten for the standalone product split -- the source monolith's main.py
wired Android straight into `aura_core`, the shared bootstrap for the WHOLE
multi-subsystem app. Aura Clinic no longer has that module; this is the
mobile counterpart of products/clinic/desktop/launcher_clinic.py instead
(same BACKEND_DIR-on-sys.path + app.init_app() + waitress-in-a-thread
pattern), adapted for Chaquopy's call surface: start_server(files_dir) ->
port, wait_until_ready(port, timeout), server_port(). Owns no business logic.
"""

import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import android_platform as plat

# This file is staged at the root of the Chaquopy python source tree, as a
# sibling of products/ and commercial_runtime/ (see app/build.gradle
# stageSharedPython) -- so it can locate the backend the same way
# launcher_clinic.py does relative to its own file.
_SUITE_ROOT = Path(__file__).resolve().parent
_BACKEND_DIR = _SUITE_ROOT / 'products' / 'clinic' / 'backend'
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_lock = threading.Lock()
_state = {'port': None}

HOST = '127.0.0.1'


def _find_free_port(start: int, stop: int):
    for port in range(start, stop):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    return None


def _run_server(port: int):
    import app as clinic_app
    clinic_app.init_app()
    from waitress import serve as _serve
    _serve(clinic_app.app, host=HOST, port=port, threads=12,
           channel_timeout=120, connection_limit=200, _quiet=True)


def start_server(files_dir: str, port: int = 5000) -> int:
    """Start the embedded Flask server. Called once from Kotlin with the app's
    private filesDir. Returns the actual port the server is bound to. Idempotent."""
    with _lock:
        if _state['port']:
            return _state['port']

        paths = plat.resolve(files_dir)

        # Standalone customer build: config.py resolves the extracted bundle as
        # BASE_DIR and skips all sample-data seeding (see products/clinic/backend/config.py).
        os.environ['AURA_BUNDLE_DIR'] = paths['bundle']
        os.environ['AURA_APP_DATA'] = paths['data']
        os.environ['AURA_STANDALONE'] = '1'

        actual = _find_free_port(port, port + 20) or port
        t = threading.Thread(target=_run_server, args=(actual,), daemon=True)
        t.start()
        _state['port'] = actual
        return actual


def wait_until_ready(port: int, timeout: float = 45.0) -> bool:
    """Block until the server answers (used by Kotlin before showing the UI)."""
    url = f'http://{HOST}:{port}/'
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    return False


def server_port():
    return _state['port']
