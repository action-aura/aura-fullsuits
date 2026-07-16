package com.actionaura.retail.ui

// Split from the source's shared AppRoot.kt (which branched its whole nav
// graph, drawer, and AI-sheet suggestions on BuildConfig.FLAVOR) into a
// retail-only file for this standalone single-flavor project -- see
// docs/android/android-migration-plan.md. Navigation routes, screens, and
// business logic for the retail path are otherwise unchanged.

import androidx.compose.animation.*
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.navigation.NavGraphBuilder
import androidx.navigation.compose.*
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.server.ServerBootstrap
import com.actionaura.retail.ui.components.NebulaBackground
import com.actionaura.retail.ui.components.auroraBrush
import com.actionaura.retail.ui.components.pulseGlow
import com.actionaura.retail.ui.i18n.AppLocale
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.screens.*
import com.actionaura.retail.ui.theme.AuroraTeal
import kotlinx.coroutines.launch

private enum class Phase { LOADING, SETUP, LOGIN, READY }

@Composable
fun AppRoot() {
    val ctx = LocalContext.current
    var phase by remember { mutableStateOf(Phase.LOADING) }

    LaunchedEffect(Unit) {
        phase = try {
            ServerBootstrap.start(ctx)
            val needsSetup = try { ApiClient.get().onboardingStatus().needs_setup } catch (e: Exception) { false }
            if (needsSetup) Phase.SETUP
            else if (try { ApiClient.get().session().authenticated } catch (e: Exception) { false }) Phase.READY
            else Phase.LOGIN
        } catch (e: Exception) { Phase.LOGIN }
    }

    // Flip the whole UI to right-to-left when Arabic is active. Reading AppLocale.lang
    // here makes the app recompose (and re-mirror) the moment the language is switched.
    val layoutDir = if (AppLocale.isRtl) LayoutDirection.Rtl else LayoutDirection.Ltr
    NebulaBackground {
        // Transparent containers don't resolve a content color, so set the default
        // (light) text color for the whole app — otherwise unstyled text renders black.
        CompositionLocalProvider(
            LocalContentColor provides MaterialTheme.colorScheme.onBackground,
            LocalLayoutDirection provides layoutDir,
        ) {
            Crossfade(targetState = phase, animationSpec = tween(280), label = "phase") { p ->
                when (p) {
                    Phase.LOADING -> LoadingScreen()
                    Phase.SETUP -> SetupScreen(onDone = { phase = Phase.READY })
                    Phase.LOGIN -> LoginScreen(onLoggedIn = { phase = Phase.READY })
                    Phase.READY -> MainShell(onLogout = { phase = Phase.LOGIN })
                }
            }
        }
    }
}

