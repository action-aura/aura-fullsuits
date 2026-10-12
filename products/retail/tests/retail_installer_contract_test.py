"""INSTALL-1 item 3: the installer script and spec carry the icon, the OS floor
and the WebView2 bootstrap.

A source-contract test is the weakest kind (it reads text, it does not run an
installer), so it is deliberately narrow: it pins the things that a later edit
could drop WITHOUT any build failing -- a missing MinVersion or icon compiles
fine and just ships a worse installer. The real proof is the artefact build
(ISCC compile, silent install, icon extracted from the installed exe); this
file only stops it silently regressing.

Run (ONE FILE PER PROCESS):
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest products/retail/tests/retail_installer_contract_test.py -q
"""
import re
import struct
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / 'packaging'
ISS = (PKG / 'aura_retail_setup.iss').read_text(encoding='utf-8')
SPEC = (PKG / 'aura_retail.spec').read_text(encoding='utf-8')
ICO = PKG / 'aura_retail.ico'


def _ico_sizes(path):
    data = path.read_bytes()
    reserved, kind, count = struct.unpack_from('<HHH', data, 0)
    assert (reserved, kind) == (0, 1), 'not an .ico file'
    sizes = set()
    for i in range(count):
        width, height = struct.unpack_from('<BB', data, 6 + 16 * i)
        sizes.add((width or 256, height or 256))
    return sizes


def test_the_icon_is_a_real_multi_size_ico():
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= _ico_sizes(ICO)


def test_installer_and_exe_both_use_that_icon():
    assert re.search(r'^SetupIconFile=aura_retail\.ico$', ISS, re.M)
    # Anchored to line start so a commented-out `#icon=` does not satisfy it.
    assert re.search(r"^\s+icon=os\.path\.join\(os\.path\.dirname\(os\.path\.abspath\(SPEC\)\), 'aura_retail\.ico'\)", SPEC, re.M)


def test_windows_floor_is_1809():
    assert re.search(r'^MinVersion=10\.0\.17763$', ISS, re.M)


def test_webview2_bootstrapper_runs_only_when_the_runtime_is_missing_and_before_any_launch():
    run = ISS[ISS.index('[Run]'):ISS.index('[UninstallRun]')]
    entries = [l for l in run.splitlines() if l.startswith('Filename:')]
    assert 'MicrosoftEdgeWebview2Setup.exe' in entries[0]
    assert '/silent /install' in entries[0] and 'Check: WebView2Missing' in entries[0]
    assert 'waituntilterminated' in entries[0]
    assert all('AuraRetail.exe' in e for e in entries[1:])      # the launches come after
    # The bundled file is also only unpacked when needed.
    assert re.search(r'MicrosoftEdgeWebview2Setup\.exe".*Check: WebView2Missing', ISS)


def test_runtime_detection_checks_the_documented_client_guid_in_all_three_places():
    assert '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}' in ISS
    for needle in ('HKLM64', 'HKLM32', 'HKCU'):
        assert needle in ISS


def test_the_installer_keeps_a_copy_of_itself_for_rollback():
    assert re.search(r'Source: "\{srcexe\}"; DestDir: "\{app\}\\installer-cache"', ISS)


def test_the_header_no_longer_claims_a_program_files_only_install():
    header = ISS.split('[Setup]')[0]
    assert 'writes into {app} (Program Files)' not in header
    assert 'per-user' in header


def test_the_licence_activation_hook_is_wrapped_to_ask_for_the_firewall_rule():
    # M3a: wiring only a source read can pin cheaply -- a refactor of app.py that
    # passes the bare hook again compiles and runs, and the till is back to
    # Windows' own prompt appearing at first hub start.
    app_py = (Path(__file__).resolve().parents[1] / 'backend' / 'app.py').read_text(encoding='utf-8')
    assert re.search(r'^\s+on_activation_success=_with_firewall_ask_after_activation\(\s*_on_licence_activated,',
                     app_py, re.M)


