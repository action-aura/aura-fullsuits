# Reporting Android Driver Validation (M5.7.12)

Real, verifiable evidence for what CAN be confirmed from this Windows
host, and an honest, disclosed limitation for what cannot — the same
symmetry this initiative has applied to iOS since M0 ("no iOS success
claimed from Windows") now applied to real on-device Android execution.

## Real limitation, disclosed upfront

This host has **no `adb`, no Android emulator binary, and no configured
AVD** (`which adb`/`which emulator`/`emulator -list-avds` all real,
executed, all empty). Real on-device/emulator instrumented test
execution against the real `AndroidSqliteDriver` and a real Android
SQLite build is **not performed this milestone** — not silently skipped,
disclosed here explicitly. `shared/src/androidInstrumentedTest/` exists
as an empty scaffold directory from the original M2 KMP setup; no test
was added to it this milestone because there is no real device/emulator
here to run it against, and a test that has never actually executed is
worse than an honestly-absent one.

## What IS real and verified

### 1. The real production driver is `AndroidSqliteDriver`, not the JDBC test driver

`shared/src/androidMain/kotlin/com/actionaura/retail/platform/AndroidDatabaseDriverFactory.kt`
(real file, unchanged this milestone):

```kotlin
class AndroidDatabaseDriverFactory(private val context: Context) : DatabaseDriverFactory {
    override fun createDriver(): SqlDriver {
        val driver = AndroidSqliteDriver(schema = RetailDatabase.Schema, context = context, name = "retail_unified.db")
        driver.execute(null, "PRAGMA journal_mode=WAL", 0)
        driver.execute(null, "PRAGMA busy_timeout=30000", 0)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        return driver
    }
}
```

### 2. The exact same generated schema/queries run on both drivers

`RetailDatabase.Schema` and every generated `*Queries` class (including
`ReportingQueries`, generated from `Reporting.sq`) come from
`shared/src/commonMain/sqldelight/` — one shared source, compiled once,
used identically by `AndroidSqliteDriver` (real app) and
`JdbcSqliteDriver` (every test in this milestone). The SQL text, bound
parameter order, and generated Kotlin method signatures are real,
identical artifacts — not two independently-maintained copies that could
drift. This is the real, structural reason JDBC-driver test evidence
transfers meaningfully to the real Android driver: the same `.sq` files
produce both.

### 3. The JDBC test driver is dependency-scoped OUT of production code

`shared/build.gradle.kts`, real source-set dependency declarations:

```kotlin
val androidMain by getting {
    dependencies {
        implementation("app.cash.sqldelight:android-driver:2.3.2")   // real driver
    }
}
val androidUnitTest by getting {
    dependencies {
        implementation("app.cash.sqldelight:sqlite-driver:2.3.2")    // JDBC test-only driver
    }
}
```

`app.cash.sqldelight:sqlite-driver` (the JDBC/xerial driver every M5.7
test uses) is declared **only** in the `androidUnitTest` source set —
`commonMain` has no SQLite driver dependency at all, and `androidMain`
depends on `android-driver`, never `sqlite-driver`. A real production
Android build cannot link the JDBC/desktop driver; this is enforced by
Gradle's own source-set dependency graph, not a convention that could
silently be violated.

### 4. No Chaquopy dependency exists

Grepped every `.kts`/`.gradle`/`.toml` file in `mobile/aura-retail-unified/`
for `chaquopy` (case-insensitive): the only match is a comment in
`settings.gradle.kts` referencing the OLD pre-unification
`android/aura-retail` app for context ("the pre-unification
Chaquopy-based app") — not a real dependency declaration. Confirmed: zero
`chaquopy` plugin/dependency lines anywhere in this module.

### 5. Required indexes exist in the real schema

Already established structurally: `Sales.sq`/`Returns.sq`/`Catalog.sq`'s
`CREATE INDEX` statements are part of `RetailDatabase.Schema.create(driver)`,
which is the exact same schema-creation call both `AndroidSqliteDriver`
and `JdbcSqliteDriver` execute — the indexes `reporting-query-plan-report.md`
validated (`sales_company_id`, `sales_branch_id`, `returns_company_id`,
`sale_items_sale_id`, `return_items_return_id`, `products_category_id`)
are created identically on both drivers, by the same schema code.

### 6. Android debug APK builds successfully

`:androidApp:assembleDebug` succeeded after every commit in this
milestone's sequence (real Gradle invocations, confirmed repeatedly
throughout M5.7) — this proves the real Android compilation target
(including `AndroidDatabaseDriverFactory` and every reporting class)
compiles and links against the real Android SDK/toolchain, even though
it was not executed on a device here.

### 7. The original stable Android apps remain untouched

`git status --short` at the repository root, filtered to exclude
`mobile/`/`docs/` paths, shows zero results — every change this
milestone (and this entire initiative) is scoped to
`mobile/aura-retail-unified/` and `docs/retail/unified_mobile/`.
`android/aura-retail` and `android/aura-clinic` (the original,
pre-unification stable apps) exist on disk and are real, confirmed
unmodified.

## What remains for a future milestone with real device access

Real on-device/emulator execution of the reporting authority (actual
`AndroidSqliteDriver` I/O against a real Android-bundled SQLite build,
real `EXPLAIN QUERY PLAN` output from that specific SQLite version, real
on-disk database file size/growth) is the concrete, named next step once
this initiative has access to a real Android device or emulator — not
assumed unnecessary, not silently deferred without a plan.
