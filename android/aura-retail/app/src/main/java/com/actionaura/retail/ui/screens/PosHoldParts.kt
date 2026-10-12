@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.HeldSaleRow
import com.actionaura.retail.ui.i18n.ltrIsolate
import com.actionaura.retail.ui.i18n.money
import com.actionaura.retail.ui.i18n.tr

// Hold / resume pieces of the till (ANDROID-2a). The state lives on the shop server
// (the desktop's own /held-sales routes); these composables only show it.

/** "Hold This Sale" -- the optional note, exactly the desktop's `_holdSale` dialog. */
@Composable
internal fun HoldDialog(busy: Boolean, onHold: (String) -> Unit, onDismiss: () -> Unit) {
    var note by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text(tr("Hold This Sale")) },
        text = {
            Column {
                Text(tr("The cart is parked and cleared here so you can start a new sale. Resume it later from Held sales."),
                    style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = note, onValueChange = { note = it.take(200) }, singleLine = true,
                    label = { Text(tr("Note (optional)")) },
                    placeholder = { Text(tr("e.g. Table 4, customer name")) },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = { Button(onClick = { onHold(note) }, enabled = !busy) { Text(tr("Hold Sale")) } },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !busy) { Text(tr("Cancel")) } },
    )
}

@Composable
internal fun ConfirmDialog(
    title: String, message: String, confirmLabel: String,
    onConfirm: () -> Unit, onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(message) },
        confirmButton = { Button(onClick = onConfirm) { Text(confirmLabel) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(tr("Cancel")) } },
    )
}

/** The server's parked sales. `rows == null` means the list is still loading or failed. */
@Composable
internal fun HeldSalesSheet(
    rows: List<HeldSaleRow>?,
    onResume: (HeldSaleRow) -> Unit,
    onDiscard: (HeldSaleRow) -> Unit,
    onDismiss: () -> Unit,
) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp).verticalScroll(rememberScrollState())) {
            Text(tr("Held sales"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(4.dp))
            Text(tr("A held sale is kept on this device's shop server, so any till can resume it."),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(12.dp))
            when {
                rows == null -> Text(tr("Loading…"), color = MaterialTheme.colorScheme.onSurfaceVariant)
                rows.isEmpty() -> Text(tr("No held sales."), color = MaterialTheme.colorScheme.onSurfaceVariant)
                else -> rows.forEach { h ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(
                                ltrIsolate(h.hold_number ?: "") + (h.label?.takeIf { it.isNotBlank() }?.let { " — $it" } ?: ""),
                                fontWeight = FontWeight.Bold,
                            )
                            Text(
                                tr("%d item(s)").format(h.item_count) + " · " + (h.customer_name ?: tr("Walk-in customer")) +
                                    " · " + (h.created_at ?: "").replace("T", " ").take(16),
                                style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Text(money(h.total), style = MaterialTheme.typography.labelLarge,
                                color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.Bold)
                        }
                        TextButton(onClick = { onDiscard(h) }) { Text(tr("Discard")) }
                        Spacer(Modifier.width(4.dp))
                        Button(onClick = { onResume(h) }) { Text(tr("Resume")) }
                    }
                    HorizontalDivider()
                }
            }
        }
    }
}