@Composable
private fun LoadingScreen() {
    Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center) {
        Surface(
            shape = CircleShape, color = MaterialTheme.colorScheme.surface,
            modifier = Modifier.size(96.dp).pulseGlow(AuroraTeal, CircleShape),
        ) {
            Box(contentAlignment = Alignment.Center) {
                Text("A", style = MaterialTheme.typography.displaySmall.copy(brush = auroraBrush()),
                    fontWeight = FontWeight.ExtraBold)
            }
        }
        Spacer(Modifier.height(28.dp))
        Text("Action Aura", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.ExtraBold)
        Spacer(Modifier.height(18.dp))
        Text(tr("Starting…"), color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

private data class Dest(val route: String, val label: String, val icon: ImageVector)

private val retailTabs = listOf(
    Dest("dashboard", "Dashboard", Icons.Default.Home),
    Dest("pos", "POS", Icons.Default.ShoppingCart),
    Dest("products", "Products", Icons.Default.Inventory2),
    Dest("more", "More", Icons.Default.MoreVert),
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MainShell(onLogout: () -> Unit) {
    val tabs = retailTabs
    val nav = rememberNavController()
    val backStack by nav.currentBackStackEntryAsState()
    val route = backStack?.destination?.route
    val isDetail = false
    val isTopLevel = tabs.any { it.route == route }
    val drawerState = rememberDrawerState(DrawerValue.Closed)
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    var aiOpen by remember { mutableStateOf(false) }

    val title = when (route) {
        "settings" -> "Settings"
        "reports" -> "Reports"
        "transactions" -> "Transactions"
        "returns" -> "Returns"
        "customers" -> "Customers"
        "receivables" -> "Receivables"
        "suppliers" -> "Suppliers"
        "purchase_orders" -> "Purchase Orders"
        "payables" -> "Payables"
        "cash_summary" -> "Cash Summary"
        "aging" -> "Aging"
        "retail_settings" -> "Settings"
        else -> tabs.firstOrNull { it.route == route }?.label ?: "Action Aura"
    }

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            ModalDrawerSheet {
                Spacer(Modifier.height(16.dp))
                Text("  Action Aura", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(16.dp))
                NavigationDrawerItem(label = { Text(tr("Settings")) }, selected = route == "settings",
                    icon = { Icon(Icons.Default.Settings, null) },
                    onClick = { scope.launch { drawerState.close() }; nav.navigate("settings") })
                NavigationDrawerItem(label = { Text(tr("Log out")) }, selected = false,
                    icon = { Icon(Icons.Default.ExitToApp, null) },
                    onClick = {
                        scope.launch {
                            drawerState.close()
                            try { ApiClient.get().logout() } catch (_: Exception) {}
                            onLogout()
                        }
                    })
            }
        },
    ) {
        Scaffold(
            topBar = {
                TopAppBar(
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = Color.Transparent,
                        scrolledContainerColor = Color.Transparent),
                    title = { Text(tr(title), fontWeight = FontWeight.Bold) },
                    navigationIcon = {
                        if (!isTopLevel) IconButton(onClick = { nav.popBackStack() }) {
                            Icon(Icons.Default.ArrowBack, tr("Back"))
                        } else IconButton(onClick = { scope.launch { drawerState.open() } }) {
                            Icon(Icons.Default.Menu, tr("Menu"))
                        }
                    },
                    actions = {
                        IconButton(onClick = { aiOpen = true }) {
                            Icon(Icons.Default.AutoAwesome, "Aura AI", tint = MaterialTheme.colorScheme.primary)
                        }
                    },
                )
            },
            bottomBar = {
                if (isTopLevel) NavigationBar(
                    containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.86f),
                    tonalElevation = 0.dp,
                ) {
                    tabs.forEach { d ->
                        NavigationBarItem(
                            selected = route == d.route,
                            onClick = {
                                nav.navigate(d.route) {
                                    popUpTo(nav.graph.startDestinationId) { saveState = true }
                                    launchSingleTop = true; restoreState = true
                                }
                            },
                            icon = { Icon(d.icon, tr(d.label)) }, label = { Text(tr(d.label)) },
                        )
                    }
                }
            },
            snackbarHost = { SnackbarHost(snackbar) },
            containerColor = Color.Transparent,
            contentColor = MaterialTheme.colorScheme.onBackground,
        ) { padding ->
            NavHost(
                navController = nav,
                startDestination = tabs.first().route,
                modifier = Modifier.padding(padding).fillMaxSize(),
                enterTransition = { fadeIn(tween(260)) + slideInHorizontally(tween(260)) { it / 14 } },
                exitTransition = { fadeOut(tween(180)) },
                popEnterTransition = { fadeIn(tween(260)) },
                popExitTransition = { fadeOut(tween(180)) + slideOutHorizontally(tween(260)) { it / 14 } },
            ) {
                retailGraph(this, nav, snackbar)
            }
        }

        if (aiOpen) AiSheet(onDismiss = { aiOpen = false }, snackbar = snackbar)
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun AiSheet(onDismiss: () -> Unit, snackbar: SnackbarHostState) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var prompt by remember { mutableStateOf("") }
    val suggestions = listOf(tr("Today's best sellers"), tr("Low stock items"), tr("Sales vs last week"), tr("Slow-moving products"))

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Default.AutoAwesome, null, tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(10.dp))
                Column {
                    Text("Aura AI", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    Text(tr("Ask about your business"), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Spacer(Modifier.height(18.dp))
            Text(tr("SUGGESTED"), style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(10.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                suggestions.forEach { s -> SuggestionChip(onClick = { prompt = s }, label = { Text(s) }) }
            }
            Spacer(Modifier.height(20.dp))
            OutlinedTextField(
                value = prompt, onValueChange = { prompt = it },
                placeholder = { Text(tr("Ask anything…")) },
                modifier = Modifier.fillMaxWidth(),
                trailingIcon = {
                    IconButton(onClick = {
                        scope.launch { onDismiss(); snackbar.showSnackbar(tr("Aura AI is coming soon ✨")) }
                    }) { Icon(Icons.Default.Send, "Send", tint = MaterialTheme.colorScheme.primary) }
                },
            )
        }
    }
}

private fun retailGraph(b: NavGraphBuilder, nav: androidx.navigation.NavController, snackbar: SnackbarHostState) {
    b.composable("dashboard") { DashboardScreen(onNavigate = { r -> nav.navigate(r) }) }
    b.composable("pos") { PosScreen(snackbar) }
    b.composable("products") { ProductsScreen(snackbar) }
    b.composable("more") { MoreScreen(onNavigate = { r -> nav.navigate(r) }) }
    b.composable("reports") { ReportsScreen(snackbar) }
    b.composable("transactions") { TransactionsScreen(snackbar) }
    b.composable("returns") { ReturnsScreen(snackbar) }
    b.composable("customers") { CustomersScreen(snackbar) }
    b.composable("receivables") { ReceivablesScreen(snackbar) }
    b.composable("suppliers") { SuppliersScreen(snackbar) }
    b.composable("purchase_orders") { PurchaseOrdersScreen(snackbar) }
    b.composable("payables") { PayablesScreen(snackbar) }
    b.composable("cash_summary") { DailyCashScreen(snackbar) }
    b.composable("aging") { AgingScreen(snackbar) }
    b.composable("retail_settings") { RetailSettingsScreen(snackbar) }
}

@Composable
fun SimpleMessage(title: String, body: String) {
    Column(Modifier.fillMaxSize().padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center) {
        Text(title, style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(8.dp))
        Text(body, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
    }
}
