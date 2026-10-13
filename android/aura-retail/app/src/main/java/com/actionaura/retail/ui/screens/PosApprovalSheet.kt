@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class, androidx.compose.foundation.layout.ExperimentalLayoutApi::class)

package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.ApprovalReq
import com.actionaura.retail.net.Approver
import com.actionaura.retail.ui.i18n.PIN_LENGTH
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.pos.KEY_BACK
import com.actionaura.retail.ui.pos.pinEdit

/**
 * "Ask a manager" -- the sheet a cashier hands to a manager when a discount is past the cashier's
 * limit or a line carries a price of its own (the desktop's `_askManagerApproval`).
 *
 * The manager is picked from [approvers] (the server's `GET discount/approvers`: a reference and a
 * display name, nothing else), types their PIN on the masked keypad, and [onApprove] hands back
 * {approver, pin} for the ONE sale it approves. The PIN lives only in this composable's state while
 * it is typed: it is not kept by the caller, stored, or logged ([ApprovalReq.toString] masks it).
 * The SERVER verifies it -- this sheet never knows whether a PIN is right, and a wrong one is
 * refused by the sale itself (with the server's own words and its lockout).
 */
@Composable
internal fun ManagerApprovalSheet(
    approvers: List<Approver>,
    onApprove: (ApprovalReq) -> Unit,
    onDismiss: () -> Unit,
) {
    var chosen by remember { mutableStateOf(approvers.firstOrNull()?.ref) }
    var pin by remember { mutableStateOf("") }
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            Modifier.padding(horizontal = 20.dp).padding(bottom = 24.dp).verticalScroll(rememberScrollState()),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(tr("Ask a manager"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold,
                modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(4.dp))
            Text(tr("A manager has to enter their PIN to allow this."), style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(12.dp))
            Text(tr("Manager"), style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.fillMaxWidth())
            FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                approvers.forEach { a ->
                    FilterChip(selected = chosen == a.ref, onClick = { chosen = a.ref }, label = { Text(a.label) })
                }
            }
            Spacer(Modifier.height(14.dp))
            // Masked: filled dots for what was typed, hollow for what is left. Never the digits.
            Text(
                "●".repeat(pin.length) + "○".repeat(PIN_LENGTH - pin.length),
                style = MaterialTheme.typography.headlineMedium,
                modifier = Modifier.semantics { contentDescription = "${pin.length} / $PIN_LENGTH" },
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(10.dp))
            // A keypad reads the same in both languages (digits are digits), so it is laid out LTR.
            CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Ltr) {
                Keypad(keys = PIN_KEYS, onKey = { pin = pinEdit(pin, it) })
            }
            Spacer(Modifier.height(14.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedButton(onClick = onDismiss, modifier = Modifier.weight(1f).height(48.dp)) { Text(tr("Cancel")) }
                Button(
                    onClick = { chosen?.let { onApprove(ApprovalReq(approver = it, pin = pin)) } },
                    enabled = chosen != null && pin.length == PIN_LENGTH,
                    modifier = Modifier.weight(1f).height(48.dp),
                ) { Text(tr("Approve")) }
            }
        }
    }
}

private val PIN_KEYS: List<List<String>> = listOf(
    listOf("7", "8", "9"), listOf("4", "5", "6"), listOf("1", "2", "3"), listOf("", "0", KEY_BACK),
)

/** A 3-column keypad; an empty key is a blank spacer, [KEY_BACK] draws a back arrow. */
@Composable
internal fun Keypad(keys: List<List<String>>, onKey: (String) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.widthIn(max = 320.dp)) {
        keys.forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                row.forEach { k ->
                    if (k.isEmpty()) Spacer(Modifier.weight(1f).height(52.dp))
                    else FilledTonalButton(
                        onClick = { onKey(k) }, modifier = Modifier.weight(1f).height(52.dp),
                    ) { Text(if (k == KEY_BACK) "⌫" else k, style = MaterialTheme.typography.titleLarge) }
                }
            }
        }
    }
}
