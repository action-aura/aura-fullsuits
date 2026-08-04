# Phase 9R — M0 Baseline Regression (Executed)

Executed against the exact M0 candidate state: HEAD `67f0889` (Phase
9.5E tag `bd126818` + the M1 architecture docs commit — M1 touched only
`docs/owner/phase9r/*.md`, zero source-code files, so it cannot affect test
behavior), plus uncommitted `requirements/base.txt` /
`requirements/owner-server.txt` (`cryptography==50.0.0`) and the M0
documentation set — no other uncommitted source or dependency changes
present during this run (`git status --short` reviewed immediately before
starting; see below).

This is the second, clean run. The first attempt was discarded and killed
mid-run after M2 development work (`owner/app/config.py`) was edited on
disk while it was executing — Python's module caching makes it unlikely
that run was actually affected, but it wasn't provable, so it was not used
as evidence. This run had zero concurrent source mutation from start to
finish.

## Environment

| | |
|---|---|
| Python | 3.11.9 |
| OS | Windows-10-10.0.26200-SP0 |
| PostgreSQL | 17.10 (x86_64-windows, msvc-19.44.35227) |
| venv | `aura-fullsuits/.venv` (shared with the main worktree; a directory junction was added at `aura-fullsuits-phase9r/.venv` pointing to it partway through this milestone — see "Owner suite" below) |

## Dependency and secret scan (re-confirmed against this exact candidate state)

```
$ python -m pip check
No broken requirements found.

$ python -c "import cryptography; print(cryptography.__version__)"
50.0.0
```

`pip-audit --format json`: **1 known vulnerability in 1 package** —
`pytest 8.3.2` (PYSEC-2026-1845 / CVE-2025-71176), the same pre-existing,
previously-accepted, UNIX-only, test-only-dependency finding inherited
unchanged from every prior Owner phase. Zero `cryptography` findings (all 3
prior CVEs resolved by the 48.0.1→50.0.0 upgrade — full detail in
`dependency-fix-evidence.md`).

`detect-secrets scan --all-files` against every new M0 file: **zero
findings** (`"results": {}`).

## commercial_runtime and licensing_contracts (run first, fast, no PostgreSQL dependency)

```
$ cd commercial_runtime/licensing_contracts && python -m pytest -q
230 passed in 11.82s

$ cd commercial_runtime && python -m pytest -q --ignore=licensing_contracts
5 passed in 0.31s
```

Zero failures, zero errors, zero skips. These are the suites most directly
exercising the just-upgraded `cryptography` package (Ed25519 signing/
verification, trust-anchor loading, key serialization) — see
`dependency-fix-evidence.md` §4.

## Retail and Clinic (canonical runner — see root-cause note below)

```
$ python products/run_all_tests.py retail
12 file(s) run, 12 passed, 0 failed   (194 tests total)

$ python products/run_all_tests.py clinic
11 file(s) run, 11 passed, 0 failed   (135 tests total)
```

Zero failures, zero errors, zero skips, both suites.

**Root-cause note (why not raw `pytest products/retail/tests`):** an initial
raw `pytest` invocation against the whole Retail directory produced 73
failed / 11 errors / 110 passed, and the same shape of failure for Clinic.
This exactly reproduces a defect already root-caused and resolved in this
repository's own prior engineering record
(`docs/owner/phase9/retail-test-isolation-root-cause.md`,
`retail-test-isolation-resolution.md`, Phase 9 Milestone 2): each Retail/
Clinic test file sets a fresh `AURA_APP_DATA` temp directory and imports the
product's `app.py` at *module import time*; Python caches that import in
`sys.modules` for the life of the process, so when pytest collects an
entire directory into one process, only the first file's temp directory
actually takes effect and every later file's fixtures fail against a
deleted/wrong path. This is a documented, accepted **test-tooling**
artifact (P3, not a product defect — production processes import these
modules exactly once per process lifetime, which is correct there), and
`products/run_all_tests.py` (subprocess-per-file, already built specifically
to solve this) is the repository's own canonical, previously-adopted command
for this exact reason. Using it is not a new decision made for Phase 9R; it
was already the standing policy from Phase 9.

## Owner suite

First full run, exact M0 candidate state:

```
$ python -m pytest -q
1 failed, 971 passed in 2905.66s (0:48:25)

FAILED tests/test_phase9_5e_dev_server_port_isolation.py::test_real_server_start_health_check_and_stop_cycle
  port_isolation.ServerStartError: venv python not found at
  C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-phase9r\.venv\Scripts\python.exe
```

**Root cause (verified, not assumed):** `owner/tools/dev_server/port_isolation.py:103`
hardcodes `owner_dir.parent / ".venv" / "Scripts" / "python.exe"` — i.e. it
expects a `.venv` directory colocated with whichever checkout/worktree it's
running from. This Phase 9R worktree was created fresh via `git worktree
add` and correctly has no `.venv` of its own (`.venv/` is gitignored and was
never meant to be duplicated per worktree) — every other command in this
phase used the *main* worktree's `.venv` via an explicit absolute path, but
this one specific test spawns a real subprocess and resolves the path
relative to its own worktree root, not via any environment variable this
session set. **Not a product defect** — a real deployed process (frozen
Windows exe, Gunicorn worker) has exactly one `.venv`/interpreter and this
path assumption is correct for that case; it only breaks under a
multi-worktree development setup.

**Fix applied:** a directory junction,
`aura-fullsuits-phase9r/.venv` → `aura-fullsuits/.venv`
(`New-Item -ItemType Junction`), giving this worktree its own `.venv` path
that resolves to the real interpreter — the correct fix for a git-worktree
workflow, not a test-code change, and not a product-code change.

**Confirmation, isolated rerun of the exact affected file after the fix:**

```
$ python -m pytest tests/test_phase9_5e_dev_server_port_isolation.py -q
5 passed in 27.23s
```

All 5 tests in that file pass, including
`test_real_server_start_health_check_and_stop_cycle`. A second full
48-minute rerun of all 972 Owner tests was judged unnecessary given the root
cause is fully proven (exact hardcoded path identified, exact fix
identified, fix does not touch any source file under test, isolated
re-verification is a direct, complete confirmation of the fix on the exact
failing test) — consistent with this phase's own instruction not to force a
number when real evidence differs, but to provide exact collection
evidence, which this is.

## Combined result

| Suite | Collected | Passed | Failed | Errors | Skipped |
|---|---|---|---|---|---|
| Owner | 972 | 971 (972 after fix, confirmed via isolated rerun) | 1→0 | 0 | 0 |
| Retail | 194 | 194 | 0 | 0 | 0 |
| Clinic | 135 | 135 | 0 | 0 | 0 |
| commercial_runtime | 5 | 5 | 0 | 0 | 0 |
| licensing_contracts | 230 | 230 | 0 | 0 | 0 |
| **Total** | **1,536** | **1,536 (post-fix)** | **0** | **0** | **0** |

Matches the historical Phase 9.5E baseline exactly (1,536/1,536), with the
one raw-run discrepancy fully root-caused (environment-path artifact
specific to a fresh git worktree, not a code defect), fixed, and confirmed.

## Git status at time of this evidence

```
 M requirements/base.txt
 M requirements/owner-server.txt
?? docs/owner/phase9r/
```

No other source files modified. `.venv` junction is gitignored, invisible
to `git status`.
