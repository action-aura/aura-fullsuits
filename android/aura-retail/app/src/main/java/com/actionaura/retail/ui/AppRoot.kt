package com.actionaura.retail.ui

// Split from the source's shared AppRoot.kt (which branched its whole nav
// graph, drawer, and AI-sheet suggestions on BuildConfig.FLAVOR) into a
// retail-only file for this standalone single-flavor project -- see
// docs/android/android-migration-plan.md. Navigation routes, screens, and
// business logic for the retail path are otherwise unchanged.

import android.util.Log
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
import com.actionaura.retail.licensing.LicenseCheckInCoordinator
import com.actionaura.retail.net.AiChatRequest
import com.actionaura.retail.net.AiChatTurn
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.net.SessionResponse
import com.actionaura.retail.net.TerminalIdentity
import com.actionaura.retail.net.apiErrorMessage
import com.actionaura.retail.server.ServerBootstrap
import com.actionaura.retail.sync.SyncCoordinator
import com.actionaura.retail.ui.components.AppBackground
import com.actionaura.retail.ui.i18n.AppLocale
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.screens.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

// ERROR is not a cosmetic addition. Every failure below -- Chaquopy failing
// to start, the bundled assets failing to extract, the embedded Flask server
// never answering /api/health -- used to be swallowed into Phase.LOGIN, which
// stranded the user on a login screen that could never succeed (there is no
// server behind it to authenticate against) with zero diagnostics anywhere:
// no log line, no message, just a form that rejects every attempt.
private enum class Phase { LOADING, LICENSE, SETUP, LOGIN, READY, ERROR }

private const val TAG = "AppRoot"

/**
 * Everything this client must learn about the signed-in session before it is a
 * usable till, in ONE place that every route to [Phase.READY] goes through.
 *
 * Two things happen here, and both used to be missing or wrong:
 *
 *  1. CAPABILITIES. `/api/auth/session` returns `capabilities` as a TOP-LEVEL
 *     key, a sibling of `user`. This used to call `RetailSession.update(
 *     session.user)`, which read them off the user object, where they have
 *     never been -- so every capability gate in the app answered TRUE for
 *     every account. `RetailSession.adopt` reads the body, not the user; see
 *     `sessionCapabilities` for the full post-mortem, and note that the
 *     resolution deliberately does NOT get re-implemented inline here, because
 *     inlining it is how the desktop's version stayed wrong through a shipped
 *     fix.
 *
 *  2. TERMINAL IDENTITY. `GET /api/devices/me` is the only thing that creates
 *     this install's device identity, and `retail_api.py::_stamp()` PEEKS at
 *     that identity for every `terminal_id` it writes. This client called it
 *     nowhere, so every sale rung on a handset carried `terminal_id` NULL
 *     forever. One idempotent GET fixes it; see net/TerminalIdentity.kt.
 *
 * Both are best-effort and neither can fail the boot: a failed session fetch
 * leaves whatever was already known (rather than resetting an admin to
 * non-admin over a dropped packet), and the device check-in swallows
 * everything but cancellation.
 */
private suspend fun adoptSession(): SessionResponse? {
    val session = try { ApiClient.get().session() } catch (e: Exception) { null }
    if (session != null) RetailSession.adopt(session)
    if (session?.authenticated == true) {
        TerminalIdentity.establish { ApiClient.get().myDevice() }
        // How to format money, from the server (see ui/i18n/Num.kt::Currency).
        // Best-effort like everything else in this function and for the same
        // reason: a dropped packet must not stop the app booting. On failure
        // the Jordanian defaults stand, which is the right answer for the home
        // market -- unlike the hard-coded dollar sign this replaced, which was
        // the wrong answer everywhere the product is actually sold.
        try {
            val tax = ApiClient.get().taxSettings().data
            com.actionaura.retail.ui.i18n.Currency.apply(tax?.currency_symbol, tax?.currency_decimals)
        } catch (e: Exception) { /* keep the defaults */ }
    }
    return session
}

// Everything after the activation gate: unchanged from before Phase.LICENSE
// existed, just factored out so both the initial boot check and the
// post-activation callback (LicensingScreen's onActivated) can reach the
// same setup/login/ready decision without duplicating it.
private suspend fun phaseAfterActivationGate(): Phase {
    val needsSetup = try { ApiClient.get().onboardingStatus().needs_setup } catch (e: Exception) { false }
    if (needsSetup) return Phase.SETUP
    val session = adoptSession()
    return if (session?.authenticated == true) Phase.READY else Phase.LOGIN
}

