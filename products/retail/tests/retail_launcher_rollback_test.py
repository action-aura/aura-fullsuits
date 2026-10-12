"""INSTALL-1: the launcher offers "go back to the previous version" ONLY when
the first boot after an update fails.

main() is driven end to end with the server, readiness probe and window faked,
because the guarded behaviours live in how main() calls the helpers:
  * a healthy post-update boot must write boot_ok and must NEVER ask
    (the deny half -- asked is COUNTED, not inferred from the exit code);
  * a failed post-update boot must ask exactly once, and on "yes" exit without
    showing the fatal dialog (the installer is about to replace the build);
  * a failed boot that is NOT after an update (no update record) must not ask;
  * "no" must leave restore and installer untouched.

Run (ONE FILE PER PROCESS):
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest products/retail/tests/retail_launcher_rollback_test.py -q
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_DESKTOP_DIR = Path(__file__).resolve().parents[1] / 'desktop'
RUNNING = '1.0.0-rc.9'
OLD = '1.0.0-rc.8'


def _import_launcher(monkeypatch, app_data_dir):
    monkeypatch.setenv('AURA_APP_DATA', str(app_data_dir))
    monkeypatch.delenv('AURA_BACKUP_DIR', raising=False)
    monkeypatch.syspath_prepend(str(_DESKTOP_DIR))
    sys.modules.pop('launcher_retail', None)
    import launcher_retail  # noqa: E402
    monkeypatch.setattr(launcher_retail, '_app_data', str(app_data_dir))
    monkeypatch.setattr(launcher_retail, '_running_version', lambda: RUNNING)
    return launcher_retail


@pytest.fixture
def L(monkeypatch, tmp_path):
    d = tmp_path / 'AuraRetailTest'
    d.mkdir()
    return _import_launcher(monkeypatch, d)


def _arm_update(L, with_record=True):
    """A way back exists on disk: kept installer, backup, update record."""
    from commercial_runtime.updates import rollback
    app = Path(L._app_data)
    (app / 'backups').mkdir(exist_ok=True)
    (app / 'backups' / 'b-pre-update.aurabak.zip').write_bytes(b'zip')
    src = app / 'src.exe'
    src.write_bytes(b'OLD')
    rollback.preserve_previous(app, OLD, src)
    if with_record:
        (app / 'updates').mkdir(exist_ok=True)
        (app / 'updates' / 'state.json').write_text(json.dumps(
            {'last_update': {'from': OLD, 'to': RUNNING, 'backup': 'b-pre-update.aurabak.zip'}}))
    return app


class Spy:
    def __init__(self, answer=True):
        self.answer = answer
        self.asked = []
        self.calls = []

    def ask(self, title, message):
        self.asked.append(message)
        return self.answer

    def restore(self, product, app_data, path):
        self.calls.append('restore')
        return {}

    def launch(self, path, args):
        self.calls.append('launch')


def _offer(L, spy, **kw):
    return L._offer_rollback(installable=kw.pop('installable', True), version=RUNNING,
                             ask=spy.ask, restore=spy.restore, launch=spy.launch,
                             show_error=lambda t, m: None, **kw)


def test_offer_yes_restores_then_launches_the_installer(L):
    _arm_update(L)
    spy = Spy(True)
    assert _offer(L, spy) == 'rolled_back'
    assert len(spy.asked) == 1 and OLD in spy.asked[0]
    assert spy.calls == ['restore', 'launch']


def test_offer_no_touches_nothing(L):
    _arm_update(L)
    spy = Spy(False)
    assert _offer(L, spy) == 'declined'
    assert len(spy.asked) == 1 and spy.calls == []


def test_not_offered_without_an_update_record(L):
    _arm_update(L, with_record=False)
    spy = Spy()
    assert _offer(L, spy) == 'not_offered'
    assert spy.asked == [] and spy.calls == []


def test_not_offered_on_a_dev_run(L):
    _arm_update(L)
    spy = Spy()
    assert _offer(L, spy, installable=False) == 'not_offered'
    assert spy.asked == []


def test_a_restore_that_fails_reports_failed_and_runs_no_installer(L):
    _arm_update(L)
    spy = Spy(True)

    def bad_restore(*a):
        spy.calls.append('restore')
        raise OSError('in use')
    outcome = L._offer_rollback(installable=True, version=RUNNING, ask=spy.ask, restore=bad_restore,
                                launch=spy.launch, show_error=lambda t, m: None)
    assert outcome == 'failed' and spy.calls == ['restore']


# -- main() end to end with fakes -------------------------------------------------
def _drive_main(monkeypatch, L, ready, offer_result='not_offered'):
    offered = []
    fatal = []
    monkeypatch.setattr(L, '_acquire_single_instance', lambda: 'handle')
    monkeypatch.setattr(L, '_bind_free_socket',
                        lambda: SimpleNamespace(getsockname=lambda: ('127.0.0.1', 5999)))
    monkeypatch.setattr(L, '_run_server', lambda sock: None)
    monkeypatch.setattr(L, 'check_readiness', lambda *a, **k: SimpleNamespace(
        ready=ready, reason='process_exited', detail='died', attempts=1, elapsed=0.1))
    monkeypatch.setattr(L, '_run_native_window', lambda url: True)

    def fake_offer(**kw):
        offered.append(1)
        return offer_result
    monkeypatch.setattr(L, '_offer_rollback', fake_offer)

    def fake_fatal(message, reason_code):
        fatal.append(reason_code)
        raise SystemExit(1)
    monkeypatch.setattr(L, '_fatal', fake_fatal)

    import urllib.request

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"product_code": "AURA_RETAIL"}'
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: _Resp())
    return offered, fatal


def test_healthy_post_update_boot_marks_ok_and_never_offers(monkeypatch, L):
    _arm_update(L)
    offered, fatal = _drive_main(monkeypatch, L, ready=True)
    L.main()
    assert offered == [] and fatal == []                        # asked ZERO times
    marker = json.loads((Path(L._app_data) / 'updates' / 'boot_ok.json').read_text())
    assert marker == {'version': RUNNING}
    from commercial_runtime.updates import rollback
    assert rollback.plan_rollback(L._app_data, RUNNING, Path(L._app_data) / 'backups') is None


def test_failed_boot_offers_once_and_declining_falls_through_to_the_fatal_dialog(monkeypatch, L):
    _arm_update(L)
    offered, fatal = _drive_main(monkeypatch, L, ready=False, offer_result='declined')
    with pytest.raises(SystemExit) as exc:
        L.main()
    assert offered == [1] and fatal == ['SERVER_PROCESS_EXITED'] and exc.value.code == 1
    assert not (Path(L._app_data) / 'updates' / 'boot_ok.json').exists()


def test_failed_boot_with_a_started_rollback_exits_cleanly_without_the_fatal_dialog(monkeypatch, L):
    _arm_update(L)
    offered, fatal = _drive_main(monkeypatch, L, ready=False, offer_result='rolled_back')
    with pytest.raises(SystemExit) as exc:
        L.main()
    assert offered == [1] and fatal == [] and exc.value.code == 0


def test_the_fail_boot_proof_hook_needs_both_flags(L):
    assert L._test_fail_boot_requested({'AURA_TEST_FAIL_BOOT': '1'}) is False
    assert L._test_fail_boot_requested({'AURA_DEV_TEST_HOOKS': '1'}) is False
    assert L._test_fail_boot_requested({'AURA_DEV_TEST_HOOKS': '1', 'AURA_TEST_FAIL_BOOT': '1'}) is True


def test_run_server_raises_under_the_hook_before_touching_the_app(monkeypatch, L):
    monkeypatch.setenv('AURA_DEV_TEST_HOOKS', '1')
    monkeypatch.setenv('AURA_TEST_FAIL_BOOT', '1')
    with pytest.raises(RuntimeError, match='simulated failed boot'):
        L._run_server(object())
