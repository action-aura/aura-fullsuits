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
    python products/run_all_tests.py retail -j 8      # bounded parallelism, 8 at a time
    python products/run_all_tests.py -j 1              # force today's exact serial behaviour

Parallelism (retail-hardware-viewports task, 2026-09-22): this runner spent
~40 minutes almost entirely idle on 15 of 16 cores, because it ran one
subprocess at a time. That was never a correctness requirement -- it was
just the simplest thing to write first. The correctness requirement, spelled
out at length above, is PROCESS isolation (one interpreter per file), not
TEMPORAL isolation (one file at a time). Nothing about "fresh interpreter,
fresh sys.modules, no bleed" needs the files to run one after another --
each file already computes its own tempfile.mkdtemp()-based AURA_APP_DATA
*inside its own child process*, which was already OS-guaranteed-unique
before this file ever supported -j > 1. So `-j N` below runs up to N of
these already-isolated subprocesses at once instead of queuing them; it
does not change what isolation means or who provides it.

Two things that DO need explicit handling under concurrency, and where to
find them:
    * MUST_RUN_SERIAL -- a file that holds some shared EXTERNAL resource
      (a real Postgres advisory lock is the known example; see that
      constant's own comment for what was actually checked here and why it
      is empty today).
    * PER_FILE_TIMEOUT_SECONDS -- previously there was no timeout at all,
      so one hung file could hang the ENTIRE run forever; see that
      constant's comment for the value chosen and why.

`-j 1` is not a special-cased fallback path -- it is the same scheduler
with a worker pool of size one, which is what makes it a trustworthy escape
hatch: there is no separate "old code path" that could quietly drift from
the parallel one.

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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    # Update channel (feat/update-channel): same rationale -- a package under
    # commercial_runtime keeps its tests in its own directory, so it needs its
    # own key here or no invocation of this script (including the
    # no-argument "everything" one) would ever discover them.
    'updates': ROOT / 'commercial_runtime' / 'updates' / 'tests',
    # Crash reports (feat/crash-reports): the privacy filter, the bounded local
    # queue, capture, the browser endpoint and the sender. Its own key for the
    # same reason as every package above: nothing else discovers it.
    'crash_reports': ROOT / 'commercial_runtime' / 'crash_reports' / 'tests',
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


