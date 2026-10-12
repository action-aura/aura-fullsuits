"""INSTALL-1 item 5: the Edge/Chrome fallback exits with its window, and the
launcher leaves a crash trail.

Groups, and what each guards:
  * _wait_for_app_window: the loop used to ignore the browser process, so
    closing the fallback window left AuraRetail.exe alive and holding the
    single-instance mutex. The fake process COUNTS poll() calls, so "returned
    because the window closed" cannot be satisfied by a loop that never looked.
    The deny-half: a still-open window keeps it waiting; the default-browser
    path (no process) keeps the old run-until-the-server-stops behaviour.
  * main() end to end on the fallback path -- the wiring, not just the helper.
  * crash trail: a real faulthandler file, and a real crash-store row written by
    sys.excepthook / threading.excepthook. Normal exits (SystemExit,
    KeyboardInterrupt) must NOT become crash rows, and a thread crash must be
    recorded once, not twice, when app.py's own capture is chained on top.

Global hooks are restored by the fixture: a test that left faulthandler or
sys.excepthook replaced would corrupt every later test in the process.

Run (ONE FILE PER PROCESS):
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest products/retail/tests/retail_launcher_crash_trail_test.py -q
"""
import faulthandler
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

_DESKTOP_DIR = Path(__file__).resolve().parents[1] / 'desktop'


def _import_launcher(monkeypatch, app_data_dir):
    monkeypatch.setenv('AURA_APP_DATA', str(app_data_dir))
    monkeypatch.syspath_prepend(str(_DESKTOP_DIR))
    sys.modules.pop('launcher_retail', None)
    import launcher_retail  # noqa: E402
    monkeypatch.setattr(launcher_retail, '_app_data', str(app_data_dir))
    monkeypatch.setattr(launcher_retail, '_running_version', lambda: '1.0.0-rc.9')
    return launcher_retail


@pytest.fixture
def L(monkeypatch, tmp_path):
    d = tmp_path / 'AuraRetailTest'
    d.mkdir()
    monkeypatch.setattr(sys, 'excepthook', sys.excepthook)          # restored on teardown
    monkeypatch.setattr(threading, 'excepthook', threading.excepthook)
    was_enabled = faulthandler.is_enabled()
    mod = _import_launcher(monkeypatch, d)
    yield mod
    faulthandler.disable()
    if was_enabled:
        faulthandler.enable()
    if mod._faulthandler_file is not None:
        mod._faulthandler_file.close()


# -- Edge fallback wait --------------------------------------------------------------
class FakeProc:
    def __init__(self, closes_after_polls=None):
        self.polls = 0
        self._limit = closes_after_polls

    def poll(self):
        self.polls += 1
        if self._limit is not None and self.polls >= self._limit:
            return 0
        return None


def test_returns_when_the_window_closes(L):
    proc = FakeProc(closes_after_polls=3)
    sleeps = []
    assert L._wait_for_app_window(proc, lambda: True, sleep=sleeps.append) == 'window_closed'
    assert proc.polls == 3 and len(sleeps) == 2          # it really watched the process


def test_keeps_waiting_while_the_window_is_open(L):
    proc = FakeProc(closes_after_polls=None)
    alive = iter([True] * 5 + [False])
    assert L._wait_for_app_window(proc, lambda: next(alive), sleep=lambda s: None) == 'server_stopped'
    assert proc.polls == 5                                # five looks, window never closed


def test_default_browser_path_has_no_process_and_waits_for_the_server(L):
    alive = iter([True, True, True, False])
    assert L._wait_for_app_window(None, lambda: next(alive), sleep=lambda s: None) == 'server_stopped'


