"""
Aura FullSuits -- deterministic combined test runner (Wave 1B, AUDIT-010).

Root cause of the cross-file pytest pollution this replaces: registry_db.py's
DB_PATH, schema.py's BASE_DIR/SUBSYS_DIR, and config.py's DATABASE_DIR are all
computed once at module import time from the AURA_APP_DATA environment
variable. Each test file sets a fresh temp directory via
os.environ.update(AURA_APP_DATA=...) before importing app.py -- but Python
caches imported modules in sys.modules for the life of the process, so only
the FIRST test file's import of these modules actually takes effect; every
later file's env var change is silently ignored, and when the first file's
teardown_module() deletes ITS temp directory, every other file still points
at that now-deleted path and fails with "no such table: users".

This is NOT a production defect: a real process (the Windows executable, or
the embedded Android backend) only ever imports these modules once per run,
so the module-level caching is completely safe there. It only surfaces when
many independent test files are collected into a single pytest process.
Reworking config.py/registry_db.py/schema.py to re-resolve AURA_APP_DATA on
every call would be architecture surgery motivated purely by test
convenience -- exactly what Wave 1B was told not to do ("do not alter
production behavior merely to make bad tests pass").

The correct fix is process-level isolation, matching how these modules are
actually used in production: one Python process per test *file*. This
script runs each test file as its own `pytest` subprocess (fresh
interpreter, fresh sys.modules, no bleed) and aggregates the results into
one report -- the "one supported command" AUDIT-010 asked for.

Usage:
    python products/run_all_tests.py                 # everything
    python products/run_all_tests.py retail           # only products/retail/tests
    python products/run_all_tests.py clinic           # only products/clinic/tests
    python products/run_all_tests.py commercial_runtime  # only commercial_runtime/tests
    python products/run_all_tests.py identity notifications  # any subset, space-separated

`commercial_runtime` means `commercial_runtime/tests` and NOTHING ELSE. Each
domain package under commercial_runtime keeps its own `<package>/tests`
directory and needs its own key in SUITES below -- identity, licensing_contracts,
notifications, sync, einvoicing. A package whose key is missing is not
discovered by ANY invocation of this script, including the no-argument
"everything" one; see the SUITES comments for the two that were missing.

JavaScript tests (CI hardening, AUDIT-010 follow-up):
    Each product's tests/ directory can also hold standalone `*_test.js`
    files -- one Node script per file, no test framework configured (this is
    a vanilla-JS, build-step-free frontend; see CLAUDE.md). Discovered and
    run the same way as the `*_test.py` suites: one `node <file>` subprocess
    per file, folded into the same PASS/FAIL summary and the same
    "N file(s) run, N passed, N failed" total, so one number means
    everything. Several of these are the ONLY coverage for real, previously
    shipped XSS/injection-breakout bugs -- see each file's header comment.

    Node availability is REQUIRED whenever JS test files are discovered.
    If `node` is not on PATH, the run FAILS LOUDLY (non-zero exit, files
    named) rather than silently reporting green having run zero JS files --
    that silent-skip is exactly how these files went unrun in CI for as long
    as they did. The only way to skip them is the explicit, opt-in
    AURA_ALLOW_MISSING_NODE=1 environment variable, intended for local
    developer machines without Node installed; CI must never set it (see
    .github/workflows/ci.yml, which sets it to "0" explicitly rather than
    relying on this default).
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# This runner must survive non-ASCII in test output, in BOTH directions.
#
# It ships an Arabic product, so test names, fixtures and failure messages
# legitimately contain Arabic -- and the JS suites print it. On Windows the
# default console encoding is cp1252, which produced two separate crashes:
#
#   reading   the subprocess reader thread died with UnicodeDecodeError, left
#             proc.stdout as None, and the runner crashed on .strip()
#   writing   printing a decoded character the console cannot represent died
#             with UnicodeEncodeError
#
# Both are worse than a failing test. Every test PASSED in each case; the
# harness fell over handling the results, which in CI reads as a failed build
# containing no failing test -- a red mark with nothing to fix.
#
# `errors='replace'` rather than 'strict' on purpose: a character the console
# cannot draw should degrade to a placeholder, never abort a test run. The
# subprocess side is set at each call site.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        # Not a real TTY (piped, or a harness that replaced the stream). The
        # decode side still holds, which is the half that crashed the runner.
        pass

# Local-developer-only escape hatch: set to '1' to explicitly skip JS tests
# when `node` is unavailable, instead of failing the run. CI must not set
# this to '1' -- see module docstring and ci.yml.
ALLOW_MISSING_NODE_ENV = 'AURA_ALLOW_MISSING_NODE'

ROOT = Path(__file__).resolve().parent.parent

SUITES = {
    'retail': ROOT / 'products' / 'retail' / 'tests',
    'clinic': ROOT / 'products' / 'clinic' / 'tests',
    'commercial_runtime': ROOT / 'commercial_runtime' / 'tests',
    # Phase 7 -- product-side licensing domain (docs/licensing/phase7/). A
    # separate key from 'commercial_runtime' (not merged into that dir)
    # because it has its own large, fast-growing test surface and mirroring
    # the retail/clinic/commercial_runtime split at this level lets
    # `python products/run_all_tests.py licensing_contracts` run just this
    # suite the same way `... retail` already does.
    'licensing_contracts': ROOT / 'commercial_runtime' / 'licensing_contracts' / 'tests',
    # Multi-device sync foundation (2026-08-06), Task 5: same reasoning as
    # 'licensing_contracts' above -- its own key so
    # `python products/run_all_tests.py sync` runs just this suite.
    'sync': ROOT / 'commercial_runtime' / 'sync' / 'tests',
    # docs/einvoicing/phase1/ -- Jordan JoFotara e-invoicing shared domain.
    # Same rationale as licensing_contracts above: its own key, own
    # fast-growing test surface, mirrors the existing split.
    'einvoicing': ROOT / 'commercial_runtime' / 'einvoicing' / 'tests',
    # ── Suites this runner used to miss entirely ──────────────────────────
    #
    # `commercial_runtime` above points at `commercial_runtime/tests` ONLY.
    # Every domain package under commercial_runtime keeps its tests in its own
    # `<package>/tests` directory, and each one has to be listed here by hand
    # or it is simply not discovered. licensing_contracts, sync and einvoicing
    # were each added as they appeared; identity and notifications never were,
    # so `python products/run_all_tests.py` -- the command this file's
    # docstring calls "the one supported command" -- silently ran neither.
    #
    # identity is the sharper of the two: it is the SERVER-SIDE half of the
    # capability seam whose CLIENT-side half
    # (products/retail/tests/retail_reports_capability_gate_test.js) already
    # runs here under 'retail'. The client half of that contract was being
    # checked on every run while the server half -- the route that decides
    # what the client is told, and the tri-state `capabilities` value the whole
    # gate is built on -- was not checked at all.
    #
    # Note this was never a total blind spot: .github/workflows/ci.yml used
    # to run a separate `python -m pytest commercial_runtime` step, which DID
    # collect both directories. But that step was one pytest process over
    # every suite at once, which is precisely the cross-file import-caching
    # hazard this whole runner exists to avoid (see the module docstring) --
    # so the only isolation-correct path to these tests skipped them, and the
    # path that reached them was the fragile one. Measured 2026-09-07:
    # identity in one process = 10 failed / 305 passed ("no such table:
    # users", "An admin account already exists"); one file per process =
    # 315 / 315. Since 2026-09-07 ci.yml calls THIS runner with every key
    # under commercial_runtime spelled out instead; a new domain package
    # therefore needs a key here AND in ci.yml, or its tests run nowhere.
    'identity': ROOT / 'commercial_runtime' / 'identity' / 'tests',
    'notifications': ROOT / 'commercial_runtime' / 'notifications' / 'tests',
}


def discover(dirs):
    files = []
    for d in dirs:
        if d.exists():
            files.extend(sorted(d.glob('*_test.py')) + sorted(d.glob('test_*.py')))
    return files


def discover_js(dirs):
    """
    Standalone Node test scripts: `*_test.js`. Same SUITES dirs as discover()
    above, so any suite (retail, clinic, ...) that grows JS tests is picked
    up automatically -- this does not structurally special-case retail.
    Today only products/retail/tests has any; products/clinic/tests has
    none yet (see run_all_tests.py callers / CI report).
    """
    files = []
    for d in dirs:
        if d.exists():
            files.extend(sorted(d.glob('*_test.js')))
    return files


def run_one(path):
    start = time.time()
    proc = subprocess.run(
        [sys.executable, '-m', 'pytest', str(path), '-q', '--no-header'],
        cwd=str(ROOT), capture_output=True, text=True,
        # UTF-8 explicitly, and never raise on a byte we cannot decode.
        # `text=True` alone decodes with the OS default -- cp1252 on
        # Windows -- so the first test to print Arabic killed the reader
        # thread with UnicodeDecodeError, left proc.stdout as None, and
        # crashed the RUNNER. Every test had passed; the harness fell over
        # collecting the results, which reads in CI as a failed build with
        # no failing test in it.
        encoding='utf-8', errors='replace',
    )
    duration = time.time() - start
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else '(no output)'
    return {
        'path': str(path.relative_to(ROOT)),
        'ok': proc.returncode == 0,
        'summary': tail,
        'duration_s': round(duration, 2),
        'stdout_tail': '\n'.join(proc.stdout.strip().splitlines()[-15:]),
    }


def run_one_js(path):
    start = time.time()
    proc = subprocess.run(
        ['node', str(path)],
        cwd=str(ROOT), capture_output=True, text=True,
        # UTF-8 explicitly, and never raise on a byte we cannot decode.
        # `text=True` alone decodes with the OS default -- cp1252 on
        # Windows -- so the first test to print Arabic killed the reader
        # thread with UnicodeDecodeError, left proc.stdout as None, and
        # crashed the RUNNER. Every test had passed; the harness fell over
        # collecting the results, which reads in CI as a failed build with
        # no failing test in it.
        encoding='utf-8', errors='replace',
    )
    duration = time.time() - start
    ok = proc.returncode == 0
    stdout_lines = proc.stdout.strip().splitlines()
    stderr_lines = proc.stderr.strip().splitlines()
    # Unlike pytest (whose PASS/FAIL summary line always lands on stdout),
    # these scripts' own convention (see e.g. retail_customer_modal_xss_test.js)
    # is console.log(...) for the "PASS: <file>" line but console.error(...)
    # for the "FAIL: <file>" line + stack trace -- and some tests also
    # legitimately console.error() as part of exercising an error-handling
    # code path they're asserting on (e.g. retail_dashboard_error_propagation_
    # _test.js), even though the run itself passes. So "last line overall" is
    # not reliable: on a pass, the real verdict is on stdout even if stderr
    # has trailing noise after it; on a fail, it's on stderr. Pick whichever
    # stream actually carries this run's verdict, falling back to the other
    # stream (and then a placeholder) so nothing is ever silently blank.
    primary, secondary = (stdout_lines, stderr_lines) if ok else (stderr_lines, stdout_lines)
    tail = primary[-1] if primary else (secondary[-1] if secondary else '(no output)')
    combined_lines = stdout_lines + stderr_lines
    return {
        'path': str(path.relative_to(ROOT)),
        'ok': ok,
        'summary': tail,
        'duration_s': round(duration, 2),
        'stdout_tail': '\n'.join(combined_lines[-15:]),
    }


def main():
    args = sys.argv[1:]
    selected = args if args else list(SUITES.keys())
    dirs = [SUITES[a] for a in selected if a in SUITES]
    py_files = discover(dirs)
    js_files = discover_js(dirs)

    if js_files and not shutil.which('node'):
        allow_missing = os.environ.get(ALLOW_MISSING_NODE_ENV) == '1'
        js_list = '\n  '.join(str(f.relative_to(ROOT)) for f in js_files)
        if allow_missing:
            # Explicit, opt-in, loud -- not a silent skip. This must never be
            # what CI does; see module docstring and ci.yml.
            print(
                f"WARNING: node is not on PATH -- SKIPPING {len(js_files)} JS "
                f"test file(s) because {ALLOW_MISSING_NODE_ENV}=1 was "
                f"explicitly set (local-developer escape hatch only):\n  {js_list}"
            )
            js_files = []
        else:
            print(
                f"FAIL: node is not on PATH -- cannot run {len(js_files)} JS "
                f"test file(s):\n  {js_list}\n"
                f"These carry the only regression coverage for real, "
                f"previously shipped bugs (stored-XSS, onclick-attribute "
                f"breakout, the owner-row Deactivate-control guard) and must "
                f"not be silently skipped. If this is an intentional "
                f"local-dev run on a machine without Node installed, set "
                f"{ALLOW_MISSING_NODE_ENV}=1 to explicitly skip them. CI "
                f"must never set this."
            )
            return 1

    files = [(f, run_one) for f in py_files] + [(f, run_one_js) for f in js_files]

    if not files:
        print('No test files found for:', selected)
        return 1

    results = []
    for f, runner in files:
        r = runner(f)
        results.append(r)
        status = 'PASS' if r['ok'] else 'FAIL'
        print(f"[{status}] {r['path']}  ({r['duration_s']}s)  {r['summary']}")

    failed = [r for r in results if not r['ok']]
    print()
    print(f"{len(results)} file(s) run, {len(results) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print()
        print('--- Failure detail ---')
        for r in failed:
            print(f"\n=== {r['path']} ===")
            print(r['stdout_tail'])
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
