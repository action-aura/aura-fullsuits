"""Aura FullSuits -- bounded-parallelism regression coverage for
products/run_all_tests.py (retail-hardware-viewports task, 2026-09-22).

WHAT THIS FILE IS TESTING, AND WHY IT DOES NOT TOUCH THE REAL SUITES.

run_all_tests.py's whole reason to exist is running dozens/hundreds of
*_test.py files as separate `pytest` subprocesses (see that file's own
module docstring for the import-caching bug this avoids). Adding bounded
parallelism (`-j N`) means: run up to N of those subprocesses at once,
without weakening the one guarantee the file exists to provide -- process
isolation per file.

A real suite (products/retail/tests is ~186 files, ~40 minutes serially)
would make this test file itself take 40 minutes and would not even prove
anything about the SCHEDULER, since the real suites don't exercise timing
edges on purpose. So every test here builds a SMALL, throwaway suite of
synthetic *_test.py files (written under pytest's own `tmp_path`, deleted
automatically after the test) and drives run_all_tests.py against THAT --
via its real public entry point, `main()`, with `SUITES` monkeypatched to
point at the synthetic directory and `sys.argv` monkeypatched to the
argument list under test. This is white-box only in the sense that it
imports the module directly (this codebase's usual "no conftest.py, each
file bootstraps itself" convention -- see e.g. retail_ar_ap_totals_test.py)
instead of shelling out to `python products/run_all_tests.py`; SUITES is a
hardcoded dict of real product directories, so there is no CLI-only way to
point the real script at a synthetic suite, and shelling out would have
made every synthetic file invisible to it.

Every synthetic file really is collected and run by a real `pytest`
subprocess (run_one() is never mocked) -- these tests prove the SCHEDULER,
not a stand-in for it.

Covers, per the task brief:
  * -j 1 and -j 4 give the same pass/fail tally AND the same report line
    order (test_serial_and_parallel_same_tally_and_order).
  * a failing file fails, and the exit code is non-zero, under both
    (same test).
  * files genuinely overlap in time at -j > 1, and never overlap at -j 1
    (test_files_run_concurrently_under_j_greater_than_1).
  * each file gets its own isolated temp dir even while several run at once
    (test_each_file_gets_isolated_temp_app_data) -- this is the one
    MUTATION-PROVEN by hand (see the task's final report for the real
    before/after pytest output): temporarily forcing every synthetic file
    onto ONE shared directory (flip FORCE_SHARED_FOR_MUTATION_PROOF below
    to True) makes this test fail, which is the whole point -- a check
    that cannot fail when the thing it guards is actually broken is not a
    check.
  * MUST_RUN_SERIAL members never overlap with each other
    (test_must_run_serial_files_never_overlap_each_other).
  * a hanging file times out without losing the other files' results
    (test_hanging_file_times_out_without_losing_other_results).
  * an exception raised inside the runner's OWN code (not a subprocess
    failure) does not lose sibling results either
    (test_runner_level_exception_does_not_lose_other_results) -- this one
    calls the scheduler directly with a fake runner_fn instead of a real
    subprocess, since "a bug in run_all_tests.py itself" isn't something a
    well-behaved synthetic pytest file can trigger from the outside.

Timing notes (measured on the dev machine this was written on, 16 logical
cores): a bare `python -m pytest <trivial file>` subprocess costs roughly
2 real seconds of pure interpreter+pytest-import startup before it even
reaches the test body. Every threshold below was chosen with that fixed
cost already priced in, and with a generous margin on top of it, per the
task brief's explicit "make the margin generous enough not to be flaky on
a loaded machine" -- this machine routinely has other agent sessions
running concurrently (see CLAUDE.md).

Run:
    pytest products/retail/tests/retail_test_runner_parallel_test.py -v
"""
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

import pytest

SUITE_ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = SUITE_ROOT / 'products' / 'run_all_tests.py'

