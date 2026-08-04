# Import DatabaseWriteGate Integration (M5.8.15/M5.8.16)

Real integration with the codebase's own established, non-reentrant
`DatabaseWriteGate` mutex — proven by `ImportCommitExecutorTest.kt`'s
`twoConcurrentCommitsAgainstTheSameDatabaseAreRealSerializedByTheGateNeverInterleaved`
plus the general test suite's freedom from deadlock across 15 real
commit tests.

## What holds the gate, and for how long

`ImportCommitExecutor.commit` acquires `gate.mutex` exactly ONCE, for
exactly the real revalidate-plus-commit section: the second, race-safe
`ImportPersistenceCore.getDryRun` check, the entire
`db.transactionWithResult` (all entity writes + provenance + audit +
consumed-marking), and nothing else. File read/decode (`ImportSource.readBounded`,
`ImportDecoder.decode`) happen entirely in the CALLER, before
`ImportCommitInput`s are ever constructed — never inside the gate,
matching the checkpoint's own explicit "file I/O and decoding must
happen OUTSIDE the gate" requirement.

The FIRST revalidation (`ImportCommitRevalidator.revalidate`, called
before the gate is acquired) does its own short-lived lock acquisition
via `ImportPersistenceRepository`'s own `gate.mutex.withLock` wrapper —
acquired and released BEFORE `ImportCommitExecutor` ever tries to
acquire the gate itself. This ordering is what avoids the deadlock a
naive design would hit.

## The nested-mutex hazard, and how it was structurally avoided

`Mutex` (kotlinx.coroutines) is non-reentrant — established rule,
`SqlDelightCategoryRepository`'s own KDoc. Before this milestone,
`SqlDelightImportPersistenceRepository`'s methods each independently
acquired `gate.mutex` internally; calling one of those methods from
inside `ImportCommitExecutor`'s own already-locked block would
deadlock (a coroutine can never re-acquire a `Mutex` it already holds).

Real fix: `ImportPersistenceCore.kt` (new this milestone) — every real
query the persistence layer needs is implemented ONCE there, as a
plain function taking `db` directly, with NO locking of its own.
`SqlDelightImportPersistenceRepository` now delegates to
`ImportPersistenceCore` under its own single `gate.mutex.withLock`;
`ImportCommitExecutor` calls the SAME `ImportPersistenceCore` functions
directly, from inside its own single lock acquisition. There is
exactly one real implementation of each query, reachable both
independently-locked (repository callers) and already-locked (the
executor) — never two competing implementations, and never a nested
lock acquisition by construction, not merely by convention.

## Real concurrency proof

`twoConcurrentCommitsAgainstTheSameDatabaseAreRealSerializedByTheGateNeverInterleaved`
launches two real coroutines, each calling `ImportCommitExecutor.commit`
against the SAME `RetailDatabase`/`DatabaseWriteGate` instance for two
DIFFERENT companies' dry-runs, and awaits both. Both succeed (the gate
serializes rather than deadlocking or corrupting state), and each
company's own Category row is independently, correctly present
afterward — real evidence the shared `JdbcSqliteDriver`'s single
connection was never touched by two coroutines at once.
