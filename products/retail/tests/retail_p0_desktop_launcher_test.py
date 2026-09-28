"""
Desktop launcher -- native pywebview wiring (Phase 0 items #10 and #11(a),
plus round-3 items W1-W3: the cookie-clear safety default failing OPEN, the
Edge/Chrome fallback never clearing its own persistent cookie jar, and
open_customer_display()'s title precedence).

products/retail/desktop/launcher_retail.py is a standalone script, not a
package member, so this test puts its directory on sys.path directly (the
same trick the module already uses on itself for commercial_runtime)
rather than assuming an __init__.py exists.

No GUI is booted here -- this session has no display server, so a real
WebView2 window cannot be shown or screenshotted (see the crew's
structured report for exactly what was, and was not, verified against the
real exe). `webview` is entirely replaced with an in-memory fake that
records every create_window()/start() call; these tests prove the
launcher WIRES the right arguments to pywebview, not that WebView2 itself
renders or persists anything. The W1/W2 sections below go one level lower
still, unit-testing `_clear_cookies_then_load` and `_open_app_window`
directly against minimal fakes/monkeypatches, rather than through the full
`_run_native_window` wiring the D1 section above already covers.

Mutation-proof: for each guard below, the fix line was reverted, the test
was re-run and observed RED, then restored and re-run GREEN -- the
decisive lines are quoted in the crew's structured report, not
re-executed here (a source revert inside the test file itself would be
mutating the very file this test lives in).

Run (ONE FILE PER PROCESS -- see the crew contract):
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest products/retail/tests/retail_p0_desktop_launcher_test.py -q
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

_DESKTOP_DIR = Path(__file__).resolve().parents[1] / 'desktop'


class _FakeEvent:
    """Mirrors pywebview's own webview.event.Event (webview/event.py) just
    enough to prove the launcher's WIRING: __iadd__ for `+=` subscription
    and a synchronous fire(). The real class dispatches on a background
    thread (Event.set()); that concurrency isn't what these tests are
    proving, so fire() runs handlers inline."""

    def __init__(self):
        self._handlers = []

    def __iadd__(self, handler):
        self._handlers.append(handler)
        return self

    def fire(self):
        for handler in list(self._handlers):
            handler()


class _FakeWindowEvents:
    def __init__(self):
        self.closed = _FakeEvent()


class _FakeWindow:
    """Records the args it was created with, and behaves like a real
    pywebview Window closely enough to prove the launcher's wiring against
    the ACTUAL installed pywebview 6.2.1 source (.venv/Lib/site-packages/
    webview/platforms/winforms.py):

    - restore() NEVER raises, on an open window or a closed one. Real
      winforms.py resolves the uid via `BrowserView.instances.get(uid)` and
      simply returns None when it is not found (:1033-1036) -- there is no
      exception path. A prior version of this fixture had restore() raise
      to simulate a closed window; that modelled a pywebview behaviour that
      does not exist (ENGINEERING.md failure shape 2 -- the fixture
      manufactured the state that made the code under test pass).
    - close()/destroy() remove the window from the owning fake webview's
      `.windows` list and fire events.closed, exactly like winforms.py's
      on_close (:389-395: `del BrowserView.instances[self.uid]`, removes
      from the module-level `windows` list, then `self.closed.set()`,
      which IS `window.events.closed` -- see winforms.py:253).
    """

    def __init__(self, title, url, webview_ref, **kwargs):
        self.title = title
        self.url = url
        # `url` above is mutated by load_url() below, like the real Window's
        # own `real_url`/`original_url` -- created_url is a separate,
        # never-mutated record of what create_window() was actually given,
        # so a blank-page create_window() can be told apart from one that
        # was later loaded with load_url() (item D1's proof needs exactly
        # that distinction).
        self.created_url = url
        self.kwargs = kwargs
        self.events = _FakeWindowEvents()
        self.restored = False
        self.destroyed = False
        self.cookies_cleared = False
        self.loaded_urls = []
        self._webview = webview_ref

    def restore(self):
        self.restored = True

    def close(self):
        """Simulates the operator closing this window via the OS chrome --
        removes it from webview.windows and fires events.closed."""
        if self in self._webview.windows:
            self._webview.windows.remove(self)
        self.events.closed.fire()

    def destroy(self):
        self.destroyed = True
        self.close()

    def clear_cookies(self):
        """Mirrors Window.clear_cookies() (webview/window.py:321-326, real
        pywebview 6.2.1) closely enough to prove item D1's wiring: records
        that it ran, and in the shared call_log so ordering against
        load_url() can be asserted."""
        self.cookies_cleared = True
        self._webview.call_log.append(('clear_cookies', self))

    def load_url(self, url):
        """Mirrors Window.load_url() (webview/window.py:269-289)."""
        self.url = url
        self.loaded_urls.append(url)
        self._webview.call_log.append(('load_url', url))


class _FakeWebview:
    """Drop-in replacement for the `webview` module: never touches a real
    GUI, just records what the launcher asked for."""

    def __init__(self, screen_count=1):
        self.create_window_calls = []
        self.start_calls = []
        # Real pywebview keeps a module-level `windows: list[Window]`
        # (webview/__init__.py:160) that on_close removes a window from --
        # `self._window in webview.windows` is how the launcher tells a
        # live window from a closed one. Mirrored here the same way.
        self.windows = []
        # Real pywebview 6.2.1 makes `screens` a @module_property -- a
        # plain list read WITHOUT calling it (webview/__init__.py:512-521).
        # Mirrored here as a plain attribute, not a method: calling it
        # (screens()) is the exact bug this shape exists to catch.
        self.screens = [object() for _ in range(screen_count)]
        # Ordered log of clear_cookies()/load_url() calls across every
        # window this fake created -- item D1's ordering proof reads this.
        self.call_log = []

    def create_window(self, title, url=None, **kwargs):
        window = _FakeWindow(title, url, self, **kwargs)
        self.create_window_calls.append(window)
        self.windows.append(window)
        return window

    def start(self, *args, **kwargs):
        self.start_calls.append((args, kwargs))
        # Real pywebview 6.2.1 starts func= on its own background thread the
        # MOMENT start() is called, before the native window exists
        # (webview/__init__.py:294-301) -- it does not wait for anything.
        # Invoked synchronously here (no real GUI thread to race against),
        # which is enough to prove the launcher's WIRING (what gets called,
        # in what order), the same simplification _FakeEvent.fire() already
        # makes for events.
        func = kwargs.get('func')
        if func is not None:
            call_args = kwargs.get('args')
            if call_args is None:
                func()
            else:
                if not hasattr(call_args, '__iter__'):
                    call_args = (call_args,)
                func(*call_args)


def _import_launcher(monkeypatch, app_data_dir):
    """Imports launcher_retail fresh against a scratch AURA_APP_DATA.
    Module-level code (logging setup, a logs/ mkdir) runs on import, so
    each test gets a fresh module object against its own directory rather
    than sharing state with any other test in this process.

    Also clears AURA_TILL_REMEMBER_LOGIN (item D1) so every test starts
    from the deterministic default (cookies cleared) regardless of what is
    set in the ambient shell -- tests that want the opt-in set it back
    explicitly via monkeypatch.setenv."""
    monkeypatch.setenv('AURA_APP_DATA', str(app_data_dir))
    monkeypatch.delenv('AURA_TILL_REMEMBER_LOGIN', raising=False)
    monkeypatch.syspath_prepend(str(_DESKTOP_DIR))
    sys.modules.pop('launcher_retail', None)
    import launcher_retail  # noqa: E402  (path prepended just above)
    return launcher_retail


@pytest.fixture
def app_data(tmp_path):
    d = tmp_path / 'AuraRetailTest'
    d.mkdir()
    return d


# ── Item #10: private_mode=False, storage_path=<AURA_APP_DATA>/webview ──────

def test_start_is_called_with_private_mode_false(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview()
    monkeypatch.setitem(sys.modules, 'webview', fake)

    launcher._run_native_window('http://127.0.0.1:5000')

    assert len(fake.start_calls) == 1
    _, kwargs = fake.start_calls[0]
    assert kwargs.get('private_mode') is False, (
        f'webview.start() must be called with private_mode=False; got kwargs={kwargs}. '
        'pywebview 6.2.1 defaults private_mode=True, which uses a throwaway '
        'WebView2 profile and wipes localStorage/the session cookie on every launch.'
    )


def test_start_is_called_with_storage_path_under_app_data(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview()
    monkeypatch.setitem(sys.modules, 'webview', fake)

    launcher._run_native_window('http://127.0.0.1:5000')

    _, kwargs = fake.start_calls[0]
    expected = str(Path(str(app_data)) / 'webview')
    assert kwargs.get('storage_path') == expected, (
        f'expected storage_path={expected!r}, got {kwargs.get("storage_path")!r}'
    )


def test_open_external_links_in_browser_is_never_disabled():
    """Guards the OTHER half of item #10: OPEN_EXTERNAL_LINKS_IN_BROWSER
    must never be set to False (it would load the customer display, or any
    external link, into the till's own window). There is no such call
    anywhere in the fix -- assert the source never gains one."""
    source = (_DESKTOP_DIR / 'launcher_retail.py').read_text(encoding='utf-8')
    assert "OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False" not in source
    assert 'OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False' not in source


# ── Item D1 (Phase 0 review round 2): session cookie safety default ─────────
#
# The owner has not decided the till's session lifetime; today's behaviour
# (private_mode=False stays, for item #10's localStorage/theme/printer
# persistence) must not silently let the till stay signed in across a
# relaunch for up to 31 days (mt_auth.py's session.permanent=True). The
# conservative default: clear the WebView2 profile's cookies before the
# app URL ever loads, via a blank-page create_window() + clear_cookies() +
# load_url() sequence. AURA_TILL_REMEMBER_LOGIN=1 is the explicit opt-in
# that skips it.

def test_till_window_is_created_blank_by_default(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview()
    monkeypatch.setitem(sys.modules, 'webview', fake)
    monkeypatch.delenv(launcher.REMEMBER_LOGIN_ENV, raising=False)

    launcher._run_native_window('http://127.0.0.1:5000')

    till_window = fake.create_window_calls[0]
    assert till_window.created_url is None, (
        'by default the till window must be created on a BLANK page (no '
        'url= at create_window() time) so nothing already in the WebView2 '
        "profile -- including a stale session cookie -- can be read before "
        f"this launch clears it; got url={till_window.created_url!r}"
    )


def test_cookies_are_cleared_before_the_app_url_loads_by_default(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview()
    monkeypatch.setitem(sys.modules, 'webview', fake)
    monkeypatch.delenv(launcher.REMEMBER_LOGIN_ENV, raising=False)

    launcher._run_native_window('http://127.0.0.1:5000')

    till_window = fake.create_window_calls[0]
    assert till_window.cookies_cleared is True, (
        'window.clear_cookies() must be called by default -- otherwise a '
        'stale session cookie from a previous launch survives and the till '
        'stays signed in as the last cashier for up to 31 days'
    )
    assert till_window.loaded_urls == ['http://127.0.0.1:5000'], (
        f'the real app URL must be loaded via load_url() after the blank '
        f'page; got {till_window.loaded_urls}'
    )
    kinds = [k for k, _ in fake.call_log]
    assert 'clear_cookies' in kinds and 'load_url' in kinds
    assert kinds.index('clear_cookies') < kinds.index('load_url'), (
        f'cookies must be cleared BEFORE the app URL loads, or the app '
        f'page could read the session cookie before it is gone; call order '
        f'was {fake.call_log}'
    )


def test_remember_login_opt_in_skips_the_cookie_clear(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview()
    monkeypatch.setitem(sys.modules, 'webview', fake)
    monkeypatch.setenv(launcher.REMEMBER_LOGIN_ENV, '1')

    launcher._run_native_window('http://127.0.0.1:5000')

    till_window = fake.create_window_calls[0]
    assert till_window.url == 'http://127.0.0.1:5000', (
        'with the opt-in, the till window must load the app URL directly '
        'at create_window() time -- no blank page, no clear, no load_url'
    )
    assert till_window.cookies_cleared is False, (
        'AURA_TILL_REMEMBER_LOGIN=1 must skip the cookie clear entirely'
    )
    assert 'clear_cookies' not in [k for k, _ in fake.call_log]


def test_remember_login_opt_in_does_not_change_the_localstorage_persistence_path(monkeypatch, app_data):
    """The opt-in only skips the SESSION COOKIE clear -- item #10's
    private_mode=False/storage_path persistence (localStorage, theme,
    printer config) is a separate concern and must stay exactly as it is
    either way."""
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview()
    monkeypatch.setitem(sys.modules, 'webview', fake)
    monkeypatch.setenv(launcher.REMEMBER_LOGIN_ENV, '1')

    launcher._run_native_window('http://127.0.0.1:5000')

    assert len(fake.start_calls) == 1
    _, kwargs = fake.start_calls[0]
    assert kwargs.get('private_mode') is False
    expected = str(Path(str(app_data)) / 'webview')
    assert kwargs.get('storage_path') == expected


# ── Item #11(a): js_api exposes open_customer_display() (I-1) ───────────────

def test_create_window_is_given_a_js_api_with_open_customer_display(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview()
    monkeypatch.setitem(sys.modules, 'webview', fake)

    launcher._run_native_window('http://127.0.0.1:5000')

    assert len(fake.create_window_calls) == 1
    till_window = fake.create_window_calls[0]
    js_api = till_window.kwargs.get('js_api')
    assert js_api is not None, (
        'webview.create_window() for the till window must be given js_api=; '
        'without it window.pywebview.api never exists and the till falls back '
        'to window.open(), which pywebview hands to the system browser (no session).'
    )
    assert callable(getattr(js_api, 'open_customer_display', None)), (
        'js_api must expose an open_customer_display() method (interface I-1)'
    )


def test_open_customer_display_uses_second_screen_fullscreen_when_present(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview(screen_count=2)
    monkeypatch.setitem(sys.modules, 'webview', fake)

    api = launcher.CustomerDisplayAPI('http://127.0.0.1:5000')
    result = api.open_customer_display()

    assert result is True
    assert len(fake.create_window_calls) == 1
    display_window = fake.create_window_calls[0]
    assert display_window.url == 'http://127.0.0.1:5000/static/customer-display.html'
    assert display_window.kwargs.get('screen') is fake.screens[1]
    assert display_window.kwargs.get('fullscreen') is True


def test_open_customer_display_falls_back_to_normal_window_with_one_screen(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview(screen_count=1)
    monkeypatch.setitem(sys.modules, 'webview', fake)

    api = launcher.CustomerDisplayAPI('http://127.0.0.1:5000')
    api.open_customer_display()

    display_window = fake.create_window_calls[0]
    assert display_window.kwargs.get('screen') is None
    assert display_window.kwargs.get('fullscreen') is False


def test_open_customer_display_reuses_existing_window_instead_of_duplicating(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview(screen_count=2)
    monkeypatch.setitem(sys.modules, 'webview', fake)

    api = launcher.CustomerDisplayAPI('http://127.0.0.1:5000')
    api.open_customer_display()
    api.open_customer_display()

    assert len(fake.create_window_calls) == 1, (
        'a second call must reuse the existing window, not spawn a duplicate'
    )
    assert fake.create_window_calls[0].restored is True


def test_open_customer_display_reopens_after_operator_closes_it(monkeypatch, app_data):
    """Reviewer finding, round 2, on the crew's Phase 0 diff
    (launcher_retail.py CustomerDisplayAPI.open_customer_display): the
    previous version of this guard modelled a closed display window as one
    whose restore() RAISES. Real pywebview 6.2.1 never does that --
    winforms.py's restore() resolves the uid via
    `BrowserView.instances.get(uid)`, gets None for a closed window, and
    just returns (winforms.py:1033-1036); there is no exception path. So
    the old fixture (_FakeWindowThatDiesOnRestore) manufactured the only
    state in which the old code's except-and-retry branch ever ran
    (ENGINEERING.md failure shape 2) -- against a REAL closed window,
    restore() would silently "succeed", open_customer_display() would
    unconditionally `return True`, and no window would ever open again for
    the life of the process while the till was told every call succeeded.

    This test instead simulates the operator closing the display window
    the way real pywebview actually reports it: on_close removes the
    window from the module-level `webview.windows` list and fires
    events.closed (winforms.py:389-395) -- exactly what _FakeWindow.close()
    does. It then proves the NEXT open_customer_display() call creates a
    REAL fresh window rather than silently restoring a dead one."""
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview(screen_count=2)
    monkeypatch.setitem(sys.modules, 'webview', fake)

    api = launcher.CustomerDisplayAPI('http://127.0.0.1:5000')
    api.open_customer_display()
    first_window = fake.create_window_calls[0]

    first_window.close()  # operator closes the display window directly

    result = api.open_customer_display()

    assert result is True
    assert len(fake.create_window_calls) == 2, (
        'closing the display window must cause the NEXT open_customer_display() '
        'call to create a fresh window -- restore() on a real closed pywebview '
        'window is a silent no-op, never an exception, so relying on an '
        'exception to detect this leaves the till believing it succeeded while '
        'nothing is actually on screen'
    )
    assert api._window is fake.create_window_calls[1], (
        'the stale closed-window reference must be replaced by the new one'
    )
    assert api._window is not first_window


def test_display_closed_event_clears_the_stale_window_reference(monkeypatch, app_data):
    """Guards the PRIMARY detection path (the membership-check in
    open_customer_display() above is the backstop): subscribing to
    events.closed keeps api._window in sync THE MOMENT the operator closes
    the display, independent of whether open_customer_display() is ever
    called again. This matters for close_display() (till-close wiring,
    item #11(a)'s orphan-process fix, tested below): without this handler,
    close_display() could be left holding a reference to a Window whose
    native handle is already gone.

    Mutation-proof (per the reviewer's own instruction): removing the
    `window.events.closed += self._on_display_closed` line makes this test
    go red -- the membership-check backstop only re-evaluates on the NEXT
    open_customer_display() call, so nothing else clears api._window here."""
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview(screen_count=2)
    monkeypatch.setitem(sys.modules, 'webview', fake)

    api = launcher.CustomerDisplayAPI('http://127.0.0.1:5000')
    api.open_customer_display()
    display_window = fake.create_window_calls[0]

    display_window.close()

    assert api._window is None, (
        'events.closed must clear the stale window reference as soon as the '
        'operator closes the display, not only on the next '
        'open_customer_display() call'
    )


# ── Item D3 (Phase 0 review round 2): title must not be the vendor name ────

def test_customer_display_window_title_is_neutral_by_default(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview(screen_count=2)
    monkeypatch.setitem(sys.modules, 'webview', fake)

    api = launcher.CustomerDisplayAPI('http://127.0.0.1:5000')
    api.open_customer_display()

    display_window = fake.create_window_calls[0]
    assert display_window.title == 'Customer Display', (
        f'the native display window title must be neutral when no shop '
        f'name is passed; got {display_window.title!r}'
    )
    assert launcher.APP_NAME not in display_window.title, (
        f"the vendor name {launcher.APP_NAME!r} must never appear on the "
        f"customer-facing display window's title; got "
        f"{display_window.title!r}"
    )


def test_customer_display_window_title_uses_shop_name_when_passed(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview(screen_count=2)
    monkeypatch.setitem(sys.modules, 'webview', fake)

    api = launcher.CustomerDisplayAPI('http://127.0.0.1:5000')
    api.open_customer_display(shop_name='Sweets & Co')

    display_window = fake.create_window_calls[0]
    assert display_window.title == 'Sweets & Co — Customer Display', (
        f'expected the shop name plus the same em-dash separator '
        f'customer-display.js:159 uses for its own document.title; got '
        f'{display_window.title!r}'
    )
    assert launcher.APP_NAME not in display_window.title


# ── Item #11(a): closing the till must not orphan the process ──────────────

def test_till_close_destroys_the_customer_display_window(monkeypatch, app_data):
    """Reviewer finding, round 2: pywebview (winforms) only exits the
    process once EVERY window it owns has closed -- on_close checks
    `len(BrowserView.instances) == 0` before calling
    WinForms.Application.Exit() (winforms.py:397). If the operator closes
    the till window while the customer display is still open, the process
    -- and the backend thread it hosts -- survives as an orphan with only
    the display on screen, and a relaunch then hits the single-instance
    mutex ('Aura Retail is already running') with no till window to find.

    _run_native_window must wire the till window's own closed event to
    destroy its companion display window, so the till closing is always
    enough by itself to reach zero instances."""
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview(screen_count=2)
    monkeypatch.setitem(sys.modules, 'webview', fake)

    launcher._run_native_window('http://127.0.0.1:5000')

    till_window = fake.create_window_calls[0]
    js_api = till_window.kwargs.get('js_api')
    js_api.open_customer_display()
    display_window = fake.create_window_calls[1]
    assert display_window in fake.windows  # sanity: display is open

    till_window.close()  # operator closes the till

    assert display_window.destroyed is True, (
        "closing the till window must destroy the customer display window too, "
        "or pywebview never sees zero windows and the process (backend included) "
        "survives as an orphan"
    )
    assert display_window not in fake.windows


def test_till_close_is_harmless_when_display_was_never_opened(monkeypatch, app_data):
    """The allow-half of the till-close wiring: closing the till when the
    customer display was never opened (self._window is still None) must
    not raise -- close_display() has nothing to destroy and must no-op."""
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview(screen_count=2)
    monkeypatch.setitem(sys.modules, 'webview', fake)

    launcher._run_native_window('http://127.0.0.1:5000')
    till_window = fake.create_window_calls[0]

    till_window.close()  # must not raise

    assert len(fake.create_window_calls) == 1  # no display window was ever created


# ── Item W1 (Phase 0 review round 3): the cookie-clear safety default must ──
# fail CLOSED, not open. `Window.clear_cookies()` is `@_loaded_call`
# (webview/window.py:321), which waits up to 20s on the 'loaded' event and
# raises `WebViewException('Main window failed to start')` on a slow cold
# WebView2 start. The old code caught that exception and loaded the app URL
# anyway -- exactly the stale-session leak item #10/D1 exists to close,
# reopened by its own error path. These tests exercise
# `_clear_cookies_then_load` directly against a minimal fake window, one
# level below the full `_run_native_window` wiring the D1 section covers,
# because the interesting behaviour here is entirely inside that one
# function's retry/give-up logic.

class _FlakyClearCookiesWindow:
    """Minimal Window stand-in, purpose-built to control exactly how many
    times clear_cookies() raises before it succeeds (or never does).
    `fail_times` mirrors the real WebViewException shape
    (webview/window.py:44's own message) so a caller that pattern-matches
    on the exception text would see the same string a real cold-start
    timeout produces.

    Item W1 (Phase 0 review round 3, SECOND finding -- this fixture's own
    prior shape hid it). `clear_cookies()` timing out is specifically the
    case where WebView2's CoreWebView2 is NOT YET READY. In that exact
    state, real `Window.load_html()` (webview/window.py:290-302) never
    touches `window.html` at all -- it only forwards to the GUI layer, and
    WebView2's own GUI layer (edgechromium.py:209-216) then stores the HTML
    on `self.html`, AN ATTRIBUTE OF THE EdgeChrome WRAPPER, NOT of the
    pywebview `Window` model, and returns without navigating (it only calls
    `EnsureCoreWebView2Async`). The content only actually reaches the screen
    once the core finishes initialising and `on_webview_ready`
    (edgechromium.py:305-311) runs -- and THAT reads `self.pywebview_window.
    real_url`, then `self.pywebview_window.html` (the WINDOW MODEL's `html`,
    which `load_html()` never touched), falling back to pywebview's own
    blank `DEFAULT_HTML` when both are empty.

    An earlier version of this fixture recorded every `load_html()` call
    into `html_loaded` and stopped there, which made `window.load_html(...)`
    alone look sufficient -- exactly ENGINEERING.md failure shape 2, "the
    fixture manufactures the state that hides the bug": a fake `load_html()`
    that always 'renders' can never catch a real pywebview version that
    defers rendering until later. `html` (below) mirrors the Window MODEL's
    own attribute and is deliberately left untouched by `load_html()`;
    `simulate_core_becomes_ready()` reproduces `on_webview_ready`'s exact
    three-way decision so a test can tell "the launcher called load_html()"
    apart from "the blocked page is what a real WebView2 core would still be
    showing once it finishes starting."
    """

    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.clear_cookies_calls = 0
        self.loaded_urls = []
        self.html_loaded = []
        self.real_url = None  # mirrors Window.real_url; only load_url() sets this
        self.html = None      # mirrors Window.html; load_html() must NOT set this (see docstring)

    def clear_cookies(self):
        self.clear_cookies_calls += 1
        if self.clear_cookies_calls <= self.fail_times:
            raise Exception('Main window failed to start')

    def load_url(self, url):
        self.loaded_urls.append(url)
        self.real_url = url

    def load_html(self, html, base_uri=None):
        self.html_loaded.append(html)
        # Deliberately does NOT set self.html -- see the class docstring.
        # Real Window.load_html() doesn't either; only the GUI-internal
        # self.html (on the EdgeChrome wrapper, not this Window model) is
        # set, and that is exactly the attribute on_webview_ready never reads.

    def simulate_core_becomes_ready(self):
        """Reproduces edgechromium.py:305-311's on_webview_ready exactly:
        real_url wins, else window.html, else pywebview's own DEFAULT_HTML.
        Returns (kind, content) for what a real WebView2 core, finishing
        initialisation AFTER load_html()/load_url() were called, would
        actually end up showing."""
        if self.real_url:
            return ('url', self.real_url)
        if self.html:
            return ('html', self.html)
        return ('html', '__PYWEBVIEW_DEFAULT_HTML__')


def test_cookie_clear_retries_once_then_loads_app_when_the_retry_succeeds(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    window = _FlakyClearCookiesWindow(fail_times=1)  # fails once, then succeeds

    launcher._clear_cookies_then_load(window, 'http://127.0.0.1:5000')

    assert window.clear_cookies_calls == 2, (
        'a single clear_cookies() failure must be retried once before giving up'
    )
    assert window.loaded_urls == ['http://127.0.0.1:5000'], (
        'once the retry succeeds, the app must load normally -- exactly like '
        'the always-succeeds case the D1 tests above already cover'
    )
    assert window.html_loaded == [], 'no blocked page when the retry recovers'


def test_cookie_clear_fails_closed_when_both_attempts_raise(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    window = _FlakyClearCookiesWindow(fail_times=99)  # never succeeds

    launcher._clear_cookies_then_load(window, 'http://127.0.0.1:5000')

    assert window.clear_cookies_calls == 2, (
        'must try once, retry once, then give up -- not loop forever waiting '
        'on a WebView2 core that is not coming back'
    )
    assert window.loaded_urls == [], (
        'W1 FAIL-CLOSED: the live app URL must NEVER load while cookies could '
        'not be confirmed cleared after a retry -- loading it anyway is '
        'exactly what let a previous cashier stay signed in'
    )
    assert len(window.html_loaded) == 1, (
        'a blocked/error page must be shown instead of the live app when the '
        'session safety check could not be confirmed'
    )
    assert 'Aura Retail' in window.html_loaded[0]

    # Item W1 (round 3, SECOND finding): calling window.load_html() is not
    # enough on its own -- see the fixture's docstring. This is exactly the
    # timeout case: CoreWebView2 was not ready DURING the call above, and
    # only finishes initialising afterwards. Prove the blocked page is what
    # actually ends up on screen once that happens, not pywebview's own
    # blank DEFAULT_HTML.
    shown_kind, shown_content = window.simulate_core_becomes_ready()
    assert shown_kind == 'html' and 'Aura Retail' in shown_content, (
        'W1: once WebView2 finally finishes initialising, on_webview_ready '
        'must load the BLOCKED page -- it reads window.html (the Window '
        'MODEL attribute), not whatever window.load_html() stored '
        'internally, so the fail-closed branch must set window.html = '
        '_SESSION_SAFETY_BLOCKED_HTML itself, or the till is left showing a '
        f"permanently blank window instead. Got ({shown_kind!r}, {shown_content!r})."
    )


# ── Item W2 (Phase 0 review round 3): the Edge/Chrome --app fallback keeps a ─
# PERSISTENT --user-data-dir that D1/W1 above never touch, so a till that
# ends up on this fallback (WebView2 missing/unavailable) stayed signed in as
# the last cashier for the full 31-day Flask session lifetime regardless of
# the native-path fix. `os.path.isfile` is forced to True so this test finds
# *some* browser candidate deterministically, on any machine, regardless of
# whether Edge/Chrome are actually installed at those exact paths here;
# `subprocess.Popen` is replaced so nothing is actually launched.

class _DummyProcess:
    """Stands in for the Popen object _open_app_window returns; nothing
    calls methods on it in these tests."""


def test_edge_fallback_clears_stale_cookies_by_default(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    monkeypatch.delenv(launcher.REMEMBER_LOGIN_ENV, raising=False)
    monkeypatch.setattr(os.path, 'isfile', lambda p: True)
    popen_calls = []
    monkeypatch.setattr(subprocess, 'Popen', lambda args, **kw: (popen_calls.append(args), _DummyProcess())[1])

    cookies_dir = Path(app_data) / 'app_window' / 'Default' / 'Network'
    cookies_dir.mkdir(parents=True)
    cookies = cookies_dir / 'Cookies'
    journal = cookies_dir / 'Cookies-journal'
    cookies.write_bytes(b'stale-session-cookie')
    journal.write_bytes(b'stale-journal')

    launcher._open_app_window('http://127.0.0.1:5000')

    assert len(popen_calls) == 1, 'sanity: the fallback must still actually launch a browser'
    assert not cookies.exists(), (
        'W2: the Edge/Chrome --app fallback profile is PERSISTENT and D1/W1 '
        'never touch it -- its stale Cookies file must be deleted before '
        'launch, or the till stays signed in as the last cashier for up to '
        '31 days on this fallback path'
    )
    assert not journal.exists(), 'the Cookies-journal sidecar must be removed with it'


def test_edge_fallback_remember_login_keeps_cookies(monkeypatch, app_data):
    """The allow-half: the opt-in must skip the fallback cookie clear too,
    exactly like it already skips the native WebView2 one (D1)."""
    launcher = _import_launcher(monkeypatch, app_data)
    monkeypatch.setenv(launcher.REMEMBER_LOGIN_ENV, '1')
    monkeypatch.setattr(os.path, 'isfile', lambda p: True)
    monkeypatch.setattr(subprocess, 'Popen', lambda args, **kw: _DummyProcess())

    cookies_dir = Path(app_data) / 'app_window' / 'Default' / 'Network'
    cookies_dir.mkdir(parents=True)
    cookies = cookies_dir / 'Cookies'
    cookies.write_bytes(b'kept-session-cookie')

    launcher._open_app_window('http://127.0.0.1:5000')

    assert cookies.exists() and cookies.read_bytes() == b'kept-session-cookie', (
        'AURA_TILL_REMEMBER_LOGIN=1 must skip the fallback cookie clear, the '
        'same opt-in D1 already honours on the native WebView2 path'
    )


def test_edge_fallback_first_run_profile_has_no_cookie_files_yet(monkeypatch, app_data):
    """The common case: a profile that has never been launched before has
    neither file. Must not raise (missing_ok, not a logged warning)."""
    launcher = _import_launcher(monkeypatch, app_data)
    monkeypatch.delenv(launcher.REMEMBER_LOGIN_ENV, raising=False)
    monkeypatch.setattr(os.path, 'isfile', lambda p: True)
    monkeypatch.setattr(subprocess, 'Popen', lambda args, **kw: _DummyProcess())

    launcher._open_app_window('http://127.0.0.1:5000')  # must not raise


# ── Item W3 (Phase 0 review round 3): open_customer_display(title=, ────────
# shop_name=) -- a caller-supplied, ready-made title (the till's own,
# already translated through t()) must win outright over shop_name and the
# neutral default, so the native window's title/taskbar text can finally be
# translated -- D3 (round 2) fixed the vendor-name leak but left the title
# permanently English.

def test_open_customer_display_uses_caller_supplied_title_when_given(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview(screen_count=2)
    monkeypatch.setitem(sys.modules, 'webview', fake)

    api = launcher.CustomerDisplayAPI('http://127.0.0.1:5000')
    api.open_customer_display(title='شاشة العميل')

    display_window = fake.create_window_calls[0]
    assert display_window.title == 'شاشة العميل', (
        "W3: a caller-supplied title (the till's own, already-translated "
        f"string) must be used verbatim; got {display_window.title!r}"
    )


def test_open_customer_display_title_wins_over_shop_name(monkeypatch, app_data):
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview(screen_count=2)
    monkeypatch.setitem(sys.modules, 'webview', fake)

    api = launcher.CustomerDisplayAPI('http://127.0.0.1:5000')
    api.open_customer_display(title='Custom Title', shop_name='Sweets & Co')

    display_window = fake.create_window_calls[0]
    assert display_window.title == 'Custom Title', (
        f'a caller-supplied title must win over shop_name; got {display_window.title!r}'
    )


def test_open_customer_display_falls_back_to_shop_name_when_no_title_given(monkeypatch, app_data):
    """Guards that W3 did not regress D3's existing shop_name behaviour --
    this is the same scenario test_customer_display_window_title_uses_
    shop_name_when_passed already covers above; kept here too as the
    explicit 'no title given' half of W3's own precedence chain."""
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview(screen_count=2)
    monkeypatch.setitem(sys.modules, 'webview', fake)

    api = launcher.CustomerDisplayAPI('http://127.0.0.1:5000')
    api.open_customer_display(shop_name='Sweets & Co')

    display_window = fake.create_window_calls[0]
    assert display_window.title == 'Sweets & Co — Customer Display'


def test_open_customer_display_blank_title_falls_through_to_shop_name(monkeypatch, app_data):
    """A caller passing an empty/whitespace-only title (e.g. an untranslated
    key that resolved to '') must not win -- it falls through exactly like
    no title at all, rather than creating a window with a blank title."""
    launcher = _import_launcher(monkeypatch, app_data)
    fake = _FakeWebview(screen_count=2)
    monkeypatch.setitem(sys.modules, 'webview', fake)

    api = launcher.CustomerDisplayAPI('http://127.0.0.1:5000')
    api.open_customer_display(title='   ', shop_name='Sweets & Co')

    display_window = fake.create_window_calls[0]
    assert display_window.title == 'Sweets & Co — Customer Display'