# products/ is not a package (no __init__.py) and run_all_tests.py is a
# script, not a library -- loaded by file path rather than `import
# products.run_all_tests`, matching this test suite's usual "no shared
# conftest.py, each file bootstraps itself" convention.
_spec = importlib.util.spec_from_file_location('run_all_tests_under_test', RUNNER_PATH)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


# ── Synthetic file templates ────────────────────────────────────────────────
#
# Deliberately dependency-free (no app.py, no Flask, no AURA_APP_DATA) --
# these test the SCHEDULER, not the product, so the fastest and least
# fragile synthetic body is the right one.

_SIMPLE_TEMPLATE = """\
import time


def test_body():
    time.sleep({sleep_s})
    assert {should_pass}
"""

# Writes its own [start, end] wall-clock interval to a JSON file in the
# shared coordination directory (passed via AURA_TEST_COORD_DIR, inherited
# from run_all_tests.py's own process since run_one()/run_one_js() never
# override `env=` on subprocess.run -- exactly like a real test file's own
# AURA_APP_DATA inherits the parent's environment before overwriting it
# inside its own process). The outer test reads these back afterwards to
# prove genuine temporal overlap (or its absence) directly from wall-clock
# evidence, not merely inferred from total run duration.
_SLEEPER_TEMPLATE = """\
import json
import os
import time
from pathlib import Path


def test_sleeper():
    coord = Path(os.environ["AURA_TEST_COORD_DIR"])
    start = time.time()
    time.sleep({sleep_s})
    end = time.time()
    (coord / "{file_id}.json").write_text(json.dumps({{"start": start, "end": end}}))
"""

# Mimics a real test file's own isolation convention (its own
# tempfile.mkdtemp()-based data dir, written to and read back after a
# sleep window that gives a concurrently-running sibling a chance to
# collide with it if isolation were ever actually broken).
#
# FORCE_SHARED_FOR_MUTATION_PROOF below controls whether these files use
# their OWN directory (normal, must stay green) or are all forced onto ONE
# shared directory (the mutation -- "hand two files the same temp dir" from
# the task brief -- must go red). See test_each_file_gets_isolated_temp_app_data.
_ISOLATION_TEMPLATE = """\
import json
import os
import tempfile
import time
from pathlib import Path


def test_isolation():
    coord = Path(os.environ["AURA_TEST_COORD_DIR"])
    if os.environ.get("AURA_TEST_FORCE_SHARED_TEMPDIR") == "1":
        data_dir = coord / "forced_shared_app_data"
        data_dir.mkdir(exist_ok=True)
    else:
        data_dir = Path(tempfile.mkdtemp(prefix="aura_synth_{file_id}_"))
    marker = data_dir / "owner.txt"
    marker.write_text("{file_id}")
    (coord / "{file_id}.dir").write_text(str(data_dir))
    time.sleep({sleep_s})
    seen = marker.read_text() if marker.exists() else "<missing>"
    assert seen == "{file_id}", (
        f"isolation broken: file {file_id!r} expected its own marker back, "
        f"saw {{seen!r}} in {{data_dir}}"
    )
"""


def _write(dir_, name, content):
    (dir_ / name).write_text(content, encoding='utf-8')


def _read_interval(coord_dir, file_id):
    return json.loads((coord_dir / f'{file_id}.json').read_text())


def _overlap(a, b):
    return a['start'] < b['end'] and b['start'] < a['end']


