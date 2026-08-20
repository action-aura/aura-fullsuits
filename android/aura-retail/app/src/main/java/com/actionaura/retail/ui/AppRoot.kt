package com.actionaura.retail.ui

// Split from the source's shared AppRoot.kt (which branched its whole nav
// graph, drawer, and AI-sheet suggestions on BuildConfig.FLAVOR) into a
// retail-only file for this standalone single-flavor project -- see
// docs/android/android-migration-plan.md. Navigation routes, screens, and
// business logic for the retail path are otherwise unchanged.

import androidx.compose.animation.*
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
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
import com.actionaura.retail.net.AiChatRequest
import com.actionaura.retail.net.AiChatTurn
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.net.apiErrorMessage
import com.actionaura.retail.server.ServerBootstrap
import com.actionaura.retail.sync.SyncCoordinator
import com.actionaura.retail.ui.components.NebulaBackground
import com.actionaura.retail.ui.components.auroraBrush
import com.actionaura.retail.ui.components.pulseGlow
import com.actionaura.retail.ui.i18n.AppLocale
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.screens.*
import com.actionaura.retail.ui.theme.AuroraTeal
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private enum class Phase { LOADING, LICENSE, SETUP, LOGIN, READY }

// States that mean "this device has never completed activation" -- /status
// deliberately returns NOT_CONFIGURED for this case too, not just for a
// truly unconfigured build (see commercial_runtime/licensing_contracts's
// own test_status_before_activation_is_not_configured_shape, which pins
// this on purpose), so BuildConfig.OWNER_LICENSING_BASE_URL is the real
// signal for "is licensing even wired up on this build" -- the state
// string alone can't distinguish "unconfigured" from "configured but never
// activated."
private val NEEDS_ACTIVATION_STATES = setOf("NOT_CONFIGURED", "ACTIVATION_REQUIRED", "ACTIVATING")

// Everything after the activation gate: unchanged from before Phase.LICENSE
// existed, just factored out so both the initial boot check and the
// post-activation callback (LicensingScreen's onActivated) can reach the
// same setup/login/ready decision without duplicating it.
private suspend fun phaseAfterActivationGate(): Phase {
    val needsSetup = try { ApiClient.get().onboardingStatus().needs_setup } catch (e: Exception) { false }
    if (needsSetup) return Phase.SETUP
    val session = try { ApiClient.get().session() } catch (e: Exception) { null }
    return if (session?.authenticated == true) {
        // Admin-gating state (Wave 1A, Part G), derived from the same
        // session check that already gates navigation.
        RetailSession.update(session.user)
        Phase.READY
    } else Phase.LOGIN
}

