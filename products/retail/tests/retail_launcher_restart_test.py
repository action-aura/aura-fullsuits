"""INSTALL-1 (F16): the launcher's restart supervisor.

The decision under test is tiny -- what to do with a child's exit code -- and
the failure it guards is silent in both directions: a supervisor that quits on
75 leaves the shopkeeper with a dead app after every restore; one that
relaunches on a CRASH code turns a startup failure into an endless loop of
error dialogs. So every branch is pinned, and `supervise` is driven with a fake
child that COUNTS its own runs (an outcome like "returned 0" cannot tell one
run from three).

Run (ONE FILE PER PROCESS):
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest products/retail/tests/retail_launcher_restart_test.py -q
"""
import sys
from pathlib import Path

import pytest

_DESKTOP_DIR = Path(__file__).resolve().parents[1] / 'desktop'


def _import_launcher(monkeypatch, app_data_dir):
    monkeypatch.setenv('AURA_APP_DATA', str(app_data_dir))
    monkeypatch.syspath_prepend(str(_DESKTOP_DIR))
    sys.modules.pop('launcher_retail', None)
    import launcher_retail  # noqa: E402
    monkeypatch.setattr(launcher_retail, '_app_data', str(app_data_dir))
    return launcher_retail


@pytest.fixture
def launcher(monkeypatch, tmp_path):
    d = tmp_path / 'AuraRetailTest'
    d.mkdir()
    return _import_launcher(monkeypatch, d)


def test_exit_code_75_is_relaunch_0_is_quit_anything_else_is_crash(launcher):
    assert launcher.exit_action(75) == 'relaunch'
    assert launcher.exit_action(0) == 'quit'
    for crash in (1, 2, 3, 74, 76, -1, 255):
        assert launcher.exit_action(crash) == 'crash', crash


def test_launcher_and_backend_agree_on_the_code(launcher):
    # Two constants in two files that must move together: pin them to each
    # other AND to the literal, so neither can drift alone.
    from commercial_runtime.install_support.restart import RESTART_EXIT_CODE
    assert launcher.RESTART_EXIT_CODE == RESTART_EXIT_CODE == 75


def _child(codes):
    runs = []
    it = iter(codes)

    def run():
        runs.append(1)
        return next(it)
    return run, runs


def test_supervise_relaunches_on_75_then_quits_on_0(launcher):
    run, runs = _child([75, 0])
    assert launcher.supervise(run) == 0
    assert len(runs) == 2                 # the child really ran a second time


def test_supervise_does_not_relaunch_a_crash(launcher):
    run, runs = _child([1])
    assert launcher.supervise(run) == 1
    assert len(runs) == 1                 # no second run after a failure


def test_supervise_quits_on_a_normal_close(launcher):
    run, runs = _child([0])
    assert launcher.supervise(run) == 0
    assert len(runs) == 1


def test_supervise_gives_up_on_a_restart_loop(launcher):
    run, runs = _child([75] * 50)
    assert launcher.supervise(run, clock=lambda: 0.0) == 75
    assert len(runs) == launcher.MAX_RELAUNCHES + 1


def test_restarts_spread_beyond_the_window_are_not_a_loop(launcher):
    ticks = iter(range(0, 10_000, 1000))      # each relaunch 1000s after the last
    run, runs = _child([75, 75, 75, 75, 75, 0])
    assert launcher.supervise(run, clock=lambda: next(ticks)) == 0
    assert len(runs) == 6


def test_should_supervise_only_frozen_or_forced_and_never_the_child(launcher):
    assert launcher.should_supervise({}, frozen=True) is True
    assert launcher.should_supervise({'AURA_LAUNCHER_SUPERVISE': '1'}, frozen=False) is True
    assert launcher.should_supervise({}, frozen=False) is False         # dev run
    assert launcher.should_supervise({'AURA_LAUNCHER_CHILD': '1'}, frozen=True) is False   # no recursion
