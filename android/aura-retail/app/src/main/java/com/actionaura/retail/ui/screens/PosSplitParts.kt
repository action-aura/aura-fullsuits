package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.actionaura.retail.ui.i18n.Currency
import com.actionaura.retail.ui.i18n.money
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.pos.SplitProblem
import com.actionaura.retail.ui.pos.SplitSummary
import com.actionaura.retail.ui.pos.TenderPartMinor
import com.actionaura.retail.ui.pos.fromMinor
import com.actionaura.retail.ui.pos.minorToInput
import com.actionaura.retail.ui.theme.Danger
import com.actionaura.retail.ui.theme.Success
import com.actionaura.retail.ui.theme.Warning

// Split-payment pieces of the till (ANDROID-2a). The rules (what covers the total, what the
// server would refuse) are in ui/pos/SplitTender.kt; these composables only show them.

/** One sentence per reason the parts cannot be charged yet -- the server's refusals, said before the money changes hands. */
internal fun splitProblemText(p: SplitProblem): String = when (p) {
    SplitProblem.NO_PARTS -> tr("Add the first payment part.")
    SplitProblem.TOO_MANY_PARTS -> tr("A sale can be paid in at most 6 parts.")
    SplitProblem.SHORT -> tr("The parts do not cover the amount due yet.")
    SplitProblem.CREDIT_OVERPAID -> tr("The part on account is more than what is left to pay.")
    SplitProblem.NON_CASH_OVERPAID -> tr("Only cash can be more than what is due. Lower the card or other amount.")
    SplitProblem.CASH_NOT_NEEDED -> tr("The cash part would all come back as change. Remove it.")
    SplitProblem.CREDIT_NEEDS_CUSTOMER -> tr("Select a customer for credit sales (walk-in not allowed)")
}

/**
 * Split payment panel. [estimateMinor] is a DISPLAY estimate (SaleEstimate.kt);
 * the server prices the sale and settles the parts against its own figure.
 */
@Composable
internal fun SplitPaymentPanel(
    methodLabel: String,
    estimateMinor: Long,
    parts: List<TenderPartMinor>,
    amountText: String,
    summary: SplitSummary,
    labelFor: (String) -> String,
    onAmountChange: (String) -> Unit,
    onFillRemaining: () -> Unit,
    onAdd: () -> Unit,
    onRemove: (Int) -> Unit,
) {
    val dp = Currency.decimals
    Column(Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(tr("Estimated amount due"), Modifier.weight(1f), style = MaterialTheme.typography.titleSmall)
            Text(money(fromMinor(estimateMinor, dp)), fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary)
        }
        Text(tr("Tax and promotions are confirmed at checkout. If the final amount differs, the till will tell you what is missing."),
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(8.dp))
        parts.forEachIndexed { i, p ->
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(labelFor(p.method), Modifier.weight(1f))
                Text(money(fromMinor(p.minor, dp)), fontWeight = FontWeight.SemiBold)
                IconButton(onClick = { onRemove(i) }) { Icon(Icons.Default.Close, tr("Remove part")) }
            }
        }
        Spacer(Modifier.height(6.dp))
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = amountText, onValueChange = onAmountChange, singleLine = true,
                label = { Text(tr("Amount") + " · " + methodLabel) },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                modifier = Modifier.weight(1f),
            )
            OutlinedButton(onClick = onFillRemaining) { Text(tr("Rest")) }
        }
        Spacer(Modifier.height(6.dp))
        Button(onClick = onAdd, modifier = Modifier.fillMaxWidth()) { Text(tr("Add part")) }
        Spacer(Modifier.height(8.dp))
        Row(Modifier.fillMaxWidth()) {
            Text(tr("Remaining"), Modifier.weight(1f), fontWeight = FontWeight.SemiBold)
            Text(money(fromMinor(summary.remainingMinor, dp)), fontWeight = FontWeight.Bold,
                color = if (summary.remainingMinor == 0L) Success else Warning)
        }
        if (summary.changeMinor > 0L) {
            Row(Modifier.fillMaxWidth()) {
                Text(tr("Change"), Modifier.weight(1f))
                Text(money(fromMinor(summary.changeMinor, dp)), fontWeight = FontWeight.Bold, color = Success)
            }
        }
        if (parts.isNotEmpty()) {
            summary.problem?.takeIf { it != SplitProblem.SHORT }?.let {
                Text(splitProblemText(it), style = MaterialTheme.typography.bodySmall, color = Danger)
            }
        }
        if (parts.size == 1 && summary.chargeable) {
            Text(tr("Add a second part to split, or turn split payment off."),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** Fill-in text for the amount box: what is still to take, at the currency's precision. */
internal fun remainingInput(summary: SplitSummary): String =
    if (summary.remainingMinor > 0L) minorToInput(summary.remainingMinor, Currency.decimals) else ""
