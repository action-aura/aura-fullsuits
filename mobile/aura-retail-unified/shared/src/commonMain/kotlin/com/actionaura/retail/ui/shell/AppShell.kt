package com.actionaura.retail.ui.shell

import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.Row
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavHostController
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.actionaura.retail.ui.adaptive.AuraWindowSize
import com.actionaura.retail.ui.adaptive.AuraWindowSizeClass
import com.actionaura.retail.ui.navigation.AuraNavHost
import com.actionaura.retail.ui.navigation.AuraRoute
import com.actionaura.retail.ui.navigation.PRIMARY_DESTINATIONS

private data class PrimaryDestinationSpec(val route: AuraRoute, val label: String, val icon: ImageVector)

private val PRIMARY_SPECS = listOf(
    PrimaryDestinationSpec(AuraRoute.Dashboard, "Dashboard", Icons.Filled.Home),
    PrimaryDestinationSpec(AuraRoute.Pos, "POS", Icons.Filled.ShoppingCart),
    PrimaryDestinationSpec(AuraRoute.Products, "Products", Icons.Filled.List),
    PrimaryDestinationSpec(AuraRoute.Sales, "Sales", Icons.Filled.Star),
    PrimaryDestinationSpec(AuraRoute.More, "More", Icons.Filled.MoreVert),
)

/**
 * M6.6 -- the real, adaptive authenticated shell. `Compact` gets a
 * bottom `NavigationBar` (matches the audited legacy app's own 4-tab
 * pattern, extended to 5 to include Sales); `Medium`/`Expanded` get a
 * permanent `NavigationRail` instead, per M6.6's own explicit adaptive
 * requirement -- never every feature crammed into bottom navigation.
 */
@Composable
fun AuthenticatedAppShell(windowSize: AuraWindowSize, navController: NavHostController = rememberNavController()) {
    when (windowSize.sizeClass) {
        AuraWindowSizeClass.Compact -> CompactShell(navController)
        AuraWindowSizeClass.Medium, AuraWindowSizeClass.Expanded -> ExpandedShell(navController)
    }
}

@Composable
private fun CompactShell(navController: NavHostController) {
    Scaffold(
        bottomBar = { AuraBottomNavigation(navController) },
    ) { padding ->
        androidx.compose.foundation.layout.Box(modifier = Modifier.padding(padding)) {
            AuraNavHost(navController)
        }
    }
}

@Composable
private fun ExpandedShell(navController: NavHostController) {
    Row {
        AuraNavigationRail(navController)
        AuraNavHost(navController)
    }
}

@Composable
private fun AuraBottomNavigation(navController: NavHostController) {
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentDestination = backStackEntry?.destination
    NavigationBar {
        PRIMARY_SPECS.forEach { spec ->
            NavigationBarItem(
                selected = currentDestination?.hierarchy?.any { it.route == spec.route::class.qualifiedName } == true,
                onClick = { navigateToPrimary(navController, spec.route) },
                icon = { Icon(spec.icon, contentDescription = spec.label) },
                label = { Text(spec.label) },
            )
        }
    }
}

@Composable
private fun AuraNavigationRail(navController: NavHostController) {
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentDestination = backStackEntry?.destination
    NavigationRail {
        PRIMARY_SPECS.forEach { spec ->
            NavigationRailItem(
                selected = currentDestination?.hierarchy?.any { it.route == spec.route::class.qualifiedName } == true,
                onClick = { navigateToPrimary(navController, spec.route) },
                icon = { Icon(spec.icon, contentDescription = spec.label) },
                label = { Text(spec.label) },
            )
        }
    }
}

/** Real, standard single-top/restore-state primary-tab navigation -- never stacks duplicate copies of the same primary destination. */
private fun navigateToPrimary(navController: NavHostController, route: AuraRoute) {
    navController.navigate(route) {
        popUpTo(navController.graph.findStartDestination().id) { saveState = true }
        launchSingleTop = true
        restoreState = true
    }
}