def _run(monkeypatch, capsys, suite_dir, jobs):
    """
    Drive run_all_tests.py's real main() against `suite_dir` at `-j jobs`,
    exactly as a human would from the command line, and return
    (returncode, stdout_text).

    Also monkeypatches ROOT to suite_dir's parent (every synthetic suite in
    this file is created as `tmp_path / 'suite'`, so that parent is
    `tmp_path`). ROOT is only ever used downstream to compute each result's
    *display* path via `path.relative_to(ROOT)` and as the subprocess cwd
    -- both irrelevant to what these dependency-free synthetic files
    actually test -- but it does have to be a real ancestor of every
    discovered file, or relative_to() raises. In real usage every SUITES
    entry already lives under the real ROOT (products/, commercial_runtime/
    -- see that dict), so this is purely a synthetic-fixture concern, not a
    production one.
    """
    monkeypatch.setattr(runner, 'ROOT', suite_dir.parent)
    monkeypatch.setattr(runner, 'SUITES', {'synthetic': suite_dir})
    monkeypatch.setattr(sys, 'argv', ['run_all_tests.py', 'synthetic', '-j', str(jobs)])
    rc = runner.main()
    out = capsys.readouterr().out
    return rc, out


_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')
_DURATION_TOKEN_RE = re.compile(r'^\(\d+(?:\.\d+)?s\)$')
# pytest's own "-q --no-header" summary line ("1 passed in 1.12s") embeds
# its own real wall-clock timing, which -- exactly like this script's own
# "(N.NNs)" duration token -- legitimately differs between ANY two runs of
# the same file, -j 1 vs -j 4 included. Both timing sources have to be
# normalized away for a same-input comparison to mean anything.
_TRAILING_TIMING_RE = re.compile(r'\s+in\s+[\d.]+s\s*$')


def _report_lines(stdout_text):
    """[PASS]/[FAIL] lines in the order main() printed them, with every
    source of run-to-run timing noise stripped out (ANSI color codes from
    the captured pytest subprocess output, this script's own "(N.NNs)"
    token, and pytest's own "in N.NNs" summary suffix) -- what's left is
    exactly the status, path and pass/fail summary, which for identical
    input MUST be identical regardless of -j."""
    lines = []
    for raw in stdout_text.splitlines():
        line = _ANSI_RE.sub('', raw)
        if not (line.startswith('[PASS]') or line.startswith('[FAIL]')):
            continue
        status, rest = line.split(']', 1)
        parts = [p for p in rest.strip().split('  ') if p]
        parts = [p for p in parts if not _DURATION_TOKEN_RE.match(p)]
        parts = [_TRAILING_TIMING_RE.sub('', p) for p in parts]
        lines.append(status + '] ' + '  '.join(parts))
    return lines


def _tally_line(stdout_text):
    for line in stdout_text.splitlines():
        if ' file(s) run, ' in line:
            return line
    return None


# ── 1. -j 1 and -j 4: same tally, same order, failure survives both ────────

