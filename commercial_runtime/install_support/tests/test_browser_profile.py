"""Verifier M1: "is our Edge/Chrome window still open?" decided from process
command lines. The lists below are fake; the real PowerShell enumeration is
exercised separately against live Edge (see the INSTALL-1 fix progress notes).
"""
import pytest

from commercial_runtime.install_support import browser_profile as bp

PROFILE = r"C:\Users\Shop\AppData\Local\AuraRetail\app_window"


def line(extra, profile=PROFILE, quote=False):
    arg = '--user-data-dir="%s"' % profile if quote else "--user-data-dir=%s" % profile
    return r'"C:\Program Files\Microsoft\Edge\Application\msedge.exe" %s %s' % (extra, arg)


@pytest.mark.parametrize("cmd", [
    line("--app=http://127.0.0.1:5001"),
    line("--type=renderer"),                                   # child processes carry the flag too
    line("--app=x", profile=PROFILE.lower()),                   # case-insensitive
    line("--app=x", profile=PROFILE.replace("\\", "/")),        # either slash
    line("--app=x", quote=True),                                # quoted value
    line("--app=x", profile=PROFILE + "\\"),                    # trailing backslash
])
def test_our_profile_is_recognised(cmd):
    assert bp.command_line_uses_profile(cmd, PROFILE) is True


@pytest.mark.parametrize("cmd", [
    line("--app=x", profile=PROFILE + "2"),                     # a sibling profile that merely starts the same
    line("--app=x", profile=r"C:\Users\Other\AppData\Local\AuraRetail\app_window"),
    r'"C:\Program Files\Microsoft\Edge\Application\msedge.exe" --profile-directory=Default',   # the person's own Edge
    "",
])
def test_other_browsers_and_other_profiles_are_not_ours(cmd):
    assert bp.command_line_uses_profile(cmd, PROFILE) is False


def test_profile_in_use_is_true_while_any_process_has_our_profile():
    lister_calls = []

    def lister():
        lister_calls.append(1)
        return [line("--profile-directory=Default", profile=r"C:\Other"), line("--type=gpu-process")]

    assert bp.profile_in_use(PROFILE, lister) is True
    assert lister_calls == [1]                                  # it really looked


def test_profile_in_use_is_false_when_only_other_browsers_run():
    assert bp.profile_in_use(PROFILE, lambda: [line("--app=y", profile=r"C:\Other")]) is False
    assert bp.profile_in_use(PROFILE, lambda: []) is False


def test_a_failed_listing_means_the_window_is_gone_not_a_hang():
    def boom():
        raise RuntimeError("powershell missing")

    assert bp.profile_in_use(PROFILE, boom) is False
