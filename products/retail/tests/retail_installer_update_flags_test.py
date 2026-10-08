"""
Aura Retail -- the installer must cooperate with the in-app updater.

The updater (commercial_runtime/updates/service.py) starts the installer with
INSTALLER_ARGS while AuraRetail.exe is still open. Three things must hold in
aura_retail_setup.iss for that to work, and each is a different failure if it
does not:

  * CloseApplications=force + the exe filter: without it a silent run cannot
    replace the locked exe (the install fails or waits on a prompt nobody can
    answer);
  * RestartApplications=no and a [Run] entry gated on /RELAUNCH=1: a silent
    install SKIPS the normal "Launch Aura Retail now" entry (skipifsilent), so
    without this entry the shop is left with no app open after an update;
  * that entry is NOT 'postinstall' (postinstall entries are skipped when
    silent) and the interactive entry is left exactly as it was.

The two writers of the parameter name (the .iss reading RELAUNCH, the updater
passing /RELAUNCH=1) are checked against each other, because a rename on one
side would silently stop the relaunch.

Run:
    pytest products/retail/tests/retail_installer_update_flags_test.py -v
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ISS = ROOT / 'products' / 'retail' / 'packaging' / 'aura_retail_setup.iss'


def _text():
    return ISS.read_text(encoding='utf-8')


def _section(name):
    text = _text()
    m = re.search(r'^\[%s\]\s*$(.*?)(?=^\[|\Z)' % re.escape(name), text, re.MULTILINE | re.DOTALL)
    assert m, '[%s] section not found in %s' % (name, ISS)
    return [ln.strip() for ln in m.group(1).splitlines() if ln.strip() and not ln.strip().startswith(';')]


def _setup():
    out = {}
    for ln in _section('Setup'):
        key, _, value = ln.partition('=')
        out[key.strip()] = value.strip()
    return out


def test_the_installer_closes_only_our_own_running_exe_without_prompting():
    s = _setup()
    assert s.get('CloseApplications') == 'force'
    assert s.get('CloseApplicationsFilter') == 'AuraRetail.exe'
    assert s.get('RestartApplications') == 'no'


def test_the_upgrade_key_and_privilege_model_were_not_disturbed():
    # The updater leans on both: the same AppId makes it an upgrade, and
    # PrivilegesRequiredOverridesAllowed=dialog is what lets /ALLUSERS elevate.
    text = _text()
    assert '#define AppId "{{A039EDA8-410E-4400-B3A5-A7CD4AF7F437}"' in text
    s = _setup()
    assert s.get('PrivilegesRequired') == 'lowest'
    assert s.get('PrivilegesRequiredOverridesAllowed') == 'dialog'


def test_the_interactive_launch_entry_is_unchanged():
    runs = _section('Run')
    interactive = [r for r in runs if 'postinstall' in r]
    assert len(interactive) == 1
    assert 'skipifsilent' in interactive[0] and 'nowait' in interactive[0]
    assert 'Check:' not in interactive[0]


def test_a_silent_relaunch_entry_exists_gated_on_the_relaunch_switch():
    runs = _section('Run')
    relaunch = [r for r in runs if 'ShouldRelaunch' in r]
    assert len(relaunch) == 1, runs
    entry = relaunch[0]
    assert entry.startswith('Filename: "{app}\\AuraRetail.exe"')
    assert 'nowait' in entry
    assert 'postinstall' not in entry     # postinstall entries do not run when silent
    assert 'skipifsilent' not in entry    # ...and this one must
    # An elevated (/ALLUSERS) update must reopen Aura as the person who uses
    # it, not as the admin who approved UAC -- otherwise a different account's
    # empty %LOCALAPPDATA%\AuraRetail opens. Implicit for postinstall entries,
    # explicit here.
    assert 'runasoriginaluser' in entry


def test_should_relaunch_reads_the_switch_exactly_as_the_updater_passes_it():
    from commercial_runtime.updates.service import INSTALLER_ARGS
    text = _text()
    m = re.search(r"function ShouldRelaunch\(\): Boolean;\s*begin\s*Result := ExpandConstant\('\{param:(\w+)\|0\}'\) = '1';\s*end;", text)
    assert m, 'ShouldRelaunch() is missing or changed shape'
    assert '/%s=1' % m.group(1) in INSTALLER_ARGS


def test_the_updater_asks_for_a_silent_forced_close_install_with_no_reboot():
    from commercial_runtime.updates.service import INSTALLER_ARGS
    assert INSTALLER_ARGS == ('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/FORCECLOSEAPPLICATIONS', '/RELAUNCH=1')
