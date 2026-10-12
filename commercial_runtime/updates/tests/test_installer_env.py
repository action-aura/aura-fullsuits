"""Verifier H1: the installer must NOT inherit the restart supervisor's markers.

The installer relaunches the new AuraRetail.exe with its own environment. An
exe that inherits AURA_LAUNCHER_CHILD / AURA_RESTART_ON_RESTORE runs
unsupervised with the 75-exit armed, so the first restore after an update makes
the till vanish. The Popen `env=` is captured (what the installer really
receives), not inferred from a launcher stub.
"""
import subprocess

from commercial_runtime.install_support import supervisor_env
from commercial_runtime.updates import service


class _PopenSpy:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))


def test_installer_gets_an_explicit_env_without_the_supervisor_markers(monkeypatch):
    spy = _PopenSpy()
    monkeypatch.setattr(subprocess, "Popen", spy)
    monkeypatch.setenv("AURA_LAUNCHER_CHILD", "4242")
    monkeypatch.setenv("AURA_RESTART_ON_RESTORE", "1")
    monkeypatch.setenv("AURA_APP_DATA", r"C:\shop")
    service.default_installer_launcher(r"C:\x\setup.exe", ["/VERYSILENT", "/RELAUNCH=1"])
    (argv, kw), = spy.calls
    assert argv == [r"C:\x\setup.exe", "/VERYSILENT", "/RELAUNCH=1"]
    env = kw["env"]
    assert env is not None, "no explicit env= means the child's env is inherited"
    for name in supervisor_env.SUPERVISOR_ENV_VARS:
        assert name not in env
    # allow-half: everything else the shop's launch depends on still arrives.
    assert env["AURA_APP_DATA"] == r"C:\shop"
    assert env.get("PATH") or env.get("Path")


def test_scrub_is_case_insensitive_and_does_not_touch_the_input():
    src = {"aura_launcher_child": "1", "AURA_RESTART_ON_RESTORE": "1", "KEEP": "y"}
    out = supervisor_env.scrubbed_env(src)
    assert out == {"KEEP": "y"}
    assert "aura_launcher_child" in src          # the caller's mapping is not mutated
