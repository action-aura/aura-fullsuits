@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.retail.ui.screens

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
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
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.AdminActionResponse
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.net.Branch
import com.actionaura.retail.net.CreateEmployeeRequest
import com.actionaura.retail.net.Employee
import com.actionaura.retail.net.SetEmployeePinRequest
import com.actionaura.retail.net.UpdateEmployeeBranchScopeRequest
import com.actionaura.retail.net.UpdateEmployeeNameRequest
import com.actionaura.retail.net.UpdateEmployeePresetRequest
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
import com.google.gson.JsonParser
import kotlinx.coroutines.launch
import retrofit2.HttpException
import java.text.Normalizer

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

/**
 * The four Registry v9 presets an owner may pick (`user_accounts.ROLE_PRESETS`).
 * A preset decides the base role AND the twelve capability rows in one go, so a
 * user who HAS a `role_preset` is changed through `PUT .../preset` and NEVER
 * through `PUT .../role`: the role route re-seeds capabilities from the bare
 * role and would silently strip an accountant of the books rows the preset
 * gave them. `ASSIGNABLE_ROLES` above stays only for legacy rows whose
 * `role_preset` is null.
 */
internal val ASSIGNABLE_PRESETS = listOf("cashier", "manager", "branch_manager", "accountant")

/** `user_accounts.PRESET_BASE_ROLE`. Sent as `role` on create so the request
 *  agrees with its own preset; the server refuses a mismatch with a 400. */
internal val PRESET_BASE_ROLE = mapOf(
    "cashier" to "cashier",
    "manager" to "manager",
    "branch_manager" to "manager",
    "accountant" to "manager",
)

/** The server's `code` for a branch_manager preset sent without a branch. */
internal const val BRANCH_SCOPE_REQUIRED_CODE = "BRANCH_SCOPE_REQUIRED"

/** `user_accounts.DISPLAY_NAME_MAX`. The server is the authority; this only
 *  keeps the Save button honest. */
internal const val DISPLAY_NAME_MAX = 60

internal fun presetLabel(preset: String?): String = when (preset?.trim()?.lowercase()) {
    "cashier" -> tr("Cashier")
    "manager" -> tr("Manager")
    "branch_manager" -> tr("Branch manager")
    "accountant" -> tr("Accountant")
    else -> preset.orEmpty()
}

/** One fixed sentence per preset, transcribed from `ROLE_PRESETS`. */
private fun presetExplainer(preset: String?): String = when (preset) {
    "manager" -> tr("Everything a cashier can do, plus discounts, stock changes and reports.")
    "branch_manager" -> tr("A manager who can also add and manage staff, for one branch only.")
    "accountant" -> tr("Reads reports and the books, records entries and approves cash differences. Cannot sell at the till.")
    else -> tr("Can sell, refund against a sale and close their own drawer.")
}

/**
 * The job label a row or the manage sheet shows.
 *
 * `preset_matches == false` is the server saying "this account's stored preset
 * no longer matches its capability rows" (someone edited a permission by hand
 * after the preset was applied). Showing "Accountant" then would be a label
 * that lies about what the person can do, so it reads "Custom" -- the same word
 * the desktop screen uses. Only an EXPLICIT false does that: null means an
 * older server that never sent the field, and must keep the preset's own name.
 * A blank preset falls back to the legacy role label.
 */
internal fun employeeJobLabel(rolePreset: String?, presetMatches: Boolean?, effectiveRole: String?): String {
    val preset = rolePreset?.trim()?.lowercase()?.takeIf { it.isNotEmpty() }
        ?: return roleLabel(effectiveRole)
    return if (presetMatches == false) tr("Custom") else presetLabel(preset)
}

/**
 * The preset the picker should show as CHOSEN: the stored one, unless the
 * server says it no longer matches (then nothing is chosen, so tapping any
 * preset -- including the stored one, which re-applies it -- is a real change).
 * Null for a legacy row with no preset at all.
 */
