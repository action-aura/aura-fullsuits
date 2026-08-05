package com.actionaura.retail.ui.navigation

import androidx.compose.runtime.Composable
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import com.actionaura.retail.ui.components.FeatureUnavailableScreen
import com.actionaura.retail.ui.components.UnavailableFeatureInfo

/**
 * M6.7 -- the real, complete navigation graph: every `AuraRoute` from
 * the checkpoint's own required destination-family list is wired here,
 * so the graph itself is real and complete even though most
 * destinations currently render `FeatureUnavailableScreen` (an honest,
 * typed unavailable state, never a fake screen with sample data).
 * Category/Branch/Reporting/Import routes are replaced with real
 * content in M6.16-M6.19; everything else cites its real owning
 * milestone from `mobile-screen-route-matrix.md`.
 */
@Composable
fun AuraNavHost(navController: NavHostController, startDestination: AuraRoute = AuraRoute.Dashboard) {
    NavHost(navController = navController, startDestination = startDestination) {
        composable<AuraRoute.Bootstrap> { unavailable("Bootstrap", "M7-M10 licensing/session milestone") }
        composable<AuraRoute.Onboarding> { unavailable("Onboarding", "M7-M10 licensing/session milestone") }
        composable<AuraRoute.SignIn> { unavailable("Sign in", "M7-M10 licensing/session milestone") }
        composable<AuraRoute.LicenseActivation> { unavailable("License activation", "M7-M10 licensing milestone") }
        composable<AuraRoute.LicenseBlocked> { unavailable("License blocked", "M7-M10 licensing milestone") }
        composable<AuraRoute.SessionExpired> { unavailable("Session expired", "M7-M10 licensing/session milestone") }

        composable<AuraRoute.Dashboard> { unavailable("Dashboard", "Reporting UI vertical slice (M6.18)") }
        composable<AuraRoute.Pos> { unavailable("Point of Sale", "Full POS/checkout milestone (post-M6)") }
        composable<AuraRoute.Products> { unavailable("Products", "Full catalog UI milestone (post-M6)") }
        composable<AuraRoute.Sales> { unavailable("Sales", "Full POS/Sale-history milestone (post-M6)") }
        composable<AuraRoute.More> { com.actionaura.retail.ui.shell.MoreHubScreen(navController) }

        composable<AuraRoute.ProductList> { unavailable("Products", "Full catalog UI milestone (post-M6)") }
        composable<AuraRoute.ProductDetails> { unavailable("Product details", "Full catalog UI milestone (post-M6)") }
        composable<AuraRoute.ProductCreate> { unavailable("Add product", "Full catalog UI milestone (post-M6)") }
        composable<AuraRoute.ProductEdit> { unavailable("Edit product", "Full catalog UI milestone (post-M6)") }
        composable<AuraRoute.Categories> { com.actionaura.retail.ui.category.CategoryListScreen(navController) }
        composable<AuraRoute.CategoryCreate> { com.actionaura.retail.ui.category.CategoryEditScreen(navController) }
        composable<AuraRoute.CategoryEdit> { unavailable("Edit category", "CategoryRepository has no update method (M5.1 real, confirmed scope) -- real, disclosed gap") }
        composable<AuraRoute.Suppliers> { unavailable("Suppliers", "Suppliers milestone (post-M6)") }
        composable<AuraRoute.SupplierDetails> { unavailable("Supplier details", "Suppliers milestone (post-M6)") }

        composable<AuraRoute.Inventory> { unavailable("Inventory", "Full catalog UI milestone (post-M6)") }
        composable<AuraRoute.InventoryAdjustment> { unavailable("Adjust stock", "Full catalog UI milestone (post-M6)") }
        composable<AuraRoute.Branches> { com.actionaura.retail.ui.branch.BranchListScreen(navController) }
        composable<AuraRoute.BranchDetails> { unavailable("Branch details", "BranchRepository has no update method (M5.1 real, confirmed scope) -- real, disclosed gap") }
        composable<AuraRoute.Customers> { unavailable("Customers", "Customers milestone (post-M6)") }
        composable<AuraRoute.CustomerDetails> { unavailable("Customer details", "Customers milestone (post-M6)") }
        composable<AuraRoute.SaleDetails> { unavailable("Sale details", "Full POS/Sale-history milestone (post-M6)") }
        composable<AuraRoute.ReturnCreate> { unavailable("Process return", "Returns milestone (post-M6)") }
        composable<AuraRoute.ReturnDetails> { unavailable("Return details", "Returns milestone (post-M6)") }

        composable<AuraRoute.SalesTrend> { unavailable("Sales trend", "Reporting UI vertical slice (M6.18)") }
        composable<AuraRoute.TopProducts> { unavailable("Top products", "Reporting UI vertical slice (M6.18)") }
        composable<AuraRoute.Reports> { unavailable("Reports", "Reporting UI vertical slice (M6.18)") }

        composable<AuraRoute.ImportHome> { unavailable("Import Center", "Import Center UI vertical slice (M6.19)") }
        composable<AuraRoute.ImportFileSelection> { unavailable("Select file", "Import Center UI vertical slice (M6.19)") }
        composable<AuraRoute.ImportInspection> { unavailable("Inspect file", "Import Center UI vertical slice (M6.19)") }
        composable<AuraRoute.ImportMapping> { unavailable("Column mapping", "Import Center UI vertical slice (M6.19)") }
        composable<AuraRoute.ImportDryRun> { unavailable("Dry-run plan", "Import Center UI vertical slice (M6.19)") }
        composable<AuraRoute.ImportCommit> { unavailable("Commit import", "Import Center UI vertical slice (M6.19)") }
        composable<AuraRoute.ImportResult> { unavailable("Import result", "Import Center UI vertical slice (M6.19)") }

        composable<AuraRoute.Settings> { unavailable("Settings", "Full Settings UI milestone (post-M6)") }
        composable<AuraRoute.Language> { unavailable("Language", "Full Settings UI milestone (post-M6)") }
        composable<AuraRoute.Theme> { unavailable("Theme", "Full Settings UI milestone (post-M6)") }
        composable<AuraRoute.BackupRestore> { unavailable("Backup & restore", "Backup/Restore milestone (M16)") }
        composable<AuraRoute.LicenseStatus> { unavailable("License status", "Licensing milestone (M7-M10)") }
        composable<AuraRoute.DeviceInformation> { unavailable("Device information", "Licensing milestone (M7-M10)") }
        composable<AuraRoute.Diagnostics> { unavailable("Diagnostics", "M20+ diagnostics milestone") }
        composable<AuraRoute.About> { unavailable("About", "Full Settings UI milestone (post-M6)") }
    }
}

@Composable
private fun unavailable(title: String, milestone: String) {
    FeatureUnavailableScreen(UnavailableFeatureInfo(title, milestone))
}
