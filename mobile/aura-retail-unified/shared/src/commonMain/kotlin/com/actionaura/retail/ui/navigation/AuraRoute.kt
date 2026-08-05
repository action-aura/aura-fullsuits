package com.actionaura.retail.ui.navigation

import kotlinx.serialization.Serializable

/**
 * M6.7 -- the one real, shared, type-safe route authority (real
 * Compose Multiplatform Navigation `@Serializable` routes,
 * `org.jetbrains.androidx.navigation:navigation-compose:2.8.0-alpha10`
 * -- never a raw string route). Covers every destination family the
 * checkpoint requires, so "screen routes cover the complete planned
 * product" (M6.29) is real and complete even though most destinations
 * render `FeatureUnavailableScreen` (M6.8) until their own owning
 * milestone builds real content -- an honest, typed unavailable state,
 * never a fake screen.
 *
 * Typed identifiers (`ProductId`/`SaleId`/`BranchId`/`CustomerId`/
 * `ImportDryRunIdArg`) carry only the primitive ID, never a serialized
 * domain object -- `type-safe-navigation-contract.md`'s own explicit
 * "never serialize entire domain objects into navigation arguments"
 * rule.
 */
sealed interface AuraRoute {

    // ---- Startup and access ----
    @Serializable data object Bootstrap : AuraRoute
    @Serializable data object Onboarding : AuraRoute
    @Serializable data object SignIn : AuraRoute
    @Serializable data object LicenseActivation : AuraRoute
    @Serializable data object LicenseBlocked : AuraRoute
    @Serializable data object SessionExpired : AuraRoute

    // ---- Primary ----
    @Serializable data object Dashboard : AuraRoute
    @Serializable data object Pos : AuraRoute
    @Serializable data object Products : AuraRoute
    @Serializable data object Sales : AuraRoute
    @Serializable data object More : AuraRoute

    // ---- Catalog ----
    @Serializable data object ProductList : AuraRoute
    @Serializable data class ProductDetails(val productId: Long) : AuraRoute
    @Serializable data object ProductCreate : AuraRoute
    @Serializable data class ProductEdit(val productId: Long) : AuraRoute
    @Serializable data object Categories : AuraRoute
    @Serializable data object CategoryCreate : AuraRoute
    @Serializable data class CategoryEdit(val categoryId: Long) : AuraRoute
    @Serializable data object Suppliers : AuraRoute
    @Serializable data class SupplierDetails(val supplierId: Long) : AuraRoute

    // ---- Operations ----
    @Serializable data object Inventory : AuraRoute
    @Serializable data class InventoryAdjustment(val productId: Long) : AuraRoute
    @Serializable data object Branches : AuraRoute
    @Serializable data class BranchDetails(val branchId: Long) : AuraRoute
    @Serializable data object Customers : AuraRoute
    @Serializable data class CustomerDetails(val customerId: Long) : AuraRoute
    @Serializable data class SaleDetails(val saleId: Long) : AuraRoute
    @Serializable data object ReturnCreate : AuraRoute
    @Serializable data class ReturnDetails(val returnId: Long) : AuraRoute

    // ---- Reporting ----
    @Serializable data object SalesTrend : AuraRoute
    @Serializable data object TopProducts : AuraRoute
    @Serializable data object Reports : AuraRoute

    // ---- Import ----
    @Serializable data object ImportHome : AuraRoute
    @Serializable data object ImportFileSelection : AuraRoute
    @Serializable data object ImportInspection : AuraRoute
    @Serializable data object ImportMapping : AuraRoute
    @Serializable data class ImportDryRun(val dryRunId: String) : AuraRoute
    @Serializable data class ImportCommit(val dryRunId: String) : AuraRoute
    @Serializable data class ImportResult(val importId: String) : AuraRoute

    // ---- Settings and support ----
    @Serializable data object Settings : AuraRoute
    @Serializable data object Language : AuraRoute
    @Serializable data object Theme : AuraRoute
    @Serializable data object BackupRestore : AuraRoute
    @Serializable data object LicenseStatus : AuraRoute
    @Serializable data object DeviceInformation : AuraRoute
    @Serializable data object Diagnostics : AuraRoute
    @Serializable data object About : AuraRoute
}

/** Real, top-level primary destinations shown in the adaptive shell's own nav (bottom bar/rail/drawer), matching `mobile-screen-route-matrix.md`'s own validated list. */
val PRIMARY_DESTINATIONS: List<AuraRoute> = listOf(
    AuraRoute.Dashboard, AuraRoute.Pos, AuraRoute.Products, AuraRoute.Sales, AuraRoute.More,
)
