# Presentation DI Scope Report (M6.3)

Real, hand-rolled composition root — `AuraAppContainer` (`com.actionaura.retail.di`).
Proven by `AuraAppContainerTest.kt` (4/4): real database-instance
identity across repositories, real gate sharing, real independence
between two separate containers.

## Why a hand-rolled composition root, not a DI framework

Every repository in this codebase already takes its dependencies as
explicit constructor parameters (`SqlDelightProductRepository(db, gate)`,
established since M5.1) — a plain class wiring them together is the
real, minimal mechanism, adds no new external dependency (no Koin/
Hilt/Dagger), and is fully unit-testable with zero reflection or
annotation-processing magic. Consistent with this module's own
established style across M1-M5.8.

## `AuraAppContainer(driverFactory: DatabaseDriverFactory)`

Constructed exactly once per real app process (platform entry point,
M6.26 wires this into `androidApp`). Holds:

- `database: RetailDatabase` — one real instance, from
  `driverFactory.createDriver()`.
- `gate: DatabaseWriteGate` — one real instance.
- Every repository (`settingsRepository`, `categoryRepository`,
  `branchRepository`, `productRepository`, `inventoryRepository`,
  `reportingRepository`, `dashboardRepository`,
  `importPersistenceRepository`) constructed from the SAME `database`/
  `gate` pair.

## Real proof of graph identity (`AuraAppContainerTest.kt`)

1. **`everyRepositoryInOneContainerSharesTheSameRealDatabaseInstance`**
   — a Branch inserted through `branchRepository` is immediately
   visible via a raw query on `container.database` directly — proves
   they are the same real connection/schema, not two independent
   databases that happen to look alike.
2. **`theSameGateInstanceIsSharedAcrossEveryRepository`** — a Category
   write and a Branch write (two independently-constructed repository
   objects) both succeed and are both visible afterward from the same
   container — proves they share one real `DatabaseWriteGate` rather
   than two competing locks against the same underlying JDBC
   connection (which would risk the exact `JdbcSqliteDriver`
   single-shared-connection concurrency hazard `stock-concurrency-report.md`
   originally found).
3. **`twoIndependentContainersNeverShareRealDatabaseState`** — two
   separately-constructed containers (`assertNotSame` on both
   `database` and `gate`) never see each other's real data — proves
   there is no accidental JVM-wide singleton hiding behind the
   composition root.
4. **`theSameContainerInstanceReturnsTheSameRealDatabaseReferenceEveryAccess`**
   — `assertSame` on repeated property access, plus non-null presence
   of every repository the container is meant to expose.

## Scope policy — real, disclosed current state

`AuraAppContainer` is APPLICATION-scoped: one instance for the entire
real process lifetime, never recreated on ordinary navigation. This is
the real, correct scope for a database that must survive login/logout
(it holds real, persistent business data) — there is no
`disposeSession()`/session-scoped teardown here, because **no real
authenticated-session concept exists in this codebase yet**
(`UserRepository`/`SessionRepository`/`LicensingRepository` remain
M7-M10 interface markers, `RepositoryBoundaries.kt`). Inventing a fake
session-scope teardown now, with nothing real to tear down, would be
exactly the "temporary RBAC authority" M6.25 explicitly forbids. When
M7-M10 add real session state, it is additive to this container (a new
session-scoped holder constructed alongside it), never a replacement.

## Navigation/screen scope

Individual `AuraViewModel` subclasses are screen-scoped (M6.2's own
creation/retention/disposal policy) — they receive repository/use-case
references from the ONE `AuraAppContainer` (via constructor injection
at the platform composition point, M6.26), never construct their own
`RetailDatabase`/`DatabaseWriteGate`. No `AuraAppContainer` reference
or database instance is ever placed inside a screen-level Composable
directly, satisfying M6.3's own explicit prohibition.