# ── Files that must never run concurrently WITH EACH OTHER ─────────────────
#
# Everything else this script discovers is safe to parallelize for free,
# because process isolation was never this script's job to provide -- see
# the module docstring. Each file already gets its own OS process and
# computes its own tempfile.mkdtemp()-based AURA_APP_DATA *inside that
# process*, which the OS already guarantees is unique; running two files'
# subprocesses at the same moment instead of one after another does not
# make two independently-mkdtemp()'d directories any more likely to
# collide, any more than two unrelated `python` invocations on the same
# machine "collide" by both existing at once.
#
# The one shape that genuinely CANNOT run concurrently with itself is two
# processes both holding a single EXTERNAL shared resource -- a real
# Postgres advisory lock is the concrete, previously-hit example: the Owner
# Control Center's Postgres-backed test database deadlocks when more than
# one pytest process touches it at once (see project memory,
# feedback_owner_test_db_single_process -- cost about an hour, twice,
# before that was understood). Checked deliberately before this file grew
# -j > 1, rather than assumed away:
#
#   * Owner's Postgres suite is not reachable through this script AT ALL --
#     SUITES above has no 'owner' key, so no invocation of this file, with
#     or without -j, has ever discovered it. There is nothing to list.
#   * Every file under the suites THIS script does discover that opens a
#     real socket (site-relay pinning/beacon/election, the ESC/POS network
#     transport test, the launcher's port-binding regression test) binds to
#     port 0 and reads the OS-assigned port back -- several of their own
#     comments say this was done specifically so concurrent test runs don't
#     collide on one fixed port number, i.e. their authors already designed
#     for exactly this. Confirmed by reading each one (2026-09-22); none
#     hold a fixed port, a fixed log path, or a filesystem lock.
#
# So this set is empty today ON PURPOSE, not by omission -- and it stays a
# live mechanism, not just a comment: if a future suite adds a
# Postgres-backed test, a fixed-port server, or any other single-writer
# external resource, add its *filename* here (path.name -- discovery mixes
# several suite directories together, and basenames are already unique
# under this codebase's naming convention). A name listed here still runs
# with full process isolation, in its own subprocess, own temp dir -- it is
# only ever scheduled with concurrency 1 *relative to other members of this
# set*. It may still run at the same moment as an ordinary parallel file;
# nothing about those conflicts with it, so forcing full exclusivity would
# only cost wall-clock time for no correctness benefit.
MUST_RUN_SERIAL = frozenset({
    # THE SITE-RELAY LAN TESTS. Added 2026-09-22 after the first real
    # parallel run of the full suite: `retail_site_relay_election_wiring_
    # test.py::test_the_beacon_observer_seam_really_listens_on_the_lan`
    # failed with "the coordinator's observe_beacons seam heard nothing",
    # and then passed 8/8 standalone moments later. It was not flaky and it
    # was not the code -- it was this change.
    #
    # WHY THE ORIGINAL PORT SWEEP MISSED IT, which is the lesson worth
    # keeping: that sweep looked for tests binding a FIXED port, found every
    # candidate binding port 0, and concluded the set was empty. But these
    # tests do not contend for a port. They broadcast a UDP beacon and
    # listen for it, so the resource they share is THE NETWORK SEGMENT --
    # and binding port 0 isolates nothing about that. Two of them running at
    # once each hear the other's beacon, or lose their own among them.
    #
    # Members may still overlap ordinary files; they are only kept from
    # overlapping EACH OTHER, which is all the isolation the shared resource
    # actually needs.
    #
    # Listed by basename, every file that broadcasts or observes a beacon --
    # not merely the one that happened to fail. The one that failed is the
    # one that got unlucky in scheduling; the others are the same hazard and
    # would surface on a different machine or a different -j.
    'retail_site_relay_election_wiring_test.py',
    'retail_site_relay_boots_when_enabled_test.py',
    # THE RUNNER'S OWN TEST, added 2026-09-23 after it failed in a full
    # parallel run and then passed 6/6 standalone moments later. Not flaky,
    # and not a defect in the runner -- a category error in where it ran.
    #
    # It MEASURES WALL-CLOCK SPEEDUP: it asserts that N files at -j 4 finish
    # meaningfully faster than the serial sum. That assertion is only
    # meaningful on a machine with spare cores. Run it as one of eight
    # workers while the other seven are saturating the CPU -- and while
    # other agents are building on the same box -- and its own measurement
    # is what gets invalidated, not the thing it measures.
    #
    # A test whose subject is TIMING cannot be scheduled against arbitrary
    # concurrent load, for the same reason the beacon tests above cannot be
    # scheduled against each other: the resource it depends on is not a
    # port, it is the machine's idle capacity.
    'retail_test_runner_parallel_test.py',
    'test_site_relay_beacon.py',
    'test_site_relay_election.py',
    'test_site_relay_coordinator.py',
    'test_site_relay_autojoin.py',
})

# Per-file subprocess timeout, in seconds. There was none before this
# change: a single hung file (an accept() with no timeout, a real deadlock)
# hung the ENTIRE run forever, full stop, with no way out short of killing
# the process tree by hand. That was survivable when "the run" meant one
# file at a time under a human's attention anyway; it stops being
# survivable the moment other worker slots are depending on this one
# freeing up, or the run is unattended. Chosen generously rather than
# tightly: the slowest suite measured end to end (retail, ~186 files) is
# ~40 minutes total, i.e. low tens of seconds per file on average, so any
# single file genuinely needing close to 10 minutes ALONE would already be
# a major outlier worth its own investigation -- this bound exists to catch
# a genuine hang, not to race a slow-but-honest test.
#
# Overridable via AURA_TEST_RUNNER_TIMEOUT_S so a test of the timeout path
# itself doesn't have to wait 10 real minutes for it to fire -- see
# retail_test_runner_parallel_test.py, and the ALLOW_MISSING_NODE_ENV
# escape hatch above for the same "explicit env var, not a CLI flag nobody
# will remember exists" pattern already used in this file.
# RAISED 600 -> 1200 on 2026-09-23, by measurement rather than by taste.
# The 600s figure was justified above on the reasoning that any single file
# needing close to ten minutes ALONE would already be a major outlier. That
# reasoning was sound and the premise turned out to be false:
# `retail_stock_sync_test.py` takes 329s -- five and a half minutes -- on an
# otherwise idle machine, timed directly. It is a two-device sync test and it
# earns most of that honestly.
#
# Serial, 329s sits comfortably inside 600. Under -j 8 on a 16GB box with
# other work running, the same file was killed at the timeout mid-run, and
# then passed standing alone moments later. So the old bound was not catching
# a hang; it was catching contention.
#
# 1200 keeps the property that actually matters -- a genuine hang is
# unbounded, so it still gets caught, just later -- while giving the one
# known slow file roughly 3.6x its measured runtime of headroom. If a file
# ever legitimately approaches THIS bound, that is the outlier worth
# investigating the original comment had in mind.
PER_FILE_TIMEOUT_SECONDS = int(os.environ.get('AURA_TEST_RUNNER_TIMEOUT_S', '1200'))