def test_serial_and_parallel_same_tally_and_order(tmp_path, monkeypatch, capsys):
    """
    Four files with DELIBERATELY staggered sleep times, in the REVERSE of
    their discovery order (aaa sleeps longest, ddd barely sleeps at all) --
    so that under real concurrency at -j 4 they finish in roughly ddd, ccc,
    bbb, aaa order, the opposite of discovery order (aaa, bbb, ccc, ddd).
    If the report were ever printed in completion order instead of
    discovery order, this specific arrangement would show it immediately
    (see this file's own mutation-proof notes in the task report -- this
    exact fixture is what was used to prove the ordering check is not
    vacuous).
    """
    suite = tmp_path / 'suite'
    suite.mkdir()
    _write(suite, 'aaa_slow_test.py', _SIMPLE_TEMPLATE.format(sleep_s=1.0, should_pass=True))
    _write(suite, 'bbb_mid_test.py', _SIMPLE_TEMPLATE.format(sleep_s=0.6, should_pass=True))
    _write(suite, 'ccc_fail_test.py', _SIMPLE_TEMPLATE.format(sleep_s=0.3, should_pass=False))
    _write(suite, 'ddd_fast_test.py', _SIMPLE_TEMPLATE.format(sleep_s=0.05, should_pass=True))

    rc1, out1 = _run(monkeypatch, capsys, suite, jobs=1)
    rc4, out4 = _run(monkeypatch, capsys, suite, jobs=4)

    lines1 = _report_lines(out1)
    lines4 = _report_lines(out4)

    assert lines1 == lines4, (
        f"-j 1 and -j 4 report DIFFERENT lines/order for identical input:\n"
        f"-j1: {lines1}\n-j4: {lines4}"
    )
    # Discovery order is alphabetical (see discover()): aaa, bbb, ccc, ddd.
    file_order = [Path(l.split(']', 1)[1].split()[0]).name for l in lines1]
    assert file_order == ['aaa_slow_test.py', 'bbb_mid_test.py', 'ccc_fail_test.py', 'ddd_fast_test.py']

    # The failing file fails under BOTH job counts.
    fail_lines1 = [l for l in lines1 if l.startswith('[FAIL]')]
    fail_lines4 = [l for l in lines4 if l.startswith('[FAIL]')]
    assert len(fail_lines1) == 1 and 'ccc_fail_test.py' in fail_lines1[0]
    assert len(fail_lines4) == 1 and 'ccc_fail_test.py' in fail_lines4[0]

    # Same tally line, verbatim (no duration content in it).
    tally1, tally4 = _tally_line(out1), _tally_line(out4)
    assert tally1 == tally4 == '4 file(s) run, 3 passed, 1 failed'

    # Non-zero exit under both.
    assert rc1 != 0
    assert rc4 != 0


# ── 2. Real concurrency at -j > 1, real seriality at -j 1 ──────────────────

def test_files_run_concurrently_under_j_greater_than_1(tmp_path, monkeypatch, capsys):
    """
    Four files that each sleep 1s and record their own [start, end]
    wall-clock interval. Proves concurrency two independent ways:
      (a) wall-clock: -j 4 must be meaningfully faster than -j 1 (generous
          margin -- see the module docstring's timing notes).
      (b) direct evidence: at -j 4, at least one PAIR of files' recorded
          intervals must genuinely overlap; at -j 1, NO pair may overlap
          (this is "-j 1 reproduces today's exact serial behaviour",
          checked at the wall-clock level, not just the report level).
    """
    coord = tmp_path / 'coord'
    coord.mkdir()
    monkeypatch.setenv('AURA_TEST_COORD_DIR', str(coord))

    suite = tmp_path / 'suite'
    suite.mkdir()
    ids = ['s1', 's2', 's3', 's4']
    for file_id in ids:
        _write(suite, f'{file_id}_test.py', _SLEEPER_TEMPLATE.format(sleep_s=1.0, file_id=file_id))

    t0 = time.time()
    rc1, _ = _run(monkeypatch, capsys, suite, jobs=1)
    serial_wall = time.time() - t0
    serial_intervals = {fid: _read_interval(coord, fid) for fid in ids}

    # Fresh coordination dir for the parallel run so the two runs' JSON
    # files never mix.
    coord2 = tmp_path / 'coord2'
    coord2.mkdir()
    monkeypatch.setenv('AURA_TEST_COORD_DIR', str(coord2))

    t0 = time.time()
    rc4, _ = _run(monkeypatch, capsys, suite, jobs=4)
    parallel_wall = time.time() - t0
    parallel_intervals = {fid: _read_interval(coord2, fid) for fid in ids}

    assert rc1 == 0 and rc4 == 0

    # (a) Wall-clock: generous margin, per the task brief. Empirically on
    # the dev machine, serial ~= 4 * (2s startup + 1s sleep) ~= 12s and
    # parallel ~= one startup + 1s sleep ~= 3-5s (a ~30-40% ratio) -- 0.6
    # leaves enormous headroom before this could ever flake from load.
    assert parallel_wall < serial_wall * 0.6, (
        f"-j 4 ({parallel_wall:.2f}s) was not meaningfully faster than "
        f"-j 1 ({serial_wall:.2f}s) for 4 files sleeping 1s each"
    )

    # (b) Direct overlap evidence.
    serial_pairs_overlap = any(
        _overlap(serial_intervals[a], serial_intervals[b])
        for i, a in enumerate(ids) for b in ids[i + 1:]
    )
    parallel_pairs_overlap = any(
        _overlap(parallel_intervals[a], parallel_intervals[b])
        for i, a in enumerate(ids) for b in ids[i + 1:]
    )
    assert not serial_pairs_overlap, f"-j 1 files overlapped in time: {serial_intervals}"
    assert parallel_pairs_overlap, f"-j 4 files never overlapped in time: {parallel_intervals}"


