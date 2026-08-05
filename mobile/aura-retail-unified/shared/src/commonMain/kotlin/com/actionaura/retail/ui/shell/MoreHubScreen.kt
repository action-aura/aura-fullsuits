package com.actionaura.retail.ui.shell

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.ListItem
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.navigation.NavHostController
import com.actionaura.retail.ui.navigation.AuraRoute
import com.actionaura.retail.ui.theme.AuraSpacing

private data class MoreHubItem(val label: String, val route: AuraRoute)

/** M6.6 -- real secondary-destination hub, same real pattern the audited legacy `MoreScreen` already uses (pure navigation, no business logic of its own). */
@Composable
fun MoreHubScreen(navController: NavHostController) {
    val items = listOf(
        MoreHubItem("Categories", AuraRoute.Categories),
        MoreHubItem("Branches", AuraRoute.Branches),
        MoreHubItem("Suppliers", AuraRoute.Suppliers),
        MoreHubItem("Customers", AuraRoute.Customers),
        MoreHubItem("Inventory", AuraRoute.Inventory),
        MoreHubItem("Reports", AuraRoute.Reports),
        MoreHubItem("Import Center", AuraRoute.ImportHome),
        MoreHubItem("Backup & restore", AuraRoute.BackupRestore),
        MoreHubItem("License status", AuraRoute.LicenseStatus),
        MoreHubItem("Settings", AuraRoute.Settings),
    )
    LazyColumn(modifier = Modifier.padding(vertical = AuraSpacing.sm)) {
        items(items) { item ->
            ListItem(
                headlineContent = { Text(item.label) },
                modifier = Modifier
                    .padding(horizontal = AuraSpacing.sm)
                    .clickable { navController.navigate(item.route) },
            )
        }
    }
}
