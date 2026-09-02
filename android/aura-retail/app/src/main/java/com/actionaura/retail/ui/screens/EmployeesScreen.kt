@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.retail.ui.screens

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Badge
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Groups
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.MailOutline
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.AdminActionResponse
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.net.CreateEmployeeRequest
import com.actionaura.retail.net.Employee
import com.actionaura.retail.net.SetEmployeePinRequest
import com.actionaura.retail.net.UpdateEmployeeRoleRequest
import com.actionaura.retail.net.UpdateEmployeeStatusRequest
import com.actionaura.retail.net.apiErrorMessage
import com.actionaura.retail.ui.RetailSession
import com.actionaura.retail.ui.components.Avatar
import com.actionaura.retail.ui.components.EmptyState
import com.actionaura.retail.ui.components.TillCard
import com.actionaura.retail.ui.components.SkeletonList
import com.actionaura.retail.ui.i18n.PIN_LENGTH
import com.actionaura.retail.ui.i18n.normalizePin
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.theme.Success
import com.actionaura.retail.ui.theme.Warning
import kotlinx.coroutines.launch

// ══════════════════════════════════════════════════════════════════════════════
//  EMPLOYEES -- the owner's account-management screen (Phase 1, design §3)
//
//  Wires the /api/admin/employees surface in
//  commercial_runtime/identity/onboarding_routes.py, which the desktop shell
//  and Clinic already share and which nothing on Android has ever called.
//  Android runs that same Flask app in-process over 127.0.0.1 (Chaquopy), so
//  this is the same server, the same registry.db and the same session cookie
//  the desktop uses -- not a parallel implementation.
//
//  Scope note: this screen manages ACCOUNTS. It changes nothing about sync
//  (design §5, Phase 5); an employee created here exists on this device's
//  registry until account sync ships.
// ══════════════════════════════════════════════════════════════════════════════

/** The two roles an owner may assign (`user_accounts.ASSIGNABLE_ROLES` minus
 *  the legacy 'employee' alias, which is accepted by the API for backwards
 *  compatibility but is not a choice any UI offers). 'admin' is absent by
 *  design -- one owner account per install, so "promote to admin" is not an
 *  operation that exists. */
private val ASSIGNABLE_ROLES = listOf("manager", "cashier")

/** English label for a stored role value. Falls through to `cashier` for the
 *  legacy 'employee' spelling and for anything unrecognised, matching
 *  `user_accounts.normalize_role()`'s least-privilege reading -- the server
 *  already sends `effective_role` computed that way, so this is only a
 *  belt-and-braces for a response that predates the field. */
private fun roleLabel(role: String?): String = when (role?.trim()?.lowercase()) {
    "admin" -> tr("Owner")
    "manager" -> tr("Manager")
    else -> tr("Cashier")
}

private fun statusLabel(status: String?): String = when (status?.trim()?.lowercase()) {
    "active" -> tr("Active")
    "disabled" -> tr("Deactivated")
    "pending_setup" -> tr("Invite pending")
    else -> status ?: ""
}

/**
 * Pull the invite token out of the server's `setup_link`.
 *
 * The server builds that link as `{request.host_url}/#setup/{token}`. On the
 * desktop `host_url` is the shell's own origin and the whole URL is useful. On
 * Android it is `http://127.0.0.1:<ephemeral port>` -- an address that means
 * nothing on anyone else's device and does not even survive this app being
 * restarted. Sharing it would be sharing a dead link, so the screen shares the
 * token, which is the part that actually IS the invite, and says where to
 * redeem it. Falls back to the whole string if the shape ever changes, because
 * an unrecognised link is still better than showing the owner nothing.
 */
internal fun inviteTokenOf(setupLink: String?): String {
    val raw = setupLink?.trim().orEmpty()
    if (raw.isEmpty()) return ""
    return raw.substringAfterLast("#setup/", raw)
}

private fun copyToClipboard(ctx: Context, label: String, text: String) {
    val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager ?: return
    cm.setPrimaryClip(ClipData.newPlainText(label, text))
}