# Named, measured exceptions to the bound above -- never a quiet global raise.
# retail_v42_till_allocation_test.py (Track S, 2026-09-30): 14 tests, each one
# REAL multi-process computers (every login is a subprocess booting the app)
# driving main-computer / release / reinstall scenarios; measured 1822.88s
# (30m22s) standing alone, 14/14 passing, killed at 1200s inside a -j 4 run.
# It is slow because it is honest, not because it hangs. 3600s keeps ~2x
# headroom; a genuine hang is still caught. An explicit
# AURA_TEST_RUNNER_TIMEOUT_S (a test of the timeout path) wins over this list.
# retail_p1b_numbering_twodevice_test.py (ROADMAP residual D6, closed
# 2026-10-12): two real computers numbering receipts; measured 17m18s (1038s)
# standing alone, 8/8 passing (2026-10-05), and killed at 1200s inside a -j 6
# run on 2026-10-12 while two other crews were also running suites -- the
# only red of 590 files. Same reasoning as above: 2400s is ~2x its solo time.
SLOW_FILE_TIMEOUT_SECONDS = {
    'retail_v42_till_allocation_test.py': 3600,
    'retail_p1b_numbering_twodevice_test.py': 2400,
}


def _timeout_for(path) -> int:
    if 'AURA_TEST_RUNNER_TIMEOUT_S' in os.environ:
        return PER_FILE_TIMEOUT_SECONDS
    return max(PER_FILE_TIMEOUT_SECONDS, SLOW_FILE_TIMEOUT_SECONDS.get(Path(path).name, 0))

# Default worker count, used only when -j/--jobs is not given. Capped, and
# never hardcoded to one machine's core count:
#   * -1 leaves a core free for the OS, an IDE, or -- concretely true of
#     this repo (see CLAUDE.md) -- another agent session sharing the same
#     machine.
#   * The cap keeps a beefy build box from launching dozens of interpreter
#     + Flask-app boots at once and thrashing on memory/disk instead of
#     actually finishing faster; DEFAULT_JOBS_CAP is a ceiling on the
#     DEFAULT only -- an explicit `-j N` above it is a deliberate override
#     and is never clamped.
#   * `os.cpu_count() or 1`: the stdlib documents cpu_count() as sometimes
#     returning None, so a bare `- 1` on that could underflow to a
#     nonsensical negative worker count on the machine it's least tested
#     against.
DEFAULT_JOBS_CAP = 8


def _default_jobs():
    return max(1, min(DEFAULT_JOBS_CAP, (os.cpu_count() or 1) - 1))


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


def _child_test_env():
    """Environment for each test subprocess: the runner's own, with LAN hub
    mode OFF unless the operator of this run set AURA_SITE_RELAY_ENABLED
    explicitly.

    WHY (measured 2026-09-24 by the adversarial verifier of the sync
    hot-start fix): `init_app()` -- and now also a successful activation --
    starts the LAN site-relay election on any Windows test process that has an
    ACTIVE licence record, and the default for an unset variable is AUTOMATIC.
    During an ordinary sweep, `retail_branch_scope_enforcement_test.py` was
    found LISTENING ON 0.0.0.0:5443 and `retail_capability_guard_test.py`
    holding UDP 45455, broadcasting real signed beacons onto the office
    wifi. The election does not filter beacons by licence, so a test
    process's beacon takes part in a REAL shop's hub election on the same
    network (and makes a real till stand down if its installation id sorts
    lower). A test run must never become a hub on somebody's LAN.

    Tests that exercise hub mode on purpose set or pop the variable
    themselves, so this default reaches only the ones that never meant to
    open a LAN socket in the first place.
    """
    env = dict(os.environ)
    env.setdefault('AURA_SITE_RELAY_ENABLED', '0')
    # Same idea for the BACKUP-1 daily backup timer (2026-10-12): it arms on
    # every boot by design, so every in-process test app would carry a live
    # timer thread and take a real backup 5 minutes in. Off by default for
    # children; retail_backup_scheduler_boot_test.py sets it explicitly to
    # prove a normal boot arms it.
    env.setdefault('AURA_BACKUP_SCHEDULER', '0')
    # Measured 2026-10-07: a developer shell that points at the lab
    # (AURA_SYNC_RELAY_URL=http://127.0.0.1:5551, AURA_OWNER_LICENSING_URL)
    # leaked both into every child, and two files failed for that reason
    # alone -- identically on the pre-W4a commit 026f1176, green with the
    # variables stripped (retail_einvoicing_regression_test's no-worker check,
    # retail_sync_relay_url_validation_test's invalid-URL gate). A sweep must
    # not depend on, or talk to, the live relay/Owner of whoever runs it.
    # Tests that need these set them themselves. AURA_TEST_KEEP_LIVE_URLS=1
    # keeps them for a deliberate run against a live lab.
    if os.environ.get('AURA_TEST_KEEP_LIVE_URLS') != '1':
        for name in ('AURA_SYNC_RELAY_URL', 'AURA_OWNER_LICENSING_URL'):
            env.pop(name, None)
    return env


