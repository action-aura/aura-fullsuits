"""
Desktop launcher -- full-screen till window (owner 2026-10-07: "it needs to be
fullscreen not just windowed").

products/retail/desktop/launcher_retail.py gained:
  - a persisted preference (`<AURA_APP_DATA>/window.json`) read by
    `_load_fullscreen_pref` / written by `_save_fullscreen_pref`;
  - four CustomerDisplayAPI methods the shell calls through
    window.pywebview.api (window_mode / toggle_fullscreen / minimize_app /
    quit_app), driving a private `_till_window`;
  - `_run_native_window` passing fullscreen=<pref>, maximized=True to the
    TILL window only.

No GUI is booted: `webview` is replaced with an in-memory fake, exactly as
retail_p0_desktop_launcher_test.py does. These tests prove the launcher's
WIRING and state handling, not that WebView2 really goes full screen.

What each group is for (ENGINEERING.md section 1 -- assert that the thing RAN,
not just an outcome):
  - the fake window COUNTS toggle/minimize/destroy calls, so "toggle works"
    cannot pass by the API merely flipping its own boolean;
  - the raising-window test pins ORDER: the flag and the pref file must only
    change AFTER the window call succeeded, otherwise a failed toggle leaves
    the shell drawing "full screen" over a window that is not, and the next
    launch restores a state the owner never reached;
  - the default-True tests pin the safe direction: a missing/corrupt file means
    "never chosen", which must open full screen, not raise and not open small.
    The allow-half (an explicit False IS honoured) is pinned too, because a
    loader that always returned True would pass every default test.

Mutation-proof: see the crew report -- (a) _load_fullscreen_pref returning
False on FileNotFoundError, (b) dropping `display_api._till_window = ...`,
(c) flipping `_is_fullscreen` before the window call, each observed RED and
restored GREEN.

Run (ONE FILE PER PROCESS):
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest products/retail/tests/retail_launcher_fullscreen_test.py -q
"""
import json
import sys
from pathlib import Path

import pytest

_DESKTOP_DIR = Path(__file__).resolve().parents[1] / 'desktop'


class _FakeEvent:
    def __init__(self):
        self._handlers = []

    def __iadd__(self, handler):
        self._handlers.append(handler)
        return self


class _FakeWindowEvents:
    def __init__(self):
        self.closed = _FakeEvent()


class _CountingWindow:
    """A till window that counts the three calls the API makes. `fail_toggle`
    makes toggle_fullscreen raise, like a window already torn down."""

    def __init__(self, fail_toggle=False):
        self.toggle_calls = 0
        self.minimize_calls = 0
        self.destroy_calls = 0
        self._fail_toggle = fail_toggle

    def toggle_fullscreen(self):
        if self._fail_toggle:
            raise RuntimeError('window is gone')
        self.toggle_calls += 1

    def minimize(self):
        self.minimize_calls += 1

    def destroy(self):
        self.destroy_calls += 1


class _FakeWebWindow:
    """What the fake `webview.create_window` hands back (records kwargs)."""

    def __init__(self, title, url, **kwargs):
        self.title = title
        self.created_url = url
        self.kwargs = kwargs
        self.events = _FakeWindowEvents()

    def clear_cookies(self):
        pass

    def load_url(self, url):
        pass


class _FakeWebview:
    def __init__(self):
        self.create_window_calls = []
        self.windows = []
        self.screens = [object()]

    def create_window(self, title, url=None, **kwargs):
        w = _FakeWebWindow(title, url, **kwargs)
        self.create_window_calls.append(w)
        self.windows.append(w)
        return w

    def start(self, *args, **kwargs):
        # Returns immediately; the cookie-clear func= is not under test here.
        return None


