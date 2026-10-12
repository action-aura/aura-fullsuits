@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.pctText
import com.actionaura.retail.ui.i18n.parseNum
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.pos.DiscountAccess
import com.actionaura.retail.ui.pos.clampDiscountPct
import com.actionaura.retail.ui.pos.maxPct
import com.actionaura.retail.ui.theme.Danger

// Discount controls for the till (ANDROID-2a). Every rule (who may discount, how far)
// is decided in ui/pos/DiscountRules.kt; these composables only collect a percentage and
// show the limit. Nothing here prices a sale, and the server re-checks every request.

/** The discount percentage for ONE line. [current] null = the line follows the whole-sale percentage. */
@Composable
internal fun LineDiscountDialog(
    productName: String, current: Double?, access: DiscountAccess,
    onSet: (Double?) -> Unit, onDismiss: () -> Unit,
) {
    var text by remember { mutableStateOf(current?.let { pctText(it) } ?: "") }
    val asked = clampDiscountPct(parseNum(text))
    val max = access.maxPct()
    val tooHigh = asked > max + 1e-9
    LocalizedAlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(tr("Discount") + " — " + productName, maxLines = 2) },
        text = {
            Column {
                OutlinedTextField(
                    value = text, onValueChange = { text = it }, singleLine = true,
                    label = { Text(tr("Discount %")) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    isError = tooHigh,
                    modifier = Modifier.fillMaxWidth(),
                )
                if (access is DiscountAccess.Limited) {
                    Spacer(Modifier.height(6.dp))
                    Text(tr("Up to %s%%").format(pctText(max)), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (tooHigh) {
                    Spacer(Modifier.height(6.dp))
                    Text(tr("You can give at most %s%% discount. Ask a manager for more.").format(pctText(max)),
                        style = MaterialTheme.typography.bodySmall, color = Danger)
                }
            }
        },
        confirmButton = {
            Button(onClick = { onSet(if (asked > 0.0) asked else null) }, enabled = !tooHigh) { Text(tr("Set")) }
        },
        dismissButton = {
            Row {
                if (current != null) TextButton(onClick = { onSet(null) }) { Text(tr("Clear")) }
                TextButton(onClick = onDismiss) { Text(tr("Cancel")) }
            }
        },
    )
}

/** The whole-sale discount box. Rendered only when [access] is not None. */
@Composable
internal fun SaleDiscountField(access: DiscountAccess, text: String, onChange: (String) -> Unit) {
    val asked = clampDiscountPct(parseNum(text))
    val tooHigh = asked > access.maxPct() + 1e-9
    Column(Modifier.fillMaxWidth()) {
        OutlinedTextField(
            value = text, onValueChange = onChange, singleLine = true,
            label = { Text(tr("Discount on the whole sale (%)")) },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            isError = tooHigh,
            supportingText = {
                when {
                    tooHigh -> Text(tr("You can give at most %s%% discount. Ask a manager for more.").format(pctText(access.maxPct())))
                    access is DiscountAccess.Limited -> Text(tr("Up to %s%%").format(pctText(access.limitPct)))
                }
            },
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