@Composable
fun AppRoot() {
    val ctx = LocalContext.current
    var phase by remember { mutableStateOf(Phase.LOADING) }

    LaunchedEffect(Unit) {
        phase = try {
            ServerBootstrap.start(ctx)
            // Multi-device sync foundation (Task 9 wiring): starts the
            // background push/pull loop once the embedded server (and
            // therefore its /_internal/sync/* routes) is up. A no-op when
            // OWNER_SYNC_BASE_URL is unconfigured (see SyncCoordinator.start()'s
            // own doc comment) -- never blocks the phase transition below,
            // since it only schedules a timer and returns immediately.
            SyncCoordinator.start(ctx)

            // Device activation gate, checked first (before setup/login) so
            // an unactivated device on a real licensed build never reaches
            // the app at all -- matches the desktop shell's equivalent gate.
            // Skipped entirely when this build has no licensing URL baked
            // in (BuildConfig.OWNER_LICENSING_BASE_URL blank), matching this
            // codebase's "unset == not enforced" convention (same as
            // OWNER_LICENSING_BASE_URL's own documented behavior) -- an
            // unlicensed dev/test build behaves exactly as before this gate
            // existed.
            val licensingConfigured = com.actionaura.retail.BuildConfig.OWNER_LICENSING_BASE_URL.isNotBlank()
            val needsActivation = if (!licensingConfigured) false else {
                val state = try {
                    com.actionaura.retail.licensing.LicensingCoordinator(ctx).status()["current_state"] as? String
                } catch (e: Exception) { null }
                state in NEEDS_ACTIVATION_STATES
            }

            if (needsActivation) Phase.LICENSE else phaseAfterActivationGate()
        } catch (e: Exception) { Phase.LOGIN }
    }

    // Flip the whole UI to right-to-left when Arabic is active. Reading AppLocale.lang
    // here makes the app recompose (and re-mirror) the moment the language is switched.
    val layoutDir = if (AppLocale.isRtl) LayoutDirection.Rtl else LayoutDirection.Ltr
    val scope = rememberCoroutineScope()
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
                    Phase.LICENSE -> LicensingScreen(
                        onBack = {}, snackbar = remember { SnackbarHostState() },
                        onActivated = { scope.launch { phase = phaseAfterActivationGate() } },
                    )
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
                NavigationDrawerItem(label = { Text(tr("Settings")) }, selected = route == "retail_settings",
                    icon = { Icon(Icons.Default.Settings, null) },
                    onClick = { scope.launch { drawerState.close() }; nav.navigate("retail_settings") })
                NavigationDrawerItem(label = { Text(tr("Log out")) }, selected = false,
                    icon = { Icon(Icons.Default.ExitToApp, null) },
                    onClick = {
                        scope.launch {
                            drawerState.close()
                            try { ApiClient.get().logout() } catch (_: Exception) {}
                            RetailSession.reset()
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

        if (aiOpen) AiSheet(onDismiss = { aiOpen = false })
    }
}

// One rendered chat turn. Deliberately plain sheet-local state (no ViewModel,
// no persistence): the conversation only needs to outlive recomposition while
// the sheet is open, matching HeldSales' "simple process-local state" habit
// rather than inventing a chat store for a single bottom sheet. This also
// matches the backend's own design -- ai_chat() stores nothing server-side;
// `history` is client-supplied per request.
private data class AiTurn(val role: String, val content: String)

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun AiSheet(onDismiss: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var prompt by remember { mutableStateOf("") }
    val turns = remember { mutableStateListOf<AiTurn>() }
    var thinking by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val listState = rememberLazyListState()
    val suggestions = listOf(tr("Today's best sellers"), tr("Low stock items"), tr("Sales vs last week"), tr("Slow-moving products"))

    fun send() {
        val msg = prompt.trim()
        if (msg.isEmpty() || thinking) return
        // `history` = the turns BEFORE this message: ai_chat() appends the
        // new `message` to the prompt itself, so including it in history too
        // would feed the model the same question twice.
        val history = turns.map { AiChatTurn(it.role, it.content) }
        turns.add(AiTurn("user", msg))
        prompt = ""; error = null; thinking = true
        scope.launch {
            try {
                // withContext(IO) keeps the long (~17-36s real generation
                // latency) wait explicitly off the main dispatcher, same as
                // ServerBootstrap.start()'s convention for slow work.
                val r = withContext(Dispatchers.IO) {
                    ApiClient.get().aiChat(AiChatRequest(
                        message = msg, history = history, lang = AppLocale.lang.tag))
                }
                val reply = r.data?.reply?.trim().orEmpty()
                if (r.success && reply.isNotEmpty()) turns.add(AiTurn("assistant", reply))
                // A 200 without a usable reply shouldn't happen (the route
                // 503s instead), but if it ever does, show what the server
                // said rather than pretending the network failed.
                else error = tr(r.error ?: "AI assistant is temporarily unavailable.")
            } catch (e: Exception) {
                // Shared mapping (net/ApiErrors.kt): connectivity failures
                // stay connectivity, a 401 (dead session -- also what an
                // unauthenticated request hits) says "log in again", a
                // licensing 403 names the subscription, and the route's own
                // 503 text ("AI assistant is temporarily unavailable.",
                // which is also how an unset/rejected upstream bearer token
                // surfaces -- the proxy folds upstream 401s into that 503)
                // is shown translated.
                error = apiErrorMessage(e)
            } finally { thinking = false }
        }
    }

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
            if (turns.isEmpty()) {
                Text(tr("SUGGESTED"), style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(10.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    suggestions.forEach { s -> SuggestionChip(onClick = { prompt = s }, label = { Text(s) }) }
                }
            } else {
                // Keep the newest turn (or the typing row) in view as the
                // conversation grows -- without this, replies land below the
                // fold and the sheet looks stuck exactly when it succeeded.
                LaunchedEffect(turns.size, thinking) {
                    listState.animateScrollToItem(turns.size - if (thinking) 0 else 1)
                }
                LazyColumn(state = listState, verticalArrangement = Arrangement.spacedBy(8.dp),
                    modifier = Modifier.fillMaxWidth().heightIn(max = 320.dp)) {
                    items(turns) { t -> AiTurnBubble(t) }
                    if (thinking) item {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                            Spacer(Modifier.width(8.dp))
                            Text(tr("Aura AI is thinking…"), style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
            error?.let {
                Spacer(Modifier.height(10.dp))
                Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
            }
            Spacer(Modifier.height(20.dp))
            OutlinedTextField(
                value = prompt, onValueChange = { prompt = it },
                placeholder = { Text(tr("Ask anything…")) },
                modifier = Modifier.fillMaxWidth(),
                trailingIcon = {
                    // The spinner replaces the send button while a turn is in
                    // flight -- send() also guards on `thinking`, so a racing
                    // tap can never fire two overlapping requests.
                    if (thinking) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp)
                    else IconButton(onClick = { send() }) {
                        Icon(Icons.Default.Send, tr("Send"), tint = MaterialTheme.colorScheme.primary)
                    }
                },
            )
        }
    }
}

@Composable
private fun AiTurnBubble(t: AiTurn) {
    val isUser = t.role == "user"
    // Arrangement.End/Start (not absolute) so the bubbles mirror correctly
    // when the whole app flips to RTL for Arabic.
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start) {
        Surface(
            shape = RoundedCornerShape(14.dp),
            color = if (isUser) MaterialTheme.colorScheme.primary.copy(alpha = 0.18f)
                else MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f),
        ) {
            Text(t.content, Modifier.padding(horizontal = 12.dp, vertical = 8.dp).widthIn(max = 300.dp),
                style = MaterialTheme.typography.bodyMedium)
        }
    }
}

private fun retailGraph(b: NavGraphBuilder, nav: androidx.navigation.NavController, snackbar: SnackbarHostState) {
    b.composable("dashboard") { DashboardScreen(onNavigate = { r -> nav.navigate(r) }) }
    b.composable("pos") { PosScreen(snackbar) }
    b.composable("products") { ProductsScreen(snackbar) }
    b.composable("categories") { CategoriesScreen(snackbar) }
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
    b.composable("retail_settings") {
        RetailSettingsScreen(snackbar, onOpenBackup = { nav.navigate("backup") }, onOpenLicensing = { nav.navigate("licensing") })
    }
    b.composable("backup") { com.actionaura.retail.ui.screens.BackupRestoreScreen(onBack = { nav.popBackStack() }, snackbar = snackbar) }
    b.composable("licensing") { com.actionaura.retail.ui.screens.LicensingScreen(onBack = { nav.popBackStack() }, snackbar = snackbar) }
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
