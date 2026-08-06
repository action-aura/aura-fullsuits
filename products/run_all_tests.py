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
"""
import subprocess
import sys
import time
from pathlib import Path

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
}


def discover(dirs):
    files = []
    for d in dirs:
        if d.exists():
            files.extend(sorted(d.glob('*_test.py')) + sorted(d.glob('test_*.py')))
    return files


def run_one(path):
    start = time.time()
    proc = subprocess.run(
        [sys.executable, '-m', 'pytest', str(path), '-q', '--no-header'],
        cwd=str(ROOT), capture_output=True, text=True,
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


def main():
    args = sys.argv[1:]
    selected = args if args else list(SUITES.keys())
    dirs = [SUITES[a] for a in selected if a in SUITES]
    files = discover(dirs)

    if not files:
        print('No test files found for:', selected)
        return 1

    results = []
    for f in files:
        r = run_one(f)
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
