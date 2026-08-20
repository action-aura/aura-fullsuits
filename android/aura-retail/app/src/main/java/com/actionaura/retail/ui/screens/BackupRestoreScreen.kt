@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.CloudDone
import androidx.compose.material.icons.filled.CloudUpload
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Restore
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.net.BackupEntry
import com.actionaura.retail.net.RestoreBackupRequest
import com.actionaura.retail.net.apiErrorMessage
import com.actionaura.retail.ui.RetailSession
import com.actionaura.retail.ui.components.EmptyState
import com.actionaura.retail.ui.components.SkeletonList
import com.actionaura.retail.ui.i18n.tr
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Admin-only local backup/restore UI (Wave 1A, Part G). Wires the existing
 * Wave 0 / Phase 3.7 secured backend endpoints
 * (commercial_runtime/backup/routes.py) into the mobile app -- see
 * com.actionaura.clinic.ui.screens.BackupRestoreScreen for the identical
 * Clinic-side implementation and its shared design rationale. The backend
 * independently re-checks admin status on every call regardless of what
 * this UI shows.
 *
 * Deliberately no open file picker or arbitrary filesystem path -- backups
 * are created into and restored from the product's own backup directory,
 * referencing only a filename already listed by GET /api/backup/list.
 */
@Composable
fun BackupRestoreScreen(onBack: () -> Unit, snackbar: SnackbarHostState) {
    var backups by remember { mutableStateOf<List<BackupEntry>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var creating by remember { mutableStateOf(false) }
    var restoreTarget by remember { mutableStateOf<BackupEntry?>(null) }
    var restoring by remember { mutableStateOf(false) }
    var restoredMessage by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    suspend fun load() {
        backups = try { ApiClient.get().listBackups().backups } catch (e: Exception) { emptyList() }
    }
    LaunchedEffect(Unit) { loading = true; load(); loading = false }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(tr("Backup & restore")) },
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.Default.ArrowBack, null) } },
            )
        },
    ) { padding ->
        if (!RetailSession.isAdmin) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                EmptyState(Icons.Default.Restore, tr("Admin access required"),
                    tr("Only an administrator account can create or restore backups."))
            }
            return@Scaffold
        }

        Column(Modifier.fillMaxSize().padding(padding).padding(16.dp)) {
            if (restoredMessage != null) {
                Card(Modifier.fillMaxWidth().padding(bottom = 12.dp)) {
                    Text(restoredMessage!!, Modifier.padding(16.dp))
                }
            }
            Button(
                onClick = {
                    creating = true
                    scope.launch {
                        try {
                            val r = ApiClient.get().createBackup()
                            if (r.status == "ok") {
                                snackbar.showSnackbar(tr("Backup created") + ": ${r.filename ?: ""}")
                                load()
                            } else snackbar.showSnackbar(r.message ?: tr("The backup could not be processed."))
                        } catch (e: Exception) {
                            snackbar.showSnackbar(apiErrorMessage(e))
                        } finally { creating = false }
                    }
                },
                enabled = !creating, modifier = Modifier.fillMaxWidth().height(52.dp),
            ) {
                if (creating) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                else { Icon(Icons.Default.CloudUpload, null); Spacer(Modifier.width(8.dp)); Text(tr("Create backup")) }
            }

            Spacer(Modifier.height(20.dp))
            Text(tr("Restore backup"), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))

            when {
                loading -> SkeletonList()
                backups.isEmpty() -> EmptyState(Icons.Default.History, tr("No backups yet"),
                    tr("Create a backup above to see it listed here."))
                else -> LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(backups, key = { it.filename }) { b ->
                        ElevatedCard(Modifier.fillMaxWidth()) {
                            Row(Modifier.padding(16.dp).fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                Icon(Icons.Default.CloudDone, null)
                                Spacer(Modifier.width(12.dp))
                                Column(Modifier.weight(1f)) {
                                    Text(b.filename, style = MaterialTheme.typography.bodyMedium, maxLines = 1)
                                    Text(
                                        SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).format(Date((b.modified_at * 1000).toLong())) +
                                            "  ·  ${b.size / 1024} KB",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                                TextButton(onClick = { restoreTarget = b }) { Text(tr("Restore backup")) }
                            }
                        }
                    }
                }
            }
        }
    }

    restoreTarget?.let { target ->
        AlertDialog(
            onDismissRequest = { if (!restoring) restoreTarget = null },
            title = { Text(tr("Restore this backup?")) },
            text = {
                Text(tr("This will replace all current data on this device with the backup's data. This cannot be undone."))
            },
            confirmButton = {
                Button(
                    onClick = {
                        restoring = true
                        scope.launch {
                            try {
                                val r = ApiClient.get().restoreBackup(RestoreBackupRequest(target.filename))
                                if (r.status == "ok") {
                                    restoredMessage = tr("Restore complete. Restart the app to continue.")
                                    restoreTarget = null
                                } else snackbar.showSnackbar(r.message ?: tr("The backup could not be processed."))
                            } catch (e: Exception) {
                                snackbar.showSnackbar(apiErrorMessage(e))
                            } finally { restoring = false }
                        }
                    },
                    enabled = !restoring,
                ) {
                    if (restoring) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    else Text(tr("Restore backup"))
                }
            },
            dismissButton = {
                TextButton(onClick = { restoreTarget = null }, enabled = !restoring) { Text(tr("Cancel")) }
            },
        )
    }
}