def run_one(path):
    start = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, '-m', 'pytest', str(path), '-q', '--no-header'],
            cwd=str(ROOT), capture_output=True, text=True,
            env=_child_test_env(),
            # UTF-8 explicitly, and never raise on a byte we cannot decode.
            # `text=True` alone decodes with the OS default -- cp1252 on
            # Windows -- so the first test to print Arabic killed the reader
            # thread with UnicodeDecodeError, left proc.stdout as None, and
            # crashed the RUNNER. Every test had passed; the harness fell over
            # collecting the results, which reads in CI as a failed build with
            # no failing test in it.
            encoding='utf-8', errors='replace',
            # See PER_FILE_TIMEOUT_SECONDS's own comment: without this, one
            # hung file hangs the whole run forever, no matter -j.
            timeout=_timeout_for(path),
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.time() - start
        # subprocess already killed the child and waited for it by the time
        # this exception is raised -- nothing leaked, this is just turning
        # a hang into an ordinary failed-result dict instead of an
        # exception that would otherwise have to propagate out of a worker
        # thread (see _run_guarded for why that matters under -j > 1).
        leftover = (exc.stdout or '').strip().splitlines()
        return {
            'path': str(path.relative_to(ROOT)),
            'ok': False,
            'summary': f'TIMEOUT after {_timeout_for(path)}s -- subprocess killed',
            'duration_s': round(duration, 2),
            'stdout_tail': '\n'.join(leftover[-15:]) if leftover else '(no output before timeout)',
        }
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
    try:
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
            # See PER_FILE_TIMEOUT_SECONDS's own comment: without this, one
            # hung file hangs the whole run forever, no matter -j.
            timeout=_timeout_for(path),
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.time() - start
        leftover = (exc.stdout or '').strip().splitlines()
        return {
            'path': str(path.relative_to(ROOT)),
            'ok': False,
            'summary': f'TIMEOUT after {_timeout_for(path)}s -- subprocess killed',
            'duration_s': round(duration, 2),
            'stdout_tail': '\n'.join(leftover[-15:]) if leftover else '(no output before timeout)',
        }
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


def _parse_args(argv):
    """
    Split argv into (jobs, suite_names). Recognises -j/--jobs in space,
    '=', and compact '-jN' forms; every other token is left untouched as a
    positional suite name -- exactly today's behaviour ("whatever isn't a
    flag is a suite name"), just with one new flag. Unknown suite names are
    still silently ignored downstream by main()'s existing
    `if a in SUITES` filter, unchanged from before -j existed.
    """
    jobs_token = None
    suites = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ('-j', '--jobs'):
            if i + 1 >= len(argv):
                raise ValueError(f"{tok} requires a value")
            jobs_token = argv[i + 1]
            i += 2
            continue
        if tok.startswith('--jobs='):
            jobs_token = tok[len('--jobs='):]
            i += 1
            continue
        if tok.startswith('-j') and len(tok) > 2 and tok[2:].isdigit():
            jobs_token = tok[2:]
            i += 1
            continue
        suites.append(tok)
        i += 1

    if jobs_token is None:
        jobs = _default_jobs()
    else:
        try:
            jobs = int(jobs_token)
        except ValueError:
            raise ValueError(f"-j/--jobs value must be an integer, got {jobs_token!r}")
        if jobs < 1:
            raise ValueError(f"-j/--jobs must be >= 1, got {jobs}")
        # Deliberately NOT clamped to DEFAULT_JOBS_CAP -- that cap only
        # protects the unattended default; an explicit -j is the caller
        # overriding it on purpose (see that constant's comment).

    return jobs, suites