def test_main_returns_after_the_fallback_window_closes(monkeypatch, L):
    stop = threading.Event()
    monkeypatch.setattr(L, '_install_crash_trail', lambda **k: {})
    monkeypatch.setattr(L, '_acquire_single_instance', lambda: 'h')
    monkeypatch.setattr(L, '_bind_free_socket', lambda: SimpleNamespace(getsockname=lambda: ('127.0.0.1', 5999)))
    monkeypatch.setattr(L, '_run_server', lambda sock: stop.wait(30))     # a server that never ends by itself
    monkeypatch.setattr(L, 'check_readiness', lambda *a, **k: SimpleNamespace(
        ready=True, reason='ready', detail='', attempts=1, elapsed=0.0))
    monkeypatch.setattr(L, '_mark_healthy_boot', lambda: None)
    monkeypatch.setattr(L, '_run_native_window', lambda url: False)       # pywebview failed
    opened = []
    monkeypatch.setattr(L, '_open_app_window', lambda url: opened.append(url) or FakeProc(closes_after_polls=2))
    monkeypatch.setattr(L.time, 'sleep', lambda s: None)
    import urllib.request

    class _R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"product_code": "AURA_RETAIL"}'
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: _R())

    done = threading.Thread(target=L.main, daemon=True)
    done.start()
    done.join(10)
    stop.set()
    assert not done.is_alive(), 'main() kept running after the fallback window closed'
    assert len(opened) == 1


# -- crash trail ------------------------------------------------------------------
class _Store:
    def __init__(self):
        self.rows = []

    def record(self, report):
        self.rows.append(report)


def _install(L, tmp_path, store=None):
    store = store if store is not None else _Store()
    faulthandler.disable()        # pytest enables it itself; start from off so is_enabled() means OUR enable
    got = L._install_crash_trail(app_data=str(tmp_path / 'trail'), store_factory=lambda d: store,
                                 version_fn=lambda: '1.0.0-rc.9')
    return got, store


def test_faulthandler_is_enabled_on_a_real_file_under_logs(L, tmp_path):
    got, _ = _install(L, tmp_path)
    path = Path(got['faulthandler_path'])
    assert path == tmp_path / 'trail' / 'logs' / 'faulthandler.log' and path.exists()
    assert faulthandler.is_enabled()          # off before _install, so this is OUR enable


def test_an_uncaught_main_thread_exception_becomes_a_crash_row(L, tmp_path):
    _, store = _install(L, tmp_path)
    try:
        raise ValueError('boom at boot')
    except ValueError:
        sys.excepthook(*sys.exc_info())
    assert len(store.rows) == 1
    row = store.rows[0]
    assert row['kind'] == 'launcher' and row['exception_type'] == 'ValueError'
    assert row['app_version'] == '1.0.0-rc.9' and row['platform'] == 'WINDOWS'


@pytest.mark.parametrize('exc', [SystemExit(0), KeyboardInterrupt()])
def test_normal_exits_are_not_crashes(L, tmp_path, exc):
    _, store = _install(L, tmp_path)
    sys.excepthook(type(exc), exc, None)
    assert store.rows == []


def test_a_thread_that_dies_before_the_app_hook_exists_is_recorded(L, tmp_path):
    _, store = _install(L, tmp_path)
    t = threading.Thread(target=lambda: (_ for _ in ()).throw(RuntimeError('import failed')))
    t.start()
    t.join()
    assert [r['kind'] for r in store.rows] == ['thread']


def test_a_thread_crash_is_not_recorded_twice_when_the_app_hook_sits_on_top(L, tmp_path):
    _, store = _install(L, tmp_path)
    ours = threading.excepthook
    app_recorded = []

    def app_hook(args):                      # what crash_capture.install chains on top
        app_recorded.append(args.exc_type)
        ours(args)
    threading.excepthook = app_hook
    t = threading.Thread(target=lambda: (_ for _ in ()).throw(RuntimeError('later crash')))
    t.start()
    t.join()
    assert app_recorded == [RuntimeError]    # the app's capture ran...
    assert store.rows == []                  # ...and ours stood down


def test_a_missing_store_never_breaks_startup(L, tmp_path):
    got = L._install_crash_trail(app_data=str(tmp_path / 'x'),
                                 store_factory=lambda d: (_ for _ in ()).throw(OSError('read-only')))
    assert got['store'] is None
    try:
        raise ValueError('still fine')
    except ValueError:
        sys.excepthook(*sys.exc_info())      # logs, does not raise
