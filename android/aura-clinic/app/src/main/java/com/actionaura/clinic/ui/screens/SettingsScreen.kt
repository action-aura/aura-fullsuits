package com.actionaura.clinic.ui.screens

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
import com.actionaura.clinic.ui.components.SectionHeader
import com.actionaura.clinic.ui.i18n.AppLang
import com.actionaura.clinic.ui.i18n.AppLocale
import com.actionaura.clinic.ui.i18n.tr
import kotlinx.coroutines.launch

@Composable
fun SettingsScreen(snackbar: SnackbarHostState, onOpenBackup: () -> Unit = {}) {
    val ctx = LocalContext.current
    var notifications by remember { mutableStateOf(true) }
    var biometric by remember { mutableStateOf(false) }
    var showLanguage by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    fun soon(name: String) = scope.launch { snackbar.showSnackbar(name + tr(" — coming soon")) }

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

        SettingsGroup(tr("Security")) {
            SettingToggle(Icons.Default.Fingerprint, tr("Biometric lock"),
                tr("Unlock with fingerprint"), biometric) { biometric = it }
            SettingRow(Icons.Default.Lock, tr("Change password"), null) { soon(tr("Change password")) }
            // Phase 4L / Wave 1A: was a "coming soon" placeholder (AUDIT-027),
            // now wired to the real, admin-gated Wave 0 backup/restore
            // endpoints -- see BackupRestoreScreen.kt. Visible to all staff
            // (matches this row's existing visibility) but the screen itself
            // only shows backup/restore actions to an admin session; the
            // backend independently enforces the same gate.
            SettingRow(Icons.Default.Backup, tr("Backup & restore"), null) { onOpenBackup() }
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