# ── 3. Isolation holds even while several files run at once ────────────────

# Flip to True ONLY as a manual, temporary mutation proof (see the task's
# final report for the real pytest output both directions) -- never commit
# it as True. When True, every isolation file in the test below shares ONE
# directory instead of getting its own, which is exactly the "hand two
# files the same temp dir" break the task asked this test to survive being
# broken by.
FORCE_SHARED_FOR_MUTATION_PROOF = False


def test_each_file_gets_isolated_temp_app_data(tmp_path, monkeypatch, capsys):
    coord = tmp_path / 'coord'
    coord.mkdir()
    monkeypatch.setenv('AURA_TEST_COORD_DIR', str(coord))
    if FORCE_SHARED_FOR_MUTATION_PROOF:
        monkeypatch.setenv('AURA_TEST_FORCE_SHARED_TEMPDIR', '1')

    suite = tmp_path / 'suite'
    suite.mkdir()
    ids = ['i1', 'i2', 'i3', 'i4']
    for file_id in ids:
        _write(suite, f'{file_id}_test.py', _ISOLATION_TEMPLATE.format(sleep_s=0.8, file_id=file_id))

    rc, out = _run(monkeypatch, capsys, suite, jobs=4)

    # Every file must have passed -- i.e. every file's own marker still
    # said what IT wrote, never a sibling's id.
    assert rc == 0, f"isolation check failed under concurrency:\n{out}"

    # And the directories they actually used must be pairwise distinct.
    dirs = {fid: (coord / f'{fid}.dir').read_text() for fid in ids}
    assert len(set(dirs.values())) == len(ids), f"two files shared a directory: {dirs}"


# ── 4. MUST_RUN_SERIAL members never overlap with each other ───────────────

def test_must_run_serial_files_never_overlap_each_other(tmp_path, monkeypatch, capsys):
    coord = tmp_path / 'coord'
    coord.mkdir()
    monkeypatch.setenv('AURA_TEST_COORD_DIR', str(coord))

    suite = tmp_path / 'suite'
    suite.mkdir()
    serial_ids = ['ser1', 'ser2', 'ser3']
    parallel_ids = ['par1', 'par2']
    for file_id in serial_ids + parallel_ids:
        _write(suite, f'{file_id}_test.py', _SLEEPER_TEMPLATE.format(sleep_s=0.8, file_id=file_id))

    monkeypatch.setattr(runner, 'MUST_RUN_SERIAL', frozenset(f'{fid}_test.py' for fid in serial_ids))

    # jobs=5 -- enough for all 5 files to start at once if nothing
    # restrained them, so this genuinely exercises the restraint.
    rc, _ = _run(monkeypatch, capsys, suite, jobs=5)
    assert rc == 0

    intervals = {fid: _read_interval(coord, fid) for fid in serial_ids + parallel_ids}

    for i, a in enumerate(serial_ids):
        for b in serial_ids[i + 1:]:
            assert not _overlap(intervals[a], intervals[b]), (
                f"two MUST_RUN_SERIAL files overlapped: {a}={intervals[a]} "
                f"{b}={intervals[b]}"
            )


# ── 5. A hanging file times out without losing sibling results ─────────────

