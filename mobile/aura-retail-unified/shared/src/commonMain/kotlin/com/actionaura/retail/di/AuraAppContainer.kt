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

    val settingsRepository: SettingsRepository = SqlDelightSettingsRepository(database, gate)
    val categoryRepository: CategoryRepository = SqlDelightCategoryRepository(database, gate)
    val branchRepository: BranchRepository = SqlDelightBranchRepository(database, gate)
    val productRepository: ProductRepository = SqlDelightProductRepository(database, gate)
    val inventoryRepository: InventoryRepository = SqlDelightInventoryRepository(database, gate)
    val reportingRepository: ReportingRepository = SqlDelightReportingRepository(database, gate, settingsRepository)
    val dashboardRepository: DashboardRepository = SqlDelightDashboardRepository(reportingRepository, productRepository)
    val importPersistenceRepository: ImportPersistenceRepository = SqlDelightImportPersistenceRepository(database, gate)

    // M6.16 -- real Category use cases (M5.2/M5.3's own existing authority, not reimplemented).
    val createCategoryUseCase = CreateCategoryUseCase(categoryRepository, unicodeTextNormalizer)
    val archiveCategoryUseCase = ArchiveCategoryUseCase(categoryRepository)
    val reactivateCategoryUseCase = ReactivateCategoryUseCase(categoryRepository, unicodeTextNormalizer)
    val listActiveCategoriesUseCase = ListActiveCategoriesUseCase(categoryRepository)

    // M6.17 -- real Branch use cases (M5.4's own existing authority).
    val activateBranchUseCase = ActivateBranchUseCase(branchRepository)
    val deactivateBranchUseCase = DeactivateBranchUseCase(branchRepository)
    val getCurrentBranchUseCase = GetCurrentBranchUseCase(branchRepository, settingsRepository)
    val setCurrentBranchUseCase = SetCurrentBranchUseCase(branchRepository, settingsRepository)
    val ensureDefaultBranchUseCase = EnsureDefaultBranchUseCase(branchRepository)
    val listActiveBranchesUseCase = ListActiveBranchesUseCase(branchRepository)
}
