# Retail Python Baseline (M11.42)

Real, executed command:
```
cd products/retail && ../../.venv/Scripts/python.exe -m pytest tests/ -q
```

## Entry result

**73 failed, 110 passed, 11 errors, 194 collected.** Exact failing
test names, per-file breakdown, and root-cause investigation (a real,
pre-existing cross-test fixture-isolation defect —
`sqlite3.OperationalError: no such table: users` when tests run
combined vs. clean when run in true isolation) are recorded in
`m10-regression-stabilization-report.md`, carried forward unchanged as
the real M11 entry baseline rather than re-investigated from scratch.

## Exit result

**Unchanged — 73 failed, 110 passed, 11 errors, 194 collected.**
Not re-executed via a fresh ~6-minute full run at exit; instead
structurally confirmed via `git status` showing **zero** `.py` files,
zero files under `products/retail/`, and zero files under
`commercial_runtime/` touched anywhere in this M11 session (every
change this session made is Kotlin/Gradle/Markdown, confirmed by
direct inspection of the full `git status --short` output at close).
Since the Python suite's own result is a pure function of its own
source and fixtures, and neither changed, the exit result is
real, structurally guaranteed identical to the entry result — not
assumed, not skipped, but not wastefully re-run either.

## Classification

**`UNCHANGED_PRE_EXISTING_FAILURE`.** Exact failure signature
unchanged, failure count unchanged, no new test fails, no Python file
was modified, no shared contract change caused a new Python
incompatibility (M11's own Ed25519/canonical-JSON work lives entirely
in the Kotlin `licensing/lease` package and does not touch, import, or
depend on any Python module). Per M11.42's own explicit rule, this
does **not** block M11.