def _import_launcher(monkeypatch, app_data_dir):
    """Fresh import against a scratch AURA_APP_DATA, then ALSO pin the module's
    own `_app_data` attribute (the prefs path is computed from it at call
    time), so the tests cannot touch a real install's window.json."""
    monkeypatch.setenv('AURA_APP_DATA', str(app_data_dir))
    monkeypatch.delenv('AURA_TILL_REMEMBER_LOGIN', raising=False)
    monkeypatch.syspath_prepend(str(_DESKTOP_DIR))
    sys.modules.pop('launcher_retail', None)
    import launcher_retail  # noqa: E402
    monkeypatch.setattr(launcher_retail, '_app_data', str(app_data_dir))
    return launcher_retail


@pytest.fixture
def app_data(tmp_path):
    d = tmp_path / 'AuraRetailTest'
    d.mkdir()
    return d


def _api(launcher, window):
    api = launcher.CustomerDisplayAPI('http://127.0.0.1:5000')
    api._till_window = window
    return api


# ── 1. The preference file ──────────────────────────────────────────────────

def test_pref_defaults_to_true_when_file_missing(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    assert not (app_data / 'window.json').exists()
    assert launcher._load_fullscreen_pref() is True


def test_pref_defaults_to_true_on_corrupt_json(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    (app_data / 'window.json').write_text('{not json', encoding='utf-8')
    assert launcher._load_fullscreen_pref() is True


@pytest.mark.parametrize('raw', ['{"fullscreen": "no"}', '{"fullscreen": 0}',
                                 '{"fullscreen": null}', '[]', '{}'])
def test_pref_defaults_to_true_when_value_is_not_a_bool(monkeypatch, app_data, raw):
    # 0 is falsy but is NOT a bool choice: treating it as False would let a
    # hand-edited/garbled file silently shrink the till out of full screen.
    launcher = _import_launcher(monkeypatch, app_data)
    (app_data / 'window.json').write_text(raw, encoding='utf-8')
    assert launcher._load_fullscreen_pref() is True


def test_pref_round_trips_both_values(monkeypatch, app_data):
    # Both directions: an explicit False must be honoured (a loader that
    # always said True would pass every default test above).
    launcher = _import_launcher(monkeypatch, app_data)
    launcher._save_fullscreen_pref(False)
    assert json.loads((app_data / 'window.json').read_text('utf-8')) == {'fullscreen': False}
    assert launcher._load_fullscreen_pref() is False
    launcher._save_fullscreen_pref(True)
    assert launcher._load_fullscreen_pref() is True


# ── 2. The API drives the window and records the choice ─────────────────────

def test_toggle_fullscreen_calls_window_flips_state_and_persists(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    win = _CountingWindow()
    api = _api(launcher, win)
    assert api.window_mode() == {'native': True, 'fullscreen': True}

    assert api.toggle_fullscreen() == {'fullscreen': False}
    assert win.toggle_calls == 1, 'the window itself must be asked to toggle exactly once'
    assert api.window_mode()['fullscreen'] is False
    assert launcher._load_fullscreen_pref() is False

    assert api.toggle_fullscreen() == {'fullscreen': True}
    assert win.toggle_calls == 2
    assert launcher._load_fullscreen_pref() is True


def test_minimize_and_quit_each_call_the_window_once(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    win = _CountingWindow()
    api = _api(launcher, win)
    assert api.minimize_app() == {'ok': True}
    assert win.minimize_calls == 1
    assert api.quit_app() == {'ok': True}
    assert win.destroy_calls == 1
    assert win.toggle_calls == 0


# ── 3. No window yet: refuse, touch nothing ─────────────────────────────────

def test_without_a_till_window_every_control_refuses_and_writes_nothing(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    api = _api(launcher, None)
    assert api.toggle_fullscreen() == {'ok': False}
    assert api.minimize_app() == {'ok': False}
    assert api.quit_app() == {'ok': False}
    assert api._is_fullscreen is True
    assert not (app_data / 'window.json').exists()


# ── 4. A failing window must not change state ───────────────────────────────

def test_failed_toggle_leaves_state_and_pref_untouched(monkeypatch, app_data):
    # The flip + save come AFTER the window call. If the order were reversed a
    # dead window would leave the shell showing the wrong mode and persist a
    # state the owner never actually reached.
    launcher = _import_launcher(monkeypatch, app_data)
    api = _api(launcher, _CountingWindow(fail_toggle=True))
    result = api.toggle_fullscreen()
    assert result['ok'] is False
    assert 'window is gone' in result['error']
    assert api._is_fullscreen is True
    assert not (app_data / 'window.json').exists()


# ── 5. _run_native_window wiring ────────────────────────────────────────────

@pytest.mark.parametrize('pref', [True, False])
def test_till_window_gets_fullscreen_pref_and_maximized(monkeypatch, app_data, pref):
    launcher = _import_launcher(monkeypatch, app_data)
    launcher._save_fullscreen_pref(pref)
    fake = _FakeWebview()
    monkeypatch.setitem(sys.modules, 'webview', fake)

    launcher._run_native_window('http://127.0.0.1:5000')

    till = fake.create_window_calls[0]
    assert till.kwargs.get('fullscreen') is pref
    assert till.kwargs.get('maximized') is True
    api = till.kwargs['js_api']
    assert api._till_window is till, (
        'the API must hold the till window or every window control returns ok:False')
    assert api._is_fullscreen is pref
    assert api.window_mode()['fullscreen'] is pref


def test_till_window_remember_login_branch_is_also_fullscreen(monkeypatch, app_data):
    # The create_window call exists twice (cookie-clear default / remember-login
    # opt-in); a fix to only one branch would pass the test above.
    launcher = _import_launcher(monkeypatch, app_data)
    monkeypatch.setenv(launcher.REMEMBER_LOGIN_ENV, '1')
    launcher._save_fullscreen_pref(False)
    fake = _FakeWebview()
    monkeypatch.setitem(sys.modules, 'webview', fake)

    launcher._run_native_window('http://127.0.0.1:5000')

    till = fake.create_window_calls[0]
    assert till.kwargs.get('fullscreen') is False
    assert till.kwargs.get('maximized') is True
    assert till.kwargs['js_api']._till_window is till


# ── 6. pywebview recursion hazard ───────────────────────────────────────────

def test_no_public_attribute_of_the_js_api_holds_a_window(monkeypatch, app_data):
    # pywebview walks PUBLIC attributes of js_api to build window.pywebview.api;
    # reaching a Window object there recurses/hangs. Windows live only in
    # underscore-prefixed attributes.
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview()
    monkeypatch.setitem(sys.modules, 'webview', fake)

    launcher._run_native_window('http://127.0.0.1:5000')

    api = fake.create_window_calls[0].kwargs['js_api']
    assert api._till_window is not None  # guard against a vacuous pass
    windows = set(map(id, fake.create_window_calls))
    for name, value in vars(api).items():
        if name.startswith('_'):
            continue
        assert id(value) not in windows and not isinstance(value, _FakeWebWindow), (
            f'public attribute {name!r} of the js_api holds a window object')

# -- 7. The Edge/Chrome fallback ------------------------------------------------
# When the native window cannot open, the till falls back to Edge/Chrome in
# --app mode. That window must open MAXIMISED, never --kiosk/--start-fullscreen:
# in those modes the page has no title bar and no way back out that a
# non-technical owner would find. Count the launch and read its real args.
def test_edge_fallback_starts_maximized_never_kiosk(monkeypatch, app_data):
    import os
    import subprocess
    launcher = _import_launcher(monkeypatch, app_data)
    monkeypatch.setattr(os.path, 'isfile', lambda p: True)
    popen_calls = []

    class _Proc:
        pass

    monkeypatch.setattr(subprocess, 'Popen', lambda args, **kw: (popen_calls.append(args), _Proc())[1])
    launcher._open_app_window('http://127.0.0.1:5000')
    assert len(popen_calls) == 1, f'expected one browser launch, got {len(popen_calls)}'
    args = popen_calls[0]
    assert '--start-maximized' in args
    assert '--kiosk' not in args and '--start-fullscreen' not in args