# -- verifier I3 / L6: the Pascal logic and the bootstrapper check ---------------------
def _pascal_function(name):
    match = re.search(r'^function %s\([^)]*\)[^\n]*\n(?:var\n(?:[ \t][^\n]*\n)*)?begin\n(.*?)\nend;' % name,
                      ISS, re.M | re.S)
    assert match, '%s not found in the .iss' % name
    return ' '.join(match.group(1).split())


def test_webview2_missing_is_the_negation_of_the_three_runtime_lookups():
    # Inno Pascal cannot be run from here, so the body is pinned as text. An
    # inverted Result (verifier I3) compiles, passes every other check and
    # installs the bootstrapper on PCs that HAVE the runtime and skips it on
    # the ones that do not -- the only thing that distinguishes it is the `not`
    # and the exact three lookups.
    body = _pascal_function('WebView2Missing')
    assert body == ('Result := not (WebView2VersionOk(HKEY_LOCAL_MACHINE, False) '
                    'or WebView2VersionOk(HKEY_LOCAL_MACHINE, True) '
                    'or WebView2VersionOk(HKEY_CURRENT_USER, False));')


def test_a_runtime_counts_as_present_only_with_a_real_version():
    body = _pascal_function('WebView2VersionOk')
    assert "Result := Found and (Version <> '') and (Version <> '0.0.0.0');" in body
    assert all(view in body for view in ('HKCU', 'HKLM32', 'HKLM64'))


def _ps(script):
    import subprocess
    import sys
    if sys.platform != 'win32':
        import pytest
        pytest.skip('Windows PowerShell only')
    import os
    # A PSModulePath inherited from PowerShell 7 stops Windows PowerShell 5.1
    # from loading Microsoft.PowerShell.Security (Get-AuthenticodeSignature).
    env = {k: v for k, v in os.environ.items() if k.upper() != 'PSMODULEPATH'}
    done = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                          capture_output=True, text=True, timeout=120, env=env)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


CHECK_PS1 = Path(__file__).resolve().parents[3] / 'scripts' / 'release' / 'webview2_bootstrapper_check.ps1'


def _check(path, extra=''):
    out = _ps(". '%s'; $r = Test-WebView2Bootstrapper -Path '%s' %s; if ($r) { $r } else { 'ACCEPTED' }"
              % (CHECK_PS1, path, extra))
    return out


def test_bootstrapper_check_rejects_unsigned_and_wrong_signer_files(tmp_path):
    fake = tmp_path / 'MicrosoftEdgeWebview2Setup.exe'
    fake.write_bytes(b'not a binary')
    assert 'signature status' in _check(fake)
    import os
    notepad = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'System32', 'notepad.exe')
    # Microsoft-signed, valid, and exactly what the OLD 'O=Microsoft Corporation'
    # test accepted -- but it is not the Microsoft code-signing identity.
    assert 'signer is not CN=Microsoft Corporation' in _check(notepad)


def test_bootstrapper_check_also_requires_the_bootstrapper_s_own_name():
    import os
    notepad = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'System32', 'notepad.exe')
    # Right signer (told to expect Windows' catalog identity), wrong file: the
    # original-filename half must be what refuses it.
    assert 'original filename' in _check(notepad, "-ExpectedSignerCommonName 'Microsoft Windows'")


def test_bootstrapper_check_accepts_when_signer_and_names_all_match():
    import os
    notepad = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'System32', 'notepad.exe')
    info = _ps("$v=(Get-Item '%s').VersionInfo; $v.OriginalFilename + '|' + $v.ProductName" % notepad).split('|')
    extra = ("-ExpectedSignerCommonName 'Microsoft Windows' -ExpectedOriginalFilename '%s' "
             "-ExpectedProductNameFragment '%s'" % (info[0], info[1][:5]))
    assert _check(notepad, extra) == 'ACCEPTED'      # the allow-half: a matching file is not refused


def test_the_build_script_uses_the_two_part_check():
    build = (Path(__file__).resolve().parents[3] / 'scripts' / 'release' / 'build_desktop_installer.ps1').read_text(encoding='utf-8')
    assert "webview2_bootstrapper_check.ps1" in build
    assert re.search(r'\$WebView2Problem = Test-WebView2Bootstrapper -Path \$WebView2Path', build)
    assert "O=Microsoft Corporation" not in build
