package com.actionaura.retail.di

import com.actionaura.retail.data.BranchRepository
import com.actionaura.retail.data.CategoryRepository
import com.actionaura.retail.data.InventoryRepository
import com.actionaura.retail.data.ProductRepository
import com.actionaura.retail.data.SettingsRepository
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightBranchRepository
import com.actionaura.retail.data.sqldelight.SqlDelightCategoryRepository
import com.actionaura.retail.data.sqldelight.SqlDelightInventoryRepository
import com.actionaura.retail.data.sqldelight.SqlDelightProductRepository
import com.actionaura.retail.data.sqldelight.SqlDelightSettingsRepository
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.importing.persistence.ImportPersistenceRepository
import com.actionaura.retail.importing.persistence.SqlDelightImportPersistenceRepository
import com.actionaura.retail.licensing.LicensingProductCode
import com.actionaura.retail.platform.DatabaseDriverFactory
import com.actionaura.retail.platform.UnicodeTextNormalizer
import com.actionaura.retail.reporting.DashboardRepository
import com.actionaura.retail.reporting.ReportingRepository
import com.actionaura.retail.reporting.SqlDelightDashboardRepository
import com.actionaura.retail.reporting.SqlDelightReportingRepository
import com.actionaura.retail.securestorage.GenerationalSecureMaterialStore
import com.actionaura.retail.securestorage.SecureBlobStore
import com.actionaura.retail.securestorage.SecureMaterialStore
import com.actionaura.retail.sync.DeviceSigner
import com.actionaura.retail.sync.PlatformDeviceSigner
import com.actionaura.retail.sync.SyncOrchestrator
import com.actionaura.retail.sync.SyncRelayConfiguration
import com.actionaura.retail.sync.SyncRelayConfigurationValidationResult
import com.actionaura.retail.sync.resolveActiveSyncTransport
import com.actionaura.retail.usecases.branch.ActivateBranchUseCase
import com.actionaura.retail.usecases.branch.DeactivateBranchUseCase
import com.actionaura.retail.usecases.branch.EnsureDefaultBranchUseCase
import com.actionaura.retail.usecases.branch.GetCurrentBranchUseCase
import com.actionaura.retail.usecases.branch.ListActiveBranchesUseCase
import com.actionaura.retail.usecases.branch.SetCurrentBranchUseCase
import com.actionaura.retail.usecases.category.ArchiveCategoryUseCase
import com.actionaura.retail.usecases.category.CreateCategoryUseCase
import com.actionaura.retail.usecases.category.ListActiveCategoriesUseCase
import com.actionaura.retail.usecases.category.ReactivateCategoryUseCase
import io.ktor.client.HttpClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

/**
 * M6.3 -- the one real, canonical composition root for the entire
 * shared presentation layer. Constructed exactly ONCE per real app
 * process (platform `Application`/`iosApp` entry point, M6.26) and
 * held for the process's whole lifetime -- every repository below
 * shares the SAME real `RetailDatabase`/`DatabaseWriteGate` instance,
 * closing the exact real hazard `presentation-di-scope-report.md`
 * exists to prove closed: navigating between screens must never
 * construct a second, independent `DatabaseWriteGate` for the same
 * underlying database (the non-reentrant-`Mutex` correctness this
 * whole codebase depends on requires exactly one gate per database,
 * `SqlDelightCategoryRepository`'s own established KDoc rule).
 *
 * Real, deliberate hand-rolled composition root, not a DI framework
 * (Koin/Hilt/Dagger) -- every repository in this codebase already
 * takes its dependencies as explicit constructor parameters
 * (`SqlDelightProductRepository(db, gate)` etc.), so a plain
 * `object`/class wiring them together is the real, minimal, fully
 * testable mechanism that adds no new external dependency and no
 * magic -- consistent with this module's own established style since
 * M5.1.
 *
 * Real, disclosed scope: this app has no authenticated-session concept
 * yet (`UserRepository`/`SessionRepository`/`LicensingRepository`
 * remain M7-M10 interface markers, `RepositoryBoundaries.kt`) -- so
 * there is currently no real "session-scoped authority" to dispose on
 * logout beyond what M7-M10 will add. `AuraAppContainer` itself is
 * correctly APPLICATION-scoped for its entire real lifetime: the
 * database must survive login/logout (it holds real business data),
 * so there is no `disposeSession()` method here to fake — inventing
 * one now would be exactly the "temporary RBAC authority" the
 * checkpoint's own M6.25 forbids. When M7-M10 add real session state,
 * that state is additive to this container, not a replacement for it.
 *
 * `secureBlobStore`: M10.31 -- the real, platform-specific
 * `SecureBlobStore` adapter (`AndroidSecureBlobStore`/
 * `IosSecureBlobStore`), supplied by the real platform entry point
 * exactly like `driverFactory` already is. Wrapped here in the one
 * real, pure-Kotlin atomicity authority (`GenerationalSecureMaterialStore`,
 * M10.6) so every screen reads the SAME real secure-storage authority
 * from the SAME composition root, never a second independent instance
 * -- the identical rule `database`/`gate` already enforce.
 */