internal fun matchedPreset(rolePreset: String?, presetMatches: Boolean?): String? =
    rolePreset?.trim()?.lowercase()?.takeIf { it.isNotEmpty() && presetMatches != false }

/** A tap on [picked] opens the "Change job?" confirm only when it is a change. */
internal fun shouldConfirmPresetChange(current: String?, picked: String): Boolean = current != picked

/** Only this preset needs a branch (`update_preset` / create_employee). */
internal const val BRANCH_SCOPED_PRESET = "branch_manager"

internal fun presetNeedsBranch(preset: String?): Boolean = preset == BRANCH_SCOPED_PRESET

// -- Name, judged the way `text_safety.clean_single_line` judges it ---------
//
// Kept format characters: ZWNJ, ZWJ, LRM, RLM and ALM. Every OTHER Cc/Cf
// character is stripped before counting, so an invisible pad cannot push a name
// over the limit on the phone while the server would accept it (or the
// reverse). Line breaks are refused, not joined.
private val NAME_KEPT_FORMAT = setOf(0x200C, 0x200D, 0x200E, 0x200F, 0x061C)
private val NAME_LINE_BREAKS = setOf(0x0A, 0x0D, 0x0B, 0x0C, 0x1C, 0x1D, 0x1E, 0x85, 0x2028, 0x2029)

// String.codePoints() needs API 24; a manual walk works on every device.
private fun codePointsOf(s: String): List<Int> {
    val out = ArrayList<Int>(s.length)
    var i = 0
    while (i < s.length) {
        val cp = s.codePointAt(i)
        out.add(cp)
        i += Character.charCount(cp)
    }
    return out
}

internal fun nameHasLineBreak(raw: String): Boolean = codePointsOf(raw).any { it in NAME_LINE_BREAKS }

/** NFC, Cc/Cf stripped (except the kept five), trimmed. Blank when nothing is left. */
internal fun cleanName(raw: String): String {
    val stripped = StringBuilder()
    codePointsOf(raw).forEach { cp ->
        val type = Character.getType(cp)
        val control = type == Character.CONTROL.toInt() || type == Character.FORMAT.toInt()
        if (!control || cp in NAME_KEPT_FORMAT) stripped.appendCodePoint(cp)
    }
    return Normalizer.normalize(stripped, Normalizer.Form.NFC).trim()
}

/** Name length as the server counts it: code points of the cleaned name. Blank is 0. */
internal fun nameLength(raw: String): Int = cleanName(raw).let { it.codePointCount(0, it.length) }

/** The English sentence for what is wrong with [raw], or null when the server
 *  would accept it (a blank name is accepted: it means "no name"). Callers
 *  pass it through `tr`. */
internal fun nameProblem(raw: String): String? = when {
    nameHasLineBreak(raw) -> "Name must be a single line."
    nameLength(raw) > DISPLAY_NAME_MAX -> "Name must be 60 characters or fewer."
    else -> null
}

/**
 * The invite request exactly as it goes on the wire. `role` is the preset's BASE
 * role (accountant and branch_manager ride on `manager`), never a fixed
 * "cashier": the server rejects a role that does not match the preset with
 * "Role does not match the chosen preset.". The branch uid is sent only for the
 * branch-scoped preset; the server ignores it for the others but a stale uid
 * from an earlier pick must not travel.
 */
internal fun buildCreateEmployeeRequest(
    email: String, name: String, preset: String, branchUid: String?,
): CreateEmployeeRequest = CreateEmployeeRequest(
    email.trim().lowercase(),
    PRESET_BASE_ROLE.getValue(preset),
    name = cleanName(name).ifEmpty { null },
    preset = preset,
    branch_scope_uid = if (presetNeedsBranch(preset)) branchUid else null,
)

/** True when a body is the server's `BRANCH_SCOPE_REQUIRED` refusal. Reads the
 *  `code` field, never the sentence, so a reworded message cannot break it. */