@Composable
fun AppRoot() {
    val ctx = LocalContext.current
    var phase by remember { mutableStateOf(Phase.LOADING) }
    var startupDiagnostic by remember { mutableStateOf<String?>(null) }
    var bootAttempt by remember { mutableIntStateOf(0) }

    LaunchedEffect(bootAttempt) {
        phase = try {
            ServerBootstrap.start(ctx)
            // Multi-device sync foundation (Task 9 wiring): starts the
            // background push/pull loop once the embedded server (and
            // therefore its /_internal/sync/* routes) is up. A no-op when
            // OWNER_SYNC_BASE_URL is unconfigured (see SyncCoordinator.start()'s
            // own doc comment) -- never blocks the phase transition below,
            // since it only schedules a timer and returns immediately.
            SyncCoordinator.start(ctx)

            // Periodic licence check-in. Before this, checkIn() was reachable
            // only from LicensingScreen's manual "Check Now" button, so
            // nothing on Android ever contacted Owner after activation: a
            // revocation or suspension never landed mid-use, and -- worse in
            // practice -- the offline grace/warning progression never
            // advanced, so a single manual press weeks later could drop the
            // user straight to RESTRICTED with no warning phase at all. Inert
            // on a build with no licensing URL, same as SyncCoordinator above,
            // and it never blocks this phase transition (it only launches a
            // loop whose first tick is one interval away).
            LicenseCheckInCoordinator.start(ctx)

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
        } catch (e: Exception) {
            // A real diagnostic, in two places, because neither alone is
            // enough: logcat is the only thing a developer with the device in
            // hand can read, and the on-screen text is the only thing a
            // customer on the phone can read back to support. The exception
            // TYPE is included on screen deliberately -- "ServerStartupError"
            // vs "PyException" is the entire difference between "the bundled
            // Python failed to start" and "the server started but never became
            // ready", and it costs the user nothing.
            Log.e(TAG, "Embedded backend startup FAILED; the app cannot reach its own server.", e)
            startupDiagnostic = "${e.javaClass.simpleName}: ${e.message ?: "no detail"}"
            Phase.ERROR
        }
    }

    // Flip the whole UI to right-to-left when Arabic is active. Reading AppLocale.lang
    // here makes the app recompose (and re-mirror) the moment the language is switched.
    val layoutDir = if (AppLocale.isRtl) LayoutDirection.Rtl else LayoutDirection.Ltr
    val scope = rememberCoroutineScope()
    AppBackground {
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
                    // Both of these used to jump straight to Phase.READY, so a
                    // brand-new install that completed SETUP, or an account
                    // that LOGged IN, reached the till without ever resolving a
                    // session -- no capabilities, and (the expensive half) no
                    // terminal identity, on precisely the two occasions a till
                    // is about to start ringing its first sales. They now go
                    // through the same funnel the cold-start path does; it is
                    // one round trip to 127.0.0.1 against the embedded server,
                    // and awaiting it is what the desktop shell does too (its
                    // login modal's success handler re-runs init() rather than
                    // rendering first and catching up afterwards).
                    Phase.SETUP -> SetupScreen(onDone = { scope.launch { adoptSession(); phase = Phase.READY } })
                    Phase.LOGIN -> LoginScreen(onLoggedIn = { scope.launch { adoptSession(); phase = Phase.READY } })
                    Phase.READY -> MainShell(onLogout = { phase = Phase.LOGIN })
                    Phase.ERROR -> StartupErrorScreen(
                        diagnostic = startupDiagnostic,
                        onRetry = { startupDiagnostic = null; phase = Phase.LOADING; bootAttempt++ },
                    )
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
            shape = CircleShape, color = MaterialTheme.colorScheme.primaryContainer,
            modifier = Modifier.size(96.dp),
        ) {
            Box(contentAlignment = Alignment.Center) {
                Text("A", style = MaterialTheme.typography.displaySmall,
                    color = MaterialTheme.colorScheme.primary,
                    fontWeight = FontWeight.ExtraBold)
            }
        }
        Spacer(Modifier.height(28.dp))
        Text("Action Aura", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.ExtraBold)
        Spacer(Modifier.height(18.dp))
        Text(tr("Starting…"), color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/**
 * Shown instead of a login screen that could never succeed. Names what
 * actually failed, and offers the one action that can help -- a retry, since
 * the two most common real causes (a slow first-run asset extraction on a
 * cold device, a readiness timeout under load) genuinely do clear on a second
 * attempt.
 */
@Composable
private fun StartupErrorScreen(diagnostic: String?, onRetry: () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(28.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(Icons.Default.ErrorOutline, null, tint = MaterialTheme.colorScheme.error,
            modifier = Modifier.size(48.dp))
        Spacer(Modifier.height(18.dp))
        Text(tr("Aura could not start"), style = MaterialTheme.typography.titleLarge,
            fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(10.dp))
        Text(
            tr("The app's built-in server did not start, so nothing can be loaded or saved. " +
                "Your data is untouched. Please try again; if this keeps happening, restart the " +
                "device and send the details below to support."),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.bodyMedium,
        )
        diagnostic?.let {
            Spacer(Modifier.height(16.dp))
            Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(10.dp)) {
                // Not translated: this is the machine detail support needs
                // verbatim, and translating an exception type would make it
                // useless to whoever has to read it back.
                Text(it, Modifier.padding(12.dp), style = MaterialTheme.typography.bodySmall)
            }
        }
        Spacer(Modifier.height(22.dp))
        Button(onClick = onRetry) { Text(tr("Try again")) }
    }
}

private data class Dest(val route: String, val label: String, val icon: ImageVector)

// Five slots, the Material maximum, and deliberately the five destinations
// that are ungated for every role -- so the bar never rearranges itself
// between logins the way a capability-filtered bar would.
//
// POS sits in the CENTRE because that is the cheapest reach for a thumb on a
// phone held one-handed, and the till is the screen this app exists for.
// Dashboard stays FIRST because `tabs.first().route` is the NavHost's start
// destination; reordering that would change what the app opens on.
//
// Icons chosen for meaning, not availability:
//   * `PointOfSale`, not `ShoppingCart` -- a cart is what a customer pushes.
//     This screen is a till.
//   * `Apps`, not `MoreVert` -- the three-dot glyph means "a menu is hidden
//     here", which is what Android uses it for everywhere else. It is the
//     wrong thing to put on a tab that IS a destination, and it was the
//     app's only remaining overflow surface after the drawer was deleted.
//   * `People` for Customers, matching MoreScreen's own entry for it.
private val retailTabs = listOf(
    Dest("dashboard", "Dashboard", Icons.Default.Home),
    Dest("products", "Products", Icons.Default.Inventory2),
    Dest("pos", "POS", Icons.Default.PointOfSale),
    Dest("customers", "Customers", Icons.Default.People),
    Dest("more", "More", Icons.Default.Apps),
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
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    var aiOpen by remember { mutableStateOf(false) }

    val title = when (route) {
        "reports" -> "Reports"
        "employee_sales" -> "By Employee"
        "transactions" -> "Transactions"
        "returns" -> "Returns"
        "customers" -> "Customers"
        "receivables" -> "Receivables"
        "suppliers" -> "Suppliers"
        "purchase_orders" -> "Purchase Orders"
        "payables" -> "Payables"
        "cash_summary" -> "Cash Summary"
        "aging" -> "Aging"
        "employees" -> "Employees"
        "retail_settings" -> "Settings"
        // These three were falling through to the "Action Aura" default, so
        // every one of them opened under the app's own name instead of its
        // own -- spotted by opening Sync status on a real device and seeing
        // the bar say "Action Aura". The fallback is deliberately a name and
        // not a blank, which is exactly why the omission is invisible: the
        // screen looks finished, just anonymous. Any new route needs a line
        // here, and SyncStatusPresentationTest now pins this one.
        "sync_status" -> "Sync status"
        "backup" -> "Backup & restore"
        "licensing" -> "Licensing"
        else -> tabs.firstOrNull { it.route == route }?.label ?: "Action Aura"
    }

    // The ModalNavigationDrawer that used to wrap this Scaffold is GONE.
    //
    // It held two entries: Settings -- which MoreScreen already lists under
    // Finance -- and Log out. So an edge-swipe gesture, a hamburger button
    // and a full-height panel existed to deliver ONE destination that was
    // not reachable anywhere else, while giving the app two competing
    // overflow surfaces. The owner, using it on a real phone: "its
    // inconvenient to swipe left and there is 2 things and it looks bad."
    //
    // Log out now sits in MoreScreen's own Session section, so overflow
    // navigation lives in exactly one place.
    run {
        Scaffold(
            topBar = {
                TopAppBar(
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = Color.Transparent,
                        scrolledContainerColor = Color.Transparent),
                    title = { Text(tr(title), fontWeight = FontWeight.Bold) },
                    navigationIcon = {
                        // Back on a detail screen; NOTHING on a top-level tab.
                        // The hamburger used to open a two-item drawer that no
                        // longer exists -- a menu button that opens nothing is
                        // worse than no button.
                        if (!isTopLevel) IconButton(onClick = { nav.popBackStack() }) {
                            Icon(Icons.Default.ArrowBack, tr("Back"))
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
                // Opaque panel chrome (the desktop tab bar is an opaque panel
                // too) -- the 0.86-alpha frosted look it had only made sense
                // over the old nebula gradient, and translucent-over-flat is
                // just a slightly wrong colour.
                if (isTopLevel) NavigationBar(
                    containerColor = MaterialTheme.colorScheme.surface,
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
                retailGraph(this, nav, snackbar, onLogout = {
                    scope.launch {
                        // Exactly the teardown the removed drawer performed:
                        // end the server session, clear local session state,
                        // then hand back to the host.
                        try { ApiClient.get().logout() } catch (_: Exception) {}
                        RetailSession.reset()
                        onLogout()
                    }
                })
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

private fun retailGraph(b: NavGraphBuilder, nav: androidx.navigation.NavController, snackbar: SnackbarHostState, onLogout: () -> Unit) {
    b.composable("dashboard") { DashboardScreen(onNavigate = { r -> nav.navigate(r) }) }
    b.composable("pos") { PosScreen(snackbar) }
    b.composable("products") { ProductsScreen(snackbar) }
    b.composable("categories") { CategoriesScreen(snackbar) }
    b.composable("more") {
        MoreScreen(
            onNavigate = { r -> nav.navigate(r) },
            // Same teardown the drawer's Log out performed: end the server
            // session, clear local session state, then hand back to the host.
            onLogout = onLogout,
        )
    }
    b.composable("reports") { ReportsScreen(snackbar, onNavigate = { r -> nav.navigate(r) }) }
    // Takings per employee (retail schema v13 attribution). Registered here
    // AND reached from ReportsScreen's own entry -- both halves are required,
    // for the same reason the employees route below spells out:
    // SettingsScreen.kt is a complete screen that has never been in this
    // graph, so it has never rendered for a single user.
    // EmployeeSalesWiringContractTest pins the route string, the composable,
    // the navigate() call and this title-map entry.
    b.composable("employee_sales") { EmployeeSalesScreen(snackbar) }
    b.composable("transactions") { TransactionsScreen(snackbar) }
    b.composable("returns") { ReturnsScreen(snackbar) }
    b.composable("customers") { CustomersScreen(snackbar) }
    b.composable("receivables") { ReceivablesScreen(snackbar) }
    b.composable("suppliers") { SuppliersScreen(snackbar) }
    b.composable("purchase_orders") { PurchaseOrdersScreen(snackbar) }
    b.composable("payables") { PayablesScreen(snackbar) }
    b.composable("cash_summary") { DailyCashScreen(snackbar) }
    b.composable("aging") { AgingScreen(snackbar) }
    // Phase 1 employee management (design doc §3). Registered here and
    // reachable from MoreScreen's "Team" section -- both halves are required:
    // SettingsScreen.kt is a fully-written screen that has never been in this
    // graph, so it has never rendered for a single user. EmployeesWiringContractTest
    // pins the route string, the composable, and a navigate() call to it.
    b.composable("employees") { EmployeesScreen(snackbar) }
    b.composable("retail_settings") {
        RetailSettingsScreen(snackbar, onOpenBackup = { nav.navigate("backup") }, onOpenLicensing = { nav.navigate("licensing") })
    }
    b.composable("backup") { com.actionaura.retail.ui.screens.BackupRestoreScreen(onBack = { nav.popBackStack() }, snackbar = snackbar) }
    b.composable("licensing") { com.actionaura.retail.ui.screens.LicensingScreen(onBack = { nav.popBackStack() }, snackbar = snackbar) }
    b.composable("sync_status") { com.actionaura.retail.ui.screens.SyncStatusScreen() }
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