def _run_guarded(entry):
    """
    Execute one (path, runner_fn) pair and never let it raise. Any
    exception -- whether from the subprocess call itself (run_one/
    run_one_js already convert a hang into a result dict, but this is a
    second line of defence for anything else, e.g. a bug in a future
    runner function) -- becomes an ordinary failed-result dict instead of
    propagating out of the worker thread. Required so that one file's
    crash can't take down the ThreadPoolExecutor future for every OTHER
    file scheduled alongside it: a worker crashing must not lose the other
    workers' results or hang the run.
    """
    path, runner_fn = entry
    try:
        return runner_fn(path)
    except Exception as exc:  # noqa: BLE001 -- deliberately catch-all, see docstring
        return {
            'path': str(path.relative_to(ROOT)),
            'ok': False,
            'summary': f'RUNNER ERROR: {type(exc).__name__}: {exc}',
            'duration_s': 0.0,
            'stdout_tail': f'{type(exc).__name__}: {exc}',
        }


def _run_files(files, jobs, on_result=None):
    """
    Run `files` (a list of (path, runner_fn) tuples, already in stable
    discovery order) with up to `jobs` concurrent subprocesses each, and
    return their result dicts in THAT SAME discovery order -- never
    completion order. A report whose line order changes run to run is much
    harder to diff, so this is load-bearing, not cosmetic (see module
    docstring, "OUTPUT MUST STAY DETERMINISTIC").

    MUST_RUN_SERIAL members are submitted to their own single-worker pool
    so two of them can never overlap with each other, while remaining free
    to overlap with ordinary parallel files (see that constant's comment
    for why that's safe).

    `on_result(index, result)`, if given, fires the instant each file's
    result becomes the NEXT one due in discovery order -- not the instant
    it finishes. This is what makes -j 1 byte-for-byte identical to the
    pre-parallel version of this script: with a single worker, results can
    only ever arrive in submission order, so the callback fires immediately
    after each file completes, exactly like the old direct call-then-print
    loop did. At -j > 1 it still reports strictly in order even though the
    underlying subprocesses finish out of order -- a fast file that
    finished early just waits to be reported until every file ahead of it
    in discovery order has also been reported.
    """
    n = len(files)
    results = [None] * n
    next_to_report = 0

    def _drain():
        nonlocal next_to_report
        while next_to_report < n and results[next_to_report] is not None:
            if on_result is not None:
                on_result(next_to_report, results[next_to_report])
            next_to_report += 1

    with ThreadPoolExecutor(max_workers=jobs) as pool, \
            ThreadPoolExecutor(max_workers=1) as serial_pool:
        # ThreadPoolExecutor, not ProcessPoolExecutor: the real concurrency
        # this script wants is at the OS-PROCESS level (each runner_fn call
        # spawns a real `pytest`/`node` subprocess) -- a plain thread
        # blocked inside subprocess.run() releases the GIL for the
        # duration of the wait, so N threads genuinely run N subprocesses
        # at once. A process pool would add pickling and Windows-spawn
        # overhead for zero extra concurrency, since the actual work never
        # runs inside these worker threads/processes at all.
        future_to_index = {}
        for i, entry in enumerate(files):
            path = entry[0]
            executor = serial_pool if path.name in MUST_RUN_SERIAL else pool
            future_to_index[executor.submit(_run_guarded, entry)] = i

        for future in as_completed(future_to_index):
            i = future_to_index[future]
            try:
                results[i] = future.result()
            except Exception as exc:  # pragma: no cover -- _run_guarded
                # already swallows everything a runner_fn can raise; this
                # only fires for a framework-level reason (e.g. a
                # cancelled future), which this script never triggers. Kept
                # anyway so a freak case degrades to "counted as failed"
                # instead of silently shrinking the report by one line.
                path = files[i][0]
                results[i] = {
                    'path': str(path.relative_to(ROOT)),
                    'ok': False,
                    'summary': f'RUNNER ERROR: {type(exc).__name__}: {exc}',
                    'duration_s': 0.0,
                    'stdout_tail': f'{type(exc).__name__}: {exc}',
                }
            _drain()

    return results


def main():
    try:
        jobs, args = _parse_args(sys.argv[1:])
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 1

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

    print(f"Running {len(files)} file(s) with -j {jobs}...")

    def _report(_index, r):
        status = 'PASS' if r['ok'] else 'FAIL'
        print(f"[{status}] {r['path']}  ({r['duration_s']}s)  {r['summary']}")

    results = _run_files(files, jobs, on_result=_report)

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