internal fun isBranchScopeRequiredBody(raw: String?): Boolean = runCatching {
    !raw.isNullOrBlank() && JsonParser.parseString(raw).asJsonObject
        .get("code")?.takeUnless { it.isJsonNull }?.asString == BRANCH_SCOPE_REQUIRED_CODE
}.getOrDefault(false)

/**
 * The plain sentence for a `BRANCH_SCOPE_REQUIRED` 400, or null for any other
 * failure so the caller falls through to the shared `apiErrorMessage`.
 *
 * Reads the body with `peek()`, which does NOT consume it. `apiErrorMessage`
 * reads the same one-shot error body afterwards; a plain `string()` here would
 * leave every OTHER 400 with an empty body and a generic message.
 */
internal fun branchScopeMessage(e: Throwable): String? {
    if (e !is HttpException || e.code() != 400) return null
    val raw = runCatching { e.response()?.errorBody()?.source()?.peek()?.readUtf8() }.getOrNull()
    return if (isBranchScopeRequiredBody(raw))
        tr("Choose a branch for this person before making them a branch manager.")
    else null
}

/**
 * How often this screen re-reads the staff list while it is open.
 *
 * WHY THIS SCREEN POLLS AT ALL, since no other list here does. Every row on it
 * can be changed by a DIFFERENT DEVICE: an owner invites a cashier on the
 * desktop till, that cashier sets their password, and the row moves out of
 * `pending_setup` into `active` -- all without this phone touching anything.
 * The screen used to load once, on first composition, and never again, so an
 * owner sitting on it watched a stale "Invite pending" badge for a cashier who
 * had finished setting up minutes earlier, with nothing on screen admitting
 * the data was old. Measured on real hardware 2026-09-14: the row read
 * "Invite pending" while the device's own registry.db already said `active`.
 *
 * 10 seconds because that is `SyncService.start`'s own tick -- the rate at
 * which the underlying rows can actually change. Polling faster would re-read
 * a database that cannot have moved; slower would leave the screen wrong for
 * longer than the data is.
 */