class AuraAppContainer(
    driverFactory: DatabaseDriverFactory,
    private val unicodeTextNormalizer: UnicodeTextNormalizer,
    secureBlobStore: SecureBlobStore,
    // Task 10 (multi-device-sync-foundation) -- the real, engine-committed
    // `HttpClient` [SyncTransport] is constructed with. MUST be CIO-backed
    // (`io.ktor.client.engine.cio.CIO`), never OkHttp -- `SyncTransport`'s
    // own class KDoc explains why (OkHttp silently drops `pull()`'s GET
    // request body). Required, not defaulted: `ktor-client-cio` is an
    // androidMain/androidUnitTest-only Gradle dependency (confirmed
    // against `shared/build.gradle.kts`), so `commonMain` cannot construct
    // one itself -- the real platform entry point (`MainActivity`)
    // constructs it exactly once and passes it in, the same established
    // pattern this constructor already uses for `driverFactory`/
    // `secureBlobStore`.
    syncHttpClient: HttpClient,
    // Task 10 -- `null` = sync relay not configured for this build/
    // environment, mirroring desktop's own established "empty relay URL =
    // inert" pattern (`task-5-report.md`) -- no production source for a
    // real relay URL exists yet on mobile (confirmed: no BuildConfig/
    // gradle-property wiring for it anywhere in this module before this
    // task), so the real, honest default here is `null`, never a
    // fabricated placeholder URL.
    syncRelayConfiguration: SyncRelayConfiguration? = null,
    // Task 10 -- a real `SupervisorJob`-rooted scope so a genuine failure
    // in one sync tick (e.g. Task 7's `DeviceSigner` `IllegalStateException`
    // on a corrupt/invalidated signing key -- deliberately never swallowed,
    // see `SyncOrchestrator`'s own KDoc) can never cascade into cancelling
    // unrelated coroutines sharing this scope.
    private val coroutineScope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Default),
) {

    private val driver = driverFactory.createDriver()
    val database: RetailDatabase = RetailDatabase(driver)
    val gate: DatabaseWriteGate = DatabaseWriteGate()
    val secureMaterialStore: SecureMaterialStore = GenerationalSecureMaterialStore(
        blobStore = secureBlobStore,
        // Real, standard kotlinx-datetime ISO-8601 instant string (already this
        // module's own real "now" authority, `CategoryListViewModel.nowEpochMillis`).
        nowIso8601 = { kotlinx.datetime.Clock.System.now().toString() },
        // Real CSPRNG-backed generation id -- the same M9 `secureRandomHex` bridge
        // that replaced this codebase's own weak-RNG defect, never a predictable
        // counter/timestamp in production.
        newGenerationId = { com.actionaura.retail.licensing.transport.secureRandomHex(16) },
    )

    // Task 7 (multi-device-sync-foundation) -- the real, net-new device
    // signing authority. Wraps the SAME `secureBlobStore` the composition
    // root was constructed with (not `secureMaterialStore` above -- see
    // `PlatformDeviceSigner`'s own KDoc for why a device signing key is
    // deliberately kept out of the activation-bundle lifecycle), so every
    // screen/use case reads the SAME real device keypair from the SAME
    // composition root, never a second independent instance -- the
    // identical rule `database`/`gate`/`secureMaterialStore` already
    // enforce. Consumed by Task 8 (activation, publishing this device's
    // public key to Owner) and Task 9 (sync client, signing push/pull).
    val deviceSigner: DeviceSigner = PlatformDeviceSigner(secureBlobStore)

    // Task 10 -- the real push/pull orchestration loop. `transportProvider`
    // is re-evaluated on every single push/pull attempt (never cached) via
    // `resolveActiveSyncTransport`, so a real activation committed after
    // this container was constructed is picked up on the very next tick
    // with no restart -- see `SyncOrchestrator`'s own KDoc for why this
    // mirrors desktop's `client_factory` rationale rather than the task
    // brief's own illustrative draft (a fixed `transport` field).
    val syncOrchestrator: SyncOrchestrator = SyncOrchestrator(
        transportProvider = {
            resolveActiveSyncTransport(
                secureBlobStore = secureBlobStore,
                secureMaterialStore = secureMaterialStore,
                deviceSigner = deviceSigner,
                httpClient = syncHttpClient,
                configuration = syncRelayConfiguration,
                productCode = LicensingProductCode.AURA_RETAIL.name,
            )
        },
        database = database,
        gate = gate,
        scope = coroutineScope,
    )

    val settingsRepository: SettingsRepository = SqlDelightSettingsRepository(database, gate)
    val categoryRepository: CategoryRepository = SqlDelightCategoryRepository(database, gate)
    val branchRepository: BranchRepository = SqlDelightBranchRepository(database, gate)
    val productRepository: ProductRepository = SqlDelightProductRepository(database, gate)
    val inventoryRepository: InventoryRepository = SqlDelightInventoryRepository(database, gate)
    val reportingRepository: ReportingRepository = SqlDelightReportingRepository(database, gate, settingsRepository)
    val dashboardRepository: DashboardRepository = SqlDelightDashboardRepository(reportingRepository, productRepository)
    val importPersistenceRepository: ImportPersistenceRepository = SqlDelightImportPersistenceRepository(database, gate)

    // M6.16 -- real Category use cases (M5.2/M5.3's own existing authority, not reimplemented).
    // Task 10 -- each write use case is wired to `syncOrchestrator::nudge`,
    // so a real create/archive/reactivate pushes immediately instead of
    // waiting out the full poll interval (`CategoryUseCases.kt`'s own KDoc).
    val createCategoryUseCase = CreateCategoryUseCase(categoryRepository, unicodeTextNormalizer, syncOrchestrator::nudge)
    val archiveCategoryUseCase = ArchiveCategoryUseCase(categoryRepository, syncOrchestrator::nudge)
    val reactivateCategoryUseCase = ReactivateCategoryUseCase(categoryRepository, unicodeTextNormalizer, syncOrchestrator::nudge)
    val listActiveCategoriesUseCase = ListActiveCategoriesUseCase(categoryRepository)

    // M6.17 -- real Branch use cases (M5.4's own existing authority).
    val activateBranchUseCase = ActivateBranchUseCase(branchRepository)
    val deactivateBranchUseCase = DeactivateBranchUseCase(branchRepository)
    val getCurrentBranchUseCase = GetCurrentBranchUseCase(branchRepository, settingsRepository)
    val setCurrentBranchUseCase = SetCurrentBranchUseCase(branchRepository, settingsRepository)
    val ensureDefaultBranchUseCase = EnsureDefaultBranchUseCase(branchRepository)
    val listActiveBranchesUseCase = ListActiveBranchesUseCase(branchRepository)

    // Task 10 -- starts the background poll loop, guarded on
    // `syncRelayConfiguration` being both present AND passing its own
    // `validate()` (never started against a config this module's own
    // security rules already reject, e.g. cleartext HTTP in production) --
    // mirrors desktop's own "empty relay URL = inert" gate exactly. When
    // `syncRelayConfiguration` is `null` (the honest default -- no
    // production URL source exists yet), `syncOrchestrator` is still
    // constructed (so `nudge()` always has a safe target to call) but its
    // poll loop never starts, and `transportProvider` always resolves
    // `null` regardless -- genuinely inert, never a crash, never an
    // attempted connection.
    //
    // Code-review fix pass (Task 10): `resolveActiveSyncTransport` (called
    // by `transportProvider` above) now ALSO checks `.validate()` itself --
    // the real gap this comment's own claim used to overstate: this `init`
    // block only ever gated the recurring poll loop, never `nudge()` (fired
    // on every category write, independent of whether the loop started),
    // so an invalid config could previously reach a real network attempt
    // via `nudge()` alone. Both checks are kept intentionally (defense in
    // depth, cheap): this one avoids ever starting a loop that would
    // forever no-op against a known-invalid config; the one inside
    // `resolveActiveSyncTransport` is the actual, unconditional security
    // gate every push/pull path -- loop tick AND `nudge()` alike -- goes
    // through.
    init {
        val configuration = syncRelayConfiguration
        if (configuration != null && configuration.validate() is SyncRelayConfigurationValidationResult.Valid) {
            syncOrchestrator.start()
        }
    }
}
