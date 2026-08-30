package com.actionaura.retail.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.net.Branch
import com.actionaura.retail.net.SetDeviceBranchRequest
import com.actionaura.retail.net.apiErrorMessage
import com.actionaura.retail.ui.CAP_EMPLOYEES
import com.actionaura.retail.ui.RetailSession
import com.actionaura.retail.ui.components.SectionHeader
import com.actionaura.retail.ui.i18n.AppLang
import com.actionaura.retail.ui.i18n.AppLocale
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.theme.Warning
import kotlinx.coroutines.launch
import retrofit2.HttpException

@Composable
fun SettingsScreen(snackbar: SnackbarHostState) {
    val ctx = LocalContext.current
    var notifications by remember { mutableStateOf(true) }
    var biometric by remember { mutableStateOf(false) }
    var showLanguage by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    fun soon(name: String) = scope.launch { snackbar.showSnackbar(name + tr(" — coming soon")) }

    // ── This device's branch pin (Wave C1 -- see Models.kt's Branch/
    // DeviceBranch doc comments for the dc22b04 defect this closes on
    // Android). Loaded independently of the rest of Settings: a failed load
    // here must not take down Theme/Language/Security/About, so it gets its
    // own loading/error state instead of gating the whole screen.
    var branchLoading by remember { mutableStateOf(true) }
    var branchLoadError by remember { mutableStateOf<String?>(null) }
    var pinnedBranchUid by remember { mutableStateOf<String?>(null) }
    var pinnedBranchName by remember { mutableStateOf<String?>(null) }
    // Flips true only if the POST itself comes back 403 -- a defensive net
    // for the moment RetailSession.capabilities is still null (fails open,
    // see holdsCapability's doc comment) and this row briefly rendered as
    // editable for an account that turns out not to hold CAP_EMPLOYEES.
    var branchForbidden by remember { mutableStateOf(false) }
    var showBranchPicker by remember { mutableStateOf(false) }

    suspend fun loadDeviceBranch() {
        try {
            val d = ApiClient.get().deviceBranch().data
            pinnedBranchUid = d?.branch_uid
            pinnedBranchName = d?.branch_name
            branchLoadError = null
        } catch (e: Exception) {
            branchLoadError = apiErrorMessage(e)
        }
    }
    LaunchedEffect(Unit) { branchLoading = true; loadDeviceBranch(); branchLoading = false }
    // A cashier holds CAP_SELL but not CAP_EMPLOYEES (see RetailSession.kt's
    // CAP_EMPLOYEES doc comment) -- this decides whether the row below is a
    // picker or a read-only fact with an explanation, BEFORE the POST is ever
    // attempted, so the common case never needs to hit a 403 to know its own
    // state.
    val canManageBranch = RetailSession.hasCapability(CAP_EMPLOYEES) && !branchForbidden

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(18.dp)) {

        SettingsGroup(tr("Appearance")) {
            SettingRow(Icons.Default.Palette, tr("Theme"), tr("System default")) { soon(tr("Theme")) }
            // Language is the one that actually does something — opens an in-app picker
            // and switches the whole UI (incl. RTL) immediately, no app restart.
            SettingRow(Icons.Default.Language, tr("Language"), AppLocale.lang.nativeName) { showLanguage = true }
            SettingToggle(Icons.Default.Notifications, tr("Notifications"),
                tr("Reminders & alerts"), notifications) { notifications = it }
        }

        SettingsGroup(tr("This Device's Branch")) {
            when {
                branchLoading -> ListItem(
                    headlineContent = { Text(tr("Loading…")) },
                    leadingContent = { Icon(Icons.Default.Storefront, null, tint = MaterialTheme.colorScheme.primary) },
                )
                branchLoadError != null -> ListItem(
                    headlineContent = { Text(tr("Couldn't load this device's branch")) },
                    supportingContent = { Text(branchLoadError!!) },
                    leadingContent = { Icon(Icons.Default.Storefront, null, tint = MaterialTheme.colorScheme.error) },
                    trailingContent = {
                        TextButton(onClick = {
                            scope.launch { branchLoading = true; loadDeviceBranch(); branchLoading = false }
                        }) { Text(tr("Try again")) }
                    },
                )
                else -> {
                    // Unpinned is the state that silently files every sale on
                    // this till under the company's default branch -- worth
                    // noticing on a chain, but not an error on the single-
                    // branch shop this same install might be. So: colored and
                    // iconed like the rest of this app's Warning states
                    // (EmployeesScreen.statusLabel's "pending_setup" is the
                    // same idiom), never Danger/red.
                    val unpinned = pinnedBranchName == null
                    ListItem(
                        headlineContent = {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    pinnedBranchName ?: tr("No branch pinned"),
                                    fontWeight = FontWeight.Medium,
                                    color = if (unpinned) Warning else MaterialTheme.colorScheme.onSurface,
                                )
                                if (unpinned) {
                                    Spacer(Modifier.width(6.dp))
                                    Icon(Icons.Default.WarningAmber, null, tint = Warning,
                                        modifier = Modifier.size(16.dp))
                                }
                            }
                        },
                        supportingContent = {
                            Text(
                                if (unpinned)
                                    tr("Sales on this till file under the company's default branch. Worth checking on a multi-branch chain.")
                                else tr("Sales rung on this till are filed under this branch."),
                                color = if (unpinned) Warning else MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        },
                        leadingContent = { Icon(Icons.Default.Storefront, null, tint = MaterialTheme.colorScheme.primary) },
                        trailingContent = if (canManageBranch) {
                            { Icon(Icons.Default.ChevronRight, null, tint = MaterialTheme.colorScheme.onSurfaceVariant) }
                        } else null,
                        modifier = if (canManageBranch) Modifier.clickable(onClick = { showBranchPicker = true }) else Modifier,
                    )
                    // Never a silent no-op: a cashier or manager sees exactly
                    // why the row doesn't open, without having to tap it
                    // first to find out. Matches EmployeesScreen's owner-gate
                    // explanation in tone.
                    if (!canManageBranch) {
                        Text(
                            tr("Only the owner can change which branch this device is pinned to. Ask the owner to make the change on their account."),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(start = 16.dp, end = 16.dp, bottom = 12.dp),
                        )
                    }
                }
            }
        }

        SettingsGroup(tr("Security")) {
            SettingToggle(Icons.Default.Fingerprint, tr("Biometric lock"),
                tr("Unlock with fingerprint"), biometric) { biometric = it }
            SettingRow(Icons.Default.Lock, tr("Change password"), null) { soon(tr("Change password")) }
            SettingRow(Icons.Default.Backup, tr("Backup & restore"), null) { soon(tr("Backup & restore")) }
        }

        SettingsGroup(tr("About")) {
            SettingRow(Icons.Default.Info, tr("Version"), "2.5.0-beta.1") {}
            SettingRow(Icons.Default.PrivacyTip, tr("Privacy policy"), null) { soon(tr("Privacy policy")) }
            SettingRow(Icons.Default.Description, tr("Terms of service"), null) { soon(tr("Terms of service")) }
        }
    }

    if (showLanguage) {
        AlertDialog(
            onDismissRequest = { showLanguage = false },
            title = { Text(tr("Choose language")) },
            text = {
                Column {
                    AppLang.entries.forEach { lang ->
                        Row(
                            Modifier.fillMaxWidth()
                                .selectable(selected = AppLocale.lang == lang, onClick = {
                                    AppLocale.set(ctx, lang)
                                    showLanguage = false
                                })
                                .padding(vertical = 12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            RadioButton(selected = AppLocale.lang == lang, onClick = {
                                AppLocale.set(ctx, lang); showLanguage = false
                            })
                            Spacer(Modifier.width(8.dp))
                            Text(lang.nativeName, style = MaterialTheme.typography.bodyLarge)
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { showLanguage = false }) { Text(tr("Done")) }
            },
        )
    }

    if (showBranchPicker) {
        BranchPickerDialog(
            currentUid = pinnedBranchUid,
            onDismiss = { showBranchPicker = false },
            onSaved = { uid, name ->
                pinnedBranchUid = uid
                pinnedBranchName = name
                showBranchPicker = false
            },
            onForbidden = {
                branchForbidden = true
                showBranchPicker = false
            },
            snackbar = snackbar,
        )
    }
}

// ── This device's branch picker (Wave C1) ───────────────────────────────────
/**
 * Only ever shown to an account `canManageBranch` in [SettingsScreen] above
 * already judged able to save. The POST is still wrapped in its own 403
 * check here regardless, because that client-side judgment can be stale (a
 * role changed elsewhere, or `RetailSession.capabilities` had not resolved
 * yet) and the server's refusal is the one that actually matters;
 * [onForbidden] is how this dialog reports that back so the row behind it
 * can drop into its read-only explanation instead of silently reopening the
 * same way next time.
 */
@Composable
private fun BranchPickerDialog(
    currentUid: String?,
    onDismiss: () -> Unit,
    onSaved: (uid: String?, name: String?) -> Unit,
    onForbidden: () -> Unit,
    snackbar: SnackbarHostState,
) {
    var loading by remember { mutableStateOf(true) }
    var loadError by remember { mutableStateOf<String?>(null) }
    var branches by remember { mutableStateOf<List<Branch>>(emptyList()) }
    var selected by remember { mutableStateOf(currentUid) }
    var saving by remember { mutableStateOf(false) }
    var saveError by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) {
        try {
            branches = ApiClient.get().branches().data
            loadError = null
        } catch (e: Exception) {
            loadError = apiErrorMessage(e)
        }
        loading = false
    }

    AlertDialog(
        onDismissRequest = { if (!saving) onDismiss() },
        title = { Text(tr("This device's branch")) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    tr("Choose which branch sales rung on this till are filed under."),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(8.dp))
                when {
                    loading -> Box(Modifier.fillMaxWidth().padding(20.dp), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator(Modifier.size(28.dp), strokeWidth = 3.dp)
                    }
                    loadError != null -> Text(loadError!!, color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall)
                    else -> Column {
                        // The explicit "clear the pin" option -- always
                        // first, and never omitted just because the company
                        // happens to have branches: unpinning is a real,
                        // reachable choice, not only an initial default.
                        Row(
                            Modifier.fillMaxWidth()
                                .selectable(selected = selected == null, onClick = { selected = null })
                                .padding(vertical = 10.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            RadioButton(selected = selected == null, onClick = { selected = null })
                            Spacer(Modifier.width(8.dp))
                            Column {
                                Text(tr("No branch pinned"), style = MaterialTheme.typography.bodyLarge)
                                Text(
                                    tr("Falls back to the company's default branch"),
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                        if (branches.isNotEmpty()) HorizontalDivider()
                        branches.forEach { b ->
                            Row(
                                Modifier.fillMaxWidth()
                                    .selectable(selected = selected == b.uid, onClick = { selected = b.uid })
                                    .padding(vertical = 10.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                RadioButton(selected = selected == b.uid, onClick = { selected = b.uid })
                                Spacer(Modifier.width(8.dp))
                                Text(b.name ?: "—", style = MaterialTheme.typography.bodyLarge)
                            }
                        }
                    }
                }
                saveError?.let {
                    Spacer(Modifier.height(8.dp))
                    Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = {
            Button(
                enabled = !saving && !loading && loadError == null,
                onClick = {
                    saving = true; saveError = null
                    scope.launch {
                        try {
                            val r = ApiClient.get().setDeviceBranch(SetDeviceBranchRequest(selected))
                            saving = false
                            val name = r.data?.branch_name
                            onSaved(r.data?.branch_uid, name)
                            snackbar.showSnackbar(
                                if (name != null) tr("This device is now pinned to %s").format(name)
                                else tr("Branch pin cleared")
                            )
                        } catch (e: Exception) {
                            saving = false
                            // A cashier's client-side gate can be stale (see
                            // this dialog's own doc comment) -- the server's
                            // CAP_EMPLOYEES refusal is what actually decides,
                            // and it gets its own honest explanation rather
                            // than falling through to apiErrorMessage's
                            // generic "Blocked by your subscription/license"
                            // wording, which would misname a role problem as
                            // a licensing one.
                            if (e is HttpException && e.code() == 403) {
                                onForbidden()
                                snackbar.showSnackbar(
                                    tr("Only the owner can change this device's branch. Ask the owner to make the change on their account.")
                                )
                            } else {
                                saveError = apiErrorMessage(e)
                            }
                        }
                    }
                },
            ) {
                if (saving) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                else Text(tr("Save"))
            }
        },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !saving) { Text(tr("Cancel")) } },
    )
}

@Composable
private fun SettingsGroup(title: String, content: @Composable ColumnScope.() -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SectionHeader(title)
        ElevatedCard(Modifier.fillMaxWidth()) { Column(content = content) }
    }
}

@Composable
private fun SettingRow(icon: ImageVector, title: String, subtitle: String?, onClick: () -> Unit) {
    ListItem(
        headlineContent = { Text(title, fontWeight = FontWeight.Medium) },
        supportingContent = subtitle?.let { { Text(it) } },
        leadingContent = { Icon(icon, null, tint = MaterialTheme.colorScheme.primary) },
        trailingContent = { Icon(Icons.Default.ChevronRight, null, tint = MaterialTheme.colorScheme.onSurfaceVariant) },
        modifier = Modifier.clickable(onClick = onClick),
    )
}

@Composable
private fun SettingToggle(icon: ImageVector, title: String, subtitle: String?, checked: Boolean, onChange: (Boolean) -> Unit) {
    ListItem(
        headlineContent = { Text(title, fontWeight = FontWeight.Medium) },
        supportingContent = subtitle?.let { { Text(it) } },
        leadingContent = { Icon(icon, null, tint = MaterialTheme.colorScheme.primary) },
        trailingContent = { Switch(checked = checked, onCheckedChange = onChange) },
    )
}