private const val EMPLOYEE_REFRESH_MILLIS = 10_000L

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
    // Distinct from `loadError` for the same reason `loadError` is distinct
    // from an empty list: "I have never been able to read this" and "I read it
    // ten seconds ago and could not re-read it just now" are different facts,
    // and they deserve different screens. The first replaces the list; this
    // one sits beside rows that are still worth showing.
    var refreshError by remember { mutableStateOf<String?>(null) }
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
    /**
     * Re-read the list without disturbing the screen.
     *
     * Two deliberate differences from `load()`, both of which matter because
     * this runs unattended every [EMPLOYEE_REFRESH_MILLIS]:
     *
     *  - it never touches `loading`, so the skeleton does not flash over a
     *    list the owner is reading every ten seconds;
     *  - a FAILURE changes nothing. `load()` correctly replaces the list with
     *    an error, because a failed FIRST load genuinely means the owner has
     *    no data. A failed background refresh means the last known list is
     *    still the best thing to show -- swallowing a momentary blip and
     *    keeping good rows on screen beats replacing them with an error
     *    banner nobody asked for, and the next tick recovers on its own.
     *    Real failures are still reported: the first load reports them, and
     *    every mutating action on this screen reports its own.
     */
    suspend fun refreshQuietly() {
        try {
            val body = ApiClient.get().employees().employees ?: return
            employees = body
            loadError = null
            refreshError = null
        } catch (e: Exception) {
            // Routed through the SAME shared mapping every other failure on
            // this screen uses -- never swallowed. An earlier draft of this
            // function discarded the exception entirely on the grounds that a
            // background tick should not shout, and EmployeesWiringContractTest
            // caught it. That test is right, and for a reason sharper than
            // consistency: silently keeping stale rows on screen is a milder
            // version of the exact bug this refresh exists to fix. The owner
            // must be able to tell "this is current" from "this is the last
            // thing I could read".
            //
            // So the rows STAY (a dropped request is not a reason to destroy a
            // good list) and the failure is surfaced beside them instead.
            refreshError = apiErrorMessage(e)
        }
    }

    LaunchedEffect(Unit) {
        loading = true; load(); loading = false
        // Cancelled automatically when the screen leaves composition, so this
        // never outlives the screen it serves.
        while (true) {
            kotlinx.coroutines.delay(EMPLOYEE_REFRESH_MILLIS)
            refreshQuietly()
        }
    }

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
                    // Only when the rows on screen might have stopped being
                    // true. Says the rows are old rather than that they are
                    // wrong, because they are the last thing this device could
                    // actually read -- and names the real reason via the
                    // shared mapping instead of a blanket sentence.
                    refreshError?.let { why ->
                        Text(
                            tr("Showing the last list this device could read — ") + why,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.error,
                        )
                        Spacer(Modifier.height(6.dp))
                    }
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
                // The name leads when the owner has given one; the email is
                // still shown under it because it is what the person signs in with.
                Text(
                    e.display_name?.takeIf { it.isNotBlank() } ?: e.email ?: "—",
                    fontWeight = FontWeight.Bold, maxLines = 1,
                )
                if (!e.display_name.isNullOrBlank()) {
                    Text(
                        e.email ?: "",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                    )
                }
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
                    // The preset label when there is one ("Accountant" says more
                    // than the base role "Manager" it rides on) -- or "Custom"
                    // when the server says the rows no longer match it.
                    employeeJobLabel(e.role_preset, e.preset_matches, e.effective_role ?: e.role),
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
    var name by remember { mutableStateOf("") }
    // A preset, not a bare role: it decides the role AND the capability rows.
    var preset by remember { mutableStateOf(ASSIGNABLE_PRESETS.first()) }
    // Only meaningful for branch_manager; the confirm button needs it there.
    var branchUid by remember { mutableStateOf<String?>(null) }
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
                // Registry v9 gave `users` a display name, so this box is now
                // real: the server keeps it (NFC, 1-60 chars) and every till
                // shows it. Optional on create -- the invite can go out before
                // the owner knows the name; blank simply means "no name yet".
                OutlinedTextField(
                    value = name, onValueChange = { name = it; error = null },
                    label = { Text(tr("Name (optional)")) },
                    singleLine = true,
                    isError = nameProblem(name) != null,
                    supportingText = { nameProblem(name)?.let { Text(tr(it)) } },
                    modifier = Modifier.fillMaxWidth(),
                )

                Text(tr("Role"), style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
                PresetPicker(selected = preset, enabled = !busy, onPick = { preset = it; error = null })
                Text(presetExplainer(preset), style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (presetNeedsBranch(preset)) {
                    BranchScopePicker(branchUid, enabled = !busy, onPick = { branchUid = it; error = null })
                }

                error?.let {
                    Text(it, color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = {
            Button(
                enabled = !busy && email.isNotBlank() && nameProblem(name) == null &&
                    (!presetNeedsBranch(preset) || !branchUid.isNullOrBlank()),
                onClick = {
                    busy = true; error = null
                    scope.launch {
                        try {
                            val r = ApiClient.get().createEmployee(
                                buildCreateEmployeeRequest(email, name, preset, branchUid))
                            // The route answers 200 with {"success": true,
                            // "setup_link": ...}. A 200 that carries neither is
                            // not something to celebrate silently, so the
                            // server's own `error` is shown when it sent one.
                            if (r.success) onCreated(inviteTokenOf(r.setup_link))
                            else error = tr(r.error ?: "The invite could not be created.")
                        } catch (e: Exception) {
                            // A duplicate email is a 400 whose body carries
                            // "Email already registered." -- apiErrorMessage
                            // surfaces the server's own sentence, translated.
                            // A branch_manager sent without a branch is a 400
                            // with code BRANCH_SCOPE_REQUIRED; the picker above
                            // prevents it, but a stale answer still gets a plain
                            // sentence rather than a raw code.
                            error = branchScopeMessage(e) ?: apiErrorMessage(e)
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

/** Four presets as a radio list: a segmented row of four labels does not fit
 *  a phone in Arabic. */
@Composable
private fun PresetPicker(selected: String?, enabled: Boolean, onPick: (String) -> Unit) {
    Column(Modifier.fillMaxWidth().selectableGroup()) {
        ASSIGNABLE_PRESETS.forEach { p ->
            Row(
                Modifier.fillMaxWidth()
                    .selectable(
                        selected = selected == p, enabled = enabled,
                        role = Role.RadioButton, onClick = { onPick(p) },
                    )
                    .padding(vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                RadioButton(selected = selected == p, onClick = null, enabled = enabled)
                Spacer(Modifier.width(10.dp))
                Text(presetLabel(p))
            }
        }
    }
}

/** The branches a branch manager may be limited to: active, and carrying the
 *  uid the server keys the scope on (never the integer id). */
internal fun scopableBranches(all: List<Branch>): List<Branch> =
    all.filter { !it.uid.isNullOrBlank() && (it.status ?: "active").lowercase() == "active" }

/**
 * "Which branch?" for a branch_manager. The server refuses that preset without
 * one (400 BRANCH_SCOPE_REQUIRED), so the person choosing it is asked here, up
 * front, instead of being told after the fact. Loads the branch list itself;
 * an empty list or a load failure is said in words and leaves nothing chosen,
 * which keeps the caller's confirm button disabled.
 */
@Composable
private fun BranchScopePicker(selectedUid: String?, enabled: Boolean, onPick: (String) -> Unit) {
    var loading by remember { mutableStateOf(true) }
    var loadError by remember { mutableStateOf<String?>(null) }
    var branches by remember { mutableStateOf<List<Branch>>(emptyList()) }
    LaunchedEffect(Unit) {
        try {
            branches = scopableBranches(ApiClient.get().branches().data)
            loadError = null
        } catch (e: Exception) {
            loadError = apiErrorMessage(e)
        }
        loading = false
    }
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(tr("Branch"), style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
        when {
            loading -> CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
            loadError != null -> Text(loadError!!, color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall)
            branches.isEmpty() -> Text(tr("There is no branch to choose yet."),
                color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
            else -> Column(Modifier.fillMaxWidth().selectableGroup()) {
                branches.forEach { b ->
                    val uid = b.uid.orEmpty()
                    Row(
                        Modifier.fillMaxWidth()
                            .selectable(
                                selected = selectedUid == uid, enabled = enabled,
                                role = Role.RadioButton, onClick = { onPick(uid) },
                            )
                            .padding(vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        RadioButton(selected = selectedUid == uid, onClick = null, enabled = enabled)
                        Spacer(Modifier.width(10.dp))
                        Text(b.name ?: "—")
                    }
                }
            }
        }
    }
}

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
    var confirmPreset by remember { mutableStateOf<String?>(null) }
    // The branch chosen inside the "Change job?" confirm, for branch_manager.
    var confirmBranchUid by remember { mutableStateOf(employee.branch_scope_uid) }
    var nameDraft by remember { mutableStateOf(employee.display_name.orEmpty()) }
    val current = (employee.effective_role ?: employee.role)?.trim()?.lowercase() ?: "cashier"
    // Non-null for every account created or edited since registry v9. Such an
    // account is edited through /preset only; see ASSIGNABLE_PRESETS.
    val storedPreset = employee.role_preset?.trim()?.lowercase()?.takeIf { it.isNotEmpty() }
    // What the picker shows as chosen: null for a "Custom" account (the server
    // says its rows no longer match the stored preset), so no preset sentence
    // is shown for it and tapping any preset -- even the stored one -- applies it.
    val currentPreset = matchedPreset(employee.role_preset, employee.preset_matches)
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
                // BRANCH_SCOPE_REQUIRED (a branch_manager preset with no
                // branch) gets its plain sentence; everything else keeps the
                // shared mapping.
                branchScopeMessage(e) ?: apiErrorMessage(e)
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
                    Text(
                        employee.display_name?.takeIf { it.isNotBlank() } ?: employee.email ?: "—",
                        fontWeight = FontWeight.Bold,
                    )
                    Text(
                        "${employee.employee_id ?: ""}  ·  " +
                            employeeJobLabel(employee.role_preset, employee.preset_matches, current) +
                            "  ·  ${statusLabel(employee.status)}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            HorizontalDivider()

            // Name, 0-60 characters as the server counts them. A BLANK box is a
            // real request: PUT .../name clears the name ("blank clears it"),
            // so the owner can take a name off, not only replace it.
            OutlinedTextField(
                value = nameDraft, onValueChange = { nameDraft = it },
                label = { Text(tr("Name")) },
                singleLine = true,
                enabled = !busy,
                isError = nameProblem(nameDraft) != null,
                supportingText = { nameProblem(nameDraft)?.let { Text(tr(it)) } },
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedButton(
                enabled = !busy && nameProblem(nameDraft) == null &&
                    cleanName(nameDraft) != employee.display_name.orEmpty(),
                modifier = Modifier.fillMaxWidth(),
                onClick = {
                    run({ ApiClient.get().updateEmployeeName(
                        employee.id, UpdateEmployeeNameRequest(cleanName(nameDraft))) },
                        tr("Name saved"))
                },
            ) { Text(tr("Save name")) }

            HorizontalDivider()

            Text(tr("Role"), style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
            if (storedPreset != null) {
                // A preset account: pick a preset, confirmed, sent to /preset.
                PresetPicker(
                    selected = currentPreset, enabled = !busy,
                    onPick = { if (shouldConfirmPresetChange(currentPreset, it)) confirmPreset = it },
                )
                // No sentence for a "Custom" account: none of the four describes
                // what this person can actually do any more.
                if (currentPreset != null) {
                    Text(presetExplainer(currentPreset), style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            } else {
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
            }

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
                    // deletes this user's twelve capability rows and re-seeds
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

    // A preset account is changed through PUT .../preset and NEVER through
    // /role: /role would set the base role and clear role_preset, leaving the
    // person labelled by a role that no longer matches their twelve rows.
    confirmPreset?.let { target ->
        AlertDialog(
            onDismissRequest = { if (!busy) confirmPreset = null },
            title = { Text(tr("Change job?")) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(presetExplainer(target))
                    Text(
                        tr("This resets their permissions to that job's defaults, including " +
                            "any exception you granted them before."),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    if (presetNeedsBranch(target)) {
                        BranchScopePicker(confirmBranchUid, enabled = !busy, onPick = { confirmBranchUid = it })
                    }
                }
            },
            confirmButton = {
                Button(
                    enabled = !busy && (!presetNeedsBranch(target) || !confirmBranchUid.isNullOrBlank()),
                    onClick = {
                        confirmPreset = null
                        val scopeUid = if (presetNeedsBranch(target)) confirmBranchUid else null
                        run({
                            // update_preset reads NO scope from its own body: it only
                            // checks the row already has one (else 400
                            // BRANCH_SCOPE_REQUIRED). So the branch is written first,
                            // through its own route, and the preset second.
                            val scoped = if (scopeUid != null)
                                ApiClient.get().updateEmployeeBranchScope(
                                    employee.id, UpdateEmployeeBranchScopeRequest(scopeUid))
                            else null
                            if (scoped != null && !scoped.success) scoped
                            else ApiClient.get().updateEmployeePreset(
                                employee.id, UpdateEmployeePresetRequest(target))
                        }, tr("Job updated"))
                    },
                ) { Text(tr("Change job")) }
            },
            dismissButton = {
                TextButton(onClick = { confirmPreset = null }, enabled = !busy) { Text(tr("Cancel")) }
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