def test_hanging_file_times_out_without_losing_other_results(tmp_path, monkeypatch, capsys):
    # Short timeout so this test doesn't wait for the real 600s default --
    # monkeypatching the already-imported constant directly is simpler and
    # more direct than the AURA_TEST_RUNNER_TIMEOUT_S env var for an
    # in-process test (that var is read once at import time, which has
    # already happened by the time this test runs; it exists for a real
    # end-to-end subprocess invocation of this script instead).
    monkeypatch.setattr(runner, 'PER_FILE_TIMEOUT_SECONDS', 4)

    suite = tmp_path / 'suite'
    suite.mkdir()
    _write(suite, 'hang_test.py', _SIMPLE_TEMPLATE.format(sleep_s=60, should_pass=True))
    _write(suite, 'pass_1_test.py', _SIMPLE_TEMPLATE.format(sleep_s=0.1, should_pass=True))
    _write(suite, 'pass_2_test.py', _SIMPLE_TEMPLATE.format(sleep_s=0.1, should_pass=True))
    _write(suite, 'fail_1_test.py', _SIMPLE_TEMPLATE.format(sleep_s=0.1, should_pass=False))

    t0 = time.time()
    rc, out = _run(monkeypatch, capsys, suite, jobs=4)
    wall = time.time() - t0

    # Proves the hang was actually KILLED at the timeout, not merely that
    # the run eventually finished on its own (which a 60s sleep alone would
    # also have done, just slowly, defeating the point of this test).
    assert wall < 20, f"run took {wall:.1f}s -- the timeout does not appear to have fired"

    lines = _report_lines(out)
    assert len(lines) == 4, f"expected 4 reported results, got {len(lines)}:\n{lines}"

    by_name = {Path(l.split(']', 1)[1].split()[0]).name: l for l in lines}
    assert by_name['hang_test.py'].startswith('[FAIL]')
    assert 'TIMEOUT' in by_name['hang_test.py']
    assert by_name['fail_1_test.py'].startswith('[FAIL]')
    assert by_name['pass_1_test.py'].startswith('[PASS]')
    assert by_name['pass_2_test.py'].startswith('[PASS]')

    assert rc != 0
    assert _tally_line(out) == '4 file(s) run, 2 passed, 2 failed'


# ── 6. A bug in the runner's OWN code doesn't lose sibling results ─────────

def test_runner_level_exception_does_not_lose_other_results():
    """
    Direct scheduler test, no subprocess: a runner_fn that raises is a
    stand-in for a bug in run_all_tests.py itself (as opposed to a
    subprocess merely exiting non-zero, which is an ordinary test failure,
    not a runner crash). _run_guarded must convert that into a normal
    failed-result dict rather than losing it -- or its siblings -- from the
    report.
    """
    def _boom(_path):
        raise RuntimeError('boom from runner_fn')

    def _ok(path):
        return {
            'path': path.name, 'ok': True, 'summary': 'ok',
            'duration_s': 0.0, 'stdout_tail': '',
        }

    # Real paths under runner.ROOT (don't need to exist -- _run_guarded's
    # fallback only calls .relative_to(ROOT) and .name on them, never opens
    # them), so this stays a pure in-memory scheduler test with zero
    # subprocess cost.
    base = runner.ROOT / 'nonexistent_synthetic_dir'
    files = [
        (base / 'a_test.py', _ok),
        (base / 'b_test.py', _boom),
        (base / 'c_test.py', _ok),
    ]

    results = runner._run_files(files, jobs=3)

    assert len(results) == 3, "a crashing runner_fn must not shrink the result list"
    assert results[0]['ok'] is True and results[0]['path'] == 'a_test.py'
    assert results[2]['ok'] is True and results[2]['path'] == 'c_test.py'
    assert results[1]['ok'] is False
    assert 'RUNNER ERROR' in results[1]['summary']
    assert 'boom from runner_fn' in results[1]['stdout_tail']
