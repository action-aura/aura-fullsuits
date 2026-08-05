package com.actionaura.retail.ui.navigation

/**
 * M6.20 -- the canonical registry of every planned mobile feature
 * surface. Real, cross-referenced against `mobile-screen-route-matrix.md`'s
 * own audited data -- not invented. Visibility here is a real UX
 * convenience only; it is NEVER authorization (M6.20's own explicit
 * rule) -- `requiredCapability` names the real future permission code
 * a Milestone 7-10 authority will enforce at the use-case boundary,
 * independent of whether this registry ever hides the entry point.
 */
data class FeatureSurfaceEntry(
    val route: AuraRoute,
    val titleKey: String,
    val destinationKind: DestinationKind,
    val requiredCapability: String,
    val implementationMilestone: String,
    val availability: Availability,
    val androidDependency: String,
    val iosDependency: String,
)

enum class DestinationKind { PRIMARY, SECONDARY }
enum class Availability { AVAILABLE, UNAVAILABLE_THIS_MILESTONE, DEFERRED }

object FeatureSurfaceRegistry {
    val entries: List<FeatureSurfaceEntry> = listOf(
        FeatureSurfaceEntry(AuraRoute.Dashboard, "dashboard.title", DestinationKind.PRIMARY, "reporting.view_dashboard", "M6.18", Availability.AVAILABLE, "none", "none (real, untested on this host)"),
        FeatureSurfaceEntry(AuraRoute.Pos, "pos.title", DestinationKind.PRIMARY, "pos.checkout", "post-M6", Availability.DEFERRED, "camera/HID barcode (legacy-proven)", "none"),
        FeatureSurfaceEntry(AuraRoute.Products, "products.title", DestinationKind.PRIMARY, "catalog.view_products", "post-M6", Availability.DEFERRED, "none", "none"),
        FeatureSurfaceEntry(AuraRoute.Sales, "sales.title", DestinationKind.PRIMARY, "sales.view_history", "post-M6", Availability.DEFERRED, "none", "none"),
        FeatureSurfaceEntry(AuraRoute.More, "more.title", DestinationKind.PRIMARY, "none (pure navigation)", "M6.6", Availability.AVAILABLE, "none", "none"),
        FeatureSurfaceEntry(AuraRoute.Categories, "category.title", DestinationKind.SECONDARY, "catalog.manage_categories", "M6.16", Availability.AVAILABLE, "none", "none (real, untested on this host)"),
        FeatureSurfaceEntry(AuraRoute.Branches, "branch.title", DestinationKind.SECONDARY, "operations.manage_branches", "M6.17", Availability.AVAILABLE, "none", "none (real, untested on this host)"),
        FeatureSurfaceEntry(AuraRoute.Suppliers, "suppliers.title", DestinationKind.SECONDARY, "purchasing.manage_suppliers", "post-M6", Availability.DEFERRED, "none", "none"),
        FeatureSurfaceEntry(AuraRoute.Customers, "customers.title", DestinationKind.SECONDARY, "sales.manage_customers", "post-M6", Availability.DEFERRED, "none", "none"),
        FeatureSurfaceEntry(AuraRoute.Inventory, "inventory.title", DestinationKind.SECONDARY, "catalog.manage_inventory", "post-M6", Availability.DEFERRED, "none", "none"),
        FeatureSurfaceEntry(AuraRoute.Reports, "reports.title", DestinationKind.SECONDARY, "reporting.view_reports", "M6.18", Availability.AVAILABLE, "none", "none (real, untested on this host)"),
        FeatureSurfaceEntry(AuraRoute.ImportHome, "import.title", DestinationKind.SECONDARY, "import.commit", "M6.19", Availability.AVAILABLE, "real FilePicker not yet built -- paste-based input only", "none"),
        FeatureSurfaceEntry(AuraRoute.BackupRestore, "backup.title", DestinationKind.SECONDARY, "system.backup_restore", "M16", Availability.DEFERRED, "none", "none"),
        FeatureSurfaceEntry(AuraRoute.LicenseStatus, "license.title", DestinationKind.SECONDARY, "system.view_license", "M7-M10", Availability.DEFERRED, "real Ed25519/Keystore machinery exists in legacy app, not ported", "none"),
        FeatureSurfaceEntry(AuraRoute.Settings, "settings.title", DestinationKind.SECONDARY, "system.manage_settings", "post-M6", Availability.DEFERRED, "none", "none"),
    )
}
