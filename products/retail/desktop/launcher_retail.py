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

Item W4 (Phase 0 review round 3 correction, on items #10/#11/D1-D3 below):
those items WERE measured live against a real, frozen PyInstaller exe built
from this tree, in round 2 -- despite an earlier round-2 verifier session in
this same file's history believing native-window checks (Win32 EnumWindows,
a real WM_CLOSE) were environmentally infeasible from a sandboxed agent
session ("EnumWindows found ZERO windows for the whole launcher process").
That diagnosis was wrong, not a real limitation: `.venv\\Scripts\\python.exe`
is a redirector stub, and the pywebview/WebView2 windows belong to ITS CHILD
PROCESS, not the stub's own PID, so a PID-only EnumWindows lookup finds
nothing even while WebView2 is rendering correctly underneath it. Walking
the process tree (enumerate every descendant PID of the launched process and
match windows against ANY of them, not just the top one) finds the real
windows. A later round-2 verifier session, using exactly that technique,
drove real WM_CLOSE against this exact code and the frozen exe and confirmed:
D1 (6/6 default relaunches came back signed out; 4/4
AURA_TILL_REMEMBER_LOGIN=1 relaunches came back signed in; a cookie row was
on disk before every relaunch), #10 (the theme and aura_printer_cfg survived
all 14 relaunches, 3 of them on the exe), D2 (WM_CLOSE on the display removes
its window and CDP target; a second open call does not duplicate it; WM_CLOSE
on the till with the display still open ends the process within 15s), and D3
(native titles went from ['Aura Retail'] to ['Customer Display','Aura
Retail']). A future session hitting the same "EnumWindows sees nothing"
symptom against this launcher should walk the process tree before concluding
the check is infeasible -- it usually is not.

CORRECTION to the paragraph above (Phase 0 review round 3, SECOND pass on
item W4 itself): it originally labelled those relaunch numbers "D1/W1"
together, as if they also verified W1's fail-closed retry branch
(`_clear_cookies_then_load`'s `except` path, below). They did not -- every
one of those relaunches was a NORMAL launch where `clear_cookies()`
succeeded on the first try, so none of them ever reached that branch at all.
This was a real, live instance of the exact thing this being a bare
docstring risks: a specific, cited-looking pass count ("D1/W1... 6/6...")
sitting next to code it does not actually cover, with nothing mechanical to
catch the claim drifting from the code -- precisely the "stale status doc"
failure mode this codebase has hit before (see CLAUDE.md's own history of
correcting itself). A round-3 adversarial verifier, working from this exact
paragraph, found that the fail-closed branch it seemed to vouch for was
ITSELF BROKEN: `window.load_html(_SESSION_SAFETY_BLOCKED_HTML)` alone never
reaches the screen when CoreWebView2 is not yet ready (real
`Window.load_html()`, webview/window.py:290-302, never sets `window.html`;
`on_webview_ready`, edgechromium.py:305-311, is what falls back to
`window.html` once the core comes up, and that attribute was never set) --
the till was left on a permanently blank window instead of the blocked page.
Now fixed (`_clear_cookies_then_load` sets `window.html` directly before
calling `load_html()` -- see that function's own docstring) and covered two
ways that WOULD have caught the original gap: a mutation-proven unit test
(`retail_p0_desktop_launcher_test.py::
test_cookie_clear_fails_closed_when_both_attempts_raise`, extended with
`_FlakyClearCookiesWindow.simulate_core_becomes_ready()`, which reproduces
`on_webview_ready`'s exact real_url/window.html/DEFAULT_HTML decision
instead of only recording that `load_html()` was called), and a live check
against the real, unmodified `_clear_cookies_then_load` on a real pywebview/
WebView2 window with `clear_cookies()` forced to raise with NO warm-up delay
(the actual not-ready-core precondition; an earlier live check in this same
round had slept first and always landed in the already-ready fast path,
which cannot see this bug -- same shape as the fixture gap above). Measured
(`C:\\Users\\MSI\\.claude\\jobs\\c5051313\\tmp\\p0\\r2\\desktop\\r3\\live_w1.py`
/ `live_w1_results.json`): with the fix, the blocked page's own text was in
the DOM within 2.3s and the live app URL was never reached; with the fix
reverted, every one of 46 samples across 12s stayed `{title:'', body:'',
href:'about:blank'}` -- the exact permanently-blank window the finding
described, restored and reconfirmed green afterwards (byte-identical
restore, `cmp`). The lesson this correction leaves in place on purpose,
rather than deleting: a claim like the paragraph above, however accurately
cited at the time it was written, is not the same thing as a test that
would notice if the code it describes stopped being true.
"""
import os
import sys
import json
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
# Item D1 (Phase 0 review round 2) -- SESSION SAFETY DEFAULT. Set to skip the
# launch-time cookie clear below and let the till stay signed in across a
# relaunch/reboot (Flask's session cookie is permanent for 31 days --
# commercial_runtime/identity/mt_auth.py sets session.permanent=True on every
# login with no "remember me" opt-in of its own). Off by default: see
# _run_native_window()'s comment for why "off" is the conservative choice
# pending the owner's decision on till session lifetime, not a claim that
# 31-day auto-login is the intended final behaviour.
REMEMBER_LOGIN_ENV = 'AURA_TILL_REMEMBER_LOGIN'
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


# Proof-only switch (INSTALL-1): lets a real installed exe be driven through a
# FAILED first boot after an update without having to ship a broken build.
# Honoured ONLY when the explicit dev flag is also set, so neither variable
# alone -- and in particular nothing a customer's environment is likely to
# contain -- can make a till fail to start.
TEST_HOOKS_ENV = 'AURA_DEV_TEST_HOOKS'
FAIL_BOOT_ENV = 'AURA_TEST_FAIL_BOOT'


def _test_fail_boot_requested(environ=None) -> bool:
    environ = os.environ if environ is None else environ
    return environ.get(TEST_HOOKS_ENV) == '1' and environ.get(FAIL_BOOT_ENV) == '1'


def _run_server(sock):
    if _test_fail_boot_requested():
        raise RuntimeError(f'{FAIL_BOOT_ENV}: simulated failed boot (proof hook)')
    import app as retail_app
    retail_app.init_app()
    from waitress import serve as _serve
    log.info(f'Starting server on http://{BIND_HOST}:{sock.getsockname()[1]}')
    _serve(retail_app.app, sockets=[sock], threads=12,
           channel_timeout=120, connection_limit=200, _quiet=True)


# ── Crash trail (INSTALL-1, review #5 item 5) ──────────────────────────────
# The frozen exe is windowed (console=False): sys.stderr is None, so a hard
# crash (native fault in WebView2/pythonnet/sqlite) and an exception that
# escapes main() both vanished without a trace -- startup.log only holds what
# was logged on purpose. Two sinks, both under the shop's own data folder:
#   * logs/faulthandler.log -- C-level tracebacks (access violations, aborts)
#     that no Python hook can catch;
#   * the crash-report store (commercial_runtime/crash_reports) -- uncaught
#     Python exceptions on the main thread / on a thread that died before
#     app.py installed its own hook, so a boot failure is a queued, scrubbed
#     report rather than a line in a log nobody reads.
_faulthandler_file = None        # kept open for the life of the process
_crash_trail_state = {}


def _install_crash_trail(*, app_data=None, store_factory=None, version_fn=None) -> dict:
    """Install faulthandler + sys.excepthook + threading.excepthook. Never
    raises: a crash reporter that stops the till starting is worse than none.
    Returns what it installed (used by tests)."""
    global _faulthandler_file
    import faulthandler
    app_data = app_data or _app_data
    installed = {'faulthandler_path': None, 'store': None}
    try:
        path = Path(app_data, 'logs', 'faulthandler.log')
        path.parent.mkdir(parents=True, exist_ok=True)
        _faulthandler_file = open(path, 'a', encoding='utf-8')
        faulthandler.enable(file=_faulthandler_file, all_threads=True)
        installed['faulthandler_path'] = str(path)
    except Exception as exc:
        log.warning(f'faulthandler not enabled: {exc}')
    try:
        if store_factory is None:
            from commercial_runtime.crash_reports.store import open_default_store as store_factory
        store = store_factory(app_data)
    except Exception as exc:
        log.warning(f'crash store unavailable; uncaught errors will only reach the log: {exc}')
        store = None
    installed['store'] = store
    version_fn = version_fn or _running_version

    def _record(exc, kind):
        if store is None or exc is None:
            return
        try:
            from commercial_runtime.crash_reports import capture
            store.record(capture.report_from_exception(
                exc, kind=kind, route=None, app_version=version_fn() or 'unknown', platform='WINDOWS'))
        except Exception as inner:
            log.warning(f'could not record a crash: {inner}')

    previous_sys_hook = sys.excepthook

    def _sys_hook(exc_type, exc, tb):
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            log.error('Uncaught exception in the launcher', exc_info=(exc_type, exc, tb))
            _record(exc, 'launcher')
        previous_sys_hook(exc_type, exc, tb)

    previous_thread_hook = threading.excepthook

    def _thread_hook(args):
        # If something was chained ON TOP of this hook after we installed it
        # (app.py's crash capture does exactly that when it is imported), it
        # has already recorded this crash before calling us: recording here too
        # would count one crash twice. We only record when we are the outermost
        # hook -- i.e. the thread died before the app's own capture existed
        # (an import-time failure, the one the rollback offer exists for).
        try:
            if threading.excepthook is _thread_hook and args.exc_type is not SystemExit \
                    and args.exc_value is not None:
                _record(args.exc_value, 'thread')
        finally:
            previous_thread_hook(args)

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook
    installed['sys_hook'] = _sys_hook
    installed['thread_hook'] = _thread_hook
    _crash_trail_state.update(installed)
    return installed


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


def _clear_edge_fallback_cookies(profile_dir: str) -> None:
    """Item W2 (Phase 0 review round 3 finding). `_open_app_window` below
    launches Edge/Chrome with a PERSISTENT `--user-data-dir` -- unlike the
    native WebView2 path (item D1/W1 above), nothing has ever cleared ITS
    cookie jar, so a till that ends up on this fallback (WebView2 missing or
    unavailable) stays signed in as the last cashier for the full 31-day
    Flask session lifetime, across every relaunch, indefinitely -- D1's
    'session safety default' held for only one of the launcher's two UI
    paths.

    Chromium (Edge included, since Chromium-based) keeps its cookie jar at
    `<user-data-dir>/Default/Network/Cookies`, since the cookie store moved
    into the Network Service process; `-journal` is SQLite's write-ahead-log
    sidecar for that same file and must go with it, or a stale journal can
    replay old rows back into a fresh database on next open. Best-effort and
    silent on a profile that was never launched before (first run has
    neither file) -- `missing_ok=True` makes that the common case, not a
    logged warning.
    """
    cookies_dir = Path(profile_dir) / 'Default' / 'Network'
    for name in ('Cookies', 'Cookies-journal'):
        try:
            (cookies_dir / name).unlink(missing_ok=True)
        except Exception as exc:
            log.warning(f'Could not clear Edge/Chrome fallback cookie file {name}: {exc}')


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
    # Item W2 (Phase 0 review round 3): mirrors D1/W1's REMEMBER_LOGIN_ENV
    # opt-out on the native path -- the same explicit escape hatch for a
    # shop that wants this fallback to stay signed in too.
    if os.environ.get(REMEMBER_LOGIN_ENV) != '1':
        _clear_edge_fallback_cookies(profile)
    try:
        return subprocess.Popen([
            exe, f'--app={url}', f'--user-data-dir={profile}',
            # --start-maximized, NOT --start-fullscreen/--kiosk (owner
            # 2026-10-07: "needs to be fullscreen not just windowed"): a
            # kiosk window has no title bar and the page could not offer a
            # way back out here; the shell's own full-screen button uses the
            # browser Fullscreen API, which Esc/the button can always leave.
            '--window-size=1400,900', '--start-maximized',
            '--no-first-run', '--no-default-browser-check',
            # No resident background owner of this profile after the window
            # closes: _wait_for_app_window relies on the browser process
            # ending with the window.
            '--disable-background-mode',
        ])
    except Exception as exc:
        log.warning(f'Could not open app window via {exe}: {exc}')
        return None


def _wait_for_app_window(proc, server_alive, poll_seconds=1.0, sleep=time.sleep) -> str:
    """Block while the fallback window is open; return when the person closes
    it (INSTALL-1, review #8 item 5).

    This loop used to be `while server_thread.is_alive(): sleep(1)` -- the
    server thread never ends by itself, so closing the Edge/Chrome window left
    AuraRetail.exe running with no window, holding the single-instance mutex;
    the next double-click then said "already running" and showed nothing.

    `proc` is the browser we started with OUR OWN --user-data-dir (see
    _open_app_window): a profile directory nothing else uses means the browser
    does not hand the window to an already-running instance and exit, so this
    process lives exactly as long as the window and is waitable. (If a stray
    Edge ever does own that profile the launch returns at once and we would
    quit with the window still open -- a known limit, mitigated by
    --disable-background-mode so a closed window leaves no resident owner.)

    `proc is None` is the default-browser path: there is no process to watch,
    so it keeps the old behaviour of running until the server stops.
    Returns 'window_closed' | 'server_stopped'."""
    while True:
        if not server_alive():
            return 'server_stopped'
        if proc is not None and proc.poll() is not None:
            return 'window_closed'
        sleep(poll_seconds)


def _window_prefs_path() -> Path:
    """Where the till window's own preferences live (per install, under
    AURA_APP_DATA -- never the exe's directory, which can be read-only)."""
    return Path(_app_data) / 'window.json'


def _load_fullscreen_pref() -> bool:
    """Whether the till window should open full screen. DEFAULT TRUE (owner
    2026-10-07: "it needs to be fullscreen not just windowed"): a missing
    file, an unreadable file or malformed JSON all mean "the owner never
    chose", so they get the default rather than an error that would stop
    the till from opening at all."""
    try:
        data = json.loads(_window_prefs_path().read_text(encoding='utf-8'))
        value = data.get('fullscreen') if isinstance(data, dict) else None
        return value if isinstance(value, bool) else True
    except FileNotFoundError:
        return True
    except Exception as exc:
        log.warning(f'Could not read window preference ({exc}); using full screen.')
        return True


def _save_fullscreen_pref(value: bool) -> None:
    """Remember the owner's last full-screen choice. Never raises: a
    read-only disk must not break the toggle the owner just pressed."""
    try:
        _window_prefs_path().write_text(
            json.dumps({'fullscreen': bool(value)}), encoding='utf-8')
    except Exception as exc:
        log.warning(f'Could not save window preference: {exc}')


def _message_box(title, message):
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)


def _ask_yes_no(title, message) -> bool:
    """Native Yes/No box (MB_YESNO | MB_ICONWARNING | MB_TOPMOST). Unlike
    _fatal's bounded dialog this WAITS for the person: it is only ever shown
    when a human is at an installed till whose app just failed to start, and
    the answer decides what happens to their shop's data."""
    import ctypes
    IDYES = 6
    return ctypes.windll.user32.MessageBoxW(0, message, title, 0x04 | 0x30 | 0x1000) == IDYES


def _running_version():
    try:
        import config as _config
        return _config.APP_VERSION
    except Exception as exc:
        log.warning(f'Could not read the app version for the rollback check: {exc}')
        return None


def _backup_dir() -> str:
    return os.environ.get('AURA_BACKUP_DIR') or str(Path(_app_data) / 'backups')


def _offer_rollback(*, installable=None, version=None, ask=None, restore=None, launch=None,
                    show_error=None) -> str:
    """The first boot after an update failed: offer the way back (INSTALL-1).

    Returns 'not_offered' | 'declined' | 'rolled_back' | 'failed'. It is only
    ever offered when (a) this is an installed build (a dev run must never
    start an installer), (b) the failed boot is the first one after an update
    -- a healthy post-update boot writes boot_ok and never reaches here -- and
    (c) everything needed to go back is on disk (rollback.plan_rollback). Each
    outcome is logged: 'never offered' and 'offered and declined' must not look
    the same in startup.log."""
    from commercial_runtime.updates import rollback
    installable = getattr(sys, 'frozen', False) if installable is None else installable
    if not installable:
        log.info('Rollback not offered: not an installed build.')
        return 'not_offered'
    version = version or _running_version()
    if not version:
        return 'not_offered'
    plan = rollback.plan_rollback(_app_data, version, _backup_dir())
    if plan is None:
        log.info('Rollback not offered: this boot is not a failed first boot after an update, '
                 'or the way back is not on disk.')
        return 'not_offered'
    ask = ask or _ask_yes_no
    show_error = show_error or (lambda title, msg: _message_box(title, msg))
    log.warning(f'Rollback OFFERED: {plan.to_version} failed to start; could go back to {plan.from_version}.')
    answer = ask(
        APP_NAME,
        f'{APP_NAME} could not start after its last update (to version {plan.to_version}).\n\n'
        f'Do you want to go back to the previous version ({plan.from_version})?\n\n'
        'Your shop data from just before the update will be put back. '
        'Anything entered since the update is set aside, not deleted.')
    if not answer:
        log.info('Rollback declined by the person at the till.')
        return 'declined'
    if restore is None:
        from commercial_runtime.backup.service import restore_backup as restore
    if launch is None:
        from commercial_runtime.updates.service import default_installer_launcher as launch
    try:
        rollback.execute_rollback(plan, app_data_dir=_app_data, backup_dir=_backup_dir(),
                                  product_code='retail', launch=launch, restore=restore)
    except Exception as exc:
        log.error(f'Rollback FAILED: {exc}', exc_info=True)
        show_error(APP_NAME, 'Going back to the previous version did not work. '
                             'Nothing was changed. Please contact Action Aura support.')
        return 'failed'
    log.info('Rollback started: the previous version installer is running.')
    return 'rolled_back'


def _mark_healthy_boot() -> None:
    """The server answered and is the right product: the first boot after an
    update (if this is one) is over, so "Go back" is never offered for it."""
    version = _running_version()
    if version:
        from commercial_runtime.updates import rollback
        rollback.mark_boot_ok(_app_data, version)


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


class CustomerDisplayAPI:
    """js_api exposed to the till's own window as `window.pywebview.api`.

    Backs cross-crew interface I-1's `open_customer_display()`. Before this
    class existed, create_window() was never given a js_api=, so
    `window.pywebview.api` never existed and the till's own
    _openCustomerDisplay() (subsystem-retail.js:1244-1310) always fell
    through to its `window.open(url)` branch. pywebview hands window.open
    calls to the SYSTEM BROWSER (webview/platforms/edgechromium.py:255-261),
    a different program with no session cookie -- so the display's own API
    calls came back 401. This class opens a real second NATIVE window
    instead, which (once item #10's private_mode=False is in effect) shares
    this process's WebView2 profile and therefore the till's own session.

    Item #10 is what makes that shared session possible; this class is what
    actually opens the second window. Moving sale state across to it (I-1
    (b): the /customer-display/state relay) and the till's status dot (I-1
    (d)) are C2/C3c/C6's items, not this file's.

    Phase 0 review round 2 (C7): the first version of this class detected a
    closed display window by catching an exception from Window.restore().
    Real pywebview 6.2.1 never raises there -- see open_customer_display()'s
    comment below -- so that branch was dead code and every call after the
    operator closed the display returned True with nothing on screen for
    the rest of the process's life. close_display() is also new: it is
    wired to the TILL window's own closed event in _run_native_window, and
    fixes the mirror-image bug (the process surviving as an orphan when the
    till closes while the display is still open).

    Item D3 (Phase 0 review round 2): the native window's title used to be
    f'{APP_NAME} - Customer Display' -- literally 'Aura Retail - Customer
    Display' -- which puts Action Aura's own vendor name on a screen the
    SHOP's customer looks at, not the shop's own identity. The title is now
    neutral ('Customer Display') or, when the caller passes shop_name (the
    shop's own branding/signup name -- see item #12's read-time fallback in
    retail_api.py), '{shop_name} — Customer Display', matching the exact
    wording and em-dash separator customer-display.js:159 already uses for
    that page's own document.title -- so the OS taskbar/alt-tab text agrees
    with what the in-page title already says once the page itself loads.
    """

    def __init__(self, base_url: str):
        self._base_url = base_url
        self._window = None
        # The TILL window and its full-screen state, set by
        # _run_native_window after create_window(). The leading underscore
        # is REQUIRED: pywebview walks the PUBLIC attributes of a js_api
        # object to expose them to JS, and walking a Window object there
        # recurses / hangs. Private attributes are skipped.
        self._till_window = None
        self._is_fullscreen = True

    # ---- till window controls (owner 2026-10-07) -------------------------
    # Public methods here become window.pywebview.api.* in the shell. Each
    # one returns a dict and NEVER raises into JS: a failed window call
    # must show as "nothing happened", not as a broken page.

    def window_mode(self):
        """Tell the shell it is inside the native app and whether it is
        currently full screen (the shell uses this to draw its button)."""
        return {"native": True, "fullscreen": bool(self._is_fullscreen)}

    def toggle_fullscreen(self):
        if self._till_window is None:
            return {"ok": False}
        try:
            self._till_window.toggle_fullscreen()
            self._is_fullscreen = not self._is_fullscreen
            _save_fullscreen_pref(self._is_fullscreen)
            return {"fullscreen": self._is_fullscreen}
        except Exception as exc:
            log.warning(f'toggle_fullscreen failed: {exc}')
            return {"ok": False, "error": str(exc)}

    def minimize_app(self):
        """In full screen there is no title bar, so the shell offers
        Minimise itself."""
        if self._till_window is None:
            return {"ok": False}
        try:
            self._till_window.minimize()
            return {"ok": True}
        except Exception as exc:
            log.warning(f'minimize_app failed: {exc}')
            return {"ok": False, "error": str(exc)}

    def quit_app(self):
        """Close the till. The till window's events.closed is wired to
        close_display(), so the customer display goes too and the process
        ends once no windows remain."""
        if self._till_window is None:
            return {"ok": False}
        try:
            self._till_window.destroy()
            return {"ok": True}
        except Exception as exc:
            log.warning(f'quit_app failed: {exc}')
            return {"ok": False, "error": str(exc)}

    def _on_display_closed(self):
        """Subscribed to the display window's own events.closed the moment
        it is created below. This is the PRIMARY way self._window is kept
        in sync with reality: it fires the instant the operator closes the
        display (or close_display() destroys it), independent of whether
        open_customer_display() is ever called again. Without it,
        close_display() could be left holding a reference to a Window
        whose native handle is already gone."""
        self._window = None

    def open_customer_display(self, title: str | None = None, shop_name: str | None = None):
        """Called from JS as window.pywebview.api.open_customer_display().

        Item W3 (Phase 0 review round 3 finding): D3 (round 2) fixed the
        vendor-name half of the native title but left it permanently
        English -- the till calls this with no arguments at all, so an
        Arabic shop's taskbar/title bar always read 'Customer Display',
        never its translation. `title` is the new, preferred parameter: a
        caller (the till, which owns t() and can pass an already-translated
        string, e.g. t('Customer Display')) that supplies one gets it used
        VERBATIM -- this file has no i18n mechanism of its own and must not
        invent one. `shop_name` (existing, round-2 behaviour) is the
        fallback when no ready-made title is given, and the neutral English
        'Customer Display' is the last resort, unchanged from round 2, so
        today's zero-argument caller keeps its exact current behaviour."""
        title = (title or '').strip() or None
        import webview
        try:
            # Real pywebview 6.2.1's restore() on a window the operator has
            # already closed is a SILENT NO-OP, never an exception:
            # winforms.py's restore() (and destroy()/hide()/etc) resolve the
            # uid via `BrowserView.instances.get(uid)`, get None, and simply
            # return (winforms.py:389-395 deletes the closed window from
            # `instances` in on_close; :1033-1036 is restore()'s own lookup).
            # An earlier version of this method relied on restore() RAISING
            # to detect a dead window -- a real closed pywebview Window
            # never does that, so restore() would silently "succeed", this
            # method would unconditionally `return True`, and NO window
            # would ever open again for the life of the process while the
            # till was told every call succeeded (Phase 0 review round 2
            # finding). `self._window in webview.windows` is the real
            # signal pywebview itself maintains -- on_close removes a
            # closed window from that exact module-level list
            # (winforms.py:392-393) -- and is the backstop for any race
            # that gets ahead of _on_display_closed above, which is the
            # primary path.
            if self._window is not None and self._window in webview.windows:
                # Already open -- bring the existing one back instead of
                # spawning a duplicate window on every till click.
                self._window.restore()
                return True
            self._window = None
            # webview.screens is a pywebview @module_property (see the
            # installed 6.2.1 source, webview/__init__.py:512-521) -- a
            # plain list read WITHOUT calling it. `webview.screens()[1]`
            # would raise TypeError; `webview.screens[1]` is correct.
            screens = webview.screens
            second_screen = screens[1] if len(screens) > 1 else None
            # Item D3: neutral title, never the vendor name. Item W3: a
            # caller-supplied `title` (already resolved/translated by the
            # caller) wins outright; otherwise fall back to shop_name plus
            # the same wording/em-dash convention customer-display.js:159
            # already uses for that page's own document.title; 'Customer
            # Display' is the final, neutral default.
            resolved_title = title or (
                f'{shop_name} — Customer Display' if shop_name else 'Customer Display'
            )
            window = webview.create_window(
                resolved_title,
                f'{self._base_url}/static/customer-display.html',
                screen=second_screen,
                # Fullscreen only when a dedicated second monitor exists --
                # fullscreening on the till's own single monitor would just
                # bury the till itself.
                fullscreen=second_screen is not None,
                width=1024,
                height=768,
            )
            window.events.closed += self._on_display_closed
            self._window = window
            return True
        except Exception as exc:
            log.warning(f'Could not open customer display window: {exc}')
            return False

    def close_display(self):
        """Wired to the TILL window's own events.closed in
        _run_native_window (item #11(a) orphan-process fix). pywebview
        (winforms) only exits the process once EVERY window it owns has
        closed -- on_close checks `len(BrowserView.instances) == 0`
        (winforms.py:397) before invoking WinForms.Application.Exit(). So
        if the operator closes the till while the customer display is
        still open, the process -- and the backend thread it hosts --
        survives as an orphan with only the display on screen, and a
        relaunch then hits the single-instance mutex ("Aura Retail is
        already running") with no till window to find (Phase 0 review
        round 2 finding). Destroying the display here as soon as the till
        closes means the till closing is always enough on its own to reach
        zero instances. Swallows errors: this runs during teardown and
        must never prevent the till itself from closing."""
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass


# Item W1 (Phase 0 review round 3). How many times `_clear_cookies_then_load`
# calls `window.clear_cookies()` before giving up and failing closed: one
# normal attempt, one retry.
_COOKIE_CLEAR_ATTEMPTS = 2

# Item W1 (Phase 0 review round 3). Shown via `window.load_html()` -- never
# `load_url()` -- when clear_cookies() could not be confirmed to have run
# after a retry, so the till is never left facing the live app with a
# session cookie that might still belong to the previous cashier. Deliberately
# does not attempt to reach the app's own `/api/auth/logout` route
# (commercial_runtime/identity/auth_routes.py:58-61, `session.clear()`):
# that route is POST-only, so a plain navigation (a GET) would 405 rather
# than log anyone out, and driving a real POST from this blank, not-yet-
# loaded page would mean a JS `fetch()` racing the same WebView2 core that
# just failed to confirm it was even ready -- no more trustworthy than the
# thing that failed. Refusing to load the app at all is the guaranteed-safe
# version of "do not show a live session".
#
# Hardcoded bilingual (EN then AR), not run through t(): this Python file has
# no bridge into the frontend's JS translation catalogue (the same reason
# D3's shop_name-only native title above stays English -- see that item's
# round-2 review note), and even if it did, nothing here can read the
# operator's saved language preference before the app itself has loaded,
# which is exactly the thing this page exists to refuse to do. The Arabic
# translation is also recorded in i18n-R2-desktop.json per the crew contract,
# even though no t() call site will ever consume it from this file.
_SESSION_SAFETY_BLOCKED_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Aura Retail</title>
<style>
  body { margin:0; height:100vh; display:flex; align-items:center;
         justify-content:center; background:#16233D; color:#F6F4EF;
         font-family:sans-serif; text-align:center; }
  div { max-width:480px; padding:24px; }
  p[dir="rtl"] { font-family:"Segoe UI",Tahoma,sans-serif; }
  hr { border:none; border-top:1px solid rgba(246,244,239,0.25); margin:16px 0; }
</style></head>
<body><div>
  <h1>Aura Retail could not start safely</h1>
  <p>The app window could not confirm the previous session was cleared, so it
  will not open with a possibly signed-in session.</p>
  <p>Please close this window and relaunch Aura Retail.</p>
  <hr>
  <p dir="rtl">تعذر تشغيل Aura Retail بأمان. تعذر التأكد من مسح الجلسة
  السابقة، لذلك لن يتم فتح التطبيق مع جلسة قد تكون لا تزال مسجّلة الدخول.
  يرجى إغلاق هذه النافذة وإعادة تشغيل Aura Retail.</p>
</div></body></html>"""


def _clear_cookies_then_load(window, app_url: str) -> None:
    """`webview.start()`'s `func=` callback (item D1, Phase 0 review round 2;
    made fail-CLOSED by item W1, Phase 0 review round 3).

    Real pywebview starts this on its OWN background thread the instant
    `webview.start()` is called, before the native window has even been
    created (.venv/Lib/site-packages/webview/__init__.py:294-301) -- it does
    NOT wait for anything first. That is safe here because `load_url` and
    `load_html` are both decorated `@_shown_call`, and `clear_cookies` is
    `@_loaded_call`, in the installed source (webview/window.py:269-289
    `load_url`, :290-302 `load_html`, :321-326 `clear_cookies`) -- each
    blocks on the matching `Window` event before running, so this function
    naturally waits for its own turn instead of racing the GUI thread that
    is about to create the window.

    `_run_native_window` creates the till window on a BLANK page (no
    `url=`) specifically so this is the very first thing that ever happens
    in this WebView2 profile on a given launch: nothing has had a chance to
    read whatever session cookie is already sitting in the profile before
    this clears it.

    Item W1 (Phase 0 review round 3 finding): the previous version caught
    every exception from `clear_cookies()` and called `load_url(app_url)`
    regardless -- the safety default FAILED OPEN. `window.clear_cookies()`
    is `@_loaded_call`, and `_api_call` (window.py:32-50) waits up to 20s on
    the 'loaded' event -- WebView2's own core becoming ready -- before
    raising `WebViewException('Main window failed to start')` when a cold
    start is unusually slow. On that path the old code loaded the app URL
    anyway, into a profile that might still hold the PREVIOUS cashier's
    session cookie -- exactly the 31-day leak item #10/D1 exists to close,
    reopened by its own error path. Now: retry `clear_cookies()` once (a
    slow-but-not-stuck WebView2 core often finishes initialising within the
    same startup window), and if it STILL raises, refuse to load the live
    app at all -- `load_html()` shows a neutral blocked page instead.
    `load_html()`, like `load_url()`, is `@_shown_call`, not `@_loaded_call`,
    so it waits on the EARLIER 'shown' event (the native window itself,
    already visible by this point in a real launch) rather than the
    still-unconfirmed 'loaded'/WebView2-core-ready event, so showing it does
    not itself risk another 20s hang.

    Item W1 (Phase 0 review round 3, SECOND finding on this same function --
    the retry/fail-closed logic above was correct, but calling
    `window.load_html()` alone was not enough to make it STICK). This is
    exactly the case `clear_cookies()` timing out normally means: CoreWebView2
    itself is not ready yet. On that exact state, real
    `Window.load_html()` (webview/window.py:290-302) never touches
    `window.html` -- it only forwards to the GUI layer -- and WebView2's own
    GUI layer (edgechromium.py:209-216) checks `self.webview.CoreWebView2`;
    when that is still falsy it stores the HTML on `self.html`, AN ATTRIBUTE
    OF THE EdgeChrome WRAPPER OBJECT, NOT of the pywebview `Window` model,
    calls `EnsureCoreWebView2Async(None)`, and returns without navigating.
    When the core eventually does finish initialising, `on_webview_ready`
    (edgechromium.py:305-311) decides what to show by reading
    `self.pywebview_window.real_url`, then `self.pywebview_window.html` --
    the WINDOW MODEL's own `html` attribute, which the `load_html()` call
    above never set -- and falls back to pywebview's own blank
    `DEFAULT_HTML` when both are empty. So the call below alone left the
    till showing a permanently blank window once WebView2 finally came up:
    the "blocked page" only ever reached a GUI-internal attribute
    `on_webview_ready` does not read. Setting `window.html` (the Window
    model attribute) directly, in addition to calling `load_html()`, closes
    that gap: `on_webview_ready`'s `elif self.pywebview_window.html:` branch
    then finds the blocked page and loads it itself once the core is ready,
    with `real_url` staying `None` because `load_url()` was never called on
    this path. Order does not matter for correctness here (both are set
    before the native window can possibly act on either), but `window.html`
    is set first so a reader sees the state that `on_webview_ready` will
    actually consult before the call that merely mirrors it into the
    session's live GUI, in case one is ever read without the other.
    """
    for attempt in range(1, _COOKIE_CLEAR_ATTEMPTS + 1):
        try:
            window.clear_cookies()
            window.load_url(app_url)
            return
        except Exception as exc:
            log.warning(f'clear_cookies() attempt {attempt}/{_COOKIE_CLEAR_ATTEMPTS} '
                        f'failed: {exc}')
    log.error('Could not confirm WebView2 cookies were cleared after retrying -- '
              'refusing to load the app with a possibly-live session (fail closed).')
    # Item W1: must be set BEFORE load_html() -- see the docstring above.
    # `load_html()` alone stores the HTML on the EdgeChrome GUI wrapper's own
    # `self.html`, which `on_webview_ready` never reads; `window.html` is the
    # separate Window-model attribute it actually falls back to once
    # CoreWebView2 finishes initialising.
    window.html = _SESSION_SAFETY_BLOCKED_HTML
    window.load_html(_SESSION_SAFETY_BLOCKED_HTML)


def _run_native_window(url: str) -> bool:
    try:
        import webview
        log.info('Opening native application window (pywebview / WebView2).')
        display_api = CustomerDisplayAPI(url)

        # Item D1 (Phase 0 review round 2) -- SESSION SAFETY DEFAULT.
        # private_mode=False below (item #10) makes WebView2 keep a real,
        # persisted profile instead of wiping it every relaunch -- that is
        # correct for localStorage (theme, aura_printer_cfg), but it also
        # means the session COOKIE now survives too, and mt_auth.py's
        # session.permanent=True gives that cookie Flask's default 31-day
        # lifetime with no "remember me" opt-in. A security review of this
        # same diff flagged that combination: a lost, stolen or briefly
        # unattended till would stay signed in as the last cashier for up to
        # a month. The owner has not decided the till's session lifetime
        # (that needs a real PERMANENT_SESSION_LIFETIME + idle-timeout
        # design in commercial_runtime/identity/mt_auth.py and/or
        # products/retail/backend/app.py, outside this file's ownership) --
        # so THIS IS THE CONSERVATIVE DEFAULT PENDING THAT DECISION, not the
        # final answer: clear the WebView2 profile's cookies at the start of
        # every launch, before the till's own page (and the session cookie
        # it would read) ever loads, via a BLANK-page create_window() +
        # window.clear_cookies() + window.load_url() sequence
        # (_clear_cookies_then_load, below). private_mode=False/storage_path
        # still keep localStorage/theme/printer settings exactly as item #10
        # intended; only the cookie is force-cleared. REMEMBER_LOGIN_ENV
        # (AURA_TILL_REMEMBER_LOGIN=1) is an explicit escape hatch for a shop
        # that wants the till to stay signed in anyway, once that is a real
        # decision rather than an accidental side effect.
        remember_login = os.environ.get(REMEMBER_LOGIN_ENV) == '1'
        # Owner 2026-10-07: the till opens full screen (maximized=True is
        # the fallback look when the owner has left full screen; 1400x900
        # stays the restore size). The customer-display window is untouched.
        start_fullscreen = _load_fullscreen_pref()
        if remember_login:
            log.info(f'{REMEMBER_LOGIN_ENV}=1 -- skipping the launch cookie '
                      'clear; the till will stay signed in across a relaunch.')
            till_window = webview.create_window(APP_NAME, url, width=1400, height=900,
                                                 fullscreen=start_fullscreen,
                                                 maximized=True,
                                                 text_select=True, zoomable=True,
                                                 js_api=display_api)
            start_kwargs = {}
        else:
            till_window = webview.create_window(APP_NAME, width=1400, height=900,
                                                 fullscreen=start_fullscreen,
                                                 maximized=True,
                                                 text_select=True, zoomable=True,
                                                 js_api=display_api)
            start_kwargs = {'func': _clear_cookies_then_load, 'args': (till_window, url)}
        display_api._till_window = till_window
        display_api._is_fullscreen = start_fullscreen
        # Item #11(a) orphan-process fix (Phase 0 review round 2): without
        # this, closing the till while the customer display is still open
        # leaves the process (and the backend it hosts) running with only
        # the display on screen -- see close_display()'s docstring for the
        # exact pywebview mechanism. Wiring the till's own closed event to
        # close its companion window means the till closing is always
        # enough by itself.
        till_window.events.closed += display_api.close_display
        # Item #10 (Phase 0): webview.start() used to be called with no
        # arguments at all, and pywebview 6.2.1 defaults BOTH of these to
        # values that silently destroy persistence -- private_mode defaults
        # True (a throwaway WebView2 profile: localStorage, including
        # aura_printer_cfg and the chosen theme, plus the session cookie,
        # are wiped on every relaunch) and storage_path defaults None (no
        # persistent profile even if private_mode were off). Pass both
        # explicitly. storage_path lives under AURA_APP_DATA, never the
        # exe's own install directory, which can be read-only and is
        # replaced wholesale on upgrade.
        #
        # Do NOT set webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] =
        # False here -- it must stay at its True default. That setting is
        # what sends a genuine external link (e.g. a help URL) to the
        # system browser instead of hijacking this window. The customer
        # display is deliberately opened as its OWN native window via
        # CustomerDisplayAPI.open_customer_display() (item #11a) above,
        # never by loading it into the till's own window.
        #
        # SECURITY TRADE-OFF, recorded rather than left as a silent side
        # effect (Phase 0 review round 2 finding on this same line, ADDRESSED
        # by item D1 above): turning private_mode off does not just persist
        # localStorage/theme/printer config (the reason this line exists) --
        # on its own it would also let the session cookie survive a relaunch
        # and a full machine reboot, because commercial_runtime/identity/
        # mt_auth.py unconditionally sets session.permanent = True with no
        # "remember me" opt-in and products/retail/backend/app.py sets no
        # PERMANENT_SESSION_LIFETIME, so that cookie carries Flask's default
        # 31-DAY lifetime. D1's launch-time cookie clear (the branch above)
        # is what keeps that from taking effect by default: this call itself
        # is unchanged from item #10 (still needed for localStorage/theme/
        # printer persistence), and start_kwargs carries D1's func=/args=
        # cookie-clear callback unless AURA_TILL_REMEMBER_LOGIN=1 opted out
        # of it. A real bounded session lifetime (Owner's app.py pattern is
        # PERMANENT_SESSION_LIFETIME_SECONDS=28800 (8h) + a 30-minute idle
        # timeout) is still the better long-term fix and still needs the
        # owner's decision plus a security reviewer's sign-off -- that change
        # belongs in products/retail/backend/app.py and/or mt_auth.py,
        # outside this file's ownership -- but D1 means an unattended till no
        # longer stays signed in for a month by accident in the meantime.
        webview.start(private_mode=False,
                      storage_path=str(Path(_app_data) / 'webview'),
                      **start_kwargs)
        log.info('Native application window closed.')
        return True
    except Exception as exc:
        log.warning(f'Native window unavailable ({exc}); will try Edge --app.')
        return False


def main():
    _install_crash_trail()
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
        if _offer_rollback() == 'rolled_back':
            sys.exit(0)           # the installer is closing and replacing this build
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
        if _offer_rollback() == 'rolled_back':
            sys.exit(0)
        _fatal(f'Port {port} answered as "{product_code}", not Aura Retail -- another Aura '
               'product is using this port. Close all Aura applications and try again.',
               reason_code='WRONG_PRODUCT_ON_PORT')
        return

    # READY reached -- this is the one and only place readiness is declared.
    # No timer, thread, or callback anywhere in this module can still fire a
    # startup-timeout failure after this point; check_readiness() has
    # already returned, and nothing below re-invokes it.
    _set_state(LauncherState.READY)
    _mark_healthy_boot()
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
            outcome = _wait_for_app_window(proc, server_thread.is_alive)
            log.info(f'Edge/Chrome fallback wait ended: {outcome}')
        except KeyboardInterrupt:
            pass

    _set_state(LauncherState.STOPPING)
    log.info('Application closed.')
    _set_state(LauncherState.STOPPED)


# ── Restart supervisor (INSTALL-1, review F16) ─────────────────────────────
# WHY A SECOND PROCESS: the Flask server runs on a thread INSIDE this process,
# so "the backend exits with code 75" can only be acted on by something that
# outlives it. The frozen exe therefore runs itself twice: the outer copy is a
# tiny supervisor (no window, no server, no mutex) that runs the real launcher
# as a child and looks at the child's exit code; the child is the app exactly as
# before. A restore ends with the child exiting 75 (commercial_runtime/
# install_support/restart.py), and the supervisor starts a fresh child -- new
# server process, new window -- instead of quitting.
#
#   0  -> the person closed the app: quit.
#   75 -> restart requested after a restore: relaunch.
#   anything else -> the child crashed/failed: pass its code through and quit,
#   exactly what the single-process launcher did (no crash loops, ever).
#
# Not supervised: a plain `python launcher_retail.py` (dev run) -- the child
# env marker and the frozen check below keep the old single-process behaviour.
SUPERVISE_ENV = 'AURA_LAUNCHER_SUPERVISE'   # "1" forces supervision from source (tests/proof)
CHILD_ENV = 'AURA_LAUNCHER_CHILD'           # set on the child so it never supervises itself
RESTART_EXIT_CODE = 75                      # == install_support.restart.RESTART_EXIT_CODE
# A child that keeps asking for a restart (a bug, not a restore) must not spin
# forever: more than this many relaunches inside the window and we stop.
MAX_RELAUNCHES = 3
RELAUNCH_WINDOW_SECONDS = 120.0


def exit_action(code: int) -> str:
    """What the supervisor does with a child's exit code. 'relaunch' | 'quit'
    | 'crash' (quit, passing the failing code through)."""
    if code == RESTART_EXIT_CODE:
        return 'relaunch'
    if code == 0:
        return 'quit'
    return 'crash'


def should_supervise(environ=None, frozen=None) -> bool:
    environ = os.environ if environ is None else environ
    frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
    if environ.get(CHILD_ENV) == '1':
        return False
    return bool(frozen) or environ.get(SUPERVISE_ENV) == '1'


def supervise(run_child, clock=time.monotonic) -> int:
    """Runs `run_child()` (returns the child's exit code) until it says quit.
    Returns the code this process should exit with."""
    relaunches = []
    while True:
        code = run_child()
        action = exit_action(code)
        log.info(f'[supervisor] child exited with {code} -> {action}')
        if action != 'relaunch':
            return code
        now = clock()
        relaunches = [t for t in relaunches if now - t < RELAUNCH_WINDOW_SECONDS]
        if len(relaunches) >= MAX_RELAUNCHES:
            log.error('[supervisor] too many restarts in a row; giving up.')
            return code
        relaunches.append(now)


def _run_child_process() -> int:
    import subprocess
    env = dict(os.environ)
    env[CHILD_ENV] = '1'
    env['AURA_RESTART_ON_RESTORE'] = '1'
    if getattr(sys, 'frozen', False):
        cmd = [sys.executable, *sys.argv[1:]]
    else:
        cmd = [sys.executable, os.path.abspath(__file__), *sys.argv[1:]]
    return subprocess.call(cmd, env=env)


if __name__ == '__main__':
    if should_supervise():
        sys.exit(supervise(_run_child_process))
    main()