@Composable
fun EmployeesScreen(snackbar: SnackbarHostState) {
    var employees by remember { mutableStateOf<List<Employee>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    // Distinct from `employees.isEmpty()`: a load that FAILED and a shop with
    // no staff are different facts, and collapsing them into one empty state
    // is how a screen ends up telling an owner they have no employees because
    // their session expired.
    var loadError by remember { mutableStateOf<String?>(null) }
    var showCreate by remember { mutableStateOf(false) }
    var invite by remember { mutableStateOf<String?>(null) }
    var sheetFor by remember { mutableStateOf<Employee?>(null) }
    var pinFor by remember { mutableStateOf<Employee?>(null) }
    val scope = rememberCoroutineScope()

    suspend fun load() {
        try {
            val body = ApiClient.get().employees().employees
            // Gson (converter-gson 2.11.0) allocates Kotlin data classes via
            // Unsafe when there is no zero-arg constructor bridging every
            // default, and OVERWRITES an already-defaulted field with a real
            // null when the server sends that key as an explicit JSON null --
            // bypassing Kotlin's own non-null guarantee at the type level.
            // Proved empirically while verifying this screen: `employees =
            // r.employees` assigns silently, and the crash lands later and
            // uncaught, at `employees.isEmpty()` in the render `when` block,
            // as a bare NullPointerException outside this try/catch. A
            // malformed 200 is exactly the failure claim 8 exists to catch --
            // routing it through the same mapping as every other failure
            // instead of only avoiding the crash keeps that promise instead
            // of trading a crash for a silent "no employees" lie.
            if (body == null) throw IllegalStateException("Malformed response: employees was null")
            employees = body
            loadError = null
        } catch (e: Exception) {
            // net/ApiErrors.kt, not a blanket message: a 403 here means this
            // account is not an admin, a licensing 403 means the subscription,
            // and only an IOException means the server is unreachable. All
            // three used to read as "Couldn't reach the server".
            loadError = apiErrorMessage(e)
            employees = emptyList()
        }
    }
    LaunchedEffect(Unit) { loading = true; load(); loading = false }

    // Admin gate. RetailSession.isAdmin comes from the same /api/auth/session
    // check that gates navigation, and the backend independently re-checks
    // `session['mt_role'] == 'admin'` on all five routes below -- so this is
    // the honest explanation, never the enforcement. It states WHY the screen
    // is empty rather than showing a working-looking screen that 403s on every
    // tap.
    if (!RetailSession.isAdmin) {
        EmptyState(
            Icons.Default.Shield,
            tr("Owner access required"),
            tr("Only the owner account can create employees or change what they can do. " +
                "Ask the owner to make the change on their account."),
        )
        return
    }

    Box(Modifier.fillMaxSize()) {
        when {
            loading -> SkeletonList()
            loadError != null -> EmptyState(
                Icons.Default.Groups, tr("Couldn't load employees"), loadError!!,
                ctaText = tr("Try again"),
                onCta = { scope.launch { loading = true; load(); loading = false } },
            )
            employees.isEmpty() -> EmptyState(
                Icons.Default.Groups, tr("No employees yet"),
                tr("Create an account for each person who works a till. They sign in with " +
                    "their own email, so every sale is recorded against the person who rang it."),
                ctaText = tr("Add employee"), onCta = { showCreate = true },
            )
            else -> LazyColumn(
                Modifier.fillMaxSize(),
                contentPadding = PaddingValues(16.dp, 16.dp, 16.dp, 96.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                items(employees, key = { it.id }) { e ->
                    EmployeeRow(e, onClick = { if (!isOwnerRow(e)) sheetFor = e })
                }
                item {
                    Spacer(Modifier.height(4.dp))
                    Text(
                        tr("Employee accounts live on this device until account sync is enabled."),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        if (!loading && loadError == null && employees.isNotEmpty()) {
            ExtendedFloatingActionButton(
                onClick = { showCreate = true },
                icon = { Icon(Icons.Default.Add, null) },
                text = { Text(tr("Add employee")) },
                modifier = Modifier.align(Alignment.BottomEnd).padding(16.dp),
            )
        }
    }

    if (showCreate) {
        CreateEmployeeDialog(
            onDismiss = { showCreate = false },
            onCreated = { token ->
                showCreate = false
                invite = token
                scope.launch { load() }
            },
            snackbar = snackbar,
        )
    }

    invite?.let { token ->
        InviteDialog(token = token, onDismiss = { invite = null }, snackbar = snackbar)
    }

    sheetFor?.let { target ->
        ManageEmployeeSheet(
            employee = target,
            onDismiss = { sheetFor = null },
            onChanged = { sheetFor = null; scope.launch { load() } },
            onSetPin = { sheetFor = null; pinFor = target },
            snackbar = snackbar,
        )
    }

    pinFor?.let { target ->
        PinDialog(
            employee = target,
            onDismiss = { pinFor = null },
            onChanged = { pinFor = null; scope.launch { load() } },
            snackbar = snackbar,
        )
    }
}

/** The owner's own row. Rendered read-only: demoting or deactivating the only
 *  admin account is a two-tap, irreversible lockout of the whole shop (there is
 *  no second owner and `create_admin` refuses to mint one while a valid admin
 *  exists). The role route refuses it server-side too; this keeps the control
 *  from being offered in the first place. */
private fun isOwnerRow(e: Employee): Boolean =
    (e.effective_role ?: e.role)?.trim()?.lowercase() == "admin"

@Composable
private fun EmployeeRow(e: Employee, onClick: () -> Unit) {
    val owner = isOwnerRow(e)
    TillCard(Modifier.fillMaxWidth(), onClick = if (owner) null else onClick) {
        Row(Modifier.padding(14.dp).fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Avatar(e.email)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(e.email ?: "—", fontWeight = FontWeight.Bold, maxLines = 1)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        e.employee_id ?: "",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    if (e.has_pin) {
                        Spacer(Modifier.width(8.dp))
                        Icon(
                            Icons.Default.Lock, tr("PIN set"),
                            modifier = Modifier.size(12.dp),
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Spacer(Modifier.width(3.dp))
                        Text(
                            tr("PIN set"),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(
                    roleLabel(e.effective_role ?: e.role),
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.Bold,
                    color = if (owner) MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.onSurface,
                )
                Text(
                    statusLabel(e.status),
                    style = MaterialTheme.typography.labelSmall,
                    color = when (e.status?.lowercase()) {
                        "active" -> Success
                        "pending_setup" -> Warning
                        else -> MaterialTheme.colorScheme.error
                    },
                )
            }
        }
    }
}

// ── Create ───────────────────────────────────────────────────────────────────

@Composable
private fun CreateEmployeeDialog(
    onDismiss: () -> Unit,
    onCreated: (String) -> Unit,
    snackbar: SnackbarHostState,
) {
    var email by remember { mutableStateOf("") }
    var role by remember { mutableStateOf(ASSIGNABLE_ROLES.first()) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text(tr("Add employee")) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    value = email, onValueChange = { email = it; error = null },
                    label = { Text(tr("Work email")) },
                    singleLine = true,
                    leadingIcon = { Icon(Icons.Default.MailOutline, null) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
                    modifier = Modifier.fillMaxWidth(),
                )
                // Says out loud that the account has no display name. `users`
                // has an email and a server-assigned EMP-0001 and nothing else
                // (registry_db.py:113-131), so a "Full name" box here would be
                // a field the server drops on the floor -- the owner would type
                // it, see it vanish, and reasonably conclude the app lost data.
                Text(
                    tr("The account is identified by this email and an ID the app assigns " +
                        "(EMP-0001, EMP-0002…). There is no separate name field."),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )

                Text(tr("Role"), style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
                SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                    ASSIGNABLE_ROLES.forEachIndexed { i, r ->
                        SegmentedButton(
                            selected = role == r,
                            onClick = { role = r },
                            shape = SegmentedButtonDefaults.itemShape(i, ASSIGNABLE_ROLES.size),
                        ) { Text(roleLabel(r)) }
                    }
                }
                Text(roleExplainer(), style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)

                error?.let {
                    Text(it, color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = {
            Button(
                enabled = !busy && email.isNotBlank(),
                onClick = {
                    busy = true; error = null
                    scope.launch {
                        try {
                            val r = ApiClient.get().createEmployee(
                                CreateEmployeeRequest(email.trim().lowercase(), role))
                            // The route answers 200 with {"success": true,
                            // "setup_link": ...}. A 200 that carries neither is
                            // not something to celebrate silently, so the
                            // server's own `error` is shown when it sent one.
                            if (r.success) onCreated(inviteTokenOf(r.setup_link))
                            else error = tr(r.error ?: "The invite could not be created.")
                        } catch (e: Exception) {
                            // A duplicate email is a 400 whose body carries
                            // "Email already registered." and an unassignable
                            // role is a 400 carrying "Role must be manager or
                            // cashier." -- apiErrorMessage surfaces the
                            // server's own sentence for both, translated.
                            error = apiErrorMessage(e)
                        } finally { busy = false }
                    }
                },
            ) {
                if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                else Text(tr("Send invite"))
            }
        },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !busy) { Text(tr("Cancel")) } },
    )
}

/**
 * What both roles can actually do. Transcribed from `user_accounts.ROLE_CAPABILITIES`
 * -- an owner choosing a role is choosing exactly that list, so this text has
 * to track it. One fixed sentence, not a per-role split, and kept character-
 * for-character identical to the desktop screen's copy of the same sentence
 * (products/retail/frontend/employees.js) -- the two apps describing the same
 * permission matrix in different words is exactly how the old per-role text
 * here drifted into implying a cashier can't refund. Two details worth
 * stating rather than smoothing over, because both are the reason the grid
 * looks the way it does:
 *  - a cashier CAN refund, because `create_return` is sale-bound (it resolves a
 *    real sale of this company or 404s and cannot exceed what was sold), so the
 *    refund a cashier can issue is not money conjured from nothing;
 *  - nobody but the owner approves a cash variance, because a role that could
 *    both close a drawer and sign off its own shortfall is the AUDIT-032
 *    self-approval hole reintroduced one shop at a time. (Neither role name
 *    is mentioned for that one -- it belongs to neither.)
 */
private fun roleExplainer(): String =
    tr("A cashier can sell, refund against a sale, and close their own drawer. A manager can also discount, adjust stock and read reports.")

// ── Invite ───────────────────────────────────────────────────────────────────

@Composable
private fun InviteDialog(token: String, onDismiss: () -> Unit, snackbar: SnackbarHostState) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    AlertDialog(
        onDismissRequest = onDismiss,
        icon = { Icon(Icons.Default.Badge, null) },
        title = { Text(tr("Invite created")) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(tr("Give this code to the employee. They enter it once to choose their own password."))
                Surface(
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    shape = RoundedCornerShape(10.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    // Not translated and not reformatted: it is an opaque
                    // credential that has to be reproduced character for
                    // character.
                    Text(
                        token,
                        Modifier.padding(12.dp),
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.Bold,
                    )
                }
                // Both facts are properties of the row `create_employee` wrote
                // into `secure_links` -- expires_at = utcnow + 7 days, and
                // `employee_setup` marks is_used=1 on redemption and refuses a
                // used link. An owner who does not know the code dies has no
                // way to explain to their employee why it stopped working.
                Text(
                    tr("It works once, and it expires 7 days from now. After that, or after " +
                        "they use it, create another invite."),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    tr("The employee redeems it on a till running Aura, on the sign-in screen's " +
                        "\"I have an invite code\" option."),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        confirmButton = {
            Button(onClick = {
                copyToClipboard(ctx, "Aura invite", token)
                scope.launch { snackbar.showSnackbar(tr("Invite code copied")) }
            }) {
                Icon(Icons.Default.ContentCopy, null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text(tr("Copy code"))
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(tr("Done")) } },
    )
}

// ── Manage (role change / deactivate / PIN) ──────────────────────────────────

@Composable
private fun ManageEmployeeSheet(
    employee: Employee,
    onDismiss: () -> Unit,
    onChanged: () -> Unit,
    onSetPin: () -> Unit,
    snackbar: SnackbarHostState,
) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var confirmRole by remember { mutableStateOf<String?>(null) }
    val current = (employee.effective_role ?: employee.role)?.trim()?.lowercase() ?: "cashier"
    val isDisabled = employee.status?.lowercase() == "disabled"

    /**
     * One place for "call it, report exactly what the server said, reload".
     *
     * `onChanged()` fires BEFORE the snackbar, and `busy` is cleared before
     * both: `showSnackbar` suspends until the snackbar is dismissed or times
     * out (~4s), so doing it the obvious way round leaves the sheet open and
     * every control greyed out for four seconds after the work is already
     * done -- which reads as a hang, on the one screen where the user is
     * changing somebody's access and most wants to see it took effect.
     */
    fun run(block: suspend () -> AdminActionResponse, okMessage: String) {
        busy = true
        scope.launch {
            val message = try {
                val r = block()
                busy = false
                if (r.success) { onChanged(); okMessage }
                else tr(r.error ?: "The change could not be saved.")
            } catch (e: Exception) {
                busy = false
                apiErrorMessage(e)
            }
            snackbar.showSnackbar(message)
        }
    }

    ModalBottomSheet(onDismissRequest = { if (!busy) onDismiss() }, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)) {

            Row(verticalAlignment = Alignment.CenterVertically) {
                Avatar(employee.email)
                Spacer(Modifier.width(12.dp))
                Column {
                    Text(employee.email ?: "—", fontWeight = FontWeight.Bold)
                    Text(
                        "${employee.employee_id ?: ""}  ·  ${roleLabel(current)}  ·  ${statusLabel(employee.status)}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            HorizontalDivider()

            Text(tr("Role"), style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
            SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                ASSIGNABLE_ROLES.forEachIndexed { i, r ->
                    SegmentedButton(
                        selected = current == r,
                        enabled = !busy,
                        // Confirmed, not applied on tap: the change resets this
                        // person's permissions, and a segmented control is one
                        // stray thumb away from doing that by accident.
                        onClick = { if (current != r) confirmRole = r },
                        shape = SegmentedButtonDefaults.itemShape(i, ASSIGNABLE_ROLES.size),
                    ) { Text(roleLabel(r)) }
                }
            }
            Text(roleExplainer(), style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)

            HorizontalDivider()

            OutlinedButton(onClick = onSetPin, enabled = !busy, modifier = Modifier.fillMaxWidth()) {
                Icon(Icons.Default.Lock, null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text(if (employee.has_pin) tr("Reset till PIN") else tr("Set till PIN"))
            }
            // THE rule, on the screen where the PIN is handed out rather than
            // only in the design document. Design §3: a PIN switches the acting
            // user on an already-signed-in till and is stamped on the rows that
            // follow; it never authorises anything on its own.
            Text(
                tr("A PIN says who is acting at the till. It does not grant permission — " +
                    "voiding a closed sale, changing a price, managing employees and approving " +
                    "a cash difference still ask for the password."),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            HorizontalDivider()

            if (isDisabled) {
                Button(
                    enabled = !busy, modifier = Modifier.fillMaxWidth(),
                    onClick = {
                        run({ ApiClient.get().updateEmployeeStatus(
                            employee.id, UpdateEmployeeStatusRequest("active")) },
                            tr("Account reactivated"))
                    },
                ) { Text(tr("Reactivate account")) }
            } else {
                OutlinedButton(
                    enabled = !busy, modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.outlinedButtonColors(
                        contentColor = MaterialTheme.colorScheme.error),
                    onClick = {
                        run({ ApiClient.get().updateEmployeeStatus(
                            employee.id, UpdateEmployeeStatusRequest("disabled")) },
                            tr("Account deactivated"))
                    },
                ) { Text(tr("Deactivate account")) }
                // Deactivation is reversible and keeps the person's history
                // attached to their account; there is deliberately no delete.
                Text(
                    tr("They are signed out everywhere and cannot sign in again. Their past " +
                        "sales stay on record. You can reactivate them later."),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }

    confirmRole?.let { target ->
        AlertDialog(
            onDismissRequest = { if (!busy) confirmRole = null },
            title = { Text(tr("Change role?")) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(roleExplainer())
                    // Says the destructive part BEFORE the tap. The route
                    // deletes this user's eight capability rows and re-seeds
                    // them from the new role, so any per-person exception the
                    // owner granted earlier is gone.
                    Text(
                        tr("This resets their permissions to that role's defaults, including " +
                            "any exception you granted them before."),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        tr("They are signed out and sign back in with the new role."),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            },
            confirmButton = {
                Button(
                    enabled = !busy,
                    onClick = {
                        confirmRole = null
                        run({ ApiClient.get().updateEmployeeRole(
                            employee.id, UpdateEmployeeRoleRequest(target)) },
                            tr("Role updated"))
                    },
                ) { Text(tr("Change role")) }
            },
            dismissButton = {
                TextButton(onClick = { confirmRole = null }, enabled = !busy) { Text(tr("Cancel")) }
            },
        )
    }
}

// ── PIN ──────────────────────────────────────────────────────────────────────

@Composable
private fun PinDialog(
    employee: Employee,
    onDismiss: () -> Unit,
    onChanged: () -> Unit,
    snackbar: SnackbarHostState,
) {
    var pin by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    // Validated with the SAME fold the server hashes with, so the button is
    // enabled exactly when the server would accept the input -- including for
    // Arabic-Indic digits off an Arabic keypad, which are a valid PIN here and
    // are stored folded so the same PIN works from an ASCII keypad tomorrow.
    val normalized = normalizePin(pin)

    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        icon = { Icon(Icons.Default.Lock, null) },
        title = { Text(if (employee.has_pin) tr("Reset till PIN") else tr("Set till PIN")) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(employee.email ?: "—", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                OutlinedTextField(
                    value = pin,
                    // Cap at the exact PIN length so the field cannot hold
                    // something the server will refuse; no other filtering,
                    // because rejecting non-ASCII digits at the keystroke would
                    // make an Arabic keypad look broken.
                    onValueChange = { if (it.length <= PIN_LENGTH) { pin = it; error = null } },
                    label = { Text(tr("New 4-digit PIN")) },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                    modifier = Modifier.fillMaxWidth(),
                )
                Text(
                    tr("A PIN says who is acting at the till. It does not grant permission — " +
                        "voiding a closed sale, changing a price, managing employees and approving " +
                        "a cash difference still ask for the password."),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (employee.has_pin) {
                    TextButton(
                        enabled = !busy,
                        onClick = {
                            busy = true
                            scope.launch {
                                // Dialog closed before the snackbar for the
                                // same reason as `run()` above -- showSnackbar
                                // suspends for the snackbar's whole lifetime.
                                try {
                                    val r = ApiClient.get().clearEmployeePin(employee.id)
                                    busy = false
                                    if (r.success) {
                                        onChanged()
                                        snackbar.showSnackbar(tr("PIN removed"))
                                    } else error = tr(r.error ?: "The change could not be saved.")
                                } catch (e: Exception) {
                                    busy = false
                                    error = apiErrorMessage(e)
                                }
                            }
                        },
                    ) { Text(tr("Remove this PIN")) }
                }
                error?.let {
                    Text(it, color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = {
            Button(
                enabled = !busy && normalized != null,
                onClick = {
                    val value = normalized ?: return@Button
                    busy = true; error = null
                    scope.launch {
                        try {
                            val r = ApiClient.get().setEmployeePin(
                                employee.id, SetEmployeePinRequest(value))
                            busy = false
                            if (r.success) { onChanged(); snackbar.showSnackbar(tr("PIN saved")) }
                            // "PIN must be exactly 4 digits." is the server's
                            // own literal (user_accounts.PinPolicyError) and is
                            // in both locale catalogs.
                            else error = tr(r.error ?: "The change could not be saved.")
                        } catch (e: Exception) {
                            busy = false
                            error = apiErrorMessage(e)
                        }
                    }
                },
            ) {
                if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                else Text(tr("Save PIN"))
            }
        },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !busy) { Text(tr("Cancel")) } },
    )
}
