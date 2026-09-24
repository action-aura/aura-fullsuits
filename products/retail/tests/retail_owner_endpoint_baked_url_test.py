"""Aura Retail -- release-baked Owner licensing URL (retail-hardware-viewports).

WHY THIS EXISTS

products/retail/backend/config.py's OWNER_LICENSING_BASE_URL had exactly one
way to reach a value: the AURA_OWNER_LICENSING_URL environment variable. An
installed copy launched from the Start menu (Inno Setup installer over a
PyInstaller onedir bundle) has no such variable and no screen to set one --
so it can never activate. android/aura-retail already solves the equivalent
problem at BUILD TIME (`ownerLicensingBaseUrl` -P property ->
`BuildConfig.OWNER_LICENSING_BASE_URL`, see build.gradle); this file proves
the desktop counterpart: a release engineer bakes the URL into
`owner_endpoint.json` via `scripts/release/build_desktop_installer.ps1`,
PyInstaller bundles it (`aura_retail.spec`), and config.py reads it back
ONLY when no operator env var is already set -- the exact precedence rule
`_resolve_effective_sync_relay_base_url()` already uses for
AURA_SYNC_RELAY_URL vs. the value persisted from Owner's own activation
response.

WHAT IS TESTED

  * `_resolve_owner_licensing_base_url()` -- the pure precedence/validation
    function -- against real temp files, no Flask, no network, no reload.
  * Import-level wiring, in a SUBPROCESS. config.py resolves this at IMPORT
    TIME -- same reasoning as retail_sync_relay_persisted_discovery_test.py's
    own module docstring, except that test can get away with an in-process
    fresh module because it seeds a database before the FIRST import of this
    process. Here the whole point under test is "what BASE_DIR resolves to
    and what config.py reads off disk the moment it is imported", which a
    real second process proves and an in-process importlib.reload() of an
    already-imported module does not.
  * The spec (aura_retail.spec) and script (build_desktop_installer.ps1)
    proof: the REAL artefact, not a description of it -- ENGINEERING.md
    section 1, "run the real artefact before claiming done". The gated test
    actually invokes the release script with -SkipInstaller, which itself
    invokes PyInstaller, and inspects the resulting dist bundle on disk.

Run standalone, one file per process (AUDIT-010):
    pytest products/retail/tests/retail_owner_endpoint_baked_url_test.py -v

The release-build proof (runs PyInstaller for real, several minutes) only
runs when AURA_RUN_RELEASE_BUILD_TEST=1 is set:
    AURA_RUN_RELEASE_BUILD_TEST=1 pytest products/retail/tests/retail_owner_endpoint_baked_url_test.py -v -k release_build
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Same boilerplate as retail_site_relay_config_test.py: config.py resolves
# BASE_DIR/DATABASE_DIR/SECRET_KEY at IMPORT time, so importing it with no
# AURA_APP_DATA/AURA_BUNDLE_DIR set would write a secret key and a database
# directory straight into products/retail/backend -- the source tree, not a
# throwaway install. AURA_BUNDLE_DIR is pointed at BACKEND_DIR itself (a
# realistic "unfrozen dev checkout" BASE_DIR) so the module-level `config`
# import below resolves a harmless, nonexistent owner_endpoint.json path
# (BACKEND_DIR has none checked in) -- exactly the ordinary dev/source case.
DATA = Path(tempfile.mkdtemp(prefix="aura_retail_owner_endpoint_baked_url_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_OWNER_LICENSING_URL", None)

import config  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── pure resolver unit tests ────────────────────────────────────────────────
# _resolve_owner_licensing_base_url(env_value, baked_path) -> (url, source, problems)
# is pure (no environment reads, everything passed in) -- same discipline as
# site_relay_should_start() and resolve_licensing_trust_anchor_path() above it
# in config.py, and for the same reason: it lets every branch be driven
# directly, with a real temp file, and with no module reload.

def _write_baked(tmp_path, payload):
    path = tmp_path / 'owner_endpoint.json'
    if isinstance(payload, str):
        path.write_bytes(payload.encode('utf-8'))
    else:
        path.write_bytes(json.dumps(payload).encode('utf-8'))
    return path


def test_env_wins_over_a_valid_baked_file(tmp_path):
    baked = _write_baked(tmp_path, {'owner_licensing_base_url': 'https://baked.example/api/licensing/v1'})
    result = config._resolve_owner_licensing_base_url(
        'https://env.example/api/licensing/v1', str(baked))
    assert result == ('https://env.example/api/licensing/v1', 'env', [])


def test_env_wins_even_when_baked_file_is_invalid(tmp_path):
    """Precedence, not validity -- an operator's own explicit env var must
    never be second-guessed by a broken baked file it never even needed to
    read. Mirrors _resolve_effective_sync_relay_base_url()'s own precedence
    rule for AURA_SYNC_RELAY_URL vs. a persisted value."""
    baked = _write_baked(tmp_path, {'owner_licensing_base_url': 'ftp://bad.example'})
    result = config._resolve_owner_licensing_base_url(
        'https://env.example/api/licensing/v1', str(baked))
    assert result == ('https://env.example/api/licensing/v1', 'env', [])


def test_baked_https_used_when_env_empty(tmp_path):
    baked = _write_baked(tmp_path, {'owner_licensing_base_url': 'https://owner.example/api/licensing/v1'})
    result = config._resolve_owner_licensing_base_url('', str(baked))
    assert result == ('https://owner.example/api/licensing/v1', 'baked', [])


def test_http_loopback_accepted(tmp_path):
    for host in ('127.0.0.1', 'localhost'):
        baked = _write_baked(tmp_path, {'owner_licensing_base_url': f'http://{host}:5551/api/licensing/v1'})
        result = config._resolve_owner_licensing_base_url('', str(baked))
        assert result == (f'http://{host}:5551/api/licensing/v1', 'baked', [])


def test_http_lan_rejected_with_a_problem(tmp_path):
    baked = _write_baked(tmp_path, {'owner_licensing_base_url': 'http://192.168.1.10:5551/api/licensing/v1'})
    url, source, problems = config._resolve_owner_licensing_base_url('', str(baked))
    assert (url, source) == ('', '')
    assert len(problems) == 1
    assert '192.168.1.10' in problems[0]


def test_non_http_scheme_rejected(tmp_path):
    """A scheme outside {http, https} must be rejected outright when baked
    alone (env empty) -- ftp, ws, file, or no scheme at all. Distinct from
    the http-vs-https loopback tests below: this is the outer scheme gate,
    which those tests never exercise because they only ever use http/https
    themselves."""
    for bad_url in ('ftp://owner.example/api/licensing/v1', 'not a url at all'):
        baked = _write_baked(tmp_path, {'owner_licensing_base_url': bad_url})
        url, source, problems = config._resolve_owner_licensing_base_url('', str(baked))
        assert (url, source) == ('', ''), f"{bad_url!r} should have been rejected"
        assert len(problems) == 1, f"{bad_url!r} should have produced exactly one problem"


def test_http_public_rejected(tmp_path):
    baked = _write_baked(tmp_path, {'owner_licensing_base_url': 'http://owner.example/api/licensing/v1'})
    url, source, problems = config._resolve_owner_licensing_base_url('', str(baked))
    assert (url, source) == ('', '')
    assert len(problems) == 1


def test_userinfo_query_and_fragment_rejected(tmp_path):
    for bad in (
        'https://user:pass@owner.example/api/licensing/v1',
        'https://owner.example/api/licensing/v1?x=1',
        'https://owner.example/api/licensing/v1#frag',
    ):
        baked = _write_baked(tmp_path, {'owner_licensing_base_url': bad})
        url, source, problems = config._resolve_owner_licensing_base_url('', str(baked))
        assert (url, source) == ('', ''), f"{bad!r} should have been rejected"
        assert len(problems) == 1, f"{bad!r} should have produced exactly one problem"


def test_missing_file_is_empty_with_no_problem(tmp_path):
    """The normal dev/source case: nobody baked anything, so there is nothing
    to warn about -- this must stay silent, not surface a phantom problem."""
    missing = tmp_path / 'does_not_exist.json'
    result = config._resolve_owner_licensing_base_url('', str(missing))
    assert result == ('', '', [])


def test_malformed_json_is_a_problem(tmp_path):
    baked = _write_baked(tmp_path, '{not json')
    url, source, problems = config._resolve_owner_licensing_base_url('', str(baked))
    assert (url, source) == ('', '')
    assert len(problems) == 1


def test_missing_key_is_a_problem(tmp_path):
    baked = _write_baked(tmp_path, {'something_else': 'https://owner.example'})
    url, source, problems = config._resolve_owner_licensing_base_url('', str(baked))
    assert (url, source) == ('', '')
    assert len(problems) == 1


def test_non_string_value_is_a_problem(tmp_path):
    baked = _write_baked(tmp_path, {'owner_licensing_base_url': 12345})
    url, source, problems = config._resolve_owner_licensing_base_url('', str(baked))
    assert (url, source) == ('', '')
    assert len(problems) == 1


def test_empty_string_value_is_a_problem(tmp_path):
    baked = _write_baked(tmp_path, {'owner_licensing_base_url': '   '})
    url, source, problems = config._resolve_owner_licensing_base_url('', str(baked))
    assert (url, source) == ('', '')
    assert len(problems) == 1


def test_non_object_json_is_a_problem(tmp_path):
    baked = _write_baked(tmp_path, ['not', 'an', 'object'])
    url, source, problems = config._resolve_owner_licensing_base_url('', str(baked))
    assert (url, source) == ('', '')
    assert len(problems) == 1


def test_http_ipv6_loopback_accepted(tmp_path):
    """'::1' is the third loopback host in the rule. Verifier finding
    (2026-09-24): deleting it from _OWNER_ENDPOINT_LOOPBACK_HOSTS left every
    other test in this file green, so nothing protected it."""
    baked = _write_baked(tmp_path, {'owner_licensing_base_url': 'http://[::1]:5551/api/licensing/v1'})
    result = config._resolve_owner_licensing_base_url('', str(baked))
    assert result == ('http://[::1]:5551/api/licensing/v1', 'baked', [])


def test_https_without_a_host_rejected(tmp_path):
    """An https:// URL with no host has to be rejected by the host check
    itself. Verifier finding: the only no-host input above ('not a url at
    all') is caught earlier by the scheme gate, so disabling the host check
    left the whole file green."""
    for bad in ('https:///api/licensing/v1', 'https://:443/api/licensing/v1'):
        baked = _write_baked(tmp_path, {'owner_licensing_base_url': bad})
        url, source, problems = config._resolve_owner_licensing_base_url('', str(baked))
        assert (url, source) == ('', ''), f"{bad!r} should have been rejected"
        assert len(problems) == 1 and 'no host' in problems[0], problems


def test_whitespace_inside_the_url_rejected(tmp_path):
    """The design names 'whitespace inside' as a rejection. Verifier finding:
    all four of these were ACCEPTED as-is -- urlsplit() does not object to a
    space in a host or path, and on Python 3.12+ it silently deletes tab and
    newline characters before parsing, so the check must look at the raw
    string, not the parsed parts."""
    for bad in (
        'https://owner.example/api/licensing v1',
        'https://own er.example/api/licensing/v1',
        'https://owner.example/api/licensing\t/v1',
        'https://owner.example/api/licensing\n/v1',
    ):
        baked = _write_baked(tmp_path, {'owner_licensing_base_url': bad})
        url, source, problems = config._resolve_owner_licensing_base_url('', str(baked))
        assert (url, source) == ('', ''), f"{bad!r} should have been rejected"
        assert len(problems) == 1 and 'whitespace' in problems[0], problems


def test_invalid_port_rejected(tmp_path):
    """A port outside 0-65535 (or not a number) is not a URL `requests` can
    ever reach. Verifier finding: config.py accepted it while the release
    script's System.Uri parse already refuses it, so the two halves of "the
    same rule" disagreed."""
    for bad in ('https://owner.example:99999/api/licensing/v1', 'https://owner.example:abc/api/licensing/v1'):
        baked = _write_baked(tmp_path, {'owner_licensing_base_url': bad})
        url, source, problems = config._resolve_owner_licensing_base_url('', str(baked))
        assert (url, source) == ('', ''), f"{bad!r} should have been rejected"
        assert len(problems) == 1 and 'port' in problems[0], problems


def test_surrounding_whitespace_is_stripped(tmp_path):
    baked = _write_baked(tmp_path, {'owner_licensing_base_url': '  https://owner.example/api/licensing/v1  '})
    result = config._resolve_owner_licensing_base_url('', str(baked))
    assert result == ('https://owner.example/api/licensing/v1', 'baked', [])
    # Env-side whitespace is stripped too: an env var of pure whitespace is
    # not "set", the same treatment the baked value gets.
    result2 = config._resolve_owner_licensing_base_url('   ', str(baked))
    assert result2 == ('https://owner.example/api/licensing/v1', 'baked', [])


# ── import-level wiring, in a subprocess ────────────────────────────────────

_IMPORT_PROBE = """
import sys
sys.path.insert(0, {suite_root!r})
sys.path.insert(0, {backend_dir!r})
import config
print("OWNER_LICENSING_BASE_URL=" + config.OWNER_LICENSING_BASE_URL)
print("OWNER_LICENSING_URL_SOURCE=" + config.OWNER_LICENSING_URL_SOURCE)
"""


def _run_import_probe(env):
    full_env = dict(os.environ)
    full_env.pop('AURA_OWNER_LICENSING_URL', None)
    full_env.update(env)
    probe_src = _IMPORT_PROBE.format(suite_root=str(SUITE_ROOT), backend_dir=str(BACKEND_DIR))
    result = subprocess.run(
        [sys.executable, '-c', probe_src],
        capture_output=True, text=True, env=full_env, cwd=str(BACKEND_DIR),
    )
    assert result.returncode == 0, (
        f"import probe subprocess failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    values = {}
    for line in result.stdout.splitlines():
        if '=' in line:
            key, _, value = line.partition('=')
            values[key] = value
    return values


def test_import_level_baked_value_used_when_env_unset(tmp_path):
    bundle_dir = tmp_path / 'bundle'
    bundle_dir.mkdir()
    app_data_dir = tmp_path / 'appdata'
    (app_data_dir / 'database' / 'subsystems').mkdir(parents=True)
    (bundle_dir / 'owner_endpoint.json').write_bytes(
        json.dumps({'owner_licensing_base_url': 'https://baked-subprocess.example/api/licensing/v1'}).encode('utf-8')
    )
    values = _run_import_probe({
        'AURA_BUNDLE_DIR': str(bundle_dir),
        'AURA_APP_DATA': str(app_data_dir),
        'AURA_STANDALONE': '1',
    })
    assert values.get('OWNER_LICENSING_BASE_URL') == 'https://baked-subprocess.example/api/licensing/v1'
    assert values.get('OWNER_LICENSING_URL_SOURCE') == 'baked'


def test_import_level_env_wins_over_baked_value(tmp_path):
    bundle_dir = tmp_path / 'bundle'
    bundle_dir.mkdir()
    app_data_dir = tmp_path / 'appdata'
    (app_data_dir / 'database' / 'subsystems').mkdir(parents=True)
    (bundle_dir / 'owner_endpoint.json').write_bytes(
        json.dumps({'owner_licensing_base_url': 'https://baked-subprocess.example/api/licensing/v1'}).encode('utf-8')
    )
    values = _run_import_probe({
        'AURA_BUNDLE_DIR': str(bundle_dir),
        'AURA_APP_DATA': str(app_data_dir),
        'AURA_STANDALONE': '1',
        'AURA_OWNER_LICENSING_URL': 'https://env-subprocess.example/api/licensing/v1',
    })
    assert values.get('OWNER_LICENSING_BASE_URL') == 'https://env-subprocess.example/api/licensing/v1'
    assert values.get('OWNER_LICENSING_URL_SOURCE') == 'env'


# ── spec / script proof ─────────────────────────────────────────────────────

def test_spec_references_owner_endpoint_and_release_build_flag():
    spec_text = (PRODUCT_DIR / 'packaging' / 'aura_retail.spec').read_text(encoding='utf-8')
    assert 'owner_endpoint.json' in spec_text
    assert 'AURA_RELEASE_BUILD' in spec_text


# ── the spec's own decisions, executed (not grepped) ────────────────────────
# Verifier finding (2026-09-24): the test above is the ONLY automated check
# the spec had, and it greps for two strings that also appear in the spec's
# COMMENTS -- commenting out the whole fail-closed block left it green. The
# helper below executes the real aura_retail.spec source against a throwaway
# ROOT (the spec derives ROOT from its own path, three levels up), with
# PyInstaller's build steps stubbed to record what Analysis() was handed.
# Milliseconds, no PyInstaller install needed, and it never touches the real
# repo's trust_anchor.json or build/release/.

SPEC_PATH = PRODUCT_DIR / 'packaging' / 'aura_retail.spec'


def _exec_spec(tmp_path, monkeypatch, *, release, trust_anchor, owner_endpoint):
    import types
    root = tmp_path / 'root'
    spec_copy = root / 'products' / 'retail' / 'packaging' / 'aura_retail.spec'
    spec_copy.parent.mkdir(parents=True)
    spec_copy.write_bytes(SPEC_PATH.read_bytes())
    ta = root / 'commercial_runtime' / 'licensing_contracts' / 'trust_anchor.json'
    oe = root / 'build' / 'release' / 'owner_endpoint.json'
    if trust_anchor:
        ta.parent.mkdir(parents=True)
        ta.write_bytes(b'{"keys": [{"key_id": "k1", "public_key": "x", "algorithm": "ed25519"}]}')
    if owner_endpoint:
        oe.parent.mkdir(parents=True)
        oe.write_bytes(b'{"owner_licensing_base_url": "https://owner.example.invalid/api/licensing/v1"}')
    if release:
        monkeypatch.setenv('AURA_RELEASE_BUILD', '1')
    else:
        monkeypatch.delenv('AURA_RELEASE_BUILD', raising=False)

    hooks = types.ModuleType('PyInstaller.utils.hooks')
    hooks.collect_data_files = lambda _name: []
    utils = types.ModuleType('PyInstaller.utils')
    utils.hooks = hooks
    pkg = types.ModuleType('PyInstaller')
    pkg.utils = utils
    monkeypatch.setitem(sys.modules, 'PyInstaller', pkg)
    monkeypatch.setitem(sys.modules, 'PyInstaller.utils', utils)
    monkeypatch.setitem(sys.modules, 'PyInstaller.utils.hooks', hooks)

    captured = {}

    def _analysis(_scripts, **kwargs):
        captured['datas'] = list(kwargs.get('datas', []))
        return types.SimpleNamespace(pure=[], zipped_data=[], scripts=[], binaries=[], zipfiles=[], datas=[])

    namespace = {
        '__name__': '__main__', 'SPEC': str(spec_copy), 'Analysis': _analysis,
        'PYZ': lambda *a, **k: None, 'EXE': lambda *a, **k: None, 'COLLECT': lambda *a, **k: None,
    }
    exec(compile(spec_copy.read_bytes(), str(spec_copy), 'exec'), namespace)
    return captured['datas'], str(ta), str(oe)


def test_spec_release_build_bundles_owner_endpoint_at_the_bundle_root(tmp_path, monkeypatch):
    """The allow-half of the fail-closed rule, and the destination: config.py
    reads os.path.join(BASE_DIR, 'owner_endpoint.json') with BASE_DIR =
    sys._MEIPASS, so the file must be bundled to '.', not into a package."""
    datas, ta, oe = _exec_spec(tmp_path, monkeypatch, release=True, trust_anchor=True, owner_endpoint=True)
    assert (oe, '.') in datas
    assert (ta, os.path.join('commercial_runtime', 'licensing_contracts')) in datas


def test_spec_release_build_fails_closed_without_owner_endpoint(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _exec_spec(tmp_path, monkeypatch, release=True, trust_anchor=True, owner_endpoint=False)
    message = str(exc.value)
    assert 'owner_endpoint.json missing' in message
    assert 'trust_anchor.json missing' not in message


def test_spec_release_build_fails_closed_without_trust_anchor(tmp_path, monkeypatch):
    """The rc.7 defect itself: a release build without trust_anchor.json."""
    with pytest.raises(SystemExit) as exc:
        _exec_spec(tmp_path, monkeypatch, release=True, trust_anchor=False, owner_endpoint=True)
    message = str(exc.value)
    assert 'trust_anchor.json missing' in message
    assert 'owner_endpoint.json missing' not in message


def test_spec_dev_build_stays_lenient_without_either_input(tmp_path, monkeypatch):
    """The other allow-half: AURA_RELEASE_BUILD unset must keep today's
    behaviour -- a plain `pyinstaller aura_retail.spec` with neither file
    still builds, and bundles neither."""
    datas, ta, oe = _exec_spec(tmp_path, monkeypatch, release=False, trust_anchor=False, owner_endpoint=False)
    sources = [src for src, _dest in datas]
    assert ta not in sources and oe not in sources


# ── the release script's own failure paths (fast, no PyInstaller) ───────────
# Verifier finding (2026-09-24): every Write-Step line was Write-Output, so
# `$__exitCode = Main` captured the whole log AND the return code as one
# array, and `exit <array>` exits 0 on both Windows PowerShell 5.1 and pwsh 7.
# Measured on the unfixed script: "FAILED: ... mismatch ... 13" printed, the
# process exit code was 0 -- every failure was a silent success to anything
# keyed on the exit code (CI, a wrapper script, the gated test below). These
# tests pin the exit code of each failure path on every PowerShell host
# present, and only take seconds because each one stops before PyInstaller.

RELEASE_SCRIPT = SUITE_ROOT / 'scripts' / 'release' / 'build_desktop_installer.ps1'
STAGED_OWNER_ENDPOINT = SUITE_ROOT / 'build' / 'release' / 'owner_endpoint.json'
_PS_HOSTS = [host for host in ('powershell', 'pwsh') if shutil.which(host)]
_needs_powershell = pytest.mark.skipif(not _PS_HOSTS, reason="no PowerShell host on PATH")


def _run_release_script(host, *args, cwd=None, env=None):
    return subprocess.run(
        [host, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(RELEASE_SCRIPT), *args],
        capture_output=True, text=True, cwd=str(cwd or SUITE_ROOT), env=env, timeout=600,
    )


# Every fast test also passes a -Python that does not exist, so that if the
# guard under test is ever broken the run fails fast at step e (exit 10)
# instead of silently starting a multi-minute real PyInstaller build -- which
# is exactly what the unfixed script did for the whitespace and git cases.
_NO_BUILD = ('-Python', str(SUITE_ROOT / 'build' / 'release' / 'no_such_python_for_tests.exe'))


@pytest.fixture
def no_release_build_in_progress():
    # The script owns build/release/owner_endpoint.json; if one already
    # exists, a real release build may be mid-flight -- never touch it.
    if STAGED_OWNER_ENDPOINT.exists():
        pytest.skip(f"{STAGED_OWNER_ENDPOINT} exists -- a release build may be in progress")


@_needs_powershell
@pytest.mark.parametrize('host', _PS_HOSTS or ['powershell'])
def test_script_rejected_url_exits_3(host, tmp_path, no_release_build_in_progress):
    result = _run_release_script(
        host, '-OwnerLicensingUrl', 'http://192.168.1.10:5551/api/licensing/v1',
        '-OutDir', str(tmp_path / 'out'), '-AllowDirty', '-SkipInstaller', *_NO_BUILD)
    assert result.returncode == 3, result.stdout + result.stderr
    assert 'FAILED:' in result.stdout and '192.168.1.10' in result.stdout
    assert not STAGED_OWNER_ENDPOINT.exists()


@_needs_powershell
@pytest.mark.parametrize('host', _PS_HOSTS or ['powershell'])
def test_script_rejects_whitespace_inside_the_url(host, tmp_path, no_release_build_in_progress):
    result = _run_release_script(
        host, '-OwnerLicensingUrl', 'https://owner.example.invalid/api/licensing v1',
        '-OutDir', str(tmp_path / 'out'), '-AllowDirty', '-SkipInstaller', *_NO_BUILD)
    assert result.returncode == 3, result.stdout + result.stderr
    assert 'whitespace' in result.stdout


@_needs_powershell
@pytest.mark.parametrize('host', _PS_HOSTS or ['powershell'])
def test_script_failure_after_staging_exits_nonzero_and_cleans_up(host, tmp_path, no_release_build_in_progress):
    """Step h on the FAILURE path: the staged file is written (step d), the
    run then fails inside the try (no such Python), and the finally block
    must still delete it -- with a non-zero exit."""
    result = _run_release_script(
        host, '-OwnerLicensingUrl', 'https://owner.example.invalid/api/licensing/v1',
        '-OutDir', str(tmp_path / 'out'), '-AllowDirty', '-SkipInstaller',
        '-Python', str(tmp_path / 'no_such_python.exe'))
    assert 'Wrote ' in result.stdout, result.stdout + result.stderr  # step d really ran
    assert result.returncode == 10, result.stdout + result.stderr
    assert not STAGED_OWNER_ENDPOINT.exists(), "finally block did not delete the staged baked URL"


@_needs_powershell
@pytest.mark.parametrize('host', _PS_HOSTS or ['powershell'])
def test_script_accepts_ipv6_loopback_like_config_does(host, tmp_path, no_release_build_in_progress):
    """Parity with config.py's rule. System.Uri reports an IPv6 host WITH its
    brackets ('[::1]'), so comparing .Host against '::1' rejected a URL
    config.py accepts. Proven by getting past step c (then failing fast at
    the missing Python, exit 10)."""
    result = _run_release_script(
        host, '-OwnerLicensingUrl', 'http://[::1]:5551/api/licensing/v1',
        '-OutDir', str(tmp_path / 'out'), '-AllowDirty', '-SkipInstaller',
        '-Python', str(tmp_path / 'no_such_python.exe'))
    assert 'OwnerLicensingUrl validated' in result.stdout, result.stdout + result.stderr
    assert result.returncode == 10, result.stdout + result.stderr
    assert not STAGED_OWNER_ENDPOINT.exists()


@_needs_powershell
@pytest.mark.parametrize('host', _PS_HOSTS or ['powershell'])
def test_script_refuses_when_git_status_cannot_run(host, tmp_path, no_release_build_in_progress):
    """Step a must not treat "git status failed" as "working tree is clean"
    (the unfixed script did, and would have recorded dirty=false for a build
    whose tree state was never known). -AllowDirty does not bypass it:
    AllowDirty is consent to a KNOWN-dirty tree, not to an unknown one.

    The repository has a commit (so `git rev-parse HEAD` still WORKS) and a
    corrupt index (so `git status` fails) -- isolating the status check. A
    plain nonexistent GIT_DIR would fail at rev-parse first and leave the
    status check untested; the verifier's first version of this test did
    exactly that, and disabling the status check left it green."""
    repo = tmp_path / 'corrupt_index_repo'
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t.invalid',
                    'commit', '-q', '--allow-empty', '-m', 'init'], check=True)
    (repo / '.git' / 'index').write_bytes(b'not-an-index')
    env = dict(os.environ)
    env['GIT_DIR'] = str(repo / '.git')
    result = _run_release_script(
        host, '-OwnerLicensingUrl', 'https://owner.example.invalid/api/licensing/v1',
        '-OutDir', str(tmp_path / 'out'), '-AllowDirty', '-SkipInstaller', *_NO_BUILD, env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'FAILED:' in result.stdout and 'git status' in result.stdout
    assert 'Working tree is clean' not in result.stdout
    assert not STAGED_OWNER_ENDPOINT.exists()


@_needs_powershell
@pytest.mark.parametrize('host', _PS_HOSTS or ['powershell'])
def test_script_refuses_when_head_cannot_be_resolved(host, tmp_path, no_release_build_in_progress):
    """The other half of step a: `git status` WORKS (a freshly initialised
    repository with no commits) but `git rev-parse HEAD` fails. The unfixed
    script recorded git_commit "UNKNOWN" and carried on; a release build must
    name the commit it was built from."""
    empty_repo = tmp_path / 'empty_repo'
    subprocess.run(['git', 'init', '-q', str(empty_repo)], check=True)
    env = dict(os.environ)
    env['GIT_DIR'] = str(empty_repo / '.git')
    result = _run_release_script(
        host, '-OwnerLicensingUrl', 'https://owner.example.invalid/api/licensing/v1',
        '-OutDir', str(tmp_path / 'out'), '-AllowDirty', '-SkipInstaller', *_NO_BUILD, env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'FAILED:' in result.stdout and 'rev-parse' in result.stdout
    assert 'UNKNOWN' not in result.stdout
    assert not STAGED_OWNER_ENDPOINT.exists()


@pytest.mark.skipif(
    os.environ.get('AURA_RUN_RELEASE_BUILD_TEST') != '1',
    reason="runs PyInstaller for real (minutes); set AURA_RUN_RELEASE_BUILD_TEST=1 to run",
)
def test_release_build_script_bakes_the_requested_url(tmp_path, no_release_build_in_progress):
    """The one real-artefact proof in this file (ENGINEERING.md section 1):
    actually invoke the release script, actually run PyInstaller, then read
    what landed on disk -- never a mock of either.

    Verifier hardening (2026-09-24), each closing a way this test used to
    pass without proving anything:
      * the stale dist bundle is removed first -- the old version read
        whatever dist/AuraRetail already held, so a leftover bundle from an
        earlier run with the same URL satisfied it even when the script built
        nothing at all (and the exit-code assertion could not notice, see the
        section above);
      * the script is run from a DIFFERENT working directory -- PyInstaller's
        default dist/ and build/ are relative to the CWD, and the unfixed
        script, run from anywhere but the repo root, built into that CWD and
        then "verified" (and would have packaged) the stale repo bundle;
      * the baked file is checked at the exact bundle root config.py reads
        (the directory holding base_library.zip, i.e. sys._MEIPASS), not
        "anywhere under dist", and the trust anchor's key ids are compared."""
    assert RELEASE_SCRIPT.exists(), f"{RELEASE_SCRIPT} not found"

    dist_dir = SUITE_ROOT / 'dist' / 'AuraRetail'
    if dist_dir.exists():
        shutil.rmtree(dist_dir)  # gitignored build output; PyInstaller --noconfirm deletes it anyway

    out_dir = tmp_path / 'release_out'
    elsewhere = tmp_path / 'some_other_cwd'
    elsewhere.mkdir()
    requested_url = 'https://owner.example.invalid/api/licensing/v1'

    result = _run_release_script(
        'powershell', '-OwnerLicensingUrl', requested_url, '-OutDir', str(out_dir),
        '-AllowDirty', '-SkipInstaller', cwd=elsewhere)
    assert result.returncode == 0, (
        f"build_desktop_installer.ps1 -SkipInstaller failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert 'FAILED' not in result.stdout
    assert 'SCRIPT_DONE exit=0' in result.stdout
    assert not (elsewhere / 'dist').exists(), "PyInstaller built into the caller's CWD, not the repo"

    base_library = list(dist_dir.rglob('base_library.zip'))
    assert len(base_library) == 1, f"expected exactly one base_library.zip under {dist_dir}: {base_library}"
    bundle_root = base_library[0].parent  # == sys._MEIPASS == config.BASE_DIR when frozen

    baked = json.loads((bundle_root / 'owner_endpoint.json').read_bytes())
    assert baked['owner_licensing_base_url'] == requested_url

    source_ids = sorted(k['key_id'] for k in json.loads(
        (SUITE_ROOT / 'commercial_runtime' / 'licensing_contracts' / 'trust_anchor.json').read_bytes())['keys'])
    bundled_ids = sorted(k['key_id'] for k in json.loads(
        (bundle_root / 'commercial_runtime' / 'licensing_contracts' / 'trust_anchor.json').read_bytes())['keys'])
    assert bundled_ids == source_ids and bundled_ids

    assert not STAGED_OWNER_ENDPOINT.exists(), (
        f"{STAGED_OWNER_ENDPOINT} should have been deleted by the script's finally block (step h), "
        f"but still exists -- a stale baked URL could leak into the next ordinary build."
    )
